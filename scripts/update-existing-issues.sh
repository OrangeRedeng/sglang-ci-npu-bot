#!/usr/bin/env bash
# Refresh existing daily + cluster GitHub Issues without rescanning CI or GLM calls.
set -Eeuo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run with sudo to load /etc/sglang-npu-bot/bot.env via systemd.' >&2; exit 2
fi
DATE="${1:-$(TZ=Europe/Moscow date +%F)}"
if [[ ! "$DATE" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo 'Usage: sudo bash scripts/update-existing-issues.sh YYYY-MM-DD' >&2; exit 2
fi
SNAP="/home/sglang-npu-bot/reports/npu-diffusion-ci-${DATE}.json"
MARKDOWN="/home/sglang-npu-bot/reports/npu-diffusion-ci-${DATE}.md"
if pgrep -u sglang-ci -f 'npu_diffusion_ci_report\.py|reformat_report\.py' >/dev/null 2>&1; then
  echo 'A report publisher is already active; refusing concurrent Issue writes.' >&2
  exit 1
fi
if [[ ! -r "$SNAP" || ! -r "$MARKDOWN" ]]; then
  echo "Missing saved snapshot/report: $SNAP, $MARKDOWN" >&2; exit 1
fi
UNIT="npu-ci-issue-sync-${DATE//-/}-$$"
systemd-run --unit="$UNIT" --wait --collect \
  -p User=sglang-ci -p Group=sglang-ci \
  -p WorkingDirectory=/home/sglang-npu-bot \
  -p Environment=HOME=/home/sglang-npu-bot \
  -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
  /usr/bin/python3 /home/sglang-npu-bot/scripts/reformat_report.py \
  --snapshot "reports/npu-diffusion-ci-${DATE}.json" \
  --previous-report "reports/npu-diffusion-ci-${DATE}.md" \
  --publish --refresh-provenance --sync-labels
printf 'Issue sync journal: sudo journalctl -u %s.service -n 100 --no-pager\n' "$UNIT"
