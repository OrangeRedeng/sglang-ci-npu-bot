#!/usr/bin/env bash
# Read-only collection test: no GLM, no GitHub Issues changes.
set -Eeuo pipefail
if [[ $(id -u) -ne 0 ]]; then echo 'Use: sudo bash deploy/run-smoke-test.sh' >&2; exit 2; fi
BOT_HOME=/home/sglang-npu-bot
# Prevent simultaneous smoke vs scheduled CI run.
if systemctl is-active --quiet npu-diffusion-ci.service || \
   systemctl is-active --quiet npu-diffusion-ci-repair.service; then
  echo 'The primary CI analyzer is running; do not start another collection simultaneously.' >&2
  exit 1
fi
UNIT="npu-diffusion-ci-smoke-$(date +%s)-$$"
echo "[SMOKE] Unit: ${UNIT}.service"
echo "[SMOKE] Follow in another SSH session: sudo journalctl -fu ${UNIT}.service"
systemd-run --unit="$UNIT" --collect --wait \
  -p User=sglang-ci -p Group=sglang-ci \
  -p WorkingDirectory="$BOT_HOME" \
  -p Environment="HOME=$BOT_HOME" \
  -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
  /usr/bin/python3 "$BOT_HOME/scripts/npu_diffusion_ci_report.py" \
  --lookback-hours 24 --max-runs-per-workflow 10 --max-job-logs 2 --no-glm --dry-run
