from bisect import bisect_right
from typing import Any

from strategy import (
    build_scoring_row,
    ensure_option_formula_columns,
    market_trend_signal,
    option_scoring_settings,
)
import re


def parse_option_metadata_from_text(text):
    symbol = str(text or "").upper()
    cleaned = re.sub(r"[^A-Z0-9]+", " ", symbol).strip()
    compact = re.sub(r"[^A-Z0-9]", "", symbol)
    months = {
        "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
        "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
        "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
    }

    metadata = {"strike": "", "expiry": "", "option_type": ""}

    type_match = re.search(r"\b(CE|PE|CALL|PUT)\b", cleaned)
    if type_match:
        option_type = type_match.group(1)
        metadata["option_type"] = "CE" if option_type == "CALL" else ("PE" if option_type == "PUT" else option_type)

    strike_match = re.search(r"\b(\d{4,6})\s*(CE|PE|CALL|PUT)\b", cleaned)
    if not strike_match:
        strike_match = re.search(r"(\d{4,6})(CE|PE|CALL|PUT)", compact)
    if strike_match:
        metadata["strike"] = strike_match.group(1)

    year_match = re.search(r"(20\d{2})\d{4}", compact)
    fallback_year = year_match.group(1) if year_match else ""

    text_expiry = re.search(
        r"\b(\d{1,2})(?:ST|ND|RD|TH)?\s+"
        r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
        r"(?:\s+(20\d{2}|\d{2}))?\b",
        cleaned,
    )
    if text_expiry:
        day, month_text, year = text_expiry.groups()
        year = year or fallback_year
        if year:
            year = f"20{year}" if len(year) == 2 else year
            metadata["expiry"] = f"{year}-{months[month_text]}-{int(day):02d}"

    if not metadata["expiry"]:
        yymmdd_match = re.search(r"(\d{2})(\d{2})(\d{2})(?=\d{4,6}(CE|PE|CALL|PUT))", compact)
        if yymmdd_match:
            yy, mm, dd = yymmdd_match.group(1), yymmdd_match.group(2), yymmdd_match.group(3)
            metadata["expiry"] = f"20{yy}-{mm}-{dd}"

    if not metadata["expiry"]:
        ddmmyy_match = re.search(r"(CE|PE|CALL|PUT)(\d{2})(\d{2})(\d{2})", compact)
        if ddmmyy_match:
            dd, mm, yy = ddmmyy_match.group(2), ddmmyy_match.group(3), ddmmyy_match.group(4)
            metadata["expiry"] = f"20{yy}-{mm}-{dd}"

    return metadata


def option_metadata(option_df, instrument: Any = ""):
    symbol = str(option_df.attrs.get("tradingsymbol") or instrument or option_df.attrs.get("instrument") or "")
    strike = option_df.attrs.get("strike", "")
    expiry = option_df.attrs.get("expiry", "")
    parsed = parse_option_metadata_from_text(symbol)

    if not strike:
        strike = parsed["strike"]
    if not expiry:
        expiry = parsed["expiry"]

    return {
        "strike": strike,
        "expiry": str(expiry)[:10] if expiry not in ("", None) else "",
    }


def row_timestamp(df, index):
    if index >= len(df):
        return None
    row = df.iloc[index]
    value = row.get("datetime", None)
    try:
        import pandas as pd
        if value is not None and not pd.isna(value):
            return pd.to_datetime(value, errors="coerce")
        date = row.get("date", "")
        time = row.get("time", "")
        if date and time:
            return pd.to_datetime(f"{date} {time}", errors="coerce")
        if date:
            return pd.to_datetime(date, errors="coerce")
    except Exception:
        return None
    return None


def timestamp_key(value):
    try:
        import pandas as pd

        parsed = pd.to_datetime(value, errors="coerce")
        if parsed is None or pd.isna(parsed):
            return None
        if getattr(parsed, "tzinfo", None) is not None:
            parsed = parsed.tz_convert(None)
        return int(parsed.value)
    except Exception:
        return None


def build_datetime_index_map(df):
    mapping = {}
    keys = []
    if df is None or "datetime" not in df.columns:
        return mapping, keys
    for index, value in enumerate(df["datetime"]):
        key = timestamp_key(value)
        if key is None:
            continue
        mapping[key] = int(index)
        keys.append(key)
    keys = sorted(set(keys))
    return mapping, keys


def attach_datetime_index_map(df):
    mapping, keys = build_datetime_index_map(df)
    df.attrs["datetime_index_map"] = mapping
    df.attrs["datetime_index_keys"] = keys
    df.attrs["last_datetime_key"] = keys[-1] if keys else None
    return df


def append_datetime_index_key(df, timestamp):
    key = timestamp_key(timestamp)
    if key is None:
        return df
    mapping = df.attrs.setdefault("datetime_index_map", {})
    keys = df.attrs.setdefault("datetime_index_keys", [])
    is_new_key = key not in mapping
    mapping[key] = len(df) - 1
    if not keys or key > keys[-1]:
        keys.append(key)
    elif is_new_key:
        keys.insert(bisect_right(keys, key), key)
    df.attrs["last_datetime_key"] = keys[-1] if keys else key
    return df


class TradingEngine:
    def __init__(self, cooldown):
        self.cooldown = max(0, int(cooldown))
        self.cooldown_until = -1
        self.last_skip_reason = ""

    def _option_type(self, instrument_name):
        name = str(instrument_name).upper()
        if "PE" in name or "PUT" in name:
            return "PE"
        if "CE" in name or "CALL" in name:
            return "CE"
        return None

    def _pick_option(self, options, wanted_type):
        for option_index, opt in enumerate(options):
            instrument = opt.attrs.get("instrument", f"OPTION_{option_index}")
            option_type = opt.attrs.get("option_type") or self._option_type(instrument)
            if option_type == wanted_type:
                return option_index, opt, instrument
        return None, None, None

    def _interval_minutes(self, settings):
        text = str(settings.get("chart_interval", "3minute")).lower()
        if text in ("minute", "1minute", "1 min"):
            return 1
        for value in (2, 3, 5):
            if str(value) in text:
                return value
        return 3

    def _aligned_option_index(self, nifty, option_df, nifty_index, settings):
        import pandas as pd

        key = None
        nifty_keys = nifty.attrs.get("datetime_index_keys") or []
        if 0 <= nifty_index < len(nifty_keys):
            key = nifty_keys[nifty_index]
            nifty_time = None
        else:
            nifty_time = row_timestamp(nifty, nifty_index)
            if nifty_time is None or pd.isna(nifty_time):
                return None
            key = timestamp_key(nifty_time)
        if "datetime" not in option_df.columns:
            return None

        option_map = option_df.attrs.get("datetime_index_map") or {}
        if key is not None and key in option_map:
            return int(option_map[key])

        keys = option_df.attrs.get("datetime_index_keys") or []
        if key is not None and keys:
            interval_ns = int(pd.Timedelta(minutes=self._interval_minutes(settings)).value)
            lower_key = key - interval_ns
            pos = bisect_right(keys, key) - 1
            if pos >= 0 and keys[pos] >= lower_key:
                return int(option_map[keys[pos]])

        if nifty_time is None:
            nifty_time = row_timestamp(nifty, nifty_index)
            if nifty_time is None or pd.isna(nifty_time):
                return None
        option_times = pd.to_datetime(option_df["datetime"], errors="coerce")
        matches = option_times[option_times == nifty_time]
        if matches.empty:
            interval = pd.Timedelta(minutes=self._interval_minutes(settings))
            matches = option_times[(option_times <= nifty_time) & (option_times >= nifty_time - interval)]
        if matches.empty:
            return None
        return int(matches.index[-1])

    def mark_trade_complete(self, exit_index):
        self.cooldown_until = max(self.cooldown_until, int(exit_index) + self.cooldown)

    def evaluate_option_signal(self, option_df, i, settings=None, trend=""):
        if i >= len(option_df):
            return {"buy_entry": "", "score_row": {}}

        min_buy_score = float(getattr(self, "min_buy_score", 75))
        expected_settings = option_scoring_settings(settings)
        if option_df.attrs.get("_option_scoring_settings") != expected_settings:
            option_df = ensure_option_formula_columns(option_df, settings)
        score = build_scoring_row(
            option_df,
            i,
            data_kind="option",
            min_buy_score=min_buy_score,
            scoring_settings=settings,
        )
        if not score:
            return {"buy_entry": "", "score_row": {}}
        if trend:
            score["NIFTY Trend"] = trend
            score["Trend Alignment"] = "PASS"
        return {
            "buy_entry": score.get("Buy Entry", ""),
            "score_row": score,
        }

    def find_trade(self, nifty, options, i, settings):
        self.last_skip_reason = ""
        if i >= len(nifty):
            self.last_skip_reason = "index_out_of_range"
            return None
        if i <= self.cooldown_until:
            self.last_skip_reason = "cooldown_active"
            return None
        bullish_threshold = float(settings.get("bullish_threshold", 16))
        bearish_threshold = float(settings.get("bearish_threshold", -15))
        rsi_bull = float(settings.get("rsi_bull", 55))
        rsi_bear = float(settings.get("rsi_bear", 45))
        rsi_reversal_bullish = float(settings.get("rsi_reversal_bullish", 70))
        rsi_reversal_bearish = float(settings.get("rsi_reversal_bearish", 20))
        self.min_buy_score = float(settings.get("min_buy_score", 75))
        trend, entry_remark = market_trend_signal(
            nifty.iloc[i],
            bullish_threshold,
            bearish_threshold,
            rsi_bull,
            rsi_bear,
            rsi_reversal_bullish,
            rsi_reversal_bearish,
        )
        if trend == "SIDEWAYS":
            self.cooldown_until = i + self.cooldown
            self.last_skip_reason = "sideways_trend"
            return None
        wanted_type = "CE" if trend == "BULLISH" else "PE"
        option_index, option_df, instrument = self._pick_option(options, wanted_type)
        if option_df is None:
            self.last_skip_reason = "missing_option_data"
            return None
        option_i = self._aligned_option_index(nifty, option_df, i, settings)
        if option_i is None:
            self.last_skip_reason = "missing_aligned_option_candle"
            return None

        evaluated = self.evaluate_option_signal(option_df, option_i, settings, trend)
        buy_entry = evaluated["buy_entry"]

        if buy_entry != "BUY":
            reason = evaluated["score_row"].get("Entry Block Reason", "")
            self.last_skip_reason = (
                reason
                or f"buy_score_below_{self.min_buy_score}"
                f"({evaluated['score_row'].get('Buy Score', 0)})"
            )
            return None
        if option_i + 1 >= len(option_df):
            self.last_skip_reason = "next_candle_missing"
            return None

        next_open = float(option_df.iloc[option_i + 1].get("open", option_df.iloc[option_i + 1]["close"]))
        entry_offset = float(settings.get("entry_offset", -2))
        entry_price = max(next_open + entry_offset, 0)
        metadata = option_metadata(option_df, instrument or "")
        self.last_skip_reason = "entry_created"
        return {
            "option": option_df,
            "option_index": option_index,
            "type": wanted_type,
            "instrument": instrument,
            "tradingsymbol": option_df.attrs.get("tradingsymbol", instrument),
            "strike": metadata["strike"],
            "expiry": metadata["expiry"],
            "entry": entry_price,
            "entry_offset": entry_offset,
            "signal_index": option_i,
            "nifty_signal_index": i,
            "entry_index": option_i + 1,
            "target": entry_price + float(settings["profit_points"]),
            "stoploss": entry_price - float(settings["safety_points"]),
            "score_row": evaluated["score_row"],
            "entry_remark": entry_remark,
        }
