"""
 Agent Tool Definitions

Four tools the agent can call:
  1. classify_question   — data question or general chat?
  2. generate_sql        — NL → SQL
  3. execute_sql         — run the query safely
  4. retry_sql           — self-correct a failed query

The agent decides which tools to call and in what order.
"""

from app.config import config
from app.schema_context import get_schema_prompt, get_schema_summary

AGENT_TOOLS = [
    {
        "name": "classify_question",
        "description": (
            "Classify whether a user question requires a database query "
            "or is general conversation. ALWAYS call this first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "classification": {
                    "type": "string",
                    "enum": ["DATA_QUESTION", "GENERAL_CHAT"],
                    "description": "DATA_QUESTION if SQL needed, GENERAL_CHAT if conversational",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief reason for this classification",
                },
            },
            "required": ["classification", "reasoning"],
        },
    },
    {
        "name": "generate_sql",
        "description": (
            "Generate a SQL SELECT query to answer a business question. "
            "Only call after classify_question returns DATA_QUESTION. "
            "Query MUST filter order_headers by the active tenant sales_org_code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        "Valid PostgreSQL SELECT statement. "
                        "Raw SQL only — no markdown, no code fences. "
                        f"Always filter by sales_org_code = '{config.ACTIVE_TENANT_VKORG}'. "
                        f"Always include LIMIT {config.MAX_ROWS_RETURNED}."
                    ),
                },
                "query_type": {
                    "type": "string",
                    "enum": ["snapshot", "raw"],
                    "description": (
                        "snapshot = uses daily_metrics_snapshots (fast, current KPIs). "
                        "raw = uses order_headers/customers (flexible, historical)."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you chose this SQL approach",
                },
            },
            "required": ["sql", "query_type", "reasoning"],
        },
    },
    {
        "name": "execute_sql",
        "description": (
            "Execute a SQL query against the database. "
            "Always call after generate_sql to get actual results. "
            "Security validation and tenant scoping are enforced automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL SELECT statement to execute",
                },
            },
            "required": ["sql"],
        },
    },
    {
        "name": "retry_sql",
        "description": (
            "Fix and retry a failed SQL query. "
            "Call this when execute_sql returns an error. "
            "Provide the original question, failed SQL, and the error message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "original_question": {
                    "type": "string",
                    "description": "The original user question",
                },
                "failed_sql": {
                    "type": "string",
                    "description": "The SQL that failed",
                },
                "error_message": {
                    "type": "string",
                    "description": "The error returned by execute_sql",
                },
                "fixed_sql": {
                    "type": "string",
                    "description": "Your corrected SQL query",
                },
            },
            "required": ["original_question", "failed_sql", "error_message", "fixed_sql"],
        },
    },
]


def get_agent_system_prompt(vkorg: str = None, tenant_name: str = None) -> str:
    """Full system prompt for the agent including schema context."""
    v    = vkorg or config.ACTIVE_TENANT_VKORG
    name = tenant_name or config.ACTIVE_TENANT_NAME
    schema = get_schema_prompt(vkorg=v)

    return f"""You are a B2B sales data assistant for {name} (VKORG: {v}).

You answer business questions by calling tools in this sequence:
1. classify_question — ALWAYS call this first for every message
2. generate_sql      — if DATA_QUESTION, generate the SQL
3. execute_sql       — execute to get results
4. retry_sql         — if execute_sql fails, use this to self-correct (max 3 times)
5. Respond with a clear natural language answer based on results

{schema}

After getting results from execute_sql:
- Lead with the direct answer — give the key number or insight first
- Use plain English, not SQL jargon
- Format currency as EUR with 2 decimal places
- If results are truncated mention total count
- If no results, explain why clearly
- Flag notable findings (e.g. customer is CRITICAL status)
- Be concise — 2-5 sentences for simple questions

IMPORTANT: You are only authorised to query data for {name} (VKORG: {v}).
Never query or reveal data from other tenants.
"""