#!/usr/bin/env python3
"""Sweep Hamilton-ring allgather over healthy + fault scenarios (uni & bi).

For a 12x16 mesh (H=4, V=8, ramp=1) this:
  * builds the golden (healthy) snake ring and simulates uni + bi allgather,
  * for each link / node fault scenario, recovers a ring with the fault-aware
    Hamiltonian search and simulates uni + bi allgather over it,
  * compares each faulted ring against its same-mode golden,
  * writes results/ring_results.csv and golden trace CSVs.

Unidirectional allgather needs a closed cycle; for the odd (1x1, 3x3) node
holes only a Hamiltonian path exists, so uni is reported infeasible there while
bi runs over the open path.
"""

import argparse
import csv
from pathlib import Path

import hamilton_ring as hr
import sim_hamilton_ring as sr

ROOT = Path(__file__).resolve().parents[1]
MX, MY, H, V, RAMP = 12, 16, 4, 8, 1

FIELDS = [
    "ring_type", "fault_class", "region", "detail", "fault_desc",
    "feasible", "ring_is_cycle", "ring_len", "sacrificed", "makespan",
    "golden_makespan", "slowdown_pct", "eject_ok", "busiest_link", "reason",
]


def sim_row(ring_type, sc, res, golden, msg_size):
    """Build one CSV row for (ring_type, scenario, recovered ring res)."""
    base = {
        "ring_type": ring_type,
        "fault_class": sc["fault_class"],
        "region": sc["region"],
        "detail": sc["detail"],
        "fault_desc": sc["desc"],
        "sacrificed": len(sc.get("sacrificed", [])),
        "golden_makespan": golden,
    }
    if not res["feasible"]:
        base.update(feasible="no", ring_is_cycle="", ring_len="", makespan="",
                    slowdown_pct="", eject_ok="", busiest_link="",
                    reason=res["reason"])
        return base
    if ring_type == "uni" and not res["is_cycle"]:
        base.update(feasible="no", ring_is_cycle="False",
                    ring_len=len(res["order"]), makespan="", slowdown_pct="",
                    eject_ok="", busiest_link="",
                    reason="unidirectional needs a closed cycle; only a "
                           "Hamiltonian path exists (colour imbalance)")
        return base
    s = sr.simulate(res["order"], res["is_cycle"], ring_type,
                    mx=MX, my=MY, h=H, vlat=V, ramp=RAMP, msg_size=msg_size)
    slow = (s["makespan"] / golden - 1.0) * 100.0 if golden else 0.0
    base.update(feasible="yes", ring_is_cycle=str(res["is_cycle"]),
                ring_len=s["ring_len"], makespan=s["makespan"],
                slowdown_pct=f"{slow:.1f}", eject_ok=str(s["eject_ok"]),
                busiest_link=s["busiest_link_flits"], reason=res["reason"])
    return base


def write_csv(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {path} ({len(rows)} rows)")


def write_golden_traces(order, mode, outdir, msg_size):
    res = sr.simulate(order, True, mode, mx=MX, my=MY, h=H, vlat=V, ramp=RAMP,
                      msg_size=msg_size, collect=True)
    summary, link_rows, router_rows = sr.build_traces(res, mx=MX, my=MY, h=H, vlat=V)
    write_csv(outdir / f"ring_trace_{mode}_summary.csv",
              ["cycle", "max_inject", "max_inFlight", "max_eject",
               "conflict_free", "pipelined"], summary)
    write_csv(outdir / f"ring_trace_{mode}_links.csv",
              ["cycle", "link", "kind", "inject", "inFlight", "sources"], link_rows)
    write_csv(outdir / f"ring_trace_{mode}_routers.csv",
              ["cycle", "router", "eject", "eject_sources", "arrive", "forward"],
              router_rows)
    return res["makespan"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--msg-size", type=int, default=1)
    ap.add_argument("--time-budget", type=float, default=20.0)
    ap.add_argument("--no-traces", action="store_true",
                    help="skip the large golden per-cycle trace CSVs")
    ap.add_argument("-o", "--outdir", type=Path, default=ROOT / "results")
    args = ap.parse_args()
    outdir = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    golden_order = hr.snake_cycle(MX, MY)
    golden = {}
    for mode in ("uni", "bi"):
        g = sr.simulate(golden_order, True, mode, mx=MX, my=MY, h=H, vlat=V,
                        ramp=RAMP, msg_size=args.msg_size)
        golden[mode] = g["makespan"]
        assert g["eject_ok"], f"golden {mode} eject count mismatch"
        print(f"golden {mode}: makespan={g['makespan']} "
              f"ramp_bw={g['ramp_bw']} eject_ok={g['eject_ok']}")

    rows = []
    for mode in ("uni", "bi"):
        rows.append({
            "ring_type": mode, "fault_class": "healthy", "region": "-",
            "detail": "-", "fault_desc": "healthy (golden snake ring)",
            "feasible": "yes", "ring_is_cycle": "True", "ring_len": len(golden_order),
            "sacrificed": 0,
            "makespan": golden[mode], "golden_makespan": golden[mode],
            "slowdown_pct": "0.0", "eject_ok": "True",
            "busiest_link": (MX * MY - 1) if mode == "uni" else (MX * MY) // 2,
            "reason": "healthy boustrophedon snake cycle",
        })

    scenarios = hr.all_scenarios(MX, MY) + hr.rebalanced_node_scenarios(MX, MY)
    for sc in scenarios:
        res = hr.find_ring(MX, MY, sc["dead_nodes"], sc["dead_links"],
                           time_budget=args.time_budget)
        if res["feasible"]:
            ok = hr.validate_ring(
                res["order"],
                hr.build_adj(MX, MY, sc["dead_nodes"], sc["dead_links"]),
                res["is_cycle"])
            assert ok, f"invalid ring for {sc['name']}"
        for mode in ("uni", "bi"):
            rows.append(sim_row(mode, sc, res, golden[mode], args.msg_size))
        kind = ("cycle" if res["is_cycle"] else "path") if res["feasible"] else "infeasible"
        print(f"  {sc['name']:18s} -> {kind}")

    write_csv(outdir / "ring_results.csv", FIELDS, rows)

    if not args.no_traces:
        for mode in ("uni", "bi"):
            write_golden_traces(golden_order, mode, outdir, args.msg_size)

    print("\nSummary (makespan / slowdown% vs golden):")
    for r in rows:
        ms = r["makespan"] if r["makespan"] != "" else "INFEASIBLE"
        sl = f"+{r['slowdown_pct']}%" if r["slowdown_pct"] not in ("", "0.0") else ""
        print(f"  {r['ring_type']:3s} {r['fault_desc'][:42]:42s} {str(ms):>10s} {sl}")


if __name__ == "__main__":
    main()
