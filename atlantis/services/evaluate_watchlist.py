from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from atlantis.config import Settings
from atlantis.services.evaluate_wallet import WalletEvaluation, evaluate_wallet


@dataclass(frozen=True)
class WatchlistRow:
    status: str
    label: str
    wallet: str
    vertical: str
    notes: str


def read_watchlist(path: Path, *, statuses: set[str]) -> list[WatchlistRow]:
    rows: list[WatchlistRow] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            status = str(row.get("status") or "").strip()
            wallet = str(row.get("wallet") or "").strip().lower()
            if status not in statuses:
                continue
            if not wallet.startswith("0x") or len(wallet) < 10:
                continue
            rows.append(
                WatchlistRow(
                    status=status,
                    label=str(row.get("label") or "").strip(),
                    wallet=wallet,
                    vertical=str(row.get("vertical") or "").strip(),
                    notes=str(row.get("notes") or "").strip(),
                )
            )
    return rows


def evaluate_watchlist(
    *,
    settings: Settings,
    wallets_csv: Path,
    statuses: set[str],
    max_trades: int,
    max_positions: int,
    since_days: int | None = None,
) -> list[tuple[WatchlistRow, WalletEvaluation]]:
    rows = read_watchlist(wallets_csv, statuses=statuses)
    evaluations: list[tuple[WatchlistRow, WalletEvaluation]] = []
    for row in rows:
        evaluations.append(
            (
                row,
                evaluate_wallet(
                    settings=settings,
                    wallet_address=row.wallet,
                    max_trades=max_trades,
                    max_positions=max_positions,
                    since_days=since_days,
                ),
            )
        )
    evaluations.sort(
        key=lambda item: (
            verdict_rank(item[1].verdict),
            item[1].sports_volume,
            item[1].recent_sports_14d,
        ),
        reverse=True,
    )
    return evaluations


def format_watchlist_evaluation(evaluations: list[tuple[WatchlistRow, WalletEvaluation]]) -> str:
    if not evaluations:
        return "No wallets matched the selected statuses."
    lines = [
        "label       status    verdict           sports_trades  sports_volume  recent  active_value  max/hr  bot  wallet",
        "----------  --------  ----------------  -------------  -------------  ------  ------------  ------  ---  ------------------------------------------",
    ]
    for row, evaluation in evaluations:
        lines.append(
            f"{row.label[:10]:<10}  "
            f"{row.status[:8]:<8}  "
            f"{evaluation.verdict:<16}  "
            f"{evaluation.sports_trades:>13}  "
            f"{evaluation.sports_volume:>13,.0f}  "
            f"{evaluation.recent_sports_14d:>6}  "
            f"{evaluation.active_sports_value:>12,.0f}  "
            f"{evaluation.max_trades_per_hour:>6}  "
            f"{'YES' if evaluation.likely_bot else 'no':<3}  "
            f"{evaluation.wallet_address}"
        )
    return "\n".join(lines)


def write_watchlist_evaluation_csv(
    path: Path,
    evaluations: list[tuple[WatchlistRow, WalletEvaluation]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "status",
        "label",
        "wallet",
        "vertical",
        "input_notes",
        "verdict",
        "trades_downloaded",
        "total_markets",
        "total_volume",
        "sports_trades",
        "sports_markets",
        "sports_volume",
        "sports_trade_share",
        "recent_trades_14d",
        "recent_sports_14d",
        "active_positions",
        "active_value",
        "active_sports_positions",
        "active_sports_value",
        "buy_count",
        "sell_count",
        "max_trades_per_hour",
        "hit_trade_cap",
        "likely_bot",
        "system_notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row, evaluation in evaluations:
            writer.writerow(
                {
                    "status": row.status,
                    "label": row.label,
                    "wallet": row.wallet,
                    "vertical": row.vertical,
                    "input_notes": row.notes,
                    "verdict": evaluation.verdict,
                    "trades_downloaded": evaluation.trades_downloaded,
                    "total_markets": evaluation.total_markets,
                    "total_volume": evaluation.total_volume,
                    "sports_trades": evaluation.sports_trades,
                    "sports_markets": evaluation.sports_markets,
                    "sports_volume": evaluation.sports_volume,
                    "sports_trade_share": evaluation.sports_trade_share,
                    "recent_trades_14d": evaluation.recent_trades_14d,
                    "recent_sports_14d": evaluation.recent_sports_14d,
                    "active_positions": evaluation.active_positions,
                    "active_value": evaluation.active_value,
                    "active_sports_positions": evaluation.active_sports_positions,
                    "active_sports_value": evaluation.active_sports_value,
                    "buy_count": evaluation.buy_count,
                    "sell_count": evaluation.sell_count,
                    "max_trades_per_hour": evaluation.max_trades_per_hour,
                    "hit_trade_cap": evaluation.hit_trade_cap,
                    "likely_bot": evaluation.likely_bot,
                    "system_notes": "; ".join(evaluation.notes),
                }
            )


def verdict_rank(verdict: str) -> int:
    return {
        "REJECT": 0,
        "PAPER_ONLY": 1,
        "WATCHLIST": 2,
        "WATCHLIST_STRONG": 3,
    }.get(verdict, 0)
