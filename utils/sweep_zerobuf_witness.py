#!/usr/bin/env python3
"""Zero-buffer witness sweep: run ring/hybrid/hybrid_v candidates and record
max_link_wait/max_ramp_wait. A result with both == 0 is a certified rigid
zero-buffer schedule (see report sec 3.6).

Patches results/allgather_scale_sweep.json in-place (merges by name, keeps
multitree if present for reference but report ignores it).

Usage:
  python3 sweep_zerobuf_witness.py --size 16x16 --bw 1 --ms 2,3,4,5
  python3 sweep_zerobuf_witness.py --all-small          # 4x4..16x16 all m
  python3 sweep_zerobuf_witness.py --size 32x32 --bw 1 --ms 5 --shard 0/4
  python3 sweep_zerobuf_witness.py --size 64x64 --bw 1 --ms 3,4,5
"""
import argparse
import json
import time
from pathlib import Path

import allgather_fast_sim as F

ROOT = Path(__file__).resolve().parents[1]
SWEEP_JSON = ROOT / "results" / "allgather_scale_sweep.json"
STRICT_JSON = ROOT / "results" / "zerobuf_strict_m1.json"
H, V = 4, 6
SMALL = ["4x4", "6x8", "8x8", "12x16", "16x16"]
HUGE = ["32x32", "64x64"]

CANDIDATES = None  # use candidates_for(mx, my) per size


def run_one(mx, my, rb, m, name, fn, extra):
    t0 = time.time()
    mk, ok, bad, mlw, mrw = fn(mx, my, H, V, rb, m, *extra)
    zc = ok and mlw == 0 and mrw == 0
    return {
        "name": name, "makespan": mk, "ok": ok, "bad": bad,
        "max_link_wait": mlw, "max_ramp_wait": mrw,
        "zero_buffer_certified": zc, "time_s": round(time.time() - t0, 1),
    }


def candidates_for(mx, my):
    cands = [
        ("ring_uni", F.run_ring, (False,)),
        ("ring_bi", F.run_ring, (True,)),
    ]
    for B in (1, 2, 4, 8):
        if my // B >= 2:
            cands.append((f"hybrid_uni_B{B}", F.run_hybrid, (B, False)))
            cands.append((f"hybrid_bi_B{B}", F.run_hybrid, (B, True)))
        if mx // B >= 2:
            cands.append((f"hybrid_v_uni_B{B}", F.run_hybrid_v, (B, False)))
            cands.append((f"hybrid_v_bi_B{B}", F.run_hybrid_v, (B, True)))
    return cands


def merge_cell(cell, new_results, mx, my, rb, m):
    by_name = {r["name"]: r for r in cell.get("results", [])}
    for r in new_results:
        by_name[r["name"]] = {
            "name": r["name"], "makespan": r["makespan"], "ok": r["ok"], "bad": r["bad"],
            "max_link_wait": r["max_link_wait"], "max_ramp_wait": r["max_ramp_wait"],
        }
    results = list(by_name.values())
    # m=1 small: inject strict packer truth for schemes we can reproduce
    if m == 1 and STRICT_JSON.exists():
        strict = json.loads(STRICT_JSON.read_text(encoding="utf-8"))
        block = strict["data"].get(f"{mx}x{my}", {}).get("bw", {}).get(str(rb))
        if block:
            for sr in block["results"]:
                if sr["name"] not in by_name:
                    continue
                by_name[sr["name"]]["makespan"] = sr["makespan"]
                by_name[sr["name"]]["max_link_wait"] = 0
                by_name[sr["name"]]["max_ramp_wait"] = 0
            results = list(by_name.values())
            cell["best_zero_buffer"] = block["best"]
    zc = [r for r in results if r.get("max_link_wait") == 0 and r.get("max_ramp_wait") == 0]
    best_zc = min(zc, key=lambda r: r["makespan"]) if zc else None
    cell["results"] = results
    cell["best"] = min(results, key=lambda r: r["makespan"])  # raw fastest (incl multitree)
    cell["best_zero_buffer"] = best_zc
    cell["note"] = "zero-buffer witness sweep; heatmap uses best_zero_buffer"
    return cell


def sweep_size(size, rb, ms, shard=None, exclude_multitree=False):
    mx, my = (int(x) for x in size.split("x"))
    sweep = json.loads(SWEEP_JSON.read_text(encoding="utf-8"))
    block = sweep["data"].setdefault(size, {"huge": size in HUGE, "bw": {}})
    jobs = [(name, fn, extra, m) for name, fn, extra in candidates_for(mx, my)
            for m in ms if not (exclude_multitree and name == "multitree")]
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        jobs = jobs[i::n]
    for name, fn, extra, m in jobs:
        r = run_one(mx, my, rb, m, name, fn, extra)
        cell = block["bw"].setdefault(str(rb), {}).setdefault(str(m), {"T": None})
        if cell["T"] is None:
            lb = json.loads((ROOT / "results" / "allgather_lb.json").read_text())["data"][size]["bw"][str(rb)][str(m)]["T"]
            cell["T"] = lb
        existing = cell.get("results", [])
        others = [x for x in existing if x["name"] != name]
        merge_cell(cell, [r] + others, mx, my, rb, m)
        z = "ZC" if r["zero_buffer_certified"] else "  "
        print(f"[{size} bw={rb} m={m}] {name:18s} mk={r['makespan']:5d} buf={r['max_link_wait']}/{r['max_ramp_wait']} {z} ({r['time_s']:.0f}s)", flush=True)
        SWEEP_JSON.write_text(json.dumps(sweep, indent=2), encoding="utf-8")
    return len(jobs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--size", action="append", default=[])
    ap.add_argument("--bw", type=int, action="append", default=[])
    ap.add_argument("--ms", default="1,2,3,4,5")
    ap.add_argument("--shard", default=None)
    ap.add_argument("--all-small", action="store_true")
    ap.add_argument("--no-multitree", action="store_true", help="for 64x64")
    args = ap.parse_args()
    sizes = SMALL if args.all_small else (args.size or SMALL + HUGE)
    bws = args.bw or [1, 2]
    ms = [int(x) for x in args.ms.split(",")]
    total = 0
    for size in sizes:
        for rb in bws:
            total += sweep_size(size, rb, ms, args.shard,
                                exclude_multitree=args.no_multitree or size == "64x64")
    print(f"Done: {total} sim calls, sweep -> {SWEEP_JSON}")


if __name__ == "__main__":
    main()
