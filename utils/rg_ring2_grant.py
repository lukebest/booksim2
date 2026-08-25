#!/usr/bin/env python3
"""S16: receiver-driven write admission, Homa-style, over stock CHI.

Homa's central idea is that the *receiver* schedules the network: a sender
may not transmit scheduled bytes until the receiver hands out a GRANT, and
the receiver grants to a few senders at once (overcommitment) so its own
downlink never idles while one sender is slow to respond.

CHI already has that grant. `WriteNoSnp` forbids WriteData until the
completer returns `DBIDResp`, so the completer -- the receiver -- already
owns the decision of who may put write data on the ring and when. The
baseline simply squanders the authority by granting on arrival. S16 keeps
the wire format untouched and only changes *when* and *to whom* DBIDResp is
issued:

  * REQs queue at the completer instead of being granted immediately.
  * A completer keeps at most `overcommit` grants outstanding (Homa's
    overcommitment degree, and the analogue of RTTbytes).
  * Among queued requesters it grants to the one it has served least so far
    -- with fixed-size writes, Homa's SRPT degenerates to fair queueing.

Two properties matter for cost.

First, no new hardware: no congestion bus, no broadcast, no per-hop slot
reservation. The control signal is a packet the protocol already sends.

Second, and this is why it is cheaper than reserving slots, grant pacing
*cannot create a bubble*. Reserving a ring slot forbids upstream injection
into that slot, so if the reserving node then fails to use it the slot is
lost. Withholding a grant only means an advantaged core has less data ready;
whichever core can use the slot still takes it.

The policy is expressed purely in terms of the completer hooks, so the same
code governs the single ring and the 3D-stacked fabric. That is the point of
placing control at the completer: it is a protocol decision, not a topology
one.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from rg_ring2_base import Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import Ring2Topology, Txn


@dataclass
class GrantKnobs:
    """The receiver's scheduling knobs, shared by every fabric."""

    # Grants a completer may have outstanding at once. This is the only
    # fairness/throughput knob: 1 serialises the completer and idles the
    # ring, a large value degenerates to the ungoverned baseline.
    overcommit: int = 32
    # Grant strictly round-robin instead of least-served-first. Kept for the
    # report: it isolates how much of the fairness comes from *ordering*
    # versus from tracking cumulative service.
    policy: str = "least_served"      # "least_served" | "round_robin"
    # Let a completer grant on arrival while it is under-subscribed, so a
    # lightly loaded fabric pays no added latency. This is the counterpart of
    # Homa's unscheduled bytes, which CHI cannot express directly.
    eager: bool = True
    trace: bool = True


@dataclass
class Ring2GrantParams(Ring2BaseParams, GrantKnobs):
    """Baseline ring fabric plus the receiver's scheduling knobs."""


class GrantMixin:
    """Receiver-driven admission, in terms of the completer hooks only."""

    def _grant_init(self) -> None:
        self.gp = self.p                       # type: ignore[attr-defined]
        # completer -> requester -> queued REQs, oldest first
        self.gq: dict[int, dict[int, deque[Txn]]] = defaultdict(
            lambda: defaultdict(deque))
        self.n_queued: dict[int, int] = defaultdict(int)
        self.outstanding: dict[int, int] = defaultdict(int)
        # completer -> requester -> WriteData flits granted so far
        self.served: dict[int, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        self.rr: dict[int, int] = defaultdict(int)
        self.peak_outstanding = 0
        st = self.st                           # type: ignore[attr-defined]
        st["n_grant_eager"] = 0
        st["n_grant_paced"] = 0
        st["n_grant_queued"] = 0
        self.grant_delay: list[int] = []
        self._qt: dict[int, int] = {}

    # -- the DBIDResp emit differs only in name between the two fabrics ----

    def _emit_dbid(self, txn: Txn) -> None:
        raise NotImplementedError

    # -- receiver-driven admission -----------------------------------------

    def _on_req_at_completer(self, txn: Txn) -> None:
        mem = txn.ha
        if self.gp.eager and self.outstanding[mem] < self.gp.overcommit \
                and self.n_queued[mem] == 0:
            self.st["n_grant_eager"] += 1
            self._grant(txn)
            return
        self.gq[mem][txn.core].append(txn)
        self.n_queued[mem] += 1
        self.st["n_grant_queued"] += 1
        self._qt[txn.txn_id] = self.t
        self._pump(mem)

    def _on_write_data_complete(self, txn: Txn) -> None:
        """A grant retired: its buffer is free, so hand out the next one."""
        self.outstanding[txn.ha] = max(0, self.outstanding[txn.ha] - 1)
        self._pump(txn.ha)

    def _grant(self, txn: Txn) -> None:
        self.outstanding[txn.ha] += 1
        # An outstanding DBID *is* a committed write-data buffer at the
        # completer, so the peak is the buffering the scheme actually needs.
        self.peak_outstanding = max(self.peak_outstanding,
                                    self.outstanding[txn.ha])
        self.served[txn.ha][txn.core] += txn.m_wdata
        self._emit_dbid(txn)

    def _pump(self, mem: int) -> None:
        """Issue grants until the completer is fully committed."""
        while self.outstanding[mem] < self.gp.overcommit \
                and self.n_queued[mem] > 0:
            core = self._pick(mem)
            if core is None:
                return
            txn = self.gq[mem][core].popleft()
            self.n_queued[mem] -= 1
            self.st["n_grant_paced"] += 1
            t0 = self._qt.pop(txn.txn_id, self.t)
            self.grant_delay.append(self.t - t0)
            self._grant(txn)

    def _pick(self, mem: int) -> int | None:
        """Which requester to grant next."""
        waiting = [c for c, q in self.gq[mem].items() if q]
        if not waiting:
            return None
        if self.gp.policy == "round_robin":
            waiting.sort()
            nxt = self.rr[mem]
            for c in waiting:
                if c >= nxt:
                    self.rr[mem] = c + 1
                    return c
            self.rr[mem] = waiting[0] + 1
            return waiting[0]
        # Least-served-first. Fixed-size writes make Homa's SRPT collapse to
        # fair queueing, so equalise cumulative granted flits instead.
        return min(waiting, key=lambda c: (self.served[mem][c], c))

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        gd = self.grant_delay
        served_spread = []
        for mem, per in self.served.items():
            vals = [v for v in per.values() if v]
            if len(vals) > 1:
                served_spread.append(max(vals) / max(1, min(vals)))
        return {
            "mode": "s16",
            "overcommit": self.gp.overcommit,
            "policy": self.gp.policy,
            "eager": self.gp.eager,
            "bus_posts": 0,
            "bus_bits": 0,
            "n_grant_eager": self.st["n_grant_eager"],
            "n_grant_paced": self.st["n_grant_paced"],
            "n_grant_queued": self.st["n_grant_queued"],
            "grant_delay_mean": round(sum(gd) / len(gd), 2) if gd else 0.0,
            "grant_delay_max": max(gd) if gd else 0,
            "served_spread_max": round(max(served_spread), 4)
            if served_spread else 1.0,
            "peak_grants": self.peak_outstanding,
            "peak_buf_flits": self.peak_outstanding * 4,
        }


class Ring2GrantSim(GrantMixin, Ring2BaseSim):
    """Baseline ring datapath; the completer paces DBIDResp."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2GrantParams | None = None, *,
                 seed: int = 0) -> None:
        super().__init__(topo, params or Ring2GrantParams(), seed=seed)
        self._grant_init()
        self.trace: list[dict[str, Any]] = []

    def _emit_dbid(self, txn: Txn) -> None:
        self._emit_write(txn, "dbid", txn.ha, txn.core, 1,
                         self.t + self._ha_delay(txn, "dbid"))
