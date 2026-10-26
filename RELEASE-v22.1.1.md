# NPU Diffusion CI Kit v22.1.1 — P0–P2 final release

Includes v22.0.2 P0 diagnostics, complete pytest failure accounting and cross-day
symptom history, plus the P1/P2 changes in CHANGELOG.md.

This patch aligns the report with the original October 8 English example in
“Ежедневный отчет NPU-Diffusion”. It fixes Actual/Expected/Limit extraction,
relative tolerance versus absolute pad, long-trace evidence retention and
flattened GLM Markdown. It adds a quantitative assessment, execution scope,
configuration evidence gaps and per-scenario experiments with acceptance criteria.
See REFERENCE-COMPARISON-v22.1.1.md for the exact comparison and remaining limits.

## Install on your openEuler server

Copy `npu-diffusion-ci-kit-v22.1.1.bundle` to `/home/swx1199799/`. Wait for any
currently running analyzer to finish before updating. The installer preserves
`bot.env`, reports and monetary caps and records its normal code rollback backup.

```bash
set -e
cd /opt/npu-diffusion-ci-kit
sudo git fetch /home/swx1199799/npu-diffusion-ci-kit-v22.1.1.bundle \
  refs/tags/v22.1.1:refs/tags/v22.1.1
sudo git checkout --detach v22.1.1
sudo bash deploy/update-from-git.sh --install
sudo bash deploy/apply-research-profile.sh --apply
```

The profile backs up bot.env and selects research output, high effort, two deep
investigations, 24,000 global output cap (16,000 per investigation, 20,000 synthesis),
100 failed-job logs and at most 38 PR-search API calls. It also expands the full GLM
analysis in the daily report. API keys, token prices, daily/per-request monetary
caps and request-count limits are preserved. Existing low financial limits can
still reject high-effort requests; rejection appears in telemetry.

## Repair current reports and legacy Issues, then force today's report

Optional preview (GitHub reads, local draft files; no Issue writes or GLM calls):

```bash
sudo bash /home/sglang-npu-bot/deploy/repair-and-run.sh --preview
```

Apply, including a new GLM investigation of the latest saved daily report and a
fresh current-day scan, even if today's Issue already exists:

```bash
sudo bash /home/sglang-npu-bot/deploy/repair-and-run.sh --apply --glm --run-today
```

This is the recommended combined recovery command. It runs as `sglang-ci` through
transient systemd services with the existing protected EnvironmentFile. It:

1. Pauses the timer; refuses to proceed while another analyzer/publisher is active.
2. Reparses all local daily JSON snapshots from retained raw logs, preserving
   originals under `reports/p0-backups/`. Missing logs are reported and their
   old evidence is retained; missing data cannot be reconstructed.
3. Refreshes the latest saved daily Issue in place, accepting PR-only families
   without individual Issues. Uses auto-selection and configured GLM budgets.
   Saves a pre-write Issue backup and rejects concurrent edits. Existing matched
   open incident details may be refreshed; unmapped or archived incidents are not
   relabelled as fixed.
4. Reconciles all bot-owned legacy Issues: administrative PR-only archival uses
   `not_planned`, older daily reports are archived, and main incidents remain open
   unless the ordinary verifier obtains equivalent testcase-level PASS evidence.
5. Performs a fresh 24-hour scan and updates today's daily Issue in place (or
   creates today's daily if absent). Normal main-incident creation/verification
   rules remain in effect. PR-only standalone Issue creation stays disabled.
6. Restores the timer if it was active before maintenance, including on failure.

The `--glm` historical refresh and fresh scan can each incur paid calls. Completed
historical GLM is required for its publication; failed/partial GLM leaves a local
recovery file and stops the command before further steps. A missing historical
daily Issue also stops refresh; inspect the printed error rather than creating
an invented historical report. Server/network/API timeouts remain possible.

To repair with no paid **historical** reanalysis, while still running today's
normal configured GLM analysis, omit `--glm`:

```bash
sudo bash /home/sglang-npu-bot/deploy/repair-and-run.sh --apply --run-today
```

To repair/refresh/cleanup with no paid GLM calls at all:

```bash
sudo bash /home/sglang-npu-bot/deploy/repair-and-run.sh --apply
```

That refresh labels historical analysis as deterministic. Original Issue bodies
remain in backups; it does not pretend old GLM output was newly validated.

## Refresh additional historical daily Issues

After maintenance, for explicit older dates, use the helper directly through a
transient unit. This example updates October 24 and 25 without GLM spending.
Run while the analyzer is idle; pause the timer during manual publication.

```bash
sudo systemctl stop npu-diffusion-ci.timer
sudo systemd-run --unit=npu-ci-refresh-history --wait --collect \
  -p User=sglang-ci -p Group=sglang-ci \
  -p WorkingDirectory=/home/sglang-npu-bot \
  -p Environment=HOME=/home/sglang-npu-bot \
  -p EnvironmentFile=/etc/sglang-npu-bot/bot.env \
  -p UMask=0077 -p TimeoutStartSec=90min \
  /usr/bin/python3 scripts/refresh_daily_snapshots.py \
  --dates 2026-10-24 2026-10-25 --apply
sudo systemctl start npu-diffusion-ci.timer
```

Add `--glm` for a paid new analysis per selected date. Each date needs its saved
JSON and exactly one existing daily Issue. Refresh does not fabricate missing
snapshots, remap incident fingerprints by title, delete Issues, or claim CI fixes.

## Normal scheduled operation and logs

```bash
sudo systemctl enable --now npu-diffusion-ci.timer
sudo systemctl list-timers npu-diffusion-ci.timer --no-pager
sudo journalctl -u npu-diffusion-ci.service -n 100 --no-pager
```

The ordinary service still skips a day already published; use the combined
recovery command with `--run-today` for a forced update. Maintenance logs use
`npu-ci-maintenance-*` transient units and are also printed by the wrapper.

```bash
sudo bash /home/sglang-npu-bot/deploy/self-test.sh
sudo bash /home/sglang-npu-bot/deploy/run-smoke-test.sh
```

Self-test includes an API connectivity preflight; smoke sampling makes source CI
reads but no paid model calls or Issue writes.

## Evidence limits

- Performance extraction requires explicit labelled actual/baseline latency with
  units. Unlabelled values and unknown stage/runner/runtime remain unknown.
- Open/merged PR status, head CI and file overlap are verified metadata, not proof
  of causality or that a merged fix passed the failing scenario. Head CI may refer
  to a newer SHA than the observed tested SHA; both are retained separately.
- PR intelligence is bounded; missing checks, changed-file truncation, exhausted
  API budgets and unsupported diff paths are not evidence of no relevant PR.
- Coverage shows observed executions. JUnit artifacts and all successful-job logs
  are not collected. Missing testcase PASS is unknown. History depends on retained
  daily JSON files; overlapping windows are deduplicated by job ID/attempt.
- Provider-omitted usage stays unreported. Reasoning token counts may be shown;
  private reasoning text is never persisted or published.
- Automatic administrative closure is not a bug fix. Main closure still requires
  the existing conservative testcase PASS verifier. Unconfirmed Issues remain.

## Validation

220 offline tests pass in the source tree, the simulated installed runtime and
the extracted source ZIP. All shipped shell scripts pass `bash -n`; Python modules
compile; Git bundle verifies and restores the release tag. Tests use synthetic
redacted evidence and API mocks, not live replay of the audited upstream runs.
The recovery wrapper was syntax-checked and its Python refresh behavior tested;
real systemd/network/credential behavior must be exercised on the target server.
No server deployment or GitHub Issue edits were made while building this release.
