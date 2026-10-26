# v22.2.0 — evidence-backed daily CI reports

The bot now keeps a complete local English report alongside the concise GitHub
Issue view. It collects main runs independently of capped PR queries, retains
test-step outcomes, parses actual testcase executions, and preserves the
exception chain needed to investigate shared failures.

## Report artifacts

- `reports/npu-diffusion-ci-YYYY-MM-DD.md`: complete eleven-section report,
  including materialized run/job ledgers, testcase attempts, metrics, source
  evidence, history, and closure criteria.
- `reports/npu-diffusion-ci-YYYY-MM-DD-issue.md`: concise publication body.
- Existing reanalysis/reformat commands also save a `-complete.md` companion;
  historical daily refresh saves the complete report and an `-issue.md` draft.
- Original paid analysis, snapshots, and confidential raw logs remain separate.

No local path is presented as a public report download URL. Artifact listings
are explicitly distinguished from verified artifact contents.

## Evidence fixes

The real job linked from bot issue #23 uses pytest headers with two underscores.
The old parser required three and discarded the performance assertion. The
regression fixture now retains approximately 57.09 seconds actual, 28.19 seconds
baseline, and a 35.24-second gate for Wan2.1.

Repeated pytest sections and actual execution retries are retained separately.
The same source log contains a final FLUX testcase PASS and seven failing
attempts each for Wan2.1 and JoyAI. Both failing scenarios log the missing CUDA
import in `rmsnorm_interleaved_rope_cutedsl.py`; the report preserves that import
path without declaring the co-occurring slowdown causally proven.

Normal testcase PASS evidence is separate from the controlled machine-readable
profile needed for automatic incident closure. Scheduled and manually dispatched
main runs now use the same provenance rules as push/main verification. Existing
incident IDs can be recovered through exact historical fingerprint aliases;
the release does not renumber incidents or import handoff IDs as live Issues.

## Collection and model recovery

Main discovery reads at most 32 runs from the preceding seven days. Daily
enumeration and main comparison evidence are recorded separately. Existing
failed-log and pass-log budgets remain in force; selected latest-main logs
receive priority. Historical comparisons never inflate daily job counts.
Source/artifact evidence is bounded to four representative runs and three
SHA-pinned source files per SHA. Listing/contents gaps and all caps are disclosed.

Relevant PR metadata and code excerpts are collected even with `--no-glm`.
GLM payloads retain branch/repository provenance and valid JSON under their
context limits. Completed stages are cached by evidence, prompt version,
endpoint, model, and reasoning/token settings. Synthesis retries reuse matching
investigations; changed evidence invalidates them. Cached stages make no model
request. Transport timeouts retain their existing uncertain-cost treatment.

`GLM_STAGE_CACHE_DIR` defaults to `reports/analysis/stage-cache`; explicitly set
it to an empty value to disable caching. Completed-stage recovery contexts are
saved under `reports/analysis/` unless overridden. Monetary caps, tariffs, retry
counts, and token limits are unchanged.

## Offline replay

```bash
python3 scripts/preview_report.py \
  --snapshot /path/to/retained-snapshot.json \
  --log JOB_ID=/path/to/retained-job.log \
  --output /path/to/preview
```

The command imports no GitHub client instance and makes no model requests or
GitHub writes. Job IDs must belong to the snapshot. Timestamped lines after its
frozen UTC snapshot are excluded. The complete Markdown, concise Issue draft,
and enriched JSON are written locally. Missing historical logs/configuration
remain unknown; a single-job replay is not a daily census.

## Validation boundaries

Offline tests exercise the source log excerpt, retry/metric isolation, main
discovery under PR caps, cancelled test-step failures, queued/skipped jobs,
historical comparison accounting, identity aliases, model-stage reuse, and
future-evidence rejection. Existing HTTP, budget, lifecycle, and publication
guards remain covered. This is bot validation; it does not validate an NPU
runtime fix or a live paid GLM response.
