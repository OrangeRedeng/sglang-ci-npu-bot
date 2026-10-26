import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/sync-incident-continuity.py'
spec=importlib.util.spec_from_file_location('continuity_refresh',SCRIPT)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FakeGitHub:
    def __init__(self): self.calls=[]
    def issue(self,n,body):self.calls.append((n,body))

class TestRefresh(unittest.TestCase):
    def test_read_only_preview_and_safe_publication(self):
        src={"generated_utc":"2026-10-21T12:05:00Z","coverage": [
             x+': 1 runs inspected; complete within query window' for x in
             ('pr-test-npu.yml','nightly-test-npu.yml','full-test-npu.yml','diffusion-ci-gt-gen-npu.yml')]}
        incident={'number':2,'state':'open','title':'[NPU-D004] qwen_image_t2i_2npu',
                  'body':'<!-- npu-diffusion-fingerprint:aaaa -->\nNeeds work',
                  'labels':[{'name':'npu-diffusion-cluster'}],
                  'html_url':'https://github.com/example/issues/2'}
        previous={'number':1,'state':'open','labels':[{'name':'npu-diffusion-daily'}],
                  'body':'<!-- npu-diffusion-report-date:2026-10-20 -->\nNPU-D004'}
        current={'number':6,'state':'open','labels':[{'name':'npu-diffusion-daily'}],
                  'body':'<!-- npu-diffusion-report-date:2026-10-21 -->\n'
                         '# Report\n\n## Incidents: impact, suspected cause, next fix\nOther details'}
        gh=FakeGitHub()
        def lookup(gh,label):
            return [incident] if label=='npu-diffusion-cluster' else [previous,current]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'snapshot.json';p.write_text(json.dumps(src))
            out=Path(d)/'report.md'
            with patch.object(mod.ci,'get_existing_issues',side_effect=lookup), \
                 patch.object(mod.ci,'clusters_from_snapshot',return_value=[]):
                text, continuity=mod.run('2026-10-21',p,gh=gh)
                self.assertIn('Incident continuity',text)
                self.assertEqual(continuity['not_observed'],['NPU-D004'])
                self.assertEqual(gh.calls,[])
                with patch.dict(os.environ,{'TARGET_REPOSITORY':'sglang-ci-npu-bot/sglang-ci-npu-bot',
                                             'GITHUB_TOKEN':'testtoken',
                                             'ARCHIVE_PREVIOUS_DAILY_ISSUES':'1'}):
                    mod.run('2026-10-21',p,publish=True,report_path=out,gh=gh)
            self.assertEqual([i for i,_ in gh.calls],[6,2,1])
            self.assertEqual(gh.calls[-1][1]['state'],'closed')
            self.assertNotIn('state',gh.calls[1][1])
            self.assertTrue(out.exists())

    def test_requires_daily_and_correct_snapshot_date(self):
        src={'generated_utc':'2026-10-20T12:05:00Z','coverage':[]}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'snap.json';p.write_text(json.dumps(src))
            with self.assertRaisesRegex(ValueError,'different Moscow'):
                mod.run('2026-10-21',p,gh=FakeGitHub())

if __name__=='__main__':unittest.main()
