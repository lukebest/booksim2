#!/usr/bin/env python3
"""S29: proactive scheduled reservation -- the TDMA / Fastpass family.

Every other scheme in this study is *reactive*: something is measured, a
signal is derived from it, and the fabric responds. There is a whole family
that refuses that loop on principle. Fastpass has a central arbiter assign
each packet a timeslot before it is sent; ExpressPass paces credits so that
data never has to contend; classical TDMA and token rings hand out the medium
on a fixed schedule. The claim is that congestion never forms, so nothing has
to be detected, and the worst case is a schedule property rather than a
control-loop property.

On a synchronous ring this family is unusually cheap to express, because the
nodes already share a clock. A frame of `tdma_slot x n_cores` cycles is cut
into one sub-slot per core. During core `c`'s sub-slot, `c` owns right of way
on its own two outgoing hops: any other node about to board a flit that would
ride over one of those hops stands aside. Nothing is withheld from a free
slot -- the actuator is the same one-sided yield S22 uses, so the *only*
difference between S22 and S29 is where the yield decision comes from:

    S22   yield to whoever the bus says is behind      (reactive, measured)
    S29   yield to whoever the calendar says owns now   (proactive, scheduled)

Keeping the actuator identical is deliberate. It is what makes the pair a
clean test of the schedule-versus-feedback question instead of a comparison
of two different arbiters.

Blind and demand-gated variants
-------------------------------
A pure calendar has one well-known defect: a slot reserved for an idle owner
is simply lost, and on a fabric whose binding hop is already ~97% occupied a
lost slot is lost bandwidth. `tdma_mode` selects which version is measured.

  * `"blind"` -- textbook TDMA. The owner's sub-slot is enforced whether or
     not it has anything to send. No signal of any kind; the schedule is the
     entire mechanism.
  * `"demand"` (default) -- the reservation variant. Each core drives a
     single bit, "I have write data queued", onto the same broadcast bus S1
     and S22 use, at the same fixed `tdma_bus_lat` cycles. Only a core that
     has asserted demand is granted its sub-slot. This is 20 bits per window
     against S1's 120 and S28's 240, and it is what turns the calendar from
     TDMA into a reservation scheme.

`tdma_dodge` is the same look-ahead S22 gets: rather than idling its own
outgoing hop, a yielding node may board a flit from a little further down its
queue that turns off the ring before the owner's hop. Both schemes get it, for
the same reason -- otherwise the comparison measures the look-ahead and not
the trigger.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import Dir, PlaneId, Ring2Topology, Txn, is_core


@dataclass
class Ring2TdmaParams(Ring2BaseParams):
    # Cycles one core owns its hops for. The frame is this times the number
    # of cores, so 8 gives an 80-cycle frame against the 100-cycle fairness
    # window -- every core is guaranteed right of way inside every window.
    tdma_slot: int = 8
    tdma_mode: str = "demand"          # "demand" | "blind"
    tdma_bus_lat: int = 30             # the bus delay every bus scheme pays
    tdma_window: int = 64              # how often the demand bit is refreshed
    # Queue depth (flits) at which a core counts as having demand. 1 = any
    # queued WriteData at all.
    tdma_demand_min: int = 1
    tdma_vcs: tuple[str, ...] = ("dat",)
    # Look-ahead depth for a yielding node, identical to S22's `dfc_dodge`.
    tdma_dodge: int = 8


class Ring2TdmaSim(Ring2BaseSim):
    """S0 data plane plus a calendar-driven, one-sided hop yield."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2TdmaParams | None = None, seed: int = 0):
        self.p: Ring2TdmaParams
        super().__init__(topo, params or Ring2TdmaParams(), seed=seed)
        self._cores: tuple[int, ...] = tuple(topo.cores)
        self.frame = max(1, self.p.tdma_slot) * len(self._cores)
        # Demand bits as read off the bus, and the pipeline carrying them.
        self.demand: set[int] = set(self._cores)
        self._pipe: dict[int, set[int]] = {}
        self.bus_posts = 0
        self.st["n_tdma_yield"] = 0
        self.st["n_tdma_dodge"] = 0
        self.st["n_tdma_idle_slot"] = 0
        self.owned: dict[int, int] = defaultdict(int)
        self.trace: dict[str, list] = {"t": [], "demand": []}

    # -- the calendar -------------------------------------------------------

    def _owner(self) -> int | None:
        """Which core owns right of way on its own hops this cycle."""
        c = self._cores[(self.t // self.p.tdma_slot) % len(self._cores)]
        if self.p.tdma_mode == "demand" and c not in self.demand:
            return None
        return c

    # -- act: one-sided yield, exactly S22's actuator -----------------------

    def _yields_to_owner(self, f: Flit, boarding_node: int) -> bool:
        if f.vc not in self.p.tdma_vcs:
            return False
        owner = self._owner()
        if owner is None or owner == boarding_node:
            return False
        return self._crosses_hop(f, owner, self.n)

    def _itag_blocks(self, f: Flit, boarding_node: int) -> bool:
        if super()._itag_blocks(f, boarding_node):
            return True
        if not is_core(boarding_node):
            return False
        if self._yields_to_owner(f, boarding_node):
            self.st["n_tdma_yield"] += 1
            return True
        return False

    def _select_inject_flit(self, node: int, plane: Any, q) -> Flit | None:
        """FIFO head, unless it would take the owner's hop and something just
        behind it would not.

        Only a flit for a *different* destination may overtake, so per-
        destination order is untouched and two flits of one WriteData burst
        can never swap.
        """
        head = super()._select_inject_flit(node, plane, q)
        d = self.p.tdma_dodge
        if head is None or not d or not is_core(node):
            return head
        if not self._yields_to_owner(head, node):
            return head
        skipped: set[int] = {head.dst}
        for f in list(q)[1:1 + d]:
            if f.dst in skipped:
                break
            if not self._yields_to_owner(f, node):
                self.st["n_tdma_dodge"] += 1
                return f
            skipped.add(f.dst)
        return head

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if f.vc in self.p.tdma_vcs and f.src == (self._owner() or -1):
            self.owned[f.src] += 1

    # -- detect / propagate: the one demand bit -----------------------------

    def _queued_dat(self, core: int) -> int:
        """WriteData this core has waiting -- purely local queue occupancy."""
        n = 0
        for pl in range(self.n_planes):
            for vc in self.p.tdma_vcs:
                for key in ((core, pl, vc), (core, pl, vc, 1),
                            (core, pl, vc, -1)):
                    q = self.srcq.get(key)
                    if q:
                        n += len(q)
                for tbl in (self.pending, self.req_pend):
                    q = tbl.get((core, pl, vc))
                    if q:
                        n += len(q)
        return n

    def _ctrl_deliver(self) -> None:
        due = self._pipe.pop(self.t, None)
        if due is not None:
            self.demand = due

    def _ctrl_issue(self) -> None:
        super()._ctrl_issue()
        p = self.p
        if p.tdma_mode != "demand":
            return
        if (self.t % p.tdma_window) != p.tdma_window - 1:
            return
        want = {c for c in self._cores
                if self._queued_dat(c) >= p.tdma_demand_min}
        self._pipe[self.t + p.tdma_bus_lat] = want
        self.bus_posts += len(self._cores)
        self.st["n_tdma_idle_slot"] += len(self._cores) - len(want)
        self.trace["t"].append(self.t)
        self.trace["demand"].append(sorted(want))

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        cs = self._cores
        return {
            "mode": "s29",
            "actuator": "scheduled_hop_yield",
            "signal": "calendar" if self.p.tdma_mode == "blind"
            else "calendar+demand_bit",
            "slot": self.p.tdma_slot, "frame": self.frame,
            "tdma_mode": self.p.tdma_mode,
            "bus_lat": self.p.tdma_bus_lat, "window": self.p.tdma_window,
            "dodge": self.p.tdma_dodge,
            "bus_posts": self.bus_posts,
            "bus_bits": self.bus_posts,       # one bit per core per window
            "n_tdma_yield": self.st["n_tdma_yield"],
            "n_tdma_dodge": self.st["n_tdma_dodge"],
            "n_idle_slots_saved": self.st["n_tdma_idle_slot"],
            "boarded_in_own_slot": {str(c): self.owned[c] for c in cs},
        }

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["tdma"] = True
        return out


# These names are part of the hook signatures this module overrides.
_ = (Dir, PlaneId, Txn, Sequence)
