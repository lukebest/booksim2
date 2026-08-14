"""AIC reticle fabric: a faithful port of the `aic-reticle-shortest-path` model.

This is a *transcription*, not a redesign. Every constant, every edge and the
phase-constrained router below are ported line-for-line from the reference
widget so that the numbers this file reports can be checked against it by
hand. Nothing here is tuned; if a number looks wrong, the reference is the
arbiter.

Why it exists: the earlier ring work carried an abstract wire model (one
number per row hop, one per column hop). The reference model instead builds a
26x33 mm reticle out of *named physical segments* and charges
`ceil(micron / 400)` per segment, so a "hop" is a composite of arm + gap +
station passes. Three consequences drive everything downstream:

  1. A core has **no vertical port**. It attaches to exactly one horizontal
     rail, at `M(2*row + col%2, col)`. Changing row is only possible by
     turning at an RBRG station, and a turn costs 10 cycles. There is no
     cheap "drop into L1 and re-inject on the column ring" path any more --
     that trick needed a vertical port the core does not have.
  2. Routing is dimension-ordered with **exactly two turns** for any
     cross-row pair (H -> V -> H) and **zero turns** for same-row pairs. The
     router enforces this with a 3-state phase machine, so it is a hard
     structural property, not a heuristic.
  3. The row ring is genuinely folded: even columns sit on rail `2r`, odd
     columns on rail `2r+1`, and the two rails are joined at both ends by a
     13-cycle fold. So the ring tour is 0,2,4,6,7,5,3,1 -- the same folded
     order the ring work already used, but with very different costs.

Geometry (all in micron; 400 micron per cycle):
  reticle 26000 x 33000, edge margin 500, lane width 105
  core pitch 3130 (x) x 5340 (y), 6 rows x 8 columns = 48 cores
  12 horizontal rails (2 per row), 16 vertical rails (2 per column)
  RBRG station at each of the 12x16 = 192 rail crossings
  8 middle stations per rail (96 total): CS where rail parity == column
  parity (that is where a core attaches), PIPE otherwise

Segment costs (cycles):
  core <-> CS access   105 um -> 1     H arm (B<->M)     1125 um -> 3
  inter-station gap     40 um -> 1     V span            4460 um -> 12
  RBRG straight        420 um -> 2     RBRG near turn     315 um -> 10
  RBRG far turn        525 um -> 10    H fold            5180 um -> 13
  V fold               405 um -> 2     CS / PIPE pass       0 um -> 0

A turn's 10 cycles are 5 ingress + 5 egress; the turn geometry is inclusive,
so the 315 / 525 um are not charged again as wire.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Geometry -- transcribed constants
# --------------------------------------------------------------------------

W, H = 26_000, 33_000
EDGE = 500
LANE = 105
FIELD_W, FIELD_H = 25_000, 32_000
PITCH_X, PITCH_Y = 3130, 5340
UM_PER_CYCLE = 400

N_ROWS, N_COLS = 6, 8
N_CORES = N_ROWS * N_COLS
N_HRAIL = 2 * N_ROWS          # 12
N_VRAIL = 2 * N_COLS          # 16

VX = [710, 3380, 3840, 6510, 6970, 9640, 10100, 12770,
      13230, 15900, 16360, 19030, 19490, 22160, 22620, 25290]
HY = [710, 5590, 6050, 10930, 11390, 16270,
      16730, 21610, 22070, 26950, 27410, 32290]
CORE_X = [2045 + PITCH_X * c for c in range(N_COLS)]

# Segment micron lengths, named exactly as the reference does.
UM_ACCESS = 105
UM_ARM = 1125
UM_GAP = 40
UM_VSPAN = 4460
UM_STRAIGHT = 420
UM_NEAR = 315
UM_FAR = 525
UM_HFOLD = 5180
UM_VFOLD = 405

CYC_STRAIGHT = 2
CYC_TURN = 10                 # 5 ingress + 5 egress
TURN_INGRESS = 5
TURN_EGRESS = 5


def cyc_of(um: int) -> int:
    """The reference charges ceil(micron / 400) for every wire segment."""
    return math.ceil(um / UM_PER_CYCLE)


# Derived per-segment cycle costs, so a reader can check them against the doc.
CYC = {
    "access": cyc_of(UM_ACCESS),      # 1
    "harm": cyc_of(UM_ARM),           # 3
    "gap": cyc_of(UM_GAP),            # 1
    "vspan": cyc_of(UM_VSPAN),        # 12
    "straight": CYC_STRAIGHT,         # 2 (given, not ceil(420/400)=2 -- same)
    "near": CYC_TURN,                 # 10
    "far": CYC_TURN,                  # 10
    "hfold": cyc_of(UM_HFOLD),        # 13
    "vfold": cyc_of(UM_VFOLD),        # 2
    "cs": 0,
    "pipe": 0,
}

# The 12 RBRG moves. (in_port, out_port, kind, from_axis, to_axis)
MOVES: list[tuple[str, str, str, str, str]] = [
    ("Wi", "Eo", "straight", "H", "H"), ("Ei", "Wo", "straight", "H", "H"),
    ("Ni", "So", "straight", "V", "V"), ("Si", "No", "straight", "V", "V"),
    ("Wi", "So", "near", "H", "V"), ("Si", "Eo", "near", "V", "H"),
    ("Ei", "No", "near", "H", "V"), ("Ni", "Wo", "near", "V", "H"),
    ("Wi", "No", "far", "H", "V"), ("Ni", "Eo", "far", "V", "H"),
    ("Ei", "So", "far", "H", "V"), ("Si", "Wo", "far", "V", "H"),
]


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Edge:
    eid: str
    kind: str
    um: int
    cyc: int
    frm: str
    to: str
    hi: int | None = None
    vi: int | None = None
    col: int | None = None
    direction: str | None = None
    trans: str | None = None       # "H2V" / "V2H" / None
    turn: int = 0
    from_axis: str | None = None
    to_axis: str | None = None


@dataclass
class Station:
    sid: str
    type: str                      # rbrg / cs / pipe
    x: int
    y: int
    hi: int
    vi: int | None = None
    col: int | None = None


def b_id(hi: int, vi: int) -> str:
    return f"B:{hi}:{vi}"


def m_id(hi: int, c: int) -> str:
    return f"M:{hi}:{c}"


def prt(sid: str, p: str) -> str:
    return f"{sid}:{p}"


@dataclass
class Fabric:
    """The station / port graph. `adj` maps a port to its outgoing edges."""

    station: dict[str, Station] = field(default_factory=dict)
    adj: dict[str, list[Edge]] = field(default_factory=dict)

    # -- construction -----------------------------------------------------
    def _add_station(self, s: Station) -> None:
        self.station[s.sid] = s

    def _add_edge(self, e: Edge) -> None:
        self.adj.setdefault(e.frm, []).append(e)

    def build(self) -> "Fabric":
        for hi in range(N_HRAIL):
            for vi in range(N_VRAIL):
                self._add_station(Station(b_id(hi, vi), "rbrg", VX[vi], HY[hi],
                                          hi, vi=vi))
            for c in range(N_COLS):
                kind = "cs" if (hi % 2) == (c % 2) else "pipe"
                self._add_station(Station(m_id(hi, c), kind, CORE_X[c], HY[hi],
                                          hi, col=c))

        # RBRG internal moves
        for hi in range(N_HRAIL):
            for vi in range(N_VRAIL):
                s = self.station[b_id(hi, vi)]
                for k, (a, z, kind, fa, ta) in enumerate(MOVES):
                    um = (UM_STRAIGHT if kind == "straight" else
                          UM_NEAR if kind == "near" else UM_FAR)
                    turn = 0 if fa == ta else 1
                    self._add_edge(Edge(
                        f"R:{hi}:{vi}:{k}", kind, um,
                        CYC_STRAIGHT if kind == "straight" else CYC_TURN,
                        prt(s.sid, a), prt(s.sid, z), hi=hi, vi=vi,
                        trans=None if not turn else f"{fa}2{ta}", turn=turn,
                        from_axis=fa, to_axis=ta))

        # Middle-station passes (CS / PIPE), zero cycles
        for hi in range(N_HRAIL):
            for c in range(N_COLS):
                s = self.station[m_id(hi, c)]
                self._add_edge(Edge(f"M:{hi}:{c}:E", s.type, 0, 0,
                                    prt(s.sid, "Wi"), prt(s.sid, "Eo"),
                                    hi=hi, col=c, direction="E"))
                self._add_edge(Edge(f"M:{hi}:{c}:W", s.type, 0, 0,
                                    prt(s.sid, "Ei"), prt(s.sid, "Wo"),
                                    hi=hi, col=c, direction="W"))

        # Horizontal rail chains: B(hi,2c) M(hi,c) B(hi,2c+1) per column
        for hi in range(N_HRAIL):
            seq: list[Station] = []
            for c in range(N_COLS):
                seq.append(self.station[b_id(hi, 2 * c)])
                seq.append(self.station[m_id(hi, c)])
                seq.append(self.station[b_id(hi, 2 * c + 1)])
            for i in range(len(seq) - 1):
                a, z = seq[i], seq[i + 1]
                gap = a.type == "rbrg" and z.type == "rbrg"
                kind = "gap" if gap else "harm"
                um = UM_GAP if gap else UM_ARM
                self._add_edge(Edge(f"H:{hi}:{i}:E", kind, um, cyc_of(um),
                                    prt(a.sid, "Eo"), prt(z.sid, "Wi"),
                                    hi=hi, direction="E"))
                self._add_edge(Edge(f"H:{hi}:{i}:W", kind, um, cyc_of(um),
                                    prt(z.sid, "Wo"), prt(a.sid, "Ei"),
                                    hi=hi, direction="W"))

        # Horizontal folds: join a row's two rails at both ends
        for r in range(N_ROWS):
            t, b = 2 * r, 2 * r + 1
            Lt, Lb = b_id(t, 0), b_id(b, 0)
            Rt, Rb = b_id(t, N_VRAIL - 1), b_id(b, N_VRAIL - 1)
            for eid, frm, to, hi in (
                    (f"HF:{r}:RE", prt(Rt, "Eo"), prt(Rb, "Ei"), t),
                    (f"HF:{r}:RW", prt(Rb, "Eo"), prt(Rt, "Ei"), b),
                    (f"HF:{r}:LE", prt(Lt, "Wo"), prt(Lb, "Wi"), t),
                    (f"HF:{r}:LW", prt(Lb, "Wo"), prt(Lt, "Wi"), b)):
                self._add_edge(Edge(eid, "hfold", UM_HFOLD, cyc_of(UM_HFOLD),
                                    frm, to, hi=hi, direction="fold"))

        # Vertical rails: span where hi is even, gap where odd
        for vi in range(N_VRAIL):
            for hi in range(N_HRAIL - 1):
                a, z = b_id(hi, vi), b_id(hi + 1, vi)
                span = hi % 2 == 0
                kind = "vspan" if span else "gap"
                um = UM_VSPAN if span else UM_GAP
                self._add_edge(Edge(f"V:{vi}:{hi}:S", kind, um, cyc_of(um),
                                    prt(a, "So"), prt(z, "Ni"),
                                    vi=vi, direction="S"))
                self._add_edge(Edge(f"V:{vi}:{hi}:N", kind, um, cyc_of(um),
                                    prt(z, "No"), prt(a, "Si"),
                                    vi=vi, direction="N"))
            top, bot = b_id(0, vi), b_id(N_HRAIL - 1, vi)
            self._add_edge(Edge(f"VF:{vi}:T", "vfold", UM_VFOLD,
                                cyc_of(UM_VFOLD), prt(top, "No"),
                                prt(top, "Ni"), vi=vi, direction="fold"))
            self._add_edge(Edge(f"VF:{vi}:B", "vfold", UM_VFOLD,
                                cyc_of(UM_VFOLD), prt(bot, "So"),
                                prt(bot, "Si"), vi=vi, direction="fold"))

        for lst in self.adj.values():
            lst.sort(key=lambda e: e.eid)
        return self

    # -- core attachment --------------------------------------------------
    @staticmethod
    def core_rc(core: int) -> tuple[int, int]:
        return divmod(core, N_COLS)

    @staticmethod
    def core_rail(core: int) -> int:
        """A core attaches to rail 2*row + (col % 2): the folded-ring order."""
        r, c = divmod(core, N_COLS)
        return 2 * r + (c % 2)

    def core_station(self, core: int) -> Station:
        r, c = divmod(core, N_COLS)
        return self.station[m_id(self.core_rail(core), c)]


# --------------------------------------------------------------------------
# Phase-constrained shortest path
# --------------------------------------------------------------------------

@dataclass
class Route:
    src: int
    dst: int
    edges: list[Edge]
    total: int
    um: int
    turns: int
    steps: int
    folds: int
    counts: dict[str, int]

    def kind_cycles(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.edges:
            out[e.kind] = out.get(e.kind, 0) + e.cyc
        return out


_KEYS = ("straight", "near", "far", "cs", "pipe", "inject", "eject",
         "qnear", "qfar")
ZERO_EXTRA = {k: 0 for k in _KEYS}


def _extra_for(kind: str, v: dict[str, int]) -> int:
    if kind == "straight":
        return v["straight"]
    if kind == "near":
        return v["near"] + v["qnear"]
    if kind == "far":
        return v["far"] + v["qfar"]
    return v.get(kind, 0) if kind in ("cs", "pipe", "inject", "eject") else 0


def route(fab: Fabric, src: int, dst: int,
          v: dict[str, int] | None = None) -> Route:
    """Minimum-cycle dimension-ordered route, ported from the reference.

    The phase machine is the whole point: `cross` pairs must spend exactly one
    H->V turn and then exactly one V->H turn, and the second turn has to land
    on a rail belonging to the destination row. Same-row pairs may not turn at
    all. So turn count is a structural constant (2 or 0), never a choice.
    """
    v = {**ZERO_EXTRA, **(v or {})}
    if src == dst:
        return Route(src, dst, [], 0, 0, 0, 0, 0, {})

    sr, sc = divmod(src, N_COLS)
    dr, dc = divmod(dst, N_COLS)
    cross = sr != dr
    final_phase = 2 if cross else 0
    sm = fab.core_station(src)
    dm = fab.core_station(dst)

    starts = []
    for i, p in enumerate(("Eo", "Wo")):
        e = Edge(f"INJ:{src}:{i}", "inject", UM_ACCESS, cyc_of(UM_ACCESS),
                 f"CORE:{src}", prt(sm.sid, p), hi=sm.hi,
                 direction="W" if i else "E")
        starts.append(e)
    targets = {prt(dm.sid, "Wi"), prt(dm.sid, "Ei")}

    # cost tuple: (total, um, turns, steps, tie) -- lexicographic, as ported
    dist: dict[tuple[str, int], tuple] = {}
    prev: dict[tuple[str, int], tuple[tuple[str, int] | None, Edge]] = {}
    heap: list[tuple[tuple, tuple[str, int]]] = []

    for e in starts:
        key = (e.to, 0)
        cost = (e.cyc + v["inject"], e.um, 0, 1, e.eid)
        if key not in dist or cost < dist[key]:
            dist[key] = cost
            prev[key] = (None, e)
            heapq.heappush(heap, (cost, key))

    best: tuple[tuple, tuple[str, int], Edge] | None = None
    while heap:
        cost, key = heapq.heappop(heap)
        if dist.get(key) != cost:
            continue
        node, phase = key
        if phase == final_phase and node in targets:
            ej = Edge(f"EJ:{dst}", "eject", UM_ACCESS, cyc_of(UM_ACCESS),
                      node, f"CORE:{dst}", hi=dm.hi)
            cand = (cost[0] + ej.cyc + v["eject"], cost[1] + ej.um,
                    cost[2], cost[3] + 1, ej.eid)
            if best is None or cand < best[0]:
                best = (cand, key, ej)
        for e in fab.adj.get(node, ()):
            ph = phase
            if not cross and e.trans:
                continue
            if cross and e.trans == "H2V":
                if ph != 0:
                    continue
                ph = 1
            elif cross and e.trans == "V2H":
                if ph != 1 or (e.hi is None) or (e.hi // 2) != dr:
                    continue
                ph = 2
            nk = (e.to, ph)
            nc = (cost[0] + e.cyc + _extra_for(e.kind, v), cost[1] + e.um,
                  cost[2] + e.turn, cost[3] + 1, e.eid)
            if nk not in dist or nc < dist[nk]:
                dist[nk] = nc
                prev[nk] = (key, e)
                heapq.heappush(heap, (nc, nk))

    if best is None:
        raise RuntimeError(f"no legal route {src} -> {dst}")

    total, um, turns, steps, _ = best[0]
    edges = [best[2]]
    k: tuple[str, int] | None = best[1]
    while k is not None:
        p = prev.get(k)
        if p is None:
            break
        edges.insert(0, p[1])
        k = p[0]
    counts: dict[str, int] = {}
    for e in edges:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    folds = counts.get("hfold", 0) + counts.get("vfold", 0)
    return Route(src, dst, edges, total, um, turns, steps, folds, counts)


# --------------------------------------------------------------------------
# Measurement helpers used by the DSE / report
# --------------------------------------------------------------------------

def all_routes(fab: Fabric) -> dict[tuple[int, int], Route]:
    return {(s, d): route(fab, s, d)
            for s in range(N_CORES) for d in range(N_CORES) if s != d}


def latency_matrix(fab: Fabric) -> list[list[int]]:
    out = [[0] * N_CORES for _ in range(N_CORES)]
    for (s, d), r in all_routes(fab).items():
        out[s][d] = r.total
    return out


def ring_order(k: int = N_COLS) -> list[int]:
    """Folded row-ring tour: 0,2,4,6,7,5,3,1 for k=8."""
    return list(range(0, k, 2)) + list(range(k - 1, 0, -2))


def audit(fab: Fabric) -> dict[str, Any]:
    rs = all_routes(fab)
    tot = [r.total for r in rs.values()]
    same = [r for (s, d), r in rs.items() if s // N_COLS == d // N_COLS]
    cross = [r for (s, d), r in rs.items() if s // N_COLS != d // N_COLS]
    n_edges = sum(len(v) for v in fab.adj.values())
    tour = ring_order()
    # ring-adjacent cost, measured on row 0 both ways
    adj_h = []
    for i in range(len(tour)):
        a, b = tour[i], tour[(i + 1) % len(tour)]
        adj_h.append((a, b, rs[(a, b)].total, rs[(a, b)].um))
    return {
        "source": "aic-reticle-shortest-path (transcribed)",
        "reticle_um": [W, H],
        "um_per_cycle": UM_PER_CYCLE,
        "n_cores": N_CORES, "n_rows": N_ROWS, "n_cols": N_COLS,
        "pitch_um": [PITCH_X, PITCH_Y],
        "n_hrails": N_HRAIL, "n_vrails": N_VRAIL,
        "n_rbrg": N_HRAIL * N_VRAIL,
        "n_middle": N_HRAIL * N_COLS,
        "n_cs": sum(1 for s in fab.station.values() if s.type == "cs"),
        "n_pipe": sum(1 for s in fab.station.values() if s.type == "pipe"),
        "n_directed_edges": n_edges,
        "segment_cycles": dict(CYC),
        "turn_cycles": CYC_TURN,
        "turn_split": [TURN_INGRESS, TURN_EGRESS],
        "diameter_cy": max(tot),
        "min_cy": min(tot),
        "avg_cy": round(sum(tot) / len(tot), 2),
        "n_pairs": len(tot),
        "same_row": {"n": len(same), "min": min(r.total for r in same),
                     "max": max(r.total for r in same),
                     "avg": round(sum(r.total for r in same) / len(same), 2),
                     "turns": sorted({r.turns for r in same})},
        "cross_row": {"n": len(cross), "min": min(r.total for r in cross),
                      "max": max(r.total for r in cross),
                      "avg": round(sum(r.total for r in cross) / len(cross), 2),
                      "turns": sorted({r.turns for r in cross})},
        "ring_tour": tour,
        "ring_adjacent_hops": adj_h,
    }


def route_ledger(r: Route) -> list[dict[str, Any]]:
    """Per-edge ledger, matching the reference's ordered link table."""
    out, run = [], 0
    for i, e in enumerate(r.edges, 1):
        run += e.cyc
        out.append({"step": i, "id": e.eid, "kind": e.kind, "um": e.um,
                    "cyc": e.cyc, "running": run, "turn": e.turn})
    return out


def main() -> None:
    fab = Fabric().build()
    a = audit(fab)
    r = route(fab, 0, 47)
    a["example_0_to_47"] = {
        "total_cy": r.total, "um": r.um, "turns": r.turns,
        "steps": r.steps, "folds": r.folds, "counts": r.counts,
        "kind_cycles": r.kind_cycles(),
        "ledger": route_ledger(r),
    }
    a["latency_matrix"] = latency_matrix(fab)
    out = Path(__file__).resolve().parents[1] / "results" / "aic_reticle_8x6.json"
    out.write_text(json.dumps(a, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}")
    for k in ("n_rbrg", "n_middle", "n_cs", "n_pipe", "n_directed_edges",
              "diameter_cy", "min_cy", "avg_cy"):
        print(f"  {k:20s} {a[k]}")
    print(f"  same-row  {a['same_row']}")
    print(f"  cross-row {a['cross_row']}")
    print(f"  0->47 = {r.total} cy / {r.um} um / {r.turns} turns / "
          f"{r.steps} steps / {r.folds} folds")


if __name__ == "__main__":
    main()
