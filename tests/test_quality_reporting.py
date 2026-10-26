"""Evidence regressions reproduced from issue #23 and collection edge cases."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import npu_diffusion_ci_report as ci
from scripts.collection_evidence import attach_observations, main_runs, source_evidence
from scripts.complete_report import render_complete_report, validate_report, main_assessment
from scripts.ci_investigation import compact_cluster, build_regression_context
from scripts.incident_verifier import comparable_jobs, parse_evidence
from scripts.log_context import analyze_file
from scripts.model_evidence import bounded_json
from scripts.testcase_observations import parse_observations

FIXTURE=Path(__file__).parent/'fixtures/job_121128243405_excerpt.log'
NOW=datetime(2026,10,25,10,15,tzinfo=timezone.utc)
NODE='x.py::Test::test_diffusion_generation[wan]'


def job(jid=1, **kw):
    data=dict(job_id=jid,run_id=10,attempt=1,workflow='pr-test-npu.yml',
              name='multimodal-gen-test-1-npu-a3 (0)',status='completed',conclusion='failure',
              run_event='schedule',head_branch='main',head_repository='sgl-project/sglang',
              source_repository='sgl-project/sglang',head_sha='a'*40,
              created_at='2026-10-25T09:00:00Z',completed_at='2026-10-25T10:00:00Z',
              url=f'https://github.com/sgl-project/sglang/actions/runs/10/job/{jid}',
              run_url='https://github.com/sgl-project/sglang/actions/runs/10')
    data.update(kw);return data


def snapshot(jobs):
    from collections import Counter
    return dict(schema_version=3,generated_utc='2026-10-25T10:15:00Z',lookback_hours=24,jobs=jobs,
                counts=dict(Counter(j['conclusion'] for j in jobs)),coverage=[],errors=[])


class QualityTests(unittest.TestCase):
    def test_real_pytest_header_metrics_and_import_ownership(self):
        result=analyze_file(FIXTURE)
        rec=job(failure_cases=result['failure_cases']);attach_observations(rec,FIXTURE)
        wan=next(c for c in rec['failure_cases'] if c['testcase'].startswith('wan2'))
        m=wan['performance'][0]
        self.assertAlmostEqual(m['actual_seconds'],57.0891776)
        self.assertAlmostEqual(m['baseline_seconds'],28.19451)
        self.assertAlmostEqual(m['threshold_seconds'],35.2431375)
        self.assertEqual(wan['causal_signature'],"ModuleNotFoundError: No module named 'cuda'")
        flux=next(o for o in rec['testcase_observations'] if o['testcase'].startswith('flux'))
        self.assertEqual(flux['status'],'PASS')
        self.assertNotIn('causal_signature',flux)
        self.assertTrue(any(m['metric']=='E2E Latency' for m in flux['performance']))
        self.assertEqual(parse_evidence(FIXTURE),{})

    def test_repeated_sections_retain_attempts_but_deduplicate_wrappers(self):
        block="__ Test.test_diffusion_generation[wan] __\nPerformanceValidationError: Validation failed for 'E2E Latency'.\nActual: 57090ms\nExpected: 28194.51ms\nLimit: 35243.1375ms\n"
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log';p.write_text(block+block.replace('57090','57091')+f'FAILED {NODE} - PerformanceValidationError: slow\n')
            c=analyze_file(p)['failure_cases'][0]
        self.assertEqual(len(c['execution_sections']),2)
        self.assertEqual([m['actual_seconds'] for m in c['performance']],[57.09,57.091])

    def test_consistency_pass_is_not_final_pass_and_infinite_psnr_is_json_safe(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log';p.write_text(NODE+"\n[Consistency Check] wan: PASSED\nFrame 0: clip=1 ssim=1 psnr=inf mean_abs_diff=0 PASS\n")
            obs,_=parse_observations(p)
            self.assertEqual(obs[0]['status'],'UNKNOWN')
            self.assertEqual(obs[0]['metrics']['psnr'],'inf')
            json.dumps(obs,allow_nan=False)

    def test_neighbour_and_unowned_exception_isolation(self):
        text="ModuleNotFoundError: no owner\n"+NODE+"\nModuleNotFoundError: owned\nFAILED\nx.py::test_other PASSED\n"
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log';p.write_text(text);obs,meta=parse_observations(p)
        self.assertEqual(obs[0]['status'],'FAIL')
        self.assertNotIn('causal_signature',obs[1])
        self.assertEqual(meta['unowned_exceptions'][0]['text'],'ModuleNotFoundError: no owner')

    def test_main_discovery_is_independent_and_excludes_future_and_pr_runs(self):
        valid=dict(id=1,head_branch='main',event='schedule',head_repository={'full_name':'sgl-project/sglang'},created_at='2026-10-24T12:00:00Z')
        gh=Mock();gh.get.return_value={'workflow_runs':[valid,dict(valid,id=2,event='pull_request'),dict(valid,id=3,created_at='2026-10-25T12:00:00Z')]}
        runs,complete=main_runs(gh,'sgl-project/sglang',NOW)
        self.assertEqual([r['id'] for r in runs],[1]);self.assertTrue(complete)
        self.assertEqual(gh.get.call_args.args[1]['branch'],'main')
        gh.get.return_value={'workflow_runs':[dict(valid,id=i) for i in range(33)]}
        self.assertFalse(main_runs(gh,'sgl-project/sglang',NOW)[1])

    def test_capped_daily_query_keeps_main_and_comparisons_out_of_denominator(self):
        main=dict(id=10,run_attempt=2,head_sha='b'*40,head_branch='main',event='schedule',
                  head_repository={'full_name':'sgl-project/sglang'},created_at='2026-10-23T12:00:00Z')
        pr=dict(main,id=11,head_branch='feature',event='pull_request',created_at='2026-10-25T09:00:00Z')
        gh=Mock()
        def jobs(_gh,rid,attempt):
            return [dict(id=rid,name='multimodal-gen-test-1-npu-a3 (0)',status='completed',conclusion='success')]
        with patch.object(ci,'WORKFLOWS',('pr-test-npu.yml',)),patch('scripts.collection_evidence.main_runs',return_value=([main],True)),patch.object(ci,'workflow_runs',return_value=([pr],False)),patch.object(ci,'get_jobs',side_effect=jobs):
            result=ci.snapshot(gh,NOW,24,1,0)
        self.assertEqual(result['counts'],{'success':1})
        self.assertEqual(result['comparison_jobs'][0]['run_id'],10)
        self.assertEqual(result['comparison_jobs'][0]['attempt'],2)
        self.assertEqual(result['latest_main_run_id'],10)

    def test_cancelled_job_retains_failed_step_and_queued_status(self):
        run=dict(id=10,run_attempt=1,created_at='2026-10-25T09:00:00Z')
        jobs=[dict(id=1,name='multimodal-gen-test-1-npu-a3',status='completed',conclusion='cancelled',steps=[dict(name='Run test',status='completed',conclusion='failure')]),
              dict(id=2,name='multimodal-gen-test-4-npu-a3',status='queued',conclusion=None)]
        with patch.object(ci,'WORKFLOWS',('pr-test-npu.yml',)),patch('scripts.collection_evidence.main_runs',return_value=([],True)),patch.object(ci,'workflow_runs',return_value=([run],True)),patch.object(ci,'get_jobs',return_value=jobs):
            r=ci.snapshot(Mock(),NOW,24,1,0)
        self.assertEqual(r['counts'],{'cancelled':1,'queued':1})
        self.assertEqual(r['jobs'][0]['test_steps'][0]['conclusion'],'failure')
        self.assertEqual(len(r['jobs']),2)

    def test_missing_artifact_and_pinned_config_are_disclosed(self):
        gh=Mock()
        def get(path,params):
            if path.endswith('/artifacts'):raise RuntimeError('expired')
            self.assertEqual(params['ref'],'a'*40)
            return dict(encoding='base64',content=base64.b64encode(b'run --suite 2-npu\ncoverage_mode: false').decode())
        gh.get.side_effect=get
        e=source_evidence(gh,'sgl-project/sglang',[job()])
        self.assertIn('artifacts unavailable',e['limitations'][0])
        self.assertIn('a'*40,e['sources'])

    def test_scheduled_main_recovery_keeps_comparability_guards(self):
        bad=job();good=job(2,head_sha='b'*40,created_at='2026-10-25T10:01:00Z')
        self.assertTrue(comparable_jobs(bad,good))
        self.assertFalse(comparable_jobs(bad,dict(good,head_repository='fork/sglang')))
        self.assertFalse(comparable_jobs(bad,dict(good,head_sha='a'*40)))

    def test_explicit_green_boundary_has_unverified_profile(self):
        bad=job(primary_test_case='wan',testcase_observations=[dict(testcase='wan',status='FAIL')])
        good=job(2,run_id=9,conclusion='success',created_at='2026-10-24T09:00:00Z',testcase_observations=[dict(testcase='wan',status='PASS')])
        c=dict(fingerprint='a'*14,count=1,jobs=[bad])
        data=snapshot([bad]);data['comparison_jobs']=[good]
        ctx=build_regression_context(data,[c])['clusters'][0]
        self.assertEqual(ctx['last_verified_green_testcase']['job_id'],2)
        self.assertFalse(ctx['good_bad_boundary_verified'])
        compact=compact_cluster(c)['jobs'][0]
        self.assertEqual(compact['head_branch'],'main')
        self.assertEqual(compact['head_repository'],'sgl-project/sglang')

    def test_complete_report_legacy_unknowns_tables_and_validation(self):
        data=snapshot([job(name='partition|0\nline')])
        report=render_complete_report(data,[],'','disabled')
        self.assertTrue(validate_report(data,report))
        self.assertEqual(report.count('\n## '),11)
        self.assertIn('partition&#124;0<br>line',report)
        self.assertIn('no retained daily evidence',report)
        self.assertIn('age ',main_assessment(data))
        with self.assertRaisesRegex(ValueError,'counts disagree'):
            validate_report(dict(data,counts={'success':1}),report)
        with self.assertRaisesRegex(ValueError,'Future'):
            validate_report(dict(data,jobs=[job(created_at='2026-10-26T09:00:00Z')]),report)

    def test_valid_bounded_model_json_preserves_required_provenance(self):
        value=dict(head_branch='main',head_repository='sgl-project/sglang',observations=[dict(excerpt='x'*5000) for _ in range(40)])
        text=bounded_json(value,2000)
        self.assertLessEqual(len(text),2000)
        obj=json.loads(text)
        self.assertEqual(obj['head_branch'],'main');self.assertTrue(obj['evidence_capped'])

    def test_stage_resume_reuses_investigation_and_rejects_changed_evidence(self):
        c=dict(fingerprint='a'*14,stable_id='OBS-'+('a'*14),count=1,jobs=[job(primary_test_case='wan')])
        env={'GLM_API_KEY':'test','GLM_API_BASE_URL':'https://example.invalid','GLM_MODEL':'test',
             'GLM_MAX_INVESTIGATIONS':'1','PR_SEARCH_ENABLED':'0','GLM_REASONING_EFFORT':'high'}
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,env):
            os.environ['GLM_STAGE_CACHE_DIR']=str(Path(d)/'cache')
            with patch('scripts.glm_http.chat_completion',side_effect=['Investigation complete',TimeoutError('timeout')]) as call:
                text,status=ci.glm_analysis(snapshot([job()]),[deepcopy(c)],mode='investigate')
                self.assertTrue(status.startswith('partial'));self.assertIn('Investigation complete',text)
                self.assertEqual(call.call_count,2)
            with patch('scripts.glm_http.chat_completion',return_value='Synthesis complete') as call:
                _,status=ci.glm_analysis(snapshot([job()]),[deepcopy(c)],mode='investigate')
                self.assertTrue(status.startswith('completed'));self.assertEqual(call.call_count,1)
            changed=deepcopy(c);changed['jobs'][0]['head_sha']='c'*40
            with patch('scripts.glm_http.chat_completion',return_value='Changed evidence analysis') as call:
                ci.glm_analysis(snapshot([job()]),[changed],mode='investigate')
                self.assertEqual(call.call_count,2)
            os.environ.pop('GLM_STAGE_CACHE_DIR',None)

    def test_cancelled_failure_is_classified_without_changing_job_counts(self):
        j=job(conclusion='cancelled',test_steps=[dict(name='Run test',conclusion='failure')],
              failure_cases_version=2,failure_cases=[dict(id='a'*14,testcase='wan',category='pytest_failure',signature='RuntimeError: failure')])
        data=snapshot([j])
        self.assertEqual(len(ci.clusters_from_snapshot(data)),1)
        self.assertEqual(data['counts'],{'cancelled':1})

    def test_fingerprint_alias_preserves_existing_incident(self):
        cluster=dict(fingerprint='b'*14,fingerprint_aliases=['a'*14],count=1,jobs=[job()])
        existing=dict(number=2,title='NPU-D018 CUDA import',body='<!-- npu-diffusion-fingerprint:'+('a'*14)+' -->',
                      html_url='https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/2')
        with patch.object(ci,'get_existing_issues',return_value=[existing]):
            ci.assign_cluster_ids(Mock(),[cluster],False,0,publish=False)
        self.assertEqual(cluster['stable_id'],'NPU-D018')
        self.assertEqual(cluster['fingerprint'],'a'*14)

    def test_offline_replay_excludes_future_log_verdict(self):
        from scripts.preview_report import replay
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); log=root/'log'
            log.write_text('2026-10-25T10:10:00Z '+NODE+'\n2026-10-25T10:20:00Z [server-test] PASSED diffusion testcase: wan\n')
            data=snapshot([job()])
            with patch.object(ci,'GitHub',side_effect=AssertionError('network forbidden')):
                output=replay(data,{'1':log},root/'output')
            self.assertTrue(output.is_file())
            self.assertEqual(data['jobs'][0]['testcase_observations'][0]['status'],'UNKNOWN')
            self.assertIn('post-snapshot',data['coverage'][-1])

    def test_historical_main_failure_is_rendered_without_daily_count_inflation(self):
        j=job(2,run_id=9,comparison_only=True,created_at='2026-10-23T09:00:00Z',completed_at='2026-10-23T10:00:00Z')
        data=snapshot([]);data['comparison_jobs']=[j]
        data['historical_main_clusters']=[dict(fingerprint='a'*14,count=1,comparison_only=True,jobs=[j],signature='ImportError: historical')]
        report=render_complete_report(data,[],'','disabled')
        self.assertTrue(validate_report(data,report))
        self.assertIn('historical comparison, excluded from daily counts',report)
        self.assertEqual(data['counts'],{})

    def test_future_completed_outcome_and_duplicate_attempt_rejected(self):
        data=snapshot([job(completed_at='2026-10-25T11:00:00Z')])
        with self.assertRaisesRegex(ValueError,'Future job outcome'):
            validate_report(data,render_complete_report(data,[],'','disabled'))
        data=snapshot([job(),job()])
        with self.assertRaisesRegex(ValueError,'Duplicate job'):
            validate_report(data,render_complete_report(data,[],'','disabled'))


if __name__=='__main__':unittest.main()
