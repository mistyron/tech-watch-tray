import subprocess
from types import SimpleNamespace

import pytest

import tech_watch.gemini as gemini


def test_resolve_command_uses_which(monkeypatch):
    monkeypatch.setattr(gemini.shutil, "which", lambda c: "/bin/gemini" if c == "gemini" else None)
    assert gemini._resolve_command("gemini") == "/bin/gemini"
    assert gemini._resolve_command("") is None


def test_resolve_command_fallback_path(monkeypatch):
    monkeypatch.setattr(gemini.shutil, "which", lambda c: None)
    monkeypatch.setattr(gemini.subprocess.os.path, "exists", lambda p: True)
    assert gemini._resolve_command("gemini") == "/home/ron_plasma/.npm-global/bin/gemini"


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


def test_command_argv_legacy_and_missing_command(monkeypatch):
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: f"/x/{c}")
    argv = gemini._command_argv("A", "p", {"gemini_command": "g", "gemini_args": ["-p"]})
    assert argv == ["/x/g", "-p", "p"]
    assert gemini._command_argv("A", "p", {"ai": {"command": []}}) is not None  # falls to legacy


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


def test_parse_analysis_markdown_bold():
    text = "Analysis\n**Verdict:** WAIT\n**Summary:** Hold current shares"
    assert gemini.parse_analysis("X", text) == ("WAIT", "Hold current shares")
    text = "- **VERDICT: BUY**\n- **SUMMARY: Breakout confirmed**"
    assert gemini.parse_analysis("X", text) == ("BUY", "Breakout confirmed")


def test_parse_analysis_conditional_buy_is_wait():
    assert gemini.parse_analysis("X", "You should buy only if it breaks above resistance")[0] == "WAIT"
    assert gemini.parse_analysis("X", "Buy now, momentum is strong")[0] == "BUY"


def test_is_unusable_analysis_requires_actionable_verdict():
    assert gemini._is_unusable_analysis("Analysis " * 100, "UNKNOWN")
    assert gemini._is_unusable_analysis("I don't have access to live market data\nVERDICT: BUY", "BUY")
    assert not gemini._is_unusable_analysis(
        "Based on the provided snapshot\nVERDICT: BUY\nSUMMARY: ok", "BUY"
    )
    assert not gemini._is_unusable_analysis("**VERDICT: BUY**\n**SUMMARY:** ok", "BUY")


def test_command_argv_preserves_unresolved_args(monkeypatch):
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: None)
    cfg = {"ai": {"command": ["missing", "--flag", "{prompt}"]}}
    assert gemini._command_argv("X", "hi", cfg) == ["missing", "--flag", "hi"]


def test_command_argv_handles_literal_braces(monkeypatch):
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: "/x/ai")
    cfg = {"ai": {"command": ["ai", "{prompt}"]}}
    assert gemini._command_argv("X", '{"a": 1}', cfg) == ["/x/ai", '{"a": 1}']


def test_query_gemini_template_with_literal_braces(monkeypatch):
    captured = {}

    def fake_argv(symbol, prompt, cfg):
        captured["prompt"] = prompt
        return ["ai", prompt]

    monkeypatch.setattr(gemini, "_command_argv", fake_argv)
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: "/bin/ai")
    monkeypatch.setattr(gemini.shutil, "which", lambda c: "/bin/ai")
    monkeypatch.setattr(
        gemini,
        "_run_analysis_command",
        lambda *a, **k: (gemini.AnalysisResult("X", "BUY", "ok", "body", True, "", "t"), ""),
    )
    cfg = {"query_template": 'Analyze {"symbol": "{symbol}"}'}
    res = gemini.query_gemini("X", cfg)
    assert res.verdict == "BUY"
    assert captured["prompt"] == 'Analyze {"symbol": "X"}'


def test_query_gemini_falls_back_on_unusable(monkeypatch):
    argv = ["gemini", "-p", "q"]
    monkeypatch.setattr(gemini, "_command_argv", lambda *a, **k: list(argv))
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: f"/bin/{c}")
    monkeypatch.setattr(gemini.shutil, "which", lambda c: f"/bin/{c}")
    monkeypatch.setattr(
        gemini, "_run_analysis_command", lambda *a, **k: (None, "unusable analysis: no verdict")
    )
    fb = gemini.AnalysisResult("X", "WAIT", "fb", "t", True, "", "t")
    monkeypatch.setattr(gemini, "_ollama_fallback", lambda *a, **k: fb)
    assert gemini.query_gemini("X", {"query_template": "{symbol}"}) is fb


def test_query_gemini_agy_fallback_on_unusable(monkeypatch):
    argv = ["agy", "--model", "bad", "-p", "q"]
    monkeypatch.setattr(gemini, "_command_argv", lambda *a, **k: list(argv))
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: f"/bin/{c}")
    monkeypatch.setattr(gemini.shutil, "which", lambda c: f"/bin/{c}")
    calls = []

    def run(argv, symbol, timeout, progress=None):
        calls.append(argv[2] if "--model" in argv else None)
        if argv[2] == "good":
            return gemini.AnalysisResult(symbol, "BUY", "ok", "body", True, "", "t"), ""
        return None, "unusable analysis: no verdict"

    monkeypatch.setattr(gemini, "_run_analysis_command", run)
    cfg = {"query_template": "{symbol}", "ai": {"agy_fallback_models": ["good"], "timeout_seconds": 5}}
    res = gemini.query_gemini("X", cfg)
    assert res.verdict == "BUY"
    assert calls == ["bad", "good"]


def test_ollama_fallback_rejects_unusable_output(monkeypatch):
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: "/bin/ollama")
    monkeypatch.setattr(gemini.shutil, "which", lambda c: "/bin/ollama")
    monkeypatch.setattr(gemini, "fetch_live_snapshot", lambda s: ("LIVE DATA", ""))
    proc = SimpleNamespace(returncode=0, stdout="no verdict here", stderr="")
    monkeypatch.setattr(gemini.subprocess, "run", lambda *a, **k: proc)
    res = gemini._ollama_fallback("XIC.TO", "prompt", {"ai": {"ollama_model": "m"}}, "t", ["429 quota"])
    assert res and not res.ok
    assert "no usable verdict" in res.summary


def test_query_gemini_end_to_end_markdown(monkeypatch):
    proc = SimpleNamespace(
        returncode=0, stdout="**Verdict:** BUY\n**Summary:** Breakout confirmed", stderr=""
    )
    monkeypatch.setattr(gemini.subprocess, "run", lambda *a, **k: proc)
    monkeypatch.setattr(gemini, "_resolve_command", lambda c: "/bin/ai")
    monkeypatch.setattr(gemini.shutil, "which", lambda c: "/bin/ai")
    cfg = {"query_template": "Analyze {symbol}", "ai": {"command": ["ai", "{prompt}"]}}
    res = gemini.query_gemini("X", cfg)
    assert res.verdict == "BUY"
    assert res.summary == "Breakout confirmed"


def test_error_classifiers():
    assert gemini._is_refusal_error("I can't access real-time data")
    assert gemini._is_timeout_error("agy: timed out after 5s")
    assert gemini._is_unusable_error("unusable analysis: no verdict")
