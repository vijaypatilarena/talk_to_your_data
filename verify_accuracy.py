"""
Quick accuracy check after the order_status_code NULL fix.
Computes ground-truth from Postgres directly, runs the agent on the
same questions, then compares.
"""

import psycopg2
from decimal import Decimal
from app.config import config
from app.agent.agent_runner import run_agent


VKORG = config.ACTIVE_TENANT_VKORG


# ---- ground truth helpers (direct SQL) ----------------------------------

def db_query(sql: str):
    conn = psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT,
        dbname=config.DB_NAME, user=config.DB_USER,
        password=config.DB_PASSWORD,
    )
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def gt_q1_2025_avg():
    return db_query(f"""
      SELECT
        ROUND(AVG(CASE WHEN document_category_code IN ('C','L') THEN net_value
                       WHEN document_category_code IN ('K','H') THEN -net_value
                       ELSE 0 END), 2) AS avg_order_value,
        COUNT(*) AS total_orders,
        ROUND(SUM(CASE WHEN document_category_code IN ('C','L') THEN net_value
                       WHEN document_category_code IN ('K','H') THEN -net_value
                       ELSE 0 END), 2) AS total_revenue
      FROM order_headers
      WHERE sales_org_code = '{VKORG}'
        AND COALESCE(order_status_code, '') NOT IN ('CANCELLED','REJECTED')
        AND order_date >= '2025-01-01'
        AND order_date <  '2025-04-01';
    """)[0]


def gt_total_revenue_2025():
    return db_query(f"""
      SELECT
        ROUND(SUM(CASE WHEN document_category_code IN ('C','L') THEN net_value
                       WHEN document_category_code IN ('K','H') THEN -net_value
                       ELSE 0 END), 2) AS total_revenue
      FROM order_headers
      WHERE sales_org_code = '{VKORG}'
        AND COALESCE(order_status_code, '') NOT IN ('CANCELLED','REJECTED')
        AND order_date >= '2025-01-01'
        AND order_date <  '2026-01-01';
    """)[0]


def gt_health_breakdown():
    return db_query(f"""
      SELECT c.health_status_code, COUNT(DISTINCT c.id)
      FROM customers c
      JOIN order_headers oh ON oh.customer_id = c.id
      WHERE oh.sales_org_code = '{VKORG}'
      GROUP BY c.health_status_code
      ORDER BY c.health_status_code;
    """)


def gt_top5_by_spend():
    return db_query(f"""
      SELECT c.customer_name, c.rolling_12m_spend
      FROM customers c
      JOIN order_headers oh ON oh.customer_id = c.id
      WHERE oh.sales_org_code = '{VKORG}'
      GROUP BY c.id, c.customer_name, c.rolling_12m_spend
      ORDER BY c.rolling_12m_spend DESC NULLS LAST
      LIMIT 5;
    """)


# ---- runner --------------------------------------------------------------

CHECKS = [
    {
        "name":      "Q1 2025 average order value",
        "question":  "Can you show me avg sales for Q1 of 2025",
        "ground":    gt_q1_2025_avg,
        "expect_hits": ["9961", "9,961", "368"],   # avg ≈ 368.7, count = 9961
    },
    {
        "name":      "Total revenue 2025",
        "question":  "What is the total revenue for the year 2025?",
        "ground":    gt_total_revenue_2025,
        "expect_hits": [],   # we'll just print and eyeball
    },
    {
        "name":      "Customer health breakdown",
        "question":  "Give me a breakdown of customers by health status",
        "ground":    gt_health_breakdown,
        "expect_hits": ["CRITICAL", "HEALTHY"],
    },
    {
        "name":      "Top 5 customers by spend",
        "question":  "Who are our top 5 customers by spend?",
        "ground":    gt_top5_by_spend,
        "expect_hits": [],
    },
]


def main():
    print(f"=== Accuracy check (tenant {VKORG}) ===\n")
    for check in CHECKS:
        print("-" * 72)
        print(f"CHECK: {check['name']}")
        print(f"Q:     {check['question']}")
        print()

        truth = check["ground"]()
        print(f"GROUND TRUTH:")
        if isinstance(truth, list):
            for r in truth:
                print(f"   {r}")
        else:
            print(f"   {truth}")
        print()

        print("AGENT...")
        result = run_agent(user_question=check["question"], tenant_vkorg=VKORG)
        ans = (result.answer or "").strip()
        print(f"AGENT ANSWER:")
        print("   " + ans.replace("\n", "\n   ")[:1500])
        print()

        if check["expect_hits"]:
            missing = [s for s in check["expect_hits"] if s not in ans]
            verdict = "✅ PASS" if not missing else f"❌ MISSING: {missing}"
            print(f"   Verdict: {verdict}")
        print()


if __name__ == "__main__":
    main()
