from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from options_auto.constants import MODE_PAPER, MODE_REAL, MODE_SHADOW
from options_auto.core.clock import iso_now
from options_auto.core.mode_guard import ModeGuard
from options_auto.execution.kite_order_adapter import KiteOrderAdapter
from options_auto.execution.paper_broker import PaperBroker
from options_auto.execution.real_execution_controller import RealExecutionController


@dataclass
class ExecutionGate:
    """Safety gate for order execution."""
    name: str
    status: bool
    message: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "timestamp": self.timestamp,
        }


@dataclass
class ExecutionRequest:
    """Request to execute an order with full context."""
    mode: str
    tradingsymbol: str
    transaction_type: str  # BUY, SELL
    quantity: int
    price: float
    trigger_price: float | None = None
    order_type: str = "LIMIT"  # LIMIT, SL
    exchange: str = "NFO"
    product: str = "MIS"
    tag: str = "OPTIONS_AUTO"
    decision_reason: str = ""
    regime: str = ""
    score: float = 0.0
    timestamp: str = field(default_factory=iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tradingsymbol": self.tradingsymbol,
            "transaction_type": self.transaction_type,
            "quantity": self.quantity,
            "price": self.price,
            "trigger_price": self.trigger_price,
            "order_type": self.order_type,
            "exchange": self.exchange,
            "product": self.product,
            "tag": self.tag,
            "decision_reason": self.decision_reason,
            "regime": self.regime,
            "score": self.score,
            "timestamp": self.timestamp,
        }


class SafeOrderExecutor:
    """Multi-mode order executor with comprehensive safety gates.
    
    Supports:
    - BACKTEST: No orders placed
    - PAPER: Paper broker simulation
    - SHADOW: Shadow mode tracking (dry-run with logging)
    - REAL: Real orders with full safety validation
    """

    def __init__(
        self,
        mode_guard: ModeGuard,
        kite_adapter: KiteOrderAdapter | None = None,
        paper_broker: PaperBroker | None = None,
        real_exec_controller: RealExecutionController | None = None,
    ):
        self.mode_guard = mode_guard
        self.kite_adapter = kite_adapter
        self.paper_broker = paper_broker or PaperBroker()
        self.real_exec_controller = real_exec_controller or RealExecutionController()
        self.execution_history: list[dict[str, Any]] = []
        self.gates_passed: list[ExecutionGate] = []
        self.gates_failed: list[ExecutionGate] = []

    def execute(
        self,
        request: ExecutionRequest,
        preflight_result: dict[str, Any] | None = None,
        force_dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute order based on mode and safety validation."""
        gates = self._check_execution_gates(request, preflight_result)
        all_pass = all(gate.status for gate in gates)

        if force_dry_run or not all_pass:
            return self._dry_run_execution(request, gates, all_pass)

        if request.mode == "BACKTEST":
            return {
                "status": "BACKTEST_MODE",
                "order_id": None,
                "message": "Backtest mode: orders logged only, not executed",
                "request": request.to_dict(),
                "gates": [g.to_dict() for g in gates],
            }

        if request.mode == "PAPER":
            return self._execute_paper(request, gates)

        if request.mode == "SHADOW":
            return self._execute_shadow(request, gates)

        if request.mode == "REAL":
            if not all_pass:
                return {
                    "status": "BLOCKED_BY_GATES",
                    "order_id": None,
                    "message": "Real execution blocked by safety gates",
                    "failed_gates": [g.to_dict() for g in self.gates_failed],
                    "request": request.to_dict(),
                }
            return self._execute_real(request, gates)

        return {
            "status": "UNKNOWN_MODE",
            "order_id": None,
            "message": f"Unknown mode: {request.mode}",
            "request": request.to_dict(),
        }

    def _check_execution_gates(
        self,
        request: ExecutionRequest,
        preflight_result: dict[str, Any] | None = None,
    ) -> list[ExecutionGate]:
        """Check all safety gates before execution."""
        gates = []

        # Gate 1: Mode guard validation
        mode_ok = self.mode_guard.mode == request.mode
        gates.append(
            ExecutionGate(
                "mode_guard",
                mode_ok,
                f"Mode: {self.mode_guard.mode}" if mode_ok else f"Mode mismatch: {self.mode_guard.mode} != {request.mode}",
            )
        )

        # Gate 2: Decision quality
        score_ok = request.score >= 60.0 if request.score else True
        gates.append(
            ExecutionGate(
                "decision_score",
                score_ok,
                f"Score: {request.score}" if score_ok else f"Score too low: {request.score} < 60",
            )
        )

        # Gate 3: Position validation
        qty_ok = int(request.quantity) > 0
        gates.append(
            ExecutionGate(
                "quantity",
                qty_ok,
                f"Quantity: {request.quantity}" if qty_ok else f"Invalid quantity: {request.quantity}",
            )
        )

        # Gate 4: Price validation
        price_ok = float(request.price) > 0
        gates.append(
            ExecutionGate(
                "price",
                price_ok,
                f"Price: {request.price}" if price_ok else f"Invalid price: {request.price}",
            )
        )

        # Gate 5: Market hours (for REAL mode)
        if request.mode == MODE_REAL:
            market_ok = preflight_result.get("evidence", {}).get("checks", {}).get("market_open", True) if preflight_result else True
            gates.append(
                ExecutionGate(
                    "market_hours",
                    market_ok,
                    "Market is open" if market_ok else "Market is closed",
                )
            )

            # Gate 6: Real execution enabled
            real_enabled = preflight_result.get("real_orders_enabled", False) if preflight_result else False
            gates.append(
                ExecutionGate(
                    "real_orders_enabled",
                    real_enabled,
                    "Real orders enabled" if real_enabled else "Real orders disabled in settings",
                )
            )

            # Gate 7: Margin available
            if preflight_result:
                margin_ok = preflight_result.get("evidence", {}).get("checks", {}).get("available_margin", False)
                gates.append(
                    ExecutionGate(
                        "margin_available",
                        bool(margin_ok),
                        "Sufficient margin available" if margin_ok else "Insufficient margin",
                    )
                )

        self.gates_passed = [g for g in gates if g.status]
        self.gates_failed = [g for g in gates if not g.status]

        return gates

    def _dry_run_execution(
        self,
        request: ExecutionRequest,
        gates: list[ExecutionGate],
        all_pass: bool,
    ) -> dict[str, Any]:
        """Record execution as dry-run/simulation."""
        result = {
            "status": "DRY_RUN",
            "order_id": f"DRY_{iso_now().replace(':', '').replace('-', '').replace('.', '')}",
            "message": "Dry-run execution: no real orders placed",
            "gates_passed": len(self.gates_passed),
            "gates_failed": len(self.gates_failed),
            "all_gates_passed": all_pass,
            "request": request.to_dict(),
            "gates": [g.to_dict() for g in gates],
            "timestamp": iso_now(),
        }

        self.execution_history.append(result)
        return result

    def _execute_paper(
        self,
        request: ExecutionRequest,
        gates: list[ExecutionGate],
    ) -> dict[str, Any]:
        """Execute order in paper mode via PaperBroker."""
        paper_order = {
            "symbol": request.tradingsymbol,
            "side": request.transaction_type,
            "quantity": request.quantity,
            "price": request.price,
            "order_type": request.order_type,
            "timestamp": iso_now(),
        }

        result = {
            "status": "PAPER_EXECUTED",
            "order_id": f"PAPER_{len(self.execution_history)}",
            "message": f"Paper order recorded: {request.transaction_type} {request.quantity} @ {request.price}",
            "request": request.to_dict(),
            "paper_order": paper_order,
            "timestamp": iso_now(),
        }

        self.execution_history.append(result)
        return result

    def _execute_shadow(
        self,
        request: ExecutionRequest,
        gates: list[ExecutionGate],
    ) -> dict[str, Any]:
        """Execute order in shadow mode (dry-run with logging)."""
        shadow_record = {
            "would_trade": True,
            "symbol": request.tradingsymbol,
            "side": request.transaction_type,
            "quantity": request.quantity,
            "entry_price": request.price,
            "regime": request.regime,
            "decision_score": request.score,
            "decision_reason": request.decision_reason,
            "timestamp": iso_now(),
        }

        result = {
            "status": "SHADOW_RECORDED",
            "order_id": f"SHADOW_{len(self.execution_history)}",
            "message": f"Shadow trade recorded: {request.transaction_type} {request.quantity} @ {request.price}",
            "request": request.to_dict(),
            "shadow_record": shadow_record,
            "timestamp": iso_now(),
        }

        self.execution_history.append(result)
        return result

    def _execute_real(
        self,
        request: ExecutionRequest,
        gates: list[ExecutionGate],
    ) -> dict[str, Any]:
        """Execute real order via Kite API (requires KiteOrderAdapter)."""
        if not self.kite_adapter:
            return {
                "status": "REAL_UNAVAILABLE",
                "order_id": None,
                "message": "KiteOrderAdapter not available",
                "request": request.to_dict(),
            }

        try:
            if request.order_type == "SL" and request.trigger_price:
                response = self.kite_adapter.place_stoploss_limit(
                    tradingsymbol=request.tradingsymbol,
                    quantity=request.quantity,
                    trigger_price=request.trigger_price,
                    price=request.price,
                    exchange=request.exchange,
                    product=request.product,
                    tag=request.tag,
                )
            elif request.transaction_type == "SELL":
                response = self.kite_adapter.place_target_limit(
                    tradingsymbol=request.tradingsymbol,
                    quantity=request.quantity,
                    price=request.price,
                    exchange=request.exchange,
                    product=request.product,
                    tag=request.tag,
                )
            else:  # BUY
                response = self.kite_adapter.place_entry_limit(
                    tradingsymbol=request.tradingsymbol,
                    quantity=request.quantity,
                    price=request.price,
                    exchange=request.exchange,
                    product=request.product,
                    tag=request.tag,
                )

            order_id = response.get("value") or response.get("order_id")
            result = {
                "status": "REAL_ORDER_PLACED",
                "order_id": order_id,
                "message": f"Real order placed: {request.transaction_type} {request.quantity} {request.tradingsymbol} @ {request.price}",
                "request": request.to_dict(),
                "api_response": response,
                "timestamp": iso_now(),
            }

        except Exception as e:
            result = {
                "status": "REAL_ORDER_FAILED",
                "order_id": None,
                "message": f"Real order failed: {str(e)}",
                "request": request.to_dict(),
                "error": str(e),
                "timestamp": iso_now(),
            }

        self.execution_history.append(result)
        return result

    def snapshot(self) -> dict[str, Any]:
        """Get execution history snapshot."""
        return {
            "total_executions": len(self.execution_history),
            "recent": self.execution_history[-10:],
            "gates_passed": len(self.gates_passed),
            "gates_failed": len(self.gates_failed),
        }
