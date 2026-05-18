"""
PRIMARY Agentic Pipeline

This is the main pipeline. Uses Claude's native tool use (Agent SDK).
The agent autonomously decides when to classify, generate SQL,
execute, and self-correct — we don't hardcode the order.

Tool call sequence the agent follows:
  classify_question → generate_sql → execute_sql → [retry_sql if needed] → respond

All tool executions are handled on our side with full security enforcement.
Every step is traced and logged.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import anthropic

from app.config import config, get_model_pricing
from app.agent.tools import AGENT_TOOLS, get_agent_system_prompt
from app.security.sql_executor import execute_safe_query
from app.logger import log_pipeline_event

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


#  Data classes 

@dataclass
class TokenUsage:
    total_input: int = 0
    total_output: int = 0

    @property
    def total(self) -> int:
        return self.total_input + self.total_output

    @property
    def estimated_cost_usd(self) -> float:
        in_rate, out_rate = get_model_pricing(config.ANTHROPIC_MODEL)
        return (self.total_input * in_rate + self.total_output * out_rate) / 1_000_000

    def to_dict(self) -> dict:
        return {
            "total_input":        self.total_input,
            "total_output":       self.total_output,
            "total":              self.total,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


@dataclass
class ToolCallRecord:
    """Records a single tool call for UI display."""
    tool_name: str
    input: dict
    output: dict
    success: bool
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class AgentTrace:
    """Full trace of the agent's reasoning and tool calls — shown in UI."""
    question: str = ""
    classification: str = ""
    classification_reasoning: str = ""
    generated_sql: str = ""
    sql_reasoning: str = ""
    query_type: str = ""
    final_sql: str = ""
    tenant_modified: bool = False
    row_limit_injected: bool = False
    query_results_summary: str = ""
    answer: str = ""
    error: str = ""
    blocked_reason: str = ""
    retry_count: int = 0
    iterations: int = 0
    latency_ms: int = 0
    timestamp: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    success: bool = False
    row_count: int = 0
    truncated: bool = False


@dataclass
class AgentResult:
    answer: str
    trace: AgentTrace
    success: bool
    error: str = ""


#  Main agent pipeline 

def run_agent(
    user_question: str,
    conversation_history: list[dict] = None,
    tenant_vkorg: str = None,
    tenant_name: str = None,
) -> AgentResult:
    """
    PRIMARY PIPELINE — Agentic NL→SQL using Claude tool use.

    The agent autonomously:
    1. Classifies the question
    2. Generates SQL if needed
    3. Executes the SQL 
    4. Self-corrects if execution fails
    5. Returns a natural language answer

    Args:
        user_question:        Plain English business question
        conversation_history: Prior turns for follow-up support
        tenant_vkorg:         Active tenant VKORG code
        tenant_name:          Active tenant display name
    """
    start_time = time.time()
    vkorg      = tenant_vkorg or config.ACTIVE_TENANT_VKORG
    company    = tenant_name  or config.ACTIVE_TENANT_NAME

    trace       = AgentTrace(
        question=user_question,
        timestamp=datetime.now().isoformat(),
    )
    token_usage = TokenUsage()
    retry_count = 0

    # Build message history
    messages = []
    if conversation_history:
        messages.extend(conversation_history[-6:])
    messages.append({"role": "user", "content": user_question})

    logger.info(f"Agent start | '{user_question[:80]}' | tenant: {vkorg}")

    #  Agentic loop 
    max_iterations = 10
    iteration      = 0

    while iteration < max_iterations:
        iteration += 1
        trace.iterations = iteration

        # Call Claude
        response = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=2000,
            system=get_agent_system_prompt(vkorg=vkorg, tenant_name=company),
            tools=AGENT_TOOLS,
            messages=messages,
        )

        # Track tokens every iteration
        token_usage.total_input  += response.usage.input_tokens
        token_usage.total_output += response.usage.output_tokens

#Agent finished  gave final answer   
        if response.stop_reason == "end_turn":
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text

            trace.answer      = final_text.strip()
            trace.success     = True
            trace.token_usage = token_usage
            trace.retry_count = retry_count
            trace.latency_ms  = int((time.time() - start_time) * 1000)

            _write_log(trace, vkorg, company)

            logger.info(
                f"Agent complete | iterations={iteration} | "
                f"tokens={token_usage.total} | "
                f"cost=${token_usage.estimated_cost_usd:.4f} | "
                f"latency={trace.latency_ms}ms"
            )
            return AgentResult(answer=trace.answer, trace=trace, success=True)

        # Agent is calling tools 
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name  = block.name
                tool_input = block.input
                tool_id    = block.id

                logger.info(f"Agent calling tool: {tool_name}")

                # Execute the tool
                result_content, tool_record = _execute_tool(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    vkorg=vkorg,
                    trace=trace,
                )

                # Track retries
                if tool_name == "retry_sql":
                    retry_count += 1
                    trace.retry_count = retry_count

                trace.tool_calls.append(tool_record)

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tool_id,
                    "content":     result_content,
                })

            # Feed results back to agent
            messages.append({"role": "user", "content": tool_results})

        else:
            logger.warning(f"Unexpected stop reason: {response.stop_reason}")
            break

    # Loop exhausted without answer
    error = "Agent did not complete within the allowed steps. Please try rephrasing."
    trace.error       = error
    trace.success     = False
    trace.token_usage = token_usage
    trace.retry_count = retry_count
    trace.latency_ms  = int((time.time() - start_time) * 1000)
    _write_log(trace, vkorg, company)

    return AgentResult(answer=error, trace=trace, success=False, error=error)


# Tool execution handlers 

def _execute_tool(
    tool_name: str,
    tool_input: dict,
    vkorg: str,
    trace: AgentTrace,
) -> tuple[str, ToolCallRecord]:
    """
    Execute a tool called by the agent.
    Returns (result_json_string, ToolCallRecord)
    """

    #  classify_question    
    if tool_name == "classify_question":
        classification = tool_input.get("classification", "DATA_QUESTION")
        reasoning      = tool_input.get("reasoning", "")

        trace.classification          = classification
        trace.classification_reasoning = reasoning

        logger.info(f"Classification: {classification} | {reasoning}")

        output = {"classification": classification, "reasoning": reasoning}
        return (
            json.dumps(output),
            ToolCallRecord(tool_name=tool_name, input=tool_input, output=output, success=True),
        )

    # generate_sql 
    elif tool_name == "generate_sql":
        sql        = tool_input.get("sql", "")
        query_type = tool_input.get("query_type", "raw")
        reasoning  = tool_input.get("reasoning", "")

        trace.generated_sql = sql
        trace.query_type    = query_type
        trace.sql_reasoning = reasoning

        logger.info(f"SQL generated (type={query_type}): {sql[:150]}")

        output = {
            "sql":        sql,
            "query_type": query_type,
            "status":     "ready_to_execute",
        }
        return (
            json.dumps(output),
            ToolCallRecord(tool_name=tool_name, input=tool_input, output=output, success=True),
        )

    #  execute_sql 
    elif tool_name == "execute_sql":
        sql = tool_input.get("sql", "")
        ex  = execute_safe_query(sql=sql, tenant_vkorg=vkorg)

        trace.final_sql          = ex.final_sql
        trace.tenant_modified    = ex.tenant_modified
        trace.row_limit_injected = ex.row_limit_injected

        if ex.blocked_reason:
            trace.blocked_reason = ex.blocked_reason
            output = {
                "success":   False,
                "error":     ex.blocked_reason,
                "blocked":   True,
                "rows":      [],
                "row_count": 0,
            }
            return (
                json.dumps(output),
                ToolCallRecord(
                    tool_name=tool_name, input=tool_input, output=output, success=False
                ),
            )

        if not ex.success:
            trace.query_results_summary = f"Error: {ex.error}"
            output = {
                "success":   False,
                "error":     ex.error,
                "rows":      [],
                "row_count": 0,
            }
            return (
                json.dumps(output),
                ToolCallRecord(
                    tool_name=tool_name, input=tool_input, output=output, success=False
                ),
            )

        trace.row_count             = ex.row_count
        trace.truncated             = ex.truncated
        trace.query_results_summary = (
            f"{ex.row_count} rows returned"
            + (" (truncated at 500)" if ex.truncated else "")
        )

        output = {
            "success":   True,
            "row_count": ex.row_count,
            "truncated": ex.truncated,
            "columns":   ex.columns,
            "rows":      ex.rows[:50],   # Send max 50 rows to agent
        }
        return (
            json.dumps(output, default=str),
            ToolCallRecord(
                tool_name=tool_name, input=tool_input,
                output={"success": True, "row_count": ex.row_count, "truncated": ex.truncated},
                success=True,
            ),
        )

    #  retry_sql
    elif tool_name == "retry_sql":
        fixed_sql     = tool_input.get("fixed_sql", "")
        error_message = tool_input.get("error_message", "")

        # Hard cap on retries — stops a misbehaving agent from looping forever.
        # trace.retry_count is incremented AFTER _execute_tool returns, so this
        # value is the count of retries ALREADY USED.
        if trace.retry_count >= config.MAX_RETRIES:
            logger.warning(
                f"Retry blocked: limit ({config.MAX_RETRIES}) reached"
            )
            output = {
                "success": False,
                "error": (
                    f"Maximum retry limit ({config.MAX_RETRIES}) reached. "
                    "Stop calling retry_sql and report the failure to the user."
                ),
                "retry_limit_hit": True,
            }
            return (
                json.dumps(output),
                ToolCallRecord(
                    tool_name=tool_name, input=tool_input, output=output, success=False
                ),
            )

        logger.info(f"Agent self-correcting SQL | error: {error_message[:100]}")

        # Execute the fixed SQL
        ex = execute_safe_query(sql=fixed_sql, tenant_vkorg=vkorg)

        if ex.success:
            trace.generated_sql         = fixed_sql
            trace.final_sql             = ex.final_sql
            trace.tenant_modified       = ex.tenant_modified
            trace.row_limit_injected    = ex.row_limit_injected
            trace.row_count             = ex.row_count
            trace.truncated             = ex.truncated
            trace.query_results_summary = (
                f"{ex.row_count} rows returned (after self-correction)"
                + (" (truncated)" if ex.truncated else "")
            )
            output = {
                "success":    True,
                "row_count":  ex.row_count,
                "truncated":  ex.truncated,
                "columns":    ex.columns,
                "rows":       ex.rows[:50],
            }
        else:
            output = {
                "success": False,
                "error":   ex.error or ex.blocked_reason,
            }

        return (
            json.dumps(output, default=str),
            ToolCallRecord(
                tool_name=tool_name, input=tool_input,
                output={"success": ex.success, "retry_for_error": error_message[:100]},
                success=ex.success,
            ),
        )

    else:
        output = {"error": f"Unknown tool: {tool_name}"}
        return (
            json.dumps(output),
            ToolCallRecord(tool_name=tool_name, input=tool_input, output=output, success=False),
        )


#  Logging          

def _write_log(trace: AgentTrace, vkorg: str, company: str):
    try:
        log_pipeline_event(
            question=trace.question,
            tenant_vkorg=vkorg,
            tenant_name=company,
            classification=trace.classification,
            generated_sql=trace.generated_sql,
            final_sql=trace.final_sql,
            execution_success=trace.success,
            row_count=trace.row_count,
            truncated=trace.truncated,
            answer=trace.answer,
            token_usage=trace.token_usage.to_dict(),
            latency_ms=trace.latency_ms,
            retry_count=trace.retry_count,
            error=trace.error,
            pipeline_mode="Agent SDK",
            tenant_modified=trace.tenant_modified,
            tool_calls=[
                {
                    "tool":    tc.tool_name,
                    "success": tc.success,
                    "time":    tc.timestamp,
                }
                for tc in trace.tool_calls
            ],
            blocked_reason=trace.blocked_reason,
        )
    except Exception as e:
        logger.error(f"Failed to write pipeline log: {e}")