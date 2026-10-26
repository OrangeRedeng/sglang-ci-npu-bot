"""Concise GitHub-readable RCA report; preserves the complete GLM response locally.

Never converts a correlation into a verified root cause. All counts and links are
from structured CI evidence; LLM claims are clearly identified as hypotheses.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import re

MAX_FULL_ANALYSIS_IN_ISSUE = 14000


def utc_msk(value):
    if not value or value == 'unknown':
        return 'unknown'
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(ZoneInfo('Europe/Moscow')).strftime('%Y-%m-%d %H:%M MSK')
    except (ValueError, TypeError):
        return 'unknown'


def _txt(text, size=220):
    """Prevent untrusted text from breaking issue tables and formatting."""
    return re.sub(r'\s+', ' ', str(text or '')).replace('|', '\\|')[:size]


def testcase(cluster):
    for j in cluster.get('jobs', []):
        t = j.get('primary_test_case')
        if t:
            return t
    m = re.search(r'Consistency check failed for ([\w-]+)', cluster.get('signature', ''))
    return m.group(1) if m else None


def compact_checks(job):
    metrics = job.get('metrics') or {}
    thresholds = job.get('thresholds') or {}
    checks = []
    for metric in job.get('failed_checks', []):
        if metric in metrics and metric in thresholds:
            tr = thresholds[metric]
            checks.append(f"{metric.upper()}={metrics[metric]} (required {tr.get('op', '?')}{tr.get('value', '?')}; FAIL)")
        elif metric == 'clip' and 'sim' in metrics and 'clip' in thresholds:
            tr = thresholds['clip']
            checks.append(f"CLIP={metrics['sim']} (required {tr.get('op', '?')}{tr.get('value', '?')}; FAIL)")
        else:
            checks.append(f"{metric.upper()} (FAIL)")
    return '; '.join(checks[:4]) if checks else _txt(job.get('error_signature'), 180)


def fix_candidates(cluster):
    """Evidence-conditional fix ideas in English, never assert a proven fix."""
    sample=(cluster.get('jobs') or [{}])[0]
    name=(testcase(cluster) or '').lower()
    cat=str(sample.get('failure_category','')).lower()
    if cat=='diffusion_consistency' or 'consistency' in str(cluster.get('signature','')).lower():
        if 'minimax' in name:
            return [
                ('Compare tested branch against its merge-base',
                 'Rerun the same testcase on the PR merge-base and HEAD with identical GT, seed, dtype and runner. Repair only the verified code difference.'),
                ('Validate reference GT provenance',
                 'Check the exact GT commit, seed, scheduler and video settings; do not relax thresholds without controlled evidence.'),
            ]
        return [
            ('Localize the quality regression',
             'Find the last testcase-level PASS, then compare the same GT and runner on good/bad commits. Correct the first proven divergent component.'),
            ('Audit NPU runtime and reference GT',
             'Compare CANN, torch_npu, sgl-kernel-npu, selected kernels, GT hash and quantization settings. Pin versions or update GT only after proving the cause.'),
        ]
    if cat=='timeout' or re.search(r'timeout|connection|proxy',str(cluster.get('signature','')),re.I):
        return [('Stabilize dependency download',
                 'Rerun pip download; if reproducible, replace the unstable proxy or use a hash-verified local wheel cache. Verify the next CI run.')]
    return [('Isolate the first failing operation',
             'Correlate the earliest traceback with a verified passing testcase, environment versions and minimal changed code; validate with rerun.')]


def extract_cluster_section(analysis, stable_id):
    """Read per-incident GLM reasoning, accommodating common Markdown heading styles."""
    if not stable_id or not analysis:
        return ''
    # Only match at the start of a Markdown heading to avoid cross references.
    h = re.search(r'^#{2,5}\s+(?:\[)?' + re.escape(stable_id) + r'\b[^\n]*', analysis, re.M | re.I)
    if h:
        rest=analysis[h.end():]
        # Recovery files contain whole standalone cluster analyses, including
        # their own ## subheadings; only the next incident is a boundary.
        recovered=analysis.lstrip().startswith('GLM synthesis failed (')
        boundary=r'^#{1,5}\s+(?:\[)?(?:NPU-D\d{3}|OBS-[a-f0-9]{14}|PREVIEW-[a-f0-9]{6})\b'
        if not recovered:
            # In synthesis summaries the following level-2 sections describe
            # cross-cluster fixes; never append them to the final incident.
            boundary+=r'|^##\s+(?:Candidate Fixes|Cross.Cluster|Global|Overall|Conclusions|Next Actions|Next Steps|Closure Rule|Summary of Fixes|Recommendations|Resolution Plan)\b'
        next_h=re.search(boundary,rest,re.M|re.I)
        end=h.end()+next_h.start() if next_h else min(len(analysis),h.end()+9000)
        return analysis[h.start():end].strip()
    # Model may use **NPU-D004 (P1)** inline instead of a heading.
    b = re.search(r'^\*\*' + re.escape(stable_id) + r'\b[^\n]*', analysis, re.M | re.I)
    if b:
        return analysis[b.start(): min(len(analysis), b.end() + 3000)].strip()
    return ''


def vetted_cluster_section(analysis, cluster):
    """Suppress model citations to PR numbers not verified by GitHub discovery.

    Synthesis text is untrusted narrative, not an authority for PR attribution.
    Never re-publish legacy push/main ghost links (#1/#2). Keep other evidence.
    """
    section=extract_cluster_section(analysis,cluster.get('analysis_ref') or cluster.get('stable_id'))
    allowed={pr.get('number') for pr in cluster.get('pr_leads', [])}
    retained=[]
    for line in section.splitlines():
        links={int(n) for n in re.findall(r'(?<![A-Za-z0-9])#(\d{1,6})\b',line)}
        links.update(int(n) for n in re.findall(r'https?://github\.com/sgl-project/sglang/pull/(\d+)',line))
        if links and not links.issubset(allowed):
            # Do not expose unvalidated PR IDs in the Issue, even inside
            # apparently harmless explanatory prose from the model.
            continue
        retained.append(line)
    return '\n'.join(retained).strip()


def short_hypotheses(analysis, stable_id, limit=2, cluster=None):
    section = vetted_cluster_section(analysis, cluster) if cluster else extract_cluster_section(analysis, stable_id)
    if not section:
        return []
    results = []
    for line in section.splitlines():
        line = line.strip()
        # Original GLM full analysis: **H1 (medium) — ...** / - **S1 ...** / ### H1 — ...
        if re.match(r'^(?:[-*]\s+|\d+\.\s+)?(?:\*\*)?(?:H|S|T)\d\b', line, re.I) or re.match(r'^#{3,5}\s+(?:H|S|T)\d\b', line):
            clean = re.sub(r'^#{3,5}\s*|^\d+\.\s*', '', line).lstrip('- ').replace('**', '').strip()
            if clean and clean not in results:
                results.append(_txt(clean, 230))
        if len(results) >= limit:
            break
    return results


def _recent_history(history, current):
    if not history:
        return []
    return [h for h in history if h.get('date') != current][:4]


def _linked_prs(cluster, limit=3):
    """Render only server-validated PR URLs; no LLM-supplied arbitrary URLs."""
    leads=[]
    for pr in cluster.get('pr_leads', [])[:limit]:
        n=pr.get('number')
        url=pr.get('url')
        if isinstance(n,int) and re.fullmatch(r'https://github\.com/sgl-project/sglang/pull/'+str(n),str(url)):
            role={'tested_branch':'tested branch (not causation)',
                  'possible_cause':'possible cause — merged before observed failure',
                  'potential_fix':'candidate fix — mechanism unverified',
                  'related_change':'related code change — execution path unverified'}.get(pr.get('relation'),'unverified')
            leads.append(f'[#{n}]({url}) {role}: {_txt(pr.get("title"),95)}')
    return leads


def render_human_report(data, clusters, analysis, glm_status, history=None,
                        include_deep=False, full_analysis_path=None, continuity=None):
    """English-only concise daily Issue body; detailed evidence stays in cluster Issues."""
    generated = data['generated_utc']
    dt = datetime.fromisoformat(generated.replace('Z', '+00:00'))
    date = dt.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    counts = Counter(data.get('counts') or {})
    failed = sum(counts.get(x, 0) for x in ('failure','timed_out','startup_failure','action_required'))
    logged = data.get('log_download_succeeded',0)
    unseen = max(0,failed-logged)
    observed = len({j.get('job_id') for c in clusters for j in c.get('jobs',[]) if j.get('job_id') is not None})
    meta={'date':date,'counts':data.get('counts',{}),'clusters':len(clusters),
          'jobs_inspected':len(data.get('jobs',[])),
          'coverage_capped':any('CAPPED' in str(s) for s in data.get('coverage',[]))}
    try:
        from scripts.issue_taxonomy import ordered_clusters, classify_cluster
    except ModuleNotFoundError:
        from issue_taxonomy import ordered_clusters, classify_cluster
    clusters=ordered_clusters(clusters)
    lines=[f'<!-- npu-diffusion-report-date:{date} -->',
           f'<!-- npu-diffusion-metrics: {json.dumps(meta,separators=(",", ":"))} -->',
           '<!-- npu-ci-report-language:en -->',
           f'# NPU Diffusion CI — {date}', '',
           f'**Snapshot:** {utc_msk(generated)} · **Window:** {data.get("lookback_hours",24)}h · '
           f'**GLM:** `{glm_status}` · [SGLang Actions](https://github.com/{data.get("source_repository","sgl-project/sglang")}/actions)', '',
           '## Executive summary','',
           f'**{failed} failed / {counts.get("success",0)} passed** jobs of {len(data.get("jobs",[]))} observed '
           f'({counts.get("skipped",0)} skipped, {counts.get("cancelled",0)} cancelled, {counts.get("in_progress",0)} in progress).',
           f'**{len(clusters)} incidents, {sum(c.get("count",0) for c in clusters)} failing testcase observations** '
           f'across {observed} classified jobs (clusters overlap).',
           f'**Log coverage:** {logged}/{failed} failed-job logs; {unseen} remain unclassified.', '',
           '## Incidents: impact, suspected cause, next fix','',
           '| Priority | Scope | Incident | Failure observations (main / PR / unknown) | Earliest observed run |',
           '|---|---|---|---:|---|']
    if continuity:
        try:
            from scripts.incident_lifecycle import render_continuity
        except ModuleNotFoundError:
            from incident_lifecycle import render_continuity
        section=render_continuity(continuity)
        anchor='## Incidents: impact, suspected cause, next fix'
        lines[lines.index(anchor):lines.index(anchor)] = [section, '']
    if glm_status.startswith('partial'):
        lines.insert(lines.index('## Incidents: impact, suspected cause, next fix'),
                     '**Analysis limitation:** Per-cluster investigations were recovered, but final GLM synthesis failed. The incident-level evidence is retained; no cross-cluster conclusion is asserted.\n')
    for c in clusters[:8]:
        cid=c.get('stable_id') or c.get('fingerprint','?')
        link=f'[{cid}]({c["issue_url"]})' if c.get('issue_url') else cid
        taxonomy=classify_cluster(c)
        scope=taxonomy['scope']
        if 'infrastructure' in taxonomy['labels']:scope+=', infrastructure'
        if 'flaky' in taxonomy['labels']:scope+=', flaky'
        counts_scope=f'{taxonomy["main_jobs"]} main / {taxonomy["pr_jobs"]} PR / {taxonomy["unknown_jobs"]} unknown'
        lines.append(f'| {taxonomy["priority"]} | {scope} | {link} · `{_txt(testcase(c) or c.get("signature"),48)}` | '
                     f'{c.get("count",0)} ({counts_scope}) | {utc_msk(c.get("first_seen_snapshot"))} |')
    lines+=['','**Evidence rule:** the earliest observed failing run is not proof of when a regression began. '
            'PR branch association and temporal proximity are not causal confirmation.','']
    for c in clusters[:6]:
        cid=c.get('stable_id') or c.get('fingerprint','?')
        url=c.get('issue_url')
        title=f'[{cid}]({url})' if url else cid
        sample=(c.get('jobs') or [{}])[0]
        taxonomy=classify_cluster(c)
        lines += [f'### {title} — {taxonomy["scope"]}: `{_txt(testcase(c) or c.get("signature"),65)}`','',
           f'**Observed:** {compact_checks(sample)} · {c.get("count",0)} failure observations '
           f'({taxonomy["main_jobs"]} main, {taxonomy["pr_jobs"]} PR, {taxonomy["unknown_jobs"]} unknown).',
           f'**First observed:** {utc_msk(c.get("first_seen_snapshot"))} (run creation, not verified regression onset).',
           f'**Example:** [job {sample.get("job_id","?")}]({sample.get("url","https://github.com/sgl-project/sglang/actions")}).',
           '**Root cause:** unverified; no last testcase-level green/first bad boundary has been established.','']
        hypotheses=short_hypotheses(analysis,cid,cluster=c)
        if hypotheses:
            lines.append('**GLM hypotheses (unverified):**')
            lines.extend('- '+h for h in hypotheses)
        else:
            lines.append('**GLM hypotheses:** No evidence-backed hypothesis was available.')
        leads=_linked_prs(c)
        lines += ['','**Related PRs (not verified causes or fixes):**']
        lines.extend('- '+p for p in leads) if leads else lines.append('- None verified by GitHub metadata and changed-file checks.')
        lines+=['','**Potential fixes (require verification):**']
        for name,action in fix_candidates(c)[:2]: lines.append(f'- **{name}:** {action}')
        lines.append('')
    lines+=['## Next actions','',
      '1. Find a verified testcase-level green **before** the earliest failed run; job-level green is not sufficient.',
      '2. For each linked PR, inspect the actual diff and merge timestamp, then compare a controlled good/bad boundary.',
      '3. Verify a minimal fix on a targeted rerun; do not close incidents on an unverified hypothesis.',
      '4. Collect missing failed-job logs or leave the corresponding failures unclassified.','',
      '## Coverage and limitations','']
    lines.extend('- '+_txt(x,240) for x in data.get('coverage',[])[:9])
    if history:
        for h in _recent_history(history,date):
            count=h.get('counts',{})
            lines.append(f'- {h.get("date")}: failure jobs={sum(count.get(k,0) for k in ("failure","timed_out","startup_failure","action_required"))}, '
                         f'clusters={h.get("clusters","?")}; coverage={"capped" if h.get("coverage_capped") else "query window"}.')
    lines+=['', '**Detailed evidence:** saved JSON and redacted logs remain on the server. '
            'See linked cluster Issues for full reasoning and PR evidence.']
    if not clusters and failed:
        lines+=['','**Unclassified:** zero clusters is not evidence of passing tests.']
    if include_deep and analysis:
        from html import escape
        lines+=['','<details><summary>Full GLM investigation</summary>','',
                escape(analysis[:MAX_FULL_ANALYSIS_IN_ISSUE]),'','</details>']
    return '\n'.join(lines)+'\n'
