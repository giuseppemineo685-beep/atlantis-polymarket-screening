# ATLANTIS — Grid trader: estrategias propias (flat + trend)

Empezo 2026-08-16. Distinto de `docs/GRID_BOT_SCREENING.md` (que evalua
bots de OTRA gente en el marketplace de copy-trade de Binance): esto es
para correr NUESTROS PROPIOS grid bots. Todavia en fase de backtest -
no esta corriendo ni en paper ni en real.

## Las 2 estrategias

### Flat / neutral grid (`atlantis/grid_trader/flat.py`)

El grid clasico. Rango estatico = maximo/minimo de los ultimos 14 dias
(`RANGE_WINDOW_DAYS`), 20 niveles parejos (`NUM_LEVELS`) entre ese
minimo y maximo. Compra cuando el precio toca un nivel desde arriba,
vende en el nivel inmediato superior - gana la diferencia en cada
"round trip", sin importar la direccion neta del precio, mientras se
quede dentro del rango.

Se arma UNA vez con datos previos al inicio y no se vuelve a tocar - un
mercado genuinamente plano no necesita perseguir su rango.

**Riesgo principal**: si el mercado deja de ser plano DESPUES de armar
el grid, el bot se queda comprando en la caida sin nunca poder vender
(ver el caso AIOUSDT en los resultados abajo).

### Trend grid (`atlantis/grid_trader/trend.py`)

Grid asimetrico y con reanclaje diario. 70% de los niveles
(`BUY_SIDE_FRACTION`) quedan por debajo del precio actual (para
acumular en las caidas de una tendencia alcista), 30% arriba (toma de
ganancia parcial). El rango completo se recalcula todos los dias usando
el minimo/maximo de los ultimos 14 dias - el grid "seguidor de
tendencia" se desplaza con el precio en vez de quedarse fijo.

**Alcance v1, dicho explicitamente: SOLO direccion `long`.** Un grid
bajista (mas niveles de venta arriba, recompra abajo) necesitaria un
motor de fills espejado (vender primero, comprar despues) que no esta
construido todavia - se decidio enviar una version long-only validada
primero antes de duplicar la superficie de codigo/bugs.

**Simplificacion del reanclaje diario, dicho explicitamente**: esta v1
NO migra posiciones abiertas de un dia al grid reconstruido del dia
siguiente (eso necesitaria bookkeeping real de posiciones entre
reconstrucciones). En cambio cada dia se simula de forma independiente:
cualquier posicion que siga abierta al ultimo bar del dia se marca a
mercado (cuenta como "realizada" ese dia), y el dia siguiente arranca
limpio con un grid recien anclado. Es una simplificacion real de lo que
haria un grid corriendo continuamente, no una escondida - ver
`atlantis/grid_trader/backtest_engine.py::run_trend_backtest`.

## El modelo (motor compartido)

`atlantis/grid_trader/grid_math.py::simulate_grid` - toma un camino de
precios (velas OHLC, solo low/high) y una lista de niveles, devuelve
profit realizado/no realizado, comisiones, trades, y drawdown maximo.

**Orden de fills dentro de una vela, dicho explicitamente**: primero se
resuelven las VENTAS de posiciones ya abiertas, despues se abren
COMPRAS nuevas. Como no hay datos tick-a-tick, esto subestima
levemente la frecuencia/ganancia en velas muy rapidas (una vela que en
la realidad barrio hacia abajo y volvio a subir en un solo movimiento
se registra como compra esta vela y venta la siguiente, no ambas en la
misma) - conservador, en la misma direccion que el resto de los
sesgos ya documentados en este repo (snapshots de prices-history,
granularidad de momentum, etc.), nunca en la direccion que haria ver
una estrategia mejor de lo que es.

**Comisiones**: 0.04% por operacion (`FEE_RATE`), tasa taker tipica de
Binance USDS-M Futures.

**Take-profit**: opcional, para de simular apenas el total (realizado +
no realizado marcado a mercado) alcanza el umbral - asi el bot no se
queda sentado en una posicion los 30 dias completos sin necesidad.

**Regimen**: reusa el Efficiency Ratio de Kaufman de
`atlantis/grid_screener/metrics.py` (rango/leve/fuerte) y agrega
`net_move_pct` (mismo signo que la tendencia) para decidir long vs
short en el candidato de tendencia.

## Metodologia de backtest: walk-forward, no "el mejor de hoy"

Version corregida 2026-08-16 a pedido del dueno, reemplazando un primer
intento que estaba mal planteado: elegia "lo que se ve mejor AHORA"
(usando el snapshot del screener de hoy) y corria un backtest con una
ventana arbitraria hacia atras - eso o probaba una clasificacion vieja
contra un regimen que ya habia cambiado (caso HYPEUSDT), o cherry-
pickeaba el movimiento mas extremo del momento (caso HUSDT, un token en
medio de un pump), que es una muestra sesgada, no una prueba justa del
metodo.

El metodo correcto (`scripts/backtest_grid_walkforward.py`):

1. Fijar T0 (un punto en el pasado, ej. hace 30 dias).
2. Para cada simbolo del universo, clasificar su regimen EN T0 usando
   SOLO datos anteriores a T0 (sin look-ahead).
3. Asignar la estrategia que ese regimen califica en ESE momento
   (rango -> flat, fuerte+alcista -> trend_long, fuerte+bajista ->
   no soportado v1, leve -> no califica).
4. Simular hacia adelante desde T0 con la estrategia asignada, con
   take-profit real (no aguantar el periodo completo por default).

## Resultados: 20 pares mas liquidos, T0 = 2026-07-17, 30 dias hacia adelante

Parametros: `usd_per_level=$20`, `20 niveles` (flat y trend),
`take-profit=10%` del capital de referencia ($400 por posicion),
comision 0.04%.

| Par | Estrategia | PnL | ROI% | Dias | Trades | Max DD | Salida |
|---|---|---:|---:|---:|---:|---:|---|
| CYSUSDT | flat | +$114.50 | +28.6% | 17.9 | 287 | -$40.67 | take-profit |
| HEMIUSDT | flat | +$44.19 | +11.0% | 3.2 | 114 | -$3.64 | take-profit |
| APRUSDT | flat | +$43.24 | +10.8% | 15.1 | 393 | -$55.39 | take-profit |
| BEATUSDT | flat | +$41.08 | +10.3% | 7.8 | 152 | -$8.03 | take-profit |
| HUSDT | flat | +$40.89 | +10.2% | 17.9 | 189 | -$7.98 | take-profit |
| AKEUSDT | trend_long | +$42.60 | +10.6% | 1.0 | 67 | -$7.00 | take-profit |
| VELVETUSDT | flat | +$40.50 | +10.1% | 18.8 | 119 | -$72.68 | take-profit |
| LINKUSDT | flat | +$21.99 | +5.5% | 30.0 | 330 | -$8.28 | periodo completo |
| BNBUSDT | flat | +$16.79 | +4.2% | 30.0 | 408 | -$4.76 | periodo completo |
| BTCUSDT | flat | +$11.75 | +2.9% | 30.0 | 511 | -$6.31 | periodo completo |
| ZECUSDT | flat | +$7.09 | +1.8% | 30.0 | 138 | -$22.70 | periodo completo |
| XRPUSDT | flat | -$22.19 | -5.5% | 30.0 | 201 | -$29.91 | periodo completo |
| COWUSDT | flat | -$33.12 | -8.3% | 30.0 | 335 | -$77.61 | periodo completo |
| **AIOUSDT** | flat | **-$117.16** | **-29.3%** | 30.0 | 287 | **-$245.50** | periodo completo |
| ETHUSDT | — | — | — | — | — | — | no califico (leve) |
| SOLUSDT | — | — | — | — | — | — | no califico (leve) |
| HYPEUSDT | — | — | — | — | — | — | no califico (leve) |
| TUTUSDT | — | — | — | — | — | — | no califico (leve) |
| ACEUSDT | — | — | — | — | — | — | tendencia bajista, no soportado |
| WALUSDT | — | — | — | — | — | — | tendencia bajista, no soportado |

### Agregado

- 14/20 calificaron (13 flat, 1 trend_long); 4 no calificaron (leve), 2
  quedaron fuera por ser bajistas (v1 no soporta short)
- 11/14 positivos (78.6%)
- PnL total: **+$252.13**
- Capital de referencia si las 14 corrian simultaneas: $5,600 -> ROI
  blended ~**4.5% en ~30 dias**
- 7 de 14 tocaron take-profit antes de los 30 dias (HEMIUSDT en solo
  3.2 dias)

### El caso AIOUSDT (por que importa)

Se clasifico "rango" el 17 de julio mirando los 14 dias previos
(tranquilos, ~$0.09-0.12). Despues se desplomo a ~$0.033 (-70%) a fines
de julio - exactamente el riesgo de "arrollamiento": el regimen cambio
DESPUES de la clasificacion, y el grid estatico siguio comprando en la
caida sin poder vender nunca. Es el unico con drawdown desproporcionado
(-$245 vs el resto entre -$4 y -$78) - el riesgo real esta concentrado
en pocos casos de ruptura fuerte, no repartido parejo.

## Limitaciones conocidas (dichas explicitamente, no escondidas)

- Una sola ventana de tiempo (T0 = 17 jul 2026). No se repitio con otro
  T0 todavia para ver si el patron se sostiene - proximo paso logico
  antes de confiar mas en el numero.
- Trend grid v1 es long-only (2 candidatos bajistas quedaron afuera).
- El reanclaje diario del trend grid no migra posiciones entre dias
  (ver seccion del modelo arriba).
- Fills a nivel de vela (horaria), no tick-a-tick - conservador por
  diseno (ver "orden de fills" arriba).
- Universo = top 20 por liquidez DE HOY, no "los 20 mas liquidos hace
  30 dias" (ese listado historico no esta disponible facilmente) -
  sesgo de supervivencia leve: solo entran simbolos que siguen siendo
  liquidos ahora.
- Sin stop-loss todavia, solo take-profit - el caso AIOUSDT muestra que
  hace falta uno para acotar el peor escenario, no solo capturar el
  mejor.

## Como correr esto de nuevo

```bash
# Walk-forward completo (clasifica + asigna + simula con take-profit)
python3 scripts/backtest_grid_walkforward.py --lookback-days 30 --forward-days 30 --universe-size 20 --usd-per-level 20 --take-profit-pct 10

# Backtest ad-hoc de un solo simbolo/estrategia (sin walk-forward)
python3 scripts/backtest_grid.py flat HYPEUSDT --days 14 --usd-per-level 20
python3 scripts/backtest_grid.py trend HUSDT --days 14 --usd-per-level 20
```

## Estado y proximos pasos

- **Estado actual: solo backtest, nada corriendo en paper ni en real.**
- Pendiente: repetir el walk-forward con otro T0 (ej. hace 60 dias)
  para ver si +4.5%/30d se sostiene o si julio fue un mes particular.
- Pendiente: agregar stop-loss al motor (hoy solo hay take-profit).
- Pendiente: decidir si pasar a paper trading con esta metodologia una
  vez repetida la validacion.
- Pendiente (si se quiere soporte para tendencias bajistas): motor de
  fills espejado (short) en `grid_math.py`.
