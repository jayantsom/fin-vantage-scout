"""
backend/agents/step2_synthesis_agent.py

Synthesis Agent — the "fan-in" step that receives all three step-1
results and uses an LLM to produce a single, plain-English rating.

Rating options: "Attractive" | "Neutral" | "Caution"

The disclaimer field ("This is an educational project, not investment
advice.") is embedded in the Pydantic model so it travels with the data
all the way to the API response — not just the UI.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from backend.agents.step1a_fundamentals_agent import FundamentalsResult
from backend.agents.step1b_momentum_agent import MomentumResult
from backend.agents.step1c_news_agent import NewsSentimentResult
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
    The final output for one ticker after all agents have run.

    rating      : One of three levels — Attractive, Neutral, or Caution.
    explanation : 3-5 sentences referencing the actual numbers.
    disclaimer  : Always present in the data, not just the UI.
    """

    ticker: str
    rating: Literal["Attractive", "Neutral", "Caution"] = "Neutral"
    explanation: str = ""
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------------------
# Pure computation function
# ---------------------------------------------------------------------------


def compute_synthesis(
    fundamentals: FundamentalsResult,
    momentum: MomentumResult,
    news: NewsSentimentResult,
) -> SynthesisResult:
    """
    Call the heavy LLM to synthesise all three data signals into a rating.

    If the LLM call or JSON parsing fails, returns a Neutral result with an
    error note in the explanation field so the pipeline never crashes.
    """
    ticker = fundamentals.ticker

    # Build the formatted prompt, injecting all the actual numbers.
    prompt = SYNTHESIS_PROMPT.format(
        ticker=ticker,
        current_ratio=fundamentals.current_ratio,
        debt_to_equity=fundamentals.debt_to_equity,
        roe=fundamentals.roe,
        gross_margin_trend=fundamentals.gross_margin_trend,
        data_available=fundamentals.data_available,
        return_6m=momentum.return_6m,
        return_12m=momentum.return_12m,
        percentile_rank_6m=momentum.percentile_rank_6m,
        percentile_rank_12m=momentum.percentile_rank_12m,
        sentiment=news.sentiment,
        news_justification=news.justification,
    )

    try:
        llm = get_llm("heavy")
        response = llm.invoke(prompt)
        raw = response.content.strip()

        # Strip markdown code fences the model sometimes adds.
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        parsed = json.loads(raw)
        rating = parsed.get("rating", "Neutral")
        explanation = parsed.get("explanation", "")

        # Guard against the model returning an unexpected rating string.
        if rating not in ("Attractive", "Neutral", "Caution"):
            rating = "Neutral"

        return SynthesisResult(
            ticker=ticker,
            rating=rating,  # type: ignore[arg-type]
            explanation=explanation,
            disclaimer=DISCLAIMER,
        )

    except Exception as exc:
        print(f"[synthesis_agent] LLM call failed for {ticker}: {exc!r}")
        return SynthesisResult(
            ticker=ticker,
            rating="Neutral",
            explanation=f"Synthesis unavailable due to an error: {type(exc).__name__}.",
            disclaimer=DISCLAIMER,
        )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def synthesis_node(state: "GraphState") -> dict:
    """
    LangGraph node: reads the three intermediate results from state,
    calls compute_synthesis, and stores the SynthesisResult.
    """
    result = compute_synthesis(
        fundamentals=state["fundamentals_result"],
        momentum=state["momentum_result"],
        news=state["news_result"],
    )
    return {"synthesis_result": result}
