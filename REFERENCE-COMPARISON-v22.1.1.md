# Comparison with the original NPU-Diffusion CI report

Reference: `npu-diffusion-ci-2026-10-08-en.md`, the complete 219-line English
baseline report from “Ежедневный отчет NPU-Diffusion”, read on October 26, 2026.
Its PR statuses and measurements describe October 8; they were not treated as
current facts or reverified as part of this implementation review.

The original report is stronger at explaining why a failure matters and what
specific evidence would resolve it. v22.1.0 has broader automation and stronger
scope/closure guards but still loses some of that engineering detail. v22.1.1
addresses the concrete parser and renderer gaps found in this comparison.

| Aspect | Original reference | v22.1.0 gap | v22.1.1 improvement |
|---|---|---|---|
| Executive assessment | Quantified performance and concrete API symptom | Mainly counters | Strongest observed symptoms and actual/baseline/limit values, ratio and scope |
| Performance assertion | Actual / Expected / Limit, rel_tol and abs_pad | Expected and Limit were not parsed | Exact assertion format supported; units normalized; relative tolerance and absolute pad kept separate |
| Per-stage metrics | E2E and denoising comparisons | Stage names underrepresented | Named validation blocks kept separate and metric name displayed |
| Long tracebacks | Human inspected full context | Bounded parser rows could drop early measurements | Preserve parsed measurements before compaction |
| API failure context | Internal AR 400 and public 500 explicitly discussed | Latest diagnostic alone could hide a prior HTTP status | Preserve HTTP statuses per testcase, without inventing a causal chain |
| Methodology | Executed diffusion partitions, runner versus suite distinction | Skips listed only globally | Workflow × provenance job conclusions table and explicit runner/device caveat |
| Configuration | Settings and source freshness caveat | Little indication of missing pinned profile | Present known structured configuration and missing fields; never substitute current source defaults |
| Experiments | Model-specific trace/payload checks | Four generic closing actions | Family-specific proposed experiment and acceptance criterion at the actual tested SHA |
| Full analysis | Readable technical Markdown | Expandable GLM text collapsed into one paragraph | Preserve headings, paragraphs and lists; omit lines citing unverified PRs |
| Same-day trend | Clearly scoped sample | Historical row could override current snapshot | Current snapshot takes precedence |
| Priorities | Historical PR-only failures labelled P0 | Copying this would overstate current evidence | Retain current provenance-based priority rules; repeated PR symptoms do not prove main impact |
| Issue lifecycle | Manual report only | More powerful but needs conservative guards | Existing archival and testcase PASS closure rules unchanged |

## Exact reference regression example

The performance block transcribed from the reference is now parsed as:

| Field | Extracted value |
|---|---:|
| Metric | E2E Latency |
| Actual | 471.0704301 s |
| Baseline (`Expected`) | 155.1062100 s |
| Gate (`Limit`) | 193.8827625 s |
| Actual / baseline | approximately 3.04× |
| Declared relative tolerance | 25% |
| Declared absolute pad | 0.020 s |

This is a historical parser regression fixture, not evidence that the same
slowdown exists in current CI. The parser does not treat arbitrary `Expected`
values outside performance validation as a latency baseline.

## What still requires collected evidence

- Last-green/first-bad testcase boundaries are unknown unless an equivalent
  execution is actually retained. A successful partition is not proof that a
  particular parametrized test ran and passed.
- Structured configuration depends on upstream logs/artifacts. The bot cannot
  reliably infer model revision, exact CANN/torch_npu stack, actual backend,
  Cache-DiT decisions or GT identity from a generic exception.
- Registered suite coverage at each tested SHA is not enumerated automatically;
  the matrix describes observed execution, not the complete expected inventory.
  Runner names are not used to infer inference device count.
- Full profiler timelines and request/response bodies are not automatically
  collected. The report proposes the smallest evidence-gathering experiment;
  it does not manufacture a mechanistic diagnosis.
- PR discovery is bounded, and relevant code overlap is a lead, not proof of
  causality. Historical relationships must be revalidated at the tested SHA.
- Absence of a failure in a capped scan remains distinct from a verified fix.

## Validation and deployment

220 offline tests pass, including reference-assertion parsing, metric-block
isolation, long-trace retention, testcase-specific HTTP codes, Markdown formatting,
unverified-PR filtering and current-day trend precedence. Source, simulated
installation and extracted ZIP checks pass. Git bundle restoration verifies the
release tag. No upstream CI jobs were rerun and no server or GitHub Issues were
modified during preparation.

Install and run using RELEASE-v22.1.1.md. The existing combined recovery command
reparses retained logs, refreshes the latest existing daily report, reconciles
legacy Issues and optionally forces today's analysis. Missing raw logs remain an
explicit limit and do not justify invented historical corrections.
