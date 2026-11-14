import os
import re
import shutil
from typing import Any

LOGIN_PROMPT = "Reply with exactly: OK"

_AGY_NAMES = {"agy", "agy.exe", "antigravity", "antigravity.exe"}
_AUTH_URL_RE = re.compile(r"https://accounts\.google\.com/[^\s\"'<>]+")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_URL_RE = re.compile(r"https?://\S+")
_AUTH_CODE_RE = re.compile(r"\b4/[0-9A-Za-z._-]{10,}\b")
_AUTH_ERROR_MARKERS = (
    "authentication required",
    "authentication failed or timed out",
    "not logged into antigravity",
    "sign-in required",
    "sign in required",
    "oauth token exchange failed",
    "missing authorization code",
    "stored credentials are invalid or expired",
)


def is_agy_argv(argv: list[str]) -> bool:
    if not argv:
        return False
    return os.path.basename(str(argv[0])).lower() in _AGY_NAMES


def headless_env(env: dict[str, str] | None = None) -> dict[str, str]:
    out = dict(os.environ if env is None else env)
    out.pop("DISPLAY", None)
    out.pop("WAYLAND_DISPLAY", None)
    out["AGY_CLI_NONINTERACTIVE_HEADLESS"] = "1"
    out["AGY_CLI_INTERACTIVE_HEADLESS"] = "1"
    return out


def is_auth_error(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _AUTH_ERROR_MARKERS)


def extract_auth_url(text: str) -> str | None:
    clean = _ANSI_RE.sub("", text or "")
    match = _AUTH_URL_RE.search(clean)
    if not match:
        return None
    return match.group(0).rstrip(".,);]}\"'")


def redact_sensitive(text: str, limit: int = 400) -> str:
    clean = _ANSI_RE.sub("", text or "")
    clean = _URL_RE.sub("<url>", clean)
    clean = _AUTH_CODE_RE.sub("<code>", clean)
    clean = " ".join(clean.split())
    if len(clean) > limit:
        clean = clean[:limit] + "…"
    return clean


def _resolve_agy(name: str) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    home = os.path.expanduser("~")
    candidates = (
        os.path.join(home, ".local", "bin", "agy"),
        "/usr/local/bin/agy",
        "/usr/local/sbin/agy",
        "/usr/bin/agy",
    )
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return name


def login_argv(cfg: dict[str, Any], prompt: str = LOGIN_PROMPT) -> list[str] | None:
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    cmd = ai.get("command")
    if not isinstance(cmd, list) or not cmd:
        return None
    parts = [str(part) for part in cmd]
    if not is_agy_argv(parts):
        return None
    has_prompt = any("{prompt}" in part for part in parts)
    argv = [part.replace("{symbol}", "").replace("{prompt}", prompt) for part in parts]
    argv[0] = _resolve_agy(argv[0])
    has_print_flag = any(flag in argv for flag in ("-p", "--print", "--prompt", "-i", "--prompt-interactive"))
    if not has_print_flag:
        argv[1:1] = ["-p", prompt]
    elif not has_prompt:
        argv.append(prompt)
    return argv
