#!/usr/bin/env bash
# Publish corrected historical Issues without GLM, then queue today's daily run.
# Designed for upgrading an already-installed openEuler bot from v20 to v21.0.x.
set -Eeuo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run as root: sudo bash deploy/update-and-run.sh ...' >&2; exit 2; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OLD_DATE=2026-10-20
NEW_DATE=2026-10-21
ANALYSIS='/home/sglang-npu-bot/reports/reanalysis/english-pr-refresh-20261021T083242Z-full-analysis.md'
DO_INSTALL=1
while (( $# )); do
  case "$1" in
    --analysis) ANALYSIS="${2:?--analysis requires a path}"; shift 2 ;;
    --old-date) OLD_DATE="${2:?--old-date requires YYYY-MM-DD}"; shift 2 ;;
    --new-date) NEW_DATE="${2:?--new-date requires YYYY-MM-DD}"; shift 2 ;;
    --skip-install) DO_INSTALL=0; shift ;;
    *) echo "Unexpected option: $1" >&2; exit 2 ;;
  esac
done
[[ "$OLD_DATE" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ && "$NEW_DATE" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]] || {
  echo 'Expected ISO dates.' >&2; exit 2;
}
# The main collector uses today's Moscow date; it cannot magically reconstruct
# a missed historical 24h window when invoked on a later date.
MSK_TODAY="$(TZ=Europe/Moscow date +%Y-%m-%d)"
if [[ "$MSK_TODAY" != "$NEW_DATE" ]]; then
  echo "Refusing misdated live run: today in Moscow is $MSK_TODAY, requested $NEW_DATE." >&2
  echo 'Historical dates require a saved snapshot, not a fresh live scan.' >&2
  exit 2
fi
[[ -s "$ANALYSIS" ]] || { echo "Missing nonempty full analysis: $ANALYSIS" >&2; exit 1; }
[[ -s "/home/sglang-npu-bot/reports/npu-diffusion-ci-$OLD_DATE.json" ]] || {
  echo "Historical snapshot missing: $OLD_DATE" >&2; exit 1;
}
if systemctl is-active --quiet npu-diffusion-ci.service ||
   pgrep -u sglang-ci -f 'npu_diffusion_ci_report\.py|reformat_report\.py|reanalyze_and_publish\.py' >/dev/null 2>&1; then
  echo 'A CI publisher is already running; refusing overlap.' >&2; exit 1
fi
# Stop calendar triggers while reconstructing the earlier report; enabled state
# is restored below even if a step fails.
TIMER_WAS_ENABLED=0
TIMER_WAS_ACTIVE=0
systemctl is-enabled --quiet npu-diffusion-ci.timer && TIMER_WAS_ENABLED=1 || true
systemctl is-active --quiet npu-diffusion-ci.timer && TIMER_WAS_ACTIVE=1 || true
cleanup() {
  rc=$?
  if (( rc != 0 )); then
    echo "Recovery failed (exit=$rc). Re-enabling previously enabled timer." >&2
    if (( TIMER_WAS_ENABLED || TIMER_WAS_ACTIVE )); then
      systemctl enable --now npu-diffusion-ci.timer || true
    fi
  fi
}
trap cleanup EXIT
systemctl stop npu-diffusion-ci.timer || true
if (( DO_INSTALL )); then
  echo '[1/3] Install v21.0.x code without starting the calendar timer.'
  bash "$ROOT/deploy/update-from-git.sh" --install --defer-timer
fi
[[ "$(cat "$ROOT/VERSION")" =~ ^21\.0\.[0-9]+$ ]] || {
  echo 'Source checkout is not v21.0.x.' >&2; exit 1
}
echo '[2/3] Refresh historical daily + cluster Issues using saved GLM synthesis (no BigModel calls).'
bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  "$OLD_DATE" --publish --reuse-analysis "$ANALYSIS"
echo '[3/3] Queue live daily run, then restore Persistent calendar timer.'
systemctl start --no-block npu-diffusion-ci.service
systemctl enable --now npu-diffusion-ci.timer
trap - EXIT
printf 'Historical %s updated; live daily service for %s requested.\n' "$OLD_DATE" "$NEW_DATE"
echo 'Monitor: sudo journalctl -fu npu-diffusion-ci.service --no-pager'
echo 'Timer: sudo systemctl list-timers --all --no-pager | grep npu-diffusion-ci'
