"""Snapshot-based symptom continuity; never evidence for Issue closure."""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
try:
    from scripts.issue_taxonomy import job_scope
    from scripts.testcase_failures import symptom_key
    from scripts.incident_verifier import CORE_FIELDS, OPTIONAL_FIELDS
except ImportError:
    from issue_taxonomy import job_scope
    from testcase_failures import symptom_key
    from incident_verifier import CORE_FIELDS, OPTIONAL_FIELDS


def _observations(data, clusters):
    for cluster in clusters:
        for job in cluster.get('jobs',[]):
            case=job.get('primary_test_case')
            if not case: continue
            profile=(job.get('case_evidence') or {}).get(case,{})
            config={k:profile[k] for k in CORE_FIELDS+OPTIONAL_FIELDS if k in profile}
            identity=[case,job_scope(job),job.get('workflow'),job.get('name'),
                      job.get('source_repository'),job.get('runner_group_name'),config]
            key=sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:14]
            yield key, {'testcase':case,'signature':job.get('error_signature',''),
                        'config':config,'scope':job_scope(job),'category':job.get('failure_category','unclassified'),
                        'job_id':job.get('job_id'),'run_id':job.get('run_id'),
                        'sha':job.get('head_sha'),'url':job.get('url'),
                        'date':data['generated_utc'][:10],
                        'observed_at':job.get('created_at') or data['generated_utc']}


def correlate_snapshots(data, clusters, root, days=7):
    try:
        from scripts.npu_diffusion_ci_report import clusters_from_snapshot
    except ImportError:
        from npu_diffusion_ci_report import clusters_from_snapshot
    now=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00'))
    records={}; errors=[]; files=0
    for path in sorted(Path(root).glob('npu-diffusion-ci-????-??-??.json')):
        try:
            old=json.loads(path.read_text(encoding='utf-8'))
            stamp=datetime.fromisoformat(old['generated_utc'].replace('Z','+00:00'))
            if not now-timedelta(days=days) <= stamp < now or stamp.date()==now.date(): continue
            files+=1
            for key,obs in _observations(old,clusters_from_snapshot(old)):
                records.setdefault(key,[]).append(obs)
        except (ValueError,OSError,KeyError,TypeError):
            errors.append(path.name)
    current={}
    for key,obs in _observations(data,clusters): current.setdefault(key,[]).append(obs)
    rows=[]
    for key,items in current.items():
        past=records.get(key,[])
        ids={x['job_id'] for x in items if x['job_id'] is not None}
        distinct=[x for x in past if x['job_id'] is None or x['job_id'] not in ids]
        prior_dates=sorted({x['date'] for x in distinct})
        baseline=max(prior_dates) if prior_dates else None
        prior=[x for x in distinct if x['date']==baseline]
        def signatures(xs): return sorted({symptom_key(x['category'],x['testcase'],x['signature']) for x in xs})
        state=('continuing' if signatures(prior)==signatures(items) else 'signature changed') if prior else (
            'overlapping observation' if past else 'new in retained history')
        rows.append({'family_id':key,'testcase':items[0]['testcase'],'scope':items[0]['scope'],
             'status':state,'previous_date':baseline,
             'first_observed':min(x['observed_at'] for x in past+items),
             'last_observed':max(x['observed_at'] for x in items),
             'signatures':sorted({x['signature'] for x in items}),
             'previous_signatures':sorted({x['signature'] for x in prior}),
             'evidence':items[:3], 'previous_evidence':prior[:3],
             'comparability':'pinned profile' if all(k in items[0]['config'] for k in CORE_FIELDS)
                             else 'scenario only; runtime/GT equivalence unverified'})
    return {'version':1,'days':days,'snapshots_read':files,'errors':errors,
            'rows':sorted(rows,key=lambda x:(x['status']!='signature changed',x['testcase'],x['family_id'])),
            'closure_evidence':False}


def render_failure_history(history):
    def cell(text): return str(text).replace('|','/').replace('\n',' ')[:240]
    lines=['## Cross-day failure signatures','',
           'Scenario correlation includes PR-only observations. A changed signature does not prove a shared cause or a fix.', '',
           '| Testcase / scope | History | Previous symptom | Current symptom | Comparability |',
           '|---|---|---|---|---|']
    for row in history.get('rows',[])[:12]:
        lines.append('| '+ ' | '.join(map(cell,[row['testcase']+' / '+row['scope'],
             row['status']+(' since '+row['previous_date'] if row['previous_date'] else ''),
             '; '.join(row['previous_signatures']) or 'unavailable',
             '; '.join(row['signatures']),row['comparability']]))+' |')
    if not history.get('rows'): lines.append('| — | No comparable retained observations | — | — | — |')
    lines+=['',f"History: {history.get('snapshots_read',0)} prior daily snapshots; {len(history.get('errors',[]))} unreadable. Missing failures are not PASS evidence."]
    return '\n'.join(lines)
