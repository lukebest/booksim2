#!/usr/bin/env python3
"""iSLIP-ring: iSLIP time-slot booking at ring grain on the 2D bufferless ring.

Same view of the fabric as CA-ring (rg_ca_ring.py): the arbiter sees rings and
bridges, never links. A cross-ring flow (x_s, y_s) -> (x_d, y_d) rides its row
ring to the bridge at (x_d, y_s), turns onto column ring x_d, and the existing
I-tag / E-tag data plane delivers it. A flow whose source already sits on the
destination ring bypasses the arbiter. Arriving on the destination ring is
all the arbiter promises.

What differs from CA-ring is the arbitration discipline. CA-ring is a credit
counter: it says "you may inject now" and lets the data plane sort out the
rest. iSLIP-ring is iSLIP-2D with the booked resource coarsened from a segment
to a ring: each grant books a whole packet at a start time, in interval tables,
and the packet then moves with no one else booked on top of it.

Booked resources per grant of one packet (m flits, first flit leaves at t0):
  * the source injection port        [t0, t0+m)                 capacity 1
  * the source ring direction        [t0, t0+m), weight h       capacity k
    h is the first-leg hop count, k the ring's segment count. The ring is one
    resource, so its segments are not told apart; instead a packet charges h
    segment-slots and the ring holds at most k per cycle. That is the
    necessary condition for no conflict (segment-cycles used <= available) and
    it keeps the spatial reuse an exclusive whole-ring hold would throw away.
  * the bridge                       [t0+wire1, t0+wire1+m)     capacity 1
    wire1 is the first-leg flight time, hops x hop delay.

Round structure follows iSLIP-2D: request bit per (source, bridge); each bridge
grants one requester round robin; each source accepts up to `grants_per_src`
grants round robin; an ordered fill adds whatever still fits the round (one
packet per bridge, grants_per_src per source, ring weight <= k). Accepted
packets are then booked at their earliest feasible start, longest first.

Like iSLIP-2D this is a batch scheduler: everything offered before a step is
scheduled in one go, and its rounds are assumed to finish within the request /
grant round trip (`round_lat` = 0). `round_lat` = 1 charges one cycle per round.

The ring-direction table is booked with a ring-level approximation: booked
segment-slots can still collide at one segment. The data plane absorbs that
with deflection, which is why this scheme keeps the ring-station storage.
"""

from __future__ import annotations

import math
from array import array
from collections import defaultdict, deque
from typing import Any

from rg_ring_topo import RingTopology
from rg_sched_cost import DEPTH, W_T, distributed_cost
from rg_steady_des import RingBaseAdapter, SteadyParams
from rg_topo import coord, nid

N_NODES = 48
HORIZON = 1 << 17


def _clog2(x: int) -> int:
    return max(1, math.ceil(math.log2(max(2, x))))


class RingSlipSim:
    scheme = "islip_ring"

    def __init__(self, p: SteadyParams, *, grants_per_src: int = 2,
                 round_lat: int = 0, book_dst: bool = False,
                 ring_cap_scale: float = 1.0, book_ej: bool = False,
                 seed: int = 0):
        self.p = p
        self.m = p.m
        self.t_rtt = p.t_rtt
        self.g = grants_per_src
        self.round_lat = round_lat
        self.book_dst = book_dst
        self.book_ej = book_ej
        self.ring_cap_scale = ring_cap_scale
        inner = SteadyParams(**{**p.__dict__, "m": 1})
        self.base = RingBaseAdapter(inner, seed=seed)
        self.sim = self.base.sim
        self.topo: RingTopology = self.base.topo
        self.mx, self.my = self.topo.mx, self.topo.my
        self.n = self.topo.n
        self.t = 0

        # static route facts: (s, d) -> first-leg ring dir, bridge, hops, wire,
        # and (for the optional destination-ring booking) the second leg.
        self.first: dict[tuple[int, int], dict[str, Any]] = {}
        self.ring_cap: dict[tuple, int] = {}
        for s in range(self.n):
            for d in range(self.n):
                if s == d:
                    continue
                legs = self.sim._route(s, d)
                if len(legs) < 2:
                    continue
                ring, direction, idx, target = legs[0]
                k = self.topo.ring_size(ring)
                hops = ((target - idx) * direction) % k
                ent: dict[str, Any] = {
                    "rk": (ring, direction),
                    "bridge": self.topo.ring_nodes(ring)[target],
                    "h": hops,
                    "wire": hops * self.topo.ring_lat(ring),
                }
                r2, d2, i2, t2 = legs[1]
                k2 = self.topo.ring_size(r2)
                ent["rk2"] = (r2, d2)
                ent["h2"] = ((t2 - i2) * d2) % k2
                ent["wire_all"] = ent["wire"] + ent["h2"] * \
                    self.topo.ring_lat(r2)
                self.first[(s, d)] = ent
                self.ring_cap[(ring, direction)] = k
                self.ring_cap[(r2, d2)] = k2
        # ring_cap_scale < 1 derates the ring below its segment count, because
        # a hot segment saturates before the ring-wide average does.
        self.ring_seg = dict(self.ring_cap)
        self.ring_cap = {rk: max(4, int(math.floor(k * ring_cap_scale)))
                         for rk, k in self.ring_cap.items()}
        self.bridges = sorted({e["bridge"] for e in self.first.values()})
        self.n_ring_dirs = len(self.ring_cap)

        self.voq: dict[tuple[int, int], deque] = defaultdict(deque)
        self.local: list[deque] = [deque() for _ in range(self.n)]
        self._orig: dict[int, list] = {}
        self._sub: dict[int, int] = {}
        self._next_orig = 0
        self._n_voq = 0
        self._n_local = 0
        self._n_wait = 0
        self.inject_at: dict[int, list] = defaultdict(list)
        self.gptr = {b: 0 for b in self.bridges}
        self.aptr = [0] * self.n
        self.use: dict[Any, array] = {}
        self.pkt_done: list[tuple] = []
        self.st: dict[str, int] = defaultdict(int)
        self.arbiter_bits, self.bits_breakdown = self._bits()

    # -- area ----------------------------------------------------------------

    def _bits(self) -> tuple[int, dict[str, int]]:
        b: dict[str, int] = {}
        mx = self.mx
        kmax = max(self.ring_seg.values())
        b["request_bitmap"] = self.n * (mx - 1)
        b["bridge_grant_ptrs"] = len(self.bridges) * _clog2(mx)
        b["source_accept_ptrs"] = self.n * _clog2(mx)
        b["round_ring_load"] = self.n_ring_dirs * _clog2(kmax + 1)
        w_ring = _clog2(kmax + 1)
        n_ring_tbl = self.n_ring_dirs
        b["interval_ring_dir"] = n_ring_tbl * DEPTH * (2 * W_T + w_ring)
        b["interval_bridge"] = len(self.bridges) * DEPTH * 2 * W_T
        b["interval_inject"] = self.n * DEPTH * 2 * W_T
        return sum(b.values()), b

    def system_bits(self) -> int:
        return distributed_cost("ring_base",
                                fifo_depth=self.p.fifo_depth)["bits"] \
            + self.arbiter_bits

    # -- workload ------------------------------------------------------------

    def offer(self, src: int, dst: int) -> None:
        oid = self._next_orig
        self._next_orig += 1
        self._orig[oid] = [self.m, self.t, src, dst, 0]
        ent = self.first.get((src, dst))
        if ent is not None:
            xb = coord(ent["bridge"], self.mx)[0]
            self.voq[(src, xb)].append((oid, dst))
            self._n_voq += 1
        else:
            self.local[src].append([oid, dst, self.m])
            self._n_local += self.m

    def backlog(self) -> int:
        return (self._n_voq * self.m + self._n_wait + self._n_local
                + self.base.backlog())

    # -- data plane glue -------------------------------------------------------

    def _send(self, src: int, dst: int, oid: int) -> None:
        pid = self.sim._pid
        self.base.offer(src, dst)
        self._sub[pid] = oid

    def _harvest(self) -> None:
        done = self.base.pkt_done
        while done:
            pid, _s, _d, _tg, t_done = done.pop()
            oid = self._sub.pop(pid)
            o = self._orig[oid]
            o[0] -= 1
            o[4] = max(o[4], t_done)
            if o[0] == 0:
                del self._orig[oid]
                self.pkt_done.append((oid, o[2], o[3], o[1], o[4]))

    # -- interval booking -------------------------------------------------------

    def _arr(self, key: Any) -> array:
        a = self.use.get(key)
        if a is None:
            a = array("b", bytes(HORIZON))
            self.use[key] = a
        return a

    def _items(self, s: int, d: int) -> list[tuple[Any, int, int, int]]:
        """(table, offset from t0, weight, capacity) of one packet grant."""
        e = self.first[(s, d)]
        out = [(("inj", s), 0, 1, 1),
               (e["rk"], 0, e["h"], self.ring_cap[e["rk"]]),
               (("br", e["bridge"]), e["wire"], 1, 1)]
        if self.book_dst:
            out.append((e["rk2"], e["wire"], e["h2"],
                        self.ring_cap[e["rk2"]]))
        if self.book_ej:
            out.append((("ej", d), e["wire_all"], 1, 1))
        return out

    def _book(self, s: int, d: int, floor: int) -> int:
        m = self.m
        items = [(self._arr(k), off, w, cap)
                 for k, off, w, cap in self._items(s, d)]
        t0 = floor
        while True:
            moved = False
            for arr, off, w, cap in items:
                lo = t0 + off
                lim = cap - w
                for i in range(lo + m - 1, lo - 1, -1):
                    if arr[i] > lim:
                        t0 = i + 1 - off
                        moved = True
                        break
            if not moved:
                break
        for arr, off, w, _cap in items:
            for i in range(t0 + off, t0 + off + m):
                arr[i] += w
        return t0

    # -- iSLIP rounds -------------------------------------------------------------

    def _schedule(self, now: int) -> None:
        mx = self.mx
        n_round = 0
        while self._n_voq:
            floor = now + self.t_rtt + n_round * self.round_lat
            n_round += 1
            reqs = {k for k, q in self.voq.items() if q}
            self.st["ctrl"] += len({s for s, _ in reqs})   # request vectors

            load: dict[tuple, int] = defaultdict(int)
            per_src: dict[int, int] = defaultdict(int)
            used_br: set[int] = set()
            picked: list[tuple[int, int, int]] = []        # (s, xb, d)

            # grant: each bridge picks a requester round robin
            grants: dict[int, list[int]] = defaultdict(list)
            for b in self.bridges:
                xb, yb = coord(b, mx)
                for k in range(mx):
                    xs = (self.gptr[b] + k) % mx
                    s = nid(xs, yb, mx)
                    if (s, xb) in reqs:
                        grants[s].append(xb)
                        break
            # accept: each source takes up to g grants round robin
            for s, xbs in grants.items():
                xbs.sort(key=lambda xb: (xb - self.aptr[s]) % mx)
                for xb in xbs:
                    if per_src[s] >= self.g:
                        break
                    d = self.voq[(s, xb)][0][1]
                    e = self.first[(s, d)]
                    k = self.ring_cap[e["rk"]]
                    if load[e["rk"]] + e["h"] > k:
                        continue
                    load[e["rk"]] += e["h"]
                    per_src[s] += 1
                    used_br.add(e["bridge"])
                    picked.append((s, xb, d))
                    self.gptr[e["bridge"]] = (coord(s, mx)[0] + 1) % mx
                    self.aptr[s] = (xb + 1) % mx
                    self.st["ctrl"] += 2                  # grant + accept
            n_unan = len(picked)
            # ordered fill: longest first arc
            chosen = {(s, xb) for s, xb, _ in picked}
            rest = []
            for (s, xb) in reqs:
                if (s, xb) in chosen:
                    continue
                d = self.voq[(s, xb)][0][1]
                e = self.first[(s, d)]
                rest.append((-e["h"], s, xb, d))
            rest.sort()
            for _h, s, xb, d in rest:
                e = self.first[(s, d)]
                if (e["bridge"] in used_br or per_src[s] >= self.g
                        or load[e["rk"]] + e["h"] > self.ring_cap[e["rk"]]):
                    continue
                load[e["rk"]] += e["h"]
                per_src[s] += 1
                used_br.add(e["bridge"])
                picked.append((s, xb, d))
                self.st["ctrl"] += 2
            # book longest arc first
            picked.sort(key=lambda x: (-self.first[(x[0], x[2])]["h"], x[0], x[2]))
            for s, xb, d in picked:
                oid, _d = self.voq[(s, xb)].popleft()
                self._n_voq -= 1
                t0 = self._book(s, d, floor)
                for j in range(self.m):
                    self.inject_at[t0 + j].append((s, d, oid))
                self._n_wait += self.m
                self.st["grants"] += 1
                self.st["t_last_start"] = max(self.st["t_last_start"], t0)
            self.st["unanimous"] += n_unan
        self.st["rounds"] += n_round

    # -- clock ------------------------------------------------------------------

    def step(self) -> None:
        t = self.t
        if self._n_voq:
            self._schedule(t)
        for s, dst, oid in self.inject_at.pop(t, ()):
            self._n_wait -= 1
            self._send(s, dst, oid)
        if self._n_local:
            srcq = self.sim.srcq
            for s in range(self.n):
                lq = self.local[s]
                if lq and not srcq[s]:
                    ent = lq[0]
                    self._send(s, ent[1], ent[0])
                    ent[2] -= 1
                    self._n_local -= 1
                    if ent[2] == 0:
                        lq.popleft()
        self.base.step()
        self._harvest()
        self.t = t + 1

    def export(self) -> dict[str, Any]:
        return {
            "ca_scheme": self.scheme,
            "arbiter_bits": self.arbiter_bits,
            "arbiter_breakdown": self.bits_breakdown,
            "system_bits": self.system_bits(),
            "n_grants": self.st["grants"],
            "rounds": self.st["rounds"],
            "unanimous": self.st["unanimous"],
            "ctrl_msgs": self.st["ctrl"],
            "t_last_start": self.st["t_last_start"],
            "n_deflections": self.sim.st["n_deflections"],
            "n_fifo_full_deflect": self.sim.st["n_fifo_full_deflect"],
        }


def _selfcheck() -> None:
    for m in (1, 16):
        p = SteadyParams(m=m, buf_depth=20, t_rtt=16)
        sim = RingSlipSim(p)
        for s in range(48):
            for d in range(48):
                if s != d:
                    sim.offer(s, d)
        got = last = 0
        while got < 48 * 47 and sim.t < 200_000:
            sim.step()
            while sim.pkt_done:
                got += 1
                last = max(last, sim.pkt_done.pop()[-1])
        assert got == 48 * 47, got
        assert sim._n_wait == 0 and sim._n_voq == 0 and sim._n_local == 0
        e = sim.export()
        print(f"islip_ring m={m}: makespan={last} rounds={e['rounds']} "
              f"grants={e['n_grants']} last_start={e['t_last_start']} "
              f"defl={e['n_deflections']} bits={e['arbiter_bits']}")
    print("breakdown", sim.bits_breakdown)


if __name__ == "__main__":
    _selfcheck()
