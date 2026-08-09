"""Execution backends. Paper mode "fills" exactly what the optimizer's
chosen candidate already simulated against real order-book depth - there
is nothing left to do except record it, since simulate_orderbook_buy
already IS the paper fill model.

Real-money execution is intentionally NOT implemented in this first
version (explicit instruction 2026-08-08: build and validate the paper
bot first). real_execute() raises rather than silently no-op-ing, so a
caller can never accidentally believe money moved when it didn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from atlantis.btc5m_hedge.optimizer import Candidate


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    side: str
    filled_quantity: Decimal
    vwap_price: Decimal | None
    total_cost: Decimal
    mode: str


def execute_paper(candidate: Candidate) -> ExecutionResult:
    return ExecutionResult(
        success=True,
        side=candidate.side,
        filled_quantity=candidate.fill.filled_quantity,
        vwap_price=candidate.fill.vwap_price,
        total_cost=candidate.fill.total_cost,
        mode="paper",
    )


def execute_real(candidate: Candidate) -> ExecutionResult:
    raise NotImplementedError(
        "Ejecucion con dinero real no implementada todavia - primera version es solo paper "
        "(instruccion explicita 2026-08-08: validar el bot en paper antes de tocar capital real)"
    )
