import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.incident_verifier import (
    parse_evidence, profiles_equal, comparable_jobs, historical_failures,
    verify_resolution, apply_verified_resolutions)
from scripts.incident_lifecycle import build_continuity, mark_verified_continuity, update_unobserved_issues

REPO='sglang-ci-npu-bot/sglang-ci-npu-bot'
FP='abc123def98701'
SHA0='a'*40
SHA1='b'*40
SHA2='c'*40
PROFILE={'testcase':'qwen_image_t2i_2npu','gt_sha':'d'*40,'seed':42,
    'dtype':'bf16','scheduler':'euler','model':'Qwen-Image','runtime_id':'cann8-torchnpu-2.9',
    'resolution':'1024x1024'}

def job(num, sha=SHA0, conclusion='failure', event='push', case=True, when='2026-10-20T09:00:00Z'):
    rec={'run_id':num,'job_id':num+500,'run_event':event,'workflow':'pr-test-npu.yml',
         'name':'multimodal-gen-test-4-npu-a3 (0)', 'source_repository':'sgl-project/sglang',
         'head_repository':'sgl-project/sglang', 'head_sha':sha,
         'head_branch':'main' if event=='push' else 'feature',
         'pr_numbers':[444] if event=='pull_request' else [],
         'conclusion':conclusion,'created_at':when,
         'url':f'https://github.com/sgl-project/sglang/actions/jobs/{num+500}'}
    if case:
        rec.update({'primary_test_case':PROFILE['testcase'], 'fingerprint':FP,
                    'failure_category':'diffusion_consistency',
                    'failure_cases':[{'id':FP,'category':'diffusion_consistency','testcase':PROFILE['testcase']}],
                    'case_evidence':{PROFILE['testcase']:{**PROFILE,'status':'FAIL'}}})
    return rec

def issue():
    return {'number':2,'state':'open','title':'[NPU-D004] qwen_image_t2i_2npu',
        'body':f'<!-- npu-diffusion-fingerprint:{FP} -->\n# Investigate\n\nKeep user notes',
        'labels':[{'name':'npu-diffusion-cluster'}],
        'html_url':f'https://github.com/{REPO}/issues/2'}

def data(records):
    return {'generated_utc':'2026-10-21T12:00:00Z','source_repository':'sgl-project/sglang',
        'jobs':records, 'log_download_failed':0, 'failed_jobs_without_logs':0,
        'log_truncated':0,
        'coverage':[f'{x}: 2 runs inspected; complete within query window' for x in (
            'pr-test-npu.yml','nightly-test-npu.yml','full-test-npu.yml','diffusion-ci-gt-gen-npu.yml')]
        +['Job-list coverage: 8/8 run job-lists available.']}

class FakeGithub:
    def __init__(self, logs=None, current=None):
        self.logs=logs or {}
        self.current=current or issue()
        self.reads=[];self.writes=[]
    def request(self, verb, path, stream_to=None):
        self.reads.append(('request', path))
        if path not in self.logs: raise RuntimeError('unavailable log')
        Path(stream_to).write_text(self.logs[path])
        return {'downloaded_bytes':Path(stream_to).stat().st_size,'truncated':False}
    def get(self,path):
        self.reads.append(('get',path))
        if '/compare/' in path:
            if '...'+SHA1 in path or '...'+SHA2 in path:
                return {'status':'ahead','behind_by':0,'ahead_by':1}
            return {'status':'diverged','behind_by':1,'ahead_by':1}
        return self.current
    def issue(self,number,payload):
        self.writes.append((number,payload))
        self.current={**self.current,**payload}

def logs(*events):
    return '\n'.join('2026-10-21T13:00:00Z task '+
        'NPU_CI_TEST_RESULT: '+json.dumps(e) for e in events)

class TestVerifier(unittest.TestCase):
    def test_parse_only_explicit_verdict_full_config(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log'
            p.write_text('test_diffusion_generation[qwen_image_t2i_2npu] PASSED\n'
                         '1 passed\nConsistency check passed for qwen_image_t2i_2npu')
            self.assertEqual(parse_evidence(p), {})
            p.write_text(logs({**PROFILE,'status':'PASS'}))
            self.assertEqual(parse_evidence(p)[PROFILE['testcase']]['gt_sha'],PROFILE['gt_sha'])
            p.write_text(logs({**PROFILE,'status':'PASS'}, {**PROFILE,'status':'FAIL'}))
            self.assertEqual(parse_evidence(p), {})
            p.write_text(logs({**PROFILE,'status':'PASS','gt_sha':'bad'}))
            self.assertEqual(parse_evidence(p), {})
            p.write_text(logs({**PROFILE,'status':'PASS','seed':True}))
            self.assertEqual(parse_evidence(p), {})
            p.write_text(logs({**PROFILE,'status':'PASS','unexpected':'anything'}))
            self.assertEqual(parse_evidence(p), {})

    def test_reject_wrong_profile_or_testcase(self):
        failed={**PROFILE,'status':'FAIL'}
        passed={**PROFILE,'status':'PASS'}
        self.assertTrue(profiles_equal(failed,passed))
        for field in ('testcase','gt_sha','seed','dtype','scheduler','model','runtime_id','resolution'):
            alt=dict(passed)
            alt[field] = 'wrong' if field!='seed' else 99
            self.assertFalse(profiles_equal(failed,alt),field)
        alt=dict(passed);del alt['resolution']
        self.assertFalse(profiles_equal(failed,alt))

    def test_criteria_include_new_sha_scope_and_job_matrix(self):
        failed=job(1)
        passed=job(2,SHA1,'success',when='2026-10-21T09:00:00Z')
        self.assertTrue(comparable_jobs(failed,passed))
        self.assertFalse(comparable_jobs(failed,{**passed,'head_sha':SHA0}))
        self.assertFalse(comparable_jobs(failed,{**passed,'name':'another matrix'}))
        self.assertFalse(comparable_jobs(failed,{**passed,'run_event':'pull_request','head_branch':'feature','pr_numbers':[4]}))
        self.assertFalse(comparable_jobs(failed,{**passed,'created_at':'2026-10-19T09:00:00Z'}))
        fpr=job(1,event='pull_request')
        ppr=job(2,SHA1,'success',event='pull_request',when='2026-10-21T09:00:00Z')
        self.assertTrue(comparable_jobs(fpr,ppr))
        self.assertFalse(comparable_jobs(fpr,{**ppr,'pr_numbers':[445]}))

    def test_real_verified_resolution_then_close_and_idempotence(self):
        first=job(1)
        passing=[job(2,SHA1,'success',case=False,when='2026-10-21T09:00:00Z'),
                 job(3,SHA2,'success',case=False,when='2026-10-21T10:00:00Z')]
        gh=FakeGithub(logs={f'/repos/sgl-project/sglang/actions/jobs/{p["job_id"]}/logs':
            logs({**PROFILE,'status':'PASS'}) for p in passing})
        with tempfile.TemporaryDirectory() as d:
            history=Path(d)/'history.json';history.write_text(json.dumps(data([first])))
            snap=data(passing)
            continuity={'not_observed':['NPU-D004'],'rows':[{'id':'NPU-D004','status':'not observed','detail':''}]}
            proof=verify_resolution(gh,snap,[issue()],continuity,[history],output_dir=Path(d)/'download')
            self.assertEqual(set(proof),{'NPU-D004'})
            self.assertEqual(len(proof['NPU-D004']['passed']),2)
            self.assertEqual(list((Path(d)/'download').glob('*')),[])
            mark_verified_continuity(continuity,proof)
            self.assertIn('verified fixed',continuity['rows'][0]['status'])
            closed=apply_verified_resolutions(gh,proof,[issue()])
            self.assertEqual(closed,['NPU-D004'])
            self.assertEqual(gh.writes[0][1]['state'],'closed')
            self.assertIn('npu-ci-resolution:verified',gh.writes[0][1]['body'])
            self.assertIn('Keep user notes',gh.writes[0][1]['body'])
            self.assertEqual(apply_verified_resolutions(gh,proof,[issue()]),[])
            self.assertEqual(len(gh.writes),1)

    def test_no_closure_on_partial_coverage_truncated_or_same_run(self):
        passing=[job(2,SHA1,'success',case=False,when='2026-10-21T09:00:00Z'),
                 job(2,SHA2,'success',case=False,when='2026-10-21T10:00:00Z')]
        gh=FakeGithub(logs={f'/repos/sgl-project/sglang/actions/jobs/{p["job_id"]}/logs':
            logs({**PROFILE,'status':'PASS'}) for p in passing})
        with tempfile.TemporaryDirectory() as d:
            hist=Path(d)/'history.json';hist.write_text(json.dumps(data([job(1)])))
            c={'not_observed':['NPU-D004']}
            for modification in ({'coverage':['pr-test-npu.yml: CAPPED']},
                                 {'failed_jobs_without_logs':1}, {'log_truncated':1},
                                 {'log_download_failed':1}):
                self.assertEqual(verify_resolution(gh,{**data(passing),**modification},
                   [issue()],c,[hist],output_dir=Path(d)/'logs'),{})
            self.assertEqual(verify_resolution(gh,data(passing),[issue()],c,[hist],
                output_dir=Path(d)/'logs'),{})

    def test_pr_needs_explicit_opt_in_and_server_fingerprint_match(self):
        prior=job(1,event='pull_request')
        passing=[job(2,SHA1,'success',event='pull_request',case=False,when='2026-10-21T09:00:00Z'),
                 job(3,SHA2,'success',event='pull_request',case=False,when='2026-10-21T10:00:00Z')]
        gh=FakeGithub(logs={f'/repos/sgl-project/sglang/actions/jobs/{p["job_id"]}/logs':
            logs({**PROFILE,'status':'PASS'}) for p in passing})
        with tempfile.TemporaryDirectory() as d:
            hist=Path(d)/'hist';hist.write_text(json.dumps(data([prior])))
            self.assertEqual(verify_resolution(gh,data(passing),[issue()],
                {'not_observed':['NPU-D004']},[hist],output_dir=Path(d)/'logs'),{})
            p=verify_resolution(gh,data(passing),[issue()],
                {'not_observed':['NPU-D004']},[hist],output_dir=Path(d)/'logs',allow_pr=True)
            self.assertIn('NPU-D004',p)
            gh.current={**issue(),'body':'<!-- npu-diffusion-fingerprint:WRONG -->'}
            self.assertEqual(apply_verified_resolutions(gh,p,[issue()]),[])
            self.assertEqual(gh.writes,[])

if __name__=='__main__':unittest.main()

class TestAdditionalSafety(unittest.TestCase):
    def test_closure_refused_after_issue_edited_post_snapshot(self):
        gh=FakeGithub(current={**issue(),'updated_at':'2026-10-21T13:00:00Z'})
        proof={'NPU-D004':{'issue_number':2,'fingerprint':FP,'case':PROFILE['testcase'],
              'snapshot':'2026-10-21T12:00:00Z','failure_job':501,'failure_sha':SHA0,
              'passed':[{'job_id':502,'url':'https://github.com/sgl-project/sglang/actions/jobs/502',
                         'head_sha':SHA1,'run_id':2},
                        {'job_id':503,'url':'https://github.com/sgl-project/sglang/actions/jobs/503',
                         'head_sha':SHA2,'run_id':3}]}}
        self.assertEqual(apply_verified_resolutions(gh,proof,[issue()]),[])
        self.assertFalse(gh.writes)

    def test_missing_baseline_structured_failure_can_never_autoclose(self):
        original=job(1)
        original.pop('case_evidence')
        self.assertEqual(historical_failures(data([original]),[]),{})

    def test_same_matrix_later_unknown_failure_blocks_closure(self):
        from scripts.incident_verifier import verify_resolution
        after=job(99,SHA2,'failure',case=False,when='2026-10-21T10:30:00Z')
        succeeding=[job(2,SHA1,'success',case=False,when='2026-10-21T09:00:00Z'),
                    job(3,SHA2,'success',case=False,when='2026-10-21T10:00:00Z')]
        with tempfile.TemporaryDirectory() as d:
            hist=Path(d)/'baseline.json';hist.write_text(json.dumps(data([job(1)])))
            gh=FakeGithub()
            result=verify_resolution(gh,data(succeeding+[after]),[issue()],
                {'not_observed':['NPU-D004']},[hist],output_dir=Path(d)/'logs')
            self.assertEqual(result,{})
            self.assertEqual(gh.reads,[])
