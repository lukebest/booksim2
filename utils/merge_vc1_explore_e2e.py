#!/usr/bin/env python3
"""Merge VC=1 explore schemes into results/pg_e2e_pareto.json.

Adds:
  - updown_best_root (M3′) from pg_vc1_best_root_probe.json
  - phase_tdm_bal2 / phase_tdm_bal3 from pg_vc1_explore_tick3.json
  - phase_tdm_policy (light→bal2, heavy→bal3)

Recomputes summary + Pareto flags. Does not re-run DES for legacy schemes.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import dse_pg_e2e_pareto as E

ROOT = Path(__file__).resolve().parents[1]
E2E = ROOT / "results" / "pg_e2e_pareto.json"
PROBE = ROOT / "results" / "pg_vc1_best_root_probe.json"
TICK3 = ROOT / "results" / "pg_vc1_explore_tick3.json"
TICK2 = ROOT / "results" / "pg_vc1_explore_tick2.json"

NEW_SCHEMES = [
    "updown_best_root",
    "phase_tdm_bal2",
    "phase_tdm_bal3",
    "phase_tdm_policy",
]


def _fault_meta(e2e_rows: list[dict], scen: str) -> tuple[int, int]:
    for r in e2e_rows:
        if r["scenario"] == scen:
            return r["n_routers"], r["n_links"]
    return 0, 0


def _row_from_e2e(scen: str, n_r: int, n_l: int, scheme: str, m0: int,
                  A: int, n_sac: int, t_e2e_ns: float,
                  t_alltoall_cy: int | None = None) -> dict:
    t_comp = E.compute_cycles(A, m0)
    me = E.m_effective(A, m0)
    if t_alltoall_cy is None:
        t_tot = int(round(t_e2e_ns * E.FREQ_GHZ))
        t_alltoall_cy = max(0, t_tot - t_comp)
    else:
        t_tot = t_comp + t_alltoall_cy
        t_e2e_ns = t_tot / E.FREQ_GHZ
    return {
        "scenario": scen,
        "n_routers": n_r,
        "n_links": n_l,
        "scheme": scheme,
        "m0": m0,
        "m_eff": me,
        "A": A,
        "n_sacrificed": n_sac,
        "num_vc": 1,
        "t_compute_cy": t_comp,
        "t_alltoall_cy": t_alltoall_cy,
        "t_e2e_cy": t_tot,
        "t_e2e_ns": t_e2e_ns,
        "comm_frac": t_alltoall_cy / t_tot if t_tot else 0.0,
        "turn_mode": None,
        "turn_vc": None,
        "fc_stage": "explore_merge",
    }


def rebuild_summary(rows: list[dict], schemes: list[str],
                    n_scen_total: int) -> list[dict]:
    vc_req: dict[str, int] = defaultdict(int)
    for r in rows:
        vc_req[r["scheme"]] = max(vc_req[r["scheme"]], r["num_vc"])
    summary = []
    for m0 in E.M0_LIST:
        for sch in schemes:
            sel = [r for r in rows if r["scheme"] == sch and r["m0"] == m0]
            if not sel:
                continue
            partial = len(sel) < n_scen_total
            if vc_req[sch] > 2:
                continue
            ts = sorted(r["t_e2e_ns"] for r in sel)
            summary.append({
                "scheme": sch,
                "m0": m0,
                "num_vc": vc_req[sch],
                "area": round(E.router_area(vc_req[sch]), 4),
                "n_scen": len(sel),
                "n_scen_total": n_scen_total,
                "partial": partial,
                "t_e2e_ns_med": round(ts[len(ts) // 2], 1),
                "t_e2e_ns_worst": round(ts[-1], 1),
                "t_e2e_ns_best": round(ts[0], 1),
                "A_med": sorted(r["A"] for r in sel)[len(sel) // 2],
                "A_worst": min(r["A"] for r in sel),
                "sac_med": sorted(r["n_sacrificed"] for r in sel)[len(sel) // 2],
                "sac_worst": max(r["n_sacrificed"] for r in sel),
                "n_fc": sum(1 for r in sel
                            if r.get("fc_stage") not in (None, "solve_scheme",
                                                         "explore_merge")),
                "comm_frac_med": round(
                    sorted(r["comm_frac"] for r in sel)[len(sel) // 2], 3),
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


def main() -> None:
    data = json.loads(E2E.read_text())
    rows = [r for r in data["rows"] if r["scheme"] not in NEW_SCHEMES]
    up_by = {(r["scenario"], r["m0"]): r for r in rows if r["scheme"] == "updown"}

    probe = { (r["sc"], r["m0"]): r
              for r in json.loads(PROBE.read_text())["results"]}
    tick3 = { (r["sc"], r["m0"]): r
              for r in json.loads(TICK3.read_text())["rows"]}
    # tick2 has tdm_bal2; tick3 also has it — prefer tick3
    tick2 = { (r["sc"], r["m0"]): r
              for r in json.loads(TICK2.read_text())["rows"]}

    n_scen = data["meta"]["n_scenarios"]
    added = 0
    for (scen, m0), pr in probe.items():
        base = up_by.get((scen, m0))
        if base is None:
            continue
        n_r, n_l = base["n_routers"], base["n_links"]
        # M3′
        rows.append(_row_from_e2e(
            scen, n_r, n_l, "updown_best_root", m0,
            A=pr["br_A"], n_sac=pr["br_sac"],
            t_e2e_ns=pr["br_e2e"], t_alltoall_cy=pr["br_mk"]))
        added += 1
        # Phase TDM — same A/sac as M3 (explore used M3 sacrifice set)
        t3 = tick3.get((scen, m0), {})
        t2 = tick2.get((scen, m0), {})
        bal2 = t3.get("tdm_bal2", t2.get("tdm_bal2"))
        bal3 = t3.get("tdm_bal3")
        if bal2 is not None:
            rows.append(_row_from_e2e(
                scen, n_r, n_l, "phase_tdm_bal2", m0,
                A=base["A"], n_sac=base["n_sacrificed"], t_e2e_ns=bal2))
            added += 1
        if bal3 is not None:
            rows.append(_row_from_e2e(
                scen, n_r, n_l, "phase_tdm_bal3", m0,
                A=base["A"], n_sac=base["n_sacrificed"], t_e2e_ns=bal3))
            added += 1
        # Deployable: light payload → bal×2, heavy → bal×3
        pol = bal2 if m0 == 1 else bal3
        if pol is not None:
            rows.append(_row_from_e2e(
                scen, n_r, n_l, "phase_tdm_policy", m0,
                A=base["A"], n_sac=base["n_sacrificed"], t_e2e_ns=pol))
            added += 1

    schemes = list(data["meta"]["schemes"])
    for s in NEW_SCHEMES:
        if s not in schemes:
            schemes.append(s)

    summary = rebuild_summary(rows, schemes, n_scen)
    data["rows"] = rows
    data["summary"] = summary
    data["meta"] = dict(data["meta"])
    data["meta"]["schemes"] = schemes
    data["meta"]["vc1_explore_merged"] = {
        "probe": str(PROBE.relative_to(ROOT)),
        "tick3": str(TICK3.relative_to(ROOT)),
        "new_schemes": NEW_SCHEMES,
        "n_rows_added": added,
    }
    E2E.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {E2E}: +{added} rows, schemes={schemes}")
    for m0 in E.M0_LIST:
        print(f"--- m0={m0} Pareto (worst) ---")
        for s in summary:
            if s["m0"] != m0:
                continue
            mark = " *" if s.get("pareto_worst") else ""
            print(f"  {s['scheme']:20s} area={s['area']:.3f} "
                  f"med={s['t_e2e_ns_med']:.1f} worst={s['t_e2e_ns_worst']:.1f}"
                  f"{mark}")


if __name__ == "__main__":
    main()
