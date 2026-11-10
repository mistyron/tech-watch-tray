import os
from datetime import datetime

import pytest
from PyQt6.QtWidgets import QApplication

# Ensure offscreen before importing widgets that may touch Qt state.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tech_watch.app as app_mod
import tech_watch.screener as screener_mod


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

    monkeypatch.setattr(screener_mod, "get_universe", lambda mode, limit: [{"symbol": "A.TO"}])
    monkeypatch.setattr(
        screener_mod,
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

    monkeypatch.setattr(screener_mod, "get_universe", boom)
    w = app_mod.ScreenerWorker("tsx_composite", 55, 10, signals)
    w.run()
    assert "bad" in out["e"]


def test_analysis_worker_continues_after_exception(monkeypatch, qapp):
    calls = []
    signals = app_mod.WorkerSignals()
    signals.result.connect(lambda r: calls.append(("r", r)))
    signals.finished.connect(lambda: calls.append(("f",)))

    def fake_query(sym, cfg, progress=None):
        if sym == "A":
            raise RuntimeError("boom")
        return app_mod.AnalysisResult(sym, "BUY", "ok", "body", True, "", "t")

    monkeypatch.setattr(app_mod, "query_gemini", fake_query)
    w = app_mod.AnalysisWorker(["A", "B"], {}, signals)
    w.run()

    results = {}
    for item in calls:
        if item[0] == "r":
            results[item[1].symbol] = item[1]
    assert results["A"].verdict == "ERROR" and "boom" in results["A"].error
    assert results["B"].verdict == "BUY"
    assert calls[-1] == ("f",)


def test_finished_clears_status_and_syncs_dialog():
    class FakeTray:
        _on_finished = app_mod.TrayApp._on_finished

        def __init__(self):
            self.check_in_progress = True
            self._active_thread = object()
            self._check_status = {"A": "pending"}
            self.calls = []
            self._sync_details_status = lambda: self.calls.append("sync")
            self._rebuild_menu = lambda: self.calls.append("menu")
            self._refresh_details_data = lambda: self.calls.append("data")

    tray = FakeTray()
    tray._on_finished()
    assert tray.check_in_progress is False
    assert tray._active_thread is None
    assert tray._check_status == {}
    assert tray.calls == ["sync", "menu", "data"]


def test_watchdog_resets_even_if_worker_still_alive():
    class FakeTray:
        _check_watchdog = app_mod.TrayApp._check_watchdog

        def __init__(self):
            self.check_in_progress = True
            self._active_thread = type("T", (), {"is_alive": lambda self: True})()
            self._check_status = {"A": "pending"}
            self.calls = []
            self._sync_details_status = lambda: self.calls.append("sync")
            self._rebuild_menu = lambda: self.calls.append("menu")
            self._refresh_details_data = lambda: self.calls.append("data")
            self.messages = []
            self.tray = type(
                "Tray",
                (),
                {"showMessage": lambda self, *a, **k: self.owner.messages.append(a)},
            )()
            self.tray.owner = self

    tray = FakeTray()
    tray._check_watchdog()
    assert tray.check_in_progress is False
    assert tray._check_status == {}
    assert tray.calls == ["sync", "menu", "data"]
    assert "still running" in tray.messages[0][1]


def test_stale_check_generation_is_ignored():
    class FakeTray:
        _on_finished = app_mod.TrayApp._on_finished
        _check_watchdog = app_mod.TrayApp._check_watchdog

        def __init__(self):
            self.check_in_progress = True
            self._active_thread = object()
            self._check_status = {"A": "pending"}
            self._check_generation = 2
            self.calls = []
            self._sync_details_status = lambda: self.calls.append("sync")
            self._rebuild_menu = lambda: self.calls.append("menu")
            self._refresh_details_data = lambda: self.calls.append("data")

    tray = FakeTray()
    tray._on_finished(1)
    tray._check_watchdog(1)
    assert tray.check_in_progress is True
    assert tray._check_status == {"A": "pending"}
    assert tray.calls == []


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

