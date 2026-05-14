from app.config import config

SCHEMA_DESCRIPTION = """
You are querying a B2B distributor PostgreSQL database.
Active tenant VKORG: '{vkorg}' — EVERY query touching order_headers MUST filter by sales_org_code = '{vkorg}'.
 
== TABLE: companies (4 rows) ==
  id UUID, name TEXT, code TEXT, default_currency CHAR(3),
  timezone TEXT, vkorg_codes JSONB, order_book_target NUMERIC
 
== TABLE: customers (6,787 rows) ==
  id UUID PRIMARY KEY
  external_id TEXT                      -- SAP customer number
  customer_name TEXT
  city TEXT, region TEXT, country_code CHAR(2)
  account_owner_name TEXT               -- Sales rep
  credit_limit NUMERIC
  customer_since DATE
  company_id UUID                       -- FK to companies.id
  -- Health status
  health_status_code TEXT               -- HEALTHY | EARLY_WARNING | AT_RISK | CRITICAL | INACTIVE
  impact_level_code TEXT                -- HIGH | MEDIUM | LOW
  requires_attention BOOLEAN
  -- Pre-computed spend metrics
  rolling_12m_spend NUMERIC             -- Total spend last 12 months EUR
  rolling_12m_order_count INTEGER
  previous_12m_spend NUMERIC            -- Spend 12-24 months ago
  average_order_value NUMERIC
  average_order_interval_days NUMERIC
  last_order_date DATE
  days_since_last_order INTEGER
  days_overdue INTEGER
  spend_change_percentage NUMERIC       -- YoY spend change %
  lifetime_order_count INTEGER
 
== TABLE: order_headers (174,952 rows) ==
  id UUID PRIMARY KEY
  external_order_id TEXT
  customer_id UUID                      -- FK to customers.id
  order_date DATE
  delivery_date DATE
  net_value NUMERIC                     -- Order value EUR
  currency_code CHAR(3)                 -- Always EUR
  order_status_code TEXT                -- OPEN | DELIVERED | CANCELLED | REJECTED
  sales_org_code TEXT                   -- *** TENANT FILTER — ALWAYS FILTER BY THIS ***
  document_category_code CHAR(1)        -- C/L = revenue, K/H = deductions
  order_type_code TEXT
  distribution_channel TEXT
  

  
  REVENUE SIGN CONVENTION (CRITICAL):
    document_category_code IN ('C','L') → ADD to revenue
    document_category_code IN ('K','H') → SUBTRACT from revenue
  Always exclude cancelled/rejected orders. order_status_code MAY BE NULL — treat
  NULL as a valid (non-cancelled) order. Use COALESCE so NULLs are not silently dropped:
    COALESCE(order_status_code, '') NOT IN ('CANCELLED','REJECTED')

  Canonical revenue formula:
    SUM(CASE WHEN document_category_code IN ('C','L') THEN net_value
             WHEN document_category_code IN ('K','H') THEN -net_value
             ELSE 0 END)
    WHERE sales_org_code = '{vkorg}'
    AND COALESCE(order_status_code, '') NOT IN ('CANCELLED','REJECTED')
 
== TABLE: order_lines (268,880 rows) ==
  id UUID, order_header_id UUID         -- FK to order_headers.id
  line_number INTEGER
  material_number TEXT                  -- Product code
  material_description TEXT             -- Product name (may be German/French/Dutch)
  material_group_code TEXT
  quantity NUMERIC, unit_of_measure TEXT
  unit_price NUMERIC, net_value NUMERIC
  plant_code TEXT
  delivery_status_code TEXT             -- DELIVERED | PENDING | PARTIAL | CANCELLED
  NOTE: No sales_org_code here — JOIN through order_headers to filter by tenant
 
== TABLE: daily_metrics_snapshots (280 rows — pre-aggregated) ==
  id UUID, snapshot_date DATE
  company_id UUID                       -- FK to companies.id
  order_book_value NUMERIC              -- Rolling 12-month
  order_book_value_month/quarter/year NUMERIC
  avg_order_value_month/quarter/year NUMERIC
  order_velocity_month/quarter/year NUMERIC
  revenue_at_risk_12m NUMERIC
  healthy_count, early_warning_count, at_risk_count, critical_count, inactive_count INTEGER
  total_customer_count INTEGER
  customers_needing_attention INTEGER
  value_at_stake NUMERIC
  overdue_count, declining_spend_count INTEGER
 
== KEY JOIN PATTERNS ==
 
-- Tenant customers:
SELECT DISTINCT c.* FROM customers c
JOIN order_headers oh ON oh.customer_id = c.id
WHERE oh.sales_org_code = '{vkorg}'
 
-- Tenant order lines:
SELECT ol.* FROM order_lines ol
JOIN order_headers oh ON oh.id = ol.order_header_id
WHERE oh.sales_org_code = '{vkorg}'
 
-- Latest KPI snapshot:
SELECT dms.* FROM daily_metrics_snapshots dms
JOIN companies co ON co.id = dms.company_id
WHERE co.vkorg_codes::text LIKE '%{vkorg}%'
ORDER BY dms.snapshot_date DESC LIMIT 1
 
== QUERY ROUTING ==
Use daily_metrics_snapshots for: current order book, health counts, KPI summaries
Use order_headers + customers for: trends, date ranges, specific analysis, product questions

== GOTCHAS (READ CAREFULLY) ==
1. customers ⨝ order_headers FAN-OUT:
   The join customers ⨝ order_headers multiplies each customer row by their
   order count. NEVER use COUNT(*) over this join when counting CUSTOMERS —
   it counts orders, not customers.
     ❌ WRONG: SELECT health_status_code, COUNT(*) FROM customers c
               JOIN order_headers oh ON oh.customer_id = c.id ...
     ✅ RIGHT: SELECT health_status_code, COUNT(DISTINCT c.id) FROM customers c
               JOIN order_headers oh ON oh.customer_id = c.id ...
   Same rule applies to SUM/AVG over customer-level columns (rolling_12m_spend,
   credit_limit, etc.) — wrap with DISTINCT or aggregate after a subquery that
   first dedupes to one row per customer.

2. For any "health status / segment counts" question, PREFER daily_metrics_snapshots
   (it already has healthy_count, at_risk_count, critical_count, etc. precomputed
   per tenant, no fan-out risk). Only fall back to customers when the snapshot
   does not have the needed dimension.

3. order_status_code is often NULL — see SQL rule #7 for the COALESCE pattern.

4. "TOP N + TIME TREND" questions return ONE ROW PER ENTITY — not one row per
   (entity, period). Common failure mode: agent does GROUP BY (customer, month)
   which gives N × 12 rows (e.g. 108-120 rows for "top 10 + monthly trend over
   12 months"). The user wanted a clean 10-row summary, not a long flat table.

   ✅ RIGHT pattern:
     Step A — CTE finds the top N entities (LIMIT inside the CTE).
     Step B — Second CTE joins those entities to order_headers and groups by
              (entity, month) to get monthly values.
     Step C — Third CTE uses a window function (LAG OVER PARTITION BY entity
              ORDER BY month) to compute period-over-period change.
     Step D — Final SELECT aggregates back to ONE row per entity with summary
              trend columns (avg_mom_change_pct, total_spend, etc.).

   ❌ WRONG pattern (the failure case):
     SELECT customer_name, month, spend FROM ... GROUP BY customer_name, month
     — returns 10 × 12 = 120 rows, not a per-customer summary.
"""
 
EXAMPLE_QUERIES = """
== EXAMPLE SQL ==
 
-- Top 5 customers by spend:
SELECT c.customer_name, c.rolling_12m_spend, c.health_status_code
FROM customers c
JOIN order_headers oh ON oh.customer_id = c.id
WHERE oh.sales_org_code = '{vkorg}'
GROUP BY c.id, c.customer_name, c.rolling_12m_spend, c.health_status_code
ORDER BY c.rolling_12m_spend DESC LIMIT 5;
 
-- Customers not ordered in 60+ days:
SELECT c.customer_name, c.last_order_date, c.days_since_last_order
FROM customers c
JOIN order_headers oh ON oh.customer_id = c.id
WHERE oh.sales_org_code = '{vkorg}' AND c.days_since_last_order > 60
GROUP BY c.id, c.customer_name, c.last_order_date, c.days_since_last_order
ORDER BY c.days_since_last_order DESC;
 
-- Total revenue this year:
SELECT SUM(CASE WHEN document_category_code IN ('C','L') THEN net_value
                WHEN document_category_code IN ('K','H') THEN -net_value
                ELSE 0 END) AS total_revenue
FROM order_headers
WHERE sales_org_code = '{vkorg}'
AND COALESCE(order_status_code, '') NOT IN ('CANCELLED','REJECTED')
AND order_date >= DATE_TRUNC('year', CURRENT_DATE);
 
-- Current KPIs from snapshot:
SELECT customers_needing_attention, value_at_stake, total_customer_count,
       healthy_count, at_risk_count, critical_count
FROM daily_metrics_snapshots dms
JOIN companies co ON co.id = dms.company_id
WHERE co.vkorg_codes::text LIKE '%{vkorg}%'
ORDER BY dms.snapshot_date DESC LIMIT 1;

-- Customer breakdown by health status (PREFERRED: snapshot — no fan-out risk):
SELECT healthy_count, early_warning_count, at_risk_count,
       critical_count, inactive_count, total_customer_count
FROM daily_metrics_snapshots dms
JOIN companies co ON co.id = dms.company_id
WHERE co.vkorg_codes::text LIKE '%{vkorg}%'
ORDER BY dms.snapshot_date DESC LIMIT 1;

-- Customer breakdown by health status (FALLBACK when snapshot dim unavailable —
-- note COUNT(DISTINCT c.id) is mandatory because the join fans out per order):
SELECT c.health_status_code, COUNT(DISTINCT c.id) AS customer_count
FROM customers c
JOIN order_headers oh ON oh.customer_id = c.id
WHERE oh.sales_org_code = '{vkorg}'
GROUP BY c.health_status_code
ORDER BY customer_count DESC;

-- Top N critical customers + month-on-month spend decline over last 12 months
-- (CANONICAL "TOP N + TIME TREND" PATTERN — ONE ROW PER CUSTOMER, NOT per month):
WITH top_critical AS (
    SELECT c.id, c.customer_name, c.rolling_12m_spend
    FROM customers c
    JOIN order_headers oh ON oh.customer_id = c.id
    WHERE oh.sales_org_code = '{vkorg}'
      AND c.health_status_code = 'CRITICAL'
    GROUP BY c.id, c.customer_name, c.rolling_12m_spend
    ORDER BY c.rolling_12m_spend DESC NULLS LAST
    LIMIT 10
),
monthly_spend AS (
    SELECT tc.customer_name,
           DATE_TRUNC('month', oh.order_date) AS month,
           SUM(CASE WHEN document_category_code IN ('C','L') THEN net_value
                    WHEN document_category_code IN ('K','H') THEN -net_value
                    ELSE 0 END) AS spend
    FROM top_critical tc
    JOIN order_headers oh ON oh.customer_id = tc.id
    WHERE oh.sales_org_code = '{vkorg}'
      AND oh.order_date >= CURRENT_DATE - INTERVAL '12 months'
      AND COALESCE(oh.order_status_code, '') NOT IN ('CANCELLED','REJECTED')
    GROUP BY tc.customer_name, DATE_TRUNC('month', oh.order_date)
),
with_lag AS (
    SELECT customer_name, month, spend,
           LAG(spend) OVER (PARTITION BY customer_name ORDER BY month) AS prev_spend
    FROM monthly_spend
)
SELECT customer_name,
       ROUND(SUM(spend), 2)                            AS total_12m_spend,
       ROUND(AVG(CASE WHEN prev_spend > 0
                      THEN ((spend - prev_spend) / prev_spend) * 100
                      ELSE NULL END), 2)               AS avg_mom_change_pct
FROM with_lag
GROUP BY customer_name
ORDER BY avg_mom_change_pct ASC;
"""
 
SQL_RULES = """
== SQL RULES (STRICT) ==
1. ONLY SELECT statements. Never INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE.
2. ALWAYS filter order_headers by sales_org_code = '{vkorg}'.
3. Output ONLY raw SQL — no markdown fences, no explanation.
4. Always include LIMIT {max_rows} unless query already has a lower LIMIT.
5. Apply document_category_code sign convention for revenue totals.
6. Use CURRENT_DATE for today. DATE_TRUNC for period boundaries.
7. Use COALESCE(column, 0) for numeric aggregations with NULLs.
   For order_status_code exclusions, use COALESCE(order_status_code, '') NOT IN (...)
   because NULL NOT IN (...) evaluates to UNKNOWN and silently drops every row.
8. CUSTOMER COUNTS: When joining customers ⨝ order_headers, ALWAYS use
   COUNT(DISTINCT c.id) (never COUNT(*)) to count customers. The join fans out
   one row per order, so COUNT(*) is an order count, not a customer count.
   Same applies to SUM/AVG of customer-level columns over this join.
9. For "how many customers by status / segment / region" type questions, query
   daily_metrics_snapshots first if it has the dimension. Only fall back to a
   customers ⨝ order_headers GROUP BY when the snapshot lacks it.
10. OUTPUT SHAPE for "top N + time trend" questions: produce ONE row per entity
    with summary trend columns (e.g. avg_mom_change_pct, total_12m_spend), NOT
    one row per (entity, period). Use the canonical pattern: CTE for top N →
    CTE for per-period values → CTE with LAG window function → final SELECT
    aggregating to one row per entity. Returning 100+ rows for a "top 10"
    question is a bug, not a feature.
11. If you cannot write a valid query output exactly: CANNOT_GENERATE
"""
 
 
def get_schema_prompt(vkorg: str = None, max_rows: int = None) -> str:
    v = vkorg or config.ACTIVE_TENANT_VKORG
    m = max_rows or config.MAX_ROWS_RETURNED
    return (
        SCHEMA_DESCRIPTION.replace("{vkorg}", v) +
        EXAMPLE_QUERIES.replace("{vkorg}", v) +
        SQL_RULES.replace("{vkorg}", v).replace("{max_rows}", str(m))
    )
 
 
def get_schema_summary() -> str:
    return """
Tables: companies, customers, order_headers, order_lines, daily_metrics_snapshots
- customers: health status, spend metrics, last order date, sales rep
- order_headers: order values, dates, status, tenant filter (sales_org_code)
- order_lines: product-level detail
- daily_metrics_snapshots: pre-aggregated daily KPIs per tenant
"""
 