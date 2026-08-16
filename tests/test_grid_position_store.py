from decimal import Decimal
from pathlib import Path

from atlantis.grid_trader.position_store import Position, load_positions, save_positions


def _sample_position(**overrides) -> Position:
    base = dict(
        symbol="BTCUSDT", strategy="flat",
        levels=[Decimal("100.5"), Decimal("101.25")],
        open_qty=[Decimal("0.5"), Decimal(0)],
        realized=Decimal("12.34"), fees=Decimal("0.56"), trades=3,
        opened_at="2026-08-16 10:00:00 UTC",
        take_profit_usd=Decimal(40), stop_loss_usd=Decimal(140),
        last_price=Decimal("100.75"),
    )
    base.update(overrides)
    return Position(**base)


def test_round_trip_preserves_decimal_precision(tmp_path: Path):
    path = tmp_path / "positions.json"
    pos = _sample_position()
    save_positions(path, [pos])
    loaded = load_positions(path)
    assert len(loaded) == 1
    got = loaded[0]
    assert got.symbol == pos.symbol
    assert got.levels == pos.levels
    assert got.open_qty == pos.open_qty
    assert got.realized == pos.realized
    assert got.take_profit_usd == pos.take_profit_usd
    assert isinstance(got.realized, Decimal)


def test_round_trip_preserves_trend_fields(tmp_path: Path):
    path = tmp_path / "positions.json"
    pos = _sample_position(strategy="trend", trend_anchor_date="2026-08-16", trend_day_realized=Decimal("5.5"))
    save_positions(path, [pos])
    got = load_positions(path)[0]
    assert got.trend_anchor_date == "2026-08-16"
    assert got.trend_day_realized == Decimal("5.5")


def test_load_missing_file_returns_empty_list(tmp_path: Path):
    assert load_positions(tmp_path / "nope.json") == []


def test_save_overwrites_previous_content(tmp_path: Path):
    path = tmp_path / "positions.json"
    save_positions(path, [_sample_position(symbol="AAAUSDT")])
    save_positions(path, [_sample_position(symbol="BBBUSDT")])
    loaded = load_positions(path)
    assert len(loaded) == 1
    assert loaded[0].symbol == "BBBUSDT"
