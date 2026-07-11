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

Credentials are read from `/home/bitcoin/ln-history-research/analysis/.env`.

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
`gossip_observations` and in `channel_updates`/`node_announcements` to join
back to the inventory without repeating `gossip_id`.

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
        ├──► gossip_observations  ← (gossip_id, collector_node_id) — who saw it, when
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

| column             | type        | nullable |
|--------------------|-------------|----------|
| internal_id        | bigint      | YES      |
| gossip_id          | varchar(64) | NO       |
| collector_node_id  | varchar(66) | NO       |
| seen_at            | timestamptz | YES      |
| sender_timestamp   | timestamptz | YES      |

Constraints: PK(gossip_id, collector_node_id), FK gossip_id → gossip_inventory(gossip_id) ON DELETE CASCADE NOT VALID.

`seen_at` = wall-clock time the collector received the message.
`sender_timestamp` = timestamp embedded in the gossip message by its originator.

Indexes: PK, (internal_id, collector_node_id), collector_node_id,
seen_at, sender_timestamp, (collector_node_id, seen_at, internal_id),
internal_id.

~95M rows.

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

Constraints: PK(gossip_id), UNIQUE(scid),
FK gossip_id → gossip_inventory(gossip_id) ON DELETE CASCADE NOT VALID,
FK source_node_id → nodes(node_id),
FK target_node_id → nodes(node_id).

Indexes: PK, UNIQUE(scid), (internal_id),
`idx_active_channels_nodes` (source_node_id, target_node_id) WHERE closing_timestamp IS NULL,
`idx_chan_validity` (funding_timestamp DESC, closing_timestamp).

~362K rows.

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

Indexes: PK, (internal_id), (is_fee_update), (is_topology_update),
(fee_proportional_millionths), (fee_base_msat, fee_proportional_millionths),
`idx_chan_upd_history_order` (scid, direction, valid_from),
`idx_chan_upd_lookup` (scid, direction, valid_to),
`idx_chan_upd_validity` (valid_from DESC, valid_to),
`idx_cu_clean_scid_dir_time` (scid, direction, valid_from),
`idx_updates_flags` (scid, direction) WHERE is_fee_update OR is_topology_update.

~71M rows.

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

| column   | type        | nullable | notes                          |
|----------|-------------|----------|--------------------------------|
| id       | bigint      | NO       | PK, auto-increment             |
| gossip_id| varchar(64) | YES      | → gossip_inventory or node_ann |
| type_id  | integer     | YES      | FK → address_types(id)         |
| address  | varchar(255)| YES      |                                |
| port     | integer     | YES      |                                |

FK: type_id → address_types(id).

Indexes: PK(id), (type_id), (port), `idx_addr_lookup` (address).

~66M rows.

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

Currently 7 rows: `alice`, `bob`, `Gossip File Import`, `Minibolt Old Bulk import`,
`bluematt` (bulk import from bitcoin.ninja/ln-replay-data), `bob-new`, `alice-new`.
The `0x020...0`, `0x030...0`, `0x040...0` entries are synthetic IDs used for
batch file imports, not real LN nodes.

PK(node_id). Indexes: (alias), (first_collection_at, last_collection_at).

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
address_types ◄── node_addresses.type_id
address_types ◄── peer_addresses.type_id

gossip_inventory ◄── channels.gossip_id          (ON DELETE CASCADE NOT VALID)
gossip_inventory ◄── channel_updates.gossip_id   (ON DELETE CASCADE)
gossip_inventory ◄── node_announcements.gossip_id (ON DELETE CASCADE)
gossip_inventory ◄── node_announcements_complete.gossip_id (ON DELETE CASCADE)
gossip_inventory ◄── gossip_observations.gossip_id (ON DELETE CASCADE NOT VALID)

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
