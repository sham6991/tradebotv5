const state = {
  settings: {},
  labels: {},
  activeSettingsProfile: "backtest",
  defaults: {},
  marketCueRaw: null,
  marketCueAnalysis: null,
  marketCueUploadedFlow: null,
  marketCueOverrides: [],
  lastStatus: null,
  redirectUrl: "",
  refreshBusy: false,
  lastRefreshOkAt: 0,
};

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

function setText(selector, value) {
  const node = typeof selector === "string" ? $(selector) : selector;
  if (!node) return;
  const next = String(value ?? "");
  if (node.textContent !== next) node.textContent = next;
}

function setPill(selector, label, tone = "muted") {
  const node = typeof selector === "string" ? $(selector) : selector;
  if (!node) return;
  setText(node, label);
  node.className = `status-pill status-${tone}`;
}

function isNearBottom(node, threshold = 40) {
  if (!node) return true;
  return node.scrollHeight - node.scrollTop - node.clientHeight < threshold;
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

const titles = {
  dashboard: "Dashboard",
  "market-cue": "Indian Market Cue Analyzer",
  backtest: "Backtest Mode",
  paper: "Virtual/Paper Trading Desk",
  live: "Zerodha Live Trading",
  replay: "Session Replay",
  zerodha: "Connections",
};

const settingOrder = [
  "balance", "lot_size", "max_trades", "profit_points", "safety_points", "stoploss_limit_buffer_points",
  "live_option_market_entry_as_limit_enabled", "live_option_market_entry_limit_buffer_points",
  "trailing_sl_enabled", "trailing_start_points", "trailing_step_points", "trailing_lock_points",
  "time_exit", "cooldown", "chart_interval", "trend_set", "bullish_threshold", "bearish_threshold",
  "rsi_bull", "rsi_bear", "rsi_reversal_bullish", "rsi_reversal_bearish",
  "bullish_reversal_condition", "bearish_reversal_condition",
  "fast_ohlcv_entry_enabled", "buy_limit_score_low", "market_entry_score",
  "minimum_body_percent", "minimum_close_position", "market_entry_minimum_body_percent",
  "market_entry_minimum_close_position", "trigger_upper_wick_max", "hard_rejection_upper_wick_max",
  "volume_previous_multiplier", "avg_volume_minimum_multiplier", "volume_pickup_avg_multiplier",
  "large_candle_multiplier", "move_from_low_max_multiplier", "gap_spike_multiplier",
  "buy_limit_offset_multiplier", "minimum_offset", "maximum_offset", "buy_limit_validity_seconds",
  "backtest_limit_fill_mode", "enable_chop_filter", "chop_lookback_candles", "chop_overlap_count",
  "aggressive_live_entry_enabled", "aggressive_setup_score", "aggressive_entry_score",
  "aggressive_upper_wick_max", "aggressive_minimum_body_percent", "aggressive_minimum_close_position",
  "aggressive_move_from_low_max_multiplier", "one_entry_attempt_per_candle",
  "missed_limit_cooldown_candles", "max_spread_points",
  "max_daily_loss", "max_daily_profit", "max_consecutive_losses", "square_off_time", "order_product",
];

const settingGroups = [
  {
    id: "nifty",
    label: "Nifty",
    keys: [
      "trend_set",
      "bullish_threshold", "bearish_threshold",
      "rsi_bull", "rsi_bear",
      "rsi_reversal_bullish", "rsi_reversal_bearish",
      "bullish_reversal_condition", "bearish_reversal_condition",
    ],
  },
  {
    id: "trading",
    label: "Trading",
    keys: [
      "fast_ohlcv_entry_enabled",
      "buy_limit_score_low", "market_entry_score",
      "minimum_body_percent", "minimum_close_position",
      "market_entry_minimum_body_percent", "market_entry_minimum_close_position",
      "trigger_upper_wick_max", "hard_rejection_upper_wick_max",
      "volume_previous_multiplier", "avg_volume_minimum_multiplier", "volume_pickup_avg_multiplier",
      "large_candle_multiplier", "move_from_low_max_multiplier", "gap_spike_multiplier",
      "buy_limit_offset_multiplier", "minimum_offset", "maximum_offset",
      "buy_limit_validity_seconds", "backtest_limit_fill_mode",
      "enable_chop_filter", "chop_lookback_candles", "chop_overlap_count",
      "aggressive_live_entry_enabled", "aggressive_setup_score", "aggressive_entry_score",
      "aggressive_upper_wick_max", "aggressive_minimum_body_percent",
      "aggressive_minimum_close_position", "aggressive_move_from_low_max_multiplier",
      "one_entry_attempt_per_candle", "missed_limit_cooldown_candles", "max_spread_points",
    ],
  },
  {
    id: "market",
    label: "Market",
    keys: [
      "balance", "lot_size", "max_trades",
      "profit_points", "safety_points", "stoploss_limit_buffer_points",
      "live_option_market_entry_as_limit_enabled", "live_option_market_entry_limit_buffer_points",
      "trailing_sl_enabled", "trailing_start_points", "trailing_step_points", "trailing_lock_points",
      "time_exit", "cooldown", "chart_interval",
      "max_daily_loss", "max_daily_profit", "max_consecutive_losses",
      "square_off_time", "order_product",
    ],
  },
];

const liveProfileHiddenSettings = new Set(["chart_interval"]);
const booleanSettings = new Set([
  "fast_ohlcv_entry_enabled",
  "enable_chop_filter",
  "aggressive_live_entry_enabled",
  "one_entry_attempt_per_candle",
  "trailing_sl_enabled",
  "live_option_market_entry_as_limit_enabled",
]);

function isEnabledValue(value) {
  return ["1", "true", "yes", "on", "enabled"].includes(String(value ?? "").trim().toLowerCase());
}

function $(selector, root = document) {
  return root.querySelector(selector);
}

function $all(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

async function api(path, payload) {
  let options = {};
  if (payload !== undefined) {
    options = payload instanceof FormData
      ? { method: "POST", body: payload }
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        };
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.remove("show"), 3600);
}

function showSettingsError(message) {
  const node = $("#settings-error");
  if (!node) return;
  node.textContent = message || "";
  node.hidden = !message;
}

function text(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function renderTickTable(table, rows) {
  const wrap = table?.closest(".tick-scroll");
  const nearBottom = isNearBottom(wrap);
  renderTable(table, (rows || []).slice(-80), ["time", "name", "token", "ltp", "volume"]);
  if (wrap && nearBottom) wrap.scrollTop = wrap.scrollHeight;
}

const labelOverrides = {
  ltp: "LTP",
  pnl: "PnL",
  "pnl_percent": "PnL %",
  "ticks_per_second": "Ticks/s",
  nifty: "NIFTY",
  ce: "CE",
  pe: "PE",
  id: "ID",
  api: "API",
  url: "URL",
};

function humanLabel(value) {
  const raw = String(value || "");
  const key = raw.toLowerCase();
  if (labelOverrides[key]) return labelOverrides[key];
  return raw
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, letter => letter.toUpperCase())
    .replace(/\bNifty\b/g, "NIFTY")
    .replace(/\bCe\b/g, "CE")
    .replace(/\bPe\b/g, "PE")
    .replace(/\bLtp\b/g, "LTP")
    .replace(/\bPnl\b/g, "PnL")
    .replace(/\bId\b/g, "ID")
    .replace(/\bUrl\b/g, "URL");
}

function humanText(value) {
  if (value === null || value === undefined || value === "") return "";
  return String(value)
    .replace(/[_-]+/g, " ")
    .replace(/\b[a-z]/g, letter => letter.toUpperCase());
}

function money(value) {
  if (value === null || value === undefined || value === "") return "Not Connected";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
}

function setMoneyTone(node, value) {
  if (!node) return;
  const number = Number(value || 0);
  node.classList.remove("money-positive", "money-negative", "money-zero");
  if (number > 0) node.classList.add("money-positive");
  else if (number < 0) node.classList.add("money-negative");
  else node.classList.add("money-zero");
}

function setMoneyText(selector, value, tone = false) {
  const node = $(selector);
  if (!node) return;
  node.textContent = money(value ?? 0);
  if (tone) setMoneyTone(node, value);
}

function shortTime(value) {
  return value ? String(value) : "";
}

function recentTimestamp(value, maxAgeMs = 5 * 60 * 1000) {
  if (!value) return false;
  const parsed = Date.parse(String(value).replace(" ", "T"));
  if (!Number.isFinite(parsed)) return false;
  return Date.now() - parsed <= maxAgeMs;
}

function hasFailedStep(rows) {
  return (rows || []).some(row => String(row?.status || "").trim().toUpperCase() === "FAILED");
}

function setCard(selector, title, detail = "") {
  const card = typeof selector === "string" ? $(selector) : selector;
  if (!card) return;
  const strong = $("strong", card);
  const small = $("small", card);
  if (strong) strong.textContent = title || "-";
  if (small) small.textContent = detail || "";
}

function renderReadiness(target, items) {
  const node = typeof target === "string" ? $(target) : target;
  if (!node) return;
  node.textContent = "";
  items.forEach(item => {
    const row = document.createElement("div");
    row.className = `readiness-item readiness-${item.state}`;
    const title = document.createElement("strong");
    title.textContent = item.label;
    const detail = document.createElement("span");
    detail.textContent = item.detail;
    row.append(title, detail);
    node.appendChild(row);
  });
}

function percentText(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(2)}%` : "-";
}

function renderMarketCue(result) {
  const scoring = result?.scoring || {};
  const raw = result?.raw_data || state.marketCueRaw || {};
  const validation = result?.validated_data || {};
  const flow = raw.institutional_flow || {};
  $("#cue-bias").textContent = scoring.bias || "Not Analyzed";
  $("#cue-confidence").textContent = scoring.confidence !== undefined ? `${scoring.confidence}%` : "-";
  $("#cue-risk").textContent = scoring.risk_level || "-";
  $("#cue-score").textContent = scoring.final_score ?? "-";
  $("#cue-reliability").textContent = validation.data_reliability || "-";
  $("#cue-fii").textContent = money(flow.fii_net);
  $("#cue-dii").textContent = money(flow.dii_net);
  setMoneyTone($("#cue-fii"), flow.fii_net);
  setMoneyTone($("#cue-dii"), flow.dii_net);
  $("#cue-flow-date").textContent = flow.data_date || "-";
  $("#cue-flow-source").textContent = `${flow.source || "NSE"} ${flow.scope || ""}`.trim();
  $("#cue-flow-status").textContent = flow.status || "-";
  $("#cue-flow-mode").textContent = flow.fetch_mode || "-";

  const sourceRows = [];
  Object.values(raw.indian_market || {}).forEach(row => sourceRows.push({
    cue_name: row.name,
    source: row.source,
    value: row.value,
    percent_change: percentText(row.percent_change),
    last_updated: row.timestamp,
    status: row.status,
    warning: row.warning || "",
  }));
  Object.values(raw.global_market || {}).forEach(row => sourceRows.push({
    cue_name: row.name,
    source: row.source,
    value: row.value,
    percent_change: percentText(row.percent_change),
    last_updated: row.timestamp,
    status: row.status,
    warning: row.warning || "",
  }));
  if (flow.source) {
    sourceRows.push({
      cue_name: "FII/DII",
      source: flow.source,
      value: `FII ${flow.fii_net ?? "-"} / DII ${flow.dii_net ?? "-"}`,
      percent_change: "",
      last_updated: flow.data_date || "",
      status: flow.status || "",
      warning: (flow.warnings || []).join("; "),
    });
  }
  renderTable("#cue-source-table", sourceRows, ["cue_name", "source", "value", "percent_change", "last_updated", "status", "warning"]);
  renderTable("#cue-score-table", scoring.contributions || [], ["category", "name", "score", "value"]);
  $("#cue-report-output").textContent = result?.report_text || result?.report?.report_text || JSON.stringify(raw.source_status || {}, null, 2);
  setMarketCueActions();
}

function renderMarketCueHistory(data) {
  const rows = (data?.reports || []).map(row => ({
    id: row.id,
    created_at: row.created_at,
    bias: row.bias,
    score: row.final_score,
    confidence: row.confidence,
    risk_level: row.risk_level,
    nifty: row.nifty_ltp,
    banknifty: row.banknifty_ltp,
    fii: row.fii_value,
    dii: row.dii_value,
    reliability: row.data_reliability,
  }));
  renderTable("#cue-history-table", rows);
  const status = $("#cue-history-status");
  if (status) status.textContent = rows.length ? `${rows.length} saved reports shown. Maximum history is 60 reports.` : "No saved reports yet.";
}

function setMarketCueActions() {
  const save = $("#cue-save");
  const analyze = $("#cue-analyze");
  if (save) save.disabled = !state.marketCueAnalysis;
  if (analyze) analyze.disabled = !(state.marketCueRaw && state.marketCueUploadedFlow);
}

function accountBalanceText(mode, margin, connection) {
  if (mode === "PAPER") return money(margin?.available ?? 0);
  if (!connection?.connected) return connectionText(connection);
  if (margin?.error) return margin.error;
  return money(margin?.available);
}

function milliseconds(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${number.toFixed(2)} ms`;
}

function renderTable(target, rows, columns = null) {
  const table = typeof target === "string" ? $(target) : target;
  if (!table) return;
  const normalized = Array.isArray(rows) ? rows : [];
  const capped = normalized.slice(-tableLimit(table));
  const cols = columns || Array.from(new Set(normalized.flatMap(row => Object.keys(row || {}))));
  if (!shouldRender(`table:${table.id || table.dataset.field || "anon"}`, { rows: capped, cols })) return;
  const wrap = table.closest(".table-wrap");
  const scrollTop = wrap ? wrap.scrollTop : 0;
  const nearBottom = isNearBottom(wrap);
  table.textContent = "";
  table.style.minWidth = `${Math.max(760, cols.length * 128)}px`;
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  cols.forEach(col => {
    const th = document.createElement("th");
    th.textContent = humanLabel(col);
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  const tbody = document.createElement("tbody");
  if (!capped.length) {
    const row = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = Math.max(cols.length, 1);
    td.textContent = "No rows";
    row.appendChild(td);
    tbody.appendChild(row);
  } else {
    capped.forEach(item => {
      const row = document.createElement("tr");
      cols.forEach(col => {
        const td = document.createElement("td");
        td.textContent = text(item?.[col]);
        row.appendChild(td);
      });
      tbody.appendChild(row);
    });
  }
  table.append(thead, tbody);
  if (wrap) wrap.scrollTop = nearBottom ? wrap.scrollHeight : scrollTop;
}

function tableLimit(table) {
  const key = String(table?.id || table?.dataset?.field || "").toLowerCase();
  if (key.includes("tick")) return 80;
  if (key.includes("order")) return 50;
  if (key.includes("log") || key.includes("event")) return 100;
  return 100;
}

function renderNetworkHealth(networkHealth) {
  const selector = $("#network-health-mode");
  if (!selector) return;
  const mode = selector.value || "PAPER";
  const health = networkHealth?.[mode] || {};
  $("#network-health-status").textContent = health.status || "Not Run";
  $("#network-health-quality").textContent = health.quality || "Unknown";
  $("#network-health-time").textContent = health.checked_at || "";
  $("#network-health-message").textContent = health.message || "Run before starting feed/trading.";
  $("#network-health-total").textContent = milliseconds(health.total_ms);
  $("#network-health-active-mode").textContent = health.mode || mode;
  renderTable("#network-health-steps", health.steps || [], ["name", "status", "duration_ms", "error"]);
}

function renderRecoveryStatus(recoveryStatus) {
  const selector = $("#recovery-mode");
  if (!selector) return;
  const mode = selector.value || "PAPER";
  const recovery = recoveryStatus?.[mode] || {};
  $("#recovery-status").textContent = recovery.status || "Not Checked";
  $("#recovery-severity").textContent = recovery.severity || "Unknown";
  $("#recovery-time").textContent = recovery.checked_at || "";
  $("#recovery-summary").textContent = recovery.summary || "Run before restarting after a lost session.";
  $("#recovery-recommendation").textContent = recovery.recommendation || "Run Check";
  $("#recovery-active-mode").textContent = recovery.mode || mode;
  renderTable("#recovery-checks", recovery.checks || [], ["name", "status", "detail"]);
  renderTable("#recovery-files", recovery.files || [], ["name", "exists", "updated_at", "path"]);
  renderTable("#recovery-findings", recovery.findings || [], ["level", "code", "message", "order_id", "status"]);
}

function currentSettings(profile) {
  const values = { ...(state.defaults || {}), ...(state.settings[profile] || {}) };
  Object.entries(state.defaults || {}).forEach(([key, fallback]) => {
    if (values[key] === null || values[key] === undefined || String(values[key]).trim() === "") {
      values[key] = fallback;
    }
  });
  return values;
}

function collectSettingsFromDialog() {
  const values = {};
  $all("[data-setting-key]", $("#settings-fields")).forEach(input => {
    values[input.dataset.settingKey] = input.type === "checkbox" ? String(input.checked) : input.value.trim();
  });
  return values;
}

function openSettings(profile) {
  state.activeSettingsProfile = profile;
  $("#settings-title").textContent = `${profile[0].toUpperCase()}${profile.slice(1)} Risk Settings`;
  showSettingsError("");
  const tabs = $("#settings-tabs");
  const fields = $("#settings-fields");
  tabs.textContent = "";
  fields.textContent = "";
  const values = currentSettings(profile);
  const rendered = new Set();

  const makeSettingField = key => {
    if ((profile === "paper" || profile === "real") && liveProfileHiddenSettings.has(key)) return;
    const label = document.createElement("label");
    label.textContent = state.labels[key] || key;
    const input = key === "chart_interval" || key === "trend_set" || key === "order_product" || key === "backtest_limit_fill_mode" || booleanSettings.has(key)
      ? document.createElement("select")
      : document.createElement("input");
    input.dataset.settingKey = key;
    if (key === "chart_interval") {
      ["1 min", "2 min", "3 min", "5 min"].forEach(option => input.add(new Option(option, option)));
    }
    if (key === "trend_set") {
      ["Auto", "Bullish", "Bearish"].forEach(option => input.add(new Option(option, option)));
    }
    if (key === "order_product") {
      ["NRML", "MIS"].forEach(option => input.add(new Option(option, option)));
    }
    if (key === "backtest_limit_fill_mode") {
      ["CONSERVATIVE", "SIMPLE", "STRICT"].forEach(option => input.add(new Option(option, option)));
    }
    if (booleanSettings.has(key) && input.tagName === "SELECT") {
      input.add(new Option("Enabled", "Enabled"));
      input.add(new Option("Disabled", "Disabled"));
      input.value = isEnabledValue(values[key]) ? "Enabled" : "Disabled";
    } else {
      input.value = values[key] ?? "";
    }
    label.appendChild(input);
    rendered.add(key);
    return label;
  };

  settingGroups.forEach((group, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `settings-tab${index === 0 ? " active" : ""}`;
    button.dataset.settingsTab = group.id;
    button.textContent = group.label;
    tabs.appendChild(button);

    const panel = document.createElement("section");
    panel.className = `settings-panel${index === 0 ? " active" : ""}`;
    panel.dataset.settingsPanel = group.id;
    const grid = document.createElement("div");
    grid.className = "settings-grid";
    group.keys.forEach(key => {
      const field = makeSettingField(key);
      if (field) grid.appendChild(field);
    });
    panel.appendChild(grid);
    fields.appendChild(panel);
  });

  settingOrder.forEach(key => {
    if (rendered.has(key)) return;
    const field = makeSettingField(key);
    if (!field) return;
    let fallbackPanel = $("[data-settings-panel='market']", fields);
    if (!fallbackPanel) fallbackPanel = fields.firstElementChild;
    $(".settings-grid", fallbackPanel).appendChild(field);
  });
  $("#settings-dialog").showModal();
}

async function loadSettings() {
  const data = await api("/api/settings");
  state.settings = data.profiles;
  state.labels = data.labels;
  state.defaults = { ...(data.defaults || data.profiles?.backtest || {}) };
}

function updateBacktestSourceView() {
  const form = $("#backtest-form");
  if (!form) return;
  const source = form.elements.data_source?.value || "manual";
  $all("[data-backtest-source]", form).forEach(node => {
    node.hidden = node.dataset.backtestSource !== source;
  });
  $all("[data-backtest-manual]", form).forEach(node => {
    node.hidden = source !== "manual";
  });
}

function buildLiveViews() {
  $all(".live-view").forEach(view => {
    const mode = view.dataset.mode;
    const profile = mode === "LIVE" ? "real" : "paper";
    $(".live-layout", view).innerHTML = `
      <section class="panel live-contracts">
        <h2>${mode === "LIVE" ? "Real Trading" : "Virtual/Paper Trading"} Contracts</h2>
        <div class="live-account-card">
          <span>${mode === "LIVE" ? "Available Margin" : "Virtual/Paper Balance"}</span>
          <strong data-field="account_balance">Not Connected</strong>
          <small data-field="account_time"></small>
          <div class="account-actions">
            <button type="button" data-action="refresh-margin">Refresh Balance</button>
            <button type="button" data-action="disconnect">Disconnect</button>
          </div>
        </div>
        <label>NIFTY Token <input data-field="nifty_token"></label>
        <button type="button" data-action="fetch-nifty">Fetch NIFTY</button>
        <span></span><span></span><span></span><span></span>
        ${["CALL", "PUT"].map((name, index) => `
          <label>${name === "CALL" ? "Call" : "Put"} Type
            <select data-option="${index}" data-field="option_type"><option>${name === "CALL" ? "CE" : "PE"}</option><option>${name === "CALL" ? "PE" : "CE"}</option></select>
          </label>
          <label>${name === "CALL" ? "Call" : "Put"} Strike <input data-option="${index}" data-field="strike"></label>
          <label>${name === "CALL" ? "Call" : "Put"} Expiry <input type="date" data-option="${index}" data-field="expiry"></label>
          <label>${name === "CALL" ? "Call" : "Put"} Trading Symbol <input data-option="${index}" data-field="tradingsymbol"></label>
          <label>${name === "CALL" ? "Call" : "Put"} Token <input data-option="${index}" data-field="token"></label>
          <button type="button" data-option="${index}" data-action="fetch-option">Fetch</button>
        `).join("")}
        <div class="live-actions">
          <button type="button" data-settings="${profile}">Risk Settings</button>
          <label>History Days <input data-field="history_days" value="5"></label>
          <label>Interval
            <select data-field="history_interval"><option>1 min</option><option>2 min</option><option selected>3 min</option><option>5 min</option></select>
          </label>
          <button type="button" data-action="start-feed">Start Feed</button>
          <button type="button" data-action="start-live" class="${mode === "LIVE" ? "danger" : "primary"}">${mode === "LIVE" ? "Start Real Trading" : "Start Paper"}</button>
          <button type="button" data-action="stop">Stop</button>
          <button type="button" data-action="square-off">Square Off</button>
          <button type="button" data-action="kill-switch" class="danger">Kill Switch</button>
          <small class="live-action-note" data-field="action_note">Connect and load contracts before starting.</small>
        </div>
      </section>
      <section class="panel live-status-panel">
        <h2>${mode} Status</h2>
        <div class="live-status-alert" data-field="status_alert" hidden></div>
        <div class="account-grid live-status-grid">
          <div><span>Connection</span><strong data-field="connection_status">Not Connected</strong><small data-field="connection_detail"></small></div>
          <div><span>Margin</span><strong data-field="margin_status">-</strong><small data-field="margin_detail"></small></div>
          <div><span>Feed</span><strong data-field="feed_status">Stopped</strong><small data-field="feed_detail"></small></div>
          <div><span>Session</span><strong data-field="session_status">Idle</strong><small data-field="session_detail"></small></div>
          <div><span>Network</span><strong data-field="network_status">Not Run</strong><small data-field="network_detail"></small></div>
          <div><span>Recovery</span><strong data-field="recovery_status">Not Checked</strong><small data-field="recovery_detail"></small></div>
        </div>
        <div class="readiness-strip" data-field="readiness_strip"></div>
        <details class="raw-details">
          <summary>Raw status details</summary>
          <pre data-field="mode_status">Waiting for connection.</pre>
        </details>
      </section>
      <section class="panel tick-tabs">
        <h2>Ticks</h2>
        <div class="tick-rate-strip">
          <div><span>NIFTY 50</span><strong data-rate="NIFTY">0/s</strong></div>
          <div><span>CE</span><strong data-rate="CE">0/s</strong></div>
          <div><span>PE</span><strong data-rate="PE">0/s</strong></div>
        </div>
        <div class="tab-buttons">
          <button type="button" class="tick-tab-button active" data-tick-tab="NIFTY">NIFTY 50</button>
          <button type="button" class="tick-tab-button" data-tick-tab="CE">CE</button>
          <button type="button" class="tick-tab-button" data-tick-tab="PE">PE</button>
        </div>
        <div class="tick-table active" data-tick-panel="NIFTY"><div class="table-wrap tick-scroll"><table data-field="ticks-NIFTY"></table></div></div>
        <div class="tick-table" data-tick-panel="CE"><div class="table-wrap tick-scroll"><table data-field="ticks-CE"></table></div></div>
        <div class="tick-table" data-tick-panel="PE"><div class="table-wrap tick-scroll"><table data-field="ticks-PE"></table></div></div>
        <div class="candle-actions">
          <button type="button" data-action="open-candles" data-candles="NIFTY">NIFTY Candles</button>
          <button type="button" data-action="open-candles" data-candles="CE">CE Candles</button>
          <button type="button" data-action="open-candles" data-candles="PE">PE Candles</button>
        </div>
      </section>
      <section class="panel">
        <h2>Order History</h2>
        <div class="table-wrap"><table data-field="orders"></table></div>
      </section>
    `;
  });
}

function livePayloadReady(view) {
  const payload = collectLivePayload(view);
  return Boolean(
    payload.nifty_token &&
    payload.options.every(option => option.tradingsymbol && option.token)
  );
}

function liveReadiness(data, view) {
  const mode = view.dataset.mode;
  const connection = data.connections?.[mode] || {};
  const margin = data.account_margins?.[mode] || {};
  const network = data.network_health?.[mode] || {};
  const recovery = data.recovery_status?.[mode] || {};
  const tokensReady = livePayloadReady(view);
  const connected = Boolean(connection.connected);
  const networkReady = mode !== "LIVE" || (
    recentTimestamp(network.checked_at) &&
    String(network.status || "").trim().toUpperCase() === "CONNECTED" &&
    !hasFailedStep(network.steps)
  );
  const recoveryReady = mode !== "LIVE" || (
    recentTimestamp(recovery.checked_at) &&
    String(recovery.severity || "").trim().toUpperCase() === "GOOD" &&
    String(recovery.status || "").trim().toUpperCase() === "SAFE TO TRADE"
  );
  const marginReady = mode !== "LIVE" || (
    Number(margin.available) > 0 &&
    !String(margin.error || "").trim()
  );
  const feedReady = connected && tokensReady;
  const startReady = feedReady && networkReady && recoveryReady && marginReady;
  const items = [
    {
      label: "Connection",
      state: connected ? "ok" : "bad",
      detail: connected ? connectionText(connection) : "Connect this mode first",
    },
    {
      label: "Contracts",
      state: tokensReady ? "ok" : "bad",
      detail: tokensReady ? "NIFTY, CE, and PE ready" : "Fetch or enter tokens",
    },
  ];
  if (mode === "LIVE") {
    items.push(
      {
        label: "Network",
        state: networkReady ? "ok" : "bad",
        detail: networkReady ? `${network.quality || "Good"} at ${shortTime(network.checked_at)}` : "Run fresh LIVE health check",
      },
      {
        label: "Recovery",
        state: recoveryReady ? "ok" : "bad",
        detail: recoveryReady ? recovery.status || "Safe To Trade" : "Run fresh LIVE recovery check",
      },
      {
        label: "Margin",
        state: marginReady ? "ok" : "bad",
        detail: marginReady ? money(margin.available) : "Fresh LIVE margin required",
      }
    );
  }
  return { connected, tokensReady, feedReady, startReady, items };
}

function renderLiveStatus(data, view) {
  const mode = view.dataset.mode;
  const connection = data.connections?.[mode] || {};
  const margin = data.account_margins?.[mode] || {};
  const feed = data.feed || {};
  const session = data.session_summary || {};
  const network = data.network_health?.[mode] || {};
  const recovery = data.recovery_status?.[mode] || {};
  const readiness = liveReadiness(data, view);

  setCard($(`[data-field="connection_status"]`, view)?.parentElement, connectionText(connection), connection.login_at || connection.label || "");
  setCard($(`[data-field="margin_status"]`, view)?.parentElement, accountBalanceText(mode, margin, connection), margin.updated_at || margin.error || "");
  setCard($(`[data-field="feed_status"]`, view)?.parentElement, humanText(feed.feed_status || "stopped"), feed.last_feed_event || feed.last_tick_received_at || "");
  const sessionLabel = session.mode === mode ? (session.session_status || (session.session_id ? "trading started" : "Idle")) : "Idle";
  setCard($(`[data-field="session_status"]`, view)?.parentElement, sessionLabel, `PnL ${money(session.session_pnl ?? 0)} | Trades ${session.session_trade_count || 0}`);
  setCard($(`[data-field="network_status"]`, view)?.parentElement, network.status || "Not Run", network.message || shortTime(network.checked_at));
  setCard($(`[data-field="recovery_status"]`, view)?.parentElement, recovery.status || "Not Checked", recovery.summary || shortTime(recovery.checked_at));
  renderReadiness($(`[data-field="readiness_strip"]`, view), readiness.items);

  const alertNode = $(`[data-field="status_alert"]`, view);
  const status = data.status || "";
  const latestAlert = (data.alerts || []).slice(-1)[0];
  const alertText = latestAlert?.message || latestAlert?.reason || "";
  const shouldShowStatus = data.current_mode === mode && /(failed|blocked|error|kill switch)/i.test(status);
  const message = shouldShowStatus ? status : alertText;
  if (alertNode) {
    alertNode.textContent = message || "";
    alertNode.hidden = !message;
  }

  const startFeed = $(`[data-action="start-feed"]`, view);
  const startLive = $(`[data-action="start-live"]`, view);
  const stop = $(`[data-action="stop"]`, view);
  const squareOff = $(`[data-action="square-off"]`, view);
  const killSwitch = $(`[data-action="kill-switch"]`, view);
  if (startFeed) startFeed.disabled = !readiness.feedReady;
  if (startLive) startLive.disabled = !readiness.startReady;
  const feedActive = data.current_mode === mode && String(feed.feed_status || "").toLowerCase() !== "stopped";
  const hasOrders = Boolean((data.active_orders || []).length || Object.keys(data.live_trade || {}).length);
  if (stop) stop.disabled = !feedActive;
  if (squareOff) squareOff.disabled = !hasOrders;
  if (killSwitch) killSwitch.disabled = !(readiness.connected || feedActive || hasOrders);
  const note = $(`[data-field="action_note"]`, view);
  if (note) {
    if (!readiness.connected) note.textContent = `Connect ${mode} before starting feed or trading.`;
    else if (!readiness.tokensReady) note.textContent = "Fetch or enter NIFTY, CE, and PE contracts before starting.";
    else if (!readiness.startReady && mode === "LIVE") note.textContent = "Run fresh LIVE health and recovery checks before real-money start.";
    else note.textContent = mode === "LIVE" ? "LIVE readiness complete." : "Paper desk is ready to start.";
  }
}

function collectLivePayload(view) {
  const optionRows = [0, 1].map(index => {
    const find = field => $(`[data-option="${index}"][data-field="${field}"]`, view)?.value.trim();
    return {
      option_type: find("option_type"),
      strike: find("strike"),
      expiry: find("expiry"),
      tradingsymbol: find("tradingsymbol"),
      token: find("token"),
    };
  });
  const mode = view.dataset.mode;
  return {
    mode,
    nifty_token: $(`[data-field="nifty_token"]`, view).value.trim(),
    history_days: $(`[data-field="history_days"]`, view).value.trim(),
    history_interval: $(`[data-field="history_interval"]`, view).value,
    options: optionRows,
    settings: currentSettings(mode === "LIVE" ? "real" : "paper"),
  };
}

function collectZerodhaBacktestPayload(form) {
  return {
    mode: form.elements.mode.value,
    nifty_token: form.elements.nifty_token.value.trim(),
    date_range_months: form.elements.date_range_months.value,
    history_interval: form.elements.history_interval.value,
    settings: currentSettings("backtest"),
  };
}

async function handleLiveAction(button) {
  if (button.disabled) return;
  const view = button.closest(".live-view");
  const mode = view.dataset.mode;
  const action = button.dataset.action;
  const payload = collectLivePayload(view);
  if (action === "fetch-nifty") {
    const data = await api("/api/live/fetch-nifty", { mode });
    $(`[data-field="nifty_token"]`, view).value = data.token;
    toast("NIFTY token fetched");
    return;
  }
  if (action === "refresh-margin") {
    const data = await api("/api/zerodha/margin", { mode });
    toast(data.error || "Balance refreshed");
    await refreshStatus();
    return;
  }
  if (action === "disconnect") {
    const data = await api("/api/zerodha/disconnect", { mode });
    toast(data.disconnected ? `${mode} disconnected` : data.message);
    await refreshStatus();
    return;
  }
  if (action === "fetch-option") {
    const index = button.dataset.option;
    const option = payload.options[Number(index)];
    const data = await api("/api/live/fetch-option", { mode, ...option });
    $(`[data-option="${index}"][data-field="tradingsymbol"]`, view).value = data.tradingsymbol;
    $(`[data-option="${index}"][data-field="token"]`, view).value = data.instrument_token;
    $(`[data-option="${index}"][data-field="expiry"]`, view).value = data.expiry;
    toast("Option contract fetched");
    return;
  }
  if (action === "start-feed") {
    await api("/api/live/start-feed", payload);
    toast("Feed connecting");
    await refreshStatus();
    return;
  }
  if (action === "start-live") {
    const data = await api("/api/live/start", payload);
    toast(data.message || `${mode} worker starting`);
    await refreshStatus();
    return;
  }
  if (action === "stop") {
    await api("/api/live/stop", {});
    toast("Stopped");
    await refreshStatus();
    return;
  }
  if (action === "square-off") {
    const data = await api("/api/live/square-off", {});
    toast(data.message || "Square-off requested");
    await refreshStatus();
    return;
  }
  if (action === "kill-switch") {
    const data = await api("/api/live/kill-switch", {});
    toast(data.reason || "Kill switch activated");
    await refreshStatus();
    return;
  }
  if (action === "open-candles") {
    const name = button.dataset.candles || "NIFTY";
    window.open(`/static/candles.html?name=${encodeURIComponent(name)}`, "_blank", "noopener");
  }
}

async function handleZerodhaBacktestAction(button) {
  const form = $("#zerodha-backtest-form");
  const mode = form.elements.mode.value || "PAPER";
  const action = button.dataset.zbtAction;
  if (action === "fetch-nifty") {
    const data = await api("/api/live/fetch-nifty", { mode });
    form.elements.nifty_token.value = data.token;
    toast("NIFTY token fetched");
    return;
  }
}

function showLiveActionError(button, error) {
  const view = button.closest(".live-view");
  const alertNode = $(`[data-field="status_alert"]`, view);
  if (!alertNode) return;
  alertNode.textContent = error.message || "Action failed";
  alertNode.hidden = false;
}

async function runNetworkHealthCheck() {
  const button = $("#network-health-run");
  const mode = $("#network-health-mode").value || "PAPER";
  button.disabled = true;
  button.textContent = "Checking...";
  try {
    const health = await api("/api/network/health", { mode });
    renderNetworkHealth({ [mode]: health });
    toast(`${mode} network health: ${health.quality}`);
  } finally {
    button.disabled = false;
    button.textContent = "Run Health Check";
  }
}

async function runRecoveryCheck() {
  const button = $("#recovery-run");
  const mode = $("#recovery-mode").value || "PAPER";
  button.disabled = true;
  button.textContent = "Checking...";
  try {
    const recovery = await api("/api/recovery/status", { mode });
    renderRecoveryStatus({ [mode]: recovery });
    toast(`${mode} recovery: ${recovery.status}`);
  } finally {
    button.disabled = false;
    button.textContent = "Check Recovery State";
  }
}

async function fetchMarketCues() {
  $("#cue-report-output").textContent = "Fetching Zerodha and global market cues. Upload NSE FII/DII CSV before analyzing...";
  state.marketCueAnalysis = null;
  setMarketCueActions();
  const data = await api("/api/market-cue/fetch", {});
  state.marketCueRaw = data;
  if (state.marketCueUploadedFlow) state.marketCueRaw.institutional_flow = state.marketCueUploadedFlow;
  renderMarketCue({ raw_data: state.marketCueRaw, report_text: "Fetched market cues. Upload NSE FII/DII CSV, then click Analyze." });
  toast("Market cues fetched");
}

async function analyzeMarketCues(extraPayload = {}) {
  if (!state.marketCueRaw) {
    toast("Fetch market cues before analyzing");
    return;
  }
  if (!state.marketCueUploadedFlow && !extraPayload.manual_fii_dii) {
    toast("Upload NSE FII/DII CSV before analyzing");
    return;
  }
  $("#cue-report-output").textContent = "Analyzing market cues...";
  const payload = { raw_data: state.marketCueRaw, ...extraPayload };
  if (state.marketCueUploadedFlow && !payload.institutional_flow) payload.institutional_flow = state.marketCueUploadedFlow;
  if (state.marketCueOverrides.length && !payload.manual_overrides) payload.manual_overrides = state.marketCueOverrides;
  const data = await api("/api/market-cue/analyze", payload);
  state.marketCueRaw = data.raw_data;
  state.marketCueAnalysis = data;
  renderMarketCue(data);
  toast(`Market cue: ${data.scoring?.bias || "analyzed"}`);
}

async function saveMarketCueReport() {
  if (!state.marketCueAnalysis) {
    toast("Analyze market cues before saving");
    return;
  }
  const data = await api("/api/market-cue/save", { analysis: state.marketCueAnalysis });
  toast(`Market cue report saved #${data.report_id} in results/market_cue`);
  await loadMarketCueHistory();
}

async function loadMarketCueHistory() {
  const data = await api("/api/market-cue/history");
  renderMarketCueHistory(data);
  $("#cue-history-table")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderCommandCenter(data = {}) {
  const connections = data.connections || {};
  const paperConnected = Boolean(connections.PAPER?.connected);
  const realConnected = Boolean(connections.LIVE?.connected);
  const feed = data.feed || {};
  const session = data.session_summary || {};
  const activeOrders = (data.active_orders || []).length;
  const feedStatus = String(feed.feed_status || "stopped").toUpperCase();
  const marketStatus = feed.market_open === true ? "OPEN" : feed.market_open === false ? "CLOSED" : "UNKNOWN";
  const dataLag = feed.last_tick_age_ms ?? feed.data_lag_ms ?? feed.backlog ?? "-";
  const killActive = Boolean(data.kill_switch || data.health?.kill_switch_active || data.recovery_status?.LIVE?.kill_switch_active);
  const realState = realConnected ? "ARMED" : "LOCKED";
  const pnl = Number(session.session_pnl || 0);
  const feedTone = feedStatus.includes("RUN") || feedStatus.includes("HEALTH") ? "success" : feedStatus.includes("STALE") ? "warning" : "muted";
  setPill("#cmd-market-status", `Market ${marketStatus}`, marketStatus === "OPEN" ? "success" : marketStatus === "CLOSED" ? "warning" : "muted");
  setPill("#cmd-paper-connection", paperConnected ? "Paper Connected" : "Paper Not Connected", paperConnected ? "success" : "muted");
  setPill("#cmd-real-connection", realConnected ? "Real Connected" : "Real Not Connected", realConnected ? "success" : "muted");
  setPill("#cmd-feed-health", `Feed ${humanText(feed.feed_status || "Stopped")}`, feedTone);
  setPill("#cmd-mode", data.current_mode || "PAPER", data.current_mode === "LIVE" ? "danger" : "info");
  setPill("#cmd-real-state", `Real ${realState}`, realState === "ARMED" ? "warning" : "muted");
  setPill("#cmd-kill-switch", killActive ? "Kill ON" : "Kill OFF", killActive ? "danger" : "success");
  setPill("#cmd-data-lag", `Lag ${dataLag}`, feed.backlog > 0 ? "warning" : "muted");
  setPill("#cmd-pnl", `PnL ${money(pnl)}`, pnl > 0 ? "success" : pnl < 0 ? "danger" : "muted");
  setPill("#cmd-active-orders", `Orders ${activeOrders}`, activeOrders ? "warning" : "muted");

  const readiness = [
    ["Paper Kite connected", paperConnected],
    ["Real Kite connected", realConnected],
    ["Access token valid", paperConnected || realConnected],
    ["Instruments loaded", Boolean(data.health?.instruments_loaded ?? true)],
    ["Market open", marketStatus === "OPEN"],
    ["Feed active", feedTone === "success"],
    ["Real money locked/armed", realConnected ? "ARMED" : "LOCKED"],
  ];
  const blockers = readiness.filter(([, ok]) => ok === false).map(([label]) => label);
  setText("#readiness-summary", blockers.length ? "NOT READY" : "READY");
  renderMetricList("#readiness-list", readiness.map(([label, value]) => [label, value === true ? "YES" : value === false ? "NO" : value]));
  renderMetricList("#risk-summary-list", [
    ["Today P&L", money(pnl)],
    ["Daily max loss", money(data.settings?.max_daily_loss ?? 0)],
    ["Daily profit target", money(data.settings?.max_daily_profit ?? 0)],
    ["Open exposure", money(data.health?.open_exposure ?? 0)],
    ["Open positions", (data.trades || []).filter(trade => String(trade.status || "").toUpperCase() === "OPEN").length],
    ["Kill switch", killActive ? "ON" : "OFF"],
  ]);
  renderMetricList("#data-health-list", [
    ["Feed state", humanText(feed.feed_status || "Stopped")],
    ["Ticks/sec", Math.round(feed.ticks_per_second || 0)],
    ["Tick backlog", feed.backlog || 0],
    ["NIFTY tick age", latestTickAge(data.ticks?.NIFTY)],
    ["Quote fallback", feed.quote_fallback_active ? "YES" : "NO"],
  ]);
  renderMetricList("#execution-health-list", [
    ["Active orders", activeOrders],
    ["Recent rejected", countOrderStatus(data.order_history, "REJECTED")],
    ["Partial fills", countPartialOrders(data.order_history)],
    ["Last order", lastOrderStatus(data.order_history)],
    ["Reconcile", data.recovery_status?.LIVE?.checked_at || "-"],
    ["Source", "Polling + reconciliation"],
  ]);
  renderMetricList("#broker-update-list", [
    ["Status source", "Polling + reconciliation"],
    ["Postback", "Disabled"],
    ["Local app safe", "Yes"],
    ["External callback required", "No"],
    ["Broker state", data.health?.broker_state || "OK"],
    ["Manual reconciliation", data.health?.manual_reconciliation_required ? "YES" : "NO"],
  ]);
  renderMetricList("#app-health-list", [
    ["Last exception", data.health?.last_exception || "-"],
    ["API latency", milliseconds(data.network_health?.LIVE?.total_ms || data.network_health?.PAPER?.total_ms)],
    ["Optimizer", optimizerRunning(data.optimizer_progress) ? "RUNNING" : "IDLE"],
    ["Replay loaded", data.last_replay ? "YES" : "NO"],
    ["Status time", new Date().toLocaleTimeString()],
  ]);
  const nextAction = blockers.length ? `Next safe action: ${blockers[0]}` : "Next safe action: Monitor dashboard";
  setText("#command-next-action", nextAction);
}

function renderMetricList(selector, rows) {
  const node = $(selector);
  if (!node || !shouldRender(`metric-list:${selector}`, rows)) return;
  node.innerHTML = rows.map(([label, value]) => `<div class="metric-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
}

function latestTickAge(rows = []) {
  const last = rows?.[rows.length - 1];
  if (!last?.time) return "-";
  const parsed = Date.parse(String(last.time).replace(" ", "T"));
  if (!Number.isFinite(parsed)) return "-";
  return `${Math.max(0, Math.round((Date.now() - parsed) / 1000))}s`;
}

function countOrderStatus(rows = [], status) {
  return (rows || []).filter(row => String(row.status || row["Order Status"] || "").toUpperCase().includes(status)).length;
}

function countPartialOrders(rows = []) {
  return (rows || []).filter(row => Number(row.filled_quantity || row["Filled Quantity"] || 0) > 0 && Number(row.pending_quantity || row["Pending Quantity"] || 0) > 0).length;
}

function lastOrderStatus(rows = []) {
  const row = (rows || [])[rows.length - 1] || {};
  return row.status || row["Order Status"] || "-";
}

function optimizerRunning(progress = {}) {
  return Object.values(progress || {}).some(item => item && !item.completed && item.started_at);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

async function refreshStatus() {
  if (state.refreshBusy) return;
  state.refreshBusy = true;
  const staleWarning = $("#main-ui-stale-warning");
  try {
    const data = await api("/api/status");
    state.lastRefreshOkAt = Date.now();
    staleWarning?.classList.remove("is-visible");
  state.lastStatus = data;
  $("#status-line").textContent = data.status || "Ready";
  state.redirectUrl = data.urls.redirect || "";
  $("#zerodha-redirect-copy").value = data.urls.redirect;
  $("#feed-status").textContent = humanText(data.feed.feed_status || "stopped");
  $("#ticks-rate").textContent = Math.round(data.feed.ticks_per_second || 0);
  $("#feed-backlog").textContent = data.feed.backlog || 0;
  $("#current-mode").textContent = data.current_mode || "PAPER";
  const realMargin = data.account_margins?.LIVE || {};
  const paperMargin = data.account_margins?.PAPER || {};
  const session = data.session_summary || {};
  $("#real-margin").textContent = realMargin.error ? realMargin.error : money(realMargin.available);
  $("#paper-margin").textContent = money(paperMargin.available ?? 0);
  $("#dash-real-margin").textContent = realMargin.error ? realMargin.error : money(realMargin.available);
  $("#dash-real-margin-time").textContent = realMargin.updated_at || "";
  $("#dash-paper-margin").textContent = paperMargin.error ? paperMargin.error : money(paperMargin.available ?? 0);
  $("#dash-paper-margin-time").textContent = paperMargin.updated_at || "";
  setMoneyText("#dash-session-pnl", session.session_pnl ?? 0, true);
  $("#dash-order-count").textContent = (data.active_orders?.length || 0) + (data.order_history?.length || 0);
  $("#paper-connection").textContent = connectionText(data.connections.PAPER);
  $("#live-connection").textContent = connectionText(data.connections.LIVE);
  renderCommandCenter(data);
  renderNetworkHealth(data.network_health || {});
  renderRecoveryStatus(data.recovery_status || {});
  $("#latest-result").textContent = JSON.stringify(data.last_backtest || data.last_replay?.summary || {}, null, 2);
  renderTable("#active-orders", data.active_orders || []);
  renderTable("#live-trade", data.live_trade && Object.keys(data.live_trade).length ? [data.live_trade] : []);
  $all(".live-view").forEach(view => {
    $(`[data-field="mode_status"]`, view).textContent = JSON.stringify({
      mode: view.dataset.mode,
      connection: data.connections[view.dataset.mode],
      margin: data.account_margins?.[view.dataset.mode],
      feed: data.feed,
      alerts: data.alerts?.slice(-3),
    }, null, 2);
    const margin = data.account_margins?.[view.dataset.mode] || {};
    const connection = data.connections?.[view.dataset.mode] || {};
    $(`[data-field="account_balance"]`, view).textContent = accountBalanceText(view.dataset.mode, margin, connection);
    $(`[data-field="account_time"]`, view).textContent = margin.updated_at || "";
    renderLiveStatus(data, view);
    ["NIFTY", "CE", "PE"].forEach(name => {
      const rateNode = $(`[data-rate="${name}"]`, view);
      if (rateNode) rateNode.textContent = `${data.tick_rates?.[name] || 0}/s`;
      renderTickTable($(`table[data-field='ticks-${name}']`, view), data.ticks?.[name] || []);
    });
    renderTable($("table[data-field='orders']", view), data.order_history || []);
  });
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

function connectionText(connection) {
  if (!connection?.connected) return connection?.blocked ? "Locked By Other Mode" : "Not Connected";
  const suffix = connection.user_id ? ` (${connection.user_id})` : "";
  return `${connection.user_name || "Connected"}${suffix}`;
}

function activateView(viewName) {
  const target = titles[viewName] ? viewName : "dashboard";
  $all(".nav[data-view]").forEach(item => item.classList.toggle("active", item.dataset.view === target));
  $all(".view").forEach(view => view.classList.toggle("active", view.id === target));
  $("#view-title").textContent = titles[target] || "TradeBot";
}

function bindNavigation() {
  $all(".nav[data-view]").forEach(button => {
    button.addEventListener("click", () => {
      window.location.hash = button.dataset.view;
      activateView(button.dataset.view);
    });
  });
  window.addEventListener("hashchange", () => activateView(window.location.hash.replace("#", "")));
  activateView(window.location.hash.replace("#", ""));
}

function bindForms() {
  document.addEventListener("click", event => {
    const settingsButton = event.target.closest("[data-settings]");
    if (settingsButton) openSettings(settingsButton.dataset.settings);
    const settingsTab = event.target.closest("[data-settings-tab]");
    if (settingsTab) {
      const dialog = settingsTab.closest("#settings-dialog");
      $all("[data-settings-tab]", dialog).forEach(button => button.classList.toggle("active", button === settingsTab));
      $all("[data-settings-panel]", dialog).forEach(panel => panel.classList.toggle("active", panel.dataset.settingsPanel === settingsTab.dataset.settingsTab));
    }
    const tickTab = event.target.closest("[data-tick-tab]");
    if (tickTab) {
      const panel = tickTab.closest(".tick-tabs");
      $all("[data-tick-tab]", panel).forEach(button => button.classList.toggle("active", button === tickTab));
      $all("[data-tick-panel]", panel).forEach(item => item.classList.toggle("active", item.dataset.tickPanel === tickTab.dataset.tickTab));
    }
    const disconnectButton = event.target.closest("[data-disconnect-mode]");
    if (disconnectButton) {
      api("/api/zerodha/disconnect", { mode: disconnectButton.dataset.disconnectMode })
        .then(async data => {
          toast(data.disconnected ? `${disconnectButton.dataset.disconnectMode} disconnected` : data.message);
          await refreshStatus();
        })
        .catch(error => toast(error.message));
    }
    const liveButton = event.target.closest(".live-view [data-action]");
    if (liveButton) handleLiveAction(liveButton).catch(error => {
      showLiveActionError(liveButton, error);
      toast(error.message);
    });
    const zerodhaBacktestButton = event.target.closest("[data-zbt-action]");
    if (zerodhaBacktestButton) handleZerodhaBacktestAction(zerodhaBacktestButton).catch(error => toast(error.message));
  });

  document.addEventListener("input", event => {
    const view = event.target.closest(".live-view");
    if (view && state.lastStatus) renderLiveStatus(state.lastStatus, view);
  });

  document.addEventListener("change", event => {
    const view = event.target.closest(".live-view");
    if (view && state.lastStatus) renderLiveStatus(state.lastStatus, view);
    if (event.target.closest("#backtest-form [name='data_source']")) updateBacktestSourceView();
  });

  $("#copy-redirect-url").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(state.redirectUrl || $("#zerodha-redirect-copy").value);
      toast("Redirect URL copied");
    } catch (_error) {
      toast(state.redirectUrl || $("#zerodha-redirect-copy").value || "Redirect URL unavailable");
    }
  });

  $("#settings-defaults").addEventListener("click", () => {
    state.settings[state.activeSettingsProfile] = { ...state.defaults };
    openSettings(state.activeSettingsProfile);
  });

  $("#settings-save").addEventListener("click", async () => {
    const values = collectSettingsFromDialog();
    showSettingsError("");
    try {
      const saved = await api(`/api/settings/${state.activeSettingsProfile}`, values);
      state.settings[state.activeSettingsProfile] = saved.values;
      $("#settings-dialog").close();
      await refreshStatus();
      toast("Settings saved");
    } catch (error) {
      const message = error.message || "Settings could not be saved.";
      showSettingsError(message);
      toast(message);
    }
  });

  $("#apply-backtest-live").addEventListener("click", async () => {
    const settings = currentSettings("backtest");
    const saved = await api("/api/settings/apply-backtest-live", { settings });
    state.settings = saved.profiles;
    await refreshStatus();
    toast("Backtest settings applied; paper balance preserved");
  });

  $("#network-health-run").addEventListener("click", () => {
    runNetworkHealthCheck().catch(error => toast(error.message));
  });
  $("#network-health-mode").addEventListener("change", () => {
    refreshStatus().catch(error => toast(error.message));
  });
  $("#recovery-run").addEventListener("click", () => {
    runRecoveryCheck().catch(error => toast(error.message));
  });
  $("#recovery-mode").addEventListener("change", () => {
    refreshStatus().catch(error => toast(error.message));
  });
  $("#cue-fetch").addEventListener("click", () => fetchMarketCues().catch(error => {
    $("#cue-report-output").textContent = error.message;
    toast(error.message);
  }));
  $("#cue-refresh").addEventListener("click", () => fetchMarketCues().catch(error => toast(error.message)));
  $("#cue-analyze").addEventListener("click", () => analyzeMarketCues().catch(error => {
    $("#cue-report-output").textContent = error.message;
    toast(error.message);
  }));
  $("#cue-save").addEventListener("click", () => saveMarketCueReport().catch(error => toast(error.message)));
  $("#cue-history").addEventListener("click", () => loadMarketCueHistory().catch(error => toast(error.message)));

  $("#cue-upload-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const data = await api("/api/market-cue/upload-fii-dii", form);
      state.marketCueUploadedFlow = data;
      state.marketCueAnalysis = null;
      $("#cue-upload-output").textContent = JSON.stringify(data, null, 2);
      if (state.marketCueRaw) {
        state.marketCueRaw.institutional_flow = data;
        renderMarketCue({ raw_data: state.marketCueRaw, report_text: "NSE FII/DII CSV parsed. Click Analyze to generate the report." });
      }
      setMarketCueActions();
      toast(`NSE CSV parsed: ${data.status}`);
    } catch (error) {
      $("#cue-upload-output").textContent = error.message;
      toast(error.message);
    }
  });

  $("#cue-manual-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const manual_fii_dii = {
      fii_net: form.get("fii_net"),
      dii_net: form.get("dii_net"),
      data_date: form.get("data_date"),
      reason: form.get("reason"),
    };
    analyzeMarketCues({ manual_fii_dii }).catch(error => {
      $("#cue-report-output").textContent = error.message;
      toast(error.message);
    });
  });

  $("#cue-override-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const field_name = String(form.get("field_name") || "").trim();
    if (!field_name) {
      toast("Enter an override field name");
      return;
    }
    state.marketCueOverrides.push({
      field_name,
      override_value: form.get("override_value"),
      reason: form.get("reason"),
    });
    analyzeMarketCues({ manual_overrides: state.marketCueOverrides }).catch(error => {
      $("#cue-report-output").textContent = error.message;
      toast(error.message);
    });
  });

  $("#backtest-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    form.set("settings", JSON.stringify(currentSettings("backtest")));
    $("#backtest-output").textContent = form.get("data_source") === "zerodha"
      ? "Fetching Zerodha candles and running backtest..."
      : "Running backtest...";
    try {
      const data = await api("/api/backtest/run", form);
      $("#backtest-output").textContent = JSON.stringify(data, null, 2);
      toast("Backtest complete");
    } catch (error) {
      $("#backtest-output").textContent = error.message;
      toast(error.message);
    }
  });

  $("#zerodha-backtest-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    $("#zerodha-backtest-output").textContent = "Running NIFTY optimizer. This can take a few minutes for larger date ranges...";
    try {
      const data = await api("/api/backtest/zerodha-optimize", collectZerodhaBacktestPayload(form));
      $("#zerodha-backtest-output").textContent = JSON.stringify(data, null, 2);
      toast("NIFTY optimizer complete");
    } catch (error) {
      $("#zerodha-backtest-output").textContent = error.message;
      toast(error.message);
    }
  });

  $all(".auth-form").forEach(form => {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const data = new FormData(form);
      try {
        const result = await api("/api/zerodha/login", {
          mode: form.dataset.mode,
          api_key: data.get("api_key"),
          api_secret: data.get("api_secret"),
        });
        window.open(result.login_url, "_blank", "noopener");
        toast("Zerodha login opened");
      } catch (error) {
        toast(error.message);
      }
    });
  });

  $("#latest-replay").addEventListener("click", async () => {
    const data = await api("/api/replay/latest");
    $(`#replay-form [name="path"]`).value = data.path || "";
    toast(data.path ? "Latest replay selected" : "No replay database found");
  });

  $("#replay-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const data = await api("/api/replay/load", form);
      $("#replay-summary").textContent = JSON.stringify({ path: data.path, summary: data.summary, highlights: data.highlights }, null, 2);
      renderTable("#replay-table", data.rows || []);
      toast("Replay loaded");
    } catch (error) {
      $("#replay-summary").textContent = error.message;
      toast(error.message);
    }
  });

  $all("input[readonly]").forEach(input => input.addEventListener("click", () => input.select()));
  updateBacktestSourceView();
}

async function boot() {
  bindNavigation();
  buildLiveViews();
  bindForms();
  setMarketCueActions();
  await loadSettings();
  await refreshStatus();
  loadMarketCueHistory().catch(() => {});
  setInterval(() => refreshStatus().catch(() => {}), 1500);
}

boot().catch(error => toast(error.message));
