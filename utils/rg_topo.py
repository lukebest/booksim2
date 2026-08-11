#!/usr/bin/env python3
"""8x6 mesh / folded 2D torus topology for the request-grant NoC study.

Metal-constant comparison:
  mesh:  82 undirected links @ 1 flit/cy, hop delay H=7 / V=9
  torus: 96 undirected links @ 0.5 flit/cy (sigma=2), same hop delay
         (bisection BW equal: 6 flit/cy vertical cut)

Torus dateline: 2 VC for bufferable; bufferless needs no VC.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Callable

MX, MY = 8, 6
N = MX * MY
H_BASE, V_BASE = 7, 9
RAMP, RAMP_BW = 2, 2

# E=0 W=1 N=2 S=3
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
DIR_NAMES = ("E", "W", "N", "S")


def nid(x: int, y: int, mx: int = MX) -> int:
    return x + mx * y


def coord(n: int, mx: int = MX) -> tuple[int, int]:
    return n % mx, n // mx


# Central scheduler placement: (x=4, y=0) — origin at TOP-LEFT, x first then y,
# i.e. the 5th column of the FIRST row. nid(4,0) = 4.
CA_X, CA_Y = 4, 0


def central_arbiter_node(mx: int = MX, my: int = MY) -> int:
    """CA placement: (4,0) — first row, 5th column (origin top-left)."""
    return nid(CA_X, CA_Y, mx)


class Topology:
    """Immutable topology view for mesh or folded torus."""

    def __init__(self, kind: str = "mesh", torus_delay_scale: int = 1):
        if kind not in ("mesh", "torus"):
            raise ValueError(f"unknown topology kind: {kind}")
        if torus_delay_scale not in (1, 2):
            raise ValueError("torus_delay_scale must be 1 or 2")
        self.kind = kind
        self.torus_delay_scale = torus_delay_scale
        self.mx, self.my, self.n = MX, MY, N
        self.sigma = 1 if kind == "mesh" else 2  # cycles per flit on a link
        scale = torus_delay_scale if kind == "torus" else 1
        self.H = H_BASE * scale
        self.V = V_BASE * scale
        self.num_vc = 1 if kind == "mesh" else 2
        self.adj = self._build_adj()
        self.undirected_links = self._undirected_links()
        self.directed_links = [(u, v) for u in self.adj for v in self.adj[u]]

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _neighbors(self, n: int) -> list[int]:
        x, y = coord(n)
        res = []
        if self.kind == "mesh":
            for dx, dy in DIRS:
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.mx and 0 <= ny < self.my:
                    res.append(nid(nx, ny))
        else:
            # folded torus: wrap in both dimensions
            for dx, dy in DIRS:
                nx = (x + dx) % self.mx
                ny = (y + dy) % self.my
                res.append(nid(nx, ny))
        return res

    def _build_adj(self) -> dict[int, list[int]]:
        return {n: sorted(self._neighbors(n)) for n in range(self.n)}

    def _undirected_links(self) -> list[frozenset[int]]:
        seen: set[frozenset[int]] = set()
        for u, nbs in self.adj.items():
            for v in nbs:
                seen.add(frozenset((u, v)))
        return sorted(seen, key=lambda e: tuple(sorted(e)))

    # ------------------------------------------------------------------
    # Geometry / latency
    # ------------------------------------------------------------------

    def is_horizontal(self, a: int, b: int) -> bool:
        ax, ay = coord(a)
        bx, by = coord(b)
        if self.kind == "mesh":
            return ay == by
        # torus: wrap hop is still "horizontal" if y matches (incl. wrap)
        if ay == by:
            return True
        if ax == bx:
            return False
        # should not happen for neighbors
        return abs((bx - ax) % self.mx) in (1, self.mx - 1)

    def link_lat(self, a: int, b: int) -> int:
        return self.H if self.is_horizontal(a, b) else self.V

    def dir_of(self, a: int, b: int) -> int:
        """Direction index from a to neighbor b (handles torus wrap)."""
        ax, ay = coord(a)
        bx, by = coord(b)
        if self.kind == "mesh":
            return DIRS.index((bx - ax, by - ay))
        dx = (bx - ax) % self.mx
        dy = (by - ay) % self.my
        if dx == 1:
            return 0  # E
        if dx == self.mx - 1:
            return 1  # W
        if dy == 1:
            return 2  # N
        if dy == self.my - 1:
            return 3  # S
        raise ValueError(f"not neighbors: {a}->{b}")

    def neighbor(self, node: int, d: int) -> int | None:
        x, y = coord(node)
        if self.kind == "mesh":
            nx, ny = x + DIRS[d][0], y + DIRS[d][1]
            if 0 <= nx < self.mx and 0 <= ny < self.my:
                nb = nid(nx, ny)
                if nb in self.adj.get(node, ()):
                    return nb
            return None
        nx = (x + DIRS[d][0]) % self.mx
        ny = (y + DIRS[d][1]) % self.my
        return nid(nx, ny)

    def path_wire_delay(self, nodes: list[int]) -> int:
        return sum(self.link_lat(nodes[i], nodes[i + 1])
                   for i in range(len(nodes) - 1))

    def hop_distance(self, a: int, b: int) -> tuple[int, int]:
        """Manhattan hop counts (hx, hy) under this topology."""
        ax, ay = coord(a)
        bx, by = coord(b)
        if self.kind == "mesh":
            return abs(bx - ax), abs(by - ay)
        dx = min((bx - ax) % self.mx, (ax - bx) % self.mx)
        dy = min((by - ay) % self.my, (ay - by) % self.my)
        return dx, dy

    def wire_distance(self, a: int, b: int) -> int:
        hx, hy = self.hop_distance(a, b)
        return hx * self.H + hy * self.V

    # ------------------------------------------------------------------
    # Control-plane latency: HALF the link-delay Manhattan distance.
    # Data plane keeps full H/V; request/grant pay ⌊wire/2⌋ end-to-end,
    # implemented hop-wise as ⌊link_lat/2⌋ (min 1) with a last-hop
    # correction so the path total equals exactly ⌊Σ link_lat / 2⌋.
    # ------------------------------------------------------------------

    def ctrl_wire_distance(self, a: int, b: int) -> int:
        """One-way control latency = ⌊Manhattan wire delay / 2⌋."""
        return self.wire_distance(a, b) // 2

    def ctrl_link_lat(self, a: int, b: int) -> int:
        """Per-hop control latency (⌊data link_lat / 2⌋, at least 1)."""
        return max(1, self.link_lat(a, b) // 2)

    def ctrl_path_hop_lats(self, path: list[int]) -> list[int]:
        """Per-hop control delays along `path`, summing to ctrl_wire_distance."""
        n = len(path) - 1
        if n <= 0:
            return []
        full = [self.link_lat(path[i], path[i + 1]) for i in range(n)]
        target = sum(full) // 2
        if target <= 0:
            return [0] * n
        # Start with floor-half; keep at least 1 when target allows.
        hops = [max(1, x // 2) for x in full]
        if sum(hops) > target:
            hops = [x // 2 for x in full]  # allow 0 on short hops
        diff = target - sum(hops)
        # Push remainder onto the last hop (can be negative → borrow).
        hops[-1] += diff
        if hops[-1] < 0:
            need = -hops[-1]
            hops[-1] = 0
            for i in range(n - 2, -1, -1):
                take = min(hops[i], need)
                hops[i] -= take
                need -= take
                if need == 0:
                    break
        return hops

    def diameter_wire(self) -> int:
        """Max pairwise wire delay."""
        best = 0
        for s in range(self.n):
            for d in range(s + 1, self.n):
                best = max(best, self.wire_distance(s, d))
        return best

    # ------------------------------------------------------------------
    # Routing: XY DOR (mesh) / DOR + dateline (torus)
    # ------------------------------------------------------------------

    def dor_path(self, src: int, dst: int) -> list[int]:
        """Dimension-order path: X then Y. Shortest wrap on torus."""
        if src == dst:
            return [src]
        sx, sy = coord(src)
        dx, dy = coord(dst)
        path = [src]
        x, y = sx, sy

        if self.kind == "mesh":
            step = 1 if dx > sx else -1
            while x != dx:
                x += step
                path.append(nid(x, y))
            step = 1 if dy > sy else -1
            while y != dy:
                y += step
                path.append(nid(x, y))
            return path

        # torus: choose shortest wrap direction
        def axis_steps(cur: int, tgt: int, size: int) -> list[int]:
            cw = (tgt - cur) % size
            ccw = (cur - tgt) % size
            if cw == 0:
                return []
            if cw < ccw or (cw == ccw and cw > 0):
                # prefer + direction on tie
                return [1] * cw
            return [-1] * ccw

        for step in axis_steps(x, dx, self.mx):
            x = (x + step) % self.mx
            path.append(nid(x, y))
        for step in axis_steps(y, dy, self.my):
            y = (y + step) % self.my
            path.append(nid(x, y))
        return path

    def vc_of_dateline(self, path: list[int], hop: int) -> int:
        """Torus per-dimension dateline (2 VC, DOR X-then-Y).

        In each dimension ring the dateline is the wrap channel in the
        direction of travel. A hop uses VC1 iff its own dimension-phase has
        already crossed (or is crossing) that dateline; starting the Y phase
        resets the rule. Mesh always returns 0.

        This is the classic Dally dateline: one-way X→Y deps + broken rings
        ⇒ 2 VCs suffice for deterministic DOR on a 2D torus.
        """
        if self.kind == "mesh" or hop >= len(path) - 1:
            return 0

        def is_x_hop(a: int, b: int) -> bool:
            return coord(a)[1] == coord(b)[1]

        def crosses_dateline(a: int, b: int) -> bool:
            ax, ay = coord(a)
            bx, by = coord(b)
            if ay == by:
                # X-ring: wrap E (mx-1→0) or W (0→mx-1)
                return (ax == self.mx - 1 and bx == 0) or (
                    ax == 0 and bx == self.mx - 1)
            # Y-ring: wrap N (my-1→0) or S (0→my-1)
            return (ay == self.my - 1 and by == 0) or (
                ay == 0 and by == self.my - 1)

        # Determine which dimension-phase the current hop belongs to, and
        # whether that phase has crossed a dateline up to and including hop.
        cur_a, cur_b = path[hop], path[hop + 1]
        cur_is_x = is_x_hop(cur_a, cur_b)
        crossed = False
        for i in range(hop + 1):
            a, b = path[i], path[i + 1]
            if is_x_hop(a, b) != cur_is_x:
                # different dimension phase — ignore (DOR: all X then all Y)
                if cur_is_x:
                    # still in X; Y hops shouldn't appear before
                    continue
                # in Y phase: skip X hops
                continue
            if crosses_dateline(a, b):
                crossed = True
                break
        # For the hop that IS the dateline cross, use VC0 on the dateline
        # channel itself and VC1 after — standard formulation uses VC0 for
        # the dateline channel (dependency breaks because nothing in VC0
        # depends on post-dateline VC0). We assign VC1 for hops strictly
        # AFTER a dateline cross in this phase.
        crossed_before = False
        for i in range(hop):
            a, b = path[i], path[i + 1]
            if is_x_hop(a, b) != cur_is_x:
                continue
            if crosses_dateline(a, b):
                crossed_before = True
                break
        return 1 if crossed_before else 0

    def all_pair_paths(self) -> dict[tuple[int, int], list[int]]:
        paths = {}
        for s in range(self.n):
            for d in range(self.n):
                if s == d:
                    continue
                paths[(s, d)] = self.dor_path(s, d)
        return paths

    def vc_of(self) -> Callable[[list[int], int], int] | None:
        if self.kind == "mesh":
            return None
        return self.vc_of_dateline

    # ------------------------------------------------------------------
    # CDG validation (local copy to avoid heavy pg_routing import side-effects)
    # ------------------------------------------------------------------

    def build_cdg(self, paths: dict[tuple[int, int], list[int]],
                  vc_of: Callable[[list[int], int], int] | None = None
                  ) -> dict[Any, set[Any]]:
        cdg: dict[Any, set[Any]] = defaultdict(set)
        for path in paths.values():
            if len(path) < 2:
                continue
            chans = []
            for i in range(len(path) - 1):
                e = (path[i], path[i + 1])
                vc = 0 if vc_of is None else vc_of(path, i)
                chans.append((e, vc))
            for i in range(len(chans) - 1):
                cdg[chans[i]].add(chans[i + 1])
                _ = cdg[chans[i + 1]]
        return cdg

    @staticmethod
    def cdg_acyclic(cdg: dict[Any, set[Any]]) -> bool:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in cdg}
        for n in list(cdg):
            for m in cdg[n]:
                color.setdefault(m, WHITE)

        def dfs(u):
            color[u] = GRAY
            for v in cdg.get(u, ()):
                if color.get(v, WHITE) == GRAY:
                    return False
                if color.get(v, WHITE) == WHITE and not dfs(v):
                    return False
            color[u] = BLACK
            return True

        return all(dfs(n) for n in list(color) if color[n] == WHITE)

    def validate_routing(self, paths: dict[tuple[int, int], list[int]]
                         ) -> tuple[bool, str]:
        vc_of = self.vc_of()
        for s in range(self.n):
            for d in range(self.n):
                if s == d:
                    continue
                if (s, d) not in paths:
                    return False, f"missing path {s}->{d}"
                p = paths[(s, d)]
                if not p or p[0] != s or p[-1] != d:
                    return False, f"bad endpoints {s}->{d}"
                for i in range(len(p) - 1):
                    if p[i + 1] not in self.adj.get(p[i], ()):
                        return False, f"edge missing {p[i]}-{p[i+1]}"
        cdg = self.build_cdg(paths, vc_of)
        if not self.cdg_acyclic(cdg):
            return False, "CDG has cycle"
        return True, "ok"

    # ------------------------------------------------------------------
    # Metal / bisection audit
    # ------------------------------------------------------------------

    def n_undirected_h(self) -> int:
        if self.kind == "mesh":
            return self.my * (self.mx - 1)  # 6*7=42
        return self.my * self.mx            # 6*8=48

    def n_undirected_v(self) -> int:
        if self.kind == "mesh":
            return self.mx * (self.my - 1)  # 8*5=40
        return self.mx * self.my            # 8*6=48

    def metal_units(self) -> float:
        """Relative metal: each undirected link costs 1 unit of length L.

        Mesh: 82 links of length L.
        Folded torus: 96 links of physical length 2L, but each carries half
        bandwidth (modeled as sigma=2). Under a 'half-width wire of 2L'
        interpretation metal = 96 * (L/2)*2 / L = 96 — see plan.
        We report both the naive link-count ratio and the equal-bisection
        accounting.
        """
        return float(len(self.undirected_links))

    def vertical_cut_links(self) -> int:
        """Number of undirected links crossing the mid vertical cut."""
        # cut between x=mx//2-1 and x=mx//2 for mesh; torus has both mid cuts
        mid = self.mx // 2
        count = 0
        for e in self.undirected_links:
            a, b = tuple(e)
            ax, ay = coord(a)
            bx, by = coord(b)
            if ay != by:
                continue
            xs = sorted([ax, bx])
            if self.kind == "mesh":
                if xs == [mid - 1, mid]:
                    count += 1
            else:
                # torus: two vertical cuts of equal width (wrap + mid)
                if xs == [mid - 1, mid] or xs == [0, self.mx - 1]:
                    count += 1
        return count

    def horizontal_cut_links(self) -> int:
        mid = self.my // 2
        count = 0
        for e in self.undirected_links:
            a, b = tuple(e)
            ax, ay = coord(a)
            bx, by = coord(b)
            if ax != bx:
                continue
            ys = sorted([ay, by])
            if self.kind == "mesh":
                if ys == [mid - 1, mid]:
                    count += 1
            else:
                if ys == [mid - 1, mid] or ys == [0, self.my - 1]:
                    count += 1
        return count

    def bisection_bw(self) -> float:
        """Directed flit/cycle across the tighter cut (one direction)."""
        # each undirected cut link contributes 1/sigma flit/cy per direction
        v = self.vertical_cut_links() / self.sigma
        h = self.horizontal_cut_links() / self.sigma
        return min(v, h)

    def audit(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "torus_delay_scale": self.torus_delay_scale,
            "n": self.n,
            "sigma": self.sigma,
            "H": self.H,
            "V": self.V,
            "num_vc": self.num_vc,
            "n_undirected": len(self.undirected_links),
            "n_undirected_h": self.n_undirected_h(),
            "n_undirected_v": self.n_undirected_v(),
            "metal_units": self.metal_units(),
            "vertical_cut_links": self.vertical_cut_links(),
            "horizontal_cut_links": self.horizontal_cut_links(),
            "bisection_bw": self.bisection_bw(),
            "diameter_wire": self.diameter_wire(),
            "ca_node": central_arbiter_node(),
            "ca_max_wire": max(self.wire_distance(central_arbiter_node(), n)
                               for n in range(self.n)),
            "ca_max_ctrl_wire": max(
                self.ctrl_wire_distance(central_arbiter_node(), n)
                for n in range(self.n)),
            "ctrl_delay_policy": "half_manhattan_linkdelay",
        }


def metal_ratio(mesh: Topology | None = None,
                torus: Topology | None = None) -> dict[str, Any]:
    mesh = mesh or Topology("mesh")
    torus = torus or Topology("torus")
    m_m = mesh.metal_units()
    m_t = torus.metal_units()
    return {
        "mesh_metal": m_m,
        "torus_metal": m_t,
        "ratio_torus_over_mesh": m_t / m_m,
        "mesh_bisection_bw": mesh.bisection_bw(),
        "torus_bisection_bw": torus.bisection_bw(),
        "bisection_equal": abs(mesh.bisection_bw() - torus.bisection_bw()) < 1e-9,
    }


def shortest_path_bfs(topo: Topology, src: int, dst: int) -> list[int]:
    """Unweighted BFS path (used for control-plane routing)."""
    if src == dst:
        return [src]
    prev: dict[int, int | None] = {src: None}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in topo.adj[u]:
            if v in prev:
                continue
            prev[v] = u
            if v == dst:
                path = [dst]
                while path[-1] != src:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                path.reverse()
                return path
            q.append(v)
    raise RuntimeError(f"disconnected: {src}->{dst}")


if __name__ == "__main__":
    import json
    for kind in ("mesh", "torus"):
        for scale in ((1,) if kind == "mesh" else (1, 2)):
            t = Topology(kind, torus_delay_scale=scale)
            paths = t.all_pair_paths()
            ok, msg = t.validate_routing(paths)
            audit = t.audit()
            audit["routing_ok"] = ok
            audit["routing_msg"] = msg
            print(json.dumps(audit, indent=2))
    print(json.dumps(metal_ratio(), indent=2))
