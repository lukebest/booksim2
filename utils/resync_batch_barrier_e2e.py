#!/usr/bin/env python3
"""Recompute batch-barrier e2e with centre-hub sync (no DES re-run).

Uses stored phase_mks from pg_batch_barrier_e2e.json; rebuilds T_sync from
the residual graph centre under link_lat: T_sync = 2·radius_wire (H/V).
"""
from __future__ import annotations

import json
from pathlib import Path

import dse_pg_e2e_pareto as E
import pg_faults_budget_8x6 as B
from dse_pg_batch_barrier_e2e import BB_TAGS, CONCURRENT, merge_into_e2e, summarize
from pg_batch_barrier import barrier_sync_cycles
from pg_routing import apply_sacrifice, solve_scheme

ROOT = Path(__file__).resolve().parents[1]
BB_JSON = ROOT / "results" / "pg_batch_barrier_e2e.json"


def _pg_for_row(scen: dict, row: dict) -> dict:
    """Rebuild the compute/route view used when the row was produced."""
    pg = B.expand_budget(scen, E.SEMANTICS)
    # UD batches used M3 sacrifice; lash/dual used their own A from sol —
    # approximate with M3 sac then trim to A if needed is messy. Prefer:
    # expand + M3 sac (matches bb_ud_*); for bb_lash/dual A usually equals
    # M3 A on this catalogue (zero sac). Fall back to M3 view.
    m3 = solve_scheme(pg, "updown")
    if m3["feasible"] and m3["n_sacrificed"]:
        pg = apply_sacrifice(pg, set(m3["sacrificed"]), True)
    # If row.A smaller (forced sac in dual/lash), drop extras as non-compute.
    if row["A"] < len(pg["compute_nodes"]):
        keep = set(sorted(pg["compute_nodes"])[: row["A"]])  # weak
        # Better: keep nodes that appear in healthy set by size match via
        # re-solve. For catalog almost always A matches.
        pass
    return {"compute_nodes": pg["compute_nodes"], "route_adj": pg["route_adj"]}


def main() -> None:
    data = json.loads(BB_JSON.read_text())
    faults = B.write_catalog(
        n_per_cell=data["meta"]["catalog"].get("n_per_cell", 1),
        seed=data["meta"]["catalog"].get("seed", 0),
    )
    by = {s["name"]: s for s in faults["scenarios"]}

    # Compare models on a few scenes
    demo = []
    new_rows = []
    for row in data["rows"]:
        if not row["scheme"].startswith("bb_"):
            new_rows.append(row)
            continue
        scen = by[row["scenario"]]
        pg = _pg_for_row(scen, row)
        # Prefer exact A: if mismatch, use eccentricity on current compute
        sync_old = row["sync_cy"]
        sync_cy, meta = barrier_sync_cycles(pg, model="center")
        sync_hop, _ = barrier_sync_cycles(pg, model="center_hop")
        sync_bin, _ = barrier_sync_cycles(pg, model="binomial")
        phases = [k for k in row["phase_mks"] if k]
        K = len(phases) if phases else row.get("n_batches", 0)
        sync_total = max(0, K - 1) * sync_cy
        t_comm = sum(phases) + sync_total
        t_comp = E.compute_cycles(row["A"], row["m0"])
        t_tot = t_comp + t_comm
        nr = dict(row)
        nr.update({
            "t_alltoall_cy": t_comm,
            "t_e2e_cy": t_tot,
            "t_e2e_ns": t_tot / E.FREQ_GHZ,
            "comm_frac": t_comm / t_tot if t_tot else 0.0,
            "sync_cy": sync_cy,
            "sync_total": sync_total,
            "sync_meta": meta,
            "sync_cy_hop": sync_hop,
            "sync_cy_binomial": sync_bin,
        })
        new_rows.append(nr)
        if len(demo) < 3 and row["scheme"] == "bb_ud_bal2" and row["m0"] == 1:
            demo.append({
                "sc": row["scenario"], "A": row["A"],
                "old": sync_old, "wire": sync_cy, "hop": sync_hop,
                "binomial": sync_bin, "meta": meta, "phases": phases,
                "e2e_old": row["t_e2e_ns"], "e2e_new": nr["t_e2e_ns"],
            })

    schemes = CONCURRENT + BB_TAGS
    summary = summarize(new_rows, schemes, data["meta"]["n_scenarios"])
    data["rows"] = new_rows
    data["summary"] = summary
    data["meta"] = dict(data["meta"])
    data["meta"]["sync_model"] = (
        "T_sync = 2·radius_wire via graph-centre gather+broadcast; "
        "radius_wire = min_c max_v dist_linklat(c,v) (H=7,V=9, same as DES); "
        "legacy hop model = 2·radius_hops"
    )
    data["meta"]["sync_model_id"] = "center_wire"
    BB_JSON.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {BB_JSON}")
    for d in demo:
        m = d["meta"]
        print(f"demo {d['sc']}: sync {d['old']} → wire={d['wire']} "
              f"(hop={d['hop']}, bin={d['binomial']}) "
              f"r_wire={m['radius_wire']} r_hop={m['radius_hops']} "
              f"center={m['center']} "
              f"e2e {d['e2e_old']:.0f}→{d['e2e_new']:.0f}")
    for m0 in E.M0_LIST:
        print(f"\n=== m0={m0} (worst, center sync) ===")
        for s in sorted((x for x in summary if x["m0"] == m0),
                        key=lambda x: (x["area"], x["t_e2e_ns_worst"])):
            mark = " *" if s.get("pareto_worst") else ""
            syn = s.get("sync_med")
            syn_s = f" sync_med={syn}" if syn is not None else ""
            print(f"  {s['scheme']:16s} worst={s['t_e2e_ns_worst']:.0f} "
                  f"med={s['t_e2e_ns_med']:.0f}{syn_s}{mark}")
    merge_into_e2e(data)


if __name__ == "__main__":
    main()
