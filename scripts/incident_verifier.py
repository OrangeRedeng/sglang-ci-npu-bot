"""Conservative testcase-level CI recovery proof. Standard library, no LLM.

A job conclusion=success is never proof that an individual testcase ran.
Only structured, exact testcase verdicts carrying a comparable execution profile
are accepted for automatic incident closure. Redacted logs stay on disk.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import re
from datetime import datetime

# CI may emit one line per testcase AFTER the assertion / metric gate completes:
# NPU_CI_TEST_RESULT: {"testcase":"...","status":"PASS",...}
# Matching record with FAIL must exist in the historical failing log.
PREFIX = 'NPU_CI_TEST_RESULT:'
CASE_RE = re.compile(r'^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$')
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
CORE_FIELDS = ('gt_sha', 'seed', 'dtype', 'scheduler', 'model', 'runtime_id')
OPTIONAL_FIELDS = ('quantization', 'resolution', 'steps', 'test_config_sha')
ACCEPTED_FIELDS = ('testcase', 'status') + CORE_FIELDS + OPTIONAL_FIELDS


def parse_evidence(path, *, limit_bytes=65_000_000):
    """Read bounded logs and reject conflicting declarations, malformed configs.

    Returns testcase -> verified record. Never derive PASS from vague text,
    pytest collection counts, metrics alone, or job conclusion.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size > limit_bytes:
        return {}
    result, conflicts = {}, set()
    with path.open('r', encoding='utf-8', errors='replace') as stream:
        for raw in stream:
            if PREFIX not in raw:
                continue
            text = raw.split(PREFIX, 1)[1].strip()
            try:
                obj = json.loads(text)
            except (ValueError, TypeError):
                continue
            if not isinstance(obj, dict) or set(obj) - set(ACCEPTED_FIELDS):
                continue
            case, status = obj.get('testcase'), obj.get('status')
            if not isinstance(case, str) or not CASE_RE.fullmatch(case) or status not in ('PASS', 'FAIL'):
                continue
            # Require *every* controlled field. Missing evidence cannot prove
            # equivalence; false negatives are safer than false closures.
            if not SHA_RE.fullmatch(str(obj.get('gt_sha', ''))):
                continue
            if type(obj.get('seed')) is not int or not 0 <= obj['seed'] < 2**63:
                continue
            if any(not isinstance(obj.get(k), str) or not 1 <= len(obj[k]) <= 128
                   for k in CORE_FIELDS if k not in ('gt_sha', 'seed')):
                continue
            if any(not isinstance(obj[k], (str, int)) or isinstance(obj[k], bool)
                   for k in OPTIONAL_FIELDS if k in obj):
                continue
            rec = {k: obj[k] for k in ACCEPTED_FIELDS if k in obj}
            if case in result and result[case] != rec:
                conflicts.add(case)
            else:
                result[case] = rec
    return {case:rec for case,rec in result.items() if case not in conflicts}


def profiles_equal(failed, passed):
    if not failed or not passed:
        return False
    if failed.get('testcase') != passed.get('testcase') or failed.get('status') != 'FAIL' or passed.get('status') != 'PASS':
        return False
    if any(failed.get(k) != passed.get(k) for k in CORE_FIELDS):
        return False
    # A field present on only one side cannot be demonstrated equivalent.
    for k in OPTIONAL_FIELDS:
        if (k in failed) != (k in passed) or (k in failed and failed[k] != passed[k]):
            return False
    return True


def _scope(job):
    event = job.get('run_event')
    if event in ('push','schedule','workflow_dispatch') and job.get('head_branch') == 'main' and (
            job.get('head_repository')==job.get('source_repository') and job.get('source_repository')):
        return ('main',)
    if event in ('pull_request', 'pull_request_target'):
        numbers = tuple(sorted(set(str(v) for v in job.get('pr_numbers', []) if v)))
        return ('pr', numbers) if numbers else None
    return None


def comparable_jobs(failed, passed):
    keys = ('workflow', 'name', 'source_repository', 'head_repository')
    if any(not failed.get(k) or failed.get(k) != passed.get(k) for k in keys):
        return False
    if (failed.get('runner_group_name') or passed.get('runner_group_name')) and (
        failed.get('runner_group_name') != passed.get('runner_group_name')):
        return False
    scope = _scope(failed)
    if not scope or scope != _scope(passed):
        return False
    if not SHA_RE.fullmatch(str(failed.get('head_sha', ''))) or not SHA_RE.fullmatch(str(passed.get('head_sha', ''))):
        return False
    # The code commit must have changed; retries of the same SHA do not prove a fix.
    if failed['head_sha'] == passed['head_sha']:
        return False
    try:
        fail_time = datetime.fromisoformat(failed['created_at'].replace('Z', '+00:00'))
        pass_time = datetime.fromisoformat(passed['created_at'].replace('Z', '+00:00'))
    except (KeyError, ValueError, AttributeError):
        return False
    return pass_time > fail_time


def historical_failures(data, history_files):
    """Find newest structured FAIL for each known signature in saved snapshots.

    Never infer the identity from titles or generated LLM prose. Prevent a
    prior failed job being silently overwritten by a newer snapshot.
    """
    result = {}
    snapshots = [data]
    for path in history_files:
        try:
            snapshots.append(json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, ValueError):
            continue
    try:
        from scripts.npu_diffusion_ci_report import _case_records_for_job
    except ImportError:
        from npu_diffusion_ci_report import _case_records_for_job
    for snap in snapshots:
        for job in snap.get('jobs', []):
            if job.get('conclusion') not in ('failure','timed_out','startup_failure','action_required'):
                continue
            for item in _case_records_for_job(job):
                signature = item.get('fingerprint')
                case = item.get('primary_test_case')
                record = (job.get('case_evidence') or {}).get(case)
                if not signature or not case or not record or record.get('status') != 'FAIL':
                    continue
                previous = result.get(signature)
                if previous is None or str(job.get('created_at','')) > str(previous['job'].get('created_at','')):
                    result[signature] = {'job': job, 'case': case, 'config': record}
    return result


def verify_resolution(source_gh, data, issues, continuity, history_files, *, limit=24,
                      output_dir='reports/verification-logs', allow_pr=False):
    """Return independently verified fix proofs (read-only source API).

    Requires two distinct completed successful workflow runs, verified exact
    testcase PASS, changed SHA, profile equality, same workflow/matrix/scope,
    no same-incident failure in the current scan, and COMPLETE scan coverage.
    A bounded number of success logs are downloaded; never spends GLM tokens.
    """
    try:
        from scripts.incident_lifecycle import coverage_complete, FP_RE, _id
    except ImportError:
        from incident_lifecycle import coverage_complete, FP_RE, _id
    if (not coverage_complete(data) or not 1 <= limit <= 200
        or int(data.get('log_download_failed', 0)) != 0
        or int(data.get('failed_jobs_without_logs', 0)) != 0
        or int(data.get('log_truncated', 0)) != 0):
        return {}
    baselines = historical_failures(data, history_files)
    candidates = {}
    issues_by_id = {_id(issue): issue for issue in issues}
    for cid in continuity.get('not_observed', []):
        issue = issues_by_id.get(cid)
        if not issue or issue.get('state') != 'open' or not issue.get('number'):
            continue
        m = FP_RE.search(issue.get('body') or '')
        if not m or m.group(1) not in baselines:
            continue
        baseline = baselines[m.group(1)]
        job = baseline['job']
        if _scope(job) is None or (_scope(job)[0] == 'pr' and not allow_pr):
            continue
        # A new failed job in the same workflow/matrix/scope after the baseline
        # is unresolved even if reducer could not recognize its testcase.
        later_failures = [r for r in data.get('jobs', [])
                          if r.get('conclusion') in ('failure','timed_out','startup_failure','action_required')
                          and all(r.get(k) == job.get(k) for k in
                                  ('workflow','name','source_repository','head_repository'))
                          and _scope(r) == _scope(job)
                          and str(r.get('created_at') or '') > str(job.get('created_at') or '')]
        if later_failures:
            continue
        candidates[cid] = (issue, m.group(1), baseline)
    if not candidates:
        return {}
    successes = sorted((j for j in data.get('jobs', []) if j.get('conclusion') == 'success'),
                       key=lambda j:j.get('created_at') or '', reverse=True)
    proofs, examined, cache = {}, 0, {}
    for cid, (issue, fp, baseline) in candidates.items():
        passing=[]
        for job in successes:
            if examined >= limit:
                break
            if not comparable_jobs(baseline['job'], job):
                continue
            job_id = job.get('job_id')
            if not isinstance(job_id, int):
                continue
            if job_id not in cache:
                examined += 1
                output = None
                try:
                    Path(output_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
                    Path(output_dir).chmod(0o700)
                    fd, name = tempfile.mkstemp(prefix='job-%s-' % job_id,
                                                suffix='.log', dir=output_dir)
                    os.close(fd)
                    output = Path(name)
                    transfer = source_gh.request('GET', '/repos/%s/actions/jobs/%d/logs' % (
                        data.get('source_repository') or 'sgl-project/sglang', job_id), stream_to=output)
                    if isinstance(transfer,dict) and transfer.get('truncated'):
                        cache[job_id] = {}
                    else:
                        cache[job_id] = parse_evidence(output)
                except Exception:
                    cache[job_id] = {}
                finally:
                    if output is not None:
                        output.unlink(missing_ok=True)
            record = cache[job_id].get(baseline['case'])
            if record and profiles_equal(baseline['config'], record):
                # A new SHA must descend from the failed SHA. A different
                # unrelated branch is not evidence that the fix landed.
                try:
                    compare = source_gh.get('/repos/%s/compare/%s...%s' % (
                        data.get('source_repository') or 'sgl-project/sglang',
                        baseline['job']['head_sha'], job['head_sha']))
                    descended = (compare.get('status') == 'ahead'
                        and compare.get('behind_by') == 0 and compare.get('ahead_by', 0) > 0)
                except Exception:
                    descended = False
                if not descended:
                    continue
                if job.get('run_id') not in {e['run_id'] for e in passing}:
                    passing.append({'run_id': job['run_id'], 'job_id': job_id,
                                    'url':job.get('url'), 'head_sha':job['head_sha'],
                                    'created_at':job['created_at']})
            if len(passing)>=2:
                break
        if len(passing)>=2:
            proofs[cid]={'fingerprint':fp,'issue_number':issue['number'],
                         'case':baseline['case'], 'failure_job':baseline['job'].get('job_id'),
                         'failure_sha':baseline['job']['head_sha'],
                         'failure_created_at':baseline['job']['created_at'],
                         'profile':{k: baseline['config'].get(k) for k in CORE_FIELDS + OPTIONAL_FIELDS if k in baseline['config']},
                         'passed':passing[:2], 'snapshot':data.get('generated_utc')}
    return proofs


def apply_verified_resolutions(gh, proofs, issues, *, enabled=True):
    """Atomic Issue body/state patch, guarded by fresh fingerprint recheck."""
    if not enabled:
        return []
    try:
        from scripts.incident_lifecycle import FP_RE, _id, _labels, LIFECYCLE_START, LIFECYCLE_END
    except ImportError:
        from incident_lifecycle import FP_RE, _id, _labels, LIFECYCLE_START, LIFECYCLE_END
    try:
        from scripts.npu_diffusion_ci_report import TARGET_REPO
    except ImportError:
        from npu_diffusion_ci_report import TARGET_REPO
    closed=[]
    for cid, proof in proofs.items():
        try:
            issue=gh.get('/repos/%s/issues/%s'%(TARGET_REPO,proof['issue_number']))
            marker=FP_RE.search(issue.get('body') or '')
            if (issue.get('state') != 'open' or _id(issue)!=cid or not marker
                or marker.group(1)!=proof['fingerprint']
                or 'npu-diffusion-cluster' not in _labels(issue)):
                continue
            # A concurrent/manual edit after evidence collection invalidates
            # this closure decision; leave Issue open for the next snapshot.
            updated = issue.get('updated_at')
            if updated and proof.get('snapshot') and str(updated) > str(proof['snapshot']):
                continue
            body=issue.get('body') or ''
            evidence='\n'.join('- [Passing job %d](%s) · commit `%s` · run %s' % (
                p['job_id'],p['url'],p['head_sha'],p['run_id']) for p in proof['passed'])
            section=(LIFECYCLE_START+'\n## Verified testcase resolution\n\n'+
                 ('<!-- npu-ci-resolution:verified;testcase=%s;snapshot=%s -->\n'% (proof['case'],proof['snapshot']))+
                 '**Automated resolution:** 2 distinct successful workflow runs, explicit testcase PASS '
                 'for the affected case, matched pinned GT/seed/dtype/scheduler/model/runtime, '
                 'same scope and CI matrix, newer changed commit SHA.\n\n'
                 '**Last observed failure:** job %s on `%s`.\n\n'%(
                     proof['failure_job'],proof['failure_sha'])+evidence+'\n'+LIFECYCLE_END)
            body=re.sub(re.escape(LIFECYCLE_START)+r'.*?'+re.escape(LIFECYCLE_END),
                        '', body, flags=re.S).rstrip()+'\n\n'+section+'\n'
            if len(body)>57000:
                continue
            gh.issue(int(issue['number']), {'body':body, 'state':'closed','state_reason':'completed'})
            closed.append(cid)
        except Exception:
            # Fail closed on any GitHub error; next run can re-evaluate.
            continue
    return closed
