"""Complete local report and shared evidence summaries for the concise Issue."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import re
try:
    from scripts.issue_taxonomy import job_scope, classify_cluster
    from scripts.ci_investigation import build_regression_context
    from scripts.report_actions import experiment_plan, render_full_analysis
except ImportError:
    from issue_taxonomy import job_scope, classify_cluster
    from ci_investigation import build_regression_context
    from report_actions import experiment_plan, render_full_analysis


def cell(value):
    if value is None or value=='':return 'unknown'
    if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False,sort_keys=True)
    return str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','&#124;').replace('\n','<br>')


def link(label,url):
    if isinstance(url,str) and re.fullmatch(r'https://github\.com/[A-Za-z0-9_./%#?=&+-]+',url):
        return f'[{cell(label)}]({url})'
    return cell(label)


def table(headers,rows):
    result=['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|']
    result+=['| '+' | '.join(str(x) for x in row)+' |' for row in rows]
    if len(result)==2:result.append('| '+' | '.join('unknown' for _ in headers)+' |')
    return '\n'.join(result)


def all_jobs(data):
    return data.get('jobs',[])+data.get('comparison_jobs',[])


def measurement_text(m):
    return (str(m.get('metric','unknown'))+': actual / baseline / limit = '+
            ' / '.join(str(m.get(k,'unknown')) for k in ('actual_seconds','baseline_seconds','threshold_seconds'))+
            ' s; relative tolerance '+str(m.get('tolerance_pct','unknown'))+'%; absolute pad '+
            str(m.get('absolute_pad_seconds','unknown'))+' s')


def boundary_text(obs):
    if not obs:return 'unknown'
    return link(obs.get('job_id'),obs.get('url'))+' / '+cell(obs.get('sha'))+' / '+cell(obs.get('created_at'))+'; profile equivalence unverified'


def main_assessment(data):
    mains=[j for j in all_jobs(data) if job_scope(j)=='main' and j.get('conclusion') in
           ('success','failure','timed_out','startup_failure','action_required','cancelled')]
    if not mains:return 'Latest executed main: unknown; no verified main execution retained.'
    latest=max(mains,key=lambda j:j.get('created_at') or '')
    peers=[j for j in mains if j.get('run_id')==latest.get('run_id') and j.get('attempt',1)==latest.get('attempt',1)]
    counts=Counter(j.get('conclusion','unknown') for j in peers)
    age='unknown'
    try:
        age=f"{(datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00'))-datetime.fromisoformat(latest['created_at'].replace('Z','+00:00'))).total_seconds()/3600:.1f}h"
    except (ValueError,KeyError):pass
    verdicts=Counter(o['status'] for j in peers for o in j.get('testcase_observations',[]) if o.get('status'))
    return ('Latest executed main: '+link(f"run {latest.get('run_id')}",latest.get('run_url'))+
            f" · SHA `{latest.get('head_sha') or 'unknown'}` · age {age} · job outcomes {dict(counts)} · "
            f"observed testcase attempts {dict(verdicts) if verdicts else 'unknown'}. "
            'Complete registered testcase coverage and production performance require explicit execution/configuration evidence.')


def cause_groups(clusters):
    groups=defaultdict(list)
    for c in clusters:
        for j in c.get('jobs',[]):
            for o in j.get('execution_observations',[]):
                if o.get('causal_signature') and o.get('causal_origin'):
                    key=(o['causal_signature'],o['causal_origin'])
                    item=(c.get('stable_id') or 'OBS-'+c['fingerprint'],j.get('primary_test_case'),j.get('job_id'))
                    if item not in groups[key]:groups[key].append(item)
    return groups


def render_complete_report(data,clusters,analysis,glm_status,history=None,continuity=None):
    investigation_clusters=clusters+data.get('historical_main_clusters',[])
    now=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00'))
    date=now.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    counts=data.get('counts',{})
    materialized=dict(Counter(j.get('conclusion') or 'unknown' for j in data.get('jobs',[])))
    lines=[f'<!-- npu-diffusion-report-date:{date} -->','<!-- npu-ci-report-language:en -->',
           '<!-- npu-diffusion-metrics: '+json.dumps({'date':date,'counts':counts,'clusters':len(clusters),
               'jobs_inspected':len(data.get('jobs',[])),
               'coverage_capped':any('CAPPED' in str(x) for x in data.get('coverage',[]))},separators=(',',':'))+' -->',
           f'# SGLang Diffusion / Ascend NPU — Daily CI Report — {date}',
           f'**Snapshot:** {now.isoformat()} / {now.astimezone(ZoneInfo("Europe/Moscow")).isoformat()}',
           f'**Window:** {data.get("window_start_utc") or (now-timedelta(hours=data.get("lookback_hours",24))).isoformat()} → {data["generated_utc"]}',
           f'**Analysis:** {cell(glm_status)}','', '## 1. Assessment and changes','', main_assessment(data),'',
           f'Daily materialized job outcomes: {cell(materialized)}. Comparison runs are excluded from these counts.',
           ('**Legacy aggregate mismatch:** stored counters '+cell(counts)+' do not reconcile with retained job rows; '
            'historical coverage is incomplete and no aggregate pass rate is validated.' if counts and counts!=materialized else ''),'',
           '## 2. Methodology and coverage','',
           'Workflow, job, test-step, and testcase outcomes are separate. Runner capacity does not establish suite device count. '
           'PASS observations prove the named execution completed its gate; configuration equivalence and incident closure require stronger evidence.',
           '',*[ '- '+cell(x) for x in data.get('coverage',[])],
           '- Log selection: '+cell(data.get('log_collection')),
           '- Source/artifact collection: '+cell((data.get('source_evidence') or {}).get('limitations',[])),
           '', '## 3. Run ledger','']
    by_run=defaultdict(list)
    for j in all_jobs(data):by_run[(j.get('run_id'),j.get('attempt',1))].append(j)
    lines.append(table(['Run / attempt','Created UTC','Branch / SHA','Event / parent outcome','Observed target jobs','Use'],[
        [link(f'{rid} / {attempt}',jobs[0].get('run_url')),cell(jobs[0].get('created_at')),
         cell(jobs[0].get('head_branch'))+' / '+cell(jobs[0].get('head_sha')),
         cell(jobs[0].get('run_event'))+' / '+cell(jobs[0].get('run_conclusion')),
         cell(dict(Counter(j.get('conclusion','unknown') for j in jobs))),
         'historical comparison' if jobs[0].get('comparison_only') else 'daily window']
        for (rid,attempt),jobs in by_run.items()]))
    for run in data.get('runs',[]):
        if not any(key[0]==run.get('id') for key in by_run):
            lines.append('- '+link(run.get('id'),run.get('html_url'))+': no target jobs materialized or job list unavailable; see collection limitations.')
    lines+=['','## 4. Job ledger','',table(
        ['Job / exact partition','Run / attempt','Scope / SHA','Job status / conclusion','Run test step','Use'],[
        [link(j.get('name') or j.get('job_id'),j.get('url')),cell(j.get('run_id'))+' / '+cell(j.get('attempt',1)),
         cell(job_scope(j))+' / '+cell(j.get('head_sha')),cell(j.get('status'))+' / '+cell(j.get('conclusion')),
         cell(j.get('test_steps')), 'comparison' if j.get('comparison_only') else 'daily'] for j in all_jobs(data)]),
        '', '## 5. Model and testcase ledger','']
    rows=[]
    for j in all_jobs(data):
        for o in j.get('testcase_observations',[]):
            rows.append([cell(o['testcase']),cell(o.get('execution_attempt')),cell(o['status']),
                         '<br>'.join(cell(measurement_text(m)) for m in o.get('performance',[])) or 'unknown',cell(o.get('metrics')),
                         link(f"{j.get('job_id')} lines {o.get('start_line')}–{o.get('end_line')}",j.get('url'))])
        for c in j.get('failure_cases',[]):
            rows.append([cell(c.get('testcase')),'pytest report sections','FAIL',
                         '<br>'.join(cell(measurement_text(m))+f" (section {m.get('section_attempt','unknown')})" for m in c.get('performance',[])) or 'unknown',
                         cell(c.get('metrics')),link(j.get('job_id'),j.get('url'))])
    lines += [table(['Testcase','Attempt / evidence','Outcome','Timing evidence (seconds)','Accuracy metrics','Source'],rows),
              '', 'Attempts and pytest report sections can describe the same execution; ledger rows must not be summed as unique tests. '
              'No observed testcase rows means unknown execution. Missing timing/accuracy fields remain unknown.',
              '', '## 6. Failure investigations','']
    for c in investigation_clusters:
        scope=classify_cluster(c); ident=c.get('stable_id') or 'OBS-'+c['fingerprint']
        lines += [f'### {ident} — {cell(c.get("signature"))}',
                  f'**FACTS:** {scope["priority"]} / {scope["scope"]}; '+('historical comparison, excluded from daily counts; ' if c.get('comparison_only') else '')+f'{c.get("count",0)} overlapping job observations. '
                  f'First observed {cell(c.get("first_seen_snapshot"))}; last observed {cell(c.get("last_seen_snapshot"))}.']
        for j in c.get('jobs',[]):
            lines.append('- '+link(j.get('job_id'),j.get('url'))+' · SHA '+cell(j.get('head_sha'))+
                         ' · diagnostic '+cell(j.get('error_signature'))+' · import exception '+cell(j.get('causal_signature')))
        representative=(c.get('jobs') or [{}])[0]
        chains=[x for o in representative.get('execution_observations',[])[:1] for x in o.get('exception_chain',[])][:6]
        if chains:
            lines+=['Representative original exception chain (abbreviated):','```text',
                    *[f"log line {x['line']}: {x['text']}" for x in chains],'```']
        action,acceptance=experiment_plan(c)
        lines += ['**HYPOTHESES:** Mechanism and causal boundary require the cited evidence and disconfirming checks. '
                  'Conditional model analysis appears below when available.',
                  '**Decisive check:** '+action, '**Acceptance / exit criterion:** '+acceptance,'']
    groups=cause_groups(investigation_clusters)
    for (signature,origin),items in groups.items():
        if len({x[1] for x in items})>1:
            lines.append('**Shared import evidence:** '+cell(signature)+' at '+cell(origin)+'; observations '+cell(items)+
                         '. This links the import path; a co-occurring latency failure still requires a controlled causal check.')
    if analysis:lines+=['',render_full_analysis(analysis,investigation_clusters,100000)]
    lines+=['','## 7. Incident history and verification','']
    context=build_regression_context(data,investigation_clusters)
    lines.append(table(['ID','First / last observed','Prior testcase PASS','First observed testcase FAIL','Closure evidence'],[
        [cell(c.get('stable_id') or 'OBS-'+c['fingerprint']),cell(c.get('first_seen_snapshot'))+' / '+cell(c.get('last_seen_snapshot')),
         boundary_text(ctx.get('last_verified_green_testcase')),boundary_text(ctx.get('first_verified_bad_testcase')),
         'not established by ordinary log verdicts'] for c,ctx in zip(investigation_clusters,context['clusters'])]))
    lines.append('Lifecycle: '+cell(continuity or data.get('failure_history') or 'unavailable; missing observations are not verified recovery'))
    lines+=['','## 8. Relevant PRs, commits, and source configuration','']
    prs=[]
    for c in investigation_clusters:
        for p in c.get('pr_leads',[]):
            prs.append([link(p.get('number'),p.get('url')),cell(p.get('relationship') or p.get('relation')),
                        cell(p.get('state') or p.get('status'))+' / '+cell(p.get('merged_at')),cell(p.get('head_sha')),
                        cell(p.get('tested_shas')),cell(p.get('ci_sha'))+' / '+cell(p.get('ci_status'))])
    lines.append(table(['PR','Relationship','State / merge time','Current head','Observed tested SHAs','CI SHA / result'],prs))
    lines.append('Current PR metadata is checked at collection time; it is not historical state at an earlier snapshot. '
                 'A passing older SHA does not validate the current patch. PR association alone establishes no cause or fix.')
    evidence=data.get('source_evidence') or {}
    for j in all_jobs(data):
        config=j.get('observation_accounting',{}).get('configuration_evidence',[])
        if config:lines+=['',link('Observed runtime/suite evidence, job '+str(j.get('job_id')),j.get('url')),
                         '```text',*[f"log line {x['line']}: {x['text']}" for x in config],'```']
    for sha,sources in evidence.get('sources',{}).items():
        for path,source in sources.items():
            lines+=['',link(path+' @ '+sha,source.get('url')),
                    '```text',*[f"{x['line']}: {x['text']}" for x in source.get('excerpt',[])],'```']
    lines+=['','## 9. Daily and seven-calendar-day comparison','']
    days={x.get('date'):x for x in history or [] if x.get('date')}
    days[date]={'counts':counts,'clusters':len(clusters)}
    rows=[]
    for i in range(7):
        day=(now.astimezone(ZoneInfo('Europe/Moscow')).date()-timedelta(days=i)).isoformat(); h=days.get(day,{})
        rows.append([day,cell(h.get('counts')),cell(h.get('clusters')),
                     'observed sample; configuration/coverage may differ' if h else 'no retained daily evidence'])
    lines+=[table(['Moscow date','Job outcomes','Families','Comparability'],rows),
            'Cross-day testcase history: '+cell(data.get('failure_history')),
            '', '## 10. Prioritized actions and closure criteria','']
    lines.append(table(['Priority / scope','Scenario','Proposed check','Acceptance'],[
        [cell(classify_cluster(c)['priority']+' / '+classify_cluster(c)['scope']),
         cell((c.get('jobs') or [{}])[0].get('primary_test_case')),
         cell(experiment_plan(c)[0]),cell(experiment_plan(c)[1])] for c in investigation_clusters]))
    lines+=['','## 11. References, limitations, and data integrity','',
            'All run and job links above refer to retained observations. Historical comparisons are excluded from daily denominators. '
            'Successful artifact listing/upload does not prove artifact contents or passing tests.',
            'Artifact listings: '+cell(evidence.get('runs')),
            'Collection errors: '+cell(data.get('errors',[])),
            'GLM status: '+cell(glm_status)+'. Completed investigations remain conditional analysis; unavailable synthesis does not erase deterministic evidence.',
            'Local Markdown is a complete report artifact, not a public download URL. No unseen logs, runtime versions, or GT identity are inferred.']
    return '\n\n'.join(lines)+'\n'


def write_complete_companion(path,data,clusters,analysis,status,history=None,continuity=None):
    full=render_complete_report(data,clusters,analysis,status,history,continuity)
    validate_report(data,full)
    output=path.with_name(path.stem+'-complete.md')
    output.write_text(full,encoding='utf-8');output.chmod(0o600)
    return output


def validate_report(data,text):
    if not text.strip() or len(re.findall(r'^## \d+\.',text,re.M))!=11:
        raise ValueError('Complete report must contain eleven sections')
    actual=Counter(j.get('conclusion') or 'unknown' for j in data.get('jobs',[]))
    if data.get('counts') and dict(actual)!=data['counts']:
        if data.get('schema_version',0)>=3 or 'Legacy aggregate mismatch' not in text:
            raise ValueError('Job ledger and reported counts disagree')
    seen=set()
    for j in all_jobs(data):
        identity=(j.get('job_id'),j.get('attempt',1))
        if identity[0] is not None and identity in seen:raise ValueError('Duplicate job attempt')
        seen.add(identity)
        if j.get('created_at') and j['created_at']>data['generated_utc']:
            raise ValueError('Future run in snapshot')
        if (j.get('completed_at') and j['completed_at']>data['generated_utc']
                and j.get('conclusion') not in ('queued','in_progress','waiting','pending','unknown')):
            raise ValueError('Future job outcome in snapshot')
    if re.search(r'\]\((?:file:|/home/|/tmp/)',text):raise ValueError('Local path presented as report link')
    return True
