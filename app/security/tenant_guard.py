"""
app/security/tenant_guard.py — Tenant Isolation Enforcement
Ensures every SQL query is scoped to the active tenant's VKORG.
Enforced at APPLICATION LAYER — never trusted to the LLM alone.
"""

import logging
import re
from app.config import config
from app.logger import log_security_event

logger = logging.getLogger(__name__)


def enforce_tenant_scope(sql: str, vkorg: str = None) -> tuple[str, bool, str]:
    """
    Ensure SQL is scoped to active tenant.
    Returns: (modified_sql, was_modified, reason)
    """
    active_vkorg = vkorg or config.ACTIVE_TENANT_VKORG
    sql_upper    = sql.upper()

    references_orders = (
        "ORDER_HEADERS" in sql_upper or
        "OH." in sql_upper or
        "DAILY_METRICS_SNAPSHOTS" in sql_upper
    )

    if not references_orders:
        return sql, False, "No order_headers reference , tenant scope not applicable"

    pattern = r"sales_org_code\s*=\s*['\"](\d+)['\"]"
    matches = re.findall(pattern, sql, re.IGNORECASE)

    if not matches:
        logger.warning(f"Tenant filter missing , injecting sales_org_code = '{active_vkorg}'")
        modified = _inject_tenant_filter(sql, active_vkorg)
        return modified, True, "Tenant filter injected (was missing)"

    for found_vkorg in matches:
        if found_vkorg != active_vkorg:
            log_security_event(
                event_type="TENANT_VIOLATION",
                detail=f"Cross-tenant attempt: tried {found_vkorg}, active is {active_vkorg}",
                sql=sql,
                tenant_vkorg=active_vkorg,
            )
            modified = re.sub(
                r"sales_org_code\s*=\s*['\"]" + re.escape(found_vkorg) + r"['\"]",
                f"sales_org_code = '{active_vkorg}'",
                sql,
                flags=re.IGNORECASE,
            )
            return modified, True, f"Cross-tenant access blocked , corrected to {active_vkorg}"

    return sql, False, "Tenant scope verified"


def _inject_tenant_filter(sql: str, vkorg: str) -> str:
    alias = _detect_order_headers_alias(sql)
    where_match = re.search(r'\bWHERE\b', sql, re.IGNORECASE)
    if where_match:
        pos = where_match.end()
        return sql[:pos] + f" {alias}.sales_org_code = '{vkorg}' AND " + sql[pos:]
    for keyword in [r'\bGROUP\s+BY\b', r'\bORDER\s+BY\b', r'\bLIMIT\b', r'\bHAVING\b']:
        m = re.search(keyword, sql, re.IGNORECASE)
        if m:
            return sql[:m.start()] + f"WHERE {alias}.sales_org_code = '{vkorg}' " + sql[m.start():]
    return sql.rstrip(";").rstrip() + f"\nWHERE {alias}.sales_org_code = '{vkorg}'"


def _detect_order_headers_alias(sql: str) -> str:
    match = re.search(r'\border_headers\b\s+(?:AS\s+)?(\w+)', sql, re.IGNORECASE)
    return match.group(1) if match else "order_headers"