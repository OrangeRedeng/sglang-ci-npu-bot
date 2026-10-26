"""Realistic v21 regressions: ghost PRs, branch scope, GLM section boundaries."""
import unittest
from unittest.mock import Mock
from pathlib import Path
import re

from scripts.issue_sync import enrich_run_provenance
from scripts.pr_forensics import discover_pr_leads
from scripts.report_editor import (extract_cluster_section, short_hypotheses,
                                   vetted_cluster_section, render_human_report)
from scripts.issue_taxonomy import classify_cluster,job_scope

class GH:
    def __init__(self):self.calls=[]
    def get(self,path,params=None):
        self.calls.append(path)
        if path.endswith('actions/runs/39442651715'):
            return dict(id=39442651715,event='push',head_branch='main',
                        head_repository=dict(full_name='sgl-project/sglang'),
                        pull_requests=[dict(number=1),dict(number=2),dict(number=1)])
        if path=='/search/issues': return {'items':[]}
        if path.endswith('/pulls/1'):return dict(number=1,html_url='https://github.com/sgl-project/sglang/pull/1',title='Add flashinfer')
        if path.endswith('/pulls/2'):return dict(number=2,html_url='https://github.com/sgl-project/sglang/pull/2',title='SRT json')
        if path.endswith('/pulls/1/files'):return [dict(filename='3rdparty/flashinfer')]
        if path.endswith('/pulls/2/files'):return [dict(filename='python/sglang/lang/ir.py')]
        raise AssertionError(path)

class TestV21(unittest.TestCase):
    def test_ghost_prs_from_push_main_are_never_tested_branch(self):
        j={'run_id':39442651715, 'pr_numbers':[1,2,1],
           'created_at':'2026-10-20T14:49:38Z',
           'head_branch':None}
        c={'stable_id':'NPU-D004','fingerprint':'abc','signature':'Consistency check failed for qwen_image_t2i_2npu:',
           'first_seen_snapshot':'2026-10-20T05:30:17Z','jobs':[j]}
        gh=GH();stats=enrich_run_provenance(gh,{},[c])
        self.assertEqual(stats['runs_enriched'],1)
        self.assertEqual(j['pr_numbers'],[])
        self.assertEqual(classify_cluster(c)['scope'],'main')
        leads=discover_pr_leads(gh,[c],max_api_calls=20,max_per_cluster=4)['NPU-D004']
        self.assertFalse(any(x['number'] in {1,2} for x in leads))
        self.assertFalse(any('/pulls/1' in x or '/pulls/2' in x for x in gh.calls))

    def test_known_push_main_clears_stale_prs(self):
        j={'run_event':'push','head_branch':'main',
            'head_repository':'sgl-project/sglang','pr_numbers':[1,2]}
        c={'jobs':[j]}
        enrich_run_provenance(Mock(),{},[c])
        self.assertEqual(j['pr_numbers'],[])

    def test_numbered_glm_hypotheses_are_visible(self):
        text='''### NPU-D004 (P1)
1. **H1 — deterministic pinned-GT mismatch, suspect only**
2. **H2 — runtime environment drift, not verified**

### NPU-D005 (P2)
1. **H1 — quantization opt-in needs verification**
'''
        h=short_hypotheses(text,'NPU-D004',limit=2)
        self.assertEqual(len(h),2)
        self.assertIn('pinned-GT',h[0])
        self.assertNotIn('quantization',str(h))

    def test_cross_cluster_synthesis_is_not_appended_to_last_issue(self):
        text='''### NPU-D004 (P1)
1. **H1 — GT mismatch**
### NPU-D005 (P2)
1. **H1 — PR-only mismatch**
### NPU-D006 (P3)
1. **H1 — network mirror outage**
## Candidate Fixes with Verification
- **D004-FC1:** Revert unrelated component
- **D005-FC1:** Disable quant
'''
        section=extract_cluster_section(text,'NPU-D006')
        self.assertIn('network mirror',section)
        self.assertNotIn('D004-FC1',section)
        self.assertNotIn('D005-FC1',section)

    def test_recovery_keeps_nested_candidate_fix_section_within_own_incident(self):
        text='''GLM synthesis failed (GLMBudgetExceeded); partial per-cluster analysis follows.
### NPU-D004
# Investigation
## Candidate Fixes with Verification
- Proposed conditional fix
### NPU-D005
# Investigation
'''
        section=extract_cluster_section(text,'NPU-D004')
        self.assertIn('Proposed conditional fix',section)
        self.assertNotIn('NPU-D005',section)

    def test_glm_stale_pr_references_are_not_republished(self):
        analysis='''### NPU-D004 (P1)
1. **H1 — confirmed metric mismatch**
Run-linked PRs #1/#2 from 2024 are context only.
2. **H2 — PR #45118 may touch the VAE code path**
### NPU-D005 (P2)
'''
        c=dict(stable_id='NPU-D004',pr_leads=[dict(number=45118)])
        text=vetted_cluster_section(analysis,c)
        self.assertNotIn('#1/#2',text)
        self.assertIn('#45118',text)
        self.assertEqual(len(short_hypotheses(analysis,'NPU-D004',cluster=c)),2)

    def test_pr_only_never_equivalent_to_main(self):
        pr={'run_event':'pull_request','head_branch':'main','head_repository':'a/sglang','pr_numbers':[44738]}
        self.assertEqual(job_scope(pr),'not-merged')
        self.assertEqual(classify_cluster(dict(jobs=[pr]))['priority'],'P2')
        self.assertEqual(job_scope(dict(pr,run_event=None)),'unconfirmed')




if __name__=='__main__':unittest.main()
