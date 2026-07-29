#!/usr/bin/env python3
"""Does allowing *more* sacrifice buy full scenario coverage?

`solve_scheme` stops at a minimum-cardinality recovery drawn from a small
candidate pool (12 singles, pairs of the first 8, prefixes k<=6, row/col
bundles, one rect fallback). Some schemes still come back INFEASIBLE:
M0s1 Super-turn 1VC and M5h half-ring in particular.

This probe asks the separate question: if the sacrifice budget is opened up,
does a legal (deadlock-free, order-preserving) table exist at all, and how many
good nodes does it cost? The escalation used here, in order:

  1. `solve_scheme` as-is (minimum sacrifice).
  2. Greedy grow: repeatedly commit the fault-nearest candidate, retrying the
     generator after each commit (budget K).
  3. Rect / row / column wipes: mask to the largest healthy rectangle, then to
     single surviving rows and columns (a 1xN or Nx1 line is trivially legal
     for every turn model), taking the largest that works.

Output: results/pg_full_cover.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pg_faults_budget_8x6 as B
import pg_routing as R
from pg_routing import MX, MY, coord, nid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_full_cover.json"

SCHEMES = ["updown", "super_turn", "super_turn_1vc", "fault_half_ring"]


def _attempt(pg: dict, scheme: str, sac: set[int]) -> dict | None:
    """Run the generator on the sacrificed view and finalize/validate."""
    remove_route = (scheme in ("rect_xy", "fault_ring_vc", "fault_half_ring")
                    or pg["semantics"] == "dead")
    view = R.apply_sacrifice(pg, sac, remove_route) if sac else pg
    if view["n_compute"] < 2:
        return None
    raw = R.SCHEME_GENERATORS[scheme](view)
    if raw is None:
        return None
    return R._finalize(view, raw, set(sac), remove_route)


def _lines(pg: dict) -> list[list[int]]:
    """Whole-row and whole-column keep-sets, largest first."""
    out = []
    for y in range(MY):
        keep = [n for n in pg["compute_nodes"] if coord(n)[1] == y]
        if len(keep) >= 2:
            out.append(keep)
    for x in range(MX):
        keep = [n for n in pg["compute_nodes"] if coord(n)[0] == x]
        if len(keep) >= 2:
            out.append(keep)
    return sorted(out, key=len, reverse=True)


def full_cover(pg: dict, scheme: str, k_max: int = 24) -> dict:
    """Escalate sacrifice until a legal table exists; report cost and stage."""
    base = R.solve_scheme(pg, scheme)
    if base["feasible"]:
        return {"stage": "solve_scheme", "n_sacrificed": base["n_sacrificed"],
                "A": base["n_compute_used"], "num_vc": base["num_vc"]}

    # 2. greedy grow over the ordered candidate pool
    cands = R.sacrifice_candidates(pg)
    sac: set[int] = {n for n in pg["compute_nodes"] if not pg["route_adj"].get(n)}
    for n in cands:
        if n in sac:
            continue
        sac.add(n)
        if len(sac) > k_max:
            break
        fin = _attempt(pg, scheme, sac)
        if fin is not None and fin["feasible"]:
            return {"stage": "greedy_grow", "n_sacrificed": fin["n_sacrificed"],
                    "A": fin["n_compute_used"], "num_vc": fin["num_vc"]}

    # 3. rect, then single rows / columns (largest that works)
    for keep in ([None] + _lines(pg)):
        if keep is None:
            fin = R._try_rect_recovery(pg, scheme, True)
            tag = "rect"
        else:
            drop = set(pg["compute_nodes"]) - set(keep)
            fin = _attempt(pg, scheme, drop)
            tag = "line"
        if fin is not None and fin["feasible"]:
            return {"stage": tag, "n_sacrificed": fin["n_sacrificed"],
                    "A": fin["n_compute_used"], "num_vc": fin["num_vc"]}

    return {"stage": "none", "n_sacrificed": None, "A": 0, "num_vc": 0}


def run(n_per_cell: int, seed: int, schemes: list[str]) -> dict:
    cat = B.write_catalog(n_per_cell=n_per_cell, seed=seed)
    scens = cat["scenarios"]
    out: dict[str, dict] = {}
    t0 = time.time()
    for sch in schemes:
        recs = {}
        n_solve = n_grow = n_coarse = n_none = 0
        print(f"=== {sch} ===", flush=True)
        for j, scen in enumerate(scens, 1):
            pg = B.expand_budget(scen, "dead")
            r = full_cover(pg, sch)
            st = r["stage"]
            if st != "solve_scheme":
                recs[scen["name"]] = r
            n_solve += st == "solve_scheme"
            n_grow += st == "greedy_grow"
            n_coarse += st in ("rect", "line")
            n_none += st == "none"
            if st != "solve_scheme":
                print(f"  {scen['name']:16s} {st:12s} "
                      f"sac={r['n_sacrificed']} A={r['A']}", flush=True)
            if j % 20 == 0:
                print(f"  …{j}/{len(scens)} {time.time() - t0:.0f}s", flush=True)
        sacs = sorted(r["n_sacrificed"] for r in recs.values()
                      if r["n_sacrificed"] is not None)
        out[sch] = {
            "n_scen": len(scens),
            "solve_scheme": n_solve, "greedy_grow": n_grow,
            "coarse": n_coarse, "infeasible": n_none,
            "full_cover": n_none == 0,
            "escalated": recs,
            "escalated_sac_med": sacs[len(sacs) // 2] if sacs else None,
            "escalated_sac_max": sacs[-1] if sacs else None,
        }
        print(f"{sch}: solve={n_solve} grow={n_grow} coarse={n_coarse} "
              f"none={n_none}", flush=True)
    return {"meta": {"n_per_cell": n_per_cell, "seed": seed,
                     "elapsed_s": round(time.time() - t0, 1)},
            "schemes": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-per-cell", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--schemes", default=",".join(SCHEMES))
    a = ap.parse_args()
    d = run(a.n_per_cell, a.seed, a.schemes.split(","))
    if OUT.exists():
        prev = json.loads(OUT.read_text())
        prev["schemes"].update(d["schemes"])
        prev["meta"] = d["meta"]
        d = prev
    OUT.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
