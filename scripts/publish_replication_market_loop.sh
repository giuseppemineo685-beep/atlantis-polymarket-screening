#!/bin/bash
# Regenera docs/replication_market.html a partir de
# atlantis/replication_market/bonereaper_monitor.jsonl y lo publica a git
# cada ~30s, igual que publish_dashboard_loop.sh hace para el dashboard de
# btc5m_hedge. Pensado para correr en el VPS de Alemania junto al monitor
# (monitor_bonereaper.py), no reemplaza a ese loop, corre en paralelo.
set -u
cd "$(dirname "$0")/.."

git_retry() {
  local n=0
  until "$@"; do
    n=$((n + 1))
    if [ "$n" -ge 3 ]; then
      echo "git $* failed after 3 attempts" >&2
      return 1
    fi
    sleep $((n * 5))
  done
}

while true; do
  python3 -B scripts/generate_replication_market_dashboard.py > /tmp/replication_market_regen.log 2>&1
  git_retry git pull --no-rebase -q -X ours
  git add docs/replication_market.html atlantis/replication_market/bonereaper_monitor.jsonl
  git diff --cached --quiet || git commit -q -m "Replication Market dashboard: $(date -u +'%Y-%m-%d %H:%M:%S UTC')"
  git_retry git push -q
  sleep 30
done
