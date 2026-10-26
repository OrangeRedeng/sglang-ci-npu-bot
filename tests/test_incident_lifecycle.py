import unittest
from scripts.incident_lifecycle import (build_continuity, render_continuity,
    archive_previous_daily_reports,update_unobserved_issues,coverage_complete)

REPO='sglang-ci-npu-bot/sglang-ci-npu-bot'


def issue(n, iid, fp, state='open'):
    return {'number':n, 'title':f'[{iid}] testcase', 'state':state,
            'html_url':f'https://github.com/{REPO}/issues/{n}',
            'body':f'<!-- npu-diffusion-fingerprint:{fp} -->\n# {iid}',
            'labels':[{'name':'npu-diffusion-cluster'}]}


def day(n,date, ids):
    return {'number':n,'state':'open', 'labels':[{'name':'npu-diffusion-daily'}],
            'body':'<!-- npu-diffusion-report-date:'+date+' -->\n'+' '.join(ids)}

class FakeGitHub:
    def __init__(self): self.calls=[]
    def issue(self,n,payload): self.calls.append((n,payload))

class TestLifecycle(unittest.TestCase):
    def setUp(self):
        self.records=[issue(2,'NPU-D004','aa11'), issue(3,'NPU-D005','bb22'),
                      issue(4,'NPU-D006','cc33'), issue(5,'NPU-D007','dd44')]
        self.daily=[day(1,'2026-10-20',['NPU-D004','NPU-D005','NPU-D006']),
                    day(6,'2026-10-21',['NPU-D006','NPU-D007'])]
        self.clusters=[{'stable_id':'NPU-D006','fingerprint':'cc33'},
                       {'stable_id':'NPU-D007','fingerprint':'dd44'}]
        self.capped={'coverage':['pr-test-npu.yml: 320 runs inspected; CAPPED at 320 runs',
                'nightly-test-npu.yml: 19 runs inspected; complete within query window',
                'full-test-npu.yml: 0 runs inspected; complete within query window',
                'diffusion-ci-gt-gen-npu.yml: 0 runs inspected; complete within query window',
                'Job-list coverage: 339/339 run job-lists available.']}

    def test_real_october_history_distinguishes_new_and_unverified(self):
        x=build_continuity(self.clusters,self.records,self.daily,'2026-10-21',self.capped)
        self.assertEqual(x['new'],['NPU-D007'])
        self.assertEqual(x['continuing'],['NPU-D006'])
        self.assertEqual(x['not_observed'],['NPU-D004','NPU-D005'])
        self.assertFalse(x['coverage_complete'])
        rendered=render_continuity(x)
        self.assertIn('NPU-D004',rendered)
        self.assertIn('no verified testcase PASS',rendered)
        self.assertIn('coverage limited',rendered)

    def test_reappearance_is_not_treated_as_new_incident(self):
        daily = self.daily + [day(7,'2026-10-22',['NPU-D006','NPU-D007'])]
        clusters = [{'stable_id':'NPU-D004','fingerprint':'aa11'}]
        x=build_continuity(clusters,self.records,daily,'2026-10-23',self.capped)
        self.assertEqual(x['reappeared'],['NPU-D004'])
        self.assertEqual(x['new'],[])
        self.assertIn('reappeared',render_continuity(x))

    def test_annotates_missing_issue_without_closing(self):
        x=build_continuity(self.clusters,self.records,self.daily,'2026-10-21',self.capped)
        gh=FakeGitHub(); update_unobserved_issues(gh,x,self.records,REPO)
        self.assertEqual([n for n,_ in gh.calls],[2,3])
        self.assertTrue(all('state' not in p for _,p in gh.calls))
        self.assertTrue(all('NOT VERIFIED FIXED' in p['body'] for _,p in gh.calls))
        self.assertTrue(all('npu-diffusion-fingerprint' in p['body'] for _,p in gh.calls))
        # Idempotent if API returns updated body
        for n,p in gh.calls: self.records[n-2]['body']=p['body']
        gh2=FakeGitHub(); update_unobserved_issues(gh2,x,self.records,REPO)
        self.assertEqual(gh2.calls,[])

    def test_archives_daily_not_incident(self):
        gh=FakeGitHub()
        ids=archive_previous_daily_reports(gh,self.daily+self.records,'2026-10-21')
        self.assertEqual(ids,[1])
        self.assertEqual(gh.calls,[(1,{'state':'closed','state_reason':'completed'})])

    def test_absence_requires_complete_coverage(self):
        self.assertFalse(coverage_complete(self.capped))
        complete={'coverage':[x.replace('CAPPED at 320 runs','complete within query window')
            for x in self.capped['coverage']]}
        self.assertTrue(coverage_complete(complete))
        complete['coverage'][-1]='PARTIAL JOB COVERAGE: 20/30 job lists available'
        self.assertFalse(coverage_complete(complete))

    def test_mismatched_fingerprint_fails_closed(self):
        self.clusters[0]['fingerprint']='wrong'
        with self.assertRaises(ValueError):
            build_continuity(self.clusters,self.records,self.daily,'2026-10-21',self.capped)

if __name__ == '__main__': unittest.main()
