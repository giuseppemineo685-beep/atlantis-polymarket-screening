# ATLANTIS Watchlist Analysis

Este documento define como analizamos las wallets aprobadas.

## Fuente

ATLANTIS lee:

`inputs/approved_wallets.csv`

Columnas:

- `status`: `approved`, `paper_only`, `paused`, `rejected`.
- `label`: nombre humano.
- `wallet`: address.
- `vertical`: sports, crypto, macro, politics, mixed.
- `notes`: criterio manual.

## Que Analiza El Sistema

Para cada wallet:

- trades descargados.
- mercados distintos.
- volumen total estimado.
- trades deportivos.
- mercados deportivos.
- volumen deportivo.
- actividad reciente en 14 dias.
- posiciones activas.
- posiciones activas deportivas.
- proporción buy/sell.
- notas de riesgo.
- verdict operativo.

## Verdicts

- `WATCHLIST_STRONG`: candidata fuerte para señales.
- `WATCHLIST`: candidata usable con restricciones.
- `PAPER_ONLY`: observar o simular.
- `REJECT`: no usar para señales.

## Comando

```bash
python3 -B -m atlantis.cli evaluate-watchlist \
  --wallets-csv inputs/approved_wallets.csv \
  --csv outputs/watchlist_evaluation.csv
```

## Como Interpretar

No copiamos una wallet porque tenga buen verdict.

Regla operativa:

- una wallet fuerte puede generar `WAIT`.
- dos o mas wallets fuertes coincidiendo pueden generar `COPY`.
- si hay conflicto entre wallets fuertes, reducir o ignorar.
- si el mercado tiene poca liquidez, ignorar.
- si el precio esta extremo, ignorar.

## Revision Diaria

Diariamente:

1. actualizar `inputs/approved_wallets.csv` si agregaste wallets nuevas.
2. correr `evaluate-watchlist`.
3. revisar wallets que bajan de calidad.
4. correr `active-portfolio`.
5. guardar outputs del dia.

Comandos:

```bash
python3 -B -m atlantis.cli evaluate-watchlist \
  --wallets-csv inputs/approved_wallets.csv \
  --csv outputs/watchlist_evaluation.csv

python3 -B -m atlantis.cli active-portfolio \
  --traders-csv outputs/sports_traders.csv \
  --bankroll 1000 \
  --csv outputs/active_sports_portfolio.csv
```
