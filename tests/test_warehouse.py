"""Deterministic transactional and temporal correctness checks with fictional auctions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import warehouse


def auction(**changes):
    row = {
        'record_date': '2026-11-23', 'comp_auction_close_date': '2026-11-19',
        'cusip': 'TEST00001', 'issue_date': '2026-11-23', 'maturity_date': '2027-02-22',
        'security_type': 'Bill', 'security_term': '13-Week', 'offering_amt': '100.01',
        'grand_total_par_awarded': '100.01', 'grand_total_par_recog': '225.50',
        'high_rate': '3.770000', 'high_yield': 'null', 'high_discnt_margin': None,
        'bid_to_cover_ratio': '2.255000', 'reopening': 'No', 'inflation_protected': 'No',
        'floating_rate': 'No', 'comp_bid_expression': 'DISCOUNT RATE',
        'pdf_filenm_comp_results': 'SYNTHETIC.pdf',
    }
    row.update(changes)
    return row


def capture(rows, start='2026-11-16', end='2026-11-20', at='2026-11-20T00:00:00Z'):
    raw = json.dumps(rows, sort_keys=True).encode()
    envelope = {'source_url': 'https://example.invalid/synthetic-auctions', 'captured_at': at,
                'window_start': start, 'window_end': end, 'rows': rows,
                'raw_sha256': warehouse.digest(rows), 'raw_bytes': max(1, len(raw)),
                'manifest_path': 'data/manifests/synthetic.json'}
    envelope['capture_id'] = warehouse.digest(envelope)
    return envelope


def image(db):
    tables = ('captures', 'capture_rows', 'auction_versions', 'auction_observations', 'checkpoint')
    return {table: [tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]
            for table in tables}


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'warehouse.sqlite'
        self.db = warehouse.connect(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def load(self, rows, **kwargs):
        return warehouse.load_capture(self.db, capture(rows, **kwargs))

    def test_exact_decimal_nulls_future_issue_and_record_date_diagnostic(self):
        self.load([auction(record_date='2026-11-24', offering_amt='10000000000000.00',
                           grand_total_par_awarded='0.01', high_yield='-0.010000',
                           high_discnt_margin='-0.000000', floating_rate='null')])
        row = self.db.execute('SELECT * FROM current_auctions').fetchone()
        self.assertEqual((row['offering_cents'], row['awarded_cents'], row['recognized_cents']),
                         (1000000000000000, 1, 22550))
        self.assertEqual((row['high_rate'],row['high_yield'],row['high_discnt_margin'],row['bid_to_cover_ratio']),
                         ('3.77','-0.01','0','2.255'))
        self.assertIsNone(row['floating_rate'])
        self.assertEqual((row['source_record_date'],row['issue_date']),('2026-11-24','2026-11-23'))
        self.assertGreater(row['issue_date'], row['auction_date'])

    def test_identical_replay_after_reopen_has_no_effect(self):
        envelope = capture([auction()])
        first = warehouse.load_capture(self.db, envelope)
        before = image(self.db)
        self.db.close()
        self.db = warehouse.connect(self.path)
        replay = warehouse.load_capture(self.db, envelope)
        self.assertFalse(first['idempotent'])
        self.assertTrue(replay['idempotent'])
        self.assertEqual(before, image(self.db))
        # Moving a retained sidecar is not a changed acquisition.
        relocated = deepcopy(envelope)
        relocated['manifest_path'] = 'data/moved/same-acquisition.json'
        self.assertTrue(warehouse.load_capture(self.db, relocated)['idempotent'])
        self.assertEqual(before, image(self.db))

    def test_capture_id_reuse_with_changed_rows_window_or_evidence_is_rejected(self):
        envelope = capture([auction()])
        warehouse.load_capture(self.db, envelope)
        before = image(self.db)
        for field, value in [('rows', [auction(offering_amt='150.00')]),
                             ('window_start','2026-11-15'), ('raw_bytes',100),
                             ('captured_at','2026-11-20T01:00:00Z')]:
            with self.subTest(field=field):
                changed = deepcopy(envelope)
                changed[field] = value
                with self.assertRaisesRegex(ValueError,'immutable evidence'):
                    warehouse.load_capture(self.db, changed)
                self.assertEqual(before,image(self.db))

    def test_unchanged_projection_advances_provenance_without_new_version(self):
        self.load([auction()])
        first = self.db.execute('SELECT * FROM current_auctions').fetchone()
        second = self.load([auction(unused_source_field='raw metadata changed')],at='2026-11-20T01:00:00Z')
        current = self.db.execute('SELECT * FROM current_auctions').fetchone()
        self.assertEqual((second['inserted'],second['revised'],second['removed'],second['unchanged']),(0,0,0,1))
        self.assertEqual(current['version'],1)
        self.assertEqual(current['observed_from'],first['observed_from'])
        self.assertEqual(current['last_observed_at'],'2026-11-20T01:00:00.000000Z')
        self.assertEqual(current['last_capture_id'],second['capture_id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM capture_rows').fetchone()[0],2)
        hashes = list(self.db.execute('SELECT raw_row_sha256 FROM capture_rows'))
        self.assertNotEqual(hashes[0][0],hashes[1][0])

    def test_aba_revisions_retain_each_observed_version(self):
        self.load([auction(offering_amt='100.00')])
        self.load([auction(offering_amt='125.00')],at='2026-11-20T01:00:00Z')
        last = self.load([auction(offering_amt='100.00')],at='2026-11-20T02:00:00Z')
        versions = list(self.db.execute('SELECT * FROM auction_versions ORDER BY version'))
        self.assertEqual([r['offering_cents'] for r in versions],[10000,12500,10000])
        self.assertEqual([r['version'] for r in versions],[1,2,3])
        self.assertEqual(versions[0]['observed_to'],versions[1]['observed_from'])
        self.assertEqual(versions[1]['observed_to'],versions[2]['observed_from'])
        self.assertIsNone(versions[2]['observed_to'])
        self.assertEqual(last['revised'],1)

    def test_observed_absence_and_reappearance_are_not_cancellation(self):
        self.load([auction()])
        absent = self.load([],at='2026-11-20T01:00:00Z')
        self.assertEqual(absent['removed'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM current_auctions').fetchone()[0],0)
        tombstone = self.db.execute('SELECT * FROM auction_versions WHERE observed_to IS NULL').fetchone()
        self.assertEqual(tombstone['state'],'absent_from_observation')
        self.assertIsNone(tombstone['offering_cents'])
        self.assertIsNone(tombstone['raw_row_sha256'])
        again = self.load([],at='2026-11-20T02:00:00Z')
        self.assertEqual(again['removed'],0)
        self.assertEqual(self.db.execute('SELECT last_observed_at FROM auction_observations').fetchone()[0], '2026-11-20T02:00:00.000000Z')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM auction_versions').fetchone()[0],2)
        restored = self.load([auction()],at='2026-11-20T03:00:00Z')
        self.assertEqual(restored['revised'],1)
        current = self.db.execute('SELECT * FROM current_auctions').fetchone()
        self.assertEqual((current['version'],current['offering_cents']),(3,10001))

    def test_tombstones_are_limited_to_the_reconciled_window(self):
        self.load([auction(),auction(cusip='TEST00002',comp_auction_close_date='2026-11-18')])
        self.load([],start='2026-11-19',end='2026-11-19',at='2026-11-20T01:00:00Z')
        survivors = list(self.db.execute('SELECT cusip FROM current_auctions'))
        self.assertEqual([r['cusip'] for r in survivors],['TEST00002'])

    def test_stale_or_same_time_intersecting_window_cannot_resurrect(self):
        self.load([auction()],at='2026-11-20T01:00:00Z')
        self.load([],start='2026-11-19',end='2026-11-19',at='2026-11-20T02:00:00Z')
        before = image(self.db)
        for at in ['2026-11-20T00:00:00Z','2026-11-20T02:00:00Z','2026-11-20T01:59:59Z']:
            with self.subTest(at=at), self.assertRaisesRegex(ValueError,'stale or same-time'):
                self.load([auction()],start='2026-11-18',end='2026-11-21',at=at)
            self.assertEqual(before,image(self.db))
        # Different timezone spelling still denotes the same instant and is stale.
        with self.assertRaisesRegex(ValueError,'stale or same-time'):
            self.load([auction()],at='2026-11-20T13:00:00+11:00')

    def test_disjoint_future_or_past_gaps_rejected_adjacent_windows_extend(self):
        self.load([])
        before = image(self.db)
        for start,end in [('2026-11-22','2026-11-23'),('2026-11-12','2026-11-14')]:
            with self.subTest(start=start), self.assertRaisesRegex(ValueError,'coverage gap'):
                self.load([],start=start,end=end,at='2026-11-20T03:00:00Z')
            self.assertEqual(before,image(self.db))
        # Adjacent windows do not overlap; their acquisition clocks can differ.
        self.load([],start='2026-11-21',end='2026-11-22',at='2026-11-19T00:00:00Z')
        self.load([],start='2026-11-14',end='2026-11-15',at='2026-11-19T01:00:00Z')
        checkpoint = self.db.execute('SELECT * FROM checkpoint').fetchone()
        self.assertEqual((checkpoint['covered_start'],checkpoint['covered_end']),('2026-11-14','2026-11-22'))

    def test_next_window_uses_coverage_through_empty_weekend_not_max_row_date(self):
        with self.assertRaisesRegex(ValueError,'start is required'):
            warehouse.next_window(self.db,'2026-11-22')
        self.assertEqual(warehouse.next_window(self.db,'2026-11-22',start='2026-11-01'),
                         {'window_start':'2026-11-01','window_end':'2026-11-22'})
        self.load([auction()],start='2026-11-01',end='2026-11-22')
        self.assertEqual(warehouse.next_window(self.db,'2026-11-23'),
                         {'window_start':'2026-11-09','window_end':'2026-11-23'})
        self.assertEqual(warehouse.next_window(self.db,'2026-11-23',lookback_days=93)['window_start'],'2026-11-01')
        for kwargs in [{'as_of':'2026-11-21'}, {'as_of':'2027-04-01'},
                       {'as_of':'2026-11-23','lookback_days':0},
                       {'as_of':'2026-11-23','lookback_days':True},
                       {'as_of':'2026-11-23','start':'2026-11-01'}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                warehouse.next_window(self.db,**kwargs)

    def test_entire_bad_batch_rejected_before_any_false_absence(self):
        self.load([auction()])
        before = image(self.db)
        invalid = [
            auction(cusip='short'), auction(cusip='test00001'),
            auction(comp_auction_close_date='2026-11-21'),
            auction(issue_date='2026-11-18'), auction(maturity_date='2026-11-22'),
            auction(record_date='2026-02-30'), auction(security_type='Unknown'),
            auction(security_term=''), auction(offering_amt='null'),
            auction(offering_amt='NaN'), auction(offering_amt='1e3'),
            auction(offering_amt='1.001'), auction(offering_amt='-0.01'),
            auction(offering_amt='10000000000000.01'), auction(high_rate='Infinity'),
            auction(high_rate='0.1234567'), auction(high_rate='1000.000001'),
            auction(high_rate='9'*41), auction(bid_to_cover_ratio='-0.1'),
            auction(reopening='true'), auction(inflation_protected=True),
            auction(grand_total_par_awarded=100.00), 'not an object',
        ]
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.load([auction(cusip='TEST00002'),bad],at='2026-11-20T01:00:00Z')
            self.assertEqual(before,image(self.db))
        with self.assertRaisesRegex(ValueError,'duplicate'):
            self.load([auction(),auction()],at='2026-11-20T01:00:00Z')
        self.assertEqual(before,image(self.db))

    def test_envelope_validation_preserves_entire_state(self):
        before = image(self.db)
        for field,value in [('capture_id','not-a-hash'),('raw_sha256','f'*63),('raw_bytes',True),
                            ('captured_at','2026-11-20T00:00:00'),('window_start','2026-11-21'),
                            ('window_end','2027-05-01'),('rows',None),('source_url','')]:
            envelope = capture([auction()]);envelope[field]=value
            with self.subTest(field=field), self.assertRaises(ValueError):
                warehouse.load_capture(self.db,envelope)
            self.assertEqual(before,image(self.db))

    def test_mid_transaction_failure_rolls_back_versions_captures_and_checkpoint(self):
        self.load([auction()])
        self.db.execute("CREATE TRIGGER fail_checkpoint BEFORE UPDATE ON checkpoint BEGIN SELECT RAISE(ABORT,'injected checkpoint failure'); END")
        before = image(self.db)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'injected checkpoint failure'):
            self.load([auction(offering_amt='200.00'),auction(cusip='TEST00002')],end='2026-11-21',at='2026-11-20T01:00:00Z')
        self.assertEqual(before,image(self.db))
        self.db.execute('DROP TRIGGER fail_checkpoint')
        result = self.load([auction(offering_amt='200.00'),auction(cusip='TEST00002')],end='2026-11-21',at='2026-11-20T01:00:00Z')
        self.assertEqual((result['inserted'],result['revised']),(1,1))

    def test_load_sequence_survives_replay_and_rolled_back_load(self):
        first_capture = capture([auction()])
        first = warehouse.load_capture(self.db, first_capture)
        self.assertEqual(first['load_sequence'], 1)
        self.assertEqual(warehouse.load_capture(self.db, first_capture)['load_sequence'], 1)
        self.db.execute("CREATE TRIGGER fail_sequence BEFORE UPDATE ON checkpoint BEGIN SELECT RAISE(ABORT,'injected sequence rollback'); END")
        second_capture = capture([auction(offering_amt='250.00')], at='2026-11-20T01:00:00Z')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected sequence rollback'):
            warehouse.load_capture(self.db, second_capture)
        self.assertEqual([r[0] for r in self.db.execute('SELECT load_sequence FROM captures')], [1])
        self.assertEqual(self.db.execute("SELECT seq FROM sqlite_sequence WHERE name='captures'").fetchone()[0], 1)
        self.db.execute('DROP TRIGGER fail_sequence')
        second = warehouse.load_capture(self.db, second_capture)
        self.assertEqual(second['load_sequence'], 2)
        third = self.load([], at='2026-11-20T02:00:00Z')
        self.assertEqual(third['load_sequence'], 3)
        self.assertEqual([r[0] for r in self.db.execute('SELECT load_sequence FROM captures ORDER BY load_sequence')], [1, 2, 3])

    def test_recovery_in_recorded_ingestion_order_reconstructs_all_state(self):
        # Adjacent captures may legitimately have older observation clocks.
        # Sorting these sidecars by captured_at would introduce a coverage gap.
        envelopes = [
            capture([auction()], at='2026-11-20T12:00:00Z'),
            capture([], start='2026-11-21', end='2026-11-22', at='2026-11-20T09:00:00Z'),
            capture([], start='2026-11-14', end='2026-11-15', at='2026-11-20T08:00:00Z'),
            capture([auction(offering_amt='125.00')], start='2026-11-19', end='2026-11-19', at='2026-11-20T13:00:00Z'),
            capture([], start='2026-11-19', end='2026-11-19', at='2026-11-20T14:00:00Z'),
        ]
        for envelope in envelopes:
            warehouse.load_capture(self.db, envelope)
        expected = image(self.db)
        records = list(self.db.execute('SELECT load_sequence,capture_id FROM captures ORDER BY load_sequence'))
        self.assertEqual([r['load_sequence'] for r in records], list(range(1, 6)))
        by_id = {envelope['capture_id']: envelope for envelope in envelopes}
        with closing(warehouse.connect(Path(self.temp.name) / 'restored.sqlite')) as restored:
            for record in records:
                result = warehouse.load_capture(restored, by_id[record['capture_id']])
                self.assertEqual(result['load_sequence'], record['load_sequence'])
            self.assertEqual(image(restored), expected)
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(restored.execute('PRAGMA foreign_key_check').fetchall(), [])
        with closing(warehouse.connect(Path(self.temp.name) / 'wrong-order.sqlite')) as wrong:
            chronological = sorted(envelopes, key=lambda item: item['captured_at'])
            warehouse.load_capture(wrong, chronological[0])
            with self.assertRaisesRegex(ValueError, 'coverage gap'):
                warehouse.load_capture(wrong, chronological[1])

    def test_capture_row_limit_rejects_before_changes(self):
        self.load([auction()])
        before = image(self.db)
        with self.assertRaisesRegex(ValueError,'at most 1000'):
            self.load([auction(cusip=f'TEST{i:05d}') for i in range(1001)],at='2026-11-20T01:00:00Z')
        self.assertEqual(before,image(self.db))

    def test_concurrent_same_capture_has_single_effect(self):
        envelope=capture([auction()])
        def load():
            with closing(warehouse.connect(self.path)) as db:
                return warehouse.load_capture(db,envelope)['idempotent']
        with ThreadPoolExecutor(max_workers=2) as pool:
            effects=list(pool.map(lambda _:load(),range(2)))
        self.assertEqual(sorted(effects),[False,True])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM captures').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM auction_versions').fetchone()[0],1)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(),[])


if __name__ == '__main__':
    unittest.main()
