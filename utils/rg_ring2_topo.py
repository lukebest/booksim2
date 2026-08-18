#!/usr/bin/env python3
"""20-node dual-plane bidirectional full ring (core / HA).

Topology
--------
20 nodes on two *independent* ring planes. Each plane is itself a bidirectional
full ring (node 19 adjacent to node 0). Every node has one inject/eject port
per plane; the two directions of a plane share that port and its buffer.

    even index  -> AI core          {0, 2, ..., 18}
    odd  index  -> memory Home Agent {1, 3, ..., 19}

A transfer is a single arc: pick a plane, pick the shortest direction (CW on a
tie), ride until the destination, leave. There is no turn and therefore no
transfer FIFO and no R4.

Conflict clauses (D-R2)
-----------------------
R1  ring-link mutual exclusion   -- 80 directed segments, or the whole
                                    (plane, dir) if spatial_reuse=whole_ring
R2  boarding mutual exclusion    -- <= board_ports per (node, plane) / cycle
R3  leaving mutual exclusion     -- <= leave_ports per (node, plane) / cycle
                                    (both directions share the leave port)

Plane selection is a scheduling / injection policy, not a topological fact:
static_hash, rr_per_pkt, least_occupied, req_resp_split.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

N_NODES = 20
N_PLANES = 2
HOP_LAT = 1
SIGMA = 1
RAMP = 1

PlaneId = int                          # 0 | 1
Dir = int                              # +1 CW (increasing index) | -1 CCW
Edge = tuple[int, int, int]            # (plane, u, v)
Pair = tuple[int, int]
PlaneSel = Literal["static_hash", "rr_per_pkt", "least_occupied",
                   "req_resp_split"]
SpatialReuse = Literal["arc", "whole_ring"]
Kind = Literal["req", "resp"]


def is_core(n: int) -> bool:
    return (n % 2) == 0


def is_ha(n: int) -> bool:
    return (n % 2) == 1


def cores(n: int = N_NODES) -> list[int]:
    return [i for i in range(n) if is_core(i)]


def has(n: int = N_NODES) -> list[int]:
    return [i for i in range(n) if is_ha(i)]


def cw_hops(src: int, dst: int, n: int = N_NODES) -> int:
    return (dst - src) % n


def ccw_hops(src: int, dst: int, n: int = N_NODES) -> int:
    return (src - dst) % n


def shortest_dir(src: int, dst: int, n: int = N_NODES) -> Dir:
    """Shortest direction; +1 (CW) on an exact tie."""
    if src == dst:
        return 1
    a, b = cw_hops(src, dst, n), ccw_hops(src, dst, n)
    return 1 if a <= b else -1


def hop_count(src: int, dst: int, direction: Dir, n: int = N_NODES) -> int:
    return cw_hops(src, dst, n) if direction > 0 else ccw_hops(src, dst, n)


def board_key(node: int, plane: PlaneId) -> tuple[str, int, int]:
    return ("board", node, plane)


def leave_key(node: int, plane: PlaneId) -> tuple[str, int, int]:
    return ("leave", node, plane)


# ---------------------------------------------------------------------------
# Path / footprint
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ring2Path:
    src: int
    dst: int
    plane: PlaneId
    dir: Dir
    nodes: tuple[int, ...]             # first = board, last = leave

    @property
    def hops(self) -> int:
        return max(0, len(self.nodes) - 1)

    def links(self) -> list[Edge]:
        return [(self.plane, self.nodes[i], self.nodes[i + 1])
                for i in range(self.hops)]

    def key(self) -> tuple[PlaneId, Dir]:
        return (self.plane, self.dir)

    def signature(self) -> tuple:
        return (self.plane, self.dir, self.nodes)


@dataclass
class Ring2Footprint:
    """Rigid occupancy of one granted single-arc transfer, relative to t0."""
    flow_id: int
    src: int
    dst: int
    path: Ring2Path
    m: int
    sigma: int
    kind: Kind = "req"
    links: list[tuple[Edge, int]] = field(default_factory=list)
    boards: list[tuple[tuple[str, int, int], int]] = field(default_factory=list)
    leaves: list[tuple[tuple[str, int, int], int]] = field(default_factory=list)
    rings: list[tuple[tuple[PlaneId, Dir], int, int]] = field(
        default_factory=list)
    dur: int = 0
    wire: int = 0
    release: int = 0

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


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

class Ring2Topology:
    """Two independent bidirectional 20-node rings with bipartite roles."""

    def __init__(self, n: int = N_NODES, n_planes: int = N_PLANES, *,
                 hop_lat: int = HOP_LAT, sigma: int = SIGMA,
                 board_ports: int = 1, leave_ports: int = 1,
                 spatial_reuse: SpatialReuse = "arc"):
        if n < 4 or n % 2:
            raise ValueError("n must be even and >= 4")
        if n_planes < 1:
            raise ValueError("n_planes")
        if spatial_reuse not in ("arc", "whole_ring"):
            raise ValueError(spatial_reuse)
        self.n = n
        self.n_planes = n_planes
        self.hop_lat = hop_lat
        self.sigma = sigma
        self.board_ports = board_ports
        self.leave_ports = leave_ports
        self.spatial_reuse = spatial_reuse
        self.cores = cores(n)
        self.has = has(n)
        self.n_cores = len(self.cores)
        self.n_has = len(self.has)
        # rings: one per (plane, dir) so sched_cost can count grant pointers
        self.rings: list[tuple[PlaneId, Dir]] = [
            (p, d) for p in range(n_planes) for d in (1, -1)]
        self.directed_links: list[Edge] = self._directed_links()
        self.undirected_links = sorted(
            {(p, min(u, v), max(u, v)) for p, u, v in self.directed_links})

    def _directed_links(self) -> list[Edge]:
        out: list[Edge] = []
        for p in range(self.n_planes):
            for i in range(self.n):
                j = (i + 1) % self.n
                out.append((p, i, j))
                out.append((p, j, i))
        return out

    def ring_nodes(self, plane: PlaneId) -> list[int]:
        return list(range(self.n))

    def make_path(self, src: int, dst: int, plane: PlaneId,
                  direction: Dir | None = None) -> Ring2Path:
        if src == dst:
            raise ValueError("src == dst")
        d = shortest_dir(src, dst, self.n) if direction is None else direction
        hops = hop_count(src, dst, d, self.n)
        nodes = tuple((src + d * h) % self.n for h in range(hops + 1))
        return Ring2Path(src=src, dst=dst, plane=plane, dir=d, nodes=nodes)

    def hop_options(self, src: int, dst: int) -> list[Dir]:
        a, b = cw_hops(src, dst, self.n), ccw_hops(src, dst, self.n)
        if src == dst:
            return [1]
        if a < b:
            return [1]
        if b < a:
            return [-1]
        return [1, -1]

    def candidates(self, src: int, dst: int, *,
                   minimal_only: bool = True) -> list[Ring2Path]:
        dirs = self.hop_options(src, dst) if minimal_only else [1, -1]
        if src == dst:
            return []
        out: list[Ring2Path] = []
        seen: set[tuple] = set()
        for p in range(self.n_planes):
            for d in dirs:
                path = self.make_path(src, dst, p, d)
                sig = path.signature()
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(path)
        return out

    def plane_of(self, src: int, dst: int, *, kind: Kind = "req",
                 txn_id: int = 0, strategy: PlaneSel = "least_occupied",
                 rr_state: dict[int, int] | None = None,
                 occupancy: dict[int, int] | None = None) -> PlaneId:
        """Pick a plane. `occupancy` is plane -> in-flight / reserved count."""
        if self.n_planes == 1:
            return 0
        if strategy == "req_resp_split":
            return 0 if kind == "req" else (1 % self.n_planes)
        if strategy == "static_hash":
            return (src + 3 * dst + (0 if kind == "req" else 1)) % self.n_planes
        if strategy == "rr_per_pkt":
            st = rr_state if rr_state is not None else {}
            p = st.get(src, 0) % self.n_planes
            st[src] = p + 1
            return p
        # least_occupied (default): fewest reserved / in-flight on that plane
        occ = occupancy if occupancy is not None else {}
        best = min(range(self.n_planes), key=lambda p: (occ.get(p, 0), p))
        if occupancy is not None:
            occupancy[best] = occupancy.get(best, 0) + 1
        return best

    def fixed_path(self, src: int, dst: int, *, kind: Kind = "req",
                   txn_id: int = 0,
                   strategy: PlaneSel = "static_hash",
                   rr_state: dict[int, int] | None = None,
                   occupancy: dict[int, int] | None = None) -> Ring2Path:
        plane = self.plane_of(src, dst, kind=kind, txn_id=txn_id,
                              strategy=strategy, rr_state=rr_state,
                              occupancy=occupancy)
        return self.make_path(src, dst, plane)

    def footprint(self, flow_id: int, path: Ring2Path, m: int, *,
                  kind: Kind = "req", release: int = 0) -> Ring2Footprint:
        dur = m * self.sigma
        fp = Ring2Footprint(flow_id=flow_id, src=path.src, dst=path.dst,
                            path=path, m=m, sigma=self.sigma, kind=kind,
                            dur=dur, release=release)
        t = 0
        fp.boards.append((board_key(path.src, path.plane), t))
        acc = t
        for e in path.links():
            fp.links.append((e, acc))
            acc += self.hop_lat
        fp.rings.append((path.key(), t, (acc - t) + dur))
        fp.leaves.append((leave_key(path.dst, path.plane), acc))
        fp.wire = acc
        return fp

    # -- loads / bounds -----------------------------------------------------

    def link_load(self, paths: Iterable[Ring2Path], m: int = 1
                  ) -> dict[Edge, int]:
        load: dict[Edge, int] = defaultdict(int)
        for p in paths:
            for e in p.links():
                load[e] += m
        return load

    def port_load(self, paths: Iterable[Ring2Path], m: int = 1
                  ) -> tuple[dict[Any, int], dict[Any, int]]:
        board: dict[Any, int] = defaultdict(int)
        leave: dict[Any, int] = defaultdict(int)
        for p in paths:
            board[board_key(p.src, p.plane)] += m
            leave[leave_key(p.dst, p.plane)] += m
        return board, leave

    def analytic_bounds(self, req_paths: Sequence[Ring2Path],
                        resp_paths: Sequence[Ring2Path], *,
                        m_req: int = 1, m_resp: int = 4,
                        t_ha: int = 0) -> dict[str, Any]:
        """Lower bounds on makespan for a closed read-return batch.

        A request of m_req flits must finish before its response of m_resp
        flits can start. The bounds below ignore that pairing except for the
        serial 'one-hop + service + one-hop' floor on a single transaction;
        the resource bounds treat the two waves as sequential convoys.
        """
        req_load = self.link_load(req_paths, m_req)
        resp_load = self.link_load(resp_paths, m_resp)
        rb, rl = self.port_load(req_paths, m_req)
        sb, sl = self.port_load(resp_paths, m_resp)
        hop = self.hop_lat
        sig = self.sigma

        def _max_load(d: dict) -> int:
            return max(d.values()) if d else 0

        def _merge(*ds: dict) -> dict:
            out: dict = defaultdict(int)
            for d in ds:
                for k, v in d.items():
                    out[k] += v
            return out

        link_req = _max_load(req_load)
        link_resp = _max_load(resp_load)
        board_req = _max_load(rb)
        leave_req = _max_load(rl)
        board_resp = _max_load(sb)
        leave_resp = _max_load(sl)
        # Plane assignment is a policy, not a physical constraint: the same
        # directed hop can sit on either plane. A valid floor therefore
        # collapses planes and divides by n_planes. Using the load of one
        # particular `plane_sel` (e.g. greedy least_occupied) overshoots
        # whenever the simulator balances better at runtime (uniform K=20
        # R=8: one-plane peak 328 > S0 makespan 312).
        def _collapse_links(d: dict) -> dict:
            out: dict = defaultdict(int)
            for (plane, u, v), val in d.items():
                out[(u, v)] += val
            return out

        def _collapse_ports(d: dict) -> dict:
            out: dict = defaultdict(int)
            for key, val in d.items():
                out[(key[0], key[1])] += val   # (kind, node)
            return out

        link_lb = math.ceil(
            _max_load(_collapse_links(_merge(req_load, resp_load)))
            / max(1, self.n_planes)) * sig
        port_lb = math.ceil(
            _max_load(_collapse_ports(_merge(rb, sb, rl, sl)))
            / max(1, self.n_planes)) * sig
        # a single txn: hops_req*lat + m_req*sig + t_ha + hops_resp*lat + m_resp*sig
        if req_paths and resp_paths:
            single = (max(p.hops for p in req_paths) * hop + m_req * sig
                      + t_ha
                      + max(p.hops for p in resp_paths) * hop + m_resp * sig)
        else:
            single = 0
        # A ring bisection cuts TWO gaps (e.g. 9-10 and 19-0). Each gap has
        # 2 dirs x n_planes directed segments. Count actual path crossings;
        # the interleaved core/HA layout is mostly 1-hop, so a 50% assumption
        # overshoots (allpairs m=4 R=1: cut_lb 100 > S2 makespan 91).
        mid, wrap = self.n // 2, 0
        cut: set[Edge] = set()
        for p in range(self.n_planes):
            cut.add((p, mid - 1, mid))
            cut.add((p, mid, mid - 1))
            cut.add((p, self.n - 1, wrap))
            cut.add((p, wrap, self.n - 1))
        cut_load = 0
        for pth, mm in ((req_paths, m_req), (resp_paths, m_resp)):
            for path in pth:
                cut_load += mm * sum(1 for e in path.links() if e in cut)
        cut_cap = len(cut)
        cut_lb = math.ceil(cut_load / max(1, cut_cap)) * sig
        bound = max(link_lb, port_lb, single, cut_lb, hop + m_req * sig
                    + t_ha + hop + m_resp * sig)
        return {
            "link_req": link_req, "link_resp": link_resp,
            "board_req": board_req, "leave_req": leave_req,
            "board_resp": board_resp, "leave_resp": leave_resp,
            "link_lb": link_lb, "port_lb": port_lb, "cut_lb": cut_lb,
            "single_txn_lb": single, "bound": bound,
            "n_req_paths": len(req_paths), "n_resp_paths": len(resp_paths),
            "n_directed": len(self.directed_links),
            "n_undirected": len(self.undirected_links),
        }


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Txn:
    """One read transaction: 1 request flit, R response flits."""
    txn_id: int
    core: int
    ha: int
    m_req: int = 1
    m_resp: int = 4


def build_allpairs(*, m: int = 1, m_resp: int = 4, n: int = N_NODES
                   ) -> list[Txn]:
    """Every (core, HA) pair issues exactly `m` transactions. Deterministic."""
    cs, hs = cores(n), has(n)
    out: list[Txn] = []
    tid = 0
    for _ in range(m):
        for c in cs:
            for h in hs:
                out.append(Txn(tid, c, h, 1, m_resp))
                tid += 1
    return out


def build_uniform(*, k: int = 100, m_resp: int = 4, n: int = N_NODES,
                  seed: int = 0) -> list[Txn]:
    """Each core issues `k` transactions to a uniform-random HA."""
    import random
    cs, hs = cores(n), has(n)
    rng = random.Random(seed)
    out: list[Txn] = []
    tid = 0
    for c in cs:
        for _ in range(k):
            h = hs[rng.randrange(len(hs))]
            out.append(Txn(tid, c, h, 1, m_resp))
            tid += 1
    return out


def paths_for_txns(topo: Ring2Topology, txns: Sequence[Txn], *,
                   strategy: PlaneSel = "static_hash"
                   ) -> tuple[list[Ring2Path], list[Ring2Path]]:
    rr: dict[int, int] = {}
    occ: dict[int, int] = {}
    reqs, resps = [], []
    for t in txns:
        reqs.append(topo.fixed_path(t.core, t.ha, kind="req", txn_id=t.txn_id,
                                    strategy=strategy, rr_state=rr,
                                    occupancy=occ))
        resps.append(topo.fixed_path(t.ha, t.core, kind="resp",
                                     txn_id=t.txn_id, strategy=strategy,
                                     rr_state=rr, occupancy=occ))
    return reqs, resps


if __name__ == "__main__":
    topo = Ring2Topology()
    assert len(topo.directed_links) == 80
    assert len(topo.cores) == 10 and len(topo.has) == 10
    assert topo.make_path(0, 1, 0).hops == 1
    assert topo.make_path(0, 19, 0).hops == 1      # wrap
    assert topo.make_path(0, 10, 0).dir == 1       # tie -> CW
    tx = build_allpairs(m=1, m_resp=4)
    assert len(tx) == 100
    rp, sp = paths_for_txns(topo, tx)
    b = topo.analytic_bounds(rp, sp, m_req=1, m_resp=4)
    print(f"n={topo.n} planes={topo.n_planes} directed={len(topo.directed_links)}")
    print(f"allpairs m=1 R=4 bound={b['bound']} link_lb={b['link_lb']} "
          f"port_lb={b['port_lb']} cut_lb={b['cut_lb']}")
