"""'Screen 2' - a deeper pass on a SHORT list of symbols already picked
from the Binance copy-trade marketplace (Screen 1, done by hand/other
Claude sessions reading the marketplace UI - no public API for that).

Deliberately kept separate from the bot's own historical performance
number (ROI, PnL, MDD from the marketplace) - this only answers "is
the market itself in good shape to enter RIGHT NOW", independent of how
well the copied bot did historically. A great historical bot on a
market that's about to break its range is still a bad entry.

Two independent 0-100 scores, not blended into one number on purpose
(the owner explicitly asked for this after seeing another Claude
session's proposal) - collapsing "is the market rangebound" and "is
there a leverage buildup that could cause a squeeze" into a single
score hides which one is actually the problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from atlantis.grid_screener.binance_client import fetch_daily_klines
from atlantis.grid_screener.futures_metrics import (
    fetch_funding_rate_pct,
    fetch_long_short_ratio,
    fetch_open_interest_change_pct,
    fetch_spread_pct,
)
from atlantis.grid_screener.metrics import (
    CORR_WINDOW_DAYS,
    bollinger_band_width_pct,
    correlacion_bucket,
    daily_log_returns,
    daily_volatility_pct,
    efficiency_ratio,
    pearson,
    position_in_range_pct,
    regimen_from_er,
    volume_change_pct,
)


@dataclass
class DeepDiveResult:
    symbol: str
    market_quality: int
    futures_risk: int
    entry_timing: str  # "bueno" | "cuidado" | "evitar"
    regimen: str
    vol_pct: float
    pos_pct: float
    bb_width_pct: float
    volume_change_pct: float
    funding_rate_pct: Decimal | None
    oi_change_pct: Decimal | None
    long_short_ratio: Decimal | None
    spread_pct: Decimal | None
    btc_corr: float
    notes: list[str]


def _market_quality_score(*, regimen: str, pos_pct: float, vol_pct: float, bb_width_pct: float) -> tuple[int, list[str]]:
    notes = []
    score = 0

    if regimen == "rango":
        score += 40
    elif regimen == "leve":
        score += 20
    else:
        notes.append("tendencia fuerte - el rango del grid puede quedar obsoleto rapido")

    if 30 <= pos_pct <= 70:
        score += 30
    elif 20 <= pos_pct <= 80:
        score += 18
    else:
        score += 4
        notes.append(f"precio en el extremo del rango ({pos_pct:.0f}%) - posible entrada tardia")

    if 1.2 <= vol_pct <= 6:
        score += 20
    elif vol_pct < 1.2:
        score += 8
        notes.append("volatilidad muy baja - las comisiones pueden comerse la ganancia")
    elif vol_pct <= 10:
        score += 14
    else:
        score += 5
        notes.append("volatilidad muy alta - rango dificil de contener")

    # Wide/expanding bands can mean a breakout is starting, not just
    # "normal" volatility - penalize extremes, not a fixed threshold,
    # since "wide" is relative to the pair's own vol_pct.
    if bb_width_pct <= vol_pct * 6:
        score += 10
    elif bb_width_pct <= vol_pct * 10:
        score += 5
    else:
        notes.append("bandas de Bollinger muy anchas relativo a la volatilidad normal - posible ruptura en curso")

    return min(score, 100), notes


def _futures_risk_score(
    *, funding_pct: Decimal | None, oi_change_pct: Decimal | None,
    long_short_ratio: Decimal | None, spread_pct: Decimal | None,
) -> tuple[int, list[str]]:
    notes = []
    score = 0

    if funding_pct is None:
        notes.append("sin dato de funding rate")
    else:
        f = abs(funding_pct)
        if f <= 0.01:
            score += 25
        elif f <= 0.03:
            score += 15
        elif f <= 0.05:
            score += 8
            notes.append(f"funding rate elevado ({funding_pct:+.4f}%) - costo de mantener la posicion")
        else:
            notes.append(f"funding rate extremo ({funding_pct:+.4f}%) - costo alto de mantener la posicion")

    if oi_change_pct is None:
        notes.append("sin dato de open interest")
    else:
        o = abs(oi_change_pct)
        if o <= 5:
            score += 25
        elif o <= 15:
            score += 15
        elif o <= 30:
            score += 8
            notes.append(f"open interest cambio {oi_change_pct:+.1f}% en 24h - posicionamiento apalancado en movimiento")
        else:
            notes.append(f"open interest cambio {oi_change_pct:+.1f}% en 24h - acumulacion/liquidacion fuerte de leverage")

    if long_short_ratio is None:
        notes.append("sin dato de long/short ratio")
    else:
        r = float(long_short_ratio)
        if 0.7 <= r <= 1.4:
            score += 25
        elif 0.5 <= r <= 2.0:
            score += 15
        elif 0.3 <= r <= 3.0:
            score += 8
            notes.append(f"long/short ratio {r:.2f} - trade algo cargado de un lado")
        else:
            notes.append(f"long/short ratio {r:.2f} - trade muy cargado de un lado, riesgo de squeeze")

    if spread_pct is None:
        notes.append("sin dato de spread")
    else:
        s = float(spread_pct)
        if s <= 0.05:
            score += 25
        elif s <= 0.15:
            score += 15
        elif s <= 0.3:
            score += 8
        else:
            notes.append(f"spread ancho ({s:.2f}%) - cuesta mas ejecutar muchas operaciones de grid")

    return min(score, 100), notes


def _entry_timing(market_quality: int, futures_risk: int, regimen: str, pos_pct: float) -> str:
    if regimen == "fuerte" or market_quality < 40 or futures_risk < 30:
        return "evitar"
    if market_quality >= 70 and futures_risk >= 60 and 20 <= pos_pct <= 80:
        return "bueno"
    return "cuidado"


def evaluar_profundo(symbol: str, btc_returns: list[float]) -> DeepDiveResult | None:
    klines = fetch_daily_klines(symbol, days=65)
    if not klines or len(klines) < 20:
        return None

    regimen = regimen_from_er(efficiency_ratio(klines))
    vol_pct = daily_volatility_pct(klines)
    pos_pct = position_in_range_pct(klines)
    bb_width = bollinger_band_width_pct(klines)
    vol_change = volume_change_pct(klines)
    returns = daily_log_returns(klines, window_days=CORR_WINDOW_DAYS)
    btc_corr = pearson(returns, btc_returns)

    funding = fetch_funding_rate_pct(symbol)
    oi_change = fetch_open_interest_change_pct(symbol)
    ls_ratio = fetch_long_short_ratio(symbol)
    spread = fetch_spread_pct(symbol)

    mq_score, mq_notes = _market_quality_score(regimen=regimen, pos_pct=pos_pct, vol_pct=vol_pct, bb_width_pct=bb_width)
    fr_score, fr_notes = _futures_risk_score(
        funding_pct=funding, oi_change_pct=oi_change, long_short_ratio=ls_ratio, spread_pct=spread,
    )
    timing = _entry_timing(mq_score, fr_score, regimen, pos_pct)

    return DeepDiveResult(
        symbol=symbol, market_quality=mq_score, futures_risk=fr_score, entry_timing=timing,
        regimen=regimen, vol_pct=vol_pct, pos_pct=pos_pct, bb_width_pct=bb_width,
        volume_change_pct=vol_change, funding_rate_pct=funding, oi_change_pct=oi_change,
        long_short_ratio=ls_ratio, spread_pct=spread, btc_corr=btc_corr,
        notes=mq_notes + fr_notes,
    )
