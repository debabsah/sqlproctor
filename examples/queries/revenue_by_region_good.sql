-- Correct "revenue by region": revenue at the order_item grain, active orders only.
SELECT c.region, SUM(oi.net_amount) AS revenue
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN customers c ON o.customer_id = c.customer_id
WHERE o.deleted_at IS NULL
GROUP BY c.region;
