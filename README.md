# ATLANTIS

ATLANTIS es un sistema propio para analizar traders de Polymarket y construir senales de copy-trading.

Estado actual: **analisis y paper signals**. No ejecuta dinero real.

## Estructura

- `atlantis/`: codigo principal.
- `inputs/approved_wallets.csv`: wallets aprobadas para analizar.
- `docs/APPROVED_WALLETS.md`: lista humana de wallets aprobadas.
- `docs/WATCHLIST_ANALYSIS.md`: metodologia de analisis.
- `outputs/`: resultados generados.

## Flujo Diario

1. Agregar wallets aprobadas en:

```text
inputs/approved_wallets.csv
```

2. Evaluar watchlist:

```bash
python3 -B -m atlantis.cli evaluate-watchlist \
  --wallets-csv inputs/approved_wallets.csv \
  --csv outputs/watchlist_evaluation.csv
```

3. Buscar sports traders adicionales:

```bash
python3 -B -m atlantis.cli discover-sports-traders \
  --leaderboard-limit 250 \
  --max-traders 50 \
  --max-trades-per-wallet 1000 \
  --min-sports-trades 25 \
  --min-sports-volume 1000 \
  --csv outputs/sports_traders.csv
```

4. Construir portfolio activo:

```bash
python3 -B -m atlantis.cli active-portfolio \
  --traders-csv outputs/sports_traders.csv \
  --min-verdict B \
  --bankroll 1000 \
  --csv outputs/active_sports_portfolio.csv
```

## Comandos Utiles

Evaluar una wallet individual:

```bash
python3 -B -m atlantis.cli evaluate-wallet 0xdb859a551fcf56e49416160911476bea7307152f
```

Ver top traders generales:

```bash
python3 -B -m atlantis.cli top-traders --time-period DAY --limit 25
```

## Decision

Una wallet aprobada no se copia automaticamente.

Regla operativa actual:

- `WATCHLIST_STRONG`: puede contribuir a senales.
- `WATCHLIST`: usar con confirmacion.
- `PAPER_ONLY`: observar.
- `REJECT`: no usar.

Para pasar a live trading faltan:

- API keys/wallet de Polymarket.
- control de stake.
- slippage maximo.
- filtro de liquidez/spread.
- kill switch.
- logs de ejecucion.
