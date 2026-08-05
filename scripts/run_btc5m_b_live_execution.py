"""BTC 'Up or Down 5m' REAL-MONEY pilot - Strategy B (momentum) ONLY.

Shares the EXACT same $100 capital pool, kill switch, and real trade log
as Strategy E (atlantis.live.btc5m_config.load_btc5m_live_settings()) -
owner's explicit choice, 2026-08-04: one combined pool for both real
BTC5m strategies rather than separate allocations. Entries use a
distinct "B_{slug}" key (single order per window, no scaling), so they
never collide with E's "{slug}#{i}" keys in the shared
outputs/live_trade_log_btc5m.csv.

MUST run strictly AFTER, never concurrently with,
run_btc5m_live_execution.py, in the SAME cron invocation/flock - both
read-modify-save the same shared CSV log file, and true concurrent
access would lose updates. Wired into run_btc5m_live_cron.sh on the
Finland VPS (untracked, edited via SSH) right after E's script call, so
they always run sequentially under the same lock.

Reuses generic helpers from run_btc5m_live_execution.py (load_log,
save_log, check_kill_switch, title_for_slug, _now,
BTC5M_MAX_SLIPPAGE_PCT) instead of duplicating them - only the window-
tracking state and the strategy-specific entry logic differ from E.

Strategy B fires AT MOST ONCE per window (30-40s before close, momentum
of the ~150s of spot movement leading up to that point - paper track
record 2026-08-04: 124 trades, 73.4% win rate, +46% avg return) - unlike
E, there is no scaling/multiple-entries complexity, and its entry prices
cluster near 0.50 (median 0.505) rather than E's near-certain 0.90+, so
the order book is far more likely to have real two-sided liquidity at
the moment it buys.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from atlantis.live.btc5m_config import load_btc5m_live_settings  # noqa: E402
from atlantis.services.live_status import read_status_flag  # noqa: E402
from atlantis.services.live_execution import get_confirmed_fill  # noqa: E402
from run_btc5m_paper_trading import (  # noqa: E402
    LOOKAHEAD_SECONDS,
    SAMPLE_INTERVAL_SECONDS,
    STALE_START_SECONDS,
    WINDOW_SECONDS,
    MarketInfo,
    Sample,
    current_window_start,
    fetch_clob_quotes,
    fetch_market_by_slug,
    fetch_spot_price,
    slug_for_window,
    strategy_b_momentum,
)
from run_btc5m_live_execution import (  # noqa: E402
    BTC5M_MAX_SLIPPAGE_PCT,
    _now,
    load_log,
    save_log,
    title_for_slug,
)

B_WINDOW_STATE_PATH = ROOT / "state" / "btc5m_b_live_window.json"


def load_window_state() -> dict | None:
    if not B_WINDOW_STATE_PATH.exists():
        return None
    try:
        import json

        return json.loads(B_WINDOW_STATE_PATH.read_text())
    except (ValueError, OSError):
        return None


def save_window_state(state: dict) -> None:
    import json

    B_WINDOW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    B_WINDOW_STATE_PATH.write_text(json.dumps(state))


def main() -> None:
    settings = load_btc5m_live_settings()
    now = datetime.now(timezone.utc)
    window_start = current_window_start(now)
    slug = slug_for_window(window_start)
    seconds_since_start = (now - window_start).total_seconds()

    state = load_window_state()
    if state is None or state.get("slug") != slug:
        market = fetch_market_by_slug(slug)
        if market is None:
            print(f"btc5m-b-live: could not fetch market for {slug}, skipping")
            return
        target_price = fetch_spot_price() if seconds_since_start <= STALE_START_SECONDS else None
        state = {
            "slug": slug,
            "condition_id": market.condition_id,
            "up_token_id": market.up_token_id,
            "down_token_id": market.down_token_id,
            "target_price": str(target_price) if target_price is not None else "",
        }
        save_window_state(state)
        print(f"btc5m-b-live: tracking new window {slug}")

    # No reconciliation/kill-switch call here - run_btc5m_live_execution.py
    # already did both this same cron cycle, against the SAME shared
    # status/log files (it runs first, see module docstring).

    close_at = window_start + timedelta(seconds=WINDOW_SECONDS)
    seconds_left = (close_at - now).total_seconds()

    if seconds_left > LOOKAHEAD_SECONDS:
        return  # cheap no-op - most cron ticks land here

    target_price = Decimal(state["target_price"]) if state.get("target_price") else None
    market = MarketInfo(
        condition_id=state["condition_id"],
        slug=slug,
        up_token_id=state["up_token_id"],
        down_token_id=state["down_token_id"],
    )

    status = read_status_flag(settings)
    log = load_log(settings.live_trade_log_path)
    entry_key = f"B_{slug}"
    title = title_for_slug(slug)

    client = None
    if status.get("enabled"):
        from atlantis.polymarket.clob_client import build_live_client
        from py_clob_client_v2.clob_types import OrderType

        client = build_live_client(settings)

    def place_entry(direction: str, spot_price: Decimal | None) -> None:
        if entry_key in log and log[entry_key].get("status") != "ERROR":
            return  # already placed successfully - retry-safety, mirrors E's invariant

        # See run_btc5m_live_execution.py's place_entry for why this is
        # logged: our target_price is a Coinbase proxy for Polymarket's
        # real resolution feed, and a real window on 2026-08-04 showed
        # direct evidence they can diverge for an entire window.
        spot_str = str(spot_price) if spot_price is not None else ""
        target_str = str(target_price) if target_price is not None else ""

        token_id = market.up_token_id if direction == "UP" else market.down_token_id
        order_ts = int(datetime.now(timezone.utc).timestamp())
        try:
            result = client.place_market_buy(
                token_id,
                Decimal(str(settings.stake_per_signal_usd)),
                max_slippage_pct=BTC5M_MAX_SLIPPAGE_PCT,
                order_type=OrderType.FAK,
            )
        except Exception as exc:
            log[entry_key] = {
                "entry_key": entry_key,
                "condition_id": market.condition_id,
                "asset": token_id,
                "window_slug": slug,
                "title": title,
                "outcome": direction,
                "direction": direction,
                "fill_price_buy": "",
                "stake_usd_requested": str(settings.stake_per_signal_usd),
                "stake_usd_actual": "",
                "shares_held": "",
                "order_id_buy": "",
                "status": "ERROR",
                "fill_price_sell": "",
                "realized_pnl_usd": "",
                "pct_return": "",
                "spot_price_at_entry": spot_str,
                "target_price": target_str,
                "date_opened": _now(),
                "date_closed": "",
                "last_updated": _now(),
            }
            save_log(settings.live_trade_log_path, log)
            print(f"btc5m-b-live: EXCEPTION placing BUY {entry_key}: {exc}")
            return

        fill_price, fill_size = result.avg_fill_price, result.filled_size
        if result.success:
            confirmed = get_confirmed_fill(
                wallet_address=settings.funder_address,
                condition_id=market.condition_id,
                asset=token_id,
                side="BUY",
                since_ts=order_ts - 30,
            )
            if confirmed:
                fill_price, fill_size = confirmed

        status_value = "EXECUTED" if result.success else "ERROR"
        log[entry_key] = {
            "entry_key": entry_key,
            "condition_id": market.condition_id,
            "asset": token_id,
            "window_slug": slug,
            "title": title,
            "outcome": direction,
            "direction": direction,
            "fill_price_buy": str(fill_price) if fill_price else "",
            "stake_usd_requested": str(settings.stake_per_signal_usd),
            "stake_usd_actual": str(fill_price * fill_size) if (fill_price and fill_size) else "",
            "shares_held": str(fill_size) if fill_size else "",
            "order_id_buy": result.order_id or "",
            "status": status_value,
            "fill_price_sell": "",
            "realized_pnl_usd": "",
            "pct_return": "",
            "spot_price_at_entry": spot_str,
            "target_price": target_str,
            "date_opened": _now(),
            "date_closed": "",
            "last_updated": _now(),
        }
        save_log(settings.live_trade_log_path, log)

        if not result.success:
            print(f"btc5m-b-live: ORDEN FALLIDA {direction} {entry_key}: {result.error}")
        elif fill_price is None or fill_size is None:
            print(
                f"btc5m-b-live: REVISAR MANUALMENTE {entry_key} - exito pero sin fill "
                f"confirmado. error={result.error} raw={result.raw_response}"
            )
        else:
            print(
                f"btc5m-b-live: EJECUTADO {direction} {entry_key} @ {fill_price}, "
                f"stake ${log[entry_key]['stake_usd_actual']}"
            )

    already_done = entry_key in log and log[entry_key].get("status") != "ERROR"
    samples: list[Sample] = []
    reported = already_done
    while True:
        seconds_left = (close_at - datetime.now(timezone.utc)).total_seconds()
        spot = fetch_spot_price()
        up_q, down_q = fetch_clob_quotes(market.condition_id, market.up_token_id, market.down_token_id)
        samples.append(
            Sample(
                seconds_to_close=seconds_left,
                spot_price=spot,
                target_price=target_price,
                up_quote=up_q,
                down_quote=down_q,
            )
        )

        if not reported:
            entries = strategy_b_momentum(samples)
            if entries:
                direction, _paper_entry_price, _paper_stake = entries[0]
                if client is not None:
                    place_entry(direction, samples[-1].spot_price)
                else:
                    print(
                        f"btc5m-b-live: señal de B en {slug}, pero trading real "
                        f"esta apagado - nada ejecutado"
                    )
                reported = True

        if seconds_left <= 2:
            break
        time.sleep(SAMPLE_INTERVAL_SECONDS)

    if not reported:
        # Owner asked (2026-08-05, after 3 windows in a row with no
        # signal - unusually low given B fires in ~97% of paper windows)
        # for a diagnostic that tells apart the real possibilities:
        # (a) sampling jitter skipped clean over the narrow 30-40s band
        #     strategy_b_momentum requires (a slow network call inside
        #     one loop iteration - fetch_spot_price/fetch_clob_quotes -
        #     can make a single step cover >10s, jumping past it
        #     entirely);
        # (b) fetch_spot_price() failed on most samples (Coinbase flaky);
        # (c) BTC genuinely didn't move net over the ~150s lookback -
        #     the fully legitimate "no signal" case.
        # Without this, all three looked identical: a silent "no disparo".
        candidates = [s for s in samples if 30 <= s.seconds_to_close <= 40]
        priced = [s for s in samples if s.spot_price is not None]
        if not candidates:
            print(
                f"btc5m-b-live: Strategy B no disparo para {slug} - NINGUNA de las "
                f"{len(samples)} muestras cayo en la ventana 30-40s antes del cierre "
                f"(posible salto de muestreo por lentitud de red)"
            )
        elif len(priced) < 2:
            print(
                f"btc5m-b-live: Strategy B no disparo para {slug} - precio de BTC "
                f"disponible en solo {len(priced)}/{len(samples)} muestras (fallo de "
                f"Coinbase)"
            )
        else:
            earliest, latest = priced[0], priced[-1]
            print(
                f"btc5m-b-live: Strategy B no disparo para {slug} - BTC sin cambio "
                f"neto: {earliest.spot_price} -> {latest.spot_price}"
            )


if __name__ == "__main__":
    main()
