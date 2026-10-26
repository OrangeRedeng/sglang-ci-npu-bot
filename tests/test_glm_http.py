"""Offline GLM-5.3 reasoning-budget/transport regression tests."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('glm_http', ROOT / 'scripts/glm_http.py')
glm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(glm)


class FakeResponse:
    def __init__(self, obj):
        self.body = json.dumps(obj).encode('utf-8')
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self.body


def api_result(content='', reasoning='', finish='length', completion=3000):
    return {'choices':[{'finish_reason':finish,'message':{
        'role':'assistant','content':content,'reasoning_content':reasoning}}],
        'usage':{'prompt_tokens':123,'completion_tokens':completion,
                 'completion_tokens_details':{'reasoning_tokens':max(0, completion-50)}}}


class TestGLMHTTP(unittest.TestCase):
    def setUp(self):
        b = patch.dict(os.environ, {'GLM_BUDGET_ENABLED':'0'})
        b.start()
        self.addCleanup(b.stop)

    def test_parse_only_visible_content_never_reasoning(self):
        answer, meta = glm.parse_completion(api_result('', 'private reasoning', 'length'))
        self.assertEqual(answer, '')
        self.assertEqual(meta['finish_reason'], 'length')
        self.assertEqual(meta['reasoning_characters'], len('private reasoning'))
        self.assertNotIn('private reasoning', str(meta))

    def test_reasoning_budget_exhaustion_retried_with_low_and_higher_limit(self):
        payloads, diagnostics = [], []
        replies = [api_result('', 'private thought', 'length', completion=6000),
                   api_result('## Root-cause hypotheses\nUnverified.', '', 'stop', completion=1300)]
        def serve(req, timeout):
            payloads.append(json.loads(req.data))
            return FakeResponse(replies.pop(0))
        with patch.dict(os.environ, {'GLM_MAX_OUTPUT_TOKENS':'8192',
                  'GLM_EMPTY_RETRIES':'1','GLM_REASONING_EFFORT':'high'}, clear=False), \
             patch.object(glm.urllib.request, 'urlopen', side_effect=serve):
            result = glm.chat_completion('https://open.bigmodel.cn/api/paas/v4',
                    'secret-test', 'glm-5.3-flash', [{'role':'user','content':'Evidence'}],
                    max_tokens=6000, on_diagnostic=diagnostics.append)
        self.assertIn('Root-cause', result)
        self.assertEqual(len(payloads), 2)
        self.assertEqual([x['max_tokens'] for x in payloads], [6000, 8192])
        self.assertEqual([x['reasoning_effort'] for x in payloads], ['high', 'low'])
        self.assertEqual(payloads[0]['thinking'], {'type':'enabled'})
        self.assertEqual(payloads[1]['thinking'], {'type':'enabled'})
        self.assertEqual(len(diagnostics), 2)
        self.assertIn('finish_reason=length', diagnostics[0])
        self.assertNotIn('private thought', repr(diagnostics))
        self.assertNotIn('secret-test', repr(diagnostics))

    def test_glm_53_default_high_without_env(self):
        seen = []
        def serve(req, timeout):
            seen.append(json.loads(req.data));return FakeResponse(api_result('PASS', '', 'stop'))
        with patch.dict(os.environ, {'GLM_REASONING_EFFORT':'','GLM_EMPTY_RETRIES':'0'},clear=False), \
             patch.object(glm.urllib.request, 'urlopen', side_effect=serve):
            self.assertEqual('PASS',glm.chat_completion('https://api.z.ai/api/paas/v4','secret',
                'glm-5.3-flash', [{'role':'user','content':'hi'}], max_tokens=2000))
        self.assertEqual(seen[0]['reasoning_effort'],'high')

    def test_high_reasoning_default_and_safe_global_budget(self):
        seen = []
        def serve(req, timeout):
            seen.append((json.loads(req.data), timeout))
            return FakeResponse(api_result('## Verified signal\nEvidence only', '', 'stop'))
        with patch.dict(os.environ, {'GLM_REASONING_EFFORT':'',
            'GLM_MAX_OUTPUT_TOKENS':'', 'GLM_API_TIMEOUT_SECONDS':''}, clear=False):
            # Remove optional environment overrides to test the real defaults.
            for key in ('GLM_MAX_OUTPUT_TOKENS', 'GLM_API_TIMEOUT_SECONDS'):
                os.environ.pop(key, None)
            with patch.object(glm.urllib.request, 'urlopen', side_effect=serve):
                glm.chat_completion('https://open.bigmodel.cn/api/paas/v4','secret',
                    'glm-5.3-flash',[{'role':'user','content':'Evidence'}],max_tokens=20000)
        self.assertEqual(seen[0][0]['reasoning_effort'], 'high')
        self.assertEqual(seen[0][0]['max_tokens'], 20000)
        self.assertEqual(seen[0][1], 420)

    def test_max_reasoning_and_32768_cap_override(self):
        seen=[]
        def serve(req, timeout):
            seen.append(json.loads(req.data))
            return FakeResponse(api_result('OK','','stop'))
        with patch.dict(os.environ, {'GLM_REASONING_EFFORT':'max',
            'GLM_MAX_OUTPUT_TOKENS':'32768','GLM_EMPTY_RETRIES':'0'}, clear=False), \
             patch.object(glm.urllib.request, 'urlopen', side_effect=serve):
            glm.chat_completion('https://open.bigmodel.cn/api/paas/v4','secret',
                'glm-5.3-flash',[],max_tokens=30000)
        self.assertEqual(seen[0]['reasoning_effort'], 'max')
        self.assertEqual(seen[0]['max_tokens'], 30000)

    def test_reasoning_only_length_reports_metadata_not_reasoning(self):
        calls = []
        def serve(req, timeout):
            calls.append(req)
            return FakeResponse(api_result('', 'DO NOT LEAK REASONING', 'length', 8192))
        with patch.dict(os.environ, {'GLM_EMPTY_RETRIES':'1',
                  'GLM_MAX_OUTPUT_TOKENS':'8192','GLM_REASONING_EFFORT':'low'},clear=False), \
             patch.object(glm.urllib.request, 'urlopen', side_effect=serve):
            with self.assertRaises(glm.GLMEmptyResponseError) as e:
                glm.chat_completion('https://open.bigmodel.cn/api/paas/v4','secret',
                        'glm-5.3-flash',[{'role':'user','content':'Evidence'}],max_tokens=6000)
        self.assertEqual(len(calls),2)
        self.assertIn('finish_reason=length',str(e.exception))
        self.assertIn('completion_tokens=8192',str(e.exception))
        self.assertNotIn('DO NOT LEAK',str(e.exception))

    def test_no_retry_for_empty_stop(self):
        calls = []
        def serve(req, timeout):
            calls.append(req);return FakeResponse(api_result('', '', 'stop', 10))
        with patch.dict(os.environ, {'GLM_EMPTY_RETRIES':'1'},clear=False), \
             patch.object(glm.urllib.request,'urlopen',side_effect=serve):
            with self.assertRaises(glm.GLMEmptyResponseError):
                glm.chat_completion('https://open.bigmodel.cn/api/paas/v4','secret',
                        'glm-5.3-flash',[],max_tokens=3000)
        self.assertEqual(len(calls),1)

    def test_no_reasoning_control_on_unrelated_model(self):
        seen=[]
        def serve(req,timeout):
            seen.append(json.loads(req.data));return FakeResponse(api_result('ok','', 'stop'))
        with patch.dict(os.environ, {'GLM_REASONING_EFFORT':'','GLM_EMPTY_RETRIES':'1'},clear=False), \
             patch.object(glm.urllib.request,'urlopen',side_effect=serve):
            glm.chat_completion('https://example.com/v4','secret','glm-4.6',[],max_tokens=4000)
        self.assertNotIn('reasoning_effort',seen[0])
        self.assertNotIn('thinking',seen[0])

    def test_list_content_supported_and_format_error_safe(self):
        result = api_result(None, '', 'stop')
        result['choices'][0]['message']['content']=[{'type':'text','text':'hi'},
                  {'type':'tool_use','id':'private'}, {'type':'text','text':'world'}]
        self.assertEqual(glm.parse_completion(result)[0], 'hi\nworld')
        with self.assertRaises(glm.GLMResponseFormatError):
            glm.parse_completion({'choices':[]})

    def test_invalid_effort_rejected_before_network(self):
        with patch.dict(os.environ, {'GLM_REASONING_EFFORT':'off'},clear=False):
            with self.assertRaisesRegex(ValueError, 'GLM_REASONING_EFFORT'):
                glm.chat_completion('https://example.com','secret','glm-5.3-flash',[],max_tokens=4000)

if __name__ == '__main__':
    unittest.main()
