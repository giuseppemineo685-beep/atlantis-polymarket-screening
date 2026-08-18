"""Forex grid trader - paper capital, via an OANDA practice account
(free demo, no real capital at risk). Same engine as the Binance bot
(atlantis/grid_trader/grid_math.py, position_store.py, logger.py
reused UNCHANGED), classification via atlantis/forex/screener.py's own
snapshot. Two things the crypto bot never needed:

1. Market-hours gate (atlantis/forex/market_hours.py) - forex closes
   every weekend, unlike crypto's 24/7 markets.
2. No BTC-style market-wide gate and no position/correlation filter yet
   - both were crypto-specific findings, not validated for forex (see
   atlantis/forex/screener.py's docstring).

When config.execute_real_orders is true (the default - see
atlantis/forex/config.yaml), every fill process_bar detects also
submits a REAL market order to the OANDA PRACTICE account (never live -
place_market_order refuses to run unless OANDA_ENV=="practice"), so the
owner can verify trades independently in their own OANDA app instead of
just trusting this repo's dashboard. On order failure, that fill (and
any later fill in the same batch) is rolled back locally via
atlantis.grid_trader.grid_math.undo_fill, so local bookkeeping never
claims a fill that didn't really happen on the broker. Setting
execute_real_orders to false falls back to pure local simulation with
zero other behavior changes.
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
from atlantis.forex.oanda_client import (  # noqa: E402
    InstrumentInfo, OrderResult, candles_to_klines, close_trade, fetch_candles, fetch_currency_instruments,
    fetch_current_price, fetch_open_position, place_market_order, round_units,
)
from atlantis.grid_trader.flat import compute_flat_grid_bounds  # noqa: E402
from atlantis.grid_trader.grid_math import build_levels, process_bar, undo_fill  # noqa: E402
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


def _place_buy(pos: Position, fill, info: InstrumentInfo | None) -> OrderResult:
    if info is None:
        return OrderResult(False, None, None, "sin_info_de_instrumento", None, error="instrumento_desconocido")
    real_units = round_units(fill.qty, info.trade_units_precision, info.minimum_trade_size)
    return place_market_order(pos.symbol, real_units)


def _place_sell(pos: Position, fill, config: ForexTraderConfig, info: InstrumentInfo | None) -> OrderResult:
    """Closes the SPECIFIC OANDA trade this level's buy opened, when we
    know its trade_id (deterministic - see close_trade's docstring for
    why a generic sell isn't safe: OANDA matches a generic sell FIFO
    against whichever trade on the instrument is oldest, not
    necessarily this level's). Falls back to a generic sell, loudly
    logged, only for positions that predate trade_id tracking."""
    origin = fill.level_index - 1
    trade_id = pos.trade_ids[origin] if 0 <= origin < len(pos.trade_ids) else None
    if trade_id is not None:
        return close_trade(trade_id)

    log_event(
        config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
        action="WARN_SIN_TRADE_ID",
        reason=f"nivel #{origin}: sin trade_id registrado, usando venta generica (FIFO no determinista)",
        price=str(fill.price), realized_profit="",
    )
    if info is None:
        return OrderResult(False, None, None, "sin_info_de_instrumento", None, error="instrumento_desconocido")
    real_units = round_units(-fill.qty, info.trade_units_precision, info.minimum_trade_size)
    return place_market_order(pos.symbol, real_units)


def _execute_fills_real(
    pos: Position, state, fills: list, config: ForexTraderConfig, instruments: dict[str, InstrumentInfo],
) -> None:
    """Attempts a REAL broker action for each fill process_bar already
    applied to `state` - buys via place_market_order (recording the
    resulting trade_id per level), sells via close_trade against that
    level's specific trade_id when known (see _place_sell). On the
    first ORDINARY failure at fill index k, rolls back fills[k:] (see
    grid_math.undo_fill's docstring for why trailing fills must also be
    undone), logs one WARN, and stops.

    TRADE_DOESNT_EXIST on a close is NOT an ordinary failure: it means
    the broker believes that trade is already gone, so rolling back
    would make local bookkeeping claim an open position that provably
    isn't there - the exact failure mode this whole mechanism exists to
    prevent. That fill is left as-is (process_bar already zeroed it)
    and the batch continues to the next fill instead of aborting."""
    info = instruments.get(pos.symbol)

    for k, fill in enumerate(fills):
        result = _place_buy(pos, fill, info) if fill.side == "buy" else _place_sell(pos, fill, config, info)

        if not result.success:
            if fill.side == "sell" and result.reject_reason == "TRADE_DOESNT_EXIST":
                origin = fill.level_index - 1
                if 0 <= origin < len(pos.trade_ids):
                    pos.trade_ids[origin] = None
                log_event(
                    config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
                    action="WARN_TRADE_YA_CERRADO",
                    reason=f"nivel #{origin}: OANDA reporta que el trade ya no existe, no se revierte localmente",
                    price=str(fill.price), realized_profit="",
                )
                continue

            for later_fill in fills[k:]:
                undo_fill(state, later_fill)
            log_event(
                config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
                action="WARN_ORDEN_FALLIDA",
                reason=f"nivel #{fill.level_index} {fill.side}: {result.reject_reason or result.error}",
                price=str(fill.price), realized_profit="",
            )
            return

        logged_profit = fill.profit
        if fill.side == "buy":
            # Real filled quantity can differ from the theoretical qty
            # (round_units floors tiny/zero quantities up to the
            # instrument's real minimum) - local bookkeeping must match
            # what actually happened on the broker, not the theoretical
            # target, or this defeats the whole point of the feature.
            state.open_qty[fill.level_index] = abs(result.filled_units)
            pos.trade_ids[fill.level_index] = result.trade_id
        else:
            origin = fill.level_index - 1
            if 0 <= origin < len(pos.trade_ids):
                pos.trade_ids[origin] = None
            if result.realized_pl is not None:
                # Correct local realized (drives the TP/SL check in
                # manage_position) to match what OANDA actually booked,
                # not the theoretical per-level estimate - this is the
                # actual fix for the original bug (our logged profit
                # didn't match the real account's numbers).
                state.realized += (result.realized_pl - fill.profit)
                logged_profit = result.realized_pl

        action = "FILL_BUY_REAL" if fill.side == "buy" else "FILL_SELL_REAL"
        reason = (
            f"nivel #{fill.level_index} teorico @ {fill.price:.6f} qty={fill.qty:.4f} | "
            f"real @ {result.fill_price} qty={result.filled_units}"
        )
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action=action, reason=reason, price=str(fill.price),
            realized_profit=str(logged_profit) if logged_profit is not None else "",
        )


def _log_fills_paper(pos: Position, fills: list, config: ForexTraderConfig) -> None:
    for fill in fills:
        action = "FILL_BUY" if fill.side == "buy" else "FILL_SELL"
        reason = f"nivel #{fill.level_index} @ {fill.price:.6f} qty={fill.qty:.4f}"
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action=action, reason=reason, price=str(fill.price),
            realized_profit=str(fill.profit) if fill.profit is not None else "",
        )


def _close_real(pos: Position, config: ForexTraderConfig, total: Decimal, close_reason: str) -> bool:
    """Confirms with OANDA itself (not local bookkeeping) that the
    broker-side position is flat before dropping local tracking - a
    still-real position must never be silently forgotten. Returns True
    to keep retrying next cycle, False once safe to drop."""
    broker_units = fetch_open_position(pos.symbol)
    if broker_units is None:
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action="WARN_CIERRE_SIN_CONFIRMAR",
            reason="no se pudo confirmar la posicion real en OANDA, reintentando", price="", realized_profit="",
        )
        return True

    if broker_units != 0:
        flatten = place_market_order(pos.symbol, -broker_units)
        if not flatten.success:
            log_event(
                config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
                action="WARN_CIERRE_SIN_CONFIRMAR",
                reason=f"orden de cierre fallo: {flatten.reject_reason or flatten.error}, reintentando",
                price="", realized_profit="",
            )
            return True
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action="CLOSE_REAL", reason=f"aplano posicion real: {broker_units} unidades",
            price=str(flatten.fill_price or ""), realized_profit=str(total),
        )

    log_event(
        config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
        action="CLOSE", reason=close_reason, price=str(pos.last_price), realized_profit=str(total), trades=str(pos.trades),
    )
    return False


def manage_position(pos: Position, config: ForexTraderConfig, instruments: dict[str, InstrumentInfo]) -> bool:
    """Same fill/exit logic as the crypto bot's manage_position, plus (
    when config.execute_real_orders) a real OANDA market order per
    detected fill - see the module docstring for the rollback/close-
    flatten rules. No daily reanchor here yet (trend.py's daily-
    reanchor concept assumes a 24/7 market that always has "today";
    forex's weekend gap makes that trickier and it's not built for
    forex yet, v1 scope). Returns False if the position should be
    dropped (closed)."""
    price_str = fetch_current_price(pos.symbol)
    if price_str is None:
        return True
    price = Decimal(price_str)

    state = pos.state()
    low, high = min(pos.last_price, price), max(pos.last_price, price)
    fills = process_bar(state, low, high, config.usd_per_level, config.fee_rate)

    if config.execute_real_orders:
        _execute_fills_real(pos, state, fills, config, instruments)
    else:
        _log_fills_paper(pos, fills, config)

    pos.apply_state(state)
    pos.last_price = price

    total = state.total(price)
    if total >= pos.take_profit_usd or total <= -pos.stop_loss_usd:
        close_reason = "take_profit" if total >= pos.take_profit_usd else "stop_loss"
        if config.execute_real_orders:
            return _close_real(pos, config, total, close_reason)
        log_event(
            config.log_path, timestamp=_now_str(), symbol=pos.symbol, strategy=pos.strategy,
            action="CLOSE", reason=close_reason, price=str(price), realized_profit=str(total), trades=str(pos.trades),
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
    _log("forex-trader-paper: arrancando (capital de paper, cuenta practice de OANDA)")
    _log(
        f"config: usd_per_level=${config.usd_per_level} niveles={config.num_levels} "
        f"take_profit=${config.take_profit_usd:.2f} stop_loss=${config.stop_loss_usd:.2f}"
    )

    instruments: dict[str, InstrumentInfo] = {}
    if config.execute_real_orders:
        _log("forex-trader: MODO ORDENES REALES activo - cada fill manda una orden real a la cuenta practice de OANDA")
        instruments = {i.name: i for i in fetch_currency_instruments()}
        _log(f"forex-trader: {len(instruments)} instrumentos con info de precision/minimo cargados")
    else:
        _log("forex-trader: modo simulacion local (execute_real_orders=false), sin ordenes reales")

    last_scan = 0.0
    while True:
        positions = load_positions(config.positions_path)
        is_open, reason = market_is_open()

        if is_open:
            remaining = []
            for pos in positions:
                keep = manage_position(pos, config, instruments)
                if keep:
                    remaining.append(pos)
                # Saved after EACH position, not batched to end-of-loop:
                # once a fill can trigger a real order, a crash before
                # persisting would mean forgetting a real fill exists -
                # on restart that could double-buy for real, or leave a
                # real position permanently orphaned. See plan for why
                # this changed from the crypto bot's pure-batch save.
                save_positions(config.positions_path, remaining)

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
