# ATLANTIS — Grid-bot screening (Binance)

Metodologia y log de decisiones para elegir bots de grid en el marketplace
de copy-trading de Binance (Futures Grid). Empezo 2026-08-16. Esto es
plata real (copy-trade), no paper trading como el resto de ATLANTIS - por
eso el pipeline es mas conservador y en varias etapas en vez de un solo
score.

## Por que 2 sistemas separados, no 1

- `atlantis/grid_screener/` (tab "Grid screener" en el dashboard):
  escanea TODO el universo de pares USDS-M perpetual con volumen >= $2M,
  automatico via cron cada 15 min. Responde "que par seria bueno para
  correr TU PROPIO grid bot".
- Este documento: evalua bots de OTRA gente ya corriendo en el
  marketplace de copy-trade de Binance, que no tiene API publica -
  requiere pegar capturas/tablas a mano. Responde "cual de estos bots ya
  existentes conviene copiar con plata real".

Comparten el modulo `atlantis/grid_screener/metrics.py` y
`futures_metrics.py` (volatilidad, regimen, posicion en rango,
Bollinger, funding, open interest, long/short, spread) pero NO comparten
universo ni frecuencia.

## El pipeline (4 etapas)

### Screen 1 — Bot performance (marketplace de Binance)

Manual: el dueno manda capturas/tablas de binance.com/en/trading-bots
(Futures Grid). No hay API publica confirmada (probado 2026-08-16:
varios endpoints `bapi` devolvieron 403/404) - forzar mas endpoints no
documentados no vale la pena por fragilidad/riesgo de ToS.

Filtros duros aplicados antes de puntuar nada:
- `trades_total >= 30` (menos que eso es ruido, no una ventaja probada)
- `runtime_days >= 2`
- `7D MDD <= 50%`

Bot Score (0-100) = `roi_por_dia / (mdd + 3)`, normalizado 0-100 contra
el maximo del lote evaluado. `roi_por_dia = ROI% / dias_corriendo` -
normaliza bots con distinto tiempo de vida. Cuando el mismo simbolo
tiene varios bots (varias direcciones/configuraciones), se usa el de
mejor risk-adjusted score como representante de ese simbolo.

### Screen 2 — Market quality + Futures risk (automatico, API publica)

`python3 scripts/run_grid_deep_dive.py SIMBOLO1 SIMBOLO2 ...`

Corre sobre la lista corta que sobrevive el Screen 1 (10-15 simbolos
tipico). Dos scores 0-100 **separados a proposito** (no blendeados) -
ver `atlantis/grid_screener/deep_dive.py`:

**Market Quality** (regimen 40pts + posicion en rango 30pts +
volatilidad 20pts + ancho de Bollinger relativo 10pts):
- Regimen via Efficiency Ratio de Kaufman (rango/leve/fuerte)
- Posicion en el rango de 30 dias (30-70% = sano, extremos = tarde)
- Volatilidad diaria % (2-6% = punto dulce)
- Bollinger width relativo a la volatilidad normal del par (bandas muy
  anchas = posible ruptura en curso)

**Futures Risk** (funding 25pts + cambio de OI 25pts + long/short
25pts + spread 25pts):
- Funding rate actual (`/fapi/v1/premiumIndex`)
- Cambio de open interest en 24h (`/futures/data/openInterestHist`) -
  swings grandes = posicionamiento apalancado armandose/deshaciendose
  rapido
- Long/short ratio global (`/futures/data/globalLongShortAccountRatio`)
  - lejos de 1.0 = trade cargado de un lado, riesgo de squeeze
- Spread bid/ask real del book (`/fapi/v1/depth`)

`ENTRY TIMING` (bueno/cuidado/evitar) se deriva de ambos scores + el
regimen - ver `_entry_timing()` en el codigo para las reglas exactas.

**Bug real encontrado y arreglado 2026-08-16**: el calculo de cambio de
volumen comparaba la vela de HOY (parcial, a mitad de dia) contra el
promedio de dias completos - hacia ver una caida falsa de ~95% en TODOS
los simbolos sin importar la actividad real. Arreglado en
`metrics.volume_change_pct` descartando la vela en curso antes de
comparar.

### Screen 3 — Event / token risk (manual, NO automatizado)

Unlocks de tokens, listings/delistings, hacks, riesgo regulatorio,
noticias puntuales. Deliberadamente NO se intenta automatizar en batch -
no hay una fuente confiable via API de Binance para esto, y automatizarlo
mal seria peor que no tenerlo. Se hace como investigacion dirigida sobre
los 3-5 finalistas, no sobre toda la lista.

### Screen 4 — Portfolio (pendiente de construir)

Correlacion entre los ganadores finales entre si + con BTC (para no
terminar con 5 bots que son en la practica la misma apuesta),
exposicion neta long/short, capital total comprometido vs. minimos de
inversion. Todavia no implementado.

## FINAL score (cuando se calcula)

`FINAL = 0.40*BotScore + 0.35*MarketQuality + 0.25*FuturesRisk`

Es una referencia de orden, NO la decision. Un score FINAL alto con
`ENTRY TIMING = evitar` significa que el historial es bueno pero el
momento actual es malo - mirar los 3 numeros por separado siempre antes
de decidir, el blend puede esconder justamente el problema que importa.

## Log de decisiones

### 2026-08-16 — Primera seleccion (Screen 1 solamente, sin Screen 2 todavia)

5 bots elegidos de ~55 unicos evaluados (tabla completa pegada por el
dueno desde las primeras 6 paginas del marketplace), filtro MDD<=20% +
trades>=50 + runtime>=3d, ordenado por risk-adjusted score:

| # | Par | Direccion | ROI | MDD 7D | Trades | Min. inversion |
|---|-----|-----------|-----|--------|--------|-----------------|
| 1 | SOXLUSDT | Neutral | 31.7% | 5.2% | 109 | $11 |
| 2 | SNDKUSDT | Long | 31.6% | 4.3% | 943 | $601 |
| 3 | MUUSDT | Long | 28.7% | 5.8% | 149 | $58 |
| 4 | NOTUSDT | Neutral | 14.2% | 4.8% | 73 | $39 |
| 5 | SKHYUSDT | Long | 12.6% | 3.9% | 15,422 | $467 |

Confirmados por el dueno como los 5 reales invertidos. Capital minimo
total: ~$1,176 USDT. Seguimiento manual en la tab "Grid bots (Copy)" del
dashboard (`outputs/grid_copytrade_tracking.csv`, actualizado cuando el
dueno manda una lectura nueva).

### 2026-08-16 — Screen 2 corrido sobre esos 5 (retroactivo)

4 de 5 dieron `CUIDADO`, MUUSDT dio `EVITAR` (regimen=fuerte, precio al
90% del rango de 30 dias). Ningun bot tiene MDD/riesgo terrible, el
problema es timing de entrada, no calidad del bot en si. No se
deshicieron posiciones por esto - es informacion para la proxima ronda,
no una razon para revertir una decision ya tomada con otro criterio.

### 2026-08-16 — Screen 2 corrido sobre los 26 candidatos que pasaron Screen 1

Tabla combinada completa (BotScore/MarketQuality/FuturesRisk/FINAL):

| Par | Dir | BotScore | MktQuality | FuturesRisk | FINAL | Entry |
|-----|-----|---------:|-----------:|------------:|------:|-------|
| AKEUSDT | Neutral | 100 | 40 | 90 | 76 | evitar |
| BLUAIUSDT | Short | 53 | 59 | 70 | 59 | cuidado |
| SNXXUSDT | Long | 58 | 53 | 70 | 59 | cuidado |
| TUTUSDT | Short | 38 | 54 | 90 | 57 | cuidado |
| SOXLUSDT | Neutral | 33 | 62 | 90 | 57 | cuidado |
| ETHUSDT | Long | 1 | 100 | 83 | 56 | bueno |
| DOGEUSDT | Long | 1 | 100 | 83 | 56 | bueno |
| SNDKUSDT | Long | 40 | 43 | 75 | 50 | cuidado |
| HEIUSDT | Neutral | 18 | 59 | 90 | 50 | cuidado |
| NOTUSDT | Neutral | 20 | 68 | 73 | 50 | cuidado |
| SKHYUSDT | Long | 19 | 48 | 100 | 49 | cuidado |
| SUIUSDT | Neutral | 7 | 74 | 83 | 49 | cuidado |
| REUSDT | Long | 2 | 74 | 90 | 49 | bueno |
| BTRUSDT | Short | 23 | 53 | 80 | 48 | cuidado |
| BTCUSDT | Long | 2 | 74 | 83 | 47 | cuidado |
| MUUSDT | Long | 22 | 34 | 100 | 46 | evitar |
| AVAAIUSDT | Short | 29 | 28 | 100 | 46 | evitar |
| BNBUSDT | Long | 3 | 68 | 83 | 46 | cuidado |
| KORUUSDT | Long | 13 | 39 | 100 | 44 | evitar |
| CRCLUSDT | Long | 4 | 62 | 75 | 42 | cuidado |
| STARUSDT | Neutral | 19 | 59 | 55 | 42 | cuidado |
| BANKUSDT | Long | 6 | 49 | 90 | 42 | cuidado |
| XMRUSDT | Short | 0 | 54 | 90 | 41 | cuidado |
| XRPUSDT | Neutral | 1 | 34 | 75 | 31 | evitar |

(KOUSDT y 龙虾USDT/LOBSTERUSDT no se pudieron evaluar - simbolos no
resueltos en la API de futuros.)

**Ejemplo del porque no confiar solo en FINAL**: AKEUSDT queda #1 en
FINAL (76) por su BotScore dominante (100), pero su propio
`ENTRY TIMING` dice `evitar` (regimen=fuerte, volatilidad 25%+). El
historial fue espectacular; las condiciones actuales no lo justifican.
Screen 3 (event risk) y Screen 4 (portfolio) todavia no se corrieron
sobre ningun finalista.

## Como correr esto de nuevo

```bash
# Screen 2 sobre una lista corta (los simbolos que sobrevivieron Screen 1)
python3 scripts/run_grid_deep_dive.py SOXLUSDT SNDKUSDT MUUSDT NOTUSDT SKHYUSDT

# resultado tambien queda en outputs/grid_deep_dive_snapshot.csv
```

Screen 1 sigue siendo manual (pegar tabla/capturas). Screen 3 y 4 no
tienen script todavia.
