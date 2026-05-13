"""
tests/test_sql_validator.py — SQL Safety Tests
Run: pytest tests/test_sql_validator.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
from app.security.sql_guard import validate_sql


class TestValidQueries:
    def test_simple_select(self):
        assert validate_sql("SELECT * FROM customers LIMIT 10").is_safe

    def test_select_with_join(self):
        sql = """SELECT c.customer_name, SUM(oh.net_value)
                 FROM customers c JOIN order_headers oh ON oh.customer_id = c.id
                 WHERE oh.sales_org_code = '1004' GROUP BY c.id LIMIT 5"""
        assert validate_sql(sql).is_safe

    def test_select_with_case(self):
        sql = """SELECT SUM(CASE WHEN document_category_code IN ('C','L') THEN net_value
                              WHEN document_category_code IN ('K','H') THEN -net_value
                              ELSE 0 END) FROM order_headers WHERE sales_org_code = '1004'"""
        assert validate_sql(sql).is_safe

    def test_select_with_cte(self):
        sql = """WITH r AS (SELECT customer_name, rolling_12m_spend FROM customers)
                 SELECT * FROM r LIMIT 10"""
        assert validate_sql(sql).is_safe


class TestDestructiveBlocked:
    def test_delete(self):        assert not validate_sql("DELETE FROM customers WHERE 1=1").is_safe
    def test_drop(self):          assert not validate_sql("DROP TABLE order_headers").is_safe
    def test_update(self):        assert not validate_sql("UPDATE customers SET credit_limit=0").is_safe
    def test_truncate(self):      assert not validate_sql("TRUNCATE TABLE order_lines").is_safe
    def test_insert(self):        assert not validate_sql("INSERT INTO customers (customer_name) VALUES ('x')").is_safe
    def test_alter(self):         assert not validate_sql("ALTER TABLE customers ADD COLUMN x TEXT").is_safe
    def test_create(self):        assert not validate_sql("CREATE TABLE stolen AS SELECT * FROM customers").is_safe


class TestInjection:
    def test_semicolon_injection(self):
        assert not validate_sql("SELECT * FROM customers LIMIT 10; DROP TABLE customers").is_safe

    def test_pg_sleep(self):
        assert not validate_sql("SELECT pg_sleep(10)").is_safe

    def test_two_clean_selects_blocked(self):
        """Phase 5: two SELECT statements separated by ';' must be blocked
        even though neither contains a forbidden keyword."""
        r = validate_sql("SELECT 1; SELECT 2")
        assert not r.is_safe
        assert "multiple" in r.reason.lower() or "statement" in r.reason.lower()

    def test_three_clean_selects_blocked(self):
        r = validate_sql("SELECT 1; SELECT 2; SELECT 3")
        assert not r.is_safe


class TestEdgeCases:
    def test_empty(self):         assert not validate_sql("").is_safe
    def test_whitespace(self):    assert not validate_sql("   ").is_safe
    def test_lowercase_delete(self): assert not validate_sql("delete from customers").is_safe
    def test_mixed_case_drop(self):  assert not validate_sql("DrOp TaBlE customers").is_safe