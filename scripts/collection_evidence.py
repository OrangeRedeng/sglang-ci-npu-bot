"""Bounded read-only source evidence, independent of model availability."""
from __future__ import annotations
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

try:
    from scripts.ci_timestamps import timestamp_key
except ImportError:
    from ci_timestamps import timestamp_key


@contextmanager
def observed_log(path, generated_utc):
    """Keep original line numbers while excluding post-snapshot log content."""
    if not generated_utc:
        yield Path(path),{'snapshot_cutoff':'unknown'}
        return
    cutoff=timestamp_key(generated_utc)
    with tempfile.TemporaryDirectory(prefix='npu-ci-observed-') as directory:
        output=Path(directory)/'observed.log';dropped=0
        with Path(path).open(encoding='utf-8-sig',errors='replace') as src,output.open('w',encoding='utf-8') as dst:
            for line in src:
                stamp=re.match(r'^(\d{4}-\d{2}-\d{2}T[0-9:.]+Z)',line)
                if stamp and timestamp_key(stamp[1])>cutoff:
                    dropped+=1;dst.write('\n')
                else:dst.write(line)
        yield output,{'snapshot_cutoff':generated_utc,'post_snapshot_lines_excluded':dropped}


def main_runs(gh, repo, now):
    path=f'/repos/{repo}/actions/workflows/pr-test-npu.yml/runs'
    obj=gh.get(path, {'branch':'main','created':
        f'{(now-timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")}..{now.strftime("%Y-%m-%dT%H:%M:%SZ")}',
        'per_page':33,'page':1})
    if not isinstance(obj,dict) or not isinstance(obj.get('workflow_runs'),list):
        raise ValueError('Malformed main run enumeration')
    candidates=obj['workflow_runs']
    runs=[r for r in candidates[:32] if r.get('head_branch')=='main'
          and r.get('event') in ('schedule','push','workflow_dispatch')
          and (r.get('head_repository') or {}).get('full_name')==repo
          and r.get('created_at','')<=now.strftime('%Y-%m-%dT%H:%M:%SZ')]
    return runs,len(candidates)<=32


def source_evidence(gh, repo, jobs, max_runs=4):
    result={'runs':{},'sources':{},'limitations':[]}
    selected=[]; seen=set()
    for j in jobs:
        key=(j.get('run_id'),j.get('head_sha'))
        if key[0] and key not in seen: selected.append(j); seen.add(key)
    if len(selected)>max_runs: result['limitations'].append(f'Artifacts/source evidence capped at {max_runs} runs')
    for j in selected[:max_runs]:
        rid=j['run_id']; sha=j.get('head_sha')
        try:
            obj=gh.get(f'/repos/{repo}/actions/runs/{rid}/artifacts',{'per_page':100,'page':1})
            if not isinstance(obj,dict) or not isinstance(obj.get('artifacts'),list):raise ValueError('Malformed artifacts')
            result['runs'][str(rid)]={'artifacts':[{
                k:a.get(k) for k in ('id','name','expired','size_in_bytes','archive_download_url')}
                for a in obj['artifacts'] if any(s in a.get('name','') for s in ('diffusion','failure','coverage'))],
                'listing_complete':obj.get('total_count',0)<=100,
                'contents_verified':False}
        except (RuntimeError,ValueError,TypeError,KeyError,OSError) as e:
            result['limitations'].append(f'Run {rid}: artifacts unavailable ({type(e).__name__})')
        if not sha or sha in result['sources']:continue
        sources={}
        for path in ('.github/workflows/pr-test-npu.yml',
                     'python/sglang/multimodal_gen/test/server/ascend/testcase_configs_npu.py',
                     'python/sglang/multimodal_gen/test/run_suite.py'):
            try:
                obj=gh.get(f'/repos/{repo}/contents/{quote(path,safe="/")}',{'ref':sha})
                if not isinstance(obj,dict) or obj.get('encoding')!='base64':raise ValueError('Unavailable source')
                text=base64.b64decode(obj['content']).decode('utf-8')
                # Preserve context without importing historical upstream Python.
                lines=text.splitlines()
                import re
                wanted=set()
                for i,line in enumerate(lines):
                    if re.search(r'--suite|coverage_mode|schedule:|cron:|part:|npu|register|TEST_CASE|testcase',line,re.I):
                        wanted.update(range(max(0,i-2),min(len(lines),i+3)))
                sources[path]={'url':f'https://github.com/{repo}/blob/{sha}/{path}',
                    'sha':sha,'excerpt':[{'line':i+1,'text':lines[i]} for i in sorted(wanted)[:240]],
                    'excerpt_capped':len(wanted)>240}
            except (RuntimeError,ValueError,TypeError,KeyError,OSError) as e:
                result['limitations'].append(f'{sha[:12]} {path}: unavailable ({type(e).__name__})')
        result['sources'][sha]=sources
    return result


def attach_observations(rec, path):
    try:
        from scripts.testcase_observations import parse_observations
    except ImportError:
        from testcase_observations import parse_observations
    observations,accounting=parse_observations(path)
    rec['testcase_observations']=observations
    rec['observation_accounting']=accounting
    for case in rec.get('failure_cases',[]):
        executions=[o for o in observations if o['testcase']==case.get('testcase')]
        case['execution_observations']=executions
        causes={o['causal_signature'] for o in executions if o.get('causal_signature')}
        if len(causes)==1:
            case['causal_signature']=next(iter(causes))
        # Traceback assertions can repeat a live measurement; keep both provenance types.
        if not case.get('performance'):
            case['performance']=[m for o in executions for m in o.get('performance',[])]
