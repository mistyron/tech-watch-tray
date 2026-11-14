import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import data_dir

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_path() -> Path:
    return data_dir() / "tech-watch.log"


def _resolve_level(level: int | str | None) -> int:
    if level is None:
        level = os.environ.get("TECH_WATCH_LOG_LEVEL", "INFO")
    if isinstance(level, int):
        return level
    text = str(level).strip().upper()
    if text.isdigit():
        return int(text)
    return getattr(logging, text, logging.INFO)


def setup_logging(level: int | str | None = None) -> Path:
    path = log_path()
    root = logging.getLogger()
    formatter = logging.Formatter(_FORMAT)

    file_handler = next(
        (
            h
            for h in root.handlers
            if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(path)
        ),
        None,
    )
    if file_handler is None:
        file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    console_handler = next((h for h in root.handlers if getattr(h, "_tech_watch_console", False)), None)
    if console_handler is None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler._tech_watch_console = True
        root.addHandler(console_handler)

    resolved = _resolve_level(level)
    root.setLevel(resolved)
    file_handler.setLevel(resolved)
    console_handler.setLevel(resolved)
    return path
