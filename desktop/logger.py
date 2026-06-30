"""
Module de log partage pour les programmes 1, 2 et 3.
Chaque programme ecrit dans son propre fichier horodate dans `logs/`
et affiche simultanement dans la console.

Utilisation :
    from logger import get_logger
    log = get_logger("programme1")
    log.info("Nouveau fichier detecte : ma_chanson.mp3")
    log.error("Impossible de se connecter a RabbitMQ")
"""

import os
import logging
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

_loggers: dict[str, logging.Logger] = {}


def get_logger(program_name: str) -> logging.Logger:
    """
    Fournit un logger configure avec deux sorties :
      - Fichier : logs/programmeX_AAAA-MM-JJ.log (niveau DEBUG)
      - Console : stdout (niveau INFO)

    Args:
        program_name: Identifiant du programme (ex: "programme1").

    Returns:
        Logger pret a l'emploi.
    """
    if program_name in _loggers:
        return _loggers[program_name]

    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger(program_name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        _loggers[program_name] = logger
        return logger

    # --- Sortie fichier (DEBUG et plus) ---
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_filename = os.path.join(LOG_DIR, f"{program_name}_{date_str}.log")

    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(file_handler)

    # --- Sortie console (INFO et plus) ---
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(console_handler)

    _loggers[program_name] = logger
    return logger
