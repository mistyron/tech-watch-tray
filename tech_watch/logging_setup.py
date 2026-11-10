import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import data_dir


def setup_logging(level: int = logging.INFO) -> Path:
    path = data_dir() / "tech-watch.log"
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and getattr(handler, "baseFilename", "") == str(path):
            return path
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    return path
