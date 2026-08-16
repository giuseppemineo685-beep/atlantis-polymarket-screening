from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from atlantis.config import Settings
from atlantis.polymarket.client import build_client
from atlantis.services.sports_traders import as_decimal, is_copy_target_trade


VERDICT_ORDER = {"REJECT": 0, "C": 1, "B": 2, "A": 3}


@dataclass(frozen=True)
class PortfolioTrader:
    username: str
    wallet_address: str
    copy_score: Decimal
    confidence_score: Decimal
    risk_score: Decimal
    verdict: str


@dataclass(frozen=True)
class PortfolioSignal:
    action: str
    stake: Decimal
    conviction: Decimal
    condition_id: str
    outcome: str
    asset: str
    title: str
    slug: str
    current_price: Decimal
    avg_entry_price: Decimal
    total_current_value: Decimal
    supporting_traders: int
    opposing_traders: int
    supporting_score: Decimal
    opposing_score: Decimal
    traders: str
    opposing_outcomes: str


def build_active_portfolio(
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
    traders = read_portfolio_traders(traders_csv, min_verdict=min_verdict)[:max_traders]
    client = build_client(settings)
    positions_by_key: dict[tuple[str, str], list[tuple[PortfolioTrader, dict[str, Any]]]] = defaultdict(list)

    for trader in traders:
        positions = client.get_user_positions(wallet_address=trader.wallet_address)
        for position in positions:
            if not is_copy_target_trade(position):
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


def make_signal(
    *,
    bankroll: Decimal,
    max_stake_pct: Decimal,
    asset: str,
    condition_id: str,
    rows: list[tuple[PortfolioTrader, dict[str, Any]]],
    representative: dict[str, Any],
    supporting_score: Decimal,
    opposing_score: Decimal,
    opposing_traders: int,
    opposing_outcomes: list[str],
) -> PortfolioSignal:
    total_value = sum(as_decimal(position.get("currentValue")) or Decimal("0") for _, position in rows)
    total_size = sum(as_decimal(position.get("size")) or Decimal("0") for _, position in rows)
    weighted_entry = Decimal("0")
    for _, position in rows:
        size = as_decimal(position.get("size")) or Decimal("0")
        weighted_entry += (as_decimal(position.get("avgPrice")) or Decimal("0")) * size
    avg_entry = weighted_entry / total_size if total_size > 0 else Decimal("0")
    current_price = as_decimal(representative.get("curPrice")) or Decimal("0")
    supporting_traders = len({trader.wallet_address for trader, _ in rows})
    conflict_ratio = opposing_score / supporting_score if supporting_score > 0 else Decimal("0")
    conviction = clamp(
        supporting_score / Decimal("3")
        + Decimal(supporting_traders * 8)
        + min(total_value / Decimal("100"), Decimal("25"))
        - conflict_ratio * Decimal("35"),
        Decimal("0"),
        Decimal("100"),
    )
    action = classify_signal(
        supporting_traders=supporting_traders,
        opposing_traders=opposing_traders,
        conflict_ratio=conflict_ratio,
        conviction=conviction,
    )
    stake = suggested_stake(
        action=action,
        bankroll=bankroll,
        max_stake_pct=max_stake_pct,
        conviction=conviction,
        supporting_traders=supporting_traders,
        conflict_ratio=conflict_ratio,
    )

    traders = ", ".join(
        sorted({trader.username or trader.wallet_address[:10] for trader, _ in rows})
    )
    return PortfolioSignal(
        action=action,
        stake=stake,
        conviction=conviction,
        condition_id=condition_id,
        outcome=str(representative.get("outcome") or ""),
        asset=asset,
        title=str(representative.get("title") or ""),
        slug=str(representative.get("slug") or ""),
        current_price=current_price,
        avg_entry_price=avg_entry,
        total_current_value=total_value,
        supporting_traders=supporting_traders,
        opposing_traders=opposing_traders,
        supporting_score=supporting_score,
        opposing_score=opposing_score,
        traders=traders,
        opposing_outcomes=", ".join(sorted(set(outcome for outcome in opposing_outcomes if outcome))),
    )


def classify_signal(
    *,
    supporting_traders: int,
    opposing_traders: int,
    conflict_ratio: Decimal,
    conviction: Decimal,
) -> str:
    if supporting_traders >= 2 and conflict_ratio <= Decimal("0.35") and conviction >= Decimal("60"):
        return "COPY"
    if supporting_traders >= 1 and conflict_ratio <= Decimal("0.75") and conviction >= Decimal("40"):
        return "WAIT"
    if opposing_traders > 0 and conflict_ratio > Decimal("0.75"):
        return "CONFLICT"
    return "IGNORE"


def suggested_stake(
    *,
    action: str,
    bankroll: Decimal,
    max_stake_pct: Decimal,
    conviction: Decimal,
    supporting_traders: int,
    conflict_ratio: Decimal,
) -> Decimal:
    if action != "COPY" or bankroll <= 0:
        return Decimal("0")
    base = bankroll * max_stake_pct
    conviction_factor = conviction / Decimal("100")
    consensus_factor = min(Decimal("1"), Decimal(supporting_traders) / Decimal("4"))
    conflict_factor = max(Decimal("0"), Decimal("1") - conflict_ratio)
    return (base * conviction_factor * consensus_factor * conflict_factor).quantize(Decimal("0.01"))


def read_portfolio_traders(path: Path, *, min_verdict: str) -> list[PortfolioTrader]:
    min_rank = VERDICT_ORDER[min_verdict]
    traders: list[PortfolioTrader] = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            verdict = str(row.get("verdict") or "REJECT")
            if VERDICT_ORDER.get(verdict, 0) < min_rank:
                continue
            traders.append(
                PortfolioTrader(
                    username=str(row.get("username") or ""),
                    wallet_address=str(row.get("wallet_address") or "").lower(),
                    copy_score=as_decimal(row.get("copy_score")) or Decimal("0"),
                    confidence_score=as_decimal(row.get("confidence_score")) or Decimal("0"),
                    risk_score=as_decimal(row.get("risk_score")) or Decimal("100"),
                    verdict=verdict,
                )
            )
    traders.sort(key=lambda item: (item.copy_score, item.confidence_score), reverse=True)
    return [trader for trader in traders if trader.wallet_address]


def format_active_portfolio(signals: list[PortfolioSignal], *, limit: int) -> str:
    if not signals:
        return "No active sports portfolio signals found."
    lines = [
        "action    stake   conv  tr  opp  price  value       outcome       title",
        "--------  ------  ----  --  ---  -----  ----------  ------------  ----------------------------------------",
    ]
    for signal in signals[:limit]:
        lines.append(
            f"{signal.action:<8}  "
            f"{signal.stake:>6,.2f}  "
            f"{signal.conviction:>4.0f}  "
            f"{signal.supporting_traders:>2}  "
            f"{signal.opposing_traders:>3}  "
            f"{signal.current_price:>5.3f}  "
            f"{signal.total_current_value:>10,.2f}  "
            f"{signal.outcome[:12]:<12}  "
            f"{signal.title[:40]}"
        )
    return "\n".join(lines)


def write_active_portfolio_csv(path: Path, signals: list[PortfolioSignal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(PortfolioSignal.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for signal in signals:
            writer.writerow({field: getattr(signal, field) for field in fields})


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))
