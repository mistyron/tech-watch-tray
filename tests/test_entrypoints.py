import importlib
import runpy
from pathlib import Path


def test_package_main_exports():
    mod = importlib.import_module("tech_watch.__main__")
    assert hasattr(mod, "main")
    # guarded; importing should not exit
    assert mod.main


def test_script_wrapper_invokes_main(monkeypatch):
    called = {}

    def fake_main():
        called["ok"] = True
        return 7

    import tech_watch.app as app

    monkeypatch.setattr(app, "main", fake_main)
    script = Path(__file__).resolve().parents[1] / "tech-watch.py"
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as e:
        assert e.code == 7
    assert called.get("ok")

