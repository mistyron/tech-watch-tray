import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class AnalysisResult:
    symbol: str
    verdict: str
    summary: str
    full_text: str
    ok: bool
    error: str = ""
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "verdict": self.verdict,
            "summary": self.summary,
            "full_text": self.full_text,
            "ok": self.ok,
            "error": self.error,
            "checked_at": self.checked_at,
        }


VERDICT_RE = re.compile(r"(?im)^\s*VERDICT\s*:\s*(BUY|WAIT|HOLD|SELL)\b")
SUMMARY_RE = re.compile(r"(?im)^\s*SUMMARY\s*:\s*(.+?)\s*$")


def _resolve_command(cmd: str) -> str | None:
    if not cmd:
        return None
    p = shutil.which(cmd)
    if p:
        return p
    fallbacks = {
        "gemini": "/home/ron_plasma/.npm-global/bin/gemini",
        "agy": shutil.which("agy") or "/usr/local/bin/agy",
        "antigravity": shutil.which("antigravity") or "/usr/local/bin/antigravity",
    }
    fb = fallbacks.get(cmd)
    if fb and shutil.which(fb):
        return fb
    if fb and subprocess.os.path.exists(fb):
        return fb
    return None


def parse_analysis(symbol: str, text: str) -> tuple[str, str]:
    verdict = "UNKNOWN"
    summary = ""
    m = VERDICT_RE.search(text or "")
    if m:
        verdict = m.group(1).upper()
    else:
        low = (text or "").lower()
        if re.search(r"\b(buy|add capital|accumulate|strong buy)\b", low) and not re.search(
            r"\b(wait|hold off|not yet|avoid buying)\b", low
        ):
            verdict = "BUY"
        elif re.search(r"\b(wait and see|wait|hold off|not yet|neutral)\b", low):
            verdict = "WAIT"
        elif re.search(r"\bsell\b", low):
            verdict = "SELL"
        elif re.search(r"\bhold\b", low):
            verdict = "HOLD"
    ms = SUMMARY_RE.search(text or "")
    if ms:
        summary = ms.group(1).strip()
    if not summary:
        for line in (text or "").splitlines():
            s = line.strip()
            if s and not s.upper().startswith("VERDICT:"):
                summary = s[:200]
                break
    return verdict, summary


def _command_argv(symbol: str, prompt: str, cfg: dict[str, Any]) -> list[str] | None:
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    cmd = ai.get("command")
    if not isinstance(cmd, list) or not cmd:
        # legacy
        legacy_cmd = str(cfg.get("gemini_command") or "gemini")
        legacy_args = cfg.get("gemini_args")
        if not isinstance(legacy_args, list):
            legacy_args = ["-p"]
        cmd = [legacy_cmd, *legacy_args, "{prompt}"]
    argv: list[str] = []
    for part in cmd:
        s = str(part)
        argv.append(s.format(prompt=prompt, symbol=symbol))
    if not argv:
        return None
    resolved = _resolve_command(argv[0])
    if not resolved:
        return [argv[0]]
    argv[0] = resolved
    return argv


def _friendly_error(text: str) -> str:
    t = text or ""
    if "IneligibleTierError" in t or "no longer supported for Gemini Code Assist for individuals" in t:
        return (
            "Gemini CLI auth/tier is no longer supported for this account/client. "
            "Set `ai.command` in ~/.config/tech-watch/config.json to a working AI command "
            "(e.g. Antigravity/`agy` CLI if installed, or another Gemini-capable CLI/script "
            "that accepts the prompt)."
        )
    if "oauth" in t.lower() and "auth" in t.lower():
        return "AI command authentication failed. Verify the configured command works in a terminal."
    return (t.strip().splitlines() or ["AI command failed"])[0][:240]


def query_gemini(symbol: str, cfg: dict[str, Any], timeout: int | None = None) -> AnalysisResult:
    checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    template = str(cfg.get("query_template") or "")
    prompt = template.format(symbol=symbol)
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    if timeout is None:
        try:
            timeout = int(ai.get("timeout_seconds") or 180)
        except Exception:
            timeout = 180
    argv = _command_argv(symbol, prompt, cfg)
    if not argv:
        return AnalysisResult(symbol, "ERROR", "No AI command configured", "", False, "Missing ai.command", checked_at)
    if not _resolve_command(argv[0]) and not shutil.which(argv[0]):
        cmd_str = shlex.join(argv)
        return AnalysisResult(
            symbol=symbol,
            verdict="ERROR",
            summary="AI command not found",
            full_text=cmd_str,
            ok=False,
            error=f"Could not find `{argv[0]}`. Configure `ai.command` in ~/.config/tech-watch/config.json.",
            checked_at=checked_at,
        )
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**subprocess.os.environ},
        )
    except subprocess.TimeoutExpired:
        return AnalysisResult(symbol, "ERROR", "AI query timed out", shlex.join(argv), False, f"Timed out after {timeout}s", checked_at)
    except Exception as e:
        return AnalysisResult(symbol, "ERROR", "Failed to run AI command", shlex.join(argv), False, str(e), checked_at)

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    text = out if out else err
    if proc.returncode != 0 and not out:
        friendly = _friendly_error(err or text)
        return AnalysisResult(symbol, "ERROR", friendly[:240], text, False, friendly, checked_at)
    verdict, summary = parse_analysis(symbol, text)
    ok = proc.returncode == 0 or bool(out)
    if verdict == "UNKNOWN" and proc.returncode != 0:
        ok = False
    return AnalysisResult(symbol, verdict, summary or "(no summary)", text, ok, _friendly_error(err) if proc.returncode != 0 and err else "", checked_at)

