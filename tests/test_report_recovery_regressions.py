"""Production regressions from the October 26 installation/publisher logs."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import npu_diffusion_ci_report as ci
from scripts.research_report import render_research_report
from scripts.analysis_recovery import save_analysis, load_analysis
from scripts.recover_daily_report import recover
from scripts.testcase_observations import parse_observations
from scripts.complete_report import render_complete_report
from scripts.ci_investigation import build_regression_context


def incident(scope, fingerprint, testcase, jid):
    jobs=[]
    for i in range(3):
        jobs.append(dict(job_id=jid+i,run_id=100+jid+i,attempt=1,
            conclusion='failure',run_event='schedule' if scope=='main' else 'pull_request',
            head_branch='main' if scope=='main' else 'feature',
            head_repository='sgl-project/sglang',source_repository='sgl-project/sglang',
            head_sha='a'*40,pr_numbers=[] if scope=='main' else [43367+i],
            primary_test_case=testcase,created_at='2026-10-26T09:00:00Z',
            url=f'https://github.com/sgl-project/sglang/actions/runs/{100+jid+i}/job/{jid+i}'))
    return dict(fingerprint=fingerprint,stable_id='OBS-'+fingerprint,count=3,
        signature='RuntimeError: Server exited early (code 1).',jobs=jobs,
        pr_leads=[dict(number=46536,url='https://github.com/sgl-project/sglang/pull/46536',
                       relation='potential_fix',title='Guard CUDA import')])


class ProductionReportTests(unittest.TestCase):
    def test_pr_leads_in_deep_section_do_not_replace_watchlist_clusters(self):
        clusters=[incident('main','a'*14,'wan',1),incident('not-merged','b'*14,'joyai',10)]
        data=dict(generated_utc='2026-10-26T10:00:00Z',schema_version=3,
            counts={'failure':6},jobs=[j for c in clusters for j in c['jobs']],coverage=[])
        report=render_research_report(data,clusters,'Completed synthesis','completed: investigate')
        self.assertIn('PR-only watchlist',report)
        self.assertIn('joyai',report)
        self.assertIn('pull/46536',report)

    def test_video_summary_and_frames_remain_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'video.log'
            p.write_text('x.py::test_diffusion_generation[video]\nMetrics: ssim=0.7 psnr=18\nFrame 0: ssim=0.7 psnr=18 FAIL\nFrame 1: ssim=1 psnr=inf PASS\nFAILED\n')
            obs,_=parse_observations(p)
        self.assertEqual(obs[0]['metrics']['ssim'],0.7)
        self.assertEqual(obs[0]['metrics']['psnr'],18)
        self.assertEqual(obs[0]['frame_metrics'][1]['metrics']['psnr'],'inf')
        self.assertEqual(obs[0]['metrics_source'],'testcase summary')

    def test_saved_history_green_is_retained_in_complete_report(self):
        c=incident('main','a'*14,'wan',1)
        good=dict(c['jobs'][0],job_id=99,head_sha='b'*40,conclusion='success',created_at='2026-10-25T09:00:00Z',
                  testcase_observations=[dict(testcase='wan',status='PASS')])
        data=dict(generated_utc='2026-10-26T10:00:00Z',jobs=c['jobs'],counts={'failure':3})
        data['regression_context']=build_regression_context(data,[c],[{'generated_utc':'2026-10-25T10:00:00Z','jobs':[good]}])
        registry=render_complete_report(data,[c],'','disabled').split('## 7.')[1].split('## 8.')[0]
        self.assertIn('b'*40,registry)

    def test_omitted_model_context_change_invalidates_cached_stages(self):
        c=incident('main','a'*14,'wan',1)
        data=dict(generated_utc='2026-10-26T10:00:00Z',jobs=c['jobs'],counts={'failure':3})
        data['jobs'][0]['uncompacted_evidence']=[{'diagnostic':str(i)} for i in range(50)]
        env=dict(GLM_API_KEY='test',GLM_API_BASE_URL='https://example.invalid',GLM_MODEL='test',
                 GLM_MAX_INVESTIGATIONS='1',PR_SEARCH_ENABLED='0',GLM_ANALYZER_COMMAND='')
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,env):
            os.environ['GLM_STAGE_CACHE_DIR']=directory
            os.environ['GLM_RECOVERY_CONTEXT_FILE']=str(Path(directory)/'context.json')
            with patch('scripts.glm_http.chat_completion',return_value='Completed investigation and synthesis') as request:
                ci.glm_analysis(deepcopy(data),[deepcopy(c)],mode='investigate')
                self.assertEqual(request.call_count,2)
                ci.glm_analysis(deepcopy(data),[deepcopy(c)],mode='investigate')
                self.assertEqual(request.call_count,2)
                changed=deepcopy(data);changed['jobs'][0]['uncompacted_evidence'][-1]['diagnostic']='new root error'
                ci.glm_analysis(changed,[deepcopy(c)],mode='investigate')
                self.assertEqual(request.call_count,4)

    def test_historical_reparse_retains_passes_and_applies_cutoff(self):
        from scripts.reparse_failure_snapshots import reparse
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);logs=root/'2026-10-26';logs.mkdir()
            (logs/'job-1.log').write_text('2026-10-26T09:00:00Z x.py::test_diffusion_generation[wan]\n2026-10-26T09:01:00Z [server-test] PASSED diffusion testcase: wan\n2026-10-26T11:01:00Z ModuleNotFoundError: future\n')
            data={'generated_utc':'2026-10-26T10:00:00Z','jobs':[{'job_id':1,'conclusion':'success'}]}
            stats=reparse(data,root)
        self.assertEqual(stats['reparsed_jobs'],1)
        self.assertEqual(data['jobs'][0]['testcase_observations'][0]['status'],'PASS')
        self.assertEqual(data['jobs'][0]['observation_accounting']['post_snapshot_lines_excluded'],1)
        self.assertEqual(data['jobs'][0]['observation_accounting']['unowned_exceptions'],[])

    def test_future_step_and_parent_outcomes_are_not_reported_at_snapshot(self):
        now=ci.parse_timestamp('2026-10-26T10:00:00Z')
        run=dict(id=1,created_at='2026-10-26T09:00:00Z',updated_at='2026-10-26T11:00:00Z',
                 status='completed',conclusion='failure',run_attempt=1)
        job=dict(id=1,name='multimodal-gen-test-1-npu-a3 (0)',status='completed',conclusion='failure',
                 completed_at='2026-10-26T11:00:00Z',steps=[dict(name='Run test',status='completed',
                 conclusion='failure',started_at='2026-10-26T09:30:00Z',completed_at='2026-10-26T10:30:00Z')])
        with patch.object(ci,'WORKFLOWS',('pr-test-npu.yml',)),patch('scripts.collection_evidence.main_runs',return_value=([],True)),patch.object(ci,'workflow_runs',return_value=([run],True)),patch.object(ci,'get_jobs',return_value=[job]):
            data=ci.snapshot(Mock(),now,24,1,0)
        self.assertEqual(data['counts'],{'in_progress':1})
        self.assertIsNone(data['jobs'][0]['test_steps'][0]['conclusion'])
        self.assertEqual(data['jobs'][0]['test_steps'][0]['status'],'in_progress')
        self.assertIsNone(data['jobs'][0]['run_conclusion'])
        self.assertIsNone(data['runs'][0]['conclusion'])


class SavedSynthesisTests(unittest.TestCase):
    date='2026-10-26'
    analysis='## Executive summary\n\n'+('Observed testcase failures and bounded source evidence. Proposed checks remain conditional. '*5)

    def prepare(self,root,bound=True):
        c=incident('not-merged','a'*14,'wan',1)
        data=dict(schema_version=3,generated_utc='2026-10-26T10:00:00Z',jobs=c['jobs'],
                  counts={'failure':3},coverage=[],glm_synthesis_status='completed',
                  pr_evidence={c['fingerprint']:c['pr_leads']})
        (root/f'npu-diffusion-ci-{self.date}.json').write_text(json.dumps(data))
        folder=root/'analysis';folder.mkdir()
        p=folder/f'npu-diffusion-ci-{self.date}-full.md'
        if bound:save_analysis(p,data,self.analysis,'completed: investigate')
        else:p.write_text(self.analysis)
        return data,p

    def test_preview_uses_saved_synthesis_without_network_or_paid_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.prepare(root)
            with patch.object(ci,'GitHub',side_effect=AssertionError('No GitHub')),patch.object(ci,'glm_analysis',side_effect=AssertionError('No paid call')):
                self.assertIsNone(recover(root,self.date))
            text=(root/'recovered'/f'npu-diffusion-ci-{self.date}-issue.md').read_text()
            self.assertIn('PR-only watchlist',text)
            self.assertIn('no new model calls',text)

    def test_legacy_requires_opt_in_and_completion_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            data,p=self.prepare(Path(directory),bound=False)
            with self.assertRaisesRegex(ValueError,'allow-legacy-analysis'):load_analysis(p,data)
            _,status=load_analysis(p,data,True)
            self.assertIn('binding unavailable',status)
            data['glm_synthesis_status']='failed: timeout'
            with self.assertRaisesRegex(ValueError,'does not prove'):load_analysis(p,data,True)

    def test_binding_mismatch_never_falls_back_to_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            data,p=self.prepare(Path(directory))
            p.write_text(self.analysis+'changed')
            with self.assertRaisesRegex(ValueError,'does not match'):load_analysis(p,data,True)

    def test_changed_snapshot_is_rejected_before_any_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data,_=self.prepare(root)
            data['jobs'][0]['head_sha']='c'*40
            (root/f'npu-diffusion-ci-{self.date}.json').write_text(json.dumps(data))
            gh=Mock()
            with self.assertRaisesRegex(ValueError,'does not match'):recover(root,self.date,True,gh=gh)
            self.assertEqual(gh.mock_calls,[])

    def test_publication_creates_daily_only_and_calls_no_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.prepare(root)
            gh=Mock();gh.create_issue.return_value={'html_url':'https://github.com/example/bot/issues/24'}
            with patch.object(ci,'TARGET_REPO','example/bot'),patch.object(ci,'get_existing_issues',return_value=[]),patch.object(ci,'ensure_label'),patch.object(ci,'glm_analysis',side_effect=AssertionError('No model')):
                url=recover(root,self.date,True,gh=gh)
            self.assertTrue(url.endswith('/24'))
            self.assertEqual(gh.create_issue.call_count,1)
            gh.issue.assert_not_called()
            self.assertEqual(gh.create_issue.call_args.args[0]['labels'],[ci.DAILY_LABEL])

    def test_concurrent_issue_edit_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.prepare(root)
            old=dict(number=24,body=f'<!-- npu-diffusion-report-date:{self.date} -->',updated_at='before')
            gh=Mock();gh.get.return_value=dict(old,updated_at='after')
            with patch.object(ci,'TARGET_REPO','example/bot'),patch.object(ci,'get_existing_issues',return_value=[old]):
                with self.assertRaisesRegex(RuntimeError,'changed concurrently'):recover(root,self.date,True,gh=gh)
            gh.issue.assert_not_called();gh.create_issue.assert_not_called()


if __name__=='__main__':unittest.main()
