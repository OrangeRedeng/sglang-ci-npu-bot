import unittest
from unittest.mock import Mock
from scripts.issue_taxonomy import job_scope, classify_cluster, merged_labels, ordered_clusters
from scripts.issue_sync import enrich_run_provenance, sync_cluster_labels

BASE={'run_event':'push','head_branch':'main','head_repository':'sgl-project/sglang',
      'source_repository':'sgl-project/sglang'}

def c(jobs, count=1):
    return {'fingerprint':'abcdef123456','jobs':jobs,'count':count,'signature':'check failed', 'severity':120}

class TestIssueTaxonomy(unittest.TestCase):
    def test_actual_main_push(self):
        self.assertEqual(job_scope(BASE),'main')
        self.assertEqual(classify_cluster(c([BASE]))['priority'],'P1')
    def test_main_push_with_linked_merged_pr_still_main(self):
        self.assertEqual(job_scope(dict(BASE, pr_numbers=[123])), 'main')
    def test_pull_request_targeting_main_never_main(self):
        record=dict(BASE,run_event='pull_request',head_branch='feature')
        self.assertEqual(job_scope(record),'not-merged')
        self.assertEqual(classify_cluster(c([record],200))['priority'],'P2')
    def test_pr_base_main_is_not_main(self):
        record=dict(BASE,run_event='pull_request',pr_numbers=[123],base_ref='main')
        self.assertEqual(job_scope(record),'not-merged')
    def test_unknown_event_does_not_assume_main(self):
        self.assertEqual(job_scope({'head_branch':'main'}),'unconfirmed')
        self.assertEqual(classify_cluster(c([{'head_branch':'main'}]))['priority'],'P3')
    def test_not_merged_requires_all_evidence(self):
        jobs=[{'run_event':'pull_request','pr_numbers':[4]}, {'run_id':14}]
        self.assertEqual(classify_cluster(c(jobs))['scope'],'unconfirmed')
    def test_main_any_verified_raises_scope(self):
        self.assertEqual(classify_cluster(c([BASE,{'run_event':'pull_request'}]))['scope'],'main')
    def test_network_flaky_not_equivalent_to_main(self):
        j={'run_event':'pull_request','failure_category':'timeout',
           'error_signature':'Read timed out: Github proxy'}
        status=classify_cluster(c([j],count=200))
        self.assertIn('infrastructure',status['labels'])
        self.assertNotIn('flaky',status['labels'])
        self.assertEqual(status['priority'],'P3')
    def test_flaky_requires_verified_testcase(self):
        self.assertNotIn('flaky',classify_cluster(c([BASE]))['labels'])
        d=c([BASE]);d['testcase_flakiness_verified']=True
        self.assertIn('flaky',classify_cluster(d)['labels'])
    def test_main_priority_over_huge_pr_only(self):
        a=c([{'run_event':'pull_request'}],count=800)
        b=c([BASE],count=1)
        self.assertIs(ordered_clusters([a,b])[0],b)
    def test_preserves_manual_labels(self):
        self.assertEqual(merged_labels([{'name':'help wanted'},{'name':'not-merged'},'npu-diffusion-cluster'],['main']),
                         ['help wanted','main','npu-diffusion-cluster'])
    def test_enrichment_from_exact_run_url(self):
        j={'run_id':123, 'pr_numbers':[]}
        cluster=c([j])
        gh=Mock()
        gh.get.return_value={'id':123,'event':'push','head_branch':'main',
             'head_repository':{'full_name':'sgl-project/sglang'},'pull_requests':[]}
        stats=enrich_run_provenance(gh,{},[cluster],max_runs=20)
        self.assertEqual(stats['runs_enriched'],1)
        self.assertEqual(cluster['incident_scope'],'main')
    def test_enrichment_missing_run_keeps_unconfirmed(self):
        j={'run_id':123}
        cluster=c([j]);gh=Mock();gh.get.side_effect=RuntimeError('HTTP 404')
        stats=enrich_run_provenance(gh,{},[cluster])
        self.assertEqual(stats['runs_unavailable'],1)
        self.assertEqual(cluster['incident_scope'],'unconfirmed')
    def test_existing_issue_fingerprint_guard(self):
        j=dict(BASE);cluster=c([j]); gh=Mock()
        gh.request.return_value={}
        issues=[{'number':7,'body':'<!-- npu-diffusion-fingerprint:abcdef123456 -->',
                 'labels':[{'name':'reviewed'}]},
                {'number':8,'body':'<!-- npu-diffusion-fingerprint:badother -->','labels':[]}]
        stats=sync_cluster_labels(gh,[cluster],'x/y',issues)
        self.assertEqual(stats['updated'],1)
        gh.issue.assert_called_once_with(7,{'labels':['main','reviewed']})
