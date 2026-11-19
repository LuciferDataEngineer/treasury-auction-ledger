# Treasury auction ledger report

Observed public Treasury API captures; auction-date coverage is a query interval, not a guarantee of upstream completeness.

Retained auction-date interval: **2026-10-01 to 2026-11-19**, inclusive.

USD amounts are exact decimal strings from integer cents. Offered and awarded par are not cash proceeds or net borrowing. Rate, yield and discount-margin values retain their source meanings and are never pooled.

Different dates may have different latest acquisition times; the capture audit shows which windows were refreshed. An absence version means not observed in a complete API response, not officially cancelled.

## Instrument Summary

| security_type | inflation_protected | floating_rate | comp_bid_expression | auction_count | offering_usd | awarded_usd | auctions_with_awarded_amount | recognized_usd | auctions_with_recognized_amount | reopenings |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Bill | Not reported | Not reported | Rate | 33 | 2959000000000.00 | 3163106835100.00 | 33 | 8518472155200.00 | 33 | 23 |
| Bill | No | No | Rate | 14 | 1319000000000.00 | 1409086089100.00 | 14 | 3604146886300.00 | 14 | 11 |
| Bond | No | No | Yield | 4 | 76000000000.00 | 83243015100.00 | 4 | 199791369500.00 | 4 | 2 |
| Note | No | No | Yield | 7 | 380000000000.00 | 422923498200.00 | 7 | 1002448604500.00 | 7 | 1 |
| Note | No | Yes | Discount Margin | 1 | 30000000000.00 | 33333576500.00 | 1 | 94975549400.00 | 1 | 0 |
| Note | Yes | No | Yield | 2 | 45000000000.00 | 46958579400.00 | 2 | 110675160700.00 | 2 | 1 |

## Daily Totals

| auction_date | auction_count | offering_usd | awarded_usd | auctions_with_awarded_amount |
| --- | --- | --- | --- | --- |
| 2026-10-01 | 2 | 195000000000.00 | 204929926700.00 | 2 |
| 2026-10-05 | 2 | 177000000000.00 | 190333615900.00 | 2 |
| 2026-10-06 | 2 | 153000000000.00 | 161533982600.00 | 2 |
| 2026-10-07 | 2 | 114000000000.00 | 121300765200.00 | 2 |
| 2026-10-08 | 3 | 237000000000.00 | 255795558600.00 | 3 |
| 2026-10-13 | 3 | 272000000000.00 | 287475290500.00 | 3 |
| 2026-10-14 | 1 | 75000000000.00 | 80054540100.00 | 1 |
| 2026-10-15 | 2 | 215000000000.00 | 229488827700.00 | 2 |
| 2026-10-19 | 2 | 177000000000.00 | 189486433700.00 | 2 |
| 2026-10-20 | 1 | 95000000000.00 | 101701431000.00 | 1 |
| 2026-10-21 | 2 | 88000000000.00 | 93403309800.00 | 2 |
| 2026-10-22 | 4 | 266000000000.00 | 281489271000.00 | 4 |
| 2026-10-26 | 3 | 246000000000.00 | 269416210600.00 | 3 |
| 2026-10-27 | 3 | 219000000000.00 | 240036562000.00 | 3 |
| 2026-10-28 | 2 | 105000000000.00 | 112155149100.00 | 2 |
| 2026-10-29 | 3 | 269000000000.00 | 285354737600.00 | 3 |
| 2026-11-02 | 2 | 177000000000.00 | 188018006700.00 | 2 |
| 2026-11-03 | 1 | 100000000000.00 | 106224817400.00 | 1 |
| 2026-11-04 | 1 | 75000000000.00 | 80410350900.00 | 1 |
| 2026-11-05 | 2 | 225000000000.00 | 241230913800.00 | 2 |
| 2026-11-09 | 4 | 335000000000.00 | 365303986600.00 | 4 |
| 2026-11-10 | 1 | 42000000000.00 | 50519618200.00 | 1 |
| 2026-11-12 | 4 | 325000000000.00 | 350096873700.00 | 4 |
| 2026-11-16 | 2 | 177000000000.00 | 189621168300.00 | 2 |
| 2026-11-17 | 1 | 100000000000.00 | 107130245700.00 | 1 |
| 2026-11-18 | 2 | 91000000000.00 | 98282616300.00 | 2 |
| 2026-11-19 | 4 | 259000000000.00 | 277857383700.00 | 4 |

## Revision History

_No rows._

## Quality

| metric | value |
| --- | --- |
| current_auctions | 61 |
| history_versions | 61 |
| captures | 1 |
| current_absent_keys | 0 |
| source_record_date_differs_from_issue_date | 0 |
| issues_after_covered_end | 6 |
| missing_awarded_amount | 0 |
| missing_reported_bid_to_cover | 0 |
| missing_results_document | 0 |

## Capture Audit

| load_sequence | captured_at | window_start | window_end | raw_rows | inserted | revised | removed | unchanged | capture_id | raw_sha256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-19T22:06:13.938740Z | 2026-10-01 | 2026-11-19 | 61 | 61 | 0 | 0 | 0 | 0e38fda0b625ce40e28838599d091a499130a53bc333fee491dd149c89db6282 | ee2d437db7d78accae70f435b94a0a2897c903867e23c714538842f42858073a |

A lack of observed revisions in an initial capture does not prove the source never revises records. Synthetic correction scenarios are documented separately.
