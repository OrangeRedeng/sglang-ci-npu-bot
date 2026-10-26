"""v22 regression tests: no network, model calls, or live Issues writes."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import log_context as logs, npu_diffusion_ci_report as bot
from scripts.incident_lifecycle import build_continuity


def job(job_id=1, scope='main', testcase='glm_image_distributed', category='pytest_failure'):
    event,branch = ('push','main') if scope=='main' else (('pull_request','fix/test') if scope=='pr' else ('',''))
    return dict(job_id=job_id,name='multimodal-gen-test-4-npu-a3 (0)',
        url=f'https://github.com/sgl-project/sglang/actions/runs/22/job/{job_id}',
        run_id=22,attempt=1,created_at='2026-10-23T10:00:00Z',
        head_repository='sgl-project/sglang',source_repository='sgl-project/sglang',
        run_event=event,head_branch=branch,pr_numbers=[44738] if scope=='pr' else [],
        failure_category=category, primary_test_case=testcase,
        error_signature=f'FAILED {testcase} - exception',conclusion='failure')


def cluster(jobs,fp='aabbcc11223344'):
    return dict(fingerprint=fp,signature=jobs[0]['error_signature'],
         first_seen_snapshot='2026-10-23T10:00:00Z',last_seen_snapshot='2026-10-23T10:00:00Z',
         count=len(jobs),severity=110,jobs=jobs)


class FakeGitHub:
    def __init__(self):self.created=[];self.modified=[]
    def create_issue(self,payload):
        self.created.append(payload)
        return {'html_url':f'https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/{30+len(self.created)}'}
    def issue(self,number,payload):self.modified.append((number,payload))


class ResearchTests(unittest.TestCase):
    def test_normal_hccl_init_not_a_failure_even_with_timestamp(self):
        for line in ['[10-23 06:02:20] world_size=1 rank=0 local_rank=0 distributed_init_method=tcp://127.0.0.1:30005 backend=hccl timeout=3600',
                     '[10-23 07:08:20] world_size=2 rank=0 local_rank=0 distributed_init_method=tcp://127.0.0.1:30005 backend=hccl timeout=3600']:
            self.assertIsNone(logs.classify(line))
        a=logs.normalize('[10-23 06:02:20] NPU failed at rank=0')
        b=logs.normalize('[10-23 07:08:20] NPU failed at rank=2')
        self.assertEqual(a,b)

    def test_actual_runtime_error_not_hidden_by_hccl_filter(self):
        self.assertEqual(logs.classify('rank=0 HCCL connection error failed')[0],'ascend_runtime')

    def test_glm_image_terminal_failure_overrides_hccl_noise(self):
        lines=['[10-23 06:02:20] world_size=2 rank=0 local_rank=0 distributed_init_method=tcp://127.0.0.1:30005 backend=hccl timeout=3600',
           'requests.exceptions.HTTPError: 500 Server Error: Internal Server Error for url: http://127.0.0.1:47395/v1/images/generations',
           'RuntimeError: Model generation returned no output. Error from scheduler: Unknown error',
           'FAILED sglang/multimodal_gen/test/server/ascend/test_glm_image_distributed.py::TestGlmImageDistributedNpu::test_external_ar_batching_multi_output_disaggregation_performance - Exception: retry() exceed maximum number of retries.']
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'run.log';path.write_text('\n'.join(lines))
            result=logs.analyze_file(path)
        self.assertEqual(result['primary']['category'],'pytest_failure')
        self.assertIn('test_external_ar_batching_multi_output_disaggregation_performance',result['primary']['testcase'])
        self.assertNotIn('ascend_runtime',result['categories'])
        cases=logs.failure_cases_from_events(result['events'])
        self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]['category'],'pytest_failure')

    def test_consistency_remains_one_family_even_with_pytest_summary(self):
        fixture=Path(__file__).parent/'fixtures/ascend_diffusion_consistency.log'
        result=logs.analyze_file(fixture)
        cases=logs.failure_cases_from_events(result['events'])
        self.assertEqual(len(cases),1)
        self.assertEqual(cases[0]['category'],'diffusion_consistency')

    def test_performance_and_consistency_do_not_share_fingerprint(self):
        t='qwen_image_t2i_2npu'
        self.assertNotEqual(logs.stable_key('performance_regression',t,t),
                            logs.stable_key('diffusion_consistency',t,t))

    def test_default_pr_only_suppressed_without_allocating_ids(self):
        gh=FakeGitHub(); c=cluster([job(scope='pr')]); clusters=[c]
        with patch.object(bot,'get_existing_issues',return_value=[]),\
             patch.dict(os.environ, {'CREATE_PR_ONLY_ISSUES':'0','MAX_PUBLISHED_MAIN_ISSUES':'3'}):
            bot.assign_cluster_ids(gh,clusters,False,12,publish=False)
            self.assertTrue(c['stable_id'].startswith('OBS-'))
            self.assertFalse(gh.created)
            bot.assign_cluster_ids(gh,clusters,False,12,publish=True)
        self.assertFalse(gh.created)
        self.assertEqual(c['issue_url'],None)
        self.assertNotIn('NPU-D',c['stable_id'])
        self.assertIn('PR-only',c['not_published_reason'])

    def test_main_issue_id_only_after_successful_create(self):
        gh=FakeGitHub(); c=cluster([job()]);cs=[c]
        with patch.object(bot,'get_existing_issues',return_value=[]),\
             patch.object(bot,'ensure_label'),\
             patch.object(bot,'sync_incident_labels',return_value=['main']),\
             patch.dict(os.environ,{'CREATE_PR_ONLY_ISSUES':'0','MAX_PUBLISHED_MAIN_ISSUES':'3'}):
            bot.assign_cluster_ids(gh,cs,False,12,publish=False)
            self.assertTrue(c['stable_id'].startswith('OBS-'))
            bot.assign_cluster_ids(gh,cs,False,12,publish=True)
        self.assertEqual(c['stable_id'],'NPU-D004')
        self.assertEqual(len(gh.created),1)
        self.assertIn('test execution failure',gh.created[0]['title'])

    def test_no_ghost_ids_after_capped_issue_creation(self):
        gh=FakeGitHub()
        clusters=[cluster([job(i)],fp=f'ff{i:012x}') for i in range(4)]
        with patch.object(bot,'get_existing_issues',return_value=[]),\
             patch.object(bot,'ensure_label'),\
             patch.object(bot,'sync_incident_labels',return_value=['main']),\
             patch.dict(os.environ,{'MAX_PUBLISHED_MAIN_ISSUES':'2'}):
            bot.assign_cluster_ids(gh,clusters,False,12,publish=True)
        self.assertEqual([c['stable_id'] for c in clusters[:2]],['NPU-D004','NPU-D005'])
        self.assertTrue(all(c['stable_id'].startswith('OBS-') for c in clusters[2:]))
        self.assertEqual(len(gh.created),2)

    def test_legacy_pr_incident_retained_without_issue_rewrites(self):
        gh=FakeGitHub();c=cluster([job(scope='pr')]);old={'title':'[NPU-D020] old PR noise',
            'body':'<!-- npu-diffusion-fingerprint:aabbcc11223344 -->',
            'number':20,'state':'open',
            'html_url':'https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/20'}
        with patch.object(bot,'get_existing_issues',return_value=[old]):
            bot.assign_cluster_ids(gh,[c],False,3,publish=True)
        self.assertEqual(c['stable_id'],'NPU-D020')
        self.assertFalse(gh.created)
        self.assertFalse(gh.modified)

    def test_research_report_main_first_bounded_pr_watchlist_no_ghost(self):
        main=cluster([job(1,scope='main',testcase='qwen_image_t2i_2npu',category='diffusion_consistency')],fp='aa11')
        main['stable_id']='NPU-D004'
        main['issue_url']='https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/2'
        prs=[cluster([job(i,scope='pr',testcase='pr_case_'+str(i))],fp=f'00{i:012x}') for i in range(40,70)]
        for c in prs:c['stable_id']='OBS-'+c['fingerprint'];c['issue_url']=None
        data=dict(generated_utc='2026-10-23T10:15:00Z',lookback_hours=24,
            source_repository='sgl-project/sglang',counts={'failure':31},
            jobs=[{'job_id':i} for i in range(31)],coverage=['pr-test-npu.yml: CAPPED at 320 runs'],
            log_download_succeeded=31)
        from scripts.research_report import render_research_report
        result=render_research_report(data,[main]+prs,'','disabled')
        self.assertIn('## Main branch — priority incidents',result)
        self.assertIn('## PR-only watchlist (no standalone Issues)',result)
        self.assertIn('issues/2',result)
        self.assertNotIn('NPU-D021',result)
        self.assertLess(len(result),11000)
        self.assertIn('22 more PR-only',result)

    def test_lifecycle_does_not_treat_obs_as_real_issue(self):
        c=cluster([job(scope='pr')]);c['stable_id']='OBS-aabbcc11223344'
        x=build_continuity([c],[],[], '2026-10-23',{'coverage':[]})
        self.assertEqual(x['new'],[])
        self.assertEqual(x['continuing'],[])

    def test_profiles_do_not_raise_paid_cost_caps(self):
        script=Path(__file__).resolve().parents[1]/'deploy/apply-research-profile.sh'
        source=script.read_text()
        self.assertIn("'GLM_REASONING_EFFORT':'high'",source)
        self.assertIn("'CREATE_PR_ONLY_ISSUES':'0'",source)
        self.assertNotIn("'GLM_DAILY_BUDGET_CNY':",source)
        self.assertNotIn("'GITHUB_TOKEN':",source)


if __name__=='__main__': unittest.main()
