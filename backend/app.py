"""
backend/app.py — The central wiring file.

Phase 3 update: 7 parallel step-1 nodes fan into step2 synthesis.
A sector/PE pre-pass runs before the graph loop to compute peer_pes for
the valuation agent (batch-scoped, same-sector peers only).

Graph shape (fan-out / fan-in):

    START
      |
      +---> fundamentals_node      --+
      +---> momentum_node          --+
      +---> technical_node         --+
      +---> earnings_quality_node  --+--> synthesis_node --> END
      +---> valuation_node         --+
      +---> moat_node              --+
      +---> news_node (1g, LLM)    --+
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

# --------------------------------------------------------------------------
# Path fix: ensure the project root is on sys.path so `config` is importable.
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
from backend.agents.step1c_technical_agent import TechnicalResult, technical_node
from backend.agents.step1d_earnings_quality_agent import EarningsQualityResult, earnings_quality_node
from backend.agents.step1e_valuation_agent import ValuationResult, valuation_node
from backend.agents.step1f_moat_agent import MoatResult, moat_node
from backend.agents.step1g_news_agent import NewsSentimentResult, news_sentiment_node
from backend.agents.step2_synthesis_agent import SynthesisResult, synthesis_node
from backend.data.market_data import get_fundamentals_data
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

    ticker: str                                          # The stock being analysed
    universe: list[str]                                  # Peer list for momentum rank
    peer_pes: list[float]                                # Same-sector batch PE values (excl. self)

    # Step-1 results (filled by parallel nodes)
    fundamentals_result: FundamentalsResult
    momentum_result: MomentumResult
    news_result: NewsSentimentResult
    technical_result: TechnicalResult
    earnings_quality_result: EarningsQualityResult
    valuation_result: ValuationResult
    moat_result: MoatResult

    # Step-2 result (filled by synthesis node)
    synthesis_result: SynthesisResult


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """
    Build and compile the LangGraph analysis pipeline.

    7 step-1 nodes run in parallel; all feed into synthesis_node.
    """
    builder: StateGraph = StateGraph(GraphState)

    builder.add_node("fundamentals", fundamentals_node)
    builder.add_node("momentum", momentum_node)
    builder.add_node("news", news_sentiment_node)
    builder.add_node("technical", technical_node)
    builder.add_node("earnings_quality", earnings_quality_node)
    builder.add_node("valuation", valuation_node)
    builder.add_node("moat", moat_node)
    builder.add_node("synthesis", synthesis_node)

    # Fan-out: START triggers all 7 step-1 nodes in parallel.
    for node in ["fundamentals", "momentum", "news", "technical",
                 "earnings_quality", "valuation", "moat"]:
        builder.add_edge(START, node)
        builder.add_edge(node, "synthesis")

    builder.add_edge("synthesis", END)

    return builder.compile()


# Compile once at startup.
_graph = build_graph()


# ---------------------------------------------------------------------------
# Sector/PE pre-pass helper
# ---------------------------------------------------------------------------


def _build_sector_pe_map(tickers: list[str]) -> dict[str, tuple[str | None, float | None]]:
    """
    For each ticker in the batch, fetch sector and trailing P/E from the
    already-cached fundamentals data.  Returns {ticker: (sector, pe)}.

    This reuses the SQLite cache from get_fundamentals_data — no extra
    network calls if fundamentals were already fetched this session.
    """
    result: dict[str, tuple[str | None, float | None]] = {}
    for ticker in tickers:
        raw = get_fundamentals_data(ticker)
        sector = raw.get("sector") if raw else None
        pe_raw = raw.get("trailingPE") if raw else None
        pe: float | None = None
        if pe_raw is not None:
            try:
                f = float(pe_raw)
                pe = f if -500 <= f <= 2000 else None
            except (ValueError, TypeError):
                pass
        result[ticker] = (sector, pe)
    return result


def _get_peer_pes(ticker: str, sector_pe_map: dict[str, tuple[str | None, float | None]]) -> list[float]:
    """
    Return P/E values of same-sector batch peers, excluding `ticker` itself.
    Empty list if no sector match or fewer than 2 peers available.
    """
    my_sector, _ = sector_pe_map.get(ticker, (None, None))
    if not my_sector:
        return []

    peers = []
    for t, (sec, pe) in sector_pe_map.items():
        if t == ticker:
            continue
        if sec == my_sector and pe is not None:
            peers.append(pe)
    return peers


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
    """Per-ticker result bundling all agent outputs."""

    ticker: str
    fundamentals: FundamentalsResult
    momentum: MomentumResult
    news: NewsSentimentResult
    technical: TechnicalResult
    earnings_quality: EarningsQualityResult
    valuation: ValuationResult
    moat: MoatResult
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
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "null"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_FRONTEND = ROOT / "frontend"
app.mount("/static", StaticFiles(directory=_FRONTEND / "static"), name="static")
_templates = Jinja2Templates(directory=_FRONTEND / "templates")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_frontend(request: Request) -> HTMLResponse:
    """Serve the single-page frontend at the root URL."""
    try:
        return _templates.TemplateResponse(request, "index.html")
    except TypeError:
        return _templates.TemplateResponse("index.html", {"request": request})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(_FRONTEND / "favicon.ico")


@app.get("/health")
def health() -> dict:
    """Simple liveness check."""
    return {"status": "ok", "service": "fin-vantage-scout"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    """
    Run the full 7-agent pipeline on one or more tickers.

    Manual mode   : {"mode":"manual","tickers":["AAPL","MSFT"]}
    Auto-screen   : {"mode":"auto_screen"} — system picks top 10

    Returns a list of per-ticker results including all 7 agent outputs,
    composite_score, and momentum_volume_confirmed.
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
        if ALPHA_VANTAGE_API_KEY and len(tickers_cleaned) > ALPHA_VANTAGE_TICKERS_PER_RUN:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Too many tickers ({len(tickers_cleaned)}).  "
                    f"Alpha Vantage free tier is limited to 25 calls/day. "
                    f"Please analyse at most {ALPHA_VANTAGE_TICKERS_PER_RUN} tickers per run."
                ),
            )
        peer_universe = list(dict.fromkeys(tickers_cleaned + FALLBACK_UNIVERSE))
        universe = tickers_cleaned
    else:
        print("[app] Auto-screen mode — fetching FFTY candidates…")
        universe = get_auto_screen_candidates()
        peer_universe = universe

    print(f"[app] Analysing {len(universe)} ticker(s): {universe}")

    # --- Sector/PE pre-pass for valuation peer comparison ------------------
    # This reuses the SQLite cache — no extra network calls on warm runs.
    print("[app] Building sector/PE map for valuation peer comparison…")
    sector_pe_map = _build_sector_pe_map(universe)

    # --- Run the graph on each ticker --------------------------------------
    results: list[TickerAnalysis] = []
    for ticker in universe:
        peer_pes = _get_peer_pes(ticker, sector_pe_map)

        initial_state: GraphState = {
            "ticker": ticker,
            "universe": peer_universe,
            "peer_pes": peer_pes,
            # Default empty models so TypedDict keys are always present
            "fundamentals_result": FundamentalsResult(ticker=ticker, data_available=False),
            "momentum_result": MomentumResult(ticker=ticker),
            "news_result": NewsSentimentResult(ticker=ticker),
            "technical_result": TechnicalResult(ticker=ticker),
            "earnings_quality_result": EarningsQualityResult(ticker=ticker),
            "valuation_result": ValuationResult(ticker=ticker),
            "moat_result": MoatResult(ticker=ticker),
            "synthesis_result": SynthesisResult(ticker=ticker),
        }

        final_state: GraphState = _graph.invoke(initial_state)

        results.append(
            TickerAnalysis(
                ticker=ticker,
                fundamentals=final_state["fundamentals_result"],
                momentum=final_state["momentum_result"],
                news=final_state["news_result"],
                technical=final_state["technical_result"],
                earnings_quality=final_state["earnings_quality_result"],
                valuation=final_state["valuation_result"],
                moat=final_state["moat_result"],
                synthesis=final_state["synthesis_result"],
            )
        )

    return AnalyzeResponse(mode=request.mode, results=results)


if __name__ == "__main__":
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
