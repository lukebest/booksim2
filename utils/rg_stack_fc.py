#!/usr/bin/env python3
"""Congestion control on the 3D-stacked fabric: S1 and S16.

S1 (`StackFcSim`) is the specified scheme, ported literally. Its four parts
are unchanged -- detect, propagate, feed back, control -- and so is its
dedicated broadcast bus:

    detect      per station per window, board failures split into `total`
                (any cause) and `net` (only failures caused by traffic
                already on the ring), plus eject deflections. Level is
                `min(7, count // 8)`.
    propagate   a dedicated 3-bit-per-side broadcast bus, never the NoC.
    feed back   every source keeps a table of its 受控节点 -- the stations its
                own flits ride through -- and takes the *max* level over them.
    control     final level = level_of(own total fail - 8 * max received net
                level); AIMD on an integer per-window injection budget.

The only thing that had to change is how 受控节点 is computed. On a single
ring it was `(idx + dir) % n` walked `hops` times. Here a source's flits
cross several rings and a die boundary, so the table is built from the route's
own edge list. That is a mechanical substitution, not a change of policy.

S16 (`StackGrantSim`) is the receiver-driven scheme, and it needed no port at
all: it is `GrantMixin` over this fabric's completer hooks. On this topology
that placement is not merely convenient -- the HA completer sits directly on
the vertical half ring, which is the bottleneck, so the receiver doing the
scheduling is the exact node that owns the scarce resource.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from rg_ring2_fc import (ALPHA_BANDS, BETA_BANDS, LEVEL_STEP, BusMsg,
                         CongestionBus, level_of)
from rg_ring2_grant import GrantMixin, GrantKnobs
from rg_stack_base import Flit, StackBaseParams, StackBaseSim
from rg_stack_topo import StackTopology, Txn


@dataclass
class StackFcParams(StackBaseParams):
    mode: str = "s1"
    window: int = 64              # control window, cycles
    bus_lat: int = 1              # broadcast bus delivery delay
    budget_min: int = 1           # never throttle a station to silence
    band: str = "spec"            # alpha / beta band mapping
    scope: str = "core_only"      # who is rate-controlled
    trace: bool = True


class StackFcSim(StackBaseSim):
    """S1 congestion control over the stacked fabric."""

    def __init__(self, topo: StackTopology,
                 params: StackFcParams | None = None, seed: int = 0):
        self.p: StackFcParams
        super().__init__(topo, params or StackFcParams(), seed=seed)
        p = self.p
        self.bus = CongestionBus(self.n, p.bus_lat)
        self.budget: dict[int, int] = {}
        self.spent: dict[int, int] = defaultdict(int)
        self.demand_win: dict[int, int] = defaultdict(int)
        self.fail_tot: dict[int, int] = defaultdict(int)
        self.fail_net: dict[int, int] = defaultdict(int)
        self.defl_win: dict[int, int] = defaultdict(int)
        self.ok_win: dict[int, int] = defaultdict(int)
        self.cum: dict[int, int] = defaultdict(int)
        # 受控节点: the stations each source's flits ride through. Built from
        # the route, because on this fabric a route is a list of edges across
        # several rings rather than a direction and a hop count.
        self.path_nodes: dict[int, set[int]] = defaultdict(set)
        self._path_seen: set[tuple[int, int, int]] = set()
        self.trace: dict[str, list] = {"t": [], "budget": [], "level": [],
                                       "recv": [], "ok": []}

    # -- who is rate-controlled --------------------------------------------

    def _controlled(self, node: int) -> bool:
        if self.p.scope == "both":
            return True
        return self._is_core[node]

    # -- detection ---------------------------------------------------------

    def _on_board_fail(self, node: int, f: Flit) -> None:
        super()._on_board_fail(node, f)
        self.fail_tot[node] += 1
        if self._fail_cause == "hop_busy":
            # Only occupancy by traffic already on the ring is a congestion
            # signal. An I-tag block or a self-imposed budget denial says
            # nothing about the fabric.
            self.fail_net[node] += 1

    def _deflect(self, f: Flit) -> None:
        self.defl_win[f.dst] += 1
        super()._deflect(f)

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        self.spent[f.src] += 1
        self.ok_win[f.src] += 1
        self.cum[f.src] += 1
        key = (f.src, f.dst, f.plane)
        if key not in self._path_seen:
            self._path_seen.add(key)
            edges = self.topo.edges
            for eid in f.route[1:]:
                self.path_nodes[f.src].add(edges[eid][0])

    # -- control -----------------------------------------------------------

    def _may_inject(self, node: int, plane: int, f: Flit | None = None
                    ) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if f is None:
            return True
        self.demand_win[node] += 1
        if not self._controlled(node):
            return True
        if self.spent[node] >= self.budget.get(node, self.p.window):
            self.st["n_fc_deny"] += 1
            self._deny_cause = "fc_budget"
            return False
        return True

    def _ctrl_deliver(self) -> None:
        self.bus.deliver(self.t)

    def _max_recv_level(self, node: int) -> int:
        """拥塞反馈: max level over this node's 受控节点."""
        best = 0
        for j in self.path_nodes.get(node, ()):
            m = self.bus.view[j]
            best = max(best, m.up, m.down)
            if best >= 7:
                break
        return best

    def _aimd_tick(self) -> None:
        if (self.t % self.p.window) != self.p.window - 1:
            return
        self._broadcast()
        self._update_s1()
        for d in (self.spent, self.fail_tot, self.fail_net, self.defl_win,
                  self.ok_win, self.demand_win):
            d.clear()

    def _broadcast(self) -> None:
        for i in range(self.n):
            self.bus.post(self.t, i, BusMsg(
                up=level_of(self.fail_net[i]),
                down=level_of(self.defl_win[i]),
                ok=self.ok_win[i], demand=self.demand_win[i],
                active=int(bool(self.ok_win[i] or self.demand_win[i])),
                cum=self.cum[i]))

    def _update_s1(self) -> None:
        p = self.p
        alpha, beta = ALPHA_BANDS[p.band], BETA_BANDS[p.band]
        rec_b, rec_l, rec_r, rec_ok = [], [], [], []
        for i in range(self.n):
            recv = self._max_recv_level(i)
            # 流量控制: take the node's own worst side, subtract what the path
            # is already reporting, then re-bucket. Only levels travel on the
            # bus, so a received level is turned back into a count at its
            # bucket's lower edge.
            own = max(self.fail_tot[i], self.defl_win[i])
            final = level_of(max(0, own - LEVEL_STEP * recv))
            if self._controlled(i):
                b = self.budget.get(i, p.window)
                if final > 0:
                    a = (alpha["lo"] if final <= 2 else
                         alpha["mid"] if final <= 5 else alpha["hi"])
                    b = max(p.budget_min, int(b * a))
                    self.st["n_aimd_decrease"] += 1
                else:
                    g = (beta["clear"] if recv == 0 else
                         beta["lo"] if recv <= 2 else beta["hi"])
                    b = min(p.window, b + g)
                    self.st["n_aimd_increase"] += 1
                self.budget[i] = b
            if self._is_core[i]:
                rec_b.append(self.budget.get(i, p.window))
                rec_l.append(final)
                rec_r.append(recv)
                rec_ok.append(self.ok_win[i])
        if p.trace:
            self.trace["t"].append(self.t)
            self.trace["budget"].append(rec_b)
            self.trace["level"].append(rec_l)
            self.trace["recv"].append(rec_r)
            self.trace["ok"].append(rec_ok)

    # -- reporting ---------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        cs = [i for i in range(self.n) if self._controlled(i)]
        out: dict[str, Any] = {
            "mode": self.p.mode, "window": self.p.window,
            "band": self.p.band, "scope": self.p.scope,
            "bus_lat": self.p.bus_lat,
            "bus_posts": self.bus.n_posts,
            "bus_bits": self.bus.n_posts * self.bus.bits_per_post("s1"),
            "n_fc_deny": self.st.get("n_fc_deny", 0),
            "n_aimd_increase": self.st.get("n_aimd_increase", 0),
            "n_aimd_decrease": self.st.get("n_aimd_decrease", 0),
            "n_controlled_nodes": len(cs),
            "mean_path_nodes": round(
                sum(len(self.path_nodes.get(i, ())) for i in cs)
                / max(1, len(cs)), 1),
            "final_budget": {str(i): self.budget.get(i, self.p.window)
                             for i in cs},
        }
        if self.p.trace and self.trace["t"]:
            n_win = len(self.trace["t"])
            nc = len(self.trace["budget"][0]) if self.trace["budget"] else 0
            out["trace"] = {k: v for k, v in self.trace.items() if v}
            out["mean_budget"] = {
                str(i): round(sum(w[i] for w in self.trace["budget"]) / n_win,
                              2) for i in range(nc)}
            out["mean_level"] = {
                str(i): round(sum(w[i] for w in self.trace["level"]) / n_win,
                              3) for i in range(nc)}
            out["mean_recv_level"] = {
                str(i): round(sum(w[i] for w in self.trace["recv"]) / n_win,
                              3) for i in range(nc)}
        return out


@dataclass
class StackGrantParams(StackBaseParams, GrantKnobs):
    """Baseline stacked fabric plus the receiver's scheduling knobs."""


class StackGrantSim(GrantMixin, StackBaseSim):
    """S16 over the stacked fabric: the HA completer paces DBIDResp.

    The HA sits on the vertical half ring, which the capacity model shows is
    the binding resource. So the node deciding who may send write data is the
    node that owns the contended link -- no side channel has to carry that
    information anywhere.
    """

    def __init__(self, topo: StackTopology,
                 params: StackGrantParams | None = None, *,
                 seed: int = 0) -> None:
        super().__init__(topo, params or StackGrantParams(), seed=seed)
        self._grant_init()

    def _emit_dbid(self, txn: Txn) -> None:
        self._emit(txn, "dbid", txn.ha, txn.core, 1,
                   self.t + self.p.t_ha_service)


@dataclass
class StackTurnParams(StackBaseParams):
    """S17: bounded-patience arbitration at the attach point."""

    # Cycles the turn FIFO may be denied its outgoing slot by pass-through
    # traffic before it wins one. 0 disables the scheme (pure in-ring
    # priority); a large value degenerates to the baseline.
    turn_patience: int = 8
    # Flits one edge may latch to hand the turn FIFO its slot. This is the
    # entire hardware cost of the scheme, and it is bounded by construction.
    yield_depth: int = 1
    trace: bool = True


class StackFairTurnSim(StackBaseSim):
    """S17: give the turn FIFO a bounded-patience slot at the attach point.

    Why this and not a rate controller. The measured root cause of per-core
    unfairness here is not a receiver and not a rate: the vertical half rings
    carry two attach points per row gap, *adjacent*, so the second one is
    permanently downstream of the first. Absolute in-ring priority means the
    downstream attach point may only use the slots the upstream one leaves,
    and because a horizontal ring crosses every column at the same position,
    that disadvantage is coherent over all 8 columns and cannot average out
    over destinations.

    Telling the advantaged core to slow down (S1) or withholding its grant
    (S16) both give the slot back to the fabric, not to the starved node --
    in-ring priority hands it to the next pass-through flit instead. The
    minimal fix is local: at the attach point, cap how long the turn FIFO can
    be denied. One counter and one flit of latch per outgoing edge, no
    broadcast bus, no new packet type, no slot schedule to distribute.
    """

    def __init__(self, topo: StackTopology,
                 params: StackTurnParams | None = None, seed: int = 0):
        self.p: StackTurnParams
        super().__init__(topo, params or StackTurnParams(), seed=seed)
        # (node, ring) -> consecutive cycles its turn FIFO wanted the slot
        # and pass-through traffic took it.
        self.turn_wait: dict[Any, int] = defaultdict(int)
        self.st["n_turn_yield"] = 0
        self.st["n_turn_win"] = 0
        self._yield_now: set[Any] = set()

    def _turn_wants(self, node: int, ring: Any) -> Flit | None:
        q = self.xq.get((node, ring))
        return q[0] if q else None

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        """Let a starved turn FIFO pre-empt a pass-through flit for one cycle."""
        if inring and self.p.turn_patience > 0 and f.ring is not None:
            eid = self._next_edge(f)
            rk = self.topo.edge_ring[eid]
            if rk == f.ring:                    # a genuine pass-through
                key = (f.node, rk)
                if self.turn_wait[key] >= self.p.turn_patience:
                    head = self._turn_wants(f.node, rk)
                    if head is not None and head.vc == f.vc \
                            and self.inring_hold[(eid, f.vc)] \
                            < self.p.yield_depth:
                        self.st["n_turn_yield"] += 1
                        self._yield_now.add(key)
                        self._hold(f, (eid, f.vc))
                        return False
        return super()._launch(f, inring=inring)

    def _drain_xfer(self) -> None:
        """Drain the transfer FIFOs, ageing only the ones that lost the slot.

        The counter has to mean "consecutive cycles this FIFO wanted its
        outgoing slot and pass-through traffic took it". Ageing a FIFO that
        drained successfully makes every queue look starved, so the yield
        fires everywhere and buys nothing.
        """
        for key in list(self.active_xq):
            q = self.xq[key]
            if not q:
                self.active_xq.pop(key, None)
                self.turn_wait.pop(key, None)
                continue
            if self._launch(q[0], inring=False):
                q.popleft()
                self.turn_wait[key] = 0
                if key in self._yield_now:
                    self.st["n_turn_win"] += 1
                if not q:
                    self.active_xq.pop(key, None)
                    self.turn_wait.pop(key, None)
            else:
                self.st["n_turn_board_fail"] += 1
                self.turn_wait[key] += 1
        self._yield_now.clear()

    def fc_summary(self) -> dict[str, Any]:
        return {
            "mode": "s17",
            "turn_patience": self.p.turn_patience,
            "yield_depth": self.p.yield_depth,
            "bus_posts": 0, "bus_bits": 0,
            "n_turn_yield": self.st["n_turn_yield"],
            "n_turn_win": self.st["n_turn_win"],
            "max_inring_hold": self.st["max_inring_hold"],
            # One counter per (attach point, outgoing ring) plus one latch
            # per outgoing edge is the whole cost.
            "n_counters": len(self.turn_wait),
            "latch_flits": self.st["max_inring_hold"],
        }


@dataclass
class StackAdaptParams(StackBaseParams):
    """S18: each core discovers its own outstanding limit at run time.

    The setting a core wants is the bandwidth-delay product: enough writes in
    flight to cover the round trip, and not one more. Too few and the core
    idles waiting for Comp; too many and the surplus only lengthens queues,
    which on this fabric means deflections, a fuller turn FIFO and -- once the
    completers saturate -- RetryAcks that park requests without retiring them.
    Neither end of that trade-off is knowable at design time, because the
    round trip depends on what every other core is doing.

    So the window is not configured, it is measured. The core already knows
    when it issued each write and when Comp came back; the shortest round trip
    it has ever seen is its uncongested baseline, and anything above that is
    queueing it is itself paying for.
    """

    win_min: int = 1
    win_max: int = 600
    win_init: int = 4
    # Queueing the core will tolerate before it backs off, as a fraction of
    # its own best observed round trip. Larger keeps more in flight and finds
    # more bandwidth; smaller holds latency down.
    rtt_slack: float = 0.5
    win_ai: float = 1.0        # additive step, once queueing is visible
    win_mi: float = 2.0        # geometric step while clearly uncongested
    # Fraction of the slack below which the fabric counts as uncongested and
    # the window may grow geometrically.
    slow_start_frac: float = 0.25
    win_md: float = 0.5        # proportional back-off gain
    md_max: float = 0.5        # cap on one back-off, so it cannot collapse
    # A RetryAck is a hard signal from the completer rather than an inference,
    # so it halves the window outright -- but only once per refractory period,
    # or a burst of bounces would drive every core to the floor together.
    retry_md: float = 0.5
    refractory: int = 64
    trace: bool = True


class StackAdaptSim(StackBaseSim):
    """S18: delay-driven adaptive outstanding, one window per core.

    Control loop, entirely local to the requester:

        measure     round trip from the cycle the REQ boards to the cycle Comp
                    retires, per transaction. `rtt_min` is the running best.
        decide      target = rtt_min * (1 + rtt_slack). Below target the
                    fabric has room, above it the core is queueing.
        act         below target, add `win_ai` per window of clean
                    completions; above it, back off in proportion to the
                    excess. A RetryAck halves the window immediately.

    Cost: per core, a window register, an rtt_min register, an accumulator,
    and the issue timestamp per outstanding entry that a retry timeout needs
    anyway. No broadcast bus, no new packet type, no completer change, and
    nothing to configure per scenario -- which is the point, since the right
    concurrency differs between scenarios and even between phases of one.
    """

    def __init__(self, topo: StackTopology,
                 params: StackAdaptParams | None = None, seed: int = 0):
        self.pa = params or StackAdaptParams()
        super().__init__(topo, self.pa, seed=seed)
        self.win: dict[int, float] = defaultdict(
            lambda: float(self.pa.win_init))
        self.rtt_min: dict[int, int] = {}
        self._acc: dict[int, float] = defaultdict(float)
        self._last_cut: dict[int, int] = defaultdict(lambda: -10**9)
        self.st["n_win_cut"] = 0
        self.st["n_win_raise"] = 0
        self.st["n_retry_cut"] = 0
        self._win_sum = 0.0
        self._win_n = 0
        self._win_trace: list[tuple[int, float]] = []

    def _clamp(self, core: int, w: float) -> None:
        self.win[core] = min(float(self.pa.win_max),
                             max(float(self.pa.win_min), w))

    def _outst_full(self, core: int) -> bool:
        return self.core_outst[core] >= max(1, int(self.win[core]))

    def _on_retry_at_requester(self, txn: Txn) -> None:
        c = txn.core
        if self.t - self._last_cut[c] < self.pa.refractory:
            return
        self._last_cut[c] = self.t
        self._clamp(c, self.win[c] * self.pa.retry_md)
        self._acc[c] = 0.0
        self.st["n_retry_cut"] += 1

    def _on_txn_done(self, txn: Txn, last: Flit) -> None:
        c = txn.core
        t0 = self.wr_tinj.get(txn.txn_id)
        if t0 is None:
            return
        rtt = self.t - t0
        best = self.rtt_min.get(c)
        if best is None or rtt < best:
            self.rtt_min[c] = best = rtt
        target = best * (1.0 + self.pa.rtt_slack)
        if rtt <= target:
            self._acc[c] += 1.0
            if self._acc[c] >= max(1.0, self.win[c]):
                self._acc[c] = 0.0
                # Two regimes, for the same reason TCP has two. The right
                # window differs between scenarios by more than an order of
                # magnitude -- five when every die is writing, thirty-odd when
                # one is -- and an additive ramp cannot cross that range
                # before the phase it is measuring has ended. So while the
                # round trip is still close to the uncongested baseline the
                # window grows geometrically, and it only switches to the
                # careful additive step once queueing starts to show.
                if rtt <= best * (1.0 + self.pa.slow_start_frac
                                  * self.pa.rtt_slack):
                    self._clamp(c, self.win[c] * self.pa.win_mi)
                else:
                    self._clamp(c, self.win[c] + self.pa.win_ai)
                self.st["n_win_raise"] += 1
        else:
            if self.t - self._last_cut[c] < self.pa.refractory:
                return
            self._last_cut[c] = self.t
            excess = (rtt - target) / max(1, rtt)
            factor = max(1.0 - self.pa.md_max,
                         1.0 - self.pa.win_md * excess)
            self._clamp(c, self.win[c] * factor)
            self._acc[c] = 0.0
            self.st["n_win_cut"] += 1

    def _sample_concurrency(self) -> None:
        super()._sample_concurrency()
        if self.win:
            m = sum(self.win.values()) / len(self.win)
            self._win_sum += m
            self._win_n += 1
            if self.pa.trace and self.t % 100 == 0:
                self._win_trace.append((self.t, round(m, 2)))

    def fc_summary(self) -> dict[str, Any]:
        wins = {c: round(v, 2) for c, v in sorted(self.win.items())}
        vals = list(wins.values()) or [0.0]
        rtts = list(self.rtt_min.values()) or [0]
        return {
            "mode": "s18",
            "rtt_slack": self.pa.rtt_slack,
            "win_init": self.pa.win_init,
            "win_mean_final": round(sum(vals) / len(vals), 2),
            "win_min_final": min(vals),
            "win_max_final": max(vals),
            "win_mean_overtime": round(self._win_sum / max(1, self._win_n), 2),
            "rtt_min_mean": round(sum(rtts) / len(rtts), 1),
            "rtt_min_lo": min(rtts),
            "rtt_min_hi": max(rtts),
            "n_win_cut": self.st["n_win_cut"],
            "n_win_raise": self.st["n_win_raise"],
            "n_retry_cut": self.st["n_retry_cut"],
            "win_by_core": wins,
            # window + rtt_min + accumulator per core; timestamps per
            # outstanding entry are already needed for retry timeouts
            "n_registers": 3 * len(wins),
            "trace": self._win_trace,
        }


@dataclass
class StackAdaptTurnParams(StackTurnParams, StackAdaptParams):
    """S19 = S18 + S17. Two independent problems, two independent fixes."""


class StackAdaptTurnSim(StackFairTurnSim, StackAdaptSim):
    """S19: adaptive concurrency at the source, fair arbitration at the turn.

    The two defects this fabric has are not the same defect, and neither fix
    addresses the other. Too much concurrency causes collapse, and it is
    fixed at the requester by sizing the window to the round trip. The
    positional advantage of the upstream attach point causes per-core
    unfairness, and it is fixed at the attach point by capping how long the
    turn FIFO can be denied. Composing them costs the sum of two small costs
    and there is no interaction to tune, because one acts on how much traffic
    exists and the other on which flit goes first.
    """

    def __init__(self, topo: StackTopology,
                 params: StackAdaptTurnParams | None = None, seed: int = 0):
        super().__init__(topo, params or StackAdaptTurnParams(), seed=seed)

    def fc_summary(self) -> dict[str, Any]:
        out = StackAdaptSim.fc_summary(self)
        out.update({k: v for k, v in StackFairTurnSim.fc_summary(self).items()
                    if k != "mode"})
        out["mode"] = "s19"
        return out
