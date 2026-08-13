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
W_FLIT = 128     # payload flit width, for pricing distributed buffering

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
               n_rounds: int | None, iters: int,
               n_ports: int = 0, n_rings: int = 0,
               conflict_domain: str = "free_at") -> dict[str, float]:
    """Bit-equivalent breakdown of the centralized scheduler's state.

    `n_ports` and `n_rings` are the ring fabric's extra resource classes: D-R
    arbitrates board and leave points, which do not exist on the mesh, and its
    grant pointers live per ring-direction rather than per link.
    """
    L, N, F = n_links, n_nodes, max(1, n_flows)
    W_f = _ceil_log2(F)
    ramps = 2 * N                       # inject + eject ramp resources
    R = L + ramps + n_ports
    interval_tbl = R * DEPTH * 2 * W_T
    free_at_reg = R * W_T               # one register per resource
    link_bitmap = L                     # "used in this round"
    cand_list = F * W_f                 # residual / eligible flow set
    R_max = max(1, n_rounds or 1)

    b: dict[str, float] = {}
    if algo in ("islip2d_mesh", "islip2d_ring"):
        # The residual VOQ bitmap is the request format: one bit per (source,
        # destination), re-sent every round until granted. It is the single
        # largest block and it is what buys "one request per source per round"
        # instead of one request per VOQ.
        b["residual_voq_bitmap"] = N * (N - 1)
        b["accept_pointers"] = N * _ceil_log2(N)
        if algo == "islip2d_mesh":
            b["grant_pointers"] = L * _ceil_log2(N)
            b["round_link_bitmap"] = link_bitmap
        else:
            # one grant pointer per ring-direction, not per link: a ring's arcs
            # are arbitrated as a unit, which is far fewer pointers than the
            # mesh needs (2*n_rings vs L)
            b["grant_pointers"] = 2 * max(1, n_rings) * _ceil_log2(N)
            b["arc_tables"] = 2 * max(1, n_rings) * N     # occupied-arc bitmap
            b["port_counters"] = n_ports * W_D
        if conflict_domain == "interval":
            # Priced as DEPTH retained (start, end) pairs per resource, the same
            # convention the rest of this module uses. `rg_steady_des._SlotMap`
            # implements it instead as a sliding occupancy bitmap about 340 bits
            # wide per server, which lands within a few percent of this and is
            # the cheaper structure to build, so the number below is not
            # flattering the interval domain.
            b["interval_tables"] = interval_tbl
        else:
            b["free_at_registers"] = free_at_reg
        return b
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
                iters: int, n_ports: int = 0, n_rings: int = 0,
                conflict_domain: str = "free_at") -> dict[str, int]:
    """Parallel comparator slices instantiated (counted in bits of width)."""
    L, N, F = n_links, n_nodes, max(1, n_flows)
    ramps = 2 * N
    c: dict[str, int] = {}
    if algo in ("islip2d_mesh", "islip2d_ring"):
        R = L + ramps + n_ports
        if conflict_domain == "interval":
            c["interval_compare"] = R * DEPTH * W_T
        c["free_at_compare"] = R * W_T
        n_arb = L if algo == "islip2d_mesh" else 2 * max(1, n_rings)
        c["grant_arbiters"] = n_arb * max(1, iters) * _ceil_log2(N)
        c["accept_arbiters"] = N * max(1, iters) * _ceil_log2(N)
        if algo == "islip2d_ring":
            # R4 pins the two phases together, so every candidate needs its
            # leave offset and board offset checked against one another
            c["turn_align_compare"] = N * 2 * W_T
        return c
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
                mean_hops: float = 6.0, conflict_domain: str = "free_at",
                n_nodes: int = 48) -> int:
    """Combinational depth of ONE arbitration decision.

    For the iterative matchers this is the depth of ONE iteration, not of all
    `iters` of them: an iteration reads the previous iteration's pointers, so
    iterations are dependent steps and `dependent_steps()` is where they are
    charged. Multiplying here as well would price them quadratically. `iters`
    stays in the signature because a caller passing it is asking about a
    specific configuration, and because the non-iterative algorithms below
    document by their absence that they ignore it.
    """
    F = max(2, n_flows)
    if algo in ("islip2d_mesh", "islip2d_ring"):
        # grant RR tree over sources, then the path-wide AND that makes an
        # accept, then (interval domain) the DEPTH-way interval compare
        d = _ceil_log2(n_nodes) + _ceil_log2(int(mean_hops) + 1)
        if algo == "islip2d_ring":
            d += 2                     # two-phase alignment on top of the AND
        if conflict_domain == "interval":
            d += _ceil_log2(DEPTH)
        return d
    if algo == "islip_mesh":
        # per-link RR tree + the path-wide AND reduce that makes a mesh accept
        req_per_link = max(2, int(F * mean_hops / max(1, n_links)))
        return _ceil_log2(req_per_link) + _ceil_log2(int(mean_hops) + 1)
    if algo == "pim_mesh":
        req_per_link = max(2, int(F * mean_hops / max(1, n_links)))
        return (_ceil_log2(req_per_link) + 2
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
    return max(1, n_rounds or 1) * (
        max(1, iters) if algo in ("islip_mesh", "pim_mesh", "islip2d_mesh",
                                  "islip2d_ring") else 1)


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
               lam: float = 1.0,
               conflict_domain: str = "free_at") -> dict[str, Any]:
    """Area + timing cost of the centralized scheduler for one workload."""
    L = len(topo.directed_links)
    N = topo.n
    rings = getattr(topo, "rings", None)
    n_rings = len(rings) if rings else 0
    # every node sits on one row ring and one column ring, each with its own
    # insertion and extraction point
    n_ports = 2 * 2 * N if n_rings else 0
    kw = {"n_ports": n_ports, "n_rings": n_rings,
          "conflict_domain": conflict_domain}
    bits = state_bits(algo, n_links=L, n_nodes=N, n_flows=n_flows,
                      n_rounds=n_rounds, iters=iters, **kw)
    cmps = comparators(algo, n_links=L, n_nodes=N, n_flows=n_flows,
                       iters=iters, **kw)
    coeff = _calibrate(L, N)
    eq = _bit_equiv(bits, cmps)
    area = coeff * eq / N
    lv = gate_levels(algo, n_links=L, n_flows=n_flows, iters=iters,
                     mean_hops=mean_hops, conflict_domain=conflict_domain,
                     n_nodes=N)
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


# ---------------------------------------------------------------------------
# What centralization removes: the distributed hardware, in the same currency
# ---------------------------------------------------------------------------

def distributed_cost(config: str, *, n_nodes: int = 48, buf_depth: int = 20,
                     num_vc: int = 1, n_ports_per_node: int = 5,
                     fifo_depth: int = 4, resv_tx: int = 1,
                     eject_depth: int = 4, reasm_depth: int = 16
                     ) -> dict[str, Any]:
    """Per-node storage and control the distributed baselines need.

    Priced in the same bit currency as `sched_cost` so the two sides of the
    argument are comparable. The point of the comparison is not that the
    arbiter is small -- it is that the distributed schemes pay for their
    autonomy in FLIT-width storage, which is one to two orders of magnitude
    more expensive per bit of decision-making.

    `mesh_base` pays the credit round trip: to keep a link busy, each input VC
    needs about as many flit slots as the credit RTT, which on H=7/V=9 links is
    15-19 cycles. That is a buffer sized by WIRE DELAY, not by traffic, and it
    is exactly what a reservation-based scheme does not need.

    `ring_base` pays per BRIDGE, and in a dimension-sliced 2D ring every one of
    the 48 nodes is a bridge between its row ring and its column ring. HiRD
    places these structures on a handful of bridge routers; here the count is
    48, so the baseline's overhead is amplified relative to the original
    proposal. That difference is a property of the topology, not of the
    mechanism, and it must be stated when quoting these numbers.
    """
    b: dict[str, float] = {}
    if config == "mesh_base":
        b["input_buffers"] = (n_nodes * n_ports_per_node * num_vc
                              * buf_depth * W_FLIT)
        b["credit_counters"] = n_nodes * n_ports_per_node * num_vc * W_D
        b["switch_alloc_pointers"] = (n_nodes * n_ports_per_node
                                      * _ceil_log2(n_ports_per_node))
    elif config == "ring_base":
        # 2 target rings per bridge
        b["transfer_fifos"] = n_nodes * 2 * fifo_depth * W_FLIT
        b["reserved_tx_buffers"] = n_nodes * 2 * resv_tx * W_FLIT
        b["eject_queues"] = n_nodes * eject_depth * W_FLIT
        b["reassembly_buffers"] = n_nodes * reasm_depth * W_FLIT
        # I-tag injection guarantee + E-tag transfer guarantee + deadlock timer
        b["starvation_counters"] = n_nodes * 3 * W_T
        b["itag_etag_state"] = n_nodes * 2 * 2       # per ring-direction flags
        b["swap_bypass_muxes"] = n_nodes * 2 * W_FLIT
    elif config in ("mesh_islip2d", "ring_islip2d"):
        # Zero station storage by construction: a granted transfer is rigid, so
        # nothing is ever held anywhere in the fabric. Sources hold packets in
        # queues they need anyway.
        pass
    else:
        raise ValueError(config)
    return {"config": config, "bits": round(sum(b.values())),
            "breakdown": {k: round(v) for k, v in b.items()},
            "bits_per_node": round(sum(b.values()) / n_nodes, 1)}


def centralization_ledger(*, buf_depth: int = 20, fifo_depth: int = 4,
                          n_rounds_mesh: int = 110, n_rounds_ring: int = 69,
                          n_flows: int = 2256) -> dict[str, Any]:
    """Side-by-side: what each configuration spends, and on what."""
    from rg_topo import Topology
    from rg_ring_topo import LEGACY_WIRE, RingTopology
    mesh, ring = Topology("mesh"), RingTopology(**LEGACY_WIRE)
    out: dict[str, Any] = {}
    for cfg, topo, algo, nr in (
            ("mesh_base", mesh, None, None),
            ("ring_base", ring, None, None),
            ("mesh_islip2d", mesh, "islip2d_mesh", n_rounds_mesh),
            ("ring_islip2d", ring, "islip2d_ring", n_rounds_ring)):
        d = distributed_cost(cfg, buf_depth=buf_depth, fifo_depth=fifo_depth)
        row: dict[str, Any] = {"distributed_bits": d["bits"],
                               "distributed_breakdown": d["breakdown"]}
        if algo:
            for dom in ("free_at", "interval"):
                c = sched_cost(algo, topo, n_flows, iters=1, n_rounds=nr,
                               conflict_domain=dom)
                row[f"arbiter_bits_{dom}"] = c["bits"]
                row[f"arbiter_breakdown_{dom}"] = c["bits_breakdown"]
                row[f"gate_levels_{dom}"] = c["gate_levels"]
                row[f"t_sched_{dom}"] = c["t_sched_cycles"]
            row["total_bits"] = row["distributed_bits"] + \
                row["arbiter_bits_interval"]
        else:
            row["total_bits"] = row["distributed_bits"]
        out[cfg] = row
    for pair in (("mesh_base", "mesh_islip2d"), ("ring_base", "ring_islip2d")):
        a, b = pair
        out[f"{b}_vs_{a}"] = {
            "storage_removed_bits": out[a]["distributed_bits"],
            "arbiter_added_bits": out[b]["arbiter_bits_interval"],
            "net_bits": (out[b]["total_bits"] - out[a]["total_bits"]),
            "ratio": (round(out[a]["total_bits"] / out[b]["total_bits"], 2)
                      if out[b]["total_bits"] else None),
        }
    return out


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
    import json

    from rg_topo import Topology
    from rg_ring_topo import RingTopology
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

    print("\n=== the two iSLIP-2D arbiters ===")
    print(f"{'algo':14} {'domain':9} {'bits':>8} {'cmp':>8} {'area':>7} "
          f"{'lv':>4} {'T_sched':>8}")
    for algo, topo, nr in (("islip2d_mesh", Topology("mesh"), 110),
                           ("islip2d_ring", RingTopology(**LEGACY_WIRE), 69)):
        for dom in ("free_at", "interval"):
            c = sched_cost(algo, topo, 2256, iters=1, n_rounds=nr,
                           conflict_domain=dom)
            print(f"{algo:14} {dom:9} {c['bits']:>8} "
                  f"{c['comparator_bits']:>8} {c['area_norm']:>7.4f} "
                  f"{c['gate_levels']:>4} {c['t_sched_cycles']:>8}")
        c = sched_cost(algo, topo, 2256, iters=1, n_rounds=nr,
                       conflict_domain="interval")
        for k, v in c["bits_breakdown"].items():
            print(f"    {k:24} {v:>8}")

    print("\n=== iters pricing: depth is per-iteration, T_sched is linear ===")
    for algo in ("islip_mesh", "pim_mesh", "islip2d_mesh"):
        c1 = sched_cost(algo, Topology("mesh"), 2256, iters=1, n_rounds=110)
        row = [f"{algo:12}"]
        for it in (1, 2, 4):
            c = sched_cost(algo, Topology("mesh"), 2256, iters=it,
                           n_rounds=110)
            assert c["gate_levels"] == c1["gate_levels"], (
                f"{algo}: depth must not scale with iters "
                f"({c['gate_levels']} vs {c1['gate_levels']} at I={it})")
            assert c["t_sched_cycles"] == it * c1["t_sched_cycles"], (
                f"{algo}: T_sched must be linear in iters, not "
                f"{c['t_sched_cycles']} at I={it}")
            row.append(f"I={it}: lv={c['gate_levels']:>2} "
                       f"T={c['t_sched_cycles']:>4}")
        print("  " + "  ".join(row))
    print("  [ok] one iteration = one decision depth; iterations are "
          "dependent steps")

    print("\n=== centralization ledger (bits) ===")
    led = centralization_ledger()
    for cfg in ("mesh_base", "ring_base", "mesh_islip2d", "ring_islip2d"):
        r = led[cfg]
        print(f"  {cfg:13} distributed={r['distributed_bits']:>8} "
              f"total={r['total_bits']:>8}")
        for k, v in r["distributed_breakdown"].items():
            print(f"      {k:24} {v:>8}")
    for k in ("mesh_islip2d_vs_mesh_base", "ring_islip2d_vs_ring_base"):
        print(f"  {k}: {json.dumps(led[k])}")
