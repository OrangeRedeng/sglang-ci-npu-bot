# Provenance and redistribution note

This project imports the NPU Diffusion CI Hybrid Kit v15 and includes pinned skill instruction files from `OrangeRedeng/sglang-ascend-agent-workflow-kit` at the commit recorded in `skills/manifest.json`. The scripts and skill files should retain the original upstream licenses and notices when redistributed. No upstream SGLang code is vendored.

The GitHub Actions fallback publisher from v15 is stored under `examples/workflows/` (not under `.github/workflows/`) to prevent automatic daily Issue publishing to the *source code* repository. Configure its target Issues repository and credentials explicitly before enabling it.
