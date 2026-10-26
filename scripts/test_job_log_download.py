#!/usr/bin/env python3
"""Read and classify a single upstream job log, no Issues or GLM API requests."""
import argparse
import os
import tempfile
from pathlib import Path
try:
    from scripts.log_context import analyze_file
    from scripts.npu_diffusion_ci_report import GitHub, SOURCE_REPO, sanitize
except ModuleNotFoundError:
    from log_context import analyze_file
    from npu_diffusion_ci_report import GitHub, SOURCE_REPO, sanitize


def main():
    parser = argparse.ArgumentParser(description='Read-only streaming GH job log test')
    parser.add_argument('job_id', type=int)
    args = parser.parse_args()
    gh = GitHub(os.getenv('SOURCE_GITHUB_TOKEN') or os.getenv('GITHUB_TOKEN'))
    print(f'[LOG-SMOKE] Source={SOURCE_REPO}, job={args.job_id}', flush=True)
    with tempfile.TemporaryDirectory(prefix='npu-ci-log-') as folder:
        raw = Path(folder)/f'job-{args.job_id}.log'
        transfer = gh.request('GET', f'/repos/{SOURCE_REPO}/actions/jobs/{args.job_id}/logs', stream_to=raw)
        if not transfer['downloaded_bytes']:
            raise RuntimeError('Empty log; unable to verify reducer')
        packet = analyze_file(raw)
        top = packet['primary']
        if not top:
            raise RuntimeError('Downloaded log, but no failure signatures were identified')
        print(f"[LOG-SMOKE] PASS: {transfer['downloaded_bytes']} bytes, {packet['total_lines']} lines, "
              f"{packet['unique_signatures']} signatures; truncated={transfer['truncated']}", flush=True)
        print('[LOG-SMOKE] Category:', top['category'], flush=True)
        print('[LOG-SMOKE] Signature:', sanitize(top['signature'])[:250], flush=True)
        print('[LOG-SMOKE] Testcase:', top.get('testcase'), flush=True)
        print('[LOG-SMOKE] Metrics:', top.get('metrics'), flush=True)
        print('[LOG-SMOKE] Thresholds:', top.get('thresholds'), flush=True)
        print('[LOG-SMOKE] Failed checks:', top.get('failed_checks'), flush=True)

if __name__ == '__main__':
    main()
