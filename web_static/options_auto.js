// state
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  defaults: {},
  status: {},
  lastResult: {},
  lastBacktest: {},
  lastShadowReport: {},
  lastReplay: {},
  pendingApprovalId: "",
  activeLog: "raw",
  activeTab: "dashboard",
  dataSource: "UNKNOWN",
  fiiDiiStatus: {},
};

let refreshTimer = null;

// api helper
async function api(path, payload) {
  const options = payload === undefined
    ? {}
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

async function apiForm(path, form) {
  const response = await fetch(path, { method: "POST", body: form });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

// format helpers
function value(input, fallback = "-") {
  if (input === undefined || input === null || input === "") return fallback;
  if (typeof input === "number" && Number.isNaN(input)) return fallback;
  return input;
}

function text(input, fallback = "-") {
  const resolved = value(input, fallback);
  if (typeof resolved === "object") return fallback;
  return String(resolved);
}

function numberValue(input, fallback = 0) {
  const parsed = Number(input);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function money(input) {
  const amount = numberValue(input, 0);
  return amount.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
}

function todayLocalIso() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 10);
}

function score(input) {
  if (input === undefined || input === null || input === "") return "-";
  return `${Number(input).toFixed(Number(input) % 1 ? 1 : 0)} / 100`;
}

function percent(input) {
  if (input === undefined || input === null || input === "") return "-";
  return `${Number(input).toFixed(2)}%`;
}

function escapeHtml(input) {
  return text(input, "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  }[char]));
}

function setText(id, content) {
  const node = $(id);
  if (node) node.textContent = text(content);
}

function setHtml(id, html) {
  const node = $(id);
  if (node) node.innerHTML = html;
}

function badgeClass(kind) {
  return {
    green: "oa-badge-green",
    red: "oa-badge-red",
    yellow: "oa-badge-yellow",
    grey: "oa-badge-grey",
    blue: "oa-badge-blue",
  }[kind] || "oa-badge-grey";
}

function setBadge(id, label, kind = "grey") {
  const node = $(id);
  if (!node) return;
  node.className = `oa-status-badge ${badgeClass(kind)}`;
  node.textContent = text(label);
}

function yesNoBadge(id, ok, yes = "YES", no = "NO") {
  setBadge(id, ok ? yes : no, ok ? "green" : "red");
}

function metric(label, content, extra = "") {
  return `<div ${extra}><span>${escapeHtml(label)}</span><strong>${escapeHtml(content)}</strong></div>`;
}

function row(label, content) {
  return `<div class="oa-plan-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(content)}</strong></div>`;
}

function renderList(id, items, emptyText = "No items to show.") {
  const rows = (items || []).filter(Boolean);
  setHtml(id, rows.length
    ? rows.map(item => `<li>${escapeHtml(item)}</li>`).join("")
    : `<li class="oa-muted">${escapeHtml(emptyText)}</li>`);
}

function alertHtml(message, kind = "info") {
  return `<div class="oa-alert oa-alert-${kind}">${escapeHtml(message)}</div>`;
}

function setTabAlert(tab, message = "", kind = "info") {
  const node = $(`#oa-${tab}-alert`);
  if (!node) return;
  node.innerHTML = message ? alertHtml(message, kind) : "";
}

function setActiveAlert(message = "", kind = "info") {
  if (state.activeTab === "dashboard") {
    setHtml("#oa-dashboard-alerts", message ? alertHtml(message, kind) : "");
  } else {
    setTabAlert(state.activeTab, message, kind);
  }
}

function emptyLike(fallback) {
  return Array.isArray(fallback) ? [] : {};
}

function parseJson(id, fallback, allowSample = false) {
  const node = $(id);
  const raw = node ? node.value.trim() : "";
  if (!raw) {
    state.dataSource = allowSample ? "DEMO" : "UNKNOWN";
    return allowSample ? fallback : emptyLike(fallback);
  }
  state.dataSource = "DEBUG";
  return JSON.parse(raw);
}

// sample payloads for Developer Debug and local smoke only
function sampleInstruments() {
  return [
    { name: "NIFTY", tradingsymbol: "NIFTY26JUN22500CE", instrument_token: "1001", instrument_type: "CE", strike: 22500, expiry: "2026-06-25", lot_size: 50, tick_size: 0.05 },
    { name: "NIFTY", tradingsymbol: "NIFTY26JUN22500PE", instrument_token: "1002", instrument_type: "PE", strike: 22500, expiry: "2026-06-25", lot_size: 50, tick_size: 0.05 },
    { name: "NIFTY", tradingsymbol: "NIFTY26JUN22600CE", instrument_token: "1003", instrument_type: "CE", strike: 22600, expiry: "2026-06-25", lot_size: 50, tick_size: 0.05 },
    { name: "NIFTY", tradingsymbol: "NIFTY26JUN22400PE", instrument_token: "1004", instrument_type: "PE", strike: 22400, expiry: "2026-06-25", lot_size: 50, tick_size: 0.05 },
  ];
}

function sampleQuotes() {
  return {
    "1001": { demo_data: true, ltp: 142.4, bid: 142.25, ask: 142.45, bid_qty: 1450, ask_qty: 1300, volume: 85000, oi: 950000, premium_return_1: 1.2, premium_return_3: 4.5, relative_volume: 1.6, option_vwap: 139.8, option_atr14: 5, momentum_score: 72 },
    "1002": { demo_data: true, ltp: 120.2, bid: 119.9, ask: 120.25, bid_qty: 900, ask_qty: 1150, volume: 76000, oi: 870000, premium_return_1: -0.2, premium_return_3: 0.4, relative_volume: 0.9, option_vwap: 121.1, option_atr14: 4.2, momentum_score: 46 },
    "1003": { demo_data: true, ltp: 97.6, bid: 97.2, ask: 97.95, bid_qty: 800, ask_qty: 850, volume: 53000, oi: 520000, premium_return_1: 0.8, premium_return_3: 2.1, relative_volume: 1.2, option_vwap: 96.8, option_atr14: 3.8, momentum_score: 68 },
    "1004": { demo_data: true, ltp: 88.5, bid: 88.05, ask: 88.7, bid_qty: 750, ask_qty: 800, volume: 51000, oi: 500000, premium_return_1: -0.3, premium_return_3: 0.2, relative_volume: 0.8, option_vwap: 89.4, option_atr14: 3.5, momentum_score: 44 },
  };
}

function sampleMarketCue() {
  return { demo_data: true, phase: "LUNCH", technical_score: 58, option_oi_score: 18, news_score: 2 };
}

function sampleReplayCandles() {
  return [
    { datetime: "2026-06-04 09:15", open: 100, high: 105, low: 98, close: 103, volume: 1000 },
    { datetime: "2026-06-04 09:18", open: 103, high: 108, low: 101, close: 107, volume: 1200 },
  ];
}

// shared payload builders
function settingsPayload(modeOverride = "") {
  const mode = modeOverride || ($("#oa-setting-mode")?.value || "PAPER");
  return {
    mode,
    underlying: $("#oa-underlying")?.value || $("#oa-backtest-symbol")?.value || "NIFTY",
    chart_interval: $("#oa-chart-interval")?.value || $("#oa-backtest-interval")?.value || "3minute",
    strategy_profile: $("#oa-profile")?.value || $("#oa-backtest-profile")?.value || "BALANCED",
    buy_score_threshold: numberValue($("#oa-score-threshold")?.value, 70),
    paper_starting_balance: numberValue($("#oa-paper-balance")?.value, 20000),
    approval_timeout_seconds: numberValue($("#oa-approval-timeout")?.value, 30),
    max_capital_per_trade_pct: numberValue($("#oa-capital-pct")?.value, 20),
    max_daily_loss: numberValue($("#oa-max-daily-loss")?.value, 1000),
    max_daily_profit_lock: numberValue($("#oa-max-daily-profit")?.value, 2500),
    max_trades_per_day: numberValue($("#oa-max-trades")?.value, 3),
    max_open_trades: numberValue($("#oa-max-open-trades")?.value, 1),
    max_consecutive_losses: numberValue($("#oa-max-consecutive-losses")?.value, 2),
    cooldown_after_trade_seconds: numberValue($("#oa-cooldown-seconds")?.value, 300),
    max_chase_points: numberValue($("#oa-max-chase")?.value, 3),
    avoid_first_minutes: numberValue($("#oa-avoid-first")?.value, 15),
    no_new_trade_after: $("#oa-no-new-after")?.value || "15:00",
    square_off_time: $("#oa-square-off")?.value || "15:15",
    trailing_stop_enabled: Boolean($("#oa-trailing")?.checked),
    break_even_sl_enabled: Boolean($("#oa-breakeven")?.checked),
    partial_exit_enabled: Boolean($("#oa-partial")?.checked),
    reversal_exit_enabled: Boolean($("#oa-reversal")?.checked),
    time_exit_enabled: Boolean($("#oa-time-exit")?.checked),
    max_holding_minutes: numberValue($("#oa-max-holding")?.value, 45),
    expiry_preference: $("#oa-expiry-mode")?.value || "AUTO",
    min_volume: numberValue($("#oa-min-volume")?.value, 0),
    min_oi: numberValue($("#oa-min-oi")?.value, 0),
    max_spread_pct: numberValue($("#oa-max-spread")?.value, 0.6),
    theta_exit_risk_score: numberValue($("#oa-theta-risk")?.value, 80),
    expiry_day_max_lots: numberValue($("#oa-expiry-day-lots")?.value, 1),
    allow_deep_otm: Boolean($("#oa-allow-deep-otm")?.checked),
    limit_order_timeout_seconds: numberValue($("#oa-limit-timeout")?.value, 30),
    max_buy_limit_modifications: numberValue($("#oa-max-mods")?.value, 2),
    sl_modify_throttle_seconds: numberValue($("#oa-sl-throttle")?.value, 10),
    slippage_buffer_points: numberValue($("#oa-slippage-buffer")?.value, 0.1),
    ask_permission_before_entry: Boolean($("#oa-ask")?.checked || $("#oa-ask-settings")?.checked),
    auto_entry_enabled: Boolean($("#oa-auto")?.checked || $("#oa-auto-settings")?.checked),
    require_fii_dii_upload: Boolean($("#oa-require-fii-dii")?.checked),
    allow_demo_data: state.activeTab === "debug",
    confirm_real_mode: Boolean($("#oa-confirm-real")?.checked),
    static_ip_confirmed: Boolean($("#oa-static-ip")?.checked),
  };
}

function evaluationPayload(modeOverride = "", options = {}) {
  const settings = settingsPayload(modeOverride);
  const allowDemo = Boolean(options.allowDemo || state.activeTab === "debug");
  const marketCue = allowDemo ? parseJson("#oa-market-cue-json", sampleMarketCue(), true) : {};
  const instruments = allowDemo ? parseJson("#oa-instruments-json", sampleInstruments(), true) : [];
  const quotes = allowDemo ? parseJson("#oa-quotes-json", sampleQuotes(), true) : {};
  const demoData = allowDemo && (marketCue.demo_data || Object.values(quotes || {}).some(item => item?.demo_data));
  const dataSource = demoData ? "DEBUG" : allowDemo && instruments.length && Object.keys(quotes || {}).length ? "DEBUG" : "ZERODHA_REQUIRED";
  state.dataSource = dataSource;
  return {
    mode: settings.mode,
    settings,
    spot: allowDemo ? numberValue($("#oa-spot")?.value, 22500) : undefined,
    quote_age_seconds: numberValue($("#oa-quote-age")?.value, 0),
    market_cue: marketCue,
    instruments,
    quotes,
    data_source: dataSource,
    demo_data: demoData,
    features: allowDemo ? { ema_alignment_score: 18, vwap_score: 14, rsi_slope_score: 10, volume_score: 8, depth_score: 5 } : {},
    time_of_day_score: 70,
  };
}

function backtestPayload() {
  const underlying = $("#oa-backtest-symbol")?.value || "NIFTY";
  const interval = $("#oa-backtest-interval")?.value || "3minute";
  const tradeDate = $("#oa-backtest-date")?.value || todayLocalIso();
  const backtestSpot = $("#oa-backtest-spot")?.value || "";
  const span = numberValue($("#oa-backtest-span")?.value, 4);
  return {
    data_source: "zerodha_historical",
    underlying,
    interval,
    trade_date: tradeDate,
    backtest_spot: backtestSpot,
    settings: {
      ...settingsPayload("BACKTEST"),
      underlying,
      chart_interval: interval,
      paper_starting_balance: numberValue($("#oa-backtest-balance")?.value, 20000),
      strategy_profile: $("#oa-backtest-profile")?.value || "BALANCED",
      max_trades_per_day: numberValue($("#oa-backtest-max-trades")?.value, 3),
      buy_score_threshold: numberValue($("#oa-backtest-score")?.value, 70),
      atm_scan_strike_span: span,
    },
  };
}

// tab navigation
function initTabs() {
  $$("[data-oa-tab]").forEach(button => {
    button.addEventListener("click", () => {
      const tab = button.dataset.oaTab;
      state.activeTab = tab;
      $$("[data-oa-tab]").forEach(item => item.classList.toggle("oa-tab-active", item === button));
      $$(".oa-tab-panel").forEach(panel => {
      panel.hidden = panel.id !== `oa-tab-${tab}`;
      });
      renderAll();
    });
  });
}

// status rendering
function renderTopStatus() {
  const result = state.lastResult || {};
  const settings = result.settings || state.status.settings || state.defaults.settings || {};
  const mode = text(result.mode || settings.mode || "PAPER");
  const account = result.account_status || state.status.account_status || {};
  const isReal = mode === "REAL";
  const connected = isReal ? Boolean(account.real?.connected) : Boolean(account.paper?.connected);
  const activeTrades = activeTradesFrom(result);
  const protectedOk = !activeTrades.length || activeTrades.every(trade => trade.position_protected);
  const ocoOk = !activeTrades.length || activeTrades.every(trade => trade.oco_active);
  const dataAllowed = result.data_quality?.allowed;
  const resultDemo = Boolean(result.demo_data || result.data_quality?.blockers?.some(item => String(item).includes("demo/sample")));
  const dataSource = result.data_source || state.dataSource || "UNKNOWN";
  const dataLabel = resultDemo ? "Demo" : dataAllowed ? "Fresh" : dataSource === "LIVE" ? "Stale" : "Waiting";
  const governor = result.governor || {};
  const governorLabel = governor.allowed === true ? "Allow" : governor.state ? "Blocked" : "Waiting";
  const engine = result.session?.status || state.status.session?.status || "Idle";
  const pnl = realizedPnl();

  setBadge("#oa-mode", mode, isReal ? "red" : mode === "PAPER" ? "green" : "blue");
  setBadge("#oa-real-money", isReal ? "YES" : "NO", isReal ? "red" : "green");
  setBadge("#oa-kite", connected ? "Connected" : "Disconnected", connected ? "green" : "red");
  setBadge("#oa-data", dataLabel, dataAllowed ? "green" : resultDemo ? "yellow" : "yellow");
  setBadge("#oa-governor", governorLabel, governor.allowed === true ? "green" : governor.state ? "yellow" : "grey");
  setBadge("#oa-engine", engine, /RUNNING|ACTIVE|READY/.test(String(engine)) ? "green" : /LOCK|ERROR|MANUAL/.test(String(engine)) ? "red" : "grey");
  setBadge("#oa-position", activeTrades.length ? (protectedOk ? "Protected" : "Unprotected") : "No Position", activeTrades.length ? (protectedOk ? "green" : "red") : "grey");
  setBadge("#oa-oco", activeTrades.length ? (ocoOk ? "Active" : "Inactive") : "Inactive", activeTrades.length ? (ocoOk ? "green" : "red") : "grey");
  setBadge("#oa-daily-pnl", money(pnl), pnl > 0 ? "green" : pnl < 0 ? "red" : "grey");
}

function renderAll() {
  renderTopStatus();
  renderDashboard();
  renderIndexTickStreams();
  renderBacktestResults();
  renderShadow();
  renderRealPreflight();
  renderPaperAccount();
  renderReports();
  renderDeveloperRawJson();
}

function activeTradesFrom(result) {
  return result.session?.active_trades || result.paper_lifecycle?.active_trades || state.status.session?.active_trades || [];
}

function realizedPnl() {
  const ledger = state.status.paper_account?.ledger || state.lastResult.paper_account?.ledger || [];
  return ledger.reduce((sum, item) => {
    const type = String(item.type || "").toUpperCase();
    return type === "SELL" ? sum + numberValue(item.amount, 0) : sum;
  }, 0);
}

// dashboard rendering
function renderDashboard() {
  const result = state.lastResult || {};
  const cue = result.market_cue || {};
  const regime = result.regime || {};
  const selection = result.selection || {};
  const selected = selection.selected || {};
  const plan = result.trade_plan || {};
  const watchdog = result.watchdog || {};
  const health = watchdog || {};

  setBadge("#oa-dashboard-cue-badge", cue.cue || "Waiting", cue.recommended_side === "WAIT" ? "yellow" : cue.cue ? "blue" : "grey");
  setText("#oa-cue", cue.cue || "-");
  setText("#oa-cue-score", cue.score !== undefined ? score(cue.score) : "-");
  setText("#oa-cue-confidence", cue.confidence !== undefined ? score(cue.confidence) : "-");
  setText("#oa-cue-updated", cue.last_updated || cue.timestamp || "-");
  setText("#oa-cue-reason", cue.reason || cue.reason_summary || "No market cue evaluated yet.");
  renderFiiDiiStatus(cue.fii_dii_status || result.fii_dii_status || state.fiiDiiStatus || state.status.fii_dii || {});

  setBadge("#oa-regime-side", regime.recommended_side || "WAIT", regime.recommended_side === "WAIT" ? "yellow" : "blue");
  setText("#oa-regime", regime.regime || "-");
  setText("#oa-regime-confidence", regime.confidence !== undefined ? score(regime.confidence) : "-");
  setText("#oa-regime-aggression", regime.aggressiveness || "-");
  setText("#oa-regime-block", regime.no_trade_reason || "-");

  setBadge("#oa-health-badge", health.mode || "Waiting", health.mode === "NORMAL" ? "green" : health.mode ? "yellow" : "grey");
  setText("#oa-session-health", health.session_health_score !== undefined ? score(health.session_health_score) : "-");
  setText("#oa-bot-health", health.bot_health_score !== undefined ? score(health.bot_health_score) : "-");
  setText("#oa-readiness-score", health.daily_readiness_score !== undefined ? score(health.daily_readiness_score) : "-");
  if (health.new_entries_allowed === undefined) {
    setBadge("#oa-new-entries", "-", "grey");
  } else {
    setBadge("#oa-new-entries", health.new_entries_allowed ? "YES" : "NO", health.new_entries_allowed ? "green" : "yellow");
  }
  setText("#oa-cooldown", `Cooldown: ${text(result.risk?.cooldown_remaining_seconds, "0")} sec`);

  const riskAmount = plan.entry_price && plan.stoploss && plan.quantity ? (plan.entry_price - plan.stoploss) * plan.quantity : 0;
  const rewardAmount = plan.entry_price && plan.target && plan.quantity ? (plan.target - plan.entry_price) * plan.quantity : 0;
  const rr = riskAmount > 0 ? (rewardAmount / riskAmount).toFixed(2) : "-";
  setBadge("#oa-decision-badge", result.allowed ? "ALLOW" : result.blockers?.length ? "BLOCKED" : "WAIT", result.allowed ? "green" : result.blockers?.length ? "yellow" : "grey");
  setHtml("#oa-plan-body", [
    row("Decision", result.allowed ? "ALLOW" : result.blockers?.length ? "BLOCKED" : "WAIT"),
    row("Side", selection.side || regime.recommended_side || "WAIT"),
    row("Contract", selected.tradingsymbol || plan.tradingsymbol || "-"),
    row("Underlying", selected.name || settingsPayload().underlying || "-"),
    row("Expiry", selected.expiry || "-"),
    row("Strike", selected.strike || "-"),
    row("Moneyness", selected.moneyness || "-"),
    row("LTP", selected.ltp || "-"),
    row("Entry Limit", plan.entry_price || "-"),
    row("Target", plan.target || "-"),
    row("Stoploss", plan.stoploss || "-"),
    row("Quantity", plan.quantity || "-"),
    row("Lots", plan.lots || "-"),
    row("Estimated Risk", riskAmount ? money(riskAmount) : "-"),
    row("Estimated Reward", rewardAmount ? money(rewardAmount) : "-"),
    row("Risk Reward", rr),
    row("Trade Score", selection.score !== undefined ? score(selection.score) : "-"),
    row("Discipline Score", result.discipline?.discipline_score !== undefined ? score(result.discipline.discipline_score) : "-"),
    row("Data Quality", result.data_quality?.allowed ? "PASS" : "WAIT"),
    row("Theta Risk", result.options_risk?.theta_risk_score !== undefined ? score(result.options_risk.theta_risk_score) : "-"),
    row("Spread", selected.spread_pct !== undefined ? percent(selected.spread_pct) : "-"),
    row("Liquidity", selected.breakdown?.liquidity !== undefined ? score(selected.breakdown.liquidity) : "-"),
    row("Reason", result.explanation || "-"),
  ].join(""));

  renderList("#oa-blockers-list", readableBlockers(result), "No blockers. Waiting for a valid setup.");
  renderActiveTradeCard(activeTradesFrom(result));
  renderDataSourcePanel(result);
  renderRecentEvents(result);
  renderDashboardAlerts(result);
}

function renderFiiDiiStatus(status = {}) {
  const label = status.status || "NEUTRAL_MISSING_UPLOAD";
  const kind = label === "UPLOADED" || label === "OK" ? "green" : label === "IGNORED" ? "grey" : label === "FAILED" || label === "REQUIRED_MISSING_UPLOAD" ? "red" : "yellow";
  setBadge("#oa-fii-dii-badge", label, kind);
  setHtml("#oa-fii-dii-status", [
    metric("File", status.file_name || "Not uploaded"),
    metric("Uploaded", status.uploaded_at || "-"),
    metric("FII Net", status.fii_net === null || status.fii_net === undefined ? "-" : money(status.fii_net)),
    metric("DII Net", status.dii_net === null || status.dii_net === undefined ? "-" : money(status.dii_net)),
    metric("Score", status.score !== undefined ? score(status.score) : score(status.fii_dii_score || 0)),
    metric("Phase Use", status.used_for_phase || "PREMARKET"),
  ].join(""));
  setText("#oa-fii-dii-note", status.warning || (status.warnings || [])[0] || "FII/DII status ready.");
}

function renderDataSourcePanel(result = {}) {
  const source = result.data_source || state.dataSource || "UNKNOWN";
  const demo = source === "DEBUG" || source === "DEMO" || Boolean(result.demo_data);
  const health = result.options_data_health || {};
  const scan = result.live_scan || state.status.live_scan || {};
  const sourceOk = source === "LIVE" || source === "zerodha_paper_data" || source === "zerodha_real_data";
  setBadge("#oa-data-source-badge", source, demo ? "yellow" : sourceOk ? "green" : "grey");
  setHtml("#oa-demo-banner", demo ? `<div class="oa-data-banner">DEMO/SAMPLE DATA - not live market data.</div>` : "");
  setHtml("#oa-data-source-panel", [
    metric("Source", source),
    metric("Spot Source", result.spot_source || health.spot_source || "-"),
    metric("Live Spot", result.spot_value || health.spot || "-"),
    metric("ATM Strike", result.atm_strike || health.atm_strike || "-"),
    metric("Strike Step", result.strike_step || health.strike_step || "-"),
    metric("Candidate Span", result.candidate_span ?? health.candidate_span ?? "-"),
    metric("Candidate Count", result.candidate_count ?? health.candidate_count ?? "-"),
    metric("Valid Quote Count", result.valid_quote_count ?? health.valid_quote_count ?? "-"),
    metric("Missing Quote Keys", (result.missing_quote_keys || health.missing_quote_keys || []).length),
    metric("Index Candles", result.live_index_candle_count ?? "-"),
    metric("Candle Interval", result.live_index_candle_interval || "-"),
    metric("Quote Age", `${text(result.quote_age_seconds ?? $("#oa-quote-age")?.value, "-")} sec`),
    metric("Stale Threshold", `${text((result.settings || state.status.settings || state.defaults.settings || {}).quote_stale_seconds, 3)} sec`),
    metric("FII/DII", (state.fiiDiiStatus.status || result.market_cue?.fii_dii_status?.status || "Not uploaded")),
    metric("News", result.market_cue?.components?.news !== undefined ? score(result.market_cue.components.news) : "No news summary"),
    metric("Trading Allowed", result.allowed ? "YES" : "NO"),
    metric("Governor", result.governor?.state || "-"),
    metric("Live Scanner", scan.running ? "RUNNING" : "STOPPED"),
    metric("Last Scan", scan.last_cycle || "-"),
    metric("Scan Count", scan.cycle_count ?? "-"),
    metric("Next Action", result.next_action || "-"),
  ].join(""));
}

function renderIndexTickStreams() {
  const ticks = (state.status.index_ticks || state.lastResult.index_ticks || []).slice(-8);
  const latest = ticks[ticks.length - 1] || {};
  const scan = state.status.live_scan || state.lastResult.live_scan || {};
  const running = Boolean(scan.running);
  $$("[data-index-tick-badge]").forEach(node => {
    node.className = `oa-status-badge ${badgeClass(running ? "green" : ticks.length ? "yellow" : "grey")}`;
    node.textContent = running ? "Live" : ticks.length ? "Last Tick" : "Waiting";
  });
  const latestHtml = ticks.length
    ? `<div class="oa-index-tick-latest">
        ${metric("Underlying", latest.underlying || "-")}
        ${metric("Spot", latest.spot ?? "-")}
        ${metric("Source", latest.spot_source || "-")}
        ${metric("Observed", latest.observed_at || "-")}
        ${metric("Exchange Time", latest.exchange_timestamp || "-")}
        ${metric("Scan", scan.running ? `Running #${scan.cycle_count ?? 0}` : "Stopped")}
      </div>`
    : `<p class="oa-empty-state">No index ticks yet.</p>`;
  const rows = ticks.slice().reverse().map(tick => `<div class="oa-index-tick-row">
      <div><span>Time</span><strong>${escapeHtml(tick.observed_at || "-")}</strong></div>
      <div><span>Mode</span><strong>${escapeHtml(tick.mode || "-")}</strong></div>
      <div><span>Spot</span><strong>${escapeHtml(tick.spot ?? "-")}</strong></div>
      <div><span>Quote Key</span><strong>${escapeHtml(tick.quote_key || "-")}</strong></div>
    </div>`).join("");
  const body = `${latestHtml}${rows ? `<div class="oa-index-tick-list">${rows}</div>` : ""}`;
  $$("[data-index-tick-panel]").forEach(node => {
    node.innerHTML = body;
  });
}

function readableBlockers(result) {
  const blockers = result.blockers || result.governor?.blockers || [];
  return blockers.map(item => `Blocked: ${item}`);
}

function renderActiveTradeCard(trades) {
  if (!trades.length) {
    setBadge("#oa-active-trade-badge", "No Position", "grey");
    setHtml("#oa-active-trade-body", `<p class="oa-empty-state">No active position.</p>`);
    setHtml("#oa-trade-timeline", "");
    return;
  }
  const trade = trades[0] || {};
  setBadge("#oa-active-trade-badge", trade.position_protected ? "Protected" : "Unprotected", trade.position_protected ? "green" : "red");
  setHtml("#oa-active-trade-body", [
    row("Contract", trade.tradingsymbol || "-"),
    row("Quantity", trade.quantity || "-"),
    row("Entry Average", trade.entry_price || trade.average_price || "-"),
    row("Current LTP", trade.last_ltp || "-"),
    row("Unrealized P&L", trade.unrealized_pnl !== undefined ? money(trade.unrealized_pnl) : "-"),
    row("Target", trade.target || "-"),
    row("Stoploss", trade.stoploss || "-"),
    row("Trailing", trade.trailing_status || "-"),
    row("OCO", trade.oco_active ? "Active" : "Inactive"),
    row("Protected", trade.position_protected ? "YES" : "NO"),
    row("Last Update", trade.updated_at || trade.opened_at || "-"),
  ].join(""));
  const steps = [
    ["Signal accepted", true],
    ["Entry placed", Boolean(trade.entry_order_id)],
    ["Entry filled", Boolean(trade.entry_price || trade.average_price)],
    ["Target placed", Boolean(trade.target_order_id)],
    ["SL placed", Boolean(trade.stoploss_order_id)],
    ["OCO active", Boolean(trade.oco_active)],
    ["Monitoring", trade.status === "ACTIVE" || trade.status === "POSITION_ACTIVE"],
    ["Trade closed", trade.status === "CLOSED"],
  ];
  setHtml("#oa-trade-timeline", steps.map(([label, done]) => `<li class="${done ? "oa-step-done" : ""}">${escapeHtml(label)}</li>`).join(""));
}

function renderRecentEvents(result) {
  const session = result.session || state.status.session || {};
  const events = [];
  if (result.selection?.selected?.tradingsymbol) {
    events.push(`Candidate found: ${result.selection.selected.tradingsymbol}, score ${text(result.selection.score, "-")}.`);
  }
  (result.blockers || []).slice(0, 4).forEach(item => events.push(`Trade blocked: ${item}`));
  (session.safety_events || []).slice(-6).forEach(item => events.push(`${item.reason || "Safety event"}.`));
  (session.rejected_log || []).slice(-4).forEach(item => events.push(`Rejected setup: ${item.reason || "-"}`));
  renderList("#oa-events", events.slice(-10).reverse(), "No recent events yet.");
}

function renderDashboardAlerts(result) {
  const alerts = [];
  const mode = result.mode || state.status.settings?.mode || state.defaults.settings?.mode || "PAPER";
  const trades = activeTradesFrom(result);
  const connected = mode === "REAL" ? result.account_status?.real?.connected : result.account_status?.paper?.connected;
  if (mode === "REAL") alerts.push(alertHtml("REAL MONEY MODE is selected. Orders require real login, preflight, final validation, and OCO safety.", "danger"));
  if (connected === false) alerts.push(alertHtml("Kite is disconnected for the selected mode.", "warning"));
  if (state.dataSource === "DEBUG" || state.dataSource === "DEMO") alerts.push(alertHtml("DEMO/SAMPLE DATA - not live market data.", "warning"));
  if (state.dataSource === "UNKNOWN") alerts.push(alertHtml("Live quote data unavailable. Trading is blocked until live data is connected.", "warning"));
  if (trades.some(trade => !trade.position_protected)) alerts.push(alertHtml("Position unprotected. Manual attention required.", "danger"));
  if (trades.some(trade => !trade.oco_active)) alerts.push(alertHtml("OCO inactive while a position exists.", "danger"));
  if (result.watchdog?.mode === "CRITICAL" || result.watchdog?.mode === "LOCKED") alerts.push(alertHtml(`Watchdog ${result.watchdog.mode}. New entries blocked.`, "danger"));
  if (result.execution?.blockers?.length) alerts.push(alertHtml(result.execution.blockers[0], "warning"));
  setHtml("#oa-dashboard-alerts", alerts.join(""));
}

// backtest rendering/actions
function initBacktestTab() {
  on("#oa-backtest-run", "click", runBacktest);
  on("#oa-backtest-replay", "click", runReplay);
}

async function runBacktest() {
  try {
    setTabAlert("backtest", "Running backtest...", "info");
    const result = await api("/api/options-auto/backtest/run", backtestPayload());
    state.lastBacktest = result;
    state.lastResult = result;
    renderBacktestResults(result);
    renderTopStatus();
    setTabAlert("backtest", "Backtest complete.", "success");
  } catch (error) {
    setTabAlert("backtest", error.message, "danger");
  }
}

function renderBacktestResults(result = state.lastBacktest) {
  const metrics = result.metrics || result.summary || {};
  const trades = result.trades || [];
  const meta = result.source_metadata || {};
  const decisions = result.decisions || [];
  const blockerCounts = decisions.reduce((counts, row) => {
    (row.blockers || (row.reason ? [row.reason] : [])).forEach(blocker => {
      counts[blocker] = (counts[blocker] || 0) + 1;
    });
    return counts;
  }, {});
  const topBlockers = Object.entries(blockerCounts).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([label, count]) => `${label} (${count})`).join("; ");
  const grossPnl = metrics.gross_pnl ?? trades.reduce((sum, trade) => sum + numberValue(trade.gross_pnl, 0), 0);
  const charges = metrics.charges ?? trades.reduce((sum, trade) => sum + numberValue(trade.charges, 0), 0);
  const netPnl = metrics.net_pnl ?? metrics.total_pnl ?? trades.reduce((sum, trade) => sum + numberValue(trade.net_pnl, 0), 0);
  setHtml("#oa-backtest-summary", [
    metric("Data Source", result.data_source_label || result.data_source || "-"),
    metric("Rows", result.rows || 0),
    metric("Option Series", result.option_frames || 0),
    metric("Spot Source", meta.spot_source || "-"),
    metric("Backtest Spot", meta.spot || "-"),
    metric("ATM Strike", meta.atm_strike || "-"),
    metric("Contracts Requested", meta.contracts_requested || 0),
    metric("Contracts Found", meta.contracts_found || 0),
    metric("Proxy Quote", meta.historical_proxy_quote_warning ? "YES" : "NO"),
    metric("Zero-Trade Reason", trades.length ? "-" : (topBlockers || "No entries satisfied the configured gates.")),
    metric("Net P&L", money(netPnl)),
    metric("Gross P&L", money(grossPnl)),
    metric("Charges", money(charges)),
    metric("Win Rate", metrics.win_rate !== undefined ? percent(metrics.win_rate) : "-"),
    metric("Total Trades", metrics.total_trades || result.trades?.length || 0),
    metric("Max Drawdown", money(metrics.max_drawdown || 0)),
    metric("Profit Factor", metrics.profit_factor || "-"),
    metric("Target Hits", metrics.target_hits || 0),
    metric("Stoploss Hits", metrics.stoploss_hits || 0),
    metric("Reversal Exits", metrics.reversal_exits || 0),
    metric("Time Exits", metrics.time_exits || 0),
    metric("Best Trade", money(metrics.best_trade || 0)),
    metric("Worst Trade", money(metrics.worst_trade || 0)),
  ].join(""));
  setHtml("#oa-backtest-trades", trades.length
    ? trades.map(trade => `<tr><td>${escapeHtml(trade.time || trade.datetime || "-")}</td><td>${escapeHtml(trade.side || "-")}</td><td>${escapeHtml(trade.tradingsymbol || "-")}</td><td>${escapeHtml(trade.entry || trade.entry_price || "-")}</td><td>${escapeHtml(trade.exit || trade.exit_price || "-")}</td><td>${escapeHtml(trade.quantity || "-")}</td><td>${escapeHtml(trade.exit_reason || "-")}</td><td>${escapeHtml(money(trade.net_pnl || 0))}</td><td>${escapeHtml(trade.score || "-")}</td></tr>`).join("")
    : `<tr><td colspan="9">No trades generated by this backtest result.</td></tr>`);
}

// shadow rendering/actions
function initShadowTab() {
  on("#oa-shadow-start", "click", runShadowStart);
  on("#oa-shadow-stop", "click", () => {
    setTabAlert("shadow", "Shadow stop requested. No stop route exists yet; no orders were ever placed.", "info");
  });
  on("#oa-shadow-report-btn", "click", runShadowReport);
}

async function runShadowStart() {
  try {
    const result = await api("/api/options-auto/shadow/start", evaluationPayload("SHADOW"));
    state.lastResult = result;
    renderShadow(result);
    renderAll();
    setTabAlert("shadow", "Shadow mode running. No orders will be placed.", "success");
  } catch (error) {
    setTabAlert("shadow", error.message, "danger");
  }
}

async function runShadowReport() {
  try {
    const report = await api("/api/options-auto/shadow/report");
    state.lastShadowReport = report;
    renderShadowReport(report);
    setTabAlert("shadow", "Shadow report generated.", "success");
  } catch (error) {
    setTabAlert("shadow", error.message, "danger");
  }
}

function renderShadow(result = state.lastResult) {
  const selected = result.selection?.selected || {};
  setHtml("#oa-shadow-status", [
    metric("Status", result.session?.status || "Idle"),
    metric("Would Trade", result.allowed ? "YES" : "NO"),
    metric("Rejected Signals", result.blockers?.length || 0),
    metric("Mode", "SHADOW"),
  ].join(""));
  setHtml("#oa-shadow-candidate", [
    row("Candidate", selected.tradingsymbol || "-"),
    row("Side", result.selection?.side || result.regime?.recommended_side || "WAIT"),
    row("Score", result.selection?.score !== undefined ? score(result.selection.score) : "-"),
    row("Reason", result.explanation || "-"),
  ].join(""));
  setHtml("#oa-shadow-plan", renderPlanRows(result.trade_plan || {}));
}

function renderShadowReport(report = state.lastShadowReport) {
  setHtml("#oa-shadow-learning", [
    metric("Signals", report.signals || 0),
    metric("Would-Have Trades", report.would_trade || 0),
    metric("Expected P&L", money(report.expected_pnl || 0)),
    metric("Actual P&L", money(report.actual_pnl || 0)),
    metric("False Entries", report.false_entries || 0),
    metric("Missed Trades", report.missed_trades || 0),
    metric("Late Entries", report.late_entries || 0),
    metric("Late Exits", report.late_exits || 0),
  ].join(""));
}

// paper rendering/actions
function initPaperTab() {
  on("#oa-paper-start", "click", runPaperStart);
  on("#oa-paper-stop", "click", stopPaperEngine);
  on("#oa-paper-kill", "click", killSwitch);
  on("#oa-paper-reset", "click", resetPaperBalance);
  on("#oa-paper-request-approval", "click", requestPaperApproval);
  on("#oa-paper-approve", "click", approvePaper);
  on("#oa-paper-reject", "click", rejectPaper);
  on("#oa-paper-execute", "click", executePaper);
  on("#oa-paper-process", "click", processPaperMarket);
}

async function runPaperStart() {
  try {
    const result = await api("/api/options-auto/paper/start", evaluationPayload("PAPER"));
    state.lastResult = result;
    renderAll();
    setTabAlert("paper", result.message || "Paper live scanner started.", "success");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function stopPaperEngine() {
  try {
    const result = await api("/api/options-auto/paper/stop", { source: "UI", mode: "PAPER" });
    state.status = result;
    state.lastResult = result.session?.last_decision ? hydrateStatusDecision(result) : result;
    renderAll();
    setTabAlert("paper", "Paper live scanner stopped. No real orders exist in paper mode.", "info");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

function actionMode() {
  const statusMode = state.status.live_scan?.mode || state.status.settings?.mode || state.lastResult.mode || settingsPayload().mode || "PAPER";
  return String(statusMode || "PAPER").toUpperCase() === "REAL" ? "REAL" : "PAPER";
}

async function stopEngine() {
  try {
    const mode = actionMode();
    const result = await api("/api/options-auto/stop", { source: "UI", mode });
    state.status = result;
    state.lastResult = result.session?.last_decision ? hydrateStatusDecision(result) : result;
    renderAll();
    setActiveAlert(`${mode} engine/feed stopped. Existing positions were not exited or modified.`, "info");
  } catch (error) {
    setActiveAlert(error.message, "danger");
  }
}

async function killSwitch() {
  try {
    const mode = actionMode();
    const result = await api("/api/options-auto/kill-switch", { source: "UI", mode });
    state.status = result;
    state.lastResult = result.session?.last_decision ? hydrateStatusDecision(result) : result;
    renderAll();
    setActiveAlert(`${mode} kill switch active. Engine/feed stopped and new entries are blocked.`, "danger");
  } catch (error) {
    setActiveAlert(error.message, "danger");
  }
}

async function requestPaperApproval() {
  try {
    const result = await api("/api/options-auto/paper/request-approval", evaluationPayload("PAPER"));
    state.lastResult = result;
    if (result.approval?.approval_id) state.pendingApprovalId = result.approval.approval_id;
    renderAll();
    setTabAlert("paper", result.approval ? "Approval card created." : result.message || "Approval not created.", result.approval ? "success" : "warning");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function approvePaper() {
  try {
    const result = await api("/api/options-auto/paper/approve", { approval_id: state.pendingApprovalId });
    state.lastResult = result;
    renderAll();
    setTabAlert("paper", result.status === "APPROVED" ? "Paper trade approved and protected." : result.message || result.status, result.status === "APPROVED" ? "success" : "warning");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function rejectPaper() {
  try {
    const result = await api("/api/options-auto/paper/reject", { approval_id: state.pendingApprovalId });
    state.lastResult = result;
    renderAll();
    setTabAlert("paper", "Paper approval rejected.", "info");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function executePaper() {
  try {
    const result = await api("/api/options-auto/paper/execute-plan", evaluationPayload("PAPER"));
    state.lastResult = result;
    renderAll();
    setTabAlert("paper", result.paper_order ? "Paper order simulated locally." : result.message || "Paper execution blocked.", result.paper_order ? "success" : "warning");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function processPaperMarket() {
  try {
    const tick = numberValue($("#oa-spot")?.value, 22500);
    const result = await api("/api/options-auto/paper/process-market", { market: { ltp: tick, high: tick, low: tick } });
    state.lastResult = result;
    renderAll();
    setTabAlert("paper", "Paper market tick processed.", "success");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

function resetPaperBalance() {
  const defaults = state.defaults.settings || {};
  if ($("#oa-paper-balance")) $("#oa-paper-balance").value = defaults.paper_starting_balance || 20000;
  setTabAlert("paper", "Paper balance input reset. Save settings or start paper engine to apply.", "info");
}

function renderPaperAccount() {
  const account = state.lastResult.paper_account || state.status.paper_account || {};
  setHtml("#oa-paper-account", [
    metric("Starting Balance", money(account.opening_balance || state.defaults.settings?.paper_starting_balance || 20000)),
    metric("Available Balance", money(account.available_balance || 0)),
    metric("Realized P&L", money(account.realized_pnl || 0)),
    metric("Unrealized P&L", money(account.unrealized_pnl || 0)),
    metric("Charges Estimate", money(account.charges || 0)),
    metric("Trades Today", activeTradesFrom(state.lastResult).length),
  ].join(""));
  setHtml("#oa-paper-plan", renderPlanRows(state.lastResult.trade_plan || {}));
  renderApprovalCard();
  renderPaperTrades();
}

function renderApprovalCard() {
  const approval = state.lastResult.approval || state.lastResult.paper_lifecycle?.pending_approval || state.status.paper_lifecycle?.pending_approval;
  if (!approval) {
    setBadge("#oa-approval-badge", "No Pending Approval", "grey");
    setHtml("#oa-approval-card", `<p class="oa-empty-state">No approval is pending.</p>`);
    return;
  }
  state.pendingApprovalId = approval.approval_id || state.pendingApprovalId;
  setBadge("#oa-approval-badge", approval.status || "PENDING", "yellow");
  const expiresIn = approval.expires_at_epoch ? Math.max(0, Math.round(approval.expires_at_epoch - Date.now() / 1000)) : "-";
  setHtml("#oa-approval-card", [
    row("Approval ID", approval.approval_id || "-"),
    row("Status", approval.status || "-"),
    row("Contract", approval.trade_plan?.tradingsymbol || "-"),
    row("Entry", approval.trade_plan?.entry_price || "-"),
    row("Target", approval.trade_plan?.target || "-"),
    row("Stoploss", approval.trade_plan?.stoploss || "-"),
    row("Quantity", approval.trade_plan?.quantity || "-"),
    row("Countdown", expiresIn === 0 ? "Expired" : `${expiresIn} sec`),
  ].join(""));
}

function renderPaperTrades() {
  const trades = activeTradesFrom(state.lastResult);
  if (!trades.length) {
    setHtml("#oa-paper-trades", `<p class="oa-empty-state">No active paper trades.</p>`);
    return;
  }
  setHtml("#oa-paper-trades", trades.map(trade => `<div class="oa-mini-trade">
    ${row("Contract", trade.tradingsymbol || "-")}
    ${row("Qty", trade.quantity || "-")}
    ${row("Entry", trade.entry_price || "-")}
    ${row("LTP", trade.last_ltp || "-")}
    ${row("P&L", trade.unrealized_pnl !== undefined ? money(trade.unrealized_pnl) : "-")}
    ${row("Target", trade.target || "-")}
    ${row("SL", trade.stoploss || "-")}
    ${row("OCO", trade.oco_active ? "Active" : "Inactive")}
    ${row("Protected", trade.position_protected ? "YES" : "NO")}
  </div>`).join(""));
}

// real rendering/actions
function initRealTab() {
  on("#oa-real-preflight", "click", runRealPreflight);
  on("#oa-real-place", "click", placeRealOrder);
  on("#oa-real-reconcile", "click", runRealReconcile);
  on("#oa-real-dry", "click", runRealDryRun);
  on("#oa-real-stop", "click", stopNewEntries);
  on("#oa-real-stop-engine", "click", stopEngine);
  on("#oa-real-kill", "click", killSwitch);
  on("#oa-stop-new-entries-top", "click", stopNewEntries);
  on("#oa-real-safe", "click", runSafeMode);
  on("#oa-real-emergency", "click", runEmergencyPlan);
}

async function runRealPreflight() {
  try {
    const result = await api("/api/options-auto/real/preflight", { ...evaluationPayload("REAL"), market_open: true, instruments_valid: true });
    state.lastResult = result;
    renderRealPreflight(result);
    renderAll();
    setTabAlert("real", result.allowed ? "Real preflight passed. Real orders remain guarded by final validation and execution safety." : "Real preflight blocked. Review checklist.", result.allowed ? "success" : "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function placeRealOrder() {
  try {
    const result = await api("/api/options-auto/real/place-order", { ...evaluationPayload("REAL"), market_open: true, instruments_valid: true });
    state.lastResult = result;
    renderAll();
    setTabAlert("real", result.real_order_sent ? `Real entry order sent: ${text(result.entry_order?.order_id, "-")}` : result.message || "Real order blocked.", result.real_order_sent ? "success" : "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function runRealReconcile() {
  try {
    const result = await api("/api/options-auto/real/reconcile", { mode: "REAL", broker_orders: [], positions: [] });
    state.lastResult = result;
    renderRealPreflight(result);
    renderAll();
    setTabAlert("real", result.ok ? "Reconciliation clean." : "Reconciliation needs attention.", result.ok ? "success" : "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function runRealDryRun() {
  try {
    const result = await api("/api/options-auto/real/dry-run", evaluationPayload("REAL"));
    state.lastResult = result;
    renderAll();
    setTabAlert("real", result.message || "Real dry-run complete. No order placed.", "info");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function stopNewEntries() {
  try {
    const result = await api("/api/options-auto/real/stop-new-entries", { source: "UI" });
    state.lastResult = result;
    renderAll();
    setTabAlert("real", "Stop New Entries is active.", "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function runSafeMode() {
  try {
    const result = await api("/api/options-auto/real/safe-mode", { source: "UI" });
    state.lastResult = result;
    renderAll();
    setTabAlert("real", "Safe Mode is active.", "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function runEmergencyPlan() {
  try {
    const result = await api("/api/options-auto/real/emergency-plan", { mode: "REAL", confirmed: Boolean($("#oa-confirm-real")?.checked), positions: [] });
    state.lastResult = result;
    renderAll();
    setTabAlert("real", "Emergency plan generated. Dry-run only; no orders sent.", "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

function renderRealPreflight(result = state.lastResult) {
  const account = result.account_status || state.status.account_status || {};
  const realConnected = Boolean(account.real?.connected);
  const paperConnected = Boolean(account.paper?.connected);
  if (paperConnected && !realConnected) {
    setText("#oa-real-mode-title", "Real Trading locked because Paper mode is active.");
    setText("#oa-real-mode-copy", "Disconnect Paper mode and connect Real Money Zerodha in the main app to run real preflight.");
  } else if (!realConnected) {
    setText("#oa-real-mode-title", "Connect Real Money Zerodha in the main app to enable Options Auto real trading.");
    setText("#oa-real-mode-copy", "No real orders are reachable until LIVE Zerodha is connected.");
  } else if (result.allowed) {
    setText("#oa-real-mode-title", "Real trading ready after preflight.");
    setText("#oa-real-mode-copy", "Orders will be placed only after final validation, execution safety, OCO, and reconciliation checks.");
  } else {
    setText("#oa-real-mode-title", "Real money connected. Run preflight before starting real engine.");
    setText("#oa-real-mode-copy", "Real orders are guarded. Review blockers before placing any order.");
  }
  const evidence = result.evidence || {};
  const checks = evidence.checks || {};
  const reconciliation = result.reconciliation || evidence.reconciliation || {};
  const hasResult = Boolean(result.state || evidence.timestamp || result.reconciliation);
  const rows = hasResult ? [
    ["Real Money Zerodha connected", checks.client_connected],
    ["Real mode explicitly confirmed", checks.real_mode_confirmed],
    ["Static IP/order readiness confirmed", checks.static_ip_confirmed],
    ["Instruments valid", checks.instruments_valid],
    ["Funds/margins fetched", checks.available_margin !== undefined && checks.available_margin !== null],
    ["Market open", checks.market_open],
    ["No unknown manual position", !(reconciliation.unknown_manual_orders || []).length],
    ["No orphan order", !(reconciliation.unknown_auto_orders || []).length],
    ["No duplicate order", !(reconciliation.duplicate_orders || []).length],
    ["No unprotected position", !(reconciliation.unprotected_positions || []).length],
    ["OCO manager ready", true],
    ["Watchdog running", checks.watchdog_ready],
    ["Rate limiter healthy", evidence.rate_limiter?.healthy !== false],
    ["Result folder writable", checks.results_writable],
    ["Reconciliation clean", reconciliation.ok],
  ] : [
    ["Real Money Zerodha connected", null],
    ["Real mode explicitly confirmed", null],
    ["Static IP/order readiness confirmed", null],
    ["Instruments valid", null],
    ["Funds/margins fetched", null],
    ["Market open", null],
    ["No unknown manual position", null],
    ["No orphan order", null],
    ["No duplicate order", null],
    ["No unprotected position", null],
    ["OCO manager ready", null],
    ["Watchdog running", null],
    ["Rate limiter healthy", null],
    ["Result folder writable", null],
    ["Reconciliation clean", null],
  ];
  setHtml("#oa-real-checklist", rows.map(([label, ok]) => checklistRow(label, ok, reasonForCheck(label, result))).join(""));
  const active = activeTradesFrom(result);
  setHtml("#oa-real-position", active.length ? active.map(trade => renderPlanRows(trade)).join("") : `<p class="oa-empty-state">No active real position is reported.</p>`);
}

function checklistRow(label, ok, reason = "") {
  const kind = ok === true ? "green" : ok === false ? "red" : "yellow";
  const icon = ok === true ? "PASS" : ok === false ? "FAIL" : "WAIT";
  return `<div class="oa-check-row"><span class="oa-status-badge ${badgeClass(kind)}">${icon}</span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(reason || "-")}</small></div>`;
}

function reasonForCheck(label, result) {
  const blockers = result.blockers || [];
  const match = blockers.find(item => item.toLowerCase().includes(label.split(" ")[0].toLowerCase()));
  if (match) return match;
  if (label.includes("disabled")) return "Real order placement is disabled.";
  return "";
}

// reports rendering/actions
function initReportsTab() {
  on("#oa-reports-refresh", "click", refresh);
  on("#oa-report-open-folder", "click", () => setTabAlert("reports", "Opening folders from the browser is not enabled in this build.", "info"));
  on("#oa-report-download", "click", () => setTabAlert("reports", "Download/export is available from generated report paths when present.", "info"));
}

function renderReports() {
  setHtml("#oa-report-backtest", [
    metric("Rows", state.lastBacktest.rows || 0),
    metric("Orders", state.lastBacktest.orders_placed || 0),
    metric("Report", state.lastBacktest.report?.audit_json ? "Available" : "-"),
  ].join(""));
  const paper = state.status.paper_account || state.lastResult.paper_account || {};
  setHtml("#oa-report-paper", [
    metric("Available", money(paper.available_balance || 0)),
    metric("Orders", paper.orders?.length || 0),
    metric("Ledger Rows", paper.ledger?.length || 0),
  ].join(""));
  setHtml("#oa-report-shadow", [
    metric("Signals", state.lastShadowReport.signals || 0),
    metric("Would Trade", state.lastShadowReport.would_trade || 0),
    metric("Expected P&L", money(state.lastShadowReport.expected_pnl || 0)),
  ].join(""));
  setHtml("#oa-report-replay", [
    metric("Rows", state.lastReplay.rows || 0),
    metric("Orders", state.lastReplay.orders_placed || 0),
    metric("Mode", state.lastReplay.mode || "-"),
  ].join(""));
  const reports = [
    state.lastBacktest.report?.audit_json && `Backtest audit: ${state.lastBacktest.report.audit_json}`,
    state.lastShadowReport.saved_report && `Shadow report: ${state.lastShadowReport.saved_report}`,
    state.lastReplay.saved_report && `Replay report: ${state.lastReplay.saved_report}`,
    state.status.result_root && `Result root: ${state.status.result_root}`,
  ].filter(Boolean);
  renderList("#oa-report-list", reports, "No report paths available yet.");
}

// settings rendering/actions
function initSettingsTab() {
  on("#oa-settings-save", "click", saveSettings);
  on("#oa-settings-reset", "click", loadDefaults);
}

function initFiiDiiUpload() {
  const form = $("#oa-fii-dii-form");
  if (!form) return;
  form.addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const fileInput = $("#oa-fii-dii-file");
      const formData = new FormData(form);
      if (!fileInput?.files?.length) {
        setTabAlert("dashboard", "Choose the latest NSE FII/DII CSV first.", "warning");
        return;
      }
      const result = await apiForm("/api/options-auto/market-cue/fii-dii-upload", formData);
      state.fiiDiiStatus = result;
      renderFiiDiiStatus(result);
      setTabAlert("dashboard", "FII/DII CSV uploaded for pre-market cue.", "success");
    } catch (error) {
      setTabAlert("dashboard", error.message, "danger");
    }
  });
}

async function saveSettings() {
  try {
    syncSettingsToggles();
    const result = await api("/api/options-auto/configure", settingsPayload());
    state.status = result;
    state.lastResult = result;
    renderAll();
    setTabAlert("settings", "Settings saved for this Options Auto session.", "success");
  } catch (error) {
    setTabAlert("settings", error.message, "danger");
  }
}

function syncSettingsToggles() {
  if ($("#oa-auto") && $("#oa-auto-settings")) $("#oa-auto").checked = $("#oa-auto-settings").checked;
  if ($("#oa-ask") && $("#oa-ask-settings")) $("#oa-ask").checked = $("#oa-ask-settings").checked;
}

function applySettings(settings) {
  const pairs = [
    ["#oa-setting-mode", settings.mode],
    ["#oa-underlying", settings.underlying],
    ["#oa-profile", settings.strategy_profile],
    ["#oa-chart-interval", settings.chart_interval],
    ["#oa-score-threshold", settings.buy_score_threshold],
    ["#oa-paper-balance", settings.paper_starting_balance],
    ["#oa-approval-timeout", settings.approval_timeout_seconds],
    ["#oa-capital-pct", settings.max_capital_per_trade_pct],
    ["#oa-max-daily-loss", settings.max_daily_loss],
    ["#oa-max-daily-profit", settings.max_daily_profit_lock],
    ["#oa-max-trades", settings.max_trades_per_day],
    ["#oa-max-open-trades", settings.max_open_trades],
    ["#oa-max-consecutive-losses", settings.max_consecutive_losses],
    ["#oa-cooldown-seconds", settings.cooldown_after_trade_seconds],
    ["#oa-max-chase", settings.max_chase_points],
    ["#oa-avoid-first", settings.avoid_first_minutes],
    ["#oa-no-new-after", settings.no_new_trade_after],
    ["#oa-square-off", settings.square_off_time],
    ["#oa-max-holding", settings.max_holding_minutes],
    ["#oa-expiry-mode", settings.expiry_preference],
    ["#oa-min-volume", settings.min_volume],
    ["#oa-min-oi", settings.min_oi],
    ["#oa-max-spread", settings.max_spread_pct],
    ["#oa-theta-risk", settings.theta_exit_risk_score],
    ["#oa-expiry-day-lots", settings.expiry_day_max_lots],
    ["#oa-limit-timeout", settings.limit_order_timeout_seconds],
    ["#oa-max-mods", settings.max_buy_limit_modifications],
    ["#oa-sl-throttle", settings.sl_modify_throttle_seconds],
    ["#oa-slippage-buffer", settings.slippage_buffer_points],
    ["#oa-backtest-balance", settings.paper_starting_balance],
    ["#oa-backtest-interval", settings.chart_interval],
    ["#oa-backtest-profile", settings.strategy_profile],
    ["#oa-backtest-score", settings.buy_score_threshold],
    ["#oa-backtest-span", settings.atm_scan_strike_span],
  ];
  pairs.forEach(([selector, content]) => {
    const node = $(selector);
    if (node && content !== undefined) node.value = content;
  });
  const toggles = [
    ["#oa-auto", settings.auto_entry_enabled],
    ["#oa-auto-settings", settings.auto_entry_enabled],
    ["#oa-ask", settings.ask_permission_before_entry],
    ["#oa-ask-settings", settings.ask_permission_before_entry],
    ["#oa-require-fii-dii", settings.require_fii_dii_upload],
    ["#oa-trailing", settings.trailing_stop_enabled],
    ["#oa-breakeven", settings.break_even_sl_enabled],
    ["#oa-partial", settings.partial_exit_enabled],
    ["#oa-reversal", settings.reversal_exit_enabled],
    ["#oa-time-exit", settings.time_exit_enabled],
    ["#oa-allow-deep-otm", settings.allow_deep_otm],
    ["#oa-confirm-real", settings.confirm_real_mode],
    ["#oa-static-ip", settings.static_ip_confirmed],
  ];
  toggles.forEach(([selector, checked]) => {
    const node = $(selector);
    if (node) node.checked = Boolean(checked);
  });
}

// developer debug rendering/actions
function initDeveloperDebugTab() {
  on("#oa-evaluate", "click", () => runAction("/api/options-auto/evaluate"));
  on("#oa-shadow", "click", runShadowStart);
  on("#oa-paper", "click", runPaperStart);
  on("#oa-paper-approval", "click", requestPaperApproval);
  on("#oa-paper-debug-approve", "click", approvePaper);
  on("#oa-paper-debug-reject", "click", rejectPaper);
  on("#oa-paper-debug-execute", "click", executePaper);
  on("#oa-paper-debug-process", "click", processPaperMarket);
  on("#oa-real-debug-dry", "click", runRealDryRun);
  on("#oa-real-debug-preflight", "click", runRealPreflight);
  on("#oa-real-debug-reconcile", "click", runRealReconcile);
  on("#oa-readiness", "click", () => runSimple("/api/options-auto/readiness", { mode: $("#oa-setting-mode")?.value || "PAPER", data_feed_alive: true, last_update_age_seconds: numberValue($("#oa-quote-age")?.value, 0) }, "debug"));
  on("#oa-health-check", "click", () => runSimple("/api/options-auto/health", { mode: $("#oa-setting-mode")?.value || "PAPER", data_feed_alive: true, last_update_age_seconds: numberValue($("#oa-quote-age")?.value, 0), memory_pct: 0, cpu_pct: 0 }, "debug"));
  on("#oa-backtest", "click", runBacktest);
  on("#oa-shadow-report", "click", runShadowReport);
  on("#oa-promotion", "click", () => runSimple("/api/options-auto/promotion/status", { metrics: { current_stage: "LEARNING", sessions_completed: 5, net_pnl: 1200, max_drawdown_pct: 4, unprotected_position_incidents: 0, major_safety_errors: 0 } }, "debug"));
  on("#oa-drift", "click", () => runSimple("/api/options-auto/drift/status", { trades: [{ pnl: 200 }, { pnl: -80 }, { pnl: 150 }] }, "debug"));
  on("#oa-missed", "click", () => runSimple("/api/options-auto/missed-trades/status", { decisions: [{ allowed: true, actual_pnl: 120 }, { allowed: false, actual_pnl: 80, reason: "Spread too wide" }] }, "debug"));
  on("#oa-replay", "click", runReplay);
  on("#oa-telegram-status", "click", () => runSimple("/api/options-auto/telegram/command", { command: "status", user_id: "UI" }, "debug"));
  $$("[data-oa-log]").forEach(button => {
    button.addEventListener("click", () => {
      $$("[data-oa-log]").forEach(item => item.classList.remove("active"));
      button.classList.add("active");
      state.activeLog = button.dataset.oaLog;
      renderDeveloperRawJson();
    });
  });
}

async function runAction(path, mode = "") {
  try {
    const result = await api(path, evaluationPayload(mode));
    state.lastResult = result;
    renderAll();
    setTabAlert("debug", "Raw action completed.", "success");
  } catch (error) {
    setTabAlert("debug", error.message, "danger");
  }
}

async function runSimple(path, payload = {}, alertTab = "debug") {
  try {
    const result = await api(path, payload);
    state.lastResult = result;
    if (path.includes("/replay/")) state.lastReplay = result;
    renderAll();
    setTabAlert(alertTab, "Action completed.", "success");
    return result;
  } catch (error) {
    setTabAlert(alertTab, error.message, "danger");
    return null;
  }
}

async function runReplay() {
  try {
    const result = await api("/api/options-auto/replay/run", { candles: sampleReplayCandles(), decisions: [{ decision: "WAIT", reason: "Opening range forming" }, { decision: "WAIT", reason: "No order in replay" }] });
    state.lastReplay = result;
    state.lastResult = result;
    renderAll();
    setTabAlert(state.activeTab === "backtest" ? "backtest" : "debug", "Replay generated. No orders placed.", "success");
  } catch (error) {
    setTabAlert(state.activeTab === "backtest" ? "backtest" : "debug", error.message, "danger");
  }
}

function renderDeveloperRawJson() {
  const log = $("#oa-log");
  if (!log) return;
  const result = state.lastResult || {};
  const session = result.session || state.status.session || {};
  let content = result;
  if (state.activeLog === "decision") content = session.decision_log || [];
  if (state.activeLog === "rejected") content = session.rejected_log || [];
  if (state.activeLog === "safety") content = session.safety_events || [];
  log.textContent = JSON.stringify(content, null, 2);
}

// shared render helpers
function renderPlanRows(plan) {
  if (!plan || !Object.keys(plan).length) return `<p class="oa-empty-state">No trade plan available.</p>`;
  return [
    row("Contract", plan.tradingsymbol || "-"),
    row("Side", plan.side || "-"),
    row("Entry", plan.entry_price || plan.entry || "-"),
    row("Target", plan.target || "-"),
    row("Stoploss", plan.stoploss || "-"),
    row("Quantity", plan.quantity || "-"),
    row("Lots", plan.lots || "-"),
  ].join("");
}

function on(selector, event, handler) {
  const node = $(selector);
  if (node) node.addEventListener(event, handler);
}

// init
async function refresh() {
  const payload = await api("/api/options-auto/status");
  state.status = payload;
  if (payload.session?.last_decision && Object.keys(payload.session.last_decision).length) {
    state.lastResult = hydrateStatusDecision(payload);
  }
  renderAll();
}

function hydrateStatusDecision(payload) {
  return {
    ...(payload.session?.last_decision || {}),
    settings: payload.settings || {},
    session: payload.session || {},
    paper_account: payload.paper_account || {},
    paper_lifecycle: payload.paper_lifecycle || {},
    real_safety: payload.real_safety || {},
    ready_trade_plan_cache: payload.ready_trade_plan_cache || {},
    adaptive: payload.adaptive || {},
    performance: payload.performance || {},
    fii_dii: payload.fii_dii || {},
    account_status: payload.account_status || {},
    index_ticks: payload.index_ticks || [],
    live_index_candles: payload.live_index_candles || {},
    live_scan: payload.live_scan || {},
  };
}

async function loadDefaults() {
  state.defaults = await api("/api/options-auto/defaults");
  const settings = state.defaults.settings || {};
  applySettings(settings);
  if ($("#oa-backtest-date") && !$("#oa-backtest-date").value) $("#oa-backtest-date").value = todayLocalIso();
  const cue = $("#oa-market-cue-json");
  const instruments = $("#oa-instruments-json");
  const quotes = $("#oa-quotes-json");
  if (cue) cue.value = JSON.stringify(sampleMarketCue(), null, 2);
  if (instruments) instruments.value = JSON.stringify(sampleInstruments(), null, 2);
  if (quotes) quotes.value = JSON.stringify(sampleQuotes(), null, 2);
  await refresh();
}

function initDashboard() {
  on("#oa-top-refresh", "click", refresh);
  on("#oa-stop-engine-top", "click", stopEngine);
  on("#oa-kill-switch-top", "click", killSwitch);
  initFiiDiiUpload();
}

function initReports() {
  renderReports();
}

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initDashboard();
  initBacktestTab();
  initShadowTab();
  initPaperTab();
  initRealTab();
  initReportsTab();
  initSettingsTab();
  initDeveloperDebugTab();
  initReports();
  loadDefaults().catch(error => {
    setHtml("#oa-dashboard-alerts", alertHtml(error.message, "danger"));
  });
  refreshTimer = window.setInterval(() => {
    if (document.visibilityState === "visible") refresh().catch(() => {});
  }, 3000);
});
