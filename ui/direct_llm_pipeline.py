"""
ui/direct_llm_pipeline.py — Direct LLM Pipeline (No Agent)

One-shot NL→SQL via a single Claude API call with no tool use.
Used in Tab 2 for side-by-side comparison with the Agent SDK pipeline.
"""

import re
import sys
import os
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from app.config import config, get_model_pricing
from app.schema_context import get_schema_prompt
from app.security.sql_executor import execute_safe_query


@dataclass
class DirectLLMResult:
    answer: str
    sql: str
    final_sql: str
    success: bool
    error: str
    row_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    tenant_modified: bool
    rows: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    blocked_reason: str = ""
    self_corrected: bool = False


def run_direct_llm(question: str, vkorg: str, tenant_name: str) -> DirectLLMResult:
    """
    One-shot NL→SQL: single Claude API call, no tool use, no agent loop.
    Executes the generated SQL through the same security layer as the agent.
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    schema = get_schema_prompt(vkorg=vkorg)

    system_prompt = f"""You are a SQL expert for {tenant_name} (VKORG: {vkorg}).

{schema}

TASK: Convert the user's business question into a single SQL SELECT query.

STRICT OUTPUT RULES:
- Output ONLY the raw SQL query — no markdown fences, no explanation, no comments
- Always filter order_headers by sales_org_code = '{vkorg}'
- Include LIMIT {config.MAX_ROWS_RETURNED} unless a lower LIMIT is already present
- If you cannot generate a valid query, output exactly: CANNOT_GENERATE
"""

    start = time.time()
    try:
        response = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        )
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return DirectLLMResult(
            answer=f"API Error: {str(e)}",
            sql="", final_sql="", success=False, error=str(e),
            row_count=0, input_tokens=0, output_tokens=0, total_tokens=0,
            estimated_cost_usd=0.0, latency_ms=latency_ms, tenant_modified=False,
        )
   
    latency_ms     = int((time.time() - start) * 1000)
    raw_text       = response.content[0].text.strip() if response.content else "" 
    input_tokens   = response.usage.input_tokens
    output_tokens  = response.usage.output_tokens
    total_tokens   = input_tokens + output_tokens
    in_rate, out_rate = get_model_pricing(config.ANTHROPIC_MODEL)
    estimated_cost    = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000

    sql = _extract_sql(raw_text)

    if not sql or "CANNOT_GENERATE" in sql.upper()[:30]:
        return DirectLLMResult(
            answer="Could not generate SQL for this question.",
            sql=raw_text, final_sql="", success=False, error="No SQL generated",
            row_count=0, input_tokens=input_tokens, output_tokens=output_tokens,
            total_tokens=total_tokens, estimated_cost_usd=estimated_cost,
            latency_ms=latency_ms, tenant_modified=False,
        )

    ex = execute_safe_query(sql=sql, tenant_vkorg=vkorg)

    if ex.blocked_reason:
        return DirectLLMResult(
            answer=f"Security blocked: {ex.blocked_reason}",
            sql=sql, final_sql=ex.final_sql, success=False,
            error=ex.blocked_reason, row_count=0,
            input_tokens=input_tokens, output_tokens=output_tokens,
            total_tokens=total_tokens, estimated_cost_usd=estimated_cost,
            latency_ms=latency_ms, tenant_modified=ex.tenant_modified,
            blocked_reason=ex.blocked_reason,
        )

    if not ex.success:
        return DirectLLMResult(
            answer=f"SQL execution error: {ex.error}",
            sql=sql, final_sql=ex.final_sql, success=False, error=ex.error,
            row_count=0, input_tokens=input_tokens, output_tokens=output_tokens,
            total_tokens=total_tokens, estimated_cost_usd=estimated_cost,
            latency_ms=latency_ms, tenant_modified=ex.tenant_modified,
        )

    answer_parts = [f"Query returned {ex.row_count} row(s)."]
    if ex.truncated:
        answer_parts.append(f"(truncated at {config.MAX_ROWS_RETURNED})")

    return DirectLLMResult(
        answer=" ".join(answer_parts),
        sql=sql, final_sql=ex.final_sql, success=True, error="",
        row_count=ex.row_count,
        input_tokens=input_tokens, output_tokens=output_tokens,
        total_tokens=total_tokens, estimated_cost_usd=estimated_cost,
        latency_ms=latency_ms, tenant_modified=ex.tenant_modified,
        rows=ex.rows, columns=ex.columns,
    )


def _extract_sql(text: str) -> str:
    fence = re.search(r'```(?:sql)?\s*(.*?)```', text, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    stripped = text.strip()
    upper = stripped.upper()
    if upper.startswith("SELECT") or upper.startswith("WITH"):
        return stripped
    sel = re.search(r'((?:SELECT|WITH)\s+.+)', stripped, re.DOTALL | re.IGNORECASE)
    if sel:
        return sel.group(1).strip()
    return stripped
