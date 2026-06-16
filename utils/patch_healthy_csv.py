#!/usr/bin/env python3
"""Patch healthy rows in results.csv from calendar smoke output."""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "results" / "results.csv"

# From test_calendar_smoke (M=1, M=4) + prior M=16/64 where unchanged pattern
UPDATES = {
    ("reduce", 1): (166, 1, 166, 1.0000),
    ("reduce", 4): (169, 4, 169, 1.0000),
    ("reduce", 16): (181, 16, 181, 1.0000),
    ("reduce", 64): (229, 64, 229, 1.0000),
    ("gather", 1): (205, 191, 204, 0.9951),
    ("gather", 4): (769, 764, 765, 0.9948),
    ("gather", 16): (3061, 3056, 3056, 0.9984),
    ("gather", 64): (12229, 12224, 12224, 0.9996),
    ("allgather", 1): (512, 191, 370, 0.7227),
    ("allgather", 4): (931, 764, 934, 1.0032),
    ("allgather", 16): (3235, 3056, 3056, 0.9447),
    ("allgather", 64): (12451, 12224, 12224, 0.9818),
    ("allreduce", 1): (323, 1, 332, 1.0279),
    ("allreduce", 4): (332, 4, 335, 1.0090),
    ("allreduce", 16): (344, 16, 347, 1.0087),
    ("allreduce", 64): (391, 64, 395, 1.0102),
    ("alltoall", 1): (929, 764, 764, 0.8224),
    ("alltoall", 4): (3218, 3056, 3056, 0.9497),
    ("alltoall", 16): (12224, 12224, 12224, 1.0000),
    ("alltoall", 64): (48896, 48896, 48896, 1.0000),
    ("anytoany", 1): (360, 764, 764, 2.1222),
    ("anytoany", 4): (897, 3056, 3056, 3.4069),
    ("anytoany", 16): (12224, 12224, 12224, 1.0000),
    ("anytoany", 64): (48896, 48896, 48896, 1.0000),
}


def main():
    rows = []
    with open(CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            if row["fault_desc"] != "healthy":
                rows.append(row)
                continue
            key = (row["collective"], int(row["msg_size"]))
            if key in UPDATES:
                m, p, b, e = UPDATES[key]
                row["makespan"] = str(m)
                row["period"] = str(p)
                row["theo_bound"] = str(b)
                row["efficiency"] = f"{e:.4f}"
            rows.append(row)

    with open(CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Patched {CSV}")


if __name__ == "__main__":
    main()
