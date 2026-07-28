#!/usr/bin/env python3
"""
ln_api_query.py — CLI wrapper for the ln-history-api backend.

Lets an agent pull channel / node / snapshot / block / stats data from a running
ln-history-api instance. Prints JSON to stdout; writes raw gossip (octet-stream) to a
file with --out.

BASE URL resolution (first that is set wins):
  --url ARG  >  $LN_HISTORY_API_URL  >  .env LN_HISTORY_API_URL  >  https://api.ln-history.info

API KEY resolution (sent as the `x-api-key` header; only needed when the API's
ApiKeyMiddleware:Enabled is true — it is false in Development):
  --key ARG  >  $LN_HISTORY_API_KEY  >  .env LN_HISTORY_API_KEY
  >  ApiKey from ln-history-api/appsettings.Development.json (dev fallback)

.env is searched (KEY=VALUE lines) in: --env ARG, $LN_HISTORY_API_ENV, the directory of
this script, ~/.config/ai/skills/personal/ln-history/.env, and the current directory.

Examples:
  ln_api_query.py channel 954349x443x2 --nodes
  ln_api_query.py channels --at now --limit 5
  ln_api_query.py node <node_id> --all-channels
  ln_api_query.py snapshot 2024-06-01T00:00:00Z --with-updates --out snap.bin
  ln_api_query.py snapshot-diff 2024-06-01T00:00:00Z 2024-06-01T00:05:00Z
  ln_api_query.py block 800000
  ln_api_query.py stats-network --at now
  ln_api_query.py get channels/954349x443x2 -q raw_gossip=true   # generic passthrough
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://api.ln-history.info"
API_PREFIX = "ln-history/v1"
REPO_APPSETTINGS = "/Users/fabiankraus/Programming/ln-history/ln-history-api/LN-history.Startup/appsettings.Development.json"
SKILL_ENV = os.path.expanduser("~/.config/ai/skills/personal/ln-history/.env")


def load_env_file(path):
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def resolve_env(cli_env):
    candidates = [
        cli_env,
        os.environ.get("LN_HISTORY_API_ENV"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        SKILL_ENV,
        os.path.join(os.getcwd(), ".env"),
    ]
    merged = {}
    for path in candidates:
        if path and os.path.isfile(path):
            for k, v in load_env_file(path).items():
                merged.setdefault(k, v)
    return merged


def resolve_key(cli_key, env_values):
    if cli_key:
        return cli_key
    if os.environ.get("LN_HISTORY_API_KEY"):
        return os.environ["LN_HISTORY_API_KEY"]
    if env_values.get("LN_HISTORY_API_KEY"):
        return env_values["LN_HISTORY_API_KEY"]
    # dev fallback: read ApiKey straight from the repo's Development appsettings
    try:
        with open(REPO_APPSETTINGS, "r", encoding="utf-8") as fh:
            return json.load(fh).get("ApiKey")
    except (OSError, ValueError):
        return None


def resolve_url(cli_url, env_values):
    return (cli_url or os.environ.get("LN_HISTORY_API_URL")
            or env_values.get("LN_HISTORY_API_URL") or DEFAULT_URL).rstrip("/")


def request(base_url, key, path, query=None, out=None):
    url = f"{base_url}/{API_PREFIX}/{path.lstrip('/')}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, method="GET")
    if key:
        req.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        sys.stderr.write(f"HTTP {err.code} for {url}\n{detail}\n")
        sys.exit(1)
    except urllib.error.URLError as err:
        sys.stderr.write(f"Cannot reach {url}: {err.reason}\n"
                         f"Is the API running? Set --url / LN_HISTORY_API_URL.\n")
        sys.exit(2)

    if "application/octet-stream" in content_type:
        if out:
            with open(out, "wb") as fh:
                fh.write(body)
            print(f"wrote {len(body)} bytes of raw gossip to {out}")
        else:
            preview = body[:32].hex()
            print(f"[octet-stream] {len(body)} bytes (use --out FILE to save). first32=0x{preview}")
        return

    text = body.decode("utf-8", "replace")
    try:
        print(json.dumps(json.loads(text), indent=2))
    except ValueError:
        print(text)


def build_query(pairs, **flags):
    query = {}
    for key, value in flags.items():
        if value is not None:
            query[key] = "true" if value is True else ("false" if value is False else value)
    for item in pairs or []:
        if "=" in item:
            k, _, v = item.partition("=")
            query[k] = v
    return query


def main():
    parser = argparse.ArgumentParser(description="Query the ln-history-api backend.")
    parser.add_argument("--url", help="base URL (default env/.env or %s)" % DEFAULT_URL)
    parser.add_argument("--key", help="x-api-key value")
    parser.add_argument("--env", help="path to a .env file")
    parser.add_argument("--out", help="write octet-stream (snapshot) responses to this file")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="generic GET under ln-history/v1/")
    g.add_argument("path")
    g.add_argument("-q", "--query", action="append", help="k=v query params (repeatable)")

    c = sub.add_parser("channel", help="single channel by scid")
    c.add_argument("scid")
    c.add_argument("--nodes", action="store_true")
    c.add_argument("--raw", action="store_true")
    c.add_argument("--at")

    cs = sub.add_parser("channels", help="channels (all / now / at T)")
    cs.add_argument("--at")
    cs.add_argument("--limit", type=int)
    cs.add_argument("--offset", type=int)

    ch = sub.add_parser("channel-history", help="channel update history")
    ch.add_argument("scid")
    ch.add_argument("--raw", action="store_true")
    ch.add_argument("--until")

    cap = sub.add_parser("channel-capacities", help="scid+capacity_sat for channels open at T (for graph building)")
    cap.add_argument("--at")

    n = sub.add_parser("node", help="single node")
    n.add_argument("node_id")
    n.add_argument("--at")
    n.add_argument("--all-channels", action="store_true", help="also compute all-time degree")
    n.add_argument("--raw", action="store_true")

    ns = sub.add_parser("nodes", help="nodes (all / now / at T)")
    ns.add_argument("--at")
    ns.add_argument("--limit", type=int)
    ns.add_argument("--offset", type=int)

    nc = sub.add_parser("node-channels", help="channels of a node")
    nc.add_argument("node_id")
    nc.add_argument("--at")
    nc.add_argument("--raw", action="store_true")
    nc.add_argument("--limit", type=int)
    nc.add_argument("--offset", type=int)

    nh = sub.add_parser("node-history", help="node announcement history")
    nh.add_argument("node_id")
    nh.add_argument("--raw", action="store_true")
    nh.add_argument("--until")

    sn = sub.add_parser("snapshot", help="raw gossip valid at a timestamp")
    sn.add_argument("timestamp")
    sn.add_argument("--with-updates", action="store_true")

    sd = sub.add_parser("snapshot-diff", help="gossip that appeared in [start, end]")
    sd.add_argument("start")
    sd.add_argument("end")
    sd.add_argument("--raw", action="store_true", help="raw bytes instead of parsed events")

    b = sub.add_parser("block", help="block by height or 64-hex hash")
    b.add_argument("id")
    ba = sub.add_parser("block-at", help="last block at or before a timestamp")
    ba.add_argument("timestamp")

    tc = sub.add_parser("stats-channels", help="top channels")
    tc.add_argument("--by", default="capacity", choices=["capacity", "update_count", "lifetime"])
    tc.add_argument("--limit", type=int)
    tn = sub.add_parser("stats-nodes", help="top nodes")
    tn.add_argument("--by", default="channels", choices=["channels", "announcements", "capacity"])
    tn.add_argument("--limit", type=int)
    net = sub.add_parser("stats-network", help="network-wide counts")
    net.add_argument("--at")
    cl = sub.add_parser("stats-closures", help="closure stats")
    cl.add_argument("--from", dest="frm")
    cl.add_argument("--to")

    args = parser.parse_args()
    env_values = resolve_env(args.env)
    base_url = resolve_url(args.url, env_values)
    key = resolve_key(args.key, env_values)

    def go(path, query=None):
        request(base_url, key, path, query, args.out)

    if args.cmd == "get":
        go(args.path, build_query(args.query))
    elif args.cmd == "channel":
        go(f"channels/{args.scid}", build_query(None, nodeInformation=args.nodes, raw_gossip=args.raw, timestamp=args.at))
    elif args.cmd == "channels":
        go("channels", build_query(None, timestamp=args.at, limit=args.limit, offset=args.offset))
    elif args.cmd == "channel-history":
        go(f"channels/{args.scid}/history", build_query(None, raw=args.raw, timestamp=args.until))
    elif args.cmd == "channel-capacities":
        go("channels/capacities", build_query(None, timestamp=args.at))
    elif args.cmd == "node":
        go(f"nodes/{args.node_id}", build_query(None, timestamp=args.at, raw_gossip=args.raw,
                                                channelCount=("all" if args.all_channels else None)))
    elif args.cmd == "nodes":
        go("nodes", build_query(None, timestamp=args.at, limit=args.limit, offset=args.offset))
    elif args.cmd == "node-channels":
        go(f"nodes/{args.node_id}/channels", build_query(None, timestamp=args.at, raw_gossip=args.raw,
                                                          limit=args.limit, offset=args.offset))
    elif args.cmd == "node-history":
        go(f"nodes/{args.node_id}/history", build_query(None, raw=args.raw, timestamp=args.until))
    elif args.cmd == "snapshot":
        go(f"snapshot/{args.timestamp}", build_query(None, withUpdates=args.with_updates))
    elif args.cmd == "snapshot-diff":
        go(f"snapshot-diff/{args.start}/{args.end}", build_query(None, rawGossip=args.raw))
    elif args.cmd == "block":
        go(f"blocks/{args.id}")
    elif args.cmd == "block-at":
        go("blocks", build_query(None, timestamp=args.timestamp))
    elif args.cmd == "stats-channels":
        go("stats/channels/top", build_query(None, by=args.by, limit=args.limit))
    elif args.cmd == "stats-nodes":
        go("stats/nodes/top", build_query(None, by=args.by, limit=args.limit))
    elif args.cmd == "stats-network":
        go("stats/network", build_query(None, timestamp=args.at))
    elif args.cmd == "stats-closures":
        go("stats/closures", build_query(None, **{"from": args.frm, "to": args.to}))


if __name__ == "__main__":
    main()
