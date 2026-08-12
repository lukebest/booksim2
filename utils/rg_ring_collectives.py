#!/usr/bin/env python3
"""Collectives on the dimension-sliced 2D bufferless ring, in two capability tiers.

Why two tiers
-------------
The HPCA'22 mechanism this study baselines against (`rg_ring_base`, E-tag/I-tag
+ deflection) is a UNICAST transport. It has no way to make a flit fan out, so
every collective it runs is a pile of point-to-point messages. The tier split
keeps that honest:

    T0  the network moves one flit from one boarding point to one extract
        point. PEs may still store-and-forward and may still accumulate into
        their own L1 -- those are compute-side abilities that no NoC grants or
        withholds -- so T0 already gets multi-phase algorithms and L1 reduction.
    T1  adds ONE network primitive: copy-and-continue. A flit rides an arc and
        every member node on the way takes a copy, so the arc is paid for once
        regardless of how many readers there are.

That is the whole hardware delta under test. It predicts something sharp and
falsifiable: multicast is a FAN-OUT primitive, so it can only help collectives
that fan out. broadcast, allgather and the allgather half of allreduce should
improve; alltoall, gather and reduce should not move at all, because every one
of their flits has exactly one reader. `mcast_applicable` records which side of
that line each (pattern, algo) falls on, and the DSE checks the prediction
rather than assuming it.

Payload convention
------------------
Every node contributes `m` flits. What each node must END with differs per
pattern, and the flit cost of a transfer is NOT the size of its item set:

    alltoall   node s sends a distinct m-flit message to each of 47 peers
    allgather  everyone ends holding all 48 contributions (48*m flits)
    broadcast  root contributes, everyone ends with m flits
    gather     root ends with 48*m flits
    reduce     root ends with m flits -- folding is size-preserving
    allreduce  everyone ends with m flits

So `Xfer.nflit` is carried explicitly. For a forwarded bundle it is
`len(items) * m`; for a reduction it stays `m` however many contributions have
already been folded in. Getting this wrong is the easiest way to make reduce
look like gather, and they are not the same collective.

Data semantics are checked, not assumed: `replay()` walks the phases moving
item sets around and reports whether every node ends up holding what the
pattern requires. A schedule that is D-R legal but delivers the wrong sets is
still wrong, and only the item replay can see that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

from rg_ring_topo import RingId, RingTopology
from rg_topo import coord, nid

Pattern = Literal["alltoall", "allgather", "allreduce", "gather", "broadcast",
                  "reduce"]
Tier = Literal["T0", "T1"]
Algo = Literal["flat", "ring_rotate", "dim_2phase", "halving_doubling"]

PATTERNS: tuple[str, ...] = ("alltoall", "allgather", "allreduce", "gather",
                             "broadcast", "reduce")

# (pattern, algo) combinations that are actually defined. A blank cell is a
# statement, not an omission: e.g. alltoall has no fan-out to exploit, so
# neither rotation nor distance doubling has anything to offer it.
ALGOS: dict[str, tuple[str, ...]] = {
    "alltoall": ("flat", "dim_2phase"),
    "allgather": ("flat", "ring_rotate", "dim_2phase", "halving_doubling"),
    "broadcast": ("flat", "ring_rotate", "dim_2phase", "halving_doubling"),
    "gather": ("flat", "ring_rotate", "dim_2phase"),
    "reduce": ("flat", "ring_rotate", "dim_2phase"),
    "allreduce": ("flat", "ring_rotate", "dim_2phase", "halving_doubling"),
}

FOLDING: frozenset[str] = frozenset({"reduce", "allreduce"})


# ---------------------------------------------------------------------------
# 1. Transfer / phase / collective
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Xfer:
    """One transfer unit = one boarding.

    `ring is None` means "two-phase unicast, let the route planner pick the
    path" (T0 shape, what `schedule_ring` and `ring_base` consume). A set ring
    means "this arc, this direction", which is what a single-ring rotation step
    or a copy-and-continue multicast needs.
    """
    xid: int
    src: int
    dsts: tuple[int, ...]
    items: frozenset[int]
    nflit: int
    op: str = "FWD"                    # FWD | ADD
    ring: RingId | None = None
    direction: int = 0

    @property
    def is_mcast(self) -> bool:
        return len(self.dsts) > 1

    @property
    def pairs(self) -> list[tuple[int, int]]:
        return [(self.src, d) for d in self.dsts]


@dataclass
class Phase:
    name: str
    xfers: list[Xfer]
    barrier: bool = True
    note: str = ""

    @property
    def n_flits(self) -> int:
        return sum(x.nflit for x in self.xfers)

    @property
    def n_mcast(self) -> int:
        return sum(1 for x in self.xfers if x.is_mcast)


@dataclass
class RingCollective:
    pattern: str
    tier: str
    algo: str
    m: int
    n: int
    phases: list[Phase]
    initial: dict[int, frozenset[int]]
    goal: dict[int, frozenset[int]]
    root: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def xfers(self) -> list[Xfer]:
        return [x for p in self.phases for x in p.xfers]

    @property
    def pairs(self) -> list[tuple[int, int]]:
        """Flattened unicast view: what a unicast-only fabric actually carries."""
        return [pr for x in self.xfers for pr in x.pairs]

    @property
    def n_flits(self) -> int:
        return sum(p.n_flits for p in self.phases)

    @property
    def link_flit_hops(self) -> int:
        """Set by the calendar/DSE once routes are known; 0 until then."""
        return 0

    def summary(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern, "tier": self.tier, "algo": self.algo,
            "m": self.m, "root": self.root,
            "n_phases": len(self.phases),
            "n_xfers": len(self.xfers),
            "n_mcast_xfers": sum(p.n_mcast for p in self.phases),
            "n_unicast_deliveries": len(self.pairs),
            "n_flits_boarded": self.n_flits,
            "phases": [{"name": p.name, "n_xfers": len(p.xfers),
                        "n_mcast": p.n_mcast, "n_flits": p.n_flits,
                        "barrier": p.barrier, "note": p.note}
                       for p in self.phases],
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# 2. Item-set replay: does the schedule actually deliver the collective?
# ---------------------------------------------------------------------------

def replay(col: RingCollective) -> dict[str, Any]:
    """Move item sets phase by phase and check the goal.

    Two failure modes are distinguished because they have different causes:
    `missing` means a node never received something it needs (the algorithm is
    incomplete), `unsourced` means a transfer claimed to carry an item its
    source did not hold yet (the phase order is wrong).
    """
    holds = {k: set(v) for k, v in col.initial.items()}
    unsourced: list[dict[str, Any]] = []
    for pi, ph in enumerate(col.phases):
        arriving: dict[int, set[int]] = {}
        for x in ph.xfers:
            if not x.items <= holds.get(x.src, set()):
                unsourced.append({
                    "phase": pi, "name": ph.name, "xid": x.xid, "src": x.src,
                    "want": sorted(x.items)[:6],
                    "missing": sorted(x.items - holds.get(x.src, set()))[:6]})
            for d in x.dsts:
                arriving.setdefault(d, set()).update(x.items)
        for d, s in arriving.items():
            holds.setdefault(d, set()).update(s)
    missing = {k: sorted(v - holds.get(k, set()))[:6]
               for k, v in col.goal.items() if not v <= holds.get(k, set())}
    return {
        "ok": not missing and not unsourced,
        "n_nodes_short": len(missing),
        "missing_examples": dict(list(missing.items())[:4]),
        "n_unsourced": len(unsourced),
        "unsourced_examples": unsourced[:4],
        "held_min": min(len(v) for v in holds.values()) if holds else 0,
        "held_max": max(len(v) for v in holds.values()) if holds else 0,
    }


# ---------------------------------------------------------------------------
# 3. Structure helpers
# ---------------------------------------------------------------------------

def hamilton_cycle(topo: RingTopology) -> list[int]:
    """Boustrophedon Hamiltonian cycle using only ring segments.

    Rows are walked alternately left-right / right-left so consecutive rows
    join with a single column segment, and the cycle closes on the column-0
    WRAP segment -- a link the mesh does not have. That closure is the reason a
    ring can run a rotation collective at all: on a mesh the same walk is a
    path, and a path cannot rotate without one node sending backwards across
    the whole array.
    """
    if topo.my % 2:
        raise ValueError("boustrophedon closure needs an even row count")
    seq: list[int] = []
    for y in range(topo.my):
        xs = range(topo.mx) if y % 2 == 0 else range(topo.mx - 1, -1, -1)
        seq += [nid(x, y, topo.mx) for x in xs]
    return seq


def ring_of(topo: RingTopology, kind: str, node: int) -> RingId:
    x, y = coord(node, topo.mx)
    return ("row", y) if kind == "row" else ("col", x)


def _succ_dir(topo: RingTopology, ring: RingId, a: int, b: int) -> int:
    """Direction of the single segment a->b on `ring` (raises if not adjacent)."""
    k = topo.ring_size(ring)
    i, j = topo.index_on(ring, a), topo.index_on(ring, b)
    if (j - i) % k == 1:
        return 1
    if (i - j) % k == 1:
        return -1
    raise ValueError(f"{a}->{b} is not one segment on {ring}")


def _neighbour_ring(topo: RingTopology, a: int, b: int) -> RingId:
    ax, ay = coord(a, topo.mx)
    bx, by = coord(b, topo.mx)
    return ("row", ay) if ay == by else ("col", ax)


class _Ids:
    def __init__(self) -> None:
        self.i = 0

    def next(self) -> int:
        self.i += 1
        return self.i - 1


# ---------------------------------------------------------------------------
# 4. Builders, one per (pattern, algo)
# ---------------------------------------------------------------------------

def _fanout_xfers(topo: RingTopology, ids: _Ids, ring: RingId, src: int,
                  members: Sequence[int], items: frozenset[int], nflit: int,
                  tier: str, op: str = "FWD", *, bidir: bool = True
                  ) -> list[Xfer]:
    """One-to-many on a single ring, as multicast (T1) or unicast fan-out (T0).

    T1 emits at most two transfers (one per direction) whatever the member
    count; T0 emits one per member. The ratio between those two counts is the
    hardware delta this study is measuring, so both go through this one place.
    """
    if not members:
        return []
    if tier == "T1":
        return [Xfer(ids.next(), src, tuple(ms), items, nflit, op, ring, d)
                for d, ms in topo.mcast_cover(ring, src, members, bidir=bidir)]
    out: list[Xfer] = []
    for d in members:
        if d == src:
            continue
        out.append(Xfer(ids.next(), src, (d,), items, nflit, op, ring,
                        _dir_to(topo, ring, src, d, bidir)))
    return out


def _dir_to(topo: RingTopology, ring: RingId, a: int, b: int, bidir: bool
            ) -> int:
    """Shortest direction, or forced clockwise when the lever is off."""
    return topo.hop_options(ring, a, b)[0] if bidir else 1


def _rotate_phases(topo: RingTopology, ids: _Ids, cycle: list[int],
                   contrib: dict[int, frozenset[int]], m: int, *,
                   steps: int, fold: bool, name: str) -> list[Phase]:
    """`steps` nearest-neighbour rotation steps along a closed cycle.

    Every node ships one payload to its successor each step, so a rotation step
    uses each of the cycle's segments exactly once: the arc load is 1 per step
    no matter how big the array is. That is the property the calendar wants and
    the reason rotation shows up in every ring collective.

    `fold=True` accumulates in L1 (payload stays m flits); otherwise the
    payload is whatever set arrived last step (payload grows).
    """
    k = len(cycle)
    pos = {n: i for i, n in enumerate(cycle)}
    carry = {n: contrib[n] for n in cycle}
    phases: list[Phase] = []
    for step in range(steps):
        xf: list[Xfer] = []
        nxt: dict[int, frozenset[int]] = {}
        for n in cycle:
            succ = cycle[(pos[n] + 1) % k]
            ring = _neighbour_ring(topo, n, succ)
            direction = _succ_dir(topo, ring, n, succ)
            payload = carry[n]
            nflit = m if fold else len(payload) * m
            xf.append(Xfer(ids.next(), n, (succ,), payload, nflit,
                           "ADD" if fold else "FWD", ring, direction))
            nxt[succ] = payload
        phases.append(Phase(f"{name}[{step}]", xf, barrier=True,
                            note="one segment per node: arc load 1"))
        if fold:
            carry = {n: carry[n] | nxt[n] for n in cycle}
        else:
            carry = nxt
    return phases


def _dim_members(topo: RingTopology, kind: str, node: int) -> list[int]:
    return [n for n in topo.ring_nodes(ring_of(topo, kind, node)) if n != node]


def _build_alltoall(topo: RingTopology, m: int, tier: str, algo: str, *,
                    bidir: bool = True) -> RingCollective:
    ids = _Ids()
    n, mx = topo.n, topo.mx
    item = lambda s, d: s * n + d          # noqa: E731 - message identity
    initial = {s: frozenset(item(s, d) for d in range(n) if d != s)
               for s in range(n)}
    goal = {d: frozenset(item(s, d) for s in range(n) if s != d)
            for d in range(n)}
    notes: list[str] = []
    if algo == "flat":
        xf = [Xfer(ids.next(), s, (d,), frozenset({item(s, d)}), m)
              for s in range(n) for d in range(n) if d != s]
        phases = [Phase("direct", xf, barrier=True,
                        note="every flit has exactly one reader")]
        notes.append("multicast cannot help: all N(N-1) messages are distinct")
    else:
        # Bundle by destination COLUMN on the row ring, then spread down the
        # column. Halves the hop count against a flat XY unicast and lets one
        # boarding carry six messages, but it needs the intermediate PE to
        # store and forward, which is a T0-legal L1 use.
        ph1: list[Xfer] = []
        for s in range(n):
            sx, sy = coord(s, mx)
            for cx in range(mx):
                if cx == sx:
                    continue
                bundle = frozenset(item(s, nid(cx, dy, mx))
                                   for dy in range(topo.my))
                relay = nid(cx, sy, mx)
                ph1.append(Xfer(ids.next(), s, (relay,), bundle,
                                len(bundle) * m, "FWD", ("row", sy),
                                _dir_to(topo, ("row", sy), s, relay, bidir)))
        ph2: list[Xfer] = []
        for relay in range(n):
            rx, ry = coord(relay, mx)
            for dy in range(topo.my):
                d = nid(rx, dy, mx)
                if d == relay:
                    continue
                carried = frozenset(item(s, d) for s in range(n)
                                    if coord(s, mx)[1] == ry and s != d)
                if not carried:
                    continue
                ph2.append(Xfer(ids.next(), relay, (d,), carried,
                                len(carried) * m, "FWD", ("col", rx),
                                _dir_to(topo, ("col", rx), relay, d, bidir)))
        phases = [Phase("row-bundle", ph1, barrier=True,
                        note="one boarding carries the whole destination column"),
                  Phase("col-spread", ph2, barrier=True)]
        notes.append("intermediate PE stores and forwards in L1 (T0-legal)")
    return RingCollective("alltoall", tier, algo, m, n, phases, initial, goal,
                          notes=notes)


def _build_allgather(topo: RingTopology, m: int, tier: str, algo: str, *,
                     bidir: bool = True) -> RingCollective:
    ids = _Ids()
    n = topo.n
    initial = {s: frozenset({s}) for s in range(n)}
    goal = {d: frozenset(range(n)) for d in range(n)}
    notes: list[str] = []
    if algo == "flat":
        xf = [Xfer(ids.next(), s, (d,), frozenset({s}), m)
              for s in range(n) for d in range(n) if d != s]
        phases = [Phase("direct", xf, barrier=True)]
        notes.append("same flow set as flat alltoall: N(N-1) one-flit messages")
    elif algo == "ring_rotate":
        cyc = hamilton_cycle(topo)
        phases = _rotate_phases(topo, ids, cyc, initial, m, steps=n - 1,
                               fold=False, name="rotate")
        notes.append(f"{n - 1} nearest-neighbour steps on a Hamiltonian cycle")
    elif algo == "dim_2phase":
        row: list[Xfer] = []
        for s in range(n):
            row += _fanout_xfers(topo, ids, ring_of(topo, "row", s), s,
                                 _dim_members(topo, "row", s),
                                 frozenset({s}), m, tier, bidir=bidir)
        col: list[Xfer] = []
        for s in range(n):
            rnodes = topo.ring_nodes(ring_of(topo, "row", s))
            bundle = frozenset(rnodes)
            col += _fanout_xfers(topo, ids, ring_of(topo, "col", s), s,
                                 _dim_members(topo, "col", s), bundle,
                                 len(bundle) * m, tier, bidir=bidir)
        phases = [Phase("row-allgather", row, barrier=True),
                  Phase("col-allgather", col, barrier=True,
                        note="payload is the whole row bundle")]
        notes.append("column phase carries mx-times the payload of the row phase")
    else:  # halving_doubling
        phases = []
        held = {s: frozenset({s}) for s in range(n)}
        for kind in ("row", "col"):
            k = topo.mx if kind == "row" else topo.my
            step = 1
            si = 0
            while step < k:
                xf: list[Xfer] = []
                nxt: dict[int, frozenset[int]] = {}
                for s in range(n):
                    ring = ring_of(topo, kind, s)
                    nodes = topo.ring_nodes(ring)
                    i = topo.index_on(ring, s)
                    partner = nodes[(i + step) % k]
                    xf.append(Xfer(ids.next(), s, (partner,), held[s],
                                   len(held[s]) * m, "FWD", ring,
                                   _dir_to(topo, ring, s, partner, bidir)))
                    nxt[partner] = nxt.get(partner, frozenset()) | held[s]
                phases.append(Phase(f"{kind}-double[{si}]", xf, barrier=True))
                held = {s: held[s] | nxt.get(s, frozenset()) for s in range(n)}
                step *= 2
                si += 1
            notes.append(f"{kind} dimension k={k}: "
                         f"{'power of two, exact doubling' if k & (k - 1) == 0 else 'NOT a power of two, doubling overshoots and duplicates'}")
        phases = phases
    return RingCollective("allgather", tier, algo, m, n, phases, initial, goal,
                          notes=notes)


def _build_broadcast(topo: RingTopology, m: int, tier: str, algo: str, *,
                     root: int, bidir: bool = True) -> RingCollective:
    ids = _Ids()
    n = topo.n
    initial = {s: (frozenset({root}) if s == root else frozenset())
               for s in range(n)}
    goal = {d: frozenset({root}) for d in range(n)}
    payload = frozenset({root})
    notes: list[str] = []
    if algo == "flat":
        xf = [Xfer(ids.next(), root, (d,), payload, m)
              for d in range(n) if d != root]
        phases = [Phase("direct", xf, barrier=True)]
        notes.append("root boards N-1 times; its insert point is the bottleneck")
    elif algo == "ring_rotate":
        cyc = hamilton_cycle(topo)
        pos = {v: i for i, v in enumerate(cyc)}
        order = cyc[pos[root]:] + cyc[:pos[root]]
        phases = []
        for i in range(n - 1):
            a, b = order[i], order[i + 1]
            ring = _neighbour_ring(topo, a, b)
            phases.append(Phase(f"chain[{i}]", [
                Xfer(ids.next(), a, (b,), payload, m, "FWD", ring,
                     _succ_dir(topo, ring, a, b))], barrier=True))
        notes.append("store-and-forward chain: minimal traffic, worst latency")
    elif algo == "dim_2phase":
        col = _fanout_xfers(topo, ids, ring_of(topo, "col", root), root,
                            _dim_members(topo, "col", root), payload, m, tier,
                            bidir=bidir)
        rootx = coord(root, topo.mx)[0]
        row: list[Xfer] = []
        for y in range(topo.my):
            s = nid(rootx, y, topo.mx)
            row += _fanout_xfers(topo, ids, ring_of(topo, "row", s), s,
                                 _dim_members(topo, "row", s), payload, m,
                                 tier, bidir=bidir)
        phases = [Phase("col-spread", col, barrier=True),
                  Phase("row-spread", row, barrier=True)]
    else:  # halving_doubling
        phases = []
        have = {root}
        step = 0
        while len(have) < n:
            xf: list[Xfer] = []
            new: set[int] = set()
            for s in sorted(have):
                for kind in ("row", "col"):
                    ring = ring_of(topo, kind, s)
                    for d in topo.ring_nodes(ring):
                        if d in have or d in new:
                            continue
                        xf.append(Xfer(ids.next(), s, (d,), payload, m, "FWD",
                                       ring,
                                       _dir_to(topo, ring, s, d, bidir)))
                        new.add(d)
                        break
                    if new & set(topo.ring_nodes(ring)):
                        break
            if not xf:
                break
            phases.append(Phase(f"double[{step}]", xf, barrier=True))
            have |= new
            step += 1
        notes.append("holders double each step; each step is one segment per holder")
    return RingCollective("broadcast", tier, algo, m, n, phases, initial, goal,
                          root=root, notes=notes)


def _build_gather_like(topo: RingTopology, m: int, tier: str, algo: str, *,
                       root: int, fold: bool, bidir: bool = True
                       ) -> RingCollective:
    """gather and reduce share their traffic SHAPE and differ only in size.

    Both move every node's contribution to the root. The difference is that a
    reduction folds: a partial sum is m flits however many contributions are in
    it, so an intermediate node re-boards m flits where a gather would have to
    re-board everything it holds. That single difference is what collapses the
    root's ejection load from (N-1)*m flits to (ring degree)*m, and it is the
    reason reduce and gather must not be reported as one number.
    """
    ids = _Ids()
    n = topo.n
    pattern = "reduce" if fold else "gather"
    initial = {s: frozenset({s}) for s in range(n)}
    goal = {root: frozenset(range(n))}
    op = "ADD" if fold else "FWD"
    notes: list[str] = []
    if algo == "flat":
        xf = [Xfer(ids.next(), s, (root,), frozenset({s}), m, op)
              for s in range(n) if s != root]
        phases = [Phase("direct", xf, barrier=True)]
        notes.append("root ejects (N-1)*m flits whether or not it folds them")
    elif algo == "ring_rotate":
        cyc = hamilton_cycle(topo)
        pos = {v: i for i, v in enumerate(cyc)}
        order = cyc[pos[root]:] + cyc[:pos[root]]
        chain = list(reversed(order[1:])) + [root]
        phases = []
        held = {s: frozenset({s}) for s in range(n)}
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            ring = _neighbour_ring(topo, a, b)
            payload = held[a]
            phases.append(Phase(f"chain[{i}]", [
                Xfer(ids.next(), a, (b,), payload, m if fold else
                     len(payload) * m, op, ring,
                     _succ_dir(topo, ring, a, b))], barrier=True))
            held[b] = held[b] | payload
        notes.append("fully serial: one segment busy at a time")
    else:  # dim_2phase
        rootx, rooty = coord(root, topo.mx)
        ph1: list[Xfer] = []
        for y in range(topo.my):
            sink = nid(rootx, y, topo.mx)
            ring = ring_of(topo, "row", sink)
            for s in topo.ring_nodes(ring):
                if s == sink:
                    continue
                ph1.append(Xfer(ids.next(), s, (sink,), frozenset({s}), m, op,
                                ring, _dir_to(topo, ring, s, sink, bidir)))
        ph2: list[Xfer] = []
        colring = ring_of(topo, "col", root)
        for s in topo.ring_nodes(colring):
            if s == root:
                continue
            held = frozenset(topo.ring_nodes(ring_of(topo, "row", s)))
            ph2.append(Xfer(ids.next(), s, (root,), held,
                            m if fold else len(held) * m, op, colring,
                            _dir_to(topo, colring, s, root, bidir)))
        phases = [Phase("row-collect", ph1, barrier=True),
                  Phase("col-collect", ph2, barrier=True)]
        notes.append("root ejects (my-1) payloads, not N-1: "
                     + ("folding makes each one m flits"
                        if fold else "but each carries a whole row"))
    return RingCollective(pattern, tier, algo, m, n, phases, initial, goal,
                          root=root, notes=notes)


def _build_allreduce(topo: RingTopology, m: int, tier: str, algo: str, *,
                     root: int, bidir: bool = True) -> RingCollective:
    n = topo.n
    initial = {s: frozenset({s}) for s in range(n)}
    goal = {d: frozenset(range(n)) for d in range(n)}
    notes: list[str] = []
    if algo in ("flat", "dim_2phase"):
        red = _build_gather_like(topo, m, tier, algo, root=root, fold=True,
                                 bidir=bidir)
        bc = _build_broadcast(topo, m, tier, algo, root=root, bidir=bidir)
        off = max((x.xid for x in red.xfers), default=-1) + 1
        phases = list(red.phases)
        for ph in bc.phases:
            phases.append(Phase(
                "bcast:" + ph.name,
                [Xfer(x.xid + off, x.src, x.dsts, frozenset(range(n)),
                      x.nflit, x.op, x.ring, x.direction) for x in ph.xfers],
                barrier=True, note=ph.note))
        notes.append("reduce then broadcast, hard barrier between")
        notes += red.notes + bc.notes
    elif algo == "ring_rotate":
        # Ring reduce-scatter + allgather. Chunking is by cycle length, and at
        # m < N there is nothing to scatter, so this degenerates to a serial
        # reduce followed by a rotation allgather. Reporting `n_chunks` keeps
        # that visible instead of pretending RS+AG is available at m=1.
        ids = _Ids()
        cyc = hamilton_cycle(topo)
        n_chunks = min(len(cyc), max(1, m))
        chunk = max(1, m // n_chunks) if n_chunks else m
        rs = _rotate_phases(topo, ids, cyc, initial, chunk,
                            steps=n - 1, fold=True, name="reduce-scatter")
        holds = {s: frozenset(range(n)) for s in range(n)}
        ag = _rotate_phases(topo, ids, cyc, holds, chunk,
                            steps=n - 1, fold=False, name="allgather")
        ag = [Phase(p.name, [Xfer(x.xid, x.src, x.dsts, x.items, chunk,
                                  "FWD", x.ring, x.direction)
                             for x in p.xfers], p.barrier, p.note)
              for p in ag]
        phases = rs + ag
        notes.append(f"n_chunks={n_chunks} at m={m}: "
                     + ("true RS+AG" if n_chunks > 1 else
                        "m < ring length, nothing to scatter -- degenerates to "
                        "serial reduce + rotation allgather"))
    else:  # halving_doubling
        ids = _Ids()
        phases = []
        held = {s: frozenset({s}) for s in range(n)}
        for kind in ("row", "col"):
            k = topo.mx if kind == "row" else topo.my
            step, si = 1, 0
            while step < k:
                xf: list[Xfer] = []
                nxt: dict[int, frozenset[int]] = {}
                for s in range(n):
                    ring = ring_of(topo, kind, s)
                    nodes = topo.ring_nodes(ring)
                    i = topo.index_on(ring, s)
                    partner = nodes[(i + step) % k]
                    xf.append(Xfer(ids.next(), s, (partner,), held[s], m,
                                   "ADD", ring,
                                   _dir_to(topo, ring, s, partner, bidir)))
                    nxt[partner] = nxt.get(partner, frozenset()) | held[s]
                phases.append(Phase(f"{kind}-fold[{si}]", xf, barrier=True))
                held = {s: held[s] | nxt.get(s, frozenset()) for s in range(n)}
                step *= 2
                si += 1
            notes.append(f"{kind} k={k}: "
                         + ("exact" if k & (k - 1) == 0
                            else "not a power of two, partners overlap"))
        notes.append("payload stays m flits every step: folding is size-preserving")
    return RingCollective("allreduce", tier, algo, m, n, phases, initial, goal,
                          root=root, notes=notes)


# ---------------------------------------------------------------------------
# 5. Dispatcher
# ---------------------------------------------------------------------------

def build_ring_collective(topo: RingTopology, pattern: str, *, m: int = 1,
                          tier: str = "T0", algo: str = "flat",
                          root: int | None = None, bidir: bool = True
                          ) -> RingCollective:
    if pattern not in PATTERNS:
        raise ValueError(f"unknown pattern {pattern}")
    if algo not in ALGOS[pattern]:
        raise ValueError(f"{pattern} has no {algo} algorithm; "
                         f"defined: {ALGOS[pattern]}")
    if tier not in ("T0", "T1"):
        raise ValueError(tier)
    r = topo.n // 2 if root is None else root
    if pattern == "alltoall":
        return _build_alltoall(topo, m, tier, algo, bidir=bidir)
    if pattern == "allgather":
        return _build_allgather(topo, m, tier, algo, bidir=bidir)
    if pattern == "broadcast":
        return _build_broadcast(topo, m, tier, algo, root=r, bidir=bidir)
    if pattern == "gather":
        return _build_gather_like(topo, m, tier, algo, root=r, fold=False,
                                  bidir=bidir)
    if pattern == "reduce":
        return _build_gather_like(topo, m, tier, algo, root=r, fold=True,
                                  bidir=bidir)
    return _build_allreduce(topo, m, tier, algo, root=r, bidir=bidir)


def multiround(col: RingCollective, rounds: int) -> RingCollective:
    """R pipelined instances of the same collective, free to overlap.

    Round r's phase p goes into phase p alongside every other round's phase p,
    so the only ordering left is the intra-round data dependency. Rounds are NOT
    barriered against each other, which is what makes the pack "free" in the
    same sense as the mesh side's multi-round packer: round 2 may fill slack
    slots round 1 left behind, and that is exactly what II_eff is measuring.

    Item sets are shared across rounds on purpose. The replay check is about
    which nodes receive which contributions, and repeating a collective does not
    change that; what changes is only occupancy, which is what is being packed.
    """
    if rounds < 1:
        raise ValueError(rounds)
    if rounds == 1:
        return col
    span = max((x.xid for x in col.xfers), default=0) + 1
    phases: list[Phase] = []
    for ph in col.phases:
        xf: list[Xfer] = []
        for r in range(rounds):
            for x in ph.xfers:
                xf.append(Xfer(x.xid + r * span, x.src, x.dsts, x.items,
                               x.nflit, x.op, x.ring, x.direction))
        phases.append(Phase(ph.name, xf, ph.barrier,
                            f"{ph.note} x{rounds} rounds".strip()))
    return RingCollective(col.pattern, col.tier, col.algo, col.m, col.n,
                          phases, col.initial, col.goal, col.root,
                          col.notes + [f"{rounds} pipelined rounds"])


def mcast_applicable(pattern: str, algo: str) -> bool:
    """Does copy-and-continue change this (pattern, algo) at all?

    True only where a transfer has more than one reader. `flat` and
    `ring_rotate` are unicast-shaped by construction, and alltoall / gather /
    reduce have no duplicated payload at any granularity, so for them T1 is
    bit-identical to T0 -- which is the prediction the DSE checks.
    """
    if algo in ("flat", "ring_rotate", "halving_doubling"):
        return False
    return pattern in ("allgather", "broadcast", "allreduce")


def all_configs(patterns: Iterable[str] = PATTERNS) -> list[tuple[str, str, str]]:
    """(pattern, algo, tier) triples worth running: T1 only where it differs."""
    out: list[tuple[str, str, str]] = []
    for p in patterns:
        for a in ALGOS[p]:
            out.append((p, a, "T0"))
            if mcast_applicable(p, a):
                out.append((p, a, "T1"))
    return out


if __name__ == "__main__":
    import json

    topo = RingTopology()
    cyc = hamilton_cycle(topo)
    print(f"Hamiltonian cycle: {len(cyc)} nodes, closes "
          f"{cyc[-1]}->{cyc[0]} on {_neighbour_ring(topo, cyc[-1], cyc[0])}")
    assert len(set(cyc)) == topo.n
    for i in range(len(cyc)):
        a, b = cyc[i], cyc[(i + 1) % len(cyc)]
        _succ_dir(topo, _neighbour_ring(topo, a, b), a, b)
    print("  every consecutive pair is one ring segment: OK")

    print(f"\n{'pattern':10} {'algo':18} {'tier':5} {'ph':>3} {'xfers':>6} "
          f"{'mcast':>6} {'deliv':>6} {'flits':>7} {'replay':>7}")
    bad = 0
    for pat, algo, tier in all_configs():
        col = build_ring_collective(topo, pat, m=1, tier=tier, algo=algo)
        rp = replay(col)
        if not rp["ok"]:
            bad += 1
        print(f"{pat:10} {algo:18} {tier:5} {len(col.phases):>3} "
              f"{len(col.xfers):>6} "
              f"{sum(p.n_mcast for p in col.phases):>6} "
              f"{len(col.pairs):>6} {col.n_flits:>7} "
              f"{'ok' if rp['ok'] else 'FAIL':>7}")
        if not rp["ok"]:
            print(f"    {json.dumps(rp)}")
    print(f"\nreplay failures: {bad}")
    assert bad == 0, "a collective does not deliver its own goal"
