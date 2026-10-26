import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import analysis_evidence as ae
import pr_forensics as pf
import refresh_daily_snapshots as refresh
import testcase_failures as tf
import npu_diffusion_ci_report as ci
import glm_http as transport


def job(n=1,pr=42,case='wan2_1'):
    return dict(job_id=n,run_id=n,attempt=1,run_event='pull_request',head_branch='feature',
                head_repository='fork/sglang',source_repository='sgl-project/sglang',
                conclusion='failure',pr_numbers=[pr],primary_test_case=case,
                created_at='2026-10-25T01:00:00Z',workflow='pr.yml',head_sha='b'*40)

def cluster(count=3,prs=(42,43),case='wan2_1'):
    return dict(fingerprint='a'*14,signature='RuntimeError: failed',count=count,
                jobs=[job(i+1,prs[i%len(prs)],case) for i in range(count)])

class EvidenceTests(unittest.TestCase):
    def test_independent_pr_selection_without_issue_creation(self):
        c=cluster(case='generic')
        self.assertEqual(ae.investigation_selection([c]),[c])
        self.assertEqual(c['investigation_reason'],'recurs across independent PRs')
        self.assertNotIn('issue_url',c)
    def test_repeated_critical_one_pr_and_frequency(self):
        self.assertTrue(ae.investigation_selection([cluster(prs=(42,))]))
        self.assertFalse(ae.investigation_selection([cluster(prs=(42,),case='generic')]))
        self.assertTrue(ae.investigation_selection([cluster(10,prs=(42,),case='generic')]))
    def test_duplicate_jobs_do_not_escalate(self):
        c=cluster();c['jobs']=[job()]*12
        self.assertFalse(ae.investigation_selection([c]))
    def test_missing_provenance_does_not_escalate(self):
        c=cluster(12)
        for j in c['jobs']:j['run_event']=None
        self.assertFalse(ae.investigation_selection([c]))
    def test_performance_units_threshold_and_relative(self):
        m=ae.performance_measurements(['E2E actual 57090ms baseline 28.19s threshold 35.24s'])[0]
        self.assertEqual(m['actual_seconds'],57.09)
        self.assertAlmostEqual(m['relative_regression_pct'],102.519,places=3)
        self.assertAlmostEqual(m['tolerance_pct'],25.009,places=3)
    def test_performance_unknown_not_invented(self):
        self.assertEqual(ae.performance_measurements(['took 57s; expected something']),[])
        self.assertEqual(ae.performance_measurements(['actual 57 baseline 28']),[])
    def test_performance_testcase_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log'
            p.write_text('___ test_diffusion_generation[wan] ___\nE PerformanceValidationError: E2E actual 57.09s baseline 28.19s threshold 35.24s\n___ test_diffusion_generation[joyai] ___\nE RuntimeError: Server exited early\nFAILED x.py::test_diffusion_generation[wan] - PerformanceValidationError: latency\nFAILED x.py::test_diffusion_generation[joyai] - RuntimeError: failed\n')
            cases,_=tf.parse_testcase_failures(p)
            self.assertEqual(len(cases[0]['performance']),1)
            self.assertEqual(cases[1]['performance'],[])
            snap={'jobs':[dict(job(),failure_cases=cases,failure_cases_version=2)]}
            clusters=ci.clusters_from_snapshot(snap)
            self.assertEqual(sum(len(j['performance']) for c in clusters for j in c['jobs']),1)
    def test_matrix_deduplicates_overlap_and_does_not_infer_testcase_pass(self):
        j=job();j['failure_cases']=[{'testcase':'wan'}]
        snap={'generated_utc':'2026-10-26T01:00:00Z','jobs':[j,dict(job(2),conclusion='success')]}
        hist=dict(snap,generated_utc='2026-10-25T20:00:00Z')
        rows=ae.coverage_matrix(snap,[hist])
        self.assertEqual(sum(r.get('jobs',0) for r in rows),2)
        self.assertEqual(sum(r.get('testcase_fail',0) for r in rows),1)
        self.assertEqual(sum(r.get('testcase_pass',0) for r in rows),0)
    def test_ci_pending_failure_empty_and_truncated(self):
        self.assertEqual(pf.summarize_ci({}, {'check_runs':[]})[0],'no checks reported')
        self.assertEqual(pf.summarize_ci({}, {'check_runs':[{'status':'queued'}]})[0],'pending')
        self.assertEqual(pf.summarize_ci({}, {'check_runs':[{'status':'completed','conclusion':'failure'}]})[0],'failure')
        self.assertIn('partial',pf.summarize_ci({}, {'check_runs':[],'total_count':101})[0])
    def test_pr_api_cap_on_errors(self):
        class Broken:
            def __init__(self):self.n=0
            def get(self,*a,**kw):self.n+=1;raise RuntimeError('offline')
        gh=Broken();pf.discover_pr_leads(gh,[cluster()]*8,max_api_calls=3)
        self.assertLessEqual(gh.n,3)
    def test_current_pr_head_ci_and_tested_sha_distinct(self):
        class GH:
            def get(self,path,params=None):
                if path=='/search/issues':return {'items':[]}
                if path.endswith('/files'):return [{'filename':'python/sglang/multimodal_gen/wan/ascend.py'}]
                if path.endswith('/status'):return {'state':'success','total_count':1}
                if path.endswith('/check-runs'):return {'check_runs':[{'status':'completed','conclusion':'success'}]}
                return {'number':42,'html_url':'https://github.com/sgl-project/sglang/pull/42','title':'diffusion wan NPU fix','state':'open','head':{'sha':'c'*40}}
        leads=pf.discover_pr_leads(GH(),[cluster(prs=(42,))],max_api_calls=20)['a'*14]
        self.assertEqual(leads[0]['ci_status'],'success')
        self.assertEqual(leads[0]['ci_sha'],'c'*40)
        self.assertEqual(leads[0]['tested_shas'],['b'*40])
    def test_telemetry_attempt_callback_and_no_reasoning_text(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'choices':[{'message':{'content':'analysis','reasoning_content':'SECRET_REASONING'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':12,'completion_tokens':32,'completion_tokens_details':{'reasoning_tokens':20}}}).encode()
        events=[]
        with patch.object(transport.urllib.request,'urlopen',return_value=Response()),patch.object(transport.glm_budget,'reserve',return_value=None),patch.object(transport.glm_budget,'finish'):
            transport.chat_completion('https://example.test','key','glm-5.3',[],on_usage=events.append)
        self.assertEqual(events[-1]['reasoning_tokens'],20)
        self.assertNotIn('SECRET_REASONING',json.dumps(events))
    def test_refresh_unpublished_observations_and_concurrency(self):
        snap={'generated_utc':'2026-10-25T01:00:00Z','jobs':[dict(job(),fingerprint='f'*14,error_signature='RuntimeError: failed',failure_category='pytest_failure')], 'coverage':[],'counts':{'failure':1}}
        issue={'number':23,'title':'daily','updated_at':'first','body':'<!-- npu-diffusion-report-date:2026-10-25 -->'}
        class GH:
            def __init__(self,changed=False):self.writes=[];self.changed=changed
            def get(self,*a):return dict(issue,updated_at='changed' if self.changed else 'first')
            def request(self,*a):self.writes.append(a)
        with tempfile.TemporaryDirectory() as d,patch.object(ci,'TARGET_REPO','sglang-ci-npu-bot/sglang-ci-npu-bot'):
            gh=GH();refresh.refresh(gh,gh,copy.deepcopy(snap),[issue],Path(d),True)
            self.assertEqual(len(gh.writes),1)
            self.assertEqual(gh.writes[0][0],'PATCH')
            self.assertNotIn('state',gh.writes[0][2])
            gh=GH(True)
            with self.assertRaises(RuntimeError):refresh.refresh(gh,gh,snap,[issue],Path(d),True)
            self.assertFalse(gh.writes)
    def test_report_surfaces_pr_investigation_metrics_and_telemetry(self):
        from research_report import render_research_report
        c=cluster();c['jobs'][0]['performance']=ae.performance_measurements(['actual 57s baseline 28s'])
        data={'generated_utc':'2026-10-26T01:00:00Z','jobs':c['jobs'],'counts':{'failure':3},'coverage':[], 'glm_telemetry':[{'stage':'triage','status':'budget_rejected'}]}
        report=render_research_report(data,[c],'','disabled')
        self.assertIn('recurring PR-only families qualify',report)
        self.assertIn('actual 57.0s, baseline 28.0s, limit unknowns',report)
        self.assertNotIn('## Performance evidence',report)
        self.assertIn('budget_rejected',report)
        self.assertNotIn('No main-specific cluster',report)

if __name__=='__main__':unittest.main()
