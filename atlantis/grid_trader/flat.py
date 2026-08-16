"""Flat/neutral grid: static range, symmetric levels. The classic
grid - built once from the recent N-day high/low and left alone (a
truly flat market doesn't need its range chased)."""

from __future__ import annotations

from decimal import Decimal

RANGE_WINDOW_DAYS = 14
NUM_LEVELS = 20


def compute_flat_grid_bounds(daily_klines: list) -> tuple[Decimal, Decimal]:
    window = daily_klines[-RANGE_WINDOW_DAYS:]
    hi = max(Decimal(str(k[2])) for k in window)
    lo = min(Decimal(str(k[3])) for k in window)
    return lo, hi
