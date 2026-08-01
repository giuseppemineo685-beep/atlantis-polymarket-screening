# ATLANTIS Approved Wallets

Lista humana de wallets aprobadas para seguimiento y posible copy-trading.

Una wallet aprobada **no significa copiar automaticamente**. Significa que entra al analisis diario.

## Estados

- `approved`: entra al analisis diario.
- `paper_only`: solo simulacion, no live.
- `paused`: no analizar temporalmente.
- `rejected`: conservar historico, pero no usar para senales.

## Wallets

| # | status | label | wallet | vertical | notes |
|---|---|---|---|---|---|
| 1 | approved | Trader01 | `0xdb859a551fcf56e49416160911476bea7307152f` | sports | manual approved |
| 2 | rejected | Trader02 | `0x1610db79f753a80207e1d66716be9e91e627ae49` | sports | LOW_SAMPLE: 0 resueltos en 7d (13 en 30d), sin muestra suficiente para confiar, rejected 2026-08-01 |
| 3 | approved | Trader03 | `0x2c335066fe58fe9237c3d3dc7b275c2a034a0563` | sports | whale/fund - $69M lifetime volume, buy-and-hold (no HFT). Use as directional signal, NOT copy position size 1:1 (scale down heavily). Also has large active losing positions - not infallible. |
| 4 | rejected | Trader04 | `0x85bbb00a84f100a74ec6a479ce6f8f45b04c9ada` | sports | LOW_SAMPLE: 0 resueltos en 7d (14 en 30d), sin muestra suficiente para confiar, rejected 2026-08-01 |
| 5 | approved | Trader05 | `0x6d3c5bd13984b2de47c3a88ddc455309aab3d294` | sports | manual approved |
| 6 | approved | Trader06 | `0xbc3107551d71e0fe3821b4ec4bb2767d313e971f` | sports | manual approved |
| 7 | rejected | Trader07 | `0x9d94f602535e518ee1cb6aade0ca9569f1b1017d` | sports | POSSIBLE_BOT + 0 sports trades in last 30d despite $47.8K active - likely dormant/inactive, review manually |
| 8 | paper_only | RN1_possible_bot | `0x2005d16a84ceefa912d4e380cd32e7ff827875ea` | sports | possible bot, paper only until delay/slippage is tested |
| 9 | rejected | Trader09 | `0xccd81fbd3395dc43a0531f8484b21c2462daf4de` | sports | DECLINING: -10,811 PnL / 81 resueltos en 7d, -30,573 en 30d, rejected 2026-08-01 |
| 10 | approved | Trader10 | `0x5e3040eb55cb0f4f86eb0af40cb84c9d3585acbf` | sports | manual approved |
| 11 | approved | Trader11 | `0xf10299cf1fff507cff45e1a906800e5b44bf1348` | sports | manual approved |
| 12 | rejected | Trader12 | `0xa697d0b3fff7d285a0f92d6ee03a7f97809e59d5` | sports | confirmed bot: 30+ positions opened within 1h, no copy |
| 13 | rejected | Trader13 | `0x84ad9c5c547a82ec9a08547b94bd922446e5bfb7` | sports | confirmed bot (LIKELY_BOT score 90): buy-only + low market diversity, no copy |
| 14 | rejected | Trader14 | `0xa804390f80019699ab34a282c0df7528fba82a75` | sports | declining performance: -18,223 PnL / 33.3% WR over last 7d (57 resolved), rejected 2026-07-25 |
| 15 | approved | Trader15 | `0x4df332e27f9ee3224f52ce30e3ce15c1075e788f` | sports | manual approved |
| 16 | rejected | Trader16 | `0x191f77486cb1c5af42e54734621804936a204a8d` | sports | DECLINING: -3,967 PnL / 74 resueltos en 7d (30d era +58,774, se cayo esta semana), rejected 2026-08-01 |
| 17 | approved | Trader17 | `0x10ff6cd4b1b5669d4ca87faebae0c869ad315088` | sports | manual approved |
| 18 | rejected | Trader18 | `0xa509ae942ac8d8bc3f8c5df30cb6ae2c9b13ae46` | sports | confirmed bot (LIKELY_BOT score 90): buy-only + low market diversity, no copy |
| 19 | rejected | Trader19 | `0xd1a850ad821b49e585edb0af3f2c7e6cb07a6427` | sports | LOW_SAMPLE: 0 resueltos en 7d (36 en 30d), sin muestra suficiente para confiar, rejected 2026-08-01 |
| 20 | approved | Trader20 | `0x60ec17443af511a21945f01430e61c803465f7b0` | sports | manual approved |
| 21 | rejected | Trader21 | `0xe548376f231af12b4d56aff55a7503a88892a9fe` | sports | LOW_SAMPLE: 0 resueltos en 7d (18 en 30d), sin muestra suficiente para confiar, rejected 2026-08-01 |
| 22 | rejected | Trader22 | `0x9db82de5a71ae539bc82f4d9ac3a007c7d742eff` | sports | LOW_SAMPLE: solo 2 resueltos en 7d (25 en 30d), sin muestra suficiente para confiar, rejected 2026-08-01 |
| 23 | approved | Trader23 | `0x224f7ef690d952cf551a471846d5afb4892e514a` | sports | manual approved |
| 24 | rejected | Trader24 | `0x2e3c40fa47b27c676ddd573064162f57d51508ba` | sports | confirmed bot (LIKELY_BOT score 90): buy-only + low market diversity, no copy |
| 25 | rejected | Trader25 | `0x893575c7d99542163c6b6e8a0fe5af0b6d217daa` | sports | confirmed bot (LIKELY_BOT score 90): buy-only + low market diversity, no copy |

## Fuente Ejecutable

El archivo que lee ATLANTIS es:

`inputs/approved_wallets.csv`
