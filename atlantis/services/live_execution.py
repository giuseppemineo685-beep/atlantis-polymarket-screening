from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from atlantis.live.config import LiveSettings
from atlantis.services.live_intents import read_intents
from atlantis.services.live_status import read_status_flag, write_status_flag

LIVE_TRADE_LOG_FIELDS = [
    "condition_id",
    "asset",
    "title",
    "slug",
    "outcome",
    "traders",
    "date_first_seen",
    "signal_price",
    "fill_price_buy",
    "stake_usd_requested",
    "stake_usd_actual",
    "shares_held",
    "order_id_buy",
    "status",
    "fill_price_sell",
    "order_id_sell",
    "realized_pnl_usd",
    "pct_return",
    "date_closed",
    "last_updated",
]


def _intent_key(intent: dict) -> str:
    return f"{intent.get('condition_id', '')}|{intent.get('asset', '')}"


def load_live_trade_log(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {_intent_key(row): row for row in rows}


def save_live_trade_log(path: Path, log: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LIVE_TRADE_LOG_FIELDS)
        writer.writeheader()
        for row in log.values():
            writer.writerow({field: row.get(field, "") for field in LIVE_TRADE_LOG_FIELDS})


@dataclass(frozen=True)
class ExecutionSummary:
    dry_run: bool
    buys_processed: int
    sells_processed: int
    skipped_duplicate: int
    kill_switch_blocked: int
    errors: list[str]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def process_pending_intents(*, settings: LiveSettings, clob_client_factory=None) -> ExecutionSummary:
    """clob_client_factory is injectable for testing; defaults to the real
    atlantis.polymarket.clob_client.build_live_client, imported lazily so
    dry-run mode never needs py_clob_client_v2 installed at all."""
    status = read_status_flag(settings)
    live_enabled = bool(status.get("enabled", False))

    intents = read_intents(settings.intents_queue_path)
    dry_log = load_live_trade_log(settings.dryrun_trade_log_path)
    real_log = load_live_trade_log(settings.live_trade_log_path)

    buys = sells = skipped = killed = 0
    errors: list[str] = []

    client = None
    if live_enabled:
        if clob_client_factory is None:
            from atlantis.polymarket.clob_client import build_live_client as clob_client_factory
        client = clob_client_factory(settings)

    for intent in intents:
        key = _intent_key(intent)
        if not key.strip("|"):
            continue

        if intent["intent_type"] == "BUY":
            target_log = real_log if live_enabled else dry_log
            if key in target_log:
                skipped += 1
                continue

            if live_enabled:
                kill_status = read_status_flag(settings)
                if not kill_status.get("enabled", False):
                    killed += 1
                    continue
                try:
                    result = client.place_market_buy(intent["asset"], Decimal(str(settings.stake_per_signal_usd)))
                except Exception as exc:
                    errors.append(f"BUY {intent.get('title')}: {exc}")
                    continue
                status_value = "EXECUTED" if result.success else "ERROR"
                target_log[key] = {
                    "condition_id": intent["condition_id"],
                    "asset": intent["asset"],
                    "title": intent["title"],
                    "slug": intent.get("slug", ""),
                    "outcome": intent["outcome"],
                    "traders": intent.get("traders", ""),
                    "date_first_seen": intent["ts"],
                    "signal_price": intent.get("signal_price", ""),
                    "fill_price_buy": str(result.avg_fill_price) if result.avg_fill_price else "",
                    "stake_usd_requested": str(settings.stake_per_signal_usd),
                    "stake_usd_actual": str(result.avg_fill_price * result.filled_size) if (result.avg_fill_price and result.filled_size) else "",
                    "shares_held": str(result.filled_size) if result.filled_size else "",
                    "order_id_buy": result.order_id or "",
                    "status": status_value,
                    "fill_price_sell": "",
                    "order_id_sell": "",
                    "realized_pnl_usd": "",
                    "pct_return": "",
                    "date_closed": "",
                    "last_updated": _now(),
                }
                if not result.success:
                    errors.append(f"BUY {intent.get('title')}: {result.error}")
            else:
                target_log[key] = {
                    "condition_id": intent["condition_id"],
                    "asset": intent["asset"],
                    "title": intent["title"],
                    "slug": intent.get("slug", ""),
                    "outcome": intent["outcome"],
                    "traders": intent.get("traders", ""),
                    "date_first_seen": intent["ts"],
                    "signal_price": intent.get("signal_price", ""),
                    "fill_price_buy": intent.get("signal_price", ""),
                    "stake_usd_requested": str(settings.stake_per_signal_usd),
                    "stake_usd_actual": str(settings.stake_per_signal_usd),
                    "shares_held": "",
                    "order_id_buy": "",
                    "status": "DRY_RUN",
                    "fill_price_sell": "",
                    "order_id_sell": "",
                    "realized_pnl_usd": "",
                    "pct_return": "",
                    "date_closed": "",
                    "last_updated": _now(),
                }
            buys += 1

        elif intent["intent_type"] == "SELL":
            target_log = real_log if live_enabled else dry_log
            row = target_log.get(key)
            if row is None or row.get("status") not in ("EXECUTED", "DRY_RUN"):
                skipped += 1
                continue

            if live_enabled:
                shares = row.get("shares_held")
                if not shares:
                    errors.append(f"SELL {intent.get('title')}: sin shares_held registradas, no se puede vender")
                    continue
                try:
                    result = client.place_market_sell(intent["asset"], Decimal(shares))
                except Exception as exc:
                    errors.append(f"SELL {intent.get('title')}: {exc}")
                    continue
                if result.success:
                    buy_price = Decimal(row["fill_price_buy"]) if row.get("fill_price_buy") else None
                    sell_price = result.avg_fill_price
                    pnl = None
                    pct = None
                    if buy_price and sell_price:
                        stake = Decimal(row.get("stake_usd_actual") or 0)
                        pnl = (sell_price - buy_price) / buy_price * stake if buy_price else None
                        pct = (sell_price / buy_price - 1) * 100
                    row["status"] = "CLOSED"
                    row["fill_price_sell"] = str(sell_price) if sell_price else ""
                    row["order_id_sell"] = result.order_id or ""
                    row["realized_pnl_usd"] = str(pnl) if pnl is not None else ""
                    row["pct_return"] = str(pct) if pct is not None else ""
                    row["date_closed"] = _now()
                else:
                    errors.append(f"SELL {intent.get('title')}: {result.error}")
                row["last_updated"] = _now()
            else:
                row["status"] = "CLOSED"
                row["fill_price_sell"] = intent.get("signal_price", "")
                row["date_closed"] = _now()
                row["last_updated"] = _now()
            sells += 1

    if live_enabled:
        save_live_trade_log(settings.live_trade_log_path, real_log)
    save_live_trade_log(settings.dryrun_trade_log_path, dry_log)

    return ExecutionSummary(
        dry_run=not live_enabled,
        buys_processed=buys,
        sells_processed=sells,
        skipped_duplicate=skipped,
        kill_switch_blocked=killed,
        errors=errors,
    )


def format_execution_summary(summary: ExecutionSummary) -> str:
    lines = [
        f"modo:                {'DRY-RUN' if summary.dry_run else 'EN VIVO'}",
        f"compras procesadas:  {summary.buys_processed}",
        f"ventas procesadas:   {summary.sells_processed}",
        f"omitidas (dup):      {summary.skipped_duplicate}",
        f"bloqueadas (switch): {summary.kill_switch_blocked}",
    ]
    if summary.errors:
        lines.append("errores:")
        lines.extend(f"  - {e}" for e in summary.errors)
    return "\n".join(lines)
