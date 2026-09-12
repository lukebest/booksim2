#!/usr/bin/env python3
"""S0 baseline for the 3D-stacked fabric: route-following cycle simulator.

Why this is not `Ring2BaseSim`
-----------------------------
The ring simulator inlines its geometry into movement: a flit's entire route
is `(dir, hops_remaining)`, the next hop is `(idx + dir) % n`, and a failed
eject is expressed as `target = n`. None of that survives a fabric where one
transfer crosses a top-die ring, a die boundary, a horizontal full ring and a
vertical full ring. Here a flit carries an explicit list of directed edges and
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
R5  SWAP  -- HPCA'22 (Wang et al.): two flits at one bridge, each wanting
             the other's fabric, exchange through a bypass and take the slot
             the partner vacates. No FIFO, no turn_lat. Applied at H↔V
             attach points and at D2D↔H / D2D↔V bridges.
R6  D2D landing buffer -- a bounded queue per (attach, dest ring, VC). A
             D2D arrival that misses SWAP and the ring FIFO sits here
             instead of an unbounded landing hold, and is re-offered next
             cycle so it can SWAP with a ring flit going the other way.

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

from rg_stack_topo import TOP_PLANES, StackTopology, Txn, vc_of

CORE_OUTSTANDING_WR = 128


@dataclass
class StackBaseParams:
    sigma: int = 1
    inj_depth: int = 12             # shared boarding FIFO per (station, plane, vc)
    shared_inj: bool = True
    dir_inj_depth: int = 8          # per-direction inject Q after the shared FIFO
    two_write_leave: bool = True    # one write port per incoming dir into leave buf
    eject_depth: int = 12           # destination PE queue
    eject_bw: int = 1               # PE drain per station per cycle
    t_inj: int = 16                 # inject starve cycles -> I-tag
    t_xfer: int = 1                 # deflections -> E-tag (specified value)
    itag_mode: str = "reserve"      # "broadcast" | "reserve"
    itag_hold: int = 8              # cycles a raised I-tag may block; 0 = never
    inj_sel: str = "free_slot"      # "rr" | "free_slot"
    per_dir_ports: bool = True      # one board port per (node, plane, dir, vc)
    per_vc_ports: bool = True       # independent board / leave / eject per VC
    t_ha_service: int = 0
    per_vc_srcq: bool = True        # WriteNoSnp needs REQ not to head-block DAT
    core_outstanding: int = CORE_OUTSTANDING_WR
    # HA request-tracker entries. A completer that runs out of them cannot
    # queue the request: CHI makes it reject with RetryAck and hand out a
    # PCrdGrant later. 0 keeps the old unlimited-completer behaviour.
    ha_pos_depth: int = 512
    turn_depth: int = 4             # ring -> ring transfer FIFO
    d2d_depth: int = 8              # die-crossing FIFO
    # HPCA'22 SWAP: two flits at one bridge, each wanting the other's fabric,
    # exchange through a bypass and take the slot the other vacates. Breaks
    # the H↔V and D2D↔ring hold-and-wait deadlock without a VC.
    swap_rule: bool = True
    # Bounded landing buffer at the bottom D2D bridge, one queue per
    # (attach, dest ring). Fresh D2D arrivals that miss SWAP and the ring
    # FIFO sit here instead of piling onto an unbounded landing hold.
    d2d_land_depth: int = 16
    resv_ej: int = 1                # eject slots only an E-tagged flit may use
    # Transfer-FIFO entries only an E-tagged flit may occupy. Measured to be
    # counterproductive here and left off by default: it withholds scarce
    # turn capacity from the common case, and once the fabric is congested
    # every flit is E-tagged anyway, so it buys nothing and costs a lap.
    resv_turn: int = 0
    plane_sel: str = "least_occupied"
    # Per-fabric link width: flits per directed edge per VC per sigma window.
    # 1 is the original R1. Raising a fabric's width is a bandwidth change,
    # not a buffer change -- FIFO depths stay put.
    d2d_bw: int = 1
    h_bw: int = 1
    v_bw: int = 1
    top_bw: int = 1
    # How many flits a D2D landing / D2D transfer FIFO may board per cycle.
    bridge_bw: int = 1
    # How many H↔V turns one station may leave, and one turn FIFO may
    # drain, per cycle. 1 is the original R2 tap. Width is a bandwidth
    # change: FIFO depths stay put. Needed once a dest hop is wider than 1,
    # or the extra hop slots starve behind a one-flit tap.
    turn_bw: int = 1
    # How many PE injects one port-group may board per cycle.
    inject_bw: int = 1


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
        self.n_planes = TOP_PLANES

        self.seg_free: dict[Any, int] = defaultdict(int)     # (eid, vc) -> t
        self.seg_win: dict[Any, int] = {}                    # wide-link window
        self.seg_used: dict[Any, int] = defaultdict(int)     # slots in window
        self._fab_bw = {
            "top": max(1, int(self.p.top_bw)),
            "d2d": max(1, int(self.p.d2d_bw)),
            "h": max(1, int(self.p.h_bw)),
            "v": max(1, int(self.p.v_bw)),
        }
        self.arrivals: dict[int, list[Flit]] = defaultdict(list)

        self.srcq: dict[Any, deque[Flit]] = defaultdict(deque)
        self.pending: dict[Any, deque[Flit]] = defaultdict(deque)
        # Flits that may try to inject now (P-Credit re-sends, WriteData,
        # responses). Kept off `pending` so a full outstanding window does
        # not scan thousands of waiting new REQs every cycle.
        self.ready: dict[Any, deque[Flit]] = defaultdict(deque)
        self.inj_starve: dict[Any, int] = defaultdict(int)
        self.i_tag: dict[Any, set[int]] = defaultdict(set)   # (ring, dir, vc)
        self.itag_t: dict[Any, int] = {}
        self.itag_resv: dict[Any, tuple] = {}
        self.itag_donor: dict[Any, int] = {}
        self._itag_culprit: int | None = None
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

        # transfer FIFOs: (node, src_fabric, dest_ring) -> flits waiting to
        # board dest_ring. Splitting on the source fabric is what gives the
        # bottom D2D landing two interfaces: D2D→H and D2D→V do not share a
        # queue with each other or with an H↔V turn at the same station.
        self.xq: dict[Any, deque[Flit]] = defaultdict(deque)
        self.active_xq: dict[Any, None] = {}
        self.xq_peak: dict[Any, int] = defaultdict(int)
        self._land_now: dict[int, int] = defaultdict(int)
        self.d2d_buf: dict[Any, deque] = defaultdict(deque)
        self.active_d2d_buf: dict[Any, None] = {}
        self.d2d_buf_peak: dict[Any, int] = defaultdict(int)
        self._fab_cap = topo.capacity()
        self._fab_names = tuple(self._fab_cap)
        self.fab_win = 50
        self._cyc_hops: dict[str, int] = defaultdict(int)
        self._cyc_hops_vc: dict[tuple[str, str], int] = defaultdict(int)
        self.peak_hops: dict[str, int] = defaultdict(int)
        self.peak_hops_vc: dict[tuple[str, str], int] = defaultdict(int)
        self._win_hops: dict[str, int] = defaultdict(int)
        self.fab_series: dict[str, Any] = {
            "window": self.fab_win, "t": [],
            "bw": {k: [] for k in self._fab_names},
            "util": {k: [] for k in self._fab_names},
            "bw_vc": {f"{k}:{vc}": [] for k in self._fab_names
                      for vc in topo.vcs},
        }
        self._win_hops_vc: dict[tuple[str, str], int] = defaultdict(int)

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
            "n_turn_hold": 0,
            "n_tap_deflect": 0, "n_board_fail": 0, "n_turn_board_fail": 0,
            "max_inj_starve": 0, "max_deflections": 0, "max_ejectq": 0,
            "max_srcq": 0, "max_pending": 0, "n_admit_stall": 0,
            "n_outst_wait": 0, "max_core_outstanding": 0,
            "max_turn_q": 0, "max_d2d_q": 0, "n_d2d_stall": 0,
            "max_d2d_landing": 0, "n_turn_resv_used": 0,
            "max_inring_hold": 0, "n_turns": 0,
            "n_swaps": 0, "n_swaps_hv": 0, "n_swaps_d2d": 0,
            "n_swaps_d2d_h": 0, "n_swaps_d2d_v": 0,
            "n_itag_yield": 0,
            "n_d2d_buf_push": 0, "max_d2d_buf": 0,
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
        self.wr_done_times: dict[int, list[int]] = defaultdict(list)
        self.rd_inject_times: dict[int, list[int]] = defaultdict(list)
        self.rd_done_times: dict[int, list[int]] = defaultdict(list)
        self.resp_left: dict[int, int] = {}
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

    def _dk(self, node: int, plane: int, vc: str, direction: int) -> Any:
        return (node, self._pk(node, plane), vc, direction)

    def _shared_vcs(self) -> tuple[str, ...]:
        return self._vc_list if self.p.per_vc_srcq else (self._vc_list[0],)

    def _src_keys(self, node: int, plane: int) -> list[Any]:
        if self.p.shared_inj:
            return [k for g in self._port_groups(node, plane) for k in g]
        if not self.p.per_vc_srcq:
            return [(node, self._pk(node, plane))]
        vcs = self._vc_list
        p = self._pk(node, plane)
        off = self.vc_rr[(node, p)] % len(vcs)
        return [(node, p, vcs[(off + i) % len(vcs)]) for i in range(len(vcs))]

    def _port_groups(self, node: int, plane: int) -> list[list[Any]]:
        """Queue keys behind each board port; one inner list per port."""
        p = self._pk(node, plane)
        if self.p.shared_inj:
            dirs = (1, -1)
            groups = [[(node, p, v, d) for d in dirs] for v in self._shared_vcs()]
        else:
            groups = [[k] for k in (
                [(node, p, v) for v in self._vc_list] if self.p.per_vc_srcq
                else [(node, p)])]
        if self.p.per_dir_ports:
            return [[k] for g in groups for k in g]
        if self.p.per_vc_ports:
            return groups
        return [[k for g in groups for k in g]]

    def _q_depth(self, key: Any) -> int:
        if self.p.shared_inj and isinstance(key, tuple) and len(key) == 4:
            return self.p.dir_inj_depth
        return self.p.inj_depth

    def _ejk(self, node: int, plane: int, vc: str | None = None) -> Any:
        p = self._pk(node, plane)
        if self.p.per_vc_ports and vc is not None:
            return (node, p, vc)
        return (node, p)

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
        f.dir = self.topo.edge_dir[f.route[0]] if f.route else 0

    # -- workload -----------------------------------------------------------

    def offer_txn(self, txn: Txn) -> None:
        self.txn_by_id[txn.txn_id] = txn
        if getattr(txn, "op", "write") == "read":
            self.resp_left[txn.txn_id] = txn.m_resp or 4
        else:
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
        waiting = (f.kind == "req" and self._is_core[f.src]
                   and f.txn_id not in self._counted)
        if waiting:
            self.pending[key].append(f)
        else:
            self.ready[key].append(f)
        self.st["max_pending"] = max(self.st["max_pending"],
                                     len(self.pending[key]) + len(self.ready[key]))
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
        q, pend, ready = self.srcq[key], self.pending[key], self.ready[key]
        depth = self._q_depth(key)
        # Evict new REQs that filled the window after they were admitted.
        if q and self._outst_blocked(q[0]):
            stuck = deque()
            while q and self._outst_blocked(q[0]):
                stuck.append(q.popleft())
            stuck.extend(pend)
            self.pending[key] = pend = stuck
        while ready and len(q) < depth:
            q.append(ready.popleft())
        while pend and len(q) < depth:
            if self._outst_blocked(pend[0]):
                break
            q.append(pend.popleft())
        if q:
            self.st["max_srcq"] = max(self.st["max_srcq"], len(q))

    def _xfer_shared(self, node: int, plane: int) -> None:
        """Shared FIFO → per-dir inject Q."""
        if not self.p.shared_inj:
            return
        p = self._pk(node, plane)
        for v in self._shared_vcs():
            sk = (node, p, v) if self.p.per_vc_srcq else (node, p)
            q = self.srcq[sk]
            parked: deque = deque()
            while q:
                f = q[0]
                if self._outst_blocked(f):
                    parked.append(q.popleft())
                    continue
                d = f.dir if f.dir in (1, -1) else 1
                dk = (node, p, v, d) if self.p.per_vc_srcq else (node, p, d)
                if len(self.srcq[dk]) >= self.p.dir_inj_depth:
                    break
                self.srcq[dk].append(q.popleft())
                self.st["max_srcq"] = max(self.st["max_srcq"],
                                          len(self.srcq[dk]))
            while parked:
                self.pending[sk].appendleft(parked.pop())

    def _src_idle(self, key: Any) -> bool:
        return (not self.srcq[key] and not self.pending[key]
                and not self.ready[key])

    def _port_idle(self, node: int, plane: int) -> bool:
        p = self._pk(node, plane)
        if not self.p.shared_inj:
            return all(self._src_idle(k) for k in self._src_keys(node, plane))
        for v in self._shared_vcs():
            sk = (node, p, v) if self.p.per_vc_srcq else (node, p)
            if not self._src_idle(sk):
                return False
            for d in (1, -1):
                dk = (node, p, v, d) if self.p.per_vc_srcq else (node, p, d)
                if self.srcq[dk]:
                    return False
        return True

    def _clear_itag(self, node: int) -> None:
        """Drop leftover I-tags. A port with nothing injectable must not
        keep the ring reserved; that blocks everyone else on an empty hop."""
        for holders in self.i_tag.values():
            holders.discard(node)

    def _wake_core(self, core: int) -> None:
        """Re-admit a core after an outstanding slot is freed."""
        for plane in range(self.n_planes):
            self.active_src[(core, plane)] = None
            p = self._pk(core, plane)
            for v in self._shared_vcs():
                self._admit((core, p, v) if self.p.per_vc_srcq else (core, p))
            self._xfer_shared(core, plane)

    # -- movement -----------------------------------------------------------

    def _hop_cap(self, eid: int) -> int:
        """Flits one directed edge may carry on one VC in a sigma window."""
        return self._fab_bw.get(self.topo.fabric_of(eid), 1)

    def _hop_ready(self, eid: int, vc: str) -> bool:
        """True if `(eid, vc)` still has a slot this cycle.

        Width 1 is the original R1: `seg_free[seg] <= t`. Wider fabrics
        keep a per-window slot count so two flits can share one hop
        without changing FIFO depths.
        """
        seg = (eid, vc)
        if self._hop_cap(eid) <= 1:
            return self.seg_free[seg] <= self.t
        win = self.seg_win.get(seg)
        if win is None or self.t >= win + self.sigma:
            return True
        return self.seg_used.get(seg, 0) < self._hop_cap(eid)

    def _hop_take(self, eid: int, vc: str) -> None:
        seg = (eid, vc)
        if self._hop_cap(eid) <= 1:
            self.seg_free[seg] = self.t + self.sigma
            return
        win = self.seg_win.get(seg)
        if win is None or self.t >= win + self.sigma:
            self.seg_win[seg] = self.t
            self.seg_used[seg] = 1
        else:
            self.seg_used[seg] += 1
        if self.seg_used[seg] >= self._hop_cap(eid):
            self.seg_free[seg] = self.seg_win[seg] + self.sigma

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
        if not self._hop_ready(eid, f.vc):
            if inring:
                self.st["n_inring_blocked"] += 1
                self._hold(f, seg)
            return False
        if f.held:
            f.held = False
            self.inring_hold[seg] -= 1
        self._hop_take(eid, f.vc)
        rk = self.topo.edge_ring[eid]
        if inring and f.ring == rk:
            self.pass_through[(f.node, rk, f.vc)] += 1
        self.edge_load[eid] += 1
        fab = rk[0]
        self.fabric_hops[fab] += 1
        self._cyc_hops[fab] += 1
        self._cyc_hops_vc[(fab, f.vc)] += 1
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

    def _age_xfer_wait(self, f: Flit) -> None:
        """E-tag a flit that is sitting on a full transfer FIFO."""
        f.fail_eject += 1
        if f.fail_eject >= self.p.t_xfer and not f.e_tag:
            f.e_tag = True
            self.st["n_etag_raised"] += 1

    def _deflect(self, f: Flit) -> None:
        """No room to eject at the destination: ride one revolution."""
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
        key = self._ejk(f.dst, f.plane, f.vc)
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

    def _xfer_key(self, node: int, src_ring: Any, dst_ring: Any) -> tuple:
        """One FIFO per (station, incoming fabric, outgoing ring).

        Bottom D2D therefore has two landing queues -- onto H and onto V --
        that do not share occupancy with an H↔V turn at the same attach.
        """
        src = src_ring[0] if src_ring is not None else ""
        return (node, src, dst_ring)

    def _xfer_dest(self, key: Any) -> Any:
        return key[2] if len(key) >= 3 else key[1]

    def _xfer_is_d2d(self, key: Any) -> bool:
        if len(key) >= 3:
            dst = key[2]
            return key[1] == "d2d" or (dst is not None and dst[0] == "d2d")
        return key[1][0] == "d2d"

    def _xdepth(self, src_ring: Any, dst_ring: Any) -> int:
        src = src_ring[0] if src_ring is not None else ""
        dst = dst_ring[0] if dst_ring is not None else ""
        if src == "d2d" or dst == "d2d":
            return self.p.d2d_depth
        return self.p.turn_depth

    def _src_fab(self, f: Flit) -> str:
        return f.ring[0] if f.ring is not None else ""

    def _dst_fab(self, f: Flit) -> str:
        return self.topo.edge_ring[self._next_edge(f)][0]

    def _d2d_buf_key(self, f: Flit) -> tuple:
        nxt = self.topo.edge_ring[self._next_edge(f)]
        return (f.node, nxt, f.vc)

    def _offer_d2d_buf(self, leave: dict[Any, list]) -> set[int]:
        """Heads of the landing buffer act as D2D arrivals this cycle."""
        from_land: set[int] = set()
        for key, q in list(self.d2d_buf.items()):
            if not q:
                self.active_d2d_buf.pop(key, None)
                continue
            n = max(1, int(self.p.bridge_bw))
            for f in list(q)[:n]:
                leave[(f.node, f.ring)].append(f)
                from_land.add(id(f))
        return from_land

    def _pop_d2d_buf(self, ids: set[int]) -> None:
        if not ids:
            return
        for key, q in list(self.d2d_buf.items()):
            while q and id(q[0]) in ids:
                q.popleft()
            if not q:
                self.active_d2d_buf.pop(key, None)

    def _push_d2d_buf(self, f: Flit) -> bool:
        key = self._d2d_buf_key(f)
        q = self.d2d_buf[key]
        if len(q) >= self.p.d2d_land_depth:
            return False
        q.append(f)
        self.active_d2d_buf[key] = None
        self.st["n_d2d_buf_push"] += 1
        self.d2d_buf_peak[key] = max(self.d2d_buf_peak[key], len(q))
        self.st["max_d2d_buf"] = max(self.st["max_d2d_buf"], len(q))
        f.fail_eject += 1
        if f.fail_eject >= self.p.t_xfer and not f.e_tag:
            f.e_tag = True
            self.st["n_etag_raised"] += 1
        return True

    def _try_swap(self, a: Flit, b: Flit) -> bool:
        """HPCA'22 SWAP: each takes the hop the other vacates.

        Half-rings are unidirectional, so the vacated slot is the unique
        outgoing hop. Both hops are checked before either launch. SWAP
        bypasses the transfer FIFO and does not charge turn_lat.

        Same-VC pairs free the exact hop the partner needs. CHI writes also
        meet as DAT/REQ down vs RSP up; those still swap if both hops are
        free, so a full FIFO cannot hold-and-wait across the bridge.
        """
        if self._at_dest(a) or self._at_dest(b):
            return False
        if a.node != b.node:
            return False
        ea, eb = self._next_edge(a), self._next_edge(b)
        if not self._hop_ready(ea, a.vc):
            return False
        if not self._hop_ready(eb, b.vc):
            return False
        sa, sb = self._src_fab(a), self._src_fab(b)
        da, db = self._dst_fab(a), self._dst_fab(b)
        if sa == sb or da != sb or db != sa:
            return False
        self.st["n_swaps"] += 1
        pair = {sa, sb}
        if pair == {"h", "v"}:
            self.st["n_swaps_hv"] += 1
        else:
            self.st["n_swaps_d2d"] += 1
            for s, d in ((sa, da), (sb, db)):
                if s == "d2d" and d == "h":
                    self.st["n_swaps_d2d_h"] += 1
                elif s == "d2d" and d == "v":
                    self.st["n_swaps_d2d_v"] += 1
        self._launch(a, inring=False)
        self._launch(b, inring=False)
        return True

    def _pair_swaps(self, by_src: dict[str, list], swapped: set[int],
                    tapped: set[tuple]) -> None:
        for a_fab, b_fab in (("d2d", "v"), ("d2d", "h"), ("h", "v")):
            ia = [f for f in by_src[a_fab]
                  if id(f) not in swapped and self._dst_fab(f) == b_fab]
            ib = [f for f in by_src[b_fab]
                  if id(f) not in swapped and self._dst_fab(f) == a_fab]
            for fa, fb in zip(ia, ib):
                na, ra, nb, rb = fa.node, fa.ring, fb.node, fb.ring
                if self._try_swap(fa, fb):
                    swapped.add(id(fa))
                    swapped.add(id(fb))
                    for node, ring in ((na, ra), (nb, rb)):
                        if ring is not None and ring[0] != "d2d":
                            tapped.add((node, ring))
                else:
                    break

    def _do_swaps(self, leave: dict[Any, list]) -> tuple[set[int], set[tuple]]:
        """Pair complementary leaves at one station.

        Same VC first (the hop the partner vacates is the one we need).
        Then DAT/REQ vs RSP across VCs, which is how a write actually
        crosses a D2D bridge in both directions at once.
        D2D↔ring before H↔V: that is the hold-and-wait that parks
        Comp/PCrd under descending WriteData.
        """
        same: dict[Any, dict[str, list[Flit]]] = defaultdict(
            lambda: defaultdict(list))
        any_vc: dict[int, dict[str, list[Flit]]] = defaultdict(
            lambda: defaultdict(list))
        for (node, ring), reqs in leave.items():
            for f in reqs:
                if self._at_dest(f):
                    continue
                src = ring[0] if ring is not None else self._src_fab(f)
                try:
                    dst = self._dst_fab(f)
                except (IndexError, KeyError):
                    continue
                if src == dst:
                    continue
                same[(node, f.vc)][src].append(f)
                any_vc[node][src].append(f)
        swapped: set[int] = set()
        tapped: set[tuple] = set()
        for by_src in same.values():
            self._pair_swaps(by_src, swapped, tapped)
        for by_src in any_vc.values():
            self._pair_swaps(by_src, swapped, tapped)
        return swapped, tapped

    def _try_turn(self, f: Flit) -> bool:
        """Hand a flit to the transfer FIFO of the ring it wants next.

        The last `resv_turn` entries are reserved for E-tagged flits, so a
        flit that has already paid for a revolution is not made to pay again.
        """
        nxt = self.topo.edge_ring[self._next_edge(f)]
        key = self._xfer_key(f.node, f.ring, nxt)
        q = self.xq[key]
        cap = self._xdepth(f.ring, nxt)
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
        slot = "max_d2d_q" if self._xfer_is_d2d(key) else "max_turn_q"
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
        if f.kind == "resp":
            self.rd_inject_times[f.dst].append(self.t)
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

    def _itag_rk(self, f: Flit) -> tuple:
        eid = self._next_edge(f)
        return (self.topo.edge_ring[eid], f.dir, f.vc)

    def _step_node(self, ring: Any, node: int, direction: int) -> int | None:
        eid = self.topo._succ.get((ring, node, direction))
        if eid is None:
            return None
        return self.topo.edges[eid][1]

    def _ring_path_lat(self, ring: Any, src: int, dst: int,
                       direction: int) -> int:
        lat, cur = 0, src
        for _ in range(64):
            if cur == dst:
                return lat
            eid = self.topo._succ.get((ring, cur, direction))
            if eid is None:
                return lat
            lat += self.topo.edge_lat[eid]
            cur = self.topo.edges[eid][1]
        return lat

    def _crosses_hop(self, f: Flit, starved: int) -> bool:
        """Would this flit's first hops ride over `starved`'s outgoing hop?"""
        if f.dir not in (1, -1) or not f.route:
            return False
        ring = self.topo.edge_ring[self._next_edge(f)]
        if ring not in self.topo.ring_of:
            return False
        cur = f.src if f.ring is None else f.node
        n = len(self.topo.ring_of[ring])
        for _ in range(n):
            if cur == starved:
                return True
            nxt = self._step_node(ring, cur, f.dir)
            if nxt is None:
                return False
            cur = nxt
        return False

    def _itag_head(self, u: int, plane: int, vc: str, d: int) -> Flit | None:
        p = self._pk(u, plane)
        if self.p.shared_inj:
            q = self.srcq.get((u, p, vc, d) if self.p.per_vc_srcq
                              else (u, p, d))
            return q[0] if q else None
        q = self.srcq.get(self._sk(u, plane, vc))
        for fl in (q or ()):
            if fl.dir == d:
                return fl
        return None

    def _itag_donor(self, rk: Any, requester: int) -> int | None:
        ring, d, vc = rk
        members = self.topo.ring_of.get(ring)
        if not members or d not in (1, -1):
            return None
        plane = ring[2] if ring[0] == "top" else 0
        cur = requester
        for _ in range(len(members) - 1):
            nxt = self._step_node(ring, cur, -d)
            if nxt is None:
                return None
            cur = nxt
            fl = self._itag_head(cur, plane, vc, d)
            if fl is not None and self._crosses_hop(fl, requester):
                return cur
        return None

    def _itag_expire(self, rk: Any) -> set[int]:
        holders = self.i_tag[rk]
        if holders and self.p.itag_hold:
            for h in [h for h in holders
                      if self.t - self.itag_t.get(rk + (h,), self.t)
                      >= self.p.itag_hold]:
                holders.discard(h)
                self.itag_t.pop(rk + (h,), None)
                self.itag_resv.pop(rk + (h,), None)
        return holders

    def _itag_pre(self) -> None:
        if self.p.itag_mode != "reserve":
            return
        self.itag_donor = {}
        for rk in [k for k, v in self.i_tag.items() if v]:
            for r in self._itag_expire(rk):
                key = rk + (r,)
                st = self.itag_resv.get(key)
                if st is not None and self.t < st[1]:
                    continue
                self.itag_resv.pop(key, None)
                u = self._itag_donor(rk, r)
                if u is not None:
                    self.itag_donor[key] = u

    def _itag_blocks(self, f: Flit, node: int) -> bool:
        rk = self._itag_rk(f)
        holders = self._itag_expire(rk)
        if not holders or node in holders:
            return False
        if self.p.itag_mode != "reserve":
            return True
        ring, d, _vc = rk
        for r in holders:
            if r == node or not self._crosses_hop(f, r):
                continue
            key = rk + (r,)
            st = self.itag_resv.get(key)
            if st is None:
                if self.itag_donor.get(key) == node:
                    self._itag_culprit = r
                    return True
                continue
            donor, eta = st
            if self.t < eta:
                # Hold only nodes the reserved bubble still has to pass.
                cur = donor
                between = False
                for _ in range(64):
                    nxt = self._step_node(ring, cur, d)
                    if nxt is None or cur == r:
                        break
                    if nxt == node:
                        between = True
                        break
                    cur = nxt
                if between:
                    self._itag_culprit = r
                    return True
        return False

    def _itag_yielded(self, node: int, f: Flit) -> None:
        r = self._itag_culprit
        if self.p.itag_mode != "reserve" or r is None:
            return
        rk = self._itag_rk(f)
        key = rk + (r,)
        if key not in self.itag_resv:
            self.itag_resv[key] = (
                node, self.t + self._ring_path_lat(rk[0], node, r, f.dir))
            self.st["n_itag_yield"] += 1
        self._itag_culprit = None

    def _itag_clear(self, node: int, f: Flit) -> None:
        rk = self._itag_rk(f)
        self.i_tag[rk].discard(node)
        self.itag_t.pop(rk + (node,), None)
        self.itag_resv.pop(rk + (node,), None)

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

        # Phase 2a -- HPCA'22 SWAP at H/V attach points and D2D bridges.
        # A flit sitting in the D2D landing buffer is a first-class D2D
        # arrival this cycle, so it can swap with a ring flit going the
        # other way.
        from_land = self._offer_d2d_buf(leave)
        if self.p.swap_rule:
            swapped, swap_tapped = self._do_swaps(leave)
        else:
            swapped, swap_tapped = set(), set()
        self._pop_d2d_buf(from_land & swapped)

        # Phase 2b -- remaining leaves: PE eject or transfer FIFO.
        # `two_write_leave` lets both incoming dirs write the dest buffer
        # in one cycle (top-die cores). Turns take `turn_bw` taps (default 1).
        for key, reqs in leave.items():
            node, ring = key
            on_ring = ring is not None and ring[0] != "d2d"
            leftover = [f for f in reqs if id(f) not in swapped]
            dests = [f for f in leftover if self._at_dest(f)]
            turns = [f for f in leftover if not self._at_dest(f)]
            swap_hit = key in swap_tapped
            taken_dir: set[int] = set()
            n_dest = 0

            def bounce(fl: Flit, *, dest: bool) -> None:
                if dest:
                    self.st["n_eject_full_deflect"] += 1
                    if on_ring:
                        self._deflect(fl)
                    elif id(fl) in from_land:
                        return
                    elif self._push_d2d_buf(fl):
                        return
                    else:
                        self.st["n_d2d_stall"] += 1
                        self._age_xfer_wait(fl)
                        self._land_now[node] = self._land_now[node] + 1
                        self.st["max_d2d_landing"] = max(
                            self.st["max_d2d_landing"], self._land_now[node])
                        self.arrivals[t + 1].append(fl)
                    return
                # Turn FIFO full (or the tap was already taken). Circling
                # occupies every hop of the ring and starves FIFO drain.
                # Mixed RW puts WriteData down and CompData up on the same
                # DAT VC; both directions fill, deflectors livelock, and
                # outstanding never retires. Sit at the station and retry.
                self.st["n_turn_full_deflect"] += 1
                if on_ring:
                    self._age_xfer_wait(fl)
                    self.st["n_turn_hold"] += 1
                    self.arrivals[t + 1].append(fl)
                elif id(fl) in from_land:
                    return
                elif self._push_d2d_buf(fl):
                    return
                else:
                    self.st["n_d2d_stall"] += 1
                    self._age_xfer_wait(fl)
                    self._land_now[node] = self._land_now[node] + 1
                    self.st["max_d2d_landing"] = max(
                        self.st["max_d2d_landing"], self._land_now[node])
                    self.arrivals[t + 1].append(fl)

            ordered = self._tap_order(node, ring, dests) if on_ring else dests
            for f in ordered:
                blocked = swap_hit
                if self.p.two_write_leave:
                    blocked = blocked or f.dir in taken_dir
                else:
                    blocked = blocked or n_dest > 0
                if blocked:
                    if swap_hit:
                        self.st["n_tap_deflect"] += 1
                    bounce(f, dest=True)
                    continue
                if self._try_eject(f):
                    taken_dir.add(f.dir)
                    n_dest += 1
                    if id(f) in from_land:
                        self._pop_d2d_buf({id(f)})
                else:
                    bounce(f, dest=True)

            tapped = swap_hit or (n_dest > 0 and not self.p.two_write_leave)
            n_turn = 0
            turn_cap = max(1, int(self.p.turn_bw))
            ordered_t = self._tap_order(node, ring, turns) if on_ring else turns
            for f in ordered_t:
                if tapped and on_ring:
                    self.st["n_tap_deflect"] += 1
                    bounce(f, dest=False)
                    continue
                if self._try_turn(f):
                    n_turn += 1
                    if on_ring and n_turn >= turn_cap:
                        tapped = True
                    if id(f) in from_land:
                        self._pop_d2d_buf({id(f)})
                else:
                    bounce(f, dest=False)

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
        self._sample_fabric()
        self.t += 1

    def _flush_fab_window(self, t_start: int | None = None,
                          width: int | None = None) -> None:
        w = width or self.fab_win
        cap, nvc = self._fab_cap, self.topo.n_vc
        self.fab_series["t"].append(
            self.t + 1 - w if t_start is None else t_start)
        for fab in self._fab_names:
            hops = self._win_hops.pop(fab, 0)
            links = max(1, cap.get(fab, 1))
            self.fab_series["bw"][fab].append(round(hops / w, 4))
            self.fab_series["util"][fab].append(
                round(hops / (w * links * nvc), 4))
            for vc in self.topo.vcs:
                vh = self._win_hops_vc.pop((fab, vc), 0)
                self.fab_series["bw_vc"][f"{fab}:{vc}"].append(
                    round(vh / w, 4))
        self._win_hops.clear()
        self._win_hops_vc.clear()

    def _sample_fabric(self) -> None:
        """Instantaneous occupancy this cycle, plus a windowed series."""
        for fab, n in self._cyc_hops.items():
            if n > self.peak_hops[fab]:
                self.peak_hops[fab] = n
            self._win_hops[fab] += n
        for k, n in self._cyc_hops_vc.items():
            if n > self.peak_hops_vc[k]:
                self.peak_hops_vc[k] = n
            self._win_hops_vc[k] += n
        self._cyc_hops.clear()
        self._cyc_hops_vc.clear()
        if (self.t + 1) % self.fab_win == 0:
            self._flush_fab_window()

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
        """Board up to `bridge_bw` / `turn_bw` flits from one transfer FIFO.

        Width 1 keeps the original head-of-line rule: one miss ends the
        drain. Wider taps already paid for extra slots; a hop miss on the
        head must not waste them -- later flits may want a free hop.
        FIFO depths are unchanged; only which ready bodies may leave.
        """
        for key in list(self.active_xq):
            q = self.xq[key]
            if not q:
                self.active_xq.pop(key, None)
                continue
            n = (max(1, int(self.p.bridge_bw)) if self._xfer_is_d2d(key)
                 else max(1, int(self.p.turn_bw)))
            items = list(q)
            q.clear()
            launched = 0
            held: list[Flit] = []
            for idx, f in enumerate(items):
                if launched >= n:
                    held.extend(items[idx:])
                    break
                if f.turn_ready > self.t:
                    held.append(f)
                    if n <= 1:
                        held.extend(items[idx + 1:])
                        break
                    continue
                if self._launch(f, inring=False):
                    launched += 1
                    continue
                self.st["n_turn_board_fail"] += 1
                held.append(f)
                if n <= 1:
                    held.extend(items[idx + 1:])
                    break
            q.extend(held)
            if not q:
                self.active_xq.pop(key, None)

    def _select_inject_flit(self, node: int, plane: int, q) -> Flit | None:
        """Which boarding-queue flit tries the inject port. Default: FIFO head."""
        return q[0] if q else None

    def _free_slot_order(self, node: int, group: list[Any]) -> list[Any]:
        ready, blocked = [], []
        for cand in group:
            q = self.srcq[cand]
            f = self._select_inject_flit(node, cand[1], q) if q else None
            ok = (f is not None
                  and not self._itag_blocks(f, node)
                  and self._hop_ready(self._next_edge(f), f.vc))
            (ready if ok else blocked).append(cand)
        return ready + blocked

    def _board_one(self, node: int, plane: int, key: Any,
                   group: list[Any]) -> None:
        """Board at most one flit on one port from `group`."""
        if self.p.inj_sel == "free_slot" and len(group) > 1:
            group = self._free_slot_order(node, group)
        qk, f, denied = None, None, None
        for cand in group:
            q = self.srcq[cand]
            if not q:
                continue
            cf = self._select_inject_flit(node, plane, q)
            if cf is None:
                continue
            if self._may_inject(node, plane, cf):
                qk, f = cand, cf
                break
            if denied is None:
                denied = cf
        if f is None:
            if denied is not None:
                self._note_deny(node, denied)
                self._itag_clear(node, denied)
                self.inj_starve[key] = 0
            return
        starve_key = (node, plane, f.vc, f.dir) if self.p.per_dir_ports \
            else ((node, plane, f.vc) if self.p.per_vc_ports else key)
        if self._itag_blocks(f, node):
            self._fail_cause = "itag"
        elif not self._hop_ready(self._next_edge(f), f.vc):
            self._fail_cause = "hop_busy"
        else:
            self._fail_cause = ""
        if self._fail_cause:
            if self._fail_cause == "itag":
                self._itag_yielded(node, f)
            self._on_board_fail(node, f)
            self.inj_starve[starve_key] += 1
            self.st["max_inj_starve"] = max(self.st["max_inj_starve"],
                                            self.inj_starve[starve_key])
            if self.inj_starve[starve_key] >= self.p.t_inj:
                rk = self._itag_rk(f)
                if node not in self.i_tag[rk]:
                    self.i_tag[rk].add(node)
                    self.itag_t[rk + (node,)] = self.t
                    self.st["n_itag_raised"] += 1
            return
        q = self.srcq[qk]
        if f is q[0]:
            q.popleft()
        else:
            q.remove(f)
        self.vc_rr[key] += 1
        self._itag_clear(node, f)
        self.inj_starve[starve_key] = 0
        f.t_inject = self.t
        self.st["n_injected"] += 1
        self._on_inject(f)
        self._launch(f, inring=False)

    def _inject(self) -> None:
        self._itag_pre()
        for key in list(self.active_src):
            node, plane = key
            p = self._pk(node, plane)
            stalled = False
            for v in self._shared_vcs():
                sk = (node, p, v) if self.p.per_vc_srcq else (node, p)
                self._admit(sk)
                stalled = stalled or bool(self.pending[sk] or self.ready[sk])
            self._xfer_shared(node, plane)
            if stalled:
                self.st["n_admit_stall"] += 1
            groups = self._port_groups(node, plane)
            if not any(self.srcq[k] for g in groups for k in g):
                self._clear_itag(node)
                if self._port_idle(node, plane):
                    self.active_src.pop(key, None)
                continue
            n_inj = max(1, int(self.p.inject_bw))
            for _ in range(n_inj):
                for group in groups:
                    self._board_one(node, plane, key, group)
            if self.p.shared_inj:
                self._xfer_shared(node, plane)
                for v in self._shared_vcs():
                    self._admit((node, p, v) if self.p.per_vc_srcq
                                else (node, p))
                self._xfer_shared(node, plane)
            if self._port_idle(node, plane):
                self.active_src.pop(key, None)

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
        if getattr(txn, "op", "write") == "read":
            self._emit(txn, "resp", txn.ha, txn.core, txn.m_resp or 4,
                       self.t + self.p.t_ha_service)
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
        elif f.kind == "resp":
            left = self.resp_left.get(f.txn_id, 1) - 1
            self.resp_left[f.txn_id] = left
            if left == 0:
                self.st["n_txn_done"] += 1
                self.rd_done_times[txn.core].append(self.t)
                self.txn_done.append((f.txn_id, self.t))
                self._ha_free_credit(txn)
                if self.p.core_outstanding > 0:
                    self.core_outst[txn.core] = max(
                        0, self.core_outst[txn.core] - 1)
                    self._wake_core(txn.core)
                self._on_txn_done(txn, f)
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
            self.wr_done_times[txn.core].append(self.t)
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
                + sum(len(q) for q in self.d2d_buf.values())
                + len(self._stash))

    def backlog(self) -> int:
        return (sum(len(q) for q in self.srcq.values())
                + sum(len(q) for q in self.pending.values())
                + sum(len(q) for q in self.ready.values()))

    def done(self) -> bool:
        return (self._n_txn_target > 0
                and self.st["n_txn_done"] >= self._n_txn_target)

    def fifo_report(self) -> dict[str, Any]:
        turn = {k: v for k, v in self.xq_peak.items()
                if not self._xfer_is_d2d(k)}
        d2d = {k: v for k, v in self.xq_peak.items() if self._xfer_is_d2d(k)}
        land_h = sum(1 for k in d2d if len(k) >= 3 and k[1] == "d2d"
                     and k[2][0] == "h")
        land_v = sum(1 for k in d2d if len(k) >= 3 and k[1] == "d2d"
                     and k[2][0] == "v")
        return {
            "n_turn_fifo": len(turn), "n_d2d_fifo": len(d2d),
            "n_d2d_land_h": land_h, "n_d2d_land_v": land_v,
            "turn_peak": max(turn.values()) if turn else 0,
            "d2d_peak": max(d2d.values()) if d2d else 0,
            "turn_depth": self.p.turn_depth, "d2d_depth": self.p.d2d_depth,
            "d2d_land_depth": self.p.d2d_land_depth,
            "swap_rule": self.p.swap_rule,
            "turn_flits": sum(turn.values()), "d2d_flits": sum(d2d.values()),
            "d2d_landing_peak": self.st["max_d2d_landing"],
            "n_d2d_stall": self.st["n_d2d_stall"],
            "n_turn_hold": self.st["n_turn_hold"],
            "n_swaps": self.st["n_swaps"],
            "n_swaps_hv": self.st["n_swaps_hv"],
            "n_swaps_d2d": self.st["n_swaps_d2d"],
            "n_swaps_d2d_h": self.st["n_swaps_d2d_h"],
            "n_swaps_d2d_v": self.st["n_swaps_d2d_v"],
            "d2d_buf_peak": self.st["max_d2d_buf"],
            "n_d2d_buf_push": self.st["n_d2d_buf_push"],
            "residual_xq": sum(len(q) for q in self.xq.values()),
            "residual_d2d_buf": sum(len(q) for q in self.d2d_buf.values()),
            "resv_turn": self.p.resv_turn,
            "n_turn_resv_used": self.st["n_turn_resv_used"],
        }

    def fabric_util(self, makespan: int) -> dict[str, Any]:
        cap = self._fab_cap
        nvc = self.topo.n_vc
        out: dict[str, Any] = {}
        for k in self._fab_names:
            hops = self.fabric_hops.get(k, 0)
            links = cap.get(k, 1)
            slots = max(1, links * nvc)
            peak = self.peak_hops.get(k, 0)
            by_vc = {vc: self.peak_hops_vc.get((k, vc), 0)
                     for vc in self.topo.vcs}
            out[k] = {
                "flit_hops": hops, "links": links,
                "util": round(hops / max(1, slots * makespan), 4),
                "avg_util": round(hops / max(1, slots * makespan), 4),
                "peak_inst_bw": peak,
                "peak_inst_util": round(peak / slots, 4),
                "peak_inst_util_link": round(peak / max(1, links), 4),
                "peak_inst_by_vc": by_vc,
                "peak_inst_util_by_vc": {
                    vc: round(n / max(1, links), 4) for vc, n in by_vc.items()
                },
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
        out["fab_bw"] = dict(self._fab_bw)
        out["bridge_bw"] = self.p.bridge_bw
        out["turn_bw"] = self.p.turn_bw
        out["inject_bw"] = self.p.inject_bw
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
        out["wr_done_times_by_core"] = {c: list(v) for c, v
                                        in sorted(self.wr_done_times.items())}
        out["rd_inject_by_core"] = {c: list(v) for c, v
                                    in sorted(self.rd_inject_times.items())}
        out["rd_done_by_core"] = {c: len(v) for c, v
                                  in sorted(self.rd_done_times.items())}
        out["rd_done_times_by_core"] = {c: list(v) for c, v
                                        in sorted(self.rd_done_times.items())}
        leftover = self.t % self.fab_win
        if leftover and (self._win_hops or self._win_hops_vc):
            self._flush_fab_window(t_start=self.t - leftover, width=leftover)
        out["fifo"] = self.fifo_report()
        out["fabric"] = self.fabric_util(max(1, self.t))
        out["fabric_series"] = self.fab_series
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
