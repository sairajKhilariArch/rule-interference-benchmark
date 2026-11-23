WITH payment_totals AS (
    SELECT order_id, SUM(amount) AS total
    FROM payments
    GROUP BY order_id
),
refund_totals AS (
    SELECT order_id, SUM(amount) AS total
    FROM refunds
    GROUP BY order_id
)
SELECT COALESCE(
    SUM(
        COALESCE(p.total, 0) - COALESCE(r.total, 0)
    ), 0
) AS net_revenue
FROM orders o
JOIN customers c
    ON c.customer_id = o.customer_id
LEFT JOIN payment_totals p
    ON p.order_id = o.order_id
LEFT JOIN refund_totals r
    ON r.order_id = o.order_id
WHERE c.is_verified = 1
  AND c.is_deleted = 0;
