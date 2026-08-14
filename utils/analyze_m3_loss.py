#!/usr/bin/env python3
"""M3 / M3' end-to-end loss vs routing-independent theoretical optimum.

T_opt_same = T_compute(A) + true_lb(same compute graph, m_eff)
T_opt_phys = T_compute(A_phys) + true_lb(physical residual, m_eff_phys)

true_lb = max(minimax_load_lb · m, inj_term, lat_lb)  — report §8.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
from collections import defaultdict
from pathlib import Path

import dse_pg_e2e_pareto as E
import pg_faults_budget_8x6 as B
import pg_routing as R

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_m3_loss.json"
SCH = ("updown", "updown_best_root")
LABEL = {"updown": "M3", "updown_best_root": "M3′"}


def true_lb(compute, adj, m: int) -> dict:
    a = len(compute)
    if a < 2:
        return {"mm": 0, "inj": 0, "lat": 0, "true_lb": 1, "bind": "none"}
    mm = R.minimax_load_lb(compute, adj)
    inj = ((a - 1) * m + R.RAMP_BW - 1) // R.RAMP_BW
    lat = R.wire_diameter_lb(compute, adj) + 2 * R.RAMP + (m - 1)
    bw = mm * m
    bind = "bw" if bw >= inj and bw >= lat else ("inj" if inj >= lat else "lat")
    return {"mm": mm, "inj": inj, "lat": lat, "true_lb": max(bw, inj, lat, 1),
            "bind": bind}


def phys_view(pg: dict) -> tuple[list[int], dict]:
    adj = pg["route_adj"]
    live = [n for n in pg["compute_nodes"] if n in adj]
    seen: set[int] = set()
    comps: list[list[int]] = []
    for s in live:
        if s in seen:
            continue
        st = [s]
        seen.add(s)
        c: list[int] = []
        while st:
            u = st.pop()
            c.append(u)
            for v in adj.get(u, ()):
                if v in live and v not in seen:
                    seen.add(v)
                    st.append(v)
        comps.append(c)
    keep = max(comps, key=len) if comps else []
    return sorted(keep), adj


def _job(args):
    tag, scen, e2e_by = args
    name = scen["name"]
    pg = B.expand_budget(scen, "dead")
    phys_c, phys_adj = phys_view(pg)
    a_phys = len(phys_c)
    out = []
    for sch in SCH:
        recs = e2e_by.get(sch)
        if not recs:
            continue
        sol = R.solve_scheme(pg, sch)
        if not sol.get("feasible"):
            continue
        compute = sol["compute_nodes"]
        adj = sol["route_adj"]
        load = R.max_link_load(sol["paths"])
        hops = sum(len(p) - 1 for p in sol["paths"].values())
        for m0, r in recs.items():
            me = r["m_eff"]
            same = true_lb(compute, adj, me)
            me_p = E.m_effective(a_phys, m0)
            phys = true_lb(phys_c, phys_adj, me_p)
            t_comp = r["t_compute_cy"]
            t_comm = r["t_alltoall_cy"]
            t_e2e = r["t_e2e_cy"]
            t_comp_p = E.compute_cycles(a_phys, m0)
            t_opt_same = t_comp + same["true_lb"]
            t_opt_phys = t_comp_p + phys["true_lb"]
            out.append({
                "tag": tag, "scenario": name, "scheme": sch,
                "label": LABEL[sch], "m0": m0,
                "region": scen.get("region"),
                "n_routers": scen.get("n_routers"),
                "n_links": scen.get("n_links"),
                "A": r["A"], "A_phys": a_phys,
                "sac": r["n_sacrificed"],
                "m_eff": me, "m_eff_phys": me_p,
                "t_comp": t_comp, "t_comm": t_comm, "t_e2e": t_e2e,
                "t_e2e_ns": r["t_e2e_ns"],
                "t_comp_phys": t_comp_p,
                "load": load, "mm": same["mm"],
                "load_ratio": load / max(same["mm"], 1),
                "hops": hops,
                "lb_same": same["true_lb"], "bind_same": same["bind"],
                "lb_phys": phys["true_lb"], "bind_phys": phys["bind"],
                "t_opt_same": t_opt_same,
                "t_opt_phys": t_opt_phys,
                "loss_same": t_e2e / t_opt_same,
                "loss_phys": t_e2e / t_opt_phys,
                "comm_loss": t_comm / same["true_lb"],
                "comm_frac": t_comm / t_e2e,
            })
    return out


def pctile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round((len(xs) - 1) * p)))]


def summarize(rows):
    blocks = []
    for tag in ("budget44", "single11"):
        for sch in SCH:
            for m0 in E.M0_LIST:
                sel = [r for r in rows if r["tag"] == tag
                       and r["scheme"] == sch and r["m0"] == m0]
                if not sel:
                    continue

                def col(k, s=sel):
                    return [x[k] for x in s]

                worst_p = max(sel, key=lambda r: r["loss_phys"])
                worst_s = max(sel, key=lambda r: r["loss_same"])
                blocks.append({
                    "tag": tag, "scheme": sch, "label": LABEL[sch],
                    "m0": m0, "n": len(sel),
                    "loss_same_med": pctile(col("loss_same"), 0.5),
                    "loss_same_p90": pctile(col("loss_same"), 0.9),
                    "loss_same_worst": max(col("loss_same")),
                    "loss_phys_med": pctile(col("loss_phys"), 0.5),
                    "loss_phys_p90": pctile(col("loss_phys"), 0.9),
                    "loss_phys_worst": max(col("loss_phys")),
                    "comm_loss_med": pctile(col("comm_loss"), 0.5),
                    "comm_loss_worst": max(col("comm_loss")),
                    "load_ratio_med": pctile(col("load_ratio"), 0.5),
                    "load_ratio_worst": max(col("load_ratio")),
                    "t_e2e_med": pctile(col("t_e2e_ns"), 0.5),
                    "t_e2e_worst": max(col("t_e2e_ns")),
                    "sac_med": pctile(col("sac"), 0.5),
                    "sac_worst": max(col("sac")),
                    "A_med": pctile(col("A"), 0.5),
                    "A_worst": min(col("A")),
                    "bind_counts": {k: sum(1 for r in sel if r["bind_same"] == k)
                                    for k in ("bw", "inj", "lat")},
                    "worst_phys_scen": worst_p["scenario"],
                    "worst_phys_loss": worst_p["loss_phys"],
                    "worst_same_scen": worst_s["scenario"],
                    "n_sac_pos": sum(1 for r in sel if r["sac"] > 0),
                })
    return blocks


def peer_gap(rows_e2e, m3_rows, tag: str) -> list[dict]:
    """Per-scenario T_e2e(M3) / T_e2e(best other DF scheme) at m0=13."""
    by = defaultdict(dict)
    for r in rows_e2e:
        if r.get("t_e2e_ns") and r.get("m0") == 13:
            by[r["scenario"]][r["scheme"]] = r
    out = []
    for r in m3_rows:
        if r["tag"] != tag or r["m0"] != 13 or r["scheme"] != "updown":
            continue
        peers = by.get(r["scenario"], {})
        if not peers:
            continue
        best_sch, best = min(
            ((s, p) for s, p in peers.items() if p.get("t_e2e_ns")),
            key=lambda kv: kv[1]["t_e2e_ns"])
        out.append({
            "scenario": r["scenario"],
            "m3_ns": r["t_e2e_ns"],
            "best_sch": best_sch,
            "best_ns": best["t_e2e_ns"],
            "gap": r["t_e2e_ns"] / best["t_e2e_ns"],
            "best_A": best["A"],
            "m3_A": r["A"],
        })
    return out


def analyze_catalog(tag, scenarios, e2e_rows, pool):
    by_scen = defaultdict(lambda: defaultdict(dict))
    for r in e2e_rows:
        if r.get("scheme") in SCH and r.get("t_e2e_ns"):
            by_scen[r["scenario"]][r["scheme"]][r["m0"]] = r
    jobs = []
    for s in scenarios:
        if s["name"] in by_scen:
            jobs.append((tag, s, dict(by_scen[s["name"]])))
    rows = []
    for i, chunk in enumerate(pool.imap_unordered(_job, jobs), 1):
        rows.extend(chunk)
        print("[%s %d/%d]" % (tag, i, len(jobs)), flush=True)
    return rows


def main():
    e2e = json.loads((ROOT / "results" / "pg_e2e_pareto.json").read_text())
    sr = json.loads((ROOT / "results" / "pg_single_router_e2e.json").read_text())
    cat44 = B.stratified_scenarios(n_per_cell=1, seed=0)
    cat11 = B.single_router_scenarios()
    nworkers = min(8, mp.cpu_count() or 4)
    ctx = mp.get_context("fork")
    with ctx.Pool(nworkers) as pool:
        rows = analyze_catalog("budget44", cat44, e2e["rows"], pool)
        rows += analyze_catalog("single11", cat11, sr["rows_avoid"], pool)
    summary = summarize(rows)
    peers44 = peer_gap(e2e["rows"], rows, "budget44")
    peers11 = peer_gap(sr["rows_avoid"], rows, "single11")

    def peer_sum(ps):
        if not ps:
            return None
        gs = [p["gap"] for p in ps]
        return {
            "n": len(ps),
            "gap_med": pctile(gs, 0.5),
            "gap_p90": pctile(gs, 0.9),
            "gap_worst": max(gs),
            "n_m3_best": sum(1 for p in ps if p["best_sch"] in SCH),
            "best_counts": {s: sum(1 for p in ps if p["best_sch"] == s)
                            for s in sorted({p["best_sch"] for p in ps})},
        }

    out = {
        "rows": rows,
        "summary": summary,
        "peer_budget44": peer_sum(peers44),
        "peer_single11": peer_sum(peers11),
        "peer_rows_budget44": peers44,
        "peer_rows_single11": peers11,
        "meta": {
            "true_lb": "max(minimax_load_lb·m, inj, lat)",
            "loss_same": "T_e2e / (T_comp(A) + true_lb(same A))",
            "loss_phys": "T_e2e / (T_comp(A_phys) + true_lb(physical residual))",
            "comm_loss": "T_alltoall / true_lb(same A)",
            "freq_ghz": E.FREQ_GHZ,
        },
    }
    OUT.write_text(json.dumps(out, indent=1))
    print("\n=== summary ===")
    for s in summary:
        print("%-9s %-4s m0=%-2d  same %.3f/%.3f/%.3f  phys %.3f/%.3f/%.3f  "
              "comm %.3f/%.3f  load %.2f/%.2f  sac %d/%d  A %d/%d  worst=%s"
              % (s["tag"], s["label"], s["m0"],
                 s["loss_same_med"], s["loss_same_p90"], s["loss_same_worst"],
                 s["loss_phys_med"], s["loss_phys_p90"], s["loss_phys_worst"],
                 s["comm_loss_med"], s["comm_loss_worst"],
                 s["load_ratio_med"], s["load_ratio_worst"],
                 s["sac_med"], s["sac_worst"],
                 s["A_med"], s["A_worst"], s["worst_phys_scen"]))
    print("peer44", json.dumps(peer_sum(peers44), indent=1))
    print("peer11", json.dumps(peer_sum(peers11), indent=1))
    print("wrote", OUT, "n=", len(rows))


if __name__ == "__main__":
    main()
