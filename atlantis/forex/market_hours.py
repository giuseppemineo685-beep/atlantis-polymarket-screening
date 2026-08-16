"""Forex-specific risk the crypto grid trader never needed: the market
CLOSES every weekend (roughly Friday 21:00 UTC to Sunday 21:00 UTC,
when the Asia-Pacific session opens) and price can gap hard across that
close - a position sized for continuous intraday movement can wake up
Monday already past its own stop-loss with no fill in between. New
entries are blocked near the close/open; existing positions are just
left alone over the weekend (no real close-out logic yet - v1 scope,
stated plainly, see docs)."""

from __future__ import annotations

from datetime import datetime, timezone

MARKET_CLOSE_WEEKDAY = 4  # Friday (Monday=0)
MARKET_CLOSE_HOUR_UTC = 21
MARKET_OPEN_WEEKDAY = 6  # Sunday
MARKET_OPEN_HOUR_UTC = 21
# Extra buffer before close / after open where new entries are blocked
# even though the market is technically still open - avoids opening a
# fresh grid right before a weekend gap, or right as illiquid Sunday-
# evening pricing resumes.
PRE_CLOSE_BUFFER_HOURS = 2
POST_OPEN_BUFFER_HOURS = 2


def market_is_open(now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour + now.minute / 60

    if weekday == 5:  # Saturday - always closed
        return False, "mercado cerrado (sabado)"
    if weekday == MARKET_CLOSE_WEEKDAY and hour >= MARKET_CLOSE_HOUR_UTC:
        return False, "mercado cerrado (viernes despues del cierre)"
    if weekday == MARKET_OPEN_WEEKDAY and hour < MARKET_OPEN_HOUR_UTC:
        return False, "mercado cerrado (domingo antes de la apertura)"
    return True, "mercado abierto"


def ok_to_open_new_position(now: datetime | None = None) -> tuple[bool, str]:
    """Stricter than market_is_open: also blocks the buffer windows
    right around the weekly close/open."""
    now = now or datetime.now(timezone.utc)
    is_open, reason = market_is_open(now)
    if not is_open:
        return False, reason

    weekday = now.weekday()
    hour = now.hour + now.minute / 60

    if weekday == MARKET_CLOSE_WEEKDAY and hour >= MARKET_CLOSE_HOUR_UTC - PRE_CLOSE_BUFFER_HOURS:
        return False, f"cerca del cierre semanal (viernes, <{PRE_CLOSE_BUFFER_HOURS}h) - no se abren posiciones nuevas"
    if weekday == MARKET_OPEN_WEEKDAY and hour < MARKET_OPEN_HOUR_UTC + POST_OPEN_BUFFER_HOURS:
        return False, f"recien abrio el mercado (domingo, <{POST_OPEN_BUFFER_HOURS}h) - no se abren posiciones nuevas"
    return True, "ok para abrir posiciones nuevas"
