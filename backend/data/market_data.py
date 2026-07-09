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
    """
    df = pd.DataFrame(records)
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
        return _records_to_df(cached)

    try:
        df = yf.download(ticker, period=period, auto_adjust=True, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        set_cached(cache_key, df.reset_index().to_dict(orient="records"))
        return df
    except Exception as exc:
        print(f"[market_data] price fetch failed for {ticker}: {exc!r}")
        return None


def get_price_volume_history(ticker: str) -> pd.DataFrame | None:
    """
    Shared helper that fetches 2 years of daily OHLCV data for `ticker`.

    Using 2y gives enough data for:
      - 12-month momentum (step1b)
      - 20-day historical volatility and 14-day ATR (step1d)
      - OBV accumulation/distribution proxy (step1d)
      - 50-day average volume comparison (step1d)

    Returns a DataFrame with columns [Open, High, Low, Close, Volume] and a
    DatetimeIndex, or None if the fetch fails.  Results are cached under the
    key "price_{ticker}_2y".
    """
    return get_price_history(ticker, "2y")


def get_fundamentals_data(ticker: str) -> dict[str, Any]:
    """
    Fetch fundamental financial data for `ticker` via yfinance.
    Returns a dict with keys like 'currentRatio', 'debtToEquity', etc.
    Missing fields will simply be absent from the dict.
    Never raises — returns an empty dict on failure.

    Phase 3 expansion: also pulls financial-statement fields needed by
    step1a (EPS/sales growth, sponsorship), step1e (cash flow), step1f
    (valuation multiples), and step1g (multi-year margins/ROIC).
    """
    cache_key = f"fundamentals_{ticker}"
    cached = get_cached(cache_key, PRICE_CACHE_TTL_HOURS)
    if cached is not None:
        return cached

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        # --- Basic info fields ---
        subset: dict[str, Any] = {
            k: info.get(k)
            for k in (
                # Existing fields
                "currentRatio", "debtToEquity", "returnOnEquity",
                "grossMargins", "trailingPegRatio", "forwardPE",
                "marketCap", "shortName",
                # Valuation (step1f)
                "trailingPE", "priceToSalesTrailing12Months",
                "enterpriseToEbitda", "sector",
                # Earnings growth (step1a)
                "earningsQuarterlyGrowth", "revenueGrowth",
                # Ownership (step1a)
                "heldPercentInsiders",
            )
        }

        # --- Quarterly earnings for EPS trend (step1a) ---
        try:
            qe = t.quarterly_income_stmt
            if qe is not None and not qe.empty:
                # yfinance income_stmt index is metrics, columns are newest-first dates
                if "Diluted EPS" in qe.index:
                    eps_vals = qe.loc["Diluted EPS"].dropna().tolist()
                elif "Basic EPS" in qe.index:
                    eps_vals = qe.loc["Basic EPS"].dropna().tolist()
                elif "Net Income" in qe.index:
                    eps_vals = qe.loc["Net Income"].dropna().tolist()
                else:
                    eps_vals = []
                # Reverse to make it oldest-first as expected by the agent
                eps_vals.reverse()
                subset["quarterly_eps"] = [float(v) for v in eps_vals[-6:]]
            else:
                subset["quarterly_eps"] = []
        except Exception:
            subset["quarterly_eps"] = []

        # --- Quarterly revenue for sales trend (step1a) ---
        try:
            qf = t.quarterly_financials
            if qf is not None and not qf.empty:
                if "Total Revenue" in qf.index:
                    rev_vals = qf.loc["Total Revenue"].dropna().tolist()
                    subset["quarterly_revenue"] = [float(v) for v in rev_vals[:6]]
                else:
                    subset["quarterly_revenue"] = []
            else:
                subset["quarterly_revenue"] = []
        except Exception:
            subset["quarterly_revenue"] = []

        # --- Annual financials for moat multi-year margins (step1g) ---
        try:
            af = t.financials  # annual income statement, newest col first
            if af is not None and not af.empty:
                years = [str(c.year) for c in af.columns]
                gross_profit = af.loc["Gross Profit"].tolist() if "Gross Profit" in af.index else []
                total_rev = af.loc["Total Revenue"].tolist() if "Total Revenue" in af.index else []
                net_income = af.loc["Net Income"].tolist() if "Net Income" in af.index else []
                subset["annual_years"] = years
                subset["annual_gross_profit"] = [float(v) if pd.notna(v) else None for v in gross_profit]
                subset["annual_total_revenue"] = [float(v) if pd.notna(v) else None for v in total_rev]
                subset["annual_net_income"] = [float(v) if pd.notna(v) else None for v in net_income]
            else:
                subset["annual_years"] = []
                subset["annual_gross_profit"] = []
                subset["annual_total_revenue"] = []
                subset["annual_net_income"] = []
        except Exception:
            subset["annual_years"] = []
            subset["annual_gross_profit"] = []
            subset["annual_total_revenue"] = []
            subset["annual_net_income"] = []

        # --- Annual cash flow for earnings quality (step1e) ---
        try:
            cf = t.cashflow
            if cf is not None and not cf.empty:
                ocf_key = next((k for k in cf.index if "Operating" in k and "Cash" in k), None)
                ni_key = next((k for k in cf.index if "Net Income" in k), None)
                subset["operating_cash_flow"] = float(cf.loc[ocf_key].iloc[0]) if ocf_key else None
                subset["net_income_cf"] = float(cf.loc[ni_key].iloc[0]) if ni_key else None
            else:
                subset["operating_cash_flow"] = None
                subset["net_income_cf"] = None
        except Exception:
            subset["operating_cash_flow"] = None
            subset["net_income_cf"] = None

        # --- Balance sheet total assets (step1e) ---
        try:
            bs = t.balance_sheet
            if bs is not None and not bs.empty:
                ta_key = next((k for k in bs.index if "Total Assets" in k), None)
                subset["total_assets"] = float(bs.loc[ta_key].iloc[0]) if ta_key else None
            else:
                subset["total_assets"] = None
        except Exception:
            subset["total_assets"] = None

        # --- Institutional holder count (step1a sponsorship) ---
        try:
            ih = t.institutional_holders
            if ih is not None and not ih.empty:
                subset["institutional_holder_count"] = len(ih)
            else:
                subset["institutional_holder_count"] = None
        except Exception:
            subset["institutional_holder_count"] = None

        set_cached(cache_key, subset)
        return subset
    except Exception as exc:
        print(f"[market_data] fundamentals fetch failed for {ticker}: {exc!r}")
        return {}
