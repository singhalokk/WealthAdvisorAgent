"""
Tool implementations for the WealthAdvisor agent.
Each function is called by the agent when Claude decides to use a tool.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from market_data import _get_session, _rsi, _macd, analyze_stock
from portfolio_reader import read_portfolio

# ── Helpers ───────────────────────────────────────────────────────────────────

def _numpy_safe(obj):
    if isinstance(obj, dict):  return {k: _numpy_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):  return [_numpy_safe(v) for v in obj]
    if isinstance(obj, np.integer): return int(obj)
    if isinstance(obj, np.floating): return None if math.isnan(float(obj)) else float(obj)
    if isinstance(obj, np.bool_): return bool(obj)
    return obj


def _fetch_intraday(ticker: str, interval: str = "5m", range_: str = "1d") -> list[dict]:
    """Fetch intraday OHLCV candles from Yahoo Finance."""
    sess = _get_session()
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    resp = sess.get(url, params={"interval": interval, "range": range_}, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    result = data.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"No intraday data for {ticker}")

    r = result[0]
    timestamps = r.get("timestamp", [])
    q = r.get("indicators", {}).get("quote", [{}])[0]
    opens   = q.get("open",   [])
    highs   = q.get("high",   [])
    lows    = q.get("low",    [])
    closes  = q.get("close",  [])
    volumes = q.get("volume", [])

    candles = []
    for i, ts in enumerate(timestamps):
        if i < len(closes) and closes[i] is not None:
            candles.append({
                "time":   ts,
                "open":   round(opens[i],   2) if opens[i]   else None,
                "high":   round(highs[i],   2) if highs[i]   else None,
                "low":    round(lows[i],    2) if lows[i]    else None,
                "close":  round(closes[i],  2),
                "volume": int(volumes[i])       if volumes[i] else 0,
            })
    return candles


def _detect_candle_pattern(candles: list[dict]) -> str:
    if len(candles) < 2:
        return "Not enough candles"

    c = candles[-1]
    p = candles[-2]
    body   = abs(c["close"] - c["open"])
    rng    = c["high"] - c["low"]
    p_body = abs(p["close"] - p["open"])

    if rng == 0:
        return "Flat candle"
    if body < rng * 0.1:
        return "Doji — indecision, watch for breakout"
    if c["close"] > c["open"] and (c["open"] - c["low"]) > body * 2:
        return "Hammer — bullish reversal signal"
    if c["close"] < c["open"] and (c["high"] - c["open"]) > body * 2:
        return "Shooting Star — bearish reversal signal"
    if (c["close"] > c["open"] and p["close"] < p["open"]
            and c["open"] <= p["close"] and c["close"] >= p["open"]):
        return "Bullish Engulfing — strong buy signal"
    if (c["close"] < c["open"] and p["close"] > p["open"]
            and c["open"] >= p["close"] and c["close"] <= p["open"]):
        return "Bearish Engulfing — strong sell signal"
    if c["close"] > c["open"]:
        return "Bullish candle"
    return "Bearish candle"


# ── Tool functions ────────────────────────────────────────────────────────────

def tool_get_stock_analysis(ticker: str) -> dict:
    """Full technical + fundamental analysis for a stock."""
    a = analyze_stock(ticker.upper())
    if a.error:
        return {"error": a.error}
    return _numpy_safe(asdict(a))


def tool_get_current_price(ticker: str) -> dict:
    """Get current price and basic info for a ticker."""
    sess = _get_session()
    url  = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}"
    resp = sess.get(url, params={"interval": "1m", "range": "1d"}, timeout=15)
    data = resp.json()
    result = data.get("chart", {}).get("result")
    if not result:
        return {"error": f"No data for {ticker}"}
    r = result[0]
    closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    closes = [c for c in closes if c is not None]
    if not closes:
        return {"error": "No price data"}
    meta = r.get("meta", {})
    return {
        "ticker":         ticker.upper(),
        "current_price":  round(closes[-1], 2),
        "previous_close": round(meta.get("previousClose", 0), 2),
        "change_pct":     round((closes[-1] - meta.get("previousClose", closes[-1]))
                                / meta.get("previousClose", closes[-1]) * 100, 2)
                          if meta.get("previousClose") else 0,
        "currency":       meta.get("currency", "USD"),
        "exchange":       meta.get("exchangeName", ""),
    }


def tool_analyze_day_trading(ticker: str, interval: str = "5m") -> dict:
    """
    Analyze intraday candles for a day trading setup.
    Returns technical levels, pattern, entry/exit suggestion, and candle data for charting.
    """
    ticker = ticker.upper()
    valid_intervals = ["1m", "2m", "5m", "15m", "30m"]
    if interval not in valid_intervals:
        interval = "5m"

    candles = _fetch_intraday(ticker, interval=interval, range_="1d")
    if len(candles) < 5:
        return {"error": f"Not enough intraday data for {ticker}"}

    closes  = pd.Series([c["close"]  for c in candles])
    highs   = [c["high"]   for c in candles]
    lows    = [c["low"]    for c in candles]
    volumes = [c["volume"] for c in candles]

    # VWAP
    typical = [(candles[i]["high"] + candles[i]["low"] + candles[i]["close"]) / 3
               for i in range(len(candles))]
    total_v  = sum(volumes)
    vwap = sum(t * v for t, v in zip(typical, volumes)) / total_v if total_v else closes.iloc[-1]

    # Support / resistance from last 20 candles
    recent = candles[-20:]
    support    = min(c["low"]  for c in recent)
    resistance = max(c["high"] for c in recent)

    current_price = float(closes.iloc[-1])
    rsi = float(_rsi(closes))
    pattern = _detect_candle_pattern(candles)

    # Entry / exit logic
    if current_price < vwap and rsi < 45:
        direction = "LONG"
        entry_low  = round(current_price * 0.999, 2)
        entry_high = round(current_price * 1.001, 2)
        exit_target = round(min(vwap, resistance), 2)
        stop_loss   = round(support * 0.998, 2)
    elif current_price > vwap and rsi > 55:
        direction = "SHORT"
        entry_low  = round(current_price * 0.999, 2)
        entry_high = round(current_price * 1.001, 2)
        exit_target = round(max(vwap, support), 2)
        stop_loss   = round(resistance * 1.002, 2)
    else:
        direction   = "WAIT"
        entry_low   = None
        entry_high  = None
        exit_target = None
        stop_loss   = None

    risk_reward = None
    if direction != "WAIT" and entry_high and exit_target and stop_loss:
        reward = abs(exit_target - entry_high)
        risk   = abs(entry_high - stop_loss)
        risk_reward = round(reward / risk, 2) if risk else None

    return {
        "ticker":        ticker,
        "interval":      interval,
        "current_price": round(current_price, 2),
        "vwap":          round(vwap, 2),
        "support":       round(support, 2),
        "resistance":    round(resistance, 2),
        "rsi":           round(rsi, 1),
        "candle_pattern":pattern,
        "direction":     direction,
        "entry_zone":    f"${entry_low} – ${entry_high}" if entry_low else "Wait for better setup",
        "exit_target":   f"${exit_target}" if exit_target else None,
        "stop_loss":     f"${stop_loss}"   if stop_loss   else None,
        "risk_reward":   f"1:{risk_reward}" if risk_reward else None,
        "candles":       candles[-60:],   # last 60 candles for chart
    }


def tool_get_portfolio(user_id: int) -> dict:
    """Load the user's active portfolio file."""
    base = Path("user_data") / str(user_id)
    active_file = base / ".active_portfolio"
    if active_file.exists():
        pid = active_file.read_text().strip()
        portfolio_path = base / "portfolios" / f"{pid}.csv"
    else:
        portfolio_path = base / "portfolio.csv"   # legacy fallback
    if not portfolio_path.exists():
        return {"error": "No portfolio saved. Please upload a portfolio file first."}
    try:
        df = read_portfolio(str(portfolio_path))
        return {
            "tickers":  df["ticker"].tolist(),
            "holdings": df.to_dict(orient="records"),
        }
    except Exception as e:
        return {"error": str(e)}


def tool_analyze_portfolio(user_id: int) -> dict:
    """Run full portfolio analysis for the user's saved portfolio."""
    from market_data import analyze_portfolio as _analyze_portfolio
    from advisor import get_portfolio_advice

    portfolio_path = Path("user_data") / str(user_id) / "portfolio.csv"
    if not portfolio_path.exists():
        return {"error": "No portfolio found. Please upload a portfolio file first."}

    portfolio_df = read_portfolio(str(portfolio_path))
    tickers  = portfolio_df["ticker"].tolist()
    analyses = _analyze_portfolio(tickers)
    advice   = get_portfolio_advice(analyses, portfolio_df)

    # Summary for agent context (full data saved to file)
    return {
        "investing_style":    advice.get("investing_style", {}),
        "portfolio_summary":  advice.get("portfolio_summary", {}),
        "recommendations":    advice.get("stock_recommendations", []),
        "top_actions":        advice.get("top_actions", []),
        "alternatives":       advice.get("alternatives", []),
        "market_context":     advice.get("market_context", ""),
        "day_trading_candidates": advice.get("day_trading_candidates", []),
    }


def tool_search_stock(query: str) -> dict:
    """Search for a stock ticker by company name or partial ticker."""
    sess = _get_session()
    url  = "https://query1.finance.yahoo.com/v1/finance/search"
    resp = sess.get(url, params={"q": query, "quotesCount": 6, "newsCount": 0}, timeout=10)
    data = resp.json()
    quotes = data.get("quotes", [])
    results = [
        {"ticker": q.get("symbol"), "name": q.get("longname") or q.get("shortname"),
         "type": q.get("quoteType"), "exchange": q.get("exchange")}
        for q in quotes if q.get("symbol")
    ]
    return {"results": results}


# ── Tool schemas for Claude ───────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "get_stock_analysis",
        "description": "Get full technical and fundamental analysis for a stock or ETF ticker. "
                       "Includes 50/200-day MA, RSI, MACD, momentum, trend, and buy/sell/hold signal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol e.g. AAPL, MSFT, SPY"}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_current_price",
        "description": "Get the current live price, previous close, and daily change for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol"}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "analyze_day_trading",
        "description": "Analyze intraday candlestick data for a day trading setup. "
                       "Returns VWAP, support/resistance, candle pattern, entry zone, exit target, "
                       "stop loss, and risk/reward ratio. Also returns candle data for chart rendering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker":   {"type": "string", "description": "Stock ticker symbol"},
                "interval": {"type": "string", "description": "Candle interval: 1m, 2m, 5m, 15m, 30m. Default is 5m.",
                             "enum": ["1m", "2m", "5m", "15m", "30m"]}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_portfolio",
        "description": "Retrieve the user's saved portfolio — list of tickers and position details.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "analyze_portfolio",
        "description": "Run a complete AI-powered analysis on the user's entire portfolio. "
                       "Returns investing style, buy/sell/hold per stock, alternatives, and top actions. "
                       "This takes 30-60 seconds to run.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "search_stock",
        "description": "Search for a stock by company name or partial ticker to find the correct symbol.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Company name or partial ticker to search"}
            },
            "required": ["query"]
        }
    },
]


# ── Dispatcher ────────────────────────────────────────────────────────────────

def execute_tool(name: str, inputs: dict, user_id: int) -> dict:
    """Route a tool call from the agent to the right function."""
    try:
        if name == "get_stock_analysis":
            return tool_get_stock_analysis(inputs["ticker"])
        if name == "get_current_price":
            return tool_get_current_price(inputs["ticker"])
        if name == "analyze_day_trading":
            return tool_analyze_day_trading(
                inputs["ticker"], inputs.get("interval", "5m")
            )
        if name == "get_portfolio":
            return tool_get_portfolio(user_id)
        if name == "analyze_portfolio":
            return tool_analyze_portfolio(user_id)
        if name == "search_stock":
            return tool_search_stock(inputs["query"])
        return {"error": f"Unknown tool: {name}"}
    except Exception as e:
        return {"error": str(e)}
