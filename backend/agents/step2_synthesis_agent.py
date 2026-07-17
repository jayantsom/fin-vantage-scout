"""
backend/agents/step2_synthesis_agent.py

Synthesis Agent — the "fan-in" step that receives all 7 step-1 results
and uses an LLM to produce a structured, labeled rating with mandatory
sections for Growth, Quality, Valuation, Momentum/Technical, Moat, Sentiment.

Phase 3 additions:
  - compute_composite_score(): blends momentum, EPS growth, and margin into
    a single 1-99 style number.
  - momentum_volume_confirmed: bool — True when price momentum AND volume
    confirm each other.
  - SynthesisResult now has mandatory labeled section fields.

Rating options: "Attractive" | "Neutral" | "Caution"
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from backend.agents.step1a_fundamentals_agent import FundamentalsResult
from backend.agents.step1b_momentum_agent import MomentumResult
from backend.agents.step1g_news_agent import NewsSentimentResult
from backend.agents.step1c_technical_agent import TechnicalResult
from backend.agents.step1d_earnings_quality_agent import EarningsQualityResult
from backend.agents.step1e_valuation_agent import ValuationResult
from backend.agents.step1f_moat_agent import MoatResult
from backend.prompts.synthesis_prompt import SYNTHESIS_PROMPT
from config import get_llm

if TYPE_CHECKING:
    from backend.app import GraphState


# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------

DISCLAIMER = "This is an educational project, not investment advice."


class SynthesisResult(BaseModel):
    """
    The final structured output for one ticker after all agents have run.

    ticker                   : Ticker symbol.
    rating                   : One of three levels — Attractive, Neutral, Caution.
    summary                  : Short overall summary of the investment case.
    growth                   : LLM section on EPS and sales growth.
    quality                  : LLM section on balance sheet and profitability.
    valuation                : LLM section on multiples vs peers.
    momentum_technical       : LLM section on price momentum and technicals.
    moat                     : LLM section on competitive durability.
    sentiment                : LLM section on news sentiment.
    composite_score          : Blended 1-99 score (deterministic, not LLM).
    momentum_volume_confirmed: True when momentum percentile >= 60 AND
                               volume is above 50-day average.
    momentum_volume_reason   : One-line explanation of the above flag.
    disclaimer               : Always present in the data, not just the UI.
    """

    ticker: str
    rating: Literal["Attractive", "Neutral", "Caution"] = Field(
        "Neutral", description="Overall synthesis rating"
    )
    summary: str = Field("", description="Overall synthesis summary")
    # Labeled section fields (from LLM)
    growth: str = ""
    quality: str = ""
    valuation: str = ""
    momentum_technical: str = ""
    moat: str = ""
    sentiment: str = ""
    # Deterministic composite
    composite_score: int | None = None
    momentum_volume_confirmed: bool = False
    momentum_volume_reason: str = ""
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------------------
# Composite score (deterministic — no LLM)
# ---------------------------------------------------------------------------


def compute_composite_score(
    momentum: MomentumResult,
    fundamentals: FundamentalsResult,
    technical: TechnicalResult,
) -> int | None:
    """
    Blend momentum percentile, EPS growth, and gross margin into a 1-99 score.

    Weights:
      40% — Momentum: average of 6m and 12m percentile ranks (0-100).
      35% — EPS Growth: mapped from the latest-quarter YoY EPS % change.
      25% — Gross Margin: mapped from gross_margin value.

    Each component is normalised to 0-100 before weighting.
    Returns None if all three inputs are unavailable.
    """
    components = []
    weights = []

    # --- Momentum component (40%) ---
    ranks = [r for r in [momentum.percentile_rank_6m, momentum.percentile_rank_12m] if r is not None]
    if ranks:
        momentum_score = sum(ranks) / len(ranks)  # already 0-100
        components.append(momentum_score * 0.40)
        weights.append(0.40)

    # --- EPS Growth component (35%) ---
    eps_growth = fundamentals.eps_pct_change_latest_qtr
    if eps_growth is not None:
        # Map EPS growth % → 0-100:
        # >= 50% growth → 100, <= -50% → 0, linear in between
        eps_score = max(0, min(100, (eps_growth + 50)))
        components.append(eps_score * 0.35)
        weights.append(0.35)

    # --- Gross Margin component (25%) ---
    gm = fundamentals.gross_margin
    if gm is not None:
        # Map gross margin (0.0-1.0) → 0-100
        gm_score = max(0, min(100, gm * 100))
        components.append(gm_score * 0.25)
        weights.append(0.25)

    if not components:
        return None

    # Normalise by the total weight used (handles missing components gracefully)
    total_weight = sum(weights)
    raw_score = sum(components) / total_weight  # 0-100
    # Scale to 1-99
    return max(1, min(99, round(raw_score)))


# ---------------------------------------------------------------------------
# Momentum/volume confirmation (deterministic)
# ---------------------------------------------------------------------------


def compute_momentum_volume_confirmed(
    momentum: MomentumResult,
    technical: TechnicalResult,
) -> tuple[bool, str]:
    """
    Return (confirmed: bool, reason: str).

    confirmed = True when:
      - 6-month percentile rank >= 60 (above-average momentum), AND
      - volume_pct_change > 0 (last-day volume above 50-day average).
    """
    rank6 = momentum.percentile_rank_6m
    vol_pct = technical.volume_pct_change

    if rank6 is None and vol_pct is None:
        return False, "Insufficient data for momentum-volume confirmation."

    rank_ok = rank6 is not None and rank6 >= 60
    vol_ok = vol_pct is not None and vol_pct > 0

    if rank_ok and vol_ok:
        return True, (
            f"6M momentum rank {rank6:.0f}th pct (≥60) confirmed by "
            f"volume {vol_pct:+.1f}% above 50-day average."
        )
    elif rank_ok and not vol_ok:
        vol_str = f"{vol_pct:+.1f}%" if vol_pct is not None else "N/A"
        return False, (
            f"Momentum rank {rank6:.0f}th pct (≥60) but volume "
            f"{vol_str} does not confirm (needs to be positive)."
        )
    elif not rank_ok and vol_ok:
        rank_str = f"{rank6:.0f}th pct" if rank6 is not None else "N/A"
        return False, (
            f"Volume is {vol_pct:+.1f}% above average but momentum "
            f"rank {rank_str} is below the 60th percentile threshold."
        )
    else:
        rank_str = f"{rank6:.0f}th pct" if rank6 is not None else "N/A"
        vol_str = f"{vol_pct:+.1f}%" if vol_pct is not None else "N/A"
        return False, (
            f"Momentum rank {rank_str} and volume {vol_str} do not confirm."
        )


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def compute_synthesis(
    fundamentals: FundamentalsResult,
    momentum: MomentumResult,
    news: NewsSentimentResult,
    technical: TechnicalResult,
    earnings_quality: EarningsQualityResult,
    valuation: ValuationResult,
    moat: MoatResult,
) -> SynthesisResult:
    """
    Call the heavy LLM to synthesise all 7 data signals into a structured rating.

    If the LLM call or JSON parsing fails, returns a Neutral result with an
    error note so the pipeline never crashes.
    """
    ticker = fundamentals.ticker

    # Deterministic fields computed before the LLM call
    composite_score = compute_composite_score(momentum, fundamentals, technical)
    mv_confirmed, mv_reason = compute_momentum_volume_confirmed(momentum, technical)

    prompt = SYNTHESIS_PROMPT.format(
        ticker=ticker,
        # Growth
        eps_pct_change_latest_qtr=fundamentals.eps_pct_change_latest_qtr,
        eps_pct_change_prev_qtr=fundamentals.eps_pct_change_prev_qtr,
        sales_pct_change_last_qtr=fundamentals.sales_pct_change_last_qtr,
        # Quality
        current_ratio=fundamentals.current_ratio,
        debt_to_equity=fundamentals.debt_to_equity,
        roe=fundamentals.roe,
        gross_margin=fundamentals.gross_margin,
        gross_margin_trend=fundamentals.gross_margin_trend,
        mgmt_ownership_pct=fundamentals.mgmt_ownership_pct,
        sponsorship_trend=fundamentals.sponsorship_trend,
        # Valuation
        pe=valuation.pe,
        ps=valuation.ps,
        ev_ebitda=valuation.ev_ebitda,
        peer_median_pe=valuation.peer_median_pe,
        # Momentum & Technical
        return_6m=momentum.return_6m,
        return_12m=momentum.return_12m,
        percentile_rank_6m=momentum.percentile_rank_6m,
        percentile_rank_12m=momentum.percentile_rank_12m,
        rsi_14=momentum.rsi_14,
        volume_pct_change=technical.volume_pct_change,
        pct_off_52w_high=technical.pct_off_52w_high,
        historical_volatility=technical.historical_volatility,
        atr=technical.atr,
        acc_dis_score=technical.acc_dis_score,
        # Moat
        margin_stability=moat.margin_stability,
        roic_persistence=moat.roic_persistence,
        revenue_consistency=moat.revenue_consistency,
        # Earnings Quality
        accruals_ratio=earnings_quality.accruals_ratio,
        cash_conversion_ratio=earnings_quality.cash_conversion_ratio,
        # Sentiment
        sentiment=news.sentiment,
        news_justification=news.justification,
        # Composite
        composite_score=composite_score,
    )

    try:
        llm = get_llm("heavy")
        response = llm.invoke(prompt)
        raw = response.content.strip()

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # Sanitise: collapse any literal newlines/tabs embedded inside JSON
        # string values. The LLM sometimes puts hard newlines in the summary
        # field despite instructions, which breaks json.loads strictly.
        import re as _re
        def _fix_json_strings(text: str) -> str:
            """Replace unescaped newlines/tabs inside JSON string literals."""
            # Replace literal newlines inside string values with a space
            result = []
            in_string = False
            escape_next = False
            for ch in text:
                if escape_next:
                    result.append(ch)
                    escape_next = False
                elif ch == '\\':
                    result.append(ch)
                    escape_next = True
                elif ch == '"':
                    result.append(ch)
                    in_string = not in_string
                elif in_string and ch in ('\n', '\r', '\t'):
                    result.append(' ')  # replace control char with space
                else:
                    result.append(ch)
            return ''.join(result)

        raw = _fix_json_strings(raw)
        parsed = json.loads(raw)
        rating = parsed.get("rating", "Neutral")
        if rating not in ("Attractive", "Neutral", "Caution"):
            rating = "Neutral"

        return SynthesisResult(
            ticker=ticker,
            rating=rating,  # type: ignore[arg-type]
            summary=parsed.get("summary", ""),
            growth=parsed.get("growth", ""),
            quality=parsed.get("quality", ""),
            valuation=parsed.get("valuation", ""),
            momentum_technical=parsed.get("momentum_technical", ""),
            moat=parsed.get("moat", ""),
            sentiment=parsed.get("sentiment", ""),
            composite_score=composite_score,
            momentum_volume_confirmed=mv_confirmed,
            momentum_volume_reason=mv_reason,
            disclaimer=DISCLAIMER,
        )

    except Exception as exc:
        print(f"[synthesis_agent] LLM call failed for {ticker}: {exc!r}")
        return SynthesisResult(
            ticker=ticker,
            rating="Neutral",
            growth=f"Synthesis unavailable: {type(exc).__name__}.",
            composite_score=composite_score,
            momentum_volume_confirmed=mv_confirmed,
            momentum_volume_reason=mv_reason,
            disclaimer=DISCLAIMER,
        )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def synthesis_node(state: "GraphState") -> dict:
    """
    LangGraph node: reads all 7 intermediate results from state,
    calls compute_synthesis, and stores the SynthesisResult.
    """
    result = compute_synthesis(
        fundamentals=state["fundamentals_result"],
        momentum=state["momentum_result"],
        news=state["news_result"],
        technical=state["technical_result"],
        earnings_quality=state["earnings_quality_result"],
        valuation=state["valuation_result"],
        moat=state["moat_result"],
    )
    return {"synthesis_result": result}
