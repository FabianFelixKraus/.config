#!/usr/bin/env python3
"""
ln_analyze.py — CLI wrapper around the `lnhistoryclient` Python library.

Lets an agent fetch a Lightning Network snapshot from the ln-history API and run graph
analytics (topology stats, node ranking, payment simulation) WITHOUT writing Python.
Prints JSON to stdout.

Requires the library's analysis extra (networkx, numpy, scipy, requests, pandas). The
simplest way to satisfy that is to run this with the client repo's virtualenv:

    REPO=/Users/fabiankraus/Programming/ln-history/ln-history-python-client
    "$REPO/.venv/bin/python" ~/.config/ai/skills/personal/ln-history/ln-history-python-client/ln_analyze.py \
        stats 2021-06-01 --enrich

Backend URL resolution:  --url ARG  >  $LN_HISTORY_BACKEND_URL  >  http://localhost:5050

Commands:
  stats     <timestamp> [--no-updates] [--enrich]                 topology statistics
  rank      <timestamp> [--metric M] [--weight W] [--n N]         top nodes by a metric
                        [--amount SAT] [--k K] [--enrich]
  simulate  <timestamp> [--n N] [--amount SAT] [--seed S]         Monte-Carlo payments
                        [--enrich]

  <timestamp> is an ISO 8601 instant (2021-06-01 or 2021-06-01T00:00:00Z) or "now".
  --metric  : degree | strength | betweenness | closeness | pagerank | eigenvector
  --weight  : none | capacity | fee   (fee only valid for betweenness/closeness)
  --enrich  : also fetch on-chain capacity_sat (one extra request) and attach it

Examples:
  ln_analyze.py stats 2021-06-01 --enrich
  ln_analyze.py rank 2021-06-01 --metric betweenness --n 20 --k 500
  ln_analyze.py rank 2021-06-01 --metric strength --weight capacity --n 20 --enrich
  ln_analyze.py rank 2021-06-01 --metric betweenness --weight fee --n 20
  ln_analyze.py simulate 2021-06-01 --n 1000 --amount 100000 --enrich
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone

# The library is not pip-installed globally; make it importable from its repo checkout.
REPO = os.environ.get("LN_HISTORY_CLIENT_REPO", "/Users/fabiankraus/Programming/ln-history/ln-history-python-client")
if REPO not in sys.path:
    sys.path.insert(0, REPO)

try:
    from lnhistoryclient.analysis import Metric, simulate_random_payments, top_nodes_by
    from lnhistoryclient.api import LnhistoryRequester
    from lnhistoryclient.graph import graph_stats
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        f"Failed to import lnhistoryclient ({exc}).\n"
        f"Run this with the client repo venv, e.g.:\n"
        f"  {REPO}/.venv/bin/python {sys.argv[0]} ...\n"
        f"or set LN_HISTORY_CLIENT_REPO to the repo path.\n"
    )
    sys.exit(2)

DEFAULT_URL = "http://localhost:5050"


def parse_timestamp(value):
    if value == "now":
        return "now"
    # Accept "2021-06-01" and full ISO instants; assume UTC when no tz given.
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def resolve_url(cli_url):
    return cli_url or os.environ.get("LN_HISTORY_BACKEND_URL") or DEFAULT_URL


def get_graph(args):
    url = resolve_url(args.url)
    ts = parse_timestamp(args.timestamp)
    with LnhistoryRequester(backend_url=url) as client:
        return client.get_snapshot(
            ts,
            with_updates=not getattr(args, "no_updates", False),
            enrich_capacity=getattr(args, "enrich", False),
        )


def cmd_stats(args):
    graph = get_graph(args)
    print(json.dumps(graph_stats(graph, label=str(args.timestamp)), indent=2, default=str))


def cmd_rank(args):
    graph = get_graph(args)
    weight = None if args.weight in (None, "none") else args.weight
    ranked = top_nodes_by(
        graph,
        Metric(args.metric),
        weight=weight,
        n=args.n,
        amount_sat=args.amount,
        k=args.k,
        seed=args.seed,
    )
    out = [
        {
            "rank": r.rank,
            "node_id": r.node_id,
            "alias": r.alias,
            "score": r.score,
            "num_channels": r.num_channels,
            "capacity_sat": r.capacity_sat,
            "announced": r.announced,
        }
        for r in ranked
    ]
    print(json.dumps({"metric": args.metric, "weight": weight, "nodes": out}, indent=2, default=str))


def cmd_simulate(args):
    graph = get_graph(args)
    summary = simulate_random_payments(graph, n=args.n, amount_sat=args.amount, seed=args.seed)
    print(
        json.dumps(
            {
                "trials": summary.trials,
                "amount_sat": summary.amount_sat,
                "num_success": summary.num_success,
                "num_failure": summary.num_failure,
                "success_rate": summary.success_rate,
                "median_hops": statistics.median(summary.hops) if summary.hops else None,
                "median_fee_msat": statistics.median(summary.fees_msat) if summary.fees_msat else None,
                "failure_reasons": summary.failure_reasons,
            },
            indent=2,
            default=str,
        )
    )


def build_parser():
    p = argparse.ArgumentParser(description="Analytics over ln-history snapshots.")
    p.add_argument("--url", help=f"backend base URL (default {DEFAULT_URL})")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("timestamp", help="ISO instant (2021-06-01[T..Z]) or 'now'")
        sp.add_argument("--enrich", action="store_true", help="attach on-chain capacity_sat")

    sp = sub.add_parser("stats", help="topology statistics")
    add_common(sp)
    sp.add_argument("--no-updates", action="store_true", help="omit channel_updates")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("rank", help="top nodes by a metric")
    add_common(sp)
    sp.add_argument("--metric", default="betweenness",
                    choices=[m.value for m in Metric])
    sp.add_argument("--weight", default="none", choices=["none", "capacity", "fee"])
    sp.add_argument("--n", type=int, default=20)
    sp.add_argument("--amount", type=int, default=100_000, help="reference amount (sat) for fee weighting")
    sp.add_argument("--k", type=int, default=None, help="pivot sample size for approx betweenness")
    sp.add_argument("--seed", type=int, default=42)
    sp.set_defaults(func=cmd_rank)

    sp = sub.add_parser("simulate", help="Monte-Carlo payment simulation")
    add_common(sp)
    sp.add_argument("--n", type=int, default=1000)
    sp.add_argument("--amount", type=int, default=100_000)
    sp.add_argument("--seed", type=int, default=42)
    sp.set_defaults(func=cmd_simulate)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
