from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client


@dataclass(frozen=True)
class TopTrader:
    rank: int
    wallet_address: str
    username: str
    pnl: Decimal
    volume: Decimal
    roi: Decimal | None
    raw: dict[str, Any]


def get_top_traders(
    *,
    settings: Settings,
    category: str,
    time_period: str,
    order_by: str,
    limit: int,
) -> list[TopTrader]:
    client = build_client(settings)
    rows = list(
        client.iter_leaderboard(
            category=category,
            time_period=time_period,
            order_by=order_by,
            max_rows=limit,
        )
    )
    traders: list[TopTrader] = []
    for index, row in enumerate(rows, start=1):
        pnl = as_decimal(row.get("pnl")) or Decimal("0")
        volume = as_decimal(row.get("vol")) or Decimal("0")
        roi = pnl / volume if volume > 0 else None
        traders.append(
            TopTrader(
                rank=as_int(row.get("rank")) or index,
                wallet_address=str(row.get("proxyWallet") or "").lower(),
                username=str(row.get("userName") or ""),
                pnl=pnl,
                volume=volume,
                roi=roi,
                raw=row,
            )
        )
    return traders


def format_top_traders(traders: list[TopTrader]) -> str:
    if not traders:
        return "No traders returned by Polymarket."

    lines = [
        "rank  username                      pnl          volume       roi        wallet",
        "----  ----------------------------  -----------  -----------  ---------  ------------------------------------------",
    ]
    for trader in traders:
        roi = f"{trader.roi * Decimal('100'):.2f}%" if trader.roi is not None else "n/a"
        username = trader.username[:28] if trader.username else "(no username)"
        lines.append(
            f"{trader.rank:<4}  "
            f"{username:<28}  "
            f"{trader.pnl:>11,.2f}  "
            f"{trader.volume:>11,.2f}  "
            f"{roi:>9}  "
            f"{trader.wallet_address}"
        )
    return "\n".join(lines)


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
