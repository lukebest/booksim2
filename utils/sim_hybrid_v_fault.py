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


def _band_ring(s, B, dead_nodes, dead_links):
    C = MX // B
    sx, _ = Z.coord(s)
    x0 = (sx // C) * C
    band_dead = [_global_to_band_local(n, x0, C)
                 for n in dead_nodes if x0 <= hr.coord(n, MX)[0] < x0 + C]
    band_links = _links_to_local(dead_links, x0, C)
    if not band_dead and not band_links:
        return Z.ham_cycle_vband(C, x0), True
    r = hr.find_ring(C, MY, band_dead, band_links, time_budget=15.0)
    if not r["feasible"] or not r["order"]:
        return None
    order = [_band_local_to_global(n, x0, C) for n in r["order"]]
    return order, r["is_cycle"]


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
        pos = {nd: k for k, nd in enumerate(order)}
        _slots, arr = Z._ring_arrivals(order, pos, s, True, RAMP_BW)
        C = MX // B
        sx, _ = Z.coord(s)
        x0 = (sx // C) * C
        home = {n for n in adj if x0 <= n % MX < x0 + C}
        entries = [(n, arr[n]) for n in arr if n in home]
        mk = max(arr.values()) + Z.RAMP if arr else Z.RAMP
        for d in adj:
            if d == s or d in home:
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
    if not bidir and not is_cycle:
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

    lat = _estimate_latency(dead_nodes, dead_links, B, adj, alive, bidir)
    if lat is None:
        return {"feasible": False, "reason": "delivery infeasible (estimate)"}
    return {
        "feasible": True,
        "makespan": lat,
        "max_off": 0,
        "method": "latency-estimate",
        "pack_ok": False,
        "alive": len(alive),
    }


def golden_makespan(B=2):
    r = simulate([], [], B=B)
    assert r["feasible"] and r.get("pack_ok", True), r
    return r["makespan"]
