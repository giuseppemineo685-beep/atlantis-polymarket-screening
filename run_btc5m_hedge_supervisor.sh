#!/bin/bash
# Idempotent start: run via @reboot cron (or manually) - does nothing if
# the processes are already running, so it is safe to invoke repeatedly.
cd /root/atlantis-polymarket-screening
pgrep -f 'scripts/run_btc5m_hedge_paper_trading.py' > /dev/null ||   nohup python3 -u scripts/run_btc5m_hedge_paper_trading.py > /var/log/atlantis-btc5m-hedge.log 2>&1 &
pgrep -f 'scripts/publish_dashboard_loop.sh' > /dev/null ||   nohup bash scripts/publish_dashboard_loop.sh > /var/log/atlantis-btc5m-hedge-dashboard.log 2>&1 &
