#!/usr/bin/env python3
"""S26: congestion-aware (non-minimal) routing -- the adaptive-routing family.

Every scheme measured so far leaves the path alone and changes either the
injection rate (S1, S17-S21, S23), the completer's grant order (S16) or the
ring arbiter (S22, I-tag). None of them touches the one decision that is made
before any of that: *which way round the ring a flit goes*. The
interconnection-network literature attacks congestion there first -- UGAL,
Valiant, CQR, GOAL all pick a path from local or global load estimates rather
than from distance alone -- so the family has to be represented before the
taxonomy can claim to be complete.

On a closed ring the choice is unusually clean. Both directions always reach
the destination, so a non-minimal route is always *available*; it is simply
longer. `route="latency"` in the baseline picks the smaller sum of link
delays and never revisits that choice. S26 keeps the same minimal route as
its default and departs from it only when the minimal direction's own
outgoing hop is measurably worse off than the other one.

Mechanism, in the same four parts the other schemes are described in:

  1. **Detect.** Each node keeps, per outgoing direction and per VC, an EWMA
     of its own board outcome: 1.0 on a failed board, 0.0 on a successful
     one. That is the fraction of the time the node cannot get onto that hop,
     which is exactly the local load estimate UGAL uses. No counter leaves
     the node.
  2. **Propagate.** Nothing. The estimate is of the node's *own* two
     outgoing hops, which it can already see.
  3. **Feed back.** Nothing. The decision is taken by the same node that
     measured, at the moment it computes the route.
  4. **Act.** Route the flit the long way round when
     `cong[minimal] - cong[other] > route_thresh`, provided the detour costs
     at most `route_max_extra` extra hops.

Two properties are worth stating before the numbers, because they are what
the measurement is for.

A detour is not free the way a yield or a withheld grant is. Sending a flit
`n - h` hops instead of `h` multiplies the link capacity it consumes by
`(n - h) / h`, and on this fabric the busiest hop is already ~97% occupied.
Adaptive routing therefore spends the one resource that is known to be
binding, in order to relieve the resource that is known to be binding. It can
only win if the load is unevenly spread *around the ring*, not merely uneven
*between cores*.

And a detour cannot manufacture a neighbour. The six-fast / four-slow split
comes from C0 / C8 / C10 / C18 having one adjacent memory node instead of
two. Rerouting changes which hops a write crosses; it does not change how
many memory nodes sit next to the core. This scheme is the direct test of
that claim.

`route_vcs` defaults to the DAT VC only: WriteData is 2 of the 4 flits of a
transaction and is the payload the study measures, while REQ / RSP are single
flits whose detour would cost latency for no capacity relief.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import (Dir, PlaneId, Ring2Topology, Txn, hop_count,
                           is_core)


@dataclass
class Ring2RouteParams(Ring2BaseParams):
    # EWMA weight on the per-(node, dir, vc) board-failure indicator. 1/32
    # settles in ~100 cycles, the same order as the fairness window, so the
    # estimate tracks the congestion the index is measured against.
    route_g: float = 1.0 / 32
    # How much worse the minimal direction has to look before the detour is
    # taken, in units of board-failure probability. 0 would flap on noise;
    # 1.0 disables the scheme.
    route_thresh: float = 0.15
    # Cap on the detour, in extra hops. The ring is 20 nodes, so an
    # unrestricted detour can cost 18 extra hops for a 1-hop write -- that is
    # never worth it and it is not what an adaptive router would do.
    route_max_extra: int = 4
    # VCs whose route may be reconsidered. DAT is the payload.
    route_vcs: tuple[str, ...] = ("dat",)
    # Only reroute traffic a core sources. HA responses are 1 flit and their
    # direction is the mirror of the request's, so detouring them just adds
    # latency to the handshake.
    route_cores_only: bool = True
    trace_route: bool = False


class Ring2RouteSim(Ring2BaseSim):
    """S0 data plane; the route is recomputed per flit from local load."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2RouteParams | None = None, seed: int = 0):
        self.p: Ring2RouteParams
        # `_place` is called from `offer_txn` during construction of the very
        # first flit, so the congestion table has to exist before super().
        self.cong: dict[Any, float] = defaultdict(float)
        self.n_detour: dict[int, int] = defaultdict(int)
        self.n_minimal: dict[int, int] = defaultdict(int)
        self.extra_hops = 0
        super().__init__(topo, params or Ring2RouteParams(), seed=seed)
        self.st["n_detour"] = 0
        self.st["n_detour_hops"] = 0
        self.trace: dict[str, list] = {"t": [], "cong_cw": [], "cong_ccw": []}

    # -- detect -------------------------------------------------------------

    def _bump(self, node: int, d: Dir, vc: str, x: float) -> None:
        g = self.p.route_g
        k = (node, d, vc)
        self.cong[k] += g * (x - self.cong[k])

    def _on_board_fail(self, node: int, f: Flit) -> None:
        super()._on_board_fail(node, f)
        self._bump(node, f.dir, f.vc, 1.0)

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        self._bump(f.src, f.dir, f.vc, 0.0)

    # -- act ----------------------------------------------------------------

    def _eligible(self, f: Flit) -> bool:
        if f.vc not in self.p.route_vcs:
            return False
        return is_core(f.src) if self.p.route_cores_only else True

    def _place(self, f: Flit) -> None:
        """Route computation. Minimal unless the local estimate says otherwise."""
        super()._place(f)
        if not self._eligible(f):
            return
        m: Dir = f.dir
        o: Dir = -m
        h_m = hop_count(f.src, f.dst, m, self.n)
        h_o = hop_count(f.src, f.dst, o, self.n)
        extra = h_o - h_m
        if extra <= 0 or extra > self.p.route_max_extra:
            self.n_minimal[f.src] += 1
            return
        if self.cong[(f.src, m, f.vc)] - self.cong[(f.src, o, f.vc)] \
                <= self.p.route_thresh:
            self.n_minimal[f.src] += 1
            return
        f.dir = o
        f.target = h_o
        self.n_detour[f.src] += 1
        self.extra_hops += extra
        self.st["n_detour"] += 1
        self.st["n_detour_hops"] += extra

    # -- reporting ----------------------------------------------------------

    def _aimd_tick(self) -> None:
        if not self.p.trace_route or (self.t % 512):
            return
        cs = self.topo.cores
        self.trace["t"].append(self.t)
        self.trace["cong_cw"].append(
            [round(self.cong[(c, 1, "dat")], 4) for c in cs])
        self.trace["cong_ccw"].append(
            [round(self.cong[(c, -1, "dat")], 4) for c in cs])

    def fc_summary(self) -> dict[str, Any]:
        cs = self.topo.cores
        n_det = sum(self.n_detour.values())
        n_min = sum(self.n_minimal.values())
        return {
            "mode": "s26",
            "actuator": "route_select",
            "bus_posts": 0, "bus_bits": 0,
            "route_g": self.p.route_g,
            "route_thresh": self.p.route_thresh,
            "route_max_extra": self.p.route_max_extra,
            "route_vcs": list(self.p.route_vcs),
            "n_detour": n_det,
            "n_minimal": n_min,
            "detour_frac": round(n_det / max(1, n_det + n_min), 5),
            "extra_hops": self.extra_hops,
            "extra_hops_per_flit": round(self.extra_hops / max(1, n_det + n_min), 4),
            "detour_by_core": {str(c): self.n_detour[c] for c in cs},
            "cong_final_cw": {str(c): round(self.cong[(c, 1, "dat")], 4)
                              for c in cs},
            "cong_final_ccw": {str(c): round(self.cong[(c, -1, "dat")], 4)
                               for c in cs},
        }

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["route"] = True
        return out


# These names are part of the hook signatures this module overrides.
_ = (PlaneId, Txn, field, Sequence)
