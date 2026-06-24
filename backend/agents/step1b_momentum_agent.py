"""
backend/agents/step1b_momentum_agent.py

Momentum Agent — computes 6-month and 12-month price returns for a ticker
and ranks them as percentiles vs. a peer universe.

Inspiration note
----------------
The percentile-rank approach here is inspired by the general concept of
Relative Strength ratings used in momentum investing.  This is an
independent calculation using freely available price data — it is NOT a
reproduction of IBD's proprietary RS Rating or any other commercial score.

This module's compute_momentum() is also called directly by
backend/data/universe.py for the auto-screen pre-filter, which is why
it lives here as a pure function rather than being embedded in the node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from backend.data.market_data import get_price_history
from backend.data.alpha_vantage import get_rsi

if TYPE_CHECKING:
    from backend.app import GraphState


# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------

from pydantic import BaseModel  # noqa: E402 (must be after __future__ annotations)


class MomentumResult(BaseModel):
    """
    Momentum metrics for one ticker relative to its peer universe.

    Percentile ranks range from 0 (worst) to 100 (best).
    None means the calculation couldn't be completed (not enough price data).

    rsi_14 is sourced independently from Alpha Vantage (14-day RSI, daily
    interval) and is None when the AV key is absent or the call fails.
    """

    ticker: str
    return_6m: float | None = None            # Percentage return over last 6 months
    return_12m: float | None = None           # Percentage return over last 12 months
    percentile_rank_6m: float | None = None   # Where this ticker ranks vs peers (6m)
    percentile_rank_12m: float | None = None  # Where this ticker ranks vs peers (12m)
    rsi_14: float | None = None               # 14-day RSI from Alpha Vantage (0–100)


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def _period_return(ticker: str, months: int) -> float | None:
    """
    Compute the total price return over the last `months` months.
    Returns None if there is insufficient price history.
    """
    period = "6mo" if months == 6 else "1y"
    df = get_price_history(ticker, period)

    if df is None or len(df) < 2:
        return None

    # Ensure the index is a DatetimeIndex so we can slice by date.
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index(pd.to_datetime(df.index))

    # Flatten MultiIndex columns — yfinance sometimes returns them for single tickers.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # "Close" column might be lowercase depending on yfinance version.
    close_col = next((c for c in df.columns if c.lower() == "close"), None)
    if close_col is None:
        return None

    start_price = df[close_col].iloc[0]
    end_price = df[close_col].iloc[-1]

    if start_price == 0 or pd.isna(start_price) or pd.isna(end_price):
        return None

    return (end_price - start_price) / start_price * 100  # as a percentage


def _percentile_rank(value: float, all_values: list[float]) -> float:
    """
    What percentage of `all_values` is strictly below `value`?
    Returns a number from 0 to 100.
    """
    if not all_values:
        return 0.0
    below = sum(1 for v in all_values if v < value)
    return (below / len(all_values)) * 100


def compute_momentum(ticker: str, universe: list[str]) -> MomentumResult:
    """
    Compute 6m and 12m returns for `ticker` and rank them vs. `universe`.

    Parameters
    ----------
    ticker   : The stock to analyse.
    universe : List of peer tickers to compute the percentile rank against.
               Can be the full FALLBACK_UNIVERSE or a custom list.

    Returns
    -------
    MomentumResult — never raises; missing data fields are None.
    """
    r6 = _period_return(ticker, 6)
    r12 = _period_return(ticker, 12)

    # Collect universe returns for ranking.
    # We only download tickers in the universe that aren't the ticker itself
    # to avoid double-counting.
    peer_returns_6m: list[float] = []
    peer_returns_12m: list[float] = []

    for peer in universe:
        if peer == ticker:
            continue
        pr6 = _period_return(peer, 6)
        pr12 = _period_return(peer, 12)
        if pr6 is not None:
            peer_returns_6m.append(pr6)
        if pr12 is not None:
            peer_returns_12m.append(pr12)

    # Compute percentile rank (only possible if we have both the ticker's
    # return and at least one peer return to compare against).
    rank6 = None
    if r6 is not None and peer_returns_6m:
        rank6 = _percentile_rank(r6, peer_returns_6m + [r6])

    rank12 = None
    if r12 is not None and peer_returns_12m:
        rank12 = _percentile_rank(r12, peer_returns_12m + [r12])

    # Fetch RSI once; it's cached so the second call inside round() won't hit AV.
    _rsi = get_rsi(ticker)

    return MomentumResult(
        ticker=ticker,
        return_6m=round(r6, 2) if r6 is not None else None,
        return_12m=round(r12, 2) if r12 is not None else None,
        percentile_rank_6m=round(rank6, 1) if rank6 is not None else None,
        percentile_rank_12m=round(rank12, 1) if rank12 is not None else None,
        rsi_14=round(_rsi, 2) if _rsi is not None else None,
    )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def momentum_node(state: "GraphState") -> dict:
    """
    LangGraph node: runs compute_momentum using the ticker and universe
    stored in the shared graph state.
    """
    ticker: str = state["ticker"]
    universe: list[str] = state["universe"]
    result = compute_momentum(ticker, universe)
    return {"momentum_result": result}
