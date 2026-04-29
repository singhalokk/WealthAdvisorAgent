"""
Claude AI-powered portfolio advisor.
Sends technical + fundamental data to Claude and returns structured JSON advice.
Uses prompt caching on the system prompt to reduce cost on repeated calls.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Optional

import anthropic
import pandas as pd

from market_data import StockAnalysis

_client = anthropic.Anthropic()

_SYSTEM_PROMPT = """You are an expert financial advisor and quantitative portfolio analyst with deep expertise in:
- Technical analysis: moving averages (50/200-day), RSI, MACD, momentum, cross signals
- Fundamental analysis: P/E ratios, market cap, sector dynamics, dividend yield, beta
- Portfolio construction: diversification, risk management, sector allocation
- ETF and stock alternatives across growth, value, dividend, and index strategies

You receive real market data and computed technical indicators and produce clear, data-driven recommendations.

RESPONSE FORMAT: Always return a single valid JSON object — no markdown fences, no prose outside JSON.

DISCLAIMER: Always include in your response that these recommendations are for educational purposes only and users should consult a licensed financial advisor before making actual investment decisions."""

_ADVICE_SCHEMA = """{
  "investing_style": {
    "primary_style": "<growth|value|dividend|index|balanced|aggressive|conservative>",
    "description": "<2-sentence description of the user's style inferred from their holdings>",
    "risk_level": "<low|moderate|high|aggressive>"
  },
  "portfolio_summary": {
    "total_holdings": <number>,
    "sectors": ["<sector1>", "..."],
    "strengths": ["<strength1>", "..."],
    "weaknesses": ["<weakness1>", "..."],
    "diversification_score": "<1-10>",
    "overall_health": "<excellent|good|fair|poor>"
  },
  "stock_recommendations": [
    {
      "ticker": "<SYMBOL>",
      "company_name": "<name>",
      "action": "<BUY|SELL|HOLD>",
      "confidence": "<high|medium|low>",
      "reasoning": "<2-3 sentences grounded in the technical data>",
      "key_signals": ["<signal1>", "<signal2>"],
      "price_target": "<short-term price target or range, or 'N/A'>",
      "risk_factors": ["<risk1>", "<risk2>"]
    }
  ],
  "alternatives": [
    {
      "replaces": "<ORIGINAL_TICKER>",
      "alternative_ticker": "<NEW_TICKER>",
      "alternative_name": "<name>",
      "reason": "<why this alternative is worth considering>",
      "type": "<same_sector|better_momentum|lower_risk|higher_growth|etf_equivalent>"
    }
  ],
  "top_actions": [
    {
      "priority": <1-5>,
      "action": "<specific action>",
      "rationale": "<why this should be done first>"
    }
  ],
  "day_trading_candidates": [
    {
      "ticker": "<SYMBOL>",
      "direction": "<long|short>",
      "entry_zone": "<price range to enter>",
      "exit_target": "<price target>",
      "stop_loss": "<stop loss level>",
      "rationale": "<reason based on technical setup>"
    }
  ],
  "market_context": "<2-3 sentences on current market environment relevant to this portfolio>",
  "disclaimer": "<educational disclaimer text>"
}"""


def _to_json_safe(obj):
    """Recursively convert numpy/pandas scalars to plain Python types."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if math.isnan(obj) else float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _serialize_stock(analysis: StockAnalysis, portfolio_row: Optional[pd.Series]) -> dict:
    d = _to_json_safe(asdict(analysis))
    d.pop('error', None)

    if portfolio_row is not None:
        position = {}
        for col in ('shares', 'current_value', 'cost_basis', 'gain_loss'):
            val = portfolio_row.get(col)
            if val is not None and not pd.isna(val):
                position[col] = _to_json_safe(val)
        if position:
            d['portfolio_position'] = position

    return d


def get_portfolio_advice(
    analyses: dict[str, StockAnalysis],
    portfolio_df: pd.DataFrame,
) -> dict:
    """
    Send all stock analyses to Claude and return structured JSON advice.
    Raises on JSON parse failure — caller should handle.
    """
    stocks_data = []
    for ticker, analysis in analyses.items():
        if analysis.error:
            continue
        row = None
        matches = portfolio_df[portfolio_df['ticker'] == ticker]
        if not matches.empty:
            row = matches.iloc[0]
        stocks_data.append(_serialize_stock(analysis, row))

    if not stocks_data:
        raise ValueError("No valid stock data to analyze")

    user_prompt = f"""Analyze the following investment portfolio and return advice matching this JSON schema exactly:

{_ADVICE_SCHEMA}

Portfolio technical and fundamental data:
{json.dumps(stocks_data, indent=2)}

Rules:
- Base every recommendation strictly on the provided data (trend, RSI, MACD, momentum, MAs)
- For day_trading_candidates, only include stocks from the portfolio that have clear intraday setups visible in the technical data; use empty list [] if none qualify
- For alternatives, suggest 1-2 per holding where genuinely beneficial — include at least one ETF alternative if applicable
- Keep reasoning concise and data-driven
- Return ONLY the JSON object, nothing else"""

    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8096,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip any accidental markdown fences Claude may add
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)
