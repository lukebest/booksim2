#!/usr/bin/env python3
"""CA-ring: ring-grain central arbitration on the 2D bufferless ring.

The arbiter sees rings and bridges, not links. A cross-ring flow
(x_s, y_s) -> (x_d, y_d) rides its row ring to the bridge at (x_d, y_s), turns
onto column ring x_d, and is delivered by the existing I-tag / E-tag data
plane. The arbiter only decides when a flit may LEAVE its source and onto the
source ring; once the flit has turned onto its destination ring it is the data
plane's problem. Flows whose source already sits on the destination ring (same
row or same column) never touch the arbiter.

Resources, all counted in flits:
  * one counter per source ring direction. Init is the ring's bandwidth-delay
    product: the flits per cycle that direction sustains (ring segments divided
    by the mean first-leg hop count, because arcs on a ring are spatially
    reused) times the no-load round trip, times ring_credit_scale. This is the
    same rule CA-credit uses for a link. ring_basis="inflight" sizes it to the
    flits a ring holds in flight instead (segments x hop delay); that is far
    smaller and throttles the ring.
  * one counter per bridge (init = no-load round trip to the bridge) x scale

Every cycle one iSLIP round runs. Request: each source raises one bit per
bridge it has a queued flit for. Grant: each bridge picks one requester round
robin, provided the bridge and the requester's ring direction still have
credit. Accept: each source takes one grant round robin. A granted flit is one
flit, injected t_rtt later. Credit comes back t_rtt after the flit leaves the
bridge FIFO onto the destination ring (a DONE message).

The grant is per flit, not per packet. The data plane already moves each flit
independently and a node injects one flit a cycle, so a flit-sized grant loses
no throughput and a 64-flit packet can never be wedged by a counter smaller
than the packet.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

from rg_ring_topo import RingTopology
from rg_sched_cost import distributed_cost
from rg_steady_des import RingBaseAdapter, SteadyParams
from rg_topo import coord, nid

N_NODES = 48


def _clog2(x: int) -> int:
    return max(1, math.ceil(math.log2(max(2, x))))


class RingGrainSim:
    scheme = "ca_ring"

    def __init__(self, p: SteadyParams, *, ring_credit_scale: float = 1.0,
                 bridge_credit_scale: float = 1.0, ring_basis: str = "bdp",
                 seed: int = 0):
        if ring_basis not in ("bdp", "inflight"):
            raise ValueError(ring_basis)
        self.ring_basis = ring_basis
        self.p = p
        self.m = p.m
        self.t_rtt = p.t_rtt
        self.ring_scale = ring_credit_scale
        self.bridge_scale = bridge_credit_scale
        # The inner data plane carries single-flit sub-packets; this wrapper
        # owns the packet boundary.
        inner = SteadyParams(**{**p.__dict__, "m": 1})
        self.base = RingBaseAdapter(inner, seed=seed)
        self.sim = self.base.sim
        self.sim.on_transfer = self._on_transfer
        self.topo: RingTopology = self.base.topo
        self.mx, self.my = self.topo.mx, self.topo.my
        self.n = self.topo.n
        self.t = 0

        # static route facts for every cross-ring pair
        self.first: dict[tuple[int, int], tuple[tuple, int]] = {}
        wire_max = 0
        for s in range(self.n):
            for d in range(self.n):
                if s == d:
                    continue
                legs = self.sim._route(s, d)
                if len(legs) < 2:
                    continue
                ring, direction, idx, target = legs[0]
                bridge = self.topo.ring_nodes(ring)[target]
                self.first[(s, d)] = ((ring, direction), bridge)
                k = self.topo.ring_size(ring)
                hops = ((target - idx) * direction) % k
                wire_max = max(wire_max, hops * self.topo.ring_lat(ring))
        self.wire_max = wire_max

        self.nol_bridge = 2 * self.t_rtt + wire_max + 1
        hop_sum: dict[tuple, list[int]] = defaultdict(list)
        for (s, d), (rk, _b) in self.first.items():
            ring, direction, idx, target = self.sim._route(s, d)[0]
            k = self.topo.ring_size(ring)
            hop_sum[rk].append(((target - idx) * direction) % k)
        self.ring_init = {}
        for rk, hs in sorted(hop_sum.items()):
            k = self.topo.ring_size(rk[0])
            if ring_basis == "inflight":
                base = k * self.topo.ring_lat(rk[0])
            else:
                base = k / (sum(hs) / len(hs)) * self.nol_bridge
            self.ring_init[rk] = max(1, int(round(ring_credit_scale * base)))
        bridges = sorted({b for _, b in self.first.values()})
        self.bridge_init = {
            b: max(1, int(round(bridge_credit_scale * self.nol_bridge)))
            for b in bridges}
        self.ring_credit = dict(self.ring_init)
        self.bridge_credit = dict(self.bridge_init)

        # per-source VOQs: one per bridge, entries are [orig_id, dst, flits_left]
        self.voq: list[dict[int, deque]] = [defaultdict(deque)
                                            for _ in range(self.n)]
        self.local: list[deque] = [deque() for _ in range(self.n)]
        self._orig: dict[int, list] = {}
        self._sub: dict[int, tuple] = {}
        self._next_orig = 0
        self._n_voq = 0
        self._n_local = 0
        self._n_wait = 0
        self.inject_at: dict[int, list] = defaultdict(list)
        self.ret: dict[int, list] = defaultdict(list)
        self.gptr = {b: 0 for b in bridges}          # over x of requesting source
        self.aptr = [0] * self.n                     # over x of granting bridge
        self.pkt_done: list[tuple] = []
        self.st: dict[str, int] = defaultdict(int)
        self.arbiter_bits, self.bits_breakdown = self._bits()

    # -- area ----------------------------------------------------------------

    def _bits(self) -> tuple[int, dict[str, int]]:
        b: dict[str, int] = {}
        mx = self.mx
        b["request_bits"] = self.n * (mx - 1)
        w_ring = _clog2(max(self.ring_init.values()) + 1)
        w_br = _clog2(max(self.bridge_init.values()) + 1)
        b["ring_credits"] = len(self.ring_init) * w_ring
        b["bridge_credits"] = len(self.bridge_init) * w_br
        b["bridge_grant_ptrs"] = len(self.bridge_init) * _clog2(mx)
        b["source_accept_ptrs"] = self.n * _clog2(mx)
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
        key = (src, dst)
        if key in self.first:
            xb = coord(self.first[key][1], self.mx)[0]
            self.voq[src][xb].append([oid, dst, self.m])
            self._n_voq += self.m
        else:
            self.local[src].append([oid, dst, self.m])
            self._n_local += self.m

    def backlog(self) -> int:
        return self._n_voq + self._n_wait + self._n_local + self.base.backlog()

    # -- data plane glue -------------------------------------------------------

    def _send(self, src: int, dst: int, oid: int,
              ringkey: tuple | None, bridge: int | None) -> None:
        pid = self.sim._pid
        self.base.offer(src, dst)
        self._sub[pid] = (oid, ringkey, bridge)

    def _on_transfer(self, f: Any, t: int) -> None:
        rec = self._sub.get(f.pid)
        if rec is None or rec[1] is None:
            return
        self.ret[t + self.t_rtt].append((rec[1], rec[2]))
        self.st["ctrl"] += 1                     # DONE

    def _harvest(self) -> None:
        done = self.base.pkt_done
        while done:
            pid, _s, _d, _tg, t_done = done.pop()
            oid = self._sub.pop(pid)[0]
            o = self._orig[oid]
            o[0] -= 1
            o[4] = max(o[4], t_done)
            if o[0] == 0:
                del self._orig[oid]
                self.pkt_done.append((oid, o[2], o[3], o[1], o[4]))

    # -- arbitration ------------------------------------------------------------

    def _head(self, s: int, xb: int):
        q = self.voq[s].get(xb)
        if not q:
            return None
        oid, dst, left = q[0]
        return oid, dst, left

    def _arbitrate(self, t: int) -> None:
        mx = self.mx
        n_req = 0
        for s in range(self.n):
            if self._n_voq and any(self.voq[s].values()):
                n_req += 1
        self.st["ctrl"] += n_req                 # one request vector per source
        if not n_req:
            return

        tent: dict[tuple, int] = defaultdict(int)
        grants: dict[int, list[tuple[int, int, int, tuple]]] = defaultdict(list)
        for b in self.bridge_credit:
            if self.bridge_credit[b] <= 0:
                continue
            xb, yb = coord(b, mx)
            start = self.gptr[b]
            for k in range(mx):
                xs = (start + k) % mx
                if xs == xb:
                    continue
                s = nid(xs, yb, mx)
                head = self._head(s, xb)
                if head is None:
                    continue
                rk = self.first[(s, head[1])][0]
                if self.ring_credit[rk] - tent[rk] <= 0:
                    continue
                tent[rk] += 1
                grants[s].append((xb, b, xs, rk))
                break

        for s, gl in grants.items():
            gl.sort(key=lambda g: (g[0] - self.aptr[s]) % mx)
            xb, b, xs, rk = gl[0]
            oid, dst, left = self._head(s, xb)
            q = self.voq[s][xb]
            q[0][2] -= 1
            if q[0][2] == 0:
                q.popleft()
            self._n_voq -= 1
            self.bridge_credit[b] -= 1
            self.ring_credit[rk] -= 1
            self.inject_at[t + self.t_rtt].append((s, dst, oid, rk, b))
            self._n_wait += 1
            self.gptr[b] = (xs + 1) % mx
            self.aptr[s] = (xb + 1) % mx
            self.st["grants"] += 1
            self.st["ctrl"] += 1                 # grant

    # -- clock ------------------------------------------------------------------

    def step(self) -> None:
        t = self.t
        for rk, b in self.ret.pop(t, ()):
            self.ring_credit[rk] += 1
            self.bridge_credit[b] += 1
        for s, dst, oid, rk, b in self.inject_at.pop(t, ()):
            self._n_wait -= 1
            self._send(s, dst, oid, rk, b)
        self._arbitrate(t)
        if self._n_local:
            srcq = self.sim.srcq
            for s in range(self.n):
                lq = self.local[s]
                if lq and not srcq[s]:
                    ent = lq[0]
                    self._send(s, ent[1], ent[0], None, None)
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
            "ring_credit": max(self.ring_init.values()),
            "ring_credit_min": min(self.ring_init.values()),
            "bridge_credit": max(self.bridge_init.values()),
            "nol_bridge": self.nol_bridge,
            "arbiter_bits": self.arbiter_bits,
            "arbiter_breakdown": self.bits_breakdown,
            "system_bits": self.system_bits(),
            "n_grants": self.st["grants"],
            "ctrl_msgs": self.st["ctrl"],
            "n_deflections": self.sim.st["n_deflections"],
        }


def _selfcheck() -> None:
    p = SteadyParams(m=1, buf_depth=20, t_rtt=16)
    sim = RingGrainSim(p)
    for s in range(48):
        for d in range(48):
            if s != d:
                sim.offer(s, d)
    got = last = 0
    while got < 48 * 47 and sim.t < 100_000:
        sim.step()
        while sim.pkt_done:
            got += 1
            last = max(last, sim.pkt_done.pop()[-1])
    assert got == 48 * 47, got
    # let the last DONE messages land
    for _ in range(4 * p.t_rtt):
        sim.step()
    assert sim.ring_credit == sim.ring_init, "ring credit leaked"
    assert sim.bridge_credit == sim.bridge_init, "bridge credit leaked"
    assert sim._n_wait == 0 and sim._n_voq == 0 and sim._n_local == 0
    print(f"ca_ring m=1: makespan={last} grants={sim.st['grants']} "
          f"bits={sim.arbiter_bits} ring_credit={sim.export()['ring_credit']} "
          f"bridge_credit={sim.export()['bridge_credit']}")
    print("breakdown", sim.bits_breakdown)


if __name__ == "__main__":
    _selfcheck()
