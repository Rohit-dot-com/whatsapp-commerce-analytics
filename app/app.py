"""
WhatsApp Commerce Analytics Dashboard
-------------------------------------
Reads from the PostgreSQL database built by sql/01_schema.sql .. 08_churn.sql
and loaded from generate_data.py's synthetic CSVs.

Run:
    streamlit run app.py

Connection:
    Reads DATABASE_URL from Streamlit secrets (.streamlit/secrets.toml) if
    present, e.g. for a hosted Postgres like Neon or Supabase:
        DATABASE_URL = "postgresql+psycopg2://user:pass@host/dbname?sslmode=require"
    Otherwise falls back to a local database named ordflo_analytics.

NOTE: All data behind this dashboard is synthetic, generated for a portfolio
project. Numbers are illustrative, not real business results.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import create_engine, text

st.set_page_config(page_title="WhatsApp Commerce Analytics", page_icon="\U0001F4AC", layout="wide")

# =============================================================================
# CONNECTION
# =============================================================================
DEFAULT_LOCAL_URL = "postgresql+psycopg2:///ordflo_analytics"


@st.cache_resource
def get_engine():
    url = st.secrets.get("DATABASE_URL", DEFAULT_LOCAL_URL) if hasattr(st, "secrets") else DEFAULT_LOCAL_URL
    return create_engine(url, pool_pre_ping=True)


@st.cache_data(ttl=600)
def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


# Snapshot date: the synthetic dataset's "today". Swap for a live MAX(date)
# query (or CURRENT_DATE) once this points at real, growing data.
SNAPSHOT = "2026-09-20"

# =============================================================================
# QUERIES  (same logic as sql/04_funnel.sql .. 08_churn.sql)
# =============================================================================
Q_FUNNEL = """
WITH funnel AS (
    SELECT
        MAX(event_date) FILTER (WHERE event = 'signed_up')          AS signed_up,
        MAX(event_date) FILTER (WHERE event = 'connected_whatsapp') AS connected,
        MAX(event_date) FILTER (WHERE event = 'first_message')      AS first_message,
        MAX(event_date) FILTER (WHERE event = 'first_order')        AS first_order
    FROM merchant_events GROUP BY merchant_id
), steps AS (
    SELECT 1 step_no, 'Signed up' step, COUNT(*) merchants FROM funnel WHERE signed_up IS NOT NULL
    UNION ALL SELECT 2, 'Connected WhatsApp', COUNT(*) FROM funnel WHERE connected IS NOT NULL
    UNION ALL SELECT 3, 'Sent first message', COUNT(*) FROM funnel WHERE first_message IS NOT NULL
    UNION ALL SELECT 4, 'Placed first order', COUNT(*) FROM funnel WHERE first_order IS NOT NULL
)
SELECT step_no, step, merchants,
       ROUND(100.0 * merchants / FIRST_VALUE(merchants) OVER (ORDER BY step_no), 1) AS pct_of_signups
FROM steps ORDER BY step_no;
"""

Q_FUNNEL_BY_TYPE = """
WITH funnel AS (
    SELECT me.merchant_id, m.business_type,
           MAX(event_date) FILTER (WHERE event = 'signed_up')   AS signed_up,
           MAX(event_date) FILTER (WHERE event = 'first_order') AS first_order
    FROM merchant_events me JOIN merchants m USING (merchant_id)
    GROUP BY me.merchant_id, m.business_type
)
SELECT business_type, COUNT(*) AS signed_up, COUNT(first_order) AS ordered,
       ROUND(100.0 * COUNT(first_order) / COUNT(*), 1) AS pct_signup_to_order
FROM funnel GROUP BY business_type ORDER BY pct_signup_to_order DESC;
"""

Q_COHORT_HEATMAP = """
WITH cohorts AS (
    SELECT merchant_id, DATE_TRUNC('week', signup_date)::date AS cohort_week FROM merchants
), order_weeks AS (
    SELECT DISTINCT merchant_id, DATE_TRUNC('week', created_at)::date AS order_week FROM orders
), activity AS (
    SELECT c.merchant_id, c.cohort_week,
           ((ow.order_week - c.cohort_week) / 7)::int AS week_number
    FROM cohorts c JOIN order_weeks ow ON ow.merchant_id = c.merchant_id
    WHERE ow.order_week >= c.cohort_week
), cohort_sizes AS (
    SELECT cohort_week, COUNT(*) AS cohort_size FROM cohorts GROUP BY cohort_week
)
SELECT cs.cohort_week, cs.cohort_size, a.week_number,
       ROUND(100.0 * COUNT(DISTINCT a.merchant_id) / cs.cohort_size, 1) AS retention_pct
FROM cohort_sizes cs
JOIN activity a ON a.cohort_week = cs.cohort_week
WHERE a.week_number BETWEEN 0 AND 8
  AND cs.cohort_week <= (:snapshot)::date - INTERVAL '8 weeks'
GROUP BY cs.cohort_week, cs.cohort_size, a.week_number
ORDER BY cs.cohort_week, a.week_number;
"""

Q_FAST_ACTIVATION = """
WITH first_order AS (
    SELECT m.merchant_id, m.signup_date, MIN(o.created_at)::date AS first_order_date
    FROM merchants m JOIN orders o USING (merchant_id)
    GROUP BY m.merchant_id, m.signup_date
), labeled AS (
    SELECT merchant_id, (first_order_date - signup_date) <= 3 AS fast_activated
    FROM first_order WHERE signup_date <= (:snapshot)::date - INTERVAL '60 days'
), lifetime AS (
    SELECT merchant_id, MIN(created_at) first_dt, MAX(created_at) last_dt FROM orders GROUP BY merchant_id
)
SELECT l.fast_activated, COUNT(*) merchants,
       ROUND(100.0 * COUNT(*) FILTER (WHERE lt.last_dt - lt.first_dt >= INTERVAL '60 days') / COUNT(*), 1) AS pct_retained_60d
FROM labeled l JOIN lifetime lt USING (merchant_id)
GROUP BY l.fast_activated ORDER BY l.fast_activated DESC;
"""

Q_AI_BY_INTENT = """
WITH ordered AS (
    SELECT conversation_id, sender, sent_at, ai_confidence, intent,
           LAG(sender) OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sender,
           LAG(sent_at) OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sent_at
    FROM messages
), replies AS (
    SELECT intent, sender, ai_confidence,
           EXTRACT(EPOCH FROM (sent_at - prev_sent_at)) AS wait_seconds
    FROM ordered WHERE sender <> 'customer' AND prev_sender = 'customer'
)
SELECT intent,
       COUNT(*) FILTER (WHERE sender = 'ai')                              AS ai_replies,
       ROUND(AVG(ai_confidence) FILTER (WHERE sender = 'ai')::numeric, 3) AS avg_confidence,
       ROUND(AVG(wait_seconds) FILTER (WHERE sender = 'ai')::numeric, 1)  AS avg_wait_sec
FROM replies GROUP BY intent ORDER BY avg_confidence DESC;
"""

Q_HANDOFF_BY_INTENT = """
WITH conv_intent AS (
    SELECT DISTINCT ON (conversation_id) conversation_id, intent
    FROM messages ORDER BY conversation_id, sent_at
), conv_handoff AS (
    SELECT conversation_id, BOOL_OR(handed_to_human) AS handed_off FROM messages GROUP BY conversation_id
)
SELECT ci.intent, COUNT(*) conversations,
       ROUND(100.0 * COUNT(*) FILTER (WHERE ch.handed_off) / COUNT(*), 1) AS handoff_rate_pct
FROM conv_intent ci JOIN conv_handoff ch USING (conversation_id)
GROUP BY ci.intent ORDER BY handoff_rate_pct DESC;
"""

Q_CANCEL_BY_WAIT = """
WITH ordered AS (
    SELECT conversation_id, sender, sent_at,
           LAG(sender) OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sender,
           LAG(sent_at) OVER (PARTITION BY conversation_id ORDER BY sent_at) AS prev_sent_at
    FROM messages
), waits AS (
    SELECT conversation_id, MAX(EXTRACT(EPOCH FROM (sent_at - prev_sent_at))) AS worst_wait_sec
    FROM ordered WHERE sender <> 'customer' AND prev_sender = 'customer'
    GROUP BY conversation_id
), bucketed AS (
    SELECT o.status,
           CASE WHEN w.worst_wait_sec < 30 THEN '1. <30s'
                WHEN w.worst_wait_sec < 120 THEN '2. 30s-2m'
                WHEN w.worst_wait_sec < 600 THEN '3. 2-10m'
                WHEN w.worst_wait_sec < 3600 THEN '4. 10-60m'
                ELSE '5. >1h' END AS wait_bucket
    FROM orders o JOIN waits w USING (conversation_id)
)
SELECT wait_bucket, COUNT(*) orders,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'cancelled') / COUNT(*), 1) AS cancellation_rate_pct
FROM bucketed GROUP BY wait_bucket ORDER BY wait_bucket;
"""

Q_STATUS_BY_TYPE = """
SELECT m.business_type,
       COUNT(*) orders,
       ROUND(100.0 * COUNT(*) FILTER (WHERE o.status = 'delivered') / COUNT(*), 1) AS pct_delivered,
       ROUND(100.0 * COUNT(*) FILTER (WHERE o.status = 'cancelled') / COUNT(*), 1) AS pct_cancelled,
       ROUND(AVG(o.amount)::numeric, 0) AS avg_order_value_inr
FROM orders o JOIN merchants m USING (merchant_id)
GROUP BY m.business_type ORDER BY pct_cancelled DESC;
"""

Q_MONTHLY_REVENUE = """
SELECT DATE_TRUNC('month', created_at)::date AS month,
       COUNT(*) AS delivered_orders, SUM(amount) AS revenue_inr
FROM orders WHERE status = 'delivered' GROUP BY 1 ORDER BY 1;
"""

Q_CHURN_BY_SEGMENT = """
WITH order_history AS (
    SELECT merchant_id, MAX(created_at)::date AS last_order_date FROM orders GROUP BY merchant_id
), flagged AS (
    SELECT m.merchant_id, m.business_type, m.plan,
           oh.last_order_date < (:snapshot)::date - INTERVAL '14 days' AS churned
    FROM merchants m JOIN order_history oh USING (merchant_id)
)
SELECT business_type, plan, COUNT(*) merchants,
       ROUND(100.0 * COUNT(*) FILTER (WHERE churned) / COUNT(*), 1) AS churn_rate_pct
FROM flagged GROUP BY business_type, plan ORDER BY churn_rate_pct DESC;
"""

Q_KPIS = """
SELECT
    (SELECT COUNT(*) FROM merchants)                                   AS total_merchants,
    (SELECT COUNT(*) FROM orders WHERE status <> 'cancelled')          AS total_orders,
    (SELECT SUM(amount) FROM orders WHERE status = 'delivered')        AS total_revenue,
    (SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE status='cancelled') / COUNT(*), 1) FROM orders) AS cancel_rate;
"""

# =============================================================================
# LOAD DATA  (with a friendly error if the DB isn't reachable)
# =============================================================================
try:
    kpis = run_query(Q_KPIS).iloc[0]
    funnel = run_query(Q_FUNNEL)
    funnel_type = run_query(Q_FUNNEL_BY_TYPE)
    cohort = run_query(Q_COHORT_HEATMAP, {"snapshot": SNAPSHOT})
    fast_activation = run_query(Q_FAST_ACTIVATION, {"snapshot": SNAPSHOT})
    ai_intent = run_query(Q_AI_BY_INTENT)
    handoff = run_query(Q_HANDOFF_BY_INTENT)
    cancel_wait = run_query(Q_CANCEL_BY_WAIT)
    status_type = run_query(Q_STATUS_BY_TYPE)
    monthly_rev = run_query(Q_MONTHLY_REVENUE)
    churn_seg = run_query(Q_CHURN_BY_SEGMENT, {"snapshot": SNAPSHOT})
except Exception as e:
    st.error(
        "Could not connect to the database. Make sure PostgreSQL is running and "
        "the `ordflo_analytics` database has been created and loaded "
        "(see sql/01_schema.sql, 02_load.sql). If you're using a hosted database, "
        "set DATABASE_URL in .streamlit/secrets.toml."
    )
    st.exception(e)
    st.stop()

# =============================================================================
# HEADER
# =============================================================================
st.title("\U0001F4AC WhatsApp Commerce Analytics")
st.caption(
    "Portfolio project \u2014 all merchants, customers, messages and orders below are "
    "**synthetic data**, generated to model a WhatsApp-first SMB commerce platform. "
    f"Snapshot date: {SNAPSHOT}."
)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Merchants", f"{int(kpis['total_merchants']):,}")
k2.metric("Orders (non-cancelled)", f"{int(kpis['total_orders']):,}")
k3.metric("Delivered revenue", f"\u20b9{int(kpis['total_revenue']):,}")
k4.metric("Cancellation rate", f"{kpis['cancel_rate']}%")

tab_funnel, tab_retention, tab_ai, tab_fulfillment, tab_churn = st.tabs(
    ["Activation Funnel", "Cohort Retention", "AI Agent Performance", "Order Fulfillment", "Churn"]
)

# ---- Funnel --------------------------------------------------------------
with tab_funnel:
    st.subheader("Merchant activation funnel")
    fig = go.Figure(go.Funnel(y=funnel["step"], x=funnel["merchants"], textinfo="value+percent initial"))
    fig.update_layout(margin=dict(t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Signup \u2192 first order, by business type")
    fig2 = px.bar(
        funnel_type.sort_values("pct_signup_to_order"),
        x="pct_signup_to_order", y="business_type", orientation="h",
        labels={"pct_signup_to_order": "% of signups that placed an order", "business_type": ""},
        text="pct_signup_to_order",
    )
    fig2.update_traces(texttemplate="%{text}%", textposition="outside")
    st.plotly_chart(fig2, use_container_width=True)

# ---- Retention -------------------------------------------------------------
with tab_retention:
    st.subheader("Weekly cohort retention")
    st.caption(
        "Share of each signup-week cohort that placed \u2265 1 order in a given week of their "
        "lifetime. Early cohorts are small, so their retention % can be noisy."
    )
    pivot = cohort.pivot(index="cohort_week", columns="week_number", values="retention_pct")
    pivot = pivot.reindex(sorted(pivot.columns), axis=1)
    pivot.columns = [f"Week {c}" for c in pivot.columns]
    fig3 = px.imshow(
        pivot, text_auto=True, color_continuous_scale="Blues", aspect="auto",
        labels=dict(x="Weeks since signup", y="Signup week (cohort)", color="Retention %"),
    )
    st.plotly_chart(fig3, use_container_width=True)

    st.subheader("Does a fast first order predict retention?")
    fa = fast_activation.copy()
    fa["label"] = fa["fast_activated"].map({True: "First order \u2264 3 days", False: "First order > 3 days"})
    fig4 = px.bar(
        fa, x="label", y="pct_retained_60d", text="pct_retained_60d",
        labels={"label": "", "pct_retained_60d": "60-day retention %"},
        color="label", color_discrete_sequence=["#2E7D32", "#C62828"],
    )
    fig4.update_traces(texttemplate="%{text}%", textposition="outside")
    fig4.update_layout(showlegend=False)
    st.plotly_chart(fig4, use_container_width=True)
    st.info(
        "Merchants who place their first order within 3 days of signup retain far better. "
        "Speeding up time-to-first-order looks like the single highest-leverage onboarding lever."
    )

# ---- AI performance ---------------------------------------------------------
with tab_ai:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("AI confidence & response time by intent")
        st.dataframe(ai_intent, use_container_width=True, hide_index=True)
    with c2:
        st.subheader("Human hand-off rate by intent")
        fig5 = px.bar(
            handoff.sort_values("handoff_rate_pct"), x="handoff_rate_pct", y="intent", orientation="h",
            labels={"handoff_rate_pct": "% of conversations escalated to a human", "intent": ""},
            text="handoff_rate_pct",
        )
        fig5.update_traces(texttemplate="%{text}%", textposition="outside")
        st.plotly_chart(fig5, use_container_width=True)
    st.info(
        "Complaints have the lowest AI confidence and by far the highest hand-off rate. "
        "This is expected \u2014 complaints are less templated \u2014 but it flags where human review "
        "or better training data would help most."
    )

# ---- Fulfillment -------------------------------------------------------------
with tab_fulfillment:
    st.subheader("Monthly delivered revenue")
    fig6 = px.line(monthly_rev, x="month", y="revenue_inr", markers=True,
                    labels={"month": "", "revenue_inr": "Revenue (\u20b9)"})
    st.plotly_chart(fig6, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Cancellation rate by business type")
        st.dataframe(status_type, use_container_width=True, hide_index=True)
    with c2:
        st.subheader("Cancellation rate vs. response wait")
        fig7 = px.bar(cancel_wait, x="wait_bucket", y="cancellation_rate_pct", text="cancellation_rate_pct",
                       labels={"wait_bucket": "Worst response wait in the order's chat",
                               "cancellation_rate_pct": "Cancellation rate %"})
        fig7.update_traces(texttemplate="%{text}%", textposition="outside")
        st.plotly_chart(fig7, use_container_width=True)
    st.info(
        "Cancellation rate climbs sharply once the slowest reply in a chat passes 10 minutes. "
        "Faster AI hand-offs to a human during busy periods should reduce cancellations."
    )

# ---- Churn -------------------------------------------------------------------
with tab_churn:
    st.subheader("Churn rate by business type & plan")
    st.caption("Churned = placed \u2265 1 order before, but none in the last 14 days.")
    fig8 = px.density_heatmap(
        churn_seg, x="plan", y="business_type", z="churn_rate_pct",
        color_continuous_scale="Reds", text_auto=True,
        labels={"churn_rate_pct": "Churn %"},
    )
    st.plotly_chart(fig8, use_container_width=True)
    st.info(
        "Free-plan merchants in restaurant and clothing churn the most. Pharmacy merchants on "
        "paid plans churn the least \u2014 a useful segment to study for what's working."
    )

st.divider()
st.caption(
    "Built with Streamlit, Plotly and PostgreSQL. All underlying SQL lives in the `sql/` folder "
    "of this project's repository. Data is synthetic."
)
