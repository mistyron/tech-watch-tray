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
