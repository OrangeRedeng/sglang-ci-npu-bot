#!/usr/bin/env bash
# Apply continuity to an existing daily Issue from saved JSON: no rescan/GLM.
set -Eeuo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 2; }
DATE="${1:?Usage: sudo bash scripts/sync-incident-continuity.sh YYYY-MM-DD [--publish]}"
[[ "$DATE" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]] || { echo 'Invalid date' >&2; exit 2; }
shift
MODE="${1:---preview}"
[[ "$MODE" == --preview || "$MODE" == --publish ]] || { echo 'Use --preview or --publish' >&2; exit 2; }
[[ $# -ge 1 ]] && shift
EXTRA=()
while (( $# )); do
  case "$1" in
    --verify-passes|--allow-pr-close) EXTRA+=("$1"); shift ;;
    --max-pass-logs)
      [[ ${2:-} =~ ^[0-9]+$ ]] || { echo 'Expected integer pass-log budget.' >&2; exit 2; }
      EXTRA+=("$1" "$2"); shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -s "/home/sglang-npu-bot/reports/npu-diffusion-ci-$DATE.json" ]] || { echo 'Saved snapshot unavailable' >&2;exit 1; }
if systemctl is-active --quiet npu-diffusion-ci.service ||
   pgrep -u sglang-ci -f 'npu_diffusion_ci_report\.py|reformat_report\.py|reanalyze_and_publish\.py' >/dev/null 2>&1; then
  echo 'Another publisher is running; refusing overlapping Issue writes.' >&2; exit 1
fi
UNIT="npu-continuity-${DATE//-/}-$$"
ARGS=("$DATE")
[[ "$MODE" == --publish ]] && ARGS+=(--publish)
ARGS+=("${EXTRA[@]}")
systemd-run --unit="$UNIT" --wait --collect \
  -p User=sglang-ci -p Group=sglang-ci \
  -p WorkingDirectory=/home/sglang-npu-bot \
  -p Environment=HOME=/home/sglang-npu-bot \
  -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
  /usr/bin/python3 /home/sglang-npu-bot/scripts/sync-incident-continuity.py "${ARGS[@]}"
echo "Review: sudo journalctl -u $UNIT.service -n 120 --no-pager"
