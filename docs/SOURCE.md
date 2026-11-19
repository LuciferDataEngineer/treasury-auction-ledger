# Treasury source and acquisition contract

Source documentation and public API probes were accessed on **20 November 2026 Australia/Melbourne** (19 November UTC). Data is provided by the U.S. Department of the Treasury's Bureau of the Fiscal Service. Original code and synthetic fixtures are separately covered by the repository's MIT license.

## Why this source now

Treasury's [4 November 2026 quarterly refunding statement](https://home.treasury.gov/news/press-releases/sb0678/) announced $125 billion of securities to refinance approximately $97 billion of privately held maturities. Auction reporting is a current operational data problem: announcements can precede results, reopenings reuse security identifiers, and mutable published records can change after an initial load. This project demonstrates auditable ingestion; its offering and award totals are not estimates of net federal borrowing or the effects of that announcement.

The [Treasury Securities Auctions Data dataset](https://fiscaldata.treasury.gov/datasets/treasury-securities-auctions-data/) contains both a legacy table and **Treasury Securities Auctions Data (Updated)**. This project deliberately uses the updated endpoint:

```text
https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/treasury_securities_auctions_v2
```

The endpoint and complete field dictionary can also be inspected in the site's [public page-data JSON](https://fiscaldata.treasury.gov/page-data/datasets/treasury-securities-auctions-data/page-data.json). At research time the updated endpoint included future November 25 announcements, while a descending probe of the legacy `auctions_query` endpoint stopped at October 22 and had missing result fields. The two table schemas are not interchangeable. No claim is made that the updated endpoint alone supplies the entire historical coverage of the combined dataset page.

## Permission and cost

[Fiscal Data's licensing policy](https://fiscaldata.treasury.gov/about-us/#licensing) permits copying, adapting, redistributing and using the site's data for commercial and noncommercial purposes. The [API documentation](https://fiscaldata.treasury.gov/api-documentation/) states that no user account or token registration is required. These public GET requests do not create a billing account, incur a paid API subscription or provision infrastructure.

Attribution is retained in the README and this document. No Treasury endorsement is implied. Source response bytes remain under the project's ignored local data directory; published examples contain reviewed observations and aggregates rather than an unrelated workspace export.

## Request and completeness contract

`source.fetch_window(data_dir, start, end)` uses explicit inclusive ISO calendar dates and retrieves all fields:

```text
filter=comp_auction_close_date:gte:START,comp_auction_close_date:lte:END
sort=comp_auction_close_date,cusip
page[size]=100
page[number]=1
```

The implementation constructs requests against the fixed official endpoint. It does not accept an arbitrary URL or follow source pagination links as new destinations. No `fields` parameter is sent: Fiscal Data documents that selecting some subsets of fields can trigger automatic aggregation. Raw record preservation requires the complete row contract.

1. Validate the requested window, with a maximum of 93 inclusive days.
2. Record `captured_at` in UTC **before the first request**. This is local acquisition-start time, not a Treasury revision timestamp.
3. Fetch page 1 and require integer pagination metadata. Continue through the declared pages, at most 10 pages and 1,000 records per pass.
4. Require each page's `meta.count` to equal its rows, stable `total-count`/`total-pages`, full intermediate pages, the correct last-page length and a matching next-link presence/absence. Empty results may declare zero or one page and must remain empty.
5. Require every row's competitive-auction date to lie in the requested interval and every CUSIP to be a nine-character uppercase alphanumeric identifier. Require the composite `(comp_auction_close_date, cusip)` keys to be globally strictly ascending. Duplicate keys, including duplicates spanning pages, reject the whole capture.
6. Fetch the entire window a **second time**. Compare canonical complete row content, not only row counts or a high watermark. Any difference rejects the capture before raw pages or a sidecar are accepted.
7. Persist both passes' response bytes by SHA-256, followed by a new acquisition sidecar. Existing matching raw bytes may be shared; a corrupt existing file is never silently replaced.

Two identical sequential reads are evidence of an observed consistent window. Fiscal Data does not expose a database snapshot token in this contract. These checks cannot prove an atomic upstream transaction, detect a change that happens and reverts between reads, or detect corrections outside the requested window. A failed request or a changing window should be retried as a whole; there are no hidden retries or incomplete partial loads.

Each response is bounded at **2 MiB**, with a **15-second socket timeout**. A **90-second acquisition deadline** is checked between reads; an active socket read can extend to its timeout. There are at most 20 GET requests per acquisition (10 pages twice). The demonstrated 61-row window requires two GETs. Changing source volume or schema may require an explicit adapter review instead of silently relaxing bounds.

## Source fields and interpretation

All values in the inspected responses were JSON strings, including the literal string `"null"`. The warehouse performs explicit date, integer and decimal normalization; it does not cast every field using the API's generic metadata. For example, the official dictionary marks `comp_bid_expression` as NUMBER, while the actual values are `Rate`, `Yield` and `Discount Margin`.

| Field | Source meaning / normalization concern |
|---|---|
| `cusip` | Identifier for the security; repeated reopenings require an auction-date component in the record key. |
| `comp_auction_close_date` | Date competitive bidding closes; the requested window and ordering field. |
| `announcemt_date` | Announcement date, which can precede the auction and results. |
| `issue_date`, `maturity_date` | Security issuance and maturity calendar dates. |
| `record_date` | Generic dictionary says publication date; in all 61 researched rows it equalled `issue_date`, including future dates. Preserve it, but do not use it as a revision timestamp or extraction watermark. |
| `security_type` | Bill, Note or Bond in the observed window. |
| `security_desc`, `security_term` | Source description and term; preserve strings instead of inferring exact maturities from labels. |
| `reopening` | Whether an additional amount of an outstanding security is auctioned; CUSIP alone is therefore insufficient. |
| `offering_amt` | USD amount offered for purchase. It is not net borrowing, proceeds or a stock of outstanding debt. |
| `grand_total_par_awarded` | Sum of par amounts awarded across participants. Store exact USD integer amounts. |
| `grand_total_par_recog` | Sum of par amounts recognized across participants. Do not interpret it as new cash received. |
| `total_par_inside_awarded`, `total_par_inside_recog` | Participants classified inside the offering amount; optional even for completed results. |
| `bid_to_cover_ratio` | Source-published ratio of received to awarded par **excluding SOMA**. Do not recompute it from grand totals without the participant exclusions. |
| `comp_bid_expression` | Rate, Yield or Discount Margin; retain the distinction when presenting rates. |
| `high_rate` | Highest accepted bill discount rate. |
| `high_yield` | Highest accepted nominal-coupon or TIPS yield. |
| `high_discnt_margin` | Highest accepted FRN discount margin; not the full FRN coupon rate. |
| `price_per_amt`, `high_price` | Price calculation's face-amount basis and reported price; TIPS treatment differs. |
| `floating_rate`, `inflation_protected` | Yes/No or missing; a missing source flag must not silently become No. |
| `pdf_filenm_comp_results` | Official auction-result filename; its presence helps distinguish published results from an announcement-only record. |

The rate/yield/margin fields represent different concepts. A single blended average across these types would be misleading. Use exact decimal representations, preserve missingness, and allow signed source rate values where appropriate. The [Treasury marketable-securities glossary](https://www.treasurydirect.gov/help-center/glossary/glossary-for-marketable-securities/) provides the financial terminology; an [official FRN result](https://www.treasurydirect.gov/instit/annceresult/press/preanre/2025/R_20250625_2.pdf) illustrates percentage units for discount margins. This project does not compare different rate types as interchangeable yields.

## Research probe observations

An initial bounded probe requested the complete window **2026-10-01 through 2026-11-19**, sorted by competitive-auction date and CUSIP. It returned HTTP 200 and 61 rows. That research probe used a page size of 1,000; the implemented adapter instead uses 100 rows per page and checks the complete window twice. The project's validation document records the adapter's own execution separately.

| Observation | Value |
|---|---:|
| Rows / distinct composite auction keys | 61 / 61 |
| Bill / Note / Bond rows | 47 / 10 / 4 |
| Rate / Yield / Discount Margin expressions | 47 / 13 / 1 |
| Reopenings / new issues | 38 / 23 |
| Offering amounts, USD | 4,809,000,000,000 |
| Grand total par awarded, USD | 5,158,651,593,400 |
| Grand total par recognized, USD | 13,530,509,725,600 |
| Rows with published result filenames | 61 |
| Missing grand total par awarded | 0 |
| Missing inside-offering awarded amount | 17 |
| `record_date` equal to `issue_date` | 61 |

These figures describe source records observed at access time. The same query may change later. Sums across auctions can include repeated issuance of the same security and must not be described as unique securities or net financing.

## Provenance and offline replay

Raw responses are stored as `data/raw/<sha256>.json`. A sidecar in `data/manifests/<capture_id>.json` retains the source URL, request window, acquisition-start UTC timestamp, page URLs/counts, byte lengths, ordered page hashes, and both passes' relative raw paths. Relative paths allow the complete data directory to be moved without rewriting its sidecars.

`raw_sha256` hashes the number of pages in each pass and each ordered response's length and bytes. `raw_bytes` counts the bytes transferred across **both** passes, even when identical pages share one local file. `capture_id` hashes the manifest metadata, including `captured_at` and a random nonce. Identical-content acquisitions therefore have distinct identities; replaying an existing sidecar keeps its original identity. This permits a later A → B → A observation sequence to be retained rather than incorrectly collapsing the last A into the first observation.

`source.read_capture(manifest_path)` is the explicit offline path. It verifies manifest identity, request parameters, local path boundaries, response sizes and hashes, complete pagination, composite-key order and both passes' content agreement. It never trusts a caller-provided `rows` array in place of the stored responses. Digests establish local integrity, not a signed Treasury attestation.

On a checksum failure, keep the damaged cache for investigation and acquire into a new data directory. Historical mutable responses cannot be recovered from their hashes through this API. Back up raw pages and sidecars together if historical replay matters.

## Deterministic source tests

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -p test_source.py -v
```

**19 source tests passed** locally on Python 3.14 during implementation. They use synthetic HTTP pages, not public network requests. Coverage includes two-pass pagination, acquisition identity and clock placement, empty windows, concurrent-source drift, metadata drift, missing pages, duplicate/out-of-order/out-of-range keys, byte limits without a Content-Length header, deadlines, redirects, partial HTTP responses, manifest/path tampering, corrupted raw files, duplicate JSON keys and nonfinite values. Combined project checks and actual live validation belong in the final validation record.
