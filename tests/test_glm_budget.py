"""Offline tests for local budget enforcement and GLM transport accounting."""
from pathlib import Path
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch
from scripts import glm_budget, glm_http


class Response:
    def __init__(self, obj):
        self._value = json.dumps(obj).encode('utf-8')
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self._value


def reply(content='ok', prompt=1200, completion=1500):
    return {'choices':[{'finish_reason':'stop','message':{'content':content}}],
      'usage':{'prompt_tokens':prompt,'completion_tokens':completion,
               'completion_tokens_details':{'reasoning_tokens':1000}}}


class TestBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {'GLM_BUDGET_ENABLED':'1', 'GLM_BUDGET_LEDGER':str(Path(self.tmp.name)/'private'/'budget.sqlite3'),
            'GLM_DAILY_BUDGET_CNY':'1.00','GLM_PER_REQUEST_BUDGET_CNY':'0.50',
            'GLM_MAX_DAILY_API_REQUESTS':'4','GLM_INPUT_CNY_PER_M':'0.80',
            'GLM_OUTPUT_CNY_PER_M':'2.80','GLM_EMPTY_RETRIES':'0',
            'GLM_MAX_OUTPUT_TOKENS':'8000','GLM_REASONING_EFFORT':'low',
            'GLM_API_TIMEOUT_SECONDS':'420'}
        p=patch.dict(os.environ,env)
        p.start();self.addCleanup(p.stop)
        self.messages=[{'role':'user','content':'Evidence '*200}]

    def run_request(self, serve=None):
        with patch.object(glm_http.urllib.request, 'urlopen', side_effect=serve or (lambda *_a,**_k: Response(reply()))):
            return glm_http.chat_completion('https://example.com/v4','SECRET','glm-5.3-flash',self.messages,max_tokens=6000)

    def test_success_records_usage_with_reasoning_in_output(self):
        self.assertEqual(self.run_request(),'ok')
        s=glm_budget.summary()
        self.assertEqual(s['requests'],1)
        self.assertEqual(s['reported_requests'],1)
        self.assertEqual(s['completion_tokens_reported'],1500)
        self.assertEqual(s['reasoning_tokens_reported'],1000)
        self.assertEqual(s['reported_cny_estimate'],'0.005160')
        self.assertEqual(s['uncertain_reserved_cny'],'0')
        path=glm_budget.ledger_path()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode),0o700)
        self.assertNotIn('SECRET',path.read_bytes().decode('latin-1', errors='ignore'))

    def test_quota_rejects_before_network(self):
        with patch.dict(os.environ, {'GLM_MAX_DAILY_API_REQUESTS':'1'}):
            self.run_request()
            calls=[]
            with patch.object(glm_http.urllib.request,'urlopen',side_effect=lambda *a,**k:calls.append(1)):
                with self.assertRaises(glm_budget.GLMBudgetExceeded):
                    glm_http.chat_completion('https://example.com/v4','SECRET','glm-5.3-flash',self.messages,max_tokens=6000)
            self.assertEqual(calls,[])
        self.assertEqual(glm_budget.summary()['requests'],1)

    def test_timeout_holds_reservation_and_consumes_request_slot(self):
        with patch.dict(os.environ, {'GLM_MAX_DAILY_API_REQUESTS':'1'}):
            def down(*args,**kwargs): raise TimeoutError('timeout')
            with self.assertRaises(TimeoutError):self.run_request(down)
            s=glm_budget.summary()
            self.assertEqual(s['requests'],1)
            self.assertEqual(s['unknown_or_inflight_requests'],1)
            self.assertGreater(float(s['uncertain_reserved_cny']),0)
            with self.assertRaises(glm_budget.GLMBudgetExceeded):self.run_request()

    def test_cost_limit_rejects_large_prompt_before_network(self):
        with patch.dict(os.environ,{'GLM_PER_REQUEST_BUDGET_CNY':'0.000001'}):
            with self.assertRaises(glm_budget.GLMBudgetExceeded):self.run_request()
        self.assertEqual(glm_budget.summary()['requests'],0)

    def test_rejection_reports_exact_local_limit(self):
        with patch.dict(os.environ,{'GLM_PER_REQUEST_BUDGET_CNY':'0.000001'}):
            with self.assertRaisesRegex(glm_budget.GLMBudgetExceeded,'per-request estimated reservation CNY') as err:
                self.run_request()
        self.assertIn('request NOT sent',str(err.exception))

    def test_daily_count_rejection_explains_cap(self):
        with patch.dict(os.environ,{'GLM_MAX_DAILY_API_REQUESTS':'1'}):
            self.run_request()
            with self.assertRaisesRegex(glm_budget.GLMBudgetExceeded,'1/1 requests'):
                self.run_request()

    def test_daily_amount_rejection_explains_cap(self):
        with patch.dict(os.environ,{'GLM_DAILY_BUDGET_CNY':'0.000001'}):
            with self.assertRaisesRegex(glm_budget.GLMBudgetExceeded,'daily estimated budget exceeded'):
                self.run_request()

    def test_no_usage_keeps_reservation(self):
        obj=reply();obj.pop('usage')
        self.run_request(lambda *a,**k:Response(obj))
        s=glm_budget.summary()
        self.assertEqual(s['unknown_or_inflight_requests'],1)
        self.assertGreater(float(s['uncertain_reserved_cny']),0)

    def test_rejected_price_and_budget_configuration(self):
        for name,val in [('GLM_DAILY_BUDGET_CNY','NaN'),('GLM_INPUT_CNY_PER_M','-1'),('GLM_MAX_DAILY_API_REQUESTS','abc')]:
            with self.subTest(name=name),patch.dict(os.environ,{name:val}):
                with self.assertRaises(ValueError):glm_budget.reserve('glm',self.messages,500)

    def test_atomic_allocation_enforces_concurrent_request_limit(self):
        import concurrent.futures
        with patch.dict(os.environ,{'GLM_MAX_DAILY_API_REQUESTS':'2'}):
            def run(_):
                try:return glm_budget.reserve('glm',self.messages,500)
                except glm_budget.GLMBudgetExceeded:return None
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(run,range(8)))
        self.assertEqual(sum(x is not None for x in results),2)
        self.assertEqual(glm_budget.summary()['requests'],2)


if __name__=='__main__':unittest.main()
