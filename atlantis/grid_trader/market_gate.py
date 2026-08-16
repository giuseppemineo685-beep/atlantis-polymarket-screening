"""Market-wide gate on new entries, added 2026-08-16 after the June
walk-forward window showed -1.26% ROI / 50.9% win rate / 9-of-57
positions with >$100 drawdown - traced to a real ~-20% BTC correction
that month (73.6k -> 58.6k), which drags MOST altcoins down together
(correlated, not independent risk) and breaks flat-grid ranges broadly
at once. The owner's own framing: BTC lateral or rising should be a
precondition for opening new grid positions, not something ignored per
symbol."""

from __future__ import annotations

from atlantis.grid_screener.metrics import VOL_WINDOW_DAYS, efficiency_ratio, net_move_pct, regimen_from_er


def btc_market_ok(btc_daily_klines_before: list) -> tuple[bool, str]:
    """True unless BTC itself is in a strong (regimen=='fuerte')
    DOWNTREND as of the data available - blocks new entries in EITHER
    strategy during that condition, since the June breakdown wasn't
    limited to flat grids (the one trend_long candidate that month,
    HUSDT, also lost -$122)."""
    if len(btc_daily_klines_before) < VOL_WINDOW_DAYS:
        return True, "sin suficiente historial de BTC, se permite por defecto"

    er = efficiency_ratio(btc_daily_klines_before)
    regimen = regimen_from_er(er)
    move = net_move_pct(btc_daily_klines_before)

    if regimen == "fuerte" and move < 0:
        return False, f"BTC en tendencia bajista fuerte (ER={er:.2f}, {move:+.1f}% en {VOL_WINDOW_DAYS}d) - no se abren posiciones nuevas"
    return True, f"BTC regimen={regimen} movimiento={move:+.1f}% - ok para entrar"
