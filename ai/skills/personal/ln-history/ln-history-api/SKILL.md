---
name: ln-history-api
description: >
  Architecture and reference for the ln-history-api .NET backend: a 3-layer
  (API -> Core -> Data) ASP.NET Core service that exposes the lnhistory Lightning
  Network gossip database and a Bitcoin Core node over a versioned REST API. Use when
  working on, extending, debugging, or reviewing ln-history-api — its endpoints, DTOs,
  data stores, Bitcoin RPC client, layering rules, or serialization conventions.
  Complements the ln-history-database skill (the DB schema).
---

# ln-history-api

The **query/read API** of the ln-history stack. It serves Lightning Network gossip
history (from the `lnhistory` PostgreSQL database) and Bitcoin block/transaction data
(from a Bitcoin Core node) over a versioned REST API. It is **read-only** — ingestion is
done by other services (`gossip-processor`, `peer-manager`, `chain-enricher`).

- **Repo:** `/Users/fabiankraus/Programming/ln-history/ln-history-api`
- **Stack:** .NET 10 / C#, ASP.NET Core (controllers), Dapper + Npgsql, System.Text.Json.
- **Deploy:** Docker image `ghcr.io/ln-history/ln-history`, container `ln-history-api`,
  exposed on host `127.0.0.1:5000 -> 8080`, behind an nginx reverse proxy and reachable
  from the internet at **`https://api.ln-history.info`**. OpenTelemetry -> otel-collector.
- **In-repo docs:** `Documentation/README.md` (original API design sketch),
  `Documentation/REFACTOR_PLAN.md` (the agreed design — source of truth),
  `Documentation/TODO-gossip-processor-node-addresses-internal-id.md` (a writer follow-up).

The whole thing was rebuilt from a stale state in 2026-07 into the layout below. If
something in the code contradicts this skill, trust the code and update this skill.

## Query tool (`ln_api_query.py`)

A stdlib-only Python CLI next to this skill wraps the API so you can actually pull data.
No venv needed.

```bash
TOOL=~/.config/ai/skills/personal/ln-history/ln-history-api/ln_api_query.py
python3 "$TOOL" [--url URL] [--key KEY] [--out FILE] <command> ...
```

**URL** resolution: `--url` > `$LN_HISTORY_API_URL` > `.env` `LN_HISTORY_API_URL` >
`https://api.ln-history.info` (the public reverse-proxy endpoint). **Key** (sent as
`x-api-key`, only needed when
`ApiKeyMiddleware:Enabled` is true): `--key` > `$LN_HISTORY_API_KEY` > `.env` >
the `ApiKey` in `ln-history-api/appsettings.Development.json` (dev fallback). `.env` is
searched next to the script, at `~/.config/ai/skills/personal/ln-history/.env`, and in
the cwd. See `.env.example`.

**If the API isn't deployed/reachable, run it locally (Development => auth off) and point the tool at it:**
```bash
cd /Users/fabiankraus/Programming/ln-history/ln-history-api
ASPNETCORE_ENVIRONMENT=Development dotnet run --project LN-history.Startup \
  --no-launch-profile --urls http://127.0.0.1:5199 &
python3 "$TOOL" --url http://127.0.0.1:5199 stats-network
```

Commands (JSON to stdout; octet-stream snapshots need `--out FILE`):
```
channel <scid> [--nodes] [--raw] [--at T]        channels [--at T|now] [--limit] [--offset]
channel-history <scid> [--raw] [--until T]       node <node_id> [--at T] [--all-channels] [--raw]
nodes [--at T|now] [--limit] [--offset]          node-channels <node_id> [--at T] [--raw] [--limit] [--offset]
node-history <node_id> [--raw] [--until T]        snapshot <T|now> [--with-updates] --out f.bin
snapshot-diff <start> <end> [--raw]              block <height|hash>   block-at <T>
stats-channels [--by capacity|update_count|lifetime] [--limit]
stats-nodes [--by channels|announcements|capacity] [--limit]
stats-network [--at T]    stats-closures [--from T] [--to T]
get <path> [-q k=v ...]      # generic passthrough under ln-history/v1/
```

Examples: `python3 "$TOOL" --url http://127.0.0.1:5199 channel 954349x443x2 --nodes`,
`... snapshot 2024-06-01T00:00:00Z --with-updates --out snap.bin`,
`... stats-channels --by update_count --limit 20`.

## Solution topology

```
LN-history.Api        controllers, DTOs (Dto/), domain->DTO mappers (Mapping/), query helpers,
                      api-key middleware. Class library (Microsoft.NET.Sdk) w/ FrameworkReference
                      Microsoft.AspNetCore.App. NO Npgsql/HttpClient here.
      │ uses
LN-history.Core       services (Services/) that orchestrate data stores + Bitcoin RPC and apply
                      assembly logic. Returns DOMAIN models, never DTOs. NO SQL/HTTP here.
      │ uses
LN-history.Data       Postgres data stores (DataStores/) via Dapper + a singleton NpgsqlDataSource.
                      Internal row types + mappers (Internal/). Hand-written SQL.
Bitcoin.Data          Bitcoin Core JSON-RPC client (Rpc/) + block data store (DataStores/). Data tier.
LN-History.Model      shared domain models + enums. Dependency-free LEAF. Everyone references it.
LN-history.Startup    composition root: Program.cs wires DI, MVC/JSON, versioning, Swagger, auth.
                      Microsoft.NET.Sdk.Web; this is the runnable host.

LightningGraph, LN-history.Cache   OUT OF SCOPE (present in solution, untouched, not wired in).
```

Layer rule: **API -> Core -> Data/Bitcoin.Data -> Model**. Dependencies never point up.
`ChannelListFilter` lives in `LN-History.Model` (shared) so the Data layer doesn't leak
into the API.

## Data layer (`LN-history.Data`)

- **Access:** `AddNpgsqlDataSource(connectionString)` registers a singleton
  `NpgsqlDataSource`; each data store opens a short-lived connection per query
  (`await using var conn = await _dataSource.OpenConnectionAsync(ct)`). No shared
  connection; no controller-opened connections.
- **Dapper:** `DapperConfiguration.EnsureConfigured()` sets
  `DefaultTypeMap.MatchNamesWithUnderscores = true` so snake_case columns map to
  PascalCase row types. Every query passes a `CommandDefinition(..., cancellationToken: ct)`.
- **Row types & mappers:** `Internal/Rows.cs` (flat Dapper targets) +
  `Internal/RowMappers.cs` (row -> domain). Complex/nested domain objects are built in
  code, not via Dapper multi-mapping.
- **`Internal/GossipBytes.Concat`** concatenates `raw_gossip` blobs (each is a
  self-describing `varint(len)++type++payload` envelope) into one byte stream.

Data stores (all `IXxxDataStore` + `XxxDataStore`, registered in `AddLnHistoryDatabase`):

- **`ChannelDataStore`** — `GetByScidAsync` (+ per-direction policies via `DISTINCT ON
  (direction) ... valid_from <= @asOf`, counts from `channel_update_counts`),
  `GetChannelsAsync`/`GetChannelsByNodeAsync` (paged, filter below), `GetUpdateHistoryAsync`,
  `GetHistoryRawAsync`, `ExistsAsync`.
- **`NodeDataStore`** — `GetByIdAsync` (nodes row + open degree + current announcement +
  addresses), `GetNodesAsync` (paged; existed-at via `first_seen <= T AND last_seen >= T`),
  `GetAnnouncementHistoryAsync`, `GetHistoryRawAsync`. `GetNodesAsync(existedAt, currentlyActive, …)`:
  `currentlyActive` (now) => `last_seen >= now() - interval '14 days'`; else `existedAt` =>
  `first_seen <= T AND last_seen >= T`; else all. Open degree via partial index; all-time
  degree via scan (opt-in). Addresses fetched by **`internal_id`** (see gotchas).
- **`ClosureDataStore`** — `GetByScidAsync` (join `channels` -> `channel_closures`).
- **`SnapshotDataStore`** — `GetSnapshotRawAsync` (LATERAL, see below), `GetDiffRawAsync`,
  `GetDiffEventsAsync`. Heavy queries wrapped in a txn with `SET LOCAL statement_timeout = 60000`.
- **`StatsDataStore`** — `TopChannelsAsync` (capacity/update_count/lifetime),
  `TopNodesAsync` (channels/announcements/capacity), `GetNetworkStatsAsync`, `GetClosureStatsAsync`.

`ChannelListFilter(DateTime? OpenAt, bool CurrentlyOpen, bool IncludeRawGossip, int Limit, int Offset)`:
resolution order — `OpenAt` (open at that instant) wins; else `CurrentlyOpen`
(`closing_timestamp IS NULL`); else all channels.

### The snapshot query (the one heavy endpoint)

"All gossip valid at T" is intrinsically ~0.85M–1.4M planner cost (retrieves 150–270K
gossip blobs) — **above the DB's 1M cost gate, accepted as deliberately heavy** and
guarded with a 60s `statement_timeout`. Shape that works:

```sql
WITH open_channels AS (
  SELECT scid, raw_gossip FROM channels
  WHERE funding_timestamp <= @t AND (closing_timestamp IS NULL OR closing_timestamp > @t))
SELECT raw_gossip FROM open_channels WHERE raw_gossip IS NOT NULL
UNION ALL
SELECT raw_gossip FROM node_announcements
  WHERE valid_from <= @t AND (valid_to > @t OR valid_to IS NULL) AND raw_gossip IS NOT NULL
UNION ALL   -- only when withUpdates
SELECT cu.raw_gossip FROM open_channels oc
  CROSS JOIN (VALUES (B'0'),(B'1')) AS d(direction)
  JOIN LATERAL (SELECT raw_gossip FROM channel_updates c
                WHERE c.scid=oc.scid AND c.direction=d.direction AND c.valid_from<=@t
                ORDER BY c.valid_from DESC LIMIT 1) cu ON true
  WHERE cu.raw_gossip IS NOT NULL;
```

Do **not** use `WHERE valid_to IS NULL` as a "now" shortcut for channel_updates — it
returns ~888K dangling active-heads of already-closed channels (cost 2.9M). The
open-channel-restricted LATERAL is both correct and cheaper for every timestamp.

## Bitcoin.Data — JSON-RPC client

- **`BitcoinRpcClient`** (typed `HttpClient`, HTTP Basic auth, `System.Text.Json`) bound
  to the **`Bitcoind`** config section (`RPCHost/RPCPort/RPCUser/RPCPassword`). Methods:
  `getblockcount`, `getblockhash`, `getblockstats` (one call yields every `BlockDto`
  field: `blockhash/height/time/total_size/subsidy/totalfee/txs`), `getrawtransaction`.
  Not-found RPC codes `-8`/`-5` map to `null`; `getblockstats(height, ["time"])` used for
  cheap probes.
- **`BlockDataStore`** — `GetByHeightAsync`, `GetByHashAsync`, `GetByTimestampAsync`
  (binary search for last block at-or-before T; heights are int32-guarded),
  `GetRawTransactionAsync` (for closing-tx bytes). Maps unix time -> UTC `DateTime`.
- Node has `txindex=1`, so `getrawtransaction` works for confirmed txs. Fulcrum is
  configured in appsettings but **not used** by the API.

## Core layer (`LN-history.Core`)

Thin seams over the data stores except `ChannelService`, which orchestrates across
stores. Registered in `AddLightningNetworkServices`.

- **`ChannelService.GetChannelAsync`** — gets channel + policies; when closed, attaches
  `Closure` (and, when raw requested, the raw closing tx via `IBlockDataStore`); when
  `includeNodes`, expands `Node1`/`Node2` via `INodeDataStore`. This is the one place
  that spans all four data stores.
- **`NodeService`** — single/list/history; `GetNodeHistoryAsync` = node base + full
  announcement chain.
- **`SnapshotService`**, **`BlockService`**, **`StatsService`** — delegate.

## Domain model (`LN-History.Model`)

Plain classes/records (mutable during assembly). Key types: `Channel`, `ChannelUpdate`,
`FeePolicy`, `DirectionPolicy`, `ChannelClosure`, `Node`, `NodeAnnouncement`, `Address`,
`Block`, `GossipEvent`, `Page<T>`, `ChannelListFilter`, stats records (`ChannelStat`,
`NodeStat`, `NetworkStats`, `ClosureStats`). Enums: `Direction`, `ClosureType`,
`NetworkAddressType`, `GossipEventType`, `ChannelRankBy`, `NodeRankBy`.

**`ShortChannelId`** (readonly struct) — wraps the 64-bit scid
(`block<<40 | tx<<16 | output`), `ToString()` = `"BLOCKxTXxOUTPUT"`, `TryParse`/`Parse`
accept both the integer and the `"865123x1x0"` forms. Validated against a real scid
(`1054414059077500929` = `958984x3454x1`). Unit-tested in `LN-history.Core.Tests`.

## API layer (`LN-history.Api`)

### Routes (all under `ln-history/v1/`, ASP.NET API versioning)

| Controller | Route | Notes |
|---|---|---|
| Channel | `GET channels/{scid}?nodeInformation&raw_gossip&timestamp` | `{scid}` = int or `"865123x1x0"`; ChannelDto |
| Channel | `GET channels?timestamp&limit&offset` | no ts=all, `now`=open, T=open-at-T; PagedResult, no fee_policies |
| Channel | `GET channels/{scid}/history?raw&timestamp` | raw=true octet-stream; raw=false `ChannelUpdateDto[]` (by valid_from) |
| Channel | `GET channels/capacities?timestamp` | `ChannelCapacityDto[]` (scid, scid_str, capacity_sat) for channels open at T (no ts=all, now=currently open). Lightweight companion to `snapshot` for graph building — channel_announcement gossip has no capacity. Literal route wins over `{scid}`. |
| Node | `GET nodes/{node_id}?raw_gossip&timestamp&channelCount=open\|all` | NodeDto (current announcement) |
| Node | `GET nodes?timestamp&limit&offset` | PagedResult<NodeDto> |
| Node | `GET nodes/{node_id}/history?raw&timestamp` | raw=true octet-stream; raw=false NodeDto+full chain |
| Node | `GET nodes/{node_id}/channels?raw_gossip&timestamp&limit&offset` | PagedResult<ChannelDto> |
| Snapshot | `GET snapshot/{timestamp}?withUpdates` | octet-stream; `{timestamp}` = instant or `now` |
| Snapshot | `GET snapshot-diff/{start}/{end}?rawGossip` | true=octet-stream; false=`GossipEventDto[]` ordered by time |
| Bitcoin | `GET blocks/{height:long}` / `blocks/{hash:regex 64hex}` / `blocks?timestamp` | BlockDto |
| Stats | `GET stats/channels/top?by&limit`, `stats/nodes/top?by&limit`, `stats/network?timestamp`, `stats/closures?from&to` | |
| Stream | `GET stream?types&since` (**WebSocket**) | Live raw-gossip stream. Upgrade required (plain GET=400). Each msg = one binary frame = raw gossip envelope (same bytes as snapshot). `types`=comma BOLT#7 types (256/257/258, default all); `since`=internal_id → backfill the gap then go live (reconnect catch-up, dedup by internal_id, cap 50k). Hidden from Swagger. Browsers: key via `?api_key=` (can't set headers on WS handshake). |

Controller discovery: the Api is a class library, so `Program.cs` does
`.AddApplicationPart(typeof(ChannelController).Assembly)`.

### Live gossip stream (WebSocket) — how it works

Source is **Postgres `LISTEN/NOTIFY`**, not polling. Path:
`gossip-processor INSERT → AFTER-INSERT trigger pg_notify('lnhistory_gossip', {"i":internal_id,"t":type})`
(delivered on COMMIT) `→ GossipNotificationListener` (Data, `BackgroundService`, one dedicated
Npgsql conn on `LISTEN` + a decoupled pump that fetches `raw_gossip` by internal_id on a pooled
conn) `→ IGossipStream` (Data, singleton in-memory fan-out; per-subscriber bounded channel,
**drop-oldest** so a slow client can't stall the listener) `→ IGossipStreamService` (Core wrapper:
`Subscribe` + `GetBackfillAsync`) `→ GossipStreamController` (Api, `UseWebSockets`).

- **The DB trigger is a separate migration** (`Documentation/migrations/2026-08-01-gossip-notify-trigger.sql`),
  run with the migration role. Without it the listener idles (LISTEN succeeds, no notifications) —
  the .NET side is inert but harmless. **Disable the triggers during bulk imports** (notification storm).
- Toggle: `GossipStream:Enabled` (default true) gates whether the hosted listener starts.
- Infra: nginx-proxy-manager needs **"Websockets Support"** on for the API proxy host; Cloudflare passes WS.
- No implicit replay: reconnect with `?since=<last internal_id>` to backfill; a small dup window at the
  seam is possible but gossip is content-addressed (idempotent), so dedup is safe.

### DTOs & serialization

- DTOs in `Dto/`; mapped from domain by `Mapping/DomainToDtoMapper` (`ToDto()` extensions).
- **System.Text.Json, `JsonNamingPolicy.SnakeCaseLower`** (properties -> snake_case),
  `JsonStringEnumConverter(SnakeCaseLower)` (enums -> `"mutual"`, `"channel_update"`),
  `byte[]` -> base64. **Nulls are emitted** (not omitted) — expansion/raw fields are
  always present, `null` unless requested (stable schema).
- Digit-containing names need explicit `[JsonPropertyName]` (SnakeCaseLower would produce
  `node_id1`, not `node_id_1`): `ChannelDto.NodeId1/2` -> `node_id_1/2`, `Node1/2` -> `node_1/2`.
- `raw_gossip` is the universal name for raw bytes (never `raw_bytes`).
- `ChannelDto.announceable_timestamp` (added 2026-07-31): block ts of `(scid>>40)+5` = 6-conf
  announceable point (BOLT 7). Threaded through `Channel` model → `ChannelRow` → `ToChannel` →
  `ChannelColumns` (SELECT) → `ChannelDto` → `DomainToDtoMapper`. Nullable; `null` until
  `chain-enricher` backfills it. Not derived from `funding_timestamp` (corrupt for ~18 channels).
- `channel_flags`/`message_flags` render as an 8-bit **binary string** (`"00000001"`).
- `fee_policies` = `Dictionary<string,DirectionPolicyDto>` keyed `"0"/"1"`; populated only
  on single-channel/history, omitted in list context. Missing direction -> key present with
  `fee_policy: null, total_update_count: 0`.
- `GossipEventDto.Data` is `object` (STJ serializes the runtime DTO type); `event_type` is
  a `GossipEventType` enum.

### Query parsing & conventions (`Infrastructure/QueryHelpers`)

- `TryParseTime` -> `TimeQuery{IsAll, IsNow, At}`; `AsOf` resolves `now`->UtcNow.
  `TryParseInstant` for required path timestamps (accepts `now`).
- Pagination: `ClampPage` — default limit 1000, max 10000, offset >= 0.
- Errors: `Problem(detail, statusCode)` -> ProblemDetails. 400 for bad scid/hash/timestamp,
  404 for a missing single resource, **empty list -> 200** with empty items.

### Auth & per-key rate limiting

Multi-tenant API keys with **cost-weighted** rate limiting (each researcher gets their own key;
the admin key is unlimited). Gated by `ApiKeyMiddleware:Enabled` (false in Development ⇒ no auth,
no limits). Public API stays **read-only** — keys are managed out-of-band, never minted over HTTP.

**Store — the `api` schema in the lnhistory Postgres** (owner `admin`; reader roles have no access
since it holds key hashes). Migration: `Documentation/migrations/2026-08-01-api-key-schema.sql`.
- `api.api_keys` — `id`, `key_hash` (sha256, unique-indexed), `display_prefix` (`lnh_ab12…`),
  `role` (`admin`|`researcher`), `owner_label`, `daily_budget` / `burst_per_sec` /
  `max_stream_conns` (**NULL ⇒ global default**), `expires_at`, `enabled`, `created_at`, `last_used_at`.
- `api.endpoint_weights` — `route_key` (the endpoint's raw route pattern, e.g.
  `GET ln-history/v{v}/snapshot/{timestamp}`) → `weight`. **Admin-tunable in-DB**, no redeploy.
- `api.usage` — aggregated ledger `(key_id, usage_day, endpoint)` → `sum_cost, req_count,
  sum_duration_ms, sum_bytes`. Written by async flush, read for reporting.

**Cost model.** Each request's cost = the static weight of its endpoint (seeded from documented DB
costs: cheap lookups=1, lists=3, `stats`=3, `snapshot`=100 / `?withUpdates`=150, `snapshot-diff`=40,
stream backfill=40). Budgets are spent in these units. Actual `duration_ms`+`bytes` are also recorded
for measurement/retuning, but enforcement is on the predictable weight.

**Enforcement (in-memory, authoritative; DB is the durable ledger):**
- `ApiKeyAuthMiddleware` (early) resolves the key from `x-api-key` (or `?api_key=` on the WS
  handshake), validates against the in-memory cache, 401 on bad/disabled/expired. `role=admin` ⇒
  bypass all limits. Sets the key context in `HttpContext.Items`.
- **Burst guard**: per-key token bucket (`burst_per_sec`, default 5/s, bucket 10) → 429.
- **Cost pre-charge**: `CostEnforcementFilter` (MVC resource filter, runs after routing so the
  matched route's weight is known, **before** the handler) — if `today_spent + weight > daily_budget`
  ⇒ **429 + `Retry-After` + ProblemDetails, DB never touched**; else deduct, run, record actuals,
  **refund on error/timeout**. Emits `X-RateLimit-Remaining` / `X-RateLimit-Reset`.
- **Daily budget**: fixed per-day (resets 00:00 UTC), default **1000** units/researcher. Counters
  reload from `api.usage` on startup (a restart can't reset a budget); deltas flush every few seconds.
- **WebSocket stream**: per-key concurrent-connection cap (`max_stream_conns`, default 2); the
  `?since` backfill is charged like a heavy query; the live tail is budget-free (in-memory fan-out,
  zero DB load); slots released on disconnect.

**Anti-lockout.** The config `ApiKey` remains valid as an **emergency unlimited admin** (bypasses the
store), so an empty/unreachable `api` schema can never lock you out. Researcher keys are cached in
memory and **reloaded every ~30s** — mint/revoke via SQL propagates within ~30s.

**Admin CLI** (`ln_api_admin.py`, next to this skill) — `mint` (prints the key **once**; only the
sha256 is stored), `revoke`, `list`, `usage`. Talks to the `api` schema via `psql`; reads the DSN
from `.env` (`LN_HISTORY_ADMIN_DSN`) or `$PGCS`.

```bash
ADMIN=~/.config/ai/skills/personal/ln-history/ln-history-api/ln_api_admin.py
python3 "$ADMIN" mint --owner "alice@uni.edu" [--daily-budget N] [--burst N] [--expires 2026-12-31]
python3 "$ADMIN" list                 # keys + prefixes + limits + last_used
python3 "$ADMIN" usage [--day today]  # per-key cost/requests/bytes from api.usage
python3 "$ADMIN" revoke lnh_ab12       # by display prefix or id
```

## Versioning & container image

Single source of truth is **`Directory.Build.props`** at the repo root: `<TargetFramework>`
(the .NET version), `<Version>` (the app version, inherited by every project — no per-project
`<TargetFramework>`/`<Version>`), and `<DockerImageRepository>`.

Release flow uses **commitizen** (`.cz.toml`, conventional commits):
```bash
cz bump                 # computes next version from commits, updates .cz.toml +
                        # Directory.Build.props <Version>, writes CHANGELOG, tags (tag == version)
git push --follow-tags
./scripts/build-image.sh   # docker buildx build --push, tagged with that <Version> (+ :latest)
```
`tag_format = "$version"` so the git tag equals the Docker tag. First-time only: no tag exists
yet, so run `cz bump --yes` (or `git tag <version>` once) to establish the baseline.

## Configuration (`appsettings[.Development].json`)

- `ConnectionStrings:PostgreSQL` — Npgsql conn string to `lnhistory`.
- `Bitcoind:{RPCHost,RPCPort,RPCUser,RPCPassword}` — Bitcoin Core JSON-RPC.
- `Fulcrum:{Host,Port}` — present but unused.
- `ApiKey`, `ApiKeyMiddleware:Enabled`.
- Env override in container: `ConnectionStrings__PostgreSQL`, `ApiKey`, `DOTNET_ENV`,
  `OTEL_*`.

## Running & verifying

The projects target **net10.0**. The .NET 10 SDK is installed at **`~/.dotnet`** (the
system `dotnet` in `/usr/local/share/dotnet` is still 8.0.x), so prefix commands:
`export DOTNET_ROOT=$HOME/.dotnet PATH=$HOME/.dotnet:$PATH`.

```bash
dotnet build LN-history-api.sln
# boot against live config (Development => api-key disabled):
ASPNETCORE_ENVIRONMENT=Development dotnet run --project LN-history.Startup \
  --no-launch-profile --urls http://127.0.0.1:5199
# swagger: http://127.0.0.1:5199/swagger/v1/swagger.json
```

Tests: `LN-history.Core.Tests` (ShortChannelId, NUnit). `Bitcoin.Data.Tests` has live
integration tests (`Category=Integration`) that read `BITCOIN_RPC_HOST/PORT/USER/PASSWORD`
from the environment and auto-skip if unset.

The DB is reachable from the dev machine over Tailscale; `psql` works with the
`appsettings.Development.json` PostgreSQL creds. Follow the **ln-history-database** skill's
cost-gate protocol (EXPLAIN heavy queries; > 1M total_cost => warn) before running
anything that touches `channel_updates` (145M), `gossip_inventory` (264M),
`node_addresses` (75M), etc.

## Known follow-ups / gotchas

- **`node_addresses.internal_id` writer gap.** 2026-07-21 a migration added
  `internal_id` + index to `node_addresses` so addresses join on the integer id (was a
  1.96M seq scan on the unindexed `varchar` `gossip_id`; now a ~2K index scan). The API
  joins addresses by `internal_id`. BUT `gossip-processor` doesn't yet write it, so
  addresses for announcements ingested **after 2026-07-21** have `internal_id = NULL` and
  return `addresses: []` until the writer is fixed + a backfill runs. See
  `Documentation/TODO-gossip-processor-node-addresses-internal-id.md`. `node_addresses_old`
  is the migration rollback (drop when confident; new indexes are `_new`-suffixed).
- **`ClosureType`** is mapped in the API from the `closure_reason` enum text. Future work:
  a DB-side `ClosureTypes` lookup table + `chain-enricher` change.
- **`node_announcements` (filtered ~95K) vs `node_announcements_complete` (~26M).** The
  API uses the **complete** table for single-node/current/history (authoritative,
  `idx_tmp_na_node_time` makes it cheap) and the **filtered** table for the snapshot
  active-node set (cheaper). Data quality of both is "under review".
- **scid 64-bit JSON precision**: always emit both `scid` (number) and `scid_str` (string).
- **Node `now` vs historical vs all.** For nodes, `timestamp=now` means "currently active" =
  `last_seen >= now() - interval '14 days'` (the gossip staleness window; ~5.5K nodes) — used by
  both `nodes?timestamp=now` and `stats/network?timestamp=now`. A historical `timestamp=T` uses
  point-in-time `first_seen <= T AND last_seen >= T`. **No timestamp**: `nodes` = all nodes ever
  (~53K); `stats/network` = all nodes + currently-open channels. (Channels' "now" is unchanged:
  `closing_timestamp IS NULL`.)
- **Bitcoin block times are only near-monotonic** — `GetByTimestamp` binary search can be
  off by a block or two around clock-skewed blocks; acceptable for "block in force at T".

## See also

- **ln-history-database** skill — full DB schema, indexes, ingestion pipeline, the
  hardware-aware cost-gate query protocol, and the `~/docker-compose.yaml` stack. Note
  that skill's blueprint can lag the live DB (verified deltas 2026-07-21: added
  `channel_update_counts`, `nodes.announcement_count`, `node_addresses.internal_id`;
  `channels` ~497K). Re-introspect live before trusting it.
