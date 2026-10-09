#!/usr/bin/env python3
"""Homa-style receiver-driven scheduling at the home agent (HA).

Homa (Montazeri et al., SIGCOMM'18) lets the RECEIVER decide who may send:

  * Unscheduled prefix. A sender transmits the first RTTbytes of a message
    blindly, with no grant, so a short message pays no round trip at all.
  * Scheduled remainder. The receiver learns the message length from the first
    packet and issues GRANTs for the rest. A grant lets the sender keep at most
    one RTTbytes "granted but not yet received" per message, i.e. grants are
    clocked by arrivals.
  * SRPT. Among messages with ungranted data, the receiver serves the one with
    the fewest bytes still to receive.
  * Overcommitment. The receiver grants to the top `degree` messages at once,
    not just one, so its downlink does not idle while a sender is slow.
  * Priorities. Receiver-assigned network priority levels. NOT modelled: both
    baseline fabrics have a single traffic class and this study does not add
    one. That removes the part of Homa that protects short messages in the
    switches; what is left is the receiver-side admission control.

This differs from `rg_dbid_dist` (v1), which is a plain DBID *pool*: every
packet waits one REQ -> DBIDResp round trip and the HA only caps the number of
writes in flight. Homa has no per-packet handshake: the grant window moves with
arrivals, and short messages never wait.

Mapping to a CHI write. The unscheduled prefix is data sent before any DBIDResp,
which stock CHI forbids for WriteNoSnp; here it stands for a pre-issued DBID
credit pool the HA hands every RN (the same trade Homa makes). Scheduled
packets correspond to ordinary DBIDResp grants, one per packet.

The wrapper has the offer / offer_msg / step / pkt_done / backlog interface of
the other simulators, around an UNMODIFIED baseline data plane.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from rg_topo import RAMP, Topology

N_NODES = 48


class _Msg:
    __slots__ = ("src", "dst", "L", "t_gen", "granted", "received",
                 "known", "done")

    def __init__(self, src: int, dst: int, L: int, t_gen: int):
        self.src, self.dst, self.L, self.t_gen = src, dst, L, t_gen
        self.granted = 0          # packets the RN has been allowed to send
        self.received = 0         # packets delivered at the HA
        self.known = False        # HA has seen this message's header
        self.done = False


class HomaSim:
    def __init__(self, base: Any, fabric: str, p: Any):
        self.base = base
        self.fabric = fabric
        self.p = p
        self.t = 0
        self.n = N_NODES
        self.window = int(p.homa_window)
        self.unsched = self.window if p.homa_unsched < 0 else int(p.homa_unsched)
        self.degree = int(p.homa_degree)
        self.sched = p.homa_sched
        self.topo = Topology("mesh" if fabric == "mesh" else "torus")
        self._lat: dict[tuple[int, int], int] = {}
        self._half = p.ctrl_lat_mode == "half"

        self.msgs: dict[int, _Msg] = {}
        self._mid = 0
        self.active: dict[int, list[int]] = defaultdict(list)   # dst -> mids
        self._grant_at: dict[int, list[int]] = defaultdict(list)  # t -> mids
        self._known_at: dict[int, list[int]] = defaultdict(list)  # U=0 REQs
        self._meta: dict[int, int] = {}      # base pid -> message id
        self._pre = 0                        # packets not yet in the fabric
        self.pkt_done: list[tuple[int, int, int, int, int]] = []

        self.n_unsched = 0
        self.n_sched = 0
        self.max_inflight = 0     # max granted-but-unreceived of one message
        self.max_active = 0       # max messages competing at one HA
        self.max_granted_at_ha = 0

    # -- passthrough -------------------------------------------------------

    @property
    def bisect(self):
        return self.base.bisect

    @property
    def bisect_busy(self) -> int:
        return self.base.bisect_busy

    def in_network(self) -> int:
        return self.base.in_network()

    def backlog(self) -> int:
        return self._pre * self.p.m + self.base.backlog()

    def _ctrl(self, a: int, b: int) -> int:
        key = (a, b) if a < b else (b, a)
        got = self._lat.get(key)
        if got is None:
            w = self.topo.wire_distance(a, b)
            got = (w // 2 if self._half else w) + RAMP
            self._lat[key] = got
        return got

    # -- interface ---------------------------------------------------------

    def _send(self, mid: int) -> None:
        m = self.msgs[mid]
        self.base.offer(m.src, m.dst)
        b = self.base
        bpid = (b._pid if hasattr(b, "_pid") else b.sim._pid) - 1
        self._meta[bpid] = mid
        self._pre -= 1

    def offer(self, src: int, dst: int) -> None:
        self.offer_msg(src, dst, 1)

    def offer_msg(self, src: int, dst: int, L: int) -> None:
        mid = self._mid
        self._mid += 1
        m = _Msg(src, dst, L, self.t)
        self.msgs[mid] = m
        self._pre += L
        u = min(self.unsched, L)
        m.granted = u
        for _ in range(u):
            self._send(mid)
        self.n_unsched += u
        if u == 0:
            # No unscheduled prefix: the RN must announce the message first.
            self._known_at[self.t + self._ctrl(src, dst)].append(mid)

    def _learn(self, m: _Msg, mid: int) -> None:
        if not m.known:
            m.known = True
            if m.granted < m.L:
                self.active[m.dst].append(mid)

    def _issue(self, dst: int) -> None:
        act = self.active[dst]
        # drop finished / fully granted messages
        act[:] = [i for i in act
                  if self.msgs[i].granted < self.msgs[i].L]
        if not act:
            return
        self.max_active = max(self.max_active, len(act))
        if self.sched == "srpt":
            act.sort(key=lambda i: (self.msgs[i].L - self.msgs[i].received,
                                    self.msgs[i].t_gen, i))
        else:
            act.sort(key=lambda i: (self.msgs[i].t_gen, i))
        for mid in act[:self.degree]:
            m = self.msgs[mid]
            while m.granted < m.L and m.granted - m.received < self.window:
                m.granted += 1
                self.n_sched += 1
                self._grant_at[self.t + self._ctrl(dst, m.src)].append(mid)
            self.max_inflight = max(self.max_inflight, m.granted - m.received)

    def step(self) -> None:
        t = self.t
        # 1. grants reaching their RN: the packet enters the fabric now
        for mid in self._grant_at.pop(t, ()):
            self._send(mid)
        for mid in self._known_at.pop(t, ()):
            self._learn(self.msgs[mid], mid)
        # 2. each HA with competing messages issues grants
        for dst in [d for d, a in self.active.items() if a]:
            self._issue(dst)
        # 3. data plane
        self.base.step()
        touched: set[int] = set()
        while self.base.pkt_done:
            bpid, s, d, _tg, t_done = self.base.pkt_done.pop()
            mid = self._meta.pop(bpid)
            m = self.msgs[mid]
            m.received += 1
            self.pkt_done.append((bpid, s, d, m.t_gen, t_done))
            self._learn(m, mid)
            touched.add(d)
            if m.received == m.L:
                m.done = True
                del self.msgs[mid]
        # packets freed window slots: let those HAs grant in the same cycle
        for dst in touched:
            if self.active.get(dst):
                self._issue(dst)
        self.t += 1

    # -- reporting ---------------------------------------------------------

    def homa_stats(self, warmup: int) -> dict[str, Any]:
        tot = self.n_unsched + self.n_sched
        out: dict[str, Any] = {
            "n_grants": self.n_sched,
            "unsched_frac": round(self.n_unsched / tot, 4) if tot else None,
            "ctrl_msgs_per_cy": round(self.n_sched / max(1, self.t), 2),
            "msg_inflight_max": self.max_inflight,
            "ha_active_max": self.max_active,
        }
        return out
