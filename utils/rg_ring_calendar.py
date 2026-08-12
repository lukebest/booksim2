#!/usr/bin/env python3
"""Static slot tables (calendars) for ring collectives, plus their robustness.

A calendar here is a rigid, compile-time assignment of one start cycle per
transfer. Nothing waits anywhere: a transfer either fits with zero slack on
every resource its arc touches, or it is placed later. That is the same rigid
semantics `RingFootprint` already encodes for the centralized arbiter, only
decided offline, so the D-R checker in `rg_ring_topo` validates a calendar
without modification.

Placement uses bitmask first-fit. Each resource keeps a saturation mask over
the horizon; "all of [a, a+dur) has spare capacity" is an AND of shifted masks,
and the feasible start set for a whole transfer is the AND of its requirements
shifted back by their prefix delays. The lowest set bit is the earliest legal
start. This matters beyond speed: the feasible set is exactly the interval
domain the report calls the difference between a working scheduler and a
non-working one, so computing it directly removes the chance of quietly
serializing on a stale frontier.

Robustness is where a static calendar earns or loses its keep, and both answers
here follow from one structural fact: **the only conflict-free ways to replay a
rigid calendar late are to shift the whole thing, or to shift a whole phase.**
Shifting one transfer on its own moves it relative to its neighbours and breaks
R1/R2/R3. So the two jitter policies below are not arbitrary design choices,
they are the complete set:

    global_shift  one constant offset for everything -- absorbs nothing, the
                  makespan grows by the worst node's lateness
    phase_shift   one offset per phase, recomputed from who is actually ready --
                  absorbs lateness that lands inside a phase it does not gate

Faults are read the same way. A ring is 2-connected, so a dead SEGMENT always
has a way round: pick the other direction. A dead NODE is qualitatively worse,
because a bufferless station sits in the path of its own ring -- unless it has a
bypass mux, losing one node kills two segments on each of its two rings and
both rings degrade to paths. `FaultModel.bypass` toggles exactly that, and the
sweep prices it.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from rg_ring_collectives import (
    ALGOS, PATTERNS, Phase, RingCollective, Xfer, build_ring_collective,
    hamilton_cycle, mcast_applicable, replay,
)
from rg_ring_topo import (
    Edge, RingFootprint, RingId, RingMcastFootprint, RingPath, RingTopology,
    board_key, build_ring_plan, leave_key, verify_dr,
)
from rg_topo import RAMP, RAMP_BW, coord, nid

FILL_ORDERS: tuple[str, ...] = ("arc_desc", "flit_desc", "flowid", "pressure")


# ---------------------------------------------------------------------------
# 1. Resource requirements (one place, both footprint kinds)
# ---------------------------------------------------------------------------

def cal_requirements(topo: RingTopology, fp: Any
                     ) -> list[tuple[Any, int, int, int]]:
    """(key, offset_from_t0, duration, capacity) for one transfer.

    The ejection entry is charged per MEMBER at that member's own arrival
    offset. For unicast that is the single destination; for a copy-and-continue
    arc it is every reader, each at the cycle the head passes it. Charging the
    arc once but the ejections many times is the whole accounting difference
    multicast makes, and putting it in one function keeps it from drifting.
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
    for d, off in fp.arrivals.items():
        out.append((("ej", d), off, fp.dur, ramp_cap))
    return out


class _Occ:
    """Per-resource saturation masks over a bounded horizon."""

    def __init__(self, horizon: int):
        self.h = horizon
        self.mask = (1 << horizon) - 1
        self.full: dict[Any, int] = defaultdict(int)
        self.cnt: dict[Any, dict[int, int]] = defaultdict(dict)

    def _run(self, key: Any, dur: int) -> int:
        """Bits `a` where [a, a+dur) all have spare capacity."""
        free = self.mask & ~self.full[key]
        if dur <= 1:
            return free
        acc = free
        shift = 1
        remaining = dur - 1
        # doubling: AND of dur consecutive shifts in log(dur) steps
        while remaining > 0:
            take = min(shift, remaining)
            acc &= acc >> take
            remaining -= take
            shift *= 2
        return acc

    def feasible_starts(self, reqs: Sequence[tuple[Any, int, int, int]],
                        t_min: int) -> int:
        ok = self.mask >> t_min << t_min
        for key, off, dur, cap in reqs:
            if dur <= 0:
                continue
            ok &= self._run(key, dur) >> off
            if not ok:
                return 0
        return ok

    def reserve(self, reqs: Sequence[tuple[Any, int, int, int]], t0: int
                ) -> None:
        for key, off, dur, cap in reqs:
            c = self.cnt[key]
            for t in range(t0 + off, t0 + off + dur):
                v = c.get(t, 0) + 1
                c[t] = v
                if v >= cap:
                    self.full[key] |= 1 << t
                if v > cap:
                    raise AssertionError(
                        f"capacity {cap} exceeded on {key} at {t}: {v}")


def _lowest_bit(x: int) -> int:
    return (x & -x).bit_length() - 1


# ---------------------------------------------------------------------------
# 2. Footprint construction from a transfer
# ---------------------------------------------------------------------------

def xfer_footprint(topo: RingTopology, x: Xfer,
                   paths: dict[tuple[int, int], RingPath] | None = None,
                   *, forbidden: frozenset[Edge] = frozenset()) -> Any:
    """A single-ring transfer becomes an arc footprint (multicast covers the
    one-member case too); a `ring=None` transfer needs the two-phase planner.
    """
    if x.ring is None:
        if paths is None:
            raise ValueError("two-phase transfer needs a route plan")
        fp = topo.footprint(x.xid, paths[(x.src, x.dsts[0])], x.nflit)
    else:
        fp = topo.mcast_footprint(x.xid, x.ring, x.src, list(x.dsts),
                                  x.direction, x.nflit, op=x.op)
    if forbidden and any(e in forbidden for e, _ in fp.links):
        raise _Blocked(x.xid)
    return fp


class _Blocked(Exception):
    def __init__(self, xid: int):
        super().__init__(f"transfer {xid} needs a dead segment")
        self.xid = xid


def _fill_key(order: str):
    if order == "arc_desc":
        return lambda fp: (-fp.hops, fp.flow_id)
    if order == "flit_desc":
        return lambda fp: (-fp.dur, -fp.hops, fp.flow_id)
    if order == "pressure":
        return lambda fp: (-fp.pressure, -fp.hops, fp.flow_id)
    if order == "flowid":
        return lambda fp: (fp.flow_id,)
    raise ValueError(f"unknown fill order: {order}")


# ---------------------------------------------------------------------------
# 3. The calendar
# ---------------------------------------------------------------------------

def latency_floor(topo: RingTopology, col: RingCollective,
                  fps: dict[int, Any]) -> dict[str, Any]:
    """Makespan with INFINITE bandwidth: the critical path through the phases.

    Every transfer in a barrier phase starts at the phase floor, so the phase
    costs its longest transfer and the phases add up. This is the floor that
    contention cannot go below, and it is the one the occupancy bound misses
    entirely. Reporting only the occupancy bound makes a wire-bound collective
    look 30x off its "bound", which says nothing about the schedule and
    everything about the wrong bound.
    """
    per_phase: list[int] = []
    for ph in col.phases:
        here = [fps[x.xid] for x in ph.xfers if x.xid in fps]
        per_phase.append(max((fp.wire + fp.dur + RAMP for fp in here),
                             default=0))
    total = 0
    for pi, ph in enumerate(col.phases):
        if ph.barrier or pi == 0:
            total += per_phase[pi]
        else:
            total = max(total, per_phase[pi])
    return {"latency_lb": total, "per_phase_latency": per_phase}


@dataclass
class Calendar:
    pattern: str
    tier: str
    algo: str
    m: int
    fill: str
    starts: dict[int, int]
    fps: dict[int, Any]
    phase_of: dict[int, int]
    phase_window: list[tuple[int, int]]
    makespan: int
    node_done: dict[int, int]
    bounds: dict[str, Any]
    horizon: int
    blocked: list[int] = field(default_factory=list)

    @property
    def items(self) -> list[tuple[Any, int]]:
        return [(self.fps[k], self.starts[k]) for k in self.starts]

    def slack(self) -> dict[str, Any]:
        """How much later each transfer could start before it gates its phase.

        Defined against the transfer's own phase end, not the global makespan,
        because a rigid calendar can only be replayed by shifting whole phases:
        slack that sits behind a barrier cannot be spent.
        """
        sl: list[int] = []
        for xid, t0 in self.starts.items():
            _, end = self.phase_window[self.phase_of[xid]]
            sl.append(max(0, end - (t0 + self.fps[xid].tail)))
        sl.sort()
        n = len(sl)
        return {
            "n": n,
            "min": sl[0] if n else 0,
            "p50": sl[n // 2] if n else 0,
            "p90": sl[min(n - 1, int(0.9 * n))] if n else 0,
            "max": sl[-1] if n else 0,
            "mean": round(sum(sl) / n, 1) if n else 0,
            "frac_zero": round(sum(1 for v in sl if v == 0) / n, 4) if n else 0,
        }

    def utilization(self, topo: RingTopology) -> dict[str, Any]:
        """Global and critical-arc link utilization.

        Both are needed. Global high with critical low means the schedule still
        has room; critical at 1.0 means the calendar has reached the arc-load
        bound and only a different route set can help.
        """
        per_link: dict[Edge, int] = defaultdict(int)
        for xid, fp in self.fps.items():
            for e, _ in fp.links:
                per_link[e] += fp.dur
        total = sum(per_link.values())
        n_links = len(topo.directed_links)
        peak = max(per_link.values()) if per_link else 0
        span = max(1, self.makespan)
        return {
            "total_link_cycles": total,
            "n_directed_links": n_links,
            "n_links_used": len(per_link),
            "global_util": round(total / (n_links * span), 4),
            "critical_arc_cycles": peak,
            "critical_arc_util": round(peak / span, 4),
            "critical_arc_cycles_vs_lb": round(
                peak / max(1, self.bounds["arc_load_lb"]), 4),
            "used_link_util": round(total / (max(1, len(per_link)) * span), 4),
        }

    def summary(self, topo: RingTopology) -> dict[str, Any]:
        lb = self.bounds
        return {
            "pattern": self.pattern, "tier": self.tier, "algo": self.algo,
            "m": self.m, "fill": self.fill,
            "makespan": self.makespan,
            "n_transfers": len(self.starts),
            "n_phases": len(self.phase_window),
            "phase_window": self.phase_window,
            "occupancy_lb": lb["occupancy_lb"],
            "latency_lb": lb["latency_lb"],
            "makespan_lb": lb["makespan_lb"],
            "binding_lb": lb["binding_lb"],
            "arc_load_lb": lb["arc_load_lb"],
            "port_lb": lb["port_lb"],
            "ramp_lb": lb["ramp_lb"],
            "makespan_over_lb": round(
                self.makespan / max(1, lb["makespan_lb"]), 3),
            "wire_bound_frac": round(
                lb["latency_lb"] / max(1, self.makespan), 3),
            "util": self.utilization(topo),
            "slack": self.slack(),
            "node_done_max": max(self.node_done.values()) if self.node_done
            else 0,
            "node_done_spread": (max(self.node_done.values())
                                 - min(self.node_done.values())
                                 if self.node_done else 0),
        }


def build_calendar(topo: RingTopology, col: RingCollective, *,
                   fill: str = "arc_desc", route_mode: str = "balanced",
                   horizon: int | None = None,
                   forbidden: frozenset[Edge] = frozenset(),
                   dead_nodes: frozenset[int] = frozenset(),
                   release: dict[int, int] | None = None,
                   ) -> Calendar:
    """Pack a collective into a rigid slot table, phase by phase.

    A barrier phase starts no earlier than the last delivery of the phase
    before it. That is deliberately pessimistic: the collectives here are
    data-dependent across phases (a row bundle cannot leave before the row
    allgather that built it), so overlapping phases would need per-node
    dependency tracking rather than a barrier. Where a phase is genuinely
    independent the builder marks `barrier=False` and the packer lets it slide
    back into the previous window.
    """
    two_phase = [x for x in col.xfers if x.ring is None]
    paths = None
    if two_phase:
        pl = sorted({(x.src, x.dsts[0]) for x in two_phase})
        paths = build_ring_plan(topo, pl, route_mode).paths

    fps: dict[int, Any] = {}
    blocked: list[int] = []
    for x in col.xfers:
        if x.src in dead_nodes or any(d in dead_nodes for d in x.dsts):
            blocked.append(x.xid)
            continue
        try:
            fps[x.xid] = xfer_footprint(topo, x, paths, forbidden=forbidden)
        except _Blocked:
            blocked.append(x.xid)
    bounds = topo.footprint_bounds(fps.values())
    bounds.update(latency_floor(topo, col, fps))
    cands = {"arc_load": bounds["arc_load_lb"], "port": bounds["port_lb"],
             "ramp": bounds["ramp_lb"], "latency": bounds["latency_lb"]}
    bounds["makespan_lb"] = max(cands.values())
    bounds["binding_lb"] = max(cands, key=lambda k: cands[k])

    if horizon is None:
        horizon = max(4096, 8 * bounds["occupancy_lb"]
                      + 64 * len(col.phases) + 4096)

    load: dict[Edge, int] = defaultdict(int)
    for fp in fps.values():
        for e, _ in fp.links:
            load[e] += fp.dur
    for fp in fps.values():
        fp.pressure = sum(load[e] for e, _ in fp.links)

    for attempt in range(6):
        occ = _Occ(horizon)
        starts: dict[int, int] = {}
        phase_of: dict[int, int] = {}
        window: list[tuple[int, int]] = []
        floor = 0
        overflow = False
        for pi, ph in enumerate(col.phases):
            here = [fps[x.xid] for x in ph.xfers if x.xid in fps]
            t_floor = floor if ph.barrier else (window[-1][0] if window else 0)
            p_start, p_end = None, t_floor
            for fp in sorted(here, key=_fill_key(fill)):
                reqs = cal_requirements(topo, fp)
                t_lo = t_floor
                if release:
                    t_lo = max(t_lo, release.get(fp.src, 0))
                ok = occ.feasible_starts(reqs, t_lo)
                if not ok:
                    overflow = True
                    break
                t0 = _lowest_bit(ok)
                if t0 + fp.eject >= horizon:
                    overflow = True
                    break
                occ.reserve(reqs, t0)
                starts[fp.flow_id] = t0
                phase_of[fp.flow_id] = pi
                p_start = t0 if p_start is None else min(p_start, t0)
                p_end = max(p_end, t0 + fp.eject)
            if overflow:
                break
            window.append((t_floor if p_start is None else p_start, p_end))
            floor = p_end
        if not overflow:
            break
        horizon *= 2
    else:
        raise RuntimeError("calendar horizon never large enough")

    node_done: dict[int, int] = defaultdict(int)
    makespan = 0
    for xid, t0 in starts.items():
        fp = fps[xid]
        for d, off in fp.arrivals.items():
            done = t0 + off + fp.dur + RAMP
            node_done[d] = max(node_done[d], done)
            makespan = max(makespan, done)

    return Calendar(col.pattern, col.tier, col.algo, col.m, fill, starts, fps,
                    phase_of, window, makespan, dict(node_done), bounds,
                    horizon, blocked)


# ---------------------------------------------------------------------------
# 4. Jitter: the two legal ways to replay a rigid calendar late
# ---------------------------------------------------------------------------

def release_offsets(topo: RingTopology, jitter: int, model: str, seed: int = 0
                    ) -> dict[int, int]:
    """Source readiness offsets, same three shapes `rg_batch_sched` uses.

    `distance_skew` compensates wire delay by letting far nodes start early; on
    a ring "far" is measured from the array centre because there is no central
    arbiter in a static calendar -- nothing is being answered, so the only
    meaningful skew is geometric.
    """
    rng = random.Random(seed)
    off: dict[int, int] = {}
    cx, cy = (topo.mx - 1) / 2, (topo.my - 1) / 2
    for s in range(topo.n):
        if model == "uniform_jitter":
            off[s] = rng.randrange(0, max(1, jitter))
        elif model == "burst":
            off[s] = 0 if s % 2 == 0 else jitter
        elif model == "distance_skew":
            x, y = coord(s, topo.mx)
            d = abs(x - cx) + abs(y - cy)
            dmax = cx + cy
            off[s] = int(round(jitter * (1 - d / dmax))) if dmax else 0
        else:
            raise ValueError(model)
    return off


def replay_jitter(cal: Calendar, release: dict[int, int], policy: str
                  ) -> dict[str, Any]:
    """Makespan of the SAME calendar when sources are late.

    Only whole-calendar and whole-phase shifts preserve D-R, so those are the
    two policies. Both keep every relative offset inside a phase, which is why
    neither needs re-verification: the shifted schedule is the original
    schedule translated, and translation cannot create a conflict.
    """
    if policy == "global_shift":
        shift = max(release.values()) if release else 0
        return {"policy": policy, "makespan": cal.makespan + shift,
                "shift_total": shift, "phase_shifts": [shift]}
    if policy != "phase_shift":
        raise ValueError(policy)

    # A phase may not start before every source feeding it holds its input.
    # For phase 0 that is the release offset; later it is when the source last
    # received something, which the previous phases' shifts already fix.
    ready: dict[int, int] = dict(release)
    shifts: list[int] = []
    acc = 0
    for pi, (w0, w1) in enumerate(cal.phase_window):
        need = 0
        members = [xid for xid, p in cal.phase_of.items() if p == pi]
        for xid in members:
            fp = cal.fps[xid]
            t_planned = cal.starts[xid] + acc
            need = max(need, ready.get(fp.src, 0) - t_planned)
        delta = max(0, need)
        acc += delta
        shifts.append(acc)
        for xid in members:
            fp = cal.fps[xid]
            for d, off in fp.arrivals.items():
                ready[d] = max(ready.get(d, 0),
                               cal.starts[xid] + acc + off + fp.dur + RAMP)
    return {"policy": policy, "makespan": cal.makespan + acc,
            "shift_total": acc, "phase_shifts": shifts}


def jitter_sweep(cal: Calendar, topo: RingTopology,
                 col: RingCollective | None = None, *,
                 models: Sequence[str] = ("uniform_jitter", "distance_skew",
                                          "burst"),
                 grid: Sequence[int] = (0, 4, 8, 16, 32, 64, 128, 256),
                 tol: float = 1.05, seed: int = 0,
                 fill: str = "arc_desc") -> dict[str, Any]:
    """J* = largest jitter whose makespan inflation stays within `tol`.

    Three policies, and the first two are expected to give the SAME answer for
    a reason worth stating: a rigid replay cannot absorb anything. Makespan
    grows one-for-one with the worst source's lateness, so J* comes out at
    whatever `tol` allows of the makespan and carries no information the
    makespan did not already carry. Per-phase resynchronization only helps when
    no transfer in the first phase starts at cycle zero from a late node, which
    is rare because the packer starts SOMETHING at zero by construction.

    `repack` is the informative one. It re-runs the packer with the release
    times as per-transfer floors, so late transfers slide into slack slots
    instead of dragging everything behind them. It needs a recompile, so it is
    not a replay policy -- it is the measurement of what the calendar's slack
    would be worth if the schedule could be rebuilt, i.e. the ceiling any
    smarter replay mechanism could reach.
    """
    out: dict[str, Any] = {"tol": tol, "grid": list(grid), "models": {}}
    base = cal.makespan
    policies = ["global_shift", "phase_shift"]
    if col is not None:
        policies.append("repack")
    for model in models:
        per_policy: dict[str, Any] = {}
        for policy in policies:
            curve = []
            jstar = 0
            for j in grid:
                rel = release_offsets(topo, j, model, seed=seed)
                if policy == "repack":
                    mk = build_calendar(topo, col, fill=fill,
                                        release=rel).makespan
                else:
                    mk = replay_jitter(cal, rel, policy)["makespan"]
                curve.append({"J": j, "makespan": mk,
                              "inflation": round(mk / base, 4)})
                if mk <= tol * base:
                    jstar = j
            per_policy[policy] = {"J_star": jstar, "curve": curve}
        out["models"][model] = per_policy
    return out


# ---------------------------------------------------------------------------
# 5. Faults
# ---------------------------------------------------------------------------

@dataclass
class FaultModel:
    name: str
    dead_nodes: frozenset[int] = frozenset()
    dead_links: frozenset[Edge] = frozenset()
    bypass: bool = True
    fault_class: str = "link"
    desc: str = ""

    def forbidden_links(self, topo: RingTopology) -> frozenset[Edge]:
        """Directed segments the calendar may not use.

        A declared dead link kills both directions: the fault is in the wire,
        not in one sender. A dead NODE kills its incident segments only when the
        station cannot be bypassed -- with a bypass mux the wire still conducts
        and only the ramp is lost, which is the difference this whole model
        exists to price.
        """
        bad: set[Edge] = set()
        for a, b in self.dead_links:
            bad.add((a, b))
            bad.add((b, a))
        if not self.bypass:
            for n in self.dead_nodes:
                for e in topo.directed_links:
                    if n in e:
                        bad.add(e)
        return frozenset(bad)

    def alive(self, topo: RingTopology) -> list[int]:
        return [n for n in range(topo.n) if n not in self.dead_nodes]


def wrap_link_scenarios(topo: RingTopology) -> list[FaultModel]:
    """The fault a ring has and a mesh does not: a broken wrap segment.

    Losing a wrap turns that ring into a path. Every other segment survives, so
    connectivity is untouched -- what breaks is the *rotation*, because a path
    cannot rotate, and the shortest-direction arc cover, because half its
    members are now only reachable the long way. This is the scenario that
    separates "the ring is 2-connected so faults are cheap" from "the ring's
    schedule assumed a cycle".
    """
    out: list[FaultModel] = []
    specs = [
        ("row0", ("row", 0)), ("row_mid", ("row", topo.my // 2)),
        ("col0", ("col", 0)), ("col_mid", ("col", topo.mx // 2)),
    ]
    for tag, ring in specs:
        nodes = topo.ring_nodes(ring)
        wrap = (nodes[-1], nodes[0])
        out.append(FaultModel(f"wrap_{tag}_1", dead_links=frozenset({wrap}),
                              fault_class="wrap",
                              desc=f"wrap segment of {ring} broken"))
    both = frozenset({
        (topo.ring_nodes(("row", 0))[-1], topo.ring_nodes(("row", 0))[0]),
        (topo.ring_nodes(("col", 0))[-1], topo.ring_nodes(("col", 0))[0]),
    })
    out.append(FaultModel("wrap_row0_col0_2", dead_links=both,
                          fault_class="wrap",
                          desc="one row wrap and one column wrap broken"))
    return out


def scattered_node_scenarios(topo: RingTopology) -> list[FaultModel]:
    """Non-adjacent dead nodes on ONE ring -- the only case a bypass mux saves.

    The repo's node scenarios are all contiguous blocks (1x1, 2x2, 3x3, a
    quadrant), and a ring survives a contiguous hole without any bypass
    hardware: remove a run of nodes and the survivors are still a path, so the
    other direction reaches every one of them. That is 2-connectivity doing its
    job, and it means the contiguous scenarios cannot show what a bypass mux is
    for.

    Two dead nodes with survivors BETWEEN them is different. Without a bypass
    the ring is cut in two places, and the survivors trapped in the middle are
    unreachable along that ring -- reachable only by leaving the dimension
    entirely, which a single-ring arc cover cannot do. These scenarios exist to
    make that distinction measurable rather than assumed.
    """
    out: list[FaultModel] = []
    specs = [
        ("row_split2", [nid(2, 0, topo.mx), nid(5, 0, topo.mx)],
         "two dead nodes on row 0 with survivors between them"),
        ("row_split3", [nid(1, 3, topo.mx), nid(4, 3, topo.mx),
                        nid(6, 3, topo.mx)],
         "three dead nodes on row 3, survivors in every gap"),
        ("col_split2", [nid(0, 1, topo.mx), nid(0, 4, topo.mx)],
         "two dead nodes on column 0 with survivors between them"),
        ("diag_scatter4", [nid(1, 1, topo.mx), nid(3, 2, topo.mx),
                           nid(5, 3, topo.mx), nid(7, 4, topo.mx)],
         "four scattered dead nodes, no two on one ring adjacent"),
    ]
    for tag, dn, desc in specs:
        out.append(FaultModel(f"scatter_{tag}", dead_nodes=frozenset(dn),
                              fault_class="scatter", desc=desc))
    return out


def repo_fault_scenarios(topo: RingTopology, *, bypass: bool = True
                         ) -> list[FaultModel]:
    """The repo's 8x6 link / node / quadrant scenarios, reused verbatim."""
    import pg_faults_8x6 as PF
    out: list[FaultModel] = []
    for sc in PF.all_scenarios(topo.mx, topo.my):
        out.append(FaultModel(
            sc["name"],
            dead_nodes=frozenset(sc["dead_nodes"]),
            dead_links=frozenset(tuple(e) for e in sc["dead_links"]),
            bypass=bypass, fault_class=sc["fault_class"],
            desc=sc["desc"]))
    return out


def clean_cover(topo: RingTopology, ring: RingId, src: int,
                members: Sequence[int], bad: frozenset[Edge], *,
                bidir: bool = True
                ) -> list[tuple[int, tuple[int, ...]]] | None:
    """Arc cover that avoids dead segments, re-split per member.

    The healthy cover picks a direction per member by distance; under a fault
    the choice has to be made per member by REACHABILITY first and distance
    second. Flipping a whole group instead is what makes a broken wrap look
    fatal when it is not: on a 6-node ring the member exactly halfway round
    goes clockwise on a tie, straight over the wrap, and dragging the rest of
    its group the other way then needs the wrap from the far side. Re-splitting
    moves that one member and leaves the group alone.

    Returns None when some member is unreachable in either direction.
    """
    k = topo.ring_size(ring)
    i = topo.index_on(ring, src)
    choice: dict[int, list[tuple[int, int]]] = {1: [], -1: []}
    for n in members:
        if n == src:
            continue
        opts: list[tuple[int, int]] = []
        for d in ((1, -1) if bidir else (1,)):
            j = topo.index_on(ring, n)
            dist = (j - i) % k if d > 0 else (i - j) % k
            arc = topo.make_arc(ring, src, n, d)
            if not any(e in bad for e in arc.links()):
                opts.append((dist, d))
        if not opts:
            return None
        opts.sort()
        choice[opts[0][1]].append((opts[0][0], n))
    out: list[tuple[int, tuple[int, ...]]] = []
    for d in (1, -1):
        if not choice[d]:
            continue
        choice[d].sort()
        out.append((d, tuple(n for _, n in choice[d])))
    return out


def _fault_aware_collective(topo: RingTopology, pattern: str, algo: str,
                           tier: str, m: int, fm: FaultModel,
                           root: int | None) -> RingCollective | None:
    """Rebuild the collective over the survivors, avoiding dead segments.

    Arc covers are re-chosen per member: take the direction whose arc is clean,
    preferring the short one. Two-phase unicast is left to the route planner,
    which is filtered afterwards. A rotation collective is rebuilt only if its
    Hamiltonian cycle survives; when it does not, the caller reports that as
    "needs a different algorithm", which is the honest answer -- there is no
    way to rotate around a hole.
    """
    alive = set(fm.alive(topo))
    bad = fm.forbidden_links(topo)
    if root is not None and root not in alive:
        return None
    if algo == "ring_rotate":
        cyc = hamilton_cycle(topo)
        for i in range(len(cyc)):
            a, b = cyc[i], cyc[(i + 1) % len(cyc)]
            if a not in alive or b not in alive or (a, b) in bad:
                return None
    col = build_ring_collective(topo, pattern, m=m, tier=tier, algo=algo,
                                root=root)
    keep_phases: list[Phase] = []
    for ph in col.phases:
        xf: list[Xfer] = []
        for x in ph.xfers:
            if x.src not in alive:
                continue
            dsts = tuple(d for d in x.dsts if d in alive)
            if not dsts:
                continue
            if x.ring is None:
                xf.append(Xfer(x.xid, x.src, dsts, x.items, x.nflit, x.op))
                continue
            cover = clean_cover(topo, x.ring, x.src, dsts, bad)
            if cover is None:
                return None
            for sub, (direction, members) in enumerate(cover):
                xf.append(Xfer(x.xid + 100000 * sub, x.src, members, x.items,
                               x.nflit, x.op, x.ring, direction))
        keep_phases.append(Phase(ph.name, xf, ph.barrier, ph.note))
    initial = {k: v for k, v in col.initial.items() if k in alive}
    goal = {k: frozenset(i for i in v if i in _item_owners(col, alive))
            for k, v in col.goal.items() if k in alive}
    return RingCollective(col.pattern, col.tier, col.algo, m, col.n,
                          keep_phases, initial, goal, col.root, col.notes)


def _item_owners(col: RingCollective, alive: set[int]) -> set[int]:
    owned: set[int] = set()
    for s in alive:
        owned |= col.initial.get(s, frozenset())
    return owned


def fault_sweep(topo: RingTopology, pattern: str, algo: str, tier: str, m: int,
                *, root: int | None = None, fill: str = "arc_desc",
                scenarios: Sequence[FaultModel] | None = None
                ) -> dict[str, Any]:
    """Per scenario: is the healthy calendar still legal, and what does a
    recompile cost?

    Three outcomes are distinguished because they need different hardware:
      immune      the healthy calendar never touches the faulty resource
      recompile   a new calendar exists; report the inflation
      infeasible  no calendar exists for this algorithm under this fault
    """
    base_col = build_ring_collective(topo, pattern, m=m, tier=tier, algo=algo,
                                     root=root)
    base = build_calendar(topo, base_col, fill=fill)
    scen = list(scenarios if scenarios is not None
                else wrap_link_scenarios(topo)
                + scattered_node_scenarios(topo)
                + repo_fault_scenarios(topo))
    rows: list[dict[str, Any]] = []
    for fm in scen:
        bad = fm.forbidden_links(topo)
        touched_link = any(e in bad for fp in base.fps.values()
                           for e, _ in fp.links)
        touched_node = any(fp.src in fm.dead_nodes
                           or any(d in fm.dead_nodes for d in fp.arrivals)
                           for fp in base.fps.values())
        immune = not touched_link and not touched_node
        row: dict[str, Any] = {
            "scenario": fm.name, "fault_class": fm.fault_class,
            "bypass": fm.bypass,
            "n_dead_nodes": len(fm.dead_nodes),
            "n_dead_links": len(fm.dead_links),
            "n_forbidden_directed": len(bad),
            "recompile_free": immune,
            "healthy_makespan": base.makespan,
        }
        if immune:
            row["outcome"] = "immune"
            row["makespan"] = base.makespan
            row["inflation"] = 1.0
            rows.append(row)
            continue
        col = _fault_aware_collective(topo, pattern, algo, tier, m, fm, root)
        if col is None:
            row["outcome"] = "infeasible"
            row["makespan"] = None
            row["inflation"] = None
            rows.append(row)
            continue
        rp = replay(col)
        cal = build_calendar(topo, col, fill=fill, forbidden=bad,
                             dead_nodes=fm.dead_nodes)
        row["outcome"] = "recompile"
        row["makespan"] = cal.makespan
        row["inflation"] = round(cal.makespan / max(1, base.makespan), 3)
        row["delivers_survivor_goal"] = rp["ok"]
        row["n_blocked_transfers"] = len(cal.blocked)
        # A node fault REMOVES work: fewer participants means fewer flits, so
        # an inflation below 1.0 means the array got smaller, not that losing a
        # node made the collective faster. Carrying the work ratio next to the
        # makespan ratio is the only way that reads correctly.
        row["n_survivors"] = len(fm.alive(topo))
        row["flits_vs_healthy"] = round(
            col.n_flits / max(1, base_col.n_flits), 3)
        row["work_normalized_inflation"] = (
            round(row["inflation"] / row["flits_vs_healthy"], 3)
            if row["flits_vs_healthy"] else None)
        rows.append(row)
    n = len(rows)
    ok = [r for r in rows if r["outcome"] != "infeasible"]
    infl = [r["inflation"] for r in ok if r["inflation"] is not None]
    wni = [r["work_normalized_inflation"] for r in ok
           if r.get("work_normalized_inflation") is not None]
    by_class: dict[str, dict[str, int]] = {}
    for r in rows:
        c = by_class.setdefault(r["fault_class"],
                                {"immune": 0, "recompile": 0, "infeasible": 0})
        c[r["outcome"]] += 1
    return {
        "pattern": pattern, "algo": algo, "tier": tier, "m": m,
        "healthy_makespan": base.makespan,
        "healthy_flits": base_col.n_flits,
        "n_scenarios": n,
        "n_immune": sum(1 for r in rows if r["outcome"] == "immune"),
        "n_recompile": sum(1 for r in rows if r["outcome"] == "recompile"),
        "n_infeasible": sum(1 for r in rows if r["outcome"] == "infeasible"),
        "worst_inflation": max(infl) if infl else None,
        "median_inflation": (sorted(infl)[len(infl) // 2] if infl else None),
        "worst_work_normalized_inflation": max(wni) if wni else None,
        "by_fault_class": by_class,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 6. Multi-round pipelining: T1, T_R, II_eff, T_avg
# ---------------------------------------------------------------------------

def multiround(topo: RingTopology, col_of_m, *, rounds: int,
               fill: str = "arc_desc") -> dict[str, Any]:
    """Pack `rounds` back-to-back instances and read off II_eff / T_avg.

    T_avg follows the definition already used for the mesh trees in
    `dse_multiflit_area_makespan`: T_avg = (T1 + T_R)/2 = T1 + (R-1)/2 * II_eff
    with II_eff = (T_R - T1)/(R - 1). T_R is MEASURED from a free multi-round
    pack, not extrapolated from a link-reuse figure, because the second round
    of a ring collective can reuse slack slots the first round left behind.
    """
    base = build_calendar(topo, col_of_m(1), fill=fill)
    if rounds <= 1:
        return {"rounds": 1, "T1": base.makespan, "T_R": base.makespan,
                "II_eff": None, "T_avg": base.makespan}
    col = col_of_m(rounds)
    cal = build_calendar(topo, col, fill=fill)
    t1, tr = base.makespan, cal.makespan
    return {
        "rounds": rounds, "T1": t1, "T_R": tr,
        "II_eff": round((tr - t1) / (rounds - 1), 2),
        "T_avg": round((t1 + tr) / 2, 1),
        "util": cal.utilization(topo),
        "occupancy_lb": cal.bounds["occupancy_lb"],
        "binding_lb": cal.bounds["binding_lb"],
    }


if __name__ == "__main__":
    import json
    import time

    topo = RingTopology()
    for m in (1, 13):
        print(f"\n=== m = {m} flit ===")
        print(f"{'pattern':10} {'algo':17} {'tier':4} {'mk':>7} {'lb':>6} "
              f"{'/lb':>6} {'bind':>9} {'wire%':>6} {'gutil':>7} {'cutil':>7} "
              f"{'slk50':>6} {'cf':>3}")
        for pat, algo, tier in [
                ("broadcast", "flat", "T0"), ("broadcast", "dim_2phase", "T0"),
                ("broadcast", "dim_2phase", "T1"),
                ("allgather", "flat", "T0"),
                ("allgather", "ring_rotate", "T0"),
                ("allgather", "dim_2phase", "T0"),
                ("allgather", "dim_2phase", "T1"),
                ("reduce", "flat", "T0"), ("reduce", "dim_2phase", "T0"),
                ("gather", "flat", "T0"), ("gather", "dim_2phase", "T0"),
                ("allreduce", "dim_2phase", "T1"),
                ("alltoall", "flat", "T0")]:
            t0 = time.perf_counter()
            col = build_ring_collective(topo, pat, m=m, tier=tier, algo=algo)
            cal = build_calendar(topo, col)
            s = cal.summary(topo)
            v = verify_dr(topo, cal.items)
            print(f"{pat:10} {algo:17} {tier:4} {s['makespan']:>7} "
                  f"{s['makespan_lb']:>6} {s['makespan_over_lb']:>6} "
                  f"{s['binding_lb']:>9} {s['wire_bound_frac']:>6} "
                  f"{s['util']['global_util']:>7} "
                  f"{s['util']['critical_arc_util']:>7} "
                  f"{s['slack']['p50']:>6} "
                  f"{int(v['conflict_free']):>3}  {time.perf_counter()-t0:.1f}s")
            assert v["conflict_free"], f"{pat}/{algo}/{tier}: {v['examples']}"
