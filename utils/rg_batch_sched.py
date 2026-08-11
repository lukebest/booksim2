#!/usr/bin/env python3
"""Windowed batch request-grant with a GLOBAL conflict-free scheduler.

Pipeline (central scheduler CA at (4,0) = nid 4, origin top-left):

  1. Each node generates its requests at DIFFERENT times (staggered).
  2. Requests travel the PRIVATE control NoC to CA using XY routing;
     arrival time therefore includes per-hop link delay (H=7 / V=9) plus
     control-vs-control contention. Nodes are at different distances, so
     arrivals are inherently non-uniform.
  3. CA closes a time window of W cycles, takes the batch of requests that
     arrived inside it, and runs a GLOBAL CONFLICT-FREE SCHEDULE (BCFS).
  4. Grants travel back CA -> src over the private control NoC (XY, link
     delay), so each source learns its start time at a different moment.
     The schedule respects these per-request release times.

BCFS (Batch Conflict-Free Scheduler) for point-to-point requests
----------------------------------------------------------------
Each granted flow is a RIGID wormhole footprint: with start time t0, path P
and m flits, it occupies directed link e during

    [ t0 + pref_P(e),  t0 + pref_P(e) + m*sigma )

where pref_P(e) is the accumulated wire delay from the source to the tail of
e, and sigma is cycles-per-flit (mesh 1, torus 2). Source/destination ramps
are capacity-limited resources (RAMP_BW flits/cycle).

Conflict-free schedule = choose t0 for every request so that on every link
no two footprints overlap, and ramp capacity is never exceeded. This is a
fixed-route job-shop / interval-packing problem (NP-hard in general), so
BCFS uses:

  * criticality-first list scheduling — schedule the request whose path
    crosses the most contended links first (it has the least slack),
  * exact earliest-feasible-start search — jump directly to the next time
    at which ALL links + both ramps are simultaneously free (no 1-cycle
    stepping), driven by interval maps,
  * multi-start over several priority orders (criticality / longest-path /
    FCFS / randomized), keeping the best batch makespan.

The output is conflict-free BY CONSTRUCTION and is re-verified by an
independent checker (`verify_conflict_free`), which is what makes the
bufferless (zero-router-buffer) data plane safe.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Literal

from rg_topo import RAMP, RAMP_BW, Topology, central_arbiter_node, coord
from rg_collectives import Collective, Flow, tree_link_schedule
from rg_arbiter import Grant

GenModel = Literal["uniform_jitter", "distance_skew", "burst"]
Priority = Literal["criticality", "longest", "fcfs", "random"]


# ---------------------------------------------------------------------------
# 1. Staggered request generation
# ---------------------------------------------------------------------------

def generate_request_times(col: Collective, *, model: GenModel = "uniform_jitter",
                           jitter: int = 64, spacing: int = 1,
                           seed: int = 0) -> dict[int, int]:
    """Per-flow request GENERATION time at its source node.

    Nodes do not generate simultaneously. Each source gets an offset, and
    its own requests are then spaced (a node can emit at most 1 control
    message per cycle anyway).

    Returns {flow_id: t_generate}.
    """
    rng = random.Random(seed)
    srcs = sorted({f.src for f in col.flows})
    offset: dict[int, int] = {}
    if model == "uniform_jitter":
        for s in srcs:
            offset[s] = rng.randrange(0, max(1, jitter))
    elif model == "distance_skew":
        # farther-from-CA nodes start earlier (tries to equalize arrivals)
        ca = central_arbiter_node()
        # needs a topology-independent proxy: Manhattan hops
        for s in srcs:
            sx, sy = coord(s)
            cx, cy = coord(ca)
            offset[s] = (abs(sx - cx) + abs(sy - cy))
        far = max(offset.values()) if offset else 0
        offset = {s: (far - v) * 4 for s, v in offset.items()}
    elif model == "burst":
        for s in srcs:
            offset[s] = 0 if s % 2 == 0 else jitter
    else:
        raise ValueError(model)

    per_src_idx: dict[int, int] = defaultdict(int)
    gen: dict[int, int] = {}
    for f in sorted(col.flows, key=lambda f: (f.src, f.flow_id)):
        k = per_src_idx[f.src]
        per_src_idx[f.src] += 1
        gen[f.flow_id] = offset[f.src] + k * spacing
    return gen


# ---------------------------------------------------------------------------
# 2. Private control NoC delivery (XY routing, 1 msg/cy/link)
# ---------------------------------------------------------------------------

def deliver_control(topo: Topology,
                    msgs: list[tuple[int, int, int, int]],
                    t_max: int = 400_000) -> tuple[dict[int, int], dict[str, Any]]:
    """Deliver control messages on the private control NoC.

    msgs: list of (t_inject, src, dst, msg_id). XY route, per-directed-link
    occupancy 1 msg/cycle, per-hop latency = data geometry H/V.
    Returns ({msg_id: t_arrive}, stats).
    """
    inject_ev: dict[int, list] = defaultdict(list)
    for t_inj, s, d, mid in msgs:
        inject_ev[t_inj].append((s, d, mid))
    arrive_ev: dict[int, list] = defaultdict(list)
    queue: dict[int, deque] = defaultdict(deque)
    link_free: dict[tuple[int, int], int] = defaultdict(int)
    result: dict[int, int] = {}
    ingress_hist: dict[int, int] = defaultdict(int)

    remaining = len(msgs)
    t = 0
    max_q = 0
    while t <= t_max and remaining > 0:
        for s, d, mid in inject_ev.pop(t, ()):
            if s == d:
                result[mid] = t
                ingress_hist[t] += 1
                remaining -= 1
            else:
                queue[s].append({"path": topo.dor_path(s, d), "hop": 0,
                                 "mid": mid})
        for node, msg in arrive_ev.pop(t, ()):
            if msg["hop"] >= len(msg["path"]) - 1:
                result[msg["mid"]] = t
                ingress_hist[t] += 1
                remaining -= 1
            else:
                queue[node].append(msg)

        for node in list(queue.keys()):
            q = queue[node]
            if not q:
                continue
            max_q = max(max_q, len(q))
            used: set[tuple[int, int]] = set()
            keep: deque = deque()
            while q:
                msg = q.popleft()
                u = msg["path"][msg["hop"]]
                v = msg["path"][msg["hop"] + 1]
                e = (u, v)
                if e in used or link_free[e] > t:
                    keep.append(msg)
                    continue
                link_free[e] = t + 1
                used.add(e)
                arrive_ev[t + topo.link_lat(u, v)].append(
                    (v, {"path": msg["path"], "hop": msg["hop"] + 1,
                         "mid": msg["mid"]}))
            queue[node] = keep
        t += 1

    stats = {
        "t_done": t,
        "n_msgs": len(msgs),
        "n_delivered": len(result),
        "max_queue": max_q,
        "max_ingress_per_cy": max(ingress_hist.values()) if ingress_hist else 0,
        "routing": "xy",
        "shared_with_data_plane": False,
    }
    return result, stats


# ---------------------------------------------------------------------------
# 3. Capacity-limited interval maps
# ---------------------------------------------------------------------------

class CapMap:
    """Per-key interval set allowing up to `cap` simultaneous intervals."""

    def __init__(self, cap: int = 1):
        self.cap = cap
        self.iv: dict[Any, list[tuple[int, int]]] = defaultdict(list)

    def feasible(self, key: Any, s: int, e: int) -> bool:
        if e <= s:
            return True
        lst = self.iv[key]
        if not lst:
            return True
        # overlap count only rises at interval starts (and at s)
        pts = [s]
        for a, b in lst:
            if s < a < e:
                pts.append(a)
        for p in pts:
            c = 0
            for a, b in lst:
                if a <= p < b:
                    c += 1
            if c + 1 > self.cap:
                return False
        return True

    def earliest(self, key: Any, dur: int, t_min: int) -> int:
        if dur <= 0:
            return t_min
        lst = self.iv[key]
        if not lst:
            return t_min
        cands = {t_min}
        for a, b in lst:
            if b > t_min:
                cands.add(b)
        for t in sorted(cands):
            if self.feasible(key, t, t + dur):
                return t
        return max(b for _, b in lst)

    def reserve(self, key: Any, s: int, e: int) -> None:
        if e > s:
            self.iv[key].append((s, e))


# ---------------------------------------------------------------------------
# 4. Footprints
# ---------------------------------------------------------------------------

@dataclass
class Footprint:
    flow_id: int
    src: int
    edges: list[tuple[tuple[int, int], int]]   # (edge, prefix_delay)
    dst_offsets: list[tuple[int, int]]         # (dst, arrive_offset)
    m: int
    link_dur: int
    ramp_dur: int
    release: int = 0
    pressure: int = 0
    span: int = 0


def build_footprint(topo: Topology, flow: Flow, release: int) -> Footprint:
    sigma = topo.sigma
    if flow.kind == "tree":
        edges = tree_link_schedule(topo, flow)
        dsts = [(d, topo.path_wire_delay(p)) for d, p in flow.paths.items()]
    else:
        d = flow.dsts[0]
        path = flow.paths[d]
        edges = []
        acc = 0
        for i in range(len(path) - 1):
            edges.append(((path[i], path[i + 1]), acc))
            acc += topo.link_lat(path[i], path[i + 1])
        dsts = [(d, acc)]
    span = max((pref for _, pref in edges), default=0)
    return Footprint(
        flow_id=flow.flow_id, src=flow.src, edges=edges, dst_offsets=dsts,
        m=flow.m, link_dur=flow.m * sigma, ramp_dur=flow.m * sigma,
        release=release, span=span,
    )


# ---------------------------------------------------------------------------
# 5. BCFS core
# ---------------------------------------------------------------------------

def _earliest_start(fp: Footprint, link: CapMap, inj: CapMap, ej: CapMap,
                    t_min: int, max_iter: int = 2000) -> int:
    t = t_min
    for _ in range(max_iter):
        cand = t
        got = inj.earliest(fp.src, fp.ramp_dur, t)
        if got > t:
            cand = max(cand, got)
        for e, pref in fp.edges:
            s = t + pref
            got = link.earliest(e, fp.link_dur, s)
            if got > s:
                cand = max(cand, got - pref)
        for d, off in fp.dst_offsets:
            s = t + off
            got = ej.earliest(d, fp.ramp_dur, s)
            if got > s:
                cand = max(cand, got - off)
        if cand <= t:
            return t
        t = cand
    return t


def _commit(fp: Footprint, t0: int, link: CapMap, inj: CapMap, ej: CapMap
            ) -> dict[tuple[int, int], tuple[int, int]]:
    res: dict[tuple[int, int], tuple[int, int]] = {}
    inj.reserve(fp.src, t0, t0 + fp.ramp_dur)
    for e, pref in fp.edges:
        s = t0 + pref
        link.reserve(e, s, s + fp.link_dur)
        res[e] = (s, s + fp.link_dur)
    for d, off in fp.dst_offsets:
        ej.reserve(d, t0 + off, t0 + off + fp.ramp_dur)
    return res


def _order(fps: list[Footprint], mode: Priority, rng: random.Random
           ) -> list[Footprint]:
    if mode == "fcfs":
        return sorted(fps, key=lambda f: (f.release, f.flow_id))
    if mode == "longest":
        return sorted(fps, key=lambda f: (-f.span, f.release, f.flow_id))
    if mode == "criticality":
        return sorted(fps, key=lambda f: (-f.pressure, -f.span, f.release,
                                          f.flow_id))
    if mode == "random":
        out = list(fps)
        rng.shuffle(out)
        return out
    raise ValueError(mode)


def bcfs_schedule(topo: Topology, fps: list[Footprint], *,
                  link: CapMap | None = None,
                  inj: CapMap | None = None,
                  ej: CapMap | None = None,
                  orders: tuple[Priority, ...] = ("criticality", "longest",
                                                  "fcfs"),
                  n_random: int = 2,
                  seed: int = 0) -> dict[str, Any]:
    """Schedule a batch conflict-free; returns best-of-multi-start result.

    If link/inj/ej maps are passed in they are treated as PRE-EXISTING
    reservations (earlier batches) and the winning assignment is committed
    into them.
    """
    ramp_cap = max(1, RAMP_BW * topo.sigma)
    edge_load: dict[tuple[int, int], int] = defaultdict(int)
    for fp in fps:
        for e, _ in fp.edges:
            edge_load[e] += 1
    for fp in fps:
        fp.pressure = sum(edge_load[e] for e, _ in fp.edges)

    base_link = link if link is not None else CapMap(1)
    base_inj = inj if inj is not None else CapMap(ramp_cap)
    base_ej = ej if ej is not None else CapMap(ramp_cap)

    rng = random.Random(seed)
    modes: list[Priority] = list(orders) + ["random"] * n_random

    best = None
    for mi, mode in enumerate(modes):
        # trial on copies of the persistent state
        tl = CapMap(1)
        tl.iv = {k: list(v) for k, v in base_link.iv.items()}
        tl.iv = defaultdict(list, tl.iv)
        ti = CapMap(ramp_cap)
        ti.iv = defaultdict(list, {k: list(v) for k, v in base_inj.iv.items()})
        te = CapMap(ramp_cap)
        te.iv = defaultdict(list, {k: list(v) for k, v in base_ej.iv.items()})

        starts: dict[int, int] = {}
        resv: dict[int, dict] = {}
        finish = 0
        for fp in _order(fps, mode, rng):
            t0 = _earliest_start(fp, tl, ti, te, fp.release)
            starts[fp.flow_id] = t0
            resv[fp.flow_id] = _commit(fp, t0, tl, ti, te)
            done = max((t0 + off + (fp.m - 1) * topo.sigma + RAMP)
                       for _, off in fp.dst_offsets)
            finish = max(finish, done)
        # The CA is online: it can only score the window in front of it, so a
        # locally best batch may leave worse residue for the next window.
        if best is None or finish < best["makespan"]:
            best = {"makespan": finish, "starts": starts, "resv": resv,
                    "order": mode, "trial": mi,
                    "link": tl, "inj": ti, "ej": te}

    assert best is not None
    # commit winner into persistent maps
    if link is not None:
        link.iv = best["link"].iv
    if inj is not None:
        inj.iv = best["inj"].iv
    if ej is not None:
        ej.iv = best["ej"].iv
    return best


def verify_conflict_free(reservations: dict[int, dict[tuple[int, int],
                                                      tuple[int, int]]]
                         ) -> dict[str, Any]:
    """Independent checker: no two granted footprints overlap on any link."""
    per_link: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
    for fid, res in reservations.items():
        for e, (s, t) in res.items():
            per_link[e].append((s, t, fid))
    violations = []
    for e, lst in per_link.items():
        lst.sort()
        for i in range(1, len(lst)):
            ps, pe, pf = lst[i - 1]
            cs, ce, cf = lst[i]
            if cs < pe:
                violations.append({"link": list(e), "a": pf, "b": cf,
                                   "a_iv": [ps, pe], "b_iv": [cs, ce]})
    return {
        "conflict_free": not violations,
        "n_links_used": len(per_link),
        "n_violations": len(violations),
        "examples": violations[:5],
    }


# ---------------------------------------------------------------------------
# 6. Full windowed pipeline
# ---------------------------------------------------------------------------

def schedule_batched(topo: Topology, col: Collective, *,
                     window: int = 64,
                     t_sched: int = 8,
                     aggregate: bool = False,
                     gen_model: GenModel = "uniform_jitter",
                     jitter: int = 64,
                     spacing: int = 1,
                     seed: int = 0,
                     orders: tuple[Priority, ...] = ("criticality", "longest",
                                                     "fcfs"),
                     n_random: int = 2) -> dict[str, Any]:
    """Staggered requests -> XY control NoC -> CA window batches -> BCFS.

    window <= 0 means "one batch": the CA waits until every request has
    arrived before arbitrating (the synchronous / barrier discipline).
    aggregate=True: a source sends ONE request covering all its flows
    (48 control messages instead of one per flow).
    """
    ca = central_arbiter_node()
    flow_by_id = {f.flow_id: f for f in col.flows}

    # (1) staggered generation
    gen = generate_request_times(col, model=gen_model, jitter=jitter,
                                 spacing=spacing, seed=seed)

    # (2) request units -> CA over the private control NoC (XY routing)
    units: dict[int, list[int]] = {}      # unit_id -> flow ids
    unit_src: dict[int, int] = {}
    if aggregate:
        by_src: dict[int, list[int]] = defaultdict(list)
        for f in col.flows:
            by_src[f.src].append(f.flow_id)
        for s, fids in by_src.items():
            uid = s
            units[uid] = sorted(fids)
            unit_src[uid] = s
    else:
        for f in col.flows:
            units[f.flow_id] = [f.flow_id]
            unit_src[f.flow_id] = f.src
    unit_gen = {u: min(gen[f] for f in fids) for u, fids in units.items()}

    req_msgs = [(unit_gen[u], unit_src[u], ca, u) for u in units]
    req_arrive, req_stats = deliver_control(topo, req_msgs)

    # (3) window batching (tumbling windows aligned to absolute time)
    single_batch = window <= 0
    batches: dict[int, list[int]] = defaultdict(list)
    for u, ta in req_arrive.items():
        batches[0 if single_batch else ta // window].append(u)

    # (4) grants: each batch decided at window close + t_sched
    grant_msgs = []
    decide_t: dict[int, int] = {}
    for k, uids in batches.items():
        last_arr = max(req_arrive[u] for u in uids)
        t_close = last_arr if single_batch else (k + 1) * window
        t_decide = max(t_close, last_arr) + t_sched
        decide_t[k] = t_decide
        for u in uids:
            grant_msgs.append((t_decide, ca, unit_src[u], u))
    grant_arrive, grant_stats = deliver_control(topo, grant_msgs)

    # (5) BCFS per batch, in window order; reservations persist across batches
    def _run(orders_: tuple[Priority, ...], n_rand: int) -> tuple[int, dict, dict, list]:
        link = CapMap(1)
        inj = CapMap(max(1, RAMP_BW * topo.sigma))
        ej = CapMap(max(1, RAMP_BW * topo.sigma))
        starts: dict[int, int] = {}
        resv: dict[int, dict] = {}
        info = []
        mk = 0
        for k in sorted(batches):
            fps = []
            for u in batches[k]:
                rel = grant_arrive.get(u, decide_t[k])
                for fid in units[u]:
                    fps.append(build_footprint(topo, flow_by_id[fid], rel))
            res = bcfs_schedule(topo, fps, link=link, inj=inj, ej=ej,
                                orders=orders_, n_random=n_rand, seed=seed + k)
            starts.update(res["starts"])
            resv.update(res["resv"])
            mk = max(mk, res["makespan"])
            info.append({
                "window": k,
                "n_requests": len(batches[k]),
                "n_flows": len(fps),
                "t_decide": decide_t[k],
                "batch_makespan": res["makespan"],
                "winning_order": res["order"],
            })
        return mk, starts, resv, info

    sched_mk, all_starts, all_resv, batch_info = _run(orders, n_random)
    fcfs_mk, _, _, _ = _run(("fcfs",), 0)

    grants = [
        Grant(flow_id=fid, src=flow_by_id[fid].src,
              t_grant_arrive=grant_arrive.get(
                  fid if not aggregate else flow_by_id[fid].src, 0),
              t_data_start=all_starts[fid],
              reservations=all_resv[fid])
        for fid in sorted(all_starts)
    ]

    verify = verify_conflict_free(all_resv)
    t_first_start = min(all_starts.values()) if all_starts else 0
    return {
        "grants": grants,
        "makespan_sched": sched_mk,
        "makespan_fcfs": fcfs_mk,
        "bcfs_gain": (fcfs_mk / sched_mk - 1.0) if sched_mk else None,
        "data_span": sched_mk - t_first_start,
        "t_first_data_start": t_first_start,
        "n_batches": len(batches),
        "n_request_units": len(units),
        "aggregate": aggregate,
        "window": window,
        "t_sched": t_sched,
        "gen_model": gen_model,
        "jitter": jitter,
        "batches": batch_info[:24],
        "verify": verify,
        "ctrl": {
            "ca_node": ca,
            "ca_coord": list(coord(ca)),
            "routing": "xy",
            "shared_with_data_plane": False,
            "req": req_stats,
            "grant": grant_stats,
            "t_first_request_gen": min(unit_gen.values()) if unit_gen else 0,
            "t_last_request_gen": max(unit_gen.values()) if unit_gen else 0,
            "t_first_request_arrive": min(req_arrive.values()) if req_arrive else 0,
            "t_last_request_arrive": max(req_arrive.values()) if req_arrive else 0,
            "t_last_grant_arrive": max(grant_arrive.values()) if grant_arrive else 0,
            "arrival_spread": (max(req_arrive.values()) - min(req_arrive.values())
                               if req_arrive else 0),
            "R_rg": (max(grant_arrive.values()) - min(unit_gen.values())
                     if grant_arrive and unit_gen else 0),
        },
    }


if __name__ == "__main__":
    import json
    from rg_collectives import build_collective

    for kind in ("mesh", "torus"):
        topo = Topology(kind)
        for pat in ("alltoall", "reduce"):
            col = build_collective(topo, pat, m=4)
            for agg in (False, True):
                for W in (16, 64, 256, 0):
                    r = schedule_batched(topo, col, window=W, t_sched=8,
                                         aggregate=agg)
                    print(f"{kind:6} {pat:9} agg={int(agg)} W={W:<5} "
                          f"units={r['n_request_units']:5} b={r['n_batches']:3} "
                          f"sched={r['makespan_sched']:6} "
                          f"fcfs={r['makespan_fcfs']:6} "
                          f"gain={r['bcfs_gain']:+.3f} "
                          f"cf={int(r['verify']['conflict_free'])} "
                          f"R_rg={r['ctrl']['R_rg']:5} "
                          f"span={r['data_span']:6}")
