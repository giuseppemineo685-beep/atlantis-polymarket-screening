from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client


SPORT_TERMS = {
    "nba",
    "wnba",
    "nfl",
    "mlb",
    "nhl",
    "epl",
    "ucl",
    "soccer",
    "football",
    "basketball",
    "baseball",
    "hockey",
    "tennis",
    "atp",
    "wta",
    "ufc",
    "mma",
    "boxing",
    "golf",
    "formula 1",
    "f1",
    "nascar",
    "cricket",
    "rugby",
    "olympics",
    "world cup",
    "champions league",
    "premier league",
    "la liga",
    "serie a",
    "bundesliga",
}


@dataclass(frozen=True)
class SportsTraderScore:
    username: str
    wallet_address: str
    leaderboard_rank: int
    leaderboard_pnl: Decimal
    leaderboard_volume: Decimal
    sports_trades: int
    sports_markets: int
    sports_volume: Decimal
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


def discover_sports_traders(
    *,
    settings: Settings,
    leaderboard_limit: int,
    max_traders: int,
    max_trades_per_wallet: int,
    min_sports_trades: int,
    min_sports_volume: Decimal,
) -> list[SportsTraderScore]:
    client = build_client(settings)
    leaderboard_rows = list(
        client.iter_leaderboard(
            category="OVERALL",
            time_period="MONTH",
            order_by="PNL",
            max_rows=leaderboard_limit,
        )
    )

    scores: list[SportsTraderScore] = []
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
        sports_trades = [trade for trade in trades if is_sports_trade(trade)]
        if len(sports_trades) < min_sports_trades:
            continue

        score = score_sports_trader(
            leaderboard_row=row,
            leaderboard_rank=index,
            wallet_address=wallet,
            sports_trades=sports_trades,
            active_positions=len(client.get_user_positions(wallet_address=wallet)),
        )
        if score.sports_volume < min_sports_volume:
            continue
        scores.append(score)

    scores.sort(
        key=lambda item: (item.copy_score, item.confidence_score, item.sports_volume),
        reverse=True,
    )
    return scores


def score_sports_trader(
    *,
    leaderboard_row: dict[str, Any],
    leaderboard_rank: int,
    wallet_address: str,
    sports_trades: list[dict[str, Any]],
    active_positions: int,
) -> SportsTraderScore:
    buy_volume = Decimal("0")
    sell_volume = Decimal("0")
    total_volume = Decimal("0")
    markets = set()
    recent_trades_14d = 0
    now_ts = max([as_int(trade.get("timestamp")) or 0 for trade in sports_trades] or [0])
    recent_cutoff = now_ts - (14 * 24 * 60 * 60)

    for trade in sports_trades:
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
    avg_trade_size = total_volume / Decimal(len(sports_trades)) if sports_trades else Decimal("0")

    confidence_score = clamp_decimal(
        Decimal(len(sports_trades)) / Decimal("2")
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
        sports_trades=len(sports_trades),
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

    return SportsTraderScore(
        username=str(leaderboard_row.get("userName") or ""),
        wallet_address=wallet_address,
        leaderboard_rank=as_int(leaderboard_row.get("rank")) or leaderboard_rank,
        leaderboard_pnl=leaderboard_pnl,
        leaderboard_volume=leaderboard_volume,
        sports_trades=len(sports_trades),
        sports_markets=len(markets),
        sports_volume=total_volume,
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


def is_sports_trade(trade: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(trade.get(key) or "").lower()
        for key in ("title", "slug", "eventSlug", "category", "outcome")
    )
    return any(term in haystack for term in SPORT_TERMS)


# Crypto (this project's own automated BTC 5m bots included) and esports
# (its own dedicated, explicitly paper-only vertical - see
# atlantis/services/esports_traders.py::ESPORTS_TERMS, kept separate on
# purpose) are the only two exclusions. Everything else - sports, politics,
# macro/Fed decisions, company-valuation markets, entertainment, etc. -
# counts as a valid copy-trading target. Added 2026-08-16 at the owner's
# request to broaden the live consensus pipeline beyond sports-only
# (`is_sports_trade` above is kept as-is for the standalone sports
# discovery/backtest tooling that still wants a sports-specific signal).
CRYPTO_TERMS = {
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "ripple",
    "dogecoin", "doge", "cardano", "ada", "litecoin", "ltc", "polkadot",
    "dot", "chainlink", "link", "avalanche", "avax", "polygon", "matic",
    "shiba", "shib", "pepe", "crypto", "cryptocurrency", "altcoin",
    "memecoin", "defi", "nft", "stablecoin", "usdc", "usdt", "tether",
    "binance", "coinbase", "satoshi", "blockchain", "web3", "up or down",
    "5m", "hourly", "wormhole", "sui", "ton",
}
ESPORTS_TERMS = {
    "esports", "lol:", "league of legends", "counter-strike", "valorant",
    "dota 2", "overwatch", "rainbow six", "call of duty", "rocket league",
    "apex legends",
}
_NON_COPY_TERMS = CRYPTO_TERMS | ESPORTS_TERMS


def is_copy_target_trade(trade: dict[str, Any]) -> bool:
    """True for any trade/position eligible for the live copy-trading
    consensus (sports OR any other non-crypto, non-esports market) - the
    broadened replacement for is_sports_trade in active_portfolio.py,
    consensus_backtest.py, trader_performance.py and evaluate_wallet.py.
    Field names in those modules (sports_trades, sports_volume, etc.) are
    kept as-is to avoid a wider CSV/dashboard column rename; they now mean
    "copy-target trades", historically sports-only."""
    haystack = " ".join(
        str(trade.get(key) or "").lower()
        for key in ("title", "slug", "eventSlug", "category", "outcome")
    )
    return not any(term in haystack for term in _NON_COPY_TERMS)


def calculate_risk_score(
    *,
    sports_trades: int,
    sports_markets: int,
    sports_volume: Decimal,
    avg_trade_size: Decimal,
    active_positions: int,
) -> Decimal:
    risk = Decimal("35")
    if sports_trades < 100:
        risk += Decimal("15")
    if sports_markets < 20:
        risk += Decimal("15")
    if sports_volume < Decimal("5000"):
        risk += Decimal("15")
    if avg_trade_size > Decimal("1000"):
        risk += Decimal("10")
    if active_positions > 50:
        risk += Decimal("10")
    if sports_markets >= 40 and sports_trades >= 150:
        risk -= Decimal("15")
    return clamp_decimal(risk, Decimal("0"), Decimal("100"))


def verdict(copy_score: Decimal, confidence_score: Decimal, risk_score: Decimal) -> str:
    if copy_score >= 75 and confidence_score >= 70 and risk_score <= 45:
        return "A"
    if copy_score >= 60 and confidence_score >= 50 and risk_score <= 65:
        return "B"
    if copy_score >= 45 and confidence_score >= 35:
        return "C"
    return "REJECT"


def format_sports_traders(scores: list[SportsTraderScore]) -> str:
    if not scores:
        return "No sports traders matched the current filters."

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
            f"{item.sports_trades:>6}  "
            f"{item.sports_markets:>4}  "
            f"{item.sports_volume:>10,.0f}  "
            f"{item.recent_trades_14d:>6}  "
            f"{item.verdict:<7}  "
            f"{item.wallet_address}"
        )
    return "\n".join(lines)


def write_sports_traders_csv(path: Path, scores: list[SportsTraderScore]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(SportsTraderScore.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for score in scores:
            writer.writerow({field: getattr(score, field) for field in fields})


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
