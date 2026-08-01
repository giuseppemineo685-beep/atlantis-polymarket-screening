from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from atlantis.config import Settings
from atlantis.services.evaluate_esports_wallet import EsportsWalletEvaluation, evaluate_esports_wallet


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


def evaluate_watchlist_esports(
    *,
    settings: Settings,
    wallets_csv: Path,
    statuses: set[str],
    max_trades: int,
    max_positions: int,
    since_days: int | None = None,
) -> list[tuple[WatchlistRow, EsportsWalletEvaluation]]:
    rows = read_watchlist(wallets_csv, statuses=statuses)
    evaluations: list[tuple[WatchlistRow, EsportsWalletEvaluation]] = []
    for row in rows:
        evaluations.append(
            (
                row,
                evaluate_esports_wallet(
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
            item[1].esports_volume,
            item[1].recent_esports_14d,
        ),
        reverse=True,
    )
    return evaluations


def format_watchlist_evaluation_esports(evaluations: list[tuple[WatchlistRow, EsportsWalletEvaluation]]) -> str:
    if not evaluations:
        return "No wallets matched the selected statuses."
    lines = [
        "label       status    verdict           esports_trades  esports_volume  recent  active_value  bot_score  bot_verdict                  wallet",
        "----------  --------  ----------------  --------------  --------------  ------  ------------  ---------  ---------------------------  ------------------------------------------",
    ]
    for row, evaluation in evaluations:
        lines.append(
            f"{row.label[:10]:<10}  "
            f"{row.status[:8]:<8}  "
            f"{evaluation.verdict:<16}  "
            f"{evaluation.esports_trades:>14}  "
            f"{evaluation.esports_volume:>14,.0f}  "
            f"{evaluation.recent_esports_14d:>6}  "
            f"{evaluation.active_esports_value:>12,.0f}  "
            f"{evaluation.bot_score:>9.0f}  "
            f"{evaluation.bot_verdict:<27}  "
            f"{evaluation.wallet_address}"
        )
    return "\n".join(lines)


def write_watchlist_evaluation_esports_csv(
    path: Path,
    evaluations: list[tuple[WatchlistRow, EsportsWalletEvaluation]],
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
        "esports_trades",
        "esports_markets",
        "esports_volume",
        "esports_trade_share",
        "recent_trades_14d",
        "recent_esports_14d",
        "active_positions",
        "active_value",
        "active_esports_positions",
        "active_esports_value",
        "buy_count",
        "sell_count",
        "hit_trade_cap",
        "bot_score",
        "bot_verdict",
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
                    "esports_trades": evaluation.esports_trades,
                    "esports_markets": evaluation.esports_markets,
                    "esports_volume": evaluation.esports_volume,
                    "esports_trade_share": evaluation.esports_trade_share,
                    "recent_trades_14d": evaluation.recent_trades_14d,
                    "recent_esports_14d": evaluation.recent_esports_14d,
                    "active_positions": evaluation.active_positions,
                    "active_value": evaluation.active_value,
                    "active_esports_positions": evaluation.active_esports_positions,
                    "active_esports_value": evaluation.active_esports_value,
                    "buy_count": evaluation.buy_count,
                    "sell_count": evaluation.sell_count,
                    "hit_trade_cap": evaluation.hit_trade_cap,
                    "bot_score": evaluation.bot_score,
                    "bot_verdict": evaluation.bot_verdict,
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
