import json

import tech_watch.config as config


def test_config_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = config.config_dir()
    assert p == tmp_path / "tech-watch"
    assert p.is_dir()


def test_config_dir_default_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: tmp_path))
    p = config.config_dir()
    assert p == tmp_path / ".config" / "tech-watch"
    assert p.is_dir()


def test_data_dir_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    p = config.data_dir()
    assert p == tmp_path / "tech-watch"
    assert p.is_dir()


def test_data_dir_default_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: tmp_path))
    p = config.data_dir()
    assert p == tmp_path / ".local" / "share" / "tech-watch"
    assert p.is_dir()


def test_config_path(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_path() == tmp_path / "tech-watch" / "config.json"


def test_save_and_load_config_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = config.json.loads(config.json.dumps(config.DEFAULT_CONFIG))
    cfg["symbols"] = ["AAPL"]
    cfg["ai"]["timeout_seconds"] = 42
    config.save_config(cfg)
    loaded = config.load_config()
    assert loaded["symbols"] == ["AAPL"]
    assert loaded["ai"]["timeout_seconds"] == 42


def test_load_config_creates_default_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    loaded = config.load_config()
    assert loaded["symbols"] == config.DEFAULT_CONFIG["symbols"]
    assert config.config_path().exists()


def test_load_config_handles_corrupt_json(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    config.config_path().write_text("{not json", encoding="utf-8")
    loaded = config.load_config()
    assert loaded["symbols"] == config.DEFAULT_CONFIG["symbols"]
    backup = config.config_path().with_suffix(".bak")
    assert backup.read_text(encoding="utf-8") == "{not json"
    assert (backup.stat().st_mode & 0o777) == 0o600


def test_load_config_coerces_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    cases = [("24h", 24), (0, 1), (999, 168), (6, 6), (float("inf"), 24)]
    for value, expected in cases:
        config.config_path().write_text(json.dumps({"check_interval_hours": value}), encoding="utf-8")
        assert config.load_config()["check_interval_hours"] == expected


def test_load_config_coerces_ai_timeouts(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    config.config_path().write_text(
        json.dumps({"ai": {"timeout_seconds": "2m", "ollama_timeout_seconds": 99999}}),
        encoding="utf-8",
    )
    loaded = config.load_config()
    assert loaded["ai"]["timeout_seconds"] == 180
    assert loaded["ai"]["ollama_timeout_seconds"] == 3600


def test_load_config_drops_non_object_results(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    config.config_path().write_text(
        json.dumps({"last_results": {"AAPL": "BUY", "MSFT": {"verdict": "WAIT"}}}),
        encoding="utf-8",
    )
    assert config.load_config()["last_results"] == {"MSFT": {"verdict": "WAIT"}}


def test_load_config_handles_non_object_json(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    config.config_path().write_text("[]", encoding="utf-8")
    loaded = config.load_config()
    assert loaded["symbols"] == config.DEFAULT_CONFIG["symbols"]
    assert loaded["ai"]["command"] == config.DEFAULT_CONFIG["ai"]["command"]


def test_load_config_merges_and_sanitizes(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    bad = {
        "symbols": "nope",
        "last_results": [],
        "notify_on": None,
        "check_on_startup": "WEIRD",
        "ai": {"command": "not-a-list", "agy_fallback_models": "nope", "timeout_seconds": 7},
        "gemini_command": "custom",
    }
    config.config_path().write_text(json.dumps(bad), encoding="utf-8")
    loaded = config.load_config()
    assert loaded["symbols"] == config.DEFAULT_CONFIG["symbols"]
    assert loaded["last_results"] == {}
    assert loaded["notify_on"] == config.DEFAULT_CONFIG["notify_on"]
    assert loaded["check_on_startup"] == "due"
    assert loaded["ai"]["command"] == ["custom", "-p", "{prompt}"]
    assert loaded["ai"]["agy_fallback_models"] == config.DEFAULT_CONFIG["ai"]["agy_fallback_models"]
    assert loaded["ai"]["timeout_seconds"] == 10
    assert "gemini_command" not in loaded
    assert "gemini_args" not in loaded


def test_load_config_migrates_legacy_command(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    config.config_path().write_text(
        json.dumps({"gemini_command": "custom", "gemini_args": ["--prompt"]}),
        encoding="utf-8",
    )
    loaded = config.load_config()
    assert loaded["ai"]["command"] == ["custom", "--prompt", "{prompt}"]


def test_save_config_uses_lock_file(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = config.json.loads(config.json.dumps(config.DEFAULT_CONFIG))
    config.save_config(cfg)
    assert config.config_path().with_suffix(".lock").exists()
    assert config.load_config()["symbols"] == cfg["symbols"]


def test_load_config_preserves_valid_startup_modes(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for mode in ("due", "always", "never"):
        config.config_dir()
        config.config_path().write_text(json.dumps({"check_on_startup": mode}), encoding="utf-8")
        assert config.load_config()["check_on_startup"] == mode


def test_config_files_and_dirs_have_restricted_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = config.json.loads(config.json.dumps(config.DEFAULT_CONFIG))
    config.save_config(cfg)
    cfg_file = config.config_path()
    cfg_dir = config.config_dir()
    lock_file = cfg_file.with_suffix(".lock")
    assert (cfg_file.stat().st_mode & 0o777) == 0o600
    assert (cfg_dir.stat().st_mode & 0o777) == 0o700
    assert (lock_file.stat().st_mode & 0o777) == 0o600
