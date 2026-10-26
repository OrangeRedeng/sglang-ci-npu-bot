#!/usr/bin/env python3
"""Manually reduce or expand an already-downloaded NPU CI log, without network/GLM."""
import argparse
import json
from pathlib import Path
try:
    from scripts.log_context import analyze_file, expand_file
except ModuleNotFoundError:
    from log_context import analyze_file, expand_file


def main():
    p = argparse.ArgumentParser(description='Streaming, bounded NPU Diffusion CI log analysis')
    p.add_argument('log', type=Path)
    p.add_argument('--expand', metavar='SIGNATURE_ID', help='second-stage targeted expansion')
    p.add_argument('-C', '--context', type=int, default=20)
    p.add_argument('--json', action='store_true', help='structured JSON instead of focused text')
    opts = p.parse_args()
    if not opts.log.is_file():
        p.error('log file missing')
    if opts.expand:
        packet = expand_file(opts.log, opts.expand, context=opts.context)
        print(json.dumps(packet, indent=2, ensure_ascii=False) if opts.json else packet['text'])
    else:
        packet = analyze_file(opts.log)
        print(json.dumps(packet, indent=2, ensure_ascii=False) if opts.json else packet['focused_text'])

if __name__ == '__main__':
    main()
