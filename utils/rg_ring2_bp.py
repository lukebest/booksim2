#!/usr/bin/env python3
"""S27: hop-by-hop link backpressure -- the PFC / credit-flow-control family.

The oldest congestion-control family in interconnects does not compute a rate
at all. A resource that is running out asserts a stop signal to the stage
immediately upstream, that stage stops, and if it fills it asserts its own
stop further upstream. Credit flow control, Ethernet PFC, wormhole
backpressure and CHI's own link credits are all this shape. It is
attractive because it is loss-free by construction and needs no measurement
of anything global.

Why the family is *not* already in the study, and why the obvious answer is
wrong
-----------------------------------------------------------------------------
The usual objection to backpressure on this fabric is that there is nothing
to pause: it is bufferless, so no hop owns a queue whose occupancy could
cross a threshold, and the down-ring leave buffers are measured at 0% full
for the whole run. Both statements are true, and both are answers to the
wrong question. The resource that is actually running out is the *link*, not
a queue: the busiest directed hop is occupied ~97% of the time. A hop's
occupancy is a perfectly good XOFF trigger, and it is one the node owning
that hop can measure locally.

So S27 is backpressure applied to link occupancy rather than to queue
occupancy:

  1. **Detect.** Every `bp_window` cycles each node computes the occupancy of
     its own outgoing directed hop, per VC, as busy-cycles / window. Above
     `bp_xoff` it asserts XOFF for that hop; below `bp_xon` it releases.
     One saturating counter and one comparator per (dir, VC).
  2. **Propagate.** Hop by hop, upstream, one cycle per hop, on a dedicated
     single-bit wire per (dir, VC) between adjacent nodes -- the same
     structure PFC uses. This is deliberately *not* the broadcast bus S1 and
     S22 use: a signal that reaches every node in one step is a bus, and the
     point of this family is that it is not one. A node `k` hops upstream
     therefore sees a `k`-cycle-old view.
  3. **Feed back.** None beyond the propagation. There is no source-side
     controller and no state at the requester.
  4. **Act.** A node may not board a flit whose path crosses a hop it
     currently sees XOFF'd, within `bp_reach` hops. Beyond that reach the
     signal is not forwarded, which is what bounds the wiring and the
     head-of-line damage -- an unbounded reach is how PFC produces
     congestion trees.

What this is expected to expose
------------------------------
Backpressure protects a resource by idling it. On a fabric whose binding
resource is a link at 97% occupancy, refusing injection into that link is
throughput lost outright, because a bufferless ring cannot store the traffic
it just refused and hand it to the same link one cycle later -- the slot is
simply gone. And the refusal is blind to *who* was taking the hop: every
injector crossing it stops, the ones that were ahead and the ones that were
behind alike. That is the same defect S1 has, arrived at from the opposite
end of the design space, and it is why this family is reported rather than
recommended.

`bp_vcs` defaults to DAT: WriteData is the traffic that fills the hot hops.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import Dir, PlaneId, Ring2Topology, Txn, is_core


@dataclass
class Ring2BpParams(Ring2BaseParams):
    # Measurement / decision window, cycles. Shorter reacts faster but the
    # occupancy estimate gets noisier; 64 matches S1's control window so the
    # two families are charged the same reaction time.
    bp_window: int = 64
    # Occupancy at which a hop asserts XOFF, and at which it releases. The
    # gap is hysteresis; without it the signal chatters every window.
    bp_xoff: float = 0.95
    bp_xon: float = 0.85
    # How many hops upstream the XOFF is forwarded. PFC's blast radius knob.
    # 0 means "the whole ring", i.e. the unbounded variant.
    bp_reach: int = 8
    # VCs whose hops are watched and gated.
    bp_vcs: tuple[str, ...] = ("dat",)
    # Gate only core-sourced traffic. Pausing the completer's responses would
    # deadlock the handshake that frees the requester's outstanding slot.
    bp_cores_only: bool = True


class Ring2BpSim(Ring2BaseSim):
    """S0 data plane plus per-hop occupancy XOFF, propagated hop by hop."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2BpParams | None = None, seed: int = 0):
        self.p: Ring2BpParams
        super().__init__(topo, params or Ring2BpParams(), seed=seed)
        # (dir, idx, vc) -> busy cycles inside the current window
        self.busy: dict[Any, int] = defaultdict(int)
        # XOFF state of each hop for the current and the previous window. A
        # node k hops upstream reads the current word only once the signal has
        # had k cycles to walk to it, otherwise the previous one -- which is
        # exactly a one-cycle-per-hop propagation of a window-rate signal.
        self.xoff_cur: dict[Any, bool] = {}
        self.xoff_prev: dict[Any, bool] = {}
        self.win_start = 0
        self.st["n_bp_deny"] = 0
        self.st["n_bp_xoff"] = 0
        self.xoff_cycles: dict[Any, int] = defaultdict(int)
        self.wire_posts = 0
        self.trace: dict[str, list] = {"t": [], "n_xoff": []}

    # -- detect: hop occupancy ---------------------------------------------

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        hop = (f.dir, f.idx, f.vc)
        ok = super()._launch(f, inring=inring)
        if ok and f.vc in self.p.bp_vcs:
            self.busy[hop] += 1
        return ok

    def _ctrl_issue(self) -> None:
        super()._ctrl_issue()
        p = self.p
        if (self.t % p.bp_window) != p.bp_window - 1:
            return
        self.xoff_prev = dict(self.xoff_cur)
        n_on = 0
        for d in (1, -1):
            for idx in range(self.n):
                for vc in p.bp_vcs:
                    hop = (d, idx, vc)
                    util = self.busy.get(hop, 0) / p.bp_window
                    was = self.xoff_cur.get(hop, False)
                    now = util >= p.bp_xoff if not was else util > p.bp_xon
                    if now != was:
                        # One bit changes state on one wire: the only thing
                        # this family ever transmits.
                        self.wire_posts += 1
                        if now:
                            self.st["n_bp_xoff"] += 1
                    self.xoff_cur[hop] = now
                    if now:
                        n_on += 1
                        self.xoff_cycles[hop] += p.bp_window
        self.busy.clear()
        self.win_start = self.t + 1
        self.trace["t"].append(self.t)
        self.trace["n_xoff"].append(n_on)

    # -- act: gate an injection whose path crosses an XOFF'd hop -----------

    def _xoff_seen(self, hop: Any, k: int) -> bool:
        """XOFF state of `hop` as visible `k` hops upstream, this cycle."""
        if self.t - k >= self.win_start:
            return self.xoff_cur.get(hop, False)
        return self.xoff_prev.get(hop, False)

    def _blocked(self, f: Flit) -> bool:
        reach = self.p.bp_reach or self.n
        span = min(f.target, reach)
        idx, d = f.idx, f.dir
        for k in range(span):
            if self._xoff_seen((d, (idx + d * k) % self.n, f.vc), k):
                return True
        return False

    def _may_inject(self, node: int, plane: PlaneId, f: Flit | None = None
                    ) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if f is None or f.vc not in self.p.bp_vcs:
            return True
        if self.p.bp_cores_only and not is_core(f.src):
            return True
        if self._blocked(f):
            self.st["n_bp_deny"] += 1
            self._deny_cause = "bp_xoff"
            return False
        return True

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        span = max(1, self.t)
        hops = sorted(self.xoff_cycles.items(), key=lambda kv: -kv[1])[:8]
        return {
            "mode": "s27",
            "actuator": "hop_xoff_gate",
            "window": self.p.bp_window,
            "xoff": self.p.bp_xoff, "xon": self.p.bp_xon,
            "reach": self.p.bp_reach, "bp_vcs": list(self.p.bp_vcs),
            # A hop-by-hop wire is not the broadcast bus, so the bus columns
            # the other schemes report stay zero and the wire is priced
            # separately: one bit per (dir, VC) between adjacent nodes.
            "bus_posts": 0, "bus_bits": 0,
            "wire_posts": self.wire_posts,
            "wire_bits": 2 * self.n * len(self.p.bp_vcs),
            "n_bp_xoff": self.st["n_bp_xoff"],
            "n_bp_deny": self.st["n_bp_deny"],
            "xoff_duty": round(sum(self.xoff_cycles.values())
                               / max(1, 2 * self.n * len(self.p.bp_vcs))
                               / span, 5),
            "worst_hops": [[f"{d}:{i}:{v}", round(c / span, 4)]
                           for (d, i, v), c in hops],
        }

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["bp"] = True
        return out


# These names are part of the hook signatures this module overrides.
_ = (Dir, Txn, Sequence)
