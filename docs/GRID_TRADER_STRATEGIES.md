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

## Repitiendo la ventana: mas simbolos, otro mes

El resultado de arriba (n=14, top 20) era una muestra chica. Se repitio
con top 100 y con un mes distinto (junio) para ver si +4.5%/30d se
sostiene:

| Ventana | Universo | Calificaron | Positivos | PnL | ROI blended |
|---|---|---|---|---|---|
| Jul 17 - Ago 16 | top 20 | 14 | 78.6% | +$252.13 | +4.5% |
| Jul 1 - Jul 31 | top 20 | 7 | 57.1% | +$37.77 | +1.35% |
| **Jul 1 - Jul 31** | **top 100** | **34** | **76.5%** | **+$611.68** | **+4.5%** |
| **Jun 1 - Jun 30** | **top 100** | **57** | **50.9%** | **-$287.83** | **-1.26%** |

Con muestra chica (n=7) julio parecia mucho mas debil de lo que en
realidad fue - con n=34 converge al mismo +4.5% que la otra ventana de
julio. Junio, en cambio, es un resultado genuinamente distinto: 50.9%
win rate (una moneda al aire) y **9 de 57 posiciones con drawdown peor
a -$100** (vs. solo 1 en julio).

**Por que junio fue distinto (verificado con datos reales, no
especulacion)**: BTC cayo de ~$73,600 (30 mayo) a ~$58,600 (30 junio),
-20% en un mes, con -17% concentrado solo en la primera semana. En una
correccion asi la mayoria de las altcoins caen correlacionadas con
BTC - no son 9 rupturas de rango independientes, es un solo evento
sistemico arrastrando casi todo a la vez. Confirma el riesgo de
"arrollamiento" ya visto en AIOUSDT, ahora a escala de portfolio.

## Stop-loss + gate de regimen de BTC

Agregados 2026-08-16 a pedido del dueno tras el resultado de junio:
"cuando BTC este lateral o subiendo algo debe implicar en nuestras
entradas".

**Gate de BTC** (`atlantis/grid_trader/market_gate.py::btc_market_ok`,
activo por defecto): antes de abrir CUALQUIER posicion nueva (flat o
trend), se chequea el regimen de BTC. Si esta en tendencia bajista
fuerte (mismo Efficiency Ratio + `net_move_pct` que se usa para
clasificar cualquier otro par), no se abren posiciones nuevas ese
periodo. Para flat (una sola entrada en T0) esto solo protege si la
baja YA es visible en T0 - si la baja empieza DESPUES de entrar (como
junio, donde BTC todavia estaba "leve" el 1 de junio), no ayuda. Para
trend (reanclaje diario) SI ayuda mid-periodo: el candidato HUSDT de
junio paso de **-$122.06 (drawdown -$215.41)** sin gate a **+$64.25**
con gate, porque bloqueo nuevas entradas una vez que la baja de BTC se
hizo visible a mitad de mes.

**Stop-loss** (`grid_math.py::simulate_grid`, parametro
`stop_loss_usd`, simetrico al take-profit): stated plainly, con datos
de velas horarias (no tick-a-tick) el stop-loss puede "saltarse" el
umbral exacto en un movimiento muy violento dentro de una sola hora
(ZECUSDT en junio: stop configurado en -$80, cerro en -$94.74) - es una
limitacion real del nivel de datos, no un bug.

Barrido de umbrales, gate de BTC siempre activo, contra jun/jul reales:

| Config | Junio ROI | Julio ROI | Notas |
|---|---|---|---|
| Sin stop-loss (solo gate) | -1.44% | ~+4.5% | gate no bloqueo nada en julio (BTC no estuvo bajista) |
| Stop-loss 20% | -0.84% | +2.55% | corta VELVETUSDT (-18.2% DD, iba a cerrar en +10.1%) y AKEUSDT (iba a cerrar en +$43.17, corta en -$110.29) |
| **Stop-loss 35%** | **-0.49%** | **+2.96%** | solo corta casos verdaderamente extremos (AKEUSDT, SPORTFUNUSDT) - 1/55 y 1/33 posiciones |

**Decision (confirmada por el dueno 2026-08-16): gate de BTC siempre
activo + stop-loss 35%** (default nuevo de
`scripts/backtest_grid_walkforward.py`). El gate es una mejora limpia,
sin costo real (no bloquea nada cuando no hace falta). El stop-loss
sigue siendo un trade-off incluso a 35% - corta algo de upside en meses
buenos a cambio de acotar el peor escenario en meses malos - pero un
umbral mas ajustado (20%) cortaba recuperaciones reales sin mejorar
junio mucho mas que esto. Ninguna combinacion prueba junio positivo:
el gate+stop-loss reduce el dano, no lo elimina - correr muchos grids
"planos" simultaneos durante una correccion de BTC sigue siendo un
riesgo real, solo mas chico que sin proteccion.

## Limitaciones conocidas (dichas explicitamente, no escondidas)

- Solo 2 meses calendario probados con muestra grande (jun, jul 2026).
  No se sabe si generaliza a mas meses/regimenes de mercado.
- Trend grid v1 es long-only (candidatos bajistas quedan afuera - en
  junio esto excluyo 2-5 simbolos segun la ventana).
- El reanclaje diario del trend grid no migra posiciones entre dias
  (ver seccion del modelo arriba).
- Fills a nivel de vela (horaria), no tick-a-tick - el stop-loss puede
  sobrepasar su umbral en una vela muy violenta (ver seccion de arriba).
- Universo = top N por liquidez DE HOY, no "los N mas liquidos hace 30
  dias" - sesgo de supervivencia leve.
- El gate de BTC solo protege entradas NUEVAS, no posiciones ya
  abiertas antes de que la baja se vuelva visible - para eso esta el
  stop-loss, que tiene su propio limite (arriba).

## Como correr esto de nuevo

```bash
# Walk-forward completo, config final (gate activo + stop-loss 35% son default)
python3 scripts/backtest_grid_walkforward.py --lookback-days 30 --forward-days 30 --universe-size 100 --usd-per-level 20 --take-profit-pct 10

# Desactivar el stop-loss o el gate para comparar
python3 scripts/backtest_grid_walkforward.py --lookback-days 30 --forward-days 30 --universe-size 100 --stop-loss-pct '' --no-btc-gate

# Backtest ad-hoc de un solo simbolo/estrategia (sin walk-forward, sin gate/stop-loss)
python3 scripts/backtest_grid.py flat HYPEUSDT --days 14 --usd-per-level 20
python3 scripts/backtest_grid.py trend HUSDT --days 14 --usd-per-level 20
```

## Estado y proximos pasos

- **Estado actual: solo backtest, nada corriendo en paper ni en real.**
- Confirmado 2026-08-16: gate de BTC + stop-loss 35% como configuracion
  base para lo que sigue.
- Pendiente: probar mas meses/ventanas para tener mas confianza que
  jun/jul.
- Pendiente: decidir si pasar a paper trading con esta configuracion.
- Pendiente (si se quiere soporte para tendencias bajistas): motor de
  fills espejado (short) en `grid_math.py`.
- Pendiente (opcional, no bloqueante): aplicar el stop-loss/gate
  tambien a `scripts/backtest_grid.py` (el backtest ad-hoc de un solo
  simbolo todavia no los tiene, solo el walk-forward).
