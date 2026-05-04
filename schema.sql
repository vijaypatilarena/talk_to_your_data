
-- Talk to Your Data — PostgreSQL Schema
-- Run: psql talk_to_your_data -f schema.sql

-- Drop existing tables (safe re-run)
DROP TABLE IF EXISTS order_lines CASCADE;
DROP TABLE IF EXISTS order_headers CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS daily_metrics_snapshots CASCADE;
DROP TABLE IF EXISTS companies CASCADE;


-- TABLE 1: companies (tenants)

CREATE TABLE companies (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    health_thresholds JSONB,
    impact_thresholds JSONB,
    default_currency CHAR(3) DEFAULT 'EUR',
    timezone TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    vkorg_codes JSONB,
    order_book_target NUMERIC
);


-- TABLE 2: customers
CREATE TABLE customers (
    id UUID PRIMARY KEY,
    external_id TEXT,
    customer_name TEXT,
    address TEXT,
    city TEXT,
    region TEXT,
    postal_code TEXT,
    country_code CHAR(2),
    industry_sector_id UUID,
    customer_hierarchy_id UUID,
    customer_group_code TEXT,
    sales_office_code TEXT,
    payment_terms_code TEXT,
    credit_limit NUMERIC,
    customer_since DATE,
    primary_email TEXT,
    primary_phone TEXT,
    website TEXT,
    source_customer_colour TEXT,
    source_ranking INTEGER,
    source_value_coefficient INTEGER,
    processing_unit TEXT,
    account_owner_name TEXT,
    rolling_12m_spend NUMERIC,
    rolling_12m_order_count INTEGER,
    previous_12m_spend NUMERIC,
    average_order_value NUMERIC,
    average_order_interval_days NUMERIC,
    last_order_date DATE,
    days_since_last_order INTEGER,
    expected_next_order_date DATE,
    days_overdue INTEGER,
    spend_change_percentage NUMERIC,
    health_status_code TEXT,
    health_status_changed_at TIMESTAMPTZ,
    previous_health_status_code TEXT,
    impact_level_code TEXT,
    requires_attention BOOLEAN,
    requires_attention_since TIMESTAMPTZ,
    ai_status_explanation TEXT,
    ai_outreach_email TEXT,
    ai_call_script TEXT,
    ai_content_generated_at TIMESTAMPTZ,
    ai_content_status_snapshot TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    source_modified_at TIMESTAMPTZ,
    source_record_version INTEGER,
    data_quality_score NUMERIC,
    data_quality_flags JSONB,
    -- Extra columns found in actual CSV
    ai_status_explanation_generated_at TIMESTAMPTZ,
    ai_outreach_email_generated_at TIMESTAMPTZ,
    ai_outreach_email_language TEXT,
    ai_call_script_generated_at TIMESTAMPTZ,
    ai_call_script_language TEXT,
    company_id UUID,
    account_owner_id UUID,
    ai_meeting_plan TEXT,
    ai_meeting_plan_generated_at TIMESTAMPTZ,
    ai_meeting_plan_language TEXT,
    lifetime_order_count INTEGER
);


-- TABLE 3: order_headers
CREATE TABLE order_headers (
    id UUID PRIMARY KEY,
    external_order_id TEXT,
    customer_id UUID REFERENCES customers(id),
    order_type_code TEXT,
    order_date DATE,
    created_date DATE,
    created_time TIME,
    delivery_date DATE,
    net_value NUMERIC,
    currency_code CHAR(3),
    order_status_code TEXT,
    sales_org_code TEXT,           -- TENANT IDENTIFIER (VKORG)
    distribution_channel TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    source_modified_at TIMESTAMPTZ,
    source_record_version INTEGER,
    source_sap_timestamp TIMESTAMPTZ,
    document_category_code CHAR(1)
);


-- TABLE 4: order_lines
CREATE TABLE order_lines (
    id UUID PRIMARY KEY,
    order_header_id UUID REFERENCES order_headers(id),
    line_number INTEGER,
    material_number TEXT,
    material_description TEXT,
    material_group_code TEXT,
    quantity NUMERIC,
    unit_of_measure TEXT,
    unit_price NUMERIC,
    net_value NUMERIC,
    plant_code TEXT,
    rejection_reason_code TEXT,
    delivery_status_code TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    source_modified_at TIMESTAMPTZ,
    source_record_version INTEGER,
    contract_reference TEXT
);


-- TABLE 5: daily_metrics_snapshots
CREATE TABLE daily_metrics_snapshots (
    id UUID PRIMARY KEY,
    snapshot_date DATE,
    company_id UUID REFERENCES companies(id),
    -- Legacy (kept as 0)
    total_order_value NUMERIC DEFAULT 0,
    open_order_value NUMERIC DEFAULT 0,
    average_order_value NUMERIC DEFAULT 0,
    revenue_at_risk NUMERIC DEFAULT 0,
    frequency_drift_count INTEGER DEFAULT 0,
    -- Order book rolling windows
    order_book_value NUMERIC,
    order_book_value_month NUMERIC,
    order_book_value_month_prev NUMERIC,
    order_book_value_month_change NUMERIC,
    order_book_value_month_change_pct NUMERIC,
    order_book_value_quarter NUMERIC,  
    order_book_value_quarter_prev NUMERIC,
    order_book_value_quarter_change NUMERIC,
    order_book_value_quarter_change_pct NUMERIC,
    order_book_value_year NUMERIC,
    order_book_value_year_prev NUMERIC,
    order_book_value_year_change NUMERIC,
    order_book_value_year_change_pct NUMERIC,
    -- AOV rolling windows
    avg_order_value_month NUMERIC,
    avg_order_value_month_prev NUMERIC,
    avg_order_value_month_change NUMERIC,
    avg_order_value_month_change_pct NUMERIC,
    avg_order_value_quarter NUMERIC,
    avg_order_value_quarter_prev NUMERIC,
    avg_order_value_quarter_change NUMERIC,
    avg_order_value_quarter_change_pct NUMERIC,
    avg_order_value_year NUMERIC,
    avg_order_value_year_prev NUMERIC,
    avg_order_value_year_change NUMERIC,
    avg_order_value_year_change_pct NUMERIC,
    -- Order velocity
    order_velocity_month NUMERIC,
    order_velocity_month_prev NUMERIC,
    order_velocity_month_change NUMERIC,
    order_velocity_quarter NUMERIC,
    order_velocity_quarter_prev NUMERIC,
    order_velocity_quarter_change NUMERIC,
    order_velocity_year NUMERIC,
    order_velocity_year_prev NUMERIC,
    order_velocity_year_change NUMERIC,
    -- Revenue at risk
    revenue_at_risk_12m NUMERIC,
    revenue_at_risk_1m_ago NUMERIC,
    revenue_at_risk_3m_ago NUMERIC,
    revenue_at_risk_12m_ago NUMERIC,
    revenue_at_risk_month_change NUMERIC,
    revenue_at_risk_month_change_pct NUMERIC,
    revenue_at_risk_quarter_change NUMERIC,
    revenue_at_risk_quarter_change_pct NUMERIC,
    revenue_at_risk_year_change NUMERIC,
    revenue_at_risk_year_change_pct NUMERIC,
    -- Health distribution
    healthy_count INTEGER,
    early_warning_count INTEGER,
    at_risk_count INTEGER,
    critical_count INTEGER,
    inactive_count INTEGER,
    total_customer_count INTEGER,
    customers_needing_attention INTEGER,
    value_at_stake NUMERIC,
    overdue_count INTEGER,
    declining_spend_count INTEGER,
    improved_count INTEGER,
    deteriorated_count INTEGER,
    new_alerts_count INTEGER,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);


-- INDEXES (critical for performance on large tables)
CREATE INDEX idx_order_headers_sales_org    ON order_headers(sales_org_code);
CREATE INDEX idx_order_headers_customer_id  ON order_headers(customer_id);
CREATE INDEX idx_order_headers_order_date   ON order_headers(order_date);
CREATE INDEX idx_order_headers_status       ON order_headers(order_status_code);
CREATE INDEX idx_order_headers_doc_cat      ON order_headers(document_category_code);
CREATE INDEX idx_order_lines_header         ON order_lines(order_header_id);
CREATE INDEX idx_order_lines_material       ON order_lines(material_number);
CREATE INDEX idx_customers_health           ON customers(health_status_code);
CREATE INDEX idx_customers_owner            ON customers(account_owner_name);
CREATE INDEX idx_customers_company          ON customers(company_id);
CREATE INDEX idx_customers_requires_attn    ON customers(requires_attention);
CREATE INDEX idx_daily_metrics_company_date ON daily_metrics_snapshots(company_id, snapshot_date DESC);

-- Verify
SELECT 'Schema created successfully' AS status;
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;