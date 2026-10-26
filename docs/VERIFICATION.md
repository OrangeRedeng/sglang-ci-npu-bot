# Verification notes

Audit date: 2026-10-26. Source audited:
[`a62d87e`](https://github.com/OrangeRedeng/sglang-ci-npu-bot/commit/a62d87e4b5de9841c702643008bb24dd9f53e332).
The documentation cleanup does not change this source implementation.

## What passed

- All 246 offline tests pass in an isolated working directory. The source and
  extracted ZIP passed the same suite during packaging.
- The real log from SGLang job `121128243405` now retains the Wan2.1 performance
  assertion, a final FLUX testcase PASS, repeated execution attempts, and the
  missing-CUDA import path.
- Complete local Markdown and concise Issue previews are generated. Published
  source files were checked against the validated local Git tree.

These checks validate bot behavior on retained logs and fixtures. They do not
establish live GLM quality, production deployment, or NPU runtime correctness.

## Hosted CI

The [initial GitHub Actions run](https://github.com/OrangeRedeng/sglang-ci-npu-bot/actions/runs/40326244964)
failed before tests ran. `actions/setup-python` could not find Python 3.9 for
the Ubuntu 26.04 runner selected by `ubuntu-latest`. Python grammar checks on
the development host do not replace running the suite on Python 3.9.

## Remaining implementation gaps

The reporting plan is **partially implemented**, with these confirmed gaps:

| Area | Finding | Required follow-up |
| --- | --- | --- |
| Accuracy metrics | `parse_observations` overwrites summary metrics with each frame. A video summary with SSIM 0.7 can end up reported as 1.0 when its final frame passes. | Preserve testcase summaries and per-frame values separately. |
| Historical report registry | GLM receives saved-history green evidence, but the complete report rebuilds its registry without that history. A known earlier testcase PASS becomes unknown in the report. | Render both views from the same retained regression context. |
| Cache identity | The cache hashes bounded model messages. Changes to evidence omitted by context reduction can leave the cache key unchanged. | Include a digest of the complete relevant evidence before reduction. |
| Historical reparsing | `reparse_failure_snapshots.py` updates failure cases only. It does not attach the new execution observations, successful testcase records, or import-chain ownership. | Enrich retained jobs through the shared observation parser and preserve the historical cutoff. |
| Artifacts and configuration | Artifact listings and SHA-pinned source excerpts are retained, but artifact contents and a complete expected testcase inventory are not parsed. | Add bounded content inspection and explicit expected-versus-observed coverage. |
| Snapshot chronology | A job completed after the frozen snapshot is marked active, but its test-step outcomes are copied without an equivalent timestamp cutoff. | Apply the snapshot cutoff to step and parent outcomes as well. |

The first three findings were reproduced independently of the existing test
suite. Passing that suite therefore does not mean the full plan is complete.

## Operational consequences

Historical refresh can recover performance assertions from retained failure
logs, but it does not yet recover all of the new observation fields. Missing
or expired evidence remains unknown. Reusing a historical investigation does
not turn a missing synthesis into a completed analysis.

The existing automatic-closure contract still requires controlled comparable
testcase evidence. Ordinary pytest PASS observations do not satisfy it.
