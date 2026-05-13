import logging
import re
from dataclasses import dataclass, field

import sqlglot
import sqlglot.expressions as exp  

logger = logging.getLogger(__name__)

FORBIDDEN_SQL_PATTERNS = [
    r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
    r"\bALTER\b", r"\bTRUNCATE\b", r"\bCREATE\b", r"\bGRANT\b",
    r"\bREVOKE\b", r"\bEXECUTE\b", r"\bEXEC\b", r"\bCOPY\b",
    r"\bpg_sleep\b", r"\bXP_\w+",
]
  

SUSPICIOUS_PATTERNS = [
    r"\bINFORMATION_SCHEMA\b", r"\bpg_catalog\b",
    r"\bpg_tables\b", r"\bpg_user\b",
]


@dataclass
class ValidationResult:
    is_safe: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def validate_sql(sql: str) -> ValidationResult:
    if not sql or not sql.strip():
        return ValidationResult(is_safe=False, reason="Empty SQL")

    sql_upper = sql.upper().strip()
    warnings = []

    # Phase 1: Must start with SELECT (or WITH for CTEs)
    stripped = sql_upper.lstrip()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        logger.warning(f"BLOCKED: Does not start with SELECT: {sql[:100]}")
        return ValidationResult(    
            is_safe=False,
            reason="Only SELECT statements are permitted",  
        )

    # Phase 2: Forbidden keywords
    for pattern in FORBIDDEN_SQL_PATTERNS:
        if re.search(pattern, sql_upper, re.IGNORECASE | re.DOTALL):
            logger.warning(f"BLOCKED: Forbidden pattern '{pattern}'")
            return ValidationResult(
                is_safe=False,
                reason="Query contains a forbidden operation. Only read-only SELECT queries are allowed.",
            )

    # Phase 3: Suspicious patterns (log only)
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, sql_upper, re.IGNORECASE):
            warnings.append(f"Suspicious pattern: {pattern}")
            logger.warning(f"Suspicious SQL pattern '{pattern}'")

    # Phase 4: AST parse via sqlglot — confirms it's a SELECT and parseable
    try:
        parsed = sqlglot.parse(sql, dialect="postgres")
        if not parsed:
            return ValidationResult(is_safe=False, reason="SQL could not be parsed")

        # Phase 5: Multi-statement / semicolon-injection check.
        # Even if every statement is a SELECT, only ONE statement is allowed.
        # This catches the case where the forbidden-keyword regex would miss
        # two clean SELECTs separated by ';' (e.g. "SELECT 1; SELECT 2").
        if len(parsed) > 1:
            logger.warning(
                f"BLOCKED: Multi-statement query ({len(parsed)} statements detected)"
            )
            return ValidationResult(
                is_safe=False,
                reason=(
                    f"Multiple SQL statements detected ({len(parsed)}). "
                    "Only one SELECT statement per query is allowed."
                ),
            )

        for statement in parsed:
            if not isinstance(statement, exp.Select):
                stmt_type = type(statement).__name__
                return ValidationResult(
                    is_safe=False,
                    reason=f"Only SELECT statements are allowed. Got: {stmt_type}",
                )
    except Exception as e:
        warnings.append(f"AST parse warning: {str(e)[:100]}")

    return ValidationResult(is_safe=True, warnings=warnings)