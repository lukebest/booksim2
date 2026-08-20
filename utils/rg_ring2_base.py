#!/usr/bin/env python3
"""S0 baseline: RR inject + I-tag / E-tag on the 20-node dual-plane ring.

This is a single-arc adaptation of `rg_ring_base.py`. There is no turn and
therefore no transfer FIFO and no Swap Rule. The two remaining failure points
are boarding (slot occupied) and leaving (shared per-plane eject queue full);
those are exactly what I-tag and E-tag bound.

Boarding queue
--------------
Each (node, plane) has an `inj_depth`-deep boarding queue. A PE hands flits to
`pending` (off-fabric backlog) and they are admitted into that queue only when
it has room, so the injection point exerts real backpressure instead of
absorbing the whole batch.

E-tag semantics (adapted, not HiRD)
-----------------------------------
The 2D ring bound E-tag to reserved *transfer-FIFO* entries. Here there is no
transfer FIFO, so an E-tagged flit may use reserved *eject-queue* entries
(`resv_ej`) that a normal flit cannot touch. Thresholds remain sweep
parameters.

Read-return
-----------
A transaction is a 1-flit request core->HA plus R response flits HA->core.
Makespan is the cycle the last response flit is drained by the core PE.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Sequence

from rg_ring2_topo import (
    Dir, Kind, PlaneId, PlaneSel, Ring2Topology, Txn, hop_count, is_core,
    shortest_dir,
)


@dataclass
class Ring2BaseParams:
    sigma: int = 1
    inj_depth: int = 8            # boarding queue per (node, plane)
    eject_depth: int = 4          # shared by both dirs of one plane
    resv_ej: int = 1              # extra eject slots only an E-tagged flit uses
    t_inj: int = 64               # inject starve cycles -> I-tag
    t_xfer: int = 4               # failed leaves -> E-tag
    eject_bw: int = 1             # PE drain per (node, plane) per cycle
    t_ha_service: int = 0         # cycles HA waits before emitting responses
    plane_sel: PlaneSel = "least_occupied"
    # Aligned across S0/S1/S2/S3: max in-flight reads per AI core
    # (request injected, last response not yet PE-drained). 0 = unlimited.
    core_outstanding: int = 512
    # AIMD knobs (ignored by S0; consumed by Ring2AimdSim)
    aimd: bool = False
    alpha: float = 0.15
    beta: float = 0.85
    epoch: int = 64
    rate_min: float = 0.30
    rate_max: float = 1.0
    rate_init: float = 1.0
    aimd_scope: str = "core_only"     # "core_only" | "both"
    # Push-on-pull knobs (ignored by S0/S1; consumed by Ring2PopSim)
    pop: bool = False
    pop_window: int = 0              # extra S3 per-(core, plane) cap; 0 = off
    pop_scope: str = "req_as_grant"  # "req_as_grant" | "resp_only"


@dataclass
class Flit:
    pid: int
    txn_id: int
    seq: int
    nflit: int
    src: int
    dst: int
    kind: Kind
    t_gen: int
    plane: PlaneId = 0
    dir: Dir = 1
    idx: int = 0
    target: int = 0
    deflections: int = 0
    e_tag: bool = False
    t_inject: int = -1
    fail_board: int = 0
    fail_eject: int = 0


class Ring2BaseSim:
    """Cycle-driven reactive dual-plane ring. Drive with `offer_txn` + `step`."""

    def __init__(self, topo: Ring2Topology, params: Ring2BaseParams | None = None,
                 seed: int = 0):
        self.topo = topo
        self.p = params or Ring2BaseParams()
        self.rng = random.Random(seed)
        self.t = 0
        self.n = topo.n
        self.n_planes = topo.n_planes
        self.sigma = topo.sigma
        self.hop_lat = topo.hop_lat

        self.seg_free: dict[Any, int] = defaultdict(int)   # (p, dir, idx) -> t
        self.arrivals: dict[int, list[Flit]] = defaultdict(list)
        self.arr_set: dict[Any, set[int]] = defaultdict(set)

        # boarding queue (`inj_depth` deep) and the PE-side backlog behind it
        self.srcq: dict[Any, deque[Flit]] = defaultdict(deque)   # (node, plane)
        self.pending: dict[Any, deque[Flit]] = defaultdict(deque)
        self.inj_starve: dict[Any, int] = defaultdict(int)
        self.i_tag: dict[Any, set[int]] = defaultdict(set)       # (p, dir)
        self.ejectq: dict[Any, deque[Flit]] = defaultdict(deque)
        self.resv_used: dict[Any, int] = defaultdict(int)
        self.eject_rr: dict[Any, int] = defaultdict(int)         # (node, plane)

        self.rr_state: dict[int, int] = {}
        self.occ: dict[int, int] = defaultdict(int)              # plane occupancy

        self.ha_ready: dict[int, list[tuple[int, Txn]]] = defaultdict(list)
        self.txn_by_id: dict[int, Txn] = {}
        self.resp_left: dict[int, int] = {}
        self.req_fail: dict[int, tuple[int, int]] = {}           # tid -> fails

        self.delivered: list[tuple[Flit, int]] = []
        self.txn_done: list[tuple[int, int]] = []                # tid, t
        self.recv_times: dict[int, list[int]] = defaultdict(list)
        self.resp_lat: list[int] = []
        # dest-core board stats for response data (dir +1 = CW, -1 = CCW)
        self.board_ok_cw: dict[int, int] = defaultdict(int)
        self.board_ok_ccw: dict[int, int] = defaultdict(int)
        self.board_fail_cw: dict[int, int] = defaultdict(int)
        self.board_fail_ccw: dict[int, int] = defaultdict(int)
        self.active_src: set[tuple] = set()
        self.active_ej: set[tuple] = set()
        self.keep_flits: bool = False
        self.st: dict[str, Any] = {
            "n_offered_req": 0, "n_offered_resp": 0,
            "n_injected": 0, "n_delivered_flits": 0,
            "n_delivered_req": 0, "n_delivered_resp": 0,
            "n_txn_done": 0, "n_deflections": 0,
            "n_etag_raised": 0, "n_itag_raised": 0,
            "n_inring_blocked": 0, "n_eject_full_deflect": 0,
            "n_board_fail": 0, "max_inj_starve": 0,
            "max_deflections": 0, "max_ejectq": 0,
            "max_srcq": 0, "max_pending": 0, "n_admit_stall": 0,
            "n_pull_wait": 0, "n_pull_issued": 0, "max_pull_outstanding": 0,
            "n_outst_wait": 0, "max_core_outstanding": 0,
            "n_aimd_increase": 0, "n_aimd_decrease": 0,
        }
        self._pid = 0
        self._n_txn_target = 0
        self._resp_stash: dict[tuple, Flit] = {}
        self.core_outst: dict[int, int] = defaultdict(int)  # per-core in-flight reads

    # -- routing / plane ----------------------------------------------------

    def _pick_plane(self, src: int, dst: int, kind: Kind, txn_id: int
                    ) -> PlaneId:
        return self.topo.plane_of(src, dst, kind=kind, txn_id=txn_id,
                                  strategy=self.p.plane_sel,
                                  rr_state=self.rr_state,
                                  occupancy=self.occ)

    def _place(self, f: Flit) -> None:
        f.dir = shortest_dir(f.src, f.dst, self.n)
        f.idx = f.src
        f.target = hop_count(f.src, f.dst, f.dir, self.n)

    # -- workload -----------------------------------------------------------

    def offer_txn(self, txn: Txn) -> None:
        self.txn_by_id[txn.txn_id] = txn
        self.resp_left[txn.txn_id] = txn.m_resp
        self._n_txn_target += 1
        plane = self._pick_plane(txn.core, txn.ha, "req", txn.txn_id)
        f = Flit(pid=self._pid, txn_id=txn.txn_id, seq=0, nflit=txn.m_req,
                 src=txn.core, dst=txn.ha, kind="req", t_gen=self.t,
                 plane=plane)
        self._pid += 1
        self._place(f)
        self._offer_flit(f)
        self.st["n_offered_req"] += 1

    def offer_batch(self, txns: Sequence[Txn]) -> None:
        for t in txns:
            self.offer_txn(t)

    def _enqueue_resps(self, txn: Txn, fail_board: int, fail_eject: int,
                       t_ready: int) -> None:
        self.req_fail[txn.txn_id] = (fail_board, fail_eject)
        plane = self._pick_plane(txn.ha, txn.core, "resp", txn.txn_id)
        for k in range(txn.m_resp):
            f = Flit(pid=self._pid, txn_id=txn.txn_id, seq=k, nflit=txn.m_resp,
                     src=txn.ha, dst=txn.core, kind="resp", t_gen=t_ready,
                     plane=plane, fail_board=fail_board, fail_eject=fail_eject)
            self._pid += 1
            self._place(f)
            self._resp_stash[(t_ready, txn.ha, txn.txn_id, k)] = f
            self.st["n_offered_resp"] += 1

    def _release_ready_resps(self) -> None:
        ready = [k for k in list(self._resp_stash) if k[0] <= self.t]
        for k in ready:
            self._offer_flit(self._resp_stash.pop(k))

    # -- boarding queue admission -------------------------------------------

    def _offer_flit(self, f: Flit) -> None:
        """A PE hands a flit over; it waits behind the boarding queue."""
        key = (f.src, f.plane)
        self.pending[key].append(f)
        self.st["max_pending"] = max(self.st["max_pending"],
                                     len(self.pending[key]))
        self.active_src.add(key)
        self._admit(key)

    def _admit(self, key: Any) -> None:
        """Move flits into the `inj_depth`-deep boarding queue while it fits."""
        q, pend = self.srcq[key], self.pending[key]
        while pend and len(q) < self.p.inj_depth:
            q.append(pend.popleft())
        if q:
            self.st["max_srcq"] = max(self.st["max_srcq"], len(q))

    def _src_idle(self, key: Any) -> bool:
        return not self.srcq[key] and not self.pending[key]

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)

    # late-bind stash so dataclass-less __init__ stays readable
    def _ensure_stash(self) -> None:
        if not hasattr(self, "_resp_stash"):
            self._resp_stash = {}

    # -- ring primitives ----------------------------------------------------

    def _seg(self, plane: PlaneId, direction: Dir, idx: int) -> Any:
        return (plane, direction, idx)

    def _can_board(self, plane: PlaneId, direction: Dir, idx: int) -> bool:
        """Outgoing slot empty for the whole flit; in-ring traffic is visible."""
        seg = self._seg(plane, direction, idx)
        if self.seg_free[seg] > self.t:
            return False
        node_key = (plane, direction, idx)
        for dt in range(self.sigma):
            if (self.t + dt) in self.arr_set[node_key]:
                return False
        return True

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        seg = self._seg(f.plane, f.dir, f.idx)
        if inring and self.seg_free[seg] > self.t:
            self.st["n_inring_blocked"] += 1
            self._on_inring_block(f)
            self.arrivals[self.t + 1].append(f)
            self.arr_set[(f.plane, f.dir, f.idx)].add(self.t + 1)
            return False
        self.seg_free[seg] = self.t + self.sigma
        nxt = (f.idx + f.dir) % self.n
        lat = self.topo.hop_lat_from(f.idx, f.dir)
        f.idx = nxt
        f.target -= 1
        self.arrivals[self.t + lat].append(f)
        self.arr_set[(f.plane, f.dir, nxt)].add(self.t + lat)
        return True

    def _deflect(self, f: Flit) -> None:
        f.deflections += 1
        f.fail_eject += 1
        self.st["n_deflections"] += 1
        self.st["max_deflections"] = max(self.st["max_deflections"],
                                         f.deflections)
        if f.deflections >= self.p.t_xfer and not f.e_tag:
            f.e_tag = True
            self.st["n_etag_raised"] += 1
        # ride a full revolution: target is n hops away again
        f.target = self.n
        self._launch(f, inring=True)

    def _eject_cap(self, key: Any, e_tag: bool) -> int:
        cap = self.p.eject_depth
        if e_tag:
            cap += self.p.resv_ej
        return cap

    def _try_eject(self, f: Flit) -> bool:
        key = (f.dst, f.plane)
        q = self.ejectq[key]
        cap = self.p.eject_depth
        if len(q) < cap:
            pass
        elif f.e_tag and self.resv_used[key] < self.p.resv_ej:
            self.resv_used[key] += 1
        else:
            return False
        q.append(f)
        self.active_ej.add(key)
        self.st["max_ejectq"] = max(self.st["max_ejectq"], len(q))
        return True

    def _itag_blocks(self, f: Flit, boarding_node: int) -> bool:
        holders = self.i_tag[(f.plane, f.dir)]
        return bool(holders) and boarding_node not in holders

    def _should_raise_itag(self, node: int, f: Flit) -> bool:
        return True

    def _leave_order(self, node: int, plane: PlaneId, reqs: list[Flit]
                     ) -> list[Flit]:
        """Who tries the shared leave port first. Default: RR on direction."""
        if len(reqs) <= 1:
            return reqs
        key = (node, plane)
        pref = self.eject_rr[key] % 2
        reqs.sort(key=lambda f: 0 if ((f.dir > 0) == (pref == 0)) else 1)
        self.eject_rr[key] += 1
        return reqs

    # -- AIMD hooks (no-ops in S0) ------------------------------------------

    def _outst_full(self, core: int) -> bool:
        cap = self.p.core_outstanding
        return cap > 0 and self.core_outst[core] >= cap

    def _pre_inject(self) -> None:
        """Optional same-cycle coordination before the inject loop."""
        return

    def _inject_keys(self) -> list:
        """Source queues to visit this cycle. Default: set order."""
        return list(self.active_src)

    def _select_inject_flit(self, node: int, plane: PlaneId, q) -> Flit | None:
        """Which boarding-queue flit tries the inject port. Default: FIFO head."""
        return q[0] if q else None

    def _may_inject(self, node: int, plane: PlaneId, f: Flit | None = None
                    ) -> bool:
        if f is None or f.kind != "req" or not is_core(f.src):
            return True
        if self._outst_full(f.src):
            self.st["n_outst_wait"] += 1
            return False
        return True

    def _on_inject(self, f: Flit) -> None:
        if f.kind != "req" or not is_core(f.src):
            return
        if self.p.core_outstanding <= 0:
            return
        self.core_outst[f.src] += 1
        self.st["max_core_outstanding"] = max(
            self.st["max_core_outstanding"], self.core_outst[f.src])

    def _ctrl_deliver(self) -> None:
        return

    def _ctrl_issue(self) -> None:
        return

    def _note_board(self, f: Flit, *, ok: bool) -> None:
        if f.kind != "resp":
            return
        dst = f.dst
        if f.dir > 0:
            if ok:
                self.board_ok_cw[dst] += 1
            else:
                self.board_fail_cw[dst] += 1
        else:
            if ok:
                self.board_ok_ccw[dst] += 1
            else:
                self.board_fail_ccw[dst] += 1

    def _on_inring_block(self, f: Flit) -> None:
        return

    def _on_board_fail(self, node: int, f: Flit) -> None:
        f.fail_board += 1
        self.st["n_board_fail"] += 1
        self._note_board(f, ok=False)

    def _on_txn_done(self, txn: Txn, last: Flit) -> None:
        return

    def _aimd_tick(self) -> None:
        return

    # -- one cycle ----------------------------------------------------------

    def step(self) -> None:
        self._ensure_stash()
        self._ctrl_deliver()
        t = self.t
        arrivals = self.arrivals.pop(t, [])
        for f in arrivals:
            self.arr_set[(f.plane, f.dir, f.idx)].discard(t)

        # group arrivals that want to leave this cycle, by (node, plane)
        leave_req: dict[Any, list[Flit]] = defaultdict(list)
        for f in arrivals:
            if f.target > 0:
                self._launch(f, inring=True)
                continue
            leave_req[(f.dst, f.plane)].append(f)

        # at most one leave per (node, plane): RR across the two dirs
        for key, reqs in leave_req.items():
            node, plane = key
            order = self._leave_order(node, plane, reqs)
            ejected = False
            for f in order:
                if not ejected and self._try_eject(f):
                    ejected = True
                    self._on_arrive_station(f)
                else:
                    self.st["n_eject_full_deflect"] += 1
                    self._deflect(f)

        self._release_ready_resps()

        # local injection: one flit per (node, plane) if the slot is free
        self._pre_inject()
        for key in self._inject_keys():
            node, plane = key
            self._admit(key)
            if self.pending[key]:
                self.st["n_admit_stall"] += 1
            q = self.srcq[key]
            if not q:
                self.inj_starve[key] = 0
                self.active_src.discard(key)
                continue
            f = self._select_inject_flit(node, plane, q)
            if f is None:
                continue
            if not self._may_inject(node, plane, f):
                # Policy denial (AIMD token, S3 receive window) is not hop
                # starvation. A leftover I-tag would lock out HA responses
                # on this (plane, dir) while the source is not even trying
                # to board — the ring goes empty. Drop it and reset starve.
                self.i_tag[(f.plane, f.dir)].discard(node)
                self.inj_starve[key] = 0
                continue
            if self._itag_blocks(f, node) or \
                    not self._can_board(f.plane, f.dir, f.idx):
                self._on_board_fail(node, f)
                self.inj_starve[key] += 1
                self.st["max_inj_starve"] = max(self.st["max_inj_starve"],
                                                self.inj_starve[key])
                if self.inj_starve[key] >= self.p.t_inj and \
                        self._should_raise_itag(node, f):
                    rk = (f.plane, f.dir)
                    if node not in self.i_tag[rk]:
                        self.i_tag[rk].add(node)
                        self.st["n_itag_raised"] += 1
                continue
            if f is q[0]:
                q.popleft()
            else:
                q.remove(f)
            self._admit(key)
            if self._src_idle(key):
                self.active_src.discard(key)
            self.i_tag[(f.plane, f.dir)].discard(node)
            self.inj_starve[key] = 0
            f.t_inject = t
            self.st["n_injected"] += 1
            self._note_board(f, ok=True)
            self._on_inject(f)
            self._launch(f, inring=False)

        # PE drains the per-plane eject queue
        for key in list(self.active_ej):
            q = self.ejectq[key]
            for _ in range(self.p.eject_bw):
                if not q:
                    break
                f = q.popleft()
                if self.resv_used[key] > 0 and len(q) >= self.p.eject_depth:
                    self.resv_used[key] -= 1
                self._on_pe_drain(f)
            if not q:
                self.active_ej.discard(key)

        # responses with t_ha=0 become ready in this same cycle
        self._release_ready_resps()
        self._aimd_tick()
        self._ctrl_issue()
        self.t += 1

    def _on_arrive_station(self, f: Flit) -> None:
        """Flit has entered the eject queue; occupancy already charged."""
        return

    def _on_pe_drain(self, f: Flit) -> None:
        self.st["n_delivered_flits"] += 1
        if self.keep_flits:
            self.delivered.append((f, self.t))
        if f.kind == "req":
            self.st["n_delivered_req"] += 1
            txn = self.txn_by_id[f.txn_id]
            self._ensure_stash()
            self._enqueue_resps(txn, f.fail_board, f.fail_eject,
                                self.t + self.p.t_ha_service)
        else:
            self.st["n_delivered_resp"] += 1
            self.recv_times[f.dst].append(self.t)
            self.resp_lat.append(self.t - f.t_gen)
            left = self.resp_left[f.txn_id] - 1
            self.resp_left[f.txn_id] = left
            if left == 0:
                self.st["n_txn_done"] += 1
                self.txn_done.append((f.txn_id, self.t))
                txn = self.txn_by_id[f.txn_id]
                if self.p.core_outstanding > 0:
                    self.core_outst[txn.core] = max(
                        0, self.core_outst[txn.core] - 1)
                self._on_txn_done(txn, f)

    # -- introspection ------------------------------------------------------

    def in_flight(self) -> int:
        return (sum(len(v) for v in self.arrivals.values())
                + sum(len(q) for q in self.ejectq.values())
                + len(getattr(self, "_resp_stash", {})))

    def backlog(self) -> int:
        return (sum(len(q) for q in self.srcq.values())
                + sum(len(q) for q in self.pending.values()))

    def done(self) -> bool:
        return self.st["n_txn_done"] >= self._n_txn_target and self._n_txn_target > 0

    def summary(self) -> dict[str, Any]:
        out = {k: (dict(v) if isinstance(v, defaultdict) else v)
               for k, v in self.st.items()}
        out["t"] = self.t
        out["backlog"] = self.backlog()
        out["in_flight"] = self.in_flight()
        out["n_txn_target"] = self._n_txn_target
        out["completed"] = self.done()
        out["makespan"] = self.t
        lat = sorted(self.resp_lat)
        if not lat and self.delivered:
            lat = sorted(t - f.t_gen for f, t in self.delivered if f.kind == "resp")
        if lat:
            out["lat_p50"] = lat[len(lat) // 2]
            out["lat_p99"] = lat[min(len(lat) - 1, int(0.99 * len(lat)))]
            out["lat_max"] = lat[-1]
        out["board_by_core"] = self.board_by_core()
        out["core_outstanding"] = self.p.core_outstanding
        out["core_outst"] = dict(self.core_outst)
        return out

    def recv_by_core(self) -> dict[int, list[int]]:
        """Cycle of every response flit drained at a core (receive events)."""
        if self.recv_times:
            return {c: list(ts) for c, ts in self.recv_times.items()}
        out: dict[int, list[int]] = defaultdict(list)
        for f, t in self.delivered:
            if f.kind == "resp":
                out[f.dst].append(int(t))
        return {c: sorted(ts) for c, ts in out.items()}

    def board_by_core(self) -> dict[int, dict[str, int]]:
        """Per-destination-core on-ramp counts for response data."""
        cores = set(self.board_ok_cw) | set(self.board_ok_ccw) | \
            set(self.board_fail_cw) | set(self.board_fail_ccw)
        out: dict[int, dict[str, int]] = {}
        for c in cores:
            ok_cw = self.board_ok_cw[c]
            ok_ccw = self.board_ok_ccw[c]
            fl_cw = self.board_fail_cw[c]
            fl_ccw = self.board_fail_ccw[c]
            out[c] = {
                "board": ok_cw + ok_ccw,
                "board_cw": ok_cw,
                "board_ccw": ok_ccw,
                "board_fail": fl_cw + fl_ccw,
                "board_fail_cw": fl_cw,
                "board_fail_ccw": fl_ccw,
            }
        return out


def run_batch(topo: Ring2Topology, txns: Sequence[Txn], *,
              params: Ring2BaseParams | None = None,
              t_max: int = 2_000_000, seed: int = 0) -> dict[str, Any]:
    sim = Ring2BaseSim(topo, params, seed=seed)
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


if __name__ == "__main__":
    import json
    from rg_ring2_topo import build_allpairs

    topo = Ring2Topology()
    tx = build_allpairs(m=1, m_resp=4)
    r = run_batch(topo, tx)
    keep = ("completed", "makespan", "n_txn_done", "n_txn_target",
            "n_delivered_flits", "n_delivered_req", "n_delivered_resp",
            "n_deflections", "n_etag_raised", "n_itag_raised",
            "n_inring_blocked", "n_eject_full_deflect", "n_board_fail",
            "max_inj_starve", "max_srcq", "max_ejectq", "n_admit_stall",
            "lat_p50", "lat_p99", "lat_max")
    print(json.dumps({k: r.get(k) for k in keep}, indent=2))
