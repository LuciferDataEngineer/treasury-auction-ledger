"""Exact-unit SQL reports over one consistent warehouse reader snapshot."""
import csv
import fcntl
import json
import os
from pathlib import Path
import tempfile

QUERIES = ('instrument_summary','daily_totals','revision_history','quality','capture_audit')
SQL_DIR = Path(__file__).parent / 'sql'
OFFICIAL = 'https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/treasury_securities_auctions_v2'


def money(value):
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError('Money must remain integer cents until formatting')
    sign = '-' if value < 0 else ''
    whole, fraction = divmod(abs(value), 100)
    return f'{sign}{whole}.{fraction:02d}'


def display_rows(cursor):
    columns = [v[0] for v in cursor.description]
    result = []
    for row in cursor:
        item = {}
        for col,value in zip(columns,row):
            if col.endswith('_cents'):
                item[col[:-6] + '_usd'] = money(value)
            else:
                item[col] = value
        result.append(item)
    return result


def analyze(db):
    db.execute('BEGIN')
    try:
        checkpoint = db.execute('SELECT * FROM checkpoint WHERE singleton=1').fetchone()
        if checkpoint is None:
            raise ValueError('Load a complete capture before requesting a report')
        urls = [r[0] for r in db.execute('SELECT DISTINCT source_url FROM captures ORDER BY source_url')]
        if urls == [OFFICIAL]:
            coverage = 'Observed public Treasury API captures; auction-date coverage is a query interval, not a guarantee of upstream completeness.'
            provenance = 'public_treasury'
        elif urls and all(url.startswith('https://example.invalid/') for url in urls):
            coverage = 'SYNTHETIC fictional auction records and simulated changes; not real Treasury observations.'
            provenance = 'synthetic'
        else:
            coverage = 'CUSTOM OR MIXED source captures; independently verify their provenance before use.'
            provenance = 'custom_or_mixed'
        tables = {}; columns = {}
        for name in QUERIES:
            cursor = db.execute((SQL_DIR/(name+'.sql')).read_text())
            columns[name] = [c[0][:-6]+'_usd' if c[0].endswith('_cents') else c[0] for c in cursor.description]
            tables[name] = display_rows(cursor)
        result = {'coverage':coverage,'provenance':provenance,'source_urls':urls,
                  'covered_start':checkpoint['covered_start'],'covered_end':checkpoint['covered_end'],
                  'columns':columns,'tables':tables}
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise


def markdown_table(rows):
    if not rows:
        return '_No rows._'
    cols = list(rows[0])
    def cell(value):
        if value is None:
            return 'Not reported'
        return str(value).replace('|','\\|').replace('\n',' ').replace('\r',' ')
    return '\n'.join(['| '+' | '.join(cols)+' |','| '+' | '.join(['---']*len(cols))+' |'] +
                     ['| '+' | '.join(cell(r[c]) for c in cols)+' |' for r in rows])


def export_report(report,destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    parent_fd = os.open(destination.parent,os.O_RDONLY)
    try:
        fcntl.flock(parent_fd,fcntl.LOCK_EX)
        if destination.exists() or destination.is_symlink():
            raise ValueError('Report output already exists; choose a new directory')
        with tempfile.TemporaryDirectory(prefix='.report-',dir=destination.parent) as temporary:
            prepared = Path(temporary)/'ready'
            prepared.mkdir()
            (prepared/'summary.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
            lines = ['# Treasury auction ledger report',report['coverage'],
                     f"Retained auction-date interval: **{report['covered_start']} to {report['covered_end']}**, inclusive.",
                     'USD amounts are exact decimal strings from integer cents. Offered and awarded par are not cash proceeds or net borrowing. Rate, yield and discount-margin values retain their source meanings and are never pooled.',
                     'Different dates may have different latest acquisition times; the capture audit shows which windows were refreshed. An absence version means not observed in a complete API response, not officially cancelled.']
            for name,rows in report['tables'].items():
                with (prepared/(name+'.csv')).open('w',newline='',encoding='utf8') as stream:
                    writer = csv.DictWriter(stream,fieldnames=report['columns'][name],lineterminator='\n')
                    writer.writeheader()
                    writer.writerows(rows)
                lines.extend(['## '+name.replace('_',' ').title(),markdown_table(rows)])
            lines.append('A lack of observed revisions in an initial capture does not prove the source never revises records. Synthetic correction scenarios are documented separately.')
            (prepared/'report.md').write_text('\n\n'.join(lines)+'\n')
            os.rename(prepared,destination)
    finally:
        os.close(parent_fd)
