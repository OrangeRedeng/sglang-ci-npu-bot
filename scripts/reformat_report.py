#!/usr/bin/env python3
"""Reformat an existing CI report without GitHub CI scan or GLM calls.

--publish is opt-in and only updates the bot's own daily/cluster Issues.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

try:
    from scripts.npu_diffusion_ci_report import (
        GitHub, clusters_from_snapshot, render_report, post_daily,
        update_cluster_issue_details)
    from scripts.log_context import repair_consistency_job_from_saved_evidence
except ModuleNotFoundError:
    from npu_diffusion_ci_report import (
        GitHub, clusters_from_snapshot, render_report, post_daily,
        update_cluster_issue_details)
    from log_context import repair_consistency_job_from_saved_evidence


def extract_previous_analysis(text):
    m = re.search(r'## Engineering investigation[^\n]*\n(.*?)\n## Collection coverage',
                  text, re.S | re.I)
    return m.group(1).strip() if m else ''


def restore_ids(clusters, old_report):
    """Use the matching recorded signature; never guess cluster Issue identities."""
    pattern = re.compile(r'^### \[(NPU-D\d{3})\]\((https://github\.com/[^\s)]+/issues/\d+)\)[^\n]*\n'
                         r'- \*\*Observed fact:\*\* `([^`]+)`', re.M)
    by_signature = {m.group(3): (m.group(1), m.group(2)) for m in pattern.finditer(old_report)}
    found = 0
    for c in clusters:
        ids = by_signature.get(c.get('signature', ''))
        if ids:
            c['stable_id'], c['issue_url'] = ids
            found += 1
        else:
            c['stable_id'] = 'PREVIEW-' + c['fingerprint'][:6]
    return found


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--previous-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--publish', action='store_true', help='Update daily and matching cluster Issues in the bot repository')
    parser.add_argument('--refresh-provenance', action='store_true',
                        help='Read GitHub run metadata to distinguish main/PR (requires --publish)')
    parser.add_argument('--max-provenance-runs', type=int, default=200,
                        help='Maximum GitHub run metadata reads (default: 200)')
    parser.add_argument('--sync-labels', action='store_true',
                        help='Create/recolor main, not-merged, unconfirmed, flaky, infrastructure labels (requires --publish)')
    args=parser.parse_args(argv)
    if (args.refresh_provenance or args.sync_labels) and not args.publish:
        parser.error('--refresh-provenance and --sync-labels require --publish')
    if not 0 <= args.max_provenance_runs <= 1000:
        parser.error('--max-provenance-runs must be 0..1000')
    data=json.loads(args.snapshot.read_text(encoding='utf-8'))
    if not isinstance(data.get('jobs'),list) or not data.get('generated_utc'):
        parser.error('Snapshot missing jobs/generated_utc')
    previous=args.previous_report.read_text(encoding='utf-8')
    analysis=extract_previous_analysis(previous)
    if not analysis:
        # v15 verbose GLM analysis is stored separately; otherwise this is a
        # legitimate deterministic-only format change, not a new RCA analysis.
        date=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00')).date().isoformat()
        path=Path('reports/analysis')/('npu-diffusion-ci-'+date+'-full.md')
        if path.exists():analysis=path.read_text(encoding='utf-8')
    repairs=sum(bool(repair_consistency_job_from_saved_evidence(j)) for j in data['jobs'])
    clusters=clusters_from_snapshot(data)
    ids=restore_ids(clusters,previous)
    gh = None
    existing_issues = []
    if args.publish:
        try:
            from scripts.npu_diffusion_ci_report import TARGET_REPO, SOURCE_REPO, CLUSTER_LABEL
        except ModuleNotFoundError:
            from npu_diffusion_ci_report import TARGET_REPO, SOURCE_REPO, CLUSTER_LABEL
        if not TARGET_REPO:
            raise RuntimeError('TARGET_REPOSITORY required to publish')
        gh=GitHub(os.getenv('GITHUB_TOKEN'))
        existing_issues=gh.list_all('/repos/%s/issues' % TARGET_REPO,
            {'labels':CLUSTER_LABEL,'state':'all'},max_pages=10)
        from_fingerprint={}
        for issue in existing_issues:
            match=re.search(r'<!-- npu-diffusion-fingerprint:([a-f0-9]+) -->',issue.get('body') or '')
            if match:
                from_fingerprint[match.group(1)]=issue
        for c in clusters:
            if c.get('stable_id','').startswith('PREVIEW-'):
                issue=from_fingerprint.get(c['fingerprint'])
                if issue:
                    found=re.search(r'NPU-D\d+', issue.get('title') or '')
                    if found:
                        c['stable_id']=found.group(0)
                        c['issue_url']=issue['html_url']
        ids=sum(bool(re.fullmatch(r'NPU-D\d+',c.get('stable_id',''))) for c in clusters)
        if args.refresh_provenance:
            try:
                from scripts.issue_sync import enrich_run_provenance
            except ModuleNotFoundError:
                from issue_sync import enrich_run_provenance
            source_gh=GitHub(os.getenv('SOURCE_GITHUB_TOKEN') or os.getenv('GITHUB_TOKEN'))
            stats=enrich_run_provenance(source_gh,data,clusters,SOURCE_REPO,args.max_provenance_runs)
            print('[REFORMAT] Verified upstream provenance:',stats,flush=True)
    status_match=re.search(r'^\*\*GLM post-analysis:\*\*\s*([^\n]+)', previous,re.M)
    status=status_match.group(1).strip() if status_match else ('archived GLM' if analysis else 'disabled')
    result=render_report(data,clusters,analysis,status)
    date=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00')).astimezone(__import__('zoneinfo').ZoneInfo('Europe/Moscow')).date().isoformat()
    outfile=args.output or Path('reports')/('npu-diffusion-ci-'+date+'.md')
    outfile.parent.mkdir(parents=True,exist_ok=True)
    if outfile.resolve()==args.previous_report.resolve():
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup=outfile.with_name(outfile.stem+'-before-v15-'+stamp+'.md')
        backup.write_text(previous, encoding='utf-8')
        backup.chmod(0o600)
        print('[REFORMAT] Original report backup:',backup,flush=True)
    if analysis:
        root=Path('reports/analysis');root.mkdir(parents=True,exist_ok=True,mode=0o700)
        root.chmod(0o700)
        full=root/('npu-diffusion-ci-'+date+'-full.md')
        full.write_text(analysis,encoding='utf-8');full.chmod(0o600)
    outfile.write_text(result,encoding='utf-8');outfile.chmod(0o600)
    try:
        from scripts.complete_report import write_complete_companion
    except ImportError:
        from complete_report import write_complete_companion
    write_complete_companion(outfile,data,clusters,analysis,status)

    print('[REFORMAT] Clusters=%d, recovered_ids=%d, repaired_jobs=%d, GLM_chars=%d, output_chars=%d, file=%s' %
          (len(clusters),ids,repairs,len(analysis),len(result),outfile),flush=True)
    if args.publish:
        if ids != len(clusters):
            raise RuntimeError('Refusing to publish: not all stable cluster IDs were verified in the previous report')
        if args.sync_labels:
            try:
                from scripts.issue_sync import sync_cluster_labels
            except ModuleNotFoundError:
                from issue_sync import sync_cluster_labels
            print('[REFORMAT] GitHub issue labels:',
                  sync_cluster_labels(gh,clusters,TARGET_REPO,existing_issues),flush=True)
        update_cluster_issue_details(gh,clusters,analysis,status)
        print('[REFORMAT] Published daily Issue:',post_daily(gh,result,date,False),flush=True)
    else:
        print('[REFORMAT] Offline: no GitHub calls or GLM charges. Use --publish to update Issues.',flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
