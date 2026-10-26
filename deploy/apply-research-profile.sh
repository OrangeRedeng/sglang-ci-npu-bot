#!/usr/bin/env bash
# Opt-in switch to the larger GLM reasoning budget. Never changes monetary caps.
set -Eeuo pipefail
MODE="${1:---preview}"
ENVFILE="${2:-/etc/sglang-npu-bot/bot.env}"
if [[ "$MODE" != --preview && "$MODE" != --apply ]]; then
  echo "Usage: $0 [--preview|--apply] [/etc/sglang-npu-bot/bot.env]" >&2
  exit 2
fi
[[ -f "$ENVFILE" ]] || { echo "Missing bot.env: $ENVFILE" >&2; exit 2; }
if [[ "$MODE" == --apply && $(id -u) != 0 ]]; then
  echo 'Use sudo for --apply.' >&2; exit 2
fi
/usr/bin/python3 - "$MODE" "$ENVFILE" <<'PY'
import os, pathlib, re, shutil, stat, sys, tempfile, time
mode, path = sys.argv[1:]
path=pathlib.Path(path)
if mode == '--apply' and path.is_symlink():
    raise SystemExit('Refusing symlink bot.env')
keys={
    'REPORT_FORMAT':'research',
    'REPORT_INCLUDE_DEEP_DETAILS':'1',
    'PR_SEARCH_ENABLED':'1',
    'PR_SEARCH_MAX_API_CALLS':'38',
    'PR_SEARCH_MAX_PER_CLUSTER':'3',
    'MAX_JOB_LOGS':'100',
    'CREATE_PR_ONLY_ISSUES':'0',
    'MAX_NEW_CLUSTER_ISSUES':'3',
    'MAX_PUBLISHED_MAIN_ISSUES':'3',
    'GLM_ANALYSIS_MODE':'auto',
    'GLM_REASONING_EFFORT':'high',
    'GLM_MAX_INVESTIGATIONS':'2',
    'GLM_MAX_PR_CONTEXT':'6',
    'GLM_MAX_OUTPUT_TOKENS':'24000',
    'GLM_TRIAGE_MAX_TOKENS':'10000',
    'GLM_CLUSTER_MAX_TOKENS':'16000',
    'GLM_SYNTHESIS_MAX_TOKENS':'20000',
}
lines=path.read_text(encoding='utf-8').splitlines()
for k,v in keys.items():print(k+'='+v)
print('Monetary limits, billing prices, API keys, and tokens will not be changed.')
if mode == '--preview':
    print('Preview only; no changes made.')
    sys.exit(0)
seen=set(); updated=[]
for line in lines:
    m=re.match(r'^(\s*)([A-Z][A-Z0-9_]*)=',line)
    if m and m.group(2) in keys:
        k=m.group(2)
        if k in seen:raise SystemExit('Refusing duplicate configured key '+k)
        updated.append(k+'='+keys[k]);seen.add(k)
    else:updated.append(line)
for k,v in keys.items():
    if k not in seen:updated.append(k+'='+v)
backup=path.with_name(path.name+'.before-research-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
if backup.exists():raise SystemExit('Backup already exists')
shutil.copy2(path,backup)
os.chmod(backup,0o600)
fd,tmp=tempfile.mkstemp(prefix='.bot.env.',dir=path.parent)
try:
    with os.fdopen(fd,'w',encoding='utf-8') as fp:fp.write('\n'.join(updated)+'\n')
    os.chmod(tmp,0o600)
    os.replace(tmp,path)
finally:
    if os.path.exists(tmp):os.unlink(tmp)
print('Profile applied. Backup:',backup)
print('WARNING: verify real BigModel costs and GLM_DAILY_BUDGET_CNY before paid runs.')
PY
