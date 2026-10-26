"""Streaming testcase accounting, independent of the bounded diagnostic display.

A diagnostic signature is evidence of a symptom, never a claim of root cause.
Pytest report sections own their exceptions; unrelated job output is not copied
into summary-only cases. All storage caps are explicit in accounting metadata.
"""
from __future__ import annotations
import re
from hashlib import sha256
try:
    from scripts.log_context import clean_line, GH_PREFIX, normalize
except ImportError:
    from log_context import clean_line, GH_PREFIX, normalize

SUMMARY = re.compile(r'^(FAILED|ERROR)\s+(.+?\.py::.+?)(?:\s+-\s+(.*))?$', re.I)
HEADER = re.compile(r'^_{2,}\s+(.+?)\s+_{2,}$')
EXCEPTION = re.compile(r'^(?:E\s+)?(?:[\w.]+(?:Error|Exception)|Exception):\s*\S', re.I)
HTTP = re.compile(r'\b(?:HTTP(?:Error)?[: /]*|)([45]\d\d)\s+(?:Client|Server) Error|HTTP/\d(?:\.\d)?[" ]+([45]\d\d)', re.I)
WRAPPER = re.compile(r'retry\(\).*maximum|scheduler: Unknown error|Model generation returned no output|Server exited early|diffusion worker.*(?:failed|start)', re.I)
MAX_CASES = 4096
try:
    from scripts.analysis_evidence import performance_measurements
except ImportError:
    from analysis_evidence import performance_measurements


def diagnostic(lines, fallback=''):
    """Prefer a concrete terminal exception over traceback headers/wrappers."""
    candidates=[]
    for i,line in enumerate(lines):
        text=GH_PREFIX.sub('',line).strip()
        text=re.sub(r'^E\s+', '', text)
        if 'Traceback (most recent call last)' in text:
            continue
        if EXCEPTION.search(text) or HTTP.search(text) or re.search(
                r'cannot honor requested distributed execution|out of memory|Consistency check failed',text,re.I):
            score=1 if WRAPPER.search(text) else 2
            if 'cannot honor requested distributed execution' in text: score=3
            if re.search(r'\b(?:ModuleNotFoundError|ImportError):', text): score=4
            # Include continuation lines of an exception, without stack frames.
            continuation=[]
            for nxt in lines[i+1:i+5]:
                nxt=GH_PREFIX.sub('',nxt).strip()
                nxt=re.sub(r'^E\s+', '',nxt)
                if re.search(r'^(?:sp_degree|ulysses_degree|ring_degree|Native Diffusers|cannot honor)',nxt):
                    continuation.append(nxt)
                else: break
            candidates.append((score,i,' '.join([text]+continuation)))
    return max(candidates,default=(0,0,fallback.strip()))[2][:800]


def _alias(node):
    return node.split('::',1)[-1].replace('::','.')


def symptom_key(category, testcase, signature):
    text=normalize(signature)
    text=re.sub(r'(https?://(?:localhost|127\.0\.0\.1|\[::1\])):\d+',r'\1:<port>',text)
    text=re.sub(r'\bpid[=: ]+\d+\b','pid=<pid>',text)
    if category in ('performance_regression','diffusion_consistency'):
        text=category # metric values vary; retain separate structured measurements
    return sha256(f'{category}|{testcase.lower()}|{text}'.encode()).hexdigest()[:14]


def parse_testcase_failures(path, max_cases=MAX_CASES):
    sections={}; summaries={}; active=None; rows=[]; overflow=0; saved_performance=[]; http_statuses=[]
    def flush():
        nonlocal overflow
        if active is not None:
            measurements=[]
            seen=set()
            for item in saved_performance+performance_measurements(rows):
                key=tuple(sorted((k,str(v)) for k,v in item.items() if k!='evidence'))
                if key not in seen: measurements.append(item); seen.add(key)
            sections.setdefault(active,[])
            if len(sections[active])<64:
                sections[active].append({'signature':diagnostic(rows),'rows':rows[-20:],
                    'performance':measurements[:12], 'http_statuses':list(http_statuses)})
            else:overflow+=1
    with path.open(encoding='utf-8-sig',errors='replace') as source:
        for raw in source:
            line=GH_PREFIX.sub('',clean_line(raw)).strip()
            header=HEADER.match(line)
            if header:
                flush(); active=header.group(1); rows=[]; saved_performance=[]; http_statuses=[]
                if len(sections)>=max_cases and active not in sections:
                    active=None; overflow+=1
                continue
            match=SUMMARY.match(line)
            if match:
                flush(); active=None; rows=[]
                _,node,reason=match.groups()
                if len(summaries)>=max_cases and node not in summaries:
                    overflow+=1; continue
                summaries[node]=reason or ''
            elif active:
                for match in re.finditer(r'\b([45]\d\d)\s+(?:Client|Server) Error|\bHTTP(?:/\d(?:\.\d)?[" ]+|[ :]+)([45]\d\d)\b',line,re.I):
                    status=int(match[1] or match[2])
                    if status not in http_statuses:http_statuses.append(status)
                # Keep diagnostic candidates even in a very long stack trace.
                if len(rows)>=160:
                    for measurement in performance_measurements(rows):
                        if measurement not in saved_performance and len(saved_performance)<12:saved_performance.append(measurement)
                    best=diagnostic(rows)
                    rows=([best] if best else [])+rows[-40:]
                rows.append(line)
    flush()
    cases=[]
    for node,reason in summaries.items():
        attempts=sections.get(_alias(node),[])
        # Headers for setup/teardown use descriptive prefixes.
        if not attempts:
            matches=[v for k,v in sections.items() if k.endswith(' '+_alias(node))]
            if len(matches)==1: attempts=matches[0]
        section=attempts[-1] if attempts else {}
        signature=diagnostic([section.get('signature',''),reason], reason)
        if not signature: signature='Unclassified pytest failure (no diagnostic exception retained)'
        category=('unclassified' if signature.startswith('Unclassified') else
                  'performance_regression' if 'PerformanceValidationError' in signature
                  else 'pytest_failure')
        param=re.search(r'test_diffusion_generation\[([^\]]+)\]$',node)
        testcase=param.group(1) if param else node
        cases.append({'id':symptom_key(category,testcase,signature),
                      'category':category,'testcase':testcase,'nodeid':node,
                      'signature':signature,'severity':113,
                      'fingerprint_aliases':[symptom_key(category,testcase,reason)],
                      'execution_sections':attempts,
                      'http_statuses':section.get('http_statuses',[]),
                      'performance': [dict(m,section_attempt=i+1) for i,a in enumerate(attempts)
                                      for m in a['performance']] or performance_measurements([reason]),
                      'log_excerpt':section.get('rows',[]) + [reason],
                      'evidence_source':'pytest_summary'})
    return cases, {'summary_testcases':len(summaries),'case_overflow':overflow,
                   'summary_present':bool(summaries),
                   'complete':bool(summaries) and overflow==0}
