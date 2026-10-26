#!/usr/bin/env python3
"""Read-only DNS, HTTPS, workflow-run and job-API preflight for openEuler.

Never writes Issues or exposes tokens; can run as the unprivileged service user.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

HOST = 'api.github.com'
BASE = 'https://api.github.com'
SOURCE = os.environ.get('SOURCE_REPOSITORY', 'sgl-project/sglang')


def check_dns(host: str = HOST) -> list[str]:
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addrs = sorted({entry[4][0] for entry in answers})
    if not addrs:
        raise OSError('DNS returned no addresses')
    print(f'[PREFLIGHT] DNS {host}: {", ".join(addrs)}', flush=True)
    return addrs


def fetch_json(path: str, *, timeout: int = 15, attempts: int = 3) -> dict:
    # Allow this preflight to use the same GitHub PAT as the real collector, but
    # ONLY to api.github.com, and never print it (or send it to a third party).
    headers = {'User-Agent': 'sglang-npu-ci-preflight', 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    token = os.environ.get('SOURCE_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(BASE + path, headers=headers)
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                obj = json.load(response)
            if not isinstance(obj, dict):
                raise ValueError('GitHub API response is not a JSON object')
            return obj
        except urllib.error.HTTPError as exc:
            # HTTP errors have a valid HTTP response, and subclass URLError.
            if exc.code in (429, 500, 502, 503, 504) and i + 1 < attempts:
                print(f'[PREFLIGHT] HTTP {exc.code}, retry {i + 1}/{attempts - 1}', flush=True)
                time.sleep(min(4, 2 ** i))
                continue
            raise RuntimeError(f'GitHub API HTTP {exc.code} on {path}; check token, access and rate limits') from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if i + 1 == attempts:
                raise RuntimeError(f'GitHub API transport failed on {path}: '
                                   f'{type(exc).__name__}; DNS/HTTPS unavailable after {attempts} attempts') from exc
            print(f'[PREFLIGHT] Temporary network failure; retry {i + 1}/{attempts - 1}', flush=True)
            time.sleep(min(4, 2 ** i))
    raise RuntimeError('Unexpected exhausted preflight retries')


def verify(sample_jobs: bool = False, *, timeout: int = 15, attempts: int = 3) -> int:
    check_dns()
    repo = fetch_json(f'/repos/{SOURCE}', timeout=timeout, attempts=attempts)
    if repo.get('full_name') != SOURCE:
        raise ValueError('GitHub returned an unexpected repository')
    print(f'[PREFLIGHT] GitHub API HTTPS: OK ({SOURCE})', flush=True)
    if sample_jobs:
        wf = quote('pr-test-npu.yml', safe='')
        runs = fetch_json(f'/repos/{SOURCE}/actions/workflows/{wf}/runs?per_page=1',
                          timeout=timeout, attempts=attempts)
        items = runs.get('workflow_runs')
        if not isinstance(items, list) or not items:
            raise ValueError('No workflow runs available in sample; check Actions endpoint')
        run = items[0]
        rid = int(run['id'])
        attempt = int(run.get('run_attempt') or 1)
        jobs = fetch_json(f'/repos/{SOURCE}/actions/runs/{rid}/attempts/{attempt}/jobs?per_page=100',
                          timeout=timeout, attempts=attempts)
        if not isinstance(jobs.get('jobs'), list) or not isinstance(jobs.get('total_count'), int):
            raise ValueError('Malformed GitHub jobs response; expected {total_count, jobs}')
        print(f'[PREFLIGHT] Actions jobs: OK (run={rid}, jobs={jobs["total_count"]})', flush=True)
    print('[PREFLIGHT] PASS', flush=True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sample-jobs', action='store_true', help='Also verify a real upstream jobs API response')
    p.add_argument('--dns-only', action='store_true', help='Only check DNS resolution (without making HTTP requests)')
    p.add_argument('--timeout', type=int, default=15)
    p.add_argument('--attempts', type=int, default=3)
    args = p.parse_args(argv)
    if args.timeout <= 0 or not 1 <= args.attempts <= 10:
        p.error('--timeout must be positive and --attempts must be 1..10')
    try:
        if args.dns_only:
            check_dns()
            return 0
        return verify(sample_jobs=args.sample_jobs, timeout=args.timeout, attempts=args.attempts)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'[PREFLIGHT] FAILED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
