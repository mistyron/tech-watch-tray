import io
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None


@dataclass
class ScreenHit:
    symbol: str
    name: str
    score: int
    verdict: str
    price: float | None
    change_pct: float | None
    dividend_yield_pct: float | None
    reasons: list[str]
    metrics: dict[str, Any]
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "score": self.score,
            "verdict": self.verdict,
            "price": self.price,
            "change_pct": self.change_pct,
            "dividend_yield_pct": self.dividend_yield_pct,
            "reasons": self.reasons,
            "metrics": self.metrics,
            "checked_at": self.checked_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _to_yahoo_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper().replace("$", "")
    if not s:
        return s
    if "." in s:
        return s
    # bare Canadian tickers in some lists need exchange suffix; default to TSX
    return f"{s}.TO"


def _chunks(items: list[str], n: int) -> Iterable[list[str]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def fetch_tsx_composite_symbols(timeout: int = 20) -> list[dict[str, str]]:
    url = "https://en.wikipedia.org/wiki/S%26P/TSX_Composite_Index"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tech-watch-tray/0.1"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    for t in tables:
        cols = [str(c) for c in t.columns]
        joined = " ".join(cols).lower()
        if "symbol" in joined or "ticker" in joined:
            sym_col = name_col = None
            for c in t.columns:
                cl = str(c).lower()
                if sym_col is None and ("symbol" in cl or "ticker" in cl):
                    sym_col = c
                if name_col is None and ("company" in cl or "name" in cl):
                    name_col = c
            if sym_col is None:
                sym_col = t.columns[0]
            rows = []
            for _, row in t.iterrows():
                sym = str(row.get(sym_col, "")).strip().upper().replace(".", "-")
                if not sym or sym == "NAN":
                    continue
                name = str(row.get(name_col, "")).strip() if name_col is not None else ""
                if name == "nan":
                    name = ""
                rows.append({"symbol": _to_yahoo_symbol(sym), "name": name, "source_symbol": sym})
            if rows:
                return rows
    raise RuntimeError("Could not find TSX Composite constituents table")


def fetch_tsx_directory_symbols(limit: int = 0, timeout: int = 20) -> list[dict[str, str]]:
    # Broader universe; can include trusts/ETFs/structured products. limit<=0 means no limit.
    url = "https://www.tsx.com/json/company-directory/search/tsx/^*"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tech-watch-tray/0.1"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json() or {}
    rows = []
    for item in data.get("results", []):
        sym = str(item.get("symbol") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        if not sym or not all(c.isalnum() or c in ".-" for c in sym):
            continue
        rows.append({"symbol": _to_yahoo_symbol(sym), "name": name, "source_symbol": sym})
        if limit and limit > 0 and len(rows) >= limit:
            break
    return rows


def get_universe(mode: str = "tsx_composite", limit: int = 0) -> list[dict[str, str]]:
    mode = (mode or "tsx_composite").strip().lower()
    if mode in ("tsx", "tsx_all", "tsx_directory"):
        return fetch_tsx_directory_symbols(limit=limit)
    rows = fetch_tsx_composite_symbols()
    return rows if not limit or limit <= 0 else rows[: max(1, limit)]


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=max(2, n // 2)).mean()


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=max(2, n // 2)).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ma_down = down.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = ma_up / ma_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast = _ema(close, 12)
    slow = _ema(close, 26)
    line = fast - slow
    signal = _ema(line, 9)
    hist = line - signal
    return line, signal, hist


def analyze_history(symbol: str, name: str, df: pd.DataFrame) -> ScreenHit | None:
    if df is None or df.empty or "Close" not in df:
        return None
    d = df.dropna(subset=["Close"]).copy()
    if len(d) < 60:
        return None
    close = d["Close"].astype(float)
    vol = d["Volume"].astype(float) if "Volume" in d else pd.Series(np.nan, index=d.index)
    price = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else price
    change_pct = ((price / prev) - 1) * 100 if prev else None

    sma20 = _sma(close, 20)
    sma50 = _sma(close, 50)
    sma200 = _sma(close, 200)
    rsi = _rsi(close, 14)
    macd_line, macd_sig, macd_hist = _macd(close)
    vol20 = _sma(vol.fillna(0), 20)

    c, s20, s50, s200 = close.iloc[-1], sma20.iloc[-1], sma50.iloc[-1], sma200.iloc[-1]
    r = rsi.iloc[-1]
    mh, mh1 = macd_hist.iloc[-1], macd_hist.iloc[-2] if len(macd_hist) > 1 else np.nan
    ml, ms = macd_line.iloc[-1], macd_sig.iloc[-1]
    ml1, ms1 = (macd_line.iloc[-2], macd_sig.iloc[-2]) if len(macd_line) > 1 else (np.nan, np.nan)
    v, v20 = vol.iloc[-1], vol20.iloc[-1]
    hi20 = close.tail(20).max()

    score = 0
    reasons: list[str] = []

    if pd.notna(s50) and pd.notna(s200) and c > s50 > s200:
        score += 25
        reasons.append("Price above rising long-term trend (close > SMA50 > SMA200)")
    elif pd.notna(s50) and c > s50:
        score += 12
        reasons.append("Price above SMA50")

    if pd.notna(s20) and pd.notna(s50) and s20 > s50 and close.iloc[-5:].mean() > s20.iloc[-5]:
        score += 10
        reasons.append("Short-term momentum above medium trend (SMA20 > SMA50)")

    if pd.notna(ml) and pd.notna(ms) and pd.notna(ml1) and pd.notna(ms1) and ml1 <= ms1 and ml > ms:
        score += 20
        reasons.append("MACD bullish crossover")
    elif pd.notna(mh) and pd.notna(mh1) and mh1 < 0 < mh:
        score += 18
        reasons.append("MACD histogram crossed above zero")
    elif pd.notna(mh) and pd.notna(mh1) and mh > mh1 and mh < 0 and abs(mh) < abs(mh1) * 0.7:
        score += 8
        reasons.append("Bearish MACD momentum fading")

    if pd.notna(r):
        if 45 <= r <= 62 and (pd.isna(rsi.iloc[-2]) or r >= rsi.iloc[-2]):
            score += 12
            reasons.append(f"RSI constructive ({r:.1f}), not overbought")
        elif 30 <= r < 45 and (pd.isna(rsi.iloc[-2]) or r > rsi.iloc[-2]):
            score += 10
            reasons.append(f"RSI recovering from weakness ({r:.1f})")
        elif r > 75:
            score -= 12
            reasons.append(f"RSI overbought ({r:.1f})")
        elif r < 30:
            score -= 4
            reasons.append(f"RSI oversold ({r:.1f}) - possible bounce, higher risk")

    if pd.notna(v) and pd.notna(v20) and v20 > 0 and v >= v20 * 1.5 and change_pct is not None and change_pct > 0:
        score += 12
        reasons.append("Above-average volume on up move")
    elif pd.notna(v) and pd.notna(v20) and v20 > 0 and v >= v20 * 1.2 and change_pct is not None and change_pct > 1.5:
        score += 8
        reasons.append("Volume confirming upside")

    if pd.notna(hi20) and price >= hi20 * 0.995 and price > prev:
        score += 10
        reasons.append("Breaking/pushing 20-day high")

    # Penalize clear downtrends / breakdowns
    if pd.notna(s50) and pd.notna(s200) and c < s50 < s200:
        score -= 18
        reasons.append("Downtrend (close < SMA50 < SMA200)")
    if pd.notna(r) and r < 35 and change_pct is not None and change_pct < -2:
        score -= 8
        reasons.append("Weak momentum with negative price action")

    dividend_yield_pct = None
    try:
        if "Dividends" in d.columns:
            div = d["Dividends"].astype(float)
            paid = div[div > 0]
            if not paid.empty and price:
                ttm = float(paid.tail(4).sum())
                if ttm > 0:
                    dividend_yield_pct = round((ttm / price) * 100, 2)
    except Exception:
        dividend_yield_pct = None

    score = max(0, min(100, score))
    verdict = "SCREAMING BUY" if score >= 65 else "STRONG" if score >= 55 else "WATCH" if score >= 40 else "NEUTRAL"
    metrics = {
        "close": round(price, 4),
        "rsi14": None if pd.isna(r) else round(float(r), 2),
        "sma20": None if pd.isna(s20) else round(float(s20), 4),
        "sma50": None if pd.isna(s50) else round(float(s50), 4),
        "sma200": None if pd.isna(s200) else round(float(s200), 4),
        "macd_hist": None if pd.isna(mh) else round(float(mh), 6),
        "volume": None if pd.isna(v) else float(v),
        "volume20": None if pd.isna(v20) else float(v20),
        "dividend_yield_pct": dividend_yield_pct,
    }
    return ScreenHit(symbol, name or symbol, score, verdict, round(price, 4), None if change_pct is None else round(change_pct, 2), dividend_yield_pct, reasons, metrics, _now_iso())


def screen_symbols(
    symbols: list[dict[str, str]],
    min_score: int = 55,
    batch_size: int = 40,
    period: str = "10mo",
    progress_cb=None,
) -> list[ScreenHit]:
    if yf is None:
        raise RuntimeError("yfinance is required for TSX screening")
    out: list[ScreenHit] = []
    total = len(symbols)
    done = 0
    for batch in _chunks(symbols, max(1, batch_size)):
        tickers = " ".join(s["symbol"] for s in batch)
        try:
            data = yf.download(tickers, period=period, interval="1d", group_by="ticker", auto_adjust=False, progress=False, threads=True, actions=True)
        except Exception:
            data = pd.DataFrame()
        for meta in batch:
            sym = meta["symbol"]
            name = meta.get("name") or ""
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if sym in data.columns.get_level_values(0):
                        df = data[sym].copy()
                    else:
                        df = pd.DataFrame()
                else:
                    df = data.copy() if len(batch) == 1 else pd.DataFrame()
                hit = analyze_history(sym, name, df)
                if hit and hit.score >= min_score:
                    out.append(hit)
            except Exception:
                pass
            done += 1
            if progress_cb and (done % 10 == 0 or done == total):
                try:
                    progress_cb(done, total, sym)
                except Exception:
                    pass
        time.sleep(0.2)
    out.sort(key=lambda h: (h.score, h.change_pct or 0), reverse=True)
    return out
