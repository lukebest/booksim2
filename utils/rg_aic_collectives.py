"""Collectives on the AIC reticle fabric: bounds, static calendars, baseline.

Everything here sits on top of `rg_aic_reticle`, so the wire costs and routing
rules are the reference model's, not this file's. Three layers:

1. **Footprints.** A transfer's occupancy falls out of its reference route:
   walk the edge list, accumulate cycles, and you know exactly which lane is
   busy when. The resource set is the 960 *inter-station* directed segments,
   and that is exact rather than approximate: in the reference graph every RBRG
   in-port is fed by exactly one inter-station segment and every out-port feeds
   exactly one, so per-segment exclusion already serialises the 3-way
   straight/near/far divergence inside a station. Nothing is double-counted and
   nothing is missed. `verify_aic_reticle_8x6.py` asserts this.

2. **Lower bounds.** Cut, port, turn and latency floors are
   routing-independent, so they hold for *any* algorithm, including ones that
   relay through intermediate cores. What the reference's own deterministic
   router happens to demand per lane is reported beside them as a separate,
   routing-dependent number -- informative, but not a bound.

3. **Schedules.** A rigid-footprint packer (zero slack, exclusive lanes) for
   static calendars, and an online no-lookahead injector for the unscheduled
   baseline.

Three properties of this fabric drive every result, and all three differ from
the abstract folded torus the earlier ring study assumed:

  * **Rows are rings, columns are lines.** A row is a genuine folded ring of 8
    cores (tour 216 cycles: six 24-cycle links, two 36-cycle fold links). The
    vertical rails only *turn around* at their ends -- a fold rejoins the same
    rail, not a different one -- so vertically the machine is a 6-level
    bidirectional line with no wraparound: row 0 to row 5 costs 111 cycles, not
    a cheap wrap. Ring rotation is therefore a row algorithm only.
  * **A core has no vertical port.** It attaches to one horizontal rail, at
    `M(2*row + col%2, col)`. Every cross-row transfer turns exactly twice
    (H->V->H) at 10 cycles a turn, and relaying through a core's L1 cannot
    avoid it, because the relay core has no vertical port either.
  * **Arc multicast is row-only.** A rail's copy-and-continue works because the
    rail passes through core stations. A vertical rail passes through no core
    station at all, so a flit riding it cannot drop a copy at an intermediate
    row without turning off. `rbrg_fork` prices the extra hardware that would
    allow it rather than assuming it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from rg_aic_reticle import (CORE_X, CYC_TURN, EDGE, N_COLS, N_CORES, N_ROWS,
                            PITCH_Y, Fabric, Route, all_routes, ring_order)

# L1 turnaround for a relay: eject into the core, re-inject on the next leg.
# The reference model has no L1, so this stays an explicit knob rather than
# being smuggled into a route cost.
RAMP = 2
# Directed lanes a core can drive at once. The reference gives its CS an E lane
# and a W lane and lets a route start on either, so 2 is the natural reading;
# the DSE sweeps 1 because the drawing shows a single 105 um core<->CS wire and
# that reading is also defensible.
CORE_LANES = 2
# One lap of a row ring, used as the deflection penalty in the baseline.
ROW_TOUR = 216
# Inter-station directed segments -- the schedulable lane resources.
N_LANES = 960
# Patterns where copy-and-continue changes the traffic a schedule must move.
T1_PATTERNS: tuple[str, ...] = ("broadcast", "allgather", "allreduce")

Tier = Literal["T0", "T1"]
Pattern = Literal["allgather", "allreduce", "alltoall", "gather",
                  "broadcast", "reduce"]
PATTERNS: tuple[Pattern, ...] = ("allgather", "allreduce", "alltoall",
                                 "gather", "broadcast", "reduce")

# Segment kinds that are real inter-station wires: the schedulable resources.
LANE_KINDS = frozenset({"harm", "gap", "vspan", "hfold", "vfold"})


# ---------------------------------------------------------------------------
# 1. Footprints
# ---------------------------------------------------------------------------

@dataclass
class Step:
    """One boarding: a reference route plus every core that reads the payload.

    `readers` is the destination for a unicast, and every core whose CS the arc
    passes for a row multicast -- that is the copy-and-continue primitive.
    `lanes` and `turns` carry head offsets from the boarding cycle, so the
    footprint is rigid: no slack anywhere.
    """
    src: int
    dst: int
    readers: tuple[int, ...]
    lanes: tuple[tuple[str, int], ...]
    turns: tuple[tuple[tuple[int, int], str, int], ...]
    arrive: dict[int, int]
    wire: int
    n_turns: int
    um: int
    kind: str = "unicast"

    @property
    def last(self) -> int:
        return max(self.arrive.values())


def _walk(r: Route) -> tuple[list[tuple[str, int]],
                             list[tuple[tuple[int, int], str, int]],
                             dict[tuple[int, int], int]]:
    """Head offsets for every lane, turn and middle-station pass on a route."""
    lanes: list[tuple[str, int]] = []
    turns: list[tuple[tuple[int, int], str, int]] = []
    passes: dict[tuple[int, int], int] = {}
    t = 0
    for e in r.edges:
        if e.kind in LANE_KINDS:
            lanes.append((e.eid, t))
        elif e.turn:
            turns.append(((e.hi or 0, e.vi or 0), e.kind, t))
        elif e.kind in ("cs", "pipe") and e.col is not None:
            passes[(e.hi or 0, e.col)] = t
        t += e.cyc
    return lanes, turns, passes


_STEP_CACHE: dict[tuple[int, int], Step] = {}


def unicast_step(rs: dict[tuple[int, int], Route], src: int, dst: int) -> Step:
    got = _STEP_CACHE.get((src, dst))
    if got is not None:
        return got
    r = rs[(src, dst)]
    lanes, turns, _ = _walk(r)
    st = Step(src=src, dst=dst, readers=(dst,), lanes=tuple(lanes),
              turns=tuple(turns), arrive={dst: r.total}, wire=r.total,
              n_turns=r.turns, um=r.um)
    _STEP_CACHE[(src, dst)] = st
    return st


def _arc_cover(rs: dict[tuple[int, int], Route], src: int, far: int,
               want: set[int]) -> tuple[Step, set[int]] | None:
    """Build a multicast Step on the shortest arc src->far; report who it hits.

    A member is covered only if its own CS actually appears on that route, so
    this cannot silently claim a reader the arc never passes.
    """
    if far == src:
        return None
    r = rs[(src, far)]
    lanes, turns, passes = _walk(r)
    hit: dict[int, int] = {far: r.total}
    for d in want:
        if d == far:
            continue
        key = (Fabric.core_rail(d), d % N_COLS)
        if key in passes:
            hit[d] = passes[key] + 1     # + the 105 um CS access
    step = Step(src=src, dst=far, readers=tuple(sorted(hit)),
                lanes=tuple(lanes), turns=tuple(turns), arrive=hit,
                wire=r.total, n_turns=r.turns, um=r.um, kind="mcast")
    return step, set(hit)


def row_multicast_steps(rs: dict[tuple[int, int], Route], src: int,
                        dsts: Sequence[int]) -> list[Step]:
    """Cover `dsts` (all in src's row) with as few rail arcs as possible.

    Two arcs is the natural answer, one per direction, because the core has an
    E lane and a W lane. The greedy below tries the farthest member first and
    verifies coverage, falling back to shorter arcs and finally to unicast, so
    a tie-broken route that does not pass an intended member degrades honestly
    instead of dropping a reader.
    """
    row = src // N_COLS
    if any(d // N_COLS != row for d in dsts):
        raise ValueError("row multicast needs a single row")
    tour = ring_order()
    k = len(tour)
    pos = {c: i for i, c in enumerate(tour)}
    sc = src % N_COLS
    todo = set(dsts)
    out: list[Step] = []
    for direction in (1, -1):
        mine = {d for d in todo
                if 0 < ((pos[d % N_COLS] - pos[sc]) % k if direction > 0
                        else (pos[sc] - pos[d % N_COLS]) % k) <= k // 2}
        while mine:
            reach = max(((pos[d % N_COLS] - pos[sc]) % k if direction > 0
                         else (pos[sc] - pos[d % N_COLS]) % k) for d in mine)
            got = None
            while reach > 0:
                far = row * N_COLS + tour[(pos[sc] + direction * reach) % k]
                cand = _arc_cover(rs, src, far, mine)
                if cand and len(cand[1]) > 1:
                    got = cand
                    break
                if cand and len(cand[1]) == 1 and reach == 1:
                    got = cand
                    break
                reach -= 1
            if got is None:
                break
            out.append(got[0])
            mine -= got[1]
            todo -= got[1]
    for d in sorted(todo):                       # anything left goes unicast
        out.append(unicast_step(rs, src, d))
    return out


# ---------------------------------------------------------------------------
# 2. Rigid-footprint packer
# ---------------------------------------------------------------------------

def _spread(mask: int, d: int) -> int:
    """Bit i set iff `mask` has any bit in [i, i+d). Window of exactly d."""
    if d <= 1 or not mask:
        return mask
    out = mask
    done = 1
    while done < d:
        take = min(done, d - done)
        out |= out >> take
        done += take
    return out


def _first_zero(mask: int, t: int) -> int:
    """Lowest position >= t whose bit in `mask` is 0, in O(1) big-int ops.

    The obvious `while (mask >> t) & 1: t += 1` costs a full-width shift per
    candidate cycle, which is quadratic in the makespan and is why packing a
    2256-boarding calendar did not finish.
    """
    x = mask >> t
    if x == 0:
        return t
    inv = x ^ ((1 << (x.bit_length() + 1)) - 1)
    return t + ((inv & -inv).bit_length() - 1)


class Packer:
    """Earliest-feasible-start list scheduler over exclusive lane windows.

    Footprints are rigid: a step's lanes are busy at fixed offsets from its
    boarding cycle, zero slack. That is what makes the output a *static*
    calendar -- no run-time arbitration is implied at any point.

    Feasibility is folded into a single forbidden-start bitmask so the search
    is one scan rather than a retry loop: for a lane at offset `off`, start `t`
    conflicts iff `spread(occ, d)` has bit `t+off`; for a core port, `t` is
    forbidden only if *every* one of its lanes is busy.
    """

    def __init__(self, *, core_lanes: int = CORE_LANES):
        self.core_lanes = core_lanes
        self.lane: dict[str, int] = defaultdict(int)
        self.inj: list[int] = [0] * (N_CORES * core_lanes)
        self.ej: list[int] = [0] * (N_CORES * core_lanes)
        self.turn: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        self.placed: list[tuple[Step, int, int]] = []
        self.makespan = 0
        self.lane_cycles = 0

    def _port_bad(self, slots: list[int], core: int, off: int, d: int) -> int:
        base = core * self.core_lanes
        bad = _spread(slots[base], d)
        for k in range(1, self.core_lanes):
            bad &= _spread(slots[base + k], d)
        return bad >> off if off else bad

    def _pick(self, slots: list[int], core: int, at: int, d: int) -> int:
        base = core * self.core_lanes
        win = ((1 << d) - 1) << at
        for k in range(self.core_lanes):
            if not (slots[base + k] & win):
                return base + k
        raise AssertionError("port claimed free but is not")

    def place(self, step: Step, dur: int, tmin: int = 0) -> int:
        d = dur
        bad = 0
        for lid, off in step.lanes:
            occ = self.lane[lid]
            if occ:
                bad |= _spread(occ, d) >> off
        bad |= self._port_bad(self.inj, step.src, 0, d)
        for rd, off in step.arrive.items():
            bad |= self._port_bad(self.ej, rd, off, d)
        t = _first_zero(bad, max(0, tmin))
        win = ((1 << d) - 1) << t
        for lid, off in step.lanes:
            self.lane[lid] |= ((1 << d) - 1) << (t + off)
            self.lane_cycles += d
        self.inj[self._pick(self.inj, step.src, t, d)] |= win
        for rd, off in step.arrive.items():
            self.ej[self._pick(self.ej, rd, t + off, d)] |= \
                ((1 << d) - 1) << (t + off)
        for stn, _kind, off in step.turns:
            self.turn[stn].append((t + off, t + off + CYC_TURN))
        self.placed.append((step, t, d))
        self.makespan = max(self.makespan, t + step.last + d)
        return t

    def turn_stats(self) -> tuple[int, float, dict[str, dict[str, float]]]:
        """Concurrent RBRG turn residency: what each bridge must hold at once."""
        peak, tot, per = 0, 0.0, {}
        span = max(1, self.makespan)
        for stn, iv in self.turn.items():
            ev: list[tuple[int, int]] = []
            for a, b in iv:
                ev.append((a, 1))
                ev.append((b, -1))
            ev.sort()
            cur = hi = 0
            area = 0
            prev = ev[0][0] if ev else 0
            for at, delta in ev:
                area += cur * (at - prev)
                prev = at
                cur += delta
                hi = max(hi, cur)
            peak = max(peak, hi)
            tot += area / span
            per[f"{stn[0]}:{stn[1]}"] = {
                "peak": hi, "mean": round(area / span, 3),
                "entries": len(iv)}
        return peak, (tot / max(1, len(self.turn))), per


# ---------------------------------------------------------------------------
# 3. Routing-independent lower bounds
# ---------------------------------------------------------------------------

def cut_lanes(fab: Fabric) -> dict[str, dict[int, int]]:
    """Directed lane counts crossing every core-column / core-row cut."""
    xc: dict[int, int] = defaultdict(int)
    yc: dict[int, int] = defaultdict(int)
    for lst in fab.adj.values():
        for e in lst:
            if e.kind not in LANE_KINDS:
                continue
            a = fab.station[e.frm.rsplit(":", 1)[0]]
            b = fab.station[e.to.rsplit(":", 1)[0]]
            for c in range(1, N_COLS):
                cut = (CORE_X[c - 1] + CORE_X[c]) / 2
                if (a.x - cut) * (b.x - cut) < 0:
                    xc[c] += 1
            for r in range(1, N_ROWS):
                cut = EDGE + r * PITCH_Y
                if (a.y - cut) * (b.y - cut) < 0:
                    yc[r] += 1
    return {"col": dict(xc), "row": dict(yc)}


def pair_demand(pattern: Pattern, root: int = 0) -> list[tuple[int, int]]:
    """The data requirement: (origin, consumer) for every message, no algorithm.

    Which cut a payload has to cross is a property of where its producer and
    consumer sit, not of how it gets there, so this is the right input to a
    structural bound.
    """
    if pattern in ("allgather", "allreduce", "alltoall"):
        return [(s, d) for s in range(N_CORES) for d in range(N_CORES)
                if s != d]
    if pattern in ("gather", "reduce"):
        return [(s, root) for s in range(N_CORES) if s != root]
    if pattern == "broadcast":
        return [(root, d) for d in range(N_CORES) if d != root]
    raise ValueError(pattern)


def _payload_counts(pattern: Pattern, root: int, combinable: bool
                    ) -> tuple[dict[int, int], dict[int, int]]:
    """Distinct payloads each core must *originate* and must *consume*.

    This is where relaying and local combining are priced in, and getting it
    right matters: a core that may forward what it received does not have to
    inject 47 separate copies of its own block, and a core that may add into
    its L1 does not have to receive 47 separate summands. Only genuinely
    distinct data is counted, so the resulting port floor holds for every
    algorithm rather than only for direct-send ones.
    """
    origin: dict[int, int] = defaultdict(int)
    consume: dict[int, int] = defaultdict(int)
    if pattern == "alltoall":
        for c in range(N_CORES):
            origin[c] = consume[c] = N_CORES - 1
    elif pattern == "allgather":
        for c in range(N_CORES):
            origin[c] = 1
            consume[c] = N_CORES - 1          # 47 distinct blocks, no combining
    elif pattern == "allreduce":
        for c in range(N_CORES):
            origin[c] = 1
            consume[c] = 1 if combinable else N_CORES - 1
    elif pattern == "gather":
        for c in range(N_CORES):
            origin[c] = 0 if c == root else 1
        consume[root] = N_CORES - 1
    elif pattern == "reduce":
        for c in range(N_CORES):
            origin[c] = 0 if c == root else 1
        consume[root] = 1 if combinable else N_CORES - 1
    elif pattern == "broadcast":
        origin[root] = 1
        for c in range(N_CORES):
            if c != root:
                consume[c] = 1
    else:
        raise ValueError(pattern)
    return origin, consume


def lower_bounds(fab: Fabric, pattern: Pattern, m: int, *, root: int = 0,
                 tier: Tier = "T0", sigma: int = 1,
                 core_lanes: int = CORE_LANES,
                 rs: dict[tuple[int, int], Route] | None = None
                 ) -> dict[str, Any]:
    """Structural floors valid for every algorithm on this fabric.

    Each family is a counting argument over a resource the fabric physically
    has, so none of them assumes a routing or a schedule. Both tiers may relay
    through a core's L1 and combine locally there; T1 adds copy-and-continue on
    a rail, which is the only extra the fabric can express.

    `cut`      a payload whose producer and consumer sit on opposite sides of a
               cut must cross it, and the cut has a fixed number of directed
               lanes. One rule covers every case: group the origin by side when
               partial sums may be combined, and group the consumer by side
               when one arc may serve them all. The number of distinct
               (origin-group, consumer-group) flows is then exactly the number
               of crossings that cannot be avoided.
    `inject`,
    `eject`    a core drives `core_lanes` directed lanes, and only genuinely
               distinct payloads are counted (see `_payload_counts`).
    `turn`     every cross-row message turns twice, and the H->V moves
               reachable from one source row are finite.
    `latency`  the last arrival cannot precede the shortest path to the
               farthest member.
    `serial`   a fan-in / fan-out of 48 cores cannot be done in fewer than
               log_(1+lanes)(48) dependent hops, each costing at least the
               cheapest hop on the fabric plus an L1 turnaround.
    """
    rs = rs or all_routes(fab)
    cuts = cut_lanes(fab)
    dur = m * sigma
    pairs = pair_demand(pattern, root)
    combinable = pattern in ("reduce", "allreduce")
    # The same payload can be replicated after a cut by L1 relay (both tiers)
    # or by rail multicast (T1). Either way one crossing serves the far side.
    replicable = pattern in ("broadcast", "allgather", "allreduce")
    mcast = tier == "T1" and pattern in T1_PATTERNS

    def side_col(core: int, c: int) -> int:
        return 0 if core % N_COLS < c else 1

    def side_row(core: int, r: int) -> int:
        return 0 if core // N_COLS < r else 1

    cut_bound, cut_detail = 0, []
    for axis, sider, rng in (("col", side_col, range(1, N_COLS)),
                             ("row", side_row, range(1, N_ROWS))):
        for k in rng:
            lanes = cuts[axis].get(k, 0)
            if not lanes:
                continue
            flows = {(sider(s, k) if combinable else s,
                      sider(d, k) if replicable else d)
                     for s, d in pairs if sider(s, k) != sider(d, k)}
            cy = math.ceil(len(flows) * dur / lanes)
            cut_detail.append({"axis": axis, "at": k, "lanes": lanes,
                               "messages": len(flows), "cycles": cy})
            cut_bound = max(cut_bound, cy)

    origin, consume = _payload_counts(pattern, root, combinable)
    if mcast:
        # one boarding can serve a whole rail arc, but rows need separate arcs
        rows_of: dict[int, set[int]] = defaultdict(set)
        for s, d in pairs:
            rows_of[s].add(d // N_COLS)
        origin = {s: min(origin[s], len(v)) if origin[s] else 0
                  for s, v in rows_of.items()}
    inject = math.ceil(max(origin.values(), default=0) * dur / core_lanes)
    eject = math.ceil(max(consume.values(), default=0) * dur / core_lanes)

    per_row_turn_lanes = 2 * 16 * 4          # 2 rails x 16 vi x 4 H2V moves
    leave: dict[int, int] = defaultdict(int)
    for s, d in pairs:
        if s // N_COLS != d // N_COLS:
            leave[s // N_COLS] += 1
    turn = math.ceil(max(leave.values(), default=0) * dur / per_row_turn_lanes)

    if pattern == "broadcast":
        lat = max(rs[(root, d)].total for d in range(N_CORES) if d != root)
    elif pattern in ("gather", "reduce"):
        lat = max(rs[(s, root)].total for s in range(N_CORES) if s != root)
    else:
        lat = max(r.total for r in rs.values())
    latency = lat + dur

    cheapest = min(r.total for r in rs.values())
    depth = math.ceil(math.log(N_CORES) / math.log(1 + core_lanes))
    serial = depth * (cheapest + RAMP) + dur if pattern != "alltoall" else dur

    load: dict[str, int] = defaultdict(int)
    for s, d in pairs:
        for lid, _ in _walk(rs[(s, d)])[0]:
            load[lid] += 1
    hot = max(load, key=load.get) if load else None

    fam = {"cut": cut_bound, "inject": inject, "eject": eject,
           "turn": turn, "latency": latency, "serial": serial}
    return {
        "pattern": pattern, "m": m, "tier": tier, "sigma": sigma,
        "n_messages": len(pairs), **fam,
        "floor": max(fam.values()),
        "binding": max(fam.items(), key=lambda kv: kv[1])[0],
        "cut_detail": sorted(cut_detail, key=lambda r: -r["cycles"])[:3],
        "ref_router_lane_cycles": (load[hot] * dur) if hot else 0,
        "ref_router_hot_lane": hot,
    }


# ---------------------------------------------------------------------------
# 4. Static calendars
# ---------------------------------------------------------------------------

@dataclass
class Calendar:
    pattern: Pattern
    algo: str
    tier: Tier
    m: int
    makespan: int
    n_boardings: int
    depth: int
    lane_cycles: int
    useful_lane_cycles: int
    turn_peak: int
    turn_mean: float
    per_bridge: dict[str, dict[str, float]] = field(default_factory=dict)
    steps: list[tuple[Step, int]] = field(default_factory=list)

    @property
    def lane_util(self) -> float:
        return self.lane_cycles / max(1, N_LANES * self.makespan)

    @property
    def useful_util(self) -> float:
        return self.useful_lane_cycles / max(1, N_LANES * self.makespan)

    @property
    def hop_tax(self) -> float:
        return self.lane_cycles / max(1, self.useful_lane_cycles)


def _useful(rs: dict[tuple[int, int], Route], pattern: Pattern, root: int,
            dur: int) -> int:
    """Lane-cycles an oracle would need: direct shortest route per message.

    The comparison this enables is the one that matters -- how much of the wire
    a schedule buys goes into moving payload towards where it is needed, versus
    into relay legs and multicast over-reach.
    """
    tot = 0
    for s, d in pair_demand(pattern, root):
        tot += len(_walk(rs[(s, d)])[0]) * dur
    return tot


def _schedule(rs, pattern: Pattern, algo: str, tier: Tier, m: int,
              phases: list[list[tuple[Step, int]]], *, root: int = 0,
              sigma: int = 1, core_lanes: int = CORE_LANES) -> Calendar:
    """Pack phases in order; phase i+1 is released when phase i has landed.

    The phase boundary *is* the data dependency: everything in a later phase
    reads something an earlier one produced, so it cannot board before that
    payload is in the relay core's L1 (hence + RAMP). Inside a phase the packer
    interleaves freely. Each boarding carries its own flit count -- a column
    leg of allgather is an 8-block row bundle, not one block.
    """
    pk = Packer(core_lanes=core_lanes)
    ready = 0
    for ph in phases:
        done = ready
        for st, size in ph:
            dur = size * sigma
            t = pk.place(st, dur, ready)
            done = max(done, t + st.last + dur)
        ready = done + RAMP
    peak, mean, per = pk.turn_stats()
    return Calendar(
        pattern=pattern, algo=algo, tier=tier, m=m, makespan=pk.makespan,
        n_boardings=len(pk.placed), depth=len(phases),
        lane_cycles=pk.lane_cycles,
        useful_lane_cycles=_useful(rs, pattern, root, m * sigma),
        turn_peak=peak, turn_mean=round(mean, 3), per_bridge=per,
        steps=[(st, t) for st, t, _d in pk.placed])


def _rows() -> list[list[int]]:
    return [[r * N_COLS + c for c in range(N_COLS)] for r in range(N_ROWS)]


def _cols() -> list[list[int]]:
    return [[r * N_COLS + c for r in range(N_ROWS)] for c in range(N_COLS)]


ALGOS: dict[str, tuple[str, ...]] = {
    "allgather": ("flat", "ring_rotate", "dim_2phase"),
    "allreduce": ("flat", "ring_rotate", "dim_2phase"),
    "alltoall": ("flat", "dim_2phase"),
    "gather": ("flat", "dim_2phase", "tree"),
    "reduce": ("flat", "dim_2phase", "tree"),
    "broadcast": ("flat", "dim_2phase", "tree"),
}


def build_calendar(fab: Fabric, rs: dict[tuple[int, int], Route],
                   pattern: Pattern, algo: str, m: int, *,
                   tier: Tier = "T0", root: int = 0, sigma: int = 1,
                   core_lanes: int = CORE_LANES, rounds: int = 1) -> Calendar:
    """Assemble the phase list for (pattern, algo, tier) and pack it.

    Sizes are explicit. A column leg of allgather carries a whole 8-block row
    bundle; an alltoall row leg carries the 6 messages bound for one column;
    a reduce leg stays at m no matter how many contributions went into it.
    Charging all of these a flat `m` would flatter the dimension-decomposed
    schedules by up to 8x.
    """
    U = lambda s, d: unicast_step(rs, s, d)
    tour = ring_order()
    ipos = {c: i for i, c in enumerate(tour)}
    combine = pattern in ("reduce", "allreduce")

    def ring_next(core: int, k: int = 1) -> int:
        r, c = divmod(core, N_COLS)
        return r * N_COLS + tour[(ipos[c] + k) % len(tour)]

    def fanout(src: int, dsts: list[int], size: int) -> list[tuple[Step, int]]:
        if tier == "T1" and pattern in T1_PATTERNS and dsts:
            return [(st, size) for st in row_multicast_steps(rs, src, dsts)]
        return [(U(src, d), size) for d in dsts]

    phases: list[list[tuple[Step, int]]] = []
    R, C = _rows(), _cols()

    if pattern in ("allgather", "allreduce", "alltoall"):
        if algo == "flat":
            phases = [[(U(s, d), m) for s in range(N_CORES)
                       for d in range(N_CORES) if s != d]]
        elif algo == "ring_rotate":
            # 7 laps of every row ring in parallel, then the vertical line.
            # Rotation is horizontal-only: a column has no wraparound, so a
            # vertical "rotation" would pay the 111-cycle return trip.
            # allreduce combines, so every lap stays at m; allgather grows.
            row_sz = m
            col_sz = m if combine else N_COLS * m
            for _ in range(1, N_COLS):
                phases.append([(U(c, ring_next(c)), row_sz)
                               for c in range(N_CORES)])
            for dr in range(1, N_ROWS):
                phases.append([(U(c, ((c // N_COLS + dr) % N_ROWS) * N_COLS
                                    + c % N_COLS), col_sz)
                               for c in range(N_CORES)])
        elif algo == "dim_2phase":
            if pattern == "alltoall":
                phases.append([st for c in range(N_CORES)
                               for st in fanout(
                                   c, [d for d in R[c // N_COLS] if d != c],
                                   N_ROWS * m)])
                phases.append([(U(s, d), N_COLS * m) for col in C
                               for s in col for d in col if s != d])
            else:
                row_sz = m
                col_sz = m if combine else N_COLS * m
                phases.append([st for c in range(N_CORES)
                               for st in fanout(
                                   c, [d for d in R[c // N_COLS] if d != c],
                                   row_sz)])
                phases.append([(U(s, d), col_sz) for col in C
                               for s in col for d in col if s != d])
        else:
            raise ValueError(algo)

    elif pattern == "broadcast":
        rrow = root // N_COLS
        if algo == "flat":
            phases = [[(U(root, d), m) for d in range(N_CORES) if d != root]]
        elif algo == "dim_2phase":
            phases.append(fanout(root, [c for c in R[rrow] if c != root], m))
            phases.append([(U(c, r * N_COLS + c % N_COLS), m)
                           for c in R[rrow]
                           for r in range(N_ROWS) if r != rrow])
        elif algo == "tree":
            have, rest = [root], [c for c in range(N_CORES) if c != root]
            while rest:
                ph, nxt = [], []
                for s in list(have):
                    if not rest:
                        break
                    d = min(rest, key=lambda x: rs[(s, x)].total)
                    rest.remove(d)
                    nxt.append(d)
                    ph.append((U(s, d), m))
                phases.append(ph)
                have += nxt
        else:
            raise ValueError(algo)

    elif pattern in ("gather", "reduce"):
        rrow = root // N_COLS
        grow = pattern == "gather"
        if algo == "flat":
            phases = [[(U(s, root), m) for s in range(N_CORES) if s != root]]
        elif algo == "dim_2phase":
            phases.append([(U(r * N_COLS + c % N_COLS, c), m)
                           for c in R[rrow]
                           for r in range(N_ROWS) if r != rrow])
            phases.append([(U(c, root), N_ROWS * m if grow else m)
                           for c in R[rrow] if c != root])
        elif algo == "tree":
            held = {c: 1 for c in range(N_CORES)}
            cur = [root] + [c for c in range(N_CORES) if c != root]
            while len(cur) > 1:
                ph, nxt = [], []
                for i in range(0, len(cur), 2):
                    if i + 1 >= len(cur):
                        nxt.append(cur[i])
                        continue
                    a, b = cur[i], cur[i + 1]
                    ph.append((U(b, a), held[b] * m if grow else m))
                    held[a] += held[b]
                    nxt.append(a)
                phases.append(ph)
                cur = nxt
        else:
            raise ValueError(algo)
    else:
        raise ValueError(pattern)

    if rounds > 1:
        phases = [leg for _ in range(rounds) for leg in phases]
    return _schedule(rs, pattern, algo, tier, m, phases, root=root,
                     sigma=sigma, core_lanes=core_lanes)


# ---------------------------------------------------------------------------
# 5. Unscheduled baseline
# ---------------------------------------------------------------------------

@dataclass
class BaseResult:
    pattern: Pattern
    m: int
    makespan: int
    n_messages: int
    deflections: int
    lane_cycles: int
    useful_lane_cycles: int
    turn_peak: int
    turn_peak_node: str | None
    turn_mean: float
    turn_full_cycles: int
    per_bridge: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def lane_util(self) -> float:
        return self.lane_cycles / max(1, N_LANES * self.makespan)


def run_base(fab: Fabric, rs: dict[tuple[int, int], Route],
             pattern: Pattern, m: int, *, root: int = 0, sigma: int = 1,
             fifo_depth: int = 4, core_lanes: int = CORE_LANES,
             t_turn: int = CYC_TURN) -> BaseResult:
    """Online, no-lookahead injection: the unscheduled mechanism.

    The reference document is a latency model and does not specify an
    arbitration policy, so this baseline states its assumptions rather than
    pretending to derive them:

      * cores present their messages in a fixed round-robin order and never
        reorder -- no global schedule, no lookahead past the message at the
        head of a core's own queue;
      * a message boards at the first cycle its whole route is free, which is
        what an E-tag style end-to-end slot reservation buys on a rigid route;
      * a turn occupies that RBRG's transfer FIFO for `t_turn`, and if the FIFO
        is already full the message is *deflected*: it takes a full row-ring lap
        before retrying. An undersized FIFO therefore costs makespan, never
        deadlock, which is the property that makes a bufferless ring safe.

    The difference from a calendar is the absence of lookahead, and that is the
    whole point of the comparison.
    """
    pairs = pair_demand(pattern, root)
    dur = m * sigma
    lane: dict[str, int] = defaultdict(int)
    inj: list[int] = [0] * (N_CORES * core_lanes)
    ej: list[int] = [0] * (N_CORES * core_lanes)
    fifo: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    deflect = 0
    lane_cycles = 0

    def port_bad(slots: list[int], core: int, off: int) -> int:
        base = core * core_lanes
        bad = _spread(slots[base], dur)
        for k in range(1, core_lanes):
            bad &= _spread(slots[base + k], dur)
        return bad >> off if off else bad

    def pick(slots: list[int], core: int, at: int) -> int:
        base = core * core_lanes
        win = ((1 << dur) - 1) << at
        for k in range(core_lanes):
            if not (slots[base + k] & win):
                return base + k
        raise AssertionError

    by_src: dict[int, list[int]] = defaultdict(list)
    for s, d in pairs:
        by_src[s].append(d)
    order: list[tuple[int, int]] = []
    i = 0
    while any(i < len(v) for v in by_src.values()):
        for s in range(N_CORES):
            if i < len(by_src[s]):
                order.append((s, by_src[s][i]))
        i += 1

    finish = 0
    for s, d in order:
        step = unicast_step(rs, s, d)
        bad = 0
        for lid, off in step.lanes:
            if lane[lid]:
                bad |= _spread(lane[lid], dur) >> off
        bad |= port_bad(inj, s, 0)
        bad |= port_bad(ej, d, step.wire)
        t = 0
        while True:
            t = _first_zero(bad, t)
            room = all(
                sum(1 for a, b in fifo[stn] if a <= t + off < b) < fifo_depth
                for stn, _k, off in step.turns)
            if room:
                break
            deflect += 1
            t += ROW_TOUR
        for lid, off in step.lanes:
            lane[lid] |= ((1 << dur) - 1) << (t + off)
            lane_cycles += dur
        inj[pick(inj, s, t)] |= ((1 << dur) - 1) << t
        ej[pick(ej, d, t + step.wire)] |= ((1 << dur) - 1) << (t + step.wire)
        for stn, _k, off in step.turns:
            fifo[stn].append((t + off, t + off + t_turn))
        finish = max(finish, t + step.wire + dur)

    peak, per, full_tot, mean_tot = 0, {}, 0, 0.0
    hot = None
    for stn, iv in fifo.items():
        ev: list[tuple[int, int]] = []
        for a, b in iv:
            ev += [(a, 1), (b, -1)]
        ev.sort()
        cur = hi = area = 0
        full = 0
        prev = ev[0][0]
        for at, delta in ev:
            area += cur * (at - prev)
            if cur >= fifo_depth:
                full += at - prev
            prev = at
            cur += delta
            hi = max(hi, cur)
        key = f"{stn[0]}:{stn[1]}"
        per[key] = {"peak": hi, "mean": round(area / max(1, finish), 3),
                    "full_cy": full, "entries": len(iv)}
        mean_tot += area / max(1, finish)
        full_tot += full
        if hi > peak:
            peak, hot = hi, key
    return BaseResult(
        pattern=pattern, m=m, makespan=finish, n_messages=len(pairs),
        deflections=deflect, lane_cycles=lane_cycles,
        useful_lane_cycles=_useful(rs, pattern, root, dur),
        turn_peak=peak, turn_peak_node=hot,
        turn_mean=round(mean_tot / max(1, len(per)), 3),
        turn_full_cycles=full_tot, per_bridge=per)
