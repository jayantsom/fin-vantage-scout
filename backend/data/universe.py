"""
backend/data/universe.py — Stock universe helpers.

Two modes:
  1. Manual  — user provides their own tickers; we just clean them up.
  2. Auto-screen — fetch the current FFTY ETF holdings, cache them, run a
     cheap momentum pre-screen, return the top 10 candidates.

IMPORTANT disclosure
--------------------
The auto-screen candidate list comes from publicly disclosed ETF holdings of
the Innovator IBD 50 ETF (FFTY).  The momentum score we compute is our own
independent calculation based on price history — it is NOT IBD's Relative
Strength Rating or any other proprietary score.
"""

from __future__ import annotations

import io

import pandas as pd
import requests

from config import FALLBACK_UNIVERSE, HOLDINGS_CACHE_TTL_HOURS
from backend.data.market_data import get_cached, set_cached

# Public holdings CSV for the Innovator IBD 50 ETF (FFTY).
# This URL serves the daily portfolio disclosure — no login required.
FFTY_HOLDINGS_URL = (
    "https://www.innovatoretfs.com/etf/PortfolioExport.aspx?ticker=ffty"
)

HOLDINGS_CACHE_KEY = "ffty_holdings"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_manual_universe(tickers: list[str]) -> list[str]:
    """
    Clean and return a user-supplied list of tickers.
    We upper-case them and strip whitespace — nothing fancier needed here.
    """
    return [t.strip().upper() for t in tickers if t.strip()]


def get_auto_screen_candidates() -> list[str]:
    """
    Fetch FFTY ETF holdings, apply a momentum pre-screen, return top 10.

    Steps:
      1. Check SQLite cache (daily expiry).
      2. If cache miss: try to download the CSV from Innovator's site.
      3. If the download fails: fall back to FALLBACK_UNIVERSE.
      4. Run compute_momentum() on every candidate (no LLM, just maths).
      5. Sort by 6-month percentile rank descending; return top 10 tickers.
    """
    candidates = _load_ffty_holdings()
    return _momentum_prescreen(candidates, top_n=10)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_ffty_holdings() -> list[str]:
    """Return the list of FFTY holdings, from cache or fresh download."""
    cached = get_cached(HOLDINGS_CACHE_KEY, HOLDINGS_CACHE_TTL_HOURS)
    if cached is not None:
        print(f"[universe] Using cached FFTY holdings ({len(cached)} tickers)")
        return cached

    tickers = _fetch_ffty_holdings_from_web()
    if tickers:
        set_cached(HOLDINGS_CACHE_KEY, tickers)
        print(f"[universe] Fetched {len(tickers)} FFTY holdings from web; cached.")
        return tickers

    print("[universe] FFTY fetch failed — using FALLBACK_UNIVERSE.")
    return FALLBACK_UNIVERSE


def _fetch_ffty_holdings_from_web() -> list[str]:
    """
    Download the FFTY holdings CSV from Innovator's public disclosure page.
    Returns a list of ticker strings, or an empty list on any failure.
    """
    try:
        resp = requests.get(FFTY_HOLDINGS_URL, timeout=15)
        resp.raise_for_status()

        # The response is a CSV.  We need to find where the actual data rows
        # start — the file has a few header/metadata lines before the column
        # headers.  We try a few common patterns.
        text = resp.text
        df: pd.DataFrame | None = None

        for skip in range(0, 6):
            try:
                candidate_df = pd.read_csv(io.StringIO(text), skiprows=skip)
                # Look for a column that plausibly contains ticker symbols.
                ticker_col = next(
                    (
                        c
                        for c in candidate_df.columns
                        if "ticker" in c.lower() or "symbol" in c.lower()
                    ),
                    None,
                )
                if ticker_col:
                    df = candidate_df
                    tickers_raw = df[ticker_col].dropna().astype(str).tolist()
                    break
            except Exception:
                continue

        if df is None:
            return []

        # Clean: strip whitespace, upper-case, remove non-alpha (e.g. blanks,
        # "Total", footnote rows).
        cleaned = [
            t.strip().upper()
            for t in tickers_raw
            if t.strip().isalpha() and len(t.strip()) <= 5
        ]
        return cleaned if cleaned else []

    except Exception as exc:
        print(f"[universe] FFTY web fetch error: {exc!r}")
        return []


def _momentum_prescreen(candidates: list[str], top_n: int) -> list[str]:
    """
    Run compute_momentum() on each candidate and return the top `top_n`
    tickers by 6-month percentile rank.

    We import compute_momentum here (inside the function) to avoid a
    circular import at module load time.
    """
    # Late import to avoid circular dependency at module load.
    from backend.agents.step1b_momentum_agent import compute_momentum  # noqa: PLC0415

    scores: list[tuple[str, float]] = []
    for ticker in candidates:
        try:
            result = compute_momentum(ticker, candidates)
            rank = result.percentile_rank_6m or 0.0
            scores.append((ticker, rank))
        except Exception as exc:
            print(f"[universe] momentum pre-screen failed for {ticker}: {exc!r}")
            scores.append((ticker, 0.0))

    scores.sort(key=lambda x: x[1], reverse=True)
    top = [t for t, _ in scores[:top_n]]
    print(f"[universe] Auto-screen selected top {top_n}: {top}")
    return top
