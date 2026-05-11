from config import (
    TARGET_PERCENT,
    STOPLOSS_PERCENT
)


class RiskManager:

    def __init__(self):

        self.max_price = 0

    def check_exit(
        self,
        entry,
        current
    ):

        profit_percent = (
            (current - entry) / entry
        ) * 100

        if current > self.max_price:
            self.max_price = current

        # TARGET
        if profit_percent >= TARGET_PERCENT:
            return True

        # STOPLOSS
        if profit_percent <= -STOPLOSS_PERCENT:
            return True

        return False