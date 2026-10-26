"""Fully offline preflight regression tests; no external API requests."""
import importlib.util
import io
import os
import socket
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

P = Path(__file__).resolve().parent.parent / 'scripts' / 'npu_network_check.py'
spec = importlib.util.spec_from_file_location('npu_network_check', P)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestPreflight(unittest.TestCase):
    def test_dns_deduplicates_addresses(self):
        replies = [(socket.AF_INET, 1, 6, '', ('20.205.243.168', 443)),
                   (socket.AF_INET, 1, 6, '', ('20.205.243.168', 443))]
        with patch.object(mod.socket, 'getaddrinfo', return_value=replies):
            self.assertEqual(mod.check_dns(), ['20.205.243.168'])

    def test_dns_error_returns_failure_not_fake_success(self):
        with patch.object(mod.socket, 'getaddrinfo', side_effect=socket.gaierror(-2, 'No name')):
            self.assertEqual(mod.main(['--dns-only']), 1)

    def test_repo_api_fails_closed_if_unexpected_repo(self):
        with patch.object(mod, 'check_dns', return_value=['20.205.243.168']), \
             patch.object(mod, 'fetch_json', return_value={'full_name': 'evil/other'}):
            self.assertEqual(mod.main([]), 1)

    def test_full_schema_preflight(self):
        replies = [{'full_name': 'sgl-project/sglang'},
                   {'workflow_runs': [{'id': 39444965393, 'run_attempt': 1}]},
                   {'total_count': 1, 'jobs': [{'name': 'multimodal-gen-test-1-npu-a3'}]}]
        with patch.object(mod, 'check_dns', return_value=['20.205.243.168']), \
             patch.object(mod, 'fetch_json', side_effect=replies) as mock:
            self.assertEqual(mod.verify(sample_jobs=True), 0)
            self.assertIn('/attempts/1/jobs', mock.call_args.args[0])

    def test_refuses_malformed_jobs(self):
        replies = [{'full_name': 'sgl-project/sglang'},
                   {'workflow_runs': [{'id': 1}]},
                   {'total_count': 2, 'jobs': None}]
        with patch.object(mod, 'check_dns', return_value=['20.205.243.168']), \
             patch.object(mod, 'fetch_json', side_effect=replies):
            self.assertEqual(mod.main(['--sample-jobs']), 1)

    def test_http_network_recovery(self):
        def error(): return urllib.error.URLError(socket.gaierror(-2, 'unavailable'))
        with patch.object(mod.urllib.request, 'urlopen', side_effect=[error(), io.BytesIO(b'{"full_name":"sgl-project/sglang"}')]) as opened, \
             patch.object(mod.time, 'sleep') as wait:
            self.assertEqual(mod.fetch_json('/repos/sgl-project/sglang', attempts=2)['full_name'], 'sgl-project/sglang')
            self.assertEqual(opened.call_count, 2)
            wait.assert_called_once()

    def test_http_fails_after_retry_budget(self):
        e = urllib.error.URLError(socket.gaierror(-2, 'no DNS'))
        with patch.object(mod.urllib.request, 'urlopen', side_effect=[e, e]), \
             patch.object(mod.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'transport failed'):
                mod.fetch_json('/repos/sgl-project/sglang', attempts=2)

    def test_http_error_classification(self):
        e = urllib.error.HTTPError('https://api.github.com', 403, 'Forbidden', {}, None)
        with patch.object(mod.urllib.request, 'urlopen', side_effect=e):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
                mod.fetch_json('/repos/sgl-project/sglang', attempts=2)

    def test_requests_only_github_api(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'my_secret'}, clear=False), \
             patch.object(mod.urllib.request, 'urlopen', return_value=io.BytesIO(b'{}')) as req:
            mod.fetch_json('/repos/sgl-project/sglang')
            obj = req.call_args.args[0]
            self.assertEqual(obj.full_url, 'https://api.github.com/repos/sgl-project/sglang')
            self.assertEqual(obj.get_header('Authorization'), 'Bearer my_secret')


if __name__ == '__main__':
    unittest.main()
