from collections import deque
import tkinter as tk

from execution_v2 import Executor
from ui_backtest import BacktestViewMixin
from ui_live import LiveViewMixin
from ui_live_runtime import LiveRuntimeMixin
from ui_replay import ReplayViewMixin
from ui_shared import SharedUIMixin
from ui_theme import PALETTE
from ui_zerodha_auth import ZerodhaAuthMixin


class TradeBotUI(BacktestViewMixin, LiveViewMixin, LiveRuntimeMixin, ReplayViewMixin, ZerodhaAuthMixin, SharedUIMixin):
    def __init__(self, root):
        self.root = root
        self.root.title("TradeBotV3 Control Center")
        self.root.geometry("1360x900")
        self.root.minsize(1180, 760)
        self.root.configure(bg=PALETTE["bg"])
        self.status_text = tk.StringVar(value="Ready")
        self.executor = Executor()
        self.zerodha_client = None
        self.tick_buffer = {"NIFTY": [], "CE": [], "PE": []}
        self.pending_tick_lines = {"NIFTY": deque(), "CE": deque(), "PE": deque()}
        self.latest_tick_by_bucket = {}
        self.tick_render_scheduled = False
        self.tick_render_interval_ms = 250
        self.max_tick_lines_per_render = 250
        self.dashboard_refresh_after_id = None
        self.dashboard_refresh_interval_ms = 1000
        self.margin_refresh_interval_ms = 2000
        self.last_margin_refresh_at = 0
        self.margin_refresh_in_progress = False
        self.tick_outputs = {}
        self.current_token_map = {}
        self.live_log_active_rows = {}
        self.live_order_history_rows = []
        self.live_trade_snapshot = {}
        self._init_zerodha_auth()
        profiles = self._load_settings_profiles()
        self.backtest_settings_values = profiles["backtest"]
        self.paper_settings_values = profiles["paper"]
        self.real_settings_values = profiles["real"]
        self._configure_theme()
        self.show_home()
        self.root.after(500, self.startup_zerodha_auth_check)
