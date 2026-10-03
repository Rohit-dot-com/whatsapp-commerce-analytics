-- =============================================================================
-- 03_validate.sql
-- Data-quality checks. Every "problems" value should be 0.
--     psql -d ordflo_analytics -f sql/03_validate.sql
-- =============================================================================

-- 1. Orders must point at a conversation that exists in messages
SELECT 'orders without messages' AS check_name, COUNT(*) AS problems
FROM orders o
WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = o.conversation_id)

UNION ALL
-- 2. Funnel events must be in order for each merchant
SELECT 'events out of order', COUNT(*)
FROM (
    SELECT merchant_id,
           MAX(event_date) FILTER (WHERE event = 'signed_up')          AS signed_up,
           MAX(event_date) FILTER (WHERE event = 'connected_whatsapp') AS connected,
           MAX(event_date) FILTER (WHERE event = 'first_message')      AS first_msg,
           MAX(event_date) FILTER (WHERE event = 'first_order')        AS first_ord
    FROM merchant_events
    GROUP BY merchant_id
) e
WHERE connected < signed_up OR first_msg < connected OR first_ord < first_msg

UNION ALL
-- 3. Messages before the merchant signed up
SELECT 'messages before signup', COUNT(*)
FROM messages m
JOIN merchants mr USING (merchant_id)
WHERE m.sent_at::date < mr.signup_date

UNION ALL
-- 4. AI confidence should exist on AI messages only
SELECT 'ai_confidence misuse', COUNT(*)
FROM messages
WHERE (sender = 'ai') <> (ai_confidence IS NOT NULL)

UNION ALL
-- 5. Delivered orders that should have a delivery time but don't
--    (service businesses legitimately have NULL delivery_minutes)
SELECT 'delivered w/o delivery_minutes (non-service)', COUNT(*)
FROM orders o
JOIN merchants m USING (merchant_id)
WHERE o.status = 'delivered'
  AND o.delivery_minutes IS NULL
  AND m.business_type NOT IN ('salon', 'tutoring')

UNION ALL
-- 6. Duplicate conversations spread across different customers
SELECT 'conversation with >1 customer', COUNT(*)
FROM (
    SELECT conversation_id
    FROM messages
    GROUP BY conversation_id
    HAVING COUNT(DISTINCT customer_id) > 1
) x;
