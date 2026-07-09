"""
backend/agents/step1a_fundamentals_agent.py

Fundamentals Agent — collects balance-sheet and profitability metrics
from yfinance and packages them into a typed Pydantic model.

Design principles (echoed throughout all agent files):
  - One Pydantic model  → defines what this agent produces.
  - One pure function   → does the actual computation (easy to unit-test).
  - One node wrapper    → thin bridge between the pure function and LangGraph.

Phase 3 additions:
  - eps_pct_change_latest_qtr / eps_pct_change_prev_qtr
  - sales_pct_change_last_qtr
  - mgmt_ownership_pct
  - sponsorship_trend
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from backend.data.market_data import get_fundamentals_data
from backend.data.alpha_vantage import get_overview

if TYPE_CHECKING:
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
    company_name: str | None = None           # e.g. "Apple Inc."
    current_ratio: float | None = None        # Current assets / current liabilities
    debt_to_equity: float | None = None       # Total debt / shareholder equity
    roe: float | None = None                  # Return on equity (net income / equity)
    gross_margin: float | None = None         # Gross profit / revenue (latest year)
    gross_margin_trend: str | None = None     # "Strong", "Moderate", or "Thin"

    # Phase 3 additions
    eps_pct_change_latest_qtr: float | None = None  # YoY EPS growth, latest quarter
    eps_pct_change_prev_qtr: float | None = None    # YoY EPS growth, prev quarter
    sales_pct_change_last_qtr: float | None = None  # YoY revenue growth, latest quarter
    mgmt_ownership_pct: float | None = None         # Insider ownership % (0-100)
    sponsorship_trend: str | None = None            # "Rising", "Flat", or "Falling"

    data_available: bool = True               # False if yfinance returned nothing


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def _av_fallback(ticker: str, *fields: str) -> dict[str, float | None]:
    """
    Fetch Alpha Vantage OVERVIEW for `ticker` and return the requested fields
    mapped to Python float (or None).  Only called when yfinance is missing
    one or more values, so the AV quota is only consumed when needed.
    """
    result: dict[str, float | None] = {field: None for field in fields}
    if not fields:
        return result

    overview = get_overview(ticker)
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
                pass
    return result


def _pct_change(new: float | None, old: float | None) -> float | None:
    """Safe YoY percentage change calculation."""
    if new is None or old is None or old == 0:
        return None
    return round(((new - old) / abs(old)) * 100, 2)


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

    # --- Core fields (yfinance primary, AV fallback) ---
    company_name = raw.get("shortName")
    current_ratio = raw.get("currentRatio")
    debt_to_equity = raw.get("debtToEquity")
    roe = raw.get("returnOnEquity")
    gross_margin = raw.get("grossMargins")

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

    # --- Gross margin trend label ---
    if gross_margin is not None:
        if gross_margin > 0.5:
            trend = "Strong"
        elif gross_margin > 0.25:
            trend = "Moderate"
        else:
            trend = "Thin"
    else:
        trend = None

    # --- EPS quarterly growth (Phase 3) ---
    eps_pct_change_latest_qtr: float | None = None
    eps_pct_change_prev_qtr: float | None = None
    qeps: list[float] = raw.get("quarterly_eps", [])
    # quarterly_eps is oldest→newest (last 6 quarters from yfinance)
    # To compute YoY we compare quarter N with quarter N-4
    if len(qeps) >= 5:
        eps_pct_change_latest_qtr = _pct_change(qeps[-1], qeps[-5])
    if len(qeps) >= 6:
        eps_pct_change_prev_qtr = _pct_change(qeps[-2], qeps[-6])

    # --- Sales quarterly growth (Phase 3) ---
    sales_pct_change_last_qtr: float | None = None
    qrev: list[float] = raw.get("quarterly_revenue", [])
    # quarterly_revenue comes newest-first from yfinance financials
    if len(qrev) >= 5:
        sales_pct_change_last_qtr = _pct_change(qrev[0], qrev[4])

    # --- Management ownership (Phase 3) ---
    mgmt_ownership_pct: float | None = None
    held_pct = raw.get("heldPercentInsiders")
    if held_pct is not None:
        try:
            mgmt_ownership_pct = round(float(held_pct) * 100, 2)
        except (ValueError, TypeError):
            pass

    # --- Sponsorship trend (Phase 3) ---
    # We have a single institutional holder count snapshot — for a real trend
    # we would need to compare across quarters.  With one data point we label
    # the count as "Flat" unless it is notably high or low.
    sponsorship_trend: str | None = None
    ih_count = raw.get("institutional_holder_count")
    if ih_count is not None:
        # Qualitative label based on count magnitude (proxy for interest level)
        if ih_count >= 50:
            sponsorship_trend = "Rising"
        elif ih_count >= 20:
            sponsorship_trend = "Flat"
        else:
            sponsorship_trend = "Falling"

    return FundamentalsResult(
        ticker=ticker,
        company_name=company_name,
        current_ratio=current_ratio,
        debt_to_equity=debt_to_equity,
        roe=roe,
        gross_margin=gross_margin,
        gross_margin_trend=trend,
        eps_pct_change_latest_qtr=eps_pct_change_latest_qtr,
        eps_pct_change_prev_qtr=eps_pct_change_prev_qtr,
        sales_pct_change_last_qtr=sales_pct_change_last_qtr,
        mgmt_ownership_pct=mgmt_ownership_pct,
        sponsorship_trend=sponsorship_trend,
        data_available=True,
    )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def fundamentals_node(state: "GraphState") -> dict:
    """
    LangGraph node: runs compute_fundamentals and returns the result so
    LangGraph can merge it back into the shared state.
    """
    ticker: str = state["ticker"]
    result = compute_fundamentals(ticker)
    return {"fundamentals_result": result}
