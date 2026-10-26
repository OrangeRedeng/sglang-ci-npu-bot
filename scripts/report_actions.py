"""Evidence-specific investigation plans; these are proposed checks, never diagnoses."""
import re
try:
    from scripts.issue_taxonomy import classify_cluster, ordered_clusters, job_scope
    from scripts.report_editor import _txt, testcase
except ImportError:
    from issue_taxonomy import classify_cluster, ordered_clusters, job_scope
    from report_editor import _txt, testcase


def experiment_plan(cluster):
    jobs=cluster.get('jobs') or [{}]
    sample=jobs[0]
    symptom=str(cluster.get('signature') or '')
    category=sample.get('failure_category','')
    if "No module named 'cuda'" in str(sample.get('causal_signature','')):
        return ('At the tested SHA, import the transformer module named by the retained traceback on Ascend. Inspect the CUDA-only import guard and compare the native/fallback backend before and after a conditional guard fix, keeping model, GT, parallelism and runtime unchanged.',
                'The NPU import succeeds without CUDA, the intended native transformer is selected, and the exact generation testcase meets its accuracy and performance gates. A co-occurring latency failure needs its own matched before/after comparison.')
    evidence=' '.join([symptom,str(sample.get('expanded_evidence') or '')]+sample.get('log_excerpt',[]))
    if category=='performance_regression' or 'PerformanceValidationError' in evidence or sample.get('performance'):
        return ('Run the exact testcase at the observed SHA and a verified testcase-green SHA (or merge-base if no verified green exists) on the same runner, model, seed, CANN/torch_npu, GT and settings. Record E2E and denoise timings, actual attention backend, H2D/D2H transfers, offload residency and Cache-DiT hit/skip counts.',
                'A matched comparison localizes the added time to a stage/operator or transfer; a controlled rerun meets the recorded limit. A job-level green is not a testcase-green reference. Do not raise the baseline just to pass.')
    if re.search(r'cannot honor requested distributed execution|Native Diffusers fallback',evidence,re.I):
        return ('At the observed SHA, record the selected transformer backend and requested SP/Ulysses/TP degrees; reproduce worker startup with the same distributed arguments, then compare a supported backend/configuration while holding other settings fixed.',
                'Every requested worker initializes with the intended distributed backend, then the exact generation testcase completes; a single-device success alone does not validate the distributed fix.')
    if re.search(r'HTTPError|[45]\d\d (?:Client|Server) Error|scheduler|/generate',evidence,re.I):
        return ('Capture the redacted internal AR response status/body, failing endpoint and sanitized request schema before raise_for_status(), plus scheduler/worker traceback at the observed SHA. Compare with an equivalent passing request; identify the first rejected operation before changing retries.',
                'The specific rejected field or backend exception is reproduced and corrected, followed by a successful end-to-end testcase. HTTP statuses in one log are observations; temporal order alone does not prove a causal chain.')
    if category in ('network_infrastructure','ci_infrastructure') or re.search(r'CERTIFICATE_VERIFY_FAILED|download.*timed out|urlopen error|ReadTimeout|asset.*timeout',evidence,re.I):
        return ('Identify the exact failing asset/dependency URL and transfer exception. Check availability before inference, prefetch into a checksum-verified runner cache, and rerun the same testcase using the verified artifact.',
                'Asset preflight and the testcase both pass with the expected checksum. A successful download alone does not prove inference correctness.')
    if category=='diffusion_consistency' or re.search(r'Consistency check failed|SSIM|PSNR',evidence):
        return ('Compare generated output on matched tested/known-green SHAs against the same pinned GT with identical seed, scheduler, dtype, quantization and runtime. Localize the first divergent stage before changing kernels or GT.',
                'The exact failed metrics satisfy the original thresholds under the pinned profile; any GT update requires independent provenance and validation, not just new reference images.')
    return ('Reproduce the exact testcase at the observed SHA, capture the first concrete backend exception and configuration, then compare one relevant change at a time with a verified passing execution.',
            'A minimal reproduction identifies the failing operation and an equivalent testcase rerun passes; root cause remains unverified until then.')


def observation_summary(data,clusters):
    lines=['## Engineering assessment','']
    for c in ordered_clusters(clusters)[:3]:
        t=classify_cluster(c); jobs=c.get('jobs',[])
        distinct=len({j.get('job_id') for j in jobs if j.get('job_id') is not None})
        prs={n for j in jobs if j.get('run_event') in ('pull_request','pull_request_target') for n in j.get('pr_numbers',[]) if type(n) is int}
        measurements=[m for j in jobs for m in j.get('performance',[]) if m.get('actual_seconds') is not None]
        detail=_txt(c.get('signature','unclassified'),220)
        if measurements:
            m=measurements[0]
            detail=f"{m.get('metric','latency')}: actual {m['actual_seconds']}s, baseline {m.get('baseline_seconds','unknown')}s, limit {m.get('threshold_seconds','unknown')}s"
            if m.get('actual_baseline_ratio') is not None:detail+=f" ({m['actual_baseline_ratio']:.2f}x baseline)"
        lines.append(f"- **{t['priority']} · {t['scope']} · `{_txt(testcase(c) or 'testcase unavailable',130)}`:** {detail}. {distinct} distinct observed jobs across {len(prs)} identified PRs. Mechanism unverified.")
    if not clusters:lines.append('No classified failure families; inspect collection coverage before inferring health.')
    lines+=['','Recurring symptoms across PRs warrant shared-path investigation; they do not establish PR independence, a common root cause, or impact on main.']
    return '\n'.join(lines)


def scope_execution(data):
    rows={}
    for j in data.get('jobs',[]):
        key=(j.get('workflow','unknown'),job_scope(j))
        r=rows.setdefault(key,dict(success=0,failure=0,skipped=0,cancelled=0,other=0))
        conclusion=j.get('conclusion')
        category='failure' if conclusion in ('failure','timed_out','startup_failure','action_required') else conclusion if conclusion in r else 'other'
        r[category]+=1
    lines=['## Methodology and executed-job scope','',
           'Only matching diffusion jobs are counted. Parent workflow failures and skipped partitions do not prove inference failures. Runner names do not establish the number of devices used by a testcase.','',
           '| Workflow | Provenance | Passed | Failed | Skipped | Cancelled | Other / active |',
           '|---|---|---:|---:|---:|---:|---:|']
    for (workflow,scope),r in sorted(rows.items()):
        lines.append('| '+_txt(workflow,100)+' | '+scope+' | '+' | '.join(str(r[k]) for k in ('success','failure','skipped','cancelled','other'))+' |')
    return '\n'.join(lines)


def evidence_completeness(cluster):
    sample=(cluster.get('jobs') or [{}])[0]
    case=sample.get('primary_test_case')
    profile=(sample.get('case_evidence') or {}).get(case,{})
    fields=('model','runtime_id','gt_sha','seed','dtype','scheduler','quantization','resolution','steps','test_config_sha')
    known=[f'{k}={profile[k]}' for k in fields if k in profile]
    missing=[k for k in fields if k not in profile]
    return '**Configuration provenance:** '+('; '.join(known) if known else 'no controlled testcase profile retained')+'. **Missing:** '+', '.join(missing)+'. Current source defaults are not substituted for historical SHA-pinned configuration.'


def action_table(clusters):
    lines=['## Actionable next steps','',
           '| Priority / scope | Exact scenario / tested SHA | Decisive experiment (proposed) | Acceptance criteria |',
           '|---|---|---|---|']
    for c in ordered_clusters(clusters)[:6]:
        t=classify_cluster(c);j=(c.get('jobs') or [{}])[0]
        action,acceptance=experiment_plan(c)
        lines.append('| '+t['priority']+' / '+t['scope']+' | `'+_txt(testcase(c) or 'unknown',150)+'` / `'+_txt(j.get('head_sha') or 'unknown',40)+'` | '+_txt(action,1000)+' | '+_txt(acceptance,700)+' |')
    if not clusters:lines.append('| — | — | Recover missing/unclear logs and confirm job provenance before diagnosis. | Explicit testcase verdict and concrete diagnostic evidence retained. |')
    return '\n'.join(lines)


def render_full_analysis(analysis,clusters,limit=14000):
    """Preserve Markdown while dropping narrative lines citing unverified PRs."""
    allowed={p.get('number') for c in clusters for p in c.get('pr_leads',[])}
    retained=[]
    for line in analysis.splitlines():
        numbers={int(n) for match in re.findall(r'https?://github\.com/sgl-project/sglang/pull/(\d+)|(?<![A-Za-z0-9])#(\d{1,6})\b',line) for n in match if n}
        if numbers and not numbers.issubset(allowed):continue
        retained.append(line)
    text='\n'.join(retained)
    if len(text)>limit:text=text[:limit]+'\n\n[Excerpt truncated; complete analysis retained locally.]'
    return text
