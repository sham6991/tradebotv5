(function () {
  function optimizerPayload(form) {
    const payload = new FormData(form);
    const mode = document.querySelector("#trading-optimizer-mode")?.value || "quick";
    const optimizer = mode === "full"
      ? { max_runs: 6000, parallel_workers: 2 }
      : { quick_mode: true, quick_max_runs: 500, parallel_workers: 2 };
    payload.set("settings", JSON.stringify(currentSettings("backtest")));
    payload.set("optimizer", JSON.stringify(optimizer));
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

  async function stopTradingTabOptimizer() {
    const output = document.querySelector("#trading-optimizer-output");
    const button = document.querySelector("#stop-trading-optimizer");
    if (button) button.disabled = true;
    try {
      const result = await api("/api/backtest/optimizer-stop", { kind: "trading_tab" });
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

  async function runTradingTabOptimizer() {
    const form = document.querySelector("#backtest-form");
    const output = document.querySelector("#trading-optimizer-output");
    const button = document.querySelector("#optimize-trading-tab");
    const stopButton = document.querySelector("#stop-trading-optimizer");
    const modeSelect = document.querySelector("#trading-optimizer-mode");
    if (!form || !output || !button) return;

    button.disabled = true;
    if (stopButton) stopButton.disabled = false;
    if (modeSelect) modeSelect.disabled = true;
    const source = form.elements.data_source?.value || "manual";
    const modeLabel = modeSelect?.selectedOptions?.[0]?.textContent || "Quick 500";
    output.textContent = source === "zerodha"
      ? `Fetching Zerodha candles and optimizing Trading tab settings (${modeLabel})...`
      : `Optimizing Trading tab settings against the selected full-day dataset (${modeLabel})...`;
    const stopProgressPolling = startProgressPolling("trading_tab", output, "#trading-optimizer-progress", output.textContent);
    try {
      const result = await api("/api/backtest/trading-optimize", optimizerPayload(form));
      stopProgressPolling();
      if (result.stopped) {
        output.textContent = result.progress ? progressText(result.progress, result.message) : result.message;
        toast(result.message);
      } else {
        setInlineProgress("#trading-optimizer-progress", { percent: 100 });
        output.textContent = JSON.stringify(result, null, 2);
        toast(`Trading tab optimizer complete: ${result.runs} runs`);
      }
    } catch (error) {
      stopProgressPolling();
      setInlineProgress("#trading-optimizer-progress", null);
      output.textContent = error.message;
      toast(error.message);
    } finally {
      button.disabled = false;
      if (stopButton) stopButton.disabled = true;
      if (modeSelect) modeSelect.disabled = false;
    }
  }

  function bindTradingOptimizer() {
    const button = document.querySelector("#optimize-trading-tab");
    const stopButton = document.querySelector("#stop-trading-optimizer");
    if (button) button.addEventListener("click", () => {
      runTradingTabOptimizer();
    });
    if (stopButton) stopButton.addEventListener("click", () => {
      stopTradingTabOptimizer();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindTradingOptimizer);
  } else {
    bindTradingOptimizer();
  }
})();
