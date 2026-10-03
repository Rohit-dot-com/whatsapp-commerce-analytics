-- =============================================================================
-- 04_funnel.sql
-- Merchant activation funnel: signed_up -> connected_whatsapp -> first_message -> first_order
-- =============================================================================

WITH funnel AS (
    SELECT
        MAX(event_date) FILTER (WHERE event = 'signed_up')          AS signed_up,
        MAX(event_date) FILTER (WHERE event = 'connected_whatsapp') AS connected,
        MAX(event_date) FILTER (WHERE event = 'first_message')      AS first_message,
        MAX(event_date) FILTER (WHERE event = 'first_order')        AS first_order
    FROM merchant_events
    GROUP BY merchant_id
),
steps AS (
    SELECT 1 AS step_no, 'Signed up'           AS step, COUNT(*) AS merchants FROM funnel WHERE signed_up  IS NOT NULL
    UNION ALL
    SELECT 2, 'Connected WhatsApp', COUNT(*) FROM funnel WHERE connected    IS NOT NULL
    UNION ALL
    SELECT 3, 'Sent first message', COUNT(*) FROM funnel WHERE first_message IS NOT NULL
    UNION ALL
    SELECT 4, 'Placed first order', COUNT(*) FROM funnel WHERE first_order  IS NOT NULL
)
SELECT
    step_no,
    step,
    merchants,
    ROUND(100.0 * merchants / FIRST_VALUE(merchants) OVER (ORDER BY step_no), 1)                         AS pct_of_signups,
    ROUND(100.0 * merchants / LAG(merchants) OVER (ORDER BY step_no), 1)                                  AS pct_of_previous_step,
    LAG(merchants) OVER (ORDER BY step_no) - merchants                                                    AS dropped_off
FROM steps
ORDER BY step_no;

-- ---------------------------------------------------------------------------
-- Same funnel, split by business_type, so you can see which segments
-- struggle to get from "connected" to "first order".
-- ---------------------------------------------------------------------------
WITH funnel AS (
    SELECT
        me.merchant_id,
        m.business_type,
        MAX(event_date) FILTER (WHERE event = 'signed_up')          AS signed_up,
        MAX(event_date) FILTER (WHERE event = 'connected_whatsapp') AS connected,
        MAX(event_date) FILTER (WHERE event = 'first_message')      AS first_message,
        MAX(event_date) FILTER (WHERE event = 'first_order')        AS first_order
    FROM merchant_events me
    JOIN merchants m USING (merchant_id)
    GROUP BY me.merchant_id, m.business_type
)
SELECT
    business_type,
    COUNT(*)                                        AS signed_up,
    COUNT(connected)                                 AS connected,
    COUNT(first_message)                             AS first_message,
    COUNT(first_order)                               AS first_order,
    ROUND(100.0 * COUNT(first_order) / COUNT(*), 1)  AS pct_signup_to_order
FROM funnel
GROUP BY business_type
ORDER BY pct_signup_to_order DESC;
