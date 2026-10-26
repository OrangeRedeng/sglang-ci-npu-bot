"""Parse GitHub timestamps on Python 3.9 without losing cutoff precision."""
from datetime import datetime
import re

FRACTION=re.compile(r'(T\d{2}:\d{2}:\d{2})[.,](\d+)')


def timestamp_key(value):
    match=FRACTION.search(value)
    remainder=''
    if match:
        digits=match[2]
        # Python 3.9 accepts only three or six fractional digits.
        value=value[:match.start()]+match[1]+'.'+(digits+'000000')[:6]+value[match.end():]
        remainder=digits[6:].rstrip('0')
    return datetime.fromisoformat(value.replace('Z','+00:00')),remainder


def parse_timestamp(value):
    return timestamp_key(value)[0]
