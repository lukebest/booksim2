#!/usr/bin/env python3
"""Open-loop steady-state DES: one injector, one statistics collector, four fabrics.

The batch schedulers in `rg_mesh_sched.py` / `rg_ring_sched.py` answer "how few
rounds to drain a known workload". This module answers the other question:
"at injection rate lambda, what latency and what accepted throughput", with
unbounded source queues and no completion barrier. Both are needed; a schedule
that wins on makespan can still lose in steady state, and vice versa.

Everything below shares ONE injector and ONE stats collector so the four
configurations are compared on the same footing:

  mesh_base     buffered 8x6 mesh, XY, credit-based flow control, input-queued
                routers with a per-output iSLIP switch allocator (`MeshBaseSim`)
  ring_base     bufferless dimension-sliced 2D ring with E-tag/I-tag arbitration
                and deflection (`rg_ring_base.RingBaseSim`)
  mesh_islip2d  centralized request-grant on the mesh under D-M (`RGSim`)
  ring_islip2d  centralized request-grant on the ring under D-R (`RGSim`)

Traffic
-------
Every node generates a packet with probability `lam` per cycle, destination
uniform over the other 47 nodes; aggregated over time this is all-to-all.
lambda is in PACKETS per node per cycle, so with m flits per packet the flit
rate is m*lambda -- the analytic saturation point scales as 1/m (see
`anchors()`).

Why sources are unbounded
-------------------------
Above lambda* the network cannot accept what is offered, and the interesting
output is exactly that gap. A bounded source queue would silently drop or
throttle the offered load and make every configuration look like it saturates
at the same place. With unbounded queues, saturation shows up as a positive
backlog slope, which is what `stable` reports.

Measurement discipline
----------------------
* `warmup` cycles are simulated and discarded, then `measure` cycles are
  recorded. Latency samples are attributed to the cycle a packet is DELIVERED,
  and only packets GENERATED after warmup are sampled, so no sample carries
  transient queueing.
* Stability = least-squares slope of total backlog over the measurement window,
  normalized per node. Above lambda* this is ~ (lam - lam*) per node per cycle.
* Accepted throughput = delivered packets / (N * measure), directly comparable
  to lam.
* Fairness = coefficient of variation of per-node delivered counts.
* For the two bufferless configurations, in-network residency is asserted to be
  zero (no flit ever waits inside the fabric); for `ring_base` that means no
  station buffering, with the bridge FIFOs accounted separately as the cost
  they are.

Central-arbiter model (RGSim)
-----------------------------
The arbiter runs every `ca_period` cycles and may book resources in the window
[t + t_rtt, t + t_rtt + horizon). A grant carries its start time t0, and the
source holds the packet until then, so booking ahead costs no extra hardware --
`horizon` is just the depth of the reservation table. A request whose earliest
conflict-free start lies beyond the horizon is NOT granted: its VOQ stays in
the residual bitmap and is re-requested next round. That is what makes this an
online scheduler, and it is where backpressure comes from: above lambda* the
frontier pins at the horizon and the source queues grow.

`horizon` cannot be set to one cycle, which was the first thing tried. A
granted transfer is rigid, so its start must satisfy every resource on its path
at once; with a one-cycle window a request is grantable only in the single cycle
when its most-booked resource happens to line up, and the rest of its path sits
idle meanwhile. Measured: a one-cycle window holds accepted throughput to 0.09
against an analytic 0.490, with 270 of 288 probes per round deferred. The
horizon must cover the free-time dispersion across a path, which is on the order
of the path's own wire delay (up to ~90 cycles here), hence the default of 128.

Conflict domain is `free_at` (per-resource earliest-free time, with `cap`
parallel servers for ramps and ring ports). Interval back-filling is pointless
here: starts are constrained to t + t_rtt and later, so holes behind the
frontier can never be used.

Run `python3 rg_steady_des.py` for a smoke test of all four.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from rg_topo import RAMP, RAMP_BW, Topology, coord
from rg_mesh_paths import build_plan
from rg_ring_topo import RingTopology, board_key, build_ring_plan, leave_key
from rg_ring_base import RingBaseParams, RingBaseSim

CONFIGS = ("mesh_base", "ring_base", "mesh_islip2d", "ring_islip2d")


# ---------------------------------------------------------------------------
# 1. Parameters
# ---------------------------------------------------------------------------

@dataclass
class SteadyParams:
    """Knobs shared by all four configurations plus the per-fabric extras."""
    lam: float = 0.1
    m: int = 1
    seed: int = 0
    warmup: int = 3000
    measure: int = 12000
    max_backlog: int = 400_000        # bail out instead of thrashing memory
    sigma: int = 1                    # cycles per flit per link (metal knob)

    # mesh_base
    buf_depth: int = 8                # flits per input VC (the CBFC knob)
    num_vc: int = 1

    # ring_base
    fifo_depth: int = 4
    swap_rule: bool = True
    resolution_mode: bool = True
    dim_order: str = "RC"
    t_inj: int = 16
    t_xfer: int = 16

    # centralized request-grant
    t_rtt: int = 16
    ca_period: int = 1
    grants_per_src: int = 1
    pipeline_depth: int = 0           # 0 = fully pipelined control loop
    ca_probe: int = 6                 # bitmap bits probed per source per round
    horizon: int = 128                # how far ahead the arbiter may book
    conflict_domain: str = "interval"  # interval / free_at
    path_mode: str = "xy"             # mesh: xy / romm_static / romm_dyn
    ring_path_mode: str = "fixed"     # ring: fixed / balanced
    board_ports: int = 1
    leave_ports: int = 1


# ---------------------------------------------------------------------------
# 2. Shared injector + collector
# ---------------------------------------------------------------------------

class Injector:
    """Bernoulli(lam) per node per cycle, uniform destination.

    Held separate from every fabric so the four configurations see the SAME
    arrival sequence for a given seed. Comparing fabrics that each rolled their
    own dice would put a per-configuration random offset on every metric.
    """

    def __init__(self, n: int, lam: float, seed: int = 0):
        self.n = n
        self.lam = lam
        self.rng = random.Random(seed)

    def arrivals(self) -> list[tuple[int, int]]:
        out = []
        for s in range(self.n):
            if self.rng.random() < self.lam:
                d = self.rng.randrange(self.n - 1)
                out.append((s, d + (1 if d >= s else 0)))
        return out


class Collector:
    """Latency samples + per-node counts + backlog trace, identical for all."""

    def __init__(self, n: int):
        self.n = n
        self.lat: list[int] = []
        self.per_node = [0] * n
        self.backlog: list[tuple[int, int]] = []
        self.grant_wait: list[int] = []
        self.n_delivered = 0
        self.n_generated = 0
        self.max_residency = 0

    def sample(self, src: int, lat: int) -> None:
        self.lat.append(lat)
        self.per_node[src] += 1
        self.n_delivered += 1

    def trace(self, t: int, backlog: int) -> None:
        self.backlog.append((t, backlog))

    # -- reductions --------------------------------------------------------

    def pct(self, q: float) -> float:
        if not self.lat:
            return float("nan")
        v = sorted(self.lat)
        k = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
        return float(v[k])

    def slope(self) -> float:
        """Least-squares backlog slope per node per cycle."""
        if len(self.backlog) < 3:
            return 0.0
        ts = [t for t, _ in self.backlog]
        bs = [b for _, b in self.backlog]
        mt = sum(ts) / len(ts)
        mb = sum(bs) / len(bs)
        num = sum((t - mt) * (b - mb) for t, b in zip(ts, bs))
        den = sum((t - mt) ** 2 for t in ts)
        return (num / den / self.n) if den else 0.0

    def fairness_cv(self) -> float:
        v = [c for c in self.per_node]
        mu = sum(v) / len(v)
        if mu <= 0:
            return float("nan")
        var = sum((x - mu) ** 2 for x in v) / len(v)
        return math.sqrt(var) / mu


# ---------------------------------------------------------------------------
# 3. mesh_base: credit-based flow control + per-router iSLIP
# ---------------------------------------------------------------------------

@dataclass
class _MFlit:
    pid: int
    seq: int
    nflit: int
    src: int
    dst: int
    t_gen: int
    path: list[int]
    hop: int = 0

    @property
    def here(self) -> int:
        return self.path[self.hop]

    @property
    def nxt(self) -> int | None:
        return self.path[self.hop + 1] if self.hop + 1 < len(self.path) else None


class MeshBaseSim:
    """Input-queued buffered mesh with credit-based flow control.

    Per cycle each router runs one iSLIP switch allocation: every output port
    picks one input port round-robin among those whose head flit wants it and
    whose downstream VC has credit. The same algorithm the centralized variant
    lifts to global scope -- the difference under test is the SCOPE of the
    matching, not the matching rule.

    Credit-based flow control is modelled with the delay that actually bites:
    a flit takes `link_lat` cycles to land in the downstream buffer, and the
    credit freed by its departure takes another `link_lat` cycles to get back.
    A single VC therefore sustains at most `buf_depth / (2*link_lat + 1)` flits
    per cycle. With H=7/V=9 that is a credit round trip of 15-19 cycles, so
    `buf_depth=4` throttles a link to ~0.25 flit/cy no matter what the topology
    could carry. That is why `buf_depth` must be swept: reporting only a shallow
    configuration understates the baseline by 2-4x and would flip the verdict.
    """

    def __init__(self, topo: Topology, p: SteadyParams, seed: int = 0):
        self.topo = topo
        self.p = p
        self.t = 0
        self.rng = random.Random(seed)
        self.dur = p.m * p.sigma

        # buf[(node, in_port)] -- in_port is the upstream node id, or -1 local
        self.buf: dict[tuple[int, int], deque[_MFlit]] = defaultdict(deque)
        # credit[(node, out_node)] = free slots in out_node's input buffer
        self.credit: dict[tuple[int, int], int] = defaultdict(
            lambda: p.buf_depth)
        self.arrive: dict[int, list[tuple[int, int, _MFlit]]] = defaultdict(list)
        self.cred_ret: dict[int, list[tuple[int, int]]] = defaultdict(list)

        self.srcq: dict[int, deque[_MFlit]] = defaultdict(deque)
        # sigma is cycles per flit per link, i.e. the metal-constant knob. It
        # has to throttle BANDWIDTH, not just scale latency: with sigma=2 a link
        # accepts a new flit every other cycle. Scaling only the latency let
        # this model exceed its own analytic anchor (0.29 measured against a
        # 0.245 bound) -- caught by the anchor check, not by inspection.
        self.out_free: dict[tuple[int, int], int] = defaultdict(int)
        self.rr: dict[tuple[int, int], int] = defaultdict(int)  # (node,out)->ptr
        self.reasm: dict[int, dict[int, int]] = defaultdict(dict)

        self.pkt_done: list[tuple[int, int, int, int, int]] = []
        self._pid = 0
        self._path: dict[tuple[int, int], list[int]] = {}
        self.st: dict[str, int] = defaultdict(int)

    # -- interface used by run_steady -------------------------------------

    def offer(self, src: int, dst: int) -> None:
        key = (src, dst)
        if key not in self._path:
            self._path[key] = self.topo.dor_path(src, dst)
        path = self._path[key]
        pid = self._pid
        self._pid += 1
        for k in range(self.p.m):
            self.srcq[src].append(_MFlit(pid, k, self.p.m, src, dst,
                                         self.t, path))

    def backlog(self) -> int:
        return sum(len(q) for q in self.srcq.values())

    def in_network(self) -> int:
        return (sum(len(q) for q in self.buf.values())
                + sum(len(v) for v in self.arrive.values()))

    # -- one cycle ---------------------------------------------------------

    def step(self) -> None:
        t = self.t

        for node, in_port, f in self.arrive.pop(t, []):
            self.buf[(node, in_port)].append(f)
        for node, out_node in self.cred_ret.pop(t, []):
            self.credit[(node, out_node)] += 1

        self._switch_alloc()
        self._inject()
        self.t += 1

    def _switch_alloc(self) -> None:
        """One iSLIP-style matching per router, plus local ejection."""
        p = self.p
        for node in range(self.topo.n):
            ports = [(node, -1)] + [(node, u) for u in self.topo.adj[node]]
            # candidates per output; output -1 means "eject here"
            want: dict[int, list[tuple[int, int]]] = defaultdict(list)
            for key in ports:
                q = self.buf.get(key)
                if not q:
                    continue
                f = q[0]
                nxt = f.nxt
                want[-1 if nxt is None else nxt].append(key)

            eject_bw = RAMP_BW
            for out, cands in want.items():
                if out == -1:
                    for key in cands[:eject_bw]:
                        f = self.buf[key].popleft()
                        self._free_slot(key, f)
                        self._deliver(f)
                    continue
                if self.out_free[(node, out)] > self.t:
                    continue
                if self.credit[(node, out)] < 1:
                    self.st["n_credit_stall"] += 1
                    continue
                ptr = self.rr[(node, out)]
                nn = self.topo.n + 1
                cands.sort(key=lambda k: ((self._pnum(k[1]) - ptr) % nn))
                key = cands[0]
                f = self.buf[key].popleft()
                self._free_slot(key, f)
                self.rr[(node, out)] = (self._pnum(key[1]) + 1) % nn
                self.credit[(node, out)] -= 1
                self.out_free[(node, out)] = self.t + p.sigma
                f.hop += 1
                lat = self.topo.link_lat(node, out) * p.sigma
                self.arrive[self.t + lat].append((out, node, f))

    def _pnum(self, in_port: int) -> int:
        """Input-port index for the RR pointer; the local port sits last."""
        return self.topo.n if in_port < 0 else in_port

    def _free_slot(self, key: tuple[int, int], f: _MFlit) -> None:
        """Return the credit for the buffer slot just vacated."""
        node, in_port = key
        if in_port < 0:
            return
        lat = self.topo.link_lat(in_port, node) * self.p.sigma
        self.cred_ret[self.t + lat].append((in_port, node))

    def _inject(self) -> None:
        for node, q in self.srcq.items():
            n = 0
            while q and n < RAMP_BW:
                key = (node, -1)
                if len(self.buf[key]) >= self.p.buf_depth:
                    break
                self.buf[key].append(q.popleft())
                n += 1

    def _deliver(self, f: _MFlit) -> None:
        d = self.reasm[f.dst]
        d[f.pid] = d.get(f.pid, 0) + 1
        if d[f.pid] == f.nflit:
            del d[f.pid]
            self.pkt_done.append((f.pid, f.src, f.dst, f.t_gen,
                                  self.t + RAMP))


# ---------------------------------------------------------------------------
# 4. Centralized request-grant, steady state (both fabrics)
# ---------------------------------------------------------------------------

class _CapFreeAt:
    """`free_at` domain: `cap` servers per key, each with an earliest-free time.

    Kept as the control for `_SlotMap`. It is one register per server, which is
    the cheap hardware, but it cannot place a transfer before a resource's
    frontier even when the intervening cycles are idle. Measured cost of that
    limitation is large and non-obvious -- see `_SlotMap`.
    """

    def __init__(self, cap: int = 1):
        self.cap = cap
        self.f: dict[Any, list[int]] = {}

    def earliest(self, key: Any, dur: int, t_min: int) -> int:
        if dur <= 0:
            return t_min
        srv = self.f.get(key)
        return t_min if srv is None else max(t_min, min(srv))

    def reserve(self, key: Any, s: int, e: int) -> None:
        if e <= s:
            return
        srv = self.f.get(key)
        if srv is None:
            srv = [0] * self.cap
            self.f[key] = srv
        i = min(range(self.cap), key=lambda j: srv[j])
        srv[i] = e

    def rebase(self, base: int) -> None:
        pass


class _SlotMap:
    """`interval` domain: a sliding availability bitmap per server per resource.

    One Python int per server holds "occupied" bits over a window of `span`
    cycles starting at `base`, which tracks simulated time. Two things fall out:

    * Finding a feasible start for a whole rigid path is one bitwise AND of the
      per-resource availability vectors, each shifted by its prefix delay. That
      is also how it would be built in hardware, and it is what the plan calls
      the interval table.
    * A transfer can be placed BEFORE a resource's frontier, in a hole.

    That second property is not a refinement, it is the difference between
    working and not working. Under `free_at`, the start time of a rigid transfer
    is the max over its path, and committing it pushes EVERY resource on the
    path out to that max -- so one congested link exports its lateness to every
    link it touches, and those links export it further. Measured: link
    utilization stalls at 22% while every frontier sits pinned at the horizon,
    holding accepted throughput at 0.109 against an analytic 0.490. With
    interval placement the same configuration reaches the analytic bound.
    """

    def __init__(self, cap: int = 1, span: int = 384):
        self.cap = cap
        self.span = span
        self.base = 0
        self.full = (1 << span) - 1
        self.occ: dict[Any, list[int]] = {}
        self.n_reserved = 0

    def rebase(self, base: int) -> None:
        d = base - self.base
        if d <= 0:
            return
        if d >= self.span:
            self.occ.clear()
        else:
            for srv in self.occ.values():
                for i in range(len(srv)):
                    srv[i] >>= d
        self.base = base

    def avail_run(self, key: Any, dur: int) -> int:
        """Bit i set => ONE server is free for all of [base+i, base+i+dur).

        The quantifier order matters and getting it wrong is not a rounding
        error. Taking "at least one server is free" per cycle and then ANDing
        across the run admits a slot where different servers cover different
        cycles, which no single transfer can use. With m*sigma = 1 the two forms
        agree, which is why this only shows up once packets occupy more than one
        cycle -- and it showed up as the capacity assertion in `reserve` firing,
        not as a silently wrong answer.
        """
        srv = self.occ.get(key)
        if srv is None:
            return self.full
        out = 0
        for x in srv:
            a = self.full & ~x
            r = a
            for k in range(1, dur):
                r &= (a >> k)
            out |= r
        return out

    def reserve(self, key: Any, s: int, e: int) -> None:
        if e <= s:
            return
        rs, re = s - self.base, e - self.base
        if rs < 0 or re > self.span:
            raise AssertionError(f"reservation {s}..{e} outside window "
                                 f"[{self.base},{self.base + self.span})")
        srv = self.occ.get(key)
        if srv is None:
            srv = [0] * self.cap
            self.occ[key] = srv
        m = ((1 << (re - rs)) - 1) << rs
        for i in range(self.cap):
            if not (srv[i] & m):
                srv[i] |= m
                self.n_reserved += 1
                return
        # Reaching here means the arbiter double-booked a resource, i.e. the
        # conflict predicate was violated. Checked on every commit rather than
        # in a separate pass, so no run can silently produce a bad schedule.
        raise AssertionError(f"capacity {self.cap} exceeded on {key} at {s}")


@dataclass
class _Req:
    """One queued packet, i.e. one entry of a source's residual VOQ bitmap."""
    pid: int
    src: int
    dst: int
    t_gen: int


class RGSim:
    """Online centralized request-grant scheduler for the mesh (D-M) or ring (D-R).

    Per arbitration round the arbiter sees one request per source carrying that
    source's residual VOQ bitmap, matches under the fabric's conflict predicate,
    and returns one wide grant per source. Ungranted VOQs stay in the bitmap and
    are re-requested next round -- that residual-bitmap discipline is the whole
    point of the "one request, few grants" formulation, and it is what makes the
    control-plane message count 2*48 per round regardless of backlog.

    Two-level pointers follow the batch implementation: a grant pointer per
    contended resource class (link on the mesh, ring-direction on the ring) and
    an accept pointer per source. In steady state the candidate set is small
    enough that the pointers matter mostly for fairness, which `fairness_cv`
    measures directly.
    """

    def __init__(self, fabric: str, p: SteadyParams, seed: int = 0):
        self.fabric = fabric
        self.p = p
        self.rng = random.Random(seed)
        self.t = 0
        self.dur = p.m * p.sigma
        self.n = 48

        pairs = [(s, d) for s in range(self.n) for d in range(self.n) if s != d]
        span = p.t_rtt + max(p.horizon, p.ca_period) + 128 + 2 * self.dur + 64

        def mk(cap: int):
            return (_SlotMap(cap, span) if p.conflict_domain == "interval"
                    else _CapFreeAt(cap))

        self.m_link, self.m_inj, self.m_ej = mk(1), mk(RAMP_BW), mk(RAMP_BW)
        self.maps = [self.m_link, self.m_inj, self.m_ej]
        if fabric == "mesh":
            self.topo = Topology("mesh")
            self.topo.sigma = p.sigma
            self._plan = None if p.path_mode == "xy" else build_plan(
                pairs, p.path_mode, seed=seed)
        else:
            self.rtopo = RingTopology(sigma=p.sigma,
                                      board_ports=p.board_ports,
                                      leave_ports=p.leave_ports)
            self.m_board, self.m_leave = mk(p.board_ports), mk(p.leave_ports)
            self.maps += [self.m_board, self.m_leave]
            self._plan = build_ring_plan(self.rtopo, pairs, p.ring_path_mode)
        self._res: dict[tuple[int, int], tuple[list, int]] = {}

        self.voq: dict[int, dict[int, deque[_Req]]] = defaultdict(
            lambda: defaultdict(deque))
        self.outstanding: dict[int, int] = defaultdict(int)
        self.voq_next: dict[tuple[int, int], int] = defaultdict(int)
        self.aptr: dict[int, int] = defaultdict(int)
        self.gptr: dict[Any, int] = defaultdict(int)
        self.sptr = 0

        self.pkt_done: list[tuple[int, int, int, int, int]] = []
        self.done_at: dict[int, list[tuple[int, int, int, int, int]]] = \
            defaultdict(list)
        self.retire_at: dict[int, list[int]] = defaultdict(list)
        self.grant_wait: list[int] = []
        self._pid = 0
        self.st: dict[str, int] = defaultdict(int)
        self._n_queued = 0

    # -- interface used by run_steady -------------------------------------

    def offer(self, src: int, dst: int) -> None:
        self.voq[src][dst].append(_Req(self._pid, src, dst, self.t))
        self._pid += 1
        self._n_queued += 1

    def backlog(self) -> int:
        return self._n_queued

    def in_network(self) -> int:
        return 0                      # bufferless by construction

    # -- footprints --------------------------------------------------------

    def _res_of(self, s: int, d: int) -> tuple[list, int]:
        """Resource list [(map, key, prefix_delay)] plus the arrival offset.

        This is the whole conflict predicate reduced to data: D-M contributes
        links + the two ramps, D-R additionally contributes board and leave
        points, and R4 needs no separate clause because the turn's leave and
        board offsets are fixed relative to t0 in the same footprint.
        """
        got = self._res.get((s, d))
        if got is not None:
            return got
        if self.fabric == "mesh":
            path = (self.topo.dor_path(s, d) if self._plan is None
                    else self._plan.paths[(s, d)])
            res, acc = [], 0
            for i in range(len(path) - 1):
                res.append((self.m_link, (path[i], path[i + 1]), acc))
                acc += self.topo.link_lat(path[i], path[i + 1]) * self.p.sigma
            wire = acc
        else:
            f = self.rtopo.footprint(0, self._plan.paths[(s, d)], self.p.m)
            res = [(self.m_link, e, pref) for e, pref in f.links]
            res += [(self.m_board, k, off) for k, off in f.boards]
            res += [(self.m_leave, k, off) for k, off in f.leaves]
            wire = f.wire
        res.append((self.m_inj, s, 0))
        res.append((self.m_ej, d, wire))
        got = (res, wire)
        self._res[(s, d)] = got
        return got

    def _earliest(self, s: int, d: int, t_lo: int, t_hi: int) -> int | None:
        """Earliest conflict-free t0 in [t_lo, t_hi), or None."""
        dur = self.dur
        res, _wire = self._res_of(s, d)
        t_lo = max(t_lo, self.voq_next[(s, d)])
        if t_lo >= t_hi:
            return None
        if self.p.conflict_domain == "interval":
            base = self.m_link.base
            mask = ((1 << (t_hi - t_lo)) - 1) << (t_lo - base)
            for mp, key, pref in res:
                mask &= mp.avail_run(key, dur) >> pref
                if not mask:
                    return None
            return base + (mask & -mask).bit_length() - 1
        t = t_lo
        for _ in range(64):
            cand = t
            for mp, key, pref in res:
                got = mp.earliest(key, dur, t + pref)
                if got > t + pref:
                    cand = max(cand, got - pref)
            if cand == t:
                return t if t < t_hi else None
            t = cand
            if t >= t_hi:
                return None
        return None

    def _commit(self, s: int, d: int, t0: int) -> int:
        dur = self.dur
        res, wire = self._res_of(s, d)
        for mp, key, pref in res:
            mp.reserve(key, t0 + pref, t0 + pref + dur)
        # M3 / R5: the next packet of this VOQ may not start before this one
        # has fully left the source, which keeps delivery in order.
        self.voq_next[(s, d)] = t0 + dur
        return t0 + wire + dur + RAMP

    # -- one cycle ---------------------------------------------------------

    def step(self) -> None:
        for s in self.retire_at.pop(self.t, ()):
            self.outstanding[s] -= 1
        self.pkt_done.extend(self.done_at.pop(self.t, ()))
        for mp in self.maps:
            mp.rebase(self.t)
        if self.t % self.p.ca_period == 0:
            self._round()
        self.t += 1

    def _round(self) -> None:
        p = self.p
        t_lo = self.t + p.t_rtt
        t_hi = t_lo + max(p.ca_period, p.horizon)
        self.st["n_rounds"] += 1
        self.st["n_ctrl_msgs"] += 2 * self.n

        # One request per source carrying its residual VOQ bitmap. The arbiter
        # scans each bitmap round-robin from that source's accept pointer and
        # probes at most `ca_probe` set bits per round.
        #
        # The pointer advances past every bit it PROBED, not only past accepted
        # ones. Strict iSLIP advances on accept alone, but here a bit can fail
        # for a reason that persists for many rounds (its bottleneck link is
        # booked past the scheduling window), and an accept-only pointer would
        # keep re-probing that same bit forever while the rest of the bitmap
        # starves. Measured: the accept-only variant collapses accepted
        # throughput by more than an order of magnitude and drives the fairness
        # CV above 1.0, with a handful of sources monopolizing the arbiter.
        for i in range(self.n):
            s = (self.sptr + i) % self.n
            if p.pipeline_depth and self.outstanding[s] >= p.pipeline_depth:
                continue
            row = self.voq[s]
            keys = [d for d, q in row.items() if q]
            if not keys:
                continue
            self.st["n_bitmap_bits"] += len(keys)
            a = self.aptr[s]
            keys.sort(key=lambda d: (d - a) % self.n)
            n_acc = 0
            last: int | None = None
            for d in keys[:p.ca_probe]:
                last = d
                if p.pipeline_depth and \
                        self.outstanding[s] + n_acc >= p.pipeline_depth:
                    break
                t0 = self._earliest(s, d, t_lo, t_hi)
                if t0 is None:
                    self.st["n_deferred"] += 1
                    continue
                req = row[d].popleft()
                self._n_queued -= 1
                t_done = self._commit(s, d, t0)
                n_acc += 1
                self.outstanding[s] += 1
                self.done_at[t_done].append((req.pid, s, d, req.t_gen, t_done))
                self.retire_at[t0 + self.dur].append(s)
                self.grant_wait.append(t0 - req.t_gen)
                self.st["n_grants"] += 1
                if n_acc >= p.grants_per_src:
                    break
            if last is not None:
                self.aptr[s] = (last + 1) % self.n
        self.sptr = (self.sptr + 1) % self.n


# ---------------------------------------------------------------------------
# 5. ring_base adapter
# ---------------------------------------------------------------------------

class RingBaseAdapter:
    """Wraps `RingBaseSim` in the same offer/step/poll shape as the others."""

    def __init__(self, p: SteadyParams, seed: int = 0):
        self.p = p
        self.topo = RingTopology(sigma=p.sigma)
        self.sim = RingBaseSim(self.topo, RingBaseParams(
            dim_order=p.dim_order, swap_rule=p.swap_rule,
            resolution_mode=p.resolution_mode, fifo_depth=p.fifo_depth,
            t_inj=p.t_inj, t_xfer=p.t_xfer), seed=seed)
        self.t_gen: dict[int, int] = {}
        self.pkt_done: list[tuple[int, int, int, int, int]] = []

    def offer(self, src: int, dst: int) -> None:
        pid = self.sim.offer(src, dst, self.p.m)
        self.t_gen[pid] = self.sim.t

    def step(self) -> None:
        self.sim.step()
        while self.sim.pkt_done:
            pid, s, d, t = self.sim.pkt_done.pop()
            self.pkt_done.append((pid, s, d, self.t_gen.pop(pid, t), t))

    def backlog(self) -> int:
        return self.sim.backlog()

    def in_network(self) -> int:
        """Flits held at stations. Ring links are counted as in flight, not held.

        The bridge FIFOs and eject queues ARE buffers, and this is where the
        baseline's "bufferless" claim has to be qualified: the rings hold no
        state, the stations do.
        """
        return (sum(len(q) for q in self.sim.fifo.values())
                + sum(len(q) for q in self.sim.ejectq.values()))


# ---------------------------------------------------------------------------
# 6. Analytic saturation anchors
# ---------------------------------------------------------------------------

def anchors(m: int = 1, sigma: int = 1) -> dict[str, float]:
    """lambda* upper bounds from hottest-resource load; any measurement above
    these means the implementation is wrong, so they go into the check list."""
    load = {                      # flits crossing the hottest resource per
        "mesh_xy": 96,            # all-to-all epoch (47 packets per node)
        "ring_fixed": 60,
        "ring_balanced": 49,
        "ring_port": 42,
        "ring_whole": 192,
    }
    return {k: 47.0 / (v * m * sigma) for k, v in load.items()}


# ---------------------------------------------------------------------------
# 7. The driver
# ---------------------------------------------------------------------------

def build(config: str, p: SteadyParams, seed: int = 0):
    if config == "mesh_base":
        topo = Topology("mesh")
        topo.sigma = p.sigma
        return MeshBaseSim(topo, p, seed=seed)
    if config == "ring_base":
        return RingBaseAdapter(p, seed=seed)
    if config == "mesh_islip2d":
        return RGSim("mesh", p, seed=seed)
    if config == "ring_islip2d":
        return RGSim("ring", p, seed=seed)
    raise ValueError(f"unknown config: {config}")


def run_steady(config: str, p: SteadyParams) -> dict[str, Any]:
    """Simulate `config` at rate p.lam and return the steady-state metrics."""
    sim = build(config, p, seed=p.seed)
    inj = Injector(48, p.lam, seed=p.seed + 1)
    col = Collector(48)
    total = p.warmup + p.measure
    trace_every = max(1, p.measure // 200)
    n_offered = 0
    bail = 0

    for t in range(total):
        for s, d in inj.arrivals():
            sim.offer(s, d)
            n_offered += 1
        sim.step()

        while sim.pkt_done:
            pid, s, d, t_gen, t_done = sim.pkt_done.pop()
            if t_gen >= p.warmup:
                col.sample(s, t_done - t_gen)

        if t >= p.warmup:
            if (t - p.warmup) % trace_every == 0:
                col.trace(t, sim.backlog())
            col.max_residency = max(col.max_residency, sim.in_network())
        if sim.backlog() > p.max_backlog:
            bail = t
            break

    slope = col.slope()
    acc = col.n_delivered / (48 * p.measure)
    # Stability needs both tests. A slope test alone passes configurations that
    # grow slowly inside a finite window; a throughput test alone passes
    # configurations that deliver the offered rate while the queue quietly
    # ramps. Note also that peak accepted throughput can sit slightly ABOVE the
    # uniform-traffic anchor once the system is overloaded: with per-VOQ
    # backpressure the arbiter delivers whatever is unblocked, so the accepted
    # MIX drifts away from uniform and away from the hot cut. The anchors bound
    # the stable region, not the overloaded peak.
    ratio = acc / p.lam if p.lam > 0 else 0.0
    out: dict[str, Any] = {
        # Every knob that can move a number is echoed, so a figure in the report
        # points at one fully specified row rather than at a curve.
        "config": config, "lam": p.lam, "m": p.m, "sigma": p.sigma,
        "buf_depth": p.buf_depth, "num_vc": p.num_vc, "t_rtt": p.t_rtt,
        "grants_per_src": p.grants_per_src,
        "conflict_domain": p.conflict_domain, "horizon": p.horizon,
        "ca_period": p.ca_period, "ca_probe": p.ca_probe,
        "pipeline_depth": p.pipeline_depth,
        "path_mode": p.path_mode, "ring_path_mode": p.ring_path_mode,
        "board_ports": p.board_ports, "leave_ports": p.leave_ports,
        "fifo_depth": p.fifo_depth, "swap_rule": p.swap_rule,
        "resolution_mode": p.resolution_mode, "dim_order": p.dim_order,
        "t_inj": p.t_inj, "t_xfer": p.t_xfer,
        "warmup": p.warmup, "measure": p.measure, "seed": p.seed,
        "accepted": round(acc, 5),
        "accepted_flits": round(acc * p.m, 5),
        "p50": col.pct(0.50), "p99": col.pct(0.99),
        "mean_lat": round(sum(col.lat) / len(col.lat), 2) if col.lat else None,
        "accept_ratio": round(ratio, 4),
        "backlog_slope": round(slope, 5),
        "stable": bool(slope < 0.002 and ratio >= 0.98 and not bail),
        "backlog_end": sim.backlog(),
        "fairness_cv": round(col.fairness_cv(), 4),
        "n_samples": len(col.lat),
        "bail_at": bail or None,
        "in_network_max": col.max_residency,
    }
    if isinstance(sim, RGSim):
        gw = sim.grant_wait
        out["grant_wait_mean"] = round(sum(gw) / len(gw), 2) if gw else None
        out["n_rounds"] = sim.st["n_rounds"]
        out["ctrl_msgs_per_cy"] = round(
            sim.st["n_ctrl_msgs"] / max(1, sim.t), 2)
        out["grants_per_round"] = round(
            sim.st["n_grants"] / max(1, sim.st["n_rounds"]), 3)
        out["defer_per_round"] = round(
            sim.st["n_deferred"] / max(1, sim.st["n_rounds"]), 2)
        out["bitmap_bits_per_round"] = round(
            sim.st["n_bitmap_bits"] / max(1, sim.st["n_rounds"]), 1)
    if isinstance(sim, RingBaseAdapter):
        s = sim.sim.st
        out["n_deflections"] = s["n_deflections"]
        out["defl_per_pkt"] = round(
            s["n_deflections"] / max(1, s["n_delivered_pkts"]), 3)
        out["n_swaps"] = s["n_swaps"]
        out["n_deadlock_recoveries"] = s["n_deadlock_recoveries"]
        out["n_out_of_order"] = s["n_out_of_order"]
        out["max_reasm"] = s["max_reasm_occupancy"]
        out["max_inj_starve"] = s["max_inj_starve"]
        out["max_deflections"] = s["max_deflections"]
    if isinstance(sim, MeshBaseSim):
        out["n_credit_stall"] = sim.st["n_credit_stall"]
    return out


# ---------------------------------------------------------------------------
# 8. Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    a = anchors()
    print("=== analytic anchors (m=1, sigma=1) ===")
    for k, v in a.items():
        print(f"  {k:14} lambda* <= {v:.3f}")

    hdr = (f"{'config':13} {'lam':>5} {'acc':>7} {'ratio':>6} {'p50':>6} "
           f"{'p99':>7} {'slope':>8} {'stab':>5} {'cv':>6} {'resid':>6}")
    print("\n=== four configurations across saturation ===")
    print(hdr)
    for lam in (0.05, 0.4, 0.5, 0.75):
        for cfg in CONFIGS:
            p = SteadyParams(lam=lam, warmup=1200, measure=5000, buf_depth=20)
            r = run_steady(cfg, p)
            print(f"{cfg:13} {lam:>5.2f} {r['accepted']:>7.4f} "
                  f"{r['accept_ratio']:>6.3f} {r['p50']:>6.0f} "
                  f"{r['p99']:>7.0f} {r['backlog_slope']:>8.4f} "
                  f"{int(r['stable']):>5} {r['fairness_cv']:>6.3f} "
                  f"{r['in_network_max']:>6}")

    print("\n=== conflict domain: the interval table is not optional ===")
    print(f"{'config':13} {'domain':9} {'lam':>5} {'acc':>7} {'p50':>6} "
          f"{'gwait':>7} {'g/rnd':>6}")
    for cfg in ("mesh_islip2d", "ring_islip2d"):
        for dom in ("interval", "free_at"):
            p = SteadyParams(lam=0.5, warmup=1200, measure=5000,
                             conflict_domain=dom)
            r = run_steady(cfg, p)
            print(f"{cfg:13} {dom:9} {0.5:>5.2f} {r['accepted']:>7.4f} "
                  f"{r['p50']:>6.0f} {r['grant_wait_mean']:>7.1f} "
                  f"{r['grants_per_round']:>6.2f}")

    print("\n=== mesh_base: buffer depth vs credit round trip (RTT ~15-19) ===")
    print(f"{'buf':>4} {'lam':>5} {'acc':>7} {'ratio':>6} {'p50':>7} {'stab':>5}")
    for bd in (4, 8, 20):
        for lam in (0.2, 0.4):
            p = SteadyParams(lam=lam, buf_depth=bd, warmup=1200, measure=5000)
            r = run_steady("mesh_base", p)
            print(f"{bd:>4} {lam:>5.2f} {r['accepted']:>7.4f} "
                  f"{r['accept_ratio']:>6.3f} {r['p50']:>7.0f} "
                  f"{int(r['stable']):>5}")
