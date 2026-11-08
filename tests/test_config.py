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
    assert loaded["ai"]["command"] == config.DEFAULT_CONFIG["ai"]["command"]
    assert loaded["ai"]["agy_fallback_models"] == config.DEFAULT_CONFIG["ai"]["agy_fallback_models"]
    assert loaded["ai"]["timeout_seconds"] == 7
    assert loaded["gemini_command"] == "custom"


def test_load_config_preserves_valid_startup_modes(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for mode in ("due", "always", "never"):
        config.config_dir()
        config.config_path().write_text(json.dumps({"check_on_startup": mode}), encoding="utf-8")
        assert config.load_config()["check_on_startup"] == mode
