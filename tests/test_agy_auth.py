from tech_watch import agy_auth


SAMPLE_OUTPUT = (
    "Authentication required. Please visit the URL to log in:\n"
    "  https://accounts.google.com/o/oauth2/auth?access_type=offline&state=abc\n"
    "\n"
    "Waiting for authentication (timeout 60s)...\n"
    "Or, paste the authorization code here and press Enter:\n"
)


def test_extract_auth_url_from_headless_output():
    assert agy_auth.extract_auth_url(SAMPLE_OUTPUT) == (
        "https://accounts.google.com/o/oauth2/auth?access_type=offline&state=abc"
    )


def test_extract_auth_url_ignores_ansi_and_trailing_punctuation():
    text = "\x1b[32mhttps://accounts.google.com/o/oauth2/auth?x=1\x1b[0m)."
    assert agy_auth.extract_auth_url(text) == "https://accounts.google.com/o/oauth2/auth?x=1"
    assert agy_auth.extract_auth_url("no link here") is None


def test_redact_sensitive_hides_urls_and_codes():
    text = (
        "Visit https://accounts.google.com/o/oauth2/auth?x=secret or paste "
        "4/0ABCdef123456789 there. \x1b[0m"
    )
    redacted = agy_auth.redact_sensitive(text)
    assert "accounts.google.com" not in redacted
    assert "4/0ABCdef" not in redacted
    assert "<url>" in redacted
    assert "<code>" in redacted
    assert "\x1b" not in redacted


def test_redact_sensitive_limits_length():
    assert len(agy_auth.redact_sensitive("x" * 1000, limit=50)) == 51


def test_is_auth_error_markers():
    assert agy_auth.is_auth_error("Error: authentication required. Run 'agy' to log in")
    assert agy_auth.is_auth_error("Authentication failed or timed out")
    assert agy_auth.is_auth_error("You are not logged into Antigravity.")
    assert agy_auth.is_auth_error("OAuth token exchange failed")
    assert not agy_auth.is_auth_error("429 quota exceeded")
    assert not agy_auth.is_auth_error("")


def test_headless_env_removes_display_and_sets_flags():
    env = agy_auth.headless_env(
        {
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "PATH": "/usr/bin",
        }
    )
    assert "DISPLAY" not in env
    assert "WAYLAND_DISPLAY" not in env
    assert env["AGY_CLI_NONINTERACTIVE_HEADLESS"] == "1"
    assert env["AGY_CLI_INTERACTIVE_HEADLESS"] == "1"
    assert env["PATH"] == "/usr/bin"


def test_login_argv_requires_agy_command(monkeypatch):
    monkeypatch.setattr(agy_auth, "_resolve_agy", lambda name: name)
    assert agy_auth.login_argv({"ai": {"command": ["gemini", "-p", "{prompt}"]}}) is None
    assert agy_auth.login_argv({"ai": {"command": []}}) is None
    assert agy_auth.login_argv({}) is None


def test_login_argv_replaces_prompt(monkeypatch):
    monkeypatch.setattr(agy_auth, "_resolve_agy", lambda name: f"/bin/{name}")
    cfg = {"ai": {"command": ["agy", "-p", "{prompt}"]}}
    assert agy_auth.login_argv(cfg) == ["/bin/agy", "-p", agy_auth.LOGIN_PROMPT]


def test_login_argv_adds_print_flag_when_missing(monkeypatch):
    monkeypatch.setattr(agy_auth, "_resolve_agy", lambda name: f"/bin/{name}")
    cfg = {"ai": {"command": ["agy", "--model", "m"]}}
    argv = agy_auth.login_argv(cfg)
    assert argv[0] == "/bin/agy"
    assert "-p" in argv
    assert agy_auth.LOGIN_PROMPT in argv
    assert argv[-1] == "m"


def test_login_argv_appends_prompt_when_flag_has_no_placeholder(monkeypatch):
    monkeypatch.setattr(agy_auth, "_resolve_agy", lambda name: f"/bin/{name}")
    cfg = {"ai": {"command": ["agy", "-p"]}}
    assert agy_auth.login_argv(cfg) == ["/bin/agy", "-p", agy_auth.LOGIN_PROMPT]


def test_resolve_agy_finds_home_fallback(monkeypatch, tmp_path):
    fake = tmp_path / ".local" / "bin" / "agy"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(agy_auth.shutil, "which", lambda name: None)
    monkeypatch.setattr(agy_auth.os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)
    assert agy_auth._resolve_agy("agy") == str(fake)
