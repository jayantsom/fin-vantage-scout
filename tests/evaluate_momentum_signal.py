"""
tests/evaluate_momentum_signal.py — Momentum Signal Validation

This is NOT a unit test.  It asks a real empirical question:
    "Does a high momentum percentile rank actually predict
     better 1-month forward returns?"

Methodology
-----------
For each ticker in FALLBACK_UNIVERSE, and for each of the last 8 quarters
(quarter-start dates), we:
  1. Compute the momentum percentile rank at that date using historical data.
  2. Compute the actual 1-month forward price return from that date.
  3. Collect all (rank, forward_return) pairs across all tickers and dates.
  4. Print the Pearson correlation coefficient.
  5. Save a scatter plot to outputs/momentum_signal_scatter.png.

We report the real result -- not tuned to look good.  A positive correlation
suggests the signal has some predictive value.  A near-zero correlation is
also a valid and interesting finding.

Usage (from project root)
-------------------------
    uv run python tests/evaluate_momentum_signal.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure the project root is on sys.path so `config` is importable.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib.pyplot as plt
import pandas as pd
from scipy import stats  # type: ignore

from backend.data.market_data import get_price_history
from backend.agents.step1b_momentum_agent import compute_momentum, _period_return
from config import FALLBACK_UNIVERSE

# Outputs live alongside this script in tests/momentum/.
OUTPUT_DIR = Path(__file__).parent / "momentum"
OUTPUT_DIR.mkdir(exist_ok=True)
OUTPUT_PATH = OUTPUT_DIR / "momentum_signal_scatter.png"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_quarter_starts(n: int = 8) -> list[date]:
    """Return the last `n` quarter-start dates (Jan/Apr/Jul/Oct 1st)."""
    today = date.today()
    quarters: list[date] = []
    year, month = today.year, today.month
    # Roll back to the current quarter start.
    quarter_month = ((month - 1) // 3) * 3 + 1
    d = date(year, quarter_month, 1)
    for _ in range(n):
        # Go back one quarter at a time.
        d = _prev_quarter(d)
        quarters.append(d)
    return quarters


def _prev_quarter(d: date) -> date:
    """Return the quarter-start date one quarter before `d`."""
    month = d.month
    if month <= 3:
        return date(d.year - 1, 10, 1)
    elif month <= 6:
        return date(d.year, 1, 1)
    elif month <= 9:
        return date(d.year, 4, 1)
    else:
        return date(d.year, 7, 1)


def _forward_return(ticker: str, start: date, days: int = 21) -> float | None:
    """
    Compute the actual price return from `start` over the next `days` trading days.
    Uses the 1-year price history (already cached) and slices to the window.
    Returns None if data is unavailable.
    """
    df = get_price_history(ticker, "5y")  # 5y covers 8 quarters of lookback + forward
    if df is None or df.empty:
        return None

    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index(pd.to_datetime(df.index))

    df = df.sort_index()
    # Flatten MultiIndex columns if present (yfinance quirk).
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    close_col = next((c for c in df.columns if c.lower() == "close"), None)
    if close_col is None:
        return None

    # Find rows on or after start date.
    future = df[df.index.date >= start]
    if len(future) < days:
        return None

    start_price = future[close_col].iloc[0]
    end_price = future[close_col].iloc[days - 1]

    if start_price == 0 or pd.isna(start_price) or pd.isna(end_price):
        return None

    return (end_price - start_price) / start_price * 100


def _momentum_rank_at(ticker: str, eval_date: date, universe: list[str]) -> float | None:
    """
    Approximate the momentum percentile rank for `ticker` at `eval_date`.

    We use the full cached price series and pick the slice ending at eval_date.
    This is a simplification — in production you would use a point-in-time
    data source to avoid look-ahead bias.
    """
    df = get_price_history(ticker, "5y")
    if df is None or df.empty:
        return None

    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index(pd.to_datetime(df.index))

    df = df.sort_index()
    # Flatten MultiIndex columns if present (yfinance quirk).
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    close_col = next((c for c in df.columns if c.lower() == "close"), None)
    if close_col is None:
        return None

    # Slice to data available up to eval_date.
    hist = df[df.index.date <= eval_date]
    if len(hist) < 60:  # need at least ~3 months of daily data
        return None

    # 6-month return from the slice.
    # Use ~130 trading days as a proxy for 6 months.
    lookback = min(130, len(hist))
    start_price = hist[close_col].iloc[-lookback]
    end_price = hist[close_col].iloc[-1]
    if start_price == 0 or pd.isna(start_price) or pd.isna(end_price):
        return None

    ticker_return = (end_price - start_price) / start_price * 100

    # Peer returns (simplified: use current 6m returns for peers — acceptable
    # for this validation exercise).
    peer_returns = []
    for peer in universe:
        if peer == ticker:
            continue
        pr = _period_return(peer, 6)
        if pr is not None:
            peer_returns.append(pr)

    if not peer_returns:
        return None

    from backend.agents.step1b_momentum_agent import _percentile_rank
    return _percentile_rank(ticker_return, peer_returns + [ticker_return])


# ---------------------------------------------------------------------------
# Main validation loop
# ---------------------------------------------------------------------------


def main() -> None:
    quarter_starts = _get_quarter_starts(n=8)
    universe = FALLBACK_UNIVERSE

    print(f"Evaluating momentum signal for {len(universe)} tickers "
          f"across {len(quarter_starts)} quarter-start dates...\n")

    ranks: list[float] = []
    fwd_returns: list[float] = []
    labels: list[str] = []

    for ticker in universe:
        for q in quarter_starts:
            rank = _momentum_rank_at(ticker, q, universe)
            fwd = _forward_return(ticker, q)
            if rank is not None and fwd is not None:
                ranks.append(rank)
                fwd_returns.append(fwd)
                labels.append(f"{ticker}@{q}")

    if len(ranks) < 2:
        print("WARNING: Not enough data points to compute a meaningful correlation.")
        print(f"   Got {len(ranks)} point(s). Try running after market hours when "
              "yfinance has more data available.")
        return

    # Pearson correlation.
    r, p_value = stats.pearsonr(ranks, fwd_returns)

    print("=" * 60)
    print(f"  Data points : {len(ranks)}")
    print(f"  Pearson r   : {r:.4f}")
    print(f"  p-value     : {p_value:.4f}")
    print("=" * 60)

    if abs(r) < 0.1:
        print("Interpretation: Negligible correlation — the 6-month momentum")
        print("percentile rank shows little predictive power for 1-month returns")
        print("in this sample.")
    elif r > 0:
        print(f"Interpretation: Positive correlation (r={r:.2f}) — higher momentum")
        print("ranks tend to be followed by higher 1-month forward returns.")
    else:
        print(f"Interpretation: Negative correlation (r={r:.2f}) — unexpected;")
        print("check data quality or sample size.")

    # Scatter plot.
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(ranks, fwd_returns, alpha=0.5, s=30, color="steelblue")

    # Regression line.
    m, b = [r * (max(fwd_returns) - min(fwd_returns)) / 100, sum(fwd_returns) / len(fwd_returns)]
    xs = [0, 100]
    slope, intercept, *_ = stats.linregress(ranks, fwd_returns)
    ax.plot(xs, [slope * x + intercept for x in xs], color="tomato", lw=2, label=f"r = {r:.3f}")

    ax.set_xlabel("6-Month Momentum Percentile Rank at Quarter Start", fontsize=12)
    ax.set_ylabel("Actual 1-Month Forward Return (%)", fontsize=12)
    ax.set_title("Momentum Signal Validation", fontsize=13)
    ax.legend()
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(OUTPUT_PATH, dpi=150)
    print(f"\nScatter plot saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
