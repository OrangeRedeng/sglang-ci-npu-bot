"""Bounded GLM Chat Completions transport with safe diagnostics.

GLM-5.3 models spend output tokens on reasoning by default; their response can
finish with ``length`` before ``message.content`` has been emitted. We must not
mistake this for an authentication or inference failure, nor expose the private
reasoning text in logs or public Issues.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

try:
    from scripts import glm_budget
except ImportError:
    import glm_budget


class GLMEmptyResponseError(ValueError):
    """No visible final answer; includes safe response *metadata* only."""


class GLMResponseFormatError(ValueError):
    """Malformed response, without logging raw model output."""


def _env_int(name, default, low, high):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(name + ' must be an integer') from exc
    if not low <= value <= high:
        raise ValueError('%s must be between %d and %d' % (name, low, high))
    return value


def _reasoning_effort(model):
    requested = os.getenv('GLM_REASONING_EFFORT', '').strip().lower()
    if requested and requested not in ('low', 'high', 'max'):
        raise ValueError('GLM_REASONING_EFFORT must be low, high or max')
    # GLM-5.3[-Flash] is a mandatory thinking model; 'off' is not supported.
    if not requested and model.lower().startswith(('glm-5.3', 'glm-5.2')):
        requested = 'high'
    return requested


def _safe_usage(obj):
    usage = obj.get('usage') or {}
    if not isinstance(usage, dict):
        usage = {}
    def nonnegative_int(x):
        return x if type(x) is int and x >= 0 else None
    detail = usage.get('completion_tokens_details') or {}
    if not isinstance(detail, dict):
        detail = {}
    return {
        'prompt_tokens': nonnegative_int(usage.get('prompt_tokens')),
        'completion_tokens': nonnegative_int(usage.get('completion_tokens')),
        'reasoning_tokens': nonnegative_int(detail.get('reasoning_tokens')),
    }


def parse_completion(obj):
    """Return (visible answer, diagnostic). Never emit reasoning text.

    Do not fall back to reasoning_content: it is not a publishable final answer.
    """
    if not isinstance(obj, dict) or not isinstance(obj.get('choices'), list) or not obj['choices']:
        raise GLMResponseFormatError('GLM API response is missing choices')
    choice = obj['choices'][0]
    if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
        raise GLMResponseFormatError('GLM API response is missing assistant message')
    msg = choice['message']
    content = msg.get('content')
    if isinstance(content, list):
        content = '\n'.join(x['text'] for x in content if isinstance(x, dict) and
                            x.get('type') == 'text' and isinstance(x.get('text'), str))
    if content is None:
        content = ''
    if not isinstance(content, str):
        raise GLMResponseFormatError('GLM API returned unsupported content type')
    reason = msg.get('reasoning_content') or msg.get('reasoning') or ''
    detail = {
        'finish_reason': str(choice.get('finish_reason') or 'unknown')[:36],
        'visible_characters': len(content.strip()),
        'reasoning_characters': len(reason) if isinstance(reason, str) else 0,
    }
    detail.update(_safe_usage(obj))
    return content.strip(), detail


def glm_budget_usage(obj):
    return _safe_usage(obj) if isinstance(obj, dict) else {}


def _description(meta):
    return ('finish_reason=%s, content_chars=%s, reasoning_chars=%s, '
            'prompt_tokens=%s, completion_tokens=%s, reasoning_tokens=%s' % (
                meta['finish_reason'], meta['visible_characters'],
                meta['reasoning_characters'], meta['prompt_tokens'],
                meta['completion_tokens'], meta['reasoning_tokens']))


def chat_completion(base_url, key, model, messages, *, max_tokens=6000,
                    on_diagnostic=None, on_usage=None, timeout_seconds=None):
    """Return only a visible final answer; retry once for reasoning budget exhaustion.

    Retries can incur additional paid tokens. No automatic retries for 4xx,
    malformed responses, non-length refusals or CLI agents.
    """
    cap = _env_int('GLM_MAX_OUTPUT_TOKENS', 24000, 512, 32768)
    timeout = _env_int('GLM_API_TIMEOUT_SECONDS', 420, 10, 1200)
    if timeout_seconds is not None:
        if type(timeout_seconds) is not int or not 10<=timeout_seconds<=1200:
            raise ValueError('GLM timeout override must be within 10..1200 seconds')
        timeout=timeout_seconds
    retries = _env_int('GLM_EMPTY_RETRIES', 0, 0, 2)
    effort = _reasoning_effort(model)
    tokens = min(max_tokens, cap)
    if tokens < 256:
        raise ValueError('GLM max_tokens must be at least 256')
    exhausted = None
    def usage_event(**info):
        if on_usage: on_usage(info)
    for attempt in range(retries + 1):
        current_effort = 'low' if attempt > 0 and effort else effort
        body = {
            'model': model, 'messages': messages, 'temperature': 0.15,
            'max_tokens': tokens, 'stream': False,
        }
        if current_effort:
            body['reasoning_effort'] = current_effort
        if model.lower().startswith('glm-5.3'):
            body['thinking'] = {'type': 'enabled'}
        req = urllib.request.Request(
            base_url.rstrip('/') + '/chat/completions',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Authorization':'Bearer ' + key, 'Content-Type':'application/json',
                     'User-Agent':'sglang-npu-diffusion-reporter'},
            method='POST')
        # Reserve budget atomically for EACH paid attempt (including retries).
        # On network timeout the outcome is UNKNOWN, never automatically free.
        usage_event(status='reserving', effort=current_effort or 'provider-default',output_cap=tokens,attempt=attempt+1)
        try:
            budget_id = glm_budget.reserve(model, messages, tokens)
        except Exception:
            usage_event(status='budget_rejected')
            raise
        started=time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
        except Exception:
            glm_budget.unknown(budget_id)
            usage_event(status='transport_failed_usage_unknown')
            raise
        try:
            obj = json.loads(raw.decode('utf-8'))
        except (UnicodeError, json.JSONDecodeError) as exc:
            glm_budget.unknown(budget_id, 'invalid_json')
            usage_event(status='invalid_json_usage_unknown')
            raise GLMResponseFormatError('GLM API returned invalid JSON') from exc
        try:
            answer, info = parse_completion(obj)
        except Exception:
            glm_budget.finish(budget_id, glm_budget_usage(obj), 'invalid_format')
            usage_event(status='invalid_format',**glm_budget_usage(obj))
            raise
        glm_budget.finish(budget_id, info, 'response')
        usage_event(**info, status='completed' if answer else 'empty_response',elapsed_seconds=round(time.monotonic()-started,3))
        if on_diagnostic:
            on_diagnostic('GLM response %d/%d: %s; output_budget=%s; effort=%s' % (
                attempt + 1, retries + 1, _description(info), tokens,
                current_effort or 'provider-default'))
        if answer:
            return answer
        exhausted = GLMEmptyResponseError('GLM returned no visible final answer (' + _description(info) + ')')
        # Retry only if there is evidence of thought-token exhaustion.
        plausible_exhaustion = (info['finish_reason'].lower() == 'length' or
                                info['reasoning_characters'] > 0)
        if not plausible_exhaustion or attempt == retries:
            break
        if tokens >= cap and (not effort or current_effort == 'low'):
            break  # Already at maximum allowed budget and cheapest reasoning effort.
        tokens = min(cap, max(tokens + 2048, int(tokens * 1.5)))
    raise exhausted
