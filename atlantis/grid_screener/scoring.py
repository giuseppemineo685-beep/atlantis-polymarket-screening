"""Same rubric as the manual grid-bot screener tool (the one built in
Claude web, later fixed and ported to a standalone artifact) - kept
faithful on purpose so the automatic Binance-fed list and the
"eyeball it yourself" tool always agree on what counts as a good grid
candidate. Weights sum to 100: volatilidad 25, regimen 30, posicion en
rango 20, liquidez 15, correlacion 10."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Flag:
    text: str
    level: str  # "warn" or "bad"


@dataclass
class Candidate:
    symbol: str
    vol_pct: float  # daily volatility, as a percent (3.5 means 3.5%)
    regimen: str  # "rango" | "leve" | "fuerte"
    pos_pct: float  # 0-100, where the price sits in its recent range
    liquidez: str  # "alto" | "medio" | "bajo"
    correlacion: str  # "alto" | "medio" | "bajo" (vs BTC)
    lev: float = 1.0


@dataclass
class Evaluation:
    score: int
    verdict: str  # "bueno" | "marginal" | "evitar"
    flags: list[Flag] = field(default_factory=list)


def evaluar(c: Candidate) -> Evaluation:
    flags: list[Flag] = []
    s = 0.0

    if c.vol_pct < 1.2:
        vol_score = 6
        flags.append(Flag("Volatilidad baja: las comisiones pueden comerse la ganancia", "warn"))
    elif c.vol_pct <= 6:
        vol_score = 25
    elif c.vol_pct <= 10:
        vol_score = 18
    else:
        vol_score = 10
        flags.append(Flag("Volatilidad muy alta: rango dificil de contener, mas riesgo", "warn"))
    s += vol_score

    if c.regimen == "rango":
        reg_score = 30
    elif c.regimen == "leve":
        reg_score = 16
    else:
        reg_score = 2
        flags.append(Flag("Tendencia fuerte: el grid puede ser arrollado / liquidacion", "bad"))
    s += reg_score

    if c.pos_pct >= 30 and c.pos_pct <= 70:
        pos_score = 20
    elif c.pos_pct >= 20 and c.pos_pct <= 80:
        pos_score = 12
    else:
        pos_score = 3
        flags.append(Flag("Precio en el extremo del rango: riesgo de entrar tarde", "bad"))
    s += pos_score

    if c.liquidez == "alto":
        liq_score = 15
    elif c.liquidez == "medio":
        liq_score = 9
    else:
        liq_score = 3
        flags.append(Flag("Liquidez baja: slippage y riesgo de pump & dump", "warn"))
    s += liq_score

    if c.correlacion == "bajo":
        corr_score = 10
    elif c.correlacion == "medio":
        corr_score = 6
    else:
        corr_score = 2
        flags.append(Flag("Alta correlacion con BTC: aporta poca diversificacion", "warn"))
    s += corr_score

    if c.lev >= 5:
        flags.append(Flag(f"Apalancamiento {c.lev:g}x: riesgo alto de liquidacion", "bad"))
    elif c.lev >= 3:
        flags.append(Flag(f"Apalancamiento {c.lev:g}x: vigila el margen", "warn"))

    score = round(s)
    has_bad = any(f.level == "bad" for f in flags)
    if score >= 72 and not has_bad:
        verdict = "bueno"
    elif score >= 50 and not has_bad:
        verdict = "marginal"
    else:
        verdict = "evitar"

    return Evaluation(score=score, verdict=verdict, flags=flags)
