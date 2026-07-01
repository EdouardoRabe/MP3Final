# Documentation — Partie Web (Mozika)

## Vue d'ensemble

L'application web s'appelle **Mozika**. C'est une application Django 5.1 qui permet de gérer des fichiers MP3 : les stocker, les lire, les organiser en playlists et en générer automatiquement. Elle expose une API REST et une interface web rendue côté serveur.

---

## Architecture globale

```
web/
├── config/          → Configuration Django (settings, urls, wsgi)
├── tracks/          → App Django : gestion des morceaux MP3
├── playlists/       → App Django : gestion des playlists + algorithme
├── frontend/        → App Django : pages HTML servies au navigateur
├── templates/       → Fichiers HTML (Jinja-like avec le moteur Django)
├── static/
│   ├── css/style.css
│   └── js/
│       ├── track-manager.js
│       ├── playlist-builder.js
│       └── audio-player.js
├── media/mp3/       → Fichiers MP3 uploadés (créé automatiquement)
└── db.sqlite3       → Base de données SQLite (défaut dev)
```

---

## Librairies utilisées

### Backend Python

| Librairie | Version | Rôle |
|---|---|---|
| `Django` | 5.1.x | Framework web principal : ORM, routing, templates, admin |
| `djangorestframework` | 3.15.x | Création de l'API REST (serializers, viewsets, routeurs) |
| `django-filter` | 24.x | Filtrage avancé sur les querysets DRF |
| `django-cors-headers` | 4.x | Autoriser les requêtes cross-origin (CORS_ALLOW_ALL_ORIGINS = True) |
| `psycopg2-binary` | 2.9.x | Driver PostgreSQL (prêt pour la prod, non utilisé en dev) |
| `python-magic` | 0.4.x | Détection du type MIME des fichiers uploadés |
| `Pillow` | ≥11.3 | Traitement d'images (prévu pour thumbnails futures) |
| `gunicorn` | 22.x | Serveur WSGI pour la production |
| `whitenoise` | 6.x | Service des fichiers statiques en production sans Nginx |

### Frontend JavaScript (chargé via CDN)

| Librairie | Version | Rôle |
|---|---|---|
| Bootstrap | 5.3.3 | Composants UI : modales, boutons, grilles, toasts |
| Bootstrap Icons | 1.11.3 | Icônes vectorielles (bi-music-note, bi-stars, etc.) |
| Alpine.js | 3.14.1 | Réactivité légère côté navigateur (état, boucles, events) |
| Howler.js | 2.2.4 | Lecture audio MP3 dans le navigateur (HTML5 Audio + Web Audio API) |
| SortableJS | 1.15.2 | Drag & drop pour réordonner les pistes dans une playlist |

---

## Configuration (config/settings.py)

### Variables d'environnement

| Variable | Défaut dev | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | clé dev insécurisée | Clé secrète Django (obligatoire à changer en prod) |
| `DJANGO_DEBUG` | `True` | Mode debug (affichage d'erreurs détaillées) |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Hôtes autorisés |

### Points importants

- **Base de données** : SQLite (`web/db.sqlite3`) par défaut. Un bloc PostgreSQL est commenté dans `settings.py` pour la production.
- **Médias** : les MP3 uploadés vont dans `web/media/mp3/`. En développement, Django les sert directement via `urlpatterns += static(MEDIA_URL, ...)`. En production, il faut un serveur web (Nginx) ou WhiteNoise.
- **Upload max** : 50 MB (`FILE_UPLOAD_MAX_MEMORY_SIZE` et `DATA_UPLOAD_MAX_MEMORY_SIZE`).
- **Locale** : Français (`fr-fr`), fuseau horaire Madagascar (`Indian/Antananarivo`).
- **Pagination API** : 50 éléments par page par défaut (`REST_FRAMEWORK.PAGE_SIZE = 50`).
- **CORS** : ouvert à toutes les origines (`CORS_ALLOW_ALL_ORIGINS = True`) — à restreindre en production.

---

## Routing des URLs (config/urls.py)

```
/admin/                    → Interface d'administration Django
/api/tracks/               → API REST des morceaux (CRUD + streaming)
/api/playlists/            → API REST des playlists (CRUD + génération + ZIP)
/                          → Page d'accueil (tableau de bord)
/tracks/                   → Page liste des morceaux
/playlists/                → Page liste des playlists
/playlists/generate/       → Page de composition de playlist
/playlists/<uuid>/         → Page détail d'une playlist
```

---

## App `tracks` — Gestion des morceaux

### Modèle Track (tracks/models.py)

Le modèle `Track` est la table centrale. Chaque ligne représente un fichier MP3 stocké sur le serveur.

| Champ | Type Django | Description |
|---|---|---|
| `id` | `UUIDField` (PK) | Identifiant unique auto-généré (pas d'entier auto-incrémenté) |
| `file` | `CharField(500)` | Chemin relatif depuis `MEDIA_ROOT` (ex: `mp3/abc123_chanson.mp3`) |
| `original_filename` | `CharField(255)` | Nom original du fichier lors de l'upload |
| `title` | `CharField(255)` | Titre du morceau (défaut: "Sans titre") |
| `artist` | `CharField(255)` | Artiste (défaut: "Inconnu") |
| `album` | `CharField(255)` | Album (optionnel) |
| `genre` | `CharField(100)` | Genre musical (nullable) |
| `language` | `CharField(50)` | Langue (nullable) |
| `duration` | `FloatField` | Durée en secondes (ex: 240.5) |
| `file_size` | `IntegerField` | Taille en octets |
| `file_hash` | `CharField(64)` | Hash SHA256 du fichier — **unique** dans la BD |
| `metadata_complete` | `BooleanField` | True si titre et artiste sont remplis (pas les valeurs par défaut) |
| `created_at` | `DateTimeField` | Auto-rempli à la création |
| `updated_at` | `DateTimeField` | Auto-mis à jour |

**Pourquoi UUID ?** Pour éviter les collisions lors d'imports depuis plusieurs sources, et parce que les UUIDs sont plus sûrs à exposer dans les URLs que des entiers séquentiels.

**Pourquoi `file` est un `CharField` et pas un `FileField` Django ?** Pour garder le contrôle total sur la gestion physique du fichier (upload manuel, suppression à la destruction, streaming custom). Un `FileField` Django ajoute une couche d'abstraction qui complique le streaming range-based.

**Détection des doublons par SHA256** : avant de sauvegarder un fichier, on calcule son hash SHA256. Si un enregistrement avec ce hash existe déjà, on refuse l'upload avec une erreur `400`. Cela empêche d'avoir deux copies du même fichier.

### Sérialiseurs (tracks/serializers.py)

Il y a trois sérialiseurs selon l'action :

**`TrackSerializer`** (lecture) :
- Expose tous les champs + trois champs calculés :
  - `file_url` : URL absolue pour télécharger le fichier (`/media/mp3/...`)
  - `play_url` : URL de streaming (`/api/tracks/<id>/play/`)
  - `duration_formatted` : durée en `mm:ss` (ex: `03:45`)

**`TrackCreateSerializer`** (création/upload) :
- Accepte un `FileField` obligatoire
- Validations dans `validate_file()` :
  1. Extension : doit être `.mp3`
  2. Type MIME : doit être `audio/mpeg`, `audio/mp3` ou `audio/mpeg3`
  3. Taille : max 50 MB
- Dans `create()` :
  1. Lit les bytes du fichier
  2. Calcule le SHA256
  3. Vérifie qu'aucun doublon n'existe
  4. Génère un nom de fichier sécurisé : `<16 premiers chars du hash>_<nom_original>.mp3`
  5. Écrit le fichier dans `media/mp3/`
  6. Crée l'enregistrement en BD

**`TrackUpdateSerializer`** (mise à jour) :
- Accepte uniquement les métadonnées : `title`, `artist`, `album`, `genre`, `language`, `original_filename`
- Pas de modification du fichier physique possible via ce sérialiseur

### Vue TrackViewSet (tracks/views.py)

C'est un `ModelViewSet` DRF qui génère automatiquement les routes CRUD. La librairie `rest_framework.routers.DefaultRouter` crée :

```
GET    /api/tracks/          → liste paginée
POST   /api/tracks/          → créer/uploader
GET    /api/tracks/<id>/     → détail
PATCH  /api/tracks/<id>/     → modifier métadonnées
DELETE /api/tracks/<id>/     → supprimer (fichier + BD)
GET    /api/tracks/<id>/play/ → streaming audio
```

**Filtrage dans `get_queryset()`** :
- Paramètre `?genre=Rock,Pop` → filtre OR sur le genre (insensible à la casse via `__iexact`)
- Idem pour `?artist=` et `?language=`
- `?search=` → recherche dans `title`, `artist`, `album`, `genre`, `original_filename`
- `?ordering=duration` ou `?ordering=-created_at` → tri

**Suppression (`perform_destroy`)** :
Avant de supprimer l'enregistrement en BD, la méthode supprime le fichier physique sur le disque :
```python
file_path = os.path.join(settings.MEDIA_ROOT, instance.file)
if os.path.isfile(file_path): os.remove(file_path)
instance.delete()
```

### Streaming audio (action `play`) — Comment on lit la musique

C'est la logique centrale pour lire un MP3 dans le navigateur. Elle utilise la **norme HTTP Range** pour permettre au lecteur de sauter à n'importe quelle position dans la chanson.

**Principe des Range requests** :
Quand Howler.js (le player frontend) veut jouer à partir de la seconde 45, le navigateur envoie :
```
GET /api/tracks/<id>/play/
Range: bytes=1440000-
```
Le serveur répond avec le code `206 Partial Content` et le contenu depuis l'octet demandé.

**Implémentation en Python** :
```python
# Parsing du header Range
range_header = request.headers.get('Range', '')
range_spec = range_header.replace('bytes=', '').split('-')
start = int(range_spec[0])
end = int(range_spec[1]) if range_spec[1] else file_size - 1
length = end - start + 1

# Générateur qui lit par chunks de 8192 octets (8 KB)
def range_stream():
    with open(file_path, 'rb') as f:
        f.seek(start)  # Sauter directement à la bonne position
        remaining = length
        while remaining > 0:
            chunk = f.read(min(8192, remaining))
            if not chunk: break
            yield chunk
            remaining -= len(chunk)

response = StreamingHttpResponse(range_stream(), status=206, content_type='audio/mpeg')
response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
response['Content-Length'] = str(length)
response['Accept-Ranges'] = 'bytes'
```

**Pourquoi `StreamingHttpResponse` ?** Pour ne pas charger tout le fichier MP3 en mémoire. On lit et envoie par morceaux de 8 KB. Un fichier de 10 MB n'utilise jamais plus de 8 KB de RAM côté serveur.

---

## App `playlists` — Gestion des playlists

### Modèles (playlists/models.py)

**`Playlist`** :
| Champ | Type | Description |
|---|---|---|
| `id` | UUID PK | Identifiant unique |
| `name` | CharField(255) | Nom de la playlist |
| `description` | TextField | Description optionnelle |
| `total_duration` | FloatField | Durée totale calculée (en secondes) |
| `created_at` | DateTimeField | Date de création |
| `updated_at` | DateTimeField | Dernière modification |

Méthodes importantes :
- `duration_formatted()` : retourne `h:mm:ss` ou `mm:ss` selon la durée
- `recalculate_duration()` : fait un `SUM` SQL sur les durées des pistes liées et sauvegarde
- `set_tracks(track_ids)` : remplace toutes les pistes par une nouvelle liste ordonnée

**`PlaylistTrack`** (table de liaison) :
C'est la table intermédiaire entre `Playlist` et `Track`. Elle ajoute le champ `position` qui détermine l'ordre des pistes.

| Champ | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `playlist` | ForeignKey → Playlist | Cascade delete |
| `track` | ForeignKey → Track | Cascade delete |
| `position` | IntegerField | Ordre (commence à 0) |
| `added_at` | DateTimeField | Date d'ajout |

Deux contraintes `UniqueConstraint` :
- `(playlist, position)` : deux pistes ne peuvent pas être à la même position
- `(playlist, track)` : une même piste ne peut pas apparaître deux fois dans la playlist

### Algorithme de génération (playlists/generator.py)

L'algorithme résout un **problème de sac à dos (0/1 Knapsack)** : trouver le sous-ensemble de pistes dont la durée totale est la plus proche possible de la durée cible, sans la dépasser.

**Pourquoi le Knapsack ?** C'est le problème exact de "remplir un sac à dos de capacité fixe (durée cible) avec des objets de poids variable (durées de pistes) pour maximiser la valeur (durée totale utilisée)". C'est NP-complet mais résolvable efficacement en DP pour nos volumes.

**La fonction `_knapsack_dp(tracks, durations, target)`** :

```python
n = len(tracks)
dp = [0] * (target + 1)        # dp[d] = meilleure durée totale pour un "budget" de d
chosen = [[] for _ in range(target + 1)]  # chosen[d] = IDs des pistes choisies

for i in range(n):
    dur = durations[i]          # durée de la piste i (en dixièmes de secondes)
    for d in range(target, dur - 1, -1):  # parcours en arrière (obligatoire pour 0/1 knapsack)
        candidate = dp[d - dur] + dur
        if candidate > dp[d]:   # si on améliore le résultat
            dp[d] = candidate
            chosen[d] = chosen[d - dur] + [track_id]

best_sum = max(dp)
best_ids = chosen[dp.index(best_sum)]
```

**Pourquoi multiplier par 10 (déciseconde) ?** Le DP doit travailler avec des entiers. Les durées sont des floats (ex: 240.5 secondes). En multipliant par 10, on travaille avec des dixièmes de secondes (2405) tout en gardant une précision suffisante.

**Complexité** : O(n × target_scaled) où `n` ≤ 200 pistes et `target_scaled` ≤ 36000 × 10 = 360 000.

**Mécanisme de relaxation** : si le résultat strict est inférieur à 60% de la cible (ex: on voulait 30 min mais on a que 18 min), on relance avec une cible élargie à 120% (36 min). Cela évite un résultat trop court quand les pistes sont longues.

**La fonction principale `generate_playlist(queryset, target_seconds)`** :
1. Récupère max 200 pistes du queryset (triées par durée décroissante)
2. Lance un premier DP strict (ne dépasse pas la cible)
3. Si résultat < 60% de la cible, relance avec tolérance 120%
4. Retourne les IDs dans l'ordre optimal + durée totale + métadonnées algorithme

### Vue PlaylistViewSet (playlists/views.py)

Routes générées par DRF Router + actions custom :

```
GET    /api/playlists/              → liste compacte (avec track_count annoté)
POST   /api/playlists/              → créer playlist (avec pistes optionnelles)
GET    /api/playlists/<id>/         → détail complet avec pistes ordonnées
PUT    /api/playlists/<id>/         → remplacer complètement
PATCH  /api/playlists/<id>/         → modifier partiellement
DELETE /api/playlists/<id>/         → supprimer
POST   /api/playlists/generate/     → générer une playlist optimale (algorithme DP)
GET    /api/playlists/<id>/download/ → télécharger le ZIP des MP3
DELETE /api/playlists/<id>/tracks/<track_id>/ → retirer une piste + réindexer
```

**Pourquoi `track_count_annotated` ?** Le queryset de base fait un `COUNT` SQL sur la relation :
```python
queryset = Playlist.objects.annotate(track_count_annotated=Count('tracks'))
```
Sans ça, `PlaylistSerializer.get_track_count()` ferait une requête SQL séparée pour **chaque** playlist dans la liste (problème N+1). L'annotation règle ce problème en un seul JOIN.

**Action `generate/`** :
1. Valide la requête avec `PlaylistGenerateSerializer`
2. Construit un queryset filtré selon genre/artiste/langue (OR dans chaque champ, `__iexact`)
3. Exclut les `exclude_ids`
4. Appelle `generate_playlist(qs, target_duration)`
5. Récupère les objets Track complets dans l'ordre retourné par l'algo
6. Retourne : liste de pistes sérialisées + durée totale + métadonnées de l'algorithme

**Action `download_zip/`** :
- Construit un fichier ZIP en mémoire avec `zipfile.ZipFile` et `io.BytesIO`
- Les fichiers sont nommés `01_chanson.mp3`, `02_autre.mp3` etc. (préfixe de position pour l'ordre)
- Retourne le ZIP en `HttpResponse` avec header `Content-Disposition: attachment`

**Action `remove_track/`** :
- Supprime le `PlaylistTrack` demandé
- Re-indexe les positions : les pistes restantes reprennent des positions 0, 1, 2...
- Recalcule la durée totale

### Sérialiseurs de playlists (playlists/serializers.py)

| Sérialiseur | Action | Rôle |
|---|---|---|
| `PlaylistSerializer` | liste | Compact : id, name, description, total_duration, track_count, dates |
| `PlaylistDetailSerializer` | retrieve | Complet : avec les pistes ordonnées incluant leur détail complet |
| `PlaylistCreateSerializer` | create | Accepte `tracks` (liste d'UUIDs ordonnée) |
| `PlaylistUpdateSerializer` | update/partial_update | Même chose, mais pour modification |
| `PlaylistGenerateSerializer` | action generate | Valide genre/artist/language/target_duration/exclude_ids |

---

## App `frontend` — Pages web

### Vues Django (frontend/views.py)

Les vues Django servent des pages HTML. Elles ne font **pas** de rendu de données complexes — elles se contentent de passer les données initiales au template, et c'est le JavaScript (Alpine.js) qui fait le reste en appelant l'API.

**`dashboard(request)`** :
- Calcule les stats globales : nombre de pistes, playlists, durée totale, taille totale
- Passe les 5 pistes et playlists les plus récentes
- Rend `dashboard.html`

**`track_list(request)`** :
- Récupère les listes de genres/artistes/langues distincts pour peupler les filtres
- Rend `tracks/list.html` (la liste elle-même est chargée par `track-manager.js` via l'API)

**`playlist_list(request)`** :
- Liste les playlists avec `Count('tracks')` annoté
- Rend `playlists/list.html`

**`playlist_generate(request)`** :
- Récupère genres/artistes/langues disponibles pour les selects du formulaire
- Ces données sont passées au template comme `window.ALL_GENRES`, etc. via un `<script>` inline
- Rend `playlists/generate.html`

**`playlist_detail(request, playlist_id)`** :
- Récupère la playlist avec `prefetch_related('tracks__track')` (évite les requêtes N+1)
- Sérialise en JSON et l'injecte dans la page dans un `<script id="playlist-data">`
- Le player JavaScript lit ce JSON au démarrage, sans faire de requête réseau supplémentaire

### Templates HTML

**`base.html`** — Template parent commun à toutes les pages :
- Charge Bootstrap 5.3.3 CSS et JS via CDN
- Charge Bootstrap Icons 1.11.3 via CDN
- Charge `style.css` (fichier local)
- Charge Alpine.js 3.14.1 (avec attribut `defer`)
- Charge Howler.js 2.2.4
- Charge SortableJS 1.15.2
- Contient la navigation top avec liens et barre de recherche
- Contient les fonctions JS globales `window.showToast()` et `window.showConfirm()`
- Définit `{% block content %}` et `{% block extra_scripts %}`

**`dashboard.html`** : tableau de bord avec cards de statistiques et listes récentes

**`tracks/list.html`** : liste des morceaux
- Alpine.js component `trackManager()` gère tout
- Barre de filtre (recherche texte + 3 selects)
- Tableau des pistes avec pagination côté client
- Modales Bootstrap pour création/édition/suppression

**`playlists/generate.html`** : générateur de playlist
- Inject les options disponibles via `window.ALL_GENRES/ARTISTS/LANGUAGES`
- Système de tags : sélectionner dans un `<select>` + cliquer "+" pour ajouter un filtre
- Champ durée cible en minutes
- Alpine.js component `playlistGenerator()` gère les appels API et les résultats
- SortableJS pour réordonner les pistes résultantes par drag & drop

**`playlists/detail.html`** : détail d'une playlist
- Injecte le JSON de la playlist dans `<script id="playlist-data" type="application/json">`
- Alpine.js component `audioPlayer()` lit ce JSON au démarrage
- Lecteur audio complet avec contrôles play/pause/prev/next/seek/volume

**`playlists/list.html`** : liste des collections sauvegardées

---

## JavaScript — Logique côté navigateur

### Pourquoi Alpine.js ?

Alpine.js est un micro-framework de réactivité. On écrit `x-data`, `x-for`, `x-show`, `x-text` directement dans le HTML. Il évite d'avoir à écrire du DOM manipulation manuel (`document.querySelector`, `innerHTML`) pour chaque interaction. C'est plus léger que React ou Vue tout en étant suffisamment puissant pour cette application.

### track-manager.js — Gestion des morceaux

Composant Alpine qui gère la page `/tracks/`.

**État** :
```javascript
tracks: [],          // pistes de la page actuelle
search: '',          // texte de recherche
filterGenre/Artist/Language: '',  // filtres actifs
sortField/sortOrder, currentPage, pageSize
editingTrack: null,  // null = création, objet = édition
selectedFile: null,  // fichier sélectionné pour upload
```

**`fetchTracks()`** : appelle `GET /api/tracks/?page=&ordering=&search=&genre=` et met à jour `this.tracks`. Appelé automatiquement à chaque changement de filtre, tri, ou page.

**`saveTrack()`** :
- Si `editingTrack != null` → `PATCH /api/tracks/<id>/` avec JSON (métadonnées seulement)
- Sinon → `POST /api/tracks/` avec `FormData` (inclut le fichier MP3)

**`deleteTrack()`** → `DELETE /api/tracks/<id>/`

**Pagination** : calculée côté client à partir de `totalCount` retourné par l'API paginée DRF. La propriété `visiblePages` génère les numéros de page à afficher (fenêtre de ±2 autour de la page courante).

### playlist-builder.js — Générateur de playlist

Composant Alpine qui gère la page `/playlists/generate/`.

**État** :
```javascript
filters: { genre: [], artist: [], language: [], target_minutes: 45 }
selectedGenre/Artist/Language: ''  // valeur sélectionnée dans le <select> (avant ajout)
resultTracks: []     // résultats de la génération
totalDuration: 0
algorithmMeta: null  // infos sur l'algorithme (méthode, relaxation, etc.)
```

**Logique de filtres par tags** :
- Un `<select>` bind sur `selectedGenre`
- Le bouton "+" appelle `addTag('genre')` qui pousse la valeur dans `filters.genre[]`
- Les tags affichés ont un "×" qui appelle `removeTag('genre', idx)`
- **Important** : si l'utilisateur sélectionne sans cliquer "+", le filtre n'est PAS envoyé à l'API

**`generate()`** : envoie `POST /api/playlists/generate/` avec :
```json
{
  "genre": ["rap", "rock"],
  "artist": [],
  "language": [],
  "target_duration": 2700
}
```
Les genres/artistes/langues sont envoyés comme **tableaux JSON**, pas comme chaînes.

**`replaceTrack(index)`** : relance la génération en excluant les pistes déjà présentes (`exclude_ids`) et remplace uniquement la piste à l'index demandé.

**Drag & drop avec SortableJS** :
```javascript
this.sortableInstance = Sortable.create(el, {
    animation: 150,
    handle: '.bi-grip-vertical',  // seule la poignée déclenche le drag
    onEnd: (evt) => {
        // Après déplacement, on relit l'ordre réel du DOM
        const ids = [];
        el.querySelectorAll('[data-id]').forEach(item => ids.push(item.getAttribute('data-id')));
        // On reconstruit resultTracks dans le nouvel ordre
        this.resultTracks = ids.map(id => this.resultTracks.find(t => t.id === id)).filter(Boolean);
    },
});
```

**`savePlaylist()`** : crée la playlist via `POST /api/playlists/` avec les IDs dans l'ordre actuel, puis redirige vers `/playlists/<id>/`.

### audio-player.js — Lecteur audio de playlist

Composant Alpine qui gère la page `/playlists/<id>/`.

**Initialisation** :
1. Cherche `<script id="playlist-data">` dans la page
2. Parse le JSON (déjà injecté par la vue Django) — évite un appel réseau
3. Si le parsing échoue, appelle `GET /api/playlists/<id>/` en fallback

**Lecture avec Howler.js** :
```javascript
this.howl = new Howl({
    src: [track.play_url],    // URL de streaming /api/tracks/<id>/play/
    html5: true,              // OBLIGATOIRE pour le streaming HTTP Range
    format: ['mp3'],
    volume: this.volume,
    onplay: () => { this.playing = true; this.startProgressUpdate(); },
    onend: () => { this.nextTrack(); },  // avance automatiquement
    onloaderror: () => { this.nextTrack(); },  // saute en cas d'erreur
});
this.howl.play();
```

**Pourquoi `html5: true` ?** Sans ce flag, Howler essaie de charger le fichier complet en mémoire via Web Audio API. Avec `html5: true`, il utilise l'élément `<audio>` natif du navigateur qui supporte nativement le streaming Range-based.

**Barre de progression** :
```javascript
startProgressUpdate() {
    this.updateInterval = setInterval(() => {
        if (this.howl && this.howl.playing()) {
            this.currentTime = this.howl.seek();  // position actuelle en secondes
            this.seekPosition = this.currentTime; // mise à jour du curseur
        }
    }, 250);  // rafraîchissement toutes les 250ms
}
```

**Seek (avance rapide)** :
```javascript
seekTo(seconds) {
    this.howl.seek(seconds);  // Howler envoie une Range request au serveur
    this.currentTime = seconds;
}
```

**Suppression d'une piste d'une playlist** : appelle `DELETE /api/playlists/<id>/tracks/<track_id>/` et met à jour le tableau local sans rechargement de page.

---

## Flux de données — Upload d'un MP3 via l'interface web

```
Navigateur (track-manager.js)
  │
  │  POST /api/tracks/
  │  Content-Type: multipart/form-data
  │  [fichier .mp3 + métadonnées]
  ▼
TrackViewSet.create()
  │
  ├─ get_serializer_class() → TrackCreateSerializer
  │
  ├─ validate_file()
  │   ├─ Extension = .mp3 ?
  │   ├─ MIME = audio/mpeg ?
  │   └─ Taille ≤ 50 MB ?
  │
  ├─ create()
  │   ├─ SHA256(bytes) → file_hash
  │   ├─ Track.objects.filter(file_hash=hash).exists() → 400 si doublon
  │   ├─ Écrit media/mp3/<hash16>_<nom>.mp3 sur disque
  │   └─ Track.objects.create(...)
  │
  └─ HTTP 201 Created { id, title, artist, play_url, ... }
```

## Flux de données — Génération d'une playlist

```
Navigateur (playlist-builder.js)
  │
  │  POST /api/playlists/generate/
  │  {"genre": ["rap"], "target_duration": 2700}
  ▼
PlaylistViewSet.generate()
  │
  ├─ PlaylistGenerateSerializer.is_valid()
  │
  ├─ Construit queryset filtré
  │   Track.objects.filter(Q(genre__iexact='rap'))
  │
  ├─ generate_playlist(qs, 2700)
  │   ├─ Récupère max 200 pistes (id, duration)
  │   ├─ Passe 1 : _knapsack_dp(tracks, durations, 27000)
  │   │   → Remplit dp[0..27000], choisit la meilleure combinaison
  │   ├─ Si résultat < 60% → Passe 2 avec cible × 1.2
  │   └─ Retourne {track_ids, total_duration, algorithm, relaxation}
  │
  ├─ Track.objects.filter(id__in=track_ids)  → objets complets
  │
  └─ HTTP 200 {tracks: [...], total_duration: 2695.0, algorithm_metadata: {...}}
```

---

## Sécurité

- **CSRF** : activé par Django (middleware `CsrfViewMiddleware`). Les formulaires HTML incluent `{% csrf_token %}`. Les appels `fetch()` en JS ne l'incluent pas — c'est acceptable car l'API est publique (pas d'authentification).
- **SQL Injection** : impossible avec l'ORM Django (paramètres liés automatiquement).
- **Upload sécurisé** : validation de l'extension, du MIME et de la taille avant d'écrire sur disque. Le nom du fichier est préfixé par le hash SHA256 (élimine les noms malveillants).
- **CORS ouvert** : `CORS_ALLOW_ALL_ORIGINS = True` — à restreindre en production.
- **Secrets** : `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` lus depuis variables d'environnement.

---

## Base de données — Diagramme simplifié

```
Track (tracks_track)
  id UUID PK
  file VARCHAR
  title, artist, album, genre, language
  duration FLOAT
  file_hash VARCHAR UNIQUE
  ...

Playlist (playlists_playlist)
  id UUID PK
  name VARCHAR
  total_duration FLOAT
  ...

PlaylistTrack (playlists_playlisttrack)
  id UUID PK
  playlist_id → Playlist (CASCADE)
  track_id → Track (CASCADE)
  position INT
  UNIQUE(playlist_id, position)
  UNIQUE(playlist_id, track_id)
```
