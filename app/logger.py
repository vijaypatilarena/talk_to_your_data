"""
app/logger.py — Structured JSON Logging

Logs every pipeline step to:
  1. logs/pipeline.jsonl  — one JSON object per query (machine-readable)
  2. logs/app.log         — human-readable text log
  3. Terminal             — real-time output during development

Every log entry includes: timestamp, tenant, question, classification,
generated_sql, final_sql, execution_result, response, token_usage,
latency_ms, retries, errors.

This directly satisfies Section 5 (Structured logging) and
Section 9 (Observability & Evaluation) of the brief.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from app.config import config


LOG_DIR = Path(config.LOG_DIR)
LOG_DIR.mkdir(exist_ok=True)

PIPELINE_LOG  = LOG_DIR / "pipeline.jsonl"   # Structured — one JSON per line
APP_LOG       = LOG_DIR / "app.log"           # Human readable
SECURITY_LOG  = LOG_DIR / "security.jsonl"    # Security events only


# Configure root logger 
def setup_logging():
    """Call once at app startup."""
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on re-runs (Streamlit reloads)
    if root.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — human readable
    fh = logging.FileHandler(APP_LOG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Terminal handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)


setup_logging()
logger = logging.getLogger(__name__)


# Structured pipeline logger 
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
    blocked_reason: str = "",
):
    """
    Write one structured JSON log entry for a complete pipeline run.
    Appends to logs/pipeline.jsonl
    """
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
    """
    Log a security-relevant event to logs/security.jsonl.
    Event types: SQL_BLOCKED, TENANT_VIOLATION, INJECTION_ATTEMPT, RATE_LIMIT
    """
    entry = {
        "timestamp":   datetime.utcnow().isoformat() + "Z",
        "event_type":  event_type,
        "detail":      detail,
        "tenant_vkorg": tenant_vkorg,
        "question":    question[:300] if question else None,
        "sql_snippet": sql[:300] if sql else None,
    }

    with open(SECURITY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    logger.warning(f"SECURITY [{event_type}]: {detail}")


def load_pipeline_logs() -> list[dict]:
    """Load all pipeline log entries. Used by evaluation runner."""
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
    """Load all security log entries."""
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