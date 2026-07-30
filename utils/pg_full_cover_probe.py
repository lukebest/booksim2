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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_full_cover.json"

SCHEMES = ["updown", "super_turn", "super_turn_1vc", "fault_half_ring"]


def full_cover(pg: dict, scheme: str, k_max: int = 24) -> dict:
    """Escalate sacrifice until a legal table exists; report cost and stage."""
    sol = R.solve_scheme_fc(pg, scheme, k_max=k_max)
    return {
        "stage": sol["fc_stage"],
        "n_sacrificed": sol["n_sacrificed"] if sol["feasible"] else None,
        "A": sol["n_compute_used"],
        "num_vc": sol.get("num_vc", 0),
    }


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
