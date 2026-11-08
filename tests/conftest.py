import os

# Headless Qt for tests that touch PyQt6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
