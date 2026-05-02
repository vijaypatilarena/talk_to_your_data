"""
ui/streamlit_app.py — Talk to Your Data
Agentic NL→SQL interface using Claude Agent SDK.

Run: streamlit run ui/streamlit_app.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from datetime import datetime

from app.config import config
from app.database import test_connection, init_pool
from app.agent.agent_runner import run_agent

#Configuration
st.set_page_config(
    page_title="Talk to Your Data",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

#Defining CSS for the app
st.markdown("""
<style>
    .tool-card {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 8px;
        padding: 10px 14px;
        margin: 4px 0;
        font-family: 'Courier New', monospace;
        font-size: 12px;
        color: #cdd6f4;
    }
    .tool-classify  { border-left: 3px solid #89b4fa; }
    .tool-generate  { border-left: 3px solid #a6e3a1; }
    .tool-execute   { border-left: 3px solid #fab387; }
    .tool-retry     { border-left: 3px solid #f38ba8; }
    .tool-success   { color: #a6e3a1; }
    .tool-fail      { color: #f38ba8; }
    .metric-pill {
        display: inline-block;
        background: #313244;
        border-radius: 12px;
        padding: 2px 10px;
        font-size: 12px;
        color: #cdd6f4;
        margin: 2px 3px;
    }
    .answer-block {
        background: #1e1e2e;
        border-left: 3px solid #89b4fa;
        padding: 12px 16px;
        border-radius: 0 8px 8px 0;
        margin: 8px 0;
    }
    .security-badge {
        background: #f38ba8;
        color: #1e1e2e;
        border-radius: 4px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

#Session state
def init_session():
    defaults = {
        "messages":             [],
        "conversation_history": [],
        "session_tokens":       0,
        "session_cost_usd":     0.0,
        "session_queries":      0,
        "session_retries":      0,
        "session_successes":    0,
        "db_connected":         False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session()

#  Tenant options 
TENANTS = {
    "Brennwald (1004 · DE)":          ("1004", "Brennwald"),
    "Lindauer (1010 · LU)":           ("1010", "Lindauer"),
    "Renaux Belgium (1032 · BE)":     ("1032", "Renaux Belgium"),
    "Renaux Luxembourg (1031 · LU)":  ("1031", "Renaux Luxembourg"),
}

# Sidebar 
with st.sidebar:
    st.title("⚙️ Settings")

    # Tenant
    st.subheader("Active Company")
    tenant_label = st.selectbox("Tenant", list(TENANTS.keys()), index=0)
    active_vkorg, active_name = TENANTS[tenant_label]

    st.divider()

    # DB Connection
    st.subheader("Database")
    if st.button("🔌 Test Connection", use_container_width=True):
        with st.spinner("Connecting..."):
            try:
                init_pool()
                ok = test_connection()
                st.session_state.db_connected = ok
            except Exception as e:
                st.error(f"Failed: {e}")
                st.session_state.db_connected = False

    if st.session_state.db_connected:
        st.success("✅ PostgreSQL Connected")
    else:
        st.warning("⚠️ Not connected")

    st.divider()

    # Session metrics
    st.subheader("📊 Session Metrics")

    total    = st.session_state.session_queries
    success  = st.session_state.session_successes
    retries  = st.session_state.session_retries
    rate     = f"{(success/total*100):.0f}%" if total > 0 else "—"

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Queries",    total)
        st.metric("Tokens",     f"{st.session_state.session_tokens:,}")
    with col2:
        st.metric("Success",    rate)
        st.metric("Cost (est)", f"${st.session_state.session_cost_usd:.4f}")

    if retries > 0:
        st.info(f"🔄 Self-corrections used: {retries}")

    st.divider()

    # Info
    st.caption(f"Model: `{config.ANTHROPIC_MODEL}`")
    st.caption(f"Row limit: `{config.MAX_ROWS_RETURNED}`")
    st.caption("Architecture: **Agent SDK**")

    if st.button("🗑️ Clear Session", use_container_width=True):
        for k in ["messages", "conversation_history",
                  "session_tokens", "session_cost_usd",
                  "session_queries", "session_retries", "session_successes"]:
            st.session_state[k] = [] if isinstance(st.session_state[k], list) else 0
        st.rerun()


#  Main header 
st.caption(
    f"Tenant: **{active_name}** (VKORG `{active_vkorg}`) · "
    f"Architecture: **Claude Agent SDK** · "
    f"Model: `{config.ANTHROPIC_MODEL}`"
)

#  Empty state example questions 
if not st.session_state.messages:
    st.markdown("### 💡 Try asking...")
    examples = [
        "Who are our top 5 customers by spend?",
        "Which customers haven't ordered in over 60 days?",
        "How many customers need attention right now?",
        "What's our total revenue this year?",
        "Give me a breakdown of customers by health status",
        "Which sales rep has the most customers?",
        "Show me customers with declining spend",
        "What's the average order value this quarter?",
    ]
    c1, c2 = st.columns(2)
    for i, ex in enumerate(examples):
        with (c1 if i % 2 == 0 else c2):
            if st.button(ex, key=f"ex_{i}", use_container_width=True):
                st.session_state["prefill"] = ex
                st.rerun()


# Message renderer 
def render_message(msg: dict):
    """Render a single assistant message with full agent trace."""
    st.markdown(msg["content"])

    trace = msg.get("trace")
    if not trace:
        return

    with st.expander("🔬 Agent Trace", expanded=False):
        tu          = trace.get("token_usage", {})
        total_tok   = tu.get("total", 0)
        cost        = tu.get("estimated_cost_usd", 0.0)
        latency     = trace.get("latency_ms", 0)
        iterations  = trace.get("iterations", 0)
        retries     = trace.get("retry_count", 0)

        # Top metrics row
        st.markdown(
            f'<span class="metric-pill">🪙 {total_tok:,} tokens</span>'
            f'<span class="metric-pill">💰 ${cost:.4f}</span>'
            f'<span class="metric-pill">⏱️ {latency}ms</span>'
            f'<span class="metric-pill">🔁 {iterations} iterations</span>'
            + (f'<span class="metric-pill">🔧 {retries} retries</span>' if retries > 0 else ""),
            unsafe_allow_html=True,
        )

        st.markdown("")

        # Tool call timeline
        tool_calls = trace.get("tool_calls", [])
        if tool_calls:
            st.markdown("**🛠️ Tool Call Sequence**")
            for i, tc in enumerate(tool_calls):
                name    = tc.get("tool", tc.get("tool_name", ""))
                success = tc.get("success", True)
                status  = "✅" if success else "❌"

                css_class = {
                    "classify_question": "tool-classify",
                    "generate_sql":      "tool-generate",
                    "execute_sql":       "tool-execute",
                    "retry_sql":         "tool-retry",
                }.get(name, "")

                label = {
                    "classify_question": "① Classify Question",
                    "generate_sql":      "② Generate SQL",
                    "execute_sql":       "③ Execute SQL",
                    "retry_sql":         "④ Self-Correct SQL",
                }.get(name, name)

                st.markdown(
                    f'<div class="tool-card {css_class}">'
                    f'{status} <strong>{label}</strong>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("")

        # Classification
        classification = trace.get("classification", "")
        reasoning      = trace.get("classification_reasoning", "")
        if classification:
            st.markdown("**Stage 1 — Classification**")
            icon = "🟢" if classification == "DATA_QUESTION" else "🔵"
            st.markdown(
                f'<div class="tool-card tool-classify">'
                f'{icon} <strong>{classification}</strong>'
                + (f' — {reasoning}' if reasoning else "") +
                f'</div>',
                unsafe_allow_html=True,
            )

        # Generated SQL
        gen_sql     = trace.get("generated_sql", "")
        sql_reason  = trace.get("sql_reasoning", "")
        query_type  = trace.get("query_type", "")
        if gen_sql:
            st.markdown("**Stage 2 — Generated SQL**")
            if sql_reason:
                st.caption(f"Reasoning: {sql_reason}")
            if query_type:
                st.caption(f"Query type: `{query_type}` ({'pre-aggregated snapshot' if query_type == 'snapshot' else 'raw order data'})")
            st.code(gen_sql, language="sql")

        # Tenant modification notice
        if trace.get("tenant_modified"):
            st.markdown(
                '<span class="security-badge">🔒 TENANT FILTER INJECTED</span> '
                'Tenant scoping was enforced by the security layer.',
                unsafe_allow_html=True,
            )

        # Execution result
        results_summary = trace.get("query_results_summary", "")
        final_sql       = trace.get("final_sql", "")
        if results_summary:
            st.markdown("**Stage 3 — Execution Result**")
            st.markdown(
                f'<div class="tool-card tool-execute">✅ {results_summary}</div>',
                unsafe_allow_html=True,
            )
            if final_sql and final_sql != gen_sql:
                with st.expander("Final SQL (after security + limit injection)"):
                    st.code(final_sql, language="sql")

        # Security block
        blocked = trace.get("blocked_reason", "")
        if blocked:
            st.error(f"🚫 Security Block: {blocked}")

        # Error
        error = trace.get("error", "")
        if error and not trace.get("success"):
            st.markdown(
                f'<div class="tool-card tool-retry">❌ {error}</div>',
                unsafe_allow_html=True,
            )

        # Retry notice
        if retries > 0:
            st.warning(f"⚠️ Agent used self-correction loop: {retries} retry attempt(s)")

        # Token breakdown table
        if total_tok > 0:
            st.markdown("**Token Breakdown**")
            st.table({
                "Metric":       ["Input tokens", "Output tokens", "Total tokens", "Est. cost (USD)"],
                "Value":        [
                    f"{tu.get('total_input', 0):,}",
                    f"{tu.get('total_output', 0):,}",
                    f"{total_tok:,}",
                    f"${cost:.4f}",
                ],
            })


# Chat history 
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["content"])
        else:
            render_message(msg)


# Chat input 
prefill    = st.session_state.pop("prefill", "")
user_input = st.chat_input(
    placeholder=f"Ask {active_name} a business question...",
) or prefill

if user_input:
    # Rate limit
    if st.session_state.session_queries >= config.RATE_LIMIT_PER_SESSION:
        st.error(f"Session limit of {config.RATE_LIMIT_PER_SESSION} queries reached. Clear session to continue.")
        st.stop()

    # Show user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Run agent
    with st.chat_message("assistant"):
        with st.spinner("Agent thinking..."):
            try:
                init_pool()

                result = run_agent(
                    user_question=user_input,
                    conversation_history=st.session_state.conversation_history,
                    tenant_vkorg=active_vkorg,
                    tenant_name=active_name,
                )

                trace = result.trace
                tu    = trace.token_usage

                # Display answer
                st.markdown(result.answer)

                # Build trace dict for storage
                trace_dict = {
                    "classification":           trace.classification,
                    "classification_reasoning": trace.classification_reasoning,
                    "generated_sql":            trace.generated_sql,
                    "sql_reasoning":            trace.sql_reasoning,
                    "query_type":               trace.query_type,
                    "final_sql":                trace.final_sql,
                    "tenant_modified":          trace.tenant_modified,
                    "query_results_summary":    trace.query_results_summary,
                    "error":                    trace.error,
                    "blocked_reason":           trace.blocked_reason,
                    "retry_count":              trace.retry_count,
                    "iterations":               trace.iterations,
                    "latency_ms":               trace.latency_ms,
                    "success":                  trace.success,
                    "row_count":                trace.row_count,
                    "truncated":                trace.truncated,
                    "tool_calls": [
                        {
                            "tool":    tc.tool_name,
                            "success": tc.success,
                        }
                        for tc in trace.tool_calls
                    ],
                    "token_usage": {
                        "total_input":        tu.total_input,
                        "total_output":       tu.total_output,
                        "total":              tu.total,
                        "estimated_cost_usd": tu.estimated_cost_usd,
                    },
                }

                # Show trace expander inline
                with st.expander("🔬 Agent Trace", expanded=False):
                    render_message({
                        "role":    "assistant",
                        "content": result.answer,
                        "trace":   trace_dict,
                    })

                # Update session state
                st.session_state.session_tokens    += tu.total
                st.session_state.session_cost_usd  += tu.estimated_cost_usd
                st.session_state.session_queries   += 1
                st.session_state.session_retries   += trace.retry_count
                if trace.success:
                    st.session_state.session_successes += 1

                st.session_state.messages.append({
                    "role":    "assistant",
                    "content": result.answer,
                    "trace":   trace_dict,
                })

                # Update conversation history for follow-ups
                st.session_state.conversation_history.extend([
                    {"role": "user",      "content": user_input},
                    {"role": "assistant", "content": result.answer},
                ])

                st.rerun()

            except Exception as e:
                error_msg = f"Error: {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({
                    "role":    "assistant",
                    "content": error_msg,
                })