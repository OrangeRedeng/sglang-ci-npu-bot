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
        self.assertIn('explicit-historical-workflow',text)
        self.assertIn('historical-testcase',text)

if __name__=='__main__':unittest.main()
