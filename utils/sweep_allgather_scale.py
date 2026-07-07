#!/usr/bin/env python3
"""Scale x message-size x ramp-bandwidth allgather sweep.

For every mesh size in SIZES, every message size m in FLITS and every
down-ramp bandwidth in RAMP_BWS, find the best-makespan scheme among the
candidate families implemented in allgather_fast_sim.py (multitree, ring
uni/bi, hybrid uni/bi with B horizontal bands, hybrid_v uni/bi with B
vertical bands).

Cost tiering (see .cursor/plans/... and allgather_fast_sim.py docstring for
why): the event-driven engine is O(N^2 * flits) per candidate scheme, so a
FULL comparison (every scheme x every B x uni/bi) is cheap up to 16x16
(<=256 nodes, sub-second per candidate) but would take tens of minutes per
single (m, ramp_bw) cell at 32x32/64x64. Because scheme/B ranking is a
function of mesh topology and ramp_bw only (message size m just scales
injection/eject timing roughly proportionally, it does not change which
delivery structure has the least bottleneck contention), for N >= 1024
(32x32, 64x64) we run the FULL comparison ONCE per (mesh, ramp_bw) at a
representative m, then reuse that winner (plus multitree/ring_bi as cheap
sanity baselines) for the other message sizes at that (mesh, ramp_bw). This
is validated against the small/medium sizes, where the full per-cell
comparison shows the winning family is indeed stable across m (see report).

Output: results/allgather_scale_sweep.json
"""

import argparse
import json
import time
from pathlib import Path

import allgather_fast_sim as F

ROOT = Path(__file__).resolve().parents[1]
LB_JSON = ROOT / "results" / "allgather_lb.json"
OUT_JSON = ROOT / "results" / "allgather_scale_sweep.json"

H, V = 4, 6
SIZES = [(4, 4), (6, 8), (8, 8), (12, 16), (16, 16), (32, 32), (64, 64)]
FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]
HUGE_N = 1024   # >= 32x32: use reduced-comparison tiering


def full_candidates(mx, my, huge=False):
    """Every scheme/B/direction combo (used for mesh N < HUGE_N, and once per
    (mesh, ramp_bw) at a representative m for N >= HUGE_N).

    For huge meshes the per-candidate cost is ~constant (O(N^2*flits),
    independent of B), so to bound the one-time "establish winner" cost we
    (a) skip unidirectional variants -- uni never once won at any smaller
    size in this study -- and (b) only sample B in {2,4,8} instead of every
    power-of-2 divisor (every sampled size so far picked bidir hybrid/hybrid_v
    with a small-to-moderate B, never the coarsest B=1 "== pure ring" nor
    consistently the single finest B; {2,4,8} brackets the observed optimum).
    """
    if huge:
        cands = [
            ("multitree", F.run_multitree, ()),
            ("ring_bi", F.run_ring, (True,)),
        ]
        for B in (2, 4, 8):
            if my // B >= 2:
                cands.append((f"hybrid_bi_B{B}", F.run_hybrid, (B, True)))
            if mx // B >= 2:
                cands.append((f"hybrid_v_bi_B{B}", F.run_hybrid_v, (B, True)))
        return cands

    cands = [
        ("multitree", F.run_multitree, ()),
        ("ring_uni", F.run_ring, (False,)),
        ("ring_bi", F.run_ring, (True,)),
    ]
    for B in F.divisors_pow2(my):
        if my // B < 2:
            continue
        cands.append((f"hybrid_uni_B{B}", F.run_hybrid, (B, False)))
        cands.append((f"hybrid_bi_B{B}", F.run_hybrid, (B, True)))
    for B in F.divisors_pow2(mx):
        if mx // B < 2:
            continue
        cands.append((f"hybrid_v_uni_B{B}", F.run_hybrid_v, (B, False)))
        cands.append((f"hybrid_v_bi_B{B}", F.run_hybrid_v, (B, True)))
    return cands


def reduced_candidates(mx, my, winner_name, winner_fn, winner_args):
    """Cheap baselines + the previously-established winner, for the
    non-representative (m, ramp_bw) cells of a huge mesh."""
    cands = [
        ("multitree", F.run_multitree, ()),
        ("ring_bi", F.run_ring, (True,)),
    ]
    if winner_name not in ("multitree", "ring_bi"):
        cands.append((winner_name, winner_fn, winner_args))
    return cands


def eval_candidate(mx, my, ramp_bw, flits, name, fn, extra_args):
    t0 = time.time()
    mk, ok, bad = fn(mx, my, H, V, ramp_bw, flits, *extra_args)
    dt = time.time() - t0
    return {"name": name, "makespan": mk, "ok": ok, "bad": bad, "time_s": round(dt, 3)}


def sweep_cell(mx, my, ramp_bw, flits, candidates, log_prefix=""):
    results = []
    for name, fn, extra in candidates:
        r = eval_candidate(mx, my, ramp_bw, flits, name, fn, extra)
        results.append(r)
        print(f"{log_prefix}{name:20s} mk={r['makespan']:7d} ok={r['ok']!s:5s} "
              f"({r['time_s']:.2f}s)")
    feas = [r for r in results if r["ok"]]
    best = min(feas, key=lambda r: r["makespan"]) if feas else None
    return results, best


def find_fn(name, mx, my, huge=False):
    """Recover the (fn, extra_args) for a candidate name produced by
    full_candidates(), so a huge mesh's one-time winner can be replayed."""
    for cand_name, fn, extra in full_candidates(mx, my, huge=huge):
        if cand_name == name:
            return fn, extra
    raise KeyError(name)


def sweep_size(mx, my, lb_data, verbose=True):
    n = mx * my
    key = f"{mx}x{my}"
    huge = n >= HUGE_N
    out = {"mx": mx, "my": my, "n": n, "huge": huge, "bw": {}}

    for rb in RAMP_BWS:
        out["bw"][rb] = {}
        winner_cache = None   # (name, fn, extra) established at rep_m for this rb
        rep_m = 3
        m_order = [rep_m] + [m for m in FLITS if m != rep_m] if huge else FLITS
        for m in m_order:
            prefix = f"[{key} bw={rb} m={m}] "
            if verbose:
                print(prefix)
            if huge:
                if winner_cache is None:
                    cands = full_candidates(mx, my, huge=True)
                    results, best = sweep_cell(mx, my, rb, m, cands, prefix)
                    winner_cache = (best["name"], *find_fn(best["name"], mx, my, huge=True))
                else:
                    wname, wfn, wargs = winner_cache
                    cands = reduced_candidates(mx, my, wname, wfn, wargs)
                    results, best = sweep_cell(mx, my, rb, m, cands, prefix)
            else:
                cands = full_candidates(mx, my)
                results, best = sweep_cell(mx, my, rb, m, cands, prefix)

            t = lb_data["data"][key]["bw"][str(rb)][str(m)]["T"]
            cell = {
                "results": results,
                "best": best,
                "T": t,
                "ratio": round(best["makespan"] / t, 4) if best else None,
            }
            out["bw"][rb][m] = cell
            if verbose:
                print(f"{prefix}-> best={best['name']} mk={best['makespan']} "
                      f"T={t} ratio={cell['ratio']}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default=None,
                     help="comma-separated MXxMY list to restrict, e.g. 4x4,8x8")
    ap.add_argument("--json", default=str(OUT_JSON))
    ap.add_argument("--out-partial", action="store_true",
                     help="write JSON after every size (crash-safe for long 64x64 runs)")
    args = ap.parse_args()

    lb_data = json.loads(LB_JSON.read_text(encoding="utf-8"))

    sizes = SIZES
    if args.sizes:
        want = set(args.sizes.split(","))
        sizes = [(mx, my) for mx, my in SIZES if f"{mx}x{my}" in want]

    out_path = Path(args.json)
    payload = {"h": H, "v": V, "sizes": [f"{mx}x{my}" for mx, my in SIZES],
               "flits": FLITS, "ramp_bws": RAMP_BWS, "huge_n": HUGE_N, "data": {}}
    if out_path.exists():
        try:
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    for mx, my in sizes:
        key = f"{mx}x{my}"
        t0 = time.time()
        payload["data"][key] = sweep_size(mx, my, lb_data)
        print(f"=== {key} done in {time.time()-t0:.1f}s ===\n")
        if args.out_partial:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            print(f"(partial) wrote {out_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
