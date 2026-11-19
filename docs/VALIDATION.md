# Validation record — 20 November 2026, Melbourne

This record distinguishes real source captures from synthetic history/recovery tests. Source acquisition times below are UTC (19 November); the Melbourne calendar date was 20 November. No cloud deployment, paid API, hosted schedule, throughput SLA or investment model was exercised.

## Offline checks

Executed on macOS with **Python 3.14.7**:

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -v
```

**44 tests passed:** 19 source, 17 warehouse and 8 reporting/CLI tests. Captured output: [examples/test-results.txt](../examples/test-results.txt).

Source cases include two-pass 101-row pagination, zero-row results, count/content drift, missing pages, duplicate keys, range/order checks, malformed JSON, nonce/capture identity, bounded HTTP reads, partial responses, redirects, timestamp placement and cached-byte tampering.

Warehouse cases include exact cents, signed rates, missing future-issue results, atomic rollback, capture-ID evidence conflicts, concurrent replay, stale/same-time overlap, scope-limited absence, reappearance, A → B → A changes, unchanged-row provenance, coverage gaps, empty-window checkpoints, lookback planning, row bounds and accepted-capture recovery order. A failed transaction or replay does not consume a new durable load sequence.

Report/CLI cases include Decimal-oracle agreement, integer formatting beyond float-safe precision, missing-result denominators, empty CSV headers, truthful synthetic labels, failed/export retry preservation, concurrent reader consistency, explicit planning and the full recovery demo from a different working directory.

CI runs offline checks on **Python 3.12 and 3.14**. The repository's Actions run and badge are authoritative for actual remote execution; no source acquisition is scheduled in CI.

## Initial real window

Command executed:

```sh
python3 validate_live.py --start 2026-10-01 --end 2026-11-19 --output examples/observed
```

Acquisition began **2026-11-19T22:06:13.938740Z**. The adapter fetched the complete window twice: **one page per pass, two GETs total**, with identical canonical records. The live validation completed at **2026-11-19T22:06:15.333714Z**. Its original evidence is preserved as [live-acquisition.json](../examples/observed/live-acquisition.json).

After adding explicit report columns and durable load order, the same saved capture was verified and replayed into a fresh database. Final report generation and validation completed at **2026-11-19T22:11:05.300118Z**. This used **no additional network requests**; the final evidence is [validation.json](../examples/observed/validation.json).

| Check | Observed result |
|---|---|
| Requested auction dates | 2026-10-01 through 2026-11-19, inclusive |
| Source rows / accepted distinct auction keys | 61 / 61 |
| Bill / Note / Bond | 47 / 10 / 4 |
| Offered USD | 4,809,000,000,000.00 |
| Par awarded USD | 5,158,651,593,400.00 |
| Par recognized USD | 13,530,509,725,600.00 |
| Independent money reconciliation | 33 grouped SQL rows matched independent Decimal calculations |
| Independent quality checks | 9 metrics matched raw-field counts |
| SQL reports executed | 5 |
| Issue dates after window end | 6, preserved |
| Missing awarded amount / reported ratio / result document | 0 / 0 / 0 |
| Bytes transferred across both passes | 744,656 |
| Replay after database reopen | Complete database dump and report unchanged |
| SQLite integrity / foreign-key violations | `ok` / 0 |

`validate_live.py` builds its monetary reference from raw dictionaries with independent Decimal sums; it does not reuse the warehouse transform or SQL queries. It checks the initial absence of revision records, capture identity and source-derived quality counts. Result coverage is explicit: missing amounts are never converted to zero before aggregation.

Initial capture ID:

```text
0e38fda0b625ce40e28838599d091a499130a53bc333fee491dd149c89db6282
```

Combined ordered raw-response SHA-256 (both passes):

```text
ee2d437db7d78accae70f435b94a0a2897c903867e23c714538842f42858073a
```

The local cache remains under ignored `data/`. Sidecars retain both passes and their page hashes, request URLs, sizes and acquisition times. Published CSV/JSON/Markdown outputs are derived from the verified capture; they contain no unrelated local files or credentials.

## Real overlapping refresh through the CLI

The initial sidecar was loaded into a second local warehouse and replayed unchanged. The actual CLI planner and sync were then run:

```sh
python3 pipeline.py load path/to/initial-capture.json --db data/live-cli.sqlite
python3 pipeline.py load path/to/initial-capture.json --db data/live-cli.sqlite
python3 pipeline.py plan --as-of 2026-11-19 --db data/live-cli.sqlite
python3 pipeline.py sync --as-of 2026-11-19 --data-dir data/overlap --db data/live-cli.sqlite
```

The planner selected **6–19 November**, the final 14 inclusive calendar days. Acquisition began **2026-11-19T22:11:34.747340Z** and retrieved **18 records twice**, with two GETs and 251,254 transferred bytes across the passes.

Result: **18 unchanged, 0 inserted, 0 revised, 0 newly absent**. The warehouse retained **61 current auctions and 61 history versions**, while accepted captures increased to two. Current key/version/projected-hash tuples were identical before and after; integrity and foreign keys passed. Evidence: [live-overlap-validation.json](../examples/live-overlap-validation.json).

These observations are minutes apart. They validate the real incremental path and provenance handling, not a long-term revision trend. The published aggregate report remains the separately identified initial capture's output.

## Synthetic interruption and return drill

Command executed after final schema changes:

```sh
python3 demo.py --work-dir data/final-demo
```

Evidence: [synthetic-validation.json](../examples/synthetic-validation.json).

| Capture | Input rows | Inserted | Revised | Newly absent | Unchanged input |
|---|---:|---:|---:|---:|---:|
| Baseline | 3 | 3 | 0 | 0 | 0 |
| Correction and absence | 2 | 1 | 1 | 1 | 0 |
| Reversion and return | 3 | 0 | 2 | 0 | 1 |

An injected SQLite trigger failure interrupted version insertion. The previous history, accepted-capture count and checkpoint were verified unchanged. Retrying the same capture after removing the trigger succeeded. Reopening the database and replaying the older accepted capture preserved newer state. A new stale overlapping capture was rejected. Final state: **4 current auctions, 8 versions, 3 captures**, integrity `ok`, zero foreign-key violations.

All records and clock times in this drill are fictional. `absent_from_observation` never means Treasury officially cancelled an auction. The test suite additionally reconstructs every business/audit table from accepted captures in durable load order; it demonstrates why timestamp-only sorting is not a valid general recovery algorithm.

## Evidence limits

- Two real overlapping captures support the numerical and ingestion claims above; they do not establish historical correction frequencies or auction completeness outside the queried source windows.
- Two matching passes cannot prove upstream transaction isolation or detect a change outside the chosen overlap.
- Local digests establish byte integrity, not Treasury-signed authenticity. The mutable endpoint cannot retrieve old raw responses by hash; historical raw replay requires the retained cache.
- Monetary, ratio and instrument definitions remain those of the source. Offered/awarded/recognized amounts are not net borrowing or cash proceeds; rates and margins are not interchangeable.
- No schema migration, long-running scheduler, clinical/investment recommendation, production security assessment, distributed recovery or scale benchmark was performed.
