"""
backend/agents/step1d_technical_agent.py

Technical Agent — computes IBD-style technical market-structure metrics
from price/volume history using only yfinance.  All fields are derived
algorithmically from OHLCV data.

IMPORTANT DISCLOSURE:
  The accumulation/distribution score here is an OBV-based proxy and is
  labelled "≈ Acc/Dis, not IBD's rating" throughout.  It is NOT a
  reproduction of IBD's proprietary Acc/Dis Rating.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pydantic import BaseModel

from backend.data.market_data import get_price_volume_history

if TYPE_CHECKING:
    from backend.app import GraphState


# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------


class TechnicalResult(BaseModel):
    """
    Market-structure metrics derived from 2 years of OHLCV history.

    volume               : Most recent day's volume.
    volume_pct_change    : % above/below the 50-day average daily volume.
    pct_off_52w_high     : How far the current close is below the 52-week high
                           (0 = at the high, negative = below).
    historical_volatility: Annualised 20-day standard deviation of log returns.
    atr                  : 14-day Average True Range (absolute dollar value).
    acc_dis_score        : Cumulative OBV-proxy over the last 20 days.
                           Label: "≈ Acc/Dis, not IBD's rating".
    price_history        : [{date, value}] — last 6m daily closes, for charting.
    volume_history       : [{date, value}] — last 6m daily volumes, for charting.
    """

    ticker: str
    volume: int | None = None
    volume_pct_change: float | None = None       # % vs 50-day avg
    pct_off_52w_high: float | None = None        # % below 52-week high
    historical_volatility: float | None = None  # annualised 20-day stdev
    atr: float | None = None                    # 14-day ATR
    acc_dis_score: float | None = None          # OBV-proxy (≈ Acc/Dis, not IBD's)
    price_history: list[dict] | None = None     # [{date, value}]
    volume_history: list[dict] | None = None    # [{date, value}]


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def _col(df: pd.DataFrame, name: str) -> str | None:
    """Return the actual column name (case-insensitive match)."""
    return next((c for c in df.columns if c.lower() == name.lower()), None)


def compute_technical(ticker: str) -> TechnicalResult:
    """
    Compute all technical fields for `ticker` from 2 years of OHLCV data.
    Never raises — missing/insufficient data fields become None.
    """
    df = get_price_volume_history(ticker)

    if df is None or len(df) < 20:
        return TechnicalResult(ticker=ticker)

    # Normalise column access
    close_col = _col(df, "close")
    high_col = _col(df, "high")
    low_col = _col(df, "low")
    volume_col = _col(df, "volume")

    if close_col is None:
        return TechnicalResult(ticker=ticker)

    closes = df[close_col].dropna()
    highs = df[high_col].dropna() if high_col else None
    lows = df[low_col].dropna() if low_col else None
    volumes = df[volume_col].dropna() if volume_col else None

    # --- Volume ---
    volume: int | None = None
    volume_pct_change: float | None = None
    if volumes is not None and len(volumes) >= 50:
        volume = int(volumes.iloc[-1])
        avg_50d = float(volumes.iloc[-51:-1].mean())
        if avg_50d > 0:
            volume_pct_change = round(((volume - avg_50d) / avg_50d) * 100, 2)

    # --- % Off 52-week High ---
    pct_off_52w_high: float | None = None
    if len(closes) >= 252:
        high_52w = float(closes.iloc[-252:].max())
    else:
        high_52w = float(closes.max())
    current_price = float(closes.iloc[-1])
    if high_52w > 0:
        pct_off_52w_high = round(((current_price - high_52w) / high_52w) * 100, 2)

    # --- Historical Volatility (20-day annualised) ---
    historical_volatility: float | None = None
    if len(closes) >= 21:
        log_returns = np.log(closes / closes.shift(1)).dropna()
        stdev_20d = float(log_returns.iloc[-20:].std())
        historical_volatility = round(stdev_20d * math.sqrt(252) * 100, 2)

    # --- Average True Range (14-day) ---
    atr: float | None = None
    if highs is not None and lows is not None and len(closes) >= 15:
        prev_closes = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_closes).abs(),
            (lows - prev_closes).abs(),
        ], axis=1).max(axis=1).dropna()
        if len(tr) >= 14:
            atr = round(float(tr.iloc[-14:].mean()), 4)

    # --- OBV-proxy Acc/Dis Score (last 20 days) ---
    acc_dis_score: float | None = None
    if volumes is not None and len(closes) >= 21 and len(volumes) >= 21:
        price_change = closes.diff().iloc[-20:]
        vol_slice = volumes.iloc[-20:]
        # Positive day: add volume; negative day: subtract volume
        signed_vol = vol_slice * price_change.apply(
            lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
        )
        acc_dis_score = round(float(signed_vol.sum()), 0)

    # --- 6m Price History [{date, value}] for charting ---
    price_history: list[dict] | None = None
    volume_history: list[dict] | None = None
    try:
        six_months_ago = pd.Timestamp.now(tz="UTC") - pd.DateOffset(months=6)
        df_6m = df[df.index >= six_months_ago] if df.index.tz is not None else df.iloc[-126:]

        if close_col and not df_6m.empty:
            price_history = [
                {"date": str(idx.date()), "value": round(float(val), 4)}
                for idx, val in zip(df_6m.index, df_6m[close_col])
                if not math.isnan(float(val))
            ]
        if volume_col and not df_6m.empty:
            volume_history = [
                {"date": str(idx.date()), "value": int(val)}
                for idx, val in zip(df_6m.index, df_6m[volume_col])
                if not math.isnan(float(val))
            ]
    except Exception as exc:
        print(f"[technical_agent] chart history failed for {ticker}: {exc!r}")

    return TechnicalResult(
        ticker=ticker,
        volume=volume,
        volume_pct_change=volume_pct_change,
        pct_off_52w_high=pct_off_52w_high,
        historical_volatility=historical_volatility,
        atr=atr,
        acc_dis_score=acc_dis_score,
        price_history=price_history,
        volume_history=volume_history,
    )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def technical_node(state: "GraphState") -> dict:
    """LangGraph node: runs compute_technical and returns the result."""
    ticker: str = state["ticker"]
    result = compute_technical(ticker)
    return {"technical_result": result}
