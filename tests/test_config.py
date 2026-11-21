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


def test_load_config_ui_geometry_and_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    loaded = config.load_config()
    assert loaded["log_levels"] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert loaded["terminal_split"] == [450, 150]
    assert loaded["terminal_visible"] is True
    assert loaded["details_size"] == [940, 660]
    assert loaded["details_pos"] is None
    assert loaded["details_maximized"] is False

    custom = {
        "log_levels": ["warning", "ERROR", "INVALID"],
        "terminal_split": [500, 250],
        "terminal_visible": False,
        "details_size": [1100, 750],
        "details_pos": [150, 250],
        "details_maximized": True,
    }
    config.config_path().write_text(json.dumps(custom), encoding="utf-8")
    loaded = config.load_config()
    assert loaded["log_levels"] == ["WARNING", "ERROR"]
    assert loaded["terminal_split"] == [500, 250]
    assert loaded["terminal_visible"] is False
    assert loaded["details_size"] == [1100, 750]
    assert loaded["details_pos"] == [150, 250]
    assert loaded["details_maximized"] is True

    bad = {
        "log_levels": "not-a-list",
        "terminal_split": [0, 0],
        "terminal_visible": "yes",
        "details_size": [50, 50],
        "details_pos": "not-a-list",
        "details_maximized": "true",
    }
    config.config_path().write_text(json.dumps(bad), encoding="utf-8")
    loaded = config.load_config()
    assert loaded["log_levels"] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert loaded["terminal_split"] == [450, 150]
    assert loaded["terminal_visible"] is True
    assert loaded["details_size"] == [940, 660]
    assert loaded["details_pos"] is None
    assert loaded["details_maximized"] is False


def test_load_config_normalizes_symbols_and_result_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    config.config_path().write_text(
        json.dumps(
            {
                "symbols": [" xic.to ", "XIC.TO", "", "shop.to"],
                "last_results": {"xic.to": {"verdict": "BUY"}, "": {"verdict": "BAD"}},
            }
        ),
        encoding="utf-8",
    )
    loaded = config.load_config()
    assert loaded["symbols"] == ["XIC.TO", "SHOP.TO"]
    assert loaded["last_results"] == {"XIC.TO": {"verdict": "BUY"}}


def test_load_config_does_not_overwrite_invalid_backup(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config.config_dir()
    path = config.config_path()
    path.write_text("{bad", encoding="utf-8")
    config.load_config()
    backup = path.with_suffix(".bak")
    assert backup.exists()
    original_backup = backup.read_text(encoding="utf-8")
    path.write_text("{bad again", encoding="utf-8")
    config.load_config()
    assert backup.read_text(encoding="utf-8") == original_backup
    backups = list(path.parent.glob("config.bak*"))
    assert len(backups) >= 2
