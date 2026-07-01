"""
P4 --- Service de nettoyage
============================
Consomme la queue RabbitMQ list_mp3_p3 et supprime les fichiers MP3
locaux dont l'upload vers l'API Django a ete confirme par P3.
"""
import json
import os
import sys
import time

import pika
from logger import get_logger

log = get_logger("programme4")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RABBITMQ_HOST = os.environ.get('RABBITMQ_HOST', 'localhost')
QUEUE_NAME = 'list_mp3_p3'


# ---------------------------------------------------------------------------
# Traitement des messages
# ---------------------------------------------------------------------------
def handle_message(body: bytes):
    """Supprime le fichier local dont le chemin est indique dans le message."""
    try:
        item = json.loads(body.decode('utf-8'))
    except json.JSONDecodeError as exc:
        log.error("Payload JSON invalide : %s", exc)
        print("[P4] Payload JSON invalide, message ignore")
        return

    name = item.get('name', '?')
    path = item.get('path', '')

    log.info("Nettoyage demande : %s", name)
    print(f"\n[P4] Suppression : {name}")

    if not path:
        log.warning("Chemin vide, suppression impossible pour : %s", name)
        print(f"  [IGNORE] Chemin vide pour {name}")
        return

    if os.path.isfile(path):
        try:
            os.remove(path)
            log.info("Fichier supprime : %s", name)
            print(f"  [OK] {name} supprime")
        except OSError as exc:
            log.error("Impossible de supprimer %s : %s", name, exc)
            print(f"  [ERREUR] Impossible de supprimer {name} : {exc}")
    else:
        log.warning("Fichier deja absent (ignore) : %s", name)
        print(f"  [ABSENT] {name} n'existe plus sur le disque")


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
                "Connecte a RabbitMQ (%s), queue '%s'",
                RABBITMQ_HOST, QUEUE_NAME,
            )
            print(f"[P4] Pret --- RabbitMQ {RABBITMQ_HOST}:{QUEUE_NAME}")
            channel.start_consuming()

        except KeyboardInterrupt:
            log.info("Arret demande")
            print("[P4] Arret.")
            sys.exit(0)

        except Exception as exc:
            log.error("Connexion RabbitMQ perdue : %s", exc)
            log.warning("Reconnexion dans 5 secondes...")
            print(f"[P4] Connexion perdue --- reconnexion dans 5s ({exc})")
            time.sleep(5)


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    print("=" * 46)
    print("  P4 - Service de nettoyage")
    print("  Suppression des MP3 locaux apres upload")
    print("=" * 46)
    listen()
