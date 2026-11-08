import html
import signal
import sys
import threading
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import config_path, load_config, save_config
from .gemini import AnalysisResult, query_gemini


def _fmt_time(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    finished = pyqtSignal()


class AnalysisWorker(threading.Thread):
    def __init__(self, symbols: list[str], cfg: dict[str, Any], signals: WorkerSignals):
        super().__init__(daemon=True)
        self.symbols = list(symbols)
        self.cfg = dict(cfg)
        self.signals = signals

    def run(self) -> None:
        try:
            for symbol in self.symbols:
                result = query_gemini(symbol, self.cfg)
                self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


class ManageSymbolsDialog(QDialog):
    def __init__(self, cfg: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Tech Watch - Securities")
        self.cfg = cfg
        self.resize(420, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Symbols monitored daily (e.g., CGL.TO, HURA.TO):"))
        self.list_widget = QListWidget()
        for s in cfg.get("symbols", []):
            self.list_widget.addItem(QListWidgetItem(str(s)))
        layout.addWidget(self.list_widget)

        row = QHBoxLayout()
        add_btn = QPushButton("Add")
        remove_btn = QPushButton("Remove")
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)
        layout.addLayout(row)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Check interval (hours):"))
        self.interval = QSpinBox()
        self.interval.setRange(1, 168)
        self.interval.setValue(int(cfg.get("check_interval_hours") or 24))
        interval_row.addWidget(self.interval)
        interval_row.addStretch(1)
        layout.addLayout(interval_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        add_btn.clicked.connect(self.add_symbol)
        remove_btn.clicked.connect(self.remove_selected)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def add_symbol(self) -> None:
        text, ok = QInputDialog.getText(self, "Add Security", "Symbol/ticker (e.g., AAPL, CGL.TO):")
        if not ok:
            return
        symbol = text.strip().upper()
        if not symbol:
            return
        existing = {self.list_widget.item(i).text().upper() for i in range(self.list_widget.count())}
        if symbol in existing:
            return
        self.list_widget.addItem(QListWidgetItem(symbol))

    def remove_selected(self) -> None:
        for item in self.list_widget.selectedItems():
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)

    def apply(self) -> dict[str, Any]:
        symbols = []
        for i in range(self.list_widget.count()):
            s = self.list_widget.item(i).text().strip().upper()
            if s and s not in symbols:
                symbols.append(s)
        self.cfg["symbols"] = symbols
        self.cfg["check_interval_hours"] = int(self.interval.value())
        return self.cfg


class DetailsDialog(QDialog):
    def __init__(self, cfg: dict[str, Any], current_symbol: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Tech Watch - Details")
        self.resize(860, 620)
        layout = QVBoxLayout(self)

        nav = QHBoxLayout()
        nav.addWidget(QLabel("Security:"))
        self.symbol_box = QComboBox()
        self.symbols = [str(s).strip().upper() for s in cfg.get("symbols", []) if str(s).strip()]
        for s in self.symbols:
            self.symbol_box.addItem(s)
        nav.addWidget(self.symbol_box, 1)
        layout.addLayout(nav)

        self.tabs = QTabWidget()
        self.summary_box = QPlainTextEdit()
        self.summary_box.setReadOnly(True)
        self.response_box = QPlainTextEdit()
        self.response_box.setReadOnly(True)
        self.tabs.addTab(self.summary_box, "Summary")
        self.tabs.addTab(self.response_box, "AI response")
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.symbol_box.currentTextChanged.connect(self._load_symbol)
        if current_symbol and current_symbol in self.symbols:
            self.symbol_box.setCurrentText(current_symbol)
        elif self.symbols:
            self._load_symbol(self.symbols[0])
        else:
            self.summary_box.setPlainText("No securities configured.")

    def _fmt_time(self, ts: str | None) -> str:
        if not ts:
            return "never"
        try:
            dt = datetime.fromisoformat(ts)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ts

    def _load_symbol(self, symbol: str) -> None:
        symbol = (symbol or "").strip().upper()
        results = self.cfg.get("last_results") if isinstance(self.cfg.get("last_results"), dict) else {}
        data = results.get(symbol) or {}
        if not data:
            self.summary_box.setPlainText(f"Symbol: {symbol}\n\nNo analysis yet. Run 'Check all now' from the tray menu.")
            self.response_box.setPlainText("")
            return
        verdict = data.get("verdict", "—")
        summary = data.get("summary", "")
        checked = self._fmt_time(data.get("checked_at"))
        error = data.get("error") or ""
        full = data.get("full_text") or ""
        text = (
            f"Symbol: {symbol}\n"
            f"Verdict: {verdict}\n"
            f"Checked: {checked}\n\n"
            f"Summary:\n{summary}\n"
        )
        if error:
            text += f"\nError:\n{error}\n"
        self.summary_box.setPlainText(text)
        self.response_box.setPlainText(full)


class TrayApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.cfg = load_config()
        self.check_in_progress = False
        self._last_notified: dict[str, str] = {}
        self._active_thread: AnalysisWorker | None = None

        self.tray = QSystemTrayIcon(self._icon_for_state("idle"))
        self.tray.setToolTip("Tech Watch: idle")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.run_daily_check)
        self._apply_timer()

        self._rebuild_menu()
        self.tray.show()

        # Delay first automatic run slightly so tray/session settles.
        QTimer.singleShot(15_000, self.run_daily_check_if_due)

    def _icon_for_state(self, state: str) -> QIcon:
        # Use theme icons for portability in KDE/Plasma.
        names = {
            "idle": ["view-refresh", "clock", "utilities-system-monitor"],
            "checking": ["process-working", "view-refresh", "system-run"],
            "alert": ["dialog-warning", "emblem-important", "flag-yellow"],
            "error": ["dialog-error", "process-stop"],
            "ok": ["dialog-information", "security-high"],
        }
        for name in names.get(state, names["idle"]):
            icon = QIcon.fromTheme(name)
            if not icon.isNull():
                return icon
        return self.app.windowIcon()

    def _save(self) -> None:
        save_config(self.cfg)

    def _apply_timer(self) -> None:
        hours = max(1, int(self.cfg.get("check_interval_hours") or 24))
        self.timer.start(hours * 60 * 60 * 1000)

    def _results(self) -> dict[str, Any]:
        res = self.cfg.get("last_results")
        return res if isinstance(res, dict) else {}

    def _rebuild_menu(self) -> None:
        self.menu.clear()

        header = QAction("Check all now", self.menu)
        header.triggered.connect(self.run_daily_check)
        header.setEnabled(not self.check_in_progress)
        self.menu.addAction(header)

        manage = QAction("Manage securities...", self.menu)
        manage.triggered.connect(self.manage_symbols)
        self.menu.addAction(manage)

        ai_act = QAction("Set AI command...", self.menu)
        ai_act.triggered.connect(self.set_ai_command)
        self.menu.addAction(ai_act)

        config_act = QAction("Open config file location", self.menu)
        config_act.triggered.connect(self.show_config_location)
        self.menu.addAction(config_act)

        self.menu.addSeparator()

        results = self._results()
        symbols = self.cfg.get("symbols", [])
        if not symbols:
            empty = QAction("No securities configured", self.menu)
            empty.setEnabled(False)
            self.menu.addAction(empty)
        for symbol in symbols:
            data = results.get(symbol) or {}
            verdict = str(data.get("verdict") or "—")
            checked = _fmt_time(data.get("checked_at"))
            summary = str(data.get("summary") or "")
            label = f"{symbol}: {verdict} ({checked})"
            act = QAction(label, self.menu)
            act.triggered.connect(lambda _=False, s=symbol: self.show_details(s))
            if summary:
                act.setToolTip(summary)
            self.menu.addAction(act)

        self.menu.addSeparator()

        quit_act = QAction("Quit", self.menu)
        quit_act.triggered.connect(self.app.quit)
        self.menu.addAction(quit_act)

        self._update_tray_state()

    def _update_tray_state(self) -> None:
        if self.check_in_progress:
            self.tray.setIcon(self._icon_for_state("checking"))
            self.tray.setToolTip("Tech Watch: checking AI…")
            return

        results = self._results()
        notify_on = {str(v).upper() for v in (self.cfg.get("notify_on") or ["BUY"])}
        alert = False
        error = False
        lines = []
        for symbol in self.cfg.get("symbols", []):
            data = results.get(symbol) or {}
            verdict = str(data.get("verdict") or "—").upper()
            if verdict in notify_on and verdict not in {"", "—", "UNKNOWN"}:
                alert = True
            if verdict == "ERROR":
                error = True
            lines.append(f"{symbol}: {verdict} @ {_fmt_time(data.get('checked_at'))}")

        state = "alert" if alert else "error" if error else "idle" if not lines else "ok"
        self.tray.setIcon(self._icon_for_state(state))
        tip = "Tech Watch\n" + ("\n".join(lines) if lines else "No securities configured")
        self.tray.setToolTip(tip[:1024])

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_details(None)

    def run_daily_check_if_due(self) -> None:
        results = self._results()
        hours = max(1, int(self.cfg.get("check_interval_hours") or 24))
        now = datetime.now().astimezone()
        due = False
        for symbol in self.cfg.get("symbols", []):
            ts = (results.get(symbol) or {}).get("checked_at")
            if not ts:
                due = True
                break
            try:
                last = datetime.fromisoformat(ts)
                age_hours = (now - last).total_seconds() / 3600
                if age_hours >= hours:
                    due = True
                    break
            except Exception:
                due = True
                break
        if due:
            self.run_daily_check()

    def run_daily_check(self) -> None:
        if self.check_in_progress:
            return
        symbols = [str(s).strip().upper() for s in self.cfg.get("symbols", []) if str(s).strip()]
        if not symbols:
            self.tray.showMessage(
                "Tech Watch",
                "No securities configured. Use 'Manage securities...' to add symbols.",
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )
            return
        self.check_in_progress = True
        self._update_tray_state()
        self.tray.showMessage(
            "Tech Watch",
            f"Checking technicals for: {', '.join(symbols)}",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )
        signals = WorkerSignals()
        signals.result.connect(self._on_result)
        signals.finished.connect(self._on_finished)
        thread = AnalysisWorker(symbols, self.cfg, signals)
        self._active_thread = thread
        thread.start()

    def _on_result(self, result_obj: object) -> None:
        if not isinstance(result_obj, AnalysisResult):
            return
        results = self._results()
        prev = results.get(result_obj.symbol) or {}
        prev_verdict = str(prev.get("verdict") or "")
        prev_checked = str(prev.get("checked_at") or "")
        results[result_obj.symbol] = result_obj.to_dict()
        self.cfg["last_results"] = results
        self._save()
        self._rebuild_menu()

        notify_on = {str(v).upper() for v in (self.cfg.get("notify_on") or ["BUY"])}
        verdict = (result_obj.verdict or "").upper()
        notify_key = f"{result_obj.symbol}:{verdict}:{result_obj.checked_at[:10]}"
        changed_to_alert = verdict in notify_on and (prev_verdict.upper() != verdict or not prev_checked)
        first_alert = verdict in notify_on and self._last_notified.get(result_obj.symbol) != notify_key
        if result_obj.ok and changed_to_alert and first_alert:
            self._last_notified[result_obj.symbol] = notify_key
            self.tray.showMessage(
                f"Tech Watch: {result_obj.symbol}",
                f"Verdict: {verdict}\n{result_obj.summary}",
                QSystemTrayIcon.MessageIcon.Warning if verdict == "BUY" else QSystemTrayIcon.MessageIcon.Information,
                15000,
            )
        elif not result_obj.ok:
            msg = result_obj.summary or result_obj.error or "Check failed"
            if result_obj.error and result_obj.error not in msg and "IneligibleTierError" not in msg:
                msg = f"{msg}\n{(result_obj.error[:180] + '…') if len(result_obj.error) > 180 else result_obj.error}"
            self.tray.showMessage(
                f"Tech Watch: {result_obj.symbol}",
                f"Check failed: {msg}",
                QSystemTrayIcon.MessageIcon.Critical,
                15000,
            )

    def _on_finished(self) -> None:
        self.check_in_progress = False
        self._active_thread = None
        self._rebuild_menu()

    def manage_symbols(self) -> None:
        dlg = ManageSymbolsDialog(self.cfg, None)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cfg = dlg.apply()
            # prune removed symbols from results? keep history; harmless
            self._save()
            self._apply_timer()
            self._rebuild_menu()

    def set_ai_command(self) -> None:
        import shlex

        ai = self.cfg.get("ai") if isinstance(self.cfg.get("ai"), dict) else {}
        current = ai.get("command") or ["gemini", "-p", "{prompt}"]
        try:
            current_s = shlex.join([str(x) for x in current])
        except Exception:
            current_s = 'gemini -p "{prompt}"'
        text, ok = QInputDialog.getText(
            None,
            "Tech Watch - AI command",
            "Command argv (use {prompt} placeholder). Example: gemini -p {prompt}\n",
            text=current_s,
        )
        if not ok:
            return
        try:
            parts = shlex.split(text.strip())
        except Exception as e:
            QMessageBox.warning(None, "Tech Watch", f"Could not parse command: {e}")
            return
        if not parts:
            return
        if not any("{prompt}" in p for p in parts):
            parts.append("{prompt}")
        self.cfg.setdefault("ai", {})["command"] = parts
        self._save()
        self._rebuild_menu()
        self.tray.showMessage(
            "Tech Watch",
            "AI command saved. Use 'Check all now' to test. If checks fail, the notification/details will show the command error.",
            QSystemTrayIcon.MessageIcon.Information,
            10000,
        )

    def show_details(self, symbol: str | None = None) -> None:
        self.cfg = load_config()  # reflect latest saved results/command changes
        dlg = DetailsDialog(self.cfg, symbol, None)
        dlg.exec()

    def show_config_location(self) -> None:
        path = str(config_path())
        self.tray.showMessage("Tech Watch config", path, QSystemTrayIcon.MessageIcon.Information, 10000)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Tech Watch")
    app.setOrganizationName("Tech Watch")

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "Tech Watch",
            "System tray is not available on this session. Start it in a Plasma/KDE tray-enabled session.",
        )
        return 1

    tray_app = TrayApp(app)

    # allow Ctrl+C in terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sig_timer = QTimer()
    sig_timer.start(500)
    sig_timer.timeout.connect(lambda: None)

    if "--check-now" in argv:
        QTimer.singleShot(1000, tray_app.run_daily_check)

    return app.exec()
