#!/usr/bin/env python3
"""20-node dual-plane bidirectional full ring (core / HA).

Topology
--------
20 nodes on two *independent* ring planes. Each plane is itself a bidirectional
full ring (node 19 adjacent to node 0). Every node has one inject/eject port
per plane; the two directions of a plane share that port and its buffer.

    even index  -> AI core          {0, 2, ..., 18}
    odd  index  -> memory Home Agent {1, 3, ..., 19}

Hop delay is per undirected edge (same both directions). Default
`RING2_LINK_LATS[i]` is the delay between node i and (i+1) mod 20;
the last entry is mem HA 19 ↔ core 0. Default routing is hop-count
shortest path (CW on a hop-count tie). `route="latency"` picks the
direction with the smaller sum of link delays, then fewer hops, then CW.

Protocol is AMBA CHI, restricted to non-cacheable non-snoopable reads
(`ReadNoSnp`): cores are RNs, HAs are completers. No SNP channel, no
cache-line state. One txn is 1 REQ flit plus R DAT flits (CompData).
The ring instantiates two independent CHI VCs (REQ and DAT); SNP/RSP
are omitted in this NC CompData closed set. Each VC has its own hop
occupancy (σ=1), so REQ and DAT may traverse the same directed hop
in the same cycle. Inject/leave ports stay one per (node, plane).

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
HOP_LAT = 2                            # nominal / fallback hop delay
# Undirected ring edges: link_lats[i] = delay between i and (i+1)%N.
# Last entry is mem HA 19 ↔ core 0.
RING2_LINK_LATS: tuple[int, ...] = (
    2, 2, 2, 3, 1, 3, 1, 1, 2, 4,
    1, 1, 3, 1, 3, 2, 2, 2, 3, 3,
)
SIGMA = 1
RAMP = 1

PlaneId = int                          # 0 | 1
Dir = int                              # +1 CW (increasing index) | -1 CCW
Edge = tuple[int, int, int]            # (plane, u, v)
Pair = tuple[int, int]
PlaneSel = Literal["static_hash", "rr_per_pkt", "least_occupied",
                   "req_resp_split"]
SpatialReuse = Literal["arc", "whole_ring"]
Kind = Literal["req", "resp", "dbid", "wdata", "comp", "retry", "pcrd"]
ChiVc = Literal["req", "rsp", "dat"]
# ReadNoSnp closed set: REQ carries requests, DAT carries CompData.
# SNP/RSP are not instantiated (no snoop; Comp is folded into CompData).
CHI_VCS: tuple[ChiVc, ...] = ("req", "dat")
# WriteNoSnp closed set needs a real RSP channel for DBIDResp and Comp.
CHI_VCS_WRITE: tuple[ChiVc, ...] = ("req", "rsp", "dat")

_VC_OF: dict[str, ChiVc] = {
    "req": "req",       # ReadNoSnp / WriteNoSnp request
    "resp": "dat",      # CompData (read)
    "dbid": "rsp",      # DBIDResp (write)
    "comp": "rsp",      # Comp (write)
    "wdata": "dat",     # WriteData (write)
    # Credit-based retry. A completer with no tracker entry rejects the
    # request with RetryAck and later hands out a PCrdGrant, at which point
    # the requester re-sends. Both are ordinary RSP-channel messages, so the
    # retry loop costs real RSP and REQ bandwidth rather than being free.
    "retry": "rsp",     # RetryAck
    "pcrd": "rsp",      # PCrdGrant
}


def vc_of(kind: Kind) -> ChiVc:
    return _VC_OF.get(kind, "dat")


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
                 spatial_reuse: SpatialReuse = "arc",
                 link_lats: Sequence[int] | None = None,
                 vcs: Sequence[str] | None = None,
                 route: Literal["hops", "latency"] = "hops"):
        if n < 4 or n % 2:
            raise ValueError("n must be even and >= 4")
        if n_planes < 1:
            raise ValueError("n_planes")
        if spatial_reuse not in ("arc", "whole_ring"):
            raise ValueError(spatial_reuse)
        self.n = n
        self.n_planes = n_planes
        self.hop_lat = hop_lat
        if link_lats is None:
            link_lats = RING2_LINK_LATS if n == N_NODES else (hop_lat,) * n
        if len(link_lats) != n:
            raise ValueError(f"link_lats must have {n} entries")
        self.link_lats = tuple(int(x) for x in link_lats)
        if any(x < 1 for x in self.link_lats):
            raise ValueError("link_lats must be >= 1")
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
        self.vcs: tuple[str, ...] = tuple(vcs) if vcs else CHI_VCS
        self.n_vc = len(self.vcs)
        if route not in ("hops", "latency"):
            raise ValueError(route)
        self.route = route
        self.directed_links: list[Edge] = self._directed_links()
        self.undirected_links = sorted(
            {(p, min(u, v), max(u, v)) for p, u, v in self.directed_links})

    @property
    def hop_bw_cap(self) -> int:
        """Directed hops × independent CHI VCs (REQ+DAT, +RSP for writes)."""
        return len(self.directed_links) * self.n_vc

    def _directed_links(self) -> list[Edge]:
        out: list[Edge] = []
        for p in range(self.n_planes):
            for i in range(self.n):
                j = (i + 1) % self.n
                out.append((p, i, j))
                out.append((p, j, i))
        return out

    def hop_lat_from(self, node: int, direction: Dir) -> int:
        """Travel time of the outgoing hop from `node` in `direction`."""
        if direction > 0:
            return self.link_lats[node % self.n]
        return self.link_lats[(node - 1) % self.n]

    def link_lat(self, u: int, v: int) -> int:
        """Delay of the undirected edge between adjacent nodes u and v."""
        if v == (u + 1) % self.n:
            return self.link_lats[u]
        if u == (v + 1) % self.n:
            return self.link_lats[v]
        raise ValueError(f"not adjacent: {u}, {v}")

    def choose_dir(self, src: int, dst: int) -> Dir:
        """Direction used when a caller does not pin one.

        `route="hops"` is hop-count (CW on a hop tie). `route="latency"`
        is the smaller sum of link delays, then fewer hops, then CW.
        The standalone `shortest_dir` helper stays hop-count.
        """
        if src == dst:
            return 1
        if self.route != "latency":
            return shortest_dir(src, dst, self.n)
        cw, ccw = self.path_lat(src, dst, 1), self.path_lat(src, dst, -1)
        if cw < ccw:
            return 1
        if ccw < cw:
            return -1
        return shortest_dir(src, dst, self.n)

    def path_lat(self, src: int, dst: int, direction: Dir | None = None
                 ) -> int:
        """Sum of link delays along the (chosen, or given) path."""
        if src == dst:
            return 0
        d = self.choose_dir(src, dst) if direction is None else direction
        hops = hop_count(src, dst, d, self.n)
        acc = 0
        node = src
        for _ in range(hops):
            acc += self.hop_lat_from(node, d)
            node = (node + d) % self.n
        return acc

    def remaining_lat(self, node: int, direction: Dir, hops: int) -> int:
        """Sum of the next `hops` outgoing delays from `node`."""
        acc = 0
        cur = node
        for _ in range(max(0, hops)):
            acc += self.hop_lat_from(cur, direction)
            cur = (cur + direction) % self.n
        return acc

    def ring_nodes(self, plane: PlaneId) -> list[int]:
        return list(range(self.n))

    def make_path(self, src: int, dst: int, plane: PlaneId,
                  direction: Dir | None = None) -> Ring2Path:
        if src == dst:
            raise ValueError("src == dst")
        d = self.choose_dir(src, dst) if direction is None else direction
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
            _p, u, v = e
            acc += self.link_lat(u, v)
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
        min_hop = min(self.link_lats) if self.link_lats else hop

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

        # CHI VCs are independent: REQ and DAT do not share hop occupancy.
        # Port bounds still merge kinds — inject/leave stay 1 per (node, plane).
        link_lb = max(
            math.ceil(_max_load(_collapse_links(req_load))
                      / max(1, self.n_planes)),
            math.ceil(_max_load(_collapse_links(resp_load))
                      / max(1, self.n_planes)),
        ) * sig
        port_lb = math.ceil(
            _max_load(_collapse_ports(_merge(rb, sb, rl, sl)))
            / max(1, self.n_planes)) * sig
        # a single txn: hops_req*lat + m_req*sig + t_ha + hops_resp*lat + m_resp*sig
        if req_paths and resp_paths:
            single = (max(self.path_lat(p.src, p.dst, p.dir)
                          for p in req_paths) + m_req * sig
                      + t_ha
                      + max(self.path_lat(p.src, p.dst, p.dir)
                            for p in resp_paths) + m_resp * sig)
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
        def _cut_load(paths, mm) -> int:
            return sum(mm * sum(1 for e in path.links() if e in cut)
                       for path in paths)
        cut_cap = len(cut)
        cut_lb = max(
            math.ceil(_cut_load(req_paths, m_req) / max(1, cut_cap)),
            math.ceil(_cut_load(resp_paths, m_resp) / max(1, cut_cap)),
        ) * sig
        bound = max(link_lb, port_lb, single, cut_lb, min_hop + m_req * sig
                    + t_ha + min_hop + m_resp * sig)
        return {
            "link_req": link_req, "link_resp": link_resp,
            "board_req": board_req, "leave_req": leave_req,
            "board_resp": board_resp, "leave_resp": leave_resp,
            "link_lb": link_lb, "port_lb": port_lb, "cut_lb": cut_lb,
            "single_txn_lb": single, "bound": bound,
            "n_req_paths": len(req_paths), "n_resp_paths": len(resp_paths),
            "n_directed": len(self.directed_links),
            "n_undirected": len(self.undirected_links),
            "n_vc": self.n_vc,
            "hop_bw_cap": self.hop_bw_cap,
        }


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Txn:
    """One CHI transaction, non-cacheable and non-snoopable.

    `op == "read"`  — ReadNoSnp: 1 REQ core→HA, then `m_resp` DAT (CompData)
                      HA→core. Two VCs, one round trip.
    `op == "write"` — WriteNoSnp: 1 REQ core→HA, 1 DBIDResp HA→core,
                      `m_wdata` WriteData core→HA, 1 Comp HA→core. Three VCs,
                      two serial round trips.
    """
    txn_id: int
    core: int
    ha: int
    m_req: int = 1
    m_resp: int = 4
    op: Literal["read", "write"] = "read"
    m_wdata: int = 0


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


def build_uniform_write(*, k: int = 100, m_wdata: int = 4, n: int = N_NODES,
                        seed: int = 0, mem: Sequence[int] | None = None,
                        core_set: Sequence[int] | None = None) -> list[Txn]:
    """Each core issues `k` WriteNoSnp txns to a uniform-random memory node.

    `mem` overrides which nodes are memory. It matters more than it looks:
    with all ten odd nodes as memory the destination set is rotationally
    symmetric, so every core sees a statistically identical view of the ring
    and uniform traffic is fair by construction. Drop any memory node and
    that symmetry is gone — cores near the gap route differently from cores
    far from it, and the position dependence shows up without any hotspot.
    """
    import random
    cs = list(core_set) if core_set is not None else cores(n)
    hs = list(mem) if mem is not None else has(n)
    rng = random.Random(seed)
    out: list[Txn] = []
    tid = 0
    for c in cs:
        for _ in range(k):
            h = hs[rng.randrange(len(hs))]
            out.append(Txn(tid, c, h, 1, 0, "write", m_wdata))
            tid += 1
    return out


def build_hot_write(*, k: int = 100, m_wdata: int = 4,
                    hot_has: Sequence[int] = (9, 11), n: int = N_NODES
                    ) -> list[Txn]:
    """Every core writes into a clustered memory region (`hot_has`).

    Two adjacent memory HAs stand in for one memory stack shared by all AI
    cores. Shortest-path routing then funnels five cores' traffic through the
    single directed hop that enters the cluster from each side, which is what
    makes a core's achieved bandwidth depend on where it sits: the core next
    to the cluster injects into a hop that is already full of everyone else's
    flits, while the core farthest away injects into a private one.
    """
    out: list[Txn] = []
    tid = 0
    for c in cores(n):
        for i in range(k):
            out.append(Txn(tid, c, hot_has[i % len(hot_has)], 1, 0,
                           "write", m_wdata))
            tid += 1
    return out


def build_hot_read(*, k: int = 100, m_resp: int = 2,
                   hot_has: Sequence[int] = (11, 13), n: int = N_NODES
                   ) -> list[Txn]:
    """Every core reads from a clustered memory region (`hot_has`).

    Same geometry as `build_hot_write`, inverted: the bulky traffic is now
    CompData leaving the two HAs rather than WriteData entering them. The
    binding resource moves from the cluster's down-ring ports onto its
    up-ring ports, but the fan-in (ten cores, two HAs) is the same.
    """
    out: list[Txn] = []
    tid = 0
    for c in cores(n):
        for i in range(k):
            out.append(Txn(tid, c, hot_has[i % len(hot_has)], 1, m_resp))
            tid += 1
    return out


# Write stimulus used by the fairness report: 128B bursts, 4KB stride,
# 64KB tiles. One CHI WriteData flit is one 64B beat, so a burst is 2 flits.
FLIT_BYTES = 64
BURST_BYTES = 128
STRIDE_BYTES = 4096
TILE_BYTES = 65536
BURST_FLITS = BURST_BYTES // FLIT_BYTES


def interleave_ha(addr: int, mem: Sequence[int]) -> int:
    """4KB-grain 8-way interleave, offset by the core so cores do not lockstep.

    `(addr // 128) % 8` aliases: 4096/128 = 32 ≡ 0 (mod 8). Mapping on the
    4KB line index does not. Adding the core high bits (`addr >> 20`) keeps
    the ten address streams from landing on the same HA in the same step.
    """
    line = addr // STRIDE_BYTES
    core = addr >> 20
    return mem[(line + core) % len(mem)]


def build_tiled_write(*, k: int = 100, m_wdata: int = BURST_FLITS,
                      n: int = N_NODES, mem: Sequence[int] | None = None,
                      core_set: Sequence[int] | None = None) -> list[Txn]:
    """Per-core tiled write: burst 128B, stride 4KB, tile 64KB.

    Each core has its own address space (`core << 20`) so ten cores do not
    lockstep onto the same HA. Within a core the walk is

        addr = tile * 64KB + (i % 16) * 4KB

    and `interleave_ha` spreads those lines across `mem`.
    """
    cs = list(core_set) if core_set is not None else cores(n)
    hs = list(mem) if mem is not None else has(n)
    lines = TILE_BYTES // STRIDE_BYTES
    out: list[Txn] = []
    tid = 0
    for c in cs:
        for i in range(k):
            tile, line = divmod(i, lines)
            addr = (c << 20) + tile * TILE_BYTES + line * STRIDE_BYTES
            out.append(Txn(tid, c, interleave_ha(addr, hs), 1, 0,
                           "write", m_wdata))
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


def write_paths_for_txns(topo: Ring2Topology, txns: Sequence[Txn], *,
                         strategy: PlaneSel = "static_hash"
                         ) -> dict[str, list[Ring2Path]]:
    """Per-CHI-VC path lists for a WriteNoSnp batch.

    REQ and DAT run core→HA, RSP runs HA→core. Each txn contributes exactly
    one path to each VC; the flit multiplicity per VC is supplied separately
    to `write_bounds` (REQ 1, RSP 2 = DBIDResp + Comp, DAT `m_wdata`).
    """
    rr: dict[int, int] = {}
    occ: dict[int, int] = {}
    out: dict[str, list[Ring2Path]] = {"req": [], "rsp": [], "dat": []}
    for t in txns:
        out["req"].append(topo.fixed_path(
            t.core, t.ha, kind="req", txn_id=t.txn_id, strategy=strategy,
            rr_state=rr, occupancy=occ))
        out["rsp"].append(topo.fixed_path(
            t.ha, t.core, kind="dbid", txn_id=t.txn_id, strategy=strategy,
            rr_state=rr, occupancy=occ))
        out["dat"].append(topo.fixed_path(
            t.core, t.ha, kind="wdata", txn_id=t.txn_id, strategy=strategy,
            rr_state=rr, occupancy=occ))
    return out


def write_bounds(topo: Ring2Topology, vc_paths: dict[str, list[Ring2Path]], *,
                 m_req: int = 1, m_rsp: int = 2, m_wdata: int = 4,
                 t_ha: int = 0, merge_port_vcs: bool = True) -> dict[str, Any]:
    """Lower bounds on makespan for a closed WriteNoSnp batch.

    Independent CHI VCs mean the hop-occupancy floor is the *max* over VCs,
    not the sum. When `merge_port_vcs` the inject / leave floor still stacks
    every VC onto one (node, plane) port; when false each VC has its own
    port and `port_lb` is the max over (kind, node, vc). `LB_txn` is a
    two-round-trip serial chain: REQ → DBIDResp → WriteData → Comp.
    """
    sig = topo.sigma
    mult = {"req": m_req, "rsp": m_rsp, "dat": m_wdata}
    n_planes = max(1, topo.n_planes)

    def _max(d: dict) -> int:
        return max(d.values()) if d else 0

    def _collapse_links(d: dict) -> dict:
        out: dict = defaultdict(int)
        for (_plane, u, v), val in d.items():
            out[(u, v)] += val
        return out

    link_by_vc: dict[str, int] = {}
    port_merged: dict = defaultdict(int)
    for vc, paths in vc_paths.items():
        m = mult[vc]
        link_by_vc[vc] = math.ceil(
            _max(_collapse_links(topo.link_load(paths, m))) / n_planes) * sig
        b, l = topo.port_load(paths, m)
        for d in (b, l):
            for key, val in d.items():
                pk = ((key[0], key[1]) if merge_port_vcs
                      else (key[0], key[1], vc))
                port_merged[pk] += val
    link_lb = max(link_by_vc.values()) if link_by_vc else 0
    port_lb = math.ceil(_max(port_merged) / n_planes) * sig

    mid, wrap = topo.n // 2, 0
    cut: set[Edge] = set()
    for p in range(topo.n_planes):
        cut.add((p, mid - 1, mid))
        cut.add((p, mid, mid - 1))
        cut.add((p, topo.n - 1, wrap))
        cut.add((p, wrap, topo.n - 1))
    cut_by_vc = {
        vc: math.ceil(sum(mult[vc] * sum(1 for e in path.links() if e in cut)
                          for path in paths) / max(1, len(cut))) * sig
        for vc, paths in vc_paths.items()
    }
    cut_lb = max(cut_by_vc.values()) if cut_by_vc else 0

    def _leg(vc: str) -> int:
        ps = vc_paths.get(vc) or []
        return max((topo.path_lat(p.src, p.dst, p.dir) for p in ps), default=0)

    # REQ → (HA service) → DBIDResp → WriteData ×W → (HA service) → Comp
    txn_lb = (_leg("req") + m_req * sig + t_ha
              + _leg("rsp") + sig
              + _leg("dat") + m_wdata * sig + t_ha
              + _leg("rsp") + sig)
    bound = max(link_lb, port_lb, cut_lb, txn_lb)
    pairs_on_hop: dict[tuple[int, int], set] = defaultdict(set)
    for p in vc_paths.get("dat") or []:
        for e in p.links():
            pairs_on_hop[(e[1], e[2])].add((p.src, p.dst))
    n_hot_dat = max((len(s) for s in pairs_on_hop.values()), default=0)
    hot_hops_dat = sorted(
        f"{u}→{v}" for (u, v), s in pairs_on_hop.items()
        if len(s) == n_hot_dat and n_hot_dat)
    # Busiest segment of every VC, and the node each one leaves from. An
    # injector at that tail has to share the very hop the bound wants full.
    hot_hops_by_vc: dict[str, list[str]] = {}
    hot_tails_by_vc: dict[str, list[int]] = {}
    for vc, paths in vc_paths.items():
        load = _collapse_links(topo.link_load(paths, mult[vc]))
        if not load:
            continue
        top = max(load.values())
        hot = sorted(e for e, val in load.items() if val == top)
        hot_hops_by_vc[vc] = [f"{u}→{v}" for u, v in hot]
        hot_tails_by_vc[vc] = sorted({u for u, _v in hot})
    return {
        "link_by_vc": link_by_vc, "cut_by_vc": cut_by_vc,
        "link_lb": link_lb, "port_lb": port_lb, "cut_lb": cut_lb,
        "txn_lb": txn_lb, "bound": bound,
        "merge_port_vcs": merge_port_vcs,
        "n_txn": len(vc_paths.get("req") or []),
        "n_vc": topo.n_vc, "hop_bw_cap": topo.hop_bw_cap,
        "m_req": m_req, "m_rsp": m_rsp, "m_wdata": m_wdata,
        "n_hot_dat": n_hot_dat, "hot_hops_dat": hot_hops_dat,
        "hot_hops_by_vc": hot_hops_by_vc, "hot_tails_by_vc": hot_tails_by_vc,
        "route": topo.route,
    }


if __name__ == "__main__":
    topo = Ring2Topology()
    assert len(topo.directed_links) == 80
    assert len(topo.cores) == 10 and len(topo.has) == 10
    assert topo.make_path(0, 1, 0).hops == 1
    assert topo.make_path(0, 19, 0).hops == 1      # wrap
    assert topo.make_path(0, 10, 0).dir == 1       # tie -> CW
    assert topo.link_lats[0] == 2 and topo.link_lats[19] == 3
    assert topo.hop_lat_from(0, 1) == 2
    assert topo.hop_lat_from(0, -1) == 3           # 19 ↔ 0
    assert topo.path_lat(0, 1) == 2
    assert topo.path_lat(0, 19) == 3
    tx = build_allpairs(m=1, m_resp=4)
    assert len(tx) == 100
    rp, sp = paths_for_txns(topo, tx)
    b = topo.analytic_bounds(rp, sp, m_req=1, m_resp=4)
    print(f"n={topo.n} planes={topo.n_planes} directed={len(topo.directed_links)}")
    print(f"allpairs m=1 R=4 bound={b['bound']} link_lb={b['link_lb']} "
          f"port_lb={b['port_lb']} cut_lb={b['cut_lb']}")
