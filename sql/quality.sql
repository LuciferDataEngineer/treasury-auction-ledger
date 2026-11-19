SELECT 'current_auctions' AS metric,COUNT(*) AS value FROM current_auctions
UNION ALL SELECT 'history_versions',COUNT(*) FROM auction_versions
UNION ALL SELECT 'captures',COUNT(*) FROM captures
UNION ALL SELECT 'current_absent_keys',COUNT(*) FROM auction_versions
 WHERE observed_to IS NULL AND state='absent_from_observation'
UNION ALL SELECT 'source_record_date_differs_from_issue_date',COUNT(*) FROM current_auctions
 WHERE source_record_date != issue_date
UNION ALL SELECT 'issues_after_covered_end',COUNT(*) FROM current_auctions
 WHERE issue_date > (SELECT covered_end FROM checkpoint WHERE singleton=1)
UNION ALL SELECT 'missing_awarded_amount',COUNT(*) FROM current_auctions WHERE awarded_cents IS NULL
UNION ALL SELECT 'missing_reported_bid_to_cover',COUNT(*) FROM current_auctions WHERE bid_to_cover_ratio IS NULL
UNION ALL SELECT 'missing_results_document',COUNT(*) FROM current_auctions WHERE pdf_filenm_comp_results IS NULL;
