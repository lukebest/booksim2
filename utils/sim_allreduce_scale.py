#!/usr/bin/env python3
"""Parameterized conflict-free zero-buffer allreduce schedulers.

Explores {INC vs node reduce} x {reduce+broadcast vs reduce-scatter+allgather}
over arbitrary 2D mesh sizes using the rigid offset packer from
sched_zerobuf_compare.
"""

from __future__ import annotations

import json
from pathlib import Path

import allreduce_bound as ab
import hamilton_ring as hr
import sched_zerobuf_compare as sz

H, V, RAMP = 4, 6, 1
INC_LAT = 3
NODE_RED_LAT = 12
RAMP_BW = 1

ROOT = Path(__file__).resolve().parents[1]
AG_SWEEP_JSON = ROOT / "results" / "allgather_scale_sweep.json"

nid = sz.nid
coord = sz.coord
edge_lat = sz.edge_lat
lk = sz.lk
manh = sz.manh
pack = sz.pack


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


def hop_count(a, b):
    ax, ay = coord(a)
    bx, by = coord(b)
    return abs(ax - bx) + abs(ay - by)


def build_latency_tree(root, alive):
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
                if nx < 0 or nx >= sz.MX or ny < 0 or ny >= sz.MY:
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
    cx, cy = (sz.MX - 1) / 2.0, (sz.MY - 1) / 2.0
    return min(alive, key=lambda n: abs(coord(n)[0] - cx) + abs(coord(n)[1] - cy))


def merge_lat(reduce_mode: str, inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT):
    return node_red_lat if reduce_mode == "node" else inc_lat


def merge_slots(v, t, reduce_mode: str, flits=1,
                inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT):
    """Footprint slots and time advance at an intermediate merge node."""
    if reduce_mode == "inc":
        return [], t + inc_lat
    u_start = max(t + 1, t + node_red_lat - flits)
    return [("D", v, t), ("U", v, u_start)], t + node_red_lat


def reduce_phase_end(root, alive, reduce_mode, flits=1,
                     inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT):
    ml = merge_lat(reduce_mode, inc_lat, node_red_lat)
    mx = 0
    for s in alive:
        if s == root:
            mx = max(mx, RAMP + ml)
        else:
            hops = hop_count(s, root)
            mx = max(mx, RAMP + manh(s, root) + max(0, hops - 1) * ml + ml)
    return mx + flits - 1


def fp_reduce_up(s, root, reduce_mode="inc", flits=1,
                 inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT):
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
            extra, t = merge_slots(v, t, reduce_mode, flits, inc_lat, node_red_lat)
            slots.extend(extra)
    return slots


def fp_root_eject(root, start, reduce_mode="inc",
                  inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT):
    ml = merge_lat(reduce_mode, inc_lat, node_red_lat)
    return [("D", root, start + ml)]


def fp_bcast_down(root):
    return sz.fp_multitree(root)


def merge_busy(busy_a, busy_b, offset):
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


def verify_phase(busy, ramp_bw, flits=1, require_eject=None):
    """Conflict check; optional per-node down-ramp eject count."""
    link_busy, up_busy, down_busy = busy
    link_ok = all(ct <= 1 for d in link_busy.values() for ct in d.values())
    up_ok = all(ct <= ramp_bw for d in up_busy.values() for ct in d.values())
    down_ok = all(ct <= ramp_bw for d in down_busy.values() for ct in d.values())
    if require_eject is None:
        return link_ok and up_ok and down_ok
    ejects = {n: sum(d.values()) for n, d in down_busy.items()}
    eject_ok = all(ejects.get(n, 0) == flits for n in require_eject)
    return link_ok and up_ok and down_ok and eject_ok


def verify_allreduce(busy, ramp_bw, flits=1, alive=None):
    if alive is None:
        alive = set(range(sz.N))
    return verify_phase(busy, ramp_bw, flits, require_eject=alive)


def fp_ring_rs(s, order, pos, reduce_mode="inc", flits=1,
               inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT):
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
            extra, t = merge_slots(w, t, reduce_mode, flits, inc_lat, node_red_lat)
            slots.extend(extra)
    return slots


def fp_ring_ag(s, order, pos, bidir, ramp_bw):
    return sz.fp_ring(s, order, pos, bidir, ramp_bw)


def scheme_tree(root, ramp_bw=RAMP_BW, flits=1, alive=None,
                reduce_mode="inc", inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT,
                name="tree_reduce_bcast"):
    if alive is None:
        alive = set(range(sz.N))
    alive = set(alive)
    _, _, dist = build_latency_tree(root, alive)
    max_lat = max(dist.get(n, 0) for n in alive)
    foot_r = {s: fp_reduce_up(s, root, reduce_mode, flits, inc_lat, node_red_lat)
              for s in alive}
    src_r = sorted(foot_r.keys(), key=lambda s: -(max_lat - dist.get(s, 0)))
    rs_end = reduce_phase_end(root, alive, reduce_mode, flits, inc_lat, node_red_lat)
    foot_phase1 = dict(foot_r)
    foot_phase1[root] = (foot_r[root]
                         + fp_root_eject(root, rs_end - merge_lat(reduce_mode,
                                                                  inc_lat,
                                                                  node_red_lat),
                                         reduce_mode, inc_lat, node_red_lat))
    mk_r, _, busy_r = pack(foot_phase1, ramp_bw, src_r, flits=flits)
    reduce_ok = verify_phase(busy_r, ramp_bw, flits)
    foot_b = {root: fp_bcast_down(root)}
    mk_b, _, busy_b = pack(foot_b, ramp_bw, [root], flits=flits)
    busy_m = merge_busy(busy_r, busy_b, mk_r + 1)
    mk = mk_r + 1 + mk_b
    phase_ok = verify_phase(busy_m, ramp_bw, flits)
    _, _, down_b = busy_b
    ejects_b = {n: sum(d.values()) for n, d in down_b.items()}
    eject_ok = all(ejects_b.get(n, 0) == flits for n in alive if n != root)
    ok = reduce_ok and phase_ok and eject_ok
    return {
        "name": name,
        "algo": "tree_bcast",
        "reduce_mode": reduce_mode,
        "makespan": mk,
        "ok": ok,
        "root": root,
        "phase_reduce_end": mk_r,
        "phase_bcast_end": mk,
    }


def scheme_hybrid_band(B, ramp_bw=RAMP_BW, flits=1, root=None,
                       reduce_mode="inc", inc_lat=INC_LAT,
                       node_red_lat=NODE_RED_LAT):
    if root is None:
        root = ab.default_root()
    R = sz.MY // B
    if R < 2 or sz.MY % B != 0:
        return {"name": f"hybrid_h{B}", "algo": "tree_bcast", "reduce_mode": reduce_mode,
                "makespan": None, "ok": False, "feasible": False}
    foot_rs = {}
    for b in range(B):
        y0 = b * R
        order = sz.ham_cycle_band(R, y0)
        pos = {nd: k for k, nd in enumerate(order)}
        for s in order:
            foot_rs[s] = fp_ring_rs(s, order, pos, reduce_mode, flits,
                                    inc_lat, node_red_lat)
    src_rs = sorted(foot_rs.keys())
    mk_band, _, busy_band = pack(foot_rs, ramp_bw, src_rs, flits=flits)
    foot_r = {s: shift_fp(fp_reduce_up(s, root, reduce_mode, flits,
                                       inc_lat, node_red_lat), 0)
              for s in range(sz.N)}
    _, _, dist = build_latency_tree(root, range(sz.N))
    max_lat = max(dist.values())
    src_r = sorted(foot_r.keys(), key=lambda s: -(max_lat - dist[s]))
    rs_end = reduce_phase_end(root, range(sz.N), reduce_mode, flits,
                              inc_lat, node_red_lat)
    foot_phase1 = {s: shift_fp(foot_r[s], mk_band + 1) for s in foot_r}
    ml = merge_lat(reduce_mode, inc_lat, node_red_lat)
    foot_phase1[root] = (foot_phase1[root]
                         + fp_root_eject(root, mk_band + 1 + rs_end - ml,
                                         reduce_mode, inc_lat, node_red_lat))
    mk_r, _, busy_r = pack(foot_phase1, ramp_bw, src_r, flits=flits)
    foot_b = {root: fp_bcast_down(root)}
    mk_b, _, busy_b = pack(foot_b, ramp_bw, [root], flits=flits)
    busy_mid = merge_busy(busy_band, busy_r, mk_band + 1)
    busy_all = merge_busy(busy_mid, busy_b, mk_band + 1 + mk_r + 1)
    mk = mk_band + 1 + mk_r + 1 + mk_b
    phase_ok = verify_phase(busy_all, ramp_bw, flits)
    _, _, down_b = busy_b
    ejects = {n: sum(d.values()) for n, d in down_b.items()}
    eject_ok = ejects.get(root, 0) == flits and all(
        ejects.get(n, 0) == flits for n in range(sz.N) if n != root)
    return {
        "name": f"hybrid_h{B}",
        "algo": "tree_bcast",
        "reduce_mode": reduce_mode,
        "makespan": mk,
        "ok": phase_ok and eject_ok,
        "B": B,
        "feasible": True,
        "root": root,
    }


def scheme_ring_rs_ag(order, is_cycle, bidir, ramp_bw=RAMP_BW, flits=1,
                      alive=None, reduce_mode="inc", inc_lat=INC_LAT,
                      node_red_lat=NODE_RED_LAT, ag_makespan=None,
                      ag_name=None):
    if alive is None:
        alive = set(order)
    pos = {nd: k for k, nd in enumerate(order)}
    if not bidir and not is_cycle:
        return {"name": "ring_rs_ag_uni", "algo": "rs_ag", "reduce_mode": reduce_mode,
                "makespan": None, "ok": False, "feasible": False,
                "reason": "uni needs closed cycle"}
    foot_rs = {s: fp_ring_rs(s, order, pos, reduce_mode, flits,
                             inc_lat, node_red_lat) for s in alive}
    src_rs = sorted(alive, key=lambda s: pos[s])
    mk_rs, _, busy_rs = pack(foot_rs, ramp_bw, src_rs, flits=flits)
    rs_ok = verify_phase(busy_rs, ramp_bw, flits)

    if ag_makespan is not None:
        mk = mk_rs + 1 + ag_makespan
        name = f"ring_{'bi' if bidir else 'uni'}_rs_optag"
        return {
            "name": name,
            "algo": "rs_ag",
            "reduce_mode": reduce_mode,
            "makespan": mk,
            "ok": rs_ok,
            "feasible": True,
            "ring_len": len(order),
            "is_cycle": is_cycle,
            "phase_rs_end": mk_rs,
            "ag_scheme": ag_name,
            "ag_makespan": ag_makespan,
        }

    foot_ag = {s: fp_ring_ag(s, order, pos, bidir, ramp_bw) for s in alive}
    mk_ag, _, busy_ag = pack(foot_ag, ramp_bw, src_rs, flits=flits)
    busy_m = merge_busy(busy_rs, busy_ag, mk_rs + 1)
    mk = mk_rs + 1 + mk_ag
    link_ok = all(ct <= 1 for d in busy_m[0].values() for ct in d.values())
    ag_ok = sz.verify(busy_ag, ramp_bw, flits=flits)
    return {
        "name": f"ring_{'bi' if bidir else 'uni'}_rs_ag",
        "algo": "rs_ag",
        "reduce_mode": reduce_mode,
        "makespan": mk,
        "ok": link_ok and ag_ok and rs_ok,
        "feasible": True,
        "ring_len": len(order),
        "is_cycle": is_cycle,
        "phase_rs_end": mk_rs,
        "phase_ag_end": mk,
    }


def load_optimal_ag(size_key, flits, ramp_bw=RAMP_BW, ag_data=None):
    if ag_data is None:
        if not AG_SWEEP_JSON.exists():
            return None, None
        ag_data = json.loads(AG_SWEEP_JSON.read_text(encoding="utf-8"))
    block = ag_data.get("data", {}).get(size_key, {})
    cell = block.get("bw", {}).get(str(ramp_bw), {}).get(str(flits), {})
    best = cell.get("best_zero_buffer") or cell.get("best")
    if not best:
        return None, None
    return best.get("makespan"), best.get("name")


def ring_order(mx, my):
    if my % 2 == 0:
        return hr.snake_cycle(mx, my), True
    order = []
    for y in range(my):
        xs = range(mx) if y % 2 == 0 else range(mx - 1, -1, -1)
        for x in xs:
            order.append(hr.nid(x, y, mx))
    return order, False


def compare_schemes(mx, my, ramp_bw=RAMP_BW, flits=1, reduce_mode="inc",
                    inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT,
                    ag_data=None, root=None):
    sz.cfg(mx, my, H, V)
    ab.cfg(mx, my, H, V)
    sz.init_ring()
    if root is None:
        root = ab.default_root()
    order, is_cycle = ring_order(mx, my)

    results = []
    results.append(scheme_tree(root, ramp_bw, flits, reduce_mode=reduce_mode,
                               inc_lat=inc_lat, node_red_lat=node_red_lat))
    for B in (2, 4, 8):
        if sz.MY % B == 0 and sz.MY // B >= 2:
            results.append(scheme_hybrid_band(B, ramp_bw, flits, root,
                                              reduce_mode, inc_lat, node_red_lat))

    ag_mk, ag_name = load_optimal_ag(f"{mx}x{my}", flits, ramp_bw, ag_data)
    results.append(scheme_ring_rs_ag(order, is_cycle, True, ramp_bw, flits,
                                     reduce_mode=reduce_mode, inc_lat=inc_lat,
                                     node_red_lat=node_red_lat,
                                     ag_makespan=ag_mk, ag_name=ag_name))
    if is_cycle:
        results.append(scheme_ring_rs_ag(order, is_cycle, False, ramp_bw, flits,
                                         reduce_mode=reduce_mode, inc_lat=inc_lat,
                                         node_red_lat=node_red_lat,
                                         ag_makespan=ag_mk, ag_name=ag_name))

    feasible = [r for r in results if r.get("makespan") is not None and r.get("ok")]
    best_tree = min((r for r in feasible if r.get("algo") == "tree_bcast"),
                    key=lambda r: r["makespan"], default=None)
    best_rsag = min((r for r in feasible if r.get("algo") == "rs_ag"),
                    key=lambda r: r["makespan"], default=None)
    best = min(feasible, key=lambda r: r["makespan"]) if feasible else None
    lb = ab.allreduce_bounds(flits, ramp_bw=ramp_bw, reduce_mode=reduce_mode,
                             inc_lat=inc_lat, node_red_lat=node_red_lat)
    return {
        "mx": mx, "my": my, "flits": flits, "reduce_mode": reduce_mode,
        "inc_lat": inc_lat, "node_red_lat": node_red_lat,
        "ramp_bw": ramp_bw, "lower_bound": lb["combined"],
        "lower_bound_rsag": lb["combined_rsag"],
        "bounds": lb,
        "schemes": results,
        "best": best,
        "best_tree_bcast": best_tree,
        "best_rs_ag": best_rsag,
        "efficiency": best["makespan"] / lb["combined"] if best else None,
    }


def compare_quadrants(mx, my, flits=1, ramp_bw=RAMP_BW,
                      inc_lat=INC_LAT, node_red_lat=NODE_RED_LAT, ag_data=None):
    out = {}
    for mode in ("inc", "node"):
        res = compare_schemes(mx, my, ramp_bw, flits, mode, inc_lat,
                              node_red_lat, ag_data)
        out[mode] = res
    best_overall = None
    for mode, res in out.items():
        if res.get("best"):
            cand = dict(res["best"])
            cand["reduce_mode"] = mode
            cand["ratio"] = cand["makespan"] / res["lower_bound"]
            if best_overall is None or cand["makespan"] < best_overall["makespan"]:
                best_overall = cand
    out["best_overall"] = best_overall
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int, default=16)
    ap.add_argument("--my", type=int, default=16)
    ap.add_argument("--m", type=int, default=1)
    ap.add_argument("--reduce-mode", choices=("inc", "node", "both"), default="both")
    ap.add_argument("--inc-lat", type=int, default=INC_LAT)
    ap.add_argument("--node-red-lat", type=int, default=NODE_RED_LAT)
    ap.add_argument("--json", type=str, default=None)
    args = ap.parse_args()

    modes = ("inc", "node") if args.reduce_mode == "both" else (args.reduce_mode,)
    results = {}
    for mode in modes:
        res = compare_schemes(args.mx, args.my, flits=args.m,
                              reduce_mode=mode, inc_lat=args.inc_lat,
                              node_red_lat=args.node_red_lat)
        results[mode] = res
        lb = res["lower_bound"]
        print(f"{args.mx}x{args.my} allreduce  M={args.m}  mode={mode}  LB={lb}")
        for s in res["schemes"]:
            ms = s.get("makespan")
            tag = f"{ms:5d}" if ms else "  N/A"
            print(f"  {s['name']:28s}  makespan={tag}  ok={s.get('ok', False)}")
        if res["best"]:
            b = res["best"]
            print(f"  Best: {b['name']}  makespan={b['makespan']}  "
                  f"eff={b['makespan']/lb:.4f}\n")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
