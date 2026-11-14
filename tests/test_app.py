import logging
import os
from datetime import datetime, timedelta

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


def test_fmt_elapsed():
    now = datetime(2024, 1, 1, 12, 0, 0)
    assert app_mod._fmt_elapsed(None, now) == ""
    assert app_mod._fmt_elapsed(now - timedelta(seconds=5), now) == "5s"
    assert app_mod._fmt_elapsed(now - timedelta(seconds=75), now) == "1m 15s"
    assert app_mod._fmt_elapsed(now - timedelta(hours=2, minutes=5), now) == "2h 05m"


def test_watchdog_delay_accounts_for_fallbacks():
    cfg = {"ai": {"timeout_seconds": 10, "ollama_timeout_seconds": 20, "agy_fallback_models": ["a", "b"]}}
    per_symbol_ms = (10 * 3 + 20 + 60) * 1000
    assert app_mod._watchdog_delay_ms(cfg, 1) == per_symbol_ms
    assert app_mod._watchdog_delay_ms(cfg, 3) == per_symbol_ms * 3
    assert app_mod._watchdog_delay_ms({}, 1) == 420_000
    assert app_mod._watchdog_delay_ms({"ai": {"timeout_seconds": "bad"}}, 1) == 420_000


def test_details_dialog_shows_elapsed_status(qapp):
    dlg = app_mod.DetailsDialog({"symbols": ["HURA.TO"], "last_results": {}})
    started = datetime.now() - timedelta(seconds=75)
    dlg.set_check_status(
        {"HURA.TO": "receiving response from agy [model]"},
        {"HURA.TO": started},
    )
    assert dlg.overview_table.item(0, 2).text().startswith("UPDATING 1m")
    assert "receiving response" in dlg.overview_table.item(0, 4).text()
    assert "1m" in dlg.status_label.text()
    dlg._status_timer.stop()
    dlg.close()


def test_details_dialog_only_ticks_active_status(qapp):
    dlg = app_mod.DetailsDialog({"symbols": ["A.TO", "B.TO"], "last_results": {}})
    started = datetime.now() - timedelta(seconds=75)
    dlg.set_check_status(
        {"A.TO": "receiving response", "B.TO": "pending"},
        {"A.TO": started, "B.TO": started},
    )
    dlg._tick_status()
    assert dlg.overview_table.item(0, 2).text().startswith("UPDATING 1m")
    assert dlg.overview_table.item(1, 2).text() == "PENDING"
    assert dlg.overview_table.item(1, 4).text() == "Queued for update…"
    assert dlg.overview_table.cellWidget(0, 0).text() == ""
    assert "A.TO" in dlg.status_label.text()
    assert "B.TO" not in dlg.status_label.text()

    dlg.set_check_status(
        {"A.TO": "pending", "B.TO": "pending"},
        {"A.TO": started, "B.TO": started},
    )
    assert not dlg._status_timer.isActive()
    assert "waiting for next security" in dlg.status_label.text()
    dlg.close()


def test_details_dialog_check_button_invokes_callback(qapp):
    checked = []
    callback = checked.append
    dlg = app_mod.DetailsDialog(
        {"symbols": ["A.TO", "B.TO"], "last_results": {}},
        None,
        None,
        callback,
    )
    assert dlg._on_check_symbol is callback
    btn = dlg.overview_table.cellWidget(1, 0)
    assert btn is not None
    assert btn.text() == ""
    assert not btn.icon().isNull()
    btn.click()
    assert checked == ["B.TO"]
    dlg.close()


def test_details_dialog_disables_check_button_while_symbol_check_running(qapp):
    checked = []
    dlg = app_mod.DetailsDialog(
        {"symbols": ["A.TO", "B.TO"], "last_results": {}},
        None,
        None,
        checked.append,
    )
    dlg.set_check_status({"A.TO": "receiving response"}, {"A.TO": datetime.now()})
    btn_a = dlg.overview_table.cellWidget(0, 0)
    btn_b = dlg.overview_table.cellWidget(1, 0)
    assert not btn_a.isEnabled()
    assert btn_b.isEnabled()
    assert "already in progress" in btn_a.toolTip()
    btn_a.click()
    assert checked == []

    dlg.set_check_status({}, {})
    assert dlg.overview_table.cellWidget(0, 0).isEnabled()
    dlg.close()


def test_details_dialog_check_button_without_callback_is_safe(qapp):
    dlg = app_mod.DetailsDialog({"symbols": ["A.TO"], "last_results": {}})
    assert dlg._on_check_symbol is None
    dlg.overview_table.cellWidget(0, 0).click()
    dlg.close()


def test_show_details_wires_single_symbol_check(qapp, monkeypatch):
    monkeypatch.setattr(app_mod, "load_config", lambda: {"symbols": ["A.TO"], "last_results": {}})

    class FakeTray:
        show_details = app_mod.TrayApp.show_details

        def __init__(self):
            self._details_dialog = None
            self._check_status = {}
            self._status_since = {}

        def check_single_symbol(self, symbol):
            pass

        def _details_closed(self):
            self._details_dialog = None

    tray = FakeTray()
    tray.show_details("A.TO")
    dlg = tray._details_dialog
    assert dlg is not None
    assert dlg._on_check_symbol == tray.check_single_symbol
    dlg.close()


def test_details_dialog_overview_double_click_opens_symbol(qapp):
    dlg = app_mod.DetailsDialog({"symbols": ["A.TO", "B.TO"], "last_results": {}})
    dlg._open_symbol_from_overview(1, 1)
    assert dlg.symbol_box.currentText() == "B.TO"
    assert dlg.tabs.currentIndex() == 1
    tabs = dlg.detail_tabs.count()
    assert tabs == 2
    dlg.close()


def test_details_dialog_summary_column_sized_to_contents(qapp):
    dlg = app_mod.DetailsDialog({"symbols": ["A.TO"], "last_results": {}})
    header = dlg.overview_table.horizontalHeader()
    for col in range(dlg.overview_table.columnCount()):
        assert header.sectionResizeMode(col) == app_mod.QHeaderView.ResizeMode.ResizeToContents
    dlg.close()


def test_check_single_symbol_starts_busy_indicator(qapp, monkeypatch):
    class FakeWorker:
        def __init__(self, symbols, cfg, signals):
            self.symbols = symbols

        def start(self):
            pass

    monkeypatch.setattr(app_mod, "AnalysisWorker", FakeWorker)
    monkeypatch.setattr(app_mod.QTimer, "singleShot", staticmethod(lambda *a, **k: None))

    class FakeTray:
        check_single_symbol = app_mod.TrayApp.check_single_symbol

        def __init__(self):
            self.check_in_progress = False
            self.cfg = {"symbols": ["A.TO"], "last_results": {}}
            self._check_generation = 0
            self._check_status = {}
            self._status_since = {}
            self._check_started_at = None
            self.calls = []
            self._update_tray_state = lambda: self.calls.append("tray")
            self._rebuild_menu = lambda: self.calls.append("menu")
            self._start_busy_indicator = lambda: self.calls.append("busy")
            self._sync_details_status = lambda: None
            self._refresh_details_data = lambda: None
            self.tray = type("T", (), {"showMessage": lambda self, *a, **k: None})()

    tray = FakeTray()
    tray.check_single_symbol("A.TO")
    assert tray.check_in_progress is True
    assert tray._check_status == {"A.TO": "pending"}
    assert tray.calls == ["tray", "menu", "busy"]


def test_on_progress_resets_timer_for_next_symbol():
    old = datetime.now() - timedelta(seconds=30)

    class FakeTray:
        _on_progress = app_mod.TrayApp._on_progress

        def __init__(self):
            self._check_status = {"A.TO": "pending", "B.TO": "pending"}
            self._status_since = {"A.TO": old, "B.TO": old}
            self._check_generation = 0
            self.calls = []
            self._sync_details_status = lambda: self.calls.append("sync")

    tray = FakeTray()
    tray._on_progress("A.TO", "preparing check")
    first_started = tray._status_since["A.TO"]
    assert first_started > old

    tray._on_progress("A.TO", "receiving response")
    assert tray._status_since["A.TO"] == first_started

    tray._on_progress("B.TO", "preparing check")
    assert tray._status_since["B.TO"] > first_started
    assert tray.calls == ["sync", "sync", "sync"]


def test_manage_symbols_dialog_add_remove_apply(qapp, monkeypatch):
    cfg = {"symbols": ["XIC.TO", "HURA.TO"], "check_interval_hours": 24}
    dlg = app_mod.ManageSymbolsDialog(cfg)
    assert [dlg.list_widget.item(i).text() for i in range(dlg.list_widget.count())] == ["XIC.TO", "HURA.TO"]

    monkeypatch.setattr(app_mod.QInputDialog, "getText", lambda *a, **k: ("aapl", True))
    dlg.add_symbol()
    assert [dlg.list_widget.item(i).text() for i in range(dlg.list_widget.count())] == [
        "XIC.TO",
        "HURA.TO",
        "AAPL",
    ]

    dlg.list_widget.item(0).setSelected(True)
    dlg.remove_selected()
    dlg.interval.setValue(6)
    applied = dlg.apply()

    assert applied is cfg
    assert applied["symbols"] == ["HURA.TO", "AAPL"]
    assert applied["check_interval_hours"] == 6
    dlg.close()


def test_tray_menu_keeps_expected_actions(qapp):
    class FakeApp:
        def quit(self):
            pass

    class FakeTray:
        _rebuild_menu = app_mod.TrayApp._rebuild_menu

        def __init__(self):
            self.menu = app_mod.QMenu()
            self.check_in_progress = False
            self.cfg = {"symbols": ["XIC.TO"], "last_results": {}}
            self.app = FakeApp()
            self._update_tray_state = lambda: None
            self.run_daily_check = lambda: None
            self.cancel_check = lambda: None
            self.manage_symbols = lambda: None
            self.show_settings = lambda: None
            self.show_screener = lambda: None
            self.show_config_location = lambda: None

    tray = FakeTray()
    tray._rebuild_menu()
    texts = [a.text() for a in tray.menu.actions() if not a.isSeparator()]
    assert texts == [
        "Check all now",
        "Manage securities...",
        "Settings...",
        "TSX Screener...",
        "Open config file location",
        "Quit",
    ]

    tray.check_in_progress = True
    tray._rebuild_menu()
    texts = [a.text() for a in tray.menu.actions() if not a.isSeparator()]
    assert "Cancel check" in texts
    manage = next(a for a in tray.menu.actions() if a.text() == "Manage securities...")
    assert not manage.isEnabled()


def test_manage_symbols_saves_and_refreshes(monkeypatch):
    cfg = {"symbols": ["XIC.TO"]}

    class FakeDialog:
        def __init__(self, dialog_cfg, parent=None):
            self.cfg = dialog_cfg

        def exec(self):
            return app_mod.QDialog.DialogCode.Accepted

        def apply(self):
            self.cfg["symbols"] = ["AAPL"]
            return self.cfg

    monkeypatch.setattr(app_mod, "ManageSymbolsDialog", FakeDialog)

    class FakeTray:
        manage_symbols = app_mod.TrayApp.manage_symbols

        def __init__(self):
            self.cfg = cfg
            self.calls = []

        def _save(self):
            self.calls.append("save")

        def _apply_timer(self):
            self.calls.append("timer")

        def _rebuild_menu(self):
            self.calls.append("menu")

        def _refresh_details_data(self):
            self.calls.append("data")

        def _sync_details_status(self):
            self.calls.append("sync")

    tray = FakeTray()
    tray.manage_symbols()
    assert tray.cfg["symbols"] == ["AAPL"]
    assert tray.calls == ["save", "timer", "menu", "data", "sync"]


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


def test_analysis_worker_logs_each_result(monkeypatch, qapp, caplog):
    signals = app_mod.WorkerSignals()

    def fake_query(sym, cfg, progress=None):
        if sym == "A":
            return app_mod.AnalysisResult(sym, "ERROR", "boom", "", False, "boom", "t")
        return app_mod.AnalysisResult(sym, "BUY", "ok", "body", True, "", "t")

    monkeypatch.setattr(app_mod, "query_gemini", fake_query)
    with caplog.at_level(logging.INFO, logger="tech_watch.app"):
        app_mod.AnalysisWorker(["A", "B"], {}, signals).run()
    assert "Check failed for A: boom" in caplog.text
    assert "Check completed for B: BUY" in caplog.text


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
        lambda u, min_score, progress_cb, errors=None: progress_cb(1, 1, "A.TO") or [Hit()],
    )
    w = app_mod.ScreenerWorker("tsx_composite", 55, 10, signals)
    w.run()
    assert out["d"]["hits"][0]["symbol"] == "A.TO"
    assert out["d"]["scanned"] == 1
    assert out["d"]["mode"] == "tsx_composite"
    assert out["d"]["min_score"] == 55
    assert out["d"]["errors"] == []
    assert "e" not in out
    assert out.get("p")


def test_screener_worker_includes_partial_errors(monkeypatch, qapp):
    signals = app_mod.ScreenerSignals()
    out = {}
    signals.done.connect(lambda r: out.setdefault("d", r))

    class Hit:
        score = 60

        def to_dict(self):
            return {"symbol": "A.TO", "score": 60}

    def fake_screen(universe, min_score, progress_cb, errors=None):
        errors.append("Y.TO: no data")
        return [Hit()]

    monkeypatch.setattr(screener_mod, "get_universe", lambda mode, limit: [{"symbol": "A.TO"}])
    monkeypatch.setattr(screener_mod, "screen_symbols", fake_screen)
    w = app_mod.ScreenerWorker("tsx_composite", 55, 10, signals)
    w.run()
    assert out["d"]["errors"] == ["Y.TO: no data"]


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


def test_analysis_worker_cancel_stops_before_next_symbol(monkeypatch, qapp):
    calls = []
    finished = []
    signals = app_mod.WorkerSignals()
    signals.finished.connect(lambda: finished.append(True))
    worker = app_mod.AnalysisWorker(["A", "B"], {}, signals)

    def fake_query(sym, cfg, progress=None):
        calls.append(sym)
        worker.cancel()
        return f"res:{sym}"

    monkeypatch.setattr(app_mod, "query_gemini", fake_query)
    worker.run()
    assert calls == ["A"]
    assert finished == [True]


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


def test_finished_clears_status_and_syncs_details():
    class FakeTray:
        _on_finished = app_mod.TrayApp._on_finished

        def __init__(self):
            self.check_in_progress = True
            self._active_thread = object()
            self._check_status = {"HURA.TO": "receiving response"}
            self._status_since = {"HURA.TO": datetime.now()}
            self._check_started_at = datetime.now()
            self.calls = []
            self._rebuild_menu = lambda: self.calls.append("menu")
            self._refresh_details_data = lambda: self.calls.append("data")
            self._sync_details_status = lambda: self.calls.append("sync")

    tray = FakeTray()
    tray._on_finished()
    assert tray.check_in_progress is False
    assert tray._active_thread is None
    assert tray._check_status == {}
    assert tray._status_since == {}
    assert tray._check_started_at is None
    assert tray.calls == ["menu", "data", "sync"]


def test_cancel_check_calls_worker_and_notifies():
    class Worker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    class FakeTray:
        cancel_check = app_mod.TrayApp.cancel_check

        def __init__(self):
            self.worker = Worker()
            self._active_thread = self.worker
            self.messages = []
            self.tray = type(
                "Tray",
                (),
                {"showMessage": lambda self, *a, **k: self.owner.messages.append(a)},
            )()
            self.tray.owner = self

    tray = FakeTray()
    tray.cancel_check()
    assert tray.worker.cancelled is True
    assert "Cancelling" in tray.messages[0][1]


def test_watchdog_resets_even_if_worker_still_alive():
    class Worker:
        def __init__(self):
            self.cancelled = False

        def is_alive(self):
            return True

        def cancel(self):
            self.cancelled = True

    class FakeTray:
        _check_watchdog = app_mod.TrayApp._check_watchdog

        def __init__(self):
            self.check_in_progress = True
            self.worker = Worker()
            self._active_thread = self.worker
            self._check_status = {"A": "pending"}
            self._status_since = {"A": datetime.now()}
            self._check_started_at = datetime.now()
            self.calls = []
            self._rebuild_menu = lambda: self.calls.append("menu")
            self._refresh_details_data = lambda: self.calls.append("data")
            self._sync_details_status = lambda: self.calls.append("sync")
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
    assert tray._status_since == {}
    assert tray._check_started_at is None
    assert tray.worker.cancelled is True
    assert tray.calls == ["menu", "data", "sync"]
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


def test_stale_result_is_logged(caplog):
    class FakeTray:
        _on_result = app_mod.TrayApp._on_result

        def __init__(self):
            self._check_generation = 2

    result = app_mod.AnalysisResult("A", "ERROR", "boom", "", False, "boom", "t")
    with caplog.at_level(logging.INFO, logger="tech_watch.app"):
        FakeTray()._on_result(result, 1)
    assert "Ignoring stale result for A" in caplog.text


def test_main_no_tray(monkeypatch):
    # `main()` creates its own QApplication; pass a fake class that reuses the
    # existing Qt instance to avoid conflicting app lifecycles in pytest.
    monkeypatch.setattr(app_mod, "setup_logging", lambda: None)
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

