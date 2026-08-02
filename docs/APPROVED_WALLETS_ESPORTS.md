# ATLANTIS — Esports — Approved Wallets

Lista humana de wallets aprobadas para la vertical "Esports"
(LoL, CS2, Valorant, Dota 2). Espejo de `docs/APPROVED_WALLETS.md`, pero
completamente separada — nunca se mezcla con el roster de deportes ni el
de Elon.

Fase actual: **paper trading** (señales de consenso en papel, notificadas
por Telegram con el prefijo "PAPER TRADE · ESPORTS"). Todas las wallets
están en `paper_only` — nada aprobado para ejecución con dinero real
todavía, y el script que corre esta vertical
(`scripts/run_screening_and_notify_esports.py`) no tiene ningún hook hacia
la cola de órdenes reales que usa la vertical de deportes.

Descubrimiento: a diferencia de deportes/Elon (que escanean el leaderboard
general de PnL), los candidatos de esports salen de mirar quién trada
directamente los mercados de esports más grandes (`discover-esports-traders`)
- encuentra especialistas aunque no estén top-N en volumen total de cuenta.

Las wallets de abajo pasaron `detect-bot-wallet` (heurístico de frecuencia)
Y una auditoría manual de **PnL neto real** (realizado + no-realizado de
posiciones abiertas ahora mismo, no solo el histórico de cerradas — varias
wallets con PnL realizado enorme resultaron estar sentadas sobre pérdidas no
realizadas igual de grandes, dejándolas en neto negativo). Empezó en 37
candidatas; se sacaron 16 con `net_esports < $15K` o negativo (quedaron 21),
y el 2026-08-02 se sacaron 5 más a pedido directo del dueño (467j6yj,
Gengfrauds, swy01, LOBODAFL, asuka7 — sin relación al PnL, decisión
manual). Quedan 16. Si alguna tenía una posición abierta en
`outputs/trade_log_esports.csv` al momento de sacarla (fue el caso de
swy01), esa posición se deja correr hasta que cierre sola — sacarla del
roster solo afecta señales nuevas.
`Esports_2c3350` es la misma wallet que `Trader03` en el roster de
deportes (whale/fund, ya documentado ahí con la nota de no copiar 1:1).

## Estados

- `approved`: entra al análisis (incluida ejecución real, si algún día se
  habilita para esta vertical).
- `paper_only`: solo señales en papel — estado de todas las wallets acá.
- `paused`: no analizar temporalmente.
- `rejected`: conservar histórico, pero no usar para señales.

## Wallets

| # | status | label | wallet | vertical | notes |
|---|---|---|---|---|---|
| 1 | paper_only | igot50k | `0x60ec17443af511a21945f01430e61c803465f7b0` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 35.0), 7 eventos de 20 escaneados, copy_verdict=A; net_esports=$160,585 (realizado+no-realizado) |
| 2 | paper_only | kutsumiakia | `0xc3e550fae1c90b71675f3355e5864c240bea519d` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 15.0), 7 eventos de 20 escaneados, copy_verdict=A; net_esports=$89,057 (realizado+no-realizado) |
| 3 | paper_only | bogemika | `0x0ee52436cfd4c726385fec293d48d99d3b44e798` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 6 eventos de 20 escaneados, copy_verdict=A; net_esports=$53,804 (realizado+no-realizado) |
| 4 | paper_only | bvrs | `0x04f661add03f080541db83c0f72eb28c8351f24a` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 35.0), 4 eventos de 20 escaneados, copy_verdict=B; net_esports=$18,700 (realizado+no-realizado) |
| 5 | paper_only | 0x60DD7009460D0bA0DF41840E5 | `0x60dd7009460d0bc6949ea6429fefba0df41840e5` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 3 eventos de 20 escaneados, copy_verdict=A; net_esports=$71,345 (realizado+no-realizado) |
| 6 | paper_only | gxxyy | `0x4aec70021891ea712aaf3e2dd76c30f6b09a4ce9` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 35.0), 3 eventos de 20 escaneados, copy_verdict=A; net_esports=$122,646 (realizado+no-realizado) |
| 7 | paper_only | Esports_0b5a14 | `0x0b5a14b1c2f547cfd238b1123d228562f5721457` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 3 eventos de 20 escaneados, copy_verdict=A; net_esports=$78,077 (realizado+no-realizado) |
| 8 | paper_only | howtoplaydota | `0xcf0aca0d7a395202aec661c3666be9cc098e320a` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 20.0), 3 eventos de 20 escaneados, copy_verdict=B; net_esports=$127,657 (realizado+no-realizado) |
| 9 | paper_only | entropytime | `0xc4ac7dee4d55d1698e88a8160d5c61ede96b9722` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 2 eventos de 20 escaneados, copy_verdict=A; net_esports=$26,196 (realizado+no-realizado) |
| 10 | paper_only | canoflanagan | `0x21468ad63a833f5f9ea5c2835fb4e9dec57ad41b` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 2 eventos de 20 escaneados, copy_verdict=A; net_esports=$33,885 (realizado+no-realizado) |
| 11 | paper_only | Esports_2c3350 | `0x2c335066fe58fe9237c3d3dc7b275c2a034a0563` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 15.0), 2 eventos de 20 escaneados, copy_verdict=A; net_esports=$2,829,000 (realizado+no-realizado) |
| 12 | paper_only | Esports_bfab15 | `0xbfab152afa43741b41be0f355f862be2630cf067` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 35.0), 2 eventos de 20 escaneados, copy_verdict=A; net_esports=$19,433 (realizado+no-realizado) |
| 13 | paper_only | fkgggg2mouzfuria | `0x52ecea7b3159f09db589e4f4ee64872fd0bba6f3` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 2 eventos de 20 escaneados, copy_verdict=A; net_esports=$3,117,801 (realizado+no-realizado) |
| 14 | paper_only | yep.itsme | `0xad2f5bf835fee93d6e7403a274cdc40f7a2dac6a` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 2 eventos de 20 escaneados, copy_verdict=A; net_esports=$18,022 (realizado+no-realizado) |
| 15 | paper_only | Esports_1610db | `0x1610db79f753a80207e1d66716be9e91e627ae49` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 2 eventos de 20 escaneados, copy_verdict=B; net_esports=$332,493 (realizado+no-realizado) |
| 16 | paper_only | NeverLosePatience | `0x997329ff3e5ed79b12e845a2f036e34aa152bd7f` | esports | discover-esports-traders + detect-bot-wallet (LIKELY_HUMAN_OR_LOW_SIGNAL, score 0.0), 2 eventos de 20 escaneados, copy_verdict=B; net_esports=$17,224 (realizado+no-realizado) |

## Fuente ejecutable

El pipeline lee `inputs/approved_wallets_esports.csv` directamente — esta
tabla es solo la versión legible para humanos, generada a partir de ese CSV.
