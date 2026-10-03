-- =============================================================================
-- 01_schema.sql
-- WhatsApp-commerce analytics: PostgreSQL schema (synthetic data)
-- Run from the project root:  psql -d ordflo_analytics -f sql/01_schema.sql
-- Safe to re-run: drops and recreates all tables.
-- =============================================================================

DROP TABLE IF EXISTS merchant_events CASCADE;
DROP TABLE IF EXISTS orders          CASCADE;
DROP TABLE IF EXISTS messages        CASCADE;
DROP TABLE IF EXISTS customers       CASCADE;
DROP TABLE IF EXISTS merchants       CASCADE;

-- One row per merchant (a small business using the platform)
CREATE TABLE merchants (
    merchant_id    INTEGER  PRIMARY KEY,
    signup_date    DATE     NOT NULL,
    business_type  TEXT     NOT NULL,
    city           TEXT     NOT NULL,
    plan           TEXT     NOT NULL CHECK (plan IN ('free', 'starter', 'pro'))
);

-- End-customers who chat with a merchant on WhatsApp (belong to one merchant)
CREATE TABLE customers (
    customer_id      INTEGER PRIMARY KEY,
    merchant_id      INTEGER NOT NULL REFERENCES merchants (merchant_id),
    first_seen_date  DATE    NOT NULL
);

-- Every WhatsApp message. A conversation = all messages sharing conversation_id.
--   ai_confidence   : only set for sender = 'ai'
--   handed_to_human : TRUE on the AI message that escalates to the merchant
CREATE TABLE messages (
    message_id       BIGINT       PRIMARY KEY,
    conversation_id  INTEGER      NOT NULL,
    merchant_id      INTEGER      NOT NULL REFERENCES merchants (merchant_id),
    customer_id      INTEGER      NOT NULL REFERENCES customers (customer_id),
    sent_at          TIMESTAMP    NOT NULL,
    sender           TEXT         NOT NULL CHECK (sender IN ('customer', 'ai', 'merchant')),
    intent           TEXT         NOT NULL CHECK (intent IN ('order', 'query', 'appointment', 'complaint')),
    ai_confidence    NUMERIC(4,3) CHECK (ai_confidence BETWEEN 0 AND 1),
    handed_to_human  BOOLEAN      NOT NULL DEFAULT FALSE
);

-- Orders created from conversations (amount in INR)
--   delivery_minutes: NULL when not delivered, or for service businesses (salon, tutoring)
CREATE TABLE orders (
    order_id          INTEGER   PRIMARY KEY,
    conversation_id   INTEGER   NOT NULL,
    merchant_id       INTEGER   NOT NULL REFERENCES merchants (merchant_id),
    created_at        TIMESTAMP NOT NULL,
    status            TEXT      NOT NULL CHECK (status IN ('placed', 'confirmed', 'delivered', 'cancelled')),
    amount            INTEGER   NOT NULL CHECK (amount > 0),
    delivery_minutes  INTEGER
);

-- Activation milestones: signed_up -> connected_whatsapp -> first_message -> first_order
CREATE TABLE merchant_events (
    merchant_id  INTEGER NOT NULL REFERENCES merchants (merchant_id),
    event        TEXT    NOT NULL CHECK (event IN ('signed_up', 'connected_whatsapp', 'first_message', 'first_order')),
    event_date   DATE    NOT NULL,
    PRIMARY KEY (merchant_id, event)
);

-- Indexes for the joins / window functions used in the analysis
CREATE INDEX idx_customers_merchant   ON customers (merchant_id);
CREATE INDEX idx_messages_conv_time   ON messages  (conversation_id, sent_at);
CREATE INDEX idx_messages_merch_time  ON messages  (merchant_id, sent_at);
CREATE INDEX idx_orders_merch_time    ON orders    (merchant_id, created_at);
CREATE INDEX idx_orders_conversation  ON orders    (conversation_id);
