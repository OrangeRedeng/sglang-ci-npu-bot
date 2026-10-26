import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import log_context as log
from scripts import npu_diffusion_ci_report as report

FIXTURE = ROOT / 'tests/fixtures/ascend_diffusion_consistency.log'

class TestLogReducer(unittest.TestCase):
    def test_real_world_qwen_quality_failure(self):
        result = log.analyze_file(FIXTURE)
        primary = result['primary']
        self.assertEqual(primary['category'], 'diffusion_consistency')
        self.assertEqual(primary['testcase'], 'qwen_image_t2i_2npu')
        self.assertAlmostEqual(primary['metrics']['ssim'], 0.9371)
        self.assertAlmostEqual(primary['metrics']['psnr'], 23.6472)
        self.assertEqual(primary['thresholds']['ssim'], {'op':'>=','value':0.95})
        self.assertEqual(primary['thresholds']['psnr'], {'op':'>=','value':28.0})
        self.assertIn('ssim', primary['failed_checks'])
        self.assertIn('psnr', primary['failed_checks'])
        self.assertTrue('Consistency check failed' in result['focused_text'])
        self.assertFalse('pip' in primary['signature'].lower())

    def test_minimax_video_aggregate_metrics_not_overwritten_by_last_frame(self):
        # Real failure shape (Minimax-H3 job 118603736162): f0 CLIP fails,
        # but f2 CLIP passes. The previous reducer reported f2 as job summary.
        lines = [
            'Consistency check failed for minimax_h3_t2va_2npu:',
            '  Metrics: sim=0.8883, ssim=0.7753, psnr=24.5820, mean_abs_diff=8.2427',
            '  Thresholds: clip>=0.9, ssim>=0.92, psnr>=24.0, mean_abs_diff<=10.0',
            '  Failed frames:',
            '  - f0 [clip, ssim] clip=0.8883 ssim=0.7806 psnr=24.7899 mean_abs_diff=7.6420',
            '  - f1 [ssim] clip=0.9209 ssim=0.7753 psnr=24.5820 mean_abs_diff=8.2427',
            '  - f2 [ssim] clip=0.9122 ssim=0.8221 psnr=25.5894 mean_abs_diff=7.0864',
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'minimax.log'
            path.write_text('\n'.join(lines), encoding='utf-8')
            primary = log.analyze_file(path)['primary']
        self.assertEqual(primary['testcase'], 'minimax_h3_t2va_2npu')
        self.assertEqual(primary['failed_checks'], ['clip', 'ssim'])
        self.assertAlmostEqual(primary['metrics']['sim'], 0.8883)
        self.assertAlmostEqual(primary['metrics']['ssim'], 0.7753)
        self.assertAlmostEqual(primary['metrics']['psnr'], 24.582)
        self.assertNotIn('clip', primary['metrics'])  # summary field is sim
        self.assertEqual(len(primary['failed_frames']), 3)
        self.assertAlmostEqual(primary['failed_frames'][0]['metrics']['clip'], 0.8883)
        self.assertAlmostEqual(primary['failed_frames'][2]['metrics']['clip'], 0.9122)
        self.assertIn('clip', primary['failed_frames'][0]['failed_checks'])
        self.assertNotIn('clip', primary['failed_frames'][2]['failed_checks'])

    def test_repair_v11_snapshot_aggregate_metrics_without_raw_log(self):
        old = {
            'failure_category':'diffusion_consistency',
            'primary_test_case':'minimax_h3_t2va_2npu',
            'metrics':{'clip':0.9122,'ssim':0.8221,'psnr':25.5894},
            'failed_checks':['clip','ssim'],
            'expanded_evidence':('--- signature 123 near line 55 ---\n'
              '56: Consistency check failed for minimax_h3_t2va_2npu:\n'
              '57: Metrics: sim=0.8883, ssim=0.7753, psnr=24.5820, mean_abs_diff=8.2427\n'
              '58: Thresholds: clip>=0.9, ssim>=0.92, psnr>=24.0, mean_abs_diff<=10.0\n'
              '59: Failed frames:\n'
              '60: - f0 [clip, ssim] clip=0.8883 ssim=0.7806 psnr=24.7899 mean_abs_diff=7.6420\n'
              '61: - f1 [ssim] clip=0.9209 ssim=0.7753 psnr=24.5820 mean_abs_diff=8.2427\n'
              '62: - f2 [ssim] clip=0.9122 ssim=0.8221 psnr=25.5894 mean_abs_diff=7.0864\n')
        }
        self.assertTrue(log.repair_consistency_job_from_saved_evidence(old))
        self.assertAlmostEqual(old['metrics']['sim'], 0.8883)
        self.assertAlmostEqual(old['metrics']['ssim'], 0.7753)
        self.assertEqual(len(old['failed_frames']), 3)
        self.assertEqual(old['failed_checks'], ['clip','ssim'])
        self.assertTrue(old['saved_evidence_reconciled'])

    def test_metric_values_do_not_change_signature(self):
        a=log.stable_key('diffusion_consistency','Consistency check failed for qwen_image_t2i_2npu: 0.91','qwen_image_t2i_2npu')
        b=log.stable_key('diffusion_consistency','Consistency check failed for qwen_image_t2i_2npu: 0.94','qwen_image_t2i_2npu')
        self.assertEqual(a,b)
        self.assertNotEqual(a,log.stable_key('diffusion_consistency','Consistency check failed for wan2_2:', 'wan2_2'))

    def test_ascend_runtime_and_oom(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'run.log'
            path.write_text('rank=0 HCCL timeout connection error\nrank=4 HCCL timeout connection error\nRuntimeError: NPU malloc failed: out of memory\n')
            results=log.analyze_file(path)
        self.assertEqual(results['primary']['category'],'npu_oom')
        self.assertIn('ascend_runtime', results['categories'])
        self.assertEqual(log.normalize('rank=0 HCCL timeout connection error'),log.normalize('rank=4 HCCL timeout connection error'))

    def test_critical_error_survives_rare_event_after_noisy_unique_errors(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log.txt'
            path.write_text(''.join('some generic error '+str(x)+' variations\n' for x in range(100)) +
                            'Consistency check failed for qwen_image_t2i_2npu:\n  Metrics: ssim=0.91\n')
            result=log.analyze_file(path,max_events=2)
        self.assertEqual(result['primary']['category'], 'diffusion_consistency')
        self.assertLessEqual(result['unique_signatures'],2)

    def test_second_stage_expands_selected_signature(self):
        primary=log.analyze_file(FIXTURE)['primary']
        detail=log.expand_file(FIXTURE,primary['id'],context=14)
        self.assertEqual(detail['match_count'],1)
        self.assertIn('Thresholds:',detail['text'])
        self.assertIn('qwen_image_t2i_2npu',detail['text'])
        self.assertEqual(detail['context'],14)
        with self.assertRaises(ValueError):
            log.expand_file(FIXTURE,'0000000000')

    def test_redaction_url_query_and_tokens(self):
        s=('Authorization: Bearer topsecret987654321\n'
           'https://example.com/logs?a=1&sig=dangeroussecret&x=3\n'
           'password=letmein12345\n')
        redacted=log.redact(s)
        for bad in ('topsecret987654321','dangeroussecret','letmein12345'):
            self.assertNotIn(bad,redacted)
        self.assertIn('[REDACTED]',redacted)

    def test_cap_focused_output_and_no_unbounded_events(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'large.log'
            with p.open('w') as f:
                for i in range(11000):
                    f.write('Error: generic failure sequence '+str(i)+'\n')
                f.write('Consistency check failed for qwen_image_t2i_2npu:\n  Metrics: ssim=0.93\n')
            v=log.analyze_file(p,max_bytes=32768,max_lines=400)
        self.assertLessEqual(len(v['focused_text'].encode()),32768)
        self.assertLessEqual(len(v['focused_text'].splitlines()),400)
        self.assertLessEqual(v['unique_signatures'],256)
        self.assertEqual(v['primary']['category'],'diffusion_consistency')
        self.assertEqual(v['total_lines'],11002)

    def test_stream_log_to_disk_no_tail_loss(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'raw.log'
            gh=report.GitHub(token='SENSITIVE')
            req_data=io.BytesIO(b'FIRST\n'+b'X'*100+b'\nLAST\n')
            with patch.object(report, 'open_github_job_log', return_value=req_data):
                result=gh.request('GET','/repos/sgl-project/sglang/actions/jobs/1/logs',stream_to=file)
            self.assertEqual(result['downloaded_bytes'],112)
            self.assertFalse(result['truncated'])
            self.assertTrue(file.read_bytes().startswith(b'FIRST'))
            self.assertTrue(file.read_bytes().endswith(b'LAST\n'))
            self.assertEqual(file.stat().st_mode & 0o777,0o600)

    def test_stream_truncation_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'raw.log'
            with patch.object(report,'MAX_STREAM_LOG_BYTES',10), \
                 patch.object(report,'open_github_job_log',return_value=io.BytesIO(b'1234567890EXTRA')):
                result=report.GitHub().request('GET','/tmp/logs',stream_to=file)
            self.assertEqual(file.read_bytes(),b'1234567890')
            self.assertTrue(result['truncated'])

    def test_snapshot_integrates_reducer(self):
        gh=report.GitHub()
        jobs=[{'id':123,'name':'multimodal-gen-test-1-npu-a3','conclusion':'failure','status':'completed'}]
        runs=[{'id':10,'created_at':'2026-10-20T12:00:00Z','head_sha':'abc','run_attempt':1}]
        def fake_request(method,path,payload=None,**kwargs):
            dest=kwargs['stream_to']; dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(FIXTURE.read_bytes())
            return {'downloaded_bytes':dest.stat().st_size,'truncated':False}
        with tempfile.TemporaryDirectory() as d, \
             patch.object(report,'workflow_runs',return_value=(runs,True)), \
             patch.object(report,'get_jobs',return_value=jobs), \
             patch.object(gh,'request',side_effect=fake_request):
            old=os.getcwd()
            try:
                os.chdir(d)
                data=report.snapshot(gh,report.parse_timestamp('2026-10-20T16:00:00Z'),24,10,1)
            finally:
                os.chdir(old)
        rec=data['jobs'][0]
        self.assertEqual(data['log_download_succeeded'],1)
        self.assertEqual(rec['failure_category'],'diffusion_consistency')
        self.assertIn('Thresholds:',rec['expanded_evidence'])
        self.assertAlmostEqual(rec['metrics']['ssim'],0.9371)
        self.assertEqual(len(report.clusters_from_snapshot(data)),1)
        self.assertNotIn('FIRST RAW',json.dumps(data))

if __name__=='__main__':unittest.main()
