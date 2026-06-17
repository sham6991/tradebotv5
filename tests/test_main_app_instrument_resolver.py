from datetime import date

from main_app.instrument_resolver import InstrumentResolver


def instruments():
    return [
        {"exchange": "NFO", "segment": "NFO-FUT", "tradingsymbol": "NIFTY26JUNFUT", "name": "NIFTY", "instrument_type": "FUT", "expiry": "2026-06-25", "lot_size": 75, "tick_size": 0.05, "instrument_token": 111},
        {"exchange": "NFO", "segment": "NFO-OPT", "tradingsymbol": "NIFTY26JUN22500CE", "name": "NIFTY", "instrument_type": "CE", "expiry": "2026-06-25", "strike": 22500, "lot_size": 75, "tick_size": 0.05, "instrument_token": 112},
        {"exchange": "NFO", "segment": "NFO-OPT", "tradingsymbol": "NIFTY26JUN22500PE", "name": "NIFTY", "instrument_type": "PE", "expiry": "2026-06-25", "strike": 22500, "lot_size": 75, "tick_size": 0.05, "instrument_token": 113},
        {"exchange": "BFO", "segment": "BFO-FUT", "tradingsymbol": "SENSEX26JUNFUT", "name": "SENSEX", "instrument_type": "FUT", "expiry": "2026-06-25", "lot_size": 10, "tick_size": 0.05, "instrument_token": 211},
        {"exchange": "BFO", "segment": "BFO-OPT", "tradingsymbol": "SENSEX26JUN76000CE", "name": "SENSEX", "instrument_type": "CE", "expiry": "2026-06-25", "strike": 76000, "lot_size": 10, "tick_size": 0.05, "instrument_token": 212},
        {"exchange": "BFO", "segment": "BFO-OPT", "tradingsymbol": "SENSEX26JUN76000PE", "name": "SENSEX", "instrument_type": "PE", "expiry": "2026-06-25", "strike": 76000, "lot_size": 10, "tick_size": 0.05, "instrument_token": 213},
        {"exchange": "NFO", "segment": "NFO-FUT", "tradingsymbol": "NIFTY25MAYFUT", "name": "NIFTY", "instrument_type": "FUT", "expiry": "2025-05-29", "lot_size": 75, "tick_size": 0.05, "instrument_token": 999},
    ]


def test_nifty_resolves_current_future_and_atm_options_with_stable_identity():
    resolved = InstrumentResolver(instruments(), today=date(2026, 6, 17)).resolve("NIFTY", 22510)

    assert not resolved.blockers
    assert resolved.spot_quote_key == "NSE:NIFTY 50"
    assert resolved.future["tradingsymbol"] == "NIFTY26JUNFUT"
    assert resolved.ce["quote_key"] == "NFO:NIFTY26JUN22500CE"
    assert resolved.pe["quote_key"] == "NFO:NIFTY26JUN22500PE"
    assert resolved.ce["instrument_token_runtime"] == 112


def test_sensex_resolves_from_instrument_master_without_nifty_fallback():
    resolved = InstrumentResolver(instruments(), today=date(2026, 6, 17)).resolve("SENSEX", 76020)

    assert not resolved.blockers
    assert resolved.spot_quote_key == "BSE:SENSEX"
    assert resolved.future["tradingsymbol"] == "SENSEX26JUNFUT"
    assert resolved.ce["tradingsymbol"].startswith("SENSEX")


def test_missing_sensex_future_blocks_by_default():
    rows = [row for row in instruments() if row["instrument_type"] != "FUT" or row["name"] != "SENSEX"]
    resolved = InstrumentResolver(rows, today=date(2026, 6, 17)).resolve("SENSEX", 76020)

    assert resolved.blockers
    assert "SENSEX futures/options not found" in resolved.blockers[0]


def test_missing_sensex_future_can_warn_when_price_only_enabled():
    rows = [row for row in instruments() if row["instrument_type"] != "FUT" or row["name"] != "SENSEX"]
    resolved = InstrumentResolver(rows, today=date(2026, 6, 17)).resolve(
        "SENSEX",
        76020,
        allow_price_only_when_futures_unavailable=True,
    )

    assert not any("futures/options not found" in blocker for blocker in resolved.blockers)
    assert resolved.warnings
