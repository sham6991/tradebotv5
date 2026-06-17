from datetime import datetime

from main_app.decision_kernel import DecisionKernel
from main_app.instrument_resolver import InstrumentResolver
from tests.test_main_app_instrument_resolver import instruments


def spot_rows():
    return [
        {"timestamp": "2026-06-17T09:15:00", "open": 22400, "high": 22420, "low": 22390, "close": 22410},
        {"timestamp": "2026-06-17T09:16:00", "open": 22410, "high": 22440, "low": 22405, "close": 22435},
        {"timestamp": "2026-06-17T09:17:00", "open": 22435, "high": 22470, "low": 22430, "close": 22465},
        {"timestamp": "2026-06-17T09:18:00", "open": 22465, "high": 22500, "low": 22460, "close": 22495},
        {"timestamp": "2026-06-17T09:19:00", "open": 22495, "high": 22520, "low": 22490, "close": 22510},
        {"timestamp": "2026-06-17T09:31:00", "open": 22510, "high": 22580, "low": 22505, "close": 22575},
    ]


def futures_rows():
    return [
        {"open": 22410 + i * 10, "high": 22440 + i * 10, "low": 22400 + i * 10, "close": 22435 + i * 10, "volume": 1000 + i * 100}
        for i in range(11)
    ]


def option_rows():
    return [
        {"open": 100 + i, "high": 108 + i, "low": 99 + i, "close": 107 + i, "volume": 1000 + i * 100}
        for i in range(12)
    ]


def test_kernel_builds_ce_limit_only_trade_plan_from_shared_decision():
    resolution = InstrumentResolver(instruments(), today=datetime(2026, 6, 17).date()).resolve("NIFTY", 22575)
    decision = DecisionKernel().evaluate(
        underlying_id="NIFTY",
        resolution=resolution,
        spot_candles=spot_rows(),
        futures_candles=futures_rows(),
        ce_candles=option_rows(),
        pe_candles=option_rows(),
        settings={"previous_close": 22380, "today_open": 22400, "required_confidence": 65, "lots": 2, "timestamp": "2026-06-17T09:31:00"},
    )

    assert decision.approved
    assert decision.direction.allowed_side == "CE_ONLY"
    assert decision.trade_plan.entry_order_type == "LIMIT"
    assert decision.trade_plan.stoploss_order_type == "SL"
    assert decision.trade_plan.target_order_type == "LIMIT"
    assert decision.trade_plan.product == "NRML"
    assert decision.trade_plan.quantity == 150


def test_kernel_blocks_when_plugin_confidence_is_below_required():
    resolution = InstrumentResolver(instruments(), today=datetime(2026, 6, 17).date()).resolve("NIFTY", 22575)
    decision = DecisionKernel().evaluate(
        underlying_id="NIFTY",
        resolution=resolution,
        spot_candles=spot_rows(),
        futures_candles=futures_rows(),
        ce_candles=option_rows(),
        pe_candles=option_rows(),
        settings={"previous_close": 22380, "today_open": 22400, "required_confidence": 101, "lots": 1, "timestamp": "2026-06-17T09:31:00"},
    )

    assert not decision.approved
    assert any("confidence" in blocker for blocker in decision.blockers)
