#!/usr/bin/env python3
"""Fault-aware hybrid B vertical-band (vband) bi allgather (0-buffer packer).

Healthy: identical to sched_zerobuf_compare.fp_hybrid_v + packer (334 cy @ 16x16).
Under faults: band ring via hamilton_ring.find_ring; cross-band via row trees
when complete, else BFS multicast tree. When the packer cannot find a
conflict-free offset assignment, falls back to a latency-only makespan
(max last delivery + ramp, inject offset 0).
"""

from collections import deque

import hamilton_ring as hr
import sched_zerobuf_compare as Z

MX = MY = 16
H, V, RAMP_BW = 4, 6, 1
N = MX * MY


def cfg(mx=16, my=16, h=4, v=6, ramp_bw=1):
    global MX, MY, H, V, RAMP_BW, N
    MX, MY, H, V, RAMP_BW = mx, my, h, v, ramp_bw
    N = mx * my
    Z.cfg(mx, my, h, v)
    Z.init_ring()


def _global_to_band_local(n, x0, C):
    x, y = hr.coord(n, MX)
    return hr.nid(x - x0, y, C)


def _band_local_to_global(n, x0, C):
    x, y = hr.coord(n, C)
    return Z.nid(x0 + x, y)


def _links_to_local(dead_links, x0, C):
    out = []
    for a, b in dead_links:
        ax, _ = hr.coord(a, MX)
        bx, _ = hr.coord(b, MX)
        if not (x0 <= ax < x0 + C and x0 <= bx < x0 + C):
            continue
        out.append((_global_to_band_local(a, x0, C),
                    _global_to_band_local(b, x0, C)))
    return out


_BAND_RING_CACHE = {}


def _band_ring_for_x0(x0, B, dead_nodes, dead_links):
    """Recover the home band's Hamilton ring/path under faults, using the SAME
    fault-aware pipeline as the global Hamilton ring: find_ring (cycle -> path)
    then find_ring_rebalanced_cycle (sacrifice neighbours to restore a cycle,
    for both colour-imbalanced and balanced-but-obstructed holes). Returns
    (order, is_cycle, sacrificed_global) or None. Cached per band."""
    C = MX // B
    key = (x0, C, frozenset(dead_nodes), frozenset(frozenset(l) for l in dead_links))
    cached = _BAND_RING_CACHE.get(key)
    if cached is not None:
        return cached

    band_dead = [_global_to_band_local(n, x0, C)
                 for n in dead_nodes if x0 <= hr.coord(n, MX)[0] < x0 + C]
    band_links = _links_to_local(dead_links, x0, C)
    if not band_dead and not band_links:
        res = (Z.ham_cycle_vband(C, x0), True, set())
        _BAND_RING_CACHE[key] = res
        return res

    def to_global(order):
        return [_band_local_to_global(n, x0, C) for n in order]

    result = None
    # 1. standard fault-aware search (cycle, then path) with snake hint
    r = hr.find_ring(C, MY, band_dead, band_links, time_budget=4.0)
    if r["feasible"] and r["order"]:
        result = (to_global(r["order"]), r["is_cycle"], set())

    # 2. rebalance (sacrifice band neighbours) to restore a CYCLE. Handles both
    #    colour-imbalanced holes and the balanced-but-no-cycle case (sacrifice
    #    one black + one white boundary node to break a region parity
    #    obstruction, e.g. a spine link/node cut). Preferred over an open path.
    if result is None or not result[1]:
        r = hr.find_ring_rebalanced_cycle(C, MY, band_dead, band_links,
                                          time_budget=10.0)
        if r["feasible"] and r["order"]:
            sac = set(_band_local_to_global(n, x0, C) for n in r["sacrificed"])
            result = (to_global(r["order"]), True, sac)

    # 3. last resort: an open Hamilton path (bi allgather toward both ends).
    if result is None:
        adj = hr.build_adj(C, MY, band_dead, band_links)
        if hr.is_connected(adj):
            nodes = list(adj)
            color_of = {n: ((n % C + n // C) & 1) for n in nodes}
            import time as _t
            deadline = _t.time() + 6.0
            for s0 in sorted(nodes, key=lambda n: (len(adj[n]), n)):
                o = hr._search(adj, s0, "path", color_of, snake_pos=None,
                               deadline=deadline)
                if o and hr.validate_ring(o, adj, False):
                    result = (to_global(o), False, set())
                    break

    _BAND_RING_CACHE[key] = result
    return result


def _band_ring(s, B, dead_nodes, dead_links):
    br = _band_ring_for_x0((Z.coord(s)[0] // (MX // B)) * (MX // B),
                           B, dead_nodes, dead_links)
    if br is None:
        return None
    order, is_cycle, _sac = br
    return order, is_cycle


def _row_fork_left(slots, arr, y, x0, t0, edge_ok):
    if Z.nid(x0, y) not in arr:
        return
    t = t0
    prev = Z.nid(x0, y)
    for xx in range(x0 - 1, -1, -1):
        cur = Z.nid(xx, y)
        if not edge_ok(prev, cur):
            break
        slots.append(('L', Z.lk(prev, cur), t))
        t += H
        slots.append(('D', cur, t))
        prev = cur


def _row_fork_right(slots, arr, y, xr, x_end, t0, edge_ok):
    if Z.nid(xr, y) not in arr:
        return
    t = t0
    prev = Z.nid(xr, y)
    for xx in range(xr + 1, x_end):
        cur = Z.nid(xx, y)
        if not edge_ok(prev, cur):
            break
        slots.append(('L', Z.lk(prev, cur), t))
        t += H
        slots.append(('D', cur, t))
        prev = cur


def _bfs_path(adj, src, dst):
    if src == dst:
        return [src]
    prev = {src: None}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in prev:
                prev[v] = u
                if v == dst:
                    q = None
                    break
                q.append(v)
    if dst not in prev:
        return None
    path = []
    u = dst
    while u is not None:
        path.append(u)
        u = prev[u]
    return path[::-1]


def _path_lat(path):
    return sum(Z.edge_lat(path[i], path[i + 1]) for i in range(len(path) - 1))


def _foreign_row_trees(slots, arr, x0, C, edge_ok):
    for y in range(MY):
        if Z.nid(x0, y) in arr:
            _row_fork_left(slots, arr, y, x0, arr[Z.nid(x0, y)], edge_ok)
        xr = x0 + C - 1
        if Z.nid(xr, y) in arr:
            _row_fork_right(slots, arr, y, xr, MX, arr[Z.nid(xr, y)], edge_ok)


def _foreign_tree_multicast(slots, arr, s, x0, C, adj):
    home = {n for n in adj if x0 <= n % MX < x0 + C}
    entries = [(n, arr[n]) for n in arr if n in home]
    if not entries:
        return False
    delivered = set(home)
    delivered.add(s)
    for d in sorted(n for n in adj if n not in delivered):
        best_path = None
        best_t0 = None
        best_lat = None
        for entry, t0 in entries:
            path = _bfs_path(adj, entry, d)
            if path is None or len(path) < 2:
                continue
            lat = _path_lat(path)
            if best_path is None or t0 + lat < best_t0 + best_lat:
                best_path, best_t0, best_lat = path, t0, lat
        if best_path is None:
            return False
        t = best_t0
        prev = best_path[0]
        for nxt in best_path[1:]:
            slots.append(('L', Z.lk(prev, nxt), t))
            t += Z.edge_lat(prev, nxt)
            if nxt not in delivered:
                slots.append(('D', nxt, t))
                delivered.add(nxt)
            prev = nxt
    return True


def _source_foreign_ok(slots, s, x0, C, adj):
    home = {n for n in adj if x0 <= n % MX < x0 + C}
    need = {d for d in adj if d != s and d not in home}
    got = {n for k, n, _ in slots if k == 'D' and n not in home}
    return need <= got


def _latency_makespan(footprints, alive):
    mk = 0
    for s in alive:
        ds = [rel for k, _, rel in footprints[s] if k == 'D']
        if ds:
            mk = max(mk, max(ds) + Z.RAMP)
    return mk


def _dijkstra(adj, src):
    import heapq
    dist = {src: 0}
    pq = [(0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1 << 60):
            continue
        for v in adj[u]:
            nd = d + Z.edge_lat(u, v)
            if nd < dist.get(v, 1 << 60):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist


def _band_bi_arrivals(order, is_cycle, s, ramp_bw):
    """Per-node arrival rels for a bi allgather over the band ring (cycle) or
    open path. Mirrors Z._ring_arrivals for cycles and sim_hamilton_ring's
    bi-on-path behaviour (source sends toward both ends) for paths."""
    n = len(order)
    pos = {nd: k for k, nd in enumerate(order)}
    i = pos[s]
    d2 = 0 if ramp_bw >= 2 else 1
    arr = {s: Z.RAMP}
    if is_cycle:
        a = n // 2
        b = (n - 1) - a
        fwd = [order[(i + k) % n] for k in range(a + 1)]
        bwd = [order[(i - k) % n] for k in range(b + 1)]
        t = Z.RAMP
        for k in range(len(fwd) - 1):
            t += Z.edge_lat(fwd[k], fwd[k + 1])
            arr[fwd[k + 1]] = t
        t = Z.RAMP + d2
        for k in range(len(bwd) - 1):
            t += Z.edge_lat(bwd[k], bwd[k + 1])
            arr[bwd[k + 1]] = t
    else:
        t = Z.RAMP
        for k in range(i, n - 1):
            t += Z.edge_lat(order[k], order[k + 1])
            arr[order[k + 1]] = t
        t = Z.RAMP + d2
        for k in range(i, 0, -1):
            t += Z.edge_lat(order[k], order[k - 1])
            arr[order[k - 1]] = t
    return arr


def _estimate_latency(dead_nodes, dead_links, B, adj, alive, bidir=True):
    all_dist = {n: _dijkstra(adj, n) for n in alive}
    worst = 0
    for s in alive:
        br = _band_ring(s, B, dead_nodes, dead_links)
        if br is None:
            return None
        order, is_cycle = br
        if not is_cycle and not bidir:
            return None
        arr = _band_bi_arrivals(order, is_cycle, s, RAMP_BW)
        ring_nodes = set(order)
        C = MX // B
        sx, _ = Z.coord(s)
        x0 = (sx // C) * C
        entries = [(n, arr[n]) for n in arr if n in ring_nodes]
        mk = max(arr.values()) + Z.RAMP if arr else Z.RAMP
        # every alive node not on the band ring (incl. sacrificed band nodes)
        # receives s's flit via shortest path from a band-ring entry node.
        for d in adj:
            if d == s or d in ring_nodes:
                continue
            best = None
            for entry, t0 in entries:
                dv = all_dist.get(entry, {}).get(d)
                if dv is None:
                    continue
                end = t0 + dv + Z.RAMP
                best = end if best is None else min(best, end)
            if best is None:
                return None
            mk = max(mk, best)
        worst = max(worst, mk)
    return worst


def fp_hybrid_v_fault(s, B, bidir, ramp_bw, dead_nodes, dead_links, adj=None):
    dead_nodes = set(dead_nodes)
    if s in dead_nodes:
        return []
    dead_links = {frozenset(l) for l in dead_links}
    if adj is None:
        adj = hr.build_adj(MX, MY, dead_nodes, dead_links)

    def edge_ok(u, v):
        return (u not in dead_nodes and v not in dead_nodes
                and frozenset((u, v)) not in dead_links)

    br = _band_ring(s, B, dead_nodes, dead_links)
    if br is None:
        return None
    order, is_cycle = br
    if not is_cycle:
        # band only has an open path (cycle structurally impossible under this
        # fault); the rigid cycle-based footprint is undefined here, so defer to
        # the latency-estimate path used by simulate() for faulted cases.
        return None
    pos = {nd: k for k, nd in enumerate(order)}
    slots, arr = Z._ring_arrivals(order, pos, s, bidir, ramp_bw)
    C = MX // B
    sx, _ = Z.coord(s)
    x0 = (sx // C) * C
    ring_len = len(slots)
    _foreign_row_trees(slots, arr, x0, C, edge_ok)
    if dead_nodes or dead_links:
        if not _source_foreign_ok(slots, s, x0, C, adj):
            slots = slots[:ring_len]
            if not _foreign_tree_multicast(slots, arr, s, x0, C, adj):
                return None
    return slots


def _eject_ok(footprints, alive):
    exp = len(alive) - 1
    got = {n: 0 for n in alive}
    for s in alive:
        for kind, key, _rel in footprints[s]:
            if kind == 'D' and key in got:
                got[key] += 1
    return all(got[n] == exp for n in alive)


def simulate(dead_nodes=(), dead_links=(), B=2, bidir=True, ramp_bw=None):
    ramp_bw = RAMP_BW if ramp_bw is None else ramp_bw
    dead_nodes = set(dead_nodes)
    dead_links = list(dead_links)
    alive = [n for n in range(N) if n not in dead_nodes]
    if not alive:
        return {"feasible": False, "reason": "no surviving nodes"}
    adj = hr.build_adj(MX, MY, dead_nodes, dead_links)
    if not hr.is_connected(adj):
        return {"feasible": False, "reason": "surviving graph disconnected"}

    if not dead_nodes and not dead_links:
        footprints = {s: [] for s in range(N)}
        for s in alive:
            footprints[s] = fp_hybrid_v_fault(
                s, B, bidir, ramp_bw, dead_nodes, dead_links, adj)
        best = None
        for order_name, gen in Z.SRC_ORDERS.items():
            mk, mo, busy, _inj, _events = Z.export_events(
                footprints, ramp_bw, gen(), flits=1)
            ok = Z.verify(busy, ramp_bw)
            rec = {"makespan": mk, "max_off": mo, "method": order_name, "pack_ok": ok}
            if ok and (best is None or mk < best["makespan"]):
                best = rec
        if best is None:
            return {"feasible": False, "reason": "packer conflict (healthy)"}
        return {"feasible": True, **best, "alive": len(alive)}

    # Faulted case: recover each band's ring (which may sacrifice some band
    # nodes to keep a closed cycle, mirroring the global rebalance). Sacrificed
    # nodes exit the allgather (not source, not receiver), like the global
    # rebalanced scenarios.
    C = MX // B
    sacrifices = set()
    for b in range(B):
        br = _band_ring_for_x0(b * C, B, dead_nodes, dead_links)
        if br is None:
            return {"feasible": False, "reason": "band ring infeasible"}
        sacrifices |= br[2]
    all_dead = dead_nodes | sacrifices
    alive_eff = [n for n in alive if n not in sacrifices]
    if not alive_eff:
        return {"feasible": False, "reason": "no participating nodes"}
    adj_eff = hr.build_adj(MX, MY, all_dead, dead_links)
    if not hr.is_connected(adj_eff):
        return {"feasible": False, "reason": "surviving graph disconnected"}

    lat = _estimate_latency(dead_nodes, dead_links, B, adj_eff, alive_eff, bidir)
    if lat is None:
        return {"feasible": False, "reason": "delivery infeasible (estimate)"}
    return {
        "feasible": True,
        "makespan": lat,
        "max_off": 0,
        "method": "latency-estimate",
        "pack_ok": False,
        "alive": len(alive_eff),
        "sacrificed": len(sacrifices),
    }


def golden_makespan(B=2):
    r = simulate([], [], B=B)
    assert r["feasible"] and r.get("pack_ok", True), r
    return r["makespan"]
