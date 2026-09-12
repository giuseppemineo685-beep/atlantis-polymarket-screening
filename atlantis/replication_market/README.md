# The Replication Market (dashboard interno)

NO es el SaaS publico (thereplicationmarket.com). Este es un dashboard interno
separado para analizar y (mas adelante, en modo paper primero) copiar en vivo
la wallet de Polymarket `0xeebde7a0e019a63e6b476eb425505b7b3e6eba30`
("Bonereaper"), detectada operando ~7,000+ trades/dia en mercados "Up or
Down" de 5 min (BTC/SOL/XRP/ETH), con ~$1.4M de ganancia acumulada real
(confirmado via user-pnl-api.polymarket.com) y 100% taker (confirmado
decodificando eventos OrderFilled on-chain - no cobra rewards de liquidez).

## monitor_bonereaper.py

Corre indefinidamente (pensado para el VPS de Alemania, screening/paper como
el resto de ATLANTIS). Por cada trade nuevo de esa wallet registra:
- mercado, moneda, lado comprado, precio pagado, tamano
- tiempo transcurrido dentro de la ventana de 5 min
- precio REAL de Chainlink en el momento exacto de la compra (via
  wss://ws-live-data.polymarket.com, topic crypto_prices_chainlink - el
  mismo feed que Polymarket usa para resolver, gratis, sin auth)
- TWAP corriente desde la apertura de la ventana hasta la compra
- una vez resuelto el mercado: ganador real y si la compra fue correcta

Salida: `bonereaper_monitor.jsonl` (append-only). Pensado para 24h+ sin
intervencion antes de sacar conclusiones de la senal.

## Deploy en Germany VPS (pendiente - SSH intermitente el 2026-09-12)

  ssh -i ~/.ssh/oracle_vps_key root@178.105.143.153
  cd /root/atlantis-polymarket-screening && git pull
  nohup python3 atlantis/replication_market/monitor_bonereaper.py \
    > /var/log/bonereaper-monitor.log 2>&1 &
