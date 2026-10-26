#!/usr/bin/env python3
"""Bounded, streaming, evidence-oriented SGLang Ascend Diffusion CI log reducer.

Inspired by OrangeRedeng/sglang-ascend-agent-workflow-kit extract-log-context.py.
Unlike a generic frequency-first reducer, preserve domain-specific failures,
metrics and thresholds independently of their numeric values. Raw logs are NEVER
embedded in the reducer's machine-readable output.
Python 3.9+, standard library only.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Optional
import json
import re
from urllib.parse import urlsplit, urlunsplit

MAX_EVENTS = 256
MAX_SAMPLES_PER_EVENT = 2
MAX_ACTIVE_SAMPLES = 48
MAX_CONTEXT_LINE = 800
DEFAULT_OUTPUT_BYTES = 32768
DEFAULT_OUTPUT_LINES = 400

ANSI = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
GH_PREFIX = re.compile(r'^\ufeff?\d{4}-\d{2}-\d{2}T[\d:.+-]+Z\s*')
INNER_TS = re.compile(r'\[\d{1,2}-\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\]')
PYTEST_FAILED = re.compile(r'^(?:\d{4}-\d{2}-\d{2}T[^ ]+Z\s*)?FAILED\s+(?P<node>[^ ]+?\.py::[^ ]+?)(?:\s+-\s+(?P<reason>.*))?$', re.I)
TS = re.compile(r'\b\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]+\b')
RANK = re.compile(r'(?i)\b(?:rank|device|worker)\s*[=:]\s*\d+\b')
PTR = re.compile(r'0x[0-9a-fA-F]{5,}')
NUM_ID = re.compile(r'\b\d{6,}\b')
PATH = re.compile(r'/(?:tmp|home/runner|__w|workspace)/[^\s:]+')
FAIL_TEST = re.compile(r'test_diffusion_generation\[([^\]]+)\]', re.I)
CONSISTENCY = re.compile(r'Consistency check failed for\s+([\w.-]+)', re.I)
CASE = re.compile(r"Diffusion testcase ['\"]?([\w.-]+)", re.I)
METRICS = re.compile(r'(?<![a-zA-Z_])\b(sim|clip|ssim|psnr|mean_abs_diff)\s*=\s*([-+]?(?:\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|inf(?:inity)?))', re.I)
THRESHOLDS = re.compile(r'\b(clip|sim|ssim|psnr|mean_abs_diff)\s*(>=|<=|>|<)\s*([-+]?\d+(?:\.\d+)?)', re.I)
AUTH = re.compile(r'(?i)(\b(?:authorization|x-api-key|api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*(?:bearer\s+|token\s+)?)[^\s,;\]\}]{5,}')
PAT = re.compile(r'\b(?:gh[pousr]_[a-zA-Z0-9_]{18,}|github_pat_[a-zA-Z0-9_]{18,})\b')
URL = re.compile(r'https?://[^\s<>\)\]]+')


def redact(line: str) -> str:
    """Redact common secrets and signed URLs before persistent derived artifacts."""
    line = AUTH.sub(r'\1[REDACTED]', PAT.sub('[REDACTED_GITHUB_TOKEN]', line))

    def url_replacement(m):
        candidate = m.group(0)
        trailing = ''
        while candidate.endswith(('.', ',', ';')):
            trailing = candidate[-1] + trailing
            candidate = candidate[:-1]
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return '[REDACTED_MALFORMED_URL]' + trailing
        if not parsed.query and not parsed.username:
            return candidate + trailing
        host = parsed.hostname or ''
        if port:
            host += ':' + str(port)
        return urlunsplit((parsed.scheme, host, parsed.path,
                           '[REDACTED_QUERY]' if parsed.query else '', '')) + trailing
    return URL.sub(url_replacement, line)


def clean_line(raw: str) -> str:
    return redact(ANSI.sub('', raw.rstrip('\n\r')))[:MAX_CONTEXT_LINE]


def normalize(line: str) -> str:
    line = GH_PREFIX.sub('', ANSI.sub('', line))
    line = INNER_TS.sub('<TS>', line)
    line = TS.sub('<TS>', line)
    line = RANK.sub('<RANK>', line)
    line = PTR.sub('<HEX>', line)
    line = PATH.sub('<PATH>', line)
    line = NUM_ID.sub('<ID>', line)
    line = re.sub(r'\bline\s+\d+\b', 'line <N>', line, flags=re.I)
    line = re.sub(r'\s+', ' ', line).strip().lower()
    return line[:450]


def classify(text: str):
    """(category, priority, testcase) or None; ignore unrelated noisy signals."""
    # HCCL's init line reports the CONFIGURED timeout, not a timeout failure.
    if re.search(r'world_size=\d+.*distributed_init_method=.*backend=hccl\s+timeout=\d+', text, re.I) and not re.search(r'(?i)(?:\b(?:error|failed|exception|timed out)\b)', text):
        return None
    match = CONSISTENCY.search(text)
    if match:
        return 'diffusion_consistency', 120, match.group(1)
    # pytest terminal summary is stronger evidence than incidental early setup logs.
    match = PYTEST_FAILED.search(GH_PREFIX.sub('', text).strip())
    if match:
        node = match.group('node').strip()
        if '/test/' in node or 'test_' in node:
            reason = match.group('reason') or ''
            category = ('performance_regression' if 'PerformanceValidationError' in reason
                        else 'pytest_failure')
            return category, 113, node
    match = FAIL_TEST.search(text)
    if match and (re.search(r'\bFAILED\b', text) or 'Failed:' in text):
        return 'diffusion_test_failure', 95, match.group(1)
    if re.search(r'(?i)\b(?:out of memory|oom|npu malloc failed|aclrt.*alloc failed)\b', text):
        return 'npu_oom', 90, None
    if re.search(r'(?i)\b(?:HCCL|EI\d{4}|EZ\d{4}|ACL_ERROR|AICORE|AICPU)\b', text) and re.search(r'(?i)\b(error|failed|exception|fatal|timed out)\b|\b(?:timeouterror|hccl.*timeout exceeded)\b', text):
        return 'ascend_runtime', 85, None
    if re.search(r'(?i)(?:\b(?:RuntimeError|AssertionError|ValueError|ImportError|OSError|TimeoutError|[a-zA-Z]+Exception):)', text):
        return 'python_exception', 72, None
    match = CASE.search(text)
    if match and 'failed' in text.lower():
        return 'diffusion_test_failure', 95, match.group(1)
    if re.search(r'(?i)\b(?:timed out|timeout(?:error| exceeded)|deadline exceeded|watchdog)\b', text):
        return 'timeout', 55, None
    if 'Traceback (most recent call last)' in text:
        return None
    if re.search(r'(?i)\b(?:failed|fatal|error|exception)\b', text):
        if re.search(r'(?i)##\[error\]|process completed with exit code|pip.s dependency resolver|deprecated|executing the custom container implementation|failed to run script step', text):
            return None
        return 'generic_error', 20, None
    return None


def stable_key(category: str, message: str, testcase: Optional[str]) -> str:
    # Metrics are deliberately not part of the signature: the same testcase
    # failing SSIM with a different value is the same failure family.
    # Do not collapse different categories (e.g. performance vs consistency).
    # Normalization handles runner timestamps even for unclassified legacy logs.
    basis = f'{category}|{testcase.lower()}' if testcase else f'{category}|{normalize(message)}'
    return sha256(basis.encode('utf-8', 'replace')).hexdigest()[:14]


@dataclass
class Window:
    center: int
    rows: list = field(default_factory=list)
    remaining: int = 0


@dataclass
class Event:
    id: str
    category: str
    signature: str
    severity: int
    testcase: Optional[str] = None
    count: int = 0
    first_line: int = 0
    last_line: int = 0
    samples: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    failed_checks: list = field(default_factory=list)
    failed_frames: list = field(default_factory=list)

    def to_dict(self):
        return dict(id=self.id, category=self.category, severity=self.severity,
                    signature=self.signature, testcase=self.testcase, count=self.count,
                    first_line=self.first_line, last_line=self.last_line,
                    metrics=self.metrics, thresholds=self.thresholds,
                    failed_checks=self.failed_checks, failed_frames=self.failed_frames,
                    windows=[{'line': w.center, 'rows': w.rows[:42]} for w in self.samples])


def parse_consistency_context(event: Event, line: str):
    """Keep testcase summary metrics distinct from each frame's metrics.

    For video tests the failing CLIP frame can be f0 while f2 passes CLIP.
    Never overwrite the failure summary with later per-frame values.
    """
    if event.category != 'diffusion_consistency':
        return
    line = GH_PREFIX.sub('', line)
    frame = re.search(r'\bf(?P<id>\d+)\s*\[(?P<checks>[^\]]+)\]', line, re.I)
    if frame:
        name = 'f' + frame.group('id')
        checks = [term.strip().lower() for term in frame.group('checks').split(',')
                  if term.strip().lower() in ('ssim', 'psnr', 'clip', 'mean_abs_diff', 'sim')]
        values = {key.lower():(value.lower() if 'inf' in value.lower() else float(value)) for key,value in METRICS.findall(line)}
        if not any(x['frame'] == name for x in event.failed_frames) and len(event.failed_frames) < 16:
            event.failed_frames.append({'frame':name, 'failed_checks':checks, 'metrics':values})
        for check in checks:
            if check not in event.failed_checks:
                event.failed_checks.append(check)
        return
    if re.search(r'(?i)\bMetrics\s*:', line):
        for name,value in METRICS.findall(line):
            event.metrics[name.lower()] = value.lower() if 'inf' in value.lower() else float(value)
    if re.search(r'(?i)\bThresholds\s*:', line):
        for name,cmp,value in THRESHOLDS.findall(line):
            event.thresholds[name.lower()] = {'op':cmp,'value':float(value)}


def repair_consistency_job_from_saved_evidence(job: dict) -> bool:
    """Correct v10/v11 frame-overwritten quality metrics from saved redacted context.

    No GitHub request or raw-log access; only act when aggregate Metrics and
    Thresholds lines are both available for the exact known primary testcase.
    """
    if job.get('failure_category') != 'diffusion_consistency':
        return False
    case = job.get('primary_test_case')
    content = job.get('expanded_evidence', '')
    if not isinstance(case, str) or not isinstance(content, str) or not content:
        return False
    event = Event(id='recovered', category='diffusion_consistency', signature='',
                  severity=0, testcase=case)
    active = False
    remaining = 20
    for line in content.splitlines():
        m = CONSISTENCY.search(line)
        if m:
            if active or m.group(1) != case:
                if active:
                    break
                continue
            active = True
            remaining = 20
            continue
        if not active:
            continue
        parse_consistency_context(event, line)
        remaining -= 1
        if remaining <= 0:
            break
    if not (event.metrics and event.thresholds):
        return False
    job['metrics'] = dict(event.metrics)
    job['thresholds'] = dict(event.thresholds)
    if event.failed_frames:
        job['failed_frames'] = list(event.failed_frames)
        job['failed_checks'] = list(event.failed_checks)
    job['saved_evidence_reconciled'] = True
    return True


def recover_consistency_event_from_windows(event: dict) -> dict:
    """Repair aggregate metrics of a testcase from saved event windows.

    Pre-v12 captures can accidentally keep the last frame's metrics in the
    event object. Only replace with an explicitly labelled summary Metrics
    and Thresholds pair; never infer aggregate values from an individual frame.
    """
    if event.get('category') != 'diffusion_consistency' or not event.get('testcase'):
        return event
    case = event['testcase']
    recovered = Event(id=event.get('id', ''), category='diffusion_consistency',
                      signature=event.get('signature', ''), severity=event.get('severity', 120),
                      testcase=case)
    for window in event.get('windows', [])[:2]:
        active = False
        remaining = 20
        for row in window.get('rows', []):
            line = row[1] if isinstance(row, (list, tuple)) and len(row) > 1 else ''
            if not isinstance(line, str):
                continue
            match = CONSISTENCY.search(line)
            if match:
                if active or match.group(1) != case:
                    if active:
                        break
                    continue
                active = True
                remaining = 20
                continue
            if not active:
                continue
            parse_consistency_context(recovered, line)
            remaining -= 1
            if remaining <= 0:
                break
        if recovered.metrics and recovered.thresholds:
            break
    if recovered.metrics and recovered.thresholds:
        event = dict(event)
        event['metrics'] = dict(recovered.metrics)
        event['thresholds'] = dict(recovered.thresholds)
        if recovered.failed_frames:
            event['failed_frames'] = list(recovered.failed_frames)
            event['failed_checks'] = list(recovered.failed_checks)
        event['saved_evidence_reconciled'] = True
    return event


def failure_cases_from_events(events: list, primary: Optional[dict] = None) -> list:
    """Extract *every* failed diffusion testcase, not just the primary event.

    For non-parametrized failures, preserve the single primary failure.
    A job with both Qwen and MiniMax failures must enter both clusters.
    """
    cases = []
    seen = set()
    consistency_cases = {e.get('testcase') for e in events
        if isinstance(e, dict) and e.get('category') == 'diffusion_consistency'}
    for source in events:
        if not isinstance(source, dict) or source.get('category') not in (
                'diffusion_consistency', 'diffusion_test_failure',
                'pytest_failure', 'performance_regression'):
            continue
        if not source.get('testcase') or not source.get('id') or source['id'] in seen:
            continue
        if source.get('category') in ('pytest_failure','diffusion_test_failure'):
            param = re.search(r'\[([^\]]+)\]$', str(source['testcase']))
            if (param and param.group(1) in consistency_cases) or source['testcase'] in consistency_cases:
                continue
        seen.add(source['id'])
        event = recover_consistency_event_from_windows(source)
        rows = [str(row[1]) for w in event.get('windows', [])[:1]
                for row in w.get('rows', []) if isinstance(row, (tuple, list)) and len(row) > 1]
        # Context before the signature can belong to an unrelated failed
        # testcase. Never attribute the previous testcase's evidence to this one.
        start_index = next((i for i, line in enumerate(rows)
                            if (m := CONSISTENCY.search(line)) and m.group(1) == event['testcase']), None)
        if start_index is not None:
            rows = rows[start_index:start_index + 14]
            for i in range(1, len(rows)):
                m = CONSISTENCY.search(rows[i])
                if m and m.group(1) != event['testcase']:
                    rows = rows[:i]
                    break
        cases.append({
            'id': event['id'], 'category': event.get('category', 'diffusion_consistency'),
            'signature': event.get('signature', ''), 'testcase': event['testcase'],
            'severity': event.get('severity', 120),
            'metrics': event.get('metrics', {}), 'thresholds': event.get('thresholds', {}),
            'failed_checks': event.get('failed_checks', []),
            'failed_frames': event.get('failed_frames', [])[:16],
            'log_excerpt': rows[-20:],
            'saved_evidence_reconciled': bool(event.get('saved_evidence_reconciled')),
        })
    # Do not manufacture a standalone incident from setup warnings or generic
    # events when no testcase-level failure could be identified.
    if not cases and isinstance(primary, dict) and primary.get('id') and (
            primary.get('category') in ('diffusion_consistency', 'diffusion_test_failure',
                                        'pytest_failure', 'performance_regression',
                                        'npu_oom', 'ascend_runtime', 'python_exception')):
        p = primary
        cases = [{'id': p['id'], 'category': p.get('category', 'unclassified'),
                  'signature': p.get('signature', ''), 'testcase': p.get('testcase'),
                  'severity': p.get('severity', 0),
                  'metrics': p.get('metrics', {}), 'thresholds': p.get('thresholds', {}),
                  'failed_checks': p.get('failed_checks', []),
                  'failed_frames': p.get('failed_frames', [])[:16],
                  'log_excerpt': [str(row[1]) for w in p.get('windows', [])[:1]
                                  for row in w.get('rows', []) if len(row) > 1][-20:]}]
    return cases


def analyze_file(path: Path, *, context: int = 6, max_events: int = MAX_EVENTS,
                 max_samples: int = MAX_SAMPLES_PER_EVENT,
                 max_bytes: int = DEFAULT_OUTPUT_BYTES,
                 max_lines: int = DEFAULT_OUTPUT_LINES) -> dict:
    """One linear pass. Events/contexts are bounded even with millions of lines."""
    path = Path(path)
    context = max(0, min(context, 40))
    events = {}
    head = []
    tail = deque(maxlen=20)
    before = deque(maxlen=context)
    active = []
    current_consistency = None
    consistency_remaining = 0
    total = matched = overflow = 0
    categories = Counter()
    with path.open('r', encoding='utf-8-sig', errors='replace') as fh:
        for lineno, raw in enumerate(fh, 1):
            total = lineno
            line = clean_line(raw)
            if lineno <= 10:
                head.append([lineno, line])
            tail.append([lineno, line])
            kept = []
            for window in active:
                if window.remaining > 0:
                    window.rows.append([lineno, line])
                    window.remaining -= 1
                if window.remaining > 0:
                    kept.append(window)
            active = kept
            if current_consistency and consistency_remaining:
                parse_consistency_context(current_consistency, line)
                consistency_remaining -= 1
                if consistency_remaining == 0:
                    current_consistency = None
            kind = classify(line)
            if kind:
                category, severity, testcase = kind
                matched += 1
                categories[category] += 1
                k = stable_key(category, line, testcase)
                event = events.get(k)
                if event is None:
                    if len(events) >= max_events:
                        # Keep a reserved path for later, rare critical failures:
                        # noisy generic errors must not fill the entire dictionary.
                        weakest = min(events.values(), key=lambda e: (e.severity, e.count))
                        if severity > weakest.severity:
                            del events[weakest.id]
                        else:
                            overflow += 1
                            before.append([lineno, line])
                            continue
                    # Suppress timestamps and incidental numeric noise in signature.
                    event = Event(id=k, category=category, signature=clean_line(GH_PREFIX.sub('', line))[:350],
                                  severity=severity, testcase=testcase)
                    events[k] = event
                event.count += 1
                event.first_line = event.first_line or lineno
                event.last_line = lineno
                if category == 'diffusion_consistency':
                    current_consistency = event
                    consistency_remaining = 15
                if len(event.samples) < max_samples and len(active) < MAX_ACTIVE_SAMPLES:
                    prev_center = event.samples[-1].center if event.samples else -1_000_000
                    if lineno - prev_center > max(2 * context + 1, 8):
                        sample = Window(center=lineno, rows=list(before) + [[lineno, line]], remaining=context)
                        event.samples.append(sample)
                        if context:
                            active.append(sample)
            before.append([lineno, line])
    ranked = sorted(events.values(), key=lambda e: (-e.severity, -e.count, e.first_line))
    primary = ranked[0] if ranked else None
    try:
        from scripts.testcase_failures import parse_testcase_failures, diagnostic
    except ImportError:
        from testcase_failures import parse_testcase_failures, diagnostic
    if primary and primary.category == 'python_exception':
        exceptions = sorted((e for e in ranked if e.category == 'python_exception'), key=lambda e:e.last_line)
        selected = diagnostic([e.signature for e in exceptions])
        primary = next((e for e in exceptions if e.signature == selected), primary)
    parsed_cases, accounting = parse_testcase_failures(path)
    legacy_cases = failure_cases_from_events([e.to_dict() for e in ranked], primary.to_dict() if primary else None)
    # Consistency events retain richer metrics than the terminal pytest wrapper.
    rich = {e['testcase']:e for e in legacy_cases if e['category']=='diffusion_consistency'}
    cases = [rich.get(e['testcase'],e) for e in parsed_cases]
    for case in cases:
        parsed=next((p for p in parsed_cases if p['testcase']==case['testcase']),{})
        for key in ('execution_sections','fingerprint_aliases','performance','http_statuses'):
            if key in parsed:case[key]=parsed[key]
    known = {e['testcase'] for e in cases} | {e.get('nodeid') for e in cases}
    cases += [e for e in legacy_cases if e.get('testcase') not in known and e.get('testcase')]
    if not cases: cases = legacy_cases
    body = render_focused(ranked, total, matched, overflow, categories, head, list(tail),
                          max_bytes=max_bytes, max_lines=max_lines)
    return {
        'version': 2,
        'failure_cases': cases, 'testcase_accounting': accounting,
        'source_bytes': path.stat().st_size,
        'total_lines': total, 'matched_lines': matched,
        'unique_signatures': len(events), 'signature_overflow': overflow,
        'categories': dict(categories),
        'primary': primary.to_dict() if primary else None,
        'events': [e.to_dict() for e in ranked[:20]],
        'focused_text': body,
    }


def render_focused(events, total, matched, overflow, categories, head, tail, *, max_bytes, max_lines):
    lines = []
    used = 0
    def add(line):
        nonlocal used
        item = redact(str(line))[:MAX_CONTEXT_LINE+50]
        length = len((item+'\n').encode('utf-8','replace'))
        if len(lines) >= max_lines or used + length > max_bytes:
            return False
        lines.append(item)
        used += length
        return True
    for h in (f'TOTAL_LINES: {total}', f'MATCHES: {matched}',
              f'UNIQUE_SIGNATURES: {len(events)}', f'SIGNATURE_OVERFLOW: {overflow}',
              '=== HIGH-PRIORITY SIGNATURES ==='):
        add(h)
    for event in events[:20]:
        if not add(f'{event.id} P{event.severity} {event.category} count={event.count} '
                   f'first={event.first_line} last={event.last_line} {event.signature}'):
            break
    add('=== CATEGORY FREQUENCIES ===')
    for category, cnt in categories.most_common(20):
        add(f'{category}: {cnt}')
    add('=== SELECTED EVIDENCE WINDOWS ===')
    for event in events[:12]:
        if not add(f'--- {event.id} {event.category} ---'):
            break
        if event.metrics:
            add('metrics=' + json.dumps(event.metrics, sort_keys=True))
        if event.thresholds:
            add('thresholds=' + json.dumps(event.thresholds, sort_keys=True))
        if event.failed_frames:
            add('failed_frames=' + json.dumps(event.failed_frames[:8], sort_keys=True))
        for sample in event.samples:
            for num, text in sample.rows:
                if not add(f'{num:8d}: {text}'):
                    break
    add('=== LOG TAIL ===')
    for num, text in tail[-20:]:
        if not add(f'{num:8d}: {text}'):
            break
    return '\n'.join(lines)+'\n'


def expand_file(path: Path, signature_id: str, *, context: int = 20,
                max_bytes: int = 32768, max_lines: int = 240):
    """Targeted second pass over the saved raw log, with genuinely wider context."""
    path = Path(path)
    context = max(1, min(context, 80))
    before = deque(maxlen=context)
    active = []
    rows = []
    used = 0
    matches = 0
    with path.open('r', encoding='utf-8-sig', errors='replace') as fh:
        for n, raw in enumerate(fh, 1):
            line = clean_line(raw)
            for w in list(active):
                if w.remaining:
                    w.rows.append([n, line]); w.remaining -= 1
                if not w.remaining:
                    active.remove(w)
            info = classify(line)
            if info and stable_key(info[0], line, info[2]) == signature_id:
                matches += 1
                if matches <= 2:
                    w = Window(center=n, rows=list(before)+[[n,line]], remaining=context)
                    active.append(w)
                    rows.append(w)
            before.append([n,line])
    if not matches:
        raise ValueError('Signature not found in raw log')
    output = []
    for w in rows:
        part = f'--- signature {signature_id} near line {w.center} ---\n'
        for n, line in w.rows:
            part += f'{n}: {line}\n'
        nbytes = len(part.encode('utf-8','replace'))
        if used+nbytes > max_bytes or len(output)+len(w.rows)+1 > max_lines:
            break
        output.append(part); used+=nbytes
    return {'signature_id': signature_id, 'match_count': matches,
            'text': '\n'.join(output), 'context': context}
