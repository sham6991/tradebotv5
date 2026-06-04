RISK_PROFILES = {
    "CONSERVATIVE": {
        "score_adjustment": 8,
        "max_capital_per_trade_pct": 10.0,
        "target_multiplier": 1.1,
        "stoploss_multiplier": 0.8,
    },
    "BALANCED": {
        "score_adjustment": 0,
        "max_capital_per_trade_pct": 20.0,
        "target_multiplier": 1.5,
        "stoploss_multiplier": 1.0,
    },
    "AGGRESSIVE": {
        "score_adjustment": -5,
        "max_capital_per_trade_pct": 30.0,
        "target_multiplier": 1.8,
        "stoploss_multiplier": 1.15,
    },
}

