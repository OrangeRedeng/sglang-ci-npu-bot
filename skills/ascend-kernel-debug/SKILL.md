---
name: ascend-kernel-debug
description: Diagnose crashing, incorrect, unsupported, or divergent Ascend/torch_npu/SGLang kernel behavior and route to the owning framework/kernel layer.
---
# Ascend kernel debug

1. Classify the operation before editing: ordinary PyTorch op, torch_npu API, sgl_kernel_npu custom kernel, Triton-Ascend, AscendC/CANN custom op, or Python fallback.
2. If this is API/stream/memory/graph/format behavior, use `ascend-torch-npu` when installed.
3. Minimize reproduction: shape, dtype, device, strides/layout/contiguity, graph/eager, hardware and exact versions.
4. Check input contract -> dtype -> shape/alignment -> layout -> output contract -> alias/in-place -> async/streams -> synchronization -> graph capture -> kernel implementation.
5. For custom op integration use `ascend-opplugin`; for Triton/AscendC load only the corresponding kernel skill.
6. Use blocking/synchronization debug knobs only diagnostically.
7. Fix at the owning layer; do not hide kernel bugs with broad fallback.
8. Validate the original failing configuration first.
