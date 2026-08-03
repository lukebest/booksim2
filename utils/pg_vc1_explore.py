#!/usr/bin/env python3
"""Probe 1VC schemes beyond stock M3: multi-root pack + link-slot TDM.

Outputs results/pg_vc1_explore.json. Not wired into the report generators.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utils"))

import pg_faults_8x6 as F
from pg_faults_8x6 import expand_pg
from pg_routing import (
    MX, MY, N, RAMP, RAMP_BW, _tree_path, _updown_labels, apply_sacrifice,
    build_cdg, cdg_acyclic, coord, dir_of, link_lat, max_link_load, nid,
    shortest_path, solve_scheme, validate_routing,
)
from dse_pg_alltoall_8x6 import (
    DEFAULT_Q, DIRS, INJ, STALL_LIMIT, T_MAX, Flit, _vc_for, simulate_alltoall,
)

OUT = ROOT / "results" / "pg_vc1_explore.json"


def e2e_ns(A: int, m0: int, mk: int, freq: float) -> float:
    return (math.ceil(4 * 1152 * m0 / A) + mk) / freq


def enum_ud_tables(pg: dict, modes: tuple[str, ...] = ("ud",)) -> list[dict]:
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    out: list[dict] = []
    if len(compute) < 2 or not adj:
        return out
    for root in sorted(adj.keys()):
        labels = _updown_labels(adj, root)
        if not labels:
            continue
        for mode in modes:
            paths: dict[tuple[int, int], list[int]] = {}
            ok = True
            hops = 0
            for s in compute:
                for d in compute:
                    if s == d:
                        continue
                    p = _tree_path(s, d, adj, labels, mode)
                    if p is None:
                        ok = False
                        break
                    paths[(s, d)] = p
                    hops += len(p) - 1
                if not ok:
                    break
            if not ok:
                continue
            if not validate_routing(paths, compute, adj)[0]:
                continue
            out.append({
                "root": root, "mode": mode, "paths": paths,
                "load": max_link_load(paths), "hops": hops,
            })
    out.sort(key=lambda t: (t["load"], t["hops"], t["root"]))
    return out


def _pack(pg: dict, paths: dict, scheme: str, meta: dict | None = None,
          sac: list | None = None) -> dict:
    sac = list(sac or [])
    return {
        "feasible": True, "paths": paths, "vc_of": None, "num_vc": 1,
        "compute_nodes": pg["compute_nodes"], "route_adj": pg["route_adj"],
        "sacrificed": sac, "n_sacrificed": len(sac),
        "n_compute_used": len(pg["compute_nodes"]),
        "scheme": scheme, "meta": meta or {},
    }


def with_m3_sac(pg: dict, builder: Callable) -> dict | None:
    r = builder(pg)
    if r is not None:
        return r
    base = solve_scheme(pg, "updown")
    if not base["feasible"]:
        return None
    pg2 = apply_sacrifice(pg, set(base["sacrificed"]), True)
    r = builder(pg2)
    if r is None:
        return {**base, "scheme": "fallback_m3", "meta": {}}
    r = dict(r)
    r["sacrificed"] = base["sacrificed"]
    r["n_sacrificed"] = base["n_sacrificed"]
    return r


def build_m3_prime(pg: dict) -> dict | None:
    tabs = enum_ud_tables(pg, ("ud",))
    if not tabs:
        return None
    t = tabs[0]
    return _pack(pg, t["paths"], "m3_prime",
                 {"root": t["root"], "load": t["load"]})


def build_multi_root_pack(pg: dict, K: int = 8,
                          modes: tuple[str, ...] = ("ud", "du")) -> dict | None:
    """Greedy: candidate UD/DU paths from top-K load roots; keep CDG acyclic."""
    tabs = enum_ud_tables(pg, modes)[:K]
    if not tabs:
        return None
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    cands: dict[tuple[int, int], list[list[int]]] = defaultdict(list)
    seen: dict[tuple[int, int], set[tuple]] = defaultdict(set)
    for t in tabs:
        for od, p in t["paths"].items():
            pt = tuple(p)
            if pt in seen[od]:
                continue
            seen[od].add(pt)
            cands[od].append(p)
    for od in cands:
        cands[od].sort(key=len)
    ods = sorted(cands, key=lambda od: (-(len(cands[od][0]) - 1), od))
    paths: dict[tuple[int, int], list[int]] = {}
    cdg: dict = defaultdict(set)
    for od in ods:
        chosen = None
        for path in cands[od]:
            added = []
            chans = [(path[i], path[i + 1]) for i in range(len(path) - 1)]
            for i in range(len(chans) - 1):
                u, v = (chans[i], 0), (chans[i + 1], 0)
                if v not in cdg[u]:
                    cdg[u].add(v)
                    added.append((u, v))
                _ = cdg[v]
            if cdg_acyclic(cdg):
                chosen = path
                break
            for u, v in added:
                cdg[u].discard(v)
        if chosen is None:
            return None
        paths[od] = chosen
    if not validate_routing(paths, compute, adj)[0]:
        return None
    return _pack(pg, paths, "multi_root_pack",
                 {"K": K, "modes": list(modes), "load": max_link_load(paths)})


def build_sp_table(pg: dict) -> dict | None:
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    paths = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = shortest_path(s, d, adj)
            if p is None:
                return None
            paths[(s, d)] = p
    return _pack(pg, paths, "shortest", {"load": max_link_load(paths)})


# ---------------------------------------------------------------------------
# DES variants: OD subset (phase TDM) and edge-slot gating (link TDM)
# ---------------------------------------------------------------------------

def simulate_alltoall_ex(
    paths: dict[tuple[int, int], list[int]],
    compute: list[int],
    adj: dict[int, list[int]],
    m: int = 1,
    Q: int = DEFAULT_Q,
    od_filter: set[tuple[int, int]] | None = None,
    edge_slot: dict[tuple[int, int], int] | None = None,
    period: int = 1,
) -> dict[str, Any] | None:
    """simulate_alltoall + optional OD filter + periodic edge enable mask."""
    if len(compute) < 2:
        return None
    compute_set = set(compute)
    if od_filter is None:
        od_filter = {(s, d) for s in compute for d in compute if s != d}
    od_filter = {(s, d) for (s, d) in od_filter if s in compute_set and d in compute_set}
    if not od_filter:
        return {"makespan": 0, "ordered_ok": True, "n_ejected": 0, "cycles": 0}

    fifos: list = [[[deque() for _ in range(1)] for _ in range(5)] for _ in range(N)]
    credits = [[[Q] for _ in range(4)] for _ in range(N)]
    arrive: dict[int, list] = defaultdict(list)
    cred_ret: dict[int, list] = defaultdict(list)

    dests: dict[int, list[int]] = {}
    for s in compute:
        ds = [d for d in compute if d != s and (s, d) in od_filter]
        if not ds:
            continue
        rot = s % len(ds)
        dests[s] = ds[rot:] + ds[:rot]
    if not dests:
        return {"makespan": 0, "ordered_ok": True, "n_ejected": 0, "cycles": 0}

    inj_state = {s: {"di": 0, "fi": 0} for s in dests}
    pkt_id = {}
    pid = 0
    for s, ds in dests.items():
        for d in ds:
            pkt_id[(s, d)] = pid
            pid += 1
    n_eject_need = len(od_filter) * m
    n_ejected = 0
    last_eject_t = 0
    ejected: dict = defaultdict(list)
    live = 0
    last_activity = 0
    t = 0

    def neighbor(node: int, d: int) -> int | None:
        x, y = F.coord(node)
        nx, ny = x + DIRS[d][0], y + DIRS[d][1]
        if 0 <= nx < MX and 0 <= ny < MY:
            nb = F.nid(nx, ny)
            if nb in adj.get(node, ()):
                return nb
        return None

    def edge_ok(u: int, v: int, now: int) -> bool:
        if edge_slot is None or period <= 1:
            return True
        sl = edge_slot.get((u, v))
        if sl is None:
            return False
        return (now % period) == sl

    while t <= T_MAX:
        activity = False
        for node, d, vc in cred_ret.pop(t, ()):
            credits[node][d][vc] += 1
            activity = True
        for node, port, fl in arrive.pop(t, ()):
            fifos[node][port][0].append(fl)
            activity = True

        if t >= RAMP:
            for s, ds in dests.items():
                budget = RAMP_BW
                st = inj_state[s]
                while budget > 0 and st["di"] < len(ds):
                    d = ds[st["di"]]
                    path = paths[(s, d)]
                    if len(fifos[s][INJ][0]) >= Q * 4:
                        break
                    fl = Flit(s, d, pkt_id[(s, d)], st["fi"], m, t, path, 0, 0)
                    fifos[s][INJ][0].append(fl)
                    live += 1
                    activity = True
                    budget -= 1
                    st["fi"] += 1
                    if st["fi"] >= m:
                        st["fi"] = 0
                        st["di"] += 1

        for node in list(adj.keys()):
            for d in range(4):
                nb = neighbor(node, d)
                if nb is None:
                    continue
                if not edge_ok(node, nb, t):
                    continue
                best = None
                best_port = -1
                for port in range(5):
                    q = fifos[node][port][0]
                    if not q:
                        continue
                    fl = q[0]
                    if fl.served_out or fl.at_dest:
                        continue
                    if fl.out_dir() != d:
                        continue
                    if credits[node][d][0] <= 0:
                        continue
                    if (best is None
                            or fl.arrival < best.arrival
                            or (fl.arrival == best.arrival
                                and (fl.src, fl.dst, fl.fi)
                                < (best.src, best.dst, best.fi))):
                        best = fl
                        best_port = port
                if best is None:
                    continue
                credits[node][d][0] -= 1
                best.served_out = True
                lat = link_lat(node, nb)
                nxt = Flit(best.src, best.dst, best.pkt, best.fi, best.m,
                           t + lat, best.path, best.hop + 1, 0)
                arrive[t + lat].append((nb, d ^ 1, nxt))
                fifos[node][best_port][0].popleft()
                activity = True
                if best_port != INJ:
                    up = neighbor(node, best_port)
                    if up is not None:
                        cred_ret[t + link_lat(node, up)].append(
                            (up, best_port ^ 1, 0))

            if node in compute_set:
                drained_here = 0
                for port in range(5):
                    if drained_here >= RAMP_BW:
                        break
                    q = fifos[node][port][0]
                    while q and drained_here < RAMP_BW:
                        fl = q[0]
                        if not fl.at_dest or fl.served_out:
                            break
                        q.popleft()
                        live -= 1
                        ejected[(fl.src, fl.dst)].append(fl.fi)
                        n_ejected += 1
                        last_eject_t = t
                        drained_here += 1
                        activity = True
                        if port != INJ:
                            up = neighbor(node, port)
                            if up is not None:
                                cred_ret[t + link_lat(node, up)].append(
                                    (up, port ^ 1, 0))

        if activity:
            last_activity = t
        if (n_ejected >= n_eject_need and not arrive and not any(
                fifos[n][p][0] for n in range(N) for p in range(5))):
            break
        if t - last_activity > STALL_LIMIT:
            return None
        t += 1
    else:
        return None

    ordered_ok = True
    for s, d in od_filter:
        if ejected.get((s, d), []) != list(range(m)):
            ordered_ok = False
            break
    return {
        "makespan": last_eject_t + RAMP,
        "ordered_ok": ordered_ok,
        "n_ejected": n_ejected,
        "cycles": t,
    }


def potential_edge_slots(pg: dict, kind: str = "xy_sum") -> tuple[dict, int]:
    """2-slot schedule: uphill vs downhill w.r.t. a node potential."""
    adj = pg["route_adj"]
    if kind == "xy_sum":
        phi = {n: coord(n)[0] + coord(n)[1] for n in adj}
    elif kind == "xy_diff":
        phi = {n: coord(n)[0] - coord(n)[1] for n in adj}
    elif kind == "bfs":
        root = max(adj.keys(), key=lambda n: (len(adj[n]), -n))
        phi = _updown_labels(adj, root) or {}
    else:
        raise ValueError(kind)
    slots = {}
    for u, vs in adj.items():
        for v in vs:
            if phi.get(v, 0) > phi.get(u, 0):
                slots[(u, v)] = 0
            elif phi.get(v, 0) < phi.get(u, 0):
                slots[(u, v)] = 1
            else:
                slots[(u, v)] = 0  # lateral → slot 0
    return slots, 2


def phase_tdm_makespan(pg: dict, tabs: list[dict], m: int, Q: int) -> dict | None:
    """Assign each OD to the shortest among tabs; run phases sequentially."""
    compute = pg["compute_nodes"]
    adj = pg["route_adj"]
    buckets: list[set[tuple[int, int]]] = [set() for _ in tabs]
    # need full path table for DES (union); each phase filters ODs
    union_paths: dict[tuple[int, int], list[int]] = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            best_i, best_p = None, None
            for i, t in enumerate(tabs):
                p = t["paths"].get((s, d))
                if p is None:
                    continue
                if best_p is None or len(p) < len(best_p):
                    best_p, best_i = p, i
            if best_p is None:
                return None
            buckets[best_i].add((s, d))
            union_paths[(s, d)] = best_p
    total = 0
    phase_mks = []
    for ods in buckets:
        if not ods:
            phase_mks.append(0)
            continue
        # paths for this phase only need those ODs, but table may be partial
        paths = {od: union_paths[od] for od in ods}
        sim = simulate_alltoall_ex(paths, compute, adj, m=m, Q=Q, od_filter=ods)
        if sim is None:
            return None
        phase_mks.append(sim["makespan"])
        total += sim["makespan"]
    return {"makespan": total, "phase_mks": phase_mks,
            "phase_ods": [len(b) for b in buckets]}


def run(quick_m0: list[int] | None = None, max_scenes: int | None = None) -> dict:
    faults = json.loads((ROOT / "results/pg_faults_budget_8x6.json").read_text())
    e2e = json.loads((ROOT / "results/pg_e2e_pareto.json").read_text())
    scens = sorted({r["scenario"] for r in e2e["rows"]})
    if max_scenes:
        scens = scens[:max_scenes]
    by = {s["name"]: s for s in faults["scenarios"]}
    freq = e2e["meta"]["freq_ghz"]
    m0_list = quick_m0 or [1, 13]
    Q = 19

    # sanity: extended DES matches stock
    pg0 = expand_pg(by[scens[0]], "dead")
    s0 = solve_scheme(pg0, "updown")
    a = simulate_alltoall(s0["paths"], s0["compute_nodes"], s0["route_adj"],
                          m=1, Q=Q, num_vc=1)
    b = simulate_alltoall_ex(s0["paths"], s0["compute_nodes"], s0["route_adj"],
                             m=1, Q=Q)
    assert a and b and a["makespan"] == b["makespan"], (a, b)

    rows = []
    t0 = time.time()
    for i, name in enumerate(scens):
        pg = expand_pg(by[name], "dead")
        m3 = solve_scheme(pg, "updown")
        assert m3["feasible"]
        schemes: dict[str, Any] = {
            "m3": m3,
            "m3_prime": with_m3_sac(pg, build_m3_prime),
            "multi_root_pack": with_m3_sac(
                pg, lambda p: build_multi_root_pack(p, K=8, modes=("ud", "du"))),
        }
        sp = with_m3_sac(pg, build_sp_table)

        # precompute top roots for phase TDM
        tabs_ud = enum_ud_tables(
            apply_sacrifice(pg, set(m3["sacrificed"]), True)
            if m3["n_sacrificed"] else pg,
            ("ud",))
        top2 = tabs_ud[:2]
        top4 = tabs_ud[:4]

        for m0 in m0_list:
            row: dict[str, Any] = {"scenario": name, "m0": m0}
            A0 = m3["n_compute_used"]
            for tag, sol in schemes.items():
                if sol is None or not sol.get("feasible"):
                    row[tag] = None
                    continue
                A = sol["n_compute_used"]
                m_eff = math.ceil(m0 * (48 / A) ** 2)
                sim = simulate_alltoall(
                    sol["paths"], sol["compute_nodes"], sol["route_adj"],
                    m=m_eff, Q=Q, num_vc=1)
                if sim is None:
                    row[tag] = None
                    continue
                row[tag] = e2e_ns(A, m0, sim["makespan"], freq)
                row[f"{tag}_mk"] = sim["makespan"]
                row[f"{tag}_A"] = A
                row[f"{tag}_load"] = max_link_load(sol["paths"])

            # phase TDM (2 and 4 roots)
            pg_r = apply_sacrifice(pg, set(m3["sacrificed"]), True) if m3["n_sacrificed"] else pg
            A = len(pg_r["compute_nodes"])
            m_eff = math.ceil(m0 * (48 / A) ** 2)
            for label, tabs in (("tdm_r2", top2), ("tdm_r4", top4)):
                if len(tabs) < 1:
                    row[label] = None
                    continue
                ph = phase_tdm_makespan(pg_r, tabs, m_eff, Q)
                if ph is None:
                    row[label] = None
                else:
                    row[label] = e2e_ns(A, m0, ph["makespan"], freq)
                    row[f"{label}_mk"] = ph["makespan"]
                    row[f"{label}_phases"] = ph["phase_mks"]

            # link-slot TDM + shortest paths (same A as M3)
            if sp is not None and sp.get("feasible"):
                A = sp["n_compute_used"]
                m_eff = math.ceil(m0 * (48 / A) ** 2)
                for kind in ("xy_sum", "bfs"):
                    slots, period = potential_edge_slots(
                        {"route_adj": sp["route_adj"]}, kind)
                    tag = f"linktdm_{kind}"
                    sim = simulate_alltoall_ex(
                        sp["paths"], sp["compute_nodes"], sp["route_adj"],
                        m=m_eff, Q=Q, edge_slot=slots, period=period)
                    if sim is None:
                        row[tag] = None
                    else:
                        row[tag] = e2e_ns(A, m0, sim["makespan"], freq)
                        row[f"{tag}_mk"] = sim["makespan"]
            else:
                row["linktdm_xy_sum"] = None
                row["linktdm_bfs"] = None

            rows.append(row)

        if (i + 1) % 8 == 0:
            print(f"  {i+1}/{len(scens)} {time.time()-t0:.0f}s", flush=True)

    summary = {}
    tags = ["m3", "m3_prime", "multi_root_pack", "tdm_r2", "tdm_r4",
            "linktdm_xy_sum", "linktdm_bfs"]
    for m0 in m0_list:
        rs = [r for r in rows if r["m0"] == m0]
        summary[str(m0)] = {}
        for tag in tags:
            vals = [r[tag] for r in rs if r.get(tag) is not None]
            if not vals:
                summary[str(m0)][tag] = {"cover": 0}
                continue
            m3v = [r["m3"] for r in rs if r.get(tag) is not None and r.get("m3") is not None]
            paired = [(r["m3"], r[tag]) for r in rs
                      if r.get(tag) is not None and r.get("m3") is not None]
            summary[str(m0)][tag] = {
                "cover": len(vals),
                "med": st.median(vals),
                "worst": max(vals),
                "better": sum(1 for a, b in paired if b < a - 1e-9),
                "worse": sum(1 for a, b in paired if b > a + 1e-9),
                "avg_delta": st.mean(b - a for a, b in paired) if paired else None,
            }
            print(f"m0={m0} {tag:16} cover={len(vals)}/{len(rs)} "
                  f"med={st.median(vals):.1f} worst={max(vals):.1f} "
                  f"Δavg={summary[str(m0)][tag]['avg_delta']:+.1f} "
                  f"better/worse="
                  f"{summary[str(m0)][tag]['better']}/"
                  f"{summary[str(m0)][tag]['worse']}", flush=True)

    out = {
        "meta": {
            "n_scenes": len(scens), "m0_list": m0_list, "Q": Q,
            "note": "1VC explore: multi-root pack, phase TDM, link-slot TDM",
        },
        "summary": summary,
        "rows": rows,
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT} in {time.time()-t0:.0f}s", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--m0", type=int, nargs="+", default=[1, 13])
    ap.add_argument("--max-scenes", type=int, default=None)
    args = ap.parse_args()
    run(quick_m0=args.m0, max_scenes=args.max_scenes)


if __name__ == "__main__":
    main()
