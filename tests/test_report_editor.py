"""Human-readable Issue smoke tests; no network or paid model calls."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import report_editor as ed
import reformat_report as fmt


def demo():
    data={'generated_utc':'2026-10-20T20:06:23Z','lookback_hours':24,
          'source_repository':'sgl-project/sglang','counts':{'failure':123,'success':753,'skipped':1198,'cancelled':348,'in_progress':4},
          'coverage':['pr-test-npu.yml: 939 runs inspected; complete within query window'],
          'log_download_succeeded':100,'jobs':[{'job_id':x} for x in range(2426)]}
    def job(id,t,metrics,checks):
        return {'job_id':id,'name':'multimodal-gen-test-4-npu-a3 (0)','url':f'https://github.com/sgl-project/sglang/actions/jobs/{id}',
           'failure_category':'diffusion_consistency','primary_test_case':t,'metrics':metrics,
           'failed_checks':checks,'created_at':'2026-10-20T05:30:17Z',
           'thresholds':{'ssim':{'op':'>=','value':0.95},'psnr':{'op':'>=','value':28}}}
    q={'stable_id':'NPU-D004','fingerprint':'abc123','issue_url':'https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/2',
       'signature':'Consistency check failed for qwen_image_t2i_2npu:',
       'severity':120,'count':99,'first_seen_snapshot':'2026-10-20T05:30:17Z','last_seen_snapshot':'2026-10-20T14:49:38Z',
       'jobs':[job(1,'qwen_image_t2i_2npu',{'ssim':0.9371,'psnr':23.6472},['ssim','psnr'])]}
    m={'stable_id':'NPU-D005','fingerprint':'zxy123','issue_url':'https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/3',
       'signature':'Consistency check failed for minimax_h3_t2va_2npu:',
       'severity':120,'count':2,'first_seen_snapshot':'2026-10-20T10:10:57Z','last_seen_snapshot':'2026-10-20T11:30:05Z',
       'jobs':[job(2,'minimax_h3_t2va_2npu',{'ssim':0.7753},['ssim'])]}
    analysis='''### NPU-D004 — fully investigated (P1)
**H1 (medium) — Test precision drift in the Qwen pipeline**
- Evidence: identical printed SSIM values
**H2 (low) — GT and config mismatch**

### NPU-D005 — triaged only (P2)
**S1 (low) — PR branch difference**
'''
    return data,[q,m],analysis


class TestReportEditor(unittest.TestCase):
    def test_report_short_and_links(self):
        data,clusters,analysis=demo()
        out=ed.render_human_report(data,clusters,analysis,'completed: investigate')
        self.assertLess(len(out),9000)
        self.assertIn('08:30 MSK',out)
        self.assertIn('13:10 MSK',out)
        self.assertIn('23 remain unclassified',out)
        self.assertIn('Test precision drift',out)
        self.assertIn('GT and config mismatch',out)
        self.assertIn('Potential fixes',out)
        self.assertIn('issues/2',out)
        self.assertNotIn('## Relevant NPU diffusion jobs',out)
        self.assertNotIn('NPU-D005 (P2)',out)

    def test_empty_model_still_has_candidate_fixes(self):
        data,clusters,_=demo()
        text=ed.render_human_report(data,clusters,'','failed')
        self.assertIn('Localize the quality regression',text)
        self.assertIn('Root cause',text)

    def test_malformed_timestamp_not_invented(self):
        self.assertEqual(ed.utc_msk('nonsense'),'unknown')

    def test_no_bogus_pass_from_success_jobs(self):
        data,clusters,analysis=demo()
        data['jobs'][0]['conclusion']='success'
        text=ed.render_human_report(data,clusters,analysis,'completed')
        self.assertIn('no last testcase-level green/first bad boundary has been established',text)

    def test_reformat_extract_and_verifies_id_signature(self):
        _,clusters,analysis=demo()
        old='''## Engineering investigation — hypotheses, not verified causes

%s

## Collection coverage and limitations
### [NPU-D004](https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/2) — 99 failed job(s)
- **Observed fact:** `Consistency check failed for qwen_image_t2i_2npu:`
### [NPU-D005](https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/3) — 2 failed job(s)
- **Observed fact:** `Consistency check failed for minimax_h3_t2va_2npu:`
'''%analysis
        self.assertIn('### NPU-D004',fmt.extract_previous_analysis(old))
        for c in clusters:c.pop('stable_id');c.pop('issue_url')
        self.assertEqual(fmt.restore_ids(clusters,old),2)
        self.assertEqual(clusters[0]['stable_id'],'NPU-D004')
        self.assertEqual(clusters[1]['stable_id'],'NPU-D005')

    def test_reformat_rejects_missing_id_on_publish(self):
        import json
        data,clusters,_=demo()
        data['jobs']=[]
        # A lightweight format-only CLI test; no network on default path.
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)
            snap=path/'snap.json';snap.write_text(json.dumps(data))
            old=path/'old.md';old.write_text('Old report without GLM analysis')
            with patch.object(fmt,'clusters_from_snapshot',return_value=clusters):
                self.assertEqual(fmt.main(['--snapshot',str(snap),'--previous-report',str(old),
                             '--output',str(path/'short.md')]),0)
            self.assertTrue((path/'short.md').exists())

if __name__=='__main__':unittest.main()

class TestPublicationSafety(unittest.TestCase):
    def test_cluster_issue_update_checks_fingerprint(self):
        import npu_diffusion_ci_report as ci
        data, clusters, analysis = demo()
        class GH:
            def __init__(self): self.updates=[]
            def get(self,path):
                return {'body':'<!-- npu-diffusion-fingerprint:abc123 -->\n# Original evidence'}
            def issue(self,num,payload): self.updates.append((num,payload))
        gh=GH()
        # The second cluster has another fingerprint, and must NOT be modified.
        with patch.object(ci,'TARGET_REPO','sglang-ci-npu-bot/sglang-ci-npu-bot'):
            ci.update_cluster_issue_details(gh,clusters,analysis,'completed: investigate')
        self.assertEqual(len(gh.updates),1)
        self.assertEqual(gh.updates[0][0],2)
        content=gh.updates[0][1]['body']
        self.assertIn('<!-- npu-diffusion-fingerprint:abc123 -->',content)
        self.assertIn('Test precision drift',content)
        self.assertIn('Candidate fixes',content)

    def test_cluster_issue_update_idempotent(self):
        import npu_diffusion_ci_report as ci
        _, clusters, analysis = demo()
        class GH:
            def __init__(self): self.content='<!-- npu-diffusion-fingerprint:abc123 -->\nEvidence'
            def get(self,path): return {'body':self.content}
            def issue(self,num,payload): self.content=payload['body']
        gh=GH()
        with patch.object(ci,'TARGET_REPO','sglang-ci-npu-bot/sglang-ci-npu-bot'):
            ci.update_cluster_issue_details(gh,clusters[:1],analysis,'completed: investigate')
            first=gh.content
            ci.update_cluster_issue_details(gh,clusters[:1],analysis,'completed: investigate')
        self.assertEqual(first,gh.content)
        self.assertEqual(first.count('<!-- npu-ci-report-language:en -->'),1)
