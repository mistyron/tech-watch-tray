import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_start_script_shell_syntax():
    for script in (
        ROOT / "start.sh",
        ROOT / "docker" / "entrypoint.sh",
        ROOT / "scripts" / "ollama-wrapper" / "ollama",
    ):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_start_script_help_lists_ollama_modes():
    result = subprocess.run(
        [str(ROOT / "start.sh"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    for option in (
        "--container-app",
        "--host-app",
        "--container-ollama",
        "--host-ollama",
        "--no-ollama",
        "--smoke-test",
        "--down",
    ):
        assert option in result.stdout


def test_start_script_installs_matching_desktop_entry():
    from tech_watch import app as app_mod

    script = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert app_mod.APP_ID in script
    assert "$APP_ID.desktop" in script
    assert "StartupWMClass=$APP_ID" in script
