"""Indian Market Cue Analyzer package.

The package is intentionally read-only with respect to trading execution. It
uses broker data only for market context and never places, modifies, or cancels
orders.
"""

from .router import MarketCueService, get_market_cue_bias

__all__ = ["MarketCueService", "get_market_cue_bias"]
