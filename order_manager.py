def classify_order_error(error):
    text = str(error or "").strip()
    lower = text.lower()
    if not text:
        return {"class": "", "retriable": False, "requires_reconciliation": False}
    if any(pattern in lower for pattern in ("timed out", "timeout", "read timed", "504", "gateway")):
        return {"class": "UNKNOWN_BROKER_STATE", "retriable": True, "requires_reconciliation": True}
    if any(pattern in lower for pattern in ("connection", "network", "temporarily unavailable", "service unavailable", "503")):
        return {"class": "BROKER_CONNECTION_ERROR", "retriable": True, "requires_reconciliation": False}
    if any(pattern in lower for pattern in ("rejected", "insufficient", "margin", "invalid", "not allowed", "rms")):
        return {"class": "BROKER_REJECTED", "retriable": False, "requires_reconciliation": False}
    if "zerodha not connected" in lower:
        return {"class": "LOCAL_VALIDATION_ERROR", "retriable": False, "requires_reconciliation": False}
    return {"class": "BROKER_ERROR", "retriable": False, "requires_reconciliation": False}


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

    def place_order(self, side, tradingsymbol, quantity, product="NRML", order_type="MARKET", price=None, trigger_price=None):
        side = str(side or "").upper()
        order_type = str(order_type or "MARKET").upper()
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
            if order_type == "SL-M":
                order_id = self.zerodha.place_stoploss_market_order(
                    tradingsymbol=tradingsymbol,
                    transaction_type=side,
                    quantity=quantity,
                    trigger_price=trigger_price,
                    product=product,
                )
                return {
                    "status": f"{side} SL-M ORDER PLACED",
                    "order_id": order_id,
                    "log_status": "SL-M PLACED",
                    "log_data": {"quantity": quantity, "trigger_price": trigger_price},
                    "error": "",
                }

            order_id = self.zerodha.place_market_order(
                tradingsymbol=tradingsymbol,
                transaction_type=side,
                quantity=quantity,
                product=product,
            )
            return {
                "status": f"{side} MARKET ORDER PLACED",
                "order_id": order_id,
                "log_status": "MARKET PLACED",
                "log_data": {"quantity": quantity},
                "error": "",
            }
        except Exception as exc:
            classification = classify_order_error(exc)
            return {
                "status": f"FAILED: {exc}",
                "order_id": "",
                "log_status": "",
                "log_data": {},
                "error": str(exc),
                "error_class": classification["class"],
                "retriable": classification["retriable"],
                "requires_reconciliation": classification["requires_reconciliation"],
            }

    def cancel_order(self, order_id):
        if not self.is_live() or not self.zerodha or not order_id:
            return {"cancelled": False, "error": ""}
        try:
            self.zerodha.cancel_order(order_id)
            return {"cancelled": True, "error": ""}
        except Exception as exc:
            return {"cancelled": False, "error": str(exc)}

    def order_status(self, order_id, fallback="UNKNOWN"):
        if not self.is_live() or not self.zerodha or not order_id:
            return fallback
        status = self.zerodha.order_status(order_id)
        return status if status and status != "UNKNOWN" else fallback

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
            return {
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

        total_quantity = self._int_value(raw.get("quantity"), fallback_quantity)
        filled_quantity = self._int_value(raw.get("filled_quantity"), 0)
        pending_quantity = self._int_value(raw.get("pending_quantity"), max(total_quantity - filled_quantity, 0))
        cancelled_quantity = self._int_value(raw.get("cancelled_quantity"), 0)
        average_price = self._float_value(raw.get("average_price") or raw.get("price"), fallback_price)
        status = str(raw.get("status") or "UNKNOWN").upper()

        return {
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
        return {
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
