# ATLANTIS

Sistema de copy-trading para Polymarket: vigila un grupo de wallets curadas
manualmente, detecta cuándo 2+ de ellas coinciden en la misma apuesta
("consenso"), y **desde el 28 de julio de 2026 ejecuta órdenes reales** con la
cuenta de Polymarket del dueño del proyecto ($25 por señal desde el
2026-08-01 (antes $20), capital asignado $500). El screening en papel sigue
corriendo en paralelo para siempre, como comparación.

**El kill switch automático por pérdida acumulada está DESACTIVADO desde el
2026-08-01, a pedido explícito del dueño** (ver sección "Kill switch" más
abajo) — el trading real ya no se frena solo sin importar cuánto pierda.

Además del vertical original de deportes, existe un segundo vertical
**Esports (LoL/CS2/Valorant/Dota2)** corriendo en paralelo, **100% en papel,
sin ninguna conexión al dinero real** — ver sección propia más abajo.

## Dónde vive cada cosa

| Componente | Ubicación |
|---|---|
| Código | este repo (GitHub, público): `github.com/giuseppemineo685-beep/atlantis-polymarket-screening` |
| Screening en papel (24/7) | VPS Alemania, Hetzner Nuremberg (`178.105.143.153`), vía `crontab -l` |
| **Ejecución real (24/7)** | VPS Finlandia, Hetzner Helsinki (`46.62.140.62`), vía `crontab -l` |
| Dashboard (pestaña Deportes: solo trades reales; pestaña Esports: 100% papel) | GitHub Pages: https://giuseppemineo685-beep.github.io/atlantis-polymarket-screening/ |
| Notificaciones | Telegram (bot propio) |
| Wallets aprobadas/rechazadas | `inputs/approved_wallets.csv` + `docs/APPROVED_WALLETS.md` |
| Cuenta de Polymarket usada para ejecutar | `0x25f745698cce689188fbfba7b8614981c028680b` (usuario "Swissman", la cuenta principal del dueño) |

**Ambos VPS son la fuente de verdad**, sincronizados solo a través de este
repo de git (cada uno hace `git pull` → trabaja → `git push` cada 2 min). No
hay ninguna otra conexión directa entre los dos servidores.

### Por qué dos servidores en dos países

Polymarket **bloquea geográficamente la colocación de órdenes** (no la
lectura de datos) desde varios países, incluida Alemania, EE.UU., Francia,
Reino Unido, Países Bajos, entre otros — lista completa en
`https://docs.polymarket.com/developers/CLOB/geoblock`. El screening (solo
lee datos públicos) puede seguir en Alemania sin problema, pero la ejecución
de órdenes reales necesitaba un servidor en un país no bloqueado — se eligió
Finlandia (única ubicación de Hetzner fuera de la lista).

## Cómo funciona el screening (papel)

1. **Ciclo rápido (cada 2 min, VPS Alemania)**: lee las posiciones activas de
   las wallets aprobadas, agrupa por mercado, si 2+ coinciden en el mismo
   resultado → señal `COPY`. Se registra en `outputs/trade_log.csv` (nunca se
   borra, solo se marca `OPEN`/`WIN`/`LOSS`/`CLOSED`). Manda Telegram solo
   para señales *nuevas*.
2. **Ciclo lento (cada 2h, VPS Alemania)**: re-evalúa cada wallet (detección
   de bot, volumen, actividad reciente) y recalcula
   `outputs/trader_performance.csv` (PnL y win-rate en ventanas móviles de
   7d/30d, para detectar traders en declive).
3. **Cierre de posiciones**: cuando **un solo trader sale** de una posición
   que tenía → se cierra (`CLOSED`, "salida temprana"), sea ganancia o
   pérdida en ese momento (el trader saliendo ES la señal, no nuestro
   PnL no-realizado).

## Cómo funciona la ejecución real (dinero real)

Dos procesos separados, conectados solo por archivos sincronizados vía git —
así un bug en la ejecución real nunca puede tocar el screening gratis, y
viceversa:

1. **`scripts/run_screening_and_notify.py`** (Alemania) tiene dos "hooks": al
   crear una fila nueva en `trade_log.csv` → encola un intent `BUY` en
   `state/live_intents_queue.jsonl`. Al marcar una fila `CLOSED` (salida
   temprana) → encola un intent `SELL`. Estos hooks están envueltos en
   `try/except` con el `import` adentro del `try`, para que ni un bug ahí
   pueda tirar abajo el screening.
2. **`scripts/run_live_execution.py`** (Finlandia, cron separado cada 2 min,
   su propio `flock`) lee esa cola y:
   - Si `state/live_trading_status.json` tiene `enabled: false` → modo
     dry-run, escribe en `outputs/live_trade_log_dryrun.csv`, no llama a
     Polymarket para nada real.
   - Si `enabled: true` → coloca la orden real (`atlantis/polymarket/clob_client.py`,
     usa `py-clob-client-v2`, **no** la versión vieja `py-clob-client`, que
     está deprecada y sus órdenes son rechazadas por el exchange) y escribe
     en `outputs/live_trade_log.csv`.
3. **Kill switch** (`atlantis/live/kill_switch.py`) — **DESACTIVADO desde el
   2026-08-01, a pedido explícito del dueño** ("no used kill switch de ahora
   en adelante" → confirmó explícitamente "Desactivar el auto-stop por
   completo"): el trading real ya **no se frena solo sin importar cuánto
   pierda**. `evaluate_and_maybe_trip()` ya no calcula ni compara la pérdida
   acumulada contra ningún umbral — solo lee si el switch está *ya* disparado
   a mano (`state/live_trading_status.json`), nunca lo dispara él mismo.
   Antes de este cambio, el switch se auto-disparaba a `kill_switch_loss_pct`%
   de pérdida acumulada; ese código fue removido por completo, no solo
   deshabilitado por config, así que no hay ningún flag que lo reactive por
   accidente.

   Si en algún momento se quisiera volver a tener un freno automático, hay
   que **reescribir la lógica de umbral en `kill_switch.py`** (no solo
   cambiar un booleano) y, para reactivar el switch después de un disparo
   manual, resetear `pnl_baseline_usd` al valor actual de `realized_pnl_usd`
   en `state/live_trading_status.json` (poner solo `enabled: true` no
   alcanza, porque `realized_pnl_since_reset_usd` seguiría siendo histórico).
4. **Notificaciones Telegram distintas**: 🟢 "COMPRA REAL ejecutada" / 🟡
   "VENTA REAL ejecutada" / 🔴 fallidas — nunca se confunden con las alertas
   normales de señal de papel.

### Credenciales de la ejecución real (viven SOLO en el VPS de Finlandia)

- `/root/.atlantis_secrets/polymarket_private_key` — clave privada exportada
  de Polymarket (cuenta tipo "Magic"/email-login), permisos `600`. **Nunca
  pasó por ningún chat ni por git** — si hay que regenerarla, el dueño la
  re-exporta desde polymarket.com → Configuración → Export Private Key, y la
  carga él mismo por SSH directo, nunca pegándola en un chat.
- `/root/atlantis-polymarket-screening/.env.live` (no está en git) — tiene
  `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_PRIVATE_KEY_PATH`,
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. `run_live_cron.sh` lo carga si
  existe.
- `signature_type=1` porque es una cuenta Magic/proxy-wallet (login por
  email) — la clave privada exportada controla una EOA distinta de la
  dirección pública/funder que tiene los fondos.

### Comandos de la ejecución real

```bash
# En el VPS de Finlandia, con las credenciales cargadas:
cd /root/atlantis-polymarket-screening && source .env.live

# Estado: kill switch, PnL real, posiciones abiertas/ganadas-sin-redimir
python3 -B -m atlantis.cli live-status

# Confirmar que las credenciales conectan bien (solo lectura)
python3 -B -m atlantis.cli check-balance

# Orden de prueba manual chiquita (nunca la corre el cron)
python3 -B -m atlantis.cli place-test-order --token-id <id> --side BUY --amount 2 --confirm

# Prender/apagar el trading real (editar a mano):
#   state/live_trading_status.json -> {"enabled": true/false, ...}
```

### La "brecha de redención" (limitación conocida, no arreglada todavía)

Cuando una posición gana, el token vale $1 pero **hay que reclamarlo con una
transacción on-chain separada** (`redeemPositions`, necesita gas en MATIC) —
no es parte de las órdenes del CLOB. Esto quedó **fuera del alcance inicial**
a propósito (superficie más riesgosa, transacciones irreversibles). Cuando
una posición resuelve GANADA sin haber tenido una venta anticipada, queda
marcada `WON_UNREDEEMED` en `outputs/live_trade_log.csv` y el dueño la
reclama manualmente en polymarket.com (botón "Redeem"). Automatizar esto es
el siguiente paso pendiente más importante.

Cuando el libro de órdenes de un mercado desaparece (porque ya resolvió)
*antes* de que nuestra señal de salida temprana pudiera vender, no hay forma
de vender — el sistema detecta esto (`atlantis/services/live_execution.py::get_market_resolution`)
y marca directamente `WON_UNREDEEMED` o `LOST` según corresponda, en vez de
reintentar para siempre.

## Cómo agregar / sacar un trader

Editar `inputs/approved_wallets.csv` (columna `status`):

- `approved`: entra en señales de consenso (y por lo tanto en ejecución real).
- `paper_only`: se sigue observando pero no cuenta para señales `COPY`.
- `rejected`: fuera de todo. Usar cuando se confirma bot o rendimiento en
  declive sostenido (ver `outputs/trader_performance.csv`, columna `flag`:
  `DECLINING` = PnL negativo últimos 7 días con ≥5 resueltas).

Reflejar el mismo cambio en `docs/APPROVED_WALLETS.md`. Después de cambiar
el CSV, correr en el VPS de Alemania:

```bash
cd /root/atlantis-polymarket-screening
./run_cron.sh slow   # recalcula portfolio_traders.csv con el cambio
```

Un bot confirmado manualmente (no solo por score automático) va además al
denylist fijo en `atlantis/services/evaluate_wallet.py`
(`CONFIRMED_BOT_WALLETS`) — el `bot_score` automático es ruidoso entre
corridas (varía según qué trades muestrea), así que una wallet puede bajar
momentáneamente del umbral y colarse de nuevo si solo se confía en el score.

## Gotchas ya encontrados (para no repetirlos)

- **`/closed-positions` de Polymarket rechaza `limit > 50`** (antes lo capeaba
  en silencio a 50, ahora tira 400). Ver `atlantis/polymarket/client.py`.
- **Sesgo de pérdidas "fantasma"**: una posición que resuelve en $0 a veces
  nunca se "redime" on-chain, así que nunca aparece en `/closed-positions`.
  `active-portfolio` sí las detecta vía `/positions` con `curPrice=0`.
- **GitHub Pages necesita `docs/.nojekyll`** — sin eso, pushes cada 2 min
  saturan la cola de build clásica de Jekyll y el dashboard queda atascado.
- **Timeouts de red crudos** (`TimeoutError`) no son lo mismo que
  `urllib.error.URLError` — hay que capturar ambos o un timeout a mitad de
  descarga tira abajo todo el pipeline sin reintentar.
- **`.gitignore` con `outputs/* + excepciones por archivo`**: cualquier CSV
  nuevo hay que sumarlo explícitamente a la lista de excepciones, si no
  `git add` lo ignora en silencio y nunca se commitea.
- **`git add archivo1 archivo2 archivo3` falla completo si UNO no existe**
  (ej. `live_trade_log.csv` antes de la primera ejecución real) — el resto
  tampoco se sube, sin error visible si además hay `2>/dev/null || true`.
  Agregar archivos de a uno, chequeando `[ -f "$archivo" ]` primero.
- **`py-clob-client` (el paquete viejo, sin `-v2`) está deprecado** — sus
  órdenes son rechazadas por el exchange ("invalid order version"). Usar
  siempre `py-clob-client-v2`.
- **Las órdenes de mercado (`MarketOrderArgs`) pueden generar un precio
  efectivo con más decimales de los que el tick size permite** (el exchange
  deriva maker/taker amount de monto÷precio). Usamos órdenes límite `FOK`
  en su lugar, calculando nosotros mismos un `size` que sea múltiplo de
  `1/gcd(precio_en_centavos, 100)` para que `size × precio` dé un monto con
  ≤2 decimales (regla real del exchange para compras "marketable").
- **El libro de órdenes de un mercado desaparece apenas resuelve** (a veces
  incluso un poco antes, cuando el precio ya está en ~0 o ~1 y no queda
  nadie operando) — una venta real ahí falla con 404 "No orderbook exists".
  Hay que chequear la resolución del mercado antes de intentar vender.
- **CRÍTICO (2026-08-01) — un fallo de parseo de la respuesta del exchange
  puede disfrazarse de orden fallida y disparar reintentos que SÍ ejecutan
  dinero real cada vez.** `_parse_order_response` (`clob_client.py`) hacía
  `Decimal(str(makingAmount))` sin capturar `InvalidOperation`; cuando el
  exchange confirmaba `success=True` pero devolvía esos campos vacíos, la
  excepción escapaba hasta el `except Exception` de `_place_fok_order`, que
  reportaba la orden como fallida (`success=False`). La lógica de reintento
  (correcta para una orden que de verdad nunca se ejecutó) la reintentaba
  cada 2 minutos — y cada reintento **volvía a ejecutar de verdad**, porque
  la orden sí se estaba llenando. Una señal de $25 llegó a $102+ en una sola
  posición (Jaime Faria, Canadian Open) antes de que se detectara. Fix: el
  parseo de `makingAmount`/`takingAmount` nunca puede degradar
  `success=True` a `False` — un fallo de parseo se reporta como error
  informativo aparte, dejando `success` intacto. Regla general: `response.get("success")`
  del exchange es la única fuente de verdad sobre si el dinero se movió;
  ningún post-procesamiento downstream debe poder invertir ese valor.
- **`SPORT_TERMS` (`atlantis/services/sports_traders.py`) no incluía
  "atp"/"wta"** — los títulos reales de mercados de tenis en Polymarket
  nunca dicen literalmente "tennis" (ej. `"Wimbledon ATP: Jannik Sinner vs
  Novak Djokovic"`), así que `is_sports_trade` clasificaba **todo el tenis**
  como no-deportivo en toda la ruta de dinero real (`active_portfolio.py`,
  `consensus_backtest.py`, `evaluate_wallet.py`). Se detectó por
  observación directa del dueño sobre una wallet candidata ("tiene decenas
  de posiciones abiertas y todas son tennis etc"), no por ningún chequeo
  automático — recordatorio de que un veredicto automático puede estar
  sistemáticamente mal para una categoría entera, y que la observación
  directa de la cuenta real es la señal a confiar cuando contradice al
  sistema.

## Comandos útiles (papel)

```bash
# Evaluar wallets (bot detection, volumen, verdict)
python3 -B -m atlantis.cli evaluate-watchlist \
  --wallets-csv inputs/approved_wallets.csv --csv outputs/watchlist_evaluation.csv

# Rendimiento por trader (7d/30d), para decidir a quién sacar
python3 -B -m atlantis.cli trader-performance \
  --statuses approved,paper_only --csv outputs/trader_performance.csv

# Backtest: si hubieras copiado el consenso, ¿ganabas?
python3 -B -m atlantis.cli consensus-backtest \
  --statuses approved --since-days 30 --csv outputs/consensus_backtest.csv

# Señales activas ahora mismo
python3 -B -m atlantis.cli active-portfolio \
  --traders-csv outputs/portfolio_traders.csv --min-verdict B --csv outputs/active_portfolio_signals.csv
```

## Vertical Esports (LoL / CS2 / Valorant / Dota2) — 100% papel

Segundo vertical, corriendo en paralelo al de deportes, **sin ninguna
conexión al dinero real** (decisión de seguridad explícita, no un detalle
pendiente):

- Roster propio: `inputs/approved_wallets_esports.csv` +
  `docs/APPROVED_WALLETS_ESPORTS.md` — 21 wallets (reducido de 37 el
  2026-08-01 tras una auditoría de PnL neto realizado+no-realizado, cutoff
  `net_esports >= $15K`).
- Dashboard: pestaña "Esports" propia, con su propio scoreboard/"Rendimiento"
  (mismo formato que la sección de deportes, sin win-rate) y su propia
  sección de historial de trades — todo en `outputs/*_esports.csv`
  independientes de los de deportes.
- La categorización (`is_esports_trade`) y el resto del pipeline de
  screening en papel son un espejo del de deportes, corriendo también en el
  VPS de Alemania. **No existe ningún hook de `enqueue_intent`/`live_intents`
  en el código de esports** — verificar con
  `grep -r "live_intents" scripts/run_screening_and_notify_esports.py` (debe
  dar vacío) antes de tocar cualquier cosa de este vertical.
- Si algún día se decide llevar esports a dinero real, es un cambio
  deliberado y explícito (nuevo roster `approved`, nuevo hook), no algo que
  deba pasar por accidente.

## Pendientes conocidos

- Automatizar la redención on-chain (`redeemPositions`) para las posiciones
  `WON_UNREDEEMED` — hoy es 100% manual en polymarket.com.
- Sin tope de posiciones concurrentes ni de gasto diario (se decidió
  explícitamente no agregarlo al activar el trading real - revisar si sigue
  siendo la decisión correcta con más datos reales).
- `state/live_intents_queue.jsonl` es append-only y crece para siempre - cada
  ciclo relee todo el historial (ineficiente pero no incorrecto, ya que hay
  deduplicación). Podría truncarse/archivarse periódicamente.
- Batch de wallets candidatas a sports (dinero real) evaluadas parcialmente,
  pendientes de decisión final antes de insertarlas en
  `inputs/approved_wallets.csv`:
  - `flatbarrel` (`0x6485f47d0344c03eb4340f985159f6eb2dcba265`) — tras el fix
    de `SPORT_TERMS` (atp/wta) el veredicto automático sigue siendo REJECT
    pese a 100% share deportivo ($1.03M volumen); falta decisión final del
    dueño.
  - `HalandinhoNazario` (`0x1f7105a18d9f36aef7c83e5df210e57cf487b2e4`) —
    dirección ya validada (40 caracteres), evaluación todavía pendiente.
  - `zagzig123` — dirección recibida hasta ahora tiene 39 caracteres
    (inválida), falta reenviar la correcta.
  - `mlin1010` (`0xf8371076fb3df0fcfcdb4d9f16bbe98bb2451d42`) — longitud
    correcta pero devuelve 0 trades; probablemente la wallet no es correcta,
    falta confirmar.
  - Instrucción del dueño: mantener cada evaluación en espera y recién al
    final insertar todas juntas en `inputs/approved_wallets.csv` (sports,
    dinero real).
- Brecha de balance real ~$247 sin explicar (2026-08-01): el balance USDC
  real vía `check-balance` ($1,018.84) superaba la estimación derivada del
  trade log ($771.71) incluso después de confirmar que las 22 posiciones
  `WON_UNREDEEMED` ya estaban redimidas. No se pudo explicar con los datos
  disponibles del propio sistema; el dueño decidió seguir sin resolverlo
  ("ok no importa, sigamos") — no re-abrir esta investigación salvo que el
  dueño la mencione de nuevo.
