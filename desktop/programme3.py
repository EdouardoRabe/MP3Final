"""
P3 --- Service d'upload
=======================
Consomme la queue RabbitMQ list_mp3_p2, uploade chaque fichier MP3
enrichi vers l'API Django et nettoie les fichiers locaux apres succes.
"""
import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import pika
from logger import get_logger

log = get_logger("programme3")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_URL = os.environ.get('DJANGO_API_URL', 'http://localhost:8000/api/tracks/')
RABBITMQ_HOST = os.environ.get('RABBITMQ_HOST', 'localhost')
QUEUE_NAME = 'list_mp3_p2'
MAX_RETRIES = 3
RETRY_DELAY = 10


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
def upload_one(filepath: str, metadata: dict) -> bool:
    """
    Envoie un fichier MP3 + metadonnees a l'API Django via POST multipart.

    Retourne True si l'API repond 201 (ou si un doublon est detecte),
    False en cas d'echec definitif.
    """
    if not os.path.isfile(filepath):
        log.error("Fichier introuvable sur le disque : %s", filepath)
        print(f"  [ABSENT] {filepath}")
        return False

    filename = os.path.basename(filepath)
    boundary = '----P3UploaderBoundary'

    with open(filepath, 'rb') as fh:
        raw = fh.read()

    parts = []
    parts.append(f'--{boundary}'.encode())
    parts.append(
        b'Content-Disposition: form-data; name="file"; filename="'
        + filename.encode('utf-8') + b'"'
    )
    parts.append(b'Content-Type: audio/mpeg')
    parts.append(b'')
    parts.append(raw)

    fields = {
        'title': metadata.get('titre', 'Sans titre'),
        'artist': metadata.get('artiste', 'Inconnu'),
        'album': metadata.get('album', ''),
        'duration': str(metadata.get('duree', 0)),
        'original_filename': filename,
        'genre': metadata.get('genre', ''),
        'language': '',
    }
    for key, value in fields.items():
        parts.append(f'--{boundary}'.encode())
        parts.append(
            f'Content-Disposition: form-data; name="{key}"'.encode()
        )
        parts.append(b'')
        parts.append(value.encode('utf-8'))

    parts.append(f'--{boundary}--'.encode())
    body = b'\r\n'.join(parts)

    req = Request(
        API_URL,
        data=body,
        headers={
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Accept': 'application/json',
        },
        method='POST',
    )

    try:
        with urlopen(req) as resp:
            if resp.status == 201:
                data = json.loads(resp.read().decode('utf-8'))
                track_id = data.get('id', '?')
                log.info("Upload OK : %s (id=%s)", filename, track_id)
                print(f"  [OK] {filename} -> id={track_id}")
                return True
            else:
                log.warning(
                    "Reponse HTTP inattendue %d pour %s", resp.status, filename
                )
                print(f"  [HTTP {resp.status}] {filename}")
                return False

    except HTTPError as exc:
        response_body = exc.read().decode('utf-8', errors='replace')
        log.error("HTTP %d sur %s : %s", exc.code, filename, response_body[:200])
        print(f"  [HTTP {exc.code}] {filename}")

        if 'hash' in response_body.lower() or 'existe deja' in response_body.lower():
            log.info("Doublon detecte pour %s, nettoyage local", filename)
            print(f"  [DOUBLON] {filename} --- fichier local supprime")
            return True
        return False

    except URLError as exc:
        log.error("Echec reseau pour %s : %s", filename, exc.reason)
        print(f"  [RESEAU] {filename} : {exc.reason}")
        return False


# ---------------------------------------------------------------------------
# Traitement des messages
# ---------------------------------------------------------------------------
def handle_message(body: bytes):
    """Traite un message JSON contenant une liste de fichiers a uploader."""
    try:
        items = json.loads(body.decode('utf-8'))
    except json.JSONDecodeError as exc:
        log.error("Payload JSON invalide : %s", exc)
        print("[P3] Payload JSON invalide, message ignore")
        return

    if not isinstance(items, list):
        log.error("Format de message inattendu (liste requise)")
        print("[P3] Format de message incorrect, message ignore")
        return

    log.info("Traitement de %d piste(s)", len(items))
    print(f"\n[P3] {len(items)} piste(s) a uploader")

    success_count = 0
    fail_count = 0

    for item in items:
        path = item.get('path', '')
        meta = item.get('metadata', {})
        name = item.get('name', os.path.basename(path) if path else '?')

        print(f"  -> {name}")

        ok = False
        for attempt in range(1, MAX_RETRIES + 1):
            if upload_one(path, meta):
                ok = True
                break
            if attempt < MAX_RETRIES:
                log.warning(
                    "Tentative %d/%d echouee pour %s, reessai dans %ds",
                    attempt, MAX_RETRIES, name, RETRY_DELAY,
                )
                print(f"     reessai {attempt}/{MAX_RETRIES} dans {RETRY_DELAY}s")
                time.sleep(RETRY_DELAY)

        if ok:
            success_count += 1
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    log.info("Fichier local nettoye : %s", name)
                except OSError as exc:
                    log.warning(
                        "Impossible de supprimer %s : %s", name, exc
                    )
        else:
            fail_count += 1
            log.error("Echec definitif pour %s apres %d tentatives", name, MAX_RETRIES)
            print(f"     [ECHEC] abandon apres {MAX_RETRIES} tentatives")

    print(f"[P3] Bilan : {success_count} succes, {fail_count} echec(s)")


# ---------------------------------------------------------------------------
# Boucle d'ecoute
# ---------------------------------------------------------------------------
def listen():
    """Boucle principale avec reconnexion automatique a RabbitMQ."""
    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST)
            )
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)

            def callback(ch, method, properties, body):
                handle_message(body)
                ch.basic_ack(delivery_tag=method.delivery_tag)

            channel.basic_consume(
                queue=QUEUE_NAME,
                on_message_callback=callback,
                auto_ack=False,
            )

            log.info(
                "Connecte a RabbitMQ (%s), queue '%s', API cible : %s",
                RABBITMQ_HOST, QUEUE_NAME, API_URL,
            )
            print(f"[P3] Pret --- RabbitMQ {RABBITMQ_HOST}:{QUEUE_NAME}")
            print(f"     API cible : {API_URL}")
            channel.start_consuming()

        except KeyboardInterrupt:
            log.info("Arret demande")
            print("[P3] Arret.")
            sys.exit(0)

        except Exception as exc:
            log.error("Connexion RabbitMQ perdue : %s", exc)
            log.warning("Reconnexion dans 5 secondes...")
            print(f"[P3] Connexion perdue --- reconnexion dans 5s ({exc})")
            time.sleep(5)


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    print("=" * 46)
    print("  P3 - Service d'upload")
    print("  Envoi des MP3 enrichis vers l'API Django")
    print("=" * 46)
    listen()
