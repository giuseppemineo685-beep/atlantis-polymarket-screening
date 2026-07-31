from __future__ import annotations

import csv
from collections import defaultdict
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

# Confirmed against real markets via gamma-api's /events?tag_slug=esports
# endpoint (2026-07-31): every esports event carries the "esports" tag plus
# a game-specific one (league-of-legends, counter-strike-2, valorant,
# dota-2), and match titles consistently start with "LoL:", "Counter-Strike:",
# "Valorant:" or "Dota 2:". Individual trade rows don't carry tags though
# (only title/slug/eventSlug), so filtering trade-by-trade still needs a
# term list like sports_traders.py's SPORT_TERMS rather than a tag lookup.
ESPORTS_TERMS = {
    "esports",
    "lol:",
    "league of legends",
    "counter-strike",
    "valorant",
    "dota 2",
    "overwatch",
    "rainbow six",
    "call of duty",
    "rocket league",
    "apex legends",
}


@dataclass(frozen=True)
class EsportsTraderScore:
    username: str
    wallet_address: str
    events_participated: int
    esports_trades: int
    esports_markets: int
    esports_volume: Decimal
    buy_volume: Decimal
    sell_volume: Decimal
    avg_trade_size: Decimal
    active_positions: int
    recent_trades_14d: int
    realized_pnl_esports: Decimal
    win_rate_esports: Decimal | None
    estimated_roi: Decimal | None
    copy_score: Decimal
    confidence_score: Decimal
    risk_score: Decimal
    verdict: str


def is_esports_trade(trade: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(trade.get(key) or "").lower()
        for key in ("title", "slug", "eventSlug", "category", "outcome")
    )
    return any(term in haystack for term in ESPORTS_TERMS)


def discover_esports_traders(
    *,
    settings: Settings,
    top_events: int,
    max_trades_per_market: int,
    min_events_participated: int,
    max_traders: int,
    max_trades_per_wallet: int,
    min_esports_trades: int,
    min_esports_volume: Decimal,
) -> list[EsportsTraderScore]:
    """Unlike discover_sports_traders/discover_elon_traders (which scan the
    OVERALL PnL leaderboard and filter by category), this scans the biggest
    current esports MARKETS directly and looks at who's actually trading
    them - it finds esports specialists even if they're not top-N by
    overall account volume, which the leaderboard-scan approach can't do.

    Known limitation: only sees wallets active in the current top_events by
    volume - a specialist who trades mostly in smaller/less liquid esports
    markets (or matches that already closed) won't show up here."""
    client = build_client(settings)
    events = client.get_events(tag_slug="esports", closed=False, limit=top_events)

    wallet_events: dict[str, set[str]] = defaultdict(set)
    wallet_volume: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for event in events:
        markets = event.get("markets") or []
        if not markets:
            continue
        top_market = max(markets, key=lambda m: as_decimal(m.get("volume")) or Decimal("0"))
        condition_id = top_market.get("conditionId")
        if not condition_id:
            continue
        for trade in client.iter_market_trades(condition_id=condition_id, max_rows=max_trades_per_market):
            wallet = str(trade.get("proxyWallet") or "").lower()
            if not wallet:
                continue
            size = as_decimal(trade.get("size")) or Decimal("0")
            price = as_decimal(trade.get("price")) or Decimal("0")
            wallet_events[wallet].add(condition_id)
            wallet_volume[wallet] += size * price

    candidates = [
        wallet
        for wallet, conds in wallet_events.items()
        if len(conds) >= min_events_participated
    ]
    candidates.sort(key=lambda w: wallet_volume[w], reverse=True)
    candidates = candidates[:max_traders]

    scores: list[EsportsTraderScore] = []
    for wallet in candidates:
        trades = list(client.iter_user_trades(wallet_address=wallet, max_rows=max_trades_per_wallet))
        esports_trades = [trade for trade in trades if is_esports_trade(trade)]
        if len(esports_trades) < min_esports_trades:
            continue

        closed_positions = list(client.iter_closed_positions(wallet_address=wallet, max_rows=500))
        closed_esports = [p for p in closed_positions if is_esports_trade(p)]

        username = ""
        for trade in esports_trades:
            username = str(trade.get("name") or trade.get("pseudonym") or "")
            if username:
                break

        score = score_esports_trader(
            username=username,
            wallet_address=wallet,
            events_participated=len(wallet_events[wallet]),
            esports_trades=esports_trades,
            closed_esports=closed_esports,
            active_positions=len(client.get_user_positions(wallet_address=wallet)),
        )
        if score.esports_volume < min_esports_volume:
            continue
        scores.append(score)

    scores.sort(
        key=lambda item: (item.copy_score, item.confidence_score, item.esports_volume),
        reverse=True,
    )
    return scores


def score_esports_trader(
    *,
    username: str,
    wallet_address: str,
    events_participated: int,
    esports_trades: list[dict[str, Any]],
    closed_esports: list[dict[str, Any]],
    active_positions: int,
) -> EsportsTraderScore:
    buy_volume = Decimal("0")
    sell_volume = Decimal("0")
    total_volume = Decimal("0")
    markets = set()
    recent_trades_14d = 0
    now_ts = max([as_int(trade.get("timestamp")) or 0 for trade in esports_trades] or [0])
    recent_cutoff = now_ts - (14 * 24 * 60 * 60)

    for trade in esports_trades:
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

    avg_trade_size = total_volume / Decimal(len(esports_trades)) if esports_trades else Decimal("0")

    # realizedPnl/totalBought on closed positions give a category-specific
    # ROI directly, unlike the Elon vertical (which had to fall back to the
    # trader's OVERALL leaderboard PnL/volume since it only had leaderboard
    # rows to work with, not closed positions for arbitrary wallets).
    realized_pnl = sum((as_decimal(p.get("realizedPnl")) or Decimal("0") for p in closed_esports), Decimal("0"))
    realized_cost = sum((as_decimal(p.get("totalBought")) or Decimal("0") for p in closed_esports), Decimal("0"))
    estimated_roi = realized_pnl / realized_cost if realized_cost > 0 else None

    wins = sum(1 for p in closed_esports if (as_decimal(p.get("curPrice")) or Decimal("0")) == 1)
    losses = sum(1 for p in closed_esports if (as_decimal(p.get("curPrice")) or Decimal("0")) == 0)
    win_rate = Decimal(wins) / Decimal(wins + losses) if (wins + losses) > 0 else None

    confidence_score = clamp_decimal(
        Decimal(len(esports_trades)) / Decimal("2")
        + Decimal(len(markets)) * Decimal("1.5")
        + min(total_volume / Decimal("1000"), Decimal("25")),
        Decimal("0"),
        Decimal("100"),
    )
    activity_score = clamp_decimal(Decimal(recent_trades_14d) * Decimal("4"), Decimal("0"), Decimal("100"))
    roi_score = clamp_decimal((estimated_roi or Decimal("0")) * Decimal("200"), Decimal("0"), Decimal("100"))
    win_rate_score = clamp_decimal((win_rate or Decimal("0.5")) * Decimal("100"), Decimal("0"), Decimal("100"))
    liquidity_score = clamp_decimal(total_volume / Decimal("300"), Decimal("0"), Decimal("100"))
    diversity_score = clamp_decimal(Decimal(len(markets)) * Decimal("5"), Decimal("0"), Decimal("100"))
    risk_score = calculate_risk_score(
        sports_trades=len(esports_trades),
        sports_markets=len(markets),
        sports_volume=total_volume,
        avg_trade_size=avg_trade_size,
        active_positions=active_positions,
    )
    copy_score = clamp_decimal(
        roi_score * Decimal("0.25")
        + win_rate_score * Decimal("0.15")
        + liquidity_score * Decimal("0.15")
        + diversity_score * Decimal("0.15")
        + activity_score * Decimal("0.10")
        + confidence_score * Decimal("0.10")
        + (Decimal("100") - risk_score) * Decimal("0.10"),
        Decimal("0"),
        Decimal("100"),
    )

    return EsportsTraderScore(
        username=username,
        wallet_address=wallet_address,
        events_participated=events_participated,
        esports_trades=len(esports_trades),
        esports_markets=len(markets),
        esports_volume=total_volume,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        avg_trade_size=avg_trade_size,
        active_positions=active_positions,
        recent_trades_14d=recent_trades_14d,
        realized_pnl_esports=realized_pnl,
        win_rate_esports=win_rate,
        estimated_roi=estimated_roi,
        copy_score=copy_score,
        confidence_score=confidence_score,
        risk_score=risk_score,
        verdict=verdict(copy_score, confidence_score, risk_score),
    )


def format_esports_traders(scores: list[EsportsTraderScore]) -> str:
    if not scores:
        return "No esports traders matched the current filters."

    lines = [
        "user                 copy  conf  risk  trades  mkts  events  volume      pnl($)    wr%    verdict  wallet",
        "-------------------  ----  ----  ----  ------  ----  ------  ----------  --------  -----  -------  ------------------------------------------",
    ]
    for item in scores:
        username = (item.username or "(no username)")[:19]
        wr = f"{item.win_rate_esports * 100:.0f}" if item.win_rate_esports is not None else "?"
        lines.append(
            f"{username:<19}  "
            f"{item.copy_score:>4.0f}  "
            f"{item.confidence_score:>4.0f}  "
            f"{item.risk_score:>4.0f}  "
            f"{item.esports_trades:>6}  "
            f"{item.esports_markets:>4}  "
            f"{item.events_participated:>6}  "
            f"{item.esports_volume:>10,.0f}  "
            f"{item.realized_pnl_esports:>8,.0f}  "
            f"{wr:>5}  "
            f"{item.verdict:<7}  "
            f"{item.wallet_address}"
        )
    return "\n".join(lines)


def write_esports_traders_csv(path: Path, scores: list[EsportsTraderScore]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(EsportsTraderScore.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for score in scores:
            writer.writerow({field: getattr(score, field) for field in fields})
