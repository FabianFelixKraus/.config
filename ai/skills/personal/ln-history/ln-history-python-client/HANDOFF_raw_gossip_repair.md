# HANDOFF — `raw_gossip` envelope audit & repair (`lnhistory` DB)

**For:** a fresh agent running **directly on the DB host** (the VPS that runs the
`ln-history-database` Postgres container and the `gossip-processor` writer).
**Written:** 2026-07-28, by the prior agent (running on the user's laptop, no DB access).
**Status:** design + Phase-1a tooling complete and unit-tested. **Nothing has been run
against the database yet. No data has been written. No audit numbers exist yet.**

Your immediate job is **Phase 1a**: run the sampler, produce the blast-radius estimate,
report it to the user, and **stop for sign-off**. Do **not** start any repair (Phase 2)
without explicit approval — it rewrites primary keys across the DB.

> ⚠️ **Load these skills first** (see "Suggested skills" at the bottom): `ln-history-database`
> (schema, cost-gate protocol, credentials, gotchas) and `ln-history-python-client`
> (the parser you must reuse). This handoff assumes their content and does **not** repeat it.

---

## 0. TL;DR / what you are actually doing

The `raw_gossip bytea` column (present in `channels`, `channel_updates`,
`node_announcements`, `node_announcements_complete`) is supposed to hold ONE self-describing
envelope format. Historically it drifted — different importers framed it differently. The
end goal is: **every blob in the canonical format, and `gossip_id = sha256(raw_gossip)` true
again for every row.** That's a large, PK-changing migration, so we're doing it carefully and
in stages.

**Right now** we only want to know **how big the problem is** — a cheap, read-only,
sample-based estimate (Phase 1a). Everything downstream (the actual repair) is designed but
deferred and depends on the numbers you produce.

This handoff **overrides the original brief** `raw_gossip_repair_prompt.md` in several
important places (see §1 and §6). Where they conflict, **this handoff wins** — it reflects
decisions made with the user after verifying against live code.

---

## 1. The canonical format — CONFIRMED, do not re-derive or "improve"

The original brief `raw_gossip_repair_prompt.md` recommended a format where the varint
counts `2 + len(payload)`. **That recommendation is WRONG for this database. Do not use it.**
Using it would mis-flag every currently-correct row as broken and trigger a full-DB rewrite.

The **authoritative writer** is `gossip-processor` (module `insert_content`, helper
`_build_raw_gossip`). Verified verbatim:

```python
def _build_raw_gossip(self, raw_payload_with_type: bytes) -> bytes:
    """Encode as varint(payload_len) + 2-byte msg_type + payload."""
    payload_len = len(raw_payload_with_type) - 2      # varint EXCLUDES the 2-byte type
    ... # little-endian: <H / <I / <Q for the 0xFD / 0xFE / 0xFF forms
    return varint + raw_payload_with_type
# gossip_id = sha256(raw_gossip_bytes).hexdigest()
```

**Canonical envelope (the single target):**
```
raw_gossip = varint_le(len(payload)) ++ uint16_be(type) ++ payload
             │ Bitcoin CompactSize, little-endian, value = len(payload) ONLY (excludes type)
                                     │ 2-byte BOLT type, big-endian: 0100=256, 0101=257, 0102=258
                                                    │ len(payload) bytes
gossip_id  = sha256(raw_gossip)   (hex, lowercase, 64 chars)
```
Example (channel_announcement): `fd b6 01`(=438, LE) `01 00`(type 256) + 438-byte payload.
That IS conformant. (438 = payload length, not 440.)

**Corollary you must internalise — drift is HISTORICAL, not ongoing.** The current writer
canonicalizes *every* message on ingest (`_strip_varint_len` → `_build_raw_gossip` → hash of
the canonical bytes). So live ingest is **not** adding new drift; you are repairing a fixed
backlog. There is no "fix the writer first / fight the writer during cleanup" problem.

- **VERIFY THIS ON THE HOST before trusting it:** confirm the *deployed* `gossip-processor`
  actually contains `_build_raw_gossip` as above (the source reviewed was ≥ v0.10.2). Find the
  running writer (container / repo on the host) and grep for `_build_raw_gossip` and
  `calculate_gossip_id`. If a *different* version is deployed that stores raw bytes as-received
  **without** canonicalizing, then the "historical only" premise is broken and you must
  re-open the convergence question with the user before any repair.

---

## 2. Two DIFFERENT checks — do not conflate them

The user's first instinct was "check each `raw_gossip` against its `gossip_id`." That does
**not** detect framing drift. Here's why, and what to do instead.

- `gossip_id` was computed as `sha256(raw_gossip)` **at ingest, over whatever bytes were
  ingested — including drifted ones.** So a drifted-but-untouched row has
  `gossip_id == sha256(raw_gossip)` **TRUE** even though its framing is wrong. The hash matches
  because it's the hash *of the wrong bytes*.
- Therefore there are **two independent checks**:
  1. **Framing conformance** (the real audit, STRUCTURAL): the leading CompactSize decodes,
     the next 2 bytes are a known type, `len(blob) == sizeof(varint) + 2 + len(payload)`, and
     the payload **structurally parses** for that type (via the `lnhistoryclient` parser).
     → This is what defines "drifted."
  2. **ID integrity** (SEPARATE signal): does `gossip_id == sha256(raw_gossip)` *today*?
     Finds rows whose bytes were edited out-of-band after ingest (rare). Reported alongside,
     **never** used to decide conformance.

The sampler reports both. Do not let anyone collapse them.

---

## 3. Why the repair is a DEDUP-MERGE, not a rewrite (the twin problem)

Because the live writer already canonicalizes, the DB very likely **already contains duplicate
logical messages**:

- A message bulk-imported years ago with drifted framing → stored with
  `gossip_id = sha256(drifted_bytes)`, `internal_id = A`.
- The *same* message later seen live → the writer canonicalized it and did
  `INSERT gossip_inventory ... ON CONFLICT (gossip_id) DO NOTHING`. The canonical hash
  **differs** from the drifted hash → **no conflict → a second row** with the correct
  `gossip_id` and a **different** `internal_id = B`.

So when you canonicalize the drifted row in Phase 2, its recomputed `gossip_id` will often
**equal the twin's existing `gossip_id`** → a blind `UPDATE ... SET gossip_id` would violate
the PK (and `channels.scid` UNIQUE, and the channel_updates SCD chain). The repair must
therefore branch **per row**:

| framing | canonical twin already exists? | Phase-2 action |
|---|---|---|
| conformant | — | leave untouched |
| drifted | **yes** | **MERGE**: delete the drifted duplicate, repoint its `gossip_observations` onto the twin via the stable `internal_id`, keep the canonical row |
| drifted | no | rewrite bytes + recompute `gossip_id` + cascade FKs |

**Phase 1a measures the twin-collision rate** precisely so we know how much of the "repair" is
actually *de-duplication* vs. *rewrite*. If most drifted rows have a twin, the repair is
mostly `DELETE`s (cheaper, and arguably the more valuable cleanup).

---

## 4. Attribution is INVERTED (a trap from this DB's own history)

The original brief says "attribute drift to the importer via `gossip_observations` →
`collectors`." **That join is broken for the rows you care about.** On 2026-07-19 the
~9.95M artifact observation rows belonging to the synthetic importers (`Gossip File Import`
`0200…`, `Minibolt Old Bulk import` `0300…`, `bluematt` `0400…`) were **deleted**. Those
synthetic importers are exactly the suspected drift sources — so a naive join finds *no*
observation for them and makes drift look like it came from the real collectors. Backwards.

**Use the inverted signal instead:**
- drifted row **with** a surviving observation from a real collector (`alice`, `alice-new`,
  `bob`, `bob-new`) → seen live.
- drifted row **with no surviving observation at all** → its only observers were the purged
  synthetic collectors → **bulk-import origin**.
- Plus `gossip_inventory.first_seen_at` for the ingest era.

The sampler implements exactly this (buckets: `real_collector`, `synthetic_no_obs`,
`synthetic_obs_residual`, `no_internal_id`).

---

## 5. THE IMMEDIATE TASK — run Phase 1a

### 5.1 The tool
`audit_raw_gossip_framing.py` — a self-contained, **read-only** (`ai_reader`) Python script.
It should be transferred to the host **together with this handoff** (they lived side by side
in the laptop's scratchpad). If you only have this handoff, §8 fully specifies the classifier
so you can rebuild the script; but prefer transferring the file to avoid divergence.

What it does:
- Samples each table **uniformly by `gossip_id` PK** (a sha256 hex — uniform over rows,
  index-supported on all four tables). It deliberately does **NOT** keyset on `internal_id`,
  because `node_announcements_complete` has **no standalone `internal_id` index** and would
  seq-scan 26M rows per probe.
- Classifies each blob **structurally** by reusing the frozen `lnhistoryclient` parser
  (`varint_decode`, `varint_encode`, `parser_factory`, and the requester's
  `_unframed_message_length` / `_is_plausible`). **It never re-implements BOLT parsing.**
- For each **drifted** row: recomputes the canonical `gossip_id`, probes `gossip_inventory`
  in bulk for a twin (collision → merge), and attributes the source via the inverted
  observation signal.
- Reports per table with **Wilson 95% CIs**: drifted %, class mix, twin-collision %, source
  split, drift-by-era, and hash-integrity (separate). Writes `phase1a_summary.json`,
  `phase1a_classes.csv`, `phase1a_report.txt` into `--out-dir`.

Unit-tested on the laptop: the classifier correctly bins constructed conformant /
missing_varint / trailing_junk / off-by-2(→wrong_length) / truncated / unknown_type blobs.
It was **not** run against the DB (no access there).

### 5.2 How to run (on the host)
```bash
# smoke test FIRST — tiny sample, one table, confirms creds + index plan cheaply
/home/bitcoin/ln-history-research/analysis/.venv/bin/python3 \
  audit_raw_gossip_framing.py \
  --env /home/bitcoin/ln-history-research/analysis/.env \
  --sample-size 200 --tables channel_updates --out-dir ./raw_gossip_audit_smoke

# then the full estimate across all four tables
/home/bitcoin/ln-history-research/analysis/.venv/bin/python3 \
  audit_raw_gossip_framing.py \
  --env /home/bitcoin/ln-history-research/analysis/.env \
  --sample-size 20000 --out-dir ./raw_gossip_audit
```
- The `--env` file is the **read-only `ai_reader`** role. The script reads `DB_*` /
  `POSTGRES_URI` from it. **Do not** put admin credentials here — Phase 1a is read-only.
- If `ai_reader` lacks direct network access and you can only reach PG via
  `docker exec ... psql`, you'll need to either (a) grant the venv host a psycopg route to the
  container's port, or (b) adapt the script's `load_dsn`/connection to shell out through the
  container. Prefer a direct read-only psycopg connection if the host allows it.

### 5.3 Cost-gate obligation (from the `ln-history-database` skill)
The sampler's per-table query is a `unnest(random_hex[]) CROSS JOIN LATERAL (… WHERE
gossip_id >= r ORDER BY gossip_id LIMIT 1)`. Each lateral iteration is a PK index probe, so it
should be cheap — but **you must confirm, not assume**. Before the full 20k run on
`channel_updates` (144M rows) and `node_announcements_complete` (26M):
- Run `ln_db_explain.py` on the sampling query (or just trust the `--sample-size 200` smoke
  run's wall-clock), and if any plan shows a **Seq Scan** on the big table, or `total_cost`
  > 1,000,000, **STOP and warn the user** (estimate + the 8-vCore/32 GB/600 GB-NVMe note +
  ask permission), per the protocol. It should be an Index Scan / Index Only Scan on the PK.

### 5.4 Deliverable & the STOP point
Produce the written report + the JSON/CSV, then **summarise to the user and wait.** The key
numbers they need to choose the Phase-2 strategy: **drifted % per table**, **twin-collision %**,
**class mix**, and **source split**. Do not proceed to Phase 2 writes without explicit sign-off.

---

## 6. Phase 2 (DESIGNED, DEFERRED — do NOT execute without approval)

Recorded here so you understand the whole arc and can plan. Decisions already made with the user:

- **Chosen `gossip_id` policy = recompute + cascade** (position A): after canonicalizing bytes,
  set `gossip_id = sha256(canonical)` so the invariant holds again. (The user explicitly
  rejected keeping a stale `gossip_id`, which would permanently break `gossip_id = sha256(...)`.)
- **Repair is a throttled, targeted TRICKLE over ~1 month**, touching only the drifted subset,
  in small batches with sleeps, because **the user needs the DB's performance for other work.**
  Idempotent (re-running a canonical row is a no-op).
- **The original brief mandates a big-bang `CREATE TABLE new + rename-swap` for
  `channel_updates`. That directly CONTRADICTS the trickle/low-load constraint** (a rename-swap
  rewrites the entire 117 GB heap + all indexes). **Resolution: the mechanism is
  DATA-CONTINGENT and chosen per table AFTER Phase 1a:**
  - small/bounded drifted fraction → **default: throttled targeted `UPDATE`/merge trickle**
    (only the drifted `internal_id`s; nothing like the full-table update that caused the prior
    643-minute write-amplification incident).
  - a table that turns out **mostly** drifted → the trickle would itself rewrite most of the
    heap, so `rename-swap` (with a maintenance window) becomes the lesser evil — but then tell
    the user "low-load" and "done this month" may be mutually exclusive for that table.
- **`internal_id` is the stable join key throughout** — use it for merges/repointing, never
  `gossip_id` (which is what's changing).

Per-blob repair algorithm (idempotent):
1. Classify (reuse the sampler's `classify()`), recover `(type, payload)`.
2. Compute `canon = varint_encode(len(payload)) + type + payload`, `new_gid = sha256(canon)`.
3. If already conformant → skip.
4. Else if `new_gid` already exists in `gossip_inventory` (twin) → **merge**: repoint
   `gossip_observations` from the drifted `internal_id` to the twin's, delete the drifted rows
   (content table + inventory). Mind the `gossip_observations` PK `(internal_id,
   internal_collector_id)` — a repoint that would collide with an existing obs row must
   `DO NOTHING`/dedupe, not error.
5. Else (no twin) → rewrite `raw_gossip = canon`, set `gossip_id = new_gid` in the content
   table **and** `gossip_inventory`, and handle the FK edges (see below).
6. **Irreparable** (truncated / unknown type / payload won't parse) → **leave unchanged, flag,
   count separately. Never guess, never drop rows silently.**

FK / cascade mechanics you must design (get user sign-off on the concrete SQL):
- `gossip_id` is the PK of `gossip_inventory`, `channels`, `channel_updates`,
  `node_announcements`, `node_announcements_complete`, and is an FK **target** from those
  content tables → `gossip_inventory(gossip_id)`, and from `channel_closures(gossip_id)` →
  `channels(gossip_id)`. The existing FKs are `ON DELETE CASCADE`, **not `ON UPDATE CASCADE`**.
  So an in-place `gossip_id` change needs either deferrable constraints, a temporary
  `ON UPDATE CASCADE`, or a delete+reinsert within one transaction. `gossip_observations` has
  **no** `gossip_id` column (it uses `internal_id`) — good, one less thing to cascade.
- `channels_gossip_id_fkey` and `channel_closures_gossip_id_fkey` are still **`NOT VALID`** —
  don't let repair writes trip over that.

Migration hygiene (from the `ln-history-database` skill — heed all of it):
- **After any rename-swap, re-apply grants** (`GRANT SELECT … TO ai_reader, grafanareader, …`)
  and diff `relacl` against the `_old` table — grants follow the OID, not the name.
- Keep `<table>_old` until the user confirms rollback isn't needed.
- Admin/migration credentials: `/home/bitcoin/ln-history-research/analysis/.env.migration`
  (and full-access `/home/bitcoin/ln-history-research/.env`). `psql` is not on the host — reach
  PG via `docker exec … ln-history-database psql`. **(Credentials are in those files; they are
  not reproduced here.)**

Phase 3 acceptance signal (the whole project is "done" when):
- The Phase-1 classifier reports **0 drifted** (except the flagged-irreparable set).
- Row counts reconcile (0 unexpected drops).
- The live API snapshot stream for a couple of timestamps parses to a single consistent
  framing — the Python client's `iter_snapshot_messages` needs **0 resyncs** afterward.

---

## 7. Hard constraints / do-nots

- **Do NOT modify the `lnhistoryclient` Python library.** It is intentionally frozen. Its
  reader (`iter_snapshot_messages`) is deliberately lenient (ignores the varint value, derives
  length structurally, resyncs past stray bytes) and must keep working *during* the repair. The
  repair is a DB-side data fix, not a client change.
- **Do NOT write to the DB in Phase 1.** Read-only `ai_reader` only.
- **Do NOT start Phase 2 without explicit user approval** of the Phase-1a numbers and the
  chosen per-table mechanism.
- **Do NOT trust the `gossip_id == sha256` check as a conformance test** (see §2).
- **Do NOT use the original brief's `2 + payload` varint definition** (see §1).
- **Respect the cost-gate**: any aggregate/scan whose `total_cost` > 1,000,000 → stop & warn.
- **Never `ON CONFLICT DO UPDATE` on `collectors`** on any hot path (smallint identity
  sequence exhaustion — see the skill). Not expected in this work, but noted.

---

## 8. Classifier spec (so the script is reconstructible if it didn't transfer)

`classify(b: bytes) -> (klass, recovered_type, canonical_bytes|None)`; reuse the frozen
library — `from lnhistoryclient.parser.common import varint_decode, varint_encode`,
`from lnhistoryclient.parser import parser_factory`,
`from lnhistoryclient.api.requester import _unframed_message_length, _is_plausible`. Known types
= {256,257,258}. Varint size from first byte: `<0xFD→1, 0xFD→3, 0xFE→5, 0xFF→9`.

Hypotheses, in order:
1. **Framed (canonical):** `s=varint_size(b[0])`; decode LE value `N=varint_decode(b[:s])`;
   `t=BE(b[s:s+2])`. If `t` known: `L=_unframed_message_length(b, s)` (length incl. type). If
   `L` and `s+L<=len(b)`: parse `payload=b[s+2:s+L]`; if parses & `_is_plausible`:
   - `N==L-2 and len(b)==s+L` → **conformant** (canon == b)
   - `len(b)==s+L and N!=L-2` → **wrong_length_varint** (e.g. off-by-2 counting the type)
   - `len(b)>s+L` → **trailing_junk**
   - else → **wrong_length_varint**.
   If `t` known but `L` is None or `s+L>len(b)` → set `needs_more=True`.
2. **Unframed (missing varint):** `t0=BE(b[0:2])`. If known: `L0=_unframed_message_length(b,0)`;
   if `L0<=len(b)` and `payload=b[2:L0]` parses & plausible → **missing_varint**; if type known
   but junk after → **trailing_junk**; if `L0` None or `>len(b)` → `needs_more=True`.
3. **Wrong-endianness:** only for multi-byte varints; if the varint decodes plausibly only as
   **big-endian** and the framed message then parses → **wrong_endianness**.
4. **Fallbacks:** `saw_known_type and needs_more` → **truncated**; `saw_known_type` →
   **unparseable**; else → **unknown_type**.

`canonical_bytes = varint_encode(len(payload)) + type.to_bytes(2,'big') + payload` whenever a
message was recovered (== `b` for conformant); None for truncated/unparseable/unknown.
Wilson 95% CI for every reported proportion. Sample by random 64-char hex `gossip_id` keyset.
Twin check: `SELECT gossip_id FROM gossip_inventory WHERE gossip_id = ANY(<recomputed>)`.
Source: `bool_or(collectors.alias IN ('alice','alice-new','bob','bob-new'))` grouped by
`gossip_observations.internal_id`; absent internal_id → bulk-import.

---

## 9. Reference map (mind LOCAL vs SERVER paths)

**On the HOST (you can open these):**
- `ln-history-database` skill — schema, cost-gate, credentials, migration gotchas. **Read it.**
- `ln-history-python-client` skill — the parser you reuse; the "inconsistently framed stream"
  gotcha section.
- `/home/bitcoin/.config/ai/tools/ln_db_query.py` (read-only queries),
  `/home/bitcoin/.config/ai/tools/ln_db_explain.py` (cost-gate).
- `/home/bitcoin/ln-history-research/analysis/.venv/bin/python3` — has `lnhistoryclient` +
  `psycopg`; run the sampler with this.
- `/home/bitcoin/ln-history-research/analysis/.env` (ai_reader),
  `…/.env.migration` (admin), `/home/bitcoin/ln-history-research/.env` (full). **Secrets — not
  reproduced in this handoff.**
- The deployed `gossip-processor` writer — **locate it and confirm `_build_raw_gossip`** (§1).
- `audit_raw_gossip_framing.py` — the Phase-1a sampler (transferred alongside this file).

**On the user's LAPTOP (referenced only; you cannot open these):**
- `raw_gossip_repair_prompt.md` — the ORIGINAL brief. Useful for the Phase-1/2/3 structure and
  the deliverables list, **but wrong on the canonical format (§1) and the attribution method
  (§4) — this handoff supersedes those.**
- `gossip-processor/main.py` — the writer source that was reviewed to confirm §1.
- `lnhistoryclient/api/requester.py` — where `_unframed_message_length` / `_is_plausible` /
  `iter_snapshot_messages` live.

---

## Suggested skills to invoke

1. **`ln-history-database`** — REQUIRED. Schema, the hardware-aware cost-gate protocol, admin
   credentials/`docker exec psql` recipe, and the rename-swap-drops-grants / collector-sequence
   gotchas. Everything DB-side depends on it.
2. **`ln-history-python-client`** — REQUIRED. The parser you must reuse (no re-implementing
   BOLT), and the canonical "inconsistently framed stream" context.
3. **`ln-history-api`** — for Phase 3 acceptance (checking the snapshot stream parses cleanly)
   and to understand who consumes `raw_gossip`.
4. **`grilling`** — optional, if you need to re-open a decision with the user (e.g. Phase-2
   mechanism per table) and want to pressure-test it before executing.
```
```

**First action:** load `ln-history-database` + `ln-history-python-client`, confirm the deployed
writer canonicalizes (§1), then run the §5.2 smoke test and, if the plan is clean, the full
Phase-1a estimate. Report the numbers and stop for sign-off.
