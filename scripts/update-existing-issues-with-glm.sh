#!/usr/bin/env bash
# Re-run GLM with validated upstream PR candidates, then update existing issues.
set -Eeuo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'Use sudo.' >&2; exit 2; }
DATE="${1:?Usage: sudo bash scripts/update-existing-issues-with-glm.sh YYYY-MM-DD [--preview]}"
MODE="${2:---publish}"
[[ "$DATE" =~ ^20[0-9]{2}-[0-9]{2}-[0-9]{2}$ ]] || { echo 'Bad YYYY-MM-DD date.' >&2; exit 2; }
[[ "$MODE" == --publish || "$MODE" == --preview ]] || { echo 'Second arg must be --publish or --preview.' >&2; exit 2; }
shift
[[ $# -eq 0 ]] || shift
EXTRA=()
REUSE_FLAG=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-partial)
      EXTRA+=(--allow-partial); shift ;;
    --reuse-analysis|--reuse-recovery)
      [[ -z "$REUSE_FLAG" ]] || { echo 'Only one analysis source is allowed.' >&2; exit 2; }
      [[ -n "${2:-}" ]] || { echo "$1 requires a file path" >&2; exit 2; }
      REUSE_FLAG="$1"
      EXTRA+=("$1" "$2"); shift 2 ;;
    *) echo 'Usage: DATE [--publish|--preview] [--allow-partial] [--reuse-analysis|--reuse-recovery FILE]' >&2; exit 2 ;;
  esac
done
if [[ "$REUSE_FLAG" == --reuse-recovery ]] && [[ " ${EXTRA[*]} " != *' --allow-partial '* ]]; then
  echo '--reuse-recovery requires explicit --allow-partial' >&2
  exit 2
fi
SNAP="/home/sglang-npu-bot/reports/npu-diffusion-ci-${DATE}.json"
[[ -r "$SNAP" ]] || { echo "Missing snapshot: $SNAP" >&2; exit 1; }
if pgrep -u sglang-ci -f 'npu_diffusion_ci_report\.py|reformat_report\.py|reanalyze_and_publish\.py' >/dev/null 2>&1; then
  echo 'Another CI publisher/analysis process is active; refusing overlap.' >&2;exit 1
fi
# Manual high-reasoning profile, explicitly isolated from economical daily bot.env.
# In v17/v18 the HTTP client silently inherited the daily 8000-token cap,
# consuming all of it in reasoning and returning empty visible content.
REFRESH_EFFORT="${GLM_REFRESH_REASONING_EFFORT:-${GLM_MANUAL_REASONING_EFFORT:-high}}"
REFRESH_TOTAL="${GLM_REFRESH_MAX_OUTPUT_TOKENS:-24000}"
REFRESH_CLUSTER="${GLM_REFRESH_CLUSTER_MAX_TOKENS:-20000}"
REFRESH_SYNTHESIS="${GLM_REFRESH_SYNTHESIS_MAX_TOKENS:-24000}"
REFRESH_TRIAGE="${GLM_REFRESH_TRIAGE_MAX_TOKENS:-10000}"
REFRESH_TIMEOUT="${GLM_REFRESH_API_TIMEOUT_SECONDS:-720}"
REFRESH_RETRIES="${GLM_REFRESH_EMPTY_RETRIES:-1}"
REFRESH_MAX_INVESTIGATIONS="${GLM_REFRESH_MAX_INVESTIGATIONS:-${GLM_MANUAL_MAX_INVESTIGATIONS:-3}}"
[[ "$REFRESH_EFFORT" == low || "$REFRESH_EFFORT" == high || "$REFRESH_EFFORT" == max ]] || {
  echo 'Invalid reasoning effort (expected low, high, max).' >&2; exit 2;
}
validate_int() {
  local key="$1" value="$2" min="$3" max="$4"
  [[ "$value" =~ ^[0-9]+$ ]] && (( 10#$value >= min && 10#$value <= max )) || {
    echo "Invalid $key=$value (expected integer $min..$max)." >&2; exit 2;
  }
}
validate_int GLM_REFRESH_MAX_OUTPUT_TOKENS "$REFRESH_TOTAL" 512 32768
validate_int GLM_REFRESH_CLUSTER_MAX_TOKENS "$REFRESH_CLUSTER" 512 32768
validate_int GLM_REFRESH_SYNTHESIS_MAX_TOKENS "$REFRESH_SYNTHESIS" 512 32768
validate_int GLM_REFRESH_TRIAGE_MAX_TOKENS "$REFRESH_TRIAGE" 512 32768
validate_int GLM_REFRESH_API_TIMEOUT_SECONDS "$REFRESH_TIMEOUT" 10 1200
validate_int GLM_REFRESH_EMPTY_RETRIES "$REFRESH_RETRIES" 0 2
validate_int GLM_REFRESH_MAX_INVESTIGATIONS "$REFRESH_MAX_INVESTIGATIONS" 1 5
if (( REFRESH_CLUSTER > REFRESH_TOTAL || REFRESH_SYNTHESIS > REFRESH_TOTAL || REFRESH_TRIAGE > REFRESH_TOTAL )); then
  echo 'Per-stage token budgets cannot exceed GLM_REFRESH_MAX_OUTPUT_TOKENS; refusing silent truncation.' >&2
  exit 2
fi
if [[ "$REFRESH_EFFORT" != low ]] && (( REFRESH_TOTAL < 12000 )); then
  echo 'High/max reasoning requires >=12000 output tokens for the manual profile.' >&2
  exit 2
fi
printf '[REFRESH] Manual GLM: effort=%s, total=%s, cluster=%s, synthesis=%s, timeout=%ss, length-retries=%s\n' \
  "$REFRESH_EFFORT" "$REFRESH_TOTAL" "$REFRESH_CLUSTER" "$REFRESH_SYNTHESIS" "$REFRESH_TIMEOUT" "$REFRESH_RETRIES"
UNIT="npu-glm-issue-v21-${DATE//-/}-$$"
if [[ "${REUSE_FLAG:-}" == --reuse-recovery || "${REUSE_FLAG:-}" == --reuse-analysis ]]; then
  echo '[REFRESH] Reusing saved report. No GLM HTTP calls will be made.'
fi
ARGS=(--snapshot "reports/npu-diffusion-ci-${DATE}.json" --mode investigate
  --max-investigations "$REFRESH_MAX_INVESTIGATIONS"
  --reasoning-effort "$REFRESH_EFFORT")
[[ "$MODE" == --publish ]] && ARGS+=(--publish)
ARGS+=("${EXTRA[@]}")
# Save the exact transient unit name BEFORE calling systemd, and always show
# its journal regardless of subprocess exit code (v17 hid failures via set -e).
set +e
systemd-run --unit="$UNIT" --wait --collect \
  -p User=sglang-ci -p Group=sglang-ci \
  -p WorkingDirectory=/home/sglang-npu-bot \
  -p Environment=HOME=/home/sglang-npu-bot \
  -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
  -p TimeoutStartSec=90min \
  /usr/bin/env \
    GLM_BUDGET_ENABLED=1 \
    GLM_DAILY_BUDGET_CNY="${GLM_REFRESH_DAILY_BUDGET_CNY:-3.00}" \
    GLM_MAX_DAILY_API_REQUESTS="${GLM_REFRESH_MAX_REQUESTS:-8}" \
    GLM_PER_REQUEST_BUDGET_CNY="${GLM_REFRESH_PER_REQUEST_BUDGET_CNY:-0.50}" \
    GLM_MAX_OUTPUT_TOKENS="$REFRESH_TOTAL" \
    GLM_CLUSTER_MAX_TOKENS="$REFRESH_CLUSTER" \
    GLM_SYNTHESIS_MAX_TOKENS="$REFRESH_SYNTHESIS" \
    GLM_TRIAGE_MAX_TOKENS="$REFRESH_TRIAGE" \
    GLM_API_TIMEOUT_SECONDS="$REFRESH_TIMEOUT" \
    GLM_EMPTY_RETRIES="$REFRESH_RETRIES" \
    /usr/bin/python3 /home/sglang-npu-bot/scripts/reanalyze_and_publish.py "${ARGS[@]}"
RC=$?
set -e
echo "Journal: sudo journalctl -u ${UNIT}.service -n 150 --no-pager -o cat"
sudo journalctl -u "${UNIT}.service" -n 100 --no-pager -o cat || true
if (( RC != 0 )); then
  echo "Issue refresh FAILED (exit=$RC). No automatic paid retry. Inspect the journal above." >&2
fi
exit "$RC"
