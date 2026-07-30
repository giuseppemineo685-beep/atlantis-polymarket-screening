from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from atlantis.live.config import LiveSettings
from atlantis.services.live_status import compute_live_status, write_status_flag


def evaluate_and_maybe_trip(settings: LiveSettings) -> bool:
    """Checks cumulative realized PnL from outputs/live_trade_log.csv against
    the configured loss threshold. If breached, writes enabled: false with
    auto_killed: true - this does NOT self-reset on a later win; a human
    must edit state/live_trading_status.json back to re-enable. Returns True
    if the kill switch is (now, or already) tripped."""
    summary = compute_live_status(settings)
    if summary.auto_killed:
        return True

    threshold = Decimal(str(settings.initial_bankroll_usd)) * Decimal(str(settings.kill_switch_loss_pct)) / 100
    if -summary.realized_pnl_since_reset_usd >= threshold:
        write_status_flag(
            settings,
            enabled=False,
            auto_killed=True,
            reason=(
                f"perdida acumulada real (desde el ultimo reset) ${-summary.realized_pnl_since_reset_usd:,.2f} "
                f">= umbral ${threshold:,.2f} ({settings.kill_switch_loss_pct}% de ${settings.initial_bankroll_usd:,.2f})"
            ),
            since=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            # Preserve the baseline - explicitly don't touch it here. The
            # auto-trip must never look like a reset.
        )
        return True
    return False
