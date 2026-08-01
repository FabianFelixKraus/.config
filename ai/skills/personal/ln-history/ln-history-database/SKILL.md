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
by one or more collector nodes. The tool for querying it is
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
GRANT SELECT ON X TO ai_reader;   -- plus grafanareader, and any other reader role
```

The same applies to constraint/index names: they follow the OID too, so a swapped-in
table keeps its `*_new_*` names until explicitly renamed (which can only happen after
the old table is dropped, since the canonical names are still occupied by it).

---

## Hardware-Aware Query Protocol

The database runs on a VPS with **8 AMD EPYC vCores, 32 GB ECC RAM, and a 600 GB NVMe drive**.

### The Golden Rule

Before using `ln_db_query.py` for any complex query — aggregations, joins, or queries that touch large tables (`gossip_observations` ~95.5M rows / 56 GB, `channel_updates` ~144M rows / 147 GB, `node_announcements_complete` ~26M rows, `node_addresses` ~75.9M rows / 17 GB, etc.) — you **MUST** first run the query through `ln_db_explain.py`.

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
| first_seen_at | timestamptz   | YES      | now()                              |
| internal_id   | bigint        | NO       | nextval(gossip_inventory_internal_id_seq) |

Constraints: PK(gossip_id), UNIQUE(internal_id).

Indexes: `gossip_inventory_first_seen_at` (first_seen_at DESC),
`gossip_inventory_type` (type), `unique_internal_id` (internal_id).

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

**95,503,734 rows; 7797 MB total** (was 18 GB heap + 38 GB indexes = 56 GB — an
86% reduction). One index only: the PK.

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
7-row list, and `gossip_observations.internal_collector_id` is an FK to it. Do not
aggregate over the 95.5M-row observations table for this.

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

~362K rows.

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

~304K rows.

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

Constraints: PK(gossip_id),
FK gossip_id → gossip_inventory(gossip_id) ON DELETE CASCADE.

`is_fee_update` and `is_topology_update` can both be false (no semantically
meaningful change vs the prior update). Distribution (71M rows):
fee_update=true ~9.4M, topology_update=true ~19.7M, neither ~44M.

Indexes — **verified 2026-07-19, only 3 remain** after the remediation (the older
8-index list above was dropped to make the bulk repair feasible and has NOT been
fully rebuilt):

| index | key | size |
|---|---|---:|
| `channel_updates_pkey` | (gossip_id) | 21 GB |
| `channel_updates_scid_direction_valid_from` | (scid, direction, valid_from) | 5579 MB |
| `channel_updates_valid_from_valid_to` | (valid_from, valid_to) | 3559 MB |
| `channel_updates_internal_id` | (internal_id) | 3090 MB (rebuilt 2026-07-19) |
| `idx_cu_active_head` | (scid, direction) **WHERE valid_to IS NULL** | 24 MB (added 2026-07-19) |

**`idx_cu_active_head` is load-bearing for ingest — do not drop it.** The
gossip-processor SCD close (`... WHERE scid=? AND direction=? AND valid_to IS NULL`)
takes **34.6 seconds** without it (scanning 193K rows to find 1 on a busy channel)
and **3.9 ms** with it. Its absence stalls the whole pipeline. The node tables have
the equivalent `idx_nac_active_head` / `idx_na_active_head` on `(node_id) WHERE
valid_to IS NULL`. See [[project-scd-active-head-indexes]].

**144,207,373 rows; 117 GB heap + 30 GB indexes = 147 GB.** The heap is bloated —
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

~22M rows. **Data quality note**: table content is not yet fully cleaned/verified.

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

~7.7M rows.

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

**75.9M rows; 13 GB heap + 4119 MB indexes = 17 GB.**

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

PK(node_id). Index: (first_seen, last_seen). ~47K rows.

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

## Staging tables (migration artifacts — all empty)

The following tables exist but contain 0 rows. They appear to be migration
intermediaries that were never dropped:

- `staging_gossip_inventory` — same schema as `gossip_inventory`
- `staging_gossip_observations` — same schema as `gossip_observations`
- `staging_channels` — same schema as `channels`
- `staging_channel_updates` — same schema as `channel_updates`
- `staging_worthless_cu` — (internal_id, gossip_id), flagged duplicate channel_updates
- `staging_worthless_na` — (gossip_id), flagged duplicate node_announcements

These can be safely dropped after confirming with the owner.

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
