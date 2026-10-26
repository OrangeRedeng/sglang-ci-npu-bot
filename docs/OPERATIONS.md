# Updating the bot and refreshing reports

The examples use the existing source checkout at `/opt/npu-diffusion-ci-kit`
and the installed bot at `/home/sglang-npu-bot`. Run them on the reporting host.
The [verification notes](VERIFICATION.md) describe the current implementation
gaps; updating does not resolve those gaps.

## Update, refresh yesterday, and collect today

The following block:

1. Pauses the timer and checks that no reporting process is active.
2. Fetches this repository's main branch and installs that exact fetched commit.
3. Reparses retained failure logs into local snapshots, with backups.
4. Updates yesterday's existing daily Issue using its saved snapshot and a new
   GLM analysis.
5. Collects a fresh 24-hour window and publishes today's report. If today's
   daily Issue already exists, it is updated in place.
6. Restores the timer if it was previously active or enabled, including after
   an error.

Yesterday is calculated in Moscow time. On October 26, 2026, it selects
**October 25** for the historical refresh and **October 26** for the new report.
The old report must already have a saved JSON snapshot and exactly one matching
daily Issue. Missing raw logs cannot be reconstructed by reparsing.

The two GLM-enabled report runs can consume the API budget configured in
`/etc/sglang-npu-bot/bot.env`. This procedure preserves that file and does not
raise spending limits. Remove `--glm` from the historical refresh command to
rebuild yesterday's report deterministically without a new historical GLM call;
today's normal run still uses its configured analysis settings.

```bash
sudo bash -e <<'SH'
exec 9>/run/npu-diffusion-ci-maintenance.lock
flock -n 9 || { echo 'Another maintenance operation is running.' >&2; exit 1; }

restore_schedule=0
if systemctl is-active --quiet npu-diffusion-ci.timer ||
   systemctl is-enabled --quiet npu-diffusion-ci.timer; then
  restore_schedule=1
fi
restore_timer() {
  if (( restore_schedule )); then
    systemctl enable --now npu-diffusion-ci.timer
  fi
}
trap restore_timer EXIT
systemctl stop npu-diffusion-ci.timer

if systemctl is-active --quiet npu-diffusion-ci.service ||
   pgrep -u sglang-ci -f 'npu_diffusion_ci_report\.py|reanalyze_and_publish\.py|reformat_report\.py|refresh_daily_snapshots\.py|reparse_failure_snapshots\.py|reconcile-legacy-issues\.py' >/dev/null; then
  echo 'A reporting process is active. Wait for it to finish.' >&2
  exit 1
fi

cd /opt/npu-diffusion-ci-kit
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo 'The source checkout has local changes; preserve them before updating.' >&2
  exit 1
fi
git fetch https://github.com/OrangeRedeng/sglang-ci-npu-bot.git main
git checkout --detach FETCH_HEAD
bash deploy/update-from-git.sh --install --defer-timer

run_bot() {
  systemd-run --wait --pipe --collect \
    --unit="npu-ci-manual-$(date +%Y%m%d%H%M%S)-$$-$RANDOM" \
    -p User=sglang-ci -p Group=sglang-ci \
    -p WorkingDirectory=/home/sglang-npu-bot \
    -p Environment=HOME=/home/sglang-npu-bot \
    -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
    -p UMask=0077 -p TimeoutStartSec=90min \
    /usr/bin/python3 "$@"
}

report_yesterday=$(TZ=Europe/Moscow date -d yesterday +%F)
run_bot scripts/reparse_failure_snapshots.py --apply
run_bot scripts/refresh_daily_snapshots.py --dates "$report_yesterday" --apply --glm
run_bot scripts/npu_diffusion_ci_report.py --lookback-hours 24
SH
```

Reparsing affects all retained local daily snapshots; it does not publish them.
Only the selected historical daily Issue is refreshed by the historical command.
The current-day collector retains the configured main-incident publication and
closure policy. This procedure does not run legacy Issue reconciliation.

The source checkout ends on a detached commit. Its existing Git remotes and
history are preserved. Repeat the explicit fetch command for later updates;
it does not switch an old `origin` to this repository.

To install the code only, run the same block through the installation command
and omit `run_bot` and the three report commands. To target a fixed historical
date, replace the `report_yesterday` assignment with the required `YYYY-MM-DD`.

## Check results

```bash
sudo systemctl list-timers npu-diffusion-ci.timer --no-pager
sudo journalctl -u npu-diffusion-ci.service -n 100 --no-pager
sudo ls -lh /home/sglang-npu-bot/reports/npu-diffusion-ci-*.md
sudo ls -lh /home/sglang-npu-bot/reports/repaired/
```

The manual commands stream their output in the terminal. The service journal
command above shows scheduled runs. Historical refresh writes its local drafts
under `reports/repaired/`; the new daily report is written under `reports/`.
Check the configured target repository for the published daily Issues.

The commands have been checked against the current scripts and for shell syntax.
They have not been executed on the deployed reporting host as part of this audit.
