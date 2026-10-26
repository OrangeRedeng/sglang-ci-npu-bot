"""Observed execution verdicts; these never authorize automatic incident closure."""
from __future__ import annotations
import re
try:
    from scripts.log_context import clean_line, GH_PREFIX
    from scripts.testcase_failures import diagnostic
    from scripts.analysis_evidence import performance_measurements
except ImportError:
    from log_context import clean_line, GH_PREFIX
    from testcase_failures import diagnostic
    from analysis_evidence import performance_measurements

NODE = re.compile(r'(?P<node>[\w./-]+\.py::[\w.]+(?:::[\w.]+)*(?:\[[^\]]+\])?)')
FINAL = re.compile(r'\[server-test\]\s+(PASSED|FAILED|SKIPPED) diffusion testcase:\s*(\S+)')
NUMBER = r'([-+]?(?:\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|inf(?:inity)?))'
METRIC = re.compile(r'\b(clip|sim|ssim|psnr|mean_abs_diff)\s*=\s*'+NUMBER, re.I)


def case_name(node):
    m = re.search(r'test_diffusion_generation\[([^\]]+)\]$', node)
    return m[1] if m else node


def parse_observations(path, limit=4096):
    observations=[]; active=None; rows=[]; exceptions=[]; metrics={}; checks=[]
    performance=[]; overflow=0; unowned=[]; seen_measurements=set(); configuration=[]

    def retain_measurements():
        for m in performance_measurements(rows):
            key=tuple(sorted((k,str(v)) for k,v in m.items() if k!='evidence'))
            if key not in seen_measurements and len(performance)<64:
                seen_measurements.add(key); performance.append(m)

    def finish(status='UNKNOWN', line=None):
        nonlocal active
        if active is None:return
        retain_measurements()
        active.update(status=status, end_line=line, performance=list(performance),
                      metrics=dict(metrics), checks=list(checks), exception_chain=list(exceptions))
        root=diagnostic([x['text'] for x in exceptions])
        # Only a concrete import failure with its module path supports a shared-cause link.
        imports=[x for x in exceptions if re.search(r'\b(?:ModuleNotFoundError|ImportError):',x['text'])]
        active['diagnostic']=root
        if imports:
            active['causal_signature']=imports[0]['text']
            active['causal_origin']=imports[0].get('origin')
        observations.append(active); active=None

    def begin(node, lineno):
        nonlocal active, rows, exceptions, metrics, checks, performance, overflow, seen_measurements
        if active and active['nodeid']==node:return
        finish(line=lineno-1)
        if len(observations)>=limit:overflow+=1; return
        name=case_name(node)
        active={'testcase':name,'nodeid':node,'start_line':lineno,
                'execution_attempt':1+sum(x['testcase']==name for x in observations),
                'evidence_source':'execution_log'}
        rows=[]; exceptions=[]; metrics={}; checks=[]; performance=[]; seen_measurements=set()

    with path.open(encoding='utf-8-sig', errors='replace') as source:
        for lineno,raw in enumerate(source,1):
            line=GH_PREFIX.sub('',clean_line(raw)).strip()
            if re.search(r'--suite\s|SGLANG_(?:CONSISTENCY_GT_DIR|TEST_FILES_CI_DATA_REVISION)|coverage_mode[=:]|(?:CANN|torch_npu|torch-npu|triton-ascend).*version',line,re.I):
                if len(configuration)<64:configuration.append({'line':lineno,'text':line})
            node=NODE.match(line)
            # Traceback/summary references must never reopen an execution.
            if node and not re.search(r' in |:\d+:',line):
                rest=line[node.end():].strip()
                if not rest or rest.startswith(('[CONFTEST]', 'PASSED','FAILED','SKIPPED','ERROR')):
                    begin(node['node'],lineno)
                    verdict=re.match(r'(PASSED|FAILED|SKIPPED|ERROR)\b',rest)
                    if verdict:finish({'PASSED':'PASS','FAILED':'FAIL','ERROR':'FAIL','SKIPPED':'SKIP'}[verdict[1]],lineno)
            final=FINAL.search(line)
            if final:
                if active and active['testcase']==final[2]:
                    finish({'PASSED':'PASS','FAILED':'FAIL','SKIPPED':'SKIP'}[final[1]],lineno)
                elif active is None and len(observations)<limit:
                    begin(final[2],lineno)
                    finish({'PASSED':'PASS','FAILED':'FAIL','SKIPPED':'SKIP'}[final[1]],lineno)
                continue
            if re.fullmatch(r'(PASSED|FAILED|SKIPPED|ERROR)(?:\s+\[.*\])?',line) and active:
                finish({'PASSED':'PASS','FAILED':'FAIL','ERROR':'FAIL','SKIPPED':'SKIP'}[line.split()[0]],lineno)
                continue
            if active and (line.startswith(('FAILED ', 'ERROR ', '__ ')) or
                           re.match(r'^=+\s+(?:FAILURES|ERRORS|short test summary|warnings summary|test session starts)',line,re.I)):
                finish(line=lineno-1)
            exc=re.search(r'\b(?:[\w.]+(?:Error|Exception)|Exception):\s*\S.*',line)
            frame=re.search(r'File "([^"]+)", line (\d+)',line)
            if frame and active:
                active['last_frame']={'path':frame[1],'line':int(frame[2]),'log_line':lineno}
            if exc:
                item={'line':lineno,'text':exc[0]}
                if active and active.get('last_frame'): item['origin']=active['last_frame']['path']
                target=exceptions if active else unowned
                if not any(x['text']==item['text'] for x in target) and len(target)<64:target.append(item)
            if active:
                rows.append(line)
                if len(rows)>=160:retain_measurements(); rows=rows[-40:]
                # Frame values stay distinct from a final testcase verdict.
                for m in METRIC.finditer(line):
                    value=m[2].lower()
                    metrics[m[1].lower()] = value if 'inf' in value else float(value)
                # Successful tests print a named performance block before validation.
                if '--- Performance Log:' in line:
                    active['performance_block']=True
                if active.get('performance_block'):
                    baseline='baseline:' in line
                    stage='stages:' in line
                    for m in re.finditer(r'\b(e2e|avg_denoise|median_denoise|\w+Stage)=([0-9.]+)ms',line):
                        name={'e2e':'E2E Latency','avg_denoise':'Average denoise step','median_denoise':'Median denoise step'}.get(m[1],m[1])
                        target=next((x for x in performance if x['metric']==name and x.get('source')=='performance_log'),None)
                        if target is None:
                            target={'metric':name,'source':'performance_log','line':lineno}; performance.append(target)
                        target['baseline_seconds' if baseline else 'actual_seconds']=float(m[2])/1000
                        if target.get('baseline_seconds'):
                            target['relative_regression_pct']=round((target['actual_seconds']/target['baseline_seconds']-1)*100,3)
                    if '--- End Performance Log:' in line:active['performance_block']=False
                if '[Consistency Check]' in line or 'Consistency check failed for' in line:
                    checks.append({'line':lineno,'text':line})
                if re.search(r'\b(?:CANN|torch[_-]npu|triton[_-]ascend|Cache-DiT|attention.backend|gt_sha|seed|sp_degree|ulysses_degree)\b',line,re.I):
                    active.setdefault('configuration_evidence',[])
                    if len(active['configuration_evidence'])<24:
                        active['configuration_evidence'].append({'line':lineno,'text':line})
    finish()
    return observations, {'observation_overflow':overflow,'unowned_exceptions':unowned,
                          'configuration_evidence':configuration,
                          'complete':overflow==0}
