"""
Tenant Isolation Enforcement
Ensures every SQL query is scoped to the active tenant's VKORG.
Enforced at APPLICATION LAYER — never trusted to the LLM alone.

Two layers of enforcement:
  1. enforce_tenant_scope()      — regex-based injection / correction (fast, common case)
  2. verify_tenant_scope_ast()   — sqlglot AST verification (catches subquery / CTE edge
                                   cases the regex misses; fails closed by blocking)
"""

import logging
import re

import sqlglot
import sqlglot.expressions as exp

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
        "OH." in sql_upper
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


# ── AST-level verifier ─────────────────────────────────────────────────────
# Runs AFTER enforce_tenant_scope() as a "trust but verify" safety net.
# Parses the (already-modified) SQL and walks every Select node, ensuring any
# scope that directly references order_headers has a sales_org_code = '<vkorg>'
# predicate in its WHERE. Catches subquery / CTE edge cases the regex misses.

def verify_tenant_scope_ast(sql: str, vkorg: str = None) -> tuple[bool, str]:
    """
    Verify every Select-scope referencing order_headers has the correct tenant
    predicate. Returns (is_safe, reason). Fail closed: block on any uncertainty.
    """
    active_vkorg = vkorg or config.ACTIVE_TENANT_VKORG

    try:
        parsed_list = sqlglot.parse(sql, dialect="postgres")
    except Exception as e:
        return False, f"AST parse failed during tenant verification: {str(e)[:120]}"

    if not parsed_list:
        return False, "Empty AST during tenant verification"
    if len(parsed_list) != 1:
        return False, f"Expected 1 statement, got {len(parsed_list)} during tenant verification"

    parsed = parsed_list[0]

    unsafe_scopes = 0
    for select in parsed.find_all(exp.Select):
        if _scope_directly_uses_order_headers(select):
            if not _scope_has_vkorg_predicate(select, active_vkorg):
                unsafe_scopes += 1

    if unsafe_scopes:
        return False, (
            f"Tenant scope verification failed: {unsafe_scopes} scope(s) reference "
            f"order_headers without sales_org_code = '{active_vkorg}' predicate"
        )

    return True, "Tenant scope verified at AST level"


def _scope_directly_uses_order_headers(select_node) -> bool:
    """True if this Select's own FROM/JOIN list references order_headers
    directly (i.e. not via a nested subquery).

    sqlglot stores the From under the key ``from_`` (trailing underscore;
    plain ``from`` would shadow Python's reserved keyword)."""
    from_clause = select_node.args.get("from_") or select_node.args.get("from")
    if from_clause is not None:
        thing = getattr(from_clause, "this", None)
        if isinstance(thing, exp.Table) and (thing.name or "").lower() == "order_headers":
            return True

    for join in (select_node.args.get("joins") or []):
        thing = getattr(join, "this", None)
        if isinstance(thing, exp.Table) and (thing.name or "").lower() == "order_headers":
            return True

    return False


def _scope_has_vkorg_predicate(select_node, vkorg: str) -> bool:
    """True if THIS Select's own WHERE clause contains sales_org_code = '<vkorg>'.

    Critically, the search is scope-local: it does NOT descend into nested
    Select/Subquery nodes inside the WHERE (e.g. ``WHERE id IN (SELECT ...)``).
    Those subqueries are different scopes and are inspected separately by the
    outer ``find_all(exp.Select)`` loop in ``verify_tenant_scope_ast``.
    """
    where = select_node.args.get("where")
    if where is None:
        return False

    for eq in _walk_eq_in_scope(where):
        left  = eq.args.get("this")
        right = eq.args.get("expression")

        col_name = None
        if isinstance(left, exp.Column):
            col_name = (left.name or "").lower()
        if col_name != "sales_org_code":
            continue

        if isinstance(right, exp.Literal):
            val = str(right.this).strip("'\"")
            if val == str(vkorg):
                return True

    return False


def _walk_eq_in_scope(node):
    """Yield every ``exp.EQ`` reachable from *node* without crossing a nested
    Select / Subquery boundary. Those represent a different scope."""
    if isinstance(node, exp.EQ):
        yield node

    for child in node.args.values():
        children = child if isinstance(child, list) else [child]
        for c in children:
            if c is None or not hasattr(c, "args"):
                continue
            # Stop at scope boundaries
            if isinstance(c, (exp.Select, exp.Subquery)):
                continue
            yield from _walk_eq_in_scope(c)