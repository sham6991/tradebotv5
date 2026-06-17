import time

from order_state import classify_order_state, normalize_order_status
from runtime_errors import classify_runtime_error


CANCELLED_STATUSES = {"CANCELLED", "CANCELED"}
CANCEL_PENDING_STATUSES = {"CANCEL PENDING", "CANCEL_PENDING"}
CANCEL_RESOLVED_STATUSES = {"COMPLETE", "FILLED", "REJECTED", *CANCELLED_STATUSES}


def classify_order_error(error):
    classification = classify_runtime_error(error, context="order_placement")
    if classification["class"] == "BROKER_MARGIN_ERROR":
        classification = {**classification, "class": "BROKER_REJECTED"}
    return classification


class ZerodhaOrderManager:
    def __init__(self, zerodha=None, mode="PAPER", default_lot_size=75):
        self.zerodha = zerodha
        self.mode = str(mode or "PAPER").upper()
        self.default_lot_size = int(default_lot_size)

    def set_context(self, zerodha=None, mode=None):
        if zerodha is not None:
            self.zerodha = zerodha
        if mode is not None:
            self.mode = str(mode or "PAPER").upper()

    def is_live(self):
        return self.mode == "LIVE"

    def place_order(self, side, tradingsymbol, quantity, product="NRML", order_type="LIMIT", price=None, trigger_price=None):
        side = str(side or "").upper()
        order_type = str(order_type or "LIMIT").upper()
        product = str(product or "NRML").upper()
        policy_error = self._main_app_order_policy_error(order_type, product, quantity)
        if policy_error:
            classification = classify_runtime_error(policy_error, context="order_placement")
            return {
                "status": f"FAILED: {policy_error}",
                "order_id": "",
                "log_status": "",
                "log_data": {},
                "error": policy_error,
                "error_class": classification["class"],
                "error_category": classification["category"],
                "retriable": False,
                "requires_reconciliation": False,
            }
        if not self.is_live():
            return {
                "status": f"PAPER {side}",
                "order_id": "",
                "log_status": "",
                "log_data": {},
                "error": "",
            }
        if not self.zerodha:
            classification = classify_order_error("ZERODHA NOT CONNECTED")
            return {
                "status": "FAILED: ZERODHA NOT CONNECTED",
                "order_id": "",
                "log_status": "",
                "log_data": {},
                "error": "ZERODHA NOT CONNECTED",
                "error_class": classification["class"],
                "error_category": classification["category"],
                "retriable": classification["retriable"],
                "requires_reconciliation": classification["requires_reconciliation"],
            }

        try:
            if order_type == "LIMIT":
                order_id = self.zerodha.place_limit_order(
                    tradingsymbol=tradingsymbol,
                    transaction_type=side,
                    quantity=quantity,
                    price=price,
                    product=product,
                )
                return {
                    "status": f"{side} LIMIT ORDER PLACED",
                    "order_id": order_id,
                    "log_status": "LIMIT PLACED",
                    "log_data": {"quantity": quantity, "price": price},
                    "error": "",
                }
            if order_type == "SL":
                order_id = self.zerodha.place_stoploss_limit_order(
                    tradingsymbol=tradingsymbol,
                    transaction_type=side,
                    quantity=quantity,
                    trigger_price=trigger_price,
                    price=price,
                    product=product,
                )
                return {
                    "status": f"{side} SL ORDER PLACED",
                    "order_id": order_id,
                    "log_status": "SL PLACED",
                    "log_data": {"quantity": quantity, "trigger_price": trigger_price, "price": price},
                    "error": "",
                }

            raise ValueError("Main App allows only LIMIT and SL-LIMIT orders.")
        except Exception as exc:
            classification = classify_order_error(exc)
            return {
                "status": f"FAILED: {exc}",
                "order_id": "",
                "log_status": "",
                "log_data": {},
                "error": str(exc),
                "error_class": classification["class"],
                "error_category": classification["category"],
                "retriable": classification["retriable"],
                "requires_reconciliation": classification["requires_reconciliation"],
            }

    def _main_app_order_policy_error(self, order_type, product, quantity):
        if order_type in {"MARKET", "SL-M", "SLM"}:
            return "Main App is LIMIT-only. MARKET and SL-M are forbidden."
        if order_type not in {"LIMIT", "SL"}:
            return "Main App allows only LIMIT and SL-LIMIT orders."
        if product != "NRML":
            return "Main App product must be NRML."
        try:
            if int(quantity or 0) <= 0:
                return "Quantity must be positive and derived from exact user lots."
        except (TypeError, ValueError):
            return "Quantity must be positive and derived from exact user lots."
        return ""

    def cancel_order(self, order_id, retries=2, retry_delay=0.2):
        if not self.is_live() or not self.zerodha or not order_id:
            return {"cancelled": False, "resolved": False, "status": "", "error": ""}

        last_error = ""
        status = self.order_status(order_id, fallback="UNKNOWN")
        if status in CANCEL_RESOLVED_STATUSES:
            return {"cancelled": status in CANCELLED_STATUSES, "accepted": False, "resolved": True, "status": status, "error": ""}
        if status in CANCEL_PENDING_STATUSES:
            return {"cancelled": False, "accepted": True, "resolved": False, "status": status, "error": ""}

        attempts = max(1, int(retries or 0) + 1)
        accepted = False
        for attempt in range(attempts):
            try:
                self.zerodha.cancel_order(order_id)
                accepted = True
                status = self.order_status(order_id, fallback="UNKNOWN")
                if status in CANCEL_RESOLVED_STATUSES:
                    return {
                        "cancelled": status in CANCELLED_STATUSES,
                        "accepted": True,
                        "resolved": True,
                        "status": status,
                        "error": "",
                        "attempts": attempt + 1,
                    }
                if status in CANCEL_PENDING_STATUSES:
                    return {
                        "cancelled": False,
                        "accepted": True,
                        "resolved": False,
                        "status": status,
                        "error": "",
                        "attempts": attempt + 1,
                    }
            except Exception as exc:
                last_error = str(exc)
                classification = classify_runtime_error(exc, context="order_cancel")
                status = self.order_status(order_id, fallback="UNKNOWN")
                if status in CANCEL_RESOLVED_STATUSES:
                    return {
                        "cancelled": status in CANCELLED_STATUSES,
                        "accepted": accepted,
                        "resolved": True,
                        "status": status,
                        "error": "",
                        "attempts": attempt + 1,
                        "error_class": classification["class"],
                        "error_category": classification["category"],
                    }
                if status in CANCEL_PENDING_STATUSES:
                    return {
                        "cancelled": False,
                        "accepted": True,
                        "resolved": False,
                        "status": status,
                        "error": "",
                        "attempts": attempt + 1,
                        "error_class": classification["class"],
                        "error_category": classification["category"],
                    }
                if attempt < attempts - 1 and retry_delay:
                    time.sleep(float(retry_delay))

        return {
            "cancelled": False,
            "accepted": accepted,
            "resolved": False,
            "status": status,
            "error": last_error,
            "attempts": attempts,
            "error_class": classify_runtime_error(last_error, context="order_cancel")["class"] if last_error else "",
            "error_category": classify_runtime_error(last_error, context="order_cancel")["category"] if last_error else "",
        }

    def modify_stoploss_trigger(self, order_id, trigger_price, quantity=None, price=None, order_type="SL"):
        if not self.is_live() or not self.zerodha or not order_id:
            return {"modified": False, "status": "", "error": ""}
        try:
            if str(order_type or "SL").upper() == "SL":
                self.zerodha.modify_stoploss_limit_order(
                    order_id=order_id,
                    trigger_price=trigger_price,
                    price=price,
                    quantity=quantity,
                )
            else:
                self.zerodha.modify_stoploss_market_order(
                    order_id=order_id,
                    trigger_price=trigger_price,
                    quantity=quantity,
                )
            return {
                "modified": True,
                "status": "MODIFIED",
                "error": "",
                "trigger_price": trigger_price,
                "price": price,
            }
        except Exception as exc:
            classification = classify_runtime_error(exc, context="order_modify")
            return {
                "modified": False,
                "status": "FAILED",
                "error": str(exc),
                "error_class": classification["class"],
                "error_category": classification["category"],
                "trigger_price": trigger_price,
                "price": price,
            }

    def modify_limit_price(self, order_id, price, quantity=None):
        if not self.is_live() or not self.zerodha or not order_id:
            return {"modified": False, "status": "", "error": ""}
        try:
            self.zerodha.modify_limit_order(
                order_id=order_id,
                price=price,
                quantity=quantity,
            )
            return {
                "modified": True,
                "status": "MODIFIED",
                "error": "",
                "price": price,
            }
        except Exception as exc:
            classification = classify_runtime_error(exc, context="order_modify")
            return {
                "modified": False,
                "status": "FAILED",
                "error": str(exc),
                "error_class": classification["class"],
                "error_category": classification["category"],
                "price": price,
            }

    def order_status(self, order_id, fallback="UNKNOWN"):
        if not self.is_live() or not self.zerodha or not order_id:
            return fallback
        status = self.zerodha.order_status(order_id)
        normalized = normalize_order_status(status)
        return normalized if normalized and normalized != "UNKNOWN" else fallback

    def order_details(self, order_id, fallback_quantity=0, fallback_price=0):
        if not self.is_live() or not self.zerodha or not order_id:
            return self._empty_order_details(order_id, fallback_quantity, fallback_price)

        raw = None
        if hasattr(self.zerodha, "get_order"):
            raw = self.zerodha.get_order(order_id)

        if not raw:
            status = self.order_status(order_id, fallback="UNKNOWN")
            average_price = self.average_price(order_id, fallback_price)
            filled_quantity = self.filled_quantity(order_id, 0)
            total_quantity = int(fallback_quantity or filled_quantity or 0)
            pending_quantity = max(total_quantity - filled_quantity, 0)
            details = {
                "order_id": str(order_id or ""),
                "status": status,
                "average_price": average_price,
                "filled_quantity": filled_quantity,
                "pending_quantity": pending_quantity,
                "quantity": total_quantity,
                "cancelled_quantity": 0,
                "is_partial": filled_quantity > 0 and pending_quantity > 0,
                "raw": {},
            }
            details["classified_state"] = classify_order_state(details, role=self._role_from_details(details))
            return details

        total_quantity = self._int_value(raw.get("quantity"), fallback_quantity)
        filled_quantity = self._int_value(raw.get("filled_quantity"), 0)
        pending_quantity = self._int_value(raw.get("pending_quantity"), max(total_quantity - filled_quantity, 0))
        cancelled_quantity = self._int_value(raw.get("cancelled_quantity"), 0)
        average_price = self._float_value(raw.get("average_price") or raw.get("price"), fallback_price)
        status = normalize_order_status(raw.get("status") or "UNKNOWN")

        details = {
            "order_id": str(raw.get("order_id") or order_id or ""),
            "status": status,
            "average_price": average_price,
            "filled_quantity": filled_quantity,
            "pending_quantity": pending_quantity,
            "quantity": total_quantity,
            "cancelled_quantity": cancelled_quantity,
            "is_partial": filled_quantity > 0 and pending_quantity > 0,
            "raw": raw,
        }
        details["classified_state"] = classify_order_state(details, role=self._role_from_details(raw))
        return details

    def average_price(self, order_id, fallback):
        if not self.is_live() or not self.zerodha or not order_id:
            return float(fallback)
        price = self.zerodha.order_average_price(order_id)
        return float(price) if price else float(fallback)

    def filled_quantity(self, order_id, fallback):
        if not self.is_live() or not self.zerodha or not order_id:
            return int(fallback)
        quantity = self.zerodha.order_filled_quantity(order_id)
        return int(quantity) if quantity else int(fallback)

    def lot_size(self, tradingsymbol):
        if not self.zerodha:
            return self.default_lot_size
        try:
            return int(self.zerodha.get_lot_size(tradingsymbol))
        except Exception:
            return self.default_lot_size

    def available_margin(self):
        if not self.zerodha:
            return None
        return self.zerodha.available_margin()

    def _empty_order_details(self, order_id, fallback_quantity, fallback_price):
        quantity = int(fallback_quantity or 0)
        details = {
            "order_id": str(order_id or ""),
            "status": "UNKNOWN",
            "average_price": float(fallback_price or 0),
            "filled_quantity": 0,
            "pending_quantity": quantity,
            "quantity": quantity,
            "cancelled_quantity": 0,
            "is_partial": False,
            "raw": {},
        }
        details["classified_state"] = classify_order_state(details, role="ENTRY")
        return details

    def _int_value(self, value, fallback=0):
        if value in ("", None):
            return int(fallback or 0)
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return int(fallback or 0)

    def _float_value(self, value, fallback=0):
        if value in ("", None):
            return float(fallback or 0)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(fallback or 0)

    def _role_from_details(self, details):
        side = str(details.get("transaction_type") or details.get("side") or "").upper()
        if side == "SELL":
            return "EXIT"
        return "ENTRY"

    def _looks_like_option_symbol(self, tradingsymbol):
        symbol = str(tradingsymbol or "").strip().upper()
        return symbol.endswith(("CE", "PE")) and any(character.isdigit() for character in symbol)
