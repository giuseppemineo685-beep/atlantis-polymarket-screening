"""BTC 'Up or Down 5m' cross-side hedge bot - LIVE PAPER TRADING (first
version, explicitly no real money per 2026-08-08 instruction). Ties
together atlantis/btc5m_hedge/{market_data,portfolio,optimizer,risk,
execution,logger}.py against the real public order book, on the
currently active window, and prints a live status line + a signal
(BUY UP/DOWN tagged with its hedge mode, WAIT, or LOCKED PROFIT) every
poll.

Strategy recap (reverse-engineered from wallet 0x3048...e7537's real
trades 2026-08-08): don't predict BTC's direction at all - build a
combined Up+Down position whose guaranteed payout (min(up_shares,
down_shares) at $1/share) exceeds total cost, regardless of which side
resolves.

Three-mode hedge state machine (optimizer.evaluate_hedge, added
2026-08-08): MODE A (PROFIT_HEDGE) always tried first - lock in a
guaranteed profit if the numbers allow it right now. If nothing in MODE A
qualifies, MODE B (DEFENSIVE_HEDGE, roughly the last 90-120s of a window)
looks for a partial top-up of the lagging side that meaningfully shrinks
the worst-case loss even without creating outright profit. Inside the
last ~30s, MODE C (EMERGENCY_HEDGE) drops the "worth it" bar entirely and
just buys whatever minimizes the worst remaining outcome. See
atlantis/btc5m_hedge/optimizer.py for the exact thresholds/scoring.

No credentials, no client, no real orders - same zero-risk shadow
philosophy as run_wallet_copy_shadow_analysis.py and
run_btc5m_arb_shadow.py, just with an actual (paper) portfolio and
decision engine behind it instead of pure observation.
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

from atlantis.btc5m_hedge.config import HedgeTimingConfig, load_config  # noqa: E402
from atlantis.btc5m_hedge.execution import execute_paper  # noqa: E402
from atlantis.btc5m_hedge.logger import WindowStats, backfill_missing_outcomes, log_decision, log_window_summary  # noqa: E402
from atlantis.btc5m_hedge.market_data import (  # noqa: E402
    MarketInfo,
    current_window_start,
    fetch_market_by_slug,
    fetch_order_book_asks,
    fetch_resolved_outcome,
    slug_for_window,
)
from atlantis.btc5m_hedge.optimizer import (  # noqa: E402
    DEFENSIVE_HEDGE,
    EMERGENCY_HEDGE,
    PROFIT_HEDGE,
    detect_price_runaway,
    evaluate_hedge,
)
from atlantis.btc5m_hedge.portfolio import OrderBookLevel, Portfolio  # noqa: E402
from atlantis.btc5m_hedge.risk import meets_safety_margin  # noqa: E402

HEDGE_MODE_LABEL = {
    PROFIT_HEDGE: "PROFIT",
    DEFENSIVE_HEDGE: "DEFENSIVE",
    EMERGENCY_HEDGE: "EMERGENCY",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


def print_status(slug: str, portfolio: Portfolio, seconds_remaining: float, signal: str, reason: str) -> None:
    roi = portfolio.get_guaranteed_roi()
    roi_str = f"{roi:.2f}%" if roi is not None else "n/a"
    print(
        f"\r{slug} | {seconds_remaining:6.1f}s left | "
        f"UP {portfolio.up_shares:8.2f}sh (${portfolio.up_cost:7.2f}) | "
        f"DOWN {portfolio.down_shares:8.2f}sh (${portfolio.down_cost:7.2f}) | "
        f"invested ${portfolio.total_cost:7.2f} | "
        f"P/L up=${portfolio.get_profit_if_up():7.2f} down=${portfolio.get_profit_if_down():7.2f} | "
        f"GUARANTEED=${portfolio.get_guaranteed_profit():7.2f} ({roi_str}) | {signal:12s}",
        end="",
    )
    if signal != "WAIT":
        print()  # keep a permanent line for anything actionable, overwrite pure WAIT ticks
        _log(reason)


def lagging_side_runaway(
    portfolio: Portfolio,
    up_levels: list[OrderBookLevel],
    down_levels: list[OrderBookLevel],
    up_history: deque[Decimal],
    down_history: deque[Decimal],
    hedge_timing_cfg: HedgeTimingConfig,
) -> bool:
    """Appends this poll's best ask (if any liquidity) to the appropriate
    side's rolling history, then checks ONLY the currently-lagging side
    (the one a completing trade would actually need to buy) for a
    detect_price_runaway trend - a tied or empty portfolio has no lagging
    side yet, so there's nothing to check."""
    if up_levels:
        up_history.append(up_levels[0].price)
    if down_levels:
        down_history.append(down_levels[0].price)
    if portfolio.up_shares == portfolio.down_shares:
        return False
    lagging_history = up_history if portfolio.up_shares < portfolio.down_shares else down_history
    return detect_price_runaway(
        list(lagging_history),
        min_samples=hedge_timing_cfg.price_runaway_window_samples,
        min_net_move=hedge_timing_cfg.price_runaway_min_move,
        max_pullback=hedge_timing_cfg.price_runaway_max_pullback,
    )


def main() -> None:
    config = load_config()
    _log("btc5m-hedge-paper: arrancando (paper trading, sin dinero real)")

    current_slug: str | None = None
    market: MarketInfo | None = None
    portfolio = Portfolio()
    stats = WindowStats()
    up_price_history: deque[Decimal] = deque(maxlen=config.hedge_timing.price_runaway_window_samples)
    down_price_history: deque[Decimal] = deque(maxlen=config.hedge_timing.price_runaway_window_samples)

    while True:
        now = datetime.now(timezone.utc)
        window_start = current_window_start(now, config.market.window_seconds)
        slug = slug_for_window(window_start)
        close_at = window_start + timedelta(seconds=config.market.window_seconds)
        seconds_remaining = (close_at - now).total_seconds()

        if slug != current_slug:
            # Close out and reset for the new slug IMMEDIATELY, before
            # attempting the market fetch below - not after it succeeds.
            # Bug found live 2026-08-08: during a connectivity outage,
            # fetch_market_by_slug kept failing and this whole block used
            # to `continue` without ever updating current_slug, so on
            # EVERY poll (once a second) `slug != current_slug` was still
            # true and the close-out/backfill logic re-ran again - one
            # real window ended up logged ~300 times in
            # btc5m_hedge_paper_window_summary.csv. Closing out exactly
            # once per real slug transition, regardless of whether the
            # fetch that follows succeeds, is what actually fixes it.
            closing_slug = current_slug
            closing_portfolio = portfolio
            closing_stats = stats
            current_slug = slug
            market = None
            portfolio = Portfolio()
            stats = WindowStats()
            up_price_history.clear()
            down_price_history.clear()

            if closing_slug is not None:
                print()
                outcome = fetch_resolved_outcome(closing_slug)
                log_window_summary(
                    config.paper.window_summary_log_path,
                    window_slug=closing_slug,
                    portfolio=closing_portfolio,
                    stats=closing_stats,
                    realized_outcome=outcome or "",
                    window_closed_at=_now(),
                )
                _log(
                    f"btc5m-hedge-paper: {closing_slug} cerrada - guaranteed_profit=${closing_portfolio.get_guaranteed_profit():.2f}, "
                    f"outcome={outcome or 'desconocido aun'}"
                )

                # A window that JUST closed almost never has a resolution
                # yet (oracle lag) - the check above is best-effort, not
                # the real fix. Every new-window transition (~every 5
                # min) also retries every OLDER window still marked
                # unresolved, so gaps self-heal within a few cycles
                # instead of staying "?" forever.
                updated = backfill_missing_outcomes(config.paper.window_summary_log_path, fetch_resolved_outcome)
                if updated:
                    _log(f"btc5m-hedge-paper: backfill resolvio {updated} ventana(s) que estaban pendientes")

        if market is None:
            fetched = fetch_market_by_slug(slug)
            if fetched is None:
                _log(f"btc5m-hedge-paper: no se pudo obtener el mercado para {slug}, reintentando")
                time.sleep(config.market.poll_interval_seconds)
                continue
            market = fetched
            _log(f"btc5m-hedge-paper: siguiendo nueva ventana {slug}")

        up_levels = fetch_order_book_asks(market.up_token_id)
        down_levels = fetch_order_book_asks(market.down_token_id)

        price_runaway = lagging_side_runaway(
            portfolio, up_levels, down_levels, up_price_history, down_price_history, config.hedge_timing
        )

        hedge_decision = evaluate_hedge(
            portfolio=portfolio,
            up_levels=up_levels,
            down_levels=down_levels,
            seconds_remaining=seconds_remaining,
            optimizer_cfg=config.optimizer,
            risk_cfg=config.risk,
            hedge_timing_cfg=config.hedge_timing,
            price_runaway=price_runaway,
        )

        before = portfolio
        if hedge_decision.candidate is not None:
            execute_paper(hedge_decision.candidate)
            portfolio = hedge_decision.candidate.new_portfolio
            stats.record(portfolio, hedge_mode=hedge_decision.hedge_mode, loss_reduction=hedge_decision.loss_reduction)
            action = "BUY"
            signal = f"BUY {hedge_decision.candidate.side} ({HEDGE_MODE_LABEL[hedge_decision.hedge_mode]})"
        elif meets_safety_margin(portfolio, config.risk) or portfolio.is_locked_profit():
            action = "LOCKED_PROFIT"
            signal = "LOCKED PROFIT"
        else:
            action = "WAIT"
            signal = "WAIT"

        # Logs EVERY poll, WAIT included, same as backtest.py - the spec
        # asks for a CSV of "todas las decisiones", and a WAIT is still a
        # decision (with before==after) worth having in the trail when
        # diagnosing why the bot didn't act at a given moment.
        log_decision(
            config.paper.decision_log_path,
            timestamp=_now(),
            window_slug=slug,
            action=action,
            before=before,
            after=portfolio,
            hedge_decision=hedge_decision,
        )

        print_status(slug, portfolio, seconds_remaining, signal, hedge_decision.reason)
        time.sleep(config.market.poll_interval_seconds)


if __name__ == "__main__":
    main()
