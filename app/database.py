import logging
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.pool
import psycopg2.extras

from app.config import config

logger = logging.getLogger(__name__)

# Module-level connection pool (initialised once)
_pool: psycopg2.pool.SimpleConnectionPool | None = None


def init_pool():
    _pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=5,
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        options="-c default_transaction_read_only=on",  # READ-ONLY enforcement
    )
    logger.info(f"Database pool initialised: {config.DB_NAME} @ {config.DB_HOST}")


def get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        init_pool()
    return _pool


@contextmanager
def get_connection():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


def execute_query(
    sql: str,
    params: tuple = (),
    row_limit: int | None = None,
) -> dict[str, Any]:
   
    limit = row_limit or config.MAX_ROWS_RETURNED

    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                # Execute the query
                cur.execute(sql, params)
                rows = cur.fetchmany(limit + 1)  # fetch one extra to detect truncation

                truncated = len(rows) > limit
                rows = rows[:limit]

                columns = [desc[0] for desc in cur.description] if cur.description else []
                rows_as_dicts = [dict(row) for row in rows]

                return {
                    "success":     True,
                    "rows":        rows_as_dicts,
                    "row_count":   len(rows_as_dicts),
                    "total_count": len(rows_as_dicts) + (1 if truncated else 0),
                    "columns":     columns,
                    "truncated":   truncated,
                    "error":       None,
                }

    except psycopg2.Error as e:
        logger.warning(f"Query failed: {e}")
        return {
            "success":     False,
            "rows":        [],
            "row_count":   0,
            "total_count": 0,
            "columns":     [],
            "truncated":   False,
            "error":       str(e),
        }


def test_connection() -> bool:
    try:
        result = execute_query("SELECT 1 AS ok")
        return result["success"]
    except Exception as e:
        logger.error(f"DB connection test failed: {e}")
        return False