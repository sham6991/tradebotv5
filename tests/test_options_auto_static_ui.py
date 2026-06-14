from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OptionsAutoStaticUITests(unittest.TestCase):
    def test_hidden_tab_panels_have_css_display_guard(self):
        css = (ROOT / "web_static" / "options_auto.css").read_text(encoding="utf-8")

        self.assertRegex(css, r"\.oa-tab-panel\[hidden\]\s*\{[^}]*display:\s*none\s*!important")

    def test_dashboard_contains_fii_upload_and_data_source_panel(self):
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")

        self.assertIn('id="oa-fii-dii-form"', html)
        self.assertIn('id="oa-fii-dii-file"', html)
        self.assertIn('id="oa-data-source-panel"', html)
        self.assertIn('id="oa-demo-banner"', html)
        self.assertIn('id="oa-live-feed-panel"', html)
        self.assertIn('id="oa-real-lifecycle-panel"', html)
        self.assertIn('id="oa-blackbox-panel"', html)
        self.assertIn('id="oa-market-context-panel"', html)
        self.assertIn('id="oa-trade-candidate-panel"', html)
        self.assertIn('id="oa-news-event-panel"', html)
        self.assertIn('id="oa-real-approval-card"', html)
        self.assertEqual(html.count('data-index-tick-panel'), 3)
        self.assertEqual(html.count('data-contract-lock-card'), 3)
        self.assertEqual(html.count('data-contract-lock-badge'), 3)
        self.assertIn("Tick Stream", html)
        self.assertIn("Contract Lock", html)
        self.assertIn('id="oa-stop-engine-top"', html)
        self.assertIn('id="oa-kill-switch-top"', html)
        self.assertIn('id="oa-paper-kill"', html)
        self.assertIn('id="oa-real-stop-engine"', html)
        self.assertIn('id="oa-real-kill"', html)
        self.assertIn("REAL MONEY MODE - LIVE ZERODHA ORDERS ONLY AFTER PREFLIGHT", html)
        self.assertIn("Start Real Scanner", html)
        self.assertNotIn("Start Real Engine", html)
        self.assertNotIn("disabled in this build", html.lower())

    def test_entry_mode_ui_does_not_offer_profile_default(self):
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn('value="OHLCV_VOLUME_PROFILE"', html)
        self.assertIn('value="FULL_CONFIRMATION"', html)
        self.assertNotIn('value="PROFILE"', html)
        self.assertIn("function normalizeEntryMode", js)

    def test_start_real_payload_does_not_force_real_enablement(self):
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        match = re.search(r"function realEnginePayload\(\) \{(?P<body>.*?)\n\}", js, re.DOTALL)

        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertIn('id="oa-dry-run-real" type="checkbox" checked', html)
        self.assertIn("dryRunRealNode ? Boolean(dryRunRealNode.checked) : true", js)
        self.assertIn('evaluationPayload("REAL")', body)
        self.assertNotIn("dry_run_real_only: false", body)
        self.assertNotIn("real_orders_enabled: true", body)
        self.assertNotIn("real_auto_entry_enabled: true", body)

    def test_real_preflight_and_settings_persistence_are_hydrated_from_status(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("lastRealPreflight", js)
        self.assertIn("function syncRealPreflightCache", js)
        self.assertIn("function realPreflightResult", js)
        self.assertIn("state.status.settings || settings", js)
        self.assertIn("bindCheckboxMirror(\"#oa-auto\", \"#oa-auto-settings\")", js)
        self.assertIn("syncSettingsToggles(\"paper\")", js)
        self.assertIn("syncSettingsToggles(\"settings\")", js)
        self.assertIn("Real Money Zerodha", js)
        self.assertIn("Entry Poll", js)
        self.assertIn("Order Updates", js)
        self.assertNotIn("Kite not connected", js)

    def test_state_changing_post_actions_refresh_ui_summary(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        self.assertIn('"/api/options-auto/ui-summary"', js)
        self.assertIn("async function refreshUiSummaryAfterMutation", js)

        for function_name in (
            "runBacktest",
            "runPaperStart",
            "stopPaperEngine",
            "stopEngine",
            "killSwitch",
            "requestPaperApproval",
            "approvePaper",
            "rejectPaper",
            "executePaper",
            "processPaperMarket",
            "resetPaperBalance",
            "runRealPreflight",
            "startRealEngine",
            "approveRealEntry",
            "rejectRealEntry",
            "runRealReconcile",
            "runRealDryRun",
            "stopNewEntries",
            "runSafeMode",
            "runEmergencyPlan",
            "saveSettings",
        ):
            match = re.search(rf"async function {function_name}\(\) \{{(?P<body>.*?)\n\}}", js, re.DOTALL)
            self.assertIsNotNone(match, function_name)
            self.assertIn("refreshUiSummaryAfterMutation", match.group("body"), function_name)

    def test_clear_actions_do_not_leave_stale_live_result(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        for function_name in ("stopPaperEngine", "stopEngine", "killSwitch", "rejectPaper", "resetPaperBalance"):
            match = re.search(rf"async function {function_name}\(\) \{{(?P<body>.*?)\n\}}", js, re.DOTALL)
            self.assertIsNotNone(match, function_name)
            self.assertIn("clearLastResult: true", match.group("body"), function_name)

        backtest = re.search(r"async function runBacktest\(\) \{(?P<body>.*?)\n\}", js, re.DOTALL)
        self.assertIsNotNone(backtest)
        self.assertNotIn("state.lastResult = result", backtest.group("body"))
        self.assertIn("clearLastResult: true", backtest.group("body"))

    def test_raw_json_controls_stay_in_debug_tab(self):
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")
        debug_index = html.index('id="oa-tab-debug"')

        for control_id in ("oa-market-cue-json", "oa-instruments-json", "oa-quotes-json"):
            control_index = html.index(f'id="{control_id}"')
            self.assertGreater(control_index, debug_index)

    def test_javascript_keeps_demo_samples_outside_live_modes(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("allowDemo", js)
        self.assertIn("demo_data", js)
        self.assertIn("/api/options-auto/market-cue/fii-dii-upload", js)
        self.assertRegex(js, re.compile(r"panel\.hidden\s*=", re.MULTILINE))

    def test_backtest_payload_uses_historical_data_request(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")
        match = re.search(r"function backtestPayload\(\) \{(?P<body>.*?)\n\}", js, re.DOTALL)

        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertIn('id="oa-backtest-spot"', html)
        self.assertIn('id="oa-backtest-expiry"', html)
        self.assertIn('id="oa-backtest-lots"', html)
        self.assertIn('id="oa-backtest-major-step"', html)
        self.assertIn('id="oa-backtest-entry-mode"', html)
        self.assertIn('id="oa-entry-mode"', html)
        self.assertIn('id="oa-backtest-span"', html)
        self.assertIn('data_source: "zerodha_historical"', body)
        self.assertIn("trade_date: tradeDate", body)
        self.assertIn("option_expiry: expiry", body)
        self.assertIn("backtest_spot: backtestSpot", body)
        self.assertIn("number_of_lots", body)
        self.assertIn("major_strike_step", body)
        self.assertIn("entry_dependency_mode", body)
        self.assertIn("atm_scan_strike_span: span", body)
        self.assertIn("backtest_compare_market_context_scenarios", body)
        self.assertIn("underlying", body)
        self.assertIn("interval", body)
        self.assertNotIn("sampleReplayCandles()", body)

    def test_backtest_renders_scenarios_and_missing_data_assumptions(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        render = re.search(r"function renderBacktestResults\(result = state\.lastBacktest\) \{(?P<body>.*?)\n\}", js, re.DOTALL)

        self.assertIsNotNone(render)
        body = render.group("body")
        self.assertIn("market_context_scenarios", body)
        self.assertIn("historical_data_assumptions", body)
        self.assertIn("Synthetic Fields", body)
        self.assertIn("Scenario Compare", body)

    def test_backtest_trade_table_and_date_picker_contracts_are_static(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")
        css = (
            (ROOT / "web_static" / "options_auto.css").read_text(encoding="utf-8")
            + (ROOT / "web_static" / "terminal_design.css").read_text(encoding="utf-8")
        )

        self.assertIn('id="oa-backtest-date" type="date"', html)
        self.assertIn('id="oa-backtest-expiry" type="date"', html)
        self.assertIn('id="oa-backtest-trades"', html)
        self.assertIn("function normalizeBacktestTrades", js)
        self.assertIn("function deriveBacktestTradesFromDecisions", js)
        self.assertIn("function backtestSummaryFromStatus", js)
        self.assertIn("function backtestTradeSide", js)
        self.assertIn("updateBacktestReportButtons", js)
        self.assertIn("function initNativeDatePickers", js)
        self.assertIn("showPicker", js)
        self.assertIn("::-webkit-calendar-picker-indicator", css)
        self.assertIn("native-date-input", css)

    def test_live_options_data_health_fields_are_visible(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        for token in (
            "Spot Source",
            "Live Spot",
            "ATM Strike",
            "Candidate Span",
            "Candidate Count",
            "Valid Quote Count",
            "Missing Quote Keys",
            "Live Scanner",
            "Quote Source",
            "Data Mode",
            "Instrument Cache",
            "Cache File",
            "Next Action",
            "Index Candles",
            "Candle Interval",
            "No Trade Reason",
            "Major Strike Step",
            "Entry Mode",
            "CE Locked",
            "PE Locked",
            "Fetched Lot Size",
            "Final Quantity",
            "ZERODHA_REQUIRED",
        ):
            self.assertIn(token, js)
        self.assertRegex(js, re.compile(r"allowDemo\s*\?\s*parseJson", re.MULTILINE))

    def test_index_tick_stream_renders_from_status_buffer(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        html = (ROOT / "web_static" / "options_auto.html").read_text(encoding="utf-8")

        self.assertIn("function renderIndexTickStreams", js)
        self.assertIn("function renderContractLockCards", js)
        self.assertIn("function renderIndustryDiagnostics", js)
        self.assertIn("WEBSOCKET_TICKS", js)
        self.assertIn("QUOTE_SNAPSHOT_POLLING", js)
        self.assertIn("Latency Blackbox", html)
        self.assertIn("Real Lifecycle", html)
        self.assertIn("state.status.index_ticks", js)
        self.assertIn("data-oa-tick-role", js)
        self.assertIn("Locked CE", js)
        self.assertIn("Locked PE", js)
        self.assertIn("slice(-80)", js)
        self.assertIn("data-index-tick-badge", js)
        self.assertIn("live_index_candles", js)
        self.assertIn("function noTradeReason", js)
        self.assertIn("function shortTime", js)
        self.assertIn("data-contract-lock-card", js)

        css = (ROOT / "web_static" / "options_auto.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.oa-index-tick-panel\s*\{[^}]*max-height:\s*356px", re.DOTALL)
        self.assertRegex(css, r"\.oa-index-tick-list\s*\{[^}]*overflow-y:\s*auto", re.DOTALL)
        self.assertRegex(css, r"\.oa-index-tick-row\s*\{[^}]*grid-template-columns:", re.DOTALL)
        self.assertRegex(css, r"\.oa-contract-lock-card\s+\.oa-plan-body\s*\{[^}]*overflow-y:\s*auto", re.DOTALL)

    def test_dashboard_renders_explainability_and_freshness_tags(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("result.explainability || result.decision_snapshot?.explainability", js)
        self.assertIn("result.freshness || result.decision_snapshot?.freshness", js)
        self.assertIn('row("Primary Stage"', js)
        self.assertIn('row("Primary Blocker"', js)
        self.assertIn('row("Freshness"', js)
        self.assertIn("function freshnessStatusText", js)
        self.assertIn("function freshnessTagText", js)

    def test_stop_and_kill_controls_call_live_engine_routes(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("function stopEngine", js)
        self.assertIn("function killSwitch", js)
        self.assertIn("/api/options-auto/stop", js)
        self.assertIn("/api/options-auto/kill-switch", js)

    def test_paper_and_real_live_order_boxes_have_render_contracts(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        css = (ROOT / "web_static" / "options_auto.css").read_text(encoding="utf-8")

        for token in (
            "function paperLifecycleFromState",
            "function paperOrdersFromState",
            "function lifecycleTradeFromReal",
            "function renderTradeDetails",
            "function renderPaperOrderRows",
            "function renderClosedPaperTrade",
            "Active Paper Trades",
            "Pending Entries",
            "Recent Paper Orders",
            "Closed Paper Trades",
            "Entry Order",
            "Target Order",
            "Stoploss Order",
            "Target Status",
            "Stoploss Status",
            "PROTECTIVE_EXIT_ACTIVE",
        ):
            self.assertIn(token, js)
        self.assertIn(".oa-paper-section", css)
        self.assertIn(".oa-order-grid", css)

    def test_frequent_refresh_uses_ui_summary_not_full_status(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("/api/options-auto/ui-summary", js)
        self.assertIn("/api/options-auto/status", js)
        self.assertIn("withCacheBuster", js)
        self.assertIn('cache: "no-store"', js)
        self.assertIn("scheduleRefresh", js)
        self.assertIn("state.activeTab === \"debug\"", js)
        self.assertIn("timeoutMs", js)
        self.assertIn("AbortController", js)

    def test_slow_backtest_and_paper_start_do_not_surface_raw_abort_error(self):
        js = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")

        self.assertIn("requestTimeoutMs", js)
        self.assertIn("requestTimeoutMessage", js)
        self.assertIn("controller.abort(new DOMException(timeoutReason, \"TimeoutError\"))", js)
        self.assertIn("aborted without reason", js)
        self.assertIn("Backtest request timed out", js)
        self.assertIn("Paper session start timed out", js)
        self.assertIn("if (path === \"/api/options-auto/backtest/run\") return 180000", js)
        self.assertIn("if (path === \"/api/options-auto/paper/start\") return 30000", js)


if __name__ == "__main__":
    unittest.main()
