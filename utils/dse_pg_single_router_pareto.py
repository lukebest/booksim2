#!/usr/bin/env python3
"""E2E Pareto under "at most one dead router" (corner / edge / center).

Sweeps the same VC≤2 avoidance set, batch-barrier 1VC variants, and the
deadlock-recovery family as the 44-scenario budget study, but on a
location-stratified catalogue: healthy + 4 corners + 4 edge midpoints +
2 interior.  No extra link faults.

  python3 utils/dse_pg_single_router_pareto.py --jobs 6
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path

import dse_pg_alltoall_8x6 as D
import dse_pg_batch_barrier_e2e as BB
import dse_pg_e2e_pareto as E
import dse_pg_recovery_pareto as R
import pg_faults_budget_8x6 as B

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_single_router_e2e.json"

# M4–M10 that the VC≤2 budget sweep left out (M4 / M5h are already in E.SCHEMES).
M4_TO_M10 = [
    "segment", "segment_lb",          # M4
    "fault_ring_vc", "fault_half_ring",  # M5 / M5h
    "lash", "lash_tor",              # M6 / M6b
    "stripe_vc",                     # M7
    "dual_updown",                   # M9
    "virtual_mesh",                  # M10
]
HIGH_VC = [
    "fault_ring_vc", "lash", "lash_tor",
    "stripe_vc", "dual_updown", "virtual_mesh",
]


def _avoid_job(arg: tuple[dict, str, int, bool]) -> dict | None:
    scen, sch, m0, full_cover = arg
    pg = B.expand_budget(scen, E.SEMANTICS)
    base = D.get_solution(pg, sch, full_cover=full_cover)
    if not base["feasible"]:
        return {"scenario": scen["name"], "scheme": sch, "m0": m0,
                "feasible": False, "reason": "INFEASIBLE",
                "region": scen.get("region")}
    a = base["n_compute_used"]
    me = E.m_effective(a, m0)
    rec = D.run_one(pg, sch, me, E.Q, full_cover=full_cover)
    if not rec["feasible"] or rec["makespan"] is None:
        return {"scenario": scen["name"], "scheme": sch, "m0": m0,
                "feasible": False, "reason": rec.get("reason"),
                "region": scen.get("region")}
    t_comp = E.compute_cycles(a, m0)
    t_comm = rec["makespan"]
    t_tot = t_comp + t_comm
    return {
        "scenario": scen["name"], "region": scen.get("region"),
        "n_routers": scen["n_routers"], "n_links": scen["n_links"],
        "scheme": sch, "m0": m0, "m_eff": me, "A": a,
        "n_sacrificed": rec["n_sacrificed"], "num_vc": rec["num_vc"],
        "t_compute_cy": t_comp, "t_alltoall_cy": t_comm,
        "t_e2e_cy": t_tot, "t_e2e_ns": t_tot / E.FREQ_GHZ,
        "comm_frac": t_comm / t_tot,
        "turn_mode": base.get("turn_mode"),
        "turn_vc": base.get("turn_vc"),
        "fc_stage": base.get("fc_stage"),
        "family": "avoidance", "feasible": True,
    }


def _bb_job(arg: tuple[dict, str, int]) -> dict | None:
    scen, tag, m0 = arg
    pg = B.expand_budget(scen, E.SEMANTICS)
    bb = BB.run_bb(pg, tag, m0, E.Q)
    if bb is None:
        return {"scenario": scen["name"], "scheme": tag, "m0": m0,
                "feasible": False, "reason": "INFEASIBLE",
                "region": scen.get("region")}
    rec = BB._row(scen, tag, m0, bb["A"], bb["n_sac"], 1, bb["makespan"],
                  extra={"sync_cy": bb["sync_cy"],
                         "sync_total": bb["sync_total"],
                         "n_batches": bb["n_batches"],
                         "src_vc": bb["src_vc"],
                         "phase_mks": bb["phase_mks"]})
    rec.update(family="batch_barrier", feasible=True,
               region=scen.get("region"))
    return rec


def run(jobs: int = 1, schemes: list[str] | None = None,
        do_bb: bool = True, do_recovery: bool = True) -> dict:
    scenarios = B.single_router_scenarios()
    t0 = time.time()
    schemes = list(schemes or (list(E.SCHEMES) + HIGH_VC))

    avoid_jobs = [(s, sch, m0, True)
                  for s in scenarios
                  for sch in schemes
                  for m0 in E.M0_LIST]
    bb_jobs = ([(s, tag, m0)
                for s in scenarios
                for tag in BB.BB_TAGS
                for m0 in E.M0_LIST] if do_bb else [])

    avoid_rows: list[dict] = []
    bb_rows: list[dict] = []
    if jobs > 1:
        with mp.Pool(jobs) as pool:
            for i, rec in enumerate(pool.imap_unordered(_avoid_job, avoid_jobs),
                                    1):
                if rec and rec.get("feasible"):
                    avoid_rows.append(rec)
                if i % 20 == 0 or i == len(avoid_jobs):
                    print("[avoid %d/%d] last=%s %s m0=%s %s"
                          % (i, len(avoid_jobs),
                             rec.get("scenario") if rec else "?",
                             rec.get("scheme") if rec else "?",
                             rec.get("m0") if rec else "?",
                             "ok" if rec and rec.get("feasible") else
                             (rec or {}).get("reason", "fail")),
                          flush=True)
            for i, rec in enumerate(pool.imap_unordered(_bb_job, bb_jobs), 1):
                if rec and rec.get("feasible"):
                    bb_rows.append(rec)
                if i % 10 == 0 or i == len(bb_jobs):
                    print("[bb %d/%d] last=%s %s m0=%s %s"
                          % (i, len(bb_jobs),
                             rec.get("scenario") if rec else "?",
                             rec.get("scheme") if rec else "?",
                             rec.get("m0") if rec else "?",
                             "ok" if rec and rec.get("feasible") else
                             (rec or {}).get("reason", "fail")),
                          flush=True)
    else:
        for i, job in enumerate(avoid_jobs, 1):
            rec = _avoid_job(job)
            if rec and rec.get("feasible"):
                avoid_rows.append(rec)
            if i % 20 == 0:
                print("[avoid %d/%d]" % (i, len(avoid_jobs)), flush=True)
        for i, job in enumerate(bb_jobs, 1):
            rec = _bb_job(job)
            if rec and rec.get("feasible"):
                bb_rows.append(rec)

    rec_rows: list[dict] = []
    rec_sum: list[dict] = []
    rec_elapsed = None
    if do_recovery:
        print("recovery sweep…", flush=True)
        rec_doc = R.run(jobs=jobs, scenarios=scenarios)
        rec_rows = rec_doc["rows"]
        rec_sum = rec_doc["summary"]
        rec_elapsed = rec_doc["meta"].get("elapsed_s")
        for r in rec_rows:
            r.setdefault("family", "recovery")

    avoid_schemes = list(dict.fromkeys(schemes + (list(BB.BB_TAGS) if do_bb else [])))
    avoid_all = avoid_rows + bb_rows
    avoid_sum = E.summarize_e2e(avoid_all, avoid_schemes, len(scenarios),
                                vc_cap=16)

    elapsed = round(time.time() - t0, 1)
    return {
        "meta": {
            "fault_model": "single_router_corner_edge_center",
            "n_scenarios": len(scenarios),
            "scenarios": [{"name": s["name"], "region": s.get("region"),
                           "dead_nodes": s["dead_nodes"]}
                          for s in scenarios],
            "freq_ghz": E.FREQ_GHZ,
            "m0_list": E.M0_LIST,
            "schemes": avoid_schemes,
            "routings": R.ROUTINGS,
            "kinds": R.KINDS,
            "total_tokens": {str(m): E.total_tokens(m) for m in E.M0_LIST},
            "elapsed_s": elapsed,
            "recovery_elapsed_s": rec_elapsed,
        },
        "rows_avoid": avoid_all,
        "rows_recovery": rec_rows,
        "summary_avoid": avoid_sum,
        "summary_recovery": rec_sum,
    }


def merge_into(base: dict, extra: dict) -> dict:
    """Replace avoidance rows for schemes present in `extra`, keep the rest."""
    new_sch = {r["scheme"] for r in extra["rows_avoid"]}
    keep = [r for r in base.get("rows_avoid", []) if r["scheme"] not in new_sch]
    avoid_all = keep + extra["rows_avoid"]
    schemes = list(dict.fromkeys(
        list(base.get("meta", {}).get("schemes", []))
        + list(extra.get("meta", {}).get("schemes", []))))
    n_scen = base["meta"]["n_scenarios"]
    base["rows_avoid"] = avoid_all
    base["summary_avoid"] = E.summarize_e2e(avoid_all, schemes, n_scen,
                                            vc_cap=16)
    base["meta"]["schemes"] = schemes
    base["meta"]["elapsed_s"] = round(
        (base["meta"].get("elapsed_s") or 0)
        + (extra["meta"].get("elapsed_s") or 0), 1)
    if extra.get("rows_recovery"):
        base["rows_recovery"] = extra["rows_recovery"]
        base["summary_recovery"] = extra["summary_recovery"]
    return base


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--schemes", nargs="*", default=None)
    ap.add_argument("--skip-bb", action="store_true")
    ap.add_argument("--skip-recovery", action="store_true")
    ap.add_argument("--merge", action="store_true",
                    help="merge into --out instead of overwriting")
    args = ap.parse_args()
    doc = run(jobs=args.jobs, schemes=args.schemes,
              do_bb=not args.skip_bb, do_recovery=not args.skip_recovery)
    if args.merge and args.out.exists():
        old = json.loads(args.out.read_text())
        doc = merge_into(old, doc)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print("Wrote %s  avoid=%d rec=%d  %ss"
          % (args.out, len(doc["rows_avoid"]), len(doc["rows_recovery"]),
             doc["meta"]["elapsed_s"]))
    for m0 in E.M0_LIST:
        print("\n=== m0=%d avoidance (worst) ===" % m0)
        cand = [s for s in doc["summary_avoid"] if s["m0"] == m0]
        for s in sorted(cand, key=lambda x: x["t_e2e_ns_worst"]):
            mark = " *" if s.get("pareto_worst") else ""
            print("  %-18s vc=%d area=%.3f worst=%7.0f med=%7.0f A=%d/%d%s"
                  % (s["scheme"], s["num_vc"], s["area"],
                     s["t_e2e_ns_worst"], s["t_e2e_ns_med"],
                     s["A_med"], s["A_worst"], mark))
        print("=== m0=%d recovery (worst) ===" % m0)
        cand = [s for s in doc["summary_recovery"]
                if s["m0"] == m0 and s.get("n_ok")]
        for s in sorted(cand, key=lambda x: x.get("t_e2e_ns_worst", 1e18)):
            print("  %-15s %-5s area=%.3f worst=%7.0f med=%7.0f ok=%d/%d"
                  % (s["routing"], s["kind"], s.get("area", 0),
                     s.get("t_e2e_ns_worst", 0), s.get("t_e2e_ns_med", 0),
                     s["n_ok"], s["n_scen_total"]))


if __name__ == "__main__":
    main()
