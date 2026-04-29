"""
Market data fetching and technical analysis.
Uses Yahoo Finance via curl_cffi (browser impersonation + crumb auth) to fetch
1-year daily price history and company fundamentals.
Computes 50/200-day MAs, RSI, MACD, and momentum signals per stock.
"""

from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd
from curl_cffi import requests as cffi_requests

warnings.filterwarnings("ignore")

_YF1 = "https://query1.finance.yahoo.com"
_YF2 = "https://query2.finance.yahoo.com"


# ── Authenticated Yahoo Finance session ───────────────────────────────────────

class _YahooSession:
    """
    Wraps a curl_cffi session with cookie + crumb authentication.
    Shared across all concurrent ticker fetches to avoid redundant auth calls.
    """

    def __init__(self) -> None:
        self._session = cffi_requests.Session(impersonate="chrome")
        self._crumb: Optional[str] = None
        self._authenticate()

    def _authenticate(self) -> None:
        self._session.get("https://finance.yahoo.com/", timeout=10)
        resp = self._session.get(f"{_YF1}/v1/test/getcrumb", timeout=10)
        if resp.status_code == 200 and resp.text:
            self._crumb = resp.text.strip()

    def get(self, url: str, params: Optional[dict] = None, **kwargs) -> cffi_requests.Response:
        p = dict(params or {})
        if self._crumb:
            p["crumb"] = self._crumb
        return self._session.get(url, params=p, **kwargs)


# Module-level session singleton — built once, reused for all tickers.
_session: Optional[_YahooSession] = None


def _get_session() -> _YahooSession:
    global _session
    if _session is None:
        _session = _YahooSession()
    return _session


# ── Yahoo Finance data helpers ────────────────────────────────────────────────

def _fetch_history(ticker: str) -> pd.Series:
    """Return a daily Close price Series for the past year."""
    sess = _get_session()
    url = f"{_YF1}/v8/finance/chart/{ticker}"
    resp = sess.get(url, params={"interval": "1d", "range": "1y"}, timeout=25)
    resp.raise_for_status()
    data = resp.json()

    result = data.get("chart", {}).get("result")
    if not result:
        err = data.get("chart", {}).get("error", {})
        raise ValueError(f"No chart data: {err}")

    r = result[0]
    timestamps = r.get("timestamp", [])
    closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])

    if not timestamps or not closes:
        raise ValueError("Empty price series")

    series = pd.Series(dict(zip(timestamps, closes)), dtype=float)
    series.index = pd.to_datetime(series.index, unit="s")
    series.name = "close"
    return series.dropna()


def _fetch_fundamentals(ticker: str) -> dict:
    """Return merged quoteSummary modules dict (empty dict on failure)."""
    sess = _get_session()
    url = f"{_YF2}/v10/finance/quoteSummary/{ticker}"
    modules = "assetProfile,summaryDetail,defaultKeyStatistics,quoteType"
    try:
        resp = sess.get(url, params={"modules": modules, "formatted": "false"}, timeout=25)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        result = data.get("quoteSummary", {}).get("result")
        if not result:
            return {}
        merged: dict = {}
        for mod in result:
            merged.update(mod)
        return merged
    except Exception:
        return {}


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class StockAnalysis:
    ticker: str
    company_name: str
    current_price: float
    sector: str
    industry: str

    ma_50: float
    ma_200: float
    price_vs_ma50_pct: float
    price_vs_ma200_pct: float
    ma50_vs_ma200_pct: float

    rsi_14: float
    macd: float
    macd_signal: float
    momentum_1m_pct: float
    momentum_3m_pct: float
    momentum_6m_pct: float

    trend: str    # strong_uptrend | uptrend | sideways | downtrend | strong_downtrend
    signal: str   # buy | sell | hold
    golden_cross: bool
    death_cross: bool

    market_cap: Optional[float]
    pe_ratio: Optional[float]
    dividend_yield_pct: Optional[float]
    beta: Optional[float]

    error: Optional[str] = field(default=None)


# ── Technical indicator helpers ───────────────────────────────────────────────

def _rsi(prices: pd.Series, period: int = 14) -> float:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _macd(prices: pd.Series) -> tuple:
    ema12 = prices.ewm(span=12, adjust=False).mean()
    ema26 = prices.ewm(span=26, adjust=False).mean()
    line = ema12 - ema26
    signal = line.ewm(span=9, adjust=False).mean()
    return float(line.iloc[-1]), float(signal.iloc[-1])


def _momentum(prices: pd.Series, trading_days: int) -> float:
    if len(prices) <= trading_days:
        return 0.0
    old = prices.iloc[-(trading_days + 1)]
    return float((prices.iloc[-1] - old) / old * 100) if old else 0.0


def _determine_trend(vs_ma50: float, vs_ma200: float, ma50_vs_ma200: float) -> str:
    if vs_ma200 > 5 and ma50_vs_ma200 > 2:
        return "strong_uptrend"
    if vs_ma200 > 0 and ma50_vs_ma200 > 0:
        return "uptrend"
    if vs_ma200 < -5 and ma50_vs_ma200 < -2:
        return "strong_downtrend"
    if vs_ma200 < 0 and ma50_vs_ma200 < 0:
        return "downtrend"
    return "sideways"


def _generate_signal(trend: str, rsi: float, macd: float, macd_sig: float) -> str:
    bullish = macd > macd_sig
    if trend in ("strong_uptrend", "uptrend") and rsi < 70 and bullish:
        return "buy"
    if trend in ("strong_downtrend", "downtrend") and not bullish:
        return "sell"
    if trend == "strong_downtrend":
        return "sell"
    return "hold"


# ── Single stock analysis ─────────────────────────────────────────────────────

def _error_result(ticker: str, msg: str) -> StockAnalysis:
    return StockAnalysis(
        ticker=ticker, company_name=ticker, current_price=0,
        sector="Unknown", industry="Unknown",
        ma_50=0, ma_200=0, price_vs_ma50_pct=0, price_vs_ma200_pct=0,
        ma50_vs_ma200_pct=0, rsi_14=50, macd=0, macd_signal=0,
        momentum_1m_pct=0, momentum_3m_pct=0, momentum_6m_pct=0,
        trend="sideways", signal="hold",
        golden_cross=False, death_cross=False,
        market_cap=None, pe_ratio=None, dividend_yield_pct=None, beta=None,
        error=msg,
    )


def analyze_stock(ticker: str) -> StockAnalysis:
    try:
        close = _fetch_history(ticker)

        if len(close) < 30:
            return _error_result(ticker, f"Only {len(close)} days of price history available")

        n = len(close)
        current_price = float(close.iloc[-1])

        ma_50  = float(close.rolling(min(50, n)).mean().iloc[-1])
        ma_200 = float(close.rolling(min(200, n)).mean().iloc[-1])

        def pct(curr: float, ref: float) -> float:
            return (curr - ref) / ref * 100 if ref else 0.0

        vs_ma50       = pct(current_price, ma_50)
        vs_ma200      = pct(current_price, ma_200)
        ma50_vs_ma200 = pct(ma_50, ma_200)

        rsi              = _rsi(close)
        macd_val, macd_s = _macd(close)

        prev_ma50  = float(close.rolling(min(50, n)).mean().iloc[-2]) if n >= 2 else ma_50
        prev_ma200 = float(close.rolling(min(200, n)).mean().iloc[-2]) if n >= 2 else ma_200
        golden_cross = bool(ma_50 > ma_200 and prev_ma50 <= prev_ma200)
        death_cross  = bool(ma_50 < ma_200 and prev_ma50 >= prev_ma200)

        trend  = _determine_trend(vs_ma50, vs_ma200, ma50_vs_ma200)
        signal = _generate_signal(trend, rsi, macd_val, macd_s)

        info       = _fetch_fundamentals(ticker)
        profile    = info.get("assetProfile", {})
        summary    = info.get("summaryDetail", {})
        key_stats  = info.get("defaultKeyStatistics", {})
        quote_type = info.get("quoteType", {})

        company_name = (
            quote_type.get("longName")
            or quote_type.get("shortName")
            or profile.get("longName")
            or ticker
        )
        sector   = profile.get("sector", "Unknown")
        industry = profile.get("industry", "Unknown")

        market_cap = summary.get("marketCap") or key_stats.get("marketCap")
        pe_ratio   = summary.get("trailingPE")
        div_yield  = summary.get("dividendYield")
        beta       = summary.get("beta")

        return StockAnalysis(
            ticker=ticker,
            company_name=company_name,
            current_price=round(current_price, 2),
            sector=sector,
            industry=industry,
            ma_50=round(ma_50, 2),
            ma_200=round(ma_200, 2),
            price_vs_ma50_pct=round(vs_ma50, 2),
            price_vs_ma200_pct=round(vs_ma200, 2),
            ma50_vs_ma200_pct=round(ma50_vs_ma200, 2),
            rsi_14=round(rsi, 1),
            macd=round(macd_val, 4),
            macd_signal=round(macd_s, 4),
            momentum_1m_pct=round(_momentum(close, 21), 2),
            momentum_3m_pct=round(_momentum(close, 63), 2),
            momentum_6m_pct=round(_momentum(close, 126), 2),
            trend=trend,
            signal=signal,
            golden_cross=golden_cross,
            death_cross=death_cross,
            market_cap=int(market_cap) if market_cap else None,
            pe_ratio=round(float(pe_ratio), 2) if pe_ratio else None,
            dividend_yield_pct=round(float(div_yield) * 100, 2) if div_yield else None,
            beta=round(float(beta), 2) if beta else None,
        )

    except Exception as exc:
        return _error_result(ticker, str(exc))


# ── Portfolio-level batch analysis ────────────────────────────────────────────

def analyze_portfolio(
    tickers: list,
    on_progress: Optional[Callable] = None,
    max_workers: int = 4,
) -> dict:
    """Analyze all tickers concurrently; calls on_progress(ticker) after each."""
    # Warm the shared session before spawning threads
    _get_session()

    results: dict = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(analyze_stock, t): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results[ticker] = future.result()
            except Exception as exc:
                results[ticker] = _error_result(ticker, str(exc))
            if on_progress:
                on_progress(ticker)
    return results
