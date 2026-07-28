#!/usr/bin/env python3
"""
Phase 1a — raw_gossip framing blast-radius ESTIMATE (read-only, sample-based).

Answers "how big is the framing-drift problem" WITHOUT a full 178M-row / 150+GB scan.
Draws a random sample per table (uniform over the gossip_id PK, a sha256 hex, so draws
spread evenly across rows), post-stratifies by ingest era, classifies each blob
structurally with the lnhistoryclient parser, and estimates per table, with Wilson 95% CIs:

  - drifted %  (non-conformant to the canonical envelope)
  - corruption-class mix
  - collision/twin %  (of drifted rows: canonicalizing yields a gossip_id that ALREADY
                       exists as a distinct row -> repair must MERGE, not rewrite)
  - source split       (surviving real-collector observation vs none -> bulk-import origin)
  - drift-by-era        (post-stratified by gossip_inventory.first_seen_at year)
  - hash-integrity      (gossip_id == sha256(raw_gossip) today) as a SEPARATE signal

CANONICAL (confirmed against gossip-processor/main.py::_build_raw_gossip):
    raw_gossip = varint_le(len(payload)) ++ uint16_be(type) ++ payload
    (the varint EXCLUDES the 2-byte type; gossip_id = sha256(raw_gossip))

READ-ONLY. Uses the ai_reader role. No writes. Every DB round-trip is an indexed lookup
or a bulk ANY() probe; there is no sequential scan of a big table. Still, per the
hardware-aware protocol, run ln_db_explain.py on the sampling query for channel_updates
once before trusting a large --sample-size.

Run on the DB host with the analysis venv:
    /home/bitcoin/ln-history-research/analysis/.venv/bin/python3 \
        audit_raw_gossip_framing.py --env /home/bitcoin/ln-history-research/analysis/.env \
        --sample-size 20000 --out-dir ./raw_gossip_audit
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional

# psycopg is imported lazily inside main() so the pure classifier can be imported/tested
# without a DB driver present.

# --- lnhistoryclient: reuse parsing + structural-length logic, never re-implement BOLT ---
from lnhistoryclient.parser.common import varint_decode, varint_encode
from lnhistoryclient.parser import parser_factory
from lnhistoryclient.api.requester import _unframed_message_length, _is_plausible

KNOWN_TYPES = {256, 257, 258}
REAL_COLLECTOR_ALIASES = ("alice", "alice-new", "bob", "bob-new")

# table -> expected BOLT type (each in-scope table is single-type)
TABLES = {
    "channels": 256,
    "channel_updates": 258,
    "node_announcements": 257,
    "node_announcements_complete": 257,
}


# --------------------------------------------------------------------------- classifier
def _varint_size(first: int) -> int:
    return 1 if first < 0xFD else 3 if first == 0xFD else 5 if first == 0xFE else 9


def _try_parse(msg_type: int, body: bytes):
    """Parse a payload body (type already stripped) for msg_type; None on failure."""
    try:
        return parser_factory.get_parser_by_message_type(msg_type)(body)
    except Exception:
        return None


def _canonical(msg_type: int, payload: bytes) -> bytes:
    return varint_encode(len(payload)) + msg_type.to_bytes(2, "big") + payload


def classify(b: bytes):
    """Return (klass, recovered_type, canonical_bytes | None).

    klass is one of:
      conformant, missing_varint, trailing_junk, wrong_length_varint,
      wrong_endianness, truncated, unknown_type, unparseable
    canonical_bytes is the re-emitted canonical envelope when the message is
    recoverable (== b when already conformant); None when unrecoverable.
    """
    n = len(b)
    if n < 4:
        return ("truncated", None, None)

    saw_known_type = False   # a known type byte was located under some hypothesis
    needs_more = False       # a known type was located but its structure needs more bytes

    # --- Hypothesis F: framed with the canonical layout (varint_le = payload_len) ---
    s = _varint_size(b[0])
    if n >= s + 2:
        n_val = varint_decode(io.BytesIO(bytes(b[:s])))  # little-endian value
        t = int.from_bytes(b[s : s + 2], "big")
        if t in KNOWN_TYPES:
            saw_known_type = True
            L = _unframed_message_length(b, s)  # length incl. type of msg at offset s
            if L is not None and s + L <= n:
                payload = b[s + 2 : s + L]
                p = _try_parse(t, payload)
                if p is not None and _is_plausible(p):
                    canon = _canonical(t, payload)
                    if n_val == L - 2 and n == s + L:
                        return ("conformant", t, canon)  # canon == b
                    if n == s + L and n_val != L - 2:
                        # right total length, wrong declared value (e.g. counts type: off-by-2)
                        return ("wrong_length_varint", t, canon)
                    if n > s + L:
                        return ("trailing_junk", t, canon)
                    return ("wrong_length_varint", t, canon)
            else:
                # Known type under the framed reading, but its structure runs past the
                # stored bytes (L is None => a length field itself is missing, or s+L>n).
                needs_more = True

    # --- Hypothesis U: unframed, starts directly with the 2-byte type ---
    t0 = int.from_bytes(b[0:2], "big")
    if t0 in KNOWN_TYPES:
        saw_known_type = True
        L0 = _unframed_message_length(b, 0)
        if L0 is not None and L0 <= n:
            payload = b[2:L0]
            p = _try_parse(t0, payload)
            if p is not None and _is_plausible(p):
                return ("missing_varint", t0, _canonical(t0, payload))
            if n > L0:
                return ("trailing_junk", t0, None)  # type known, junk, but payload bad
        else:
            needs_more = True  # L0 is None or L0 > n => truncated under unframed reading

    # --- Hypothesis BE: multi-byte varint only decodes sensibly as big-endian ---
    if s > 1 and n >= s + 2:
        n_be = varint_decode(io.BytesIO(bytes(b[:s])), big_endian=True)
        t = int.from_bytes(b[s : s + 2], "big")
        if t in KNOWN_TYPES and n_be is not None:
            L = _unframed_message_length(b, s)
            if L is not None and s + L <= n and n_be == L - 2:
                payload = b[s + 2 : s + L]
                p = _try_parse(t, payload)
                if p is not None and _is_plausible(p):
                    return ("wrong_endianness", t, _canonical(t, payload))

    # --- Fallbacks ---
    if saw_known_type and needs_more:
        return ("truncated", None, None)
    if saw_known_type:
        return ("unparseable", None, None)
    return ("unknown_type", None, None)


# --------------------------------------------------------------------------- statistics
def wilson(k: int, m: int, z: float = 1.96):
    """Wilson 95% CI for a proportion; returns (p, lo, hi)."""
    if m == 0:
        return (0.0, 0.0, 0.0)
    p = k / m
    denom = 1 + z * z / m
    centre = (p + z * z / (2 * m)) / denom
    half = (z * math.sqrt(p * (1 - p) / m + z * z / (4 * m * m))) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


# --------------------------------------------------------------------------- db access
def load_dsn(env_path: Optional[str]) -> str:
    """Build a psycopg conninfo from an env file (DB_* vars) or POSTGRES_URI/DATABASE_URL."""
    env = {}
    if env_path and os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
    for key in ("POSTGRES_URI", "DATABASE_URL", "POSTGRES_URL"):
        if env.get(key):
            return env[key]
        if os.environ.get(key):
            return os.environ[key]
    host = env.get("DB_HOST") or os.environ.get("DB_HOST", "localhost")
    port = env.get("DB_PORT") or os.environ.get("DB_PORT", "5432")
    name = env.get("DB_NAME") or os.environ.get("DB_NAME", "lnhistory")
    user = env.get("DB_USER") or os.environ.get("DB_USER", "ai_reader")
    pw = env.get("DB_PASSWORD") or os.environ.get("DB_PASSWORD", "")
    return f"host={host} port={port} dbname={name} user={user} password={pw}"


def sample_table(conn, table: str, k: int, rng: random.Random):
    """Return dict rows (internal_id, gossip_id, raw_gossip, first_seen_at) drawn
    uniformly over the table's rows.

    Sampling key is gossip_id (the PK on all four in-scope tables) — a sha256 hex,
    uniformly distributed, so a random 64-hex string + nearest row >= it is a uniform
    draw, index-supported everywhere. (internal_id is NOT independently indexed on
    node_announcements_complete, so keyset-on-internal_id would seq-scan there.)
    Era comes from the gossip_inventory PK join on gossip_id (always present via FK).
    """
    hexchars = "0123456789abcdef"
    rids = ["".join(rng.choice(hexchars) for _ in range(64)) for _ in range(k)]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT x.internal_id, x.gossip_id, x.raw_gossip, gi.first_seen_at
            FROM unnest(%s::text[]) AS r(rid)
            CROSS JOIN LATERAL (
                SELECT internal_id, gossip_id, raw_gossip
                FROM "{table}"
                WHERE gossip_id >= r.rid
                ORDER BY gossip_id
                LIMIT 1
            ) x
            LEFT JOIN gossip_inventory gi ON gi.gossip_id = x.gossip_id
            """,
            (rids,),
        )
        seen = {}
        for iid, gid, raw, fs in cur.fetchall():
            if gid in seen:
                continue
            seen[gid] = {
                "internal_id": iid,
                "gossip_id": gid,
                "raw": bytes(raw) if raw is not None else b"",
                "first_seen_at": fs,
            }
        return list(seen.values())


def twins_present(conn, gids: list[str]) -> set:
    if not gids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gossip_id FROM gossip_inventory WHERE gossip_id = ANY(%s)", (gids,)
        )
        return {row[0] for row in cur.fetchall()}


def real_obs_map(conn, iids: list[int]) -> dict:
    """internal_id -> True if any surviving observation is from a real collector,
    False if only synthetic observers survive. Absent key = no surviving observation."""
    if not iids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.internal_id,
                   bool_or(c.alias = ANY(%s)) AS has_real
            FROM gossip_observations o
            JOIN collectors c ON c.internal_collector_id = o.internal_collector_id
            WHERE o.internal_id = ANY(%s)
            GROUP BY o.internal_id
            """,
            (list(REAL_COLLECTOR_ALIASES), iids),
        )
        return {iid: bool(has_real) for iid, has_real in cur.fetchall()}


# --------------------------------------------------------------------------- audit
def audit_table(conn, table: str, msg_type: int, k: int, rng: random.Random):
    rows = sample_table(conn, table, k, rng)
    m = len(rows)
    result = {
        "table": table,
        "expected_type": msg_type,
        "sampled": m,
        "classes": Counter(),
        "hash_ok": 0,
        "hash_checked": 0,
        "drifted": 0,
        "twin_collisions": 0,
        "drifted_recoverable": 0,
        "source": Counter(),          # among DRIFTED: real_obs / synthetic_no_obs / synthetic_obs
        "era_drift": defaultdict(lambda: [0, 0]),  # year -> [drifted, total]
        "examples": {},               # class -> first ~16 hex bytes
    }
    if m == 0:
        return result

    drifted_new_gids = []
    drifted_iids = []
    drifted_rows = []

    for r in rows:
        b = r["raw"]
        klass, rtype, canon = classify(b)
        result["classes"][klass] += 1
        result["examples"].setdefault(klass, b[:16].hex())

        # hash-integrity signal (separate from conformance)
        if r["gossip_id"]:
            result["hash_checked"] += 1
            if hashlib.sha256(b).hexdigest() == r["gossip_id"]:
                result["hash_ok"] += 1

        year = r["first_seen_at"].year if r["first_seen_at"] else 0
        is_drifted = klass != "conformant"
        result["era_drift"][year][1] += 1
        if is_drifted:
            result["era_drift"][year][0] += 1
            result["drifted"] += 1
            if canon is not None:
                result["drifted_recoverable"] += 1
                new_gid = hashlib.sha256(canon).hexdigest()
                if new_gid != r["gossip_id"]:
                    drifted_new_gids.append(new_gid)
                drifted_rows.append((r, new_gid))
            drifted_iids.append(r["internal_id"])

    # twin collisions (recomputed gossip_id already exists as a distinct row)
    present = twins_present(conn, list(set(drifted_new_gids)))
    for r, new_gid in drifted_rows:
        if new_gid != r["gossip_id"] and new_gid in present:
            result["twin_collisions"] += 1

    # source attribution among drifted rows (inverted: purged synthetic observations)
    resolvable = [i for i in drifted_iids if i is not None]
    obs = real_obs_map(conn, list(set(resolvable)))
    for iid in drifted_iids:
        if iid is None:
            result["source"]["no_internal_id"] += 1     # can't attribute (old writer gap)
        elif iid not in obs:
            result["source"]["synthetic_no_obs"] += 1   # bulk-import origin
        elif obs[iid]:
            result["source"]["real_collector"] += 1
        else:
            result["source"]["synthetic_obs_residual"] += 1
    return result


def render(results, total_rows):
    lines = []
    lines.append("=" * 78)
    lines.append("raw_gossip framing — Phase 1a blast-radius ESTIMATE (sample-based)")
    lines.append("canonical = varint_le(len(payload)) ++ uint16_be(type) ++ payload")
    lines.append("=" * 78)
    for res in results:
        t = res["table"]
        m = res["sampled"]
        d = res["drifted"]
        p, lo, hi = wilson(d, m)
        approx_rows = total_rows.get(t)
        est = f"  (~{int(p*approx_rows):,} of {approx_rows:,} rows)" if approx_rows else ""
        lines.append("")
        lines.append(f"### {t}  (type {res['expected_type']})")
        lines.append(f"  sampled: {m:,}")
        lines.append(f"  DRIFTED: {d:,} = {p*100:.2f}%  (95% CI {lo*100:.2f}–{hi*100:.2f}%){est}")
        if res["hash_checked"]:
            hp = res["hash_ok"] / res["hash_checked"]
            lines.append(f"  hash-integrity (gossip_id == sha256): {hp*100:.2f}% ok "
                         f"[separate signal, NOT conformance]")
        lines.append("  class mix:")
        for cls, cnt in res["classes"].most_common():
            ex = res["examples"].get(cls, "")
            lines.append(f"    {cls:<20} {cnt:>7,}  {cnt/m*100:5.2f}%   e.g. {ex}")
        if d:
            tc = res["twin_collisions"]
            tp, tlo, thi = wilson(tc, d)
            lines.append(f"  twin collisions (repair = MERGE): {tc:,}/{d:,} drifted = "
                         f"{tp*100:.2f}% (95% CI {tlo*100:.2f}–{thi*100:.2f}%)")
            lines.append(f"    -> unique (in-place rewrite): {d - tc:,}")
            lines.append("  source of drifted rows (inverted attribution):")
            for src, cnt in res["source"].most_common():
                lines.append(f"    {src:<24} {cnt:>7,}  {cnt/d*100:5.2f}%")
            lines.append("  drift by ingest era (first_seen year):")
            for year in sorted(res["era_drift"]):
                dd, tt = res["era_drift"][year]
                yl = str(year) if year else "unknown"
                lines.append(f"    {yl:<8} {dd:>6,}/{tt:<6,} = {dd/tt*100:5.2f}% drifted")
    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default="/home/bitcoin/ln-history-research/analysis/.env",
                    help="env file with DB_* vars or POSTGRES_URI (ai_reader role)")
    ap.add_argument("--sample-size", type=int, default=20000, help="random draws per table")
    ap.add_argument("--tables", nargs="*", default=list(TABLES), choices=list(TABLES))
    ap.add_argument("--seed", type=int, default=1234567)
    ap.add_argument("--out-dir", default="./raw_gossip_audit")
    args = ap.parse_args()

    import psycopg  # lazy: keeps the classifier importable without a DB driver

    rng = random.Random(args.seed)
    os.makedirs(args.out_dir, exist_ok=True)
    dsn = load_dsn(args.env)

    results = []
    total_rows = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET statement_timeout = '600s'")
        for table in args.tables:
            print(f"[{datetime.now(timezone.utc):%H:%M:%S}] sampling {table} "
                  f"(n={args.sample_size}) ...", file=sys.stderr)
            with conn.cursor() as cur:
                cur.execute(f'SELECT reltuples::bigint FROM pg_class WHERE relname = %s', (table,))
                row = cur.fetchone()
                total_rows[table] = int(row[0]) if row and row[0] and row[0] > 0 else None
            res = audit_table(conn, table, TABLES[table], args.sample_size, rng)
            results.append(res)

    report = render(results, total_rows)
    print(report)

    # machine-readable deliverables
    json_out = os.path.join(args.out_dir, "phase1a_summary.json")
    with open(json_out, "w") as f:
        json.dump(
            [
                {
                    "table": r["table"],
                    "expected_type": r["expected_type"],
                    "approx_total_rows": total_rows.get(r["table"]),
                    "sampled": r["sampled"],
                    "drifted": r["drifted"],
                    "drifted_pct": (r["drifted"] / r["sampled"]) if r["sampled"] else None,
                    "drifted_ci95": wilson(r["drifted"], r["sampled"])[1:],
                    "twin_collisions": r["twin_collisions"],
                    "drifted_recoverable": r["drifted_recoverable"],
                    "hash_ok": r["hash_ok"],
                    "hash_checked": r["hash_checked"],
                    "classes": dict(r["classes"]),
                    "source": dict(r["source"]),
                    "era_drift": {str(y): v for y, v in r["era_drift"].items()},
                    "examples_hex16": r["examples"],
                }
                for r in results
            ],
            f,
            indent=2,
        )

    csv_out = os.path.join(args.out_dir, "phase1a_classes.csv")
    with open(csv_out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table", "class", "count", "sampled", "pct", "example_hex16"])
        for r in results:
            for cls, cnt in r["classes"].most_common():
                w.writerow([r["table"], cls, cnt, r["sampled"],
                            f"{cnt/r['sampled']*100:.4f}", r["examples"].get(cls, "")])

    with open(os.path.join(args.out_dir, "phase1a_report.txt"), "w") as f:
        f.write(report + "\n")
    print(f"\nwrote {json_out}\n      {csv_out}\n      {args.out_dir}/phase1a_report.txt",
          file=sys.stderr)


if __name__ == "__main__":
    main()
