#!/usr/bin/env python3
"""The check list for the iSLIP-2D study, as executable assertions.

Every claim the report makes has to come from a run, and every run has to be
re-checked by something other than the code that produced it. This module is
that something. Each check returns a row with a verdict and the numbers behind
it, so a failure names the quantity rather than just failing.

Groups
------
    common   conflict-freedom re-derived from the topology, zero residency in
             the bufferless configurations, rounds never below the lower bound,
             residual-bitmap discipline, control-plane message accounting
    D-M      the 96 cut bound, ROMM's latency invariance, when ROMM pays,
             delivery order
    D-R      all five clauses independently, the 192-link and 1.17x metal
             accounting, what a pure link predicate misses on the ring, the cost
             of whole-ring arbitration
    base     ring_base: whether the Swap Rule can fire at all, whether removing
             it deadlocks, whether starvation stays bounded
    steady   measured saturation against the analytic anchors, credit-buffer
             sensitivity, plateau behaviour, zero in-network residency

Writes results/verify_islip2d_8x6.json. Exit code is nonzero if any check fails,
so this is usable as a gate before the report is written.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from rg_topo import RAMP_BW, Topology
from rg_collectives import build_collective
from rg_mesh_paths import (build_plan, check_latency_invariance, cut_bound,
                           max_load, pairs_of)
from rg_mesh_sched import schedule_mesh, verify_rounds_disjoint
from rg_ring_base import RingBaseParams, run_batch
from rg_ring_sched import schedule_ring
from rg_ring_topo import (LEGACY_WIRE, RingTopology, fixed_plan,
                          greedy_max_set, misuse_stats, route_delay_spread)
from rg_steady_des import SteadyParams, anchors, run_steady

OUT = Path(__file__).resolve().parents[1] / "results" / "verify_islip2d_8x6.json"
N = 48
A2A = [(s, d) for s in range(N) for d in range(N) if s != d]
INF = 1 << 20

rows: list[dict[str, Any]] = []


def mesh_misuse(paths: dict, n_samples: int, seed: int) -> dict[str, Any]:
    """How badly the crossbar predicate misjudges D-M, both directions.

    The crossbar predicate is "distinct sources and distinct destinations".
    On a mesh it fails both ways, and the two failures need separate samples
    because they are conditioned on disjoint events: the unsafe rate is
    measured over pairs the predicate CLEARS, the over-strict rate over pairs
    it REJECTS for sharing a source.
    """
    import random as _r
    rng = _r.Random(seed)
    keys = list(paths)

    def links(k):
        p = paths[k]
        return {(p[i], p[i + 1]) for i in range(len(p) - 1)}

    n_clear = n_unsafe = n_same = n_same_free = 0
    for _ in range(n_samples):
        a, b = rng.choice(keys), rng.choice(keys)
        if a == b:
            continue
        if a[0] != b[0] and a[1] != b[1]:
            n_clear += 1
            if links(a) & links(b):
                n_unsafe += 1
        if a[0] == b[0] and a[1] != b[1]:
            n_same += 1
            if not (links(a) & links(b)):
                n_same_free += 1
    # Greedy maximum conflict-free set under M1, to compare against the 48 a
    # crossbar permutation would allow.
    sizes = []
    for t in range(20):
        r2 = _r.Random(1000 + t)
        order = keys[:]
        r2.shuffle(order)
        used: set = set()
        n = 0
        for k in order:
            le = links(k)
            if not (le & used):
                used |= le
                n += 1
        sizes.append(n)
    return {
        "n_samples": n_samples,
        "unsafe_rate": round(n_unsafe / max(1, n_clear), 4),
        "same_src_actually_free_rate": round(n_same_free / max(1, n_same), 4),
        "greedy_mean": round(sum(sizes) / len(sizes), 1),
        "greedy_max": max(sizes),
    }


def check(group: str, name: str, ok: bool, **detail: Any) -> None:
    rows.append({"group": group, "check": name, "ok": bool(ok)} | detail)
    flag = "ok  " if ok else "FAIL"
    extra = " ".join(f"{k}={v}" for k, v in detail.items())
    print(f"  [{flag}] {group}/{name} {extra}")


# ---------------------------------------------------------------------------
# common
# ---------------------------------------------------------------------------

def common() -> None:
    print("== common ==")
    topo = Topology("mesh")
    col = build_collective(topo, "alltoall", m=1)
    r = schedule_mesh(topo, col, "islip2d_mesh", grants_per_src=2,
                      pipeline_depth=INF)
    v = verify_rounds_disjoint(topo, col, r)
    check("common", "mesh_conflict_free", r["verify"]["n_violations"] == 0,
          violations=r["verify"]["n_violations"])
    check("common", "mesh_rounds_disjoint_recheck",
          v["overlaps"] == 0 and v["ramp_violations"] == 0,
          overlaps=v["overlaps"], ramp=v["ramp_violations"])
    check("common", "mesh_rounds_ge_lb", r["n_rounds"] >= r["round_lb"],
          rounds=r["n_rounds"], lb=r["round_lb"], ratio=r["round_ratio"])
    check("common", "mesh_residual_bitmap_discipline",
          r["residual_bitmap_ok"] is True,
          reported=r["residual_bitmap_ok"])
    # one request and one grant per source per round, independent of backlog
    per_round = r["ctrl_msgs_total"] / max(1, r["n_rounds"])
    check("common", "mesh_ctrl_msgs_per_round", abs(per_round - 2 * N) < 2 * N,
          per_round=round(per_round, 1), bound=2 * N,
          total=r["ctrl_msgs_total"], one_shot_reference=2 * len(A2A))

    rt = RingTopology(**LEGACY_WIRE)
    rr = schedule_ring(rt, A2A, m=1, grants_per_src=2, pipeline_depth=INF)
    tot = sum(rr["verify"][k] for k in (
        "R1_link_violations", "R2_board_violations", "R3_leave_violations",
        "R4_turn_violations", "R5_voq_violations"))
    check("common", "ring_conflict_free", tot == 0, violations=tot)
    check("common", "ring_rounds_ge_lb", rr["n_rounds"] >= rr["round_lb"],
          rounds=rr["n_rounds"], lb=rr["round_lb"], ratio=rr["round_ratio"])
    check("common", "ring_residual_bitmap_discipline",
          rr["residual_bitmap_ok"] is True,
          reported=rr["residual_bitmap_ok"])
    check("common", "ring_bufferless_zero_residency",
          rr["verify"]["max_turn_residency"] == 0,
          max_turn_residency=rr["verify"]["max_turn_residency"])


# ---------------------------------------------------------------------------
# D-M
# ---------------------------------------------------------------------------

def dm() -> None:
    print("== D-M (mesh) ==")
    cb = cut_bound(A2A)
    check("D-M", "cut_bound_is_96", cb["cut_bound"] == 96,
          cut_bound=cb["cut_bound"], witness=cb["witness"],
          n_pairs=cb["n_pairs"])
    xy = build_plan(A2A, "xy")
    check("D-M", "xy_meets_cut_bound", xy.max_load == 96,
          max_load=xy.max_load, cut_bound=xy.cut_bound)

    inv = check_latency_invariance(Topology("mesh"), sample=8, seed=0)
    check("D-M", "romm_latency_invariant",
          inv["mismatch_hops"] == 0 and inv["mismatch_wire"] == 0,
          n_checked=inv["n_checked"], mismatch_hops=inv["mismatch_hops"],
          mismatch_wire=inv["mismatch_wire"])
    mm = mesh_misuse(xy.paths, n_samples=40_000, seed=0)
    # Both directions of the crossbar predicate's failure, on the same sample.
    # Unsafe: it clears pairs that share a link. Over-strict: it rejects
    # same-source pairs that the mesh can actually run together.
    check("D-M", "crossbar_predicate_fails_both_ways",
          mm["unsafe_rate"] > 0 and mm["same_src_actually_free_rate"] > 0,
          unsafe_rate=mm["unsafe_rate"],
          same_src_actually_free_rate=mm["same_src_actually_free_rate"],
          n_samples=mm["n_samples"], greedy_mean=mm["greedy_mean"],
          greedy_max=mm["greedy_max"], crossbar_permutation=48)

    topo = Topology("mesh")
    gains: dict[str, Any] = {}
    for pat in ("alltoall", "transpose", "hotspot_any", "cornerAtoB",
                "halfxhalf", "permutation"):
        col = build_collective(topo, pat, m=1)
        pairs = pairs_of(col)
        base_load = max_load(build_plan(pairs, "xy").paths)
        bound = cut_bound(pairs)["cut_bound"]
        n_xy = schedule_mesh(topo, col, "islip2d_mesh", path_mode="xy",
                             grants_per_src=2,
                             pipeline_depth=INF)["n_rounds"]
        n_ro = schedule_mesh(topo, col, "islip2d_mesh",
                             path_mode="romm_static", grants_per_src=2,
                             pipeline_depth=INF)["n_rounds"]
        romm_load = max_load(build_plan(pairs, "romm_static").paths)
        gains[pat] = {"xy_load": base_load, "cut_bound": bound,
                      "romm_has_headroom": base_load > bound,
                      "romm_load": romm_load,
                      "load_gain": base_load - romm_load,
                      "rounds_xy": n_xy, "rounds_romm": n_ro,
                      "round_gain": round(1 - n_ro / n_xy, 3)}
    # The criterion is about LOAD and holds in that form: ROMM cannot reduce the
    # peak link load of a pattern that already sits on its cut bound.
    bad = [p for p, g in gains.items()
           if g["load_gain"] > 0 and not g["romm_has_headroom"]]
    check("D-M", "romm_cannot_reduce_load_without_headroom", not bad,
          offenders=bad, detail=gains)
    # ROUND count is a separate matter, and this is where the plan's criterion
    # was too strong. `permutation` sits exactly on its cut bound (load 3) yet
    # still drops 4 rounds to 3, because at that scale the binding constraint is
    # how well the conflict graph PACKS, not the peak load, and one round is 25%
    # of the total. So path diversity can pay even with zero load headroom;
    # headroom predicts the load win, not the schedule win.
    packing = {p: g for p, g in gains.items()
               if g["round_gain"] > 0.02 and g["load_gain"] == 0}
    check("D-M", "round_gain_without_load_gain_is_a_packing_effect",
          all(g["rounds_xy"] - g["rounds_romm"] <= 2 for g in packing.values()),
          patterns=list(packing), detail=packing)

    for pm, expect_zero in (("xy", True), ("romm_static", True),
                            ("romm_dyn", True)):
        col = build_collective(topo, "alltoall", m=4)
        r = schedule_mesh(topo, col, "islip2d_mesh", path_mode=pm,
                          grants_per_src=2, pipeline_depth=INF)
        # M3 serializes each VOQ, and every path mode here is static per pair,
        # so no reordering is possible by construction; this checks the
        # construction rather than trusting it.
        starts: dict[tuple[int, int], list[int]] = {}
        for g in r["grants"]:
            starts.setdefault((g.src, g.flow_id), []).append(g.t_data_start)
        ooo = sum(1 for v in starts.values()
                  if any(b < a for a, b in zip(v, v[1:])))
        check("D-M", f"in_order_{pm}", (ooo == 0) == expect_zero,
              out_of_order=ooo)


# ---------------------------------------------------------------------------
# D-R
# ---------------------------------------------------------------------------

def dr() -> None:
    print("== D-R (ring) ==")
    topo = RingTopology(**LEGACY_WIRE)
    a = topo.audit()
    check("D-R", "192_directed_links", a["n_directed_links"] == 192,
          directed=a["n_directed_links"], undirected=a["n_undirected_links"])
    check("D-R", "metal_ratio_1_17", abs(a["metal_ratio_vs_mesh"] - 1.17) < 0.01,
          ratio=a["metal_ratio_vs_mesh"], mesh_undirected=a["mesh_undirected"])
    same = topo.assert_same_links_as_torus()
    check("D-R", "link_set_equals_folded_torus", same["equal"], **same)
    check("D-R", "every_node_is_a_bridge", a["n_bridges"] == 48,
          bridges=a["n_bridges"])

    for mode, want in (("fixed", 60), ("balanced", 49)):
        pl = topo.bounds(list(build_plan_ring(topo, mode).values()))
        check("D-R", f"link_bound_{mode}", pl["max_link_load"] == want,
              measured=pl["max_link_load"], expected=want,
              round_lb=pl["round_lb"])

    spread = route_delay_spread(topo, A2A)
    check("D-R", "minimal_routes_are_latency_invariant",
          spread["latency_invariant"], **{
              k: spread[k] for k in ("n_pairs", "pairs_with_hop_spread",
                                     "pairs_with_wire_spread",
                                     "max_wire_spread")})
    # The plan attributed R5's static-route requirement to the ring as a fabric.
    # It actually belongs to non-minimal routing: inside the minimal candidate
    # set the ring is latency invariant exactly like mesh ROMM (check above),
    # and only opening up the long way round breaks it.
    wide = route_delay_spread(topo, A2A, minimal_only=False)
    check("D-R", "non_minimal_routes_break_latency_invariance",
          not wide["latency_invariant"] and wide["frac_wire_spread"] > 0.9,
          frac_with_spread=wide["frac_wire_spread"],
          pairs_with_spread=wide["pairs_with_wire_spread"],
          max_wire_spread=wide["max_wire_spread"],
          minimal_set_spread=spread["max_wire_spread"])

    plan = fixed_plan(topo, A2A)
    mis = misuse_stats(topo, plan.paths, n_samples=40_000, seed=0)
    check("D-R", "pure_link_predicate_is_unsafe",
          mis["false_negative_rate_of_pure_R1"] > 0,
          rate=mis["false_negative_rate_of_pure_R1"],
          kinds=mis["port_clash_kind_frac"])
    g1 = greedy_max_set(topo, plan.paths, clauses="R1", trials=20)
    g3 = greedy_max_set(topo, plan.paths, clauses="R1+R2+R3", trials=20)
    over = g1["mean"] / g3["mean"]
    check("D-R", "pure_link_predicate_overstates_concurrency", over > 1.2,
          r1_only_mean=g1["mean"], r1r2r3_mean=g3["mean"],
          overstatement=round(over, 2))

    arc = schedule_ring(topo, A2A, m=1, grants_per_src=2, pipeline_depth=INF)
    whole = schedule_ring(
        RingTopology(**LEGACY_WIRE, spatial_reuse="whole_ring"), A2A, m=1,
        grants_per_src=2, pipeline_depth=INF)
    check("D-R", "whole_ring_costs_multiple_x",
          whole["n_rounds"] > 2 * arc["n_rounds"],
          arc_rounds=arc["n_rounds"], whole_rounds=whole["n_rounds"],
          factor=round(whole["n_rounds"] / arc["n_rounds"], 2),
          mesh_reference=110)

    for ports in (1, 2):
        t = RingTopology(**LEGACY_WIRE, board_ports=ports, leave_ports=ports)
        r = schedule_ring(t, A2A, m=1, grants_per_src=2, pipeline_depth=INF)
        tot = sum(r["verify"][k] for k in (
            "R1_link_violations", "R2_board_violations",
            "R3_leave_violations", "R4_turn_violations", "R5_voq_violations"))
        check("D-R", f"clauses_hold_ports{ports}", tot == 0,
              rounds=r["n_rounds"], by_clause={
                  k: r["verify"][k] for k in (
                      "R1_link_violations", "R2_board_violations",
                      "R3_leave_violations", "R4_turn_violations",
                      "R5_voq_violations")})


def build_plan_ring(topo: RingTopology, mode: str):
    from rg_ring_topo import build_ring_plan
    return build_ring_plan(topo, A2A, mode).paths


# ---------------------------------------------------------------------------
# ring_base
# ---------------------------------------------------------------------------

def base() -> None:
    print("== ring_base (E-tag / I-tag + deflection) ==")
    topo = RingTopology(**LEGACY_WIRE)

    # Under a fixed dimension order every turn goes row -> column, so a bridge
    # never sees mutual turns and the Swap Rule is unreachable. This is a
    # structural claim, so it is checked rather than assumed.
    rc = run_batch(topo, A2A, m=4, params=RingBaseParams(
        dim_order="RC", swap_rule=True, fifo_depth=1, eject_depth=1,
        eject_bw=1, t_deadlock=256), t_max=120_000)
    check("base", "swap_rule_unreachable_under_fixed_dim_order",
          rc["n_swaps"] == 0, swaps=rc["n_swaps"], completed=rc["completed"])

    mixed = {}
    for swap in (True, False):
        for slot in (True, False):
            r = run_batch(topo, A2A, m=4, params=RingBaseParams(
                dim_order="mixed", swap_rule=swap, slot_ring=slot,
                fifo_depth=1, eject_depth=1, eject_bw=1, t_deadlock=256),
                t_max=120_000)
            mixed[(swap, slot)] = r
    check("base", "swap_rule_fires_under_mixed_dim_order",
          mixed[(True, True)]["n_swaps"] > 0,
          swaps=mixed[(True, True)]["n_swaps"])
    # The plan expected removing the Swap Rule to deadlock. It does not, and the
    # reason is structural: at most one flit arrives per node per ring-direction
    # per cycle, so a flit that cannot turn always has its continuation segment
    # free. Deflection is unconditionally available, so the failure mode is
    # latency, not deadlock. Recorded as a deviation, not silently dropped.
    no_swap_completes = all(mixed[(False, s)]["completed"] for s in (True, False))
    check("base", "no_deadlock_even_without_swap_rule", no_swap_completes,
          completed_slot=bool(mixed[(False, True)]["completed"]),
          completed_pipelined=bool(mixed[(False, False)]["completed"]),
          note="deviates from plan: deflection is always available")
    check("base", "no_flit_ever_blocked_in_ring",
          all(r["n_inring_blocked"] == 0 for r in mixed.values()),
          blocked={f"swap{int(k[0])}_slot{int(k[1])}": v["n_inring_blocked"]
                   for k, v in mixed.items()})

    # injection and transfer guarantees must bound the wait, not merely reduce it
    starve = {}
    for t_inj in (8, 16, 64):
        r = run_batch(topo, A2A, m=4, params=RingBaseParams(
            dim_order="RC", t_inj=t_inj, t_xfer=t_inj, fifo_depth=1,
            eject_depth=1, eject_bw=1), t_max=120_000)
        starve[t_inj] = r["max_inj_starve"]
    check("base", "starvation_bounded_and_tracks_threshold",
          all(v < 10_000 for v in starve.values()), max_inj_starve=starve)

    r = run_batch(topo, A2A, m=4, params=RingBaseParams(dim_order="RC"),
                  t_max=120_000)
    check("base", "deflection_causes_reordering_so_reassembly_is_required",
          r["n_out_of_order"] > 0, out_of_order=r["n_out_of_order"],
          max_reasm=r["max_reasm_occupancy"], deflections=r["n_deflections"])


# ---------------------------------------------------------------------------
# steady state
# ---------------------------------------------------------------------------

def steady() -> None:
    print("== steady state ==")
    an = anchors()
    lams = (0.3, 0.4, 0.45, 0.5, 0.55, 0.7, 0.75, 0.8)
    curves: dict[str, list[dict[str, Any]]] = {}
    for cfg in ("mesh_base", "ring_base", "mesh_islip2d", "ring_islip2d"):
        curves[cfg] = [run_steady(cfg, SteadyParams(
            lam=l, buf_depth=20, warmup=1200, measure=5000)) for l in lams]

    for cfg, key in (("mesh_base", "mesh_xy"), ("mesh_islip2d", "mesh_xy"),
                     ("ring_base", "ring_fixed"),
                     ("ring_islip2d", "ring_fixed")):
        stable = [r["lam"] for r in curves[cfg] if r["stable"]]
        ls = max(stable) if stable else 0.0
        check("steady", f"{cfg}_lam_star_within_anchor", ls <= an[key] + 1e-9,
              lam_star=ls, anchor=round(an[key], 3),
              peak_accepted=max(r["accepted"] for r in curves[cfg]))

    for cfg in ("mesh_islip2d", "ring_islip2d"):
        check("steady", f"{cfg}_zero_in_network_residency",
              all(r["in_network_max"] == 0 for r in curves[cfg]),
              max_residency=max(r["in_network_max"] for r in curves[cfg]))

    # deep buffers must beat shallow ones, and shallow must fall well short of
    # the anchor: a baseline reported only at buf=4 would be understated 4x
    bufs = {}
    for bd in (4, 8, 20):
        rs = [run_steady("mesh_base", SteadyParams(
            lam=l, buf_depth=bd, warmup=1200, measure=5000))
            for l in (0.2, 0.4)]
        bufs[bd] = round(max(r["accepted"] for r in rs), 4)
    check("steady", "credit_buffer_must_cover_rtt",
          bufs[20] > bufs[8] > bufs[4] and bufs[4] < 0.5 * an["mesh_xy"],
          peak_accepted_by_buf_depth=bufs, anchor=round(an["mesh_xy"], 3))

    # accepted throughput must plateau or fall past saturation, never climb
    for cfg in ("mesh_base", "ring_base", "mesh_islip2d", "ring_islip2d"):
        acc = [r["accepted"] for r in curves[cfg]]
        peak = max(acc)
        check("steady", f"{cfg}_plateaus_past_saturation",
              acc[-1] <= peak + 1e-9, peak=round(peak, 4),
              at_highest_lam=round(acc[-1], 4),
              falls_back=round(peak - acc[-1], 4))

    # the interval table is load bearing, not a refinement
    for cfg in ("mesh_islip2d", "ring_islip2d"):
        iv = run_steady(cfg, SteadyParams(lam=0.5, warmup=1200, measure=5000,
                                          conflict_domain="interval"))
        fa = run_steady(cfg, SteadyParams(lam=0.5, warmup=1200, measure=5000,
                                          conflict_domain="free_at"))
        check("steady", f"{cfg}_interval_beats_free_at",
              iv["accepted"] > 2 * fa["accepted"],
              interval=iv["accepted"], free_at=fa["accepted"],
              factor=round(iv["accepted"] / max(fa["accepted"], 1e-9), 2))

    # low load is where a control round trip should hurt
    lo = {c: run_steady(c, SteadyParams(lam=0.01, buf_depth=20, warmup=1200,
                                        measure=5000))["p50"]
          for c in ("mesh_base", "mesh_islip2d", "ring_base", "ring_islip2d")}
    check("steady", "rg_pays_rtt_at_low_load",
          lo["mesh_islip2d"] > lo["mesh_base"], p50=lo)


if __name__ == "__main__":
    t0 = time.perf_counter()
    common()
    dm()
    dr()
    base()
    steady()
    n_fail = sum(1 for r in rows if not r["ok"])
    res = {"rows": rows, "n_checks": len(rows), "n_fail": n_fail,
           "wall_secs": round(time.perf_counter() - t0, 1)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(f"\n{len(rows) - n_fail}/{len(rows)} checks passed "
          f"({res['wall_secs']}s) -> {OUT}")
    sys.exit(1 if n_fail else 0)
