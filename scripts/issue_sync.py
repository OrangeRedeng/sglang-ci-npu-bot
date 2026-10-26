"""Conservative historical Issue sync and provenance enrichment; no GLM calls."""
from __future__ import annotations
from collections import defaultdict
import re

try:
    from scripts.issue_taxonomy import annotate_clusters, merged_labels, LABELS
except ModuleNotFoundError:
    from issue_taxonomy import annotate_clusters, merged_labels, LABELS


def enrich_run_provenance(gh, data, clusters, source_repo='sgl-project/sglang', max_runs=200):
    """Add evidence for old snapshots lacking run event/branch/repository metadata.

    If a run is unavailable, leave it UNKNOWN; do not assume it is PR-only.
    """
    eligible = defaultdict(list)
    for c in clusters:
        for job in c.get('jobs', []):
            # Sanitise known provenance even when enrichment is unnecessary.
            if job.get('run_event') and job.get('run_event') not in ('pull_request','pull_request_target'):
                job['pr_numbers'] = []
            if not job.get('run_event') and job.get('run_id'):
                eligible[str(job['run_id'])].append(job)
    successes, errors = 0, 0
    for run_id, records in list(eligible.items())[:max_runs]:
        if not re.fullmatch(r'\d+', run_id):
            errors += 1
            continue
        try:
            run = gh.get('/repos/%s/actions/runs/%s' % (source_repo, run_id))
            if not isinstance(run, dict) or str(run.get('id')) != run_id:
                raise ValueError('Mismatch run ID')
            for j in records:
                j['run_event'] = run.get('event')
                j['head_branch'] = run.get('head_branch')
                head_repo = run.get('head_repository') or {}
                j['head_repository'] = head_repo.get('full_name') if isinstance(head_repo, dict) else None
                j['source_repository'] = source_repo
                # GitHub can attach unrelated historical PRs to push/main runs
                # (real example: push/main linked #1 and #2 from 2024).
                # Never union old, untrusted snapshot metadata back in.
                if run.get('event') in ('pull_request', 'pull_request_target'):
                    j['pr_numbers'] = sorted({p['number'] for p in run.get('pull_requests', [])
                        if isinstance(p, dict) and isinstance(p.get('number'),int) and p['number'] > 0})
                else:
                    j['pr_numbers'] = []
            successes += 1
        except (RuntimeError, ValueError, KeyError, TypeError, OSError):
            errors += 1
    annotate_clusters(clusters)
    return {'runs_enriched':successes,'runs_unavailable':errors,
            'runs_not_inspected':max(0,len(eligible)-max_runs)}


def sync_cluster_labels(gh, clusters, target_repo, existing_issues=None):
    """Safely apply owned taxonomy labels only to fingerprint-verified Issues."""
    from urllib.parse import quote
    label_endpoint = '/repos/%s/labels' % target_repo
    for label, (color, description) in LABELS.items():
        try:
            gh.request('POST', label_endpoint,
                       {'name':label,'color':color,'description':description})
        except RuntimeError as e:
            if 'HTTP 422' not in str(e): raise
            # Recolor even preexisting labels to the declared palette.
            gh.request('PATCH', label_endpoint+'/'+quote(label,safe=''),
                       {'color':color,'description':description})
    if existing_issues is None:
        existing_issues = gh.list_all('/repos/%s/issues' % target_repo,
                                       {'labels':'npu-diffusion-cluster','state':'all'},max_pages=10)
    mapping = {}
    for issue in existing_issues:
        m = re.search(r'<!-- npu-diffusion-fingerprint:([a-f0-9]+) -->',issue.get('body') or '')
        if m: mapping[m.group(1)] = issue
    count, missing = 0, []
    annotate_clusters(clusters)
    for c in clusters:
        issue = mapping.get(c.get('fingerprint'))
        if not issue:
            missing.append(c.get('fingerprint'))
            continue
        labels = merged_labels(issue.get('labels',[]), c['issue_labels'])
        gh.issue(issue['number'], {'labels': labels})
        count += 1
    return {'updated':count, 'unmatched':missing}
