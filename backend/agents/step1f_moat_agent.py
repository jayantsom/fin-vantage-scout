"""
backend/agents/step1g_moat_agent.py

Moat Agent — assesses competitive durability by measuring the stability of
gross margins and ROIC over available historical years.

Methodology:
  Coefficient of Variation (CoV) = Standard Deviation / Mean.
  A lower CoV indicates a more consistent (durable) metric over time.
  We compute CoV for gross margin, ROIC, and YoY revenue growth.

  ROIC proxy: Net Income / Total Assets (used when dedicated equity/invested
  capital data is unavailable via free yfinance fields).
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from pydantic import BaseModel

from backend.data.market_data import get_fundamentals_data

if TYPE_CHECKING:
    from backend.app import GraphState


# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------


class MoatResult(BaseModel):
    """
    Competitive durability metrics for one ticker.

    margin_stability    : CoV of annual gross margin (lower = more stable).
    roic_persistence    : CoV of annual ROIC proxy (lower = more persistent).
    revenue_consistency : CoV of YoY revenue growth (lower = more consistent).
    margin_by_year      : [{year, value}] — gross margin %, for charting.
    roic_by_year        : [{year, value}] — ROIC proxy %, for charting.
    """

    ticker: str
    margin_stability: float | None = None     # CoV of gross margin
    roic_persistence: float | None = None     # CoV of ROIC proxy
    revenue_consistency: float | None = None  # CoV of YoY revenue growth
    margin_by_year: list[dict] | None = None  # [{year, value}]
    roic_by_year: list[dict] | None = None    # [{year, value}]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cov(values: list[float]) -> float | None:
    """
    Coefficient of Variation = stdev / mean.
    Returns None if fewer than 2 valid values or mean is 0.
    """
    valid = [v for v in values if v is not None]
    if len(valid) < 2:
        return None
    mean = statistics.mean(valid)
    if mean == 0:
        return None
    stdev = statistics.stdev(valid)
    return round(stdev / abs(mean), 4)


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def compute_moat(ticker: str) -> MoatResult:
    """
    Compute multi-year CoV metrics for gross margin, ROIC, and revenue growth.
    Never raises — missing data fields become None.
    """
    raw = get_fundamentals_data(ticker)

    if not raw:
        return MoatResult(ticker=ticker)

    years: list[str] = raw.get("annual_years", [])
    gross_profits: list = raw.get("annual_gross_profit", [])
    total_revenues: list = raw.get("annual_total_revenue", [])
    net_incomes: list = raw.get("annual_net_income", [])
    total_assets = raw.get("total_assets")

    # --- Gross Margin by Year ---
    margin_by_year: list[dict] | None = None
    gross_margins: list[float] = []

    if years and gross_profits and total_revenues:
        pairs = list(zip(years, gross_profits, total_revenues))
        margin_rows = []
        for yr, gp, rev in pairs:
            if gp is not None and rev is not None and rev != 0:
                gm = round((gp / rev) * 100, 2)
                gross_margins.append(gm)
                margin_rows.append({"year": yr, "value": gm})
        if margin_rows:
            margin_by_year = margin_rows

    margin_stability = _cov(gross_margins) if len(gross_margins) >= 2 else None

    # --- ROIC Proxy by Year (Net Income / Total Assets) ---
    # True ROIC requires invested capital data not reliably available on the
    # free yfinance API, so we use Net Income / Total Assets as a proxy.
    roic_by_year: list[dict] | None = None
    roic_values: list[float] = []

    if years and net_incomes and total_assets and total_assets != 0:
        roic_rows = []
        for yr, ni in zip(years, net_incomes):
            if ni is not None:
                roic = round((ni / total_assets) * 100, 2)
                roic_values.append(roic)
                roic_rows.append({"year": yr, "value": roic})
        if roic_rows:
            roic_by_year = roic_rows

    roic_persistence = _cov(roic_values) if len(roic_values) >= 2 else None

    # --- Revenue Consistency (CoV of YoY revenue growth) ---
    revenue_consistency: float | None = None
    if total_revenues and len(total_revenues) >= 3:
        # yfinance returns newest-first — reverse to compute YoY correctly
        rev_sorted = list(reversed([r for r in total_revenues if r is not None]))
        yoy_growths = []
        for i in range(1, len(rev_sorted)):
            if rev_sorted[i - 1] != 0:
                growth = (rev_sorted[i] - rev_sorted[i - 1]) / abs(rev_sorted[i - 1])
                yoy_growths.append(growth)
        if len(yoy_growths) >= 2:
            revenue_consistency = _cov(yoy_growths)

    return MoatResult(
        ticker=ticker,
        margin_stability=margin_stability,
        roic_persistence=roic_persistence,
        revenue_consistency=revenue_consistency,
        margin_by_year=margin_by_year,
        roic_by_year=roic_by_year,
    )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def moat_node(state: "GraphState") -> dict:
    """LangGraph node: runs compute_moat and returns the result."""
    ticker: str = state["ticker"]
    result = compute_moat(ticker)
    return {"moat_result": result}
