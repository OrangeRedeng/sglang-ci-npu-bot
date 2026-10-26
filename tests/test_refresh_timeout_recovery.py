"""Recovery of the reported v22.1.2 synthesis-timeout case without API spending."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import refresh_daily_snapshots as r
import npu_diffusion_ci_report as ci
import glm_http

FINGERPRINTS=('82a8aa5e24e425','2360050b453640')

def recovery_text():
    return 'GLM synthesis failed (timeout); partial per-cluster analysis follows.\n\n'+'\n\n'.join('### '+f+'\n'+'Observed facts and conditional mechanistic hypotheses; exact testcase and runtime require verification. '*3 for f in FINGERPRINTS)

class RecoveryTests(unittest.TestCase):
    def test_legacy_bare_fingerprints_normalized_to_observations(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'2026-10-25-refresh-failed.md';p.write_text(recovery_text())
            clusters=[{'fingerprint':f} for f in FINGERPRINTS]
            text=r.recover_investigations(p,clusters,'2026-10-25')
            self.assertIn('### OBS-'+FINGERPRINTS[0],text)
            self.assertEqual(clusters[1]['analysis_ref'],'OBS-'+FINGERPRINTS[1])
            self.assertIn('Synthesis unavailable',text)
    def test_wrong_date_unknown_duplicate_and_failed_sections_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'2026-10-25-refresh-failed.md'
            for text in [recovery_text().replace(FINGERPRINTS[0],'f'*14), recovery_text()+'\n### '+FINGERPRINTS[0]+'\n'+'duplicate '*30,recovery_text().replace('Observed facts','Cluster-specific GLM analysis unavailable')]:
                p.write_text(text)
                with self.assertRaises(RuntimeError):r.recover_investigations(p,[{'fingerprint':f} for f in FINGERPRINTS],'2026-10-25')
            with self.assertRaises(RuntimeError):r.recover_investigations(p,[],'2026-10-26')
    def test_refresh_recovery_makes_no_paid_calls_and_does_not_close(self):
        data={'generated_utc':'2026-10-25T10:00:00Z','coverage':[],'counts':{'failure':2},'jobs':[
            {'job_id':i,'conclusion':'failure','run_event':'pull_request','head_branch':'feature','pr_numbers':[42],
             'fingerprint':f,'error_signature':'RuntimeError: failed','primary_test_case':'test_'+str(i)} for i,f in enumerate(FINGERPRINTS)]}
        issue={'number':23,'body':'<!-- npu-diffusion-report-date:2026-10-25 -->','updated_at':'unchanged'}
        class GH:
            def __init__(self):self.writes=[]
            def get(self,*a):return issue
            def request(self,*a):self.writes.append(a)
        with tempfile.TemporaryDirectory() as d,patch.object(r.ci,'TARGET_REPO','sglang-ci-npu-bot/sglang-ci-npu-bot'),patch.object(r.ci,'glm_analysis',side_effect=AssertionError('must not call GLM')):
            root=Path(d);p=root/'2026-10-25-refresh-failed.md';p.write_text(recovery_text())
            gh=GH()
            r.refresh(gh,gh,data,[issue],root,apply=True,reuse_investigations=p,allow_partial=True)
            self.assertEqual(len(gh.writes),1)
            self.assertEqual(gh.writes[0][0],'PATCH')
            self.assertNotIn('state',gh.writes[0][2])
            self.assertIn('partial: recovered',gh.writes[0][2]['body'])
    def test_recovery_requires_explicit_partial_flag_before_writes(self):
        with patch.object(r.ci,'TARGET_REPO','bot/repo'):
            with self.assertRaises(SystemExit):r.main(['--reuse-investigations','x','--dates','2026-10-25'])
    def test_timeout_override_validation_before_network(self):
        with patch.object(glm_http.urllib.request,'urlopen',side_effect=AssertionError('network')):
            with self.assertRaises(ValueError):glm_http.chat_completion('https://example.test','secret','glm-5.3',[],timeout_seconds=1201)
    def test_future_checkpoint_saved_before_synthesis_timeout(self):
        import os
        clusters=[{'fingerprint':FINGERPRINTS[0],'stable_id':'OBS-'+FINGERPRINTS[0],
                   'signature':'RuntimeError: failed','count':1,'jobs':[{'job_id':1,'run_event':'push','head_branch':'main','head_repository':'sgl-project/sglang','primary_test_case':'wan'}]}]
        data={'generated_utc':'2026-10-25T10:00:00Z','jobs':[],'coverage':[],'counts':{}}
        with tempfile.TemporaryDirectory() as d:
            checkpoint=Path(d)/'context.json'
            env={'GLM_API_KEY':'test-key','GLM_API_BASE_URL':'https://example.test','GLM_MODEL':'glm-5.3','GLM_ANALYZER_COMMAND':'','GLM_RECOVERY_CONTEXT_FILE':str(checkpoint),'GLM_STAGE_CACHE_DIR':'','GLM_MAX_INVESTIGATIONS':'1'}
            def fake(*args,**kwargs):
                if kwargs.get('timeout_seconds')==900:
                    self.assertTrue(checkpoint.exists())
                    obj=json.loads(checkpoint.read_text());self.assertEqual(len(obj['investigations']),1)
                    raise TimeoutError()
                return 'Completed cluster investigation with observed evidence and conditional mechanisms. '*10
            # ci imports scripts.glm_http when the package is available.
            import scripts.glm_http as transport
            with patch.dict(os.environ,env),patch.object(transport,'chat_completion',side_effect=fake):
                text,status=ci.glm_analysis(data,clusters,mode='investigate')
            self.assertTrue(status.startswith('partial'))
            self.assertIn('Completed cluster investigation',text)
            self.assertEqual(json.loads(checkpoint.read_text())['data']['glm_synthesis_status'],'failed: TimeoutError')

if __name__=='__main__':unittest.main()
