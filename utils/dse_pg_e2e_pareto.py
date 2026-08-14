#!/usr/bin/env python3
"""End-to-end (compute + alltoall) time vs router area Pareto for 8x6 PG.

Fault model: stratified random ≤4 dead routers + ≤8 undirected links
(bidirectional = 1), with no router–link endpoint overlap. Replaces the
fixed link_*/node_* catalogue. See utils/pg_faults_budget_8x6.py.

Evaluation: only schemes designed for ≤2 VC (VC=1 and VC=2). Higher-VC
schemes (M5 f-ring 4VC, LASH, Stripe, …) keep their descriptions in the
report but are not swept here.

Workload: the dispatch half of a MoE expert-parallel FFN layer --
  alltoall dispatch -> expert FFN, run back to back (no overlap).

Strong scaling pins total tokens at the healthy 48-PE config; area is
router-only and scales with the worst VC count a scheme needs.
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

# VC≤2 evaluation set only. Descriptions for excluded schemes remain in the
# HTML report / pg_routing docstring.
SCHEMES = [
    # 1 VC
    "east_first",
    "super_turn_1vc",
    "xy",
    "rect_xy",
    "updown",
    "updown_best_root",  # M3′: same UD, load-optimal root
    "updown_lb",
    "segment",
    "segment_lb",
    # ≤2 VC
    "super_turn",
    "fault_half_ring",
    # Phase-TDM (schedule-level, 1VC per wave): merge via
    # utils/merge_vc1_explore_e2e.py — not a single path-table DES scheme.
    # M9 dual_updown / M10 virtual_mesh: descriptions kept; not in e2e.
]

SEMANTICS = "dead"
Q = D.DEFAULT_Q

A_FLIT = PPA.ARCH_A3_BUFFERS / PPA.ARCH_A3_INTERIOR_FLITS
PORTS = 5

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_e2e_pareto.json"


def total_tokens(m0: int) -> float:
    return A_FULL * A_FULL * m0 * FLIT_BYTES / TOKEN_BYTES


def m_effective(a: int, m0: int) -> int:
    return max(1, math.ceil(m0 * (A_FULL / a) ** 2))


def compute_cycles(a: int, m0: int) -> int:
    return math.ceil(CYCLES_PER_TOKEN * total_tokens(m0) / a)


def router_area(num_vc: int) -> float:
    buffers = PORTS * num_vc * Q * A_FLIT
    return PPA.BASELINE_CROSSBAR + PPA.BASELINE_CONTROL + buffers


def pareto(points: list[dict], xk: str, yk: str) -> list[dict]:
    out = []
    for p in points:
        if not any(o is not p and o[xk] <= p[xk] and o[yk] <= p[yk]
                   and (o[xk] < p[xk] or o[yk] < p[yk]) for o in points):
            out.append(p)
    return sorted(out, key=lambda p: p[xk])


def summarize_e2e(rows: list[dict], schemes: list[str], n_scen: int,
                  vc_cap: int = 2) -> list[dict]:
    """Per-(scheme, m0) median/worst e2e + Pareto flags.  Soft-drops >vc_cap."""
    vc_req: dict[str, int] = defaultdict(int)
    for r in rows:
        vc_req[r["scheme"]] = max(vc_req[r["scheme"]], r["num_vc"])
    summary = []
    for m0 in M0_LIST:
        for sch in schemes:
            sel = [r for r in rows if r["scheme"] == sch and r["m0"] == m0]
            if not sel:
                print(f"  skip summary {sch} m0={m0}: 0/{n_scen}", flush=True)
                continue
            partial = len(sel) < n_scen
            if partial:
                print(f"  partial summary {sch} m0={m0}: "
                      f"{len(sel)}/{n_scen} covered", flush=True)
            if vc_req[sch] > vc_cap:
                print(f"  skip summary {sch} m0={m0}: "
                      f"num_vc={vc_req[sch]} > {vc_cap}", flush=True)
                continue
            ts = sorted(r["t_e2e_ns"] for r in sel)
            summary.append({
                "scheme": sch,
                "m0": m0,
                "num_vc": vc_req[sch],
                "area": round(router_area(vc_req[sch]), 4),
                "n_scen": len(sel),
                "n_scen_total": n_scen,
                "partial": partial,
                "t_e2e_ns_med": round(ts[len(ts) // 2], 1),
                "t_e2e_ns_worst": round(ts[-1], 1),
                "t_e2e_ns_best": round(ts[0], 1),
                "A_med": sorted(r["A"] for r in sel)[len(sel) // 2],
                "A_worst": min(r["A"] for r in sel),
                "sac_med": sorted(r["n_sacrificed"]
                                  for r in sel)[len(sel) // 2],
                "sac_worst": max(r["n_sacrificed"] for r in sel),
                "n_fc": sum(1 for r in sel
                            if r.get("fc_stage") not in (None, "solve_scheme")),
                "comm_frac_med": round(
                    sorted(r["comm_frac"] for r in sel)[len(sel) // 2], 3),
            })
    for m0 in M0_LIST:
        cand = [s for s in summary if s["m0"] == m0 and not s.get("partial")]
        front_w = {s["scheme"] for s in pareto(cand, "area", "t_e2e_ns_worst")}
        front_m = {s["scheme"] for s in pareto(cand, "area", "t_e2e_ns_med")}
        for s in summary:
            if s["m0"] != m0:
                continue
            s["pareto_worst"] = (not s.get("partial")
                                 and s["scheme"] in front_w)
            s["pareto_med"] = (not s.get("partial")
                               and s["scheme"] in front_m)
    return summary


def run(quick: bool = False, n_per_cell: int | None = None,
        seed: int = 0, full_cover: bool = True,
        scenarios: list[dict] | None = None) -> dict:
    if scenarios is None:
        n_per = n_per_cell if n_per_cell is not None else (1 if quick else 4)
        cat = B.write_catalog(n_per_cell=n_per, seed=seed)
        scenarios = cat["scenarios"]
        cat_meta = cat["meta"]
    else:
        cat_meta = {"n_scenarios": len(scenarios),
                    "note": "caller-supplied catalogue"}

    rows = []
    t0 = time.time()
    total = len(scenarios) * len(SCHEMES) * len(M0_LIST)
    i = 0
    for scen in scenarios:
        pg = B.expand_budget(scen, SEMANTICS)
        for sch in SCHEMES:
            for m0 in M0_LIST:
                i += 1
                base = D.get_solution(pg, sch, full_cover=full_cover)
                if not base["feasible"]:
                    print(f"[{i}/{total}] {scen['name']:22s} {sch:16s} "
                          f"m0={m0:2d} -> INFEASIBLE", flush=True)
                    continue
                a = base["n_compute_used"]
                me = m_effective(a, m0)
                rec = D.run_one(pg, sch, me, Q, full_cover=full_cover)
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
                    "fc_stage": base.get("fc_stage"),
                })
                if (i % 20 == 0 or sch in ("super_turn", "super_turn_1vc",
                                           "fault_half_ring")):
                    print(f"[{i}/{total}] {scen['name']:22s} {sch:16s} "
                          f"m0={m0:2d} A={a:2d} vc={rec['num_vc']} "
                          f"e2e={t_tot / FREQ_GHZ:8.1f}ns", flush=True)

    summary = summarize_e2e(rows, SCHEMES, len(scenarios), vc_cap=2)

    meta = {
        "fault_model": "budget_≤4R_≤8L_nonoverlap",
        "catalog": cat_meta,
        "n_scenarios": len(scenarios),
        "freq_ghz": FREQ_GHZ,
        "pe_macs_per_cycle": PE_MACS_PER_CYCLE,
        "d_model": D_MODEL, "d_ff": D_FF, "elem_bytes": ELEM_BYTES,
        "flit_bytes": FLIT_BYTES, "token_bytes": TOKEN_BYTES,
        "cycles_per_token": CYCLES_PER_TOKEN,
        "semantics": SEMANTICS, "Q": Q,
        "m0_list": M0_LIST,
        "schemes": SCHEMES,
        "vc_cap": 2,
        "full_cover": full_cover,
        "total_tokens": {str(m): total_tokens(m) for m in M0_LIST},
        "area_model": {
            "a_flit": A_FLIT, "ports": PORTS,
            "crossbar": PPA.BASELINE_CROSSBAR,
            "control": PPA.BASELINE_CONTROL,
            "note": "normalized to IQ-XY baseline router = 1.0; "
                    "48 routers present in every scheme, so only VC matters",
        },
        "elapsed_s": round(time.time() - t0, 1),
    }
    return {"meta": meta, "rows": rows, "summary": summary}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="1 sample per (nr,nl) cell (~44 scenarios)")
    ap.add_argument("--n-per-cell", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-full-cover", action="store_true",
                    help="stop at minimum-cardinality sacrifice (no escalation)")
    args = ap.parse_args()
    out = run(quick=args.quick, n_per_cell=args.n_per_cell, seed=args.seed,
              full_cover=not args.no_full_cover)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"Wrote {OUT}  ({len(out['rows'])} rows, {out['meta']['elapsed_s']}s)")
    for m0 in M0_LIST:
        print(f"\n=== m0={m0} Pareto (worst) ===")
        cand = [s for s in out["summary"] if s["m0"] == m0]
        for s in sorted(cand, key=lambda x: x["area"]):
            mark = " *" if s.get("pareto_worst") else ""
            print(f"  {s['scheme']:16s} vc={s['num_vc']} area={s['area']:.3f} "
                  f"worst={s['t_e2e_ns_worst']:.0f} med={s['t_e2e_ns_med']:.0f}"
                  f"{mark}")


if __name__ == "__main__":
    main()
