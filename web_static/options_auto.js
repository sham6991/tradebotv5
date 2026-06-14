// state
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  defaults: {},
  status: {},
  lastResult: {},
  lastRealPreflight: {},
  lastBacktest: {},
  lastShadowResult: {},
  lastShadowReport: {},
  lastReplay: {},
  pendingApprovalId: "",
  realPendingApprovalId: "",
  activeLog: "raw",
  activeTab: "dashboard",
  activeTickRole: "INDEX",
  dataSource: "UNKNOWN",
  fiiDiiStatus: {},
  refreshBusy: false,
  fullRefreshBusy: false,
  lastRefreshOkAt: 0,
};

let refreshTimer = null;
const uiCache = {};
const pendingCommands = new Set();

function stableStringify(value) {
  try { return JSON.stringify(value ?? null); }
  catch { return String(value); }
}

function shouldRender(key, value) {
  const next = stableStringify(value);
  if (uiCache[key] === next) return false;
  uiCache[key] = next;
  return true;
}

async function guardedCommand(key, button, fn) {
  if (pendingCommands.has(key)) return null;
  pendingCommands.add(key);
  if (button) {
    button.disabled = true;
    button.dataset.pending = "true";
  }
  try {
    return await fn();
  } finally {
    pendingCommands.delete(key);
    if (button) {
      button.disabled = false;
      button.dataset.pending = "false";
    }
  }
}

// api helper
async function api(path, payload, requestOptions = {}) {
  const timeoutMs = requestTimeoutMs(path, payload, requestOptions);
  const timeoutReason = requestTimeoutMessage(path, timeoutMs);
  const controller = new AbortController();
  let didTimeout = false;
  const timer = window.setTimeout(() => {
    didTimeout = true;
    try {
      controller.abort(new DOMException(timeoutReason, "TimeoutError"));
    } catch {
      controller.abort();
    }
  }, timeoutMs);
  const fetchOptions = payload === undefined
    ? {}
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      };
  const requestPath = payload === undefined ? withCacheBuster(path) : path;
  try {
    const response = await fetch(requestPath, { ...fetchOptions, cache: "no-store", signal: controller.signal });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || response.statusText);
    return data;
  } catch (error) {
    if (didTimeout || error?.name === "AbortError" || error?.name === "TimeoutError" || /aborted without reason/i.test(String(error?.message || ""))) {
      throw new Error(timeoutReason);
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function withCacheBuster(path) {
  const separator = String(path || "").includes("?") ? "&" : "?";
  return `${path}${separator}_=${Date.now()}`;
}

function requestTimeoutMs(path, payload, requestOptions = {}) {
  if (requestOptions.timeoutMs !== undefined) return Number(requestOptions.timeoutMs);
  if (path === "/api/options-auto/backtest/run") return 180000;
  if (path === "/api/options-auto/paper/start") return 30000;
  if (path === "/api/options-auto/real/start-engine" || path === "/api/options-auto/real/approve-entry") return 30000;
  if (path === "/api/options-auto/evaluate" || path === "/api/options-auto/paper/execute-plan" || path === "/api/options-auto/paper/request-approval") return 30000;
  return Number(payload === undefined ? 3000 : 5000);
}

function requestTimeoutMessage(path, timeoutMs) {
  const seconds = Math.max(1, Math.round(Number(timeoutMs || 0) / 1000));
  if (path === "/api/options-auto/backtest/run") {
    return `Backtest request timed out after ${seconds}s. The server may still be finishing; refresh Backtest/Reports before running it again.`;
  }
  if (path === "/api/options-auto/paper/start") {
    return `Paper session start timed out after ${seconds}s. Refresh Options Auto before pressing Start Paper Engine again.`;
  }
  return `Options Auto request timed out after ${seconds}s. Refresh the page and check the latest session state before retrying.`;
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

function checkedValue(selector, fallback = false) {
  const node = $(selector);
  return node ? Boolean(node.checked) : Boolean(fallback);
}

function pairedCheckboxValue(primarySelector, secondarySelector, fallback = false) {
  const primary = $(primarySelector);
  const secondary = $(secondarySelector);
  if (state.activeTab === "settings" && secondary) return Boolean(secondary.checked);
  if (primary) return Boolean(primary.checked);
  if (secondary) return Boolean(secondary.checked);
  return Boolean(fallback);
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

function normalizeEntryMode(input) {
  const mode = text(input, "FULL_CONFIRMATION").trim().toUpperCase();
  if (["SIMPLE", "SIMPLE_OHLCV", "MAIN_APP_STYLE", "OHLCV_VOLUME", "OHLCV_VOLUME_PROFILE"].includes(mode)) {
    return "OHLCV_VOLUME_PROFILE";
  }
  return "FULL_CONFIRMATION";
}

function latency(item) {
  if (!item || typeof item !== "object") return "-";
  return `${numberValue(item.p95_ms ?? item.p95 ?? item.latest_ms ?? item.last_ms, 0).toFixed(0)} ms`;
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
  if (node && node.textContent !== text(content)) node.textContent = text(content);
}

function setHtml(id, html) {
  const node = $(id);
  if (!node || !shouldRender(`html:${id}`, html)) return;
  node.innerHTML = html;
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
  const saved = state.status.settings || state.defaults.settings || {};
  const dryRunRealNode = $("#oa-dry-run-real");
  return {
    mode,
    underlying: $("#oa-underlying")?.value || $("#oa-backtest-symbol")?.value || "NIFTY",
    expiry: $("#oa-expiry-date")?.value || $("#oa-backtest-expiry")?.value || "",
    option_expiry: $("#oa-expiry-date")?.value || $("#oa-backtest-expiry")?.value || "",
    chart_interval: $("#oa-chart-interval")?.value || $("#oa-backtest-interval")?.value || "3minute",
    strategy_profile: $("#oa-profile")?.value || $("#oa-backtest-profile")?.value || "BALANCED",
    entry_dependency_mode: normalizeEntryMode($("#oa-entry-mode")?.value || $("#oa-backtest-entry-mode")?.value),
    number_of_lots: numberValue($("#oa-lots")?.value || $("#oa-backtest-lots")?.value, 1),
    buy_score_threshold: numberValue($("#oa-score-threshold")?.value, 50),
    paper_starting_balance: numberValue($("#oa-paper-balance")?.value, saved.paper_starting_balance ?? 20000),
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
    major_strike_selection_enabled: true,
    use_major_strikes_only: true,
    major_strike_step: numberValue($("#oa-major-strike-step")?.value || $("#oa-backtest-major-step")?.value, 100),
    contract_reselection_minutes: numberValue($("#oa-contract-reselect-minutes")?.value, 60),
    max_hop_strikes: 5,
    lock_contracts_until_trade_or_timeout: true,
    reselect_after_exit_cooldown: true,
    strict_liquidity_filter: Boolean($("#oa-strict-liquidity")?.checked),
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
    ask_permission_before_entry: pairedCheckboxValue("#oa-ask", "#oa-ask-settings", saved.ask_permission_before_entry),
    auto_entry_enabled: pairedCheckboxValue("#oa-auto", "#oa-auto-settings", saved.auto_entry_enabled),
    require_fii_dii_upload: checkedValue("#oa-require-fii-dii", saved.require_fii_dii_upload),
    news_event_enabled: checkedValue("#oa-news-event-enabled", saved.news_event_enabled),
    news_event_provider: $("#oa-news-event-provider")?.value || saved.news_event_provider || "ZERODHA_PULSE",
    news_event_cache_ttl_seconds: numberValue($("#oa-news-cache-ttl")?.value, saved.news_event_cache_ttl_seconds ?? saved.news_refresh_ttl_seconds ?? 300),
    news_event_min_score_for_warning: numberValue($("#oa-news-warning-score")?.value, saved.news_event_min_score_for_warning ?? 40),
    news_event_min_score_for_shock: numberValue($("#oa-news-shock-score")?.value, saved.news_event_min_score_for_shock ?? 70),
    news_event_require_market_confirmation: checkedValue("#oa-news-market-confirm", saved.news_event_require_market_confirmation),
    news_event_show_in_ui: checkedValue("#oa-news-show-ui", saved.news_event_show_in_ui),
    allow_demo_data: state.activeTab === "debug",
    confirm_real_mode: checkedValue("#oa-confirm-real", saved.confirm_real_mode),
    static_ip_confirmed: checkedValue("#oa-static-ip", saved.static_ip_confirmed),
    dry_run_real_only: dryRunRealNode ? Boolean(dryRunRealNode.checked) : true,
    real_orders_enabled: checkedValue("#oa-real-orders-enabled", saved.real_orders_enabled),
    real_auto_entry_enabled: checkedValue("#oa-real-auto-entry", saved.real_auto_entry_enabled),
    market_context_enforcement_enabled: checkedValue("#oa-market-context-enforced", saved.market_context_enforcement_enabled),
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
  const expiry = $("#oa-backtest-expiry")?.value || $("#oa-expiry-date")?.value || "";
  const backtestSpot = $("#oa-backtest-spot")?.value || "";
  const span = numberValue($("#oa-backtest-span")?.value, 4);
  return {
    data_source: "zerodha_historical",
    underlying,
    expiry,
    option_expiry: expiry,
    interval,
    trade_date: tradeDate,
    backtest_spot: backtestSpot,
    settings: {
      ...settingsPayload("BACKTEST"),
      underlying,
      chart_interval: interval,
      paper_starting_balance: numberValue($("#oa-backtest-balance")?.value, 20000),
      strategy_profile: $("#oa-backtest-profile")?.value || "BALANCED",
      entry_dependency_mode: normalizeEntryMode($("#oa-backtest-entry-mode")?.value || $("#oa-entry-mode")?.value),
      number_of_lots: numberValue($("#oa-backtest-lots")?.value || $("#oa-lots")?.value, 1),
      major_strike_step: numberValue($("#oa-backtest-major-step")?.value || $("#oa-major-strike-step")?.value, 100),
      max_trades_per_day: numberValue($("#oa-backtest-max-trades")?.value, 3),
      buy_score_threshold: numberValue($("#oa-backtest-score")?.value, 50),
      atm_scan_strike_span: span,
      backtest_compare_market_context_scenarios: true,
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

function initTickStreamTabs() {
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-oa-tick-role]");
    if (!button) return;
    state.activeTickRole = String(button.dataset.oaTickRole || "INDEX").toUpperCase();
    renderIndexTickStreams();
  });
}

// status rendering
function renderTopStatus() {
  const status = state.status || {};
  const result = state.lastResult || {};
  const mode = selectedModeFromState(result);
  const tradeMode = tradingModeFromState(result);
  const session = sessionDisplayState(tradeMode, status);
  const isReal = tradeMode === "REAL";
  const connected = session.connected;
  const activeTrades = activeTradesFrom(status, { currentOnly: true });
  const hasActiveTrades = Boolean(activeTrades.length);
  const protectedOk = !activeTrades.length || activeTrades.every(trade => trade.position_protected);
  const ocoOk = !activeTrades.length || activeTrades.every(trade => trade.oco_active);
  const lifecycle = isCurrentRealProcessActive(status) ? (status.real_order_lifecycle || result.real_order_lifecycle || {}) : {};
  const protectedState = String(lifecycle.protected_state || (protectedOk ? "PROTECTED" : "INACTIVE")).toUpperCase();
  const protectionFailed = /FAILED|UNPROTECTED|RECONCILIATION/.test(protectedState) || lifecycle.state === "UNPROTECTED_POSITION";
  const protectionActive = hasActiveTrades && (/PROTECTIVE_EXIT_ACTIVE|PROTECTED/.test(protectedState) || protectedOk);
  const dataQuality = status.data_quality || result.data_quality || {};
  const dataAllowed = dataQuality.allowed;
  const resultDemo = Boolean(status.demo_data || result.demo_data || dataQuality.blockers?.some(item => String(item).includes("demo/sample")));
  const dataSource = status.data_source || result.data_source || state.dataSource || "UNKNOWN";
  const dataLabel = resultDemo ? "Demo" : dataAllowed ? "Fresh" : dataSource === "LIVE" ? "Stale" : "Waiting";
  const governor = status.governor || result.governor || {};
  const governorLabel = governor.allowed === true ? "Allow" : governor.state ? "Blocked" : "Waiting";
  const engine = session.label;
  const pnl = realizedPnl();

  setBadge("#oa-mode", mode, isReal ? "red" : mode === "PAPER" ? "green" : "blue");
  setBadge("#oa-real-money", isReal ? "YES" : "NO", isReal ? "red" : "green");
  setBadge("#oa-kite", connected ? "Connected" : "Disconnected", connected ? "green" : "red");
  setBadge("#oa-data", dataLabel, dataAllowed ? "green" : resultDemo ? "yellow" : "yellow");
  setBadge("#oa-governor", governorLabel, governor.allowed === true ? "green" : governor.state ? "yellow" : "grey");
  setBadge("#oa-engine", engine, session.running ? "green" : connected ? "yellow" : "red");
  setBadge("#oa-position", hasActiveTrades ? (protectedOk ? "Protected" : "Unprotected") : "No Position", hasActiveTrades ? (protectedOk ? "green" : "red") : "grey");
  setBadge("#oa-protection", protectionFailed ? "Failed" : protectionActive ? "Protected" : "Inactive", protectionFailed ? "red" : protectionActive ? "green" : "grey");
  setBadge("#oa-oco", activeTrades.length ? (ocoOk ? "Active" : "Inactive") : "Inactive", activeTrades.length ? (ocoOk ? "green" : "red") : "grey");
  setBadge("#oa-kill-state", status.real_safety?.safe_mode || status.session?.status === "KILL_SWITCH_ACTIVE" ? "ON" : "OFF", status.real_safety?.safe_mode ? "red" : "green");
  setBadge("#oa-daily-pnl", money(pnl), pnl > 0 ? "green" : pnl < 0 ? "red" : "grey");
}

function renderAll() {
  renderTopStatus();
  renderCockpitSafety();
  renderDashboard();
  renderIndustryDiagnostics();
  renderIndexTickStreams();
  renderContractLockCards();
  renderBacktestResults();
  renderShadow();
  renderRealPreflight();
  renderRealApprovalCard();
  renderPaperAccount();
  renderReports();
  renderDeveloperRawJson();
}

function renderCockpitSafety(result = state.status || state.lastResult) {
  result = result || {};
  const status = state.status || {};
  const mode = tradingModeFromState(result);
  const account = accountStatusFromState(result);
  const isReal = mode === "REAL";
  const session = sessionDisplayState(mode, result);
  const connected = session.connected;
  const lifecycle = isCurrentRealProcessActive(result) ? (result.real_order_lifecycle || status.real_order_lifecycle || {}) : {};
  const lifecycleState = String(lifecycle.state || "IDLE").toUpperCase();
  const protectedState = String(lifecycle.protected_state || "FLAT").toUpperCase();
  const lock = contractLockFromState({ liveOnly: true });
  const feed = status.options_live_feed || {};
  const feedHealth = feed.health || {};
  const dataHealthy = !(feedHealth.stale || feedHealth.feed_stale) && !status.live_scan?.blocked;
  const governor = result.governor || {};
  const blockers = [];
  const connectionLabel = isReal ? "Real Money Zerodha" : "Paper Zerodha data";
  if (!connected) blockers.push(`${connectionLabel} not connected`);
  if (connected && !session.running) blockers.push("Session not started");
  if (isReal && !account.real?.connected) blockers.push("Real money is locked");
  if (!dataHealthy) blockers.push("Feed is waiting or stale");
  if (!(lock.ce && lock.pe)) blockers.push("CE/PE contracts are not locked");
  if (governor.allowed === false) blockers.push(...(governor.blockers || ["Governor is blocking"]));
  if (/UNPROTECTED|FAILED|RECONCILIATION/.test(`${lifecycleState} ${protectedState}`)) blockers.push("Position protection requires manual attention");
  const canTrade = blockers.length === 0 && (mode !== "REAL" || account.real?.connected);
  setBadge("#oa-can-trade-badge", canTrade ? "YES" : "NO", canTrade ? "green" : "red");
  setHtml("#oa-can-trade-body", [
    checklistRow("Kite connected", connected, connected ? "Connected" : "Connect in Main App"),
    checklistRow("Access token valid", connected, connected ? "Token available" : "Login required"),
    checklistRow("Live session started", session.running, session.running ? "Live scanner is running" : session.message),
    checklistRow("Feed fresh", dataHealthy, dataHealthy ? "Fresh" : "Waiting/stale"),
    checklistRow("Contract locked", Boolean(lock.ce && lock.pe), lock.ce && lock.pe ? "CE and PE locked" : "Start engine to lock contracts"),
    checklistRow("Governor ready", governor.allowed !== false, (governor.blockers || [])[0] || "No blocker"),
    checklistRow("No unsafe open position", !/UNPROTECTED|FAILED|RECONCILIATION/.test(`${lifecycleState} ${protectedState}`), protectedState),
    checklistRow("Real money armed", mode !== "REAL" || account.real?.connected, mode === "REAL" ? "Real connection required" : "Paper/shadow/backtest"),
  ].join(""));
  renderDangerBanner(lifecycle, blockers, lock);
  renderLifecycleTimeline(lifecycle, currentLiveDecisionFromState());
  renderContractCards(lock);
  renderRealWorkflow(canTrade, blockers);
}

function renderDangerBanner(lifecycle = {}, blockers = [], lock = {}) {
  const stateText = String(lifecycle.state || "").toUpperCase();
  const protectedState = String(lifecycle.protected_state || "").toUpperCase();
  const danger = /UNPROTECTED|FAILED|RECONCILIATION|KILL_SWITCH|REJECTED|PARTIAL_FILL_UNRESOLVED|BROKER_STATE_UNKNOWN/.test(`${stateText} ${protectedState}`);
  const node = $("#oa-danger-banner");
  if (!node) return;
  node.hidden = !danger;
  if (!danger) return;
  const fill = lifecycle.fill || {};
  node.innerHTML = [
    `<strong>DANGER: POSITION PROTECTION REQUIRES ATTENTION</strong>`,
    `<div>State: ${escapeHtml(stateText || "-")} | Protected State: ${escapeHtml(protectedState || "-")}</div>`,
    `<div>Instrument: ${escapeHtml(lifecycle.entry_order?.tradingsymbol || lock.ce?.tradingsymbol || lock.pe?.tradingsymbol || "-")}</div>`,
    `<div>Qty: ${escapeHtml(fill.filled_quantity || lifecycle.entry_order?.quantity || "-")} | Avg: ${escapeHtml(fill.average_price || "-")}</div>`,
    `<div>Manual action: Open Kite orderbook and verify position, target, and stoploss immediately.</div>`,
    `<div>Blocker: ${escapeHtml((lifecycle.blockers || blockers || [])[0] || "-")}</div>`,
  ].join("");
}

function renderLifecycleTimeline(lifecycle = {}, currentDecision = {}) {
  const stateText = String(lifecycle.state || "IDLE").toUpperCase();
  const protectedState = String(lifecycle.protected_state || "FLAT").toUpperCase();
  setBadge("#oa-lifecycle-stage", protectedState || stateText, /FAILED|UNPROTECTED|RECONCILIATION/.test(`${stateText} ${protectedState}`) ? "red" : /ACTIVE|OCO|FLAT/.test(`${stateText} ${protectedState}`) ? "green" : "yellow");
  const steps = [
    ["Waiting for signal", !currentDecision.allowed],
    ["Signal accepted", Boolean(currentDecision.allowed)],
    ["Entry submitted", /ENTRY|PROTECTION|OCO|TARGET|SL|EXIT|UNPROTECTED/.test(stateText)],
    ["Entry open", /ENTRY_ORDER_OPEN/.test(stateText)],
    ["Entry partial", /ENTRY_PARTIAL/.test(stateText)],
    ["Entry filled", /ENTRY_COMPLETE|PROTECTION|OCO|TARGET|SL|EXIT|UNPROTECTED/.test(stateText)],
    ["Protective exit placing", protectedState === "PROTECTIVE_EXIT_PLACING"],
    ["Protective exit active", protectedState === "PROTECTIVE_EXIT_ACTIVE" || stateText === "OCO_ACTIVE"],
    ["Position protected", protectedState === "PROTECTIVE_EXIT_ACTIVE" || stateText === "OCO_ACTIVE"],
    ["Exit filled", /TARGET_FILLED|SL_FILLED|EXIT_RECONCILED/.test(stateText)],
    ["Flat", protectedState === "FLAT" || stateText === "EXIT_RECONCILED"],
    ["Reconciled", Boolean(lifecycle.reconciliation)],
  ];
  setHtml("#oa-lifecycle-timeline", steps.map(([label, active]) => `<li class="${active ? "is-active" : ""}">${escapeHtml(label)}</li>`).join(""));
}

function renderContractCards(lock = {}) {
  renderContractCard("#oa-ce-contract-card", lock.ce || {}, "CE");
  renderContractCard("#oa-pe-contract-card", lock.pe || {}, "PE");
}

function renderContractCard(selector, contract, label) {
  const node = $(selector);
  if (!node) return;
  const body = node.querySelector(".oa-summary-grid");
  if (!body) return;
  const rows = [
    metric("Symbol", contract.tradingsymbol || `No ${label} locked`),
    metric("Strike", contract.strike || "-"),
    metric("Expiry", contract.expiry || "-"),
    metric("LTP", contract.ltp || contract.premium || "-"),
    metric("Bid", contract.bid || "-"),
    metric("Ask", contract.ask || "-"),
    metric("Spread", contract.spread_pct ?? "-"),
    metric("Bid Qty", contract.bid_qty || "-"),
    metric("Ask Qty", contract.ask_qty || "-"),
    metric("Tick Age", contract.age_seconds !== undefined ? `${contract.age_seconds}s` : "-"),
    metric("Volume", contract.volume || "-"),
    metric("OI", contract.oi || "-"),
    metric("Signal", contract.signal_state || contract.status || "-"),
    metric("Blocker", contract.blocker || contract.hop_reason || "None"),
  ].join("");
  if (shouldRender(`contract:${selector}`, rows)) body.innerHTML = rows;
}

function renderRealWorkflow(canTrade, blockers = []) {
  const account = state.status.account_status || {};
  const connected = Boolean(account.real?.connected);
  const currentReal = isCurrentRealProcessActive(state.status);
  const lifecycle = currentReal ? (state.status.real_order_lifecycle || {}) : {};
  const approval = realApprovalFromState();
  const lock = contractLockFromState({ liveOnly: true });
  const preflight = realPreflightResult();
  const steps = [
    ["Connect real Zerodha in main app", connected],
    ["Verify access token", connected],
    ["Load instruments", Boolean(lock.ce || lock.pe || state.status.instrument_cache)],
    ["Confirm market open", true],
    ["Check margin", Boolean(account.real_margin || account.real?.funds)],
    ["Check feed freshness", !((state.status.options_live_feed?.health || {}).stale || (state.status.options_live_feed?.health || {}).feed_stale)],
    ["Check spread/depth", true],
    ["Run real preflight", hasRealPreflightResult(preflight)],
    ["Run real dry run", state.status.session?.status === "REAL_DRY_RUN_SCANNING"],
    ["Arm real trading", connected && Boolean($("#oa-confirm-real")?.checked)],
    ["Start real scanner", state.status.live_scan?.running && state.status.live_scan?.mode === "REAL"],
    ["Approve setup when required", !approval || approval.status !== "PENDING"],
    ["Monitor lifecycle", currentReal && Boolean(lifecycle.state)],
    ["Stop new entries if needed", true],
    ["Emergency flatten / kill switch only if needed", !/UNPROTECTED|FAILED/.test(`${lifecycle.state} ${lifecycle.protected_state}`)],
  ];
  setBadge("#oa-real-workflow-badge", canTrade ? "READY" : "BLOCKED", canTrade ? "green" : "yellow");
  setHtml("#oa-real-workflow", steps.map(([label, done]) => `<li class="${done ? "is-active" : ""}">${escapeHtml(label)}${!done && blockers[0] ? `<small>${escapeHtml(blockers[0])}</small>` : ""}</li>`).join(""));
}

function hasRealPreflightResult(result = {}) {
  return Boolean(result && typeof result === "object" && (result.state || result.evidence?.timestamp || result.evidence?.checks));
}

function realPreflightResult(result = {}) {
  if (hasRealPreflightResult(result)) return result;
  if (hasRealPreflightResult(state.lastRealPreflight)) return state.lastRealPreflight;
  const runtime = state.status.real_safety?.last_preflight || {};
  if (hasRealPreflightResult(runtime)) return { ...runtime, account_status: state.status.account_status || {} };
  if (hasRealPreflightResult(state.lastResult)) return state.lastResult;
  return {};
}

function realApprovalFromState() {
  const approval = firstNonEmptyObject(
    state.lastResult.approval,
    state.lastResult.real_pending_approval,
    state.lastResult.live_scan_action?.approval,
    state.status.real_pending_approval
  );
  const approvalId = String(approval.approval_id || "");
  const mode = String(approval.mode || "").toUpperCase();
  return mode === "REAL" || approvalId.startsWith("OA-REAL") ? approval : {};
}

function syncRealPreflightCache(payload = {}) {
  const account = payload.account_status || {};
  const sessionStatus = String(payload.session?.status || "").toUpperCase();
  if (!account.real?.connected || sessionStatus === "REAL_DISCONNECTED") {
    state.lastRealPreflight = {};
    return;
  }
  const runtime = payload.real_safety?.last_preflight || {};
  if (hasRealPreflightResult(runtime)) {
    state.lastRealPreflight = { ...runtime, account_status: account };
  }
}

function activeTradesFrom(result = {}, options = {}) {
  if (options.currentOnly) {
    const mode = tradingModeFromState(result);
    if (!liveSessionStarted(mode, result)) return [];
  }
  const trades = firstNonEmptyList(
    result.session?.active_trades,
    result.paper_lifecycle?.active_trades,
    state.status.session?.active_trades,
    state.status.paper_lifecycle?.active_trades,
  );
  const realTrade = lifecycleTradeFromReal(result.real_order_lifecycle || state.status.real_order_lifecycle || {});
  if (!realTrade) return trades;
  const exists = trades.some(trade => trade.entry_order_id && trade.entry_order_id === realTrade.entry_order_id);
  return exists ? trades : [...trades, realTrade];
}

function liveScanFromState(result = {}) {
  return result.live_scan || state.status.live_scan || {};
}

function isLiveModeRunning(mode = "", result = {}) {
  const scan = liveScanFromState(result);
  const expected = String(mode || "").toUpperCase();
  const actual = String(scan.mode || "").toUpperCase();
  return Boolean(scan.running) && (!expected || actual === expected);
}

function selectedModeFromState(result = {}) {
  if (state.activeTab === "real") return "REAL";
  if (state.activeTab === "paper") return "PAPER";
  return String(
    state.status.settings?.mode
    || result.settings?.mode
    || state.status.mode
    || result.mode
    || state.defaults.settings?.mode
    || "PAPER"
  ).toUpperCase();
}

function tradingModeFromState(result = {}) {
  return selectedModeFromState(result) === "REAL" ? "REAL" : "PAPER";
}

function accountStatusFromState(result = {}) {
  return result.account_status || state.status.account_status || {};
}

function modeConnected(mode = "PAPER", result = {}) {
  const account = accountStatusFromState(result);
  return String(mode || "").toUpperCase() === "REAL"
    ? Boolean(account.real?.connected)
    : Boolean(account.paper?.connected);
}

function liveSessionStarted(mode = "PAPER", result = {}) {
  const normalized = String(mode || "").toUpperCase() === "REAL" ? "REAL" : "PAPER";
  return modeConnected(normalized, result) && isLiveModeRunning(normalized, result);
}

function sessionDisplayState(mode = "PAPER", result = {}) {
  const normalized = String(mode || "").toUpperCase() === "REAL" ? "REAL" : "PAPER";
  const connected = modeConnected(normalized, result);
  const running = liveSessionStarted(normalized, result);
  const modeLabel = normalized === "REAL" ? "Real Money" : "Paper";
  const connectionLabel = normalized === "REAL" ? "Real Money Zerodha" : "Paper Zerodha data";
  return {
    mode: normalized,
    modeLabel,
    connected,
    running,
    label: !connected ? "DISCONNECTED" : running ? "RUNNING" : "SESSION NOT STARTED",
    message: !connected
      ? `${connectionLabel} is not connected. Connect it in Main App.`
      : `${modeLabel} connected. Start ${modeLabel} Engine to begin live scanning.`,
  };
}

function currentLiveDecisionFromState() {
  const statusDecision = state.status.session?.last_decision || {};
  if (isCurrentLiveDecision(statusDecision, state.status)) {
    return hydrateStatusDecision(state.status);
  }
  if (isCurrentLiveDecision(state.lastResult, state.lastResult)) {
    return state.lastResult;
  }
  return {};
}

function isCurrentLiveDecision(decision = {}, payload = {}) {
  if (!decision || typeof decision !== "object" || !Object.keys(decision).length) return false;
  const mode = tradingModeFromState(payload);
  if (!liveSessionStarted(mode, payload)) return false;
  const decisionMode = String(decision.mode || payload.mode || payload.settings?.mode || "").toUpperCase();
  if (["BACKTEST", "SHADOW", "REPLAY"].includes(decisionMode)) return false;
  if (decisionMode && (decisionMode === "REAL" ? "REAL" : "PAPER") !== mode) return false;
  const startedAt = payload.live_scan?.started_at || state.status.live_scan?.started_at || "";
  const decisionAt = decision.timestamp || decision.generated_at || decision.datetime || "";
  if (startedAt && !decisionAt) return false;
  if (startedAt && !isTimestampAtOrAfter(decisionAt, startedAt, 1000)) return false;
  return true;
}

function isTimestampAtOrAfter(value, lowerBound, toleranceMs = 0) {
  const observed = Date.parse(value);
  const threshold = Date.parse(lowerBound);
  if (!Number.isFinite(observed) || !Number.isFinite(threshold)) return false;
  return observed + toleranceMs >= threshold;
}

function isPaperLifecycleActive(lifecycle = {}) {
  return Boolean(
    lifecycle.pending_approval
    || (Array.isArray(lifecycle.pending_entries) && lifecycle.pending_entries.length)
    || (Array.isArray(lifecycle.active_trades) && lifecycle.active_trades.length)
  );
}

function isRealLifecycleActive(lifecycle = {}) {
  const lifecycleState = String(lifecycle.state || "").toUpperCase();
  if (!lifecycleState || /^(IDLE|FLAT|TARGET_FILLED|SL_FILLED|EXIT_RECONCILED|CANCELLED|REJECTED)$/.test(lifecycleState)) return false;
  return Boolean(
    lifecycle.entry_order?.order_id
    || lifecycle.target_order?.order_id
    || lifecycle.stoploss_order?.order_id
    || lifecycle.fill?.filled_quantity
    || lifecycle.trade_plan?.tradingsymbol
    || lifecycleState
  );
}

function isCurrentPaperProcessActive(result = state.lastResult) {
  return liveSessionStarted("PAPER", result);
}

function isCurrentRealProcessActive(result = state.lastResult) {
  return liveSessionStarted("REAL", result);
}

function isAnyCurrentLiveProcessActive(result = state.lastResult) {
  return isCurrentPaperProcessActive(result) || isCurrentRealProcessActive(result);
}

function firstNonEmptyList(...lists) {
  return lists.find(items => Array.isArray(items) && items.length) || [];
}

function firstNonEmptyObject(...items) {
  return items.find(item => item && typeof item === "object" && Object.keys(item).length) || {};
}

function paperLifecycleFromState(result = state.lastResult) {
  return firstNonEmptyObject(result.paper_lifecycle, state.status.paper_lifecycle);
}

function paperAccountFromState(result = state.lastResult) {
  const lifecycle = paperLifecycleFromState(result);
  return firstNonEmptyObject(result.paper_account, state.status.paper_account, lifecycle.account);
}

function uniqueByKey(items = [], keyFn) {
  const seen = new Set();
  return (items || []).filter(item => {
    const key = keyFn(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function paperOrdersFromState(result = state.lastResult) {
  const lifecycle = paperLifecycleFromState(result);
  const account = paperAccountFromState(result);
  return uniqueByKey([
    ...(lifecycle.account?.orders || []),
    ...(account.orders || []),
    ...(lifecycle.orders || []),
  ], order => order.order_id || order.id || stableStringify(order));
}

function lifecycleTradeFromReal(lifecycle = {}) {
  const lifecycleState = String(lifecycle.state || "").toUpperCase();
  const protectedState = String(lifecycle.protected_state || "").toUpperCase();
  if (!lifecycleState || /^(IDLE|FLAT)$/.test(lifecycleState)) return null;
  if (/TARGET_FILLED|SL_FILLED|EXIT_RECONCILED|CANCELLED|REJECTED/.test(lifecycleState)) return null;
  const entry = lifecycle.entry_order || {};
  const target = lifecycle.target_order || {};
  const stoploss = lifecycle.stoploss_order || {};
  const fill = lifecycle.fill || {};
  const plan = lifecycle.trade_plan || lifecycle.plan || {};
  const symbol = fill.tradingsymbol || plan.tradingsymbol || entry.tradingsymbol || target.tradingsymbol || stoploss.tradingsymbol || "";
  const hasOrderState = entry.order_id || target.order_id || stoploss.order_id || fill.filled_quantity || symbol;
  if (!hasOrderState) return null;
  const targetOpen = isOpenOrderStatus(target.status);
  const stoplossOpen = isOpenOrderStatus(stoploss.status);
  const protectedActive = protectedState === "PROTECTIVE_EXIT_ACTIVE" || (targetOpen && stoplossOpen);
  return {
    mode: "REAL",
    tradingsymbol: symbol,
    side: plan.side || entry.transaction_type || "-",
    quantity: fill.filled_quantity || entry.filled_quantity || entry.quantity || plan.quantity || target.quantity || stoploss.quantity || "-",
    entry_price: fill.average_price || entry.average_price || plan.entry_price || entry.price || "-",
    average_price: fill.average_price || entry.average_price || "-",
    last_ltp: fill.last_price || plan.last_ltp || "-",
    target: plan.target || target.price || target.trigger_price || "-",
    stoploss: plan.stoploss || stoploss.trigger_price || stoploss.price || "-",
    entry_order_id: entry.order_id || entry.id || "-",
    target_order_id: target.order_id || target.id || "-",
    stoploss_order_id: stoploss.order_id || stoploss.id || "-",
    entry_status: entry.status || "-",
    target_status: target.status || "-",
    stoploss_status: stoploss.status || "-",
    oco_active: protectedActive,
    position_protected: protectedActive,
    status: lifecycle.state || "-",
    protected_state: lifecycle.protected_state || "-",
    updated_at: lifecycle.updated_at || latestLifecycleEvent(lifecycle)?.timestamp || latestLifecycleEvent(lifecycle)?.created_at || "-",
  };
}

function isOpenOrderStatus(status) {
  return /OPEN|TRIGGER PENDING|PENDING/.test(String(status || "").toUpperCase());
}

function orderStatusKind(status) {
  const label = String(status || "").toUpperCase();
  if (/COMPLETE|FILLED/.test(label)) return "green";
  if (/REJECT|CANCEL|FAIL/.test(label)) return "red";
  if (/OPEN|TRIGGER|PENDING|PARTIAL/.test(label)) return "yellow";
  return "grey";
}

function latestLifecycleEvent(source = {}) {
  const rows = Array.isArray(source) ? source : (source.history || source.events || []);
  return rows.length ? rows[rows.length - 1] : null;
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
  const session = sessionDisplayState(tradingModeFromState(state.status), state.status);
  if (!session.running) {
    renderDashboardIdle(session, {});
    return;
  }
  const result = currentLiveDecisionFromState();
  if (!Object.keys(result).length) {
    renderDashboardWaiting(session);
    return;
  }
  const cue = result.market_cue || {};
  const regime = result.regime || {};
  const selection = result.selection || {};
  const selected = selection.selected || {};
  const plan = result.trade_plan || {};
  const watchdog = result.watchdog || {};
  const health = watchdog || {};
  const explainability = result.explainability || result.decision_snapshot?.explainability || {};
  const freshness = result.freshness || result.decision_snapshot?.freshness || {};

  setBadge("#oa-dashboard-cue-badge", cue.cue || "Waiting", cue.recommended_side === "WAIT" ? "yellow" : cue.cue ? "blue" : "grey");
  setText("#oa-cue", cue.cue || "-");
  setText("#oa-cue-score", cue.score !== undefined ? score(cue.score) : "-");
  setText("#oa-cue-confidence", cue.confidence !== undefined ? score(cue.confidence) : "-");
  setText("#oa-cue-updated", cue.last_updated || cue.timestamp || "-");
  setText("#oa-cue-reason", cue.reason || cue.reason_summary || "No market cue evaluated yet.");
  renderFiiDiiStatus(cue.fii_dii_status || result.fii_dii_status || state.fiiDiiStatus || state.status.fii_dii || {});
  renderNewsEventSignal(newsEventSignalFrom(result));

  setBadge("#oa-regime-side", regime.recommended_side || "WAIT", regime.recommended_side === "WAIT" ? "yellow" : "blue");
  setText("#oa-regime", regime.regime || "-");
  setText("#oa-regime-confidence", regime.confidence !== undefined ? score(regime.confidence) : "-");
  setText("#oa-regime-aggression", regime.aggressiveness || "-");
  setText("#oa-regime-block", regime.no_trade_reason || "-");
  renderMarketContext(result.market_context || result.market_playbook || {});
  renderTradeCandidateValidation(result.trade_candidate_validation || {});

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
    row("Governor", result.governor?.state || "-"),
    row("Primary Stage", explainability.primary_block_stage || result.governor?.primary_block_stage || result.governor?.state || "-"),
    row("Primary Blocker", explainability.primary_blocker || result.governor?.primary_blocker || "-"),
    row("Freshness", freshnessStatusText(freshness)),
    row("No Trade Reason", noTradeReason(result)),
    row("Discipline Score", result.discipline?.discipline_score !== undefined ? score(result.discipline.discipline_score) : "-"),
    row("Data Quality", result.data_quality?.allowed ? "PASS" : "WAIT"),
    row("Theta Risk", result.options_risk?.theta_risk_score !== undefined ? score(result.options_risk.theta_risk_score) : "-"),
    row("Spread", selected.spread_pct !== undefined ? percent(selected.spread_pct) : "-"),
    row("Liquidity", selected.breakdown?.liquidity !== undefined ? score(selected.breakdown.liquidity) : "-"),
    row("Reason", result.explanation || "-"),
  ].join(""));

  renderList("#oa-blockers-list", readableBlockers(result), "No blockers. Waiting for a valid setup.");
  renderActiveTradeCard(activeTradesFrom(result, { currentOnly: true }));
  renderDataSourcePanel(result);
  renderRecentEvents(result);
  renderDashboardAlerts(result);
}

function renderMarketContext(context = {}) {
  const permission = context.permission || "WAITING";
  const bad = Boolean(context.would_block) || ["WAIT", "BLOCK", "UNKNOWN"].includes(permission);
  const good = ["ALLOW", "ALLOW_SELECTIVE"].includes(permission);
  setBadge("#oa-market-context-badge", permission, bad ? "yellow" : good ? "green" : "grey");
  setHtml("#oa-market-context-panel", [
    metric("Market Type", context.market_type || "-"),
    metric("Playbook", context.playbook || "-"),
    metric("Recommended Side", context.recommended_side || "WAIT"),
    metric("Confidence", context.confidence !== undefined ? score(context.confidence) : "-"),
    metric("Enforcement", context.enforcement || "-"),
    metric("Would Block", context.would_block === undefined ? "-" : context.would_block ? "YES" : "NO"),
  ].join(""));
  setText("#oa-market-context-reason", context.reason || "No market context evaluated yet.");
}

function renderTradeCandidateValidation(validation = {}) {
  const stage = validation.stage || "WAITING";
  const allowed = Boolean(validation.allowed);
  const selected = validation.selected_contract || {};
  const evidence = validation.evidence || {};
  setBadge("#oa-trade-candidate-badge", stage, allowed ? "green" : stage === "WAITING" ? "grey" : "yellow");
  setHtml("#oa-trade-candidate-panel", [
    metric("Contract", selected.tradingsymbol || evidence.tradingsymbol || "-"),
    metric("Side", selected.option_type || selected.instrument_type || evidence.side || "-"),
    metric("Score", evidence.score !== undefined ? score(evidence.score) : "-"),
    metric("LTP", selected.ltp || evidence.ltp || "-"),
    metric("Bid / Ask", selected.bid || selected.ask ? `${text(selected.bid)} / ${text(selected.ask)}` : "-"),
    metric("Spread", selected.spread_pct !== undefined ? percent(selected.spread_pct) : "-"),
    metric("Depth", selected.total_depth || evidence.total_depth || "-"),
    metric("Blockers", (validation.blockers || []).slice(0, 2).join("; ") || "-"),
  ].join(""));
}

function renderDashboardIdle(session, result = {}) {
  const message = session.message || "Start the matching engine to begin live scanning.";
  setBadge("#oa-dashboard-cue-badge", session.label, session.connected ? "yellow" : "red");
  setText("#oa-cue", "-");
  setText("#oa-cue-score", "-");
  setText("#oa-cue-confidence", "-");
  setText("#oa-cue-updated", "-");
  setText("#oa-cue-reason", message);
  renderFiiDiiStatus(state.fiiDiiStatus || state.status.fii_dii || {});
  renderNewsEventSignal(newsEventSignalFrom(result));

  setBadge("#oa-regime-side", "WAIT", "grey");
  setText("#oa-regime", "-");
  setText("#oa-regime-confidence", "-");
  setText("#oa-regime-aggression", "-");
  setText("#oa-regime-block", message);
  renderMarketContext({ permission: "WAIT", reason: message });
  renderTradeCandidateValidation({ stage: "WAITING", blockers: [message] });

  setBadge("#oa-health-badge", session.label, session.connected ? "yellow" : "red");
  setText("#oa-session-health", "-");
  setText("#oa-bot-health", "-");
  setText("#oa-readiness-score", "-");
  setBadge("#oa-new-entries", "NO", session.connected ? "yellow" : "red");
  setText("#oa-cooldown", "Cooldown: 0 sec");

  setBadge("#oa-decision-badge", session.label, session.connected ? "yellow" : "red");
  setHtml("#oa-plan-body", [
    row("Session", session.label),
    row("Mode", session.modeLabel),
    row("Decision", "WAIT"),
    row("Next Step", message),
  ].join(""));
  renderList("#oa-blockers-list", [message], "No current live session.");
  renderActiveTradeCard([]);
  renderDataSourcePanel({
    data_source: state.status.data_source || result.data_source || "UNKNOWN",
    live_scan: state.status.live_scan || {},
    options_data_health: state.status.options_live_feed?.health || {},
    allowed: false,
    next_action: message,
  });
  renderList("#oa-events", [], "No current live session events.");
  setHtml("#oa-dashboard-alerts", alertHtml(message, session.connected ? "warning" : "danger"));
}

function renderDashboardWaiting(session) {
  const message = `${session.modeLabel} engine is running. Waiting for the next current live scan decision.`;
  setBadge("#oa-dashboard-cue-badge", "SCANNING", "yellow");
  setText("#oa-cue", "-");
  setText("#oa-cue-score", "-");
  setText("#oa-cue-confidence", "-");
  setText("#oa-cue-updated", "-");
  setText("#oa-cue-reason", message);
  renderFiiDiiStatus(state.fiiDiiStatus || state.status.fii_dii || {});
  renderNewsEventSignal(newsEventSignalFrom({}));

  setBadge("#oa-regime-side", "WAIT", "grey");
  setText("#oa-regime", "-");
  setText("#oa-regime-confidence", "-");
  setText("#oa-regime-aggression", "-");
  setText("#oa-regime-block", message);
  renderMarketContext({ permission: "WAIT", reason: message });
  renderTradeCandidateValidation({ stage: "WAITING", blockers: [message] });

  setBadge("#oa-health-badge", "SCANNING", "yellow");
  setText("#oa-session-health", "-");
  setText("#oa-bot-health", "-");
  setText("#oa-readiness-score", "-");
  setBadge("#oa-new-entries", "NO", "yellow");
  setText("#oa-cooldown", "Cooldown: 0 sec");

  setBadge("#oa-decision-badge", "WAIT", "grey");
  setHtml("#oa-plan-body", [
    row("Session", "RUNNING"),
    row("Mode", session.modeLabel),
    row("Decision", "WAIT"),
    row("Next Step", message),
  ].join(""));
  renderList("#oa-blockers-list", [message], "Waiting for current scan decision.");
  renderActiveTradeCard([]);
  renderDataSourcePanel({
    data_source: state.status.data_source || "UNKNOWN",
    live_scan: state.status.live_scan || {},
    options_data_health: state.status.options_live_feed?.health || {},
    allowed: false,
    next_action: message,
  });
  renderList("#oa-events", [], "No current live session events.");
  setHtml("#oa-dashboard-alerts", alertHtml(message, "warning"));
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

function newsEventSignalFrom(result = {}) {
  return firstNonEmptyObject(
    result.news_event_signal,
    result.market_context?.news_event_signal,
    result.market_playbook?.news_event_signal,
    state.status.news_event_signal,
    state.lastResult.news_event_signal,
    state.lastResult.market_context?.news_event_signal
  );
}

function renderNewsEventSignal(signal = {}) {
  const settings = state.status.settings || state.defaults.settings || {};
  const card = $(".oa-news-event-card");
  if (card) card.hidden = settings.news_event_show_in_ui === false;
  if (settings.news_event_show_in_ui === false) return;
  const status = String(signal.status || "REFRESH_PENDING").toUpperCase();
  const severity = String(signal.severity || "NONE").toUpperCase();
  const isShock = status === "NEWS_EVENT_SHOCK" || severity === "SHOCK";
  const isWarning = status === "NEWS_WARNING" || severity === "WARNING";
  const isOk = ["NO_RELEVANT_NEWS", "OK"].includes(status);
  const isDisabled = status === "DISABLED";
  const kind = isShock ? "red" : isWarning ? "yellow" : isOk ? "green" : isDisabled ? "grey" : "yellow";
  const label = isShock ? "SHOCK" : isWarning ? "WARNING" : isDisabled ? "DISABLED" : status.replaceAll("_", " ");
  if (card) {
    card.classList.toggle("is-shock", isShock);
    card.classList.toggle("is-warning", isWarning && !isShock);
  }
  setBadge("#oa-news-event-badge", label || "Waiting", kind);
  const headlines = (signal.matched_headlines || []).slice(0, 3);
  setHtml("#oa-news-event-panel", [
    metric("Provider", signal.provider || "ZERODHA_PULSE"),
    metric("Score", signal.score !== undefined ? score(signal.score) : "-"),
    metric("Severity", signal.severity || "-"),
    metric("Event Type", signal.event_type || "-"),
    metric("Would Block", signal.would_block ? "YES" : "NO"),
    metric("Market Confirmed", signal.market_confirmation || signal.market_confirmed ? "YES" : "NO"),
    metric("Newest Age", signal.newest_item_age_minutes !== null && signal.newest_item_age_minutes !== undefined ? `${signal.newest_item_age_minutes} min` : "-"),
    metric("Fetched", signal.fetched_at || "-"),
    metric("Cache", signal.cache_status || (signal.stale ? "STALE" : "-")),
    headlines.length ? `<div class="oa-news-headlines">${headlines.map(item => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : "",
  ].join(""));
  setText("#oa-news-event-reason", signal.reason || signal.error || "No Zerodha Pulse news signal yet.");
}

function renderDataSourcePanel(result = {}) {
  const source = result.data_source || state.dataSource || "UNKNOWN";
  const demo = source === "DEBUG" || source === "DEMO" || Boolean(result.demo_data);
  const health = result.options_data_health || {};
  const freshness = result.freshness || result.decision_snapshot?.freshness || {};
  const scan = result.live_scan || state.status.live_scan || {};
  const cache = result.instrument_cache || state.status.instrument_cache || {};
  const exchanges = cache.exchanges || {};
  const cacheRows = Object.values(exchanges);
  const firstCache = cacheRows[0] || {};
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
    metric("Quote Source", result.quote_source || health.quote_source || "-"),
    metric("Data Mode", result.data_mode || health.data_mode || "-"),
    metric("Quote Age", `${text(result.quote_age_seconds ?? $("#oa-quote-age")?.value, "-")} sec`),
    metric("Stale Threshold", `${text((result.settings || state.status.settings || state.defaults.settings || {}).quote_stale_seconds, 3)} sec`),
    metric("Freshness", freshnessStatusText(freshness)),
    metric("Fresh Tags", freshnessTagText(freshness)),
    metric("Instrument Cache", firstCache.source || "-"),
    metric("Cache File", firstCache.path || "-"),
    metric("FII/DII", (state.fiiDiiStatus.status || result.market_cue?.fii_dii_status?.status || "Not uploaded")),
    metric("News", newsEventSignalFrom(result).status || (result.market_cue?.components?.news !== undefined ? score(result.market_cue.components.news) : "No news summary")),
    metric("Trading Allowed", result.allowed ? "YES" : "NO"),
    metric("Governor", result.governor?.state || "-"),
    metric("Live Scanner", scan.running ? "RUNNING" : "STOPPED"),
    metric("Last Scan", scan.last_cycle || "-"),
    metric("Scan Count", scan.cycle_count ?? "-"),
    metric("Next Action", result.next_action || "-"),
  ].join(""));
}

function freshnessStatusText(freshness = {}) {
  const summary = freshness.summary || {};
  if (!summary.status) return "-";
  return `${summary.status} (${summary.fresh_count || 0} fresh, ${summary.stale_count || 0} stale, ${summary.unknown_count || 0} unknown)`;
}

function freshnessTagText(freshness = {}) {
  const summary = freshness.summary || {};
  const stale = summary.stale_tags || [];
  const unknown = summary.unknown_tags || [];
  if (stale.length) return `Stale: ${stale.slice(0, 3).join(", ")}`;
  if (unknown.length) return `Unknown: ${unknown.slice(0, 3).join(", ")}`;
  return summary.status ? "All known tags fresh" : "-";
}

function renderIndustryDiagnostics() {
  const result = state.lastResult || {};
  const feed = result.options_live_feed || state.status.options_live_feed || {};
  const feedHealth = feed.health || {};
  const feedMode = feedHealth.data_mode || feed.data_mode || "UNKNOWN";
  const feedStale = Boolean(feedHealth.feed_stale);
  const roleStatuses = feedHealth.role_statuses || {};
  const freshRoles = Object.entries(roleStatuses).filter(([, item]) => item?.fresh).map(([role]) => role);
  const staleRoles = Object.entries(roleStatuses).filter(([, item]) => item?.stale).map(([role]) => role);
  const runtime = result.runtime_persistence || state.status.runtime_persistence || {};
  const referenceCache = result.reference_cache || state.status.reference_cache || {};
  const featureCache = result.feature_cache || state.status.feature_cache || {};
  const apiBudget = result.api_budget || state.status.api_budget || {};
  const recentApiCalls = apiBudget.real_api_calls_recent || {};
  setBadge("#oa-live-feed-badge", feedMode, feedStale ? "red" : feedMode === "WEBSOCKET_TICKS" ? "green" : feedMode === "QUOTE_SNAPSHOT_POLLING" ? "yellow" : "grey");
  setHtml("#oa-live-feed-panel", [
    metric("Data Mode", feedMode),
    metric("Broker Updates", "Polling + reconciliation"),
    metric("Postback Required", "NO"),
    metric("Postback", "DISABLED"),
    metric("Websocket", feed.websocket_connected ? "CONNECTED" : "DISCONNECTED"),
    metric("Quote Fallback", feed.quote_polling_fallback ? "ENABLED" : "OFF"),
    metric("Index Tick", feedHealth.last_index_tick || "-"),
    metric("CE Tick", feedHealth.last_ce_tick || "-"),
    metric("PE Tick", feedHealth.last_pe_tick || "-"),
    metric("Fresh Roles", freshRoles.join(", ") || "-"),
    metric("Stale Roles", staleRoles.join(", ") || "-"),
    metric("Feed Stale", feedStale ? "YES" : "NO"),
    metric("Stale Labels", (feedHealth.stale_labels || []).join(", ") || "-"),
    metric("Subscribed Tokens", (feed.subscribed_tokens || []).join(", ") || "-"),
    metric("Option Streams", (feed.option_candles?.streams || []).length),
    metric("Runtime Saved", runtime.last_saved_at || "-"),
    metric("Reference Warm", referenceCache.warmed ? "YES" : "NO"),
    metric("Feature Cache", `${featureCache.hits || 0}/${featureCache.misses || 0}`),
    metric("Recent API Calls", Object.values(recentApiCalls).reduce((sum, value) => sum + Number(value || 0), 0)),
    metric("Reconcile Poll", apiBudget.real_broker_reconcile_poll_seconds ? `${apiBudget.real_broker_reconcile_poll_seconds}s` : "-"),
  ].join(""));

  const currentReal = isCurrentRealProcessActive(result);
  const realSession = sessionDisplayState("REAL", result);
  if (!currentReal) {
    setBadge("#oa-real-lifecycle-badge", realSession.label, realSession.connected ? "yellow" : "red");
    setHtml("#oa-real-lifecycle-panel", [
      metric("State", realSession.label),
      metric("Protected State", "-"),
      metric("Safe Mode", "-"),
      metric("Emergency Flatten", "-"),
      metric("Entry Order", "-"),
      metric("Entry Status", "-"),
      metric("Entry Qty", "-"),
      metric("Entry Price", "-"),
      metric("Fill Qty", "-"),
      metric("Avg Fill", "-"),
      metric("Target Order", "-"),
      metric("Target Status", "-"),
      metric("Target Price", "-"),
      metric("Stoploss Order", "-"),
      metric("Stoploss Status", "-"),
      metric("Stoploss Price", "-"),
      metric("Last Event", "No current real scanner session"),
      metric("Blocker", realSession.message),
    ].join(""));
  } else {
    const lifecycle = result.real_order_lifecycle || state.status.real_order_lifecycle || {};
    const lifecycleState = lifecycle.state || "IDLE";
    const protectedState = lifecycle.protected_state || "-";
    const entryOrder = lifecycle.entry_order || {};
    const targetOrder = lifecycle.target_order || {};
    const stoplossOrder = lifecycle.stoploss_order || {};
    const fill = lifecycle.fill || {};
    const lastEvent = latestLifecycleEvent(lifecycle);
    const lifecycleBad = /UNPROTECTED|SAFE|MANUAL|REJECTED|CANCELLED|TIMEOUT/.test(String(lifecycleState)) || /FAILED|RECONCILIATION_REQUIRED/.test(String(protectedState));
    const lifecycleGood = /OCO_ACTIVE|TARGET_FILLED|SL_FILLED|EXIT_RECONCILED/.test(String(lifecycleState)) && !lifecycleBad;
    setBadge("#oa-real-lifecycle-badge", lifecycleState, lifecycleBad ? "red" : lifecycleGood ? "green" : lifecycleState === "IDLE" ? "grey" : "yellow");
    setHtml("#oa-real-lifecycle-panel", [
      metric("State", lifecycleState),
      metric("Protected State", protectedState),
      metric("Safe Mode", lifecycle.safe_mode ? "YES" : "NO"),
      metric("Emergency Flatten", lifecycle.emergency_flatten_required ? "YES" : "NO"),
      metric("Entry Order", entryOrder.order_id || entryOrder.id || "-"),
      metric("Entry Status", entryOrder.status || "-"),
      metric("Entry Qty", entryOrder.quantity || fill.filled_quantity || "-"),
      metric("Entry Price", entryOrder.price || entryOrder.average_price || "-"),
      metric("Fill Qty", fill.filled_quantity || "-"),
      metric("Avg Fill", fill.average_price || "-"),
      metric("Target Order", targetOrder.order_id || targetOrder.id || "-"),
      metric("Target Status", targetOrder.status || "-"),
      metric("Target Price", targetOrder.price || targetOrder.trigger_price || "-"),
      metric("Stoploss Order", stoplossOrder.order_id || stoplossOrder.id || "-"),
      metric("Stoploss Status", stoplossOrder.status || "-"),
      metric("Stoploss Price", stoplossOrder.trigger_price || stoplossOrder.price || "-"),
      metric("Last Event", lastEvent ? eventText("Real", lastEvent).replace(/^Real - /, "") : "-"),
      metric("Blocker", (lifecycle.blockers || [])[0] || "-"),
    ].join(""));
  }

  const blackbox = result.blackbox || state.status.blackbox || {};
  const report = blackbox.latency_report || {};
  const performance = result.performance || state.status.performance || {};
  const perfSummary = performance.summary || {};
  const latencySummary = result.latency || state.status.latency || {};
  const count = report.count || (blackbox.events || []).length || 0;
  setBadge("#oa-blackbox-badge", count ? `${count} Events` : "No Events", count ? "blue" : "grey");
  setHtml("#oa-blackbox-panel", [
    metric("Events", count),
    metric("UI Summary p95", latency(latencySummary["options_auto.ui_summary"])),
    metric("Full Status p95", latency(latencySummary["options_auto.status_full"])),
    metric("Evaluate p95", latency(latencySummary["options_auto.evaluate_total"])),
    metric("Persist p95", latency(latencySummary["options_auto.runtime_persist"])),
    metric("Decision p95", latency(report.decision_latency_ms)),
    metric("Validation p95", latency(report.validation_latency_ms)),
    metric("Submit Ack p95", latency(report.submit_to_ack_ms)),
    metric("Ack Fill p95", latency(report.ack_to_fill_ms)),
    metric("Protection p95", latency(report.protection_delay_ms)),
    metric("Data Age p95", latency(report.data_age_ms)),
    metric("Scan Latest", latency({ p95: perfSummary.live_scan_cycle?.latest_ms })),
    metric("Feature Latest", latency({ p95: perfSummary.index_feature_build?.latest_ms })),
    metric("Wake Events", perfSummary.event_driven_scan_wake?.count || 0),
  ].join(""));
}

function renderIndexTickStreams() {
  const feed = state.status.options_live_feed || state.lastResult.options_live_feed || {};
  const health = feed.health || {};
  const streams = feed.tick_streams || {};
  const fallbackIndexTicks = (state.status.index_ticks || state.lastResult.index_ticks || []).map(tick => ({
    role: "INDEX",
    observed_at: tick.observed_at,
    exchange_timestamp: tick.exchange_timestamp,
    ltp: tick.spot,
    source: tick.spot_source,
    age_seconds: tick.age_seconds,
    tradingsymbol: tick.underlying,
    quote_key: tick.quote_key,
    scan: tick.live_scan_cycle,
  }));
  const roles = [
    ["INDEX", "NIFTY"],
    ["CE", "Locked CE"],
    ["PE", "Locked PE"],
  ];
  const activeRole = roles.some(([role]) => role === state.activeTickRole) ? state.activeTickRole : "INDEX";
  const ticksByRole = {
    INDEX: (streams.INDEX || fallbackIndexTicks || []).slice(-80),
    CE: (streams.CE || []).slice(-80),
    PE: (streams.PE || []).slice(-80),
  };
  const ticks = ticksByRole[activeRole] || [];
  const latest = ticks[ticks.length - 1] || {};
  const scan = state.status.live_scan || state.lastResult.live_scan || {};
  const running = Boolean(scan.running);
  const roleStatus = (health.role_statuses || {})[activeRole] || {};
  const isFresh = Boolean(roleStatus.fresh);
  const isStale = Boolean(roleStatus.stale);
  const tabButtons = `<div class="oa-tick-tabs">${roles.map(([role, label]) => {
    const rows = ticksByRole[role] || [];
    const status = (health.role_statuses || {})[role] || {};
    const className = ["oa-tick-tab", activeRole === role ? "oa-tick-tab-active" : "", status.stale ? "oa-tick-tab-stale" : status.fresh ? "oa-tick-tab-live" : ""].filter(Boolean).join(" ");
    return `<button type="button" class="${className}" data-oa-tick-role="${role}">${label}<span>${rows.length}</span></button>`;
  }).join("")}</div>`;
  $$("[data-index-tick-badge]").forEach(node => {
    node.className = `oa-status-badge ${badgeClass(isFresh ? "green" : isStale ? "red" : ticks.length ? "yellow" : "grey")}`;
    node.textContent = isFresh ? `${activeRole} Live` : isStale ? `${activeRole} Stale` : ticks.length ? `${activeRole} Last Tick` : "Waiting";
  });
  const latestHtml = ticks.length
    ? `<div class="oa-index-tick-latest">
        ${metric("Role", activeRole)}
        ${metric("Symbol", latest.tradingsymbol || latest.underlying || "-")}
        ${metric(activeRole === "INDEX" ? "Spot" : "LTP", latest.ltp ?? latest.spot ?? "-")}
        ${metric("Bid", activeRole === "INDEX" ? "-" : latest.bid ?? "-")}
        ${metric("Ask", activeRole === "INDEX" ? "-" : latest.ask ?? "-")}
        ${metric("Depth", activeRole === "INDEX" ? "Index packet" : latest.depth_present ? "FULL" : "MISSING")}
        ${metric("Bid Qty", activeRole === "INDEX" ? "-" : latest.bid_qty ?? "-")}
        ${metric("Ask Qty", activeRole === "INDEX" ? "-" : latest.ask_qty ?? "-")}
        ${metric("Source", latest.source || latest.spot_source || "-")}
        ${metric("Observed", latest.observed_at || "-")}
        ${metric("Exchange Time", latest.exchange_timestamp || "-")}
        ${metric("Scan", scan.running ? `Running #${scan.cycle_count ?? 0}` : "Stopped")}
      </div>`
    : `<p class="oa-empty-state">No ${activeRole === "INDEX" ? "NIFTY" : activeRole} ticks yet.</p>`;
  const rows = ticks.slice().reverse().map(tick => `<div class="oa-index-tick-row">
      <div><span>Observed</span><strong>${escapeHtml(shortTime(tick.observed_at) || "-")}</strong></div>
      <div><span>LTP</span><strong>${escapeHtml(tick.ltp ?? tick.spot ?? "-")}</strong></div>
      <div><span>Bid</span><strong>${escapeHtml(activeRole === "INDEX" ? "-" : tick.bid ?? "-")}</strong></div>
      <div><span>Ask</span><strong>${escapeHtml(activeRole === "INDEX" ? "-" : tick.ask ?? "-")}</strong></div>
      <div><span>Depth</span><strong>${escapeHtml(activeRole === "INDEX" ? "Index" : tick.depth_present ? "Full" : "Missing")}</strong></div>
      <div><span>Source</span><strong>${escapeHtml(tick.source || tick.spot_source || "-")}</strong></div>
      <div><span>Age</span><strong>${escapeHtml(tick.age_seconds ?? "-")}s</strong></div>
    </div>`).join("");
  const body = `${tabButtons}${latestHtml}${rows ? `<div class="oa-index-tick-list">${rows}</div>` : ""}`;
  $$("[data-index-tick-panel]").forEach(node => {
    node.innerHTML = body;
  });
}

function renderContractLockCards() {
  const lock = contractLockFromState({ liveOnly: true });
  const hasLock = Boolean(lock && (lock.ce?.tradingsymbol || lock.pe?.tradingsymbol));
  $$("[data-contract-lock-badge]").forEach(node => {
    node.className = `oa-status-badge ${badgeClass(hasLock ? "green" : "grey")}`;
    node.textContent = hasLock ? (lock.status || "CONTRACTS_LOCKED") : "No Lock";
  });
  const body = hasLock ? [
    row("Lock Status", lock.status || "CONTRACTS_LOCKED"),
    row("Underlying", lock.underlying || "-"),
    row("Spot at Lock", lock.spot_at_lock ?? "-"),
    row("Major Step", lock.major_strike_step || lock.major_step || "-"),
    row("Expiry", lock.expiry || "-"),
    row("Lots", lock.lots || lock.ce?.lots || lock.pe?.lots || "-"),
    row("Fetched Lot Size", lock.ce?.lot_size || lock.pe?.lot_size || "-"),
    row("Final Quantity", lock.ce?.quantity || lock.pe?.quantity || "-"),
    row("CE Selected", lock.ce?.tradingsymbol || lock.ce?.strike || "-"),
    row("CE Premium", lock.ce?.premium ?? "-"),
    row("CE Margin", lock.ce?.margin_required_estimate !== undefined ? money(lock.ce.margin_required_estimate) : "-"),
    row("CE Reason", lock.ce?.hop_reason || "-"),
    row("PE Selected", lock.pe?.tradingsymbol || lock.pe?.strike || "-"),
    row("PE Premium", lock.pe?.premium ?? "-"),
    row("PE Margin", lock.pe?.margin_required_estimate !== undefined ? money(lock.pe.margin_required_estimate) : "-"),
    row("PE Reason", lock.pe?.hop_reason || "-"),
    row("Reselect In", lock.valid_until ? timeLeftText(lock.valid_until) : "-"),
  ].join("") : `<p class="oa-empty-state">No live contract lock. Start Paper or Real scanner to lock fresh CE/PE contracts.</p>`;
  $$("[data-contract-lock-card]").forEach(node => {
    node.innerHTML = body;
  });
}

function contractLockFromState(options = {}) {
  const liveOnly = Boolean(options.liveOnly);
  if (liveOnly && !isAnyCurrentLiveProcessActive()) return {};
  const resultLock = state.lastResult.contract_lock || {};
  if (resultLock.ce || resultLock.pe) return resultLock;
  const statusLock = state.status.contract_lock?.lock || {};
  if (statusLock.ce || statusLock.pe) return statusLock;
  if (liveOnly) return {};
  const metaLock = state.lastBacktest?.contract_lock || state.lastBacktest?.source_metadata?.contract_lock || {};
  if (metaLock.ce || metaLock.pe) return metaLock;
  return {};
}

function timeLeftText(value) {
  const deadline = new Date(value).getTime();
  if (!Number.isFinite(deadline)) return "-";
  const remaining = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

function noTradeReason(result = {}) {
  if (result.allowed) return "Trade selected.";
  const blockers = result.blockers || result.governor?.blockers || result.selection?.blockers || [];
  if (blockers.length) return blockers[0];
  if (result.regime?.recommended_side === "WAIT") return result.regime.no_trade_reason || "Regime says WAIT.";
  if (!result.selection?.selected) return "No selected trade candidate.";
  return result.explanation || "Waiting for valid setup.";
}

function shortTime(value) {
  if (!value) return "";
  const textValue = String(value);
  const match = textValue.match(/T?(\d{2}:\d{2}:\d{2})/);
  return match ? match[1] : textValue;
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
  setHtml("#oa-active-trade-body", renderTradeDetails(trade));
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
  const paperLifecycle = paperLifecycleFromState(result);
  const realLifecycle = isCurrentRealProcessActive(result) ? (result.real_order_lifecycle || state.status.real_order_lifecycle || {}) : {};
  const currentPaper = isCurrentPaperProcessActive(result);
  const events = [];
  if (result.selection?.selected?.tradingsymbol) {
    events.push(`Candidate found: ${result.selection.selected.tradingsymbol}, score ${text(result.selection.score, "-")}.`);
  }
  (result.blockers || []).slice(0, 4).forEach(item => events.push(`Trade blocked: ${item}`));
  (session.safety_events || []).slice(-6).forEach(item => events.push(`${item.reason || "Safety event"}.`));
  (session.rejected_log || []).slice(-4).forEach(item => events.push(`Rejected setup: ${item.reason || "-"}`));
  if (currentPaper) (paperLifecycle.events || []).slice(-6).forEach(item => events.push(eventText("Paper", item)));
  (realLifecycle.history || realLifecycle.events || []).slice(-6).forEach(item => events.push(eventText("Real", item)));
  renderList("#oa-events", events.slice(-10).reverse(), "No recent events yet.");
}

function eventText(prefix, item = {}) {
  if (typeof item === "string") return `${prefix}: ${item}`;
  const label = item.event || item.event_type || item.state || item.status || item.reason || "Event";
  const symbol = item.tradingsymbol || item.contract || item.trade_plan?.tradingsymbol || "";
  const orderId = item.order_id || item.entry_order_id || item.target_order_id || item.stoploss_order_id || "";
  const stamp = shortTime(item.timestamp || item.created_at || item.updated_at || item.at || "");
  return [prefix, label, symbol, orderId, stamp].filter(Boolean).join(" - ");
}

function renderDashboardAlerts(result) {
  const alerts = [];
  const mode = tradingModeFromState(result);
  const trades = activeTradesFrom(result, { currentOnly: true });
  const connected = modeConnected(mode, result);
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
  on("#oa-backtest-export", "click", showBacktestReportPath);
  on("#oa-backtest-folder", "click", showBacktestFolderPath);
}

async function runBacktest() {
  try {
    setTabAlert("backtest", "Running backtest...", "info");
    const result = await api("/api/options-auto/backtest/run", backtestPayload());
    state.lastBacktest = result;
    await refreshUiSummaryAfterMutation(result, { clearLastResult: true, preserveBacktest: true, timeoutMs: 5000 });
    renderBacktestResults(result);
    renderTopStatus();
    setTabAlert("backtest", "Backtest complete.", "success");
  } catch (error) {
    setTabAlert("backtest", error.message, "danger");
  }
}

function renderBacktestResults(result = state.lastBacktest) {
  result = Object.keys(result || {}).length ? result : backtestSummaryFromStatus(state.status) || {};
  const metrics = result.metrics || result.summary || {};
  const trades = normalizeBacktestTrades(result);
  const meta = result.source_metadata || {};
  const marketContextScenarios = result.market_context_scenarios || {};
  const historicalAssumptions = result.historical_data_assumptions || meta.historical_data_assumptions || {};
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
  const scenarioCompare = formatBacktestScenarioCompare(marketContextScenarios);
  const syntheticFields = (historicalAssumptions.synthetic_fields || []).join(", ") || "-";
  const unavailableFields = (historicalAssumptions.unavailable_fields || []).join(", ") || "-";
  setHtml("#oa-backtest-summary", [
    metric("Data Source", result.data_source_label || result.data_source || "-"),
    metric("Rows", result.rows || 0),
    metric("Option Series", result.option_frames || 0),
    metric("Spot Source", meta.spot_source || "-"),
    metric("Backtest Spot", meta.spot || "-"),
    metric("Major Strike Step", meta.major_strike_step || result.major_strike_step || "-"),
    metric("Entry Mode", result.settings?.entry_dependency_mode || "-"),
    metric("CE Locked", meta.selected_ce?.tradingsymbol || result.contract_lock?.ce?.tradingsymbol || "-"),
    metric("PE Locked", meta.selected_pe?.tradingsymbol || result.contract_lock?.pe?.tradingsymbol || "-"),
    metric("Lots", meta.lots || result.contract_lock?.lots || "-"),
    metric("Fetched Lot Size", meta.fetched_lot_size?.CE || result.contract_lock?.ce?.lot_size || "-"),
    metric("Final Quantity", meta.final_quantity?.CE || result.contract_lock?.ce?.quantity || "-"),
    metric("ATM Strike", meta.atm_strike || "-"),
    metric("Contracts Requested", meta.contracts_requested || 0),
    metric("Contracts Found", meta.contracts_found || 0),
    metric("Proxy Quote", meta.historical_proxy_quote_warning ? "YES" : "NO"),
    metric("Scenario Compare", scenarioCompare || "-"),
    metric("Synthetic Fields", syntheticFields),
    metric("Missing Historical Data", unavailableFields),
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
    ? trades.map(trade => `<tr><td>${escapeHtml(trade.time || trade.datetime || "-")}</td><td>${escapeHtml(backtestTradeSide(trade))}</td><td>${escapeHtml(trade.tradingsymbol || "-")}</td><td>${escapeHtml(trade.entry || trade.entry_price || "-")}</td><td>${escapeHtml(trade.exit || trade.exit_price || "-")}</td><td>${escapeHtml(trade.quantity || "-")}</td><td>${escapeHtml(trade.exit_reason || trade.reason || "-")}</td><td>${escapeHtml(money(trade.net_pnl || 0))}</td><td>${escapeHtml(trade.score || "-")}</td></tr>`).join("")
    : `<tr><td colspan="9">No trades generated by this backtest result.</td></tr>`);
  updateBacktestReportButtons(result, trades);
}

function formatBacktestScenarioCompare(scenarios = {}) {
  return ["BASELINE", "REPORT_ONLY", "ENFORCED"]
    .filter(name => scenarios[name])
    .map(name => {
      const scenario = scenarios[name] || {};
      const metrics = scenario.metrics || {};
      return `${name}: ${scenario.orders_placed || metrics.total_trades || 0} trades, ${money(metrics.net_pnl ?? metrics.total_pnl ?? 0)}`;
    })
    .join(" | ");
}

function backtestSummaryFromStatus(status = {}) {
  const summary = status.session?.last_decision?.summary;
  if (!summary || String(summary.mode || "").toUpperCase() !== "BACKTEST") return null;
  return {
    ...summary,
    session: status.session || summary.session || {},
    account_status: status.account_status || summary.account_status || {},
  };
}

function normalizeBacktestTrades(result = {}) {
  const decisions = result.decisions || [];
  if (Array.isArray(result.trades) && result.trades.length) {
    return result.trades.map(trade => {
      const entryDecision = findBacktestDecision(decisions, trade.tradingsymbol, trade.entry_index, "ENTRY");
      const exitDecision = findBacktestDecision(decisions, trade.tradingsymbol, trade.exit_index, "EXIT")
        || findBacktestDecision(decisions, trade.tradingsymbol, trade.exit_index, "END_OF_DAY_EXIT");
      return {
        ...trade,
        time: trade.time || trade.datetime || trade.entry_time || trade.opened_at || entryDecision?.datetime || indexLabel("Entry", trade.entry_index),
        exit_time: trade.exit_time || exitDecision?.datetime || indexLabel("Exit", trade.exit_index),
        exit_reason: trade.exit_reason || trade.reason || trade.close_reason || exitDecision?.reason,
        side: backtestTradeSide(trade),
        score: trade.score || entryDecision?.score || entryDecision?.decision_snapshot?.trade_score_breakdown?.score || entryDecision?.decision_snapshot?.selected_contract?.score,
      };
    });
  }
  return deriveBacktestTradesFromDecisions(decisions);
}

function findBacktestDecision(decisions = [], symbol = "", rowIndex, decisionName = "") {
  return (decisions || []).find(row => (
    String(row.decision || "").toUpperCase() === decisionName
    && String(row.tradingsymbol || "") === String(symbol || "")
    && (rowIndex === undefined || rowIndex === null || String(row.row) === String(rowIndex))
  ));
}

function deriveBacktestTradesFromDecisions(decisions = []) {
  const openBySymbol = {};
  const rows = [];
  (decisions || []).forEach(row => {
    const decision = String(row.decision || "").toUpperCase();
    const symbol = row.tradingsymbol || row.contract || "";
    if (!symbol) return;
    if (decision === "ENTRY") {
      openBySymbol[symbol] = {
        tradingsymbol: symbol,
        side: backtestTradeSide(row),
        time: row.datetime || indexLabel("Entry", row.row),
        entry_price: row.entry_price || row.entry,
        quantity: row.quantity,
        score: row.score || row.decision_snapshot?.trade_score_breakdown?.score || row.decision_snapshot?.selected_contract?.score,
      };
      return;
    }
    if (decision === "EXIT" || decision === "END_OF_DAY_EXIT") {
      const entry = openBySymbol[symbol] || { tradingsymbol: symbol, side: backtestTradeSide(row) };
      rows.push({
        ...entry,
        datetime: entry.time || row.datetime || indexLabel("Exit", row.row),
        exit_price: row.exit_price || row.exit,
        quantity: entry.quantity || row.quantity,
        exit_reason: row.exit_reason || row.reason || decision,
        gross_pnl: row.gross_pnl,
        charges: row.charges,
        net_pnl: row.net_pnl,
      });
      delete openBySymbol[symbol];
    }
  });
  return rows;
}

function backtestTradeSide(trade = {}) {
  const explicit = String(trade.side || trade.option_type || trade.instrument_type || "").toUpperCase();
  if (explicit === "CE" || explicit === "PE") return explicit;
  const symbol = String(trade.tradingsymbol || trade.contract || "").toUpperCase();
  if (symbol.endsWith("CE") || symbol.includes("CE ")) return "CE";
  if (symbol.endsWith("PE") || symbol.includes("PE ")) return "PE";
  return "-";
}

function indexLabel(label, index) {
  return index === undefined || index === null || index === "" ? "" : `${label} #${index}`;
}

function updateBacktestReportButtons(result = {}, trades = []) {
  const report = result.report || {};
  const exportButton = $("#oa-backtest-export");
  const folderButton = $("#oa-backtest-folder");
  if (exportButton) {
    exportButton.disabled = !report.audit_json;
    exportButton.dataset.reportPath = report.audit_json || "";
    exportButton.textContent = report.audit_json ? "Audit JSON Ready" : "Export Excel";
  }
  if (folderButton) {
    folderButton.disabled = !report.folder;
    folderButton.dataset.reportFolder = report.folder || "";
    folderButton.textContent = report.folder ? "Result Folder Ready" : "Open Result Folder";
  }
  if (trades.length && !report.audit_json) {
    setTabAlert("backtest", `Backtest complete with ${trades.length} trade(s). Report path was not returned.`, "warning");
  }
}

function showBacktestReportPath() {
  const path = $("#oa-backtest-export")?.dataset.reportPath || state.lastBacktest?.report?.audit_json || "";
  setTabAlert("backtest", path ? `Backtest audit report: ${path}` : "No backtest audit report path is available yet.", path ? "info" : "warning");
}

function showBacktestFolderPath() {
  const path = $("#oa-backtest-folder")?.dataset.reportFolder || state.lastBacktest?.report?.folder || "";
  setTabAlert("backtest", path ? `Backtest result folder: ${path}` : "No backtest result folder is available yet.", path ? "info" : "warning");
}

// shadow rendering/actions
function initShadowTab() {
  on("#oa-shadow-start", "click", runShadowStart);
  on("#oa-shadow-stop", "click", runShadowStop);
  on("#oa-shadow-report-btn", "click", runShadowReport);
}

async function runShadowStart() {
  try {
    const result = await api("/api/options-auto/shadow/start", evaluationPayload("SHADOW"));
    state.lastShadowResult = result;
    renderShadow(result);
    renderAll();
    setTabAlert("shadow", "Shadow mode running. No orders will be placed.", "success");
  } catch (error) {
    setTabAlert("shadow", error.message, "danger");
  }
}

async function runShadowStop() {
  try {
    const result = await api("/api/options-auto/shadow/stop", { source: "UI" });
    state.lastShadowResult = result;
    state.status = { ...state.status, ...(result || {}) };
    renderAll();
    setTabAlert("shadow", result.message || "Shadow mode stopped.", "info");
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

function renderShadow(result = state.lastShadowResult) {
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
  bindCheckboxMirror("#oa-auto", "#oa-auto-settings");
  bindCheckboxMirror("#oa-ask", "#oa-ask-settings");
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
    syncSettingsToggles("paper");
    const result = await api("/api/options-auto/paper/start", evaluationPayload("PAPER"));
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("paper", result.message || "Paper live scanner started.", "success");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function stopPaperEngine() {
  try {
    const result = await api("/api/options-auto/paper/stop", { source: "UI", mode: "PAPER" });
    await refreshUiSummaryAfterMutation(result, { clearLastResult: true });
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
    await refreshUiSummaryAfterMutation(result, { clearLastResult: true });
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
    await refreshUiSummaryAfterMutation(result, { clearLastResult: true });
    renderAll();
    setActiveAlert(`${mode} kill switch active. Engine/feed stopped and new entries are blocked.`, "danger");
  } catch (error) {
    setActiveAlert(error.message, "danger");
  }
}

async function requestPaperApproval() {
  try {
    const result = await api("/api/options-auto/paper/request-approval", evaluationPayload("PAPER"));
    if (result.approval?.approval_id) state.pendingApprovalId = result.approval.approval_id;
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("paper", result.approval ? "Approval card created." : result.message || "Approval not created.", result.approval ? "success" : "warning");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function approvePaper() {
  try {
    const result = await api("/api/options-auto/paper/approve", { approval_id: state.pendingApprovalId });
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("paper", result.status === "APPROVED" ? "Paper trade approved and protected." : result.message || result.status, result.status === "APPROVED" ? "success" : "warning");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function rejectPaper() {
  try {
    const result = await api("/api/options-auto/paper/reject", { approval_id: state.pendingApprovalId });
    state.pendingApprovalId = "";
    await refreshUiSummaryAfterMutation(result, { clearLastResult: true });
    renderAll();
    setTabAlert("paper", "Paper approval rejected.", "info");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function executePaper() {
  try {
    const result = await api("/api/options-auto/paper/execute-plan", evaluationPayload("PAPER"));
    await refreshUiSummaryAfterMutation(result);
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
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("paper", "Paper market tick processed.", "success");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

async function resetPaperBalance() {
  try {
    syncSettingsToggles("paper");
    const defaults = state.defaults.settings || {};
    const balance = numberValue($("#oa-paper-balance")?.value, defaults.paper_starting_balance || 20000);
    const result = await api("/api/options-auto/paper/reset-account", {
      ...settingsPayload(),
      mode: "PAPER",
      paper_starting_balance: balance,
    });
    await refreshUiSummaryAfterMutation(result, { clearLastResult: true, applySettings: true });
    renderAll();
    setTabAlert("paper", "Paper account balance reset. Future paper sessions will continue from this ledger.", "success");
  } catch (error) {
    setTabAlert("paper", error.message, "danger");
  }
}

function renderPaperAccount() {
  const lifecycle = paperLifecycleFromState(state.lastResult);
  const account = paperAccountFromState(state.lastResult);
  const currentPaper = isCurrentPaperProcessActive(state.lastResult);
  const paperSession = sessionDisplayState("PAPER", state.lastResult);
  const activeTrades = currentPaper ? activeTradesFrom(state.lastResult).filter(trade => trade.mode !== "REAL") : [];
  const closedTrades = currentPaper ? (lifecycle.closed_trades || []) : [];
  const orders = currentPaper ? paperOrdersFromState(state.lastResult) : [];
  const scan = liveScanFromState(state.lastResult);
  setHtml("#oa-paper-account", [
    metric("Live Session", currentPaper ? (scan.running ? "RUNNING" : "POSITION/PENDING") : paperSession.label),
    metric("Starting Balance", money(account.opening_balance || state.status.settings?.paper_starting_balance || state.defaults.settings?.paper_starting_balance || 20000)),
    metric("Available Balance", money(account.available_balance || 0)),
    metric("Realized P&L", money(account.realized_pnl || 0)),
    metric("Unrealized P&L", money(account.unrealized_pnl || 0)),
    metric("Charges Estimate", money(account.charges || 0)),
    metric("Trades Today", activeTrades.length + closedTrades.length),
    metric("Closed Trades", closedTrades.length),
    metric("Orders", orders.length),
  ].join(""));
  setHtml("#oa-paper-plan", currentPaper ? renderPlanRows(state.lastResult.trade_plan || {}) : `<p class="oa-empty-state">No current paper live trade plan. Start Paper Engine to scan fresh live data.</p>`);
  renderApprovalCard();
  renderPaperTrades();
}

function renderApprovalCard() {
  if (!isCurrentPaperProcessActive(state.lastResult)) {
    setBadge("#oa-approval-badge", "Session Not Started", "grey");
    setHtml("#oa-approval-card", `<p class="oa-empty-state">No active paper live session. Start Paper Engine before creating or approving a paper entry.</p>`);
    return;
  }
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
  const lifecycle = paperLifecycleFromState(state.lastResult);
  if (!isCurrentPaperProcessActive(state.lastResult)) {
    setHtml("#oa-paper-trades", `<p class="oa-empty-state">No active paper live session. Stored backtest or previous-session trades are shown only in Backtest/Reports.</p>`);
    return;
  }
  const trades = activeTradesFrom(state.lastResult).filter(trade => trade.mode !== "REAL");
  const pendingEntries = lifecycle.pending_entries || [];
  const orders = paperOrdersFromState(state.lastResult);
  const closedTrades = lifecycle.closed_trades || [];
  if (!trades.length && !pendingEntries.length && !orders.length && !closedTrades.length) {
    setHtml("#oa-paper-trades", `<p class="oa-empty-state">No paper trades or paper orders yet.</p>`);
    return;
  }
  const sections = [];
  if (trades.length) {
    sections.push(paperSection("Active Paper Trades", trades.map(renderMiniTrade).join("")));
  }
  if (pendingEntries.length) {
    sections.push(paperSection("Pending Entries", pendingEntries.slice(-6).reverse().map(renderPendingEntry).join("")));
  }
  if (orders.length) {
    sections.push(paperSection("Recent Paper Orders", orders.slice(-10).reverse().map(renderPaperOrderRows).join("")));
  }
  if (closedTrades.length) {
    sections.push(paperSection("Closed Paper Trades", closedTrades.slice(-6).reverse().map(renderClosedPaperTrade).join("")));
  }
  setHtml("#oa-paper-trades", sections.join(""));
}

function paperSection(title, body) {
  return `<section class="oa-paper-section"><h3>${escapeHtml(title)}</h3>${body}</section>`;
}

function renderMiniTrade(trade = {}) {
  return `<div class="oa-mini-trade">${renderTradeDetails(trade)}</div>`;
}

function renderPendingEntry(entry = {}) {
  const order = entry.entry_order || entry.order || {};
  const plan = entry.trade_plan || entry.plan || {};
  return `<div class="oa-mini-trade">
    ${row("Approval", entry.approval_id || entry.entry_id || "-")}
    ${row("Entry Order", order.order_id || entry.entry_order_id || "-")}
    ${row("Contract", plan.tradingsymbol || entry.tradingsymbol || order.tradingsymbol || "-")}
    ${row("Qty", plan.quantity || order.quantity || entry.quantity || "-")}
    ${row("Entry", plan.entry_price || order.price || entry.entry_price || "-")}
    ${row("Status", entry.status || order.status || "PENDING")}
    ${row("Created", entry.created_at || order.created_at || "-")}
  </div>`;
}

function renderPaperOrderRows(order = {}) {
  return `<div class="oa-order-grid">
    ${metric("Order ID", order.order_id || order.id || "-")}
    ${metric("Status", order.status || "-")}
    ${metric("Txn", order.transaction_type || order.side || "-")}
    ${metric("Contract", order.tradingsymbol || "-")}
    ${metric("Qty", order.quantity || order.filled_quantity || "-")}
    ${metric("Price", order.average_price || order.price || order.trigger_price || "-")}
    ${metric("Type", order.order_type || "-")}
    ${metric("Tag", order.tag || order.reason || "-")}
  </div>`;
}

function renderClosedPaperTrade(trade = {}) {
  return `<div class="oa-mini-trade">
    ${row("Contract", trade.tradingsymbol || "-")}
    ${row("Qty", trade.quantity || "-")}
    ${row("Entry", trade.entry_price || trade.average_price || "-")}
    ${row("Exit", trade.exit_price || "-")}
    ${row("Exit Reason", trade.exit_reason || trade.reason || "-")}
    ${row("Net P&L", money(trade.pnl_net ?? trade.net_pnl ?? 0))}
    ${row("Entry Order", trade.entry_order_id || "-")}
    ${row("Exit Order", trade.exit_order_id || trade.target_order_id || trade.stoploss_order_id || "-")}
    ${row("Closed", trade.closed_at || trade.updated_at || "-")}
  </div>`;
}

// real rendering/actions
function initRealTab() {
  on("#oa-real-preflight", "click", runRealPreflight);
  on("#oa-real-place", "click", startRealEngine);
  on("#oa-real-approve-entry", "click", approveRealEntry);
  on("#oa-real-reject-entry", "click", rejectRealEntry);
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
    state.lastRealPreflight = result;
    await refreshUiSummaryAfterMutation(result);
    renderRealPreflight(result);
    renderAll();
    setTabAlert("real", result.allowed ? "Real preflight passed. Real orders remain guarded by final validation and execution safety." : "Real preflight blocked. Review checklist.", result.allowed ? "success" : "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

function realEnginePayload() {
  return evaluationPayload("REAL");
}

async function startRealEngine() {
  try {
    const result = await api("/api/options-auto/real/start-engine", { ...realEnginePayload(), market_open: true, instruments_valid: true });
    if (result.approval?.approval_id || result.real_pending_approval?.approval_id) {
      state.realPendingApprovalId = result.approval?.approval_id || result.real_pending_approval?.approval_id;
    }
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("real", result.real_engine_started ? "Real scanner started. It will scan and wait for manual approval when required." : result.message || "Real scanner blocked.", result.real_engine_started ? "success" : "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function approveRealEntry() {
  try {
    const approvalId = state.realPendingApprovalId || realApprovalFromState().approval_id || "";
    const result = await api("/api/options-auto/real/approve-entry", { approval_id: approvalId, ...realEnginePayload(), market_open: true, instruments_valid: true });
    if (result.real_order_sent) state.realPendingApprovalId = "";
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("real", result.real_order_sent ? "Manual approval accepted. Real BUY entry was sent to Zerodha." : result.message || "Real approval did not send an order.", result.real_order_sent ? "success" : "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function rejectRealEntry() {
  try {
    const approvalId = state.realPendingApprovalId || realApprovalFromState().approval_id || "";
    const result = await api("/api/options-auto/real/reject-entry", { approval_id: approvalId });
    state.realPendingApprovalId = "";
    await refreshUiSummaryAfterMutation(result, { clearLastResult: true });
    renderAll();
    setTabAlert("real", "Real approval rejected. No Zerodha order was sent.", "info");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function runRealReconcile() {
  try {
    const result = await api("/api/options-auto/real/reconcile", { mode: "REAL" });
    await refreshUiSummaryAfterMutation(result);
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
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("real", result.message || "Real dry-run complete. No order placed.", "info");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function stopNewEntries() {
  try {
    const result = await api("/api/options-auto/real/stop-new-entries", { source: "UI" });
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("real", "Stop New Entries is active.", "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function runSafeMode() {
  try {
    const result = await api("/api/options-auto/real/safe-mode", { source: "UI" });
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("real", "Safe Mode is active.", "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

async function runEmergencyPlan() {
  try {
    const result = await api("/api/options-auto/real/emergency-plan", { mode: "REAL", confirmed: Boolean($("#oa-confirm-real")?.checked) });
    await refreshUiSummaryAfterMutation(result);
    renderAll();
    setTabAlert("real", "Emergency plan generated. Dry-run only; no orders sent.", "warning");
  } catch (error) {
    setTabAlert("real", error.message, "danger");
  }
}

function renderRealPreflight(result = {}) {
  result = realPreflightResult(result);
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
    setText("#oa-real-mode-title", "Real money connected. Run preflight, then start Real Scanner.");
    setText("#oa-real-mode-copy", "Until Real Scanner is started, no current real order lifecycle is shown.");
  }
  const evidence = result.evidence || {};
  const checks = evidence.checks || {};
  const reconciliation = result.reconciliation || evidence.reconciliation || {};
  const hasResult = realConnected && Boolean(result.state || evidence.timestamp || result.reconciliation);
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
  const active = isCurrentRealProcessActive(result) ? activeTradesFrom(result).filter(trade => trade.mode === "REAL") : [];
  setHtml("#oa-real-position", active.length ? active.map(trade => `<div class="oa-mini-trade">${renderTradeDetails(trade)}</div>`).join("") : `<p class="oa-empty-state">No active real position or current real order lifecycle is reported.</p>`);
  renderRealApprovalCard();
}

function renderRealApprovalCard() {
  const approval = realApprovalFromState();
  if (!approval || !approval.approval_id) {
    setBadge("#oa-real-approval-badge", "No Pending Approval", "grey");
    setHtml("#oa-real-approval-card", `<p class="oa-empty-state">No real entry approval is pending. Start Real Scanner to scan live data.</p>`);
    return;
  }
  state.realPendingApprovalId = approval.approval_id || state.realPendingApprovalId;
  const expiresIn = approval.expires_at_epoch ? Math.max(0, Math.round(approval.expires_at_epoch - Date.now() / 1000)) : "-";
  setBadge("#oa-real-approval-badge", approval.status || "PENDING", approval.status === "PENDING" ? "yellow" : "grey");
  setHtml("#oa-real-approval-card", [
    row("Approval ID", approval.approval_id || "-"),
    row("Status", approval.status || "-"),
    row("Contract", approval.trade_plan?.tradingsymbol || approval.selected_contract?.tradingsymbol || "-"),
    row("Entry", approval.trade_plan?.entry_price || "-"),
    row("Target", approval.trade_plan?.target || "-"),
    row("Stoploss", approval.trade_plan?.stoploss || "-"),
    row("Quantity", approval.trade_plan?.quantity || "-"),
    row("Reason", approval.reason || "Manual approval required before Zerodha order placement."),
    row("Countdown", expiresIn === 0 ? "Expired" : `${expiresIn} sec`),
  ].join(""));
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
  const paperLifecycle = paperLifecycleFromState(state.lastResult);
  const paper = paperAccountFromState(state.lastResult);
  const paperOrders = paperOrdersFromState(state.lastResult);
  setHtml("#oa-report-paper", [
    metric("Available", money(paper.available_balance || 0)),
    metric("Orders", paperOrders.length),
    metric("Closed Trades", (paperLifecycle.closed_trades || []).length),
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
  on("#oa-settings-reset", "click", () => loadDefaults({ resetControls: true }));
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
    syncSettingsToggles("settings");
    const result = await api("/api/options-auto/configure", settingsPayload());
    await refreshUiSummaryAfterMutation(result, { applySettings: true });
    renderAll();
    setTabAlert("settings", "Settings saved for future Options Auto sessions.", "success");
  } catch (error) {
    setTabAlert("settings", error.message, "danger");
  }
}

function syncSettingsToggles(source = "") {
  syncCheckboxPair("#oa-auto", "#oa-auto-settings", source === "settings");
  syncCheckboxPair("#oa-ask", "#oa-ask-settings", source === "settings");
}

function syncCheckboxPair(primarySelector, secondarySelector, preferSecondary = false) {
  const primary = $(primarySelector);
  const secondary = $(secondarySelector);
  if (!primary || !secondary) return;
  const checked = preferSecondary ? secondary.checked : primary.checked;
  primary.checked = checked;
  secondary.checked = checked;
}

function bindCheckboxMirror(primarySelector, secondarySelector) {
  const primary = $(primarySelector);
  const secondary = $(secondarySelector);
  if (!primary || !secondary || primary.dataset.oaMirrorBound) return;
  primary.dataset.oaMirrorBound = "true";
  secondary.dataset.oaMirrorBound = "true";
  primary.addEventListener("change", () => { secondary.checked = primary.checked; });
  secondary.addEventListener("change", () => { primary.checked = secondary.checked; });
}

function applySettings(settings) {
  const pairs = [
    ["#oa-setting-mode", settings.mode],
    ["#oa-underlying", settings.underlying],
    ["#oa-expiry-date", settings.expiry || settings.option_expiry],
    ["#oa-profile", settings.strategy_profile],
    ["#oa-entry-mode", normalizeEntryMode(settings.entry_dependency_mode)],
    ["#oa-chart-interval", settings.chart_interval],
    ["#oa-lots", settings.number_of_lots],
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
    ["#oa-major-strike-step", settings.major_strike_step],
    ["#oa-contract-reselect-minutes", settings.contract_reselection_minutes],
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
    ["#oa-backtest-expiry", settings.expiry || settings.option_expiry],
    ["#oa-backtest-lots", settings.number_of_lots],
    ["#oa-backtest-major-step", settings.major_strike_step],
    ["#oa-backtest-profile", settings.strategy_profile],
    ["#oa-backtest-entry-mode", normalizeEntryMode(settings.entry_dependency_mode)],
    ["#oa-backtest-score", settings.buy_score_threshold],
    ["#oa-backtest-span", settings.atm_scan_strike_span],
    ["#oa-news-event-provider", settings.news_event_provider],
    ["#oa-news-cache-ttl", settings.news_event_cache_ttl_seconds || settings.news_refresh_ttl_seconds],
    ["#oa-news-warning-score", settings.news_event_min_score_for_warning],
    ["#oa-news-shock-score", settings.news_event_min_score_for_shock],
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
    ["#oa-news-event-enabled", settings.news_event_enabled],
    ["#oa-news-market-confirm", settings.news_event_require_market_confirmation],
    ["#oa-news-show-ui", settings.news_event_show_in_ui],
    ["#oa-trailing", settings.trailing_stop_enabled],
    ["#oa-breakeven", settings.break_even_sl_enabled],
    ["#oa-partial", settings.partial_exit_enabled],
    ["#oa-reversal", settings.reversal_exit_enabled],
    ["#oa-time-exit", settings.time_exit_enabled],
    ["#oa-allow-deep-otm", settings.allow_deep_otm],
    ["#oa-strict-liquidity", settings.strict_liquidity_filter],
    ["#oa-confirm-real", settings.confirm_real_mode],
    ["#oa-static-ip", settings.static_ip_confirmed],
    ["#oa-dry-run-real", settings.dry_run_real_only],
    ["#oa-real-orders-enabled", settings.real_orders_enabled],
    ["#oa-real-auto-entry", settings.real_auto_entry_enabled],
    ["#oa-market-context-enforced", settings.market_context_enforcement_enabled],
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
function renderTradeDetails(trade = {}) {
  const pnl = trade.unrealized_pnl !== undefined
    ? money(trade.unrealized_pnl)
    : trade.pnl_net !== undefined || trade.net_pnl !== undefined
      ? money(trade.pnl_net ?? trade.net_pnl)
      : "-";
  return [
    row("Contract", trade.tradingsymbol || "-"),
    row("Status", trade.status || "-"),
    row("Quantity", trade.quantity || "-"),
    row("Entry Order", trade.entry_order_id || "-"),
    row("Entry Status", trade.entry_status || "-"),
    row("Entry Average", trade.entry_price || trade.average_price || "-"),
    row("Current LTP", trade.last_ltp || "-"),
    row("P&L", pnl),
    row("Target", trade.target || "-"),
    row("Target Order", trade.target_order_id || "-"),
    row("Target Status", trade.target_status || "-"),
    row("Stoploss", trade.stoploss || "-"),
    row("Stoploss Order", trade.stoploss_order_id || "-"),
    row("Stoploss Status", trade.stoploss_status || "-"),
    row("Trailing", trade.trailing_status || "-"),
    row("OCO", trade.oco_active ? "Active" : "Inactive"),
    row("Protected", trade.position_protected ? "YES" : "NO"),
    row("Protected State", trade.protected_state || "-"),
    row("Last Update", trade.updated_at || trade.opened_at || "-"),
  ].join("");
}

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
  if (!node) return;
  if (event === "click" && node.tagName === "BUTTON") {
    node.addEventListener(event, eventObject => guardedCommand(selector, node, () => handler(eventObject)));
    return;
  }
  node.addEventListener(event, handler);
}

function initNativeDatePickers() {
  $$("input[type='date']").forEach(input => {
    input.classList.add("native-date-input");
    input.addEventListener("click", () => {
      if (typeof input.showPicker !== "function" || input.disabled || input.readOnly) return;
      try { input.showPicker(); } catch {}
    });
    input.addEventListener("keydown", event => {
      if (!["Enter", " "].includes(event.key)) return;
      if (typeof input.showPicker !== "function" || input.disabled || input.readOnly) return;
      try { input.showPicker(); } catch {}
    });
  });
}

// init
async function refreshUiSummaryAfterMutation(result = {}, options = {}) {
  const staleWarning = $("#oa-ui-stale-warning");
  try {
    const payload = normalizeRefreshPayload(await api("/api/options-auto/ui-summary", undefined, { timeoutMs: options.timeoutMs || 5000 }));
    state.lastRefreshOkAt = Date.now();
    staleWarning?.classList.remove("is-visible");
    state.status = payload;
    syncRealPreflightCache(payload);
    const backtestSummary = backtestSummaryFromStatus(payload);
    if (backtestSummary && !options.preserveBacktest) state.lastBacktest = backtestSummary;
    if (options.clearLastResult) {
      state.lastResult = {};
    } else {
      replaceLastResultFromStatus(payload);
    }
    if (options.applySettings) {
      applySettings(payload.settings || result.settings || {});
    }
    return payload;
  } catch (error) {
    if (staleWarning) {
      staleWarning.textContent = `UI stale - ${error.message}`;
      staleWarning.classList.add("is-visible");
    }
    if (result && Object.keys(result).length) {
      state.status = normalizeRefreshPayload({ ...state.status, ...result });
      if (options.clearLastResult) {
        state.lastResult = {};
      } else {
        replaceLastResultFromStatus(state.status);
      }
      if (options.applySettings) {
        applySettings(state.status.settings || result.settings || {});
      }
    }
    return state.status;
  }
}

async function refresh(options = {}) {
  if (state.refreshBusy) return;
  state.refreshBusy = true;
  const staleWarning = $("#oa-ui-stale-warning");
  const useFullStatus = Boolean(options.full || state.activeTab === "debug");
  const endpoint = useFullStatus ? "/api/options-auto/status" : "/api/options-auto/ui-summary";
  try {
    const payload = normalizeRefreshPayload(await api(endpoint, undefined, { timeoutMs: useFullStatus ? 6000 : 3000 }));
    state.lastRefreshOkAt = Date.now();
    staleWarning?.classList.remove("is-visible");
    state.status = payload;
    syncRealPreflightCache(payload);
    const backtestSummary = backtestSummaryFromStatus(payload);
    if (backtestSummary) state.lastBacktest = backtestSummary;
    replaceLastResultFromStatus(payload);
    renderAll();
  } catch (error) {
    if (staleWarning) {
      staleWarning.textContent = `UI stale - ${error.message}`;
      staleWarning.classList.add("is-visible");
    }
    throw error;
  } finally {
    state.refreshBusy = false;
    if (staleWarning && state.lastRefreshOkAt && Date.now() - state.lastRefreshOkAt > 6000) {
      staleWarning.textContent = `UI stale - last successful update ${Math.round((Date.now() - state.lastRefreshOkAt) / 1000)}s ago.`;
      staleWarning.classList.add("is-visible");
    }
  }
}

function normalizeRefreshPayload(payload = {}) {
  const previous = state.status || {};
  const last = state.lastResult || {};
  const normalized = { ...(payload || {}) };
  const previousAccount = previous.account_status || last.account_status || {};
  if (!normalized.account_status || !Object.keys(normalized.account_status).length) {
    normalized.account_status = previousAccount;
  }
  const previousSettings = previous.settings || last.settings || {};
  if (!normalized.settings || !Object.keys(normalized.settings).length) {
    normalized.settings = { ...previousSettings };
  }
  if (!normalized.settings.mode) {
    normalized.settings = { ...normalized.settings, mode: normalized.mode || previous.mode || last.mode || "PAPER" };
  }
  return normalized;
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
    tick_streams: payload.options_live_feed?.tick_streams || {},
    live_index_candles: payload.live_index_candles || {},
    live_scan: payload.live_scan || {},
    options_live_feed: payload.options_live_feed || {},
    runtime_persistence: payload.runtime_persistence || {},
    reference_cache: payload.reference_cache || {},
    feature_cache: payload.feature_cache || {},
    api_budget: payload.api_budget || {},
    blackbox: payload.blackbox || {},
  };
}

function replaceLastResultFromStatus(payload = {}) {
  const decision = payload.session?.last_decision || {};
  if (!Object.keys(decision).length) {
    state.lastResult = {};
    return;
  }
  state.lastResult = isCurrentLiveDecision(decision, payload) ? hydrateStatusDecision(payload) : {};
}

async function loadDefaults(options = {}) {
  const resetControls = Boolean(options.resetControls);
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
  await refresh({ full: true });
  if (!resetControls) {
    applySettings(state.status.settings || settings);
  }
}

function initDashboard() {
  on("#oa-top-refresh", "click", () => refresh({ full: true }));
  on("#oa-stop-engine-top", "click", stopEngine);
  on("#oa-kill-switch-top", "click", killSwitch);
  initFiiDiiUpload();
}

function initReports() {
  renderReports();
}

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initTickStreamTabs();
  initNativeDatePickers();
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
  scheduleRefresh();
  document.addEventListener("visibilitychange", scheduleRefresh);
});

function refreshDelayMs() {
  if (state.activeTab === "debug") return 10000;
  return document.visibilityState === "visible" ? 1000 : 7000;
}

function scheduleRefresh() {
  if (refreshTimer) window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => {
    refresh({ full: state.activeTab === "debug" }).catch(() => {}).finally(scheduleRefresh);
  }, refreshDelayMs());
}
