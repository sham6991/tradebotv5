const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const SETUP_STORAGE_KEY = "tradebotv5_intraday_setup";

const state = {
  symbols: ["INFY", "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK"],
  exchanges: ["NSE", "NSE", "NSE", "NSE", "NSE"],
  defaultSettings: null,
  status: null,
  accountStatus: null,
  lastMarketData: null,
  statusPollTimer: null,
  statusPollBusy: false,
  statusPollCount: 0,
  fiiDiiUpload: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed ${response.status}`);
  return data;
}

async function apiForm(path, formData) {
  const response = await fetch(path, { method: "POST", body: formData });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed ${response.status}`);
  return data;
}

function money(value) {
  return Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function switchStep(step) {
  $$(".flow-step").forEach((button) => button.classList.toggle("active", button.dataset.step === step));
  $$(".flow-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `${step}-step`));
}

function switchPaperWorkflow(workflow) {
  $$(".paper-workflow-tab").forEach((button) => button.classList.toggle("active", button.dataset.paperWorkflow === workflow));
  $$(".paper-workflow-panel").forEach((panel) => panel.classList.toggle("active", panel.dataset.paperPanel === workflow));
}

function updateWorkflowForMode() {
  const isPaper = $("#mode").value === "PAPER";
  $(".paper-workflow-tabs").classList.toggle("hidden", !isPaper);
  $("#backtest-workflow").classList.toggle("hidden", !isPaper);
  if (!isPaper) {
    switchPaperWorkflow("live");
    $("#start-session").textContent = "Start Real Session";
  } else {
    $("#start-session").textContent = "Start Live Paper Session";
  }
  applyModeLock();
}

function disableForm(selector, disabled) {
  const form = document.querySelector(selector);
  if (!form) return;
  form.classList.toggle("disabled-card", disabled);
  form.querySelectorAll("input, select, button").forEach((field) => {
    field.disabled = disabled;
  });
}

function currentModeLock() {
  const statusMode = state.status?.status === "RUNNING" ? state.status?.settings?.mode : "";
  if (statusMode === "REAL") return { mode: "REAL", reason: "Real Money intraday engine is running." };
  if (statusMode === "PAPER") return { mode: "PAPER", reason: "Paper Trading intraday engine is running." };
  const accountLock = state.accountStatus?.mode_lock || {};
  if (accountLock.mode) return accountLock;
  return { mode: "", reason: "" };
}

function applyModeLock() {
  const lock = currentModeLock();
  const lockedMode = lock.mode === "LIVE" ? "REAL" : lock.mode;
  const running = state.status?.status === "RUNNING";
  $("#mode").disabled = running || Boolean(lockedMode);
  if (lockedMode === "PAPER") $("#mode").value = "PAPER";
  if (lockedMode === "REAL") $("#mode").value = "REAL";
  ["#chart-interval", "#limit-timeout", "#permission", "#side", "#min-score", "#min-rr", "#max-loss", "#max-trades", "#max-open-trades", "#risk-pct", "#capital-pct", "#estimated-leverage", "#max-quantity", "#max-capital", "#confirm-real", "#auto-real-orders-confirmed", "#live-news-enabled", "#active-management-enabled", "#breakeven-enabled", "#trailing-enabled", "#partial-exit-enabled", "#allow-simulated-fallback", "#recalculate-exit-fill", "#trailing-method", "#paper-fill-model", "#emergency-exit-order-type", "#paper-balance-input", "#save-paper-balance", "#reset-paper-balance"].forEach((selector) => {
    const field = $(selector);
    if (field) field.disabled = running;
  });
  updateWorkflowForModeWithoutLock();
}

function updateWorkflowForModeWithoutLock() {
  const isPaper = $("#mode").value === "PAPER";
  $(".paper-workflow-tabs").classList.toggle("hidden", !isPaper);
  $("#backtest-workflow").classList.toggle("hidden", !isPaper);
  $("#start-session").textContent = isPaper ? "Start Live Paper Engine" : "Start Real Engine";
}

function renderSymbols() {
  const grid = $("#symbols-grid");
  grid.innerHTML = "";
  state.symbols.forEach((symbol, index) => {
    const wrap = document.createElement("div");
    wrap.className = "symbol-pair";
    const input = document.createElement("input");
    input.value = symbol;
    input.placeholder = `Stock ${index + 1}`;
    input.addEventListener("input", () => {
      state.symbols[index] = input.value.toUpperCase();
    });
    const exchange = document.createElement("select");
    exchange.innerHTML = "<option>NSE</option><option>BSE</option>";
    exchange.value = state.exchanges[index] || "NSE";
    exchange.addEventListener("change", () => {
      state.exchanges[index] = exchange.value;
    });
    wrap.append(input, exchange);
    grid.append(wrap);
  });
}

function payloadFromForm() {
  const side = $("#side").value;
  return {
    mode: $("#mode").value,
    broker: "Zerodha",
    strategy_profile: $("#strategy-profile").value,
    stocks: $$(".symbol-pair").map((wrap) => ({
      symbol: wrap.querySelector("input").value.trim().toUpperCase(),
      exchange: wrap.querySelector("select").value,
    })),
    ask_permission_before_entry: $("#permission").value === "yes",
    order_mode: "LIMIT_ONLY",
    side_permission: side,
    candle_interval: $("#chart-interval").value,
    limit_order_timeout_seconds: Number($("#limit-timeout").value || 30),
    minimum_entry_score: Number($("#min-score").value || 70),
    minimum_risk_reward: Number($("#min-rr").value || 1.5),
    max_daily_loss: Number($("#max-loss").value || 2500),
    max_trades_per_day: Number($("#max-trades").value || 5),
    max_open_positions: Number($("#max-open-trades").value || 1),
    risk_per_trade_pct: Number($("#risk-pct").value || 1),
    max_capital_allocation_pct: Number($("#capital-pct").value || 20),
    estimated_leverage: Number($("#estimated-leverage").value || 5),
    max_quantity_per_trade: Number($("#max-quantity").value || 0),
    max_capital_per_trade: Number($("#max-capital").value || 25000),
    active_trade_management_enabled: $("#active-management-enabled").checked,
    breakeven_sl_enabled: $("#breakeven-enabled").checked,
    active_trailing_sl_enabled: $("#trailing-enabled").checked,
    trailing_method: $("#trailing-method").value,
    paper_fill_model: $("#paper-fill-model").value,
    emergency_exit_order_type: $("#emergency-exit-order-type").value,
    partial_exit_enabled: $("#partial-exit-enabled").checked,
    allow_simulated_fallback: $("#allow-simulated-fallback").checked,
    require_live_data_for_paper: !$("#allow-simulated-fallback").checked,
    show_data_source_warning: true,
    status_refresh_uses_cached_broker_state: true,
    recalculate_exit_from_actual_fill: $("#recalculate-exit-fill").checked,
    breakeven_trigger_r: 1,
    trail_activation_r: 1.2,
    partial_exit_trigger_r: 1,
    partial_exit_qty_pct: 50,
    condition_exit_enabled: true,
    dynamic_target_enabled: true,
    time_exit_enabled: true,
    news_enabled: true,
    live_news_enabled: $("#live-news-enabled").checked,
    paper_starting_balance: Number(state.accountStatus?.paper?.funds?.available || 100000),
    confirm_real_mode: $("#confirm-real").checked,
    auto_real_orders_confirmed: $("#auto-real-orders-confirmed").checked,
  };
}

function loadSavedSetup() {
  try {
    return JSON.parse(localStorage.getItem(SETUP_STORAGE_KEY) || "null");
  } catch (error) {
    return null;
  }
}

function saveSetup() {
  const payload = payloadFromForm();
  localStorage.setItem(SETUP_STORAGE_KEY, JSON.stringify(payload));
  $("#status-message").textContent = "Setup saved. Future intraday sessions will load these values.";
}

function resetSetupDefaults() {
  localStorage.removeItem(SETUP_STORAGE_KEY);
  applyPayloadToForm(state.defaultSettings || {});
  $("#status-message").textContent = "Setup reset to backend defaults.";
}

function applyPayloadToForm(payload = {}) {
  const stocks = payload.stocks || payload.symbols || [];
  if (stocks.length) {
    state.symbols = stocks.slice(0, 5).map((row) => {
      if (typeof row === "string") return row.includes(":") ? row.split(":").pop().toUpperCase() : row.toUpperCase();
      return String(row.symbol || row.tradingsymbol || "").toUpperCase();
    });
    state.exchanges = stocks.slice(0, 5).map((row) => {
      if (typeof row === "string" && row.includes(":")) return row.split(":")[0].toUpperCase();
      return String(row.exchange || "NSE").toUpperCase();
    });
    while (state.symbols.length < 5) state.symbols.push("");
    while (state.exchanges.length < 5) state.exchanges.push("NSE");
    if ($("#symbols-grid")) renderSymbols();
  }
  setFieldValue("#mode", payload.mode || "PAPER");
  setFieldValue("#strategy-profile", payload.strategy_profile || "BALANCED");
  setFieldValue("#permission", payload.ask_permission_before_entry === false ? "no" : "yes");
  setFieldValue("#side", payload.side_permission || (payload.allow_long === false ? "SHORT_ONLY" : payload.allow_short === false ? "LONG_ONLY" : "BOTH"));
  setFieldValue("#chart-interval", payload.candle_interval || "minute");
  setFieldValue("#limit-timeout", payload.limit_order_timeout_seconds || 30);
  setFieldValue("#min-score", payload.minimum_entry_score || 70);
  setFieldValue("#min-rr", payload.minimum_risk_reward || payload.min_risk_reward || 1.5);
  setFieldValue("#max-loss", payload.max_daily_loss || 2500);
  setFieldValue("#max-trades", payload.max_trades_per_day || 5);
  setFieldValue("#max-open-trades", payload.max_open_positions || 1);
  setFieldValue("#risk-pct", payload.risk_per_trade_pct || 1);
  setFieldValue("#capital-pct", payload.max_capital_allocation_pct || 20);
  setFieldValue("#estimated-leverage", payload.estimated_leverage || 5);
  setFieldValue("#max-quantity", payload.max_quantity_per_trade || 0);
  setFieldValue("#max-capital", payload.max_capital_per_trade || 25000);
  setFieldValue("#trailing-method", payload.trailing_method || "HYBRID");
  setFieldValue("#paper-fill-model", payload.paper_fill_model || "CANDLE_TOUCH_CONSERVATIVE");
  setFieldValue("#emergency-exit-order-type", payload.emergency_exit_order_type || "AGGRESSIVE_LIMIT");
  setChecked("#live-news-enabled", payload.live_news_enabled !== false);
  setChecked("#active-management-enabled", payload.active_trade_management_enabled !== false);
  setChecked("#breakeven-enabled", payload.breakeven_sl_enabled !== false);
  setChecked("#trailing-enabled", payload.active_trailing_sl_enabled !== false);
  setChecked("#partial-exit-enabled", Boolean(payload.partial_exit_enabled));
  setChecked("#allow-simulated-fallback", Boolean(payload.allow_simulated_fallback));
  setChecked("#recalculate-exit-fill", payload.recalculate_exit_from_actual_fill !== false);
  setChecked("#auto-real-orders-confirmed", Boolean(payload.auto_real_orders_confirmed));
  updateWorkflowForModeWithoutLock();
}

function setFieldValue(selector, value) {
  const field = $(selector);
  if (field) field.value = value;
}

function setChecked(selector, value) {
  const field = $(selector);
  if (field) field.checked = Boolean(value);
}

function sampleCandles(symbol, index, future = false) {
  const base = 1200 + index * 185;
  const bullish = index % 2 === 0;
  const rows = [];
  const count = future ? 38 : 34;
  for (let i = 0; i < count; i += 1) {
    const drift = bullish ? i * 1.8 : -i * 1.35;
    const wave = Math.sin(i / 3) * 4;
    const open = base + drift + wave;
    const close = open + (bullish ? 2.4 : -2.1) + Math.cos(i / 2);
    const high = Math.max(open, close) + 3.2;
    const low = Math.min(open, close) - 2.6;
    rows.push({
      timestamp: new Date(Date.now() - (count - i) * 60000).toISOString(),
      open,
      high,
      low,
      close,
      volume: 80000 + i * 1700 + index * 9000,
    });
  }
  const ltp = rows[rows.length - 1].close;
  return {
    ltp,
    candles: rows,
    depth: {
      buy: [{ price: ltp - 0.05, quantity: 12000 + index * 1200 }, { price: ltp - 0.10, quantity: 9000 }],
      sell: [{ price: ltp + 0.05, quantity: 9800 }, { price: ltp + 0.10, quantity: 8500 + index * 1100 }],
    },
  };
}

function evaluationPayload(future = false) {
  const marketData = {};
  state.symbols.forEach((symbol, index) => {
    marketData[symbol] = sampleCandles(symbol, index, future);
  });
  state.lastMarketData = marketData;
  return {
    market_trend: "Bullish",
    market_data: marketData,
    news: [
      { symbol: state.symbols[0], headline: `${state.symbols[0]} wins growth order and beats sector expectations`, source: "Manual" },
      { symbol: state.symbols[1], headline: `${state.symbols[1]} sees weak volume after downgrade report`, source: "Manual" },
    ],
  };
}

function renderAccountStatus(data) {
  state.accountStatus = data;
  const paper = data.paper?.funds || {};
  const real = data.real?.funds || {};
  const paperConnection = data.paper?.zerodha_data_connection || {};
  const realConnection = data.real?.zerodha || {};
  const paperConnected = Boolean(paperConnection.connected);
  const realConnected = Boolean(realConnection.connected);
  $("#paper-balance").textContent = money(paper.available);
  $("#real-balance").textContent = realConnected ? money(real?.available) : "Not Connected";
  const lock = currentModeLock();
  $("#account-status").textContent = `Main App Connections: Paper ${paperConnected ? "connected" : "not connected"} | Real ${realConnected ? "connected" : "not connected"}${lock.reason ? ` | ${lock.reason}` : ""}`;
  renderConnectionCard("#paper-connection-card", "#paper-connection-state", "#paper-connection-detail", paperConnection, "PAPER live data and Zerodha historical backtest data");
  renderConnectionCard("#real-connection-card", "#real-connection-state", "#real-connection-detail", realConnection, "REAL intraday orders and margin checks");
  if (!paperConnected) {
    $("#paper-connection-detail").textContent = "Connect from Main App -> Connections for PAPER live data and Zerodha historical backtest data. Backtest can still run with simulated data.";
  }
  const pendingProfit = Number(paper.pending_session_profit || 0);
  $("#paper-account-state").textContent = `${money(paper.available)} available${pendingProfit ? ` | ${money(pendingProfit)} pending profit` : ""}`;
  if ($("#paper-balance-input") && document.activeElement !== $("#paper-balance-input")) {
    $("#paper-balance-input").value = Math.round(Number(paper.available || 0));
  }
  applyModeLock();
}

function renderConnectionCard(cardSelector, stateSelector, detailSelector, connection, purpose) {
  const connected = Boolean(connection?.connected);
  const card = $(cardSelector);
  if (card) {
    card.classList.toggle("connected", connected);
    card.classList.toggle("blocked", Boolean(connection?.blocked));
  }
  const user = connection?.user_name || connection?.user_id || "";
  $(stateSelector).textContent = connected ? `Connected${user ? `: ${user}` : ""}` : "Not Connected";
  $(detailSelector).textContent = connected
    ? `${purpose}. Login time: ${connection.login_at || "current session"}.`
    : `Connect from Main App -> Connections before using ${purpose}.`;
}

function renderStatus(data) {
  state.status = data;
  const settings = data.settings || {};
  const funds = data.funds || data.paper_account || {};
  const pnl = data.session_pnl || {};
  const active = data.active_trade;
  const engine = data.engine || {};
  const modeState = data.mode_state || {};
  $("#session-line").textContent = data.session_id ? `Session ${data.session_id} | ${settings.mode} | Engine ${engine.running ? "running" : "stopped"}` : "Connect account, lock settings, then trade.";
  $("#mode-banner").textContent = modeState.banner || (settings.mode ? `${settings.mode} MODE ACTIVE` : "No Session");
  $("#lock-status").textContent = data.settings_locked ? engine.running ? "ENGINE ON" : "LOCKED" : "OPEN";
  $("#capital").textContent = money(funds.available);
  $("#used-margin").textContent = money(funds.used_margin);
  $("#day-pnl").textContent = money(pnl.total);
  $("#active-symbol").textContent = active?.status === "OPEN" ? `${active.symbol} ${active.side}` : "None";
  renderWatch(data.snapshots || []);
  renderBest(data.pending_signal || data.last_signal);
  renderMargin(data.pending_signal || data.last_signal, settings);
  renderActiveTrade(active);
  renderNews(data.latest_news || [], data.snapshots || [], data.latest_news_status || {});
  renderOrders(data.order_history || []);
  renderFiiDiiStatus(data.fii_dii_upload);
  renderDataSource(data);
  $("#journal-output").textContent = JSON.stringify({
    status: data.status,
    session_id: data.session_id,
    pnl,
    funds,
    export_path: data.export_path || "",
    event_blackout_blockers: data.event_blackout_blockers || [],
    data_source_status: data.data_source_status || {},
    stock_data_health: data.stock_data_health || {},
    stock_live_feed: data.stock_live_feed || {},
    profile_policy: data.profile_policy || {},
    kill_switch_report: data.kill_switch_report || {},
    engine,
  }, null, 2);
  if (data.status === "RUNNING") {
    switchStep("terminal");
    startStatusPolling();
    $("#status-message").textContent = engine.running
      ? `Engine is evaluating continuously every ${Number(engine.interval_seconds || 5).toFixed(0)} seconds.`
      : "Session is running; engine heartbeat is not active.";
  } else {
    stopStatusPolling();
  }
  applyModeLock();
}

function renderDataSource(data = {}) {
  const policy = data.data_source_status || data.data_source_policy || {};
  const stockHealth = data.stock_data_health || {};
  const liveFeed = data.stock_live_feed || {};
  const profile = data.profile_policy || data.settings?.profile_policy || {};
  const snapshots = data.snapshots || [];
  const first = snapshots[0] || {};
  const source = policy.source || first.data_source || "data_unavailable";
  const status = policy.status || first.source_status || "ERROR";
  const sourceError = policy.source_error || first.source_error || data.last_data_fetch_error || "";
  const mode = data.settings?.mode || $("#mode")?.value || "";
  const orderExecution = policy.order_execution || (mode === "REAL" ? "Real Zerodha Orders" : "Paper Simulation");
  const warning = (policy.warnings || []).join("; ") || (source === "simulated_fallback" ? "Simulated fallback data is active. This is only for testing." : "");
  const dataMode = policy.data_mode || first.data_mode || "candle_polling";
  const wsStatus = liveFeed.websocket_connected
    ? `CONNECTED (${(liveFeed.subscribed_tokens || []).length || 0} tokens)`
    : liveFeed.running
      ? `WAITING (${(liveFeed.subscribed_tokens || []).length || 0} tokens)`
      : "STOPPED";
  const badge = $("#data-source-badge");
  if (badge) {
    badge.textContent = sourceLabel(source);
    badge.className = status === "ERROR" ? "negative" : status === "WARNING" ? "neutral" : "positive";
  }
  const grid = $("#data-source-grid");
  if (grid) {
    const fields = [
      ["Market Data", sourceLabel(source)],
      ["Order Execution", orderExecution],
      ["Data Mode", dataModeLabel(dataMode)],
      ["Stock Data Health", stockHealth.status || "WAITING"],
      ["Live Tick Status", wsStatus],
      ["Source Status", status],
      ["Source Error", sourceError || "None"],
      ["Last Candle", first.last_candle_time || ""],
      ["Last Tick", liveFeed.last_tick_at || first.last_tick_time || ""],
      ["Backfill Status", (stockHealth.warnings || [])[0] || first.reason?.data_source?.backfill_status || "None"],
      ["Profile", data.settings?.strategy_profile || $("#strategy-profile")?.value || "BALANCED"],
      ["Profile Min Score", profile.minimum_entry_score ?? data.settings?.minimum_entry_score ?? "-"],
      ["Profile RVOL", profile.relative_volume_threshold ?? data.settings?.relative_volume_threshold ?? "-"],
      ["Quote Time", first.quote_timestamp || ""],
      ["Paper Fill Model", paperFillLabel(data.settings?.paper_fill_model || $("#paper-fill-model")?.value)],
      ["Real Auto Orders", data.settings?.auto_real_orders_confirmed ? "Confirmed" : "Not confirmed"],
      ["Emergency Exit", data.settings?.emergency_exit_order_type || $("#emergency-exit-order-type")?.value || "AGGRESSIVE_LIMIT"],
    ];
    grid.innerHTML = fields.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  }
  const warningTarget = $("#data-source-warning");
  if (warningTarget) {
    warningTarget.textContent = sourceError ? `Data fetch error: ${sourceError}` : warning;
  }
}

function renderUnavailableSource(message) {
  renderDataSource({
    data_source_status: {
      source: "data_unavailable",
      status: "ERROR",
      source_error: message,
      order_execution: $("#mode")?.value === "REAL" ? "Real Zerodha Orders" : "Paper Simulation",
      warnings: [],
    },
    snapshots: [],
    settings: { mode: $("#mode")?.value || "PAPER", paper_fill_model: $("#paper-fill-model")?.value, emergency_exit_order_type: $("#emergency-exit-order-type")?.value },
  });
}

function sourceLabel(source) {
  const labels = {
    zerodha_paper_data: "Zerodha Paper Data",
    zerodha_real_data: "Zerodha Real Data",
    zerodha_cached: "Zerodha Cached",
    provided: "Provided Test Data",
    provided_test_data: "Provided Test Data",
    simulated_fallback: "Simulated Fallback",
    backtest_data: "Backtest Data",
    replay_data: "Replay Data",
    simulated_backtest_data: "Simulated Backtest Data",
    data_unavailable: "Data Unavailable",
    unknown: "Unknown",
  };
  return labels[source] || source || "Data Unavailable";
}

function dataModeLabel(mode) {
  const labels = {
    websocket_tick_candles_preferred: "Websocket tick candles preferred",
    websocket_tick_candles: "Websocket tick-built candles",
    websocket_tick_candles_pending: "Websocket tick candles pending",
    candle_polling_bootstrap_or_fallback: "Historical bootstrap / polling fallback",
    candle_polling: "Candle polling",
    provided_market_data: "Provided market data",
  };
  return labels[mode] || mode || "Candle polling";
}

function paperFillLabel(value) {
  const text = String(value || "CANDLE_TOUCH_CONSERVATIVE").toUpperCase();
  if (text === "LTP_TOUCH") return "LTP touch simulation";
  return "Candle OHLC simulation";
}

function renderFiiDiiStatus(upload) {
  if (!upload) return;
  state.fiiDiiUpload = upload;
  const output = $("#intraday-fii-dii-output");
  if (!output) return;
  output.textContent = JSON.stringify({
    status: upload.status,
    valid: upload.valid,
    data_date: upload.data_date,
    fii_net: upload.fii_net,
    dii_net: upload.dii_net,
    file: upload.source_file_name,
    message: upload.message,
  }, null, 2);
}

function renderWatch(rows) {
  $("#snapshot-count").textContent = `${rows.length} snapshots`;
  $("#watch-body").innerHTML = rows.map((row) => {
    const change = row.close - row.open;
    const changeClass = change >= 0 ? "positive" : "negative";
    const entryGate = entryGateText(row);
    return `<tr>
      <td>${row.exchange}:${row.symbol}</td>
      <td>${money(row.ltp)}</td>
      <td class="${changeClass}">${money(change)}</td>
      <td>${Number(row.volume || 0).toLocaleString()}</td>
      <td>${Number(row.candles_available || 0).toLocaleString()}</td>
      <td>${String(row.last_candle_time || "").replace("T", " ").slice(0, 16)}</td>
      <td>${sourceLabel(row.data_source || "")}${row.source_status && row.source_status !== "OK" ? ` (${row.source_status})` : ""}</td>
      <td>${money(row.relative_volume)}</td>
      <td>${money(row.vwap)}</td>
      <td>${money(row.ema20)}</td>
      <td>${money(row.ema50)}</td>
      <td>${Number(row.rsi || 0).toFixed(1)}</td>
      <td>${Number(row.liquidity_score || 0).toFixed(1)}</td>
      <td>${row.trap_warning}</td>
      <td><span class="sentiment ${sentimentClass(row.news_sentiment)}">${row.news_sentiment || "Unavailable"}</span></td>
      <td>${Number(row.news_score || 0).toFixed(1)}</td>
      <td class="gate-cell">${escapeHtml(entryGate.trigger)}</td>
      <td class="${entryGate.passed ? "positive" : "muted"} gate-cell">${escapeHtml(entryGate.gate)}</td>
      <td>${Number(row.final_long_score || 0).toFixed(1)}</td>
      <td>${Number(row.final_short_score || 0).toFixed(1)}</td>
      <td>${row.selected_side}</td>
    </tr>`;
  }).join("");
}

function entryGateText(row) {
  const structure = row?.reason?.entry_structure || {};
  const breakdown = row?.reason?.score_breakdown || {};
  const longScore = Number(row.final_long_score || 0);
  const shortScore = Number(row.final_short_score || 0);
  const selected = breakdown.side === "SHORT" ? "short" : breakdown.side === "LONG" ? "long" : row.selected_side === "SHORT" ? "short" : row.selected_side === "LONG" ? "long" : longScore >= shortScore ? "long" : "short";
  const sideState = structure[selected] || {};
  const trigger = breakdown.primary_trigger || sideState.primary_trigger || "No structure";
  const blockers = breakdown.blockers || sideState.blockers || [];
  const gates = breakdown.gates || {};
  const passed = Boolean(breakdown.eligible || (Object.keys(gates).length && Object.values(gates).every(Boolean)));
  const firstBlocker = blockers[0] || "Waiting for score";
  return {
    trigger,
    gate: passed ? "PASS" : firstBlocker,
    passed,
  };
}

function sentimentClass(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("positive")) return "positive";
  if (text.includes("negative")) return "negative";
  if (text.includes("neutral")) return "neutral";
  return "muted";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderNews(rows, snapshots = [], newsStatus = {}) {
  const table = $("#latest-news");
  const snapshotNews = snapshots
    .filter((row) => row.news_sentiment && row.news_sentiment !== "Unavailable")
    .map((row) => ({
      timestamp: "",
      symbol: row.symbol,
      source: "Signal score",
      sentiment: row.news_sentiment,
      impact: Number(row.news_score || 0).toFixed(1),
      headline: `News sentiment score ${Number(row.news_score || 0).toFixed(1)}`,
    }));
  const newsRows = rows.length ? rows : snapshotNews;
  $("#news-count").textContent = `${newsRows.length} item${newsRows.length === 1 ? "" : "s"}`;
  if (!newsRows.length) {
    const message = newsStatus.message || "No live headlines returned; news score is treated as neutral.";
    table.innerHTML = `<tr><td>${escapeHtml(message)}</td></tr>`;
    return;
  }
  table.innerHTML = `<thead><tr><th>Time</th><th>Symbol</th><th>Source</th><th>Sentiment</th><th>Impact</th><th>Headline</th></tr></thead><tbody>${
    newsRows.map((row) => {
      const headline = escapeHtml(row.headline || "");
      const url = escapeHtml(row.url || "");
      return `<tr>
      <td>${escapeHtml(String(row.timestamp || "").replace("T", " ").slice(0, 19))}</td>
      <td>${escapeHtml(row.symbol || "")}</td>
      <td>${escapeHtml(row.source || "")}</td>
      <td><span class="sentiment ${sentimentClass(row.sentiment)}">${escapeHtml(row.sentiment || "Unknown")}</span></td>
      <td>${escapeHtml(row.impact || "")}</td>
      <td class="headline-cell">${url ? `<a href="${url}" target="_blank" rel="noopener">${headline}</a>` : headline}</td>
    </tr>`;
    }).join("")
  }</tbody>`;
}

function renderBacktestSummary(summary = {}) {
  const bestPossibleTrades = (summary.best_possible_trades || summary.best_signals || []).slice(0, 10).map((row) => ({
    time: row.time,
    symbol: row.symbol,
    side: row.side,
    score: row.score,
    entry: row.entry_price,
    stoploss: row.stoploss,
    target: row.target,
    decision: row.decision,
  }));
  const bestTrades = (summary.best_trades || []).slice(0, 10).map((row) => ({
    symbol: row.symbol,
    side: row.side,
    quantity: row.quantity,
    entry: row.entry_price,
    exit: row.exit_price,
    pnl_net: row.pnl_net,
    reason: row.exit_reason,
  }));
  const sessionPnl = summary.session_pnl || {};
  $("#backtest-summary").innerHTML = [
    ["Mode", "BACKTEST / REPLAY ONLY"],
    ["Replay Steps", Number(summary.replay_steps || 0).toLocaleString()],
    ["Candles", Number(summary.candle_count || 0).toLocaleString()],
    ["Data Source", summary.data_source || ""],
    ["Session P&L", money(sessionPnl.total)],
    ["Excel Export", summary.export_path || ""],
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  renderSimpleTable("#backtest-best-signals", bestPossibleTrades, ["time", "symbol", "side", "score", "entry", "stoploss", "target", "decision"]);
  renderSimpleTable("#backtest-closed-trades", bestTrades, ["symbol", "side", "quantity", "entry", "exit", "pnl_net", "reason"]);
  $("#backtest-report-json").textContent = JSON.stringify({
    replay_steps: summary.replay_steps || 0,
    candle_count: summary.candle_count || 0,
    data_source: summary.data_source || "",
    session_pnl: sessionPnl,
    export_path: summary.export_path || "",
    best_possible_trades: bestPossibleTrades,
    best_trades: bestTrades,
  }, null, 2);
  $("#mode-banner").textContent = "BACKTEST / REPLAY ONLY - NO LIVE ORDERS";
  $("#lock-status").textContent = "REPORT";
  $("#active-symbol").textContent = "None";
}

function renderBacktestError(message) {
  $("#backtest-summary").innerHTML = `<div><span>Status</span><strong>Backtest failed</strong></div><div><span>Reason</span><strong>${escapeHtml(message)}</strong></div>`;
  renderSimpleTable("#backtest-best-signals", [], ["time", "symbol", "side", "score", "entry", "stoploss", "target", "decision"]);
  renderSimpleTable("#backtest-closed-trades", [], ["symbol", "side", "quantity", "entry", "exit", "pnl_net", "reason"]);
  $("#backtest-report-json").textContent = JSON.stringify({ error: message }, null, 2);
  $("#mode-banner").textContent = "BACKTEST / REPLAY ONLY - NO LIVE ORDERS";
  $("#lock-status").textContent = "REPORT";
  $("#active-symbol").textContent = "None";
}

function renderSimpleTable(selector, rows, columns) {
  const table = $(selector);
  if (!rows.length) {
    table.innerHTML = "<tr><td>No rows generated.</td></tr>";
    return;
  }
  table.innerHTML = `<thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead><tbody>${
    rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column] ?? "")}</td>`).join("")}</tr>`).join("")
  }</tbody>`;
}

function renderBest(signal) {
  const target = $("#best-setup");
  if (!signal) {
    target.innerHTML = "<div><span>Status</span><strong>No setup selected</strong></div>";
    return;
  }
  const fields = [
    ["Stock", `${signal.exchange}:${signal.symbol}`],
    ["Direction", signal.side],
    ["Setup", signal.setup_name],
    ["Score", Number(signal.score || 0).toFixed(1)],
    ["Confidence", Number(signal.confidence || 0).toFixed(1)],
    ["Entry LIMIT", money(signal.entry_price)],
    ["Stoploss", money(signal.stoploss)],
    ["Target LIMIT", money(signal.target)],
    ["Risk Reward", Number(signal.risk_reward || 0).toFixed(2)],
    ["Decision", signal.final_decision],
    ["Blockers", (signal.blockers || []).join("; ") || "No active blockers"],
    ["Why", signal.explanation],
  ];
  target.innerHTML = fields.map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
}

function marginValue(margin, key, fallback = "0") {
  const value = margin?.[key];
  return value === undefined || value === null || value === "" ? fallback : value;
}

function renderMargin(signal, settings = {}) {
  const target = $("#margin-quantity");
  const margin = signal?.margin || {};
  const allowed = margin.allowed_margin_capital ?? margin.allowed_capital;
  const fields = [
    ["Estimated MIS Leverage", `${Number(marginValue(margin, "estimated_leverage", settings.estimated_leverage || 5)).toFixed(1)}x`],
    ["Actual Required Margin", margin.actual_required_margin === undefined || margin.actual_required_margin === null ? "Awaiting validation" : money(margin.actual_required_margin)],
    ["Margin Validation", margin.margin_validation_status || (signal ? "Awaiting approval" : "No setup selected")],
    ["Final Quantity", marginValue(margin, "final_quantity")],
    ["Risk-Based Quantity", marginValue(margin, "risk_based_quantity")],
    ["Margin-Based Quantity", marginValue(margin, "margin_based_quantity")],
    ["Allowed Capital for This Trade", allowed === undefined || allowed === null ? "0.00" : money(allowed)],
  ];
  target.innerHTML = fields.map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
}

function renderActiveTrade(trade) {
  const target = $("#active-trade");
  if (!trade || trade.status !== "OPEN") {
    target.innerHTML = "<div><span>Status</span><strong>No active trade</strong></div>";
    return;
  }
  const management = trade.management || {};
  const fields = [
    ["Symbol", `${trade.exchange}:${trade.symbol}`],
    ["Side", trade.side],
    ["Quantity", trade.quantity],
    ["Entry", money(trade.entry_price)],
    ["Initial SL", money(trade.initial_stoploss_trigger || trade.stoploss_trigger)],
    ["SL Trigger", money(trade.stoploss_trigger)],
    ["SL Limit", money(trade.stoploss_limit)],
    ["Initial Target", money(trade.initial_target || trade.target)],
    ["Target", money(trade.target)],
    ["Health", Number(management.health_score || 0).toFixed(1)],
    ["Action", management.action || "OPEN"],
    ["R Multiple", Number(management.r_multiple || 0).toFixed(2)],
    ["Unrealized P&L", money(trade.unrealized_pnl)],
    ["Last LTP", money(trade.last_ltp || trade.entry_price)],
    ["Margin Used", money(trade.margin_required)],
    ["Management Note", management.freeze_reason || management.reason || "Monitoring"],
  ];
  target.innerHTML = fields.map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
}

function renderOrders(rows) {
  const table = $("#order-history");
  if (!rows.length) {
    table.innerHTML = "<tr><td>No session orders yet.</td></tr>";
    return;
  }
  table.innerHTML = `<thead><tr><th>Time</th><th>Role</th><th>Symbol</th><th>Txn</th><th>Type</th><th>Qty</th><th>Price</th><th>Trigger</th><th>Status</th><th>Message</th></tr></thead><tbody>${
    rows.map((row) => `<tr>
      <td>${row.updated_at || row.created_at || ""}</td>
      <td>${row.role || ""}</td>
      <td>${row.symbol || ""}</td>
      <td>${row.transaction_type || ""}</td>
      <td>${row.order_type || ""}</td>
      <td>${row.quantity || ""}</td>
      <td>${money(row.price)}</td>
      <td>${row.trigger_price ? money(row.trigger_price) : ""}</td>
      <td>${row.status || ""}</td>
      <td>${row.status_message || ""}</td>
    </tr>`).join("")
  }</tbody>`;
}

async function refreshAccounts() {
  renderAccountStatus(await api("/api/intraday/account-status"));
}

async function refreshIntradayStatus() {
  renderStatus(await api("/api/intraday/status"));
}

async function updatePaperBalance(reset = true) {
  const balance = Number($("#paper-balance-input").value || state.accountStatus?.paper?.funds?.available || 0);
  if (!balance || balance <= 0) throw new Error("Enter a paper balance greater than zero.");
  await api("/api/intraday/paper-account", {
    method: "POST",
    body: JSON.stringify({ balance, reset }),
  });
  await refreshAccounts();
  await refreshIntradayStatus();
  $("#status-message").textContent = reset
    ? `Paper account reset to ${money(balance)}.`
    : `Paper available balance updated to ${money(balance)}.`;
}

function startStatusPolling() {
  if (state.statusPollTimer) return;
  state.statusPollTimer = window.setInterval(pollRunningEngine, 3000);
}

function stopStatusPolling() {
  if (!state.statusPollTimer) return;
  window.clearInterval(state.statusPollTimer);
  state.statusPollTimer = null;
}

async function pollRunningEngine() {
  if (state.statusPollBusy) return;
  state.statusPollBusy = true;
  try {
    const data = await api("/api/intraday/status");
    renderStatus(data);
    state.statusPollCount += 1;
    if (state.statusPollCount % 3 === 0) {
      await refreshAccounts();
    }
  } catch (error) {
    $("#journal-output").textContent = error.message;
  } finally {
    state.statusPollBusy = false;
  }
}

async function init() {
  try {
    const defaults = await api("/api/intraday/defaults");
    state.defaultSettings = defaults.settings || null;
  } catch (error) {
    state.defaultSettings = null;
  }
  const savedSetup = loadSavedSetup();
  if (savedSetup || state.defaultSettings) {
    applyPayloadToForm(savedSetup || state.defaultSettings);
  }
  renderSymbols();
  if (savedSetup || state.defaultSettings) {
    applyPayloadToForm(savedSetup || state.defaultSettings);
  }
  $$(".flow-step").forEach((button) => button.addEventListener("click", () => switchStep(button.dataset.step)));
  $$(".paper-workflow-tab").forEach((button) => button.addEventListener("click", () => switchPaperWorkflow(button.dataset.paperWorkflow)));
  $("#mode").addEventListener("change", updateWorkflowForMode);
  $("#save-setup").addEventListener("click", saveSetup);
  $("#reset-setup").addEventListener("click", resetSetupDefaults);
  updateWorkflowForMode();
  await refreshAccounts();
  await refreshIntradayStatus();

  $("#intraday-fii-dii-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const form = new FormData(event.currentTarget);
      const data = await apiForm("/api/intraday/upload-fii-dii", form);
      renderFiiDiiStatus(data.fii_dii_upload);
      $("#status-message").textContent = data.fii_dii_upload.valid
        ? "FII/DII CSV uploaded. PAPER and REAL session start is now allowed."
        : data.fii_dii_upload.message;
    } catch (error) {
      $("#intraday-fii-dii-output").textContent = error.message;
    }
  });

  $("#save-paper-balance").addEventListener("click", async () => {
    try {
      await updatePaperBalance(false);
    } catch (error) {
      $("#status-message").textContent = error.message;
      renderUnavailableSource(error.message);
    }
  });

  $("#reset-paper-balance").addEventListener("click", async () => {
    try {
      await updatePaperBalance(true);
    } catch (error) {
      $("#status-message").textContent = error.message;
    }
  });

  $("#start-session").addEventListener("click", async () => {
    try {
      const data = await api("/api/intraday/start", { method: "POST", body: JSON.stringify(payloadFromForm()) });
      renderStatus(data);
      startStatusPolling();
      await refreshAccounts();
    } catch (error) {
      $("#status-message").textContent = error.message;
    }
  });

  $("#run-backtest").addEventListener("click", async () => {
    const button = $("#run-backtest");
    try {
      button.disabled = true;
      button.textContent = "Running Backtest...";
      $("#status-message").textContent = "Running isolated backtest / replay report. No live orders will be placed.";
      $("#backtest-report-json").textContent = "Running backtest / replay report...";
      const payload = { ...payloadFromForm(), backtest_date: $("#backtest-date").value || new Date().toISOString().slice(0, 10) };
      const data = await api("/api/intraday/paper-backtest", { method: "POST", body: JSON.stringify(payload) });
      const accountNote = data.summary?.paper_balance_unchanged ? " Paper account unchanged." : "";
      const source = data.summary?.data_source || "unknown";
      $("#status-message").textContent = `Backtest complete. Replayed ${data.summary?.replay_steps || 0} candles. Data source: ${source}.${accountNote} Export: ${data.summary.export_path}`;
      renderBacktestSummary(data.summary || {});
      switchStep("backtest");
      stopStatusPolling();
      await refreshAccounts();
    } catch (error) {
      $("#status-message").textContent = error.message;
      renderBacktestError(error.message);
      switchStep("backtest");
    } finally {
      button.disabled = false;
      button.textContent = "Run Backtest / Replay";
    }
  });

  $("#evaluate").addEventListener("click", async () => {
    try {
      renderStatus(await api("/api/intraday/evaluate", { method: "POST", body: JSON.stringify({ market_trend: "Bullish" }) }));
      startStatusPolling();
    } catch (error) {
      $("#journal-output").textContent = error.message;
      renderUnavailableSource(error.message);
    }
  });

  $("#approve-entry").addEventListener("click", async () => {
    try {
      renderStatus(await api("/api/intraday/approve", { method: "POST", body: "{}" }));
    } catch (error) {
      $("#journal-output").textContent = error.message;
    }
  });

  $("#reject-entry").addEventListener("click", async () => {
    try {
      renderStatus(await api("/api/intraday/reject", { method: "POST", body: JSON.stringify({ reason: "Rejected from terminal" }) }));
    } catch (error) {
      $("#journal-output").textContent = error.message;
    }
  });

  $("#process-orders").addEventListener("click", async () => {
    try {
      renderStatus(await api("/api/intraday/process-orders", { method: "POST", body: JSON.stringify({}) }));
      await refreshAccounts();
    } catch (error) {
      $("#journal-output").textContent = error.message;
    }
  });

  $("#force-timeout").addEventListener("click", async () => {
    try {
      renderStatus(await api("/api/intraday/process-orders", { method: "POST", body: JSON.stringify({ force_entry_timeout: true }) }));
      await refreshAccounts();
    } catch (error) {
      $("#journal-output").textContent = error.message;
    }
  });

  $("#kill-switch").addEventListener("click", async () => {
    stopStatusPolling();
    renderStatus(await api("/api/intraday/kill-switch", { method: "POST", body: "{}" }));
  });

  $("#stop-session").addEventListener("click", async () => {
    stopStatusPolling();
    renderStatus(await api("/api/intraday/stop", { method: "POST", body: "{}" }));
    await refreshAccounts();
  });

  window.addEventListener("beforeunload", stopStatusPolling);
  const today = new Date().toISOString().slice(0, 10);
  $("#backtest-date").value = today;
}

init().catch((error) => {
  $("#status-message").textContent = error.message;
});
