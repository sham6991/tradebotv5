"""Intraday Stocks Terminal package.

The package is intentionally split into small modules so the stocks terminal can
grow without loading the existing options trading web app with new responsibilities.
"""

from .terminal_service import IntradayTerminalService

__all__ = ["IntradayTerminalService"]
