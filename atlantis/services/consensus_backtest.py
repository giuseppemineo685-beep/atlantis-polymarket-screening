from __future__ import annotations

import csv
import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.sports_traders import as_decimal, as_int, is_copy_target_trade


@dataclass(frozen=True)
class ConsensusSignal:
    condition_id: str
    asset: str
    title: str
    outcome: str
    supporting_wallets: int
    wallets: str
    avg_entry_price: Decimal
    exit_price: Decimal | None
    pct_return: Decimal | None
    resolved: bool
    won: bool | None
    hypothetical_pnl: Decimal


def run_consensus_backtest(
    *,
    settings: Settings,
    wallets_csv: Path,
    statuses: set[str],
    since_days: int,
    min_supporting_wallets: int,
    stake_per_signal: Decimal,
    exclude_wallets: set[str] | None = None,
    max_closed_positions_per_wallet: int = 5000,
) -> list[ConsensusSignal]:
    client = build_client(settings)
    wallets = read_wallets(wallets_csv, statuses=statuses)
    if exclude_wallets:
        wallets = [w for w in wallets if w not in exclude_wallets]
    cutoff = int(time.time()) - since_days * 24 * 60 * 60

    # (conditionId, asset) -> list of (wallet, avgPrice)
    groups: dict[tuple[str, str], list[tuple[str, Decimal]]] = defaultdict(list)
    representative: dict[tuple[str, str], dict[str, Any]] = {}

    for wallet in wallets:
        closed = list(
            client.iter_closed_positions(wallet_address=wallet, max_rows=max_closed_positions_per_wallet)
        )
        for position in closed:
            if not is_copy_target_trade(position):
                continue
            ts = as_int(position.get("timestamp")) or 0
            if ts < cutoff:
                continue
            condition_id = str(position.get("conditionId") or "")
            asset = str(position.get("asset") or "")
            if not condition_id or not asset:
                continue
            avg_price = as_decimal(position.get("avgPrice")) or Decimal("0")
            groups[(condition_id, asset)].append((wallet, avg_price))
            representative[(condition_id, asset)] = position

    signals: list[ConsensusSignal] = []
    for key, rows in groups.items():
        distinct_wallets = {w for w, _ in rows}
        if len(distinct_wallets) < min_supporting_wallets:
            continue
        position = representative[key]
        avg_price = sum(price for _, price in rows) / Decimal(len(rows))
        cur_price = as_decimal(position.get("curPrice"))
        resolved = cur_price in (Decimal("0"), Decimal("1"))
        won = (cur_price == Decimal("1")) if resolved else None
        exit_price = cur_price if resolved else None
        pct_return = ((exit_price / avg_price) - 1) * Decimal("100") if (resolved and avg_price > 0) else None
        if resolved and avg_price > 0:
            shares = stake_per_signal / avg_price
            payout = shares * (cur_price or Decimal("0"))
            hypothetical_pnl = payout - stake_per_signal
        else:
            hypothetical_pnl = Decimal("0")
        signals.append(
            ConsensusSignal(
                condition_id=key[0],
                asset=key[1],
                title=str(position.get("title") or ""),
                outcome=str(position.get("outcome") or ""),
                supporting_wallets=len(distinct_wallets),
                wallets=", ".join(sorted(distinct_wallets)),
                avg_entry_price=avg_price,
                exit_price=exit_price,
                pct_return=pct_return,
                resolved=resolved,
                won=won,
                hypothetical_pnl=hypothetical_pnl,
            )
        )

    signals.sort(key=lambda s: (s.supporting_wallets, s.hypothetical_pnl), reverse=True)
    return signals


def write_consensus_backtest_csv(path: Path, signals: list[ConsensusSignal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(ConsensusSignal.__dataclass_fields__.keys()) + ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for signal in signals:
            row = {field: getattr(signal, field) for field in fields if field != "status"}
            row["status"] = "WIN" if signal.won else ("LOSS" if signal.resolved else "OPEN")
            writer.writerow(row)


def read_wallets(path: Path, *, statuses: set[str]) -> list[str]:
    wallets = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            status = str(row.get("status") or "").strip()
            wallet = str(row.get("wallet") or "").strip().lower()
            if status in statuses and wallet.startswith("0x"):
                wallets.append(wallet)
    return wallets


def format_consensus_backtest(signals: list[ConsensusSignal], *, stake_per_signal: Decimal) -> str:
    resolved_signals = [s for s in signals if s.resolved]
    unresolved = len(signals) - len(resolved_signals)
    wins = sum(1 for s in resolved_signals if s.won)
    losses = sum(1 for s in resolved_signals if not s.won)
    total_pnl = sum(s.hypothetical_pnl for s in resolved_signals)
    total_staked = Decimal(len(resolved_signals)) * stake_per_signal

    lines = [
        f"consensus signals found:     {len(signals)}",
        f"  resolved:                  {len(resolved_signals)}",
        f"  still open/unresolved:     {unresolved}",
        f"  wins:                      {wins}",
        f"  losses:                    {losses}",
        f"  win rate:                  {(wins / len(resolved_signals) * 100) if resolved_signals else 0:.1f}%",
        f"  stake per signal:          ${stake_per_signal:,.2f}",
        f"  total staked (resolved):   ${total_staked:,.2f}",
        f"  hypothetical total PnL:    ${total_pnl:,.2f}",
        f"  hypothetical ROI:          {(total_pnl / total_staked * 100) if total_staked else 0:.2f}%",
        "",
        "supp  outcome       pnl        title",
        "----  ------------  ---------  ----------------------------------------",
    ]
    for signal in signals[:40]:
        status = "WIN" if signal.won else ("LOSS" if signal.resolved else "OPEN")
        lines.append(
            f"{signal.supporting_wallets:>4}  "
            f"{signal.outcome[:12]:<12}  "
            f"{signal.hypothetical_pnl:>8,.2f}  "
            f"[{status}] {signal.title[:50]}"
        )
    return "\n".join(lines)
