"""Research-style main-first daily report. Never invent published Issue IDs.

All counts are drawn from the bounded CI snapshot. PR leads are unverified
unless separately proven by testcase rerun and code attribution.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timedelta
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



def latest_main_partitions(data):
    try:
        from scripts.complete_report import all_jobs
        from scripts.issue_taxonomy import job_scope
    except ImportError:
        from complete_report import all_jobs
        from issue_taxonomy import job_scope
    jobs={}
    for job in all_jobs(data):
        if job_scope(job)=='main' and job.get('conclusion') in ('success',*FAILURES,'cancelled'):
            jobs.setdefault((job.get('job_id'),job.get('attempt',1)),job)
    if not jobs:return []
    latest=max(jobs.values(),key=lambda j:(j.get('created_at') or '',j.get('attempt',1)))
    return sorted((j for j in jobs.values() if
        (j.get('run_id'),j.get('attempt',1))==(latest.get('run_id'),latest.get('attempt',1))),
        key=lambda j:j.get('name',''))


def final_testcases(job):
    # Retries and repeated summaries must not multiply unique testcase totals.
    cases={}
    for observation in job.get('testcase_observations',[]):
        if observation.get('testcase'):
            cases[observation['testcase']]=observation.get('status') or 'UNKNOWN'
    return cases


def status_label(status):
    value=str(status or 'unknown')
    icon=('✅' if value.lower() in ('pass','passed','success') else
          '❌' if value.lower() in ('fail','failed','error',*FAILURES) else
          '⏳' if value.lower() in ('queued','in_progress','pending','waiting') else
          '⏭️' if value.lower() in ('skip','skipped') else
          '🚫' if value.lower()=='cancelled' else '❔')
    return icon+' '+value.replace('_',' ')


def model_label(name):
    for prefix,label in [('test_external_ar_','GLM distributed'),('wan2_1_','Wan2.1'),
            ('wan2_2_','Wan2.2'),('joyai_','JoyAI'),('qwen_image_','Qwen-Image'),
            ('minimax_h3_','MiniMax-H3'),('mova_','MOVA'),('ltx_2_','LTX-2'),
            ('flux_2_klein_','FLUX.2 Klein'),('flux_2_','FLUX.2'),('flux_image_','FLUX.1'),
            ('glm_image_','GLM-Image'),('ernie_image_','ERNIE-Image'),('z_image_','Z-Image')]:
        if name.rsplit('::',1)[-1].startswith(prefix):return label
    return name.rsplit('::',1)[-1]


def ci_results(data):
    try:
        from scripts.complete_report import cell, link, table
    except ImportError:
        from complete_report import cell, link, table
    jobs=data.get('jobs',[])
    counts=Counter(j.get('conclusion') or j.get('status') or 'unknown' for j in jobs)
    runs={(j.get('run_id'),j.get('attempt',1)) for j in jobs if j.get('run_id')}
    lines=['## CI results','',
        f'Checked **{len(jobs)} diffusion jobs** across **{len(runs)} run '+('attempt' if len(runs)==1 else 'attempts')+'**. '
        'This is a sample, not the full daily CI.','',
        table(['Job outcome','Count'],[[cell(status_label(k)),str(v)] for k,v in counts.items()]+[['**Total**',f'**{len(jobs)}**']]),'']
    peers=latest_main_partitions(data)
    if not peers:return '\n'.join(lines+['Latest executed main: unavailable; partition outcomes are unknown.'])
    first=peers[0]
    age='unknown'
    try:
        age=f"{(datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00'))-datetime.fromisoformat(first['created_at'].replace('Z','+00:00'))).total_seconds()/3600:.1f}h"
    except (ValueError,KeyError):pass
    lines += ['**Latest executed main:** '+link('run '+str(first.get('run_id')),first.get('run_url'))+
        ' · SHA `'+cell(str(first.get('head_sha') or 'unknown')[:12])+'` · '+age+' old.','']
    gates=first.get('gate_jobs') or []
    lines+=['**PR gate:** '+(', '.join(cell(status_label(g.get('conclusion') or g.get('status'))) for g in gates) or 'unknown: not retained')+'.','']
    rows=[];totals=Counter()
    for job in peers:
        config=(job.get('observation_accounting') or {}).get('configuration_evidence',[])
        suite=next((m[1] for x in config if (m:=re.search(r'--suite[=\s]+[\"\']?([\w-]+)',x.get('text','')))),None)
        partition=next((m for x in config if (m:=re.search(r'--partition-id[=\s]+[\"\']?(\d+)',x.get('text','')))),None)
        label=(suite+' / partition '+partition[1]) if suite and partition else job.get('name') or 'unknown partition'
        cases=final_testcases(job);totals.update(cases.values())
        verdicts='; '.join(cell(model_label(name))+' '+cell(status_label(status)) for name,status in cases.items()) or 'unknown: no testcase verdicts retained'
        steps=job.get('test_steps') or []
        step=', '.join(status_label(x.get('conclusion') or x.get('status')) for x in steps) or 'unknown'
        rows.append([link(label,job.get('url')),cell(status_label(job.get('conclusion')))+'<br>Tests: '+cell(step),verdicts])
    lines += [table(['Partition','Result / Run test','Testcases'],rows),'',
        '**Testcases:** '+
        (', '.join(f'{n} {status}' for status,n in sorted(totals.items(),key=lambda item:(item[0]!='PASS',item[0]))) if totals else 'unknown')+
        '. Each testcase is counted once per partition; retries are not extra tests.',
        '❔ Unknown means no verdict was retained. A passing gate or job alone does not prove testcase recovery.']
    return '\n'.join(lines)


def collection_trend(history,meta):
    today=datetime.fromisoformat(meta['date']).date()
    points={x.get('date'):x for x in list(history or [])+[meta] if x.get('date') and
        (today-timedelta(days=6)).isoformat()<=x['date']<=meta['date']}
    days=sorted(points)
    failed=[];completed=[]
    for day in days:
        counts=points[day].get('counts') or {}
        f=sum(counts.get(k,0) for k in FAILURES)
        failed.append(f);completed.append(f+counts.get('success',0))
    if len(days)<2:
        return '## Seven-day collection trend\n\nNot enough retained history for a chart ('+str(len(days))+'/7 dates). Missing dates are not zero failures.'
    maximum=max(completed+[1])
    lines=['## Seven-day collection trend','',
        'Bars: failed jobs. Line: completed jobs (success + failure). Skipped, cancelled, and active jobs are excluded.','',
        '```mermaid','xychart-beta',
        '    x-axis ['+', '.join('"'+d+'"' for d in days)+']',
        f'    y-axis "Observed jobs" 0 --> {maximum}',
        '    bar ['+', '.join(map(str,failed))+']',
        '    line ['+', '.join(map(str,completed))+']','```','',
        f'{len(days)}/7 dates have retained observations; missing dates are omitted, never filled with zero. '
        'Sampling and log coverage vary across days; this is collection volume, not a matched-testcase regression rate.']
    if any(p.get('coverage_capped') for p in points.values()):
        lines.append('At least one displayed date has capped or partial coverage.')
    return '\n'.join(lines)


def render_research_report(data,clusters,analysis,glm_status,history=None,continuity=None,include_deep=False):
    try:
        from scripts.complete_report import cause_groups, cell
    except ImportError:
        from complete_report import cause_groups, cell
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
    classified={j.get('job_id') for c in clusters for j in c.get('jobs',[])}
    missing=sum(j.get('conclusion') in FAILURES and j.get('job_id') not in classified for j in data.get('jobs',[]))
    if legacy_mismatch:missing=max(0,failed-observed)
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
           ci_results(data),'',
           '<details><summary>Collection details</summary>','',
           'Parent workflow failures and skipped partitions do not prove testcase failures.','',
           f'**{failed} failed / {passed} passed** jobs of {len(data.get("jobs",[]))} observed '
           f'({counts.get("skipped",0)} skipped, {counts.get("cancelled",0)} cancelled, {counts.get("in_progress",0)} in progress).',
           f'**Main-impact incident families:** {len(main)}. **PR-only families:** {len(pr)} '
           f'(watchlist only). **Unconfirmed:** {len(unknown)}.',
           f'**{len(clusters)} classified failure families, {testcase_observations} failing testcase observations** '
           f'across {observed} classified jobs (clusters overlap).',
           f'**Log coverage:** {data.get("log_download_succeeded",0)} successful downloads across collected scopes; '
           f'{missing} daily failed jobs lack classified evidence.',
           f'**Completed-job failure share:** {100*failed/done:.1f}% ({failed}/{done}; excludes skipped/cancelled/in progress).'
           if done else '**Completed-job failure share:** unavailable (no completed jobs).','',
           '</details>','',
           '## Main branch — priority incidents','',
           '| Problem | What happened | Job observations |',
           '|---|---|---:|']
    if main:
        for c in main[:8]:
            t=classify_cluster(c)
            sample=(c.get('jobs') or [{}])[0]
            measurements=[m for j in c.get('jobs',[]) for m in j.get('performance',[])
                          if m.get('actual_seconds') is not None]
            symptom=str(c.get('signature') or _family(c))
            detail=('Server failed to start' if 'Server exited early' in symptom else
                    'Image quality check failed' if _family(c)=='quality / GT' else
                    'Generation exceeded its time limit' if _family(c)=='performance' else cell(_txt(symptom,110)))
            if measurements:
                m=measurements[0]
                detail=f"{float(m['actual_seconds']):.2f}s actual"
                if m.get('threshold_seconds') is not None:detail+=f" / {float(m['threshold_seconds']):.2f}s limit"
            lines.append(f'| ❌ {t["priority"]} · {_link(c)} · {cell(model_label(_case_name(c)))} | '
                         f'{detail} | {c.get("count",0)} ({t["main_jobs"]} main) |')
    else:
        lines.append('| ❔ No classified main failure | Coverage may be incomplete; main is not verified green | — |')
    if len(main)>8:lines.append(f'\n*{len(main)-8} additional main families retained in local JSON.*')
    if job_only_observations:
        lines.append(f'**Job-only diagnostic observations:** {job_only_observations}; excluded from testcase totals because testcase identity is unavailable.')
    try:
        from scripts.report_actions import observation_summary, evidence_completeness, action_table, render_full_analysis, experiment_plan
    except ImportError:
        from report_actions import observation_summary, evidence_completeness, action_table, render_full_analysis, experiment_plan
    engineering=observation_summary(data,clusters)
    lines+=['','## What changed','']
    if continuity:
        for k,label in [('new','new'),('continuing','continuing'),('reappeared','reappeared'),
                        ('not_observed','not seen, not verified fixed'),('closed_since_previous','closed'),
                        ('archived_since_previous','archived PR-only (not fixed)')]:
            ids=[x for x in continuity.get(k,[]) if re.fullmatch(r'NPU-D\d{3}',str(x))]
            if not ids:continue
            icon='✅' if k=='closed_since_previous' else '❌' if k in ('new','reappeared') else '❔'
            lines.append(f'- {icon} **{label}: {len(ids)}**'+(' — '+', '.join(ids[:6]) if ids else '')+
                         (f' (+{len(ids)-6} more)' if len(ids)>6 else ''))
        if not any(continuity.get(k) for k in ('new','continuing','reappeared','not_observed','closed_since_previous','archived_since_previous')):
            lines.append('No incident changes recorded.')
        lines.append('Missing failures are not proof of recovery.')
    else:
        lines.append('❔ Incident history is unavailable; recovery is not verified.')
    lines+=['',collection_trend(history,meta),'','## Next checks','']
    plans={}
    try:
        from scripts.report_actions import experiment_plan
    except ImportError:
        from report_actions import experiment_plan
    for cluster in (main or pr)[:3]:
        action,acceptance=experiment_plan(cluster)
        plans.setdefault((action,acceptance),[]).append(model_label(_case_name(cluster)))
    for (action,acceptance),names in plans.items():
        if 'CUDA-only' in action:
            check='Check CUDA-only imports on NPU, then rerun the affected testcases with guarded imports.'
            done='The native backend starts and the original accuracy and timing checks pass.'
        elif 'Record E2E and denoise timings' in action:
            check='Compare failing and known-passing commits on the same runner and settings. Find which stage became slower.'
            done='The exact testcase meets its recorded time limit.'
        elif 'pinned GT' in action:
            check='Compare outputs against the same pinned reference with matching seed, settings, and runtime.'
            done='The original quality thresholds pass under the same configuration.'
        else:
            check='Reproduce the exact testcase at the failing commit and capture the first concrete error before changing code.'
            done='The error is explained and the testcase passes under the same configuration.'
        lines+=['- **'+cell(', '.join(names))+':** '+check,'  **Done when:** '+done]
    if not plans:lines.append('- Check missing logs before drawing a conclusion.')
    lines+=['','<details><summary>Diagnostics, evidence, and PR watchlist</summary>','',engineering,'']
    try:
        from scripts.failure_history import render_failure_history
    except ImportError:
        from failure_history import render_failure_history
    lines += ['', '<details><summary>Cross-day failure signatures</summary>', '',
              render_failure_history(data.get('failure_history') or {}), '', '</details>', '']
    accounting=[j.get('testcase_accounting',{}) for j in data.get('jobs',[]) if j.get('testcase_accounting')]
    if accounting:
        lines.append('**Testcase extraction:** '+str(sum(x.get('summary_testcases',0) for x in accounting))+
                     ' pytest FAILED/ERROR verdicts; '+str(sum(x.get('case_overflow',0) for x in accounting))+
                     ' extraction overflow records. Logs may be truncated or omit summaries.')
    try:
        from scripts.analysis_evidence import investigation_selection
    except ImportError:
        from analysis_evidence import investigation_selection
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
        for pr_link in _linked_prs(c,limit=4):lines.append('- **PR lead:** '+pr_link)
        lines.append('')
    lines+=['## PR-only watchlist (no standalone Issues)','',
            '| Testcase / scenario | Failure type | Observations | Tested PRs (not causes) | Evidence |',
            '|---|---|---:|---|---|']
    watch,total_watch,pr_observations=_pr_watch_rows(pr,8)
    lines.extend(watch or ['| — | — | 0 | — | — |'])
    if total_watch>8: lines.append(f'\n*{total_watch-8} more PR-only testcase families are stored in local JSON. No new PR-only Issues were created.*')
    if legacy_mismatch:
        lines+=['','**Legacy aggregate mismatch:** stored counters do not reconcile with retained job rows; historical coverage is incomplete. No aggregate pass rate is validated.']
    shared=cause_groups(clusters)
    for (signature,origin),observations in shared.items():
        cases=sorted({x[1] for x in observations if x[1]})
        lines+=['','**Observed import chain:** `'+cell(signature)+'` in `'+cell(origin)+'`; scenarios '+
                ', '.join('`'+cell(x)+'`' for x in cases)+
                '. This is direct import evidence. The performance effect requires a matched comparison.']
    telemetry=data.get('glm_telemetry',[])
    if telemetry:
        lines+=['','**GLM stages:** '+ '; '.join(cell(t.get('stage','unknown'))+': '+cell(t.get('status','unknown')) for t in telemetry[:8])+'.']
    lines+=['',action_table(clusters),'',
           '## Coverage and limitations','']
    lines += ['- '+_txt(x,240) for x in data.get('coverage',[])[:9]]
    if failed and not clusters:
        lines.append('**Unclassified:** zero clusters is not evidence of passing tests; inspect missing/unclear logs.')
    if glm_status.startswith('partial') or glm_status=='failed':
        lines.append('**Analysis limitation:** final GLM synthesis failed or was unavailable; only deterministic evidence is reliable.')
    lines.append('**Detailed evidence:** local JSON snapshots, redacted log context, and full GLM analysis retained on server. No unverified Issue IDs are presented as published.')
    lines+=['','</details>']
    if include_deep and analysis:
        lines += ['','<details><summary>Full GLM investigation (unverified)</summary>','',
                  render_full_analysis(analysis,clusters,14000),'','</details>']
    return '\n'.join(lines)+'\n'
