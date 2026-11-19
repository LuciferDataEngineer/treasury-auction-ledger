SELECT security_type,
       inflation_protected,
       floating_rate,
       comp_bid_expression,
       COUNT(*) AS auction_count,
       SUM(offering_cents) AS offering_cents,
       SUM(awarded_cents) AS awarded_cents,
       COUNT(awarded_cents) AS auctions_with_awarded_amount,
       SUM(recognized_cents) AS recognized_cents,
       COUNT(recognized_cents) AS auctions_with_recognized_amount,
       SUM(CASE WHEN reopening='Yes' THEN 1 ELSE 0 END) AS reopenings
FROM current_auctions
GROUP BY security_type,inflation_protected,floating_rate,comp_bid_expression
ORDER BY security_type,inflation_protected,floating_rate,comp_bid_expression;
