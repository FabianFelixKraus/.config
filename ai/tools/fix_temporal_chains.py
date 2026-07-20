#!/home/bitcoin/ln-history-research/analysis/.venv/bin/python3
"""Repair SCD Type-2 temporal chains in the lnhistory database.

Enforces, per partition, ordered by valid_from:
  * valid_to(row N) == valid_from(row N+1)   (no gaps, no overlaps)
  * the most-recent row in each partition has valid_to IS NULL

Strict DBA architecture (see temporal-chain-verification-prompt.md):
  * A named server-side streaming cursor reads rows chronologically by
    partition (constant client memory, no 72M-row materialisation).
  * The "LEAD(valid_from)" is computed IN PYTHON by buffering one previous
    row per partition.
  * Only rows whose stored valid_to is actually wrong are collected, and
    they are flushed in batches of 10,000 with psycopg2.extras.execute_values
    using UPDATE ... FROM (VALUES %s) keyed on the primary key (gossip_id).

The read runs in a REPEATABLE READ, read-only snapshot on its own connection;
writes go through a second connection. Re-running is idempotent.

Usage:
  fix_temporal_chains.py --table channel_updates [--apply]
  fix_temporal_chains.py --table node_announcements_complete [--apply]
  fix_temporal_chains.py --table both [--apply]

Without --apply the script only DIAGNOSES (counts anomalies, writes nothing).
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv
import psycopg2
import psycopg2.extras


# ---- per-table configuration -------------------------------------------------
# order_cols: partition key columns followed by the chronological key, plus
# internal_id as a deterministic tiebreaker (matches the SQL diagnosis ordering).
TABLES = {
    "channel_updates": {
        "partition_cols": ("scid", "direction"),
        "select_cols": ("scid", "direction", "gossip_id", "valid_from", "valid_to", "internal_id"),
        "order_by": "scid, direction, valid_from, internal_id",
    },
    "node_announcements_complete": {
        "partition_cols": ("node_id",),
        "select_cols": ("node_id", "gossip_id", "valid_from", "valid_to", "internal_id"),
        "order_by": "node_id, valid_from, internal_id",
    },
}

BATCH_SIZE = 10_000
ITERSIZE = 50_000  # server-side cursor fetch chunk


def conn_params():
    load_dotenv(os.path.join(os.getcwd(), ".env"))
    host = os.getenv("DB_HOST", "localhost")
    if host != "localhost" and not host.startswith("127."):
        host = "localhost"
    params = {
        "host": host,
        "port": int(os.getenv("DB_PORT", 5432)),
        "dbname": "lnhistory",
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "options": "-c search_path=public",
    }
    missing = [k for k, v in params.items() if v is None]
    if missing:
        print(f"Error: missing .env variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return params


def flush(write_conn, table, batch):
    """Bulk-apply a batch of (new_valid_to, gossip_id) fixes via execute_values."""
    if not batch:
        return 0
    sql = (
        f"UPDATE {table} AS t "
        f"SET valid_to = v.new_valid_to::timestamptz "
        f"FROM (VALUES %s) AS v(new_valid_to, gossip_id) "
        f"WHERE t.gossip_id = v.gossip_id"
    )
    with write_conn.cursor() as wcur:
        psycopg2.extras.execute_values(wcur, sql, batch, template="(%s, %s)", page_size=BATCH_SIZE)
    write_conn.commit()
    return len(batch)


def repair_table(table, apply):
    cfg = TABLES[table]
    pcols = cfg["partition_cols"]
    scols = cfg["select_cols"]
    # index positions inside each fetched row tuple
    idx = {c: i for i, c in enumerate(scols)}
    p_idx = [idx[c] for c in pcols]
    gid_i, vf_i, vt_i = idx["gossip_id"], idx["valid_from"], idx["valid_to"]

    read_conn = psycopg2.connect(**conn_params())
    read_conn.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
    write_conn = psycopg2.connect(**conn_params()) if apply else None
    if write_conn:
        write_conn.autocommit = False

    # anomaly counters
    n_rows = 0
    n_chain_fix = 0   # valid_to should equal next valid_from but doesn't
    n_tail_fix = 0    # last row in partition should be NULL but isn't
    n_bad_order = 0   # valid_from >= valid_to after correction (dup-timestamp artifact)
    n_updated = 0
    batch = []

    def key(row):
        return tuple(row[i] for i in p_idx)

    def correct(row, new_vt):
        """If row's stored valid_to != desired new_vt, queue a fix."""
        nonlocal n_updated
        if row[vt_i] != new_vt:  # None != ts, or ts != ts — the anomaly test
            batch.append((new_vt, row[gid_i]))
            if len(batch) >= BATCH_SIZE and apply:
                n_updated += flush(write_conn, table, batch)
                batch.clear()

    cur = read_conn.cursor(name=f"streaming_cursor_{table}")
    cur.itersize = ITERSIZE
    cur.execute(f"SELECT {', '.join(scols)} FROM {table} ORDER BY {cfg['order_by']}")

    prev = None
    t0 = time.time()
    for row in cur:
        n_rows += 1
        if prev is not None:
            if key(row) == key(prev):
                # same partition: prev.valid_to must equal this row's valid_from
                desired = row[vf_i]
                if prev[vt_i] != desired:
                    n_chain_fix += 1
                    correct(prev, desired)
                if desired is not None and row[vf_i] is not None and prev[vf_i] is not None \
                        and prev[vf_i] >= desired:
                    n_bad_order += 1
            else:
                # partition boundary: prev was the tail -> valid_to must be NULL
                if prev[vt_i] is not None:
                    n_tail_fix += 1
                    correct(prev, None)
        prev = row
        if n_rows % 1_000_000 == 0:
            rate = n_rows / max(time.time() - t0, 1e-9)
            print(f"  [{table}] scanned {n_rows:,} rows "
                  f"(chain={n_chain_fix:,} tail={n_tail_fix:,} updated={n_updated:,}) "
                  f"{rate:,.0f} rows/s", flush=True)

    # final buffered row is the tail of the last partition
    if prev is not None and prev[vt_i] is not None:
        n_tail_fix += 1
        correct(prev, None)

    if apply and batch:
        n_updated += flush(write_conn, table, batch)
        batch.clear()

    cur.close()
    read_conn.rollback()  # read-only snapshot, nothing to commit
    read_conn.close()
    if write_conn:
        write_conn.close()

    anomalies = n_chain_fix + n_tail_fix
    print(f"\n=== {table} ===", flush=True)
    print(f"  rows scanned          : {n_rows:,}")
    print(f"  chain breaks (gap/ovl): {n_chain_fix:,}")
    print(f"  tail not NULL         : {n_tail_fix:,}")
    print(f"  total anomalies       : {anomalies:,}")
    print(f"  dup-timestamp (vf>=vt): {n_bad_order:,}  (zero-length interval artifact)")
    if apply:
        print(f"  rows UPDATED          : {n_updated:,}")
    else:
        print(f"  DRY RUN — no rows written (pass --apply to repair)")
    return {"table": table, "rows": n_rows, "chain": n_chain_fix,
            "tail": n_tail_fix, "anomalies": anomalies, "bad_order": n_bad_order,
            "updated": n_updated}


def main():
    ap = argparse.ArgumentParser(description="Repair SCD2 temporal chains.")
    ap.add_argument("--table", choices=["channel_updates", "node_announcements_complete", "both"],
                    default="both")
    ap.add_argument("--apply", action="store_true", help="Actually write fixes (default: dry-run)")
    args = ap.parse_args()

    targets = (["channel_updates", "node_announcements_complete"]
               if args.table == "both" else [args.table])

    print(f"fix_temporal_chains.py starting  mode={'APPLY' if args.apply else 'DRY-RUN'}  "
          f"tables={targets}", flush=True)
    t0 = time.time()
    summary = [repair_table(t, args.apply) for t in targets]
    dt = time.time() - t0

    print("\n================ SUMMARY ================", flush=True)
    for s in summary:
        print(f"  {s['table']:>28}: {s['anomalies']:,} anomalies "
              f"({s['chain']:,} chain + {s['tail']:,} tail), "
              f"{s['updated']:,} updated")
    print(f"  elapsed: {dt/60:.1f} min")


if __name__ == "__main__":
    main()
