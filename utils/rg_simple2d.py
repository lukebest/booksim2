#!/usr/bin/env python3
"""Resource-bounded mesh scheduler: one LDPS round, one fault, no interval table.

Why this exists
---------------
`islip2d_mesh` spends almost all of its area on the interval occupancy table
(~100 kbit) and almost all of its arbitration time on two-level grant/accept
plus a path-wide AND. Under a tight CA budget that combination is the thing
that does not fit.

This module keeps the only invariant the data plane actually needs -- a round
is a link-disjoint path set -- and throws away everything else:

  * no interval hole-filling (one link-used bitmap, 164 bits)
  * no per-link grant pointers (one rotating source pointer)
  * no iSLIP iterations (one greedy pass per round)
  * path is combinational from (src, dst, failed)

The fault model is "at most one router is gone". Flows to or from it vanish.
Every other pair still has a path: XY if it misses the hole, YX if XY hits it,
and a 2-hop U-detour when src and dst share a row or column and the hole sits
between them (the only case where XY and YX are the same line).

The three sites the study has to cover -- corner, edge, interior -- are the
three degree classes of an 8x6 mesh (2 / 3 / 4). One representative of each
is enough to expose the load shift; the route rule itself is the same function.

Hardware story the cost model prices
------------------------------------
Each source offers one dest from its residual RR. The CA therefore sees 48
candidates, not 2256 VOQs: a 48-wide eligible mask plus a 14-hop path AND.
State that survives a round is the VOQ bitmap, two 6-bit pointers and a
7-bit fault register. Folding the 48-wide pass into 8-wide row waves is an
optional area/time trade, not part of the algorithm.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from rg_topo import H_BASE, MX, MY, N, RAMP_BW, V_BASE, Topology, coord, nid

Pair = tuple[int, int]
Edge = tuple[int, int]

# One of each degree class. Corner = (0,0), top-edge = (3,0), interior = (3,2).
SITES: dict[str, int] = {
    "healthy": -1,
    "corner": nid(0, 0),
    "edge": nid(3, 0),
    "center": nid(3, 2),
}

# Second representative of each degree class, so the route rule is checked
# against more than the three sites the tables quote.
EXTRA_SITES: dict[str, int] = {
    "corner2": nid(7, 5),     # opposite corner
    "edge2": nid(7, 2),       # right edge
    "center2": nid(4, 3),     # the other interior cell
}


def site_kind(node: int) -> str:
    x, y = coord(node)
    xe, ye = x in (0, MX - 1), y in (0, MY - 1)
    if xe and ye:
        return "corner"
    if xe or ye:
        return "edge"
    return "center"


def xy_path(src: int, dst: int) -> list[int]:
    sx, sy = coord(src)
    dx, dy = coord(dst)
    path = [src]
    x, y = sx, sy
    step = 1 if dx > sx else -1
    while x != dx:
        x += step
        path.append(nid(x, y))
    step = 1 if dy > sy else -1
    while y != dy:
        y += step
        path.append(nid(x, y))
    return path


def yx_path(src: int, dst: int) -> list[int]:
    sx, sy = coord(src)
    dx, dy = coord(dst)
    path = [src]
    x, y = sx, sy
    step = 1 if dy > sy else -1
    while y != dy:
        y += step
        path.append(nid(x, y))
    step = 1 if dx > sx else -1
    while x != dx:
        x += step
        path.append(nid(x, y))
    return path


def _hits(path: list[int], failed: int) -> bool:
    return failed in path


def _parallel_row(y: int) -> int:
    return y + 1 if y + 1 < MY else y - 1


def _parallel_col(x: int) -> int:
    return x + 1 if x + 1 < MX else x - 1


def detour_path(src: int, dst: int, failed: int) -> list[int]:
    """U-detour when src, dst and the hole are colinear.

    Same row: step to the neighbouring row, walk past the hole, step back.
    Same column: the symmetric move. The neighbour always exists -- a mesh
    line has at least one side -- and the hole is on the original line, so
    the three-segment path cannot contain it.
    """
    sx, sy = coord(src)
    dx, dy = coord(dst)
    fx, fy = coord(failed)
    if sy == dy == fy:
        ny = _parallel_row(sy)
        return xy_path(src, nid(sx, ny))[0:] + xy_path(nid(sx, ny), nid(dx, ny))[1:] + xy_path(nid(dx, ny), dst)[1:]
    if sx == dx == fx:
        nx = _parallel_col(sx)
        return xy_path(src, nid(nx, sy))[0:] + xy_path(nid(nx, sy), nid(nx, dy))[1:] + xy_path(nid(nx, dy), dst)[1:]
    raise ValueError(f"detour requested for non-colinear {src}->{dst} hole={failed}")


def route(src: int, dst: int, failed: int = -1) -> list[int] | None:
    """Static path for (src, dst) given at most one hole. None if either is dead."""
    if src == dst:
        return [src]
    if failed >= 0 and (src == failed or dst == failed):
        return None
    xy = xy_path(src, dst)
    if failed < 0 or not _hits(xy, failed):
        return xy
    yx = yx_path(src, dst)
    if not _hits(yx, failed):
        return yx
    det = detour_path(src, dst, failed)
    if _hits(det, failed):
        raise AssertionError(f"detour still hits hole: {src}->{dst} F={failed} {det}")
    return det


def route_kind(src: int, dst: int, failed: int) -> str:
    if failed < 0:
        return "xy"
    if src == failed or dst == failed:
        return "dead"
    xy = xy_path(src, dst)
    if not _hits(xy, failed):
        return "xy"
    yx = yx_path(src, dst)
    if not _hits(yx, failed):
        return "yx"
    return "detour"


def wire_delay(path: list[int]) -> int:
    delay = 0
    for a, b in zip(path, path[1:]):
        ax, ay = coord(a)
        bx, by = coord(b)
        delay += H_BASE if ay == by else V_BASE
    return delay


def links_of(path: list[int]) -> list[Edge]:
    return list(zip(path, path[1:]))


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def alltoall_pairs(failed: int = -1) -> list[Pair]:
    alive = [n for n in range(N) if n != failed]
    return [(s, d) for s in alive for d in alive if s != d]


def schedule_simple2d(pairs: list[Pair] | None = None, *,
                      failed: int = -1,
                      grants_per_src: int = 1,
                      ramp_cap: int = RAMP_BW) -> dict[str, Any]:
    """Greedy LDPS rounds. One dest per source per pass; sources rotate."""
    pairs = list(pairs if pairs is not None else alltoall_pairs(failed))
    residual: dict[int, list[int]] = defaultdict(list)
    path_of: dict[Pair, list[int]] = {}
    kinds: dict[str, int] = defaultdict(int)
    for s, d in pairs:
        p = route(s, d, failed)
        if p is None:
            kinds["dead"] += 1
            continue
        residual[s].append(d)
        path_of[(s, d)] = p
        kinds[route_kind(s, d, failed)] += 1

    load: dict[Edge, int] = defaultdict(int)
    hops = []
    for p in path_of.values():
        hops.append(len(p) - 1)
        for e in links_of(p):
            load[e] += 1
    cut = max(load.values()) if load else 0

    src_ptr = 0
    dst_ptr: dict[int, int] = defaultdict(int)
    rounds: list[list[Pair]] = []
    n_grant_ops = 0

    while any(residual.values()):
        used: set[Edge] = set()
        inj = [0] * N
        ejt = [0] * N
        granted: list[Pair] = []
        order = [(src_ptr + i) % N for i in range(N)]
        for s in order:
            if not residual[s] or inj[s] >= grants_per_src:
                continue
            dests = residual[s]
            start = dst_ptr[s] % len(dests)
            pick = None
            for k in range(len(dests)):
                d = dests[(start + k) % len(dests)]
                n_grant_ops += 1
                if ejt[d] >= ramp_cap:
                    continue
                edges = links_of(path_of[(s, d)])
                if any(e in used for e in edges):
                    continue
                pick = d
                break
            if pick is None:
                continue
            for e in links_of(path_of[(s, pick)]):
                used.add(e)
            inj[s] += 1
            ejt[pick] += 1
            dests.remove(pick)
            dst_ptr[s] = 0
            granted.append((s, pick))
        if not granted:
            raise RuntimeError("simple2d stalled: residual remains but no LDPS")
        src_ptr = (src_ptr + 1) % N
        rounds.append(granted)

    max_wire = []
    for rnd in rounds:
        max_wire.append(max(wire_delay(path_of[g]) for g in rnd))
    return {
        "algo": "simple2d",
        "failed": failed,
        "site": "healthy" if failed < 0 else site_kind(failed),
        "n_pairs": len(path_of),
        "n_rounds": len(rounds),
        "cut_bound": cut,
        "over_cut": round(len(rounds) / cut, 3) if cut else 0.0,
        "route_mix": dict(kinds),
        "mean_hops": round(sum(hops) / len(hops), 3) if hops else 0.0,
        "max_hops": max(hops) if hops else 0,
        "mean_round_wire": round(sum(max_wire) / len(max_wire), 1) if max_wire else 0.0,
        "data_span": int(sum(max_wire)),
        "grants_per_src": grants_per_src,
        "n_grant_ops": n_grant_ops,
        "max_link_load": cut,
        "hottest_links": sorted(load.items(), key=lambda kv: -kv[1])[:4],
        "rounds": rounds,
        "path_of": path_of,
    }


def verify_round(rounds: list[list[Pair]], path_of: dict[Pair, list[int]],
                 failed: int = -1) -> dict[str, Any]:
    """Conflict-freedom + hole avoidance. Returns counts, raises on a miss."""
    n_conflict = 0
    n_hole = 0
    seen: set[Pair] = set()
    for rnd in rounds:
        used: dict[Edge, Pair] = {}
        srcs: list[int] = []
        for s, d in rnd:
            if (s, d) in seen:
                raise AssertionError(f"duplicate grant {(s, d)}")
            seen.add((s, d))
            srcs.append(s)
            p = path_of[(s, d)]
            if failed >= 0 and failed in p:
                n_hole += 1
            for e in links_of(p):
                if e in used:
                    n_conflict += 1
                used[e] = (s, d)
        if len(srcs) != len(set(srcs)):
            raise AssertionError("two grants from one source in a round")
    missing = set(path_of) - seen
    return {
        "n_granted": len(seen),
        "n_missing": len(missing),
        "n_link_conflicts": n_conflict,
        "n_paths_through_hole": n_hole,
        "ok": not missing and n_conflict == 0 and n_hole == 0,
    }


def reachability(failed: int) -> dict[str, Any]:
    """Every surviving pair must have a hole-free path."""
    dead = 0
    ok = 0
    mix: dict[str, int] = defaultdict(int)
    for s in range(N):
        for d in range(N):
            if s == d:
                continue
            k = route_kind(s, d, failed)
            mix[k] += 1
            p = route(s, d, failed)
            if p is None:
                dead += 1
                continue
            if failed in p:
                raise AssertionError(f"path hits hole {s}->{d} F={failed} {p}")
            for a, b in zip(p, p[1:]):
                ax, ay = coord(a)
                bx, by = coord(b)
                if abs(ax - bx) + abs(ay - by) != 1:
                    raise AssertionError(f"non-adjacent hop {a}->{b} on {p}")
            ok += 1
    return {"failed": failed, "alive_pairs": ok, "dead_pairs": dead,
            "mix": dict(mix), "expected_alive": (N - 1) * (N - 2),
            "expected_dead": 2 * (N - 1)}


def compare_to_islip2d() -> dict[str, Any]:
    """Same all-to-all, same slot discipline: simple2d vs islip2d iters=0/1."""
    from rg_collectives import build_alltoall
    from rg_mesh_sched import schedule_mesh

    topo = Topology("mesh")
    col = build_alltoall(topo)
    out = {"simple2d": {k: v for k, v in schedule_simple2d().items()
                        if k not in ("rounds", "path_of", "hottest_links")}}
    for iters in (0, 1):
        r = schedule_mesh(topo, col, "islip2d_mesh", grants_per_src=1,
                          conflict_domain="free_at", iters=iters)
        out[f"islip2d_I{iters}"] = {
            "n_rounds": r["n_rounds"],
            "round_lb": r["round_lb"],
            "round_ratio": r["round_ratio"],
        }
    return out


def site_row(name: str, failed: int) -> dict[str, Any]:
    rch = reachability(failed) if failed >= 0 else {
        "alive_pairs": N * (N - 1), "dead_pairs": 0,
        "mix": {"xy": N * (N - 1)}, "expected_alive": N * (N - 1),
        "expected_dead": 0,
    }
    sch = schedule_simple2d(failed=failed)
    v = verify_round(sch["rounds"], sch["path_of"], failed=failed)
    if not v["ok"]:
        raise AssertionError((name, v))
    if failed >= 0:
        if rch["alive_pairs"] != rch["expected_alive"]:
            raise AssertionError((name, rch))
        if rch["dead_pairs"] != rch["expected_dead"]:
            raise AssertionError((name, rch))
    return {
        "site": name,
        "failed": failed,
        "degree": 0 if failed < 0 else len(Topology("mesh").adj[failed]),
        "alive": rch["alive_pairs"],
        "dead": rch["dead_pairs"],
        "route_mix": sch["route_mix"],
        "cut_bound": sch["cut_bound"],
        "n_rounds": sch["n_rounds"],
        "over_cut": sch["over_cut"],
        "mean_hops": sch["mean_hops"],
        "max_hops": sch["max_hops"],
        "mean_round_wire": sch["mean_round_wire"],
        "data_span": sch["data_span"],
        "verify": {k: v[k] for k in ("n_granted", "n_missing",
                                     "n_link_conflicts",
                                     "n_paths_through_hole", "ok")},
    }


def extra_sites() -> list[dict[str, Any]]:
    """Same rule, three different holes. Must stay reachable and conflict-free."""
    return [site_row(name, f) for name, f in EXTRA_SITES.items()]


def sweep() -> dict[str, Any]:
    rows = [site_row(name, f) for name, f in SITES.items()]
    extra = extra_sites()
    return {"rows": rows, "extra_sites": extra,
            "vs_islip2d": compare_to_islip2d()}


# ---------------------------------------------------------------------------
# Cost (kept next to the algorithm so the prices cannot drift from the design)
# ---------------------------------------------------------------------------

def cost_bits(*, n_nodes: int = N, n_links: int = 164) -> dict[str, int]:
    """State the CA has to keep. No interval table, no per-link pointers."""
    w_id = 6
    return {
        "residual_voq_bitmap": n_nodes * (n_nodes - 1),
        "round_link_bitmap": n_links,
        "source_pointer": w_id,
        "accept_pointers": n_nodes * w_id,
        "fault_register": w_id + 1,
    }


if __name__ == "__main__":
    print("=== reachability: every surviving pair has a hole-free path ===")
    for name, f in SITES.items():
        if f < 0:
            continue
        r = reachability(f)
        assert r["alive_pairs"] == r["expected_alive"], r
        assert r["dead_pairs"] == r["expected_dead"], r
        print(f"  {name:7} F={f:2} deg={len(Topology('mesh').adj[f])}  "
              f"alive={r['alive_pairs']} dead={r['dead_pairs']}  mix={r['mix']}")

    print("\n=== simple2d all-to-all (grants_per_src=1) ===")
    data = sweep()
    hdr = (f"{'site':<8} {'F':>3} {'alive':>6} {'cut':>5} {'rounds':>7} "
           f"{'×cut':>6} {'hops':>6} {'wire':>7} {'span':>7}  mix")
    print(hdr)
    for row in data["rows"]:
        print(f"{row['site']:<8} {row['failed']:>3} {row['alive']:>6} "
              f"{row['cut_bound']:>5} {row['n_rounds']:>7} "
              f"{row['over_cut']:>6.2f} {row['mean_hops']:>6.2f} "
              f"{row['mean_round_wire']:>7.1f} {row['data_span']:>7}  "
              f"{row['route_mix']}")

    print("\n=== extra fault sites (same rule, other holes) ===")
    print(hdr)
    for row in data["extra_sites"]:
        print(f"{row['site']:<8} {row['failed']:>3} {row['alive']:>6} "
              f"{row['cut_bound']:>5} {row['n_rounds']:>7} "
              f"{row['over_cut']:>6.2f} {row['mean_hops']:>6.2f} "
              f"{row['mean_round_wire']:>7.1f} {row['data_span']:>7}  "
              f"{row['route_mix']}")

    print("\n=== same slot discipline vs islip2d_mesh ===")
    for k, v in data["vs_islip2d"].items():
        print(f"  {k:16} {v}")

    bits = cost_bits()
    print(f"\n=== CA state  {sum(bits.values())} bits ===")
    for k, v in bits.items():
        print(f"  {k:24} {v:6}")
    print("  [ok] simple2d schedules the residual all-to-all under one hole")
