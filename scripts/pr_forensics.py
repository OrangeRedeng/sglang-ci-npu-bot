"""Bounded, evidence-gated GitHub PR leads for NPU diffusion incidents.

Search results are never root-cause proof. A temporal match is a lead, not a fix.
Only GitHub API-returned PRs with exact repo/number and relevant changed files survive.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import os
import re
from urllib.parse import quote

MAX_GH_REQUESTS = 38
PATH_RE = re.compile(r'(?:multimodal_gen|diffusion|ascend|npu|sgl_kernel|ci-data)', re.I)
EXCLUDE_PATH_RE = re.compile(r'(?:^docs/|^examples/|cuda|rocm|amd|vlm|llm|flashinfer)', re.I)
FIX_RE = re.compile(r'\b(fix|bugfix|repair|correct|regression|consistency|gt|precision)\b', re.I)


def _parse_time(ts):
    try:
        if not ts: return None
        return datetime.fromisoformat(ts.replace('Z','+00:00')).astimezone(timezone.utc)
    except (ValueError,AttributeError): return None


def _terms(cluster):
    combined = ' '.join([str(cluster.get('signature',''))] +
      [str(j.get('primary_test_case','')) for j in cluster.get('jobs',[])[:2]]).lower()
    if 'qwen_image' in combined or 'qwen-image' in combined: return ('qwen', 'image')
    if 'minimax' in combined: return ('minimax', 'h3')
    if 'wan' in combined: return ('wan', 'diffusion')
    if 'glm' in combined: return ('glm', 'image')
    if 'flux' in combined: return ('flux', 'diffusion')
    if 'triton' in combined or 'pip' in combined or 'proxy' in combined: return ('triton', 'ascend')
    return ('diffusion','npu')


def _relevant(pr, files, cluster):
    terms=_terms(cluster)
    title=str(pr.get('title') or '').lower()
    candidate_files = [str(f.get('filename') or '') for f in files if isinstance(f,dict)]
    paths=[p for p in candidate_files if PATH_RE.search(p) and not EXCLUDE_PATH_RE.search(p)]
    if not paths: return False, []
    # Prevent irrelevant generic kernel/LLM PRs from being included merely due to time.
    signal = ' '.join([title] + paths).lower().replace('_','-')
    if terms == ('qwen','image'):
        hit=('qwen' in signal and ('image' in signal or 'diffusion' in signal)) or ('diffusion' in signal and 'consistency' in signal)
    elif terms == ('minimax','h3'):
        hit=('minimax' in signal and ('h3' in signal or 'diffusion' in signal)) or ('diffusion' in signal and 'consistency' in signal)
    elif terms == ('triton','ascend'):
        hit=('triton' in signal and ('ascend' in signal or 'npu' in signal)) or ('dependency' in signal and 'npu' in signal)
    elif terms[0] in ('wan','glm','flux'):
        hit=terms[0] in signal or ('diffusion' in signal and any(x in signal for x in ('scheduler','worker','distributed','pipeline')))
    else:
        hit='diffusion' in signal and ('npu' in signal or 'ascend' in signal or 'consistency' in signal)
    return hit, paths[:5]


def discover_pr_leads(gh, clusters, repo='sgl-project/sglang', max_per_cluster=3,
                      max_api_calls=30, days_before=7):
    """Find verified *candidate* PRs, never infer causality from timestamp alone.

    Uses GitHub issue search for discovery, then GET pull and changed filenames.
    All errors fail softly, never block the normal CI report.
    """
    if gh is None: return {}
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid source repo')
    max_calls=max(0,min(int(max_api_calls),MAX_GH_REQUESTS))
    limit=max(0,min(int(max_per_cluster),5))
    request_count=0
    cache={}
    results={}
    for cluster in clusters[:8]:
        ident=cluster.get('stable_id') or cluster.get('fingerprint')
        if not ident:continue
        if request_count >= max_calls or not limit:
            results[ident]=[]
            continue
        try:
            from scripts.issue_taxonomy import classify_cluster
        except ModuleNotFoundError:
            from issue_taxonomy import classify_cluster
        scope=classify_cluster(cluster)['scope']
        main_times=[_parse_time(j.get('created_at')) for j in cluster.get('jobs', [])
           if j.get('run_event') in ('push','schedule','workflow_dispatch')
           and j.get('head_branch')=='main'
           and j.get('head_repository')==repo and j.get('created_at')]
        main_times=[t for t in main_times if t]
        earliest=(min(main_times) if main_times else _parse_time(cluster.get('first_seen_snapshot')))
        leads=[]
        considered=set()
        cluster_cap=min(max_calls, request_count+max(7,max_calls//max(1,min(8,len(clusters)))))
        term=_terms(cluster)
        # Tested branch is strong provenance evidence, but not a confirmed cause.
        # Restrict branch attribution to PR-triggered runs. GitHub's
        # push/main pull_requests list can contain unrelated historical PRs.
        # Rank by observed frequency, never numeric order.
        from collections import Counter
        linked_counter=Counter(n for j in cluster.get('jobs',[])
            if j.get('run_event') in ('pull_request','pull_request_target')
            for n in j.get('pr_numbers',[])
            if isinstance(n,int) and n > 0)
        linked=[n for n,_ in sorted(linked_counter.items(),
            key=lambda item:(-item[1],-item[0]))[:3]]
        query_list=[]
        if earliest and scope == 'main':
            lo=(earliest-timedelta(days=days_before)).date().isoformat()
            hi=earliest.date().isoformat()
            query_list.append(('possible_cause',f'repo:{repo} is:pr is:merged merged:{lo}..{hi} {term[0]}'))
        # For potential fixes search ongoing PRs, never claim fixes without a targeted rerun.
        query_list.append(('potential_fix',f'repo:{repo} is:pr is:open {term[0]}'))
        latest=max((_parse_time(j.get('created_at')) for j in cluster.get('jobs',[]) if _parse_time(j.get('created_at'))),default=earliest)
        latest=_parse_time(cluster.get('analysis_as_of')) or latest
        if latest:
            lo=(latest-timedelta(days=days_before)).date().isoformat()
            query_list.append(('merged_change',f'repo:{repo} is:pr is:merged merged:>={lo} {term[0]}'))
            if term[0]!='diffusion':
                query_list.append(('merged_change',f'repo:{repo} is:pr is:merged merged:>={lo} diffusion'))
        entries=[('tested_branch',n) for n in linked]
        for relation,q in query_list:
            if request_count >= cluster_cap: break
            try:
                request_count+=1
                obj=gh.get('/search/issues',{'q':q,'per_page':25})
                added=0
                for entry in (obj.get('items') or [])[:25]:
                    n=entry.get('number')
                    title=str(entry.get('title') or '').lower()
                    if not isinstance(n,int) or n<=0 or (relation,n) in entries: continue
                    normalized=title.replace('_','-')
                    if term==('qwen','image'):
                        relevant_title=('qwen-image' in normalized or 'qwen image' in title or
                            ('qwen' in title and 'diffusion' in title) or
                            ('diffusion' in title and 'consistency' in title))
                    elif term==('minimax','h3'):
                        relevant_title=('minimax-h3' in normalized or 'minimax h3' in title or
                            ('minimax' in title and 'diffusion' in title))
                    elif term==('triton','ascend'):
                        relevant_title=('triton-ascend' in normalized or ('triton' in title and 'npu' in title))
                    elif term[0] in ('wan','glm','flux'):
                        relevant_title=term[0] in title or ('diffusion' in title and any(x in title for x in ('scheduler','worker','distributed')))
                    else:
                        relevant_title='diffusion' in title
                    if not relevant_title: continue
                    if relation == 'potential_fix' and not FIX_RE.search(title): continue
                    entries.append((relation,n));added+=1
                    if added>=4: break
            except (RuntimeError,ValueError,TypeError,OSError):
                continue
        for relation,n in entries:
            if request_count>=cluster_cap or len(leads)>=limit:break
            if n in considered:continue
            considered.add(n)
            try:
                if n not in cache:
                    if request_count+2>cluster_cap: break
                    request_count+=1
                    pr=gh.get(f'/repos/{repo}/pulls/{n}')
                    request_count+=1
                    files=gh.get(f'/repos/{repo}/pulls/{n}/files',{'per_page':75})
                    cache[n]=(pr,files)
                pr,files=cache[n]
                if not isinstance(pr,dict) or pr.get('number')!=n or not isinstance(files,list):continue
                canonical=f'https://github.com/{repo}/pull/{n}'
                if pr.get('html_url')!=canonical:continue
                relevant,paths=_relevant(pr,files,cluster)
                # Even the observed PR branch must touch relevant components;
                # otherwise it may only be CI scheduling context, not a lead.
                if not relevant: continue
                merged=_parse_time(pr.get('merged_at'))
                if relation=='possible_cause':
                    if not earliest or not merged or merged>earliest or merged < earliest-timedelta(days=days_before):continue
                elif relation=='potential_fix':
                    if pr.get('state')!='open' or not FIX_RE.search(str(pr.get('title') or '')):continue
                elif relation=='tested_branch':
                    # Historical PR metadata can be stale: never label a PR
                    # tested if it was created after the observed test run.
                    start=_parse_time(pr.get('created_at'))
                    job_dates=[_parse_time(j.get('created_at')) for j in cluster.get('jobs',[])
                        if j.get('run_event') in ('pull_request','pull_request_target') and n in j.get('pr_numbers',[])]
                    job_dates=[d for d in job_dates if d]
                    if start and job_dates and start>max(job_dates):continue
                if relation=='merged_change':
                    if not merged or not latest or merged>latest or merged<latest-timedelta(days=days_before):continue
                    relation='related_change'
                ci_status='not checked (API budget or missing SHA)'
                sha=(pr.get('head') or {}).get('sha')
                ci_evidence={}
                if sha and re.fullmatch(r'[a-fA-F0-9]{7,64}',sha) and request_count+2<=cluster_cap:
                    try:
                        request_count+=1
                        status=gh.get(f'/repos/{repo}/commits/{sha}/status')
                        request_count+=1
                        checks=gh.get(f'/repos/{repo}/commits/{sha}/check-runs',{'per_page':100})
                        ci_status,ci_evidence=summarize_ci(status,checks)
                    except (RuntimeError,ValueError,TypeError,OSError):
                        ci_status='unavailable'
                title=str(pr.get('title') or '')
                # File/title overlap is not proof the failing testcase executed
                # the changed path. Only explicitly checked runtime coverage
                # may promote a merged change to a candidate cause.
                coverage=set(cluster.get('verified_execution_paths') or [])
                code_path_verified=bool(coverage.intersection(set(paths)))
                if relation=='possible_cause' and not code_path_verified:
                    relation='related_change'
                # A fix title alone is insufficient: fixes for hangs or
                # platform admission are not verified quality-metric fixes.
                if relation=='potential_fix' and ('consistency' in str(cluster.get('signature','')).lower()):
                    if not re.search(r'consistency|accuracy|numeri|precision|metric|reference|\bgt\b|ssim|psnr|calibrat|quantiz|quality',title,re.I):
                        relation='related_change'
                leads.append({'number':n,'url':canonical,'title':title[:180],
                    'relation':relation,'status':'merged' if merged else pr.get('state','unknown'),
                    'author':(pr.get('user') or {}).get('login'),
                    'updated_at':pr.get('updated_at'), 'head_sha':(pr.get('head') or {}).get('sha'),
                    'ci_status':ci_status, 'ci_evidence':ci_evidence,
                    'ci_sha':sha, 'metadata_checked_at':datetime.now(timezone.utc).isoformat(), 'files_complete': len(files)<75,
                    'tested_shas':sorted({j.get('head_sha') for j in cluster.get('jobs',[]) if j.get('head_sha') and n in j.get('pr_numbers',[])}),
                    'minimal_experiment':'Rerun the exact failing testcase and configuration at the observed tested SHA and its merge-base on the same runner/runtime/GT; compare exception and metrics.',
                    'merged_at':pr.get('merged_at'),'first_failure_at':cluster.get('first_seen_snapshot'),
                    'relevant_files':paths,'code_path_verified':code_path_verified,
                    'diff_excerpts':[{'filename':f['filename'],'patch':str(f.get('patch') or '')[:1200]} for f in files if f.get('filename') in paths][:3],
                    'evidence':'GitHub-verified changed files and timestamps. No causal conclusion without testcase-level A/B.'})
            except (RuntimeError,ValueError,TypeError,OSError):
                continue
        results[ident]=leads[:limit]
    return results


def attach_pr_leads(clusters, leads):
    for c in clusters:
        c['pr_leads']=leads.get(c.get('stable_id') or c.get('fingerprint'),[])
    return clusters


def summarize_ci(status,checks):
    """Commit statuses plus check-runs at one explicit PR head SHA."""
    if not isinstance(status,dict) or not isinstance(checks,dict):return 'unavailable',{}
    runs=checks.get('check_runs') or []
    states=[x.get('conclusion') if x.get('status')=='completed' else 'pending' for x in runs]
    legacy=status.get('state') if status.get('total_count',len(status.get('statuses') or [])) else None
    if any(x in ('failure','timed_out','cancelled','action_required','startup_failure') for x in states) or legacy in ('error','failure'):
        result='failure'
    elif 'pending' in states or legacy=='pending':result='pending'
    elif checks.get('total_count',len(runs))>len(runs):result='partial (checks capped)'
    elif (states or legacy) and all(x in ('success','neutral','skipped') for x in states):result='success'
    else:result='no checks reported'
    return result,{'check_runs_observed':len(runs),'check_runs_total':checks.get('total_count'),
                   'legacy_status':legacy,'conclusions':{x:states.count(x) for x in set(states)}}
