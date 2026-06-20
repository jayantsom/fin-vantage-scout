"""
backend/data/market_data.py — yfinance wrappers + SQLite cache helpers.

Why cache?  yfinance talks to Yahoo Finance over the network.  Repeating
the same request every time a user clicks "Analyze" is slow and can get
rate-limited.  We store results in a local SQLite file and only re-fetch
when the data is older than the configured TTL.

Cache schema (single table):
    key       TEXT PRIMARY KEY   — e.g. "price_AAPL_6mo"
    value     TEXT               — JSON-serialised payload
    fetched_at TEXT              — ISO-8601 UTC timestamp
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from config import PRICE_CACHE_TTL_HOURS

# The SQLite database lives next to this file so it is easy to inspect or
# delete during development.
DB_PATH = Path(__file__).parent.parent.parent / "cache.db"


# ---------------------------------------------------------------------------
# Custom JSON encoder — handles pandas Timestamp and Python datetime
# ---------------------------------------------------------------------------


class _DatetimeEncoder(json.JSONEncoder):
    """Extend the default encoder to handle Timestamp / datetime objects."""

    def default(self, obj: Any) -> Any:  # noqa: ANN401
        if isinstance(obj, (pd.Timestamp, datetime)):
            return obj.isoformat()
        return super().default(obj)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_connection() -> sqlite3.Connection:
    """Open (or create) the SQLite DB and ensure the cache table exists."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_cached(key: str, ttl_hours: int) -> Any | None:
    """
    Return the cached value for `key` if it was fetched within `ttl_hours`.
    Returns None if the entry is missing or expired.
    """
    conn = _get_connection()
    row = conn.execute(
        "SELECT value, fetched_at FROM cache WHERE key = ?", (key,)
    ).fetchone()
    conn.close()

    if row is None:
        return None

    value_json, fetched_at_str = row
    fetched_at = datetime.fromisoformat(fetched_at_str)
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600

    if age_hours > ttl_hours:
        return None  # Expired

    return json.loads(value_json)


def set_cached(key: str, value: Any) -> None:
    """Store `value` (must be JSON-serialisable) in the cache under `key`."""
    conn = _get_connection()
    conn.execute(
        """INSERT INTO cache (key, value, fetched_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at""",
        (key, json.dumps(value, cls=_DatetimeEncoder), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# yfinance wrappers
# ---------------------------------------------------------------------------


def _records_to_df(records: list[dict]) -> pd.DataFrame:
    """
    Convert a list-of-records (as stored in SQLite) back to a proper DataFrame
    with a DatetimeIndex.

    yfinance's reset_index() produces a "Date" or "Datetime" column.  When we
    do pd.DataFrame(records) that column comes back as a plain string column,
    not the index.  This helper promotes it back to a DatetimeIndex.
    """
    df = pd.DataFrame(records)
    # Find whichever column holds the date (yfinance uses "Date" or "Datetime").
    date_col = next(
        (c for c in df.columns if c.lower() in ("date", "datetime")), None
    )
    if date_col is not None:
        df = df.set_index(pd.to_datetime(df[date_col])).drop(columns=[date_col])
        df.index.name = "Date"
    return df


def get_price_history(ticker: str, period: str) -> pd.DataFrame | None:
    """
    Fetch OHLCV price history for `ticker` over `period` (e.g. "1y", "2y", "5y").
    Results are cached for PRICE_CACHE_TTL_HOURS.
    Returns a DataFrame with a DatetimeIndex, or None if the fetch fails.
    """
    cache_key = f"price_{ticker}_{period}"
    cached = get_cached(cache_key, PRICE_CACHE_TTL_HOURS)
    if cached is not None:
        # Reconstruct a proper DataFrame with DatetimeIndex from cached records.
        return _records_to_df(cached)

    try:
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df.empty:
            return None
        # yfinance can return MultiIndex columns when downloading a single
        # ticker; flatten them so the DataFrame is always simple.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        # Store as a list-of-records so json.dumps can handle it.
        set_cached(cache_key, df.reset_index().to_dict(orient="records"))
        return df
    except Exception as exc:
        print(f"[market_data] price fetch failed for {ticker}: {exc!r}")
        return None


def get_fundamentals_data(ticker: str) -> dict[str, Any]:
    """
    Fetch fundamental financial data for `ticker` via yfinance.
    Returns a dict with keys like 'currentRatio', 'debtToEquity', etc.
    Missing fields will simply be absent from the dict.
    Never raises — returns an empty dict on failure.
    """
    cache_key = f"fundamentals_{ticker}"
    cached = get_cached(cache_key, PRICE_CACHE_TTL_HOURS)
    if cached is not None:
        return cached

    try:
        info = yf.Ticker(ticker).info or {}
        # Persist only the subset we actually use (keeps the DB small).
        subset = {
            k: info.get(k)
            for k in (
                "currentRatio",
                "debtToEquity",
                "returnOnEquity",
                "grossMargins",
                "trailingPegRatio",
                "forwardPE",
                "marketCap",
                "shortName",
            )
        }
        set_cached(cache_key, subset)
        return subset
    except Exception as exc:
        print(f"[market_data] fundamentals fetch failed for {ticker}: {exc!r}")
        return {}
