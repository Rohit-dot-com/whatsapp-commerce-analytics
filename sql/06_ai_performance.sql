-- =============================================================================
-- 06_ai_performance.sql
-- AI agent performance: confidence, hand-off rate, and response time by intent.
-- Response time is derived, not stored: it is the gap between a customer
-- message and the next reply (ai or merchant) in the same conversation.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Response time per reply, using LAG() over each conversation.
--    A "reply" is any ai/merchant message that immediately follows a
--    customer message. wait_seconds = time since that customer message.
-- ---------------------------------------------------------------------------
WITH ordered AS (
    SELECT
        message_id,
        conversation_id,
        merchant_id,
        intent,
        sender,
        sent_at,
        ai_confidence,
        handed_to_human,
        LAG(sender)  OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sender,
        LAG(sent_at) OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sent_at
    FROM messages
),
replies AS (
    SELECT
        conversation_id,
        merchant_id,
        intent,
        sender,
        ai_confidence,
        handed_to_human,
        EXTRACT(EPOCH FROM (sent_at - prev_sent_at)) AS wait_seconds
    FROM ordered
    WHERE sender <> 'customer'
      AND prev_sender = 'customer'
)
SELECT
    intent,
    COUNT(*) FILTER (WHERE sender = 'ai')                                    AS ai_replies,
    ROUND(AVG(ai_confidence) FILTER (WHERE sender = 'ai')::numeric, 3)       AS avg_ai_confidence,
    ROUND(AVG(wait_seconds)  FILTER (WHERE sender = 'ai')::numeric, 1)       AS avg_ai_wait_sec,
    ROUND(
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wait_seconds) FILTER (WHERE sender = 'ai')::numeric, 1
    )                                                                        AS median_ai_wait_sec
FROM replies
GROUP BY intent
ORDER BY avg_ai_confidence DESC;

-- ---------------------------------------------------------------------------
-- 2. Hand-off rate by intent: share of conversations where the AI escalated
--    to a human at least once. One row per conversation (not per message).
-- ---------------------------------------------------------------------------
WITH conv_intent AS (
    -- a conversation's intent = the intent of its first message
    SELECT DISTINCT ON (conversation_id)
        conversation_id, merchant_id, intent
    FROM messages
    ORDER BY conversation_id, sent_at
),
conv_handoff AS (
    SELECT conversation_id, BOOL_OR(handed_to_human) AS handed_off
    FROM messages
    GROUP BY conversation_id
)
SELECT
    ci.intent,
    COUNT(*)                                                        AS conversations,
    COUNT(*) FILTER (WHERE ch.handed_off)                           AS handed_off,
    ROUND(100.0 * COUNT(*) FILTER (WHERE ch.handed_off) / COUNT(*), 1) AS handoff_rate_pct
FROM conv_intent ci
JOIN conv_handoff ch USING (conversation_id)
GROUP BY ci.intent
ORDER BY handoff_rate_pct DESC;

-- ---------------------------------------------------------------------------
-- 3. Merchant-level AI scorecard: useful for a "worst-performing merchants"
--    table on the dashboard (low confidence + high hand-off = needs review).
-- ---------------------------------------------------------------------------
WITH ordered AS (
    SELECT
        message_id, conversation_id, merchant_id, sender, sent_at, ai_confidence, handed_to_human,
        LAG(sender)  OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sender,
        LAG(sent_at) OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sent_at
    FROM messages
),
ai_replies AS (
    SELECT merchant_id, ai_confidence, handed_to_human,
           EXTRACT(EPOCH FROM (sent_at - prev_sent_at)) AS wait_seconds
    FROM ordered
    WHERE sender = 'ai' AND prev_sender = 'customer'
)
SELECT
    merchant_id,
    COUNT(*)                                          AS ai_replies,
    ROUND(AVG(ai_confidence)::numeric, 3)              AS avg_confidence,
    ROUND(100.0 * AVG(handed_to_human::int), 1)        AS handoff_rate_pct,
    ROUND(AVG(wait_seconds)::numeric, 1)               AS avg_wait_sec
FROM ai_replies
GROUP BY merchant_id
HAVING COUNT(*) >= 20                                   -- enough volume to be meaningful
ORDER BY avg_confidence ASC, handoff_rate_pct DESC
LIMIT 15;
