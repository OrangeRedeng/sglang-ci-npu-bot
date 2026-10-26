"""Regression tests for guarded administrative cleanup, no HTTP requests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from importlib.machinery import SourceFileLoader

ROOT=Path(__file__).resolve().parents[1]
m=SourceFileLoader('legacy_reconciler',str(ROOT/'scripts/reconcile-legacy-issues.py')).load_module()


def incident(n,scope='not-merged',title=None,body=None,state='open'):
    body=body or f'<!-- npu-diffusion-fingerprint:{n:016x} -->\n' \
        f'# NPU-D{n:03d} — report\n**Observed signature:** `FAILED test_diffusion_generation[case_{n}_2npu]`'
    return {'number':n,'title':title or f'[NPU-D{n:03d}] Broken old label',
            'state':state,'body':body,'labels':[{'name':'npu-diffusion-cluster'}, {'name':scope}],
            'html_url':f'https://github.com/{m.REPO}/issues/{n}'}


def daily(n,date,state='open'):
    return {'number':n,'state':state,'title':f'Daily {date}',
        'body':f'<!-- npu-diffusion-report-date:{date} -->\n# Report for {date}\n---',
        'labels':[{'name':'npu-diffusion-daily'}]}


class GithubStub:
    def __init__(self,issues):
        self.issues={x['number']:dict(x) for x in issues}
        self.writes=[]
        self.conflict=None
    def get(self,path,params=None):
        if params:
            return list(self.issues.values()) if params['page']==1 else []
        issue=int(path.rsplit('/',1)[1]); val=dict(self.issues[issue])
        if self.conflict==issue:val['body']+='\nchanged concurrently'
        return val
    def request(self,method,path,payload):
        self.writes.append((method,path,payload))
        n=int(path.rsplit('/',1)[1]); self.issues[n].update(payload)
        return dict(self.issues[n])


class LegacyMigrationTests(unittest.TestCase):
    def test_main_never_closed_pr_archived_daily_latest_kept(self):
        issues=[incident(2,'main'),incident(3),incident(16,body='<!-- npu-diffusion-fingerprint:0000000000000010 -->\n'
                  '[10-23 08:07:46] world_size=2 rank=0 local_rank=0 distributed_init_method=tcp://127.0.0.1:30005 backend=hccl timeout=3600'),
                daily(8,'2026-10-22'),daily(21,'2026-10-23')]
        actions,latest=m.plan(issues)
        self.assertEqual(latest,21)
        self.assertEqual([c['number'] for c in actions if c['action']=='archive_pr_only'],[3,16])
        self.assertEqual([c['number'] for c in actions if c['action']=='archive_daily'],[8])
        self.assertFalse(any(c['number']==2 and c['action']=='archive_pr_only' for c in actions))
        self.assertIn('HCCL init is not the cause',next(c for c in actions if c['number']==16)['new_title'])

    def test_unknown_scope_and_not_owned_untouched(self):
        a=incident(2,'unconfirmed'); b=incident(3)
        b['labels']=[{'name':'not-merged'}]
        acts,_=m.plan([a,b,daily(21,'2026-10-23')]);self.assertEqual(acts,[])

    def test_idempotent_archival_with_main_safety(self):
        i=[incident(2,'main'),incident(3),daily(21,'2026-10-23')]
        gh=GithubStub(i)
        with tempfile.TemporaryDirectory() as root:
            with patch.dict('os.environ',{'GITHUB_TOKEN':'dummy','TARGET_REPOSITORY':m.REPO}):
                original,planned,today=m.preview(gh)
                result=m.apply(gh,original,planned,today,Path(root))
                self.assertTrue(Path(result['backup_path']).is_file())
                self.assertEqual(Path(result['backup_path']).stat().st_mode & 0o777,0o600)
                self.assertEqual(gh.issues[3]['state'],'closed')
                self.assertIn('Not fixed / not resolved',gh.issues[3]['body'].replace('**',''))
                self.assertEqual(gh.issues[2]['state'],'open')
                self.assertIn(m.DAILY_MARKER,gh.issues[21]['body'])
                self.assertEqual(len([a for a in gh.writes if a[1].endswith('/3')]),1)
                _,next_actions,_=m.preview(gh)
                self.assertEqual(next_actions,[])
                self.assertIn('1 legacy PR-only',m.summary_section(list(gh.issues.values())))

    def test_concurrent_edit_skipped(self):
        gh=GithubStub([incident(3),daily(21,'2026-10-23')]);gh.conflict=3
        with tempfile.TemporaryDirectory() as root:
            with patch.dict('os.environ',{'GITHUB_TOKEN':'dummy','TARGET_REPOSITORY':m.REPO}):
                old,actions,last=m.preview(gh)
                outcome=m.apply(gh,old,actions,last,Path(root))
                self.assertEqual(len(outcome['skipped']),1)
                self.assertEqual(gh.issues[3]['state'],'open')

    def test_does_not_claim_hccl_is_root_cause(self):
        x=incident(16,body=('<!-- npu-diffusion-fingerprint:0000000000000010 -->\n'
          '**Observed signature:** `[10-23 08:07:46] world_size=2 rank=0 backend=hccl timeout=3600`'))
        self.assertEqual(m.family(x),'needs re-triage')
        body=m.archival_append(x,21,True)
        self.assertIn('must be re-triaged',body)
        self.assertNotIn('verified fixed',body)
        self.assertEqual(body,m.archival_append(dict(x,body=body),21,True))

    def test_existing_closed_main_never_reopened(self):
        x=incident(2,'main',state='closed');acts,_=m.plan([x]);self.assertEqual(acts,[])

if __name__=='__main__':unittest.main()
