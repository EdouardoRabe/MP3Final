# Documentation — Partie Desktop (Pipeline MP3)

## Vue d'ensemble

Le desktop est un **pipeline de traitement en 3 étapes** qui surveille un dossier local, enrichit les fichiers MP3 détectés avec leurs métadonnées, les filtre selon une blacklist, et les envoie automatiquement à l'API web Django.

Les 3 programmes sont indépendants. Ils communiquent entre eux via **RabbitMQ**, un serveur de messagerie qui agit comme intermédiaire. Chaque programme tourne en continu et attend des messages.

---

## Architecture globale

```
desktop/
├── programme1.py    → Surveille le dossier, détecte les MP3, publie dans RabbitMQ
├── programme2.py    → Lit les métadonnées ID3, filtre la blacklist, transmet
├── programme3.py    → Uploade vers l'API Django, supprime les fichiers locaux
├── logger.py        → Module de log partagé par les 3 programmes
├── blacklist.json   → Liste des artistes et genres à rejeter
├── requirements.txt → Dépendances Python
└── playlist/        → Dossier surveillé (créé automatiquement si absent)
    └── (fichiers .mp3 à déposer ici)
```

---

## Flux de données complet

```
┌─────────────────┐
│   Utilisateur   │
│ dépose un .mp3  │
│ dans ./playlist │
└────────┬────────┘
         │
         ▼
┌─────────────────┐    queue        ┌─────────────────┐    queue        ┌─────────────────┐
│  Programme 1    │ list_mp3_p1 ──▶ │  Programme 2    │ list_mp3_p2 ──▶ │  Programme 3    │
│   (Watchdog)    │                 │   (Mutagen)     │                 │   (Uploader)    │
│                 │                 │                 │                 │                 │
│ - Scan initial  │                 │ - Lit tags ID3  │                 │ - POST multipart│
│ - Détecte MP3   │                 │ - Vérifie black │                 │ - Retry x3      │
│ - Buffer 10s    │                 │   list          │                 │ - Supprime local│
│ - Publie batch  │                 │ - Enrichit JSON │                 │   après succès  │
└─────────────────┘                 └─────────────────┘                 └────────┬────────┘
                                                                                 │ HTTP POST
                                                                                 ▼
                                                                    ┌────────────────────────┐
                                                                    │   API Django           │
                                                                    │ /api/tracks/           │
                                                                    │ (web/tracks/views.py)  │
                                                                    └────────────────────────┘
```

---

## Librairies utilisées

### Python standard (incluses avec Python, pas à installer)

| Module | Utilisé par | Rôle |
|---|---|---|
| `os` | P1, P2, P3 | Manipulation des chemins de fichiers, suppression |
| `time` | P1, P3 | `time.sleep()` pour les timers et les retries |
| `json` | P1, P2, P3 | Sérialisation/désérialisation des messages RabbitMQ |
| `threading` | P1 | Thread daemon pour le flush périodique |
| `logging` | logger.py | Système de journalisation configurable |
| `datetime` | logger.py | Génération du nom de fichier log horodaté |
| `urllib.request` | P3 | Envoi de requêtes HTTP POST multipart à Django |-]
| `urllib.error` | P3 | Gestion des erreurs HTTP (`HTTPError`, `URLError`) |
| `sys` | P3 | `sys.exit(0)` à l'arrêt propre |

### Librairies à installer (requirements.txt)

| Librairie | Version | Utilisé par | Rôle |
|---|---|---|---|
| `watchdog` | 6.0.0 | P1 | Surveillance du système de fichiers en temps réel |
| `mutagen` | 1.47.0 | P2 | Lecture des tags ID3 des fichiers MP3 |
| `pika` | 1.3.2 | P1, P2, P3 | Client Python pour RabbitMQ (protocole AMQP) |

---

## Programme 1 — Surveillance du dossier (programme1.py)

### Rôle

P1 surveille le dossier `./playlist`. Quand un fichier `.mp3` apparaît (déposé par l'utilisateur ou déplacé), il l'ajoute à un buffer. Toutes les 10 secondes, il envoie le contenu du buffer à P2 via RabbitMQ.

### Librairie : watchdog

**Watchdog** est une bibliothèque Python qui utilise les API natives du système d'exploitation pour surveiller les changements dans le système de fichiers :
- Sur Linux : `inotify`
- Sur macOS : `FSEvents`
- Sur Windows : `ReadDirectoryChangesW`

C'est beaucoup plus efficace que de scanner le dossier en boucle toutes les N secondes (pas de polling, événement immédiat).

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class FileCreatedHandler(FileSystemEventHandler):
    def on_created(self, event):
        # Déclenché quand un fichier est créé dans le dossier
        if not event.is_directory and event.src_path.lower().endswith('.mp3'):
            self._register(event.src_path)

    def on_moved(self, event):
        # Déclenché quand un fichier est déplacé DANS le dossier
        self._register(event.dest_path)

observer = Observer()
observer.schedule(FileCreatedHandler(), path=WATCH_DIR, recursive=False)
observer.start()
```

**Pourquoi `on_moved` en plus de `on_created` ?** Sur certains systèmes, quand on "dépose" un fichier dans un dossier (drag & drop depuis un autre dossier du même disque), le système émet un événement `MOVED` plutôt que `CREATED`.

### Librairie : threading

P1 a deux tâches simultanées :
1. **Watchdog** (dans le thread principal) : écoute les événements système de fichiers
2. **Flush loop** (dans un thread daemon) : envoie le buffer toutes les 10 secondes

```python
lock = threading.Lock()  # Protège l'accès concurrent à pending_files

def flush_loop():
    while True:
        time.sleep(10)
        with lock:  # Verrouillage pour éviter les conditions de course
            if not pending_files:
                continue
            publish_to_rabbitmq(pending_files)
            pending_files.clear()

threading.Thread(target=flush_loop, daemon=True).start()
```

**Pourquoi un `Lock` ?** Le thread watchdog et le thread flush accèdent tous les deux à `pending_files`. Sans verrou, un thread pourrait modifier la liste pendant que l'autre la lit → comportement imprévisible. Le `with lock:` garantit qu'un seul thread à la fois modifie `pending_files`.

**Pourquoi `daemon=True` ?** Un thread daemon s'arrête automatiquement quand le programme principal se termine. Sans ça, le programme resterait bloqué même après `Ctrl+C`.

### Librairie : pika (publication)

```python
import pika

def publish_to_rabbitmq(payload):
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()
    channel.queue_declare(queue="list_mp3_p1", durable=True)  # Crée la queue si elle n'existe pas
    channel.basic_publish(
        exchange='',                    # Exchange par défaut (direct routing)
        routing_key="list_mp3_p1",      # Nom de la queue destination
        body=json.dumps(payload),       # Contenu du message en JSON
    )
    connection.close()
```

**`durable=True`** : la queue survit à un redémarrage de RabbitMQ. Sans ça, si RabbitMQ s'arrête, les messages en attente sont perdus.

### Format du message publié dans `list_mp3_p1`

```json
[
  {"name": "chanson1.mp3", "path": "/chemin/absolu/vers/chanson1.mp3"},
  {"name": "chanson2.mp3", "path": "/chemin/absolu/vers/chanson2.mp3"}
]
```

### Scan initial

Au démarrage, P1 ne fait pas que surveiller le futur. Il scanne aussi les fichiers déjà présents dans `./playlist` au moment du lancement. Cela permet de reprendre les fichiers qui auraient été déposés pendant que P1 était arrêté.

---

## Programme 2 — Extraction des métadonnées et filtrage (programme2.py)

### Rôle

P2 consomme la queue `list_mp3_p1`. Pour chaque fichier reçu, il :
1. Lit les tags ID3 (titre, artiste, album, genre, durée)
2. Vérifie si l'artiste ou le genre est dans la blacklist
3. Si non blacklisté, ajoute les métadonnées et transmet à P3 via `list_mp3_p2`

### Librairie : mutagen

**Mutagen** est une bibliothèque Python pour lire (et écrire) les métadonnées des fichiers audio. Elle supporte MP3 (ID3), FLAC, OGG, M4A, etc.

Pour les MP3, les métadonnées sont stockées dans des **tags ID3** : blocs de données en début (ou fin) du fichier qui contiennent le titre, l'artiste, l'album, l'image de couverture, etc. Chaque tag a un code à 4 lettres :

| Tag ID3 | Contenu |
|---|---|
| `TIT2` | Titre du morceau |
| `TPE1` | Artiste principal |
| `TALB` | Album |
| `TCON` | Genre musical |
| `TRCK` | Numéro de piste |

```python
from mutagen.mp3 import MP3       # Pour lire les infos techniques (durée, bitrate)
from mutagen.id3 import ID3       # Pour lire les tags ID3

def extract_metadata(filepath):
    audio = MP3(filepath)          # audio.info.length = durée en secondes
    tags = ID3(filepath)           # tags.get('TIT2') = objet tag ou None

    return {
        "duree": round(audio.info.length, 2),
        "artiste": str(tags.get('TPE1', 'Inconnu')).strip(),
        "titre": str(tags.get('TIT2', 'Sans titre')).strip(),
        "album": str(tags.get('TALB', 'Album inconnu')).strip(),
        "genre": str(tags.get('TCON', '')).strip(),
    }
```

**Pourquoi `str(tags.get('TPE1', 'Inconnu'))` ?** `tags.get('TPE1')` ne retourne pas une chaîne mais un objet Mutagen (`TPE1Frame`). L'appel à `str()` convertit cet objet en texte lisible. La valeur par défaut `'Inconnu'` est une chaîne Python ordinaire, donc le `str()` est inoffensif dans ce cas.

### Blacklist (blacklist.json)

```json
{
  "artistes": [],
  "genres": []
}
```

Pour bloquer un artiste ou un genre, il suffit d'ajouter son nom dans la liste :
```json
{
  "artistes": ["Inconnu", "Artiste Indésirable"],
  "genres": ["Publicité", "Spam"]
}
```

La blacklist est rechargée à **chaque message reçu** (pas au démarrage). Cela permet de modifier la blacklist sans redémarrer P2.

**Comparaison insensible à la casse** :
```python
artistes = [a.strip().lower() for a in data.get("artistes", [])]
# ...
artiste = metadata.get("artiste", "").strip().lower()
if artiste in blacklist["artistes"]:  # "Bob Marley" == "bob marley" → True
    return True
```

### Librairie : pika (consommation + publication)

P2 consomme `list_mp3_p1` et publie dans `list_mp3_p2` :

```python
# Consommation
channel.queue_declare(queue='list_mp3_p1', durable=True)
channel.basic_consume(
    queue='list_mp3_p1',
    on_message_callback=on_message_received,
    auto_ack=False,   # ACK manuel : le message n'est supprimé de la queue qu'après traitement
)
channel.start_consuming()  # Boucle bloquante qui écoute indéfiniment
```

**`auto_ack=False` et `ch.basic_ack()`** : avec `auto_ack=True`, RabbitMQ considère le message traité dès qu'il est envoyé au consommateur. Si P2 plante pendant le traitement, le message est perdu. Avec `auto_ack=False`, P2 doit explicitement appeler `ch.basic_ack()` après traitement réussi. Si P2 plante sans ACK, RabbitMQ remet le message en queue.

### Format du message publié dans `list_mp3_p2`

```json
[
  {
    "name": "chanson1.mp3",
    "path": "/chemin/absolu/vers/chanson1.mp3",
    "metadata": {
      "duree": 240.5,
      "artiste": "Bob Marley",
      "titre": "One Love",
      "album": "Exodus",
      "genre": "Reggae"
    }
  }
]
```

---

## Programme 3 — Upload vers l'API Django (programme3.py)

### Rôle

P3 consomme la queue `list_mp3_p2`. Pour chaque fichier, il :
1. Construit une requête HTTP POST multipart (fichier + métadonnées)
2. L'envoie à `http://localhost:8000/api/tracks/`
3. Réessaie jusqu'à 3 fois en cas d'échec (avec 10 secondes entre chaque tentative)
4. Si succès (201 ou doublon détecté) → supprime le fichier local
5. Se reconnecte automatiquement à RabbitMQ si la connexion est perdue

### Configuration par variables d'environnement

```python
API_URL = os.environ.get('DJANGO_API_URL', 'http://localhost:8000/api/tracks/')
RABBITMQ_HOST = os.environ.get('RABBITMQ_HOST', 'localhost')
MAX_RETRIES = 3
RETRY_DELAY = 10  # secondes entre chaque tentative
```

Pour pointer vers une autre instance Django :
```bash
export DJANGO_API_URL=http://192.168.1.10:8000/api/tracks/
python3 programme3.py
```

### Construction manuelle d'une requête multipart

P3 n'utilise pas `requests` (non installé) mais la bibliothèque standard `urllib`. Pour envoyer un fichier via HTTP, le format `multipart/form-data` est obligatoire.

**Structure d'une requête multipart** :
```
--boundary
Content-Disposition: form-data; name="file"; filename="chanson.mp3"
Content-Type: audio/mpeg

[bytes du fichier MP3]
--boundary
Content-Disposition: form-data; name="title"

One Love
--boundary
Content-Disposition: form-data; name="artist"

Bob Marley
--boundary--
```

**Implémentation** :
```python
boundary = '----P3UploaderBoundary'

# Partie fichier
parts = []
parts.append(f'--{boundary}'.encode())
parts.append(b'Content-Disposition: form-data; name="file"; filename="' + filename.encode() + b'"')
parts.append(b'Content-Type: audio/mpeg')
parts.append(b'')
parts.append(raw)  # bytes du fichier MP3

# Parties texte (métadonnées)
for key, value in fields.items():
    parts.append(f'--{boundary}'.encode())
    parts.append(f'Content-Disposition: form-data; name="{key}"'.encode())
    parts.append(b'')
    parts.append(value.encode('utf-8'))

parts.append(f'--{boundary}--'.encode())
body = b'\r\n'.join(parts)  # Les parties sont séparées par \r\n (CRLF)
```

### Gestion des erreurs et retry

```python
for attempt in range(1, MAX_RETRIES + 1):
    if upload_one(path, meta):    # True si 201 OK
        ok = True
        break
    if attempt < MAX_RETRIES:
        time.sleep(RETRY_DELAY)   # Attendre 10s avant de réessayer
```

**Détection des doublons** :
Si Django répond avec `HTTP 400` et que le corps de la réponse contient `"hash"` ou `"existe deja"`, c'est que le fichier existe déjà dans la base. P3 traite ça comme un **succès** (le fichier est quand même dans l'API, peu importe qui l'a mis) et supprime le fichier local :

```python
except HTTPError as exc:
    response_body = exc.read().decode('utf-8')
    if 'hash' in response_body.lower() or 'existe deja' in response_body.lower():
        return True  # Considéré comme succès → suppression locale
    return False
```

### Suppression du fichier local après upload réussi

```python
if ok:
    if os.path.isfile(path):
        os.remove(path)  # Supprime ./playlist/chanson.mp3
```

**Pourquoi supprimer ?** Pour éviter d'uploader le même fichier plusieurs fois si P3 redémarre ou si P1 re-scanne le dossier. Une fois uploadé, la détection par hash SHA256 côté Django protège des doublons, mais autant nettoyer localement aussi.

### Reconnexion automatique à RabbitMQ

```python
def listen():
    while True:  # Boucle infinie de reconnexion
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
            channel.start_consuming()  # Bloque ici jusqu'à déconnexion
        except KeyboardInterrupt:
            sys.exit(0)  # Arrêt propre sur Ctrl+C
        except Exception as exc:
            log.error("Connexion RabbitMQ perdue : %s", exc)
            time.sleep(5)  # Attendre 5s avant de reconnecter
```

Si RabbitMQ s'arrête puis redémarre, P3 se reconnecte automatiquement dans les 5 secondes.

---

## Module partagé — logger.py

### Rôle

Un seul module de logging configuré pour les 3 programmes. Chaque programme a son propre fichier de log horodaté dans `desktop/logs/`.

### Implémentation

```python
import logging
from datetime import datetime

LOG_DIR = "logs/"
_loggers = {}  # Cache : évite de recréer le logger si appelé plusieurs fois

def get_logger(program_name: str) -> logging.Logger:
    if program_name in _loggers:
        return _loggers[program_name]

    logger = logging.getLogger(program_name)
    logger.setLevel(logging.DEBUG)  # Capture tous les niveaux

    # Handler fichier : DEBUG et plus → logs/programmeX_2026-07-01.log
    date_str = datetime.now().strftime("%Y-%m-%d")
    file_handler = logging.FileHandler(f"logs/{program_name}_{date_str}.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))

    # Handler console : INFO et plus → stdout
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    _loggers[program_name] = logger
    return logger
```

**Utilisation** :
```python
from logger import get_logger
log = get_logger("programme1")

log.debug("Détail technique invisible en console")   # → fichier seulement
log.info("Événement normal")                          # → fichier + console
log.warning("Quelque chose d'anormal")               # → fichier + console
log.error("Erreur importante")                        # → fichier + console
```

**Fichiers produits** :
```
desktop/logs/
├── programme1_2026-07-01.log
├── programme2_2026-07-01.log
└── programme3_2026-07-01.log
```

Un nouveau fichier est créé chaque jour (date dans le nom). Les fichiers anciens s'accumulent (pas de rotation automatique dans cette version).

---

## RabbitMQ — Rôle et fonctionnement

### Qu'est-ce que RabbitMQ ?

RabbitMQ est un **message broker** : un serveur intermédiaire qui reçoit des messages et les distribue aux consommateurs. Il implémente le protocole **AMQP** (Advanced Message Queuing Protocol).

**Pourquoi RabbitMQ plutôt qu'un appel direct ?** :
- **Découplage** : P1 ne sait pas si P2 est démarré ou non. Il publie et oublie. P2 lira le message quand il sera prêt.
- **Fiabilité** : si P2 est arrêté, les messages s'accumulent dans la queue jusqu'à ce qu'il redémarre.
- **Scalabilité** : on peut lancer plusieurs instances de P2 en parallèle, chacune prenant un message différent.

### Queues utilisées

| Queue | Producteur | Consommateur | Contenu |
|---|---|---|---|
| `list_mp3_p1` | Programme 1 | Programme 2 | `[{name, path}, ...]` |
| `list_mp3_p2` | Programme 2 | Programme 3 | `[{name, path, metadata}, ...]` |

### Paramètres de connexion

```python
import pika
connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
```

Par défaut, RabbitMQ écoute sur le port `5672` sur `localhost`. Aucune authentification n'est configurée (utilise le compte guest par défaut).

---

## Démarrage et ordre de lancement

Les 3 programmes sont **indépendants** mais doivent tous tourner en même temps. L'ordre recommandé :

```bash
# Terminal 1 — démarrer RabbitMQ (si pas démarré en service)
sudo systemctl start rabbitmq-server

# Terminal 2 — P2 d'abord (pour qu'il soit prêt à recevoir de P1)
cd desktop
source venv/bin/activate
python3 programme2.py

# Terminal 3 — P3
python3 programme3.py

# Terminal 4 — P1 en dernier (il va scanner le dossier et envoyer à P2)
python3 programme1.py
```

Si on démarre P1 avant P2 : les messages publiés dans `list_mp3_p1` restent dans la queue jusqu'à ce que P2 démarre. RabbitMQ les conserve (grâce à `durable=True`).

---

## Fichier blacklist.json — Configuration du filtre

Structure par défaut (rien de bloqué) :
```json
{
  "artistes": [],
  "genres": []
}
```

Exemple avec filtres actifs :
```json
{
  "artistes": ["Artiste Spam", "Inconnu"],
  "genres": ["Advertisement", "Test"]
}
```

**Règles** :
- La comparaison est insensible à la casse (`"Bob Marley"` == `"bob marley"`)
- Si l'artiste OU le genre est dans la blacklist, la piste est rejetée
- La blacklist est relue à chaque message (pas besoin de redémarrer P2 après modification)
- Les espaces en début/fin sont ignorés (`.strip()`)

---

## Résumé des cas d'utilisation

### Cas 1 : Dépôt d'un nouveau MP3

1. Utilisateur copie `one_love.mp3` dans `desktop/playlist/`
2. **P1** (watchdog) détecte l'événement `on_created` immédiatement
3. **P1** attend le prochain flush (max 10 secondes) et publie dans `list_mp3_p1`
4. **P2** reçoit le message, lit les tags ID3 avec mutagen :
   - `artiste = "Bob Marley"`, `titre = "One Love"`, `genre = "Reggae"`, `duree = 180.0`
5. **P2** vérifie la blacklist → pas bloqué
6. **P2** publie dans `list_mp3_p2` avec les métadonnées enrichies
7. **P3** reçoit le message et envoie `POST /api/tracks/` avec le fichier MP3 et les métadonnées
8. Django valide, calcule le SHA256, écrit `web/media/mp3/abc123_one_love.mp3`, crée l'enregistrement BD
9. Django répond `201 Created {"id": "uuid-...", "title": "One Love", ...}`
10. **P3** supprime `desktop/playlist/one_love.mp3`

### Cas 2 : Fichier déjà uploadé (doublon)

1. Utilisateur redépose le même `one_love.mp3`
2. P1 → P2 → P3 : même chemin jusqu'à l'upload
3. Django calcule le SHA256 → identique à un fichier existant → `HTTP 400 {"file": "...hash...existe déjà..."}`
4. **P3** détecte les mots-clés `hash` dans la réponse → considère comme succès
5. **P3** supprime le fichier local quand même
6. Résultat : le fichier n'est pas dupliqué dans l'API

### Cas 3 : Artiste blacklisté

1. `artiste_spam.mp3` déposé dans `./playlist`
2. P1 détecte et publie
3. **P2** lit les tags : `artiste = "Artiste Spam"`
4. **P2** vérifie la blacklist : `"artiste spam"` est dans `blacklist["artistes"]`
5. **P2** rejette la piste (log `[BLACKLISTE]`) et ne la transmet pas à P3
6. Le fichier reste dans `./playlist` (P3 ne le supprime jamais car P3 ne l'a pas reçu)

### Cas 4 : Django non disponible (retry)

1. P3 reçoit un fichier à uploader
2. Django est arrêté → `URLError: Connection refused`
3. P3 attend 10 secondes, réessaie → toujours en échec
4. P3 attend 10 secondes, réessaie une 3e fois → en échec
5. P3 marque le fichier en **échec définitif** et passe au suivant
6. Le fichier reste dans `./playlist` (non supprimé)
7. **Le message est quand même ACK** : il n'est pas remis dans la queue

---

## Variables d'environnement disponibles

| Variable | Défaut | Programme | Description |
|---|---|---|---|
| `DJANGO_API_URL` | `http://localhost:8000/api/tracks/` | P3 | URL de l'API Django |
| `RABBITMQ_HOST` | `localhost` | P3 | Adresse du broker RabbitMQ |

P1 et P2 ont `localhost` codé en dur pour RabbitMQ (pas de variable d'env).
