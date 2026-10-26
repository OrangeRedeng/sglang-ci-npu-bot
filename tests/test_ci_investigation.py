"""Offline unit tests for v11 evidence routing and GLM multi-pass behavior."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ci_investigation',ROOT/'scripts/ci_investigation.py')
inv=importlib.util.module_from_spec(spec); spec.loader.exec_module(inv)
spec2=importlib.util.spec_from_file_location('npu_diffusion_ci_report',ROOT/'scripts/npu_diffusion_ci_report.py')
ci=importlib.util.module_from_spec(spec2); spec2.loader.exec_module(ci)


def fixtures():
    log={'job_id':123,'run_id':456,'name':'multimodal-gen-test-4-npu-a3 (0)',
         'url':'https://github.com/sgl-project/sglang/actions/runs/456/job/123',
         'head_sha':'b'*40,'created_at':'2026-10-20T12:00:00Z','workflow':'pr-test-npu.yml',
         'pr_numbers':[44525],'run_event':'pull_request','conclusion':'failure','fingerprint':'abc123dead',
         'error_signature':'Consistency check failed for qwen_image_t2i_2npu:',
         'failure_category':'diffusion_consistency','primary_test_case':'qwen_image_t2i_2npu',
         'metrics':{'ssim':.9371,'psnr':23.6472}, 'thresholds':{'ssim':{'op':'>=','value':.95}},
         'failed_checks':['ssim','psnr'], 'log_excerpt':['Metrics: ssim=0.9371'],
         'expanded_evidence':'f0 [ssim, psnr]', 'log_analysis':{'events':[]}}
    data={'generated_utc':'2026-10-20T15:00:00Z','counts':{'failure':1},
          'coverage':['pr-test-npu.yml: CAPPED at 320 runs'], 'jobs':[log],
          'lookback_hours':24,'log_analysis_attempted':1,'failed_jobs_without_logs':0,'errors':[]}
    clusters=ci.clusters_from_snapshot(data);ci.assign_cluster_ids(None,clusters,True,0)
    return data,clusters

class TestInvestigation(unittest.TestCase):
    def setUp(self):
        b = patch.dict(os.environ, {'GLM_BUDGET_ENABLED':'0'})
        b.start()
        self.addCleanup(b.stop)

    def test_skills_are_pinned_and_load(self):
        b=inv.load_skill_bundle(['sglang-regression','ascend-regression'])
        self.assertEqual(b['source_commit'],'7e321feb72d90b1326ec456a7a34c05ed26b3456')
        self.assertIn('good/bad commit',b['skills'][1]['content'])

    def test_checksum_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'sglang-regression').mkdir()
            (root/'manifest.json').write_text(json.dumps({'source_commit':'test','skills':{'sglang-regression':'0'*64}}))
            (root/'sglang-regression'/'SKILL.md').write_text('modified')
            with patch.object(inv,'SKILLS_ROOT',root):
                with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                    inv.load_skill_bundle(['sglang-regression'])

    def test_router_skips_irrelevant_kernel_skills(self):
        data,clusters=fixtures();r=inv.relevant_skills(clusters[0])
        self.assertIn('ascend-regression',r)
        self.assertNotIn('ascend-kernel-debug',r)
        self.assertIn('sglang-pr-review',r)

    def test_kernel_error_routes_to_debug(self):
        r=inv.relevant_skills({'signature':'HCCL timeout with NPU graph capture error'})
        self.assertIn('ascend-kernel-debug',r)

    def test_no_unverified_last_green(self):
        data,clusters=fixtures();old=dict(data)
        old['generated_utc']='2026-10-19T15:00:00Z'
        old['jobs']=[dict(data['jobs'][0],job_id=999,conclusion='success',fingerprint=None)]
        context=inv.build_regression_context(data,clusters,[old])
        e=context['clusters'][0]
        self.assertFalse(e['good_bad_boundary_verified'])
        self.assertIsNone(e['last_verified_green_testcase'])
        self.assertEqual(e['job_level_successes_same_workflow_and_matrix_name'][0]['job_id'],999)
        self.assertTrue(context['snapshot_window_capped'])

    def test_pr_enrichment_only_attached_run_metadata(self):
        data,clusters=fixtures()
        class GH:
            calls=[]
            def get(self,path,params=None):
                self.calls.append(path)
                if path.endswith('/pulls/44525'):
                    return {'number':44525,'title':'fix diffusion npu','html_url':'https://github.com/sgl-project/sglang/pull/44525','head':{'sha':'a'},'base':{'sha':'b'},'state':'open'}
                return [{'filename':'python/sglang/multimodal_gen/runtime/diffusion.py','status':'modified','patch':'@@ diff'}]
        gh=GH();out=inv.related_pr_context(gh,clusters,'sgl-project/sglang')
        self.assertEqual(len(out),1)
        self.assertIn('unverified',out[0]['causality'])
        self.assertEqual(out[0]['candidate_files'][0]['status'],'modified')
        self.assertEqual(len(gh.calls),2)

    def test_glm_triage_and_investigate_request_counts(self):
        data,clusters=fixtures()
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self):return b'{"choices":[{"message":{"content":"Hypothesis: compare good/bad runs, cause unverified."}}]}'
        def do_api(req,timeout):
            self.requests.append(json.loads(req.data.decode('utf-8')))
            return Response()
        self.requests=[]
        with patch.dict(os.environ,{'GLM_API_KEY':'test','GLM_API_BASE_URL':'https://example.com/v4',
             'GLM_MODEL':'glm-test','GLM_ANALYSIS_MODE':'triage'},clear=False),\
             patch.object(ci.urllib.request,'urlopen',side_effect=do_api):
            text,status=ci.glm_analysis(data,clusters)
            self.assertTrue(status.startswith('completed'))
            self.assertEqual(len(self.requests),1)
            self.assertIn('ascend-regression',self.requests[0]['messages'][0]['content'])
        self.requests=[]
        with patch.dict(os.environ,{'GLM_API_KEY':'test','GLM_API_BASE_URL':'https://example.com/v4',
             'GLM_MODEL':'glm-test','GLM_MAX_INVESTIGATIONS':'1'},clear=False),\
             patch.object(ci.urllib.request,'urlopen',side_effect=do_api):
            text,status=ci.glm_analysis(data,clusters,mode='investigate')
            self.assertEqual(status,'completed: investigate')
            self.assertEqual(len(self.requests),2)
            self.assertIn('hypotheses',self.requests[1]['messages'][0]['content'].lower())

    def test_elevated_glm_stage_budgets_and_overrides(self):
        """Ensure raising only the global cap does not leave legacy 5-6k stage limits."""
        data, clusters = fixtures()
        requested = []
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self):
                return b'{"choices":[{"finish_reason":"stop","message":{"content":"Engineering evidence supported"}}]}'
        def api(req, timeout):
            requested.append(json.loads(req.data))
            return Response()
        env = {'GLM_API_KEY':'test', 'GLM_API_BASE_URL':'https://example.com/v4',
               'GLM_MODEL':'glm-5.3-flash','GLM_MAX_INVESTIGATIONS':'1',
               'GLM_REASONING_EFFORT':'high', 'GLM_MAX_OUTPUT_TOKENS':'24000',
               'GLM_TRIAGE_MAX_TOKENS':'12000', 'GLM_CLUSTER_MAX_TOKENS':'16000',
               'GLM_SYNTHESIS_MAX_TOKENS':'20000'}
        with patch.dict(os.environ, env, clear=False), \
             patch.object(ci.urllib.request, 'urlopen', side_effect=api):
            self.assertEqual(ci.glm_analysis(data, clusters,mode='triage')[1], 'completed: triage')
            self.assertEqual(ci.glm_analysis(data, clusters,mode='investigate')[1], 'completed: investigate')
        self.assertEqual([x['max_tokens'] for x in requested], [12000,16000,20000])
        self.assertEqual([x['reasoning_effort'] for x in requested], ['high']*3)

    def test_all_empty_clusters_skip_paid_synthesis(self):
        data, clusters = fixtures()
        requests = []
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self):return b'{"choices":[{"finish_reason":"stop","message":{"content":""}}]}'
        def api(req,timeout):
            requests.append(json.loads(req.data));return Response()
        env = {'GLM_API_KEY':'test','GLM_API_BASE_URL':'https://example.com/v4',
               'GLM_MODEL':'glm-5.3-flash','GLM_MAX_INVESTIGATIONS':'1',
               'GLM_EMPTY_RETRIES':'0'}
        with patch.dict(os.environ,env,clear=False), \
             patch.object(ci.urllib.request,'urlopen',side_effect=api):
            text,status=ci.glm_analysis(data,clusters,mode='investigate')
        self.assertEqual(status,'failed')
        self.assertEqual(len(requests),1) # no ungrounded synthesis
        self.assertIn('synthesis was skipped',text)
        self.assertIn('GLMEmptyResponseError',text)

    def test_partial_cluster_investigation_cannot_claim_completed(self):
        data,clusters=fixtures()
        other=dict(data['jobs'][0],job_id=789,fingerprint='ff123456789abc',
                   error_signature='Consistency check failed for minimax_h3_t2va_2npu:',
                   primary_test_case='minimax_h3_t2va_2npu')
        data['jobs'].append(other)
        clusters=ci.clusters_from_snapshot(data)
        ci.assign_cluster_ids(None,clusters,True,0)
        bodies=[{'choices':[{'finish_reason':'stop','message':{'content':'Cluster 1: unverified hypothesis'}}]},
                {'choices':[{'finish_reason':'stop','message':{'content':''}}]},
                {'choices':[{'finish_reason':'stop','message':{'content':'Synthesis with incomplete evidence'}}]}]
        calls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self):return json.dumps(bodies.pop(0)).encode('utf-8')
        def api(req,timeout):
            calls.append(json.loads(req.data));return Response()
        env={'GLM_API_KEY':'test','GLM_API_BASE_URL':'https://example.com/v4',
             'GLM_MODEL':'glm-5.3-flash','GLM_MAX_INVESTIGATIONS':'2',
             'GLM_EMPTY_RETRIES':'0'}
        with patch.dict(os.environ,env,clear=False), \
             patch.object(ci.urllib.request,'urlopen',side_effect=api):
            text,status=ci.glm_analysis(data,clusters,mode='investigate')
        self.assertEqual(status,'partial: investigate')
        self.assertEqual(len(calls),3)
        self.assertIn('Synthesis with incomplete evidence',text)

    def test_reanalysis_does_not_invoke_github(self):
        path=ROOT/'scripts/reanalyze_snapshot.py'
        spec=importlib.util.spec_from_file_location('reanalysis',path)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        data,clusters=fixtures()
        with tempfile.TemporaryDirectory() as d:
            snap=Path(d)/'snapshot.json';snap.write_text(json.dumps(data))
            out=Path(d)/'reanalysis.md'
            with patch.object(mod,'glm_analysis',return_value=('Hypothesis, not verified','completed: triage')) as mock:
                self.assertEqual(mod.main(['--snapshot',str(snap),'--mode','triage','--output',str(out)]),0)
            self.assertTrue(out.is_file())
            self.assertIn('Root cause',out.read_text())
            self.assertIsNone(mock.call_args.kwargs.get('gh'))

if __name__=='__main__':unittest.main()
