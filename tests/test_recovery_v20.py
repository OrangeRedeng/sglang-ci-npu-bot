"""Recovery publication validates saved incident analyses and never re-runs GLM."""
from pathlib import Path
import os
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts import reanalyze_and_publish as mod

IDS = ['NPU-D004','NPU-D005','NPU-D006']
RECOVERY = ('GLM synthesis failed (GLMBudgetExceeded); partial per-cluster analysis follows.\n\n'
    + '\n\n'.join('### '+n+'\n' + ('Verified observed evidence and unverified hypothesis. '*7)
                    for n in IDS))
CLUSTERS = [dict(stable_id=cid) for cid in IDS]


class TestRecoveredPublication(unittest.TestCase):
    def test_fully_recovered_three_incidents(self):
        mod.validate_recovery_analysis(RECOVERY,CLUSTERS)
        self.assertTrue(mod.eligible_for_publication('partial: recovered investigations', RECOVERY, True))
        self.assertFalse(mod.eligible_for_publication('partial: recovered investigations', RECOVERY))
    def test_refuses_wrong_snapshot_and_missing_incident(self):
        with self.assertRaisesRegex(RuntimeError,'do not match'):
            mod.validate_recovery_analysis(RECOVERY,[dict(stable_id='NPU-D200')])
        with self.assertRaisesRegex(RuntimeError,'do not match'):
            mod.validate_recovery_analysis(RECOVERY.replace('### NPU-D005','### NPU-D200'),CLUSTERS)
    def test_refuses_duplicate_and_unavailable(self):
        with self.assertRaisesRegex(RuntimeError,'Duplicate'):
            mod.validate_recovery_analysis(RECOVERY + '\n### NPU-D004\n' + 'More'*50,CLUSTERS)
        with self.assertRaisesRegex(RuntimeError,'Missing successful'):
            mod.validate_recovery_analysis(RECOVERY.replace('Verified observed evidence and unverified hypothesis. '*7, 'Cluster-specific GLM analysis unavailable: timeout.',1),CLUSTERS)
    def test_refuses_not_recovery_and_non_english(self):
        with self.assertRaisesRegex(RuntimeError,'Not a GLM failed-synthesis recovery'):
            mod.validate_recovery_analysis('All complete'+RECOVERY,CLUSTERS)
        with self.assertRaisesRegex(RuntimeError,'English-only'):
            mod.validate_recovery_analysis(RECOVERY+'\nПроблема', CLUSTERS)
    def test_cli_requires_partial(self):
        with self.assertRaises(SystemExit) as ctx:
            mod.main(['--snapshot','unused.json','--reuse-recovery','recovery.md'])
        self.assertEqual(ctx.exception.code,2)
    def test_reuse_flag_does_not_complete_a_failed_synthesis(self):
        # Under v19 --reuse-analysis incorrectly promoted a recorded partial to completed.
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'recovery.md';path.write_text(RECOVERY)
            self.assertTrue(path.read_text().startswith('GLM synthesis failed'))
            # Do not allow publishing under completed status from this input.
            self.assertTrue(mod.validate_recovery_analysis(RECOVERY,CLUSTERS) is None)

class TestOfflineRecoveryIntegration(unittest.TestCase):
    def test_preview_and_publish_use_no_paid_model(self):
        from unittest.mock import patch
        import os
        from scripts import pr_forensics
        class FakeGitHub:
            writes=[]
            def __init__(self,token):pass
            def list_all(self,path,query,max_pages):
                if query.get('labels')=='npu-diffusion-cluster':
                    return [{'title':'['+cid+'] test',
                             'html_url':'https://github.com/bot/repo/issues/'+str(i+2),
                             'number':i+2,
                             'body':'<!-- npu-diffusion-fingerprint:'+str(i)+'abc -->'}
                            for i,cid in enumerate(IDS)]
                return [{'title':'Daily', 'number':1,
                         'body':'<!-- npu-diffusion-report-date:2026-10-20 -->'}]
            def issue(self,number,payload): self.writes.append((number,payload))

        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            f=base/'recovery.md';f.write_text(RECOVERY,encoding='utf-8')
            snap=base/'snapshot.json'
            snap.write_text(json.dumps({'jobs':[],'generated_utc':'2026-10-20T17:00:00Z',
                                        'counts':{'failure':3,'success':0},
                                        'coverage':[],'log_download_succeeded':3}),encoding='utf-8')
            cases=[{'fingerprint':str(i)+'abc','count':1,'signature':'Test failure',
                    'first_seen_snapshot':'2026-10-20T10:00:00Z',
                    'jobs':[{'job_id':12+i,'metrics':{},'thresholds':{},
                            'failure_category':'timeout','url':'https://github.com/sgl-project/sglang/actions'}]}
                   for i in range(3)]
            old=os.getcwd()
            try:
                os.chdir(td)
                with patch.object(mod,'TARGET_REPO','bot/repo'),\
                     patch.object(mod,'GitHub',FakeGitHub),\
                     patch.object(mod,'clusters_from_snapshot',return_value=cases),\
                     patch.object(mod,'glm_analysis',side_effect=AssertionError('Paid GLM unexpectedly called')),\
                     patch.object(mod,'update_cluster_issue_details') as update,\
                     patch.object(mod,'sync_cluster_labels') as labels,\
                     patch.object(pr_forensics,'discover_pr_leads',return_value={}),\
                     patch.object(pr_forensics,'attach_pr_leads'):
                    FakeGitHub.writes=[]
                    args=['--snapshot',str(snap),'--reuse-recovery',str(f),
                          '--allow-partial','--max-run-metadata','0']
                    self.assertEqual(mod.main(args),0)
                    self.assertEqual(FakeGitHub.writes,[])
                    self.assertFalse(update.called)
                    self.assertEqual(mod.main(args+['--publish']),0)
                    self.assertTrue(update.called)
                    self.assertTrue(labels.called)
                    self.assertEqual(len(FakeGitHub.writes),1)
                    issue,payload=FakeGitHub.writes[0]
                    self.assertEqual(issue,1)
                    self.assertIn('partial: recovered investigations',payload['body'])
                    self.assertIn('final GLM synthesis failed',payload['body'])
                    self.assertIn('<!-- npu-ci-report-language:en -->',payload['body'])
            finally:
                os.chdir(old)

if __name__=='__main__':unittest.main()

class TestOfflineRecoverySmoke(unittest.TestCase):
    def test_publish_recovered_without_glm_calls(self):
        from scripts import pr_forensics
        data = dict(generated_utc='2026-10-20T17:14:00Z',jobs=[],counts={})
        clusters = [dict(stable_id=n,fingerprint=('abc%d'%i),signature='signature',
                         count=1,jobs=[]) for i,n in enumerate(IDS)]
        repo='sglang-ci-npu-bot/sglang-ci-npu-bot'
        issues=[dict(number=i+2,title='['+n+'] Test',
                     body='<!-- npu-diffusion-fingerprint:abc%d -->'%i,
                     html_url='https://github.com/'+repo+'/issues/'+str(i+2))
                     for i,n in enumerate(IDS)]
        daily=[dict(number=1,title='Daily',
                    body='<!-- npu-diffusion-report-date:2026-10-20 -->',
                    html_url='https://github.com/'+repo+'/issues/1')]
        class FakeGitHub:
            writes=[]
            def __init__(self,*args,**kwargs):pass
            def list_all(self,path,opts,max_pages=10):
                return issues if opts.get('labels')=='npu-diffusion-cluster' else daily
            def issue(self, number, payload):self.writes.append((number,payload))
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            snapshot=root/'sample.json';snapshot.write_text(json.dumps(data))
            saved=root/'saved.md';saved.write_text(RECOVERY)
            cwd=os.getcwd();os.chdir(root)
            try:
                with patch.object(mod,'TARGET_REPO',repo), \
                     patch.object(mod,'GitHub',FakeGitHub), \
                     patch.object(mod,'clusters_from_snapshot',return_value=clusters), \
                     patch.object(mod,'glm_analysis',side_effect=AssertionError('PAID GLM CALL')), \
                     patch.object(mod,'render_report',return_value='# English report\n') as render, \
                     patch.object(mod,'update_cluster_issue_details') as cluster_writes, \
                     patch.object(mod,'sync_cluster_labels') as sync, \
                     patch.object(pr_forensics,'discover_pr_leads',return_value={}), \
                     patch.object(pr_forensics,'attach_pr_leads'):
                    result=mod.main(['--snapshot',str(snapshot),'--publish',
                                    '--allow-partial','--reuse-recovery',str(saved),
                                    '--max-run-metadata','0'])
                    self.assertEqual(result,0)
                    self.assertIn('partial: recovered investigations',render.call_args.args[3])
                    self.assertEqual(len(FakeGitHub.writes),1)
                    self.assertEqual(FakeGitHub.writes[0][0],1)
                    self.assertTrue(cluster_writes.called)
                    self.assertTrue(sync.called)
            finally:os.chdir(cwd)
