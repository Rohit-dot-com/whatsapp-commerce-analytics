#!/usr/bin/env python3
"""
generate_data.py
----------------
Synthetic dataset for a WhatsApp-commerce analytics project
(merchant funnel, cohort retention, AI-agent performance, order fulfillment).

ALL DATA IS SYNTHETIC. Numbers are illustrative, not real business results.

Tables written to ./data/ (CSV):
    merchants        one row per merchant
    customers        end-customers of each merchant
    messages         every WhatsApp message (customer / ai / merchant)
    orders           orders created from conversations
    merchant_events  activation milestones (signed_up -> ... -> first_order)

Patterns deliberately baked in (so the analysis has something to find):
    1. Merchants whose first order comes within 3 days of signup retain far better.
    2. Retention/churn differs by business type and plan.
    3. The AI is more confident on order intents than on complaints,
       and low-confidence / complaint chats are handed to a human more often.
    4. Slow responses (esp. human hand-offs) lead to more cancelled orders.
    5. Weekend volume is higher; message traffic peaks late morning and evening.

Usage:
    pip install numpy pandas
    python generate_data.py                    # defaults
    python generate_data.py --merchants 800 --seed 7 --out data
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# =============================================================================
# CONFIG  (tweak these to change the shape of the data)
# =============================================================================
SEED = 42
N_MERCHANTS = 600
START_DATE = pd.Timestamp("2025-10-01")      # first possible signup
SNAPSHOT_DATE = pd.Timestamp("2026-09-20")   # "today" inside the dataset
SNAPSHOT_TS = SNAPSHOT_DATE + pd.Timedelta(days=1)

# Activation funnel probabilities (each is conditional on the previous step)
P_CONNECT = 0.88     # signed up -> connected WhatsApp
P_MESSAGE = 0.90     # connected -> first message
P_ORDER = 0.72       # first message -> first order
P_FAST = 0.40        # share of ordering merchants whose first order is <= 3 days after signup

# Lifetime multipliers: the "first order within 3 days" effect
FAST_LIFE_MULT = 2.0
SLOW_LIFE_MULT = 0.7

BASE_RATE = 1.5      # baseline conversations per active merchant per day
REPEAT_P = 0.50      # chance a conversation comes from a returning customer

# Cancellation model: p = BASE + SLOW * (1 - exp(-worst_wait_minutes / 30))
P_CANCEL_BASE = 0.07
P_CANCEL_SLOW = 0.30

INTENTS = ["order", "query", "appointment", "complaint"]
MIX_STANDARD = [0.55, 0.24, 0.08, 0.13]
MIX_APPT_HEAVY = [0.20, 0.25, 0.45, 0.10]

BUSINESS_TYPES = {
    #                share  mean_life_days  avg_order_inr  volume  delivery_median_min  intent_mix
    "grocery":     dict(share=0.22, life=150, aov=850,  vol=1.2, deliv=45,   mix=MIX_STANDARD),
    "bakery":      dict(share=0.14, life=120, aov=700,  vol=0.9, deliv=50,   mix=MIX_STANDARD),
    "restaurant":  dict(share=0.16, life=70,  aov=520,  vol=1.6, deliv=35,   mix=MIX_STANDARD),
    "clothing":    dict(share=0.14, life=90,  aov=1800, vol=0.7, deliv=90,   mix=MIX_STANDARD),
    "pharmacy":    dict(share=0.10, life=170, aov=480,  vol=1.0, deliv=40,   mix=MIX_STANDARD),
    "salon":       dict(share=0.10, life=130, aov=1100, vol=0.8, deliv=None, mix=MIX_APPT_HEAVY),
    "electronics": dict(share=0.08, life=60,  aov=4200, vol=0.5, deliv=180,  mix=MIX_STANDARD),
    "tutoring":    dict(share=0.06, life=100, aov=1500, vol=0.5, deliv=None, mix=MIX_APPT_HEAVY),
}

PLANS = {
    "free":    dict(share=0.45, rate=0.7, life=0.8),
    "starter": dict(share=0.40, rate=1.0, life=1.0),
    "pro":     dict(share=0.15, rate=1.8, life=1.4),
}

CITIES = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Pune", "Ahmedabad", "Jaipur",
          "Lucknow", "Chennai", "Kolkata", "Indore", "Chandigarh", "Kanpur", "Meerut", "Bareilly"]
CITY_W = np.array([10, 10, 10, 8, 8, 7, 6, 6, 6, 6, 5, 4, 5, 4, 3], dtype=float)
CITY_P = CITY_W / CITY_W.sum()

# Hour-of-day traffic shape (IST): morning bump and an evening peak
HOUR_W = np.array([1, 1, 1, 1, 1, 2, 4, 6, 8, 10, 12, 12,
                   11, 9, 8, 8, 9, 11, 13, 13, 12, 9, 5, 2], dtype=float)
HOUR_P = HOUR_W / HOUR_W.sum()

# AI confidence ~ Beta(a, b): high for orders, lower for complaints
AI_CONF_BETA = {"order": (9, 1.3), "query": (7, 1.5), "appointment": (8, 1.6), "complaint": (4, 2.4)}
TURNS = {"order": (2, 5), "query": (1, 3), "appointment": (2, 4), "complaint": (2, 5)}  # customer<->AI exchanges
P_HANDOFF = {"order": 0.06, "query": 0.05, "appointment": 0.05, "complaint": 0.20}
P_SLOW_AI = 0.06     # occasional slow AI reply (degraded / queued)


# =============================================================================
# 1. MERCHANTS + ACTIVATION PLAN
# =============================================================================
def plan_activation(rng, signup):
    """Decide how far a merchant gets in the funnel and when. Returns 3 dates (or NaT)."""
    conn = rng.random() < P_CONNECT
    msg = conn and rng.random() < P_MESSAGE
    order = msg and rng.random() < P_ORDER
    c = m = d = None
    if order:
        d = int(rng.integers(0, 4)) if rng.random() < P_FAST else 4 + int(rng.exponential(9))
        c = min(d, int(rng.exponential(1.0)))
        m = min(d, c + int(rng.exponential(1.5)))
    elif msg:
        c = int(rng.exponential(1.5))
        m = c + int(rng.exponential(2.0))
    elif conn:
        c = int(rng.exponential(1.5))

    out = []
    for k in (c, m, d):
        dt = signup + pd.Timedelta(days=k) if k is not None else pd.NaT
        # anything not strictly before the snapshot date "hasn't happened yet"
        out.append(dt if (pd.notna(dt) and dt < SNAPSHOT_DATE) else pd.NaT)
    return out


def build_merchants(rng, n):
    types = list(BUSINESS_TYPES)
    plans = list(PLANS)
    btype = rng.choice(types, size=n, p=[BUSINESS_TYPES[t]["share"] for t in types])
    plan = rng.choice(plans, size=n, p=[PLANS[p]["share"] for p in plans])
    city = rng.choice(CITIES, size=n, p=CITY_P)

    # Signups skew later in time => a growing startup
    span = (SNAPSHOT_DATE - START_DATE).days - 14
    offsets = (span * rng.beta(1.6, 1.0, size=n)).astype(int)
    signup = START_DATE + pd.to_timedelta(offsets, unit="D")

    df = pd.DataFrame({"signup_date": signup, "business_type": btype, "plan": plan, "city": city})
    df = df.sort_values("signup_date").reset_index(drop=True)
    df.insert(0, "merchant_id", np.arange(1, n + 1))

    plans_out = [plan_activation(rng, s) for s in df["signup_date"]]
    df["connect_date"] = [p[0] for p in plans_out]
    df["first_message_date"] = [p[1] for p in plans_out]
    df["first_order_date"] = [p[2] for p in plans_out]
    df["fast_activated"] = (df["first_order_date"] - df["signup_date"]).dt.days <= 3
    df["rate_mult"] = rng.lognormal(0, 0.45, size=n)   # some merchants are simply busier
    return df


# =============================================================================
# 2. CONVERSATION SCHEDULE (who talks to whom, and when)
# =============================================================================
def merchant_conversations(rng, m):
    """Conversation start-days + intents for one merchant (times of day are added later)."""
    t = BUSINESS_TYPES[m.business_type]
    p = PLANS[m.plan]
    days, intents, force = [], [], []
    non_order = ["query", "appointment", "complaint"]

    if pd.notna(m.first_order_date):
        # a few pre-order chats between first message and first order
        if m.first_message_date < m.first_order_date:
            gap = (m.first_order_date - m.first_message_date).days
            n_pre = int(rng.integers(1, 4))
            offs = [0] + [int(rng.integers(0, gap)) for _ in range(n_pre - 1)]
            for o in offs:
                days.append(m.first_message_date + pd.Timedelta(days=o))
                intents.append(rng.choice(non_order, p=[0.6, 0.2, 0.2]))
                force.append(False)

        # active period: from first order until churn (or the snapshot)
        life_mean = t["life"] * p["life"] * (FAST_LIFE_MULT if m.fast_activated else SLOW_LIFE_MULT)
        life = 7 + rng.exponential(life_mean)
        end = min(m.first_order_date + pd.Timedelta(days=int(life)), SNAPSHOT_DATE)
        idx = pd.date_range(m.first_order_date, end, freq="D")
        tt = np.arange(len(idx))
        lam = (BASE_RATE * t["vol"] * p["rate"] * m.rate_mult
               * np.where(idx.dayofweek >= 5, 1.25, 1.0)      # weekend bump
               * np.clip(1 - 0.5 * tt / life, 0.3, 1.0))      # slow fade before churn
        counts = rng.poisson(lam)
        counts[0] = 0                                          # day 0 = the guaranteed first order
        days.append(m.first_order_date)
        intents.append("order")
        force.append(True)
        rep = np.repeat(idx, counts)
        if len(rep):
            days.extend(list(rep))
            intents.extend(rng.choice(INTENTS, size=len(rep), p=t["mix"]))
            force.extend([False] * len(rep))

    elif pd.notna(m.first_message_date):
        # sent a few messages, never placed an order
        n = int(rng.integers(1, 6))
        span = int(rng.integers(1, 8))
        offs = np.concatenate([[0], rng.integers(0, span, n - 1)])
        for o in offs:
            d = min(m.first_message_date + pd.Timedelta(days=int(o)), SNAPSHOT_DATE - pd.Timedelta(days=1))
            days.append(d)
            intents.append(rng.choice(non_order, p=[0.6, 0.2, 0.2]))
            force.append(False)

    if not days:
        return None
    return pd.DataFrame({"merchant_id": m.merchant_id, "day": pd.to_datetime(days),
                         "intent": intents, "force_order": force})


def build_conversations(rng, merchants):
    parts = [merchant_conversations(rng, m) for m in merchants.itertuples()]
    convs = pd.concat([p for p in parts if p is not None], ignore_index=True)
    hours = rng.choice(24, size=len(convs), p=HOUR_P)
    secs = rng.integers(0, 3600, size=len(convs))
    convs["start_ts"] = convs["day"] + pd.to_timedelta(hours * 3600 + secs, unit="s")
    convs = convs.sort_values("start_ts").reset_index(drop=True)
    convs.insert(0, "conversation_id", np.arange(1, len(convs) + 1))
    return convs.drop(columns="day")


# =============================================================================
# 3. SIMULATE EACH CONVERSATION -> messages (+ maybe an order)
# =============================================================================
def simulate_conversation(rng, intent, force_order):
    """Times are seconds since the conversation started."""
    a, b = AI_CONF_BETA[intent]
    n_turns = int(rng.integers(*TURNS[intent]))
    t = 0.0
    msgs, waits, confs = [], [], []
    handoff = False

    for turn in range(n_turns):
        if turn > 0:
            t += rng.lognormal(np.log(45), 0.7)            # customer typing gap
        msgs.append((t, "customer", None, False))

        wait = rng.lognormal(np.log(5), 0.5)                # AI answers in ~5 sec...
        if rng.random() < P_SLOW_AI:
            wait += rng.uniform(45, 600)                    # ...unless it is slow
        t += wait
        conf = float(np.clip(rng.beta(a, b), 0.05, 0.99))
        confs.append(conf)
        waits.append(wait)

        escalate = conf < 0.30 or rng.random() < P_HANDOFF[intent] / n_turns
        msgs.append((t, "ai", conf, escalate))
        if escalate:
            handoff = True
            human_wait = float(np.clip(rng.lognormal(np.log(720), 1.0), 60, 86400))  # median ~12 min
            t += human_wait
            waits.append(human_wait)
            msgs.append((t, "merchant", None, False))
            if rng.random() < 0.5:                          # short follow-up exchange
                t += rng.uniform(30, 300)
                msgs.append((t, "customer", None, False))
                t += human_wait * 0.3
                msgs.append((t, "merchant", None, False))
            break

    order = None
    if intent == "order":
        p_conv = min(0.95, 0.30 + 0.62 * float(np.mean(confs)))
        if handoff:
            p_conv *= 0.8
        if force_order or rng.random() < p_conv:
            worst_min = max(waits) / 60.0
            p_cancel = P_CANCEL_BASE + P_CANCEL_SLOW * (1 - np.exp(-worst_min / 30.0))
            order = dict(offset=t + rng.uniform(20, 300), cancelled=bool(rng.random() < p_cancel))
    return msgs, order


def simulate_all(rng, merchants, convs):
    btype = dict(zip(merchants["merchant_id"], merchants["business_type"]))
    pools = {}                      # merchant_id -> list of customer_ids
    cust_rows, msg_rows, order_rows = [], [], []
    next_cust = 1

    for c in convs.itertuples():
        # ----- pick a customer (new or returning) -----
        pool = pools.setdefault(c.merchant_id, [])
        if pool and rng.random() < REPEAT_P:
            cust = pool[int(rng.integers(len(pool)))]
        else:
            cust = next_cust
            next_cust += 1
            pool.append(cust)
            cust_rows.append((cust, c.merchant_id, c.start_ts.normalize()))

        msgs, order = simulate_conversation(rng, c.intent, c.force_order)
        for off, sender, conf, handoff in msgs:
            msg_rows.append((c.conversation_id, c.merchant_id, cust, c.start_ts, off,
                             sender, c.intent, conf, handoff))

        if order:
            spec = BUSINESS_TYPES[btype[c.merchant_id]]
            created = c.start_ts + pd.Timedelta(seconds=order["offset"])
            age_h = (SNAPSHOT_TS - created).total_seconds() / 3600
            if order["cancelled"]:
                status = "cancelled"
            elif age_h < 1:
                status = "placed"
            elif age_h < 4:
                status = "confirmed"
            else:
                status = "delivered"
            deliv = None
            if status == "delivered" and spec["deliv"] is not None:
                deliv = max(5, int(round(rng.lognormal(np.log(spec["deliv"]), 0.4))))
            amount = max(50, int(round(rng.lognormal(np.log(spec["aov"]), 0.45), -1)))
            order_rows.append((c.conversation_id, c.merchant_id, created, status, amount, deliv))

    customers = pd.DataFrame(cust_rows, columns=["customer_id", "merchant_id", "first_seen_date"])

    messages = pd.DataFrame(msg_rows, columns=["conversation_id", "merchant_id", "customer_id",
                                               "conv_start", "offset_sec", "sender", "intent",
                                               "ai_confidence", "handed_to_human"])
    messages["sent_at"] = (messages["conv_start"] + pd.to_timedelta(messages["offset_sec"], unit="s")).dt.floor("s")
    messages = messages[messages["sent_at"] < SNAPSHOT_TS]        # nothing from the "future"
    messages = messages.sort_values(["sent_at", "conversation_id"]).reset_index(drop=True)
    messages.insert(0, "message_id", np.arange(1, len(messages) + 1))
    messages["ai_confidence"] = messages["ai_confidence"].round(3)
    messages = messages[["message_id", "conversation_id", "merchant_id", "customer_id",
                         "sent_at", "sender", "intent", "ai_confidence", "handed_to_human"]]

    orders = pd.DataFrame(order_rows, columns=["conversation_id", "merchant_id", "created_at",
                                               "status", "amount", "delivery_minutes"])
    orders["created_at"] = orders["created_at"].dt.floor("s")
    orders = orders[orders["created_at"] < SNAPSHOT_TS]
    orders = orders.sort_values("created_at").reset_index(drop=True)
    orders.insert(0, "order_id", np.arange(1, len(orders) + 1))
    orders["delivery_minutes"] = orders["delivery_minutes"].astype("Int64")
    return customers, messages, orders


# =============================================================================
# 4. MERCHANT EVENTS (built from the actual data so everything is consistent)
# =============================================================================
def build_events(merchants, messages, orders):
    ev = [merchants[["merchant_id", "signup_date"]].rename(columns={"signup_date": "event_date"}).assign(event="signed_up"),
          merchants.dropna(subset=["connect_date"])[["merchant_id", "connect_date"]]
                   .rename(columns={"connect_date": "event_date"}).assign(event="connected_whatsapp")]

    fm = messages.groupby("merchant_id")["sent_at"].min().dt.normalize().rename("event_date").reset_index()
    ev.append(fm.assign(event="first_message"))
    fo = orders.groupby("merchant_id")["created_at"].min().dt.normalize().rename("event_date").reset_index()
    ev.append(fo.assign(event="first_order"))

    events = pd.concat(ev, ignore_index=True)
    events["event_date"] = pd.to_datetime(events["event_date"]).dt.date
    return events.sort_values(["merchant_id", "event_date"]).reset_index(drop=True)[["merchant_id", "event", "event_date"]]


# =============================================================================
# 5. SANITY CHECKS (confirm the baked-in patterns are really there)
# =============================================================================
def sanity_report(merchants, customers, messages, orders, events):
    print("\n================ SANITY REPORT ================")
    print(f"merchants: {len(merchants):,} | customers: {len(customers):,} | "
          f"messages: {len(messages):,} | orders: {len(orders):,}")

    f = events.groupby("event")["merchant_id"].nunique()
    order = ["signed_up", "connected_whatsapp", "first_message", "first_order"]
    print("\nFunnel:")
    for i, e in enumerate(order):
        pct = f[e] / f["signed_up"] * 100
        print(f"  {e:<20}{f[e]:>6,}  ({pct:5.1f}% of signups)")

    print("\nOrder status mix (%):")
    print((orders["status"].value_counts(normalize=True) * 100).round(1).to_string())

    # retention: kept ordering >= 60 days after the first order (only merchants old enough)
    g = orders.groupby("merchant_id")["created_at"].agg(["min", "max"])
    g = g.join(merchants.set_index("merchant_id")[["fast_activated", "business_type"]])
    g = g[g["min"] <= SNAPSHOT_TS - pd.Timedelta(days=60)]
    g["kept_60d"] = (g["max"] - g["min"]).dt.days >= 60
    print("\n60-day retention after first order, by activation speed:")
    print((g.groupby("fast_activated")["kept_60d"].mean() * 100).round(1)
          .rename({True: "first order <=3d", False: "first order >3d"}).to_string())
    print("\n60-day retention by business type (%):")
    print((g.groupby("business_type")["kept_60d"].mean() * 100).round(1).sort_values().to_string())

    conv = (messages[messages["sender"] == "ai"]
            .groupby("conversation_id").agg(intent=("intent", "first"), handoff=("handed_to_human", "max")))
    print("\nHuman hand-off rate by intent (%):")
    print((conv.groupby("intent")["handoff"].mean() * 100).round(1).to_string())
    print("\nAvg AI confidence by intent:")
    print(messages[messages["sender"] == "ai"].groupby("intent")["ai_confidence"].mean().round(3).to_string())

    # cancellation vs. worst wait (computed from message gaps, the same way you'd do it in SQL)
    m = messages.sort_values(["conversation_id", "sent_at"]).copy()
    # every AI / merchant message is a "reply": its wait = gap since the previous message
    m["gap"] = (m["sent_at"] - m.groupby("conversation_id")["sent_at"].shift()).dt.total_seconds()
    replies = m[m["sender"] != "customer"].dropna(subset=["gap"])
    worst = replies.groupby("conversation_id")["gap"].max().rename("worst_wait_sec")
    o = orders.join(worst, on="conversation_id").dropna(subset=["worst_wait_sec"])
    o["wait_bucket"] = pd.cut(o["worst_wait_sec"], [-1, 30, 120, 600, 3600, 1e9],
                              labels=["<30s", "30s-2m", "2-10m", "10-60m", ">1h"])
    o["cancelled"] = o["status"].eq("cancelled")
    print("\nCancellation rate by worst response wait (%):")
    print((o.groupby("wait_bucket", observed=True)["cancelled"].mean() * 100).round(1).to_string())
    print("==============================================\n")


# =============================================================================
# MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Generate synthetic WhatsApp-commerce data.")
    ap.add_argument("--merchants", type=int, default=N_MERCHANTS)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", type=str, default="data")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print("Building merchants...")
    merchants = build_merchants(rng, args.merchants)
    print("Scheduling conversations...")
    convs = build_conversations(rng, merchants)
    print(f"Simulating {len(convs):,} conversations...")
    customers, messages, orders = simulate_all(rng, merchants, convs)
    events = build_events(merchants, messages, orders)

    merchants_out = merchants[["merchant_id", "signup_date", "business_type", "city", "plan"]].copy()
    merchants_out["signup_date"] = merchants_out["signup_date"].dt.date
    customers["first_seen_date"] = customers["first_seen_date"].dt.date

    merchants_out.to_csv(out / "merchants.csv", index=False)
    customers.to_csv(out / "customers.csv", index=False)
    messages.to_csv(out / "messages.csv", index=False)
    orders.to_csv(out / "orders.csv", index=False)
    events.to_csv(out / "merchant_events.csv", index=False)
    print(f"Saved 5 CSV files to ./{out}/")

    sanity_report(merchants, customers, messages, orders, events)


if __name__ == "__main__":
    main()
