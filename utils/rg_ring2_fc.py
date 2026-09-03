#!/usr/bin/env python3
"""Congestion control for the bufferless dual-plane ring (S1 and S15).

Both schemes sit on top of `Ring2BaseSim` and share the same four-part
structure — detect, propagate, feed back, control — and the same dedicated
broadcast bus. They differ only in *what* is aggregated and in whether the
ring arbitration itself is touched.

S1  (`mode="s1"`) is the specified scheme, implemented literally:

    detect      per node per window, up-ring board failures and down-ring
                eject deflections, split into `total` (any cause) and `net`
                (only failures caused by in-ring occupancy). Level is
                `min(7, count // 8)`, i.e. 0-7 -> 0, 8-15 -> 1, ... >=56 -> 7.
    propagate   a dedicated 3-bit-per-direction broadcast bus, never the NoC.
    feed back   each node keeps a table of its 受控节点 — the path nodes its
                own flits ride through — and takes the *max* level over them.
    control     final level = level_of(own total fail - 8 * max received net
                level); AIMD on an integer per-window injection budget.

S15 (`mode="s15"`) keeps the bus and the window but replaces max-of-levels
with a max-min fair share computed from the broadcast achieved counts, and
adds bounded slot reservation, which is the part that actually creates a slot
for a starved node instead of merely asking others to slow down.

Neither scheme changes the datapath: in-ring flits are still never stalled by
a local injector (`n_inring_blocked == 0`), except for the explicitly bounded
S15 reservation yield.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from rg_ring2_base import Flit, Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import Dir, PlaneId, Ring2Topology, is_core, is_ha

# level -> multiplicative shrink; keyed by the *final* congestion level.
ALPHA_BANDS: dict[str, dict[str, float]] = {
    "spec":   {"lo": 0.75, "mid": 0.5,   "hi": 0.25},
    "harsh":  {"lo": 0.5,  "mid": 0.25,  "hi": 0.125},
    "gentle": {"lo": 0.875, "mid": 0.75, "hi": 0.5},
}
# max received level -> additive grow.
BETA_BANDS: dict[str, dict[str, int]] = {
    "spec":   {"clear": 16, "lo": 8, "hi": 2},
    "harsh":  {"clear": 8,  "lo": 4, "hi": 1},
    "gentle": {"clear": 32, "lo": 16, "hi": 8},
}
LEVEL_STEP = 8          # failures per congestion level
LEVEL_MAX = 7           # 3 bits


def level_of(count: int) -> int:
    """0-7 -> 0, 8-15 -> 1, ..., >=56 -> 7."""
    return min(LEVEL_MAX, max(0, int(count)) // LEVEL_STEP)


def _node_of(key: Any) -> int:
    """S1 keys per-window counters by node, S15 by (node, VC)."""
    return key[0] if isinstance(key, tuple) else key


@dataclass
class Ring2FcParams(Ring2BaseParams):
    mode: str = "s1"              # "s1" | "s15"
    window: int = 64              # control window, cycles
    bus_lat: int = 1              # broadcast bus delivery delay
    budget_min: int = 1           # never throttle a node to silence
    band: str = "spec"            # alpha / beta band mapping
    scope: str = "core_only"      # "core_only" | "ha_only" | "both"
    # Which failures feed S1's congestion level. "both" is the specified
    # scheme: own level = max(board failures, eject deflections), and the bus
    # carries both 3-bit fields. "up" counts only failed boards (上环失败),
    # "down" only failed ejects (下环失败); the unused bus field reads 0.
    signal: str = "both"          # "both" | "up" | "down"
    trace: bool = True            # keep per-window traces for the report
    # -- S15 only -------------------------------------------------------
    reserve_gap: int = 16         # how far below the ring-wide mean a node
                                  # must fall before it may post a hole
    reserve_max: int = 32         # most holes one node may hold in a window
    fair_headroom: float = 1.25   # let the shared hop try to grow a little
    busy_frac: float = 0.4        # hop occupancy before it advertises a share
    eject_share: bool = True      # also treat the leave port as a bottleneck
    fair_tol: float = 0.20        # spread in cumulative progress that has to
                                  # show up before the controller engages
    budget_init: int = 8          # slow start: no land grab before the first
                                  # feedback arrives
    credit: bool = True           # carry each node's cumulative shortfall
    pace_burst: int = 1           # leaky-bucket depth; 0 spends the budget
                                  # as fast as slots allow
    hold_depth: int = 0           # flits a segment may latch for a hole;
                                  # 0 keeps the ring strictly bufferless
    # Multiply S1's per-node budget ceiling. 1 = window (× VC count when
    # ports are split). <1 is the only existing-style knob that actually
    # lowers the 3 flit/cycle cap after per_vc_ports.
    cap_scale: float = 1.0
    # Independent AIMD budget per outgoing direction. The node-level
    # budget cannot equalise CW/CCW board-fail counts: shrinking it
    # scales both sides together and the ratio stays or grows.
    dir_split: bool = False


@dataclass
class BusMsg:
    """One node's broadcast: 3 bits up, 3 bits down, plus S15 side-band."""
    up: int = 0                   # net congestion level, injection side
    down: int = 0                 # net congestion level, ejection side
    ok: int = 0                   # flits successfully boarded this window
    demand: int = 0               # flits it wanted to board this window
    active: int = 0               # still has work
    cum: int = 0                  # S15: flits boarded since t=0, the only
                                  # quantity that says who is behind overall
    # S15: max-min fair share this node advertises for each outgoing
    # (direction, VC) it is a bottleneck on, in flits per window.
    fair: dict[tuple[int, str], int] = field(default_factory=dict)
    # S15: max-min share of this node's *leave* port, per VC. A hop is not
    # the only shared resource on the path — the destination's single eject
    # port per plane is one too, and overloading it is what turns arrivals
    # into full-revolution deflections.
    fair_ej: dict[str, int] = field(default_factory=dict)


def maxmin_share(counts: list[int], cap: int) -> int:
    """Single-bottleneck max-min share of `cap` among demands `counts`.

    Sources asking for less than an equal split keep what they ask for and
    their leftover is redistributed; the share returned is what every
    remaining (unsatisfied) source may have.
    """
    if not counts:
        return cap
    rest, n = cap, len(counts)
    for i, c in enumerate(sorted(counts)):
        share = rest / (n - i)
        if c > share:
            return max(0, int(share))
        rest -= c
    return cap


class CongestionBus:
    """A dedicated broadcast wire, priced separately from the NoC.

    Every node drives its window summary onto the bus at each window
    boundary and every node sees it `lat` cycles later. Nothing here consumes
    a ring hop, an inject port or an eject port, which is the whole point of
    having a side channel: congestion feedback must not queue behind the
    congestion it is reporting.
    """

    def __init__(self, n: int, lat: int = 1):
        self.n = n
        self.lat = max(0, lat)
        self.view: list[BusMsg] = [BusMsg() for _ in range(n)]
        self._pipe: dict[int, list[tuple[int, BusMsg]]] = defaultdict(list)
        self.n_posts = 0

    def post(self, t: int, node: int, msg: BusMsg) -> None:
        self._pipe[t + self.lat].append((node, msg))
        self.n_posts += 1

    def deliver(self, t: int) -> None:
        for node, msg in self._pipe.pop(t, []):
            self.view[node] = msg

    def bits_per_post(self, mode: str) -> int:
        """Wire width the area model has to pay for.

        S1 needs two 3-bit levels. S15 adds an 8-bit achieved count, a
        16-bit cumulative count, an active bit, and one 8-bit advertised
        fair share per outgoing (direction, VC) — six of them on a 3-VC
        bidirectional ring.
        """
        return 6 if mode == "s1" else 6 + 8 + 16 + 1 + 6 * 8


class Ring2FcSim(Ring2BaseSim):
    """S1 / S15 congestion control over the bufferless ring."""

    def __init__(self, topo: Ring2Topology,
                 params: Ring2FcParams | None = None, seed: int = 0):
        self.p: Ring2FcParams
        super().__init__(topo, params or Ring2FcParams(), seed=seed)
        p = self.p
        self.bus = CongestionBus(self.n, p.bus_lat)
        self.budget: dict[Any, int] = {}
        self.spent: dict[Any, int] = defaultdict(int)
        self.demand_win: dict[Any, int] = defaultdict(int)
        self.want_dir: dict[Any, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        self.fail_tot: dict[int, int] = defaultdict(int)
        self.fail_dir: dict[Any, int] = defaultdict(int)
        self.fail_net: dict[int, int] = defaultdict(int)
        self.defl_win: dict[int, int] = defaultdict(int)
        self.ok_win: dict[int, int] = defaultdict(int)
        self.cum: dict[Any, int] = defaultdict(int)
        # 受控节点: the (node, direction) hops each source's flits ride
        # through. Direction matters — a node is only a bottleneck for the
        # sources that cross it the same way round.
        self.path_nodes: dict[int, set[tuple[int, int]]] = defaultdict(set)
        self._path_seen: set[tuple[int, int, int]] = set()
        self.trace: dict[str, list] = {"t": [], "budget": [], "level": [],
                                       "recv": [], "ok": [], "target": []}
        # Run totals of the two raw S1 signals per controlled node, and how
        # many windows each one (and the bus feedback) was non-zero in.
        self.sig_sum: dict[str, dict[int, int]] = {
            k: defaultdict(int) for k in
            ("up", "down", "up_lv", "down_lv", "recv_lv", "windows")}
        # -- S15 state ---------------------------------------------------
        # Census of who crossed each outgoing hop this window:
        # (node, dir, vc) -> src -> flits. This is what lets a node advertise
        # a real max-min share instead of a bare congestion level.
        self.cross: dict[Any, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        # Leave port: what it served, and what was offered to it (a deflected
        # arrival is demand the port could not take).
        self.ej_served: dict[Any, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        self.ej_offered: dict[Any, dict[int, int]] = defaultdict(
            lambda: defaultdict(int))
        self.dst_nodes: dict[tuple[int, Dir], set[int]] = defaultdict(set)
        self.target: dict[Any, int] = {}
        # Best throughput each shared resource has been seen to sustain.
        # Anchoring the advertised capacity here stops the loop from
        # ratcheting itself down: throttling lowers this window's measured
        # load, which would otherwise lower the next window's share.
        self.cap_peak: dict[Any, float] = defaultdict(float)
        # Cumulative shortfall against the fair share. Equal *rates* are not
        # enough on a finite batch: a core that got a head start before the
        # loop converged keeps it forever. Carrying the deficit forward is
        # what equalises delivered bandwidth rather than instantaneous rate.
        self.credit: dict[Any, int] = defaultdict(int)
        # (plane, dir, node, vc) -> cycles held open for that node
        self.hole: dict[Any, set[int]] = {}
        self.hop_cap = p.window * topo.n_planes
        self.st["n_fc_deny"] = 0
        self.st["n_reserved"] = 0
        self.st["n_reserve_yield"] = 0
        self.st["n_reserve_used"] = 0

    # -- who is rate-controlled ---------------------------------------------

    def _controlled(self, node: int) -> bool:
        if self.p.scope == "both":
            return True
        if self.p.scope == "ha_only":
            return is_ha(node)
        return is_core(node)

    def _bkey(self, node: int, vc: str, d: Dir | None = None) -> Any:
        """S1 budgets a node; S15 a VC; dir_split a (node, direction)."""
        if self.p.mode == "s15":
            return (node, vc)
        if self.p.dir_split and d is not None:
            return (node, d)
        return node

    # -- detection ----------------------------------------------------------

    def _on_board_fail(self, node: int, f: Flit) -> None:
        super()._on_board_fail(node, f)
        self.fail_tot[node] += 1
        self.fail_dir[(node, f.dir)] += 1
        if self._fail_cause == "hop_busy":
            # Only in-ring occupancy is a congestion signal. An I-tag block or
            # a self-imposed budget denial says nothing about the ring.
            self.fail_net[node] += 1

    def _deflect(self, f: Flit) -> None:
        self.defl_win[f.dst] += 1
        self.ej_offered[(f.dst, f.vc)][f.src] += 1
        super()._deflect(f)

    def _on_arrive_station(self, f: Flit) -> None:
        super()._on_arrive_station(f)
        self.ej_served[(f.dst, f.vc)][f.src] += 1
        self.ej_offered[(f.dst, f.vc)][f.src] += 1

    def _on_inject(self, f: Flit) -> None:
        super()._on_inject(f)
        bk = self._bkey(f.src, f.vc, f.dir)
        self.spent[bk] += 1
        self.ok_win[bk] += 1
        self.cum[bk] += 1
        cyc = self.hole.get((f.plane, f.dir, f.src, f.vc))
        if cyc and self.t in cyc:
            self.st["n_reserve_used"] += 1
        key = (f.src, f.dir, f.target)
        if key not in self._path_seen:
            self._path_seen.add(key)
            node = f.src
            for _ in range(f.target):
                self.path_nodes[f.src].add((node, f.dir))
                node = (node + f.dir) % self.n
            self.dst_nodes[(f.src, f.dir)].add(f.dst)

    def _src_keys(self, node: int, plane: PlaneId) -> list[Any]:
        keys = super()._src_keys(node, plane)
        if not self.hole or len(keys) < 2:
            return keys
        for vc in self._vc_list:
            for d in (1, -1):
                cyc = self.hole.get((plane, d, node, vc))
                if cyc and self.t in cyc:
                    # A hole is on one VC's segment. Let that VC take the
                    # board port first, or the slot is spent on another VC
                    # and the yield upstream bought nothing.
                    k = (node, plane, vc)
                    if k in keys:
                        return [k] + [x for x in keys if x != k]
        return keys

    def _ready_to_board(self, node: int, plane: PlaneId, d: Dir,
                        vc: str) -> bool:
        """Is the local node actually holding a flit for this exact slot?

        Without this check a hole preempts a pass-through flit for a slot
        nobody takes, which is pure loss.
        """
        q = self._q((node, plane, vc))
        return bool(q) and q[0].dir == d

    def _hits_hole(self, node: int, f: Flit) -> bool:
        """Would boarding `f` here run it through a hole reserved downstream?

        `_can_board` refuses a local injection when `arr_set` shows an in-ring
        flit landing within sigma, and that booking was made when some
        upstream node injected. So a hole can only be kept open by refusing
        the *injection* that would fill it — the plan's "upstream nodes must
        not inject into the reserved slot". Doing it at board time is also
        the only version that stays bufferless: a flit already riding the
        ring is never stopped, so nothing has to be stored anywhere.

        Link latencies are static, so each node can compute when its flit
        reaches every node on its path and compare against the hole schedule
        the bus broadcast.
        """
        idx, when = node, self.t
        for _ in range(f.target):
            idx = (idx + f.dir) % self.n
            when += self.topo.hop_lat_from((idx - f.dir) % self.n, f.dir)
            if idx == node:
                break
            cyc = self.hole.get((f.plane, f.dir, idx, f.vc))
            if cyc and any(c <= when < c + self.sigma for c in cyc):
                return True
        return False

    def _yields_to_hole(self, f: Flit) -> bool:
        """Should this in-ring flit stand aside for the local node's hole?

        Suppressing upstream injections cannot open every hole on its own:
        flits already riding — deflected ones above all — keep landing on
        reserved cycles. Letting the reserving node latch one of them for a
        cycle closes that last gap. The latch is capped at `hold_depth` flits
        per segment, so this is a fixed, countable buffer rather than the
        unbounded queue an unrestricted yield would need.
        """
        if self.p.hold_depth <= 0 or f.src == f.idx:
            return False
        seg = self._seg(f.plane, f.dir, f.idx, f.vc)
        if self.inring_hold[seg] >= self.p.hold_depth and not f.held:
            return False
        cyc = self.hole.get((f.plane, f.dir, f.idx, f.vc))
        return bool(cyc and self.t in cyc
                    and self._ready_to_board(f.idx, f.plane, f.dir, f.vc))

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        if inring and self.hole and self._yields_to_hole(f):
            self.st["n_reserve_yield"] += 1
            self._hold(f, self._seg(f.plane, f.dir, f.idx, f.vc))
            return False
        ok = super()._launch(f, inring=inring)
        if ok and self.p.mode == "s15":
            # `f.idx` has already advanced, so charge the segment we left.
            self.cross[((f.idx - f.dir) % self.n, f.dir, f.vc)][f.src] += 1
        return ok

    # -- control ------------------------------------------------------------

    def _may_inject(self, node: int, plane: PlaneId, f: Flit | None = None
                    ) -> bool:
        if not super()._may_inject(node, plane, f):
            return False
        if f is None:
            return True
        bk = self._bkey(node, f.vc, f.dir)
        self.demand_win[bk] += 1
        self.want_dir[bk][f.dir] += 1
        if not self._controlled(node):
            return True
        cyc = self.hole.get((f.plane, f.dir, node, f.vc))
        if cyc and self.t in cyc:
            return True                 # its own reserved slot is never gated
        if self.hole and self._hits_hole(node, f):
            self.st["n_reserve_yield"] += 1
            self._deny_cause = "fc_budget"
            return False
        init = self.p.budget_init if self.p.mode == "s15" else self._s1_win_cap()
        if self.spent[bk] >= self._allowed(bk, init):
            self.st["n_fc_deny"] += 1
            self._deny_cause = "fc_budget"
            return False
        return True

    def _s1_win_cap(self) -> int:
        """S1's per-node budget ceiling. Scale by VC count when ports split."""
        w = self.p.window
        if self.p.per_vc_ports:
            w = w * max(1, len(self._vc_list))
        scale = self.p.cap_scale if self.p.cap_scale > 0 else 1.0
        return max(self.p.budget_min, int(round(w * scale)))

    def _allowed(self, bk: tuple, init: int) -> int:
        """Flits releasable so far this window.

        Spending a whole window's budget as fast as free slots appear makes
        every node bursty in lockstep, and simultaneous arrivals at a memory
        node are what produce eject deflections -- which then ride the ring
        again and squeeze out injection at the nodes they pass.  Metering the
        budget out evenly (leaky bucket, depth ``pace_burst``) keeps the same
        rate while spreading arrivals.
        """
        b = self.budget.get(bk, init)
        if self.p.pace_burst <= 0:
            return b
        phase = (self.t % self.p.window) + 1
        return min(b, (b * phase) // self.p.window + self.p.pace_burst)

    def _ctrl_deliver(self) -> None:
        self.bus.deliver(self.t)

    def _aimd_tick(self) -> None:
        if (self.t % self.p.window) != self.p.window - 1:
            return
        self._broadcast()
        if self.p.mode == "s15":
            self._update_s15()
        else:
            self._update_s1()
        self._reset_window()

    def _broadcast(self) -> None:
        s15 = self.p.mode == "s15"
        for i in range(self.n):
            ok = sum(v for k, v in self.ok_win.items() if _node_of(k) == i)
            dem = sum(v for k, v in self.demand_win.items()
                      if _node_of(k) == i)
            fair: dict[tuple[int, str], int] = {}
            fair_ej: dict[str, int] = {}
            if s15:
                for vc in self._vc_list:
                    for d in (1, -1):
                        served = self.cross.get((i, d, vc))
                        adv = self._advertise((i, d, vc), served, served)
                        if adv is not None:
                            fair[(d, vc)] = adv
                    if self.p.eject_share:
                        adv = self._advertise(
                            ("ej", i, vc), self.ej_served.get((i, vc)),
                            self.ej_offered.get((i, vc)))
                        if adv is not None:
                            fair_ej[vc] = adv
            sig = self.p.signal
            self.bus.post(self.t, i, BusMsg(
                up=level_of(self.fail_net[i]) if sig != "down" else 0,
                down=level_of(self.defl_win[i]) if sig != "up" else 0,
                ok=ok, demand=dem,
                active=int(bool(ok or dem)),
                cum=sum(v for k, v in self.cum.items() if _node_of(k) == i),
                fair=fair, fair_ej=fair_ej,
            ))

    def _advertise(self, key: Any, served: dict[int, int] | None,
                   offered: dict[int, int] | None) -> int | None:
        """The max-min share a shared resource offers the sources using it.

        Capacity is what the resource has been *seen to carry*, not its paper
        figure. The paper figure is unreachable here — arbitration for the
        shared leave port turns a fraction of arrivals into full-revolution
        deflections — and advertising a share nobody can attain constrains
        nobody. The running peak is used rather than this window's load so
        that throttling cannot ratchet the advertised share downwards.

        Demand is estimated per source as its usage here scaled by the ratio
        of what it asked for to what it got, both of which arrive on the bus.
        A source that is not asking keeps its small allocation and max-min
        hands the leftover to the sources that are.
        """
        if not served:
            return None
        peak = max(sum(served.values()), self.cap_peak[key] * 0.98)
        self.cap_peak[key] = peak
        if peak < self.p.busy_frac * self.hop_cap:
            return None                     # not a bottleneck; say nothing
        cap = min(self.hop_cap, int(peak * self.p.fair_headroom))
        demands = []
        for s, c in (offered or served).items():
            m = self.bus.view[s]
            demands.append(int(c * m.demand / m.ok) if m.ok else c)
        return maxmin_share(demands, cap)

    def _max_recv_level(self, node: int) -> int:
        """拥塞反馈: max level over this node's 受控节点."""
        best = 0
        for j, _d in self.path_nodes.get(node, ()):
            m = self.bus.view[j]
            best = max(best, m.up, m.down)
            if best >= LEVEL_MAX:
                break
        return best

    def _fair_target(self, node: int, vc: str) -> int:
        """How many flits this node may board per window on `vc`.

        Shares combine differently depending on how the traffic uses the
        resource. Every flit heading one way round crosses *all* the hops on
        that side, so those shares take a min. But a flit lands on exactly
        *one* leave port, and a node's two directions are separate streams,
        so those shares add. Taking a global min — the obvious reading —
        would charge a node once for a resource it only puts a fraction of
        its traffic through, and throttle it to a fraction of its due.
        """
        total = 0
        for d in (1, -1):
            hop = self.p.window
            for j, dd in self.path_nodes.get(node, ()):
                if dd != d:
                    continue
                adv = self.bus.view[j].fair.get((d, vc))
                if adv is not None and adv < hop:
                    hop = adv
            dsts = self.dst_nodes.get((node, d)) or ()
            if not dsts:
                continue
            ej = sum(self.bus.view[j].fair_ej.get(vc, self.p.window)
                     for j in dsts)
            total += min(hop, ej)
        if total == 0:
            total = self.p.window
        return max(self.p.budget_min, min(self.p.window, total))

    def _update_s1(self) -> None:
        p = self.p
        alpha = ALPHA_BANDS[p.band]
        beta = BETA_BANDS[p.band]
        rec_t, rec_b, rec_l, rec_r, rec_ok = [], [], [], [], []
        cap = self._s1_win_cap()
        for i in range(self.n):
            recv = self._max_recv_level(i)
            keys = ([(i, 1), (i, -1)] if p.dir_split else [i])
            if not self._controlled(i):
                rec_b.append(self.budget.get(keys[0], cap))
                rec_l.append(0); rec_r.append(recv)
                rec_ok.append(sum(self.ok_win.get(bk, 0) for bk in keys))
                continue
            node_b, node_lv = 0, 0
            self.sig_sum["up"][i] += self.fail_tot[i]
            self.sig_sum["down"][i] += self.defl_win[i]
            self.sig_sum["up_lv"][i] += level_of(self.fail_tot[i]) > 0
            self.sig_sum["down_lv"][i] += level_of(self.defl_win[i]) > 0
            self.sig_sum["recv_lv"][i] += recv > 0
            self.sig_sum["windows"][i] += 1
            for bk in keys:
                if p.dir_split:
                    own = self.fail_dir.get(bk, 0)
                elif p.signal == "up":
                    own = self.fail_tot[i]
                elif p.signal == "down":
                    own = self.defl_win[i]
                else:
                    own = max(self.fail_tot[i], self.defl_win[i])
                final = level_of(max(0, own - LEVEL_STEP * recv))
                b = self.budget.get(bk, cap)
                if final > 0:
                    a = (alpha["lo"] if final <= 2 else
                         alpha["mid"] if final <= 5 else alpha["hi"])
                    b = max(p.budget_min, int(b * a))
                    self.st["n_aimd_decrease"] += 1
                else:
                    g = (beta["clear"] if recv == 0 else
                         beta["lo"] if recv <= 2 else beta["hi"])
                    b = min(cap, b + g)
                    self.st["n_aimd_increase"] += 1
                self.budget[bk] = b
                node_b += b
                node_lv = max(node_lv, final)
            rec_b.append(node_b // len(keys))
            rec_l.append(node_lv)
            rec_r.append(recv)
            rec_ok.append(sum(self.ok_win.get(bk, 0) for bk in keys))
        if p.trace:
            self.trace["t"].append(self.t)
            self.trace["budget"].append(rec_b)
            self.trace["level"].append(rec_l)
            self.trace["recv"].append(rec_r)
            self.trace["ok"].append(rec_ok)

    def _reset_window(self) -> None:
        self.spent.clear()
        self.fail_tot.clear()
        self.fail_dir.clear()
        self.fail_net.clear()
        self.defl_win.clear()
        self.ok_win.clear()
        self.demand_win.clear()
        self.want_dir.clear()
        self.cross.clear()
        self.ej_served.clear()
        self.ej_offered.clear()

    # -- S15: max-min fair share + bounded slot reservation -----------------

    def _spread(self) -> float:
        """How unequal cumulative progress is across the active nodes.

        A fair-share controller has no business throttling a workload that is
        already fair — on uniform traffic every core is symmetric, the shares
        bind anyway, and the ring loses throughput for nothing. Engaging only
        once the bus shows real spread makes the scheme free in that case.
        """
        cum = [m.cum for i, m in enumerate(self.bus.view)
               if m.active and self._controlled(i)]
        if len(cum) < 2:
            return 0.0
        mean = sum(cum) / len(cum)
        if mean <= 0:
            return 0.0
        return (max(cum) - min(cum)) / mean

    def _update_s15(self) -> None:
        """Track the advertised fair share, then reserve a slot for whoever
        still cannot reach it.

        The rate half fixes *who* should slow down — S1's max-of-levels tells
        every core the same thing, a max-min share tells the core that is
        over its share and nobody else. The reservation half exists because
        a rate alone cannot guarantee a slot on a bufferless ring: if the
        freed slot is immediately taken by another pass-through flit, the
        starved node is no better off.
        """
        p = self.p
        beta = BETA_BANDS[p.band]["clear"]
        engaged = self._spread() > p.fair_tol
        rec_b, rec_l, rec_r, rec_ok, rec_t = [], [], [], [], []
        for i in range(self.n):
            for vc in self._vc_list:
                bk = (i, vc)
                tgt = p.window if not engaged else self._fair_target(i, vc)
                self.target[bk] = tgt
                if not self._controlled(i):
                    continue
                want = tgt
                if p.credit:
                    self.credit[bk] = max(-p.window, min(
                        p.window, self.credit[bk] + tgt - self.ok_win.get(bk, 0)))
                    want = max(p.budget_min,
                               min(p.window, tgt + self.credit[bk]))
                b = self.budget.get(bk, p.budget_init)
                if b > want:                     # multiplicative decrease
                    b = max(p.budget_min, want, int(b * 0.5))
                    self.st["n_aimd_decrease"] += 1
                elif b < want:                   # additive increase
                    b = min(want, b + beta)
                    self.st["n_aimd_increase"] += 1
                self.budget[bk] = b
            rec_b.append(self.budget.get((i, "dat"), p.budget_init))
            rec_t.append(self.target.get((i, "dat"), p.window))
            rec_l.append(level_of(self.fail_tot[i]))
            rec_r.append(self._max_recv_level(i))
            rec_ok.append(self.ok_win.get((i, "dat"), 0))
        self._reserve_slots()
        if p.trace:
            self.trace["t"].append(self.t)
            self.trace["budget"].append(rec_b)
            self.trace["target"].append(rec_t)
            self.trace["level"].append(rec_l)
            self.trace["recv"].append(rec_r)
            self.trace["ok"].append(rec_ok)

    def _reserve_slots(self) -> None:
        """Hold future outgoing slots open for nodes that cannot reach their
        share, spread evenly over the coming window.

        This is the part a rate controller cannot do. Once a node's outgoing
        hop runs at 0.9 occupancy, telling other sources to slow down is not
        enough — whatever they give back is taken by the next pass-through
        flit, because in-ring priority is absolute. A hole inverts that
        priority for one cycle: the pass-through flit waits a cycle in a
        single latch and the local node gets the slot.

        Eligibility is decided ring-wide, on the cumulative counts the bus
        already carries, not on each node's own per-window target. Judging
        locally makes almost every node believe it is behind — they all post,
        every hole is cancelled by someone else's, and the ring pays tens of
        thousands of yields for nothing. Comparing against the mean picks out
        only the genuine stragglers, so their holes actually survive.

        A straggler posts at most `reserve_max` holes, never more than its
        deficit, spread across the window so no burst of yields piles up.
        Nothing is posted once the spread closes, so the mechanism costs
        nothing in the fair case.
        """
        p = self.p
        self.hole.clear()
        start = self.t + 1 + p.bus_lat
        msgs = self.bus.view
        live = [m.cum for i, m in enumerate(msgs)
                if m.active and self._controlled(i)]
        if not live:
            return
        mean_cum = sum(live) / len(live)
        for i in range(self.n):
            if not self._controlled(i):
                continue
            m = msgs[i]
            if not m.active:
                continue
            deficit = mean_cum - m.cum
            if deficit <= p.reserve_gap:
                continue                          # not a straggler
            for vc in self._vc_list:
                bk = (i, vc)
                if self.demand_win.get(bk, 0) <= self.ok_win.get(bk, 0):
                    continue                      # not actually asking
                d_want = self.want_dir.get(bk) or {}
                if not d_want:
                    continue
                d = max(d_want, key=lambda x: d_want[x])
                n_hole = min(p.reserve_max, int(deficit))
                if n_hole <= 0:
                    continue
                stride = max(1, p.window // n_hole)
                for j in range(n_hole):
                    c = start + j * stride
                    if c > self.t + p.window:
                        break
                    plane = j % self.n_planes
                    self.hole.setdefault((plane, d, i, vc), set()).add(c)
                    self.st["n_reserved"] += 1

    # -- reporting ----------------------------------------------------------

    def fc_summary(self) -> dict[str, Any]:
        cs = [i for i in range(self.n) if self._controlled(i)]
        out: dict[str, Any] = {
            "mode": self.p.mode, "window": self.p.window,
            "band": self.p.band, "scope": self.p.scope,
            "signal": self.p.signal,
            "bus_lat": self.p.bus_lat,
            "signal_sum": {k: {str(i): v[i] for i in cs}
                           for k, v in self.sig_sum.items()},
            "bus_posts": self.bus.n_posts,
            "bus_bits": self.bus.n_posts * self.bus.bits_per_post(self.p.mode),
            "n_fc_deny": self.st.get("n_fc_deny", 0),
            "n_aimd_increase": self.st.get("n_aimd_increase", 0),
            "n_aimd_decrease": self.st.get("n_aimd_decrease", 0),
            "n_reserved": self.st.get("n_reserved", 0),
            "n_reserve_used": self.st.get("n_reserve_used", 0),
            "n_reserve_yield": self.st.get("n_reserve_yield", 0),
            "path_nodes": {str(i): sorted({j for j, _d
                                           in self.path_nodes.get(i, ())})
                           for i in cs},
            "final_budget": {str(i): self.budget.get(
                self._bkey(i, "dat"), self.p.window) for i in cs},
        }
        if self.p.trace and self.trace["t"]:
            out["trace"] = {k: v for k, v in self.trace.items() if v}
            out["trace"]["nodes"] = list(range(self.n))
            n_win = len(self.trace["t"])
            out["mean_budget"] = {
                str(i): round(sum(w[i] for w in self.trace["budget"]) / n_win,
                              2) for i in cs}
            out["mean_level"] = {
                str(i): round(sum(w[i] for w in self.trace["level"]) / n_win,
                              3) for i in cs}
            out["mean_recv_level"] = {
                str(i): round(sum(w[i] for w in self.trace["recv"]) / n_win,
                              3) for i in cs}
        return out
