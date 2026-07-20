#!/home/bitcoin/ln-history-research/analysis/.venv/bin/python3
"""EXPLAIN a SQL query against the lnhistory database. Returns Total Cost and Plan Rows."""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
import psycopg2
import psycopg2.extras


def main():
    parser = argparse.ArgumentParser(
        description="EXPLAIN a SQL query and return Total Cost and Plan Rows."
    )
    parser.add_argument("query", help="SQL query to explain")
    args = parser.parse_args()

    load_dotenv(os.path.join(os.getcwd(), ".env"))

    host = os.getenv("DB_HOST", "localhost")
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

    explain_sql = f"EXPLAIN (FORMAT JSON) {args.query}"

    try:
        with conn.cursor() as cur:
            cur.execute(explain_sql)
            result = cur.fetchone()
    except psycopg2.Error as e:
        print(f"EXPLAIN error: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    plan = result[0][0]["Plan"]
    total_cost = plan.get("Total Cost")
    plan_rows = plan.get("Plan Rows")

    print(json.dumps({"total_cost": total_cost, "plan_rows": plan_rows}, indent=2))


if __name__ == "__main__":
    main()
