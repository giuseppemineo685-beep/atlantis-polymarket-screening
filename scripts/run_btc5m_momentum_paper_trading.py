"""BTC 'Up or Down 5m' DIRECTIONAL momentum bot - LIVE PAPER TRADING.
Deliberately takes one-sided risk on BTC's own short-term direction,
unlike run_btc5m_hedge_paper_trading.py which never does - see
atlantis/btc5m_momentum/signal.py for the 2026-08-09 real-data
derivation of the signal this is built on (pre-window BTC momentum
predicts the window's Up/Down outcome with a real, if modest, edge that
Polymarket's own price does not appear to account for).

One decision per window, made once at window open: compute momentum from
a rolling BTC price buffer, and if it clears the minimum-move threshold
AND the favored side is still cheap enough, buy it. No further action
for the rest of the window - unlike the hedge bot, there is no second
leg to complete.

No credentials, no client, no real orders - paper only.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.btc5m_hedge.market_data import (  # noqa: E402
    MarketInfo,
    current_window_start,
    fetch_market_by_slug,
    fetch_order_book_asks,
    fetch_resolved_outcome,
    slug_for_window,
)
from atlantis.btc5m_hedge.portfolio import fill_from_levels  # noqa: E402
from atlantis.btc5m_momentum.config import load_config  # noqa: E402
from atlantis.btc5m_momentum.logger import (  # noqa: E402
    backfill_missing_outcomes,
    compute_session_realized,
    log_decision,
    log_window_summary,
)
from atlantis.btc5m_momentum.market_data import fetch_binance_price  # noqa: E402
from atlantis.btc5m_momentum.position import Position  # noqa: E402
from atlantis.btc5m_momentum.signal import UP, Decision, compute_momentum_pct, decide, evaluate_signal  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


def price_from_lookback(history: deque[tuple[float, Decimal]], now_ts: float, lookback_seconds: int) -> Decimal | None:
    """Nearest sample AT OR BEFORE now_ts - lookback_seconds - the exact
    lookback instant almost never has a sample landing precisely on it
    (polling is ~1s apart), so this walks back to the closest one that
    doesn't reach INTO the window being predicted (never picks a sample
    newer than the target instant)."""
    target = now_ts - lookback_seconds
    best: Decimal | None = None
    for ts, price in history:
        if ts <= target:
            best = price
        else:
            break
    return best


def decide_and_open(
    *,
    price_now: Decimal | None,
    now_ts: float,
    price_history: deque[tuple[float, Decimal]],
    config,
    market: MarketInfo,
    session_realized: Decimal,
) -> tuple[Position, Decision, Decimal | None]:
    """Runs the one-shot entry decision for a freshly-opened window.
    `price_now` is the SAME sample the caller already fetched for the
    rolling history this poll - deliberately not re-fetched here, so the
    momentum calc and the history buffer can never disagree about what
    "now" means."""
    if session_realized <= -config.risk.max_session_loss_usd:
        return Position(), Decision(
            False, None,
            f"circuit breaker: perdida de sesion ${-session_realized:.2f} alcanzo el limite (${config.risk.max_session_loss_usd})",
        ), None

    price_lookback = price_from_lookback(price_history, now_ts, config.signal.momentum_lookback_seconds)
    price_trend_lookback = price_from_lookback(price_history, now_ts, config.signal.trend_lookback_seconds)
    if price_now is None or price_lookback is None or price_trend_lookback is None:
        return Position(), Decision(False, None, "sin suficiente historial de precio de BTC todavia"), None

    momentum_pct = compute_momentum_pct(price_now, price_lookback)
    long_momentum_pct = compute_momentum_pct(price_now, price_trend_lookback)
    signal = evaluate_signal(
        momentum_pct, min_pct_move=config.signal.momentum_min_pct_move, long_momentum_pct=long_momentum_pct
    )
    if signal is None:
        return Position(), decide(None, None, max_entry_price=config.risk.max_entry_price), momentum_pct

    token_id = market.up_token_id if signal.side == UP else market.down_token_id
    levels = fetch_order_book_asks(token_id)
    favored_price = levels[0].price if levels else None

    decision = decide(signal, favored_price, max_entry_price=config.risk.max_entry_price)
    if not decision.should_bet:
        return Position(), decision, momentum_pct

    requested_quantity = config.risk.bet_size_usd / favored_price
    fill = fill_from_levels(requested_quantity, levels)
    if fill.filled_quantity <= 0:
        return Position(), Decision(False, None, "libro se vacio entre la lectura y la ejecucion"), momentum_pct

    position = Position(side=decision.side, quantity=fill.filled_quantity, cost=fill.total_cost)
    return position, decision, momentum_pct


def main() -> None:
    config = load_config()
    _log("btc5m-momentum-paper: arrancando (paper trading, sin dinero real)")

    current_slug: str | None = None
    market: MarketInfo | None = None
    position = Position()
    momentum_pct: Decimal | None = None
    session_realized = Decimal(0)
    # ~2x the lookback so price_from_lookback always has something to walk
    # back to even right after startup or a brief fetch gap.
    price_history: deque[tuple[float, Decimal]] = deque(
        maxlen=max(config.signal.momentum_lookback_seconds, config.signal.trend_lookback_seconds) * 2
    )

    while True:
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        window_start = current_window_start(now, config.market.window_seconds)
        slug = slug_for_window(window_start)
        close_at = window_start + timedelta(seconds=config.market.window_seconds)
        seconds_remaining = (close_at - now).total_seconds()

        price = fetch_binance_price()
        if price is not None:
            price_history.append((now_ts, price))

        if slug != current_slug:
            closing_slug = current_slug
            closing_position = position
            closing_momentum = momentum_pct
            current_slug = slug
            market = None

            if closing_slug is not None:
                print()
                outcome = fetch_resolved_outcome(closing_slug)
                log_window_summary(
                    config.paper.window_summary_log_path,
                    window_slug=closing_slug,
                    position=closing_position,
                    momentum_pct=closing_momentum,
                    realized_outcome=outcome or "",
                    window_closed_at=_now(),
                )
                if outcome:
                    realized = closing_position.realized_profit(outcome)
                    _log(
                        f"btc5m-momentum-paper: {closing_slug} cerrada - side={closing_position.side or '(sin apuesta)'}, "
                        f"outcome={outcome}, realized=${realized:.2f}"
                    )
                else:
                    _log(f"btc5m-momentum-paper: {closing_slug} cerrada - outcome desconocido aun")

                updated = backfill_missing_outcomes(config.paper.window_summary_log_path, fetch_resolved_outcome)
                if updated:
                    _log(f"btc5m-momentum-paper: backfill resolvio {updated} ventana(s) que estaban pendientes")

                # Recomputed from disk, not accumulated in memory - see
                # compute_session_realized's own docstring for the real
                # 2026-08-15 bug this fixes (the circuit breaker silently
                # never fired across 6 real days despite -$64.55 at its
                # worst point, because the in-memory total only updated
                # on the "resolved right at close" path, which almost
                # never succeeds).
                session_realized = compute_session_realized(config.paper.window_summary_log_path)
                _log(f"btc5m-momentum-paper: sesion acumulada real=${session_realized:.2f}")

        if market is None:
            fetched = fetch_market_by_slug(slug)
            if fetched is None:
                _log(f"btc5m-momentum-paper: no se pudo obtener el mercado para {slug}, reintentando")
                time.sleep(config.market.poll_interval_seconds)
                continue
            market = fetched
            _log(f"btc5m-momentum-paper: siguiendo nueva ventana {slug}")

            position, decision, momentum_pct = decide_and_open(
                price_now=price, now_ts=now_ts, price_history=price_history, config=config,
                market=market, session_realized=session_realized,
            )
            log_decision(
                config.paper.decision_log_path,
                timestamp=_now(), window_slug=slug, decision=decision,
                momentum_pct=momentum_pct, position=position,
            )
            if decision.should_bet:
                _log(f"btc5m-momentum-paper: BET {position.side} x{position.quantity:.4f} (${position.cost:.2f}) - {decision.reason}")
            else:
                _log(f"btc5m-momentum-paper: NO_BET - {decision.reason}")

        print(
            f"\r{slug} | {seconds_remaining:6.1f}s left | "
            f"posicion={position.side or '-':5s} qty={float(position.quantity):8.4f} costo=${float(position.cost):6.2f} | "
            f"sesion realizada=${float(session_realized):+7.2f}",
            end="",
        )
        time.sleep(config.market.poll_interval_seconds)


if __name__ == "__main__":
    main()
