"""Local BigModel request budget and usage ledger (Python 3.9+, stdlib only).

Prices are USER-CONFIGURED estimates, not an authoritative BigModel bill.
A request with unknown outcome keeps the entire estimate reserved until manually
reconciled. Atomic SQLite reservations prevent concurrent jobs overspending the
same configured allowance, but API metering can differ from estimates.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_UP
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo


class GLMBudgetExceeded(RuntimeError):
    """Request blocked before dispatch by configured local quota."""


def _decimal_env(name, default):
    try:
        d = Decimal(os.environ.get(name, str(default)).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(name + ' must be a non-negative decimal') from exc
    if not d.is_finite() or d < 0:
        raise ValueError(name + ' must be a finite non-negative decimal')
    return d


def enabled():
    value = os.getenv('GLM_BUDGET_ENABLED', '1').strip()
    if value not in ('0', '1'):
        raise ValueError('GLM_BUDGET_ENABLED must be 0 or 1')
    return value == '1'


def ledger_path():
    return Path(os.getenv('GLM_BUDGET_LEDGER', 'reports/glm-budget.sqlite3'))


def current_day():
    return datetime.now(timezone.utc).astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()


@contextmanager
def _connection():
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The parent folder should not be world-readable, even if pre-existing.
    path.parent.chmod(0o700)
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    if path.exists():
        path.chmod(0o600)
    con.execute('PRAGMA busy_timeout=30000')
    con.execute('''CREATE TABLE IF NOT EXISTS calls (
       id TEXT PRIMARY KEY, day TEXT NOT NULL, utc TEXT NOT NULL,
       model TEXT NOT NULL, state TEXT NOT NULL,
       reserved_cny TEXT NOT NULL, actual_cny TEXT,
       prompt_tokens INTEGER, completion_tokens INTEGER, reasoning_tokens INTEGER,
       result TEXT NOT NULL)''')
    con.execute('CREATE INDEX IF NOT EXISTS calls_day ON calls(day)')
    try:
        yield con
    finally:
        con.close()


def rates():
    return (_decimal_env('GLM_INPUT_CNY_PER_M', '0.80'),
            _decimal_env('GLM_OUTPUT_CNY_PER_M', '2.80'))


def estimate_reservation(messages, max_output_tokens):
    # Conservative envelope: one input token per UTF-8 byte, 1.5x price
    # reserve multiplier. Provider tokenization may still differ materially.
    raw = json.dumps(messages, ensure_ascii=False).encode('utf-8')
    est_input = max(1024, len(raw))
    in_rate, out_rate = rates()
    return ((Decimal(est_input) * in_rate +
             Decimal(max_output_tokens) * out_rate) / Decimal(1000000) * Decimal('1.5'))


def _fmt(d):
    return str(d.quantize(Decimal('0.000001'), rounding=ROUND_UP))


def reserve(model, messages, max_output_tokens):
    if not enabled():
        return None
    daily = _decimal_env('GLM_DAILY_BUDGET_CNY', '1.00')
    per_call = _decimal_env('GLM_PER_REQUEST_BUDGET_CNY', '0.50')
    count_cap = os.getenv('GLM_MAX_DAILY_API_REQUESTS', '4').strip()
    try:
        count_cap = int(count_cap)
    except ValueError as exc:
        raise ValueError('GLM_MAX_DAILY_API_REQUESTS must be integer') from exc
    if not 0 <= count_cap <= 100:
        raise ValueError('GLM_MAX_DAILY_API_REQUESTS must be within 0..100')
    requested = Decimal(_fmt(estimate_reservation(messages, max_output_tokens)))
    if requested > per_call:
        raise GLMBudgetExceeded('per-request estimated reservation CNY %s exceeds configured cap CNY %s (prompt_bytes=%d, output_token_cap=%d); request NOT sent' % (str(requested),str(per_call),len(json.dumps(messages,ensure_ascii=False).encode('utf-8')),max_output_tokens))
    day = current_day()
    with _connection() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT state,reserved_cny,actual_cny FROM calls WHERE day=?',(day,)).fetchall()
        counted = sum((Decimal(actual) if actual is not None else Decimal(res))
                      for _,res,actual in rows)
        if len(rows) >= count_cap or counted + requested > daily:
            db.execute('ROLLBACK')
            if len(rows) >= count_cap:
                raise GLMBudgetExceeded('daily API request cap reached: %d/%d requests on %s (MSK); request NOT sent' % (len(rows),count_cap,day))
            raise GLMBudgetExceeded('daily estimated budget exceeded: committed CNY %s + requested reserve CNY %s > daily cap CNY %s on %s (MSK); request NOT sent' % (str(counted),str(requested),str(daily),day))
        ident = uuid4().hex
        db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (ident, day, datetime.now(timezone.utc).isoformat(), model,
                    'reserved', _fmt(requested), None, None, None, None, 'pending'))
        db.execute('COMMIT')
    return ident


def finish(ident, usage=None, result='response'):
    if ident is None:
        return
    usage = usage or {}
    pt = usage.get('prompt_tokens')
    ct = usage.get('completion_tokens')
    rt = usage.get('reasoning_tokens')
    complete = type(pt) is int and pt >= 0 and type(ct) is int and ct >= 0
    in_price, out_price = rates()
    charge = _fmt((Decimal(pt)*in_price+Decimal(ct)*out_price)/Decimal(1000000)) if complete else None
    state = 'reported' if complete else 'unknown'
    with _connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE calls SET state=?,actual_cny=?,prompt_tokens=?,completion_tokens=?,reasoning_tokens=?,result=? WHERE id=? AND state=?',
                   (state,charge,pt if complete else None,ct if complete else None,
                    rt if type(rt) is int and rt>=0 else None,result[:30],ident,'reserved'))
        db.execute('COMMIT')


def unknown(ident, result='timeout_or_transport_error'):
    if ident is None:
        return
    with _connection() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE calls SET state='unknown', result=? WHERE id=? AND state='reserved'",(result[:30],ident))
        db.execute('COMMIT')


def summary(day=None):
    day = day or current_day()
    daily = _decimal_env('GLM_DAILY_BUDGET_CNY', '1.00')
    with _connection() as db:
        rows=db.execute('SELECT state,reserved_cny,actual_cny,prompt_tokens,completion_tokens,reasoning_tokens FROM calls WHERE day=?',(day,)).fetchall()
    reported=sum((Decimal(x[2]) for x in rows if x[2] is not None), Decimal('0'))
    uncertain=sum((Decimal(x[1]) for x in rows if x[2] is None), Decimal('0'))
    return {'day_msk':day,'requests':len(rows), 'reported_requests':sum(x[2] is not None for x in rows),
            'unknown_or_inflight_requests':sum(x[2] is None for x in rows),
            'reported_cny_estimate':str(reported), 'uncertain_reserved_cny':str(uncertain),
            'committed_cny_estimate':str(reported+uncertain),'daily_limit_cny':str(daily),
            'remaining_local_estimate_cny':str(max(Decimal(0),daily-reported-uncertain)),
            'prompt_tokens_reported':sum(x[3] or 0 for x in rows),
            'completion_tokens_reported':sum(x[4] or 0 for x in rows),
            'reasoning_tokens_reported':sum(x[5] or 0 for x in rows)}


def main():
    parser=argparse.ArgumentParser(description='Local GLM usage and budget status; no API/network')
    parser.add_argument('--day',default=None, help='YYYY-MM-DD, Moscow date; defaults to today')
    args=parser.parse_args()
    if args.day and (len(args.day)!=10 or not all(x in '0123456789-' for x in args.day)):
        parser.error('expected YYYY-MM-DD')
    print(json.dumps(summary(args.day),indent=2,ensure_ascii=False))


if __name__=='__main__':
    main()
