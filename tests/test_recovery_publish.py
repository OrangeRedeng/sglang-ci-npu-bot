"""Paid-stage checkpoint and fail-safe manual Issue refresh recovery."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import npu_diffusion_ci_report as ci
import reanalyze_and_publish as refresh
from test_ci_investigation import fixtures


class Response:
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self):
        return json.dumps({'choices':[{'finish_reason':'stop',
           'message':{'content':'A verified English engineering answer, root cause unverified.'}}]}).encode()


class TestGLMCache(unittest.TestCase):
    def test_identical_requests_resume_without_api_and_new_request_invalidates(self):
        data,clusters=fixtures()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'GLM_BUDGET_ENABLED':'0', 'GLM_STAGE_CACHE_DIR':tmp,
            'GLM_API_KEY':'test','GLM_API_BASE_URL':'https://example.com/v4',
            'GLM_MODEL':'glm-test', 'GLM_MAX_INVESTIGATIONS':'1',
            'GLM_REASONING_EFFORT':'low'},clear=False):
            calls=[]
            def response(req,timeout):calls.append(req);return Response()
            with patch.object(ci.urllib.request,'urlopen',side_effect=response):
                a,status=ci.glm_analysis(data,clusters,mode='investigate')
                self.assertEqual(status,'completed: investigate')
                self.assertEqual(len(calls),2)
                a2,status2=ci.glm_analysis(data,clusters,mode='investigate')
                self.assertEqual(a2,a)
                self.assertEqual(len(calls),2)
                os.environ['GLM_REASONING_EFFORT']='high'
                ci.glm_analysis(data,clusters,mode='investigate')
                self.assertEqual(len(calls),4)
            files=list(Path(tmp).glob('*.json'))
            self.assertEqual(len(files),4)
            self.assertTrue(all('content' in json.loads(f.read_text()) for f in files))
            self.assertTrue(all(not (f.stat().st_mode & 0o077) for f in files))

    def test_failed_stages_not_cached(self):
        data,clusters=fixtures()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'GLM_BUDGET_ENABLED':'0', 'GLM_STAGE_CACHE_DIR':tmp,
            'GLM_API_KEY':'test','GLM_API_BASE_URL':'https://example.com/v4',
            'GLM_MODEL':'glm-test','GLM_MAX_INVESTIGATIONS':'1'},clear=False):
            with patch.object(ci.urllib.request,'urlopen',side_effect=TimeoutError('network timeout')):
                a,status=ci.glm_analysis(data,clusters,mode='investigate')
            self.assertEqual(status,'failed')
            self.assertEqual(list(Path(tmp).glob('*.json')),[])


class TestPublishGate(unittest.TestCase):
    def test_partial_requires_explicit_opt_in_and_valid_result(self):
        part=('GLM synthesis failed (TimeoutError); partial per-cluster analysis follows.\n\n'
             '### NPU-D004\n'+ 'English evidence backed hypothesis. '*12+'\n')
        self.assertFalse(refresh.eligible_for_publication('partial: investigate',part))
        self.assertTrue(refresh.eligible_for_publication('partial: investigate',part,True))
        self.assertFalse(refresh.eligible_for_publication('failed',part,True))
        bad='GLM synthesis failed\n\n### NPU-D004\nCluster-specific GLM analysis unavailable: timeout.'
        self.assertFalse(refresh.eligible_for_publication('partial: investigate',bad,True))
        self.assertTrue(refresh.eligible_for_publication('completed: saved','English verified output'))
    def test_empty_refused(self):
        self.assertFalse(refresh.eligible_for_publication('completed: triage','  '))
        self.assertFalse(refresh.eligible_for_publication('partial: investigate',''))

if __name__=='__main__':unittest.main()
