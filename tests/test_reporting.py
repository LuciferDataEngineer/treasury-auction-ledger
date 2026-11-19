from contextlib import closing
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import demo
import reports
import warehouse
from validate_live import expected_aggregates

ROOT=Path(__file__).resolve().parents[1]


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=warehouse.connect(self.root/'test.sqlite')
        self.fixture=json.loads(demo.FIXTURE.read_text())
        self.captures=[demo.envelope(b,self.root) for b in self.fixture['captures']]

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def test_decimal_formatting_never_rounds_through_float(self):
        self.assertEqual(reports.money(9007199254740993),'90071992547409.93')
        self.assertEqual(reports.money(-101),'-1.01')
        self.assertIsNone(reports.money(None))
        with self.assertRaises(ValueError):reports.money(0.1)

    def test_sql_matches_independent_decimal_oracle_with_pending_results(self):
        warehouse.load_capture(self.db,self.captures[0]);actual=reports.analyze(self.db)
        expected=expected_aggregates(self.captures[0]['rows'])
        for table,rows in expected.items():
            ordered=lambda x:sorted(json.dumps(r,sort_keys=True) for r in x)
            self.assertEqual(ordered(actual['tables'][table]),ordered(rows))
        self.assertEqual(sum(Decimal(r['offering_usd']) for r in actual['tables']['instrument_summary']),Decimal('600000000.01'))
        quality={r['metric']:r['value'] for r in actual['tables']['quality']}
        self.assertEqual(quality['missing_awarded_amount'],1)
        self.assertEqual(quality['missing_reported_bid_to_cover'],1)
        self.assertEqual(actual['provenance'],'synthetic')

    def test_empty_history_csv_retains_header_and_report_identity(self):
        warehouse.load_capture(self.db,self.captures[0]);actual=reports.analyze(self.db)
        target=self.root/'report';reports.export_report(actual,target)
        self.assertEqual(json.loads((target/'summary.json').read_text()),actual)
        self.assertEqual((target/'revision_history.csv').read_text().splitlines(),[','.join(actual['columns']['revision_history'])])
        self.assertIn('SYNTHETIC',(target/'report.md').read_text())
        self.assertNotIn(str(self.root),(target/'summary.json').read_text())

    def test_failed_or_repeated_export_preserves_existing_results(self):
        warehouse.load_capture(self.db,self.captures[0]);actual=reports.analyze(self.db)
        target=self.root/'report'
        with patch('reports.csv.DictWriter.writerows',side_effect=OSError('disk fault')):
            with self.assertRaises(OSError):reports.export_report(actual,target)
        self.assertFalse(target.exists());self.assertFalse(list(self.root.glob('.report-*')))
        reports.export_report(actual,target)
        before=(target/'summary.json').read_bytes()
        with self.assertRaises(ValueError):reports.export_report(actual,target)
        self.assertEqual((target/'summary.json').read_bytes(),before)

    def test_reader_transaction_survives_concurrent_refresh(self):
        warehouse.load_capture(self.db,self.captures[0])
        invoked=[]
        with closing(warehouse.connect(self.root/'test.sqlite')) as other:
            def callback(sql):
                if 'SELECT security_type' in sql and not invoked:
                    invoked.append(True);warehouse.load_capture(other,self.captures[1])
            self.db.set_trace_callback(callback)
            try:actual=reports.analyze(self.db)
            finally:self.db.set_trace_callback(None)
        self.assertTrue(invoked)
        self.assertEqual(actual['covered_end'],'2026-11-10')
        self.assertEqual(len(actual['tables']['capture_audit']),1)
        self.assertEqual(sum(r['auction_count'] for r in actual['tables']['instrument_summary']),3)
        self.assertEqual(reports.analyze(self.db)['covered_end'],'2026-11-15')

    def test_empty_complete_window_is_reportable(self):
        capture=deepcopy(self.captures[0]);capture['rows']=[];capture['capture_id']='a'*64
        warehouse.load_capture(self.db,capture)
        actual=reports.analyze(self.db)
        self.assertEqual(actual['tables']['instrument_summary'],[])
        self.assertEqual(actual['covered_end'],'2026-11-10')
        reports.export_report(actual,self.root/'empty')
        self.assertEqual(len((self.root/'empty/instrument_summary.csv').read_text().splitlines()),1)

    def test_demo_cli_recovery_and_reanalysis_from_another_directory(self):
        target=self.root/'demo'
        run=subprocess.run([sys.executable,str(ROOT/'demo.py'),'--work-dir',str(target)],cwd=self.root,capture_output=True,text=True,check=True)
        result=json.loads(run.stdout)
        self.assertEqual(result['final'],{'current_auctions':4,'history_versions':8,'captures':3})
        self.assertTrue(result['rollback_preserved_history_and_checkpoint'])
        self.assertTrue(result['retry_after_reopen_preserved_newer_state'])
        plan=subprocess.run([sys.executable,str(ROOT/'pipeline.py'),'plan','--db',str(target/'demo.sqlite'),'--as-of','2026-11-20'],cwd=self.root,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(plan.stdout),{'window_start':'2026-11-02','window_end':'2026-11-20'})
        report=subprocess.run([sys.executable,str(ROOT/'pipeline.py'),'report','--db',str(target/'demo.sqlite'),'--output',str(target/'cli-report')],cwd=self.root,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(report.stdout)['provenance'],'synthetic')
        failed=subprocess.run([sys.executable,str(ROOT/'demo.py'),'--work-dir',str(target)],cwd=self.root,capture_output=True,text=True)
        self.assertNotEqual(failed.returncode,0)

    def test_no_capture_no_report(self):
        with self.assertRaises(ValueError):reports.analyze(self.db)
