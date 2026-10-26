#!/usr/bin/env bash
# Safe migration of all historical Issues, never delete or claim PR regressions fixed.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Use sudo' >&2;exit 2; }
MODE="${1:---preview}"
[[ "$MODE" == --preview || "$MODE" == --apply ]] || { echo 'Usage: sudo bash scripts/reconcile-legacy-issues.sh [--preview|--apply]' >&2;exit 2; }
[[ $# -le 1 ]] || exit 2
if pgrep -u sglang-ci -f 'npu_diffusion_ci_report\.py|reformat_report\.py|reanalyze_and_publish\.py|reconcile-legacy-issues\.py' >/dev/null 2>&1;then
  echo 'Another publisher/reconciler is running.' >&2;exit 1
fi
UNIT="npu-legacy-issues-$(date +%Y%m%d%H%M%S)-$$"
ARGS=()
[[ "$MODE" == --apply ]] && ARGS+=(--apply)
set +e
systemd-run --unit="$UNIT" --wait --collect \
  -p User=sglang-ci -p Group=sglang-ci \
  -p WorkingDirectory=/home/sglang-npu-bot \
  -p Environment=HOME=/home/sglang-npu-bot \
  -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
  -p TimeoutStartSec=20min \
  /usr/bin/python3 /home/sglang-npu-bot/scripts/reconcile-legacy-issues.py "${ARGS[@]}"
RC=$?
set -e
journalctl -u "${UNIT}.service" -n 150 --no-pager -o cat || true
exit "$RC"
