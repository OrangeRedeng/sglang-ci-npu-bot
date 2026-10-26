"""v17 PR evidence, English-only issue and safe refresh tests."""
import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import pr_forensics as pf
import report_editor as ed
import reanalyze_and_publish as refresh

class StubGH:
    def __init__(self):self.requests=[]
    def get(self,path,params=None):
        self.requests.append((path,params))
        if path=='/search/issues':
            if 'is:merged' in params['q']:
                return {'items':[{'number':101,'title':'[Fix] diffusion: qwen image consistency'},
                                 {'number':102,'title':'[Feat] diffusion: qwen image scheduler'}]}
            return {'items':[{'number':103,'title':'[Fix] diffusion: qwen image NPU mismatch'},
                             {'number':104,'title':'[Fix] CUDA Qwen image'},
                             {'number':105,'title':'[Feat] qwen-vl image decode'}]}
        num=int(path.rsplit('/',1)[-1]) if not path.endswith('/files') else int(path.split('/')[-2])
        if path.endswith('/files'):
            return [{'filename': 'python/sglang/multimodal_gen/runtime/pipelines/qwen_image/ascend.py','status':'modified'}] if num!=104 else [{'filename':'python/sglang/srt/cuda/llm.py'}]
        merged={'101':'2026-10-19T11:00:00Z','102':'2026-10-21T11:00:00Z'}.get(str(num))
        return {'number':num,'html_url':f'https://github.com/sgl-project/sglang/pull/{num}',
            'title':{101:'[Fix] diffusion: qwen image consistency',102:'[Feat] diffusion: qwen image scheduler',
                     103:'[Fix] diffusion: qwen image NPU mismatch',104:'[Fix] CUDA Qwen image',105:'[Feat] qwen-vl image decode'}[num],
            'merged_at':merged,'state':'closed' if merged else 'open'}

class TestPRForensics(unittest.TestCase):
    def setUp(self):
        self.cluster={'stable_id':'NPU-D004','signature':'Consistency check failed for qwen_image_t2i_2npu:',
          'first_seen_snapshot':'2026-10-20T05:30:17Z',
          'jobs':[{'run_event':'push','head_branch':'main','head_repository':'sgl-project/sglang','created_at':'2026-10-20T05:30:17Z'}]}
    def test_verified_links_and_temporal_boundary(self):
        gh=StubGH()
        leads=pf.discover_pr_leads(gh,[self.cluster],max_api_calls=24,max_per_cluster=3)['NPU-D004']
        numbers={x['number'] for x in leads}
        self.assertIn(101,numbers)
        self.assertIn(103,numbers)
        self.assertNotIn(102,numbers) # Merged AFTER observed failure: cannot cause earlier failure
        self.assertNotIn(104,numbers) # CUDA excluded
        self.assertNotIn(105,numbers) # no fix intent
        self.assertEqual(next(x for x in leads if x['number']==101)['relation'],'related_change')
        self.assertFalse(next(x for x in leads if x['number']==101)['code_path_verified'])
        self.assertTrue(all(x['url'].startswith('https://github.com/sgl-project/sglang/pull/') for x in leads))
    def test_tested_pr_is_not_claimed_as_cause(self):
        gh=StubGH()
        c=dict(self.cluster,jobs=[{'pr_numbers':[103], 'run_event':'pull_request', 'head_branch':'feature', 'head_repository':'sgl-project/sglang'}])
        leads=pf.discover_pr_leads(gh,[c],max_per_cluster=3)['NPU-D004']
        self.assertEqual(next(x for x in leads if x['number']==103)['relation'],'tested_branch')
        self.assertFalse(any(x['relation']=='possible_cause' for x in leads))
    def test_no_gh_no_hallucinated_links(self):
        self.assertEqual(pf.discover_pr_leads(None,[self.cluster]),{})
    def test_bad_repository_refused(self):
        with self.assertRaises(ValueError):
            pf.discover_pr_leads(StubGH(),[self.cluster],repo='not a repo')
    def test_english_report_and_pr_link(self):
        from test_report_editor import demo
        data,clusters,analysis=demo()
        clusters[0]['pr_leads']=[{'number':101,'url':'https://github.com/sgl-project/sglang/pull/101',
          'title':'[Fix] diffusion qwen image','relation':'possible_cause'}]
        output=ed.render_human_report(data,clusters,analysis,'completed: investigate')
        self.assertIsNone(__import__('re').search('[\\u0400-\\u04ff]',output))
        self.assertIn('pull/101',output)
        self.assertIn('possible cause',output)
        self.assertNotIn('pull/999',output)
    def test_issue_fingerprint_mapping(self):
        import npu_diffusion_ci_report as ci
        clusters=[{'fingerprint':'abc','signature':'something'}]
        issue={'title':'[NPU-D004] foo','body':'<!-- npu-diffusion-fingerprint:abc -->',
             'html_url':'https://github.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/2'}
        with patch.object(refresh,'TARGET_REPO','sglang-ci-npu-bot/sglang-ci-npu-bot'):
            self.assertEqual(refresh.map_existing_issues(clusters,[issue]),[])
            self.assertEqual(clusters[0]['stable_id'],'NPU-D004')
            self.assertEqual(refresh.map_existing_issues([{'fingerprint':'different'}],[issue]),['different'])

if __name__=='__main__':unittest.main()
