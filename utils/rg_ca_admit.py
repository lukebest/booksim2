#!/usr/bin/env python3
"""CA-credit and CA-rate admission on the 8x6 mesh and ring.

These are the two central arbiters in docs/rg.md, placed on the same fabrics
as iSLIP-2D so the report can compare them with mesh_islip2d / ring_islip2d.

CA-credit
    One in-flight counter per resource. A link's counter is the bandwidth-delay
    product: flits per cycle x no-load RTT x ca_credit_scale. The HA counter is
    its tracker count. Each epoch every source presents up to ca_probe oldest
    requests; a request is granted only when every resource on its footprint
    still has credit. Credit is taken at the grant and returned when the DONE
    message arrives (one t_rtt after delivery). Granted packets then enter the
    existing data plane — buffered XY mesh, or the deflection ring — one t_rtt
    later. There is no interval table.

CA-rate
    Every ca_period the arbiter collects each core's backlogged demand, computes
    max-min fair rates over link and HA capacities by water-filling, and sends
    the rates down. Cores enforce them with a per-VOQ token bucket at admit
    time. Demand and rates each take one t_rtt, so a newly computed vector
    takes effect 2*t_rtt later. No per-packet request or DONE.

Both schemes need a data plane that can queue, because neither reserves time
slots. iSLIP-2D does, which is why it can delete those buffers. The area model
reports the arbiter on its own and the arbiter plus the data-plane storage.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

from rg_ring_topo import RingTopology
from rg_sched_cost import distributed_cost
from rg_steady_des import MeshBaseSim, RingBaseAdapter, SteadyParams
from rg_topo import RAMP, RAMP_BW, N, Topology

# Analytic packet-rate anchors (m=sigma=1). HA pool defaults are sized to these
# so a tracker shortage is a knob, not an accident.
_ANCHOR = {"mesh": 47.0 / 96.0, "ring": 47.0 / 60.0}


def _clog2(x: int) -> int:
    return max(1, math.ceil(math.log2(max(2, x))))


class _Pkt:
    __slots__ = ("src", "dst", "t_gen", "res")

    def __init__(self, src: int, dst: int, t_gen: int, res: tuple):
        self.src, self.dst, self.t_gen, self.res = src, dst, t_gen, res


class FabricMap:
    """Footprints, credit initials, and rate capacities for one fabric."""

    def __init__(self, fabric: str, p: SteadyParams):
        if fabric not in ("mesh", "ring"):
            raise ValueError(fabric)
        self.fabric = fabric
        self.p = p
        self.n = N
        self.m = p.m
        pairs = [(s, d) for s in range(N) for d in range(N) if s != d]
        wires: dict[tuple[int, int], int] = {}
        raw: dict[tuple[int, int], list[tuple]] = {}
        if fabric == "mesh":
            topo = Topology("mesh")
            topo.sigma = p.sigma
            for s, d in pairs:
                path = topo.dor_path(s, d)
                acc = 0
                keys = []
                for i in range(len(path) - 1):
                    a, b = path[i], path[i + 1]
                    keys.append(("L", (a, b)))
                    acc += topo.link_lat(a, b) * p.sigma
                keys.append(("I", s))
                keys.append(("E", d))
                keys.append(("H", d))
                raw[(s, d)] = keys
                wires[(s, d)] = acc
            self._bw = {
                "L": 1.0 / p.sigma,
                "I": float(RAMP_BW),
                "E": float(RAMP_BW),
            }
        else:
            rt = RingTopology(sigma=p.sigma, board_ports=p.board_ports,
                              leave_ports=p.leave_ports)
            for s, d in pairs:
                fp = rt.footprint(0, rt.fixed_path(s, d), p.m)
                keys = [("L", e) for e, _ in fp.links]
                keys += [("B", k) for k, _ in fp.boards]
                keys += [("V", k) for k, _ in fp.leaves]
                keys += [("I", s), ("E", d), ("H", d)]
                raw[(s, d)] = keys
                wires[(s, d)] = fp.wire
            self._bw = {
                "L": 1.0 / p.sigma,
                "B": float(p.board_ports) / p.sigma,
                "V": float(p.leave_ports) / p.sigma,
                "I": float(RAMP_BW),
                "E": float(RAMP_BW),
            }
        self.max_wire = max(wires.values())
        # Grant flight + no-load wire + eject ramp + DONE flight.
        self.nol = 2 * p.t_rtt + self.max_wire + RAMP
        if p.ha_trackers > 0:
            self.ha = p.ha_trackers
        else:
            self.ha = max(1, math.ceil(_ANCHOR[fabric] * self.nol))

        # Credit initials in the counter's own unit: flits for links and
        # ports, transactions for the HA. Scale touches the BDP only.
        scale = p.ca_credit_scale
        self.init: dict[tuple, int] = {}
        self.cap: dict[tuple, float] = {}  # packets per cycle, for CA-rate
        seen: list[tuple] = []
        for keys in raw.values():
            for k in keys:
                if k in self.init:
                    continue
                seen.append(k)
                kind = k[0]
                if kind == "H":
                    self.init[k] = self.ha
                    self.cap[k] = self.ha / self.nol
                else:
                    bw = self._bw[kind]
                    self.init[k] = max(1, int(round(bw * self.nol * scale)))
                    self.cap[k] = bw / p.m
        self.link_credit = max(self.init[k] for k in self.init if k[0] == "L")
        self.index = {k: i for i, k in enumerate(seen)}
        self.keys = seen
        self.paths: dict[tuple[int, int], tuple[int, ...]] = {}
        self.charge: dict[tuple[int, int], tuple[tuple[int, int], ...]] = {}
        for sd, keys in raw.items():
            # One charge per resource even if a footprint listed it twice.
            uniq = []
            used = set()
            for k in keys:
                i = self.index[k]
                if i in used:
                    continue
                used.add(i)
                amt = 1 if k[0] == "H" else p.m
                uniq.append((i, amt))
            self.paths[sd] = tuple(i for i, _ in uniq)
            self.charge[sd] = tuple(uniq)
        self.arbiter_bits, self.bits_breakdown = self._bits()

    def _bits(self) -> tuple[int, dict[str, int]]:
        """Arbiter state. Token buckets are core-side and listed apart."""
        p = self.p
        b: dict[str, int] = {}
        if p.match == "ca_rate":
            w = 8
            n_voq = self.n * (self.n - 1)
            b["rate_table"] = n_voq * w
            b["accept_pointers"] = self.n * _clog2(self.n)
            b["token_buckets_existing"] = n_voq * 8
        else:
            w = _clog2(max(self.init.values()) + 1)
            n_link = sum(1 for k in self.keys if k[0] != "H")
            b["resource_credits"] = n_link * w
            b["ha_credits"] = self.n * _clog2(self.ha + 1)
            b["rr_pointer"] = _clog2(self.n)
            b["request_regs"] = self.n * p.ca_probe * _clog2(self.n)
        # Existing token buckets are not new silicon. Keep them out of the
        # arbiter total; the breakdown still shows the line.
        total = sum(v for k, v in b.items() if k != "token_buckets_existing")
        return total, b

    def system_bits(self) -> int:
        cfg = "mesh_base" if self.fabric == "mesh" else "ring_base"
        return distributed_cost(cfg, buf_depth=self.p.buf_depth,
                                fifo_depth=self.p.fifo_depth)["bits"] + self.arbiter_bits


def waterfill(flow_res: list[tuple[int, ...]],
              cap: dict[int, float]) -> list[float]:
    """Max-min rates. Every backlogged flow starts at 0 and climbs equally
    until one of its resources is tight, then it freezes."""
    n = len(flow_res)
    rate = [0.0] * n
    if n == 0:
        return rate
    active = [True] * n
    n_act = n
    cnt: dict[int, int] = defaultdict(int)
    res_flows: dict[int, list[int]] = defaultdict(list)
    left = dict(cap)
    for i, fr in enumerate(flow_res):
        for r in fr:
            cnt[r] += 1
            res_flows[r].append(i)
    for _ in range(n + 1):
        if n_act <= 0:
            break
        best = math.inf
        for r, c in cnt.items():
            if c > 0:
                best = min(best, left[r] / c)
        if not math.isfinite(best) or best <= 1e-15:
            break
        for i in range(n):
            if active[i]:
                rate[i] += best
        saturated = [r for r, c in cnt.items() if c > 0 and left[r] - best * c <= 1e-8]
        for r in cnt:
            if cnt[r] > 0:
                left[r] -= best * cnt[r]
        if not saturated:
            break
        for r in saturated:
            for i in res_flows[r]:
                if not active[i]:
                    continue
                active[i] = False
                n_act -= 1
                for rr in flow_res[i]:
                    cnt[rr] -= 1
    return rate


class _AdmitBase:
    scheme = ""

    def __init__(self, fabric: str, p: SteadyParams, seed: int = 0):
        self.fabric = fabric
        self.p = p
        self.fm = FabricMap(fabric, p)
        self.n = N
        self.t = 0
        if fabric == "mesh":
            topo = Topology("mesh")
            topo.sigma = p.sigma
            self.base: Any = MeshBaseSim(topo, p, seed=seed)
        else:
            self.base = RingBaseAdapter(p, seed=seed)
        self.q: list[dict[int, deque]] = [defaultdict(deque) for _ in range(N)]
        self.order: list[deque] = [deque() for _ in range(N)]  # arrival order
        self._n_q = 0
        self._n_wait = 0
        self.pkt_done: list[tuple] = []
        self.st: dict[str, int] = defaultdict(int)
        self._inflight: dict[int, tuple] = {}

    @property
    def bisect(self):
        return self.base.bisect

    @property
    def bisect_busy(self) -> int:
        return self.base.bisect_busy

    def in_network(self) -> int:
        return self.base.in_network()

    def backlog(self) -> int:
        return (self._n_q + self._n_wait) * self.p.m + self.base.backlog()

    def offer(self, src: int, dst: int) -> None:
        res = self.fm.charge[(src, dst)]
        pkt = _Pkt(src, dst, self.t, res)
        self.q[src][dst].append(pkt)
        self.order[src].append(dst)
        self._n_q += 1

    def _send(self, pkt: _Pkt) -> None:
        base = self.base
        if self.fabric == "mesh":
            pid = base._pid
            base.offer(pkt.src, pkt.dst)
        else:
            pid = base.sim._pid
            base.offer(pkt.src, pkt.dst)
        self._inflight[pid] = (pkt.t_gen, pkt.res)

    def _harvest(self) -> None:
        done = self.base.pkt_done
        while done:
            pid, s, d, _tg, t_done = done.pop()
            t_gen, res = self._inflight.pop(pid)
            self.pkt_done.append((pid, s, d, t_gen, t_done))
            self._on_done(t_done, res)

    def _on_done(self, t_done: int, res: tuple) -> None:
        pass

    def step(self) -> None:
        raise NotImplementedError

    def ca_export(self) -> dict[str, Any]:
        t = max(1, self.t)
        return {
            "ca_scheme": self.scheme,
            "nol_rtt": self.fm.nol,
            "max_wire": self.fm.max_wire,
            "ha_trackers_eff": self.fm.ha,
            "link_credit": self.fm.link_credit,
            "arbiter_bits": self.fm.arbiter_bits,
            "arbiter_breakdown": self.fm.bits_breakdown,
            "system_bits": self.fm.system_bits(),
            "datapath_bits": self.fm.system_bits() - self.fm.arbiter_bits,
            "ctrl_msgs_per_cy": round(self.st["ctrl"] / t, 3),
            "n_grants": self.st["grants"],
            "n_defer": self.st["defer"],
        }


class CreditSim(_AdmitBase):
    scheme = "ca_credit"

    def __init__(self, fabric: str, p: SteadyParams, seed: int = 0):
        super().__init__(fabric, p, seed)
        self.credit = [self.fm.init[k] for k in self.fm.keys]
        self.ret: dict[int, list[tuple[int, int]]] = defaultdict(list)
        self.inject_at: dict[int, list[_Pkt]] = defaultdict(list)
        self.sptr = 0

    def _can(self, pkt: _Pkt) -> bool:
        c = self.credit
        return all(c[i] >= a for i, a in pkt.res)

    def _take(self, pkt: _Pkt) -> None:
        c = self.credit
        for i, a in pkt.res:
            c[i] -= a

    def _on_done(self, t_done: int, res: tuple) -> None:
        self.ret[t_done + self.p.t_rtt].append(res)
        self.st["ctrl"] += 1  # DONE

    def step(self) -> None:
        t = self.t
        p = self.p
        for res in self.ret.pop(t, ()):
            for i, a in res:
                self.credit[i] += a
        for pkt in self.inject_at.pop(t, ()):
            self._n_wait -= 1
            self._send(pkt)
        if t % p.ca_period == 0:
            self._epoch(t)
        self.base.step()
        self._harvest()
        self.t = t + 1

    def _epoch(self, t: int) -> None:
        p = self.p
        n = self.n
        for k in range(n):
            s = (self.sptr + k) % n
            order = self.order[s]
            if not order:
                continue
            self.st["ctrl"] += 1  # one request from this source
            probed: list[_Pkt] = []
            # Oldest ca_probe packets. order[] may contain dests whose queue
            # we already granted; those entries are dropped as we see them.
            hops = 0
            while order and len(probed) < p.ca_probe and hops < p.ca_probe * 4:
                hops += 1
                d = order.popleft()
                qq = self.q[s][d]
                if not qq:
                    continue
                probed.append(qq.popleft())
                self._n_q -= 1
            n_acc = 0
            put_back: list[_Pkt] = []
            for pkt in probed:
                if n_acc >= p.grants_per_src:
                    put_back.append(pkt)
                    self._n_q += 1
                    continue
                if self._can(pkt):
                    self._take(pkt)
                    self.inject_at[t + p.t_rtt].append(pkt)
                    self._n_wait += 1
                    n_acc += 1
                    self.st["grants"] += 1
                    self.st["ctrl"] += 1  # grant
                else:
                    self.st["defer"] += 1
                    put_back.append(pkt)
                    self._n_q += 1
            for pkt in reversed(put_back):
                self.q[s][pkt.dst].appendleft(pkt)
                self.order[s].appendleft(pkt.dst)
        self.sptr = (self.sptr + 1) % n


class RateSim(_AdmitBase):
    scheme = "ca_rate"

    def __init__(self, fabric: str, p: SteadyParams, seed: int = 0):
        # FabricMap prices the rate table only when match == ca_rate.
        saved = p.match
        p.match = "ca_rate"
        super().__init__(fabric, p, seed)
        p.match = saved
        self.rate: dict[tuple[int, int], float] = {}
        self.tok: dict[tuple[int, int], float] = defaultdict(float)
        self.activate: dict[int, dict[tuple[int, int], float]] = {}
        self.aptr = [0] * N
        # Static resource-index caps, copied per water-fill.
        self._cap = {self.fm.index[k]: v for k, v in self.fm.cap.items()}

    def step(self) -> None:
        t = self.t
        p = self.p
        if t % p.ca_period == 0:
            self.activate[t + 2 * p.t_rtt] = self._compute()
            self.st["ctrl"] += 2 * self.n
        nxt = self.activate.pop(t, None)
        if nxt is not None:
            self.rate = nxt
            for key in list(self.tok):
                if key not in nxt:
                    del self.tok[key]
        self._integrate()
        self._admit()
        self.base.step()
        self._harvest()
        self.t = t + 1

    def _compute(self) -> dict[tuple[int, int], float]:
        flows: list[tuple[int, int]] = []
        fres: list[tuple[int, ...]] = []
        for s in range(self.n):
            for d, qq in self.q[s].items():
                if qq:
                    flows.append((s, d))
                    fres.append(self.fm.paths[(s, d)])
        rates = waterfill(fres, dict(self._cap))
        return {sd: r for sd, r in zip(flows, rates) if r > 0}

    def _integrate(self) -> None:
        period = self.p.ca_period
        tok = self.tok
        for sd, r in self.rate.items():
            cap = max(1.0, r * period)
            tok[sd] = min(cap, tok.get(sd, 0.0) + r)

    def offer(self, src: int, dst: int) -> None:
        res = self.fm.charge[(src, dst)]
        self.q[src][dst].append(_Pkt(src, dst, self.t, res))
        self._n_q += 1

    def _admit(self) -> None:
        tok = self.tok
        for s in range(self.n):
            sent = 0
            start = self.aptr[s]
            for k in range(self.n):
                if sent >= RAMP_BW:
                    break
                d = (start + k) % self.n
                if d == s:
                    continue
                qq = self.q[s].get(d)
                if not qq:
                    continue
                key = (s, d)
                if tok.get(key, 0.0) < 1.0:
                    continue
                pkt = qq.popleft()
                self._n_q -= 1
                tok[key] -= 1.0
                self._send(pkt)
                sent += 1
                self.st["grants"] += 1
                self.aptr[s] = (d + 1) % self.n


def build_ca(config: str, p: SteadyParams, seed: int = 0):
    fabric, _, kind = config.split("_")
    if kind == "credit":
        return CreditSim(fabric, p, seed=seed)
    if kind == "rate":
        return RateSim(fabric, p, seed=seed)
    raise ValueError(config)


def _selfcheck() -> None:
    # Two flows, one shared link of 1 pkt/cy and a private link of 0.2.
    rates = waterfill([(0,), (0, 1)], {0: 1.0, 1: 0.2})
    assert abs(rates[0] - 0.8) < 1e-6, rates
    assert abs(rates[1] - 0.2) < 1e-6, rates
    # Equal split on one link.
    rates = waterfill([(0,), (0,)], {0: 1.0})
    assert abs(rates[0] - 0.5) < 1e-6 and abs(rates[1] - 0.5) < 1e-6, rates


if __name__ == "__main__":
    _selfcheck()
    print("waterfill ok")
    p = SteadyParams(lam=0.05, warmup=200, measure=400, buf_depth=20,
                     ca_credit_scale=1.0)
    from rg_steady_des import run_steady
    for cfg in ("mesh_ca_credit", "mesh_ca_rate"):
        r = run_steady(cfg, p)
        print(f"{cfg:16} acc={r['accepted']:.3f} stable={r['stable']} "
              f"p50={r['p50']} bits={r['arbiter_bits']} "
              f"nol={r['nol_rtt']} ha={r['ha_trackers_eff']} "
              f"ctrl={r['ctrl_msgs_per_cy']}")
