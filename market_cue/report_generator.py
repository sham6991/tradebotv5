from __future__ import annotations

from typing import Any


def generate_report(raw_data: dict[str, Any], validation: dict[str, Any], scoring: dict[str, Any]) -> dict[str, Any]:
    flow = raw_data.get("institutional_flow") or {}
    indian = raw_data.get("indian_market") or {}
    global_data = raw_data.get("global_market") or {}
    bias = scoring.get("bias", "Sideways / Neutral")
    confidence = scoring.get("confidence", 35)
    risk = scoring.get("risk_level", "High")
    final_score = scoring.get("final_score", 0)

    sections = {
        "Market Opening Bias": (
            f"Overall bias is {bias} with {confidence}% confidence. "
            f"The transparent cue score is {final_score}. Risk level is {risk}."
        ),
        "Data Reliability": _data_reliability_text(validation, raw_data),
        "Global Market Summary": _global_summary(global_data),
        "Indian Market Context": _indian_summary(indian),
        "Institutional Flow Analysis": _flow_summary(flow),
        "Currency, Crude and Bond Impact": _macro_summary(global_data),
        "NIFTY 50 Trading Zones": _zone_text(scoring.get("nifty_zones") or {}),
        "BANK NIFTY Trading Zones": _zone_text(scoring.get("banknifty_zones") or {}),
        "CE/PE Conditional Plan": option_plan(bias),
        "Risk Level": f"Risk is {risk} because the score, data reliability, volatility, and cue alignment were assessed together.",
        "Final View": final_view(bias, confidence, risk),
        "Disclaimer": "This is a market analysis and decision-support tool only. It is not financial advice and must not be used as an automatic trading signal.",
    }
    text = "\n\n".join(f"{title}\n{body}" for title, body in sections.items())
    return {"sections": sections, "report_text": text}


def option_plan(bias: str) -> str:
    if bias == "Strong Bullish":
        return "Prefer bullish setups. CE is considered only after price sustains above resistance, VWAP, or opening high. Avoid chasing a large gap-up. PE is considered only if the bullish view fails and price breaks support."
    if bias == "Mild Bullish":
        return "CE is preferred only after confirmation. Wait for the first 15-minute candle and avoid trading inside the no-trade zone."
    if bias == "Mild Bearish":
        return "PE is preferred only after breakdown confirmation. CE is considered only if the market reclaims resistance with strength."
    if bias == "Strong Bearish":
        return "Prefer bearish setups. PE is considered only after price sustains below support, VWAP, or opening low. Avoid shorting after a very large gap-down unless continuation confirms."
    return "No directional bias. Trade only confirmed breakout or breakdown, reduce quantity, and avoid the first 15 to 30 minutes."


def final_view(bias: str, confidence: int, risk: str) -> str:
    return (
        f"The professional opening view is {bias}. With {confidence}% confidence and {risk.lower()} risk, "
        "treat this as a pre-market framework and wait for live price confirmation before taking any option trade."
    )


def _data_reliability_text(validation: dict[str, Any], raw_data: dict[str, Any]) -> str:
    warnings = validation.get("warnings") or []
    flow = raw_data.get("institutional_flow") or {}
    indian = raw_data.get("indian_market") or {}
    fallback_names = [
        name
        for name, row in indian.items()
        if (row or {}).get("ltp_source") == "historical_fallback"
    ]
    fallback_text = f" Indian fallback used for: {', '.join(fallback_names)}." if fallback_names else ""
    return (
        f"Reliability is {validation.get('data_reliability')}. "
        f"Global values available: {validation.get('global_available_count')}/{validation.get('global_total_count')}. "
        f"FII/DII status: {flow.get('status')} via {flow.get('fetch_mode')}. "
        f"{fallback_text}"
        + ("Warnings: " + "; ".join(warnings) if warnings else "No major reliability warnings.")
    )


def _global_summary(global_data: dict[str, Any]) -> str:
    names = ["Dow Jones", "Nasdaq Futures", "S&P Futures", "Nikkei 225", "Hang Seng", "Shanghai", "FTSE 100", "DAX", "CAC 40"]
    parts = [_cue_sentence(global_data.get(name), name) for name in names if global_data.get(name)]
    return " ".join(parts) or "Global cue data is unavailable."


def _indian_summary(indian: dict[str, Any]) -> str:
    return " ".join(_cue_sentence(indian.get(name), name) for name in ("NIFTY 50", "BANK NIFTY", "India VIX") if indian.get(name)) or "Indian market data is unavailable."


def _flow_summary(flow: dict[str, Any]) -> str:
    return (
        f"FII/FPI net value is {flow.get('fii_net')} and DII net value is {flow.get('dii_net')} {flow.get('units', 'INR crores')}. "
        f"Data date is {flow.get('data_date') or 'missing'}, source is {flow.get('source')}, scope is {flow.get('scope')}, "
        f"segment is {flow.get('segment')}. Treat this as previous trading day cash market flow, not live intraday flow."
    )


def _macro_summary(global_data: dict[str, Any]) -> str:
    names = ["WTI Crude", "DXY", "USD/INR", "US 10Y Yield", "Gold", "Silver"]
    return " ".join(_cue_sentence(global_data.get(name), name) for name in names if global_data.get(name)) or "Macro cue data is unavailable."


def _zone_text(zone: dict[str, Any]) -> str:
    if zone.get("status") != "OK":
        return zone.get("warning", "Trading zones unavailable.")
    return (
        f"Support 1: {zone.get('support_1')}, Support 2: {zone.get('support_2')}, "
        f"Resistance 1: {zone.get('resistance_1')}, Resistance 2: {zone.get('resistance_2')}, "
        f"No-trade zone: {zone.get('no_trade_zone')}, bullish confirmation: {zone.get('bullish_confirmation')}, "
        f"bearish confirmation: {zone.get('bearish_confirmation')}."
    )


def _cue_sentence(row: dict[str, Any] | None, name: str) -> str:
    row = row or {}
    change = row.get("percent_change")
    if change is None:
        return f"{name} is unavailable or missing previous close."
    direction = "positive" if change > 0 else "negative" if change < 0 else "flat"
    suffix = ""
    if row.get("ltp_source") == "historical_fallback":
        suffix = " Live LTP was unavailable, so this is based on daily historical fallback."
    elif row.get("fetch_mode") == "history_fallback":
        suffix = " This uses yfinance daily history fallback."
    return f"{name} is {direction} at {change:.2f}%.{suffix}"
