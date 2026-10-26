# NPU Diffusion CI Kit

Evidence-backed daily reporting for SGLang diffusion CI on Huawei Ascend NPU.
The bot collects workflow/job logs and testcase metrics, investigates failure
signatures, and maintains incident continuity with conservative recovery checks.
It saves a complete English Markdown report and a concise GitHub Issue view.

Source repository: [OrangeRedeng/sglang-ci-npu-bot](https://github.com/OrangeRedeng/sglang-ci-npu-bot).
Publication uses the configured `TARGET_REPOSITORY`; hosting this source does not
change the deployed bot or its report destination.

Current source version: **22.2.0**. See [release notes](RELEASE-v22.2.0.md) for
collection limits, model-stage recovery, and offline replay instructions.


**v22.0.0 — Main-first research reporting and conservative Issue publication.**

Reads SGLang GitHub Actions (no upstream writes), retains bounded and redacted failure evidence, groups failed **testcases** (not just GitHub jobs), optionally investigates with GLM and pinned Ascend/SGLang skills, and publishes concise human-facing reports to `sglang-ci-npu-bot/sglang-ci-npu-bot` Issues.

## v22.0.0: Research-first and no PR-only Issue flood

Rooted in the 23 October audit of GitHub Issue #21: v21.0.3 mistook
`backend=hccl timeout=3600` initialization logs for failures and assigned
multiple ephemeral NPU-D IDs to unpublished observations. v22 removes this
false signal, parses pytest failures, and prioritizes actual main CI jobs
within the job-log budget.

The **default** publishes *one research-style daily Issue*, optionally up to
three validated main-impact incident Issues, and **zero new PR-only Issues**.
PR-only regressions are summarized in a bounded watchlist; all observations
are still retained in snapshot JSON. Existing historical Issues are never
bulk-closed or deleted. No new Issue gets an external NPU-D ID until the
GitHub creation succeeds. Existing incident fingerprint identity is retained.

The default GLM configuration for new installs is `auto` / `high`, with two
prioritized deep investigations and a 24k global output-token cap. It remains
subject to calibrated local monetary limits and per-request admission checks;
**existing `/etc/sglang-npu-bot/bot.env` is not overwritten**. Run
`sudo bash deploy/apply-research-profile.sh --preview` and optionally `--apply`
to switch legacy settings without changing secrets or monetary limits.

Installation, profile migration, limitations and backward compatibility:
`docs/RESEARCH_MODE_V22.md`. Validated on the offline regression suite; real
server installation is a separate step.

## v21.0.3: Verified testcase recovery and safe automatic incident closure

In addition to daily `new` / `continuing` / `reappeared` / `not observed` continuity,
this release optionally closes a historical incident **only when** an exact testcase
PASS appears in two different later successful CI runs with a comparable pinned
configuration, descending commit ancestry, complete scan coverage and no newer
same-matrix failure. `INCIDENT_VERIFY_PASSES=1` and `INCIDENT_AUTO_CLOSE=1`
are the default; `INCIDENT_AUTO_CLOSE_PR=0` prevents untrusted PR CI logs from
closing persistent Issues. No GLM analysis is involved in closure.

**Requires upstream SGLang CI instrumentation**: `NPU_CI_TEST_RESULT` on
both testcase PASS and FAIL with pinned model, seed, dtype, GT SHA, scheduler,
runtime ID. Old Issues without baseline evidence and capped 21 October scans
remain open, rather than being closed on disappearance. See
`docs/TESTCASE_PASS_VERIFICATION.md` for the log contract, 9 core proof tests,
preview commands and limitations.

Use `sudo bash deploy/update-from-git.sh --install` on the v21.0.3 tagged
checkout to update the running kit. Existing secrets, reports, and timer state
are preserved. No historical replay is run automatically by the installer.

## v21.0.1: Deployment self-test hotfix

The v21.0.0 runtime omitted deployment source files needed by `test_v21_regressions`;
`update-and-run.sh` therefore failed at its installed self-test and rolled back.
v21.0.1 deploys those files and validates both source and installed test trees.
It also preserves the previously deployed revision when installing from a newly
unpacked Git archive so `--rollback` returns to the correct release.

## v21: Verified PR attribution, main/PR counts, timer recovery and combined backfill

- Prevent GitHub's unrelated `push/main` pull-request associations from being
  displayed as tested-branch suspects (observed ghost links: PR #1 and #2).
  Only GitHub `pull_request`/`pull_request_target` run metadata can establish a
  tested PR; PR leads additionally require relevant code changes.
- Categorize code-path-unverified merged changes as **related_change**, not a
  possible cause; don't call hang/platform-gate fixes numerical-consistency
  fixes without matching mechanisms and testcase evidence.
- Render GLM's numbered hypotheses (`1. **H1 — …**`), prevent global synthesis
  remediation sections from being appended to NPU-D006, and suppress
  unverifiable PR numbers in embedded GLM narrative.
- Report *main*, *PR-only* and *unknown* failure counts separately, without
  treating the full cross-PR incident total as `main` failures.
- Enable timer catch-up (`Persistent=true`) and restore enabled-but-stopped
  timers after a successful update. Installer repairs ownership of only the
  issue-backup directory; it never recursively changes secrets or reports.

**One operational command after importing Git tag v21.0.1:** re-publish the
20 October report from the saved **completed** GLM analysis (no BigModel call),
then queue the 21 October live collection and restore the timer:

```bash
sudo bash /opt/npu-diffusion-ci-kit/deploy/update-and-run.sh
```

This is intended to be invoked **on 21 October 2026 Moscow time**. It
fails closed if run later, because a live scan cannot reconstruct an older
24-hour window. See `docs/GIT_DEPLOYMENT_RU.md` for archive import and
`--analysis` override. The 21 October daily run uses normal GLM budget limits.

## v20: Publish recovered paid investigations without re-querying GLM

If `investigate` successfully completed all three cluster calls but final synthesis
failed, the v19 refresh wrote a recovery Markdown. **Do not rerun GLM just to
publish those paid investigations.** After installing v20, inspect the first line
of the saved recovery to identify the synthesis error class:

```bash
sudo head -n 3 /home/sglang-npu-bot/reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md
```

Then update the **existing** Issue #1 and matching cluster Issues, explicitly
preserving the `partial` status and performing **zero BigModel API calls**:

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --allow-partial \
  --reuse-recovery reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md
```

This still reads existing GitHub Issues, validates fingerprint mapping, and
rediscovers verified candidate PR links via GitHub REST API. It makes no new CI
job scan and does not call GLM. Publication is **fail-closed** if any expected
incident lacks a substantive successful saved investigation, or when language
is not English. The summary prominently says synthesis was unavailable.
The `--reuse-analysis` option is reserved for a genuinely completed report,
and no longer silently upgrades a failed synthesis to `completed`.

For preview, replace `--publish` with `--preview`. A full *new* synthesis
requires GLM calls; one can alternatively run the normal refresh after
checking its estimated budget, but the three cached per-cluster calls may be
reused only when their complete prompts have not changed.

## v19: Fix high-reasoning refresh token starvation

The previous manual `--publish` command used `GLM_REASONING_EFFORT=high` but
inherited `GLM_MAX_OUTPUT_TOKENS=8000` from the daily profile. Both Qwen
and synthesis could return an empty visible answer after spending 8000 tokens
exclusively on model reasoning. v19 explicitly sets the manual budget in the
transient service while keeping the daily 8k cap unchanged:

| Manual GLM setting | Default |
| --- | ---: |
| `GLM_MAX_OUTPUT_TOKENS` | 24000 |
| `GLM_CLUSTER_MAX_TOKENS` | 20000 |
| `GLM_SYNTHESIS_MAX_TOKENS` | 24000 |
| `GLM_API_TIMEOUT_SECONDS` | 720 |
| `GLM_EMPTY_RETRIES` | 1 (only when reasoning exhausts the output budget) |

To refresh the original issues **with a new BigModel call**:

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish
```

An exact request checkpoint is used if available. Successful responses from
v17 were **not** checkpointed and cannot be replayed unless an independent
saved analysis file exists. v18/v19 successful stages are cached, but changing
reasoning or budget changes the cache key. If the request still fails, the
script prints journal diagnostics and leaves existing Issues unchanged. The
optional `--allow-partial` is a separate, explicit operator choice.

To tune just this manual refresh, override `GLM_REFRESH_*` through `sudo env`:

```bash
sudo env GLM_REFRESH_MAX_OUTPUT_TOKENS=28000 \
  GLM_REFRESH_CLUSTER_MAX_TOKENS=24000 \
  GLM_REFRESH_SYNTHESIS_MAX_TOKENS=28000 \
  bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish
```

This is a **local spending estimate only**, not a guarantee of provider billing;
check BigModel Console for actual charges. Manual refresh uses a separate
CNY 3/day local cap while daily generation continues with its own normal
configuration. The local ledger shares the same date and records all calls.

## v18: Issue-refresh failure recovery (2026-10-21)

v17's `update-existing-issues-with-glm.sh` exited without printing the systemd
journal on failure (`set -e`). It also discarded completed, paid GLM cluster
responses if the final synthesis timed out and refused any Issue updates if
`glm_analysis` returned `partial`.

v18 fixes the operator workflow:

- Prints the transient-unit journal **even on failure** and returns the true exit code.
- Saves each successful visible GLM response in a content-addressed, private
  `reports/reanalysis/glm-stage-cache/*.json` file. A repeat with the *same
  model, prompt, PR evidence, reasoning effort and token budget* reuses it and
  pays only for missing stages. Timed-out stages are never cached.
- Always saves GLM output to `reports/reanalysis/glm-refresh-recovery-*.md`
  before checking publication eligibility; this may include partial evidence.
- Explicit `--allow-partial` allows an **honestly labelled partial report**
  only if at least one cluster has meaningful generated analysis. No silently
  treating a failed synthesis as `completed`.
- Explicit `--reuse-analysis /path/to/full.md` publishes a previously saved
  English GLM analysis **without any new model calls**; verified PR discovery
  is still performed. The publication is marked as reused, not fresh.
- English-only, fingerprint validation, no new GitHub Issues, preservation
  of existing Issue bodies in backups, and local BigModel budget remain active.

### Recommended v18 recovery command

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --allow-partial
```

**No-charge publication using an older paid analysis** (only if the file exists):

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --reuse-analysis \
  reports/analysis/npu-diffusion-ci-2026-10-20-full.md
```

The `--reuse-analysis` path is relative to `/home/sglang-npu-bot`.
The v17 run that already failed **cannot be retroactively checkpointed**;
only responses previously saved separately in local analysis files can be
reused from that run. Investigate its precise error first via:

```bash
sudo journalctl -u npu-glm-issue-v17-20261020-338584.service -n 180 --no-pager -o cat
```

## New in v17

- **English-only publication** for daily and cluster Issues, including deterministic fix ideas and GLM synthesis. Model responses containing Cyrillic are not accepted for publication.
- **Bounded, GitHub-verified PR leads** (`pr_forensics.py`): distinguish tested branch context, merged-before-first-observed-failure suspects, and open *potential* fixes. Checks actual PR status, canonical URL, and changed filenames; correlation is never treated as proof.
- **GLM re-investigation of existing Issues** using the saved JSON snapshot (no full CI rescan), with verified stable issue IDs, optional run provenance, PR enrichment, local backups, and explicit `--publish` opt-in.
- **One-command English+PR refresh**: `sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh 2026-10-20 --publish` after installing v19. This calls BigModel and may incur costs, but downloads no CI logs and creates no new Issues.
- Existing taxonomy labels, SemVer Git updater, pinned agent skills and offline tests remain intact.

## New in v16

- **`main` (red)**: only confirmed failed runs actually executed on `sgl-project/sglang@main`; PR targets do not qualify.
- **`not-merged` (purple)**: failures confined to PR/unmerged branch runs; lower priority than `main`.
- **`unconfirmed` (lavender)**: unknown provenance, incomplete samples, or missing causal verification.
- **`flaky` (yellow)**: only with independently verified testcase-level instability; no automatic inference from a job-level pass.
- **`infrastructure` (blue-gray)**: runner, network, GitHub proxy or dependency download symptoms.
- Report and GLM ordering now put verified `main` incidents before PR-only and infrastructure cases, *independently of raw counts*. P1 defaults to confirmed main failures, P2 to PR-only issues, P3 to unconfirmed/infra unless the impact is independently established.
- Git SemVer history, reproducible Git tag releases, safe openEuler updater with rollback, offline GitHub issue refresh command.

## Installation (openEuler)

```bash
sudo git clone https://github.com/OrangeRedeng/npu-diffusion-ci-kit.git /opt/npu-diffusion-ci-kit
cd /opt/npu-diffusion-ci-kit
sudo bash deploy/update-from-git.sh --to v20.0.0
sudo systemctl enable --now npu-diffusion-ci.timer
```

**Until the remote repository is created and pushed**, install the included Git archive:

```bash
sudo tar -xzf npu-diffusion-ci-kit-v19-git.tar.gz -C /opt
sudo bash /opt/npu-diffusion-ci-kit/deploy/update-from-git.sh --install
```

Details: [Git deployment and updates](docs/GIT_DEPLOYMENT_RU.md) and [openEuler migration](SETUP_HYBRID_OPENEULER_RU.md).

## Refresh existing Issues with GLM + verified PR links

```bash
# Issue #1 and NPU-D004/D005/D006 (saved snapshot from 2026-10-20)
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh 2026-10-20 --publish
```

The command runs up to three per-cluster GLM investigations plus synthesis. It overrides the local GLM reservation ceiling to CNY 3/day and up to eight requests **only for this manual run** (budget and
rate limits apply). It saves backups of old Issue descriptions in `reports/issue-backups/`.
The optional `--preview` generates a draft without modifying any GitHub Issue but still
uses paid GLM calls. No scan of GitHub Actions or download of job logs is performed.

## Recover the 2026-10-20 English investigations for free (no GLM API)

v19 successfully completed the Qwen-Image, MiniMax-H3 and infrastructure cluster
investigations, but local budget protection blocked final synthesis (`GLMBudgetExceeded`).
The existing recorded `reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md`
contains those three investigations. Publish them **with a truthful partial status**:

```bash
# No BigModel requests and no Actions CI scan; GitHub API reads/writes are still made.
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues-with-glm.sh \
  2026-10-20 --publish --allow-partial \
  --reuse-recovery reports/reanalysis/glm-refresh-recovery-20261021T074819Z.md
```

The command verifies all three NPU-D IDs and existing fingerprinted Issues, English
language, and independently verifies PR metadata. It refuses a partial recovery if
not explicitly `--allow-partial`; no automatic claims of completed synthesis.
Use `--preview` in place of `--publish` to check formatting without mutations.

A fresh full synthesis requires one paid request; increase the local per-request
and daily reservation ceilings **explicitly**, after inspecting the budget ledger,
then rerun v20 to reuse content-addressed successful stage checkpoints when identical.

## Refresh today's/previous Issues without GLM or new full CI scan

```bash
sudo bash /home/sglang-npu-bot/scripts/update-existing-issues.sh 2026-10-20
```

## Update from Git

```bash
sudo bash /opt/npu-diffusion-ci-kit/deploy/update-from-git.sh --check
sudo bash /opt/npu-diffusion-ci-kit/deploy/update-from-git.sh --latest
```

### Security/operational rules

- No raw CI logs or API credentials committed to Git or published in Issues.
- Explicitly unverified main / last-green boundaries remain unconfirmed; PR base `main` ≠ main run.
- Bot Issues are separate from the source-code repository and SGLang upstream.
- Controlled local GLM token spending and no automated upstream fixes.
- Pinned skill files and SHA manifest are retained from v15.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

**GitHub Actions publisher isolation:** the optional fallback workflow is intentionally disabled under `examples/workflows/`; only the offline unit-test workflow runs automatically in this kit source repository. Report Issues remain in the separate bot repository.


## Cross-day incident continuity (v21.0.2)

Daily Issues now include a deterministic incident table compared with the
previously published date. `new` means absent from the previous dated report;
`continuing` means observed failed again; `reappeared` is a known fingerprint returning after a gap; `not observed` is NOT a verified fix.
Window caps, incomplete logs, different SHAs and skipped cases are not evidence
of a fix. Unobserved incident Issues are annotated and remain open. A later
recurrence preserves the same fingerprint-bound NPU-D identifier and reopens a
previously closed Issue. Successful publication archives old daily REPORT Issues,
not unresolved incident Issues; disable by setting
`ARCHIVE_PREVIOUS_DAILY_ISSUES=0` in bot.env.

To backfill the current/previously published daily report from an existing
JSON snapshot, with no GLM calls or CI rescan:

```bash
sudo bash /home/sglang-npu-bot/scripts/sync-incident-continuity.sh 2026-10-21 --preview
sudo bash /home/sglang-npu-bot/scripts/sync-incident-continuity.sh 2026-10-21 --publish
```

No persistent incident is automatically closed on absence alone. Verified
testcase-level PASS evidence on comparable code, GT, runtime, hardware and
branch scope must be established before closure; those checks are not present
in v21.0.2, so unresolved incident closure remains manual.

The default daily GLM profile remains economical `triage/low`. The existing
manual refresh has a separate `high` / 24000-token profile. Do not enable
`high` with `GLM_MAX_OUTPUT_TOKENS=8000`: reasoning may consume the entire
output budget, yielding empty visible content. Tune spending ceilings first.


## Historical Issue cleanup (v22.0.1)

To preview an administrative cleanup of old PR-only Issues (never deletion or falsely declaring fixed):

```bash
sudo bash /home/sglang-npu-bot/scripts/reconcile-legacy-issues.sh --preview
# after review:
sudo bash /home/sglang-npu-bot/scripts/reconcile-legacy-issues.sh --apply
```

See [migration policy](docs/LEGACY_ISSUE_MIGRATION_RU.md).

## P0 diagnostic and continuity patch (v22.0.2)

See [installation, offline historical repair, validation and limits](docs/P0_UPDATE_V2202.md).

## P0–P2 final release (v22.1.0)

See [RELEASE-v22.1.0.md](RELEASE-v22.1.0.md) for installation, one-command historical repair, existing Issue refresh/archival and a forced current-day launch.

## Reference-report alignment (v22.1.1)

See [REFERENCE-COMPARISON-v22.1.1.md](REFERENCE-COMPARISON-v22.1.1.md) and [RELEASE-v22.1.1.md](RELEASE-v22.1.1.md).

## Installation hotfix v22.1.2

[RELEASE-v22.1.2.md](RELEASE-v22.1.2.md) fixes historical snapshot contamination of installed self-tests without relaxing the report-length assertion.

## Synthesis-timeout recovery v22.1.3

See [RELEASE-v22.1.3.md](RELEASE-v22.1.3.md) to recover saved October 25 investigations without repeating paid historical calls.
