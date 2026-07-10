-- Naive "revenue by region": joins shipments and silently inflates revenue,
-- because every order_item row is multiplied by that order's shipment count.
SELECT c.region, SUM(oi.net_amount) AS revenue
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN customers c ON o.customer_id = c.customer_id
JOIN shipments s ON o.order_id = s.order_id
WHERE o.deleted_at IS NULL
GROUP BY c.region;
