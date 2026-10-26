# Verified testcase resolution — v21.0.3

The bot no longer equates a successful GitHub Actions **job** with the successful execution of an affected **testcase**. It can safely close a persistent incident only after independently matching two successful testcase observations to an earlier structured FAIL, with unchanged test inputs and a descendant code commit.

## Required source-CI instrumentation

The SGLang workflow must print a machine-readable line **after the actual testcase gate is evaluated**, both on FAIL and PASS. Exact one-line format:

```text
NPU_CI_TEST_RESULT: {"testcase":"qwen_image_t2i_2npu","status":"PASS","gt_sha":"dddddddddddddddddddddddddddddddddddddddd","seed":42,"dtype":"bf16","scheduler":"euler","model":"Qwen-Image","runtime_id":"cann8.3-torch_npu2.9.0","resolution":"1024x1024"}
```

Use the equivalent `"status":"FAIL"` in the failure log, emitted by the same assertion/check (not from a generic job-finally handler). The bot does **not** create this record from a job conclusion or any GLM assertion. Keep `gt_sha` as the **full 40-character Git SHA** of the exact pinned ground-truth data. The `runtime_id` must represent a reproducible immutable environment (CANN/torch_npu/sgl-kernel-npu/container digest); merely writing `latest` is not a trustworthy pin. The same test may print `quantization`, `resolution`, `steps`, `test_config_sha`; if present they must match on both sides. Avoid putting secrets in marker values.

For example, an upstream pytest fixture can emit the line when it finishes evaluating actual image/video quality metrics:

```python
import json

def log_ci_test_outcome(testcase, passed, *, gt_sha, seed, dtype,
                        scheduler, model, runtime_id, **details):
    record = dict(testcase=testcase, status='PASS' if passed else 'FAIL',
                  gt_sha=gt_sha, seed=int(seed), dtype=dtype,
                  scheduler=scheduler, model=model, runtime_id=runtime_id)
    record.update(details)
    print('NPU_CI_TEST_RESULT: '+json.dumps(record, sort_keys=True), flush=True)
```

Call it **only after** all testcase assertions/consistency thresholds are evaluated; do not emit PASS for skipped or xfailed cases. CI authors should guard this emission and avoid allowing arbitrary PR code to fabricate a pass. PR-scoped automatic closure is disabled by default (`INCIDENT_AUTO_CLOSE_PR=0`), because an untrusted PR may control its own log output.

## Requirements to close

1. Exact archived FAIL record for the incident fingerprint and testcase from a **local historical snapshot**. Historical reports without structured FAIL remain unresolved; no facts are invented.
2. A complete, uncapped snapshot for all tracked workflows, no failed log downloads/truncations/unread failures, no later same-matrix failed jobs.
3. Two **different** completed successful GitHub Actions runs in the same workflow, matrix job, repository, branch scope, PR number (if allowed), and matching structured testcase PASS.
4. Compare **all** `gt_sha`, `seed`, `dtype`, `scheduler`, `model`, `runtime_id` and any extra structured configuration fields; any missing value or mismatch invalidates closure.
5. Both passing runs were created after the last known failure; the HEAD SHA is different, and GitHub Compare API verifies it descends from the failing SHA (`ahead`, no behind commits).
6. The publisher has successfully published the daily report, then re-fetches the incident Issue and checks issue ID, state, ownership label and fingerprint before updating body and closing with GitHub reason `completed`.
7. Reappearance of an exact fingerprint reopens the historical Issue with the same NPU-D ID.

A successful job that merely logs `1 passed`, an LLM hypothesis, a merged PR, or a missing failure never qualifies. Capped scans (including the 2026-10-21 320-run scan) cannot auto-close anything. Correct classification as fixed may lag until an uncapped run with complete evidence occurs.

## Settings / cost

```dotenv
INCIDENT_VERIFY_PASSES=1
INCIDENT_AUTO_CLOSE=1
INCIDENT_AUTO_CLOSE_PR=0
INCIDENT_MAX_PASS_LOGS=24
```

These are default behavior for v21.0.3. Success log downloads use source GitHub API calls only. They do not call GLM and do not send code to the CI repo. Verifier logs are temporary and deleted after reading. If more than 24 qualified success logs need examination, the remainder wait for a subsequent scan.

For a saved snapshot use the installed root-owned wrapper, which sources the existing secret `EnvironmentFile` without exposing tokens in command arguments. It reads successful-job logs but does not rescan workflows or invoke GLM:

```bash
sudo bash /home/sglang-npu-bot/scripts/sync-incident-continuity.sh 2026-10-21 --preview --verify-passes
sudo bash /home/sglang-npu-bot/scripts/sync-incident-continuity.sh 2026-10-21 --publish --verify-passes
```

Do not increase `MAX_RUNS_PER_WORKFLOW` blindly; high run counts significantly increase GitHub API traffic. The missing-marker and capped-coverage cases remain open with explicit continuity states until CI can supply evidence. `GLM_REASONING_EFFORT=high` can improve hypotheses, but **never influences closure decisions**.
