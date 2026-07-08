#!/usr/bin/env python3
"""64x64 "certified zero-buffer" sweep, EXCLUDING multitree (see report sec.
3.5 -- confirmed to need 10000+ flit buffers there, not credible).

Method (rigorous, avoids the O(N^3 * m) rigid packer which is infeasible at
N=4096 -- extrapolated cost from measured 16x16/12x16 scaling: WEEKS):
allgather_fast_sim's event-driven engine reserves each hop at the next free
resource-cycle (FastCal). If, for a given (scheme, ramp_bw, m), EVERY single
reservation across the whole run lands exactly at its "ready" cycle (i.e.
max_link_wait == 0 AND max_ramp_wait == 0), then no implicit queuing was
used anywhere -- this is BY DEFINITION a valid zero-buffer, conflict-free,
non-blocking rigid schedule (the same guarantee sched_zerobuf_compare.py's
offset search targets, just witnessed directly by simulation instead of
searched for). When wait > 0 somewhere, the recorded makespan is NOT
zero-buffer-certified and is reported as such.

Usage: sharded manually across parallel processes via --shard i/n (see
run_shell commands in the session) to fit within a reasonable wall-clock
budget on a 16-core / ~30GB box; each shard writes its own partial JSON,
merged by --merge.
"""
import argparse
import json
import time
from pathlib import Path

import allgather_fast_sim as F

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "results" / "zerobuf64_shards"
OUT_JSON = ROOT / "results" / "zerobuf_64x64_witness.json"

MX, MY, H, V = 64, 64, 4, 6
FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]

# multitree deliberately excluded (see module docstring).
# B in {2,4}: at 4x4-32x32, hybrid(_v)_bi_B2 consistently won or tied among
# hybrid families, B4 is the nearest comparison point -- kept small so the
# 64x64 sweep (measured ~90s+ per single call, growing with m) is tractable.
CANDIDATES = [
    ("ring_uni", F.run_ring, (False,)),
    ("ring_bi", F.run_ring, (True,)),
]
# B=1 for hybrid_v is the "transposed ring" (vertical spine + horizontal
# comb teeth, cheaper than ring_bi's horizontal spine + vertical teeth since
# h<v here); hybrid_bi_B1/hybrid_uni_B1 are IDENTICAL to ring_bi/ring_uni
# (same ham_cycle_band(mx,my,0) construction) so are skipped as redundant.
for B in (2, 4):
    CANDIDATES.append((f"hybrid_uni_B{B}", F.run_hybrid, (B, False)))
    CANDIDATES.append((f"hybrid_bi_B{B}", F.run_hybrid, (B, True)))
    CANDIDATES.append((f"hybrid_v_uni_B{B}", F.run_hybrid_v, (B, False)))
    CANDIDATES.append((f"hybrid_v_bi_B{B}", F.run_hybrid_v, (B, True)))
CANDIDATES.append(("hybrid_v_uni_B1", F.run_hybrid_v, (1, False)))
CANDIDATES.append(("hybrid_v_bi_B1", F.run_hybrid_v, (1, True)))


def run_one(name, fn, extra, rb, m):
    t0 = time.time()
    mk, ok, bad, mlw, mrw = fn(MX, MY, H, V, rb, m, *extra)
    dt = time.time() - t0
    zero_buf = ok and mlw == 0 and mrw == 0
    return {"name": name, "ramp_bw": rb, "m": m, "makespan": mk, "ok": ok,
            "max_link_wait": mlw, "max_ramp_wait": mrw,
            "zero_buffer_certified": zero_buf, "time_s": round(dt, 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shard", default=None, help="i/n, e.g. 0/4")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--ms", default=None, help="comma list e.g. 3 or 1,2,4,5")
    ap.add_argument("--names", default=None, help="comma list of candidate names to restrict to")
    args = ap.parse_args()

    if args.merge:
        merged = []
        for f in sorted(OUT_DIR.glob("shard_*.json")):
            merged.extend(json.loads(f.read_text(encoding="utf-8")))
        OUT_JSON.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        print(f"Merged {len(merged)} records -> {OUT_JSON}")
        return

    ms = [int(x) for x in args.ms.split(",")] if args.ms else FLITS
    names = set(args.names.split(",")) if args.names else None
    cands = [c for c in CANDIDATES if names is None or c[0] in names]

    jobs = [(name, fn, extra, rb, m)
            for name, fn, extra in cands
            for rb in RAMP_BWS
            for m in ms]

    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        jobs = jobs[i::n]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag_bits = [args.shard.replace("/", "of") if args.shard else "all"]
    if args.ms:
        tag_bits.append(f"m{args.ms.replace(',', '_')}")
    if args.names:
        tag_bits.append("named")
    shard_tag = "_".join(tag_bits)
    out_path = OUT_DIR / f"shard_{shard_tag}.json"
    results = []
    for name, fn, extra, rb, m in jobs:
        r = run_one(name, fn, extra, rb, m)
        results.append(r)
        print(f"[64x64 bw={rb} m={m}] {name:18s} mk={r['makespan']:6d} "
              f"buf={r['max_link_wait']}/{r['max_ramp_wait']} "
              f"zero_buf={r['zero_buffer_certified']} ({r['time_s']:.0f}s)", flush=True)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Shard done: {len(results)} jobs -> {out_path}")


if __name__ == "__main__":
    main()
