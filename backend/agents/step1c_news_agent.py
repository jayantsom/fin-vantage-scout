"""
backend/agents/step1c_news_agent.py

News Sentiment Agent — fetches recent headlines for a ticker using
DuckDuckGo Search (no API key needed) and asks an LLM to classify
the overall sentiment as Positive, Neutral, or Negative.

Flow:
  1. Search DuckDuckGo for "{ticker} stock news" (last 3-5 results).
  2. Format the headlines into the NEWS_SENTIMENT_PROMPT.
  3. Send to get_llm("light") — the smaller, faster model.
  4. Parse the JSON response into a NewsSentimentResult.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

# The duckduckgo-search library was renamed to 'ddgs' in newer versions.
# We try the new name first and fall back to the old one gracefully.
try:
    from ddgs import DDGS  # type: ignore
except ImportError:
    from duckduckgo_search import DDGS  # type: ignore

from pydantic import BaseModel

from backend.prompts.news_prompt import NEWS_SENTIMENT_PROMPT
from config import get_llm

if TYPE_CHECKING:
    from backend.app import GraphState


# ---------------------------------------------------------------------------
# Pydantic result model
# ---------------------------------------------------------------------------


class NewsSentimentResult(BaseModel):
    """
    News sentiment reading for one ticker.

    headlines     : The raw headline strings fetched from DuckDuckGo.
    sentiment     : LLM-assigned label: Positive, Neutral, or Negative.
    justification : One-sentence explanation from the LLM.
    """

    ticker: str
    headlines: list[str] = []
    sentiment: Literal["Positive", "Neutral", "Negative"] = "Neutral"
    justification: str = "Insufficient news data to determine sentiment."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_headlines(ticker: str, max_results: int = 5) -> list[str]:
    """
    Search DuckDuckGo for recent news about `ticker` and return headlines.
    Returns an empty list if the search fails.
    """
    query = f"{ticker} stock news"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        return [r.get("title", "") for r in results if r.get("title")]
    except Exception as exc:
        print(f"[news_agent] DuckDuckGo search failed for {ticker}: {exc!r}")
        return []


def _llm_classify_sentiment(
    ticker: str, headlines: list[str]
) -> tuple[Literal["Positive", "Neutral", "Negative"], str]:
    """
    Ask the LLM to classify headline sentiment.
    Returns (sentiment_label, justification_sentence).
    Falls back to ("Neutral", default message) on any error.
    """
    if not headlines:
        return "Neutral", "No headlines were available to analyse."

    headlines_text = "\n".join(f"- {h}" for h in headlines)
    prompt = NEWS_SENTIMENT_PROMPT.format(headlines=headlines_text)

    try:
        llm = get_llm("light")
        response = llm.invoke(prompt)
        # LangChain returns an AIMessage; .content is the string payload.
        raw = response.content.strip()

        # Strip markdown fences if the model wrapped the JSON.
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        parsed = json.loads(raw)
        sentiment = parsed.get("sentiment", "Neutral")
        justification = parsed.get("justification", "No justification provided.")

        # Validate the sentiment value — LLMs sometimes deviate.
        if sentiment not in ("Positive", "Neutral", "Negative"):
            sentiment = "Neutral"

        return sentiment, justification  # type: ignore[return-value]

    except Exception as exc:
        print(f"[news_agent] LLM call failed for {ticker}: {exc!r}")
        return "Neutral", f"Sentiment analysis unavailable ({type(exc).__name__})."


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------


def news_sentiment_node(state: "GraphState") -> dict:
    """
    LangGraph node: fetches headlines and classifies sentiment for the
    ticker in the shared graph state.
    """
    ticker: str = state["ticker"]
    headlines = _fetch_headlines(ticker)
    sentiment, justification = _llm_classify_sentiment(ticker, headlines)

    result = NewsSentimentResult(
        ticker=ticker,
        headlines=headlines,
        sentiment=sentiment,
        justification=justification,
    )
    return {"news_result": result}
