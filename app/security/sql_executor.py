"""
Safe SQL Execution
Combines validation + tenant enforcement + execution.
Called directly by agent tools.
"""

import logging
import re
from dataclasses import dataclass, field

from app.config import config
from app.database import execute_query
from app.security.sql_guard import validate_sql
from app.security.tenant_guard import enforce_tenant_scope
from app.logger import log_security_event

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success: bool
    rows: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error: str = ""
    blocked_reason: str = ""
    tenant_modified: bool = False
    final_sql: str = ""


def execute_safe_query(sql: str, tenant_vkorg: str = None) -> ExecutionResult:
    """
    Full pipeline: validate → enforce tenant → inject row limit → execute.
    """
    vkorg = tenant_vkorg or config.ACTIVE_TENANT_VKORG

    # Step 1: SQL Safety Validation
    validation = validate_sql(sql)
    if not validation.is_safe:
        log_security_event(
            event_type="SQL_BLOCKED",
            detail=validation.reason,
            sql=sql,
            tenant_vkorg=vkorg,
        )
        return ExecutionResult(
            success=False,
            blocked_reason=validation.reason,
            error=validation.reason,
            final_sql=sql,
        )

    # Step 2: Tenant Enforcement
    scoped_sql, was_modified, scope_reason = enforce_tenant_scope(sql, vkorg)
    if was_modified:
        logger.info(f"Tenant scope: {scope_reason}")

    # Step 3: Row Limit Injection
    final_sql = _ensure_row_limit(scoped_sql, config.MAX_ROWS_RETURNED)

    # Step 4: Execute
    logger.info(f"Executing SQL for tenant {vkorg}")
    result = execute_query(final_sql, row_limit=config.MAX_ROWS_RETURNED)

    if not result["success"]:
        return ExecutionResult(
            success=False,
            error=result["error"],
            final_sql=final_sql,
            tenant_modified=was_modified,
        )

    return ExecutionResult(
        success=True,
        rows=result["rows"],
        columns=result["columns"],
        row_count=result["row_count"],
        truncated=result["truncated"],
        final_sql=final_sql,
        tenant_modified=was_modified,
    )


def _ensure_row_limit(sql: str, max_rows: int) -> str:
    limit_match = re.search(r'\bLIMIT\s+(\d+)', sql, re.IGNORECASE)
    if limit_match:
        existing = int(limit_match.group(1))
        if existing <= max_rows:
            return sql
        return re.sub(r'\bLIMIT\s+\d+', f'LIMIT {max_rows}', sql, flags=re.IGNORECASE)
    return sql.rstrip(";").rstrip() + f"\nLIMIT {max_rows}"