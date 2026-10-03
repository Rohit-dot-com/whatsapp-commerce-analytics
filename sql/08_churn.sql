-- =============================================================================
-- 08_churn.sql
-- Churn flag: a merchant is "churned" if they have placed >=1 order before,
-- but none in the last 14 days as of the snapshot date.
-- Also segments churn by business type and plan, and flags at-risk merchants
-- (active but slowing down) for an ops/retention team to reach out to.
-- =============================================================================

-- Snapshot date: the "today" the whole dataset was generated around.
-- Swap this for CURRENT_DATE once you're on live data.
\set snapshot '''2026-09-20'''

-- ---------------------------------------------------------------------------
-- 1. Churn flag per merchant
-- ---------------------------------------------------------------------------
WITH order_history AS (
    SELECT
        merchant_id,
        MIN(created_at)::date AS first_order_date,
        MAX(created_at)::date AS last_order_date,
        COUNT(*)              AS total_orders
    FROM orders
    GROUP BY merchant_id
)
SELECT
    m.merchant_id,
    m.business_type,
    m.plan,
    oh.first_order_date,
    oh.last_order_date,
    oh.total_orders,
    (:snapshot::date - oh.last_order_date)                                   AS days_since_last_order,
    CASE WHEN oh.last_order_date < :snapshot::date - INTERVAL '14 days'
         THEN TRUE ELSE FALSE END                                            AS churned
FROM merchants m
JOIN order_history oh USING (merchant_id)
ORDER BY days_since_last_order DESC
LIMIT 20;

-- ---------------------------------------------------------------------------
-- 2. Churn rate by business type and plan
--    Only merchants with >=1 order are "at risk of churning" here —
--    merchants that never ordered belong in the funnel analysis, not this one.
-- ---------------------------------------------------------------------------
WITH order_history AS (
    SELECT merchant_id, MAX(created_at)::date AS last_order_date
    FROM orders
    GROUP BY merchant_id
),
flagged AS (
    SELECT
        m.merchant_id, m.business_type, m.plan,
        oh.last_order_date < :snapshot::date - INTERVAL '14 days' AS churned
    FROM merchants m
    JOIN order_history oh USING (merchant_id)
)
SELECT
    business_type,
    plan,
    COUNT(*)                                                   AS merchants,
    COUNT(*) FILTER (WHERE churned)                            AS churned,
    ROUND(100.0 * COUNT(*) FILTER (WHERE churned) / COUNT(*), 1) AS churn_rate_pct
FROM flagged
GROUP BY business_type, plan
ORDER BY churn_rate_pct DESC;

-- ---------------------------------------------------------------------------
-- 3. "At risk" merchants: still active (ordered in last 14 days) but their
--    ordering pace has slowed a lot vs. their own earlier average.
--    Compares last-14-day order count to the merchant's average 14-day
--    order count over their full history, using window functions.
-- ---------------------------------------------------------------------------
WITH daily AS (
    SELECT merchant_id, created_at::date AS order_date
    FROM orders
),
per_merchant AS (
    SELECT
        merchant_id,
        COUNT(*) FILTER (WHERE order_date >= :snapshot::date - INTERVAL '14 days')  AS orders_last_14d,
        COUNT(*) FILTER (WHERE order_date <  :snapshot::date - INTERVAL '14 days')  AS orders_before,
        MIN(order_date)                                                             AS first_order_date,
        MAX(order_date)                                                             AS last_order_date
    FROM daily
    GROUP BY merchant_id
),
rated AS (
    SELECT
        merchant_id,
        orders_last_14d,
        last_order_date,
        -- average orders per 14-day window over the merchant's prior lifetime
        ROUND(
            orders_before / GREATEST(1.0,
                EXTRACT(DAY FROM (:snapshot::date - INTERVAL '14 days' - first_order_date)) / 14.0)
        , 2) AS avg_orders_per_14d_before
    FROM per_merchant
    WHERE orders_before >= 3   -- need some history to compare against
)
SELECT
    merchant_id,
    orders_last_14d,
    avg_orders_per_14d_before,
    last_order_date
FROM rated
WHERE last_order_date >= :snapshot::date - INTERVAL '14 days'   -- still technically active
  AND orders_last_14d < 0.5 * avg_orders_per_14d_before          -- pace more than halved
ORDER BY avg_orders_per_14d_before DESC
LIMIT 20;
