#!/home/bitcoin/ln-history-research/analysis/.venv/bin/python3
"""Query the lnhistory PostgreSQL database. Reads credentials from .env in CWD."""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
import psycopg2
import psycopg2.extras


def main():
    parser = argparse.ArgumentParser(description="Run a SQL query against the lnhistory database.")
    parser.add_argument("query", help="SQL query to execute")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON instead of table")
    args = parser.parse_args()

    load_dotenv(os.path.join(os.getcwd(), ".env"))

    host = os.getenv("DB_HOST", "localhost")
    # Always connect via localhost since the container exposes port 5432 on 127.0.0.1
    if host != "localhost" and not host.startswith("127."):
        host = "localhost"

    conn_params = {
        "host": host,
        "port": int(os.getenv("DB_PORT", 5432)),
        "dbname": "lnhistory",
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "options": "-c search_path=public",
    }

    missing = [k for k, v in conn_params.items() if v is None]
    if missing:
        print(f"Error: missing .env variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    try:
        conn = psycopg2.connect(**conn_params)
    except psycopg2.OperationalError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(args.query)
            rows = cur.fetchall() if cur.description else []
            col_names = [desc.name for desc in cur.description] if cur.description else []
    except psycopg2.Error as e:
        print(f"Query error: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    if not rows:
        print("(no rows returned)")
        return

    data = [dict(r) for r in rows]

    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
        return

    # Formatted table output
    col_widths = {c: len(c) for c in col_names}
    str_rows = []
    for row in data:
        str_row = {c: str(row[c]) if row[c] is not None else "NULL" for c in col_names}
        for c in col_names:
            col_widths[c] = max(col_widths[c], len(str_row[c]))
        str_rows.append(str_row)

    sep = "+-" + "-+-".join("-" * col_widths[c] for c in col_names) + "-+"
    header = "| " + " | ".join(c.ljust(col_widths[c]) for c in col_names) + " |"

    print(sep)
    print(header)
    print(sep)
    for row in str_rows:
        print("| " + " | ".join(row[c].ljust(col_widths[c]) for c in col_names) + " |")
    print(sep)
    print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")


if __name__ == "__main__":
    main()
