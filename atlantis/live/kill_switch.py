from __future__ import annotations

from atlantis.live.config import LiveSettings
from atlantis.services.live_status import compute_live_status


def evaluate_and_maybe_trip(settings: LiveSettings) -> bool:
    """Auto-trip on cumulative loss disabled per explicit user request
    (2026-08-01) - real trading no longer stops itself no matter how much
    is lost. Still honors an already-tripped state (e.g. if
    state/live_trading_status.json was set by hand), but never trips it
    automatically anymore. Returns True if the kill switch is (already)
    tripped."""
    summary = compute_live_status(settings)
    return bool(summary.auto_killed)
