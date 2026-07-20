#!/usr/bin/env python3
"""
Import BlueMatt channel_updates CSV into the lnhistory PostgreSQL database.

Adapted from import_bluematt_announcements.py.  Inherits the same
copy_expert COPY architecture but adds four Senior DBA safeguards:

  1. Staging table created dynamically (LIKE channel_updates INCLUDING DEFAULTS)
     so it always mirrors the production schema.
  2. Session timezone is forced to UTC before every COPY to prevent silent
     timestamp coercion when the server TZ differs.
  3. All non-PK indexes on channel_updates are dropped before the bulk INSERT
     and rebuilt with CREATE INDEX CONCURRENTLY afterwards (10-20× faster
     than maintaining 11 indexes live during a 160 M-row merge).
  4. The merge uses a WHERE EXISTS guard so orphaned rows (gossip_id not yet
     in gossip_inventory) are silently skipped instead of aborting the
     transaction with a FK violation.

Credentials are read from /home/bitcoin/ln-history-research/.env which uses
POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DBNAME / POSTGRES_USER / POSTGRES_PASSWORD.
Override any of them via environment variables, or pass --dsn directly.

Usage:
  python import_bluematt_updates.py --csv-dir /path/to/csv/output
  python import_bluematt_updates.py --csv-dir ./output --dry-run
  python import_bluematt_updates.py --csv-dir ./output --skip-index-drop
"""

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ENV_FILE = Path("/home/bitcoin/ln-history-research/.env")

STAGING_TABLE    = "staging_channel_updates"
PRODUCTION_TABLE = "channel_updates"
CSV_FILENAME     = "updates.csv"

# Columns exported by updates_sql() in insert-bluematt-gossip-multiprocess.py,
# in the exact order they appear in the CSV header.
CSV_COLUMNS = (
    "gossip_id, scid, direction, valid_from, valid_to, signature, chain_hash, "
    "message_flags, channel_flags, cltv_expiry_delta, htlc_minimum_msat, "
    "fee_base_msat, fee_proportional_millionths, htlc_maximum_msat, raw_gossip, "
    "is_fee_update, is_topology_update, internal_id"
)

# Columns that exist in both staging and production and that we want to INSERT.
# Excludes internal_id — it is NULL in the CSV; let the production sequence assign it.
MERGE_COLUMNS = (
    "gossip_id, scid, direction, valid_from, valid_to, signature, chain_hash, "
    "message_flags, channel_flags, cltv_expiry_delta, htlc_minimum_msat, "
    "fee_base_msat, fee_proportional_millionths, htlc_maximum_msat, raw_gossip, "
    "is_fee_update, is_topology_update"
)


# ── Connection ────────────────────────────────────────────────────────────────

def connect(dsn_override):
    load_dotenv(ENV_FILE, override=False)
    if dsn_override:
        return psycopg2.connect(dsn_override)
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    if host != "localhost" and not host.startswith("127."):
        host = "127.0.0.1"
    return psycopg2.connect(
        host=host,
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DBNAME", "lnhistory"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )


# ── Risk 1: staging table ─────────────────────────────────────────────────────

def ensure_staging_table(cur):
    """CREATE TABLE IF NOT EXISTS staging mirrors the live production schema."""
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {STAGING_TABLE}
            (LIKE {PRODUCTION_TABLE} INCLUDING DEFAULTS)
    """)
    print(f"    Ensured {STAGING_TABLE} exists (LIKE {PRODUCTION_TABLE} INCLUDING DEFAULTS)")


def truncate_staging(cur):
    cur.execute(f"TRUNCATE {STAGING_TABLE}")
    print(f"    TRUNCATE {STAGING_TABLE}")


# ── Risk 6: index management ──────────────────────────────────────────────────

def fetch_non_pk_indexes(cur):
    """
    Return a list of (index_name, indexdef) for every non-PK index on
    channel_updates.  indexdef is the full CREATE INDEX … statement that
    pg_indexes stores — ready to re-execute verbatim.
    """
    cur.execute("""
        SELECT indexname, indexdef
        FROM   pg_indexes
        WHERE  tablename = %s
          AND  indexname != %s
        ORDER  BY indexname
    """, (PRODUCTION_TABLE, f"{PRODUCTION_TABLE}_pkey"))
    rows = cur.fetchall()
    print(f"    Found {len(rows)} non-PK index(es) on {PRODUCTION_TABLE}:")
    for name, defn in rows:
        print(f"      {name}")
    return rows


def drop_indexes(conn, indexes):
    """DROP each index outside a transaction (requires autocommit=True)."""
    conn.autocommit = True
    with conn.cursor() as cur:
        for name, _ in indexes:
            print(f"    DROP INDEX {name} ...")
            t0 = time.time()
            cur.execute(f"DROP INDEX IF EXISTS {name}")
            print(f"      done in {time.time() - t0:.1f}s")
    conn.autocommit = False


def rebuild_indexes(conn, indexes):
    """
    Rebuild every dropped index with CREATE INDEX CONCURRENTLY.
    CONCURRENTLY cannot run inside a transaction, so autocommit must be True.
    Errors during rebuild are logged but do not abort the run — the data is
    already committed; a failed index can be rebuilt manually.
    """
    conn.autocommit = True
    with conn.cursor() as cur:
        for name, defn in indexes:
            # Replace CREATE INDEX with CREATE INDEX CONCURRENTLY so the rebuild
            # does not block production reads.  pg_indexes already stores the
            # canonical CREATE INDEX statement without CONCURRENTLY.
            concurrent_defn = defn.replace(
                "CREATE INDEX", "CREATE INDEX CONCURRENTLY", 1
            ).replace(
                "CREATE UNIQUE INDEX", "CREATE UNIQUE INDEX CONCURRENTLY", 1
            )
            print(f"    {concurrent_defn[:80]}{'…' if len(concurrent_defn) > 80 else ''}")
            t0 = time.time()
            try:
                cur.execute(concurrent_defn)
                print(f"      {name}: built in {time.time() - t0:.1f}s")
            except Exception as exc:
                print(f"      WARNING: failed to rebuild {name}: {exc}", file=sys.stderr)
    conn.autocommit = False


# ── Risk 3 + COPY ─────────────────────────────────────────────────────────────

def copy_csv(cur, csv_path: Path):
    """
    Force UTC on the session, then COPY the updates CSV into the staging table.
    UTC is set here (not at connect time) so it is in effect for every COPY
    regardless of server default.
    """
    cur.execute("SET TIME ZONE 'UTC'")

    size_mb = csv_path.stat().st_size / 1_048_576
    print(f"    COPY {csv_path.name} ({size_mb:.1f} MB) → {STAGING_TABLE} ...")
    t0 = time.time()
    with csv_path.open("r", encoding="utf-8") as fh:
        cur.copy_expert(
            f"COPY {STAGING_TABLE} ({CSV_COLUMNS}) FROM STDIN "
            f"WITH (FORMAT CSV, HEADER TRUE, NULL '')",
            fh,
        )
    print(f"    done in {time.time() - t0:.1f}s  ({cur.rowcount:,} rows staged)")


# ── Risk 2: FK-shielded merge ─────────────────────────────────────────────────

def merge_into_production(cur):
    """
    INSERT only rows whose gossip_id already exists in gossip_inventory.
    Orphaned rows (no parent) are silently skipped rather than aborting
    the transaction with a FK violation.
    """
    sql = f"""
        INSERT INTO {PRODUCTION_TABLE} ({MERGE_COLUMNS})
        SELECT {MERGE_COLUMNS}
        FROM   {STAGING_TABLE} s
        WHERE  EXISTS (
            SELECT 1
            FROM   gossip_inventory gi
            WHERE  gi.gossip_id = s.gossip_id
        )
        ON CONFLICT DO NOTHING
    """
    t0 = time.time()
    cur.execute(sql)
    inserted = cur.rowcount
    print(
        f"    merged {inserted:,} new rows into {PRODUCTION_TABLE}  "
        f"({time.time() - t0:.1f}s)"
    )
    return inserted


# ── Pre-flight orphan count ───────────────────────────────────────────────────

def count_orphans(cur):
    """
    Report how many staged rows will be skipped due to a missing parent in
    gossip_inventory.  Runs before the merge so the operator can decide
    whether to halt and backfill.
    """
    cur.execute(f"""
        SELECT COUNT(*)
        FROM   {STAGING_TABLE} s
        WHERE  NOT EXISTS (
            SELECT 1
            FROM   gossip_inventory gi
            WHERE  gi.gossip_id = s.gossip_id
        )
    """)
    n = cur.fetchone()[0]
    if n > 0:
        print(
            f"    WARNING: {n:,} staged rows have no matching gossip_inventory entry "
            f"and will be skipped by the merge."
        )
    else:
        print("    FK pre-check: all staged gossip_ids are present in gossip_inventory.")
    return n


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Import BlueMatt channel_updates CSV into lnhistory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Typical run — credentials come from /home/bitcoin/ln-history-research/.env
  python import_bluematt_updates.py --csv-dir ./output

  # Explicit DSN override
  python import_bluematt_updates.py --csv-dir ./output \\
    --dsn "host=127.0.0.1 dbname=lnhistory user=admin password=secret"

  # Dry-run: stages but does not merge or touch indexes
  python import_bluematt_updates.py --csv-dir ./output --dry-run

  # Skip the index drop/rebuild (e.g. re-run after partial failure)
  python import_bluematt_updates.py --csv-dir ./output --skip-index-drop
        """,
    )
    parser.add_argument(
        "--csv-dir", required=True,
        help=f"Directory containing {CSV_FILENAME}",
    )
    parser.add_argument(
        "--dsn", default=None,
        help="Full libpq connection string (overrides env / .env file)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="COPY into staging but skip the production merge and index work",
    )
    parser.add_argument(
        "--skip-index-drop", action="store_true",
        help="Merge without dropping/rebuilding indexes (slower but safer for re-runs)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_dir) / CSV_FILENAME
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found", file=sys.stderr)
        sys.exit(1)

    conn = connect(args.dsn)
    conn.autocommit = False

    saved_indexes = []

    try:
        # ── Step 1: ensure staging table exists ──────────────────────────────
        print(f"\n── Staging table ──")
        with conn.cursor() as cur:
            ensure_staging_table(cur)
            truncate_staging(cur)
        conn.commit()

        # ── Step 2: drop non-PK indexes (outside transaction, autocommit) ────
        if not args.dry_run and not args.skip_index_drop:
            print(f"\n── Index management — pre-load ──")
            with conn.cursor() as cur:
                saved_indexes = fetch_non_pk_indexes(cur)
            conn.commit()
            drop_indexes(conn, saved_indexes)   # sets autocommit=True internally
            # autocommit is restored to False inside drop_indexes

        # ── Step 3: COPY CSV into staging ────────────────────────────────────
        print(f"\n── COPY → {STAGING_TABLE} ──")
        with conn.cursor() as cur:
            copy_csv(cur, csv_path)
        conn.commit()
        print("    committed staging load")

        if args.dry_run:
            print("\n[dry-run] stopping after staging load — no production merge performed")
            return

        # ── Step 4: orphan pre-flight check ──────────────────────────────────
        print(f"\n── FK pre-check ──")
        with conn.cursor() as cur:
            count_orphans(cur)
        conn.commit()

        # ── Step 5: FK-shielded merge into production ─────────────────────────
        print(f"\n── Merge → {PRODUCTION_TABLE} ──")
        with conn.cursor() as cur:
            total_inserted = merge_into_production(cur)
        conn.commit()
        print("    committed production merge")

        # ── Step 6: truncate staging ─────────────────────────────────────────
        with conn.cursor() as cur:
            truncate_staging(cur)
        conn.commit()

    except Exception as exc:
        conn.rollback()
        print(f"\nERROR: {exc}", file=sys.stderr)
        # If indexes were dropped but the merge failed, warn the operator so they
        # can rebuild manually rather than leaving the table index-free.
        if saved_indexes:
            print(
                "\nWARNING: indexes were dropped before the error.  "
                "Rebuild them manually or re-run without --skip-index-drop.",
                file=sys.stderr,
            )
            for name, defn in saved_indexes:
                print(f"  {defn}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    # ── Step 7: rebuild indexes (outside transaction, autocommit) ────────────
    if saved_indexes:
        conn2 = connect(args.dsn)
        try:
            print(f"\n── Index management — post-load ──")
            rebuild_indexes(conn2, saved_indexes)
        finally:
            conn2.close()

    print(f"\nDone — {total_inserted:,} new rows inserted into {PRODUCTION_TABLE}")


if __name__ == "__main__":
    main()
