-- =============================================================================
-- 02_load.sql
-- Loads the generated CSVs into PostgreSQL.
-- Run from the PROJECT ROOT (so the relative data/ paths resolve):
--     psql -d ordflo_analytics -f sql/02_load.sql
-- \copy is a psql command: it reads the file on YOUR machine (no superuser needed).
-- Load order matters because of foreign keys.
-- =============================================================================

\echo 'Loading CSVs...'

\copy merchants        FROM 'data/merchants.csv'        WITH (FORMAT csv, HEADER true)
\copy customers        FROM 'data/customers.csv'        WITH (FORMAT csv, HEADER true)
\copy messages         FROM 'data/messages.csv'         WITH (FORMAT csv, HEADER true)
\copy orders           FROM 'data/orders.csv'           WITH (FORMAT csv, HEADER true)
\copy merchant_events  FROM 'data/merchant_events.csv'  WITH (FORMAT csv, HEADER true)

-- Refresh planner statistics so queries are fast
ANALYZE;

\echo 'Row counts:'
SELECT 'merchants' AS table_name, COUNT(*) AS row_count FROM merchants
UNION ALL SELECT 'customers',       COUNT(*) FROM customers
UNION ALL SELECT 'messages',        COUNT(*) FROM messages
UNION ALL SELECT 'orders',          COUNT(*) FROM orders
UNION ALL SELECT 'merchant_events', COUNT(*) FROM merchant_events;
