from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.active_portfolio import (
    PortfolioSignal,
    PortfolioTrader,
    classify_signal,  # noqa: F401 - re-exported for callers that import from this module
    format_active_portfolio,  # noqa: F401
    make_signal,
    read_portfolio_traders,
    suggested_stake,  # noqa: F401
    write_active_portfolio_csv,  # noqa: F401
)
from atlantis.services.esports_traders import is_esports_trade
from atlantis.services.sports_traders import as_decimal


def build_active_portfolio_esports(
    *,
    settings: Settings,
    traders_csv: Path,
    min_verdict: str,
    max_traders: int,
    bankroll: Decimal,
    max_stake_pct: Decimal,
    min_position_value: Decimal,
    min_price: Decimal,
    max_price: Decimal,
) -> list[PortfolioSignal]:
    """Same consensus mechanism as active_portfolio.py::build_active_portfolio
    (make_signal/classify_signal/suggested_stake are reused unchanged, they're
    vertical-agnostic) - the only real difference is is_esports_trade instead
    of is_sports_trade when filtering which positions count at all."""
    traders = read_portfolio_traders(traders_csv, min_verdict=min_verdict)[:max_traders]
    client = build_client(settings)
    positions_by_key: dict[tuple[str, str], list[tuple[PortfolioTrader, dict[str, Any]]]] = defaultdict(list)

    for trader in traders:
        positions = client.get_user_positions(wallet_address=trader.wallet_address)
        for position in positions:
            if not is_esports_trade(position):
                continue
            current_value = as_decimal(position.get("currentValue")) or Decimal("0")
            current_price = as_decimal(position.get("curPrice")) or Decimal("0")
            condition_id = str(position.get("conditionId") or "")
            asset = str(position.get("asset") or "")
            if not condition_id or not asset:
                continue
            if current_value < min_position_value:
                continue
            if current_price < min_price or current_price > max_price:
                continue
            positions_by_key[(condition_id, asset)].append((trader, position))

    signals = []
    grouped_by_market: dict[str, list[tuple[str, list[tuple[PortfolioTrader, dict[str, Any]]]]]] = defaultdict(list)
    for (condition_id, asset), rows in positions_by_key.items():
        grouped_by_market[condition_id].append((asset, rows))

    for condition_id, outcome_groups in grouped_by_market.items():
        market_scores = {
            asset: sum(trader.copy_score for trader, _ in rows)
            for asset, rows in outcome_groups
        }
        for asset, rows in outcome_groups:
            representative = rows[0][1]
            supporting_score = market_scores[asset]
            opposing_score = sum(score for other_asset, score in market_scores.items() if other_asset != asset)
            opposing_traders = sum(
                len(other_rows)
                for other_asset, other_rows in outcome_groups
                if other_asset != asset
            )
            signal = make_signal(
                bankroll=bankroll,
                max_stake_pct=max_stake_pct,
                asset=asset,
                condition_id=condition_id,
                rows=rows,
                representative=representative,
                supporting_score=supporting_score,
                opposing_score=opposing_score,
                opposing_traders=opposing_traders,
                opposing_outcomes=[
                    str(other_rows[0][1].get("outcome") or "")
                    for other_asset, other_rows in outcome_groups
                    if other_asset != asset and other_rows
                ],
            )
            signals.append(signal)

    signals.sort(
        key=lambda item: (
            item.action == "COPY",
            item.conviction,
            item.supporting_traders,
            item.total_current_value,
        ),
        reverse=True,
    )
    return signals
