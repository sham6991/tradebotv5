import time

import pytest

from main_app.decision_kernel import TradePlan
from main_app.execution import OrderLifecycleEngine, PaperBroker
from main_app.execution.brokers import _validate_policy
from main_app.tick_engine import LatestTickCache, decision_due


def plan():
    return TradePlan(
        underlying_id="NIFTY",
        exchange="NFO",
        tradingsymbol="NIFTY26JUN22500CE",
        side="CE",
        product="NRML",
        quantity=75,
        lots=1,
        lot_size=75,
        entry_order_type="LIMIT",
        entry_limit=100,
        stoploss_order_type="SL",
        stoploss_trigger=92,
        stoploss_limit=90,
        target_order_type="LIMIT",
        target_limit=112,
    )


def test_order_policy_rejects_market_slm_and_non_nrml():
    with pytest.raises(ValueError):
        _validate_policy("BUY", "MARKET", "NRML", 75)
    with pytest.raises(ValueError):
        _validate_policy("SELL", "SL-M", "NRML", 75)
    with pytest.raises(ValueError):
        _validate_policy("BUY", "LIMIT", "MIS", 75)


def test_lifecycle_places_stoploss_before_target_and_records_ledger():
    broker = PaperBroker(100000)
    lifecycle = OrderLifecycleEngine(broker)
    state = lifecycle.submit_entry(plan())
    entry_id = state.entry_order_id
    broker.fill_order(entry_id, 100)
    state = lifecycle.on_entry_filled(plan(), average_price=100, filled_quantity=75)

    assert state.state == "OCO_ACTIVE"
    assert [event["event"] for event in state.events] == [
        "ENTRY_LIMIT_PLACED",
        "STOPLOSS_SL_LIMIT_PLACED",
        "TARGET_LIMIT_PLACED",
    ]
    assert broker.ledger[0]["event_type"] == "OPENING_BALANCE"
    assert any(row["event_type"] == "ENTRY_DEBIT" for row in broker.ledger)


def test_lifecycle_blocks_target_when_stoploss_not_verified():
    class BadStopBroker(PaperBroker):
        def place_sl_limit_sell(self, *args, **kwargs):
            order = super().place_sl_limit_sell(*args, **kwargs)
            order["status"] = "REJECTED"
            return order

    lifecycle = OrderLifecycleEngine(BadStopBroker(100000))
    lifecycle.submit_entry(plan())
    state = lifecycle.on_entry_filled(plan(), average_price=100, filled_quantity=75)

    assert state.state == "PROTECTION_FAILED"
    assert not state.target_order_id


def test_target_and_stoploss_exit_paths_cancel_opposite_order():
    broker = PaperBroker(100000)
    lifecycle = OrderLifecycleEngine(broker)
    lifecycle.submit_entry(plan())
    lifecycle.on_entry_filled(plan(), average_price=100, filled_quantity=75)

    target_state = lifecycle.on_target_filled()
    assert target_state.state == "FLAT_CONFIRMED"
    assert broker.get_order(target_state.stoploss_order_id)["status"] == "CANCELLED"


def test_double_exit_fill_requires_manual_reconciliation():
    lifecycle = OrderLifecycleEngine(PaperBroker(100000))
    state = lifecycle.on_both_exit_orders_filled()

    assert state.state == "MANUAL_RECONCILIATION_REQUIRED"
    assert state.blockers


def test_tick_cache_is_latest_wins_and_records_throttle():
    cache = LatestTickCache(max_queue_size=1)
    cache.on_tick({"instrument_token": 1, "last_price": 100})
    cache.on_tick({"instrument_token": 1, "last_price": 101})
    snapshot = cache.snapshot_latest()

    assert snapshot[1]["last_price"] == 101
    assert cache.metrics_snapshot()["dropped"] >= 1
    cache.mark_processed(1, decision_ms=1200)
    cache.mark_processed(1, decision_ms=1300)
    assert cache.metrics_snapshot()["throttle_mode"]


def test_decision_due_respects_events_and_interval():
    assert decision_due(active_pending_limit_order=True)
    assert not decision_due(last_decision_epoch=time.time(), min_interval_seconds=10)
