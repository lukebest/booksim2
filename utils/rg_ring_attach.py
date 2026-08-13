#!/usr/bin/env python3
"""How should 48 AI cores attach to the 2D bufferless ring fabric?

The paper (Application-defined NoC for heterogeneous chiplets) fixes three
things and leaves one open:

  fixed  -- horizontal rings + vertical rings, bufferless, deflection based
  fixed  -- H<->V traffic changes ring through a BRIDGE (a transfer FIFO: the
            only place a flit may wait, because a ring itself cannot stall)
  fixed  -- a core has AT MOST 2 ports facing the ring fabric
  open   -- each ring may be a FULL ring (closed loop over the whole dimension,
            folded-torus layout so there is no single long wrap wire) or a HALF
            ring, and a core may spend its 2 ports in several ways

This module makes that open part an explicit design space and settles it with
structural facts instead of taste. Everything here is ROUTING-INDEPENDENT:
connectivity, port budget, cut capacity, hop distance, metal length, and the
three lower bounds that need no schedule (cut / core-port / L1-ramp). No
calendar is built, so no scheme is flattered by the quality of its schedule.

Two readings of "half ring" are both evaluated, because they are different
machines and the difference matters:

  half-span   -- the loop covers half the nodes of the dimension (row 8 -> 2x4,
                 col 6 -> 2x3). Shorter loop, fewer hops, less wire.
  half-lane   -- the loop covers the whole dimension but keeps only ONE of the
                 two counter-rotating lanes. Half the metal, so under a
                 metal-constant yardstick its links can be twice as wide
                 (sigma 2 -> 1), which is how this repo already compares
                 fabrics (`islip2d-mesh-ring-8x6.md` §3).

A BRIDGE here always has its own tap on each of the two rings it joins, and the
two taps may sit on different cores -- that is what makes a seam bridge able to
carry traffic across a cut that no ring segment crosses. A bridge whose two
taps are the same core is "co-located": in scheme A it reuses that core's two
ring ports, so turning costs no extra tap.

The decisive facts, in order of how much they move the answer:

  1. CONNECTIVITY. With <=2 ports per core, a half-SPAN split disconnects the
     fabric unless something repairs the seam: a column ring never changes x,
     and a split row ring never spans the seam, so no path crosses it.
  2. CUT CAPACITY. Every seam repair leaves fewer flits/cycle crossing the
     bisection than the full-ring fabric, because a store-and-forward FIFO
     replaces what used to be several parallel segments. Bandwidth-bound
     collectives (alltoall, allgather) inherit that loss directly.
  3. PORT/RAMP MATCH. A core's L1 ramp moves RAMP_BW=2 flits/cycle. Attaching
     1 row port + 1 column port makes ring ports exactly equal to the ramp, so
     neither side is an artificial bottleneck; 1 port halves the achievable
     inject/eject rate, and a 3rd port could not be fed anyway.

Output: results/ring_attach_8x6.json, rendered by
utils/gen_ring_collectives_report.py and asserted by
utils/verify_ring_collectives_8x6.py.
"""

from __future__ import annotations

import heapq
import itertools
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from rg_topo import (MX, MY, PITCH_H, PITCH_V, RAMP_BW, T_TURN_BRIDGE, coord,
                     nid)

N = MX * MY
ROOT = 0                # collective root, matches the rest of the ring work
OUT = Path(__file__).resolve().parents[1] / "results" / "ring_attach_8x6.json"

# Wire delay is charged per core pitch, so a scheme that needs longer wires
# pays for them in latency instead of getting them for free. This is the same
# model the pipeline uses, implemented independently on top of positions.
T_TURN = T_TURN_BRIDGE  # co-located bridge: hand-off between a core's 2 ports
T_BRIDGE = T_TURN + 1   # seam bridge: same crossing plus one store-and-forward
H_PITCH, V_PITCH = PITCH_H, PITCH_V

Kind = Literal["row", "col"]
PATTERNS = ("alltoall", "allgather", "allreduce", "gather", "broadcast",
            "reduce")


# ---------------------------------------------------------------------------
# 1. A scheme = rings + how cores tap them + where the bridges are
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ring:
    rid: str
    kind: Kind
    nodes: tuple[int, ...]        # cyclic order of cores on the loop
    lanes: int                    # 2 = counter-rotating pair, 1 = single lane

    @property
    def k(self) -> int:
        return len(self.nodes)

    def segments(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for i in range(self.k):
            a, b = self.nodes[i], self.nodes[(i + 1) % self.k]
            out.append((a, b))
            if self.lanes == 2:
                out.append((b, a))
        return out

    def positions(self) -> list[int]:
        """Physical index of each core along the ring's own dimension."""
        return [coord(c)[0] if self.kind == "row" else coord(c)[1]
                for c in self.nodes]

    def span(self) -> int:
        p = self.positions()
        return max(p) - min(p)

    def metal(self) -> int:
        """Wire length in core pitches, per lane summed over lanes.

        The shortest closed tour over collinear points is out-and-back, so a
        folded loop costs 2*span regardless of how the fold is ordered. For a
        loop over consecutive cores that is 2k-2, matching the folded torus.
        A loop that wraps around the array edge (staggered half rings) has
        span = MX-1 even though it holds only 4 cores, so it is NOT cheap.
        """
        return 2 * self.span() * self.lanes

    def tour(self) -> list[int]:
        """Physical positions in the order the folded loop visits them."""
        q = sorted(self.positions())
        k = len(q)
        out = [q[i] for i in range(0, k, 2)]              # go: every other core
        back = [q[i] for i in range(k - 1 if (k - 1) % 2 else k - 2, 0, -2)]
        return out + back                                  # closed, folded

    def link_pitches(self) -> list[int]:
        """Length of each segment of the folded loop, in core pitches.

        Folding trades one long wrap for uniformly medium wires: a consecutive
        loop gets k-2 segments of two pitches and two of one, so a ring
        neighbour is normally the core after next. That is where the ring's
        per-hop delay comes from, and it is why hop count alone is the wrong
        yardstick when comparing schemes with different spans.
        """
        t = self.tour()
        return [abs(t[i] - t[(i + 1) % len(t)]) for i in range(len(t))]

    def max_link_pitches(self) -> int:
        """Longest single wire, which is what sets the achievable clock.

        Folding a consecutive loop caps this at 2 pitches. A wrap-around loop
        cannot be folded down: some wire has to reach across the array.
        """
        return max(self.link_pitches())


@dataclass(frozen=True)
class Bridge:
    """Joins ring `ra` (tapped at core `ta`) to ring `rb` (tapped at `tb`)."""
    ra: str
    ta: int
    rb: str
    tb: int

    @property
    def colocated(self) -> bool:
        return self.ta == self.tb


@dataclass
class Scheme:
    key: str
    label: str
    note: str
    rings: list[Ring]
    bridges: list[Bridge] = field(default_factory=list)
    # (core, ring) taps the core deliberately does NOT own, so that its port
    # count stays inside the budget
    drop: set[tuple[int, str]] = field(default_factory=set)
    # a core may spend both ports on one ring (scheme H)
    width_per_tap: int = 1
    # flits/cycle a single segment carries. A scheme that spends half the metal
    # on wires is allowed to spend it on width instead, which is the
    # metal-constant yardstick this repo already uses (sigma 2 -> 1).
    width: int = 1

    def attach(self) -> dict[int, list[str]]:
        at: dict[int, list[str]] = defaultdict(list)
        for r in self.rings:
            for c in r.nodes:
                if (c, r.rid) not in self.drop:
                    at[c].append(r.rid)
        return {c: at.get(c, []) for c in range(N)}

    def ports(self) -> dict[int, int]:
        at = self.attach()
        return {c: len(at[c]) * self.width_per_tap for c in range(N)}


def _row_full(lanes: int = 2) -> list[Ring]:
    return [Ring(f"row{y}", "row", tuple(nid(x, y) for x in range(MX)), lanes)
            for y in range(MY)]


def _col_full(lanes: int = 2) -> list[Ring]:
    return [Ring(f"col{x}", "col", tuple(nid(x, y) for y in range(MY)), lanes)
            for x in range(MX)]


def _row_half(lanes: int = 2, *, stagger: bool = False) -> list[Ring]:
    """Rows as two loops of 4. `stagger` shifts odd rows by 2 columns, which is
    the only way a half-span row split stays connected across the seam without
    paying for seam bridges: the shifted loops straddle the seam themselves."""
    out: list[Ring] = []
    for y in range(MY):
        off = 2 if (stagger and y % 2) else 0
        for h in range(2):
            xs = [(off + 4 * h + i) % MX for i in range(4)]
            out.append(Ring(f"row{y}h{h}", "row",
                            tuple(nid(x, y) for x in xs), lanes))
    return out


def _col_half(lanes: int = 2) -> list[Ring]:
    out: list[Ring] = []
    for x in range(MX):
        for h in range(2):
            ys = [3 * h + i for i in range(3)]
            out.append(Ring(f"col{x}h{h}", "col",
                            tuple(nid(x, y) for y in ys), lanes))
    return out


def _colocated_hv(row_of: str = "row{y}", col_of: str = "col{x}"
                  ) -> list[Bridge]:
    return [Bridge(row_of.format(y=y), nid(x, y), col_of.format(x=x),
                   nid(x, y)) for y in range(MY) for x in range(MX)]


def schemes() -> list[Scheme]:
    """Candidates, all inside the <=2 ports/core budget."""
    out: list[Scheme] = []
    hv = _colocated_hv()

    out.append(Scheme(
        key="A_full_2port",
        label="A 全环双向：1 行口 + 1 列口",
        note="桥与核同址、复用核的这 2 个口，不额外开环上抽头；每核皆桥",
        rings=_row_full() + _col_full(), bridges=hv))

    out.append(Scheme(
        key="B_full_1lane",
        label="B 全环单向（半环＝只留一条车道）：1 行口 + 1 列口",
        note="金属减半，按金属恒定口径把省下的换成 2× 线宽；代价是跳数翻倍",
        rings=_row_full(1) + _col_full(1), bridges=hv, width=2))

    # half-span rows: each core still taps its row half and its full column;
    # the two loops of row y are joined at the seam by a bridge tapping x=3 on
    # the left loop and x=4 on the right loop
    row_h_hv = [Bridge(f"row{y}h{x // 4}", nid(x, y), f"col{x}", nid(x, y))
                for y in range(MY) for x in range(MX)]
    row_seam = [Bridge(f"row{y}h0", nid(3, y), f"row{y}h1", nid(4, y))
                for y in range(MY)]
    out.append(Scheme(
        key="C0_rowhalf_noseam",
        label="C0 行半环(2×4) + 列全环，不加缝桥",
        note="用来证明「半跨环必须修缝」：列环不改变 x，半行环又跨不过缝，"
             "左右两半彼此不可达",
        rings=_row_half() + _col_full(), bridges=row_h_hv))

    out.append(Scheme(
        key="C_rowhalf_seam",
        label="C 行半环(2×4) + 列全环 + 6 个行-行缝桥",
        note="行内跳数减半；跨缝必须过缝桥（存转发），x 对分塌到缝桥带宽",
        rings=_row_half() + _col_full(), bridges=row_h_hv + row_seam))

    col_h_hv = [Bridge(f"row{y}", nid(x, y), f"col{x}h{y // 3}", nid(x, y))
                for y in range(MY) for x in range(MX)]
    col_seam = [Bridge(f"col{x}h0", nid(x, 2), f"col{x}h1", nid(x, 3))
                for x in range(MX)]
    out.append(Scheme(
        key="D_colhalf_seam",
        label="D 列半环(2×3) + 行全环 + 8 个列-列缝桥",
        note="列本来只有 6 个核，再拆一半收益很小，却把 y 对分压到缝桥带宽",
        rings=_row_full() + _col_half(), bridges=col_h_hv + col_seam))

    both_hv = [Bridge(f"row{y}h{x // 4}", nid(x, y), f"col{x}h{y // 3}",
                      nid(x, y)) for y in range(MY) for x in range(MX)]
    out.append(Scheme(
        key="E_bothhalf_seam",
        label="E 行列都半环 + 14 个缝桥",
        note="跳数最短、金属最省，但两个方向的对分都塌到缝桥上",
        rings=_row_half() + _col_half(),
        bridges=both_hv + row_seam + col_seam))

    stag_hv: list[Bridge] = []
    for y in range(MY):
        off = 2 if y % 2 else 0
        for x in range(MX):
            h = 0 if ((x - off) % MX) < 4 else 1
            stag_hv.append(Bridge(f"row{y}h{h}", nid(x, y), f"col{x}",
                                  nid(x, y)))
    out.append(Scheme(
        key="F_rowhalf_stagger",
        label="F 行半环错位（奇偶行错开 2 列）+ 列全环，无缝桥",
        note="靠奇数行的半环自己跨过缝来保持连通，省掉缝桥硬件",
        rings=_row_half(stagger=True) + _col_full(), bridges=stag_hv))

    # 1 port: the core only taps its row ring; every H<->V transfer is done by
    # a bridge that owns its own taps on both rings
    only_col_dropped = {(nid(x, y), f"col{x}")
                        for x in range(MX) for y in range(MY)}
    out.append(Scheme(
        key="G_row_only_1port",
        label="G 每核只 1 口（只挂行环），纵向全靠桥",
        note="核侧最省，但注入/弹出被压到 1 flit/cy，只用掉一半 L1 ramp",
        rings=_row_full() + _col_full(), bridges=hv, drop=only_col_dropped))

    out.append(Scheme(
        key="H_two_on_row",
        label="H 2 口都挂同一行环（不挂列环）",
        note="行向进出翻倍，但核无法直接进出列环，每个跨行流程多两次桥",
        rings=_row_full() + _col_full(), bridges=hv, drop=only_col_dropped,
        width_per_tap=2))
    return out


# ---------------------------------------------------------------------------
# 2. Structure: connectivity, distance, cuts, metal
# ---------------------------------------------------------------------------

def _ring_map(s: Scheme) -> dict[str, Ring]:
    return {r.rid: r for r in s.rings}


def hop_graph(s: Scheme) -> dict[tuple[int, str], list[tuple[tuple[int, str],
                                                             int, int]]]:
    """States are (core, ring being ridden); edges are (state, cycles, hops).

    Riding a segment costs its own physical length: pitches x the dimension's
    per-pitch delay, so a folded two-pitch neighbour hop costs 2xPITCH and the
    two fold ends cost one. Turning at a core is free of wire but costs T_TURN
    and is only legal if the core taps both rings (co-located bridge). A bridge
    with taps on two different cores costs T_BRIDGE and is open to anyone
    reaching either tap.
    """
    rm = _ring_map(s)
    at = s.attach()
    g: dict[tuple[int, str], list[tuple[tuple[int, str], int, int]]] = \
        defaultdict(list)
    for r in s.rings:
        pitch = H_PITCH if r.kind == "row" else V_PITCH
        span = r.link_pitches()
        for i in range(r.k):
            a, b = r.nodes[i], r.nodes[(i + 1) % r.k]
            lat = pitch * span[i]
            g[(a, r.rid)].append(((b, r.rid), lat, 1))
            if r.lanes == 2:
                g[(b, r.rid)].append(((a, r.rid), lat, 1))
    for c in range(N):
        for r1, r2 in itertools.permutations(at[c], 2):
            g[(c, r1)].append(((c, r2), T_TURN, 0))
    for b in s.bridges:
        if b.colocated and b.ra in at[b.ta] and b.rb in at[b.tb]:
            continue                     # already covered by the core's turn
        if b.ta not in rm[b.ra].nodes or b.tb not in rm[b.rb].nodes:
            raise ValueError(f"bridge tap off-ring: {b}")
        g[(b.ta, b.ra)].append(((b.tb, b.rb), T_BRIDGE, 1))
        g[(b.tb, b.rb)].append(((b.ta, b.ra), T_BRIDGE, 1))
    return g


def distances(s: Scheme) -> dict[str, Any]:
    """All-pairs shortest latency over the state graph.

    A core may only enter/leave the fabric on a ring it taps itself, so a
    1-port scheme cannot start on a column ring even though bridges can put
    flits there.
    """
    g = hop_graph(s)
    at = s.attach()
    lat = [[None] * N for _ in range(N)]
    hop = [[None] * N for _ in range(N)]
    for src in range(N):
        if not at[src]:
            continue
        dist: dict[tuple[int, str], tuple[int, int]] = {}
        pq: list[tuple[int, int, tuple[int, str]]] = [
            (0, 0, (src, r)) for r in at[src]]
        heapq.heapify(pq)
        while pq:
            d, h, st = heapq.heappop(pq)
            if st in dist:
                continue
            dist[st] = (d, h)
            for nxt, w, dh in g[st]:
                if nxt not in dist:
                    heapq.heappush(pq, (d + w, h + dh, nxt))
        for dst in range(N):
            cand = [dist[(dst, r)] for r in at[dst] if (dst, r) in dist]
            if cand:
                lat[src][dst], hop[src][dst] = min(cand)
    pairs = [(a, b) for a in range(N) for b in range(N) if a != b]
    ok = [(a, b) for a, b in pairs if lat[a][b] is not None]
    lats = [lat[a][b] for a, b in ok]
    hs = [hop[a][b] for a, b in ok]
    return {
        "reachable_pairs": len(ok), "total_pairs": len(pairs),
        "connected": len(ok) == len(pairs),
        "diameter_cy": max(lats) if lats else None,
        "avg_lat_cy": round(sum(lats) / len(lats), 2) if lats else None,
        "max_hops": max(hs) if hs else None,
        "avg_hops": round(sum(hs) / len(hs), 2) if hs else None,
    }


def cut_rows(s: Scheme, axis: str) -> list[dict[str, Any]]:
    """Capacity of every straight cut, in flits/cycle per direction.

    A ring segment contributes if its endpoints straddle the cut. A bridge
    contributes if its two TAPS straddle the cut -- that is the only way a
    seam repair buys crossing bandwidth, and it buys exactly one FIFO's worth.
    """
    lim = MX if axis == "x" else MY

    def side(c: int, k: int) -> bool:
        return (coord(c)[0] if axis == "x" else coord(c)[1]) < k

    rows = []
    for k in range(1, lim):
        seg = 0
        for r in s.rings:
            for a, b in r.segments():
                if side(a, k) != side(b, k):
                    seg += 1
        # `seg` counts both directions when lanes=2; per-direction capacity is
        # what a schedule can use each cycle in the tighter direction
        fwd = sum(1 for r in s.rings for a, b in r.segments()
                  if side(a, k) and not side(b, k))
        bwd = seg - fwd
        br = sum(1 for b in s.bridges if side(b.ta, k) != side(b.tb, k))
        rows.append({"at": k, "segments": seg, "bridges": br,
                     "cap_per_dir": (min(fwd, bwd) + br) * s.width})
    return rows


def cuts(s: Scheme) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for axis in ("x", "y"):
        rows = cut_rows(s, axis)
        mn = min(rows, key=lambda r: r["cap_per_dir"])
        out[axis] = {"per_cut": rows, "min_cap_per_dir": mn["cap_per_dir"],
                     "at": mn["at"]}
    return out


def metal(s: Scheme) -> dict[str, Any]:
    at = s.attach()
    mesh_links = MY * (MX - 1) + MX * (MY - 1)     # 82 unit-length links
    mesh_pitches = 2 * mesh_links                  # both directions, length 1
    wire = sum(r.metal() for r in s.rings)
    # A bridge needs its own tap unless it sits on one core that already owns
    # ports on both of its rings -- that is what makes scheme A's turn free.
    extra = [b for b in s.bridges
             if not (b.colocated and b.ra in at[b.ta] and b.rb in at[b.tb])]
    return {
        "wire_pitches": wire,
        "wire_pitches_x_width": wire * s.width,
        "max_link_pitches": max(r.max_link_pitches() for r in s.rings),
        "n_directed_segments": sum(len(r.segments()) for r in s.rings),
        "n_undirected_segments": sum(r.k for r in s.rings),
        "n_rings": len(s.rings),
        "n_bridges": len(s.bridges),
        "n_extra_tap_bridges": len(extra),
        "segment_width": s.width,
        "mesh_wire_pitches": mesh_pitches,
        "wire_vs_mesh": round(wire * s.width / mesh_pitches, 3),
        "links_vs_mesh": round(sum(r.k for r in s.rings) / mesh_links, 4),
    }


# ---------------------------------------------------------------------------
# 3. Routing-independent lower bounds for the six collectives
# ---------------------------------------------------------------------------
# Each collective is reduced to demands no schedule can dodge:
#
#   cross(a, b)  -- flits that must cross a cut splitting the fabric into a
#                   cores (root's side) and b cores, per direction
#   inject/eject -- flits a single core must push into / pull out of the fabric
#
# T0 = unicast only (the paper's mechanism: no in-network copy, no in-network
# add). T1 = arc multicast + L1 reduction: one crossing can serve everything
# behind the cut, and two flits can merge into one.

def cross_demand(pattern: str, tier: str, a: int, b: int) -> int:
    """Per-direction crossing requirement; `a` holds the root."""
    if pattern in ("alltoall",):
        return a * b
    if pattern in ("allgather", "allreduce"):
        if tier == "T0":
            return a * b
        return max(a, b) if pattern == "allgather" else 1
    if pattern == "gather":
        return b                       # distinct items, no combining possible
    if pattern == "broadcast":
        return b if tier == "T0" else 1
    if pattern == "reduce":
        return b if tier == "T0" else 1
    raise ValueError(pattern)


def core_demand(pattern: str, tier: str) -> tuple[int, int]:
    """(max inject, max eject) over cores, in flits, m=1."""
    k = N - 1
    if pattern == "alltoall":
        return k, k
    if pattern == "allgather":
        return (k, k) if tier == "T0" else (1, k)
    if pattern == "allreduce":
        return (k, k) if tier == "T0" else (1, 1)
    if pattern == "gather":
        return 1, k
    if pattern == "broadcast":
        return (k, 1) if tier == "T0" else (1, 1)
    if pattern == "reduce":
        return (1, k) if tier == "T0" else (1, 1)
    raise ValueError(pattern)


def bounds(s: Scheme, pattern: str, tier: str, *, cu: dict[str, Any],
           min_ports: int, width: int = 1) -> dict[str, Any]:
    """max(cut bound, core-port bound, L1-ramp bound), in cycles."""
    rx, ry = coord(ROOT)
    cut_lb: float = 0
    witness = None
    for axis, lim in (("x", MX), ("y", MY)):
        for row in cu[axis]["per_cut"]:
            k = row["at"]
            lo = (k * MY) if axis == "x" else (k * MX)
            root_lo = (rx < k) if axis == "x" else (ry < k)
            a, b = (lo, N - lo) if root_lo else (N - lo, lo)
            need = cross_demand(pattern, tier, a, b)
            cap = row["cap_per_dir"]
            lb = math.inf if cap == 0 else math.ceil(need / cap)
            if lb > cut_lb:
                cut_lb, witness = lb, (f"{axis}={k}：容量 {cap} flit/cy，"
                                       f"必须过 {need} flit")
    inj, ej = core_demand(pattern, tier)
    port_lb = math.ceil(max(inj, ej) / max(1, min_ports * width))
    ramp_lb = math.ceil(max(inj, ej) / RAMP_BW)
    cands = {"cut": cut_lb, "port": port_lb, "ramp": ramp_lb}
    inf = cut_lb == math.inf
    return {
        "cut_lb": None if inf else int(cut_lb), "cut_witness": witness,
        "port_lb": port_lb, "ramp_lb": ramp_lb,
        "lb": None if inf else int(max(cands.values())),
        "binding": max(cands, key=lambda k: cands[k]),
    }


# ---------------------------------------------------------------------------
# 4. Rank and report
# ---------------------------------------------------------------------------

def analyse(s: Scheme) -> dict[str, Any]:
    pp = s.ports()
    cu = cuts(s)
    mp = min(pp.values())
    row: dict[str, Any] = {
        "key": s.key, "label": s.label, "note": s.note,
        "ports_min": mp, "ports_max": max(pp.values()),
        "ports_ok": max(pp.values()) <= 2,
        "core_rate": min(mp * s.width, RAMP_BW),
        "ramp_match": mp * s.width == RAMP_BW,
        "dims_directly_reachable": sorted(
            {("row" if r.kind == "row" else "col")
             for r in s.rings if any(r.rid in s.attach()[c]
                                     for c in r.nodes)}),
        "structure": metal(s),
        "cuts": {k: {"min_cap_per_dir": v["min_cap_per_dir"], "at": v["at"],
                     "per_cut": v["per_cut"]} for k, v in cu.items()},
        "distance": distances(s),
        "bounds": {f"{p}/{t}": bounds(s, p, t, cu=cu, min_ports=mp,
                                      width=s.width)
                   for p in PATTERNS for t in ("T0", "T1")},
    }
    row["disconnected"] = not row["distance"]["connected"]
    return row


def rank(rows_in: Any) -> list[dict[str, Any]]:
    """Score every scheme against A, apply the physical gates, then rank.

    Four gates are pass/fail because they decide whether the thing can be
    built and clocked at all; everything else is ranking, so nothing is
    disqualified on taste.
    """
    rows = list(rows_in)
    base = next(r for r in rows if r["key"] == "A_full_2port")
    for r in rows:
        r["vs_A"] = {}
        for k, b in r["bounds"].items():
            ab = base["bounds"][k]["lb"]
            r["vs_A"][k] = (None if (b["lb"] is None or not ab)
                            else round(b["lb"] / ab, 3))
        fin = [v for v in r["vs_A"].values() if v is not None]
        r["worst_vs_A"] = max(fin) if fin else None
        r["best_vs_A"] = min(fin) if fin else None
        r["gates"] = {
            "每核 ≤2 口": r["ports_ok"],
            "全连通": not r["disconnected"],
            "环侧速率跟得上 L1 ramp": r["core_rate"] == RAMP_BW,
            "最长单根线 ≤2 pitch": r["structure"]["max_link_pitches"] <= 2,
        }
        r["fails"] = [k for k, v in r["gates"].items() if not v]
        r["rank"] = None
    ok = [r for r in rows if not r["fails"]]
    ok.sort(key=lambda r: (r["worst_vs_A"],
                           r["structure"]["wire_pitches_x_width"],
                           r["structure"]["n_extra_tap_bridges"],
                           r["distance"]["avg_lat_cy"]))
    for i, r in enumerate(ok):
        r["rank"] = i + 1
    return ok


def main() -> None:
    rows = [analyse(s) for s in schemes()]
    ok = rank(rows)
    winner = ok[0]
    payload = {
        "geometry": {"mx": MX, "my": MY, "n": N, "root": ROOT,
                     "pitch_h": H_PITCH, "pitch_v": V_PITCH,
                     "H_hop": 2 * H_PITCH, "V_hop": 2 * V_PITCH,
                     "t_turn": T_TURN,
                     "t_bridge": T_BRIDGE, "ramp_bw": RAMP_BW},
        "patterns": list(PATTERNS),
        "schemes": rows,
        "ranking": [r["key"] for r in ok],
        "recommend": winner["key"],
        "recommend_label": winner["label"],
        "runner_up": ok[1]["key"] if len(ok) > 1 else None,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False))

    print(f"{'scheme':22s} {'rate':>4s} {'conn':>5s} {'dims':>4s} "
          f"{'xcut':>5s} {'ycut':>5s} {'wire':>5s} {'maxW':>4s} "
          f"{'xtap':>4s} {'avgLat':>7s} {'dia':>4s} {'worst/A':>8s}  fails")
    for r in rows:
        print(f"{r['key']:22s} {r['core_rate']:4d} "
              f"{'yes' if not r['disconnected'] else 'NO':>5s} "
              f"{len(r['dims_directly_reachable']):4d} "
              f"{r['cuts']['x']['min_cap_per_dir']:5d} "
              f"{r['cuts']['y']['min_cap_per_dir']:5d} "
              f"{r['structure']['wire_pitches_x_width']:5d} "
              f"{r['structure']['max_link_pitches']:4d} "
              f"{r['structure']['n_extra_tap_bridges']:4d} "
              f"{str(r['distance']['avg_lat_cy']):>7s} "
              f"{str(r['distance']['diameter_cy']):>4s} "
              f"{str(r['worst_vs_A']):>8s}  {','.join(r['fails']) or '-'}")
    print(f"\nrecommend: {winner['key']} — {winner['label']}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
