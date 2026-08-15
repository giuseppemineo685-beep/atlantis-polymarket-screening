"""BTC 'Up or Down 5m' MODERATE UNDERDOG bot - LIVE PAPER TRADING.
Buys whichever side is currently cheaper in the last ~2.5 minutes of the
window, but only within a moderate price band - see
atlantis/btc5m_longshot/signal.py for the 2026-08-15 real-data
derivation (extreme longshots below $0.05 are a confirmed LOSING bet;
$0.05-$0.25 has a real, sizeable edge).

Unlike run_btc5m_momentum_paper_trading.py (one decision at window
open), this polls continuously through the entry window and fires on
the FIRST qualifying observation, since the real edge was found by
sampling many points across the last ~150s, not a single instant.

No credentials, no client, no real orders - paper only.
"""

from __future__ import annotations

import sys
import time
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
from atlantis.btc5m_longshot.config import load_config  # noqa: E402
from atlantis.btc5m_longshot.logger import (  # noqa: E402
    backfill_missing_outcomes,
    compute_session_realized,
    log_decision,
    log_window_summary,
)
from atlantis.btc5m_longshot.position import Position  # noqa: E402
from atlantis.btc5m_longshot.signal import UP, decide  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


def try_enter(
    *,
    market: MarketInfo,
    seconds_remaining: float,
    config,
    session_realized: Decimal,
):
    """One poll's worth of the entry check. Returns (position, decision,
    underdog_price) - position stays empty (Position()) whenever the
    real book doesn't confirm a fill, even if the quoted price alone
    would have qualified."""
    if session_realized <= -config.risk.max_session_loss_usd:
        from atlantis.btc5m_longshot.signal import Decision
        return Position(), Decision(
            False, None,
            f"circuit breaker: perdida de sesion ${-session_realized:.2f} alcanzo el limite (${config.risk.max_session_loss_usd})",
        ), None

    up_levels = fetch_order_book_asks(market.up_token_id)
    down_levels = fetch_order_book_asks(market.down_token_id)
    up_price = up_levels[0].price if up_levels else None
    down_price = down_levels[0].price if down_levels else None

    decision = decide(
        up_price=up_price, down_price=down_price, seconds_remaining=seconds_remaining,
        entry_window_seconds=config.signal.entry_window_seconds,
        min_underdog_price=config.signal.min_underdog_price,
        max_underdog_price=config.signal.max_underdog_price,
    )
    underdog_price = up_price if (up_price is not None and down_price is not None and up_price <= down_price) else down_price
    if not decision.should_bet:
        return Position(), decision, underdog_price

    levels = up_levels if decision.side == UP else down_levels
    requested_quantity = config.risk.bet_size_usd / underdog_price
    fill = fill_from_levels(requested_quantity, levels)
    if fill.filled_quantity <= 0:
        from atlantis.btc5m_longshot.signal import Decision
        return Position(), Decision(False, None, "libro se vacio entre la lectura y la ejecucion"), underdog_price

    position = Position(side=decision.side, quantity=fill.filled_quantity, cost=fill.total_cost)
    return position, decision, underdog_price


def main() -> None:
    config = load_config()
    _log("btc5m-longshot-paper: arrancando (paper trading, sin dinero real)")

    current_slug: str | None = None
    market: MarketInfo | None = None
    position = Position()
    underdog_price: Decimal | None = None
    bet_this_window = False
    session_realized = Decimal(0)

    while True:
        now = datetime.now(timezone.utc)
        window_start = current_window_start(now, config.market.window_seconds)
        slug = slug_for_window(window_start)
        close_at = window_start + timedelta(seconds=config.market.window_seconds)
        seconds_remaining = (close_at - now).total_seconds()

        if slug != current_slug:
            closing_slug = current_slug
            closing_position = position
            closing_underdog_price = underdog_price
            current_slug = slug
            market = None
            position = Position()
            underdog_price = None
            bet_this_window = False

            if closing_slug is not None:
                print()
                outcome = fetch_resolved_outcome(closing_slug)
                log_window_summary(
                    config.paper.window_summary_log_path,
                    window_slug=closing_slug,
                    position=closing_position,
                    underdog_price=closing_underdog_price,
                    realized_outcome=outcome or "",
                    window_closed_at=_now(),
                )
                if outcome:
                    realized = closing_position.realized_profit(outcome)
                    _log(
                        f"btc5m-longshot-paper: {closing_slug} cerrada - side={closing_position.side or '(sin apuesta)'}, "
                        f"outcome={outcome}, realized=${realized:.2f}"
                    )
                else:
                    _log(f"btc5m-longshot-paper: {closing_slug} cerrada - outcome desconocido aun")

                updated = backfill_missing_outcomes(config.paper.window_summary_log_path, fetch_resolved_outcome)
                if updated:
                    _log(f"btc5m-longshot-paper: backfill resolvio {updated} ventana(s) que estaban pendientes")

                session_realized = compute_session_realized(config.paper.window_summary_log_path)
                _log(f"btc5m-longshot-paper: sesion acumulada real=${session_realized:.2f}")

        if market is None:
            fetched = fetch_market_by_slug(slug)
            if fetched is None:
                _log(f"btc5m-longshot-paper: no se pudo obtener el mercado para {slug}, reintentando")
                time.sleep(config.market.poll_interval_seconds)
                continue
            market = fetched
            _log(f"btc5m-longshot-paper: siguiendo nueva ventana {slug}")

        if not bet_this_window and seconds_remaining <= config.signal.entry_window_seconds:
            position, decision, underdog_price = try_enter(
                market=market, seconds_remaining=seconds_remaining, config=config, session_realized=session_realized,
            )
            if decision.should_bet or "circuit breaker" in decision.reason:
                bet_this_window = True
                log_decision(
                    config.paper.decision_log_path,
                    timestamp=_now(), window_slug=slug, decision=decision,
                    underdog_price=underdog_price, position=position,
                )
                if decision.should_bet:
                    _log(f"btc5m-longshot-paper: BET {position.side} x{position.quantity:.4f} (${position.cost:.2f}) - {decision.reason}")
                else:
                    _log(f"btc5m-longshot-paper: NO_BET - {decision.reason}")

        print(
            f"\r{slug} | {seconds_remaining:6.1f}s left | "
            f"posicion={position.side or '-':5s} qty={float(position.quantity):8.4f} costo=${float(position.cost):6.2f} | "
            f"sesion realizada=${float(session_realized):+7.2f}",
            end="",
        )
        time.sleep(config.market.poll_interval_seconds)


if __name__ == "__main__":
    main()
