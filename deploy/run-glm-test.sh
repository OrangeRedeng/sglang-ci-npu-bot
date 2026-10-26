#!/usr/bin/env bash
# GLM-only snapshot reanalysis; no GitHub scans or Issue publication.
set -Eeuo pipefail
if [[ $(id -u) -ne 0 ]]; then echo 'Use sudo bash deploy/run-glm-test.sh [triage|investigate|auto] [snapshot.json]' >&2; exit 2; fi
MODE="${1:-triage}"
case "$MODE" in triage|investigate|auto) ;; *) echo 'Mode must be triage|investigate|auto' >&2; exit 2;; esac
BOT_HOME=/home/sglang-npu-bot
SNAPSHOT="${2:-}"
if [[ -z "$SNAPSHOT" ]]; then
  SNAPSHOT="$(find "$BOT_HOME/reports" -maxdepth 1 -type f -name 'npu-diffusion-ci-*.json' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
fi
if [[ -z "$SNAPSHOT" || ! -f "$SNAPSHOT" ]]; then echo 'No saved CI snapshot available' >&2; exit 1; fi
UNIT="npu-glm-v14-${MODE}-$(date +%s)-$$"
echo "[GLM] ${MODE}; snapshot=${SNAPSHOT}; unit=${UNIT}.service"
echo "[GLM] This may consume paid BigModel Standard API tokens. No GitHub reads/writes."
set +e
systemd-run --unit="$UNIT" --collect --wait \
  -p User=sglang-ci -p Group=sglang-ci \
  -p WorkingDirectory="$BOT_HOME" -p Environment="HOME=$BOT_HOME" \
  -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
  /usr/bin/python3 "$BOT_HOME/scripts/reanalyze_snapshot.py" --mode "$MODE" --snapshot "$SNAPSHOT"
result=$?
set -e
echo "[GLM] Logs: sudo journalctl -u ${UNIT}.service -n 100 --no-pager"
echo "[GLM] Diagnostic summary (does not print model reasoning or API credentials):"
journalctl -u "${UNIT}.service" --no-pager -o cat 2>/dev/null \
  | grep -E 'GLM response|GLM=|GLM investigation|GLM synthesis|failed|WARNING|Reconstructed aggregate|REANALYZE|GLM budget' \
  | tail -35 || true
exit "$result"
