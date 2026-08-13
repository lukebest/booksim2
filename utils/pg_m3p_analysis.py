#!/usr/bin/env python3
"""Quantitative backing for the M3' Up*/Down* proof + load-balance slides.

Everything the slides claim about M3' is measured here, on the same routing code
the DSE uses, and cached to results/pg_m3p_analysis.json:

  theorem44   — residual-graph connectivity vs zero-sacrifice feasibility over
                the 44-scenario budget catalogue (Theorem 1 check)
  maximality  — for every live root: how many of the forbidden down->up turns
                can be re-permitted without closing a CDG cycle (Theorem 2)
  placement   — different acyclic turn sets (XY / Glass-Ni / Up*/Down* /
                XY-seeded maximal) at equal 1 VC: peak link load, router
                transit load, all-to-all makespan from the DES
  selection   — min-max path re-selection inside a fixed legal set
  loads       — per-directed-link all-to-all load maps used for the heat maps

  python3 utils/pg_m3p_analysis.py            # recompute everything
  python3 utils/pg_m3p_analysis.py --quick    # skip the 44-scenario sweep
"""
from __future__ import annotations

import argparse
import heapq
import json
import random
import statistics as st
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pg_faults_8x6 as F
import pg_faults_budget_8x6 as B
import pg_routing as R

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "pg_m3p_analysis.json"

E, W, NN, S = 0, 1, 2, 3
DEMO_DEAD_NODES = [(3, 2), (4, 2)]
DEMO_DEAD_LINK = ((1, 4), (2, 4))


# ---------------------------------------------------------------------------
# Turn-set algebra.  A turn is (c_in, c_out) = ((u,v), (v,w)), 180 deg excluded.
# ---------------------------------------------------------------------------

def all_turns(adj: dict[int, list[int]]) -> list[tuple]:
    return [((u, v), (v, w)) for u in adj for v in adj[u] for w in adj[v]
            if w != u]


def ud_turnset(adj: dict[int, list[int]], labels: dict[int, int]) -> set[tuple]:
    """Up*/Down*: every turn except down -> up."""
    T = set()
    for a, b in all_turns(adj):
        u, v = a
        _, w = b
        if labels[v] > labels[u] and labels[w] < labels[v]:
            continue
        T.add((a, b))
    return T


def model_turnset(adj: dict[int, list[int]],
                  banned: set[tuple[int, int]]) -> set[tuple]:
    """Turn model given as banned (in_dir, out_dir) pairs, e.g. XY = Y->X."""
    T = set()
    for a, b in all_turns(adj):
        u, v = a
        _, w = b
        if (R.dir_of(u, v), R.dir_of(v, w)) in banned:
            continue
        T.add((a, b))
    return T


XY_BAN = {(NN, E), (NN, W), (S, E), (S, W)}
WEST_FIRST_BAN = {(NN, W), (S, W)}
NEG_FIRST_BAN = {(E, S), (NN, W)}


def turn_digraph(T: set[tuple]) -> dict[tuple, set[tuple]]:
    g: dict[tuple, set[tuple]] = {}
    for a, b in T:
        g.setdefault(a, set()).add(b)
    return g


def reaches(g: dict, src, dst) -> bool:
    if src == dst:
        return True
    seen, stack = {src}, [src]
    while stack:
        u = stack.pop()
        for v in g.get(u, ()):
            if v == dst:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


def turn_set_acyclic(T: set[tuple]) -> bool:
    g = turn_digraph(T)
    color: dict[tuple, int] = {}

    def dfs(u):
        color[u] = 1
        for v in g.get(u, ()):
            c = color.get(v, 0)
            if c == 1 or (c == 0 and not dfs(v)):
                return False
        color[u] = 2
        return True

    return all(dfs(n) for n in list(g) if color.get(n, 0) == 0)


def addable_turns(adj, T: set[tuple]) -> list[tuple]:
    """Forbidden turns that could be re-permitted without closing a cycle."""
    g = turn_digraph(T)
    return [(a, b) for a, b in all_turns(adj)
            if (a, b) not in T and not reaches(g, b, a)]


def augment_maximal(adj, T: set[tuple], rng: random.Random) -> tuple[set, int]:
    """Greedily grow T to a maximal acyclic turn set."""
    T = set(T)
    g = turn_digraph(T)
    order = all_turns(adj)
    rng.shuffle(order)
    added = 0
    for a, b in order:
        if (a, b) in T or reaches(g, b, a):
            continue
        T.add((a, b))
        g.setdefault(a, set()).add(b)
        added += 1
    return T, added


# ---------------------------------------------------------------------------
# Path selection inside a fixed legal set (min-max link load)
# ---------------------------------------------------------------------------

def turn_dijkstra(s: int, d: int, adj: dict[int, list[int]], T: set[tuple],
                  load: dict[tuple[int, int], int], alpha: float = 3.0
                  ) -> list[int] | None:
    """Cheapest T-legal path; cost of a link = (load+1)^alpha (convex => spreads)."""
    if s == d:
        return [s]
    INF = float("inf")
    dist: dict[tuple[int, int], float] = {}
    prev: dict[tuple[int, int], tuple[int, int] | None] = {}
    pq: list[tuple[float, int, int]] = []
    for v in adj[s]:
        c = (load.get((s, v), 0) + 1) ** alpha
        dist[(s, v)] = c
        prev[(s, v)] = None
        heapq.heappush(pq, (c, s, v))
    goal = None
    while pq:
        c, u, v = heapq.heappop(pq)
        if c > dist.get((u, v), INF):
            continue
        if v == d:
            goal = (u, v)
            break
        for w in adj[v]:
            if w == u or ((u, v), (v, w)) not in T:
                continue
            nc = c + (load.get((v, w), 0) + 1) ** alpha
            if nc < dist.get((v, w), INF):
                dist[(v, w)] = nc
                prev[(v, w)] = (u, v)
                heapq.heappush(pq, (nc, v, w))
    if goal is None:
        return None
    seq = [goal]
    while prev[seq[-1]] is not None:
        seq.append(prev[seq[-1]])           # type: ignore[arg-type]
    seq.reverse()
    return [seq[0][0]] + [e[1] for e in seq]


def minmax_paths(adj, compute, T: set[tuple], *, init=None, rounds: int = 6,
                 alpha: float = 3.0) -> dict | None:
    """Rip-up / re-route every pair inside T until the peak link load settles.

    Any subset of T-legal paths keeps the CDG inside the (acyclic) turn digraph,
    so deadlock freedom and reachability are unaffected by the choice.
    """
    load = {(u, v): 0 for u in adj for v in adj[u]}
    paths: dict[tuple[int, int], list[int]] = {}
    if init is not None:
        paths = {k: list(v) for k, v in init.items()}
        for p in paths.values():
            for i in range(len(p) - 1):
                load[(p[i], p[i + 1])] += 1
    else:
        for s in compute:
            for d in compute:
                if s == d:
                    continue
                p = turn_dijkstra(s, d, adj, T, load, alpha)
                if p is None:
                    return None
                paths[(s, d)] = p
                for i in range(len(p) - 1):
                    load[(p[i], p[i + 1])] += 1
    keys = list(paths)
    for _ in range(rounds):
        keys.sort(key=lambda k: -max(load[(paths[k][i], paths[k][i + 1])]
                                     for i in range(len(paths[k]) - 1)))
        for k in keys:
            p = paths[k]
            for i in range(len(p) - 1):
                load[(p[i], p[i + 1])] -= 1
            q = turn_dijkstra(k[0], k[1], adj, T, load, alpha)
            if q is None:
                return None
            paths[k] = q
            for i in range(len(q) - 1):
                load[(q[i], q[i + 1])] += 1
    return paths


def all_pairs_routable(adj, compute, T: set[tuple]) -> bool:
    for s in compute:
        for d in compute:
            if s != d and turn_dijkstra(s, d, adj, T, {}, 1.0) is None:
                return False
    return True


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def load_stats(adj, paths, lb: int) -> dict[str, Any]:
    ld = R.link_loads(paths)
    vals = [ld.get((u, v), 0) for u in adj for v in adj[u]]
    n = len(vals)
    mean = sum(vals) / n
    srt = sorted(vals)
    gini = (sum((2 * i - n + 1) * v for i, v in enumerate(srt))
            / (n * sum(vals)))
    transit: dict[int, int] = defaultdict(int)
    for p in paths.values():
        for node in p[1:-1]:
            transit[node] += 1
    peak = max(vals)
    return {
        "peak": peak,
        "peak_over_lb": round(peak / lb, 3),
        "mean": round(mean, 1),
        "cv": round(st.pstdev(vals) / mean, 3),
        "gini": round(gini, 3),
        "hot80": sum(1 for v in vals if v >= 0.8 * peak),
        "hot90": sum(1 for v in vals if v >= 0.9 * peak),
        "n_links": n,
        "hops": sum(len(p) - 1 for p in paths.values()),
        "avg_hops": round(sum(len(p) - 1 for p in paths.values()) / len(paths), 3),
        "router_transit_peak": max(transit.values()),
        "throughput_ratio": round(lb / peak, 3),
    }


def des_makespan(paths, compute, adj, m: int) -> int | None:
    import dse_pg_alltoall_8x6 as D
    res = D.simulate_alltoall(paths, compute, adj, m=m, Q=19, num_vc=1)
    return res["makespan"] if res else None


def dump_loads(adj, paths) -> dict[str, int]:
    ld = R.link_loads(paths)
    return {"%d-%d" % (u, v): ld.get((u, v), 0)
            for u in sorted(adj) for v in sorted(adj[u])}


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

def demo_pg() -> dict:
    scen = {
        "name": "m3p_demo", "fault_class": "mixed", "region": "center",
        "detail": "demo",
        "dead_nodes": [F.nid(x, y) for x, y in DEMO_DEAD_NODES],
        "dead_links": [(F.nid(*DEMO_DEAD_LINK[0]), F.nid(*DEMO_DEAD_LINK[1]))],
        "desc": "2 node holes + 1 link cut",
    }
    return F.expand_pg(scen, "dead")


def xy_sacrifice(pg: dict, scheme: str = "xy") -> dict[str, Any]:
    """XY on the *same* residual graph.

    Dimension-order routing cannot cover a graph with holes, so the DSE
    sacrifice ladder (solve_scheme) throws healthy nodes away until the table
    builds. Reporting that surviving subset is what makes an apples-to-apples
    heat map possible: same faults, same links, but far fewer live pairs.
    """
    sol = R.solve_scheme(pg, scheme)
    if not sol["feasible"]:
        return {"routable": False, "scheme": scheme}
    kept, adj = sorted(sol["compute_nodes"]), sol["route_adj"]
    lb = R.minimax_load_lb(kept, adj)
    rec = load_stats(adj, sol["paths"], lb)
    rec.update(routable=True, scheme=scheme, lb=lb, kept=kept, n_kept=len(kept),
               sacrificed=sol["sacrificed"], n_sacrificed=sol["n_sacrificed"],
               n_pairs=len(sol["paths"]), n_good=len(pg["compute_nodes"]))
    return rec


def xy_best_sacrifice(pg: dict) -> dict[str, Any]:
    """XY at its best on the same residual graph.

    solve_scheme's generic ladder is conservative (it falls back to a line), so
    for a fair picture we search XY's own optimum directly: greedily drop the
    node involved in the most broken XY pairs until every surviving pair has an
    intact L-shaped path. The result is the largest hole-free sub-grid XY can
    serve, i.e. an upper bound on how well XY can do here.
    """
    adj, good = pg["route_adj"], list(pg["compute_nodes"])
    S = set(good)
    while True:
        bad = [(s, d) for s in S for d in S
               if s != d and R.xy_path(s, d, adj) is None]
        if not bad:
            break
        cnt: dict[int, int] = {}
        for s, d in bad:
            cnt[s] = cnt.get(s, 0) + 1
            cnt[d] = cnt.get(d, 0) + 1
        S.discard(max(sorted(cnt), key=lambda n: (cnt[n], -n)))
    kept = sorted(S)
    paths = {(s, d): R.xy_path(s, d, adj) for s in kept for d in kept if s != d}
    lb = R.minimax_load_lb(kept, adj)
    rec = load_stats(adj, paths, lb)
    rec.update(routable=True, scheme="xy_greedy_sacrifice", lb=lb, kept=kept,
               n_kept=len(kept), n_sacrificed=len(good) - len(kept),
               n_pairs=len(paths), n_good=len(good),
               loads=dump_loads(adj, paths))
    return rec


def theorem44() -> dict[str, Any]:
    """Theorem 1 check: residual graph connected <=> M3' needs zero sacrifice."""
    out = []
    for scen in B.stratified_scenarios(n_per_cell=1, seed=0):
        pg = B.expand_budget(scen, "dead")
        adj, compute = pg["route_adj"], pg["compute_nodes"]
        seen: set[int] = set()
        comps = []
        for n in adj:
            if n in seen:
                continue
            c = R.bfs_reachable(adj, n)
            seen |= c
            comps.append(len(c))
        comps.sort(reverse=True)
        raw = R.gen_updown_best_root(pg)
        ok = False
        if raw:
            ok, _ = R.validate_routing(raw["paths"], compute, adj)
        xy = R.gen_xy(pg)
        xy_ok = False
        if xy:
            xy_ok, _ = R.validate_routing(xy["paths"], compute, adj)
        out.append({
            "name": scen["name"], "n_routers": scen["n_routers"],
            "n_links": scen["n_links"], "n_components": len(comps),
            "isolated": sum(comps[1:]), "connected": len(comps) == 1,
            "m3p_zero_sacrifice": bool(raw and ok),
            "xy_zero_sacrifice": bool(xy and xy_ok),
            "peak": R.max_link_load(raw["paths"]) if raw else None,
            "root": raw["root"] if raw else None,
            "lb": R.minimax_load_lb(compute, adj),
        })
        print("   %-16s comp=%d iso=%2d m3p0sac=%-5s xy0sac=%s"
              % (out[-1]["name"], out[-1]["n_components"], out[-1]["isolated"],
                 out[-1]["m3p_zero_sacrifice"], out[-1]["xy_zero_sacrifice"]),
              flush=True)
    conn = [r for r in out if r["connected"]]
    disc = [r for r in out if not r["connected"]]
    return {
        "rows": out,
        "n": len(out),
        "n_connected": len(conn),
        "n_disconnected": len(disc),
        "connected_all_zero_sacrifice": all(r["m3p_zero_sacrifice"]
                                            for r in conn),
        "disconnected_isolated": [r["isolated"] for r in disc],
        "disconnected_names": [r["name"] for r in disc],
        "violations": sum(1 for r in out
                          if r["connected"] != r["m3p_zero_sacrifice"]),
        "xy_zero_sacrifice": sum(1 for r in out if r["xy_zero_sacrifice"]),
    }


def maximality(pgs: dict[str, dict]) -> dict[str, Any]:
    """Theorem 2 check: no forbidden down->up turn can be re-permitted."""
    out = {}
    for tag, pg in pgs.items():
        adj = pg["route_adj"]
        AT = all_turns(adj)
        per_root = []
        for root in sorted(adj):
            labels = R._updown_labels(adj, root)
            if labels is None or len(labels) < len(adj):
                continue
            T = ud_turnset(adj, labels)
            per_root.append({
                "root": root,
                "permitted": len(T),
                "forbidden": len(AT) - len(T),
                "acyclic": turn_set_acyclic(T),
                "addable": len(addable_turns(adj, T)),
            })
        out[tag] = {
            "total_turns": len(AT),
            "n_roots": len(per_root),
            "permitted_min": min(r["permitted"] for r in per_root),
            "permitted_max": max(r["permitted"] for r in per_root),
            "permitted_frac": round(
                st.mean(r["permitted"] for r in per_root) / len(AT), 4),
            "forbidden_tested": sum(r["forbidden"] for r in per_root),
            "addable_total": sum(r["addable"] for r in per_root),
            "all_acyclic": all(r["acyclic"] for r in per_root),
        }
        print("   maximality[%s]: roots=%d permitted=%d-%d/%d forbidden "
              "tested=%d addable=%d" % (
                  tag, out[tag]["n_roots"], out[tag]["permitted_min"],
                  out[tag]["permitted_max"], out[tag]["total_turns"],
                  out[tag]["forbidden_tested"], out[tag]["addable_total"]),
              flush=True)
    return out


def random_maximal_sizes(adj, k: int = 12) -> dict[str, Any]:
    sizes = []
    for seed in range(k):
        T, _ = augment_maximal(adj, set(), random.Random(2000 + seed))
        sizes.append(len(T))
    return {"n": k, "min": min(sizes), "max": max(sizes),
            "median": st.median(sizes)}


def placement_study(pg: dict, tag: str, *, with_des=(1, 13),
                    with_loads=()) -> dict[str, Any]:
    """Same 1 VC hardware, different acyclic turn sets -> load + makespan."""
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    lb = R.minimax_load_lb(compute, adj)
    AT = all_turns(adj)
    res: dict[str, Any] = {"lb": lb, "total_turns": len(AT), "schemes": {},
                           "loads": {}}

    m3p = R.gen_updown_best_root(pg)
    root = m3p["root"]
    labels = R._updown_labels(adj, root)
    Tud = ud_turnset(adj, labels)
    m3 = R.gen_updown(pg)

    entries: list[tuple[str, set[tuple] | None, dict | None]] = [
        ("xy", model_turnset(adj, XY_BAN), None),
        ("m3", None, m3["paths"] if m3 else None),
        ("m3p", Tud, m3p["paths"]),
        ("m3p_minmax", Tud, "minmax"),
        ("west_first", model_turnset(adj, WEST_FIRST_BAN), "minmax"),
        ("negative_first", model_turnset(adj, NEG_FIRST_BAN), "minmax"),
        ("xy_seeded_maximal", "xyseed", "minmax"),
    ]
    for name, T, how in entries:
        if T == "xyseed":
            best = None
            for seed in range(4):
                T2, _ = augment_maximal(adj, model_turnset(adj, XY_BAN),
                                        random.Random(seed))
                p = minmax_paths(adj, compute, T2)
                if p is None:
                    continue
                pk = R.max_link_load(p)
                if best is None or pk < best[0]:
                    best = (pk, T2, p)
            if best is None:
                res["schemes"][name] = {"routable": False}
                print("   %-18s not routable" % name, flush=True)
                continue
            _, T, paths = best
        elif how == "minmax":
            if not all_pairs_routable(adj, compute, T):
                res["schemes"][name] = {"routable": False,
                                        "permitted": len(T)}
                print("   %-18s not routable" % name, flush=True)
                continue
            init = m3p["paths"] if name == "m3p_minmax" else None
            paths = minmax_paths(adj, compute, T, init=init)
        else:
            paths = how
            if name == "xy":
                paths = {}
                for s in compute:
                    for d in compute:
                        if s == d:
                            continue
                        q = R.xy_path(s, d, adj)
                        if q is None:
                            paths = None
                            break
                        paths[(s, d)] = q
                    if paths is None:
                        break
        if paths is None:
            res["schemes"][name] = {"routable": False}
            print("   %-18s not routable" % name, flush=True)
            continue
        ok, msg = R.validate_routing(paths, compute, adj)
        rec = load_stats(adj, paths, lb)
        rec.update(routable=True, valid=ok, reason=msg,
                   permitted=(len(T) if T else None),
                   root=(root if name.startswith("m3p") else
                         (m3["root"] if name == "m3" else None)))
        for m in with_des:
            rec["makespan_m%d" % m] = des_makespan(paths, compute, adj, m)
        res["schemes"][name] = rec
        if name in with_loads:
            res["loads"][name] = dump_loads(adj, paths)
        print("   %-18s peak=%4d (%.2fx) transit=%4d mk1=%s mk13=%s valid=%s"
              % (name, rec["peak"], rec["peak_over_lb"],
                 rec["router_transit_peak"], rec.get("makespan_m1"),
                 rec.get("makespan_m13"), ok), flush=True)
    res["root"] = root
    res["root_xy"] = list(F.coord(root))
    res["m3_root"] = m3["root"] if m3 else None
    res["m3_root_xy"] = list(F.coord(m3["root"])) if m3 else None
    return res


def _find_cycle(g: dict) -> list | None:
    color: dict[tuple, int] = {}
    parent: dict[tuple, tuple] = {}
    for s in list(g):
        if color.get(s, 0):
            continue
        color[s] = 1
        stack = [(s, iter(g.get(s, ())))]
        while stack:
            u, it = stack[-1]
            advanced = False
            for v in it:
                c = color.get(v, 0)
                if c == 1:                          # back edge closes a cycle
                    cyc = [v]
                    x = u
                    while x != v:
                        cyc.append(x)
                        x = parent[x]
                    cyc.reverse()
                    return cyc
                if c == 0:
                    color[v] = 1
                    parent[v] = u
                    stack.append((v, iter(g.get(v, ()))))
                    advanced = True
                    break
            if not advanced:
                color[u] = 2
                stack.pop()
    return None


def _swap_in(T: set[tuple], t: tuple, prio: dict) -> tuple[set | None, int]:
    """Permit turn t, then ban the least-used turn on every cycle it closes."""
    T = set(T)
    T.add(t)
    dropped = 0
    for _ in range(60):
        cyc = _find_cycle(turn_digraph(T))
        if cyc is None:
            return T, dropped
        edges = [(cyc[i], cyc[(i + 1) % len(cyc)]) for i in range(len(cyc))]
        edges = [e for e in edges if e != t and e in T]
        if not edges:
            return None, dropped
        T.discard(min(edges, key=lambda e: prio.get(e, 0)))
        dropped += 1
    return None, dropped


def _turn_usage(paths) -> dict[tuple, int]:
    u: dict[tuple, int] = defaultdict(int)
    for p in paths.values():
        for i in range(len(p) - 2):
            u[((p[i], p[i + 1]), (p[i + 1], p[i + 2]))] += 1
    return u


def swap_search(pg: dict, *, iters: int = 120, seed: int = 0,
                fanout: int = 14, with_des=(1, 13)) -> dict[str, Any]:
    """M3'' : move the 70 restrictions instead of adding turns.

    Start from the M3' turn set (guaranteed routable), repeatedly permit a
    forbidden turn that touches the hottest link and re-ban whatever closes a
    cycle. Every accepted step is re-verified for all-pair routability and CDG
    acyclicity, so M3' remains the guaranteed fallback.
    """
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    lb = R.minimax_load_lb(compute, adj)
    AT = all_turns(adj)
    m3p = R.gen_updown_best_root(pg)
    root = m3p["root"]
    T = ud_turnset(adj, R._updown_labels(adj, root))
    paths = minmax_paths(adj, compute, T, init=m3p["paths"])
    peak = R.max_link_load(paths)
    start_peak = peak
    rng = random.Random(seed)
    trace = []
    t0 = time.time()
    for it in range(iters):
        ld = R.link_loads(paths)
        hot = max(ld, key=lambda e: ld[e])
        prio = _turn_usage(paths)
        cands = [t for t in AT if t not in T]
        rng.shuffle(cands)
        near = set(hot)
        cands.sort(key=lambda t: 0 if (near & {t[0][0], t[0][1], t[1][1]})
                   else 1)
        for t in cands[:fanout]:
            T2, _ = _swap_in(T, t, prio)
            if T2 is None:
                continue
            p2 = minmax_paths(adj, compute, T2, rounds=3)
            if p2 is None:
                continue
            pk2 = R.max_link_load(p2)
            if pk2 >= peak:
                continue
            ok, _ = R.validate_routing(p2, compute, adj)
            if not ok:
                continue
            T, paths, peak = T2, p2, pk2
            trace.append({"iter": it, "peak": peak, "permitted": len(T)})
            print("      it%3d peak -> %d (%.2fx) |T|=%d  %.0fs"
                  % (it, peak, peak / lb, len(T), time.time() - t0), flush=True)
            break
    paths = minmax_paths(adj, compute, T, init=paths, rounds=8)
    ok, msg = R.validate_routing(paths, compute, adj)
    rec = load_stats(adj, paths, lb)
    rec.update(valid=ok, reason=msg, permitted=len(T), total_turns=len(AT),
               root=root, start_peak=start_peak, iters=iters, seed=seed,
               trace=trace, search_s=round(time.time() - t0, 1))
    for m in with_des:
        rec["makespan_m%d" % m] = des_makespan(paths, compute, adj, m)
    return rec


def root_sweep(pg: dict) -> dict[str, Any]:
    """Peak load per root, before and after min-max re-selection."""
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    rows = []
    for root in sorted(adj):
        base = R._updown_table(adj, compute, root, "ud")
        if not base:
            continue
        T = ud_turnset(adj, R._updown_labels(adj, root))
        la = minmax_paths(adj, compute, T, init=base, rounds=5)
        rows.append({"root": root, "xy": list(F.coord(root)),
                     "base": R.max_link_load(base),
                     "minmax": R.max_link_load(la) if la else None})
    b = [r["base"] for r in rows]
    m = [r["minmax"] for r in rows if r["minmax"]]
    return {"rows": rows,
            "base": {"min": min(b), "median": st.median(b), "max": max(b)},
            "minmax": {"min": min(m), "median": st.median(m), "max": max(m)}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="skip the 44-scenario theorem sweep")
    ap.add_argument("--swap-iters", type=int, default=120)
    ap.add_argument("-o", type=Path, default=OUT_JSON)
    args = ap.parse_args()

    t0 = time.time()
    healthy = F.healthy_pg()
    demo = demo_pg()

    print("placement study (healthy)")
    ph = placement_study(healthy, "healthy",
                         with_loads=("xy", "m3p", "m3p_minmax"))
    print("placement study (2 holes + 1 cut)")
    pd = placement_study(demo, "demo", with_loads=("m3p", "m3p_minmax"))
    print("XY on the same residual graph (sacrifice needed)")
    pd["xy_sacrifice"] = xy_sacrifice(demo)
    pd["xy_best"] = xy_best_sacrifice(demo)
    pd["loads"]["xy_best"] = pd["xy_best"].pop("loads")
    print("   DSE ladder keeps %s/%s; XY's own optimum keeps %d/%d, peak=%d "
          "(%.2fx its own cut bound)"
          % (pd["xy_sacrifice"].get("n_kept"), pd["xy_sacrifice"].get("n_good"),
             pd["xy_best"]["n_kept"], pd["xy_best"]["n_good"],
             pd["xy_best"]["peak"], pd["xy_best"]["peak_over_lb"]))
    pd["dead_nodes"] = [F.nid(x, y) for x, y in DEMO_DEAD_NODES]
    pd["dead_links"] = [[F.nid(*DEMO_DEAD_LINK[0]), F.nid(*DEMO_DEAD_LINK[1])]]
    pd["n_compute"] = len(demo["compute_nodes"])
    print("root sweep (healthy)")
    rs = healthy_rs = root_sweep(healthy)
    print("   base   min=%d med=%.0f max=%d" % (rs["base"]["min"],
                                                rs["base"]["median"],
                                                rs["base"]["max"]))
    print("   minmax min=%d med=%.0f max=%d" % (rs["minmax"]["min"],
                                                rs["minmax"]["median"],
                                                rs["minmax"]["max"]))
    print("maximality")
    mx = maximality({"healthy": healthy, "demo": demo})
    rand = random_maximal_sizes(healthy["route_adj"])
    print("   random maximal sets: %s" % rand)
    print("swap search (healthy)")
    sw_h = swap_search(healthy, iters=args.swap_iters)
    print("swap search (2 holes + 1 cut)")
    sw_d = swap_search(demo, iters=args.swap_iters)

    doc: dict[str, Any] = {
        "meta": {
            "mx": F.MX, "my": F.MY, "H": R.H, "V": R.V, "Q": 19, "num_vc": 1,
            "traffic": "alltoall, 1 packet per ordered pair, m flits/packet",
            "demo_fault": {"dead_nodes": DEMO_DEAD_NODES,
                           "dead_link": DEMO_DEAD_LINK},
            "generated_s": None,
        },
        "healthy": ph,
        "demo": pd,
        "root_sweep_healthy": healthy_rs,
        "maximality": mx,
        "random_maximal_healthy": rand,
        "swap_search": {"healthy": sw_h, "demo": sw_d},
    }
    if not args.quick:
        print("theorem 1 check over the 44-scenario budget catalogue")
        doc["theorem44"] = theorem44()
    doc["meta"]["generated_s"] = round(time.time() - t0, 1)
    args.o.parent.mkdir(parents=True, exist_ok=True)
    args.o.write_text(json.dumps(doc, indent=1))
    print("wrote %s  (%.0fs)" % (args.o, time.time() - t0))


if __name__ == "__main__":
    main()
