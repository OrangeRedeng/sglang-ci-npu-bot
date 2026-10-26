"""Deterministic cross-day Issue continuity, independent of LLM output.

Not seeing a previously failing testcase is NOT proof of a fix. Workflow caps,
missing job lists, skipped cases, and PR branch churn all create false greens.
"""
from __future__ import annotations

from datetime import datetime
import re

ID_RE = re.compile(r'\bNPU-D\d{3}\b')
DATE_RE = re.compile(r'<!-- npu-diffusion-report-date:(\d{4}-\d{2}-\d{2}) -->')
FP_RE = re.compile(r'<!-- npu-diffusion-fingerprint:([a-f0-9]+) -->')
LIFECYCLE_START = '<!-- npu-ci-lifecycle:start -->'
LIFECYCLE_END = '<!-- npu-ci-lifecycle:end -->'
RESOLUTION_RE = re.compile(r'<!-- npu-ci-resolution:verified;[^\n]* -->')
ARCHIVED_PR_MARKER = '<!-- npu-ci-legacy-pr-archived:v22.0.1 -->'


def _labels(issue):
    return {v.get('name') if isinstance(v, dict) else str(v)
            for v in issue.get('labels', [])}


def _id(issue):
    m = ID_RE.search(issue.get('title') or '')
    return m.group(0) if m else None


def coverage_complete(data):
    coverage = data.get('coverage', [])
    # Any failure to enumerate a workflow or load its job list invalidates a
    # negative observation (absence); never close based on such a scan.
    if not coverage or not all(any(str(s).startswith(w+': ') for s in coverage)
                                   for w in ('pr-test-npu.yml','nightly-test-npu.yml',
                                             'full-test-npu.yml','diffusion-ci-gt-gen-npu.yml')):
        return False
    bad = ('CAPPED', 'PARTIAL', 'unavailable', 'FAILED', 'ERROR')
    return not any(any(x.lower() in str(s).lower() for x in bad) for s in coverage)


def previous_report(daily_issues, date):
    candidates = []
    for issue in daily_issues:
        m = DATE_RE.search(issue.get('body') or '')
        if m and m.group(1) < date:
            candidates.append((m.group(1), issue))
    return max(candidates, key=lambda x: x[0]) if candidates else (None,None)


def build_continuity(clusters, existing_issues, daily_issues, date, data):
    """Use the previous dated report, not Issue creation timestamp, as baseline."""
    previous_date, previous = previous_report(daily_issues, date)
    previous_ids = set(ID_RE.findall((previous or {}).get('body') or ''))
    historical_ids = set()
    for old in daily_issues:
        found = DATE_RE.search(old.get('body') or '')
        if found and found.group(1) < date:
            historical_ids.update(ID_RE.findall(old.get('body') or ''))
    current = {c.get('stable_id') for c in clusters if ID_RE.fullmatch(str(c.get('stable_id') or ''))}
    issue_map = {iid: x for x in existing_issues if (iid := _id(x))}
    # Older daily reports contained ghost D IDs for clusters never published.
    # Exclude them from historical lineage; only GitHub-created incidents count.
    historical_ids.intersection_update(issue_map)
    previous_ids.intersection_update(issue_map)
    # Exact issue fingerprint provides stronger identity than name similarities.
    for c in clusters:
        cid = c.get('stable_id')
        entry = issue_map.get(cid)
        if entry:
            marker = FP_RE.search(entry.get('body') or '')
            if not marker or marker.group(1) != c.get('fingerprint'):
                raise ValueError('Fingerprint mismatch for historical incident '+str(cid))
    absent_yesterday = current - previous_ids if previous_date else current
    reappeared = sorted(absent_yesterday & historical_ids) if previous_date else []
    new = sorted(absent_yesterday - set(reappeared))
    continuing = sorted(current & previous_ids)
    not_observed = sorted({k for k,v in issue_map.items() if v.get('state')=='open'}-current)
    closed_since_previous = sorted({k for k,v in issue_map.items()
                                  if v.get('state')=='closed' and ARCHIVED_PR_MARKER not in (v.get('body') or '') and
                                  (not previous_date or str(v.get('closed_at') or '')[:10]>=previous_date)})
    archived_since_previous = sorted({k for k,v in issue_map.items()
                                  if v.get('state')=='closed' and ARCHIVED_PR_MARKER in (v.get('body') or '') and
                                  (not previous_date or str(v.get('closed_at') or '')[:10]>=previous_date)})
    rows = []
    def append(cid,status,note):
        issue = issue_map.get(cid) or {}
        rows.append({'id':cid,'url':issue.get('html_url'),'status':status,
                     'detail':note,'number':issue.get('number'),
                     'fingerprint':(FP_RE.search(issue.get('body') or '') or [None,None])[1]})
    for cid in new:
        append(cid,'new','New relative to previous daily report' if previous_date else 'No previous daily report')
    for cid in continuing:
        append(cid,'continuing','Still has a failed testcase observation in the current snapshot')
    for cid in reappeared:
        append(cid,'reappeared','Known historical fingerprint failed again after an absent report')
    for cid in not_observed:
        append(cid,'not observed' if coverage_complete(data) else 'coverage limited',
               'Not found in the current scan; no verified testcase PASS, keep open')
    for cid in closed_since_previous:
        issue=issue_map[cid]
        verified = bool(RESOLUTION_RE.search(issue.get('body') or ''))
        append(cid,'fixed (verified)' if verified else 'closed (unverified)',
               'Issue state is closed; verification marker '+('present' if verified else 'absent'))
    for cid in archived_since_previous:
        append(cid,'archived (PR-only; NOT fixed)',
               'Administratively closed to reduce noise. Original failure evidence retained; monitor in daily PR-watchlist')
    return {'date':date,'previous_date':previous_date,'coverage_complete':coverage_complete(data),
            'new':new,'continuing':continuing,'reappeared':reappeared,'not_observed':not_observed,
            'closed_since_previous':closed_since_previous,'archived_since_previous':archived_since_previous,'rows':rows}


def render_continuity(continuity):
    if not continuity: return ''
    date = continuity.get('previous_date')
    lines = ['## Incident continuity', '',
        '**Baseline:** '+('previous report '+date if date else 'no earlier daily report available')+
        '; **negative-evidence coverage:** '+('complete' if continuity['coverage_complete'] else 'limited')+'.', '',
        '| Incident | Lifecycle | Evidence / next action |',
        '|---|---|---|']
    for r in continuity.get('rows', []):
        link = '[%s](%s)' % (r['id'],r['url']) if r.get('url') else r['id']
        lines.append('| %s | %s | %s |' % (link,r['status'],r['detail']))
    if not continuity.get('rows'):
        lines.append('| — | No incidents recorded | — |')
    lines += ['', '**Closure guard:** disappearance, job-level success, PR merge, and GLM hypotheses do not prove a testcase is fixed. '
        'Close persistent incident Issues only after independently verified testcase-level passing evidence '
        'on a comparable commit, configuration and scope. Reopen on a recurrence.', '']
    return '\n'.join(lines)


def mark_verified_continuity(continuity, proofs):
    """Only a deterministic testcase proof, not model text, upgrades a status."""
    if not proofs:
        return continuity
    for row in continuity.get('rows', []):
        if row.get('id') in proofs and row.get('status') in ('not observed','coverage limited'):
            row['status']='verified fixed (pending publication)'
            row['detail']='Exact testcase PASS on two separate comparable new-commit runs; pending Issue closure'
    continuity['verified_proofs']=sorted(proofs)
    return continuity


def update_unobserved_issues(gh, continuity, existing_issues, repo):
    """Annotate missing open Issues without closing them or erasing human notes."""
    by_id={_id(issue):issue for issue in existing_issues}
    for cid in continuity.get('not_observed',[]):
        if cid in continuity.get('verified_proofs', []):
            continue
        issue=by_id.get(cid)
        if not issue or 'npu-diffusion-cluster' not in _labels(issue): continue
        body=issue.get('body') or ''
        if not FP_RE.search(body): continue
        evidence='complete' if continuity['coverage_complete'] else 'limited'
        section=(LIFECYCLE_START+'\n## Lifecycle status — '+continuity['date']+'\n\n'
            '**Not observed in latest window; NOT VERIFIED FIXED.** Collection coverage: '+evidence+'. '
            'The affected testcase has no independently verified passing rerun at a controlled SHA/configuration. '
            'This Issue remains open.\n'+LIFECYCLE_END)
        replaced=re.sub(re.escape(LIFECYCLE_START)+r'.*?'+re.escape(LIFECYCLE_END),
                        lambda _: section,body,flags=re.S)
        if replaced==body and LIFECYCLE_START not in body:
            replaced=body.rstrip()+'\n\n'+section+'\n'
        if len(replaced)>57000: continue
        if replaced!=body:
            gh.issue(int(issue['number']),{'body':replaced})


def archive_previous_daily_reports(gh, daily_issues, today):
    """Archive superseded REPORT Issues, not unresolved INCIDENT Issues."""
    archived=[]
    for issue in daily_issues:
        m=DATE_RE.search(issue.get('body') or '')
        if not m or m.group(1)>=today or issue.get('state')!='open':
            continue
        if 'npu-diffusion-daily' not in _labels(issue):continue
        gh.issue(int(issue['number']), {'state':'closed','state_reason':'completed'})
        archived.append(int(issue['number']))
    return archived
