"""Capture, reconcile, plan and report Treasury auctions without credentials."""
import argparse
from contextlib import closing
import json
import logging
from pathlib import Path

import source
import warehouse
from reports import analyze,export_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest='command',required=True)
    fetch=cmds.add_parser('fetch');fetch.add_argument('--start',required=True);fetch.add_argument('--end',required=True)
    fetch.add_argument('--data-dir',type=Path,default=Path('data'))
    load=cmds.add_parser('load');load.add_argument('manifest',type=Path)
    report=cmds.add_parser('report');report.add_argument('--output',type=Path,required=True)
    for name in ('plan','sync'):
        cmd=cmds.add_parser(name);cmd.add_argument('--as-of',required=True)
        cmd.add_argument('--lookback-days',type=int,default=14);cmd.add_argument('--start')
        if name=='sync':cmd.add_argument('--data-dir',type=Path,default=Path('data'))
    for cmd in (load,report,cmds.choices['plan'],cmds.choices['sync']):
        cmd.add_argument('--db',type=Path,default=Path('data/auctions.sqlite'))
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    if args.command=='fetch':
        capture=source.fetch_window(args.data_dir,args.start,args.end)
        print(capture['manifest_path']);return
    args.db.parent.mkdir(parents=True,exist_ok=True)
    with closing(warehouse.connect(args.db)) as db:
        if args.command=='load':result=warehouse.load_capture(db,source.read_capture(args.manifest))
        elif args.command=='report':
            result=analyze(db);export_report(result,args.output)
        else:
            result=warehouse.next_window(db,args.as_of,lookback_days=args.lookback_days,start=args.start)
            if args.command=='sync':
                capture=source.fetch_window(args.data_dir,result['window_start'],result['window_end'])
                result=warehouse.load_capture(db,capture)
    print(json.dumps(result,sort_keys=True,indent=2))


if __name__=='__main__':
    main()
