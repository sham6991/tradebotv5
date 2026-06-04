const $ = (selector, root = document) => root.querySelector(selector);

const state = {
  defaults: {},
  status: {},
  lastResult: {},
  pendingApprovalId: "",
  activeLog: "decision",
};

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

function sampleInstruments() {
  return [
    { name: "NIFTY", tradingsymbol: "NIFTY26JUN22500CE", instrument_token: "1001", instrument_type: "CE", strike: 22500, lot_size: 50, tick_size: 0.05 },
    { name: "NIFTY", tradingsymbol: "NIFTY26JUN22500PE", instrument_token: "1002", instrument_type: "PE", strike: 22500, lot_size: 50, tick_size: 0.05 },
    { name: "NIFTY", tradingsymbol: "NIFTY26JUN22600CE", instrument_token: "1003", instrument_type: "CE", strike: 22600, lot_size: 50, tick_size: 0.05 },
    { name: "NIFTY", tradingsymbol: "NIFTY26JUN22400PE", instrument_token: "1004", instrument_type: "PE", strike: 22400, lot_size: 50, tick_size: 0.05 },
  ];
}

function sampleQuotes() {
  return {
    "1001": { ltp: 142.4, bid: 142.25, ask: 142.45, bid_qty: 1450, ask_qty: 1300, volume: 85000, oi: 950000, momentum_score: 72 },
    "1002": { ltp: 120.2, bid: 119.9, ask: 120.25, bid_qty: 900, ask_qty: 1150, volume: 76000, oi: 870000, momentum_score: 46 },
    "1003": { ltp: 97.6, bid: 97.2, ask: 97.95, bid_qty: 800, ask_qty: 850, volume: 53000, oi: 520000, momentum_score: 68 },
    "1004": { ltp: 88.5, bid: 88.05, ask: 88.7, bid_qty: 750, ask_qty: 800, volume: 51000, oi: 500000, momentum_score: 44 },
  };
}

function sampleMarketCue() {
  return { phase: "LUNCH", technical_score: 34, option_oi_score: 18, news_score: 2 };
}

function sampleReplayCandles() {
  return [
    { datetime: "2026-06-04 09:15", open: 100, high: 105, low: 98, close: 103, volume: 1000 },
    { datetime: "2026-06-04 09:18", open: 103, high: 108, low: 101, close: 107, volume: 1200 },
  ];
}

function parseJson(id, fallback) {
  const text = $(id).value.trim();
  if (!text) return fallback;
  return JSON.parse(text);
}

function settingsPayload() {
  return {
    mode: $("#oa-setting-mode").value,
    underlying: $("#oa-underlying").value,
    strategy_profile: $("#oa-profile").value,
    buy_score_threshold: Number($("#oa-score-threshold").value || 70),
    paper_starting_balance: Number($("#oa-paper-balance").value || 20000),
    max_capital_per_trade_pct: Number($("#oa-capital-pct").value || 20),
    ask_permission_before_entry: $("#oa-ask").checked,
    auto_entry_enabled: $("#oa-auto").checked,
    confirm_real_mode: $("#oa-confirm-real").checked,
    static_ip_confirmed: $("#oa-static-ip").checked,
  };
}

function evaluationPayload(modeOverride = "") {
  const settings = settingsPayload();
  if (modeOverride) settings.mode = modeOverride;
  return {
    mode: settings.mode,
    settings,
    spot: Number($("#oa-spot").value || 0),
    quote_age_seconds: Number($("#oa-quote-age").value || 0),
    market_cue: parseJson("#oa-market-cue-json", sampleMarketCue()),
    instruments: parseJson("#oa-instruments-json", sampleInstruments()),
    quotes: parseJson("#oa-quotes-json", sampleQuotes()),
    features: { ema_alignment_score: 18, vwap_score: 14, rsi_slope_score: 10, volume_score: 8, depth_score: 5 },
    time_of_day_score: 70,
  };
}

function setText(id, text) {
  const node = $(id);
  if (node) node.textContent = text;
}

function money(value) {
  const number = Number(value || 0);
  return number.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function renderStatus(payload) {
  state.status = payload || {};
  const settings = payload.settings || state.defaults.settings || {};
  const session = payload.session || {};
  const account = payload.account_status || {};
  setText("#oa-mode", settings.mode || "PAPER");
  setText("#oa-engine", session.status || "Idle");
  setText("#oa-real-orders", settings.real_orders_enabled ? "Enabled" : "Disabled");
  const connected = settings.mode === "REAL" ? account.real?.connected : account.paper?.connected;
  setText("#oa-kite", connected ? "Connected" : "Disconnected");
  const dataOk = state.lastResult.data_quality?.allowed;
  setText("#oa-data", dataOk ? "Fresh" : "Waiting");
  const health = payload.watchdog || state.lastResult.watchdog || {};
  setText("#oa-health-top", health.mode || "-");
}

function renderResult(result) {
  state.lastResult = result || {};
  renderStatus({ ...(state.status || {}), settings: { ...(state.status.settings || {}), mode: result.mode || state.status.settings?.mode }, session: result.session, account_status: result.account_status });
  const cue = result.market_cue || {};
  const regime = result.regime || {};
  const selection = result.selection || {};
  const selected = selection.selected || {};
  setText("#oa-cue", cue.cue || "-");
  setText("#oa-cue-score", cue.score !== undefined ? `${cue.score} / ${cue.confidence}` : "0");
  setText("#oa-regime", regime.regime || "-");
  setText("#oa-regime-side", regime.recommended_side || "WAIT");
  setText("#oa-contract", selected.tradingsymbol || "-");
  setText("#oa-contract-score", selection.score ? `${selection.score.toFixed(1)}` : "0");

  const rows = [
    ["Decision", result.allowed ? "ALLOW" : "WAIT"],
    ["Side", selection.side || regime.recommended_side || "WAIT"],
    ["Contract", selected.tradingsymbol || "-"],
    ["Score", selection.score ? selection.score.toFixed(2) : "0"],
    ["LTP", selected.ltp || "-"],
    ["Spread", selected.spread_pct !== undefined ? `${selected.spread_pct}%` : "-"],
    ["Moneyness", selected.moneyness || "-"],
    ["Reason", result.explanation || "-"],
    ["Blockers", (result.blockers || []).join("; ") || "-"],
  ];
  $("#oa-plan-body").innerHTML = rows.map(([name, value]) => `<div class="oa-plan-row"><span>${name}</span><strong>${value}</strong></div>`).join("");
  renderSafety(result);
  if (result.approval?.approval_id) state.pendingApprovalId = result.approval.approval_id;
  renderLog();
}

function renderSafety(result) {
  const isReal = result.mode === "REAL";
  const dataFresh = result.data_quality?.allowed;
  const attention = (result.blockers || []).length > 0;
  const activeTrades = result.session?.active_trades || result.paper_lifecycle?.active_trades || [];
  const protectedOk = !activeTrades.length || activeTrades.every(trade => trade.position_protected);
  const ocoOk = !activeTrades.length || activeTrades.every(trade => trade.oco_active);
  setSafety("#oa-protected", protectedOk, protectedOk ? "YES" : "NO");
  setSafety("#oa-oco", ocoOk, ocoOk ? "YES" : "NO");
  setSafety("#oa-real", isReal, isReal ? "YES" : "NO");
  const connected = result.account_status ? (isReal ? result.account_status.real?.connected : result.account_status.paper?.connected) : false;
  setSafety("#oa-kite-safe", Boolean(connected), connected ? "YES" : "NO");
  setSafety("#oa-fresh", dataFresh, dataFresh ? "YES" : "NO");
  const watchdog = result.watchdog || {};
  if (watchdog.mode) {
    const healthOk = watchdog.mode === "NORMAL";
    setSafety("#oa-health", healthOk, watchdog.mode, healthOk ? "ok" : watchdog.mode === "CRITICAL" || watchdog.mode === "LOCKED" ? "bad" : "warn");
  }
  const reconciliation = result.reconciliation || result.evidence?.reconciliation;
  if (reconciliation) {
    setSafety("#oa-reconcile", Boolean(reconciliation.ok), reconciliation.ok ? "YES" : "NO");
  }
  setSafety("#oa-attention", !attention, attention ? "YES" : "NO", attention ? "warn" : "ok");
}

function setSafety(selector, ok, label = "", forcedClass = "") {
  const node = $(selector);
  if (!node) return;
  node.classList.remove("oa-ok", "oa-bad", "oa-warn");
  node.classList.add(forcedClass ? `oa-${forcedClass}` : ok ? "oa-ok" : "oa-bad");
  const strong = $("strong", node);
  if (strong) strong.textContent = label || (ok ? "YES" : "NO");
}

function renderLog() {
  const result = state.lastResult || {};
  const session = result.session || state.status.session || {};
  let content = result;
  if (state.activeLog === "decision") content = session.decision_log || [];
  if (state.activeLog === "rejected") content = session.rejected_log || [];
  if (state.activeLog === "safety") content = session.safety_events || [];
  $("#oa-log").textContent = JSON.stringify(content, null, 2);
}

async function refresh() {
  const payload = await api("/api/options-auto/status");
  state.status = payload;
  renderStatus(payload);
  if (!state.lastResult.session) renderLog();
}

async function runAction(path, mode = "") {
  try {
    const result = await api(path, evaluationPayload(mode));
    renderResult(result);
  } catch (error) {
    $("#oa-log").textContent = error.message;
  }
}

async function runSimple(path, payload = {}) {
  try {
    const result = await api(path, payload);
    state.lastResult = result;
    renderStatus({ ...(state.status || {}), settings: state.status.settings || state.defaults.settings || {}, session: result.session || state.status.session, account_status: result.account_status, watchdog: result.watchdog });
    renderSafety(result);
    renderLog();
  } catch (error) {
    $("#oa-log").textContent = error.message;
  }
}

async function loadDefaults() {
  state.defaults = await api("/api/options-auto/defaults");
  const settings = state.defaults.settings || {};
  $("#oa-setting-mode").value = settings.mode || "PAPER";
  $("#oa-underlying").value = settings.underlying || "NIFTY";
  $("#oa-profile").value = settings.strategy_profile || "BALANCED";
  $("#oa-score-threshold").value = settings.buy_score_threshold || 70;
  $("#oa-paper-balance").value = settings.paper_starting_balance || 20000;
  $("#oa-capital-pct").value = settings.max_capital_per_trade_pct || 20;
  $("#oa-ask").checked = Boolean(settings.ask_permission_before_entry);
  $("#oa-auto").checked = Boolean(settings.auto_entry_enabled);
  $("#oa-confirm-real").checked = Boolean(settings.confirm_real_mode);
  $("#oa-static-ip").checked = Boolean(settings.static_ip_confirmed);
  $("#oa-market-cue-json").value = JSON.stringify(sampleMarketCue(), null, 2);
  $("#oa-instruments-json").value = JSON.stringify(sampleInstruments(), null, 2);
  $("#oa-quotes-json").value = JSON.stringify(sampleQuotes(), null, 2);
  await refresh();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#oa-refresh").addEventListener("click", refresh);
  $("#oa-evaluate").addEventListener("click", () => runAction("/api/options-auto/evaluate"));
  $("#oa-shadow").addEventListener("click", () => runAction("/api/options-auto/shadow/start", "SHADOW"));
  $("#oa-paper").addEventListener("click", () => runAction("/api/options-auto/paper/start", "PAPER"));
  $("#oa-paper-approval").addEventListener("click", () => runAction("/api/options-auto/paper/request-approval", "PAPER"));
  $("#oa-paper-approve").addEventListener("click", () => runSimple("/api/options-auto/paper/approve", { approval_id: state.pendingApprovalId }));
  $("#oa-paper-execute").addEventListener("click", () => runAction("/api/options-auto/paper/execute-plan", "PAPER"));
  $("#oa-paper-process").addEventListener("click", () => runSimple("/api/options-auto/paper/process-market", { market: { ltp: Number($("#oa-spot").value || 0), high: Number($("#oa-spot").value || 0), low: Number($("#oa-spot").value || 0) } }));
  $("#oa-real-dry").addEventListener("click", () => runAction("/api/options-auto/real/dry-run", "REAL"));
  $("#oa-real-preflight").addEventListener("click", () => runSimple("/api/options-auto/real/preflight", { ...evaluationPayload("REAL"), market_open: true, instruments_valid: true }));
  $("#oa-real-reconcile").addEventListener("click", () => runSimple("/api/options-auto/real/reconcile", { mode: "REAL", broker_orders: [], positions: [] }));
  $("#oa-real-stop").addEventListener("click", () => runSimple("/api/options-auto/real/stop-new-entries", { source: "UI" }));
  $("#oa-real-safe").addEventListener("click", () => runSimple("/api/options-auto/real/safe-mode", { source: "UI" }));
  $("#oa-real-emergency").addEventListener("click", () => runSimple("/api/options-auto/real/emergency-plan", { mode: "REAL", confirmed: $("#oa-confirm-real").checked, positions: [] }));
  $("#oa-readiness").addEventListener("click", () => runSimple("/api/options-auto/readiness", { mode: $("#oa-setting-mode").value, data_feed_alive: true, last_update_age_seconds: Number($("#oa-quote-age").value || 0) }));
  $("#oa-health-check").addEventListener("click", () => runSimple("/api/options-auto/health", { mode: $("#oa-setting-mode").value, data_feed_alive: true, last_update_age_seconds: Number($("#oa-quote-age").value || 0), memory_pct: 0, cpu_pct: 0 }));
  $("#oa-backtest").addEventListener("click", () => runAction("/api/options-auto/backtest/run", "BACKTEST"));
  $("#oa-shadow-report").addEventListener("click", () => runSimple("/api/options-auto/shadow/report"));
  $("#oa-promotion").addEventListener("click", () => runSimple("/api/options-auto/promotion/status", { metrics: { current_stage: "LEARNING", sessions_completed: 5, net_pnl: 1200, max_drawdown_pct: 4, unprotected_position_incidents: 0, major_safety_errors: 0 } }));
  $("#oa-drift").addEventListener("click", () => runSimple("/api/options-auto/drift/status", { trades: [{ pnl: 200 }, { pnl: -80 }, { pnl: 150 }] }));
  $("#oa-missed").addEventListener("click", () => runSimple("/api/options-auto/missed-trades/status", { decisions: [{ allowed: true, actual_pnl: 120 }, { allowed: false, actual_pnl: 80, reason: "Spread too wide" }] }));
  $("#oa-replay").addEventListener("click", () => runSimple("/api/options-auto/replay/run", { candles: sampleReplayCandles(), decisions: [{ decision: "WAIT", reason: "Opening range forming" }, { decision: "WAIT", reason: "No order in replay" }] }));
  $("#oa-telegram-status").addEventListener("click", () => runSimple("/api/options-auto/telegram/command", { command: "status", user_id: "UI" }));
  document.querySelectorAll("[data-oa-log]").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-oa-log]").forEach(item => item.classList.remove("active"));
      button.classList.add("active");
      state.activeLog = button.dataset.oaLog;
      renderLog();
    });
  });
  loadDefaults().catch(error => {
    $("#oa-log").textContent = error.message;
  });
});
