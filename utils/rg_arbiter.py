#!/usr/bin/env python3
"""Request-grant arbiters and grant allocation for the RG NoC study.

Arbiters:
  CA — centralized at central_arbiter_node(); all requests funnel in
  DA — per-destination (or per-root for sync collectives)

Grant semantics:
  bufferable — admission only: assign a start time so link *rates* stay
               within capacity (token-bucket / simple epoch packing)
  bufferless — cycle-exact slot reservation on every link along the path
               (first-fit); routers must be zero-buffer

Control plane (PRIVATE NoC):
  Request/grant messages travel on a dedicated control NoC that is
  topologically isomorphic to the data plane but owns its own physical
  links — NEVER multiplexed onto data-plane wires. Data flits and control
  messages therefore have zero link-level interference. Contension that
  remains is purely among control messages on the private control fabric
  (plus the CA node's ingress serialization).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Literal

from rg_topo import (
    N, RAMP, RAMP_BW, Topology, central_arbiter_node,
)
from rg_collectives import Collective, Flow, tree_link_schedule

ArbiterKind = Literal["ca", "da"]
DataPlane = Literal["bufferable", "bufferless"]


@dataclass
class Grant:
    flow_id: int
    src: int
    t_grant_arrive: int   # when grant reaches the source
    t_data_start: int     # when source may begin injecting
    # bufferless: reserved intervals per directed edge
    reservations: dict[tuple[int, int], tuple[int, int]] = field(
        default_factory=dict)  # edge -> (start, end_exclusive)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduleResult:
    grants: list[Grant]
    makespan_lb: int          # analytical completion lower bound from grants
    t_all_grants_issued: int
    t_barrier_fire: int | None
    ctrl_stats: dict[str, Any]
    n_requests: int
    aggregate: bool
    arbiter: ArbiterKind
    plane: DataPlane


# ---------------------------------------------------------------------------
# Interval helpers for bufferless reservation
# ---------------------------------------------------------------------------

class BusyMap:
    """Per-key sorted list of half-open [start, end) busy intervals."""

    def __init__(self):
        self._busy: dict[Any, list[tuple[int, int]]] = defaultdict(list)

    def conflicts(self, key: Any, start: int, end: int) -> bool:
        for a, b in self._busy[key]:
            if a < end and start < b:
                return True
        return False

    def reserve(self, key: Any, start: int, end: int) -> None:
        if end <= start:
            return
        lst = self._busy[key]
        lst.append((start, end))
        lst.sort()
        # merge adjacent/overlapping
        merged: list[tuple[int, int]] = []
        for a, b in lst:
            if not merged or a > merged[-1][1]:
                merged.append((a, b))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        self._busy[key] = merged

    def earliest(self, key: Any, duration: int, t0: int = 0) -> int:
        """Earliest start >= t0 with [start, start+duration) free."""
        if duration <= 0:
            return t0
        t = t0
        while True:
            blocked = False
            for a, b in self._busy[key]:
                if a < t + duration and t < b:
                    t = b
                    blocked = True
                    break
            if not blocked:
                return t


# ---------------------------------------------------------------------------
# Bufferless first-fit reservation
# ---------------------------------------------------------------------------

def reserve_unicast(topo: Topology, path: list[int], m: int,
                    busy: BusyMap, t_ready: int,
                    ramp_busy: BusyMap, src: int
                    ) -> tuple[int, dict[tuple[int, int], tuple[int, int]]]:
    """Find earliest t0 >= t_ready such that the rigid wormhole footprint
    fits. Returns (t0, reservations).

    Flit i occupies link e at [t0 + prefix(e) + i*sigma,
                               t0 + prefix(e) + i*sigma + sigma).
    Equivalent occupancy window on link e: [t0+prefix, t0+prefix+m*sigma).
    Also reserves src inject ramp for m flits at rate RAMP_BW.
    """
    sigma = topo.sigma
    # Build (edge, prefix) list
    edges: list[tuple[tuple[int, int], int]] = []
    delay = 0
    for i in range(len(path) - 1):
        edges.append(((path[i], path[i + 1]), delay))
        delay += topo.link_lat(path[i], path[i + 1])
    duration = m * sigma
    # Inject ramp: ceil(m / RAMP_BW) cycles of inject occupancy starting t0
    inj_dur = (m + RAMP_BW - 1) // RAMP_BW

    t0 = t_ready
    # iterate until all edges + ramp free
    for _ in range(1_000_000):
        ok = True
        # check inject
        if ramp_busy.conflicts(("inj", src), t0, t0 + inj_dur):
            t0 = ramp_busy.earliest(("inj", src), inj_dur, t0)
            ok = False
            continue
        cand_t = t0
        for edge, pref in edges:
            start = t0 + pref
            end = start + duration
            if busy.conflicts(edge, start, end):
                # push t0 so this edge clears
                # earliest start on this edge, then back out prefix
                e_start = busy.earliest(edge, duration, start)
                cand_t = max(cand_t, e_start - pref)
                ok = False
        if ok:
            break
        if cand_t <= t0:
            t0 += 1
        else:
            t0 = cand_t
    else:
        raise RuntimeError("reservation search exhausted")

    res: dict[tuple[int, int], tuple[int, int]] = {}
    for edge, pref in edges:
        start = t0 + pref
        end = start + duration
        busy.reserve(edge, start, end)
        res[edge] = (start, end)
    ramp_busy.reserve(("inj", src), t0, t0 + inj_dur)
    # also reserve eject at destination for arrival window
    if len(path) >= 1:
        dst = path[-1]
        arrive0 = t0 + (0 if len(path) < 2 else
                        sum(topo.link_lat(path[i], path[i + 1])
                            for i in range(len(path) - 1)))
        # eject m flits at RAMP_BW
        eject_dur = (m + RAMP_BW - 1) // RAMP_BW
        # find free eject slot (may push — but for LB we just reserve at arrive)
        ej_t = ramp_busy.earliest(("ej", dst), eject_dur, arrive0)
        # if eject pushes, we don't re-solve the whole path (approximation);
        # DES will handle real eject contention for bufferable; for bufferless
        # we accept ej_t and record it
        ramp_busy.reserve(("ej", dst), ej_t, ej_t + eject_dur)
    return t0, res


def reserve_tree(topo: Topology, flow: Flow, busy: BusyMap, t_ready: int,
                 ramp_busy: BusyMap
                 ) -> tuple[int, dict[tuple[int, int], tuple[int, int]]]:
    """Reserve a multicast tree as a rigid footprint from a single inject."""
    sigma = topo.sigma
    schedule = tree_link_schedule(topo, flow)
    duration = flow.m * sigma
    inj_dur = (flow.m + RAMP_BW - 1) // RAMP_BW
    t0 = t_ready
    for _ in range(1_000_000):
        ok = True
        if ramp_busy.conflicts(("inj", flow.src), t0, t0 + inj_dur):
            t0 = ramp_busy.earliest(("inj", flow.src), inj_dur, t0)
            ok = False
            continue
        cand_t = t0
        for edge, pref in schedule:
            start = t0 + pref
            end = start + duration
            if busy.conflicts(edge, start, end):
                e_start = busy.earliest(edge, duration, start)
                cand_t = max(cand_t, e_start - pref)
                ok = False
        if ok:
            break
        if cand_t <= t0:
            t0 += 1
        else:
            t0 = cand_t
    else:
        raise RuntimeError("tree reservation search exhausted")

    res: dict[tuple[int, int], tuple[int, int]] = {}
    for edge, pref in schedule:
        start = t0 + pref
        end = start + duration
        busy.reserve(edge, start, end)
        res[edge] = (start, end)
    ramp_busy.reserve(("inj", flow.src), t0, t0 + inj_dur)
    # eject at every destination
    for d, path in flow.paths.items():
        arrive0 = t0 + topo.path_wire_delay(path)
        eject_dur = (flow.m + RAMP_BW - 1) // RAMP_BW
        ej_t = ramp_busy.earliest(("ej", d), eject_dur, arrive0)
        ramp_busy.reserve(("ej", d), ej_t, ej_t + eject_dur)
    return t0, res


# ---------------------------------------------------------------------------
# Bufferable admission (rate / epoch packing)
# ---------------------------------------------------------------------------

def admit_bufferable_unicast(topo: Topology, path: list[int], m: int,
                             load: dict[tuple[int, int], int],
                             t_ready: int, src_next: dict[int, int],
                             src: int) -> int:
    """Assign start time: source inject serialization + link load accounting.

    We don't do cycle-exact reservation; we ensure each link's cumulative
    granted flits stay consistent with a makespan horizon. Practical rule:
      t0 = max(t_ready, src_next[src])
      src_next[src] = t0 + ceil(m / RAMP_BW)
      accumulate load[e] += m for each edge
    The actual contention is resolved by the bufferable DES.
    """
    inj_dur = (m + RAMP_BW - 1) // RAMP_BW
    t0 = max(t_ready, src_next.get(src, 0))
    src_next[src] = t0 + inj_dur
    for i in range(len(path) - 1):
        e = (path[i], path[i + 1])
        load[e] = load.get(e, 0) + m
    return t0


# ---------------------------------------------------------------------------
# Control-plane DES
# ---------------------------------------------------------------------------

@dataclass
class CtrlMsg:
    kind: str          # "request" | "grant"
    src: int
    dst: int
    flow_id: int
    path: list[int]
    hop: int
    inject_t: int
    meta: dict = field(default_factory=dict)


# Private control NoC: 1 control message / cycle / directed control link.
# Independent of data-plane sigma (torus data σ=2 does NOT apply here —
# control metal is a separate budget from the mesh/torus data metal-constant).
CTRL_MSGS_PER_LINK_CY = 1


def simulate_control_plane(
    topo: Topology,
    requests: list[tuple[int, int, int]],  # (src, dst_arb, flow_id)
    grants_plan: list[tuple[int, int, int, int]],
    # (arb, src, flow_id, issue_t_local) — issue_t_local relative to
    # arbiter having received the triggering request(s)
    t_sched: int = 1,
    sync_barrier: bool = False,
    n_barrier_requests: int = 0,
) -> dict[str, Any]:
    """Cycle-accurate PRIVATE control NoC DES.

    Physical isolation: control links are a separate resource from the data
    NoC (``shared_with_data_plane=False``). Only control-vs-control contention
    exists. Hop latency is **half** the data-plane link-delay Manhattan
    (⌊H/2⌋ / ⌊V/2⌋ with last-hop correction so path total =
    ⌊wire_distance/2⌋). Occupancy is always ``CTRL_MSGS_PER_LINK_CY``.
    """
    # Link pipelines: arrive[t] -> list of CtrlMsg arriving at a node
    arrive: dict[int, list[CtrlMsg]] = defaultdict(list)
    # Per directed CONTROL edge: busy until (exclusive) — NOT data links
    link_free: dict[tuple[int, int], int] = defaultdict(int)

    # Arbiter inbox queues (CA: one node; DA: many)
    inbox: dict[int, deque] = defaultdict(deque)  # arb_node -> msgs
    received_req: dict[int, int] = {}  # flow_id -> t_arrive_at_arb
    grant_arrive: dict[int, int] = {}  # flow_id -> t_arrive_at_src
    req_arrive_src_send: dict[int, int] = {}

    # Schedule initial requests at t=0 from each src
    pending_inject: list[CtrlMsg] = []
    for src, dst, fid in requests:
        # Control plane uses XY (dimension-order) routing, same as data plane
        path = topo.dor_path(src, dst) if src != dst else [src]
        pending_inject.append(CtrlMsg(
            "request", src, dst, fid, path, 0, 0,
            meta={"hop_lats": topo.ctrl_path_hop_lats(path)}))
        req_arrive_src_send[fid] = 0

    # Grant issue plan built dynamically for sync; for async we issue after
    # each request + t_sched
    grant_issue_q: list[tuple[int, int, int, int]] = []  # (t, arb, src, fid)
    # Pre-seeded grants_plan used when provided (absolute times)
    for arb, src, fid, t_iss in grants_plan:
        grant_issue_q.append((t_iss, arb, src, fid))
    grant_issue_q.sort()

    barrier_count = 0
    barrier_fired = False
    t_barrier = None
    n_req_needed = n_barrier_requests if sync_barrier else 0

    # Track arbiter ingress: msgs delivered to arb per cycle
    ingress_hist: dict[int, int] = defaultdict(int)

    T_MAX = 200_000
    t = 0
    inflight = 0
    outstanding_grants = set()

    def try_send(msg: CtrlMsg, t_now: int) -> bool:
        nonlocal inflight
        if msg.hop >= len(msg.path) - 1:
            # arrived
            if msg.kind == "request":
                inbox[msg.dst].append((t_now, msg))
                received_req[msg.flow_id] = t_now
                ingress_hist[t_now] += 1
            else:
                grant_arrive[msg.flow_id] = t_now
            return True
        u = msg.path[msg.hop]
        v = msg.path[msg.hop + 1]
        if link_free[(u, v)] > t_now:
            return False
        lats = msg.meta.get("hop_lats") or topo.ctrl_path_hop_lats(msg.path)
        lat = lats[msg.hop] if msg.hop < len(lats) else topo.ctrl_link_lat(u, v)
        # Private control link occupancy (independent of data-plane sigma)
        link_free[(u, v)] = t_now + CTRL_MSGS_PER_LINK_CY
        nxt = CtrlMsg(msg.kind, msg.src, msg.dst, msg.flow_id, msg.path,
                      msg.hop + 1, msg.inject_t, msg.meta)
        arrive[t_now + lat].append(nxt)
        inflight += 1
        return True

    # msgs waiting at a node for an free outbound link
    waiting: dict[int, deque] = defaultdict(deque)

    while t <= T_MAX:
        # arrivals
        for msg in arrive.pop(t, ()):
            inflight -= 1
            if msg.hop >= len(msg.path) - 1:
                if msg.kind == "request":
                    inbox[msg.dst].append((t, msg))
                    received_req[msg.flow_id] = t
                    ingress_hist[t] += 1
                else:
                    grant_arrive[msg.flow_id] = t
            else:
                waiting[msg.path[msg.hop]].append(msg)

        # inject new requests
        still = []
        for msg in pending_inject:
            if msg.src == msg.dst:
                # local
                inbox[msg.dst].append((t, msg))
                received_req[msg.flow_id] = t
                ingress_hist[t] += 1
            else:
                waiting[msg.src].append(msg)
        pending_inject = still

        # Arbiter processing
        if sync_barrier and not barrier_fired:
            # count unique flow requests received across all arbs (CA: one)
            barrier_count = len(received_req)
            if barrier_count >= n_req_needed:
                barrier_fired = True
                t_barrier = t + t_sched
                # issue grants to all sources at t_barrier
                # For allgather sync: one grant per flow (per source tree)
                for fid, src_dst in [(fid, requests[i][0])
                                     for i, (s, d, fid) in enumerate(requests)]:
                    # find arb = request dst
                    arb = requests[[r[2] for r in requests].index(fid)][1]
                    src = requests[[r[2] for r in requests].index(fid)][0]
                    grant_issue_q.append((t_barrier, arb, src, fid))
                grant_issue_q.sort()
        elif not sync_barrier:
            # async: for each newly received request, schedule grant after t_sched
            for arb_node, q in list(inbox.items()):
                while q:
                    t_arr, msg = q.popleft()
                    # only process once
                    issue_t = t_arr + t_sched
                    if msg.flow_id not in outstanding_grants:
                        outstanding_grants.add(msg.flow_id)
                        grant_issue_q.append(
                            (issue_t, arb_node, msg.src, msg.flow_id))
                grant_issue_q.sort()

        # Issue grants whose time has come
        while grant_issue_q and grant_issue_q[0][0] <= t:
            issue_t, arb, src, fid = grant_issue_q.pop(0)
            if src == arb:
                grant_arrive[fid] = t
            else:
                path = topo.dor_path(arb, src)
                waiting[arb].append(CtrlMsg(
                    "grant", arb, src, fid, path, 0, t,
                    meta={"hop_lats": topo.ctrl_path_hop_lats(path)}))

        # Advance waiting msgs (one per out-edge per cycle, oldest first)
        for node in list(waiting.keys()):
            q = waiting[node]
            if not q:
                continue
            # group by out neighbor; send at most 1 per out edge
            sent_edges: set[tuple[int, int]] = set()
            remain = deque()
            while q:
                msg = q.popleft()
                if msg.hop >= len(msg.path) - 1:
                    continue
                u = msg.path[msg.hop]
                v = msg.path[msg.hop + 1]
                e = (u, v)
                if e in sent_edges or link_free[e] > t:
                    remain.append(msg)
                    continue
                if try_send(msg, t):
                    sent_edges.add(e)
                else:
                    remain.append(msg)
            waiting[node] = remain

        # termination: all grants arrived
        if (len(grant_arrive) >= len(requests)
                and inflight == 0
                and not any(waiting.values())
                and not grant_issue_q
                and not pending_inject):
            break
        t += 1

    max_ingress = max(ingress_hist.values()) if ingress_hist else 0
    return {
        "request_arrive": received_req,
        "grant_arrive": grant_arrive,
        "t_barrier": t_barrier,
        "t_done": t,
        "n_requests": len(requests),
        "n_grants": len(grant_arrive),
        "max_ingress_per_cy": max_ingress,
        "ingress_total": sum(ingress_hist.values()),
        "t_last_request": max(received_req.values()) if received_req else 0,
        "t_first_grant": min(grant_arrive.values()) if grant_arrive else None,
        "t_last_grant": max(grant_arrive.values()) if grant_arrive else None,
        # Explicit isolation contract
        "shared_with_data_plane": False,
        "control_noc": "private_isomorphic",
        "ctrl_msgs_per_link_cy": CTRL_MSGS_PER_LINK_CY,
        "ctrl_delay_policy": "half_manhattan_linkdelay",
        "note": ("request/grant on private control NoC; zero physical-link "
                 "sharing with data plane; one-way latency = ⌊Manhattan "
                 "wire delay / 2⌋; remaining contention is "
                 "control-vs-control + CA ingress only"),
    }


# ---------------------------------------------------------------------------
# Top-level scheduler
# ---------------------------------------------------------------------------

def _request_targets(col: Collective, arbiter: ArbiterKind,
                     aggregate: bool
                     ) -> list[tuple[int, int, int]]:
    """Build (src, arb_dst, flow_id) request list."""
    ca = central_arbiter_node()
    reqs: list[tuple[int, int, int]] = []
    if arbiter == "ca":
        if aggregate and col.pattern == "alltoall":
            # one request per source
            for s in range(col.n):
                reqs.append((s, ca, s))  # flow_id = src id for aggregate
        elif col.mode == "sync_barrier":
            for s in range(col.n):
                reqs.append((s, ca, s))
        else:
            for f in col.flows:
                reqs.append((f.src, ca, f.flow_id))
    else:
        # DA
        if col.pattern == "broadcast":
            reqs.append((col.root, col.root, 0))
        elif col.pattern in ("allgather",) and col.mode == "sync_barrier":
            # sync DA: designate root as aggregator
            root = col.root if col.root >= 0 else 0
            for s in range(col.n):
                reqs.append((s, root, s))
        elif col.pattern == "allreduce":
            root = col.root
            for s in range(col.n):
                reqs.append((s, root, s))
        elif col.pattern == "alltoall":
            if aggregate:
                for s in range(col.n):
                    # still need per-dest for DA; approximate: request to self
                    # then fan-out — model as per-dest for fidelity
                    for f in col.flows:
                        if f.src == s:
                            reqs.append((s, f.dsts[0], f.flow_id))
            else:
                for f in col.flows:
                    reqs.append((f.src, f.dsts[0], f.flow_id))
        elif col.pattern == "reduce":
            for f in col.flows:
                reqs.append((f.src, f.dsts[0], f.flow_id))
        elif col.pattern == "allgather" and col.mode == "async_tree":
            # each tree requests at CA-equivalent: use src itself as local
            # arbiter that coordinates with a logical fabric — for DA async
            # trees we send request to a designated root for conflict-free
            # allocation
            root = 0
            for f in col.flows:
                reqs.append((f.src, root, f.flow_id))
        else:
            for f in col.flows:
                dst = f.dsts[0] if f.dsts else f.src
                reqs.append((f.src, dst, f.flow_id))
    return reqs


def schedule(topo: Topology, col: Collective, *,
             arbiter: ArbiterKind = "ca",
             plane: DataPlane = "bufferable",
             t_sched: int = 1,
             aggregate: bool = False,
             w_out: int = 10**9,
             ) -> ScheduleResult:
    """Run control-plane DES then allocate data-plane grants."""
    sync = col.mode == "sync_barrier"
    reqs = _request_targets(col, arbiter, aggregate)

    # For aggregate alltoall under CA, map src-level grant to all its flows
    aggregate_map: dict[int, list[int]] = defaultdict(list)
    if aggregate and col.pattern == "alltoall" and arbiter == "ca":
        for f in col.flows:
            aggregate_map[f.src].append(f.flow_id)
        # requests already one-per-src with flow_id=src

    ctrl = simulate_control_plane(
        topo, reqs, grants_plan=[], t_sched=t_sched,
        sync_barrier=sync,
        n_barrier_requests=len(reqs) if sync else 0,
    )

    # Map flow_id -> grant arrive time
    # For aggregate: grant_arrive keyed by src id
    grant_time: dict[int, int] = {}
    if aggregate and col.pattern == "alltoall" and arbiter == "ca":
        for src, tga in ctrl["grant_arrive"].items():
            for fid in aggregate_map[src]:
                grant_time[fid] = tga
    else:
        grant_time = dict(ctrl["grant_arrive"])

    # Fill any missing (local / edge cases)
    for f in col.flows:
        if f.flow_id not in grant_time:
            # fallback: use t_last_grant or 0
            grant_time[f.flow_id] = ctrl.get("t_last_grant") or 0

    # Data-plane allocation
    grants: list[Grant] = []
    busy = BusyMap()
    ramp_busy = BusyMap()
    load: dict[tuple[int, int], int] = {}
    src_next: dict[int, int] = {}
    # outstanding window: per-src limit on concurrent granted-not-done flows
    # Approximated by serializing starts when w_out is small via src_next
    # and a per-src active count with estimated duration.
    src_active_end: dict[int, list[int]] = defaultdict(list)

    # Order flows by grant arrival then flow_id
    ordered = sorted(col.flows, key=lambda f: (grant_time[f.flow_id], f.flow_id))

    makespan_lb = 0
    for f in ordered:
        t_g = grant_time[f.flow_id]
        # enforce W_out: wait until fewer than w_out prior flows from src active
        t_ready = t_g
        ends = src_active_end[f.src]
        if len(ends) >= w_out:
            ends.sort()
            # wait until the earliest active finishes enough slots
            need_free = len(ends) - w_out + 1
            t_ready = max(t_ready, ends[need_free - 1])

        if plane == "bufferless":
            if f.kind == "tree":
                t0, res = reserve_tree(topo, f, busy, t_ready, ramp_busy)
            else:
                path = f.paths[f.dsts[0]]
                t0, res = reserve_unicast(topo, path, f.m, busy, t_ready,
                                          ramp_busy, f.src)
            # completion estimate
            if f.kind == "tree":
                done = max(
                    (t0 + topo.path_wire_delay(p) + (f.m - 1) * topo.sigma
                     + RAMP)
                    for p in f.paths.values()) if f.paths else t0 + RAMP
            else:
                p = f.paths[f.dsts[0]]
                done = (t0 + topo.path_wire_delay(p)
                        + (f.m - 1) * topo.sigma + RAMP)
            grants.append(Grant(f.flow_id, f.src, t_g, t0, res))
        else:
            # bufferable
            if f.kind == "tree":
                # admit by injecting m flits once; tree fanout handled in DES
                # Use src->farthest path for load accounting of tree edges
                t0 = admit_bufferable_unicast(
                    topo, [f.src], f.m, load, t_ready, src_next, f.src)
                # also account tree edge loads
                for e, _pref in tree_link_schedule(topo, f):
                    load[e] = load.get(e, 0) + f.m
                done = t0 + max((topo.path_wire_delay(p)
                                 for p in f.paths.values()), default=0) \
                    + (f.m - 1) * topo.sigma + 2 * RAMP
            else:
                path = f.paths[f.dsts[0]]
                t0 = admit_bufferable_unicast(
                    topo, path, f.m, load, t_ready, src_next, f.src)
                done = (t0 + topo.path_wire_delay(path)
                        + (f.m - 1) * topo.sigma + 2 * RAMP)
            grants.append(Grant(f.flow_id, f.src, t_g, t0, {}))

        src_active_end[f.src].append(done)
        makespan_lb = max(makespan_lb, done)

    return ScheduleResult(
        grants=grants,
        makespan_lb=makespan_lb,
        t_all_grants_issued=ctrl.get("t_last_grant") or 0,
        t_barrier_fire=ctrl.get("t_barrier"),
        ctrl_stats=ctrl,
        n_requests=len(reqs),
        aggregate=aggregate,
        arbiter=arbiter,
        plane=plane,
    )


if __name__ == "__main__":
    import json
    from rg_collectives import build_collective

    topo = Topology("mesh")
    for pat in ("broadcast", "alltoall", "allgather"):
        sync = pat in ("allgather", "allreduce")
        col = build_collective(topo, pat, m=1, sync=sync)
        for plane in ("bufferable", "bufferless"):
            for arb in ("ca", "da"):
                agg = (pat == "alltoall" and arb == "ca")
                # for smoke: aggregate alltoall to keep runtime small
                if pat == "alltoall" and not agg:
                    continue
                sr = schedule(topo, col, arbiter=arb, plane=plane,
                              t_sched=1, aggregate=agg, w_out=4)
                print(f"{pat:10} {plane:12} {arb} agg={agg} "
                      f"n_req={sr.n_requests} mk_lb={sr.makespan_lb} "
                      f"last_g={sr.t_all_grants_issued} "
                      f"barr={sr.t_barrier_fire} "
                      f"t_last_req={sr.ctrl_stats['t_last_request']}")
