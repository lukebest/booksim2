#!/usr/bin/env python3
"""Run BookSim2 trace sweep for 6x8 allgather (Route A hop + Route B tree)."""

import argparse
import csv
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOKSIM = ROOT / "src" / "booksim"
BASE_CONFIG = ROOT / "runfiles" / "traceconfig"
TRACE_DIR = ROOT / "results" / "traces" / "6x8"
OUT_CSV = ROOT / "results" / "booksim_trace_sweep.csv"
OUT_JSON = ROOT / "results" / "booksim_trace_sweep.json"


def run_one(trace_mode, trace_file, fork_file, expected_mk, m, router="iq"):
    cfg_lines = BASE_CONFIG.read_text(encoding="utf-8").splitlines()
    overrides = {
        "trace_mode": trace_mode,
        "trace_file": str(trace_file.relative_to(ROOT)),
        "fork_file": str(fork_file.relative_to(ROOT)) if fork_file else "",
        "expected_makespan": str(expected_mk),
        "msg_size": str(m),
        "router": router,
        "result_csv": "results/booksim_trace_row.csv",
    }
    out_lines = []
    for line in cfg_lines:
        key = line.split("=")[0].strip() if "=" in line else None
        if key in overrides:
            out_lines.append(f"{key} = {overrides[key]};")
        else:
            out_lines.append(line)
    with tempfile.NamedTemporaryFile("w", suffix=".config", delete=False, encoding="utf-8") as tf:
        tf.write("\n".join(out_lines) + "\n")
        cfg_path = tf.name
    rc = subprocess.run([str(BOOKSIM), cfg_path], cwd=ROOT, capture_output=True, text=True)
    row_csv = ROOT / "results" / "booksim_trace_row.csv"
    sim_mk = None
    stalls = None
    ok = rc.returncode == 0
    if row_csv.exists():
        lines = row_csv.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) >= 2:
            parts = lines[-1].split(",")
            if len(parts) >= 3:
                sim_mk = int(float(parts[2]))
            if len(parts) >= 6:
                stalls = int(float(parts[5]))
    return {
        "ok": ok,
        "returncode": rc.returncode,
        "sim_makespan": sim_mk,
        "expected_makespan": expected_mk,
        "buffer_full_stalls": stalls,
        "stdout_tail": rc.stdout[-800:] if rc.stdout else "",
        "stderr_tail": rc.stderr[-400:] if rc.stderr else "",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="max cases (0=all)")
    ap.add_argument("--scheme", default=None)
    ap.add_argument("--mode", choices=["hop", "tree", "both"], default="both")
    args = ap.parse_args()

    if not BOOKSIM.exists():
        raise SystemExit(f"Build BookSim first: make -C src  ({BOOKSIM} missing)")

    summary = json.loads((TRACE_DIR / "export_summary.json").read_text(encoding="utf-8"))
    cases = [c for c in summary if c.get("ok")]
    if args.scheme:
        cases = [c for c in cases if c["scheme"] == args.scheme]

    results = []
    for i, meta in enumerate(cases):
        if args.limit and i >= args.limit:
            break
        scheme = meta["scheme"]
        rb = meta["ramp_bw"]
        m = meta["m"]
        stem = TRACE_DIR / scheme / f"bw{rb}_m{m}"
        if not stem.with_suffix(".hop").exists():
            continue
        if args.mode in ("hop", "both"):
            r = run_one("hop", stem.with_suffix(".hop"), stem.with_suffix(".fork"),
                        meta["expected_makespan"], m, router="iq")
            r.update({"route": "A_hop", "scheme": scheme, "ramp_bw": rb, "m": m})
            results.append(r)
            print(f"A {scheme} bw={rb} m={m} ok={r['ok']} sim={r['sim_makespan']} exp={meta['expected_makespan']} stalls={r['buffer_full_stalls']}")
        if args.mode in ("tree", "both"):
            r = run_one("tree", stem.with_suffix(".tree"), stem.with_suffix(".fork"),
                        meta["expected_makespan"], m, router="fork_iq")
            r.update({"route": "B_tree", "scheme": scheme, "ramp_bw": rb, "m": m})
            results.append(r)
            print(f"B {scheme} bw={rb} m={m} ok={r['ok']} sim={r['sim_makespan']} exp={meta['expected_makespan']} stalls={r['buffer_full_stalls']}")

    OUT_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    if results:
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=sorted(results[0].keys()))
            w.writeheader()
            for row in results:
                w.writerow(row)
    print(f"Wrote {OUT_JSON} ({len(results)} rows)")


if __name__ == "__main__":
    main()
