"""CSV loggers - one row per decision (every buy AND every WAIT worth
recording, so the full reasoning trail is reconstructable after the
fact) and one row per market once it resolves (the aggregate outcome).
Field lists match the spec exactly so downstream analysis doesn't need
to guess column meaning.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from atlantis.btc5m_hedge.optimizer import DEFENSIVE_HEDGE, EMERGENCY_HEDGE, PROFIT_HEDGE, HedgeDecision
from atlantis.btc5m_hedge.portfolio import Portfolio

DECISION_FIELDS = [
    "timestamp",
    "window_slug",
    "action",
    "hedge_mode",
    "side",
    "quantity",
    "execution_price",
    "cost_before",
    "cost_after",
    "up_shares_before",
    "up_shares_after",
    "down_shares_before",
    "down_shares_after",
    "profit_up_before",
    "profit_up_after",
    "profit_down_before",
    "profit_down_after",
    "guaranteed_profit_before",
    "guaranteed_profit_after",
    # Worst-case/defensive-hedge fields (2026-08-08 MODE A/B/C spec) -
    # current_/new_ pair with guaranteed_profit_before/after above
    # (worst_case_profit IS guaranteed_profit, see
    # Portfolio.get_worst_case_profit's docstring) but kept as their own
    # named columns since that's the vocabulary MODE B/C decisions are
    # made in, and duplicating them here means a reader never has to
    # remember "oh, worst_case_profit is secretly the same column as
    # guaranteed_profit".
    "current_worst_case_profit",
    "new_worst_case_profit",
    "current_max_loss",
    "new_max_loss",
    "loss_reduction",
    "loss_reduction_pct",
    "seconds_remaining",
    "candidate_quantity",
    "candidate_vwap",
    "reason",
]

WINDOW_SUMMARY_FIELDS = [
    "window_slug",
    "total_cost",
    "up_shares",
    "down_shares",
    "profit_if_up",
    "profit_if_down",
    "guaranteed_profit",
    "realized_outcome",
    "realized_profit",
    "number_of_orders",
    "number_of_profit_hedges",
    "number_of_defensive_hedges",
    "number_of_emergency_hedges",
    "average_up_price",
    "average_down_price",
    "max_capital_used",
    "window_closed_at",
]


def _append(path: Path, fields: list[str], row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def log_decision(
    path: Path,
    *,
    timestamp: str,
    window_slug: str,
    action: str,
    before: Portfolio,
    after: Portfolio,
    hedge_decision: HedgeDecision,
) -> None:
    candidate = hedge_decision.candidate
    side = candidate.side if candidate is not None else None
    quantity = candidate.fill.filled_quantity if candidate is not None else None
    execution_price = candidate.fill.vwap_price if candidate is not None else None
    pct = hedge_decision.loss_reduction_pct

    _append(
        path,
        DECISION_FIELDS,
        {
            "timestamp": timestamp,
            "window_slug": window_slug,
            "action": action,
            "hedge_mode": hedge_decision.hedge_mode,
            "side": side or "",
            "quantity": str(quantity) if quantity is not None else "",
            "execution_price": str(execution_price) if execution_price is not None else "",
            "cost_before": str(before.total_cost),
            "cost_after": str(after.total_cost),
            "up_shares_before": str(before.up_shares),
            "up_shares_after": str(after.up_shares),
            "down_shares_before": str(before.down_shares),
            "down_shares_after": str(after.down_shares),
            "profit_up_before": str(before.get_profit_if_up()),
            "profit_up_after": str(after.get_profit_if_up()),
            "profit_down_before": str(before.get_profit_if_down()),
            "profit_down_after": str(after.get_profit_if_down()),
            "guaranteed_profit_before": str(before.get_guaranteed_profit()),
            "guaranteed_profit_after": str(after.get_guaranteed_profit()),
            "current_worst_case_profit": str(hedge_decision.current_worst_case_profit),
            "new_worst_case_profit": str(hedge_decision.new_worst_case_profit),
            "current_max_loss": str(hedge_decision.current_max_loss),
            "new_max_loss": str(hedge_decision.new_max_loss),
            "loss_reduction": str(hedge_decision.loss_reduction),
            "loss_reduction_pct": str(pct) if pct is not None else "",
            "seconds_remaining": f"{hedge_decision.seconds_remaining:.2f}",
            "candidate_quantity": str(quantity) if quantity is not None else "",
            "candidate_vwap": str(execution_price) if execution_price is not None else "",
            "reason": hedge_decision.reason,
        },
    )


@dataclass
class WindowStats:
    """Accumulates the few things Portfolio itself doesn't track
    (order count, peak capital used, which hedge mode fired) across a
    window's lifetime."""

    number_of_orders: int = 0
    number_of_profit_hedges: int = 0
    number_of_defensive_hedges: int = 0
    number_of_emergency_hedges: int = 0
    max_capital_used: Decimal = Decimal(0)
    defensive_loss_reduction_total: Decimal = Decimal(0)

    def record(self, portfolio_after_order: Portfolio, hedge_mode: str = "", loss_reduction: Decimal | None = None) -> None:
        self.number_of_orders += 1
        self.max_capital_used = max(self.max_capital_used, portfolio_after_order.total_cost)
        if hedge_mode == PROFIT_HEDGE:
            self.number_of_profit_hedges += 1
        elif hedge_mode == DEFENSIVE_HEDGE:
            self.number_of_defensive_hedges += 1
            if loss_reduction is not None:
                self.defensive_loss_reduction_total += loss_reduction
        elif hedge_mode == EMERGENCY_HEDGE:
            self.number_of_emergency_hedges += 1


def log_window_summary(
    path: Path,
    *,
    window_slug: str,
    portfolio: Portfolio,
    stats: WindowStats,
    realized_outcome: str,
    window_closed_at: str,
) -> None:
    if realized_outcome == "Up":
        realized_profit: Decimal | None = portfolio.up_shares - portfolio.total_cost
    elif realized_outcome == "Down":
        realized_profit = portfolio.down_shares - portfolio.total_cost
    else:
        realized_profit = None  # unknown/unresolved - never silently treated as a loss

    _append(
        path,
        WINDOW_SUMMARY_FIELDS,
        {
            "window_slug": window_slug,
            "total_cost": str(portfolio.total_cost),
            "up_shares": str(portfolio.up_shares),
            "down_shares": str(portfolio.down_shares),
            "profit_if_up": str(portfolio.get_profit_if_up()),
            "profit_if_down": str(portfolio.get_profit_if_down()),
            "guaranteed_profit": str(portfolio.get_guaranteed_profit()),
            "realized_outcome": realized_outcome or "",
            "realized_profit": str(realized_profit) if realized_profit is not None else "",
            "number_of_orders": stats.number_of_orders,
            "number_of_profit_hedges": stats.number_of_profit_hedges,
            "number_of_defensive_hedges": stats.number_of_defensive_hedges,
            "number_of_emergency_hedges": stats.number_of_emergency_hedges,
            "average_up_price": str(portfolio.avg_up_cost) if portfolio.avg_up_cost is not None else "",
            "average_down_price": str(portfolio.avg_down_cost) if portfolio.avg_down_cost is not None else "",
            "max_capital_used": str(stats.max_capital_used),
            "window_closed_at": window_closed_at,
        },
    )


def backfill_missing_outcomes(path: Path, fetch_outcome: Callable[[str], str | None]) -> int:
    """Re-checks every window still missing realized_outcome - a single
    check at close time (log_window_summary's caller normally only tries
    once) almost always misses since UMA/oracle resolution lags close by
    anywhere from under a minute to several minutes. Confirmed live
    2026-08-08: every window in the summary log showed realized_outcome
    as "?" because nothing ever went back to check again. Rewrites the
    whole file in place (small - one row per 5-minute window) with any
    newly-resolved outcomes found; returns how many rows were updated so
    a caller can log something useful."""
    if not path.exists():
        return 0
    rows = list(csv.DictReader(path.open()))
    updated = 0
    for row in rows:
        if row.get("realized_outcome"):
            continue
        outcome = fetch_outcome(row["window_slug"])
        if outcome is None:
            continue
        try:
            up_shares = Decimal(row.get("up_shares") or "0")
            down_shares = Decimal(row.get("down_shares") or "0")
            total_cost = Decimal(row.get("total_cost") or "0")
        except Exception:
            continue
        payout = up_shares if outcome == "Up" else down_shares
        row["realized_outcome"] = outcome
        row["realized_profit"] = str(payout - total_cost)
        updated += 1
    if updated:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=WINDOW_SUMMARY_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in WINDOW_SUMMARY_FIELDS})
    return updated
