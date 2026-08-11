#!/usr/bin/env python3
"""A family of iSLIP / BvN-style schedulers for a 2D mesh (link + space).

Why the crossbar algorithms do not transfer directly
----------------------------------------------------
iSLIP and Birkhoff-von Neumann allocate the ports of ONE crossbar: the
resource is a bipartite matching, and a *permutation matrix* is the unit of
allocation (each input and each output used once per slot).

A 2D mesh allocates *directed links across time*, and a flow consumes a whole
path, not a single output port. The natural unit is therefore

    LDPS = Link-Disjoint Path Set
         = a set of flows whose routes (XY path for unicast, tree edge set
           for multicast) are pairwise link-disjoint, and which respect
           source/destination ramp bandwidth.

With that substitution the classic algorithms map over cleanly:

    permutation matrix        ->  LDPS (one "slot configuration")
    BvN decomposition         ->  partition the flow set into fewest LDPS
                                  rounds; lower bound = max_e (#flows on e)
    iSLIP grant/accept        ->  each LINK grants one requester (RR pointer);
                                  a flow accepts only if EVERY link on its
                                  path granted it  (mesh-specific: a crossbar
                                  needs 1 output to agree, a mesh needs all)

Two structural classes fall out, and they are the axis of the Pareto study:

  * SLOT / PHASE based (bvn, mwm, islip, pim, latin): a round is a single
    LDPS, so conflict-freedom is guaranteed BY CONSTRUCTION and the scheduler
    only needs a link-used bitmap. The price is the *convoy effect* — the
    round lasts as long as its slowest member.
  * PIPELINED (bcfs, greedy_ff): flows may start at arbitrary offsets and are
    packed into holes of a per-link interval map. No convoy effect, but the
    scheduler must carry the interval tables.

An important negative result this framing exposes: a permutation (which is
optimal to schedule on a crossbar) is NOT link-disjoint under XY routing on a
mesh, so a Latin-square / fixed-BvN table needs more than N-1 rounds here.

Every algorithm emits the same artifact — a list of ``Grant`` with rigid
wormhole reservations — so the existing bufferless / bufferable DES and the
independent ``verify_conflict_free`` checker apply unchanged.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from rg_topo import RAMP, RAMP_BW, Topology, central_arbiter_node, coord
from rg_collectives import Collective, Flow, tree_link_schedule
from rg_arbiter import Grant
from rg_batch_sched import (
    CapMap, Footprint, build_footprint, bcfs_schedule, deliver_control,
    generate_request_times, verify_conflict_free,
)

Algo = Literal["bvn_mesh", "mwm_mesh", "latin_mesh", "bcfs",
               "islip_mesh", "pim_mesh", "greedy_ff"]

SLOT_ALGOS: tuple[Algo, ...] = ("bvn_mesh", "mwm_mesh", "latin_mesh",
                                "islip_mesh", "pim_mesh")
BATCH_ALGOS: tuple[Algo, ...] = ("bvn_mesh", "mwm_mesh", "latin_mesh", "bcfs")
INCREMENTAL_ALGOS: tuple[Algo, ...] = ("islip_mesh", "pim_mesh", "greedy_ff")
ALL_ALGOS: tuple[Algo, ...] = ("bvn_mesh", "mwm_mesh", "latin_mesh", "bcfs",
                               "islip_mesh", "pim_mesh", "greedy_ff")

ALGO_CLASS: dict[str, str] = {
    "bvn_mesh": "batch/slot",
    "mwm_mesh": "batch/slot",
    "latin_mesh": "batch/slot",
    "bcfs": "batch/pipelined",
    "islip_mesh": "incremental/slot",
    "pim_mesh": "incremental/slot",
    "greedy_ff": "incremental/pipelined",
}

ALGO_SELECT: dict[str, str] = {
    "bvn_mesh": "first_fit",
    "mwm_mesh": "max_weight",
    "latin_mesh": "algebraic",
    "islip_mesh": "rr_pointer",
    "pim_mesh": "random",
}


# ---------------------------------------------------------------------------
# 1. LDPS primitives
# ---------------------------------------------------------------------------

def link_set(topo: Topology, flow: Flow) -> frozenset[tuple[int, int]]:
    """Directed links a flow occupies: XY path (unicast) or tree edges."""
    if flow.kind == "tree":
        return frozenset(e for e, _ in tree_link_schedule(topo, flow))
    d = flow.dsts[0]
    path = flow.paths[d]
    return frozenset((path[i], path[i + 1]) for i in range(len(path) - 1))


def fp_tail(fp: Footprint) -> int:
    """Cycles after t0 until every resource this footprint touches is free."""
    link_tail = max((pref + fp.link_dur for _, pref in fp.edges), default=0)
    ramp_tail = max((off + fp.ramp_dur for _, off in fp.dst_offsets),
                    default=0)
    return max(link_tail, ramp_tail, fp.ramp_dur)


def fp_eject(fp: Footprint) -> int:
    """Cycles after t0 until the last flit is ejected at a destination."""
    return max((off + fp.ramp_dur + RAMP for _, off in fp.dst_offsets),
               default=RAMP)


@dataclass
class MeshFlow:
    """A schedulable unit: footprint + its LDPS link set + priorities."""
    fp: Footprint
    links: frozenset[tuple[int, int]]
    dsts: tuple[int, ...]
    pressure: int = 0        # sum of link loads -> "most constrained"
    algebraic: int = 0       # ROM round index for latin_mesh
    tail: int = 0
    eject: int = 0


def build_mesh_flows(topo: Topology, col: Collective,
                     release: dict[int, int]) -> list[MeshFlow]:
    mfs: list[MeshFlow] = []
    for f in col.flows:
        fp = build_footprint(topo, f, release.get(f.flow_id, 0))
        ls = link_set(topo, f)
        mfs.append(MeshFlow(fp=fp, links=ls, dsts=tuple(f.dsts),
                            tail=fp_tail(fp), eject=fp_eject(fp)))
    load = link_load(mfs)
    for mf in mfs:
        mf.pressure = sum(load[e] for e in mf.links)
        mf.fp.pressure = mf.pressure
    _assign_algebraic(topo, mfs)
    return mfs


def link_load(mfs: list[MeshFlow]) -> dict[tuple[int, int], int]:
    load: dict[tuple[int, int], int] = defaultdict(int)
    for mf in mfs:
        for e in mf.links:
            load[e] += 1
    return load


def max_link_load(mfs: list[MeshFlow]) -> int:
    """Lower bound on the number of LDPS rounds (the Birkhoff-count analog)."""
    load = link_load(mfs)
    return max(load.values()) if load else 0


def _assign_algebraic(topo: Topology, mfs: list[MeshFlow]) -> None:
    """ROM order for latin_mesh: alltoall -> shift k = (d - s) mod N.

    For an all-to-all this reproduces the Latin-square / round-robin
    permutation schedule that is optimal on a crossbar. Other patterns fall
    back to a deterministic (src, dst) rotation, which is equally state-free.
    """
    n = topo.n
    for mf in mfs:
        s = mf.fp.src
        if len(mf.dsts) == 1:
            mf.algebraic = (mf.dsts[0] - s) % n
        else:
            mf.algebraic = s % n


# ---------------------------------------------------------------------------
# 2. Slot (phase) machinery — one round = one LDPS
# ---------------------------------------------------------------------------

@dataclass
class SlotState:
    """Per-link round-robin pointers + accounting, persistent across slots."""
    ptr: dict[tuple[int, int], int] = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    n_grant_ops: int = 0     # arbitration work counter (for the cost model)
    n_iter_ops: int = 0


def _priority_key(select: str, st: SlotState):
    """Sequential (path-level) allocation order used to complete a round."""
    if select == "first_fit":
        return lambda mf: (mf.fp.flow_id,)
    if select == "max_weight":
        return lambda mf: (-mf.pressure, -mf.tail, mf.fp.flow_id)
    if select == "algebraic":
        return lambda mf: (mf.algebraic, mf.fp.flow_id)
    if select == "random":
        return lambda mf: (st.rng.random(),)
    if select == "rr_pointer":
        base = st.ptr.get(_GLOBAL, 0)
        return lambda mf: ((mf.fp.flow_id - base) % _MOD, mf.fp.flow_id)
    raise ValueError(select)


_GLOBAL = (-1, -1)     # key for the round-robin token in SlotState.ptr
_MOD = 1 << 30


class _RoundBuilder:
    """Accumulates one LDPS: link-disjoint, ramp-bandwidth respecting."""

    def __init__(self, ramp_cap: int):
        self.ramp_cap = ramp_cap
        self.links: set[tuple[int, int]] = set()
        self.inj: dict[int, int] = defaultdict(int)
        self.ej: dict[int, int] = defaultdict(int)
        self.acc: list[MeshFlow] = []

    def feasible(self, mf: MeshFlow) -> bool:
        if mf.links & self.links:
            return False
        if self.inj[mf.fp.src] >= self.ramp_cap:
            return False
        return all(self.ej[d] < self.ramp_cap for d in mf.dsts)

    def add(self, mf: MeshFlow) -> None:
        self.links |= mf.links
        self.inj[mf.fp.src] += 1
        for d in mf.dsts:
            self.ej[d] += 1
        self.acc.append(mf)


def _islip_iterations(rb: _RoundBuilder, pending: list[MeshFlow],
                      st: SlotState, iters: int, select: str) -> int:
    """Distributed request / per-link grant / path-wide accept, `iters` times.

    This is the literal iSLIP transplant. Its yield is the headline
    mesh-vs-crossbar result: a flow needs UNANIMOUS grants from all h links on
    its path, so with k contenders per link the success probability decays
    like k^-(h-1). For h≈6 and k≈80 that is effectively zero, which is why a
    purely link-local arbiter livelocks on a mesh and every member of the
    family needs the sequential (path-level) completion pass below.

    Returns the number of flows admitted by unanimity alone.
    """
    n_unan = 0
    for _ in range(max(0, iters)):
        requests: dict[tuple[int, int], list[MeshFlow]] = defaultdict(list)
        elig: list[MeshFlow] = []
        for mf in pending:
            if rb.feasible(mf):
                elig.append(mf)
                for e in mf.links:
                    requests[e].append(mf)
        if not elig:
            break
        st.n_iter_ops += 1
        granted: dict[int, set[tuple[int, int]]] = defaultdict(set)
        for e, cands in requests.items():
            st.n_grant_ops += len(cands)
            if select == "random":
                w = st.rng.choice(cands)
            else:
                p = st.ptr.get(e, 0)
                after = [mf for mf in cands if mf.fp.flow_id >= p]
                w = min(after or cands, key=lambda mf: mf.fp.flow_id)
            granted[w.fp.flow_id].add(e)
        by_id = {mf.fp.flow_id: mf for mf in elig}
        winners = [by_id[fid] for fid, g in granted.items()
                   if g >= by_id[fid].links]
        if not winners:
            break
        winners.sort(key=lambda mf: (-mf.pressure, mf.fp.flow_id))
        got = False
        for mf in winners:
            if not rb.feasible(mf):
                continue
            rb.add(mf)
            n_unan += 1
            got = True
            if select == "rr_pointer":
                for e in mf.links:      # iSLIP: advance only on accept
                    st.ptr[e] = mf.fp.flow_id + 1
        if not got:
            break
        done = {mf.fp.flow_id for mf in rb.acc}
        pending = [mf for mf in pending if mf.fp.flow_id not in done]
    return n_unan


def maximal_ldps(topo: Topology, cands: list[MeshFlow], weight_fn,
                 *, rb: _RoundBuilder | None = None) -> list[MeshFlow]:
    """Greedily extract one MAXIMAL link-disjoint path set.

    Visits candidates in `weight_fn` order and keeps a flow whenever its link
    set and both ramps are still free in the round under construction. Pass an
    existing `rb` to extend a partially built round. Maximal by construction:
    on return no remaining candidate fits.
    """
    if rb is None:
        rb = _RoundBuilder(max(1, RAMP_BW * topo.sigma))
    for mf in sorted(cands, key=weight_fn):
        if rb.feasible(mf):
            rb.add(mf)
    return rb.acc


def _match_slot(topo: Topology, mfs: list[MeshFlow], select: str,
                st: SlotState, iters: int,
                ramp_cap: int) -> tuple[list[MeshFlow], int]:
    """Build one maximal LDPS. Returns (accepted, n_admitted_by_unanimity)."""
    rb = _RoundBuilder(ramp_cap)
    n_unan = 0
    if select in ("rr_pointer", "random"):
        n_unan = _islip_iterations(rb, mfs, st, iters, select)
    done = {mf.fp.flow_id for mf in rb.acc}
    rest = [mf for mf in mfs if mf.fp.flow_id not in done]
    # sequential path-level completion (see _islip_iterations for why it is
    # unavoidable on a mesh)
    st.n_grant_ops += len(rest)
    maximal_ldps(topo, rest, _priority_key(select, st), rb=rb)
    if select == "rr_pointer" and rb.acc:
        st.ptr[_GLOBAL] = max(mf.fp.flow_id for mf in rb.acc) + 1
    return rb.acc, n_unan


class _FreeAt:
    """One `free_at` register per resource (vs BCFS's full interval table)."""

    def __init__(self, cap: int):
        self.cap = cap
        self.t: dict[Any, list[int]] = {}

    def ready(self, key, slot: int) -> int:
        v = self.t.get(key)
        return 0 if v is None else v[slot]

    def take(self, key, slot: int, until: int) -> None:
        v = self.t.setdefault(key, [0] * self.cap)
        v[slot] = until
        v.sort()


def slot_schedule(topo: Topology, mfs: list[MeshFlow], *,
                  select: str, iters: int = 1, seed: int = 0,
                  incremental: bool = False,
                  t_floor: int = 0) -> dict[str, Any]:
    """Partition flows into LDPS rounds and lay the rounds out in time.

    Rounds keep phase semantics — every member of a round shares one start
    time — but consecutive rounds are *pipelined* through one `free_at`
    register per link/ramp instead of a global barrier, so a round only waits
    for the links it actually reuses. The barrier makespan (Σ round durations)
    is still reported as `convoy_makespan` to quantify what pipelining buys.

    incremental=False (batch): every flow is eligible at once — the offline
      BvN-style round partition.
    incremental=True: only flows whose grant already arrived are eligible, and
      the RR pointers carry across rounds (online discipline).
    """
    ramp_cap = max(1, RAMP_BW * topo.sigma)
    st = SlotState(rng=random.Random(seed))
    link_free = _FreeAt(1)
    inj_free = _FreeAt(ramp_cap)
    ej_free = _FreeAt(ramp_cap)
    remaining = list(mfs)
    starts: dict[int, int] = {}
    resv: dict[int, dict[tuple[int, int], tuple[int, int]]] = {}
    round_of: dict[int, int] = {}
    rounds: list[dict[str, Any]] = []
    t_prev = t_floor
    makespan = t_floor
    convoy = 0
    n_unan_total = 0
    guard = 0

    while remaining:
        guard += 1
        if guard > 20000:
            raise RuntimeError("slot_schedule did not converge")
        if incremental:
            ready = [mf for mf in remaining if mf.fp.release <= t_prev]
            if not ready:
                t_prev = min(mf.fp.release for mf in remaining)
                continue
        else:
            ready = remaining

        acc, n_unan = _match_slot(topo, ready, select, st, iters, ramp_cap)
        if not acc:
            nxt = [mf.fp.release for mf in remaining if mf.fp.release > t_prev]
            if not nxt:
                raise RuntimeError("empty LDPS with nothing pending")
            t_prev = min(nxt)
            continue
        n_unan_total += n_unan

        # ---- one shared start time for the whole round (phase semantics)
        t0 = t_prev
        inj_slot: dict[int, int] = defaultdict(int)
        ej_slot: dict[int, int] = defaultdict(int)
        for mf in acc:
            t0 = max(t0, mf.fp.release)
            for e, pref in mf.fp.edges:
                t0 = max(t0, link_free.ready(e, 0) - pref)
            s = inj_slot[mf.fp.src]
            t0 = max(t0, inj_free.ready(mf.fp.src, s))
            inj_slot[mf.fp.src] = s + 1
            for d, off in mf.fp.dst_offsets:
                k = ej_slot[d]
                t0 = max(t0, ej_free.ready(d, k) - off)
                ej_slot[d] = k + 1

        dur = max(mf.tail for mf in acc)
        inj_slot.clear()
        ej_slot.clear()
        for mf in acc:
            starts[mf.fp.flow_id] = t0
            round_of[mf.fp.flow_id] = len(rounds)
            r: dict[tuple[int, int], tuple[int, int]] = {}
            for e, pref in mf.fp.edges:
                s = t0 + pref
                r[e] = (s, s + mf.fp.link_dur)
                link_free.take(e, 0, s + mf.fp.link_dur)
            resv[mf.fp.flow_id] = r
            k = inj_slot[mf.fp.src]
            inj_free.take(mf.fp.src, k, t0 + mf.fp.ramp_dur)
            inj_slot[mf.fp.src] = k + 1
            for d, off in mf.fp.dst_offsets:
                k = ej_slot[d]
                ej_free.take(d, k, t0 + off + mf.fp.ramp_dur)
                ej_slot[d] = k + 1
            makespan = max(makespan, t0 + mf.eject)
        rounds.append({"round": len(rounds), "start": t0, "dur": dur,
                       "n_flows": len(acc)})
        convoy += dur
        acc_ids = {mf.fp.flow_id for mf in acc}
        remaining = [mf for mf in remaining if mf.fp.flow_id not in acc_ids]
        t_prev = t0

    lb = max_link_load(mfs)
    return {
        "starts": starts,
        "resv": resv,
        "round_of": round_of,
        "n_rounds": len(rounds),
        "round_lb": lb,
        "round_ratio": (round(len(rounds) / lb, 3) if lb else None),
        "rounds": rounds[:32],
        "makespan_sched": makespan,
        "convoy_span": convoy,
        "grant_ops": st.n_grant_ops,
        "iter_ops": st.n_iter_ops,
        "n_unanimous": n_unan_total,
        "unanimous_frac": (round(n_unan_total / len(mfs), 4) if mfs else None),
        "mean_flows_per_round": (
            round(len(mfs) / len(rounds), 2) if rounds else None),
    }


# ---------------------------------------------------------------------------
# 3. Pipelined reference members (interval packing, no convoy effect)
# ---------------------------------------------------------------------------

def pipelined_schedule(topo: Topology, mfs: list[MeshFlow], *,
                       multi_start: bool, seed: int = 0) -> dict[str, Any]:
    """greedy_ff (arrival-order first fit) / bcfs (criticality + multi-start).

    Both pack rigid footprints into per-link interval-map holes, so flows may
    start at arbitrary offsets — no round structure, no convoy effect.
    """
    fps = [mf.fp for mf in mfs]
    orders = ("criticality", "longest", "fcfs") if multi_start else ("fcfs",)
    link = CapMap(1)
    inj = CapMap(max(1, RAMP_BW * topo.sigma))
    ej = CapMap(max(1, RAMP_BW * topo.sigma))
    res = bcfs_schedule(topo, fps, link=link, inj=inj, ej=ej, orders=orders,
                        n_random=2 if multi_start else 0, seed=seed)
    by_id = {mf.fp.flow_id: mf for mf in mfs}
    makespan = 0
    for fid, t0 in res["starts"].items():
        makespan = max(makespan, t0 + by_id[fid].eject)
    return {
        "starts": res["starts"],
        "resv": res["resv"],
        "round_of": {},
        "n_rounds": None,
        "round_lb": max_link_load(mfs),
        "round_ratio": None,
        "rounds": [],
        "makespan_sched": makespan,
        "convoy_span": None,
        "grant_ops": len(fps) * (5 if multi_start else 1),
        "iter_ops": len(fps),
        "n_unanimous": None,
        "unanimous_frac": None,
        "mean_flows_per_round": None,
        "winning_order": res["order"],
    }


# ---------------------------------------------------------------------------
# 4. Control plane (private NoC, XY, half-Manhattan latency)
# ---------------------------------------------------------------------------

def control_phase(topo: Topology, col: Collective, *,
                  incremental: bool, window: int, t_sched: int,
                  aggregate: bool, gen_model: str, jitter: int,
                  seed: int) -> tuple[dict[int, int], dict[str, Any]]:
    """Staggered VOQ requests -> CA(4,0) -> arbitration -> grants back.

    Default discipline (``aggregate=False``): **one request = one VOQ**.
    For unicast alltoall each source holds N−1 VOQs
    ``VOQ[s→d], d≠s``, so the fabric carries up to N·(N−1) request units
    (48×47 = 2256 on 8×6). ``aggregate=True`` collapses a source's VOQs
    into a single control message (legacy / sensitivity only).

    Returns ({flow_id: release_time}, ctrl_stats). Incremental algorithms
    arbitrate each VOQ request on arrival (window is bypassed); batch
    algorithms close a tumbling window of W cycles first.
    """
    ca = central_arbiter_node()
    gen = generate_request_times(col, model=gen_model, jitter=jitter,
                                 spacing=1, seed=seed)
    units: dict[int, list[int]] = {}
    unit_src: dict[int, int] = {}
    if aggregate:
        # Legacy: one control message covers every VOQ of a source.
        by_src: dict[int, list[int]] = defaultdict(list)
        for f in col.flows:
            by_src[f.src].append(f.flow_id)
        for s, fids in by_src.items():
            units[s] = sorted(fids)
            unit_src[s] = s
    else:
        # Canonical: one request per VOQ / flow.
        for f in col.flows:
            units[f.flow_id] = [f.flow_id]
            unit_src[f.flow_id] = f.src
    unit_gen = {u: min(gen[f] for f in fids) for u, fids in units.items()}

    req_arrive, req_stats = deliver_control(
        topo, [(unit_gen[u], unit_src[u], ca, u) for u in units])

    grant_msgs = []
    if incremental:
        for u, ta in req_arrive.items():
            grant_msgs.append((ta + t_sched, ca, unit_src[u], u))
        n_batches = len(req_arrive)
    else:
        batches: dict[int, list[int]] = defaultdict(list)
        single = window <= 0
        for u, ta in req_arrive.items():
            batches[0 if single else ta // window].append(u)
        for k, uids in batches.items():
            last = max(req_arrive[u] for u in uids)
            t_close = last if single else (k + 1) * window
            t_dec = max(t_close, last) + t_sched
            for u in uids:
                grant_msgs.append((t_dec, ca, unit_src[u], u))
        n_batches = len(batches)
    grant_arrive, grant_stats = deliver_control(topo, grant_msgs)

    release: dict[int, int] = {}
    for u, fids in units.items():
        r = grant_arrive.get(u, 0)
        for fid in fids:
            release[fid] = r

    # Per-source VOQ counts (alltoall → N−1 each; trees → 1 per src).
    voq_per_src: dict[int, int] = defaultdict(int)
    for f in col.flows:
        voq_per_src[f.src] += 1

    stats = {
        "ca_node": ca,
        "ca_coord": list(coord(ca)),
        "routing": "xy",
        "ctrl_delay_policy": "half_manhattan_linkdelay",
        "shared_with_data_plane": False,
        "request_unit": ("source_aggregate" if aggregate
                         else "voq"),
        "aggregate": aggregate,
        "n_voqs": len(col.flows),
        "n_voq_per_src_max": max(voq_per_src.values()) if voq_per_src else 0,
        "n_request_units": len(units),
        "n_batches": n_batches,
        "req": req_stats,
        "grant": grant_stats,
        "t_last_request_arrive": max(req_arrive.values()) if req_arrive else 0,
        "t_last_grant_arrive": max(grant_arrive.values()) if grant_arrive else 0,
        "arrival_spread": (max(req_arrive.values()) - min(req_arrive.values())
                           if req_arrive else 0),
        "R_rg": (max(grant_arrive.values()) - min(unit_gen.values())
                 if grant_arrive and unit_gen else 0),
    }
    return release, stats


# ---------------------------------------------------------------------------
# 5. Top-level entry
# ---------------------------------------------------------------------------

def schedule_mesh(topo: Topology, col: Collective, algo: Algo, *,
                  iters: int = 1, window: int = 64, t_sched: int = 8,
                  aggregate: bool = False,
                  gen_model: str = "uniform_jitter", jitter: int = 64,
                  seed: int = 0) -> dict[str, Any]:
    """Run one member of the family end to end; returns grants + stats.

    By default each request is one VOQ (``aggregate=False``): alltoall has
    N·(N−1) request units. Set ``aggregate=True`` only for sensitivity.
    """
    if algo not in ALL_ALGOS:
        raise ValueError(f"unknown algo: {algo}")
    incremental = algo in INCREMENTAL_ALGOS
    release, ctrl = control_phase(
        topo, col, incremental=incremental, window=window, t_sched=t_sched,
        aggregate=aggregate, gen_model=gen_model, jitter=jitter, seed=seed)
    mfs = build_mesh_flows(topo, col, release)

    if algo in SLOT_ALGOS:
        res = slot_schedule(topo, mfs, select=ALGO_SELECT[algo], iters=iters,
                            seed=seed, incremental=incremental)
    elif algo == "bcfs":
        res = pipelined_schedule(topo, mfs, multi_start=True, seed=seed)
    else:  # greedy_ff
        res = pipelined_schedule(topo, mfs, multi_start=False, seed=seed)

    flow_by_id = {f.flow_id: f for f in col.flows}
    grants = [
        Grant(flow_id=fid, src=flow_by_id[fid].src,
              t_grant_arrive=release.get(fid, 0),
              t_data_start=t0,
              reservations=res["resv"][fid])
        for fid, t0 in sorted(res["starts"].items())
    ]
    verify = verify_conflict_free(res["resv"])
    t_first = min(res["starts"].values()) if res["starts"] else 0
    span = res["makespan_sched"] - t_first
    return {
        "algo": algo,
        "algo_class": ALGO_CLASS[algo],
        "select": ALGO_SELECT.get(algo, "interval_packing"),
        "iters": iters if algo in ("islip_mesh", "pim_mesh") else 1,
        "grants": grants,
        "n_flows": len(mfs),
        "makespan_sched": res["makespan_sched"],
        "data_span": span,
        "t_first_data_start": t_first,
        "n_rounds": res["n_rounds"],
        "round_lb": res["round_lb"],
        "round_ratio": res["round_ratio"],
        "rounds": res["rounds"],
        "mean_flows_per_round": res["mean_flows_per_round"],
        # Σ round durations: what a hard per-round barrier would have cost,
        # versus the `free_at`-pipelined span actually achieved.
        "convoy_span": res["convoy_span"],
        "convoy_ratio": (round(res["convoy_span"] / span, 3)
                         if res["convoy_span"] and span else None),
        "n_unanimous": res["n_unanimous"],
        "unanimous_frac": res["unanimous_frac"],
        "grant_ops": res["grant_ops"],
        "iter_ops": res["iter_ops"],
        "round_of": res["round_of"],
        "verify": verify,
        "ctrl": ctrl,
    }


def verify_rounds_disjoint(topo: Topology, col: Collective,
                           res: dict[str, Any]) -> dict[str, Any]:
    """Independent recheck that every round really is an LDPS.

    Re-derives each flow's link set from the topology (not from the reservation
    table the scheduler wrote) and confirms pairwise disjointness inside every
    round, plus that ramp usage stays within RAMP_BW·sigma.
    """
    round_of: dict[int, int] = res["round_of"]
    if not round_of:
        return {"n_rounds": 0, "overlaps": 0, "ramp_violations": 0,
                "disjoint": True}
    by_round: dict[int, list[int]] = defaultdict(list)
    for fid, r in round_of.items():
        by_round[r].append(fid)
    flow_by_id = {f.flow_id: f for f in col.flows}
    cap = max(1, RAMP_BW * topo.sigma)
    bad = 0
    ramp_bad = 0
    for r, fids in by_round.items():
        seen: set[tuple[int, int]] = set()
        inj: dict[int, int] = defaultdict(int)
        ej: dict[int, int] = defaultdict(int)
        for fid in fids:
            f = flow_by_id[fid]
            ls = link_set(topo, f)
            if ls & seen:
                bad += 1
            seen |= ls
            inj[f.src] += 1
            for d in f.dsts:
                ej[d] += 1
        if any(v > cap for v in inj.values()) or any(v > cap
                                                     for v in ej.values()):
            ramp_bad += 1
    return {"n_rounds": len(by_round), "overlaps": bad,
            "ramp_violations": ramp_bad,
            "disjoint": bad == 0 and ramp_bad == 0}


if __name__ == "__main__":
    from rg_collectives import build_collective
    import time

    for kind in ("mesh",):
        topo = Topology(kind)
        for pat in ("alltoall", "reduce", "allgather", "broadcast"):
            col = build_collective(topo, pat, m=4,
                                   sync=pat in ("allgather", "allreduce"))
            print(f"--- {kind} {pat} (flows={len(col.flows)}) ---")
            for algo in ALL_ALGOS:
                t0 = time.time()
                r = schedule_mesh(topo, col, algo, iters=1)
                print(f"  {algo:12} {ALGO_CLASS[algo]:22} "
                      f"mk={r['makespan_sched']:6} "
                      f"rounds={str(r['n_rounds']):>5}/lb={r['round_lb']:<4} "
                      f"cvy={str(r['convoy_ratio']):>6} "
                      f"un={str(r['unanimous_frac']):>7} "
                      f"cf={int(r['verify']['conflict_free'])} "
                      f"{time.time()-t0:.2f}s")
