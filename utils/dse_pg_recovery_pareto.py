#!/usr/bin/env python3
"""End-to-end time vs area for deadlock RECOVERY schemes on the 8x6 PG mesh.

Same workload, same DES, same fault catalogue and same e2e model as
utils/dse_pg_e2e_pareto.py (deadlock avoidance) -- only the deadlock
strategy differs, so the two Pareto fronts are directly comparable but are
plotted separately.

Three routings are swept, all 1 VC and all sacrificing nothing beyond the
nodes the residual graph physically disconnects:

  xy_detour     baseline XY where the L-path survives, minimal detour else
  minmax        no turn model at all, peak link load minimised -- the best
                fault-avoiding routing once deadlock is the mechanism's job
  updown_relax  M3' best-root Up*/Down* core + free paths for the pairs the
                tree cannot serve (so no turn-driven sacrifice ladder)

The "none" kind is the control experiment for each routing: same paths, no
recovery mechanism, which deadlocks in the DES whenever the CDG is cyclic.

Writes results/pg_recovery_e2e.json.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import time
from pathlib import Path

import dse_pg_e2e_pareto as E
import pg_deadlock_recovery as DR
import pg_faults_budget_8x6 as B
import pg_routing as R

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_recovery_e2e.json"
AVOID = ROOT / "results" / "pg_e2e_pareto.json"

KINDS = ["none", "sb", "spin", "swap"]
ROUTINGS = ["xy_detour", "minmax", "updown_relax", "super_turn_1vc"]
ROUTING_LABEL = {
    "xy_detour": "基线 XY + 最小绕障",
    "minmax": "无转向模型 · min-max 负载均衡",
    "updown_relax": "M3′ best-root 核心 + 非法转向补齐",
    "super_turn_1vc": "Super-turn 转向集合压到 1 VC",
}
M0_LIST = E.M0_LIST
Q = E.Q
SEMANTICS = "dead"
# The parent DES gives up at 200k cycles; recovery runs legitimately need
# more, so the guard is raised and runs that still hit it are reported as
# censored (">CAP cy") rather than as failures of the mechanism.
T_MAX_REC = 1_500_000

# --------------------------------------------------------------------------
# Hardware cost.  Two parts: what this project's area currency can express
# (buffers / tables, priced at A_FLIT per 128b flit slot) and the control
# logic, which only the papers' own synthesis can price -- carried as a
# percentage of the baseline 1-VC router with its source.
# --------------------------------------------------------------------------

# Which routings this process actually simulates (--routings narrows it so a
# new routing can be added without re-running the ones already in the JSON).
SWEEP_ROUTINGS = list(ROUTINGS)

A_FLIT = E.A_FLIT
BIT = A_FLIT / 128.0          # one flop bit, in area currency
ROM_BIT = 0.15 * BIT          # SRAM/ROM bit, same ratio as rg_sched_cost

MECH = {
    "none": {
        "label": "无恢复机制（对照）",
        "pct": 0.0, "pct_hi": 0.0, "pct_src": "—",
        "extra_bits": 0, "extra_flits": 0.0,
        "detect": "无", "action": "无",
    },
    "sb": {
        "label": "Static Bubble (HPCA'17)",
        "pct": 0.005, "pct_hi": 0.10,
        "pct_src": "自报 <0.5%（32nm DSENT，对 1-cycle mesh router）；"
                   "SPIN 论文测得 10%（15nm RTL）；DeDR 测得 6.1%（45nm）",
        # FSM+counter at the 15 bubble routers, is_deadlock bit + IO_priority
        # + source-id at all 48, turn buffer 2b/turn up to the loop bound.
        "extra_bits": (1 + 6 + 6) + (6 + 4 + 2 * 24) * 15 / 48,
        "extra_flits": 15 / 48,      # 15 packet-sized bubbles, chip-wide
        "detect": "timeout t_DD=34cy + probe 绕环一趟",
        "action": "环上一个静态气泡开一拍，环整体前进 1 跳",
    },
    "spin": {
        "label": "SPIN (ISCA'18)",
        "pct": 0.04, "pct_hi": 0.15,
        "pct_src": "自报 4%（15nm RTL，对 west-first router）；"
                   "DRAIN 测得 ~15%（11nm，对 DoR router）；DeDR 测得 4.0%",
        # loop buffer log2(radix) x N = 3 x 48 bits, FSM, is_deadlock, src-id
        "extra_bits": 3 * 48 + 7 + 1 + 6,
        "extra_flits": 0.0,
        "detect": "timeout t_DD=128cy + probe 绕环一趟",
        "action": "环上所有包同一拍同步前进 1 跳（spin）",
    },
    "swap": {
        "label": "SWAP (MICRO'19)",
        "pct": 0.04, "pct_hi": 0.04,
        "pct_src": "自报 ~4%（对 XY/west-first router），比 escape-VC 低 30%",
        # swapPointer + swap_req/ack wires; datapath: 2:1 mux+demux per port,
        # u-turn in the crossbar (counted in pct), no extra buffer.
        "extra_bits": 4 + 2 * 5,
        "extra_flits": 0.0,
        "detect": "无检测；每 K*N=48cy 轮到一次（TDM）",
        "action": "与下游交换两个包，被换的包回退 1 跳（backtrack）",
    },
}


def mech_area(kind: str, reorder_flits: float = 0.0, hi: bool = False) -> float:
    m = MECH[kind]
    base = E.router_area(1)
    pct = m["pct_hi"] if hi else m["pct"]
    return (base * (1 + pct) + m["extra_bits"] * ROM_BIT
            + (m["extra_flits"] + reorder_flits) * A_FLIT)


# --------------------------------------------------------------------------

def one_scenario(job: tuple[dict, tuple[str, ...]]) -> list[dict]:
    """Worker entry.  The routing list travels inside the job because 3.14's
    default start method re-imports this module in the child, which would
    reset a module-level override."""
    scen, routings = job
    out: list[dict] = []
    for routing in routings:
        out.extend(one_case(scen, routing))
    return out


def one_case(scen: dict, routing: str) -> list[dict]:
    pg = B.expand_budget(scen, SEMANTICS)
    sol = DR.solve_routing(pg, routing)
    out: list[dict] = []
    base = {
        "scenario": scen["name"],
        "routing": routing,
        "n_routers": scen["n_routers"],
        "n_links": scen["n_links"],
    }
    if not sol["feasible"]:
        for kind in KINDS:
            for m0 in M0_LIST:
                out.append({**base, "kind": kind, "m0": m0,
                            "feasible": False, "reason": sol["reason"],
                            "n_sacrificed": sol.get("n_sacrificed")})
        return out
    a = sol["n_compute_used"]
    common = {
        **base,
        "A": a,
        "n_sacrificed": sol["n_sacrificed"],
        "n_detour_pairs": sol["n_detour_pairs"],
        "n_free_pairs": sol["n_free_pairs"],
        "root": sol["root"],
        "cdg_acyclic": sol["cdg_acyclic"],
        "max_load": sol["max_load"],
        # Best peak load any routing could achieve on this residual graph:
        # tells load balance apart from "the graph is simply that narrow".
        "load_lb": R.minimax_load_lb(sol["compute_nodes"], sol["route_adj"]),
        "hops": sol["hops"],
        "num_vc": 1,
    }
    for m0 in M0_LIST:
        me = E.m_effective(a, m0)
        t_comp = E.compute_cycles(a, m0)
        for kind in KINDS:
            t0 = time.time()
            sim = DR.simulate_recovery(
                sol["paths"], sol["compute_nodes"], sol["route_adj"],
                m=me, Q=Q, kind=None if kind == "none" else kind,
                t_max=T_MAX_REC)
            dt = round(time.time() - t0, 2)
            rec = {**common, "kind": kind, "m0": m0, "m_eff": me,
                   "t_compute_cy": t_comp, "des_s": dt}
            if sim is None or sim.get("failed"):
                why = "no_compute" if sim is None else sim["failed"]
                rec.update(feasible=False,
                           reason={"stall": "DEADLOCK_NOT_CLEARED",
                                   "tmax": "OVER_%dcy" % T_MAX_REC}.get(
                                       why, why))
                if sim:
                    rec.update(done_frac=sim["done_frac"],
                               n_detect=sim["rec_detect"],
                               n_spins=sim["rec_spins"],
                               n_grants=sim["rec_grants"],
                               n_swaps=sim["rec_swaps"],
                               cycles=sim["cycles"])
                out.append(rec)
                continue
            mk = sim["makespan"]
            rec.update(
                feasible=True, reason="ok",
                t_alltoall_cy=mk,
                t_e2e_cy=t_comp + mk,
                t_e2e_ns=(t_comp + mk) / E.FREQ_GHZ,
                comm_frac=mk / (t_comp + mk),
                ordered_ok=sim["ordered_ok"],
                n_pairs_out_of_order=sim["n_pairs_out_of_order"],
                reorder_depth=sim["reorder_depth"],
                n_detect=sim["rec_detect"],
                n_false_pos=sim["rec_false_pos"],
                n_resolve=sim["rec_resolve"],
                n_grants=sim["rec_grants"],
                n_spins=sim["rec_spins"],
                n_swaps=sim["rec_swaps"],
                n_backtrack=sim["rec_backtrack"],
                busy_cy=sim["rec_busy_cy"],
                stall_cy=sim["rec_stall_cy"],
                ring_max=sim["rec_ring_max"],
                ring_avg=(round(sim["rec_ring_sum"] / sim["rec_detect"], 1)
                          if sim["rec_detect"] else None),
                lap_avg=(round(sim["rec_lap_sum"] / sim["rec_detect"], 1)
                         if sim["rec_detect"] else None),
                no_bubble=sim["rec_no_bubble"],
                bonus_left=sim["bonus_left"],
            )
            out.append(rec)
    return out


def summarize(rows: list[dict], n_scen: int) -> list[dict]:
    summary = []
    for routing in ROUTINGS:
        sel = [r for r in rows if r.get("routing") == routing]
        if sel:
            summary.extend(summarize_one(sel, routing))
    return summary


def summarize_one(rows: list[dict], routing: str) -> list[dict]:
    summary = []
    for m0 in M0_LIST:
        for kind in KINDS:
            sel = [r for r in rows if r["kind"] == kind and r["m0"] == m0
                   and r.get("feasible")]
            allr = [r for r in rows if r["kind"] == kind and r["m0"] == m0]
            if not sel:
                summary.append({
                    "kind": kind, "routing": routing, "m0": m0, "n_ok": 0,
                    "n_scen_total": len(allr),
                    "reasons": sorted({r.get("reason") for r in allr}),
                })
                continue
            ts = sorted(r["t_e2e_ns"] for r in sel)
            ro = max(r["reorder_depth"] for r in sel)
            summary.append({
                "kind": kind, "routing": routing, "m0": m0,
                "n_ok": len(sel), "n_scen_total": len(allr),
                "complete": len(sel) == len(allr),
                "num_vc": 1,
                "area": round(mech_area(kind, ro if kind == "swap" else 0.0),
                              4),
                "area_hi": round(mech_area(kind, ro if kind == "swap" else 0.0,
                                           hi=True), 4),
                "t_e2e_ns_med": round(ts[len(ts) // 2], 1),
                "t_e2e_ns_worst": round(ts[-1], 1),
                "t_e2e_ns_best": round(ts[0], 1),
                "A_med": sorted(r["A"] for r in sel)[len(sel) // 2],
                "A_worst": min(r["A"] for r in sel),
                "sac_med": sorted(r["n_sacrificed"]
                                  for r in sel)[len(sel) // 2],
                "sac_worst": max(r["n_sacrificed"] for r in sel),
                "n_ordered_ok": sum(1 for r in sel if r["ordered_ok"]),
                "reorder_depth_max": ro,
                "detect_med": sorted(r["n_detect"]
                                     for r in sel)[len(sel) // 2],
                "detect_worst": max(r["n_detect"] for r in sel),
                "false_pos_worst": max(r["n_false_pos"] for r in sel),
                "recover_worst": max(r["n_grants"] + r["n_spins"]
                                     + r["n_swaps"] for r in sel),
                "busy_frac_med": round(sorted(
                    r["busy_cy"] / max(r["t_alltoall_cy"], 1)
                    for r in sel)[len(sel) // 2], 3),
                "stall_frac_med": round(sorted(
                    r["stall_cy"] / max(r["t_alltoall_cy"], 1)
                    for r in sel)[len(sel) // 2], 3),
                "ring_max": max((r["ring_max"] for r in sel), default=0),
                "no_bubble": sum(r["no_bubble"] for r in sel),
                "comm_frac_med": round(sorted(
                    r["comm_frac"] for r in sel)[len(sel) // 2], 3),
                "max_load_med": sorted(r["max_load"]
                                       for r in sel)[len(sel) // 2],
                "max_load_worst": max(r["max_load"] for r in sel),
                "load_ratio_med": round(sorted(
                    r["max_load"] / max(r.get("load_lb") or 1, 1)
                    for r in sel)[len(sel) // 2], 3),
                "load_ratio_worst": round(max(
                    r["max_load"] / max(r.get("load_lb") or 1, 1)
                    for r in sel), 3),
                "hops_med": sorted(r["hops"] for r in sel)[len(sel) // 2],
                "n_cyclic": sum(1 for r in sel if not r["cdg_acyclic"]),
                "cyc_ch_med": sorted(r.get("cdg_cycle_channels") or 0
                                     for r in sel)[len(sel) // 2],
                "cyc_frac_med": round(sorted(
                    (r.get("cdg_cycle_channels") or 0)
                    / max(r.get("cdg_channels") or 1, 1)
                    for r in sel)[len(sel) // 2], 3),
                "n_free_pairs_worst": max(r["n_free_pairs"] for r in sel),
            })
    return summary


def avoidance_reference() -> list[dict]:
    """1-VC deadlock-avoidance rows for the same 44 scenarios, for context."""
    if not AVOID.exists():
        return []
    doc = json.loads(AVOID.read_text())
    keep = {"xy", "updown", "updown_best_root", "segment", "east_first"}
    return [s for s in doc.get("summary", []) if s["scheme"] in keep]


def run(names: list[str] | None = None, jobs: int = 1) -> dict:
    cat = B.write_catalog(n_per_cell=1, seed=0)
    scenarios = cat["scenarios"]
    if names:
        scenarios = [s for s in scenarios if s["name"] in names]
    t0 = time.time()
    rows: list[dict] = []
    jobs_in = [(s, tuple(SWEEP_ROUTINGS)) for s in scenarios]
    if jobs > 1:
        with mp.Pool(jobs) as pool:
            for i, part in enumerate(
                    pool.imap_unordered(one_scenario, jobs_in), 1):
                rows.extend(part)
                print("[%d/%d] %s done" % (i, len(scenarios),
                                           part[0]["scenario"]), flush=True)
    else:
        for i, job in enumerate(jobs_in, 1):
            part = one_scenario(job)
            rows.extend(part)
            for r in part:
                if r["m0"] == M0_LIST[-1]:
                    print("  %-14s %-5s m0=%-2d A=%s sac=%s -> %s mk=%s "
                          "(%ss)" % (r["scenario"], r["kind"], r["m0"],
                                     r.get("A"), r.get("n_sacrificed"),
                                     r.get("reason"),
                                     r.get("t_alltoall_cy"), r.get("des_s")),
                          flush=True)
            print("[%d/%d]" % (i, len(scenarios)), flush=True)
    rows.sort(key=lambda r: (r["scenario"], ROUTINGS.index(r["routing"]),
                             r["m0"], KINDS.index(r["kind"])))
    return {
        "meta": {
            "fault_model": "budget_≤4R_≤8L_nonoverlap",
            "catalog": cat["meta"],
            "n_scenarios": len(scenarios),
            "routings": SWEEP_ROUTINGS,
            "routing_label": ROUTING_LABEL,
            "kinds": KINDS,
            "mech": MECH,
            "m0_list": M0_LIST,
            "Q": Q, "semantics": SEMANTICS,
            "freq_ghz": E.FREQ_GHZ,
            "t_dd": {"sb": DR.SB_T_DD, "spin": DR.SPIN_T_DD},
            "swap_period_cy": DR.SWAP_K * 48,
            "sb_nodes": sorted(DR.SB_NODES),
            "n_sb_nodes": len(DR.SB_NODES),
            "area_model": {
                "base_1vc": round(E.router_area(1), 4),
                "a_flit": A_FLIT, "flop_bit": BIT, "rom_bit": ROM_BIT,
                "note": "同 dse_pg_e2e_pareto 口径；机制逻辑面积用各论文自报"
                        "百分比，area_hi 用第三方复现的上限",
            },
            "elapsed_s": round(time.time() - t0, 1),
        },
        "rows": rows,
        "summary": summarize(rows, len(scenarios)),
        "avoidance_reference": avoidance_reference(),
    }


TDD_LIST = [34, 128, 512, 1024]
TDD_SCEN = ["b_r1_l2_0000", "b_r2_l4_0000", "b_r4_l8_0000"]


def _tdd_job(arg: tuple[str, str, int]) -> dict:
    name, kind, t_dd = arg
    scen = next(s for s in B.stratified_scenarios(n_per_cell=1, seed=0)
                if s["name"] == name)
    pg = B.expand_budget(scen, SEMANTICS)
    sol = DR.solve_xy_detour(pg)
    a = sol["n_compute_used"]
    me = E.m_effective(a, 1)
    t0 = time.time()
    sim = DR.simulate_recovery(sol["paths"], sol["compute_nodes"],
                               sol["route_adj"], m=max(me, 2), Q=Q, kind=kind,
                               t_dd=t_dd, t_max=T_MAX_REC)
    rec = {"scenario": name, "kind": kind, "t_dd": t_dd, "m": max(me, 2),
           "A": a, "des_s": round(time.time() - t0, 1)}
    if sim is None or sim.get("failed"):
        rec["reason"] = sim["failed"] if sim else "no_compute"
        return rec
    rec.update(makespan=sim["makespan"], n_detect=sim["rec_detect"],
               n_false_pos=sim["rec_false_pos"], n_spins=sim["rec_spins"],
               n_grants=sim["rec_grants"], stall_cy=sim["rec_stall_cy"],
               busy_cy=sim["rec_busy_cy"])
    return rec


def run_tdd(jobs: int = 1) -> dict:
    """Detection-threshold sensitivity: the ranking must not hinge on t_DD."""
    work = [(n, k, td) for n in TDD_SCEN for k in ("sb", "spin")
            for td in TDD_LIST]
    t0 = time.time()
    if jobs > 1:
        with mp.Pool(jobs) as pool:
            rows = list(pool.imap(_tdd_job, work))
    else:
        rows = [_tdd_job(w) for w in work]
    return {"meta": {"t_dd_list": TDD_LIST, "scenarios": TDD_SCEN,
                     "routing": "xy_detour", "m0": 1,
                     "elapsed_s": round(time.time() - t0, 1)},
            "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--routings", nargs="*", default=None,
                    choices=ROUTINGS,
                    help="simulate a subset (merge into the JSON afterwards)")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--tdd", action="store_true",
                    help="detection-threshold sensitivity only")
    args = ap.parse_args()
    if args.routings:
        global SWEEP_ROUTINGS
        SWEEP_ROUTINGS = [r for r in ROUTINGS if r in args.routings]
    if args.tdd:
        doc = run_tdd(jobs=args.jobs)
        out = Path(args.out) if args.out else (
            ROOT / "results" / "pg_recovery_tdd.json")
        out.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        for r in doc["rows"]:
            print("  %-14s %-4s t_dd=%-5d mk=%-8s det=%-4s stall=%s"
                  % (r["scenario"], r["kind"], r["t_dd"],
                     r.get("makespan") or r.get("reason"), r.get("n_detect"),
                     r.get("stall_cy")))
        print("Wrote %s (%ss)" % (out, doc["meta"]["elapsed_s"]))
        return
    doc = run(names=args.only, jobs=args.jobs)
    out = Path(args.out) if args.out else OUT
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print("Wrote %s (%d rows, %ss)"
          % (out, len(doc["rows"]), doc["meta"]["elapsed_s"]))
    for m0 in M0_LIST:
        for routing in ROUTINGS:
            print("\n=== m0=%d  routing=%s ===" % (m0, routing))
            for s in doc["summary"]:
                if s["m0"] != m0 or s["routing"] != routing:
                    continue
                if not s["n_ok"]:
                    print("  %-5s  0/%d  %s" % (s["kind"], s["n_scen_total"],
                                                s["reasons"]))
                    continue
                print("  %-5s ok=%2d/%2d area=%.3f worst=%8.0fns med=%8.0fns "
                      "load=%3d sac_worst=%d ord=%d/%d det_worst=%d"
                      % (s["kind"], s["n_ok"], s["n_scen_total"], s["area"],
                         s["t_e2e_ns_worst"], s["t_e2e_ns_med"],
                         s["max_load_med"], s["sac_worst"],
                         s["n_ordered_ok"], s["n_ok"], s["detect_worst"]))


if __name__ == "__main__":
    main()
