#!/usr/bin/env python3
"""Offline report replay. No GitHub client, publication, closure, or GLM calls."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
try:
    from scripts.collection_evidence import attach_observations, observed_log
    from scripts.complete_report import render_complete_report, validate_report
    from scripts.log_context import analyze_file
    from scripts.npu_diffusion_ci_report import clusters_from_snapshot, render_report, parse_timestamp
except ImportError:
    from collection_evidence import attach_observations, observed_log
    from complete_report import render_complete_report, validate_report
    from log_context import analyze_file
    from npu_diffusion_ci_report import clusters_from_snapshot, render_report, parse_timestamp


def replay(data,logs,output):
    now=parse_timestamp(data['generated_utc'])
    known={str(j['job_id']):j for j in data.get('jobs',[])+data.get('comparison_jobs',[]) if j.get('job_id')}
    for jid,path in logs.items():
        if jid not in known:raise ValueError('Log job ID absent from selected snapshot: '+jid)
        job=known[jid]
        with observed_log(path,data['generated_utc']) as (bounded,cutoff):
            dropped=cutoff.get('post_snapshot_lines_excluded',0)
            result=analyze_file(bounded)
            job.update(failure_cases=result['failure_cases'],failure_cases_version=2,
                       testcase_accounting=result['testcase_accounting'])
            attach_observations(job,bounded)
            if dropped:data.setdefault('coverage',[]).append(f'Job {jid}: {dropped} post-snapshot log lines excluded')
    # Legacy counts are checked rather than silently repaired.
    data.setdefault('counts',dict(Counter(j.get('conclusion') or 'unknown' for j in data.get('jobs',[]))))
    clusters=clusters_from_snapshot(data)
    for c in clusters:
        c['stable_id']='OBS-'+c['fingerprint'];c['issue_url']=None
    status='disabled: offline deterministic replay; no GLM requests'
    full=render_complete_report(data,clusters,'',status)
    validate_report(data,full)
    issue=render_report(data,clusters,'',status)
    date=now.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    output.mkdir(parents=True,exist_ok=True)
    prefix=output/f'npu-diffusion-ci-{date}'
    for suffix,content in (('.md',full),('-issue.md',issue),('.json',json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))):
        path=prefix.with_name(prefix.name+suffix)
        path.write_text(content,encoding='utf-8');path.chmod(0o600)
    return prefix.with_suffix('.md')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--log',action='append',default=[],metavar='JOB_ID=PATH')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    logs={}
    for value in args.log:
        jid,sep,path=value.partition('=')
        if not sep or not jid.isdigit() or jid in logs:p.error('--log requires a unique numeric JOB_ID=PATH')
        logs[jid]=Path(path)
    print(replay(json.loads(args.snapshot.read_text(encoding='utf-8')),logs,args.output))


if __name__=='__main__':main()
