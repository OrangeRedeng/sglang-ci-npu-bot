#!/usr/bin/env bash
# Repair retained evidence, refresh latest daily, reconcile legacy Issues, optionally run today.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Use sudo.' >&2;exit 2; }
MODE=--preview
GLM=0
RUN=0
for arg in "$@"; do
  case "$arg" in
    --preview|--apply) MODE="$arg";;
    --glm) GLM=1;;
    --run-today) RUN=1;;
    *) echo 'Usage: repair-and-run.sh [--preview|--apply] [--glm] [--run-today]' >&2;exit 2;;
  esac
done
[[ "$MODE" == --apply || $GLM$RUN == 00 ]] || { echo '--glm and --run-today require --apply.' >&2;exit 2; }
exec 9>/run/npu-diffusion-ci-maintenance.lock
flock -n 9 || { echo 'Maintenance already running.' >&2;exit 1; }
TIMER_WAS_ACTIVE=0
systemctl is-active --quiet npu-diffusion-ci.timer && TIMER_WAS_ACTIVE=1 || true
systemctl stop npu-diffusion-ci.timer
restore_timer() { if (( TIMER_WAS_ACTIVE )); then systemctl start npu-diffusion-ci.timer; fi; }
trap restore_timer EXIT
if systemctl is-active --quiet npu-diffusion-ci.service || pgrep -u sglang-ci -f 'npu_diffusion_ci_report\.py|reanalyze_and_publish\.py|reconcile-legacy-issues\.py|refresh_daily_snapshots\.py|reparse_failure_snapshots\.py' >/dev/null; then
  echo 'Another analyzer/publisher is active; wait for it to finish and rerun.' >&2;exit 1
fi
run_bot() {
  local unit="npu-ci-maintenance-$(date +%Y%m%d%H%M%S)-$$-$RANDOM"
  local rc=0
  systemd-run --unit="$unit" --wait --collect \
    -p User=sglang-ci -p Group=sglang-ci \
    -p WorkingDirectory=/home/sglang-npu-bot \
    -p Environment=HOME=/home/sglang-npu-bot \
    -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
    -p UMask=0077 -p TimeoutStartSec=90min \
    /usr/bin/python3 "$@" || rc=$?
  journalctl -u "$unit.service" -n 100 --no-pager -o cat || true
  return "$rc"
}
ARGS=(); [[ "$MODE" == --apply ]] && ARGS+=(--apply)
run_bot scripts/reparse_failure_snapshots.py "${ARGS[@]}"
REFRESH=("${ARGS[@]}"); (( GLM == 0 )) || REFRESH+=(--glm)
run_bot scripts/refresh_daily_snapshots.py "${REFRESH[@]}"
run_bot scripts/reconcile-legacy-issues.py "${ARGS[@]}"
if (( RUN )); then
  # Deliberately omit --skip-if-published-today: update the current daily in place.
  run_bot scripts/npu_diffusion_ci_report.py --lookback-hours 24
fi
if [[ "$MODE" == --apply ]]; then echo 'Repair/refresh completed. Main incidents still require verified testcase PASS for closure.'; fi
