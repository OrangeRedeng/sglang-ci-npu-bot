#!/usr/bin/env python3
"""Render a saved completed synthesis; publish only the selected daily Issue on request."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import re
from zoneinfo import ZoneInfo
try:
    from scripts import npu_diffusion_ci_report as ci
    from scripts.analysis_recovery import load_analysis
    from scripts.complete_report import render_complete_report, validate_report
except ImportError:
    import npu_diffusion_ci_report as ci
    from analysis_recovery import load_analysis
    from complete_report import render_complete_report, validate_report


def recover(root,date,publish=False,allow_legacy=False,gh=None):
    root=Path(root)
    data=json.loads((root/f'npu-diffusion-ci-{date}.json').read_text(encoding='utf-8'))
    actual=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    if actual!=date:raise ValueError('Snapshot date does not match requested report')
    analysis,status=load_analysis(root/'analysis'/f'npu-diffusion-ci-{date}-full.md',data,allow_legacy)
    clusters=ci.clusters_from_snapshot(data)
    for c in clusters:
        c.update(stable_id='OBS-'+c['fingerprint'],issue_url=None,
                 pr_leads=data.get('pr_evidence',{}).get(c['fingerprint'],[]))
    # Build both reports before any GitHub mutation. Recovery never calls GLM.
    complete=render_complete_report(data,clusters,analysis,status)
    validate_report(data,complete)
    issue=ci.render_report(data,clusters,analysis,status)
    if len(issue)>ci.MAX_ISSUE_BODY:raise ValueError('Recovered Issue exceeds publication limit')
    output=root/'recovered';output.mkdir(parents=True,exist_ok=True,mode=0o700)
    for suffix,text in (('.md',complete),('-issue.md',issue)):
        p=output/f'npu-diffusion-ci-{date}{suffix}'
        p.write_text(text,encoding='utf-8');p.chmod(0o600)
    if not publish:
        print(f'Preview: {output}; {status}; no GitHub writes')
        return None
    if not ci.TARGET_REPO:raise ValueError('TARGET_REPOSITORY required for publication')
    gh=gh or ci.GitHub(os.getenv('GITHUB_TOKEN'))
    marker=f'<!-- npu-diffusion-report-date:{date} -->'
    matches=[i for i in ci.get_existing_issues(gh,ci.DAILY_LABEL) if marker in (i.get('body') or '') and 'pull_request' not in i]
    if len(matches)>1:raise ValueError('Multiple daily Issues match the date')
    if matches:
        old=matches[0]
        backup=root/'issue-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        backup.mkdir(parents=True,mode=0o700)
        p=backup/f'issue-{old["number"]}.json';p.write_text(json.dumps(old,indent=2),encoding='utf-8');p.chmod(0o600)
        latest=gh.get(f'/repos/{ci.TARGET_REPO}/issues/{old["number"]}')
        if latest.get('body')!=old.get('body') or latest.get('updated_at')!=old.get('updated_at'):
            raise RuntimeError('Daily Issue changed concurrently; refusing overwrite')
        gh.issue(old['number'],{'body':issue})
        url=old.get('html_url')
    else:
        ci.ensure_label(gh,ci.DAILY_LABEL,'0969da','Daily Ascend NPU diffusion CI report')
        if any(marker in (i.get('body') or '') for i in ci.get_existing_issues(gh,ci.DAILY_LABEL)):
            raise RuntimeError('Daily Issue appeared during recovery; rerun to check it')
        created=gh.create_issue({'title':f'[NPU Diffusion CI] Daily Report — {date}',
                                 'body':issue,'labels':[ci.DAILY_LABEL]})
        url=created['html_url']
    print(f'Recovered daily report: {url}; {status}; no incident creation or closure')
    return url


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--date',required=True)
    p.add_argument('--reports',type=Path,default=Path('reports'))
    p.add_argument('--publish',action='store_true')
    p.add_argument('--allow-legacy-analysis',action='store_true')
    a=p.parse_args(argv)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',a.date):p.error('Date must be YYYY-MM-DD')
    recover(a.reports,a.date,a.publish,a.allow_legacy_analysis)
    return 0


if __name__=='__main__':raise SystemExit(main())
