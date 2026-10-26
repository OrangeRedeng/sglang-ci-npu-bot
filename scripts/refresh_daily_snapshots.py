#!/usr/bin/env python3
"""Refresh existing daily Issues from repaired snapshots. No new Issues or closure.

Default preview: GitHub reads only, no GLM calls. --apply writes with backup and
concurrent-edit guard. --glm adds paid auto-mode analysis only with --apply.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
try:
    from scripts import npu_diffusion_ci_report as ci
    from scripts.failure_history import correlate_snapshots
    from scripts.analysis_evidence import coverage_matrix, load_coverage_history
except ImportError:
    import npu_diffusion_ci_report as ci
    from failure_history import correlate_snapshots
    from analysis_evidence import coverage_matrix, load_coverage_history


def recover_investigations(path, clusters, date):
    """Validate legacy failed-synthesis Markdown against this dated snapshot.

    Legacy files lack a snapshot digest: ID/date checks prevent obvious mismatch,
    but do not constitute a verified causal result or a complete synthesis.
    """
    path=Path(path)
    if path.name!=date+'-refresh-failed.md':
        raise RuntimeError('Recovery filename must match the selected snapshot date')
    if path.stat().st_size>150_000:raise RuntimeError('Recovery file exceeds limit')
    text=path.read_text(encoding='utf-8')
    if not text.startswith('GLM synthesis failed (') or 'partial per-cluster analysis follows.' not in text[:500]:
        raise RuntimeError('Expected saved failed-synthesis investigations')
    if re.search(r'[\u0400-\u04ff]',text):raise RuntimeError('Recovery must be English-only')
    aliases={}
    for c in clusters:
        for alias in (c.get('stable_id'),c['fingerprint'],'OBS-'+c['fingerprint']):
            if alias:aliases[alias]=c
    pattern=r'(?m)^### (NPU-D\d{3}|OBS-[a-f0-9]{14}|[a-f0-9]{14})\s*$'
    headings=list(re.finditer(pattern,text))
    if not headings:raise RuntimeError('No recognized recovered cluster sections')
    seen=set();sections=[]
    for index,heading in enumerate(headings):
        ident=heading[1];c=aliases.get(ident)
        if c is None or c['fingerprint'] in seen:
            raise RuntimeError('Unknown or duplicate recovered cluster '+ident)
        seen.add(c['fingerprint'])
        body=text[heading.end():headings[index+1].start() if index+1<len(headings) else len(text)].strip()
        if 'Cluster-specific GLM analysis unavailable' in body:continue
        if len(body)<100:raise RuntimeError('Incomplete recovered cluster '+ident)
        ref=c.get('stable_id') or 'OBS-'+c['fingerprint']
        c['analysis_ref']=ref
        sections.append('### '+ref+'\n'+body)
    if not sections:raise RuntimeError('No completed investigations to recover')
    return ('Recovered completed cluster investigations. **Synthesis unavailable; '
            'uninvestigated families remain deterministic observations.**\n\n'+'\n\n'.join(sections))


def refresh(gh,source,data,issues,root,apply=False,use_glm=False,reuse_investigations=None,allow_partial=False):
    from zoneinfo import ZoneInfo
    date=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    marker='<!-- npu-diffusion-report-date:'+date+' -->'
    daily=[i for i in issues if marker in (i.get('body') or '') and 'pull_request' not in i]
    if len(daily)!=1:raise RuntimeError(f'Expected one existing daily Issue for {date}; found {len(daily)}')
    issue=daily[0]
    clusters=ci.clusters_from_snapshot(data)
    # Reuse only exact fingerprints; PR-only observations legitimately have no Issue.
    matched=[]
    try:
        from scripts.issue_taxonomy import classify_cluster
    except ImportError:
        from issue_taxonomy import classify_cluster
    for c in clusters:
        c['incident_scope']=classify_cluster(c)['scope']
        for old in issues:
            if f"<!-- npu-diffusion-fingerprint:{c['fingerprint']} -->" not in (old.get('body') or ''):continue
            ident=re.search(r'\bNPU-D\d{3}\b',old.get('title') or '')
            if not ident:continue
            c['stable_id']=ident[0];c['issue_url']=old['html_url']
            if old.get('state')=='open' and c['incident_scope']=='main':matched.append(c)
            break
        c['pr_leads']=(data.get('pr_evidence') or {}).get(c['fingerprint'],[])
    data['failure_history']=correlate_snapshots(data,clusters,root)
    data['coverage_matrix']=coverage_matrix(data,load_coverage_history(data,root))
    try:
        from scripts.ci_investigation import build_regression_context, load_saved_history
    except ImportError:
        from ci_investigation import build_regression_context, load_saved_history
    data['regression_context']=build_regression_context(data,clusters,load_saved_history(data,root/'snapshots'))
    analysis='Historical report rebuilt from retained evidence; no new GLM investigation.'
    status='historical deterministic refresh'
    if reuse_investigations:
        if use_glm or not allow_partial:
            raise RuntimeError('Recovery requires --allow-partial and cannot use --glm')
        analysis=recover_investigations(reuse_investigations,clusters,date)
        status='partial: recovered completed investigations; synthesis unavailable; no new GLM calls'
        data['glm_telemetry']=[{'stage':'recovery','status':'reused completed analyses; original API usage unavailable'}]
    if apply and use_glm:
        checkpoint=root/'analysis'/f'{date}-refresh-context.json'
        checkpoint.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.environ.setdefault('GLM_STAGE_CACHE_DIR',str(root/'glm-stage-cache'))
        previous=os.environ.get('GLM_RECOVERY_CONTEXT_FILE')
        os.environ['GLM_RECOVERY_CONTEXT_FILE']=str(checkpoint)
        try:
            analysis,status=ci.glm_analysis(data,clusters,mode='auto',gh=source)
        finally:
            if previous is None:os.environ.pop('GLM_RECOVERY_CONTEXT_FILE',None)
            else:os.environ['GLM_RECOVERY_CONTEXT_FILE']=previous
        data['pr_evidence']={c['fingerprint']:c.get('pr_leads',[]) for c in clusters}
        if not status.startswith('completed'):
            # Preserve paid partial result without replacing authoritative report.
            recovery=root/'analysis'/f'{date}-refresh-failed.md'
            recovery.parent.mkdir(parents=True,exist_ok=True)
            recovery.write_text(analysis,encoding='utf-8');recovery.chmod(0o600)
            raise RuntimeError(f'GLM status {status}; not publishing. Recovery: {recovery}')
    report=ci.render_report(data,clusters,analysis,status)
    try:
        from scripts.complete_report import render_complete_report, validate_report
    except ImportError:
        from complete_report import render_complete_report, validate_report
    full_report=render_complete_report(data,clusters,analysis,status)
    validate_report(data,full_report)
    draft=root/'repaired'/f'npu-diffusion-ci-{date}.md'
    draft.parent.mkdir(parents=True,exist_ok=True);draft.write_text(full_report,encoding='utf-8');draft.chmod(0o600)
    issue_draft=draft.with_name(draft.stem+'-issue.md')
    issue_draft.write_text(report,encoding='utf-8');issue_draft.chmod(0o600)
    print(f'{date}: existing daily #{issue["number"]}; {len(clusters)} families; {len(matched)} mapped open incidents; apply={apply}; GLM={status}')
    if not apply:return
    backup=root/'issue-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup.mkdir(parents=True,mode=0o700)
    (backup/'issues.json').write_text(json.dumps(issues,indent=2),encoding='utf-8');(backup/'issues.json').chmod(0o600)
    latest=gh.get(f'/repos/{ci.TARGET_REPO}/issues/{issue["number"]}')
    if latest.get('updated_at')!=issue.get('updated_at') or latest.get('body')!=issue.get('body'):
        raise RuntimeError('Daily Issue changed concurrently; refusing overwrite')
    gh.request('PATCH',f'/repos/{ci.TARGET_REPO}/issues/{issue["number"]}',{'body':report})
    # Deterministic refresh changes daily only. Incident RCA needs completed GLM.
    if use_glm:ci.update_cluster_issue_details(gh,matched,analysis,status,strict=True)
    evidence=root/'repaired'/f'npu-diffusion-ci-{date}.json'
    data['pr_evidence']={c['fingerprint']:c.get('pr_leads',[]) for c in clusters}
    evidence.write_text(json.dumps(data,indent=2),encoding='utf-8');evidence.chmod(0o600)
    (root/'repaired'/f'{date}-analysis.md').write_text(analysis,encoding='utf-8')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reports',type=Path,default=Path('reports'))
    p.add_argument('--dates',nargs='+',help='Explicit saved daily dates; default latest snapshot')
    p.add_argument('--reuse-investigations',type=Path,help='Reuse saved failed-synthesis Markdown; no paid calls')
    p.add_argument('--allow-partial',action='store_true',help='Explicitly publish labelled recovered analyses without synthesis')
    p.add_argument('--apply',action='store_true');p.add_argument('--glm',action='store_true')
    a=p.parse_args(argv)
    if not ci.TARGET_REPO:p.error('TARGET_REPOSITORY required')
    if a.reuse_investigations and (a.glm or not a.allow_partial or not a.dates or len(a.dates)!=1):
        p.error('Recovery requires --allow-partial and exactly one --dates value; cannot combine with --glm')
    if a.allow_partial and not a.reuse_investigations:p.error('--allow-partial requires --reuse-investigations')
    if a.glm and not a.apply:p.error('--glm requires --apply; preview never makes paid calls')
    files=sorted(a.reports.glob('npu-diffusion-ci-????-??-??.json'))
    if a.dates:
        if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}',d) for d in a.dates):p.error('Invalid date')
        files=[a.reports/f'npu-diffusion-ci-{d}.json' for d in a.dates]
    else:files=files[-1:]
    if not files:raise RuntimeError('No saved daily snapshots')
    gh=ci.GitHub(os.getenv('GITHUB_TOKEN'));source=ci.GitHub(os.getenv('SOURCE_GITHUB_TOKEN') or os.getenv('GITHUB_TOKEN'))
    issues=gh.list_all(f'/repos/{ci.TARGET_REPO}/issues',{'state':'all'},max_pages=20)
    if len(issues)>=2000:raise RuntimeError('Issue enumeration capped; refusing refresh')
    for f in files:refresh(gh,source,json.loads(f.read_text()),issues,a.reports,a.apply,a.glm,a.reuse_investigations,a.allow_partial)
    return 0
if __name__=='__main__':raise SystemExit(main())
