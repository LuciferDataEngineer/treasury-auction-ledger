"""Fictional correction, rollback, absence and reappearance drill; no network."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import warehouse
from reports import analyze,export_report

FIXTURE=Path(__file__).parent/'fixtures/synthetic-windows.json'


def require(condition,message):
    if not condition:raise ValueError(message)


def envelope(batch,root):
    raw=json.dumps(batch['rows'],sort_keys=True,separators=(',',':')).encode()
    raw_path=root/(batch['name']+'.json')
    raw_path.write_bytes(raw)
    capture={k:batch[k] for k in ('window_start','window_end','captured_at','rows')}
    capture.update(source_url='https://example.invalid/synthetic-treasury-auctions',
                   raw_sha256=hashlib.sha256(raw).hexdigest(),raw_bytes=len(raw),
                   manifest_path=str(raw_path.resolve()))
    capture['capture_id']=warehouse.digest({k:v for k,v in capture.items() if k!='manifest_path'})
    return capture


def fingerprint(db):
    return warehouse.digest([list(row) for row in db.execute('SELECT * FROM auction_versions ORDER BY auction_date,cusip,version')])


def run_demo(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    fixture=json.loads(FIXTURE.read_text())
    captures=[envelope(batch,root) for batch in fixture['captures']]
    loads=[]
    with closing(warehouse.connect(root/'demo.sqlite')) as db:
        loads.append(warehouse.load_capture(db,captures[0]))
        before=fingerprint(db)
        checkpoint=tuple(db.execute('SELECT * FROM checkpoint').fetchone())
        db.execute("CREATE TRIGGER injected_fault BEFORE INSERT ON auction_versions WHEN NEW.version=2 BEGIN SELECT RAISE(ABORT,'injected interruption'); END")
        try:warehouse.load_capture(db,captures[1])
        except sqlite3.IntegrityError:pass
        else:raise ValueError('Injected interruption failed to stop load')
        require(fingerprint(db)==before,'Failed load changed history')
        require(tuple(db.execute('SELECT * FROM checkpoint').fetchone())==checkpoint,'Failed load advanced checkpoint')
        require(db.execute('SELECT COUNT(*) FROM captures').fetchone()[0]==1,'Failed load left a capture')
        db.execute('DROP TRIGGER injected_fault')
        loads.append(warehouse.load_capture(db,captures[1]))
        require(db.execute("SELECT COUNT(*) FROM auction_versions WHERE observed_to IS NULL AND state='absent_from_observation'").fetchone()[0]==1,'Missing source row not recorded')
        loads.append(warehouse.load_capture(db,captures[2]))
        final=fingerprint(db)
    with closing(warehouse.connect(root/'demo.sqlite')) as db:
        retry=warehouse.load_capture(db,captures[1])
        require(retry['idempotent'] and fingerprint(db)==final,'Retry reverted newer state')
        stale={**captures[1],'capture_id':'f'*64}
        try:warehouse.load_capture(db,stale)
        except ValueError as error:
            require('stale' in str(error),'Unexpected stale rejection')
        else:raise ValueError('Stale new capture was accepted')
        require(fingerprint(db)==final,'Rejected stale capture changed history')
        require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','Integrity check failed')
        require(not db.execute('PRAGMA foreign_key_check').fetchall(),'Foreign-key violations')
        counts={'current_auctions':db.execute('SELECT COUNT(*) FROM current_auctions').fetchone()[0],
                'history_versions':db.execute('SELECT COUNT(*) FROM auction_versions').fetchone()[0],
                'captures':db.execute('SELECT COUNT(*) FROM captures').fetchone()[0]}
        require(counts=={'current_auctions':4,'history_versions':8,'captures':3},'Unexpected final drill counts')
        export_report(analyze(db),root/'report')
    fields=('inserted','revised','removed','unchanged','raw_rows')
    evidence={'coverage':fixture['coverage'],'loads':[{k:r[k] for k in fields} for r in loads],
              'final':counts,'rollback_preserved_history_and_checkpoint':True,
              'retry_after_reopen_preserved_newer_state':True,'stale_new_capture_rejected':True,
              'sqlite_integrity':'ok','foreign_key_violations':0,'history_sha256':final}
    (root/'validation.json').write_text(json.dumps(evidence,indent=2)+'\n')
    return evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--work-dir',type=Path,default=Path('data/demo'))
    print(json.dumps(run_demo(parser.parse_args().work_dir),indent=2))
