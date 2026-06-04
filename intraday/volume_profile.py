from __future__ import annotations


def calculate_volume_profile(candles: list[dict], buckets: int = 24, bin_size: float | None = None, tick_size: float | None = None) -> dict:
    if not candles:
        return {"poc": 0.0, "vah": 0.0, "val": 0.0, "nodes": []}
    ranges = []
    for row in candles:
        close = float(row.get("close") or 0)
        high = float(row.get("high") or close or 0)
        low = float(row.get("low") or close or 0)
        volume = float(row.get("volume") or 0)
        if high or low or close:
            ranges.append({"low": min(low, high), "high": max(low, high), "close": close, "volume": volume})
    if not ranges:
        return {"poc": 0.0, "vah": 0.0, "val": 0.0, "nodes": []}
    low = min(row["low"] for row in ranges)
    high = max(row["high"] for row in ranges)
    if high == low:
        return {"poc": high, "vah": high, "val": low, "nodes": [{"price": high, "volume": sum(row["volume"] for row in ranges)}]}
    width = _profile_width(low, high, buckets, bin_size, tick_size)
    bucket_count = max(1, int((high - low) / width) + 1)
    profile = [0.0 for _ in range(bucket_count)]
    for row in ranges:
        start = min(bucket_count - 1, max(0, int((row["low"] - low) / width)))
        end = min(bucket_count - 1, max(start, int((row["high"] - low) / width)))
        touched = max(1, end - start + 1)
        allocated = row["volume"] / touched if touched else row["volume"]
        for index in range(start, end + 1):
            profile[index] += allocated
    poc_index = max(range(bucket_count), key=lambda index: profile[index])
    total_volume = sum(profile)
    target_volume = total_volume * 0.70
    value_low = value_high = poc_index
    running = profile[poc_index]
    while running < target_volume and (value_low > 0 or value_high < bucket_count - 1):
        left_volume = profile[value_low - 1] if value_low > 0 else -1
        right_volume = profile[value_high + 1] if value_high < bucket_count - 1 else -1
        if right_volume >= left_volume:
            value_high += 1
            running += max(0.0, right_volume)
        else:
            value_low -= 1
            running += max(0.0, left_volume)

    def level(index: int) -> float:
        return round(low + (index + 0.5) * width, 4)

    return {
        "poc": level(poc_index),
        "vah": level(value_high),
        "val": level(value_low),
        "nodes": [{"price": level(index), "volume": volume} for index, volume in enumerate(profile)],
    }


def _profile_width(low: float, high: float, buckets: int, bin_size: float | None, tick_size: float | None) -> float:
    explicit = bin_size or tick_size
    if explicit:
        return max(float(explicit), 0.0001)
    buckets = max(4, int(buckets or 24))
    return max((high - low) / buckets, 0.0001)
