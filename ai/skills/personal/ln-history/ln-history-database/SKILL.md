---
name: ln-history-database
description: >
  Blueprint of the lnhistory PostgreSQL database: tables, columns, types, constraints,
  foreign keys, indexes, enums, and ingestion pipeline. Use when writing or reviewing
  SQL queries against lnhistory, reasoning about schema design, or understanding how
  Lightning gossip messages are stored.
---

# ln-history-database blueprint

The `lnhistory` database stores Bitcoin Lightning Network gossip messages collected
by one or more collector nodes.

> **Row counts and sizes last verified 2026-08-25.** They drift fast — `channel_updates`
> alone doubled between the original notes and that check, and several figures in this
> file were wrong by 3.6×, 84×, or (for the "empty" staging tables) infinitely.
> **Before relying on any count or size below, re-read it from the catalog** — this is
> free and takes a second:
>
> ```sql
> SELECT relname, reltuples::bigint, pg_size_pretty(pg_total_relation_size(oid))
> FROM pg_class WHERE relkind='r' AND relnamespace='public'::regnamespace
> ORDER BY pg_total_relation_size(oid) DESC;
> -- and for column selectivity, never count(*):
> SELECT attname, null_frac, most_common_vals, most_common_freqs
> FROM pg_stats WHERE tablename='<t>' AND attname='<c>';
> ```
>
> Note `btc_usd_ohlc` (151,391 rows, 21 MB) also lives in `public` — it is
> price-collector data, not gossip, and is not documented in this file. The tool for querying it is
`~/.config/ai/tools/ln_db_query.py` — it requires the project venv:

```
/home/bitcoin/ln-history-research/analysis/.venv/bin/python3 \
  /home/bitcoin/.config/ai/tools/ln_db_query.py "SELECT ..." [--json]
```

Credentials are read from `/home/bitcoin/ln-history-research/analysis/.env` — this is
the **read-only `ai_reader`** role (SELECT only).

Any write (UPDATE, DELETE, CREATE INDEX, ALTER TABLE, CLUSTER) needs the admin role
in `/home/bitcoin/ln-history-research/analysis/.env.migration` (vars are `DB_HOST`,
`DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`). `psql` is not installed on the host;
reach it through the container:

```
set -a && . ./.env.migration && set +a && docker exec -e PGPASSWORD="$DB_PASSWORD" \
  ln-history-database psql -U "$DB_USER" -d "$DB_NAME" -c "..."
```

Full-access credentials also live in `/home/bitcoin/ln-history-research/.env`
(`POSTGRES_USER=admin`, `POSTGRES_PASSWORD`, `POSTGRES_URI`).

### ⚠️ Rename-swap migrations silently drop grants

Grants are attached to the table's OID, not its name. The
`CREATE TABLE new` → `DROP/rename old` → `rename new` pattern used throughout this
database therefore produces a table that **no non-owner role can read**, and nothing
warns you — the failure surfaces later as `permission denied for table X` from
`ai_reader`, Grafana, or the API.

This happened to `node_addresses` on 2026-07-21: the new table came up with
`{admin, grafanareader}` while the retained `node_addresses_old` still had
`{admin, grafanareader, ai_reader}`. Read-only access was broken for a day.

**After every rename-swap, re-apply grants and diff them against the old table:**

```sql
SELECT relname, relacl FROM pg_class WHERE relname IN ('X', 'X_old');
GRANT SELECT ON X TO ai_reader, grafanareader;   -- plus any other reader role
```

`yannik` (added 2026-08-13) is **exempt** — it has
`ALTER DEFAULT PRIVILEGES FOR ROLE admin IN SCHEMA public GRANT SELECT ON TABLES`,
so any table admin creates is readable by it from birth, rename-swap included.
Consider doing the same for `ai_reader` / `grafanareader` to retire this hazard.

## Roles

| role | login | privileges |
|---|---|---|
| `admin` | yes | superuser — `.env.migration`, `/home/bitcoin/ln-history-research/.env` |
| `ai_reader` | yes | SELECT on public (explicit per-table grants) — `analysis/.env` |
| `grafanareader` | yes | SELECT on public (explicit per-table grants) |
| `yannik` | yes | SELECT on all of public + default privileges, owns schema `yannik` for scratch tables/matviews, `pg_read_all_stats`, TEMP; conn limit 10; `statement_timeout=60min`, `idle_in_transaction_session_timeout=15min` (both session-overridable) |

No reader role has USAGE on the `api` schema (`api_keys`, `usage`) — keep it that way.

The same applies to constraint/index names: they follow the OID too, so a swapped-in
table keeps its `*_new_*` names until explicitly renamed (which can only happen after
the old table is dropped, since the canonical names are still occupied by it).

---

## ⚠️ `random_page_cost` is set in three places — command line wins

**Changed 2026-08-25: effective value is now `1.1`** (was `4`, the spinning-disk default,
on an NVMe box that already had `effective_io_concurrency = 200`). The stale value was
suppressing index use database-wide.

The value is pinned in **three** places, and they disagree. Precedence, highest first:

| set where | value | how to change | takes effect |
|---|---|---|---|
| `ALTER DATABASE lnhistory SET` | **1.1** ← *effective* | `ALTER DATABASE lnhistory SET random_page_cost = x;` | new sessions, no restart |
| container command line (`docker-compose.yaml:294`, `-c random_page_cost=4`) | 4 | edit compose, recreate container | needs a DB restart |
| `postgresql.auto.conf` (`ALTER SYSTEM`) | 1.1 | `ALTER SYSTEM SET …; SELECT pg_reload_conf();` | only if the two above are cleared |

**`ALTER SYSTEM` alone does nothing here** — the command-line option overrides it. The
database-level setting is the only lever that works without recreating the container.
If you ever `ALTER DATABASE … RESET random_page_cost`, the value silently reverts to 4
from the command line. **The compose file should be corrected at the next planned
restart** so all three agree; until then, leave the database-level setting in place.

Measured impact on the state-change extraction over `channel_updates` (see
`idx_cu_topology_events` below) — each step was necessary, the index alone did nothing:

| stage | ordered-extraction cost | plan |
|---|---:|---|
| baseline | 27,879,717 | Seq Scan 117 GB + 71M-row external sort |
| + partial covering index | 27,879,717 | **unchanged — planner ignored it** |
| + `random_page_cost` 1.1 | 10,286,135 | Index Only Scan, sort eliminated |
| + `VACUUM` (VM 86.1% → 100%) | **1,547,572** | Index Only Scan, no heap fetches |

Single-channel event history over the same path went from a seq scan to **cost 53**.

---

## ⚠️ `channel_closures` records the FUNDING tx for 23,551 channels (4.6%)

**Found and verified against Bitcoin Core 2026-08-25.** For 23,551 closed channels,
`closing_txid` / `closing_height` / `closing_timestamp` describe the channel's **funding**
transaction, not its close.

Proof: for each sampled row, the tx at `closing_txid`, at the scid's output index, has a
`witness_v0_scripthash` output whose value equals `channels.capacity_sat` **exactly** —
that is the 2-of-2 funding output. The channels *are* genuinely closed (the funding output
is spent on-chain); only the recorded transaction is wrong.

**Detector — exact, cheap, and far better than any timestamp heuristic:**

```sql
SELECT count(*) FROM channels c JOIN channel_closures cl ON cl.gossip_id = c.gossip_id
WHERE cl.closing_height = (c.scid >> 40);     -- closes in its own funding block
```

**Consequences:**
- `closing_timestamp = funding_timestamp` for 23,548 of them, so the channel has **zero
  lifetime and is invisible to every point-in-time query** — `funding_timestamp <= T AND
  closing_timestamp > T` can never be true. Any snapshot, graph build or open-channel
  count silently omits 4.6% of closed channels.
- The closure economics are computed from the wrong transaction and are garbage:
  `mining_fee_sat = 0` in **23,550 of 23,551** rows (against 38 of 441,809 for correct
  closures), and `settled_balance_sat` averages 108.5M sat versus 13.1M — 8× inflated
  because it is reading funding outputs.

`mining_fee_sat = 0` is therefore a second, independent signature of the same bug.

#### FIXED and backfilled 2026-08-26

Writer fixed in **chain-enricher 0.6.1**: the close is now the *earliest confirmed*
scripthash-history entry above the funding height, never `history[-1]`. Unconfirmed
spends are skipped rather than processed. Backfill repaired 23,567 rows in 32 min via
batched Fulcrum calls.

| check | before | after |
|---|---:|---:|
| `closing_height = scid >> 40` | 23,551 | **6** |
| zero-lifetime (`closing_timestamp <= funding_timestamp`) | 23,548 | **3** |
| `mining_fee_sat = 0` | 23,550 | **9** |
| mean / median `mining_fee_sat` | 0 / 0 | **440 / 236** |

The residual 6 are channels whose spend was still unconfirmed — the fixed worker picks
them up once it confirms. Backup: `repair_20260826_closures` (23,573 rows, includes
`scid` and both timestamps; restore from it to roll back).

**Closure type was wrong for 59% of them** — 12,546 force→mutual and 1,380 mutual→force
on reclassification, because the old value was derived from the funding tx's input
sequence. Do not trust `channel_closures.type` on any row predating this repair.

> **⚠️ Height matching alone is not sufficient — match the funding OUTPOINT.**
> The Electrum scripthash identifies a *script*, not an outpoint, so a scripthash can
> carry activity unrelated to this channel's funding output. The height-based backfill
> mis-selected **36 of 23,573 rows (0.15%)**, caught by flagging
> `mining_fee_sat > capacity_sat * 0.05` and re-checked against the chain. 35 were
> re-selected by requiring an input spending `(funding_txid, scid & 0xFFFF)`; the last
> one (`scid 761140222877237249`) had an **unspent funding output** — it is still open
> and had been wrongly closed by the *original* enricher, so its closure row was deleted
> and `closing_timestamp` reset to NULL.
>
> A verification sample of 300 repaired rows confirmed 300/300 spend the correct funding
> outpoint, and 400/400 sampled channels are genuinely closed on-chain.
>
> **The live worker now does this check too, as of chain-enricher 0.6.2** — it resolves the
> funding txid from the history entry at the funding height and accepts only a candidate
> spending `(funding_txid, scid & 0xFFFF)`. Costs no extra RPC: the candidate tx was already
> being fetched to compute financials, so that fetch was moved up and reused. Until 0.6.2
> the worker would silently *re-close* a wrongly-closed channel after the row was deleted —
> observed on `scid 761140222877237249`, which is genuinely open.

#### The "7.1% bad fees" claim was wrong — retracted 2026-08-26

An earlier version of this section reported 31,293 of 442,062 closure rows (7.1%) as
having implausible fees, based on `mining_fee_sat > capacity_sat * 0.05`. **That was a
threshold artifact, not a defect.** The ratio simply tracks channel size:

| capacity | closures | flagged >5% | median fee |
|---|---:|---:|---:|
| < 100k sat | 31,327 | 57.1% | 2,810 |
| 100k–500k | 61,510 | 20.3% | 2,811 |
| 500k–2M | 100,192 | 0.9% | 2,061 |
| ≥ 2M | 249,125 | **0.0%** | 1,422 |

The *absolute* median fee is a normal 1.4–2.8k sat in every band — small channels just
have a small denominator. Verified on-chain over 200 sampled flagged rows: **200/200**
had the correct closing tx, and `mining_fee_sat` equalled
`funding_output_value − sum(vout)` exactly. `capacity_sat` matched the real funding output
value in every case. **The fee formula is correct.**

**Use an absolute threshold to find real corruption, not a ratio.** `mining_fee_sat >
1_000_000` returned 33 rows, of which on-chain checking showed 19 genuine and **14
mis-selected** — all with fee ≈ capacity, the signature of a recorded tx that does not
spend the funding outpoint. Those 14 were repaired 2026-08-26 by outpoint matching
(backup: `repair_20260826b_closures`). Result: fees ≥ 0.1 BTC went 9 → **0**,
`mining_fee_sat >= capacity_sat` went 1 → **0**, max fee 999,685,878 → 7,425,158.

Current health checks worth keeping in monitoring:

```sql
-- must stay ~0 (residue = spends not yet confirmed)
SELECT count(*) FROM channels c JOIN channel_closures cl USING (gossip_id)
WHERE cl.closing_height = (c.scid >> 40);
-- must stay 0: a fee cannot equal or exceed the channel's capacity
SELECT count(*) FROM channel_closures cl JOIN channels c USING (gossip_id)
WHERE cl.mining_fee_sat >= c.capacity_sat;
```

### Separately: 3 channels whose `scid` points at the closing tx

A different defect, 3 rows (`1054528408251269120`, `1057089170841862145`,
`1059249711032631296`). Here `closing_txid` is a real close (output value does *not* match
capacity), but `scid >> 40` and the scid's tx-index match the **closing** transaction's
block and position. Tracing `vin[0]` finds the real funding tx 44–210 blocks earlier.
Because `announceable_timestamp` is derived from the scid, it is wrong for these too —
which is what the `announceable - funding` heuristic actually flags.

**Do not use `announceable_timestamp - funding_timestamp` as the corruption detector.**
Re-measured 2026-08-25 over all 508,258 channels: median 46:04, p01 12:35, p99 1:54:48,
and only **6** rows outside [0, 1 day] — 3 of which are false positives. Blocks 933233/933238
are a worked example: funding 15:44:10, announceable 15:28:07, delta −16:03, and **both
values match Core exactly**. Bitcoin only requires a block timestamp to exceed the median
of the previous 11, so the chain legitimately runs backwards; block 933234 (15:18:33) is
25 minutes *earlier* than 933233. The heuristic assumes a monotonicity Bitcoin does not
provide.

`announceable_timestamp` coverage is now **100%** (508,258 of 508,258, 0 missing), so the
"chain-enricher never backfills it" warning elsewhere in this file is out of date.

---

## `gossip_first_observed` — the real receipt time (built 2026-08-25)

`gossip_inventory.first_seen_at` stores the originator timestamp, not a receipt time, and
only ~25% of the archive was ever observed by our collectors — so it cannot be repaired in
place without leaving a column that silently mixes two meanings. This side table holds the
answer for the rows where one exists:

```sql
CREATE TABLE gossip_first_observed AS
  SELECT internal_id, min(seen_at) AS first_observed_at
  FROM gossip_observations GROUP BY internal_id;
```

**69,531,060 rows; 2937 MB heap + 1489 MB PK + 520 MB (first_observed_at) = 4947 MB.**
Range 2025-12-04 17:27:49 … 2026-08-25 20:06:21. Granted to `ai_reader`, `grafanareader`,
`yannik`. Built in ~3 min with `work_mem = 2GB`; no lock on `gossip_inventory`, no bloat.

**Presence is the discriminator**: a row here means we observed the message and this is
when; no row means bulk-imported with no receipt time. Join on `internal_id`.

Note the timestamp index is only **520 MB (7.5 bytes/row)** — btree deduplication, because
`seen_at` values repeat heavily. A naive estimate from an all-distinct 8-byte column
(`unique_internal_id`, 24.7 bytes/row) would have predicted ~1.6 GB.

**Kept fresh hourly** by `analysis/refresh_gossip_first_observed.sh` (cron `17 * * * *`,
added 2026-08-26; ~4 s for a day's backlog, log alongside the script). Note the column is
`first_seen_at` — the name now means what it says, in the table where it is true. To top
up by hand:

```sql
INSERT INTO gossip_first_observed
SELECT o.internal_id, min(o.seen_at) FROM gossip_observations o
WHERE o.internal_id > (SELECT max(internal_id) FROM gossip_first_observed)
GROUP BY o.internal_id
ON CONFLICT (internal_id) DO NOTHING;
```

---

## Hardware-Aware Query Protocol

The database runs on a VPS with **8 AMD EPYC vCores, 32 GB ECC RAM, and a 600 GB NVMe drive**.

### The Golden Rule

Before using `ln_db_query.py` for any complex query — aggregations, joins, or queries that touch large tables (`channel_updates` 144.5M rows / 159 GB, `gossip_inventory` 265M rows / 80 GB, `node_announcements_complete` 30.9M rows / 44 GB, `node_addresses` 81.2M rows / 19 GB, `gossip_observations` 126M rows / 13 GB, etc.) — you **MUST** first run the query through `ln_db_explain.py`.

Run the explain tool using the project venv:

```
/home/bitcoin/ln-history-research/analysis/.venv/bin/python3 \
  /home/bitcoin/.config/ai/tools/ln_db_explain.py "SELECT ..."
```

### Cost threshold

If `total_cost` returned by `ln_db_explain.py` **exceeds 1,000,000**, you must **stop** and warn the user before proceeding. The warning must include:

1. The estimated `total_cost` and `plan_rows` values.
2. A reminder of the hardware specs (8 vCores, 32 GB RAM, 600 GB NVMe).
3. An explicit request for the user's permission before running the query with `ln_db_query.py`.

Only run `ln_db_query.py` after the user grants explicit permission.

Queries with `total_cost` ≤ 1,000,000 may proceed directly to `ln_db_query.py` without prompting.

---

## Key identifiers

**`gossip_id`** — `varchar(64)`, hex-encoded SHA-256 of the raw gossip bytes stored
in the `raw_gossip` column. It is the primary key of every gossip-carrying table
(`gossip_inventory`, `channels`, `channel_updates`, `node_announcements`,
`node_announcements_complete`). Identical content always produces the same key.

**`raw_gossip`** — `bytea`. The stored bytes are a self-describing envelope:
`varint(length) ++ uint16(type) ++ payload_bytes`. The varint prefix is
little-endian encoded. Example header for a 430-byte channel_announcement:
`fd ae 01` (varint = 430) `01 00` (type = 256).

**`internal_id`** — `bigint`, auto-incremented from the shared sequence
`gossip_inventory_internal_id_seq`. Assigned at first ingest. Used in
`gossip_observations`, `channels`, `channel_updates`, `node_announcements*` and
`node_addresses` to join back to the inventory without repeating `gossip_id`.
**Prefer it over `gossip_id` for joins** — an 8-byte integer comparison against a
65-byte hex string. On most tables `gossip_id` is the PK and so is at least indexed;
on `node_addresses` it is **not** indexed at all. Measured 2026-07-21: the full
"addresses of a node's current announcement" query costs **10,664** via
`internal_id`, against ~1.96M for the `gossip_id` equivalent.

**`scid`** — `bigint`, short channel ID encoded as a single 64-bit integer
(standard LN encoding: block_height « 40 | tx_index « 16 | output_index).

**`node_id`** / **`collector_node_id`** / **`peer_node_id`** — `varchar(66)`,
hex-encoded 33-byte compressed public key.

**`direction`** — `bit(1)`. Channel updates are directional; `0` = node_1→node_2,
`1` = node_2→node_1 (per BOLT 7 channel flags bit 0).

---

## Gossip message types

The `type` column in `gossip_inventory` (and staging equivalents) is the BOLT 7
message type number:

| type | message              | approx rows (inventory) |
|------|----------------------|------------------------|
| 256  | channel_announcement | 409 K                  |
| 257  | node_announcement    | 27 M                   |
| 258  | channel_update       | 75 M                   |
| 4101 | core-lightning-internal: channel_amount       | 8.9 K                  |
| 4103 | core-lightning-internal: gossip_store_delete_chan       | 2.4 K                  |
| 4106 | gossip_store_chan_dying       | 2.4 K                  |

---

## Ingestion pipeline

```
gossip received by collector node
        │
        ▼
  gossip_inventory        ← one row per unique gossip_id
  (+ gossip_inventory_future for future-timestamped msgs)
        │
        ├──► gossip_observations  ← (internal_id, internal_collector_id) — who saw it, when
        │
        ├──► channels             ← type 256, one row per channel (scid)
        │       └──► channel_closures  ← on-chain close detected
        │
        ├──► channel_updates      ← type 258, temporal validity chain per (scid, direction)
        │
        └──► node_announcements_complete  ← type 257, ALL announcements (full history)
                └──► node_announcements  ← filtered/deduplicated subset for fast analytics
                        └──► node_addresses  ← addresses extracted from each announcement
```

**`gossip_inventory_future`**: quarantine for messages whose embedded timestamp
is in the future. Shares the same `internal_id` sequence as `gossip_inventory`
to keep IDs globally unique. ~1,900 rows; under investigation.

---

## Temporal validity pattern

`channel_updates` and `node_announcements` / `node_announcements_complete` all
carry `valid_from` / `valid_to` columns. This is a bitemporal chain:
- `valid_from` = the sender's timestamp from the message.
- `valid_to` = the `valid_from` of the next message that superseded this one
  (NULL = currently active).
- "Current state" query: `WHERE valid_to IS NULL`.
- "State at time T" query: `WHERE valid_from <= T AND (valid_to IS NULL OR valid_to > T)`.

---

## Tables — full blueprint

### `gossip_inventory`

Registry of every unique gossip message ever received.

| column        | type          | nullable | default                            |
|---------------|---------------|----------|------------------------------------|
| gossip_id     | varchar(64)   | NO       |                                    |
| type          | integer       | NO       |                                    |
| first_seen_at | timestamptz   | YES      | now() — **never used, see below**  |
| internal_id   | bigint        | NO       | nextval(gossip_inventory_internal_id_seq) |

### ✅ RENAMED 2026-08-26: `first_seen_at` → `sender_timestamp`

**Renamed 2026-08-26** (`gossip_inventory` and `gossip_inventory_future`), with
`gossip-processor` 0.10.4 deployed in the same window. The old name and its
`DEFAULT now()` implied a wall-clock "when we first saw this gossip". It never was. `gossip-processor` 0.10.2 always supplies a
value explicitly, so the `DEFAULT now()` never fires, and the value it supplies is the
timestamp *from inside the gossip message*:

```python
# main.py:537   — gossip_ts_int is the MESSAGE's timestamp
content = self.db.insert_content(cur, gossip_id, msg_type, raw_gossip_bytes,
                                 parsed_obj, gossip_ts_int)
# main.py:165-173
dt = datetime.fromtimestamp(timestamp_int, timezone.utc)
INSERT INTO gossip_inventory (gossip_id, type, first_seen_at) VALUES (%s, %s, %s)
```

The real receipt time (`receipt_dt`) is in scope at the call site and is passed
correctly to `insert_observation` — so **`gossip_observations.seen_at` is trustworthy
and `gossip_inventory.first_seen_at` is not.**

Measured over the 35,246 most recent inventory rows (`internal_id > 312700000`):

| check | result |
|---|---|
| rows whose `first_seen_at` has a sub-second component | **0 of 35,246** (message timestamps are whole unix seconds; `now()` would give microseconds) |
| `first_seen_at <> gossip_observations.seen_at` | **104,642 of 104,661** pairs (99.98%) |
| `seen_at - first_seen_at` | avg −323 s, range **−233 days … +155 days** |
| oldest `first_seen_at` among the newest 35K inserts | 2026-03-24 — five months before the row existed (re-broadcast old gossip) |

**Rules that follow:**
- For "when did we first observe this gossip", use `gossip_first_observed.first_seen_at`
  (or `min(gossip_observations.seen_at)`). Never `sender_timestamp`.
- Any propagation-lag measurement built on `first_seen_at` computes ≈0 by construction,
  because it is subtracting the sender timestamp from itself. The
  `announceable_timestamp` lag recipe under `channels` is safe — it correctly uses
  `gossip_observations.seen_at`.
- `max(first_seen_at)` is a **future** date (2027-04-15 as of 2026-08-25); see the
  timestamp-abuse note below. It is not clock skew on our side.
- The rename was chosen over "fix the writer and backfill", because only ~25% of the
  archive was ever observed by our collectors — a backfill would have left the column a
  silent mixture of two meanings. `gossip_first_observed` carries the receipt time for
  the rows where one exists; presence there is the discriminator.

### ⚠️ Future-dated node_announcements poison the active head

`gossip_inventory_future` exists to quarantine future-timestamped messages, but
**`gossip-processor` never writes to it** (0 references in `main.py`) — it is dead code
from an earlier pipeline, frozen at 1,942 rows. Nothing filters future timestamps today.

As of 2026-08-25, `node_announcements_complete` holds **1,102 rows with
`valid_from` in the future** (2026-11-24 … 2030-04-02), from just **4 node_ids** — two
of which account for 1,100 in two short bursts (969 announcements stamped 2027-04-15,
131 stamped 2026-11-24). `channel_updates` and `channels` are clean (0 future rows), so
channel geometry is unaffected.

The harm is the SCD chain: for all four nodes the future-dated row is the **active head**
(`valid_to IS NULL`), so `WHERE valid_to IS NULL` returns it instead of the real current
announcement. For `03a6680e…` it masks 24 legitimate announcements behind a row claiming
2030-04-02. This is the BOLT 7 timestamp-abuse vector — a far-future timestamp
permanently wins the "newest announcement" race, because every honest later announcement
carries a lower timestamp and is rejected as stale.

#### Repaired 2026-08-25 — and the blast radius was wider than the one table

The poison had spread to four tables, not one. All of it was backed up before deletion,
in a single transaction:

| table | rows removed | backup |
|---|---:|---|
| `node_announcements_complete` | 1,102 | `repair_20260825_nac` |
| `node_announcements` (filtered) | 7 | `repair_20260825_na` |
| `node_addresses` | 1,187 | `repair_20260825_addr` |
| `nodes` (first_seen/last_seen rebuilt, not deleted) | 4 | `repair_20260825_nodes` |

`nodes` had been corrupted too: all four had a **future `last_seen`**, and `026e6215…`
had `first_seen` *later than* `last_seen`. That matters because the API's "currently
active" test is `last_seen >= now() - 14 days`, so these four were permanently active.
Both columns were rebuilt as min/max over surviving announcements and channels.

After the deletes the SCD head was restored: `03a6680e…` has its 24 real announcements
back with the 2020-10-10 row as active head. The other three nodes now have **zero**
announcements — honest, since every announcement they ever sent carried an impossible
timestamp. They still exist in `nodes` via their channels (14, 11 and 29 respectively).

Verified after: 0 future rows beyond 1 day in either announcement table, 0 orphaned
address rows, every affected node `first_seen <= last_seen <= now()`, at most one active
head each. **To roll back, re-insert from the four `repair_20260825_*` tables.**

**Deliberately left alone: 85 rows in the 1 h–1 d future band, from 2 nodes ~4 h fast.**
A persistently fast clock still supersedes its own announcements correctly — the chain is
internally consistent, just shifted — so these are recoverable data, not poison.

#### Ingest clamp — DEPLOYED in gossip-processor 0.10.3 (2026-08-25)

`MAX_FUTURE_SKEW_SECONDS` (default **86400**, `0` disables) rejects type 257/258 whose
originator timestamp is more than a day ahead, increments
`gossip_future_timestamp_rejected_total{type,source}` and logs the node and skew.
Type 256 carries no originator timestamp and is untouched — exactly why `channels` was
never poisoned.

24 h rather than 1 h is deliberate: a persistently fast clock still supersedes its own
announcements correctly, so its chain is internally consistent and worth keeping. Only
the un-supersedable case (months or years out) is blocked. Lower it to 3600 to also
exclude the two ~4 h-skewed nodes.

Released as `ghcr.io/ln-history/gossip-processor:0.10.3` (git tag `0.10.3`, commit
`447d186`), compose pinned at `docker-compose.yaml:198`. Post-deploy check: all three ZMQ
sources reconnected, lag 0.02–0.60 s, 0 errors, sequence advancing normally.

**Monitor with** `increase(gossip_future_timestamp_rejected_total[1d])` — a non-zero
value means someone is still broadcasting impossible timestamps and the clamp is earning
its keep. The counter has no samples until the first rejection, which is normal.

Constraints: PK(gossip_id), UNIQUE(internal_id).

Indexes — **verified 2026-08-25, only 3 exist** (`gossip_inventory_first_seen_at` is
listed in older versions of this file but **does not exist**; any predicate on
`first_seen_at` is a 31 GB seq scan, cost ~5.4M):

| index | key | size | idx_scan |
|---|---|---:|---:|
| `gossip_inventory_pkey` | (gossip_id) | 40 GB | 35,341,360 |
| `unique_internal_id` | (internal_id) | 6250 MB | 2 |
| `gossip_inventory_type` | (type) | 3322 MB | 0 |

**265,106,928 rows; 31 GB heap + 50 GB indexes = 80 GB.**

---

### `gossip_inventory_future`

Same schema as `gossip_inventory`. Holds messages with a future sender timestamp.
Shares the `gossip_inventory_internal_id_seq` sequence so `internal_id` values
are globally unique across both tables. ~1,900 rows.

Indexes: same pattern — pkey(gossip_id), unique(internal_id), type, first_seen_at DESC.

---

### `gossip_observations`

Records which collector observed which gossip message, and when.

**Rebuilt 2026-07-19** onto surrogate keys. `gossip_id` and `collector_node_id` are
GONE from this table — writing to either is an error.

| column                | type        | nullable |
|-----------------------|-------------|----------|
| internal_id           | bigint      | NO       |
| seen_at               | timestamptz | YES      |
| sender_timestamp      | timestamptz | YES      |
| internal_collector_id | smallint    | NO       |

Constraints: PK(internal_id, internal_collector_id),
FK internal_collector_id → collectors(internal_collector_id).

Join back to content via `internal_id` → `gossip_inventory.internal_id`
(or `channel_updates` / `node_announcements*.internal_id`), and to the collector
via `internal_collector_id` → `collectors`.

`seen_at` = wall-clock time the collector received the message.
`sender_timestamp` = timestamp embedded in the gossip message by its originator.

**126,046,456 rows; 13 GB total** (6854 MB heap + 6138 MB indexes) as of 2026-08-25.
The 2026-07-19 rebuild took it to 95,503,734 rows / 7797 MB (from 18 GB heap + 38 GB
indexes = 56 GB — an 86% reduction); it has since grown by 30.5M rows through normal
ingest. ~~One index only: the PK.~~ **Corrected 2026-09-03: there are three.**
`gossip_observations_seen_at` (btree on `seen_at`) and
`gossip_observations_internal_collector_id` were added after the rebuild. This
matters: a time-windowed observation count is now cheap — `SELECT
internal_collector_id, count(*) ... WHERE seen_at > now() - interval '5 minutes'
GROUP BY 1` costs **691**, not the ~2M a seq scan would. That is what makes live
per-collector gossip panels affordable.

### Migration record (2026-07-19)

Old shape was `PK(gossip_id varchar(64), collector_node_id varchar(66))` — those two
65/67-byte hex strings were ~75% of every 175.7-byte tuple AND of the 26 GB PK,
while `internal_id` already existed as an 8-byte surrogate for `gossip_id`.

Done as `CREATE TABLE` + bulk `INSERT` + rename-swap, NOT `ALTER`/`UPDATE` — an
in-place update of 95.5M rows would have hit the index write-amplification that made
the `channel_updates` repair take 643 minutes. **The whole rebuild took 5.5 minutes.**
Script: `analysis/migrate_observations_slim.sh`.

The 20,547,521 rows with `internal_id IS NULL` were resolved *inline during the copy*
by hash-joining `gossip_inventory` (`SET enable_nestloop=off` — a nested loop would
be 20.5M random probes at ~4 MB/s and never finish), rather than as a separate
backfill UPDATE. Reconciliation: 95,503,734 rows both sides, **0 dropped orphans**;
`gossip_inventory_future` contributed 0 rows.

Dropped in the rebuild (all had ~0 use): `idx_obs_collector_time` (9598 MB,
31 scans), `idx_obs_internal_id` (1896 MB, 0 scans),
`gossip_observations_sender_timestamp` (828 MB, 0 scans). Re-add only on evidence.

`gossip_observations_old` retains the pre-migration table until dropped.

#### Content policy (decided 2026-07-19)

This table holds ONLY observations from the four own-platform collectors:
`alice`, `alice-new`, `bob`, `bob-new`. Bulk-import rows do not belong here.
9,954,765 artifact rows were deleted on 2026-07-19 (`0200…0000` "Gossip File
Import", `0300…0000` "Minibolt Old Bulk import", `0200…bluematt` — the last not
even present in `collectors`, where bluematt is `0400…0000`).

**Identify artifact rows by which collector they belong to, resolved against the
`collectors` table** — that is the only sound test. The 2026-07-19 deletion was done
on that basis, using `collector_node_id`; since the rebuild dropped that column, the
equivalent test today is `internal_collector_id` joined to `collectors`.

**CORRECTION (2026-07-19):** an earlier version of this file claimed
"real collectors always populate `sender_timestamp`, artifact rows never do."
**That is false.** It was inferred from a sample of each collector's OLDEST rows.
The newest row for *every* one of the four real collectors also has
`sender_timestamp IS NULL` — the current `gossip-processor` never writes the
column at all (see the writer-bugs note below). Do not use `sender_timestamp`
to classify rows.

The `seen_at` shape is still suggestive but not conclusive: artifact imports show
millions of rows inside a minutes-long window (e.g. 9.38M rows in 3 minutes),
because `seen_at` was set to the import wall-clock.

Consequence: `analysis/observations.csv` must never be imported (deleted 2026-07-19).

**Enumerating distinct collectors:** just read `collectors` — it is the authoritative
list (**8 rows** as of 2026-08-25; `charlie-new`, internal_collector_id 9, was added
2026-08-01 and older versions of this file said 7), and
`gossip_observations.internal_collector_id` is an FK to it. Do not aggregate over the
126M-row observations table for this.

### Observation coverage is sparse and blocky — only ~25% of gossip_inventory

**Mapped 2026-08-25.** `gossip_observations` does not cover the archive evenly. Probing
20k-wide `internal_id` windows every 8M shows four covered bands separated by a very
large empty middle:

| internal_id band | observed? | note |
|---|---|---|
| ~0 – 30M | yes, ~100% of ids | 2 collectors (alice, bob) |
| ~32M – 38M | no | |
| ~40M – 55M | yes | |
| ~56M – 100M | no | |
| ~104M – 118M | yes, ~1 obs/id | |
| **~120M – 278M** | **no** | the bulk imports — 158M ids, zero observations |
| ~280M – 313M+ | yes, ~35% of ids, 2.87 obs each | alice-new, bob-new, charlie-new |

Estimated **~60–70M of 265M rows (~25%)** have any observation. The rest were bulk-imported
and their observation rows were deleted in the 2026-07-19 content-policy cleanup, so
**there is no receipt time for them and none can be reconstructed.**

Consequence for anyone tempted to "fix" `first_seen_at` in place: an UPDATE from
`min(gossip_observations.seen_at)` can only touch that ~25%, leaving the column a silent
mixture of real receipt times and originator timestamps with **no way to tell which is
which**. That is worse than uniformly wrong. Prefer a side table keyed on `internal_id`,
where presence *is* the discriminator. See [[project-first-seen-at-semantics]].

**Do not trust `pg_stats.n_distinct` on these big tables.** For
`gossip_observations.internal_id` it reports 7.67M, implying 16.8 observations per
message; direct measurement on a bounded window gives **2.87**. The sampled n_distinct
estimator underestimates badly at this scale — measure on an `internal_id` window instead.

> **Superseded:** earlier versions of this file recommended a recursive skip scan over
> `idx_obs_collector_time` on `gossip_observations.collector_node_id`. **Both the
> column and that index were removed in the 2026-07-19 rebuild** — that query now
> errors. Kept here only so the old advice is recognisably dead.

**The skip-scan trick itself is still useful**, just not on that column: to enumerate
the distinct values of a low-cardinality indexed column without scanning the table,
recurse on `> previous` with `LIMIT 1`. This is how the 5 distinct `type_id` values in
the 75.9M-row `node_addresses` were confirmed instantly before validating its FK:

```sql
WITH RECURSIVE t AS (
  (SELECT type_id AS v FROM node_addresses ORDER BY type_id LIMIT 1)
  UNION ALL
  SELECT (SELECT n.type_id FROM node_addresses n
          WHERE n.type_id > t.v ORDER BY n.type_id LIMIT 1)
  FROM t WHERE t.v IS NOT NULL)
SELECT v FROM t WHERE v IS NOT NULL;
```

(Note it skips NULLs — `> t.v` excludes them — which is usually what you want for an
FK pre-check, since NULLs never violate a `MATCH SIMPLE` foreign key.)

---

### `channels`

One row per `channel_announcement` (type 256). The channel lives here until closed.

| column              | type        | nullable | notes                         |
|---------------------|-------------|----------|-------------------------------|
| gossip_id           | varchar(64) | NO       | PK                            |
| scid                | bigint      | YES      | UNIQUE — LN short channel ID  |
| funding_timestamp   | timestamptz | YES      | block timestamp of funding tx |
| closing_timestamp   | timestamptz | YES      | NULL = open channel           |
| capacity_sat        | bigint      | YES      |                               |
| source_node_id      | varchar(66) | YES      | FK → nodes(node_id)           |
| target_node_id      | varchar(66) | YES      | FK → nodes(node_id)           |
| node_signature_1    | varchar(144)| YES      | hex DER signature             |
| node_signature_2    | varchar(144)| YES      |                               |
| bitcoin_signature_1 | varchar(144)| YES      |                               |
| bitcoin_signature_2 | varchar(144)| YES      |                               |
| features            | bytea       | YES      |                               |
| chain_hash          | varchar(64) | YES      |                               |
| bitcoin_key_1       | varchar(66) | YES      |                               |
| bitcoin_key_2       | varchar(66) | YES      |                               |
| raw_gossip          | bytea       | YES      | full envelope (see above)     |
| internal_id         | bigint      | YES      | → gossip_inventory.internal_id |
| announceable_timestamp | timestamptz | YES   | block-header ts of block (funding_height+5); 6-conf announceable point. Added+backfilled 2026-07-31 — see note below |

Constraints: PK(gossip_id), UNIQUE(scid),
FK gossip_id → gossip_inventory(gossip_id) ON DELETE CASCADE NOT VALID,
FK source_node_id → nodes(node_id),
FK target_node_id → nodes(node_id).

Indexes: PK, UNIQUE(scid), (internal_id),
`idx_active_channels_nodes` (source_node_id, target_node_id) WHERE closing_timestamp IS NULL,
`idx_chan_validity` (funding_timestamp DESC, closing_timestamp).

**507,588 rows; 1568 MB heap + 179 MB indexes = 1747 MB** (2026-08-25).

#### `announceable_timestamp` (added 2026-07-31)

Block-header timestamp of block `(scid >> 40) + 5` — i.e. the block at which the funding
tx reaches **6 confirmations** (the BOLT 7 "SHOULD have 6 confirmations before announcing"
threshold). Added for **channel_announcement propagation-lag** analysis (lag = first
`gossip_observations.seen_at` of the announcement − `announceable_timestamp`).

- **Backfilled once** (all 500,381 rows) from Bitcoin Core `getblockhash`→`getblockheader`
  (header-only, ~1–2k blocks/s; `getblockstats` is 100× slower — it loads block bodies).
  Values are **scid-derived and Core-authoritative**, independent of `funding_timestamp`.
- **⚠️ NOT written by ingest.** `gossip-processor` doesn't populate it, so every channel
  ingested after 2026-07-31 has `announceable_timestamp = NULL` until the writer threads it
  in (same recurring "new column not in every INSERT path" defect as `internal_id` ×4) or a
  periodic backfill runs. Detect drift: `SELECT count(*) FROM channels WHERE scid IS NOT NULL
  AND announceable_timestamp IS NULL`.
- **Do NOT self-map block times from `funding_timestamp`/`closing_timestamp`.** A first
  attempt built the height→ts map from those columns; ~12+ heights have a **corrupt
  `funding_timestamp`** (doesn't match the scid's block — off by days to >1 year; some carry
  sub-second precision, i.e. a wall-clock value not a block time), which poisoned the map.
  The column is a cheap corruption detector: `announceable_timestamp - funding_timestamp`
  should be a small positive ~5-block interval (median ~46 min); gaps >1 day or strongly
  negative flag a bad `funding_timestamp` (18 such channels as of 2026-07-31).

---

### `channel_closures`

On-chain closure data for channels that have been closed.
One row per channel; PK is `gossip_id` (same as the channel's announcement gossip_id).

| column              | type           | nullable | notes                        |
|---------------------|----------------|----------|------------------------------|
| gossip_id           | varchar(64)    | NO       | PK, FK → channels(gossip_id) |
| closing_txid        | varchar(64)    | NO       | Bitcoin txid (hex)           |
| closing_height      | integer        | NO       | block height                 |
| closing_timestamp   | timestamptz    | NO       |                              |
| type                | closure_reason | NO       | enum: mutual/force/breach/unknown |
| settled_balance_sat | bigint         | NO       |                              |
| mining_fee_sat      | bigint         | NO       |                              |
| output_0_sat        | bigint         | YES      | default 0                    |
| output_1_sat        | bigint         | YES      | default 0                    |
| balance_node_1_sat  | bigint         | YES      |                              |
| balance_node_2_sat  | bigint         | YES      |                              |

FK: gossip_id → channels(gossip_id) ON DELETE CASCADE NOT VALID.

Enum `closure_reason`: `mutual`, `force`, `breach`, `unknown`.

Indexes: PK(gossip_id), (closing_height), (type),
`idx_closures_fees` (mining_fee_sat), `idx_closures_ts` (closing_timestamp).

**464,541 rows; 225 MB total** (2026-08-25).

---

### `channel_updates`

One row per unique `channel_update` message (type 258), forming a temporal chain
per (scid, direction).

| column                   | type        | nullable | notes                        |
|--------------------------|-------------|----------|------------------------------|
| gossip_id                | varchar(64) | NO       | PK                           |
| scid                     | bigint      | YES      |                              |
| direction                | bit(1)      | YES      | 0 or 1                       |
| valid_from               | timestamptz | NO       | sender timestamp             |
| valid_to                 | timestamptz | YES      | NULL = currently active      |
| signature                | varchar(144)| YES      |                              |
| chain_hash               | varchar(64) | YES      |                              |
| message_flags            | integer     | YES      |                              |
| channel_flags            | integer     | YES      |                              |
| cltv_expiry_delta        | integer     | YES      |                              |
| htlc_minimum_msat        | bigint      | YES      |                              |
| fee_base_msat            | bigint      | YES      |                              |
| fee_proportional_millionths | bigint   | YES      | ppm                          |
| htlc_maximum_msat        | bigint      | YES      |                              |
| raw_gossip               | bytea       | YES      | full envelope                |
| is_fee_update            | boolean     | YES      | fee changed vs previous      |
| is_topology_update       | boolean     | YES      | enabled/disabled bit changed |
| internal_id              | bigint      | YES      | → gossip_inventory.internal_id |
| inbound_fee_base_msat    | integer     | YES      | **VIRTUAL generated** — lnd TLV 55555, signed |
| inbound_fee_proportional_millionths | integer | YES | **VIRTUAL generated** — lnd TLV 55555, signed |

Constraints: PK(gossip_id),
FK gossip_id → gossip_inventory(gossip_id) ON DELETE CASCADE.

`is_fee_update` and `is_topology_update` can both be false (no semantically
meaningful change vs the prior update).

**Distribution — re-read from `pg_stats` 2026-08-25 over 144.5M rows** (the old
"71M rows: fee ~9.4M / topology ~19.7M / neither ~44M" line was written when the
table was half its current size and understated topology updates by 3.6×):

| flag | fraction | ≈ rows |
|---|---:|---:|
| `is_fee_update = true` | 17.44% | ~25.2M |
| `is_topology_update = true` | **49.01%** | **~70.8M** |

Read these from `pg_stats.most_common_freqs` (free) rather than counting — a
`count(*) WHERE is_topology_update` is a 16.7M-cost seq scan of the 117 GB heap.

**Channels flap hard.** Measured over 2023-10-01 (one day, via the
`valid_from` index, cost 399K, 2.95 s): 70,409 topology events touching 18,593 distinct
(scid, direction) pairs — **15.0% of all directions flipped state that day** — across
13,775 channels (19.1% of those open), at **3.79 flips per flipping direction**.
Consequence: enabled/disabled is a high-frequency signal, not a property that can be
meaningfully sampled at monthly resolution. Prefer a duty-cycle aggregate over an
instantaneous read for anything time-series.

**`is_fee_update` means OUTBOUND fee only** (`fee_base_msat` /
`fee_proportional_millionths`). It does not consider the inbound fee below.
Measured over 42,806 consecutive update pairs: inbound-fee changes are 98.4%
co-incident with outbound changes, and only 0.10% of the "neither" bucket hides an
inbound-only change — which is why the classifier was deliberately left alone
rather than recomputed over 144M rows.

#### Inbound fees — TLV 55555 (added 2026-08-21)

`inbound_fee_base_msat` and `inbound_fee_proportional_millionths` are **PG18
`VIRTUAL` generated columns**. Nothing is stored: they decode `raw_gossip` on read.
The `ALTER` was **2.958 ms** with `relfilenode` unchanged — no rewrite, no backfill,
and retroactively correct for all history back to 2024-04-29.

**Why virtual and not stored:** a stored column would have meant an UPDATE over
~32M rows (~26 GB live + 26 GB dead, index amplification across 5 indexes) — the
shape of the operation that took 643 minutes in 2026-07-18. More importantly, a
derived column **cannot fall to the recurring writer defect** (a new column not
threaded into every INSERT path, which has hit this project four times), because
`gossip-processor` never writes it at all.

**What the field is.** lnd's *inbound* fee, `lnwire.Fee{BaseFee int32, FeeRate
int32}` at TLV record type 55555 (`lnwire/typed_fee.go`), carried in the
`channel_update` extra opaque data and **covered by the signature** (`DataToSign()`
appends `ExtraOpaqueData`). Two BE `uint32` reinterpreted as `int32`.

**It is NOT a "negative fee".** `--accept-positive-inbound-fees` defaults off in
lnd, but that governs only what a node may *set*; ~3% of TLV-bearing rows on the
wire carry positive rates. Observed ranges: base −1,000,000…+1,000 msat, rate
−500,000…+10,000 ppm.

**NULL vs 0 is meaningful:** NULL = no TLV in the message; `(0,0)` = record present
and explicitly zero, i.e. lnd ≥ 0.18 with no inbound fee configured — a free
implementation fingerprint, and ~47% of TLV-bearing rows.

**Measured shape** (1,435,076-row sample, 2026-08-21). `raw_gossip` has exactly
three lengths and the magic bytes occur at position 140 and nowhere else:

| `octet_length(raw_gossip)` | meaning | share |
|---|---|---:|
| 131 | no `htlc_max`, no TLV | 0.5% |
| 139 | `htlc_max`, no TLV | 77.3% |
| 151 | `htlc_max` + 12-byte TLV | **22.2%** (~32M rows) |

Live traffic is already at ~42% TLV density, so adoption is still climbing.

**Decode is guarded** on the literal bytes `fd d9 03 08` at position 140; a guard
miss yields NULL, never a wrong number. Verified against an independent Python
`struct.unpack('>ii', …)` over 14,000 rows: **0 mismatches**.
Drift monitor: `analysis/check_channel_update_layout_drift.sql`, run **hourly over a
1-hour window** — at 24 h the planner abandons the `valid_from` index and seq-scans
117 GB (cost 1.07M vs 48.7K).

**⚠️ Three PG18 limitations that bite here:**
1. **Virtual generated columns cannot be indexed.** Use a **byte-identical**
   expression index instead — a near-miss silently produces a seq scan (verified).
   No index has been built yet: `valid_from` correlation is −0.208, so an index pass
   over 32M rows means 32M random heap fetches and the planner will prefer a seq scan
   for any full-history aggregation. **Index-only scans are impossible** on these
   columns — tested with both the covering and expression-predicate forms.
2. **They cannot call user-defined functions** ("not yet supported"), so the decode
   expression must be inlined and duplicated verbatim anywhere it is reused.
3. **`NEW.<virtual column>` reads as NULL inside a BEFORE trigger.** Reading via a
   normal `SELECT` is fine. This is why `classify_gossip_update()` could not consume
   the inbound fee even if you wanted it to.

**SQL gotcha:** in PostgreSQL `|` and `<<` bind *looser* than `-`, so
`a<<24 | b - c` parses as `a<<24 | (b-c)`. The decode needs explicit parentheses or
it yields plausible-looking wrong numbers.

Not yet done (tracked as follow-ups): `lnhistoryclient.parse_channel_update` still
discards these bytes — relevant because graph building and payment simulation then
model routing cost wrong for 22% of updates — and the .NET API does not expose them
(they must be mapped read-only there, or EF will try to write them).

Indexes — **verified 2026-07-19, only 3 remain** after the remediation (the older
8-index list above was dropped to make the bulk repair feasible and has NOT been
fully rebuilt):

Sizes and scan counts re-read 2026-08-25 (`pg_stat_user_indexes`); indexes total **42 GB**:

| index | key | size | idx_scan |
|---|---|---:|---:|
| `channel_updates_pkey` | (gossip_id) | 26 GB | **0** |
| `channel_updates_scid_direction_valid_from` | (scid, direction, valid_from) | 6707 MB | 44,569,120 |
| `channel_updates_internal_id` | (internal_id) | 5500 MB | 1,356 |
| `channel_updates_valid_from_valid_to` | (valid_from, valid_to) | 4683 MB | 25,875 |
| `idx_cu_topology_events` | (scid, direction, valid_from) INCLUDE (channel_flags) **WHERE is_topology_update** | 3359 MB | added 2026-08-25 |
| `idx_cu_active_head` | (scid, direction) **WHERE valid_to IS NULL** | 33 MB | 5,056,864 |

**`channel_updates_pkey` is 26 GB and has never been scanned** (0 scans since stats
reset). It cannot simply be dropped — it enforces the `gossip_id` PK and backs the
`ON DELETE CASCADE` FK from `gossip_inventory` — but it is the single largest object
in the database earning nothing on the read path. Worth a conversation.

**`idx_cu_topology_events` (added 2026-08-25)** exists so the enabled/disabled
state-change history can be extracted without a 117 GB seq scan. A plain index on
`is_topology_update` would be useless (49% selectivity — the planner would ignore it);
this one is *partial* (only the ~70.8M event rows) and *covering* (`INCLUDE
(channel_flags)`), so the extraction becomes an index-only scan already ordered by
(scid, direction, valid_from) — exactly the order needed to build state intervals with
`LAG`, with no sort. Built `CONCURRENTLY` in 6 min 08 s so ingest kept writing.

**It is useless on its own.** With `random_page_cost = 4` the planner ignored it entirely
and kept seq-scanning. It only pays off combined with the corrected cost setting *and* a
current visibility map — see the `random_page_cost` section at the top of this file for
the measured chain.

**`idx_cu_active_head` is load-bearing for ingest — do not drop it.** The
gossip-processor SCD close (`... WHERE scid=? AND direction=? AND valid_to IS NULL`)
takes **34.6 seconds** without it (scanning 193K rows to find 1 on a busy channel)
and **3.9 ms** with it. Its absence stalls the whole pipeline. The node tables have
the equivalent `idx_nac_active_head` / `idx_na_active_head` on `(node_id) WHERE
valid_to IS NULL`. See [[project-scd-active-head-indexes]].

**144.9M rows; 117 GB heap + 45 GB indexes = 162 GB** (2026-08-25, after adding
`idx_cu_topology_events`). `VACUUM (VERBOSE, ANALYZE)` on 2026-08-25 took **6 min 07 s**,
removed 1,129,729 dead item identifiers, and took the visibility map from **86.1% to
100.0% all-visible** (2,131,717 pages newly marked) — which is what makes index-only
scans on this table actually index-only. Dead tuples 1,126,792 → 19,962. The heap is bloated —
live data is ~73 GB of tuples (~80 GB as a fresh heap), so ~37 GB is reclaimable
free space (only 76K dead tuples; already vacuumed). Correlation on
`valid_from`/`valid_to` is ≈ −0.17, i.e. physically unordered.

Remediated 2026-07-18: `internal_id` backfilled (59,838,108 rows), valid_to chain
repaired (81,173,892 fixes), flags recomputed (18,001,610), trigger
`trigger_classify_gossip` re-enabled. Verified: 0 partitions with >1 active head.

---

### `node_announcements_complete`

All `node_announcement` messages (type 257) ever received, forming a complete
temporal chain. Every row is unique by `gossip_id`. `valid_from` is NOT NULL.

| column        | type        | nullable | notes                            |
|---------------|-------------|----------|----------------------------------|
| gossip_id     | varchar(64) | NO       | PK                               |
| node_id       | varchar(66) | YES      | FK → nodes(node_id)              |
| valid_from    | timestamptz | NO       | sender timestamp                 |
| valid_to      | timestamptz | YES      | NULL = currently active          |
| signature     | varchar(144)| YES      |                                  |
| features      | bytea       | YES      |                                  |
| rgb_color     | varchar(6)  | YES      | hex RGB e.g. "ff0000"            |
| alias         | varchar(32) | YES      |                                  |
| raw_gossip    | bytea       | YES      | full envelope                    |
| is_data_update| boolean     | YES      | alias/color/features changed     |
| internal_id   | bigint      | YES      | → gossip_inventory.internal_id   |

Constraints: PK(gossip_id),
FK gossip_id → gossip_inventory(gossip_id) ON DELETE CASCADE,
FK node_id → nodes(node_id).

`is_data_update=true` means the announcement changed something meaningful
(alias, rgb_color, features) vs the previous announcement for the same node.
~82K rows have is_data_update=true out of 22M total.

Indexes: PK,
`idx_node_ann_backfill_fast` (node_id, valid_from DESC) INCLUDE (alias, rgb_color, features, gossip_id),
`idx_node_ann_lookup` (node_id, valid_to),
`idx_node_ann_validity` (valid_from DESC, valid_to),
`idx_tmp_na_node_time` (node_id, valid_from).

**30,928,060 rows; 36 GB heap + 8900 MB indexes = 44 GB** (2026-08-25).
**Data quality note**: table content is not yet fully cleaned/verified.

---

### `node_announcements`

A filtered, deduplicated subset of `node_announcements_complete` designed for fast
analytical queries. Intended to hold only announcements where meaningful data
changed. **Data quality note**: currently under review — may still contain rows
that should be filtered out or be missing rows from the complete table.

Same schema as `node_announcements_complete` except `valid_from` is nullable.

Indexes: PK(gossip_id), (internal_id),
`idx_na_clean_node_time` (node_id, valid_from),
`idx_na_validity` (valid_from DESC, valid_to).

**91,320 rows; 123 MB total** (2026-08-25). An earlier version of this file said
"~7.7M rows" — that was wrong by ~84×, and the ln-history-api skill's "filtered ~95K"
was the correct figure.

---

### `node_addresses`

Network addresses extracted from node announcements. Multiple rows per announcement.

| column     | type        | nullable | notes                              |
|------------|-------------|----------|------------------------------------|
| id         | bigint      | NO       | PK, auto-increment                 |
| gossip_id  | varchar(64) | YES      | → gossip_inventory or node_ann     |
| type_id    | integer     | YES      | FK → address_types(id)             |
| address    | varchar(255)| YES      |                                    |
| port       | integer     | YES      |                                    |
| internal_id| bigint      | YES      | → gossip_inventory.internal_id     |

Constraints: PK(id), FK type_id → address_types(id) — **VALIDATED 2026-07-21**
(7.6 s; all 5 distinct `type_id` values resolve).

Indexes: `node_addresses_pkey` (id), `idx_addr_lookup` (address),
`idx_node_addresses_internal_id` (internal_id), `node_addresses_port` (port),
`node_addresses_type_id` (type_id).

**81,184,288 rows; 14 GB heap + 4780 MB indexes = 19 GB** (2026-08-25).

**Join addresses by `internal_id`, not `gossip_id`.** The `internal_id` column was
added 2026-07-21 precisely for this. `node_addresses.gossip_id` has **no index**, so
joining on it costs ~1.96M per lookup — over the gate. The query below was measured
at **10,664** on 2026-07-21.

```sql
SELECT na.* FROM node_addresses na
JOIN node_announcements_complete c ON c.internal_id = na.internal_id
WHERE c.node_id = '<node_id>' AND c.valid_to IS NULL;
```

`internal_id` is NOT NULL in practice (0 NULLs verified 2026-07-21) but the column is
nullable — the writer omitted it until gossip-processor 0.10.2, so any future NULLs
mean a writer regression. Check with
`SELECT count(*) FROM node_addresses WHERE internal_id IS NULL` — cheap, since the
index covers NULLs (cost ~7).

---

### `address_types`

Reference table for address type codes (BOLT 7 §network address descriptor):

| id | name  | description                      |
|----|-------|----------------------------------|
| 1  | IPv4  | Standard IPv4 address            |
| 2  | IPv6  | Standard IPv6 address            |
| 3  | TorV2 | Deprecated Tor v2 onion service  |
| 4  | TorV3 | Tor v3 onion service             |
| 5  | DNS   | DNS hostname                     |

PK(id).

---

### `nodes`

One row per unique node public key seen in channels or announcements.

| column     | type        | nullable |
|------------|-------------|----------|
| node_id    | varchar(66) | NO       | PK |
| first_seen | timestamptz | YES      |    |
| last_seen  | timestamptz | YES      |    |

PK(node_id). Index: (first_seen, last_seen). **53,105 rows** (2026-08-25).

---

### `collectors`

One row per gossip-collecting node operated for this research project.

| column                  | type        | nullable | notes                         |
|-------------------------|-------------|----------|-------------------------------|
| node_id                 | varchar(66) | NO       | PK — the collector's pubkey   |
| alias                   | varchar(100)| YES      | human label                   |
| first_collection_at     | timestamptz | YES      | default now()                 |
| last_collection_at      | timestamptz | YES      |                               |
| total_messages_collected| bigint      | YES      | default 0                     |
| notes                   | text        | YES      |                               |
| internal_collector_id   | smallint    | NO       | UNIQUE, identity — see warning below |

> **⚠️ `internal_collector_id` is a `smallint` IDENTITY — max 32767.**
> PostgreSQL evaluates an identity default (`nextval()`) **before** it detects an
> `ON CONFLICT` collision. So an `INSERT … ON CONFLICT DO UPDATE` on `collectors`
> burns one sequence value **per call**, even when it always conflicts.
> A per-message upsert therefore exhausts the sequence within minutes and every
> subsequent transaction dies with
> `nextval: reached maximum value of sequence … (32767)`.
> **This took ingest down for 23 h on 2026-07-19/20.**
> Never upsert `collectors` on the hot path — cache `node_id → internal_collector_id`
> in-process and only INSERT on a genuine miss. Reset with
> `ALTER TABLE collectors ALTER COLUMN internal_collector_id RESTART WITH <max+1>;`
> (sequences are non-transactional — a rollback does *not* give the value back).

Currently 7 rows (verified 2026-07-19):

| alias | node_id | real collector? |
|---|---|---|
| alice | `023bcadd…58fe2` | yes |
| alice-new | `02a877d7…c7a48a` | yes |
| bob | `0332dfc4…a53d28` | yes |
| bob-new | `03fc854f…c945c` | yes |
| Gossip File Import | `020000…0000` | synthetic |
| Minibolt Old Bulk import | `030000…0000` | synthetic |
| bluematt | `040000…0000` | synthetic |

The `0x020…0`, `0x030…0`, `0x040…0` entries are synthetic IDs used for batch file
imports, not real LN nodes.

PK(node_id). Indexes: (alias), (first_collection_at, last_collection_at).
Referenced by `peer_sessions.collector_node_id`.

---

## Writer history: `gossip-processor/main.py`

### FIXED in 0.10.0 (deployed 2026-07-19)

Four defects found by auditing live data, all now repaired and verified in
production (269 channel_updates written with 0 NULL `internal_id`):

- `channel_updates.internal_id` was never written — `_handle_channel_update()`
  neither accepted nor inserted it. Re-accrued the exact damage the 2026-07-18
  remediation spent 7.7 h repairing (59,838,108 rows).
- `channels.internal_id` was never written — same defect in
  `_handle_channel_announcement()`.
- `gossip_observations.sender_timestamp` was never written. **This is why
  `sender_timestamp` must NOT be used to classify artifact rows** — the newest
  row for all four real collectors had it NULL, so the "real collectors always
  populate it" signature was false.
- `insert_content()` discarded `internal_id` on the duplicate path.
  `ON CONFLICT (gossip_id) DO NOTHING RETURNING internal_id` returns no row on
  conflict, and duplicates are the majority path (every message is seen by
  several collectors). The duplicate path must re-`SELECT` the id.

Migration prompt for these: `analysis/gossip-processor-migration-prompt.md`.

### FIXED in 0.10.1 (2026-07-20) — sequence exhaustion, 23 h outage

0.10.0's `register_collector()` ran `INSERT … ON CONFLICT DO UPDATE … RETURNING`
on **every message** to resolve `internal_collector_id`. Because identity defaults
evaluate before conflict detection, this burned one `smallint` sequence value per
message and jammed at 32767 (see the `collectors` warning above). Every batch then
failed and rolled back — **193 consecutive failures, ingest dead from
2026-07-19 10:27 to 2026-07-20 09:21.**

The fix, in `Database`:
- `_collector_ids` cache; `register_collector()` does cache → `SELECT` → INSERT
  only on a genuine miss.
- `_collector_ids_pending` holds ids created by the in-flight batch. They are
  promoted to the cache by `commit_batch()` and discarded by `rollback_batch()` —
  caching an id from a rolled-back transaction would pin a nonexistent id and
  cause FK violations on every subsequent batch.
- `total_messages_collected` is accumulated in memory and folded into one UPDATE
  per collector by `flush_collector_stats()`. This also removed ~200 row-lock
  acquisitions per transaction on a 7-row table.
- `db_worker` now tracks how many messages it actually processed, so the failure
  path no longer double-calls `queue.task_done()`.

**Lesson worth generalising:** any per-message `ON CONFLICT` upsert against a
table with an identity/serial column is a sequence-consumption bug waiting to
happen, regardless of column width. Cache, or read-before-write.

### FIXED in 0.10.2 (deployed 2026-07-21) — `node_addresses.internal_id`

Same class as the 0.10.0 omissions: `insert_node_addresses()` had the announcement's
`internal_id` available in the caller but wrote only
`(gossip_id, type_id, address, port)`, so every address row ingested after the
2026-07-21 migration was invisible to the API's `internal_id` join.

Fix: `insert_node_addresses()` now takes a required `internal_id` and writes it;
all three call sites in `_handle_node_announcement()` pass it (the normal path, the
defensive gossip_id-conflict path, and the timestamp-collision path — all three must
pass it, since addresses are written on every one of them).

Backfilled in two passes (`UPDATE … FROM gossip_inventory WHERE internal_id IS NULL`):
7,098 rows pre-deploy, then 1,461 more that accrued between the first backfill and the
deploy. Post-deploy rows verified: 0 NULL. **When backfilling a table the live writer
is still appending to, expect a second pass — and take the NULL count *after* the
cutover, not before.**

**The recurring defect in this writer is a new surrogate key not being threaded into
every insert path.** It has now happened four times (`channels`, `channel_updates`,
`gossip_observations.sender_timestamp`, `node_addresses`). When adding an
`internal_id`-style column, grep every INSERT against that table and check each
early-`return` branch in the handler.

---

### `observed_peers`

Nodes that a collector has directly connected to (not necessarily announced).

| column             | type        | nullable | notes      |
|--------------------|-------------|----------|------------|
| node_id            | varchar(66) | NO       | PK         |
| first_connected_at | timestamptz | YES      | default now() |
| last_connected_at  | timestamptz | YES      |            |
| notes              | text        | YES      |            |

PK(node_id). Index: (first_connected_at, last_connected_at). ~1,271 rows.

---

### `peer_sessions`

Each TCP connection between a collector and a peer.

| column             | type        | nullable | notes                                     |
|--------------------|-------------|----------|-------------------------------------------|
| id                 | bigint      | NO       | PK, auto-increment                        |
| collector_node_id  | varchar(66) | YES      | FK → collectors(node_id)                  |
| peer_node_id       | varchar(66) | YES      | FK → observed_peers(node_id)              |
| features           | text        | YES      | init message features (hex or JSON)       |
| connected_at       | timestamptz | NO       |                                           |
| disconnected_at    | timestamptz | YES      | NULL = session still active               |
| termination_reason | text        | YES      |                                           |

Constraints: PK(id),
UNIQUE NULLS NOT DISTINCT (collector_node_id, peer_node_id, disconnected_at) — enforces at most one active session per (collector, peer) pair.

Indexes: PK, UNIQUE(one_active_session),
`idx_peer_sessions_active` (collector_node_id, disconnected_at) WHERE disconnected_at IS NULL,
`idx_peer_sessions_range` GiST (collector_node_id, tstzrange(connected_at, disconnected_at)).

~2,964 rows.

---

### `peer_addresses`

Network addresses observed during peer connection (from the init/node_announcement).

| column     | type        | nullable | notes                      |
|------------|-------------|----------|----------------------------|
| id         | bigint      | NO       | PK, auto-increment         |
| session_id | bigint      | YES      | FK → peer_sessions(id) ON DELETE CASCADE |
| type_id    | integer     | YES      | FK → address_types(id)     |
| address    | varchar(255)| YES      |                            |
| port       | integer     | YES      |                            |

Indexes: PK(id), (session_id), (type_id), (port). ~3,106 rows.

---

## Staging tables — ALL DROPPED 2026-08-26

> **All six are gone as of 2026-08-26.** The four empty ones were dropped deliberately.
> `staging_worthless_cu` (16.4M rows) and `staging_worthless_na` (18.1M rows) held 5.4 GB
> earlier the same day but had **already been dropped by something else** before that —
> no `DROP` appears in the container log, and `log_statement` is off, so a successful DDL
> from another session leaves no trace. Recorded here only so the disappearance is not
> mistaken for data loss. Database is 325 GB after the cleanup.

| table | rows (2026-08-25) | size | notes |
|---|---:|---:|---|
| `staging_worthless_na` | **18,073,064** | 3391 MB | (gossip_id), flagged duplicate node_announcements |
| `staging_worthless_cu` | **16,444,117** | 2066 MB | (internal_id, gossip_id), flagged duplicate channel_updates |
| `staging_channels` | 0 | 8 kB | same schema as `channels` |
| `staging_channel_updates` | 0 | 8 kB | same schema as `channel_updates` |
| `staging_gossip_inventory` | 0 | 0 bytes | same schema as `gossip_inventory` |
| `staging_gossip_observations` | 0 | 0 bytes | same schema as `gossip_observations` |

The four genuinely-empty ones are safe to drop. The two `worthless_*` tables record
which rows a past dedup pass judged redundant — establish whether that provenance is
still needed before touching them.

---

## Sequences

| sequence                        | used by                                   |
|---------------------------------|-------------------------------------------|
| gossip_inventory_internal_id_seq | gossip_inventory.internal_id AND gossip_inventory_future.internal_id |
| node_addresses_id_seq           | node_addresses.id                         |
| peer_sessions_id_seq            | peer_sessions.id                          |
| peer_addresses_id_seq           | peer_addresses.id                         |

All sequences increment by 1.

---

## Enum types

**`closure_reason`** (used in `channel_closures.type`):
`mutual` | `force` | `breach` | `unknown`

---

## Foreign key graph

```
address_types ◄── node_addresses.type_id         (VALIDATED 2026-07-21)
address_types ◄── peer_addresses.type_id
   note: node_addresses.internal_id → gossip_inventory.internal_id is the intended
   join for addresses, but no FK enforces it.

gossip_inventory ◄── channels.gossip_id          (ON DELETE CASCADE NOT VALID)
gossip_inventory ◄── channel_updates.gossip_id   (ON DELETE CASCADE)
gossip_inventory ◄── node_announcements.gossip_id (ON DELETE CASCADE)
gossip_inventory ◄── node_announcements_complete.gossip_id (ON DELETE CASCADE)
collectors ◄── gossip_observations.internal_collector_id   (added 2026-07-19)
   note: gossip_observations no longer has gossip_id / collector_node_id, so its
   old FK to gossip_inventory is GONE. Join via internal_id (no FK enforces it).

nodes ◄── channels.source_node_id
nodes ◄── channels.target_node_id
nodes ◄── node_announcements_complete.node_id

channels ◄── channel_closures.gossip_id          (ON DELETE CASCADE NOT VALID)

collectors ◄── peer_sessions.collector_node_id
observed_peers ◄── peer_sessions.peer_node_id
peer_sessions ◄── peer_addresses.session_id      (ON DELETE CASCADE)
```

Note: `NOT VALID` FKs were created without verifying existing data.
They enforce future inserts but pre-existing rows may violate them.
**Still NOT VALID as of 2026-07-21** (verified against `pg_constraint`):
`channels_gossip_id_fkey` and `channel_closures_gossip_id_fkey`. Every other FK in
the database is validated.

Validating is cheaper than it looks when the referenced table is small and cached —
`node_addresses_type_id_fkey` took **7.6 s over 75.9M rows**, and
`ALTER TABLE … VALIDATE CONSTRAINT` takes only SHARE UPDATE EXCLUSIVE, so ingest
keeps writing throughout. Enumerate the distinct child values first (skip scan over
the FK column's index) to confirm it will succeed before starting.
