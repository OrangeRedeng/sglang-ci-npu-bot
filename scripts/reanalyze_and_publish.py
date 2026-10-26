#!/usr/bin/env python3
"""Refresh existing NPU CI Issues using GLM, source PR evidence and saved snapshot.

No CI rescan, no job-log downloads, no writes to upstream SGLang. Publication is
explicit opt-in (--publish) and refuses unknown fingerprints / incomplete GLM.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

try:
    from scripts.npu_diffusion_ci_report import (GitHub, clusters_from_snapshot,
        glm_analysis, render_report, update_cluster_issue_details, TARGET_REPO, SOURCE_REPO)
    from scripts.issue_sync import enrich_run_provenance, sync_cluster_labels
    from scripts.log_context import repair_consistency_job_from_saved_evidence
except ModuleNotFoundError:
    from npu_diffusion_ci_report import (GitHub, clusters_from_snapshot,
        glm_analysis, render_report, update_cluster_issue_details, TARGET_REPO, SOURCE_REPO)
    from issue_sync import enrich_run_provenance, sync_cluster_labels
    from log_context import repair_consistency_job_from_saved_evidence


def map_existing_issues(clusters, existing):
    """Read-only identity recovery from fingerprint marker, not PR/title proximity."""
    by_fp={}
    for issue in existing:
        found=re.search(r'<!-- npu-diffusion-fingerprint:([a-f0-9]+) -->',issue.get('body') or '')
        if found:by_fp[found.group(1)]=issue
    missing=[]
    for c in clusters:
        i=by_fp.get(c['fingerprint'])
        if not i:
            missing.append(c['fingerprint']);continue
        matched=re.search(r'\bNPU-D\d{3}\b',i.get('title',''))
        if not matched or not re.fullmatch(r'https://github\.com/'+re.escape(TARGET_REPO)+r'/issues/\d+',i.get('html_url','')):
            missing.append(c['fingerprint']);continue
        c['stable_id']=matched.group(0);c['issue_url']=i['html_url']
    return missing


def eligible_for_publication(status: str, analysis: str, allow_partial: bool = False) -> bool:
    """Fail closed on empty/failed model output; permit explicit labeled partials.

    A synthesis timeout can still leave useful fully generated cluster analyses.
    Partial publication remains an explicit operator choice.
    """
    if not analysis or not analysis.strip(): return False
    if status.startswith('completed'): return True
    if not (allow_partial and status.startswith('partial')): return False
    sections=re.findall(r'(?ms)^### NPU-D\d+\s*\n(.*?)(?=^### NPU-D\d+\s*\n|\Z)',analysis)
    if sections:
        return any(len(x.strip())>=100 and
                   'Cluster-specific GLM analysis unavailable' not in x for x in sections)
    return (not analysis.lstrip().startswith('GLM synthesis failed') and
            len(analysis.strip())>=350)


def validate_recovery_analysis(analysis: str, clusters: list[dict]) -> None:
    """Permit publishing recorded per-cluster evidence, never a fabricated synthesis.

    The recovery file must be the actual documented partial output and contain
    a substantial successful investigation for every verified cluster Issue.
    This does not make the missing synthesis a completed stage.
    """
    if not analysis.startswith('GLM synthesis failed (') or \
            'partial per-cluster analysis follows.' not in analysis[:500]:
        raise RuntimeError('Not a GLM failed-synthesis recovery; use --reuse-analysis for a full response')
    matches = list(re.finditer(r'(?m)^### (NPU-D\d{3})\s*$', analysis))
    sections = {}
    for i, match in enumerate(matches):
        body = analysis[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(analysis)].strip()
        if match.group(1) in sections:
            raise RuntimeError('Duplicate incident in recovery: ' + match.group(1))
        sections[match.group(1)] = body
    expected = {c.get('stable_id') for c in clusters}
    if not expected or None in expected or set(sections) != expected:
        raise RuntimeError('Recovery incident IDs do not match snapshot/fingerprinted Issues')
    for incident, section in sections.items():
        if len(section) < 100 or 'Cluster-specific GLM analysis unavailable' in section:
            raise RuntimeError('Missing successful GLM investigation for ' + incident)
    if re.search(r'[\u0400-\u04ff]', analysis):
        raise RuntimeError('Recovery is not English-only')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--publish',action='store_true',help='Modify existing bot Issues after checked GLM')
    parser.add_argument('--allow-partial',action='store_true',help='Allow clearly marked partial GLM if at least one cluster completed')
    parser.add_argument('--reuse-analysis',type=Path,default=None,help='Use a full English GLM analysis, no paid calls')
    parser.add_argument('--reuse-recovery',type=Path,default=None,help='Publish successful recovered cluster investigations after failed synthesis; requires --allow-partial, no GLM calls')
    parser.add_argument('--mode',choices=('triage','investigate'),default='investigate')
    parser.add_argument('--max-investigations',type=int,default=3)
    parser.add_argument('--reasoning-effort',choices=('low','high','max'),default='high')
    parser.add_argument('--max-run-metadata',type=int,default=160)
    parser.add_argument('--output',type=Path,default=None)
    args=parser.parse_args(argv)
    if args.reuse_recovery and args.reuse_analysis:
        parser.error('Choose --reuse-recovery OR --reuse-analysis')
    if args.reuse_recovery and not args.allow_partial:
        parser.error('--reuse-recovery requires explicit --allow-partial')
    if not 1<=args.max_investigations<=5:parser.error('max-investigations must be 1..5')
    if not 0<=args.max_run_metadata<=500:parser.error('max-run-metadata must be 0..500')
    if not TARGET_REPO:parser.error('TARGET_REPOSITORY is required')
    data=json.loads(args.snapshot.read_text(encoding='utf-8'))
    if not isinstance(data.get('jobs'),list) or not data.get('generated_utc'):
        parser.error('Invalid snapshot: jobs/generated_utc required')
    date=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00')).astimezone(
        ZoneInfo('Europe/Moscow')).date().isoformat()
    for j in data['jobs']:repair_consistency_job_from_saved_evidence(j)
    clusters=clusters_from_snapshot(data)
    gh=GitHub(os.getenv('GITHUB_TOKEN'))
    source=GitHub(os.getenv('SOURCE_GITHUB_TOKEN') or os.getenv('GITHUB_TOKEN'))
    existing=gh.list_all('/repos/%s/issues'%TARGET_REPO,{'labels':'npu-diffusion-cluster','state':'all'},max_pages=10)
    missing=map_existing_issues(clusters,existing)
    if missing:
        raise RuntimeError('Refusing Issue updates: missing verified fingerprint mapping for %s'%','.join(missing[:5]))
    daily=gh.list_all('/repos/%s/issues'%TARGET_REPO,{'labels':'npu-diffusion-daily','state':'all'},max_pages=10)
    exact=[i for i in daily if '<!-- npu-diffusion-report-date:%s -->'%date in (i.get('body') or '')]
    if len(exact)!=1:
        raise RuntimeError('Expected exactly one existing daily Issue with date marker %s; found %d'%(date,len(exact)))
    print('[REFRESH] Verified %d clusters and daily Issue #%s; no CI rescan'%(len(clusters),exact[0]['number']),flush=True)
    if args.max_run_metadata:
        stats=enrich_run_provenance(source,data,clusters,SOURCE_REPO,args.max_run_metadata)
        print('[REFRESH] Run provenance: %s'%stats,flush=True)
    os.environ['GLM_REASONING_EFFORT']=args.reasoning_effort
    os.environ['GLM_MAX_INVESTIGATIONS']=str(args.max_investigations)
    os.environ.setdefault('GLM_STAGE_CACHE_DIR','reports/reanalysis/glm-stage-cache')
    if args.reuse_analysis or args.reuse_recovery:
        source_file = args.reuse_recovery or args.reuse_analysis
        analysis=source_file.read_text(encoding='utf-8')
        if not analysis.strip(): raise RuntimeError('Saved GLM analysis file is empty')
        if args.reuse_recovery:
            validate_recovery_analysis(analysis, clusters)
        elif analysis.lstrip().startswith('GLM synthesis failed ('):
            raise RuntimeError('This is a partial synthesis recovery. Use --reuse-recovery with --allow-partial')
        # Still fetch verified PR evidence: the old analysis may predate PR discovery.
        try:
            from scripts.pr_forensics import discover_pr_leads, attach_pr_leads
        except ModuleNotFoundError:
            from pr_forensics import discover_pr_leads, attach_pr_leads
        attach_pr_leads(clusters,discover_pr_leads(source,clusters,SOURCE_REPO))
        if args.reuse_recovery:
            status='partial: recovered investigations; synthesis unavailable'
            print('[REFRESH] Reusing verified cluster investigations from %s; SYNTHESIS FAILED; no BigModel requests'%source_file,flush=True)
        else:
            status='completed: reused English analysis (no new model calls)'
            print('[REFRESH] Reusing saved GLM analysis %s; no BigModel requests'%source_file,flush=True)
    else:
        analysis,status=glm_analysis(data,clusters,mode=args.mode,gh=source)
    timestamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    # Always preserve the text before applying the publication gate. A failed
    # synthesis must not discard three already paid-for cluster investigations.
    recovery=Path('reports/reanalysis')/('glm-refresh-recovery-'+timestamp+'.md')
    recovery.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    recovery.parent.chmod(0o700)
    recovery.write_text(analysis,encoding='utf-8');recovery.chmod(0o600)
    print('[REFRESH] Model status=%s; recovery=%s'%(status,recovery),flush=True)
    publishable=eligible_for_publication(status,analysis,args.allow_partial)
    if re.search(r'[\u0400-\u04ff]',analysis):
        raise RuntimeError('GLM text contains Cyrillic; recovery saved at %s'%recovery)
    if not publishable:
        raise RuntimeError('GLM did not provide publishable analysis (%s). Recovery: %s. '
             'Successful completed stages are cached for retry.'%(status,recovery))
    report=render_report(data,clusters,analysis,status)
    if re.search(r'[\u0400-\u04ff]',report):
        raise RuntimeError('Rendered report is not English-only; recovery at %s'%recovery)
    output=args.output or Path('reports/reanalysis')/('english-pr-refresh-'+timestamp+'.md')
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    output.write_text(report,encoding='utf-8');output.chmod(0o600)
    try:
        from scripts.complete_report import write_complete_companion
    except ImportError:
        from complete_report import write_complete_companion
    write_complete_companion(output,data,clusters,analysis,status)

    complete=output.with_name(output.stem+'-full-analysis.md')
    complete.write_text(analysis,encoding='utf-8');complete.chmod(0o600)
    print('[REFRESH] GLM=%s, PR links=%d; draft=%s'%(status,sum(len(c.get('pr_leads',[])) for c in clusters),output),flush=True)
    if args.publish:
        backup=Path('reports/issue-backups')
        backup.mkdir(parents=True,exist_ok=True,mode=0o700);backup.chmod(0o700)
        # Write all backups before the first remote mutation.
        full=[exact[0]]+[i for i in existing if any(c.get('issue_url')==i.get('html_url') for c in clusters)]
        for entry in full:
            path=backup/('issue-%s-before-v17-%s.md'%(entry['number'],timestamp))
            path.write_text(entry.get('body') or '',encoding='utf-8');path.chmod(0o600)
        # Each cluster update re-verifies its fingerprint before writes.
        update_cluster_issue_details(gh,clusters,analysis,status,strict=True)
        sync_cluster_labels(gh,clusters,TARGET_REPO,existing)
        gh.issue(exact[0]['number'],{'body':report,'title':'[NPU Diffusion CI] Daily Report — '+date})
        print('[REFRESH] Updated daily Issue #%d and verified cluster Issues'%exact[0]['number'],flush=True)
    else:
        print('[REFRESH] Preview only: NO GitHub Issue writes; %s.' % ('NO paid GLM requests were made' if (args.reuse_analysis or args.reuse_recovery) else 'paid GLM analysis was performed'),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
