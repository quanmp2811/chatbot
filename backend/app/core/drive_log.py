from __future__ import annotations

import logging
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "drive-sync.log"


def get_drive_logger() -> logging.Logger:
    logger = logging.getLogger("app.drive")
    if logger.handlers:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_drive_info(message: str) -> None:
    get_drive_logger().info(message)


def log_drive_error(message: str) -> None:
    get_drive_logger().error(message)
