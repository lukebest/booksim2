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
                self.active_xq.discard(key)
                self.turn_wait.pop(key, None)
                continue
            if self._launch(q[0], inring=False):
                q.popleft()
                self.turn_wait[key] = 0
                if key in self._yield_now:
                    self.st["n_turn_win"] += 1
                if not q:
                    self.active_xq.discard(key)
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
