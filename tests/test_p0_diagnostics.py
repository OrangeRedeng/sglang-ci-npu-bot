"""P0 regression scenarios from the October audit (synthetic, redacted logs)."""
import json
from pathlib import Path
import tempfile
import unittest
from scripts import log_context as log
from scripts import npu_diffusion_ci_report as ci
from scripts.failure_history import correlate_snapshots, render_failure_history
from scripts.testcase_failures import parse_testcase_failures


def analyze(text):
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'job.log'; path.write_text(text)
        return log.analyze_file(path)


def snapshot(text, day='25', jid=1, **extra):
    a=analyze(text)
    job={'job_id':jid,'run_id':jid,'conclusion':'failure','workflow':'pr-test-npu.yml',
         'name':'multimodal-gen-test-4-npu-a3 (0)','head_branch':'feature',
         'run_event':'pull_request','pr_numbers':[jid],
         'head_sha':str(jid)*40,'created_at':f'2026-10-{day}T10:00:00Z',
         'failure_cases':a['failure_cases'],'failure_cases_version':2,
         # Legacy event packet remains deliberately truncated, as in production.
         'log_analysis':{'events':a['events'][:12]}}
    job.update(extra)
    return {'generated_utc':f'2026-10-{day}T12:00:00Z','jobs':[job]}


def http(code):
    return f'''___ TestGlm.test_distributed ___
Traceback (most recent call last):
E   requests.exceptions.HTTPError: {code} Server Error: /v1/images/generations
E   RuntimeError: Model generation returned no output. Error from scheduler: Unknown error
E   Exception: retry() exceed maximum number of retries.
FAILED test/x.py::TestGlm::test_distributed - Exception: retry() exceed maximum number of retries.
'''


class P0Diagnostics(unittest.TestCase):
    def test_traceback_is_never_diagnostic(self):
        a=analyze('Traceback (most recent call last):\n  File "x", line 1\nRuntimeError: bad tensor shape\n')
        self.assertEqual(a['primary']['signature'],'RuntimeError: bad tensor shape')
        self.assertIsNone(log.classify('Traceback (most recent call last):'))

    def test_http_cause_survives_scheduler_and_retry_wrappers(self):
        a=analyze(http(500));cases=a['failure_cases']
        self.assertEqual(len(cases),1)
        self.assertIn('500 Server Error',cases[0]['signature'])
        self.assertNotIn('retry()',cases[0]['signature'])
        self.assertNotEqual(cases[0]['id'],analyze(http(404))['failure_cases'][0]['id'])

    def test_distributed_fallback_not_traceback(self):
        a=analyze('''___ test_diffusion_generation[wan2_2_t2v_14b_w8a8_2npu] ___
Traceback (most recent call last):
E   RuntimeError: Native Diffusers fallback for transformer_2 cannot honor requested distributed execution:
E   sp_degree=2, ulysses_degree=2
FAILED test/x.py::test_diffusion_generation[wan2_2_t2v_14b_w8a8_2npu] - RuntimeError: worker failed
''')
        self.assertEqual(len(a['failure_cases']),1)
        self.assertIn('cannot honor',a['failure_cases'][0]['signature'])
        self.assertIn('ulysses_degree=2',a['failure_cases'][0]['signature'])

    def test_two_different_testcases_no_cross_attribution(self):
        text='''___ test_diffusion_generation[wan2_1] ___
E   PerformanceValidationError: actual 57.09 baseline 28.19 threshold 35.24
___ test_diffusion_generation[joyai] ___
E   RuntimeError: Server exited early (code 1)
FAILED test/x.py::test_diffusion_generation[wan2_1] - PerformanceValidationError: E2E too slow
FAILED test/x.py::test_diffusion_generation[joyai] - RuntimeError: Server exited early (code 1)
'''
        data=snapshot(text); clusters=ci.clusters_from_snapshot(data)
        self.assertEqual(sum(c['count'] for c in clusters),2)
        by_case={c['jobs'][0]['primary_test_case']:c['signature'] for c in clusters}
        self.assertIn('PerformanceValidationError',by_case['wan2_1'])
        self.assertNotIn('PerformanceValidationError',by_case['joyai'])

    def test_all_45_failed_and_error_cases_survive_display_cap(self):
        text='\n'.join(f'{"ERROR" if i%2 else "FAILED"} test/x.py::test_case_{i} - ValueError: shape {i}' for i in range(45))
        data=snapshot(text)
        clusters=ci.clusters_from_snapshot(data)
        self.assertEqual(len(clusters),45)
        self.assertEqual(sum(c['count'] for c in clusters),45)
        self.assertEqual(data['jobs'][0]['failure_cases'],json.loads(json.dumps(data))['jobs'][0]['failure_cases'])

    def test_explicit_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'job.log';p.write_text('\n'.join(f'ERROR test/x.py::test_{i} - ValueError: fail' for i in range(4)))
            cases,meta=parse_testcase_failures(p,max_cases=2)
        self.assertEqual(len(cases),2)
        self.assertFalse(meta['complete'])
        self.assertEqual(meta['case_overflow'],2)

    def test_summary_without_cause_is_explicit_unknown(self):
        a=analyze('ERROR test/x.py::test_setup\n')
        self.assertIn('Unclassified',a['failure_cases'][0]['signature'])

    def test_cross_day_500_to_404_without_issue_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); old=snapshot(http(500),'24',1)
            (root/'npu-diffusion-ci-2026-10-24.json').write_text(json.dumps(old))
            data=snapshot(http(404),'25',2)
            h=correlate_snapshots(data,ci.clusters_from_snapshot(data),root)
        self.assertEqual(len(h['rows']),1)
        row=h['rows'][0]
        self.assertEqual(row['status'],'signature changed')
        self.assertIn('500',row['previous_signatures'][0]);self.assertIn('404',row['signatures'][0])
        self.assertFalse(h['closure_evidence'])
        self.assertIn('unverified',row['comparability'])
        self.assertIn('signature changed',render_failure_history(h))

    def test_scope_and_runtime_gt_changes_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=snapshot(http(500),'24',1)
            old['jobs'][0]['case_evidence']={'test/x.py::TestGlm::test_distributed':{'runtime_id':'cann-a','gt_sha':'a'*40}}
            (root/'npu-diffusion-ci-2026-10-24.json').write_text(json.dumps(old))
            data=snapshot(http(404),'25',2,case_evidence={'test/x.py::TestGlm::test_distributed':{'runtime_id':'cann-b','gt_sha':'b'*40}})
            h=correlate_snapshots(data,ci.clusters_from_snapshot(data),root)
            self.assertEqual(h['rows'][0]['status'],'new in retained history')
            data=snapshot(http(500),'25',3,run_event='push',head_branch='main',head_repository='sgl-project/sglang')
            h=correlate_snapshots(data,ci.clusters_from_snapshot(data),root)
            self.assertEqual(h['rows'][0]['status'],'new in retained history')

    def test_overlapping_job_not_new_reproduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=snapshot(http(500),'24',1)
            (root/'npu-diffusion-ci-2026-10-24.json').write_text(json.dumps(old))
            data=snapshot(http(500),'25',1)
            h=correlate_snapshots(data,ci.clusters_from_snapshot(data),root)
        self.assertEqual(h['rows'][0]['status'],'overlapping observation')

    def test_repeated_symptom_across_independent_prs_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=snapshot(http(500),'24',1)
            (root/'npu-diffusion-ci-2026-10-24.json').write_text(json.dumps(old))
            data=snapshot(http(500),'25',2)
            h=correlate_snapshots(data,ci.clusters_from_snapshot(data),root)
        self.assertEqual(h['rows'][0]['status'],'continuing')

    def test_parametrized_node_with_spaces_and_error_setup(self):
        a=analyze('___ ERROR at setup of TestA.test_a[batch 2] ___\nE   ValueError: invalid shape\nERROR test/x.py::TestA::test_a[batch 2] - ValueError: invalid shape\n')
        self.assertEqual(len(a['failure_cases']),1)
        self.assertIn('[batch 2]',a['failure_cases'][0]['testcase'])
        self.assertIn('invalid shape',a['failure_cases'][0]['signature'])

    def test_offline_reparse_keeps_missing_logs_and_rebuilds_full_cases(self):
        from scripts.reparse_failure_snapshots import reparse, main
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); raw=root/'raw-logs'/'2026-10-25';raw.mkdir(parents=True)
            data=snapshot(http(500)); data['jobs'][0]['failure_cases']=[]
            data['jobs'].append({'job_id':999,'conclusion':'failure','fingerprint':'old'})
            (raw/'job-1.log').write_text(http(404))
            target=root/'npu-diffusion-ci-2026-10-25.json'
            original=json.dumps(data);target.write_text(original)
            main(['--reports',str(root)])
            self.assertEqual(target.read_text(),original)
            main(['--reports',str(root),'--apply'])
            saved=json.loads(target.read_text())
            self.assertIn('404',saved['jobs'][0]['failure_cases'][0]['signature'])
            self.assertEqual(saved['jobs'][1]['fingerprint'],'old')
            self.assertEqual(next((root/'p0-backups').glob('*/*.json')).read_text(),original)

    def test_secrets_redacted_in_new_case_records(self):
        a=analyze('FAILED test/x.py::test_secret - RuntimeError: api_key=abcdefghijk987654\n')
        self.assertNotIn('abcdefghijk987654',json.dumps(a['failure_cases']))

if __name__=='__main__': unittest.main()
