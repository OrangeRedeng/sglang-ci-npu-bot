#!/usr/bin/env python3
"""Refresh cross-day continuity in an existing report from saved JSON only.

No CI rescan; no GLM/API billing. Preview is read-only; --publish writes to
owned GitHub Issues using TARGET_REPOSITORY and GITHUB_TOKEN.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re

try:
    from scripts import npu_diffusion_ci_report as ci
    from scripts.incident_lifecycle import (build_continuity,render_continuity,
        update_unobserved_issues,archive_previous_daily_reports)
except ModuleNotFoundError:
    import npu_diffusion_ci_report as ci
    from incident_lifecycle import (build_continuity,render_continuity,
        update_unobserved_issues,archive_previous_daily_reports)


def replace_section(body, section):
    start='## Incident continuity'
    next_heading=re.compile(r'(?m)^## (?!Incident continuity)[^\n]+$')
    index=body.find(start)
    if index>=0:
        match=next_heading.search(body,index+len(start))
        if not match:raise ValueError('Cannot find following section after existing continuity')
        return body[:index]+section+'\n'+body[match.start():]
    marker='## Incidents: impact, suspected cause, next fix'
    index=body.find(marker)
    if index<0:raise ValueError('Expected compact report structure not found')
    return body[:index]+section+'\n'+body[index:]


def run(date, snapshot, publish=False, report_path=None, gh=None, verify_passes=False, max_pass_logs=24, allow_pr=False, source_gh=None):
    if not re.fullmatch(r'20\d\d-\d\d-\d\d',date):raise ValueError('Bad ISO date')
    data=json.loads(Path(snapshot).read_text(encoding='utf-8'))
    generated=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00'))
    from zoneinfo import ZoneInfo
    if generated.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()!=date:
        raise ValueError('Snapshot belongs to a different Moscow calendar day')
    gh=gh or ci.GitHub(os.getenv('GITHUB_TOKEN'))
    clusters=ci.clusters_from_snapshot(data)
    existing=ci.get_existing_issues(gh,ci.CLUSTER_LABEL)
    daily=ci.get_existing_issues(gh,ci.DAILY_LABEL)
    lookup={x.get('body','').split('<!-- npu-diffusion-fingerprint:',1)[-1].split(' -->',1)[0]:x
            for x in existing if '<!-- npu-diffusion-fingerprint:' in (x.get('body') or '')}
    for c in clusters:
        match=lookup.get(c['fingerprint'])
        if match:
            c['issue_url']=match.get('html_url')
            m=re.search(r'NPU-D\d{3}',match.get('title') or '')
            c['stable_id']=m.group(0) if m else 'UNKNOWN'
    continuity=build_continuity(clusters, existing,daily,date,data)
    proofs={}
    if verify_passes:
        try:
            from scripts.incident_verifier import verify_resolution
            from scripts.incident_lifecycle import mark_verified_continuity
        except ImportError:
            from incident_verifier import verify_resolution
            from incident_lifecycle import mark_verified_continuity
        # Read-only GitHub Actions job log downloads; no workflow rescan and no GLM.
        source_gh=source_gh or ci.GitHub(os.getenv('SOURCE_GITHUB_TOKEN') or os.getenv('GITHUB_TOKEN'))
        previous_dates = set()
        for entry in daily:
            match = re.search(r'<!-- npu-diffusion-report-date:(\d{4}-\d{2}-\d{2}) -->',
                              entry.get('body') or '')
            if match and match.group(1) < date:
                previous_dates.add(match.group(1))
        histories=[Path('reports') / ('npu-diffusion-ci-'+old+'.json')
                   for old in sorted(previous_dates)]
        histories+=sorted(Path('reports/snapshots').glob('npu-diffusion-ci-*.json'),reverse=True)[:18]
        proofs=verify_resolution(source_gh,data,existing,continuity,histories,
                                 limit=max_pass_logs,allow_pr=allow_pr)
        mark_verified_continuity(continuity,proofs)
    section=render_continuity(continuity)
    report=next((x for x in daily if f'<!-- npu-diffusion-report-date:{date} -->' in (x.get('body') or '')),None)
    if not report:raise ValueError('Daily GitHub Issue does not exist for '+date)
    refreshed=replace_section(report['body'],section)
    if publish:
        if not os.getenv('TARGET_REPOSITORY'):
            raise ValueError('TARGET_REPOSITORY required for publication')
        if not os.getenv('GITHUB_TOKEN'):
            raise ValueError('GITHUB_TOKEN required for publication')
        gh.issue(report['number'],{'body':refreshed})
        if proofs and os.getenv('INCIDENT_AUTO_CLOSE','1')=='1':
            try:
                from scripts.incident_verifier import apply_verified_resolutions
            except ImportError:
                from incident_verifier import apply_verified_resolutions
            closed=apply_verified_resolutions(gh,proofs,existing)
            continuity['verified_proofs']=closed
        update_unobserved_issues(gh,continuity,existing,ci.TARGET_REPO)
        if os.getenv('ARCHIVE_PREVIOUS_DAILY_ISSUES','1')=='1':
            archive_previous_daily_reports(gh,daily,date)
        if report_path:
            Path(report_path).write_text(refreshed,encoding='utf-8')
    return refreshed,continuity


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('date',help='Moscow calendar date YYYY-MM-DD')
    ap.add_argument('--snapshot')
    ap.add_argument('--output')
    ap.add_argument('--publish',action='store_true')
    ap.add_argument('--verify-passes',action='store_true', help='Read bounded successful-job logs from GitHub, no CI rescan or GLM')
    ap.add_argument('--max-pass-logs',type=int,default=24)
    ap.add_argument('--allow-pr-close',action='store_true', help='Opt-in PR-scoped resolution verification')
    args=ap.parse_args()
    path=args.snapshot or 'reports/npu-diffusion-ci-'+args.date+'.json'
    output=args.output or 'reports/npu-diffusion-ci-'+args.date+'.md'
    report,continuity=run(args.date,path,args.publish,output if args.publish else None,
        verify_passes=args.verify_passes,max_pass_logs=args.max_pass_logs,allow_pr=args.allow_pr_close)
    print(render_continuity(continuity))
    print('Mode: '+('PUBLISHED' if args.publish else 'PREVIEW: no Issue writes, no GLM calls'))

if __name__=='__main__':main()
