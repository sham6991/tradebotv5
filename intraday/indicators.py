from __future__ import annotations

from typing import Iterable


def _numbers(values: Iterable) -> list[float]:
    result = []
    for value in values:
        try:
            result.append(float(value))
        except (TypeError, ValueError):
            continue
    return result


def ema(values: Iterable, period: int) -> list[float]:
    prices = _numbers(values)
    if not prices:
        return []
    period = max(1, int(period or 1))
    alpha = 2 / (period + 1)
    output = [prices[0]]
    for price in prices[1:]:
        output.append((price * alpha) + (output[-1] * (1 - alpha)))
    return output


def rsi(values: Iterable, period: int = 14) -> list[float]:
    prices = _numbers(values)
    if not prices:
        return []
    period = max(1, int(period or 14))
    if len(prices) == 1:
        return [50.0]
    gains = [max(prices[index] - prices[index - 1], 0.0) for index in range(1, len(prices))]
    losses = [max(prices[index - 1] - prices[index], 0.0) for index in range(1, len(prices))]
    output = [50.0 for _ in prices]
    if len(gains) < period:
        for index in range(1, len(prices)):
            gain_slice = gains[:index]
            loss_slice = losses[:index]
            output[index] = _rsi_from_average(
                sum(gain_slice) / max(1, len(gain_slice)),
                sum(loss_slice) / max(1, len(loss_slice)),
            )
        return output

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    output[period] = _rsi_from_average(avg_gain, avg_loss)
    for price_index in range(period + 1, len(prices)):
        gain = gains[price_index - 1]
        loss = losses[price_index - 1]
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        output[price_index] = _rsi_from_average(avg_gain, avg_loss)
    return output


def atr(candles: list[dict], period: int = 14) -> list[float]:
    if not candles:
        return []
    period = max(1, int(period or 14))
    true_ranges = []
    previous_close = None
    for row in candles:
        high = float(row.get("high") or row.get("close") or 0)
        low = float(row.get("low") or row.get("close") or 0)
        close = float(row.get("close") or 0)
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = close
    output = []
    average = 0.0
    for index, true_range in enumerate(true_ranges):
        if index < period:
            average = sum(true_ranges[: index + 1]) / (index + 1)
        elif index == period:
            average = sum(true_ranges[:period]) / period
            average = ((average * (period - 1)) + true_range) / period
        else:
            average = ((average * (period - 1)) + true_range) / period
        output.append(average)
    return output


def _rsi_from_average(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def relative_volume(candles: list[dict], lookback: int = 20) -> float:
    if not candles:
        return 0.0
    volumes = _numbers(row.get("volume") for row in candles)
    if not volumes:
        return 0.0
    current = volumes[-1]
    history = volumes[-max(2, int(lookback or 20)):-1]
    if not history:
        return 1.0
    average = sum(history) / len(history)
    return current / average if average else 0.0


def candle_state(candles: list[dict]) -> dict:
    if not candles:
        return {"body_pct": 0.0, "trend": "UNKNOWN"}
    current = candles[-1]
    open_price = float(current.get("open") or current.get("close") or 0)
    close_price = float(current.get("close") or open_price)
    high = float(current.get("high") or max(open_price, close_price))
    low = float(current.get("low") or min(open_price, close_price))
    total_range = max(high - low, 0.0001)
    body_pct = abs(close_price - open_price) / total_range
    closes = _numbers(row.get("close") for row in candles[-5:])
    if len(closes) >= 3 and closes[-1] > closes[0]:
        trend = "RISING"
    elif len(closes) >= 3 and closes[-1] < closes[0]:
        trend = "FALLING"
    else:
        trend = "SIDEWAYS"
    return {"body_pct": body_pct, "trend": trend}
