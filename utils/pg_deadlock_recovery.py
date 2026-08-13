#!/usr/bin/env python3
"""Deadlock RECOVERY (not avoidance) on the 8x6 partial-good mesh.

Routing is the baseline XY table with its deadlock-freedom property dropped:
strict XY wherever the L-shaped path survives, minimal detour around the
fault otherwise.  That table sacrifices no compute node as long as the
residual graph is connected, but its CDG is cyclic -- so a recovery
mechanism has to clean up after it.  Three published mechanisms are modelled
on top of the very same path table:

  SB    Static Bubble  -- Ramrakhyani & Krishna, HPCA'17, pp. 253-264
  SPIN  Synchronized Progress in Interconnection Networks
                       -- Ramrakhyani, Gratz & Krishna, ISCA'18, pp. 699-711
  SWAP  Synchronized Weaving of Adjacent Packets
                       -- Parasar, Enright Jerger, Gratz, San Miguel &
                          Krishna, MICRO'19, pp. 873-885

The simulator is a fork of dse_pg_alltoall_8x6.simulate_alltoall: same
credit/FIFO/arbitration engine, same H=7 / V=9 wire delays, plus per-buffer
blocked timers, an exact single-VC dependency-chain probe, and the three
recovery actions.  selftest() asserts the fork reproduces the parent DES on
deadlock-free inputs, so recovery numbers are comparable to the avoidance
numbers in results/pg_e2e_pareto.json.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

import pg_faults_8x6 as F
import pg_routing as R
from dse_pg_alltoall_8x6 import (
    DEFAULT_Q, INJ, STALL_LIMIT, T_MAX, Flit,
)

MX, MY, N = F.MX, F.MY, F.N
DIRS = R.DIRS
RAMP, RAMP_BW = R.RAMP, R.RAMP_BW

# --------------------------------------------------------------------------
# Mechanism parameters, all taken from the three papers
# --------------------------------------------------------------------------

# Static Bubble, HPCA'17 Table II: t_DD = 34 cycles.
SB_T_DD = 34
# SPIN, ISCA'18 Sec. VI-A: "The default value of t_DD is 128".
SPIN_T_DD = 128
# SWAP, MICRO'19 Eq. 1: swapCycle = (cycle/m) % (K*N) == router_id, K >= 1.
SWAP_K = 1
# SWAP Sec. 4.2.1: req -> check -> ack -> swap.
SWAP_HANDSHAKE = 4

RECOVERY_KINDS = ("sb", "spin", "swap")


def sb_positions(mx: int = MX, my: int = MY) -> list[int]:
    """Static-Bubble placement rule (HPCA'17 Sec. III).

    A bubble is added at (x, y) with x > 0 and y > 0 when
    x%4 == y%4, or (x%4, y%4) == (1, 3), or (x%4, y%4) == (3, 1).
    Gives 21 routers on 8x8 and 89 on 16x16, matching the paper; 15 on 8x6.
    """
    out = []
    for x in range(mx):
        for y in range(my):
            if x == 0 or y == 0:
                continue
            a, b = x % 4, y % 4
            if a == b or (a, b) == (1, 3) or (a, b) == (3, 1):
                out.append(F.nid(x, y))
    return sorted(out)


SB_NODES = frozenset(sb_positions())


# --------------------------------------------------------------------------
# Routing: baseline XY, detour only where the fault broke it
# --------------------------------------------------------------------------

def _xy_productive(u: int, v: int, dst: int) -> bool:
    ux, uy = F.coord(u)
    vx, vy = F.coord(v)
    dx, dy = F.coord(dst)
    if ux != dx:
        return vy == uy and abs(dx - vx) < abs(dx - ux)
    return vx == ux and abs(dy - vy) < abs(dy - uy)


def xy_detour_paths(adj: dict[int, list[int]], compute: list[int],
                    penalty: float = 64.0) -> dict[str, Any] | None:
    """XY where it survives, minimum-deviation shortest path elsewhere.

    No turn restriction is imposed, so a path exists for every connected
    pair -- the price is a cyclic CDG.  Deviation is priced above any wire
    delay so an intact XY L-path always wins.
    """
    paths: dict[tuple[int, int], list[int]] = {}
    n_detour = 0
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = R.xy_path(s, d, adj)
            if p is None:
                def w(u: int, v: int, _d: int = d) -> float:
                    return R.link_lat(u, v) + (
                        0.0 if _xy_productive(u, v, _d) else penalty)
                p = R.dijkstra_path(s, d, adj, w)
                if p is None:
                    return None
                n_detour += 1
            paths[(s, d)] = p
    return {"paths": paths, "n_detour_pairs": n_detour}


def _balance(paths: dict[tuple[int, int], list[int]],
             adj: dict[int, list[int]],
             fixed: set[tuple[int, int]] = frozenset(),
             rounds: int = 14, hot_k: int = 96,
             beta: float = 24.0, seed: int = 0
             ) -> dict[tuple[int, int], list[int]]:
    """Min-max link-load refinement, same loop as R.load_balance_paths.

    Difference: no CDG re-validation, because in the recovery premise the
    turn set is unrestricted by construction -- that is exactly the freedom
    a recovery mechanism buys.  `fixed` pairs are never re-routed (used to
    keep an Up*/Down* core intact while only the illegal pairs float).
    """
    import random
    rng = random.Random(seed)
    paths = dict(paths)
    best, best_load = dict(paths), R.max_link_load(paths)
    movable = [k for k in paths if k not in fixed]
    for _ in range(rounds):
        loads: dict[tuple[int, int], int] = defaultdict(int)
        for p in paths.values():
            for i in range(len(p) - 1):
                loads[(p[i], p[i + 1])] += 1
        scored = []
        for key in movable:
            p = paths[key]
            sc = max((loads[(p[i], p[i + 1])] for i in range(len(p) - 1)),
                     default=0)
            scored.append((sc, rng.random(), key))
        scored.sort(reverse=True)
        improved = False
        for _sc, _r, key in scored[:hot_k]:
            s, d = key
            old = paths[key]
            for i in range(len(old) - 1):
                loads[(old[i], old[i + 1])] -= 1

            def w(u: int, v: int, _l=loads) -> float:
                return R.link_lat(u, v) + beta * _l[(u, v)]

            new = R.dijkstra_path(s, d, adj, w) or old
            paths[key] = new
            for i in range(len(new) - 1):
                loads[(new[i], new[i + 1])] += 1
            improved = improved or new != old
        cur = R.max_link_load(paths)
        if cur < best_load:
            best, best_load = dict(paths), cur
        if not improved:
            break
    return best


def minmax_paths(adj: dict[int, list[int]], compute: list[int]
                 ) -> dict[str, Any] | None:
    """R1: unrestricted min-max routing -- optimal fault avoidance, no turns.

    Start from wire-delay shortest paths (no turn model at all), then push
    the peak link load down.  This is the routing a recovery-based design
    would actually pick: reachability and load are the only objectives left
    once deadlock freedom is delegated to the mechanism.
    """
    paths: dict[tuple[int, int], list[int]] = {}
    for s in compute:
        for d in compute:
            if s == d:
                continue
            p = R.dijkstra_path(s, d, adj, R.link_lat)
            if p is None:
                return None
            paths[(s, d)] = p
    return {"paths": _balance(paths, adj), "n_detour_pairs": len(paths)}


def updown_relax_paths(adj: dict[int, list[int]], compute: list[int]
                       ) -> dict[str, Any] | None:
    """R2: M3' Up*/Down* core + illegal-turn completion for the rest.

    Picks the best-root height function exactly like R.gen_updown_best_root
    (min peak load, then hops), but instead of sacrificing the nodes whose
    pairs Up*/Down* cannot serve, it routes those pairs freely.  Only those
    few paths can close a CDG cycle, so the recovery mechanism is armed but
    rarely fires, while >99% of the traffic keeps M3's balanced tree paths.
    """
    best = None
    for root in sorted(adj.keys()):
        labels = R._updown_labels(adj, root)
        if labels is None:
            continue
        paths: dict[tuple[int, int], list[int]] = {}
        free: set[tuple[int, int]] = set()
        for s in compute:
            for d in compute:
                if s == d:
                    continue
                p = R._tree_path(s, d, adj, labels, "ud")
                if p is None:
                    p = R.dijkstra_path(s, d, adj, R.link_lat)
                    if p is None:
                        break
                    free.add((s, d))
                paths[(s, d)] = p
            else:
                continue
            break
        else:
            key = (len(free), R.max_link_load(paths),
                   sum(len(p) - 1 for p in paths.values()), root)
            if best is None or key < best[0]:
                best = (key, root, paths, free)
    if best is None:
        return None
    _key, root, paths, free = best
    # Only the illegal pairs may move: the Up*/Down* core must stay a tree
    # routing, otherwise the "rarely fires" property is lost.
    return {"paths": _balance(paths, adj, fixed=set(paths) - free),
            "root": root, "n_free_pairs": len(free),
            "n_detour_pairs": len(free)}


def cdg_cycle_channels(cdg: dict[Any, set[Any]]) -> int:
    """Channels that lie on at least one CDG cycle (Tarjan SCC, iterative).

    A finer measure than the acyclic/cyclic bit: it says how much of the
    network is actually exposed to deadlock, which is what decides how often
    a recovery mechanism has to fire.
    """
    index: dict[Any, int] = {}
    low: dict[Any, int] = {}
    on_stack: set[Any] = set()
    stack: list[Any] = []
    counter = 0
    out = 0
    for root in list(cdg):
        if root in index:
            continue
        work: list[tuple[Any, Any]] = [(root, iter(cdg.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(cdg.get(w, ()))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1 or v in cdg.get(v, ()):
                    out += len(comp)
    return out


# --------------------------------------------------------------------------
# Routing: Glass-Ni super-turn table run on ONE physical VC
# --------------------------------------------------------------------------

SUPER_DUALS = [
    ("east_west", ["east_first", "west_first"]),
    ("north_south", ["north_last", "south_last"]),
    ("east_north", ["east_first", "north_last"]),
    ("east_south", ["east_first", "south_last"]),
    ("west_north", ["west_first", "north_last"]),
    ("west_south", ["west_first", "south_last"]),
]


def _turn_paths_1vc(adj: dict[int, list[int]], compute: list[int],
                    models: list[str]
                    ) -> tuple[dict[tuple[int, int], list[int]],
                               set[tuple[int, int]]] | None:
    """Shortest path allowed by any of `models`, free path if none is."""
    oks = [R._turn_ok_factory(R._TURN_MODELS[n]) for n in models]
    paths: dict[tuple[int, int], list[int]] = {}
    free: set[tuple[int, int]] = set()
    for s in compute:
        for d in compute:
            if s == d:
                continue
            best = None
            for ok in oks:
                p = R._pick_turn_path(s, d, adj, ok)
                if p is not None and (best is None or len(p) < len(best)):
                    best = p
            if best is None:
                best = R.dijkstra_path(s, d, adj, R.link_lat)
                if best is None:
                    return None
                free.add((s, d))
            paths[(s, d)] = best
    return paths, free


def super_turn_1vc_paths(adj: dict[int, list[int]], compute: list[int]
                         ) -> dict[str, Any] | None:
    """R3: M0s super-turn's turn set, but on a single physical VC.

    gen_super_turn keeps its two Glass-Ni layers on separate VCs so each
    layer's CDG stays acyclic.  Here both layers share one VC: the turn set
    is the union, which is far more flexible (and shorter-path) than any
    single model or than Up*/Down*, at the price of a few CDG cycles that a
    recovery mechanism has to clean up.  Candidates are the 4 single models
    (already 1-VC legal when they cover) and the 6 duals; the pick minimises
    (pairs needing an out-of-model path, channels on a cycle, peak load,
    hops), i.e. it buys flexibility only where it stays nearly acyclic.
    """
    best = None
    for tag, models in ([(n, [n]) for n in R._TURN_MODELS] + SUPER_DUALS):
        built = _turn_paths_1vc(adj, compute, models)
        if built is None:
            continue
        paths, free = built
        cyc = cdg_cycle_channels(R.build_cdg(paths))
        key = (len(free), cyc, R.max_link_load(paths),
               sum(len(p) - 1 for p in paths.values()), tag)
        if best is None or key < best[0]:
            best = (key, tag, models, paths, free)
    if best is None:
        return None
    _key, tag, models, paths, free = best
    # Balance only the out-of-model pairs, for the same reason as R2: the
    # in-model core is what keeps the cycle count low.
    return {"paths": _balance(paths, adj, fixed=set(paths) - free),
            "turn_mode": tag, "turn_layers": list(models),
            "n_free_pairs": len(free), "n_detour_pairs": len(free)}


ROUTINGS = {
    "xy_detour": xy_detour_paths,
    "minmax": minmax_paths,
    "updown_relax": updown_relax_paths,
    "super_turn_1vc": super_turn_1vc_paths,
}


def solve_routing(pg: dict, routing: str = "xy_detour") -> dict[str, Any]:
    """Path table + sacrifice bookkeeping, mirroring R.solve_scheme output.

    Only nodes that the residual graph cannot reach are given up; there is
    no turn-restriction-driven sacrifice ladder for any of the routings here.
    """
    adj = pg["route_adj"]
    compute = list(pg["compute_nodes"])
    keep = compute
    sac: list[int] = []
    if compute:
        seed = max(compute, key=lambda n: len(R.bfs_reachable(adj, n))
                   if n in adj else 0)
        reach = R.bfs_reachable(adj, seed) if seed in adj else set()
        keep = [n for n in compute if n in reach]
        sac = [n for n in compute if n not in reach]
    if len(keep) < 2:
        return {"feasible": False, "reason": "residual graph disconnected",
                "n_sacrificed": len(sac), "sacrificed": sac,
                "n_compute_used": len(keep),
                "n_originally_good": pg["n_originally_good"]}
    raw = ROUTINGS[routing](adj, keep)
    if raw is None:
        return {"feasible": False, "reason": "no path", "sacrificed": sac,
                "n_sacrificed": len(sac), "n_compute_used": len(keep),
                "n_originally_good": pg["n_originally_good"]}
    cdg = R.build_cdg(raw["paths"])
    return {
        "feasible": True,
        "scheme": routing,
        "routing": routing,
        "paths": raw["paths"],
        "vc_of": None,
        "compute_nodes": keep,
        "route_adj": adj,
        "sacrificed": sac,
        "n_sacrificed": len(sac),
        "n_compute_used": len(keep),
        "n_originally_good": pg["n_originally_good"],
        "num_vc": 1,
        "n_detour_pairs": raw["n_detour_pairs"],
        # Pairs routed with no turn restriction at all.  For the turn-free
        # routings that is every pair; updown_relax reports only the pairs
        # its Up*/Down* core could not serve.
        "n_free_pairs": raw.get("n_free_pairs", len(raw["paths"])),
        "root": raw.get("root"),
        "turn_mode": raw.get("turn_mode"),
        "cdg_acyclic": R.cdg_acyclic(cdg),
        "cdg_cycle_channels": cdg_cycle_channels(cdg),
        "cdg_channels": len(cdg),
        "max_load": R.max_link_load(raw["paths"]),
        "hops": sum(len(p) - 1 for p in raw["paths"].values()),
        "reason": "ok",
    }


def solve_xy_detour(pg: dict) -> dict[str, Any]:
    return solve_routing(pg, "xy_detour")


# --------------------------------------------------------------------------
# DES with deadlock recovery
# --------------------------------------------------------------------------

def simulate_recovery(paths: dict[tuple[int, int], list[int]],
                      compute: list[int],
                      adj: dict[int, list[int]],
                      m: int = 1,
                      Q: int = DEFAULT_Q,
                      kind: str | None = None,
                      t_dd: int | None = None,
                      swap_k: int = SWAP_K,
                      check_order: bool = True,
                      t_max: int = T_MAX,
                      trace: bool = False) -> dict[str, Any] | None:
    """One-shot alltoall DES; `kind` in {None, 'sb', 'spin', 'swap'}.

    kind=None reproduces dse_pg_alltoall_8x6.simulate_alltoall exactly.
    On failure returns a dict carrying "failed": "stall" when the mechanism
    could not restart a wedged network, "tmax" when it was merely too slow.
    """
    if len(compute) < 2:
        return None
    if kind is not None and kind not in RECOVERY_KINDS:
        raise ValueError(kind)
    if t_dd is None:
        t_dd = SB_T_DD if kind == "sb" else SPIN_T_DD

    compute_set = set(compute)
    fifos: list[list[deque]] = [[deque() for _ in range(5)] for _ in range(N)]
    credits = [[Q] * 4 for _ in range(N)]
    arrive: dict[int, list] = defaultdict(list)
    cred_ret: dict[int, list] = defaultdict(list)

    dests = {}
    for s in compute:
        ds = [d for d in compute if d != s]
        rot = s % len(ds)
        dests[s] = ds[rot:] + ds[:rot]
    inj_state = {s: {"di": 0, "fi": 0, "pkt": 0} for s in compute}
    pkt_id = {}
    pid = 0
    for s in compute:
        for d in dests[s]:
            pkt_id[(s, d)] = pid
            pid += 1

    ejected = defaultdict(list)
    n_eject_need = len(compute) * (len(compute) - 1) * m
    n_ejected = 0
    last_eject_t = 0
    last_activity = 0
    t = 0

    nbr: list[list[int | None]] = [[None] * 4 for _ in range(N)]
    for node in range(N):
        x, y = F.coord(node)
        for d in range(4):
            nx, ny = x + DIRS[d][0], y + DIRS[d][1]
            if 0 <= nx < MX and 0 <= ny < MY:
                nb = F.nid(nx, ny)
                if nb in adj.get(node, ()):
                    nbr[node][d] = nb

    # --- recovery state ---------------------------------------------------
    blk: dict[tuple[int, int], int] = {}
    bonus: dict[tuple[int, int], int] = {}      # extra credits handed out
    disabled: dict[tuple[int, int], int] = {}   # (node,out_dir) -> only port
    frozen: set[tuple[int, int]] = set()        # (node,port) held for a spin
    rec: dict[str, Any] | None = None
    swap_ptr = [0] * N
    pend_swap: dict[int, tuple] = {}
    st = {"detect": 0, "false_pos": 0, "resolve": 0, "grants": 0, "spins": 0,
          "swaps": 0, "swap_try": 0, "backtrack": 0, "busy_cy": 0,
          "stall_cy": 0, "ring_max": 0, "ring_sum": 0, "no_bubble": 0,
          "lap_sum": 0}
    events: list[str] = []
    live_routers = sorted(adj.keys())
    n_rt = len(live_routers)

    def head(node: int, port: int):
        q = fifos[node][port]
        if not q:
            return None
        fl = q[0]
        if fl.at_dest:
            return None
        return fl

    def walk(node: int, port: int) -> list[tuple[int, int, int, int]] | None:
        """Probe: follow the buffer dependence chain, return the ring."""
        order: list[tuple[int, int, int, int]] = []
        pos: dict[tuple[int, int], int] = {}
        cur = (node, port)
        while True:
            if cur in pos:
                return order[pos[cur]:]
            fl = head(*cur)
            if fl is None:
                return None
            d = fl.out_dir()
            if d is None or credits[cur[0]][d] > 0:
                return None
            nb = nbr[cur[0]][d]
            if nb is None:
                return None
            pos[cur] = len(order)
            order.append((cur[0], cur[1], d, nb))
            cur = (nb, d ^ 1)
            if len(order) > 4 * N:
                return None

    def lap_cycles(ring) -> int:
        """One special-message lap: 1 cycle per router + the wire delays."""
        return sum(1 + R.link_lat(u, nb) for u, _p, _d, nb in ring)

    def ring_alive(ring) -> bool:
        for u, p, d, nb in ring:
            fl = head(u, p)
            if fl is None or fl.out_dir() != d or credits[u][d] > 0:
                return False
        return True

    def grant_bubble(ring) -> bool:
        """Switch on one static bubble inside the ring (HPCA'17 Sec. IV-A)."""
        for u, p, d, nb in ring:
            if nb in SB_NODES:
                credits[u][d] += 1
                bonus[(u, d)] = bonus.get((u, d), 0) + 1
                return True
        return False

    def reclaim_bubbles() -> None:
        for key, extra in list(bonus.items()):
            u, d = key
            if credits[u][d] > extra:
                credits[u][d] -= extra
                del bonus[key]

    def do_spin(ring) -> None:
        """All ring buffers push their head flit out in the same cycle.

        Each ring buffer frees one slot and takes one, so the credit state is
        unchanged; the flits are in flight for their wire delay.
        """
        for u, p, d, nb in ring:
            q = fifos[u][p]
            fl = q.popleft()
            nxt = Flit(fl.src, fl.dst, fl.pkt, fl.fi, fl.m,
                       t + R.link_lat(u, nb), fl.path, fl.hop + 1, 0)
            arrive[t + R.link_lat(u, nb)].append((nb, d ^ 1, nxt))

    def try_swap(up: int) -> None:
        """SWAP initiator step (MICRO'19 Sec. 4.2): pick swapFwd, ask, swap."""
        st["swap_try"] += 1
        ports = [(swap_ptr[up] + i) % 4 for i in range(4)]
        for p in ports:
            fl = head(up, p)
            if fl is None:
                continue
            d = fl.out_dir()
            if d is None:
                continue
            down = nbr[up][d]
            if down is None or credits[up][d] > 0:
                continue           # not blocked: no swap needed
            bport = d ^ 1
            back = head(down, bport)
            if back is None:
                continue           # nothing to hand back
            swap_ptr[up] = (p + 1) % 4
            pend_swap[t + SWAP_HANDSHAKE] = (up, p, d, down, bport)
            return
        swap_ptr[up] = (swap_ptr[up] + 1) % 4

    def exec_swap(up: int, p: int, d: int, down: int, bport: int) -> None:
        fwd = head(up, p)
        back = head(down, bport)
        if fwd is None or back is None or fwd.out_dir() != d:
            return
        lat = R.link_lat(up, down)
        fifos[up][p].popleft()
        fifos[down][bport].popleft()
        # swapFwd makes forward progress into the slot swapBack vacated
        a = Flit(fwd.src, fwd.dst, fwd.pkt, fwd.fi, fwd.m, t + lat,
                 fwd.path, fwd.hop + 1, 0)
        arrive[t + lat].append((down, bport, a))
        # swapBack u-turns into the slot swapFwd vacated: it now sits at `up`
        # and re-enters `down` on its next hop (MICRO'19 Sec. 4.2.2).
        bp = back.path
        h = back.hop
        new_path = bp[:h] + [up] + bp[h:]
        b = Flit(back.src, back.dst, back.pkt, back.fi, back.m, t + lat,
                 new_path, h, 0)
        arrive[t + lat].append((up, p, b))
        st["swaps"] += 1
        st["backtrack"] += 1
        if trace and len(events) < 40:
            events.append("t=%d swap %s<->%s" % (t, F.coord(up),
                                                 F.coord(down)))

    def bail(why: str) -> dict[str, Any]:
        out = {"failed": why, "makespan": None, "kind": kind, "cycles": t,
               "n_ejected": n_ejected, "n_eject_need": n_eject_need,
               "done_frac": round(n_ejected / max(n_eject_need, 1), 4)}
        out.update({("rec_" + k): v for k, v in st.items()})
        return out

    while t <= t_max:
        activity = False

        for node, d in cred_ret.pop(t, ()):
            credits[node][d] += 1
            activity = True

        for node, port, fl in arrive.pop(t, ()):
            fifos[node][port].append(fl)
            activity = True

        if t >= RAMP:
            for s in compute:
                budget = RAMP_BW
                stt = inj_state[s]
                while budget > 0 and stt["di"] < len(dests[s]):
                    d = dests[s][stt["di"]]
                    if len(fifos[s][INJ]) >= Q * 4:
                        break
                    fl = Flit(s, d, pkt_id[(s, d)], stt["fi"], m, t,
                              paths[(s, d)], 0, 0)
                    fifos[s][INJ].append(fl)
                    activity = True
                    budget -= 1
                    stt["fi"] += 1
                    if stt["fi"] >= m:
                        stt["fi"] = 0
                        stt["di"] += 1
                        stt["pkt"] += 1

        # --- recovery: scheduled steps before arbitration -----------------
        if kind == "swap":
            ev = pend_swap.pop(t, None)
            if ev is not None:
                exec_swap(*ev)
                activity = True
            if n_rt:
                slot = t % (swap_k * n_rt)
                if slot < n_rt:
                    try_swap(live_routers[slot])
        elif kind is not None and rec is not None:
            if t >= rec["t_evt"]:
                ring = rec["ring"]
                ph = rec["phase"]
                if ph == "enable":
                    # SB: the enable message clears is_deadlock / IO_priority
                    # around the loop before normal traffic resumes.
                    for key in rec.get("held", ()):
                        disabled.pop(key, None)
                    st["resolve"] += 1
                    rec = None
                elif not ring_alive(ring):
                    if ph == "probe":
                        st["false_pos"] += 1
                    frozen.difference_update(rec.get("froze", ()))
                    if rec.get("held"):
                        rec.update(phase="enable", t_evt=t + rec["lap"])
                    else:
                        st["resolve"] += 1
                        rec = None
                elif ph == "probe":
                    st["detect"] += 1
                    st["ring_sum"] += len(ring)
                    st["ring_max"] = max(st["ring_max"], len(ring))
                    st["lap_sum"] += rec["lap"]
                    if kind == "sb":
                        rec.update(phase="disable", t_evt=t + rec["lap"])
                    else:
                        held = {(u, p) for u, p, _d, _n in ring}
                        frozen |= held
                        rec.update(phase="spin", t_evt=t + 2 * rec["lap"],
                                   froze=held)
                elif ph == "disable":
                    keys = {}
                    for u, p, d, nb in ring:
                        disabled[(u, d)] = p
                        keys[(u, d)] = p
                    if grant_bubble(ring):
                        st["grants"] += 1
                        rec.update(phase="check", t_evt=t + rec["lap"],
                                   held=set(keys))
                    else:
                        st["no_bubble"] += 1
                        for key in keys:
                            disabled.pop(key, None)
                        rec = None
                    activity = True
                elif ph == "spin":
                    # ISCA'18: spin cycle = (move sent) + 2 x loop length; the
                    # probe_move that arms the next spin costs 2 laps again,
                    # and it re-checks the same latched loop.
                    do_spin(ring)
                    st["spins"] += 1
                    held = {(u, p) for u, p, _d, _n in ring}
                    frozen |= held
                    rec.update(t_evt=t + 2 * rec["lap"], froze=held)
                    activity = True
                elif ph == "check":
                    # check_probe: same latched loop, one lap per extra hop.
                    if grant_bubble(ring):
                        st["grants"] += 1
                    rec["t_evt"] = t + rec["lap"]
                    activity = True
            if rec is not None:
                st["busy_cy"] += 1

        if kind == "sb" and bonus:
            reclaim_bubbles()

        # --- arbitration --------------------------------------------------
        for node in live_routers:
            fnode = fifos[node]
            ports = [p for p in range(5) if fnode[p]]
            if not ports:
                continue
            for d in range(4):
                nb = nbr[node][d]
                if nb is None or credits[node][d] <= 0:
                    continue
                only = disabled.get((node, d)) if disabled else None
                best = None
                best_port = -1
                for port in ports:
                    if only is not None and port != only:
                        continue
                    if frozen and (node, port) in frozen:
                        continue
                    q = fnode[port]
                    if not q:
                        continue
                    fl = q[0]
                    if fl.at_dest or fl.out_dir() != d:
                        continue
                    if (best is None
                            or fl.arrival < best.arrival
                            or (fl.arrival == best.arrival
                                and (fl.src, fl.dst, fl.fi)
                                < (best.src, best.dst, best.fi))):
                        best = fl
                        best_port = port
                if best is None:
                    continue
                credits[node][d] -= 1
                lat = R.link_lat(node, nb)
                nxt = Flit(best.src, best.dst, best.pkt, best.fi, best.m,
                           t + lat, best.path, best.hop + 1, 0)
                arrive[t + lat].append((nb, d ^ 1, nxt))
                fifos[node][best_port].popleft()
                activity = True
                if best_port != INJ:
                    up = nbr[node][best_port]
                    if up is not None:
                        cred_ret[t + R.link_lat(node, up)].append(
                            (up, best_port ^ 1))

            if node in compute_set:
                drained = 0
                for port in ports:
                    if drained >= RAMP_BW:
                        break
                    q = fnode[port]
                    while q and drained < RAMP_BW:
                        fl = q[0]
                        if not fl.at_dest:
                            break
                        q.popleft()
                        ejected[(fl.src, fl.dst)].append(fl.fi)
                        n_ejected += 1
                        last_eject_t = t
                        drained += 1
                        activity = True
                        if port != INJ:
                            up = nbr[node][port]
                            if up is not None:
                                cred_ret[t + R.link_lat(node, up)].append(
                                    (up, port ^ 1))

        # --- blocked timers and probe launch ------------------------------
        # Skipped while a recovery is in flight: only one FSM may hold the
        # loop at a time, and the HW counters simply keep running, so a
        # still-blocked buffer re-probes on the cycle after teardown.
        if kind is not None and kind != "swap" and rec is None:
            for node in live_routers:
                for port in range(5):
                    fl = head(node, port)
                    key = (node, port)
                    if fl is None:
                        if key in blk:
                            del blk[key]
                        continue
                    d = fl.out_dir()
                    if d is None or credits[node][d] > 0:
                        blk.pop(key, None)
                        continue
                    c = blk.get(key, 0) + 1
                    blk[key] = c
                    if rec is None and c >= t_dd:
                        ring = walk(node, port)
                        if ring:
                            lap = lap_cycles(ring)
                            rec = {"phase": "probe", "t_evt": t + lap,
                                   "ring": ring, "lap": lap,
                                   "seed": (node, port), "held": set(),
                                   "froze": set(), "t0": t}
                            blk[key] = 0

        if activity:
            last_activity = t
        else:
            st["stall_cy"] += 1
        if (n_ejected >= n_eject_need and not arrive
                and not any(fifos[n][p] for n in range(N)
                            for p in range(5))):
            break
        if t - last_activity > STALL_LIMIT:
            return bail("stall")
        t += 1
    else:
        return bail("tmax")

    ordered_ok = True
    n_ooo = 0
    reorder_depth = 0
    if check_order:
        want = list(range(m))
        for s in compute:
            for d in compute:
                if s == d:
                    continue
                seq = ejected.get((s, d), [])
                if seq == want:
                    continue
                ordered_ok = False
                n_ooo += 1
                # flits a reassembly buffer would have to hold: worst gap
                # between what arrived and what was still expected in order
                nxt = 0
                held = 0
                pend: set[int] = set()
                for fi in seq:
                    if fi == nxt:
                        nxt += 1
                        while nxt in pend:
                            pend.discard(nxt)
                            nxt += 1
                    else:
                        pend.add(fi)
                    held = max(held, len(pend))
                reorder_depth = max(reorder_depth, held)

    out = {
        "makespan": last_eject_t + RAMP,
        "ordered_ok": ordered_ok,
        "n_pairs_out_of_order": n_ooo,
        "reorder_depth": reorder_depth,
        "bonus_left": len(bonus),
        "n_ejected": n_ejected,
        "cycles": t,
        "kind": kind,
    }
    out.update({("rec_" + k): v for k, v in st.items()})
    if trace:
        out["events"] = events
    return out


# --------------------------------------------------------------------------
# self test: the fork must match the parent DES where both are legal
# --------------------------------------------------------------------------

def selftest(verbose: bool = True) -> bool:
    import dse_pg_alltoall_8x6 as D
    import pg_faults_budget_8x6 as B

    ok = True
    cases = [("healthy", F.healthy_pg())]
    for scen in B.stratified_scenarios(n_per_cell=1, seed=0)[:6]:
        cases.append((scen["name"], B.expand_budget(scen, "dead")))
    for name, pg in cases:
        sol = R.solve_scheme(pg, "xy")
        if not sol["feasible"]:
            continue
        for m in (1, 3):
            a = D.simulate_alltoall(sol["paths"], sol["compute_nodes"],
                                    sol["route_adj"], m=m)
            b = simulate_recovery(sol["paths"], sol["compute_nodes"],
                                  sol["route_adj"], m=m, kind=None)
            bad = b is None or b.get("failed")
            same = (a is None) == bool(bad) and (
                a is None or a["makespan"] == b["makespan"])
            ok &= same
            if verbose:
                print("  %-18s m=%d parent=%s fork=%s %s"
                      % (name, m, a and a["makespan"],
                         b and (b.get("failed") or b["makespan"]),
                         "OK" if same else "MISMATCH"))
    sb = sb_positions(8, 8)
    ok &= len(sb) == 21
    ok &= len(sb_positions(16, 16)) == 89
    if verbose:
        print("  static bubbles: 8x8=%d (paper 21)  16x16=%d (paper 89)  "
              "8x6=%d" % (len(sb), len(sb_positions(16, 16)), len(SB_NODES)))
        print("selftest:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    selftest()
