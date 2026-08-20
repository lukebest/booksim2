#!/usr/bin/env python3
"""S3: request-as-POP on the S0 dual-plane ring.

A memory-read request *is* the scheduling information. There is no extra
1-bit notify / pull-token control plane.

    core has receive-window credits  ->  may push the read request (S0 hop)
    request PE-drains at the HA      ->  HA enqueues that request
    HA RR among pending requests     ->  offers that request's response flits
    core PE-drains a response        ->  frees one receive-window credit

Admission uses the aligned per-core outstanding cap
(`core_outstanding`, default 100) from the base datapath. `pop_window`
is an optional extra per-(core, resp plane) cap (0 = off). The HA then
sees arrived requests and RR-schedules which one gets its response burst.

`pop_scope=resp_only`: requests inject like S0 (no core window); the HA
still schedules among arrived requests. Ablation of the receive window.

Hop reservation is *not* added — that is S2. I-tag / E-tag / boarding
queue / point-to-point credit stay on the datapath.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Sequence

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import Ring2Topology, Txn


class Ring2PopSim(Ring2BaseSim):
    """S0 datapath + core receive window + HA request scheduler."""

    def __init__(self, topo: Ring2Topology, params: Ring2BaseParams | None = None,
                 seed: int = 0):
        p = params or Ring2BaseParams()
        p.pop = True
        if p.pop_scope == "both":
            p.pop_scope = "req_as_grant"
        super().__init__(topo, p, seed=seed)
        # core receive-window: outstanding reads (one slot per in-flight txn)
        self.core_used: dict[Any, int] = defaultdict(int)     # (core, plane)
        self.outstanding: dict[Any, int] = self.core_used     # verify alias
        # HA-side pending reads, one deque per (ha, resp_plane)
        self.ha_pending: dict[Any, deque] = defaultdict(deque)
        self.active_ha: set[tuple] = set()
        # pin response plane at first credit check so window matches eject
        self.resp_plane: dict[int, int] = {}

    def _core_window(self) -> bool:
        return self.p.pop_scope != "resp_only"

    def _resp_plane_of(self, txn: Txn) -> int:
        pid = txn.txn_id
        if pid not in self.resp_plane:
            self.resp_plane[pid] = self._pick_plane(
                txn.ha, txn.core, "resp", pid)
        return self.resp_plane[pid]

    def _may_inject(self, node: int, plane: int, f: Flit | None = None) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if (f is None or f.kind != "req" or not self._core_window()
                or self.p.pop_window <= 0):
            return True
        txn = self.txn_by_id[f.txn_id]
        ckey = (txn.core, self._resp_plane_of(txn))
        if self.core_used[ckey] < self.p.pop_window:
            return True
        self.st["n_pull_wait"] += 1
        return False

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        if f.kind != "req" or not self._core_window() or self.p.pop_window <= 0:
            return
        txn = self.txn_by_id[f.txn_id]
        ckey = (txn.core, self._resp_plane_of(txn))
        self.core_used[ckey] += 1
        self.st["max_pull_outstanding"] = max(
            self.st["max_pull_outstanding"], self.core_used[ckey])
        f.pulled = True  # type: ignore[attr-defined]

    def _ha_accept_req(self, txn: Txn, f: Flit) -> None:
        """Request arrived at HA: it becomes a schedulable POP entry."""
        plane = self._resp_plane_of(txn)
        rec = {
            "txn": txn,
            "left": txn.m_resp,
            "plane": plane,
            "fail_board": f.fail_board,
            "fail_eject": f.fail_eject,
            "t_ready": self.t + self.p.t_ha_service,
        }
        key = (txn.ha, plane)
        self.ha_pending[key].append(rec)
        self.active_ha.add(key)
        self.req_fail[txn.txn_id] = (f.fail_board, f.fail_eject)

    def _offer_txn_resps(self, rec: dict[str, Any]) -> None:
        """HA scheduled this request: emit its full response burst."""
        txn: Txn = rec["txn"]
        plane = rec["plane"]
        t_ready = rec["t_ready"]
        for k in range(txn.m_resp):
            flit = Flit(pid=self._pid, txn_id=txn.txn_id, seq=k,
                        nflit=txn.m_resp, src=txn.ha, dst=txn.core,
                        kind="resp", t_gen=t_ready, plane=plane,
                        fail_board=rec["fail_board"],
                        fail_eject=rec["fail_eject"])
            self._pid += 1
            self._place(flit)
            self._offer_flit(flit)
            self.st["n_offered_resp"] += 1
            self.st["n_pull_issued"] += 1

    def _ctrl_issue(self) -> None:
        """One arrived request per (HA, plane) per cycle; full response."""
        for key in list(self.active_ha):
            q = self.ha_pending[key]
            n = len(q)
            scheduled = False
            for _ in range(n):
                rec = q[0]
                if rec["t_ready"] > self.t:
                    q.rotate(-1)
                    continue
                q.popleft()
                self._offer_txn_resps(rec)
                scheduled = True
                break
            if not q:
                self.active_ha.discard(key)
            elif not scheduled:
                # all remaining are still in t_ha; try again next cycle
                pass

    def _on_pe_drain(self, f: Flit) -> None:
        if f.kind == "req":
            self.st["n_delivered_flits"] += 1
            if self.keep_flits:
                self.delivered.append((f, self.t))
            self.st["n_delivered_req"] += 1
            self._ha_accept_req(self.txn_by_id[f.txn_id], f)
            return
        super()._on_pe_drain(f)
        if (self._core_window() and self.p.pop_window > 0
                and self.resp_left.get(f.txn_id, 0) == 0):
            ckey = (f.dst, f.plane)
            self.core_used[ckey] = max(0, self.core_used[ckey] - 1)

    def in_flight(self) -> int:
        pending = sum(rec["left"] for q in self.ha_pending.values()
                      for rec in q)
        return super().in_flight() + pending

    def summary(self) -> dict[str, Any]:
        out = super().summary()
        out["pop"] = True
        out["pop_window"] = self.p.pop_window
        out["pop_scope"] = self.p.pop_scope
        out["n_pull_wait"] = self.st["n_pull_wait"]
        out["n_pull_issued"] = self.st["n_pull_issued"]
        out["max_pull_outstanding"] = self.st["max_pull_outstanding"]
        out["pull_outstanding"] = dict(self.core_used)
        out["n_ha_pending"] = sum(len(q) for q in self.ha_pending.values())
        return out


def run_batch(topo: Ring2Topology, txns: Sequence[Txn], *,
              params: Ring2BaseParams | None = None,
              t_max: int = 2_000_000, seed: int = 0) -> dict[str, Any]:
    p = params or Ring2BaseParams()
    p.pop = True
    sim = Ring2PopSim(topo, p, seed=seed)
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
    out["hop_starts"] = sim.hop_starts
    return out


if __name__ == "__main__":
    import json
    from rg_ring2_topo import build_allpairs

    topo = Ring2Topology()
    tx = build_allpairs(m=1, m_resp=4)
    r = run_batch(topo, tx)
    keep = ("completed", "makespan", "n_txn_done", "n_deflections",
            "n_board_fail", "n_pull_wait", "n_pull_issued",
            "max_pull_outstanding", "max_ejectq", "lat_p50", "lat_p99")
    print(json.dumps({k: r.get(k) for k in keep}, indent=2))
