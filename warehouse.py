"""Atomic reconciliation of complete Treasury auction date windows.

History tracks observations by this collector, not official Treasury event time.
Missing rows become observed-absence versions, never inferred cancellations.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3

LOGGER = logging.getLogger(__name__)
MAX_WINDOW_DAYS = 93
MAX_ROWS = 1000
FACT_FIELDS = (
    'source_record_date', 'issue_date', 'maturity_date', 'security_type', 'security_term',
    'offering_cents', 'awarded_cents', 'recognized_cents', 'high_rate', 'high_yield',
    'high_discnt_margin', 'bid_to_cover_ratio', 'reopening', 'comp_bid_expression',
    'inflation_protected', 'floating_rate', 'pdf_filenm_comp_results',
)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def connect(path):
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript((Path(__file__).parent / 'sql/schema.sql').read_text())
    return db


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def iso_date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'{field} must be YYYY-MM-DD')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f'{field} must be a valid calendar date') from None


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('captured_at must be a timezone-aware ISO timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('captured_at must be a timezone-aware ISO timestamp') from None
    if parsed.tzinfo is None:
        raise ValueError('captured_at must include a timezone')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def optional(value):
    if value is None or value == 'null':
        return None
    if not isinstance(value, str):
        raise ValueError('source scalar must be text or null')
    return value.strip() or None


def text_value(value, field, required=False, limit=200):
    value = optional(value)
    if value is None and required:
        raise ValueError(f'{field} is required')
    if value is not None and (len(value) > limit or any(ord(c) < 32 for c in value)):
        raise ValueError(f'{field} is oversized or contains control characters')
    return value


def decimal_value(value, field, money=False, required=False):
    value = optional(value)
    if value is None:
        if required:
            raise ValueError(f'{field} is required')
        return None
    fraction = 2 if money else 6
    if len(value) > 40 or not re.fullmatch(r'-?\d+(?:\.\d{1,' + str(fraction) + r'})?', value):
        raise ValueError(f'{field} must be bounded fixed-point decimal text')
    with localcontext() as context:
        context.prec = 50
        parsed = Decimal(value)
        maximum = Decimal('10000000000000') if money else Decimal('1000')
        if abs(parsed) > maximum or (money and parsed < 0) or (field == 'bid_to_cover_ratio' and parsed < 0):
            raise ValueError(f'{field} exceeds the supported numeric range')
        if money:
            return int(parsed * 100)
        normalized = format(parsed, 'f').rstrip('0').rstrip('.') if '.' in value else str(parsed)
        return '0' if parsed == 0 else normalized


def flag(value, field):
    value = optional(value)
    if value not in (None, 'Yes', 'No'):
        raise ValueError(f'{field} must be Yes, No, or null')
    return value


def project(row):
    if not isinstance(row, dict):
        raise ValueError('auction row must be an object')
    auction = iso_date(row.get('comp_auction_close_date'), 'comp_auction_close_date')
    cusip = text_value(row.get('cusip'), 'cusip', required=True, limit=9)
    if not re.fullmatch(r'[A-Z0-9]{9}', cusip):
        raise ValueError('cusip must contain nine uppercase alphanumeric characters')
    record = iso_date(row.get('record_date'), 'record_date')
    issue = iso_date(row.get('issue_date'), 'issue_date')
    maturity = iso_date(row.get('maturity_date'), 'maturity_date')
    if not auction <= issue <= maturity:
        raise ValueError('expected auction_date <= issue_date <= maturity_date')
    security_type = text_value(row.get('security_type'), 'security_type', required=True)
    if security_type not in {'Bill', 'Note', 'Bond'}:
        raise ValueError('unsupported security_type')
    result = {
        'auction_date': auction, 'cusip': cusip, 'source_record_date': record,
        'issue_date': issue, 'maturity_date': maturity, 'security_type': security_type,
        'security_term': text_value(row.get('security_term'), 'security_term', required=True),
        'offering_cents': decimal_value(row.get('offering_amt'), 'offering_amt', money=True, required=True),
        'awarded_cents': decimal_value(row.get('grand_total_par_awarded'), 'grand_total_par_awarded', money=True),
        'recognized_cents': decimal_value(row.get('grand_total_par_recog'), 'grand_total_par_recog', money=True),
    }
    for field in ('high_rate', 'high_yield', 'high_discnt_margin', 'bid_to_cover_ratio'):
        result[field] = decimal_value(row.get(field), field)
    for field in ('reopening', 'inflation_protected', 'floating_rate'):
        result[field] = flag(row.get(field), field)
    result['comp_bid_expression'] = text_value(row.get('comp_bid_expression'), 'comp_bid_expression')
    result['pdf_filenm_comp_results'] = text_value(row.get('pdf_filenm_comp_results'), 'pdf_filenm_comp_results')
    return result


def validate(envelope):
    start = iso_date(envelope.get('window_start'), 'window_start')
    end = iso_date(envelope.get('window_end'), 'window_end')
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if not 1 <= span <= MAX_WINDOW_DAYS:
        raise ValueError('capture window must contain 1 to 93 inclusive calendar days')
    at = timestamp(envelope.get('captured_at'))
    for field in ('capture_id', 'raw_sha256'):
        if not isinstance(envelope.get(field), str) or not re.fullmatch(r'[a-f0-9]{64}', envelope[field]):
            raise ValueError(f'{field} must be lowercase SHA-256')
    for field in ('source_url', 'manifest_path'):
        if not isinstance(envelope.get(field), str) or not envelope[field]:
            raise ValueError(f'{field} is required')
    if type(envelope.get('raw_bytes')) is not int or envelope['raw_bytes'] <= 0:
        raise ValueError('raw_bytes must be a positive integer')
    rows = envelope.get('rows')
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError('rows must be a list with at most 1000 items')
    prepared = {}
    for row in rows:
        doc = project(row)
        key = (doc['auction_date'], doc['cusip'])
        if not start <= key[0] <= end:
            raise ValueError('auction row lies outside the complete capture window')
        if key in prepared:
            raise ValueError('duplicate auction_date/cusip within complete window')
        prepared[key] = (doc, digest(row), digest(doc))
    # Preserve input ordering as part of the acquisition fingerprint. Source
    # capture_id is authoritative only together with verified immutable fields.
    identity = {
        'capture_id': envelope['capture_id'], 'source_url': envelope['source_url'],
        'captured_at': at, 'window_start': start, 'window_end': end,
        'raw_sha256': envelope['raw_sha256'], 'raw_bytes': envelope['raw_bytes'],
        'rows_sha256': digest(rows), 'raw_rows': len(rows),
    }
    return identity, prepared


def _append_version(db, key, old, state, capture, doc=None, raw_hash=None, projected_hash=None):
    if old is not None:
        db.execute('UPDATE auction_versions SET observed_to=?, closing_capture_id=? WHERE auction_date=? AND cusip=? AND version=?',
                   (capture['captured_at'], capture['capture_id'], *key, old['version']))
    values = (key[0], key[1], 1 if old is None else old['version'] + 1,
              capture['captured_at'], None, capture['capture_id'], None, state,
              raw_hash, projected_hash, *((doc or {}).get(field) for field in FACT_FIELDS))
    db.execute('INSERT INTO auction_versions VALUES (' + ','.join('?' for _ in values) + ')', values)


def load_capture(db, envelope):
    identity, prepared = validate(envelope)
    capture_id = identity['capture_id']
    with transaction(db):
        existing = db.execute('SELECT * FROM captures WHERE capture_id=?', (capture_id,)).fetchone()
        if existing is not None:
            if any(existing[key] != value for key, value in identity.items()):
                raise ValueError('capture_id reused with different immutable evidence')
            report = dict(existing)
            report['idempotent'] = True
            return report
        start, end, at = identity['window_start'], identity['window_end'], identity['captured_at']
        stale = db.execute('SELECT 1 FROM captures WHERE window_start <= ? AND window_end >= ? AND captured_at >= ? LIMIT 1',
                           (end, start, at)).fetchone()
        if stale:
            raise ValueError('stale or same-time capture overlaps a previously observed window')
        checkpoint = db.execute('SELECT * FROM checkpoint WHERE singleton=1').fetchone()
        if checkpoint:
            if ((date.fromisoformat(start) - date.fromisoformat(checkpoint['covered_end'])).days > 1
                    or (date.fromisoformat(checkpoint['covered_start']) - date.fromisoformat(end)).days > 1):
                raise ValueError('disjoint window would create an unobserved coverage gap')
        current = { (row['auction_date'], row['cusip']): row for row in db.execute(
            'SELECT * FROM auction_versions WHERE observed_to IS NULL AND auction_date BETWEEN ? AND ?', (start, end)) }
        changes = {'inserted': 0, 'revised': 0, 'removed': 0, 'unchanged': 0}
        for key, (_, _, projected_hash) in prepared.items():
            old = current.get(key)
            if old is None:
                changes['inserted'] += 1
            elif old['state'] == 'present' and old['projected_sha256'] == projected_hash:
                changes['unchanged'] += 1
            else:
                changes['revised'] += 1
        absent = {key: old for key, old in current.items() if key not in prepared and old['state'] == 'present'}
        changes['removed'] = len(absent)
        record = {**identity, 'manifest_path': envelope['manifest_path'], **changes}
        columns = list(record)
        db.execute('INSERT INTO captures (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')',
                   [record[column] for column in columns])
        for key, (doc, raw_hash, projected_hash) in prepared.items():
            old = current.get(key)
            if old is None or old['state'] != 'present' or old['projected_sha256'] != projected_hash:
                _append_version(db, key, old, 'present', identity, doc, raw_hash, projected_hash)
            db.execute('INSERT INTO capture_rows VALUES (?,?,?,?,?)', (capture_id, *key, raw_hash, projected_hash))
            db.execute('INSERT INTO auction_observations VALUES (?,?,?,?) ON CONFLICT(auction_date,cusip) DO UPDATE SET last_observed_at=excluded.last_observed_at,last_capture_id=excluded.last_capture_id',
                       (*key, at, capture_id))
        for key, old in absent.items():
            _append_version(db, key, old, 'absent_from_observation', identity)
            db.execute('UPDATE auction_observations SET last_observed_at=?,last_capture_id=? WHERE auction_date=? AND cusip=?', (at, capture_id, *key))
        # Repeated absence is another observation of the same open tombstone;
        # advance provenance without manufacturing an additional version.
        for key, old in current.items():
            if key not in prepared and old['state'] == 'absent_from_observation':
                db.execute('UPDATE auction_observations SET last_observed_at=?,last_capture_id=? WHERE auction_date=? AND cusip=?', (at, capture_id, *key))
        covered_start = min(start, checkpoint['covered_start']) if checkpoint else start
        covered_end = max(end, checkpoint['covered_end']) if checkpoint else end
        db.execute('INSERT INTO checkpoint VALUES (1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET covered_start=excluded.covered_start,covered_end=excluded.covered_end,last_capture_id=excluded.last_capture_id',
                   (covered_start, covered_end, capture_id))
        stored_rows = db.execute('SELECT COUNT(*) FROM capture_rows WHERE capture_id=?', (capture_id,)).fetchone()[0]
        if stored_rows != identity['raw_rows']:
            raise ValueError('capture row conservation failed')
        report = dict(db.execute('SELECT * FROM captures WHERE capture_id=?', (capture_id,)).fetchone())
        report['idempotent'] = False
    LOGGER.info('capture %s rows=%s inserted=%s revised=%s absent=%s unchanged=%s',
                capture_id, identity['raw_rows'], changes['inserted'], changes['revised'], changes['removed'], changes['unchanged'])
    return report


def next_window(db, as_of, lookback_days=14, start=None):
    end = iso_date(as_of, 'as_of')
    if type(lookback_days) is not int or not 1 <= lookback_days <= MAX_WINDOW_DAYS:
        raise ValueError('lookback_days must be an integer from 1 to 93')
    checkpoint = db.execute('SELECT * FROM checkpoint WHERE singleton=1').fetchone()
    if checkpoint is None:
        if start is None:
            raise ValueError('start is required for a new warehouse')
        beginning = iso_date(start, 'start')
    else:
        if start is not None:
            raise ValueError('start is only allowed for a new warehouse; load explicit backfill windows separately')
        if end < checkpoint['covered_end']:
            raise ValueError('as_of cannot precede the current coverage checkpoint')
        beginning = max(checkpoint['covered_start'],
                        (date.fromisoformat(checkpoint['covered_end']) - timedelta(days=lookback_days - 1)).isoformat())
    span = (date.fromisoformat(end) - date.fromisoformat(beginning)).days + 1
    if not 1 <= span <= MAX_WINDOW_DAYS:
        raise ValueError('next window exceeds 93 days or has reversed dates; catch up in bounded intervals')
    return {'window_start': beginning, 'window_end': end}
