#!/usr/bin/env python3
"""
load_data.py — Load all CSVs into PostgreSQL
Run: python load_data.py

Place this at the root of your project.
Update DATA_DIR to point to your extracted CSV folder.
"""

import os
import sys
import time
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# ── UPDATE THIS to your actual path ──────────────────────────────────────────
# Example: "/Users/Ganesha/Downloads/pre-processed data"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
# ─────────────────────────────────────────────────────────────────────────────

DB_CONFIG = {
    "dbname":   os.getenv("DB_NAME", "talk_to_your_data"),
    "user":     os.getenv("DB_USER", os.getenv("USER")),
    "password": os.getenv("DB_PASSWORD", ""),
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432"),
}

# Load order matters — respect foreign keys
TABLES = [
    ("companies",               "companies.csv"),
    ("customers",               "customers.csv"),
    ("order_headers",           "order_headers.csv"),
    ("order_lines",             "order_lines.csv"),
    ("daily_metrics_snapshots", "daily_metrics_snapshots.csv"),
]


def load_table(cursor, table: str, csv_path: str):
    print(f"  Loading {table}...", end=" ", flush=True)
    start = time.time()
    with open(csv_path, "r", encoding="utf-8") as f:
        cursor.copy_expert(
            f"COPY {table} FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '')",
            f,
        )
    elapsed = time.time() - start
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"{count:,} rows  ({elapsed:.1f}s)")


def main():
    print("\n=== Talk to Your Data — Data Loader ===\n")

    # Verify data directory
    if not os.path.isdir(DATA_DIR):
        print(f"ERROR: DATA_DIR not found: {DATA_DIR}")
        print("Update the DATA_DIR variable at the top of load_data.py")
        sys.exit(1)

    # Verify all CSVs exist
    for table, csv_file in TABLES:
        path = os.path.join(DATA_DIR, csv_file)
        if not os.path.exists(path):
            print(f"ERROR: Missing file: {path}")
            sys.exit(1)

    print(f"Data directory: {DATA_DIR}")
    print(f"Database:       {DB_CONFIG['dbname']} @ {DB_CONFIG['host']}\n")

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        cur = conn.cursor()

        print("Loading tables:")
        for table, csv_file in TABLES:
            csv_path = os.path.join(DATA_DIR, csv_file)
            load_table(cur, table, csv_path)

        conn.commit()
        print("\n✅ All tables loaded successfully!\n")

        # Final row count summary
        print("Row counts:")
        for table, _ in TABLES:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"  {table:<30} {cur.fetchone()[0]:>10,}")

        cur.close()
        conn.close()

    except psycopg2.Error as e:
        print(f"\n❌ Database error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()