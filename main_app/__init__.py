"""Main App deterministic trading architecture.

This package contains the Main App-only decision, instrument, tick, and
order-lifecycle components.
"""

from main_app.decision_kernel import DecisionKernel, KernelDecision, TradePlan
from main_app.direction_engine import DirectionDecision, DirectionEngine
from main_app.instrument_resolver import InstrumentResolver, InstrumentResolution
from main_app.underlyings import UnderlyingSpec, get_underlying_spec

__all__ = [
    "DecisionKernel",
    "DirectionDecision",
    "DirectionEngine",
    "InstrumentResolution",
    "InstrumentResolver",
    "KernelDecision",
    "TradePlan",
    "UnderlyingSpec",
    "get_underlying_spec",
]
