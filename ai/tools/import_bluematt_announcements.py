#!/usr/bin/env python3
"""
Import BlueMatt channel-announcement CSVs into the lnhistory PostgreSQL database.

Loads 3 CSV files into staging tables, then merges into production tables using
ON CONFLICT DO NOTHING (safe for re-runs).  Processing order: inventory →
observations → channels, to satisfy foreign key dependencies.

Credentials are read from /home/bitcoin/ln-history-research/.env which uses
POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DBNAME / POSTGRES_USER / POSTGRES_PASSWORD.
Override any of them via environment variables, or pass --dsn directly.

Usage:
  python import_bluematt_announcements.py --csv-dir /path/to/csv/output
"""

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ENV_FILE = Path("/home/bitcoin/ln-history-research/.env")

# ── Staging → production table map ───────────────────────────────────────────

PIPELINE = [
    {
        "csv":     "inventory.csv",
        "staging": "staging_gossip_inventory",
        "target":  "gossip_inventory",
        # internal_id is NULL in the CSV (sequence-assigned by production).
        # COPY needs all columns; merge omits internal_id so the sequence fires.
        "staging_columns": "gossip_id, type, first_seen_at, internal_id",
        "merge_columns":   "gossip_id, type, first_seen_at",
    },
    {
        "csv":     "observations.csv",
        "staging": "staging_gossip_observations",
        "target":  "gossip_observations",
        # internal_id is nullable in gossip_observations — NULL is fine.
        "staging_columns": "internal_id, gossip_id, collector_node_id, seen_at, sender_timestamp",
        "merge_columns":   "internal_id, gossip_id, collector_node_id, seen_at, sender_timestamp",
    },
    {
        "csv":     "channels.csv",
        "staging": "staging_channels",
        "target":  "channels",
        # internal_id is nullable in channels — NULL is fine.
        "staging_columns": (
            "gossip_id, scid, funding_timestamp, closing_timestamp, capacity_sat, "
            "source_node_id, target_node_id, node_signature_1, node_signature_2, "
            "bitcoin_signature_1, bitcoin_signature_2, features, chain_hash, "
            "bitcoin_key_1, bitcoin_key_2, raw_gossip, internal_id"
        ),
        "merge_columns": (
            "gossip_id, scid, funding_timestamp, closing_timestamp, capacity_sat, "
            "source_node_id, target_node_id, node_signature_1, node_signature_2, "
            "bitcoin_signature_1, bitcoin_signature_2, features, chain_hash, "
            "bitcoin_key_1, bitcoin_key_2, raw_gossip, internal_id"
        ),
    },
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def connect(args):
    load_dotenv(ENV_FILE, override=False)
    if args.dsn:
        return psycopg2.connect(args.dsn)
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    # Container exposes port on 127.0.0.1; docker-internal hostnames won't resolve here.
    if host != "localhost" and not host.startswith("127."):
        host = "127.0.0.1"
    return psycopg2.connect(
        host=host,
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DBNAME", "lnhistory"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )


def copy_csv(cur, csv_path: Path, staging_table: str, staging_columns: str):
    """COPY a CSV file into staging_table via copy_expert (fast, no row-level overhead)."""
    size_mb = csv_path.stat().st_size / 1_048_576
    print(f"    COPY {csv_path.name} ({size_mb:.1f} MB) → {staging_table} ...")
    t0 = time.time()
    with csv_path.open("r", encoding="utf-8") as fh:
        cur.copy_expert(
            f"COPY {staging_table} ({staging_columns}) FROM STDIN "
            f"WITH (FORMAT CSV, HEADER TRUE, NULL '')",
            fh,
        )
    print(f"    done in {time.time() - t0:.1f}s  ({cur.rowcount:,} rows staged)")


def merge(cur, staging: str, target: str, merge_columns: str):
    """INSERT … ON CONFLICT DO NOTHING from staging into the production table."""
    sql = (
        f"INSERT INTO {target} ({merge_columns}) "
        f"SELECT {merge_columns} FROM {staging} "
        f"ON CONFLICT DO NOTHING"
    )
    t0 = time.time()
    cur.execute(sql)
    inserted = cur.rowcount
    print(f"    merged {inserted:,} new rows into {target}  ({time.time() - t0:.1f}s)")
    return inserted


def upsert_nodes_from_channels(cur):
    """Insert any source/target node_ids from staging_channels that are missing from nodes.

    Joins the production gossip_inventory (not staging, which is already truncated by
    the time this runs) to get first_seen_at for the new nodes.
    """
    sql = """
        INSERT INTO nodes (node_id, first_seen, last_seen)
        SELECT node_id, MIN(seen_at), MAX(seen_at)
        FROM (
            SELECT source_node_id AS node_id,
                   gi.first_seen_at AS seen_at
            FROM staging_channels sc
            JOIN gossip_inventory gi USING (gossip_id)
            UNION ALL
            SELECT target_node_id,
                   gi.first_seen_at
            FROM staging_channels sc
            JOIN gossip_inventory gi USING (gossip_id)
        ) t
        GROUP BY node_id
        ON CONFLICT (node_id) DO NOTHING
    """
    t0 = time.time()
    cur.execute(sql)
    inserted = cur.rowcount
    print(f"    upserted {inserted:,} new rows into nodes  ({time.time() - t0:.1f}s)")
    return inserted


def truncate_staging(cur, staging: str):
    cur.execute(f"TRUNCATE {staging}")
    print(f"    TRUNCATE {staging}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Import BlueMatt channel-announcement CSVs into lnhistory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Typical run — credentials come from /home/bitcoin/ln-history-research/.env
  python import_bluematt_announcements.py --csv-dir ./output

  # Explicit DSN override
  python import_bluematt_announcements.py --csv-dir ./output \\
    --dsn "host=127.0.0.1 dbname=lnhistory user=admin password=secret"

  # Dry-run: loads staging only, rolls back before any production write
  python import_bluematt_announcements.py --csv-dir ./output --dry-run
        """,
    )
    parser.add_argument(
        "--csv-dir", required=True,
        help="Directory containing inventory.csv, observations.csv, channels.csv",
    )
    parser.add_argument(
        "--dsn", default=None,
        help="Full libpq connection string (overrides env / .env file)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="COPY into staging but skip the production merge (rolls back everything)",
    )
    parser.add_argument(
        "--skip-observations", action="store_true",
        help="Skip the observations.csv step entirely (do not load gossip_observations). "
             "Use when storage cannot hold the ~108 GB observations growth. "
             "observations.csv is not required to exist when this is set.",
    )
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)

    # Optionally drop the observations step (storage-constrained runs).
    pipeline = [s for s in PIPELINE if not (args.skip_observations and s["csv"] == "observations.csv")]
    if args.skip_observations:
        print("    --skip-observations: gossip_observations will NOT be loaded")

    # Verify all CSV files exist before touching the database.
    missing = [s["csv"] for s in pipeline if not (csv_dir / s["csv"]).exists()]
    if missing:
        print(f"ERROR: missing CSV files in {csv_dir}: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    conn = connect(args)
    conn.autocommit = False

    total_inserted = 0
    try:
        for step in pipeline:
            csv_path  = csv_dir / step["csv"]
            staging   = step["staging"]
            target          = step["target"]
            staging_columns = step["staging_columns"]
            merge_columns   = step["merge_columns"]

            print(f"\n── {step['csv']}  →  {staging}  →  {target} ──")
            with conn.cursor() as cur:
                copy_csv(cur, csv_path, staging, staging_columns)

                if args.dry_run:
                    print("    [dry-run] skipping merge")
                    conn.rollback()
                    # Re-open transaction for the next staging load (so COPY works).
                    continue

                if target == "channels":
                    upsert_nodes_from_channels(cur)

                n = merge(cur, staging, target, merge_columns)
                total_inserted += n
                conn.commit()
                print("    committed")

                with conn.cursor() as cur2:
                    truncate_staging(cur2, staging)
                    conn.commit()

    except Exception as exc:
        conn.rollback()
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    if args.dry_run:
        print("\n[dry-run] rolled back all changes — no data written")
    else:
        print(f"\nDone — {total_inserted:,} total new rows inserted across all tables")


if __name__ == "__main__":
    main()
