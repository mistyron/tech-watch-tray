import logging
import math
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import DEFAULT_CONFIG

log = logging.getLogger(__name__)


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


_MD_LEAD = r"[- \t*_`#>]*"
VERDICT_RE = re.compile(rf"(?im)^{_MD_LEAD}VERDICT[ \t*_`]*\s*:[ \t*_`]*\s*(BUY|WAIT|HOLD|SELL)\b")
SUMMARY_RE = re.compile(rf"(?im)^{_MD_LEAD}SUMMARY[ \t*_`]*\s*:[ \t*_`]*\s*(.+?)[ \t*_`]*\s*$")

_REFUSAL_MARKERS = (
    "don't have access to live market data",
    "do not have access to live market data",
    "can't retrieve current",
    "cannot retrieve current",
    "unable to retrieve current",
    "don't have real-time",
    "do not have real-time",
    "can't access real-time",
    "cannot access real-time",
    "as an ai language model",
    "i'm unable to provide real-time",
    "i am unable to provide real-time",
    "i'm sorry, but i can't help",
    "i’m sorry, but i can’t help",
    "i'm sorry, but i cannot help",
    "i cannot assist with",
    "i can't assist with",
    "i’m unable to assist",
    "not able to provide financial advice",
)

_WAIT_CUES = (
    "wait for breakout",
    "wait for a breakout",
    "wait for confirmation",
    "wait for a confirmation",
    "wait for pullback",
    "wait for a pullback",
    "wait for a better",
    "wait for better",
    "wait for a trigger",
    "wait for a signal",
    "wait for the signal",
    "clear breakout signal is still needed",
    "breakout signal is still needed",
    "no clear breakout",
    "not yet justified",
    "before adding more capital wait",
    "hold off until",
    "hold off on adding",
    "would wait",
    "i would wait",
    "confirmation is still needed",
    "not a buy",
    "not buy",
    "avoid adding",
    "don't add",
    "do not add",
)
_CONDITIONAL_CUES = (
    "add if ",
    "add only if",
    "add to the position only if",
    "buy if ",
    "buy only if",
    "only add if",
    "only buy if",
    "consider adding if",
    "add capital if",
)
_CONDITION_RE = re.compile(
    r"\b(still needed|not yet|wait|before adding|confirmation|breakout above|breaks above|closes above|reclaim|trigger)\b"
)
_CAUTION_RE = re.compile(r"\b(wait|hold off|not yet|avoid buying|only if|if it breaks|if it closes|if price breaks)\b")


def _strip_md(s: str) -> str:
    return (s or "").strip().strip("*_`#> \t")


def _has_caution(text: str) -> bool:
    low = (text or "").lower()
    if any(cue in low for cue in _WAIT_CUES):
        return True
    return any(cue in low for cue in _CONDITIONAL_CUES) and bool(_CONDITION_RE.search(low))


def _resolve_command(cmd: str) -> str | None:
    if not cmd:
        return None
    p = shutil.which(cmd)
    if p:
        return p
    home = str(subprocess.os.path.expanduser("~"))
    fallbacks = {
        "gemini": [
            subprocess.os.path.join(home, ".npm-global", "bin", "gemini"),
        ],
        "agy": [
            subprocess.os.path.join(home, ".local", "bin", "agy"),
            subprocess.os.path.join(home, ".npm-global", "bin", "agy"),
            "/usr/local/bin/agy",
        ],
        "antigravity": [
            subprocess.os.path.join(home, ".local", "bin", "antigravity"),
            subprocess.os.path.join(home, ".npm-global", "bin", "antigravity"),
            "/usr/local/bin/antigravity",
        ],
        "ollama": [
            "/usr/local/sbin/ollama",
            "/usr/local/bin/ollama",
            "/usr/bin/ollama",
        ],
    }
    for fb in fallbacks.get(cmd, []):
        if fb and shutil.which(fb):
            return shutil.which(fb)
        if fb and subprocess.os.path.exists(fb) and subprocess.os.access(fb, subprocess.os.X_OK):
            return fb
    return None


def _reconcile_verdict(verdict: str, summary: str, text: str) -> str:
    if verdict != "BUY":
        return verdict
    combined = f"{summary}\n{text or ''}".lower()
    if _has_caution(combined):
        return "WAIT"
    return verdict


def parse_analysis(symbol: str, text: str) -> tuple[str, str]:
    verdict = "UNKNOWN"
    summary = ""
    m = VERDICT_RE.search(text or "")
    if m:
        verdict = m.group(1).upper()
    else:
        low = (text or "").lower()
        if (
            re.search(r"\b(buy|add capital|accumulate|strong buy)\b", low)
            and not _has_caution(low)
            and not _CAUTION_RE.search(low)
        ):
            verdict = "BUY"
        elif _has_caution(low) or _CAUTION_RE.search(low) or re.search(
            r"\b(wait and see|wait|hold off|not yet|neutral)\b", low
        ):
            verdict = "WAIT"
        elif re.search(r"\b(sell|selling|sold)\b", low):
            verdict = "SELL"
        elif re.search(r"\bhold\b", low):
            verdict = "HOLD"
    ms = SUMMARY_RE.search(text or "")
    if ms:
        summary = _strip_md(ms.group(1))
    if not summary:
        for line in (text or "").splitlines():
            s = _strip_md(line)
            if s and not VERDICT_RE.match(s) and not s.lower().startswith("summary"):
                summary = s[:200]
                break
    verdict = _reconcile_verdict(verdict, summary, text or "")
    return verdict, summary


def _command_argv(symbol: str, prompt: str, cfg: dict[str, Any]) -> list[str] | None:
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    cmd = ai.get("command")
    if not isinstance(cmd, list) or not cmd:
        return None
    argv: list[str] = []
    for part in cmd:
        s = str(part).replace("{symbol}", symbol)
        argv.append(s.replace("{prompt}", prompt))
    if not argv:
        return None
    resolved = _resolve_command(argv[0])
    if not resolved:
        return argv
    argv[0] = resolved
    return argv


def _is_agy_command(argv: list[str]) -> bool:
    if not argv:
        return False
    name = subprocess.os.path.basename(str(argv[0])).lower()
    return name in {"agy", "agy.exe", "antigravity", "antigravity.exe"}


def _current_agy_model(argv: list[str]) -> str | None:
    a = list(argv)
    for i, p in enumerate(a):
        if p == "--model" and i + 1 < len(a):
            return str(a[i + 1])
        if isinstance(p, str) and p.startswith("--model="):
            return p.split("=", 1)[1]
    return None


def _with_agy_model(argv: list[str], model: str) -> list[str]:
    a = list(argv)
    for i, p in enumerate(a):
        if p == "--model" and i + 1 < len(a):
            a[i + 1] = model
            return a
        if isinstance(p, str) and p.startswith("--model="):
            a[i] = f"--model={model}"
            return a
    for flag in ("-p", "--print", "--prompt", "-i", "--prompt-interactive"):
        if flag in a:
            i = a.index(flag)
            return a[:i] + ["--model", model] + a[i:]
    return a + ["--model", model]


_YF_EXCHANGE_SUFFIXES = (
    ".TO",
    ".V",
    ".CN",
    ".NE",
    ".L",
    ".DE",
    ".F",
    ".SW",
    ".PA",
    ".AS",
    ".BR",
    ".MI",
    ".MC",
    ".ST",
    ".HE",
    ".CO",
    ".OL",
    ".IR",
    ".AX",
    ".NZ",
    ".HK",
    ".T",
    ".SI",
    ".KS",
    ".KQ",
    ".TW",
    ".TWO",
    ".SA",
    ".MX",
    ".BA",
    ".JO",
    ".PK",
)


def _yf_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return s
    try:
        from .screener import _to_yahoo_symbol  # type: ignore
        return _to_yahoo_symbol(s)
    except Exception:
        return s if "." in s else f"{s}.TO"


def _yf_symbol_candidates(symbol: str) -> list[str]:
    s = (symbol or "").strip().upper().replace("$", "")
    if not s:
        return []
    if s.startswith("^") or "=" in s:
        return [s]
    if any(s.endswith(suffix) for suffix in _YF_EXCHANGE_SUFFIXES):
        if s.endswith(".TO"):
            base = s[:-3]
            if "." in base:
                return [f"{base.replace('.', '-')}.TO"]
        return [s]
    if "." in s:
        base, _, cls = s.rpartition(".")
        hyphen = f"{base}-{cls}"
        return [hyphen, f"{hyphen}.TO", s]
    return [s, f"{s}.TO"]


def _fmt_num(v: Any, digits: int = 4) -> str:
    try:
        f = float(v)
    except Exception:
        return "n/a"
    if math.isnan(f) or math.isinf(f):
        return "n/a"
    if abs(f) >= 1000 and digits > 2:
        return f"{f:,.2f}"
    return f"{f:.{digits}f}"


def fetch_live_snapshot(symbol: str) -> tuple[str, str]:
    try:
        import pandas as pd
        import yfinance as yf
        from .screener import _sma, _rsi, _macd  # type: ignore
    except Exception as e:
        return "", f"Live data unavailable (missing dependency): {e}"

    candidates = _yf_symbol_candidates(symbol)
    if not candidates:
        return "", "No symbol provided for live data."
    data = None
    ysym = candidates[0]
    errors: list[str] = []
    for candidate in candidates:
        try:
            candidate_data = yf.download(
                candidate, period="10mo", interval="1d", progress=False, auto_adjust=True, threads=False
            )
        except Exception as e:
            errors.append(f"{candidate}: {e}")
            continue
        if candidate_data is None or getattr(candidate_data, "empty", True):
            errors.append(f"{candidate}: no data")
            continue
        data = candidate_data
        ysym = candidate
        break
    if data is None:
        detail = f" Last error: {errors[-1]}" if errors else ""
        return "", f"No live market data returned for {symbol} (tried {', '.join(candidates)}).{detail}"

    df = data
    if isinstance(getattr(df, "columns", None), pd.MultiIndex):
        if ysym in df.columns.get_level_values(0):
            df = df[ysym].copy()
        elif ysym in df.columns.get_level_values(-1):
            df = df.xs(ysym, axis=1, level=-1)
        else:
            try:
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            except Exception:
                pass
    if "Close" not in df.columns:
        return "", f"Live data for {ysym} missing Close prices."
    df = df.dropna(subset=["Close"])
    if len(df) < 30:
        return "", f"Insufficient live history for {ysym} ({len(df)} rows)."

    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float) if "Volume" in df.columns else None
    price = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else price
    chg = ((price / prev) - 1) * 100 if prev else 0.0
    asof = df.index[-1]
    try:
        asof_s = asof.date().isoformat()
    except Exception:
        asof_s = str(asof)

    s20 = _sma(close, 20).iloc[-1]
    s50 = _sma(close, 50).iloc[-1]
    s200 = _sma(close, 200).iloc[-1] if len(close) >= 200 else float("nan")
    rsi14 = _rsi(close, 14).iloc[-1]
    _ml, _ms, mh = _macd(close)
    mhv = mh.iloc[-1]
    mh1 = mh.iloc[-2] if len(mh) > 1 else float("nan")
    mh_dir = "rising" if (not math.isnan(float(mhv)) and not math.isnan(float(mh1)) and float(mhv) > float(mh1)) else "falling/flat"
    hi20 = float(close.tail(20).max())
    lo20 = float(close.tail(20).min())
    hi52 = float(close.tail(min(len(close), 252)).max())
    lo52 = float(close.tail(min(len(close), 252)).min())
    vol_s = "n/a"
    if vol is not None and len(vol.dropna()):
        v = float(vol.fillna(0).iloc[-1])
        v20 = float(vol.fillna(0).tail(20).mean())
        vol_s = f"{v:,.0f} (20d avg {v20:,.0f})"

    lines = [
        f"Source: Yahoo Finance via yfinance (live market data), symbol {ysym}, as of {asof_s}.",
        f"Price: {_fmt_num(price, 4)} ({chg:+.2f}% vs prior close)",
        f"SMA20: {_fmt_num(s20)} | SMA50: {_fmt_num(s50)} | SMA200: {_fmt_num(s200)}",
        f"RSI(14): {_fmt_num(rsi14, 2)}",
        f"MACD histogram: {_fmt_num(mhv, 6)} (prev {_fmt_num(mh1, 6)}, {mh_dir})",
        f"Volume: {vol_s}",
        f"20-day range (support/resistance proxy): {_fmt_num(lo20)} - {_fmt_num(hi20)}",
        f"52-week/available range: {_fmt_num(lo52)} - {_fmt_num(hi52)}",
        f"Bars used: {len(df)} daily closes.",
        "Use ONLY the data above for technical claims; if data is insufficient, say so.",
    ]
    return "\n".join(lines), ""


def _friendly_error(text: str) -> str:
    t = text or ""
    if "IneligibleTierError" in t or "no longer supported for Gemini Code Assist for individuals" in t:
        return (
            "Gemini CLI auth/tier is no longer supported for this account/client. "
            "Set `ai.command` in ~/.config/tech-watch/config.json to a working AI command "
            "(e.g. Antigravity/`agy` CLI if installed, or another Gemini-capable CLI/script "
            "that accepts the prompt)."
        )
    low = t.lower()
    if "resource_exhausted" in low or "quota" in low or "429" in low or "rate limit" in low or "too many requests" in low:
        line = (t.strip().splitlines() or ["Quota/rate limit reached"])[0]
        return line[:240]
    if "oauth" in low and "auth" in low:
        return "AI command authentication failed. Verify the configured command works in a terminal."
    return (t.strip().splitlines() or ["AI command failed"])[0][:240]


def _is_fallback_error(text: str) -> bool:
    low = (text or "").lower()
    markers = (
        "resource_exhausted",
        "quota",
        "error_code\":429",
        " 429",
        "(429",
        "code 429",
        "rate limit",
        "too many requests",
        "ineligibletier",
        "no longer supported for gemini code assist",
    )
    return any(m in low for m in markers)


def _is_missing_command_error(text: str) -> bool:
    low = (text or "").lower()
    markers = (
        "ai command not found",
        "could not find `",
        "command not found",
        "no such file or directory",
        "not found on path",
        "filenotfounderror",
    )
    return any(m in low for m in markers)


def _is_refusal_error(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _REFUSAL_MARKERS)


def _is_timeout_error(text: str) -> bool:
    low = (text or "").lower()
    return "timed out" in low or "timeout" in low


def _is_unusable_error(text: str) -> bool:
    return "unusable analysis" in (text or "").lower()


def _ollama_fallback(
    symbol: str,
    prompt: str,
    cfg: dict[str, Any],
    checked_at: str,
    prior_errors: list[str] | str,
    progress: Any = None,
) -> AnalysisResult | None:
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    if not ai.get("fallback_on_quota", True):
        return None
    errs = [prior_errors] if isinstance(prior_errors, str) else list(prior_errors or [])
    if not errs or not any(
        _is_fallback_error(e)
        or _is_missing_command_error(e)
        or _is_refusal_error(e)
        or _is_timeout_error(e)
        or _is_unusable_error(e)
        for e in errs
    ):
        return None
    model = str(ai.get("ollama_model") or "llama3.2:1b").strip() or "llama3.2:1b"
    cmd = str(ai.get("ollama_command") or "ollama").strip() or "ollama"
    resolved = _resolve_command(cmd) or shutil.which(cmd)
    if not resolved:
        return None
    try:
        ot = int(ai.get("ollama_timeout_seconds") or 180)
    except Exception:
        ot = 180

    if progress:
        try:
            progress(f"fetching live market data for {symbol}", symbol)
        except Exception:
            pass
    snapshot, snap_err = fetch_live_snapshot(symbol)
    prior_text = "\n".join(f"- {_friendly_error(e)[:220]}" for e in errs[-6:])
    if snap_err:
        note = f"Prior AI attempts failed:\n{prior_text}\nOllama fallback skipped: {snap_err}"
        return AnalysisResult(symbol, "ERROR", snap_err[:240], note, False, note, checked_at)

    full_prompt = (
        "You are a careful technical-analysis assistant.\n"
        "Use ONLY the live market data snapshot below. Do not guess or use outdated prices.\n"
        "If the snapshot is insufficient, answer with VERDICT: WAIT and explain briefly.\n\n"
        f"{snapshot}\n\n"
        f"User request:\n{prompt}\n\n"
        "End your response with exactly these two lines:\n"
        "VERDICT: BUY|WAIT|HOLD|SELL\n"
        "SUMMARY: <one short sentence>"
    )
    argv = [resolved, "run", model, full_prompt]
    if progress:
        try:
            progress(f"sending live snapshot to Ollama [{model}]", symbol)
        except Exception:
            pass
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=ot, env={**subprocess.os.environ})
    except subprocess.TimeoutExpired:
        note = f"Prior AI attempts failed:\n{prior_text}\nOllama fallback ({model}) timed out after {ot}s."
        return AnalysisResult(symbol, "ERROR", f"Ollama fallback timed out ({model})", note, False, note, checked_at)
    except Exception as e:
        note = f"Prior AI attempts failed:\n{prior_text}\nOllama fallback failed: {e}"
        return AnalysisResult(symbol, "ERROR", f"Ollama fallback failed: {e}", note, False, note, checked_at)

    if progress:
        try:
            progress(f"receiving response from Ollama [{model}]", symbol)
        except Exception:
            pass
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    text = out if out else err
    if proc.returncode != 0 and not out:
        note = f"Prior AI attempts failed:\n{prior_text}\nOllama fallback ({model}) error: {(err or text)[:500]}"
        return AnalysisResult(symbol, "ERROR", _friendly_error(err or text)[:240], note, False, note, checked_at)

    if progress:
        try:
            progress(f"analyzing response from Ollama [{model}]", symbol)
        except Exception:
            pass
    verdict, summary = parse_analysis(symbol, text)
    if _is_unusable_analysis(text, verdict):
        note = (
            f"Prior AI attempts failed:\n{prior_text}\n"
            f"Ollama fallback ({model}) returned no usable verdict:\n{(text or '(empty response)')[:500]}"
        )
        return AnalysisResult(
            symbol,
            "ERROR",
            "Ollama fallback returned no usable verdict",
            note,
            False,
            note,
            checked_at,
        )
    header = (
        f"[Fallback: local Ollama {model} using live Yahoo Finance snapshot because prior AI attempts failed]\n"
        f"Prior attempts:\n{prior_text}\n\n"
        f"{snapshot}\n\n---\n\n"
    )
    full_text = header + text
    return AnalysisResult(symbol, verdict, summary or "(no summary)", full_text, True, "", checked_at)


def _is_unusable_analysis(text: str, verdict: str) -> bool:
    low = (text or "").lower()
    has_verdict = bool(VERDICT_RE.search(text or ""))
    has_refusal = _is_refusal_error(low)
    if has_refusal and not (has_verdict and ("snapshot" in low or "provided data" in low)):
        return True
    # This app requires an explicit actionable verdict; unstructured replies should
    # fall through to other agy models / Ollama+live snapshot fallback.
    return not has_verdict


def _model_label(argv: list[str]) -> str:
    if _is_agy_command(argv):
        m = _current_agy_model(argv)
        return f"agy" + (f" [{m}]" if m else "")
    return subprocess.os.path.basename(str(argv[0])) if argv else "AI"


def _run_analysis_command(
    argv: list[str],
    symbol: str,
    timeout: int,
    progress: Any = None,
) -> tuple[AnalysisResult | None, str]:
    label = _model_label(argv)
    if progress:
        try:
            progress(f"sending query to {label}", symbol)
        except Exception:
            pass
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env={**subprocess.os.environ})
    except subprocess.TimeoutExpired:
        return None, f"{label}: timed out after {timeout}s"
    except Exception as e:
        return None, f"{label}: {e}"
    if progress:
        try:
            progress(f"receiving response from {label}", symbol)
        except Exception:
            pass

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    text = out if out else err
    checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if proc.returncode != 0 and not out:
        return None, (err or text or f"exit {proc.returncode}")

    if progress:
        try:
            progress(f"analyzing response from {label}", symbol)
        except Exception:
            pass
    verdict, summary = parse_analysis(symbol, text)
    primary_err = _friendly_error(err) if proc.returncode != 0 and err else ""
    ok = proc.returncode == 0 or bool(out)
    if verdict == "ERROR" and _is_fallback_error(err or text):
        return None, (err or text)
    if verdict == "UNKNOWN" and proc.returncode != 0:
        return None, (err or text or "unknown verdict")
    if _is_unusable_analysis(text, verdict):
        detail = (text or err or "missing actionable verdict")[:300]
        return None, f"unusable analysis: {detail}"
    return AnalysisResult(symbol, verdict, summary or "(no summary)", text, ok, primary_err, checked_at), ""


def _agy_fallback_models(argv: list[str], cfg: dict[str, Any]) -> list[str]:
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    models = ai.get("agy_fallback_models")
    if not isinstance(models, list):
        models = list(DEFAULT_CONFIG["ai"]["agy_fallback_models"])
    current = _current_agy_model(argv)
    out: list[str] = []
    for m in models:
        s = str(m).strip()
        if not s or s == current or s in out:
            continue
        out.append(s)
    return out


def query_gemini(symbol: str, cfg: dict[str, Any], timeout: int | None = None, progress: Any = None) -> AnalysisResult:
    checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    template = str(cfg.get("query_template") or "")
    prompt = template.replace("{symbol}", symbol)
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
        primary_error = f"Could not find `{argv[0]}`. Configure `ai.command` in ~/.config/tech-watch/config.json."
        fb = _ollama_fallback(symbol, prompt, cfg, checked_at, [primary_error], progress=progress)
        if fb:
            return fb
        return AnalysisResult(symbol=symbol, verdict="ERROR", summary="AI command not found", full_text=cmd_str, ok=False, error=primary_error, checked_at=checked_at)

    attempts: list[tuple[str, str]] = []  # (label, error)
    result, err = _run_analysis_command(argv, symbol, timeout, progress=progress)
    if result:
        return result
    label = shlex.join(argv[:3]) + (" ..." if len(argv) > 3 else "")
    if _current_agy_model(argv):
        label = f"agy [{_current_agy_model(argv)}]"
    attempts.append((label, err))

    if _is_agy_command(argv) and (
        _is_fallback_error(err)
        or _is_refusal_error(err)
        or _is_timeout_error(err)
        or _is_unusable_error(err)
    ):
        for model in _agy_fallback_models(argv, cfg):
            if progress:
                try:
                    progress(f"switching agy fallback model to [{model}]", symbol)
                except Exception:
                    pass
            model_argv = _with_agy_model(argv, model)
            result, merr = _run_analysis_command(model_argv, symbol, timeout, progress=progress)
            if result:
                result.full_text = (
                    f"[Fallback within agy: succeeded with model {model} after prior quota/error]\n"
                    f"Prior attempts:\n" + "\n".join(f"- {a}: {_friendly_error(e)[:180]}" for a, e in attempts) + "\n\n---\n\n" + result.full_text
                )
                return result
            attempts.append((f"agy [{model}]", merr))

    prior_errors = [f"{a}: {e}" for a, e in attempts]
    fb = _ollama_fallback(symbol, prompt, cfg, checked_at, prior_errors, progress=progress)
    if fb:
        return fb
    last = attempts[-1][1] if attempts else "AI command failed"
    friendly = _friendly_error(last)
    detail = "Tried:\n" + "\n".join(f"- {a}: {_friendly_error(e)[:180]}" for a, e in attempts)
    log.debug("AI check failed for %s: %s", symbol, friendly)
    return AnalysisResult(symbol, "ERROR", friendly[:240], detail, False, friendly, checked_at)

