"""Deterministic P1/P2 evidence. Missing measurements remain unknown."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import re


def investigation_selection(clusters):
    try:
        from scripts.issue_taxonomy import classify_cluster, ordered_clusters
    except ImportError:
        from issue_taxonomy import classify_cluster, ordered_clusters
    selected=[]
    for c in ordered_clusters(clusters):
        scope=classify_cluster(c)['scope']
        jobs=c.get('jobs',[])
        prs={n for j in jobs if j.get('run_event') in ('pull_request','pull_request_target')
             for n in j.get('pr_numbers',[]) if type(n) is int and n>0}
        runs={j.get('run_id') or j.get('job_id') for j in jobs} - {None}
        count=len({j.get('job_id') for j in jobs} - {None})
        critical=any(re.search(r'glm.*image|wan|mini.?max|qwen.*image', str(j.get('primary_test_case','')),re.I) for j in jobs)
        reason=('main failure' if scope=='main' else
                'recurs across independent PRs' if scope=='not-merged' and len(prs)>=2 and count>=3 else
                'frequent failure across runs' if scope=='not-merged' and count>=10 and len(runs)>=3 else
                'repeated critical scenario' if scope=='not-merged' and critical and count>=3 and len(runs)>=2 else None)
        if reason:
            c['investigation_reason']=reason
            selected.append(c)
    return selected


NUM=r'([0-9]+(?:\.[0-9]+)?)'
def performance_measurements(lines):
    """Parse explicit latency assertions, including SGLang Actual/Expected/Limit.

    Expected is treated as baseline only in a performance validation block.
    Declared relative tolerance and absolute padding remain distinct.
    """
    text=' '.join(lines)
    named=list(re.finditer(r"Validation failed for\s+['\"]([^'\"]+)['\"]",text,re.I))
    if named:
        parts=[(m[1],text[m.start():named[i+1].start() if i+1<len(named) else len(text)]) for i,m in enumerate(named)]
    else:
        parts=[(None,p) for p in re.split(r'(?i)(?=\b(?:E2E|denoising|decoding|encoding|TTFT)\s+(?:latency|actual|time))',text)]
    result=[]
    for metric,part in parts:
        performance_block=bool(metric or re.search(r'PerformanceValidationError|\[performance\]',part,re.I))
        vals={}
        baseline=r'(?:baseline(?:\s+(?:latency|time))?'+(r'|expected' if performance_block else '')+')'
        for key,labels in [('actual',r'actual(?:\s+(?:latency|time))?'),
                           ('baseline',baseline),
                           ('threshold',r'(?:threshold|limit|maximum|allowed(?:\s+max(?:imum)?)?)')]:
            m=re.search(r'\b'+labels+r'\s*[:=]?\s*'+NUM+r'\s*(ms|seconds?|secs?|s)\b',part,re.I)
            if m: vals[key+'_seconds']=float(m[1])/(1000 if m[2].lower()=='ms' else 1)
        if 'actual_seconds' not in vals: continue
        base=vals.get('baseline_seconds')
        if base and base>0:
            vals['actual_baseline_ratio']=round(vals['actual_seconds']/base,6)
            vals['relative_regression_pct']=round((vals['actual_seconds']/base-1)*100,3)
        limit=vals.get('threshold_seconds')
        if limit and limit>0:vals['actual_limit_ratio']=round(vals['actual_seconds']/limit,6)
        tol=re.search(r'\b(?:tolerance|rel_tol)\s*[:=]?\s*'+NUM+r'\s*%',part,re.I)
        pad=re.search(r'\babs_pad\s*[:=]?\s*'+NUM+r'\s*(ms|seconds?|secs?|s)\b',part,re.I)
        if pad:vals['absolute_pad_seconds']=float(pad[1])/(1000 if pad[2].lower()=='ms' else 1)
        if tol:
            vals['tolerance_pct']=float(tol[1]);vals['tolerance_source']='declared relative tolerance'
        elif base and base>0 and limit is not None:
            vals['tolerance_pct']=round((limit/base-1)*100,3)
            vals['tolerance_source']='derived effective limit ratio (may include absolute pad)'
        name=re.match(r'(E2E|denoising|decoding|encoding|TTFT)',part,re.I)
        vals['metric']=metric or (name[1] if name else 'latency (stage unspecified)')
        vals['evidence']=part[:900]
        if vals not in result:result.append(vals)
    return result[:12]


def coverage_matrix(data, history=()):
    """Observed workflow/scenario/scope by day; job PASS is never testcase PASS."""
    try:
        from scripts.issue_taxonomy import job_scope
    except ImportError:
        from issue_taxonomy import job_scope
    rows=defaultdict(Counter); seen=set()
    now=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00'))
    for snap in [data]+list(history):
        try: ts=datetime.fromisoformat(snap['generated_utc'].replace('Z','+00:00'))
        except (KeyError,ValueError): continue
        if ts>now or ts<now-timedelta(days=7):continue
        day=ts.date().isoformat()
        for j in snap.get('jobs',[]):
            identity=(j.get('job_id'),j.get('attempt',1))
            if identity[0] is not None:
                if identity in seen:continue
                seen.add(identity)
            # Use job creation day so overlapping windows never move an observation.
            observed_day=str(j.get('created_at') or day)[:10]
            wf=j.get('workflow','unknown');scope=job_scope(j)
            key=(observed_day,wf,'[job-level; testcase unknown]',scope)
            rows[key]['jobs']+=1
            rows[key][j.get('conclusion') or 'unknown']+=1
            cases={c.get('testcase') for c in j.get('failure_cases',[]) if c.get('testcase')}
            for case in cases: rows[(observed_day,wf,case,scope)]['testcase_fail']+=1
            for case,e in j.get('case_evidence',{}).items():
                if e.get('status')=='PASS': rows[(observed_day,wf,case,scope)]['testcase_pass']+=1
            # Ordinary verdicts are execution evidence, never automatic closure proof.
            for case in {o.get('testcase') for o in j.get('testcase_observations',[]) if o.get('status')=='PASS'}:
                if j.get('case_evidence',{}).get(case,{}).get('status')!='PASS':
                    rows[(observed_day,wf,case,scope)]['testcase_pass']+=1
    return [dict(date=k[0],workflow=k[1],testcase=k[2],scope=k[3],**v) for k,v in sorted(rows.items())]


def render_evidence(data,clusters,history=()):
    lines=['## Performance evidence','',
           '| Testcase / metric | Actual / baseline / maximum (s) | Regression / relative tolerance / absolute pad | Runner / tested SHA |',
           '|---|---|---|---|']
    seen=set();total=0
    for c in clusters:
        for j in c.get('jobs',[]):
            key=(j.get('job_id'),j.get('primary_test_case'))
            if key in seen:continue
            seen.add(key)
            for m in j.get('performance',[]):
                total+=1
                if total>12:continue
                lines.append('| '+str(j.get('primary_test_case','unknown')).replace('|','/')+' / '+str(m.get('metric','unknown')).replace('|','/')+' | '+
                    ' / '.join(str(m.get(x,'unknown')) for x in ('actual_seconds','baseline_seconds','threshold_seconds'))+' | '+
                    str(m.get('relative_regression_pct','unknown'))+'% / '+str(m.get('tolerance_pct','unknown'))+'% / '+str(m.get('absolute_pad_seconds','unknown'))+'s | '+
                    str(j.get('runner_name') or j.get('runner_group_name') or 'unknown')+' / `'+str(j.get('head_sha') or 'unknown')+'` |')
    if not total:lines.append('| — | No explicit actual/baseline pair retained | unknown | unknown |')
    lines+=['','Measurements describe observed slowdown; runner/runtime/GT equivalence and root cause require a controlled rerun.','',
            '## GLM execution telemetry','',
            '| Stage | Status | Effort / output cap | Input / output / reasoning tokens |',
            '|---|---|---|---|']
    telemetry=data.get('glm_telemetry',[])
    for stage in telemetry:
      for attempt in stage.get('attempts') or [stage]:
        t=dict(attempt)
        t['stage']=stage.get('stage','unknown')
        lines.append('| '+str(t.get('stage','unknown'))+' | '+str(t.get('status','unknown'))+' | '+
                     str(t.get('effort','unknown'))+' / '+str(t.get('output_cap','unknown'))+' | '+
                     ' / '.join(str(t.get(k)) if t.get(k) is not None else 'unreported' for k in ('prompt_tokens','completion_tokens','reasoning_tokens'))+' |')
    if not telemetry:lines.append('| — | No recorded API attempts (disabled, cached legacy output, or unavailable) | unknown | unreported |')
    lines+=['','Output usage includes reasoning when reported by the provider; unknown usage is never treated as zero. Cached stages incur no new API usage.','',
            '## Observed coverage matrix (seven days)','',
            '| Date (UTC) | Workflow | Scenario | Scope | Jobs | Job PASS / FAIL | Testcase PASS / FAIL |',
            '|---|---|---|---|---:|---|---|']
    rows=data.get('coverage_matrix') or coverage_matrix(data,history)
    for r in rows[:80]:
        lines.append('| '+' | '.join(str(r[k]).replace('|','/') for k in ('date','workflow','testcase','scope'))+' | '+str(r.get('jobs','—'))+' | '+str(r.get('success','—'))+' / '+str(sum(r.get(x,0) for x in ('failure','timed_out','startup_failure','action_required')) if r.get('jobs') else '—')+' | '+str(r.get('testcase_pass','unknown'))+' / '+str(r.get('testcase_fail','unknown'))+' |')
    if len(rows)>80:lines.append(f'\n{len(rows)-80} further rows retained in the snapshot coverage_matrix field.')
    lines+=['','Only observed rows are shown. Missing rows mean unknown execution, not zero failures or passing coverage. Successful-job logs are not routinely collected; testcase PASS requires explicit structured evidence. Workflow caps and missing logs still apply.']
    return '\n'.join(lines)


def load_coverage_history(data, root='reports'):
    from pathlib import Path
    import json
    result=[]
    for p in sorted(Path(root).glob('npu-diffusion-ci-????-??-??.json'),reverse=True)[:8]:
        if p.stat().st_size>30_000_000:continue
        try:
            snap=json.loads(p.read_text(encoding='utf-8'))
            if snap.get('generated_utc','')<data['generated_utc']:result.append(snap)
        except (OSError,ValueError,TypeError):continue
    return result
