"""Deterministic fake HTTP pages; no public API calls in tests."""
from contextlib import contextmanager
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import source

START, END = '2026-10-01', '2026-11-19'


def row(number=1, **changes):
    value = {'cusip': f'SYN{number:06d}', 'comp_auction_close_date': START,
             'offering_amt': '100', 'high_yield': '3.125', 'security_type': 'Note'}
    value.update(changes)
    return value


def page(rows, total=None, pages=None, next_link=None):
    total = len(rows) if total is None else total
    pages = max(1, (total + source.PAGE_SIZE - 1) // source.PAGE_SIZE) if pages is None else pages
    return json.dumps({'data': rows, 'meta': {'count': len(rows), 'total-count': total, 'total-pages': pages},
                       'links': {'next': next_link}}, sort_keys=True).encode()


class Response(io.BytesIO):
    def __init__(self, payload, url, length=None, status=200):
        super().__init__(payload)
        self.url, self.status = url, status
        self.headers = {} if length is None else {'Content-Length': str(length)}


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'data'

    def tearDown(self):
        self.temp.cleanup()

    def fetch(self, payloads, start=START, end=END):
        with patch.object(source, '_get', side_effect=payloads):
            return source.fetch_window(self.root, start, end)

    def test_single_page_roundtrip_preserves_original_rows_and_both_raw_passes(self):
        payload = page([row(), row(2, extra_source_field='preserve me')])
        result = self.fetch([payload, payload])
        replay = source.read_capture(result['manifest_path'])
        self.assertEqual(result, replay)
        self.assertEqual(result['raw_bytes'], len(payload) * 2)
        self.assertEqual(len(list((self.root/'raw').glob('*.json'))), 1)
        self.assertEqual(len(result['passes']), 2)
        self.assertEqual(result['rows'][1]['extra_source_field'], 'preserve me')
        self.assertNotIn('fields=', result['passes'][0][0]['url'])

    def test_pagination_assembles_complete_globally_sorted_window_twice(self):
        first = page([row(i) for i in range(1, 101)], total=101, next_link='page2')
        last = page([row(101)], total=101)
        with patch.object(source, '_get', side_effect=[first, last, first, last]) as get:
            result = source.fetch_window(self.root, START, END)
        self.assertEqual(len(result['rows']), 101)
        self.assertEqual(get.call_count, 4)
        self.assertEqual(len(source.read_capture(result['manifest_path'])['rows']), 101)

    def test_empty_windows_accept_zero_or_one_page_count(self):
        result = self.fetch([page([], pages=0), page([], pages=1)])
        self.assertEqual(result['rows'], [])
        self.assertEqual(source.read_capture(result['manifest_path'])['rows'], [])

    def test_new_acquisitions_keep_distinct_identity_even_for_same_content(self):
        payload = page([row()])
        first, second = self.fetch([payload, payload]), self.fetch([payload, payload])
        self.assertNotEqual(first['capture_id'], second['capture_id'])
        self.assertEqual(first['raw_sha256'], second['raw_sha256'])
        self.assertEqual(len(list((self.root/'manifests').glob('*.json'))), 2)

    def test_acquisition_clock_is_recorded_before_network(self):
        events = []
        class Clock:
            @classmethod
            def now(cls, zone):
                events.append('start')
                from datetime import datetime
                return datetime(2026, 11, 19, 22, 1, tzinfo=zone)
        def get(url, deadline):
            events.append('network')
            return page([row()])
        with patch.object(source, 'datetime', Clock), patch.object(source, '_get', side_effect=get):
            result = source.fetch_window(self.root, START, END)
        self.assertEqual(events, ['start', 'network', 'network'])
        self.assertEqual(result['captured_at'], '2026-11-19T22:01:00Z')

    def test_content_drift_is_rejected_before_persistence(self):
        with self.assertRaisesRegex(source.SourceError, 'changed between passes'):
            self.fetch([page([row()]), page([row(high_yield='3.126')])])
        self.assertFalse(self.root.exists())

    def test_count_drift_between_pages_is_rejected(self):
        first = page([row(i) for i in range(1, 101)], total=101, next_link='page2')
        last = page([row(101)], total=102)
        with self.assertRaisesRegex(source.SourceError, 'metadata changed'):
            self.fetch([first, last])
        self.assertFalse(self.root.exists())

    def test_declared_growth_past_bounds_rejected_before_more_requests(self):
        payload = page([row()], total=1001, pages=11, next_link='page2')
        with self.assertRaisesRegex(source.SourceError, 'exceeds budget'):
            self.fetch([payload])
        self.assertFalse(self.root.exists())

    def test_missing_count_or_incomplete_last_page_rejected(self):
        bad = json.loads(page([row()]));bad['meta']['count'] = 2
        with self.assertRaisesRegex(source.SourceError, 'row count mismatch'):
            self.fetch([json.dumps(bad).encode()])
        with self.assertRaisesRegex(source.SourceError, 'Incomplete'):
            self.fetch([page([row()], total=2)])

    def test_duplicate_unsorted_and_out_of_range_keys_rejected(self):
        cases = [[row(), row()], [row(2), row(1)], [row(comp_auction_close_date='2026-09-30')],
                 [row(comp_auction_close_date='2026-02-30')], [row(cusip='bad')]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(source.SourceError):
                self.fetch([page(rows)])
        self.assertFalse(self.root.exists())

    def test_duplicate_keys_across_page_boundaries_rejected(self):
        first = page([row(i) for i in range(1, 101)], total=101, next_link='page2')
        with self.assertRaisesRegex(source.SourceError, 'duplicated'):
            self.fetch([first, page([row(100)], total=101)])

    def test_date_window_bounds_checked_before_http(self):
        for start, end in [('2026-11-20','2026-11-19'), ('2026-01-01','2026-11-19'), ('2026-1-1',END)]:
            with self.subTest(start=start), patch.object(source, '_get') as get:
                with self.assertRaises(source.SourceError):
                    source.fetch_window(self.root, start, end)
                get.assert_not_called()

    def test_raw_tampering_is_detected_without_replacing_corruption(self):
        payload = page([row()]); result = self.fetch([payload, payload])
        raw = self.root / result['passes'][0][0]['path']
        changed = payload.replace(b'3.125', b'3.126');raw.write_bytes(changed)
        with self.assertRaisesRegex(source.SourceError, 'checksum mismatch'):
            source.read_capture(result['manifest_path'])
        with self.assertRaisesRegex(source.SourceError, 'differs'):
            self.fetch([payload, payload])
        self.assertEqual(raw.read_bytes(), changed)

    def test_manifest_tampering_and_untrusted_raw_paths_rejected(self):
        payload = page([row()]); result = self.fetch([payload, payload])
        path = Path(result['manifest_path']); manifest = json.loads(path.read_text())
        manifest['raw_bytes'] += 1;path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(source.SourceError, 'identity mismatch'):
            source.read_capture(path)
        manifest['passes'][0][0]['path'] = '../outside.json'
        manifest['capture_id'] = source._capture_identity(manifest);path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(source.SourceError, 'path mismatch'):
            source.read_capture(path)

    def test_both_saved_passes_are_revalidated(self):
        payload = page([row()]); result = self.fetch([payload, payload])
        path = Path(result['manifest_path']); manifest = json.loads(path.read_text())
        corrected = page([row(high_yield='4.125')]); digest = source.sha(corrected)
        (self.root/'raw'/f'{digest}.json').write_bytes(corrected)
        ref=manifest['passes'][1][0]
        ref.update(sha256=digest, bytes=len(corrected), path=f'raw/{digest}.json')
        manifest['raw_sha256'],manifest['raw_bytes']=source._raw_identity([[payload],[corrected]])
        manifest['capture_id']=source._capture_identity(manifest);path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(source.SourceError, 'Saved passes disagree'):
            source.read_capture(path)

    def test_duplicate_json_keys_and_nonfinite_numbers_rejected(self):
        for payload in [b'{"data":[],"data":[]}', page([row(offering_amt=float('nan'))])]:
            with self.subTest(payload=payload), self.assertRaises(source.SourceError):
                self.fetch([payload])

    def test_download_limits_without_content_length_and_declared_oversize(self):
        url=source._url(START,END,1)
        with patch.object(source,'MAX_RESPONSE_BYTES',10):
            for payload,length in [(b'x'*11,None),(b'',11)]:
                with self.subTest(length=length), patch.object(source,'urlopen',return_value=Response(payload,url,length)):
                    with self.assertRaisesRegex(source.SourceError,'byte budget'):
                        source._get(url,time.monotonic()+10)

    def test_deadline_and_untrusted_redirect_fail_closed(self):
        url=source._url(START,END,1)
        with patch.object(source,'urlopen') as opener:
            with self.assertRaisesRegex(source.SourceError,'deadline'):
                source._get(url,time.monotonic()-1)
            opener.assert_not_called()
        with patch.object(source,'urlopen',return_value=Response(b'{}','https://example.invalid/data')):
            with self.assertRaisesRegex(source.SourceError,'redirect'):
                source._get(url,time.monotonic()+10)

    def test_http_partial_content_is_not_accepted(self):
        url=source._url(START,END,1)
        with patch.object(source,'urlopen',return_value=Response(page([row()]),url,status=206)):
            with self.assertRaisesRegex(source.SourceError,'HTTP 200'):
                source._get(url,time.monotonic()+10)
