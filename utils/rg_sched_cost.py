#!/usr/bin/env python3
"""Analytic resource model for the mesh scheduler family.

Two numbers per algorithm, both derived from structure rather than fitted:

  area_norm       scheduler state bits + comparators -> silicon, normalized to
                  one IQ-XY router area and amortized over the 48 nodes, so it
                  can be added straight onto `router_area()`'s arbiter term.
                  Calibrated so `greedy_ff` lands on the existing
                  ARB_AREA["ca"] = 0.05 of utils/dse_rg_noc_8x6.py.

  t_sched_cycles  timing feasibility. An arbitration decision has a
                  combinational depth; decisions that DEPEND on each other
                  must serialize. This is charged back onto the makespan, which
                  is what stops the model from rewarding an algorithm that is
                  only fast because it is unbuildable.

The dependent-step count is the discriminator between the two classes:

  slot / phase (bvn, mwm, latin, islip, pim)
      One LDPS is built per round from a link-used bitmap; all links test in
      parallel, so a round is one dependent step (× `iters` for the iSLIP
      iterations). Steps ≈ n_rounds ≈ max_e load(e) — tens.
  pipelined (bcfs, greedy_ff)
      Flow k's earliest-feasible offset depends on the placement of flows
      1..k-1, so the steps are Θ(n_flows) — thousands for an all-to-all.

That is the real cost of dropping the convoy effect, and it is why the Pareto
front is not simply "pipelined dominates".
"""

from __future__ import annotations

import math
from typing import Any

# --- widths (bits) ---------------------------------------------------------
W_T = 12         # time stamp, enough for a few thousand cycles
W_D = 6          # per-link demand / load counter
DEPTH = 16       # intervals retained per resource in an interval map

# --- technology-ish coefficients ------------------------------------------
FLOP_BIT = 1.0            # a sequential state bit  (unit)
ROM_BIT = 0.15            # a ROM/SRAM bit is far denser than a flop
CMP_BIT_EQ = 0.6          # a 1-bit comparator slice, in flop-bit equivalents
LEVELS_PER_CYCLE = 12     # gate levels that fit in one NoC clock period

# Calibration: pick BIT_AREA so greedy_ff == 0.05 (see module docstring).
# Solved once for the mesh reference point and then held fixed for every
# algorithm and both topologies.
_CAL_ALGO = "greedy_ff"
_CAL_TARGET = 0.05


def _ceil_log2(x: int) -> int:
    return max(1, math.ceil(math.log2(max(2, x))))


def state_bits(algo: str, *, n_links: int, n_nodes: int, n_flows: int,
               n_rounds: int | None, iters: int) -> dict[str, float]:
    """Bit-equivalent breakdown of the centralized scheduler's state."""
    L, N, F = n_links, n_nodes, max(1, n_flows)
    W_f = _ceil_log2(F)
    ramps = 2 * N                       # inject + eject ramp resources
    interval_tbl = (L + ramps) * DEPTH * 2 * W_T
    free_at_reg = (L + ramps) * W_T     # one register per resource
    link_bitmap = L                     # "used in this round"
    cand_list = F * W_f                 # residual / eligible flow set
    R_max = max(1, n_rounds or 1)

    b: dict[str, float] = {}
    if algo == "greedy_ff":
        b["interval_tables"] = interval_tbl
        b["candidate_list"] = cand_list
    elif algo == "bcfs":
        # best-so-far + working copy, plus pressure weights per flow
        b["interval_tables"] = 2 * interval_tbl
        b["pressure_weights"] = F * W_D
        b["candidate_list"] = cand_list
    elif algo == "bvn_mesh":
        b["free_at_registers"] = free_at_reg
        b["round_link_bitmap"] = link_bitmap
        b["residual_set"] = cand_list
        b["round_table"] = R_max * W_f
    elif algo == "mwm_mesh":
        b["free_at_registers"] = free_at_reg
        b["round_link_bitmap"] = link_bitmap
        b["residual_set"] = cand_list
        b["round_table"] = R_max * W_f
        b["demand_matrix"] = N * N * W_D
        b["link_load_counters"] = L * W_D
    elif algo == "latin_mesh":
        b["free_at_registers"] = free_at_reg
        b["round_link_bitmap"] = link_bitmap
        b["rom_rounds"] = (N - 1) * N * _ceil_log2(N) * ROM_BIT
    elif algo == "islip_mesh":
        b["free_at_registers"] = free_at_reg
        b["round_link_bitmap"] = link_bitmap
        b["residual_set"] = cand_list
        b["rr_pointers"] = L * W_f
        b["request_bitmap"] = L * min(F, 128)
        b["grant_vector"] = L
    elif algo == "pim_mesh":
        b["free_at_registers"] = free_at_reg
        b["round_link_bitmap"] = link_bitmap
        b["residual_set"] = cand_list
        b["request_bitmap"] = L * min(F, 128)
        b["grant_vector"] = L
        b["lfsr"] = 32
    else:
        raise ValueError(algo)
    return b


def comparators(algo: str, *, n_links: int, n_nodes: int, n_flows: int,
                iters: int) -> dict[str, int]:
    """Parallel comparator slices instantiated (counted in bits of width)."""
    L, N, F = n_links, n_nodes, max(1, n_flows)
    ramps = 2 * N
    c: dict[str, int] = {}
    if algo in ("greedy_ff", "bcfs"):
        # every retained interval on every resource is compared in parallel
        c["interval_compare"] = (L + ramps) * DEPTH * W_T
        c["max_reduce"] = (L + ramps) * W_T
        if algo == "bcfs":
            c["weight_sort"] = F * W_D
    elif algo in ("bvn_mesh", "latin_mesh"):
        c["free_at_compare"] = (L + ramps) * W_T
    elif algo == "mwm_mesh":
        c["free_at_compare"] = (L + ramps) * W_T
        c["weight_argmax"] = F * W_D
    elif algo in ("islip_mesh", "pim_mesh"):
        c["free_at_compare"] = (L + ramps) * W_T
        c["rr_arbiters"] = L * max(1, iters) * _ceil_log2(F)
    else:
        raise ValueError(algo)
    return c


def gate_levels(algo: str, *, n_links: int, n_flows: int, iters: int,
                mean_hops: float = 6.0) -> int:
    """Combinational depth of ONE arbitration decision."""
    F = max(2, n_flows)
    if algo == "islip_mesh":
        # per-link RR tree + the path-wide AND reduce that makes a mesh accept
        req_per_link = max(2, int(F * mean_hops / max(1, n_links)))
        return max(1, iters) * (_ceil_log2(req_per_link)
                                + _ceil_log2(int(mean_hops) + 1))
    if algo == "pim_mesh":
        req_per_link = max(2, int(F * mean_hops / max(1, n_links)))
        return max(1, iters) * (_ceil_log2(req_per_link) + 2
                                + _ceil_log2(int(mean_hops) + 1))
    if algo in ("bvn_mesh", "mwm_mesh"):
        # link-bitmap AND over the path + (mwm) a weight argmax tree
        d = _ceil_log2(int(mean_hops) + 1) + 2
        return d + (_ceil_log2(F) if algo == "mwm_mesh" else 0)
    if algo == "latin_mesh":
        return 2                      # ROM read + bitmap AND
    if algo in ("greedy_ff", "bcfs"):
        # DEPTH-way interval compare, max-reduce over the path, then iterate
        return _ceil_log2(DEPTH) + W_T // 3 + _ceil_log2(int(mean_hops) + 1)
    raise ValueError(algo)


def dependent_steps(algo: str, *, n_flows: int, n_rounds: int | None,
                    iters: int) -> int:
    """Arbitration steps that cannot be overlapped (see module docstring)."""
    if algo in ("greedy_ff", "bcfs"):
        mult = 5 if algo == "bcfs" else 1     # bcfs re-runs multi-start
        return max(1, n_flows) * mult
    return max(1, n_rounds or 1) * (max(1, iters)
                                    if algo in ("islip_mesh", "pim_mesh")
                                    else 1)


def _bit_equiv(bits: dict[str, float], cmps: dict[str, int]) -> float:
    return (sum(bits.values()) * FLOP_BIT
            + sum(cmps.values()) * CMP_BIT_EQ)


def _calibrate(n_links: int, n_nodes: int) -> float:
    """Bit-equivalent -> normalized-per-node-area coefficient."""
    ref = _bit_equiv(
        state_bits(_CAL_ALGO, n_links=n_links, n_nodes=n_nodes,
                   n_flows=2256, n_rounds=None, iters=1),
        comparators(_CAL_ALGO, n_links=n_links, n_nodes=n_nodes,
                    n_flows=2256, iters=1))
    return _CAL_TARGET * n_nodes / ref


def sched_cost(algo: str, topo, n_flows: int, *, iters: int = 1,
               n_rounds: int | None = None, mean_hops: float = 6.0,
               lam: float = 1.0) -> dict[str, Any]:
    """Area + timing cost of the centralized scheduler for one workload."""
    L = len(topo.directed_links)
    N = topo.n
    bits = state_bits(algo, n_links=L, n_nodes=N, n_flows=n_flows,
                      n_rounds=n_rounds, iters=iters)
    cmps = comparators(algo, n_links=L, n_nodes=N, n_flows=n_flows,
                       iters=iters)
    coeff = _calibrate(L, N)
    eq = _bit_equiv(bits, cmps)
    area = coeff * eq / N
    lv = gate_levels(algo, n_links=L, n_flows=n_flows, iters=iters,
                     mean_hops=mean_hops)
    steps = dependent_steps(algo, n_flows=n_flows, n_rounds=n_rounds,
                            iters=iters)
    cyc_per_step = max(1, math.ceil(lv / LEVELS_PER_CYCLE))
    return {
        "algo": algo,
        "n_links": L,
        "bits": round(sum(bits.values())),
        "bits_breakdown": {k: round(v) for k, v in bits.items()},
        "comparator_bits": sum(cmps.values()),
        "comparators_breakdown": cmps,
        "bit_equiv": round(eq),
        "area_norm": round(area, 4),
        "area_norm_scaled": round(lam * area, 4),
        "area_total_norm": round(area * N, 3),
        "gate_levels": lv,
        "cycles_per_step": cyc_per_step,
        "dependent_steps": steps,
        "t_sched_cycles": steps * cyc_per_step,
        "lam": lam,
    }


def pareto_front(points: list[tuple[float, float, Any]]
                 ) -> list[tuple[float, float, Any]]:
    """Minimize both coordinates; returns the non-dominated subset."""
    out = []
    for x, y, tag in sorted(points, key=lambda p: (p[0], p[1])):
        if all(not (a <= x and b <= y and (a < x or b < y))
               for a, b, _ in points):
            out.append((x, y, tag))
    best = math.inf
    front = []
    for x, y, tag in sorted(out, key=lambda p: (p[0], p[1])):
        if y < best:
            best = y
            front.append((x, y, tag))
    return front


def lam_winner(rows: list[dict[str, Any]], lam: float, *,
               makespan_key: str = "makespan",
               area_key: str = "area_norm") -> dict[str, Any] | None:
    """Who minimizes makespan_norm + lam * area_norm at this trade-off weight.

    Makespan is normalized by the best makespan in the set so the two axes are
    commensurate and `lam` reads as "cycles I would pay per unit of area".
    """
    if not rows:
        return None
    mk_ref = min(r[makespan_key] for r in rows) or 1
    scored = [(r[makespan_key] / mk_ref + lam * r[area_key], r) for r in rows]
    return min(scored, key=lambda t: t[0])[1]


if __name__ == "__main__":
    from rg_topo import Topology
    from rg_mesh_sched import ALL_ALGOS

    for kind in ("mesh", "torus"):
        topo = Topology(kind)
        print(f"=== {kind}  L={len(topo.directed_links)} ===")
        print(f"{'algo':12} {'bits':>8} {'cmp':>7} {'area':>7} "
              f"{'lv':>3} {'steps':>6} {'T_sched':>8}")
        for algo in ALL_ALGOS:
            c = sched_cost(algo, topo, 2256, iters=2, n_rounds=110)
            print(f"{algo:12} {c['bits']:>8} {c['comparator_bits']:>7} "
                  f"{c['area_norm']:>7.4f} {c['gate_levels']:>3} "
                  f"{c['dependent_steps']:>6} {c['t_sched_cycles']:>8}")
