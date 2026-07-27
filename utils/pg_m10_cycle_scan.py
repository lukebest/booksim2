#!/usr/bin/env python3
"""Search the 8x6 fault space for cases where M10 (virtual mesh) deadlocks.

`gen_virtual_mesh` has no constructive deadlock-freedom proof: it builds two
candidate tables — the trimmed one (U-turn loops removed) and the plain
concatenation — and ships whichever has an acyclic CDG. This scan classifies a
fault set by what the two candidates do:

  both_ok      — either table would work
  trim_only    — the raw concatenation cycles, trimming saves it
  raw_only     — trimming introduces a cycle, the raw fallback saves it
  BOTH_CYCLIC  — M10 fails outright and falls back to sacrificing good nodes
  nopath       — no table can be built at all (unrelated to deadlock)

Run after touching gen_virtual_mesh / _trim_revisits to check for regressions.
Writes results/pg_m10_cycle_scan.json.

  python3 utils/pg_m10_cycle_scan.py            # exhaustive ≤2 faults + samples
  python3 utils/pg_m10_cycle_scan.py --full     # + exhaustive 3-dead-node (~8min)
"""
from __future__ import annotations

import itertools
import json
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.setrecursionlimit(10000)

import pg_faults_8x6 as F
import pg_routing as R
from pg_routing import MX, MY, coord, nid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_m10_cycle_scan.json"
SEED = 20260727

# Hand-picked dead-node shapes probing the compact-vs-staggered hypothesis.
SHAPES = {
    "紧凑一行 (3,2)(4,2)(5,2)": [(3, 2), (4, 2), (5, 2)],
    "紧凑一列 (3,1)(3,2)(3,3)": [(3, 1), (3, 2), (3, 3)],
    "紧凑 L (3,2)(4,2)(4,3)": [(3, 2), (4, 2), (4, 3)],
    "2x2 块 @(3,2)": [(3, 2), (4, 2), (3, 3), (4, 3)],
    "3x3 块 @(3,2)": [(x, y) for x in (3, 4, 5) for y in (2, 3, 4)],
    "同行分散 (1,2)(4,2)(7,2)": [(1, 2), (4, 2), (7, 2)],
    "同列分散 (3,1)(3,3)(3,5)": [(3, 1), (3, 3), (3, 5)],
    "阶梯对角 (1,0)(3,1)(5,1)": [(1, 0), (3, 1), (5, 1)],
    "阶梯对角 (2,0)(4,1)(6,1)": [(2, 0), (4, 1), (6, 1)],
    "错列相邻 (1,0)(2,2)(4,2)": [(1, 0), (2, 2), (4, 2)],
}


def all_links() -> list[tuple[int, int]]:
    out = []
    for n in range(MX * MY):
        x, y = coord(n)
        if x + 1 < MX:
            out.append((n, nid(x + 1, y)))
        if y + 1 < MY:
            out.append((n, nid(x, y + 1)))
    return out


def _mk_pg(dead_nodes=(), dead_links=()) -> dict:
    return F.expand_pg({"name": "probe", "dead_nodes": list(dead_nodes),
                        "dead_links": [tuple(l) for l in dead_links]}, "dead")


def _candidate_tables(pg: dict):
    """Rebuild both tables gen_virtual_mesh would try → {trim: ok, raw: ok}."""
    adj = pg["route_adj"]
    alive = {n for n, nbs in adj.items() if nbs}
    compute = [n for n in pg["compute_nodes"] if n in alive]
    if len(compute) < 2:
        return None

    expand = {}
    for a in alive:
        ax, ay = coord(a)
        for dx, dy in R.DIRS:
            bx, by = ax + dx, ay + dy
            if not (0 <= bx < MX and 0 <= by < MY):
                continue
            b = nid(bx, by)
            if b in alive:
                p = R._expand_logical_edge(a, b, adj)
                if p is not None:
                    expand[(a, b)] = p

    concat = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            way = [n for n in R._logical_xy(s, d) if n in alive]
            if not way or way[0] != s or way[-1] != d:
                return None
            phys = [s]
            for i in range(len(way) - 1):
                seg = expand.get((way[i], way[i + 1])) or \
                    R.shortest_path(way[i], way[i + 1], adj)
                if seg is None:
                    return None
                phys.extend(seg[1:])
            concat[(s, d)] = phys

    res = {}
    for trim in (True, False):
        paths, x_hops = {}, {}
        for (s, d), raw in concat.items():
            phys = R._trim_revisits(raw) if trim else raw
            dx = coord(d)[0]
            n_x = 0
            if coord(s)[0] != dx:
                for i in range(len(phys) - 1):
                    n_x += 1
                    if coord(phys[i + 1])[0] == dx:
                        break
            paths[(s, d)] = phys
            x_hops[(s, d)] = n_x

        def vc_of(path, i, _x=x_hops):
            return 0 if i < _x[(path[0], path[-1])] else 1

        res[trim] = R.validate_routing(paths, compute, adj, vc_of)[0]
    return res


def verdict(dead_nodes=(), dead_links=()) -> str:
    r = _candidate_tables(_mk_pg(dead_nodes, dead_links))
    if r is None:
        return "nopath"
    t, w = r[True], r[False]
    return ("both_ok" if t and w else "trim_only" if t
            else "raw_only" if w else "BOTH_CYCLIC")


def _label_links(dl):
    return [f"{coord(a)}-{coord(b)}" for a, b in dl]


def run_stage(name, cases, fails, t0):
    """cases yields (dead_nodes, dead_links); returns a Counter."""
    cnt = Counter()
    for i, (dn, dl) in enumerate(cases):
        v = verdict(dn, dl)
        cnt[v] += 1
        if v == "BOTH_CYCLIC":
            fails.append({"stage": name,
                          "dead_nodes": [str(coord(n)) for n in dn],
                          "dead_links": _label_links(dl)})
        if (i + 1) % 2000 == 0:
            print(f"    {name}: {i+1} done, {time.time()-t0:.0f}s, "
                  f"BOTH_CYCLIC={cnt['BOTH_CYCLIC']}", flush=True)
    print(f"  {name}: {dict(cnt)}", flush=True)
    return cnt


def main() -> None:
    full = "--full" in sys.argv
    t0 = time.time()
    links = all_links()
    nodes = list(range(MX * MY))
    rnd = random.Random(SEED)
    fails: list[dict] = []
    stages: dict[str, dict] = {}

    print("穷举阶段")
    stages["1_link"] = dict(run_stage(
        "单链路断（全部 %d）" % len(links),
        (((), [l]) for l in links), fails, t0))
    stages["1_node"] = dict(run_stage(
        "单节点死（全部 %d）" % len(nodes),
        (([n], ()) for n in nodes), fails, t0))
    stages["2_link"] = dict(run_stage(
        "双链路断（全部 %d）" % (len(links) * (len(links) - 1) // 2),
        (((), list(c)) for c in itertools.combinations(links, 2)), fails, t0))
    stages["2_node"] = dict(run_stage(
        "双节点死（全部 %d）" % (len(nodes) * (len(nodes) - 1) // 2),
        ((list(c), ()) for c in itertools.combinations(nodes, 2)), fails, t0))

    if full:
        stages["3_node"] = dict(run_stage(
            "三节点死（全部 %d）" % (48 * 47 * 46 // 6),
            ((list(c), ()) for c in itertools.combinations(nodes, 3)),
            fails, t0))

    print("随机抽样阶段")

    def rand_links(n):
        for _ in range(n):
            yield (), rnd.sample(links, rnd.randint(3, 6))

    def rand_mixed(n):
        for _ in range(n):
            yield (rnd.sample(nodes, rnd.randint(1, 4)),
                   rnd.sample(links, rnd.randint(0, 4)))

    stages["rand_3to6_links"] = dict(run_stage(
        "随机 3–6 断链 ×6000", rand_links(6000), fails, t0))
    stages["rand_nodes_links"] = dict(run_stage(
        "随机 1–4 死点 + 0–4 断链 ×4000", rand_mixed(4000), fails, t0))

    print("形状假设检验")
    shapes = {}
    for name, cs in SHAPES.items():
        shapes[name] = verdict([nid(x, y) for x, y in cs], ())
        print(f"  {name:28s} -> {shapes[name]}")

    print("出厂故障目录")
    catalog = {}
    for s in F.all_scenarios():
        for sem in ("dead", "transit"):
            pg = F.expand_pg(s, sem)
            r = _candidate_tables(pg)
            v = ("nopath" if r is None else
                 "both_ok" if r[True] and r[False] else
                 "trim_only" if r[True] else
                 "raw_only" if r[False] else "BOTH_CYCLIC")
            catalog[f"{s['name']}/{sem}"] = v
    print("  ", dict(Counter(catalog.values())))

    doc = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed": SEED,
            "full_3node": full,
            "elapsed_s": round(time.time() - t0, 1),
        },
        "stages": stages,
        "shapes": shapes,
        "catalog": catalog,
        "catalog_summary": dict(Counter(catalog.values())),
        "failures": fails,
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"\nWrote {OUT}  ({doc['meta']['elapsed_s']}s, "
          f"{len(fails)} BOTH_CYCLIC)")


if __name__ == "__main__":
    main()
