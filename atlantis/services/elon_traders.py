from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.sports_traders import (
    as_decimal,
    as_int,
    calculate_risk_score,
    clamp_decimal,
    verdict,
)

# Confirmed against real markets via gamma-api's public-search endpoint
# (2026-07-30): "Elon Musk # of tweets [date range]?" ($7-8.5M/week volume,
# by far the dominant market type), "What will Elon post this week?"
# ($6-24K), "Elon Musk Net Worth on [date]?", plus one-off news-driven
# events ("Will Elon register the America Party...", "...rejoin the Trump
# Administration..."). Every title found contains "Elon" or "Musk"
# literally, so a plain substring check is enough - no need for a longer
# term list like SPORT_TERMS.
ELON_TERMS = {"elon", "musk"}


@dataclass(frozen=True)
class ElonTraderScore:
    username: str
    wallet_address: str
    leaderboard_rank: int
    leaderboard_pnl: Decimal
    leaderboard_volume: Decimal
    elon_trades: int
    elon_markets: int
    elon_volume: Decimal
    buy_volume: Decimal
    sell_volume: Decimal
    avg_trade_size: Decimal
    active_positions: int
    recent_trades_14d: int
    estimated_roi: Decimal | None
    copy_score: Decimal
    confidence_score: Decimal
    risk_score: Decimal
    verdict: str


def is_elon_trade(trade: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(trade.get(key) or "").lower()
        for key in ("title", "slug", "eventSlug", "category", "outcome")
    )
    return any(term in haystack for term in ELON_TERMS)


def discover_elon_traders(
    *,
    settings: Settings,
    leaderboard_limit: int,
    max_traders: int,
    max_trades_per_wallet: int,
    min_elon_trades: int,
    min_elon_volume: Decimal,
) -> list[ElonTraderScore]:
    """Scans the OVERALL leaderboard and filters by Elon-mention activity -
    same mechanism as sports_traders.py::discover_sports_traders.

    Known limitation: this only finds wallets that are ALSO among the
    top-N by general PnL. A trader who's excellent specifically at
    Elon-mentions but has modest overall volume won't show up here - if
    this comes back thin, the fallback (not implemented here) would be
    looking at the largest current holders of specific Elon markets
    directly instead of the general leaderboard."""
    client = build_client(settings)
    leaderboard_rows = list(
        client.iter_leaderboard(
            category="OVERALL",
            time_period="MONTH",
            order_by="PNL",
            max_rows=leaderboard_limit,
        )
    )

    scores: list[ElonTraderScore] = []
    for index, row in enumerate(leaderboard_rows[:max_traders], start=1):
        wallet = str(row.get("proxyWallet") or "").lower()
        if not wallet:
            continue

        trades = list(
            client.iter_user_trades(
                wallet_address=wallet,
                max_rows=max_trades_per_wallet,
            )
        )
        elon_trades = [trade for trade in trades if is_elon_trade(trade)]
        if len(elon_trades) < min_elon_trades:
            continue

        score = score_elon_trader(
            leaderboard_row=row,
            leaderboard_rank=index,
            wallet_address=wallet,
            elon_trades=elon_trades,
            active_positions=len(client.get_user_positions(wallet_address=wallet)),
        )
        if score.elon_volume < min_elon_volume:
            continue
        scores.append(score)

    scores.sort(
        key=lambda item: (item.copy_score, item.confidence_score, item.elon_volume),
        reverse=True,
    )
    return scores


def score_elon_trader(
    *,
    leaderboard_row: dict[str, Any],
    leaderboard_rank: int,
    wallet_address: str,
    elon_trades: list[dict[str, Any]],
    active_positions: int,
) -> ElonTraderScore:
    buy_volume = Decimal("0")
    sell_volume = Decimal("0")
    total_volume = Decimal("0")
    markets = set()
    recent_trades_14d = 0
    now_ts = max([as_int(trade.get("timestamp")) or 0 for trade in elon_trades] or [0])
    recent_cutoff = now_ts - (14 * 24 * 60 * 60)

    for trade in elon_trades:
        size = as_decimal(trade.get("size")) or Decimal("0")
        price = as_decimal(trade.get("price")) or Decimal("0")
        notional = size * price
        total_volume += notional
        side = str(trade.get("side") or "").upper()
        if side == "BUY":
            buy_volume += notional
        elif side == "SELL":
            sell_volume += notional
        if trade.get("conditionId"):
            markets.add(str(trade["conditionId"]))
        timestamp = as_int(trade.get("timestamp")) or 0
        if timestamp >= recent_cutoff:
            recent_trades_14d += 1

    leaderboard_pnl = as_decimal(leaderboard_row.get("pnl")) or Decimal("0")
    leaderboard_volume = as_decimal(leaderboard_row.get("vol")) or Decimal("0")
    estimated_roi = leaderboard_pnl / leaderboard_volume if leaderboard_volume > 0 else None
    avg_trade_size = total_volume / Decimal(len(elon_trades)) if elon_trades else Decimal("0")

    confidence_score = clamp_decimal(
        Decimal(len(elon_trades)) / Decimal("2")
        + Decimal(len(markets)) * Decimal("1.5")
        + min(total_volume / Decimal("1000"), Decimal("25")),
        Decimal("0"),
        Decimal("100"),
    )
    activity_score = clamp_decimal(Decimal(recent_trades_14d) * Decimal("4"), Decimal("0"), Decimal("100"))
    roi_score = clamp_decimal((estimated_roi or Decimal("0")) * Decimal("350"), Decimal("0"), Decimal("100"))
    liquidity_score = clamp_decimal(total_volume / Decimal("300"), Decimal("0"), Decimal("100"))
    diversity_score = clamp_decimal(Decimal(len(markets)) * Decimal("5"), Decimal("0"), Decimal("100"))
    risk_score = calculate_risk_score(
        sports_trades=len(elon_trades),
        sports_markets=len(markets),
        sports_volume=total_volume,
        avg_trade_size=avg_trade_size,
        active_positions=active_positions,
    )
    copy_score = clamp_decimal(
        roi_score * Decimal("0.30")
        + liquidity_score * Decimal("0.20")
        + diversity_score * Decimal("0.15")
        + activity_score * Decimal("0.15")
        + confidence_score * Decimal("0.15")
        + (Decimal("100") - risk_score) * Decimal("0.05"),
        Decimal("0"),
        Decimal("100"),
    )

    return ElonTraderScore(
        username=str(leaderboard_row.get("userName") or ""),
        wallet_address=wallet_address,
        leaderboard_rank=as_int(leaderboard_row.get("rank")) or leaderboard_rank,
        leaderboard_pnl=leaderboard_pnl,
        leaderboard_volume=leaderboard_volume,
        elon_trades=len(elon_trades),
        elon_markets=len(markets),
        elon_volume=total_volume,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        avg_trade_size=avg_trade_size,
        active_positions=active_positions,
        recent_trades_14d=recent_trades_14d,
        estimated_roi=estimated_roi,
        copy_score=copy_score,
        confidence_score=confidence_score,
        risk_score=risk_score,
        verdict=verdict(copy_score, confidence_score, risk_score),
    )


def format_elon_traders(scores: list[ElonTraderScore]) -> str:
    if not scores:
        return "No Elon-mention traders matched the current filters."

    lines = [
        "rank  user                 copy  conf  risk  trades  mkts  volume      recent  verdict  wallet",
        "----  -------------------  ----  ----  ----  ------  ----  ----------  ------  -------  ------------------------------------------",
    ]
    for item in scores:
        username = (item.username or "(no username)")[:19]
        lines.append(
            f"{item.leaderboard_rank:<4}  "
            f"{username:<19}  "
            f"{item.copy_score:>4.0f}  "
            f"{item.confidence_score:>4.0f}  "
            f"{item.risk_score:>4.0f}  "
            f"{item.elon_trades:>6}  "
            f"{item.elon_markets:>4}  "
            f"{item.elon_volume:>10,.0f}  "
            f"{item.recent_trades_14d:>6}  "
            f"{item.verdict:<7}  "
            f"{item.wallet_address}"
        )
    return "\n".join(lines)


def write_elon_traders_csv(path: Path, scores: list[ElonTraderScore]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(ElonTraderScore.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for score in scores:
            writer.writerow({field: getattr(score, field) for field in fields})
