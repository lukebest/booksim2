#!/usr/bin/env python3
"""Dimension-sliced 2D bufferless ring: topology + conflict definition D-R.

Topology
--------
6 row rings of 8 nodes + 8 column rings of 6 nodes, each bidirectional with
wrap-around. Node (x,y) belongs to exactly two rings: row ring y and column
ring x, so **every node is a bridge** between its two rings.

A ring of k nodes has k adjacent segments (k-1 near neighbours + 1 wrap), so

    6 rows * 8 + 8 cols * 6 = 48 + 48 = 96 undirected = 192 directed segments

The two directions of one segment are INDEPENDENT resources: counter-rotating
arcs on the same physical ring never contend. Against the 8x6 mesh (7*6 + 5*8
= 82 undirected / 164 directed) the wrap segments are the only difference:
+6 +8 = 14 undirected, hence 96/82 = 1.17x metal. That link set is *identical*
to the repo's folded 2D torus (`Topology("torus")`), which this module asserts.
The ring differs only at the NODES: a torus router is a buffered full crossbar,
a ring station has no crossbar, has a bounded number of insert/extract points,
and cannot turn without leaving one ring and boarding the other.

Wire model
----------
Folding is a layout, so it has to be paid for in wire length, not assumed away.
One core pitch costs PITCH_H=5 cycles horizontally and PITCH_V=7 vertically. A
folded k-cycle is an out-and-back tour over the k collinear cores, so a ring
neighbour is normally the core AFTER NEXT -- two pitches, 10 / 14 cycles -- and
exactly two segments per ring close the tour between physically adjacent cores
at one pitch (5 / 7 cycles). Per lane that is 2(k-1) pitches, i.e. the 2x-mesh
metal the attachment study charges. Hop-minimal routing stays latency-minimal:
the two short segments sit half a ring apart, so any half-ring arc contains
exactly one of them and the two directions of a tie stay tied.

Changing rings costs t_turn = T_TURN_BRIDGE = 10 cycles at the bridge, for the
calendar and for the `ring_base` simulator alike. A 2D path therefore pays one
bridge crossing that no routing choice can avoid.

Definition D-R (five clauses)
-----------------------------
R1  ring-link mutual exclusion   -- 192 directed segments, occupancy is an arc
R2  boarding mutual exclusion    -- <= board_ports per (node, ring) per cycle
R3  leaving mutual exclusion     -- <= leave_ports per (node, ring) per cycle;
                                    destination ejection SHARES the extract
                                    point with turn-offs
R4  turn atomicity               -- a turn consumes the row ring's extract
                                    point and the column ring's insert point
                                    exactly `t_turn` apart: zero slack, no
                                    waiting allowed
R5  same-VOQ serialization + STATIC route -- ring candidate routes differ in
                                    hop count AND wire delay, so switching
                                    route breaks ordering (unlike mesh ROMM,
                                    which is latency invariant)

R2/R3/R4 are NOT implied by R1: two arcs can meet at one node on one ring with
disjoint link sets (counter-rotating convergence, or a turn-off colliding with
a local ejection) and a pure link predicate cannot see it. That is the
quantitative reason D-M must not be reused here.
"""

from __future__ import annotations

import heapq
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

from rg_topo import (MX, MY, PITCH_H, PITCH_V, RAMP, RAMP_BW, T_TURN_BRIDGE,
                     coord, nid)

# The wire setup used by the islip2d study before the folded-pitch model landed:
# one uniform hop delay per segment (wrap included) and a free ring change. Kept
# so those results stay reproducible; do not use it for new ring work.
LEGACY_WIRE = {"pitch_h": 7, "pitch_v": 9, "folded": False, "t_turn": 1}

RingId = tuple[str, int]          # ("row", y) | ("col", x)
Edge = tuple[int, int]            # directed segment, by node ids
Pair = tuple[int, int]
DimOrder = Literal["RC", "CR"]
SpatialReuse = Literal["arc", "whole_ring"]


# ---------------------------------------------------------------------------
# 1. Arcs and two-phase paths
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Arc:
    """A contiguous run of segments on one ring, in one direction."""
    ring: RingId
    dir: int                      # +1 (increasing index) | -1
    nodes: tuple[int, ...]        # first = boarding node, last = leaving node

    @property
    def hops(self) -> int:
        return len(self.nodes) - 1

    @property
    def start(self) -> int:
        return self.nodes[0]

    @property
    def end(self) -> int:
        return self.nodes[-1]

    @property
    def empty(self) -> bool:
        return self.hops == 0

    def links(self) -> list[Edge]:
        return [(self.nodes[i], self.nodes[i + 1]) for i in range(self.hops)]

    def key(self) -> tuple[RingId, int]:
        """`whole_ring` resource: the ring and the direction of travel."""
        return (self.ring, self.dir)


@dataclass(frozen=True)
class RingPath:
    src: int
    dst: int
    order: DimOrder
    a1: Arc                       # first phase (may be empty)
    a2: Arc                       # second phase (may be empty)
    turn: int | None              # node where the ring change happens

    @property
    def hops(self) -> int:
        return self.a1.hops + self.a2.hops

    @property
    def arcs(self) -> tuple[Arc, ...]:
        return tuple(a for a in (self.a1, self.a2) if not a.empty)

    def links(self) -> list[Edge]:
        return self.a1.links() + self.a2.links()

    def signature(self) -> tuple:
        """Identity used by R5 (static-route) checking."""
        return (self.order, self.a1.ring, self.a1.dir, self.a1.nodes,
                self.a2.ring, self.a2.dir, self.a2.nodes)


def board_key(node: int, ring: RingId) -> tuple[str, int, RingId]:
    return ("board", node, ring)


def leave_key(node: int, ring: RingId) -> tuple[str, int, RingId]:
    return ("leave", node, ring)


# ---------------------------------------------------------------------------
# 2. Footprint: rigid, zero-slack occupancy relative to t0
# ---------------------------------------------------------------------------

@dataclass
class RingFootprint:
    """Occupancy of one granted ring transfer.

    Every entry is (resource_key, offset_from_t0[, duration]). `turn` records
    the (leave_offset, board_offset) pair that clause R4 pins together.
    """
    flow_id: int
    src: int
    dst: int
    path: RingPath
    m: int
    sigma: int
    links: list[tuple[Edge, int]] = field(default_factory=list)
    boards: list[tuple[tuple[str, int, RingId], int]] = field(
        default_factory=list)
    leaves: list[tuple[tuple[str, int, RingId], int]] = field(
        default_factory=list)
    rings: list[tuple[tuple[RingId, int], int, int]] = field(
        default_factory=list)
    turn: tuple[int, int] | None = None
    dur: int = 0                  # m*sigma: per-resource occupancy
    wire: int = 0                 # t0 -> head of last hop arrives at dst
    release: int = 0
    pressure: int = 0

    @property
    def tail(self) -> int:
        lt = max((pref + self.dur for _, pref in self.links), default=0)
        rt = max((off + d for _, off, d in self.rings), default=0)
        return max(lt, rt, self.wire + self.dur, self.dur)

    @property
    def eject(self) -> int:
        return self.wire + self.dur + RAMP

    @property
    def hops(self) -> int:
        return self.path.hops

    @property
    def voq_key(self) -> tuple:
        return (self.src, self.dst)

    @property
    def arrivals(self) -> dict[int, int]:
        return {self.dst: self.wire}


@dataclass
class RingMcastFootprint:
    """One boarding that serves several leave points on a SINGLE ring.

    This is the copy-and-continue primitive: the flit rides one arc and every
    node in `dsts` takes a copy as it passes, so the arc is paid for once no
    matter how many members read it. Two consequences that the unicast
    footprint does not have:

      * R3 is charged once per member, not once per transfer, because every
        copy consumes that node's extract point for `dur` cycles.
      * there is no turn, so R4 is vacuous. A multicast that must change rings
        is expressed as two phases, one arc each, because a bufferless station
        cannot fan out across rings without a copy sitting somewhere.

    `op="ADD"` marks a transfer whose payload the receiving PE accumulates into
    its L1 rather than storing beside its own copy. It changes nothing about
    the network occupancy -- it is recorded here only so the calendar exporter
    can emit the right opcode and the data-semantics checker can fold items.
    """
    flow_id: int
    src: int
    dsts: tuple[int, ...]
    arc: Arc
    m: int
    sigma: int
    op: str = "FWD"
    links: list[tuple[Edge, int]] = field(default_factory=list)
    boards: list[tuple[tuple[str, int, RingId], int]] = field(
        default_factory=list)
    leaves: list[tuple[tuple[str, int, RingId], int]] = field(
        default_factory=list)
    rings: list[tuple[tuple[RingId, int], int, int]] = field(
        default_factory=list)
    turn: tuple[int, int] | None = None
    dur: int = 0
    wire: int = 0                 # t0 -> head reaches the FARTHEST member
    release: int = 0
    pressure: int = 0
    arrive: dict[int, int] = field(default_factory=dict)

    # -- the attributes verify_dr / the packer share with the unicast form ---

    @property
    def dst(self) -> int:
        return self.dsts[-1]

    @property
    def tail(self) -> int:
        lt = max((pref + self.dur for _, pref in self.links), default=0)
        rt = max((off + d for _, off, d in self.rings), default=0)
        return max(lt, rt, self.wire + self.dur, self.dur)

    @property
    def eject(self) -> int:
        return self.wire + self.dur + RAMP

    @property
    def hops(self) -> int:
        return self.arc.hops

    @property
    def voq_key(self) -> tuple:
        return (self.src, self.dsts)

    @property
    def arrivals(self) -> dict[int, int]:
        return dict(self.arrive)


AnyFootprint = "RingFootprint | RingMcastFootprint"


# ---------------------------------------------------------------------------
# 3. The topology
# ---------------------------------------------------------------------------

class RingTopology:
    """Dimension-sliced 2D bufferless ring with explicit port resources."""

    def __init__(self, mx: int = MX, my: int = MY, *, sigma: int = 1,
                 delay_scale: int = 1, board_ports: int = 1,
                 leave_ports: int = 1, t_turn: int = T_TURN_BRIDGE,
                 pitch_h: int = PITCH_H, pitch_v: int = PITCH_V,
                 folded: bool = True,
                 spatial_reuse: SpatialReuse = "arc"):
        if spatial_reuse not in ("arc", "whole_ring"):
            raise ValueError(spatial_reuse)
        self.mx, self.my = mx, my
        self.n = mx * my
        self.sigma = sigma
        self.delay_scale = delay_scale
        self.folded = folded
        self.pitch_h = pitch_h * delay_scale
        self.pitch_v = pitch_v * delay_scale
        # A typical (2-pitch) segment, kept for audits and for the unfolded
        # control where every segment is the same length.
        self.H = self.pitch_h * (2 if folded else 1)
        self.V = self.pitch_v * (2 if folded else 1)
        self.board_ports = board_ports
        self.leave_ports = leave_ports
        self.t_turn = t_turn
        self.spatial_reuse = spatial_reuse
        self.row_rings: list[RingId] = [("row", y) for y in range(my)]
        self.col_rings: list[RingId] = [("col", x) for x in range(mx)]
        self.rings: list[RingId] = self.row_rings + self.col_rings
        self.directed_links: list[Edge] = self._directed_links()
        self.undirected_links = sorted(
            {frozenset(e) for e in self.directed_links},
            key=lambda s: tuple(sorted(s)))

    # -- structure ----------------------------------------------------------

    def ring_size(self, ring: RingId) -> int:
        return self.mx if ring[0] == "row" else self.my

    def ring_nodes(self, ring: RingId) -> list[int]:
        kind, idx = ring
        if kind == "row":
            return [nid(x, idx, self.mx) for x in range(self.mx)]
        return [nid(idx, y, self.mx) for y in range(self.my)]

    def pitch(self, ring: RingId) -> int:
        return self.pitch_h if ring[0] == "row" else self.pitch_v

    def link_pitches(self, ring: RingId, i: int) -> int:
        """Physical length, in core pitches, of the segment leaving index `i`.

        Folding lays a k-cycle over k collinear cores as an out-and-back tour,
        so a ring neighbour is normally the core *after next* -- two pitches --
        and exactly two segments close the tour over physically adjacent cores:
        the far turn (k/2-1 -> k/2) and the near one (k-1 -> 0, the segment an
        unfolded drawing would show as the long wrap). Total 2(k-1) pitches,
        which is the 2x-mesh metal the attachment study charges scheme A.
        """
        if not self.folded:
            return 1
        k = self.ring_size(ring)
        return 1 if i % k in (k // 2 - 1, k - 1) else 2

    def link_lat(self, ring: RingId, i: int) -> int:
        """Wire delay of the segment leaving index `i` of `ring`."""
        return self.pitch(ring) * self.link_pitches(ring, i)

    def arc_lats(self, arc: Arc) -> list[int]:
        """Per-hop wire delay along `arc`, in travel order."""
        out = []
        for h in range(arc.hops):
            i = self.index_on(arc.ring, arc.nodes[h])
            out.append(self.link_lat(arc.ring,
                                     i if arc.dir > 0 else i - 1))
        return out

    def ring_wire(self, ring: RingId) -> int:
        """Total wire delay round one lane of `ring`."""
        return sum(self.link_lat(ring, i)
                   for i in range(self.ring_size(ring)))

    def wire_distance(self, src: int, dst: int) -> int:
        """Zero-contention shortest delay, minimised over every legal route.

        Dijkstra over states (core, ring being ridden): riding a segment costs
        that segment's own wire delay and changing rings costs `t_turn`. This is
        routing independent by construction -- it does not assume the two-phase
        dimension order the calendars use, so it is a floor for ANY schedule,
        including one that relays through a third core.
        """
        if src == dst:
            return 0
        best: dict[tuple[int, RingId], int] = {}
        # boarding either of the core's two rings is free: the turn cost is only
        # paid when a flit already riding one ring moves to the other
        heap = [(0, src, r) for r in self.rings_of(src)]
        heapq.heapify(heap)
        out = None
        while heap:
            d, node, ring = heapq.heappop(heap)
            if best.get((node, ring), 1 << 60) <= d:
                continue
            best[(node, ring)] = d
            if node == dst:
                out = d
                break
            i = self.index_on(ring, node)
            k = self.ring_size(ring)
            order = self.ring_nodes(ring)
            for direction in (1, -1):
                j = (i + direction) % k
                lat = self.link_lat(ring, i if direction > 0 else i - 1)
                heapq.heappush(heap, (d + lat, order[j], ring))
            for other in self.rings_of(node):
                if other != ring:
                    heapq.heappush(heap, (d + self.t_turn, node, other))
        return out if out is not None else -1

    def rings_of(self, node: int) -> tuple[RingId, RingId]:
        x, y = coord(node, self.mx)
        return ("row", y), ("col", x)

    def _directed_links(self) -> list[Edge]:
        out: list[Edge] = []
        for ring in self.rings:
            nodes = self.ring_nodes(ring)
            k = len(nodes)
            for i in range(k):
                a, b = nodes[i], nodes[(i + 1) % k]
                out.append((a, b))
                out.append((b, a))
        return out

    def index_on(self, ring: RingId, node: int) -> int:
        x, y = coord(node, self.mx)
        return x if ring[0] == "row" else y

    def make_arc(self, ring: RingId, start: int, end: int, direction: int
                 ) -> Arc:
        """Arc from start to end travelling in `direction`, wrapping if needed."""
        k = self.ring_size(ring)
        order = self.ring_nodes(ring)
        i = self.index_on(ring, start)
        j = self.index_on(ring, end)
        hops = (j - i) % k if direction > 0 else (i - j) % k
        seq = [order[(i + direction * h) % k] for h in range(hops + 1)]
        return Arc(ring=ring, dir=direction, nodes=tuple(seq))

    def hop_options(self, ring: RingId, start: int, end: int) -> list[int]:
        """Directions whose hop count is minimal (both, on an exact tie)."""
        k = self.ring_size(ring)
        i = self.index_on(ring, start)
        j = self.index_on(ring, end)
        cw = (j - i) % k
        ccw = (i - j) % k
        if cw == 0:
            return [1]
        if cw < ccw:
            return [1]
        if ccw < cw:
            return [-1]
        return [1, -1]

    # -- paths --------------------------------------------------------------

    def make_path(self, s: int, d: int, order: DimOrder, d1: int, d2: int
                  ) -> RingPath:
        """Two-phase path. RC = row ring first, CR = column ring first."""
        sx, sy = coord(s, self.mx)
        dx, dy = coord(d, self.mx)
        if order == "RC":
            r1: RingId = ("row", sy)
            r2: RingId = ("col", dx)
            t = nid(dx, sy, self.mx)
        else:
            r1 = ("col", sx)
            r2 = ("row", dy)
            t = nid(sx, dy, self.mx)
        a1 = self.make_arc(r1, s, t, d1)
        a2 = self.make_arc(r2, t, d, d2)
        turn = None if (a1.empty or a2.empty) else t
        return RingPath(src=s, dst=d, order=order, a1=a1, a2=a2, turn=turn)

    def candidates(self, s: int, d: int, *, minimal_only: bool = True
                   ) -> list[RingPath]:
        """Direction x dimension-order candidate set.

        Minimal set = shortest direction on each phase (2 dimension orders,
        doubled per exact tie: 8-node rings tie at 4 hops, 6-node at 3).
        `minimal_only=False` adds the long way round, which the plan expects to
        HURT: every arc inflates, so the arc-load bound rebounds.
        """
        out: list[RingPath] = []
        seen: set[tuple] = set()
        sx, sy = coord(s, self.mx)
        dx, dy = coord(d, self.mx)
        for order in ("RC", "CR"):
            if order == "RC":
                r1, r2 = ("row", sy), ("col", dx)
                t = nid(dx, sy, self.mx)
            else:
                r1, r2 = ("col", sx), ("row", dy)
                t = nid(sx, dy, self.mx)
            o1 = self.hop_options(r1, s, t)
            o2 = self.hop_options(r2, t, d)
            if not minimal_only:
                if self.index_on(r1, s) != self.index_on(r1, t):
                    o1 = [1, -1]
                if self.index_on(r2, t) != self.index_on(r2, d):
                    o2 = [1, -1]
            for da in o1:
                for db in o2:
                    p = self.make_path(s, d, order, da, db)
                    sig = p.signature()
                    if sig in seen:
                        continue
                    seen.add(sig)
                    out.append(p)
        return out

    def fixed_path(self, s: int, d: int) -> RingPath:
        """`fixed` mode: RC order, shortest direction, +1 on a tie."""
        r1: RingId = ("row", coord(s, self.mx)[1])
        t = nid(coord(d, self.mx)[0], coord(s, self.mx)[1], self.mx)
        r2: RingId = ("col", coord(d, self.mx)[0])
        d1 = self.hop_options(r1, s, t)[0]
        d2 = self.hop_options(r2, t, d)[0]
        return self.make_path(s, d, "RC", d1, d2)

    # -- footprints ---------------------------------------------------------

    def footprint(self, flow_id: int, path: RingPath, m: int,
                  release: int = 0) -> RingFootprint:
        """Rigid occupancy: phase 2 starts exactly t_turn after phase 1 ends."""
        dur = m * self.sigma
        fp = RingFootprint(flow_id=flow_id, src=path.src, dst=path.dst,
                           path=path, m=m, sigma=self.sigma, dur=dur,
                           release=release)
        t = 0
        arcs = (path.a1, path.a2)
        prev_leave: int | None = None
        for idx, a in enumerate(arcs):
            if a.empty:
                continue
            if prev_leave is not None:
                t = prev_leave + self.t_turn      # R4: rigid hand-off
            fp.boards.append((board_key(a.start, a.ring), t))
            lats = self.arc_lats(a)
            acc = t
            for e, lat in zip(a.links(), lats):
                fp.links.append((e, acc))
                acc += lat
            fp.rings.append((a.key(), t, (acc - t) + dur))
            fp.leaves.append((leave_key(a.end, a.ring), acc))
            if idx == 0 and not arcs[1].empty:
                fp.turn = (acc, acc + self.t_turn)
            prev_leave = acc
            t = acc
        fp.wire = t
        return fp

    # -- multicast: one boarding, many extract points -----------------------

    def mcast_cover(self, ring: RingId, src: int, members: Iterable[int],
                    *, bidir: bool = True
                    ) -> list[tuple[int, tuple[int, ...]]]:
        """Split `members` into at most two arcs, one per direction.

        A bidirectional ring reaches the far side either way, so the cover that
        minimises both span and arc load sends each member the short way: with
        k nodes the worst member is ceil((k-1)/2) hops instead of k-1, and every
        segment carries half the sources it would under a single-direction
        cover. That halving is the whole point of walking both ways, and it is
        why the calendars below never use a full-circle multicast.

        `bidir=False` is the control that prices it: one arc all the way round.
        It costs one fewer boarding at the source -- worth remembering, because
        the two directions of a bidirectional cover contend for the SAME insert
        point in this model, so walking both ways is not free.

        Returns [(direction, member_nodes_in_arc_order)], excluding `src`.
        """
        k = self.ring_size(ring)
        i = self.index_on(ring, src)
        cw: list[tuple[int, int]] = []
        ccw: list[tuple[int, int]] = []
        for n in members:
            if n == src:
                continue
            j = self.index_on(ring, n)
            dcw = (j - i) % k
            dccw = (i - j) % k
            if bidir and dccw < dcw:
                ccw.append((dccw, n))
            else:
                cw.append((dcw, n))
        out: list[tuple[int, tuple[int, ...]]] = []
        for direction, lst in ((1, cw), (-1, ccw)):
            if not lst:
                continue
            lst.sort()
            out.append((direction, tuple(n for _, n in lst)))
        return out

    def mcast_footprint(self, flow_id: int, ring: RingId, src: int,
                        members: Sequence[int], direction: int, m: int,
                        *, op: str = "FWD", release: int = 0
                        ) -> RingMcastFootprint:
        """Rigid occupancy of one copy-and-continue arc.

        The arc runs from `src` to the farthest member; every member on the way
        charges its own extract point at the cycle the head passes it.
        """
        if not members:
            raise ValueError("multicast needs at least one member")
        far = max(members,
                  key=lambda n: (self.index_on(ring, n) - self.index_on(ring, src)
                                 ) % self.ring_size(ring) if direction > 0 else
                  (self.index_on(ring, src) - self.index_on(ring, n))
                  % self.ring_size(ring))
        arc = self.make_arc(ring, src, far, direction)
        dur = m * self.sigma
        lats = self.arc_lats(arc)
        fp = RingMcastFootprint(flow_id=flow_id, src=src,
                                dsts=tuple(members), arc=arc, m=m,
                                sigma=self.sigma, op=op, dur=dur,
                                release=release)
        fp.boards.append((board_key(src, ring), 0))
        acc = 0
        at: dict[int, int] = {}
        for pos, e in enumerate(arc.links()):
            fp.links.append((e, acc))
            acc += lats[pos]
            at[arc.nodes[pos + 1]] = acc
        member_set = set(members)
        missing = member_set - set(at)
        if missing:
            raise ValueError(f"members {sorted(missing)} not on arc {arc.nodes}")
        for n in members:
            fp.leaves.append((leave_key(n, ring), at[n]))
            fp.arrive[n] = at[n]
        fp.rings.append((arc.key(), 0, acc + dur))
        fp.wire = acc
        return fp

    # -- loads / bounds -----------------------------------------------------

    def link_load(self, paths: Iterable[RingPath]) -> dict[Edge, int]:
        load: dict[Edge, int] = defaultdict(int)
        for p in paths:
            for e in p.links():
                load[e] += 1
        return load

    def ring_load(self, paths: Iterable[RingPath]
                  ) -> dict[tuple[RingId, int], int]:
        load: dict[tuple[RingId, int], int] = defaultdict(int)
        for p in paths:
            for a in p.arcs:
                load[a.key()] += 1
        return load

    def port_load(self, paths: Iterable[RingPath]) -> dict[Any, int]:
        load: dict[Any, int] = defaultdict(int)
        for p in paths:
            for a in p.arcs:
                load[board_key(a.start, a.ring)] += 1
                load[leave_key(a.end, a.ring)] += 1
        return load

    def bounds(self, paths: Iterable[RingPath]) -> dict[str, Any]:
        pl = list(paths)
        ll = self.link_load(pl)
        rl = self.ring_load(pl)
        pol = self.port_load(pl)
        board = {k: v for k, v in pol.items() if k[0] == "board"}
        leave = {k: v for k, v in pol.items() if k[0] == "leave"}
        max_link = max(ll.values()) if ll else 0
        max_ring = max(rl.values()) if rl else 0
        max_board = max(board.values()) if board else 0
        max_leave = max(leave.values()) if leave else 0
        port_lb = max(math.ceil(max_board / max(1, self.board_ports)),
                      math.ceil(max_leave / max(1, self.leave_ports)))
        primary = max_ring if self.spatial_reuse == "whole_ring" else max_link
        return {
            "n_flows": len(pl),
            "max_link_load": max_link,
            "max_ring_load": max_ring,
            "max_board_load": max_board,
            "max_leave_load": max_leave,
            "port_lb": port_lb,
            "round_lb": max(primary, port_lb),
            "link_witness": (list(max(ll, key=lambda k: ll[k])) if ll
                             else None),
            "port_witness": (str(max(pol, key=lambda k: pol[k])) if pol
                             else None),
            "spatial_reuse": self.spatial_reuse,
        }

    def footprint_bounds(self, fps: Iterable[Any]) -> dict[str, Any]:
        """Same bounds as `bounds()` but read off footprints, so it works for
        multicast too. Loads are in CYCLES of occupancy (m*sigma per grant),
        not in grants, because a calendar is packed in cycles and a multi-flit
        grant holds its segment for the whole burst.
        """
        link: dict[Edge, int] = defaultdict(int)
        ring: dict[tuple[RingId, int], int] = defaultdict(int)
        board: dict[Any, int] = defaultdict(int)
        leave: dict[Any, int] = defaultdict(int)
        inj: dict[int, int] = defaultdict(int)
        ej: dict[int, int] = defaultdict(int)
        n = 0
        for fp in fps:
            n += 1
            for e, _ in fp.links:
                link[e] += fp.dur
            for k, _, _ in fp.rings:
                ring[k] += fp.dur
            for k, _ in fp.boards:
                board[k] += fp.dur
            for k, _ in fp.leaves:
                leave[k] += fp.dur
            inj[fp.src] += fp.dur
            for d in fp.arrivals:
                ej[d] += fp.dur
        max_link = max(link.values()) if link else 0
        max_ring = max(ring.values()) if ring else 0
        max_board = max(board.values()) if board else 0
        max_leave = max(leave.values()) if leave else 0
        max_inj = max(inj.values()) if inj else 0
        max_ej = max(ej.values()) if ej else 0
        ramp_cap = max(1, RAMP_BW * self.sigma)
        primary = max_ring if self.spatial_reuse == "whole_ring" else max_link
        port_lb = max(math.ceil(max_board / max(1, self.board_ports)),
                      math.ceil(max_leave / max(1, self.leave_ports)))
        ramp_lb = max(math.ceil(max_inj / ramp_cap),
                      math.ceil(max_ej / ramp_cap))
        cands = {"arc_load": primary, "port": port_lb, "ramp": ramp_lb}
        binding = max(cands, key=lambda k: cands[k])
        return {
            "n_grants": n,
            "arc_load_lb": primary,
            "port_lb": port_lb,
            "ramp_lb": ramp_lb,
            "max_link_cycles": max_link,
            "max_ring_cycles": max_ring,
            "max_board_cycles": max_board,
            "max_leave_cycles": max_leave,
            "max_inj_cycles": max_inj,
            "max_eject_cycles": max_ej,
            "occupancy_lb": max(cands.values()),
            "binding_lb": binding,
            "total_link_cycles": sum(link.values()),
            "link_witness": (list(max(link, key=lambda k: link[k])) if link
                             else None),
        }

    # -- metal audit --------------------------------------------------------

    def audit(self) -> dict[str, Any]:
        mesh_undirected = self.my * (self.mx - 1) + self.mx * (self.my - 1)
        return {
            "mx": self.mx, "my": self.my, "n": self.n,
            "sigma": self.sigma, "delay_scale": self.delay_scale,
            "H": self.H, "V": self.V, "t_turn": self.t_turn,
            "folded": self.folded,
            "pitch_h": self.pitch_h, "pitch_v": self.pitch_v,
            "row_hop_cycles": sorted({self.link_lat(("row", 0), i)
                                      for i in range(self.mx)}),
            "col_hop_cycles": sorted({self.link_lat(("col", 0), i)
                                      for i in range(self.my)}),
            "row_ring_wire": self.ring_wire(("row", 0)),
            "col_ring_wire": self.ring_wire(("col", 0)),
            "board_ports": self.board_ports,
            "leave_ports": self.leave_ports,
            "spatial_reuse": self.spatial_reuse,
            "n_row_rings": len(self.row_rings),
            "n_col_rings": len(self.col_rings),
            "n_directed_links": len(self.directed_links),
            "n_undirected_links": len(self.undirected_links),
            "n_ring_mutex_units": (len(self.directed_links)
                                   if self.spatial_reuse == "arc"
                                   else 2 * len(self.rings)),
            "mesh_undirected": mesh_undirected,
            "metal_ratio_vs_mesh": round(
                len(self.undirected_links) / mesh_undirected, 4),
            "n_bridges": self.n,      # every node bridges its two rings
        }

    def assert_same_links_as_torus(self) -> dict[str, Any]:
        """The segment set must equal the repo's folded 2D torus."""
        from rg_topo import Topology
        t = Topology("torus")
        mine = {tuple(sorted(e)) for e in self.directed_links}
        theirs = {tuple(sorted(e)) for e in t.directed_links}
        return {
            "equal": mine == theirs,
            "n_mine": len(mine), "n_torus": len(theirs),
            "only_mine": len(mine - theirs), "only_torus": len(theirs - mine),
        }


# ---------------------------------------------------------------------------
# 4. Path plans (fixed / balanced / dyn) -- R5 requires a STATIC choice
# ---------------------------------------------------------------------------

@dataclass
class RingPlan:
    mode: str
    paths: dict[Pair, RingPath]
    bounds: dict[str, Any] = field(default_factory=dict)
    sweeps_run: int = 0
    trace: list[int] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode, "sweeps": self.sweeps_run,
                               "trace": self.trace}
        out.update({k: self.bounds.get(k) for k in
                    ("max_link_load", "max_ring_load", "max_board_load",
                     "max_leave_load", "port_lb", "round_lb")})
        return out


def fixed_plan(topo: RingTopology, pairs: Iterable[Pair]) -> RingPlan:
    pl = list(pairs)
    paths = {k: topo.fixed_path(*k) for k in pl}
    return RingPlan("fixed", paths, bounds=topo.bounds(paths[k] for k in pl))


def balanced_plan(topo: RingTopology, pairs: Iterable[Pair], *,
                  sweeps: int = 8, seed: int = 0,
                  minimal_only: bool = True) -> RingPlan:
    """Greedy + rip-up over the direction x dimension-order candidate set.

    Scores on the same resources the scheduler will arbitrate: arc load first,
    then port load, so a candidate that merely moves congestion from a segment
    onto an extract point does not count as an improvement.
    """
    pl = list(pairs)
    cand = {k: topo.candidates(k[0], k[1], minimal_only=minimal_only)
            for k in pl}
    paths: dict[Pair, RingPath] = {k: cand[k][0] for k in pl}

    link: dict[Edge, int] = defaultdict(int)
    port: dict[Any, int] = defaultdict(int)

    def add(p: RingPath, sign: int) -> None:
        for e in p.links():
            link[e] += sign
        for a in p.arcs:
            port[board_key(a.start, a.ring)] += sign
            port[leave_key(a.end, a.ring)] += sign

    for p in paths.values():
        add(p, +1)
    trace = [max(link.values()) if link else 0]

    order = list(pl)
    done = 0
    for sweep in range(max(0, sweeps)):
        done = sweep + 1
        order.sort(key=lambda k: -max((link[e] for e in paths[k].links()),
                                      default=0))
        moved = 0
        for k in order:
            add(paths[k], -1)
            best = paths[k]
            best_key: tuple | None = None
            for p in cand[k]:
                links = p.links()
                peak = max((link[e] for e in links), default=0)
                total = sum(link[e] for e in links)
                pk = 0
                for a in p.arcs:
                    pk = max(pk, port[board_key(a.start, a.ring)],
                             port[leave_key(a.end, a.ring)])
                key = (peak, pk, total, p.hops,
                       0 if p.signature() == paths[k].signature() else 1)
                if best_key is None or key < best_key:
                    best_key, best = key, p
            if best.signature() != paths[k].signature():
                moved += 1
                paths[k] = best
            add(paths[k], +1)
        trace.append(max(link.values()) if link else 0)
        if moved == 0:
            break

    return RingPlan("balanced" if minimal_only else "balanced_nonmin", paths,
                    bounds=topo.bounds(paths[k] for k in pl),
                    sweeps_run=done, trace=trace)


def dyn_plan(topo: RingTopology, pairs: Iterable[Pair], *, seed: int = 0,
             minimal_only: bool = True) -> RingPlan:
    """Independent random candidate per pair: the R5 violator, kept as the
    reorder-rate control. Ring candidates differ in hop count, so this DOES
    reorder; the mesh ROMM equivalent does not."""
    rng = random.Random(seed)
    pl = list(pairs)
    paths = {k: rng.choice(topo.candidates(k[0], k[1],
                                           minimal_only=minimal_only))
             for k in pl}
    return RingPlan("dyn", paths, bounds=topo.bounds(paths[k] for k in pl))


def build_ring_plan(topo: RingTopology, pairs: Iterable[Pair], mode: str, *,
                    seed: int = 0, sweeps: int = 8) -> RingPlan:
    if mode == "fixed":
        return fixed_plan(topo, pairs)
    if mode == "balanced":
        return balanced_plan(topo, pairs, sweeps=sweeps, seed=seed)
    if mode == "balanced_nonmin":
        return balanced_plan(topo, pairs, sweeps=sweeps, seed=seed,
                             minimal_only=False)
    if mode == "dyn":
        return dyn_plan(topo, pairs, seed=seed)
    raise ValueError(f"unknown ring path mode: {mode}")


def route_delay_spread(topo: RingTopology, pairs: Iterable[Pair], *,
                       minimal_only: bool = True) -> dict[str, Any]:
    """How badly candidate routes differ -- why R5 needs a static route.

    Any nonzero wire-delay spread means an in-flight route change can reorder,
    which is exactly what mesh ROMM avoids by latency invariance.

    Run it both ways: the minimal candidate set turns out to be latency
    invariant just like mesh ROMM, so the dividing line for R5 is not
    ring-versus-mesh but minimal-versus-non-minimal routing.
    """
    n_pairs = n_hop = n_wire = worst = 0
    for k in pairs:
        n_pairs += 1
        cands = topo.candidates(k[0], k[1], minimal_only=minimal_only)
        if len(cands) < 2:
            continue
        wires = {topo.footprint(0, p, 1).wire for p in cands}
        if len({p.hops for p in cands}) > 1:
            n_hop += 1
        if len(wires) > 1:
            n_wire += 1
            worst = max(worst, max(wires) - min(wires))
    return {
        "n_pairs": n_pairs,
        "pairs_with_hop_spread": n_hop,
        "pairs_with_wire_spread": n_wire,
        "frac_wire_spread": round(n_wire / n_pairs, 4) if n_pairs else 0.0,
        "max_wire_spread": worst,
        "latency_invariant": n_wire == 0,
    }


# ---------------------------------------------------------------------------
# 5. Independent D-R checker (five clauses, re-derived from the paths)
# ---------------------------------------------------------------------------

def _cap_violations(items: list[tuple[Any, int, int, int]], cap: int
                    ) -> list[dict[str, Any]]:
    """Concurrency > cap on any key. Items are (key, start, end, tag)."""
    by_key: dict[Any, list[tuple[int, int, int]]] = defaultdict(list)
    for key, s, e, tag in items:
        if e > s:
            by_key[key].append((s, e, tag))
    out: list[dict[str, Any]] = []
    for key, lst in by_key.items():
        ev: list[tuple[int, int, int]] = []
        for s, e, tag in lst:
            ev.append((s, 1, tag))
            ev.append((e, -1, tag))
        ev.sort(key=lambda t: (t[0], t[1]))
        live = 0
        for t, delta, tag in ev:
            live += delta
            if live > cap:
                out.append({"key": str(key), "t": t, "cap": cap,
                            "concurrent": live, "tag": tag})
                break
    return out


def occupancy(topo: RingTopology, fp: RingFootprint, t0: int
              ) -> dict[str, list[tuple[Any, int, int]]]:
    """Absolute-time occupancy of one grant, grouped by resource class."""
    dur = fp.dur
    out: dict[str, list[tuple[Any, int, int]]] = {
        "link": [(e, t0 + pref, t0 + pref + dur) for e, pref in fp.links],
        "board": [(k, t0 + off, t0 + off + dur) for k, off in fp.boards],
        "leave": [(k, t0 + off, t0 + off + dur) for k, off in fp.leaves],
        "ring": [(k, t0 + off, t0 + off + d) for k, off, d in fp.rings],
    }
    return out


def verify_dr(topo: RingTopology,
              items: Sequence[tuple[Any, int]]) -> dict[str, Any]:
    """Re-check all five D-R clauses from the paths, not from the scheduler.

    `items` = [(footprint, t0)]; a footprint is either a unicast
    `RingFootprint` or a `RingMcastFootprint`. Every clause is checked
    independently so a failure names the clause; R4 in particular asserts ZERO
    residency at the turn point, which is the property that makes the
    bufferless ring legal.

    Multicast needs no new clause, only the honest charging already implied by
    R3: a copy-and-continue arc appears once in `links` and once per member in
    `leaves`, so `_cap_violations` counts each copy against that node's extract
    point. What multicast DOES change is R5: the serialization key becomes the
    whole member set, because two arcs from one source to overlapping member
    sets are the same logical stream and must not interleave.
    """
    link_items: list[tuple[Any, int, int, int]] = []
    ring_items: list[tuple[Any, int, int, int]] = []
    board_items: list[tuple[Any, int, int, int]] = []
    leave_items: list[tuple[Any, int, int, int]] = []
    r4_bad: list[dict[str, Any]] = []
    turn_residency: list[int] = []
    by_voq: dict[tuple, list[tuple[int, Any]]] = defaultdict(list)
    n_mcast = 0
    n_copies = 0
    mcast_bad: list[dict[str, Any]] = []

    for fp, t0 in items:
        occ = occupancy(topo, fp, t0)
        for e, s, t in occ["link"]:
            link_items.append((e, s, t, fp.flow_id))
        for k, s, t in occ["ring"]:
            ring_items.append((k, s, t, fp.flow_id))
        for k, s, t in occ["board"]:
            board_items.append((k, s, t, fp.flow_id))
        for k, s, t in occ["leave"]:
            leave_items.append((k, s, t, fp.flow_id))
        if isinstance(fp, RingMcastFootprint):
            n_mcast += 1
            n_copies += len(fp.dsts)
            if len(fp.leaves) != len(set(fp.dsts)):
                mcast_bad.append({"flow": fp.flow_id,
                                  "reason": "leaves not one per member",
                                  "n_leaves": len(fp.leaves),
                                  "n_members": len(set(fp.dsts))})
            if len(fp.boards) != 1:
                mcast_bad.append({"flow": fp.flow_id,
                                  "reason": "multicast must board once",
                                  "n_boards": len(fp.boards)})
        if fp.turn is not None:
            leave_off, board_off = fp.turn
            residency = board_off - leave_off - topo.t_turn
            turn_residency.append(residency)
            if residency != 0:
                r4_bad.append({"flow": fp.flow_id, "residency": residency})
            # the two halves must be the SAME cycle pair on the two rings
            path = getattr(fp, "path", None)
            if path is None:
                r4_bad.append({"flow": fp.flow_id,
                               "reason": "turn without a two-phase path"})
            else:
                phase1_leave = leave_key(path.a1.end, path.a1.ring)
                phase2_board = board_key(path.a2.start, path.a2.ring)
                if (phase1_leave, leave_off) not in fp.leaves or \
                        (phase2_board, board_off) not in fp.boards:
                    r4_bad.append({"flow": fp.flow_id,
                                   "reason": "turn unpaired"})
        by_voq[fp.voq_key].append((t0, fp))

    if topo.spatial_reuse == "whole_ring":
        r1 = _cap_violations(ring_items, 1)
        r1_kind = "whole_ring"
    else:
        r1 = _cap_violations(link_items, 1)
        r1_kind = "arc"
    r2 = _cap_violations(board_items, topo.board_ports)
    r3 = _cap_violations(leave_items, topo.leave_ports)

    r5_bad: list[dict[str, Any]] = []
    for voq, lst in by_voq.items():
        lst.sort(key=lambda t: t[0])
        sigs = {(fp.path.signature() if getattr(fp, "path", None) is not None
                 else (fp.arc.ring, fp.arc.dir, fp.arc.nodes))
                for _, fp in lst}
        if len(sigs) > 1:
            r5_bad.append({"voq": str(voq), "reason": "route switched",
                           "n_routes": len(sigs)})
        for i in range(1, len(lst)):
            if lst[i][0] < lst[i - 1][0] + lst[i - 1][1].dur:
                r5_bad.append({"voq": str(voq), "reason": "overlap",
                               "t": lst[i][0]})
                break

    return {
        "n_grants": len(items),
        "r1_kind": r1_kind,
        "R1_link_violations": len(r1),
        "R2_board_violations": len(r2),
        "R3_leave_violations": len(r3),
        "R4_turn_violations": len(r4_bad),
        "R5_voq_violations": len(r5_bad),
        "MC_shape_violations": len(mcast_bad),
        "n_mcast_grants": n_mcast,
        "n_mcast_copies": n_copies,
        "max_turn_residency": max(turn_residency) if turn_residency else 0,
        "n_turns": len(turn_residency),
        "conflict_free": not (r1 or r2 or r3 or r4_bad or r5_bad or mcast_bad),
        "examples": {"R1": r1[:3], "R2": r2[:3], "R3": r3[:3],
                     "R4": r4_bad[:3], "R5": r5_bad[:3], "MC": mcast_bad[:3]},
    }


# ---------------------------------------------------------------------------
# 6. Why D-M must not be reused here (quantified)
# ---------------------------------------------------------------------------

def pair_conflict_kind(topo: RingTopology, pa: RingPath, pb: RingPath,
                       m: int = 1) -> dict[str, bool]:
    """Classify one simultaneous (same t0) pair of grants.

    `link` = a pure D-M-style predicate would see it.
    `board`/`leave` = only D-R sees it. The interesting population is
    link-disjoint but port-conflicting: those are the false negatives a
    transplanted mesh predicate would let through onto a bufferless ring.
    """
    fa = topo.footprint(0, pa, m)
    fb = topo.footprint(1, pb, m)
    oa, ob = occupancy(topo, fa, 0), occupancy(topo, fb, 0)

    def clash(cls: str, cap: int) -> bool:
        items = [(k, s, e, 0) for k, s, e in oa[cls]]
        items += [(k, s, e, 1) for k, s, e in ob[cls]]
        return bool(_cap_violations(items, cap))

    return {
        "link": clash("link", 1),
        "board": clash("board", topo.board_ports),
        "leave": clash("leave", topo.leave_ports),
    }


def misuse_stats(topo: RingTopology, paths: dict[Pair, RingPath], *,
                 n_samples: int = 20000, seed: int = 0) -> dict[str, Any]:
    """Sampled rates behind the "two predicates are not interchangeable" claim."""
    rng = random.Random(seed)
    keys = list(paths.keys())
    n_link_disjoint = 0
    n_port_clash = 0
    kinds = {"board_leave": 0, "board_board": 0, "leave_leave": 0}
    n_diff_diff = 0
    n_diff_diff_conflict = 0
    n_same_src = 0
    n_same_src_free = 0
    for _ in range(n_samples):
        a = rng.choice(keys)
        b = rng.choice(keys)
        if a == b:
            continue
        c = pair_conflict_kind(topo, paths[a], paths[b])
        any_conf = c["link"] or c["board"] or c["leave"]
        if not c["link"]:
            n_link_disjoint += 1
            if c["board"] or c["leave"]:
                n_port_clash += 1
                if c["board"] and c["leave"]:
                    kinds["board_leave"] += 1
                elif c["board"]:
                    kinds["board_board"] += 1
                else:
                    kinds["leave_leave"] += 1
        if a[0] != b[0] and a[1] != b[1]:
            n_diff_diff += 1
            if any_conf:
                n_diff_diff_conflict += 1
        if a[0] == b[0] and a[1] != b[1]:
            n_same_src += 1
            if not any_conf:
                n_same_src_free += 1
    return {
        "n_samples": n_samples,
        "link_disjoint_pairs": n_link_disjoint,
        "port_clash_among_link_disjoint": n_port_clash,
        "false_negative_rate_of_pure_R1": (
            round(n_port_clash / n_link_disjoint, 4) if n_link_disjoint else 0),
        "port_clash_kinds": kinds,
        "port_clash_kind_frac": {
            k: (round(v / n_port_clash, 3) if n_port_clash else 0)
            for k, v in kinds.items()},
        "crossbar_predicate_unsafe_rate": (
            round(n_diff_diff_conflict / n_diff_diff, 4) if n_diff_diff else 0),
        "same_src_actually_free_rate": (
            round(n_same_src_free / n_same_src, 4) if n_same_src else 0),
    }


def greedy_max_set(topo: RingTopology, paths: dict[Pair, RingPath], *,
                   clauses: str = "R1+R2+R3", trials: int = 20,
                   seed: int = 0, m: int = 1) -> dict[str, Any]:
    """Greedy maximum simultaneous (shared t0) set under a chosen predicate.

    Comparing `R1` against `R1+R2+R3` measures how much a pure link predicate
    OVERSTATES the set that may be released together on a ring.
    """
    rng = random.Random(seed)
    keys = list(paths.keys())
    use_ports = "R2" in clauses or "R3" in clauses
    sizes: list[int] = []
    for _ in range(trials):
        order = keys[:]
        rng.shuffle(order)
        used_link: set[Edge] = set()
        used_ring: set[tuple[RingId, int]] = set()
        board: dict[Any, int] = defaultdict(int)
        leave: dict[Any, int] = defaultdict(int)
        n = 0
        for k in order:
            p = paths[k]
            if topo.spatial_reuse == "whole_ring":
                rk = [a.key() for a in p.arcs]
                if any(r in used_ring for r in rk):
                    continue
            else:
                ls = p.links()
                if any(e in used_link for e in ls):
                    continue
            if use_ports:
                ok = True
                for a in p.arcs:
                    if board[board_key(a.start, a.ring)] >= topo.board_ports \
                            or leave[leave_key(a.end, a.ring)] >= \
                            topo.leave_ports:
                        ok = False
                        break
                if not ok:
                    continue
            for e in p.links():
                used_link.add(e)
            for a in p.arcs:
                used_ring.add(a.key())
                board[board_key(a.start, a.ring)] += 1
                leave[leave_key(a.end, a.ring)] += 1
            n += 1
        sizes.append(n)
    return {
        "clauses": clauses,
        "trials": trials,
        "mean": round(sum(sizes) / len(sizes), 1) if sizes else 0,
        "max": max(sizes) if sizes else 0,
        "min": min(sizes) if sizes else 0,
    }


if __name__ == "__main__":
    import json

    topo = RingTopology()
    print("--- audit ---")
    print(json.dumps(topo.audit(), indent=2))
    print("--- same link set as folded torus? ---")
    print(json.dumps(topo.assert_same_links_as_torus(), indent=2))

    a2a = [(s, d) for s in range(topo.n) for d in range(topo.n) if s != d]
    print("\n--- plans on alltoall ---")
    plans = {}
    for mode in ("fixed", "balanced", "balanced_nonmin", "dyn"):
        p = build_ring_plan(topo, a2a, mode)
        plans[mode] = p
        print(f"  {mode:16} {json.dumps(p.summary())}")

    print("\n--- whole_ring vs arc ---")
    wr = RingTopology(spatial_reuse="whole_ring")
    wp = fixed_plan(wr, a2a)
    print(f"  whole_ring     {json.dumps(wp.summary())}")

    print("\n--- route delay spread (why R5 needs static routes) ---")
    print(json.dumps(route_delay_spread(topo, a2a), indent=2))

    print("\n--- D-M misuse on the ring ---")
    print(json.dumps(misuse_stats(topo, plans["fixed"].paths,
                                  n_samples=4000), indent=2))

    print("\n--- greedy max simultaneous set ---")
    for cl in ("R1", "R1+R2+R3"):
        print(f"  {cl:10} {json.dumps(greedy_max_set(topo, plans['fixed'].paths, clauses=cl))}")

    print("\n--- D-R checker: must reject the bad case AND accept the good ---")
    fps = [topo.footprint(i, plans["fixed"].paths[k], 1)
           for i, k in enumerate(a2a[:40])]
    bad = verify_dr(topo, [(f, 0) for f in fps])
    good = verify_dr(topo, [(f, i * 200) for i, f in enumerate(fps)])
    print(f"  all at t0=0      conflict_free={bad['conflict_free']} "
          f"(R1={bad['R1_link_violations']} R2={bad['R2_board_violations']} "
          f"R3={bad['R3_leave_violations']})")
    print(f"  spaced 200 apart conflict_free={good['conflict_free']} "
          f"turns={good['n_turns']} "
          f"max_turn_residency={good['max_turn_residency']}")
    assert not bad["conflict_free"], "checker failed to see a real conflict"
    assert good["conflict_free"], "checker rejected a serialized schedule"
    assert good["max_turn_residency"] == 0, "R4 residency must be zero"
    print("  OK")

