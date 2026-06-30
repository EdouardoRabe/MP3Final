import os
import time
import json
import threading
import pika
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from logger import get_logger

log = get_logger("programme1")

WATCH_DIR = "./playlist"
pending_files = []
lock = threading.Lock()


def scan_existing_files():
    """Inventorie les fichiers MP3 deja presents dans le dossier surveille."""
    log.info("Demarrage du scan initial du dossier %s", WATCH_DIR)
    print("[P1] Scan initial du dossier de surveillance...")

    abs_watch = os.path.abspath(WATCH_DIR)
    for entry in os.listdir(abs_watch):
        if not entry.lower().endswith('.mp3'):
            continue
        full_path = os.path.join(abs_watch, entry)
        item = {"name": entry, "path": full_path}
        if item not in pending_files:
            pending_files.append(item)
            log.info("Fichier existant enregistre : %s", entry)
            print(f"  -> {entry}")


def publish_to_rabbitmq(payload):
    """Envoie un lot de fichiers a la queue list_mp3_p1."""
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()
    channel.queue_declare(queue="list_mp3_p1", durable=True)
    channel.basic_publish(
        exchange='',
        routing_key="list_mp3_p1",
        body=json.dumps(payload, ensure_ascii=False),
    )
    connection.close()


def flush_loop():
    """Toutes les 60s, vide le buffer vers RabbitMQ."""
    while True:
        time.sleep(10)
        with lock:
            if not pending_files:
                continue
            try:
                count = len(pending_files)
                publish_to_rabbitmq(pending_files)
                pending_files.clear()
                log.info("Lot de %d fichier(s) transmis a P2 (queue list_mp3_p1)", count)
                print(f"[P1] {count} fichier(s) transmis a P2")
            except Exception as exc:
                log.error("Echec de la publication RabbitMQ : %s", exc)
                print(f"[P1] ECHEC publication RabbitMQ : {exc}")


class FileCreatedHandler(FileSystemEventHandler):
    """Reagit a la creation de nouveaux fichiers .mp3 dans le dossier."""

    def _register(self, src_path):
        if not src_path.lower().endswith('.mp3'):
            return
        with lock:
            entry = {
                "name": os.path.basename(src_path),
                "path": src_path,
            }
            if entry not in pending_files:
                pending_files.append(entry)
                log.info("Nouveau fichier detecte : %s", entry["name"])
                print(f"[P1] Nouveau : {entry['name']}")

    def on_created(self, event):
        if event.is_directory:
            return
        self._register(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        self._register(event.dest_path)


if __name__ == "__main__":
    os.makedirs(WATCH_DIR, exist_ok=True)

    scan_existing_files()

    threading.Thread(target=flush_loop, daemon=True).start()

    observer = Observer()
    observer.schedule(
        FileCreatedHandler(), path=WATCH_DIR, recursive=False
    )
    observer.start()

    abs_path = os.path.abspath(WATCH_DIR)
    log.info("Surveillance activee : %s", abs_path)
    print(f"[P1] Surveillance activee : {abs_path}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Arret demande par l'utilisateur")
        print("[P1] Arret.")
        observer.stop()
