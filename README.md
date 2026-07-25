# ATLANTIS

Sistema de screening de copy-trading para Polymarket: vigila un grupo de wallets
curadas manualmente, detecta cuándo 2+ de ellas coinciden en la misma apuesta
("consenso"), y notifica esa señal. Corre solo, 24/7, en un VPS.

**Estado actual: paper trading / screening.** No ejecuta dinero real. Cero
integración con wallet/API de Polymarket para operar — solo lee datos públicos.

## Dónde vive cada cosa

| Componente | Ubicación |
|---|---|
| Código | este repo (GitHub, público) |
| Ejecución 24/7 | VPS (`178.105.143.153`), vía `crontab -l` |
| Dashboard | GitHub Pages: https://giuseppemineo685-beep.github.io/atlantis-polymarket-screening/ |
| Notificaciones | Telegram (bot propio) |
| Wallets aprobadas/rechazadas | `inputs/approved_wallets.csv` + `docs/APPROVED_WALLETS.md` |

**El VPS es la fuente de verdad de los datos.** Corre `run_cron.sh` (no está en
git — tiene los secrets de Telegram hardcodeados) cada 2 min vía cron, que hace
`git pull` → screening → `git push`. GitHub es solo el canal de sincronización
entre el VPS y el dashboard público, no hace falta tocarlo a mano.

## Cómo funciona el screening

1. **Ciclo rápido (cada 2 min)**: lee las posiciones activas de las wallets
   aprobadas, agrupa por mercado, si 2+ coinciden en el mismo resultado →
   señal `COPY`. Se registra en `outputs/trade_log.csv` (nunca se borra, solo
   se marca `OPEN`/`WIN`/`LOSS`/`CLOSED`). Manda Telegram solo para señales
   *nuevas*.
2. **Ciclo lento (cada 2h)**: re-evalúa cada wallet (detección de bot, volumen,
   actividad reciente) y recalcula `outputs/trader_performance.csv` (PnL y
   win-rate en ventanas móviles de 7d/30d, para detectar traders en declive).
3. **Cierre de posiciones**: cuando **un solo trader sale** de una posición que
   tenía, y en ese momento la posición está en ganancia → se cierra sola
   (`CLOSED`, badge "salida temprana"). Si está en pérdida, solo avisa, no
   fuerza el cierre.

## Cómo agregar / sacar un trader

Editar `inputs/approved_wallets.csv` (columna `status`):

- `approved`: entra en señales de consenso.
- `paper_only`: se sigue observando (aparece en `trader_performance.csv` y en
  la tabla de traders) pero no cuenta para señales `COPY`.
- `rejected`: fuera de todo. Usar cuando se confirma bot o rendimiento en
  declive sostenido (ver `outputs/trader_performance.csv`, columna `flag`:
  `DECLINING` = PnL negativo últimos 7 días con ≥5 resueltas).

Reflejar el mismo cambio en `docs/APPROVED_WALLETS.md` (documentación humana,
no la lee el código). Después de cambiar el CSV, correr en el VPS:

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
  nunca se "redime" on-chain (nadie paga gas para reclamar $0), así que nunca
  aparece en `/closed-positions`. Esto infla el win-rate aparente de cualquier
  wallet. `active-portfolio` sí las detecta vía `/positions` con `curPrice=0`.
- **GitHub Pages necesita `docs/.nojekyll`** — sin eso, pushes cada 2 min
  saturan la cola de build clásica de Jekyll y el dashboard queda atascado
  varios ciclos atrás.
- **Timeouts de red crudos** (`TimeoutError`) no son lo mismo que
  `urllib.error.URLError` — si solo reintentás el segundo, un timeout a mitad
  de descarga tira abajo todo el pipeline sin reintentar.
- **`.gitignore` con `outputs/* + excepciones por archivo`**: cualquier CSV
  nuevo que agregue un comando hay que sumarlo explícitamente a la lista de
  excepciones, si no `git add` lo ignora en silencio y nunca se commitea.

## Comandos útiles

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

## Para pasar a dinero real (no implementado)

- API keys / wallet de ejecución en Polymarket.
- Límite de stake por señal y por día.
- Slippage máximo aceptable.
- Filtro de liquidez/spread mínimo del mercado.
- Kill switch si el sistema empieza a perder de forma sostenida.
- Logs de ejecución real (vs. señal solamente).
