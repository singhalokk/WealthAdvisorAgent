"""
Portfolio file reader — supports CSV and plain text formats.
Flexible column detection handles various brokerage export formats.
"""

from __future__ import annotations

import re
import pandas as pd
from pathlib import Path

_TICKER_COLS = ['ticker', 'symbol', 'stock', 'etf', 'security', 'name']
_SHARES_COLS = ['shares', 'quantity', 'qty', 'units', 'amount', 'position']
_VALUE_COLS  = ['value', 'current_value', 'market_value', 'total_value', 'mkt value', 'current value']
_COST_COLS   = ['cost', 'cost_basis', 'invested', 'purchase_price', 'avg cost', 'average cost', 'basis']
_GAIN_COLS   = ['gain', 'loss', 'gain_loss', 'gain/loss', 'profit_loss', 'profit/loss', 'pnl', 'return', 'unrealized']


def _normalize_name(s: str) -> str:
    return s.lower().strip().replace(' ', '_').replace('/', '_')


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {_normalize_name(c): c for c in df.columns}
    for name in candidates:
        if _normalize_name(name) in lower_map:
            return lower_map[_normalize_name(name)]
    return None


def _clean_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(r'[$,%()]', '', regex=True).str.strip(),
        errors='coerce'
    )


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame()

    ticker_col = _find_col(df, _TICKER_COLS) or df.columns[0]
    result['ticker'] = df[ticker_col].astype(str).str.upper().str.strip()

    for out_col, candidates in [
        ('shares',        _SHARES_COLS),
        ('current_value', _VALUE_COLS),
        ('cost_basis',    _COST_COLS),
        ('gain_loss',     _GAIN_COLS),
    ]:
        src = _find_col(df, candidates)
        result[out_col] = _clean_numeric(df[src]) if src else pd.NA

    # Drop rows with empty/invalid tickers
    result = result[result['ticker'].str.match(r'^[A-Z][A-Z0-9.\-]{0,9}$')]
    return result.reset_index(drop=True)


def _parse_plain_text(path: str) -> pd.DataFrame:
    """One ticker per line, optionally followed by shares/value data."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            ticker_match = re.match(r'^([A-Z][A-Z0-9.\-]{0,9})$', parts[0].upper())
            if not ticker_match:
                continue
            ticker = ticker_match.group(1)
            shares = None
            if len(parts) > 1:
                try:
                    shares = float(parts[1])
                except ValueError:
                    pass
            rows.append({'ticker': ticker, 'shares': shares,
                         'current_value': pd.NA, 'cost_basis': pd.NA, 'gain_loss': pd.NA})
    return pd.DataFrame(rows)


def read_portfolio(file_path: str) -> pd.DataFrame:
    """
    Read a portfolio from a CSV or plain-text file.

    Returns a DataFrame with columns:
        ticker, shares, current_value, cost_basis, gain_loss
    All columns except ticker may be NA if not provided.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Portfolio file not found: {file_path}")

    # Try CSV / delimited formats first
    for sep in [',', '\t', ';', r'\s+']:
        try:
            df = pd.read_csv(file_path, sep=sep, engine='python', skip_blank_lines=True)
            if df.shape[1] >= 1 and df.shape[0] >= 1:
                normalized = _normalize(df)
                if len(normalized) > 0:
                    return normalized
        except Exception:
            continue

    # Fall back to plain-text (one ticker per line)
    result = _parse_plain_text(file_path)
    if len(result) == 0:
        raise ValueError(f"Could not parse any holdings from {file_path}")
    return result
