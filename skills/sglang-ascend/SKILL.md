---
name: sglang-ascend
description: Route SGLang Ascend NPU work to the correct SGLang, torch_npu, profiling, HCCL, Triton, AscendC, or custom-op workflow.
---
# SGLang Ascend/NPU router

Use for `hardware_backend/npu`, `torch_npu`, HCCL, NPUGraph, Ascend attention, NPU quantization/LoRA, `sgl-kernel-npu`, NPU CI/model support.

Before version-sensitive diagnosis record hardware/device, CANN, torch, torch_npu, sgl-kernel-npu version/commit, SGLang commit, eager/graph mode, dtype/quantization, TP/DP/EP/HCCL and exact workload/backend path.

Route by ownership: SGLang orchestration -> SGLang rules; torch_npu semantics -> `ascend-torch-npu`; profiles -> profiling skills; single-op benchmark -> `ascend-npu-op-benchmark`; adaptation -> `official-npu-adapter-reviewer`; profiling anomaly -> `official-ascend-profiling-anomaly`; HCCL -> `official-hccl-test`; custom PyTorch op -> `ascend-opplugin`; Triton/AscendC -> corresponding leaf skill; optimized SGLang kernel -> inspect `sgl-kernel-npu` first.

For new or optimized NPU kernels, prefer the bundled `sglang-ascend-kernel-dev` orchestration skill, then load only the selected CANNBot/KernelHive leaf skill.

Do not load every NPU skill at once. Do not mechanically translate CUDA/NCCL/graph/stream/layout/dtype assumptions to Ascend/HCCL/NPUGraph.
