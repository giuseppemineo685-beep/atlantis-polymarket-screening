"""Unified grid trader - paper trading only. ONE strategy, not two: it
continuously scans the universe, classifies each pair's CURRENT regime,
and opens whichever grid mechanic (flat or trend) that regime qualifies
for - see docs/GRID_TRADER_STRATEGIES.md for the full derivation of
every threshold used here. A position, once opened, keeps the exit
rules of whatever regime it was opened under even if the pair's
classification later changes - only NEW entries re-check.

Reuses atlantis/grid_screener/'s own snapshot CSV (refreshed every
15 min by its own cron job) for classification instead of re-fetching
klines for the whole universe here - no point paying that cost twice.

No credentials, no client, no real orders - paper only.
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

from atlantis.grid_screener.binance_client import fetch_current_price, fetch_daily_klines  # noqa: E402
from atlantis.grid_trader.config import GridTraderConfig, load_config  # noqa: E402
from atlantis.grid_trader.flat import compute_flat_grid_bounds  # noqa: E402
from atlantis.grid_trader.grid_math import build_levels, process_bar  # noqa: E402
from atlantis.grid_trader.logger import log_event  # noqa: E402
from atlantis.grid_trader.market_gate import btc_market_ok  # noqa: E402
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


def manage_position(pos: Position, config: GridTraderConfig) -> bool:
    """Polls price, processes fills, checks exits. Returns False if the
    position should be dropped (closed)."""
    price_str = fetch_current_price(pos.symbol)
    if price_str is None:
        return True
    price = Decimal(price_str)

    if pos.strategy == "trend":
        today = _now().date().isoformat()
        if pos.trend_anchor_date and pos.trend_anchor_date != today:
            day_total = pos.state().total(price)
            pos.trend_day_realized += day_total
            log_event(
                config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy="trend",
                action="DAY_REANCHOR", reason=f"dia {pos.trend_anchor_date} cerrado, pnl del dia=${day_total:.2f}",
                price=str(price), realized_profit=str(day_total), trades=str(pos.trades),
            )
            if pos.trend_day_realized >= pos.take_profit_usd or pos.trend_day_realized <= -pos.stop_loss_usd:
                reason = "take_profit" if pos.trend_day_realized >= pos.take_profit_usd else "stop_loss"
                log_event(
                    config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy="trend",
                    action="CLOSE", reason=reason, price=str(price),
                    realized_profit=str(pos.trend_day_realized), trades=str(pos.trades),
                )
                return False

            btc_daily = fetch_daily_klines("BTCUSDT")
            btc_ok, btc_reason = btc_market_ok(btc_daily) if btc_daily else (True, "sin datos de BTC")
            if not btc_ok:
                log_event(
                    config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy="trend",
                    action="SKIP_REANCHOR", reason=btc_reason, price=str(price),
                )
                pos.open_qty = [Decimal(0)] * len(pos.levels)
                pos.realized = Decimal(0)
                pos.fees = Decimal(0)
                pos.trades = 0
                pos.last_price = price
                pos.trend_anchor_date = today
                return True

            daily = fetch_daily_klines(pos.symbol)
            if daily:
                lower, upper = compute_trend_grid_bounds(daily, "long")
                pos.levels = build_levels(lower, upper, config.num_levels)
            pos.open_qty = [Decimal(0)] * len(pos.levels)
            pos.realized = Decimal(0)
            pos.fees = Decimal(0)
            pos.trades = 0
            pos.trend_anchor_date = today
            # Grid just got rebuilt with brand-new levels for today - the
            # OLD last_price (from however many hours ago the previous
            # poll was) has no relationship to these new levels. Anchor
            # to today's price now and defer the first fill-check to the
            # next poll, instead of feeding process_bar a [stale_price,
            # fresh_price] span that has nothing to do with real
            # movement against the new grid.
            pos.last_price = price
            return True

    state = pos.state()
    low, high = min(pos.last_price, price), max(pos.last_price, price)
    fills = process_bar(state, low, high, config.usd_per_level, config.fee_rate)
    pos.apply_state(state)
    pos.last_price = price

    for fill in fills:
        action = "FILL_BUY" if fill.side == "buy" else "FILL_SELL"
        reason = f"nivel #{fill.level_index} @ ${fill.price:.8f} qty={fill.qty:.6f}"
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action=action, reason=reason, price=str(fill.price),
            realized_profit=str(fill.profit) if fill.profit is not None else "",
        )

    total = state.total(price) if pos.strategy == "flat" else pos.trend_day_realized + state.total(price)
    if total >= pos.take_profit_usd or total <= -pos.stop_loss_usd:
        reason = "take_profit" if total >= pos.take_profit_usd else "stop_loss"
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action="CLOSE", reason=reason, price=str(price), realized_profit=str(total), trades=str(pos.trades),
        )
        return False

    return True


def scan_for_new_entries(positions: list[Position], config: GridTraderConfig) -> list[Position]:
    open_symbols = {p.symbol for p in positions}
    slots = config.max_concurrent_positions - len(positions)
    if slots <= 0:
        _log("grid-trader: sin cupos libres, no se escanean entradas nuevas")
        return positions

    snapshot = read_screener_snapshot(config.screener_snapshot_path)
    if not snapshot:
        _log("grid-trader: sin snapshot del screener todavia, reintentando en el proximo ciclo")
        return positions

    btc_daily = fetch_daily_klines("BTCUSDT")
    btc_ok, btc_reason = btc_market_ok(btc_daily) if btc_daily else (True, "sin datos de BTC")
    _log(f"grid-trader: BTC {btc_reason}")

    flat_candidates = []
    trend_candidates = []
    for row in snapshot:
        symbol = row["symbol"]
        if symbol in open_symbols:
            continue
        try:
            regimen = row["regimen"]
            pos_pct = float(row["pos_pct"])
            corr_value = float(row["corr_value"])
            er = float(row["er"])
            move_pct = float(row["net_move_pct"])
        except (KeyError, ValueError):
            continue

        if regimen == "rango" and config.flat_pos_min <= pos_pct <= config.flat_pos_max and abs(corr_value) < float(config.flat_max_corr):
            flat_candidates.append((symbol, float(row.get("score") or 0)))
        elif regimen == "fuerte" and move_pct >= 0:
            trend_candidates.append((symbol, er))

    flat_candidates.sort(key=lambda c: c[1], reverse=True)
    trend_candidates.sort(key=lambda c: c[1], reverse=True)

    for symbol, _score in flat_candidates:
        if slots <= 0:
            break
        if not btc_ok:
            continue
        daily = fetch_daily_klines(symbol)
        time.sleep(0.1)
        if not daily or len(daily) < 14:
            continue
        lower, upper = compute_flat_grid_bounds(daily)
        levels = build_levels(lower, upper, config.num_levels)
        price_str = fetch_current_price(symbol)
        if price_str is None:
            continue
        pos = Position(
            symbol=symbol, strategy="flat", levels=levels, open_qty=[Decimal(0)] * len(levels),
            realized=Decimal(0), fees=Decimal(0), trades=0, opened_at=_now_str(),
            take_profit_usd=config.take_profit_usd, stop_loss_usd=config.stop_loss_usd,
            last_price=Decimal(price_str),
        )
        positions.append(pos)
        slots -= 1
        log_event(
            config.log_path, timestamp=_now_str(), symbol=symbol, strategy="flat", action="OPEN",
            reason=f"rango [{lower:.6f},{upper:.6f}]", price=price_str,
        )
        _log(f"grid-trader: OPEN flat {symbol} @ {price_str}")

    for symbol, er in trend_candidates:
        if slots <= 0:
            break
        if not btc_ok:
            continue
        daily = fetch_daily_klines(symbol)
        time.sleep(0.1)
        if not daily or len(daily) < 14:
            continue
        lower, upper = compute_trend_grid_bounds(daily, "long")
        levels = build_levels(lower, upper, config.num_levels)
        price_str = fetch_current_price(symbol)
        if price_str is None:
            continue
        pos = Position(
            symbol=symbol, strategy="trend", levels=levels, open_qty=[Decimal(0)] * len(levels),
            realized=Decimal(0), fees=Decimal(0), trades=0, opened_at=_now_str(),
            take_profit_usd=config.take_profit_usd, stop_loss_usd=config.stop_loss_usd,
            last_price=Decimal(price_str), trend_anchor_date=_now().date().isoformat(),
        )
        positions.append(pos)
        slots -= 1
        log_event(
            config.log_path, timestamp=_now_str(), symbol=symbol, strategy="trend", action="OPEN",
            reason=f"ER={er:.2f} rango [{lower:.6f},{upper:.6f}]", price=price_str,
        )
        _log(f"grid-trader: OPEN trend {symbol} @ {price_str} (ER={er:.2f})")

    return positions


def main() -> None:
    config = load_config()
    _log("grid-trader-paper: arrancando (paper trading, sin dinero real)")
    _log(
        f"config: usd_per_level=${config.usd_per_level} niveles={config.num_levels} "
        f"take_profit=${config.take_profit_usd:.2f} stop_loss=${config.stop_loss_usd:.2f} "
        f"max_posiciones={config.max_concurrent_positions}"
    )

    last_scan = 0.0
    while True:
        positions = load_positions(config.positions_path)

        remaining = []
        for pos in positions:
            keep = manage_position(pos, config)
            if keep:
                remaining.append(pos)
        positions = remaining

        if time.time() - last_scan >= config.scan_interval_seconds:
            positions = scan_for_new_entries(positions, config)
            last_scan = time.time()

        save_positions(config.positions_path, positions)

        summary = ", ".join(f"{p.symbol}({p.strategy})" for p in positions) or "ninguna"
        print(f"\r{_now_str()} | posiciones abiertas: {len(positions)} [{summary}]", end="")
        time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
