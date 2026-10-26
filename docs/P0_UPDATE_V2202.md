# P0 update — v22.0.2

This patch addresses the three P0 findings in the October 26 audit. It does not
change the administrative PR-only archival policy or testcase PASS closure guard.

## What changes

1. **Diagnostic extraction.** Bare traceback headers are ignored. Pytest failure
   sections supply concrete exceptions independently for each testcase, preserving
   worker/backend failures instead of retry wrappers. Distributed execution error
   continuations are retained. Different HTTP statuses have different fingerprints.
2. **Testcase accounting.** A separate streaming pass reads every pytest terminal
   FAILED/ERROR node, including setup/teardown and parametrized cases. A complete
   `failure_cases` packet survives the 12/20-event evidence display limits. Quality
   events preserve SSIM/PSNR metrics and existing incident fingerprints. Explicit
   structured FAIL records with no exception remain unclassified observations.
3. **Cross-day correlation.** The last seven days of local daily JSON snapshots
   link matching testcase/scenario/scope/configuration observations, including
   PR-only observations with no GitHub Issue. Reports show previous and current
   symptoms. Job/run/SHA evidence remains in JSON; the same job appearing in two
   lookback windows is not counted as an independent reproduction.

Unknown runtime/GT equivalence is labelled `scenario only`; this is a search lead,
not proof of a shared root cause. Changes in known pinned configuration are kept
separate. Missing history does not establish first bad, last green, or a fix.

## Install

Copy `npu-diffusion-ci-kit-v22.0.2.bundle` to `/home/swx1199799/` and run:

```bash
set -e
cd /opt/npu-diffusion-ci-kit
sudo git fetch /home/swx1199799/npu-diffusion-ci-kit-v22.0.2.bundle \
  refs/tags/v22.0.2:refs/tags/v22.0.2
sudo git checkout --detach v22.0.2
sudo bash deploy/update-from-git.sh --install
```

The installer deploys the new modules. Existing configuration and reports are
preserved. No extra GLM budget or environment migration is required.

## Repair historical snapshot classification (offline)

Run when the scheduled analyzer is idle. Preview first:

```bash
sudo -u sglang-ci bash -c 'cd /home/sglang-npu-bot && /usr/bin/python3 scripts/reparse_failure_snapshots.py'
```

Apply to local daily JSON snapshots, preserving backups:

```bash
sudo -u sglang-ci bash -c 'cd /home/sglang-npu-bot && /usr/bin/python3 scripts/reparse_failure_snapshots.py --apply'
```

Backups are under `reports/p0-backups/<timestamp>/`. The command reparses retained
raw logs, not the truncated event display. Missing raw logs leave existing evidence
intact and are reported as `raw_logs_unavailable`; those lost details cannot be
reconstructed reliably. Old Markdown reports and GitHub Issues are not rewritten
by this offline operation.

## Validate and run

```bash
sudo bash /home/sglang-npu-bot/deploy/self-test.sh
sudo bash /home/sglang-npu-bot/deploy/run-smoke-test.sh
```

The smoke command uses a small sample and performs no GLM calls or Issue writes.
To produce the current daily report with the configured GLM and publication policy:

```bash
sudo systemctl start npu-diffusion-ci.service
sudo journalctl -u npu-diffusion-ci.service -n 100 --no-pager
```

## Limits and validation

The terminal-summary parser has an explicit 4096-case cap and overflow telemetry.
Truncated downloads, missing pytest summaries, unretained raw logs, and unavailable
historical snapshots remain coverage limits. It does not collect JUnit artifacts.
A terminal summary proves a failed testcase, not its underlying cause.

Regression tests use synthetic redacted reproductions of the audited symptoms,
plus the existing quality/log fixtures and all previous release tests. They do not
claim a live replay of October 24–25 upstream jobs. No server deployment or GitHub
publication was performed while preparing this release.
