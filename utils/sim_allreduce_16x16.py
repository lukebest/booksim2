#!/usr/bin/env python3
"""16x16 conflict-free zero-buffer allreduce schedulers.

Compares ring RS+AG, tree reduce+broadcast, and dimensional / hybrid schemes.
Uses the rigid offset packer from sched_zerobuf_compare.
"""

from __future__ import annotations

import json
from pathlib import Path

import allreduce_bound as ab
import hamilton_ring as hr
import sched_zerobuf_compare as sz

MX, MY, H, V, RAMP = 16, 16, 4, 6, 1
N = MX * MY
DEFAULT_ROOT = 8 + 8 * MX
R_LAT = 2

nid = sz.nid
coord = sz.coord
edge_lat = sz.edge_lat
lk = sz.lk
manh = sz.manh
pack = sz.pack
RAMP = sz.RAMP


def shift_fp(slots, delta):
    return [(k, key, rel + delta) for k, key, rel in slots]


def dim_path(s, d):
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


def build_latency_tree(root, alive):
    """Min-latency BFS tree rooted at `root` over alive nodes."""
    alive = set(alive)
    parent = {root: -1}
    dist = {root: 0}
    frontier = [root]
    while frontier:
        nxt = []
        for u in frontier:
            ux, uy = coord(u)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = ux + dx, uy + dy
                if nx < 0 or nx >= MX or ny < 0 or ny >= MY:
                    continue
                v = nid(nx, ny)
                if v not in alive or v in parent:
                    continue
                parent[v] = u
                dist[v] = dist[u] + (H if dy == 0 else V)
                nxt.append(v)
        frontier = nxt
    children = {n: [] for n in alive}
    for n in alive:
        if n != root and parent.get(n, -1) >= 0:
            children[parent[n]].append(n)
    return parent, children, dist


def pick_root(alive):
    alive = list(alive)
    if not alive:
        return None
    cx, cy = (MX - 1) / 2.0, (MY - 1) / 2.0
    return min(alive, key=lambda n: abs(coord(n)[0] - cx) + abs(coord(n)[1] - cy))


def footprint_span(slots, flits=1):
    """Latest cycle touched by a rigid footprint (links + ejects)."""
    end = 0
    for kind, key, rel in slots:
        if kind == "L":
            p, c = key // 100000, key % 100000
            end = max(end, rel + edge_lat(p, c) + flits - 1)
        elif kind == "D":
            end = max(end, rel + flits - 1 + RAMP)
        elif kind == "U":
            end = max(end, rel + flits - 1 + RAMP)
    return end


def reduce_phase_end(root, alive, r_lat=R_LAT, flits=1):
    """When the last reduced flit is ready at root (before broadcast)."""
    mx = 0
    for s in alive:
        if s == root:
            mx = max(mx, RAMP + r_lat)
        else:
            hops = hop_count(s, root)
            mx = max(mx, RAMP + manh(s, root) + max(0, hops - 1) * r_lat + r_lat)
    return mx + flits - 1


def hop_count(a, b):
    ax, ay = coord(a)
    bx, by = coord(b)
    return abs(ax - bx) + abs(ay - by)


def fp_reduce_up(s, root, r_lat=R_LAT):
    """Leaf-to-root reduce path; no per-source down-ramp at root."""
    if s == root:
        return [("U", s, 0)]
    path = dim_path(s, root)
    slots = [("U", s, 0)]
    t = RAMP
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        slots.append(("L", lk(u, v), t))
        t += edge_lat(u, v)
        if v != root:
            t += r_lat
    return slots


def fp_root_eject(root, start, r_lat=R_LAT):
    return [("D", root, start + r_lat)]


def fp_bcast_down(root):
    return sz.fp_multitree(root)


def pack_phases(phases, ramp_bw, src_orders, flits=1):
    """Pack sequential phases; return (total_makespan, ok, phase_makespans)."""
    offset = 0
    phase_ms = []
    combined = {}
    all_ok = True
    for foot, order_gen in phases:
        shifted = {s: shift_fp(sl, offset) for s, sl in foot.items()}
        mk, _, busy = pack(shifted, ramp_bw, order_gen(), flits=flits)
        ok = verify_allreduce(busy, ramp_bw, flits, alive=set(foot.keys()) | set(range(N)))
        all_ok = all_ok and ok
        phase_ms.append(mk - offset if offset else mk)
        offset = mk + 1
        combined = shifted
    total = offset - 1 if offset else 0
    return total, all_ok, phase_ms, combined


def verify_allreduce(busy, ramp_bw, flits=1, alive=None):
    link_busy, up_busy, down_busy = busy
    link_ok = all(ct <= 1 for d in link_busy.values() for ct in d.values())
    up_ok = all(ct <= ramp_bw for d in up_busy.values() for ct in d.values())
    down_ok = all(ct <= ramp_bw for d in down_busy.values() for ct in d.values())
    if alive is None:
        alive = set(range(N))
    ejects = {n: sum(d.values()) for n, d in down_busy.items()}
    eject_ok = all(ejects.get(n, 0) == flits for n in alive)
    return link_ok and up_ok and down_ok and eject_ok


def fp_ring_rs(s, order, pos, r_lat=R_LAT):
    i = pos[s]
    n = len(order)
    chain = [order[(i + k) % n] for k in range(n)]
    slots = [("U", s, 0)]
    t = RAMP
    for k in range(len(chain) - 1):
        u, w = chain[k], chain[k + 1]
        slots.append(("L", lk(u, w), t))
        t += edge_lat(u, w)
        if w != s:
            t += r_lat
    return slots


def fp_ring_ag(s, order, pos, bidir, ramp_bw):
    return sz.fp_ring(s, order, pos, bidir, ramp_bw)


def merge_busy(busy_a, busy_b, offset):
    """Merge two busy tables; shift busy_b by offset cycles."""
    out = []
    for tbl in range(3):
        merged = {}
        for key, d in busy_a[tbl].items():
            merged[key] = dict(d)
        for key, d in busy_b[tbl].items():
            tgt = merged.setdefault(key, {})
            for cyc, ct in d.items():
                tgt[cyc + offset] = tgt.get(cyc + offset, 0) + ct
        out.append(merged)
    return tuple(out)


def scheme_tree(root, ramp_bw=1, flits=1, alive=None, r_lat=R_LAT, name="tree_reduce_bcast"):
    if alive is None:
        alive = set(range(N))
    alive = set(alive)
    _, _, dist = build_latency_tree(root, alive)
    max_lat = max(dist.get(n, 0) for n in alive)
    foot_r = {s: fp_reduce_up(s, root, r_lat) for s in alive}
    src_r = sorted(foot_r.keys(), key=lambda s: -(max_lat - dist.get(s, 0)))
    rs_end = reduce_phase_end(root, alive, r_lat, flits)
    foot_phase1 = dict(foot_r)
    foot_phase1[root] = foot_r[root] + fp_root_eject(root, rs_end - r_lat, r_lat)
    mk_r, _, busy_r = pack(foot_phase1, ramp_bw, src_r, flits=flits)
    foot_b = {root: fp_bcast_down(root)}
    mk_b, _, busy_b = pack(foot_b, ramp_bw, [root], flits=flits)
    busy_m = merge_busy(busy_r, busy_b, mk_r + 1)
    mk = mk_r + 1 + mk_b
    link_ok = all(ct <= 1 for d in busy_m[0].values() for ct in d.values())
    up_ok = all(ct <= ramp_bw for d in busy_m[1].values() for ct in d.values())
    down_ok = all(ct <= ramp_bw for d in busy_m[2].values() for ct in d.values())
    ejects = {n: sum(d.values()) for n, d in busy_m[2].items()}
    eject_ok = ejects.get(root, 0) == flits and all(
        ejects.get(n, 0) == flits for n in alive if n != root)
    ok = link_ok and up_ok and down_ok and eject_ok
    return {
        "name": name,
        "makespan": mk,
        "ok": ok,
        "root": root,
        "phase_reduce_end": mk_r,
    }


def scheme_dim_tree(root=DEFAULT_ROOT, ramp_bw=1, flits=1, r_lat=R_LAT):
    return scheme_tree(root, ramp_bw, flits, r_lat=r_lat, name="dim_multitree")


def scheme_hybrid_band(B, ramp_bw=1, flits=1, root=DEFAULT_ROOT, r_lat=R_LAT):
    """Horizontal-band local ring RS, then global tree reduce+broadcast."""
    R = MY // B
    if R < 2 or MY % B != 0:
        return {"name": f"hybrid_h{B}", "makespan": None, "ok": False, "feasible": False}
    foot_rs = {}
    for b in range(B):
        y0 = b * R
        order = sz.ham_cycle_band(R, y0)
        pos = {nd: k for k, nd in enumerate(order)}
        for s in order:
            foot_rs[s] = fp_ring_rs(s, order, pos, r_lat)
    src_rs = sorted(foot_rs.keys())
    mk_band, _, busy_band = pack(foot_rs, ramp_bw, src_rs, flits=flits)
    foot_r = {s: shift_fp(fp_reduce_up(s, root, r_lat), 0) for s in range(N)}
    _, _, dist = build_latency_tree(root, range(N))
    max_lat = max(dist.values())
    src_r = sorted(foot_r.keys(), key=lambda s: -(max_lat - dist[s]))
    rs_end = reduce_phase_end(root, range(N), r_lat, flits)
    foot_phase1 = {s: shift_fp(foot_r[s], mk_band + 1) for s in foot_r}
    foot_phase1[root] = foot_phase1[root] + fp_root_eject(root, mk_band + 1 + rs_end - r_lat, r_lat)
    mk_r, _, busy_r = pack(foot_phase1, ramp_bw, src_r, flits=flits)
    foot_b = {root: fp_bcast_down(root)}
    mk_b, _, busy_b = pack(foot_b, ramp_bw, [root], flits=flits)
    busy_mid = merge_busy(busy_band, busy_r, mk_band + 1)
    busy_all = merge_busy(busy_mid, busy_b, mk_band + 1 + mk_r + 1)
    mk = mk_band + 1 + mk_r + 1 + mk_b
    link_ok = all(ct <= 1 for d in busy_all[0].values() for ct in d.values())
    ejects = {n: sum(d.values()) for n, d in busy_all[2].items()}
    eject_ok = ejects.get(root, 0) == flits and all(
        ejects.get(n, 0) == flits for n in range(N) if n != root)
    ok = link_ok and eject_ok
    return {"name": f"hybrid_h{B}", "makespan": mk, "ok": ok, "B": B, "feasible": True}


def scheme_ring(order, is_cycle, bidir, ramp_bw=1, flits=1, alive=None, r_lat=R_LAT):
    if alive is None:
        alive = set(order)
    pos = {nd: k for k, nd in enumerate(order)}
    if not bidir and not is_cycle:
        return {"name": "ring_rs_ag_uni", "makespan": None, "ok": False,
                "feasible": False, "reason": "uni needs closed cycle"}
    foot_rs = {s: fp_ring_rs(s, order, pos, r_lat) for s in alive}
    src_rs = sorted(alive, key=lambda s: pos[s])
    mk_rs, _, busy_rs = pack(foot_rs, ramp_bw, src_rs, flits=flits)
    foot_ag = {s: fp_ring_ag(s, order, pos, bidir, ramp_bw) for s in alive}
    mk_ag, _, busy_ag = pack(foot_ag, ramp_bw, src_rs, flits=flits)
    busy_m = merge_busy(busy_rs, busy_ag, mk_rs + 1)
    mk = mk_rs + 1 + mk_ag
    link_ok = all(ct <= 1 for d in busy_m[0].values() for ct in d.values())
    ag_ok = sz.verify(busy_ag, ramp_bw, flits=flits)
    return {
        "name": "ring_bi_rs_ag" if bidir else "ring_uni_rs_ag",
        "makespan": mk,
        "ok": link_ok and ag_ok,
        "feasible": True,
        "ring_len": len(order),
        "is_cycle": is_cycle,
        "phase_rs_end": mk_rs,
    }


def scheme_hybrid_reduce_bcast(B, vertical, ramp_bw=1, flits=1, root=DEFAULT_ROOT,
                               r_lat=R_LAT):
    if vertical:
        return scheme_tree(root, ramp_bw, flits, r_lat=r_lat,
                           name=f"hybrid_v{B}_tree")
    return scheme_hybrid_band(B, ramp_bw, flits, root, r_lat)


def scheme_dim_multitree_reduce_bcast(ramp_bw=1, flits=1, root=DEFAULT_ROOT, r_lat=R_LAT):
    """Dimensional X-then-Y reduce to root, then dimensional broadcast."""
    return scheme_tree(root, ramp_bw, flits, r_lat=r_lat)


def compare_schemes(ramp_bw=1, flits=1, r_lat=R_LAT, root=DEFAULT_ROOT):
    sz.cfg(MX, MY, H, V)
    sz.init_ring()
    order = hr.snake_cycle(MX, MY)
    results = []
    results.append(scheme_tree(root, ramp_bw, flits, r_lat=r_lat))
    results.append(scheme_dim_tree(root, ramp_bw, flits, r_lat))
    results.append(scheme_ring(order, True, False, ramp_bw, flits, r_lat=r_lat))
    results.append(scheme_ring(order, True, True, ramp_bw, flits, r_lat=r_lat))
    for B in (2, 4, 8):
        if MY % B == 0 and MY // B >= 2:
            results.append(scheme_hybrid_reduce_bcast(B, False, ramp_bw, flits, root, r_lat))
        if MX % B == 0 and MX // B >= 2:
            results.append(scheme_hybrid_reduce_bcast(B, True, ramp_bw, flits, root, r_lat))
    feasible = [r for r in results if r.get("makespan") is not None and r.get("ok")]
    best = min(feasible, key=lambda r: r["makespan"]) if feasible else None
    lb = ab.allreduce_bounds(flits, r_lat, ramp_bw)["combined"]
    return {
        "flits": flits,
        "r_lat": r_lat,
        "ramp_bw": ramp_bw,
        "lower_bound": lb,
        "schemes": results,
        "best": best,
        "efficiency": best["makespan"] / lb if best else None,
    }


def simulate_fault(dead_nodes=(), dead_links=(), ramp_bw=1, flits=1, r_lat=R_LAT,
                   scheme="tree"):
    sz.cfg(MX, MY, H, V)
    adj = hr.build_adj(MX, MY, dead_nodes, dead_links)
    alive = set(adj.keys())
    if len(alive) <= 1:
        return {"feasible": False, "reason": "too few alive nodes"}
    root = pick_root(alive)
    ring_res = hr.find_ring(MX, MY, dead_nodes, dead_links)
    if not ring_res["feasible"]:
        ring_res = hr.find_ring_rebalanced(MX, MY, dead_nodes, dead_links)
    candidates = []
    if scheme in ("tree", "auto"):
        candidates.append(scheme_tree(root, ramp_bw, flits, alive, r_lat))
    if scheme in ("ring", "auto") and ring_res["feasible"]:
        order = [n for n in ring_res["order"] if n in alive]
        candidates.append(scheme_ring(order, ring_res["is_cycle"], True,
                                    ramp_bw, flits, alive, r_lat))
    good = [c for c in candidates if c.get("makespan") and c.get("ok")]
    if not good:
        return {"feasible": False, "reason": "no conflict-free schedule",
                "ring": ring_res}
    best = min(good, key=lambda c: c["makespan"])
    return {
        "feasible": True,
        "makespan": best["makespan"],
        "scheme": best["name"],
        "ok": best["ok"],
        "root": root,
        "ring": ring_res,
        "best_detail": best,
    }


def sweep_m(ramp_bw=1, r_lat=R_LAT):
    out = []
    for M in range(1, 7):
        row = compare_schemes(ramp_bw, M, r_lat)
        row["M"] = M
        out.append(row)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default=None)
    ap.add_argument("--m", type=int, default=1)
    ap.add_argument("--r-lat", type=int, default=R_LAT)
    args = ap.parse_args()
    sz.cfg(MX, MY, H, V)
    sz.init_ring()
    res = compare_schemes(1, args.m, args.r_lat)
    lb = res["lower_bound"]
    print(f"16x16 allreduce  M={args.m}  R_LAT={args.r_lat}  LB={lb}\n")
    for s in res["schemes"]:
        ms = s.get("makespan")
        tag = f"{ms:5d}" if ms else "  N/A"
        ok = s.get("ok", False)
        print(f"  {s['name']:24s}  makespan={tag}  ok={ok}")
    if res["best"]:
        b = res["best"]
        eff = b["makespan"] / lb
        print(f"\nBest: {b['name']}  makespan={b['makespan']}  efficiency={eff:.4f}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
