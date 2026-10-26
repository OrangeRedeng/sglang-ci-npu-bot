#!/usr/bin/env bash
# Offline regression tests + read-only GitHub DNS/API/jobs preflight.
set -Eeuo pipefail
BOT_HOME=/home/sglang-npu-bot
PYTHON=/usr/bin/python3
[[ -x "$PYTHON" ]] || { echo 'Missing /usr/bin/python3' >&2; exit 2; }
echo '[TEST] Offline unit tests (no network; no secrets)'
"$PYTHON" - "$BOT_HOME" <<'PYTEST'
import os
from pathlib import Path
import subprocess
import sys
import tempfile
root=Path(sys.argv[1]).resolve()
env=dict(os.environ)
env['PYTHONPATH']=str(root)+os.pathsep+env.get('PYTHONPATH','')
# Fixtures must never inherit a deployed server's reports/ or stage caches.
with tempfile.TemporaryDirectory(prefix='npu-ci-self-test-') as working:
    result=subprocess.run([sys.executable,'-m','unittest','discover',
                           '-s',str(root/'tests'),'-p','test_*.py','-v'],
                          cwd=working,env=env)
raise SystemExit(result.returncode)
PYTEST
echo '[TEST] Live GitHub API preflight as sglang-ci (no secrets loaded)'
if [[ $(id -u) -eq 0 ]]; then
  runuser -u sglang-ci -- "$PYTHON" "$BOT_HOME/scripts/npu_network_check.py" --sample-jobs
else
  "$PYTHON" "$BOT_HOME/scripts/npu_network_check.py" --sample-jobs
fi
printf '\n[TEST] PASS. For CI sampling run: sudo bash %s/deploy/run-smoke-test.sh\n' "$BOT_HOME"
