"""
frontend/streamlit_app.py — The user-facing web interface.

This file only talks to the backend over HTTP — it does NOT import any
backend code directly.  This keeps the two layers cleanly separated.

Layout
------
  Sidebar   : Project disclaimer / limitations note.
  Main area : Mode selector → ticker input (manual) → Analyze button.
              Results are shown as per-ticker cards after the API call.
"""

from __future__ import annotations

import time

import requests
import streamlit as st

# The backend URL.  Change this if you run the backend on a different host/port.
BACKEND_URL = "http://localhost:8000"


# ---------------------------------------------------------------------------
# Helpers  (defined here so they're available to all code below)
# ---------------------------------------------------------------------------


def _fmt(value, fmt: str, suffix: str = "") -> str:
    """Format a numeric value for display, returning 'N/A' if None."""
    if value is None:
        return "N/A"
    try:
        return f"{value:{fmt}}{suffix}"
    except (TypeError, ValueError):
        return str(value)

# ---------------------------------------------------------------------------
# Page config (must be the first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Fin-Vantage Scout",
    page_icon="📈",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar — limitations note
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("📈 Fin-Vantage Scout")
    st.caption("Multi-Agent Equity Screener")
    st.divider()
    st.markdown(
        """
        ### ⚠️ Portfolio Project

        This is an **educational demo** built with free, open-source tools.

        **Known limitations**
        - Market data from Yahoo Finance (15-min delay, may have gaps)
        - News via DuckDuckGo (no real-time feed)
        - LLM ratings are heuristic, not research-grade
        - No fundamental trend history (only latest snapshot)

        **Not investment advice.**
        Do your own due diligence before making any financial decisions.
        """,
        unsafe_allow_html=False,
    )
    st.divider()
    st.markdown("**Tech stack**")
    st.markdown("FastAPI · LangGraph · Groq LLaMA · yfinance · DuckDuckGo Search")

# ---------------------------------------------------------------------------
# Main area — header
# ---------------------------------------------------------------------------

st.title("📊 Fin-Vantage Scout")
st.subheader("AI-powered, multi-agent stock screener for learning purposes")
st.divider()

# ---------------------------------------------------------------------------
# Mode selector
# ---------------------------------------------------------------------------

mode = st.radio(
    "**Analysis mode**",
    options=["manual", "auto_screen"],
    format_func=lambda m: "✏️ Manual — enter your own tickers"
    if m == "manual"
    else "🔍 Auto-Screen — top 10 from FFTY ETF universe",
    horizontal=True,
)

tickers_input: list[str] = []

if mode == "manual":
    raw = st.text_input(
        "Tickers (comma-separated)",
        placeholder="e.g. AAPL, NVDA, MSFT",
        help="Enter up to ~10 tickers. More tickers = longer wait.",
    )
    tickers_input = [t.strip().upper() for t in raw.split(",") if t.strip()]
    if tickers_input:
        st.caption(f"Will analyse: {', '.join(tickers_input)}")
else:
    st.info(
        "Auto-screen will fetch the current FFTY ETF holdings, run a momentum "
        "pre-screen, and analyse the top 10 candidates. This may take 1-2 minutes.",
        icon="ℹ️",
    )

st.divider()

# ---------------------------------------------------------------------------
# Analyze button
# ---------------------------------------------------------------------------

can_run = (mode == "auto_screen") or (mode == "manual" and len(tickers_input) > 0)

if st.button("🚀 Run Analysis", disabled=not can_run, type="primary", use_container_width=True):
    payload: dict = {"mode": mode}
    if mode == "manual":
        payload["tickers"] = tickers_input

    with st.spinner("Running agents… this may take a minute or two ⏳"):
        try:
            t0 = time.time()
            resp = requests.post(f"{BACKEND_URL}/analyze", json=payload, timeout=300)
            elapsed = time.time() - t0

            if resp.status_code != 200:
                st.error(f"Backend returned HTTP {resp.status_code}: {resp.text}")
                st.stop()

            data = resp.json()
            st.success(f"Analysis complete in {elapsed:.1f}s — {len(data['results'])} ticker(s) analysed.")

        except requests.exceptions.ConnectionError:
            st.error(
                f"Could not connect to the backend at {BACKEND_URL}. "
                "Make sure the backend is running:\n\n"
                "```\nuv run uvicorn backend.app:app --reload\n```"
            )
            st.stop()
        except Exception as exc:
            st.error(f"Unexpected error: {exc!r}")
            st.stop()

    # -----------------------------------------------------------------------
    # Results — one card per ticker
    # -----------------------------------------------------------------------

    RATING_COLORS = {
        "Attractive": "🟢",
        "Neutral": "🟡",
        "Caution": "🔴",
    }

    RATING_CSS = {
        "Attractive": "background-color:#1a472a;color:#69db7c;",
        "Neutral": "background-color:#3b3a1c;color:#ffe066;",
        "Caution": "background-color:#4a1010;color:#ff6b6b;",
    }

    for item in data["results"]:
        ticker = item["ticker"]
        synthesis = item["synthesis"]
        fundamentals = item["fundamentals"]
        momentum = item["momentum"]
        news = item["news"]

        rating = synthesis["rating"]
        emoji = RATING_COLORS.get(rating, "⚪")
        css = RATING_CSS.get(rating, "")

        with st.container():
            st.markdown("---")
            # Rating badge + ticker header
            col_badge, col_title = st.columns([1, 5])
            with col_badge:
                st.markdown(
                    f'<div style="padding:12px 16px;border-radius:8px;text-align:center;'
                    f'font-size:1.1rem;font-weight:700;{css}">'
                    f"{emoji} {rating}</div>",
                    unsafe_allow_html=True,
                )
            with col_title:
                st.markdown(f"## {ticker}")

            # Synthesis explanation
            st.markdown(f"**Analysis:** {synthesis['explanation']}")

            # Disclaimer — always visible
            st.warning(synthesis["disclaimer"], icon="⚠️")

            # Expandable raw data sections
            with st.expander("📐 Fundamentals"):
                col1, col2, col3 = st.columns(3)
                col1.metric("Current Ratio", _fmt(fundamentals.get("current_ratio"), ".2f"))
                col2.metric("Debt/Equity", _fmt(fundamentals.get("debt_to_equity"), ".2f"))
                col3.metric("ROE", _fmt(fundamentals.get("roe"), ".1%"))
                col1.metric("Gross Margin", _fmt(fundamentals.get("gross_margin"), ".1%"))
                col2.metric("Margin Trend", fundamentals.get("gross_margin_trend") or "N/A")
                col3.metric("Data Available", "✅ Yes" if fundamentals.get("data_available") else "❌ No")

            with st.expander("📈 Momentum"):
                col1, col2 = st.columns(2)
                col1.metric("6-Month Return", _fmt(momentum.get("return_6m"), ".1f", suffix="%"))
                col2.metric("12-Month Return", _fmt(momentum.get("return_12m"), ".1f", suffix="%"))
                col1.metric("6M Percentile Rank", _fmt(momentum.get("percentile_rank_6m"), ".0f", suffix="th"))
                col2.metric("12M Percentile Rank", _fmt(momentum.get("percentile_rank_12m"), ".0f", suffix="th"))

            with st.expander("📰 News Sentiment"):
                sentiment = news["sentiment"]
                s_color = {"Positive": "🟢", "Neutral": "🟡", "Negative": "🔴"}.get(sentiment, "⚪")
                st.markdown(f"**Sentiment:** {s_color} {sentiment}")
                st.markdown(f"**Justification:** {news['justification']}")
                if news["headlines"]:
                    st.markdown("**Headlines used:**")
                    for h in news["headlines"]:
                        st.markdown(f"- {h}")
                else:
                    st.caption("No headlines retrieved.")

