#!/usr/bin/env python3
"""Zero-buffer superposition of allgather with broadcast/gather on 6×8 mesh.

Root = (0, 1). Model matches sched_zerobuf_compare rigid 0-buffer packing:
  * directed link ≤ 1 flit/cycle, ramp ≤ ramp_bw
  * rigid footprints; only freedom is per-source inject offset
  => conflict-free + non-blocking + 0 router buffer by construction.

Analyses:
  1. allgather ⊕ broadcast(root)
  2. allgather ⊕ gather(root)

Output helpers feed results/report_superpose_6x8.html.
"""

from __future__ import annotations

import math
from collections import defaultdict

import allgather_6x8_rigid as R
import allgather_lower_bounds as LB
import sched_zerobuf_compare as S

MX, MY = 6, 8
H, V = 7, 9
N = MX * MY
ROOT_XY = (0, 1)
FLITS = [1, 2, 3, 4, 5]
RAMP_BW = 1

AG_SCHEMES = ["multitree", "axis_ccw", "ring_bi", "hybrid_v_bi_B2", "border_bi_Q4"]
# Prefer schemes that are strong alone; ring/hybrid kept for completeness but
# gather search uses AG_SCHEMES_FAST to keep runtime tractable.
AG_SCHEMES_FAST = ["multitree", "axis_ccw", "ring_bi", "border_bi_Q4"]
TREE_KINDS = ["multitree", "axis_ccw", "yx_tree"]
# axis_ccw gather falls back to multitree parents — identical tree.
GATHER_TREE_KINDS = ["multitree", "yx_tree"]

_SOLO_AG_CACHE = {}
_SOLO_BC_CACHE = {}
_SOLO_G_CACHE = {}


def setup(h=H, v=V):
    R.setup(h, v)
    global N
    N = S.N


def root_id():
    return S.nid(*ROOT_XY)


# --------------------------------------------------------------------------
# Broadcast / gather footprints (single-root trees)
# --------------------------------------------------------------------------
def tree_edges_yx(s):
    """Y-then-X dimensional tree (transpose of tree_edges)."""
    sx, sy = S.coord(s)
    e = []
    for y in range(sy + 1, S.MY):
        e.append((S.nid(sx, y - 1), S.nid(sx, y)))
    for y in range(sy - 1, -1, -1):
        e.append((S.nid(sx, y + 1), S.nid(sx, y)))
    for y in range(S.MY):
        for x in range(sx + 1, S.MX):
            e.append((S.nid(x - 1, y), S.nid(x, y)))
        for x in range(sx - 1, -1, -1):
            e.append((S.nid(x + 1, y), S.nid(x, y)))
    return e


def fp_tree_yx(s):
    slots = [("U", s, 0)]
    for p, c in tree_edges_yx(s):
        slots.append(("L", S.lk(p, c), S.RAMP + S.manh(s, p)))
    for d in range(S.N):
        if d != s:
            slots.append(("D", d, S.RAMP + S.manh(s, d)))
    return slots


def fp_broadcast(root, kind):
    if kind == "multitree":
        return S.fp_multitree(root)
    if kind == "axis_ccw":
        return S.fp_axis_ccw(root)
    if kind == "yx_tree":
        return fp_tree_yx(root)
    raise KeyError(kind)


def _parent_map(edges):
    """child -> parent for a spanning tree rooted at the unique source of edges."""
    return {c: p for p, c in edges}


def _path_to_root(node, parent):
    path = [node]
    while node in parent:
        node = parent[node]
        path.append(node)
    return path


def _unicast_slots(path):
    """Rigid unicast along path[0] -> path[-1]; inject at path[0], eject at end."""
    if len(path) < 2:
        return []
    s = path[0]
    slots = [("U", s, 0)]
    t = S.RAMP
    for i in range(len(path) - 1):
        u, w = path[i], path[i + 1]
        slots.append(("L", S.lk(u, w), t))
        t += S.edge_lat(u, w)
    slots.append(("D", path[-1], t))
    return slots


def gather_parent_map(root, kind):
    """child -> parent (next hop toward root) on the spanning tree.

    Same tree as the matching broadcast; traffic flows opposite direction.
    axis_ccw is not a tree — fall back to multitree parents.
    """
    if kind == "multitree":
        return _parent_map(S.tree_edges(root))
    if kind == "yx_tree":
        return _parent_map(tree_edges_yx(root))
    if kind == "axis_ccw":
        return _parent_map(S.tree_edges(root))
    raise KeyError(kind)


def fp_gather_sources(root, kind):
    """Dict src -> unicast-to-root footprint for every src ≠ root."""
    parent = gather_parent_map(root, kind)
    out = {}
    for s in range(S.N):
        if s == root:
            continue
        path = _path_to_root(s, parent)
        assert path[-1] == root, (s, path)
        out[s] = _unicast_slots(path)
    return out


# --------------------------------------------------------------------------
# Allgather footprints (single-round, flits handled by packer)
# --------------------------------------------------------------------------
def ag_footprints(scheme, ramp_bw=1):
    if scheme == "multitree":
        return {s: S.fp_multitree(s) for s in range(S.N)}
    if scheme == "axis_ccw":
        return {s: S.fp_axis_ccw(s) for s in range(S.N)}
    if scheme == "ring_bi":
        return {
            s: S.fp_ring(s, S.RING_ORDER, S.RING_POS, True, ramp_bw)
            for s in range(S.N)
        }
    if scheme == "hybrid_v_bi_B2":
        return {
            s: S.fp_hybrid_v(s, R.B_HYBRID_V, True, ramp_bw)
            for s in range(S.N)
        }
    if scheme == "border_bi_Q4":
        return {s: S.fp_border(s, True, ramp_bw) for s in range(S.N)}
    raise KeyError(scheme)


# --------------------------------------------------------------------------
# Packing / verify for superposition
# --------------------------------------------------------------------------
def _pack_named(footprints, order, ramp_bw, flits, down_cap=None):
    return S.pack(footprints, ramp_bw, order, flits=flits, down_cap=down_cap)


def verify_caps(busy, ramp_bw, down_cap=None):
    uc = ramp_bw
    dc = ramp_bw if down_cap is None else down_cap
    link_busy, up_busy, down_busy = busy
    if not all(ct <= 1 for d in link_busy.values() for ct in d.values()):
        return False
    if not all(ct <= uc for d in up_busy.values() for ct in d.values()):
        return False
    if not all(ct <= dc for d in down_busy.values() for ct in d.values()):
        return False
    return True


def eject_counts(busy):
    _, _, down_busy = busy
    return {n: sum(d.values()) for n, d in down_busy.items()}


def verify_ag_bcast(busy, root, m, ramp_bw, down_cap=None):
    if not verify_caps(busy, ramp_bw, down_cap):
        return False
    ej = eject_counts(busy)
    for n in range(S.N):
        # AG: (N-1)*m everywhere; bcast: +m at every non-root
        need = (S.N - 1) * m + (0 if n == root else m)
        if ej.get(n, 0) != need:
            return False
    return True


def verify_ag_gather(busy, root, m, ramp_bw, down_cap=None):
    if not verify_caps(busy, ramp_bw, down_cap):
        return False
    ej = eject_counts(busy)
    for n in range(S.N):
        # AG: (N-1)*m everywhere; gather: +(N-1)*m at root only
        need = (S.N - 1) * m + ((S.N - 1) * m if n == root else 0)
        if ej.get(n, 0) != need:
            return False
    return True


def _ag_orders():
    orders = []
    for name, gen in S.SRC_ORDERS.items():
        try:
            order = gen()
        except Exception:
            continue
        if len(order) == S.N:
            orders.append((name, order))
    return orders


def _gather_src_orders(root, full=False):
    """Orderings over gather sources (nodes ≠ root)."""
    others = [s for s in range(S.N) if s != root]
    rx, ry = S.coord(root)

    def dist(s):
        x, y = S.coord(s)
        return abs(x - rx) + abs(y - ry)

    base = [
        ("far_first", sorted(others, key=lambda s: -dist(s))),
        ("near_first", sorted(others, key=dist)),
        ("col", sorted(others, key=lambda s: (S.coord(s)[0], S.coord(s)[1]))),
    ]
    if not full:
        return base
    return base + [
        ("natural", list(others)),
        ("rev", list(reversed(others))),
        ("row", sorted(others, key=lambda s: (S.coord(s)[1], S.coord(s)[0]))),
    ]


# --------------------------------------------------------------------------
# Lower bounds
# --------------------------------------------------------------------------
def bcast_lower_bound(root, m, ramp_bw=1):
    """Broadcast alone: root up-ramp + farthest delivery latency."""
    max_d = max(S.manh(root, d) for d in range(S.N) if d != root)
    lat = S.RAMP + max_d + (m - 1) + S.RAMP
    up = math.ceil(m / ramp_bw) + S.RAMP  # inject + eject at leaves covered in lat
    # leaf down-ramp serializes m flits; already in lat via (m-1)
    return max(lat, math.ceil(m / ramp_bw))


def gather_lower_bound(root, m, ramp_bw=1):
    """Gather alone: root must eject (N-1)*m; plus farthest source latency."""
    max_d = max(S.manh(s, root) for s in range(S.N) if s != root)
    lat = S.RAMP + max_d + (m - 1) + S.RAMP
    eject = math.ceil((S.N - 1) * m / ramp_bw)
    return max(lat, eject)


def ag_lower_bound(m, ramp_bw=1):
    return LB.bounds_for(S.MX, S.MY, S.H, S.V, m, ramp_bw)["T"]


def ag_bcast_lower_bound(root, m, ramp_bw=1):
    """Combined necessary LB for AG ⊕ broadcast."""
    ag = ag_lower_bound(m, ramp_bw)
    bc = bcast_lower_bound(root, m, ramp_bw)
    # root up-ramp: AG inject m + bcast inject m
    root_up = math.ceil(2 * m / ramp_bw)
    # non-root down-ramp: AG (N-1)m + bcast m
    nonroot_down = math.ceil(N * m / ramp_bw)
    # corner still sees AG traffic; bcast adds at most m more via 2 links
    corner = math.ceil(((N - 1) * m + m) / 2)  # rough: AG+bcast into a corner
    return max(ag, bc, root_up, nonroot_down, corner)


def ag_gather_lower_bound(root, m, ramp_bw=1):
    """Combined necessary LB for AG ⊕ gather — root down-ramp is 2*(N-1)*m."""
    ag = ag_lower_bound(m, ramp_bw)
    g = gather_lower_bound(root, m, ramp_bw)
    root_down = math.ceil(2 * (N - 1) * m / ramp_bw)
    return max(ag, g, root_down)


# --------------------------------------------------------------------------
# Solo baselines (for sum comparison)
# --------------------------------------------------------------------------
def solo_broadcast(root, kind, m, ramp_bw=1):
    key = (root, kind, m, ramp_bw)
    if key in _SOLO_BC_CACHE:
        return _SOLO_BC_CACHE[key]
    foot = {"bcast": fp_broadcast(root, kind)}
    mk, _, busy = _pack_named(foot, ["bcast"], ramp_bw, m)
    ok = verify_caps(busy, ramp_bw)
    ej = eject_counts(busy)
    ok = ok and all(ej.get(n, 0) == m for n in range(S.N) if n != root)
    ok = ok and ej.get(root, 0) == 0
    out = mk if ok else None
    _SOLO_BC_CACHE[key] = out
    return out


def solo_gather(root, kind, m, ramp_bw=1):
    key = (root, kind, m, ramp_bw)
    if key in _SOLO_G_CACHE:
        return _SOLO_G_CACHE[key]
    gfoot = fp_gather_sources(root, kind)
    foot = {f"g{s}": slots for s, slots in gfoot.items()}
    best = None
    for _, srcs in _gather_src_orders(root, full=True):
        order = [f"g{s}" for s in srcs]
        mk, _, busy = _pack_named(foot, order, ramp_bw, m)
        if not verify_caps(busy, ramp_bw):
            continue
        ej = eject_counts(busy)
        if ej.get(root, 0) != (S.N - 1) * m:
            continue
        if any(ej.get(n, 0) != 0 for n in range(S.N) if n != root):
            continue
        if best is None or mk < best:
            best = mk
    _SOLO_G_CACHE[key] = best
    return best


def solo_allgather(scheme, m, ramp_bw=1):
    key = (scheme, m, ramp_bw)
    if key in _SOLO_AG_CACHE:
        return _SOLO_AG_CACHE[key]
    rec = R.scheme_makespan(scheme, ramp_bw, m)
    out = rec["makespan"] if rec.get("zbuf") else None
    _SOLO_AG_CACHE[key] = out
    return out


# --------------------------------------------------------------------------
# Superposition search
# --------------------------------------------------------------------------
def pack_ag_bcast(ag_scheme, bcast_kind, m, ramp_bw=1, mode="interleave"):
    """mode: interleave | ag_first | bcast_first"""
    root = root_id()
    ag = ag_footprints(ag_scheme, ramp_bw)
    bcast = fp_broadcast(root, bcast_kind)
    best = None  # (mk, meta)

    if mode == "ag_first":
        # sequential: pack AG, then bcast against empty (add makespans)
        mk_ag = solo_allgather(ag_scheme, m, ramp_bw)
        mk_bc = solo_broadcast(root, bcast_kind, m, ramp_bw)
        if mk_ag is None or mk_bc is None:
            return None
        return {
            "makespan": mk_ag + mk_bc,
            "ok": True,
            "mode": mode,
            "ag_scheme": ag_scheme,
            "tree": bcast_kind,
            "order": "sequential",
            "ag_mk": mk_ag,
            "extra_mk": mk_bc,
        }

    if mode == "bcast_first":
        mk_ag = solo_allgather(ag_scheme, m, ramp_bw)
        mk_bc = solo_broadcast(root, bcast_kind, m, ramp_bw)
        if mk_ag is None or mk_bc is None:
            return None
        return {
            "makespan": mk_bc + mk_ag,
            "ok": True,
            "mode": mode,
            "ag_scheme": ag_scheme,
            "tree": bcast_kind,
            "order": "sequential",
            "ag_mk": mk_ag,
            "extra_mk": mk_bc,
        }

    # interleave: merge footprints
    foot = dict(ag)
    foot["bcast"] = bcast
    for oname, ag_order in _ag_orders():
        for place in ("bcast_first", "bcast_mid", "bcast_last"):
            if place == "bcast_first":
                order = ["bcast"] + list(ag_order)
            elif place == "bcast_last":
                order = list(ag_order) + ["bcast"]
            else:
                mid = len(ag_order) // 2
                order = list(ag_order[:mid]) + ["bcast"] + list(ag_order[mid:])
            mk, _, busy = _pack_named(foot, order, ramp_bw, m)
            ok = verify_ag_bcast(busy, root, m, ramp_bw)
            if not ok:
                continue
            if best is None or mk < best[0]:
                best = (mk, {
                    "makespan": mk,
                    "ok": True,
                    "mode": "interleave",
                    "ag_scheme": ag_scheme,
                    "tree": bcast_kind,
                    "order": f"{oname}/{place}",
                    "busy": busy,
                })
    if best is None:
        return None
    rec = best[1]
    # drop busy from returned record (large); keep ramp stats
    busy = rec.pop("busy")
    rec["ramp"] = R.ramp_stats(busy, rec["makespan"])
    return rec


def pack_ag_gather(ag_scheme, gather_kind, m, ramp_bw=1, mode="interleave"):
    root = root_id()
    ag = ag_footprints(ag_scheme, ramp_bw)
    gfoot_raw = fp_gather_sources(root, gather_kind)

    if mode in ("ag_first", "gather_first"):
        mk_ag = solo_allgather(ag_scheme, m, ramp_bw)
        mk_g = solo_gather(root, gather_kind, m, ramp_bw)
        if mk_ag is None or mk_g is None:
            return None
        return {
            "makespan": mk_ag + mk_g,
            "ok": True,
            "mode": mode,
            "ag_scheme": ag_scheme,
            "tree": gather_kind,
            "order": "sequential",
            "ag_mk": mk_ag,
            "extra_mk": mk_g,
        }

    foot = dict(ag)
    for s, slots in gfoot_raw.items():
        foot[f"g{s}"] = slots

    # Restrict order search: corner/center dominate AG; far_first/col for gather.
    ag_orders = [(n, o) for n, o in _ag_orders() if n in ("corner", "center", "col")]
    g_orders = _gather_src_orders(root, full=False)
    places = ("gather_first", "gather_last", "gather_mid")

    best = None
    for oname, ag_order in ag_orders:
        for gname, gsrcs in g_orders:
            g_order = [f"g{s}" for s in gsrcs]
            for place in places:
                if place == "gather_first":
                    order = g_order + list(ag_order)
                elif place == "gather_last":
                    order = list(ag_order) + g_order
                else:
                    mid = len(ag_order) // 2
                    order = list(ag_order[:mid]) + g_order + list(ag_order[mid:])
                mk, _, busy = _pack_named(foot, order, ramp_bw, m)
                if not verify_caps(busy, ramp_bw):
                    continue
                if not verify_ag_gather(busy, root, m, ramp_bw):
                    continue
                if best is None or mk < best[0]:
                    best = (mk, {
                        "makespan": mk,
                        "ok": True,
                        "mode": "interleave",
                        "ag_scheme": ag_scheme,
                        "tree": gather_kind,
                        "order": f"{oname}/{gname}/{place}",
                        "busy": busy,
                    })
    if best is None:
        return None
    rec = best[1]
    busy = rec.pop("busy")
    rec["ramp"] = R.ramp_stats(busy, rec["makespan"])
    return rec


def search_ag_bcast(m, ramp_bw=1, solo=None):
    root = root_id()
    lb = ag_bcast_lower_bound(root, m, ramp_bw)
    ag_lb = ag_lower_bound(m, ramp_bw)
    if solo:
        bc_best = min(
            (v for v in solo["bcast"].values() if v is not None), default=None)
        best_ag = min(
            (v for v in solo["ag"].values() if v is not None), default=None)
    else:
        bc_best = min(
            (solo_broadcast(root, k, m, ramp_bw) for k in TREE_KINDS),
            key=lambda x: x if x is not None else 10**9,
        )
        ag_solos = [solo_allgather(s, m, ramp_bw) for s in AG_SCHEMES]
        best_ag = min((x for x in ag_solos if x is not None), default=None)

    candidates = []
    # sequential baseline once
    if best_ag is not None and bc_best is not None:
        candidates.append({
            "makespan": best_ag + bc_best,
            "ok": True,
            "mode": "ag_first",
            "ag_scheme": "best_solo",
            "tree": "best_solo",
            "order": "sequential",
            "ag_mk": best_ag,
            "extra_mk": bc_best,
        })
    for ag_s in AG_SCHEMES:
        for tk in TREE_KINDS:
            print(f"  AG⊕Bcast m={m} {ag_s}/{tk}/interleave ...", flush=True)
            rec = pack_ag_bcast(ag_s, tk, m, ramp_bw, mode="interleave")
            if rec and rec.get("ok"):
                candidates.append(rec)
    if not candidates:
        return {
            "makespan": None, "ok": False, "lb": lb,
            "ag_lb": ag_lb, "extra_solo": bc_best,
        }
    best = min(candidates, key=lambda r: r["makespan"])
    best["lb"] = lb
    best["ag_lb"] = ag_lb
    best["extra_solo"] = bc_best
    best["best_ag_solo"] = best_ag
    best["sum_lb"] = (best_ag + bc_best) if (best_ag and bc_best) else None
    best["gap_to_lb"] = best["makespan"] - lb
    best["perfect"] = best["makespan"] == lb
    return best


def search_ag_gather(m, ramp_bw=1, solo=None):
    root = root_id()
    lb = ag_gather_lower_bound(root, m, ramp_bw)
    ag_lb = ag_lower_bound(m, ramp_bw)
    if solo:
        g_best = min(
            (v for v in solo["gather"].values() if v is not None), default=None)
        best_ag = min(
            (v for v in solo["ag"].values() if v is not None), default=None)
    else:
        g_best = min(
            (solo_gather(root, k, m, ramp_bw) for k in GATHER_TREE_KINDS),
            key=lambda x: x if x is not None else 10**9,
        )
        ag_solos = [solo_allgather(s, m, ramp_bw) for s in AG_SCHEMES]
        best_ag = min((x for x in ag_solos if x is not None), default=None)

    candidates = []
    if best_ag is not None and g_best is not None:
        candidates.append({
            "makespan": best_ag + g_best,
            "ok": True,
            "mode": "ag_first",
            "ag_scheme": "best_solo",
            "tree": "best_solo",
            "order": "sequential",
            "ag_mk": best_ag,
            "extra_mk": g_best,
        })
    for ag_s in AG_SCHEMES_FAST:
        for tk in GATHER_TREE_KINDS:
            print(f"  AG⊕Gather m={m} {ag_s}/{tk}/interleave ...", flush=True)
            rec = pack_ag_gather(ag_s, tk, m, ramp_bw, mode="interleave")
            if rec and rec.get("ok"):
                candidates.append(rec)
    if not candidates:
        return {
            "makespan": None, "ok": False, "lb": lb,
            "ag_lb": ag_lb, "extra_solo": g_best,
        }
    best = min(candidates, key=lambda r: r["makespan"])
    best["lb"] = lb
    best["ag_lb"] = ag_lb
    best["extra_solo"] = g_best
    best["best_ag_solo"] = best_ag
    best["sum_lb"] = (best_ag + g_best) if (best_ag and g_best) else None
    best["gap_to_lb"] = best["makespan"] - lb
    best["perfect"] = best["makespan"] == lb
    return best


def compute(flits=None, ramp_bw=RAMP_BW):
    setup()
    flits = flits or FLITS
    root = root_id()
    out = {
        "mx": S.MX, "my": S.MY, "h": S.H, "v": S.V,
        "ramp_bw": ramp_bw, "root": ROOT_XY, "root_id": root,
        "ag_bcast": {}, "ag_gather": {},
        "solo": {},
    }
    for m in flits:
        print(f"=== m={m} solos ===", flush=True)
        solo = {
            "ag_lb": ag_lower_bound(m, ramp_bw),
            "bcast_lb": bcast_lower_bound(root, m, ramp_bw),
            "gather_lb": gather_lower_bound(root, m, ramp_bw),
            "ag_bcast_lb": ag_bcast_lower_bound(root, m, ramp_bw),
            "ag_gather_lb": ag_gather_lower_bound(root, m, ramp_bw),
            "bcast": {k: solo_broadcast(root, k, m, ramp_bw) for k in TREE_KINDS},
            "gather": {
                k: solo_gather(root, k, m, ramp_bw) for k in GATHER_TREE_KINDS
            },
            "ag": {s: solo_allgather(s, m, ramp_bw) for s in AG_SCHEMES},
        }
        out["solo"][m] = solo
        print(f"=== m={m} AG⊕Bcast ===", flush=True)
        out["ag_bcast"][m] = search_ag_bcast(m, ramp_bw, solo=solo)
        print(f"=== m={m} AG⊕Gather ===", flush=True)
        out["ag_gather"][m] = search_ag_gather(m, ramp_bw, solo=solo)
    return out


def _strip_busy(obj):
    if isinstance(obj, dict):
        return {k: _strip_busy(v) for k, v in obj.items() if k != "busy"}
    return obj


if __name__ == "__main__":
    import json
    from pathlib import Path

    setup()
    data = compute()
    path = Path(__file__).resolve().parents[1] / "results" / "superpose_6x8.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_strip_busy(data), f, indent=2)
    print(f"Wrote {path}")
    for m in FLITS:
        b = data["ag_bcast"][m]
        g = data["ag_gather"][m]
        print(
            f"m={m}: AG⊕Bcast mk={b.get('makespan')} lb={b.get('lb')} "
            f"[{b.get('ag_scheme')}/{b.get('tree')}/{b.get('mode')}] "
            f"perfect={b.get('perfect')}"
        )
        print(
            f"m={m}: AG⊕Gather mk={g.get('makespan')} lb={g.get('lb')} "
            f"[{g.get('ag_scheme')}/{g.get('tree')}/{g.get('mode')}] "
            f"perfect={g.get('perfect')}"
        )
