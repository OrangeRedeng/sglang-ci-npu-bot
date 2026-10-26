import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "npu_diffusion_ci_report.py"
spec = importlib.util.spec_from_file_location("ci_report", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestCIReport(unittest.TestCase):
    def setUp(self):
        discovery=patch('scripts.collection_evidence.main_runs',return_value=([],True))
        discovery.start();self.addCleanup(discovery.stop)
    def test_job_log_redirect_does_not_forward_pat_to_blob_storage(self):
        # GitHub sends a temporary HTTPS redirect to a signed storage URL.
        # Python urllib's default redirect handling would otherwise forward PAT.
        req = mod.urllib.request.Request(
            "https://api.github.com/repos/sgl-project/sglang/actions/jobs/1/logs",
            headers={"Authorization": "Bearer test-secret", "User-Agent": "ci-bot"},
        )
        new_url = "https://logstorage.blob.core.windows.net/container/log?sig=temporary"
        redirected = mod.GitHubLogRedirectHandler().redirect_request(
            req, io.BytesIO(), 302, "Found", {}, new_url
        )
        self.assertEqual(redirected.full_url, new_url)
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertFalse(any(k.lower() == "authorization" for k, _ in redirected.header_items()))
        self.assertEqual(redirected.get_header("User-agent"), "ci-bot")

    def test_job_log_redirect_same_origin_retains_github_auth(self):
        req = mod.urllib.request.Request(
            "https://api.github.com/repos/sgl-project/sglang/actions/jobs/1/logs",
            headers={"Authorization": "Bearer test-secret"},
        )
        redirected = mod.GitHubLogRedirectHandler().redirect_request(
            req, io.BytesIO(), 302, "Found", {}, "https://api.github.com/other"
        )
        self.assertEqual(redirected.get_header("Authorization"), "Bearer test-secret")

    def test_job_log_redirect_refuses_http(self):
        req = mod.urllib.request.Request(
            "https://api.github.com/repos/sgl-project/sglang/actions/jobs/1/logs",
            headers={"Authorization": "Bearer test-secret"},
        )
        with self.assertRaisesRegex(urllib.error.HTTPError, "Refusing non-HTTPS"):
            mod.GitHubLogRedirectHandler().redirect_request(
                req, io.BytesIO(), 302, "Found", {}, "http://logstorage.invalid/file"
            )

    def test_binary_log_download_uses_safe_redirect_opener(self):
        gh = mod.GitHub("test-secret")
        with patch.object(mod, "open_github_job_log", return_value=io.BytesIO(b"sample log")) as opened:
            data = gh.request("GET", "/repos/sgl-project/sglang/actions/jobs/1/logs", binary=True)
        self.assertEqual(data, "sample log")
        sent = opened.call_args.args[0]
        self.assertEqual(sent.get_header("Authorization"), "Bearer test-secret")

    def test_report_does_not_present_missing_log_clusters_as_green(self):
        fake = {"generated_utc": "2026-10-20T10:15:00Z", "lookback_hours": 24,
                "counts": {"failure": 59}, "coverage": [], "jobs": [],
                "log_analysis_attempted": 20, "failed_jobs_without_logs": 39,
                "log_download_succeeded": 0, "log_download_failed": 20,
                "errors": ["HTTP 401"]}
        body = mod.render_report(fake, [], "not run", "disabled")
        self.assertIn("**Unclassified:**", body)
        self.assertIn("Log coverage:", body)
        self.assertIn("zero clusters is not evidence of passing tests", body)

    def test_job_selection(self):
        self.assertTrue(mod.job_matches({"name": "multimodal-gen-test-4-npu-a3 (2)"}, "pr-test-npu.yml"))
        self.assertTrue(mod.job_matches({"name": "multimodal-gen-test-1-npu-a3 (1)"}, "pr-test-npu.yml"))
        self.assertTrue(mod.job_matches({"name": "multimodal-diffusion-gen-2npu (3)"}, "diffusion-ci-gt-gen-npu.yml"))
        self.assertFalse(mod.job_matches({"name": "base-b-test-8-npu-a3"}, "pr-test-npu.yml"))

    def test_stable_fingerprints(self):
        a = 'RuntimeError: got bad ptr 0x012ad in /tmp/run-a at line 99'
        b = 'RuntimeError: got bad ptr 0x5bb33 in /tmp/run-b at line 12'
        self.assertEqual(mod.fingerprint(a), mod.fingerprint(b))

    def test_sensitive_redaction(self):
        self.assertNotIn('abcd123456789', mod.sanitize('api_key=abcd123456789'))

    def test_traceback_extraction(self):
        sig, excerpt = mod.failure_excerpt('start\nTraceback...\nRuntimeError: NPU malloc failed\nError: Process completed with exit code 1')
        self.assertIn('RuntimeError', sig)
        self.assertTrue(excerpt)

    def test_diffusion_consistency_failure_beats_pip_and_runner_errors(self):
        log = "\n".join([
            "ERROR: pip's dependency resolver conflicts with installed packages",
            "FAILED sglang/multimodal_gen/test/server/ascend/test_server_2_npu.py::TestDiffusionServerTwoNpu::test_diffusion_generation[qwen_image_t2i_2npu] - Failed",
            "[consistency]",
            "Consistency check failed for qwen_image_t2i_2npu:",
            "  Metrics: sim=0.9984, ssim=0.9371, psnr=23.6472",
            "  Thresholds: clip>=0.92, ssim>=0.95, psnr>=28.0",
            "##[error]Error: failed to run script step",
            "##[error]Process completed with exit code 1",
        ])
        sig, excerpt = mod.failure_excerpt(log)
        self.assertIn("qwen_image_t2i_2npu", sig)
        self.assertIn("Consistency check failed", sig)
        self.assertIn("ssim=0.9371", " ".join(excerpt))
        self.assertNotIn("pip", sig)

    def test_empty_clusters(self):
        self.assertEqual(mod.clusters_from_snapshot({'jobs': []}), [])

    def test_cluster_dedup(self):
        data = {'jobs': [
            {'fingerprint': 'abc', 'error_signature': 'RuntimeError: X', 'created_at': '2026-10-20T08:00:00Z'},
            {'fingerprint': 'abc', 'error_signature': 'RuntimeError: X', 'created_at': '2026-10-20T09:00:00Z'}]}
        clusters = mod.clusters_from_snapshot(data)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]['count'], 2)

    def test_no_run_failure_in_job_count(self):
        data = {'generated_utc': '2026-10-20T10:15:00Z', 'lookback_hours': 24,
                'counts': {'skipped': 2, 'success': 1}, 'coverage': [], 'jobs': [],
                'log_analysis_attempted': 0, 'failed_jobs_without_logs': 0, 'errors': []}
        report = mod.render_report(data, [], 'n/a', 'disabled')
        self.assertIn('2 skipped', report)
        self.assertNotIn('failed=2', report)

    def test_utc_moscow_conversion(self):
        t = mod.parse_timestamp('2026-10-20T10:15:00Z').astimezone(mod.ZoneInfo('Europe/Moscow'))
        self.assertEqual(t.strftime('%H:%M'), '13:15')


    def test_skip_existing_daily_before_collection(self):
        with patch.object(mod, "TARGET_REPO", "sglang-ci-npu-bot/sglang-ci-npu-bot"), \
             patch.object(mod, "daily_issue_exists", return_value="https://github.com/bot/repo/issues/1"), \
             patch.object(mod, "snapshot", side_effect=AssertionError("collector ran unexpectedly")):
            result = mod.main(["--skip-if-published-today"])
        self.assertEqual(result, 0)

    def test_no_glm_switch_in_dry_run(self):
        fake = {"generated_utc": "2026-10-20T10:15:00Z", "lookback_hours": 24,
                "counts": {}, "coverage": [], "jobs": [],
                "log_analysis_attempted": 0, "failed_jobs_without_logs": 0, "errors": []}
        with tempfile.TemporaryDirectory() as d:
            prev = os.getcwd()
            try:
                os.chdir(d)
                with patch.object(mod, "snapshot", return_value=fake), \
                     patch.object(mod, "utc_now", return_value=mod.parse_timestamp("2026-10-20T10:15:00Z")), \
                     patch.object(mod, "glm_analysis", side_effect=AssertionError("GLM called")):
                    result = mod.main(["--dry-run", "--no-glm"])
                self.assertIsNone(result)
                report = Path("reports/npu-diffusion-ci-2026-10-20.md").read_text()
                self.assertIn("disabled", report)
            finally:
                os.chdir(prev)

    def test_v4_proxy_env_ignored_for_rest_api_in_v5(self):
        fake = {"generated_utc": mod.utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lookback_hours": 24, "counts": {}, "coverage": [], "jobs": [],
                "log_analysis_attempted": 0, "failed_jobs_without_logs": 0, "errors": []}
        def collect(source_gh, *args):
            self.assertEqual(source_gh.base, "https://api.github.com")
            self.assertEqual(source_gh.public_read_proxy, "")
            self.assertEqual(source_gh.token, "FAKE_PRIVATE_TEST_TOKEN")
            return fake
        with tempfile.TemporaryDirectory() as d, \
             patch.dict(os.environ, {
                 "GITHUB_PUBLIC_READ_PROXY": "https://gh-proxy.test.osinfra.cn/",
                 "GITHUB_TOKEN": "FAKE_PRIVATE_TEST_TOKEN"}, clear=False), \
             patch.object(mod, "snapshot", side_effect=collect), \
             patch("sys.stderr", new_callable=io.StringIO) as logs:
            old = os.getcwd()
            try:
                os.chdir(d)
                mod.main(["--dry-run", "--no-glm"])
            finally:
                os.chdir(old)
            self.assertIn("deprecated and ignored", logs.getvalue())


    def test_jobs_api_envelope_actual_github_shape(self):
        # Confirmed against live GET /actions/runs/39444965393/attempts/1/jobs.
        response = {"total_count": 2, "jobs": [
            {"id": 118704927056, "name": "set-image-config", "conclusion": "success"},
            {"id": 118704927999, "name": "multimodal-gen-test-1-npu-a3", "conclusion": "skipped"},
        ]}
        gh = mod.GitHub()
        with patch.object(gh, "get", return_value=response) as m:
            got = mod.get_jobs(gh, 39444965393, 1)
        self.assertEqual(len(got), 2)
        self.assertTrue(mod.job_matches(got[1], "pr-test-npu.yml"))
        self.assertEqual(m.call_args.args[0],
                         "/repos/sgl-project/sglang/actions/runs/39444965393/attempts/1/jobs")

    def test_jobs_api_pagination(self):
        gh = mod.GitHub()
        first = {"total_count": 101, "jobs": [{"id": i} for i in range(100)]}
        second = {"total_count": 101, "jobs": [{"id": 100}]}
        with patch.object(gh, "get", side_effect=[first, second]) as m:
            jobs = mod.get_jobs(gh, 1, 2)
        self.assertEqual(len(jobs), 101)
        self.assertEqual(m.call_count, 2)

    def test_jobs_api_partial_page_is_not_silently_accepted(self):
        gh = mod.GitHub()
        with patch.object(gh, "get", return_value={"total_count": 5, "jobs": [{"id": 1}]}):
            with self.assertRaisesRegex(RuntimeError, "Incomplete jobs result"):
                mod.get_jobs(gh, 1, 1)

    def test_jobs_api_malformed_response_is_rejected(self):
        gh = mod.GitHub()
        with patch.object(gh, "get", return_value={"total_count": 1}):
            with self.assertRaisesRegex(RuntimeError, "Expected object with jobs list"):
                mod.get_jobs(gh, 1, 1)

    def test_total_job_list_collection_failure_is_not_reported_as_green(self):
        gh = mod.GitHub()
        runs = [{"id": 1, "run_attempt": 1, "created_at": "2026-10-20T12:00:00Z"}]
        with patch.object(mod, "workflow_runs", return_value=(runs, True)), \
             patch.object(mod, "get_jobs", side_effect=RuntimeError("broken schema")):
            with self.assertRaisesRegex(RuntimeError, "All 4 upstream job-list requests failed"):
                mod.snapshot(gh, mod.parse_timestamp('2026-10-20T16:00:00Z'), 24, 320, 0)

    def test_one_run_successful_while_other_job_lists_fail_marks_partial(self):
        gh = mod.GitHub()
        runs = [{"id": 1, "run_attempt": 1}, {"id": 2, "run_attempt": 1}]
        def mocked_jobs(_gh, run_id, attempt):
            if run_id == 1:
                return [{"id": 123, "name": "multimodal-gen-test-1-npu-a3",
                         "conclusion": "skipped", "status": "completed"}]
            raise RuntimeError("unavailable")
        with patch.object(mod, "workflow_runs", return_value=(runs, True)), \
             patch.object(mod, "get_jobs", side_effect=mocked_jobs):
            data = mod.snapshot(gh, mod.utc_now(), 24, 320, 0)
        self.assertEqual(data["job_lists_attempted"], 8)
        self.assertEqual(data["job_lists_loaded"], 4)
        self.assertEqual(data["counts"], {"skipped": 1})
        self.assertTrue(any("PARTIAL JOB COVERAGE" in x for x in data["coverage"]))

    def test_workflow_runs_probe_one_more_than_limit(self):
        gh = mod.GitHub()
        with patch.object(gh, "get", return_value={"workflow_runs": [{"id": i} for i in range(100)]}):
            runs, complete = mod.workflow_runs(gh, "pr-test-npu.yml", mod.utc_now(), 80)
        self.assertEqual(len(runs), 80)
        self.assertFalse(complete)

    def test_public_proxy_get_never_forwards_token(self):
        proxy = "https://gh-proxy.test.osinfra.cn/"
        github = mod.GitHub(public_read_proxy=proxy)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"full_name":"sgl-project/sglang"}')) as stub:
            result = github.get("/repos/sgl-project/sglang")
        self.assertEqual(result["full_name"], "sgl-project/sglang")
        request = stub.call_args.args[0]
        self.assertEqual(request.full_url, proxy + "https://api.github.com/repos/sgl-project/sglang")
        self.assertNotIn("authorization", {key.lower() for key, _ in request.header_items()})

    def test_public_proxy_refuses_credentials_and_writes(self):
        proxy = "https://gh-proxy.test.osinfra.cn/"
        with self.assertRaisesRegex(ValueError, "Refusing to forward"):
            mod.GitHub("SENSITIVE_TOKEN", public_read_proxy=proxy)
        github = mod.GitHub(public_read_proxy=proxy)
        with self.assertRaisesRegex(ValueError, "only permits"):
            github.request("POST", "/repos/sgl-project/sglang/issues", {"title": "test"})
        with self.assertRaisesRegex(ValueError, "only permits api.github.com"):
            github.request("GET", "https://evil.invalid/secret")

    def test_all_upstream_api_reads_failed_is_not_published_as_green(self):
        with patch.object(mod, "workflow_runs", side_effect=RuntimeError("proxy does not serve API")):
            with self.assertRaisesRegex(RuntimeError, "All source workflow API reads failed"):
                mod.snapshot(mod.GitHub(public_read_proxy="https://gh-proxy.test.osinfra.cn/"),
                             mod.utc_now(), 24, 80, 20)

    def test_public_proxy_rejects_insecure_url(self):
        with self.assertRaisesRegex(ValueError, "must be an HTTPS URL"):
            mod.GitHub(public_read_proxy="http://example.com/")

    def test_local_agent_adapter_contract(self):
        fake = {"generated_utc": "2026-10-20T10:15:00Z", "counts": {}, "coverage": []}
        # Simple adapter validates it can read the input without network or API keys.
        cmd = f"{sys.executable} -c \"import sys,json; x=json.load(sys.stdin); print('## Agent output')\""
        with patch.dict(os.environ, {"GLM_ANALYZER_COMMAND": cmd, "GLM_API_KEY": "",
                                          "GLM_API_BASE_URL": "", "GLM_MODEL": "", "GLM_BUDGET_ENABLED":"0"}):
            analysis, status = mod.glm_analysis(fake, [])
        self.assertEqual(status, "completed: local agent")
        self.assertIn("Agent output", analysis)


    def test_paid_cli_adapter_blocked_when_budget_enabled(self):
        fake = {'generated_utc':'2026-10-20T10:15:00Z','counts':{},'coverage':[]}
        with patch.dict(os.environ, {'GLM_ANALYZER_COMMAND':'echo test',
                'GLM_BUDGET_ENABLED':'1'}):
            _result, status = mod.glm_analysis(fake, [])
        self.assertEqual(status, 'invalid_configuration')

    def test_dns_failure_is_retried_then_recovers(self):
        import socket
        error = urllib.error.URLError(socket.gaierror(-2, 'Name or service not known'))
        gh = mod.GitHub()
        with patch('urllib.request.urlopen', side_effect=[error, error, io.BytesIO(b'{"ok": true}')]) as called, \
             patch('time.sleep') as slept:
            result = gh.get('/repos/sgl-project/sglang')
        self.assertEqual(result, {'ok': True})
        self.assertEqual(called.call_count, 3)
        self.assertEqual(slept.call_count, 2)

    def test_persistent_dns_failure_raises_transport_error(self):
        import socket
        error = urllib.error.URLError(socket.gaierror(-2, 'Name or service not known'))
        gh = mod.GitHub()
        with patch('urllib.request.urlopen', side_effect=[error] * 4) as called, \
             patch('time.sleep'):
            with self.assertRaisesRegex(mod.GitHubTransportError, 'network failure after 4 attempts'):
                gh.get('/repos/sgl-project/sglang')
        self.assertEqual(called.call_count, 4)

    def test_dns_network_outage_aborts_instead_of_falsely_publishing(self):
        gh = mod.GitHub()
        runs = [{'id': i, 'run_attempt': 1} for i in range(6)]
        with patch.object(mod, 'workflow_runs', return_value=(runs, True)), \
             patch.object(mod, 'get_jobs', side_effect=mod.GitHubTransportError('dns unavailable')):
            with self.assertRaisesRegex(RuntimeError, '3 consecutive GitHub API network failures'):
                mod.snapshot(gh, mod.utc_now(), 24, 320, 0)



if __name__ == '__main__':
    unittest.main()
