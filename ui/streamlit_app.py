"""
ui/streamlit_app.py — Talk to Your Data: Research Demonstration Interface
Architecture: Claude Agent SDK + PostgreSQL + Streamlit

Tabs: Chat | Comparison | Security | Evaluation | Logs
Run:  streamlit run ui/streamlit_app.py
"""

import sys, os, re, time, io, difflib
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UI   = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _UI)

import streamlit as st
import pandas as pd

from app.config import config
from app.database import test_connection, init_pool
from app.agent.agent_runner import run_agent
from app.security.tenant_guard import enforce_tenant_scope
from app.logger import load_pipeline_logs, load_security_logs
from evaluation.test_questions import TEST_QUESTIONS

try:
    import sqlglot
    import sqlglot.expressions as exp
    HAS_SQLGLOT = True
except ImportError:
    HAS_SQLGLOT = False

try:
    from direct_llm_pipeline import run_direct_llm
    HAS_DIRECT = True
except ImportError:
    HAS_DIRECT = False

# ═══════════════════════════════════════════════════════════════════════════════
#  Page Config
# ═══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Talk to Your Data · Research Demo",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════════
TENANTS = {
    "Brennwald (1004 · DE)":         ("1004", "Brennwald"),
    "Lindauer (1010 · LU)":          ("1010", "Lindauer"),
    "Renaux Belgium (1032 · BE)":    ("1032", "Renaux Belgium"),
    "Renaux Luxembourg (1031 · LU)": ("1031", "Renaux Luxembourg"),
}

TOOL_LABEL = {
    "classify_question": "Classify",
    "generate_sql":      "Generate SQL",
    "execute_sql":       "Execute",
    "retry_sql":         "Self-Correct",
    "answer":            "Answer",
}

TOOL_ICON = {
    "classify_question": "🔍",
    "generate_sql":      "⚙️",
    "execute_sql":       "▶️",
    "retry_sql":         "↩️",
    "answer":            "💬",
}

TOOL_CSS = {
    "classify_question": "fn-classify",
    "generate_sql":      "fn-generate",
    "execute_sql":       "fn-execute",
    "retry_sql":         "fn-retry",
    "answer":            "fn-answer",
}

ATTACK_DEMOS = [
    {
        "id": "ATK-01", "label": "DELETE Attack",
        "sql": "DELETE FROM customers WHERE 1=1",
        "description": "Attempts to delete all customer records",
        "expect": "blocked",
    },
    {
        "id": "ATK-02", "label": "DROP TABLE",
        "sql": "DROP TABLE order_headers",
        "description": "Attempts to destroy the order_headers table",
        "expect": "blocked",
    },
    {
        "id": "ATK-03", "label": "Semicolon Injection",
        "sql": "SELECT * FROM customers; DROP TABLE orders",
        "description": "Injects a second destructive statement after a valid SELECT",
        "expect": "blocked",
    },
    {
        "id": "ATK-04", "label": "Cross-Tenant Access",
        "sql": "SELECT * FROM order_headers WHERE sales_org_code = '9999'",
        "description": "Attempts to read another tenant's data (VKORG 9999)",
        "expect": "modified",
        "is_tenant": True,
    },
    {
        "id": "ATK-05", "label": "System Schema Probe",
        "sql": "SELECT table_name FROM information_schema.tables",
        "description": "Tries to enumerate the database schema",
        "expect": "warn",
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
#  CSS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
/* ── Core tool cards ── */
.tool-card {
    background:#1e1e2e; border:1px solid #313244; border-radius:8px;
    padding:10px 14px; margin:4px 0;
    font-family:'Courier New',monospace; font-size:12px; color:#cdd6f4;
}
.tool-classify{border-left:4px solid #89b4fa;}
.tool-generate{border-left:4px solid #a6e3a1;}
.tool-execute {border-left:4px solid #fab387;}
.tool-retry   {border-left:4px solid #f38ba8;}
.tool-answer  {border-left:4px solid #cba6f7;}

/* ── Metric pill ── */
.metric-pill{
    display:inline-block; background:#313244; border-radius:12px;
    padding:2px 10px; font-size:12px; color:#cdd6f4; margin:2px 3px;
}

/* ── Flow diagram ── */
.flow-container{
    display:flex; align-items:flex-start; gap:4px; flex-wrap:wrap;
    padding:12px 14px; background:#181825; border-radius:10px;
    border:1px solid #313244; margin:8px 0; overflow-x:auto;
}
.flow-node{
    background:#1e1e2e; border:2px solid #45475a; border-radius:8px;
    padding:8px 10px; min-width:88px; text-align:center;
    font-size:11px; color:#cdd6f4; flex-shrink:0;
}
.flow-node-icon {font-size:16px;}
.flow-node-name {font-weight:bold; font-size:11px; margin:2px 0;}
.flow-node-detail{font-size:10px; color:#a6adc8; word-break:break-word; max-width:120px;}
.flow-node-time {font-size:9px; color:#585b70; margin-top:2px;}
.fn-question{border-color:#74c7ec;}
.fn-classify{border-color:#89b4fa;}
.fn-generate{border-color:#a6e3a1;}
.fn-execute {border-color:#fab387;}
.fn-retry   {border-color:#f38ba8; background:#2a1a1e;}
.fn-answer  {border-color:#cba6f7;}
.fn-thinking{border-color:#f9e2af; opacity:0.6;}
.flow-arrow{color:#585b70; font-size:18px; padding:0 2px; align-self:center; flex-shrink:0;}
.flow-arrow-retry{color:#f38ba8; font-size:18px; align-self:center;}

/* ── Security checkpoints ── */
.checkpoint-row{
    display:flex; align-items:flex-start; gap:10px;
    padding:6px 10px; border-radius:4px; margin:3px 0;
    font-family:'Courier New',monospace; font-size:12px; color:#cdd6f4;
}
.cp-pass{background:#1a2e1a;}
.cp-fail{background:#2e1a1a;}
.cp-warn{background:#2e2a1a;}
.cp-icon{min-width:20px; font-size:13px;}
.cp-phase{flex:1.4; font-weight:bold; color:#cdd6f4;}
.cp-detail{flex:2; font-size:11px; color:#a6adc8;}

/* ── Self-correction box ── */
.correction-box{
    background:#1e1020; border:2px solid #f38ba8; border-radius:8px;
    padding:14px; margin:8px 0; color:#cdd6f4;
}
.correction-title{color:#f38ba8; font-weight:bold; font-size:14px; margin-bottom:8px;}

/* ── Backend flow timeline ── */
.backend-step{
    border-left:2px solid #313244; padding-left:16px; margin-left:8px;
    padding-top:3px; padding-bottom:3px; position:relative;
}
.backend-step::before{
    content:''; width:10px; height:10px; border-radius:50%;
    background:#585b70; position:absolute; left:-6px; top:8px;
}
.bs-done::before    {background:#a6e3a1;}
.bs-blocked::before {background:#f38ba8;}
.bs-modified::before{background:#f9e2af;}

/* ── Badges ── */
.security-badge{background:#f38ba8;color:#1e1e2e;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:bold;}
.pass-badge    {background:#a6e3a1;color:#1e1e2e;border-radius:4px;padding:1px 6px;font-size:11px;}
.fail-badge    {background:#f38ba8;color:#1e1e2e;border-radius:4px;padding:1px 6px;font-size:11px;}
.na-badge      {background:#585b70;color:#cdd6f4;border-radius:4px;padding:1px 6px;font-size:11px;}
.warn-badge    {background:#f9e2af;color:#1e1e2e;border-radius:4px;padding:1px 6px;font-size:11px;}

/* ── Context indicator ── */
.context-indicator{
    background:#1e2a1e; border:1px solid #a6e3a1; border-radius:6px;
    padding:6px 10px; font-size:11px; color:#a6e3a1; margin-bottom:8px;
}

/* ── Log entries ── */
.log-security{background:#2e1a1a;border-left:3px solid #f38ba8;padding:6px 10px;border-radius:0 4px 4px 0;font-family:monospace;font-size:11px;color:#cdd6f4;margin:3px 0;}
.log-retry   {background:#2e2a1a;border-left:3px solid #f9e2af;padding:6px 10px;border-radius:0 4px 4px 0;font-family:monospace;font-size:11px;color:#cdd6f4;margin:3px 0;}
.log-normal  {background:#1e1e2e;border-left:3px solid #313244;padding:6px 10px;border-radius:0 4px 4px 0;font-family:monospace;font-size:11px;color:#cdd6f4;margin:3px 0;}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
#  Session State
# ═══════════════════════════════════════════════════════════════════════════════
_DEFAULTS: dict = {
    "messages":             [],
    "conversation_history": [],
    "session_tokens":       0,
    "session_cost_usd":     0.0,
    "session_queries":      0,
    "session_retries":      0,
    "session_successes":    0,
    "db_connected":         False,
    "last_trace_dict":      None,
    "comparison_llm":       None,
    "comparison_agent":     None,
    "comparison_question":  "",
    "eval_results":         [],
    "token_history":        [],
    "current_query_tokens": {},
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ═══════════════════════════════════════════════════════════════════════════════
#  Helper Utilities
# ═══════════════════════════════════════════════════════════════════════════════

def fmt_ms(ms: int) -> str:
    return f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms}ms"


def _step_duration(tool_calls: list, idx: int) -> str:
    tc = tool_calls[idx]
    if idx + 1 < len(tool_calls):
        nxt = tool_calls[idx + 1]
        try:
            t1 = datetime.fromisoformat(tc.get("timestamp", ""))
            t2 = datetime.fromisoformat(nxt.get("timestamp", ""))
            ms = int((t2 - t1).total_seconds() * 1000)
            return fmt_ms(ms)
        except Exception:
            pass
    return ""


def _tool_detail(tc: dict) -> str:
    tool = tc.get("tool", "")
    inp  = tc.get("input", {})
    out  = tc.get("output", {})
    if tool == "classify_question":
        return out.get("classification", inp.get("classification", ""))
    if tool == "generate_sql":
        sql = inp.get("sql", "")
        return (sql[:45] + "…") if len(sql) > 45 else sql
    if tool == "execute_sql":
        if tc.get("success"):
            return f"{out.get('row_count', 0)} rows"
        return f"Error: {str(out.get('error',''))[:35]}"
    if tool == "retry_sql":
        err = inp.get("error_message", "")
        return (err[:40] + "…") if len(err) > 40 else err
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
#  Flow Diagram
# ═══════════════════════════════════════════════════════════════════════════════

def _flow_node(tool: str, detail: str = "", time_str: str = "", status: str = "done") -> str:
    icon    = TOOL_ICON.get(tool, "•")
    label   = TOOL_LABEL.get(tool, tool)
    css     = TOOL_CSS.get(tool, "")
    s_icon  = "✅" if status == "done" else "❌" if status == "failed" else "⏳"
    d_html  = f'<div class="flow-node-detail">{detail}</div>' if detail else ""
    t_html  = f'<div class="flow-node-time">{time_str}</div>' if time_str else ""
    return (
        f'<div class="flow-node {css}">'
        f'<div class="flow-node-icon">{icon}</div>'
        f'<div class="flow-node-name">{label}</div>'
        f'{d_html}'
        f'<div style="font-size:10px;margin-top:2px;">{s_icon}</div>'
        f'{t_html}'
        f'</div>'
    )


def render_flow(
    tool_calls: list,
    question: str = "",
    answer: str = "",
    show_answer: bool = False,
    thinking: bool = False,
) -> str:
    q_preview = (question[:38] + "…") if len(question) > 38 else question
    nodes = [
        f'<div class="flow-node fn-question">'
        f'<div class="flow-node-icon">❓</div>'
        f'<div class="flow-node-name">Question</div>'
        f'<div class="flow-node-detail">{q_preview}</div>'
        f'</div>'
    ]

    if thinking and not tool_calls:
        nodes.append('<div class="flow-arrow">→</div>')
        nodes.append(
            '<div class="flow-node fn-thinking">'
            '<div class="flow-node-icon">🔍</div>'
            '<div class="flow-node-name">Classify</div>'
            '<div class="flow-node-detail">thinking…</div>'
            '</div>'
        )
    else:
        for i, tc in enumerate(tool_calls):
            tool    = tc.get("tool", "")
            success = tc.get("success", True)
            detail  = _tool_detail(tc)
            t_str   = _step_duration(tool_calls, i)
            status  = "done" if success else "failed"
            arrow   = '<div class="flow-arrow-retry">↩</div>' if tool == "retry_sql" else '<div class="flow-arrow">→</div>'
            nodes.append(arrow)
            nodes.append(_flow_node(tool, detail, t_str, status))

    if show_answer and not thinking:
        a_preview = (answer[:38] + "…") if len(answer) > 38 else answer
        nodes.append('<div class="flow-arrow">→</div>')
        nodes.append(
            f'<div class="flow-node fn-answer">'
            f'<div class="flow-node-icon">💬</div>'
            f'<div class="flow-node-name">Answer</div>'
            f'<div class="flow-node-detail">{a_preview}</div>'
            f'<div style="font-size:10px;margin-top:2px;">✅</div>'
            f'</div>'
        )

    return f'<div class="flow-container">{"".join(nodes)}</div>'


# ═══════════════════════════════════════════════════════════════════════════════
#  Security Checkpoints
# ═══════════════════════════════════════════════════════════════════════════════

_FORBIDDEN = [
    r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b", r"\bDROP\b",
    r"\bALTER\b",  r"\bTRUNCATE\b", r"\bCREATE\b", r"\bGRANT\b",
    r"\bREVOKE\b", r"\bEXECUTE\b",  r"\bEXEC\b",   r"\bCOPY\b",
    r"\bpg_sleep\b",
]
_SUSPICIOUS = [
    r"\bINFORMATION_SCHEMA\b", r"\bpg_catalog\b",
    r"\bpg_tables\b",           r"\bpg_user\b",
]


def run_security_checkpoints(sql: str, vkorg: str) -> list[dict]:
    cps: list[dict] = []
    upper = sql.upper().strip()

    # Phase 1 — SELECT check
    ok1 = upper.startswith("SELECT") or upper.startswith("WITH")
    cps.append({
        "phase": "Phase 1: SELECT Check", "passed": ok1, "warn": False,
        "detail": "Starts with SELECT / WITH" if ok1 else "Must start with SELECT — only read-only queries permitted",
    })
    if not ok1:
        return cps

    # Phase 2 — Forbidden keywords
    bad_kw = None
    for pat in _FORBIDDEN:
        m = re.search(pat, upper, re.IGNORECASE | re.DOTALL)
        if m:
            bad_kw = m.group().strip()
            break
    cps.append({
        "phase": "Phase 2: Forbidden Keywords", "passed": bad_kw is None, "warn": False,
        "detail": "No forbidden keywords found" if not bad_kw else f"BLOCKED — '{bad_kw}' keyword detected",
    })
    if bad_kw:
        return cps

    # Phase 3 — Suspicious patterns (warn only)
    susp = None
    for pat in _SUSPICIOUS:
        if re.search(pat, upper, re.IGNORECASE):
            susp = pat.replace(r"\b", "")
            break
    cps.append({
        "phase": "Phase 3: Suspicious Patterns", "passed": True, "warn": susp is not None,
        "detail": "No system table access detected" if not susp else f"Warning: {susp} access detected (logged)",
    })

    # Phase 4 — AST parse
    ast_ok, ast_detail = True, "Confirmed SELECT node via sqlglot"
    if HAS_SQLGLOT:
        try:
            parsed = sqlglot.parse(sql, dialect="postgres")
            if not parsed:
                ast_ok, ast_detail = False, "SQL could not be parsed"
            else:
                for stmt in parsed:
                    if not isinstance(stmt, exp.Select):
                        ast_ok = False
                        ast_detail = f"Non-SELECT statement: {type(stmt).__name__}"
                        break
        except Exception as e:
            ast_detail = f"Parse warning: {str(e)[:70]}"
    else:
        ast_detail = "sqlglot unavailable — skipped"
    cps.append({
        "phase": "Phase 4: AST Parse (sqlglot)", "passed": ast_ok, "warn": False,
        "detail": ast_detail,
    })

    # Phase 5 — Semicolon / multi-statement injection
    semi_ok, semi_detail = True, "Single statement only"
    if HAS_SQLGLOT:
        try:
            stmts = [s for s in sqlglot.parse(sql, dialect="postgres") if s]
            if len(stmts) > 1:
                semi_ok = False
                semi_detail = f"Multiple statements detected ({len(stmts)}) — injection blocked"
        except Exception:
            pass
    cps.append({
        "phase": "Phase 5: Semicolon Injection", "passed": semi_ok, "warn": False,
        "detail": semi_detail,
    })

    # Tenant guard
    try:
        _, modified, scope_reason = enforce_tenant_scope(sql, vkorg)
        cps.append({
            "phase": "Tenant Guard", "passed": True, "warn": modified,
            "detail": scope_reason + (" — filter injected" if modified else ""),
            "modified": modified,
        })
    except Exception as e:
        cps.append({"phase": "Tenant Guard", "passed": False, "warn": False, "detail": str(e)})

    # Row limit
    has_limit = bool(re.search(r'\bLIMIT\s+\d+', sql, re.IGNORECASE))
    cps.append({
        "phase": "Row Limit", "passed": True, "warn": False,
        "detail": f"LIMIT {config.MAX_ROWS_RETURNED} {'already present' if has_limit else 'will be injected automatically'}",
    })

    return cps


def render_checkpoints_html(cps: list[dict]) -> str:
    rows = []
    for cp in cps:
        if not cp["passed"]:
            css, icon = "cp-fail", "❌"
        elif cp.get("warn"):
            css, icon = "cp-warn", "⚠️"
        else:
            css, icon = "cp-pass", "✅"
        rows.append(
            f'<div class="checkpoint-row {css}">'
            f'<span class="cp-icon">{icon}</span>'
            f'<span class="cp-phase">{cp["phase"]}</span>'
            f'<span class="cp-detail">{cp["detail"]}</span>'
            f'</div>'
        )
    return "\n".join(rows)


# ═══════════════════════════════════════════════════════════════════════════════
#  Self-Correction Visualizer
# ═══════════════════════════════════════════════════════════════════════════════

def _sql_diff_html(sql1: str, sql2: str) -> str:
    lines1 = sql1.splitlines()
    lines2 = sql2.splitlines()
    diff   = list(difflib.unified_diff(lines1, lines2, lineterm="", n=2))
    if not diff:
        return "<em style='color:#a6adc8;'>No textual differences</em>"
    parts = []
    for line in diff[2:]:
        if line.startswith("+"):
            parts.append(f'<span style="color:#a6e3a1;background:#1a2e1a;display:block;">{line}</span>')
        elif line.startswith("-"):
            parts.append(f'<span style="color:#f38ba8;background:#2e1a1a;display:block;text-decoration:line-through;">{line}</span>')
        elif line.startswith("@@"):
            parts.append(f'<span style="color:#585b70;display:block;">{line}</span>')
        else:
            parts.append(f'<span style="color:#a6adc8;display:block;">{line}</span>')
    return (
        f'<div style="font-family:monospace;font-size:10px;padding:8px;'
        f'background:#11111b;border-radius:4px;overflow:auto;">{"".join(parts)}</div>'
    )


def render_self_correction(trace_dict: dict) -> str:
    if not trace_dict or trace_dict.get("retry_count", 0) == 0:
        return ""
    retry_calls = [tc for tc in trace_dict.get("tool_calls", []) if tc.get("tool") == "retry_sql"]
    if not retry_calls:
        return ""

    html_parts = []
    total = len(retry_calls)
    for i, rc in enumerate(retry_calls):
        inp         = rc.get("input", {})
        failed_sql  = inp.get("failed_sql", "")
        fixed_sql   = inp.get("fixed_sql", "")
        error_msg   = inp.get("error_message", "")
        success     = rc.get("success", False)
        diff_html   = _sql_diff_html(failed_sql, fixed_sql) if failed_sql and fixed_sql else ""

        html_parts.append(f"""
<div class="correction-box">
  <div class="correction-title">⚠️ SELF CORRECTION — Attempt {i+1} of {total}</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px;">
    <div>
      <div style="color:#f38ba8;font-size:11px;font-weight:bold;margin-bottom:4px;">❌ Failed SQL</div>
      <pre style="font-size:10px;background:#11111b;padding:6px;border-radius:4px;color:#cdd6f4;overflow:auto;">{failed_sql[:400]}</pre>
      <div style="color:#f38ba8;font-size:11px;font-weight:bold;margin-top:6px;margin-bottom:4px;">PostgreSQL Error</div>
      <div style="font-size:10px;background:#11111b;padding:6px;border-radius:4px;color:#f38ba8;">{error_msg[:250]}</div>
    </div>
    <div>
      <div style="color:#a6e3a1;font-size:11px;font-weight:bold;margin-bottom:4px;">✅ Agent's Fix</div>
      <pre style="font-size:10px;background:#11111b;padding:6px;border-radius:4px;color:#a6e3a1;overflow:auto;">{fixed_sql[:400]}</pre>
      <div style="font-size:11px;margin-top:6px;">
        Result: {'<span style="color:#a6e3a1;">✅ Success</span>' if success else '<span style="color:#f38ba8;">❌ Still failed</span>'}
      </div>
    </div>
  </div>
  {"<div style='margin-top:10px;'><div style='color:#cdd6f4;font-size:11px;font-weight:bold;margin-bottom:4px;'>SQL Diff</div>" + diff_html + "</div>" if diff_html else ""}
</div>
""")
    return "\n".join(html_parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  Backend Flow Visualization
# ═══════════════════════════════════════════════════════════════════════════════

def render_backend_flow(trace_dict: dict, query_num: int, vkorg: str = "") -> str:  # noqa: ARG001
    if not trace_dict:
        return ""

    q   = trace_dict.get("question", "")
    ans = trace_dict.get("answer", "")
    tu  = trace_dict.get("token_usage", {})

    steps = [
        ("bs-done", "User Input",
         f'Question: "{(q[:70] + "…") if len(q) > 70 else q}"'),
        ("bs-done", f"Rate Limit Check ({query_num}/{config.RATE_LIMIT_PER_SESSION} / session)",
         "Passed — within session limit"),
        ("bs-done", "Agent SDK Loop Start",
         f"Model: {config.ANTHROPIC_MODEL}  Max iterations: 10"),
    ]

    for tc in trace_dict.get("tool_calls", []):
        tool    = tc.get("tool", "")
        inp     = tc.get("input", {})
        out     = tc.get("output", {})
        success = tc.get("success", True)
        css     = "bs-done" if success else "bs-blocked"

        if tool == "classify_question":
            cls = out.get("classification", inp.get("classification", "?"))
            rsn = inp.get("reasoning", "")[:70]
            steps.append((css, "Tool: classify_question",
                          f"→ {cls}  |  {rsn}"))

        elif tool == "generate_sql":
            sql  = inp.get("sql", "")
            qtyp = inp.get("query_type", "?")
            steps.append((css, "Tool: generate_sql",
                          f"→ type={qtyp}  |  {sql[:70]}…"))

        elif tool == "execute_sql":
            mod_txt = "✅ filter injected" if trace_dict.get("tenant_modified") else "✅ verified"
            steps.append(("bs-modified" if trace_dict.get("tenant_modified") else "bs-done",
                          "Security Layer",
                          f"sql_guard ✅ → tenant_guard {mod_txt} → row_limit ✅"))
            rows    = out.get("row_count", 0)
            trunc   = out.get("truncated", False)
            steps.append((css, "PostgreSQL Execution",
                          f"→ Read-only connection  |  {rows} rows returned  |  truncated: {trunc}"))

        elif tool == "retry_sql":
            err = inp.get("error_message", "")[:60]
            steps.append(("bs-modified", "Self-Correction Loop",
                          f"→ Fixing: {err}"))

    steps.append((
        "bs-done" if trace_dict.get("success") else "bs-blocked",
        "Agent Formulates Answer",
        (ans[:90] + "…") if len(ans) > 90 else ans,
    ))
    steps.append(("bs-done", "Logger → logs/pipeline.jsonl",
                  f"tokens={tu.get('total',0):,}  |  latency={trace_dict.get('latency_ms',0)}ms  |  retries={trace_dict.get('retry_count',0)}"))
    steps.append(("bs-done", "UI Renders Response",
                  f"rows={trace_dict.get('row_count',0)}  |  success={trace_dict.get('success',False)}"))

    step_html = "\n".join(
        f'<div class="backend-step {css}">'
        f'<div style="font-size:12px;font-weight:bold;color:#cdd6f4;">{title}</div>'
        f'<div style="font-size:11px;color:#a6adc8;margin-top:2px;font-family:monospace;">{detail}</div>'
        f'</div>'
        for css, title, detail in steps
    )
    return f'<div style="padding:4px 0;">{step_html}</div>'


# ═══════════════════════════════════════════════════════════════════════════════
#  Trace Renderer (used in history and inline)
# ═══════════════════════════════════════════════════════════════════════════════

def render_trace_detail(trace: dict, query_num: int, vkorg: str):
    tu        = trace.get("token_usage", {})
    total_tok = tu.get("total", 0)
    cost      = tu.get("estimated_cost_usd", 0.0)
    latency   = trace.get("latency_ms", 0)
    iterations= trace.get("iterations", 0)
    retries   = trace.get("retry_count", 0)

    st.markdown(
        f'<span class="metric-pill">🪙 {total_tok:,} tokens</span>'
        f'<span class="metric-pill">💰 ${cost:.4f}</span>'
        f'<span class="metric-pill">⏱️ {fmt_ms(latency)}</span>'
        f'<span class="metric-pill">🔁 {iterations} iterations</span>'
        + (f'<span class="metric-pill" style="color:#f38ba8;">⚠️ {retries} retries</span>' if retries else ""),
        unsafe_allow_html=True,
    )
    st.markdown("")

    tool_calls = trace.get("tool_calls", [])
    if tool_calls:
        st.markdown("**Agent Decision Flow**")
        st.markdown(
            render_flow(tool_calls, trace.get("question",""), trace.get("answer",""), show_answer=True),
            unsafe_allow_html=True,
        )

    if retries > 0:
        st.markdown("**Self-Correction Loop**")
        st.markdown(render_self_correction(trace), unsafe_allow_html=True)

    st.markdown("**Backend Pipeline Journey**")
    st.markdown(render_backend_flow(trace, query_num, vkorg), unsafe_allow_html=True)

    cls = trace.get("classification", "")
    cls_rsn = trace.get("classification_reasoning", "")
    if cls:
        icon = "🟢" if cls == "DATA_QUESTION" else "🔵"
        st.markdown("**Classification**")
        st.markdown(
            f'<div class="tool-card tool-classify">{icon} <strong>{cls}</strong>'
            + (f' — {cls_rsn}' if cls_rsn else '') + '</div>',
            unsafe_allow_html=True,
        )

    gen_sql = trace.get("generated_sql", "")
    if gen_sql:
        st.markdown("**Generated SQL**")
        sql_rsn = trace.get("sql_reasoning", "")
        qtype   = trace.get("query_type", "")
        if sql_rsn:
            st.caption(f"Reasoning: {sql_rsn}")
        if qtype:
            st.caption(f"Query type: `{qtype}` ({'pre-aggregated snapshot' if qtype=='snapshot' else 'raw order data'})")
        st.code(gen_sql, language="sql")

    if trace.get("tenant_modified"):
        st.markdown(
            '<span class="security-badge">🔒 TENANT FILTER INJECTED</span> '
            'Tenant scoping enforced by the security layer.',
            unsafe_allow_html=True,
        )

    final_sql = trace.get("final_sql", "")
    if final_sql and final_sql != gen_sql:
        with st.expander("Final SQL (after security + limit injection)"):
            st.code(final_sql, language="sql")

    results_summary = trace.get("query_results_summary", "")
    if results_summary:
        st.markdown(
            f'<div class="tool-card tool-execute">▶️ {results_summary}</div>',
            unsafe_allow_html=True,
        )

    blocked = trace.get("blocked_reason", "")
    if blocked:
        st.error(f"🚫 Security Block: {blocked}")

    error = trace.get("error", "")
    if error and not trace.get("success"):
        st.markdown(
            f'<div class="tool-card tool-retry">❌ {error}</div>',
            unsafe_allow_html=True,
        )

    if total_tok > 0:
        st.markdown("**Token Breakdown**")
        st.table({
            "Metric": ["Input tokens", "Output tokens", "Total tokens", "Est. cost (USD)"],
            "Value":  [
                f"{tu.get('total_input',0):,}",
                f"{tu.get('total_output',0):,}",
                f"{total_tok:,}",
                f"${cost:.4f}",
            ],
        })

    if gen_sql:
        st.markdown("**Security Checkpoints (this query)**")
        cps = run_security_checkpoints(gen_sql, vkorg)
        st.markdown(render_checkpoints_html(cps), unsafe_allow_html=True)


def render_message(msg: dict, query_num: int = 0, vkorg: str = "1004"):
    st.markdown(msg["content"])
    trace = msg.get("trace")
    if not trace:
        return
    with st.expander("🔬 Agent Trace + Flow + Security", expanded=False):
        render_trace_detail(trace, query_num, vkorg)


# ═══════════════════════════════════════════════════════════════════════════════
#  Evaluation helper
# ═══════════════════════════════════════════════════════════════════════════════

def _run_eval_q(q: dict, vkorg: str, tenant_name: str) -> dict:
    import time as _time
    start  = _time.time()
    try:
        result = run_agent(user_question=q["question"], tenant_vkorg=vkorg, tenant_name=tenant_name)
    except Exception as e:
        return {
            "id": q["id"], "persona": q.get("persona",""), "question": q["question"],
            "status": "❌", "sql_valid": "❌", "tenant_ok": "❌",
            "rows": 0, "tokens": 0, "cost": 0.0, "retries": 0, "latency_ms": 0,
            "error": str(e),
        }
    elapsed = int((_time.time() - start) * 1000)
    trace   = result.trace
    tu      = trace.token_usage

    expected  = q.get("expected_type", "DATA_QUESTION")
    must_have = [k.upper() for k in q.get("must_contain", [])]
    must_not  = [k.upper() for k in q.get("must_not_contain", [])]
    sql_upper = (trace.generated_sql or "").upper()

    cls_ok   = trace.classification == expected
    sql_ok   = bool(trace.generated_sql) if expected == "DATA_QUESTION" else True
    kw_ok    = all(k in sql_upper for k in must_have) if must_have and trace.generated_sql else True
    no_bad   = not any(k in sql_upper for k in must_not) if must_not and trace.generated_sql else True
    tenant_ok= (vkorg in (trace.final_sql or "") or "sales_org_code" in (trace.final_sql or "").lower()) if expected == "DATA_QUESTION" and trace.generated_sql else True
    has_ans  = bool(result.answer and len(result.answer) > 10)
    no_err   = not bool(trace.error)

    passed = all([cls_ok, sql_ok, kw_ok, no_bad, tenant_ok, has_ans, no_err])

    return {
        "id":       q["id"],
        "persona":  q.get("persona", ""),
        "question": q["question"][:60],
        "status":   "✅" if passed else "❌",
        "sql_valid":"✅" if sql_ok else "❌",
        "tenant_ok":"✅" if tenant_ok else "❌",
        "rows":     trace.row_count,
        "tokens":   tu.total,
        "cost":     f"${tu.estimated_cost_usd:.4f}",
        "retries":  trace.retry_count,
        "latency_ms": elapsed,
        "error":    trace.error or "",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("⚙️ Configuration")

    st.subheader("Active Company")
    tenant_label = st.selectbox("Tenant", list(TENANTS.keys()), index=0, key="tenant_select")
    active_vkorg, active_name = TENANTS[tenant_label]

    st.divider()
    st.subheader("Database")
    if st.button("🔌 Test Connection", use_container_width=True):
        with st.spinner("Connecting…"):
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
    st.subheader("📊 Session Metrics")
    total   = st.session_state.session_queries
    success = st.session_state.session_successes
    retries = st.session_state.session_retries
    rate    = f"{(success/total*100):.0f}%" if total else "—"

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Queries", total)
        st.metric("Tokens",  f"{st.session_state.session_tokens:,}")
    with c2:
        st.metric("Success", rate)
        st.metric("Cost",    f"${st.session_state.session_cost_usd:.4f}")
    if retries:
        st.info(f"🔄 Self-corrections: {retries}")

    # ── Current Query Token Meter ──
    if st.session_state.current_query_tokens:
        st.divider()
        st.subheader("🔢 Last Query Tokens")
        cqt = st.session_state.current_query_tokens
        st.markdown(
            f"Input: **{cqt.get('input',0):,}** · Output: **{cqt.get('output',0):,}**\n\n"
            f"Total: **{cqt.get('total',0):,}** tokens · **${cqt.get('cost',0.0):.4f}**"
        )

    # ── Token History Chart ──
    hist = st.session_state.token_history
    if hist:
        st.divider()
        st.subheader("📈 Token Usage (last 5)")
        df_h = pd.DataFrame([
            {"Q": f"Q{i+1}", "Input": h["input"], "Output": h["output"]}
            for i, h in enumerate(hist[-5:])
        ])
        st.bar_chart(df_h.set_index("Q"))

    st.divider()
    st.caption(f"Model: `{config.ANTHROPIC_MODEL}`")
    st.caption(f"Row limit: `{config.MAX_ROWS_RETURNED}`")
    st.caption("Architecture: **Claude Agent SDK**")

    if st.button("🗑️ Clear Session", use_container_width=True):
        for k in list(_DEFAULTS.keys()):
            st.session_state[k] = _DEFAULTS[k]
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN HEADER
# ═══════════════════════════════════════════════════════════════════════════════
st.caption(
    f"Tenant: **{active_name}** (VKORG `{active_vkorg}`) · "
    f"Architecture: **Claude Agent SDK** · Model: `{config.ANTHROPIC_MODEL}`"
)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "💬 Chat",
    "⚖️ Comparison",
    "🔒 Security",
    "📊 Evaluation",
    "📋 Logs",
])


# ═══════════════════════════════════════════════════════════════════════════════
#  TAB 1 — Chat  (Req 1, 4, 5, 7, 8)
# ═══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown("### 🤖 Live Agent Chat")

    # Conversation context indicator (Req 8)
    conv = st.session_state.conversation_history
    if conv:
        turns = len(conv) // 2
        prev_q = conv[-2].get("content", "")[:55] if len(conv) >= 2 else ""
        st.markdown(
            f'<div class="context-indicator">💬 Using context from {turns} previous turn(s) — '
            f'last: "{prev_q}…"</div>',
            unsafe_allow_html=True,
        )

    # Example starters / follow-up chains
    if not st.session_state.messages:
        with st.expander("💡 Example Questions", expanded=True):
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

        with st.expander("🔗 Follow-up Chains (tests conversation memory)"):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("""
**Chain 1 — Customer Funnel**
1. *"Who are our top 5 customers?"*
2. *"Which of those are at risk?"*
3. *"What was their revenue last year?"*
""")
                if st.button("▶ Start Chain 1", key="chain1"):
                    st.session_state["prefill"] = "Who are our top 5 customers by spend?"
                    st.rerun()
            with col2:
                st.markdown("""
**Chain 2 — Revenue Analysis**
1. *"Revenue this month?"*
2. *"Compare to last month"*
3. *"Which customers drove the change?"*
""")
                if st.button("▶ Start Chain 2", key="chain2"):
                    st.session_state["prefill"] = "What's our total revenue this month compared to last month?"
                    st.rerun()

    # Render chat history
    for idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                render_message(msg, query_num=idx // 2 + 1, vkorg=active_vkorg)

    # Chat input
    prefill    = st.session_state.pop("prefill", "")
    user_input = st.chat_input(placeholder=f"Ask {active_name} a business question…") or prefill

    if user_input:
        if st.session_state.session_queries >= config.RATE_LIMIT_PER_SESSION:
            st.error(f"Session limit of {config.RATE_LIMIT_PER_SESSION} queries reached. Clear session to continue.")
            st.stop()

        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            flow_ph = st.empty()
            flow_ph.markdown(
                render_flow([], question=user_input, thinking=True),
                unsafe_allow_html=True,
            )

            try:
                init_pool()
                with st.spinner("Agent thinking…"):
                    result = run_agent(
                        user_question=user_input,
                        conversation_history=st.session_state.conversation_history,
                        tenant_vkorg=active_vkorg,
                        tenant_name=active_name,
                    )

                trace = result.trace
                tu    = trace.token_usage

                trace_dict = {
                    "question":                 user_input,
                    "answer":                   result.answer,
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
                            "tool":      tc.tool_name,
                            "success":   tc.success,
                            "input":     tc.input,
                            "output":    tc.output,
                            "timestamp": tc.timestamp,
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

                # Animate flow nodes appearing one by one (Req 1)
                tcs = trace_dict["tool_calls"]
                for i in range(len(tcs)):
                    flow_ph.markdown(
                        render_flow(tcs[:i+1], question=user_input,
                                    answer=result.answer, show_answer=False),
                        unsafe_allow_html=True,
                    )
                    time.sleep(0.35)
                flow_ph.markdown(
                    render_flow(tcs, question=user_input,
                                answer=result.answer, show_answer=True),
                    unsafe_allow_html=True,
                )

                # Self-correction box (Req 5)
                if trace.retry_count > 0:
                    st.markdown(render_self_correction(trace_dict), unsafe_allow_html=True)

                # Answer
                st.markdown(result.answer)

                # Full trace expander
                qnum = st.session_state.session_queries + 1
                with st.expander("🔬 Agent Trace · Flow · Backend Journey · Security", expanded=False):
                    render_trace_detail(trace_dict, qnum, active_vkorg)

                # Session state updates
                st.session_state.session_tokens   += tu.total
                st.session_state.session_cost_usd += tu.estimated_cost_usd
                st.session_state.session_queries  += 1
                st.session_state.session_retries  += trace.retry_count
                if trace.success:
                    st.session_state.session_successes += 1

                st.session_state.last_trace_dict      = trace_dict
                st.session_state.current_query_tokens = {
                    "input": tu.total_input, "output": tu.total_output,
                    "total": tu.total,       "cost":   tu.estimated_cost_usd,
                }
                hist = st.session_state.token_history
                hist.append({"input": tu.total_input, "output": tu.total_output})
                st.session_state.token_history = hist[-10:]

                st.session_state.messages.append({
                    "role": "assistant", "content": result.answer, "trace": trace_dict,
                })
                st.session_state.conversation_history.extend([
                    {"role": "user",      "content": user_input},
                    {"role": "assistant", "content": result.answer},
                ])
                st.rerun()

            except Exception as e:
                import traceback
                err = f"Error: {str(e)}"
                flow_ph.empty()
                st.error(err)
                st.code(traceback.format_exc(), language="python")
                st.session_state.messages.append({"role": "assistant", "content": err})


# ═══════════════════════════════════════════════════════════════════════════════
#  TAB 2 — Comparison: Direct LLM vs Agent SDK  (Req 2)
# ═══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### ⚖️ Direct LLM vs Agent SDK Comparison")
    st.caption("Same question, same security layer — different pipeline architecture.")

    if not HAS_DIRECT:
        # Degrade gracefully — disable just this tab, keep the rest of the app alive.
        st.error(
            "⚠️ `direct_llm_pipeline.py` not found in `ui/`. "
            "The comparison view is disabled, but Chat / Security / Evaluation / Logs "
            "remain fully functional. Restore the file to re-enable this tab."
        )
    else:
        cmp_q = st.text_input(
            "Question to compare",
            value=st.session_state.comparison_question or "Who are our top 5 customers by spend?",
            key="cmp_input",
        )

        run_cmp = st.button("▶ Run Comparison", type="primary", use_container_width=False)

        if run_cmp and cmp_q:
            st.session_state.comparison_question = cmp_q
            lcol, rcol = st.columns(2)

            with lcol:
                st.markdown("#### 🔴 Option A — Direct LLM")
                st.caption("One API call · No tool use · No self-correction")
                with st.spinner("Running direct LLM…"):
                    llm_r = run_direct_llm(cmp_q, active_vkorg, active_name)
                st.session_state.comparison_llm = llm_r

            with rcol:
                st.markdown("#### 🟢 Option B — Agent SDK")
                st.caption("Autonomous tool calls · Classification · Self-correction")
                with st.spinner("Running agent…"):
                    init_pool()
                    ag_r = run_agent(cmp_q, tenant_vkorg=active_vkorg, tenant_name=active_name)
                st.session_state.comparison_agent = ag_r

        llm_r = st.session_state.comparison_llm
        ag_r  = st.session_state.comparison_agent

        if llm_r and ag_r:
            st.divider()
            lcol, rcol = st.columns(2)
            ag_trace = ag_r.trace if hasattr(ag_r, "trace") else None

            with lcol:
                st.markdown("#### 🔴 Direct LLM Result")
                status_badge = '<span class="pass-badge">PASS</span>' if llm_r.success else '<span class="fail-badge">FAIL</span>'
                st.markdown(status_badge, unsafe_allow_html=True)
                st.markdown(
                    f'<span class="metric-pill">🪙 {llm_r.total_tokens:,} tokens</span>'
                    f'<span class="metric-pill">💰 ${llm_r.estimated_cost_usd:.4f}</span>'
                    f'<span class="metric-pill">⏱️ {fmt_ms(llm_r.latency_ms)}</span>',
                    unsafe_allow_html=True,
                )
                if llm_r.sql:
                    st.markdown("**Generated SQL**")
                    st.code(llm_r.sql, language="sql")
                if llm_r.tenant_modified:
                    st.markdown('<span class="security-badge">🔒 TENANT FILTER INJECTED</span>', unsafe_allow_html=True)
                if llm_r.blocked_reason:
                    st.error(f"🚫 Blocked: {llm_r.blocked_reason}")
                if llm_r.error and not llm_r.blocked_reason:
                    st.warning(f"Error: {llm_r.error}")
                if llm_r.success:
                    st.success(f"✅ {llm_r.row_count} rows returned")
                    if llm_r.rows:
                        st.dataframe(pd.DataFrame(llm_r.rows[:10]), use_container_width=True)
                else:
                    st.info("No self-correction — single-shot only")

            with rcol:
                st.markdown("#### 🟢 Agent SDK Result")
                ag_trace_d = None
                if ag_trace:
                    ag_tu = ag_trace.token_usage
                    status_badge = '<span class="pass-badge">PASS</span>' if ag_trace.success else '<span class="fail-badge">FAIL</span>'
                    st.markdown(status_badge, unsafe_allow_html=True)
                    st.markdown(
                        f'<span class="metric-pill">🪙 {ag_tu.total:,} tokens</span>'
                        f'<span class="metric-pill">💰 ${ag_tu.estimated_cost_usd:.4f}</span>'
                        f'<span class="metric-pill">⏱️ {fmt_ms(ag_trace.latency_ms)}</span>'
                        f'<span class="metric-pill">🔁 {ag_trace.iterations} iterations</span>',
                        unsafe_allow_html=True,
                    )
                    tool_seq = " → ".join(
                        TOOL_LABEL.get(tc.tool_name, tc.tool_name)
                        for tc in ag_trace.tool_calls
                    )
                    st.caption(f"Tool call sequence: {tool_seq}")
                    if ag_trace.generated_sql:
                        st.markdown("**Generated SQL**")
                        st.code(ag_trace.generated_sql, language="sql")
                    if ag_trace.tenant_modified:
                        st.markdown('<span class="security-badge">🔒 TENANT FILTER INJECTED</span>', unsafe_allow_html=True)
                    if ag_trace.blocked_reason:
                        st.error(f"🚫 Blocked: {ag_trace.blocked_reason}")
                    if ag_trace.success:
                        st.success(f"✅ {ag_trace.row_count} rows returned")
                    if ag_trace.retry_count > 0:
                        st.warning(f"⚠️ Self-corrected {ag_trace.retry_count} time(s)")
                    st.markdown(f"**Answer:** {ag_r.answer}")

                    ag_trace_d = {
                        "total_input": ag_tu.total_input, "total_output": ag_tu.total_output,
                        "total": ag_tu.total, "cost": ag_tu.estimated_cost_usd,
                        "latency": ag_trace.latency_ms, "retries": ag_trace.retry_count,
                        "sql": ag_trace.generated_sql, "rows": ag_trace.row_count,
                        "success": ag_trace.success,
                    }

            # Comparison Table
            st.divider()
            st.markdown("### 📊 Head-to-Head Comparison")

            ag_tu_t = ag_trace.token_usage if ag_trace else None
            cmp_data = {
                "Metric":        ["SQL Generated", "Tokens Used", "Latency", "Cost (USD)", "Self-corrected", "Rows Returned", "Pass/Fail"],
                "Direct LLM":   [
                    "✅" if llm_r.sql else "❌",
                    f"{llm_r.total_tokens:,}",
                    fmt_ms(llm_r.latency_ms),
                    f"${llm_r.estimated_cost_usd:.4f}",
                    "N/A",
                    str(llm_r.row_count),
                    "✅ Pass" if llm_r.success else "❌ Fail",
                ],
                "Agent SDK":    [
                    "✅" if (ag_trace and ag_trace.generated_sql) else "❌",
                    f"{ag_tu_t.total:,}" if ag_tu_t else "—",
                    fmt_ms(ag_trace.latency_ms) if ag_trace else "—",
                    f"${ag_tu_t.estimated_cost_usd:.4f}" if ag_tu_t else "—",
                    f"Yes ({ag_trace.retry_count}x)" if ag_trace and ag_trace.retry_count else "No",
                    str(ag_trace.row_count) if ag_trace else "—",
                    "✅ Pass" if (ag_trace and ag_trace.success) else "❌ Fail",
                ],
            }
            st.table(pd.DataFrame(cmp_data).set_index("Metric"))


# ═══════════════════════════════════════════════════════════════════════════════
#  TAB 3 — Security  (Req 3, 9)
# ═══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### 🔒 Security Visualization")

    sec_c1, sec_c2 = st.columns([1, 1])

    with sec_c1:
        # Last query checkpoints
        st.markdown("#### Checkpoint Timeline — Last Query")
        last_trace = st.session_state.last_trace_dict
        if last_trace and last_trace.get("generated_sql"):
            sql_to_check = last_trace["generated_sql"]
            st.caption(f"Checking: `{sql_to_check[:80]}…`")
            cps = run_security_checkpoints(sql_to_check, active_vkorg)
            st.markdown(render_checkpoints_html(cps), unsafe_allow_html=True)
        else:
            st.info("Run a query in the Chat tab to see live security checkpoints here.")

        # Tenant Isolation Demo (Req 9)
        st.divider()
        st.markdown("#### 🏢 Tenant Isolation Demo")
        breach_q = "Who are our top 5 customers by spend?"
        st.caption(f'Query: "{breach_q}"')

        t_col1, t_col2 = st.columns(2)
        with t_col1:
            if st.button(f"Run as {active_name}", use_container_width=True, key="tenant_a"):
                with st.spinner(f"Running for {active_name}…"):
                    init_pool()
                    r = run_agent(breach_q, tenant_vkorg=active_vkorg, tenant_name=active_name)
                st.success(f"VKORG {active_vkorg}: {r.trace.row_count} rows")
                if r.trace.generated_sql:
                    st.code(r.trace.generated_sql[:300], language="sql")

        other_tenants = [(v, n) for _, (v, n) in TENANTS.items() if v != active_vkorg]
        other_vkorg, other_name = other_tenants[0] if other_tenants else (active_vkorg, active_name)
        with t_col2:
            if st.button(f"Run as {other_name}", use_container_width=True, key="tenant_b"):
                with st.spinner(f"Running for {other_name}…"):
                    init_pool()
                    r2 = run_agent(breach_q, tenant_vkorg=other_vkorg, tenant_name=other_name)
                st.success(f"VKORG {other_vkorg}: {r2.trace.row_count} rows")
                if r2.trace.generated_sql:
                    st.code(r2.trace.generated_sql[:300], language="sql")

        st.caption("⬆️ Notice: same question, different sales_org_code filter — completely isolated results.")

        # Breach attempt
        st.divider()
        st.markdown("#### 🚨 Breach Attempt Demo")
        breach_sql = f"SELECT * FROM order_headers WHERE sales_org_code = '9999'"
        st.code(breach_sql, language="sql")
        if st.button("🔴 Attempt Cross-Tenant Breach", key="breach_btn", type="secondary"):
            _, modified, reason = enforce_tenant_scope(breach_sql, active_vkorg)
            if modified:
                st.warning(f"⚠️ BREACH INTERCEPTED — tenant_guard.py blocked it")
                st.markdown(f"Reason: **{reason}**")
                st.markdown(f'<span class="security-badge">🔒 SQL rewritten to VKORG {active_vkorg}</span>', unsafe_allow_html=True)
            else:
                st.info("No cross-tenant pattern detected in this SQL.")

    with sec_c2:
        st.markdown("#### 🎭 Security Demo Mode — Attack Scenarios")
        st.caption("Click any attack to see exactly which security phase blocks it.")

        for atk in ATTACK_DEMOS:
            with st.expander(f"{atk['id']}: {atk['label']}", expanded=False):
                st.caption(atk["description"])
                st.code(atk["sql"], language="sql")
                if st.button(f"Run {atk['id']}", key=f"atk_{atk['id']}"):
                    cps = run_security_checkpoints(atk["sql"], active_vkorg)
                    st.markdown(render_checkpoints_html(cps), unsafe_allow_html=True)

                    last_failed = next((cp for cp in reversed(cps) if not cp["passed"]), None)
                    if last_failed:
                        st.error(f"🚫 BLOCKED at: **{last_failed['phase']}** — {last_failed['detail']}")
                    elif any(cp.get("modified") for cp in cps):
                        st.warning("⚠️ ALLOWED but SQL was modified (tenant correction applied)")
                    elif any(cp.get("warn") for cp in cps):
                        st.warning("⚠️ ALLOWED with warnings (logged for review)")
                    else:
                        st.success("✅ PASSED all security checks")


# ═══════════════════════════════════════════════════════════════════════════════
#  TAB 4 — Evaluation  (Req 6)
# ═══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.markdown("### 📊 Accuracy Evaluation Dashboard")

    eval_vkorg, eval_name = active_vkorg, active_name
    st.caption(f"Tenant: **{eval_name}** (VKORG `{eval_vkorg}`) · {len(TEST_QUESTIONS)} test questions")

    if st.button("▶ Run All 17 Test Questions", type="primary", key="run_eval"):
        st.session_state.eval_results = []
        progress = st.progress(0, text="Starting evaluation…")
        results_ph = st.empty()
        try:
            init_pool()
        except Exception as e:
            st.error(f"DB connection failed: {e}")
            st.stop()

        for i, q in enumerate(TEST_QUESTIONS):
            progress.progress(
                (i + 1) / len(TEST_QUESTIONS),
                text=f"Running Q{q['id']}: {q['question'][:60]}…"
            )
            r = _run_eval_q(q, eval_vkorg, eval_name)
            st.session_state.eval_results.append(r)
            df_live = pd.DataFrame(st.session_state.eval_results)
            results_ph.dataframe(df_live, use_container_width=True)
            time.sleep(0.1)

        progress.progress(1.0, text="✅ Evaluation complete")

    ev_results = st.session_state.eval_results
    if ev_results:
        st.divider()

        # Summary metrics
        total_q  = len(ev_results)
        passed_q = sum(1 for r in ev_results if r["status"] == "✅")
        pass_rate = passed_q / total_q * 100 if total_q else 0
        avg_lat  = sum(r.get("latency_ms", 0) for r in ev_results) / max(total_q, 1)
        avg_tok  = sum(r.get("tokens", 0) for r in ev_results) / max(total_q, 1)
        tot_cost = sum(float(r.get("cost", "$0").replace("$","")) for r in ev_results)
        tot_ret  = sum(r.get("retries", 0) for r in ev_results)
        ret_rate = tot_ret / max(total_q, 1) * 100

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Pass Rate", f"{passed_q}/{total_q} ({pass_rate:.0f}%)")
        m2.metric("Avg Latency", fmt_ms(int(avg_lat)))
        m3.metric("Avg Tokens", f"{avg_tok:,.0f}")
        m4.metric("Total Cost", f"${tot_cost:.4f}")
        m5.metric("Self-corrections", tot_ret)
        m6.metric("Correction Rate", f"{ret_rate:.0f}%")

        st.divider()
        st.markdown("**Results Table**")
        df_ev = pd.DataFrame(ev_results)
        st.dataframe(df_ev, use_container_width=True, height=500)

        # Failures
        fails = [r for r in ev_results if r["status"] == "❌"]
        if fails:
            with st.expander(f"❌ {len(fails)} Failed Question(s)"):
                for f in fails:
                    st.markdown(f"**Q{f['id']}** [{f['persona']}]: {f['question']}")
                    if f.get("error"):
                        st.caption(f"Error: {f['error']}")

        # CSV export
        buf = io.StringIO()
        pd.DataFrame(ev_results).to_csv(buf, index=False)
        st.download_button(
            label="⬇️ Export Results CSV",
            data=buf.getvalue(),
            file_name=f"eval_{eval_vkorg}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )
    else:
        st.info("Click **Run All 17 Test Questions** above to start the evaluation.")


# ═══════════════════════════════════════════════════════════════════════════════
#  TAB 5 — Live Logs  (Req 10)
# ═══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.markdown("### 📋 Live Log Viewer")

    auto_refresh = st.checkbox("Auto-refresh every 3 seconds", value=False, key="log_refresh")

    lc1, lc2 = st.columns(2)

    with lc1:
        st.markdown("#### Pipeline Log (`logs/pipeline.jsonl`)")
        try:
            pipeline_logs = load_pipeline_logs()
        except Exception as e:
            pipeline_logs = []
            st.warning(f"Could not load pipeline log: {e}")

        last10 = pipeline_logs[-10:] if pipeline_logs else []
        security_count = sum(1 for e in pipeline_logs if e.get("blocked_reason") or e.get("error"))
        retry_count_log = sum(1 for e in pipeline_logs if e.get("retry_count", 0) > 0)

        pill1 = f'<span class="metric-pill">Total entries: {len(pipeline_logs)}</span>'
        pill2 = f'<span class="metric-pill" style="color:#f38ba8;">Security events: {security_count}</span>'
        pill3 = f'<span class="metric-pill" style="color:#f9e2af;">Self-corrections: {retry_count_log}</span>'
        st.markdown(pill1 + pill2 + pill3, unsafe_allow_html=True)
        st.caption("Showing last 10 entries")

        for entry in reversed(last10):
            ts       = entry.get("timestamp", "")[:19]
            tenant   = entry.get("tenant_vkorg", "?")
            q        = entry.get("question", "")[:55]
            rows     = entry.get("row_count", 0)
            tokens   = entry.get("token_usage", {}).get("total", 0)
            latency  = entry.get("latency_ms", 0)
            retries  = entry.get("retry_count", 0)
            blocked  = entry.get("blocked_reason", "")
            success  = entry.get("execution_success", False)

            if blocked:
                css = "log-security"
                icon = "🚨"
            elif retries > 0:
                css = "log-retry"
                icon = "⚠️"
            else:
                css = "log-normal"
                icon = "✅" if success else "❌"

            summary = f"{ts} | VKORG:{tenant} | {icon} | rows:{rows} | tok:{tokens} | {latency}ms"
            if retries:
                summary += f" | retries:{retries}"
            if blocked:
                summary += f" | BLOCKED:{blocked[:40]}"
            st.markdown(f'<div class="{css}">{summary}<br/><span style="color:#585b70;">Q: {q}</span></div>', unsafe_allow_html=True)

    with lc2:
        st.markdown("#### Security Log (`logs/security.jsonl`)")
        try:
            sec_logs = load_security_logs()
        except Exception as e:
            sec_logs = []
            st.warning(f"Could not load security log: {e}")

        last10_sec = sec_logs[-10:] if sec_logs else []
        st.markdown(
            f'<span class="metric-pill" style="color:#f38ba8;">Security events: {len(sec_logs)}</span>',
            unsafe_allow_html=True,
        )
        st.caption("Showing last 10 security events")

        if last10_sec:
            for ev in reversed(last10_sec):
                ts     = ev.get("timestamp", "")[:19]
                etype  = ev.get("event_type", "?")
                detail = ev.get("detail", "")[:80]
                tenant = ev.get("tenant_vkorg", "?")
                st.markdown(
                    f'<div class="log-security">'
                    f'🚨 {ts} | {etype} | VKORG:{tenant}<br/>'
                    f'<span style="color:#a6adc8;">{detail}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("No security events logged yet.")

        # Raw JSON viewer
        with st.expander("Raw JSON — last pipeline entry"):
            if pipeline_logs:
                st.json(pipeline_logs[-1])
            else:
                st.write("No entries yet.")

    if auto_refresh:
        time.sleep(3)
        st.rerun()
