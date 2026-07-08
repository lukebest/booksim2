#!/usr/bin/env python3
"""Run BookSim2 trace sweep for 6x8 allgather (Route B tree + fork by default)."""

import argparse
import csv
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOKSIM = ROOT / "src" / "booksim"
BASE_CONFIG = ROOT / "runfiles" / "traceconfig"
TRACE_DIR_DEFAULT = ROOT / "results" / "traces" / "6x8"
OUT_CSV = ROOT / "results" / "booksim_trace_sweep.csv"
OUT_JSON = ROOT / "results" / "booksim_trace_sweep.json"
OUT_CSV_ZBUF = ROOT / "results" / "booksim_zbuf_6x8_sweep.csv"
OUT_JSON_ZBUF = ROOT / "results" / "booksim_zbuf_6x8_sweep.json"


def run_one(trace_mode, trace_file, fork_file, expected_mk, m, router="iq", meta_scheme=""):
    if trace_mode == "tree" and fork_file:
        fork_cfg = str(fork_file.relative_to(ROOT))
    else:
        fork_cfg = "none"
    cfg_lines = BASE_CONFIG.read_text(encoding="utf-8").splitlines()
    overrides = {
        "trace_mode": trace_mode,
        "trace_file": str(trace_file.relative_to(ROOT)),
        "fork_file": fork_cfg,
        "expected_makespan": str(expected_mk),
        "msg_size": str(m),
        "router": router,
        "result_csv": "results/booksim_trace_row.csv",
        "trace_drain_slack": str(512 + m * 256),
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
    total_rec = None
    total_exp = None
    ok = rc.returncode == 0
    if row_csv.exists():
        lines = row_csv.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) >= 2:
            parts = lines[-1].split(",")
            if len(parts) >= 3:
                sim_mk = int(float(parts[2]))
            if len(parts) >= 4:
                total_rec = int(float(parts[3]))
            if len(parts) >= 5:
                total_exp = int(float(parts[4]))
            if len(parts) >= 6:
                stalls = int(float(parts[5]))
            if total_rec is not None and total_exp is not None:
                ok = total_rec >= total_exp
    return {
        "ok": ok,
        "returncode": rc.returncode,
        "sim_makespan": sim_mk,
        "expected_makespan": expected_mk,
        "total_received": total_rec,
        "total_expected": total_exp,
        "buffer_full_stalls": stalls,
        "stdout_tail": rc.stdout[-800:] if rc.stdout else "",
        "stderr_tail": rc.stderr[-400:] if rc.stderr else "",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="max cases (0=all)")
    ap.add_argument("--scheme", default=None)
    ap.add_argument("--mode", choices=["hop", "tree", "both"], default="tree")
    ap.add_argument("--trace-dir", default=None, help="trace root (default: results/traces/6x8)")
    ap.add_argument("--zbuf-report", action="store_true",
                    help="6x8 zero-buffer report traces -> booksim_zbuf_6x8_sweep.json (Route B only)")
    args = ap.parse_args()

    if not BOOKSIM.exists():
        raise SystemExit(f"Build BookSim first: make -C src  ({BOOKSIM} missing)")

    trace_dir = Path(args.trace_dir) if args.trace_dir else TRACE_DIR_DEFAULT
    if args.zbuf_report:
        trace_dir = ROOT / "results" / "traces" / "6x8_zbuf"
        args.mode = "tree"
    out_json = OUT_JSON_ZBUF if args.zbuf_report else OUT_JSON
    out_csv = OUT_CSV_ZBUF if args.zbuf_report else OUT_CSV

    summary = json.loads((trace_dir / "export_summary.json").read_text(encoding="utf-8"))
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
        stem = trace_dir / scheme / f"bw{rb}_m{m}"
        if not stem.with_suffix(".tree").exists() or not stem.with_suffix(".fork").exists():
            continue
        if args.mode in ("hop", "both"):
            if not stem.with_suffix(".hop").exists():
                continue
            r = run_one("hop", stem.with_suffix(".hop"), stem.with_suffix(".fork"),
                        meta["expected_makespan"], m, router="iq", meta_scheme=scheme)
            r.update({"route": "A_hop", "scheme": scheme, "ramp_bw": rb, "m": m})
            results.append(r)
            print(f"A {scheme} bw={rb} m={m} ok={r['ok']} sim={r['sim_makespan']} exp={meta['expected_makespan']} stalls={r['buffer_full_stalls']}")
        if args.mode in ("tree", "both"):
            r = run_one("tree", stem.with_suffix(".tree"), stem.with_suffix(".fork"),
                        meta["expected_makespan"], m, router="iq", meta_scheme=scheme)
            r.update({"route": "B_tree", "scheme": scheme, "ramp_bw": rb, "m": m})
            results.append(r)
            print(f"B {scheme} bw={rb} m={m} ok={r['ok']} sim={r['sim_makespan']} exp={meta['expected_makespan']} stalls={r['buffer_full_stalls']}")

    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    if results:
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=sorted(results[0].keys()))
            w.writeheader()
            for row in results:
                w.writerow(row)
    print(f"Wrote {out_json} ({len(results)} rows)")


if __name__ == "__main__":
    main()
