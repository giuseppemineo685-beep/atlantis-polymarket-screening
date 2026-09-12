#!/bin/bash
# Corre el monitor de Bonereaper + el auto-publish del dashboard durante una
# duracion acotada (pensado para un job de GitHub Actions, que tiene un
# limite duro de 6h por corrida). Commitea/pushea seguido para no perder
# progreso si el job se corta. Al terminar el tiempo, apaga el monitor
# prolijamente y hace un commit final.
#
# Uso: run_replication_market_monitor_bounded.sh <duracion_en_segundos>
set -u
cd "$(dirname "$0")/.."

DURATION="${1:-20400}"  # default 5h40m, deja margen bajo el limite de 6h
END=$(( $(date +%s) + DURATION ))

git config user.name "atlantis-bot" 2>/dev/null
git config user.email "atlantis-bot@users.noreply.github.com" 2>/dev/null

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

publish_once() {
  # Descartamos el HTML regenerado del ciclo anterior (es 100% derivado, se
  # puede recrear) ANTES de pull - si no, "git pull" se niega a mergear
  # porque hay cambios locales sin commitear y el loop queda trabado.
  git checkout -- docs/replication_market.html 2>/dev/null
  git_retry git pull --no-rebase -q -X ours
  # Recien ahora regeneramos, sobre el jsonl ya actualizado con lo que bajo
  # el pull (por si otra corrida escribio de mas).
  python3 -B scripts/generate_replication_market_dashboard.py > /tmp/replication_market_regen.log 2>&1
  # Diagnostico: cuantos precios reales tenemos por simbolo, para poder ver
  # esto sin esperar a que el job termine y sin logs que se descartan.
  {
    echo "=== $(date -u +'%Y-%m-%d %H:%M:%S UTC') ==="
    python3 -B -c "
import json, collections
c = collections.Counter()
with_price = collections.Counter()
for line in open('atlantis/replication_market/bonereaper_monitor.jsonl'):
    r = json.loads(line)
    c[r.get('symbol')] += 1
    if r.get('chainlink_price_at_trade') is not None:
        with_price[r.get('symbol')] += 1
for sym in c:
    print(f'{sym}: {c[sym]} trades, {with_price.get(sym,0)} con precio chainlink')
"
    echo "--- ultimas 15 lineas del monitor ---"
    tail -15 /tmp/monitor_bonereaper.log 2>/dev/null
  } > atlantis/replication_market/monitor_debug.log
  git add docs/replication_market.html atlantis/replication_market/bonereaper_monitor.jsonl atlantis/replication_market/monitor_debug.log
  git diff --cached --quiet || git commit -q -m "Replication Market dashboard (Actions): $(date -u +'%Y-%m-%d %H:%M:%S UTC')"
  git_retry git push -q
}

echo "starting monitor_bonereaper.py in background for ${DURATION}s"
python3 -u atlantis/replication_market/monitor_bonereaper.py > /tmp/monitor_bonereaper.log 2>&1 &
MONITOR_PID=$!

while [ "$(date +%s)" -lt "$END" ]; do
  sleep 60
  publish_once
  # if the monitor died (crash), restart it rather than running the rest of
  # the window with no data collection
  if ! kill -0 "$MONITOR_PID" 2>/dev/null; then
    echo "monitor died, restarting"
    tail -30 /tmp/monitor_bonereaper.log
    python3 -u atlantis/replication_market/monitor_bonereaper.py > /tmp/monitor_bonereaper.log 2>&1 &
    MONITOR_PID=$!
  fi
done

echo "window elapsed, stopping monitor"
kill "$MONITOR_PID" 2>/dev/null
sleep 2
publish_once
echo "done"
