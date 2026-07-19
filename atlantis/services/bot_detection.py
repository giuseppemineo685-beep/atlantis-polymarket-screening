from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.evaluate_wallet import notional
from atlantis.services.sports_traders import as_int


FAST_MARKET_TERMS = ("up or down", "5m", "15m")


@dataclass(frozen=True)
class BotDetection:
    wallet_address: str
    trades_downloaded: int
    trades_1h: int
    trades_6h: int
    trades_24h: int
    buys: int
    sells: int
    unique_markets: int
    fast_market_trades: int
    fast_market_share: Decimal
    total_volume: Decimal
    bot_score: Decimal
    verdict: str
    reasons: list[str]


def detect_bot_wallet(
    *,
    settings: Settings,
    wallet_address: str,
    max_trades: int,
) -> BotDetection:
    wallet = wallet_address.lower()
    client = build_client(settings)
    trades = list(client.iter_user_trades(wallet_address=wallet, max_rows=max_trades))
    timestamps = [as_int(trade.get("timestamp")) or 0 for trade in trades]
    now_ts = max(timestamps or [0])
    side_counts = Counter(str(trade.get("side") or "").upper() for trade in trades)
    unique_markets = len({trade.get("conditionId") for trade in trades if trade.get("conditionId")})
    fast_market_trades = sum(1 for trade in trades if is_fast_market_trade(trade))
    fast_market_share = Decimal(fast_market_trades) / Decimal(len(trades)) if trades else Decimal("0")
    trades_1h = sum(1 for ts in timestamps if ts >= now_ts - 60 * 60)
    trades_6h = sum(1 for ts in timestamps if ts >= now_ts - 6 * 60 * 60)
    trades_24h = sum(1 for ts in timestamps if ts >= now_ts - 24 * 60 * 60)
    total_volume = sum(notional(trade) for trade in trades)
    reasons = bot_reasons(
        trades=trades,
        trades_1h=trades_1h,
        trades_6h=trades_6h,
        trades_24h=trades_24h,
        buys=side_counts["BUY"],
        sells=side_counts["SELL"],
        unique_markets=unique_markets,
        fast_market_share=fast_market_share,
    )
    score = bot_score(reasons)
    return BotDetection(
        wallet_address=wallet,
        trades_downloaded=len(trades),
        trades_1h=trades_1h,
        trades_6h=trades_6h,
        trades_24h=trades_24h,
        buys=side_counts["BUY"],
        sells=side_counts["SELL"],
        unique_markets=unique_markets,
        fast_market_trades=fast_market_trades,
        fast_market_share=fast_market_share,
        total_volume=total_volume,
        bot_score=score,
        verdict=bot_verdict(score),
        reasons=reasons,
    )


def is_fast_market_trade(trade: dict) -> bool:
    haystack = " ".join(
        str(trade.get(key) or "").lower()
        for key in ("title", "slug", "eventSlug")
    )
    return all(term in haystack for term in ("up or down",)) or any(
        term in haystack for term in ("updown-5m", "updown-15m")
    )


def bot_reasons(
    *,
    trades: list[dict],
    trades_1h: int,
    trades_6h: int,
    trades_24h: int,
    buys: int,
    sells: int,
    unique_markets: int,
    fast_market_share: Decimal,
) -> list[str]:
    reasons = []
    if trades_1h >= 20:
        reasons.append("20+ trades in 1h")
    if trades_6h >= 50:
        reasons.append("50+ trades in 6h")
    if trades_24h >= 100:
        reasons.append("100+ trades in 24h")
    if buys >= 100 and sells == 0:
        reasons.append("buy-only pattern")
    if trades and unique_markets / len(trades) > 0.70:
        reasons.append("very high market churn")
    if fast_market_share >= Decimal("0.50"):
        reasons.append("dominant fast up/down markets")
    return reasons


def bot_score(reasons: list[str]) -> Decimal:
    weights = {
        "20+ trades in 1h": Decimal("30"),
        "50+ trades in 6h": Decimal("20"),
        "100+ trades in 24h": Decimal("15"),
        "buy-only pattern": Decimal("25"),
        "very high market churn": Decimal("15"),
        "dominant fast up/down markets": Decimal("35"),
    }
    return min(sum((weights.get(reason, Decimal("0")) for reason in reasons), Decimal("0")), Decimal("100"))


def bot_verdict(score: Decimal) -> str:
    if score >= 70:
        return "LIKELY_BOT"
    if score >= 40:
        return "POSSIBLE_BOT"
    return "LIKELY_HUMAN_OR_LOW_SIGNAL"


def format_bot_detection(result: BotDetection) -> str:
    lines = [
        f"wallet: {result.wallet_address}",
        f"verdict: {result.verdict}",
        f"bot_score: {result.bot_score:.0f}",
        "",
        "metric               value",
        "-------------------  ----------------",
        f"trades_downloaded    {result.trades_downloaded}",
        f"trades_1h            {result.trades_1h}",
        f"trades_6h            {result.trades_6h}",
        f"trades_24h           {result.trades_24h}",
        f"buys                 {result.buys}",
        f"sells                {result.sells}",
        f"unique_markets       {result.unique_markets}",
        f"fast_market_trades   {result.fast_market_trades}",
        f"fast_market_share    {result.fast_market_share * Decimal('100'):.2f}%",
        f"total_volume_est     {result.total_volume:,.2f}",
        "",
        "reasons:",
    ]
    if result.reasons:
        lines.extend(f"- {reason}" for reason in result.reasons)
    else:
        lines.append("- none")
    return "\n".join(lines)
