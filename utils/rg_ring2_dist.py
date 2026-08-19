#!/usr/bin/env python3
"""S4: local / distributed injection policies on the S0 datapath.

No central matching. Each node only looks at local queues, I-tag, hop
credit, and (optionally) a per-destination outstanding counter. The
point is to close S0's gap to the analytic bound without S2's arbiter.

Policies (combinable via params)
--------------------------------
resp_bypass_itag   responses ignore a request-held I-tag
no_req_itag        cores never raise I-tag (responses still may)
leave_useful       at a core prefer ejecting resp; at an HA prefer req
ha_outst           extra per-(core, HA) outstanding cap (0 = off)
req_slot           cores may inject requests only in even slots of
                   this many cycles (0 = off). A distributed two-wave.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import Ring2Topology, Txn, is_core, is_ha


@dataclass
class Ring2DistParams(Ring2BaseParams):
    resp_bypass_itag: bool = False
    no_req_itag: bool = False
    leave_useful: bool = True
    ha_outst: int = 0
    req_slot: int = 0
    short_first: bool = False


class Ring2DistSim(Ring2BaseSim):
    """S0 datapath + local priority / dest-cap / optional request slots."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2DistParams | Ring2BaseParams | None = None,
                 seed: int = 0):
        p = params or Ring2DistParams()
        if not isinstance(p, Ring2DistParams):
            p = Ring2DistParams(**{k: getattr(p, k) for k in
                                   Ring2BaseParams.__dataclass_fields__})
        super().__init__(topo, p, seed=seed)
        self.ha_used: dict[tuple[int, int], int] = defaultdict(int)

    def _itag_blocks(self, f: Flit, boarding_node: int) -> bool:
        if getattr(self.p, "resp_bypass_itag", False) and f.kind == "resp":
            return False
        return super()._itag_blocks(f, boarding_node)

    def _should_raise_itag(self, node: int, f: Flit) -> bool:
        if getattr(self.p, "no_req_itag", False) and f.kind == "req":
            return False
        return True

    def _leave_order(self, node: int, plane: int, reqs: list[Flit]):
        if not getattr(self.p, "leave_useful", False) or len(reqs) <= 1:
            return super()._leave_order(node, plane, reqs)
        # useful kind first: resp at cores (recv), req at HAs (unlock resp)
        prefer = "resp" if is_core(node) else "req"
        reqs.sort(key=lambda f: 0 if f.kind == prefer else 1)
        return reqs

    def _admit(self, key) -> None:
        if not getattr(self.p, "short_first", False):
            return super()._admit(key)
        q, pend = self.srcq[key], self.pending[key]
        while pend and len(q) < self.p.inj_depth:
            best_i, best_h = 0, 10**9
            for i, f in enumerate(pend):
                if f.target < best_h:
                    best_h, best_i = f.target, i
            q.append(pend[best_i])
            del pend[best_i]
        if q:
            self.st["max_srcq"] = max(self.st["max_srcq"], len(q))

    def _may_inject(self, node: int, plane: int, f: Flit | None = None) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if f is None:
            return True
        slot = getattr(self.p, "req_slot", 0)
        if slot > 0 and f.kind == "req" and (self.t // slot) % 2 == 1:
            self.st["n_outst_wait"] += 1
            return False
        cap = getattr(self.p, "ha_outst", 0)
        if cap > 0 and f.kind == "req" and is_core(f.src):
            txn = self.txn_by_id[f.txn_id]
            if self.ha_used[(txn.core, txn.ha)] >= cap:
                self.st["n_outst_wait"] += 1
                return False
        return True

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if f.kind != "req" or getattr(self.p, "ha_outst", 0) <= 0:
            return
        txn = self.txn_by_id[f.txn_id]
        self.ha_used[(txn.core, txn.ha)] += 1

    def _on_pe_drain(self, f: Flit) -> None:
        super()._on_pe_drain(f)
        if (f.kind == "resp" and getattr(self.p, "ha_outst", 0) > 0
                and self.resp_left.get(f.txn_id, 0) == 0):
            txn = self.txn_by_id[f.txn_id]
            key = (txn.core, txn.ha)
            self.ha_used[key] = max(0, self.ha_used[key] - 1)

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["dist"] = True
        out["resp_bypass_itag"] = getattr(self.p, "resp_bypass_itag", False)
        out["no_req_itag"] = getattr(self.p, "no_req_itag", False)
        out["leave_useful"] = getattr(self.p, "leave_useful", False)
        out["ha_outst"] = getattr(self.p, "ha_outst", 0)
        out["req_slot"] = getattr(self.p, "req_slot", 0)
        return out


def run_batch(topo: Ring2Topology, txns: Sequence[Txn], *,
              params: Ring2DistParams | Ring2BaseParams | None = None,
              t_max: int = 2_000_000, seed: int = 0) -> dict[str, Any]:
    p = params or Ring2DistParams()
    sim = Ring2DistSim(topo, p, seed=seed)
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
    out["recv_by_core"] = sim.recv_by_core()
    return out


def _silence_unused() -> None:
    _ = is_ha
