# v22.0.0 — Main-first research mode and Issue creation safety

## Main changes

1. **False signal removed.** `world_size=... backend=hccl timeout=3600` is a configuration log, not an HCCL timeout. Timestamp normalization handles `[10-23 06:02:20]` and pytest terminal failure lines are preferred over noisy setup lines. A pytest failure can be retained as `pytest_failure` for non-parametrized GLM-Image tests. Exact consistency and performance failures remain distinct families.
2. **Main-first.** Bounded raw-log download prioritizes confirmed main runs; classification/issue ordering places main ahead of PR-only. Scope comes from GitHub *run event + actual branch + repository*, never PR base or a linked merged PR.
3. **No PR-only Issue storm.** `CREATE_PR_ONLY_ISSUES=0` and `MAX_PUBLISHED_MAIN_ISSUES=3` by default. Existing PR-only Issues are retained but not replaced/duplicated; PR failures are in a bounded watchlist and full local JSON. Unknown/ambiguous errors are never promoted to new Issues.
4. **No ghost D IDs.** New NPU-D IDs are allocated only following successful Issue creation after optional GLM analysis. Unpublished observations use local `OBS-<fingerprint>` identifiers, never misleading NPU-D IDs.
5. **Research report.** Main-impact summary and mechanistic verification steps; bounded PR-only watchlist, metadata-linked PR matrix, continuity summary, 7-day sample metrics and coverage caveats. No PR attribution from an LLM without the GitHub-verified changed-file evidence.
6. **Higher reasoning defaults.** New install: `auto`, `high`, 24k global output cap, 16k cluster, 20k synthesis, two deep investigations. Cost/token use is still bounded by **existing** locally calibrated GLM_BUDGET settings. If an existing `bot.env` still specifies `low` and `8000`, the installer intentionally leaves these secrets/configuration unchanged.

## Deployment to openEuler

Transfer the v22 `.bundle` into `/home/swx1199799`, then:

```bash
set -e
cd /opt/npu-diffusion-ci-kit
sudo git fetch /home/swx1199799/npu-diffusion-ci-kit-v22.0.0.bundle refs/tags/v22.0.0:refs/tags/v22.0.0
sudo git checkout --detach v22.0.0
sudo bash deploy/update-from-git.sh --install
sudo bash deploy/apply-research-profile.sh --preview
# Inspect prices / available daily budget. Then, if desired:
# sudo bash deploy/apply-research-profile.sh --apply
```

With `--apply`, `bot.env` is backed up in the same directory with mode 0600. The script never alters API keys, budget prices, or monetary safety caps. A high cap can still produce a model-budget rejection if the actual price/tokens exceed configured local limits.

**Safe dry-run:**

```bash
sudo -u sglang-ci bash -c 'cd /home/sglang-npu-bot && /usr/bin/python3 scripts/npu_diffusion_ci_report.py --dry-run --no-glm'
```

Check `reports/npu-diffusion-ci-YYYY-MM-DD.md`; no GitHub Issues writes or model calls on dry-run. Before allowing live publish verify `MAX_RUNS_PER_WORKFLOW`, `MAX_JOB_LOGS`, and network coverage. Do **not** rerun a published day expecting replacement without checking the idempotence logic and saved snapshot coverage.

## Existing Issues (#2–#20)

No existing Issue is automatically closed as a duplicate or marked fixed by v22. Historical PR-only Issues remain, but new PR-only findings are not published as individual Issues. To clean up misleading #13–#20, first inspect the original pytest traceback; some represent genuine GLM-Image/other regressions. Apply reviewed labels/links, then close as superseded only when the correct replacement has been established. The verified-testcase-PASS closure logic remains conservative.

## Reliability limitations

- Generic pytest failures are categorized by testcase but the *underlying* backend cause remains unverified without a traceback; this is not automatically a proven root cause.
- PR metadata lookup is bounded. `ci_status=not checked` is deliberately explicit; CI conclusion/mergeability is not invented.
- Seven-day percentages use completed jobs in the observed scan, not comparable repeated cohorts. CI pagination/log limits may bias trends.
- Existing `bot.env` is preserved; opt-in migration required for high reasoning.
- Snapshot replay is deterministic offline. Real GitHub and GLM behavior must be validated with preview before live publication.
