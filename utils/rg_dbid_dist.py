#!/usr/bin/env python3
"""Distributed request-grant: the home agent (HA) arbitrates when it returns DBID.

CHI write handshake, which is the only control the protocol already carries:

    RN --WriteReq--> HA          (RN holds the data; nothing enters the fabric)
    RN <--DBIDResp-- HA          (HA has a data buffer for this write)
    RN --WriteData--> HA         (m flits, only after DBIDResp arrives)

WriteData is forbidden until DBIDResp, so the HA already decides *who* may put
write data into the fabric and *when*. This module changes nothing on the wire;
it only changes the HA's policy for issuing DBIDResp. There is no central
arbiter and no link-occupancy table: each HA sees only its own request queue.

What the HA can and cannot see
------------------------------
It can bound the number of its own writes in flight (`K`, the DBID pool size /
overcommit degree), choose which requester to serve next, and pace its grants.
It cannot see link conflicts on the path, so the data plane underneath is the
UNMODIFIED baseline fabric (`MeshBaseSim` with credit flow control, or
`RingBaseAdapter` with E-tag / I-tag + deflection). The question under test is
how much of the centralized scheduler's benefit comes from simply bounding
the admitted load at the destination, with a table of K counters per HA.

Because every HA admits at most K writes, the fabric holds at most 48*K
packets. That is an admission-control bound, and it is what can stop the
baseline's congestion collapse.

Timing model
------------
REQ and DBIDResp ride the protocol's own REQ / RSP channels, modelled as
contention-free with latency = wire delay of the Manhattan path + RAMP
(`ctrl_lat_mode="full"`), or half of that wire delay (`"half"`, matches the
private control NoC of the centralized study). The DBID is freed when the last
data flit lands at the HA. The wrapper has the same offer / step / pkt_done /
backlog interface as the other simulators, so `run_steady` drives it unchanged.
Latency is measured from the cycle the write was generated, i.e. it includes
the REQ -> DBIDResp round trip.

K = 0 means an unbounded pool: every REQ is granted the cycle it arrives, which
is the baseline plus a fixed round trip. That is the control experiment.
"""

from __future__ import annotations

import heapq
from collections import defaultdict, deque
from typing import Any

from rg_topo import RAMP, RAMP_BW, Topology

N_NODES = 48


class DbidDistSim:
    """HA-side DBID arbitration wrapped around a baseline data plane."""

    def __init__(self, base: Any, fabric: str, p: Any):
        self.base = base
        self.fabric = fabric
        self.p = p
        self.t = 0
        self.n = N_NODES
        self.k = int(p.dbid_k)                       # 0 = unbounded
        self.policy = p.dbid_policy                  # least_served / round_robin
        self.pace = float(p.dbid_pace)               # min cycles between grants
        self.topo = Topology("mesh" if fabric == "mesh" else "torus")
        self._half = p.ctrl_lat_mode == "half"
        self._lat: dict[tuple[int, int], int] = {}

        # (time, seq) -> payload event queues
        self._req_at: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        self._dbid_at: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
        # HA state, indexed by dst
        self.queue: dict[int, dict[int, deque]] = defaultdict(
            lambda: defaultdict(deque))
        self.active: dict[int, set[int]] = defaultdict(set)
        self.outst = [0] * self.n
        self.served: dict[int, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        self.rr = [0] * self.n
        self.next_grant = [0.0] * self.n
        self._pre = 0                  # writes not yet injected into the fabric
        self._pid = 0
        self._meta: dict[int, tuple[int, int]] = {}  # base pid -> (t_gen, req)
        self.pkt_done: list[tuple[int, int, int, int, int]] = []

        self.dbid_wait: list[int] = []
        self.st: dict[str, int] = defaultdict(int)
        self.ha_out_max = 0
        self.ha_queue_max = 0

    # -- passthrough used by run_steady ------------------------------------

    @property
    def bisect(self):
        return self.base.bisect

    @property
    def bisect_busy(self) -> int:
        return self.base.bisect_busy

    def in_network(self) -> int:
        return self.base.in_network()

    def backlog(self) -> int:
        # Same unit as the baselines (flits waiting at a source): a write that
        # has not been granted yet is still waiting at its source.
        return self._pre * self.p.m + self.base.backlog()

    # -- control-plane latency --------------------------------------------

    def _ctrl(self, a: int, b: int) -> int:
        key = (a, b) if a < b else (b, a)
        got = self._lat.get(key)
        if got is None:
            w = self.topo.wire_distance(a, b)
            got = (w // 2 if self._half else w) + RAMP
            self._lat[key] = got
        return got

    # -- interface ---------------------------------------------------------

    def offer(self, src: int, dst: int) -> None:
        rid = self._pid
        self._pid += 1
        self._pre += 1
        self._meta[rid] = (self.t, src)
        self._req_at[self.t + self._ctrl(src, dst)].append((rid, src, dst))

    def _pick(self, dst: int) -> int:
        act = self.active[dst]
        if self.policy == "round_robin":
            ptr = self.rr[dst]
            src = min(act, key=lambda s: (s - ptr) % self.n)
        else:
            ptr = self.rr[dst]
            sv = self.served[dst]
            src = min(act, key=lambda s: (sv[s], (s - ptr) % self.n))
        self.rr[dst] = (src + 1) % self.n
        return src

    def _grant(self, dst: int) -> None:
        t = self.t
        while self.active[dst] and (self.k == 0 or self.outst[dst] < self.k):
            if self.pace and self.next_grant[dst] > t:
                return
            src = self._pick(dst)
            q = self.queue[dst][src]
            rid, t_req = q.popleft()
            if not q:
                self.active[dst].discard(src)
            self.outst[dst] += 1
            self.served[dst][src] += self.p.m
            self.ha_out_max = max(self.ha_out_max, self.outst[dst])
            if self.pace:
                self.next_grant[dst] = max(self.next_grant[dst], t) + self.pace
            self._dbid_at[t + self._ctrl(src, dst)].append((rid, src, dst))
            self.st["n_grants"] += 1

    def step(self) -> None:
        t = self.t
        # 1. REQs landing at their HA
        touched: set[int] = set()
        for rid, src, dst in self._req_at.pop(t, ()):
            self.queue[dst][src].append((rid, t))
            self.active[dst].add(src)
            touched.add(dst)
        # 2. HAs issue DBIDResp (every HA with a waiting queue, since a
        #    completing write can have freed a slot or a pace timer can expire)
        for dst in list(touched | {d for d, a in self.active.items() if a}):
            qlen = sum(len(q) for q in self.queue[dst].values())
            self.ha_queue_max = max(self.ha_queue_max, qlen)
            self._grant(dst)
        # 3. DBIDResp reaching the RN: data enters the baseline fabric now
        for rid, src, dst in self._dbid_at.pop(t, ()):
            t_gen, _ = self._meta[rid]
            self.dbid_wait.append(t - t_gen)
            self._pre -= 1
            self.base.offer(src, dst)
            bpid = self._base_pid()
            self._meta[("b", bpid)] = (t_gen, dst)
            self._meta.pop(rid, None)
        # 4. data plane
        self.base.step()
        while self.base.pkt_done:
            bpid, s, d, _tg, t_done = self.base.pkt_done.pop()
            t_gen, dst = self._meta.pop(("b", bpid))
            self.outst[dst] -= 1
            self.pkt_done.append((bpid, s, d, t_gen, t_done))
        self.t += 1

    def _base_pid(self) -> int:
        """Id of the packet the baseline just accepted (both share `_pid`)."""
        b = self.base
        if hasattr(b, "_pid"):
            return b._pid - 1
        return b.sim._pid - 1
