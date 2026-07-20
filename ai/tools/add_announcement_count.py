#!/usr/bin/env python3
"""
add_announcement_count.py

Adds an `announcement_count` integer column to `nodes`, backfills it with
the total number of rows per node_id in `node_announcements_complete`, and
installs an AFTER INSERT trigger to keep it current for future inserts.

All three operations run inside a single transaction. The script is idempotent:
re-running after a partial failure is safe.
"""

import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path("/home/bitcoin/ln-history-research/.env"), override=True)

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

SQL_ADD_COLUMN = """
ALTER TABLE nodes
  ADD COLUMN IF NOT EXISTS announcement_count integer NOT NULL DEFAULT 0
"""

SQL_BACKFILL = """
WITH counts AS (
    SELECT node_id, COUNT(gossip_id) AS cnt
    FROM node_announcements_complete
    WHERE node_id IS NOT NULL
    GROUP BY node_id
)
UPDATE nodes
SET announcement_count = counts.cnt
FROM counts
WHERE nodes.node_id = counts.node_id
"""

SQL_CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION increment_announcement_count()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE nodes
    SET announcement_count = announcement_count + 1
    WHERE node_id = NEW.node_id;
    RETURN NEW;
END;
$$
"""

# DROP + CREATE so re-runs are idempotent without IF NOT EXISTS on CREATE TRIGGER
SQL_DROP_TRIGGER = """
DROP TRIGGER IF EXISTS trg_increment_announcement_count
  ON node_announcements_complete
"""

SQL_CREATE_TRIGGER = """
CREATE TRIGGER trg_increment_announcement_count
AFTER INSERT ON node_announcements_complete
FOR EACH ROW EXECUTE FUNCTION increment_announcement_count()
"""

SQL_VERIFY = """
SELECT
    COUNT(*)                                          AS total_nodes,
    COUNT(*) FILTER (WHERE announcement_count = 0)   AS zero_count_nodes,
    SUM(announcement_count)                          AS total_announcements,
    MAX(announcement_count)                          AS max_count,
    MIN(announcement_count) FILTER (WHERE announcement_count > 0) AS min_nonzero
FROM nodes
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("Connecting to database...")
    conn = get_conn()
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            # ------------------------------------------------------------------
            # Step 1: ADD COLUMN (DDL — must be outside the main transaction on
            # some Postgres versions, but ADD COLUMN IF NOT EXISTS is safe to
            # run inside a transaction block in Postgres 9.6+).
            # ------------------------------------------------------------------
            print("Step 1: Adding column announcement_count (IF NOT EXISTS)...")
            cur.execute(SQL_ADD_COLUMN)
            print("  Done.")

            # ------------------------------------------------------------------
            # Step 2: Bulk backfill — single CTE + UPDATE … FROM
            # ------------------------------------------------------------------
            print("Step 2: Backfilling announcement_count from node_announcements_complete...")
            print("  (This will scan ~22M rows — expected 15-60s on NVMe)")
            t0 = time.monotonic()
            cur.execute(SQL_BACKFILL)
            rows_updated = cur.rowcount
            elapsed = time.monotonic() - t0
            print(f"  Rows updated: {rows_updated:,}  ({elapsed:.1f}s)")

            # ------------------------------------------------------------------
            # Step 3: Trigger function + trigger
            # ------------------------------------------------------------------
            print("Step 3: Creating trigger function increment_announcement_count()...")
            cur.execute(SQL_CREATE_FUNCTION)

            print("Step 3: Installing AFTER INSERT trigger on node_announcements_complete...")
            cur.execute(SQL_DROP_TRIGGER)
            cur.execute(SQL_CREATE_TRIGGER)
            print("  Trigger installed.")

            # ------------------------------------------------------------------
            # Verification (pre-commit)
            # ------------------------------------------------------------------
            print("\nVerification (pre-commit)...")
            cur.execute(SQL_VERIFY)
            row = cur.fetchone()
            total_nodes, zero_nodes, total_ann, max_count, min_nonzero = row
            print(f"  total_nodes:        {total_nodes:,}")
            print(f"  zero_count_nodes:   {zero_nodes:,}  (nodes with no announcements)")
            print(f"  total_announcements:{total_ann:,}")
            print(f"  max_count:          {max_count:,}")
            print(f"  min_nonzero:        {min_nonzero:,}")

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
