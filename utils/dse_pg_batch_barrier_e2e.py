#!/usr/bin/env python3
"""E2E Pareto: concurrent multi-VC vs batch-barrier (1VC + sync) variants.

Quick budget catalogue (≤4R+≤8L). Writes results/pg_batch_barrier_e2e.json
and merges selected rows into results/pg_e2e_pareto.json for the HTML report.

  python3 utils/dse_pg_batch_barrier_e2e.py --quick
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path

import dse_pg_alltoall_8x6 as D
import dse_pg_e2e_pareto as E
import pg_faults_budget_8x6 as B
from pg_batch_barrier import (
    barrier_sync_cycles, batched_makespan, dual_ud_batches, lash_batches,
    ud_bal_batches,
)
from pg_routing import apply_sacrifice, solve_scheme

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_batch_barrier_e2e.json"
E2E = ROOT / "results" / "pg_e2e_pareto.json"

# Concurrent baselines (area = peak VC)
CONCURRENT = [
    "updown",
    "updown_best_root",
    "super_turn",
    "dual_updown",
    "lash",
    # virtual_mesh / stripe_vc: multi physical VC — out of scope
]

# Batch-barrier variants (physical VC = 1)
# name -> builder tag
BB_TAGS = [
    "bb_ud_bal2",
    "bb_ud_bal3",
    "bb_ud_policy",  # m0=1 → bal2, else bal3
    "bb_lash",
    "bb_dual",
]

# Old explore names to drop from e2e JSON on merge
DROP_OLD = {
    "phase_tdm_bal2", "phase_tdm_bal3", "phase_tdm_policy",
}


def _row(scen: dict, scheme: str, m0: int, A: int, n_sac: int, num_vc: int,
         t_comm: int, extra: dict | None = None) -> dict:
    t_comp = E.compute_cycles(A, m0)
    t_tot = t_comp + t_comm
    rec = {
        "scenario": scen["name"],
        "n_routers": scen["n_routers"],
        "n_links": scen["n_links"],
        "scheme": scheme,
        "m0": m0,
        "m_eff": E.m_effective(A, m0),
        "A": A,
        "n_sacrificed": n_sac,
        "num_vc": num_vc,
        "t_compute_cy": t_comp,
        "t_alltoall_cy": t_comm,
        "t_e2e_cy": t_tot,
        "t_e2e_ns": t_tot / E.FREQ_GHZ,
        "comm_frac": t_comm / t_tot if t_tot else 0.0,
        "fc_stage": "batch_barrier_dse",
    }
    if extra:
        rec.update(extra)
    return rec


def run_bb(pg: dict, tag: str, m0: int, Q: int) -> dict | None:
    """Return {A, n_sac, makespan, sync_*, n_batches} or None."""
    # Sacrifice with stock M3 (same as explore) so UD tables stay feasible.
    m3 = solve_scheme(pg, "updown")
    if not m3["feasible"]:
        return None
    pg_r = (apply_sacrifice(pg, set(m3["sacrificed"]), True)
            if m3["n_sacrificed"] else pg)
    A = len(pg_r["compute_nodes"])
    m_eff = E.m_effective(A, m0)
    sync, _ = barrier_sync_cycles(pg_r)

    batches = None
    src_vc = 1
    if tag == "bb_ud_bal2" or (tag == "bb_ud_policy" and m0 == 1):
        batches = ud_bal_batches(pg_r, 2)
    elif tag == "bb_ud_bal3" or (tag == "bb_ud_policy" and m0 != 1):
        batches = ud_bal_batches(pg_r, 3)
    elif tag == "bb_lash":
        sol = solve_scheme(pg_r, "lash")
        if not sol["feasible"]:
            return None
        batches = lash_batches(sol)
        src_vc = sol["num_vc"]
        A = sol["n_compute_used"]
        m_eff = E.m_effective(A, m0)
        # re-sync on possibly different compute set
        sync, _ = barrier_sync_cycles({
            "compute_nodes": sol["compute_nodes"],
            "route_adj": sol["route_adj"],
        })
        pg_r = {"compute_nodes": sol["compute_nodes"],
                "route_adj": sol["route_adj"]}
        n_sac = sol["n_sacrificed"]
        ph = batched_makespan(pg_r, batches, m_eff, Q, sync)
        if ph is None:
            return None
        return {
            "A": A, "n_sac": n_sac, "makespan": ph["makespan"],
            "sync_cy": ph["sync_cy"], "sync_total": ph["sync_total"],
            "n_batches": ph["n_batches"], "phase_mks": ph["phase_mks"],
            "src_vc": src_vc,
        }
    elif tag == "bb_dual":
        sol = solve_scheme(pg_r, "dual_updown")
        if not sol["feasible"]:
            return None
        batches = dual_ud_batches(sol)
        src_vc = sol["num_vc"]
        A = sol["n_compute_used"]
        m_eff = E.m_effective(A, m0)
        sync, _ = barrier_sync_cycles({
            "compute_nodes": sol["compute_nodes"],
            "route_adj": sol["route_adj"],
        })
        pg_r = {"compute_nodes": sol["compute_nodes"],
                "route_adj": sol["route_adj"]}
        n_sac = sol["n_sacrificed"]
        ph = batched_makespan(pg_r, batches, m_eff, Q, sync)
        if ph is None:
            return None
        return {
            "A": A, "n_sac": n_sac, "makespan": ph["makespan"],
            "sync_cy": ph["sync_cy"], "sync_total": ph["sync_total"],
            "n_batches": ph["n_batches"], "phase_mks": ph["phase_mks"],
            "src_vc": src_vc,
        }
    else:
        return None

    if batches is None:
        return None
    ph = batched_makespan(pg_r, batches, m_eff, Q, sync)
    if ph is None:
        return None
    return {
        "A": A, "n_sac": m3["n_sacrificed"], "makespan": ph["makespan"],
        "sync_cy": ph["sync_cy"], "sync_total": ph["sync_total"],
        "n_batches": ph["n_batches"], "phase_mks": ph["phase_mks"],
        "src_vc": src_vc,
    }


def summarize(rows: list[dict], schemes: list[str], n_scen: int) -> list[dict]:
    vc_req: dict[str, int] = defaultdict(int)
    for r in rows:
        vc_req[r["scheme"]] = max(vc_req[r["scheme"]], r["num_vc"])
    summary = []
    for m0 in E.M0_LIST:
        for sch in schemes:
            sel = [r for r in rows if r["scheme"] == sch and r["m0"] == m0]
            if not sel:
                continue
            partial = len(sel) < n_scen
            ts = sorted(r["t_e2e_ns"] for r in sel)
            summary.append({
                "scheme": sch,
                "m0": m0,
                "num_vc": vc_req[sch],
                "area": round(E.router_area(vc_req[sch]), 4),
                "n_scen": len(sel),
                "n_scen_total": n_scen,
                "partial": partial,
                "t_e2e_ns_med": round(ts[len(ts) // 2], 1),
                "t_e2e_ns_worst": round(ts[-1], 1),
                "t_e2e_ns_best": round(ts[0], 1),
                "A_med": sorted(r["A"] for r in sel)[len(sel) // 2],
                "A_worst": min(r["A"] for r in sel),
                "sac_med": sorted(r["n_sacrificed"] for r in sel)[len(sel) // 2],
                "sac_worst": max(r["n_sacrificed"] for r in sel),
                "n_fc": sum(1 for r in sel
                            if r.get("fc_stage") not in (
                                None, "solve_scheme", "explore_merge",
                                "batch_barrier_dse")),
                "comm_frac_med": round(
                    sorted(r["comm_frac"] for r in sel)[len(sel) // 2], 3),
                "sync_med": (round(sorted(r.get("sync_total", 0) for r in sel)
                                   [len(sel) // 2], 1)
                             if any("sync_total" in r for r in sel) else None),
            })
    for m0 in E.M0_LIST:
        cand = [s for s in summary if s["m0"] == m0 and not s.get("partial")]
        front_w = {s["scheme"] for s in E.pareto(cand, "area", "t_e2e_ns_worst")}
        front_m = {s["scheme"] for s in E.pareto(cand, "area", "t_e2e_ns_med")}
        for s in summary:
            if s["m0"] != m0:
                continue
            s["pareto_worst"] = (not s.get("partial") and s["scheme"] in front_w)
            s["pareto_med"] = (not s.get("partial") and s["scheme"] in front_m)
    return summary


def run(quick: bool = True, seed: int = 0) -> dict:
    n_per = 1 if quick else 4
    cat = B.write_catalog(n_per_cell=n_per, seed=seed)
    scenarios = cat["scenarios"]
    Q = E.Q
    rows: list[dict] = []
    t0 = time.time()
    schemes_all = CONCURRENT + BB_TAGS
    total = len(scenarios) * len(schemes_all) * len(E.M0_LIST)
    i = 0

    for scen in scenarios:
        pg = B.expand_budget(scen, E.SEMANTICS)
        # concurrent
        for sch in CONCURRENT:
            for m0 in E.M0_LIST:
                i += 1
                base = D.get_solution(pg, sch, full_cover=True)
                if not base["feasible"]:
                    print(f"[{i}/{total}] {scen['name']} {sch} m0={m0} INFEAS",
                          flush=True)
                    continue
                a = base["n_compute_used"]
                me = E.m_effective(a, m0)
                rec = D.run_one(pg, sch, me, Q, full_cover=True)
                if not rec["feasible"] or rec["makespan"] is None:
                    print(f"[{i}/{total}] {scen['name']} {sch} m0={m0} fail",
                          flush=True)
                    continue
                rows.append(_row(scen, sch, m0, a, rec["n_sacrificed"],
                                 rec["num_vc"], rec["makespan"]))
                if i % 30 == 0:
                    print(f"[{i}/{total}] {scen['name']} {sch} m0={m0} "
                          f"e2e={rows[-1]['t_e2e_ns']:.0f}ns vc={rec['num_vc']}",
                          flush=True)
        # batch-barrier
        for tag in BB_TAGS:
            for m0 in E.M0_LIST:
                i += 1
                bb = run_bb(pg, tag, m0, Q)
                if bb is None:
                    print(f"[{i}/{total}] {scen['name']} {tag} m0={m0} fail",
                          flush=True)
                    continue
                rows.append(_row(
                    scen, tag, m0, bb["A"], bb["n_sac"],
                    num_vc=1,  # physical VC during each batch
                    t_comm=bb["makespan"],
                    extra={
                        "sync_cy": bb["sync_cy"],
                        "sync_total": bb["sync_total"],
                        "n_batches": bb["n_batches"],
                        "src_vc": bb["src_vc"],
                        "phase_mks": bb["phase_mks"],
                    },
                ))
                if i % 30 == 0:
                    print(f"[{i}/{total}] {scen['name']} {tag} m0={m0} "
                          f"e2e={rows[-1]['t_e2e_ns']:.0f}ns "
                          f"batches={bb['n_batches']} sync={bb['sync_total']}",
                          flush=True)

    summary = summarize(rows, schemes_all, len(scenarios))
    meta = {
        "fault_model": "budget_≤4R_≤8L_nonoverlap",
        "catalog": cat["meta"],
        "n_scenarios": len(scenarios),
        "freq_ghz": E.FREQ_GHZ,
        "pe_macs_per_cycle": E.PE_MACS_PER_CYCLE,
        "d_model": E.D_MODEL, "d_ff": E.D_FF, "elem_bytes": E.ELEM_BYTES,
        "flit_bytes": E.FLIT_BYTES, "token_bytes": E.TOKEN_BYTES,
        "cycles_per_token": E.CYCLES_PER_TOKEN,
        "semantics": E.SEMANTICS, "Q": Q,
        "m0_list": E.M0_LIST,
        "schemes": schemes_all,
        "concurrent": CONCURRENT,
        "batch_barrier": BB_TAGS,
        "sync_model": (
            "T_sync = 2·radius_wire via graph-centre gather+broadcast; "
            "radius_wire uses Dijkstra with link_lat (H=7,V=9, same as DES); "
            "T_comm = Σ batch_makespan + (K−1)·T_sync"
        ),
        "sync_model_id": "center_wire",
        "total_tokens": {str(m): E.total_tokens(m) for m in E.M0_LIST},
        "area_model": {
            "a_flit": E.A_FLIT, "ports": E.PORTS,
            "crossbar": __import__("ppa_analytic_model").BASELINE_CROSSBAR,
            "control": __import__("ppa_analytic_model").BASELINE_CONTROL,
            "note": "batch-barrier schemes sized at physical VC=1",
        },
        "elapsed_s": round(time.time() - t0, 1),
    }
    return {"meta": meta, "rows": rows, "summary": summary}


def merge_into_e2e(bb: dict) -> None:
    """Replace old phase_tdm / bb rows; add concurrent high-VC + new bb."""
    if not E2E.exists():
        E2E.write_text(json.dumps(bb, indent=1) + "\n")
        return
    data = json.loads(E2E.read_text())
    new_schemes = set(bb["meta"]["schemes"])
    keep = [r for r in data["rows"]
            if r["scheme"] not in DROP_OLD and r["scheme"] not in new_schemes]
    rows = keep + bb["rows"]
    schemes = [s for s in data["meta"]["schemes"]
               if s not in DROP_OLD and s not in new_schemes]
    schemes += [s for s in bb["meta"]["schemes"] if s not in schemes]
    n_scen = data["meta"].get("n_scenarios") or bb["meta"]["n_scenarios"]
    summary = summarize(rows, schemes, n_scen)
    data["rows"] = rows
    data["summary"] = summary
    data["meta"] = dict(data["meta"])
    data["meta"]["schemes"] = schemes
    data["meta"]["batch_barrier"] = bb["meta"]
    data["meta"]["vc_cap"] = max(
        (s["num_vc"] for s in summary), default=2)
    E2E.write_text(json.dumps(data, indent=1) + "\n")
    print(f"merged into {E2E}: {len(rows)} rows, schemes={schemes}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", default=True)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-merge", action="store_true")
    args = ap.parse_args()
    out = run(quick=not args.full, seed=args.seed)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"Wrote {OUT} ({len(out['rows'])} rows, {out['meta']['elapsed_s']}s)")
    for m0 in E.M0_LIST:
        print(f"\n=== m0={m0} (worst) ===")
        for s in sorted((x for x in out["summary"] if x["m0"] == m0),
                        key=lambda x: (x["area"], x["t_e2e_ns_worst"])):
            mark = " *" if s.get("pareto_worst") else ""
            syn = s.get("sync_med")
            syn_s = f" sync_med={syn}" if syn is not None else ""
            print(f"  {s['scheme']:16s} vc={s['num_vc']} area={s['area']:.3f} "
                  f"med={s['t_e2e_ns_med']:.0f} worst={s['t_e2e_ns_worst']:.0f}"
                  f"{syn_s}{mark}")
    if not args.no_merge:
        merge_into_e2e(out)


if __name__ == "__main__":
    main()
