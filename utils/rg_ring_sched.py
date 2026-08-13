#!/usr/bin/env python3
"""islip2d_ring: centralized boarding/leaving arbitration under D-R.

The arbitration unit here is the RING, not the segment: 28 pointers (14 rings
x 2 directions) instead of 192, which is also what a ring station can
physically arbitrate. A path touches exactly two ring-directions, so the
unanimity test is a 2-wide AND instead of the mesh's up-to-12-wide one.

That narrow AND does NOT translate into more unanimity, and it is worth being
explicit because the intuition points the wrong way. Measured on all-to-all,
the distributed phase admits 16% of flows on the ring versus 28% on the mesh.
The binding quantity is the NUMBER of grant units, not the width of the AND: a
ring-direction grants one source per iteration, so at most 28 flows per
iteration can be unanimous, while the mesh's 164 link arbiters can nominate far
more. Coarsening the arbiter to make it physically implementable is what costs
the unanimity, and on both fabrics the distributed phase ends up costing rounds
against a plain ordered fill (mesh 110 vs 97, ring 69 vs 66).

Grant  = each ring-direction picks one requesting SOURCE (round robin).
Accept = each source picks up to `grants_per_src` of the VOQs that got both of
         their ring-directions, in a_s order over destinations.
Fill   = arc-length descending. On a ring the per-ring conflict graph is a
         circular-arc graph, where first-fit in decreasing-length order is the
         classic good order -- unlike the mesh, where L-shaped intersection
         graphs give greedy no structure to exploit.

Clause R4 needs no scheduling freedom at all: `RingFootprint` pins phase 2 at
exactly t_turn after phase 1 ends, so a granted transfer either fits with zero
residency at the turn point or is not granted. That is precisely what a
DISTRIBUTED ring station cannot do -- it would have to know the far ring's
future occupancy -- and it is why centralizing this arbitration is a
feasibility argument, not a performance argument.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from rg_topo import RAMP_BW
from rg_batch_sched import CapMap
from rg_ring_topo import (
    Pair, RingFootprint, RingPath, RingPlan, RingTopology, board_key,
    build_ring_plan, leave_key, verify_dr,
)

FILL_ORDERS: tuple[str, ...] = ("arc_desc", "arc_asc", "pressure", "random",
                                "flowid")


# ---------------------------------------------------------------------------
# 1. Resource requirement list -- one place that encodes all of D-R
# ---------------------------------------------------------------------------

def requirements(topo: RingTopology, fp: RingFootprint
                 ) -> list[tuple[Any, int, int, int]]:
    """(resource_key, offset_from_t0, duration, capacity) for one grant.

    R1 picks the mutual-exclusion granularity (segment vs whole ring),
    R2/R3 are the board/leave ports, and the ramps + per-VOQ entry carry the
    M2-equivalent and R5-serialization parts.
    """
    ramp_cap = max(1, RAMP_BW * topo.sigma)
    out: list[tuple[Any, int, int, int]] = []
    if topo.spatial_reuse == "arc":
        for e, pref in fp.links:
            out.append((("L", e), pref, fp.dur, 1))
    else:
        for k, off, d in fp.rings:
            out.append((("R", k), off, d, 1))
    for k, off in fp.boards:
        out.append((k, off, fp.dur, topo.board_ports))
    for k, off in fp.leaves:
        out.append((k, off, fp.dur, topo.leave_ports))
    out.append((("inj", fp.src), 0, fp.dur, ramp_cap))
    out.append((("ej", fp.dst), fp.wire, fp.dur, ramp_cap))
    out.append((("voq", fp.src, fp.dst), 0, fp.dur, 1))
    return out


class _FreeAtSet:
    """One `free_at` register per (resource, slot); capacity varies per key."""

    def __init__(self) -> None:
        self.t: dict[Any, list[int]] = {}

    def ready(self, key: Any, cap: int, slot: int) -> int:
        v = self.t.get(key)
        return 0 if v is None else v[min(slot, cap - 1)]

    def take(self, key: Any, cap: int, slot: int, until: int) -> None:
        v = self.t.setdefault(key, [0] * cap)
        v[min(slot, cap - 1)] = until
        v.sort()


class _IvSet:
    """Full interval tables, one CapMap per distinct capacity."""

    def __init__(self) -> None:
        self.by_cap: dict[int, CapMap] = {}

    def _m(self, cap: int) -> CapMap:
        if cap not in self.by_cap:
            self.by_cap[cap] = CapMap(cap)
        return self.by_cap[cap]

    def earliest(self, key: Any, cap: int, dur: int, t_min: int) -> int:
        return self._m(cap).earliest(key, dur, t_min)

    def reserve(self, key: Any, cap: int, s: int, e: int) -> None:
        self._m(cap).reserve(key, s, e)


# ---------------------------------------------------------------------------
# 2. Round construction: R1 + R2 + R3 enforced while accumulating
# ---------------------------------------------------------------------------

class _RingRound:
    """Accumulates one simultaneously-releasable set under D-R."""

    def __init__(self, topo: RingTopology, src_cap: int):
        self.topo = topo
        self.ramp_cap = max(1, RAMP_BW * topo.sigma)
        self.src_cap = max(1, min(src_cap, self.ramp_cap))
        self.links: set[Any] = set()
        self.rings: set[Any] = set()
        self.board: dict[Any, int] = defaultdict(int)
        self.leave: dict[Any, int] = defaultdict(int)
        self.inj: dict[int, int] = defaultdict(int)
        self.ej: dict[int, int] = defaultdict(int)
        self.voq: set[Pair] = set()
        self.acc: list[RingFootprint] = []

    def feasible(self, fp: RingFootprint) -> bool:
        if self.topo.spatial_reuse == "arc":
            for e, _ in fp.links:
                if e in self.links:
                    return False
        else:
            for k, _, _ in fp.rings:
                if k in self.rings:
                    return False
        for k, _ in fp.boards:
            if self.board[k] >= self.topo.board_ports:
                return False
        for k, _ in fp.leaves:
            if self.leave[k] >= self.topo.leave_ports:
                return False
        if self.inj[fp.src] >= self.src_cap:
            return False
        if self.ej[fp.dst] >= self.ramp_cap:
            return False
        return (fp.src, fp.dst) not in self.voq

    def add(self, fp: RingFootprint) -> None:
        for e, _ in fp.links:
            self.links.add(e)
        for k, _, _ in fp.rings:
            self.rings.add(k)
        for k, _ in fp.boards:
            self.board[k] += 1
        for k, _ in fp.leaves:
            self.leave[k] += 1
        self.inj[fp.src] += 1
        self.ej[fp.dst] += 1
        self.voq.add((fp.src, fp.dst))
        self.acc.append(fp)


@dataclass
class RingSlotState:
    rng: random.Random = field(default_factory=random.Random)
    gptr: dict[Any, int] = field(default_factory=dict)   # ring-dir -> src ptr
    aptr: dict[int, int] = field(default_factory=dict)   # src -> dst ptr
    n_grant_ops: int = 0
    n_iter_ops: int = 0


def _fill_key(fill: str, st: RingSlotState):
    if fill == "arc_desc":
        return lambda fp: (-fp.hops, fp.flow_id)
    if fill == "arc_asc":
        return lambda fp: (fp.hops, fp.flow_id)
    if fill == "pressure":
        return lambda fp: (-fp.pressure, -fp.hops, fp.flow_id)
    if fill == "random":
        return lambda fp: (st.rng.random(),)
    if fill == "flowid":
        return lambda fp: (fp.flow_id,)
    raise ValueError(f"unknown fill order: {fill}")


def _ring_round(topo: RingTopology, cands: list[RingFootprint],
                st: RingSlotState, *, iters: int, src_cap: int, fill: str
                ) -> tuple[list[RingFootprint], int]:
    n = topo.n
    rb = _RingRound(topo, src_cap)
    n_unan = 0

    for it in range(max(0, iters)):
        elig = [fp for fp in cands if rb.feasible(fp)]
        if not elig:
            break
        st.n_iter_ops += 1
        req: dict[Any, set[int]] = defaultdict(set)
        for fp in elig:
            for a in fp.path.arcs:
                req[a.key()].add(fp.src)
        granted: dict[Any, int] = {}
        for rk, srcs in req.items():
            st.n_grant_ops += len(srcs)
            p = st.gptr.get(rk, 0)
            granted[rk] = min(srcs, key=lambda s: ((s - p) % n, s))
        by_src: dict[int, list[RingFootprint]] = defaultdict(list)
        for fp in elig:
            if all(granted.get(a.key()) == fp.src for a in fp.path.arcs):
                by_src[fp.src].append(fp)
        took = 0
        for s, lst in by_src.items():
            a_ptr = st.aptr.get(s, 0)
            lst.sort(key=lambda fp: (((fp.dst - a_ptr) % n), fp.flow_id))
            for fp in lst:
                if not rb.feasible(fp):
                    continue
                rb.add(fp)
                n_unan += 1
                took += 1
                if it == 0:
                    st.aptr[s] = (fp.dst + 1) % n
                    for arc in fp.path.arcs:
                        st.gptr[arc.key()] = (s + 1) % n
        if took == 0:
            break

    done = {fp.flow_id for fp in rb.acc}
    rest = [fp for fp in cands if fp.flow_id not in done]
    st.n_grant_ops += len(rest)
    for fp in sorted(rest, key=_fill_key(fill, st)):
        if rb.feasible(fp):
            rb.add(fp)
    return rb.acc, n_unan


# ---------------------------------------------------------------------------
# 3. Flow construction
# ---------------------------------------------------------------------------

def build_ring_flows(topo: RingTopology, plan: RingPlan,
                     pairs: Sequence[Pair], m: int = 1,
                     release: int = 0) -> list[RingFootprint]:
    fps = [topo.footprint(i, plan.paths[k], m, release)
           for i, k in enumerate(pairs)]
    load = topo.link_load(plan.paths[k] for k in pairs)
    for fp in fps:
        fp.pressure = sum(load[e] for e, _ in fp.links)
    return fps


# ---------------------------------------------------------------------------
# 4. The schedule loop (same synchronous slotted discipline as the mesh)
# ---------------------------------------------------------------------------

def islip2d_ring_schedule(topo: RingTopology, fps: list[RingFootprint], *,
                          grants_per_src: int = 1,
                          conflict_domain: str = "free_at",
                          iters: int = 1, fill: str = "arc_desc",
                          t_rtt: int = 16, pipeline_depth: int = 1,
                          seed: int = 0) -> dict[str, Any]:
    if conflict_domain not in ("free_at", "interval"):
        raise ValueError(conflict_domain)
    st = RingSlotState(rng=random.Random(seed))
    free = _FreeAtSet()
    ivs = _IvSet()
    reqs = {fp.flow_id: requirements(topo, fp) for fp in fps}

    residual = list(fps)
    starts: dict[int, int] = {}
    round_of: dict[int, int] = {}
    rounds: list[dict[str, Any]] = []
    round_start: list[int] = []
    makespan = 0
    convoy = 0
    n_unan_total = 0
    ctrl_msgs = 0
    bitmap_ok = True
    req_per_round: list[int] = []

    P = max(1, pipeline_depth)
    guard = 0
    while residual:
        guard += 1
        if guard > 40000:
            raise RuntimeError("islip2d_ring_schedule did not converge")
        r = len(rounds)
        before = {fp.flow_id for fp in residual}
        n_src = len({fp.src for fp in residual})
        req_per_round.append(n_src)
        ctrl_msgs += 2 * n_src

        acc, n_unan = _ring_round(topo, residual, st, iters=iters,
                                  src_cap=grants_per_src, fill=fill)
        if not acc:
            raise RuntimeError("empty round with VOQs still pending")
        n_unan_total += n_unan
        ctrl_floor = t_rtt if r < P else round_start[r - P] + t_rtt

        if conflict_domain == "free_at":
            slot: dict[Any, int] = defaultdict(int)
            t0 = ctrl_floor
            for fp in acc:
                for key, off, dur, cap in reqs[fp.flow_id]:
                    k = slot[key]
                    t0 = max(t0, free.ready(key, cap, k) - off)
                    slot[key] = k + 1
            slot.clear()
            for fp in acc:
                starts[fp.flow_id] = t0
                for key, off, dur, cap in reqs[fp.flow_id]:
                    k = slot[key]
                    free.take(key, cap, k, t0 + off + dur)
                    slot[key] = k + 1
            t_round = t0
            dur_round = max(fp.tail for fp in acc)
        else:
            t_first = None
            for fp in sorted(acc, key=_fill_key(fill, st)):
                t0 = ctrl_floor
                for _ in range(2000):
                    cand = t0
                    for key, off, dur, cap in reqs[fp.flow_id]:
                        got = ivs.earliest(key, cap, dur, t0 + off)
                        if got > t0 + off:
                            cand = max(cand, got - off)
                    if cand <= t0:
                        break
                    t0 = cand
                starts[fp.flow_id] = t0
                for key, off, dur, cap in reqs[fp.flow_id]:
                    ivs.reserve(key, cap, t0 + off, t0 + off + dur)
                t_first = t0 if t_first is None else min(t_first, t0)
            t_round = t_first if t_first is not None else ctrl_floor
            dur_round = max(starts[fp.flow_id] + fp.tail
                            for fp in acc) - t_round

        for fp in acc:
            round_of[fp.flow_id] = r
            makespan = max(makespan, starts[fp.flow_id] + fp.eject)
        round_start.append(t_round)
        rounds.append({"round": r, "start": t_round, "dur": dur_round,
                       "n_flows": len(acc), "n_unanimous": n_unan,
                       "ctrl_floor": ctrl_floor, "n_requesting_srcs": n_src})
        convoy += dur_round
        acc_ids = {fp.flow_id for fp in acc}
        residual = [fp for fp in residual if fp.flow_id not in acc_ids]
        if {fp.flow_id for fp in residual} != before - acc_ids:
            bitmap_ok = False

    n_rounds = len(rounds)
    t_first_start = min(starts.values()) if starts else 0
    return {
        "starts": starts,
        "round_of": round_of,
        "n_rounds": n_rounds,
        "rounds": rounds[:32],
        "makespan_sched": makespan,
        "data_span": makespan - t_first_start,
        "t_first_data_start": t_first_start,
        "convoy_span": convoy,
        "grant_ops": st.n_grant_ops,
        "iter_ops": st.n_iter_ops,
        "n_unanimous": n_unan_total,
        "unanimous_frac": (round(n_unan_total / len(fps), 4) if fps else None),
        "mean_flows_per_round": (round(len(fps) / n_rounds, 2)
                                 if n_rounds else None),
        "grants_per_src": grants_per_src,
        "conflict_domain": conflict_domain,
        "fill": fill,
        "iters": iters,
        "t_rtt": t_rtt,
        "pipeline_depth": pipeline_depth,
        "ctrl_msgs_total": ctrl_msgs,
        "ctrl_msgs_per_round": 2 * topo.n,
        "residual_bitmap_ok": bitmap_ok,
        "requests_per_round": req_per_round[:32],
    }


def schedule_ring(topo: RingTopology, pairs: Sequence[Pair], *,
                  ring_path_mode: str = "fixed", m: int = 1,
                  grants_per_src: int = 1, conflict_domain: str = "free_at",
                  iters: int = 1, fill: str = "arc_desc", t_rtt: int = 16,
                  pipeline_depth: int = 1, seed: int = 0,
                  sweeps: int = 8) -> dict[str, Any]:
    """End to end: plan the routes, schedule, then re-verify all five clauses."""
    plan = build_ring_plan(topo, pairs, ring_path_mode, seed=seed,
                           sweeps=sweeps)
    fps = build_ring_flows(topo, plan, pairs, m, release=t_rtt)
    res = islip2d_ring_schedule(topo, fps, grants_per_src=grants_per_src,
                                conflict_domain=conflict_domain, iters=iters,
                                fill=fill, t_rtt=t_rtt,
                                pipeline_depth=pipeline_depth, seed=seed)
    by_id = {fp.flow_id: fp for fp in fps}
    items = [(by_id[fid], t0) for fid, t0 in res["starts"].items()]
    res["verify"] = verify_dr(topo, items)
    res["plan"] = plan.summary()
    res["round_lb"] = plan.bounds["round_lb"]
    res["round_ratio"] = (round(res["n_rounds"] / res["round_lb"], 3)
                          if res["round_lb"] else None)
    res["audit"] = topo.audit()
    return res


if __name__ == "__main__":
    import json
    import time

    n = 48
    a2a = [(s, d) for s in range(n) for d in range(n) if s != d]

    print("=== ring alltoall, m=1 ===")
    print("--- path mode x grants_per_src (arc, ports=1, free_at) ---")
    for pm in ("fixed", "balanced"):
        for g in (1, 2):
            topo = RingTopology(**LEGACY_WIRE)
            t0 = time.perf_counter()
            r = schedule_ring(topo, a2a, ring_path_mode=pm, grants_per_src=g,
                              pipeline_depth=1 << 20)
            print(f"  {pm:9} g={g} rounds={r['n_rounds']:>4}/lb="
                  f"{r['round_lb']:<4} ratio={r['round_ratio']:<6} "
                  f"un={r['unanimous_frac']:<7} "
                  f"cf={int(r['verify']['conflict_free'])} "
                  f"turnres={r['verify']['max_turn_residency']} "
                  f"{time.perf_counter()-t0:.1f}s")

    print("\n--- ports 1 vs 2 (fixed, g=2) ---")
    for bp in (1, 2):
        topo = RingTopology(**LEGACY_WIRE, board_ports=bp, leave_ports=bp)
        r = schedule_ring(topo, a2a, ring_path_mode="fixed", grants_per_src=2,
                          pipeline_depth=1 << 20)
        print(f"  ports={bp} rounds={r['n_rounds']:>4}/lb={r['round_lb']:<4} "
              f"cf={int(r['verify']['conflict_free'])}")

    print("\n--- spatial reuse: arc vs whole_ring (fixed, g=2) ---")
    for sr in ("arc", "whole_ring"):
        topo = RingTopology(**LEGACY_WIRE, spatial_reuse=sr)
        r = schedule_ring(topo, a2a, ring_path_mode="fixed", grants_per_src=2,
                          pipeline_depth=1 << 20)
        print(f"  {sr:11} rounds={r['n_rounds']:>4}/lb={r['round_lb']:<4} "
              f"cf={int(r['verify']['conflict_free'])}")

    print("\n--- iters x fill (fixed, g=2) ---")
    print(f"  {'fill':10} " + " ".join(f"it={i}" for i in (0, 1, 2)))
    for fl in FILL_ORDERS:
        row = []
        for it in (0, 1, 2):
            topo = RingTopology(**LEGACY_WIRE)
            r = schedule_ring(topo, a2a, ring_path_mode="fixed",
                              grants_per_src=2, iters=it, fill=fl,
                              pipeline_depth=1 << 20)
            row.append(r["n_rounds"])
        print(f"  {fl:10} " + " ".join(f"{v:4}" for v in row))

    print("\n--- conflict domain (fixed, g=2) ---")
    for cd in ("free_at", "interval"):
        topo = RingTopology(**LEGACY_WIRE)
        r = schedule_ring(topo, a2a, ring_path_mode="fixed", grants_per_src=2,
                          conflict_domain=cd, pipeline_depth=1 << 20)
        print(f"  {cd:9} rounds={r['n_rounds']:>4} span={r['data_span']:>6} "
              f"cf={int(r['verify']['conflict_free'])}")
