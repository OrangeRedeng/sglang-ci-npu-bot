# v22.1.3 — recover completed investigations after synthesis timeout

The supplied server log confirms v22.1.2 installed successfully, all 222 tests and
the live API preflight passed, and the research profile was applied. Historical
snapshot reparse completed for October 20–25. October 20 has 23 unavailable raw
logs; their old evidence is retained.

Both October 25 cluster investigations completed (IDs `82a8aa5e24e425` and
`2360050b453640`). The subsequent synthesis exceeded the 420-second transport
timeout. The refresh saved its partial result to:

`/home/sglang-npu-bot/reports/analysis/2026-10-25-refresh-failed.md`

The failed refresh did not publish that report. Its fail-closed exit also prevented
legacy reconciliation and the forced current-day scan in the combined command.
Do not rerun `repair-and-run.sh --apply --glm` just to recover these results:
that can repeat the completed paid historical investigations.

## Install the hotfix and resume

Copy `npu-diffusion-ci-kit-v22.1.3.bundle` to `/home/swx1199799/`. Wait for any
currently active analyzer to finish. Run this child-shell block:

```bash
sudo bash -e <<'SH'
cd /opt/npu-diffusion-ci-kit
git fetch /home/swx1199799/npu-diffusion-ci-kit-v22.1.3.bundle \
  refs/tags/v22.1.3:refs/tags/v22.1.3
git checkout --detach v22.1.3
bash deploy/update-from-git.sh --install

bash /home/sglang-npu-bot/deploy/recover-and-run.sh \
  --apply --date 2026-10-25 \
  --recovery reports/analysis/2026-10-25-refresh-failed.md \
  --run-today

systemctl enable --now npu-diffusion-ci.timer
systemctl list-timers npu-diffusion-ci.timer --no-pager
SH
```

Existing profile values, API keys, reports, the saved recovery file and monetary
caps are preserved; there is no need to apply the research profile again.

The recovery wrapper pauses/restores the previously active timer, holds the
maintenance lock and refuses to compete with an active publisher. It:

1. Validates the saved failed-synthesis Markdown against the selected dated snapshot
   and its known cluster fingerprints/IDs. Converts legacy raw fingerprints into
   observation references for rendering. Requires at least one substantial completed
   English section. Unknown/duplicate IDs, mismatched date and empty content fail
   closed before publication; explicitly failed cluster sections are omitted.
2. Updates the existing October 25 daily Issue as **partial: recovered completed
   investigations; synthesis unavailable**. Saves a pre-write Issue backup and
   rejects concurrent edits. No model request, upstream CI scan, new Issue, or
   incident closure is performed by the recovery refresh itself. Legacy model
   PR citations are filtered against retained verified PR evidence; old recovery
   files lack a snapshot digest and cannot establish causal truth.
3. Runs the legacy administrative reconciliation, preserving main incidents and
   archiving eligible PR-only Issues as not planned rather than fixed.
4. With --run-today, runs a fresh 24-hour current-day scan and normal GLM analysis.
   These are new current-day calls and can consume the existing budget. Normal
   conservative main incident creation/closure rules apply.

The original investigations are not rerun during historical recovery. Synthesis
is not retried during this recovery either. To recover/clean up without any new
paid model calls, omit `--run-today`. A preview can use `--preview` instead of
`--apply` and must omit `--run-today`.

## Future refreshes

New historical GLM refreshes enable completed-stage caching and atomically save
`reports/analysis/DATE-refresh-context.json` after each cluster investigation,
before synthesis, and on synthesis completion/failure. This contains the redacted
snapshot, verified PR evidence, telemetry and completed visible analyses. It does
not store private reasoning text or API credentials.

Synthesis uses `GLM_SYNTHESIS_TIMEOUT_SECONDS=900` by default (allowed 10–1200).
Cluster investigations still use the existing `GLM_API_TIMEOUT_SECONDS` (normally
420). This changes waiting time, not the output-token cap, retry count, request
quota or monetary limits. A longer timeout cannot guarantee completion; incomplete
historical output still requires explicit partial recovery before publication.

The timed-out old synthesis may have consumed provider tokens. Its reservation
remains uncertain; this release does not mark it free, erase spending records or
increase monetary caps. Provider-omitted historical usage stays unavailable.

## Verification

228 offline tests pass in the source tree, simulated installed layout, isolated
installer test entrypoint and extracted ZIP. Tests include the two reported
fingerprints, no-paid-call publication, invalid recovery rejection, explicit
partial authorization, transport timeout validation and pre-synthesis checkpoint
persistence. Shell syntax and Git bundle tag restoration are verified. These are
mocked recovery reproductions, not a live read of the server's saved Markdown.
No server deployment or GitHub Issue modification was performed while preparing
the release. If the server's recovery file fails validation, retain it and inspect
the stated mismatch instead of regenerating the paid historical analysis.
