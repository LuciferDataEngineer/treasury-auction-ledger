SELECT auction_date,cusip,version,state,observed_from,observed_to,
       offering_cents,awarded_cents,comp_bid_expression,
       high_rate,high_yield,high_discnt_margin,bid_to_cover_ratio
FROM auction_versions
WHERE version > 1
ORDER BY auction_date,cusip,version;
