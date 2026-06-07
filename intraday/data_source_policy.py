from __future__ import annotations

from typing import Any

from .constants import MODE_BACKTEST, MODE_PAPER, MODE_REAL, MODE_REPLAY


class IntradayDataSource:
    PROVIDED = "provided"
    PROVIDED_TEST_DATA = "provided_test_data"
    ZERODHA_PAPER = "zerodha_paper_data"
    ZERODHA_REAL = "zerodha_real_data"
    ZERODHA_CACHED = "zerodha_cached"
    SIMULATED_FALLBACK = "simulated_fallback"
    BACKTEST_DATA = "backtest_data"
    REPLAY_DATA = "replay_data"
    SIMULATED_BACKTEST_DATA = "simulated_backtest_data"
    UNAVAILABLE = "data_unavailable"


def resolve_intraday_data_source(
    mode: str,
    payload: dict | None,
    paper_connected: bool,
    live_connected: bool,
    settings: Any,
) -> dict[str, Any]:
    payload = dict(payload or {})
    mode = str(mode or "").upper()
    has_market_data = bool(payload.get("market_data"))
    allow_simulated = bool(getattr(settings, "allow_simulated_fallback", False))
    require_live_paper = bool(getattr(settings, "require_live_data_for_paper", True))
    websocket_primary = bool(getattr(settings, "websocket_primary_enabled", True))
    live_data_mode = "websocket_tick_candles_preferred" if websocket_primary else "candle_polling"
    manual_context = bool(
        payload.get("allow_provided_market_data")
        or payload.get("debug")
        or payload.get("manual_evaluation")
        or mode in {MODE_BACKTEST, MODE_REPLAY}
    )

    if has_market_data:
        if mode == MODE_REAL and not manual_context:
            return _policy(
                IntradayDataSource.UNAVAILABLE,
                False,
                "ERROR",
                "Provided market data cannot be used for an active REAL intraday session.",
                blockers=["REAL mode requires Zerodha Real Data; provided test data is blocked."],
            )
        return _policy(
            IntradayDataSource.PROVIDED_TEST_DATA if manual_context or mode in {MODE_PAPER, MODE_REAL} else IntradayDataSource.PROVIDED,
            True,
            "WARNING" if mode in {MODE_PAPER, MODE_REAL} else "OK",
            "Market data supplied in request payload.",
            allow_simulated=False,
            warnings=["Provided market data is for testing/manual evaluation, not live Zerodha data."] if mode in {MODE_PAPER, MODE_REAL} else [],
            data_mode="provided_market_data",
        )

    if mode == MODE_PAPER:
        if paper_connected:
            return _policy(
                IntradayDataSource.ZERODHA_PAPER,
                True,
                "OK",
                "Using Zerodha Paper Data connection for market data.",
                requires_fetch=True,
                data_mode=live_data_mode,
            )
        if require_live_paper and not allow_simulated:
            return _policy(
                IntradayDataSource.UNAVAILABLE,
                False,
                "ERROR",
                "Connect Paper Data Zerodha in the main app before starting Intraday Paper.",
                blockers=["Connect Paper Data Zerodha in the main app before starting Intraday Paper."],
            )
        if allow_simulated:
            return _policy(
                IntradayDataSource.SIMULATED_FALLBACK,
                True,
                "WARNING",
                "Simulated fallback is active. This is not live Zerodha market data.",
                allow_simulated=True,
                warnings=["Simulated fallback is active. This is not live Zerodha market data."],
            )
        return _policy(
            IntradayDataSource.UNAVAILABLE,
            False,
            "ERROR",
            "Paper live data is unavailable and simulated fallback is disabled.",
            blockers=["Paper live data is unavailable and simulated fallback is disabled."],
        )

    if mode == MODE_REAL:
        if live_connected:
            return _policy(
                IntradayDataSource.ZERODHA_REAL,
                True,
                "OK",
                "Using Zerodha Real Data connection for market data.",
                requires_fetch=True,
                data_mode=live_data_mode,
            )
        return _policy(
            IntradayDataSource.UNAVAILABLE,
            False,
            "ERROR",
            "Connect Real Money Zerodha in the main app before starting real intraday.",
            blockers=["Connect Real Money Zerodha in the main app before starting real intraday."],
        )

    if mode == MODE_BACKTEST:
        return _policy(IntradayDataSource.BACKTEST_DATA, True, "OK", "Backtest uses isolated historical or simulated backtest data.", allow_simulated=True)
    if mode == MODE_REPLAY:
        return _policy(IntradayDataSource.REPLAY_DATA, True, "OK", "Replay uses isolated historical or simulated replay data.", allow_simulated=True)

    return _policy(IntradayDataSource.UNAVAILABLE, False, "ERROR", "Intraday data source is unavailable.", blockers=["Intraday data source is unavailable."])


def _policy(
    source: str,
    allowed: bool,
    status: str,
    reason: str,
    *,
    requires_fetch: bool = False,
    allow_simulated: bool = False,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    data_mode: str = "candle_polling",
) -> dict[str, Any]:
    order_execution = "Real Zerodha Orders" if source == IntradayDataSource.ZERODHA_REAL else "Paper Simulation"
    return {
        "source": source,
        "allowed": bool(allowed),
        "status": status,
        "reason": reason,
        "requires_fetch": bool(requires_fetch),
        "allow_simulated": bool(allow_simulated),
        "blockers": list(blockers or []),
        "warnings": list(warnings or []),
        "order_execution": order_execution,
        "data_mode": data_mode,
    }
