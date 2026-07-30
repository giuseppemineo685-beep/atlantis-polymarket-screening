from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from atlantis.live.config import LiveSettings


@dataclass(frozen=True)
class LiveStatusSummary:
    enabled: bool
    auto_killed: bool
    reason: str
    since: str
    realized_pnl_usd: Decimal  # lifetime total, for display
    realized_pnl_since_reset_usd: Decimal  # what the kill switch actually measures against
    pnl_baseline_usd: Decimal
    pct_of_bankroll_consumed: Decimal
    open_positions: int
    won_unredeemed: int


DEFAULT_STATUS = {"enabled": False, "auto_killed": False, "reason": "", "since": "", "pnl_baseline_usd": "0"}


def read_status_flag(settings: LiveSettings) -> dict:
    if not settings.status_path.exists():
        return dict(DEFAULT_STATUS)
    try:
        flag = json.loads(settings.status_path.read_text())
    except (json.JSONDecodeError, OSError):
        # Fail closed: an unparseable switch file must never be read as "on".
        return {**DEFAULT_STATUS, "auto_killed": True, "reason": "status file unreadable"}
    flag.setdefault("pnl_baseline_usd", "0")
    return flag


def write_status_flag(
    settings: LiveSettings,
    *,
    enabled: bool,
    auto_killed: bool,
    reason: str,
    since: str,
    pnl_baseline_usd: Decimal | None = None,
) -> None:
    # Preserve the existing baseline unless the caller explicitly passes a
    # new one - the kill switch's own auto-trip must never reset it (that
    # would erase the very loss it just detected).
    if pnl_baseline_usd is None:
        pnl_baseline_usd = Decimal(read_status_flag(settings).get("pnl_baseline_usd", "0"))
    settings.status_path.parent.mkdir(parents=True, exist_ok=True)
    settings.status_path.write_text(
        json.dumps(
            {
                "enabled": enabled,
                "auto_killed": auto_killed,
                "reason": reason,
                "since": since,
                "pnl_baseline_usd": str(pnl_baseline_usd),
            },
            indent=2,
        )
    )


def compute_live_status(settings: LiveSettings) -> LiveStatusSummary:
    flag = read_status_flag(settings)

    rows: list[dict] = []
    if settings.live_trade_log_path.exists():
        with settings.live_trade_log_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

    realized_pnl = sum(
        (Decimal(r["realized_pnl_usd"]) for r in rows if r.get("realized_pnl_usd") not in (None, "")),
        Decimal("0"),
    )
    pnl_baseline = Decimal(str(flag.get("pnl_baseline_usd", "0")))
    # Everything before a manual reset is "known history" - only PnL earned
    # SINCE the baseline was set counts toward tripping the kill switch
    # again. Without this, reactivating after a trip would just re-trip on
    # the very next cycle since the historical loss never goes away.
    realized_since_reset = realized_pnl - pnl_baseline

    open_positions = sum(1 for r in rows if r.get("status") in ("EXECUTED", "PENDING"))
    won_unredeemed = sum(1 for r in rows if r.get("status") == "WON_UNREDEEMED")

    bankroll = Decimal(str(settings.initial_bankroll_usd)) if settings.initial_bankroll_usd else Decimal("0")
    pct_consumed = (-realized_since_reset / bankroll * 100) if bankroll else Decimal("0")

    return LiveStatusSummary(
        enabled=bool(flag.get("enabled", False)),
        auto_killed=bool(flag.get("auto_killed", False)),
        reason=str(flag.get("reason", "")),
        since=str(flag.get("since", "")),
        realized_pnl_usd=realized_pnl,
        realized_pnl_since_reset_usd=realized_since_reset,
        pnl_baseline_usd=pnl_baseline,
        pct_of_bankroll_consumed=pct_consumed,
        open_positions=open_positions,
        won_unredeemed=won_unredeemed,
    )


def format_live_status(summary: LiveStatusSummary, settings: LiveSettings) -> str:
    lines = [
        f"trading en vivo:        {'ACTIVADO' if summary.enabled else 'APAGADO'}",
    ]
    if summary.auto_killed:
        lines.append(f"kill switch disparado:   SI  ({summary.reason})")
    lines.extend(
        [
            f"capital asignado:        ${settings.initial_bankroll_usd:,.2f}",
            f"kill switch en:          -{settings.kill_switch_loss_pct:.1f}% (${settings.initial_bankroll_usd * settings.kill_switch_loss_pct / 100:,.2f})",
            f"pnl real acumulado (total historico): ${summary.realized_pnl_usd:,.2f}",
            f"pnl desde el ultimo reset (esto mide el kill switch): ${summary.realized_pnl_since_reset_usd:,.2f}",
            f"% de capital consumido: {summary.pct_of_bankroll_consumed:.1f}%",
            f"posiciones abiertas:     {summary.open_positions}",
            f"ganadas sin redimir:     {summary.won_unredeemed}",
        ]
    )
    return "\n".join(lines)
