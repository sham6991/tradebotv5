import pandas as pd


KiteConnect = None
KiteTicker = None


def _load_kiteconnect():
    global KiteConnect, KiteTicker
    if KiteConnect is not None and KiteTicker is not None:
        return

    try:
        from kiteconnect import KiteConnect as ImportedKiteConnect, KiteTicker as ImportedKiteTicker
    except ImportError:
        KiteConnect = None
        KiteTicker = None
        return

    KiteConnect = ImportedKiteConnect
    KiteTicker = ImportedKiteTicker


class ZerodhaClient:
    def __init__(self, api_key, api_secret=None, access_token=None):
        _load_kiteconnect()
        if KiteConnect is None:
            raise ImportError(
                "kiteconnect is not installed. Run: "
                ".\\.venv\\Scripts\\python.exe -m pip install kiteconnect"
            )

        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.kite = KiteConnect(api_key=api_key)
        self.ticker = None
        self._named_tickers = {}
        self._instrument_cache = {}

        if access_token:
            self.kite.set_access_token(access_token)

    def login_url(self):
        return self.kite.login_url()

    def set_access_token(self, access_token):
        self.kite.set_access_token(access_token)
        self.access_token = access_token

    def generate_session(self, request_token):
        if not self.api_secret:
            raise ValueError("API secret is required to generate an access token.")

        data = self.kite.generate_session(
            request_token=request_token,
            api_secret=self.api_secret
        )
        self.kite.set_access_token(data["access_token"])
        self.access_token = data["access_token"]
        return data["access_token"]

    def profile(self):
        return self.kite.profile()

    def place_market_order(
        self,
        tradingsymbol,
        transaction_type,
        quantity,
        exchange=None,
        product=None,
        variety=None,
        validity=None,
        tag=None
    ):
        transaction = self._transaction_type(transaction_type)

        return self.kite.place_order(
            variety=variety or self.kite.VARIETY_REGULAR,
            exchange=exchange or self.kite.EXCHANGE_NFO,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction,
            quantity=int(quantity),
            product=product or getattr(self.kite, "PRODUCT_NRML", "NRML"),
            order_type=self.kite.ORDER_TYPE_MARKET,
            validity=validity or self.kite.VALIDITY_DAY,
            tag=tag
        )

    def place_limit_order(
        self,
        tradingsymbol,
        transaction_type,
        quantity,
        price,
        exchange=None,
        product=None,
        variety=None,
        validity=None,
        tag=None
    ):
        transaction = self._transaction_type(transaction_type)

        return self.kite.place_order(
            variety=variety or self.kite.VARIETY_REGULAR,
            exchange=exchange or self.kite.EXCHANGE_NFO,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction,
            quantity=int(quantity),
            product=product or getattr(self.kite, "PRODUCT_NRML", "NRML"),
            order_type=self.kite.ORDER_TYPE_LIMIT,
            price=float(price),
            validity=validity or self.kite.VALIDITY_DAY,
            tag=tag
        )

    def place_stoploss_market_order(
        self,
        tradingsymbol,
        transaction_type,
        quantity,
        trigger_price,
        exchange=None,
        product=None,
        variety=None,
        validity=None,
        tag=None
    ):
        if self._looks_like_option_symbol(tradingsymbol):
            raise ValueError("SL-M is blocked for option stoploss orders; use SL with trigger and limit price.")
        transaction = self._transaction_type(transaction_type)
        order_type_slm = getattr(self.kite, "ORDER_TYPE_SLM", "SL-M")

        return self.kite.place_order(
            variety=variety or self.kite.VARIETY_REGULAR,
            exchange=exchange or self.kite.EXCHANGE_NFO,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction,
            quantity=int(quantity),
            product=product or getattr(self.kite, "PRODUCT_NRML", "NRML"),
            order_type=order_type_slm,
            trigger_price=float(trigger_price),
            validity=validity or self.kite.VALIDITY_DAY,
            tag=tag
        )

    def place_stoploss_limit_order(
        self,
        tradingsymbol,
        transaction_type,
        quantity,
        trigger_price,
        price,
        exchange=None,
        product=None,
        variety=None,
        validity=None,
        tag=None
    ):
        transaction = self._transaction_type(transaction_type)
        order_type_sl = getattr(self.kite, "ORDER_TYPE_SL", "SL")

        return self.kite.place_order(
            variety=variety or self.kite.VARIETY_REGULAR,
            exchange=exchange or self.kite.EXCHANGE_NFO,
            tradingsymbol=tradingsymbol,
            transaction_type=transaction,
            quantity=int(quantity),
            product=product or getattr(self.kite, "PRODUCT_NRML", "NRML"),
            order_type=order_type_sl,
            trigger_price=float(trigger_price),
            price=float(price),
            validity=validity or self.kite.VALIDITY_DAY,
            tag=tag
        )

    def cancel_order(self, order_id, variety=None):
        return self.kite.cancel_order(
            variety=variety or self.kite.VARIETY_REGULAR,
            order_id=order_id
        )

    def modify_stoploss_market_order(
        self,
        order_id,
        trigger_price,
        variety=None,
        quantity=None,
        price=None,
        order_type=None,
        validity=None,
    ):
        payload = {
            "variety": variety or self.kite.VARIETY_REGULAR,
            "order_id": order_id,
            "trigger_price": float(trigger_price),
        }
        if quantity not in ("", None):
            payload["quantity"] = int(quantity)
        if price not in ("", None):
            payload["price"] = float(price)
        if order_type not in ("", None):
            payload["order_type"] = order_type
        if validity not in ("", None):
            payload["validity"] = validity
        return self.kite.modify_order(**payload)

    def modify_stoploss_limit_order(
        self,
        order_id,
        trigger_price,
        price,
        variety=None,
        quantity=None,
        order_type=None,
        validity=None,
    ):
        payload = {
            "variety": variety or self.kite.VARIETY_REGULAR,
            "order_id": order_id,
            "order_type": order_type or getattr(self.kite, "ORDER_TYPE_SL", "SL"),
            "trigger_price": float(trigger_price),
            "price": float(price),
        }
        if quantity not in ("", None):
            payload["quantity"] = int(quantity)
        if validity not in ("", None):
            payload["validity"] = validity
        return self.kite.modify_order(**payload)

    def modify_limit_order(
        self,
        order_id,
        price,
        variety=None,
        quantity=None,
        order_type=None,
        validity=None,
    ):
        payload = {
            "variety": variety or self.kite.VARIETY_REGULAR,
            "order_id": order_id,
            "order_type": order_type or getattr(self.kite, "ORDER_TYPE_LIMIT", "LIMIT"),
            "price": float(price),
        }
        if quantity not in ("", None):
            payload["quantity"] = int(quantity)
        if validity not in ("", None):
            payload["validity"] = validity
        return self.kite.modify_order(**payload)

    def place_equity_market_order(self, tradingsymbol, transaction_type, quantity):
        return self.place_market_order(
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            exchange=self.kite.EXCHANGE_NSE,
            product=self.kite.PRODUCT_CNC,
            variety=self.kite.VARIETY_REGULAR
        )

    def place_amo_equity_order(self, tradingsymbol, transaction_type, quantity):
        return self.place_market_order(
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            exchange=self.kite.EXCHANGE_NSE,
            product=self.kite.PRODUCT_CNC,
            variety=self.kite.VARIETY_AMO
        )

    def orders(self):
        return self.kite.orders()

    def get_order(self, order_id):
        for order in self.orders():
            if str(order.get("order_id")) == str(order_id):
                return order
        return None

    def order_status(self, order_id):
        order = self.get_order(order_id)
        if not order:
            return "UNKNOWN"
        return str(order.get("status", "UNKNOWN")).upper()

    def order_average_price(self, order_id):
        order = self.get_order(order_id)
        if not order:
            return None
        price = order.get("average_price") or order.get("price")
        try:
            return float(price) if price not in ("", None) else None
        except (TypeError, ValueError):
            return None

    def order_filled_quantity(self, order_id):
        order = self.get_order(order_id)
        if not order:
            return 0
        try:
            return int(order.get("filled_quantity") or order.get("quantity") or 0)
        except (TypeError, ValueError):
            return 0

    def available_margin(self):
        margins = self.kite.margins()
        for segment in ("equity", "commodity"):
            data = margins.get(segment, {}) if isinstance(margins, dict) else {}
            available = data.get("available", {})
            for key in ("live_balance", "cash", "opening_balance", "net"):
                value = available.get(key)
                if value not in ("", None):
                    return float(value)
        return None

    def instruments(self, exchange=None):
        return self.kite.instruments(exchange) if exchange else self.kite.instruments()

    def get_lot_size(self, tradingsymbol, exchange=None):
        if not tradingsymbol:
            raise ValueError("Tradingsymbol is required to fetch lot size.")

        exchange = exchange or self.kite.EXCHANGE_NFO

        if exchange not in self._instrument_cache:
            self._instrument_cache[exchange] = self.kite.instruments(exchange)

        symbol = str(tradingsymbol).upper()

        for instrument in self._instrument_cache[exchange]:
            if str(instrument.get("tradingsymbol", "")).upper() == symbol:
                return int(instrument.get("lot_size") or 1)

        raise ValueError(f"Lot size not found for {tradingsymbol} on {exchange}.")

    def get_nifty50_token(self):
        for instrument in self._get_cached_instruments(self.kite.EXCHANGE_NSE):
            if str(instrument.get("tradingsymbol", "")).upper() == "NIFTY 50":
                return int(instrument["instrument_token"])
        return 256265

    def find_option_contract(self, option_type, strike, expiry=None, name="NIFTY"):
        option_type = str(option_type).upper()
        strike = float(str(strike).replace(",", ""))
        wanted_name = str(name).upper()
        matches = []
        wanted_expiry = self._normalise_expiry(expiry) if expiry else None

        for instrument in self._get_cached_instruments(self.kite.EXCHANGE_NFO):
            if str(instrument.get("instrument_type", "")).upper() != option_type:
                continue
            if str(instrument.get("segment", "")).upper() != "NFO-OPT":
                continue
            if float(instrument.get("strike") or 0) != strike:
                continue

            instrument_name = str(instrument.get("name", "")).upper()
            tradingsymbol = str(instrument.get("tradingsymbol", "")).upper()

            if instrument_name != wanted_name and not tradingsymbol.startswith(wanted_name):
                continue

            if wanted_expiry:
                if self._normalise_expiry(instrument.get("expiry")) != wanted_expiry:
                    continue

            matches.append(instrument)

        if not matches:
            raise ValueError(
                f"No {wanted_name} {option_type} contract found for strike {strike}"
                + (f" and expiry {expiry}." if expiry else ".")
            )

        matches.sort(key=lambda item: str(item.get("expiry")))
        return matches[0]

    def get_option_expiries(self, option_type=None, strike=None, name="NIFTY"):
        wanted_name = str(name).upper()
        wanted_type = str(option_type).upper() if option_type else None
        wanted_strike = float(str(strike).replace(",", "")) if strike else None
        expiries = set()

        for instrument in self._get_cached_instruments(self.kite.EXCHANGE_NFO):
            if str(instrument.get("segment", "")).upper() != "NFO-OPT":
                continue
            if wanted_type and str(instrument.get("instrument_type", "")).upper() != wanted_type:
                continue
            if wanted_strike is not None and float(instrument.get("strike") or 0) != wanted_strike:
                continue

            instrument_name = str(instrument.get("name", "")).upper()
            tradingsymbol = str(instrument.get("tradingsymbol", "")).upper()

            if instrument_name != wanted_name and not tradingsymbol.startswith(wanted_name):
                continue

            expiry = self._normalise_expiry(instrument.get("expiry"))
            if expiry:
                expiries.add(expiry)

        return sorted(expiries)

    def _normalise_expiry(self, expiry):
        if not expiry:
            return ""
        if hasattr(expiry, "strftime"):
            return expiry.strftime("%Y-%m-%d")
        return str(expiry)[:10]

    def _get_cached_instruments(self, exchange):
        if exchange not in self._instrument_cache:
            self._instrument_cache[exchange] = self.kite.instruments(exchange)
        return self._instrument_cache[exchange]

    def _looks_like_option_symbol(self, tradingsymbol):
        symbol = str(tradingsymbol or "").strip().upper()
        return symbol.endswith(("CE", "PE")) and any(character.isdigit() for character in symbol)

    def historical_candles(self, instrument_token, from_date, to_date, interval="5minute"):
        candles = self.kite.historical_data(
            instrument_token=int(instrument_token),
            from_date=from_date,
            to_date=to_date,
            interval=interval
        )
        return pd.DataFrame(candles)

    def start_ticker(
        self,
        instrument_tokens,
        on_ticks,
        on_connect=None,
        on_close=None,
        on_error=None,
        on_reconnect=None,
        on_noreconnect=None,
        on_order_update=None,
    ):
        self.ticker = self._create_ticker(
            instrument_tokens,
            on_ticks,
            on_connect=on_connect,
            on_close=on_close,
            on_error=on_error,
            on_reconnect=on_reconnect,
            on_noreconnect=on_noreconnect,
            on_order_update=on_order_update,
        )
        return self.ticker

    def start_named_ticker(
        self,
        name,
        instrument_tokens,
        on_ticks,
        on_connect=None,
        on_close=None,
        on_error=None,
        on_reconnect=None,
        on_noreconnect=None,
        on_order_update=None,
    ):
        name = str(name or "default")
        self.stop_named_ticker(name)
        ticker = self._create_ticker(
            instrument_tokens,
            on_ticks,
            on_connect=on_connect,
            on_close=on_close,
            on_error=on_error,
            on_reconnect=on_reconnect,
            on_noreconnect=on_noreconnect,
            on_order_update=on_order_update,
        )
        if not hasattr(self, "_named_tickers"):
            self._named_tickers = {}
        self._named_tickers[name] = ticker
        return ticker

    def _create_ticker(
        self,
        instrument_tokens,
        on_ticks,
        on_connect=None,
        on_close=None,
        on_error=None,
        on_reconnect=None,
        on_noreconnect=None,
        on_order_update=None,
    ):
        if KiteTicker is None:
            _load_kiteconnect()
        if KiteTicker is None:
            raise ImportError(
                "KiteTicker is not available. Run: "
                ".\\.venv\\Scripts\\python.exe -m pip install kiteconnect"
            )

        if not self.access_token:
            raise ValueError("Access token is required before starting KiteTicker.")

        tokens = [int(token) for token in instrument_tokens]
        ticker = KiteTicker(self.api_key, self.access_token)

        def handle_connect(ws, response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)
            if on_connect:
                on_connect(response)

        def handle_close(ws, code, reason):
            if on_close:
                on_close(code, reason)

        def handle_order_update(ws, order):
            if not on_order_update:
                return
            try:
                on_order_update(order)
            except TypeError:
                on_order_update(ws, order)

        ticker.on_ticks = lambda ws, ticks: on_ticks(ticks)
        ticker.on_connect = handle_connect
        ticker.on_close = handle_close
        ticker.on_error = lambda ws, code, reason: on_error(code, reason) if on_error else None
        ticker.on_reconnect = lambda ws, attempts_count: on_reconnect(attempts_count) if on_reconnect else None
        ticker.on_noreconnect = lambda ws: on_noreconnect() if on_noreconnect else None
        if on_order_update:
            ticker.on_order_update = handle_order_update
        ticker.connect(threaded=True)
        return ticker

    def stop_ticker(self):
        if self.ticker:
            self.ticker.close()
            self.ticker = None

    def stop_named_ticker(self, name):
        tickers = getattr(self, "_named_tickers", None)
        if not tickers:
            return
        ticker = tickers.pop(str(name or "default"), None)
        if ticker:
            ticker.close()

    def stop_all_tickers(self):
        self.stop_ticker()
        tickers = getattr(self, "_named_tickers", None)
        if not tickers:
            return
        for ticker in list(tickers.values()):
            try:
                ticker.close()
            except Exception:
                pass
        tickers.clear()

    def _transaction_type(self, transaction_type):
        value = str(transaction_type).upper()
        if value == "BUY":
            return self.kite.TRANSACTION_TYPE_BUY
        if value == "SELL":
            return self.kite.TRANSACTION_TYPE_SELL
        raise ValueError("transaction_type must be BUY or SELL.")
