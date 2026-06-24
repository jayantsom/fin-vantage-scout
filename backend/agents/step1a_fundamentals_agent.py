"""
backend/agents/step1a_fundamentals_agent.py

Fundamentals Agent — collects balance-sheet and profitability metrics
from yfinance and packages them into a typed Pydantic model.

Design principles (echoed throughout all agent files):
  - One Pydantic model  → defines what this agent produces.
  - One pure function   → does the actual computation (easy to unit-test).
  - One node wrapper    → thin bridge between the pure function and LangGraph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from backend.data.market_data import get_fundamentals_data
from backend.data.alpha_vantage import get_overview

if TYPE_CHECKING:
    # GraphState is only needed for type-checking; avoids a circular import at runtime.
    from backend.app import GraphState


# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------


class FundamentalsResult(BaseModel):
    """
    Snapshot of key fundamental metrics for one ticker.

    All numeric fields are Optional[float] because yfinance does not always
    have data for every metric on every stock.  `data_available` is False
    when yfinance returned nothing useful at all.
    """

    ticker: str
    company_name: str | None = None          # e.g. "Apple Inc."
    current_ratio: float | None = None       # Current assets / current liabilities
    debt_to_equity: float | None = None      # Total debt / shareholder equity
    roe: float | None = None                 # Return on equity (net income / equity)
    gross_margin: float | None = None        # Gross profit / revenue (latest year)
    gross_margin_trend: str | None = None    # "Improving", "Stable", or "Declining"
    data_available: bool = True              # False if yfinance returned nothing


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def _av_fallback(ticker: str, *fields: str) -> dict[str, float | None]:
    """
    Fetch Alpha Vantage OVERVIEW for `ticker` and return the requested fields
    mapped to Python float (or None).  Only called when yfinance is missing
    one or more values, so the AV quota is only consumed when needed.

    AV field names used here:
        CurrentRatio          → current_ratio
        DebtToEquityRatio     → debt_to_equity
        ReturnOnEquityTTM     → roe
        ProfitMargin          → gross_margin (gross is not directly available;
                                 profit margin is the closest free-tier proxy)
    """
    result: dict[str, float | None] = {field: None for field in fields}
    if not fields:
        return result

    overview = get_overview(ticker)  # returns None silently on failure
    if overview is None:
        return result

    av_map = {
        "current_ratio": "CurrentRatio",
        "debt_to_equity": "DebtToEquityRatio",
        "roe": "ReturnOnEquityTTM",
        "gross_margin": "ProfitMargin",
    }
    for field in fields:
        av_key = av_map.get(field)
        if av_key is None:
            continue
        raw_val = overview.get(av_key)
        if raw_val is not None:
            try:
                result[field] = float(raw_val)
            except (ValueError, TypeError):
                pass  # leave as None
    return result


def compute_fundamentals(ticker: str) -> FundamentalsResult:
    """
    Fetch fundamental data for `ticker` and return a FundamentalsResult.

    This function never raises — missing or broken data becomes None /
    data_available=False so the rest of the pipeline can still run.

    Data resolution order:
      1. yfinance (primary, no quota cost)
      2. Alpha Vantage OVERVIEW (fallback for any None fields, consumes quota)
    """
    raw: dict[str, Any] = get_fundamentals_data(ticker)

    if not raw:
        return FundamentalsResult(ticker=ticker, data_available=False)

    # Collect yfinance values first.
    company_name = raw.get("shortName")
    current_ratio = raw.get("currentRatio")
    debt_to_equity = raw.get("debtToEquity")
    roe = raw.get("returnOnEquity")
    gross_margin = raw.get("grossMargins")

    # Identify any fields that yfinance couldn't provide.
    missing = [
        field
        for field, val in [
            ("current_ratio", current_ratio),
            ("debt_to_equity", debt_to_equity),
            ("roe", roe),
            ("gross_margin", gross_margin),
        ]
        if val is None
    ]

    # Attempt Alpha Vantage fallback only when something is missing.
    if missing:
        av = _av_fallback(ticker, *missing)
        if current_ratio is None:
            current_ratio = av.get("current_ratio")
        if debt_to_equity is None:
            debt_to_equity = av.get("debt_to_equity")
        if roe is None:
            roe = av.get("roe")
        if gross_margin is None:
            gross_margin = av.get("gross_margin")

    # Gross margin "trend" is a simplified reading: we only have one snapshot
    # from yfinance's .info dict, so we label it as a static value here.
    # A more advanced version would compare trailing annual income statements.
    if gross_margin is not None:
        if gross_margin > 0.5:
            trend = "Strong"
        elif gross_margin > 0.25:
            trend = "Moderate"
        else:
            trend = "Thin"
    else:
        trend = None

    return FundamentalsResult(
        ticker=ticker,
        company_name=company_name,
        current_ratio=current_ratio,
        debt_to_equity=debt_to_equity,
        roe=roe,
        gross_margin=gross_margin,
        gross_margin_trend=trend,
        data_available=True,
    )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def fundamentals_node(state: "GraphState") -> dict:
    """
    LangGraph node: runs compute_fundamentals and returns the result so
    LangGraph can merge it back into the shared state.

    LangGraph calls each node with the current state dict and expects a dict
    back containing only the keys that changed.
    """
    ticker: str = state["ticker"]
    result = compute_fundamentals(ticker)
    return {"fundamentals_result": result}
