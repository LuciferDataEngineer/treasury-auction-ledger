"""Validate a bounded public capture against an independent exact-decimal oracle."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
import json
from pathlib import Path
import platform
import tempfile

import source
import warehouse
from reports import analyze,export_report


def require(condition,message):
    if not condition:raise ValueError(message)


def clean(value):
    return None if value in (None,'null','') else value


def expected_aggregates(rows):
    instruments={};daily={}
    for r in rows:
        key=tuple(clean(r.get(k)) for k in ('security_type','inflation_protected','floating_rate','comp_bid_expression'))
        if key not in instruments:
            instruments[key]=dict(zip(('security_type','inflation_protected','floating_rate','comp_bid_expression'),key))
            instruments[key].update(auction_count=0,offering_usd=Decimal(0),awarded_usd=None,
                                    auctions_with_awarded_amount=0,recognized_usd=None,
                                    auctions_with_recognized_amount=0,reopenings=0)
        a=instruments[key];a['auction_count']+=1;a['offering_usd']+=Decimal(r['offering_amt'])
        a['reopenings']+=r.get('reopening')=='Yes'
        for raw,col,count in [('grand_total_par_awarded','awarded_usd','auctions_with_awarded_amount'),
                              ('grand_total_par_recog','recognized_usd','auctions_with_recognized_amount')]:
            if clean(r.get(raw)) is not None:
                a[col]=(a[col] or Decimal(0))+Decimal(r[raw]);a[count]+=1
        day=r['comp_auction_close_date']
        d=daily.setdefault(day,{'auction_date':day,'auction_count':0,'offering_usd':Decimal(0),
                               'awarded_usd':None,'auctions_with_awarded_amount':0})
        d['auction_count']+=1;d['offering_usd']+=Decimal(r['offering_amt'])
        if clean(r.get('grand_total_par_awarded')) is not None:
            d['awarded_usd']=(d['awarded_usd'] or Decimal(0))+Decimal(r['grand_total_par_awarded'])
            d['auctions_with_awarded_amount']+=1
    def render(groups):
        return [{k:(format(v,'.2f') if isinstance(v,Decimal) else v) for k,v in row.items()} for row in groups]
    return {'instrument_summary':render(instruments.values()),'daily_totals':render(daily.values())}


def run(data_dir,output,start=None,end=None,manifest=None):
    data_dir=Path(data_dir);output=Path(output)
    if output.exists():raise ValueError('Choose a new validation output directory')
    acquisition=source.read_capture(manifest) if manifest else source.fetch_window(data_dir,start,end)
    acquisition=source.read_capture(acquisition['manifest_path'])
    data_dir.mkdir(parents=True,exist_ok=True)
    reference=expected_aggregates(acquisition['rows'])
    with tempfile.TemporaryDirectory(prefix='validation-',dir=data_dir) as temp:
        db_path=Path(temp)/'check.sqlite'
        with closing(warehouse.connect(db_path)) as db:
            initial=warehouse.load_capture(db,acquisition)
            result=analyze(db)
            require(initial['inserted']==len(acquisition['rows']),'Initial load did not conserve input rows')
            for name,rows in reference.items():
                normalize=lambda values:sorted(json.dumps(r,sort_keys=True) for r in values)
                require(normalize(result['tables'][name])==normalize(rows),'Independent aggregate mismatch: '+name)
            require(not result['tables']['revision_history'],'Initial capture invented source revisions')
            quality={x['metric']:x['value'] for x in result['tables']['quality']}
            expected_quality={
                'current_auctions':len(acquisition['rows']),'history_versions':len(acquisition['rows']),
                'captures':1,'current_absent_keys':0,
                'source_record_date_differs_from_issue_date':sum(r['record_date']!=r['issue_date'] for r in acquisition['rows']),
                'issues_after_covered_end':sum(r['issue_date']>acquisition['window_end'] for r in acquisition['rows']),
                'missing_awarded_amount':sum(clean(r.get('grand_total_par_awarded')) is None for r in acquisition['rows']),
                'missing_reported_bid_to_cover':sum(clean(r.get('bid_to_cover_ratio')) is None for r in acquisition['rows']),
                'missing_results_document':sum(clean(r.get('pdf_filenm_comp_results')) is None for r in acquisition['rows']),
            }
            require(quality==expected_quality,'Independent quality count mismatch')
            require(len(result['tables']['capture_audit'])==1,'Capture provenance missing')
            require(result['tables']['capture_audit'][0]['capture_id']==acquisition['capture_id'],'Wrong capture ID in report')
            dump='\n'.join(db.iterdump())
            require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','SQLite integrity failure')
            require(not db.execute('PRAGMA foreign_key_check').fetchall(),'Foreign key failure')
        with closing(warehouse.connect(db_path)) as db:
            replay=warehouse.load_capture(db,acquisition)
            require(replay['idempotent'],'Capture retry did not report idempotency')
            require('\n'.join(db.iterdump())==dump,'Capture retry changed stored state')
            require(analyze(db)==result,'Capture retry changed report')
    export_report(result,output)
    totals={}
    for source_field,label in [('offering_amt','offering_usd'),('grand_total_par_awarded','awarded_usd'),('grand_total_par_recog','recognized_usd')]:
        present=[Decimal(r[source_field]) for r in acquisition['rows'] if clean(r.get(source_field)) is not None]
        totals[label]=format(sum(present,Decimal(0)),'.2f') if present else None
    evidence={'validated_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
              'python':platform.python_version(),'mode':'verified offline replay' if manifest else 'bounded live two-pass acquisition',
              'source_url':acquisition['source_url'],'captured_at':acquisition['captured_at'],
              'window_start':acquisition['window_start'],'window_end':acquisition['window_end'],
              'capture_id':acquisition['capture_id'],'raw_sha256':acquisition['raw_sha256'],
              'raw_bytes_both_passes':acquisition['raw_bytes'],'pages_per_pass':[len(p) for p in acquisition['passes']],
              'source_rows':len(acquisition['rows']),'sql_queries_executed':5,
              'independent_monetary_group_rows_compared':sum(len(r) for r in reference.values()),
              'quality_metrics_compared':len(expected_quality),'totals':totals,
              'issues_after_covered_end':expected_quality['issues_after_covered_end'],
              'replay_after_reopen_unchanged':True,'sqlite_integrity':'ok','foreign_key_violations':0,
              'coverage':'One observed real capture window. Corrections and recovery scenarios are synthetic demonstrations.',
              'cloud_deployed':False}
    (output/'validation.json').write_text(json.dumps(evidence,indent=2)+'\n')
    return evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path('data'));parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--start');parser.add_argument('--end');parser.add_argument('--manifest',type=Path)
    args=parser.parse_args()
    if (args.manifest and (args.start or args.end)) or (not args.manifest and not(args.start and args.end)):
        parser.error('Choose --manifest OR both --start and --end')
    print(json.dumps(run(args.data_dir,args.output,args.start,args.end,args.manifest),indent=2))
