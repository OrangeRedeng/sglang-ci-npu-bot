---
name: ascend-regression
description: Diagnose an Ascend NPU regression across SGLang, torch_npu, CANN, sgl-kernel-npu or graph/communication boundaries.
---
# Ascend regression

1. Establish good/bad commit, suspect PR, or dependency-version boundary.
2. Inspect changed NPU-relevant files first.
3. Classify ownership: shared SGLang / NPU backend / torch_npu / sgl-kernel-npu / NPUGraph / HCCL / dependency compatibility / model adaptation.
4. Check dependency changes before compensating in application code.
5. Use CUDA only as semantic reference where contracts should match.
6. State hypothesis, implement smallest backend-correct fix, validate failing NPU path.
