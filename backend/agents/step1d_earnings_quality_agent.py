"""
backend/agents/step1e_earnings_quality_agent.py

Earnings Quality Agent — computes accrual-based metrics to assess whether
reported earnings are backed by real cash flows.

Methodology reference:
  Sloan, R.G. (1996). "Do Stock Prices Fully Reflect Information in Accruals
  and Cash Flows About Future Earnings?" The Accounting Review, 71(3).

A high accruals ratio implies earnings are driven by non-cash accounting
entries rather than operating cash.  A cash_conversion_ratio < 1.0 similarly
suggests that reported net income exceeds cash actually generated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from backend.data.market_data import get_fundamentals_data

if TYPE_CHECKING:
    from backend.app import GraphState


# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------


class EarningsQualityResult(BaseModel):
    """
    Accruals-based earnings quality metrics for one ticker.

    accruals_ratio       : (Net Income - Operating Cash Flow) / Total Assets.
                           Sloan (1996). Lower (more negative) = better quality.
    cash_conversion_ratio: Operating Cash Flow / Net Income.
                           > 1.0 = earnings are well-supported by cash.
                           < 1.0 = earnings contain significant non-cash items.
    """

    ticker: str
    accruals_ratio: float | None = None       # (NI - OCF) / Total Assets
    cash_conversion_ratio: float | None = None  # OCF / NI


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def compute_earnings_quality(ticker: str) -> EarningsQualityResult:
    """
    Compute Sloan accruals ratio and cash conversion ratio for `ticker`.
    Never raises — missing data fields become None.
    """
    raw = get_fundamentals_data(ticker)

    if not raw:
        return EarningsQualityResult(ticker=ticker)

    net_income = raw.get("net_income_cf")
    operating_cash_flow = raw.get("operating_cash_flow")
    total_assets = raw.get("total_assets")

    # --- Accruals Ratio: (NI - OCF) / Total Assets ---
    accruals_ratio: float | None = None
    if (
        net_income is not None
        and operating_cash_flow is not None
        and total_assets is not None
        and total_assets != 0
    ):
        accruals_ratio = round((net_income - operating_cash_flow) / total_assets, 4)

    # --- Cash Conversion Ratio: OCF / NI ---
    cash_conversion_ratio: float | None = None
    if (
        operating_cash_flow is not None
        and net_income is not None
        and net_income != 0
    ):
        cash_conversion_ratio = round(operating_cash_flow / net_income, 4)

    return EarningsQualityResult(
        ticker=ticker,
        accruals_ratio=accruals_ratio,
        cash_conversion_ratio=cash_conversion_ratio,
    )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def earnings_quality_node(state: "GraphState") -> dict:
    """LangGraph node: runs compute_earnings_quality and returns the result."""
    ticker: str = state["ticker"]
    result = compute_earnings_quality(ticker)
    return {"earnings_quality_result": result}
