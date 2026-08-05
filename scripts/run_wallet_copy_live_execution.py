"""BTC 'Up or Down 5m' REAL-MONEY - direct copy of wallet
0x3048d65321be3497164cdfc2996f94f98a2e7537 (the real high-volume bot this
whole BTC5m vertical was originally modeled on, via Strategy E). Instead
of guessing direction/timing from our own price samples - the source of
every real bug hit building E and B (the batching-timing bug, the paper-
vs-real entry-price mismatch, the disproven "5 shares minimum" theory) -
this just watches that wallet's OWN real trades and mirrors the exact
asset (token_id) it buys, immediately. No window-tracking, no opening-
price capture, no sampling loop, no direction logic at all - the
wallet's trade tells us everything we need (asset, side, price, slug).

Runs on the FINLAND VPS ONLY (order placement geoblocked from Germany).
Deliberately its OWN cron entry/lockfile (run_btc5m_copy_cron.sh,
untracked, Finland-only), separate from run_btc5m_live_cron.sh (E+B) -
E's ~148s blocking sampling loop already once starved B of its own
sampling time by running first in the same cron invocation (fixed
2026-08-05); mixing this script's tight ~2s polling loop into that same
wrapper would risk the identical problem in reverse.

Polls data-api.polymarket.com/trades (same endpoint already used for
fill confirmation elsewhere in this repo) for up to ~55s per cron
invocation, every ~2s - near-continuous coverage without a persistent
daemon process, which nothing else in this repo uses either.

Own $50 capital pool (see atlantis.live.btc5m_config.load_btc5m_copy_live_settings)
and own trade log (outputs/live_trade_log_btc5m_copy.csv) - kept
separate from E/B's shared pool since this is a fresh, untested
mechanism, not because it needs different credentials (same real
account/funder as everything else).

Real-money safety carried over from this session's hard-won lessons:
- Never trust the raw order-post response for fill price/size - confirm
  via get_confirmed_fill() (queries /trades directly), same as E/B.
- First-ever run establishes a "seen trades" baseline WITHOUT copying
  anything - otherwise the very first invocation would try to copy the
  wallet's entire recent trade history from already-resolved windows.
- Dedicated kill switch, same mechanics as E/B (auto-pause if realized
  losses reach the full allocated capital).
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from atlantis.live.btc5m_config import load_btc5m_copy_live_settings  # noqa: E402
from atlantis.services.live_status import read_status_flag  # noqa: E402
from atlantis.services.live_execution import get_confirmed_fill, reconcile_resolved_positions  # noqa: E402
from run_btc5m_live_execution import check_kill_switch  # noqa: E402 - fully generic, parametrized by settings

TARGET_WALLET = "0x3048d65321be3497164cdfc2996f94f98a2e7537"
TRADES_API_URL = f"https://data-api.polymarket.com/trades?user={TARGET_WALLET}&limit=20"

POLL_INTERVAL_SECONDS = 2
# Cron fires every 60s with its own flock - stop with a buffer before the
# next tick so this invocation reliably exits before the next one tries
# to start, rather than relying on flock -n to just skip a late overlap.
POLL_DURATION_SECONDS = 55

SEEN_TRADES_PATH = ROOT / "state" / "btc5m_copy_seen_trades.json"
MAX_SEEN_TRADES = 500  # bounds the state file's size - only recent history matters for dedup

BTC5M_COPY_MAX_SLIPPAGE_PCT = Decimal("8")

COPY_LIVE_FIELDS = [
    "entry_key",
    "condition_id",
    "asset",
    "window_slug",
    "title",
    "outcome",
    "source_price",
    "source_size",
    "source_timestamp",
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


def load_log(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {row["entry_key"]: row for row in rows if row.get("entry_key")}


def save_log(path: Path, log: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COPY_LIVE_FIELDS)
        writer.writeheader()
        for row in log.values():
            writer.writerow({field: row.get(field, "") for field in COPY_LIVE_FIELDS})


def load_seen_trades() -> set[str]:
    if not SEEN_TRADES_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_TRADES_PATH.read_text()))
    except (ValueError, OSError):
        return set()


def save_seen_trades(seen: set[str]) -> None:
    SEEN_TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_TRADES_PATH.write_text(json.dumps(list(seen)[-MAX_SEEN_TRADES:]))


def fetch_wallet_trades() -> list[dict]:
    req = urllib.request.Request(
        TRADES_API_URL, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m-copy/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def copy_trade(settings, client, trade: dict, log: dict) -> None:
    tx_hash = str(trade.get("transactionHash", ""))
    entry_key = f"COPY_{tx_hash}"
    if entry_key in log:
        return  # transactionHash is unique per trade - never re-attempt one already logged

    from py_clob_client_v2.clob_types import OrderType

    token_id = str(trade.get("asset", ""))
    condition_id = str(trade.get("conditionId", ""))
    title = str(trade.get("title", ""))
    outcome = str(trade.get("outcome", ""))
    slug = str(trade.get("slug", ""))
    order_ts = int(datetime.now(timezone.utc).timestamp())

    base_row = {
        "entry_key": entry_key,
        "condition_id": condition_id,
        "asset": token_id,
        "window_slug": slug,
        "title": title,
        "outcome": outcome,
        "source_price": str(trade.get("price", "")),
        "source_size": str(trade.get("size", "")),
        "source_timestamp": str(trade.get("timestamp", "")),
        "stake_usd_requested": str(settings.stake_per_signal_usd),
        "date_opened": _now(),
        "last_updated": _now(),
    }

    try:
        result = client.place_market_buy(
            token_id,
            Decimal(str(settings.stake_per_signal_usd)),
            max_slippage_pct=BTC5M_COPY_MAX_SLIPPAGE_PCT,
            order_type=OrderType.FAK,
        )
    except Exception as exc:
        log[entry_key] = {
            **base_row,
            "fill_price_buy": "",
            "stake_usd_actual": "",
            "shares_held": "",
            "order_id_buy": "",
            "status": "ERROR",
            "fill_price_sell": "",
            "realized_pnl_usd": "",
            "pct_return": "",
            "date_closed": "",
        }
        save_log(settings.live_trade_log_path, log)
        print(f"btc5m-copy: EXCEPTION copiando {entry_key}: {exc}")
        return

    fill_price, fill_size = result.avg_fill_price, result.filled_size
    if result.success:
        confirmed = get_confirmed_fill(
            wallet_address=settings.funder_address,
            condition_id=condition_id,
            asset=token_id,
            side="BUY",
            since_ts=order_ts - 30,
        )
        if confirmed:
            fill_price, fill_size = confirmed

    status_value = "EXECUTED" if result.success else "ERROR"
    log[entry_key] = {
        **base_row,
        "fill_price_buy": str(fill_price) if fill_price else "",
        "stake_usd_actual": str(fill_price * fill_size) if (fill_price and fill_size) else "",
        "shares_held": str(fill_size) if fill_size else "",
        "order_id_buy": result.order_id or "",
        "status": status_value,
        "fill_price_sell": "",
        "realized_pnl_usd": "",
        "pct_return": "",
        "date_closed": "",
    }
    save_log(settings.live_trade_log_path, log)

    if not result.success:
        print(f"btc5m-copy: ORDEN FALLIDA {outcome} {entry_key}: {result.error}")
    elif fill_price is None or fill_size is None:
        print(f"btc5m-copy: REVISAR MANUALMENTE {entry_key} - exito pero sin fill confirmado. error={result.error}")
    else:
        print(f"btc5m-copy: EJECUTADO {outcome} {entry_key} @ {fill_price}, stake ${log[entry_key]['stake_usd_actual']}")


def main() -> None:
    settings = load_btc5m_copy_live_settings()

    log = load_log(settings.live_trade_log_path)
    resolved_count, _notifications = reconcile_resolved_positions(log)
    if resolved_count:
        save_log(settings.live_trade_log_path, log)
        print(f"btc5m-copy: reconciled {resolved_count} position(s)")

    check_kill_switch(settings)

    status = read_status_flag(settings)
    if not status.get("enabled"):
        print("btc5m-copy: trading real apagado - nada que hacer")
        return

    from atlantis.polymarket.clob_client import build_live_client

    client = build_live_client(settings)

    # established=False only for the very first poll of the very first
    # invocation this state file has ever seen - that pass marks whatever
    # trades are currently visible as "seen" WITHOUT copying them, so we
    # never try to mass-copy the wallet's entire recent history on
    # startup. Every poll after that (including later in this same
    # invocation) copies normally.
    established = SEEN_TRADES_PATH.exists()
    seen = load_seen_trades()

    deadline = time.time() + POLL_DURATION_SECONDS
    copied_this_run = 0
    while time.time() < deadline:
        trades = fetch_wallet_trades()
        candidates = [
            t
            for t in trades
            if t.get("transactionHash")
            and str(t["transactionHash"]) not in seen
            and str(t.get("side", "")).upper() == "BUY"
            and str(t.get("slug", "")).startswith("btc-updown-5m-")
        ]
        candidates.sort(key=lambda t: t.get("timestamp", 0))
        for t in candidates:
            seen.add(str(t["transactionHash"]))
            if established:
                copy_trade(settings, client, t, log)
                copied_this_run += 1
        if candidates:
            save_seen_trades(seen)
        established = True
        time.sleep(POLL_INTERVAL_SECONDS)

    if copied_this_run:
        print(f"btc5m-copy: {copied_this_run} trade(s) copiado(s) este ciclo")


if __name__ == "__main__":
    main()
