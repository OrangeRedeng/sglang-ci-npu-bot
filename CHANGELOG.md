# Changelog

## 22.2.2

- Install every Python helper so installed reports and tests can import the
  same modules as the source checkout. Existing CLI permissions are preserved.
- Run the full suite from an installed tree in CI, including checks that
  updates preserve configuration and saved reports.

## 22.2.1

- Reports now finish rendering when an investigation includes PR links. Previously,
  the PR-only watchlist could crash after the paid synthesis had completed.
- A saved completed synthesis can be turned back into a daily report without
  another GLM request. New saved analyses are checked against their snapshots;
  older files need explicit recovery approval.
- Preserve video summary/frame metrics, saved-history greens, full-evidence
  cache identity, and timestamp cutoffs during collection and reparsing.
- Pin hosted tests to a runner with a published Python 3.9 build.
- Clarify updating an existing checkout and recovering a paid report.

## 22.2.0

- Recover real pytest sections, retry measurements, final testcase verdicts, and import chains.
- Collect main comparison evidence independently and retain test-step/provenance details.
- Save complete Markdown ledgers alongside concise Issue drafts.
- Resume exact-evidence GLM stages and reject post-snapshot evidence in offline replay.

## v22.1.3 — 2026-10-26

- Recover completed cluster investigations after a timed-out historical synthesis
  using --reuse-investigations and explicit --allow-partial. Validate dated filename,
  cluster IDs, English content and section completeness; do not make model calls.
- Publish recovered content only as partial; do not claim synthesis completed,
  invent missing provider usage, or update incident RCA from the legacy recovery.
- Preserve completed stage cache and atomic context checkpoints (snapshot, PR
  evidence, telemetry, analyses) before synthesis on future historical refreshes.
- Use a separate synthesis timeout: GLM_SYNTHESIS_TIMEOUT_SECONDS, default 900s;
  cluster requests keep their existing timeout. Monetary caps remain unchanged.
- Add recover-and-run.sh: restore the saved daily analysis, reconcile legacy Issues
  and optionally run today's scan without repeating historical log reparsing or
  paid investigations. Existing maintenance lock/timer guards are retained.

Validation: 228 offline tests, including no-paid-call recovery and forced synthesis
failure with a context checkpoint verified before the failing request.

## v22.1.2 — 2026-10-26

- Fix the v22.1.1 installed self-test failure (`11492 not less than 11000`):
  report rendering no longer loads reports/snapshots from the current directory.
  Seven-day coverage remains supplied explicitly by the collector/refresh flow.
- Run installer unit tests in an isolated temporary working directory with the
  installed package on PYTHONPATH. Production reports and stage caches cannot
  influence the regression fixtures through relative paths.
- Add regression coverage with 100 unrelated saved-history workflows and verify
  that explicitly supplied historical coverage still renders without disk reads.

Validation: 222 unit tests in source and installed/packaged layouts, including
an installed layout populated with historical snapshots. Report-length contract
remains unchanged; no limit was increased to mask the failure.

## v22.1.1 — 2026-10-26

Reference-report alignment against npu-diffusion-ci-2026-10-08-en.md.

- Parse the actual SGLang `Actual / Expected / Limit` performance assertion;
  distinguish relative tolerance from absolute padding and preserve metric names.
- Preserve quantitative evidence and observed HTTP codes through long traceback
  compaction, separately for each testcase. HTTP code co-occurrence is not proof
  of a causal chain.
- Add a quantitative engineering assessment, workflow/provenance execution table,
  SHA-specific proposed experiments, acceptance criteria and missing-profile view.
- Preserve Markdown in expandable GLM analysis and suppress lines referencing
  unverified PR numbers. Current-day trend data wins over same-date history.
- Keep main-first priorities, administrative PR-only archival and conservative
  testcase PASS closure rules. Historical reference priorities are not copied.

Validation: 220 unit tests, including seven reference-format regression cases.

## v22.1.0 — 2026-10-26

Final release for the October 26 P0–P2 audit. Includes all v22.0.2 P0 fixes.

- P1: explicit auto-investigation selection for main failures and repeated PR-only
  families: >=3 distinct jobs on >=2 PRs; >=10 jobs over >=3 runs; or critical
  GLM-Image/Wan/MiniMax/Qwen-Image scenarios with >=3 jobs over >=2 runs.
  This does not create PR-only Issues or establish a common root cause.
- P1: bounded open/merged PR discovery with author, updated time, relevant paths,
  bounded diff excerpts, observed tested SHAs, current head SHA and CI statuses +
  check-runs. Query failures count against the API budget. Truncated file/check
  lists and unavailable evidence remain explicit. No unverified fix promotion.
- P1: testcase-isolated explicit actual/baseline/threshold latency measurements,
  unit conversion, relative regression and tolerance; runner name/labels retained.
- P2: per-stage/per-attempt GLM effort, output budget, completion status,
  provider-reported input/output/reasoning tokens and elapsed time persisted in
  snapshot JSON. No private reasoning text. Missing usage is not zero.
- P2: seven-day observed workflow/scenario/scope matrix, with job PASS separate
  from explicit testcase PASS. Duplicate job attempts across windows are counted
  once. Missing testcase execution remains unknown, not inferred green.
- Recovery: new existing-daily refresh accepts unpublished PR-only observations,
  saves backups/drafts and rejects concurrent daily edits. It never creates or
  closes Issues. Optional paid analysis fails closed on incomplete GLM output.
- Deployment: repair-and-run.sh pauses/restores an active timer, refuses an active
  publisher, reparses retained logs, refreshes the latest existing daily, applies
  legacy archival and optionally forces a new current-day analysis.
- Research profile: high effort, two deep investigations, 100 failed-job log cap,
  38-request PR search cap, expandable full GLM report. Financial caps unchanged.

Validation: 213 offline unit tests, followed by package and installation checks.
These checks did not include live upstream replay or deployment.

## v22.0.2 — 2026-10-26

- P0: separate streaming pytest FAILED/ERROR accounting from bounded log display;
  retain testcase-specific exceptions and all summary verdicts up to an explicit
  4096-case cap, including parameter IDs with spaces and setup/teardown errors.
- P0: discard traceback headers as diagnostic signals; prefer concrete exceptions
  over scheduler/retry wrappers, retain distributed configuration continuations,
  and distinguish HTTP statuses in semantic symptom fingerprints.
- P0: prefer the complete versioned failure_cases packet over truncated events.
  Preserve consistency metrics and historical fingerprints for quality incidents.
- P0: seven-day local snapshot correlation includes unpublished PR-only failures,
  previous/current signatures, first/last observations, SHA/job evidence, scope,
  and available pinned runtime/GT configuration. Unknown equivalence is explicit;
  overlapping job windows do not count as independent reproductions.
- Include symptom history in daily reports and GLM evidence. Watchlist rows retain
  distinct diagnostic signatures. No changed symptom or absent failure proves a fix.
- Offline historical reparse command supports preview, per-snapshot backups, and
  explicit missing-log reporting. It makes no GitHub or GLM calls.

## v22.0.1 — legacy Issues reconciliation

- Add safe, repeatable read-only preview and opt-in administrative archive of existing PR-only incident Issues. Main incidents are preserved until verified testcase PASS.
- Keep historical failure evidence and links in closed PR-only Issues; no deletion or false "fixed" claims.
- Improve legacy Issue titles, record a migration note on latest daily report, and make off-server backups before any write.
- Reject changed Issues on optimistic concurrency recheck.

# v22.0.0 — Main-first evidence triage and research-style reporting

- Ignore normal HCCL distributed-init lines (`backend=hccl timeout=3600`), normalize inner timestamps, prefer pytest terminal failures to incidental startup warnings.
- Retain distinct performance vs consistency testcase failure families; arbitrary pytest cases (including GLM-Image distributed tests) are diagnosable without fabricating an Ascend runtime root cause.
- Priority collection: confirmed main runs before PR-only jobs when bounded `MAX_JOB_LOGS` would otherwise miss important regressions.
- Issue creation follows the optional GLM analysis instead of preceding it. Existing fingerprints keep historic IDs; never assign NPU-D IDs to unpublished observations.
- Default publish: no new PR-only Issues, maximum 3 main-branch incident Issues per day, and no new Issues for weak/unclassified signals. Existing PR-only Issues are preserved, not silently rewritten or closed.
- Research daily: main-first incidence and investigation, compact PR-only watchlist, linked and status-labelled PR leads, lifecycle counts, 7-day completed-job statistics with sampling caveats.
- GLM defaults: `auto` with high reasoning, 24k max output, two targeted investigations; keep monetary budget guardrails. Explicit legacy `bot.env` upgrade helper with backup and no monetary/secret changes.
- Add regression tests for fake HCCL incidents, GLM-Image HTTP 500 pytest, ghost IDs, PR-only suppression, existing Issue preservation and report-size caps; installer includes the new module/tests.
- Review existing #13-#20 separately; no automatic retroactive close or rewrite.

# v21.0.3 — Verified testcase recovery and automatic Issue closure

- Bounded read-only GitHub success-log verifier: one job `success` is not a testcase PASS. Require a structured PASS for exactly the historical failed testcase and configuration.
- Preserve structured FAIL evidence from failed CI logs in saved snapshots; require full pinned GT SHA, seed, dtype, scheduler, model, runtime identity and all optional supplied configuration.
- Require two independent new-commit successful workflow runs, same workflow/matrix, scope and source repository, GitHub-verified descendant commit ancestry, and no subsequent matching matrix failures.
- Fail closed for capped/partial scans, downloaded-log gaps or truncation, missing historical FAIL, absent provenance, same SHA retries, changed CI configuration, divergent commits or mismatched Issue fingerprints.
- Close only after daily report publication; re-read Issue and reject concurrent edits before atomic GitHub `completed` state patch. Preserve manual notes and reopen exact fingerprints on recurrence.
- PR-scoped auto-close disabled by default as PR logs may be controlled by untrusted code. Opt-in only after audit; defaults configurable in bot.env.
- Add `--verify-passes` preview/publish option to offline continuity wrapper; it reads bounded job logs but does not rescan workflows or call GLM.
- Upstream CI must emit `NPU_CI_TEST_RESULT` on both PASS and FAIL; without it older incidents stay open. See `docs/TESTCASE_PASS_VERIFICATION.md`.
- Offline source + deployment suite updated with additional proof and safety cases.

# v21.0.2 — Incident lifecycle and cross-day continuity

- Deterministic cross-day new/continuing/reappeared/not-observed status, comparing IDs with the preceding daily Issue.
- No false fixed conclusion on capped queries, missing jobs, incomplete failed log coverage or missing testcase-level PASS.
- History fed into bounded GLM evidence, without delegating closure to the LLM.
- Prior dated daily report Issues automatically archived after a successful new publication (opt-out via ARCHIVE_PREVIOUS_DAILY_ISSUES=0). Persistent incident Issues remain open.
- Offline `sync-incident-continuity.py` / systemd wrapper reuses saved JSON to refresh existing GitHub Issues without new GLM calls or CI scan.
- SSL certificate failures correctly receive the infrastructure taxonomy label when classification evidence is available.

## v21.0.1 — 2026-10-21

- Fix installed self-test failing with `FileNotFoundError` for `deploy/systemd/npu-diffusion-ci.timer`: install a self-contained mirror of the regression-test deployment inputs (`deploy/systemd/*`, `update-from-git.sh`, `install-openeuler.sh`, `VERSION`).
- Add source-tree regression guard to catch missing installer copy steps before deployment; offline tests now cover both source and installed tree contract.
- Allow the combined historical-refresh and daily-run script to accept v21.0.x patch releases.
- Record the previously *deployed* Git SHA for rollback when installing from a newly unpacked tagged checkout (instead of incorrectly recording the new checkout HEAD).
- Preserve existing systemd timer enablement, historical saved GLM analysis and all config, secrets and reports.

## v21.0.0 — 2026-10-21

- Remove spurious historical PR #1/#2 attribution from `push/main` GitHub Actions data; explicitly reclassify stale PR metadata from old snapshots, select PR-only runs by event, rank links by observed occurrence rather than PR number.
- Require relevant changed files for every tested PR link; downgrade upstream PRs without verified executed code paths to `related_change`, rather than present them as proven causes or fixes. Hang/platform-specific changes are not assumed to solve consistency metrics.
- Show per-incident branch provenance breakdown (actual main, PR and unknown jobs); keep real main P1 priority without implying all incident failures occurred on main.
- Parse numbered GLM H1/H2 hypotheses and fence synthesis-wide fix sections from incident-specific bodies; suppress references to unvalidated PRs in GLM excerpts.
- Fix systemd timer being enabled but inactive after deployment: restore active or enabled state; use Persistent=true for missed calendar ticks.
- Repair ownership of the specific `reports/issue-backups` directory during install.
- Add `deploy/update-and-run.sh` to install v21 (with timer deferred), refresh 2026-10-20 from its already paid full English GLM report, then start the 2026-10-21 daily service asynchronously and re-enable timer.
- Extend offline regression tests for ghost PR metadata, English GLM extracts and timer lifecycle; installer now deploys all shipped test files; no BigModel calls in historical refresh.

## v20.0.0 — 2026-10-21

- Restore published English Issues from saved full per-cluster GLM analysis when the final synthesis is blocked by `GLMBudgetExceeded`. `--reuse-recovery FILE --allow-partial` performs NO paid model calls, clearly discloses the missing final synthesis, and refuses partial/mismatched incident IDs.
- Harden `--reuse-analysis` against accidentally labelling synthesis-failed recovery as a complete report.
- Add actionable budget rejection diagnostics distinguishing per-call estimate, daily CNY cap and daily request-count cap; no secrets logged.
- Emit synthesis failure type to the systemd log while retaining completed GLM stage checkpoints for optional synthesis-only retry.
- Keep full English content, verified PR links and exact Issue fingerprint checks.  Preserve v19 defaults for daily economic reporting and currency limits.
- Package Git artifacts with sanitized reproducible modification times and without ignored reports or credentials.

## v20.0.0 — 2026-10-21

- Add `--reuse-recovery` for a failed-synthesis recovery Markdown: offline reuse of already paid, successful cluster investigations without any new GLM call. Publication requires explicit `--allow-partial`.
- Verify every expected NPU-Dxxx stable ID is present exactly once, contains substantive evidence, and matches fingerprint-verified existing Issues; reject unrelated, incomplete or mixed-language recovery files.
- Preserve truthful `partial: recovered investigations; synthesis unavailable` status. `--reuse-analysis` now rejects failed synthesis recoveries instead of mislabeling them complete.
- Report safe synthesis failure exception class to systemd journal to distinguish local `GLMBudgetExceeded` from `TimeoutError`, `HTTPError`, etc. Exact source evidence remains in saved recovery.
- Keep verified PR candidate discovery and English-only publication; no CI re-scan or new Issues. Add offline end-to-end recovery publication test; 136 tests passed.
- Package clean tracked Git sources with stable archive timestamps (avoid harmless but noisy future-timestamp warnings on hosts with clock skew).

## v19.0.0 — 2026-10-21

- Fix v17/v18 manual GLM Issue refresh inheriting the daily `GLM_MAX_OUTPUT_TOKENS=8000` ceiling despite `reasoning_effort=high`, resulting in `finish_reason=length`, reasoning-only responses, and refused publication.
- Force a separate manual profile via `systemd-run /usr/bin/env`: output ceiling 24000, cluster 20000, synthesis 24000, timeout 720s, one retry only on reasoning-token exhaustion, with checked configurable `GLM_REFRESH_*` overrides.
- Validate profile at startup, rejecting stage budgets greater than the global cap and high/max reasoning with an impractically low cap before any GLM billing.
- Retain v18 stage checkpoints, safe partial publication, unchanged daily economy profile, English-only reports, fingerprint checks and PR evidence verification.
- Add shell-profile tests for the inherited cap regression.

## v18.0.0 — 2026-10-21

- Correct v17 refresh-shell exit behavior: always print systemd journal on failed transient service.
- Checkpoint each successful GLM request and reuse only on exact request identity; preserve partial generated analysis before publication checks.
- Add explicit `--allow-partial` publishing policy, with clear status labels and minimum content validation.
- Add `--reuse-analysis` to recover an already paid English GLM result and rediscover verified PR URLs without new model calls.
- Preserve all protections: saved snapshots, immutable fingerprint mapping, root-owned installer, English-only Issues, GitHub API PR verification, local usage budget.
- Tests for stage-cache replay/invalidation, timeout exclusion and publication eligibility.

# Changelog

## v16.0.0 — Git-native release (2026-10-21)

- Convert the v15 ZIP kit to a standalone, versioned Git repository with SemVer tags, source bundle and updater.
- Capture real GitHub run provenance (`run_event`, `head_branch`, `head_repository`) in the snapshot.
- Scope incident priority to **verified main runs**, PR-only `not-merged`, or `unconfirmed` when evidence is incomplete.
- Mark verified GitHub network/runner artifacts as `infrastructure`; label `flaky` only with independently confirmed testcase-level intermittent evidence.
- Create colored GitHub Issue labels and preserve user-applied labels during scope transitions.
- Prioritize `main` clusters both in the human-facing report and in deep GLM investigation order.
- Add a safe standalone updater (`--check`, `--install`, `--latest`, `--to vX.Y.Z`, `--rollback`) with installed-code backup and timer preservation.
- Add historical Issue synchronizer (no new CI collection and **no GLM calls**) with bounded GitHub run metadata enrichment.

## v15.0.0 — Imported baseline

- Human-first reports, separate cluster analyses, single-command report reformatting.
- GLM reasoning modes and local budget, testcase-level multi-failure clustering, locally pinned Ascend skills.
