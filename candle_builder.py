from datetime import datetime
from math import isfinite


class _ActiveCandle:
    __slots__ = ("bucket_id", "datetime", "open", "high", "low", "close", "volume", "last_timestamp")

    def __init__(self, bucket_id, timestamp, price, volume, last_timestamp):
        self.bucket_id = bucket_id
        self.datetime = timestamp
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = volume
        self.last_timestamp = last_timestamp

    def update(self, price, volume, timestamp):
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        self.volume += volume
        self.last_timestamp = timestamp

    def to_dict(self):
        return {
            "datetime": self.datetime,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


class CandleBuilder:
    def __init__(self, interval_minutes, max_keys=4096, drop_out_of_order=True):
        self.interval_minutes = max(1, int(interval_minutes or 1))
        self.interval_seconds = self.interval_minutes * 60
        self.max_keys = max(1, int(max_keys or 1))
        self.drop_out_of_order = bool(drop_out_of_order)
        self.current = {}
        self.last_volume = {}
        self.stats = {
            "received_ticks": 0,
            "accepted_ticks": 0,
            "completed_candles": 0,
            "invalid_ticks": 0,
            "out_of_order_ticks": 0,
            "dropped_key_limit_ticks": 0,
            "volume_reset_ticks": 0,
        }

    def add_tick(self, key, price, timestamp=None, volume=None):
        self.stats["received_ticks"] += 1
        if not key:
            self.stats["invalid_ticks"] += 1
            return None

        timestamp = self._coerce_timestamp(timestamp)
        price = self._coerce_price(price)
        if timestamp is None or price is None:
            self.stats["invalid_ticks"] += 1
            return None

        active = self.current.get(key)
        if active is None and len(self.current) >= self.max_keys:
            self.stats["dropped_key_limit_ticks"] += 1
            return None

        bucket_id, bucket_minutes = self._bucket_id(timestamp)
        candle_volume = self._volume_delta(key, volume)

        if active is None:
            self.current[key] = _ActiveCandle(
                bucket_id,
                self._bucket_start(timestamp, bucket_minutes),
                price,
                candle_volume,
                timestamp,
            )
            self.stats["accepted_ticks"] += 1
            return None

        if self.drop_out_of_order and (bucket_id < active.bucket_id or timestamp < active.last_timestamp):
            self.stats["out_of_order_ticks"] += 1
            return None

        if active.bucket_id != bucket_id:
            completed = active.to_dict()
            self.current[key] = _ActiveCandle(
                bucket_id,
                self._bucket_start(timestamp, bucket_minutes),
                price,
                candle_volume,
                timestamp,
            )
            self.stats["accepted_ticks"] += 1
            self.stats["completed_candles"] += 1
            return completed

        active.update(price, candle_volume, timestamp)
        self.stats["accepted_ticks"] += 1
        return None

    def flush_completed(self, timestamp=None):
        timestamp = self._coerce_timestamp(timestamp)
        if timestamp is None:
            self.stats["invalid_ticks"] += 1
            return []

        cutoff_id, _bucket_minutes = self._bucket_id(timestamp)
        completed = []
        for key, active in list(self.current.items()):
            if active.bucket_id < cutoff_id:
                completed.append((key, active.to_dict()))
                del self.current[key]
        self.stats["completed_candles"] += len(completed)
        return completed

    def snapshot(self, key=None):
        if key is not None:
            active = self.current.get(key)
            return active.to_dict() if active else None
        return {item_key: active.to_dict() for item_key, active in self.current.items()}

    def remove_key(self, key):
        self.current.pop(key, None)
        self.last_volume.pop(key, None)

    def reset(self):
        self.current.clear()
        self.last_volume.clear()
        for key in self.stats:
            self.stats[key] = 0

    def _coerce_timestamp(self, timestamp):
        if timestamp is None:
            return datetime.now()
        if isinstance(timestamp, datetime):
            return self._drop_timezone(timestamp)
        if isinstance(timestamp, (int, float)):
            try:
                return datetime.fromtimestamp(timestamp)
            except (OverflowError, OSError, ValueError):
                return None
        try:
            return self._drop_timezone(datetime.fromisoformat(str(timestamp)))
        except (TypeError, ValueError):
            return None

    def _drop_timezone(self, timestamp):
        if timestamp.tzinfo is not None:
            return timestamp.replace(tzinfo=None)
        return timestamp

    def _coerce_price(self, price):
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        if not isfinite(price) or price <= 0:
            return None
        return price

    def _bucket_id(self, timestamp):
        total_minutes = timestamp.hour * 60 + timestamp.minute
        bucket_minutes = (total_minutes // self.interval_minutes) * self.interval_minutes
        return timestamp.toordinal() * 1440 + bucket_minutes, bucket_minutes

    def _bucket_start(self, timestamp, bucket_minutes=None):
        if bucket_minutes is None:
            _bucket_id, bucket_minutes = self._bucket_id(timestamp)
        return timestamp.replace(
            hour=bucket_minutes // 60,
            minute=bucket_minutes % 60,
            second=0,
            microsecond=0,
        )

    def _volume_delta(self, key, volume):
        if volume in ("", None):
            return 0
        try:
            volume = int(volume)
        except (TypeError, ValueError):
            return 0
        if volume < 0:
            return 0

        previous = self.last_volume.get(key)
        self.last_volume[key] = volume
        if previous is None:
            return 0
        if volume < previous:
            self.stats["volume_reset_ticks"] += 1
            return 0
        return volume - previous
