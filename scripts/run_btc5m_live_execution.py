"""BTC 'Up or Down 5m' REAL-MONEY pilot - Strategy E ONLY. $100 total
capital, $1 per real entry (owner's explicit instructions, 2026-08-04).

Runs on the FINLAND VPS ONLY - order placement is geoblocked from Germany,
same reason the sports vertical splits screening (Germany) from execution
(Finland). This script imports the PURE discovery/sampling/strategy
functions from scripts/run_btc5m_paper_trading.py (same directory, no
side effects in those functions - confirmed before reuse) but keeps its
own, completely separate state:

- Own kill-switch/enabled file: state/live_trading_status_btc5m.json
  (via atlantis.live.btc5m_config.load_btc5m_live_settings() - NOT
  atlantis.live.config.load_live_settings(), which is sports' own,
  non-namespaced settings).
- Own window-tracking state: state/btc5m_live_window.json - deliberately
  NOT the same file the Germany paper script writes
  (state/btc5m_window.json, which is git-synced every minute by that
  script's own cron) - two machines writing the same git-tracked file on
  independent clocks would race and corrupt each other's view of "the
  current window's opening price".
- Own real trade log: outputs/live_trade_log_btc5m.csv.

Zero import of atlantis.services.live_intents or anything from the sports
BUY/SELL intent queue - this vertical has no early-exit/SELL concept at
all (a 5-minute market just resolves on its own), so there is nothing to
enqueue and nothing that could ever cross-contaminate the sports queue.

Real-money safety carried over from this session's hard-won lessons:
- Never trust the raw order-post response for fill price/size - confirm
  via get_confirmed_fill() (queries /trades directly) same as sports.
- A parse/confirm failure on a successful order must never be retried
  (status stays EXECUTED, not ERROR) - only a genuinely failed order
  (result.success == False) is eligible for retry on the next cycle.
- Dedicated kill switch: if realized losses reach the full $100 allocated,
  auto-pause (write enabled: false) until manually re-enabled. This is
  independent of, and does not touch, sports' own kill switch (which has
  auto-trip disabled by a separate, earlier decision).
"""

from __future__ import annotations

import csv
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
from atlantis.services.live_status import read_status_flag, write_status_flag, compute_live_status  # noqa: E402
from atlantis.services.live_execution import (  # noqa: E402
    get_confirmed_fill,
    reconcile_resolved_positions,
)
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
    strategy_e_scaling_replicator,
)

LIVE_WINDOW_STATE_PATH = ROOT / "state" / "btc5m_live_window.json"

# Wider than the shared 3% default in clob_client.py (sports keeps using
# that default, unaffected) - every real BTC5m attempt so far has failed
# with FOK "couldn't be fully filled" (thin order-book liquidity at the
# instant of the attempt), not a price/format issue. A wider tolerance
# lets the BUY limit price reach further into the book to find a match,
# at the cost of a worse average fill price when it works.
BTC5M_MAX_SLIPPAGE_PCT = Decimal("8")

BTC5M_LIVE_FIELDS = [
    "entry_key",
    "condition_id",
    "asset",
    "window_slug",
    "title",
    "outcome",
    "direction",
    "fill_price_buy",
    "stake_usd_requested",
    "stake_usd_actual",
    "shares_held",
    "order_id_buy",
    "status",
    "fill_price_sell",
    "realized_pnl_usd",
    "pct_return",
    "date_opened",
    "date_closed",
    "last_updated",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def title_for_slug(slug: str) -> str:
    try:
        ts = int(slug.rsplit("-", 1)[-1])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return f"Bitcoin Up or Down 5m — {dt.strftime('%Y-%m-%d %H:%M')} UTC"
    except (ValueError, IndexError):
        return slug


def load_window_state() -> dict | None:
    if not LIVE_WINDOW_STATE_PATH.exists():
        return None
    try:
        import json

        return json.loads(LIVE_WINDOW_STATE_PATH.read_text())
    except (ValueError, OSError):
        return None


def save_window_state(state: dict) -> None:
    import json

    LIVE_WINDOW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIVE_WINDOW_STATE_PATH.write_text(json.dumps(state))


def load_log(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {row["entry_key"]: row for row in rows if row.get("entry_key")}


def save_log(path: Path, log: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=BTC5M_LIVE_FIELDS)
        writer.writeheader()
        for row in log.values():
            writer.writerow({field: row.get(field, "") for field in BTC5M_LIVE_FIELDS})


def check_kill_switch(settings) -> None:
    """Pilot-specific auto-pause: if realized losses reach the full
    allocated capital, disable and stay disabled until a human re-enables
    it. Reuses compute_live_status (fully generic) rather than a custom
    PnL sum. Conservative by construction: WON_UNREDEEMED rows have a
    blank realized_pnl_usd until manually redeemed (same known limitation
    documented for sports), so this can trip a little early if wins sit
    unredeemed - never late, which is the safe direction to be wrong in."""
    status = read_status_flag(settings)
    if not status.get("enabled") or status.get("auto_killed"):
        return
    summary = compute_live_status(settings)
    if summary.realized_pnl_since_reset_usd <= -Decimal(str(settings.initial_bankroll_usd)):
        write_status_flag(
            settings,
            enabled=False,
            auto_killed=True,
            reason=(
                f"Kill switch BTC5m pilot: perdida realizada "
                f"${-summary.realized_pnl_since_reset_usd:.2f} alcanzo el capital "
                f"asignado (${settings.initial_bankroll_usd:.2f})"
            ),
            since=_now(),
        )
        print("btc5m-live: KILL SWITCH disparado, trading real pausado")


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
            print(f"btc5m-live: could not fetch market for {slug}, skipping")
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
        print(f"btc5m-live: tracking new window {slug}")

    # Reconciliation and the kill-switch check run every cycle regardless
    # of how close we are to a window close, so a resolved position gets
    # flagged and any loss gets counted as promptly as possible.
    log = load_log(settings.live_trade_log_path)
    resolved_count, _notifications = reconcile_resolved_positions(log)
    if resolved_count:
        save_log(settings.live_trade_log_path, log)
        print(f"btc5m-live: reconciled {resolved_count} position(s)")

    check_kill_switch(settings)

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
    log = load_log(settings.live_trade_log_path)  # reload - reconciliation above may have changed it
    title = title_for_slug(slug)

    client = None
    if status.get("enabled"):
        from atlantis.polymarket.clob_client import build_live_client

        client = build_live_client(settings)

    from py_clob_client_v2.clob_types import OrderType

    def place_entry(i: int, direction: str) -> None:
        entry_key = f"{slug}#{i}"
        if entry_key in log and log[entry_key].get("status") != "ERROR":
            return  # already placed - retry-safety, mirrors the sports invariant

        token_id = market.up_token_id if direction == "UP" else market.down_token_id
        order_ts = int(datetime.now(timezone.utc).timestamp())
        try:
            # FAK, not the default FOK - every FOK attempt failed with
            # "couldn't be fully filled" (thin order books at the moment of
            # the attempt); FAK takes whatever fill is available and
            # cancels the rest.
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
                "date_opened": _now(),
                "date_closed": "",
                "last_updated": _now(),
            }
            save_log(settings.live_trade_log_path, log)
            print(f"btc5m-live: EXCEPTION placing BUY {entry_key}: {exc}")
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
            "date_opened": _now(),
            "date_closed": "",
            "last_updated": _now(),
        }
        save_log(settings.live_trade_log_path, log)

        if not result.success:
            print(f"btc5m-live: ORDEN FALLIDA {direction} {entry_key}: {result.error}")
        elif fill_price is None or fill_size is None:
            print(
                f"btc5m-live: REVISAR MANUALMENTE {entry_key} - exito pero sin fill "
                f"confirmado. error={result.error} raw={result.raw_response}"
            )
        else:
            print(
                f"btc5m-live: EJECUTADO {direction} {entry_key} @ {fill_price}, "
                f"stake ${log[entry_key]['stake_usd_actual']}"
            )

    samples: list[Sample] = []
    entries_seen = 0
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

        # Evaluated after EVERY sample, not once after this loop ends. The
        # old structure called strategy_e_scaling_replicator(samples) only
        # AFTER the loop broke (seconds_left <= 2) and then placed every
        # entry back-to-back at that instant - meaning even the entry
        # "computed for 150s before close" actually reached the exchange
        # right as (or after) the window itself closed, guaranteeing a
        # dead/frozen book. Paper trading never noticed this bug because
        # it only replays each sample's already-recorded quote after the
        # fact - it never needs to transact at that exact instant. Root-
        # caused 2026-08-04 after two isolated $1/$6 test orders proved the
        # order pipeline itself works fine (the cheap-side one filled
        # instantly); the batching bug is what put every real Strategy E
        # attempt at the worst possible moment regardless of order type.
        entries = strategy_e_scaling_replicator(samples)
        if client is not None:
            for i in range(entries_seen, len(entries)):
                direction, _paper_entry_price, _paper_stake = entries[i]
                place_entry(i, direction)
        elif len(entries) > entries_seen:
            print(
                f"btc5m-live: {len(entries) - entries_seen} señal(es) nueva(s) de E en "
                f"{slug}, pero trading real esta apagado - nada ejecutado"
            )
        entries_seen = len(entries)

        if seconds_left <= 2:
            break
        time.sleep(SAMPLE_INTERVAL_SECONDS)

    if entries_seen == 0:
        print(f"btc5m-live: Strategy E no disparo para {slug}")


if __name__ == "__main__":
    main()
