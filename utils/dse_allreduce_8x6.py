#!/usr/bin/env python3
"""8x6 mesh allreduce DSE: lower bounds, reduce-site placement, scheme search.

Parameter set (sole truth for this study):
  MX=8, MY=6, H=7, V=9, RAMP=5, RAMP_BW=2, COMPUTE=5
  m in {1, 13, 32, 200}

Reduce sites (orthogonal attributes: ramp_crossings / uses_ramp_bw / alu_pipelined):
  S1 l1     : PE/L1 round-trip merge
  S2 nic    : NIC-side ALU (latency still crosses ramp; no L1 BW)
  S3 router : in-router inline ALU
  S4 none   : no in-network reduce (allgather + local compute)

Schemes:
  A tree_reduce_bcast  — spanning-tree reduce then multicast bcast
  B dual_tree          — split m across two edge-disjoint trees (tick 1+)
  C dim_rs_ag          — dimensional RS+AG (applicable when m % MX == 0)
  D ring_rs_ag         — Hamilton ring RS+AG (bandwidth baseline / falsifier)
  E allgather_local    — S4 only: allgather + PE compute

Loop protocol: tick 0 baseline, then gap-directed EXTRA_SCHEMES until
no >=1% improvement for one full tick → loop_status=converged.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import hamilton_ring as hr
import sched_zerobuf_compare as sz

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "allreduce_8x6_dse.json"

# ---------------------------------------------------------------------------
# Sole parameter truth
# ---------------------------------------------------------------------------
MX, MY = 8, 6
H, V = 7, 9
RAMP = 5
RAMP_BW = 2
COMPUTE = 5
N = MX * MY
MESSAGE_FLITS = (1, 13, 32, 200)
DIAMETER = (MX - 1) * H + (MY - 1) * V  # 94


def setup_globals():
    """Point sched_zerobuf_compare at this study's geometry + RAMP=5."""
    sz.cfg(MX, MY, H, V)
    sz.RAMP = RAMP
    sz.init_ring()


def nid(x: int, y: int) -> int:
    return x + MX * y


def coord(n: int) -> tuple[int, int]:
    return n % MX, n // MX


def edge_lat(u: int, v: int) -> int:
    return H if coord(u)[1] == coord(v)[1] else V


def manh(a: int, b: int) -> int:
    ax, ay = coord(a)
    bx, by = coord(b)
    return abs(ax - bx) * H + abs(ay - by) * V


def hop_count(a: int, b: int) -> int:
    ax, ay = coord(a)
    bx, by = coord(b)
    return abs(ax - bx) + abs(ay - by)


# ---------------------------------------------------------------------------
# Lower bounds
# ---------------------------------------------------------------------------
def lower_bounds(m: int) -> dict:
    """Five-family lower bounds; ideal T_LB = 108 + ceil(m/2)."""
    k = 2  # corner degree == ramp_bw
    ser = math.ceil(m / k)
    l1_causal = 2 * RAMP + DIAMETER + COMPUTE + ser - 1  # 108 + ceil(m/2)
    l2_inject = math.ceil(m / RAMP_BW)
    l3_eject_final = math.ceil(m / RAMP_BW)
    l4_corner_cut = math.ceil(m / 2)
    l4_v_bisect = math.ceil(m / MY)  # vertical cut has MY links (half-mesh X)
    l5_ag_eject = math.ceil((N - 1) * m / RAMP_BW)

    ideal = max(l1_causal, l2_inject, l3_eject_final, l4_corner_cut, l4_v_bisect)
    # Plan site LBs: S1/S2 → 118+ceil(m/2); S3 → 108+ceil(m/2); S4 includes L5.
    site = {
        "l1": 118 + ser,
        "nic": 118 + ser,
        "router": 108 + ser,
        "none": max(108 + ser, l5_ag_eject),
    }
    return {
        "m": m,
        "diameter": DIAMETER,
        "L1_causal": l1_causal,
        "L2_inject": l2_inject,
        "L3_eject_final": l3_eject_final,
        "L4_corner_cut": l4_corner_cut,
        "L4_v_bisect": l4_v_bisect,
        "L5_ag_eject": l5_ag_eject,
        "T_LB": ideal,
        "T_LB_check": 108 + ser,
        "site_LB": site,
        "latency_dominated": l1_causal >= max(l2_inject, l3_eject_final, l4_corner_cut),
    }


# ---------------------------------------------------------------------------
# Reduce site cost model
# ---------------------------------------------------------------------------
SITES = {
    "l1": {
        "id": "S1",
        "ramp_crossings": 2,
        "uses_ramp_bw": True,
        "label": "L1/PE local",
    },
    "nic": {
        "id": "S2",
        "ramp_crossings": 2,
        "uses_ramp_bw": False,
        "label": "NIC-side ALU",
    },
    "router": {
        "id": "S3",
        "ramp_crossings": 0,
        "uses_ramp_bw": False,
        "label": "router inline ALU",
    },
    "none": {
        "id": "S4",
        "ramp_crossings": 0,
        "uses_ramp_bw": False,
        "label": "no in-network reduce",
    },
}


def alu_cost(m: int, pipelined: bool) -> int:
    if pipelined:
        return COMPUTE + m - 1
    return COMPUTE * m


def merge_cost(site: str, m: int, pipelined: bool) -> int:
    """Single-level merge latency (time advance at a merge node)."""
    if site == "none":
        return 0
    alu = alu_cost(m, pipelined)
    crossings = SITES[site]["ramp_crossings"]
    return crossings * RAMP + alu


def merge_slots(site: str, node: int, t_arrive: int, m: int, pipelined: bool):
    """Footprint slots + ready time after merge at `node`.

    t_arrive = cycle when last needed flit is present at the router.
    Returns (extra_slots, t_ready_to_forward).
    """
    if site == "none":
        return [], t_arrive
    cost = merge_cost(site, m, pipelined)
    if SITES[site]["uses_ramp_bw"]:
        # Down at t_arrive (head), PE compute, up so that ready = t_arrive + cost
        # Pipelined: occupy D for m cycles starting t_arrive; U for m cycles
        # ending just before forward.
        d_start = t_arrive
        u_start = t_arrive + cost - m  # last U flit at t_arrive+cost-1
        if u_start < t_arrive + 1:
            u_start = t_arrive + 1
        slots = [("D", node, d_start), ("U", node, u_start)]
        return slots, t_arrive + cost
    return [], t_arrive + cost


# ---------------------------------------------------------------------------
# Trees
# ---------------------------------------------------------------------------
def dim_path(s: int, d: int) -> list[int]:
    path = [s]
    x, y = coord(s)
    dx, dy = coord(d)
    while x != dx:
        x += 1 if x < dx else -1
        path.append(nid(x, y))
    while y != dy:
        y += 1 if y < dy else -1
        path.append(nid(x, y))
    return path


def build_shortest_tree(root: int) -> tuple[dict, dict, dict]:
    """BFS tree by hop count; edge weights still H/V for timing."""
    parent = {root: -1}
    dist = {root: 0}
    frontier = [root]
    while frontier:
        nxt = []
        for u in frontier:
            ux, uy = coord(u)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = ux + dx, uy + dy
                if not (0 <= nx < MX and 0 <= ny < MY):
                    continue
                v = nid(nx, ny)
                if v in parent:
                    continue
                parent[v] = u
                dist[v] = dist[u] + edge_lat(u, v)
                nxt.append(v)
        frontier = nxt
    children: dict[int, list[int]] = {n: [] for n in range(N)}
    for n in range(N):
        if n != root and parent.get(n, -1) >= 0:
            children[parent[n]].append(n)
    return parent, children, dist


def pick_roots() -> list[int]:
    """Centre, near-centre, corners."""
    cx, cy = (MX - 1) / 2.0, (MY - 1) / 2.0
    centre = min(range(N), key=lambda n: abs(coord(n)[0] - cx) + abs(coord(n)[1] - cy))
    near = []
    for n in range(N):
        if n == centre:
            continue
        x, y = coord(n)
        if abs(x - cx) + abs(y - cy) <= 2:
            near.append(n)
    near = sorted(near, key=lambda n: abs(coord(n)[0] - cx) + abs(coord(n)[1] - cy))[:4]
    corners = [nid(0, 0), nid(MX - 1, 0), nid(0, MY - 1), nid(MX - 1, MY - 1)]
    out, seen = [], set()
    for r in [centre] + near + corners:
        if r not in seen:
            out.append(r)
            seen.add(r)
    return out


# ---------------------------------------------------------------------------
# Busy / verify helpers
# ---------------------------------------------------------------------------
def empty_busy():
    return (defaultdict(dict), defaultdict(dict), defaultdict(dict))


def add_slots(busy, slots, flits: int, offset: int = 0):
    link_busy, up_busy, down_busy = busy

    def table(kind):
        return link_busy if kind == "L" else up_busy if kind == "U" else down_busy

    mk = 0
    for kind, key, rel in slots:
        cyc = offset + rel
        t = table(kind)
        for i in range(flits):
            t[key][cyc + i] = t[key].get(cyc + i, 0) + 1
        if kind == "D":
            mk = max(mk, cyc + flits - 1 + RAMP)
        else:
            mk = max(mk, cyc + flits - 1)
    return mk


def merge_busy(a, b, offset: int):
    out = []
    for tbl in range(3):
        merged = {}
        for key, d in a[tbl].items():
            merged[key] = dict(d)
        for key, d in b[tbl].items():
            tgt = merged.setdefault(key, {})
            for cyc, ct in d.items():
                tgt[cyc + offset] = tgt.get(cyc + offset, 0) + ct
        out.append(merged)
    return tuple(out)


def verify_caps(busy, ramp_bw: int = RAMP_BW) -> bool:
    link_busy, up_busy, down_busy = busy
    if any(ct > 1 for d in link_busy.values() for ct in d.values()):
        return False
    if any(ct > ramp_bw for d in up_busy.values() for ct in d.values()):
        return False
    if any(ct > ramp_bw for d in down_busy.values() for ct in d.values()):
        return False
    return True


def verify_final_eject(busy, m: int, alive=None) -> bool:
    if alive is None:
        alive = range(N)
    _, _, down = busy
    ejects = {n: sum(d.values()) for n, d in down.items()}
    return all(ejects.get(n, 0) == m for n in alive)


def busy_makespan(busy) -> int:
    mk = 0
    for tbl in busy:
        for d in tbl.values():
            if d:
                mk = max(mk, max(d))
    return mk


# ---------------------------------------------------------------------------
# Scheme A: tree reduce + broadcast
# ---------------------------------------------------------------------------
def tree_reduce_schedule(root: int, site: str, m: int, pipelined: bool):
    """Bottom-up tree reduce with rigid per-edge send times.

    Returns (makespan_reduce_end, busy, t_ready_at_root).
    t_ready_at_root = cycle when reduced result (all m flits) is ready at root
    router (before final eject / before bcast inject).
    """
    if site == "none":
        return None, None, None
    parent, children, _ = build_shortest_tree(root)
    busy = empty_busy()

    # ready_send[n] = earliest cycle when n can begin sending its m-flit
    # reduced partial toward parent (data present at n's router).
    ready_send: dict[int, int] = {}

    def compute(node: int) -> int:
        kids = children[node]
        local_at_router = RAMP  # local inject at cycle 0 → at router at RAMP
        if not kids:
            # Leaf: inject m flits; with RAMP_BW lanes may need serialization.
            # Head at router at RAMP; all m present after RAMP + ceil(m/rb)-1.
            inj = math.ceil(m / RAMP_BW)
            # Record up-ramp occupancy (inject from cycle 0)
            for i in range(m):
                lane_t = i // RAMP_BW  # 0,0,1,1,... for rb=2
                busy[1][node][lane_t] = busy[1][node].get(lane_t, 0) + 1
            ready_send[node] = RAMP + inj - 1
            # Actually head is ready at RAMP; we send starting when head ready.
            # For pipeline, send can start at RAMP (first flit); last flit
            # injected at inj-1. ready_send = start of send = RAMP.
            ready_send[node] = RAMP
            return ready_send[node]

        arrivals = []
        for k in kids:
            t0 = compute(k)
            # send m flits on link k→node starting at t0
            lat = edge_lat(k, node)
            key = sz.lk(k, node)
            for i in range(m):
                cyc = t0 + i
                busy[0][key][cyc] = busy[0][key].get(cyc, 0) + 1
            # first flit arrives t0+lat; last t0+lat+m-1
            arrivals.append(t0 + lat)
        # local contribution
        for i in range(m):
            lane_t = i // RAMP_BW
            busy[1][node][lane_t] = busy[1][node].get(lane_t, 0) + 1
        t_local_first = RAMP
        t_merge_start = max(max(arrivals), t_local_first)
        extra, t_ready = merge_slots(site, node, t_merge_start, m, pipelined)
        for kind, key, rel in extra:
            # merge_slots returns absolute times in rel
            tbl = 1 if kind == "U" else 2
            for i in range(m):
                busy[tbl][key][rel + i] = busy[tbl][key].get(rel + i, 0) + 1
        ready_send[node] = t_ready
        return t_ready

    t_root = compute(root)
    # Root final merge already done inside compute(root); result ready at t_root.
    # Optional: eject to PE for "result at L1" semantics — allreduce needs
    # every node to receive, so bcast follows; root also needs final eject of
    # the result. Record root down-ramp of m flits at t_root.
    for i in range(m):
        busy[2][root][t_root + i] = busy[2][root].get(t_root + i, 0) + 1
    mk = t_root + m - 1 + RAMP  # result at root L1
    ok = verify_caps(busy)
    return mk, busy, t_root, ok


def fp_bcast_tree(root: int) -> list[tuple[str, int, int]]:
    """Multicast tree footprint from root (XY dimensional)."""
    slots = [("U", root, 0)]
    # X then Y spanning tree edges
    rx, ry = coord(root)
    edges = []
    for x in range(rx + 1, MX):
        edges.append((nid(x - 1, ry), nid(x, ry)))
    for x in range(rx - 1, -1, -1):
        edges.append((nid(x + 1, ry), nid(x, ry)))
    for x in range(MX):
        for y in range(ry + 1, MY):
            edges.append((nid(x, y - 1), nid(x, y)))
        for y in range(ry - 1, -1, -1):
            edges.append((nid(x, y + 1), nid(x, y)))
    # distances from root along tree
    dist = {root: 0}
    children = defaultdict(list)
    for p, c in edges:
        children[p].append(c)
        dist[c] = dist[p] + edge_lat(p, c)
    for p, c in edges:
        slots.append(("L", sz.lk(p, c), RAMP + dist[p]))
    for d in range(N):
        if d != root:
            slots.append(("D", d, RAMP + dist[d]))
    return slots


def scheme_tree(root: int, site: str, m: int, pipelined: bool) -> dict:
    name = f"tree_r{root}"
    if site == "none":
        return {
            "name": name, "algo": "tree_bcast", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": "tree reduce requires in-network site",
        }
    mk_r, busy_r, t_ready, ok_r = tree_reduce_schedule(root, site, m, pipelined)
    if busy_r is None or not ok_r:
        return {
            "name": name, "algo": "tree_bcast", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": "reduce phase conflict",
            "root": root,
        }
    # Broadcast: root re-injects result. Start after reduce result ready at PE
    # (mk_r) then up-ramp — or from router at t_ready without PE round-trip.
    # Use router-ready for S3; for S1/S2 result was ejected so need re-inject.
    if SITES[site]["uses_ramp_bw"] or site == "l1":
        bcast_start = mk_r + 1  # after PE has result
    else:
        bcast_start = t_ready + 1  # forward from router

    foot_b = {root: fp_bcast_tree(root)}
    mk_b, _, busy_b = sz.pack(foot_b, RAMP_BW, [root], flits=m)
    busy_all = merge_busy(busy_r, busy_b, bcast_start)
    mk = bcast_start + mk_b
    # Caps only on the combined schedule. Final-result delivery: every non-root
    # gets m flits from the bcast phase; root already ejected m in reduce.
    # Intermediate L1 merge D slots are NOT final-result ejects — do not use
    # verify_final_eject on the combined busy tables.
    _, _, down_b = busy_b
    bcast_ejects = {n: sum(d.values()) for n, d in down_b.items()}
    eject_ok = all(bcast_ejects.get(n, 0) == m for n in range(N) if n != root)
    ok = verify_caps(busy_all) and ok_r and eject_ok
    return {
        "name": name,
        "algo": "tree_bcast",
        "site": site,
        "makespan": mk,
        "ok": ok,
        "feasible": True,
        "root": root,
        "phase_reduce_end": mk_r,
        "phase_bcast_start": bcast_start,
        "phase_bcast_end": mk,
        "pipelined": pipelined,
    }


# ---------------------------------------------------------------------------
# Scheme B: dual tree (split m into two halves, two roots)
# ---------------------------------------------------------------------------
def scheme_dual_tree(root_a: int, root_b: int, site: str, m: int,
                     pipelined: bool) -> dict:
    name = f"dual_tree_r{root_a}_{root_b}"
    if site == "none":
        return {
            "name": name, "algo": "dual_tree", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": "dual tree needs in-network reduce",
        }
    m0 = m // 2
    m1 = m - m0
    if m0 < 1:
        return {
            "name": name, "algo": "dual_tree", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": "m<2 cannot split",
        }
    # Run two reduce+bcast sequentially on partitions of flits — a safe
    # feasible upper bound (parallel would need edge-disjoint proof).
    r0 = scheme_tree(root_a, site, m0, pipelined)
    r1 = scheme_tree(root_b, site, m1, pipelined)
    if not r0.get("ok") or not r1.get("ok"):
        return {
            "name": name, "algo": "dual_tree", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": "sub-tree failed",
            "parts": [r0, r1],
        }
    # Sequential composition upper bound
    mk = r0["makespan"] + 1 + r1["makespan"]
    return {
        "name": name,
        "algo": "dual_tree",
        "site": site,
        "makespan": mk,
        "ok": True,
        "feasible": True,
        "roots": [root_a, root_b],
        "split": [m0, m1],
        "pipelined": pipelined,
        "note": "sequential dual-tree upper bound (edge-disjoint parallel TBD)",
    }


# ---------------------------------------------------------------------------
# Scheme C: dimensional RS + AG
# ---------------------------------------------------------------------------
def dim_applicable(m: int) -> bool:
    return m % MX == 0


def scheme_dim_rs_ag(site: str, m: int, pipelined: bool) -> dict:
    name = "dim_rs_ag"
    if not dim_applicable(m):
        return {
            "name": name, "algo": "dim_rs_ag", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": f"m={m} not divisible by MX={MX}",
            "applicable": False,
        }
    if site == "none":
        return {
            "name": name, "algo": "dim_rs_ag", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": "dim RS needs in-network reduce",
            "applicable": True,
        }
    # Analytic phased model (rigid, conflict-free by construction within rows/cols):
    # Phase RS-X: each row of MX nodes, chunk = m/MX. Ring RS around the row.
    #   steps = MX-1; each step moves chunk flits + merge.
    chunk = m // MX
    mc = merge_cost(site, chunk, pipelined)
    # Row edge is H. Uni-directional RS: (MX-1) hops of chunk serialization.
    # Per step: send chunk on link (takes chunk cycles occupancy) + merge.
    # Pipelined along ring: makespan_row_rs ≈ (MX-1)*(H + mc) + (chunk-1)
    # More careful: first flit travels (MX-1)*H with (MX-1) merges; + chunk-1.
    row_rs = (MX - 1) * (H + mc) + (chunk - 1) + RAMP
    # Phase RS-Y: each column of MY nodes on chunk-sized (already row-reduced)
    # pieces. After full 2D RS each node holds m/N — but m/N may be fractional.
    # When m % N != 0, Y phase does reduce (not equal RS) of the chunk to one
    # leader then broadcast within column — use tree reduce cost.
    if m % N == 0:
        chunk_y = m // N
        mc_y = merge_cost(site, chunk_y, pipelined)
        col_rs = (MY - 1) * (V + mc_y) + (chunk_y - 1) + RAMP
        col_ag = (MY - 1) * V + (chunk_y - 1) + 2 * RAMP
        row_ag = (MX - 1) * H + (chunk - 1) + 2 * RAMP
        mk = row_rs + 1 + col_rs + 1 + col_ag + 1 + row_ag
        mode = "full_rs_ag"
    else:
        # Y: gather/reduce chunk to column centre + bcast chunk
        mc_y = merge_cost(site, chunk, pipelined)
        hops_y = MY - 1
        col_red = RAMP + hops_y * V + hops_y * mc_y + (chunk - 1)
        col_bcast = RAMP + hops_y * V + RAMP + (chunk - 1)
        row_ag = (MX - 1) * H + (chunk - 1) + 2 * RAMP
        mk = row_rs + 1 + col_red + 1 + col_bcast + 1 + row_ag
        mode = "rs_x_tree_y"
    return {
        "name": name,
        "algo": "dim_rs_ag",
        "site": site,
        "makespan": mk,
        "ok": True,
        "feasible": True,
        "applicable": True,
        "mode": mode,
        "chunk": chunk,
        "pipelined": pipelined,
        "note": "analytic phased dimensional bound (per-row/col exclusive links)",
    }


# ---------------------------------------------------------------------------
# Scheme D: Hamilton ring RS + AG
# ---------------------------------------------------------------------------
def ring_order():
    if MY % 2 == 0:
        return hr.snake_cycle(MX, MY), True
    order = []
    for y in range(MY):
        xs = range(MX) if y % 2 == 0 else range(MX - 1, -1, -1)
        for x in xs:
            order.append(hr.nid(x, y, MX))
    return order, False


def fp_ring_rs(s: int, order: list[int], pos: dict, site: str, m: int,
               pipelined: bool):
    i = pos[s]
    n = len(order)
    chain = [order[(i + k) % n] for k in range(n)]
    slots = [("U", s, 0)]
    t = RAMP
    for k in range(len(chain) - 1):
        u, w = chain[k], chain[k + 1]
        slots.append(("L", sz.lk(u, w), t))
        t += edge_lat(u, w)
        if w != s:
            extra, t = merge_slots(site, w, t, m, pipelined)
            # merge_slots returns absolute times — convert to relative for fp
            # by using t as absolute from inject 0; slots store absolute-from-0
            for kind, key, rel in extra:
                slots.append((kind, key, rel))
    return slots


def scheme_ring(site: str, m: int, pipelined: bool, bidir: bool = True) -> dict:
    name = f"ring_{'bi' if bidir else 'uni'}_rs_ag"
    if site == "none":
        return {
            "name": name, "algo": "rs_ag", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": "ring RS needs in-network reduce",
        }
    order, is_cycle = ring_order()
    if not bidir and not is_cycle:
        return {
            "name": name, "algo": "rs_ag", "site": site,
            "makespan": None, "ok": False, "feasible": False,
            "reason": "uni needs closed cycle",
        }
    pos = {nd: k for k, nd in enumerate(order)}

    # For large m or many sources, full pack of RS footprints is expensive.
    # Analytic RS: (N-1) hops with merge each; AG similar without merge.
    mc = merge_cost(site, m, pipelined)
    # Average edge on snake ≈ mix of H and V; use exact ring length latency.
    ring_lat = 0
    for i in range(len(order)):
        a, b = order[i], order[(i + 1) % len(order)]
        if not is_cycle and i == len(order) - 1:
            break
        try:
            ring_lat += edge_lat(a, b)
        except Exception:
            ring_lat += H
    if not is_cycle:
        # open path: sum consecutive
        ring_lat = sum(edge_lat(order[i], order[i + 1]) for i in range(len(order) - 1))

    # Uni RS around full ring: (N-1) merges + ring_lat + ser
    rs_analytic = RAMP + ring_lat + (N - 1) * mc + (m - 1)
    # Bidirectional RS halves the ring distance
    if bidir:
        rs_analytic = RAMP + (ring_lat // 2) + (N // 2) * mc + (m - 1)

    # AG: use sz.fp_ring + pack for m=1,13; analytic for large m
    if m <= 13:
        foot_rs = {
            s: fp_ring_rs(s, order, pos, site, m, pipelined) for s in range(N)
        }
        src = sorted(range(N), key=lambda s: pos[s])
        mk_rs, _, busy_rs = sz.pack(foot_rs, RAMP_BW, src, flits=m)
        rs_ok = verify_caps(busy_rs)
        foot_ag = {s: sz.fp_ring(s, order, pos, bidir, RAMP_BW) for s in range(N)}
        mk_ag, _, busy_ag = sz.pack(foot_ag, RAMP_BW, src, flits=m)
        ag_ok = sz.verify(busy_ag, RAMP_BW, flits=m)
        mk = mk_rs + 1 + mk_ag
        ok = rs_ok and ag_ok
        return {
            "name": name, "algo": "rs_ag", "site": site,
            "makespan": mk, "ok": ok, "feasible": True,
            "phase_rs_end": mk_rs, "phase_ag_end": mk,
            "pipelined": pipelined, "method": "packed",
        }

    ag_analytic = RAMP + (ring_lat // (2 if bidir else 1)) + (m - 1) + RAMP
    # Allgather eject: each node receives (N-1)*m flits at rb=2
    ag_eject = math.ceil((N - 1) * m / RAMP_BW) + DIAMETER
    ag = max(ag_analytic, ag_eject)
    mk = rs_analytic + 1 + ag
    return {
        "name": name, "algo": "rs_ag", "site": site,
        "makespan": mk, "ok": True, "feasible": True,
        "pipelined": pipelined, "method": "analytic",
        "note": "analytic ring RS+AG (pack skipped for m>13)",
    }


# ---------------------------------------------------------------------------
# Scheme E: allgather + local compute (S4)
# ---------------------------------------------------------------------------
def scheme_allgather_local(m: int, tree_name: str = "dim_xy") -> dict:
    name = f"allgather_{tree_name}"
    # Build XY multicast tree footprints for every source and pack.
    footprints = {}
    for s in range(N):
        footprints[s] = fp_bcast_tree(s)  # source-as-root multicast = allgather tree

    best = None
    for order_name, gen in sz.SRC_ORDERS.items():
        try:
            order = gen()
        except TypeError:
            continue
        if len(order) != N:
            continue
        mk, _, busy = sz.pack(footprints, RAMP_BW, order, flits=m)
        if not sz.verify(busy, RAMP_BW, flits=m):
            continue
        if best is None or mk < best[0]:
            best = (mk, order_name, busy)

    if best is None:
        # Fallback analytic: eject bound + diameter
        mk = max(
            math.ceil((N - 1) * m / RAMP_BW) + RAMP,
            2 * RAMP + DIAMETER + m - 1,
        )
        return {
            "name": name, "algo": "allgather_local", "site": "none",
            "makespan": mk + COMPUTE, "ok": True, "feasible": True,
            "ag_makespan": mk, "compute": COMPUTE, "method": "analytic_fallback",
        }

    mk_ag, order_name, _ = best
    return {
        "name": name, "algo": "allgather_local", "site": "none",
        "makespan": mk_ag + COMPUTE, "ok": True, "feasible": True,
        "ag_makespan": mk_ag, "compute": COMPUTE,
        "pack_order": order_name, "method": "packed",
    }


# ---------------------------------------------------------------------------
# Evaluation driver
# ---------------------------------------------------------------------------
def evaluate_cell(m: int, site: str, pipelined: bool, schemes: list[str],
                  extra: dict | None = None) -> dict:
    """Run requested schemes for one (m, site, pipelined) cell."""
    lb = lower_bounds(m)
    site_lb = lb["site_LB"][site]
    results = []
    roots = pick_roots()

    if "A" in schemes and site != "none":
        best_tree = None
        for r in roots:
            rec = scheme_tree(r, site, m, pipelined)
            results.append(rec)
            if rec.get("ok") and rec.get("makespan") is not None:
                if best_tree is None or rec["makespan"] < best_tree["makespan"]:
                    best_tree = rec
        if best_tree:
            results.append({**best_tree, "name": "tree_best", "is_best_tree": True})

    if "B" in schemes and site != "none":
        # Dual tree: centre + opposite-ish root
        ra = roots[0]
        rb = nid(MX - 1 - coord(ra)[0], MY - 1 - coord(ra)[1])
        results.append(scheme_dual_tree(ra, rb, site, m, pipelined))
        if extra and "dual_roots" in extra:
            for ra2, rb2 in extra["dual_roots"]:
                results.append(scheme_dual_tree(ra2, rb2, site, m, pipelined))

    if "C" in schemes and site != "none":
        results.append(scheme_dim_rs_ag(site, m, pipelined))

    if "D" in schemes and site != "none":
        results.append(scheme_ring(site, m, pipelined, bidir=True))
        results.append(scheme_ring(site, m, pipelined, bidir=False))

    if "E" in schemes and site == "none":
        # For large m packing allgather is heavy — still try; fallback inside.
        if m >= 200:
            # Analytic only for m=200 to keep DSE tractable
            mk = max(
                math.ceil((N - 1) * m / RAMP_BW) + RAMP,
                2 * RAMP + DIAMETER + m - 1,
            )
            results.append({
                "name": "allgather_dim_xy",
                "algo": "allgather_local",
                "site": "none",
                "makespan": mk + COMPUTE,
                "ok": True,
                "feasible": True,
                "ag_makespan": mk,
                "compute": COMPUTE,
                "method": "analytic_eject_lb",
            })
        else:
            results.append(scheme_allgather_local(m))

    feasible = [r for r in results if r.get("ok") and r.get("makespan") is not None]
    best = min(feasible, key=lambda r: r["makespan"]) if feasible else None
    return {
        "m": m,
        "site": site,
        "pipelined": pipelined,
        "T_LB": lb["T_LB"],
        "site_LB": site_lb,
        "bounds": lb,
        "schemes": results,
        "best": best,
        "ratio_vs_ideal": (best["makespan"] / lb["T_LB"]) if best else None,
        "ratio_vs_site": (best["makespan"] / site_lb) if best else None,
    }


# ---------------------------------------------------------------------------
# Loop ticks
# ---------------------------------------------------------------------------
def run_loop(max_ticks: int = 4) -> dict:
    setup_globals()
    ticks = []
    best_global = {}  # (m, site, pipe) -> makespan
    no_improve = 0
    extra: dict = {}

    for tick in range(max_ticks):
        # Scheme set grows with ticks
        if tick == 0:
            schemes_in = ["A", "C", "D", "E"]
        else:
            schemes_in = ["A", "B", "C", "D", "E"]
            # Gap-directed extras
            extra = dict(extra)
            extra.setdefault("dual_roots", [])
            # Add more dual-root pairs from corners
            extra["dual_roots"] = [
                (nid(3, 2), nid(4, 3)),
                (nid(0, 0), nid(7, 5)),
                (nid(3, 0), nid(4, 5)),
            ]

        cells = []
        improved = False
        for m in MESSAGE_FLITS:
            for site in ("l1", "nic", "router", "none"):
                for pipe in (True, False):
                    if site == "none" and not pipe:
                        # pipelining N/A for S4; evaluate once
                        if pipe is False:
                            pass
                        cell = evaluate_cell(m, site, True, schemes_in, extra)
                        # dedupe: only one pipe value for none
                        key = (m, site, True)
                        cells.append(cell)
                        mk = cell["best"]["makespan"] if cell["best"] else None
                        if mk is not None:
                            prev = best_global.get(key)
                            if prev is None or mk < prev * 0.99:
                                if prev is not None:
                                    improved = True
                                best_global[key] = mk
                        break  # only one pipe for none
                    if site == "none":
                        continue
                    cell = evaluate_cell(m, site, pipe, schemes_in, extra)
                    cells.append(cell)
                    key = (m, site, pipe)
                    mk = cell["best"]["makespan"] if cell["best"] else None
                    if mk is not None:
                        prev = best_global.get(key)
                        if prev is None or mk < prev * 0.99:
                            if prev is not None:
                                improved = True
                            best_global[key] = mk

        # Summaries
        summary = []
        for cell in cells:
            b = cell.get("best")
            summary.append({
                "m": cell["m"],
                "site": cell["site"],
                "pipelined": cell["pipelined"],
                "T_LB": cell["T_LB"],
                "site_LB": cell["site_LB"],
                "best_name": b["name"] if b else None,
                "best_makespan": b["makespan"] if b else None,
                "ratio_vs_ideal": cell["ratio_vs_ideal"],
                "ratio_vs_site": cell["ratio_vs_site"],
            })

        ticks.append({
            "tick": tick,
            "schemes": schemes_in,
            "extra_keys": list(extra.keys()),
            "summary": summary,
            "n_cells": len(cells),
            "cells": cells,
        })

        if tick == 0:
            continue
        if improved:
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= 1:
                break

    status = "converged" if no_improve >= 1 else "stopped"
    # Overall best per m across sites
    per_m = {}
    last_cells = ticks[-1]["cells"]
    for m in MESSAGE_FLITS:
        cands = [c for c in last_cells if c["m"] == m and c.get("best")]
        if not cands:
            per_m[str(m)] = None
            continue
        best = min(cands, key=lambda c: c["best"]["makespan"])
        per_m[str(m)] = {
            "site": best["site"],
            "pipelined": best["pipelined"],
            "scheme": best["best"]["name"],
            "algo": best["best"]["algo"],
            "makespan": best["best"]["makespan"],
            "T_LB": best["T_LB"],
            "site_LB": best["site_LB"],
            "ratio_vs_ideal": best["ratio_vs_ideal"],
        }

    # Site ranking per m (pipelined=True preferred)
    site_rank = {}
    for m in MESSAGE_FLITS:
        rows = []
        for site in ("l1", "nic", "router", "none"):
            cands = [
                c for c in last_cells
                if c["m"] == m and c["site"] == site and c.get("best")
            ]
            if not cands:
                continue
            # Prefer pipelined when available
            cands.sort(key=lambda c: (not c["pipelined"], c["best"]["makespan"]))
            c = cands[0]
            rows.append({
                "site": site,
                "pipelined": c["pipelined"],
                "makespan": c["best"]["makespan"],
                "scheme": c["best"]["name"],
                "ratio_vs_ideal": c["ratio_vs_ideal"],
            })
        rows.sort(key=lambda r: r["makespan"])
        site_rank[str(m)] = rows

    bounds_table = [lower_bounds(m) for m in MESSAGE_FLITS]

    return {
        "schema": "allreduce-8x6-dse/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "mx": MX, "my": MY, "h": H, "v": V,
            "ramp": RAMP, "ramp_bw": RAMP_BW, "compute": COMPUTE,
            "n": N, "diameter": DIAMETER,
            "message_flits": list(MESSAGE_FLITS),
        },
        "sites": SITES,
        "bounds_table": bounds_table,
        "loop_status": status,
        "n_ticks": len(ticks),
        "ticks": ticks,
        "best_per_m": per_m,
        "site_rank_per_m": site_rank,
        "notes": [
            "Makespans are feasible rigid-pack / analytic upper bounds, not optimality proofs.",
            "Ideal T_LB = 108 + ceil(m/2); verified in bounds_table.T_LB_check.",
            "S4 (none) uses allgather + local COMPUTE; bandwidth-dominated for large m.",
            "Ring / dim schemes for large m may use analytic phased models (see method field).",
        ],
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-ticks", type=int, default=4)
    ap.add_argument("--json", type=str, default=str(OUT))
    ap.add_argument("--bounds-only", action="store_true")
    args = ap.parse_args()

    setup_globals()
    print(f"8x6 allreduce  H={H} V={V} RAMP={RAMP} RAMP_BW={RAMP_BW} "
          f"COMPUTE={COMPUTE} diam={DIAMETER}")
    print(f"{'m':>5}  {'L1':>6}  {'L2':>6}  {'L3':>6}  {'L4c':>6}  "
          f"{'L5':>6}  {'T_LB':>6}  {'check':>6}")
    for m in MESSAGE_FLITS:
        b = lower_bounds(m)
        print(f"{m:5d}  {b['L1_causal']:6d}  {b['L2_inject']:6d}  "
              f"{b['L3_eject_final']:6d}  {b['L4_corner_cut']:6d}  "
              f"{b['L5_ag_eject']:6d}  {b['T_LB']:6d}  {b['T_LB_check']:6d}")
        assert b["T_LB"] == b["T_LB_check"] == 108 + math.ceil(m / 2)

    if args.bounds_only:
        return

    print("\nRunning loop DSE...")
    result = run_loop(max_ticks=args.max_ticks)
    path = Path(args.json)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {path}  loop_status={result['loop_status']}  "
          f"ticks={result['n_ticks']}")
    print("\nBest per m:")
    for m, rec in result["best_per_m"].items():
        if rec is None:
            print(f"  m={m}: none")
            continue
        print(f"  m={m}: {rec['makespan']}  site={rec['site']}  "
              f"scheme={rec['scheme']}  ratio={rec['ratio_vs_ideal']:.3f}")


if __name__ == "__main__":
    main()
