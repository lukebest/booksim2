#!/usr/bin/env python3
"""S0 baseline for the 3D-stacked fabric: route-following cycle simulator.

Why this is not `Ring2BaseSim`
-----------------------------
The ring simulator inlines its geometry into movement: a flit's entire route
is `(dir, hops_remaining)`, the next hop is `(idx + dir) % n`, and a failed
eject is expressed as `target = n`. None of that survives a fabric where one
transfer crosses a top-die ring, a die boundary, a horizontal half ring and a
vertical half ring. Here a flit carries an explicit list of directed edges and
walks it, so a turn, a die crossing and a revolution are all just edits to
that list. The reusable parts -- CHI WriteNoSnp phasing, boarding queues,
per-VC occupancy, the completer hooks -- are kept.

Resources and failure points
----------------------------
R1  link  -- one flit per (directed edge, CHI VC) per sigma cycles.
R2  tap   -- a flit may only *leave* a given ring at a given station once per
             cycle, whether it leaves to a PE or to another ring.
R3  eject -- the destination PE's eject queue is `eject_depth` deep.
R4  turn  -- changing ring, or crossing the die boundary, passes through a
             bounded transfer FIFO.

In-ring priority is absolute and is enforced by *ordering*, not by lookahead:
arrivals claim their outgoing edge before any FIFO or PE gets to try, so a
local injector can never displace traffic already on the ring. When a flit
cannot leave its ring it deflects -- one full revolution of the ring it is on.
Strict bufferlessness therefore still holds on the links; it does not hold at
the turns, and the FIFO occupancy those need is measured and reported rather
than assumed away.

CHI WriteNoSnp
--------------
    REQ (core->HA) -> DBIDResp (HA->core) -> WriteData xW (core->HA)
    -> Comp (HA->core)

REQ / RSP / DAT are independent VCs. `_on_req_at_completer` and
`_on_write_data_complete` are the same hooks the ring simulator exposes, so a
receiver-driven admission scheme drops in unchanged.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Sequence

from rg_stack_topo import StackTopology, Txn, vc_of

CORE_OUTSTANDING_WR = 128


@dataclass
class StackBaseParams:
    sigma: int = 1
    inj_depth: int = 8              # boarding queue per (station, plane, vc)
    eject_depth: int = 4            # destination PE queue
    eject_bw: int = 1               # PE drain per station per cycle
    t_inj: int = 64                 # inject starve cycles -> I-tag
    t_xfer: int = 4                 # deflections -> E-tag
    t_ha_service: int = 0
    per_vc_srcq: bool = True        # WriteNoSnp needs REQ not to head-block DAT
    core_outstanding: int = CORE_OUTSTANDING_WR
    # HA request-tracker entries. A completer that runs out of them cannot
    # queue the request: CHI makes it reject with RetryAck and hand out a
    # PCrdGrant later. 0 keeps the old unlimited-completer behaviour.
    ha_pos_depth: int = 16
    turn_depth: int = 4             # ring -> ring transfer FIFO
    d2d_depth: int = 8              # die-crossing FIFO
    resv_ej: int = 1                # eject slots only an E-tagged flit may use
    # Transfer-FIFO entries only an E-tagged flit may occupy. Measured to be
    # counterproductive here and left off by default: it withholds scarce
    # turn capacity from the common case, and once the fabric is congested
    # every flit is E-tagged anyway, so it buys nothing and costs a lap.
    resv_turn: int = 0
    plane_sel: str = "least_occupied"


@dataclass
class Flit:
    pid: int
    txn_id: int
    seq: int
    nflit: int
    src: int
    dst: int
    kind: str
    t_gen: int
    plane: int = 0
    vc: str = "req"
    route: tuple[int, ...] = ()
    hop: int = 0                    # index of the next edge on the route
    # Deflection laps still owed. Kept apart from `route` so a deflection is
    # O(lap) rather than a rebuild of a route that grows without bound.
    detour: list[int] = field(default_factory=list)
    dpos: int = 0
    node: int = 0                   # station it is sitting at
    ring: Any = None                # ring of the edge it arrived on
    dir: int = 0
    deflections: int = 0
    e_tag: bool = False
    t_inject: int = -1
    fail_board: int = 0
    fail_eject: int = 0
    held: bool = False
    n_turn: int = 0
    turn_ready: int = 0             # cycle the turn FIFO may launch this flit


class StackBaseSim:
    """Cycle-driven simulator over the stacked fabric. Drive with `step`."""

    def __init__(self, topo: StackTopology,
                 params: StackBaseParams | None = None, seed: int = 0):
        self.topo = topo
        self.p = params or StackBaseParams()
        self.rng = random.Random(seed)
        self.t = 0
        self.n = topo.n
        self.sigma = self.p.sigma
        self.n_planes = 2

        self.seg_free: dict[Any, int] = defaultdict(int)     # (eid, vc) -> t
        self.arrivals: dict[int, list[Flit]] = defaultdict(list)

        self.srcq: dict[Any, deque[Flit]] = defaultdict(deque)
        self.pending: dict[Any, deque[Flit]] = defaultdict(deque)
        self.inj_starve: dict[Any, int] = defaultdict(int)
        self.i_tag: dict[Any, set[int]] = defaultdict(set)   # (ring, vc)
        self.ejectq: dict[Any, deque[Flit]] = defaultdict(deque)
        self.resv_used: dict[Any, int] = defaultdict(int)
        # Insertion-ordered "sets": a plain set iterates in hash order, which
        # varies with PYTHONHASHSEED between processes and silently becomes an
        # arbitration tie-break -- near the concurrency cliff that flipped
        # whole runs between draining and livelocking. A dict keeps insertion
        # order, so service is first-come-first-served: reproducible, and
        # without the static index bias that sorting would introduce into a
        # fairness study.
        self.active_src: dict[Any, None] = {}
        self.active_ej: dict[Any, None] = {}

        # transfer FIFOs: (node, next_ring) -> flits waiting to board it
        self.xq: dict[Any, deque[Flit]] = defaultdict(deque)
        self.active_xq: dict[Any, None] = {}
        self.xq_peak: dict[Any, int] = defaultdict(int)
        self._land_now: dict[int, int] = defaultdict(int)

        self.occ: dict[int, int] = defaultdict(int)          # plane balance
        self._vc_list: tuple[str, ...] = tuple(topo.vcs)
        self.vc_rr: dict[Any, int] = defaultdict(int)
        self.tap_rr: dict[Any, int] = defaultdict(int)

        self.txn_by_id: dict[int, Txn] = {}
        self.delivered: list[tuple[Flit, int]] = []
        self.txn_done: list[tuple[int, int]] = []
        self.resp_lat: list[int] = []
        self.keep_flits = False

        self.st: dict[str, Any] = {
            "n_offered_req": 0, "n_injected": 0, "n_delivered_flits": 0,
            "n_txn_done": 0, "n_deflections": 0, "n_etag_raised": 0,
            "n_itag_raised": 0, "n_inring_blocked": 0,
            "n_eject_full_deflect": 0, "n_turn_full_deflect": 0,
            "n_tap_deflect": 0, "n_board_fail": 0, "n_turn_board_fail": 0,
            "max_inj_starve": 0, "max_deflections": 0, "max_ejectq": 0,
            "max_srcq": 0, "max_pending": 0, "n_admit_stall": 0,
            "n_outst_wait": 0, "max_core_outstanding": 0,
            "max_turn_q": 0, "max_d2d_q": 0, "n_d2d_stall": 0,
            "max_d2d_landing": 0, "n_turn_resv_used": 0,
            "max_inring_hold": 0, "n_turns": 0,
            "n_fc_deny": 0, "n_aimd_increase": 0, "n_aimd_decrease": 0,
        }
        self.inring_hold: dict[Any, int] = defaultdict(int)
        self._pid = 0
        self._n_txn_target = 0
        self._stash: dict[tuple, Flit] = {}
        self.core_outst: dict[int, int] = defaultdict(int)

        # -- credit retry and its two consequences -------------------------
        # `core_outst` counts every transaction the core cannot retire yet,
        # including those parked waiting for a P-Credit. Those make no
        # forward progress, so the *effective* concurrency is the difference.
        self.ha_used: dict[int, int] = defaultdict(int)
        self.pcrd_q: dict[int, deque[int]] = defaultdict(deque)
        self.parked: set[int] = set()      # awaiting PCrdGrant
        self._granted: set[int] = set()    # holds a P-Credit, will be accepted
        self._counted: set[int] = set()    # already charged to core_outst
        self.retry_by_core: dict[int, int] = defaultdict(int)
        self._park_t0: dict[int, int] = {}
        self.park_wait: list[int] = []
        self._eff_sum = 0
        self._nom_sum = 0
        self._conc_samples = 0
        # completion order per core, in units of the core's own issue rank,
        # which is what makes reordering measurable
        self._issue_rank: dict[int, int] = {}
        self._issued: dict[int, int] = defaultdict(int)
        self.compl_ranks: dict[int, list[int]] = defaultdict(list)
        self.hop_starts: list[int] = []
        self.fabric_hops: dict[str, int] = defaultdict(int)
        self.edge_load: dict[int, int] = defaultdict(int)

        self.wdata_left: dict[int, int] = {}
        self.wr_t0: dict[int, int] = {}
        self.wr_tinj: dict[int, int] = {}
        self.net_lat: list[int] = []
        self.wr_inject_times: dict[int, list[int]] = defaultdict(list)
        self.wr_recv_times: dict[int, list[int]] = defaultdict(list)
        self.board_fail_cause: dict[Any, dict[str, int]] = defaultdict(
            lambda: defaultdict(int))
        self.board_ok_by_src: dict[Any, int] = defaultdict(int)
        # pass-through vs. injected flits at every station, per ring: the
        # measured version of "how loaded is the slot I am trying to take"
        self.pass_through: dict[Any, int] = defaultdict(int)
        self.inj_ok_at: dict[Any, int] = defaultdict(int)
        self.inj_fail_at: dict[Any, int] = defaultdict(int)
        # Top-die ring direction: +1 CW (index+), -1 CCW. Counted only for
        # AI-core injects (REQ + WriteData). Policy denials are not failures.
        self.board_ok_dir: dict[tuple[int, int], int] = defaultdict(int)
        self.board_fail_dir: dict[tuple[int, int], int] = defaultdict(int)
        self._fail_cause = "hop_busy"
        self._deny_cause = "outstanding"

        self._is_top = [not nd.on_bottom for nd in topo.nodes]
        self._is_core = [nd.role == "core" for nd in topo.nodes]

    # -- keys ---------------------------------------------------------------

    def _pk(self, node: int, plane: int) -> int:
        """A station has one physical port; only top-die nodes have two planes."""
        return plane if self._is_top[node] else 0

    def _sk(self, node: int, plane: int, vc: str) -> Any:
        p = self._pk(node, plane)
        return (node, p, vc) if self.p.per_vc_srcq else (node, p)

    def _src_keys(self, node: int, plane: int) -> list[Any]:
        if not self.p.per_vc_srcq:
            return [(node, self._pk(node, plane))]
        vcs = self._vc_list
        p = self._pk(node, plane)
        off = self.vc_rr[(node, p)] % len(vcs)
        return [(node, p, vcs[(off + i) % len(vcs)]) for i in range(len(vcs))]

    def _ejk(self, node: int, plane: int) -> Any:
        return (node, self._pk(node, plane))

    # -- routing ------------------------------------------------------------

    def _pick_plane(self, src: int, dst: int) -> int:
        if not (self._is_top[src] or self._is_top[dst]):
            return 0
        if self.p.plane_sel == "least_occupied":
            best = min(range(self.n_planes),
                       key=lambda p: (self.occ[p], p))
        else:
            best = self._pid % self.n_planes
        self.occ[best] += 1
        return best

    def _place(self, f: Flit) -> None:
        f.vc = vc_of(f.kind)
        f.route = self.topo.route(f.src, f.dst, f.plane)
        f.hop = 0
        f.node = f.src
        f.ring = None
        f.dir = 0

    # -- workload -----------------------------------------------------------

    def offer_txn(self, txn: Txn) -> None:
        self.txn_by_id[txn.txn_id] = txn
        self.wdata_left[txn.txn_id] = txn.m_wdata
        self.wr_t0[txn.txn_id] = self.t
        self._n_txn_target += 1
        plane = self._pick_plane(txn.core, txn.ha)
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

    def _emit(self, txn: Txn, kind: str, src: int, dst: int, count: int,
              t_ready: int) -> None:
        plane = self._pick_plane(src, dst)
        for k in range(count):
            f = Flit(pid=self._pid, txn_id=txn.txn_id, seq=k, nflit=count,
                     src=src, dst=dst, kind=kind, t_gen=t_ready, plane=plane)
            self._pid += 1
            self._place(f)
            self._stash[(t_ready, src, txn.txn_id, kind, k)] = f
        key = f"n_offered_{kind}"
        self.st[key] = self.st.get(key, 0) + count

    def _release_ready(self) -> None:
        if not self._stash:
            return
        for k in [k for k in self._stash if k[0] <= self.t]:
            self._offer_flit(self._stash.pop(k))

    # -- boarding queue -----------------------------------------------------

    def _offer_flit(self, f: Flit) -> None:
        key = self._sk(f.src, f.plane, f.vc)
        self.pending[key].append(f)
        self.st["max_pending"] = max(self.st["max_pending"],
                                     len(self.pending[key]))
        self.active_src[(f.src, self._pk(f.src, f.plane))] = None
        self._admit(key)

    def _outst_blocked(self, f: Flit) -> bool:
        """A new REQ that cannot take an outstanding slot yet.

        These must not occupy the inject-queue head. The rest of the core's
        closed batch sits behind the window; if they HOL-block a P-Credit
        re-send, the completer's grant can never be used and the fabric
        deadlocks with an empty ring and a full pending list. Re-sends
        already hold their slot (`txn_id in _counted`) and must go first.
        """
        return (f.kind == "req"
                and self._is_core[f.src]
                and f.txn_id not in self._counted
                and self._outst_full(f.src))

    def _admit(self, key: Any) -> None:
        q, pend = self.srcq[key], self.pending[key]
        # Evict new REQs that filled the window after they were admitted.
        if q and self._outst_blocked(q[0]):
            stuck = deque()
            while q and self._outst_blocked(q[0]):
                stuck.append(q.popleft())
            stuck.extend(pend)
            self.pending[key] = pend = stuck
        # A P-Credit re-send already holds its outstanding slot. It must
        # pass the rest of the closed batch (still waiting on a free
        # slot) or the grant is delivered into a queue that never moves.
        skipped = deque()
        while pend and len(q) < self.p.inj_depth:
            f = pend.popleft()
            if self._outst_blocked(f):
                skipped.append(f)
                continue
            q.append(f)
        if skipped:
            skipped.extend(pend)
            self.pending[key] = skipped
        if q:
            self.st["max_srcq"] = max(self.st["max_srcq"], len(q))

    def _src_idle(self, key: Any) -> bool:
        return not self.srcq[key] and not self.pending[key]

    def _clear_itag(self, node: int) -> None:
        """Drop leftover I-tags. A port with nothing injectable must not
        keep the ring reserved; that blocks everyone else on an empty hop."""
        for holders in self.i_tag.values():
            holders.discard(node)

    def _wake_core(self, core: int) -> None:
        """Re-admit a core after an outstanding slot is freed."""
        for plane in range(self.n_planes):
            self.active_src[(core, plane)] = None
            for qk in self._src_keys(core, plane):
                self._admit(qk)

    # -- movement -----------------------------------------------------------

    def _next_edge(self, f: Flit) -> int:
        """The edge this flit wants next: a deflection lap outranks the route."""
        if f.dpos < len(f.detour):
            return f.detour[f.dpos]
        return f.route[f.hop]

    def _at_dest(self, f: Flit) -> bool:
        return f.dpos >= len(f.detour) and f.hop >= len(f.route)

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        eid = self._next_edge(f)
        seg = (eid, f.vc)
        if self.seg_free[seg] > self.t:
            if inring:
                self.st["n_inring_blocked"] += 1
                self._hold(f, seg)
            return False
        if f.held:
            f.held = False
            self.inring_hold[seg] -= 1
        self.seg_free[seg] = self.t + self.sigma
        self.hop_starts.append(self.t)
        rk = self.topo.edge_ring[eid]
        if inring and f.ring == rk:
            self.pass_through[(f.node, rk, f.vc)] += 1
        self.edge_load[eid] += 1
        self.fabric_hops[rk[0]] += 1
        if f.dpos < len(f.detour):
            f.dpos += 1
            if f.dpos >= len(f.detour):
                f.detour = []
                f.dpos = 0
        else:
            f.hop += 1
        f.ring = rk
        f.dir = self.topo.edge_dir[eid]
        f.node = self.topo.edges[eid][1]
        self.arrivals[self.t + self.topo.edge_lat[eid]].append(f)
        return True

    def _hold(self, f: Flit, seg: Any) -> None:
        if not f.held:
            f.held = True
            self.inring_hold[seg] += 1
            self.st["max_inring_hold"] = max(self.st["max_inring_hold"],
                                             self.inring_hold[seg])
        self.arrivals[self.t + 1].append(f)

    def _deflect(self, f: Flit) -> None:
        """No room to leave the ring: ride one full revolution of it."""
        f.deflections += 1
        f.fail_eject += 1
        self.st["n_deflections"] += 1
        self.st["max_deflections"] = max(self.st["max_deflections"],
                                         f.deflections)
        if f.deflections >= self.p.t_xfer and not f.e_tag:
            f.e_tag = True
            self.st["n_etag_raised"] += 1
        lap = self.topo.lap(f.ring, f.node, f.dir)
        f.detour = list(lap) + f.detour[f.dpos:]
        f.dpos = 0
        self._launch(f, inring=True)

    def _try_eject(self, f: Flit) -> bool:
        key = self._ejk(f.dst, f.plane)
        q = self.ejectq[key]
        if len(q) < self.p.eject_depth:
            pass
        elif f.e_tag and self.resv_used[key] < self.p.resv_ej:
            self.resv_used[key] += 1
        else:
            return False
        q.append(f)
        self.active_ej[key] = None
        self.st["max_ejectq"] = max(self.st["max_ejectq"], len(q))
        self._on_arrive_station(f)
        return True

    def _xdepth(self, ring: Any) -> int:
        return self.p.d2d_depth if ring[0] == "d2d" else self.p.turn_depth

    def _try_turn(self, f: Flit) -> bool:
        """Hand a flit to the transfer FIFO of the ring it wants next.

        The last `resv_turn` entries are reserved for E-tagged flits, so a
        flit that has already paid for a revolution is not made to pay again.
        """
        nxt = self.topo.edge_ring[self._next_edge(f)]
        key = (f.node, nxt)
        q = self.xq[key]
        cap = self._xdepth(nxt)
        if len(q) >= cap:
            return False
        if len(q) >= cap - self.p.resv_turn:
            if not f.e_tag:
                return False
            self.st["n_turn_resv_used"] += 1
        q.append(f)
        f.n_turn += 1
        # Attach-point H <-> V turn pays turn_lat before it may re-board.
        # D2D landings do not: their latency is already on the D2D edge.
        cur = f.ring[0] if f.ring is not None else None
        nxtk = nxt[0]
        if {cur, nxtk} == {"h", "v"}:
            f.turn_ready = self.t + self.topo.turn_lat
        else:
            f.turn_ready = self.t
        self.st["n_turns"] += 1
        self.active_xq[key] = None
        depth = len(q)
        self.xq_peak[key] = max(self.xq_peak[key], depth)
        slot = "max_d2d_q" if nxt[0] == "d2d" else "max_turn_q"
        self.st[slot] = max(self.st[slot], depth)
        return True

    # -- policy hooks (no-ops in S0) ---------------------------------------

    def _outst_full(self, core: int) -> bool:
        cap = self.p.core_outstanding
        return cap > 0 and self.core_outst[core] >= cap

    def _pre_inject(self) -> None:
        return

    def _may_inject(self, node: int, plane: int, f: Flit | None = None) -> bool:
        if f is None or f.kind != "req" or not self._is_core[f.src]:
            return True
        if f.txn_id in self._counted:
            return True          # re-send: already holds its slot
        if self._outst_full(f.src):
            self.st["n_outst_wait"] += 1
            self._deny_cause = "outstanding"
            return False
        return True

    def _note_deny(self, node: int, f: Flit) -> None:
        self.board_fail_cause[(node, f.vc)][self._deny_cause] += 1

    def _inject_dir(self, f: Flit) -> int:
        """CW (+1) or CCW (-1) of the first hop this flit will take."""
        return self.topo.edge_dir[self._next_edge(f)]

    def _note_core_board(self, f: Flit, *, ok: bool) -> None:
        if not self._is_core[f.src]:
            return
        d = self._inject_dir(f)
        f.dir = d
        slot = self.board_ok_dir if ok else self.board_fail_dir
        slot[(f.src, d)] += 1

    def _on_inject(self, f: Flit) -> None:
        self.board_ok_by_src[(f.src, f.vc)] += 1
        self.inj_ok_at[(f.src, f.vc)] += 1
        self._note_core_board(f, ok=True)
        if f.kind == "wdata":
            self.wr_inject_times[f.src].append(self.t)
        if f.kind != "req" or not self._is_core[f.src]:
            return
        # Batch latency is measured from the offer, which for a closed batch
        # is t=0 for every transaction and therefore mostly source backlog.
        # Network latency is measured from the cycle the REQ actually boards.
        # A re-sent request is the same transaction, so it must not be charged
        # to the outstanding budget twice, and its original injection time is
        # the one that makes latency include the retry round trip.
        if f.txn_id in self._counted:
            self.st["n_req_resent"] = self.st.get("n_req_resent", 0) + 1
            return
        self._counted.add(f.txn_id)
        self.wr_tinj[f.txn_id] = self.t
        self._issue_rank[f.txn_id] = self._issued[f.src]
        self._issued[f.src] += 1
        if self.p.core_outstanding <= 0:
            return
        self.core_outst[f.src] += 1
        self.st["max_core_outstanding"] = max(
            self.st["max_core_outstanding"], self.core_outst[f.src])

    def _on_board_fail(self, node: int, f: Flit) -> None:
        f.fail_board += 1
        self.st["n_board_fail"] += 1
        self.board_fail_cause[(node, f.vc)][self._fail_cause] += 1
        self.inj_fail_at[(node, f.vc)] += 1
        self._note_core_board(f, ok=False)

    def _on_arrive_station(self, f: Flit) -> None:
        return

    def _on_inring_block(self, f: Flit) -> None:
        return

    def _on_txn_done(self, txn: Txn, last: Flit) -> None:
        return

    def _ctrl_deliver(self) -> None:
        return

    def _ctrl_issue(self) -> None:
        return

    def _aimd_tick(self) -> None:
        return

    def _itag_blocks(self, f: Flit, node: int) -> bool:
        eid = self._next_edge(f)
        holders = self.i_tag[(self.topo.edge_ring[eid], f.vc)]
        return bool(holders) and node not in holders

    # -- one cycle ----------------------------------------------------------

    def step(self) -> None:
        self._ctrl_deliver()
        self._sample_concurrency()
        t = self.t
        arrivals = self.arrivals.pop(t, [])
        self._land_now.clear()

        # Phase 1 -- in-ring continuation. Absolute priority: these claim
        # their outgoing edge before any FIFO or PE is allowed to try.
        leave: dict[Any, list[Flit]] = defaultdict(list)
        for f in arrivals:
            if not self._at_dest(f) and \
                    f.ring == self.topo.edge_ring[self._next_edge(f)]:
                self._launch(f, inring=True)
            else:
                leave[(f.node, f.ring)].append(f)

        # Phase 2 -- leaving a ring: to a PE, or to another ring's FIFO.
        # One flit may leave a given ring at a given station per cycle; the
        # rest deflect a full revolution.
        for key, reqs in leave.items():
            node, ring = key
            on_ring = ring is not None and ring[0] != "d2d"
            tapped = False
            for f in self._tap_order(node, ring, reqs) if on_ring else reqs:
                if tapped:
                    self.st["n_tap_deflect"] += 1
                    self._deflect(f)
                    continue
                ok = (self._try_eject(f) if self._at_dest(f)
                      else self._try_turn(f))
                if ok:
                    tapped = on_ring
                    continue
                if self._at_dest(f):
                    self.st["n_eject_full_deflect"] += 1
                else:
                    self.st["n_turn_full_deflect"] += 1
                if on_ring:
                    self._deflect(f)
                else:
                    # Arrived over a die crossing, which is not a ring, so
                    # there is nowhere to circulate: hold at the landing and
                    # escalate, or the flit would wait on an equal footing
                    # with fresh arrivals for as long as the FIFO stays full.
                    self.st["n_d2d_stall"] += 1
                    f.fail_eject += 1
                    if f.fail_eject >= self.p.t_xfer and not f.e_tag:
                        f.e_tag = True
                        self.st["n_etag_raised"] += 1
                    self._land_now[node] = self._land_now[node] + 1
                    self.st["max_d2d_landing"] = max(
                        self.st["max_d2d_landing"], self._land_now[node])
                    self.arrivals[t + 1].append(f)

        self._release_ready()

        # Phase 3 -- transfer FIFOs board their outgoing ring. Ranked above
        # new injection: a flit already inside the fabric has consumed
        # resources, and starving a bounded FIFO would back pressure into
        # deflection storms upstream.
        self._drain_xfer()

        # Phase 4 -- PE injection.
        self._pre_inject()
        self._inject()

        # Phase 5 -- PE drains its eject queue.
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
                self.active_ej.pop(key, None)

        self._release_ready()
        self._aimd_tick()
        self._ctrl_issue()
        self.t += 1

    def _tap_order(self, node: int, ring: Any, reqs: list[Flit]) -> list[Flit]:
        """Who gets the ring's tap. Oldest-deflected first, so a flit that
        has already circulated is not passed over again."""
        if len(reqs) <= 1:
            return reqs
        self.tap_rr[(node, ring)] += 1
        off = self.tap_rr[(node, ring)]
        idx = list(range(len(reqs)))
        idx.sort(key=lambda i: (-reqs[i].deflections,
                                (i + off) % len(reqs)))
        return [reqs[i] for i in idx]

    def _drain_xfer(self) -> None:
        for key in list(self.active_xq):
            q = self.xq[key]
            if not q:
                self.active_xq.pop(key, None)
                continue
            f = q[0]
            if f.turn_ready > self.t:
                continue
            if self._launch(f, inring=False):
                q.popleft()
                if not q:
                    self.active_xq.pop(key, None)
            else:
                self.st["n_turn_board_fail"] += 1

    def _inject(self) -> None:
        for key in list(self.active_src):
            node, plane = key
            qkeys = self._src_keys(node, plane)
            stalled = False
            for qk in qkeys:
                self._admit(qk)
                stalled = stalled or bool(self.pending[qk])
            if stalled:
                self.st["n_admit_stall"] += 1
            if not any(self.srcq[qk] for qk in qkeys):
                # Pending new REQs may still be waiting on outstanding.
                # Leave the port active so a later Comp can admit them;
                # drop I-tag so a full window does not pin the ring.
                self._clear_itag(node)
                self.inj_starve[key] = 0
                if not any(self.pending[qk] for qk in qkeys):
                    self.active_src.pop(key, None)
                continue
            qk, f, denied, idx = None, None, None, 0
            for cand in qkeys:
                q = self.srcq[cand]
                if not q:
                    continue
                for i, cf in enumerate(q):
                    if self._may_inject(node, plane, cf):
                        qk, f, idx = cand, cf, i
                        break
                    if denied is None:
                        denied = cf
                if f is not None:
                    break
            if f is None:
                if denied is None:
                    continue
                f = denied
            if qk is None:
                # A policy refused the port; that is not hop starvation, and
                # leaving an I-tag set would lock the ring out for nothing.
                self._note_deny(node, f)
                self.i_tag[(self.topo.edge_ring[self._next_edge(f)],
                            f.vc)].discard(node)
                self.inj_starve[key] = 0
                continue
            if self._itag_blocks(f, node):
                self._fail_cause = "itag"
            elif self.seg_free[(self._next_edge(f), f.vc)] > self.t:
                self._fail_cause = "hop_busy"
            else:
                self._fail_cause = ""
            if self._fail_cause:
                self._on_board_fail(node, f)
                self.inj_starve[key] += 1
                self.st["max_inj_starve"] = max(self.st["max_inj_starve"],
                                                self.inj_starve[key])
                if self.inj_starve[key] >= self.p.t_inj:
                    rk = (self.topo.edge_ring[self._next_edge(f)], f.vc)
                    if node not in self.i_tag[rk]:
                        self.i_tag[rk].add(node)
                        self.st["n_itag_raised"] += 1
                continue
            del self.srcq[qk][idx]
            self._admit(qk)
            self.vc_rr[key] += 1
            if all(self._src_idle(k) for k in qkeys):
                self.active_src.pop(key, None)
            self.i_tag[(self.topo.edge_ring[self._next_edge(f)],
                        f.vc)].discard(node)
            self.inj_starve[key] = 0
            f.t_inject = self.t
            self.st["n_injected"] += 1
            self._on_inject(f)
            self._launch(f, inring=False)

    # -- CHI WriteNoSnp phases ---------------------------------------------

    def _ha_take_credit(self, txn: Txn) -> bool:
        """CHI credit check. False means the request was bounced with RetryAck.

        A completer cannot silently queue a request it has no tracker entry
        for. It answers RetryAck, remembers that it owes the requester a
        P-Credit, and the requester parks the request until the PCrdGrant
        arrives -- then re-sends it. That costs two RSP messages and a second
        REQ traversal, and it reorders the request stream, because the bounced
        request restarts behind requests that were issued after it.
        """
        d = self.p.ha_pos_depth
        if d <= 0:
            return True
        if txn.txn_id in self._granted:
            # arrived holding a P-Credit: acceptance is guaranteed and the
            # entry was already reserved when the grant was sent
            self._granted.discard(txn.txn_id)
            return True
        if self.ha_used[txn.ha] < d:
            self.ha_used[txn.ha] += 1
            return True
        self.pcrd_q[txn.ha].append(txn.txn_id)
        self.parked.add(txn.txn_id)
        self._park_t0[txn.txn_id] = self.t
        self.st["n_retry"] = self.st.get("n_retry", 0) + 1
        self.retry_by_core[txn.core] += 1
        self._on_retry(txn)
        self._emit(txn, "retry", txn.ha, txn.core, 1,
                   self.t + self.p.t_ha_service)
        return False

    def _ha_free_credit(self, txn: Txn) -> None:
        """Release the tracker entry and hand it to the longest waiter."""
        if self.p.ha_pos_depth <= 0:
            return
        self.ha_used[txn.ha] = max(0, self.ha_used[txn.ha] - 1)
        q = self.pcrd_q[txn.ha]
        if not q:
            return
        tid = q.popleft()
        nxt = self.txn_by_id[tid]
        self.ha_used[nxt.ha] += 1          # reserved for the grantee
        self._granted.add(tid)
        self.st["n_pcrd"] = self.st.get("n_pcrd", 0) + 1
        self._emit(nxt, "pcrd", nxt.ha, nxt.core, 1,
                   self.t + self.p.t_ha_service)

    def _on_retry(self, txn: Txn) -> None:
        """Hook at the completer: it just bounced this request."""
        return

    def _on_retry_at_requester(self, txn: Txn) -> None:
        """Hook at the requester: its RetryAck arrived. Congestion signal."""
        return

    def _sample_concurrency(self) -> None:
        """Nominal vs effective concurrency, once per cycle.

        The outstanding register bounds the *nominal* count. What determines
        whether a core can cover the round trip is the effective count: the
        transactions actually moving, excluding those parked on a P-Credit.
        """
        nom = sum(self.core_outst.values())
        parked = len(self.parked)
        self._nom_sum += nom
        self._eff_sum += nom - parked
        self._conc_samples += 1
        if parked > self.st.get("max_parked", 0):
            self.st["max_parked"] = parked

    def _on_req_at_completer(self, txn: Txn) -> None:
        """Completer decides when to grant its write buffer.

        CHI already puts this at the receiver: WriteData may not be sent until
        DBIDResp comes back. The baseline grants on arrival and throws the
        authority away; a receiver-driven scheme overrides this to pace it.
        """
        if not self._ha_take_credit(txn):
            return
        self._emit(txn, "dbid", txn.ha, txn.core, 1,
                   self.t + self.p.t_ha_service)

    def _on_write_data_complete(self, txn: Txn) -> None:
        return

    def _on_pe_drain(self, f: Flit) -> None:
        self.st["n_delivered_flits"] += 1
        if self.keep_flits:
            self.delivered.append((f, self.t))
        txn = self.txn_by_id[f.txn_id]
        key = f"n_delivered_{f.kind}"
        self.st[key] = self.st.get(key, 0) + 1
        if f.kind == "req":
            self._on_req_at_completer(txn)
        elif f.kind == "dbid":
            self._emit(txn, "wdata", txn.core, txn.ha, txn.m_wdata, self.t)
        elif f.kind == "wdata":
            self.wr_recv_times[f.dst].append(self.t)
            left = self.wdata_left[f.txn_id] - 1
            self.wdata_left[f.txn_id] = left
            if left == 0:
                self._emit(txn, "comp", txn.ha, txn.core, 1,
                           self.t + self.p.t_ha_service)
                self._ha_free_credit(txn)
                self._on_write_data_complete(txn)
        elif f.kind == "retry":
            # The requester now learns it was bounced. This is the only
            # congestion signal CHI already gives it, for free.
            self._on_retry_at_requester(txn)
        elif f.kind == "pcrd":
            self.parked.discard(f.txn_id)
            t0 = self._park_t0.pop(f.txn_id, None)
            if t0 is not None:
                self.park_wait.append(self.t - t0)
            self._emit(txn, "req", txn.core, txn.ha, 1, self.t)
        else:                                    # Comp retires the txn
            self.st["n_txn_done"] += 1
            self.compl_ranks[txn.core].append(self._issue_rank.get(f.txn_id, 0))
            self.txn_done.append((f.txn_id, self.t))
            self.resp_lat.append(self.t - self.wr_t0[f.txn_id])
            t_in = self.wr_tinj.get(f.txn_id)
            if t_in is not None:
                self.net_lat.append(self.t - t_in)
            if self.p.core_outstanding > 0:
                self.core_outst[txn.core] = max(
                    0, self.core_outst[txn.core] - 1)
                self._wake_core(txn.core)
            self._on_txn_done(txn, f)

    def _retry_stats(self) -> dict[str, Any]:
        """Retry cost, the concurrency it wastes, and the reordering it causes.

        Reordering is measured per core against that core's own issue order:
        an inversion is a pair of its transactions that retired in the
        opposite order to the one they were issued in. Normalising by the
        number of pairs gives a 0..1 figure comparable across run lengths.
        """
        n = max(1, self._conc_samples)
        inv = pairs = 0
        worst = 0.0
        for ranks in self.compl_ranks.values():
            m = len(ranks)
            if m < 2:
                continue
            bad = sum(1 for i in range(m) for j in range(i + 1, m)
                      if ranks[j] < ranks[i])
            tot = m * (m - 1) // 2
            inv += bad
            pairs += tot
            worst = max(worst, bad / tot)
        retries = self.st.get("n_retry", 0)
        done = max(1, self.st["n_txn_done"])
        pw = sorted(self.park_wait)
        return {
            "park_wait_mean": round(sum(pw) / len(pw), 1) if pw else 0,
            "park_wait_p99": pw[min(len(pw) - 1, int(0.99 * len(pw)))]
            if pw else 0,
            "n_retry": retries,
            "n_pcrd": self.st.get("n_pcrd", 0),
            "n_req_resent": self.st.get("n_req_resent", 0),
            "retry_per_txn": round(retries / done, 4),
            "max_parked": self.st.get("max_parked", 0),
            "nom_conc_mean": round(self._nom_sum / n, 2),
            "eff_conc_mean": round(self._eff_sum / n, 2),
            "eff_frac": round(self._eff_sum / max(1, self._nom_sum), 4),
            "reorder": round(inv / max(1, pairs), 5),
            "reorder_worst_core": round(worst, 5),
            "retry_by_core": dict(sorted(self.retry_by_core.items())),
        }

    # -- introspection ------------------------------------------------------

    def in_flight(self) -> int:
        return (sum(len(v) for v in self.arrivals.values())
                + sum(len(q) for q in self.ejectq.values())
                + sum(len(q) for q in self.xq.values())
                + len(self._stash))

    def backlog(self) -> int:
        return (sum(len(q) for q in self.srcq.values())
                + sum(len(q) for q in self.pending.values()))

    def done(self) -> bool:
        return (self._n_txn_target > 0
                and self.st["n_txn_done"] >= self._n_txn_target)

    def fifo_report(self) -> dict[str, Any]:
        turn = {k: v for k, v in self.xq_peak.items() if k[1][0] != "d2d"}
        d2d = {k: v for k, v in self.xq_peak.items() if k[1][0] == "d2d"}
        return {
            "n_turn_fifo": len(turn), "n_d2d_fifo": len(d2d),
            "turn_peak": max(turn.values()) if turn else 0,
            "d2d_peak": max(d2d.values()) if d2d else 0,
            "turn_depth": self.p.turn_depth, "d2d_depth": self.p.d2d_depth,
            "turn_flits": sum(turn.values()), "d2d_flits": sum(d2d.values()),
            "d2d_landing_peak": self.st["max_d2d_landing"],
            "n_d2d_stall": self.st["n_d2d_stall"],
            "resv_turn": self.p.resv_turn,
            "n_turn_resv_used": self.st["n_turn_resv_used"],
            "residual_xq": sum(len(q) for q in self.xq.values()),
        }

    def fabric_util(self, makespan: int) -> dict[str, Any]:
        cap = self.topo.capacity()
        out: dict[str, Any] = {}
        for k, hops in sorted(self.fabric_hops.items()):
            links = cap.get(k, 1) * self.topo.n_vc
            out[k] = {
                "flit_hops": hops, "links": cap.get(k, 1),
                "util": round(hops / max(1, links * makespan), 4),
            }
        return out

    def summary(self) -> dict[str, Any]:
        out = {k: v for k, v in self.st.items() if not k.startswith("_")}
        out["t"] = self.t
        out["makespan"] = self.t
        out["backlog"] = self.backlog()
        out["in_flight"] = self.in_flight()
        out["n_txn_target"] = self._n_txn_target
        out["completed"] = self.done()
        lat = sorted(self.resp_lat)
        if lat:
            out["lat_p50"] = lat[len(lat) // 2]
            out["lat_p99"] = lat[min(len(lat) - 1, int(0.99 * len(lat)))]
            out["lat_max"] = lat[-1]
            out["lat_mean"] = round(sum(lat) / len(lat), 1)
        net = sorted(self.net_lat)
        if net:
            out["net_p50"] = net[len(net) // 2]
            out["net_p99"] = net[min(len(net) - 1, int(0.99 * len(net)))]
            out["net_mean"] = round(sum(net) / len(net), 1)
        out["core_outstanding"] = self.p.core_outstanding
        out["ha_pos_depth"] = self.p.ha_pos_depth
        out["retry"] = self._retry_stats()
        out["wr_inject_by_core"] = {c: list(v) for c, v
                                    in sorted(self.wr_inject_times.items())}
        out["wr_recv_by_ha"] = {h: len(v) for h, v
                                in sorted(self.wr_recv_times.items())}
        # Retired transactions per core. Unlike injection counts this is
        # comparable between a run that drained and one that collapsed,
        # because both are divided by the same makespan.
        out["wr_done_by_core"] = {c: len(v) for c, v
                                  in sorted(self.compl_ranks.items())}
        out["fifo"] = self.fifo_report()
        out["fabric"] = self.fabric_util(max(1, self.t))
        out["board_fail_by_src"] = {
            f"{n}:{vc}": dict(row) for (n, vc), row
            in sorted(self.board_fail_cause.items())}
        out["board_by_core_dir"] = self.board_dir_report()
        return out

    def board_dir_report(self) -> dict[str, dict[str, int]]:
        """Per-core top-die CW/CCW board successes and failures."""
        out: dict[str, dict[str, int]] = {}
        for c in self.topo.cores:
            nd = self.topo.nodes[c]
            out[str(c)] = {
                "die": nd.die, "idx": nd.idx,
                "ok_cw": self.board_ok_dir.get((c, 1), 0),
                "ok_ccw": self.board_ok_dir.get((c, -1), 0),
                "fail_cw": self.board_fail_dir.get((c, 1), 0),
                "fail_ccw": self.board_fail_dir.get((c, -1), 0),
            }
        return out


def run_batch(topo: StackTopology, txns: Sequence[Txn], *,
              params: StackBaseParams | None = None,
              sim_cls: type[StackBaseSim] = StackBaseSim,
              t_max: int = 4_000_000, seed: int = 0,
              stall_after: int = 40_000) -> dict[str, Any]:
    sim = sim_cls(topo, params, seed=seed)
    sim.offer_batch(txns)
    last_progress, last_count = 0, 0
    while sim.t < t_max and not sim.done():
        sim.step()
        if sim.st["n_delivered_flits"] != last_count:
            last_count = sim.st["n_delivered_flits"]
            last_progress = sim.t
        elif sim.t - last_progress > stall_after:
            break
    out = sim.summary()
    out["stall_detected"] = not out["completed"]
    if hasattr(sim, "fc_summary"):
        out["fc"] = sim.fc_summary()          # type: ignore[attr-defined]
    return out


if __name__ == "__main__":
    import json
    from rg_stack_topo import build_uniform_write

    topo = StackTopology()
    tx = build_uniform_write(topo, k=20, seed=0)
    r = run_batch(topo, tx)
    keep = ("completed", "makespan", "n_txn_done", "n_txn_target",
            "n_delivered_flits", "n_deflections", "n_eject_full_deflect",
            "n_turn_full_deflect", "n_tap_deflect", "n_board_fail",
            "n_inring_blocked", "n_turns", "max_deflections",
            "max_ejectq", "max_srcq", "lat_p50", "lat_p99", "lat_max")
    print(json.dumps({k: r.get(k) for k in keep}, indent=2))
    print(json.dumps(r["fifo"], indent=2))
    print(json.dumps(r["fabric"], indent=2))
