#!/usr/bin/env python3
"""S22 on the 3D-stacked fabric: deficit-triggered yielding, ported.

What carries over unchanged
---------------------------
The mechanism is the one measured on the single ring (`rg_ring2_dfc.py`) and
its thesis is unchanged: *nothing is ever withheld from a free slot*. Each
member counts the DAT flits it boards, drives that count onto the same 6-bit
broadcast bus S1 uses, and every member holds the resulting table. A member
whose count is below the table mean by more than `dfc_thresh` asserts a
request; a member that is *not* behind stands aside for a requester whose hop
it would otherwise take. A yield hands the slot straight to somebody who is
behind, so progress is equalised without giving up hops, and `dfc_hold` bounds
how long one request may block.

What the stacked fabric forces to change
----------------------------------------
1. **Geometry.** The ring simulator decides "does this flit ride past node
   `h`" with `((h - idx) * dir) % n >= target`. Here a route is an explicit
   edge list that crosses a top ring, a die boundary, a horizontal ring and a
   vertical ring, so the test is done against the flit's actual span: the
   source nodes of the run of edges it is about to take on the ring it is
   entering. That set is exact -- these are precisely the outgoing hops the
   flit will occupy -- and it is narrower than `StackBaseSim._crosses_hop`,
   which walks a whole revolution and therefore degenerates to "same ring".

2. **Who a member is.** `dfc_grain` picks between the ring's original
   per-core accounting and per-top-die-group accounting. The second is the
   granularity the group study is scored on, and it shrinks the bus table
   from 60 entries to 6.

3. **Where the actuator can bite.** A top-die ring carries exactly one
   group, so under `dfc_grain="group"` the yield is structurally inert on it:
   the yielding core and the crossed core always belong to the same member.
   Inter-group contention on this fabric is downstream -- the D2D bridges and
   the bottom-die rings -- and the only injection ports there that hold
   traffic for several groups at once belong to the HAs. So the group-grain
   actuator is the *destination-ordered dodge* at those ports: among the
   flits already queued, board the one whose group is behind. That is still
   a reordering and never a withholding, and for writes it lands on
   `DBIDResp`, which is the CHI grant that decides whose WriteData may follow.

   The two grains are therefore not variants of one setting but two places
   to stand, and both are swept:

     * `core`  -- yield on the top-die rings, equalising cores within a die.
     * `group` -- dodge at the HAs, equalising the six groups.

4. **Attribution.** A DAT flit is charged to the core end of its
   transaction: the source for WriteData, the destination for CompData. That
   is the same convention the per-group bandwidth metric uses, so the
   controller and the score cannot disagree about who made progress.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import islice
from typing import Any

from rg_stack_base import Flit, StackBaseParams, StackBaseSim
from rg_stack_topo import StackTopology

# Window counts ride the bus at S1's width: 6 bits, saturating.
BUS_BITS = 6
BUS_MAX = (1 << BUS_BITS) - 1


@dataclass
class StackDfcParams(StackBaseParams):
    mode: str = "s22"
    dfc_window: int = 64          # control window, cycles
    dfc_bus_lat: int = 30         # the same bus delay S1 is charged
    dfc_thresh: float = 2.0       # deficit (flits) before requesting a yield
    dfc_clear: float = 0.0        # deficit at which a requester stands down
    dfc_hold: int = 8             # cycles a request may block before standing
                                  # down; 0 = never expire
    dfc_backoff: int = 0          # cycles a stood-down request stays quiet
    dfc_cap: float = 64.0         # clamp on the accumulated deficit
    dfc_margin: float = 0.0       # how much further behind a requester must be
    # Per-cycle entitlement for the bus-free variant. The equal share is a
    # topology constant, so a node can accrue it locally instead of learning
    # it from the bus: no posts, no 30-cycle delay, and the signal is exact
    # rather than stale. 0 keeps the bus-derived mean.
    dfc_target: float = 0.0
    # "core" -> one member per AI core (the ring study's accounting).
    # "group" -> one member per top die, which is what the study scores.
    dfc_grain: str = "core"
    # Which nodes may act on a request. Cores act by yielding on their own
    # ring; HAs act by reordering the responses they already hold.
    dfc_scope_nodes: str = "core_only"     # "core_only" | "ha_only" | "both"
    # VCs whose boarding counts as progress.
    dfc_vcs: tuple[str, ...] = ("dat",)
    # VCs the actuator may reorder or hold back. Adding "rsp" lets an HA
    # order DBIDResp by group, which is the grant that gates WriteData.
    # Adding "req" is what gives a *core* a lever on a read batch, where the
    # DAT it is credited with is issued by the HA and never passes its own
    # inject port: without it the core-grain actuator degenerates to S0.
    dfc_act_vcs: tuple[str, ...] = ("dat",)
    # How many entries past the head the inject arbiter may look for a flit
    # that serves a requester instead. Yielding by idling wastes the
    # yielder's own outgoing hop, and every hot hop here is already near
    # saturation, so that waste is exactly what makes fairness cost
    # bandwidth. 0 disables the look-ahead.
    dfc_dodge: int = 0
    # With this on, any flit whose member is not behind is a dodge candidate,
    # so the look-ahead hunts for one whose member *is*. This is what makes
    # the dodge work on destination rather than on geometry, and it is the
    # only actuator the group grain has. Off, the ring study's rule applies:
    # only a flit that takes a requester's hop is worth dodging around.
    dfc_dest_pref: bool = True


class StackDfcSim(StackBaseSim):
    """S0 data plane plus deficit-triggered yielding and dodging."""

    def __init__(self, topo: StackTopology,
                 params: StackDfcParams | None = None, seed: int = 0):
        self.p: StackDfcParams
        super().__init__(topo, params or StackDfcParams(), seed=seed)
        p = self.p
        # node -> member id, -1 for a node that is nobody's proxy
        self._member = [-1] * self.n
        for c in topo.cores:
            self._member[c] = c if p.dfc_grain == "core" else topo.nodes[c].die
        self._member_ids = sorted({m for m in self._member if m >= 0})
        self._is_ha = [nd.role == "ha" for nd in topo.nodes]

        self.ok_win: dict[int, int] = defaultdict(int)
        self.deficit: dict[int, float] = defaultdict(float)
        self.req: set[int] = set()
        self.req_t: dict[int, int] = {}
        self.quiet_until: dict[int, int] = {}
        # Cumulative boarded count per member, as seen on the bus. Every node
        # holds the same table; its own entry comes off the bus too, so both
        # sides of the comparison have crossed the same quantiser and delay.
        self.cum_bus: dict[int, int] = defaultdict(int)
        self._pipe: dict[int, dict[int, int]] = defaultdict(dict)
        self.bus_posts = 0
        self._span: dict[Any, frozenset[int]] = {}
        # Activity counters, not event counts: the free-slot arbiter looks at
        # a candidate once to order the group and again to board it, so one
        # boarding attempt can bump these more than once.
        self.st["n_dfc_yield"] = 0
        self.st["n_dfc_req"] = 0
        self.st["n_dfc_dodge"] = 0
        self.trace: dict[str, list] = {"t": [], "deficit": [], "ok": []}

    # -- membership ---------------------------------------------------------

    def _flit_member(self, f: Flit) -> int:
        """The core end of this flit's transaction.

        WriteData is charged to its source, CompData and the responses to
        their destination -- in both cases the AI core whose progress the
        flit represents.
        """
        m = self._member[f.src]
        return m if m >= 0 else self._member[f.dst]

    def _acts_here(self, node: int) -> bool:
        scope = self.p.dfc_scope_nodes
        if scope == "both":
            return self._is_core[node] or self._is_ha[node]
        if scope == "ha_only":
            return self._is_ha[node]
        return self._is_core[node]

    def _actor_member(self, node: int, f: Flit) -> int:
        """Whose progress this boarding would count towards.

        At a core that is the core itself; at an HA it is the group the
        response is headed for, because the HA injects on that group's behalf.
        """
        m = self._member[node]
        return m if m >= 0 else self._flit_member(f)

    # -- geometry -----------------------------------------------------------

    def _span_nodes(self, f: Flit) -> frozenset[int]:
        """Outgoing hops this flit will occupy on the ring it is entering.

        Only ever asked about a queued flit, so the route index is 0 and
        there is no deflection lap to fold in. The run stops where the flit
        leaves the ring: hops beyond that are somebody else's contention.
        """
        got = self._span.get(f.route)
        if got is not None:
            return got
        topo = self.topo
        if not f.route:
            got = frozenset()
        else:
            ring = topo.edge_ring[f.route[0]]
            out = []
            for eid in f.route:
                if topo.edge_ring[eid] != ring:
                    break
                out.append(topo.edges[eid][0])
            got = frozenset(out)
        self._span[f.route] = got
        return got

    def _takes_requester_hop(self, f: Flit, node: int) -> bool:
        """Would boarding this flit spend a hop a needier member is waiting on?"""
        m = self._actor_member(node, f)
        floor = self.deficit[m] + self.p.dfc_margin
        for u in self._span_nodes(f):
            if u == node:
                continue
            mu = self._member[u]
            if mu < 0 or mu == m or mu not in self.req:
                continue
            if self.deficit[mu] < floor:
                continue
            return True
        return False

    def _at_others_expense(self, f: Flit, node: int) -> bool:
        """Is boarding `f` a slot spent on somebody who is not behind?"""
        if self._flit_member(f) in self.req:
            return False
        if self.p.dfc_dest_pref:
            return True
        return self._takes_requester_hop(f, node)

    # -- the actuators ------------------------------------------------------

    def _itag_blocks(self, f: Flit, node: int) -> bool:
        """Base I-tag first, then yield to any requester this flit rides past.

        The yield is deliberately one-sided: a member that is behind never
        yields, so a slot given up always lands with somebody who needs it.
        Only the geometric test can block -- withholding a slot merely
        because the destination is ahead would be pacing, which is the thing
        this scheme exists to avoid.
        """
        if super()._itag_blocks(f, node):
            return True
        if not self.req or f.vc not in self.p.dfc_act_vcs:
            return False
        if not self._acts_here(node):
            return False
        if self._actor_member(node, f) in self.req:
            return False
        if self._takes_requester_hop(f, node):
            self.st["n_dfc_yield"] += 1
            return True
        return False

    def _select_inject_flit(self, node: int, plane: int, q) -> Flit | None:
        """FIFO head, unless a flit just behind it serves a needier member.

        Only a flit for a *different* destination may overtake, so
        per-destination order is untouched and two flits of one WriteData
        burst can never swap. If nothing better is queued the head boards
        anyway: the look-ahead reorders, it never withholds.
        """
        head = q[0] if q else None
        d = self.p.dfc_dodge
        if head is None or not d or not self.req:
            return head
        if head.vc not in self.p.dfc_act_vcs or not self._acts_here(node):
            return head
        if not self._at_others_expense(head, node):
            return head
        skipped = {head.dst}
        for f in islice(q, 1, 1 + d):
            if f.dst in skipped:
                break
            if f.vc == head.vc and not self._at_others_expense(f, node):
                self.st["n_dfc_dodge"] += 1
                return f
            skipped.add(f.dst)
        return head

    # -- accounting ---------------------------------------------------------

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if f.vc not in self.p.dfc_vcs:
            return
        m = self._flit_member(f)
        if m < 0:
            return
        self.ok_win[m] += 1
        # Boarding is what the request was for, so charge it against the
        # shortfall now rather than at the next window: a member that has
        # caught up must stop costing its neighbours slots this cycle.
        self.deficit[m] -= 1.0
        if m in self.req and self.deficit[m] <= self.p.dfc_clear:
            self._stand_down(m)

    def _stand_down(self, m: int) -> None:
        self.req.discard(m)
        self.req_t.pop(m, None)
        if self.p.dfc_backoff:
            self.quiet_until[m] = self.t + self.p.dfc_backoff

    # -- control ------------------------------------------------------------

    def _ctrl_deliver(self) -> None:
        if self.p.dfc_target > 0:
            self._accrue()
        else:
            due = self._pipe.pop(self.t, None)
            if due:
                for m, c in due.items():
                    self.cum_bus[m] += c
                self._reprice()
        if not self.p.dfc_hold:
            return
        # A request cannot stop transit, so a requester starved by transit
        # rather than by injection would otherwise idle its upstream
        # neighbours indefinitely.
        for m in [m for m in self.req
                  if self.t - self.req_t.get(m, self.t) >= self.p.dfc_hold]:
            self._stand_down(m)

    def _rearm(self, m: int) -> None:
        p = self.p
        if (self.deficit[m] >= p.dfc_thresh and m not in self.req
                and self.t >= self.quiet_until.get(m, 0)):
            self.req.add(m)
            self.req_t[m] = self.t
            self.st["n_dfc_req"] += 1
        elif self.deficit[m] <= p.dfc_clear and m in self.req:
            self._stand_down(m)

    def _accrue(self) -> None:
        """Bus-free deficit: entitlement accrues at `dfc_target` every cycle.

        The board path already spends it, so this only adds the credit side.
        `dfc_cap` bounds it both ways, which stops a member that has been
        starved for a long stretch from holding a standing request forever.
        """
        p = self.p
        for m in self._member_ids:
            self.deficit[m] = min(p.dfc_cap, self.deficit[m] + p.dfc_target)
            self._rearm(m)

    def _reprice(self) -> None:
        """Recompute every deficit from the freshly delivered table."""
        p = self.p
        ids = self._member_ids
        if not ids:
            return
        mean = sum(self.cum_bus[m] for m in ids) / len(ids)
        for m in ids:
            self.deficit[m] = max(-p.dfc_cap,
                                  min(p.dfc_cap, mean - self.cum_bus[m]))
            self._rearm(m)

    def _aimd_tick(self) -> None:
        p = self.p
        if (self.t % p.dfc_window) != p.dfc_window - 1:
            return
        rec_d, rec_ok = [], []
        for m in self._member_ids:
            if p.dfc_target <= 0:      # the bus-free variant posts nothing
                self._pipe[self.t + p.dfc_bus_lat][m] = min(BUS_MAX,
                                                            self.ok_win[m])
                self.bus_posts += 1
            rec_d.append(round(self.deficit[m], 2))
            rec_ok.append(self.ok_win[m])
        self.ok_win.clear()
        self.trace["t"].append(self.t)
        self.trace["deficit"].append(rec_d)
        self.trace["ok"].append(rec_ok)

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        p = self.p
        ids = self._member_ids
        n_win = max(1, len(self.trace["t"]))
        return {
            "mode": "s22", "window": p.dfc_window, "bus_lat": p.dfc_bus_lat,
            "thresh": p.dfc_thresh, "clear": p.dfc_clear, "hold": p.dfc_hold,
            "backoff": p.dfc_backoff, "grain": p.dfc_grain,
            "n_members": len(ids),
            "tracked_vcs": list(p.dfc_vcs), "act_vcs": list(p.dfc_act_vcs),
            "nodes": p.dfc_scope_nodes, "target": p.dfc_target,
            "dodge": p.dfc_dodge, "dest_pref": p.dfc_dest_pref,
            "margin": p.dfc_margin,
            "bus_posts": self.bus_posts,
            "bus_bits": self.bus_posts * BUS_BITS,
            "bus_width_bits": BUS_BITS,
            "table_entries": len(ids),
            "n_dfc_req": self.st["n_dfc_req"],
            "n_dfc_yield": self.st["n_dfc_yield"],
            "n_dfc_dodge": self.st["n_dfc_dodge"],
            "final_deficit": {str(m): round(self.deficit[m], 2) for m in ids},
            "mean_abs_deficit": round(
                sum(abs(v) for w in self.trace["deficit"] for v in w)
                / max(1, n_win * len(ids)), 3),
        }

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["dfc"] = True
        return out
