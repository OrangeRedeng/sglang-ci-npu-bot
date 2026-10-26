"""Regression tests: two failed parametrized testcases can share one GitHub job."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from scripts import log_context as log
from scripts import npu_diffusion_ci_report as ci

QWEN='''Consistency check failed for qwen_image_t2i_2npu:
  Metrics: sim=0.9984, ssim=0.9371, psnr=23.6472, mean_abs_diff=3.7006
  Thresholds: clip>=0.92, ssim>=0.95, psnr>=28.0, mean_abs_diff<=8.0
  Failed frames:
  - f0 [ssim, psnr] clip=0.9984 ssim=0.9371 psnr=23.6472 mean_abs_diff=3.7006
'''
MINIMAX='''Consistency check failed for minimax_h3_t2va_2npu:
  Metrics: sim=0.8883, ssim=0.7753, psnr=24.5820, mean_abs_diff=8.2427
  Thresholds: clip>=0.9, ssim>=0.92, psnr>=24.0, mean_abs_diff<=10.0
  Failed frames:
  - f0 [clip, ssim] clip=0.8883 ssim=0.7806 psnr=24.7899 mean_abs_diff=7.6420
  - f1 [ssim] clip=0.9209 ssim=0.7753 psnr=24.5820 mean_abs_diff=8.2427
  - f2 [ssim] clip=0.9122 ssim=0.8221 psnr=25.5894 mean_abs_diff=7.0864
'''

def make_log(logtext):
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp)/'job.log'
        p.write_text(logtext)
        analysis=log.analyze_file(p)
    return analysis

def make_job(jid, analysis):
    p=analysis['primary']
    return {'job_id': jid,'run_id': jid+100,'name':'multimodal-gen-test-4-npu-a3 (0)',
            'url': 'https://github.com/sgl-project/sglang/actions/runs/%d/job/%d'%(jid+100,jid),
            'workflow':'pr-test-npu.yml','head_sha':'a'*40,
            'created_at':'2026-10-20T12:00:00Z','conclusion':'failure',
            'fingerprint':p['id'],'failure_category':p['category'],
            'error_signature':p['signature'],'primary_test_case':p.get('testcase'),
            'metrics':p.get('metrics',{}),'thresholds':p.get('thresholds',{}),
            'failed_checks':p.get('failed_checks',[]),'failed_frames':p.get('failed_frames',[]),
            'log_analysis':{'events':analysis['events']},
            'failure_cases':log.failure_cases_from_events(analysis['events'],p)}

class TestMultiCaseClusters(unittest.TestCase):
    def test_both_testcases_extracted_and_metrics_are_isolated(self):
        a=make_log(QWEN+'noise\n'+MINIMAX)
        cases=log.failure_cases_from_events(a['events'],a['primary'])
        self.assertEqual(len(cases),2)
        by_test={x['testcase']:x for x in cases}
        self.assertEqual(set(by_test),{'qwen_image_t2i_2npu','minimax_h3_t2va_2npu'})
        q=by_test['qwen_image_t2i_2npu']
        m=by_test['minimax_h3_t2va_2npu']
        self.assertAlmostEqual(q['metrics']['ssim'],.9371)
        self.assertAlmostEqual(q['metrics']['psnr'],23.6472)
        self.assertAlmostEqual(m['metrics']['ssim'],.7753)
        self.assertAlmostEqual(m['failed_frames'][0]['metrics']['clip'],.8883)
        self.assertEqual(m['failed_checks'],['clip','ssim'])
        self.assertEqual(q['failed_checks'],['ssim','psnr'])

    def test_fifty_jobs_52_failed_testcase_observations(self):
        q=make_log(QWEN)
        both=make_log(QWEN+MINIMAX)
        jobs=[make_job(i,q) for i in range(1000,1048)]
        jobs.extend(make_job(i,both) for i in range(1048,1050))
        data={'generated_utc':'2026-10-20T17:14:00Z','lookback_hours':24,
              'jobs':jobs,'counts':{'failure':50},'coverage':[],'errors':[],
              'log_analysis_attempted':50,'failed_jobs_without_logs':0}
        clusters=ci.clusters_from_snapshot(data)
        self.assertEqual(len(clusters),2)
        by_name={c['jobs'][0]['primary_test_case']:c for c in clusters}
        self.assertEqual(by_name['qwen_image_t2i_2npu']['count'],50)
        self.assertEqual(by_name['minimax_h3_t2va_2npu']['count'],2)
        self.assertEqual({j['job_id'] for j in by_name['minimax_h3_t2va_2npu']['jobs']}, {1048,1049})
        self.assertEqual({j['job_id'] for j in by_name['qwen_image_t2i_2npu']['jobs']}&
                         {j['job_id'] for j in by_name['minimax_h3_t2va_2npu']['jobs']},{1048,1049})
        for c in clusters:
            c['stable_id']='PREVIEW-'+c['fingerprint'][:6]
        report=ci.render_report(data,clusters,'Analysis','completed: triage')
        self.assertIn('52 failing testcase observations** across 50 classified jobs',report)
        self.assertIn('50 classified jobs',report)
        self.assertIn('2 failure observations',report)
        self.assertEqual(data['counts']['failure'],50)

    def test_saved_v12_snapshot_recovers_secondary_failure_without_raw_logs(self):
        both=make_log(QWEN+MINIMAX)
        job=make_job(1020,both)
        job.pop('failure_cases')  # historical v12 snapshot
        # Simulate the old reducer writing the LAST frame's values into one event.
        for e in job['log_analysis']['events']:
            if e.get('testcase')=='minimax_h3_t2va_2npu':
                e['metrics']={'clip':.9122,'ssim':.8221,'psnr':25.5894}
                e['failed_frames']=[]
        data={'jobs':[job]}
        clusters=ci.clusters_from_snapshot(data)
        self.assertEqual(len(clusters),2)
        m=next(c for c in clusters if c['jobs'][0]['primary_test_case']=='minimax_h3_t2va_2npu')
        self.assertAlmostEqual(m['jobs'][0]['metrics']['ssim'],.7753)
        self.assertAlmostEqual(m['jobs'][0]['metrics']['sim'],.8883)
        self.assertNotIn('clip',m['jobs'][0]['metrics'])
        self.assertAlmostEqual(m['jobs'][0]['failed_frames'][0]['metrics']['clip'],.8883)
        self.assertNotIn('qwen_image_t2i_2npu',m['jobs'][0]['expanded_evidence'])

    def test_skipped_job_is_not_a_failure_even_if_stale_fingerprint(self):
        q=make_log(QWEN)
        job=make_job(1021,q); job['conclusion']='skipped'
        self.assertEqual(ci.clusters_from_snapshot({'jobs':[job]}),[])

if __name__=='__main__':
    unittest.main()
