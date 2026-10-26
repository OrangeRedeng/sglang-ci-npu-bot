"""Guard against v17's inherited daily 8k cap on high reasoning refreshes."""
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts'/'update-existing-issues-with-glm.sh'

class TestManualRefreshProfile(unittest.TestCase):
    @staticmethod
    def profile_shell():
        shell=SCRIPT.read_text(encoding='utf-8')
        return shell.split('# Manual high-reasoning profile,')[1].split('UNIT="npu-glm-issue-v21-')[0]
    def run_profile(self, env=None):
        script='set -euo pipefail\n' + '# Manual high-reasoning profile,' + self.profile_shell() +              '\nprintf "%s,%s,%s,%s,%s,%s" "$REFRESH_EFFORT" "$REFRESH_TOTAL" "$REFRESH_CLUSTER" "$REFRESH_SYNTHESIS" "$REFRESH_TIMEOUT" "$REFRESH_RETRIES"\n'
        return subprocess.run(['bash','-c',script],text=True,capture_output=True,
          env={**os.environ, 'GLM_MANUAL_REASONING_EFFORT':'high', **(env or {})})
    def test_defaults_override_daily_token_cap(self):
        result=self.run_profile({'GLM_MAX_OUTPUT_TOKENS':'8000'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1],'high,24000,20000,24000,720,1')
    def test_stage_above_cap_rejected_early(self):
        result=self.run_profile({'GLM_REFRESH_MAX_OUTPUT_TOKENS':'16000'})
        self.assertEqual(result.returncode,2)
        self.assertIn('refusing silent truncation',result.stderr)
    def test_high_reasoning_with_too_few_tokens_rejected(self):
        result=self.run_profile({'GLM_REFRESH_MAX_OUTPUT_TOKENS':'8000',
                                 'GLM_REFRESH_CLUSTER_MAX_TOKENS':'8000',
                                 'GLM_REFRESH_SYNTHESIS_MAX_TOKENS':'8000',
                                 'GLM_REFRESH_TRIAGE_MAX_TOKENS':'6000'})
        self.assertEqual(result.returncode,2)
        self.assertIn('High/max reasoning',result.stderr)
    def test_manual_override(self):
        result=self.run_profile({'GLM_REFRESH_REASONING_EFFORT':'low',
                                 'GLM_REFRESH_MAX_OUTPUT_TOKENS':'15000',
                                 'GLM_REFRESH_CLUSTER_MAX_TOKENS':'14000',
                                 'GLM_REFRESH_SYNTHESIS_MAX_TOKENS':'14000',
                                 'GLM_REFRESH_API_TIMEOUT_SECONDS':'300',
                                 'GLM_REFRESH_EMPTY_RETRIES':'0'})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1],'low,15000,14000,14000,300,0')

if __name__=='__main__': unittest.main()
