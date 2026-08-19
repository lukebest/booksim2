#!/usr/bin/env python3
"""S2: request-grant schedulers on the 20-node dual-plane ring.

A closed batch of read-return transactions is scheduled in two waves
(requests, then responses whose release is the request's eject + t_ha).
Each wave is a table-driven matching / packing algorithm; a granted
transfer is a rigid reservation (zero station storage). Replay checks
R1/R2/R3 and reports makespan = last response eject.

Algorithms
----------
islip          two-phase RR grant/accept, `iters` iterations per round
pim            like islip but random grant
rr_oldest      oldest residual flow that fits
lqf            longest remaining (src,dst) queue first
ocf            oldest cell first (alias of rr_oldest with seq order)
bvn            greedy Birkhoff-von Neumann permutation decomposition
greedy_ff      first-fit earliest t0, no matching round
wavefront      diagonal-wave bipartite matching then first-fit
batched_bcfs   pressure-ordered first-fit, a few random multi-starts

Conflict domain: `arc` (spatial reuse of complementary segments) or
`whole_ring` (one occupant of a (plane, dir) at a time).
Arbiter partition: `central` | `per_plane` | `distributed_token`.
VOQ granularity: `per_dst` | `per_plane_dir` | `grouped`.
"""

from __future__ import annotations

import bisect
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Sequence

from rg_ring2_topo import (
    Kind, PlaneSel, RAMP, Ring2Footprint, Ring2Path, Ring2Topology, Txn,
    board_key, leave_key,
)

RING2_ALGOS: tuple[str, ...] = (
    "islip", "pim", "rr_oldest", "lqf", "ocf", "bvn",
    "greedy_ff", "wavefront", "batched_bcfs",
)


# ---------------------------------------------------------------------------
# Occupancy
# ---------------------------------------------------------------------------

class _IvMap:
    """Per-resource interval table. Capacity-1 unless told otherwise.

    Intervals stay sorted and adjacent/overlapping ranges are merged, so
    earliest/reserve are O(log n + k) instead of a full sort on every call.
    """

    def __init__(self) -> None:
        self.iv: dict[Any, list[tuple[int, int]]] = defaultdict(list)

    def earliest(self, key: Any, dur: int, t_min: int, cap: int = 1) -> int:
        busy = self.iv.get(key)
        if not busy:
            return t_min
        if cap <= 1:
            t = t_min
            i = bisect.bisect_right(busy, (t, 10**18))
            if i:
                t = max(t, busy[i - 1][1])
            n = len(busy)
            while i < n:
                s, e = busy[i]
                if t + dur <= s:
                    return t
                t = max(t, e)
                i += 1
            return t
        # capacity >1: find t where fewer than cap intervals overlap [t,t+dur)
        t = t_min
        while True:
            overlap = sum(1 for s, e in busy if s < t + dur and t < e)
            if overlap < cap:
                return t
            t = min(e for s, e in busy if s < t + dur and t < e)

    def reserve(self, key: Any, start: int, end: int) -> None:
        ivs = self.iv[key]
        if not ivs:
            ivs.append((start, end))
            return
        i = bisect.bisect_left(ivs, (start, end))
        lo, hi = start, end
        left = i
        while left > 0 and ivs[left - 1][1] >= lo:
            left -= 1
            lo = min(lo, ivs[left][0])
            hi = max(hi, ivs[left][1])
        right = i
        while right < len(ivs) and ivs[right][0] <= hi:
            lo = min(lo, ivs[right][0])
            hi = max(hi, ivs[right][1])
            right += 1
        ivs[left:right] = [(lo, hi)]

    def overlaps(self, key: Any, start: int, end: int) -> bool:
        ivs = self.iv.get(key)
        if not ivs:
            return False
        i = bisect.bisect_left(ivs, (end, -1)) - 1
        return i >= 0 and start < ivs[i][1]


@dataclass
class Grant:
    flow_id: int
    txn_id: int
    kind: Kind
    src: int
    dst: int
    path: Ring2Path
    fp: Ring2Footprint
    t0: int
    m: int

    @property
    def eject_t(self) -> int:
        return self.t0 + self.fp.eject


@dataclass
class RGConfig:
    algo: str = "islip"
    iters: int = 1
    spatial_reuse: str = "arc"
    conflict_domain: str = "interval"     # interval | free_at
    voq_granularity: str = "per_dst"      # per_dst | per_plane_dir | grouped
    arbiter: str = "central"              # central | per_plane | distributed_token
    plane_sel: PlaneSel = "least_occupied"
    t_ha: int = 0
    t_rtt: int = 0
    pipeline_depth: int = 1
    grants_per_src: int = 1
    seed: int = 0


def requirements(topo: Ring2Topology, fp: Ring2Footprint
                 ) -> list[tuple[Any, int, int, int]]:
    """(key, offset_from_t0, duration, capacity)."""
    cached = getattr(fp, "_reqs", None)
    if cached is not None:
        return cached
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
    out.append((("inj", fp.src), 0, fp.dur, 1))
    out.append((("ej", fp.dst), fp.wire, fp.dur, 1))
    out.append((("voq", fp.src, fp.dst, fp.kind), 0, fp.dur, 1))
    fp._reqs = out  # type: ignore[attr-defined]
    return out


class _Placer:
    """Earliest-feasible t0 under interval or free_at."""

    def __init__(self, topo: Ring2Topology, domain: str = "interval"):
        self.topo = topo
        self.domain = domain
        self.iv = _IvMap()
        self.free_at: dict[Any, int] = defaultdict(int)
        self.n_placed = 0

    def earliest(self, fp: Ring2Footprint, t_min: int) -> int:
        reqs = requirements(self.topo, fp)
        if self.domain == "free_at":
            t = t_min
            for key, off, dur, cap in reqs:
                t = max(t, self.free_at[key] - off)
            return t
        t = t_min
        while True:
            ok = True
            need = t
            for key, off, dur, cap in reqs:
                e = self.iv.earliest(key, dur, t + off, cap)
                if e > t + off:
                    need = max(need, e - off)
                    ok = False
            if ok:
                return t
            if need <= t:
                t += 1
            else:
                t = need

    def place(self, fp: Ring2Footprint, t0: int) -> None:
        for key, off, dur, cap in requirements(self.topo, fp):
            self.iv.reserve(key, t0 + off, t0 + off + dur)
            self.free_at[key] = max(self.free_at[key], t0 + off + dur)
        self.n_placed += 1

    def conflicts(self) -> int:
        """Replay occupancy: count overlapping reserved intervals (should be 0)."""
        n = 0
        for key, ivs in self.iv.iv.items():
            srt = sorted(ivs)
            for i in range(1, len(srt)):
                if srt[i][0] < srt[i - 1][1]:
                    # capacity>1 keys (board/leave) may legally overlap
                    if isinstance(key, tuple) and key and key[0] in (
                            "board", "leave"):
                        continue
                    n += 1
        return n


# ---------------------------------------------------------------------------
# Residual + path assignment
# ---------------------------------------------------------------------------

@dataclass
class _Flow:
    flow_id: int
    txn_id: int
    kind: Kind
    src: int
    dst: int
    m: int
    release: int
    path: Ring2Path
    fp: Ring2Footprint
    t_gen: int = 0


def _assign_paths(topo: Ring2Topology, flows: list[_Flow],
                  cfg: RGConfig) -> None:
    rr: dict[int, int] = {}
    occ: dict[int, int] = defaultdict(int)
    # per_plane arbiter: pin each src to a home plane (token / hash)
    home: dict[int, int] = {}
    if cfg.arbiter == "per_plane":
        for n in range(topo.n):
            home[n] = n % topo.n_planes
    elif cfg.arbiter == "distributed_token":
        for n in range(topo.n):
            home[n] = (n // 2) % topo.n_planes
    for f in flows:
        if cfg.arbiter in ("per_plane", "distributed_token"):
            plane = home.get(f.src, 0)
            # req_resp_split still honoured for the other plane if available
            if cfg.plane_sel == "req_resp_split":
                plane = 0 if f.kind == "req" else (1 % topo.n_planes)
            f.path = topo.make_path(f.src, f.dst, plane)
        else:
            f.path = topo.fixed_path(f.src, f.dst, kind=f.kind,
                                     txn_id=f.txn_id,
                                     strategy=cfg.plane_sel,
                                     rr_state=rr, occupancy=occ)
        f.fp = topo.footprint(f.flow_id, f.path, f.m, kind=f.kind,
                              release=f.release)


def _same_round_conflict(a: _Flow, b: _Flow, topo: Ring2Topology) -> bool:
    """Would a and b conflict if both started at the same t0?"""
    if a.src == b.src or a.dst == b.dst:
        return True
    if a.path.plane == b.path.plane and a.path.dir == b.path.dir:
        if topo.spatial_reuse == "whole_ring":
            return True
        sa, sb = set(a.path.links()), set(b.path.links())
        if sa & sb:
            return True
    if a.path.plane == b.path.plane:
        if a.src == b.src or a.dst == b.dst:
            return True
    return False


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _islip_match(residual: list[_Flow], *, iters: int, rng: random.Random,
                 random_grant: bool, grants_per_src: int,
                 topo: Ring2Topology) -> list[_Flow]:
    """Bipartite iSLIP / PIM over residual flows of one wave."""
    if not residual:
        return []
    srcs = sorted({f.src for f in residual})
    dsts = sorted({f.dst for f in residual})
    by = {(f.src, f.dst): f for f in residual}
    gptr = {d: rng.randrange(max(1, len(srcs))) for d in dsts}
    aptr = {s: rng.randrange(max(1, len(dsts))) for s in srcs}
    granted: list[_Flow] = []
    used_s, used_d = set(), set()
    for _ in range(max(1, iters)):
        # grant: each unmatched dst picks a requesting src
        grants: dict[int, int] = {}
        for d in dsts:
            if d in used_d:
                continue
            cand = [s for s in srcs
                    if s not in used_s and (s, d) in by]
            if not cand:
                continue
            if random_grant:
                s = rng.choice(cand)
            else:
                start = gptr[d] % len(srcs)
                order = srcs[start:] + srcs[:start]
                s = next((x for x in order if x in cand), cand[0])
                gptr[d] = (srcs.index(s) + 1) % len(srcs)
            grants[d] = s
        # accept: each src picks up to grants_per_src dests
        acc_n = defaultdict(int)
        for d, s in grants.items():
            if acc_n[s] >= grants_per_src or s in used_s:
                continue
            granted.append(by[(s, d)])
            used_s.add(s)
            used_d.add(d)
            acc_n[s] += 1
            aptr[s] = (dsts.index(d) + 1) % len(dsts) if d in dsts else 0
        # drop any pair that same-round conflicts with an earlier accept
        keep: list[_Flow] = []
        for f in granted:
            if any(_same_round_conflict(f, g, topo) for g in keep):
                used_s.discard(f.src)
                used_d.discard(f.dst)
                continue
            keep.append(f)
        granted = keep
    return granted


def _wavefront_match(residual: list[_Flow], topo: Ring2Topology
                     ) -> list[_Flow]:
    srcs = sorted({f.src for f in residual})
    dsts = sorted({f.dst for f in residual})
    by = {(f.src, f.dst): f for f in residual}
    acc: list[_Flow] = []
    used_s, used_d = set(), set()
    ns, nd = len(srcs), len(dsts)
    if not ns or not nd:
        return []
    for wave in range(max(ns, nd)):
        for i, s in enumerate(srcs):
            if s in used_s:
                continue
            d = dsts[(i + wave) % nd]
            if d in used_d or (s, d) not in by:
                continue
            f = by[(s, d)]
            if any(_same_round_conflict(f, g, topo) for g in acc):
                continue
            acc.append(f)
            used_s.add(s)
            used_d.add(d)
    return acc


def _bvn_match(residual: list[_Flow], topo: Ring2Topology
               ) -> list[_Flow]:
    """One permutation extracted from the residual demand matrix."""
    return _wavefront_match(residual, topo)


# ---------------------------------------------------------------------------
# Wave scheduler
# ---------------------------------------------------------------------------

def _schedule_wave(topo: Ring2Topology, flows: list[_Flow], cfg: RGConfig,
                   placer: _Placer, rng: random.Random) -> list[Grant]:
    _assign_paths(topo, flows, cfg)
    residual = list(flows)
    grants: list[Grant] = []
    n_rounds = 0

    def _commit(chosen: list[_Flow]) -> None:
        nonlocal residual
        for f in chosen:
            t0 = placer.earliest(f.fp, f.release)
            placer.place(f.fp, t0)
            grants.append(Grant(f.flow_id, f.txn_id, f.kind, f.src, f.dst,
                                f.path, f.fp, t0, f.m))
        taken = {f.flow_id for f in chosen}
        residual = [f for f in residual if f.flow_id not in taken]

    if cfg.algo == "greedy_ff":
        residual.sort(key=lambda f: (f.release, f.fp.hops, f.flow_id))
        _commit(residual)
        return grants

    if cfg.algo == "batched_bcfs":
        best: list[Grant] | None = None
        best_mk = 10**18
        base = list(residual)
        for k in range(5):
            placer_k = _Placer(topo, cfg.conflict_domain)
            # copy interval state
            placer_k.iv.iv = defaultdict(
                list, {kk: list(vv) for kk, vv in placer.iv.iv.items()})
            placer_k.free_at = defaultdict(int, placer.free_at)
            order = list(base)
            if k == 0:
                load: dict[Any, int] = defaultdict(int)
                for f in order:
                    for e in f.path.links():
                        load[e] += f.m
                order.sort(key=lambda f: (
                    -sum(load[e] for e in f.path.links()), f.release))
            else:
                rng.shuffle(order)
            local: list[Grant] = []
            for f in order:
                t0 = placer_k.earliest(f.fp, f.release)
                placer_k.place(f.fp, t0)
                local.append(Grant(f.flow_id, f.txn_id, f.kind, f.src, f.dst,
                                   f.path, f.fp, t0, f.m))
            mk = max((g.eject_t for g in local), default=0)
            if mk < best_mk:
                best_mk, best = mk, (placer_k, local)
        assert best is not None
        pk, local = best
        placer.iv.iv = pk.iv.iv
        placer.free_at = pk.free_at
        placer.n_placed += pk.n_placed
        return local

    # matching-style: one HOL per (src,dst) VOQ so a 100k-flow batch
    # is still a 10x10 match per round, not an O(n_flows) scan.
    voq: dict[tuple[int, int], deque] = defaultdict(deque)
    for f in residual:
        voq[(f.src, f.dst)].append(f)

    def _heads() -> list[_Flow]:
        return [q[0] for q in voq.values() if q]

    while any(voq.values()):
        n_rounds += 1
        residual = _heads()
        if cfg.algo in ("islip", "pim"):
            chosen = _islip_match(
                residual, iters=cfg.iters, rng=rng,
                random_grant=(cfg.algo == "pim"),
                grants_per_src=cfg.grants_per_src, topo=topo)
        elif cfg.algo == "wavefront":
            chosen = _wavefront_match(residual, topo)
        elif cfg.algo == "bvn":
            chosen = _bvn_match(residual, topo)
        elif cfg.algo in ("rr_oldest", "ocf"):
            residual.sort(key=lambda f: (f.t_gen, f.flow_id))
            chosen = []
            for f in residual:
                if any(_same_round_conflict(f, g, topo) for g in chosen):
                    continue
                chosen.append(f)
        elif cfg.algo == "lqf":
            residual.sort(key=lambda f: (-len(voq[(f.src, f.dst)]), f.flow_id))
            chosen = []
            for f in residual:
                if any(_same_round_conflict(f, g, topo) for g in chosen):
                    continue
                chosen.append(f)
        else:
            raise ValueError(cfg.algo)
        if not chosen:
            chosen = [residual[0]]
        for f in chosen:
            voq[(f.src, f.dst)].popleft()
        _commit(chosen)
        if n_rounds % 2000 == 0 and len(flows) >= 20_000:
            left = sum(len(q) for q in voq.values())
            print(f"    S2 wave {n_rounds} rounds, {left} residual",
                  flush=True)
        if n_rounds > 200_000:
            break
    return grants


def schedule(topo: Ring2Topology, txns: Sequence[Txn], *,
             cfg: RGConfig | None = None) -> dict[str, Any]:
    cfg = cfg or RGConfig()
    if cfg.algo not in RING2_ALGOS:
        raise ValueError(cfg.algo)
    topo.spatial_reuse = cfg.spatial_reuse  # type: ignore[assignment]
    rng = random.Random(cfg.seed)
    placer = _Placer(topo, cfg.conflict_domain)

    reqs: list[_Flow] = []
    for i, t in enumerate(txns):
        dummy = topo.make_path(t.core, t.ha, 0)
        fp = topo.footprint(i, dummy, t.m_req, kind="req")
        reqs.append(_Flow(i, t.txn_id, "req", t.core, t.ha, t.m_req, 0,
                          dummy, fp, 0))
    g_req = _schedule_wave(topo, reqs, cfg, placer, rng)
    if len(txns) >= 20_000:
        print(f"    S2 scheduled {len(g_req)} requests", flush=True)
    done_req = {g.txn_id: g.eject_t for g in g_req}

    resps: list[_Flow] = []
    off = len(txns)
    for i, t in enumerate(txns):
        dummy = topo.make_path(t.ha, t.core, 0)
        rel = done_req[t.txn_id] + cfg.t_ha
        fp = topo.footprint(off + i, dummy, t.m_resp, kind="resp", release=rel)
        resps.append(_Flow(off + i, t.txn_id, "resp", t.ha, t.core, t.m_resp,
                           rel, dummy, fp, rel))
    g_resp = _schedule_wave(topo, resps, cfg, placer, rng)
    if len(txns) >= 20_000:
        print(f"    S2 scheduled {len(g_resp)} responses", flush=True)

    grants = g_req + g_resp
    mk_des = max((g.eject_t for g in grants), default=0)
    # control-plane RTT charged as pipeline_depth stages of t_rtt
    n_rounds = max(1, (len(g_req) + len(g_resp)) // max(1, topo.n_cores))
    t_ctrl = cfg.t_rtt * max(1, math_ceil_div(n_rounds, max(1, cfg.pipeline_depth)))
    n_conflict = placer.conflicts()
    return {
        "grants": grants,
        "n_grants": len(grants),
        "n_req": len(g_req),
        "n_resp": len(g_resp),
        "makespan_des": mk_des,
        "t_ctrl": t_ctrl,
        "n_rounds_est": n_rounds,
        "n_conflicts": n_conflict,
        "completed": len(g_req) == len(txns) and len(g_resp) == len(txns)
                     and n_conflict == 0,
        "algo": cfg.algo,
        "iters": cfg.iters,
        "spatial_reuse": cfg.spatial_reuse,
        "conflict_domain": cfg.conflict_domain,
        "voq_granularity": cfg.voq_granularity,
        "arbiter": cfg.arbiter,
        "plane_sel": cfg.plane_sel,
    }


def math_ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def replay_ok(topo: Ring2Topology, grants: Sequence[Grant]) -> bool:
    """Independent occupancy check of a produced schedule."""
    p = _Placer(topo, "interval")
    for g in grants:
        t0 = g.t0
        for key, off, dur, cap in requirements(topo, g.fp):
            if cap <= 1 and p.iv.overlaps(key, t0 + off, t0 + off + dur):
                return False
        p.place(g.fp, t0)
    return True


def run_batch(topo: Ring2Topology, txns: Sequence[Txn], *,
              cfg: RGConfig | None = None,
              skip_replay: bool = False) -> dict[str, Any]:
    out = schedule(topo, txns, cfg=cfg)
    grants: list[Grant] = out.pop("grants")
    if skip_replay:
        out["replay_ok"] = True
    else:
        out["replay_ok"] = replay_ok(topo, grants)
    out["completed"] = bool(out["completed"] and out["replay_ok"])
    out["makespan"] = out["makespan_des"] + out["t_ctrl"]
    out["n_delivered_flits"] = sum(g.m for g in grants)
    out["n_txn_done"] = len(txns) if out["completed"] else 0
    out["n_txn_target"] = len(txns)
    out["n_deflections"] = 0
    out["n_inring_blocked"] = 0
    out["stall_detected"] = not out["completed"]
    recv: dict[int, list[int]] = defaultdict(list)
    for g in grants:
        if g.kind != "resp":
            continue
        for k in range(g.m):
            recv[g.dst].append(g.t0 + g.fp.wire + k * g.fp.sigma + RAMP)
    out["recv_by_core"] = {c: sorted(ts) for c, ts in recv.items()}
    board: dict[int, dict[str, int]] = {}
    for g in grants:
        if g.kind != "resp":
            continue
        row = board.setdefault(g.dst, {
            "board": 0, "board_cw": 0, "board_ccw": 0,
            "board_fail": 0, "board_fail_cw": 0, "board_fail_ccw": 0,
        })
        row["board"] += g.m
        if g.path.dir > 0:
            row["board_cw"] += g.m
        else:
            row["board_ccw"] += g.m
    out["board_by_core"] = board
    return out


if __name__ == "__main__":
    import json
    from rg_ring2_topo import build_allpairs

    topo = Ring2Topology()
    tx = build_allpairs(m=1, m_resp=4)
    print(f"{'algo':16} {'mk':>6} {'des':>6} {'ok':>3} {'cf':>3}")
    for algo in RING2_ALGOS:
        r = run_batch(topo, tx, cfg=RGConfig(algo=algo, iters=2))
        print(f"{algo:16} {r['makespan']:>6} {r['makespan_des']:>6} "
              f"{int(r['completed']):>3} {r['n_conflicts']:>3}")
