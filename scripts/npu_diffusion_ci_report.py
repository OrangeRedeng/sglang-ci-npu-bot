#!/usr/bin/env python3
"""Publish evidence-first Ascend diffusion CI snapshots to GitHub Issues.

No third-party dependencies. Never runs SGLang tests, changes upstream CI,
posts on upstream repositories, or auto-closes an unresolved failure cluster.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
try:
    from scripts.log_context import analyze_file, redact, expand_file, failure_cases_from_events
except ModuleNotFoundError:
    # Direct systemd invocation uses /home/sglang-npu-bot/scripts as sys.path[0].
    from log_context import analyze_file, redact, expand_file, failure_cases_from_events
from zoneinfo import ZoneInfo

SOURCE_REPO = os.environ.get("SOURCE_REPOSITORY", "sgl-project/sglang")
TARGET_REPO = os.environ.get("TARGET_REPOSITORY", os.environ.get("GITHUB_REPOSITORY", ""))
WORKFLOWS = (
    "pr-test-npu.yml",
    "nightly-test-npu.yml",
    "full-test-npu.yml",
    "diffusion-ci-gt-gen-npu.yml",
)
DAILY_LABEL = "npu-diffusion-daily"
CLUSTER_LABEL = "npu-diffusion-cluster"
LIVE_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "action_required"}
CANCELLED = {"cancelled", "skipped", "neutral"}
DIFFUSION_JOBS = re.compile(r"multimodal-gen-test-(?:1|4)-npu-a3|multimodal-diffusion-gen-(?:1|2)npu", re.I)
SIGNAL = re.compile(r"(?:traceback|\berror\b|\bfailed\b|assertion|timeout|exception|fatal|out of memory|oom|killed|npu error)", re.I)
SECRET = re.compile(r"(?i)(authorization\s*:\s*(?:bearer|token)\s+|(?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*)[\w.+/=\-]{6,}")
MAX_LOG_DOWNLOAD_BYTES = 30_000_000
MAX_LOG_TAIL_BYTES = 1_500_000  # legacy binary=True smoke helper only
MAX_STREAM_LOG_BYTES = 64 * 1024 * 1024  # bounded on-disk original
MAX_ISSUE_BODY = 58_000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sanitize(text: str) -> str:
    return redact(SECRET.sub(r"\1[REDACTED]", text))


def fingerprint_line(text: str) -> str:
    text = sanitize(text).lower()
    text = re.sub(r"\x1b\[[0-9;]*[a-z]", "", text)
    text = re.sub(r"\b0x[0-9a-f]+\b", "0xADDR", text)
    text = re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "<IP>", text)
    text = re.sub(r"\b[0-9a-f]{32,64}\b", "<HASH>", text)
    text = re.sub(r"(?:/tmp/|/home/runner/)[^\s:]+'?", "<PATH>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[t ][\d:.+z-]+\b", "<TIME>", text)
    text = re.sub(r"\bline \d+\b", "line N", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def fingerprint(text: str) -> str:
    return hashlib.sha256(fingerprint_line(text).encode()).hexdigest()[:14]


def failure_excerpt(raw: str) -> tuple[str, list[str]]:
    """Prefer the last exception/error rather than the generic GH step exit message."""
    lines = [sanitize(s.strip())[:700] for s in raw.splitlines()]
    hits = [(i, s) for i, s in enumerate(lines) if SIGNAL.search(s)]
    if not hits:
        return "Failure reason unavailable in downloaded job logs", []
    # Prefer a precise diffusion consistency mismatch over unrelated pip warnings
    # or generic GitHub runner errors emitted near the end of a failed job.
    consistency = [(i, s) for i, s in hits if "consistency check failed for" in s.lower()]
    test_failures = [(i, s) for i, s in hits if re.search(r"\bFAILED\s+.*test_diffusion_generation\[", s, re.I)]
    preferred = [(i, s) for i, s in hits if re.search(r"(?:\w+(?:Error|Exception):|\bERROR\b|\bTimeoutError:|\bRuntimeError:|\bAssertionError:)", s, re.I)]
    candidates = consistency or test_failures or preferred or hits
    candidates = [(i, s) for i, s in candidates if not re.search(r"Process completed with exit code|##\[error\]", s)] or candidates
    index, message = candidates[-1]
    region = lines[max(0, index - 4) : min(len(lines), index + 3)]
    return message[:360], region


class GitHubTransportError(RuntimeError):
    """Network failure after bounded retries. No API response was received."""


def progress(message: str) -> None:
    """Unbuffered progress reporting for systemd/journald."""
    print(f"[NPU-CI] {datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {message}",
          file=sys.stderr, flush=True)


class GitHubLogRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS log redirects without forwarding GitHub credentials.

    GitHub job-log endpoints respond with an HTTPS redirect to a short-lived
    storage URL. urllib's default redirect handler copies Authorization across
    hosts, which can make signed Azure Blob URLs reject the download with 401
    and unnecessarily disclose the GitHub PAT to the storage service.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        before = urllib.parse.urlsplit(req.full_url)
        after = urllib.parse.urlsplit(newurl)
        if after.scheme.lower() != "https" or not after.hostname:
            raise urllib.error.HTTPError(
                req.full_url, code, "Refusing non-HTTPS GitHub log redirect", headers, fp
            )
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        before_origin = (before.scheme.lower(), before.hostname, before.port)
        after_origin = (after.scheme.lower(), after.hostname, after.port)
        if before_origin != after_origin:
            for mapping in (redirected.headers, redirected.unredirected_hdrs):
                for key in list(mapping):
                    if key.lower() in ("authorization", "proxy-authorization"):
                        del mapping[key]
        return redirected


def open_github_job_log(request, timeout):
    """An isolated opener so unrelated API/GLM transport remains unchanged."""
    return urllib.request.build_opener(GitHubLogRedirectHandler()).open(request, timeout=timeout)


class GitHub:
    def __init__(self, token: str | None = None, base: str = "https://api.github.com",
                 public_read_proxy: str | None = None):
        """Optional proxy is ONLY for anonymous GET requests to public GitHub API.

        Never tunnel an authenticated request or a write through a third-party
        GitHub reverse proxy. GITHUB_TOKEN/PAT must go straight to GitHub.
        """
        self.token = token
        self.base = base.rstrip("/")
        self.public_read_proxy = (public_read_proxy or "").strip().rstrip("/")
        if self.public_read_proxy:
            parsed = urllib.parse.urlsplit(self.public_read_proxy)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("GITHUB_PUBLIC_READ_PROXY must be an HTTPS URL without credentials or query")
            if self.base != "https://api.github.com":
                raise ValueError("Public GitHub reverse proxy is only supported for api.github.com")
            if self.token:
                raise ValueError("Refusing to forward GitHub credentials through a public reverse proxy")

    def request(self, method: str, path: str, payload=None, *, binary: bool = False, stream_to: Path | None = None):
        if stream_to is not None and (method != "GET" or payload is not None):
            raise ValueError("Streaming requires GET without payload")
        origin_url = self.base + path if path.startswith("/") else path
        url = origin_url
        if self.public_read_proxy:
            if method != "GET" or payload is not None:
                raise ValueError("Public GitHub reverse proxy only permits unauthenticated GET requests")
            parsed_origin = urllib.parse.urlsplit(origin_url)
            if parsed_origin.scheme != "https" or parsed_origin.hostname != "api.github.com":
                raise ValueError("Public reverse proxy only permits api.github.com requests")
            # Full-URL path rewriting, as used by SGLang NPU CI Git proxy.
            url = f"{self.public_read_proxy}/{origin_url}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "sglang-npu-diffusion-reporter", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        for attempt in range(4):
            try:
                with (open_github_job_log(request, timeout=50) if binary or stream_to is not None else urllib.request.urlopen(request, timeout=50)) as response:
                    if stream_to is not None:
                        # Full bounded original on disk; no tail-only evidence loss.
                        # Atomic rename and restrictive permissions; on failures remove
                        # incomplete downloads before retry. Original is never published.
                        path_target = Path(stream_to)
                        path_target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        path_target.parent.chmod(0o700)
                        partial = path_target.with_name(path_target.name + ".part")
                        count = 0
                        with partial.open("wb") as fout:
                            partial.chmod(0o600)
                            while count < MAX_STREAM_LOG_BYTES:
                                block = response.read(min(65536, MAX_STREAM_LOG_BYTES - count))
                                if not block:
                                    break
                                fout.write(block)
                                count += len(block)
                            # Probe one byte so a limit hit is explicitly reported.
                            truncated = bool(response.read(1)) if count == MAX_STREAM_LOG_BYTES else False
                        partial.replace(path_target)
                        return {"downloaded_bytes": count, "truncated": truncated}
                    if binary:
                        # Keep the tail, where stack traces usually appear, instead of
                        # the beginning. Bound network traffic and retained data.
                        tails = deque()
                        tail_size = 0
                        downloaded = 0
                        while downloaded < MAX_LOG_DOWNLOAD_BYTES:
                            block = response.read(min(64 * 1024, MAX_LOG_DOWNLOAD_BYTES - downloaded))
                            if not block:
                                break
                            downloaded += len(block)
                            tails.append(block)
                            tail_size += len(block)
                            while tail_size > MAX_LOG_TAIL_BYTES and len(tails) > 1:
                                tail_size -= len(tails.popleft())
                        return b"".join(tails).decode("utf-8", errors="replace")
                    body = response.read().decode("utf-8")
                    if not body:
                        return {}
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError as exc:
                        if self.public_read_proxy:
                            raise RuntimeError("GitHub public proxy returned non-JSON; check whether it supports api.github.com (see SETUP guide)") from exc
                        raise
            except urllib.error.HTTPError as exc:
                if stream_to is not None:
                    Path(str(stream_to) + ".part").unlink(missing_ok=True)
                if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                    progress(f"HTTP {exc.code} on GitHub API {method} {path.split('?')[0]}; "
                             f"retry {attempt + 1}/3")
                    time.sleep(min(16, 2 ** attempt))
                    continue
                detail = exc.read(500).decode("utf-8", errors="replace")
                raise RuntimeError(f"GitHub API {method} {path}: HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if stream_to is not None:
                    Path(str(stream_to) + ".part").unlink(missing_ok=True)
                # urllib wraps temporary DNS resolver failures as URLError.
                # Retrying just HTTP 5xx would otherwise lose the entire report
                # after processing hundreds of jobs.
                reason = sanitize(str(getattr(exc, "reason", exc)))[:180]
                if attempt < 3:
                    progress(f"NETWORK {type(exc).__name__} on GitHub API {method} "
                             f"{path.split('?')[0]}: {reason}; retry {attempt + 1}/3")
                    time.sleep(2 ** attempt)
                    continue
                raise GitHubTransportError(
                    f"GitHub API {method} {path.split('?')[0]}: network failure "
                    f"after 4 attempts ({reason}); verify api.github.com DNS, "
                    "service proxy and outbound HTTPS"
                ) from exc
            except Exception:
                # Clean up a partially written log on unexpected disk/parser
                # failures; do not relabel such errors as transient DNS errors.
                if stream_to is not None:
                    Path(str(stream_to) + ".part").unlink(missing_ok=True)
                raise
        raise RuntimeError("GitHub API retry budget exhausted")

    def get(self, path: str, params: dict | None = None):
        if params:
            path += "?" + urllib.parse.urlencode(params)
        return self.request("GET", path)

    def list_all(self, path: str, params: dict | None = None, max_pages: int = 4):
        data = []
        p = dict(params or {})
        p["per_page"] = 100
        for page in range(1, max_pages + 1):
            p["page"] = page
            chunk = self.get(path, p)
            if not isinstance(chunk, list):
                raise RuntimeError(f"Expected list from {path}")
            data.extend(chunk)
            if len(chunk) < 100:
                break
        return data

    def issue(self, number: int, payload: dict):
        return self.request("PATCH", f"/repos/{TARGET_REPO}/issues/{number}", payload)

    def create_issue(self, payload: dict):
        return self.request("POST", f"/repos/{TARGET_REPO}/issues", payload)


def workflow_runs(gh: GitHub, workflow: str, since: datetime, limit: int) -> tuple[list[dict], bool]:
    """Enumerate created-in-window runs and probe one extra to detect truncation."""
    if limit < 1:
        raise ValueError("MAX_RUNS_PER_WORKFLOW must be positive")
    runs = []
    # Query limit+1 to distinguish exactly `limit` from >limit matches.
    max_pages = (limit + 1 + 99) // 100
    for page in range(1, max_pages + 1):
        obj = gh.get(
            f"/repos/{SOURCE_REPO}/actions/workflows/{workflow}/runs",
            {"per_page": 100, "page": page,
             "created": f">={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"},
        )
        if not isinstance(obj, dict) or not isinstance(obj.get("workflow_runs"), list):
            raise RuntimeError(f"Malformed workflow run response for {workflow}")
        batch = obj["workflow_runs"]
        runs.extend(batch)
        if len(runs) > limit or len(batch) < 100:
            break
    return runs[:limit], len(runs) <= limit


def job_matches(job: dict, workflow: str) -> bool:
    name = job.get("name") or ""
    # GT-generation workflow only: its matrix jobs are explicitly diffusion.
    return bool(DIFFUSION_JOBS.search(name))


def get_jobs(gh: GitHub, run_id: int, attempt: int) -> list[dict]:
    """GitHub Jobs REST API returns {total_count: int, jobs: [...]}.

    Unlike Issues API, this is not a top-level list. Always validate the
    response to avoid generating false zero-job reports on schema errors.
    """
    path = f"/repos/{SOURCE_REPO}/actions/runs/{run_id}/attempts/{attempt}/jobs"
    jobs = []
    for page in range(1, 11):
        payload = gh.get(path, {"per_page": 100, "page": page})
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise RuntimeError(f"Expected object with jobs list from {path}")
        batch = payload["jobs"]
        jobs.extend(batch)
        total = payload.get("total_count")
        if isinstance(total, int):
            if len(jobs) >= total:
                return jobs
            if len(batch) < 100:
                raise RuntimeError(f"Incomplete jobs result from {path}: got {len(jobs)} of {total}")
        elif len(batch) < 100:
            return jobs
    raise RuntimeError(f"More than 1000 jobs in {path}; refusing a truncated result")


def snapshot(gh: GitHub, now: datetime, hours: int, max_runs: int, max_logs: int) -> dict:
    try:
        from scripts.collection_evidence import main_runs, source_evidence, attach_observations
    except ImportError:
        from collection_evidence import main_runs, source_evidence, attach_observations
    since = now - timedelta(hours=hours)
    records = []
    comparison_records = []
    run_records = []
    seen_jobs = set()
    coverage = []
    try:
        independent_main, main_complete = main_runs(gh, SOURCE_REPO, now)
        coverage.append(f'Independent main discovery: {len(independent_main)} runs / seven days; '+
                        ('complete' if main_complete else 'CAPPED at 32 runs'))
    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as exc:
        independent_main=[]
        coverage.append(f'Independent main discovery unavailable ({type(exc).__name__})')
    available_workflows = 0
    job_lists_attempted = 0
    job_lists_loaded = 0
    candidate_logs = []
    stats = Counter()
    for workflow in WORKFLOWS:
        progress(f"Scanning {workflow}: finding runs within {hours}h (limit {max_runs})")
        try:
            runs, complete = workflow_runs(gh, workflow, since, max_runs)
        except RuntimeError as exc:
            coverage.append(f"{workflow}: workflow enumeration unavailable ({str(exc)[:150]})")
            if workflow != 'pr-test-npu.yml' or not independent_main: continue
            runs,complete=[],False
        runs=[r for r in runs if not r.get('created_at') or since <= parse_timestamp(r['created_at']) <= now]
        if workflow == 'pr-test-npu.yml':
            by_id={r['id']:r for r in runs}
            by_id.update({r['id']:r for r in independent_main})
            runs=list(by_id.values())
        available_workflows += 1
        progress(f"{workflow}: enumerated {len(runs)} runs; {'complete' if complete else 'CAPPED'}")
        coverage.append(f"{workflow}: {len(runs)} runs inspected; {'complete within query window' if complete else f'CAPPED at {max_runs} runs'}")
        consecutive_transport_errors = 0
        for run_index, run in enumerate(runs, 1):
            is_comparison=bool(run.get('created_at') and parse_timestamp(run['created_at']) < since)
            run_records.append({k:run.get(k) for k in ('id','html_url','head_sha','head_branch',
                'event','status','conclusion','created_at','updated_at','run_attempt')})
            run_records[-1].update(workflow=workflow, comparison_only=is_comparison)
            if run.get('updated_at') and parse_timestamp(run['updated_at'])>now:
                run_records[-1].update(status='unknown',conclusion=None,snapshot_status_unverified=True)
            jobs = []
            job_lists_attempted += 1
            try:
                jobs = get_jobs(gh, run["id"], run.get("run_attempt") or 1)
                job_lists_loaded += 1
                consecutive_transport_errors = 0
            except GitHubTransportError as exc:
                consecutive_transport_errors += 1
                coverage.append(f"Run {run['id']}: job list unavailable ({str(exc)[:120]})")
                progress(f"WARNING: {workflow} run {run['id']}: network failure "
                         f"({consecutive_transport_errors} consecutive): {exc}")
                if consecutive_transport_errors >= 3:
                    raise RuntimeError(
                        f"Aborting CI scan after {consecutive_transport_errors} "
                        "consecutive GitHub API network failures; refusing to "
                        "publish an incomplete report. Check DNS/proxy/network."
                    ) from exc
            except RuntimeError as exc:
                consecutive_transport_errors = 0
                coverage.append(f"Run {run['id']}: job list unavailable ({str(exc)[:120]})")
                progress(f"WARNING: {workflow} run {run['id']}: {str(exc)[:160]}")
            gates=[]
            for gate in jobs:
                if re.search(r'(?:^| / )pr-gate$',gate.get('name','')):
                    state={k:gate.get(k) for k in ('name','status','conclusion')}
                    if gate.get('started_at') and parse_timestamp(gate['started_at'])>now:
                        state.update(status='queued',conclusion=None)
                    elif gate.get('completed_at') and parse_timestamp(gate['completed_at'])>now:
                        state.update(status='in_progress',conclusion=None)
                    gates.append(state)
            for job in jobs:
                if not job_matches(job, workflow):
                    continue
                identity=(job['id'],run.get('run_attempt') or 1)
                if identity in seen_jobs: continue
                seen_jobs.add(identity)
                status=job.get('status') or 'unknown'
                conclusion = job.get("conclusion") or (status if status != "completed" else "unknown")
                after_snapshot=bool(job.get('completed_at') and parse_timestamp(job['completed_at'])>now)
                if after_snapshot: status=conclusion='in_progress'
                if not is_comparison: stats[conclusion] += 1
                test_steps=[{k:s.get(k) for k in ('name','status','conclusion','started_at','completed_at')}
                            for s in job.get('steps',[]) if re.search(r'run.*test|test.*run',s.get('name',''),re.I)]
                for step in test_steps:
                    if step.get('started_at') and parse_timestamp(step['started_at'])>now:
                        step.update(status='queued',conclusion=None,started_at=None,completed_at=None)
                    elif step.get('completed_at') and parse_timestamp(step['completed_at'])>now:
                        step.update(status='in_progress',conclusion=None,completed_at=None)
                run_conclusion=run.get('conclusion')
                if run.get('updated_at') and parse_timestamp(run['updated_at'])>now:
                    run_conclusion=None
                record = {
                    "run_id": run["id"], "attempt": run.get("run_attempt", 1),
                    "workflow": workflow, "job_id": job["id"], "name": job.get("name", ""),
                    "conclusion": conclusion, "run_conclusion": run_conclusion,
                    "status":status, "test_steps":test_steps, "gate_jobs":gates, "comparison_only":is_comparison,
                    "evidence_after_snapshot":after_snapshot,
                    "url": job.get("html_url") or f"https://github.com/{SOURCE_REPO}/actions/runs/{run['id']}",
                    "run_url": run.get("html_url"), "created_at": run.get("created_at"),
                    "head_sha": run.get("head_sha"),
                    "run_event": run.get("event"), "head_branch": run.get("head_branch"),
                    "head_repository": (run.get("head_repository") or {}).get("full_name")
                        if isinstance(run.get("head_repository"), dict) else None,
                    "source_repository": SOURCE_REPO,
                    "started_at": job.get("started_at"), "completed_at": job.get("completed_at"),
                    "runner_group_name": job.get("runner_group_name"),
                    "runner_name": job.get("runner_name"), "runner_labels":job.get("labels",[]),
                    "pr_numbers": [p.get("number") for p in run.get("pull_requests", []) if p.get("number")] if run.get("event") in ("pull_request", "pull_request_target") else [],
                }
                (comparison_records if is_comparison else records).append(record)
                if not after_snapshot and (conclusion in LIVE_CONCLUSIONS or
                        (conclusion=='cancelled' and any(s.get('conclusion')=='failure' for s in test_steps))):
                    candidate_logs.append(record)
            if run_index % 20 == 0 or run_index == len(runs):
                progress(f"{workflow}: {run_index}/{len(runs)} runs; "
                         f"job-lists={job_lists_loaded}/{job_lists_attempted}; "
                         f"matching-diffusion-jobs={len(records)}; "
                         f"failure-job-candidates={len(candidate_logs)}")
    if available_workflows == 0:
        raise RuntimeError("All source workflow API reads failed; refusing to publish an unverified empty report.")
    if job_lists_attempted and not job_lists_loaded:
        raise RuntimeError(f"All {job_lists_attempted} upstream job-list requests failed; refusing to publish an unverified zero-job report. Check GitHub API response shape/permissions.")
    if job_lists_loaded < job_lists_attempted:
        coverage.append(f"PARTIAL JOB COVERAGE: {job_lists_loaded}/{job_lists_attempted} run job-lists available; results are incomplete.")
    else:
        coverage.append(f"Job-list coverage: {job_lists_loaded}/{job_lists_attempted} run job-lists available.")
    # Main-first evidence collection: a flood of recent PR-only jobs must NOT
    # exhaust the log budget before older main failures are investigated.
    try:
        from scripts.issue_taxonomy import job_scope
    except ModuleNotFoundError:
        from issue_taxonomy import job_scope
    candidate_logs.sort(key=lambda r:(job_scope(r) == 'main', r.get('created_at') or ''), reverse=True)
    all_records=records+comparison_records
    main_executed=[j for j in all_records if job_scope(j)=='main' and j['conclusion'] not in ('skipped','queued','in_progress','unknown')]
    latest_main=max(main_executed,key=lambda j:j.get('created_at') or '',default={}).get('run_id')
    pass_limit=max(0,int(os.getenv('INCIDENT_MAX_PASS_LOGS','24'))) if max_logs else 0
    successes=sorted([j for j in all_records if j['conclusion']=='success' and not j.get('evidence_after_snapshot')],
                     key=lambda j:(j.get('run_id')==latest_main,job_scope(j)=='main',j.get('created_at') or ''),reverse=True)
    main_logs=[j for j in all_records if j.get('run_id')==latest_main and j['conclusion'] in ('success',*LIVE_CONCLUSIONS)]
    pool=main_logs+candidate_logs+successes
    selected=[]; selected_ids=set(); failure_used=pass_used=0
    for j in pool:
        if j['job_id'] in selected_ids:continue
        failed=j in candidate_logs
        if failed and failure_used>=max_logs:continue
        if not failed and (j['conclusion']!='success' or pass_used>=pass_limit):continue
        selected.append(j); selected_ids.add(j['job_id'])
        if failed:failure_used+=1
        else:pass_used+=1
    errors = []
    successful_log_downloads = 0
    analyzed_jobs = 0
    truncated_logs = 0
    log_root = Path("reports") / "raw-logs" / now.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
    if selected:
        log_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_root.chmod(0o700)
    progress(f"Completed API scan: {job_lists_loaded}/{job_lists_attempted} job-lists "
             f"loaded; {len(records)} matching jobs; {len(candidate_logs)} failed jobs; "
             f"downloading up to {max_logs} logs")
    for log_index, rec in enumerate(selected, 1):
        progress(f"Downloading failed job log {log_index}/{min(max_logs, len(candidate_logs))}: "
                 f"job={rec['job_id']}")
        try:
            raw_path = log_root / f"job-{int(rec['job_id'])}.log"
            transfer = gh.request("GET", f"/repos/{SOURCE_REPO}/actions/jobs/{rec['job_id']}/logs",
                                  stream_to=raw_path)
            successful_log_downloads += 1
            if transfer.get("truncated"):
                truncated_logs += 1
            analysis = analyze_file(raw_path)
            # Structured testcase FAIL records are retained for later PASS
            # comparison; no generic successful-job inference is permitted.
            try:
                from scripts.incident_verifier import parse_evidence
            except ImportError:
                from incident_verifier import parse_evidence
            rec["case_evidence"] = parse_evidence(raw_path)
            analyzed_jobs += 1
            primary = analysis.get("primary")
            rec["failure_cases"] = analysis["failure_cases"]
            rec["testcase_accounting"] = analysis["testcase_accounting"]
            rec["failure_cases_version"] = 2
            attach_observations(rec, raw_path)
            known_cases = {x.get("testcase") for x in rec["failure_cases"]}
            for case,evidence in rec["case_evidence"].items():
                if evidence.get('status') != 'FAIL':continue
                if case not in known_cases:
                    rec["failure_cases"].append({"id":fingerprint("structured-fail:"+case),
                        "testcase":case,"category":"unclassified",
                        "signature":"Structured testcase FAIL; diagnostic exception unavailable",
                        "severity":0,"log_excerpt":[],"evidence_source":"NPU_CI_TEST_RESULT"})
            rec["failure_category"] = primary["category"] if primary else "unclassified"
            rec["log_analysis"] = {
                # A bounded, redacted evidence packet; raw content only stays on disk.
                "source_bytes": analysis["source_bytes"],
                "total_lines": analysis["total_lines"],
                "truncated": bool(transfer.get("truncated")),
                "matched_lines": analysis["matched_lines"],
                "unique_signatures": analysis["unique_signatures"],
                "signature_overflow": analysis["signature_overflow"],
                "events": analysis["events"][:12],
                "focused_text": analysis["focused_text"][:16000],
            }
            if primary:
                rec["error_signature"] = primary["signature"]
                rec["log_excerpt"] = [line for w in primary["windows"][:1]
                                      for _, line in w["rows"]][-20:]
                rec["fingerprint"] = primary["id"]
                rec["primary_test_case"] = primary.get("testcase")
                rec["metrics"] = primary.get("metrics", {})
                rec["thresholds"] = primary.get("thresholds", {})
                rec["failed_checks"] = primary.get("failed_checks", [])
                rec["failed_frames"] = primary.get("failed_frames", [])
                # Stage 2: second streaming pass, targeting the primary failure
                # with wider context. Store redacted evidence; never send raw logs.
                expanded = expand_file(raw_path, primary["id"], context=18,
                                       max_bytes=12_000, max_lines=100)
                rec["expanded_evidence"] = expanded["text"][:8500]
            else:
                rec["error_signature"] = "Unclassified: no diagnostic signal in available log"
                rec["log_excerpt"] = []
                rec["fingerprint"] = fingerprint(f"unclassified:{rec['name']}")
            progress(f"Analyzed log job={rec['job_id']}: bytes={transfer['downloaded_bytes']}, "
                     f"lines={analysis['total_lines']}, signatures={analysis['unique_signatures']}, "
                     f"primary={rec['failure_category']}, truncated={transfer.get('truncated')}")
        except Exception as exc:
            progress(f"WARNING: unable to download job log {rec['job_id']}: {sanitize(str(exc))[:200]}")
            errors.append(f"Job {rec['job_id']}: unable to download log ({str(exc)[:130]})")
    evidence=source_evidence(gh,SOURCE_REPO,
        sorted(all_records,key=lambda j:(j.get('run_id')==latest_main,job_scope(j)=='main'),reverse=True)) if max_logs else {}
    return {
        "schema_version":3, "window_start_utc":since.strftime('%Y-%m-%dT%H:%M:%SZ'),
        "comparison_jobs":comparison_records, "runs":run_records, "source_evidence":evidence,
        "latest_main_run_id":latest_main, "successful_logs_selected":pass_used,
        "log_collection":{"failure_limit":max_logs,"pass_limit":pass_limit,
                          "selected":len(selected),"unselected":len({j['job_id'] for j in pool})-len(selected)},
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lookback_hours": hours, "source_repository": SOURCE_REPO,
        "coverage": coverage, "counts": dict(stats), "jobs": records,
        "job_lists_attempted": job_lists_attempted, "job_lists_loaded": job_lists_loaded,
        "log_analysis_attempted": min(max_logs, len(candidate_logs)),
        "log_download_succeeded": successful_log_downloads,
        "log_analyzed": analyzed_jobs,
        "log_truncated": truncated_logs,
        "log_storage": str(log_root),
        "log_download_failed": len(errors),
        "failed_jobs_without_logs": max(0, len(candidate_logs) - max_logs),
        "errors": errors,
    }


def _case_records_for_job(job: dict) -> list[dict]:
    """One failed job can contribute to multiple testcase-specific clusters.

    Reconstruct from v10-v12 saved event windows where possible, so a GLM
    reanalysis does not require a new network scan or raw-log download.
    """
    from_events = None
    if job.get('log_analysis', {}).get('events'):
        from_events = failure_cases_from_events(job['log_analysis']['events'])
    cases = (job.get('failure_cases') if job.get('failure_cases_version',0) >= 2
             else from_events or job.get('failure_cases')) or []
    if not cases:
        # Legacy fallback: older JSON retained the primary failure alone.
        if not job.get('fingerprint'):
            return []
        cases = [{'id':job['fingerprint'], 'category':job.get('failure_category', 'unclassified'),
                  'testcase':job.get('primary_test_case'),
                  'signature':job.get('error_signature', ''),
                  'metrics':job.get('metrics', {}),
                  'thresholds':job.get('thresholds', {}),
                  'failed_checks':job.get('failed_checks', []),
                  'failed_frames':job.get('failed_frames', []),
                  'log_excerpt':job.get('log_excerpt', []),
                  'severity':job.get('log_analysis',{}).get('events',[{}])[0].get('severity',0)
                   if job.get('log_analysis',{}).get('events') else 0}]
    seen = set()
    result = []
    for case in cases:
        if not isinstance(case,dict) or not case.get('id') or case['id'] in seen:
            continue
        seen.add(case['id'])
        rec = dict(job)
        rec.update({'fingerprint':case['id'], 'failure_category':case.get('category','unclassified'),
                    'primary_test_case':case.get('testcase'),
                    'error_signature':case.get('signature',''),
                    'performance':case.get('performance') or [], 'http_statuses':case.get('http_statuses') or [],
                    'fingerprint_aliases':case.get('fingerprint_aliases',[]),
                    'causal_signature':case.get('causal_signature'),
                    'execution_observations':case.get('execution_observations',[]),
                    'execution_sections':case.get('execution_sections',[]),
                    'metrics':case.get('metrics') or {}, 'thresholds':case.get('thresholds') or {},
                    'failed_checks':case.get('failed_checks') or [],
                    'failed_frames':case.get('failed_frames') or [],
                    'log_excerpt':case.get('log_excerpt') or [],
                    'case_severity':case.get('severity',0)})
        # The legacy job summary was already repaired from expanded evidence in v12.
        if case['id'] == job.get('fingerprint') and job.get('saved_evidence_reconciled'):
            for k in ('metrics','thresholds','failed_checks','failed_frames'):
                rec[k] = job.get(k, rec[k])
        if case['id'] != job.get('fingerprint'):
            # The old primary expansion belongs to ANOTHER testcase. Never pass it
            # as evidence for this case; use the testcase-specific excerpt instead.
            rec['expanded_evidence'] = '\n'.join(rec['log_excerpt'])[:3500]
        result.append(rec)
    return result


def clusters_from_snapshot(data: dict) -> list[dict]:
    """Cluster testcase failures; job and failure counts are not disjoint."""
    grouped = defaultdict(list)
    for job in data['jobs']:
        cancelled_test_failure=(job.get('conclusion')=='cancelled' and
            any(s.get('conclusion')=='failure' for s in job.get('test_steps',[])))
        if job.get('conclusion') and job.get('conclusion') not in LIVE_CONCLUSIONS and not cancelled_test_failure:
            continue
        for rec in _case_records_for_job(job):
            grouped[rec['fingerprint']].append(rec)
    result = []
    for key, jobs in grouped.items():
        timestamps = [j.get('created_at') for j in jobs if j.get('created_at')]
        first = min(timestamps) if timestamps else 'unknown'
        last = max(timestamps) if timestamps else 'unknown'
        # Deduplicate only *known* job IDs; old synthetic snapshots may lack them.
        seen_ids = set()
        unique_jobs = []
        for job in jobs:
            job_id = job.get('job_id')
            if job_id is not None and job_id in seen_ids:
                continue
            if job_id is not None:
                seen_ids.add(job_id)
            unique_jobs.append(job)
        jobs = unique_jobs
        result.append({'fingerprint':key,'signature':jobs[0].get('error_signature',''),
                       'fingerprint_aliases':sorted({a for j in jobs for a in j.get('fingerprint_aliases',[]) if a!=key}),
                       'first_seen_snapshot':first,'last_seen_snapshot':last,
                       'count':len(jobs),'severity':max((j.get('case_severity',0) for j in jobs),default=0),
                       'jobs':jobs})
    return sorted(result,key=lambda g:(-g['severity'],-g['count'],g['fingerprint']))


def get_existing_issues(gh: GitHub, label: str) -> list[dict]:
    return gh.list_all(f"/repos/{TARGET_REPO}/issues", {"labels": label, "state": "all"}, max_pages=10)


def ensure_label(gh: GitHub, label: str, color: str, description: str):
    try:
        gh.request("POST", f"/repos/{TARGET_REPO}/labels", {"name": label, "color": color, "description": description})
    except RuntimeError as exc:
        if "HTTP 422" not in str(exc):
            raise


def sync_incident_labels(gh, cluster, existing=None):
    """Update only incident taxonomy labels; preserve existing human labels."""
    try:
        from scripts.issue_taxonomy import LABELS, classify_cluster, merged_labels
    except ModuleNotFoundError:
        from issue_taxonomy import LABELS, classify_cluster, merged_labels
    taxonomy = classify_cluster(cluster)
    for label in taxonomy['labels']:
        color, description = LABELS[label]
        ensure_label(gh, label, color, description)
    current = existing.get('labels', []) if existing else []
    return merged_labels(current, taxonomy['labels'])


def _incident_title(cluster):
    """Human-readable title from a validated testcase, never a timestamp or init line."""
    jobs = cluster.get('jobs') or []
    sample = jobs[0] if jobs else {}
    case = str(sample.get('primary_test_case') or '').strip()
    category = str(sample.get('failure_category') or '')
    if '::' in case:
        case = case.rsplit('::', 1)[-1]
    if len(case) > 78:
        case = case[:75] + '...'
    label = {'diffusion_consistency':'quality consistency regression',
             'performance_regression':'performance regression',
             'pytest_failure':'test execution failure',
             'diffusion_test_failure':'diffusion testcase failure',
             'npu_oom':'NPU out of memory',
             'ascend_runtime':'Ascend runtime failure'}.get(category,'failure')
    if case:
        return f'{case} — {label}'
    return label if category in ('npu_oom', 'ascend_runtime') else 'Unclassified CI failure'


def _eligible_for_issue(cluster):
    """Publish main-impact evidence only. Never create PR-only Issues by default.

    Existing Issues are *tracked* regardless of this policy. Scope alone does
    not establish root cause. The categories below require a terminal test
    failure, a concrete OOM, or an explicit runtime error (not an HCCL init).
    """
    from_issue = cluster.get('jobs') or []
    if not from_issue:
        return False, 'No evidence-backed failing job'
    try:
        from scripts.issue_taxonomy import classify_cluster
    except ModuleNotFoundError:
        from issue_taxonomy import classify_cluster
    taxonomy = classify_cluster(cluster)
    if taxonomy['scope'] != 'main':
        if taxonomy['scope'] == 'not-merged' and os.getenv('CREATE_PR_ONLY_ISSUES', '0') == '1':
            pass
        else:
            return False, 'PR-only/unconfirmed: included in daily watchlist, not a new Issue'
    allowed = {'diffusion_consistency', 'diffusion_test_failure',
               'pytest_failure', 'performance_regression', 'npu_oom', 'ascend_runtime'}
    if not any(j.get('failure_category') in allowed for j in from_issue):
        return False, 'No verified testcase-level or concrete runtime failure signal'
    if any(re.search(r'world_size=\d+.*backend=hccl\s+timeout=\d+',
                     str(j.get('error_signature','')), re.I) for j in from_issue):
        return False, 'Normal HCCL initialization is not a failure signal'
    return True, ''


def assign_cluster_ids(gh: GitHub, clusters: list[dict], dry_run: bool, cap: int,
                       *, publish=True) -> None:
    """Match persistent IDs first; only allocate an ID after creating an Issue.

    The read-only pass is used before GLM, so the model can investigate without
    any premature GitHub writes. The publishing pass is run after triage.
    """
    try:
        from scripts.issue_taxonomy import annotate_clusters
    except ModuleNotFoundError:
        from issue_taxonomy import annotate_clusters
    annotate_clusters(clusters)
    if dry_run:
        for item in clusters:
            item['stable_id'] = 'PREVIEW-' + item['fingerprint'][:6]
            item['issue_url'] = None
            item['analysis_ref'] = item['stable_id']
        return
    issues = get_existing_issues(gh, CLUSTER_LABEL)
    by_fingerprint = {}
    seen_ids = []
    for issue in issues:
        match = re.search(r'<!-- npu-diffusion-fingerprint:([a-f0-9]+) -->', issue.get('body') or '')
        if match:
            by_fingerprint[match.group(1)] = issue
        prefix = re.search(r'NPU-D(\d+)', issue.get('title') or '')
        if prefix:
            seen_ids.append(int(prefix.group(1)))
    next_id = max([3, *seen_ids]) + 1
    created = 0
    safe_cap = max(0, min(int(cap), int(os.getenv('MAX_PUBLISHED_MAIN_ISSUES','3'))))
    for item in clusters:
        matches={by_fingerprint[a]['number']:by_fingerprint[a] for a in
                 [item['fingerprint']]+item.get('fingerprint_aliases',[]) if a in by_fingerprint}
        old=next(iter(matches.values())) if len(matches)==1 else None
        if old and item['fingerprint'] not in by_fingerprint:
            item['current_fingerprint']=item['fingerprint']
            item['fingerprint']=next(a for a,i in by_fingerprint.items() if i['number']==old['number'])
        if old:
            stable = re.search(r'NPU-D\d+', old.get('title',''))
            item['stable_id'] = stable.group(0) if stable else 'OBS-' + item['fingerprint']
            item['issue_url'] = old.get('html_url')
            item.setdefault('analysis_ref',item['stable_id'])
            # For legacy PR-only incidents preserve historical provenance, but
            # stop refreshing their Issues on each observation.
            if not publish or item.get('incident_scope') != 'main':
                continue
        else:
            if not publish:
                item['stable_id'] = 'OBS-' + item['fingerprint']
                item['analysis_ref'] = item['stable_id']
                item['issue_url'] = None
                continue
            eligible, reason = _eligible_for_issue(item)
            if not eligible or created >= safe_cap:
                item['stable_id'] = item.get('analysis_ref') or 'OBS-' + item['fingerprint']
                item['issue_url'] = None
                item['not_published_reason'] = reason or f'Main Issue cap ({safe_cap})'
                continue
            item['stable_id'] = f'NPU-D{next_id:03d}'
        lines = [f"<!-- npu-diffusion-fingerprint:{item['fingerprint']} -->",
                 '<!-- npu-ci-report-language:en -->',
                 f"# {item['stable_id']} — {_incident_title(item)}",
                 f"**Observed signature:** `{sanitize(item['signature'])[:350]}`",
                 f"**Observed failures in snapshot:** {item['count']}",
                 f"**First seen in snapshot:** {item['first_seen_snapshot']}",
                 f"**Last seen in snapshot:** {item['last_seen_snapshot']}",
                 '**Root cause:** not verified. A passing job or PR association is not causal proof.',
                 '## Reproduction evidence']
        lines += [f"- [{job.get('name','job')}]({job.get('url','')}) (run {job.get('run_id','?')}, "
                  f"attempt {job.get('attempt','?')}) "
                  f"category={job.get('failure_category', 'unknown')} "
                  f"testcase={job.get('primary_test_case') or 'unknown'} "
                  f"metrics={sanitize(json.dumps(job.get('metrics', {}), sort_keys=True))[:240]} "
                  f"thresholds={sanitize(json.dumps(job.get('thresholds', {}), sort_keys=True))[:240]}"
                  for job in item['jobs'][:16]]
        lines += ['', '## Resolution policy',
                  'Close only after independently verified equivalent testcase-level PASS.']
        body = '\n'.join(lines)[:MAX_ISSUE_BODY]
        if old:
            payload = {'body':body,'labels':sync_incident_labels(gh,item,old)}
            if old.get('state') == 'closed': payload['state'] = 'open'
            gh.issue(old['number'],payload)
        else:
            try:
                ensure_label(gh,CLUSTER_LABEL,'b60205','Persistent verified main-branch diffusion CI failure')
                new = gh.create_issue({'title':f"[{item['stable_id']}] {_incident_title(item)}"[:125],
                     'body':body,'labels':[CLUSTER_LABEL]+sync_incident_labels(gh,item)})
            except (RuntimeError, OSError, ValueError) as exc:
                item['stable_id'] = item.get('analysis_ref') or 'OBS-' + item['fingerprint']
                item['issue_url'] = None
                item['not_published_reason'] = 'Issue publication failed; no ID reserved'
                progress('WARNING: cluster Issue creation failed: '+type(exc).__name__)
                continue
            item['issue_url'] = new['html_url']
            next_id += 1
            created += 1

def glm_analysis(data: dict, clusters: list[dict], mode: str | None = None, gh=None, continuity=None) -> tuple[str, str]:
    """Evidence-driven analysis with locally pinned SGLang/Ascend skills.

    triage = one bounded LLM call; investigate = per-cluster calls plus synthesis;
    auto = investigate top-priority repeated clusters and summarize all others.
    All invocation paths are compatible with the previous two-argument API.
    """
    try:
        from scripts.glm_http import chat_completion, GLMEmptyResponseError
        from scripts.ci_investigation import (SYSTEM_POLICY, build_regression_context,
            compact_cluster, load_saved_history, load_skill_bundle, ranked_investigation_clusters,
            related_pr_context, relevant_skills)
    except ModuleNotFoundError:
        from glm_http import chat_completion, GLMEmptyResponseError
        from ci_investigation import (SYSTEM_POLICY, build_regression_context,
            compact_cluster, load_saved_history, load_skill_bundle, ranked_investigation_clusters,
            related_pr_context, relevant_skills)
    try:
        from scripts.model_evidence import bounded_json
    except ImportError:
        from model_evidence import bounded_json
    key = os.getenv("GLM_API_KEY", "").strip()
    base_url = os.getenv("GLM_API_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("GLM_MODEL", "").strip()
    cli_command = os.getenv("GLM_ANALYZER_COMMAND", "").strip()
    if not cli_command and not all([key, base_url, model]):
        return "Not run (no GLM agent command or complete API configuration).", "disabled"
    if cli_command:
        try:
            from scripts.glm_budget import enabled as budget_enabled
        except ImportError:
            from glm_budget import enabled as budget_enabled
        if budget_enabled():
            return ("CLI GLM_ANALYZER_COMMAND has no verifiable API usage. "
                    "Disable it and configure BigModel HTTP, or explicitly set "
                    "GLM_BUDGET_ENABLED=0 at your own risk.", "invalid_configuration")
    if not cli_command and not base_url.startswith("https://"):
        return "Not run: GLM_API_BASE_URL must use HTTPS.", "invalid_configuration"
    mode = (mode or os.getenv("GLM_ANALYSIS_MODE", "auto")).strip().lower()
    if mode not in ("triage", "investigate", "auto"):
        return "Invalid GLM_ANALYSIS_MODE. Use triage, investigate, or auto.", "invalid_configuration"
    max_cases = max(0, min(int(os.getenv("GLM_MAX_INVESTIGATIONS", "2")), 5))
    def stage_budget(name: str, default: int) -> int:
        """Upper bound for a single GLM request (reasoning + visible answer)."""
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError as exc:
            raise ValueError(name + ' must be an integer') from exc
        if not 512 <= value <= 32768:
            raise ValueError(name + ' must be within 512..32768')
        return value
    triage_budget = stage_budget('GLM_TRIAGE_MAX_TOKENS', 10000)
    cluster_budget = stage_budget('GLM_CLUSTER_MAX_TOKENS', 16000)
    synthesis_budget = stage_budget('GLM_SYNTHESIS_MAX_TOKENS', 20000)
    # Optional bounded GitHub evidence search. This is not part of the paid model call
    # and never claims a PR caused/fixed a regression solely based on timestamps.
    if gh is not None and not data.get('pr_evidence_collected') and os.getenv('PR_SEARCH_ENABLED', '1') == '1':
        try:
            try:
                from scripts.pr_forensics import discover_pr_leads, attach_pr_leads
            except ModuleNotFoundError:
                from pr_forensics import discover_pr_leads, attach_pr_leads
            for c in clusters:c['analysis_as_of']=data.get('generated_utc')
            discovered = discover_pr_leads(gh, clusters, SOURCE_REPO,
                max_per_cluster=int(os.getenv('PR_SEARCH_MAX_PER_CLUSTER', '3')),
                max_api_calls=int(os.getenv('PR_SEARCH_MAX_API_CALLS', '24')))
            attach_pr_leads(clusters, discovered)
            progress('Verified related PR leads for %d clusters' % len(discovered))
        except (RuntimeError,ValueError,TypeError,OSError) as exc:
            progress('WARNING: optional PR discovery unavailable: %s'%type(exc).__name__)
    history = load_saved_history(data)
    historical=data.get('historical_main_clusters',[])
    context = build_regression_context(data, clusters+historical, history)
    data['regression_context']=context
    # These extra reads are optional and MUST be attached only to PR numbers
    # recorded by GitHub run metadata; a linked PR is not an established cause.
    pr_context = related_pr_context(gh, clusters, SOURCE_REPO,
                   max_prs=int(os.getenv("GLM_MAX_PR_CONTEXT", "6"))) if mode != 'triage' else []
    try:
        from scripts.analysis_evidence import investigation_selection
    except ImportError:
        from analysis_evidence import investigation_selection
    prioritized = historical+(investigation_selection(clusters) if mode == 'auto' else clusters)
    data['investigation_selection']=[{'fingerprint':c['fingerprint'],
        'reason':c.get('investigation_reason','explicit investigation mode')} for c in prioritized]
    data['glm_telemetry']=[]
    deep = ranked_investigation_clusters(prioritized, max_cases) if mode != 'triage' else []
    evidence = {"generated_utc":data.get("generated_utc"),"counts":data.get("counts",{}),
                "coverage":data.get("coverage",[])[:20],
                "log_download_succeeded":data.get("log_download_succeeded", 0),
                "failed_jobs_without_logs":data.get("failed_jobs_without_logs", 0),
                "regression_context":context,"linked_pr_context":pr_context,
                "source_evidence":data.get("source_evidence",{}),
                "cross_day_failure_signatures":data.get("failure_history",{}),
                "clusters":[compact_cluster(c) for c in clusters[:20]],
                "historical_main_comparison_clusters":[compact_cluster(c) for c in historical[:5]],
                "historical_incident_lifecycle": ({
                    "previous_report_date":continuity.get('previous_date'),
                    "new":continuity.get('new', []),
                    "continuing":continuity.get('continuing', []),
                    "reappeared":continuity.get('reappeared', []),
                    "not_observed_not_verified_fixed":continuity.get('not_observed', []),
                    "collection_complete":continuity.get('coverage_complete', False),
                } if continuity else {}),
                "verified_pr_leads":{c.get("stable_id") or c.get("fingerprint"):c.get("pr_leads",[])
                  for c in clusters[:20]}}
    full_evidence_sha=hashlib.sha256(json.dumps({
        'snapshot':data.get('generated_utc'),'jobs':data.get('jobs',[]),
        'comparison_jobs':data.get('comparison_jobs',[]),'source_evidence':data.get('source_evidence',{}),
        'clusters':clusters,'historical':historical,'context':context,'pr_context':pr_context,
        'continuity':continuity},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()

    def record_usage(telemetry, info):
        if 'attempt' in info:
            telemetry.setdefault('attempts',[]).append(dict(info))
        elif telemetry.get('attempts'):
            telemetry['attempts'][-1].update(info)
        telemetry.update(info)

    def invoke(messages, max_tokens=3500, stage="triage"):
        telemetry={"stage":stage,"status":"started"}
        data["glm_telemetry"].append(telemetry)
        # Opt-in content-addressed checkpoint for manual issue refreshes only.
        # A finished visible response is saved immediately, BEFORE synthesis.
        # Cache keys include the full prompt, model, reasoning config and budget;
        # changed PR evidence invalidates the previous answer automatically.
        cache_root = os.getenv('GLM_STAGE_CACHE_DIR', 'reports/analysis/stage-cache').strip()
        cache_path = None
        if cache_root and not cli_command:
            import hashlib
            sig = json.dumps({'prompt_version':4, 'evidence_sha256':full_evidence_sha,
                'endpoint':base_url, 'model': model, 'messages': messages,
                'max_tokens': max_tokens,
                'reasoning_effort': os.getenv('GLM_REASONING_EFFORT', ''),
                'output_cap': os.getenv('GLM_MAX_OUTPUT_TOKENS', '')},
                ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            fingerprint = hashlib.sha256(sig.encode('utf-8')).hexdigest()
            cache_dir = Path(cache_root)
            cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            cache_dir.chmod(0o700)
            cache_path = cache_dir / (fingerprint + '.json')
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding='utf-8'))
                if (cached.get('sha256') == fingerprint and
                        isinstance(cached.get('content'), str) and cached['content'].strip() and
                        not re.search(r'[\u0400-\u04ff]', cached['content'])):
                    progress('GLM stage checkpoint hit: %s (no API call)' % fingerprint[:12])
                    telemetry.update(status='cache_hit',effort=os.getenv('GLM_REASONING_EFFORT','provider-default'),output_cap=max_tokens)
                    return cached['content']
        if cli_command:
            command = shlex.split(cli_command)
            if not command:
                raise ValueError("Empty GLM_ANALYZER_COMMAND")
            completed = subprocess.run(command,
                input=json.dumps({"messages":messages},ensure_ascii=False),
                text=True,capture_output=True,timeout=240,check=True)
            answer = completed.stdout.strip()
        else:
            request_options={}
            if stage=='synthesis':request_options['timeout_seconds']=int(os.getenv('GLM_SYNTHESIS_TIMEOUT_SECONDS','900'))
            answer=chat_completion(base_url,key,model,messages,max_tokens=max_tokens,
                                   on_diagnostic=progress, on_usage=lambda info: record_usage(telemetry,info), **request_options)
        telemetry['status']='completed'
        if not isinstance(answer,str) or not answer.strip():
            raise ValueError('GLM returned empty response')
        if re.search(r'[\u0400-\u04ff]', answer):
            raise ValueError('GLM response is not English-only; refusing mixed-language publication')
        answer = sanitize(answer[:36000])
        if cache_path is not None:
            import uuid
            temp_path = cache_path.with_suffix('.%s.tmp' % uuid.uuid4().hex)
            try:
                # No credentials or private reasoning content are stored here.
                with os.fdopen(os.open(str(temp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                               'w', encoding='utf-8') as stream:
                    json.dump({'sha256': fingerprint, 'content': answer}, stream, ensure_ascii=False)
                os.replace(str(temp_path), str(cache_path))
            finally:
                if temp_path.exists(): temp_path.unlink()
        return answer

    try:
        def skill_text(skill_names):
            bundle=load_skill_bundle(skill_names)
            return 'Pinned skill references (source commit %s):\n'%bundle['source_commit'] + '\n\n'.join(
                '[Skill %s]\n%s'%(s['name'],s['content']) for s in bundle['skills'])
        base_names=('ascend-log-analysis','sglang-regression','ascend-regression')
        # The triage call is always bounded independently of job volume.
        if mode=='triage' or not deep:
            system=SYSTEM_POLICY + '\n' + skill_text(base_names)
            msg=[{'role':'system','content':system},
                 {'role':'user','content':sanitize(bounded_json(evidence,46000))}]
            answer=invoke(msg,max_tokens=triage_budget)
            return answer, ('completed: local agent' if cli_command else ('completed: triage' if mode=='triage' else 'completed: auto/triage'))

        investigations=[]
        data['glm_investigations']=investigations
        def checkpoint_investigations():
            target=os.getenv('GLM_RECOVERY_CONTEXT_FILE','').strip() or (
                'reports/analysis/'+str(data.get('generated_utc','unknown')).replace(':','')+'-context.json')
            import tempfile
            path=Path(target)
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            payload={'generated_utc':data.get('generated_utc'),'data':data,
                     'clusters':clusters,'investigations':investigations,
                     'synthesis_status':data.get('glm_synthesis_status','not started')}
            fd,temp=tempfile.mkstemp(prefix='.glm-context-',dir=path.parent)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(payload,stream,ensure_ascii=False)
                os.replace(temp,path)
            finally:
                if os.path.exists(temp):os.unlink(temp)
        for index,c in enumerate(deep,1):
            skill_names=relevant_skills(c)
            target={'snapshot':data.get('generated_utc'),'counts':data.get('counts',{}),
                'coverage':data.get('coverage',[])[:10],
                'cluster':compact_cluster(c,max_jobs=4),
                'regression_context':next((x for x in context['clusters']
                    if x['cluster']==(c.get('stable_id') or c.get('fingerprint'))),{}),
                'linked_pr_context':pr_context, 'verified_pr_leads':c.get('pr_leads',[])}
            prompt=SYSTEM_POLICY+'\nInvestigate ONLY this failure cluster; make no claims about other clusters. '
            prompt+='Give suspected component ownership, ranked mechanistic hypotheses, a falsification step for each, the smallest decisive experiment, and exact verified PR URLs (if provided); never assert causation from chronology alone. Respond in English only.\n'
            prompt+=skill_text(skill_names)
            progress('GLM investigation %d/%d: %s, skills=%s'%(index,len(deep),
                        c.get('stable_id',c.get('fingerprint','?')),','.join(skill_names)))
            try:
                content=invoke([{'role':'system','content':prompt},
                                {'role':'user','content':sanitize(bounded_json(target,31000))}],max_tokens=cluster_budget,stage='investigate:'+str(c.get('stable_id') or c.get('fingerprint')))
                investigations.append({'id':c.get('stable_id') or c.get('fingerprint'),'analysis':content})
            except Exception as e:
                progress('WARNING: GLM cluster investigation failed: %s%s'%(type(e).__name__, (' — '+sanitize(str(e))[:250]) if type(e).__name__=='GLMBudgetExceeded' else ''))
                investigations.append({'id':c.get('stable_id') or c.get('fingerprint'),'failed':True,
                    'analysis':'Cluster-specific GLM analysis unavailable: %s.'%type(e).__name__ +
                    (' ('+sanitize(str(e))[:240]+')' if isinstance(e,GLMEmptyResponseError) else '')})
            checkpoint_investigations()
        failed_investigations=[x for x in investigations if x.get('failed')]
        if len(failed_investigations)==len(investigations):
            details='; '.join(x['id']+': '+x['analysis'] for x in investigations)
            return ('All GLM cluster investigations failed; synthesis was skipped to avoid an ungrounded, paid request.\n\n'+details)[:26000], 'failed'
        digest_input={'all_evidence':evidence,'investigations':investigations}
        progress('GLM synthesis: %d cluster investigations; %d total clusters'%(len(investigations),len(clusters)))
        system=SYSTEM_POLICY+'\nSynthesize the complete daily report. Do NOT omit uninvestigated clusters; distinguish fully investigated from triaged-only clusters. '
        system+='Do not upgrade uncertain hypotheses to facts. Use ranked ordered actions.\n'+skill_text(base_names)
        data['glm_synthesis_status']='started'
        checkpoint_investigations()
        try:
            answer=invoke([{'role':'system','content':system},
              {'role':'user','content':sanitize(bounded_json(digest_input,51000))}],max_tokens=synthesis_budget,stage='synthesis')
            data['glm_synthesis_status']='completed'
            checkpoint_investigations()
            return answer, ('partial: '+mode if failed_investigations else 'completed: '+mode)
        except Exception as e:
            data['glm_synthesis_status']='failed: '+type(e).__name__
            checkpoint_investigations()
            progress('WARNING: GLM synthesis failed: %s%s; keeping successful per-cluster investigations'%(type(e).__name__, (' — '+sanitize(str(e))[:250]) if type(e).__name__=='GLMBudgetExceeded' else ''))
            partial='\n\n'.join('### %s\n%s'%(x['id'],x['analysis']) for x in investigations)
            return ('GLM synthesis failed (%s); partial per-cluster analysis follows.\n\n'%type(e).__name__+partial)[:26000], 'partial: '+mode
    except Exception as exc:
        # Don't accidentally reveal API keys or signed URLs in exception text.
        return ('GLM investigation failed (%s: %s). Deterministic evidence snapshot remains available.'%
                (type(exc).__name__,sanitize(str(exc))[:180])), 'failed'


def load_history(gh: GitHub, date: str, dry_run: bool) -> list[dict]:
    if dry_run:
        return []
    history = []
    for issue in get_existing_issues(gh, DAILY_LABEL):
        match = re.search(r"<!-- npu-diffusion-metrics: (\{[^\n]*\}) -->", issue.get("body") or "")
        if match:
            try:
                item = json.loads(match.group(1))
                if 0 <= (datetime.fromisoformat(date).date() - datetime.fromisoformat(item["date"]).date()).days <= 7:
                    history.append(item)
            except (KeyError, ValueError, TypeError):
                continue
    return sorted(history, key=lambda item: item["date"], reverse=True)


def render_legacy_report(data: dict, clusters: list[dict], analysis: str, glm_status: str, history: list[dict] | None = None) -> str:
    msktime = parse_timestamp(data["generated_utc"]).astimezone(ZoneInfo("Europe/Moscow"))
    date = msktime.strftime("%Y-%m-%d")
    counts = Counter(data["counts"])
    meta = {"date": date, "counts": data["counts"], "clusters": len(clusters),
            "jobs_inspected": len(data["jobs"]), "coverage_capped": any("CAPPED" in s for s in data["coverage"])}
    lines = [f"<!-- npu-diffusion-report-date:{date} -->",
             f"<!-- npu-diffusion-metrics: {json.dumps(meta, separators=(',', ':'))} -->",
             f"# NPU Diffusion CI — Daily Report — {date}", "",
             f"**Snapshot:** {data['generated_utc']} UTC / {msktime:%Y-%m-%d %H:%M} MSK  ",
             f"**Source:** [sgl-project/sglang Actions](https://github.com/{SOURCE_REPO}/actions)  ",
             f"**Window:** last {data['lookback_hours']} hours (run created time, not test completion time)  ",
             f"**Scope:** explicitly named Ascend diffusion jobs only; PR, Nightly, Full, diffusion GT generation workflows.  ",
             f"**GLM post-analysis:** {glm_status}  ",
             f"**Investigation policy:** evidence-backed hypotheses; pinned local Ascend/SGLang skills; no automated fixes.", "",
             "## TL;DR",
             f"Observed {sum(counts.values())} diffusion jobs: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) + ".",
             f"Distinct evidence-backed failure signatures: {len(clusters)}.",
             (f"Failed testcase observations: {sum(c['count'] for c in clusters)} across "
              f"{len({j.get('job_id') for c in clusters for j in c['jobs']})} distinct failed jobs with classified evidence. "
              "One job can contain multiple failed testcases; cluster counts are NOT additive job totals."),
             "Do not interpret an entire workflow's failed status as diffusion regression: skipped, cancelled, and pending jobs are counted separately.",
             "", "## Engineering investigation — hypotheses, not verified causes", "", analysis,
             "", "## Collection coverage and limitations"]
    lines.extend([f"- {c}" for c in data["coverage"]])
    lines += [f"- Log downloads: {data.get('log_download_succeeded', 0)} succeeded / "
              f"{data['log_analysis_attempted']} attempted; "
              f"{data.get('log_download_failed', len(data['errors']))} failed; "
              f"failed jobs beyond log budget: {data['failed_jobs_without_logs']}; "
              f"truncated raw logs: {data.get('log_truncated', 0)}." ]
    lines += [f"- {x}" for x in data["errors"]]
    lines += ["", "## Relevant NPU diffusion jobs", "",
              "| Workflow | Job | Conclusion | Run / SHA |", "|---|---|---|---|"]
    for job in sorted(data["jobs"], key=lambda j: j.get("created_at") or "", reverse=True)[:65]:
        title = job["name"].replace("|", "\\|")[:90]
        runsha = (job.get("head_sha") or "unknown")[:10]
        lines.append(f"| {job['workflow']} | [{title}]({job['url']}) | {job['conclusion']} | {job['run_id']} / `{runsha}` |")
    if len(data["jobs"]) > 65:
        lines.append(f"\n*Table truncated at 65 jobs; full machine-readable artifact includes all {len(data['jobs'])} inspected diffusion jobs.*")
    lines.extend(["", "## Seven-day report-to-report trend", ""])
    if history:
        lines += ["| Date (MSK) | Inspected | Failed | Succeeded | Skipped | Cancelled | Clusters | Coverage |",
                  "|---|---:|---:|---:|---:|---:|---:|---|"]
        for item in [meta, *[h for h in history if h.get("date") != date]][:7]:
            counter = item.get("counts", {})
            lines.append(f"| {item['date']} | {item.get('jobs_inspected', '?')} | {sum(counter.get(k, 0) for k in LIVE_CONCLUSIONS)} | {counter.get('success', 0)} | {counter.get('skipped', 0)} | {counter.get('cancelled', 0)} | {item.get('clusters', '?')} | {'capped' if item.get('coverage_capped') else 'query window'} |")
        lines.append("*Trend compares observed snapshots, not a complete seven-day census; no unsampled dates are inferred.*")
    else:
        lines.append("No previous daily Issue snapshots with machine-readable metrics yet; a trend will build as reports are published.")
    lines.extend(["", "## Deduplicated failure clusters", ""])
    if not clusters:
        if any(counts.get(k, 0) for k in LIVE_CONCLUSIONS) and not data.get('log_download_succeeded', 0):
            lines.append("**Unclassified failures:** Failed jobs exist, but none of their logs were downloaded successfully. "
                         "Zero clusters does NOT mean zero regressions. Repair log retrieval before assessing root causes.")
        else:
            lines.append("No distinct failures with retrievable signatures in this snapshot. This is not proof that all upstream diffusion tests passed.")
    for c in clusters:
        url = c.get("issue_url")
        title = f"[{c['stable_id']}]({url})" if url else c.get("stable_id", "Not assigned")
        lines += [f"### {title} — {c['count']} failed job(s)",
                  f"- **Observed fact:** `{sanitize(c['signature'])[:300]}`",
                  f"- **First/last seen in this snapshot:** {c['first_seen_snapshot']} / {c['last_seen_snapshot']}",
                  "- **Root cause:** unverified; do not infer causation from proximity alone."]
        for job in c["jobs"][:8]:
            metric = job.get("metrics") or {}
            thresholds = job.get("thresholds") or {}
            checks = job.get("failed_checks") or []
            lines.append(f"- [Job: {job['name']}]({job['url']})"
                         f" — category={job.get('failure_category', 'unknown')}"
                         + (f", testcase={job['primary_test_case']}" if job.get('primary_test_case') else "")
                         + (f", failed_checks={','.join(checks)}" if checks else ""))
            if metric:
                lines.append(f"  - **Observed metrics:** {sanitize(json.dumps(metric, sort_keys=True))[:350]}")
            if thresholds:
                lines.append(f"  - **Configured thresholds:** {sanitize(json.dumps(thresholds, sort_keys=True))[:350]}")
        if c.get("not_published_reason"):
            lines.append(f"- **Issue creation skipped:** {c['not_published_reason']}")
    lines += ["", "## Historical reference IDs", "",
              "- NPU-D001: MiniMax-H3 E2E / Cache-DiT (historical; do not call active without current logs).",
              "- NPU-D002: GLM-Image autoregressive HTTP 400 (historical).",
              "- NPU-D003: JoyAI asset timeout (historical).",
              "", "## Evidence policy", "",
              "- Only completed failed diffusion jobs with retrieved logs are clustered; cancellation, skipping, and in-progress are not test failures.",
              "- `first_seen_snapshot` does not establish the historical first occurrence or last green run.",
              "- A status change from failed to fixed requires a relevant confirmed passing rerun, not absence from today's sample.",
              "- Seven-day historical trend and baseline comparison are intentionally not fabricated when full historical job-level data are unavailable."]
    text = "\n".join(lines)
    if len(text) > MAX_ISSUE_BODY:
        text = text[:MAX_ISSUE_BODY - 300] + "\n\n*Issue body truncated at GitHub limit. See workflow artifact for complete machine-readable evidence.*\n"
    return text


def render_report(data: dict, clusters: list[dict], analysis: str, glm_status: str,
                  history: list[dict] | None = None, continuity=None) -> str:
    """Human-first daily Issue by default; legacy verbose format remains opt-in."""
    fmt=os.getenv('REPORT_FORMAT', 'research').strip().lower()
    if fmt == 'verbose':
        return render_legacy_report(data, clusters, analysis, glm_status, history)
    if fmt == 'research':
        try:
            from scripts.research_report import render_research_report
        except ModuleNotFoundError:
            from research_report import render_research_report
        return render_research_report(data, clusters, analysis, glm_status, history, continuity,
            include_deep=os.getenv('REPORT_INCLUDE_DEEP_DETAILS','0')=='1')
    try:
        from scripts.report_editor import render_human_report
    except ModuleNotFoundError:
        from report_editor import render_human_report
    return render_human_report(data, clusters, analysis, glm_status, history,
        include_deep=os.getenv('REPORT_INCLUDE_DEEP_DETAILS', '0') == '1', continuity=continuity)


def update_cluster_issue_details(gh, clusters, glm_analysis_text, glm_status, strict=False):
    """Publish bounded English-only cluster RCA + verified PR leads.

    Preserve the fingerprint mapping, never overwrite a mismatched Issue.
    Server-side backups of prior bodies are made before edits.
    """
    if os.getenv('REPORT_UPDATE_CLUSTER_DETAILS','1') != '1':
        return
    try:
        from scripts.report_editor import vetted_cluster_section, fix_candidates, compact_checks, utc_msk, testcase, _linked_prs
    except ModuleNotFoundError:
        from report_editor import vetted_cluster_section, fix_candidates, compact_checks, utc_msk, testcase, _linked_prs
    backups=Path('reports/issue-backups')
    for c in clusters[:8]:
        if c.get('incident_scope') == 'not-merged' and os.getenv('CREATE_PR_ONLY_ISSUES','0') != '1':
            continue
        cid, link = c.get('stable_id', ''), c.get('issue_url','')
        if not re.fullmatch(r'NPU-D\d{3}',cid) or not re.fullmatch(
                r'https://github\.com/'+re.escape(TARGET_REPO)+r'/issues/\d+',str(link)):
            continue
        number=int(link.rsplit('/',1)[1])
        try:
            issue=gh.get('/repos/%s/issues/%d'%(TARGET_REPO,number))
            original=issue.get('body') or ''
            marker='<!-- npu-diffusion-fingerprint:%s -->'%c['fingerprint']
            if marker not in original:
                progress('WARNING: fingerprint mismatch in cluster Issue %d; skipped'%number)
                continue
            backups.mkdir(parents=True,exist_ok=True,mode=0o700)
            backups.chmod(0o700)
            path=backups/('issue-%d-before-v17-%s.md'%(number,datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')))
            path.write_text(original,encoding='utf-8');path.chmod(0o600)
            sample=(c.get('jobs') or [{}])[0]
            try:
                from scripts.issue_taxonomy import classify_cluster
            except ModuleNotFoundError:
                from issue_taxonomy import classify_cluster
            scope_counts=classify_cluster(c)
            lines=[marker,'<!-- npu-ci-report-language:en -->',
                '# %s — NPU Diffusion CI incident'%cid,'',
                '**Observed signature:** `%s`'%sanitize(c.get('signature',''))[:230],
                '**Failure observations:** %d (%d main, %d PR, %d unknown)'%(
                    c.get('count',0),scope_counts['main_jobs'],scope_counts['pr_jobs'],scope_counts['unknown_jobs']),
                '**Earliest observed failing run:** %s (run creation; not a verified regression onset)'%utc_msk(c.get('first_seen_snapshot')),
                '**Root cause:** unverified; no verified last-green/first-bad testcase boundary.','',
                '## Evidence','',
                '- **Failed checks:** %s'%compact_checks(sample),
                '- **Representative run:** [job %s](%s)'%(sample.get('job_id','?'),sample.get('url','https://github.com/sgl-project/sglang/actions')),'',
                '## Candidate PRs (unverified)','']
            leads=_linked_prs(c,limit=5)
            if leads: lines.extend('- '+x for x in leads)
            else:lines.append('- No PR passed GitHub status, timing and relevant-file checks.')
            lines+=['','**Interpretation:** tested branch is not an identified culprit; a merged-before-failure PR is a temporal suspect only; an open PR is not a verified fix.','',
              '## Candidate fixes and verification','']
            for title,action in fix_candidates(c)[:3]: lines.append('- **%s:** %s'%(title,action))
            snippet=vetted_cluster_section(glm_analysis_text,c)
            if glm_status.startswith(('completed','partial')) and snippet and not re.search(r'[\u0400-\u04ff]',snippet):
                lines+=['','## GLM investigation (unverified)','',sanitize(snippet)[:7000]]
            else:
                lines+=['','## GLM investigation','',
                         'No verified English-language cluster-specific analysis available.']
            lines+=['','## Closure rule','',
                'Do not close as fixed without a relevant passing testcase rerun at a known SHA and controlled configuration.']
            # Preserve an explicitly demarcated user-managed appendix, if present.
            if '<!-- npu-ci-user-notes -->' in original:
                lines+=['','<!-- npu-ci-user-notes -->'+original.split('<!-- npu-ci-user-notes -->',1)[1][:15000]]
            body='\n'.join(lines)[:MAX_ISSUE_BODY]
            gh.issue(number,{'body':body, 'title':'[%s] %s'%(cid, _incident_title(c))})
        except Exception as exc:
            progress('WARNING: cluster Issue detail update failed for %s: %s'%(cid,type(exc).__name__))
            if strict:raise


def post_daily(gh: GitHub, report: str, date: str, dry_run: bool) -> str:
    if len(report)>MAX_ISSUE_BODY:
        raise ValueError('Concise Issue exceeds publication limit; complete Markdown retained locally')
    if dry_run:
        return "DRY RUN — not posted"
    ensure_label(gh, DAILY_LABEL, "0969da", "Daily Ascend NPU diffusion CI report")
    issues = get_existing_issues(gh, DAILY_LABEL)
    marker = f"<!-- npu-diffusion-report-date:{date} -->"
    matched = next((issue for issue in issues if marker in (issue.get("body") or "")), None)
    if matched:
        gh.issue(matched["number"], {"body": report, "title": f"[NPU Diffusion CI] Daily Report — {date}"})
        return matched["html_url"]
    issue = gh.create_issue({"title": f"[NPU Diffusion CI] Daily Report — {date}",
                             "body": report, "labels": [DAILY_LABEL]})
    return issue["html_url"]


def daily_issue_exists(gh: GitHub, date: str) -> str | None:
    """Return the matching daily report URL, if a publisher already finished today.

    This is a best-effort preflight for the server/fallback schedule; it does
    not provide a transactional lock if two publishers execute concurrently.
    """
    marker = f"<!-- npu-diffusion-report-date:{date} -->"
    for issue in get_existing_issues(gh, DAILY_LABEL):
        if marker in (issue.get("body") or ""):
            return issue.get("html_url")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--max-runs-per-workflow", type=int, default=None,
                        help="Optional per-workflow cap for a fast smoke test; "
                             "default comes from MAX_RUNS_PER_WORKFLOW")
    parser.add_argument("--max-job-logs", type=int, default=None,
                        help="Maximum failed job logs to download/analyze (default MAX_JOB_LOGS)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-if-published-today", action="store_true",
                        help="Exit before collecting logs or calling GLM if a daily Issue already exists")
    parser.add_argument("--glm-mode", choices=("auto", "triage", "investigate"),
                        default=None, help="Override GLM_ANALYSIS_MODE for this run")
    parser.add_argument("--no-glm", action="store_true",
                        help="Disable LLM/API post-analysis for this invocation")
    args = parser.parse_args(argv)
    if not 1 <= args.lookback_hours <= 168:
        parser.error("--lookback-hours must be 1..168")
    if args.max_runs_per_workflow is not None and not 1 <= args.max_runs_per_workflow <= 5000:
        parser.error("--max-runs-per-workflow must be 1..5000")
    if args.max_job_logs is not None and not 0 <= args.max_job_logs <= 500:
        parser.error("--max-job-logs must be 0..500")
    if not TARGET_REPO and not args.dry_run:
        parser.error("TARGET_REPOSITORY or GITHUB_REPOSITORY required")
    now = utc_now()
    date = now.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
    # Publication and Issue reads always go DIRECT to api.github.com with PAT.
    gh = GitHub(os.getenv("GITHUB_TOKEN"))
    # This service uses GitHub's REST API, NOT git clone. Some GitHub
    # accelerators, including gh-proxy.test.osinfra.cn, serve git/raw assets
    # but return 403 for api.github.com. For safety and reliability REST API
    # requests are always direct and authenticated; no PAT goes via a
    # third-party URL-prefix proxy. Keep backwards compatibility with v4 env.
    if os.getenv("GITHUB_PUBLIC_READ_PROXY", "").strip():
        print("WARNING: GITHUB_PUBLIC_READ_PROXY is deprecated and ignored for REST API. "
              "GitHub download/clone proxies do not necessarily support api.github.com. "
              "Use direct HTTPS for authenticated API requests.", file=sys.stderr)
    # Optional separate source token, otherwise reuse the bot token. The
    # source repository is public, so credentials are not strictly required
    # for reads, but a token reduces anonymous rate-limiting.
    source_gh = GitHub(os.getenv("SOURCE_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN"))
    try:
        if args.skip_if_published_today and not args.dry_run:
            previous = daily_issue_exists(gh, date)
            if previous:
                print(f"Already published for {date} MSK: {previous}; skipping collection and GLM")
                return 0
        progress(f"Collection started: source={SOURCE_REPO}, target={TARGET_REPO}, "
                 f"lookback={args.lookback_hours}h, no_glm={args.no_glm}, dry_run={args.dry_run}")
        data = snapshot(source_gh, now, args.lookback_hours,
                        args.max_runs_per_workflow or int(os.getenv("MAX_RUNS_PER_WORKFLOW", "320")),
                        args.max_job_logs if args.max_job_logs is not None else
                        int(os.getenv("MAX_JOB_LOGS", "20")))
        clusters = clusters_from_snapshot(data)
        historical=clusters_from_snapshot({'jobs':[j for j in data.get('comparison_jobs',[])
            if j.get('run_id')==data.get('latest_main_run_id')]})
        for c in historical:
            c.update(stable_id='OBS-'+c['fingerprint'],comparison_only=True)
        data['historical_main_clusters']=historical
        try:
            from scripts.issue_taxonomy import ordered_clusters
        except ModuleNotFoundError:
            from issue_taxonomy import ordered_clusters
        clusters = ordered_clusters(clusters)
        try:
            from scripts.failure_history import correlate_snapshots
        except ImportError:
            from failure_history import correlate_snapshots
        data["failure_history"] = correlate_snapshots(data, clusters, Path("reports"))
        # Writing machine-readable evidence comes first; a failed publisher must
        # not cause raw findings to disappear.
        Path("reports").mkdir(exist_ok=True)
        # Old raw source logs are confidential and never attached to GitHub.
        # Expire by file mtime. 0 disables automatic deletion (not recommended).
        retention = max(0, int(os.getenv("RAW_LOG_RETENTION_DAYS", "7")))
        raw_root = Path("reports/raw-logs")
        if retention and raw_root.exists():
            cutoff = time.time() - retention * 86400
            for previous in raw_root.glob("*/job-*.log"):
                if previous.is_file() and previous.stat().st_mtime < cutoff:
                    previous.unlink()
            # Bound local raw-log disk retention independently of age.
            # Oldest files are evicted first; derived JSON/Issues are unaffected.
            disk_budget = max(128, int(os.getenv("RAW_LOG_DISK_BUDGET_MB", "2048"))) * 1024 * 1024
            stored = [f for f in raw_root.glob("*/job-*.log") if f.is_file()]
            total_bytes = sum(f.stat().st_size for f in stored)
            for old_log in sorted(stored, key=lambda f: f.stat().st_mtime):
                if total_bytes <= disk_budget:
                    break
                size = old_log.stat().st_size
                old_log.unlink()
                total_bytes -= size
            for folder in raw_root.iterdir():
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
        snapshot_text = json.dumps(data, indent=2, ensure_ascii=False)
        (Path("reports") / f"npu-diffusion-ci-{date}.json").write_text(snapshot_text, encoding="utf-8")
        # Keep bounded intra-day snapshots: a subsequent refresh must not erase
        # all previous context for identifying recurring failures.
        snapshot_dir = Path("reports/snapshots")
        snapshot_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        snapshot_dir.chmod(0o700)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        saved = snapshot_dir / f"npu-diffusion-ci-{stamp}.json"
        saved.write_text(snapshot_text, encoding="utf-8")
        saved.chmod(0o600)
        old_snapshots = sorted(snapshot_dir.glob("npu-diffusion-ci-*.json"), reverse=True)
        for old in old_snapshots[18:]:
            old.unlink()
        assign_cluster_ids(gh, clusters, args.dry_run,
                           int(os.getenv('MAX_NEW_CLUSTER_ISSUES','3')), publish=False)
        history = load_history(gh, date, args.dry_run)
        continuity = None
        if not args.dry_run:
            try:
                from scripts.incident_lifecycle import build_continuity
            except ModuleNotFoundError:
                from incident_lifecycle import build_continuity
            prior_incidents = get_existing_issues(gh, CLUSTER_LABEL)
            prior_daily = get_existing_issues(gh, DAILY_LABEL)
            continuity = build_continuity(clusters, prior_incidents, prior_daily, date, data)
        proofs = {}
        if not args.dry_run and continuity and os.getenv('INCIDENT_VERIFY_PASSES', '1') == '1':
            try:
                from scripts.incident_verifier import verify_resolution
                from scripts.incident_lifecycle import mark_verified_continuity
            except ImportError:
                from incident_verifier import verify_resolution
                from incident_lifecycle import mark_verified_continuity
            try:
                histories = ([Path('reports') / ('npu-diffusion-ci-'+old+'.json')
                             for old in sorted({str(x.get('body','')).split('<!-- npu-diffusion-report-date:',1)[-1][:10]
                                                for x in prior_daily if '<!-- npu-diffusion-report-date:' in str(x.get('body',''))})]
                             + sorted(Path('reports/snapshots').glob('npu-diffusion-ci-*.json'),reverse=True)[:18])
                proofs = verify_resolution(source_gh, data, prior_incidents, continuity, histories,
                    limit=int(os.getenv('INCIDENT_MAX_PASS_LOGS','24')),
                    allow_pr=os.getenv('INCIDENT_AUTO_CLOSE_PR','0')=='1')
                mark_verified_continuity(continuity, proofs)
                progress('Testcase verification: %d verified recoveries; %d missing incidents' %
                    (len(proofs),len(continuity['not_observed'])))
            except Exception as exc:
                progress('WARNING: testcase verification unavailable; NO incident closures (%s)' % type(exc).__name__)
                proofs = {}
        if os.getenv('PR_SEARCH_ENABLED','1')=='1':
            try:
                try:
                    from scripts.pr_forensics import discover_pr_leads, attach_pr_leads
                except ImportError:
                    from pr_forensics import discover_pr_leads, attach_pr_leads
                for c in clusters+historical:c['analysis_as_of']=data.get('generated_utc')
                attach_pr_leads(clusters+historical,discover_pr_leads(source_gh,clusters+historical,SOURCE_REPO,
                    max_per_cluster=int(os.getenv('PR_SEARCH_MAX_PER_CLUSTER','3')),
                    max_api_calls=int(os.getenv('PR_SEARCH_MAX_API_CALLS','38'))))
                data['pr_evidence_collected']=True
            except (RuntimeError,ValueError,TypeError,OSError) as exc:
                data.setdefault('errors',[]).append('PR evidence unavailable: '+type(exc).__name__)
        if args.no_glm:
            analysis, status = "Disabled for this run (--no-glm).", "disabled"
        else:
            analysis, status = glm_analysis(data, clusters, mode=args.glm_mode, gh=source_gh, continuity=continuity)
        if os.getenv("GLM_BUDGET_ENABLED", "1") == "1" and not args.no_glm:
            try:
                try:
                    from scripts.glm_budget import summary as budget_summary
                except ImportError:
                    from glm_budget import summary as budget_summary
                usage = budget_summary()
                progress("GLM local budget: requests=%d, reported_cost_estimate_CNY=%s, "
                         "unconfirmed_reserved_CNY=%s, remaining_local_estimate_CNY=%s" % (
                            usage['requests'], usage['reported_cny_estimate'],
                            usage['uncertain_reserved_cny'],usage['remaining_local_estimate_cny']))
            except (OSError, ValueError, RuntimeError) as e:
                progress('WARNING: GLM local budget summary unavailable: %s' % type(e).__name__)
        try:
            from scripts.analysis_evidence import coverage_matrix, load_coverage_history
            from scripts.ci_investigation import load_saved_history, build_regression_context
        except ImportError:
            from analysis_evidence import coverage_matrix, load_coverage_history
            from ci_investigation import load_saved_history, build_regression_context
        if not data.get('regression_context'):
            data['regression_context']=build_regression_context(data,clusters+historical,load_saved_history(data))
        data['pr_evidence']={c['fingerprint']:c.get('pr_leads',[]) for c in clusters}
        data['coverage_matrix']=coverage_matrix(data,load_coverage_history(data))
        for evidence_path in [Path('reports') / f'npu-diffusion-ci-{date}.json', saved]:
            evidence_path.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
            evidence_path.chmod(0o600)
        # Preserve the full paid analysis and raw evidence locally even though
        # the daily GitHub Issue is deliberately concise.
        full_root = Path('reports/analysis')
        full_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        full_root.chmod(0o700)
        full_path = full_root / f'npu-diffusion-ci-{date}-full.md'
        try:
            from scripts.analysis_recovery import save_analysis
        except ImportError:
            from analysis_recovery import save_analysis
        save_analysis(full_path,data,analysis,status)
        try:
            from scripts.complete_report import render_complete_report, validate_report
        except ImportError:
            from complete_report import render_complete_report, validate_report
        validate_report(data,render_complete_report(data,clusters,analysis,status,history,continuity))
        # Do not create Issues until after deterministic classification AND
        # paid analysis. Non-published observations retain local OBS IDs only.
        if not args.dry_run:
            assign_cluster_ids(gh, clusters, False,
                               int(os.getenv('MAX_NEW_CLUSTER_ISSUES','3')),publish=True)
            if continuity:
                continuity = build_continuity(clusters, prior_incidents, prior_daily, date, data)
            update_cluster_issue_details(gh, clusters, analysis, status)
        report = render_report(data, clusters, analysis, status, history, continuity)
        try:
            from scripts.complete_report import render_complete_report, validate_report
        except ImportError:
            from complete_report import render_complete_report, validate_report
        complete_report=render_complete_report(data,clusters,analysis,status,history,continuity)
        validate_report(data,complete_report)
        outfile = Path("reports") / f"npu-diffusion-ci-{date}.md"
        outfile.write_text(complete_report, encoding="utf-8")
        outfile.chmod(0o600)
        issuefile=outfile.with_name(outfile.stem+'-issue.md')
        issuefile.write_text(report,encoding='utf-8')
        issuefile.chmod(0o600)
        url = post_daily(gh, report, date, args.dry_run)
        # Continuity updates happen only after successful daily publication.
        # Never close an incident merely because the current window missed it.
        if not args.dry_run and continuity:
            try:
                from scripts.incident_lifecycle import (update_unobserved_issues,
                    archive_previous_daily_reports)
            except ModuleNotFoundError:
                from incident_lifecycle import (update_unobserved_issues,
                    archive_previous_daily_reports)
            # Never close Issues until the daily report was committed; verify
            # the latest server-side body/fingerprint again before patching.
            if proofs and os.getenv('INCIDENT_AUTO_CLOSE','1') == '1':
                try:
                    from scripts.incident_verifier import apply_verified_resolutions
                except ImportError:
                    from incident_verifier import apply_verified_resolutions
                closed = apply_verified_resolutions(gh, proofs, prior_incidents)
                if closed: progress('Verified testcase incident Issues closed: %s' % closed)
                # Do not leave an unclosed Issue marked fixed when writes fail.
                continuity['verified_proofs']=closed
                if set(closed) != set(proofs):
                    progress('WARNING: some verified Issues could not be closed; manual review required')
            try:
                update_unobserved_issues(gh, continuity,
                    [x for x in prior_incidents if not any(
                        (y.get('name') if isinstance(y,dict) else str(y)) == 'not-merged'
                        for y in x.get('labels',[]))], TARGET_REPO)
            except Exception as exc:
                progress('WARNING: incomplete incident lifecycle annotation: %s' % type(exc).__name__)
            if os.getenv('ARCHIVE_PREVIOUS_DAILY_ISSUES','1') == '1':
                try:
                    archived=archive_previous_daily_reports(gh, prior_daily, date)
                    if archived: progress('Archived previous daily report Issues: %s' % archived)
                except Exception as exc:
                    progress('WARNING: previous daily report archival failed: %s' % type(exc).__name__)
        print(f"Daily report: {url}; jobs={len(data['jobs'])}; clusters={len(clusters)}; GLM={status}; file={outfile}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
