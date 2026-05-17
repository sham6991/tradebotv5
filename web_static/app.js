const state = {
  settings: {},
  labels: {},
  activeSettingsProfile: "backtest",
  defaults: {},
};

const titles = {
  dashboard: "Dashboard",
  backtest: "Backtest Mode",
  paper: "Paper Trading Desk",
  live: "Zerodha Live Trading",
  replay: "Session Replay",
  zerodha: "Zerodha URLs",
};

const settingOrder = [
  "balance", "lot_size", "max_trades", "profit_points", "safety_points", "entry_offset",
  "time_exit", "cooldown", "chart_interval", "bullish_threshold", "bearish_threshold",
  "rsi_bull", "rsi_bear", "rsi_reversal_bullish", "rsi_reversal_bearish",
  "watch_buy_score", "min_buy_score", "strong_buy_score", "min_volume_ratio", "min_option_volume",
  "aggression_score_cap", "compression_range_ratio", "expansion_range_ratio", "max_chase_range_ratio",
  "failed_breakout_penalty", "early_breakout_min_score",
  "max_daily_loss", "max_daily_profit", "max_consecutive_losses", "square_off_time", "order_product",
];

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

function text(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
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

function milliseconds(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${number.toFixed(2)} ms`;
}

function renderTable(target, rows, columns = null) {
  const table = typeof target === "string" ? $(target) : target;
  if (!table) return;
  table.textContent = "";
  const normalized = Array.isArray(rows) ? rows : [];
  const cols = columns || Array.from(new Set(normalized.flatMap(row => Object.keys(row || {}))));
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
  if (!normalized.length) {
    const row = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = Math.max(cols.length, 1);
    td.textContent = "No rows";
    row.appendChild(td);
    tbody.appendChild(row);
  } else {
    normalized.forEach(item => {
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
    values[input.dataset.settingKey] = input.value.trim();
  });
  return values;
}

function openSettings(profile) {
  state.activeSettingsProfile = profile;
  $("#settings-title").textContent = `${profile[0].toUpperCase()}${profile.slice(1)} Risk Settings`;
  const fields = $("#settings-fields");
  fields.textContent = "";
  const values = currentSettings(profile);
  settingOrder.forEach(key => {
    const label = document.createElement("label");
    label.textContent = state.labels[key] || key;
    const input = key === "chart_interval" || key === "order_product" ? document.createElement("select") : document.createElement("input");
    input.dataset.settingKey = key;
    if (key === "chart_interval") {
      ["1 min", "2 min", "3 min", "5 min"].forEach(option => input.add(new Option(option, option)));
    }
    if (key === "order_product") {
      ["NRML", "MIS"].forEach(option => input.add(new Option(option, option)));
    }
    input.value = values[key] ?? "";
    label.appendChild(input);
    fields.appendChild(label);
  });
  $("#settings-dialog").showModal();
}

async function loadSettings() {
  const data = await api("/api/settings");
  state.settings = data.profiles;
  state.labels = data.labels;
  state.defaults = { ...(data.defaults || data.profiles?.backtest || {}) };
}

function buildLiveViews() {
  $all(".live-view").forEach(view => {
    const mode = view.dataset.mode;
    const profile = mode === "LIVE" ? "real" : "paper";
    $(".live-layout", view).innerHTML = `
      <section class="panel live-contracts">
        <h2>${mode === "LIVE" ? "Real Trading" : "Paper Trading"} Contracts</h2>
        <div class="live-account-card">
          <span>${mode === "LIVE" ? "Available Margin" : "Paper Data Account"}</span>
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
        </div>
      </section>
      <section class="panel">
        <h2>${mode} Status</h2>
        <pre data-field="mode_status">Waiting for connection.</pre>
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
        <div class="tick-table active" data-tick-panel="NIFTY"><div class="table-wrap"><table data-field="ticks-NIFTY"></table></div></div>
        <div class="tick-table" data-tick-panel="CE"><div class="table-wrap"><table data-field="ticks-CE"></table></div></div>
        <div class="tick-table" data-tick-panel="PE"><div class="table-wrap"><table data-field="ticks-PE"></table></div></div>
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

async function handleLiveAction(button) {
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
    return;
  }
  if (action === "disconnect") {
    const data = await api("/api/zerodha/disconnect", { mode });
    toast(data.disconnected ? `${mode} disconnected` : data.message);
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
    return;
  }
  if (action === "start-live") {
    await api("/api/live/start", payload);
    toast(`${mode} worker started`);
    return;
  }
  if (action === "stop") {
    await api("/api/live/stop", {});
    toast("Stopped");
    return;
  }
  if (action === "square-off") {
    const data = await api("/api/live/square-off", {});
    toast(data.message || "Square-off requested");
    return;
  }
  if (action === "kill-switch") {
    const data = await api("/api/live/kill-switch", {});
    toast(data.reason || "Kill switch activated");
    return;
  }
  if (action === "open-candles") {
    const name = button.dataset.candles || "NIFTY";
    window.open(`/static/candles.html?name=${encodeURIComponent(name)}`, "_blank", "noopener");
  }
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

async function refreshStatus() {
  const data = await api("/api/status");
  $("#status-line").textContent = data.status || "Ready";
  $("#redirect-url").value = data.urls.redirect;
  $("#zerodha-redirect-copy").value = data.urls.redirect;
  $("#feed-status").textContent = humanText(data.feed.feed_status || "stopped");
  $("#ticks-rate").textContent = Math.round(data.feed.ticks_per_second || 0);
  $("#feed-backlog").textContent = data.feed.backlog || 0;
  $("#current-mode").textContent = data.current_mode || "PAPER";
  const realMargin = data.account_margins?.LIVE || {};
  const paperMargin = data.account_margins?.PAPER || {};
  $("#real-margin").textContent = money(realMargin.available);
  $("#paper-margin").textContent = paperMargin.available !== null && paperMargin.available !== undefined ? money(paperMargin.available) : connectionText(data.connections.PAPER);
  $("#dash-real-margin").textContent = realMargin.error ? realMargin.error : money(realMargin.available);
  $("#dash-real-margin-time").textContent = realMargin.updated_at || "";
  $("#dash-paper-margin").textContent = paperMargin.error ? paperMargin.error : (paperMargin.available !== null && paperMargin.available !== undefined ? money(paperMargin.available) : connectionText(data.connections.PAPER));
  $("#dash-paper-margin-time").textContent = paperMargin.updated_at || "";
  $("#dash-order-count").textContent = (data.active_orders?.length || 0) + (data.order_history?.length || 0);
  $("#paper-connection").textContent = connectionText(data.connections.PAPER);
  $("#live-connection").textContent = connectionText(data.connections.LIVE);
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
    $(`[data-field="account_balance"]`, view).textContent = margin.error ? margin.error : money(margin.available);
    $(`[data-field="account_time"]`, view).textContent = margin.updated_at || "";
    ["NIFTY", "CE", "PE"].forEach(name => {
      const rateNode = $(`[data-rate="${name}"]`, view);
      if (rateNode) rateNode.textContent = `${data.tick_rates?.[name] || 0}/s`;
      renderTable($(`table[data-field='ticks-${name}']`, view), (data.ticks?.[name] || []).slice(-80), ["time", "name", "token", "ltp", "volume"]);
    });
    renderTable($("table[data-field='orders']", view), data.order_history || []);
  });
}

function connectionText(connection) {
  if (!connection?.connected) return connection?.blocked ? "Locked By Other Mode" : "Not Connected";
  const suffix = connection.user_id ? ` (${connection.user_id})` : "";
  return `${connection.user_name || "Connected"}${suffix}`;
}

function bindNavigation() {
  $all(".nav").forEach(button => {
    button.addEventListener("click", () => {
      $all(".nav").forEach(item => item.classList.toggle("active", item === button));
      $all(".view").forEach(view => view.classList.toggle("active", view.id === button.dataset.view));
      $("#view-title").textContent = titles[button.dataset.view] || "TradeBot";
    });
  });
}

function bindForms() {
  document.addEventListener("click", event => {
    const settingsButton = event.target.closest("[data-settings]");
    if (settingsButton) openSettings(settingsButton.dataset.settings);
    const tickTab = event.target.closest("[data-tick-tab]");
    if (tickTab) {
      const panel = tickTab.closest(".tick-tabs");
      $all("[data-tick-tab]", panel).forEach(button => button.classList.toggle("active", button === tickTab));
      $all("[data-tick-panel]", panel).forEach(item => item.classList.toggle("active", item.dataset.tickPanel === tickTab.dataset.tickTab));
    }
    const disconnectButton = event.target.closest("[data-disconnect-mode]");
    if (disconnectButton) {
      api("/api/zerodha/disconnect", { mode: disconnectButton.dataset.disconnectMode })
        .then(data => toast(data.disconnected ? `${disconnectButton.dataset.disconnectMode} disconnected` : data.message))
        .catch(error => toast(error.message));
    }
    const liveButton = event.target.closest(".live-view [data-action]");
    if (liveButton) handleLiveAction(liveButton).catch(error => toast(error.message));
  });

  $("#settings-defaults").addEventListener("click", () => {
    state.settings[state.activeSettingsProfile] = { ...state.defaults };
    openSettings(state.activeSettingsProfile);
  });

  $("#settings-save").addEventListener("click", async () => {
    const values = collectSettingsFromDialog();
    const saved = await api(`/api/settings/${state.activeSettingsProfile}`, values);
    state.settings[state.activeSettingsProfile] = saved.values;
    $("#settings-dialog").close();
    toast("Settings saved");
  });

  $("#apply-backtest-live").addEventListener("click", async () => {
    const settings = currentSettings("backtest");
    const saved = await api("/api/settings/apply-backtest-live", { settings });
    state.settings = saved.profiles;
    toast("Backtest settings applied to Paper and Real");
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

  $("#backtest-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    form.set("settings", JSON.stringify(currentSettings("backtest")));
    $("#backtest-output").textContent = "Running backtest...";
    try {
      const data = await api("/api/backtest/run", form);
      $("#backtest-output").textContent = JSON.stringify(data, null, 2);
      toast("Backtest complete");
    } catch (error) {
      $("#backtest-output").textContent = error.message;
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
}

async function boot() {
  bindNavigation();
  buildLiveViews();
  bindForms();
  await loadSettings();
  await refreshStatus();
  setInterval(() => refreshStatus().catch(() => {}), 1500);
}

boot().catch(error => toast(error.message));
