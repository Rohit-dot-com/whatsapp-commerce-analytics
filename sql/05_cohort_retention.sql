-- =============================================================================
-- 05_cohort_retention.sql
-- Weekly cohort retention: merchants grouped by signup week, tracked by
-- whether they placed >=1 order in each subsequent week of their lifetime.
-- =============================================================================

WITH cohorts AS (
    SELECT merchant_id, DATE_TRUNC('week', signup_date)::date AS cohort_week
    FROM merchants
),
order_weeks AS (
    -- distinct (merchant, week) pairs in which the merchant placed >=1 order
    SELECT DISTINCT merchant_id, DATE_TRUNC('week', created_at)::date AS order_week
    FROM orders
),
activity AS (
    SELECT
        c.merchant_id,
        c.cohort_week,
        ow.order_week,
        -- week index: 0 = signup week, 1 = one week later, etc.
        ((ow.order_week - c.cohort_week) / 7)::int AS week_number
    FROM cohorts c
    JOIN order_weeks ow ON ow.merchant_id = c.merchant_id
    WHERE ow.order_week >= c.cohort_week
),
cohort_sizes AS (
    SELECT cohort_week, COUNT(*) AS cohort_size
    FROM cohorts
    GROUP BY cohort_week
),
retention AS (
    SELECT
        cohort_week,
        week_number,
        COUNT(DISTINCT merchant_id) AS active_merchants
    FROM activity
    WHERE week_number BETWEEN 0 AND 12          -- first 12 weeks of life
    GROUP BY cohort_week, week_number
)
SELECT
    r.cohort_week,
    cs.cohort_size,
    r.week_number,
    r.active_merchants,
    ROUND(100.0 * r.active_merchants / cs.cohort_size, 1) AS retention_pct
FROM retention r
JOIN cohort_sizes cs USING (cohort_week)
ORDER BY r.cohort_week, r.week_number;

-- ---------------------------------------------------------------------------
-- Same thing, pivoted into a week-0..week-8 heatmap shape (easier to paste
-- straight into a spreadsheet or a Streamlit dataframe for the heatmap).
-- Only cohorts with at least 8 full weeks of possible history are shown,
-- so every column is a fair comparison across cohorts.
-- ---------------------------------------------------------------------------
WITH cohorts AS (
    SELECT merchant_id, DATE_TRUNC('week', signup_date)::date AS cohort_week
    FROM merchants
),
order_weeks AS (
    SELECT DISTINCT merchant_id, DATE_TRUNC('week', created_at)::date AS order_week
    FROM orders
),
activity AS (
    SELECT c.merchant_id, c.cohort_week,
           ((ow.order_week - c.cohort_week) / 7)::int AS week_number
    FROM cohorts c
    JOIN order_weeks ow ON ow.merchant_id = c.merchant_id
    WHERE ow.order_week >= c.cohort_week
),
cohort_sizes AS (
    SELECT cohort_week, COUNT(*) AS cohort_size FROM cohorts GROUP BY cohort_week
)
SELECT
    cs.cohort_week,
    cs.cohort_size,
    ROUND(100.0 * COUNT(DISTINCT a.merchant_id) FILTER (WHERE a.week_number = 0) / cs.cohort_size, 1) AS w0,
    ROUND(100.0 * COUNT(DISTINCT a.merchant_id) FILTER (WHERE a.week_number = 1) / cs.cohort_size, 1) AS w1,
    ROUND(100.0 * COUNT(DISTINCT a.merchant_id) FILTER (WHERE a.week_number = 2) / cs.cohort_size, 1) AS w2,
    ROUND(100.0 * COUNT(DISTINCT a.merchant_id) FILTER (WHERE a.week_number = 4) / cs.cohort_size, 1) AS w4,
    ROUND(100.0 * COUNT(DISTINCT a.merchant_id) FILTER (WHERE a.week_number = 8) / cs.cohort_size, 1) AS w8
FROM cohort_sizes cs
LEFT JOIN activity a ON a.cohort_week = cs.cohort_week
WHERE cs.cohort_week <= CURRENT_DATE - INTERVAL '8 weeks'
GROUP BY cs.cohort_week, cs.cohort_size
ORDER BY cs.cohort_week;

-- ---------------------------------------------------------------------------
-- The "fast activation" story for your README: does ordering within 3 days
-- of signup predict better 8-week retention? (window function: FIRST_VALUE)
-- ---------------------------------------------------------------------------
WITH first_order AS (
    SELECT
        m.merchant_id,
        m.signup_date,
        MIN(o.created_at)::date AS first_order_date
    FROM merchants m
    JOIN orders o USING (merchant_id)
    GROUP BY m.merchant_id, m.signup_date
),
labeled AS (
    SELECT
        merchant_id,
        (first_order_date - signup_date) <= 3 AS fast_activated
    FROM first_order
    WHERE signup_date <= CURRENT_DATE - INTERVAL '60 days'   -- old enough to measure 60d retention
),
lifetime AS (
    SELECT merchant_id, MIN(created_at) AS first_dt, MAX(created_at) AS last_dt
    FROM orders
    GROUP BY merchant_id
)
SELECT
    l.fast_activated,
    COUNT(*)                                                            AS merchants,
    ROUND(100.0 * COUNT(*) FILTER (
        WHERE lt.last_dt - lt.first_dt >= INTERVAL '60 days'
    ) / COUNT(*), 1)                                                    AS pct_retained_60d
FROM labeled l
JOIN lifetime lt USING (merchant_id)
GROUP BY l.fast_activated
ORDER BY l.fast_activated DESC;
