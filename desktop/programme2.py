import pika
import json
import os
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
from logger import get_logger

log = get_logger("programme2")

BLACKLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blacklist.json")
DUREE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "duree.json")


def load_blacklist():
    """Charge la blacklist depuis le fichier JSON."""
    if not os.path.exists(BLACKLIST_FILE):
        log.warning("Fichier blacklist.json introuvable, aucune blacklist active")
        return {"artistes": [], "genres": []}
    with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    artistes = [a.strip().lower() for a in data.get("artistes", []) if a.strip()]
    genres = [g.strip().lower() for g in data.get("genres", []) if g.strip()]
    log.info("Blacklist chargee : %d artiste(s), %d genre(s)", len(artistes), len(genres))
    return {"artistes": artistes, "genres": genres}


def load_duree():
    """Charge la durée max depuis duree.json. Retourne None si absent ou non configuré."""
    if not os.path.exists(DUREE_FILE):
        log.warning("Fichier duree.json introuvable, aucune limite de duree active")
        return None
    with open(DUREE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    duree_max = data.get("duree_max")
    if duree_max is None or duree_max == 0:
        return None
    log.info("Limite de duree chargee : %ss", duree_max)
    return float(duree_max)


def is_trop_longue(metadata, duree_max):
    """Retourne True si la durée du morceau dépasse la limite configurée."""
    if duree_max is None:
        return False
    return float(metadata.get("duree", 0)) > duree_max


def is_blacklisted(metadata, blacklist):
    """Verifie si un morceau correspond a un critere de la blacklist."""
    artiste = metadata.get("artiste", "").strip().lower()
    genre = metadata.get("genre", "").strip().lower()
    if artiste in blacklist["artistes"]:
        return True
    if genre in blacklist["genres"]:
        return True
    return False


def extract_metadata(filepath):
    """Lit les metadonnees ID3 d'un fichier MP3."""
    try:
        audio = MP3(filepath)
        tags = ID3(filepath)
        return {
            "duree": round(audio.info.length, 2),
            "artiste": str(tags.get('TPE1', 'Inconnu')).strip(),
            "titre": str(tags.get('TIT2', 'Sans titre')).strip(),
            "album": str(tags.get('TALB', 'Album inconnu')).strip(),
            "genre": str(tags.get('TCON', '')).strip(),
        }
    except Exception:
        log.warning("Lecture impossible du fichier %s, valeurs par defaut utilisees", filepath)
        return {
            "duree": 0,
            "artiste": "Inconnu",
            "titre": "Erreur lecture",
            "album": "Inconnu",
            "genre": "",
        }


def forward_to_p3(payload):
    """Publie les fichiers enrichis dans la queue list_mp3_p2."""
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()
    channel.queue_declare(queue="list_mp3_p2", durable=True)
    channel.basic_publish(
        exchange='',
        routing_key="list_mp3_p2",
        body=json.dumps(payload, ensure_ascii=False),
    )
    connection.close()


def on_message_received(ch, method, properties, body):
    """Callback RabbitMQ : recoit de P1, enrichit, transmet a P3."""
    raw_files = json.loads(body.decode('utf-8'))
    total = len(raw_files)
    log.info("Reception de %d fichier(s) de P1", total)
    print(f"\n[P2] {total} fichier(s) recu(s) de P1")

    blacklist = load_blacklist()
    duree_max = load_duree()
    enriched = []
    skipped = 0
    blacklisted = 0
    trop_longues = 0

    for item in raw_files:
        if not os.path.exists(item["path"]):
            log.warning("Fichier absent, ignore : %s", item["path"])
            skipped += 1
            continue
        item["metadata"] = extract_metadata(item["path"])
        meta = item["metadata"]
        if is_blacklisted(meta, blacklist):
            log.info("  [BLACKLISTE] %s | %s - %s (genre: %s)", item["name"], meta["artiste"], meta["titre"], meta["genre"])
            print(f"  [BLACKLISTE] {item['name']} --- {meta['artiste']} / {meta['genre']}")
            blacklisted += 1
            continue
        if is_trop_longue(meta, duree_max):
            log.info("  [TROP LONGUE] %s | %ss > %ss | %s - %s", item["name"], meta["duree"], duree_max, meta["artiste"], meta["titre"])
            print(f"  [TROP LONGUE] {item['name']} --- {meta['duree']}s > {duree_max}s")
            trop_longues += 1
            continue
        enriched.append(item)
        log.info("  %s | %ss | %s - %s", item["name"], meta["duree"], meta["artiste"], meta["titre"])

    if enriched:
        forward_to_p3(enriched)
        log.info("%d piste(s) transmise(s) a P3", len(enriched))
        print(f"[P2] {len(enriched)} piste(s) analysee(s) et transmise(s) a P3")
    if skipped:
        print(f"[P2] {skipped} fichier(s) ignore(s) (absent du disque)")
    if blacklisted:
        print(f"[P2] {blacklisted} piste(s) blacklistee(s)")
    if trop_longues:
        print(f"[P2] {trop_longues} piste(s) rejetee(s) (duree depassee)")

    ch.basic_ack(delivery_tag=method.delivery_tag)


if __name__ == '__main__':
    log.info("Demarrage du service d'extraction de metadonnees")
    print("[P2] Service d'extraction demarre, en attente de P1...")

    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()
    channel.queue_declare(queue='list_mp3_p1', durable=True)
    channel.basic_consume(
        queue='list_mp3_p1',
        on_message_callback=on_message_received,
        auto_ack=False,
    )
    channel.start_consuming()
