"""Conservative GitHub run provenance and incident classification.

Never assign 'main' from a PR's base ref, a job name or a missing event.
An Issue can be P0/P1 on main only with an observed completed main run.
"""
from __future__ import annotations
import re

LABELS = {
    'main': ('d73a4a', 'Observed failure on an actual main branch run; not PR base'),
    'not-merged': ('6f42c1', 'Observed only in pull_request or unmerged branch runs'),
    'unconfirmed': ('d4c5f9', 'Run provenance or causal attribution is unverified'),
    'flaky': ('fbca04', 'Testcase-level intermittent behavior independently verified'),
    'infrastructure': ('bfdadc', 'Network, runner, dependency download or CI infrastructure symptom'),
}
MANAGED = frozenset(LABELS)
INFRA_RE = re.compile(r'(?:http\s*(?:401|403|429|500|502|503|504)\b|connection (?:reset|abort|refused)|'
                      r'network (?:unreachable|error)|temporary failure in name resolution|'
                      r'failed to (?:resolve|download)|read timed out|httpsconnectionpool|'
                      r'wheel.*(?:download|fetch).*tim(?:ed)?\s*out|'
                      r'github(?:\.com)? (?:proxy|api).*tim(?:ed)?\s*out|sslcertverificationerror|certificate_verify_failed|self.signed certificate)', re.I)


def job_scope(job):
    """Provenance from the upstream GitHub *run*, not from PR base or SHA."""
    event = str(job.get('run_event') or '').lower()
    branch = str(job.get('head_branch') or '')
    repo = str(job.get('head_repository') or '')
    source = str(job.get('source_repository') or 'sgl-project/sglang')
    prs = job.get('pr_numbers') or []
    if event in ('pull_request', 'pull_request_target', 'merge_group'):
        return 'not-merged'
    # A push to actual main stays main even when GitHub links a *merged* PR
    # in pull_requests[]. In contrast, PR event base=main is never main.
    if event in ('push', 'schedule', 'workflow_dispatch') and branch == 'main' and repo == source:
        return 'main'
    # PR numbers without a verified event are untrusted (legacy snapshots).
    if event in ('push', 'workflow_dispatch') and branch and branch != 'main' and repo == source:
        return 'not-merged'
    return 'unconfirmed'


def classify_cluster(cluster):
    jobs = cluster.get('jobs') or []
    scopes = {job_scope(job) for job in jobs}
    # main takes precedence over PR-only; presence of unknown jobs must not
    # turn a partially enriched cluster into a "not-merged only" incident.
    primary = ('main' if 'main' in scopes else
               'not-merged' if scopes == {'not-merged'} else 'unconfirmed')
    infra = any(job.get('failure_category') in ('network_infrastructure', 'ci_infrastructure') or
                (job.get('failure_category') in ('timeout', 'generic_error') and
                 INFRA_RE.search(str(job.get('error_signature') or '') + ' ' +
                                 str(job.get('expanded_evidence') or '')[:1000]))
                for job in jobs)
    # Do not infer 'flaky' merely from job-level successes or non-identical SHAs.
    flaky = bool(cluster.get('testcase_flakiness_verified'))
    labels = {primary}
    if infra: labels.add('infrastructure')
    if flaky: labels.add('flaky')
    # Main is *not* P0 by default; P0 requires independently established
    # material gating/business severity, not simply widespread PR failures.
    if infra:
        priority = 'P2' if primary == 'main' else 'P3'
    elif primary == 'main':
        priority = 'P1'
    elif primary == 'not-merged':
        priority = 'P2'
    else:
        priority = 'P3'
    return {'scope': primary, 'labels': sorted(labels), 'priority': priority,
            'main_jobs': sum(job_scope(j) == 'main' for j in jobs),
            'pr_jobs': sum(job_scope(j) == 'not-merged' for j in jobs),
            'unknown_jobs': sum(job_scope(j) == 'unconfirmed' for j in jobs)}


def annotate_clusters(clusters):
    for cluster in clusters:
        status = classify_cluster(cluster)
        cluster['incident_scope'] = status['scope']
        cluster['incident_priority'] = status['priority']
        cluster['issue_labels'] = status['labels']
        cluster['scope_counts'] = {k: status[k] for k in ('main_jobs','pr_jobs','unknown_jobs')}
    return clusters


def ordered_clusters(clusters):
    annotate_clusters(clusters)
    return sorted(clusters, key=lambda c: (
        {'main':0, 'not-merged':1, 'unconfirmed':2}.get(c['incident_scope'],2),
        1 if 'infrastructure' in c['issue_labels'] else 0,
        -int(c.get('count') or 0), -int(c.get('severity') or 0)))


def merged_labels(existing, desired):
    """Preserve user-maintained labels; replace only bot-managed scope labels."""
    names = [str(x.get('name') if isinstance(x, dict) else x) for x in existing]
    return sorted((set(names) - MANAGED) | set(desired))
