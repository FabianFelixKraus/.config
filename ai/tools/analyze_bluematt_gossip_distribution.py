#!/usr/bin/env python3
"""
Analyze temporal distribution of Lightning Network channel_update binary files.

Binary format per record:
  8 bytes: <Q  Unix timestamp (unsigned long long, little-endian)
  2 bytes: <H  payload length (unsigned short, little-endian)
  N bytes: payload (skipped via seek)
"""

import os
import struct
import sys
from collections import Counter
from datetime import datetime, timezone

HEADER_FMT = "<QH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 10 bytes


def iter_timestamps(filepath: str):
    """Yield Unix timestamps from a channel_update binary file, seeking over payloads."""
    with open(filepath, "rb") as f:
        while True:
            header = f.read(HEADER_SIZE)
            if not header:
                break
            if len(header) < HEADER_SIZE:
                print(f"  warning: truncated header at end of {os.path.basename(filepath)}, skipping {len(header)} bytes", file=sys.stderr)
                break
            ts, payload_len = struct.unpack(HEADER_FMT, header)
            yield ts
            f.seek(payload_len, os.SEEK_CUR)


def count_by_month(filepath: str) -> tuple[Counter, int]:
    counts: Counter = Counter()
    total = 0
    for ts in iter_timestamps(filepath):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        counts[dt.strftime("%Y-%m")] += 1
        total += 1
        if total % 1_000_000 == 0:
            print(f"  {os.path.basename(filepath)}: {total:,} records...", file=sys.stderr)
    return counts, total


def main():
    base = os.path.expanduser("~/gossip_stores")
    files = {
        "channel_updates.bin": os.path.join(base, "channel_updates.bin"),
        "channel_updates_from_1690848000.bin": os.path.join(base, "channel_updates_from_1690848000.bin"),
    }

    for label, path in files.items():
        if not os.path.exists(path):
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            sys.exit(1)

    counts = {}
    totals = {}
    for label, path in files.items():
        print(f"Scanning {label} ({os.path.getsize(path) / 1e9:.1f} GB)...", file=sys.stderr)
        counts[label], totals[label] = count_by_month(path)

    all_months = sorted(set(counts["channel_updates.bin"]) | set(counts["channel_updates_from_1690848000.bin"]))

    col_a = "channel_updates.bin"
    col_b = "channel_updates_from_1690848000.bin"
    w0, w1, w2 = 12, 24, 36

    sep = f"+{'-'*(w0+2)}+{'-'*(w1+2)}+{'-'*(w2+2)}+"
    header = f"| {'Year-Month':<{w0}} | {col_a:<{w1}} | {col_b:<{w2}} |"

    print()
    print(sep)
    print(header)
    print(sep)
    for month in all_months:
        a = counts[col_a].get(month, 0)
        b = counts[col_b].get(month, 0)
        print(f"| {month:<{w0}} | {a:>{w1},} | {b:>{w2},} |")
    print(sep)
    print(f"| {'TOTAL':<{w0}} | {totals[col_a]:>{w1},} | {totals[col_b]:>{w2},} |")
    print(sep)
    print()

    # Overlap analysis
    only_a = sum(v for k, v in counts[col_a].items() if k not in counts[col_b])
    only_b = sum(v for k, v in counts[col_b].items() if k not in counts[col_a])
    shared_months = set(counts[col_a]) & set(counts[col_b])
    print(f"Months only in {col_a}: {sorted(set(counts[col_a]) - set(counts[col_b]))}")
    print(f"Months only in {col_b}: {sorted(set(counts[col_b]) - set(counts[col_a]))}")
    print(f"Shared months: {len(shared_months)}")
    print(f"Records in months exclusive to {col_a}: {only_a:,}")
    print(f"Records in months exclusive to {col_b}: {only_b:,}")


if __name__ == "__main__":
    main()
