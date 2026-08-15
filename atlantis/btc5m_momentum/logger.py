"""CSV loggers for the momentum bot - one row per window's entry decision
(BET or NO_BET, whichever it was) and one row per window once resolved.
Mirrors atlantis/btc5m_hedge/logger.py's pattern, much shorter because
there is only ever one decision per window (at open), not one per poll.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Callable

from atlantis.btc5m_momentum.position import Position
from atlantis.btc5m_momentum.signal import Decision

DECISION_FIELDS = [
    "timestamp",
    "window_slug",
    "action",
    "side",
    "momentum_pct",
    "quantity",
    "execution_price",
    "cost",
    "reason",
]

WINDOW_SUMMARY_FIELDS = [
    "window_slug",
    "side",
    "quantity",
    "cost",
    "momentum_pct",
    "realized_outcome",
    "realized_profit",
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
    decision: Decision,
    momentum_pct: Decimal | None,
    position: Position,
) -> None:
    _append(
        path,
        DECISION_FIELDS,
        {
            "timestamp": timestamp,
            "window_slug": window_slug,
            "action": "BET" if decision.should_bet else "NO_BET",
            "side": decision.side or "",
            "momentum_pct": str(momentum_pct) if momentum_pct is not None else "",
            "quantity": str(position.quantity) if position.is_open else "",
            "execution_price": str(position.cost / position.quantity) if position.is_open and position.quantity else "",
            "cost": str(position.cost) if position.is_open else "",
            "reason": decision.reason,
        },
    )


def log_window_summary(
    path: Path,
    *,
    window_slug: str,
    position: Position,
    momentum_pct: Decimal | None,
    realized_outcome: str,
    window_closed_at: str,
) -> None:
    realized_profit = position.realized_profit(realized_outcome or None) if realized_outcome else None
    _append(
        path,
        WINDOW_SUMMARY_FIELDS,
        {
            "window_slug": window_slug,
            "side": position.side or "",
            "quantity": str(position.quantity) if position.is_open else "",
            "cost": str(position.cost) if position.is_open else "",
            "momentum_pct": str(momentum_pct) if momentum_pct is not None else "",
            "realized_outcome": realized_outcome or "",
            "realized_profit": str(realized_profit) if realized_profit is not None else "",
            "window_closed_at": window_closed_at,
        },
    )


def backfill_missing_outcomes(path: Path, fetch_outcome: Callable[[str], str | None]) -> int:
    """Same rationale as atlantis/btc5m_hedge/logger.py's own version -
    a window that just closed almost never has a resolution yet."""
    if not path.exists():
        return 0
    rows = list(csv.DictReader(path.open()))
    updated = 0
    for row in rows:
        if row.get("realized_outcome") or not row.get("side"):
            continue
        outcome = fetch_outcome(row["window_slug"])
        if outcome is None:
            continue
        try:
            quantity = Decimal(row.get("quantity") or "0")
            cost = Decimal(row.get("cost") or "0")
        except Exception:
            continue
        realized = (quantity - cost) if outcome == row["side"] else -cost
        row["realized_outcome"] = outcome
        row["realized_profit"] = str(realized)
        updated += 1
    if updated:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=WINDOW_SUMMARY_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in WINDOW_SUMMARY_FIELDS})
    return updated


def compute_session_realized(path: Path) -> Decimal:
    """Sums every resolved realized_profit in the window-summary file -
    the source of truth for the circuit breaker, recomputed from disk
    rather than accumulated incrementally in memory.

    Regression fix 2026-08-15: the live bot's in-memory session_realized
    was only ever incremented at the single "check resolution right at
    window close" moment, which - per the SAME oracle-lag lesson already
    documented in atlantis/btc5m_hedge/logger.py's backfill_missing_
    outcomes - almost never succeeds. backfill_missing_outcomes fixes the
    outcome in THIS FILE on a later poll, but nothing ever fed that back
    into the in-memory counter, so max_session_loss_usd's circuit breaker
    silently never fired even as real cumulative loss reached -$64.55
    over 6 real days (final session PnL -$35.19, well past the $20
    breaker) - the in-memory value stayed near $0 the whole time.
    Recomputing from the file on every poll instead of tracking a
    separate running total means the breaker can never drift out of sync
    with reality again."""
    if not path.exists():
        return Decimal(0)
    total = Decimal(0)
    for row in csv.DictReader(path.open()):
        if row.get("realized_profit"):
            try:
                total += Decimal(row["realized_profit"])
            except Exception:
                continue
    return total
