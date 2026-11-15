#!/usr/bin/env python3
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)


class Splash(QWidget):
    def __init__(self, icon_path: str, status_path: str) -> None:
        super().__init__()
        self.status_path = Path(status_path)
        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(420, 220)

        panel = QFrame(self)
        panel.setObjectName("panel")
        panel.setGeometry(self.rect())
        panel.setStyleSheet(
            "#panel { background: palette(window);"
            " border: 1px solid palette(mid); border-radius: 12px; }"
        )

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        icon = QLabel()
        icon.setPixmap(QIcon(icon_path).pixmap(96, 96))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)

        self.status = QLabel("Starting Tech Watch...")
        font = self.status.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self.status.setFont(font)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(
            "QProgressBar { border: 0; background: palette(alternate-base); border-radius: 3px; }"
            "QProgressBar::chunk { background: palette(highlight); border-radius: 3px; }"
        )
        layout.addWidget(bar)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(100)

        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.move(geometry.center() - self.rect().center())

    def refresh(self) -> None:
        try:
            text = self.status_path.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if text and text != self.status.text():
            self.status.setText(text)


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Tech Watch Tray")
    app.setDesktopFileName("io.github.mistyron.TechWatchTray")
    splash = Splash(sys.argv[1], sys.argv[2])
    splash.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
