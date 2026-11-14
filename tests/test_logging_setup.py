import logging
from logging.handlers import RotatingFileHandler

from tech_watch.logging_setup import setup_logging


def test_setup_logging_writes_rotating_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    root = logging.getLogger()
    before = set(root.handlers)
    before_rotating = sum(isinstance(h, RotatingFileHandler) for h in before)
    try:
        path = setup_logging()
        assert path == tmp_path / "tech-watch" / "tech-watch.log"
        logging.getLogger("test").warning("hello")
        assert "hello" in path.read_text(encoding="utf-8")
        assert setup_logging() == path
        rotating = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(rotating) == before_rotating + 1
    finally:
        for handler in set(root.handlers) - before:
            root.removeHandler(handler)
            handler.close()


def test_setup_logging_writes_to_stdout(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("TECH_WATCH_LOG_LEVEL", raising=False)
    root = logging.getLogger()
    before = set(root.handlers)
    before_level = root.level
    try:
        setup_logging()
        setup_logging()
        console = [h for h in root.handlers if getattr(h, "_tech_watch_console", False)]
        assert len(console) == 1
        logging.getLogger("test.console").warning("visible in console")
        captured = capsys.readouterr()
        assert "WARNING test.console: visible in console" in captured.out
    finally:
        for handler in set(root.handlers) - before:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(before_level)


def test_setup_logging_reads_env_level(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("TECH_WATCH_LOG_LEVEL", "DEBUG")
    root = logging.getLogger()
    before = set(root.handlers)
    before_level = root.level
    try:
        setup_logging()
        assert root.level == logging.DEBUG
        setup_logging(level="WARNING")
        assert root.level == logging.WARNING
        setup_logging(level=logging.ERROR)
        assert root.level == logging.ERROR
    finally:
        for handler in set(root.handlers) - before:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(before_level)
