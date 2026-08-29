#!/usr/bin/env python3
"""S22: deficit-triggered scoped yield -- fair access without withholding.

Why not a pacer, and why not S1
-------------------------------
Two mechanisms were measured on this fabric first, and both fail for the
same structural reason.

*Sender pacing* (S21) does produce the regularity the per-bin index wants: a
strict bucket injects on a fixed interval and reaches Jain 0.993. But a
credit gate can only ever *withhold*, and the ring offers its free slots
irregularly, so the sender misses the coincidences. The bank depth needed to
ride out a run of blocked cycles is the same bank depth that lets a burst
back out, so the trade never closes: burst 1 quantises the rate to 1/2 and
costs 17%, burst 2 gives the exact rate and still costs 10%.

*S1's AIMD* triggers its multiplicative decrease on the node's own board
failures. On a bufferless ring those failures are caused by other nodes'
transit traffic, so the victim throttles itself and hands its slots to the
cores that were already ahead -- bandwidth 0.23 against 0.59.

What this does instead
----------------------
Nothing is ever withheld from a free slot. The only thing that changes is
*who wins* when two nodes want the same hop, and it is decided by who is
behind:

  1. Each node counts the flits it boards. Every `dfc_window` cycles it
     drives that count onto the same dedicated broadcast bus S1 uses, at the
     same 6-bit width.
  2. Every node accumulates every count it sees, including its own, into a
     one-counter-per-node table. Its deficit is the ring mean of that table
     minus its own entry. Reading its own progress off the bus rather than
     from a local counter is what keeps the comparison exact: both sides
     have crossed the same 6-bit quantiser and the same bus delay, so no
     start-up transient can leave a permanent offset behind.
  3. A node whose deficit passes `dfc_thresh` asserts a request. A node that
     is *not* behind yields the hop to any requester it would ride past --
     the scoped test, so a request only costs the injectors that are
     actually taking that requester's slots, not every node on the plane.

Because a yield hands the slot straight to a node that is behind, and only
happens while somebody is behind, the mechanism equalises cumulative
progress without giving up hops. `dfc_hold` bounds how long one request may
block before it stands down, which caps the damage when a requester is being
starved by transit rather than by injection -- a request cannot stop transit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import PlaneId, Ring2Topology, Txn, is_core, is_ha

# Window counts ride the bus at S1's width: 6 bits, saturating.
BUS_BITS = 6
BUS_MAX = (1 << BUS_BITS) - 1


@dataclass
class Ring2DfcParams(Ring2BaseParams):
    dfc_window: int = 64          # control window, cycles
    dfc_bus_lat: int = 30         # the same bus delay S1 is charged
    dfc_thresh: float = 2.0       # deficit (flits) before requesting a yield
    dfc_clear: float = 0.0        # deficit at which a requester stands down
    dfc_hold: int = 8             # cycles a request may block before standing
                                  # down; 0 = never expire
    dfc_backoff: int = 0          # cycles a stood-down request stays quiet
    dfc_scope: str = "segment"    # "segment" (crossers only) | "plane"
    dfc_vcs: tuple[str, ...] = ("dat",)    # VCs the deficit is tracked on
    dfc_cap: float = 64.0         # clamp on the accumulated deficit
    dfc_scope_nodes: str = "core_only"     # "core_only" | "ha_only" | "both"
    # How many entries deep the inject arbiter may look past a flit it has
    # to yield, hunting for one that turns off the ring *before* the
    # requester. Yielding by idling wastes the slot on the yielder's own
    # outgoing hop, and every hot hop here is already ~91% loaded, so that
    # waste is exactly what makes fairness cost bandwidth. Sending a
    # non-crossing flit instead keeps the yielder's hop busy and still frees
    # the hop the requester is starving on. 0 disables the look-ahead.
    dfc_dodge: int = 0
    # How much further behind a requester has to be before it is worth
    # yielding to. Yielding to a node that is barely behind buys almost no
    # fairness and still risks a wasted hop, and the yielder already holds
    # the whole bus table, so it can compare the two deficits for free.
    dfc_margin: float = 0.0
    # Per-cycle entitlement, in flits, for a *bus-free* variant. The equal-rate
    # share on this fabric is a topology constant (lambda* = 2/7 txn/cycle/core,
    # so 4/7 DAT flits split over two directions), not something that has to be
    # discovered by comparing notes with the other cores. Set it and each node
    # accrues `dfc_target` of deficit per cycle and spends 1.0 per boarded flit,
    # so the deficit is a purely local counter: no bus posts, no 30-cycle delay,
    # and the signal is exact rather than a 30-cycle-stale mean. 0 keeps the
    # bus-derived mean. Note this changes only how the deficit is *measured* --
    # the actuator is still the yield, so a node that has no entitlement stands
    # aside for a needier one rather than idling its own hop.
    dfc_target: float = 0.0


@dataclass
class _Post:
    counts: dict[int, int] = field(default_factory=dict)


class Ring2DfcSim(Ring2BaseSim):
    """S0 data plane plus deficit-triggered scoped yielding."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2DfcParams | None = None, seed: int = 0):
        self.p: Ring2DfcParams
        super().__init__(topo, params or Ring2DfcParams(), seed=seed)
        self.ok_win: dict[int, int] = defaultdict(int)
        self.deficit: dict[int, float] = defaultdict(float)
        self.req: set[int] = set()
        self.req_t: dict[int, int] = {}
        self.quiet_until: dict[int, int] = {}
        # Cumulative boarded count per node, as seen on the bus. Every node
        # holds the same table; a node's own entry comes off the bus too.
        self.cum_bus: dict[int, int] = defaultdict(int)
        self._pipe: dict[int, dict[int, int]] = defaultdict(dict)
        self.bus_posts = 0
        # Activity counters, not event counts: the free-slot arbiter evaluates
        # a candidate once to order the group and again to board it, so one
        # boarding attempt can bump these several times. Comparable across
        # configurations, not interpretable as cycles.
        self.st["n_dfc_yield"] = 0
        self.st["n_dfc_req"] = 0
        self.st["n_dfc_dodge"] = 0
        self.trace: dict[str, list] = {"t": [], "deficit": [], "ok": []}

    # -- membership ---------------------------------------------------------

    def _tracked(self, node: int, vc: str) -> bool:
        if vc not in self.p.dfc_vcs:
            return False
        if self.p.dfc_scope_nodes == "both":
            return True
        if self.p.dfc_scope_nodes == "ha_only":
            return is_ha(node)
        return is_core(node)

    def _members(self) -> list[int]:
        return [n for n in range(self.n)
                if any(self._tracked(n, vc) for vc in self.p.dfc_vcs)]

    # -- the yield rule -----------------------------------------------------

    def _itag_blocks(self, f: Flit, boarding_node: int) -> bool:
        """Base I-tag first, then yield to any requester this flit rides past.

        Yielding is the whole mechanism and it is deliberately one-sided: a
        node that is behind never yields, so a slot given up always lands
        with someone who needs it.
        """
        if super()._itag_blocks(f, boarding_node):
            return True
        if not self.req or not self._tracked(boarding_node, f.vc):
            return False
        if self._crosses_requester(f, boarding_node):
            self.st["n_dfc_yield"] += 1
            return True
        return False

    def _crosses_requester(self, f: Flit, boarding_node: int) -> bool:
        if boarding_node in self.req:
            return False
        floor = self.deficit[boarding_node] + self.p.dfc_margin
        for h in self.req:
            if h == boarding_node or self.deficit[h] < floor:
                continue
            if self.p.dfc_scope == "segment" and \
                    ((h - f.idx) * f.dir) % self.n >= f.target:
                continue
            return True
        return False

    def _select_inject_flit(self, node: int, plane: Any, q) -> Flit | None:
        """FIFO head, unless it would take a requester's hop and something
        just behind it would not.

        Only a flit for a *different* destination may overtake, so per-
        destination order is untouched; two flits of one WriteData burst can
        never swap.
        """
        head = super()._select_inject_flit(node, plane, q)
        d = self.p.dfc_dodge
        if head is None or not d or not self.req:
            return head
        if not self._crosses_requester(head, node):
            return head
        skipped: set[int] = {head.dst}
        for f in list(q)[1:1 + d]:
            if f.dst in skipped:
                break
            if not self._crosses_requester(f, node):
                self.st["n_dfc_dodge"] += 1
                return f
            skipped.add(f.dst)
        return head

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if self._tracked(f.src, f.vc):
            self.ok_win[f.src] += 1
            # Boarding is what the request was for, so charge it against the
            # shortfall straight away instead of waiting for the next window:
            # a requester that has caught up must stop costing its upstream
            # neighbours slots this cycle, not 64 cycles from now.
            self.deficit[f.src] -= 1.0
            if f.src in self.req and self.deficit[f.src] <= self.p.dfc_clear:
                self._stand_down(f.src)

    def _stand_down(self, node: int) -> None:
        self.req.discard(node)
        self.req_t.pop(node, None)
        if self.p.dfc_backoff:
            self.quiet_until[node] = self.t + self.p.dfc_backoff

    # -- control ------------------------------------------------------------

    def _ctrl_deliver(self) -> None:
        if self.p.dfc_target > 0:
            self._accrue()
        else:
            due = self._pipe.pop(self.t, None)
            if due:
                for n, c in due.items():
                    self.cum_bus[n] += c
                self._reprice()
        if not self.p.dfc_hold:
            return
        # A request cannot stop transit, so a requester starved by transit
        # would otherwise idle its upstream injectors indefinitely.
        for h in [h for h in self.req
                  if self.t - self.req_t.get(h, self.t) >= self.p.dfc_hold]:
            self._stand_down(h)

    def _accrue(self) -> None:
        """Bus-free deficit: entitlement accrues at `dfc_target` every cycle.

        The board path already spends the deficit (`-= 1.0` per flit), so all this
        adds is the credit side. `dfc_cap` still bounds it in both directions,
        which is what stops a node that has been starved for a long stretch from
        holding a standing request forever.
        """
        p = self.p
        for n in self._members():
            self.deficit[n] = min(p.dfc_cap, self.deficit[n] + p.dfc_target)
            if (self.deficit[n] >= p.dfc_thresh and n not in self.req
                    and self.t >= self.quiet_until.get(n, 0)):
                self.req.add(n)
                self.req_t[n] = self.t
                self.st["n_dfc_req"] += 1
            elif self.deficit[n] <= p.dfc_clear and n in self.req:
                self._stand_down(n)

    def _reprice(self) -> None:
        """Recompute every node's deficit from the freshly delivered table."""
        p = self.p
        members = self._members()
        if not members:
            return
        mean = sum(self.cum_bus[n] for n in members) / len(members)
        for n in members:
            self.deficit[n] = max(-p.dfc_cap,
                                  min(p.dfc_cap, mean - self.cum_bus[n]))
            if (self.deficit[n] >= p.dfc_thresh and n not in self.req
                    and self.t >= self.quiet_until.get(n, 0)):
                self.req.add(n)
                self.req_t[n] = self.t
                self.st["n_dfc_req"] += 1
            elif self.deficit[n] <= p.dfc_clear and n in self.req:
                self._stand_down(n)

    def _aimd_tick(self) -> None:
        p = self.p
        if (self.t % p.dfc_window) != p.dfc_window - 1:
            return
        members = self._members()
        rec_d, rec_ok = [], []
        for n in members:
            if p.dfc_target <= 0:      # bus-free variant posts nothing
                self._pipe[self.t + p.dfc_bus_lat][n] = min(BUS_MAX,
                                                            self.ok_win[n])
                self.bus_posts += 1
            rec_d.append(round(self.deficit[n], 2))
            rec_ok.append(self.ok_win[n])
        self.ok_win.clear()
        self.trace["t"].append(self.t)
        self.trace["deficit"].append(rec_d)
        self.trace["ok"].append(rec_ok)

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        p = self.p
        members = self._members()
        n_win = max(1, len(self.trace["t"]))
        return {
            "mode": "s22", "window": p.dfc_window, "bus_lat": p.dfc_bus_lat,
            "thresh": p.dfc_thresh, "clear": p.dfc_clear, "hold": p.dfc_hold,
            "backoff": p.dfc_backoff, "scope": p.dfc_scope,
            "tracked_vcs": list(p.dfc_vcs), "nodes": p.dfc_scope_nodes,
            "bus_posts": self.bus_posts,
            "bus_bits": self.bus_posts * BUS_BITS,
            "n_dfc_req": self.st["n_dfc_req"],
            "n_dfc_yield": self.st["n_dfc_yield"],
            "n_dfc_dodge": self.st["n_dfc_dodge"], "dodge": p.dfc_dodge,
            "margin": p.dfc_margin,
            "final_deficit": {str(n): round(self.deficit[n], 2)
                              for n in members},
            "mean_abs_deficit": round(
                sum(abs(v) for w in self.trace["deficit"] for v in w)
                / max(1, n_win * len(members)), 3),
        }

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["dfc"] = True
        return out


def run_batch(topo: Ring2Topology, txns: Sequence[Txn], *,
              params: Ring2DfcParams | None = None,
              t_max: int = 2_000_000, seed: int = 0) -> dict[str, Any]:
    sim = Ring2DfcSim(topo, params or Ring2DfcParams(), seed=seed)
    sim.offer_batch(txns)
    last_progress, last_count = 0, 0
    while sim.t < t_max and not sim.done():
        sim.step()
        if sim.st["n_delivered_flits"] != last_count:
            last_count = sim.st["n_delivered_flits"]
            last_progress = sim.t
        elif sim.t - last_progress > 40_000:
            break
    out = sim.summary()
    out["stall_detected"] = not out["completed"]
    out["fc"] = sim.fc_summary()
    return out


# `PlaneId` is part of the hook signatures this module overrides.
_ = PlaneId
