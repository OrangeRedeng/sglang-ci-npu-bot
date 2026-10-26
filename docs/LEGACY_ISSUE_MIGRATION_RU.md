# NPU CI v22.0.1: reconciliation of existing GitHub Issues

The historical incident cleanup is an **administrative archive**, never a claim that
an underlying PR failure was fixed. Do not delete Issues: retained CI job links and
fingerprints are essential for regression history.

## Policy

- Only Issues labeled `npu-diffusion-cluster` with an existing validated
  `<!-- npu-diffusion-fingerprint:... -->` marker and NPU-D number qualify.
- Issues labeled `main` are retained open (only the title may be normalized).
  A separate existing testcase PASS verifier decides whether a main incident is fixed.
- Open incident Issues labeled `not-merged` but not `main` are closed as
  `not_planned` and receive `PR-only archived` in the title. Their body is
  **appended**, never overwritten, and explicitly says **not fixed**.
- Unconfirmed-provenance Issues and third-party/human Issues are not changed.
- Older daily report Issues are closed as completed; the latest daily remains open.
- Latest daily receives one replaceable migration note explaining ghost NPU-D IDs
  and linking users to PR-only watchlists in subsequent research reports.
- Dry-run issues **no writes**; applying backs up all issue payloads with mode 0600
  to `reports/issue-backups/reconcile-legacy-issues-TIMESTAMP.json` before any mutation.
- Before each write, the issue is reloaded and body/title/state/labels must still
  match the planned source. Conflicts are skipped, never force-overwritten.
- Running the migration twice is idempotent; a failure during GitHub API writes
  may leave a partial migration. Re-run to complete; original backup is retained.

## Server commands

Run after installing v22.0.1 (latest kit) on openEuler:

```bash
sudo bash /home/sglang-npu-bot/scripts/reconcile-legacy-issues.sh --preview
sudo bash /home/sglang-npu-bot/scripts/reconcile-legacy-issues.sh --apply
```

The wrapper executes with the service user and EnvironmentFile under systemd.
No model calls, rescans, deletion, or changes to GLM budgets.

## Verified main fixes

For main incidents, `INCIDENT_AUTO_CLOSE=1` still requires exact verified
same-testcase PASS evidence, as defined in `TESTCASE_PASS_VERIFICATION.md`.
Administrative archiving and verified resolution are separate lifecycle states.

Use `sync-incident-continuity.sh YYYY-MM-DD --preview --verify-passes` and then
`--publish --verify-passes` to refresh/close main incidents when structured
passing logs and comparable runtime/commit evidence exist. Do not assume
that lack of failures in a capped 24h scan proves a fix.
