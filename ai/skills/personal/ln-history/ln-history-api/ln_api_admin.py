#!/usr/bin/env python3
"""
ln_api_admin.py — admin tool for ln-history-api keys + usage.

Manages the `api` schema (api.api_keys / api.usage) DIRECTLY over psql — there is NO
key-management endpoint on the public API (it stays read-only). Only the admin (you),
with DB reach over Tailscale, runs this.

DSN resolution (first that is set wins):
  --dsn ARG  >  $LN_HISTORY_ADMIN_DSN  >  $PGCS  >  built from ln-history-api's
  appsettings.Development.json ConnectionStrings:PostgreSQL

Keys: generated as `lnh_<random>`; only their sha256 is stored. The raw key is printed
ONCE by `mint` and can never be recovered — lost ⇒ revoke + mint a new one.

Examples:
  ln_api_admin.py mint --owner "alice@uni.edu"
  ln_api_admin.py mint --owner "heavy-user" --daily-budget 5000 --burst 10 --max-streams 4
  ln_api_admin.py mint --owner "me" --role admin           # unlimited key
  ln_api_admin.py list
  ln_api_admin.py usage                 # today (UTC), per key
  ln_api_admin.py usage --day 2026-08-01
  ln_api_admin.py revoke lnh_ab12cd34   # by display prefix (or numeric id)
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import secrets
import subprocess
import sys

REPO_APPSETTINGS = "/Users/fabiankraus/Programming/ln-history/ln-history-api/LN-history.Startup/appsettings.Development.json"


def resolve_dsn(cli_dsn):
    if cli_dsn:
        return cli_dsn
    if os.environ.get("LN_HISTORY_ADMIN_DSN"):
        return os.environ["LN_HISTORY_ADMIN_DSN"]
    if os.environ.get("PGCS"):
        return os.environ["PGCS"]
    try:
        with open(REPO_APPSETTINGS, "r", encoding="utf-8") as fh:
            cs = json.load(fh)["ConnectionStrings"]["PostgreSQL"]
        d = dict(kv.split("=", 1) for kv in cs.split(";") if "=" in kv)
        # Npgsql keys are case-insensitive; normalize the ones we need.
        low = {k.lower(): v for k, v in d.items()}
        return (f"postgresql://{low['username']}:{low['password']}"
                f"@{low['host']}:{low.get('port', '5432')}/{low['database']}")
    except (OSError, ValueError, KeyError) as err:
        sys.stderr.write(f"Cannot resolve a DSN: {err}\nSet --dsn or $LN_HISTORY_ADMIN_DSN.\n")
        sys.exit(2)


def psql(dsn, sql, quiet=False):
    cmd = ["psql", dsn, "-v", "ON_ERROR_STOP=1"]
    if quiet:
        cmd += ["-tA", "-q"]
    cmd += ["-c", sql]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    return result.stdout


def q(value):
    """Quote a value as a SQL string literal (doubling single quotes)."""
    return "'" + str(value).replace("'", "''") + "'"


def num_or_null(value):
    return "NULL" if value is None else str(int(value))


def cmd_mint(args, dsn):
    raw = "lnh_" + secrets.token_urlsafe(24)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]
    expires = f"{q(args.expires)}::timestamptz" if args.expires else "NULL"
    sql = ("INSERT INTO api.api_keys "
           "(key_hash, display_prefix, role, owner_label, daily_budget, burst_per_sec, max_stream_conns, expires_at) "
           f"VALUES ({q(key_hash)}, {q(prefix)}, {q(args.role)}, {q(args.owner)}, "
           f"{num_or_null(args.daily_budget)}, {num_or_null(args.burst)}, {num_or_null(args.max_streams)}, {expires}) "
           "RETURNING id;")
    out = psql(dsn, sql, quiet=True).strip()
    print("=" * 64)
    print(f"  API key minted (id {out}, role {args.role}, owner {args.owner})")
    print(f"  KEY (shown once — store it now):\n\n    {raw}\n")
    print(f"  prefix {prefix} | daily_budget "
          f"{args.daily_budget if args.daily_budget is not None else 'default'} | "
          f"burst {args.burst if args.burst is not None else 'default'} | "
          f"max_streams {args.max_streams if args.max_streams is not None else 'default'}")
    print("=" * 64)


def cmd_revoke(args, dsn):
    target = args.key
    where = f"id = {int(target)}" if target.isdigit() else f"display_prefix = {q(target)}"
    out = psql(dsn, f"UPDATE api.api_keys SET enabled = false WHERE {where} RETURNING id, display_prefix;",
               quiet=True).strip()
    print(f"revoked: {out}" if out else "no matching key")


def cmd_list(args, dsn):
    print(psql(dsn,
               "SELECT id, display_prefix, role, owner_label, "
               "coalesce(daily_budget::text,'(default)') AS daily_budget, "
               "coalesce(burst_per_sec::text,'(default)') AS burst, "
               "coalesce(max_stream_conns::text,'(default)') AS max_streams, "
               "enabled, expires_at, last_used_at "
               "FROM api.api_keys ORDER BY id;"))


def cmd_usage(args, dsn):
    day = args.day
    if day in (None, "today"):
        day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    print(psql(dsn,
               "SELECT k.display_prefix, k.owner_label, u.usage_day, "
               "sum(u.sum_cost) AS cost, sum(u.req_count) AS reqs, "
               "sum(u.sum_duration_ms) AS ms, sum(u.sum_bytes) AS bytes "
               "FROM api.usage u JOIN api.api_keys k ON k.id = u.key_id "
               f"WHERE u.usage_day = {q(day)}::date "
               "GROUP BY k.display_prefix, k.owner_label, u.usage_day "
               "ORDER BY cost DESC;"))


def main():
    p = argparse.ArgumentParser(description="Admin ln-history-api keys + usage (via psql).")
    p.add_argument("--dsn", help="Postgres DSN (else $LN_HISTORY_ADMIN_DSN / $PGCS / dev appsettings)")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mint", help="create a key (prints it once)")
    m.add_argument("--owner", required=True, help="label: who this key is for")
    m.add_argument("--role", default="researcher", choices=["researcher", "admin"])
    m.add_argument("--daily-budget", type=int, help="cost units/day (default from config)")
    m.add_argument("--burst", type=int, help="requests/sec (default from config)")
    m.add_argument("--max-streams", type=int, help="concurrent WS connections (default from config)")
    m.add_argument("--expires", help="expiry date/time, e.g. 2026-12-31")

    r = sub.add_parser("revoke", help="disable a key by prefix or id")
    r.add_argument("key")

    sub.add_parser("list", help="list all keys")

    u = sub.add_parser("usage", help="per-key usage for a UTC day")
    u.add_argument("--day", help="YYYY-MM-DD or 'today' (default today, UTC)")

    args = p.parse_args()
    dsn = resolve_dsn(args.dsn)
    {"mint": cmd_mint, "revoke": cmd_revoke, "list": cmd_list, "usage": cmd_usage}[args.cmd](args, dsn)


if __name__ == "__main__":
    main()
