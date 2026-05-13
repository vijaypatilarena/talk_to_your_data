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
8. If you cannot write a valid query output exactly: CANNOT_GENERATE
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
 