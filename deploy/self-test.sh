#!/usr/bin/env bash
# Check deployed code without network, credentials, model calls, or publication.
set -Eeuo pipefail
BOT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON=/usr/bin/python3
[[ -x "$PYTHON" ]] || { echo 'Missing /usr/bin/python3' >&2; exit 2; }
"$PYTHON" -I -B - "$BOT_HOME" <<'PYTEST'
import importlib
from pathlib import Path
import sys
root=Path(sys.argv[1]).resolve()
if sys.version_info < (3,9):
    raise SystemExit('Python 3.9 or newer is required')
sys.path[:0]=[str(root),str(root/'scripts')]
files=list((root/'scripts').glob('*.py'))
if not files:
    raise SystemExit('Installed Python scripts are missing')
for path in files:
    compile(path.read_bytes(),str(path),'exec')
for name in ('npu_diffusion_ci_report','analysis_recovery','collection_evidence',
             'complete_report','model_evidence','preview_report','recover_daily_report',
             'testcase_observations','refresh_daily_snapshots','reparse_failure_snapshots'):
    importlib.import_module('scripts.'+name)
for name in ('npu-diffusion-ci.service','npu-diffusion-ci.timer'):
    if not (root/'deploy/systemd'/name).is_file():
        raise SystemExit('Missing systemd unit: '+name)
print('Installation OK: version '+(root/'VERSION').read_text().strip()+
      ', '+str(len(files))+' Python files checked; reporting modules import successfully.')
PYTEST
