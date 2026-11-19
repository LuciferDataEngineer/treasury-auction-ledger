"""Bounded, credential-free acquisition of Treasury auction windows.

Every acquisition fetches every page twice. Identical normalized row content is
required before either pass is accepted; this is not an upstream transaction.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, urlopen
import uuid

SOURCE_URL = ('https://api.fiscaldata.treasury.gov/services/api/fiscal_service/'
              'v1/accounting/od/treasury_securities_auctions_v2')
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_DAYS = 93
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
REQUEST_TIMEOUT = 15
ACQUISITION_SECONDS = 90


class SourceError(ValueError):
    """Source completeness, integrity or bounds could not be established."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('utf-8')


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise SourceError('Dates must use YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SourceError('Invalid calendar date') from error


def _window(start, end):
    first, last = _iso_date(start), _iso_date(end)
    if not 1 <= (last - first).days + 1 <= MAX_DAYS:
        raise SourceError(f'Window must contain between 1 and {MAX_DAYS} calendar days')
    return start, end


def _url(start, end, page):
    params = {
        'filter': f'comp_auction_close_date:gte:{start},comp_auction_close_date:lte:{end}',
        'sort': 'comp_auction_close_date,cusip',
        'page[size]': str(PAGE_SIZE), 'page[number]': str(page),
    }
    # No fields parameter: projection can trigger implicit upstream aggregation.
    return SOURCE_URL + '?' + urlencode(params)


def _same_url(actual, expected):
    a, e = urlsplit(actual), urlsplit(expected)
    return (a.scheme == 'https' and a.netloc == e.netloc and a.path == e.path
            and not a.fragment and parse_qs(a.query) == parse_qs(e.query))


def _get(url, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SourceError('Acquisition deadline exceeded')
    request = Request(url, headers={'User-Agent': 'treasury-auction-ledger/1.0 (public-data ETL)'})
    try:
        with urlopen(request, timeout=min(REQUEST_TIMEOUT, remaining)) as response:
            if response.status != 200:
                raise SourceError('Source did not return HTTP 200')
            if not _same_url(response.url, url):
                raise SourceError('Unexpected source redirect')
            length = response.headers.get('Content-Length')
            if length is not None and int(length) > MAX_RESPONSE_BYTES:
                raise SourceError('Response exceeds byte budget')
            chunks, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise SourceError('Acquisition deadline exceeded')
                chunk = response.read(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise SourceError('Response exceeds byte budget')
                chunks.append(chunk)
            return b''.join(chunks)
    except SourceError:
        raise
    except Exception as error:
        raise SourceError('Public Treasury request failed; retry the complete window') from error


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceError('Duplicate JSON object key')
        result[key] = value
    return result


def _page(payload):
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_RESPONSE_BYTES:
        raise SourceError('Response bytes are empty or exceed budget')
    try:
        doc = json.loads(payload, object_pairs_hook=_unique_pairs)
        rows, meta, links = doc['data'], doc['meta'], doc['links']
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise SourceError('Data must be a list of records')
        count, total, pages = meta['count'], meta['total-count'], meta['total-pages']
        if any(type(value) is not int for value in (count, total, pages)):
            raise SourceError('Pagination counts must be integers')
        if count != len(rows) or not 0 <= count <= PAGE_SIZE:
            raise SourceError('Page row count mismatch')
        if not 0 <= total <= PAGE_SIZE * MAX_PAGES:
            raise SourceError('Window record count exceeds budget')
        expected_pages = math.ceil(total / PAGE_SIZE)
        if (total and pages != expected_pages) or (not total and pages not in (0, 1)):
            raise SourceError('Pagination total-pages mismatch')
        if 'next' not in links:
            raise SourceError('Pagination next link is missing')
        canonical(rows)  # Reject non-finite numeric JSON extensions.
        return rows, total, max(1, pages), links['next']
    except SourceError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise SourceError('Unexpected Treasury response schema') from error


def _validate_pass(payloads, start, end):
    if not isinstance(payloads, list) or not 1 <= len(payloads) <= MAX_PAGES:
        raise SourceError('Invalid page count')
    combined, total_seen, expected_pages = [], None, None
    for page_number, payload in enumerate(payloads, 1):
        rows, total, pages, next_link = _page(payload)
        if total_seen is None:
            total_seen, expected_pages = total, pages
        if (total, pages) != (total_seen, expected_pages):
            raise SourceError('Pagination metadata changed within a pass')
        expected_count = min(PAGE_SIZE, max(0, total - (page_number - 1) * PAGE_SIZE))
        if len(rows) != expected_count:
            raise SourceError('Incomplete or overfull page')
        if (page_number < pages) != (next_link is not None):
            raise SourceError('Pagination next link contradicts completeness')
        combined.extend(rows)
    if len(payloads) != expected_pages or len(combined) != total_seen:
        raise SourceError('Window is incomplete')
    previous = None
    for row in combined:
        try:
            day, cusip = row['comp_auction_close_date'], row['cusip']
            _iso_date(day)
            if not isinstance(cusip, str) or not re.fullmatch(r'[A-Z0-9]{9}', cusip):
                raise SourceError('Invalid CUSIP key')
            if not start <= day <= end:
                raise SourceError('Auction date is outside requested window')
            key = (day, cusip)
            if previous is not None and key <= previous:
                raise SourceError('Rows are duplicated or not globally sorted by auction key')
            previous = key
        except (KeyError, TypeError) as error:
            raise SourceError('Auction key is missing or invalid') from error
    return combined


def _collect(start, end, deadline):
    first = _get(_url(start, end, 1), deadline)
    _, _, pages, _ = _page(first)
    payloads = [first]
    for page in range(2, pages + 1):
        payloads.append(_get(_url(start, end, page), deadline))
    return payloads, _validate_pass(payloads, start, end)


def _raw_identity(passes):
    digest, total = hashlib.sha256(), 0
    for payloads in passes:
        digest.update(len(payloads).to_bytes(4, 'big'))
        for payload in payloads:
            digest.update(len(payload).to_bytes(8, 'big'))
            digest.update(payload)
            total += len(payload)
    return digest.hexdigest(), total


def _save_new(path, payload, allow_existing=False):
    """Atomic create; never replace a corrupt content-addressed file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not allow_existing or path.stat().st_size != len(payload) or path.read_bytes() != payload:
                raise SourceError('Existing acquisition file differs; preserve and investigate')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _capture_identity(manifest):
    return sha(canonical({key: value for key, value in manifest.items() if key != 'capture_id'}))


def fetch_window(data_dir, start, end):
    """Fetch a bounded window twice, persist verified raw pages and a sidecar."""
    _window(start, end)
    captured_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    deadline = time.monotonic() + ACQUISITION_SECONDS
    first, first_rows = _collect(start, end, deadline)
    second, second_rows = _collect(start, end, deadline)
    if canonical(first_rows) != canonical(second_rows):
        raise SourceError('Window changed between passes; retry the complete window')
    passes = [first, second]
    raw_sha256, raw_bytes = _raw_identity(passes)
    manifest = {
        'schema_version': 1, 'source_url': SOURCE_URL, 'captured_at': captured_at,
        'window_start': start, 'window_end': end, 'page_size': PAGE_SIZE,
        'nonce': uuid.uuid4().hex, 'raw_sha256': raw_sha256, 'raw_bytes': raw_bytes,
        'passes': [[{'page': index, 'url': _url(start, end, index),
                     'sha256': sha(payload), 'bytes': len(payload), 'path': f'raw/{sha(payload)}.json'}
                    for index, payload in enumerate(pages, 1)] for pages in passes],
    }
    manifest['capture_id'] = _capture_identity(manifest)
    data_dir = Path(data_dir).resolve()
    for pages in passes:
        for payload in pages:
            _save_new(data_dir / 'raw' / (sha(payload) + '.json'), payload, allow_existing=True)
    manifest_path = data_dir / 'manifests' / (manifest['capture_id'] + '.json')
    _save_new(manifest_path, json.dumps(manifest, indent=2).encode() + b'\n')
    return {**manifest, 'manifest_path': str(manifest_path), 'rows': first_rows}


def read_capture(manifest_path):
    """Offline replay; verify both raw passes, all metadata and their equality."""
    path = Path(manifest_path).resolve()
    try:
        if not 0 < path.stat().st_size <= MAX_MANIFEST_BYTES:
            raise SourceError('Acquisition sidecar exceeds byte budget')
        manifest = json.loads(path.read_bytes(), object_pairs_hook=_unique_pairs)
        start, end = _window(manifest['window_start'], manifest['window_end'])
        if manifest['schema_version'] != 1 or manifest['source_url'] != SOURCE_URL or manifest['page_size'] != PAGE_SIZE:
            raise SourceError('Unsupported acquisition contract')
        if manifest['capture_id'] != _capture_identity(manifest):
            raise SourceError('Acquisition sidecar identity mismatch')
        if not re.fullmatch(r'[0-9a-f]{32}', manifest['nonce']):
            raise SourceError('Invalid acquisition nonce')
        stamp = manifest['captured_at']
        if not isinstance(stamp, str) or not stamp.endswith('Z'):
            raise SourceError('Acquisition timestamp must be UTC')
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise SourceError('Acquisition timestamp must be timezone aware')
        refs = manifest['passes']
        if not isinstance(refs, list) or len(refs) != 2:
            raise SourceError('Exactly two complete passes are required')
        root = path.parent.parent
        passes = []
        for refs_in_pass in refs:
            if not isinstance(refs_in_pass, list) or not 1 <= len(refs_in_pass) <= MAX_PAGES:
                raise SourceError('Invalid saved page count')
            payloads = []
            for index, ref in enumerate(refs_in_pass, 1):
                digest, size = ref['sha256'], ref['bytes']
                if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                    raise SourceError('Invalid saved page digest')
                if type(size) is not int or not 0 < size <= MAX_RESPONSE_BYTES:
                    raise SourceError('Invalid saved page length')
                expected_path = f'raw/{digest}.json'
                if ref['page'] != index or ref['url'] != _url(start, end, index) or ref['path'] != expected_path:
                    raise SourceError('Saved page request or path mismatch')
                raw_path = (root / expected_path).resolve()
                if not raw_path.is_relative_to(root.resolve()) or raw_path.parent != root / 'raw':
                    raise SourceError('Saved raw page escaped data directory')
                if raw_path.stat().st_size != size:
                    raise SourceError('Saved raw page length mismatch')
                payload = raw_path.read_bytes()
                if sha(payload) != digest:
                    raise SourceError('Saved raw page checksum mismatch')
                payloads.append(payload)
            passes.append(payloads)
        first, second = (_validate_pass(payloads, start, end) for payloads in passes)
        if canonical(first) != canonical(second):
            raise SourceError('Saved passes disagree')
        if _raw_identity(passes) != (manifest['raw_sha256'], manifest['raw_bytes']):
            raise SourceError('Combined raw provenance mismatch')
        return {**manifest, 'manifest_path': str(path), 'rows': first}
    except SourceError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, OSError) as error:
        raise SourceError('Cannot verify acquisition sidecar') from error
