"""Bind saved synthesis to its evidence; legacy recovery is explicit."""
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from datetime import datetime


def evidence_digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,
        separators=(',',':')).encode('utf-8')).hexdigest()


def save_analysis(path, data, analysis, status):
    path=Path(path)
    path.write_text(analysis,encoding='utf-8');path.chmod(0o600)
    metadata={'snapshot_sha256':evidence_digest(data),
              'analysis_sha256':hashlib.sha256(analysis.encode('utf-8')).hexdigest(),
              'status':status,'generated_utc':data['generated_utc']}
    sidecar=path.with_name(path.name+'.meta.json')
    sidecar.write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8');sidecar.chmod(0o600)


def load_analysis(path, data, allow_legacy=False):
    path=Path(path)
    date=datetime.fromisoformat(data['generated_utc'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
    if path.name!=f'npu-diffusion-ci-{date}-full.md':
        raise ValueError('Saved synthesis filename must match the snapshot date')
    if path.stat().st_size>150_000:raise ValueError('Saved synthesis exceeds size limit')
    analysis=path.read_text(encoding='utf-8')
    if len(analysis.strip())<100 or re.search(r'[\u0400-\u04ff]',analysis):
        raise ValueError('Saved synthesis is empty, incomplete, or not English')
    if analysis.lstrip().startswith(('GLM synthesis failed','GLM investigation failed','All GLM cluster investigations failed','Not run')):
        raise ValueError('Saved analysis is not a completed synthesis')
    sidecar=path.with_name(path.name+'.meta.json')
    if sidecar.exists():
        meta=json.loads(sidecar.read_text(encoding='utf-8'))
        if (meta.get('snapshot_sha256')!=evidence_digest(data) or
                meta.get('analysis_sha256')!=hashlib.sha256(analysis.encode('utf-8')).hexdigest() or
                not str(meta.get('status','')).startswith('completed')):
            raise ValueError('Saved synthesis does not match this snapshot or is incomplete')
        return analysis,'completed: recovered saved synthesis; no new model calls'
    if not allow_legacy:raise ValueError('Legacy synthesis has no binding metadata; --allow-legacy-analysis is required')
    if data.get('glm_synthesis_status')!='completed':
        raise ValueError('Snapshot does not prove that synthesis completed')
    return analysis,('completed: recovered legacy dated synthesis; exact file/snapshot binding unavailable; no new model calls')
