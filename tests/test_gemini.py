import subprocess
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

import tech_watch.gemini as gemini


def test_resolve_command_uses_which(monkeypatch):
    monkeypatch.setattr(gemini.shutil, "which", lambda c: "/bin/gemini" if c == "gemini" else None)
    assert gemini._resolve_command("gemini") == "/bin/gemini"
    assert gemini._resolve_command("") is None


def test_resolve_command_fallback_path(monkeypatch):
    monkeypatch.setattr(gemini.shutil, "which", lambda c: None)
    monkeypatch.setattr(gemini.subprocess.os.path, "exists", lambda p: True)
    expected = gemini.subprocess.os.path.join(
        gemini.subprocess.os.path.expanduser("~"), ".npm-global", "bin", "gemini"
    )
    assert gemini._resolve_command("gemini") == expected


def test_reconcile_verdict_buy_wait_cues():
    assert gemini._reconcile_verdict("BUY", "wait for breakout confirmation", "") == "WAIT"
    assert gemini._reconcile_verdict("BUY", "no clear breakout yet", "") == "WAIT"
    assert gemini._reconcile_verdict("BUY", "add only if it breaks above resistance", "not yet confirmed") == "WAIT"
    assert gemini._reconcile_verdict("SELL", "wait for breakout", "") == "SELL"
    assert gemini._reconcile_verdict("BUY", "momentum is strong", "add capital now") == "BUY"


def test_parse_analysis_explicit_verdict():
    text = "Analysis\nVERDICT: BUY\nSUMMARY: Strong uptrend"
    v, s = gemini.parse_analysis("X", text)
    assert v == "BUY"
    assert s == "Strong uptrend"


def test_parse_analysis_reconciles_wait():
    text = "VERDICT: BUY\nSUMMARY: Wait for a breakout above resistance"
    v, _ = gemini.parse_analysis("X", text)
    assert v == "WAIT"


def test_parse_analysis_heuristics():
    assert gemini.parse_analysis("X", "You should buy and accumulate")[0] == "BUY"
    assert gemini.parse_analysis("X", "Wait and see for now")[0] == "WAIT"
    assert gemini.parse_analysis("X", "Consider selling this position")[0] == "SELL"
    assert gemini.parse_analysis("X", "Hold your shares")[0] == "HOLD"
    v, s = gemini.parse_analysis("X", "")
    assert v == "UNKNOWN"
    assert s == ""


def test_command_argv_substitutes_and_resolves(monkeypatch):
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: f"/x/{c}")
    cfg = {"ai": {"command": ["myai", "--sym", "{symbol}", "{prompt}"]}}
    argv = gemini._command_argv("AAPL", "hello", cfg)
    assert argv == ["/x/myai", "--sym", "AAPL", "hello"]


def test_command_argv_missing_command(monkeypatch):
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: f"/x/{c}")
    assert gemini._command_argv("A", "p", {"gemini_command": "g", "gemini_args": ["-p"]}) is None
    assert gemini._command_argv("A", "p", {"ai": {"command": []}}) is None


def test_is_agy_command_and_model_helpers():
    assert gemini._is_agy_command(["/usr/bin/agy", "-p"])
    assert gemini._is_agy_command(["C:/antigravity.exe"])
    assert not gemini._is_agy_command(["gemini"])
    assert not gemini._is_agy_command([])

    argv = ["agy", "--model", "m1", "-p", "x"]
    assert gemini._current_agy_model(argv) == "m1"
    assert gemini._current_agy_model(["agy", "--model=m2"]) == "m2"
    assert gemini._current_agy_model(["agy", "-p"]) is None

    assert gemini._with_agy_model(["agy", "--model", "old", "-p", "x"], "new")[2] == "new"
    assert gemini._with_agy_model(["agy", "--model=old"], "new") == ["agy", "--model=new"]
    assert gemini._with_agy_model(["agy", "-p", "x"], "m") == ["agy", "--model", "m", "-p", "x"]
    assert gemini._with_agy_model(["agy"], "m") == ["agy", "--model", "m"]


def test_yf_symbol():
    assert gemini._yf_symbol("XIC.TO") == "XIC.TO"
    assert gemini._yf_symbol("xic") == "XIC.TO"
    assert gemini._yf_symbol("BRK-B") == "BRK-B.TO"
    assert gemini._yf_symbol("") == ""


def test_yf_symbol_candidates_tries_us_and_tsx():
    assert gemini._yf_symbol_candidates("AAPL") == ["AAPL", "AAPL.TO"]
    assert gemini._yf_symbol_candidates("$AAPL") == ["AAPL", "AAPL.TO"]
    assert gemini._yf_symbol_candidates("XIC") == ["XIC", "XIC.TO"]
    assert gemini._yf_symbol_candidates("XIC.TO") == ["XIC.TO"]
    assert gemini._yf_symbol_candidates("BBD.B") == ["BBD-B", "BBD-B.TO", "BBD.B"]
    assert gemini._yf_symbol_candidates("BBD.B.TO") == ["BBD-B.TO"]
    assert gemini._yf_symbol_candidates("BRK.B") == ["BRK-B", "BRK-B.TO", "BRK.B"]
    assert gemini._yf_symbol_candidates("BRK-B") == ["BRK-B", "BRK-B.TO"]
    assert gemini._yf_symbol_candidates("^GSPC") == ["^GSPC"]
    assert gemini._yf_symbol_candidates("EURUSD=X") == ["EURUSD=X"]
    assert gemini._yf_symbol_candidates("") == []


def test_fetch_live_snapshot_uses_adjusted_prices_and_candidates(monkeypatch):
    calls = []
    idx = pd.date_range("2024-01-01", periods=60, freq="B")
    df = pd.DataFrame({"Close": range(1, 61), "Volume": [1_000_000] * 60}, index=idx)

    def download(symbol, **kwargs):
        calls.append((symbol, kwargs))
        if symbol == "AAPL":
            return pd.DataFrame()
        return df

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    text, err = gemini.fetch_live_snapshot("AAPL")
    assert err == ""
    assert calls[0][0] == "AAPL"
    assert calls[1][0] == "AAPL.TO"
    assert all(kwargs["auto_adjust"] is True for _, kwargs in calls)
    assert "AAPL.TO" in text


def test_fetch_live_snapshot_retries_after_candidate_exception(monkeypatch):
    calls = []
    idx = pd.date_range("2024-01-01", periods=60, freq="B")
    df = pd.DataFrame({"Close": range(1, 61), "Volume": [1_000_000] * 60}, index=idx)

    def download(symbol, **kwargs):
        calls.append(symbol)
        if symbol == "AAPL":
            raise RuntimeError("temporary failure")
        return df

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    text, err = gemini.fetch_live_snapshot("AAPL")
    assert err == ""
    assert calls == ["AAPL", "AAPL.TO"]
    assert "AAPL.TO" in text


def test_fetch_live_snapshot_reports_all_candidates_failed(monkeypatch):
    calls = []

    def download(symbol, **kwargs):
        calls.append(symbol)
        return pd.DataFrame()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    text, err = gemini.fetch_live_snapshot("AAPL")
    assert text == ""
    assert calls == ["AAPL", "AAPL.TO"]
    assert "tried AAPL, AAPL.TO" in err
    assert "Last error: AAPL.TO: no data" in err


def test_fmt_num():
    assert gemini._fmt_num(1.23456) == "1.2346"
    assert gemini._fmt_num(1234.5, 4) == "1,234.50"
    assert gemini._fmt_num("bad") == "n/a"
    assert gemini._fmt_num(float("nan")) == "n/a"
    assert gemini._fmt_num(float("inf")) == "n/a"


def test_friendly_error_and_fallback_detection():
    assert "no longer supported" in gemini._friendly_error("IneligibleTierError blah")
    assert "429" in gemini._friendly_error("HTTP 429 resource_exhausted quota")
    assert "authentication failed" in gemini._friendly_error("OAuth auth error")
    assert gemini._friendly_error("\n\nboom\nmore") == "boom"
    assert gemini._is_fallback_error("resource_exhausted")
    assert gemini._is_fallback_error("code 429")
    assert gemini._is_fallback_error("rate limit")
    assert not gemini._is_fallback_error("syntax error")


def test_model_label():
    assert gemini._model_label(["/bin/agy", "--model", "m"]) == "agy [m]"
    assert gemini._model_label(["/usr/bin/gemini"]) == "gemini"
    assert gemini._model_label([]) == "AI"


def test_agy_fallback_models_filters_current_and_dupes():
    argv = ["agy", "--model", "m1"]
    cfg = {"ai": {"agy_fallback_models": ["m1", "m2", "m2", " m3 ", ""]}}
    assert gemini._agy_fallback_models(argv, cfg) == ["m2", "m3"]


def test_is_unusable_analysis():
    assert gemini._is_unusable_analysis("I don't have access to live market data", "UNKNOWN")
    assert gemini._is_unusable_analysis("I am unable to provide real-time info", "UNKNOWN")
    assert gemini._is_unusable_analysis("short generic", "UNKNOWN")
    good = "Looks fine\nVERDICT: BUY\nSUMMARY: ok"
    assert not gemini._is_unusable_analysis(good, "BUY")


def test_run_analysis_command_success(monkeypatch):
    proc = SimpleNamespace(returncode=0, stdout="VERDICT: BUY\nSUMMARY: up", stderr="")
    monkeypatch.setattr(gemini.subprocess, "run", lambda *a, **k: proc)
    events = []
    result, err = gemini._run_analysis_command(["gemini"], "X", 10, progress=lambda d, s: events.append((d, s)))
    assert err == ""
    assert result and result.verdict == "BUY" and result.ok
    assert any("sending" in e[0] for e in events)


def test_run_analysis_command_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(gemini.subprocess, "run", boom)
    result, err = gemini._run_analysis_command(["gemini"], "X", 1)
    assert result is None
    assert "timed out" in err


def test_run_analysis_command_unusable(monkeypatch):
    proc = SimpleNamespace(returncode=0, stdout="I don't have access to live market data", stderr="")
    monkeypatch.setattr(gemini.subprocess, "run", lambda *a, **k: proc)
    result, err = gemini._run_analysis_command(["agy"], "X", 10)
    assert result is None
    assert err


def test_ollama_fallback_disabled(monkeypatch):
    assert gemini._ollama_fallback("X", "p", {"ai": {"fallback_on_quota": False}}, "t", ["429 quota"]) is None


def test_ollama_fallback_not_triggered():
    assert gemini._ollama_fallback("X", "p", {"ai": {}}, "t", ["syntax error"]) is None
    assert gemini._ollama_fallback("X", "p", {"ai": {}}, "t", []) is None


def test_ollama_fallback_success(monkeypatch):
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: "/bin/ollama")
    monkeypatch.setattr(gemini.shutil, "which", lambda c: "/bin/ollama")
    monkeypatch.setattr(gemini, "fetch_live_snapshot", lambda s: ("LIVE DATA", ""))
    proc = SimpleNamespace(returncode=0, stdout="VERDICT: WAIT\nSUMMARY: mixed", stderr="")
    monkeypatch.setattr(gemini.subprocess, "run", lambda *a, **k: proc)
    res = gemini._ollama_fallback("XIC.TO", "prompt", {"ai": {"ollama_model": "m"}}, "t", ["429 quota"])
    assert res and res.verdict == "WAIT" and res.ok
    assert "Yahoo Finance snapshot" in res.full_text


def test_ollama_fallback_snapshot_error(monkeypatch):
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: "/bin/ollama")
    monkeypatch.setattr(gemini.shutil, "which", lambda c: "/bin/ollama")
    monkeypatch.setattr(gemini, "fetch_live_snapshot", lambda s: ("", "no data"))
    res = gemini._ollama_fallback("X", "p", {"ai": {}}, "t", ["quota"])
    assert res and not res.ok and "no data" in res.summary


def test_fetch_live_snapshot_missing_deps(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *a, **k):
        if name in ("yfinance", "numpy", "pandas"):
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    text, err = gemini.fetch_live_snapshot("X")
    assert text == "" and "Live data unavailable" in err


def test_query_gemini_no_command(monkeypatch):
    monkeypatch.setattr(gemini, "_command_argv", lambda *a, **k: None)
    res = gemini.query_gemini("X", {})
    assert not res.ok and res.verdict == "ERROR"


def test_query_gemini_command_not_found_with_fallback(monkeypatch):
    monkeypatch.setattr(gemini, "_command_argv", lambda *a, **k: ["missing", "-p", "x"])
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: None)
    monkeypatch.setattr(gemini.shutil, "which", lambda c: None)
    fb = gemini.AnalysisResult("X", "WAIT", "fb", "t", True, "", "t")
    monkeypatch.setattr(gemini, "_ollama_fallback", lambda *a, **k: fb)
    assert gemini.query_gemini("X", {"query_template": "{symbol}"}) is fb


def test_query_gemini_agy_fallback_chain(monkeypatch):
    argv = ["agy", "--model", "bad", "-p", "q"]
    monkeypatch.setattr(gemini, "_command_argv", lambda *a, **k: list(argv))
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: f"/bin/{c}")
    monkeypatch.setattr(gemini.shutil, "which", lambda c: f"/bin/{c}")
    calls = []

    def run(argv, symbol, timeout, progress=None):
        calls.append(argv[2] if "--model" in argv else None)
        if argv[2] == "good":
            return gemini.AnalysisResult(symbol, "BUY", "ok", "body", True, "", "t"), ""
        return None, "429 resource_exhausted quota"

    monkeypatch.setattr(gemini, "_run_analysis_command", run)
    cfg = {"query_template": "{symbol}", "ai": {"agy_fallback_models": ["good"], "timeout_seconds": 5}}
    res = gemini.query_gemini("X", cfg)
    assert res.verdict == "BUY"
    assert "succeeded with model good" in res.full_text
    assert calls == ["bad", "good"]


def test_query_gemini_ollama_fallback_on_quota(monkeypatch):
    argv = ["gemini", "-p", "q"]
    monkeypatch.setattr(gemini, "_command_argv", lambda *a, **k: list(argv))
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: f"/bin/{c}")
    monkeypatch.setattr(gemini, "_run_analysis_command", lambda *a, **k: (None, "429 quota"))
    fb = gemini.AnalysisResult("X", "WAIT", "fb", "t", True, "", "t")
    monkeypatch.setattr(gemini, "_ollama_fallback", lambda *a, **k: fb)
    assert gemini.query_gemini("X", {"query_template": "{symbol}"}) is fb
