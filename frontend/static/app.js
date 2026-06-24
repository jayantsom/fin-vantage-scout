/**
 * frontend/static/app.js
 * ───────────────────────
 * All client-side logic for Fin-Vantage Scout.
 *
 * No framework, no build step.  Plain ES2022 with fetch() and DOM APIs.
 *
 * Sections
 * --------
 *   1. Constants & state
 *   2. Health check
 *   3. Mode switcher
 *   4. Ticker input handling
 *   5. API call + loading state
 *   6. Card rendering (the big piece)
 *   7. Company name lookup (ticker → full name)
 *   8. Utility helpers
 *   9. Scroll helpers
 *  10. Init on DOMContentLoaded
 */

/* ═══════════════════════════════════════════════════════════════
   1. Constants & state
═══════════════════════════════════════════════════════════════ */

const BACKEND_URL = "http://localhost:8000";

/** Current analysis mode — kept in sync with the UI. */
let currentMode = "manual";

/** Parsed list of tickers from the text input. */
let currentTickers = [];

/* ─── Theme Management ─── */
function initTheme() {
  const saved = localStorage.getItem("theme");
  const systemPrefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = saved === "dark" || (!saved && systemPrefersDark);
  
  if (isDark) {
    document.body.classList.remove("light-mode");
  } else {
    document.body.classList.add("light-mode");
  }
  updateThemeIcon(!isDark);
}

function toggleTheme() {
  const isCurrentlyLight = document.body.classList.toggle("light-mode");
  localStorage.setItem("theme", isCurrentlyLight ? "light" : "dark");
  updateThemeIcon(isCurrentlyLight);
}

function updateThemeIcon(isLight) {
  const icon = document.getElementById("theme-icon");
  if (!icon) return;
  if (isLight) {
    icon.innerHTML = `<path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" />`;
  } else {
    icon.innerHTML = `<path stroke-linecap="round" stroke-linejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />`;
  }
}

/* ─── Sidebar Collapse ─── */
function toggleSidebar() {
  const shell = document.getElementById("app-shell");
  shell.classList.toggle("sidebar-collapsed");
}

/* ─── Reset Application ─── */
function resetApp() {
  document.getElementById("ticker-input").value = "";
  currentTickers = [];
  updateRunButton();
  renderTickerPreview();
  clearError();
  hideResults();
  setTab("screener");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ─── Tab Switching ─── */
function setTab(tabId) {
  // Update sidebar active buttons
  document.getElementById("nav-screener")?.classList.toggle("active", tabId === "screener");
  document.getElementById("nav-methodology")?.classList.toggle("active", tabId === "methodology");

  // Show/hide tab panels
  document.getElementById("tab-screener").classList.toggle("active", tabId === "screener");
  document.getElementById("tab-methodology").classList.toggle("active", tabId === "methodology");
}

/* ─── Metric Documentation Clicking ─── */
function showMetricDoc(metricId) {
  setTab("methodology");
  
  const targetCard = document.getElementById(`doc-${metricId}`);
  if (targetCard) {
    setTimeout(() => {
      targetCard.scrollIntoView({ behavior: "smooth", block: "center" });
      targetCard.classList.add("highlight-pulse");
      
      // Remove the highlight class after the pulse animation runs
      setTimeout(() => {
        targetCard.classList.remove("highlight-pulse");
      }, 2000);
    }, 150);
  }
}

/* ═══════════════════════════════════════════════════════════════
   2. Health check  — polls /health and updates the dot in the topbar
═══════════════════════════════════════════════════════════════ */

async function checkHealth() {
  const dot   = document.getElementById("health-dot");
  const label = document.getElementById("health-label");
  try {
    const res = await fetch(`${BACKEND_URL}/health`, { signal: AbortSignal.timeout(4000) });
    if (res.ok) {
      dot.className   = "health-dot online";
      label.textContent = "API Online";
    } else {
      throw new Error("non-200");
    }
  } catch {
    dot.className   = "health-dot offline";
    label.textContent = "API Offline";
  }
}

/* ═══════════════════════════════════════════════════════════════
   3. Mode switcher
═══════════════════════════════════════════════════════════════ */

function setMode(mode) {
  currentMode = mode;

  const btnManual = document.getElementById("btn-manual");
  const btnAuto   = document.getElementById("btn-auto");
  const manualRow = document.getElementById("manual-input-row");
  const autoRow   = document.getElementById("auto-screen-row");
  const preview   = document.getElementById("ticker-preview");

  if (mode === "manual") {
    btnManual.classList.add("active");
    btnAuto.classList.remove("active");
    btnManual.setAttribute("aria-pressed", "true");
    btnAuto.setAttribute("aria-pressed", "false");
    manualRow.style.display = "flex";
    autoRow.style.display   = "none";
    preview.style.display   = "block";
    updateRunButton();
  } else {
    btnAuto.classList.add("active");
    btnManual.classList.remove("active");
    btnAuto.setAttribute("aria-pressed", "true");
    btnManual.setAttribute("aria-pressed", "false");
    autoRow.style.display   = "block";
    manualRow.style.display = "none";
    preview.style.display   = "none";
  }
}

/* ═══════════════════════════════════════════════════════════════
   4. Ticker input handling
═══════════════════════════════════════════════════════════════ */

function onTickerInput() {
  const raw = document.getElementById("ticker-input").value;
  currentTickers = raw
    .split(",")
    .map(t => t.trim().toUpperCase())
    .filter(t => t.length > 0 && /^[A-Z.^-]{1,10}$/.test(t));
  updateRunButton();
  renderTickerPreview();
}

/** Allow pressing Enter to submit. */
function onTickerKeydown(event) {
  if (event.key === "Enter" && currentTickers.length > 0) {
    runAnalysis();
  }
}

function renderTickerPreview() {
  const preview = document.getElementById("ticker-preview");
  if (currentTickers.length === 0) {
    preview.textContent = "";
    return;
  }
  preview.innerHTML = currentTickers
    .map(t => `<span>${t}</span>`)
    .join("") + `<span style="color:var(--text-muted);">  ·  ${currentTickers.length} ticker${currentTickers.length > 1 ? "s" : ""}</span>`;
}

function updateRunButton() {
  const btn = document.getElementById("run-btn");
  if (btn) {
    btn.disabled = currentTickers.length === 0;
  }
}

/* ═══════════════════════════════════════════════════════════════
   5. API call + loading state
═══════════════════════════════════════════════════════════════ */

async function runAnalysis() {
  clearError();
  hideResults();
  showLoading(true);

  const payload = { mode: currentMode };
  if (currentMode === "manual") {
    payload.tickers = currentTickers;
  }

  try {
    const t0 = Date.now();
    updateLoadingStage("Dispatching agents…", "Fan-out: fundamentals · momentum · news running in parallel");

    const res = await fetch(`${BACKEND_URL}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`Backend returned HTTP ${res.status}: ${detail}`);
    }

    const data = await res.json();
    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

    showLoading(false);
    renderResults(data, elapsed);

  } catch (err) {
    showLoading(false);
    let msg = err.message || String(err);
    if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
      msg = `Could not connect to the backend at <code>${BACKEND_URL}</code>. `
          + `Make sure the backend is running:<br><br>`
          + `<code>uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload</code>`;
    }
    showError(msg);
  }
}

function showLoading(visible) {
  const el = document.getElementById("loading-overlay");
  el.classList.toggle("visible", visible);
}

function updateLoadingStage(stage, detail) {
  const s = document.getElementById("loading-stage");
  const d = document.getElementById("loading-detail");
  if (s) s.textContent = stage;
  if (d) d.textContent = detail;
}

function showError(htmlMessage) {
  const banner = document.getElementById("error-banner");
  const text   = document.getElementById("error-text");
  text.innerHTML = htmlMessage;
  banner.classList.add("visible");
}

function clearError() {
  document.getElementById("error-banner").classList.remove("visible");
}

function hideResults() {
  document.getElementById("results-container").classList.remove("visible");
  document.getElementById("results-grid").innerHTML = "";
}

/* ═══════════════════════════════════════════════════════════════
   6. Card rendering
═══════════════════════════════════════════════════════════════ */

function renderResults(data, elapsed) {
  const container = document.getElementById("results-container");
  const grid      = document.getElementById("results-grid");
  const metaText  = document.getElementById("results-meta-text");

  const count = data.results.length;
  metaText.innerHTML = `<strong>${count}</strong> ticker${count !== 1 ? "s" : ""} analysed — completed in <strong>${elapsed}s</strong>`;

  grid.innerHTML = "";
  data.results.forEach((item, idx) => {
    const card = buildStockCard(item);
    // Stagger card entrance animation
    card.style.animationDelay = `${idx * 60}ms`;
    grid.appendChild(card);
  });

  container.classList.add("visible");

  // Scroll results into view
  setTimeout(() => {
    container.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 100);
}

/**
 * Build a complete stock card DOM element for one TickerAnalysis object.
 * This is the largest function — it mirrors the data structure returned
 * by the FastAPI /analyze endpoint.
 */
function buildStockCard(item) {
  const { ticker, synthesis, fundamentals, momentum, news } = item;
  const rating    = synthesis.rating;   // "Attractive" | "Neutral" | "Caution"
  const sentiment = news.sentiment;     // "Positive"   | "Neutral" | "Negative"

  // Look up full company name from the yfinance fetch, fallback to dictionary, then ticker
  const companyName = fundamentals.company_name || COMPANY_NAMES[ticker] || ticker;

  // Rating dot character
  const ratingDot = { Attractive: "●", Neutral: "●", Caution: "●" }[rating] ?? "●";

  // ── Build the card element ──────────────────────────────────────
  const card = document.createElement("article");
  card.className = "stock-card";
  card.setAttribute("data-ticker", ticker);

  // Top colour stripe
  card.innerHTML += `<div class="card-stripe stripe-${rating.toLowerCase()}"></div>`;

  // Header row: ticker + company name + rating badge
  card.innerHTML += `
    <div class="card-header">
      <div class="card-ticker-block">
        <div class="card-ticker">${escHtml(ticker)}</div>
        <div class="card-company">${escHtml(companyName)}</div>
      </div>
      <div class="rating-badge badge-${rating.toLowerCase()}">
        <span>${ratingDot}</span>
        ${escHtml(rating)}
      </div>
    </div>`;

  // AI synthesis explanation
  card.innerHTML += `
    <div class="card-analysis">
      ${escHtml(synthesis.explanation || "No synthesis available.")}
    </div>`;

  // Fundamental metrics grid (4 cells) - Interactive clicks to Methodology
  const cr    = fmtNum(fundamentals.current_ratio,  ".2f");
  const de    = fmtNum(fundamentals.debt_to_equity, ".2f");
  const roe   = fmtPct(fundamentals.roe);
  const gm    = fmtPct(fundamentals.gross_margin);
  const gmTrend = fundamentals.gross_margin_trend ?? "N/A";

  card.innerHTML += `
    <div class="metrics-grid">
      <div class="metric-cell metric-clickable" onclick="showMetricDoc('current-ratio')" title="Click to view definition & methodology">
        <div class="metric-label">Current Ratio</div>
        <div class="metric-value ${numClass(fundamentals.current_ratio)}">${cr}</div>
        <div class="metric-sub">Liquidity</div>
      </div>
      <div class="metric-cell metric-clickable" onclick="showMetricDoc('debt-to-equity')" title="Click to view definition & methodology">
        <div class="metric-label">Debt / Equity</div>
        <div class="metric-value ${fundamentals.debt_to_equity !== null && fundamentals.debt_to_equity > 200 ? "negative" : ""}">${de}</div>
        <div class="metric-sub">Leverage</div>
      </div>
      <div class="metric-cell metric-clickable" onclick="showMetricDoc('roe')" title="Click to view definition & methodology">
        <div class="metric-label">ROE</div>
        <div class="metric-value ${fundamentals.roe !== null ? (fundamentals.roe > 0 ? "positive" : "negative") : "na"}">${roe}</div>
        <div class="metric-sub">Profitability</div>
      </div>
      <div class="metric-cell metric-clickable" onclick="showMetricDoc('gross-margin')" title="Click to view definition & methodology">
        <div class="metric-label">Gross Margin</div>
        <div class="metric-value">${gm}</div>
        <div class="metric-sub">${escHtml(gmTrend)}</div>
      </div>
    </div>`;

  // Footer row: news sentiment (left) + momentum bars (right) - Interactive clicks to Methodology
  const r6m   = momentum.return_6m;
  const r12m  = momentum.return_12m;
  const pct6  = momentum.percentile_rank_6m;
  const pct12 = momentum.percentile_rank_12m;
  const rsi   = momentum.rsi_14;

  // Unique ID for the headlines list so the toggle button works
  const hlId = `hl-${ticker}-${Date.now()}`;

  card.innerHTML += `
    <div class="card-footer-row">

      <!-- News sentiment -->
      <div class="footer-section metric-clickable" onclick="showMetricDoc('news')" title="Click to view sentiment logic">
        <div class="footer-section-label">News Sentiment</div>
        <div class="sentiment-chip sentiment-${sentiment} metric-clickable-badge">
          ${sentimentDot(sentiment)} ${escHtml(sentiment)}
        </div>
        <div class="sentiment-justification" style="margin-top:6px;">
          ${escHtml(news.justification || "")}
        </div>
        <!-- Stop propagation on headline toggle click so it doesn't navigate to methodology -->
        <div onclick="event.stopPropagation();">
          ${buildHeadlinesToggle(news.headlines, hlId)}
        </div>
      </div>

      <!-- Momentum -->
      <div class="footer-section metric-clickable" onclick="showMetricDoc('momentum')" title="Click to view momentum ranking logic">
        <div class="footer-section-label">Momentum & RSI</div>
        <div class="momentum-bars">
          ${buildMomentumBar("6M", r6m, pct6)}
          ${buildMomentumBar("12M", r12m, pct12)}
          ${buildMomentumRSIBar("RSI 14", rsi)}
        </div>
        <div style="margin-top:6px;font-size:10px;color:var(--text-muted);">
          Returns vs peers, plus 14D RSI
        </div>
      </div>

    </div>`;

  return card;
}

/** Build a single momentum bar row. */
function buildMomentumBar(label, returnPct, percentile) {
  const retStr = returnPct !== null ? `${returnPct > 0 ? "+" : ""}${returnPct.toFixed(1)}%` : "N/A";
  const pctStr = percentile !== null ? `${Math.round(percentile)}th pct` : "–";

  // Bar width based on percentile; colour based on return sign
  const barWidth  = percentile !== null ? Math.max(2, Math.round(percentile)) : 0;
  const fillClass = percentile === null ? "" : percentile >= 60 ? "fill-attractive" : percentile >= 35 ? "fill-neutral" : "fill-caution";
  const valColor  = returnPct === null ? "var(--text-muted)" : returnPct > 0 ? "var(--c-attractive)" : "var(--c-caution)";

  return `
    <div class="momentum-bar-row">
      <span class="momentum-bar-label">${label}</span>
      <div class="momentum-bar-track">
        <div class="momentum-bar-fill ${fillClass}" style="width:${barWidth}%"></div>
      </div>
      <span class="momentum-bar-val" style="color:${valColor};">${retStr} <span style="color:var(--text-muted);font-size:9px;">${pctStr}</span></span>
    </div>`;
}

/** Build a single momentum bar row for RSI. */
function buildMomentumRSIBar(label, rsiVal) {
  const rsiStr = rsiVal !== null ? rsiVal.toFixed(1) : "N/A";
  const barWidth = rsiVal !== null ? Math.min(100, Math.max(0, Math.round(rsiVal))) : 0;
  
  let fillClass = "";
  let valColor = "var(--text-muted)";
  
  if (rsiVal !== null) {
    if (rsiVal >= 60) {
      fillClass = "fill-attractive";
      valColor = "var(--c-attractive)";
    } else if (rsiVal <= 40) {
      fillClass = "fill-caution";
      valColor = "var(--c-caution)";
    } else {
      fillClass = "fill-neutral";
      valColor = "var(--text-primary)";
    }
  }

  return `
    <div class="momentum-bar-row">
      <span class="momentum-bar-label" title="14-Day Relative Strength Index (Alpha Vantage)">${label}</span>
      <div class="momentum-bar-track">
        <div class="momentum-bar-fill ${fillClass}" style="width:${barWidth}%"></div>
      </div>
      <span class="momentum-bar-val" style="color:${valColor};">${rsiStr}</span>
    </div>`;
}

/** Build the "N headlines" toggle + collapsible list. */
function buildHeadlinesToggle(headlines, id) {
  if (!headlines || headlines.length === 0) {
    return `<div style="margin-top:4px;font-size:10px;color:var(--text-muted);">No headlines retrieved.</div>`;
  }

  const items = headlines
    .map(h => `<div class="headline-item"><span class="headline-bullet">›</span>${escHtml(h)}</div>`)
    .join("");

  return `
    <button class="headlines-toggle" onclick="toggleHeadlines('${id}', this)" aria-expanded="false" aria-controls="${id}">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M19 9l-7 7-7-7"/>
      </svg>
      ${headlines.length} headline${headlines.length > 1 ? "s" : ""} used
    </button>
    <div class="headlines-list" id="${id}" aria-hidden="true">
      ${items}
    </div>`;
}

/** Toggle the headlines expander. */
function toggleHeadlines(id, btn) {
  const list = document.getElementById(id);
  if (!list) return;
  const isOpen = list.classList.toggle("open");
  btn.setAttribute("aria-expanded", isOpen);
  list.setAttribute("aria-hidden", !isOpen);
  // Flip the chevron via inline style
  btn.querySelector("svg").style.transform = isOpen ? "rotate(180deg)" : "";
}

/* ═══════════════════════════════════════════════════════════════
   7. Company name lookup table
   Source: well-known US equities.  Fallback = ticker itself.
═══════════════════════════════════════════════════════════════ */

const COMPANY_NAMES = {
  AAPL: "Apple Inc.", MSFT: "Microsoft Corp.", GOOGL: "Alphabet Inc.", GOOG: "Alphabet Inc.",
  AMZN: "Amazon.com Inc.", NVDA: "NVIDIA Corp.", META: "Meta Platforms Inc.",
  TSLA: "Tesla Inc.", BRK: "Berkshire Hathaway", JPM: "JPMorgan Chase & Co.",
  JNJ: "Johnson & Johnson", V: "Visa Inc.", UNH: "UnitedHealth Group",
  XOM: "Exxon Mobil Corp.", WMT: "Walmart Inc.", PG: "Procter & Gamble Co.",
  MA: "Mastercard Inc.", HD: "Home Depot Inc.", CVX: "Chevron Corp.",
  ABBV: "AbbVie Inc.", KO: "Coca-Cola Co.", MRK: "Merck & Co.", PEP: "PepsiCo Inc.",
  AVGO: "Broadcom Inc.", COST: "Costco Wholesale Corp.", LLY: "Eli Lilly & Co.",
  TMO: "Thermo Fisher Scientific", DHR: "Danaher Corp.", ACN: "Accenture plc",
  MCD: "McDonald's Corp.", NEE: "NextEra Energy Inc.", NKE: "Nike Inc.",
  TXN: "Texas Instruments Inc.", QCOM: "Qualcomm Inc.", ORCL: "Oracle Corp.",
  AMD: "Advanced Micro Devices", INTC: "Intel Corp.", MU: "Micron Technology",
  AMAT: "Applied Materials Inc.", LRCX: "Lam Research Corp.", KLAC: "KLA Corp.",
  MRVL: "Marvell Technology", MPWR: "Monolithic Power Systems",
  NFLX: "Netflix Inc.", DIS: "Walt Disney Co.", PYPL: "PayPal Holdings",
  SHOP: "Shopify Inc.", SQ: "Block Inc.", ROKU: "Roku Inc.",
  UBER: "Uber Technologies", LYFT: "Lyft Inc.", ABNB: "Airbnb Inc.",
  COIN: "Coinbase Global", RBLX: "Roblox Corp.", SNAP: "Snap Inc.",
  TWTR: "Twitter / X", ZM: "Zoom Video Communications", DOCU: "DocuSign Inc.",
  CRM: "Salesforce Inc.", NOW: "ServiceNow Inc.", SNOW: "Snowflake Inc.",
  DDOG: "Datadog Inc.", NET: "Cloudflare Inc.", CRWD: "CrowdStrike Holdings",
  PANW: "Palo Alto Networks", ZS: "Zscaler Inc.", OKTA: "Okta Inc.",
  PLTR: "Palantir Technologies", SOFI: "SoFi Technologies", HOOD: "Robinhood Markets",
  BABA: "Alibaba Group", NIO: "NIO Inc.", XPEV: "XPeng Inc.", LI: "Li Auto Inc.",
  TSM: "Taiwan Semiconductor", ASML: "ASML Holding", SAP: "SAP SE",
  SMCI: "Super Micro Computer", DELL: "Dell Technologies", HPQ: "HP Inc.",
  IBM: "IBM Corp.", CSCO: "Cisco Systems", ADBE: "Adobe Inc.",
  INTU: "Intuit Inc.", ADSK: "Autodesk Inc.", ANSS: "ANSYS Inc.",
  CDNS: "Cadence Design Systems", SNPS: "Synopsys Inc.",
  GS: "Goldman Sachs Group", MS: "Morgan Stanley", BAC: "Bank of America",
  WFC: "Wells Fargo & Co.", C: "Citigroup Inc.", USB: "U.S. Bancorp",
  GE: "GE Aerospace", BA: "Boeing Co.", CAT: "Caterpillar Inc.",
  RTX: "RTX Corp.", LMT: "Lockheed Martin Corp.", NOC: "Northrop Grumman",
  DE: "Deere & Co.", MMM: "3M Co.", HON: "Honeywell International",
  EMR: "Emerson Electric Co.", ETN: "Eaton Corp.", ROK: "Rockwell Automation",
  SPGI: "S&P Global Inc.", ICE: "Intercontinental Exchange", CME: "CME Group",
  CB: "Chubb Ltd.", PGR: "Progressive Corp.", AIG: "American International Group",
  CVS: "CVS Health Corp.", CI: "Cigna Group", HUM: "Humana Inc.",
  AMGN: "Amgen Inc.", GILD: "Gilead Sciences", REGN: "Regeneron Pharmaceuticals",
  VRTX: "Vertex Pharmaceuticals", BIIB: "Biogen Inc.", MRNA: "Moderna Inc.",
  PFE: "Pfizer Inc.", BMY: "Bristol-Myers Squibb",
  ENPH: "Enphase Energy", FSLR: "First Solar Inc.", SEDG: "SolarEdge Technologies",
  PLUG: "Plug Power Inc.", BE: "Bloom Energy Corp.",
  AMT: "American Tower Corp.", PLD: "Prologis Inc.", EQIX: "Equinix Inc.",
  WELL: "Welltower Inc.", O: "Realty Income Corp.",
  SPY: "SPDR S&P 500 ETF", QQQ: "Invesco QQQ Trust", IWM: "iShares Russell 2000 ETF",
  FFTY: "Innovator IBD 50 ETF",
};

/* ═══════════════════════════════════════════════════════════════
   8. Utility helpers
═══════════════════════════════════════════════════════════════ */

/** Format a float with a printf-style format string (only ".Nf" supported). */
function fmtNum(value, fmt) {
  if (value === null || value === undefined) return "N/A";
  const decimals = parseInt((fmt.match(/\.(\d)f/) || ["", "2"])[1], 10);
  return value.toFixed(decimals);
}

/** Format a float as a percentage (value is in decimal form, e.g. 0.25 → 25.0%). */
function fmtPct(value) {
  if (value === null || value === undefined) return "N/A";
  return (value * 100).toFixed(1) + "%";
}

/** CSS class name for a numeric metric — "positive", "negative", or "na". */
function numClass(value) {
  if (value === null || value === undefined) return "na";
  return value >= 1.5 ? "positive" : value < 1 ? "negative" : "";
}

/** SVG dot icon for sentiment chips. */
function sentimentDot(sentiment) {
  const color = { Positive: "#22c55e", Neutral: "#f59e0b", Negative: "#ef4444" }[sentiment] ?? "#8a9bbf";
  return `<svg width="8" height="8" viewBox="0 0 8 8"><circle cx="4" cy="4" r="4" fill="${color}"/></svg>`;
}

/** Escape HTML special characters to prevent XSS from API responses. */
function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ═══════════════════════════════════════════════════════════════
   9. Scroll helpers
═══════════════════════════════════════════════════════════════ */

function scrollToPanel() {
  document.getElementById("control-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function scrollToResults() {
  const el = document.getElementById("results-container");
  if (el && el.classList.contains("visible")) {
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  } else {
    document.getElementById("control-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

/* ═══════════════════════════════════════════════════════════════
   10. Init on DOMContentLoaded
═══════════════════════════════════════════════════════════════ */

document.addEventListener("DOMContentLoaded", () => {
  // Initialize theme (Light/Dark mode)
  initTheme();

  // Check backend health immediately, then every 30 seconds
  checkHealth();
  setInterval(checkHealth, 30_000);

  // Set initial mode
  setMode("manual");

  // Focus the ticker input
  document.getElementById("ticker-input")?.focus();
});
