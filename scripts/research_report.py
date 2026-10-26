"""Research-style main-first daily report. Never invent published Issue IDs.

All counts are drawn from the bounded CI snapshot. PR leads are unverified
unless separately proven by testcase rerun and code attribution.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import re

try:
    from scripts.report_editor import utc_msk, _txt, compact_checks, fix_candidates, short_hypotheses, _linked_prs, testcase
    from scripts.issue_taxonomy import classify_cluster, ordered_clusters
except ModuleNotFoundError:
    from report_editor import utc_msk, _txt, compact_checks, fix_candidates, short_hypotheses, _linked_prs, testcase
    from issue_taxonomy import classify_cluster, ordered_clusters

FAILURES=('failure','timed_out','startup_failure','action_required')


def _case_name(cluster):
    name=testcase(cluster) or cluster.get('signature') or 'Unclassified'
    if '::' in name: name=name.rsplit('::',1)[-1]
    name=re.sub(r'^\[\d+-\d+\s+\d+:\d+:\d+\]\s*','',str(name))
    return _txt(name,87)


def _family(cluster):
    job=(cluster.get('jobs') or [{}])[0]
    name=_case_name(cluster)
    cat=str(job.get('failure_category') or 'unclassified')
    if cat=='diffusion_consistency': return 'quality / GT'
    if cat=='performance_regression' or 'PerformanceValidationError' in cluster.get('signature',''):
        return 'performance'
    if cat=='npu_oom': return 'OOM'
    if cat=='ascend_runtime': return 'NPU runtime'
    if re.search(r'HTTPError|500 Server Error|Scheduler|server error',str(job.get('expanded_evidence') or '')+str(cluster.get('signature') or ''),re.I):
        return 'server / inference'
    if cat in ('pytest_failure','diffusion_test_failure'): return 'test failure'
    return 'unclassified'


def _link(cluster):
    url=cluster.get('issue_url')
    ident=cluster.get('stable_id') or ''
    if (isinstance(url,str) and re.fullmatch(r'https://github\.com/sglang-ci-npu-bot/sglang-ci-npu-bot/issues/\d+',url)
            and re.fullmatch(r'NPU-D\d{3}',ident)):
        return f'[{ident}]({url})'
    return 'Observation (no Issue)'


def _pr_watch_rows(clusters, max_rows=8):
    grouped=defaultdict(lambda: {'count':0,'prs':set(),'runs':[]})
    for c in clusters:
        for j in c.get('jobs',[]):
            if classify_cluster({'jobs':[j]})['scope'] != 'not-merged': continue
            key=(_case_name(c),_family(c)+' · '+str(c.get('signature') or '')[:180])
            row=grouped[key]; row['count']+=1
            row['prs'].update(n for n in (j.get('pr_numbers') or []) if isinstance(n,int) and n>0)
            if j.get('url') and len(row['runs'])<2:row['runs'].append(j['url'])
    rows=sorted(grouped.items(),key=lambda kv:(-kv[1]['count'],kv[0]))
    body=[]
    for (case,family),v in rows[:max_rows]:
        prs=', '.join(f'[#{n}](https://github.com/sgl-project/sglang/pull/{n})' for n in sorted(v['prs'])[:3]) or 'not identified'
        job_link=f'[sample job]({v["runs"][0]})' if v['runs'] else 'unavailable'
        body.append(f'| `{case}` | {family} | {v["count"]} | {prs} | {job_link} |')
    return body,len(rows),sum(row['count'] for row in grouped.values())


def _pr_matrix(clusters, max_rows=8):
    entries={}
    for c in clusters:
        for lead in c.get('pr_leads',[]):
            n=lead.get('number');u=lead.get('url')
            if not isinstance(n,int) or u!=f'https://github.com/sgl-project/sglang/pull/{n}':continue
            if n not in entries: entries[n]=lead
    rank={'tested_branch':0,'possible_cause':1,'potential_fix':2,'related_change':3}
    sorted_leads=sorted(entries.values(),key=lambda p:(rank.get(p.get('relation'),4),p['number']))
    lines=[]
    for pr in sorted_leads[:max_rows]:
        relation={'tested_branch':'Tested branch, not cause',
                  'possible_cause':'Suspected regression, unverified',
                  'potential_fix':'Possible fix, unverified',
                  'related_change':'Related code, unverified'}.get(pr.get('relation'),'Unverified')
        paths=', '.join('`'+_txt(x.rsplit('/',1)[-1],42)+'`' for x in pr.get('relevant_files',[])[:2]) or 'Not verified'
        lines.append(f"| [#{pr['number']}]({pr['url']}) | {_txt(pr.get('title'),70)} | "
                     f"{_txt(pr.get('status'),16)} · CI: {_txt(pr.get('ci_status','not checked'),38)} at `{str(pr.get('ci_sha') or 'unknown')[:12]}` | "
                     f"{_txt(pr.get('author') or 'unknown',22)} · {_txt(str(pr.get('updated_at') or 'unknown')[:10],16)} | {relation} | {paths} |")
    return lines,len(entries)


def render_research_report(data,clusters,analysis,glm_status,history=None,continuity=None,include_deep=False):
    try:
        from scripts.complete_report import main_assessment, cause_groups, cell
    except ImportError:
        from complete_report import main_assessment, cause_groups, cell
    now=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00'))
    date=now.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    counts=Counter(data.get('counts') or {})
    materialized=Counter(j.get('conclusion') or 'unknown' for j in data.get('jobs',[]))
    legacy_mismatch=bool(counts and materialized!=counts and data.get('schema_version',0)<3)
    failed=sum(counts.get(k,0) for k in FAILURES)
    passed=counts.get('success',0)
    done=failed+passed
    if legacy_mismatch:done=0
    observed=len({j.get('job_id') for c in clusters for j in c.get('jobs',[]) if j.get('job_id') is not None})
    missing=max(0,failed-observed)
    testcase_observations=sum(1 for c in clusters for j in c.get('jobs',[]) if j.get('primary_test_case'))
    job_only_observations=sum(1 for c in clusters for j in c.get('jobs',[]) if not j.get('primary_test_case'))
    main=ordered_clusters([c for c in clusters if classify_cluster(c)['scope']=='main'])
    pr=[c for c in clusters if classify_cluster(c)['scope']=='not-merged']
    unknown=[c for c in clusters if classify_cluster(c)['scope']=='unconfirmed']
    cap=any('CAPPED' in str(x) for x in data.get('coverage',[]))
    meta={'date':date,'counts':data.get('counts',{}),'clusters':len(clusters),
          'jobs_inspected':len(data.get('jobs',[])),'coverage_capped':cap}
    lines=[f'<!-- npu-diffusion-report-date:{date} -->',
           '<!-- npu-diffusion-metrics: '+json.dumps(meta,separators=(',',':'))+' -->',
           '<!-- npu-ci-report-language:en -->',
           '# NPU Diffusion CI — '+date,'',
           f'**Snapshot:** {utc_msk(data["generated_utc"])} · **Window:** {data.get("lookback_hours",24)}h · '
           f'**GLM:** `{glm_status}`','',
           '## Executive summary','', main_assessment(data),'',
           f'**{failed} failed / {passed} passed** jobs of {len(data.get("jobs",[]))} observed '
           f'({counts.get("skipped",0)} skipped, {counts.get("cancelled",0)} cancelled, {counts.get("in_progress",0)} in progress).',
           f'**Main-impact incident families:** {len(main)}. **PR-only families:** {len(pr)} '
           f'(watchlist only). **Unconfirmed:** {len(unknown)}.',
           f'**{len(clusters)} classified failure families, {testcase_observations} failing testcase observations** '
           f'across {observed} classified jobs (clusters overlap).',
           f'**Log coverage:** {data.get("log_download_succeeded",0)}/{failed} failed-job logs; '
           f'{missing} remain unclassified at the job level.' if missing else
           f'**Log coverage:** {data.get("log_download_succeeded",0)}/{failed} failed-job logs; 0 remain unclassified.',
           f'**Completed-job failure share:** {100*failed/done:.1f}% ({failed}/{done}; excludes skipped/cancelled/in progress).'
           if done else '**Completed-job failure share:** unavailable (no completed jobs).','',
           '## Main branch — priority incidents','',
           '| Priority | Validated evidence | Failure observations | First observed |',
           '|---|---|---:|---|']
    if main:
        for c in main[:8]:
            t=classify_cluster(c)
            sample=(c.get('jobs') or [{}])[0]
            lines.append(f'| {t["priority"]} | {_link(c)} · `{_case_name(c)}` ({_family(c)}) | '
                         f'{c.get("count",0)} ({t["main_jobs"]} main) | {utc_msk(c.get("first_seen_snapshot"))} |')
    else:
        lines.append('| — | No classified main-branch failures in the observed window; not proof main is green | — | — |')
    if len(main)>8:lines.append(f'\n*{len(main)-8} additional main families retained in local JSON.*')
    if job_only_observations:
        lines.append(f'**Job-only diagnostic observations:** {job_only_observations}; excluded from testcase totals because testcase identity is unavailable.')
    try:
        from scripts.report_actions import observation_summary, scope_execution, evidence_completeness, action_table, render_full_analysis, experiment_plan
    except ImportError:
        from report_actions import observation_summary, scope_execution, evidence_completeness, action_table, render_full_analysis, experiment_plan
    lines+=['',observation_summary(data,clusters),'',scope_execution(data),'']
    lines+=['','## Lifecycle and continuity','']
    if continuity:
        for k,label in [('new','new'),('continuing','continuing'),('reappeared','reappeared'),
                        ('not_observed','not seen, not verified fixed'),('closed_since_previous','closed'),
                        ('archived_since_previous','archived PR-only (not fixed)')]:
            ids=[x for x in continuity.get(k,[]) if re.fullmatch(r'NPU-D\d{3}',str(x))]
            lines.append(f'- **{label}: {len(ids)}**'+(' — '+', '.join(ids[:6]) if ids else '')+
                         (f' (+{len(ids)-6} more)' if len(ids)>6 else ''))
        lines.append('A missing failure in a capped scan is not a testcase-level PASS. No closure without equivalent PASS evidence.')
    else:
        lines.append('Issue lifecycle comparison unavailable; snapshot symptom history is shown separately below.')
    try:
        from scripts.failure_history import render_failure_history
    except ImportError:
        from failure_history import render_failure_history
    lines += ['', render_failure_history(data.get('failure_history') or {}), '']
    accounting=[j.get('testcase_accounting',{}) for j in data.get('jobs',[]) if j.get('testcase_accounting')]
    if accounting:
        lines.append('**Testcase extraction:** '+str(sum(x.get('summary_testcases',0) for x in accounting))+
                     ' pytest FAILED/ERROR verdicts; '+str(sum(x.get('case_overflow',0) for x in accounting))+
                     ' extraction overflow records. Logs may be truncated or omit summaries.')
    try:
        from scripts.analysis_evidence import investigation_selection, render_evidence
    except ImportError:
        from analysis_evidence import investigation_selection, render_evidence
    selected=investigation_selection(clusters)
    pr_deep=[c for c in selected if classify_cluster(c)['scope']=='not-merged']
    lines+=['','## Priority regression investigations','']
    if not main:
        lines.append('No classified main failure selected. Root cause remains unverified.')
        if pr_deep:lines.append(f'{len(pr_deep)} recurring PR-only families qualify for investigation; this does not establish main impact or create Issues.')
        if unknown:
            lines.append('Unconfirmed provenance — not independently actionable:')
            for c in unknown[:2]:
                lines.append(f'- `{_case_name(c)}` — {c.get("count",0)} failure observations; no Issue creation')
    for c in (main+pr_deep)[:5]:
        job=(c.get('jobs') or [{}])[0];ident=_link(c);cid=c.get('stable_id') or ''
        lines+=[f'### {ident}: {_case_name(c)}','',
                '**Scope / selection:** '+classify_cluster(c)['scope']+' / '+c.get('investigation_reason','main failure')+'.',
                '**Diagnostic symptom (FACT, mechanism unverified):** `'+_txt(c.get('signature','unknown'),700)+'`.',
                f'**Observed:** {compact_checks(job)} · {c.get("count",0)} failure observations.',
                f'**First observed:** {utc_msk(c.get("first_seen_snapshot"))} (not a proven regression onset).',
                f'**Evidence:** [job {job.get("job_id","?")}]({job.get("url","https://github.com/sgl-project/sglang/actions")}).',
                '**Root cause:** unverified; no last testcase-level green/first bad boundary has been established.']
        lines+=['',evidence_completeness(c),'']
        if job.get('http_statuses'):
            lines.append('**Observed HTTP statuses in this testcase:** '+', '.join(map(str,job['http_statuses']))+'. Their presence alone does not establish a causal chain.')
        hypotheses=short_hypotheses(analysis,cid,cluster=c)
        if hypotheses: lines+=['','**Mechanistic hypotheses (unverified):**']+['- '+x for x in hypotheses]
        lines+=['','**Potential fixes / decisive checks (require verification):**']
        action,acceptance=experiment_plan(c)
        lines.append('- **Experiment:** '+action)
        lines.append('- **Acceptance:** '+acceptance)
        for pr in _linked_prs(c,limit=4):lines.append('- **PR lead:** '+pr)
        lines.append('')
    lines+=['## PR-only watchlist (no standalone Issues)','',
            '| Testcase / scenario | Failure type | Observations | Tested PRs (not causes) | Evidence |',
            '|---|---|---:|---|---|']
    watch,total_watch,pr_observations=_pr_watch_rows(pr,8)
    lines.extend(watch or ['| — | — | 0 | — | — |'])
    if total_watch>8: lines.append(f'\n*{total_watch-8} more PR-only testcase families are stored in local JSON. No new PR-only Issues were created.*')
    lines+=['','## Verified PR metadata — relationships are not causal claims','',
           '| PR | Title | State / CI | Author / updated | Relationship | Relevant changed files |',
           '|---|---|---|---|---|---|']
    prs,n_prs=_pr_matrix(main+pr,8)
    lines.extend(prs or ['| — | No verified source PR metadata available | — | — | — | — |'])
    if legacy_mismatch:
        lines+=['','**Legacy aggregate mismatch:** stored counters do not reconcile with retained job rows; historical coverage is incomplete. No aggregate pass rate is validated.']
    shared=cause_groups(clusters)
    for (signature,origin),observations in shared.items():
        cases=sorted({x[1] for x in observations if x[1]})
        lines+=['','**Observed import chain:** `'+cell(signature)+'` in `'+cell(origin)+'`; scenarios '+
                ', '.join('`'+cell(x)+'`' for x in cases)+
                '. This is direct import evidence. The performance effect requires a matched comparison.']
    if n_prs>8:lines.append(f'\n*{n_prs-8} additional verified PR leads in local analysis.*')
    lines+=['',render_evidence(data,clusters),'']
    lines+=['','## Seven-day collection trend','',
           '| Date | Completed jobs | Failed | Completed-job failure share | Cluster families | Coverage |',
           '|---|---:|---:|---:|---:|---|']
    points=list(history or [])+[meta]
    uniq={x.get('date'):x for x in points if x.get('date')}
    for day in sorted(uniq,reverse=True)[:7]:
        item=uniq[day];count=item.get('counts') or {}
        f=sum(count.get(k,0) for k in FAILURES);d=f+count.get('success',0)
        lines.append(f"| {day} | {d} | {f} | {(f'{100*f/d:.1f}%' if d else 'n/a')} | "
                     f"{item.get('clusters','?')} | {'capped / partial' if item.get('coverage_capped') else 'query window'} |")
    lines+=['','**Comparability warning:** workflow sampling, testcase execution, PR composition, and job-log caps may differ across days. These are observed jobs, not a controlled matched-testcase trend.','',
           action_table(clusters),'',
           '## Coverage and limitations','']
    lines += ['- '+_txt(x,240) for x in data.get('coverage',[])[:9]]
    if failed and not clusters:
        lines.append('**Unclassified:** zero clusters is not evidence of passing tests; inspect missing/unclear logs.')
    if glm_status.startswith('partial') or glm_status=='failed':
        lines.append('**Analysis limitation:** final GLM synthesis failed or was unavailable; only deterministic evidence is reliable.')
    lines.append('**Detailed evidence:** local JSON snapshots, redacted log context, and full GLM analysis retained on server. No unverified Issue IDs are presented as published.')
    if include_deep and analysis:
        lines += ['','<details><summary>Full GLM investigation (unverified)</summary>','',
                  render_full_analysis(analysis,clusters,14000),'','</details>']
    return '\n'.join(lines)+'\n'
