#!/usr/bin/env python3
"""E2E time × area Pareto under the budget fault model (≤4R / ≤8L).

Drops the fixed 36-catalogue. Schemes: retained baselines + super_turn.
A scheme must cover every sampled scenario (after sacrifice recovery) to enter
the summary / Pareto — same rule as dse_pg_e2e_pareto.py.

  python3 utils/dse_pg_budget_pareto.py --quick
  python3 utils/dse_pg_budget_pareto.py
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path

import dse_pg_alltoall_8x6 as D
import pg_faults_budget_8x6 as B
import ppa_analytic_model as PPA

FREQ_GHZ = 1.5
PE_MACS_PER_CYCLE = 8 * 64 * 16
D_MODEL, D_FF, ELEM_BYTES = 64, 256, 2
FLIT_BYTES = 64
TOKEN_BYTES = D_MODEL * ELEM_BYTES
CYCLES_PER_TOKEN = (2 * D_MODEL * D_FF) / PE_MACS_PER_CYCLE

A_FULL = 48
M0_LIST = [1, 13]
# Lean set: keep Pareto-relevant baselines + the new scheme. Exclude known
# hard-property failures (xy/rect/segment) that only look fast via sacrifice.
SCHEMES = [
    "east_first", "super_turn",
    "updown", "lash", "stripe_vc", "dual_updown", "virtual_mesh",
    "fault_ring_vc",
]
SEMANTICS = "dead"
Q = D.DEFAULT_Q
A_FLIT = PPA.ARCH_A3_BUFFERS / PPA.ARCH_A3_INTERIOR_FLITS
PORTS = 5

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_budget_e2e_pareto.json"


def total_tokens(m0: int) -> float:
    return A_FULL * A_FULL * m0 * FLIT_BYTES / TOKEN_BYTES


def m_effective(a: int, m0: int) -> int:
    return max(1, math.ceil(m0 * (A_FULL / a) ** 2))


def compute_cycles(a: int, m0: int) -> int:
    return math.ceil(CYCLES_PER_TOKEN * total_tokens(m0) / a)


def router_area(num_vc: int) -> float:
    return PPA.BASELINE_CROSSBAR + PPA.BASELINE_CONTROL + PORTS * num_vc * Q * A_FLIT


def pareto(points, xk, yk):
    out = []
    for p in points:
        if not any(o is not p and o[xk] <= p[xk] and o[yk] <= p[yk]
                   and (o[xk] < p[xk] or o[yk] < p[yk]) for o in points):
            out.append(p)
    return sorted(out, key=lambda p: p[xk])


def run(quick: bool = False, seed: int = 0) -> dict:
    n_per = 1 if quick else 4
    cat = B.write_catalog(n_per_cell=n_per, seed=seed)
    scenarios = cat["scenarios"]
    # Register super_turn in the DES scheme list cache if needed
    if "super_turn" not in D.SCHEMES:
        D.SCHEMES = list(D.SCHEMES) + ["super_turn"]

    rows = []
    t0 = time.time()
    total = len(scenarios) * len(SCHEMES) * len(M0_LIST)
    i = 0
    for scen in scenarios:
        pg = B.expand_budget(scen, SEMANTICS)
        for sch in SCHEMES:
            for m0 in M0_LIST:
                i += 1
                base = D.get_solution(pg, sch)
                if not base["feasible"]:
                    print(f"[{i}/{total}] {scen['name']:22s} {sch:16s} "
                          f"m0={m0:2d} -> INFEASIBLE", flush=True)
                    continue
                a = base["n_compute_used"]
                me = m_effective(a, m0)
                rec = D.run_one(pg, sch, me, Q)
                if not rec["feasible"] or rec["makespan"] is None:
                    print(f"[{i}/{total}] {scen['name']:22s} {sch:16s} "
                          f"m0={m0:2d} -> {rec.get('reason')}", flush=True)
                    continue
                t_comp = compute_cycles(a, m0)
                t_comm = rec["makespan"]
                t_tot = t_comp + t_comm
                rows.append({
                    "scenario": scen["name"],
                    "n_routers": scen["n_routers"],
                    "n_links": scen["n_links"],
                    "scheme": sch,
                    "m0": m0,
                    "m_eff": me,
                    "A": a,
                    "n_sacrificed": rec["n_sacrificed"],
                    "num_vc": rec["num_vc"],
                    "t_compute_cy": t_comp,
                    "t_alltoall_cy": t_comm,
                    "t_e2e_cy": t_tot,
                    "t_e2e_ns": t_tot / FREQ_GHZ,
                    "comm_frac": t_comm / t_tot,
                    "turn_mode": base.get("turn_mode"),
                    "turn_vc": base.get("turn_vc"),
                })
                if i % 20 == 0 or sch == "super_turn":
                    print(f"[{i}/{total}] {scen['name']:22s} {sch:16s} "
                          f"m0={m0:2d} A={a:2d} vc={rec['num_vc']} "
                          f"e2e={t_tot / FREQ_GHZ:8.1f}ns", flush=True)

    vc_req: dict[str, int] = defaultdict(int)
    for r in rows:
        vc_req[r["scheme"]] = max(vc_req[r["scheme"]], r["num_vc"])

    summary = []
    for m0 in M0_LIST:
        for sch in SCHEMES:
            sel = [r for r in rows if r["scheme"] == sch and r["m0"] == m0]
            if len(sel) < len(scenarios):
                print(f"  skip summary {sch} m0={m0}: "
                      f"{len(sel)}/{len(scenarios)} covered", flush=True)
                continue
            ts = sorted(r["t_e2e_ns"] for r in sel)
            summary.append({
                "scheme": sch,
                "m0": m0,
                "num_vc": vc_req[sch],
                "area": round(router_area(vc_req[sch]), 4),
                "n_scen": len(sel),
                "t_e2e_ns_med": round(ts[len(ts) // 2], 1),
                "t_e2e_ns_worst": round(ts[-1], 1),
                "t_e2e_ns_best": round(ts[0], 1),
                "A_med": sorted(r["A"] for r in sel)[len(sel) // 2],
                "A_worst": min(r["A"] for r in sel),
                "sac_med": sorted(r["n_sacrificed"] for r in sel)[len(sel) // 2],
                "comm_frac_med": round(
                    sorted(r["comm_frac"] for r in sel)[len(sel) // 2], 3),
            })

    for m0 in M0_LIST:
        cand = [s for s in summary if s["m0"] == m0]
        fw = {s["scheme"] for s in pareto(cand, "area", "t_e2e_ns_worst")}
        fm = {s["scheme"] for s in pareto(cand, "area", "t_e2e_ns_med")}
        for s in cand:
            s["pareto_worst"] = s["scheme"] in fw
            s["pareto_med"] = s["scheme"] in fm

    return {
        "meta": {
            "fault_model": "budget_≤4R_≤8L",
            "catalog": cat["meta"],
            "freq_ghz": FREQ_GHZ,
            "semantics": SEMANTICS, "Q": Q,
            "m0_list": M0_LIST,
            "schemes": SCHEMES,
            "total_tokens": {str(m): total_tokens(m) for m in M0_LIST},
            "elapsed_s": round(time.time() - t0, 1),
        },
        "rows": rows,
        "summary": summary,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    out = run(quick=args.quick, seed=args.seed)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"Wrote {OUT}  ({len(out['rows'])} rows, {out['meta']['elapsed_s']}s)")
    for m0 in M0_LIST:
        print(f"\n=== m0={m0} Pareto (worst) ===")
        cand = [s for s in out["summary"] if s["m0"] == m0]
        for s in sorted(cand, key=lambda x: x["area"]):
            mark = " *" if s.get("pareto_worst") else ""
            print(f"  {s['scheme']:16s} vc={s['num_vc']} area={s['area']:.3f} "
                  f"worst={s['t_e2e_ns_worst']:.0f} med={s['t_e2e_ns_med']:.0f} "
                  f"A_w={s['A_worst']} sac_med={s['sac_med']}{mark}")


if __name__ == "__main__":
    main()
