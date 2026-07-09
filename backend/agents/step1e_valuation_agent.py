"""
backend/agents/step1f_valuation_agent.py

Valuation Agent — computes standard valuation multiples and compares them
against same-sector peers within the current analysis batch.

IMPORTANT LABELLING:
  peer_median_pe is derived from the P/E values of same-sector tickers in the
  *current batch only* — not a sector-wide or market-wide median.  It is
  labelled "Batch Peer Median" in the output to make this scope explicit.
  No external sector database is used.
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


class ValuationResult(BaseModel):
    """
    Valuation multiples for one ticker vs. same-sector batch peers.

    pe                      : Trailing twelve-month P/E ratio.
    ps                      : Price-to-Sales (TTM).
    ev_ebitda               : Enterprise Value / EBITDA.
    peer_comparison_available: True when >= 2 same-sector peers are in the batch.
    peer_median_pe          : Median P/E of same-sector batch peers (excl. self).
                              Labelled "Batch Peer Median PE — batch scope only."
    """

    ticker: str
    pe: float | None = None
    ps: float | None = None
    ev_ebitda: float | None = None
    peer_comparison_available: bool = False
    peer_median_pe: float | None = None   # batch-scope only, not sector-wide


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def compute_valuation(ticker: str, peer_pes: list[float]) -> ValuationResult:
    """
    Compute valuation multiples for `ticker` and compare against `peer_pes`.

    Parameters
    ----------
    ticker   : The stock being analysed.
    peer_pes : P/E values of same-sector batch peers (excluding this ticker).
               Pre-computed by app.py before the graph runs.

    Returns
    -------
    ValuationResult — never raises; missing data fields are None.
    """
    raw = get_fundamentals_data(ticker)

    if not raw:
        return ValuationResult(ticker=ticker)

    pe_raw = raw.get("trailingPE")
    ps_raw = raw.get("priceToSalesTrailing12Months")
    ev_raw = raw.get("enterpriseToEbitda")

    # Sanitise — yfinance sometimes returns negative or absurdly large P/Es
    def _clean(val: object, lo: float = -500, hi: float = 2000) -> float | None:
        if val is None:
            return None
        try:
            f = float(val)
            return round(f, 2) if lo <= f <= hi else None
        except (ValueError, TypeError):
            return None

    pe = _clean(pe_raw, -500, 2000)
    ps = _clean(ps_raw, 0, 500)
    ev_ebitda = _clean(ev_raw, -100, 500)

    # --- Peer comparison ---
    valid_peers = [v for v in peer_pes if v is not None]
    peer_comparison_available = len(valid_peers) >= 2
    peer_median_pe: float | None = None
    if peer_comparison_available:
        peer_median_pe = round(statistics.median(valid_peers), 2)

    return ValuationResult(
        ticker=ticker,
        pe=pe,
        ps=ps,
        ev_ebitda=ev_ebitda,
        peer_comparison_available=peer_comparison_available,
        peer_median_pe=peer_median_pe,
    )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def valuation_node(state: "GraphState") -> dict:
    """
    LangGraph node: reads peer_pes from state (pre-computed by app.py),
    runs compute_valuation, and returns the result.
    """
    ticker: str = state["ticker"]
    peer_pes: list[float] = state.get("peer_pes", [])  # type: ignore[attr-defined]
    result = compute_valuation(ticker, peer_pes)
    return {"valuation_result": result}
