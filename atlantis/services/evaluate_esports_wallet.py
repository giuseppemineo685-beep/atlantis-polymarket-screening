from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.esports_traders import is_esports_trade
from atlantis.services.evaluate_wallet import CONFIRMED_BOT_WALLETS, notional
from atlantis.services.sports_traders import as_decimal, as_int


@dataclass(frozen=True)
class EsportsWalletEvaluation:
    wallet_address: str
    trades_downloaded: int
    total_markets: int
    total_volume: Decimal
    esports_trades: int
    esports_markets: int
    esports_volume: Decimal
    esports_trade_share: Decimal
    recent_trades_14d: int
    recent_esports_14d: int
    active_positions: int
    active_value: Decimal
    active_esports_positions: int
    active_esports_value: Decimal
    buy_count: int
    sell_count: int
    realized_pnl_esports: Decimal
    win_rate_esports: Decimal | None
    hit_trade_cap: bool
    bot_score: Decimal
    bot_verdict: str
    likely_bot: bool
    verdict: str
    notes: list[str]
    active_esports: list[dict[str, Any]]


def evaluate_esports_wallet(
    *,
    settings: Settings,
    wallet_address: str,
    max_trades: int,
    max_positions: int,
    since_days: int | None = None,
) -> EsportsWalletEvaluation:
    from atlantis.services.bot_detection import detect_bot_wallet

    wallet = wallet_address.lower()
    client = build_client(settings)
    start = None
    if since_days is not None:
        import time

        start = int(time.time()) - since_days * 24 * 60 * 60
    trades = list(client.iter_user_trades(wallet_address=wallet, max_rows=max_trades, start=start))
    positions = client.get_user_positions(wallet_address=wallet, limit=max_positions)
    closed_positions = list(client.iter_closed_positions(wallet_address=wallet, max_rows=500))
    bot_result = detect_bot_wallet(settings=settings, wallet_address=wallet, max_trades=max_trades)

    esports_trades = [trade for trade in trades if is_esports_trade(trade)]
    total_volume = sum(notional(trade) for trade in trades)
    esports_volume = sum(notional(trade) for trade in esports_trades)
    total_markets = len({trade.get("conditionId") for trade in trades if trade.get("conditionId")})
    esports_markets = len({trade.get("conditionId") for trade in esports_trades if trade.get("conditionId")})
    side_counts = Counter(str(trade.get("side") or "").upper() for trade in trades)

    now_ts = max([as_int(trade.get("timestamp")) or 0 for trade in trades] or [0])
    cutoff_14d = now_ts - (14 * 24 * 60 * 60)
    recent_trades_14d = sum(1 for trade in trades if (as_int(trade.get("timestamp")) or 0) >= cutoff_14d)
    recent_esports_14d = sum(1 for trade in esports_trades if (as_int(trade.get("timestamp")) or 0) >= cutoff_14d)

    active_esports = [position for position in positions if is_esports_trade(position)]
    active_value = sum(as_decimal(position.get("currentValue")) or Decimal("0") for position in positions)
    active_esports_value = sum(
        as_decimal(position.get("currentValue")) or Decimal("0") for position in active_esports
    )

    closed_esports = [p for p in closed_positions if is_esports_trade(p)]
    realized_pnl_esports = sum(
        (as_decimal(p.get("realizedPnl")) or Decimal("0") for p in closed_esports), Decimal("0")
    )
    wins = sum(1 for p in closed_esports if (as_decimal(p.get("curPrice")) or Decimal("0")) == 1)
    losses = sum(1 for p in closed_esports if (as_decimal(p.get("curPrice")) or Decimal("0")) == 0)
    win_rate_esports = Decimal(wins) / Decimal(wins + losses) if (wins + losses) > 0 else None

    hit_trade_cap = len(trades) >= max_trades
    likely_bot = bot_result.verdict == "LIKELY_BOT" or wallet in CONFIRMED_BOT_WALLETS

    notes = build_esports_notes(
        trades=trades,
        esports_trades=esports_trades,
        esports_markets=esports_markets,
        esports_volume=esports_volume,
        recent_esports_14d=recent_esports_14d,
        active_esports=active_esports,
        win_rate_esports=win_rate_esports,
        closed_esports_count=len(closed_esports),
        side_counts=side_counts,
        bot_result=bot_result,
        hit_trade_cap=hit_trade_cap,
    )
    verdict = esports_wallet_verdict(notes, likely_bot=likely_bot)

    return EsportsWalletEvaluation(
        wallet_address=wallet,
        trades_downloaded=len(trades),
        total_markets=total_markets,
        total_volume=total_volume,
        esports_trades=len(esports_trades),
        esports_markets=esports_markets,
        esports_volume=esports_volume,
        esports_trade_share=Decimal(len(esports_trades)) / Decimal(len(trades)) if trades else Decimal("0"),
        recent_trades_14d=recent_trades_14d,
        recent_esports_14d=recent_esports_14d,
        active_positions=len(positions),
        active_value=active_value,
        active_esports_positions=len(active_esports),
        active_esports_value=active_esports_value,
        buy_count=side_counts["BUY"],
        sell_count=side_counts["SELL"],
        realized_pnl_esports=realized_pnl_esports,
        win_rate_esports=win_rate_esports,
        hit_trade_cap=hit_trade_cap,
        bot_score=bot_result.bot_score,
        bot_verdict=bot_result.verdict,
        likely_bot=likely_bot,
        verdict=verdict,
        notes=notes,
        active_esports=sorted(
            active_esports,
            key=lambda row: as_decimal(row.get("currentValue")) or Decimal("0"),
            reverse=True,
        ),
    )


def build_esports_notes(
    *,
    trades: list[dict[str, Any]],
    esports_trades: list[dict[str, Any]],
    esports_markets: int,
    esports_volume: Decimal,
    recent_esports_14d: int,
    active_esports: list[dict[str, Any]],
    win_rate_esports: Decimal | None,
    closed_esports_count: int,
    side_counts: Counter,
    bot_result: Any,
    hit_trade_cap: bool,
) -> list[str]:
    notes = []
    if len(esports_trades) >= 50 and esports_markets >= 15:
        notes.append("strong esports sample")
    if esports_volume >= Decimal("5000"):
        notes.append("high esports volume")
    if recent_esports_14d >= 5:
        notes.append("recently active in esports")
    if closed_esports_count >= 10 and win_rate_esports is not None:
        if win_rate_esports >= Decimal("0.6"):
            notes.append(f"strong esports win rate ({win_rate_esports * 100:.0f}% over {closed_esports_count} closed)")
        elif win_rate_esports <= Decimal("0.4"):
            notes.append(f"weak esports win rate ({win_rate_esports * 100:.0f}% over {closed_esports_count} closed)")
    if side_counts["SELL"] < max(5, side_counts["BUY"] // 20):
        notes.append("mostly buys; exits may be resolution-driven")
    if active_esports:
        notes.append("has active esports exposure")
    large_zero_positions = [
        position
        for position in active_esports
        if (as_decimal(position.get("curPrice")) or Decimal("0")) == 0
        and (as_decimal(position.get("avgPrice")) or Decimal("0")) > Decimal("0.05")
    ]
    if large_zero_positions:
        notes.append("contains resolved/near-zero losing esports positions")
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


def esports_wallet_verdict(notes: list[str], *, likely_bot: bool = False) -> str:
    if likely_bot:
        return "REJECT"
    positive = {
        "strong esports sample",
        "high esports volume",
        "recently active in esports",
        "has active esports exposure",
        "large overall sample",
    }
    strong_positive = {"strong esports win rate"}
    negative = {
        "mostly buys; exits may be resolution-driven",
        "contains resolved/near-zero losing esports positions",
    }
    strong_negative = {"weak esports win rate"}
    score = sum(1 for note in notes if note in positive) - sum(1 for note in notes if note in negative)
    score += sum(2 for note in notes if note.startswith("strong esports win rate"))
    score -= sum(2 for note in notes if note.startswith("weak esports win rate"))
    if score >= 4:
        return "WATCHLIST_STRONG"
    if score >= 2:
        return "WATCHLIST"
    if score >= 0:
        return "PAPER_ONLY"
    return "REJECT"


def format_esports_wallet_evaluation(evaluation: EsportsWalletEvaluation, *, active_limit: int = 20) -> str:
    win_rate_str = f"{evaluation.win_rate_esports * 100:.2f}%" if evaluation.win_rate_esports is not None else "n/a"
    lines = [
        f"wallet: {evaluation.wallet_address}",
        f"verdict: {evaluation.verdict}",
        "",
        f"{'metric':<30}  value",
        f"{'-' * 30}  {'-' * 16}",
        f"trades_downloaded              {evaluation.trades_downloaded}",
        f"total_markets                  {evaluation.total_markets}",
        f"total_volume                   {evaluation.total_volume:,.2f}",
        f"esports_trades                 {evaluation.esports_trades}",
        f"esports_markets                {evaluation.esports_markets}",
        f"esports_volume_est             {evaluation.esports_volume:,.2f}",
        f"esports_trade_share            {evaluation.esports_trade_share * Decimal('100'):.2f}%",
        f"recent_esports_14d             {evaluation.recent_esports_14d}",
        f"realized_pnl_esports           {evaluation.realized_pnl_esports:,.2f}",
        f"win_rate_esports               {win_rate_str}",
        f"active_esports_positions       {evaluation.active_esports_positions}",
        f"active_esports_value           {evaluation.active_esports_value:,.2f}",
        f"buy_count                      {evaluation.buy_count}",
        f"sell_count                     {evaluation.sell_count}",
        f"bot_score                      {evaluation.bot_score:.0f} ({evaluation.bot_verdict})",
        "",
        "notes:",
    ]
    lines.extend(f"- {note}" for note in evaluation.notes)
    lines.extend(["", "active esports positions:"])
    if not evaluation.active_esports:
        lines.append("- none")
        return "\n".join(lines)

    lines.append("value       price   pnl%       outcome       title")
    lines.append("----------  ------  ---------  ------------  ----------------------------------------")
    for position in evaluation.active_esports[:active_limit]:
        value = as_decimal(position.get("currentValue")) or Decimal("0")
        price = as_decimal(position.get("curPrice")) or Decimal("0")
        pnl = as_decimal(position.get("percentPnl")) or Decimal("0")
        outcome = str(position.get("outcome") or "")[:12]
        title = str(position.get("title") or "")[:40]
        lines.append(f"{value:>10,.2f}  {price:>6.3f}  {pnl:>8.2f}%  {outcome:<12}  {title}")
    return "\n".join(lines)
