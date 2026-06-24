"""
backend/data/alpha_vantage.py — Alpha Vantage REST client (free tier).

Why direct REST calls instead of MCP?
--------------------------------------
We only ever call two known endpoints (OVERVIEW and RSI).  A full MCP client
would add unjustified complexity for two fixed calls.  Plain `requests` is
simpler, easier to test, and trivial to understand.

Rate-limit context
------------------
Alpha Vantage free tier: 25 requests / day, 5 requests / minute.
Both functions are cached (same SQLite cache as market_data.py) to avoid
burning quota on repeated calls for the same ticker.

Failure strategy
----------------
Both public functions return None on *any* error (missing key, network
timeout, rate-limit 429, unexpected JSON shape).  The calling agents treat
None as "data unavailable" and continue without raising.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from backend.data.market_data import get_cached, set_cached

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://www.alphavantage.co/query"

# Cache the OVERVIEW for 24 h (it changes infrequently — same cadence as
# fundamental data from yfinance).
_OVERVIEW_TTL_HOURS = 24

# Cache RSI for 1 h (daily RSI is computed on EOD prices; 1 h is fine for
# an intraday session but we don't want to re-fetch more than necessary).
_RSI_TTL_HOURS = 1


def _api_key() -> str | None:
    """Return the Alpha Vantage API key from the environment, or None."""
    return os.getenv("ALPHA_VANTAGE_API_KEY") or None


def _get(params: dict[str, str], timeout: int = 10) -> dict[str, Any] | None:
    """
    Make a GET request to the Alpha Vantage API.

    Returns the parsed JSON dict, or None if anything goes wrong.
    Never raises.
    """
    key = _api_key()
    if not key:
        print("[alpha_vantage] ALPHA_VANTAGE_API_KEY not set — skipping call.")
        return None

    try:
        resp = requests.get(
            _BASE_URL,
            params={**params, "apikey": key},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        # Alpha Vantage signals errors inside the JSON body, not via HTTP status.
        if "Error Message" in data:
            print(f"[alpha_vantage] API error: {data['Error Message']}")
            return None
        if "Note" in data:
            # "Thank you for using Alpha Vantage! …" rate-limit note.
            print(f"[alpha_vantage] Rate-limit note: {data['Note']}")
            return None
        if "Information" in data:
            # Another form of the rate-limit / demo-key message.
            print(f"[alpha_vantage] Info message: {data['Information']}")
            return None

        return data

    except Exception as exc:
        print(f"[alpha_vantage] Request failed: {exc!r}")
        return None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_overview(ticker: str) -> dict[str, Any] | None:
    """
    Fetch the OVERVIEW endpoint for `ticker` (company fundamentals).

    Returns a dict with keys like 'PERatio', 'CurrentRatio', 'DebtToEquityRatio',
    'ReturnOnEquityTTM', 'GrossProfitTTM', etc.  See Alpha Vantage docs for the
    full field list.

    Returns None if the key is missing, the ticker is unknown, or any error
    occurs.  Results are cached for _OVERVIEW_TTL_HOURS.
    """
    cache_key = f"av_overview_{ticker}"
    cached = get_cached(cache_key, _OVERVIEW_TTL_HOURS)
    if cached is not None:
        return cached

    data = _get({"function": "OVERVIEW", "symbol": ticker})
    if data is None:
        return None

    # An empty dict (ticker not found on AV) is also treated as None.
    if not data:
        return None

    set_cached(cache_key, data)
    return data


def get_rsi(ticker: str, interval: str = "daily", time_period: int = 14) -> float | None:
    """
    Fetch the most recent RSI value for `ticker`.

    Parameters
    ----------
    ticker      : Stock symbol (e.g. "AAPL").
    interval    : "daily" (default), "weekly", or "monthly".
    time_period : Look-back window for RSI calculation (default 14).

    Returns the latest RSI as a float (0–100), or None on any failure.
    Results are cached for _RSI_TTL_HOURS.
    """
    cache_key = f"av_rsi_{ticker}_{interval}_{time_period}"
    cached = get_cached(cache_key, _RSI_TTL_HOURS)
    if cached is not None:
        # Cached value is stored as a dict {"rsi": <float>} to stay JSON-safe.
        return cached.get("rsi")

    data = _get(
        {
            "function": "RSI",
            "symbol": ticker,
            "interval": interval,
            "time_period": str(time_period),
            "series_type": "close",
        }
    )
    if data is None:
        return None

    try:
        # Shape: {"Technical Analysis: RSI": {"2024-06-21": {"RSI": "62.34"}, ...}}
        ta_key = "Technical Analysis: RSI"
        rsi_series: dict = data[ta_key]
        # The first key is the most recent date.
        latest_date = next(iter(rsi_series))
        rsi_value = float(rsi_series[latest_date]["RSI"])
    except (KeyError, StopIteration, ValueError, TypeError) as exc:
        print(f"[alpha_vantage] RSI parse error for {ticker}: {exc!r}")
        return None

    set_cached(cache_key, {"rsi": rsi_value})
    return rsi_value
