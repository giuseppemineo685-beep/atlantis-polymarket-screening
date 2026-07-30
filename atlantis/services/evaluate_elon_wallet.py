from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.elon_traders import is_elon_trade
from atlantis.services.evaluate_wallet import CONFIRMED_BOT_WALLETS, notional
from atlantis.services.sports_traders import as_decimal, as_int


@dataclass(frozen=True)
class ElonWalletEvaluation:
    wallet_address: str
    trades_downloaded: int
    total_markets: int
    total_volume: Decimal
    elon_trades: int
    elon_markets: int
    elon_volume: Decimal
    elon_trade_share: Decimal
    recent_trades_14d: int
    recent_elon_14d: int
    active_positions: int
    active_value: Decimal
    active_elon_positions: int
    active_elon_value: Decimal
    buy_count: int
    sell_count: int
    hit_trade_cap: bool
    bot_score: Decimal
    bot_verdict: str
    likely_bot: bool
    verdict: str
    notes: list[str]
    active_elon: list[dict[str, Any]]


def evaluate_elon_wallet(
    *,
    settings: Settings,
    wallet_address: str,
    max_trades: int,
    max_positions: int,
    since_days: int | None = None,
) -> ElonWalletEvaluation:
    from atlantis.services.bot_detection import detect_bot_wallet

    wallet = wallet_address.lower()
    client = build_client(settings)
    start = None
    if since_days is not None:
        import time

        start = int(time.time()) - since_days * 24 * 60 * 60
    trades = list(client.iter_user_trades(wallet_address=wallet, max_rows=max_trades, start=start))
    positions = client.get_user_positions(wallet_address=wallet, limit=max_positions)
    bot_result = detect_bot_wallet(settings=settings, wallet_address=wallet, max_trades=max_trades)

    elon_trades = [trade for trade in trades if is_elon_trade(trade)]
    total_volume = sum(notional(trade) for trade in trades)
    elon_volume = sum(notional(trade) for trade in elon_trades)
    total_markets = len({trade.get("conditionId") for trade in trades if trade.get("conditionId")})
    elon_markets = len({trade.get("conditionId") for trade in elon_trades if trade.get("conditionId")})
    side_counts = Counter(str(trade.get("side") or "").upper() for trade in trades)

    now_ts = max([as_int(trade.get("timestamp")) or 0 for trade in trades] or [0])
    cutoff_14d = now_ts - (14 * 24 * 60 * 60)
    recent_trades_14d = sum(1 for trade in trades if (as_int(trade.get("timestamp")) or 0) >= cutoff_14d)
    recent_elon_14d = sum(1 for trade in elon_trades if (as_int(trade.get("timestamp")) or 0) >= cutoff_14d)

    active_elon = [position for position in positions if is_elon_trade(position)]
    active_value = sum(as_decimal(position.get("currentValue")) or Decimal("0") for position in positions)
    active_elon_value = sum(
        as_decimal(position.get("currentValue")) or Decimal("0") for position in active_elon
    )

    hit_trade_cap = len(trades) >= max_trades
    likely_bot = bot_result.verdict == "LIKELY_BOT" or wallet in CONFIRMED_BOT_WALLETS

    notes = build_elon_notes(
        trades=trades,
        elon_trades=elon_trades,
        elon_markets=elon_markets,
        elon_volume=elon_volume,
        recent_elon_14d=recent_elon_14d,
        active_elon=active_elon,
        side_counts=side_counts,
        bot_result=bot_result,
        hit_trade_cap=hit_trade_cap,
    )
    verdict = elon_wallet_verdict(notes, likely_bot=likely_bot)

    return ElonWalletEvaluation(
        wallet_address=wallet,
        trades_downloaded=len(trades),
        total_markets=total_markets,
        total_volume=total_volume,
        elon_trades=len(elon_trades),
        elon_markets=elon_markets,
        elon_volume=elon_volume,
        elon_trade_share=Decimal(len(elon_trades)) / Decimal(len(trades)) if trades else Decimal("0"),
        recent_trades_14d=recent_trades_14d,
        recent_elon_14d=recent_elon_14d,
        active_positions=len(positions),
        active_value=active_value,
        active_elon_positions=len(active_elon),
        active_elon_value=active_elon_value,
        buy_count=side_counts["BUY"],
        sell_count=side_counts["SELL"],
        hit_trade_cap=hit_trade_cap,
        bot_score=bot_result.bot_score,
        bot_verdict=bot_result.verdict,
        likely_bot=likely_bot,
        verdict=verdict,
        notes=notes,
        active_elon=sorted(
            active_elon,
            key=lambda row: as_decimal(row.get("currentValue")) or Decimal("0"),
            reverse=True,
        ),
    )


def build_elon_notes(
    *,
    trades: list[dict[str, Any]],
    elon_trades: list[dict[str, Any]],
    elon_markets: int,
    elon_volume: Decimal,
    recent_elon_14d: int,
    active_elon: list[dict[str, Any]],
    side_counts: Counter,
    bot_result: Any,
    hit_trade_cap: bool,
) -> list[str]:
    notes = []
    if len(elon_trades) >= 50 and elon_markets >= 15:
        notes.append("strong elon-mention sample")
    if elon_volume >= Decimal("20000"):
        notes.append("high elon-mention volume")
    if recent_elon_14d >= 5:
        notes.append("recently active in elon-mentions")
    if side_counts["SELL"] < max(5, side_counts["BUY"] // 20):
        notes.append("mostly buys; exits may be resolution-driven")
    if active_elon:
        notes.append("has active elon-mention exposure")
    large_zero_positions = [
        position
        for position in active_elon
        if (as_decimal(position.get("curPrice")) or Decimal("0")) == 0
        and (as_decimal(position.get("avgPrice")) or Decimal("0")) > Decimal("0.05")
    ]
    if large_zero_positions:
        notes.append("contains resolved/near-zero losing elon-mention positions")
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


def elon_wallet_verdict(notes: list[str], *, likely_bot: bool = False) -> str:
    if likely_bot:
        return "REJECT"
    positive = {
        "strong elon-mention sample",
        "high elon-mention volume",
        "recently active in elon-mentions",
        "has active elon-mention exposure",
        "large overall sample",
    }
    negative = {
        "mostly buys; exits may be resolution-driven",
        "contains resolved/near-zero losing elon-mention positions",
    }
    score = sum(1 for note in notes if note in positive) - sum(1 for note in notes if note in negative)
    if score >= 4:
        return "WATCHLIST_STRONG"
    if score >= 2:
        return "WATCHLIST"
    if score >= 0:
        return "PAPER_ONLY"
    return "REJECT"


def format_elon_wallet_evaluation(evaluation: ElonWalletEvaluation, *, active_limit: int = 20) -> str:
    lines = [
        f"wallet: {evaluation.wallet_address}",
        f"verdict: {evaluation.verdict}",
        "",
        f"{'metric':<30}  value",
        f"{'-' * 30}  {'-' * 16}",
        f"trades_downloaded              {evaluation.trades_downloaded}",
        f"total_markets                  {evaluation.total_markets}",
        f"total_volume                   {evaluation.total_volume:,.2f}",
        f"elon_trades                    {evaluation.elon_trades}",
        f"elon_markets                   {evaluation.elon_markets}",
        f"elon_volume_est                {evaluation.elon_volume:,.2f}",
        f"elon_trade_share               {evaluation.elon_trade_share * Decimal('100'):.2f}%",
        f"recent_elon_14d                {evaluation.recent_elon_14d}",
        f"active_elon_positions          {evaluation.active_elon_positions}",
        f"active_elon_value              {evaluation.active_elon_value:,.2f}",
        f"buy_count                      {evaluation.buy_count}",
        f"sell_count                     {evaluation.sell_count}",
        f"bot_score                      {evaluation.bot_score:.0f} ({evaluation.bot_verdict})",
        "",
        "notes:",
    ]
    lines.extend(f"- {note}" for note in evaluation.notes)
    lines.extend(["", "active elon-mention positions:"])
    if not evaluation.active_elon:
        lines.append("- none")
        return "\n".join(lines)

    lines.append("value       price   pnl%       outcome       title")
    lines.append("----------  ------  ---------  ------------  ----------------------------------------")
    for position in evaluation.active_elon[:active_limit]:
        value = as_decimal(position.get("currentValue")) or Decimal("0")
        price = as_decimal(position.get("curPrice")) or Decimal("0")
        pnl = as_decimal(position.get("percentPnl")) or Decimal("0")
        outcome = str(position.get("outcome") or "")[:12]
        title = str(position.get("title") or "")[:40]
        lines.append(f"{value:>10,.2f}  {price:>6.3f}  {pnl:>8.2f}%  {outcome:<12}  {title}")
    return "\n".join(lines)
