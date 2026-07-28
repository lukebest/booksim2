#!/usr/bin/env python3
"""Reachability analysis for M0 east-first, and what fixes its blind spot.

East-first bans the two turns *into* east (N->E, S->E), so no turn ever leads
back to east and every eastward hop must precede the first N/S/W hop. A legal
path is therefore always shaped:

    a straight eastward run inside the source row,  then  an N/S/W-only walk

`reach_model` builds the reachable set straight from that shape, with no turn
search involved, and the scan cross-checks it against `gen_east_first` — a
zero-mismatch result means the failure characterisation is exact, not empirical.

Three routers are compared on every fault set:
  east_first  M0 as implemented (XY preferred, turn-aware BFS fallback)
  xy          M1, the stricter DOR baseline
  dual        east-first on VC0 + west-first on VC1, each pair locked to one VC
              (west-first bans the turns into west, so it can make exactly the
              detour east-first cannot)

  python3 utils/pg_east_first_reach.py           # 1-fault spaces + catalog
  python3 utils/pg_east_first_reach.py --full    # + all 2-link / 2-node sets
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.setrecursionlimit(10000)

import pg_faults_8x6 as F
import pg_routing as R
from pg_routing import MX, MY, coord, dir_of, nid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_east_first_reach.json"

E, W, N, S = 0, 1, 2, 3


def _ef_turn(d_in: int, d_out: int) -> bool:
    if d_in == d_out:
        return True
    if d_in == (d_out ^ 1):
        return False
    return (d_in, d_out) not in ((N, E), (S, E))


def _wf_turn(d_in: int, d_out: int) -> bool:
    if d_in == d_out:
        return True
    if d_in == (d_out ^ 1):
        return False
    return (d_in, d_out) not in ((N, W), (S, W))


# ---------------------------------------------------------------------------
# closed-form reachability (independent of the router)
# ---------------------------------------------------------------------------

def _nsw_reach(starts, adj) -> set[int]:
    """Nodes reachable using only N/S/W hops; 180 turns still forbidden."""
    seen = set(starts)
    q = deque((n, -1) for n in starts)
    done = set()
    while q:
        u, din = q.popleft()
        if (u, din) in done:
            continue
        done.add((u, din))
        for v in adj.get(u, ()):
            dout = dir_of(u, v)
            if dout == E or (din >= 0 and din == (dout ^ 1)):
                continue
            seen.add(v)
            q.append((v, dout))
    return seen


def reach_model(s: int, adj: dict[int, list[int]]) -> set[int]:
    x, y = coord(s)
    run = [s]
    xm = x
    while xm + 1 < MX and nid(xm + 1, y) in adj.get(nid(xm, y), ()):
        xm += 1
        run.append(nid(xm, y))
    # The eastward run may be cut short at any column it passes through.
    return _nsw_reach(run, adj) | set(run)


def model_says_fail(pg: dict) -> bool:
    adj = pg["route_adj"]
    live = [n for n in pg["compute_nodes"] if adj.get(n)]
    for s in live:
        r = reach_model(s, adj)
        if any(d not in r for d in live if d != s):
            return True
    return False


# ---------------------------------------------------------------------------
# routers
# ---------------------------------------------------------------------------

def dual_feasible(pg: dict) -> tuple[bool, bool, float]:
    """(feasible, cdg_acyclic, fraction of pairs needing west-first/VC1)."""
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    paths, which = {}, {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = R.xy_path(s, d, adj) or R._turn_bfs(s, d, adj, _ef_turn)
            vc = 0
            if p is None:
                p = R._turn_bfs(s, d, adj, _wf_turn)
                vc = 1
            if p is None:
                return False, False, 0.0
            paths[(s, d)], which[(s, d)] = p, vc

    def vc_of(path, i):
        del i
        return which[(path[0], path[-1])]

    ok, _ = R.validate_routing(paths, compute, adj, vc_of)
    n1 = sum(1 for v in which.values() if v == 1)
    return True, ok, (n1 / len(which) if which else 0.0)


def _mk(dn=(), dl=()) -> dict:
    return F.expand_pg({"name": "probe", "dead_nodes": list(dn),
                        "dead_links": [tuple(l) for l in dl]}, "dead")


def _drop_iso(pg: dict, sem: str = "dead") -> dict:
    iso = {n for n in pg["compute_nodes"] if not pg["route_adj"].get(n)}
    return R.apply_sacrifice(pg, iso, sem == "dead") if iso else pg


def all_links() -> list[tuple[int, int]]:
    out = []
    for n in range(MX * MY):
        x, y = coord(n)
        if x + 1 < MX:
            out.append((n, nid(x + 1, y)))
        if y + 1 < MY:
            out.append((n, nid(x, y + 1)))
    return out


def run_space(name: str, cases, mismatches: list) -> dict:
    c = Counter()
    for dn, dl in cases:
        pg = _drop_iso(_mk(dn, dl))
        ef = R.gen_east_first(pg) is not None
        if (not ef) != model_says_fail(pg):  # model must agree with the router
            mismatches.append({"space": name,
                               "dead_nodes": [str(coord(n)) for n in dn],
                               "dead_links": [f"{coord(a)}-{coord(b)}"
                                              for a, b in dl],
                               "router_ok": ef})
        c["east_first_ok"] += ef
        c["xy_ok"] += R.gen_xy(pg) is not None
        feas, acyc, _ = dual_feasible(pg)
        c["dual_ok"] += feas and acyc
        c["dual_cyclic"] += feas and not acyc
        c["n"] += 1
    print(f"  {name}: {dict(c)}", flush=True)
    return dict(c)


def main() -> None:
    full = "--full" in sys.argv
    t0 = time.time()
    links = all_links()
    nodes = list(range(MX * MY))
    mismatches: list = []
    spaces: dict[str, dict] = {}

    spaces["1_link"] = run_space(
        "单链路断（全部 82）", [((), [l]) for l in links], mismatches)
    spaces["1_node"] = run_space(
        "单节点死（全部 48）", [([n], ()) for n in nodes], mismatches)
    if full:
        spaces["2_link"] = run_space(
            "双链路断（全部 3321）",
            [((), list(c)) for c in itertools.combinations(links, 2)],
            mismatches)
        spaces["2_node"] = run_space(
            "双节点死（全部 1128）",
            [(list(c), ()) for c in itertools.combinations(nodes, 2)],
            mismatches)

    # Which single faults break M0, split by link orientation / node column.
    by_kind: Counter = Counter()
    v_fail_cols: set[int] = set()
    for a, b in links:
        kind = "H" if coord(a)[1] == coord(b)[1] else "V"
        ok = R.gen_east_first(_drop_iso(_mk((), [(a, b)]))) is not None
        by_kind[f"{kind}_{'ok' if ok else 'fail'}"] += 1
        if kind == "V" and not ok:
            v_fail_cols.add(coord(a)[0])
    node_ok_cols = sorted({coord(n)[0] for n in nodes
                           if R.gen_east_first(_drop_iso(_mk([n], ())))
                           is not None})

    # Catalog: failing cells and what recovery costs there.
    catalog = {}
    for scen in F.all_scenarios():
        for sem in ("dead", "transit"):
            pg = F.expand_pg(scen, sem)
            base = _drop_iso(pg, sem)
            if R.gen_east_first(base) is not None:
                catalog[f"{scen['name']}/{sem}"] = {"verdict": "ok"}
                continue
            sol = R.solve_scheme(pg, "east_first")
            feas, acyc, vc1 = dual_feasible(base)
            catalog[f"{scen['name']}/{sem}"] = {
                "verdict": "fail_path",
                "sacrifice": sol["n_sacrificed"],
                "A": sol["n_compute_used"],
                "xy_sacrifice": R.solve_scheme(pg, "xy")["n_sacrificed"],
                "dual_fixes": bool(feas and acyc),
                "dual_vc1_frac": round(vc1, 4),
            }
            print(f"  catalog {scen['name']:22s} {sem:8s} fail → "
                  f"sac={sol['n_sacrificed']} dual="
                  f"{'ok' if feas and acyc else 'no'} "
                  f"vc1={vc1:.1%}", flush=True)

    fails = [v for v in catalog.values() if v["verdict"] != "ok"]
    doc = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "full": full,
            "elapsed_s": round(time.time() - t0, 1),
            "model_mismatches": len(mismatches),
        },
        "spaces": spaces,
        "single_fault_breakdown": {
            "by_link_orientation": dict(by_kind),
            "vertical_fail_columns": sorted(v_fail_cols),
            "node_ok_columns": node_ok_cols,
        },
        "catalog": catalog,
        "catalog_summary": {
            "n_fail": len(fails),
            "sacrifice_total": sum(f["sacrifice"] for f in fails),
            "sacrifice_median": (sorted(f["sacrifice"] for f in fails)
                                 [len(fails) // 2] if fails else 0),
            "dual_fixes_all": all(f["dual_fixes"] for f in fails),
        },
        "model_mismatches": mismatches[:20],
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"\nWrote {OUT}  ({doc['meta']['elapsed_s']}s, "
          f"model mismatches={len(mismatches)})")


if __name__ == "__main__":
    main()
