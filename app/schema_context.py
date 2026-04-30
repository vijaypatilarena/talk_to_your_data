"""
app/schema_context.py — Schema description injected into every LLM prompt.

This is the most important prompt engineering file in the project.
The LLM must know the schema precisely to generate valid SQL.
Strategy used: Curated schema subset (Appendix A, Option 1/4 from brief).
"""

from app.config import config

# ── Core schema description ───────────────────────────────────────────────────
SCHEMA_DESCRIPTION = """
You are querying a B2B distributor database with the following tables.
The active tenant (company) is filtered by: sales_org_code = '{vkorg}'
EVERY query you write MUST include WHERE sales_org_code = '{vkorg}' on order_headers.

== TABLE: companies (4 rows — tenant reference) ==
  id UUID, name TEXT, code TEXT, default_currency CHAR(3),
  timezone TEXT, vkorg_codes JSONB, order_book_target NUMERIC

== TABLE: customers (6,787 rows) ==
  id UUID PRIMARY KEY
  external_id TEXT                     -- SAP customer number
  customer_name TEXT                   -- Company name
  city TEXT, region TEXT, country_code CHAR(2)
  account_owner_name TEXT              -- Sales rep name
  credit_limit NUMERIC                 -- EUR
  customer_since DATE
  company_id UUID                      -- FK to companies.id
  -- Health & status (pre-computed snapshots)
  health_status_code TEXT              -- HEALTHY | EARLY_WARNING | AT_RISK | CRITICAL | INACTIVE
  impact_level_code TEXT               -- HIGH | MEDIUM | LOW
  requires_attention BOOLEAN           -- true when unhealthy AND high/medium impact
  -- Spend metrics (pre-computed rolling windows)
  rolling_12m_spend NUMERIC            -- Total spend last 12 months (EUR)
  rolling_12m_order_count INTEGER      -- Order count last 12 months
  previous_12m_spend NUMERIC           -- Spend 12–24 months ago
  average_order_value NUMERIC          -- Mean order value
  average_order_interval_days NUMERIC  -- Mean days between orders
  last_order_date DATE                 -- MAX(order_date) for this customer
  days_since_last_order INTEGER        -- Days since last order
  days_overdue INTEGER                 -- Days past expected next order (0 if not overdue)
  spend_change_percentage NUMERIC      -- YoY spend change %
  lifetime_order_count INTEGER         -- All-time order count

== TABLE: order_headers (174,952 rows) ==
  id UUID PRIMARY KEY
  external_order_id TEXT               -- SAP sales document number
  customer_id UUID                     -- FK to customers.id
  order_date DATE                      -- When customer placed order
  delivery_date DATE                   -- Expected delivery
  net_value NUMERIC                    -- Order value in EUR (before sign adjustment)
  currency_code CHAR(3)                -- Always EUR
  order_status_code TEXT               -- OPEN | DELIVERED | CANCELLED | REJECTED
  sales_org_code TEXT                  -- *** TENANT FILTER — always filter by this ***
  document_category_code CHAR(1)       -- C/L = revenue, K/H = deductions (returns/credits)
  order_type_code TEXT                 -- SAP order type (OR=standard, etc.)
  distribution_channel TEXT

  IMPORTANT — Revenue sign convention:
    document_category_code IN ('C','L') → ADD to revenue
    document_category_code IN ('K','H') → SUBTRACT from revenue (returns/credits)
  Always exclude: order_status_code IN ('CANCELLED','REJECTED')
  
  Canonical order book formula:
    SUM(CASE WHEN document_category_code IN ('C','L') THEN net_value
             WHEN document_category_code IN ('K','H') THEN -net_value
             ELSE 0 END)
    WHERE sales_org_code = '{vkorg}'
    AND order_status_code NOT IN ('CANCELLED','REJECTED')

== TABLE: order_lines (268,880 rows) ==
  id UUID, order_header_id UUID        -- FK to order_headers.id
  line_number INTEGER
  material_number TEXT                 -- Product code
  material_description TEXT            -- Product name (may be in German/French/Dutch)
  material_group_code TEXT             -- Product category
  quantity NUMERIC, unit_of_measure TEXT
  unit_price NUMERIC, net_value NUMERIC  -- quantity × unit_price
  plant_code TEXT                      -- Warehouse/plant
  delivery_status_code TEXT            -- DELIVERED | PENDING | PARTIAL | CANCELLED
  NOTE: No sales_org_code on this table — JOIN through order_headers to filter by tenant

== TABLE: daily_metrics_snapshots (280 rows — pre-aggregated KPIs) ==
  id UUID, snapshot_date DATE
  company_id UUID                      -- FK to companies.id (tenant scope)
  -- Use this table for current KPI/dashboard questions (faster)
  order_book_value NUMERIC             -- Rolling 12-month order book
  order_book_value_month NUMERIC       -- Last 30 days
  order_book_value_quarter NUMERIC     -- Last 90 days
  order_book_value_year NUMERIC        -- Last 12 months
  avg_order_value_month/quarter/year NUMERIC
  order_velocity_month/quarter/year NUMERIC  -- Mean days between orders (lower=faster)
  revenue_at_risk_12m NUMERIC          -- Revenue from unhealthy customers
  healthy_count INTEGER, early_warning_count INTEGER
  at_risk_count INTEGER, critical_count INTEGER, inactive_count INTEGER
  total_customer_count INTEGER
  customers_needing_attention INTEGER  -- requires_attention = true
  value_at_stake NUMERIC               -- Rolling spend of at-risk customers
  overdue_count INTEGER, declining_spend_count INTEGER
  improved_count INTEGER, deteriorated_count INTEGER

== KEY JOIN PATTERNS ==

-- Customers of this tenant:
SELECT c.* FROM customers c
JOIN order_headers oh ON oh.customer_id = c.id
WHERE oh.sales_org_code = '{vkorg}'

-- Order lines for this tenant:
SELECT ol.* FROM order_lines ol
JOIN order_headers oh ON oh.id = ol.order_header_id
WHERE oh.sales_org_code = '{vkorg}'

-- Latest KPI snapshot for this tenant:
SELECT dms.* FROM daily_metrics_snapshots dms
JOIN companies co ON co.id = dms.company_id
WHERE co.vkorg_codes::text LIKE '%{vkorg}%'
ORDER BY dms.snapshot_date DESC LIMIT 1

== QUERY ROUTING GUIDE ==
Use daily_metrics_snapshots when user asks:
  - "what's our order book value?" / current KPIs / health summary / how many customers are at risk
Use order_headers + order_lines when user asks:
  - specific date ranges / trends / historical analysis / product-level questions
Use customers table when user asks:
  - customer lists / health status / sales rep accounts / spend rankings
"""

EXAMPLE_QUERIES = """
== EXAMPLE QUESTION → SQL PAIRS ==

Q: "Who are our top 5 customers by spend?"
A: SELECT c.customer_name, c.rolling_12m_spend, c.health_status_code
   FROM customers c
   JOIN order_headers oh ON oh.customer_id = c.id
   WHERE oh.sales_org_code = '{vkorg}'
   GROUP BY c.id, c.customer_name, c.rolling_12m_spend, c.health_status_code
   ORDER BY c.rolling_12m_spend DESC
   LIMIT 5;

Q: "Which customers haven't ordered in over 60 days?"
A: SELECT customer_name, last_order_date, days_since_last_order, health_status_code
   FROM customers c
   JOIN order_headers oh ON oh.customer_id = c.id
   WHERE oh.sales_org_code = '{vkorg}'
   AND c.days_since_last_order > 60
   GROUP BY c.id, c.customer_name, c.last_order_date, c.days_since_last_order, c.health_status_code
   ORDER BY c.days_since_last_order DESC;

Q: "What's our total revenue this year?"
A: SELECT SUM(CASE WHEN document_category_code IN ('C','L') THEN net_value
                   WHEN document_category_code IN ('K','H') THEN -net_value
                   ELSE 0 END) AS total_revenue
   FROM order_headers
   WHERE sales_org_code = '{vkorg}'
   AND order_status_code NOT IN ('CANCELLED','REJECTED')
   AND order_date >= DATE_TRUNC('year', CURRENT_DATE);

Q: "How many customers need attention right now?"
A: SELECT customers_needing_attention, value_at_stake, total_customer_count
   FROM daily_metrics_snapshots dms
   JOIN companies co ON co.id = dms.company_id
   WHERE co.vkorg_codes::text LIKE '%{vkorg}%'
   ORDER BY dms.snapshot_date DESC LIMIT 1;

Q: "Give me a breakdown of customers by health status"
A: SELECT health_status_code, COUNT(*) as customer_count,
          ROUND(AVG(rolling_12m_spend), 2) as avg_spend
   FROM customers c
   JOIN order_headers oh ON oh.customer_id = c.id
   WHERE oh.sales_org_code = '{vkorg}'
   GROUP BY c.id, c.health_status_code
   -- wrap in subquery to avoid double-counting:
   -- actually simplified:
   GROUP BY health_status_code
   ORDER BY customer_count DESC;
"""

SQL_RULES = """
== STRICT SQL RULES ==
1. ONLY generate SELECT statements. Never INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE.
2. ALWAYS filter order_headers by sales_org_code = '{vkorg}'.
3. To filter order_lines by tenant, JOIN through order_headers.
4. Output ONLY raw SQL — no markdown, no code fences, no explanation.
5. If the question is ambiguous, make a reasonable assumption and write the query.
6. Always add LIMIT {max_rows} unless the query already has a lower LIMIT.
7. For revenue totals, always apply the document_category_code sign convention.
8. Dates: use CURRENT_DATE for "today". DATE_TRUNC for month/quarter/year boundaries.
9. NULL handling: use COALESCE(column, 0) for numeric aggregations.
10. If you cannot write a valid query for this question, output exactly: CANNOT_GENERATE
"""


def get_schema_prompt(vkorg: str = None, max_rows: int = None) -> str:
    """
    Return the full schema context string, formatted with the active tenant.
    Injected into every SQL generation prompt.
    """
    v = vkorg or config.ACTIVE_TENANT_VKORG
    m = max_rows or config.MAX_ROWS_RETURNED
    return (
        SCHEMA_DESCRIPTION.replace("{vkorg}", v) +
        EXAMPLE_QUERIES.replace("{vkorg}", v) +
        SQL_RULES.replace("{vkorg}", v).replace("{max_rows}", str(m))
    )


def get_schema_summary() -> str:
    """Short schema summary for classifier prompt (no examples)."""
    return """
Tables available: companies, customers, order_headers, order_lines, daily_metrics_snapshots
Key facts:
- customers has health status, spend metrics, last order date
- order_headers has order values, dates, status
- order_lines has product-level detail
- daily_metrics_snapshots has pre-aggregated daily KPIs per tenant
"""