import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OptionsAutoUiRenderContractTests(unittest.TestCase):
    def test_backtest_renderer_shows_backend_trades_and_enables_report_buttons(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        source = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        payload = {
            "data_source": "zerodha_historical",
            "rows": 125,
            "option_frames": 2,
            "report": {
                "folder": r"results\options_auto\backtests\2026-06-07\OA-TEST",
                "audit_json": r"results\options_auto\backtests\2026-06-07\OA-TEST\audit.json",
            },
            "metrics": {"total_trades": 1, "total_pnl": 1900.25, "win_rate": 100},
            "decisions": [
                {
                    "row": 1,
                    "datetime": "2026-06-05 09:15:00+05:30",
                    "decision": "ENTRY",
                    "tradingsymbol": "NIFTY26JUN23200PE",
                    "score": 68.96,
                },
                {
                    "row": 30,
                    "datetime": "2026-06-05 10:45:00+05:30",
                    "decision": "EXIT",
                    "tradingsymbol": "NIFTY26JUN23200PE",
                    "reason": "TARGET",
                    "exit_price": 86.75,
                    "net_pnl": 1900.25,
                    "charges": 40,
                },
            ],
            "trades": [
                {
                    "tradingsymbol": "NIFTY26JUN23200PE",
                    "entry_index": 1,
                    "exit_index": 30,
                    "entry_price": 56.9,
                    "exit_price": 86.75,
                    "quantity": 65,
                    "exit_reason": "TARGET",
                    "net_pnl": 1900.25,
                }
            ],
        }
        script = self._node_script(source, payload, expected_symbol="NIFTY26JUN23200PE")

        result = self._run_node_script(node, script)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backtest_renderer_derives_completed_trades_from_decisions(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        source = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        payload = {
            "data_source": "zerodha_historical",
            "rows": 60,
            "option_frames": 2,
            "report": {
                "folder": r"results\options_auto\backtests\2026-06-07\OA-DERIVED",
                "audit_json": r"results\options_auto\backtests\2026-06-07\OA-DERIVED\audit.json",
            },
            "metrics": {"total_trades": 1, "total_pnl": -589.25},
            "decisions": [
                {
                    "row": 33,
                    "datetime": "2026-06-05 10:51:00+05:30",
                    "decision": "ENTRY",
                    "tradingsymbol": "NIFTY26JUN23200PE",
                    "entry_price": 71.55,
                    "quantity": 65,
                    "score": 77.92,
                },
                {
                    "row": 35,
                    "datetime": "2026-06-05 11:00:00+05:30",
                    "decision": "EXIT",
                    "tradingsymbol": "NIFTY26JUN23200PE",
                    "reason": "STOPLOSS",
                    "exit_price": 63.1,
                    "net_pnl": -589.25,
                },
            ],
        }
        script = self._node_script(source, payload, expected_symbol="NIFTY26JUN23200PE")

        result = self._run_node_script(node, script)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backtest_renderer_uses_status_summary_after_page_refresh(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        source = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        backtest_summary = {
            "mode": "BACKTEST",
            "data_source": "zerodha_historical",
            "rows": 42,
            "option_frames": 2,
            "report": {
                "folder": r"results\options_auto\backtests\2026-06-07\OA-STATUS",
                "audit_json": r"results\options_auto\backtests\2026-06-07\OA-STATUS\audit.json",
            },
            "metrics": {"total_trades": 1, "total_pnl": 626.25},
            "trades": [
                {
                    "tradingsymbol": "NIFTY26JUN23200PE",
                    "entry_index": 39,
                    "exit_index": 41,
                    "entry_price": 45.2,
                    "exit_price": 55.45,
                    "quantity": 65,
                    "exit_reason": "TARGET",
                    "net_pnl": 626.25,
                }
            ],
            "decisions": [
                {
                    "row": 39,
                    "datetime": "2026-06-05 11:09:00+05:30",
                    "decision": "ENTRY",
                    "tradingsymbol": "NIFTY26JUN23200PE",
                    "score": 64.52,
                },
                {
                    "row": 41,
                    "datetime": "2026-06-05 11:15:00+05:30",
                    "decision": "EXIT",
                    "tradingsymbol": "NIFTY26JUN23200PE",
                    "reason": "TARGET",
                    "net_pnl": 626.25,
                },
            ],
        }
        script = self._node_script(
            source,
            {},
            expected_symbol="NIFTY26JUN23200PE",
            status_summary=backtest_summary,
        )

        result = self._run_node_script(node, script)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_live_paper_and_real_lifecycle_boxes_show_orders_and_exits(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        source = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        script = textwrap.dedent(
            f"""
            const nodes = {{}};
            function makeNode(tagName = "DIV") {{
              return {{
                tagName,
                innerHTML: "",
                textContent: "",
                disabled: false,
                dataset: {{}},
                value: "",
                checked: false,
                className: "",
                classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
                addEventListener() {{}},
              }};
            }}
            [
              "#oa-paper-account",
              "#oa-paper-plan",
              "#oa-approval-badge",
              "#oa-approval-card",
              "#oa-paper-trades",
              "#oa-real-lifecycle-badge",
              "#oa-real-lifecycle-panel",
              "#oa-real-mode-title",
              "#oa-real-mode-copy",
              "#oa-real-checklist",
              "#oa-real-position",
              "#oa-active-trade-badge",
              "#oa-active-trade-body",
              "#oa-trade-timeline",
              "#oa-events",
            ].forEach(id => nodes[id] = makeNode());
            globalThis.document = {{
              visibilityState: "visible",
              querySelector(selector) {{ return nodes[selector] || null; }},
              querySelectorAll() {{ return []; }},
              addEventListener() {{}},
            }};
            globalThis.window = {{
              setInterval() {{}},
              crypto: {{ randomUUID() {{ return "test-id"; }} }},
            }};
            {source}
            const paperLifecycle = {{
              active_trades: [{{
                tradingsymbol: "NIFTY26JUN22600CE",
                quantity: 65,
                entry_price: 120.5,
                last_ltp: 131.2,
                unrealized_pnl: 695.5,
                target: 150,
                stoploss: 105,
                entry_order_id: "PAPER-ENTRY-1",
                target_order_id: "PAPER-TARGET-1",
                stoploss_order_id: "PAPER-SL-1",
                oco_active: true,
                position_protected: true,
                status: "ACTIVE",
              }}],
              pending_entries: [{{
                approval_id: "APP-1",
                trade_plan: {{ tradingsymbol: "NIFTY26JUN22700CE", quantity: 65, entry_price: 98.5 }},
                entry_order: {{ order_id: "PAPER-PENDING-1", status: "OPEN", quantity: 65, price: 98.5 }},
              }}],
              closed_trades: [{{
                tradingsymbol: "NIFTY26JUN22500PE",
                quantity: 65,
                entry_price: 80,
                exit_price: 91,
                pnl_net: 675,
                exit_reason: "TARGET_FILLED",
                entry_order_id: "PAPER-OLD-ENTRY",
                target_order_id: "PAPER-OLD-TARGET",
                closed_at: "2026-06-07T10:30:00+05:30",
              }}],
              events: [
                {{ event: "ENTRY_PENDING", order_id: "PAPER-PENDING-1", tradingsymbol: "NIFTY26JUN22700CE", timestamp: "2026-06-07T10:01:00+05:30" }},
                {{ event: "TARGET_FILLED", order_id: "PAPER-OLD-TARGET", tradingsymbol: "NIFTY26JUN22500PE", timestamp: "2026-06-07T10:30:00+05:30" }},
              ],
              account: {{
                opening_balance: 20000,
                available_balance: 20675,
                realized_pnl: 675,
                unrealized_pnl: 695.5,
                charges: 40,
                orders: [
                  {{ order_id: "PAPER-ENTRY-1", status: "COMPLETE", transaction_type: "BUY", tradingsymbol: "NIFTY26JUN22600CE", quantity: 65, average_price: 120.5, order_type: "LIMIT" }},
                  {{ order_id: "PAPER-TARGET-1", status: "OPEN", transaction_type: "SELL", tradingsymbol: "NIFTY26JUN22600CE", quantity: 65, price: 150, order_type: "LIMIT" }},
                  {{ order_id: "PAPER-SL-1", status: "TRIGGER PENDING", transaction_type: "SELL", tradingsymbol: "NIFTY26JUN22600CE", quantity: 65, trigger_price: 105, order_type: "SL" }},
                ],
              }},
            }};
            const realLifecycle = {{
              state: "OCO_ACTIVE",
              protected_state: "PROTECTIVE_EXIT_ACTIVE",
              trade_plan: {{ tradingsymbol: "NIFTY26JUN22600CE", side: "CE", quantity: 65, entry_price: 120.5, target: 150, stoploss: 105 }},
              entry_order: {{ order_id: "REAL-ENTRY-1", status: "COMPLETE", quantity: 65, average_price: 120.5, price: 120.5 }},
              fill: {{ filled_quantity: 65, average_price: 120.5 }},
              target_order: {{ order_id: "REAL-TARGET-1", status: "OPEN", quantity: 65, price: 150 }},
              stoploss_order: {{ order_id: "REAL-SL-1", status: "TRIGGER PENDING", quantity: 65, trigger_price: 105 }},
              history: [
                {{ event: "ENTRY_FILLED", order_id: "REAL-ENTRY-1", timestamp: "2026-06-07T10:03:00+05:30" }},
                {{ event: "OCO_ACTIVE", order_id: "REAL-TARGET-1", timestamp: "2026-06-07T10:03:02+05:30" }},
              ],
            }};
            state.status = {{
              settings: {{ mode: "PAPER" }},
              paper_lifecycle: paperLifecycle,
              account_status: {{ paper: {{ connected: true }}, real: {{ connected: false }} }},
              live_scan: {{ running: true, mode: "PAPER" }},
              session: {{ active_trades: [] }},
            }};
            state.lastResult = {{
              settings: state.status.settings,
              paper_lifecycle: paperLifecycle,
              account_status: state.status.account_status,
              live_scan: state.status.live_scan,
              session: {{ active_trades: [] }},
            }};
            renderPaperAccount();
            renderActiveTradeCard(activeTradesFrom(state.lastResult, {{ currentOnly: true }}));
            renderRecentEvents(state.lastResult);

            const paperHtml = nodes["#oa-paper-trades"].innerHTML;
            const paperEventsHtml = nodes["#oa-events"].innerHTML;
            for (const expected of [
              "Active Paper Trades",
              "Pending Entries",
              "Recent Paper Orders",
              "Closed Paper Trades",
              "PAPER-ENTRY-1",
              "PAPER-TARGET-1",
              "PAPER-SL-1",
              "PAPER-PENDING-1",
              "PAPER-OLD-TARGET",
              "TARGET_FILLED",
            ]) {{
              if (!paperHtml.includes(expected)) throw new Error("Paper lifecycle box missed " + expected + ": " + paperHtml);
            }}
            if (!paperEventsHtml.includes("ENTRY_PENDING")) {{
              throw new Error("Recent events did not include paper lifecycle events: " + paperEventsHtml);
            }}

            state.status = {{
              settings: {{ mode: "REAL" }},
              account_status: {{ paper: {{ connected: false }}, real: {{ connected: true }} }},
              real_order_lifecycle: realLifecycle,
              live_scan: {{ running: true, mode: "REAL" }},
              session: {{ active_trades: [] }},
            }};
            state.lastResult = {{
              settings: state.status.settings,
              account_status: state.status.account_status,
              real_order_lifecycle: realLifecycle,
              live_scan: state.status.live_scan,
              session: {{ active_trades: [] }},
            }};
            renderIndustryDiagnostics();
            renderRealPreflight(state.lastResult);
            renderActiveTradeCard(activeTradesFrom(state.lastResult, {{ currentOnly: true }}));
            renderRecentEvents(state.lastResult);

            const lifecycleHtml = nodes["#oa-real-lifecycle-panel"].innerHTML;
            const realPositionHtml = nodes["#oa-real-position"].innerHTML;
            const activeHtml = nodes["#oa-active-trade-body"].innerHTML;
            const realEventsHtml = nodes["#oa-events"].innerHTML;
            for (const expected of ["REAL-ENTRY-1", "REAL-TARGET-1", "REAL-SL-1", "COMPLETE", "OPEN", "TRIGGER PENDING", "OCO_ACTIVE"]) {{
              if (!lifecycleHtml.includes(expected)) throw new Error("Real lifecycle panel missed " + expected + ": " + lifecycleHtml);
            }}
            for (const expected of ["REAL-ENTRY-1", "REAL-TARGET-1", "REAL-SL-1", "PROTECTIVE_EXIT_ACTIVE"]) {{
              if (!realPositionHtml.includes(expected) && !activeHtml.includes(expected)) {{
                throw new Error("Real position/dashboard boxes missed " + expected + ": " + realPositionHtml + " | " + activeHtml);
              }}
            }}
            if (!realEventsHtml.includes("OCO_ACTIVE")) {{
              throw new Error("Recent events did not include real lifecycle events: " + realEventsHtml);
            }}
            """
        )

        result = self._run_node_script(node, script)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stopped_backtest_status_does_not_render_stale_live_lock_or_paper_trade(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        source = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        script = textwrap.dedent(
            f"""
            const nodes = {{}};
            const contractCards = [];
            const contractBadges = [];
            function makeNode(tagName = "DIV") {{
              return {{
                tagName,
                innerHTML: "",
                textContent: "",
                disabled: false,
                dataset: {{}},
                value: "",
                checked: false,
                className: "",
                classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
                addEventListener() {{}},
              }};
            }}
            [
              "#oa-mode",
              "#oa-real-money",
              "#oa-kite",
              "#oa-data",
              "#oa-governor",
              "#oa-engine",
              "#oa-position",
              "#oa-protection",
              "#oa-oco",
              "#oa-kill-state",
              "#oa-daily-pnl",
              "#oa-paper-account",
              "#oa-paper-plan",
              "#oa-approval-badge",
              "#oa-approval-card",
              "#oa-paper-trades",
              "#oa-real-mode-title",
              "#oa-real-mode-copy",
              "#oa-real-checklist",
              "#oa-real-position",
            ].forEach(id => nodes[id] = makeNode());
            for (let index = 0; index < 2; index += 1) {{
              contractCards.push(makeNode());
              contractBadges.push(makeNode());
            }}
            globalThis.document = {{
              visibilityState: "visible",
              querySelector(selector) {{ return nodes[selector] || null; }},
              querySelectorAll(selector) {{
                if (selector === "[data-contract-lock-card]") return contractCards;
                if (selector === "[data-contract-lock-badge]") return contractBadges;
                return [];
              }},
              addEventListener() {{}},
            }};
            globalThis.window = {{
              setInterval() {{}},
              crypto: {{ randomUUID() {{ return "test-id"; }} }},
            }};
            {source}
            state.defaults = {{ settings: {{ paper_starting_balance: 20000 }} }};
            state.status = {{
              settings: {{ mode: "BACKTEST" }},
              data_source: "zerodha_historical",
              live_scan: {{ running: false, mode: "", cycle_count: 0 }},
              session: {{
                status: "BACKTEST_COMPLETE",
                active_trades: [],
                last_decision: {{
                  mode: "BACKTEST",
                  trade_plan: {{ tradingsymbol: "NIFTY26JUN22600CE", entry_price: 40 }},
                  summary: {{ mode: "BACKTEST", trades: [{{ tradingsymbol: "NIFTY26JUN22600CE" }}] }},
                }},
              }},
              paper_lifecycle: {{
                active_trades: [],
                pending_entries: [],
                closed_trades: [{{
                  tradingsymbol: "NIFTY26JUN22600CE",
                  entry_order_id: "STALE-PAPER-ENTRY",
                  exit_reason: "TARGET_FILLED",
                }}],
                account: {{
                  available_balance: 23136.25,
                  orders: [{{ order_id: "STALE-PAPER-ENTRY", tradingsymbol: "NIFTY26JUN22600CE", status: "COMPLETE" }}],
                }},
              }},
              contract_lock: {{
                lock: {{
                  status: "TRADE_EXITED",
                  ce: {{ tradingsymbol: "NIFTY26JUN22600CE" }},
                  pe: {{ tradingsymbol: "NIFTY26JUN22500PE" }},
                }},
              }},
              real_order_lifecycle: {{ state: "IDLE", protected_state: "FLAT", entry_order: {{}}, target_order: {{}}, stoploss_order: {{}}, fill: {{}} }},
              account_status: {{ paper: {{ connected: true }}, real: {{ connected: false }} }},
            }};
            state.lastResult = hydrateStatusDecision(state.status);
            renderTopStatus();
            renderContractLockCards();
            renderPaperAccount();
            renderPaperTrades();
            renderRealPreflight(state.lastResult);

            const lockHtml = contractCards.map(node => node.innerHTML).join("\\n");
            const paperHtml = nodes["#oa-paper-trades"].innerHTML;
            const planHtml = nodes["#oa-paper-plan"].innerHTML;
            const accountHtml = nodes["#oa-paper-account"].innerHTML;
            const realHtml = nodes["#oa-real-position"].innerHTML;
            if (lockHtml.includes("NIFTY26JUN22600CE")) throw new Error("Stopped live lock rendered stale CE: " + lockHtml);
            if (!lockHtml.includes("No live contract lock")) throw new Error("Stopped lock card did not explain live lock state: " + lockHtml);
            if (paperHtml.includes("STALE-PAPER-ENTRY") || paperHtml.includes("Closed Paper Trades")) throw new Error("Stopped paper tab rendered stale trade: " + paperHtml);
            if (!paperHtml.includes("No active paper live session")) throw new Error("Stopped paper tab did not explain stopped state: " + paperHtml);
            if (!planHtml.includes("No current paper live trade plan")) throw new Error("Stopped paper plan rendered stale plan: " + planHtml);
            if (!accountHtml.includes("SESSION NOT STARTED")) throw new Error("Paper account did not show stopped live session: " + accountHtml);
            if (!realHtml.includes("No active real position")) throw new Error("Real position should be empty when lifecycle is idle: " + realHtml);
            if (nodes["#oa-protection"].textContent !== "Inactive") throw new Error("Protection badge should be inactive without position: " + nodes["#oa-protection"].textContent);
            """
        )

        result = self._run_node_script(node, script)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stopped_or_disconnected_real_session_does_not_render_stale_real_orders(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")

        source = (ROOT / "web_static" / "options_auto.js").read_text(encoding="utf-8")
        script = textwrap.dedent(
            f"""
            const nodes = {{}};
            function makeNode(tagName = "DIV") {{
              return {{
                tagName,
                innerHTML: "",
                textContent: "",
                disabled: false,
                dataset: {{}},
                value: "",
                checked: false,
                className: "",
                classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
                addEventListener() {{}},
              }};
            }}
            [
              "#oa-live-feed-badge",
              "#oa-live-feed-panel",
              "#oa-real-lifecycle-badge",
              "#oa-real-lifecycle-panel",
              "#oa-blackbox-badge",
              "#oa-blackbox-panel",
            ].forEach(id => nodes[id] = makeNode());
            globalThis.document = {{
              visibilityState: "visible",
              querySelector(selector) {{ return nodes[selector] || null; }},
              querySelectorAll() {{ return []; }},
              addEventListener() {{}},
            }};
            globalThis.window = {{
              setInterval() {{}},
              crypto: {{ randomUUID() {{ return "test-id"; }} }},
            }};
            {source}
            const staleLifecycle = {{
              state: "OCO_ACTIVE",
              protected_state: "PROTECTIVE_EXIT_ACTIVE",
              entry_order: {{ order_id: "REAL-OLD-ENTRY", status: "COMPLETE", quantity: 65, average_price: 120.5 }},
              target_order: {{ order_id: "REAL-OLD-TARGET", status: "OPEN", price: 150 }},
              stoploss_order: {{ order_id: "REAL-OLD-SL", status: "TRIGGER PENDING", trigger_price: 105 }},
              fill: {{ filled_quantity: 65, average_price: 120.5 }},
              history: [{{ event: "OCO_ACTIVE", order_id: "REAL-OLD-TARGET" }}],
            }};

            state.status = {{
              settings: {{ mode: "REAL" }},
              account_status: {{ paper: {{ connected: false }}, real: {{ connected: false }} }},
              live_scan: {{ running: false, mode: "REAL" }},
              real_order_lifecycle: staleLifecycle,
            }};
            state.lastResult = {{ real_order_lifecycle: staleLifecycle, account_status: state.status.account_status, live_scan: state.status.live_scan }};
            renderIndustryDiagnostics();
            let html = nodes["#oa-real-lifecycle-panel"].innerHTML;
            if (!nodes["#oa-real-lifecycle-badge"].textContent.includes("DISCONNECTED")) throw new Error("Real lifecycle badge should show disconnected: " + nodes["#oa-real-lifecycle-badge"].textContent);
            if (html.includes("REAL-OLD-ENTRY") || html.includes("REAL-OLD-TARGET") || html.includes("REAL-OLD-SL")) throw new Error("Disconnected real lifecycle rendered stale order ids: " + html);

            state.status.account_status.real.connected = true;
            state.lastResult.account_status = state.status.account_status;
            renderIndustryDiagnostics();
            html = nodes["#oa-real-lifecycle-panel"].innerHTML;
            if (!nodes["#oa-real-lifecycle-badge"].textContent.includes("SESSION NOT STARTED")) throw new Error("Real lifecycle badge should show session not started: " + nodes["#oa-real-lifecycle-badge"].textContent);
            if (html.includes("REAL-OLD-ENTRY") || html.includes("REAL-OLD-TARGET") || html.includes("REAL-OLD-SL")) throw new Error("Stopped real lifecycle rendered stale order ids: " + html);
            if (!html.includes("No current real engine session")) throw new Error("Stopped real lifecycle did not explain current state: " + html);
            """
        )

        result = self._run_node_script(node, script)

        self.assertEqual(result.returncode, 0, result.stderr)

    def _node_script(self, source: str, payload: dict, *, expected_symbol: str, status_summary: dict | None = None) -> str:
        status_setup = ""
        if status_summary is not None:
            status_setup = f"""
            state.status = {{ session: {{ last_decision: {{ summary: {json.dumps(status_summary)} }} }} }};
            """
        return textwrap.dedent(
            f"""
            const nodes = {{}};
            function makeNode(tagName = "DIV") {{
              return {{
                tagName,
                innerHTML: "",
                textContent: "",
                disabled: false,
                dataset: {{}},
                value: "",
                className: "",
                classList: {{ add() {{}}, remove() {{}}, toggle() {{}} }},
                addEventListener() {{}},
              }};
            }}
            [
              "#oa-backtest-summary",
              "#oa-backtest-trades",
              "#oa-backtest-alert",
            ].forEach(id => nodes[id] = makeNode());
            nodes["#oa-backtest-export"] = makeNode("BUTTON");
            nodes["#oa-backtest-folder"] = makeNode("BUTTON");
            globalThis.document = {{
              visibilityState: "visible",
              querySelector(selector) {{ return nodes[selector] || null; }},
              querySelectorAll() {{ return []; }},
              addEventListener() {{}},
            }};
            globalThis.window = {{
              setInterval() {{}},
              crypto: {{ randomUUID() {{ return "test-id"; }} }},
            }};
            {source}
            {status_setup}
            renderBacktestResults({json.dumps(payload)});
            const html = nodes["#oa-backtest-trades"].innerHTML;
            if (!html.includes("{expected_symbol}")) throw new Error("Backtest trade row did not render symbol: " + html);
            if (html.includes("No trades generated")) throw new Error("Backtest renderer hid available trades: " + html);
            if (!html.includes("PE")) throw new Error("Backtest renderer did not show derived CE/PE side: " + html);
            if (nodes["#oa-backtest-export"].disabled) throw new Error("Report button should be enabled when report path exists or remain enabled by default in derived mode.");
            """
        )

    def _run_node_script(self, node: str, script: str) -> subprocess.CompletedProcess[str]:
        path = ""
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
                path = handle.name
                handle.write(script)
            return subprocess.run([node, path], cwd=ROOT, text=True, capture_output=True, timeout=20)
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
