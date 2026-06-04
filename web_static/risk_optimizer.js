(function () {
  function optimizerPayload(form) {
    const payload = new FormData(form);
    payload.set("settings", JSON.stringify(currentSettings("backtest")));
    return payload;
  }

  function progressText(progress, fallback) {
    if (!progress) return fallback;
    const percent = Number(progress.percent || 0).toFixed(1);
    const count = progress.total ? `${progress.completed || 0}/${progress.total} tests` : "Preparing tests";
    const stage = progress.stage ? `${progress.stage}: ` : "";
    return `${stage}${percent}% complete\n${count}\n${progress.message || fallback}\nUpdated: ${progress.updated_at || ""}`;
  }

  function setInlineProgress(selector, progress) {
    const node = document.querySelector(selector);
    if (!node) return;
    node.textContent = progress ? `${Number(progress.percent || 0).toFixed(1)}%` : "";
  }

  async function stopRiskSettingsOptimizer() {
    const output = document.querySelector("#risk-optimizer-output");
    const button = document.querySelector("#stop-risk-optimizer");
    if (button) button.disabled = true;
    try {
      const result = await api("/api/backtest/optimizer-stop", { kind: "risk_settings" });
      if (output && result.progress) output.textContent = progressText(result.progress, result.message);
      toast(result.message);
    } catch (error) {
      if (button) button.disabled = false;
      toast(error.message);
    }
  }

  function startProgressPolling(kind, output, inlineSelector, fallback) {
    let stopped = false;
    const poll = async () => {
      if (stopped) return;
      try {
        const status = await api("/api/status");
        const progress = status.optimizer_progress?.[kind];
        if (progress && (progress.active || progress.updated_at)) {
          output.textContent = progressText(progress, fallback);
          setInlineProgress(inlineSelector, progress);
        }
      } catch (_error) {
        // Keep the optimizer request running even if one progress poll fails.
      }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }

  async function runRiskSettingsOptimizer() {
    const form = document.querySelector("#backtest-form");
    const output = document.querySelector("#risk-optimizer-output");
    const button = document.querySelector("#optimize-risk-settings");
    const stopButton = document.querySelector("#stop-risk-optimizer");
    if (!form || !output || !button) return;

    button.disabled = true;
    if (stopButton) stopButton.disabled = false;
    const source = form.elements.data_source?.value || "manual";
    output.textContent = source === "zerodha"
      ? "Fetching Zerodha candles and optimizing risk settings..."
      : "Optimizing risk settings against the selected full-day dataset...";
    const stopProgressPolling = startProgressPolling("risk_settings", output, "#risk-optimizer-progress", output.textContent);
    try {
      const result = await api("/api/backtest/risk-optimize", optimizerPayload(form));
      stopProgressPolling();
      if (result.stopped) {
        output.textContent = result.progress ? progressText(result.progress, result.message) : result.message;
        toast(result.message);
      } else {
        setInlineProgress("#risk-optimizer-progress", { percent: 100 });
        output.textContent = JSON.stringify(result, null, 2);
        toast(`Risk optimizer complete: ${result.runs} runs`);
      }
    } catch (error) {
      stopProgressPolling();
      setInlineProgress("#risk-optimizer-progress", null);
      output.textContent = error.message;
      toast(error.message);
    } finally {
      button.disabled = false;
      if (stopButton) stopButton.disabled = true;
    }
  }

  function bindRiskOptimizer() {
    const button = document.querySelector("#optimize-risk-settings");
    const stopButton = document.querySelector("#stop-risk-optimizer");
    if (button) button.addEventListener("click", () => {
      runRiskSettingsOptimizer();
    });
    if (stopButton) stopButton.addEventListener("click", () => {
      stopRiskSettingsOptimizer();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindRiskOptimizer);
  } else {
    bindRiskOptimizer();
  }
})();
