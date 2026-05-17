function $(selector, root = document) {
  return root.querySelector(selector);
}

async function api(path) {
  const response = await fetch(path);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

function humanLabel(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, letter => letter.toUpperCase());
}

function text(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isFinite(value) ? value.toFixed(2).replace(/\.00$/, "") : "";
  return String(value);
}

function renderTable(target, rows, columns = null) {
  const table = typeof target === "string" ? $(target) : target;
  table.textContent = "";
  const normalized = Array.isArray(rows) ? rows : [];
  const cols = columns || Array.from(new Set(normalized.flatMap(row => Object.keys(row || {}))));
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

async function refreshCandles() {
  const name = $("#candle-name").value;
  const data = await api(`/api/candles?name=${encodeURIComponent(name)}&limit=300`);
  $("#candle-title").textContent = `${data.name} Candle Builder`;
  $("#candle-status").textContent = data.message || `${data.mode} ${data.session_id} | ${data.interval_minutes} minute candles`;
  renderTable("#active-candle", data.active || [], ["time", "open", "high", "low", "close", "volume"]);
  renderTable("#completed-candles", data.completed || [], ["time", "open", "high", "low", "close", "volume"]);
}

function boot() {
  const params = new URLSearchParams(window.location.search);
  const name = (params.get("name") || "NIFTY").toUpperCase();
  if (["NIFTY", "CE", "PE"].includes(name)) $("#candle-name").value = name;
  $("#candle-name").addEventListener("change", refreshCandles);
  refreshCandles().catch(error => $("#candle-status").textContent = error.message);
  setInterval(() => refreshCandles().catch(() => {}), 1500);
}

boot();
