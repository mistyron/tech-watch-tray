import logging
import shlex
import signal
import sys
import threading
from datetime import datetime
from typing import Any, Callable

from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QIcon, QDesktopServices, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import config_path, load_config, save_config
from .gemini import AnalysisResult, query_gemini
from .logging_setup import log_path, setup_logging

log = logging.getLogger(__name__)


def _fmt_time(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def _fmt_elapsed(started_at: datetime | None, now: datetime | None = None) -> str:
    if started_at is None:
        return ""
    seconds = max(0, int(((now or datetime.now()) - started_at).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _watchdog_delay_ms(cfg: dict[str, Any], symbol_count: int) -> int:
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    try:
        timeout = int(ai.get("timeout_seconds") or 180)
        ollama_timeout = int(ai.get("ollama_timeout_seconds") or 180)
    except (TypeError, ValueError):
        timeout, ollama_timeout = 180, 180
    models = ai.get("agy_fallback_models") if isinstance(ai.get("agy_fallback_models"), list) else []
    per_symbol = max(60, timeout * (1 + len(models)) + ollama_timeout + 60)
    return max(30_000, max(1, symbol_count) * per_symbol * 1000)


def _tray_icon_pixmap(state: str, size: int = 64) -> QPixmap:
    states = {
        "idle": {"bg": QColor(52, 73, 94), "fg": QColor(236, 240, 241), "accent": QColor(149, 165, 166)},
        "checking": {"bg": QColor(41, 128, 185), "fg": QColor(236, 240, 241), "accent": QColor(174, 214, 241)},
        "alert": {"bg": QColor(39, 174, 96), "fg": QColor(255, 255, 255), "accent": QColor(213, 245, 227)},
        "error": {"bg": QColor(192, 57, 43), "fg": QColor(255, 255, 255), "accent": QColor(245, 183, 177)},
        "ok": {"bg": QColor(44, 62, 80), "fg": QColor(236, 240, 241), "accent": QColor(46, 204, 113)},
    }
    colors = states.get(state, states["idle"])
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    pad = max(1, size // 16)
    rect = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
    radius = size * 0.22
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(colors["bg"]))
    p.drawRoundedRect(rect, radius, radius)

    # Subtle monitor/chart frame
    chart = rect.adjusted(size * 0.16, size * 0.20, -size * 0.16, -size * 0.22)
    p.setPen(QPen(QColor(255, 255, 255, 45), max(1, size // 32)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(chart, size * 0.06, size * 0.06)

    # Trend line / candlestick-like bars to suggest "tech watch"
    x0 = chart.left() + chart.width() * 0.12
    x1 = chart.right() - chart.width() * 0.12
    base = chart.bottom() - chart.height() * 0.18
    top = chart.top() + chart.height() * 0.18
    points = [
        QPointF(x0, base - chart.height() * 0.18),
        QPointF(x0 + (x1 - x0) * 0.33, base - chart.height() * 0.38),
        QPointF(x0 + (x1 - x0) * 0.62, base - chart.height() * 0.28),
        QPointF(x1, top + chart.height() * 0.08),
    ]
    path = QPainterPath(points[0])
    for pt in points[1:]:
        path.lineTo(pt)
    p.setPen(QPen(colors["fg"], max(1.5, size * 0.055), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)

    # End marker colored by state accent
    marker_r = max(1.8, size * 0.055)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(colors["accent"]))
    p.drawEllipse(points[-1], marker_r, marker_r)

    if state == "alert":
        # Small upward tick
        p.setPen(QPen(QColor(255, 255, 255, 230), max(1.5, size * 0.05), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx = rect.right() - size * 0.18
        cy = rect.top() + size * 0.18
        d = size * 0.055
        p.drawLine(QPointF(cx - d, cy + d * 0.2), QPointF(cx - d * 0.1, cy + d))
        p.drawLine(QPointF(cx - d * 0.1, cy + d), QPointF(cx + d, cy - d * 0.6))
    elif state == "error":
        p.setPen(QPen(QColor(255, 255, 255, 230), max(1.5, size * 0.05), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx = rect.right() - size * 0.18
        cy = rect.top() + size * 0.18
        d = size * 0.055
        p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
        p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))

    p.end()
    return pm


def _tray_icon(state: str) -> QIcon:
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        icon.addPixmap(_tray_icon_pixmap(state, size))
    return icon


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    progress = pyqtSignal(str, str)  # symbol, detail
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
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        def progress(detail: str, sym: str = "") -> None:
            self.signals.progress.emit(sym or "", detail)

        try:
            for i, symbol in enumerate(self.symbols):
                if self._cancel.is_set():
                    break
                self.signals.progress.emit(symbol, "preparing check")
                try:
                    result = query_gemini(symbol, self.cfg, progress=progress)
                except Exception as e:
                    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
                    result = AnalysisResult(
                        symbol,
                        "ERROR",
                        f"Check failed: {e}",
                        "",
                        False,
                        str(e),
                        checked_at,
                    )
                if isinstance(result, AnalysisResult):
                    if result.ok:
                        log.info("Check completed for %s: %s", symbol, result.verdict)
                    else:
                        log.warning(
                            "Check failed for %s: %s",
                            symbol,
                            result.summary or result.error or result.verdict or "unknown error",
                        )
                self.signals.progress.emit(symbol, "formatting reply")
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
            from .screener import get_universe, screen_symbols

            universe = get_universe(self.mode, limit=self.limit)
            min_score = self.min_score
            errors: list[str] = []
            hits = screen_symbols(
                universe,
                min_score=1,
                progress_cb=lambda d, t, s: self.signals.progress.emit(d, t, s),
                errors=errors,
            )
            filtered = [h for h in hits if h.score >= min_score]
            self.signals.done.emit({
                "hits": [h.to_dict() for h in filtered],
                "top": [h.to_dict() for h in hits[:15]],
                "scanned": len(universe),
                "mode": self.mode,
                "min_score": min_score,
                "errors": errors[:20],
            })
        except ImportError as e:
            self.signals.error.emit(
                f"TSX screener dependencies unavailable ({e}). Install with: pip install .[screener]"
            )
        except Exception as e:
            log.exception("TSX screener failed")
            self.signals.error.emit(str(e))


class ManageSymbolsDialog(QDialog):
    def __init__(self, cfg: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowIcon(_tray_icon("idle"))
        self.setWindowTitle("Tech Watch Tray - Securities")
        self.cfg = cfg
        self.resize(420, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Symbols monitored daily (e.g., CGL.TO, HURA.TO):"))
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
            self.list_widget.takeItem(self.list_widget.row(item))

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
    def __init__(self, cfg: dict[str, Any], current_symbol: str | None = None, parent: QWidget | None = None,
                 on_check_symbol: Callable[[str], None] | None = None):
        super().__init__(parent)
        self.setWindowIcon(_tray_icon("idle"))
        self.cfg = cfg
        self._on_check_symbol = on_check_symbol
        self.check_status: dict[str, str] = {}
        self._status_since: dict[str, datetime] = {}
        self._check_icon = QIcon.fromTheme("view-refresh")
        if self._check_icon.isNull():
            self._check_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        self.setWindowTitle("Tech Watch Tray - Details")
        self.resize(940, 660)
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.overview_table = QTableWidget(0, 5)
        self.overview_table.setHorizontalHeaderLabels(["Check", "Symbol", "Verdict", "Checked", "Summary"])
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
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
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

        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setStyleSheet("QLabel { padding: 4px 6px; color: palette(mid); }")
        layout.addWidget(self.status_label)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._tick_status)
        self.finished.connect(lambda _: self._status_timer.stop())

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
        self._update_status_bar()
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

    def set_check_status(
        self,
        status: dict[str, str] | None,
        status_since: dict[str, datetime] | None = None,
    ) -> None:
        previous = self.check_status
        self.check_status = dict(status or {})
        now = datetime.now()
        if status_since is None:
            for sym, st in self.check_status.items():
                if previous.get(sym) != st or sym not in self._status_since:
                    self._status_since[sym] = now
        else:
            self._status_since = dict(status_since)
        for sym in list(self._status_since):
            if sym not in self.check_status:
                del self._status_since[sym]
        if any(st != "pending" for st in self.check_status.values()):
            self._status_timer.start()
        else:
            self._status_timer.stop()
        self._refresh_overview()
        self._update_status_bar()
        current = self.symbol_box.currentText().strip().upper()
        if current:
            self._load_symbol(current)

    def _elapsed_label(self, symbol: str) -> str:
        return _fmt_elapsed(self._status_since.get(symbol))

    def _updating_label(self, symbol: str) -> str:
        elapsed = self._elapsed_label(symbol)
        return f"UPDATING {elapsed}" if elapsed else "UPDATING…"

    def _tick_status(self) -> None:
        active = {sym: st for sym, st in self.check_status.items() if st != "pending"}
        if not active:
            self._status_timer.stop()
            self._update_status_bar()
            return
        for row, symbol in enumerate(self.symbols):
            if row >= self.overview_table.rowCount():
                break
            if symbol not in active:
                continue
            verdict_item = self.overview_table.item(row, 2)
            summary_item = self.overview_table.item(row, 4)
            if verdict_item is not None:
                verdict_item.setText(self._updating_label(symbol))
            if summary_item is not None:
                summary_item.setText(active[symbol])
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        status = self.check_status
        if not status:
            self.status_label.setText("Ready")
            return
        active = {sym: st for sym, st in status.items() if st != "pending"}
        if not active:
            self.status_label.setText("Checking — waiting for next security…")
            return
        parts = []
        for sym in self.symbols:
            st = active.get(sym)
            if st:
                elapsed = self._elapsed_label(sym)
                parts.append(f"{sym}: {st} ({elapsed})" if elapsed else f"{sym}: {st}")
        # include any active symbols not in the current symbol list
        for sym, st in active.items():
            if sym not in self.symbols:
                elapsed = self._elapsed_label(sym)
                parts.append(f"{sym}: {st} ({elapsed})" if elapsed else f"{sym}: {st}")
        self.status_label.setText("Checking — " + " | ".join(parts))

    def _refresh_overview(self) -> None:
        results = self.cfg.get("last_results") if isinstance(self.cfg.get("last_results"), dict) else {}
        self.overview_table.setRowCount(0)
        for symbol in self.symbols:
            data = results.get(symbol) or {}
            verdict = str(data.get("verdict") or "—")
            checked = self._fmt_time(data.get("checked_at"))
            summary = str(data.get("summary") or "")
            status = self.check_status.get(symbol)
            if status == "pending":
                verdict = "PENDING"
                summary = summary or "Queued for update…"
            elif status:
                verdict = self._updating_label(symbol)
                summary = str(status) or "Check in progress…"
            row = self.overview_table.rowCount()
            self.overview_table.insertRow(row)
            check_btn = QPushButton()
            check_btn.setFixedSize(26, 26)
            check_btn.setIcon(self._check_icon)
            check_btn.setIconSize(QSize(16, 16))
            check_btn.setFlat(True)
            check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            check_btn.setEnabled(status is None)
            check_btn.setToolTip(
                f"Check technicals for {symbol}"
                if status is None
                else f"Check already in progress for {symbol}"
            )
            check_btn.clicked.connect(lambda _checked=False, s=symbol: self._check_symbol(s))
            self.overview_table.setCellWidget(row, 0, check_btn)
            # Set symbol verdict and other data in columns 1-4
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
                if status and col in (1, 2):  # symbol and verdict columns
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                if status and status != "pending" and col == 1:
                    elapsed = self._elapsed_label(symbol)
                    item.setToolTip(
                        f"Status: {status}\nElapsed: {elapsed or '0s'}\n"
                        "Use the tray menu's 'Cancel check' if this is taking too long."
                    )
                if col == 3 and summary:
                    item.setToolTip(summary)
                # Column index shifted by 1 due to Check column
                self.overview_table.setItem(row, col + 1, item)

    def _check_symbol(self, symbol: str) -> None:
        if symbol in self.check_status:
            return
        if self._on_check_symbol is not None:
            self._on_check_symbol(symbol)

    def _open_symbol_from_overview(self, row: int, _col: int) -> None:
        item = self.overview_table.item(row, 1)
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


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowIcon(_tray_icon("idle"))
        self.setWindowTitle("Tech Watch Tray - Settings")
        self.resize(760, 720)
        self._cfg = cfg

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)

        general = QGroupBox("General")
        gform = QFormLayout(general)
        self.symbols_edit = QLineEdit(", ".join(str(s) for s in cfg.get("symbols", []) if str(s).strip()))
        self.symbols_edit.setPlaceholderText("e.g. CGL.TO, HURA.TO, XIC.TO")
        gform.addRow("Symbols (comma-separated):", self.symbols_edit)
        self.interval = QSpinBox()
        self.interval.setRange(1, 168)
        self.interval.setValue(max(1, int(cfg.get("check_interval_hours") or 24)))
        gform.addRow("Check interval (hours):", self.interval)
        self.startup_mode = QComboBox()
        self.startup_mode.addItem("Only when due (recommended)", "due")
        self.startup_mode.addItem("Always check on startup", "always")
        self.startup_mode.addItem("Never check on startup", "never")
        cur_start = str(cfg.get("check_on_startup") or "due").strip().lower()
        idx = self.startup_mode.findData(cur_start)
        if idx >= 0:
            self.startup_mode.setCurrentIndex(idx)
        gform.addRow("On startup:", self.startup_mode)
        self.notify_on = QLineEdit(", ".join(str(v).upper() for v in (cfg.get("notify_on") or ["BUY"])))
        self.notify_on.setPlaceholderText("BUY, WAIT, HOLD, SELL")
        gform.addRow("Notify on verdicts:", self.notify_on)
        layout.addWidget(general)

        ai_cfg = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
        ai = QGroupBox("AI command / fallbacks")
        aform = QFormLayout(ai)
        cmd = ai_cfg.get("command") or ["gemini", "-p", "{prompt}"]
        try:
            cmd_s = shlex.join([str(x) for x in cmd])
        except Exception:
            cmd_s = 'gemini -p "{prompt}"'
        self.ai_command = QLineEdit(cmd_s)
        self.ai_command.setPlaceholderText('agy -p {prompt}  (use {prompt}, optional {symbol})')
        aform.addRow("Primary command:", self.ai_command)
        self.ai_timeout = QSpinBox()
        self.ai_timeout.setRange(10, 3600)
        self.ai_timeout.setValue(max(10, int(ai_cfg.get("timeout_seconds") or 180)))
        aform.addRow("Primary timeout (s):", self.ai_timeout)
        agy_models = ai_cfg.get("agy_fallback_models") if isinstance(ai_cfg.get("agy_fallback_models"), list) else []
        self.agy_models = QLineEdit(", ".join(str(m).strip() for m in agy_models if str(m).strip()))
        self.agy_models.setPlaceholderText("gpt-oss-120b-medium, claude-sonnet-4-6, ...")
        aform.addRow("agy fallback models:", self.agy_models)
        self.fallback_quota = QCheckBox("Use fallbacks on quota/rate/auth/resource errors")
        self.fallback_quota.setChecked(bool(ai_cfg.get("fallback_on_quota", True)))
        aform.addRow("", self.fallback_quota)
        self.ollama_command = QLineEdit(str(ai_cfg.get("ollama_command") or "ollama"))
        aform.addRow("Ollama command:", self.ollama_command)
        self.ollama_model = QLineEdit(str(ai_cfg.get("ollama_model") or "llama3.2:1b"))
        aform.addRow("Ollama model:", self.ollama_model)
        self.ollama_timeout = QSpinBox()
        self.ollama_timeout.setRange(10, 3600)
        self.ollama_timeout.setValue(max(10, int(ai_cfg.get("ollama_timeout_seconds") or 180)))
        aform.addRow("Ollama timeout (s):", self.ollama_timeout)
        layout.addWidget(ai)

        prompt = QGroupBox("Prompt template")
        pbox = QVBoxLayout(prompt)
        pbox.addWidget(QLabel("Use {symbol} placeholder. Include VERDICT: BUY|WAIT|HOLD|SELL and SUMMARY: lines for parsing."))
        self.prompt = QPlainTextEdit()
        self.prompt.setPlainText(str(cfg.get("query_template") or ""))
        self.prompt.setMinimumHeight(180)
        pbox.addWidget(self.prompt)
        layout.addWidget(prompt)

        path = QLabel(f"Config file: {config_path()}")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path.setWordWrap(True)
        layout.addWidget(path)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self) -> None:
        try:
            parts = shlex.split(self.ai_command.text().strip())
        except Exception as e:
            QMessageBox.warning(self, "Settings", f"Could not parse primary command: {e}")
            return
        if not parts:
            QMessageBox.warning(self, "Settings", "Primary AI command cannot be empty.")
            return
        if not any("{prompt}" in p for p in parts):
            parts.append("{prompt}")
        symbols = []
        for s in self.symbols_edit.text().split(","):
            sym = s.strip().upper()
            if sym and sym not in symbols:
                symbols.append(sym)
        notify = []
        for v in self.notify_on.text().split(","):
            vv = v.strip().upper()
            if vv and vv not in notify:
                notify.append(vv)
        agy_models = []
        for m in self.agy_models.text().split(","):
            mm = m.strip()
            if mm and mm not in agy_models:
                agy_models.append(mm)
        template = self.prompt.toPlainText().strip()
        if "{symbol}" not in template:
            QMessageBox.warning(self, "Settings", "Prompt template must include {symbol}.")
            return
        self._cfg["symbols"] = symbols
        self._cfg["check_interval_hours"] = int(self.interval.value())
        self._cfg["check_on_startup"] = self.startup_mode.currentData() or "due"
        self._cfg["notify_on"] = notify or ["BUY"]
        self._cfg["query_template"] = template
        ai = self._cfg.setdefault("ai", {})
        ai["command"] = parts
        ai["timeout_seconds"] = int(self.ai_timeout.value())
        ai["agy_fallback_models"] = agy_models
        ai["fallback_on_quota"] = bool(self.fallback_quota.isChecked())
        ai["ollama_command"] = self.ollama_command.text().strip() or "ollama"
        ai["ollama_model"] = self.ollama_model.text().strip() or "llama3.2:1b"
        ai["ollama_timeout_seconds"] = int(self.ollama_timeout.value())
        self.accept()

    def result_config(self) -> dict[str, Any]:
        return self._cfg


class ScreenerDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowIcon(_tray_icon("idle"))
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
        self.limit.setRange(0, 5000)
        self.limit.setSingleStep(25)
        self.limit.setSpecialValueText("No limit")
        self.limit.setValue(221)
        self.limit.setToolTip("0 = no limit (TSX directory can be very large/slow)")
        controls.addWidget(self.limit)
        self.run_btn = QPushButton("Scan")
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

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Symbol", "Score", "Verdict", "Risk", "Price", "Chg %", "Div %", "Reasons"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        th = self.table.horizontalHeader()
        th.setStretchLastSection(False)
        for i in range(8):
            th.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
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
        errors = payload.get("errors") or []
        show = self._hits if self._hits else top
        if self._hits:
            text = f"Scanned {scanned} symbols. Found {len(self._hits)} match(es) with score >= {min_score}."
        else:
            text = f"Scanned {scanned} symbols. No symbols met score >= {min_score}; showing top {len(show)} by technical score (market may have few strong setups now)."
        if errors:
            text += f" {len(errors)} data error(s); hover for details."
            self.status.setToolTip("\n".join(str(e) for e in errors))
        else:
            self.status.setToolTip("")
        self.status.setText(text)
        for hit in show:
            row = self.table.rowCount()
            self.table.insertRow(row)
            reasons = "; ".join(hit.get("reasons") or [])
            div = hit.get("dividend_yield_pct")
            risk = hit.get("risk")
            risk_label = hit.get("risk_label") or ""
            risk_text = "" if risk is None else f"{risk_label} ({risk})"
            vals = [
                str(hit.get("symbol") or ""),
                str(hit.get("score") or ""),
                str(hit.get("verdict") or ""),
                risk_text,
                "" if hit.get("price") is None else str(hit.get("price")),
                "" if hit.get("change_pct") is None else f"{hit.get('change_pct')}%",
                "" if div is None else f"{div}%",
                reasons,
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if col == 2 and val == "SCREAMING BUY":
                    item.setForeground(Qt.GlobalColor.darkGreen)
                if col == 3 and risk is not None:
                    rr = (hit.get("metrics") or {}).get("risk_reasons") or []
                    tip = f"Risk {risk_label} ({risk}/100, technical heuristic)\n" + ("\n".join(f"- {r}" for r in rr) if rr else "- no major technical risk flags detected")
                    item.setToolTip(tip)
                    if risk >= 75:
                        item.setForeground(Qt.GlobalColor.darkRed)
                    elif risk >= 55:
                        item.setForeground(QColor(180, 120, 0))
                    elif risk < 35:
                        item.setForeground(Qt.GlobalColor.darkGreen)
                if not self._hits and col in (0, 1, 2):
                    item.setForeground(Qt.GlobalColor.gray)
                if col == 7:
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
        self._settings_dialog: SettingsDialog | None = None
        self._check_status: dict[str, str] = {}
        self._status_since: dict[str, datetime] = {}
        self._check_started_at: datetime | None = None
        self._check_generation = 0
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
        return _tray_icon(state)

    def _busy_pixmap(self, angle: int) -> QPixmap:
        base = self._busy_base_icon or self.tray.icon() or self._icon_for_state("checking")
        size = 64
        pm = base.pixmap(size, size)
        if pm.isNull():
            pm = _tray_icon_pixmap("checking", size)
        out = QPixmap(pm.size())
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.drawPixmap(0, 0, pm)
        width = max(2, size // 14)
        pen = QPen(QColor(236, 240, 241, 235))
        pen.setWidth(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        inset = width + max(1, size // 20)
        rect = out.rect().adjusted(inset, inset, -inset, -inset)
        start = (-angle % 360) * 16
        p.drawArc(rect, start, 110 * 16)
        p.end()
        return out

    def _animate_busy_icon(self) -> None:
        if not self.check_in_progress:
            return
        self._busy_angle = (self._busy_angle + 30) % 360
        self.tray.setIcon(QIcon(self._busy_pixmap(self._busy_angle)))
        self.tray.setToolTip(self._checking_tooltip())

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

        if self.check_in_progress:
            cancel_act = QAction("Cancel check", self.menu)
            cancel_act.triggered.connect(self.cancel_check)
            self.menu.addAction(cancel_act)

        manage_act = QAction("Manage securities...", self.menu)
        manage_act.triggered.connect(self.manage_symbols)
        manage_act.setEnabled(not self.check_in_progress)
        self.menu.addAction(manage_act)

        settings_act = QAction("Settings...", self.menu)
        settings_act.triggered.connect(self.show_settings)
        self.menu.addAction(settings_act)

        scan_act = QAction("TSX Screener...", self.menu)
        scan_act.triggered.connect(self.show_screener)
        self.menu.addAction(scan_act)

        config_act = QAction("Open config file location", self.menu)
        config_act.triggered.connect(self.show_config_location)
        self.menu.addAction(config_act)

        self.menu.addSeparator()

        quit_act = QAction("Quit", self.menu)
        quit_act.triggered.connect(self.app.quit)
        self.menu.addAction(quit_act)

        self._update_tray_state()

    def check_single_symbol(self, symbol: str) -> None:
        if self.check_in_progress:
            log.info("Skipping single symbol check for %s: a check is already in progress", symbol)
            self.tray.showMessage(
                "Tech Watch Tray",
                "A check is already in progress. Please wait for it to finish.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
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
        if symbol not in symbols:
            self.tray.showMessage(
                "Tech Watch Tray",
                f"Symbol {symbol} is not in the configured securities list.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
            return
        log.info("Starting single symbol check for %s", symbol)
        self.check_in_progress = True
        self._check_started_at = datetime.now()
        self._check_generation += 1
        generation = self._check_generation
        self._check_status = {symbol: "pending"}
        self._status_since = {symbol: self._check_started_at}
        self._update_tray_state()
        self._rebuild_menu()
        self._start_busy_indicator()
        log.info("Starting check for %s", symbol)
        self.tray.showMessage(
            "Tech Watch Tray",
            f"Checking technicals for: {symbol}",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )
        signals = WorkerSignals()
        signals.progress.connect(lambda s, st, g=generation: self._on_progress(s, st, g))
        signals.result.connect(lambda r, g=generation: self._on_result(r, g))
        signals.finished.connect(lambda g=generation: self._on_finished(g))
        thread = AnalysisWorker([symbol], self.cfg, signals)
        self._active_thread = thread
        self._sync_details_status()
        self._refresh_details_data()
        thread.start()
        QTimer.singleShot(
            _watchdog_delay_ms(self.cfg, 1),
            lambda g=generation: self._check_watchdog(g),
        )

    def _checking_tooltip(self) -> str:
        elapsed = _fmt_elapsed(self._check_started_at)
        suffix = f" ({elapsed})" if elapsed else ""
        return f"Tech Watch Tray: checking AI…{suffix}"

    def _update_tray_state(self) -> None:
        if self.check_in_progress:
            self.tray.setToolTip(self._checking_tooltip())
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
            if not self.cfg.get("symbols"):
                self.manage_symbols()
                return
            self.show_details(None)

    def _schedule_next_check(self) -> None:
        hours = max(1, int(self.cfg.get("check_interval_hours") or 24))
        self.timer.start(hours * 60 * 60 * 1000)

    def run_daily_check_if_due(self) -> None:
        mode = str(self.cfg.get("check_on_startup") or "due").strip().lower()
        if mode == "never":
            self._schedule_next_check()
            return
        if mode == "always":
            self.run_daily_check()
            return
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
        self._check_started_at = datetime.now()
        self._check_generation += 1
        generation = self._check_generation
        self._check_status = {symbol: "pending" for symbol in symbols}
        self._status_since = {symbol: self._check_started_at for symbol in symbols}
        self._update_tray_state()
        self._rebuild_menu()
        self._start_busy_indicator()
        self.tray.showMessage(
            "Tech Watch Tray",
            f"Checking technicals for: {', '.join(symbols)}",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )
        signals = WorkerSignals()
        signals.progress.connect(lambda s, st, g=generation: self._on_progress(s, st, g))
        signals.result.connect(lambda r, g=generation: self._on_result(r, g))
        signals.finished.connect(lambda g=generation: self._on_finished(g))
        thread = AnalysisWorker(symbols, self.cfg, signals)
        self._active_thread = thread
        self._sync_details_status()
        self._refresh_details_data()
        thread.start()
        self._schedule_next_check()
        # Failsafe: don't leave UI stuck if a worker misbehaves (includes agy model retries + Ollama fallback).
        QTimer.singleShot(
            _watchdog_delay_ms(self.cfg, len(symbols)),
            lambda g=generation: self._check_watchdog(g),
        )

    def _sync_details_status(self) -> None:
        dlg = self._details_dialog
        if dlg is not None and dlg.isVisible():
            dlg.set_check_status(self._check_status, self._status_since)

    def _refresh_details_data(self) -> None:
        dlg = self._details_dialog
        if dlg is not None and dlg.isVisible():
            dlg.update_data(self.cfg)

    def _on_progress(self, symbol: str, status: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._check_generation:
            return
        if symbol:
            previous = self._check_status.get(symbol)
            if previous == "pending" and status != "pending":
                self._status_since[symbol] = datetime.now()
            elif symbol not in self._status_since:
                self._status_since[symbol] = datetime.now()
            self._check_status[symbol] = status
        self._sync_details_status()

    def _on_result(self, result_obj: object, generation: int | None = None) -> None:
        if generation is not None and generation != self._check_generation:
            log.info(
                "Ignoring stale result for %s from check generation %s (current %s)",
                getattr(result_obj, "symbol", "?"),
                generation,
                self._check_generation,
            )
            return
        if not isinstance(result_obj, AnalysisResult):
            return
        results = self._results()
        prev = results.get(result_obj.symbol) or {}
        prev_verdict = str(prev.get("verdict") or "")
        prev_checked = str(prev.get("checked_at") or "")
        results[result_obj.symbol] = result_obj.to_dict()
        self.cfg["last_results"] = results
        self._check_status.pop(result_obj.symbol, None)
        self._status_since.pop(result_obj.symbol, None)
        self._save()
        self._rebuild_menu()
        self._refresh_details_data()
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

    def _on_finished(self, generation: int | None = None) -> None:
        if generation is not None and generation != self._check_generation:
            return
        self.check_in_progress = False
        self._active_thread = None
        self._check_status = {}
        self._status_since = {}
        self._check_started_at = None
        self._rebuild_menu()
        self._refresh_details_data()
        self._sync_details_status()

    def _check_watchdog(self, generation: int | None = None) -> None:
        if generation is not None and generation != self._check_generation:
            return
        if not self.check_in_progress:
            return
        t = self._active_thread
        alive = t is not None and t.is_alive()
        if alive and hasattr(t, "cancel"):
            t.cancel()
            log.warning("Cancelling stalled check worker after watchdog timeout")
        self.check_in_progress = False
        self._active_thread = None
        self._check_status = {}
        self._status_since = {}
        self._check_started_at = None
        self._rebuild_menu()
        self._refresh_details_data()
        self._sync_details_status()
        msg = (
            "Check state was reset after a stalled check (worker is still running; cancellation requested)."
            if alive
            else "Check state was reset after a stalled check."
        )
        self.tray.showMessage("Tech Watch Tray", msg, QSystemTrayIcon.MessageIcon.Warning, 8000)

    def cancel_check(self) -> None:
        t = self._active_thread
        if t is not None and hasattr(t, "cancel"):
            t.cancel()
            self.tray.showMessage(
                "Tech Watch Tray",
                "Cancelling after the current symbol finishes.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def manage_symbols(self) -> None:
        dlg = ManageSymbolsDialog(self.cfg, None)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cfg = dlg.apply()
            self._save()
            self._apply_timer()
            self._rebuild_menu()
            self._refresh_details_data()
            self._sync_details_status()

    def _details_closed(self) -> None:
        self._details_dialog = None

    def show_details(self, symbol: str | None = None) -> None:
        self.cfg = load_config()  # reflect latest saved results/command changes
        dlg = self._details_dialog
        if dlg is not None and dlg.isVisible():
            dlg.update_data(self.cfg, symbol)
            dlg.set_check_status(self._check_status, self._status_since)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = DetailsDialog(self.cfg, symbol, None, self.check_single_symbol)
        dlg.set_check_status(self._check_status, self._status_since)
        self._details_dialog = dlg
        dlg.finished.connect(self._details_closed)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def show_config_location(self) -> None:
        path = str(config_path())
        self.tray.showMessage("Tech Watch Tray config", path, QSystemTrayIcon.MessageIcon.Information, 10000)

    def show_settings(self) -> None:
        dlg = self._settings_dialog
        if dlg is not None and dlg.isVisible():
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        self.cfg = load_config()
        dlg = SettingsDialog(self.cfg, None)
        self._settings_dialog = dlg

        def _finished(code: int) -> None:
            self._settings_dialog = None
            if code == QDialog.DialogCode.Accepted:
                self.cfg = dlg.result_config()
                self._save()
                self._apply_timer()
                self._rebuild_menu()
                self.tray.showMessage(
                    "Tech Watch Tray",
                    "Settings saved. Restart is not required; new checks will use them.",
                    QSystemTrayIcon.MessageIcon.Information,
                    7000,
                )

        dlg.finished.connect(_finished)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

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

    def show_log_location(self) -> None:
        path = log_path()
        log.info("Opening log file %s", path)
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            return
        self.tray.showMessage(
            "Tech Watch Tray log",
            f"Log file will appear at {path}",
            QSystemTrayIcon.MessageIcon.Information,
            10000,
        )


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argv = argv if argv is not None else sys.argv
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Tech Watch Tray")
    app.setOrganizationName("Tech Watch Tray")
    app.setWindowIcon(_tray_icon("idle"))

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
