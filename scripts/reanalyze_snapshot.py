#!/usr/bin/env python3
"""Reanalyse saved NPU Diffusion snapshot with GLM; NO GitHub reads or Issues writes."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from datetime import datetime,timezone
try:
    from scripts.npu_diffusion_ci_report import clusters_from_snapshot, glm_analysis, render_report
    from scripts.log_context import repair_consistency_job_from_saved_evidence
except ModuleNotFoundError:
    from npu_diffusion_ci_report import clusters_from_snapshot, glm_analysis, render_report
    from log_context import repair_consistency_job_from_saved_evidence


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--mode',choices=('auto','triage','investigate'),default='investigate')
    parser.add_argument('--output',type=Path,default=None)
    args=parser.parse_args(argv)
    # An explicit manual investigation may use deeper reasoning without
    # changing the daily systemd service's economical triage profile.
    if args.mode == 'investigate':
        overrides = {
            'GLM_REASONING_EFFORT': ('GLM_MANUAL_REASONING_EFFORT', 'high'),
            'GLM_MAX_INVESTIGATIONS': ('GLM_MANUAL_MAX_INVESTIGATIONS', '1'),
            'GLM_MAX_OUTPUT_TOKENS': ('GLM_MANUAL_MAX_OUTPUT_TOKENS', '16000'),
            'GLM_CLUSTER_MAX_TOKENS': ('GLM_MANUAL_CLUSTER_MAX_TOKENS', '12000'),
            'GLM_SYNTHESIS_MAX_TOKENS': ('GLM_MANUAL_SYNTHESIS_MAX_TOKENS', '12000'),
        }
        for key, (manual, default) in overrides.items():
            os.environ[key] = os.getenv(manual, default)
    data=json.loads(args.snapshot.read_text(encoding='utf-8'))
    if not isinstance(data.get('jobs'),list) or not data.get('generated_utc'):
        parser.error('Snapshot missing jobs/generated_utc')
    repaired=sum(1 for job in data['jobs'] if repair_consistency_job_from_saved_evidence(job))
    if repaired:
        print('[REANALYZE] Reconstructed aggregate/per-frame consistency metrics for %d jobs from saved redacted evidence' % repaired, flush=True)
    clusters=clusters_from_snapshot(data)
    for c in clusters:
        c['stable_id']='PREVIEW-'+c['fingerprint'][:6]
    print('[REANALYZE] Jobs=%d, clusters=%d, failed testcase observations=%d, mode=%s; no GitHub calls or Issues writes' %
          (len(data['jobs']),len(clusters),sum(c['count'] for c in clusters),args.mode),flush=True)
    content,status=glm_analysis(data,clusters,mode=args.mode,gh=None)
    if os.getenv('GLM_BUDGET_ENABLED','1') == '1':
        try:
            try:
                from scripts.glm_budget import summary as glm_budget_summary
            except ModuleNotFoundError:
                from glm_budget import summary as glm_budget_summary
            usage = glm_budget_summary()
            print('[REANALYZE] Local GLM budget: requests=%d, reported_estimate_CNY=%s, '
                  'unknown_reserved_CNY=%s, remaining_CNY=%s' % (
                    usage['requests'], usage['reported_cny_estimate'],
                    usage['uncertain_reserved_cny'],usage['remaining_local_estimate_cny']), flush=True)
        except Exception as exc:
            print('[REANALYZE] WARNING: Budget summary unavailable: %s' % type(exc).__name__, flush=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=args.output or Path('reports/reanalysis')/('glm-investigation-'+stamp+'.md')
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    text=render_report(data,clusters,content,status,history=None)
    output.write_text(text,encoding='utf-8')
    try:
        from scripts.complete_report import write_complete_companion
    except ImportError:
        from complete_report import write_complete_companion
    write_complete_companion(output,data,clusters,content,status)

    output.chmod(0o600)
    print('[REANALYZE] GLM=%s; file=%s'%(status,output),flush=True)
    return 0 if status.startswith('completed') else 1

if __name__=='__main__':
    raise SystemExit(main())
