from __future__ import annotations

from typing import Any

from .utils import percent_change, round_to, safe_float


def score_market_cues(raw_data: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    contributions: list[dict[str, Any]] = []
    indian = raw_data.get("indian_market") or {}
    global_data = raw_data.get("global_market") or {}
    flow = raw_data.get("institutional_flow") or {}

    _add_index(contributions, "NIFTY 50", indian.get("NIFTY 50"), 3)
    _add_index(contributions, "BANK NIFTY", indian.get("BANK NIFTY"), 2)
    vix_change = _row_change(indian.get("India VIX"))
    _add(contributions, "Indian", "India VIX", _vix_score(vix_change), vix_change)

    fii = safe_float(flow.get("fii_net"))
    dii = safe_float(flow.get("dii_net"))
    _add(contributions, "Institutional", "FII/FPI cash flow", _fii_score(fii), fii)
    _add(contributions, "Institutional", "DII cash flow", _dii_score(dii), dii)
    _add(contributions, "Institutional", "FII-DII absorption", _flow_extra(fii, dii), {"fii": fii, "dii": dii})

    _add(contributions, "Global", "Nasdaq Futures", _nasdaq_future_score(_global_change(global_data, "Nasdaq Futures")), _global_change(global_data, "Nasdaq Futures"))
    _add(contributions, "Global", "S&P Futures", _sign_score(_global_change(global_data, "S&P Futures"), 1), _global_change(global_data, "S&P Futures"))
    _add(contributions, "Global", "Dow Jones", _sign_score(_global_change(global_data, "Dow Jones"), 0.5), _global_change(global_data, "Dow Jones"))
    _score_group(contributions, "Asia", global_data, ["Nikkei 225", "Hang Seng", "Shanghai"], 0.5, 1)
    _score_group(contributions, "Europe", global_data, ["FTSE 100", "DAX", "CAC 40"], 0.25, 0.5)

    crude = _global_change(global_data, "WTI Crude")
    _add(contributions, "Currency/Commodity/Bond", "WTI Crude", _crude_score(crude), crude)
    _add(contributions, "Currency/Commodity/Bond", "DXY", _inverse_sign_score(_global_change(global_data, "DXY"), 0.5), _global_change(global_data, "DXY"))
    _add(contributions, "Currency/Commodity/Bond", "USD/INR", _usdinr_score(_global_change(global_data, "USD/INR")), _global_change(global_data, "USD/INR"))
    _add(contributions, "Currency/Commodity/Bond", "US 10Y Yield", _yield_score(_global_change(global_data, "US 10Y Yield")), _global_change(global_data, "US 10Y Yield"))

    final_score = round(sum(item["score"] for item in contributions), 2)
    bias = classify_bias(final_score)
    confidence = calculate_confidence(final_score, contributions, validation, flow, raw_data)
    risk_level = calculate_risk_level(final_score, confidence, contributions, validation, flow, raw_data)
    return {
        "final_score": final_score,
        "bias": bias,
        "confidence": confidence,
        "risk_level": risk_level,
        "contributions": contributions,
        "positive_cues": [item for item in contributions if item["score"] > 0],
        "negative_cues": [item for item in contributions if item["score"] < 0],
        "neutral_cues": [item for item in contributions if item["score"] == 0],
        "nifty_zones": generate_zones((indian.get("NIFTY 50") or {}).get("previous_close"), "NIFTY"),
        "banknifty_zones": generate_zones((indian.get("BANK NIFTY") or {}).get("previous_close"), "BANKNIFTY"),
    }


def classify_bias(score: float) -> str:
    if score >= 7:
        return "Strong Bullish"
    if score >= 3:
        return "Mild Bullish"
    if score <= -7:
        return "Strong Bearish"
    if score <= -3:
        return "Mild Bearish"
    return "Sideways / Neutral"


def calculate_confidence(score: float, contributions: list[dict[str, Any]], validation: dict[str, Any], flow: dict[str, Any], raw_data: dict[str, Any]) -> int:
    confidence = 50 + min(abs(score) * 3, 18)
    positives = sum(1 for item in contributions if item["score"] > 0)
    negatives = sum(1 for item in contributions if item["score"] < 0)
    if positives and negatives:
        confidence -= min(12, min(positives, negatives) * 2)
    if flow.get("fii_net") is None and flow.get("dii_net") is None:
        confidence -= 5
    elif flow.get("fii_net") is None or flow.get("dii_net") is None:
        confidence -= 3
    if validation.get("global_available_count", 0) < max(8, int(validation.get("global_total_count", 0) * 0.55)):
        confidence -= 10
    indian = raw_data.get("indian_market") or {}
    if any((indian.get(name) or {}).get("status") == "FAILED" for name in ("NIFTY 50", "BANK NIFTY")):
        confidence -= 15
    if any((indian.get(name) or {}).get("ltp_source") == "historical_fallback" for name in ("NIFTY 50", "BANK NIFTY")):
        confidence -= 8
    if validation.get("global_stale_count", 0):
        confidence -= 5
    if any("older than" in warning.lower() for warning in validation.get("warnings", [])):
        confidence -= 5
    if any((row or {}).get("stale") for row in (raw_data.get("global_market") or {}).values()):
        confidence -= 5
    if validation.get("data_reliability") == "Good":
        confidence += 5
    return int(max(35, min(85, round(confidence))))


def calculate_risk_level(score: float, confidence: int, contributions: list[dict[str, Any]], validation: dict[str, Any], flow: dict[str, Any], raw_data: dict[str, Any]) -> str:
    if validation.get("data_reliability") == "Poor" or confidence <= 45:
        return "High"
    heavy_fii_selling = safe_float(flow.get("fii_net")) is not None and safe_float(flow.get("fii_net")) <= -3000
    crude_score = next((item["score"] for item in contributions if item["name"] == "WTI Crude"), 0)
    vix_score = next((item["score"] for item in contributions if item["name"] == "India VIX"), 0)
    if heavy_fii_selling or crude_score <= -1.5 or vix_score <= -2:
        return "High"
    if abs(score) >= 7 and validation.get("data_reliability") == "Good":
        return "Low"
    return "Medium"


def generate_zones(previous_close: Any, instrument: str) -> dict[str, Any]:
    previous = safe_float(previous_close)
    if previous is None:
        return {"status": "UNAVAILABLE", "warning": "Previous close missing."}
    if instrument == "BANKNIFTY":
        r1, r2, step = 0.0045, 0.0075, 10
    else:
        r1, r2, step = 0.0035, 0.006, 5
    support_1 = round_to(previous * (1 - r1), step)
    support_2 = round_to(previous * (1 - r2), step)
    resistance_1 = round_to(previous * (1 + r1), step)
    resistance_2 = round_to(previous * (1 + r2), step)
    return {
        "status": "OK",
        "previous_close": round_to(previous, step),
        "support_1": support_1,
        "support_2": support_2,
        "resistance_1": resistance_1,
        "resistance_2": resistance_2,
        "no_trade_zone": f"{support_1} to {resistance_1}",
        "bullish_confirmation": resistance_1,
        "bearish_confirmation": support_1,
        "strong_bullish_level": resistance_2,
        "strong_bearish_level": support_2,
    }


def _add(contributions: list[dict[str, Any]], category: str, name: str, score: float, value: Any) -> None:
    contributions.append({"category": category, "name": name, "score": round(float(score or 0), 2), "value": value})


def _add_index(contributions: list[dict[str, Any]], name: str, row: dict[str, Any] | None, strong_score: int) -> None:
    row = row or {}
    change = _row_change(row)
    score = _index_score(change, strong_score)
    contribution = {"category": "Indian", "name": name, "score": round(float(score or 0), 2), "value": change}
    if row.get("ltp_source") == "historical_fallback":
        contribution["score"] = round(contribution["score"] * 0.5, 2)
        contribution["note"] = "Reduced because live Kite LTP was unavailable and historical fallback was used."
    contributions.append(contribution)


def _row_change(row: dict[str, Any] | None) -> float | None:
    row = row or {}
    return safe_float(row.get("percent_change")) if row.get("percent_change") is not None else percent_change(row.get("value"), row.get("previous_close"))


def _global_change(global_data: dict[str, Any], name: str) -> float | None:
    return _row_change(global_data.get(name))


def _index_score(change: float | None, strong_score: int) -> int:
    if change is None:
        return 0
    mild = max(1, strong_score - 1)
    if change >= 0.40:
        return strong_score
    if change >= 0.15:
        return mild
    if change <= -0.40:
        return -strong_score
    if change <= -0.15:
        return -mild
    return 0


def _vix_score(change: float | None) -> int:
    if change is None:
        return 0
    if change < 0:
        return 1
    if change > 4:
        return -2
    if change > 0:
        return -1
    return 0


def _fii_score(value: float | None) -> int:
    if value is None:
        return 0
    if value > 3000:
        return 3
    if value >= 1000:
        return 2
    if value > 0:
        return 1
    if value < -3000:
        return -3
    if value <= -1000:
        return -2
    if value < 0:
        return -1
    return 0


def _dii_score(value: float | None) -> int:
    if value is None:
        return 0
    if value > 2000:
        return 2
    if value >= 500:
        return 1
    if value <= -2000:
        return -2
    if value <= -500:
        return -1
    return 0


def _flow_extra(fii: float | None, dii: float | None) -> float:
    if fii is None or dii is None:
        return 0
    if fii < 0 and dii > 0 and abs(fii) - dii > 1500:
        return -1
    if fii < 0 and dii > 0 and dii >= abs(fii) * 0.65:
        return 0.5
    if fii > 0 and dii > 0:
        return 1
    if fii < 0 and dii < 0:
        return -1
    return 0


def _nasdaq_future_score(change: float | None) -> float:
    if change is None:
        return 0
    if change > 0.25:
        return 1.5
    if change > 0:
        return 1
    if change < -0.25:
        return -1.5
    if change < 0:
        return -1
    return 0


def _sign_score(change: float | None, points: float) -> float:
    if change is None or change == 0:
        return 0
    return points if change > 0 else -points


def _inverse_sign_score(change: float | None, points: float) -> float:
    return -_sign_score(change, points)


def _score_group(contributions: list[dict[str, Any]], category: str, global_data: dict[str, Any], names: list[str], points: float, majority_points: float) -> None:
    signs = []
    for name in names:
        change = _global_change(global_data, name)
        signs.append(1 if change and change > 0 else -1 if change and change < 0 else 0)
        _add(contributions, category, name, _sign_score(change, points), change)
    positive = sum(1 for item in signs if item > 0)
    negative = sum(1 for item in signs if item < 0)
    majority = majority_points if positive >= 2 else -majority_points if negative >= 2 else 0
    _add(contributions, category, f"{category} majority", majority, {"positive": positive, "negative": negative})


def _crude_score(change: float | None) -> float:
    if change is None:
        return 0
    if change <= -2:
        return 1.5
    if change <= -1:
        return 1
    if change >= 2:
        return -1.5
    if change >= 1:
        return -1
    return 0


def _usdinr_score(change: float | None) -> float:
    if change is None:
        return 0
    if change > 0.5:
        return -1
    return -0.5 if change > 0 else 0.5 if change < 0 else 0


def _yield_score(change: float | None) -> float:
    if change is None:
        return 0
    if change > 1:
        return -1
    return -0.5 if change > 0 else 0.5 if change < 0 else 0
