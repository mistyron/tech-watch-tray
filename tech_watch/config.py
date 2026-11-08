import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "tech-watch"
DEFAULT_CONFIG = {
    "symbols": ["XIC.TO"],
    "check_interval_hours": 24,
    "ai": {
        # Command argv list. Supported placeholders: {prompt}, {symbol}
        "command": ["gemini", "-p", "{prompt}"],
        "timeout_seconds": 180,
    },
    # Legacy/backward-compat fields (used if ai.command is absent/invalid)
    "gemini_command": "gemini",
    "gemini_args": ["-p"],
    "query_template": (
        "Analyze the current technical indicators for {symbol} (price trend, moving averages, "
        "RSI, MACD, volume, and key support/resistance if available). "
        "I already hold this security. Based on the technicals right now, is it promising to add "
        "capital to my existing holding, or should I wait and see?\n\n"
        "Be specific and practical. End your response with exactly these two lines:\n"
        "VERDICT: BUY|WAIT|HOLD|SELL\n"
        "SUMMARY: <one short sentence>"
    ),
    "notify_on": ["BUY"],
    "last_results": {},
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    path = Path(base) / APP_NAME if base else Path.home() / ".config" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    path = Path(base) / APP_NAME if base else Path.home() / ".local" / "share" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        save_config(DEFAULT_CONFIG.copy())
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update({k: v for k, v in data.items() if k != "ai" and v is not None})
    # deep-merge ai config
    ai = json.loads(json.dumps(DEFAULT_CONFIG.get("ai") or {}))
    user_ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    if user_ai:
        ai.update({k: v for k, v in user_ai.items() if v is not None})
    merged["ai"] = ai
    # Ensure types/keys for nested-ish fields
    if not isinstance(merged.get("symbols"), list):
        merged["symbols"] = DEFAULT_CONFIG["symbols"][:]
    if not isinstance(merged.get("last_results"), dict):
        merged["last_results"] = {}
    if not isinstance(merged.get("notify_on"), list):
        merged["notify_on"] = DEFAULT_CONFIG["notify_on"][:]
    if not isinstance(merged.get("ai", {}).get("command"), list):
        merged.setdefault("ai", {})["command"] = DEFAULT_CONFIG["ai"]["command"][:]
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
