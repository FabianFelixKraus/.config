# Task: Audit & repair the `raw_gossip` envelope across the `lnhistory` database

You are working on the **`lnhistory` PostgreSQL database** (the ln-history gossip store).
Use the **`ln-history-database`** skill for schema, connection, the hardware-aware
cost-gate protocol, and the write/admin credentials. Use the **`ln-history-api`** skill
only for context on how blobs are served.

Every gossip-carrying row stores a `raw_gossip` `bytea`. These blobs are supposed to be a
single, self-describing envelope. **Because gossip was ingested from several different
importers over time (real collectors, "Gossip File Import" `0x0200…`, "Minibolt Old Bulk
import" `0x0300…`, "bluematt" `0x0400…`), the envelope framing has drifted and is now
inconsistent.** Your job is to (1) measure the blast radius precisely, then (2) repair
every malformed blob so the whole column is consistent and matches the canonical format.

## Canonical envelope (the target every blob must match)

```
raw_gossip = varint_le(N) ++ uint16_be(type) ++ payload
             └─ CompactSize, little-endian
                            └─ 2-byte BOLT message type, big-endian
                                          └─ N bytes
```

- `varint_le` is a **Bitcoin CompactSize** integer, **little-endian** for the multi-byte
  forms (`0xFD`+2, `0xFE`+4, `0xFF`+8; values <253 are a single byte).
- **`N` = `len(type) + len(payload)` = `2 + len(payload)`** — i.e. the varint counts
  **everything after itself**, so a reader can do `n = read_varint(); blob = read(n)` and
  get exactly `type ++ payload`. Total stored bytes = `sizeof(varint) + N`.
- `type` is big-endian: `01 00` = 256 (channel_announcement), `01 01` = 257
  (node_announcement), `01 02` = 258 (channel_update); CLN-internal types 4101–4106 may
  also appear in some tables.

**VERIFY THE CANONICAL FIRST — do not assume.** Before rewriting anything, confirm which
convention the *authoritative writer* (`gossip-processor/main.py`, module
`insert_content`) and the *authoritative reader* actually use — specifically whether the
varint counts `2 + payload` (recommended above) or `payload` only. Pick the convention
the live pipeline expects and enforce it consistently; the wrong choice corrupts every
blob. Document the decision explicitly in your report before any write.

### Ground-truth anomalies already observed (starting evidence, not exhaustive)

Measured against the live backend on `localhost:5050` while debugging the Python client:

- **channel_announcement blob** (`channels.raw_gossip`): 443 bytes,
  `fd b6 01 | 01 00 | <438-byte payload>`. Here `varint = 438 = len(payload)` — it
  **excludes** the 2-byte type. (Off-by-2 vs. the recommended canonical.)
- **node_announcement blob** (`node_announcements*.raw_gossip`): begins **directly with
  the type** `01 01 …` — **no varint prefix at all**.
- **channel_update blob** (`channel_updates.raw_gossip`): has a 1-byte varint whose value
  again does not consistently equal `2 + payload`; some records carry a stray trailing
  byte, so record stride ≠ declared length.

So at least three distinct corruption classes coexist, and they correlate with message
type / table (and probably with import source). Confirm and quantify all of them.

## Tables in scope (all have a `raw_gossip` column)

| table | approx rows | notes |
|---|---|---|
| `channels` | ~500 K | type 256 |
| `channel_updates` | ~144 M | type 258 — **huge; cost-gate every query** |
| `node_announcements` | ~7.7 M | type 257 (filtered) |
| `node_announcements_complete` | ~26 M | type 257 (full history) |

Follow the **cost-gate protocol**: run `ln_db_explain.py` before any aggregate/scan on
`channel_updates` / `node_announcements_complete`; if `total_cost > 1,000,000`, STOP and
warn me (with the estimate, the 8-vCore/32 GB/600 GB-NVMe hardware note, and a request
for permission) before running it.

## Phase 1 — Blast-radius audit (READ-ONLY, do this first)

Use the read-only `ai_reader` role (`ln_db_query.py`). Classify each blob without writing.
A blob is **conformant** iff: the leading CompactSize varint decodes to `N`, the next 2
bytes are a known type, the total stored length equals `sizeof(varint) + N`, **and** the
payload structurally parses for that type (use the `lnhistoryclient` parser in
`/Users/fabiankraus/Programming/ln-history/ln-history-python-client` — `varint_decode`,
`varint_encode`, `strip_known_message_type`, and the `parse_*` functions — do not
re-implement BOLT parsing).

Because conformance requires structural parsing, do the classification in a **Python
script that streams rows** (server-side cursor / keyset pagination on `internal_id`),
not in pure SQL. Sample first (e.g. 100 K rows per table, stratified) to design the
classifier, then run it at scale only with permission for the big tables.

Report the **blast radius** with these breakdowns:

1. **Overall**: per table — total blobs, # conformant, # malformed, % malformed.
2. **By message type** (256 / 257 / 258 / CLN).
3. **By corruption class**, at minimum:
   - `missing_varint` (blob starts with the type, no length prefix)
   - `varint_counts_payload` (off-by-2: varint = `len(payload)` not `2 + payload`)
   - `varint_counts_message_minus_something` / other off-by-k
   - `wrong_endianness` (varint decodes only when read big-endian)
   - `trailing_junk` / `leading_junk` (extra bytes beyond the parsed message)
   - `truncated` (declared/structural length exceeds stored bytes)
   - `unknown_or_wrong_type`
   - `unparseable` (type known but payload fails structural parse)
4. **By probable import source**: join to `gossip_observations` →
   `collectors` (via `internal_collector_id`) to attribute each `internal_id` to the
   importer that first wrote it, and cross-tabulate corruption class × collector
   (`alice/alice-new/bob/bob-new` vs the synthetic `Gossip File Import` / `Minibolt Old
   Bulk import` / `bluematt`). This directly answers "which imports caused the drift."
5. **Representative hex examples** (first ~16 bytes) for each corruption class.

Deliver Phase 1 as a written report + a machine-readable summary (JSON/CSV) before
touching any data.

## Phase 2 — Repair (WRITE — only after I approve the Phase-1 report)

Requires the **admin/migration role** (`.env.migration`, via `docker exec … psql`; `psql`
is not on the host). Heed the skill's warnings:

- **Do NOT do an in-place `UPDATE` on `channel_updates` (144 M rows)** — that repeats the
  index write-amplification that made a prior repair take 643 minutes. Use the
  **`CREATE TABLE new` + bulk `INSERT` (normalized blobs) + rename-swap** pattern.
- **After every rename-swap, re-apply grants** (`GRANT SELECT … TO ai_reader,
  grafanareader, …`) and diff `relacl` against the `_old` table — grants follow the OID,
  not the name, so the swapped-in table silently loses reader access otherwise. Rename
  the `_new`-suffixed constraints/indexes back to canonical names after dropping `_old`.
- Keep `<table>_old` until I confirm, for rollback.

Repair algorithm per blob (idempotent):
1. Parse the blob leniently to recover `type` and `payload` (handle each corruption class
   from Phase 1 — strip/recover a bad or missing varint, drop trailing junk, fix
   endianness).
2. Re-emit the canonical envelope: `varint_encode(2 + len(payload)) ++ type ++ payload`.
3. If the blob is already canonical, emit it unchanged (idempotent — re-running is a
   no-op).
4. If a blob cannot be safely recovered (truncated / unknown type / payload won't parse),
   **do not guess** — leave it unchanged, flag it, and count it separately. Report the
   irreparable set; do not silently drop rows.

`gossip_id` is `sha256(raw_gossip)`, so **rewriting the bytes changes the primary key.**
Decide and document how to handle this before writing — options: (a) recompute `gossip_id`
and cascade to all FK references (`channels`, `channel_updates`, `node_announcements*`,
`gossip_observations` via `internal_id`), or (b) keep `gossip_id`/`internal_id` stable and
only normalize the stored bytes, accepting that `gossip_id` no longer equals
`sha256(raw_gossip)` for repaired rows. This is a correctness-critical decision — surface
the trade-off and get my sign-off. `internal_id` is stable either way; prefer it for joins.

## Phase 3 — Verify

- Re-run the Phase-1 classifier on the repaired tables: expect **0 malformed** (except the
  flagged-irreparable set).
- Round-trip check: for a random sample, `read varint → read N → parse` yields the same
  typed message as parsing the original recoverable blob.
- Confirm grants match `_old`; confirm row counts reconcile (0 unexpected drops);
  confirm the live API snapshot stream for a couple of timestamps now parses to a single
  consistent framing (the Python client's `iter_snapshot_messages` should need **no**
  resyncs afterward — that's the acceptance signal).

## Deliverables

1. Phase-1 blast-radius report (tables above + import-source cross-tab + hex examples).
2. The confirmed canonical definition and the `gossip_id` decision, each with rationale.
3. The repair migration script(s) + a dry-run summary (counts to be changed per class).
4. Post-repair verification results.
5. A short "how the drift happened" narrative tied to the offending importers, so the
   `gossip-processor` writer can be fixed to prevent recurrence.

Work read-only until Phase 1 is reviewed. Cost-gate every heavy query. Never write
without explicit approval.
