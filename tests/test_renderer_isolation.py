"""Report rendering must not inspect an installed server's retained snapshots."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
from scripts.research_report import render_research_report
import test_v22_research_mode as research_tests

class RendererIsolationTests(unittest.TestCase):
    def test_existing_report_length_contract_with_populated_server_history(self):
        before=Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as d:
                os.chdir(d)
                p=Path('reports/snapshots/old.json');p.parent.mkdir(parents=True)
                p.write_text(json.dumps({'generated_utc':'2026-10-22T10:15:00Z',
                    'jobs':[{'job_id':900+i,'workflow':'unrelated-'+str(i),'conclusion':'success'} for i in range(100)]}))
                result=unittest.TestResult()
                research_tests.ResearchTests('test_research_report_main_first_bounded_pr_watchlist_no_ghost').run(result)
                self.assertEqual(result.errors,[])
                self.assertEqual(result.failures,[])
        finally:os.chdir(before)
    def test_explicit_historical_coverage_still_rendered_without_disk_lookup(self):
        data={'generated_utc':'2026-10-26T10:00:00Z','jobs':[],'counts':{},'coverage':[],
              'coverage_matrix':[{'date':'2026-10-22','workflow':'explicit-historical-workflow','testcase':'historical-testcase','scope':'main','jobs':1,'success':1}]}
        with patch('scripts.ci_investigation.load_saved_history',side_effect=AssertionError('renderer must not read disk')):
            text=render_research_report(data,[],'','disabled')
        self.assertNotIn('## Observed coverage matrix',text)
        self.assertNotIn('explicit-historical-workflow',text)
        self.assertEqual(data['coverage_matrix'][0]['testcase'],'historical-testcase')

    def test_issue_layout_counts_latest_verdicts_and_keeps_evidence_local(self):
        from scripts.research_report import ci_results
        job=research_tests.job()
        job.update(conclusion='failure',head_sha='a'*40,
            run_url='https://github.com/sgl-project/sglang/actions/runs/22',
            test_steps=[{'name':'Run test','conclusion':'failure'}],
            observation_accounting={'configuration_evidence':[
                {'text':'--suite 2-npu'}, {'text':'--partition-id 0'}]},
            testcase_observations=[{'testcase':'wan|retry','status':'FAIL'},
                {'testcase':'wan|retry','status':'PASS'}, {'testcase':'startup','status':'FAIL'}])
        data={'generated_utc':'2026-10-23T10:15:00Z','jobs':[job],
              'comparison_jobs':[dict(job)],'counts':{'failure':1}}
        text=render_research_report(data,[],'','disabled',history=[{'date':'2026-10-22','counts':{'success':1}}])
        self.assertLess(text.index('## CI results'),text.index('## Main branch — priority incidents'))
        self.assertIn('2-npu / partition 0',text)
        self.assertIn('1 PASS, 1 FAIL',text)
        self.assertIn('✅ PASS',text)
        self.assertIn('❌ FAIL',text)
        self.assertIn('<summary>Diagnostics, evidence, and PR watchlist</summary>',text)
        from scripts.research_report import status_label
        for state,icon in [('skipped','⏭️'),('cancelled','🚫'),('in_progress','⏳'),('unknown','❔')]:
            self.assertTrue(status_label(state).startswith(icon))
        self.assertNotIn('## Executive summary',text)
        self.assertIn('wan&#124;retry',text)
        self.assertEqual(ci_results(data).count('| **Total** | **1** |'),1)
        for section in ('## Verified PR metadata','## Performance evidence','## Observed coverage matrix'):
            self.assertNotIn(section,text)
        self.assertIn('```mermaid\nxychart-beta',text)

    def test_trend_excludes_future_old_dates_and_does_not_fill_missing_dates(self):
        from scripts.research_report import collection_trend
        history=[{'date':day,'counts':{'failure':n}} for day,n in
                 [('2026-10-19',999),('2026-10-21',2),('2026-10-26',999),('2026-10-27',999)]]
        text=collection_trend(history,{'date':'2026-10-26','counts':{'success':3,'failure':1},'coverage_capped':True})
        self.assertIn('x-axis ["2026-10-21", "2026-10-26"]',text)
        self.assertIn('bar [2, 1]',text)
        self.assertIn('line [2, 4]',text)
        self.assertIn('2/7 dates',text)
        self.assertNotIn('999',text)

if __name__=='__main__':unittest.main()
