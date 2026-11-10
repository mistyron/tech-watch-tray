import math

import numpy as np
import pandas as pd
import pytest

import tech_watch.screener as screener


def test_now_iso():
    s = screener._now_iso()
    assert "T" in s and len(s) >= 19


def test_to_yahoo_symbol():
    assert screener._to_yahoo_symbol("xic") == "XIC.TO"
    assert screener._to_yahoo_symbol("XIC.TO") == "XIC.TO"
    assert screener._to_yahoo_symbol(" brk-b ") == "BRK-B.TO"
    assert screener._to_yahoo_symbol("$AAPL") == "AAPL.TO"
    assert screener._to_yahoo_symbol("BBD.B") == "BBD-B.TO"
    assert screener._to_yahoo_symbol("BBD.B.TO") == "BBD-B.TO"
    assert screener._to_yahoo_symbol("") == ""


def test_chunks():
    assert list(screener._chunks(["a", "b", "c", "d"], 2)) == [["a", "b"], ["c", "d"]]
    assert list(screener._chunks(["a"], 5)) == [["a"]]


def test_indicators_shapes():
    # include up/down moves so RSI/MACD produce non-trivial values
    rng = np.random.default_rng(0)
    s = pd.Series(100 + np.cumsum(rng.normal(0.1, 1.0, 120)), dtype=float)
    assert len(screener._sma(s, 20)) == 120
    assert len(screener._ema(s, 20)) == 120
    assert screener._sma(s, 20).iloc[:19].isna().all()
    assert not pd.isna(screener._sma(s, 20).iloc[19])
    assert screener._ema(s, 20).iloc[:19].isna().all()
    assert not pd.isna(screener._ema(s, 20).iloc[19])
    rsi = screener._rsi(s, 14)
    assert len(rsi) == 120
    valid = rsi.dropna()
    assert not valid.empty
    assert float(valid.min()) >= 0 and float(valid.max()) <= 100
    ml, ms, mh = screener._macd(s)
    assert len(ml) == len(ms) == len(mh) == 120


def test_analyze_history_empty():
    assert screener.analyze_history("X", "X", pd.DataFrame()) is None
    assert screener.analyze_history("X", "X", None) is None
    df = pd.DataFrame({"Close": [1, 2, 3]})
    assert screener.analyze_history("X", "X", df) is None  # too short


def _make_df(n=260, start=10.0, drift=0.002, vol=1e6):
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    steps = np.random.default_rng(42).normal(drift, 0.01, n)
    close = start * np.exp(np.cumsum(steps))
    volume = np.full(n, vol)
    return pd.DataFrame({"Close": close, "Volume": volume, "Dividends": 0.0}, index=idx)


def test_analyze_history_returns_hit_and_dict():
    hit = screener.analyze_history("X.TO", "Test Co", _make_df())
    assert hit is not None
    assert hit.symbol == "X.TO"
    assert 0 <= hit.score <= 100
    assert hit.verdict in {"SCREAMING BUY", "STRONG", "WATCH", "NEUTRAL"}
    assert 0 <= hit.risk <= 100
    d = hit.to_dict()
    assert d["symbol"] == "X.TO" and "metrics" in d and "checked_at" in d


def test_analyze_history_downtrend_penalties():
    n = 260
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = np.linspace(100, 40, n)
    df = pd.DataFrame({"Close": close, "Volume": 1e6}, index=idx)
    hit = screener.analyze_history("D.TO", "Down", df)
    assert hit is not None
    assert any("Downtrend" in r or "Weak" in r or "below" in r for r in hit.reasons) or hit.score < 50


def test_analyze_history_dividend_yield():
    df = _make_df(n=260)
    df["Dividends"] = 0.0
    df.iloc[-1, df.columns.get_loc("Dividends")] = 0.25
    df.iloc[-64, df.columns.get_loc("Dividends")] = 0.25
    df.iloc[-128, df.columns.get_loc("Dividends")] = 0.25
    df.iloc[-192, df.columns.get_loc("Dividends")] = 0.25
    hit = screener.analyze_history("DIV.TO", "Div", df)
    assert hit is not None
    # may compute a yield if paid rows seen
    assert hit.metrics["dividend_yield_pct"] is None or hit.metrics["dividend_yield_pct"] >= 0


def test_analyze_history_dividend_yield_uses_trailing_year():
    df = _make_df(n=520, start=10.0, drift=0.0)
    df["Dividends"] = 0.0
    for pos in (-1, -80, -160, -300):
        df.iloc[pos, df.columns.get_loc("Dividends")] = 0.25
    hit = screener.analyze_history("DIV.TO", "Div", df)
    assert hit is not None
    price = hit.price
    expected = round((0.75 / price) * 100, 2)
    assert hit.metrics["dividend_yield_pct"] == expected


def test_analyze_history_minimum_bars_does_not_crash():
    n = 60
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    df = pd.DataFrame(
        {"Close": np.linspace(10, 12, n), "Volume": 1_000_000},
        index=idx,
    )
    hit = screener.analyze_history("X.TO", "X", df)
    assert hit is not None
    assert hit.metrics["sma200"] is None
    assert 0 <= hit.score <= 100


def test_screen_symbols_requires_yfinance(monkeypatch):
    monkeypatch.setattr(screener, "yf", None)
    with pytest.raises(RuntimeError, match="yfinance"):
        screener.screen_symbols([{"symbol": "X.TO", "name": "X"}])


def test_screen_symbols_with_mocked_yfinance(monkeypatch):
    df = _make_df()
    fake_yf = SimpleNamespaceYF(df)
    monkeypatch.setattr(screener, "yf", fake_yf)
    monkeypatch.setattr(screener.time, "sleep", lambda *_: None)
    calls = []
    errors = []
    hits = screener.screen_symbols(
        [{"symbol": "X.TO", "name": "X"}, {"symbol": "Y.TO", "name": "Y"}],
        min_score=0,
        batch_size=10,
        progress_cb=lambda d, t, s: calls.append((d, t, s)),
        errors=errors,
    )
    assert isinstance(hits, list)
    assert calls
    assert errors == []
    assert fake_yf.calls[0]["auto_adjust"] is True


def test_screen_symbols_surfaces_download_failure(monkeypatch):
    class BadYF:
        def download(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(screener, "yf", BadYF())
    monkeypatch.setattr(screener.time, "sleep", lambda *_: None)
    errors = []
    hits = screener.screen_symbols([{"symbol": "X.TO", "name": "X"}], errors=errors)
    assert hits == []
    assert any("network down" in e for e in errors)


class SimpleNamespaceYF:
    def __init__(self, df):
        self._df = df
        self.calls = []

    def download(self, tickers, period, interval, group_by, auto_adjust, progress, threads, actions=False):
        self.calls.append({"tickers": tickers, "auto_adjust": auto_adjust})
        # return MultiIndex like yfinance group_by='ticker' for multiple tickers
        syms = str(tickers).split()
        if len(syms) <= 1:
            return self._df.copy()
        arrays = []
        for s in syms:
            for c in self._df.columns:
                arrays.append((s, c))
        mi = pd.MultiIndex.from_tuples(arrays)
        data = pd.concat([self._df] * len(syms), axis=1)
        data.columns = mi
        return data


def test_fetch_functions_and_get_universe(monkeypatch):
    monkeypatch.setattr(screener, "fetch_tsx_composite_symbols", lambda timeout=20: [{"symbol": "A.TO"}, {"symbol": "B.TO"}])
    monkeypatch.setattr(screener, "fetch_tsx_directory_symbols", lambda limit=0, timeout=20: [{"symbol": "B.TO"}, {"symbol": "C.TO"}][: (limit or None)])
    assert screener.get_universe("tsx_composite") == [{"symbol": "A.TO"}, {"symbol": "B.TO"}]
    assert screener.get_universe("tsx_composite", limit=1) == [{"symbol": "A.TO"}]
    assert screener.get_universe("tsx_all", limit=1) == [{"symbol": "B.TO"}]


def test_fetch_tsx_composite_symbols_parses_table(monkeypatch):
    table = pd.DataFrame({"Ticker": ["RY", "TD"], "Company": ["Royal", "TD Bank"]})

    class Resp:
        text = "<table></table>"
        def raise_for_status(self): pass

    monkeypatch.setattr(screener.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(screener.pd, "read_html", lambda *a, **k: [table])
    rows = screener.fetch_tsx_composite_symbols()
    assert rows[0]["symbol"] == "RY.TO"
    assert rows[0]["name"] == "Royal"


def test_fetch_tsx_composite_symbols_no_table(monkeypatch):
    class Resp:
        text = "x"
        def raise_for_status(self): pass

    monkeypatch.setattr(screener.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(screener.pd, "read_html", lambda *a, **k: [pd.DataFrame({"a": [1]})])
    with pytest.raises(RuntimeError):
        screener.fetch_tsx_composite_symbols()


def test_fetch_tsx_directory_symbols(monkeypatch):
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"results": [{"symbol": "RY", "name": "Royal"}, {"symbol": "BAD SYM", "name": "x"}]}

    monkeypatch.setattr(screener.requests, "get", lambda *a, **k: Resp())
    rows = screener.fetch_tsx_directory_symbols(limit=10)
    assert rows == [{"symbol": "RY.TO", "name": "Royal", "source_symbol": "RY"}]
