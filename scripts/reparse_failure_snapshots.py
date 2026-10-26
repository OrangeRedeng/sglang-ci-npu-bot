#!/usr/bin/env python3
"""Reparse retained raw logs offline. Default: preview only. No GitHub/GLM calls."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
try:
    from scripts.log_context import analyze_file
    from scripts.npu_diffusion_ci_report import clusters_from_snapshot
    from scripts.failure_history import correlate_snapshots
except ImportError:
    from log_context import analyze_file
    from npu_diffusion_ci_report import clusters_from_snapshot
    from failure_history import correlate_snapshots


def reparse(data, raw_root):
    updated=missing=0
    for job in data.get('jobs',[]):
        if job.get('conclusion') not in ('failure','timed_out','startup_failure','action_required'): continue
        jid=job.get('job_id')
        if not isinstance(jid,int): missing+=1; continue
        # Duplicate downloads for an overlapping daily window: newest retained copy.
        candidates=sorted(Path(raw_root).glob(f'*/job-{jid}.log'))
        if not candidates: missing+=1; continue
        analysis=analyze_file(candidates[-1])
        job['failure_cases']=analysis['failure_cases']
        job['failure_cases_version']=2
        job['testcase_accounting']=analysis['testcase_accounting']
        job['testcase_accounting']['source_truncated']=bool(job.get('log_analysis',{}).get('truncated'))
        updated+=1
    return {'reparsed_jobs':updated,'raw_logs_unavailable':missing}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports',type=Path,default=Path('reports'))
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    files=sorted(args.reports.glob('npu-diffusion-ci-????-??-??.json'))
    for path in files:
        data=json.loads(path.read_text(encoding='utf-8'))
        result=reparse(data,args.reports/'raw-logs')
        data['failure_history']=correlate_snapshots(data,clusters_from_snapshot(data),args.reports)
        print(json.dumps({'snapshot':path.name,**result,'apply':args.apply}))
        if args.apply:
            backup=args.reports/'p0-backups'/stamp/path.name
            backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copy2(path,backup)
            temporary=path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(path)
    return 0

if __name__=='__main__': raise SystemExit(main())
