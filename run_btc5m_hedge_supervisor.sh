#!/bin/bash
# Idempotent start: run via @reboot cron (or manually) - does nothing if
# the processes are already running, so it is safe to invoke repeatedly.
cd /root/atlantis-polymarket-screening
# btc5m_hedge PAUSED 2026-08-15 at the owner's request - 6 real days live
# showed 61.3% win rate but -$61.12 net (avg win $0.48 vs avg loss -$0.85,
# needs 64.0% to break even) - not proven profitable. Code and data left
# in place, just not auto-started anymore.
pgrep -f 'scripts/publish_dashboard_loop.sh' > /dev/null ||   nohup bash scripts/publish_dashboard_loop.sh > /var/log/atlantis-btc5m-hedge-dashboard.log 2>&1 &
pgrep -f 'scripts/run_btc5m_momentum_paper_trading.py' > /dev/null ||   nohup python3 -u scripts/run_btc5m_momentum_paper_trading.py > /var/log/atlantis-btc5m-momentum.log 2>&1 &
