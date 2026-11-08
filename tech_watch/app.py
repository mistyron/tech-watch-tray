import html
import signal
import sys
import threading
from datetime import datetime
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import config_path, load_config, save_config
from .gemini import AnalysisResult, query_gemini
from .screener import get_universe, screen_symbols


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
    progress = pyqtSignal(str, str)  # symbol, status
    finished = pyqtSignal()


class ScreenerSignals(QObject):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(object)
    error = pyqtSignal(str)


class AnalysisWorker(threading.Thread):
    def __init__(self, symbols: list[str], cfg: dict[str, Any], signals: WorkerSignals):
        super().__init__(daemon=True)
        self.symbols = list(symbols)
        self.cfg = dict(cfg)
        self.signals = signals

    def run(self) -> None:
        try:
            for i, symbol in enumerate(self.symbols):
                self.signals.progress.emit(symbol, "updating")
                result = query_gemini(symbol, self.cfg)
                self.signals.result.emit(result)
                for pending in self.symbols[i + 1 :]:
                    self.signals.progress.emit(pending, "pending")
        finally:
            self.signals.finished.emit()


class ScreenerWorker(threading.Thread):
    def __init__(self, mode: str, min_score: int, limit: int, signals: ScreenerSignals):
        super().__init__(daemon=True)
        self.mode = mode
        self.min_score = min_score
        self.limit = limit
        self.signals = signals

    def run(self) -> None:
        try:
            universe = get_universe(self.mode, limit=self.limit)
            min_score = self.min_score
            hits = screen_symbols(
                universe,
                min_score=1,
                progress_cb=lambda d, t, s: self.signals.progress.emit(d, t, s),
            )
            filtered = [h for h in hits if h.score >= min_score]
            self.signals.done.emit({
                "hits": [h.to_dict() for h in filtered],
                "top": [h.to_dict() for h in hits[:15]],
                "scanned": len(universe),
                "mode": self.mode,
                "min_score": min_score,
            })
        except Exception as e:
            self.signals.error.emit(str(e))


class ManageSymbolsDialog(QDialog):
    def __init__(self, cfg: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Tech Watch Tray - Securities")
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
        self.check_status: dict[str, str] = {}
        self.setWindowTitle("Tech Watch Tray - Details")
        self.resize(940, 660)
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.overview_table = QTableWidget(0, 4)
        self.overview_table.setHorizontalHeaderLabels(["Symbol", "Verdict", "Checked", "Summary"])
        self.overview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.overview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.overview_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.overview_table.verticalHeader().setVisible(False)
        self.overview_table.setWordWrap(False)
        self.overview_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.overview_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self.overview_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.overview_table.setColumnWidth(3, 520)
        self.overview_table.cellDoubleClicked.connect(self._open_symbol_from_overview)
        self.tabs.addTab(self.overview_table, "Overview")

        symbol_page = QWidget()
        symbol_layout = QVBoxLayout(symbol_page)
        nav = QHBoxLayout()
        nav.addWidget(QLabel("Security:"))
        self.symbol_box = QComboBox()
        self.symbols = [str(s).strip().upper() for s in cfg.get("symbols", []) if str(s).strip()]
        for s in self.symbols:
            self.symbol_box.addItem(s)
        nav.addWidget(self.symbol_box, 1)
        symbol_layout.addLayout(nav)
        self.detail_tabs = QTabWidget()
        self.summary_box = QPlainTextEdit()
        self.summary_box.setReadOnly(True)
        self.response_box = QPlainTextEdit()
        self.response_box.setReadOnly(True)
        self.detail_tabs.addTab(self.summary_box, "Summary")
        self.detail_tabs.addTab(self.response_box, "AI response")
        symbol_layout.addWidget(self.detail_tabs)
        self.tabs.addTab(symbol_page, "Security details")
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.symbol_box.currentTextChanged.connect(self._load_symbol)
        self._refresh_overview()
        if current_symbol and current_symbol in self.symbols:
            self.symbol_box.setCurrentText(current_symbol)
            self.tabs.setCurrentIndex(1)
        elif self.symbols:
            self._load_symbol(self.symbols[0])

    def update_data(self, cfg: dict[str, Any], symbol: str | None = None) -> None:
        self.cfg = cfg
        current = symbol or self.symbol_box.currentText()
        self.symbols = [str(s).strip().upper() for s in cfg.get("symbols", []) if str(s).strip()]
        self.symbol_box.blockSignals(True)
        self.symbol_box.clear()
        for s in self.symbols:
            self.symbol_box.addItem(s)
        self.symbol_box.blockSignals(False)
        self._refresh_overview()
        if current in self.symbols:
            self.symbol_box.setCurrentText(current)
            self._load_symbol(current)
        elif self.symbols:
            self.symbol_box.setCurrentIndex(0)
            self._load_symbol(self.symbols[0])
        else:
            self.summary_box.setPlainText("No securities configured.")
            self.response_box.setPlainText("")
        if symbol:
            self.tabs.setCurrentIndex(1)

    def _fmt_time(self, ts: str | None) -> str:
        if not ts:
            return "never"
        try:
            dt = datetime.fromisoformat(ts)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ts

    def set_check_status(self, status: dict[str, str] | None) -> None:
        self.check_status = dict(status or {})
        self._refresh_overview()

    def _refresh_overview(self) -> None:
        results = self.cfg.get("last_results") if isinstance(self.cfg.get("last_results"), dict) else {}
        self.overview_table.setRowCount(0)
        for symbol in self.symbols:
            data = results.get(symbol) or {}
            verdict = str(data.get("verdict") or "—")
            checked = self._fmt_time(data.get("checked_at"))
            summary = str(data.get("summary") or "")
            status = self.check_status.get(symbol)
            if status == "updating":
                verdict = "UPDATING…"
                summary = "Check in progress…"
            elif status == "pending":
                verdict = "PENDING"
                summary = summary or "Queued for update…"
            row = self.overview_table.rowCount()
            self.overview_table.insertRow(row)
            values = [symbol, verdict, checked, summary]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 1 and verdict.upper() == "BUY":
                    item.setForeground(Qt.GlobalColor.darkGreen)
                elif col == 1 and verdict.upper() in {"ERROR", "SELL"}:
                    item.setForeground(Qt.GlobalColor.darkRed)
                elif col == 1 and verdict.upper() == "WAIT":
                    item.setForeground(Qt.GlobalColor.darkYellow)
                elif col == 1 and verdict.upper().startswith("UPDATING"):
                    item.setForeground(QColor(61, 174, 233))
                elif col == 1 and verdict.upper() == "PENDING":
                    item.setForeground(Qt.GlobalColor.gray)
                if status in {"updating", "pending"} and col in (0, 1):
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                if col == 3 and summary:
                    item.setToolTip(summary)
                self.overview_table.setItem(row, col, item)

    def _open_symbol_from_overview(self, row: int, _col: int) -> None:
        item = self.overview_table.item(row, 0)
        if not item:
            return
        symbol = item.text().strip().upper()
        if symbol:
            self.symbol_box.setCurrentText(symbol)
            self.tabs.setCurrentIndex(1)

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


class ScreenerDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Tech Watch Tray - TSX Screener")
        self.resize(980, 620)
        self._thread: ScreenerWorker | None = None
        self._hits: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Universe:"))
        self.mode = QComboBox()
        self.mode.addItem("TSX Composite (faster)", "tsx_composite")
        self.mode.addItem("TSX directory sample (broader, slower)", "tsx_directory")
        controls.addWidget(self.mode)
        controls.addWidget(QLabel("Min score:"))
        self.min_score = QSpinBox()
        self.min_score.setRange(1, 100)
        self.min_score.setValue(55)
        controls.addWidget(self.min_score)
        controls.addWidget(QLabel("Max symbols:"))
        self.limit = QSpinBox()
        self.limit.setRange(10, 2500)
        self.limit.setSingleStep(25)
        self.limit.setValue(221)
        controls.addWidget(self.limit)
        self.run_btn = QPushButton("Scan for screaming buys")
        controls.addWidget(self.run_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.status = QLabel("Scans TSX symbols using technicals (RSI/MACD/SMAs/volume). Not financial advice; verify before trading.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Symbol", "Score", "Verdict", "Price", "Chg %", "Div %", "Reasons"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        th = self.table.horizontalHeader()
        th.setStretchLastSection(False)
        for i, mode in enumerate([QHeaderView.ResizeMode.ResizeToContents] * 6 + [QHeaderView.ResizeMode.Interactive]):
            th.setSectionResizeMode(i, mode)
        self.table.setColumnWidth(6, 420)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.run_btn.clicked.connect(self.start_scan)

    def start_scan(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.run_btn.setEnabled(False)
        self._hits = []
        self.table.setRowCount(0)
        self.progress.setValue(0)
        self.status.setText("Loading TSX universe…")
        signals = ScreenerSignals()
        signals.progress.connect(self.on_progress)
        signals.done.connect(self.on_done)
        signals.error.connect(self.on_error)
        self._thread = ScreenerWorker(self.mode.currentData(), int(self.min_score.value()), int(self.limit.value()), signals)
        self._thread.start()

    def on_progress(self, done: int, total: int, symbol: str) -> None:
        pct = int((done / total) * 100) if total else 0
        self.progress.setValue(max(0, min(100, pct)))
        self.status.setText(f"Scanning {done}/{total}: {symbol}")

    def on_done(self, payload: object) -> None:
        self.run_btn.setEnabled(True)
        self.progress.setValue(100)
        if not isinstance(payload, dict):
            self.status.setText("Scan finished.")
            return
        self._hits = payload.get("hits") or []
        top = payload.get("top") or []
        scanned = payload.get("scanned") or 0
        min_score = payload.get("min_score") or self.min_score.value()
        show = self._hits if self._hits else top
        if self._hits:
            self.status.setText(f"Scanned {scanned} symbols. Found {len(self._hits)} match(es) with score >= {min_score}.")
        else:
            self.status.setText(f"Scanned {scanned} symbols. No symbols met score >= {min_score}; showing top {len(show)} by technical score (market may have few strong setups now).")
        for hit in show:
            row = self.table.rowCount()
            self.table.insertRow(row)
            reasons = "; ".join(hit.get("reasons") or [])
            div = hit.get("dividend_yield_pct")
            vals = [
                str(hit.get("symbol") or ""),
                str(hit.get("score") or ""),
                str(hit.get("verdict") or ""),
                "" if hit.get("price") is None else str(hit.get("price")),
                "" if hit.get("change_pct") is None else f"{hit.get('change_pct')}%",
                "" if div is None else f"{div}%",
                reasons,
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if col == 2 and val == "SCREAMING BUY":
                    item.setForeground(Qt.GlobalColor.darkGreen)
                if not self._hits and col in (0, 1, 2):
                    item.setForeground(Qt.GlobalColor.gray)
                if col == 6:
                    item.setToolTip(reasons)
                self.table.setItem(row, col, item)

    def on_error(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText(f"Scan failed: {msg}")
        QMessageBox.warning(self, "Tech Watch Tray - TSX Screener", msg)


class TrayApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.cfg = load_config()
        self.check_in_progress = False
        self._last_notified: dict[str, str] = {}
        self._active_thread: AnalysisWorker | None = None
        self._details_dialog: DetailsDialog | None = None
        self._screener_dialog: ScreenerDialog | None = None
        self._check_status: dict[str, str] = {}
        self._busy_angle = 0
        self._busy_base_icon: QIcon | None = None

        self.tray = QSystemTrayIcon(self._icon_for_state("idle"))
        self.tray.setToolTip("Tech Watch Tray: idle")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.run_daily_check)
        self._apply_timer()

        self.busy_timer = QTimer(self)
        self.busy_timer.setInterval(120)
        self.busy_timer.timeout.connect(self._animate_busy_icon)

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

    def _busy_pixmap(self, angle: int) -> QPixmap:
        base = self._busy_base_icon or self.tray.icon() or self._icon_for_state("idle")
        size = 22
        pm = base.pixmap(size, size)
        if pm.isNull():
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
        out = QPixmap(pm.size())
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.drawPixmap(0, 0, pm)
        width = 2
        pen = QPen(QColor(61, 174, 233, 235))  # Plasma-like blue accent
        pen.setWidth(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        inset = (width // 2) + 1
        rect = out.rect().adjusted(inset, inset, -inset, -inset)
        span = 110 * 16
        start = (-angle % 360) * 16
        p.drawArc(rect, start, span)
        p.end()
        return out

    def _animate_busy_icon(self) -> None:
        if not self.check_in_progress:
            return
        self._busy_angle = (self._busy_angle + 30) % 360
        self.tray.setIcon(QIcon(self._busy_pixmap(self._busy_angle)))

    def _start_busy_indicator(self) -> None:
        self._busy_angle = 0
        current = self.tray.icon()
        self._busy_base_icon = current if not current.isNull() else self._icon_for_state("idle")
        self._animate_busy_icon()
        if not self.busy_timer.isActive():
            self.busy_timer.start()

    def _stop_busy_indicator(self) -> None:
        if self.busy_timer.isActive():
            self.busy_timer.stop()

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

        scan_act = QAction("Scan TSX for screaming buys...", self.menu)
        scan_act.triggered.connect(self.show_screener)
        self.menu.addAction(scan_act)

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
            self.tray.setToolTip("Tech Watch Tray: checking AI…")
            return

        self._stop_busy_indicator()
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
        tip = "Tech Watch Tray\n" + ("\n".join(lines) if lines else "No securities configured")
        self.tray.setToolTip(tip[:1024])

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            dlg = self._details_dialog
            if dlg is not None and dlg.isVisible():
                dlg.hide()
                return
            self.show_details(None)

    def _schedule_next_check(self) -> None:
        hours = max(1, int(self.cfg.get("check_interval_hours") or 24))
        self.timer.start(hours * 60 * 60 * 1000)

    def run_daily_check_if_due(self) -> None:
        results = self._results()
        hours = max(1, int(self.cfg.get("check_interval_hours") or 24))
        now = datetime.now().astimezone()
        due = False
        oldest_age_hours = None
        for symbol in self.cfg.get("symbols", []):
            ts = (results.get(symbol) or {}).get("checked_at")
            if not ts:
                due = True
                oldest_age_hours = hours
                break
            try:
                last = datetime.fromisoformat(ts)
                age_hours = max(0.0, (now - last).total_seconds() / 3600)
            except Exception:
                due = True
                oldest_age_hours = hours
                break
            if age_hours >= hours:
                due = True
                oldest_age_hours = hours
                break
            remaining = hours - age_hours
            oldest_age_hours = remaining if oldest_age_hours is None else min(oldest_age_hours, remaining)
        if due:
            self.run_daily_check()
            return
        # Not due yet (e.g. app restarted): schedule next auto-check for the remaining interval.
        wait_hours = hours if oldest_age_hours is None else max(1 / 60, oldest_age_hours)
        self.timer.start(int(wait_hours * 60 * 60 * 1000))

    def run_daily_check(self) -> None:
        if self.check_in_progress:
            return
        symbols = [str(s).strip().upper() for s in self.cfg.get("symbols", []) if str(s).strip()]
        if not symbols:
            self.tray.showMessage(
                "Tech Watch Tray",
                "No securities configured. Use 'Manage securities...' to add symbols.",
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )
            return
        self.check_in_progress = True
        self._update_tray_state()
        self._start_busy_indicator()
        self.tray.showMessage(
            "Tech Watch Tray",
            f"Checking technicals for: {', '.join(symbols)}",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )
        signals = WorkerSignals()
        signals.progress.connect(self._on_progress)
        signals.result.connect(self._on_result)
        signals.finished.connect(self._on_finished)
        thread = AnalysisWorker(symbols, self.cfg, signals)
        self._active_thread = thread
        self._sync_details_status()
        thread.start()
        self._schedule_next_check()
        # Failsafe: don't leave UI stuck if a worker misbehaves.
        QTimer.singleShot(max(30, len(symbols) * (int((self.cfg.get("ai") or {}).get("timeout_seconds") or 180) + 30)) * 1000, self._check_watchdog)

    def _sync_details_status(self) -> None:
        dlg = self._details_dialog
        if dlg is not None and dlg.isVisible():
            dlg.set_check_status(self._check_status)

    def _on_progress(self, symbol: str, status: str) -> None:
        if symbol:
            self._check_status[symbol] = status
        self._sync_details_status()

    def _on_result(self, result_obj: object) -> None:
        if not isinstance(result_obj, AnalysisResult):
            return
        results = self._results()
        prev = results.get(result_obj.symbol) or {}
        prev_verdict = str(prev.get("verdict") or "")
        prev_checked = str(prev.get("checked_at") or "")
        results[result_obj.symbol] = result_obj.to_dict()
        self.cfg["last_results"] = results
        self._check_status.pop(result_obj.symbol, None)
        self._save()
        self._rebuild_menu()
        self._sync_details_status()

        notify_on = {str(v).upper() for v in (self.cfg.get("notify_on") or ["BUY"])}
        verdict = (result_obj.verdict or "").upper()
        notify_key = f"{result_obj.symbol}:{verdict}:{result_obj.checked_at[:10]}"
        changed_to_alert = verdict in notify_on and (prev_verdict.upper() != verdict or not prev_checked)
        first_alert = verdict in notify_on and self._last_notified.get(result_obj.symbol) != notify_key
        if result_obj.ok and changed_to_alert and first_alert:
            self._last_notified[result_obj.symbol] = notify_key
            self.tray.showMessage(
                f"Tech Watch Tray: {result_obj.symbol}",
                f"Verdict: {verdict}\n{result_obj.summary}",
                QSystemTrayIcon.MessageIcon.Warning if verdict == "BUY" else QSystemTrayIcon.MessageIcon.Information,
                15000,
            )
        elif not result_obj.ok:
            msg = result_obj.summary or result_obj.error or "Check failed"
            if result_obj.error and result_obj.error not in msg and "IneligibleTierError" not in msg:
                msg = f"{msg}\n{(result_obj.error[:180] + '…') if len(result_obj.error) > 180 else result_obj.error}"
            self.tray.showMessage(
                f"Tech Watch Tray: {result_obj.symbol}",
                f"Check failed: {msg}",
                QSystemTrayIcon.MessageIcon.Critical,
                15000,
            )

    def _on_finished(self) -> None:
        self.check_in_progress = False
        self._active_thread = None
        self._check_status = {}
        self._rebuild_menu()
        self._sync_details_status()

    def _check_watchdog(self) -> None:
        if not self.check_in_progress:
            return
        t = self._active_thread
        if t is not None and t.is_alive():
            return
        self.check_in_progress = False
        self._active_thread = None
        self._check_status = {}
        self._rebuild_menu()
        self._sync_details_status()
        self.tray.showMessage("Tech Watch Tray", "Check state was reset after a stalled check.", QSystemTrayIcon.MessageIcon.Warning, 8000)

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
            "Tech Watch Tray - AI command",
            "Command argv (use {prompt} placeholder). Example: gemini -p {prompt}\n",
            text=current_s,
        )
        if not ok:
            return
        try:
            parts = shlex.split(text.strip())
        except Exception as e:
            QMessageBox.warning(None, "Tech Watch Tray", f"Could not parse command: {e}")
            return
        if not parts:
            return
        if not any("{prompt}" in p for p in parts):
            parts.append("{prompt}")
        self.cfg.setdefault("ai", {})["command"] = parts
        self._save()
        self._rebuild_menu()
        self.tray.showMessage(
            "Tech Watch Tray",
            "AI command saved. Use 'Check all now' to test. If checks fail, the notification/details will show the command error.",
            QSystemTrayIcon.MessageIcon.Information,
            10000,
        )

    def _details_closed(self) -> None:
        self._details_dialog = None

    def show_details(self, symbol: str | None = None) -> None:
        self.cfg = load_config()  # reflect latest saved results/command changes
        dlg = self._details_dialog
        if dlg is not None and dlg.isVisible():
            dlg.update_data(self.cfg, symbol)
            dlg.set_check_status(self._check_status)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = DetailsDialog(self.cfg, symbol, None)
        dlg.set_check_status(self._check_status)
        self._details_dialog = dlg
        dlg.finished.connect(self._details_closed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def show_config_location(self) -> None:
        path = str(config_path())
        self.tray.showMessage("Tech Watch Tray config", path, QSystemTrayIcon.MessageIcon.Information, 10000)

    def show_screener(self) -> None:
        dlg = self._screener_dialog
        if dlg is not None and dlg.isVisible():
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = ScreenerDialog(None)
        self._screener_dialog = dlg
        dlg.finished.connect(lambda *_: setattr(self, "_screener_dialog", None))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Tech Watch Tray")
    app.setOrganizationName("Tech Watch Tray")

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None,
            "Tech Watch Tray",
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
