/**
 * frontend/static/app.js
 * ───────────────────────
 * All client-side logic for Fin-Vantage Scout.
 *
 * No framework, no build step.  Plain ES2022 with fetch() and DOM APIs.
 */

const BACKEND_URL = "http://localhost:8000";

let currentMode = "manual";
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
  document.getElementById("nav-screener")?.classList.toggle("active", tabId === "screener");
  document.getElementById("nav-methodology")?.classList.toggle("active", tabId === "methodology");
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
      setTimeout(() => targetCard.classList.remove("highlight-pulse"), 2000);
    }, 150);
  }
}

/* ─── Health check ─── */
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

/* ─── Mode switcher ─── */
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

/* ─── Ticker input handling ─── */
function onTickerInput() {
  const raw = document.getElementById("ticker-input").value;
  currentTickers = raw
    .split(",")
    .map(t => t.trim().toUpperCase())
    .filter(t => t.length > 0 && /^[A-Z.^-]{1,10}$/.test(t));
  updateRunButton();
  renderTickerPreview();
}

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

/* ─── API call + loading state ─── */
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
    updateLoadingStage("Dispatching agents…", "Fan-out: 7 parallel agents running");

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
      msg = `Could not connect to the backend at <code>${BACKEND_URL}</code>. Make sure the backend is running.`;
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
  // Clear any existing chart instances to avoid memory leaks
  window._charts = window._charts || {};
  Object.values(window._charts).forEach(c => c.destroy());
  window._charts = {};
  window._chartData = {};
}

/* ─── Card rendering ─── */
function renderResults(data, elapsed) {
  const container = document.getElementById("results-container");
  const grid      = document.getElementById("results-grid");
  const metaText  = document.getElementById("results-meta-text");

  const count = data.results.length;
  metaText.innerHTML = `<strong>${count}</strong> ticker${count !== 1 ? "s" : ""} analysed — completed in <strong>${elapsed}s</strong>`;

  grid.innerHTML = "";
  data.results.forEach((item, idx) => {
    const card = buildStockCard(item);
    card.style.animationDelay = `${idx * 60}ms`;
    grid.appendChild(card);
    
    // Initialize charts after appending to DOM
    setTimeout(() => {
      initChart(item.ticker, item);
    }, 10 + idx * 50);
  });

  container.classList.add("visible");
  setTimeout(() => {
    container.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 100);
}

/**
 * PHASE 4 LAYOUT
 */
function buildStockCard(item) {
  const { ticker, synthesis, fundamentals, momentum, news, technical, earnings_quality, valuation, moat } = item;
  const rating = synthesis.rating;
  const companyName = fundamentals.company_name || COMPANY_NAMES[ticker] || ticker;
  const ratingDot = { Attractive: "●", Neutral: "●", Caution: "●" }[rating] ?? "●";

  const card = document.createElement("article");
  card.className = "stock-card";
  card.setAttribute("data-ticker", ticker);

  // 1. Stripe & Header
  card.innerHTML += `<div class="card-stripe stripe-${rating.toLowerCase()}"></div>`;
  card.innerHTML += `
    <div class="card-header">
      <div class="card-ticker-block">
        <div class="card-ticker">${escHtml(ticker)}</div>
        <div class="card-company">${escHtml(companyName)}</div>
      </div>
      <div style="display:flex; gap:12px; align-items:center;">
        <div class="rating-badge" title="Fin-Vantage Composite Score (0-100) combining Fundamentals, Momentum, Valuation, and Quality." onclick="setTab('methodology')" style="cursor:pointer; background:var(--bg-input); border:1px solid var(--brand-teal); color:var(--brand-teal); font-size:16px;">
          Score: ${synthesis.composite_score || '--'}
        </div>
        <div class="rating-badge badge-${rating.toLowerCase()}">
          <span>${ratingDot}</span>
          ${escHtml(rating)}
        </div>
      </div>
    </div>`;


  // 2. AI Analysis (Summary + Sections)
  card.innerHTML += `
    <div class="synthesis-summary" style="padding: 16px 20px 8px; font-size: 14px; color: var(--text-primary); line-height: 1.5;">
      <strong>Mr. FinVantage Scout:</strong> ${escHtml(synthesis.summary || "No summary available.")}
    </div>
    <div class="ai-phrases">
      <div class="ai-phrase-box" onclick="setTab('methodology')" style="cursor:pointer;" title="Click to view methodology definitions"><div class="ai-phrase-label">Growth</div><div class="ai-phrase-text">${escHtml(synthesis.growth)}</div></div>
      <div class="ai-phrase-box" onclick="setTab('methodology')" style="cursor:pointer;" title="Click to view methodology definitions"><div class="ai-phrase-label">Quality</div><div class="ai-phrase-text">${escHtml(synthesis.quality)}</div></div>
      <div class="ai-phrase-box" onclick="setTab('methodology')" style="cursor:pointer;" title="Click to view methodology definitions"><div class="ai-phrase-label">Valuation</div><div class="ai-phrase-text">${escHtml(synthesis.valuation)}</div></div>
      <div class="ai-phrase-box" onclick="setTab('methodology')" style="cursor:pointer;" title="Click to view methodology definitions"><div class="ai-phrase-label">Momentum & Tech</div><div class="ai-phrase-text">${escHtml(synthesis.momentum_technical)}</div></div>
      <div class="ai-phrase-box" onclick="setTab('methodology')" style="cursor:pointer;" title="Click to view methodology definitions"><div class="ai-phrase-label">Moat</div><div class="ai-phrase-text">${escHtml(synthesis.moat)}</div></div>
      <div class="ai-phrase-box" onclick="setTab('methodology')" style="cursor:pointer;" title="Click to view methodology definitions"><div class="ai-phrase-label">Sentiment</div><div class="ai-phrase-text">${escHtml(synthesis.sentiment)}</div></div>
    </div>
  `;

  // 3. Quick Reference Tiles (Moved up)
  card.innerHTML += `
    <div class="quick-tiles">
      <div class="quick-tile qt-1"><div class="quick-tile-label">Avg Pct Rank</div><div class="quick-tile-value">${fmtNum((momentum.percentile_rank_6m + momentum.percentile_rank_12m)/2, ".0f")}</div></div>
      <div class="quick-tile qt-2"><div class="quick-tile-label">Vol vs 50D</div><div class="quick-tile-value">${fmtNum(technical.volume_pct_change, ".1f")}%</div></div>
      <div class="quick-tile qt-3"><div class="quick-tile-label">Accruals</div><div class="quick-tile-value">${fmtNum(earnings_quality.accruals_ratio, ".3f")}</div></div>
      <div class="quick-tile qt-4"><div class="quick-tile-label">Gross Margin</div><div class="quick-tile-value">${fmtPct(fundamentals.gross_margin)}</div></div>
      <div class="quick-tile qt-5"><div class="quick-tile-label">P/E vs Peers</div><div class="quick-tile-value">${fmtNum(valuation.pe, ".1f")} / ${fmtNum(valuation.peer_median_pe, ".1f")}</div></div>
    </div>
  `;

  // 4. Data Table (Collapsible)
  card.innerHTML += `
    <details class="card-section" open>
      <summary>Detailed Agent Metrics</summary>
      <div class="data-table-container">
        <div class="tag-legend">
          <span class="source-tag tag-api">API Raw Data</span>
          <span class="source-tag tag-calculated">Calculated</span>
          <span class="source-tag tag-approx" title="Independently estimated; not IBD's proprietary rating.">Approximated Proxy</span>
        </div>
        <table class="data-table">
          <thead><tr><th>Metric</th><th class="numeric">Value</th><th>Source</th></tr></thead>
          <tbody data-group="1a. Fundamentals">
            <tr><td>Current Ratio</td><td class="numeric">${fmtNum(fundamentals.current_ratio, ".2f")}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>Debt-to-Equity</td><td class="numeric">${fmtNum(fundamentals.debt_to_equity, ".2f")}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>ROE</td><td class="numeric">${fmtPct(fundamentals.roe)}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>Gross Margin</td><td class="numeric">${fmtPct(fundamentals.gross_margin)}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>EPS YoY (Latest Qtr)</td><td class="numeric">${fmtNum(fundamentals.eps_pct_change_latest_qtr, ".1f")}%</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td>Sales YoY (Latest Qtr)</td><td class="numeric">${fmtNum(fundamentals.sales_pct_change_last_qtr, ".1f")}%</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
          </tbody>
          <tbody data-group="1b. Momentum">
            <tr><td>6m Return</td><td class="numeric">${fmtNum(momentum.return_6m, ".1f")}%</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>12m Return</td><td class="numeric">${fmtNum(momentum.return_12m, ".1f")}%</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>Average Percentile Rank</td><td class="numeric">${fmtNum((momentum.percentile_rank_6m + momentum.percentile_rank_12m) / 2, ".0f")}</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td>RSI (14-Day)</td><td class="numeric">${fmtNum(momentum.rsi_14, ".1f")}</td><td><span class="source-tag tag-api">API</span></td></tr>
          </tbody>
          <tbody data-group="1c. Technical">
            <tr><td>Volume</td><td class="numeric">${technical.volume ? technical.volume.toLocaleString() : '--'}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>Vol vs 50d Avg</td><td class="numeric">${fmtNum(technical.volume_pct_change, ".1f")}%</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td>% Off 52w High</td><td class="numeric">${fmtNum(technical.pct_off_52w_high, ".1f")}%</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td>Hist. Volatility</td><td class="numeric">${fmtNum(technical.historical_volatility, ".1f")}%</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td>ATR (14-Day)</td><td class="numeric">${fmtNum(technical.atr, ".2f")}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>Acc/Dis Approx</td><td class="numeric">${fmtNum(technical.acc_dis_score, ".2f")}</td><td><span class="source-tag tag-approx">Approx</span></td></tr>
          </tbody>
          <tbody data-group="1d. Earnings Quality">
            <tr><td>Sloan Accruals Ratio</td><td class="numeric">${fmtNum(earnings_quality.accruals_ratio, ".3f")}</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td>Cash Conversion Ratio</td><td class="numeric">${fmtNum(earnings_quality.cash_conversion_ratio, ".2f")}</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
          </tbody>
          <tbody data-group="1e. Valuation">
            <tr><td>Trailing P/E</td><td class="numeric">${fmtNum(valuation.pe, ".2f")}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>Price / Sales</td><td class="numeric">${fmtNum(valuation.ps, ".2f")}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>EV / EBITDA</td><td class="numeric">${fmtNum(valuation.ev_ebitda, ".2f")}</td><td><span class="source-tag tag-api">API</span></td></tr>
            <tr><td>Peer Median P/E</td><td class="numeric">${fmtNum(valuation.peer_median_pe, ".2f")}</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
          </tbody>
          <tbody data-group="1f. Moat">
            <tr><td>Margin Stability (CoV)</td><td class="numeric">${fmtNum(moat.margin_stability, ".2f")}</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td>ROIC Persistence (CoV)</td><td class="numeric">${fmtNum(moat.roic_persistence, ".2f")}</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td>Rev Consistency (CoV)</td><td class="numeric">${fmtNum(moat.revenue_consistency, ".2f")}</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
          </tbody>
          <tbody data-group="1g. News Sentiment (LLM)">
            <tr><td>Sentiment Score</td><td class="numeric">${escHtml(news.sentiment)}</td><td><span class="source-tag tag-calculated">Calculated</span></td></tr>
            <tr><td colspan="3">${buildHeadlinesToggle(news.headlines, 'hl-'+ticker)}</td></tr>
          </tbody>
        </table>
      </div>
    </details>
  `;

  // 5. Charts Container
  card.innerHTML += `
    <details class="card-section">
      <summary>Charts & Visualizations</summary>
      <div class="chart-header">
        <select class="chart-select" onchange="window.switchChart(this, '${ticker}')">
          <option value="price_volume">Price & Volume (6M)</option>
          <option value="momentum">Momentum Percentile</option>
          <option value="margin_roic">Margin & ROIC Trend</option>
          ${valuation.peer_comparison_available ? '<option value="valuation">Valuation vs Peers</option>' : ''}
        </select>
      </div>
      <div id="chart-${ticker}" class="chart-container"></div>
    </details>
  `;


  return card;
}

/* ─── Headlines Toggle Phase 4 ─── */
function buildHeadlinesToggle(headlines, id) {
  if (!headlines || headlines.length === 0) {
    return `<div style="margin-top:4px;font-size:10px;color:var(--text-muted);">No headlines retrieved.</div>`;
  }

  // Phase 4 headlines are list of dicts: {title, url}
  const items = headlines
    .map(h => `<div class="headline-item" style="padding:4px 0;"><span class="headline-bullet" style="margin-right:6px;">›</span><a href="${escHtml(h.url)}" target="_blank" rel="noopener noreferrer" style="color:var(--brand-teal); text-decoration:none;">${escHtml(h.title)}</a></div>`)
    .join("");

  return `
    <button class="headlines-toggle" onclick="toggleHeadlines('${id}', this)" aria-expanded="false" aria-controls="${id}" style="background:none; border:none; color:var(--text-secondary); cursor:pointer; font-size:11px; padding:4px 0; display:flex; align-items:center; gap:4px;">
      <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="12" height="12">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
      </svg>
      View ${headlines.length} headlines
    </button>
    <div class="headlines-list" id="${id}" aria-hidden="true" style="display:none; padding-left:14px;">
      ${items}
    </div>`;
}

function toggleHeadlines(id, btn) {
  const list = document.getElementById(id);
  if (!list) return;
  const isHidden = list.style.display === "none";
  list.style.display = isHidden ? "block" : "none";
  btn.setAttribute("aria-expanded", isHidden);
  btn.querySelector("svg").style.transform = isHidden ? "rotate(180deg)" : "";
}

/* ─── ApexCharts Integration ─── */
window._charts = {};
window._chartData = {};

function initChart(ticker, item) {
  // Store item data globally for chart switching
  window._chartData[ticker] = item;
  renderChart(ticker, "price_volume");
}

window.switchChart = function(selectEl, ticker) {
  renderChart(ticker, selectEl.value);
}

function renderChart(ticker, type) {
  const containerId = `chart-${ticker}`;
  const el = document.getElementById(containerId);
  if (!el) return;
  
  if (window._charts[ticker]) {
    window._charts[ticker].destroy();
  }

  const data = window._chartData[ticker];
  let options = {};
  const isDark = !document.body.classList.contains('light-mode');
  const textColor = isDark ? '#8a9bbf' : '#475569';
  const gridColor = isDark ? '#1e293b' : '#e2e8f0';

  if (type === "price_volume") {
    const ph = data.technical.price_history || [];
    const vh = data.technical.volume_history || [];
    
    // Sort by date ascending for charts
    const sortedPh = [...ph].sort((a,b) => new Date(a.date) - new Date(b.date));
    const sortedVh = [...vh].sort((a,b) => new Date(a.date) - new Date(b.date));
    
    options = {
      chart: { type: 'line', height: 280, toolbar: { show: false }, background: 'transparent', animations: { enabled: false } },
      series: [
        { name: 'Price', type: 'line', data: sortedPh.map(d => ({ x: d.date, y: d.value })) },
        { name: 'Volume', type: 'bar', data: sortedVh.map(d => ({ x: d.date, y: d.value })) }
      ],
      colors: ['#00b4d8', 'rgba(103, 58, 183, 0.4)'],
      stroke: { width: [2, 0] },
      xaxis: { type: 'datetime', labels: { style: { colors: textColor } }, axisBorder: { show: false } },
      yaxis: [
        { title: { text: 'Price', style: { color: textColor } }, labels: { style: { colors: textColor }, formatter: v => v ? v.toFixed(2) : '' } },
        { opposite: true, title: { text: 'Volume', style: { color: textColor } }, labels: { style: { colors: textColor }, formatter: v => v ? (v/1000000).toFixed(1)+'M' : '' } }
      ],
      grid: { borderColor: gridColor, strokeDashArray: 4 },
      legend: { labels: { colors: textColor } },
      theme: { mode: isDark ? 'dark' : 'light' }
    };
  } 
  else if (type === "momentum") {
    options = {
      chart: { type: 'bar', height: 280, toolbar: { show: false }, background: 'transparent' },
      series: [{ name: 'Percentile Rank', data: [data.momentum.percentile_rank_6m || 0, data.momentum.percentile_rank_12m || 0] }],
      plotOptions: { bar: { horizontal: true, borderRadius: 4, dataLabels: { position: 'top' } } },
      colors: ['#f0c040'],
      xaxis: { categories: ['6M Rank', '12M Rank'], max: 100, labels: { style: { colors: textColor } } },
      yaxis: { labels: { style: { colors: textColor } } },
      grid: { borderColor: gridColor, strokeDashArray: 4 },
      theme: { mode: isDark ? 'dark' : 'light' }
    };
  }
  else if (type === "margin_roic") {
    const my = data.moat.margin_by_year || [];
    const ry = data.moat.roic_by_year || [];
    const sortedMy = [...my].sort((a,b) => a.year - b.year);
    const sortedRy = [...ry].sort((a,b) => a.year - b.year);
    
    if (sortedMy.length < 2) {
      el.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text-muted);">Not enough historical data (<2 years)</div>`;
      return;
    }
    
    options = {
      chart: { type: 'line', height: 280, toolbar: { show: false }, background: 'transparent' },
      series: [
        { name: 'Gross Margin', data: sortedMy.map(d => ({ x: d.year.toString(), y: d.value })) },
        { name: 'ROIC Proxy', data: sortedRy.map(d => ({ x: d.year.toString(), y: d.value })) }
      ],
      colors: ['#00b4d8', '#f0c040'],
      stroke: { curve: 'smooth', width: 2 },
      xaxis: { categories: sortedMy.map(d => d.year.toString()), labels: { style: { colors: textColor } } },
      yaxis: { labels: { style: { colors: textColor }, formatter: v => v ? (v*100).toFixed(1)+'%' : '' } },
      grid: { borderColor: gridColor, strokeDashArray: 4 },
      legend: { labels: { colors: textColor } },
      theme: { mode: isDark ? 'dark' : 'light' }
    };
  }
  else if (type === "valuation") {
    options = {
      chart: { type: 'bar', height: 280, toolbar: { show: false }, background: 'transparent' },
      series: [{ name: 'P/E Ratio', data: [data.valuation.pe || 0, data.valuation.peer_median_pe || 0] }],
      colors: ['#00b4d8'],
      xaxis: { categories: [ticker, 'Peer Median'], labels: { style: { colors: textColor } } },
      yaxis: { labels: { style: { colors: textColor } } },
      grid: { borderColor: gridColor, strokeDashArray: 4 },
      theme: { mode: isDark ? 'dark' : 'light' }
    };
  }

  const chart = new ApexCharts(el, options);
  chart.render();
  window._charts[ticker] = chart;
}

/* ─── Company name lookup table ─── */
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
  NFLX: "Netflix Inc.", DIS: "Walt Disney Co.", PYPL: "PayPal Holdings",
  SHOP: "Shopify Inc.", SQ: "Block Inc.", ROKU: "Roku Inc.",
  UBER: "Uber Technologies", LYFT: "Lyft Inc.", ABNB: "Airbnb Inc.",
  COIN: "Coinbase Global", RBLX: "Roblox Corp.", SNAP: "Snap Inc.",
  CRM: "Salesforce Inc.", NOW: "ServiceNow Inc.", SNOW: "Snowflake Inc.",
  DDOG: "Datadog Inc.", NET: "Cloudflare Inc.", CRWD: "CrowdStrike Holdings",
  PANW: "Palo Alto Networks", ZS: "Zscaler Inc.", OKTA: "Okta Inc.",
  PLTR: "Palantir Technologies", SOFI: "SoFi Technologies", HOOD: "Robinhood Markets",
  BABA: "Alibaba Group", NIO: "NIO Inc.", XPEV: "XPeng Inc.", LI: "Li Auto Inc.",
  TSM: "Taiwan Semiconductor", ASML: "ASML Holding", SAP: "SAP SE",
  SMCI: "Super Micro Computer", DELL: "Dell Technologies", HPQ: "HP Inc.",
  IBM: "IBM Corp.", CSCO: "Cisco Systems", ADBE: "Adobe Inc.",
  GS: "Goldman Sachs Group", MS: "Morgan Stanley", BAC: "Bank of America",
  WFC: "Wells Fargo & Co.", C: "Citigroup Inc.", USB: "U.S. Bancorp",
  GE: "GE Aerospace", BA: "Boeing Co.", CAT: "Caterpillar Inc.",
  CVS: "CVS Health Corp.", CI: "Cigna Group", HUM: "Humana Inc.",
  AMGN: "Amgen Inc.", GILD: "Gilead Sciences", REGN: "Regeneron Pharmaceuticals",
  VRTX: "Vertex Pharmaceuticals", BIIB: "Biogen Inc.", MRNA: "Moderna Inc.",
  PFE: "Pfizer Inc.", BMY: "Bristol-Myers Squibb",
  ENPH: "Enphase Energy", FSLR: "First Solar Inc.", SEDG: "SolarEdge Technologies",
  SPY: "SPDR S&P 500 ETF", QQQ: "Invesco QQQ Trust", IWM: "iShares Russell 2000 ETF",
  FFTY: "Innovator IBD 50 ETF",
};

/* ─── Utility helpers ─── */
function fmtNum(value, fmt) {
  if (value === null || value === undefined) return "N/A";
  const decimals = parseInt((fmt.match(/\.(\d)f/) || ["", "2"])[1], 10);
  return value.toFixed(decimals);
}

function fmtPct(value) {
  if (value === null || value === undefined) return "N/A";
  return (value * 100).toFixed(1) + "%";
}

function numClass(value) {
  if (value === null || value === undefined) return "na";
  return value >= 1.5 ? "positive" : value < 1 ? "negative" : "";
}

function sentimentDot(sentiment) {
  const color = { Positive: "#22c55e", Neutral: "#f59e0b", Negative: "#ef4444" }[sentiment] ?? "#8a9bbf";
  return `<svg width="8" height="8" viewBox="0 0 8 8"><circle cx="4" cy="4" r="4" fill="${color}"/></svg>`;
}

function escHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ─── Init on DOMContentLoaded ─── */
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  checkHealth();
  setInterval(checkHealth, 30_000);
  setMode("manual");
  document.getElementById("ticker-input")?.focus();
});
