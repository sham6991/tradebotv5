"""Compatibility import for the backtesting execution simulator.

Live paper and Zerodha real-money execution do not use this module. Their
runtime behavior lives in live_session.py.
"""

from backtest_runtime import BacktestTradingCore


class TradingCore(BacktestTradingCore):
    def __init__(self, engine, mode="BACKTEST"):
        super().__init__(engine)
        self.mode = "BACKTEST"

__all__ = ["BacktestTradingCore", "TradingCore"]
