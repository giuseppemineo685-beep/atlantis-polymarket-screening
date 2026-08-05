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

Runs on the FINLAND VPS ONLY (order placement geoblocked from Germany),
as a PERSISTENT process under systemd (atlantis-btc5m-copy.service,
Restart=always) - NOT cron. Owner asked (2026-08-05) to cut copy latency
as much as possible; the original cron-every-60s design (build a fresh
CLOB client and re-derive API creds every invocation, then exit and sit
idle for ~5s before the next tick) wasted real time on process/client
startup and left a dead gap between invocations where the wallet's
trades went unwatched entirely. A persistent process builds the client
ONCE and never stops polling. Because this process is long-lived, a code
change here needs `systemctl restart atlantis-btc5m-copy` to take effect -
git pull alone does not reload a running process's already-imported
code. Git sync of the STATE/LOG files (not the code) is handled by a
separate, lightweight cron entry that only runs git commands.

Polls data-api.polymarket.com/trades (same endpoint already used for
fill confirmation elsewhere in this repo) every ~0.5s.

Order placement and fill-confirmation are decoupled: place_market_buy
returns almost immediately (the order response itself already carries a
parsed price/size in the common case - see clob_client.py's
_parse_order_response), so the row is logged and the polling loop moves
on to the NEXT candidate trade right away. The extra get_confirmed_fill
safety check (added after a real incident where a raw response's fields
were unparseable and the fill went unconfirmed) runs in a background
thread instead of blocking the loop, and updates the same row once
confirmed. A lock serializes writes to the shared CSV between the main
loop and these background threads.

Own $50 capital pool (see atlantis.live.btc5m_config.load_btc5m_copy_live_settings)
and own trade log (outputs/live_trade_log_btc5m_copy.csv) - kept
separate from E/B's shared pool since this is a fresh, untested
mechanism, not because it needs different credentials (same real
account/funder as everything else).

Real-money safety carried over from this session's hard-won lessons:
- First-ever run establishes a "seen trades" baseline WITHOUT copying
  anything - otherwise the very first invocation would try to copy the
  wallet's entire recent trade history from already-resolved windows.
- Dedicated kill switch, same mechanics as E/B (auto-pause if realized
  losses reach the full allocated capital) - re-checked periodically,
  not just once at startup, since this process never exits on its own.
"""

from __future__ import annotations

import csv
import json
import sys
import threading
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

POLL_INTERVAL_SECONDS = 0.5
# How often (in poll iterations) to re-check the kill switch / reconcile
# resolved positions / re-read the enabled flag - this process never
# exits on its own, so these can't be "once at startup" like E/B.
HOUSEKEEPING_INTERVAL_SECONDS = 30

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

log_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


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


def _confirm_fill_later(settings, condition_id: str, token_id: str, order_ts: int, entry_key: str) -> None:
    """Runs in a background thread - see module docstring. Re-checks
    /trades directly (the known-safe source of truth) and corrects the
    already-logged row if the order response's own parsed price/size
    turns out to have been missing or wrong. Reloads the log fresh under
    the lock rather than reusing a dict captured earlier - the main loop
    may have added/modified other rows (or another background thread may
    have) since this thread started, and writing a stale snapshot back
    would silently erase those."""
    confirmed = get_confirmed_fill(
        wallet_address=settings.funder_address,
        condition_id=condition_id,
        asset=token_id,
        side="BUY",
        since_ts=order_ts - 30,
    )
    if not confirmed:
        return
    fill_price, fill_size = confirmed
    with log_lock:
        log = load_log(settings.live_trade_log_path)
        row = log.get(entry_key)
        if row is None:
            return
        row["fill_price_buy"] = str(fill_price)
        row["stake_usd_actual"] = str(fill_price * fill_size)
        row["shares_held"] = str(fill_size)
        row["last_updated"] = _now()
        save_log(settings.live_trade_log_path, log)


def copy_trade(settings, client, trade: dict) -> None:
    tx_hash = str(trade.get("transactionHash", ""))
    entry_key = f"COPY_{tx_hash}"
    with log_lock:
        log = load_log(settings.live_trade_log_path)
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
        with log_lock:
            log = load_log(settings.live_trade_log_path)
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
        _log(f"btc5m-copy: EXCEPTION copiando {entry_key}: {exc}")
        return

    # Use whatever price/size the order response itself already parsed
    # (see clob_client.py's _parse_order_response) - log right away and
    # let the loop move on to the next candidate immediately. The
    # background thread below re-verifies against /trades and corrects
    # this row if the raw response turns out to have been missing/wrong.
    fill_price, fill_size = result.avg_fill_price, result.filled_size
    status_value = "EXECUTED" if result.success else "ERROR"
    with log_lock:
        log = load_log(settings.live_trade_log_path)
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
        _log(f"btc5m-copy: ORDEN FALLIDA {outcome} {entry_key}: {result.error}")
        return

    _log(f"btc5m-copy: EJECUTADO {outcome} {entry_key} @ {fill_price}, stake ${log[entry_key]['stake_usd_actual']}")
    threading.Thread(
        target=_confirm_fill_later,
        args=(settings, condition_id, token_id, order_ts, entry_key),
        daemon=True,
    ).start()


def main() -> None:
    settings = load_btc5m_copy_live_settings()
    client = None
    established = SEEN_TRADES_PATH.exists()
    seen = load_seen_trades()
    enabled = False
    last_housekeeping = 0.0

    _log("btc5m-copy: proceso persistente arrancando")

    while True:
        # Wall-clock based, not a poll-count modulo - the idle branch
        # below sleeps 5s/iteration while the active branch sleeps 0.5s/
        # iteration, so counting iterations made this check fire every
        # ~30s while active but every ~5 MINUTES while idle (60 iterations
        # x 5s instead of x 0.5s). Confirmed live 2026-08-05: enabling
        # copying took several minutes to be noticed because of exactly
        # this - the status flag flip sat unread until the next
        # housekeeping pass finally rolled around.
        if time.time() - last_housekeeping >= HOUSEKEEPING_INTERVAL_SECONDS:
            last_housekeeping = time.time()
            with log_lock:
                log = load_log(settings.live_trade_log_path)
                resolved_count, _notifications = reconcile_resolved_positions(log)
                if resolved_count:
                    save_log(settings.live_trade_log_path, log)
                    _log(f"btc5m-copy: reconciled {resolved_count} position(s)")
            check_kill_switch(settings)
            status = read_status_flag(settings)
            enabled = bool(status.get("enabled"))
            if enabled and client is None:
                from atlantis.polymarket.clob_client import build_live_client

                client = build_live_client(settings)
                _log("btc5m-copy: cliente real construido, activo")
            elif not enabled and client is not None:
                client = None
                _log("btc5m-copy: trading real apagado - pausando copiado")

        if not enabled:
            time.sleep(5)  # coarser idle poll while off - no point hammering the API
            continue

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
                # Dispatched, not called inline - copy_trade does several
                # sequential network calls (best-price/order-book lookup,
                # tick size, the order POST itself), each of which can be
                # slow under the network flakiness this whole session has
                # seen repeatedly. Calling it inline here blocks detection
                # of NEWER trades until it returns; if several of the
                # wallet's trades land in one poll (it trades several
                # times a second sometimes) a single slow one backs up
                # everything behind it. Confirmed live 2026-08-05: two
                # real copies landed 208s and 307s after the wallet's own
                # trade - far more than the polling interval could
                # explain on its own.
                threading.Thread(target=copy_trade, args=(settings, client, t), daemon=True).start()
        if candidates:
            save_seen_trades(seen)
        established = True

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
