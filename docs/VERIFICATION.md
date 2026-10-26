# Verification notes

## Current source checks

The offline suite passes 260 tests in both the source and installed trees.
The installer runs in a temporary fixture with account and service operations
stubbed; actual file copies, module imports, permissions, and preservation of
configuration and reports are checked. The full suite then runs from that
installed tree in an isolated working directory.
Tests cover the production crash supplied on October 26, saved-synthesis recovery,
metric isolation, history, cache identity, and snapshot cutoffs. These checks do
not establish live GLM quality or deployment on the reporting host.

The production crash was a local-variable collision in the research renderer:
a loop over rendered PR links replaced the PR-cluster list with a string.
Rendering then failed after the paid synthesis had completed. The regression
now includes both main and PR clusters with verified PR links.

## Fixed in this source

- PR-link rendering preserves the PR-cluster list.
- Testcase summary metrics stay separate from per-frame values.
- The complete report uses the same saved regression context as GLM.
- Cache keys include an unbounded-evidence digest before model context reduction.
- Historical reparsing retains PASS observations and import ownership, respects
  the frozen snapshot, and can be limited to selected dates.
- Test-step and parent outcomes completed after the snapshot are not reported
  as known outcomes at that snapshot.
- Completed synthesis is saved with snapshot/content hashes. Recovery refuses
  mismatches and publishes only a selected daily report when requested.
- Hosted tests use Ubuntu 22.04. The official
  [Python build manifest](https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json)
  lists an x64 Python 3.9 build for this runner.

The [initial hosted run](https://github.com/OrangeRedeng/sglang-ci-npu-bot/actions/runs/40326244964)
failed during Python setup on Ubuntu 26.04 before tests ran. The runner change
requires a new hosted run after publication; local grammar checks do not replace
running Python 3.9 in CI.

## Remaining limitations

Artifact listings and SHA-pinned source excerpts are collected, but artifact
contents and a complete expected testcase inventory are not parsed. Coverage
remains bounded and must not be presented as an exhaustive daily census.

Legacy synthesis files have no snapshot/content binding. Recovery requires
explicit `--allow-legacy-analysis`, a matching dated filename, and a snapshot
recording completed synthesis. The report states that exact binding is
unavailable. Partial or failed synthesis is rejected by this recovery command.

Ordinary pytest PASS observations do not satisfy the controlled configuration
and repeated-run evidence required for automatic incident closure. Daily report
recovery performs no incident creation or closure and makes no model calls.
