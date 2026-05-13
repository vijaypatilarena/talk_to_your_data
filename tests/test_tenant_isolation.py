"""
tests/test_tenant_isolation.py — Tests for tenant isolation

Verifies:
  - Correct tenant filter is present
  - Cross-tenant access is blocked/corrected
  - Missing tenant filter gets injected
  - tenant_guard works at the application layer

Run: pytest tests/test_tenant_isolation.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from app.security.tenant_guard import enforce_tenant_scope, verify_tenant_scope_ast


ACTIVE_VKORG = "1004"   # Brennwald — the "logged in" tenant for these tests


class TestTenantFilterPresent:

    def test_correct_tenant_passes_unchanged(self):
        sql = f"SELECT * FROM order_headers WHERE sales_org_code = '{ACTIVE_VKORG}' LIMIT 10"
        modified_sql, was_modified, reason = enforce_tenant_scope(sql, ACTIVE_VKORG)
        assert not was_modified, "Correct tenant filter should not be modified"
        assert ACTIVE_VKORG in modified_sql

    def test_correct_tenant_in_join_query(self):
        sql = f"""
        SELECT c.customer_name FROM customers c
        JOIN order_headers oh ON oh.customer_id = c.id
        WHERE oh.sales_org_code = '{ACTIVE_VKORG}'
        """
        _, was_modified, _ = enforce_tenant_scope(sql, ACTIVE_VKORG)
        assert not was_modified


class TestCrossTenantBlocking:

    def test_wrong_tenant_gets_corrected(self):
        wrong_vkorg = "9999"
        sql = f"SELECT * FROM order_headers WHERE sales_org_code = '{wrong_vkorg}'"
        modified_sql, was_modified, reason = enforce_tenant_scope(sql, ACTIVE_VKORG)
        assert was_modified, "Wrong tenant should be corrected"
        assert ACTIVE_VKORG in modified_sql
        assert wrong_vkorg not in modified_sql

    def test_lindauer_blocked_when_brennwald_active(self):
        lindauer_vkorg = "1010"
        sql = f"SELECT * FROM order_headers WHERE sales_org_code = '{lindauer_vkorg}'"
        modified_sql, was_modified, _ = enforce_tenant_scope(sql, ACTIVE_VKORG)
        assert was_modified
        assert ACTIVE_VKORG in modified_sql
        assert lindauer_vkorg not in modified_sql

    def test_renaux_be_blocked_when_brennwald_active(self):
        renaux_be = "1032"
        sql = f"""
        SELECT c.customer_name, SUM(oh.net_value)
        FROM customers c
        JOIN order_headers oh ON oh.customer_id = c.id
        WHERE oh.sales_org_code = '{renaux_be}'
        GROUP BY c.id
        """
        modified_sql, was_modified, _ = enforce_tenant_scope(sql, ACTIVE_VKORG)
        assert was_modified
        assert renaux_be not in modified_sql


class TestMissingTenantFilter:

    def test_missing_filter_gets_injected(self):
        sql = "SELECT * FROM order_headers LIMIT 10"
        modified_sql, was_modified, reason = enforce_tenant_scope(sql, ACTIVE_VKORG)
        assert was_modified, "Missing tenant filter should be injected"
        assert ACTIVE_VKORG in modified_sql

    def test_missing_filter_in_join_query(self):
        sql = """
        SELECT c.customer_name, COUNT(oh.id) as order_count
        FROM customers c
        JOIN order_headers oh ON oh.customer_id = c.id
        GROUP BY c.id
        LIMIT 20
        """
        modified_sql, was_modified, _ = enforce_tenant_scope(sql, ACTIVE_VKORG)
        assert was_modified
        assert ACTIVE_VKORG in modified_sql


class TestTenantScopeNotApplicable:

    def test_customers_only_query_no_modification(self):
        # customers table doesn't have sales_org_code — tenant via company_id
        sql = """
        SELECT customer_name, health_status_code, rolling_12m_spend
        FROM customers
        WHERE health_status_code = 'CRITICAL'
        ORDER BY rolling_12m_spend DESC
        LIMIT 10
        """
        _, was_modified, reason = enforce_tenant_scope(sql, ACTIVE_VKORG)
        # Should not be modified — no order_headers reference
        assert not was_modified

    def test_daily_metrics_snapshot_query(self):
        sql = """
        SELECT * FROM daily_metrics_snapshots dms
        JOIN companies co ON co.id = dms.company_id
        WHERE co.vkorg_codes::text LIKE '%1004%'
        ORDER BY dms.snapshot_date DESC LIMIT 1
        """
        _, was_modified, _ = enforce_tenant_scope(sql, ACTIVE_VKORG)
        # No order_headers — should not be modified
        assert not was_modified


class TestASTLevelVerification:
    """The AST verifier is a 'trust but verify' safety net that catches edge
    cases the regex injection can miss (subqueries, CTEs, multi-scope refs).
    """

    def test_correct_filter_passes(self):
        sql = f"SELECT * FROM order_headers WHERE sales_org_code = '{ACTIVE_VKORG}'"
        ok, _ = verify_tenant_scope_ast(sql, ACTIVE_VKORG)
        assert ok

    def test_correct_filter_with_alias_passes(self):
        sql = f"""
        SELECT * FROM order_headers oh
        WHERE oh.sales_org_code = '{ACTIVE_VKORG}'
        """
        ok, _ = verify_tenant_scope_ast(sql, ACTIVE_VKORG)
        assert ok

    def test_wrong_vkorg_in_filter_is_flagged(self):
        sql = "SELECT * FROM order_headers WHERE sales_org_code = '9999'"
        ok, reason = verify_tenant_scope_ast(sql, ACTIVE_VKORG)
        assert not ok
        assert ACTIVE_VKORG in reason

    def test_missing_filter_is_flagged(self):
        sql = "SELECT * FROM order_headers"
        ok, reason = verify_tenant_scope_ast(sql, ACTIVE_VKORG)
        assert not ok

    def test_subquery_only_filter_does_not_protect_outer(self):
        """The classic regex edge case: filter is inside a subquery, outer
        scope is unscoped. AST verifier must catch this."""
        sql = f"""
        SELECT * FROM order_headers
        WHERE id IN (
            SELECT id FROM order_headers WHERE sales_org_code = '{ACTIVE_VKORG}'
        )
        """
        ok, _ = verify_tenant_scope_ast(sql, ACTIVE_VKORG)
        assert not ok, "Outer scope has no tenant filter — must be flagged"

    def test_customers_only_query_passes(self):
        sql = "SELECT customer_name FROM customers WHERE health_status_code = 'CRITICAL'"
        ok, _ = verify_tenant_scope_ast(sql, ACTIVE_VKORG)
        assert ok  # no order_headers reference at all → nothing to scope


class TestAllFourTenants:
    """Verify tenant isolation works for each of the four companies."""

    @pytest.mark.parametrize("tenant_vkorg,wrong_vkorg", [
        ("1004", "1010"),  # Brennwald active, Lindauer blocked
        ("1010", "1004"),  # Lindauer active, Brennwald blocked
        ("1032", "1031"),  # Renaux-BE active, Renaux-LU blocked
        ("1031", "1032"),  # Renaux-LU active, Renaux-BE blocked
    ])
    def test_cross_tenant_corrected_for_all_tenants(self, tenant_vkorg, wrong_vkorg):
        sql = f"SELECT * FROM order_headers WHERE sales_org_code = '{wrong_vkorg}'"
        modified_sql, was_modified, _ = enforce_tenant_scope(sql, tenant_vkorg)
        assert was_modified
        assert tenant_vkorg in modified_sql
        assert wrong_vkorg not in modified_sql