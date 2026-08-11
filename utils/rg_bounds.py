#!/usr/bin/env python3
"""Analytical lower bounds for request-grant collectives on mesh/torus.

Five data-plane families (parameterized by sigma + topology) plus
request-grant control-plane bounds (R_rg and arbiter ingress convergence).
"""

from __future__ import annotations

import math
from typing import Any

from rg_topo import (
    MX, MY, N, RAMP, RAMP_BW, Topology, central_arbiter_node, coord,
)


def eject_lb(n: int, m: int, ramp_bw: int = RAMP_BW) -> int:
    return math.ceil((n - 1) * m / ramp_bw)


def corner_lb(topo: Topology, m: int) -> int:
    """Worst-node ingress cut bound.

    Mesh corner has 2 incoming links @ 1/sigma flit/cy each.
    Torus: every node has 4 incoming links @ 1/sigma each.
    Bound: ceil((N-1)*m / (degree_in / sigma)).
    """
    if topo.kind == "mesh":
        # corner degree = 2
        ingress = 2 / topo.sigma
    else:
        ingress = 4 / topo.sigma
    return math.ceil((topo.n - 1) * m / ingress)


def latency_lb(topo: Topology, m: int) -> int:
    """Farthest pair wire delay + serialization + 2*RAMP."""
    return 2 * RAMP + topo.diameter_wire() + (m - 1) * topo.sigma


def bisect_lb(topo: Topology, m: int, pattern: str = "allgather") -> int:
    """Bisection cut bound. Pattern decides crossing traffic volume.

    For allgather/allreduce/alltoall: half the nodes' data must cross.
    For broadcast/reduce: one source's m flits cross once (multicast fanout
    local) — much weaker; we still report it for completeness.
    """
    n = topo.n
    v_links = topo.vertical_cut_links()
    h_links = topo.horizontal_cut_links()
    # capacity per direction across cut (flit/cy)
    v_cap = v_links / topo.sigma
    h_cap = h_links / topo.sigma

    if pattern in ("broadcast", "reduce"):
        # one message of m flits needs to cross each cut at most once
        cross = m
    elif pattern == "alltoall":
        # every pair across the cut: (n/2)*(n/2)*m
        cross_v = (n // 2) * (n - n // 2) * m
        cross_h = (n // 2) * (n - n // 2) * m
        return max(math.ceil(cross_v / v_cap), math.ceil(cross_h / h_cap))
    else:
        # allgather / allreduce: each of n/2 sources sends m across
        cross = (n // 2) * m

    return max(math.ceil(cross / v_cap), math.ceil(cross / h_cap))


def release_lb(topo: Topology, m: int, ramp_bw: int = RAMP_BW) -> int:
    """Earliest completion at the worst receiver (relaxed links)."""
    # pick a corner for mesh; any node for torus (symmetric)
    if topo.kind == "mesh":
        receivers = [0]  # corner
    else:
        receivers = [0]  # all equivalent up to rotation
    best_cmax = 0
    for r in receivers:
        releases = []
        for s in range(topo.n):
            if s == r:
                continue
            dist = topo.wire_distance(s, r)
            releases.extend(RAMP + dist + i * topo.sigma for i in range(m))
        lanes = [-10**18] * ramp_bw
        for release in sorted(releases):
            lane = min(range(ramp_bw), key=lambda j: lanes[j])
            lanes[lane] = max(release, lanes[lane] + 1)
        best_cmax = max(best_cmax, max(lanes) + RAMP)
    return best_cmax


def data_bounds(topo: Topology, m: int, pattern: str = "allgather",
                ramp_bw: int = RAMP_BW) -> dict[str, Any]:
    b_eject = eject_lb(topo.n, m, ramp_bw)
    b_corner = corner_lb(topo, m)
    b_lat = latency_lb(topo, m)
    b_bisect = bisect_lb(topo, m, pattern)
    b_release = release_lb(topo, m, ramp_bw)

    # Pattern-specific: broadcast/reduce don't need full eject of (N-1)*m
    # at every node — only root (reduce) or every node receives m (broadcast).
    if pattern == "broadcast":
        b_eject = math.ceil(m / ramp_bw)  # each node receives m from root
        b_corner = math.ceil(m / (2 / topo.sigma if topo.kind == "mesh"
                                  else 4 / topo.sigma))
        # release: one source, all receivers
        b_release = 2 * RAMP + topo.diameter_wire() + (m - 1) * topo.sigma
    elif pattern == "reduce":
        # gather to root: root ejects (N-1)*m
        b_eject = math.ceil((topo.n - 1) * m / ramp_bw)
        b_release = release_lb(topo, m, ramp_bw)

    t = max(b_eject, b_corner, b_lat, b_bisect, b_release)
    binding = [name for name, val in (
        ("eject", b_eject), ("corner", b_corner),
        ("latency", b_lat), ("bisect", b_bisect),
        ("release", b_release)) if val == t]
    return {
        "eject_lb": b_eject,
        "corner_lb": b_corner,
        "latency_lb": b_lat,
        "bisect_lb": b_bisect,
        "release_lb": b_release,
        "T_data": t,
        "binding": binding,
        "pattern": pattern,
        "m": m,
        "topo": topo.kind,
        "sigma": topo.sigma,
    }


# ---------------------------------------------------------------------------
# Request-grant control-plane bounds
# ---------------------------------------------------------------------------

def path_control_delay(topo: Topology, src: int, dst: int) -> int:
    """One-way control-message delay = ⌊link-delay Manhattan / 2⌋.

    Control plane is a private NoC (1 msg/cy/link); hop latency is half the
    data-plane H/V Manhattan distance, not the full data wire delay.
    """
    return topo.ctrl_wire_distance(src, dst)


def r_rg_async_min(topo: Topology, arb: int | None = None) -> int:
    """Min R_rg for async single-flow (closest node to CA)."""
    arb = arb if arb is not None else central_arbiter_node()
    return min(2 * path_control_delay(topo, s, arb) for s in range(topo.n)
               if s != arb) + 0  # T_sched added separately


def r_rg_async_max(topo: Topology, arb: int | None = None) -> int:
    arb = arb if arb is not None else central_arbiter_node()
    return max(2 * path_control_delay(topo, s, arb) for s in range(topo.n))


def r_rg_sync(topo: Topology, arb: int | None = None) -> int:
    """Sync barrier: wait for farthest request + broadcast grants back."""
    arb = arb if arb is not None else central_arbiter_node()
    to_arb = max(path_control_delay(topo, s, arb) for s in range(topo.n))
    # grant broadcast: arb -> all, bounded by diameter from arb
    from_arb = max(path_control_delay(topo, arb, s) for s in range(topo.n))
    return to_arb + from_arb


def r_rg_da(topo: Topology, src: int, dst: int) -> int:
    """Distributed arbiter: request to dst, grant back. 2*wire(s,d)."""
    return 2 * path_control_delay(topo, src, dst)


def ctrl_convergence_lb(n_requests: int, ingress_ports: int = 4) -> int:
    """Arbiter ingress serialization on the PRIVATE control NoC.

    Control messages do not share data-plane links; the remaining bottleneck
    at a centralized arbiter is its control-router ingress degree (≤4 on
    a mesh/torus node).
    """
    return math.ceil(n_requests / ingress_ports)


def n_requests_for(pattern: str, n: int = N, aggregate: bool = False,
                   sync: bool = False) -> int:
    """How many request messages hit the arbiter(s)."""
    if pattern == "alltoall":
        if aggregate:
            return n  # one request per source covering all dests
        return n * (n - 1)
    if pattern == "broadcast":
        return 1  # one tree request from root
    if pattern == "reduce":
        if sync or aggregate:
            return n  # each source requests to join gather
        return n - 1  # each non-root source
    if pattern in ("allgather", "allreduce"):
        if sync:
            return n  # barrier: one request per node
        # async allgather: one request per multicast tree = n
        return n
    raise ValueError(pattern)


def rg_bounds(topo: Topology, m: int, pattern: str,
              arbiter: str = "ca",
              sync: bool = False,
              aggregate: bool = False,
              t_sched: int = 1,
              ramp_bw: int = RAMP_BW) -> dict[str, Any]:
    """Combined data + request-grant lower bound."""
    data = data_bounds(topo, m, pattern, ramp_bw)
    arb = central_arbiter_node()
    n_req = n_requests_for(pattern, topo.n, aggregate=aggregate, sync=sync)
    conv = ctrl_convergence_lb(n_req, ingress_ports=4 if arbiter == "ca"
                               else 1)  # DA: each dst has 1 logical port
    # For DA alltoall, each dest sees (n-1) requests → conv = n-1
    if arbiter == "da" and pattern == "alltoall" and not aggregate:
        conv = topo.n - 1
    elif arbiter == "da" and pattern == "alltoall" and aggregate:
        # aggregate doesn't apply cleanly to DA; treat as per-dest
        conv = topo.n - 1

    if arbiter == "ca":
        if sync or pattern in ("allgather", "allreduce") and sync:
            r_rg = r_rg_sync(topo, arb) + t_sched
        elif pattern in ("allgather", "allreduce") and not sync:
            # async trees still need each request to reach CA
            r_rg = r_rg_async_max(topo, arb) + t_sched
        else:
            r_rg = r_rg_async_max(topo, arb) + t_sched
    else:
        # DA: bound by max pairwise 2*wire
        if pattern == "broadcast":
            # root requests to... itself for tree grant? model as local
            r_rg = t_sched
        else:
            r_rg = max(2 * topo.ctrl_wire_distance(s, d)
                       for s in range(topo.n) for d in range(topo.n)
                       if s != d) + t_sched

    # Control plane lower bound on makespan contribution
    t_ctrl = max(r_rg, conv + (r_rg_async_min(topo, arb) if arbiter == "ca"
                               else 0) + t_sched)

    t_total = data["T_data"] + t_ctrl
    # More carefully: data can overlap with late grants for async; for sync
    # data starts only after all grants. Use additive for sync, max for async
    # as a lower bound (data cannot finish before max(T_data, first_grant+...))
    if sync or pattern in ("allgather", "allreduce"):
        # sync: data starts after barrier grants
        lb = data["T_data"] + t_ctrl
    else:
        # async: lower bound is at least max(T_data, t_ctrl) but typically
        # T_data + min R_rg since first flow pays R_rg then data bound
        lb = data["T_data"] + r_rg_async_min(topo, arb) + t_sched
        lb = max(lb, t_ctrl)  # cannot beat pure control serialization

    return {
        **data,
        "arbiter": arbiter,
        "sync": sync,
        "aggregate": aggregate,
        "t_sched": t_sched,
        "n_requests": n_req,
        "ctrl_convergence_lb": conv,
        "R_rg": r_rg,
        "R_rg_min": (r_rg_async_min(topo, arb) + t_sched if arbiter == "ca"
                     else t_sched),
        "R_rg_sync": r_rg_sync(topo, arb) + t_sched,
        "T_ctrl": t_ctrl,
        "T_lb": lb,
    }


def assert_bisection_equal(m: int = 1) -> dict[str, Any]:
    """Self-check: mesh and torus must have equal bisect_lb for allgather."""
    mesh = Topology("mesh")
    torus = Topology("torus")
    bm = bisect_lb(mesh, m, "allgather")
    bt = bisect_lb(torus, m, "allgather")
    ba_m = bisect_lb(mesh, m, "alltoall")
    ba_t = bisect_lb(torus, m, "alltoall")
    return {
        "allgather_mesh": bm,
        "allgather_torus": bt,
        "allgather_equal": bm == bt,
        "alltoall_mesh": ba_m,
        "alltoall_torus": ba_t,
        "alltoall_equal": ba_m == ba_t,
        "mesh_bisection_bw": mesh.bisection_bw(),
        "torus_bisection_bw": torus.bisection_bw(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(assert_bisection_equal(), indent=2))
    for kind in ("mesh", "torus"):
        topo = Topology(kind)
        for pat in ("alltoall", "allgather", "allreduce", "broadcast", "reduce"):
            for m in (1, 4, 16):
                b = rg_bounds(topo, m, pat, arbiter="ca",
                              sync=pat in ("allgather", "allreduce"),
                              aggregate=False)
                print(f"{kind:6} {pat:10} m={m:2} T_data={b['T_data']:5} "
                      f"T_lb={b['T_lb']:5} bind={'+'.join(b['binding'])} "
                      f"conv={b['ctrl_convergence_lb']} R_rg={b['R_rg']}")
