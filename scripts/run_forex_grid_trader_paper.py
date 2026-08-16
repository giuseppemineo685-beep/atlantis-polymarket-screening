"""Forex grid trader - paper trading only, via an OANDA practice
account (free demo, no real capital). Same engine as the Binance bot
(atlantis/grid_trader/grid_math.py, position_store.py, logger.py
reused UNCHANGED), classification via atlantis/forex/screener.py's own
snapshot. Two things the crypto bot never needed:

1. Market-hours gate (atlantis/forex/market_hours.py) - forex closes
   every weekend, unlike crypto's 24/7 markets.
2. No BTC-style market-wide gate and no position/correlation filter yet
   - both were crypto-specific findings, not validated for forex (see
   atlantis/forex/screener.py's docstring).

No credentials submit real orders - paper only, reads OANDA purely as
a market-data source.
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.forex.config import ForexTraderConfig, load_config  # noqa: E402
from atlantis.forex.market_hours import market_is_open, ok_to_open_new_position  # noqa: E402
from atlantis.forex.oanda_client import candles_to_klines, fetch_candles, fetch_current_price  # noqa: E402
from atlantis.grid_trader.flat import compute_flat_grid_bounds  # noqa: E402
from atlantis.grid_trader.grid_math import build_levels, process_bar  # noqa: E402
from atlantis.grid_trader.logger import log_event  # noqa: E402
from atlantis.grid_trader.position_store import Position, load_positions, save_positions  # noqa: E402
from atlantis.grid_trader.trend import compute_trend_grid_bounds  # noqa: E402


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_str() -> str:
    return _now().strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now_str()}] {msg}")


def read_screener_snapshot(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def manage_position(pos: Position, config: ForexTraderConfig) -> bool:
    """Same fill/exit logic as the crypto bot's manage_position - no
    daily reanchor here yet (trend.py's daily-reanchor concept assumes
    a 24/7 market that always has "today"; forex's weekend gap makes
    that trickier and it's not built for forex yet, v1 scope). Returns
    False if the position should be dropped (closed)."""
    price_str = fetch_current_price(pos.symbol)
    if price_str is None:
        return True
    price = Decimal(price_str)

    state = pos.state()
    low, high = min(pos.last_price, price), max(pos.last_price, price)
    fills = process_bar(state, low, high, config.usd_per_level, config.fee_rate)
    pos.apply_state(state)
    pos.last_price = price

    for fill in fills:
        action = "FILL_BUY" if fill.side == "buy" else "FILL_SELL"
        reason = f"nivel #{fill.level_index} @ {fill.price:.6f} qty={fill.qty:.4f}"
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action=action, reason=reason, price=str(fill.price),
            realized_profit=str(fill.profit) if fill.profit is not None else "",
        )

    total = state.total(price)
    if total >= pos.take_profit_usd or total <= -pos.stop_loss_usd:
        reason = "take_profit" if total >= pos.take_profit_usd else "stop_loss"
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action="CLOSE", reason=reason, price=str(price), realized_profit=str(total), trades=str(pos.trades),
        )
        return False

    return True


def scan_for_new_entries(positions: list[Position], config: ForexTraderConfig) -> list[Position]:
    open_symbols = {p.symbol for p in positions}
    slots = config.max_concurrent_positions - len(positions)
    if slots <= 0:
        _log("forex-trader: sin cupos libres, no se escanean entradas nuevas")
        return positions

    ok_to_open, market_reason = ok_to_open_new_position()
    _log(f"forex-trader: {market_reason}")
    if not ok_to_open:
        return positions

    snapshot = read_screener_snapshot(config.screener_snapshot_path)
    if not snapshot:
        _log("forex-trader: sin snapshot del screener todavia, reintentando en el proximo ciclo")
        return positions

    flat_candidates = []
    trend_candidates = []
    for row in snapshot:
        instrument = row["instrument"]
        if instrument in open_symbols:
            continue
        try:
            regimen = row["regimen"]
            pos_pct = float(row["pos_pct"])
            er = float(row["er"])
            move_pct = float(row["net_move_pct"])
        except (KeyError, ValueError):
            continue

        if regimen == "rango" and config.flat_pos_min <= pos_pct <= config.flat_pos_max:
            flat_candidates.append((instrument, 0))
        elif regimen == "fuerte" and move_pct >= 0:
            trend_candidates.append((instrument, er))

    trend_candidates.sort(key=lambda c: c[1], reverse=True)

    for instrument, _ in flat_candidates:
        if slots <= 0:
            break
        daily = candles_to_klines(fetch_candles(instrument, granularity="D", count=35) or [])
        time.sleep(0.15)
        if len(daily) < 14:
            continue
        lower, upper = compute_flat_grid_bounds(daily)
        levels = build_levels(lower, upper, config.num_levels)
        price_str = fetch_current_price(instrument)
        if price_str is None:
            continue
        pos = Position(
            symbol=instrument, strategy="flat", levels=levels, open_qty=[Decimal(0)] * len(levels),
            realized=Decimal(0), fees=Decimal(0), trades=0, opened_at=_now_str(),
            entry_price=Decimal(price_str),
            take_profit_usd=config.take_profit_usd, stop_loss_usd=config.stop_loss_usd,
            last_price=Decimal(price_str),
        )
        positions.append(pos)
        slots -= 1
        log_event(
            config.log_path, timestamp=_now_str(), symbol=instrument, strategy="flat", action="OPEN",
            reason=f"rango [{lower:.6f},{upper:.6f}]", price=price_str,
        )
        _log(f"forex-trader: OPEN flat {instrument} @ {price_str}")

    for instrument, er in trend_candidates:
        if slots <= 0:
            break
        daily = candles_to_klines(fetch_candles(instrument, granularity="D", count=35) or [])
        time.sleep(0.15)
        if len(daily) < 14:
            continue
        lower, upper = compute_trend_grid_bounds(daily, "long")
        levels = build_levels(lower, upper, config.num_levels)
        price_str = fetch_current_price(instrument)
        if price_str is None:
            continue
        pos = Position(
            symbol=instrument, strategy="trend", levels=levels, open_qty=[Decimal(0)] * len(levels),
            realized=Decimal(0), fees=Decimal(0), trades=0, opened_at=_now_str(),
            entry_price=Decimal(price_str),
            take_profit_usd=config.take_profit_usd, stop_loss_usd=config.stop_loss_usd,
            last_price=Decimal(price_str), trend_anchor_date=_now().date().isoformat(),
        )
        positions.append(pos)
        slots -= 1
        log_event(
            config.log_path, timestamp=_now_str(), symbol=instrument, strategy="trend", action="OPEN",
            reason=f"ER={er:.2f} rango [{lower:.6f},{upper:.6f}]", price=price_str,
        )
        _log(f"forex-trader: OPEN trend {instrument} @ {price_str} (ER={er:.2f})")

    return positions


def main() -> None:
    config = load_config()
    _log("forex-trader-paper: arrancando (paper trading, sin dinero real, cuenta practice de OANDA)")
    _log(
        f"config: usd_per_level=${config.usd_per_level} niveles={config.num_levels} "
        f"take_profit=${config.take_profit_usd:.2f} stop_loss=${config.stop_loss_usd:.2f}"
    )

    last_scan = 0.0
    while True:
        positions = load_positions(config.positions_path)
        is_open, reason = market_is_open()

        if is_open:
            remaining = []
            for pos in positions:
                keep = manage_position(pos, config)
                if keep:
                    remaining.append(pos)
            positions = remaining

            if time.time() - last_scan >= config.scan_interval_seconds:
                positions = scan_for_new_entries(positions, config)
                last_scan = time.time()
        else:
            _log(f"forex-trader: {reason} - no se procesan fills ni entradas nuevas")

        save_positions(config.positions_path, positions)

        summary = ", ".join(f"{p.symbol}({p.strategy})" for p in positions) or "ninguna"
        print(f"\r{_now_str()} | mercado {'abierto' if is_open else 'cerrado'} | posiciones: {len(positions)} [{summary}]", end="")
        time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
