---
name: sglang-benchmark-analysis
description: Compare SGLang benchmark results under fixed conditions and prevent invalid performance claims.
---
# Benchmark analysis

1. Verify same model, commit baseline, environment, hardware, backend, dtype/quant, TP/DP/EP, graph state and workload.
2. If not comparable, stop and identify the confounder.
3. Record exact commands, warmup, run count and aggregate metric.
4. Distinguish TTFT/ITL/latency/throughput/memory and saturated vs single-request regimes.
5. Treat one run as exploratory, not final evidence.
6. A kernel-local improvement needs real model serving validation before an E2E claim.
