import os
from datetime import datetime

import pytest
from PyQt6.QtWidgets import QApplication

# Ensure offscreen before importing widgets that may touch Qt state.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tech_watch.app as app_mod


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_fmt_time_none():
    assert app_mod._fmt_time(None) == "never"
    assert app_mod._fmt_time("") == "never"


def test_fmt_time_formats_and_fallback():
    ts = "2024-05-06T13:14:15+00:00"
    assert app_mod._fmt_time(ts) == datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    assert app_mod._fmt_time("not-a-date") == "not-a-date"


def test_tray_icon_and_pixmap(qapp):
    for state in ("idle", "checking", "alert", "error", "ok", "unknown"):
        pm = app_mod._tray_icon_pixmap(state, 32)
        assert not pm.isNull()
        assert pm.width() == 32
        icon = app_mod._tray_icon(state)
        assert not icon.isNull()


def test_analysis_worker_emits(monkeypatch, qapp):
    calls = []
    signals = app_mod.WorkerSignals()
    signals.progress.connect(lambda s, d: calls.append(("p", s, d)))
    signals.result.connect(lambda r: calls.append(("r", r)))
    signals.finished.connect(lambda: calls.append(("f",)))

    monkeypatch.setattr(app_mod, "query_gemini", lambda sym, cfg, progress=None: f"res:{sym}")
    w = app_mod.AnalysisWorker(["A", "B"], {"x": 1}, signals)
    w.run()
    assert ("r", "res:A") in calls and ("r", "res:B") in calls
    assert calls[-1] == ("f",)
    assert any(c[0] == "p" and c[1] == "B" and c[2] == "pending" for c in calls)


def test_screener_worker_success(monkeypatch, qapp):
    signals = app_mod.ScreenerSignals()
    out = {}
    signals.progress.connect(lambda d, t, s: out.setdefault("p", []).append((d, t, s)))
    signals.done.connect(lambda r: out.setdefault("d", r))
    signals.error.connect(lambda e: out.setdefault("e", e))

    class Hit:
        score = 60

        def to_dict(self):
            return {"symbol": "A.TO", "score": 60}

    monkeypatch.setattr(app_mod, "get_universe", lambda mode, limit: [{"symbol": "A.TO"}])
    monkeypatch.setattr(
        app_mod,
        "screen_symbols",
        lambda u, min_score, progress_cb: progress_cb(1, 1, "A.TO") or [Hit()],
    )
    w = app_mod.ScreenerWorker("tsx_composite", 55, 10, signals)
    w.run()
    assert out["d"]["hits"][0]["symbol"] == "A.TO"
    assert out["d"]["scanned"] == 1
    assert out["d"]["mode"] == "tsx_composite"
    assert out["d"]["min_score"] == 55
    assert "e" not in out
    assert out.get("p")


def test_screener_worker_error(monkeypatch, qapp):
    signals = app_mod.ScreenerSignals()
    out = {}
    signals.error.connect(lambda e: out.setdefault("e", e))

    def boom(*a, **k):
        raise RuntimeError("bad")

    monkeypatch.setattr(app_mod, "get_universe", boom)
    w = app_mod.ScreenerWorker("tsx_composite", 55, 10, signals)
    w.run()
    assert "bad" in out["e"]


def test_main_no_tray(monkeypatch):
    # `main()` creates its own QApplication; pass a fake class that reuses the
    # existing Qt instance to avoid conflicting app lifecycles in pytest.
    existing = QApplication.instance()

    class FakeApp:
        def __init__(self, argv):
            self._app = existing

        def __getattr__(self, name):
            return getattr(self._app, name)

        def exec(self):
            return 0

    monkeypatch.setattr(app_mod, "QApplication", FakeApp)
    monkeypatch.setattr(app_mod.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    monkeypatch.setattr(app_mod.QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    assert app_mod.main(["prog"]) == 1

