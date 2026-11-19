SELECT auction_date,COUNT(*) AS auction_count,
       SUM(offering_cents) AS offering_cents,
       SUM(awarded_cents) AS awarded_cents,
       COUNT(awarded_cents) AS auctions_with_awarded_amount
FROM current_auctions
GROUP BY auction_date ORDER BY auction_date;
