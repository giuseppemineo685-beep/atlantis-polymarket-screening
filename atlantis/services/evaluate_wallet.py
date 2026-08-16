from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.sports_traders import as_decimal, as_int, is_copy_target_trade


# Wallets manually confirmed as bots through direct investigation (see
# outputs/watchlist_evaluation.md notes). The automated bot_score is noisy
# run-to-run (it samples recent trades, so a wallet can drift from
# LIKELY_BOT to POSSIBLE_BOT between cycles) - that flip once let
# RN1_possible_bot back into a live consensus signal. A manual confirmation
# overrides the score permanently instead of relying on it staying above
# the auto-reject threshold on every single run.
CONFIRMED_BOT_WALLETS = {
    "0x2005d16a84ceefa912d4e380cd32e7ff827875ea",  # RN1_possible_bot
    "0x6d3c5bd13984b2de47c3a88ddc455309aab3d294",  # Trader05, confirmed bot 2026-08-02
}


@dataclass(frozen=True)
class WalletEvaluation:
    wallet_address: str
    trades_downloaded: int
    total_markets: int
    total_volume: Decimal
    sports_trades: int
    sports_markets: int
    sports_volume: Decimal
    sports_trade_share: Decimal
    recent_trades_14d: int
    recent_sports_14d: int
    active_positions: int
    active_value: Decimal
    active_sports_positions: int
    active_sports_value: Decimal
    buy_count: int
    sell_count: int
    hit_trade_cap: bool
    bot_score: Decimal
    bot_verdict: str
    likely_bot: bool
    verdict: str
    notes: list[str]
    active_sports: list[dict[str, Any]]


def evaluate_wallet(
    *,
    settings: Settings,
    wallet_address: str,
    max_trades: int,
    max_positions: int,
    since_days: int | None = None,
) -> WalletEvaluation:
    # Deferred import: atlantis.services.bot_detection imports `notional` from
    # this module, so importing it at module load time would be circular.
    from atlantis.services.bot_detection import detect_bot_wallet

    wallet = wallet_address.lower()
    client = build_client(settings)
    start = None
    if since_days is not None:
        start = int(time.time()) - since_days * 24 * 60 * 60
    trades = list(client.iter_user_trades(wallet_address=wallet, max_rows=max_trades, start=start))
    positions = client.get_user_positions(wallet_address=wallet, limit=max_positions)
    bot_result = detect_bot_wallet(settings=settings, wallet_address=wallet, max_trades=max_trades)

    sports_trades = [trade for trade in trades if is_copy_target_trade(trade)]
    total_volume = sum(notional(trade) for trade in trades)
    sports_volume = sum(notional(trade) for trade in sports_trades)
    total_markets = len({trade.get("conditionId") for trade in trades if trade.get("conditionId")})
    sports_markets = len({trade.get("conditionId") for trade in sports_trades if trade.get("conditionId")})
    side_counts = Counter(str(trade.get("side") or "").upper() for trade in trades)

    now_ts = max([as_int(trade.get("timestamp")) or 0 for trade in trades] or [0])
    cutoff_14d = now_ts - (14 * 24 * 60 * 60)
    recent_trades_14d = sum(1 for trade in trades if (as_int(trade.get("timestamp")) or 0) >= cutoff_14d)
    recent_sports_14d = sum(
        1 for trade in sports_trades if (as_int(trade.get("timestamp")) or 0) >= cutoff_14d
    )

    active_sports = [position for position in positions if is_copy_target_trade(position)]
    active_value = sum(as_decimal(position.get("currentValue")) or Decimal("0") for position in positions)
    active_sports_value = sum(
        as_decimal(position.get("currentValue")) or Decimal("0") for position in active_sports
    )

    hit_trade_cap = len(trades) >= max_trades
    # POSSIBLE_BOT alone is too weak to auto-reject: high trade frequency by
    # itself also matches legitimate high-volume active sports bettors who
    # cover many distinct markets and do sell sometimes (see Trader05 spot
    # check). Only auto-reject on LIKELY_BOT, which requires stronger
    # combined signals (buy-only + low market diversity + fast-market share).
    likely_bot = bot_result.verdict == "LIKELY_BOT" or wallet in CONFIRMED_BOT_WALLETS

    notes = build_notes(
        trades=trades,
        sports_trades=sports_trades,
        sports_markets=sports_markets,
        sports_volume=sports_volume,
        recent_sports_14d=recent_sports_14d,
        active_sports=active_sports,
        side_counts=side_counts,
        bot_result=bot_result,
        hit_trade_cap=hit_trade_cap,
    )
    verdict = wallet_verdict(notes, likely_bot=likely_bot)

    return WalletEvaluation(
        wallet_address=wallet,
        trades_downloaded=len(trades),
        total_markets=total_markets,
        total_volume=total_volume,
        sports_trades=len(sports_trades),
        sports_markets=sports_markets,
        sports_volume=sports_volume,
        sports_trade_share=Decimal(len(sports_trades)) / Decimal(len(trades)) if trades else Decimal("0"),
        recent_trades_14d=recent_trades_14d,
        recent_sports_14d=recent_sports_14d,
        active_positions=len(positions),
        active_value=active_value,
        active_sports_positions=len(active_sports),
        active_sports_value=active_sports_value,
        buy_count=side_counts["BUY"],
        sell_count=side_counts["SELL"],
        hit_trade_cap=hit_trade_cap,
        bot_score=bot_result.bot_score,
        bot_verdict=bot_result.verdict,
        likely_bot=likely_bot,
        verdict=verdict,
        notes=notes,
        active_sports=sorted(
            active_sports,
            key=lambda row: as_decimal(row.get("currentValue")) or Decimal("0"),
            reverse=True,
        ),
    )


def build_notes(
    *,
    trades: list[dict[str, Any]],
    sports_trades: list[dict[str, Any]],
    sports_markets: int,
    sports_volume: Decimal,
    recent_sports_14d: int,
    active_sports: list[dict[str, Any]],
    side_counts: Counter,
    bot_result: Any,
    hit_trade_cap: bool,
) -> list[str]:
    notes = []
    if len(sports_trades) >= 100 and sports_markets >= 50:
        notes.append("strong sports sample")
    if sports_volume >= Decimal("100000"):
        notes.append("high sports volume")
    if recent_sports_14d >= 10:
        notes.append("recently active in sports")
    if side_counts["SELL"] < max(5, side_counts["BUY"] // 20):
        notes.append("mostly buys; exits may be resolution-driven")
    if active_sports:
        notes.append("has active sports exposure")
    large_zero_positions = [
        position
        for position in active_sports
        if (as_decimal(position.get("curPrice")) or Decimal("0")) == 0
        and (as_decimal(position.get("avgPrice")) or Decimal("0")) > Decimal("0.05")
    ]
    if large_zero_positions:
        notes.append("contains resolved/near-zero losing sports positions")
    if len(trades) >= 500:
        notes.append("large overall sample")
    if bot_result.verdict != "LIKELY_HUMAN_OR_LOW_SIGNAL":
        notes.append(
            f"bot_detection={bot_result.verdict} (score {bot_result.bot_score:.0f}): "
            + ", ".join(bot_result.reasons)
        )
    if hit_trade_cap:
        notes.append("hit trade download cap; true history is longer than sampled")
    return notes


def wallet_verdict(notes: list[str], *, likely_bot: bool = False) -> str:
    if likely_bot:
        return "REJECT"
    positive = {
        "strong sports sample",
        "high sports volume",
        "recently active in sports",
        "has active sports exposure",
        "large overall sample",
    }
    negative = {
        "mostly buys; exits may be resolution-driven",
        "contains resolved/near-zero losing sports positions",
    }
    score = sum(1 for note in notes if note in positive) - sum(1 for note in notes if note in negative)
    if score >= 4:
        return "WATCHLIST_STRONG"
    if score >= 2:
        return "WATCHLIST"
    if score >= 0:
        return "PAPER_ONLY"
    return "REJECT"


def format_wallet_evaluation(evaluation: WalletEvaluation, active_limit: int) -> str:
    lines = [
        f"wallet: {evaluation.wallet_address}",
        f"verdict: {evaluation.verdict}",
        "",
        "metric                         value",
        "-----------------------------  ----------------",
        f"trades_downloaded              {evaluation.trades_downloaded}",
        f"total_markets                  {evaluation.total_markets}",
        f"total_volume_est               {evaluation.total_volume:,.2f}",
        f"sports_trades                  {evaluation.sports_trades}",
        f"sports_markets                 {evaluation.sports_markets}",
        f"sports_volume_est              {evaluation.sports_volume:,.2f}",
        f"sports_trade_share             {evaluation.sports_trade_share * Decimal('100'):.2f}%",
        f"recent_sports_14d              {evaluation.recent_sports_14d}",
        f"active_sports_positions        {evaluation.active_sports_positions}",
        f"active_sports_value            {evaluation.active_sports_value:,.2f}",
        f"buy_count                      {evaluation.buy_count}",
        f"sell_count                     {evaluation.sell_count}",
        "",
        "notes:",
    ]
    lines.extend(f"- {note}" for note in evaluation.notes)
    lines.extend(["", "active sports positions:"])
    if not evaluation.active_sports:
        lines.append("- none")
        return "\n".join(lines)

    lines.append("value       price   pnl%       outcome       title")
    lines.append("----------  ------  ---------  ------------  ----------------------------------------")
    for position in evaluation.active_sports[:active_limit]:
        value = as_decimal(position.get("currentValue")) or Decimal("0")
        price = as_decimal(position.get("curPrice")) or Decimal("0")
        pnl = as_decimal(position.get("percentPnl")) or Decimal("0")
        outcome = str(position.get("outcome") or "")[:12]
        title = str(position.get("title") or "")[:40]
        lines.append(f"{value:>10,.2f}  {price:>6.3f}  {pnl:>8.2f}%  {outcome:<12}  {title}")
    return "\n".join(lines)


def notional(row: dict[str, Any]) -> Decimal:
    return (as_decimal(row.get("size")) or Decimal("0")) * (
        as_decimal(row.get("price")) or Decimal("0")
    )
