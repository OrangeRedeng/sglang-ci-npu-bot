---
name: sglang-regression
description: Diagnose and fix an SGLang regression when a relevant commit, PR, good/bad boundary, or changed behavior is known.
---
# Regression workflow

1. For code-changing work, first run `python3 .codex/scripts/resolve-handoff.py --json`; consume a matching open handoff before broad rediscovery.
2. Start with suspect diff and direct callers/data flow.
3. Do not broadly scan the repository until this evidence is insufficient.
4. Classify shared vs backend-specific vs dependency behavior.
5. State one concrete root-cause hypothesis before editing.
6. Make the smallest fix preserving intended behavior.
7. Add a regression test only if it has independent guard value and is actually required.
8. Validate the original failing path first, then stop.
9. If report-only work defers actionable implementation, create a compact handoff.
10. If an auto-discovered handoff was fully addressed, mark it consumed.
