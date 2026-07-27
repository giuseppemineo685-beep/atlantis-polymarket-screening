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
    realized_pnl_usd: Decimal
    pct_of_bankroll_consumed: Decimal
    open_positions: int
    won_unredeemed: int


def read_status_flag(settings: LiveSettings) -> dict:
    if not settings.status_path.exists():
        return {"enabled": False, "auto_killed": False, "reason": "", "since": ""}
    try:
        return json.loads(settings.status_path.read_text())
    except (json.JSONDecodeError, OSError):
        # Fail closed: an unparseable switch file must never be read as "on".
        return {"enabled": False, "auto_killed": True, "reason": "status file unreadable", "since": ""}


def write_status_flag(settings: LiveSettings, *, enabled: bool, auto_killed: bool, reason: str, since: str) -> None:
    settings.status_path.parent.mkdir(parents=True, exist_ok=True)
    settings.status_path.write_text(
        json.dumps({"enabled": enabled, "auto_killed": auto_killed, "reason": reason, "since": since}, indent=2)
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
    open_positions = sum(1 for r in rows if r.get("status") in ("EXECUTED", "PENDING"))
    won_unredeemed = sum(1 for r in rows if r.get("status") == "WON_UNREDEEMED")

    bankroll = Decimal(str(settings.initial_bankroll_usd)) if settings.initial_bankroll_usd else Decimal("0")
    pct_consumed = (-realized_pnl / bankroll * 100) if bankroll else Decimal("0")

    return LiveStatusSummary(
        enabled=bool(flag.get("enabled", False)),
        auto_killed=bool(flag.get("auto_killed", False)),
        reason=str(flag.get("reason", "")),
        since=str(flag.get("since", "")),
        realized_pnl_usd=realized_pnl,
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
            f"pnl real acumulado:      ${summary.realized_pnl_usd:,.2f}",
            f"% de capital consumido: {summary.pct_of_bankroll_consumed:.1f}%",
            f"posiciones abiertas:     {summary.open_positions}",
            f"ganadas sin redimir:     {summary.won_unredeemed}",
        ]
    )
    return "\n".join(lines)
