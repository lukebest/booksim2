#!/usr/bin/env python3
"""3D-stacked fabric: 6 top-die full rings over one bottom die of half rings.

Geometry
--------
**Top die** (x6). Each is the existing 20-node dual-plane *bidirectional* full
ring. Roles by ring index:

    even {0,2,...,18}                  -> AI core   (10 per die, 60 total)
    {1,3,5,7,11,13,15,17}              -> D2D bridge (8 per die, 48 total)
    {9, 19}                            -> non-terminal, forwards only

The former memory nodes are now die-to-die bridges: no HA lives on a top die.

**Bottom die**. 96 HAs as 12 rows x 8 columns, plus 48 attach points. The NoC
is 6 horizontal + 8 vertical *half rings*, where a half ring is a
**unidirectional closed ring** -- it still wraps, but carries one direction
only, so distance is not symmetric and `dst - src` must be taken modulo the
ring length.

  * 6 horizontal half rings, 8 attach points each (one per column).
    Rings 0,1 sit in the row1/row2 gap; 2,3 in row6/row7; 4,5 in row11/row12.
  * 8 vertical half rings, 18 nodes each: the column's 12 HAs interleaved with
    the 6 attach points that cross it. Travel order is

        HA r1, A(h0), A(h1), HA r2..r6, A(h2), A(h3),
        HA r7..r11, A(h4), A(h5), HA r12   -> wraps to HA r1

    8 x 18 = 144 = 96 HA + 48 attach.

An attach point is a node on **both** its horizontal ring and its column's
vertical ring. It is simultaneously the D2D landing point and the turn node.

Attach grouping
---------------
The 48 attach points are grouped **8 per top die as 2 attach rows x 4
columns**. A row gap holds 2 attach rows (its two horizontal rings) x 8
columns = 16 attach points = 2 groups, split left/right:

    gap 0 (h rings 0,1)  ->  die 0 cols 0-3,  die 1 cols 4-7
    gap 1 (h rings 2,3)  ->  die 2 cols 0-3,  die 3 cols 4-7
    gap 2 (h rings 4,5)  ->  die 4 cols 0-3,  die 5 cols 4-7

A horizontal ring still spans all 8 columns, so the two dies sharing a gap
also share its two horizontal rings, and either die can borrow one to reach a
column outside its own group.

The consequence that drives the whole study: a die's group covers only **4 of
the 8 columns**, so half of every core's writes must ride a horizontal ring to
reach the target column. The horizontal rings are therefore load bearing, and
with only 48 directed horizontal links against 144 vertical ones they are a
candidate bottleneck in their own right.

HA-to-bridge binding
--------------------
Each HA is bound to one D2D bridge, so the destination alone fixes the
crossing point. A die has 8 bridges and must reach all 96 HAs, so each bridge
owns exactly one column's 12 HAs. Four of a die's bridges land in the column
they serve (no horizontal hop); the other four land in the group's columns and
must cross to the far half. The two dies in a gap use *different* horizontal
rings for their far traffic, which keeps the two rings of a gap balanced.

Routing
-------
Destination driven, matching the hardware: the target HA fixes the bridge,
hence the source and destination on *both* dies, and the path is then the
shortest one within each die -- direction and plane on the top-die ring, and
the shortest horizontal/vertical combination on the bottom die. Implemented as
a single shortest-path search in which every D2D edge except the bound one is
removed, which composes the two per-die shortest paths automatically.

`lat` and `hops` remain available as reference points, but they ignore the
binding and so are **not implementable** on this hardware; they exist only to
quantify what the binding costs.

A route is a list of directed edge ids, not a (direction, hop-count) pair:
a transfer changes ring, changes die, and changes axis, so there is no single
arc and no modular-arithmetic next hop.

Conflict clauses
----------------
R1  link mutual exclusion   -- one flit per (directed edge, CHI VC) per sigma
R2  boarding mutual exclusion -- <= board_ports per (station, plane) / cycle
R3  leaving mutual exclusion  -- <= leave_ports per (station, plane) / cycle
R4  turn / D2D hand-off       -- crossing rings or dies passes through a
                                 bounded transfer FIFO; strict bufferlessness
                                 holds on the links, not at the crossings
"""

from __future__ import annotations

import heapq
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

from rg_ring2_topo import (CHI_VCS_WRITE, RING2_LINK_LATS, Txn, vc_of)

# -- fixed shape -------------------------------------------------------------

N_TOP_DIE = 6
TOP_N = 20                                  # nodes per top-die ring
TOP_PLANES = 2
TOP_CORES = tuple(range(0, TOP_N, 2))       # 0,2,...,18
TOP_BRIDGES = (1, 3, 5, 7, 11, 13, 15, 17)  # former mem nodes -> D2D
TOP_INERT = (9, 19)                         # neither core nor bridge

N_ROWS = 12
N_COLS = 8
N_HA = N_ROWS * N_COLS                      # 96
# Horizontal rings live in the gaps *after* these rows (1-based rows).
H_GAP_AFTER_ROW = (1, 6, 11)
H_PER_GAP = 2
N_HRING = len(H_GAP_AFTER_ROW) * H_PER_GAP  # 6
N_ATTACH = N_HRING * N_COLS                 # 48
V_LEN = N_ROWS + N_HRING                    # 18 nodes per vertical half ring

# An attach group is 2 attach rows x GROUP_COLS columns = 8 points per top die.
GROUP_COLS = 4
assert H_PER_GAP * GROUP_COLS == len(TOP_BRIDGES) == 8
assert N_COLS == 2 * GROUP_COLS

SIGMA = 1
ANY_PLANE = -1         # bottom-die and D2D links are shared by both planes

# -- default latencies -------------------------------------------------------

BOT_HOP_LAT = 1        # bottom-die half-ring hop: short, regular array
D2D_LAT = 4            # die-to-die crossing: SerDes + CDC
TURN_LAT = 1           # horizontal -> vertical hand-off inside an attach point

Role = Literal["core", "bridge", "inert", "attach", "ha"]
RingKind = Literal["top", "d2d", "h", "v"]


# ---------------------------------------------------------------------------
# node identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Node:
    """One station in the stack, with a stable global id."""
    nid: int
    role: Role
    die: int                  # top-die index, or -1 for the bottom die
    idx: int = -1             # top-die ring index
    row: int = -1             # bottom-die row (1-based), HA only
    col: int = -1             # bottom-die column, attach + HA
    hring: int = -1           # horizontal half ring, attach only
    vpos: int = -1            # position on the vertical half ring

    @property
    def on_bottom(self) -> bool:
        return self.die < 0


def _v_layout() -> tuple[tuple[str, int], ...]:
    """Travel order of one vertical half ring.

    Returns 18 entries of ("ha", row) or ("attach", hring).
    """
    out: list[tuple[str, int]] = []
    h = 0
    for row in range(1, N_ROWS + 1):
        out.append(("ha", row))
        if row in H_GAP_AFTER_ROW:
            for _ in range(H_PER_GAP):
                out.append(("attach", h))
                h += 1
    return tuple(out)


V_LAYOUT = _v_layout()


class StackTopology:
    """6 bidirectional top-die rings + 48 D2D links + bottom half rings."""

    def __init__(self, *, n_die: int = N_TOP_DIE,
                 top_link_lats: Sequence[int] = RING2_LINK_LATS,
                 bot_hop_lat: int = BOT_HOP_LAT, d2d_lat: int = D2D_LAT,
                 turn_lat: int = TURN_LAT, sigma: int = SIGMA,
                 board_ports: int = 1, leave_ports: int = 1,
                 route_mode: str = "bound", h_assign: str = "split",
                 vcs: Sequence[str] = CHI_VCS_WRITE) -> None:
        if len(top_link_lats) != TOP_N:
            raise ValueError(f"top_link_lats must have {TOP_N} entries")
        if len(V_LAYOUT) != V_LEN:
            raise ValueError("vertical layout inconsistent")
        self.n_die = n_die
        self.top_link_lats = tuple(int(x) for x in top_link_lats)
        self.bot_hop_lat = bot_hop_lat
        self.d2d_lat = d2d_lat
        self.turn_lat = turn_lat
        self.route_mode = route_mode
        self.h_assign = h_assign
        self.sigma = sigma
        self.board_ports = board_ports
        self.leave_ports = leave_ports
        self.vcs = tuple(vcs)
        self.n_vc = len(self.vcs)

        self.nodes: list[Node] = []
        self._top: dict[tuple[int, int], int] = {}
        self._attach: dict[tuple[int, int], int] = {}   # (hring, col) -> nid
        self._ha: dict[tuple[int, int], int] = {}       # (row, col) -> nid
        self._build_nodes()
        self._build_binding()

        # directed edges, indexed by id
        self.edges: list[tuple[int, int]] = []          # (u, v)
        self.edge_lat: list[int] = []
        self.edge_plane: list[int] = []
        self.edge_ring: list[tuple[RingKind, int, int]] = []  # kind, id, plane
        self._eid: dict[tuple[int, int, int], int] = {}       # (u,v,plane)->id
        self._out: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._build_edges()

        # per-ring membership and successor map, for revolution deflection
        self.ring_of: dict[tuple[RingKind, int, int], list[int]] = {}
        self._succ: dict[tuple[Any, int, int], int] = {}
        self.edge_dir: list[int] = []
        self._build_rings()

        self._route_cache: dict[tuple[int, int, int], tuple[int, ...]] = {}
        self._lap_cache: dict[tuple[Any, int, int], tuple[int, ...]] = {}

    # -- construction ------------------------------------------------------

    def _add(self, node: Node) -> int:
        self.nodes.append(node)
        return node.nid

    def _build_nodes(self) -> None:
        nid = 0
        for d in range(self.n_die):
            for i in range(TOP_N):
                role: Role = ("core" if i in TOP_CORES else
                              "bridge" if i in TOP_BRIDGES else "inert")
                self._add(Node(nid, role, d, idx=i))
                self._top[(d, i)] = nid
                nid += 1
        for col in range(N_COLS):
            for vpos, (what, key) in enumerate(V_LAYOUT):
                if what == "ha":
                    self._add(Node(nid, "ha", -1, row=key, col=col, vpos=vpos))
                    self._ha[(key, col)] = nid
                else:
                    self._add(Node(nid, "attach", -1, col=col, hring=key,
                                   vpos=vpos))
                    self._attach[(key, col)] = nid
                nid += 1

        self.cores = [n.nid for n in self.nodes if n.role == "core"]
        self.bridges = [n.nid for n in self.nodes if n.role == "bridge"]
        self.attaches = [n.nid for n in self.nodes if n.role == "attach"]
        self.has = [n.nid for n in self.nodes if n.role == "ha"]
        self.n = len(self.nodes)

    def top(self, die: int, idx: int) -> int:
        return self._top[(die, idx)]

    def attach(self, hring: int, col: int) -> int:
        return self._attach[(hring, col)]

    def ha(self, row: int, col: int) -> int:
        return self._ha[(row, col)]

    # -- attach grouping and HA-to-bridge binding --------------------------

    def die_gap(self, die: int) -> int:
        """Which row gap a die's attach group sits in."""
        return die // H_PER_GAP

    def die_half(self, die: int) -> int:
        """0 for the left column half (0-3), 1 for the right half (4-7)."""
        return die % H_PER_GAP

    def die_cols(self, die: int) -> tuple[int, ...]:
        """The GROUP_COLS columns this die's attach points physically sit in."""
        base = self.die_half(die) * GROUP_COLS
        return tuple(range(base, base + GROUP_COLS))

    def die_hrings(self, die: int) -> tuple[int, int]:
        """(near ring, far ring) for this die, out of its gap's two rings.

        Two defensible choices, and they trade the two bottlenecks against
        each other:

        ``split``  the dies sharing a gap use opposite rings for far traffic,
                   so each ring carries one die's column crossings. Halves the
                   horizontal load, but both dies then land on the *same*
                   attach point of a given column, so they contend there.
        ``stack``  both dies use the low ring for near and the high one for
                   far. The two dies now reach a column at different attach
                   points, but all horizontal traffic piles onto one ring.
        """
        g, half = self.die_gap(die), self.die_half(die)
        lo = g * H_PER_GAP
        if self.h_assign == "stack":
            return (lo, lo + 1)
        far = lo + half
        return (lo + (1 - half), far)

    def bridge_target_col(self, die: int, idx: int) -> int:
        """The column whose 12 HAs are bound to this bridge.

        The 8 bridges of a die own the 8 columns one apiece: the first
        GROUP_COLS serve the die's own columns, the rest serve the far half.
        """
        j = TOP_BRIDGES.index(idx)
        cols = self.die_cols(die)
        if j < GROUP_COLS:
            return cols[j]
        return (cols[j - GROUP_COLS] + GROUP_COLS) % N_COLS

    def bridge_landing(self, die: int, idx: int) -> int:
        """The attach point this bridge's D2D link lands on."""
        j = TOP_BRIDGES.index(idx)
        near, far = self.die_hrings(die)
        cols = self.die_cols(die)
        if j < GROUP_COLS:
            return self.attach(near, cols[j])
        return self.attach(far, cols[j - GROUP_COLS])

    def ha_bridge(self, die: int, col: int) -> int:
        """The bridge index on `die` bound to every HA in `col`."""
        return self._bind[(die, col)]

    def _build_binding(self) -> None:
        self._bind: dict[tuple[int, int], int] = {}
        for d in range(self.n_die):
            for idx in TOP_BRIDGES:
                col = self.bridge_target_col(d, idx)
                if (d, col) in self._bind:
                    raise ValueError(f"die {d} column {col} bound twice")
                self._bind[(d, col)] = idx
            missing = [c for c in range(N_COLS) if (d, c) not in self._bind]
            if missing:
                raise ValueError(f"die {d} reaches no bridge for {missing}")

    def _link(self, u: int, v: int, lat: int, plane: int,
              ring: tuple[RingKind, int, int]) -> None:
        """Register a directed edge. `plane` = ANY_PLANE means every plane."""
        eid = len(self.edges)
        self.edges.append((u, v))
        self.edge_lat.append(lat)
        self.edge_plane.append(plane)
        self.edge_ring.append(ring)
        self._eid[(u, v, plane)] = eid
        self._out[u].append(eid)

    def _build_edges(self) -> None:
        # top dies: bidirectional, two planes, per-edge latency
        for d in range(self.n_die):
            for p in range(TOP_PLANES):
                for i in range(TOP_N):
                    j = (i + 1) % TOP_N
                    lat = self.top_link_lats[i]
                    u, v = self.top(d, i), self.top(d, j)
                    self._link(u, v, lat, p, ("top", d, p))
                    self._link(v, u, lat, p, ("top", d, p))
        # D2D: bridge <-> attach, both ways. The crossing is one physical
        # link shared by both top-die planes, so it is plane-agnostic.
        for d in range(self.n_die):
            for j, idx in enumerate(TOP_BRIDGES):
                u, v = self.top(d, idx), self.bridge_landing(d, idx)
                self._link(u, v, self.d2d_lat, ANY_PLANE, ("d2d", d, j))
                self._link(v, u, self.d2d_lat, ANY_PLANE, ("d2d", d, j))
        # horizontal half rings: unidirectional, wraps
        for h in range(N_HRING):
            for c in range(N_COLS):
                u = self.attach(h, c)
                v = self.attach(h, (c + 1) % N_COLS)
                self._link(u, v, self.bot_hop_lat, ANY_PLANE, ("h", h, 0))
        # vertical half rings: unidirectional, wraps
        for c in range(N_COLS):
            for pos in range(V_LEN):
                u = self._v_node(c, pos)
                v = self._v_node(c, (pos + 1) % V_LEN)
                self._link(u, v, self.bot_hop_lat, ANY_PLANE, ("v", c, 0))

    def _v_node(self, col: int, pos: int) -> int:
        what, key = V_LAYOUT[pos]
        return self.ha(key, col) if what == "ha" else self.attach(key, col)

    def _build_rings(self) -> None:
        for d in range(self.n_die):
            for p in range(TOP_PLANES):
                self.ring_of[("top", d, p)] = [self.top(d, i)
                                               for i in range(TOP_N)]
        for h in range(N_HRING):
            self.ring_of[("h", h, 0)] = [self.attach(h, c)
                                         for c in range(N_COLS)]
        for c in range(N_COLS):
            self.ring_of[("v", c, 0)] = [self._v_node(c, p)
                                         for p in range(V_LEN)]
        # Travel direction of every edge, and the ring successor map that
        # `lap()` walks. Top rings carry both directions; a half ring and a
        # D2D crossing carry one.
        pos: dict[tuple[Any, int], int] = {}
        for rk, members in self.ring_of.items():
            for i, nid in enumerate(members):
                pos[(rk, nid)] = i
        for eid, (u, v) in enumerate(self.edges):
            rk = self.edge_ring[eid]
            if rk[0] == "d2d":
                self.edge_dir.append(0)
                continue
            members = self.ring_of[rk]
            d = 1 if pos[(rk, v)] == (pos[(rk, u)] + 1) % len(members) else -1
            self.edge_dir.append(d)
            self._succ[(rk, u, d)] = eid

    def lap(self, ring: Any, node: int, direction: int = 1) -> tuple[int, ...]:
        """One full revolution of `ring` starting and ending at `node`.

        This is what a deflection costs: a flit that cannot leave its ring --
        because the eject queue or the turn FIFO is full -- keeps circulating
        rather than being buffered or dropped.
        """
        key = (ring, node, direction)
        hit = self._lap_cache.get(key)
        if hit is not None:
            return hit
        out: list[int] = []
        cur = node
        while True:
            eid = self._succ[(ring, cur, direction)]
            out.append(eid)
            cur = self.edges[eid][1]
            if cur == node:
                break
        res = tuple(out)
        self._lap_cache[key] = res
        return res

    # -- geometry ----------------------------------------------------------

    @property
    def directed_links(self) -> int:
        return len(self.edges)

    @property
    def hop_bw_cap(self) -> int:
        return len(self.edges) * self.n_vc

    def eid(self, u: int, v: int, plane: int = 0) -> int:
        return self._eid[(u, v, plane)]

    def edge_dst(self, eid: int) -> int:
        return self.edges[eid][1]

    def is_d2d(self, eid: int) -> bool:
        return self.edge_ring[eid][0] == "d2d"

    def fabric_of(self, eid: int) -> str:
        """Which fabric an edge belongs to, for the capacity accounting."""
        return self.edge_ring[eid][0]

    def capacity(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for eid in range(len(self.edges)):
            out[self.fabric_of(eid)] += 1
        return dict(out)

    # -- routing -----------------------------------------------------------

    def route(self, src: int, dst: int, plane: int = 0, *,
              mode: str | None = None) -> tuple[int, ...]:
        """Directed-edge sequence from `src` to `dst`.

        `mode` selects the routing policy:

        ``bound`` the hardware's own rule and the default. The destination HA
                  fixes the bound bridge, so the crossing point is not a
                  routing choice; the path is then shortest within each die.
        ``lat``   least total latency ignoring the binding.
        ``hops``  least hop count ignoring the binding.

        `lat` and `hops` are **not implementable** here -- they let a write
        cross at whichever bridge happens to be convenient, which the
        HA-to-bridge binding forbids. They are kept to quantify that cost.

        `plane` selects which top-die plane the route may use; bottom-die and
        D2D edges are shared by both planes. Ties break deterministically.
        """
        mode = mode or self.route_mode
        key = (src, dst, plane, mode)
        hit = self._route_cache.get(key)
        if hit is not None:
            return hit
        if src == dst:
            self._route_cache[key] = ()
            return ()
        only = self._bound_d2d(src, dst) if mode == "bound" else None
        out = self._dijkstra(src, dst, plane, mode, only)
        self._route_cache[key] = out
        return out

    def _bound_d2d(self, src: int, dst: int) -> int | None:
        """The one D2D crossing this source/destination pair is allowed.

        Returns None for traffic that stays on one die, which then routes
        freely within it.
        """
        a, b = self.nodes[src], self.nodes[dst]
        if a.role == "core" and b.role in ("ha", "attach"):
            die, col = a.die, b.col
        elif a.role in ("ha", "attach") and b.role == "core":
            die, col = b.die, a.col
        else:
            return None
        idx = self.ha_bridge(die, col)
        return self.eid(self.top(die, idx), self.bridge_landing(die, idx),
                        ANY_PLANE) if a.role == "core" else \
            self.eid(self.bridge_landing(die, idx), self.top(die, idx),
                     ANY_PLANE)

    def _dijkstra(self, src: int, dst: int, plane: int, mode: str,
                  only_d2d: int | None) -> tuple[int, ...]:
        """Shortest path, optionally pinned to a single D2D crossing.

        Pinning the crossing is what makes this the composition of the two
        per-die shortest paths: the die boundary is no longer a choice, so the
        search optimises each side independently.
        """
        weight = (lambda e: 1) if mode == "hops" else \
            (lambda e: self.edge_lat[e])
        dist: dict[int, tuple[int, int]] = {src: (0, 0)}
        prev: dict[int, int] = {}
        pq: list[tuple[int, int, int]] = [(0, 0, src)]
        seen: set[int] = set()
        while pq:
            lat, hops, u = heapq.heappop(pq)
            if u in seen:
                continue
            seen.add(u)
            if u == dst:
                break
            for eid in self._out[u]:
                ep = self.edge_plane[eid]
                if ep != ANY_PLANE and ep != plane:
                    continue          # a top-die plane is private to itself
                if only_d2d is not None and self.is_d2d(eid) \
                        and eid != only_d2d:
                    continue          # the binding allows exactly one crossing
                v = self.edges[eid][1]
                if v in seen:
                    continue
                nl, nh = lat + weight(eid), hops + 1
                old = dist.get(v)
                if old is None or (nl, nh) < old:
                    dist[v] = (nl, nh)
                    prev[v] = eid
                    heapq.heappush(pq, (nl, nh, v))
        if dst not in prev and dst != src:
            raise ValueError(f"unreachable: {src} -> {dst}")
        path: list[int] = []
        cur = dst
        while cur != src:
            eid = prev[cur]
            path.append(eid)
            cur = self.edges[eid][0]
        return tuple(reversed(path))

    def _ring_path(self, rk: Any, u: int, v: int, direction: int) -> list[int]:
        out: list[int] = []
        cur = u
        while cur != v:
            eid = self._succ[(rk, cur, direction)]
            out.append(eid)
            cur = self.edges[eid][1]
        return out

    def _top_path(self, die: int, plane: int, u: int, v: int) -> list[int]:
        """Shorter-latency direction around the top-die ring."""
        rk = ("top", die, plane)
        cw = self._ring_path(rk, u, v, 1)
        ccw = self._ring_path(rk, u, v, -1)
        kcw = (sum(self.edge_lat[e] for e in cw), len(cw))
        kccw = (sum(self.edge_lat[e] for e in ccw), len(ccw))
        return cw if kcw <= kccw else ccw

    def route_lat(self, src: int, dst: int, plane: int = 0) -> int:
        return sum(self.edge_lat[e] for e in self.route(src, dst, plane))

    def pick_plane(self, src: int, dst: int, *,
                   occupancy: dict[int, int] | None = None) -> int:
        """Least-occupied top-die plane; bottom-only paths are plane 0."""
        if self.nodes[src].on_bottom and self.nodes[dst].on_bottom:
            return 0
        if occupancy is None:
            return 0
        best = min(range(TOP_PLANES), key=lambda p: (occupancy.get(p, 0), p))
        occupancy[best] = occupancy.get(best, 0) + 1
        return best

    # -- bounds ------------------------------------------------------------

    def write_bounds(self, txns: Sequence[Txn], *, m_req: int = 1,
                     m_rsp: int = 2, m_wdata: int = 4,
                     t_ha: int = 0) -> dict[str, Any]:
        """Lower bounds on makespan for a closed WriteNoSnp batch.

        Independent CHI VCs make the link floor the max over VCs, not the sum.
        Inject / leave ports merge every VC because a station has one port.
        """
        sig = self.sigma
        mult = {"req": m_req, "rsp": m_rsp, "dat": m_wdata}
        link: dict[str, dict[int, int]] = {vc: defaultdict(int)
                                           for vc in ("req", "rsp", "dat")}
        port: dict[tuple[str, int], int] = defaultdict(int)
        fabric: dict[str, int] = defaultdict(int)
        legs: dict[str, int] = defaultdict(int)
        occ: dict[int, int] = {}
        for t in txns:
            plane = self.pick_plane(t.core, t.ha, occupancy=occ)
            fwd = self.route(t.core, t.ha, plane)
            rev = self.route(t.ha, t.core, plane)
            for vc, path, m in (("req", fwd, m_req), ("dat", fwd, m_wdata),
                                ("rsp", rev, m_rsp)):
                for eid in path:
                    link[vc][eid] += m
                    fabric[self.fabric_of(eid)] += m
                port[("board", self.edges[path[0]][0])] += m
                port[("leave", self.edges[path[-1]][1])] += m
                legs[vc] = max(legs[vc],
                               sum(self.edge_lat[e] for e in path))

        link_by_vc = {vc: (max(d.values()) if d else 0) * sig
                      for vc, d in link.items()}
        link_lb = max(link_by_vc.values()) if link_by_vc else 0
        port_lb = (max(port.values()) if port else 0) * sig
        # Per-fabric floor: total flit-hops on that fabric over its link count.
        cap = self.capacity()
        fab_lb = {k: math.ceil(v / max(1, cap.get(k, 1))) * sig
                  for k, v in fabric.items()}
        cut_lb = max(fab_lb.values()) if fab_lb else 0
        txn_lb = (legs["req"] + m_req * sig + t_ha
                  + legs["rsp"] + sig
                  + legs["dat"] + m_wdata * sig + t_ha
                  + legs["rsp"] + sig)
        bound = max(link_lb, port_lb, cut_lb, txn_lb)
        return {
            "link_by_vc": link_by_vc, "link_lb": link_lb,
            "port_lb": port_lb, "cut_lb": cut_lb, "fabric_lb": dict(fab_lb),
            "txn_lb": txn_lb, "bound": bound, "n_txn": len(txns),
            "capacity": cap, "n_vc": self.n_vc,
            "m_req": m_req, "m_rsp": m_rsp, "m_wdata": m_wdata,
        }


# ---------------------------------------------------------------------------
# workload
# ---------------------------------------------------------------------------

def build_uniform_write(topo: StackTopology, *, k: int = 400,
                        m_wdata: int = 4, seed: int = 0,
                        dies: Sequence[int] | None = None) -> list[Txn]:
    """Every AI core writes `k` times, uniformly over all 96 HAs.

    Each core draws a fresh permutation-based cycle over the HA list so the
    per-core destination histogram is as flat as the count allows: demand is
    symmetric by construction, and anything unequal in the result is the
    fabric's doing.

    `dies` restricts the traffic to the cores of those top dies. The same
    fabric under a lighter load wants a *larger* per-core concurrency, not a
    smaller one -- which is the whole reason a single configured limit cannot
    be right everywhere.
    """
    rng = random.Random(seed)
    hs = list(topo.has)
    out: list[Txn] = []
    tid = 0
    cs = ([c for c in topo.cores if topo.nodes[c].die in set(dies)]
          if dies is not None else topo.cores)
    for c in cs:
        bag: list[int] = []
        while len(bag) < k:
            chunk = hs[:]
            rng.shuffle(chunk)
            bag.extend(chunk)
        for i in range(k):
            out.append(Txn(tid, c, bag[i], 1, 0, "write", m_wdata))
            tid += 1
    return out


if __name__ == "__main__":
    t = StackTopology()
    assert len(t.cores) == 60, len(t.cores)
    assert len(t.has) == 96, len(t.has)
    assert len(t.attaches) == 48, len(t.attaches)
    assert len(t.bridges) == 48, len(t.bridges)
    assert t.n == N_TOP_DIE * TOP_N + N_HA + N_ATTACH, t.n
    cap = t.capacity()
    print(f"nodes={t.n} cores={len(t.cores)} has={len(t.has)} "
          f"attach={len(t.attaches)}")
    print(f"directed links={t.directed_links} capacity={cap}")
    # a core on die 0 reaching an HA: must cross D2D exactly once
    c0 = t.top(0, 0)
    h0 = t.ha(7, 3)
    r = t.route(c0, h0, 0)
    print(f"route core(d0,i0) -> HA(r7,c3): {len(r)} hops "
          f"lat={sum(t.edge_lat[e] for e in r)} "
          f"d2d={sum(1 for e in r if t.is_d2d(e))}")
    assert sum(1 for e in r if t.is_d2d(e)) == 1
    tx = build_uniform_write(t, k=2)
    assert len(tx) == 120, len(tx)
    b = t.write_bounds(tx)
    print(f"bound={b['bound']} link={b['link_lb']} port={b['port_lb']} "
          f"fabric={b['fabric_lb']} txn={b['txn_lb']}")
