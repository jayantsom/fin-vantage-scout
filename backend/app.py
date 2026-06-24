"""
backend/app.py — The central wiring file.

This is the one place that knows the full shape of the pipeline:
  1. The LangGraph graph definition (fan-out / fan-in)
  2. The FastAPI routes that trigger the graph

Reading order for beginners
---------------------------
Start with GraphState to understand what data flows through the graph.
Then look at build_graph() to see how nodes connect.
Then look at the /analyze route to see how a web request triggers the graph.

LangGraph primer
----------------
LangGraph models computation as a directed graph where each node is a
function that receives the current "state" and returns a dict of updates.
The framework merges those updates back into the state before calling the
next node.  Parallel nodes run concurrently (in separate threads by
default) and their outputs are merged before the next node runs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

# --------------------------------------------------------------------------
# Path fix: ensure the project root is on sys.path so `config` is importable
# when uvicorn runs from the project root.
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

from backend.agents.step1a_fundamentals_agent import FundamentalsResult, fundamentals_node
from backend.agents.step1b_momentum_agent import MomentumResult, momentum_node
from backend.agents.step1c_news_agent import NewsSentimentResult, news_sentiment_node
from backend.agents.step2_synthesis_agent import SynthesisResult, synthesis_node
from backend.data.universe import get_auto_screen_candidates, get_manual_universe
from config import FALLBACK_UNIVERSE, ALPHA_VANTAGE_API_KEY, ALPHA_VANTAGE_TICKERS_PER_RUN


# ---------------------------------------------------------------------------
# Shared graph state
# ---------------------------------------------------------------------------


class GraphState(TypedDict):
    """
    The data container that flows through the LangGraph pipeline.

    Every node receives this dict and returns a subset of it to update.
    LangGraph merges updates automatically — nodes don't need to copy keys
    they didn't touch.
    """

    ticker: str                                    # The stock being analysed
    universe: list[str]                            # Peer list for momentum rank
    fundamentals_result: FundamentalsResult        # Filled by step1a
    momentum_result: MomentumResult                # Filled by step1b
    news_result: NewsSentimentResult               # Filled by step1c
    synthesis_result: SynthesisResult              # Filled by step2


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """
    Build and compile the LangGraph analysis pipeline.

    Graph shape (fan-out then fan-in):

        START
          |
          +---> fundamentals_node --+
          |                         |
          +---> momentum_node   ----+--> synthesis_node --> END
          |                         |
          +---> news_node      -----+

    The three step-1 nodes run in parallel.  LangGraph waits for all three
    to finish before calling synthesis_node.
    """
    # Create a graph builder that will hold our GraphState.
    builder: StateGraph = StateGraph(GraphState)

    # Register every node (function name → label in the graph).
    builder.add_node("fundamentals", fundamentals_node)
    builder.add_node("momentum", momentum_node)
    builder.add_node("news", news_sentiment_node)
    builder.add_node("synthesis", synthesis_node)

    # Fan-out: START triggers all three step-1 nodes in parallel.
    builder.add_edge(START, "fundamentals")
    builder.add_edge(START, "momentum")
    builder.add_edge(START, "news")

    # Fan-in: all three step-1 nodes feed into synthesis.
    builder.add_edge("fundamentals", "synthesis")
    builder.add_edge("momentum", "synthesis")
    builder.add_edge("news", "synthesis")

    # After synthesis, the graph ends.
    builder.add_edge("synthesis", END)

    return builder.compile()


# Compile once at startup — not on every request.
_graph = build_graph()


# ---------------------------------------------------------------------------
# FastAPI request / response models
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    """
    Request body for POST /analyze.

    mode    : "manual"      — user provides a list of tickers.
              "auto_screen" — system fetches FFTY holdings and screens them.
    tickers : Required when mode="manual", ignored when mode="auto_screen".
    """

    mode: Literal["manual", "auto_screen"]
    tickers: list[str] | None = None


class TickerAnalysis(BaseModel):
    """Per-ticker result bundling all four agent outputs."""

    ticker: str
    fundamentals: FundamentalsResult
    momentum: MomentumResult
    news: NewsSentimentResult
    synthesis: SynthesisResult


class AnalyzeResponse(BaseModel):
    """Top-level response for POST /analyze."""

    mode: str
    results: list[TickerAnalysis]


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Fin-Vantage Scout",
    description="Multi-agent equity screener — educational portfolio project.",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# CORS — allow the browser's fetch() calls to reach /analyze from any
# localhost origin (Streamlit used port 8501; the new HTML frontend is
# served directly by this FastAPI server on port 8000).
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "null"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static files + Jinja2 templates
# Paths are relative to this file (backend/app.py).
# ROOT is already defined above as the project root directory.
# ---------------------------------------------------------------------------

_FRONTEND = ROOT / "frontend"

# Mount /static → frontend/static/ so the browser can load CSS, JS, images.
app.mount("/static", StaticFiles(directory=_FRONTEND / "static"), name="static")

# Jinja2Templates points at frontend/templates/ which holds index.html.
_templates = Jinja2Templates(directory=_FRONTEND / "templates")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_frontend(request: Request) -> HTMLResponse:
    """Serve the single-page frontend at the root URL."""
    try:
        return _templates.TemplateResponse(request, "index.html")
    except TypeError:
        return _templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health() -> dict:
    """Simple liveness check. Returns 200 OK when the server is running."""
    return {"status": "ok", "service": "fin-vantage-scout"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """
    Run the full 4-agent pipeline on one or more tickers.

    Manual mode   : provide {"mode":"manual","tickers":["AAPL","MSFT"]}
    Auto-screen   : provide {"mode":"auto_screen"} — system picks top 10

    Returns a list of per-ticker results.  Each result includes raw data
    from all four agents plus the synthesised rating and explanation.
    """
    # --- Input validation ---------------------------------------------------
    if request.mode == "manual":
        if not request.tickers:
            raise HTTPException(
                status_code=422,
                detail="mode='manual' requires a non-empty 'tickers' list.",
            )
        tickers_cleaned = get_manual_universe(request.tickers)
        if not tickers_cleaned:
            raise HTTPException(
                status_code=422,
                detail="All provided tickers were empty or invalid.",
            )
        # Alpha Vantage quota guard: each ticker may consume up to 2 AV calls
        # (OVERVIEW + RSI).  Enforce a cap only when the key is configured.
        if ALPHA_VANTAGE_API_KEY and len(tickers_cleaned) > ALPHA_VANTAGE_TICKERS_PER_RUN:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Too many tickers ({len(tickers_cleaned)}).  "
                    f"Alpha Vantage free tier is limited to 25 calls/day. "
                    f"Please analyse at most {ALPHA_VANTAGE_TICKERS_PER_RUN} tickers per run."
                ),
            )
        # For momentum percentile ranking, we need a peer universe.  When the
        # user enters only a few tickers, we expand the ranking pool by merging
        # their tickers with FALLBACK_UNIVERSE (deduped) so every ticker gets a
        # meaningful percentile rank instead of null.
        peer_universe = list(dict.fromkeys(tickers_cleaned + FALLBACK_UNIVERSE))
        # The outer loop analyses only what the user requested.
        universe = tickers_cleaned
    else:
        # auto_screen mode: ignore tickers field entirely.
        print("[app] Auto-screen mode — fetching FFTY candidates…")
        universe = get_auto_screen_candidates()
        peer_universe = universe  # already a broad set

    print(f"[app] Analysing {len(universe)} ticker(s): {universe}")

    # --- Run the graph on each ticker --------------------------------------
    results: list[TickerAnalysis] = []
    for ticker in universe:
        initial_state: GraphState = {
            "ticker": ticker,
            "universe": peer_universe,  # broad peer set for percentile ranking
            # The three result fields start as None — LangGraph requires all
            # TypedDict keys to be present, so we fill them with empty models.
            "fundamentals_result": FundamentalsResult(ticker=ticker, data_available=False),
            "momentum_result": MomentumResult(ticker=ticker),
            "news_result": NewsSentimentResult(ticker=ticker),
            "synthesis_result": SynthesisResult(ticker=ticker),
        }

        # .invoke() runs the full graph synchronously and returns the final state.
        final_state: GraphState = _graph.invoke(initial_state)

        results.append(
            TickerAnalysis(
                ticker=ticker,
                fundamentals=final_state["fundamentals_result"],
                momentum=final_state["momentum_result"],
                news=final_state["news_result"],
                synthesis=final_state["synthesis_result"],
            )
        )

    return AnalyzeResponse(mode=request.mode, results=results)


# Allow running with `python backend/app.py` for quick local testing.
if __name__ == "__main__":
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
