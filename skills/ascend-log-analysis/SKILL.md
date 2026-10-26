---
name: ascend-log-analysis
description: Triage large SGLang CI and Ascend/NPU logs without dumping the full log into model context.
---
# Large SGLang / Ascend log analysis

For a local/downloaded log at least 1 MiB or 10,000 lines (or clearly large/repetitive), reduction is mandatory before raw-log reading.

1. Check file size/line count without reading the body.
2. Preserve the original log.
3. Run `.codex/scripts/extract-log-context.py <log-file>` before raw reading.
4. Read the bounded focused artifact under `.codex-artifacts/logs/`. The default is capped at roughly 32 KiB / 400 lines and groups repeated events by normalized signature.
5. Start with the signature table and representative windows. If one concrete signature needs more context, rerun `extract-log-context.py <log> --expand <signature-id>` instead of increasing the global output cap.
6. Read a narrow raw-log range only when a concrete missing fact requires it.
7. Separate symptom from root cause; do not quote/replay repetitive rank/timestamp/address noise.
