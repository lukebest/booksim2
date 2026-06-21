#!/usr/bin/env python3
"""Scan per-quadrant Hamilton ring shape variants under 0 router-buffer scheduling.

Each quadrant can use a base comb orientation (horizontal / vertical / vflip)
plus rotation 0/90/180/270 degrees within the 8×8 tile.  For every 4-tuple of
shapes we run sched_ring_zerobuf.schedule() (strict ring_buf=0, eject_buf=0)
and record makespan + AFIFO depth.

Usage:
  python3 sweep_quad_ring_shapes.py              # full scan (256–20736 configs)
  python3 sweep_quad_ring_shapes.py --quick      # rect rotations only (256)
  python3 sweep_quad_ring_shapes.py --bases rect,vflip,vband
"""

import argparse
import itertools
import json
import time
from pathlib import Path

import sim_fused_rings as fr
import sched_ring_zerobuf as S

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "quad_ring_shape_sweep.json"

# Q0=左下, Q1=右下, Q2=左上, Q3=右上
QUAD_LABEL = ("Q0左下", "Q1右下", "Q2左上", "Q3右上")
ROT_LABEL = ("0°", "90°顺", "180°", "270°顺(90°逆)")


def rot_local(lx, ly, w, h, rot):
    if rot == 0:
        return lx, ly
    if rot == 1:                      # 90° CW
        return ly, w - 1 - lx
    if rot == 2:                      # 180°
        return w - 1 - lx, h - 1 - ly
    return h - 1 - ly, lx             # 270° CW = 90° CCW


def transform_order(order, x0, y0, w, h, rot):
    out = []
    for nd in order:
        x, y = fr.coord(nd)
        lx, ly = x - x0, y - y0
        nlx, nly = rot_local(lx, ly, w, h, rot)
        out.append(fr.nid(x0 + nlx, y0 + nly))
    return out


def base_order(base, x0, y0, w, h):
    if base == "rect":
        return fr.ham_cycle_rect(x0, y0, w, h)
    if base == "vflip":
        return fr.ham_cycle_rect_vflip(x0, y0, w, h)
    if base == "vband":
        order = [fr.nid(x0, y0 + y) for y in range(h)]
        for i, y in enumerate(range(h - 1, -1, -1)):
            cols = range(1, w) if i % 2 == 0 else range(w - 1, 0, -1)
            for xloc in cols:
                order.append(fr.nid(x0 + xloc, y0 + y))
        return order
    raise ValueError(base)


def quad_ham(base, rot, x0, y0, w, h):
    return transform_order(base_order(base, x0, y0, w, h), x0, y0, w, h, rot)


def validate_order(order, w, h):
    n = w * h
    if len(order) != n or len(set(order)) != n:
        return False
    pos = {nd: i for i, nd in enumerate(order)}
    for i, nd in enumerate(order):
        x, y = fr.coord(nd)
        nbrs = []
        if x > 0:
            nbrs.append(fr.nid(x - 1, y))
        if x < fr._MX - 1:
            nbrs.append(fr.nid(x + 1, y))
        if y > 0:
            nbrs.append(fr.nid(x, y - 1))
        if y < fr._MY - 1:
            nbrs.append(fr.nid(x, y + 1))
        nxt = order[(i + 1) % n]
        prv = order[(i - 1) % n]
        if nxt not in nbrs or prv not in nbrs:
            return False
    return True


def make_quads(cfg):
    """cfg: 4-tuple of (base, rot) for Q0..Q3."""
    fr.cfg(16, 16, 4, 6)
    hw, hh = 8, 8
    specs = [(0, 0), (hw, 0), (0, hh), (hw, hh)]
    reps = [(hw - 1, hh - 1), (hw, hh - 1), (hw - 1, hh), (hw, hh)]
    quads = []
    for (x0, y0), (rx, ry), (base, rot) in zip(specs, reps, cfg):
        order = quad_ham(base, rot, x0, y0, hw, hh)
        assert validate_order(order, hw, hh), f"invalid cycle {base} rot{rot} @({x0},{y0})"
        quads.append({"rep": fr.nid(rx, ry), "order": order})
    return quads


def cfg_str(cfg):
    parts = []
    for qi, (base, rot) in enumerate(cfg):
        parts.append(f"{QUAD_LABEL[qi]}:{base}+{ROT_LABEL[rot]}")
    return "; ".join(parts)


def cfg_short(cfg):
    return tuple((b, r) for b, r in cfg)


def run_one(cfg, scheme):
    quads = make_quads(cfg)
    if scheme == "border":
        deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
    else:
        deliv = lambda s, b, q=quads: S.deliv_ringfollow_quads(s, b, q)
    r = S.schedule(16, True, 2, deliv, spread=0, quads=quads)
    return r


def canonical_shapes(bases):
    """Deduplicate (base,rot) that yield the same node order on Q0."""
    fr.cfg(16, 16, 4, 6)
    seen = {}
    out = []
    for b in bases:
        for r in range(4):
            key = tuple(quad_ham(b, r, 0, 0, 8, 8))
            if key not in seen:
                seen[key] = (b, r)
                out.append((b, r))
    return out


def all_configs(bases):
    per_quad = canonical_shapes(bases)
    return list(itertools.product(per_quad, repeat=4))


def sweep(bases, schemes=("border", "ringfollow"), progress_every=64):
    cfgs = all_configs(bases)
    results = {sch: [] for sch in schemes}
    t0 = time.time()
    for i, cfg in enumerate(cfgs):
        for sch in schemes:
            r = run_one(cfg, sch)
            if r.get("ok"):
                results[sch].append({
                    "cfg": cfg_short(cfg),
                    "cfg_str": cfg_str(cfg),
                    "makespan": r["makespan"],
                    "afifo_depth": r["afifo_depth"],
                    "max_inject_off": r["max_inject_off"],
                })
        if (i + 1) % progress_every == 0:
            elapsed = time.time() - t0
            print(f"  ... {i + 1}/{len(cfgs)} ({elapsed:.1f}s)", flush=True)
    return results, len(cfgs), time.time() - t0


def report(results, n_cfgs, elapsed, bases):
    print(f"\nScanned {n_cfgs} shape configs ({bases}) in {elapsed:.1f}s\n")
    for sch in results:
        rows = sorted(results[sch], key=lambda x: (x["makespan"], x["afifo_depth"]))
        if not rows:
            print(f"=== {sch}: no feasible schedules ===")
            continue
        best = rows[0]
        print(f"=== {sch}: MIN makespan = {best['makespan']} cy "
              f"(AFIFO depth {best['afifo_depth']}) ===")
        print(f"    {best['cfg_str']}")
        print(f"    cfg={best['cfg']}")
        print("  Top 8:")
        for r in rows[:8]:
            print(f"    mk={r['makespan']:4d} afifo={r['afifo_depth']:2d}  {r['cfg_str']}")
        # Pareto: best makespan at each AFIFO depth
        by_af = {}
        for r in rows:
            d = r["afifo_depth"]
            if d not in by_af or r["makespan"] < by_af[d]["makespan"]:
                by_af[d] = r
        print("  Best makespan per AFIFO depth (first 12 depths):")
        for d in sorted(by_af)[:12]:
            r = by_af[d]
            print(f"    depth={d:2d} -> mk={r['makespan']:4d}  {r['cfg']}")
        print()


def user_example():
    """左下+左上 90°顺; 右下+右上 90°逆 — user-suggested pattern."""
    cfg = (("rect", 1), ("rect", 3), ("rect", 1), ("rect", 3))
    print("User example: Q0/Q2 CW90, Q1/Q3 CCW90")
    print(f"  {cfg_str(cfg)}")
    for sch in ("border", "ringfollow"):
        r = run_one(cfg, sch)
        print(f"  {sch:11s} mk={r['makespan']} afifo={r['afifo_depth']} ok={r['ok']}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="only ham_cycle_rect + 4 rotations (256 configs)")
    ap.add_argument("--bases", default="rect,vflip,vband",
                    help="comma-separated base types per quadrant")
    ap.add_argument("--example", action="store_true", help="run user example only")
    ap.add_argument("--json", action="store_true", help="write full results JSON")
    args = ap.parse_args()

    fr.cfg(16, 16, 4, 6)
    if args.example:
        user_example()
        return

    bases = ["rect"] if args.quick else [b.strip() for b in args.bases.split(",")]
    per_quad = canonical_shapes(bases)
    print("0 router-buffer ring shape sweep (16×16 bidir, ramp=2, spread=0)")
    print(f"Bases: {bases}  ->  {len(per_quad)} distinct shapes/quadrant")
    print(f"  {len(per_quad) ** 4} configs × 2 schemes")

    user_example()

    results, n_cfgs, elapsed = sweep(bases)
    report(results, n_cfgs, elapsed, bases)

    if args.json:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        payload = {"bases": bases, "n_configs": n_cfgs, "elapsed_s": elapsed,
                   "results": results}
        OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
