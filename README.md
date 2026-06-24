# Fin-Vantage Scout

A lean, beginner-readable **multi-agent equity screener** built as a
portfolio/educational project. It uses a LangGraph fan-out/fan-in pipeline
to analyse stocks and produce plain-English investment summaries.

> **⚠️ Not investment advice.** This is an educational demo using free, open-source
> tools. Data has inherent delays and limitations. Never make financial decisions
> based solely on this tool.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI  (backend/app.py)                 │
│                                                                   │
│  POST /analyze                                                    │
│      │                                                            │
│      │  LangGraph fan-out                                         │
│      ├──► fundamentals_node  (yfinance + Alpha Vantage fallback)  │
│      ├──► momentum_node      (yfinance rank + Alpha Vantage RSI)  │
│      └──► news_sentiment_node (DuckDuckGo → LLM light)           │
│                │                                                  │
│                └──► synthesis_node (LLM heavy → rating)          │
└─────────────────────────────────────────────────────────────────┘
         ▲ HTTP POST /analyze
┌────────┴────────────┐
│  Streamlit frontend  │
│  (frontend/streamlit_app.py) │
└─────────────────────┘
```

## Stock Selection Modes

### Manual
Provide a comma-separated list of tickers in the UI. Used as-is.

### Auto-Screen
Fetches the current holdings of the **Innovator IBD 50 ETF (FFTY)** from
Innovator's public ETF disclosure page — no login, no scraping of IBD's
paid platform. Holdings are cached in SQLite for 24 hours.

A momentum pre-screen ranks all candidates by 6-month return percentile and
keeps the **top 10** for full analysis (conserves free-tier LLM quota).

**Disclosure:** The momentum score computed here is an independent calculation
based on publicly available price data. It is not a reproduction of IBD's
proprietary Relative Strength Rating or any other commercial score.

---

## Setup

### Prerequisites
- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) — install with `pip install uv` or follow the uv docs
- (Optional) [Ollama](https://ollama.com) for local LLM fallback

### 1. Clone and install dependencies

```bash
git clone <your-repo-url>
cd fin-vantage-scout
uv sync
```

### 2. Set your API keys

Create a `.env` file in the project root (this file is git-ignored):

```
GROQ_API_KEY=your_groq_key_here
ALPHA_VANTAGE_API_KEY=your_av_key_here
```

**Groq (Primary LLM):** Get a free API key at [console.groq.com](https://console.groq.com).
The system falls back to local Ollama automatically if this key is missing or fails.

**Alpha Vantage (Secondary Data):** Get a free API key at [alphavantage.co](https://www.alphavantage.co/support/#api-key).
- Used as a fallback for missing fundamental data, and for independent RSI momentum signals.
- **Quota Safety:** The free tier allows 25 calls/day. When configured, the `/analyze` endpoint enforces a strict cap of **10 tickers per run** to prevent burning your daily limit.
- If left unset, Alpha Vantage calls are silently skipped.

### 3. (Optional) Set up Ollama fallback

```bash
ollama pull llama3.2:3b
```

---

## Running the Project

All commands should be run **from the project root** so `config.py` is importable.

### Start the application

```bash
uv run uvicorn backend.app:app --reload
```

The application serves the UI directly. Open your browser to:
`http://localhost:8000`

Verify the backend is running via the API:
```bash
curl http://localhost:8000/health
# → {"status":"ok","service":"fin-vantage-scout"}
```

---

## Signal Validation

```bash
uv run python tests/evaluate_momentum_signal.py
```

This script evaluates whether the momentum percentile rank actually predicts
1-month forward returns. It prints the Pearson correlation coefficient and
saves a scatter plot to `outputs/momentum_signal_scatter.png`.

Results are reported as-is — not tuned to look good.

---

## File Structure

```
fin-vantage-scout/
├── pyproject.toml               # uv project config + dependencies
├── config.py                    # LLM factory, fallback universe, TTLs
├── cache.db                     # SQLite cache (auto-created on first run)
├── backend/
│   ├── app.py                   # LangGraph graph + FastAPI routes
│   ├── agents/
│   │   ├── step1a_fundamentals_agent.py
│   │   ├── step1b_momentum_agent.py
│   │   ├── step1c_news_agent.py
│   │   └── step2_synthesis_agent.py
│   ├── prompts/
│   │   ├── news_prompt.py
│   │   └── synthesis_prompt.py
│   └── data/
│       ├── market_data.py       # yfinance wrappers + SQLite cache helpers
│       └── universe.py          # Manual / auto-screen universe selection
├── frontend/
│   └── streamlit_app.py         # Streamlit UI (HTTP only, no backend imports)
├── tests/
│   └── evaluate_momentum_signal.py  # Momentum signal validation
└── outputs/
    └── momentum_signal_scatter.png  # (auto-created by tests script)
```

---

## What's Next

Planned future agents (deferred for clarity in v1):

- **Valuation agent** — P/E, P/S, EV/EBITDA vs. sector median
- **Moat agent** — gross margin stability, brand signal from search trends
- **Earnings-quality agent** — accruals ratio, cash conversion
- **Backtesting harness** — systematic forward-return measurement for each new signal

Each new agent would get its own `stepN_*.py` file and a corresponding
validation script in `tests/`.
