-- =============================================================================
-- 07_fulfillment.sql
-- Order fulfillment: status mix, cancellation rate by business type and by
-- response-time bucket, and delivery-time distribution.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Overall order status mix, and by business type
-- ---------------------------------------------------------------------------
SELECT
    m.business_type,
    COUNT(*)                                                         AS orders,
    COUNT(*) FILTER (WHERE o.status = 'delivered')                   AS delivered,
    COUNT(*) FILTER (WHERE o.status = 'cancelled')                   AS cancelled,
    ROUND(100.0 * COUNT(*) FILTER (WHERE o.status = 'delivered') / COUNT(*), 1) AS pct_delivered,
    ROUND(100.0 * COUNT(*) FILTER (WHERE o.status = 'cancelled') / COUNT(*), 1) AS pct_cancelled,
    ROUND(AVG(o.amount)::numeric, 0)                                 AS avg_order_value_inr
FROM orders o
JOIN merchants m USING (merchant_id)
GROUP BY m.business_type
ORDER BY pct_cancelled DESC;

-- ---------------------------------------------------------------------------
-- 2. Cancellation rate by "worst response wait" in the order's conversation.
--    This is the key story: slow responses -> more cancellations.
--    Response wait is derived the same way as in 06_ai_performance.sql.
-- ---------------------------------------------------------------------------
WITH ordered AS (
    SELECT
        conversation_id, sender, sent_at,
        LAG(sender)  OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sender,
        LAG(sent_at) OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sent_at
    FROM messages
),
waits AS (
    SELECT
        conversation_id,
        MAX(EXTRACT(EPOCH FROM (sent_at - prev_sent_at))) AS worst_wait_sec
    FROM ordered
    WHERE sender <> 'customer' AND prev_sender = 'customer'
    GROUP BY conversation_id
),
bucketed AS (
    SELECT
        o.order_id,
        o.status,
        CASE
            WHEN w.worst_wait_sec < 30   THEN '1. <30s'
            WHEN w.worst_wait_sec < 120  THEN '2. 30s-2m'
            WHEN w.worst_wait_sec < 600  THEN '3. 2-10m'
            WHEN w.worst_wait_sec < 3600 THEN '4. 10-60m'
            ELSE                              '5. >1h'
        END AS wait_bucket
    FROM orders o
    JOIN waits w USING (conversation_id)
)
SELECT
    wait_bucket,
    COUNT(*)                                                          AS orders,
    COUNT(*) FILTER (WHERE status = 'cancelled')                      AS cancelled,
    ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'cancelled') / COUNT(*), 1) AS cancellation_rate_pct
FROM bucketed
GROUP BY wait_bucket
ORDER BY wait_bucket;

-- ---------------------------------------------------------------------------
-- 3. Delivery time distribution (median, p90) by business type, for
--    delivered orders only. NULLs (salon/tutoring/not-yet-delivered) excluded.
-- ---------------------------------------------------------------------------
SELECT
    m.business_type,
    COUNT(*)                                                                       AS delivered_orders,
    ROUND(AVG(o.delivery_minutes)::numeric, 0)                                     AS avg_minutes,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY o.delivery_minutes)                 AS median_minutes,
    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY o.delivery_minutes)                 AS p90_minutes
FROM orders o
JOIN merchants m USING (merchant_id)
WHERE o.status = 'delivered' AND o.delivery_minutes IS NOT NULL
GROUP BY m.business_type
ORDER BY median_minutes DESC;

-- ---------------------------------------------------------------------------
-- 4. Monthly revenue trend (delivered orders only) — feeds a line chart.
-- ---------------------------------------------------------------------------
SELECT
    DATE_TRUNC('month', created_at)::date AS month,
    COUNT(*)                              AS delivered_orders,
    SUM(amount)                           AS revenue_inr,
    ROUND(AVG(amount)::numeric, 0)        AS avg_order_value_inr
FROM orders
WHERE status = 'delivered'
GROUP BY 1
ORDER BY 1;
