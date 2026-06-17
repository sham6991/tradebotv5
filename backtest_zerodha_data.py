from datetime import datetime, time

import pandas as pd

from indicators import clean_and_add_indicators
from main_app.underlyings import get_underlying_spec, normalize_underlying_id
from strategy import ensure_option_formula_columns


MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def parse_trade_date(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("Trade date is required for Zerodha historical backtest data.")
    return pd.to_datetime(text, errors="raise").date()


def market_datetime_range(trade_date):
    date_value = parse_trade_date(trade_date)
    return datetime.combine(date_value, MARKET_OPEN), datetime.combine(date_value, MARKET_CLOSE)


def _required(value, label):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required for Zerodha historical backtest data.")
    return text


def _prepare_index(frame, label, underlying_id):
    if frame is None or frame.empty:
        raise ValueError(f"Zerodha returned no candles for {label}.")
    prepared = clean_and_add_indicators(frame)
    required = {"datetime", "open", "high", "low", "close"}
    missing = required - set(prepared.columns)
    if missing:
        raise ValueError(f"{label} historical candles missing columns: {', '.join(sorted(missing))}.")
    prepared.attrs["instrument"] = normalize_underlying_id(underlying_id)
    prepared.attrs["tradingsymbol"] = label
    return prepared


def _prepare_option(frame, contract, option_type, settings):
    symbol = str(contract.get("tradingsymbol") or f"NIFTY_{option_type}").strip()
    if frame is None or frame.empty:
        raise ValueError(f"Zerodha returned no candles for {symbol}.")
    prepared = clean_and_add_indicators(frame)
    prepared = ensure_option_formula_columns(prepared, settings)
    prepared.attrs["data_kind"] = "option"
    prepared.attrs["instrument"] = symbol
    prepared.attrs["tradingsymbol"] = symbol
    prepared.attrs["option_type"] = str(contract.get("instrument_type") or option_type).upper()
    prepared.attrs["strike"] = str(contract.get("strike", "") or "")
    prepared.attrs["expiry"] = str(contract.get("expiry", "") or "")[:10]
    return prepared


def _contract_row(contract, option_type):
    return {
        "option_type": str(contract.get("instrument_type") or option_type).upper(),
        "tradingsymbol": contract.get("tradingsymbol", ""),
        "instrument_token": contract.get("instrument_token", ""),
        "strike": contract.get("strike", ""),
        "expiry": str(contract.get("expiry", ""))[:10],
    }


def fetch_zerodha_backtest_data(
    zerodha,
    trade_date,
    interval,
    settings,
    call_strike,
    call_expiry,
    put_strike,
    put_expiry,
    underlying_id="NIFTY",
):
    if not zerodha:
        raise ValueError("Connect Virtual/Paper Zerodha first.")

    spec = get_underlying_spec(underlying_id or settings.get("underlying_id"))
    from_dt, to_dt = market_datetime_range(trade_date)
    interval = str(interval or "").strip() or "3minute"

    call_contract = zerodha.find_option_contract(
        option_type="CE",
        strike=_required(call_strike, "Call strike"),
        expiry=_required(call_expiry, "Call expiry"),
        name=spec.underlying_id,
    )
    put_contract = zerodha.find_option_contract(
        option_type="PE",
        strike=_required(put_strike, "Put strike"),
        expiry=_required(put_expiry, "Put expiry"),
        name=spec.underlying_id,
    )
    index_token = int(zerodha.get_index_token(spec.underlying_id) if hasattr(zerodha, "get_index_token") else zerodha.get_nifty50_token())

    index_frame = _prepare_index(
        zerodha.historical_candles(index_token, from_dt, to_dt, interval=interval),
        spec.display_name,
        spec.underlying_id,
    )
    options = [
        _prepare_option(
            zerodha.historical_candles(call_contract["instrument_token"], from_dt, to_dt, interval=interval),
            call_contract,
            "CE",
            settings,
        ),
        _prepare_option(
            zerodha.historical_candles(put_contract["instrument_token"], from_dt, to_dt, interval=interval),
            put_contract,
            "PE",
            settings,
        ),
    ]

    metadata = {
        "data_source": "zerodha_historical",
        "data_source_label": "Zerodha Historical (Virtual/Paper)",
        "trade_date": from_dt.strftime("%Y-%m-%d"),
        "from": from_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "to": to_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "interval": interval,
        "underlying_id": spec.underlying_id,
        "index_token": index_token,
        "nifty_token": index_token,
        "contracts": [_contract_row(call_contract, "CE"), _contract_row(put_contract, "PE")],
    }
    return index_frame, options, metadata
