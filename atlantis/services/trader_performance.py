from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.sports_traders import as_decimal, as_int, is_copy_target_trade

# Windows measured for the swap-decision heuristic. Kept short: the whole
# point is to catch a trader going cold *recently*, not to re-litigate their
# lifetime track record (that's what the initial vetting/backtest already
# covered).
WINDOWS_DAYS = (7, 30)

# A trader only gets flagged DECLINING once there are enough resolved bets
# in the 7d window to mean something - a couple of losing coin-flip UFC
# bets shouldn't be enough to bench someone.
MIN_SAMPLE_FOR_FLAG = 5


@dataclass(frozen=True)
class WindowStats:
    resolved_count: int
    wins: int
    losses: int
    win_rate: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class TraderPerformance:
    label: str
    wallet_address: str
    flag: str  # DECLINING | LOW_SAMPLE | OK
    windows: dict[int, WindowStats]


def _window_stats(closed_positions: list[dict], *, cutoff_ts: int) -> WindowStats:
    resolved = [p for p in closed_positions if (as_int(p.get("timestamp")) or 0) >= cutoff_ts]
    wins = sum(1 for p in resolved if (as_decimal(p.get("realizedPnl")) or Decimal("0")) > 0)
    losses = len(resolved) - wins
    realized_pnl = sum((as_decimal(p.get("realizedPnl")) or Decimal("0")) for p in resolved)
    win_rate = (Decimal(wins) / Decimal(len(resolved)) * 100) if resolved else Decimal("0")
    return WindowStats(
        resolved_count=len(resolved),
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        realized_pnl=realized_pnl,
    )


def read_labeled_wallets(path: Path, *, statuses: set[str]) -> list[tuple[str, str]]:
    wallets = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            status = str(row.get("status") or "").strip()
            wallet = str(row.get("wallet") or "").strip().lower()
            label = str(row.get("label") or "").strip()
            if status in statuses and wallet.startswith("0x"):
                wallets.append((label, wallet))
    return wallets


def compute_trader_performance(
    *,
    settings: Settings,
    wallets_csv: Path,
    statuses: set[str],
    max_closed_positions_per_wallet: int = 5000,
    min_sample_for_flag: int = MIN_SAMPLE_FOR_FLAG,
) -> list[TraderPerformance]:
    client = build_client(settings)
    wallets = read_labeled_wallets(wallets_csv, statuses=statuses)
    now = int(time.time())
    cutoffs = {days: now - days * 24 * 60 * 60 for days in WINDOWS_DAYS}

    results = []
    for label, wallet in wallets:
        closed = [
            p
            for p in client.iter_closed_positions(
                wallet_address=wallet, max_rows=max_closed_positions_per_wallet
            )
            if is_copy_target_trade(p)
        ]
        windows = {days: _window_stats(closed, cutoff_ts=cutoff) for days, cutoff in cutoffs.items()}
        shortest = windows[min(WINDOWS_DAYS)]
        if shortest.resolved_count < min_sample_for_flag:
            flag = "LOW_SAMPLE"
        elif shortest.realized_pnl < 0:
            flag = "DECLINING"
        else:
            flag = "OK"
        results.append(
            TraderPerformance(label=label, wallet_address=wallet, flag=flag, windows=windows)
        )

    results.sort(key=lambda r: r.windows[min(WINDOWS_DAYS)].realized_pnl)
    return results


def write_trader_performance_csv(path: Path, results: list[TraderPerformance]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["label", "wallet_address", "flag"]
    for days in WINDOWS_DAYS:
        fields += [
            f"pnl_{days}d",
            f"win_rate_{days}d",
            f"resolved_{days}d",
            f"wins_{days}d",
            f"losses_{days}d",
        ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for r in results:
            row = {"label": r.label, "wallet_address": r.wallet_address, "flag": r.flag}
            for days in WINDOWS_DAYS:
                stats = r.windows[days]
                row[f"pnl_{days}d"] = stats.realized_pnl
                row[f"win_rate_{days}d"] = stats.win_rate
                row[f"resolved_{days}d"] = stats.resolved_count
                row[f"wins_{days}d"] = stats.wins
                row[f"losses_{days}d"] = stats.losses
            writer.writerow(row)


def format_trader_performance(results: list[TraderPerformance]) -> str:
    header = f"{'label':<20}{'flag':<12}"
    for days in WINDOWS_DAYS:
        header += f"{'pnl_' + str(days) + 'd':>12}{'wr_' + str(days) + 'd':>9}{'n_' + str(days) + 'd':>6}   "
    lines = [header]
    for r in results:
        line = f"{r.label:<20}{r.flag:<12}"
        for days in WINDOWS_DAYS:
            stats = r.windows[days]
            line += f"{stats.realized_pnl:>12,.2f}{stats.win_rate:>8.1f}%{stats.resolved_count:>6}   "
        lines.append(line)
    return "\n".join(lines)
