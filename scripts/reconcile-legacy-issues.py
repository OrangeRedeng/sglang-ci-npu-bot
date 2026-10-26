#!/usr/bin/env python3
"""One-time and repeatable historical Issue cleanup, never a claim of CI fix.

Default mode is read-only. --apply closes bot-owned PR-only incident Issues
administratively as 'not planned', preserves all evidence, and retains main
incidents for actual testcase-level verification. No GitHub Issue is deleted.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

try:
    from scripts.npu_diffusion_ci_report import GitHub
except ModuleNotFoundError:
    from npu_diffusion_ci_report import GitHub

REPO = 'sglang-ci-npu-bot/sglang-ci-npu-bot'
BASE = '/repos/' + REPO + '/issues'
FP_RE = re.compile(r'<!-- npu-diffusion-fingerprint:([0-9a-f]{10,64}) -->')
DAY_RE = re.compile(r'<!-- npu-diffusion-report-date:(\d{4}-\d{2}-\d{2}) -->')
ID_RE = re.compile(r'\bNPU-D\d{3}\b')
ARCHIVE_MARKER = '<!-- npu-ci-legacy-pr-archived:v22.0.1 -->'
DAILY_MARKER = '<!-- npu-ci-legacy-migration:start -->'
DAILY_END = '<!-- npu-ci-legacy-migration:end -->'
CASE_PATTERNS = (
    re.compile(r'test_diffusion_generation\[([a-zA-Z0-9_\-]+)\]'),
    re.compile(r"Diffusion testcase ['\"]([^'\"]+)['\"]"),
    re.compile(r'Consistency check failed for\s+([a-zA-Z0-9_\-]+)'),
    re.compile(r'\b([a-z][a-z0-9_]*_(?:1npu|2npu|npu))\b', re.I),
    re.compile(r'FAILED\s+\S+::([\w]+)::([\w]+)')
)


def labels(issue):
    return {str(x.get('name') if isinstance(x,dict) else x) for x in issue.get('labels', [])}


def issue_id(issue):
    match=ID_RE.search(issue.get('title') or '')
    if not match: match=ID_RE.search(issue.get('body') or '')
    return match.group(0) if match else None


def case(issue):
    blob=(issue.get('body') or '') + '\n' + (issue.get('title') or '')
    for regex in CASE_PATTERNS:
        m=regex.search(blob)
        if m:return (m.group(1) if len(m.groups())==1 else m.group(1)+'::'+m.group(2))[:75]
    if 'world_size=' in blob and 'backend=hccl timeout=3600' in blob:
        return 'Unclassified pytest failure (HCCL init is not the cause)'
    if re.search(r'SSLCertVerificationError|CERTIFICATE_VERIFY_FAILED',blob,re.I):
        return 'Dependency mirror — TLS certificate error'
    if re.search(r'ReadTimeoutError|Read timed out',blob,re.I):
        return 'Dependency mirror — network timeout'
    # Never turn an arbitrary log line into a purported root cause.
    return 'Unclassified CI failure — inspect original job'


def family(issue):
    s=(issue.get('body') or '')[:3200]
    if re.search(r'PerformanceValidationError|performance validation',s,re.I):return 'performance'
    if re.search(r'Consistency check failed|SSIM=|PSNR=|CLIP=',s,re.I):return 'quality / GT'
    if re.search(r'SSL|ReadTimeoutError|HTTPSConnectionPool|connection-timeout',s,re.I):return 'infrastructure'
    if 'world_size=' in s and 'backend=hccl timeout=3600' in s:return 'needs re-triage'
    if re.search(r'HTTPError: 500|Server Error',s,re.I):return 'server HTTP 500'
    return 'test failure'


def title_for(issue,scope):
    ident=issue_id(issue)
    qualifier='main' if scope=='main' else 'PR-only archived'
    return (f'[{ident}] [{qualifier}] {case(issue)} — {family(issue)}')[:125]


def enumerate_all(gh):
    # Deliberately fail closed if the bounded collection is incomplete.
    result=[]
    for n in range(1,21):
        batch=gh.get(BASE,{'state':'all','per_page':100,'page':n})
        if not isinstance(batch,list):raise RuntimeError('GitHub returned non-list, aborting')
        result += [x for x in batch if 'pull_request' not in x]
        if len(batch)<100:return result
    raise RuntimeError('GitHub pagination exceeded 2000 Issues; aborting rather than using partial state')


def plan(issues):
    result=[]
    daily=[]
    for issue in issues:
        if DAY_RE.search(issue.get('body') or '') and 'npu-diffusion-daily' in labels(issue):
            daily.append(issue)
    newest=max(((DAY_RE.search(x.get('body') or '').group(1),x['number']) for x in daily),default=(None,None))
    for issue in sorted(issues,key=lambda x:x['number']):
        n=issue['number'];body=issue.get('body') or '';existing=issue.get('title') or ''
        owned='npu-diffusion-cluster' in labels(issue)
        if issue.get('pull_request'):continue
        day=DAY_RE.search(body)
        if day and 'npu-diffusion-daily' in labels(issue):
            if issue.get('state')=='open' and n != newest[1]:
                result.append({'number':n,'kind':'old_daily','action':'archive_daily',
                               'title':existing,'new_title':existing,'state':'closed','reason':'completed'})
            continue
        if not owned or not FP_RE.search(body) or not issue_id(issue):continue
        labs=labels(issue)
        if 'main' in labs:
            if issue.get('state')=='open' and title_for(issue,'main')!=existing:
                result.append({'number':n,'kind':'main','action':'rename_only',
                              'title':existing,'new_title':title_for(issue,'main'),'state':'open'})
            continue
        if 'not-merged' in labs and 'main' not in labs:
            if issue.get('state')=='open':
                result.append({'number':n,'kind':'pr_only','action':'archive_pr_only',
                              'title':existing,'new_title':title_for(issue,'pr_only'),
                              'state':'closed','reason':'not_planned',
                              'needs_retriage':family(issue)=='needs re-triage'})
            elif ARCHIVE_MARKER in body:continue
            # Never rewrite separately closed human-reviewed issues.
            continue
        # Unconfirmed provenance is never enough evidence to close or demote.
    return result,newest[1]


def archival_append(issue,latest_number,needs_retriage):
    body=issue.get('body') or ''
    if ARCHIVE_MARKER in body:return body
    latest=f'https://github.com/{REPO}/issues/{latest_number}' if latest_number else f'https://github.com/{REPO}/issues'
    detail=('The displayed HCCL initialization string was NOT a proven error. The actual pytest '
            'traceback must be re-triaged from the linked CI job(s).'
            if needs_retriage else 'The failure may still exist on the PR branch; it has NOT been verified fixed.')
    section=(f'{ARCHIVE_MARKER}\n\n## Administrative disposition — archived PR-only observation\n\n'
             f'Archived under the **main-first reporting policy** to prevent a flood of '
             f'standalone PR-only Issues. **Not fixed / not resolved by a verified PASS.** '
             f'This observation remains available here with its original evidence, '
             f'and PR-only failures are tracked in the [daily report / PR-watchlist]({latest}).\n\n'
             f'**Evidence limitation:** {detail}\n\n'
             f'**Reopening:** If a comparable failure is established on real `main`, create/link '
             f'a main incident or reopen only after validating fingerprint and source provenance.\n')
    return body.rstrip()+'\n\n'+section


def summary_section(issues):
    archived=[x for x in issues if ARCHIVE_MARKER in (x.get('body') or '')]
    ids=[issue_id(x) for x in archived if issue_id(x)]
    return ('\n'.join([DAILY_MARKER,
        '## Historical Issue reconciliation (administrative)', '',
        f'**{len(ids)} legacy PR-only incident Issues were archived administratively**, '
        'not marked fixed, to reduce noise. They retain their original evidence and can be reviewed.',
        'The preserved original incident lists may contain historical/unpublished NPU-D IDs; '
        'a listed ID is not proof that a corresponding GitHub Issue exists.',
        'New PR-only failures are summarized in the daily PR-watchlist rather than filed as new Issues.',
        'Actual `main` incidents remain open until verified testcase-level PASS evidence.',
        DAILY_END]))


def preview(gh):
    issues=enumerate_all(gh)
    changes,newest=plan(issues)
    return issues,changes,newest


def apply(gh,issues,changes,newest,backup_dir:Path):
    if not os.getenv('GITHUB_TOKEN'):
        raise ValueError('GITHUB_TOKEN required to modify Issues')
    if os.getenv('TARGET_REPOSITORY')!=REPO:
        raise ValueError('TARGET_REPOSITORY must match '+REPO)
    backup_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup_dir.chmod(0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=backup_dir/('reconcile-legacy-issues-'+stamp+'.json')
    # Store all fetched issue bodies, including skipped ones, before any writes.
    with backup.open('x',encoding='utf-8') as f:
        f.write(json.dumps({'repository':REPO,'generated_utc':stamp,'issues':issues,
                            'planned_actions':changes},ensure_ascii=False,indent=2))
        f.flush();os.fsync(f.fileno())
    backup.chmod(0o600)
    original={x['number']:x for x in issues}
    performed=[];skipped=[]
    for change in changes:
        n=change['number'];old=original[n]
        fresh=gh.get(BASE+f'/{n}')
        # Concurrent edits or even label changes must never be clobbered.
        expected=(old.get('body'),old.get('title'),old.get('state'),labels(old))
        actual=(fresh.get('body'),fresh.get('title'),fresh.get('state'),labels(fresh))
        if expected!=actual:
            skipped.append({'number':n,'reason':'changed since snapshot'});continue
        action=change['action']
        if action=='archive_pr_only':
            payload={'title':change['new_title'],
                     'body':archival_append(old,newest,change.get('needs_retriage',False)),
                     'state':'closed','state_reason':'not_planned'}
        elif action=='archive_daily':payload={'state':'closed','state_reason':'completed'}
        elif action=='rename_only':payload={'title':change['new_title']}
        else:raise ValueError('Unknown action')
        gh.request('PATCH',BASE+f'/{n}',payload)
        performed.append({'number':n,'action':action})
    # The daily note is written after archiving and uses freshly read state,
    # so it cannot claim an archive succeeded when the API rejected it.
    if newest is not None:
        latest=gh.get(BASE+f'/{newest}')
        if 'npu-diffusion-daily' in labels(latest) and DAY_RE.search(latest.get('body') or ''):
            after=enumerate_all(gh)
            sec=summary_section(after)
            body=latest.get('body') or ''
            if DAILY_MARKER in body:
                body=re.sub(re.escape(DAILY_MARKER)+r'.*?'+re.escape(DAILY_END),
                            lambda _:sec,body,flags=re.S)
            else:
                # Put the note first: historic ghost IDs in report otherwise mislead.
                head=body.split('\n',3)
                if len(head)>=3 and head[0].startswith('<!--'):
                    body='\n'.join(head[:3])+'\n\n'+sec+'\n\n'+'\n'.join(head[3:])
                else:body=sec+'\n\n'+body
            if body!=latest.get('body'):
                gh.request('PATCH',BASE+f'/{newest}',{'body':body})
                performed.append({'number':newest,'action':'daily_migration_note'})
    return {'backup_path':str(backup),'performed':performed,'skipped':skipped}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true',help='Write GitHub changes; default is read-only preview')
    p.add_argument('--backup-dir',type=Path,default=Path('reports/issue-backups'))
    p.add_argument('--json',action='store_true',help='Machine-readable summary')
    args=p.parse_args(argv)
    if args.apply and (not os.getenv('GITHUB_TOKEN') or os.getenv('TARGET_REPOSITORY')!=REPO):
        p.error('Apply requires GITHUB_TOKEN and TARGET_REPOSITORY='+REPO)
    gh=GitHub(os.getenv('GITHUB_TOKEN'))
    issues,changes,newest=preview(gh)
    result={'mode':'apply' if args.apply else 'preview','repository':REPO,
            'scanned':len(issues),'latest_daily_issue':newest,'changes':changes,
            'counts':{k:sum(c['action']==k for c in changes) for k in
                      ('archive_pr_only','archive_daily','rename_only')}}
    if args.apply:result['result']=apply(gh,issues,changes,newest,args.backup_dir)
    if args.json:print(json.dumps(result,indent=2,ensure_ascii=False))
    else:
        print(f"{result['mode'].upper()}: {len(issues)} issues, planned {len(changes)} changes")
        for c in changes:print(f"#{c['number']:>3} {c['action']}: {c['new_title']}")
        if args.apply:print('BACKUP:',result['result']['backup_path']);print('APPLIED:',len(result['result']['performed']))
        else:print('PREVIEW ONLY. Run with --apply to close PR-only Issues administratively.')
    return 0

if __name__=='__main__':raise SystemExit(main())
