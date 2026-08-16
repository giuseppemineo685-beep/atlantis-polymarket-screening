"""Persists open grid positions across bot restarts - one JSON file,
list of positions. Decimal <-> str at the boundary since json doesn't
know Decimal."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

from atlantis.grid_trader.grid_math import GridState


@dataclass
class Position:
    symbol: str
    strategy: str  # "flat" | "trend"
    levels: list[Decimal]
    open_qty: list[Decimal]
    realized: Decimal
    fees: Decimal
    trades: int
    opened_at: str
    entry_price: Decimal  # market price at OPEN time - never changes, unlike last_price
    take_profit_usd: Decimal
    stop_loss_usd: Decimal
    last_price: Decimal
    trend_anchor_date: str | None = None  # only for strategy=="trend" - which day's grid is live
    trend_day_realized: Decimal = Decimal(0)  # cumulative pnl across days for THIS position's trend take-profit/stop-loss check
    events: list[str] = field(default_factory=list)  # short human-readable log, most recent last

    def state(self) -> GridState:
        return GridState(
            levels=list(self.levels), open_qty=list(self.open_qty),
            realized=self.realized, fees=self.fees, trades=self.trades,
        )

    def apply_state(self, state: GridState) -> None:
        self.open_qty = list(state.open_qty)
        self.realized = state.realized
        self.fees = state.fees
        self.trades = state.trades


def _to_jsonable(pos: Position) -> dict:
    d = asdict(pos)
    d["levels"] = [str(x) for x in pos.levels]
    d["open_qty"] = [str(x) for x in pos.open_qty]
    d["realized"] = str(pos.realized)
    d["fees"] = str(pos.fees)
    d["entry_price"] = str(pos.entry_price)
    d["take_profit_usd"] = str(pos.take_profit_usd)
    d["stop_loss_usd"] = str(pos.stop_loss_usd)
    d["last_price"] = str(pos.last_price)
    d["trend_day_realized"] = str(pos.trend_day_realized)
    return d


def _from_jsonable(d: dict) -> Position:
    return Position(
        symbol=d["symbol"], strategy=d["strategy"],
        levels=[Decimal(x) for x in d["levels"]], open_qty=[Decimal(x) for x in d["open_qty"]],
        realized=Decimal(d["realized"]), fees=Decimal(d["fees"]), trades=d["trades"],
        opened_at=d["opened_at"],
        # Fallback to last_price for positions persisted before this field
        # existed - the best available approximation, not a real gap for
        # anything opened from now on.
        entry_price=Decimal(d.get("entry_price", d["last_price"])),
        take_profit_usd=Decimal(d["take_profit_usd"]),
        stop_loss_usd=Decimal(d["stop_loss_usd"]), last_price=Decimal(d["last_price"]),
        trend_anchor_date=d.get("trend_anchor_date"),
        trend_day_realized=Decimal(d.get("trend_day_realized", "0")),
        events=d.get("events", []),
    )


def load_positions(path: Path) -> list[Position]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [_from_jsonable(d) for d in data]


def save_positions(path: Path, positions: list[Position]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_to_jsonable(p) for p in positions], indent=2))
