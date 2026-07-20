#!/usr/bin/env python3
"""
fix_nodes_timestamps.py

Recalculates first_seen / last_seen for every row in `nodes` using
MIN/MAX(valid_from) from node_announcements_complete as the source of truth.

Runs as a single bulk UPDATE inside one transaction — no loops, no N+1 queries.
"""

import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path("/home/bitcoin/ln-history-research/.env"), override=True)

SQL = """
WITH node_timestamps AS (
    SELECT node_id,
           MIN(valid_from) AS first_seen,
           MAX(valid_from) AS last_seen
    FROM node_announcements_complete
    WHERE node_id IS NOT NULL
    GROUP BY node_id
),
channel_timestamps AS (
    SELECT node_id,
           MIN(funding_timestamp) AS first_seen,
           MAX(funding_timestamp) AS last_seen
    FROM (
        SELECT source_node_id AS node_id, funding_timestamp FROM channels
        WHERE funding_timestamp IS NOT NULL
        UNION ALL
        SELECT target_node_id AS node_id, funding_timestamp FROM channels
        WHERE funding_timestamp IS NOT NULL
    ) ch
    WHERE node_id IS NOT NULL
    GROUP BY node_id
),
combined AS (
    SELECT
        COALESCE(na.node_id, ct.node_id)        AS node_id,
        COALESCE(na.first_seen, ct.first_seen)  AS first_seen,
        COALESCE(na.last_seen,  ct.last_seen)   AS last_seen
    FROM node_timestamps na
    FULL OUTER JOIN channel_timestamps ct ON na.node_id = ct.node_id
)
UPDATE nodes n
SET
    first_seen = c.first_seen,
    last_seen  = c.last_seen
FROM combined c
WHERE n.node_id = c.node_id
"""

VERIFY_SQL = """
SELECT
    COUNT(*)                                            AS total_nodes,
    COUNT(*) FILTER (WHERE first_seen = TIMESTAMPTZ '1970-01-01') AS epoch_first_seen,
    COUNT(*) FILTER (WHERE last_seen  = TIMESTAMPTZ '1970-01-01') AS epoch_last_seen,
    COUNT(*) FILTER (WHERE first_seen IS NULL)          AS null_first_seen,
    COUNT(*) FILTER (WHERE last_seen  IS NULL)          AS null_last_seen
FROM nodes
"""


def get_conn():
    dsn = os.environ.get("POSTGRES_URI")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ.get("POSTGRES_PORT", 5432),
        dbname=os.environ["POSTGRES_DBNAME"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def main():
    print("Connecting to database...")
    conn = get_conn()
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            # Pre-flight: snapshot of current anomaly count
            print("Pre-flight check...")
            cur.execute(
                "SELECT COUNT(*) FROM nodes WHERE first_seen = TIMESTAMPTZ '1970-01-01'"
            )
            epoch_before = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM nodes")
            total = cur.fetchone()[0]
            print(f"  nodes total:          {total:,}")
            print(f"  epoch first_seen (before): {epoch_before:,}")

            # Run the bulk update
            print("\nRunning bulk UPDATE...")
            t0 = time.monotonic()
            cur.execute(SQL)
            rows_updated = cur.rowcount
            elapsed = time.monotonic() - t0
            print(f"  rows updated: {rows_updated:,}  ({elapsed:.1f}s)")

            # Post-update verification (still inside the transaction)
            print("\nPost-update verification (pre-commit)...")
            cur.execute(VERIFY_SQL)
            row = cur.fetchone()
            print(f"  total_nodes:       {row[0]:,}")
            print(f"  epoch_first_seen:  {row[1]:,}  (should be 0)")
            print(f"  epoch_last_seen:   {row[2]:,}  (should be 0)")
            print(f"  null_first_seen:   {row[3]:,}")
            print(f"  null_last_seen:    {row[4]:,}")

            if row[1] != 0 or row[2] != 0:
                print("\nERROR: epoch timestamps still present — rolling back.")
                conn.rollback()
                sys.exit(1)

            conn.commit()
            print("\nCommitted successfully.")

    except Exception as exc:
        conn.rollback()
        print(f"\nERROR: {exc} — transaction rolled back.")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
