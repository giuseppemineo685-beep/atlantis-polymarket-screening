#!/bin/bash
# Regenerates docs/index.html from the live btc5m_hedge state and publishes
# it to git every ~30s, so GitHub Pages (or anyone pulling the repo) sees a
# near-live dashboard without needing shell/VPS access. Separate from the
# trading loop itself (which polls every ~1s) - committing that often would
# flood git history and risk push races with nothing to show for it.
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
  python3 -B scripts/generate_dashboard.py > /tmp/dashboard_regen.log 2>&1
  git_retry git pull --no-rebase -q -X ours
  git add docs/index.html
  git diff --cached --quiet || git commit -q -m "Dashboard: $(date -u +'%Y-%m-%d %H:%M:%S UTC')"
  git_retry git push -q
  sleep 30
done
