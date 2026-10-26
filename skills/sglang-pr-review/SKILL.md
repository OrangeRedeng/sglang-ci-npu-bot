---
name: sglang-pr-review
description: Review an SGLang pull request for actionable correctness/regression findings without modifying code.
---
# SGLang PR review

1. For broad review, build one bounded first-pass packet with `.codex/scripts/workflow-review-packet.py --pr <N>` or `--base <ref>` instead of iteratively reconstructing the PR through many small retrieval calls.
2. Use the packet's changed-file/component index and narrow (~20-30 line) hunks. Expand only suspicious code, callers, invariants, or regression hypotheses.
3. Load only applicable SGLang rules/skills. Reuse a `SKILL.md` already read in the session unless the file changed or a specific missing section is required.
4. Never start broad review with multi-file `--unified=70`, `--unified=80`, or similar large-context diffs.
5. Review correctness/regressions -> backend portability -> NPU behavior if relevant -> state/concurrency -> duplication/dead code -> test value.
6. Keep a short coverage checklist for a large review.
7. Do not edit code.
8. If actionable findings are discovered and are not already authoritative unresolved GitHub review threads, run `.codex/scripts/new-handoff.sh <N> [topic]` and write only actionable implementation data into the resulting handoff.
9. If no actionable findings exist, do not create an empty handoff.
10. Stop after all changed code paths are covered; do not spend additional tool calls proving already-established facts.
