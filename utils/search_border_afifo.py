#!/usr/bin/env python3
"""Search minimum allgather makespan for border 4-ring + AFIFO model.

Constraints (per design spec):
  * Quadrant Hamilton rings: 0 router buffer, conflict-free, non-blocking.
  * Cross-border links: AFIFO depth <= AFIFO_CAP (default 5).
  * Multi-point border injection + short arcs; diagonal via intermediate quadrant.
  * Down-ramp BW: uni ring @ ramp=1, bi ring @ ramp=2.
  * Load-balanced AFIFO usage across parallel border links.

Two schedulers:
  * strict  — sched_ring_zerobuf.schedule (ring_buf=0 by construction)
  * pipelined — sim_fused_rings.simulate_afifo (link pipelining; ring_buf may >0)

Results persist to results/border_afifo_search.json for /loop iterations.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import sim_fused_rings as fr
import sched_ring_zerobuf as S
from sweep_quad_ring_shapes import canonical_shapes, make_quads

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "border_afifo_search.json"
SIZES = (4, 8, 16)
AFIFO_CAP = 5


def eject_lb(n, ramp_bw):
    return (n - 1 + ramp_bw - 1) // ramp_bw


def try_schedule(sz, bidir, ramp_bw, deliv_fn, spread, lb_cross, quads=None):
    r = S.schedule(sz, bidir, ramp_bw, deliv_fn, spread=spread,
                   lb_cross=lb_cross, quads=quads)
    if not r.get("ok"):
        return None
    bal = r["afifo_balanced"]["peak"]
    return {
        "makespan": r["makespan"],
        "afifo_depth": r["afifo_depth"],
        "afifo_balanced": bal,
        "spread": spread,
        "lb_cross": lb_cross,
        "scheduler": "strict",
    }


def try_atomic(sz, bidir, ramp_bw, deliv_fn, order, quads=None):
    r = S.schedule_atomic(sz, bidir, ramp_bw, deliv_fn, afifo_cap=AFIFO_CAP,
                          order=order)
    if not r.get("ok"):
        return None
    return {
        "makespan": r["makespan"],
        "afifo_depth": r["afifo_depth"],
        "afifo_balanced": r["afifo_balanced"]["peak"],
        "order": order,
        "scheduler": "atomic",
    }


def try_pipelined(sz, bidir, ramp_bw, deliv_builder):
    fr.cfg(sz, sz, 4, 6)
    n = sz * sz
    deliveries = {s: deliv_builder(s, bidir) for s in range(n)}
    r = fr.simulate_afifo(deliveries, ramp_bw)
    return {
        "makespan": r["makespan"],
        "ring_buf": r["ring_buf"],
        "afifo_depth": r["afifo_buf"],
        "eject_buf": r["eject_buf"],
        "scheduler": "pipelined",
    }


def feasible(rec, cap=AFIFO_CAP, mode="balanced"):
    """balanced: load-balanced AFIFO peak <= cap; per_link: any single link <= cap."""
    if rec is None:
        return False
    if rec["scheduler"] == "pipelined":
        return rec.get("ring_buf", 0) == 0 and rec.get("afifo_depth", 99) <= cap
    if mode == "per_link":
        return rec.get("afifo_depth", 99) <= cap
    return rec.get("afifo_balanced", 99) <= cap


def better(a, b):
    """True if a is strictly better than b (lower makespan)."""
    if b is None:
        return True
    return a["makespan"] < b["makespan"]


def search_config(sz, bidir, ramp_bw, deliv_fn, deliv_builder, quads=None, deep=False):
    n = sz * sz
    lb = eject_lb(n, ramp_bw)
    best = {"strict_any": None, "strict_balanced": None, "strict_per_link": None,
            "pipelined": None, "pipelined_feasible": None}

    spread_max = 60 if deep else 25
    for sp in range(spread_max):
        for lb_cross in (False, True):
            rec = try_schedule(sz, bidir, ramp_bw, deliv_fn, sp, lb_cross, quads)
            if rec is None:
                continue
            if better(rec, best["strict_any"]):
                best["strict_any"] = rec
            if feasible(rec, mode="balanced") and better(rec, best["strict_balanced"]):
                best["strict_balanced"] = rec
            if feasible(rec, mode="per_link") and better(rec, best["strict_per_link"]):
                best["strict_per_link"] = rec

    for order in ("interleave", "natural", "quad"):
        rec = try_atomic(sz, bidir, ramp_bw, deliv_fn, order, quads)
        if rec is None:
            continue
        if better(rec, best["strict_any"]):
            best["strict_any"] = rec
        if feasible(rec, mode="balanced") and better(rec, best["strict_balanced"]):
            best["strict_balanced"] = rec
        if feasible(rec, mode="per_link") and better(rec, best["strict_per_link"]):
            best["strict_per_link"] = rec

    pip = try_pipelined(sz, bidir, ramp_bw, deliv_builder)
    best["pipelined"] = pip
    if feasible(pip):
        best["pipelined_feasible"] = pip

    return {"size": sz, "bidir": bidir, "ramp_bw": ramp_bw, "n": n, "eject_lb": lb,
            **best}


def search_border(sz, deep=False):
    deliv = S.deliv_border
    builder = fr.build_border_delivery
    return search_config(sz, False, 1, deliv, builder, deep=deep), \
           search_config(sz, True, 2, deliv, builder, deep=deep)


def search_shapes(sz, bidir, ramp_bw, bases=("rect", "vflip", "vband"), limit=64):
    """Sample uniform 4-quad shape combos (16x16 only; make_quads is fixed to 16)."""
    if sz != 16:
        return []
    fr.cfg(sz, sz, 4, 6)
    shapes = canonical_shapes(list(bases))
    results = []
    for shape in shapes:
        cfg = (shape, shape, shape, shape)
        quads = make_quads(cfg)
        deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
        builder = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
        r = search_config(sz, bidir, ramp_bw, deliv, builder, quads=quads)
        fe = r.get("strict_balanced")
        if fe:
            results.append({"cfg": cfg, "makespan": fe["makespan"],
                            "afifo_depth": fe["afifo_depth"],
                            "afifo_balanced": fe["afifo_balanced"],
                            "detail": fe})
    results.sort(key=lambda x: x["makespan"])
    return results[:limit]


def run_iteration(deep=False, shape_search=False):
    t0 = time.time()
    out = {"updated": datetime.now(timezone.utc).isoformat(),
           "afifo_cap": AFIFO_CAP, "configs": {}}

    for sz in SIZES:
        uni, bi = search_border(sz, deep=deep)
        out["configs"][f"{sz}x{sz}"] = {"uni": uni, "bi": bi}
        if shape_search and sz == 16:
            out["configs"][f"{sz}x{sz}"]["bi_shapes"] = search_shapes(sz, True, 2)
            out["configs"][f"{sz}x{sz}"]["uni_shapes"] = search_shapes(sz, False, 1)

    out["elapsed_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    prev = {}
    if OUT.exists():
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    out["history"] = prev.get("history", [])
    # record improvements
    improvements = []
    for key in out["configs"]:
        for mode in ("uni", "bi"):
            cur = out["configs"][key][mode]
            old = prev.get("configs", {}).get(key, {}).get(mode, {})
            for field in ("strict_balanced", "strict_any", "strict_per_link", "pipelined_feasible"):
                c = cur.get(field)
                o = old.get(field)
                if c and (not o or c["makespan"] < o.get("makespan", 1 << 30)):
                    improvements.append(f"{key} {mode} {field}: {o and o.get('makespan')} -> {c['makespan']}")
    out["improvements"] = improvements
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def print_summary(data):
    print(f"AFIFO cap={data['afifo_cap']}  elapsed={data['elapsed_s']:.1f}s")
    if data.get("improvements"):
        print("Improvements:", ", ".join(data["improvements"]))
    hdr = (f"{'size':>8s} {'cfg':>4s} {'eject_lb':>8s} "
           f"{'strict_bal':>10s} {'strict_any':>10s} {'pipe':>6s}")
    print(hdr)
    for key in sorted(data["configs"].keys(), key=lambda k: int(k.split("x")[0])):
        for mode, tag in (("uni", "uni"), ("bi", "bi")):
            c = data["configs"][key][mode]
            sb = c.get("strict_balanced")
            sa = c.get("strict_any")
            pp = c.get("pipelined")
            def fmt(x):
                return f"{x['makespan']:>10d}" if x else f"{'—':>10s}"
            print(f"{key:>8s} {tag:>4s} {c['eject_lb']:8d} "
                  f"{fmt(sb)} {fmt(sa)} {fmt(pp)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true", help="wider spread sweep")
    ap.add_argument("--shapes", action="store_true", help="scan ring shape variants")
    args = ap.parse_args()
    data = run_iteration(deep=args.deep, shape_search=args.shapes)
    print_summary(data)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
