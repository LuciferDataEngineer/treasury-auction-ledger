PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS captures (
    load_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL CHECK(raw_bytes > 0),
    manifest_path TEXT NOT NULL,
    rows_sha256 TEXT NOT NULL,
    raw_rows INTEGER NOT NULL CHECK(raw_rows >= 0),
    inserted INTEGER NOT NULL CHECK(inserted >= 0),
    revised INTEGER NOT NULL CHECK(revised >= 0),
    removed INTEGER NOT NULL CHECK(removed >= 0),
    unchanged INTEGER NOT NULL CHECK(unchanged >= 0),
    CHECK(window_start <= window_end),
    CHECK(raw_rows = inserted + revised + unchanged)
);
CREATE INDEX IF NOT EXISTS captures_by_window ON captures(window_start, window_end, captured_at);

CREATE TABLE IF NOT EXISTS capture_rows (
    capture_id TEXT NOT NULL REFERENCES captures(capture_id),
    auction_date TEXT NOT NULL,
    cusip TEXT NOT NULL,
    raw_row_sha256 TEXT NOT NULL,
    projected_sha256 TEXT NOT NULL,
    PRIMARY KEY(capture_id, auction_date, cusip)
);

CREATE TABLE IF NOT EXISTS auction_versions (
    auction_date TEXT NOT NULL,
    cusip TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    observed_from TEXT NOT NULL,
    observed_to TEXT,
    observed_capture_id TEXT NOT NULL REFERENCES captures(capture_id),
    closing_capture_id TEXT REFERENCES captures(capture_id),
    state TEXT NOT NULL CHECK(state IN ('present', 'absent_from_observation')),
    raw_row_sha256 TEXT,
    projected_sha256 TEXT,
    source_record_date TEXT,
    issue_date TEXT,
    maturity_date TEXT,
    security_type TEXT,
    security_term TEXT,
    offering_cents INTEGER,
    awarded_cents INTEGER,
    recognized_cents INTEGER,
    high_rate TEXT,
    high_yield TEXT,
    high_discnt_margin TEXT,
    bid_to_cover_ratio TEXT,
    reopening TEXT,
    comp_bid_expression TEXT,
    inflation_protected TEXT,
    floating_rate TEXT,
    pdf_filenm_comp_results TEXT,
    PRIMARY KEY(auction_date, cusip, version),
    CHECK((observed_to IS NULL) = (closing_capture_id IS NULL)),
    CHECK(observed_to IS NULL OR observed_from < observed_to),
    CHECK(state != 'present' OR (source_record_date IS NOT NULL AND issue_date IS NOT NULL
          AND maturity_date IS NOT NULL AND security_type IS NOT NULL
          AND security_term IS NOT NULL AND offering_cents IS NOT NULL
          AND raw_row_sha256 IS NOT NULL AND projected_sha256 IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_version ON auction_versions(auction_date, cusip) WHERE observed_to IS NULL;
CREATE INDEX IF NOT EXISTS versions_by_capture ON auction_versions(observed_capture_id);
CREATE INDEX IF NOT EXISTS versions_by_type ON auction_versions(security_type, auction_date);

CREATE TABLE IF NOT EXISTS auction_observations (
    auction_date TEXT NOT NULL,
    cusip TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    last_capture_id TEXT NOT NULL REFERENCES captures(capture_id),
    PRIMARY KEY(auction_date, cusip)
);

CREATE TABLE IF NOT EXISTS checkpoint (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    covered_start TEXT NOT NULL,
    covered_end TEXT NOT NULL,
    last_capture_id TEXT NOT NULL REFERENCES captures(capture_id),
    CHECK(covered_start <= covered_end)
);

CREATE VIEW IF NOT EXISTS current_auctions AS
SELECT v.*, o.last_observed_at, o.last_capture_id
FROM auction_versions v JOIN auction_observations o USING(auction_date, cusip)
WHERE v.observed_to IS NULL AND v.state='present';
