"""Regression cases transcribed from the October 8 English reference report.

These are parser/renderer fixtures, not a live replay or current CI findings.
"""
import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analysis_evidence import performance_measurements
from testcase_failures import parse_testcase_failures
from report_actions import experiment_plan, render_full_analysis, evidence_completeness
from research_report import render_research_report

REFERENCE="""PerformanceValidationError:
[request 1/1] [performance] Validation failed for 'E2E Latency'.
Actual:   471070.4301ms
Expected: 155106.2100ms
Limit:    193882.7625ms (rel_tol: 25.0%, abs_pad: 20.0ms)
"""

class ReferenceTests(unittest.TestCase):
    def test_original_assertion_expected_limit_and_pad(self):
        m=performance_measurements(REFERENCE.splitlines())[0]
        self.assertEqual(m['metric'],'E2E Latency')
        self.assertAlmostEqual(m['actual_seconds'],471.0704301)
        self.assertAlmostEqual(m['baseline_seconds'],155.10621)
        self.assertAlmostEqual(m['threshold_seconds'],193.8827625)
        self.assertEqual(m['tolerance_pct'],25)
        self.assertEqual(m['absolute_pad_seconds'],.02)
        self.assertAlmostEqual(m['actual_baseline_ratio'],3.037082,places=5)
    def test_expected_is_not_globally_assumed_baseline(self):
        m=performance_measurements(['Actual: 4s Expected: 2s'])[0]
        self.assertNotIn('baseline_seconds',m)
    def test_distinct_metric_blocks_do_not_cross_pair(self):
        m=performance_measurements((REFERENCE+REFERENCE.replace('E2E Latency','Average denoise step').replace('471070.4301','9185.84')).splitlines())
        self.assertEqual(len(m),2)
        self.assertEqual(m[1]['metric'],'Average denoise step')
        self.assertAlmostEqual(m[1]['actual_seconds'],9.18584)
    def test_long_trace_preserves_metrics_and_testcase_http_statuses(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'log'
            p.write_text('___ test_diffusion_generation[minimax] ___\n'+REFERENCE+'\n'.join(['stack frame']*200)+'\n___ test_distributed ___\nRuntimeError: GLM AR error: 400 Client Error: Bad Request\nHTTPError: 500 Server Error: Internal Server Error\nFAILED x.py::test_diffusion_generation[minimax] - PerformanceValidationError: too slow\nFAILED x.py::test_distributed - Exception: retry() exceed maximum\n')
            cases,_=parse_testcase_failures(p)
            self.assertEqual(cases[0]['http_statuses'],[])
            self.assertEqual(cases[1]['http_statuses'],[400,500])
            self.assertEqual(cases[1]['performance'],[])
            self.assertEqual(cases[0]['performance'][0]['baseline_seconds'],155.10621)
    def test_family_specific_experiment_and_acceptance(self):
        action,acceptance=experiment_plan({'signature':'PerformanceValidationError','jobs':[]})
        self.assertIn('Cache-DiT',action)
        self.assertIn('Do not raise the baseline',acceptance)
        action,acceptance=experiment_plan({'signature':'RuntimeError: Native Diffusers fallback cannot honor requested distributed execution','jobs':[]})
        self.assertIn('SP/Ulysses/TP',action)
        self.assertIn('single-device',acceptance)
    def test_markdown_preserved_and_unverified_pr_filtered(self):
        text='## OBS-abcdef01234567\n\n- **H1:** test\n- See https://github.com/sgl-project/sglang/pull/999\n- See #12\n'
        output=render_full_analysis(text,[{'pr_leads':[{'number':12}]}])
        self.assertIn('\n\n- **H1:**',output)
        self.assertNotIn('pull/999',output)
        self.assertIn('#12',output)
    def test_missing_config_and_current_day_history_not_overstated(self):
        c={'fingerprint':'a'*14,'signature':'PerformanceValidationError','count':1,'jobs':[{'job_id':1,'conclusion':'failure','run_event':'pull_request','pr_numbers':[1],'primary_test_case':'minimax','head_sha':'b'*40,'performance':performance_measurements(REFERENCE.splitlines())}]}
        data={'generated_utc':'2026-10-26T09:00:00Z','counts':{'failure':1},'jobs':c['jobs'],'coverage':[]}
        report=render_research_report(data,[c],'## Test\n\n- Hypothesis','disabled',history=[{'date':'2026-10-26','counts':{'failure':999}}],include_deep=True)
        self.assertIn('3.04x baseline',report)
        self.assertNotIn('| 2026-10-26 | 999',report)
        self.assertIn('Acceptance criteria',report)
        self.assertIn('Parent workflow failures',report)
        self.assertIn('## Test\n\n- Hypothesis',report)
        self.assertIn('Missing:',evidence_completeness(c))

if __name__=='__main__':unittest.main()
