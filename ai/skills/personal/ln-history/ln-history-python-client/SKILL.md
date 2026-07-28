---
name: ln-history-python-client
description: >
  Architecture and usage of the `lnhistoryclient` Python library: parsing raw Lightning
  Network (BOLT #7) gossip, building networkx graphs of a snapshot, ranking nodes by
  centrality, and simulating payments. Use when parsing gossip bytes, constructing or
  analysing LN snapshot graphs, computing betweenness / capacity / fee rankings,
  simulating routing, or querying the ln-history API from Python. Bundles the
  `ln_analyze.py` CLI. Complements the ln-history-api (REST backend) and
  ln-history-database (schema) skills.
---

# ln-history-python-client

A Python library that (1) **parses** raw BOLT #7 Lightning gossip into typed dataclasses
and (2) **analyses** it — building a `networkx` graph of a network snapshot, ranking
nodes, and simulating payments. Published to PyPI as `lnhistoryclient`; the parser core
is dependency-free, everything heavier is an opt-in extra.

- **Repo:** `/Users/fabiankraus/Programming/ln-history/ln-history-python-client`
- **Venv (has the analysis deps):** `<repo>/.venv/bin/python`
- **Install for analytics:** `pip install "lnhistoryclient[analysis]"`
  (networkx, numpy, scipy, requests, pandas, matplotlib)
- **Default backend:** `http://localhost:5050` (the ln-history-api)

## Quick start — the CLI (no Python needed)

`ln_analyze.py` (next to this skill) wraps the library so an agent can pull a snapshot and
run analytics straight to JSON. Run it with the repo venv:

```bash
REPO=/Users/fabiankraus/Programming/ln-history/ln-history-python-client
PY="$REPO/.venv/bin/python"
SKILL=~/.config/ai/skills/personal/ln-history/ln-history-python-client/ln_analyze.py

"$PY" "$SKILL" stats 2021-06-01 --enrich
"$PY" "$SKILL" rank 2021-06-01 --metric betweenness --n 20 --k 500
"$PY" "$SKILL" rank 2021-06-01 --metric strength --weight capacity --n 20 --enrich
"$PY" "$SKILL" rank 2021-06-01 --metric betweenness --weight fee --n 20
"$PY" "$SKILL" simulate 2021-06-01 --n 1000 --amount 100000 --enrich
```

`<timestamp>` is an ISO instant (`2021-06-01` or `2021-06-01T00:00:00Z`) or `now`.
`--enrich` spends one extra request to attach real on-chain `capacity_sat`. `--url`
overrides the backend. See the tool's `--help` for all flags.

## Library architecture

```
lnhistoryclient/
  parser/       raw bytes -> typed dataclasses (BOLT #7 + CLN-internal + LND TLV). Zero deps.
  model/        dataclasses (ChannelAnnouncement, ChannelUpdate, NodeAnnouncement, ...)
  graph/        snapshot graph construction (needs networkx)
  analysis/     centrality, routing, payment simulation, concentration, weight conventions
  api/          LnhistoryRequester — HTTP client for the ln-history API (needs requests)
```

Layering: `analysis`/`api` -> `graph` -> `parser`/`model`. The parser never imports the
heavy layers.

### Parsing (dependency-free core)

```python
from lnhistoryclient.parser.parser_factory import get_parser_from_bytes
from lnhistoryclient.parser.common import strip_known_message_type
msg = get_parser_from_bytes(raw)(strip_known_message_type(raw))   # -> dataclass
```
`parser/gossip_file.py::read_gossip_file(path)` auto-detects GSP / CLN `gossip_store` /
plain varint-delimited files and yields raw message bytes.

### Graph model (the canonical object)

`graph.build_multidigraph(messages)` returns a **lossless `networkx.MultiDiGraph`** — the
source of truth. Every channel becomes two directed edges keyed by `scid`, so parallel
channels are preserved. Then project for the analysis you need:

- `to_undirected_simple(G)` — simple `Graph` for topological centrality (parallel channels
  summed into `capacity_sat` / `num_channels`).
- `to_directed_simple(G)` — simple `DiGraph` for routing/fees (parallels collapse to the
  cheapest policy; capacity summed; disabled only if every parallel is).

Capacity is **not** in BOLT #7 gossip. Edges carry an `htlc_maximum_msat` proxy;
`graph.attach_capacity(G, scid_to_cap)` (or the API's `enrich_capacity=True`) writes real
`capacity_sat`. `graph.graph_stats(G)` returns nodes/channels/components as a JSON dict.

### Node ranking

One entry point over a metric enum:

```python
from lnhistoryclient.analysis import Metric, top_nodes_by
top_nodes_by(G, Metric.BETWEENNESS)                    # unweighted routing importance
top_nodes_by(G, Metric.STRENGTH, weight="capacity")    # most liquidity
top_nodes_by(G, Metric.BETWEENNESS, weight="fee")      # cheapest-path centrality
```
Metrics: `DEGREE, STRENGTH, BETWEENNESS, CLOSENESS, PAGERANK, EIGENVECTOR`. `weight` is
`None | "capacity" | "fee"` (`fee` only for path metrics). `k=` enables approximate
betweenness sampling on large graphs; `seed=` for reproducibility. Returns `NodeRank`
records (`rank, node_id, alias, score, num_channels, capacity_sat, announced`).

### Weight conventions (the one place sign/inversion bugs hide)

`analysis/weights.py` is the single source of truth:
- **fee** distance = `fee_base_msat + amount_sat*1000*ppm/1e6` (reference amount default
  100 000 sat) — a cost.
- **capacity** for *path* metrics = `1/capacity` (high capacity is *preferred*, so inverted
  to a distance); for *strength* metrics = raw capacity summed.
- Fee-weighted path metrics run on the **directed** projection (fees are directional);
  unweighted/capacity betweenness run on the undirected topology.

### Payment simulation (balance-agnostic)

```python
from lnhistoryclient.analysis import simulate_payment, simulate_random_payments
simulate_payment(G, src, dst, amount_sat=100_000)                 # single path, cheapest fee
simulate_random_payments(G, n=1000, amount_sat=100_000, seed=42)  # Monte-Carlo summary
```
Single-path cheapest-fee Dijkstra, honouring disabled / htlc-min / htlc-max / capacity as
**hard filters**. **Gossip never reveals channel balances**, so a "success" means a route
*could* exist — an upper bound on routability, not a liquidity guarantee. Routing is behind
`analysis.routing.RoutingStrategy`; a multi-part (MPP) or probabilistic router drops in
there without changing the simulation API.

### API client

```python
from lnhistoryclient.api import LnhistoryRequester
with LnhistoryRequester(backend_url="http://localhost:5050") as c:
    G = c.get_snapshot(datetime(2021,6,1), with_updates=True, enrich_capacity=True)
```
Also `get_capacities(ts)` (bulk `scid->capacity_sat`), `get_node`, `get_channel`,
`get_network_stats`. `api_key` is optional (dev backends run with auth off). The old
`lnhistoryclient.Lnhistoryrequester` import path still works as a shim.

## ⚠️ Critical gotcha: the snapshot stream is inconsistently framed

The `/snapshot/{ts}` octet-stream concatenates `raw_gossip` blobs whose framing **drifted
across historical imports** and is NOT uniform:

- **channel_announcement** blobs: `varint_le(payload_len) ++ type ++ payload` where the
  varint counts the payload only (excludes the 2-byte type). E.g. a 443-byte blob is
  `fd b6 01`(=438) + `01 00`(type 256) + 438-byte payload.
- **node_announcement** blobs: **no varint prefix** — start directly with the type `01 01`.
- **channel_update** blobs: a varint whose value counts differently, plus stray bytes.

Because of this, `api.requester.iter_snapshot_messages` **ignores the varint's numeric
value**: it uses the varint only to locate the 2-byte type, computes each message's true
length from its BOLT #7 structure (`_unframed_message_length`), validates every parse
(`_is_plausible`: pubkey prefix 0x02/0x03, positive scid, sane timestamp), and resyncs one
byte past any false match. This recovers exact channel counts and ~all updates (2021
snapshot: went from 405 to 38 141 updates once fixed). **Do not "simplify" this reader to a
plain varint loop — it will silently drop most of the stream.**

The proper fix is a backend data repair (normalise every `raw_gossip` blob to one framing).
Note `gossip_id = sha256(raw_gossip)`, so rewriting bytes changes primary keys — decide
recompute-and-cascade vs. keep-id before any write. A ready-to-hand-off repair brief lives
next to this skill: **`raw_gossip_repair_prompt.md`** (Phase 1 read-only blast-radius audit
by table/type/import-source → Phase 2 rename-swap repair → Phase 3 verify). Run it against
the DB with the ln-history-database skill.

## Testing & conventions

- `pytest` (in the `dev` extra): `cd <repo> && .venv/bin/python -m pytest -q`. 48 unit
  tests cover weights, projections, centrality, routing, and the snapshot reader on toy
  graphs with known answers. API tests are not run without a backend.
- black + ruff (line length 120) via pre-commit; conventional commits (commitizen `cz bump`).
- `examples/analyse_snapshot.py` is the full end-to-end showcase;
  `examples/download_snapshots.py` / `plot_centrality_history.py` are the historical-analysis
  scripts, now built on the library.

## See also

- **ln-history-api** — the REST backend this client calls (endpoints, DTOs, the snapshot
  query). Note `channels/capacities` is the bulk endpoint powering `enrich_capacity`.
- **ln-history-database** — the `lnhistory` schema and the cost-gate query protocol; needed
  for the `raw_gossip` framing repair.
