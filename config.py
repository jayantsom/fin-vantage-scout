"""
config.py — Project-wide configuration, shared by backend/ and tests/.

Keeping this at the root means both `backend/` and `tests/` can do
    from config import get_llm, FALLBACK_UNIVERSE
without any sys.path manipulation.

LLM strategy
------------
We try Groq first (fast cloud inference, free tier).  If *anything* goes wrong
(missing API key, rate-limit, network error) we transparently fall back to a
local Ollama model.  The caller never needs to know which one ran.
"""

from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv

# Load .env at import time — safe to call multiple times (it's a no-op after
# the first call).  The only required variable is GROQ_API_KEY.
load_dotenv()

# ---------------------------------------------------------------------------
# LLM model name constants
# ---------------------------------------------------------------------------

GROQ_HEAVY_MODEL = "llama-3.3-70b-versatile"   # used for synthesis (richer reasoning)
GROQ_LIGHT_MODEL = "llama-3.1-8b-instant"       # used for news sentiment (fast + cheap)
OLLAMA_FALLBACK_MODEL = "llama3.2:3b"           # local fallback; run `ollama pull llama3.2:3b`

# ---------------------------------------------------------------------------
# Cache TTLs
# ---------------------------------------------------------------------------

PRICE_CACHE_TTL_HOURS: int = 1      # yfinance price history
HOLDINGS_CACHE_TTL_HOURS: int = 24  # FFTY ETF holdings list

# ---------------------------------------------------------------------------
# Alpha Vantage (secondary data source)
# ---------------------------------------------------------------------------

# Free-tier key — set in .env.  If unset, Alpha Vantage calls are silently
# skipped (the fallback logic treats None as "data unavailable").
ALPHA_VANTAGE_API_KEY: str | None = os.getenv("ALPHA_VANTAGE_API_KEY")

# Hard cap on how many tickers can be analysed per /analyze request when
# Alpha Vantage is enabled.  Each ticker consumes up to 2 AV calls
# (OVERVIEW + RSI).  The free tier allows 25 calls/day, so 10 tickers is a
# conservative bound that leaves room for retries and ad-hoc lookups.
ALPHA_VANTAGE_DAILY_LIMIT: int = 25
ALPHA_VANTAGE_TICKERS_PER_RUN: int = 10  # max tickers per /analyze call

# ---------------------------------------------------------------------------
# Fallback universe (~30 momentum-quality tickers)
#
# This list serves two purposes:
#   1. Fallback candidate universe if the FFTY holdings fetch fails.
#   2. The peer group used to compute percentile rank in momentum scoring.
#
# These are example tickers chosen for diversity and historical momentum
# behaviour — NOT a recommendation, NOT an IBD list.
# ---------------------------------------------------------------------------

FALLBACK_UNIVERSE: list[str] = [
    "AAPL", "MSFT", "NVDA", "AVGO", "META",
    "GOOGL", "AMZN", "LLY", "UNH", "V",
    "MA", "JPM", "ORCL", "CRM", "NOW",
    "PANW", "ANET", "CDNS", "SNPS", "KLAC",
    "LRCX", "MRVL", "TTD", "AXON", "DECK",
    "CRDO", "COHR", "CELH", "NFLX", "DASH",
]

# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


def get_llm(tier: Literal["heavy", "light"]):
    """
    Return a LangChain chat model for the requested tier.

    Tries Groq first.  If that raises *any* exception (missing key, rate limit,
    network timeout, …) it falls back to a local Ollama instance.

    Parameters
    ----------
    tier : "heavy" | "light"
        "heavy" → bigger model, used for the synthesis step.
        "light" → smaller/faster model, used for news sentiment.

    Returns
    -------
    A LangChain BaseChatModel instance ready to call .invoke() on.
    """
    groq_model = GROQ_HEAVY_MODEL if tier == "heavy" else GROQ_LIGHT_MODEL

    try:
        # langchain-groq reads GROQ_API_KEY from the environment automatically.
        from langchain_groq import ChatGroq  # type: ignore

        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set — switching to Ollama fallback")

        return ChatGroq(model=groq_model, temperature=0.2)

    except Exception as groq_err:
        # Log so the developer knows the fallback kicked in, then continue.
        print(f"[config] Groq unavailable ({groq_err!r}); falling back to Ollama '{OLLAMA_FALLBACK_MODEL}'")

        from langchain_ollama import ChatOllama  # type: ignore

        return ChatOllama(model=OLLAMA_FALLBACK_MODEL, temperature=0.2)
