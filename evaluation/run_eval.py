"""
evaluation/run_eval.py — Automated Evaluation Runner

Run: python evaluation/run_eval.py
     python evaluation/run_eval.py --tenant 1010
     python evaluation/run_eval.py --security
     python evaluation/run_eval.py --questions 1,2,3
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import config
from app.database import init_pool, test_connection
from app.agent.agent_runner import run_agent
from app.security.sql_guard import validate_sql
from app.security.tenant_guard import enforce_tenant_scope
from evaluation.test_questions import TEST_QUESTIONS, SECURITY_TEST_CASES

TENANT_MAP   = {"1004": "Brennwald", "1010": "Lindauer", "1032": "Renaux Belgium", "1031": "Renaux Luxembourg"}
REPORT_PATH  = Path("logs/eval_report.json")
REPORT_PATH.parent.mkdir(exist_ok=True)

GREEN  = "\033[92m"; RED    = "\033[91m"
YELLOW = "\033[93m"; BOLD   = "\033[1m"; RESET  = "\033[0m"


def run_question(q: dict, vkorg: str, tenant_name: str) -> dict:
    q_id      = q["id"]
    question  = q["question"]
    expected  = q["expected_type"]
    must_have = [k.upper() for k in q.get("must_contain", [])]
    must_not  = [k.upper() for k in q.get("must_not_contain", [])]

    print(f"\n  [{q_id:>2}] {question[:65]}")
    start = time.time()

    try:
        result  = run_agent(user_question=question, tenant_vkorg=vkorg, tenant_name=tenant_name)
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        print(f"       {RED}ERROR: {e}{RESET}")
        return {"id": q_id, "question": question, "passed": False, "error": str(e),
                "latency_ms": elapsed, "token_total": 0, "cost_usd": 0,
                "retry_count": 0, "rows_returned": 0, "classification": "", "generated_sql": ""}

    elapsed = int((time.time() - start) * 1000)
    trace   = result.trace
    tu      = trace.token_usage

    checks = {}
    checks["classification_correct"] = (trace.classification == expected)
    checks["sql_generated"]          = bool(trace.generated_sql) if expected == "DATA_QUESTION" else True
    sql_upper = trace.generated_sql.upper()
    checks["keywords_present"]       = all(k in sql_upper for k in must_have) if must_have and trace.generated_sql else True
    checks["no_forbidden_keywords"]  = not any(k in sql_upper for k in must_not) if must_not and trace.generated_sql else True
    checks["tenant_scoped"]          = (vkorg in (trace.final_sql or "") or "sales_org_code" in (trace.final_sql or "").lower()) if expected == "DATA_QUESTION" and trace.generated_sql else True
    checks["has_answer"]             = bool(result.answer and len(result.answer) > 10)
    checks["no_error"]               = not bool(trace.error)

    passed = all(checks.values())
    status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    print(f"       {status} | {trace.classification} | {trace.row_count} rows | {elapsed}ms | {tu.total} tokens | ${tu.estimated_cost_usd:.4f}")

    if not passed:
        failed = [k for k, v in checks.items() if not v]
        print(f"       {YELLOW}Failed: {', '.join(failed)}{RESET}")
    if trace.retry_count > 0:
        print(f"       {YELLOW}⚠ Self-correction: {trace.retry_count} retry(s){RESET}")

    return {
        "id": q_id, "question": question, "persona": q.get("persona", ""),
        "passed": passed, "error": trace.error or "",
        "latency_ms": elapsed, "classification": trace.classification,
        "generated_sql": trace.generated_sql, "final_sql": trace.final_sql,
        "answer": result.answer[:300] if result.answer else "",
        "token_total": tu.total, "cost_usd": tu.estimated_cost_usd,
        "retry_count": trace.retry_count, "rows_returned": trace.row_count,
        "check_results": checks,
    }


def run_security(vkorg: str) -> list[dict]:
    print(f"\n{BOLD}Security Tests{RESET}\n{'─'*50}")
    results = []
    for case in SECURITY_TEST_CASES:
        c_id  = case["id"]
        ctype = case["type"]
        desc  = case["description"]
        print(f"\n  [{c_id}] {ctype}: {desc}")

        if ctype in ("DESTRUCTIVE_SQL", "INJECTION", "VALID_QUERY"):
            v = validate_sql(case["sql"])
            passed = (not v.is_safe) == case["should_block"]
            print(f"       {'✅' if passed else '❌'} blocked={not v.is_safe} expected_block={case['should_block']} | {v.reason[:60]}")
            results.append({"id": c_id, "type": ctype, "description": desc, "passed": passed,
                            "should_block": case["should_block"], "was_blocked": not v.is_safe})

        elif ctype == "TENANT_VIOLATION":
            _, modified, reason = enforce_tenant_scope(case["sql"], case.get("active_vkorg", vkorg))
            passed = modified == case.get("should_modify", False)
            print(f"       {'✅' if passed else '❌'} modified={modified} | {reason[:60]}")
            results.append({"id": c_id, "type": ctype, "description": desc, "passed": passed,
                            "was_modified": modified})

        elif ctype == "PROMPT_INJECTION":
            try:
                result = run_agent(user_question=case["question"], tenant_vkorg=vkorg, tenant_name=TENANT_MAP.get(vkorg, ""))
                sql    = result.trace.generated_sql
                safe   = not sql or sql.upper().strip().startswith("SELECT")
                print(f"       {'✅' if safe else '❌'} handled gracefully | sql_safe={safe}")
                results.append({"id": c_id, "type": ctype, "description": desc, "passed": safe})
            except Exception as e:
                print(f"       {RED}ERROR: {e}{RESET}")
                results.append({"id": c_id, "type": ctype, "description": desc, "passed": False, "error": str(e)})

    return results


def print_summary(results: list[dict], elapsed: float):
    total    = len(results)
    passed   = sum(1 for r in results if r["passed"])
    rate     = passed / total * 100 if total else 0
    lats     = [r["latency_ms"] for r in results if r.get("latency_ms")]
    toks     = [r["token_total"] for r in results if r.get("token_total")]
    costs    = [r.get("cost_usd", 0) for r in results]
    retries  = sum(r.get("retry_count", 0) for r in results)

    print(f"\n{'='*55}")
    print(f"{BOLD}EVALUATION SUMMARY{RESET}")
    print(f"{'='*55}")
    print(f"  Questions:      {total}")
    print(f"  Passed:         {GREEN}{passed}{RESET}")
    print(f"  Failed:         {RED}{total-passed}{RESET}")
    print(f"  Pass rate:      {BOLD}{rate:.1f}%{RESET}")
    print(f"  Avg latency:    {sum(lats)/len(lats):.0f}ms" if lats else "  Avg latency:    —")
    print(f"  Avg tokens:     {sum(toks)/len(toks):.0f}" if toks else "  Avg tokens:     —")
    print(f"  Total cost:     ${sum(costs):.4f}")
    print(f"  Self-corrections: {retries}")
    print(f"  Total time:     {elapsed:.1f}s")
    print(f"{'='*55}")
    if total - passed > 0:
        print(f"\n{RED}Failed:{RESET}")
        for r in results:
            if not r["passed"]:
                print(f"  [{r['id']:>2}] {r.get('question','')[:60]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant",    default="1004")
    parser.add_argument("--questions", default="")
    parser.add_argument("--security",  action="store_true")
    args = parser.parse_args()

    vkorg = args.tenant
    name  = TENANT_MAP.get(vkorg, "Unknown")

    print(f"\n{BOLD}{'='*55}{RESET}")
    print(f"{BOLD}Talk to Your Data — Evaluation Runner{RESET}")
    print(f"{'='*55}")
    print(f"  Tenant: {name} ({vkorg})")
    print(f"  Mode:   Agent SDK")
    print(f"  Time:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    print(f"\n  Connecting to database...")
    try:
        init_pool()
        if not test_connection():
            print(f"  {RED}DB connection failed{RESET}")
            sys.exit(1)
        print(f"  {GREEN}Connected{RESET}")
    except Exception as e:
        print(f"  {RED}DB error: {e}{RESET}")
        sys.exit(1)

    if args.security:
        results = run_security(vkorg)
        passed  = sum(1 for r in results if r["passed"])
        print(f"\n  Security: {passed}/{len(results)} passed")
        _save(results, {"type": "security", "tenant": vkorg})
        return

    questions = TEST_QUESTIONS
    if args.questions:
        ids       = [int(x.strip()) for x in args.questions.split(",")]
        questions = [q for q in TEST_QUESTIONS if q["id"] in ids]

    print(f"  Questions: {len(questions)}\n{'─'*55}")

    results = []
    t0      = time.time()
    for q in questions:
        results.append(run_question(q, vkorg, name))
        time.sleep(0.5)

    print_summary(results, time.time() - t0)
    _save(results, {"type": "questions", "tenant_vkorg": vkorg, "tenant_name": name, "mode": "Agent SDK"})


def _save(results, meta):
    total  = len(results)
    passed = sum(1 for r in results if r["passed"])
    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "meta":    meta,
        "results": results,
        "summary": {
            "total": total, "passed": passed,
            "pass_rate": passed / total * 100 if total else 0,
            "avg_latency_ms": sum(r.get("latency_ms",0) for r in results) / max(total,1),
            "avg_tokens":     sum(r.get("token_total",0) for r in results) / max(total,1),
            "total_cost_usd": sum(r.get("cost_usd",0) for r in results),
            "total_retries":  sum(r.get("retry_count",0) for r in results),
        },
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  📄 Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()