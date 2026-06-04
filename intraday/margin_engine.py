from __future__ import annotations

from math import floor
from typing import Any, Callable

from .constants import MODE_REAL
from .order_request import entry_order


def calculate_intraday_equity_quantity(
    symbol,
    exchange,
    side,
    entry_price,
    stoploss_price,
    available_funds,
    max_capital_allocation_percent,
    risk_per_trade_percent,
    estimated_leverage=5,
    mode="REAL",
    margin_calculator: Callable[[Any], Any] | None = None,
    user_max_quantity: int | None = None,
    session_id: str = "",
):
    entry_price = float(entry_price or 0)
    stoploss_price = float(stoploss_price or 0)
    available_funds = float(available_funds or 0)
    allocation_pct = float(max_capital_allocation_percent or 0) / 100.0
    risk_pct = float(risk_per_trade_percent or 0) / 100.0
    leverage = max(1.0, float(estimated_leverage or 1))
    allowed_margin_capital = max(0.0, available_funds * allocation_pct)
    max_loss_allowed = max(0.0, available_funds * risk_pct)
    risk_per_share = abs(entry_price - stoploss_price)

    result = {
        "symbol": str(symbol or "").upper(),
        "exchange": str(exchange or "NSE").upper(),
        "side": str(side or "").upper(),
        "entry_price": entry_price,
        "stoploss_price": stoploss_price,
        "available_funds": available_funds,
        "allowed_margin_capital": allowed_margin_capital,
        "max_loss_allowed": max_loss_allowed,
        "risk_per_share": risk_per_share,
        "estimated_leverage": leverage,
        "leverage_used": leverage,
        "estimated_trade_value": 0.0,
        "estimated_required_margin": 0.0,
        "actual_required_margin": None,
        "margin_based_quantity": 0,
        "risk_based_quantity": 0,
        "user_max_quantity": int(user_max_quantity or 0),
        "final_quantity": 0,
        "trade_value": 0.0,
        "expected_charges": 0.0,
        "net_risk": 0.0,
        "margin_validation_status": "FAILED",
        "rejection_reason": "",
        "raw_margin_response": None,
    }

    if entry_price <= 0:
        return _reject(result, "Entry price must be greater than zero.")
    if risk_per_share <= 0:
        return _reject(result, "Risk per share must be greater than zero.")
    if allowed_margin_capital <= 0:
        return _reject(result, "Allowed Capital for This Trade is zero.")
    if max_loss_allowed <= 0:
        return _reject(result, "Risk per trade is zero.")

    estimated_exposure = allowed_margin_capital * leverage
    margin_based_quantity = floor(estimated_exposure / entry_price)
    risk_based_quantity = floor(max_loss_allowed / risk_per_share)
    candidate_quantity = min(margin_based_quantity, risk_based_quantity)
    if user_max_quantity:
        candidate_quantity = min(candidate_quantity, int(user_max_quantity))

    result.update({
        "estimated_trade_value": estimated_exposure,
        "estimated_required_margin": estimated_exposure / leverage,
        "margin_based_quantity": margin_based_quantity,
        "risk_based_quantity": risk_based_quantity,
        "final_quantity": max(0, candidate_quantity),
    })

    if candidate_quantity <= 0:
        return _reject(result, "Quantity is zero because margin/risk settings do not allow this trade.")

    if str(mode or "").upper() == MODE_REAL:
        if margin_calculator is None:
            return _reject(result, "Margin validation failed. Real order blocked for safety.")
        final = _validate_real_margin(
            result,
            candidate_quantity,
            margin_calculator,
            session_id=session_id,
        )
        if final["final_quantity"] <= 0:
            return _reject(final, final.get("rejection_reason") or "Insufficient margin after Zerodha margin validation.")
        return final

    trade_value = candidate_quantity * entry_price
    actual_required_margin = trade_value / leverage
    if actual_required_margin > allowed_margin_capital:
        candidate_quantity = floor((allowed_margin_capital * leverage) / entry_price)
        if user_max_quantity:
            candidate_quantity = min(candidate_quantity, int(user_max_quantity))
        trade_value = candidate_quantity * entry_price
        actual_required_margin = trade_value / leverage
    if candidate_quantity <= 0 or actual_required_margin > allowed_margin_capital:
        return _reject(result, "Quantity is zero because margin/risk settings do not allow this trade.")
    result.update(_final_values(candidate_quantity, entry_price, actual_required_margin, risk_per_share, leverage))
    result["margin_validation_status"] = "PASSED"
    return result


def _validate_real_margin(result, candidate_quantity, margin_calculator, session_id=""):
    allowed = float(result["allowed_margin_capital"])
    entry_price = float(result["entry_price"])
    risk_per_share = float(result["risk_per_share"])
    quantity = int(candidate_quantity)
    last_response = None
    last_margin = None
    while quantity > 0:
        request = entry_order(
            result["symbol"],
            result["side"],
            quantity,
            entry_price,
            exchange=result["exchange"],
            session_id=session_id,
        )
        try:
            response = margin_calculator(request)
        except Exception as exc:
            result["raw_margin_response"] = {"error": str(exc)}
            return _reject(result, "Margin validation failed. Real order blocked for safety.")
        actual = parse_required_margin(response)
        broker_available = parse_available_funds(response)
        available_limit = min(float(result["available_funds"]), broker_available) if broker_available is not None else float(result["available_funds"])
        last_response = response
        last_margin = actual
        if actual is None:
            result["raw_margin_response"] = response
            return _reject(result, "Margin validation failed. Real order blocked for safety.")
        margin_limit = min(allowed, available_limit)
        if actual <= margin_limit:
            result.update(_final_values(quantity, entry_price, actual, risk_per_share, float(result["estimated_leverage"])))
            result["actual_required_margin"] = actual
            result["raw_margin_response"] = response
            result["margin_validation_status"] = "PASSED"
            return result
        if actual <= 0:
            quantity = 0
        else:
            reduced = floor(quantity * margin_limit / actual)
            quantity = min(quantity - 1, reduced) if reduced >= quantity else reduced
    result["actual_required_margin"] = last_margin
    result["raw_margin_response"] = last_response
    return _reject(result, "Insufficient margin after Zerodha margin validation.")


def parse_required_margin(response) -> float | None:
    if response in ("", None):
        return None
    if isinstance(response, (int, float)):
        return float(response)
    if isinstance(response, list):
        values = [parse_required_margin(item) for item in response]
        values = [value for value in values if value is not None]
        return sum(values) if values else None
    if isinstance(response, dict):
        if "required" in response:
            return float(response.get("required") or 0)
        if "actual_required_margin" in response:
            return float(response.get("actual_required_margin") or 0)
        if "total" in response and not isinstance(response.get("total"), dict):
            return float(response.get("total") or 0)
        if "data" in response:
            return parse_required_margin(response.get("data"))
        total = 0.0
        found = False
        for key in ("span", "exposure", "option_premium", "additional", "bo", "cash", "var"):
            if key in response:
                total += float(response.get(key) or 0)
                found = True
        if found:
            return total
    return None


def parse_available_funds(response) -> float | None:
    if not isinstance(response, dict):
        return None
    for key in ("available", "available_funds", "available_cash"):
        if key in response:
            try:
                return float(response.get(key) or 0)
            except (TypeError, ValueError):
                return None
    data = response.get("data")
    if isinstance(data, dict):
        return parse_available_funds(data)
    return None


def _final_values(quantity: int, entry_price: float, actual_margin: float, risk_per_share: float, leverage: float) -> dict:
    trade_value = int(quantity) * float(entry_price)
    leverage = max(1.0, float(leverage or 1))
    return {
        "final_quantity": int(quantity),
        "trade_value": trade_value,
        "estimated_trade_value": trade_value,
        "estimated_required_margin": trade_value / leverage,
        "actual_required_margin": float(actual_margin),
        "expected_charges": max(1.0, trade_value * 0.0003),
        "net_risk": int(quantity) * float(risk_per_share),
    }


def _reject(result: dict, reason: str) -> dict:
    result["final_quantity"] = 0
    result["margin_validation_status"] = "FAILED"
    result["rejection_reason"] = reason
    return result
