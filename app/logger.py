import json
import logging
import os
from datetime import datetime
from pathlib import Path

from app.config import config

LOG_DIR      = Path(config.LOG_DIR)
LOG_DIR.mkdir(exist_ok=True)

PIPELINE_LOG = LOG_DIR / "pipeline.jsonl"
SECURITY_LOG = LOG_DIR / "security.jsonl"
APP_LOG      = LOG_DIR / "app.log"


def setup_logging():
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    root  = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        return
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(APP_LOG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)


setup_logging()
logger = logging.getLogger(__name__)


def log_pipeline_event(
    question: str,
    tenant_vkorg: str,
    tenant_name: str,
    classification: str,
    generated_sql: str,
    final_sql: str,
    execution_success: bool,
    row_count: int,
    truncated: bool,
    answer: str,
    token_usage: dict,
    latency_ms: int,
    retry_count: int,
    error: str,
    pipeline_mode: str,
    tenant_modified: bool,
    tool_calls: list = None,
    blocked_reason: str = "",
):
    entry = {
        "timestamp":         datetime.utcnow().isoformat() + "Z",
        "tenant_vkorg":      tenant_vkorg,
        "tenant_name":       tenant_name,
        "pipeline_mode":     pipeline_mode,
        "question":          question,
        "classification":    classification,
        "generated_sql":     generated_sql,
        "final_sql":         final_sql,
        "sql_modified":      tenant_modified,
        "execution_success": execution_success,
        "row_count":         row_count,
        "truncated":         truncated,
        "retry_count":       retry_count,
        "error":             error or None,
        "blocked_reason":    blocked_reason or None,
        "answer_preview":    answer[:200] if answer else None,
        "token_usage":       token_usage,
        "latency_ms":        latency_ms,
        "tool_calls":        tool_calls or [],
    }
    with open(PIPELINE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    logger.info(
        f"Query logged | tenant={tenant_vkorg} | "
        f"success={execution_success} | rows={row_count} | "
        f"tokens={token_usage.get('total', 0)} | latency={latency_ms}ms"
    )


def log_security_event(
    event_type: str,
    detail: str,
    sql: str = "",
    tenant_vkorg: str = "",
    question: str = "",
):
    entry = {
        "timestamp":    datetime.utcnow().isoformat() + "Z",
        "event_type":   event_type,
        "detail":       detail,
        "tenant_vkorg": tenant_vkorg,
        "question":     question[:300] if question else None,
        "sql_snippet":  sql[:300] if sql else None,
    }
    with open(SECURITY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    logger.warning(f"SECURITY [{event_type}]: {detail}")


def load_pipeline_logs() -> list[dict]:
    if not PIPELINE_LOG.exists():
        return []
    entries = []
    with open(PIPELINE_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def load_security_logs() -> list[dict]:
    if not SECURITY_LOG.exists():
        return []
    entries = []
    with open(SECURITY_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries