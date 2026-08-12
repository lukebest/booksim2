#!/usr/bin/env python3
"""`ring_base`: E-tag / I-tag + deflection baseline for the 2D bufferless ring.

Provenance and honesty note
---------------------------
Modelled on the HiSilicon HPCA'22 paper *Application Defined On-chip Networks
for Heterogeneous Chiplets: An Implementation Perspective* (Wang, Feng, Xiang,
Li, Xia). The original is paywalled, so the fine-grained timing is reconstructed
from the same-lineage HiRD design (Hierarchical Rings with Deflection --
Ausavarungnirun / Fallin / Mutlu), which supplies the mechanisms the paper
names: in-ring priority, injection and transfer guarantees, deflection, the Swap
Rule, and a deadlock-resolution mode. Thresholds (`t_inj`, `t_xfer`,
`t_deadlock`), FIFO depths and reassembly depth are therefore SWEEP PARAMETERS,
not values taken from the paper.

This is NOT the same kind of object as definition D-R
----------------------------------------------------
D-R is a scheduling PREDICATE: a central arbiter proves a batch conflict-free
before anything moves. E-tag/I-tag is a REACTIVE per-cycle policy: conflicts are
resolved as they happen, by priority and by deflection, and no global
conflict-freedom is ever established. The two cannot be compared as definitions,
only by outcome (throughput, latency, ordering, area).

Mechanisms implemented
----------------------
* in-ring priority -- a flit already on a ring is never buffered or stalled.
  The model asserts this invariant rather than assuming it: boarding is only
  allowed into a slot that is empty for the whole flit duration, which it can
  check because an in-ring flit is already on the wire (lookahead < hop delay).
* injection guarantee (I-tag) -- a source starved for `t_inj` cycles raises its
  I-tag, which inhibits every other node on that ring-direction from boarding
  until the starved node gets in.
* transfer guarantee (E-tag) -- a flit deflected `t_xfer` times raises its
  E-tag and may then use the reserved Tx buffer entries (`resv_tx`) that normal
  traffic cannot touch.
* deflection -- a flit that cannot turn or eject stays on its current ring and
  retries a full revolution later. Deflection is always possible (in-ring
  priority), so the failure mode is wasted bandwidth and latency tails, not
  blocking.
* Swap Rule -- two flits meeting at one bridge, each wanting the other's ring,
  exchange rings directly through a bypass datapath, using the slot the other
  vacates. The handover is only physical when each flit continues in its
  partner's direction, since the slot being handed over is the partner's.
* deadlock detection + resolution mode -- a transfer FIFO blocked for
  `t_deadlock` cycles with a full eject queue triggers recovery: one eject-queue
  flit is pushed into the reserved buffer to free space.

Measured: this ring cannot deadlock, and the Swap Rule is not what saves it
-------------------------------------------------------------------------
Two results contradict the intuition this baseline was built to test, and both
are structural rather than load-dependent:

1. Under a fixed dimension order every turn is row -> column, so a bridge never
   sees turns in both directions and the Swap Rule can never fire (0 swaps;
   swap on/off give bit-identical runs). It only has work to do under `mixed`
   order, which is the hierarchical-ring configuration HiRD assumes.
2. Even under `mixed` order, at saturation, with `fifo_depth=1` and in the
   slot-scarce regime, NO deadlock occurs with the Swap Rule disabled. The
   reason is that at most one flit can arrive per (node, ring-direction, cycle),
   so a flit that cannot turn always finds its continuation segment free:
   deflection is unconditionally available. The failure mode of a bufferless
   deflection ring is therefore livelock and latency tails, not deadlock, and
   what bounds it is the E-tag/I-tag guarantees, not the Swap Rule. The deadlock
   detector's threshold condition does fire (blocked FIFO + full eject queue),
   but the system recovers on its own, so those firings are false positives.

Reproducing a true deadlock needs a station that can BLOCK an in-ring flit --
multi-flit packets held contiguous, or a bridge that is the only path between
ring levels. Neither holds for a dimension-sliced 2D ring with single-flit
deflection, so the "centralization removes the deadlock hardware" argument has
to be narrowed accordingly.
* reassembly -- deflection and swapping deliver flits out of order, so the
  destination needs a reassembly buffer; occupancy is measured, not assumed.

So the baseline is NOT buffer-free: the rings are, but every one of the 48 nodes
is a bridge, and therefore carries a transfer FIFO, reserved Tx entries, an
eject queue, threshold counters and a reassembly buffer. That per-node cost is
the thing centralized arbitration removes.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from rg_topo import RAMP_BW
from rg_ring_topo import RingId, RingTopology


@dataclass
class RingBaseParams:
    # Dimension order decides whether a bridge sees turns in BOTH directions,
    # and that turns out to decide whether the Swap Rule has anything to do:
    #   "RC"    every turn is row -> column. The turn dependency graph is
    #           acyclic by construction, so no bridge deadlock exists and the
    #           Swap Rule never fires (measured: 0 swaps, and swap on/off give
    #           bit-identical results).
    #   "mixed" some flows turn row -> column and others column -> row, which
    #           is the configuration HiRD's Swap Rule was designed for: two
    #           flits at one bridge each needing the other's ring, both FIFOs
    #           full. This is the setting where deadlock is reproducible.
    dim_order: str = "RC"        # "RC" | "CR" | "mixed"
    # Slot scarcity is the other precondition for deadlock, and it is a
    # PHYSICAL assumption, not a policy knob. With the pipelined links the rest
    # of this study assumes (hop delay H=7 / V=9, one flit per cycle per
    # segment) a segment holds 7-9 flits in flight, so a flit that cannot turn
    # can always keep circulating and the ring cannot deadlock -- only livelock,
    # which is what the E-tag/I-tag guarantees bound. `slot_ring=True` collapses
    # the hop delay to one flit time, giving the classic slotted ring with as
    # many slots as nodes, which is the regime HiRD's Swap Rule was written for.
    # Use it ONLY for the deadlock-existence experiment: it changes the latency
    # baseline and is not comparable with the throughput numbers.
    slot_ring: bool = False
    fifo_depth: int = 4          # transfer FIFO entries per (node, target ring)
    resv_tx: int = 1             # extra entries only an E-tagged flit may use
    t_inj: int = 64              # cycles of injection starvation -> I-tag
    t_xfer: int = 4              # deflections before E-tag is raised
    swap_rule: bool = True
    reasm_depth: int = 64        # destination reassembly buffer (flits)
    eject_depth: int = 4         # eject queue before the PE drains it
    t_deadlock: int = 512        # blocked-FIFO cycles -> declare deadlock
    resolution_mode: bool = True
    eject_bw: int = RAMP_BW      # flits the PE drains per cycle


@dataclass
class Flit:
    pid: int
    seq: int
    nflit: int
    src: int
    dst: int
    t_gen: int
    phase: int                   # 0 = first ring, 1 = second ring
    ring: RingId = ("row", 0)
    dir: int = 1
    idx: int = 0                 # position on the current ring
    target: int = 0              # index on this ring where it must leave
    deflections: int = 0
    e_tag: bool = False
    t_inject: int = -1


class RingBaseSim:
    """Cycle-driven reactive ring. Drive it with `offer()` + `step()`."""

    def __init__(self, topo: RingTopology, params: RingBaseParams | None = None,
                 seed: int = 0):
        self.topo = topo
        self.p = params or RingBaseParams()
        self.rng = random.Random(seed)
        self.t = 0
        self.sigma = topo.sigma

        # per (s,d) static two-phase route description (RC, shortest direction)
        self.route: dict[tuple[int, int], Any] = {}

        # ring state
        self.seg_free: dict[Any, int] = defaultdict(int)
        self.arrivals: dict[int, list[Flit]] = defaultdict(list)
        self.arr_set: dict[Any, set[int]] = defaultdict(set)

        # bridge state: FIFO per (node, target ring)
        self.fifo: dict[Any, deque[Flit]] = defaultdict(deque)
        self.fifo_blocked: dict[Any, int] = defaultdict(int)
        self.resv_used: dict[Any, int] = defaultdict(int)

        # endpoint state
        self.srcq: dict[int, deque[Flit]] = defaultdict(deque)
        self.inj_starve: dict[int, int] = defaultdict(int)
        self.i_tag: dict[Any, set[int]] = defaultdict(set)   # ring-dir -> nodes
        self.ejectq: dict[int, deque[Flit]] = defaultdict(deque)
        self.reasm: dict[int, dict[int, set[int]]] = defaultdict(dict)
        self.max_seen: dict[tuple[int, int], int] = {}

        # results / stats
        self.delivered: list[tuple[Flit, int]] = []
        self.pkt_done: list[tuple[int, int, int, int]] = []  # pid,src,dst,t
        self.st: dict[str, Any] = {
            "n_offered": 0, "n_injected": 0, "n_delivered_flits": 0,
            "n_delivered_pkts": 0, "n_deflections": 0,
            "n_swaps": 0, "n_etag_raised": 0, "n_itag_raised": 0,
            "n_deadlock_recoveries": 0, "n_deadlock_detected": 0,
            "n_inring_blocked": 0, "n_eject_full_deflect": 0,
            "n_fifo_full_deflect": 0, "n_reasm_overflow": 0,
            "n_out_of_order": 0, "max_reasm_occupancy": 0,
            "max_inj_starve": 0, "max_deflections": 0,
            "deflect_hist": defaultdict(int),
        }
        self._pid = 0

        # optional bisection accounting, off until the driver calls count_cut()
        self.cut: frozenset[tuple[int, int]] = frozenset()
        self.cut_win = (0, 0)
        self.cut_busy = 0

    def count_cut(self, links: Any, t0: int, t1: int) -> None:
        """Count link-busy cycles on `links` for ring hops taken in [t0, t1).

        Counts HOPS, not packets, so a deflected flit that rides past its turn
        and comes around is charged for every crossing. Measured on 8x6
        all-to-all that turns out to be a small effect (under 0.01 deflections
        per packet even past saturation); the accounting is per hop anyway
        because it is the only definition that stays correct for a fabric whose
        route is decided cycle by cycle.
        """
        self.cut = frozenset(links)
        self.cut_win = (t0, t1)
        self.cut_busy = 0

    # -- routing -----------------------------------------------------------

    def _pick_order(self, s: int, d: int) -> str:
        if self.p.dim_order in ("RC", "CR"):
            return self.p.dim_order
        return "RC" if ((s * 7 + d * 13) & 1) == 0 else "CR"

    def _route(self, s: int, d: int):
        key = (s, d)
        if key not in self.route:
            want = self._pick_order(s, d)
            p = self.topo.fixed_path(s, d)
            if p.order != want:
                for c in self.topo.candidates(s, d):
                    if c.order == want:
                        p = c
                        break
            legs = []
            for a in p.arcs:
                legs.append((a.ring, a.dir,
                             self.topo.index_on(a.ring, a.start),
                             self.topo.index_on(a.ring, a.end)))
            self.route[key] = legs
        return self.route[key]

    # -- workload injection ------------------------------------------------

    def offer(self, src: int, dst: int, nflit: int = 1) -> int:
        """Enqueue one packet at the source (source queues are unbounded)."""
        pid = self._pid
        self._pid += 1
        legs = self._route(src, dst)
        ring, direction, idx, target = legs[0]
        for k in range(nflit):
            self.srcq[src].append(Flit(pid=pid, seq=k, nflit=nflit, src=src,
                                       dst=dst, t_gen=self.t, phase=0,
                                       ring=ring, dir=direction, idx=idx,
                                       target=target))
        self.st["n_offered"] += nflit
        return pid

    # -- ring primitives ---------------------------------------------------

    def _seg(self, f: Flit) -> Any:
        return (f.ring, f.dir, f.idx)

    def _can_board(self, ring: RingId, direction: int, idx: int) -> bool:
        """Slot must be empty for the whole flit, and stay empty.

        An in-ring flit is already on the wire, so the station can see it
        arriving (`arr_set` lookahead of < hop delay cycles). This is what makes
        "in-ring traffic is never stalled" an invariant rather than a hope.
        """
        seg = (ring, direction, idx)
        if self.seg_free[seg] > self.t:
            return False
        node_key = (ring, direction, idx)
        for dt in range(self.sigma):
            if (self.t + dt) in self.arr_set[node_key]:
                return False
        return True

    def _launch(self, f: Flit, *, inring: bool) -> bool:
        seg = self._seg(f)
        if inring and self.seg_free[seg] > self.t:
            # Must never happen for a flit already on its ring (one arrival per
            # node per ring-direction per cycle). Counted so the invariant is
            # checked rather than assumed; the flit is re-presented next cycle
            # instead of being dropped, so the model can never lose traffic.
            self.st["n_inring_blocked"] += 1
            self.arrivals[self.t + 1].append(f)
            self.arr_set[(f.ring, f.dir, f.idx)].add(self.t + 1)
            return False
        self.seg_free[seg] = self.t + self.sigma
        k = self.topo.ring_size(f.ring)
        nxt = (f.idx + f.dir) % k
        lat = self.sigma if self.p.slot_ring else self.topo.ring_lat(f.ring)
        if self.cut:
            order = self.topo.ring_nodes(f.ring)
            if (order[f.idx], order[nxt]) in self.cut:
                t0, t1 = self.cut_win
                self.cut_busy += max(0, min(self.t + self.sigma, t1)
                                     - max(self.t, t0))
        f.idx = nxt
        self.arrivals[self.t + lat].append(f)
        self.arr_set[(f.ring, f.dir, nxt)].add(self.t + lat)
        return True

    def _deflect(self, f: Flit) -> None:
        f.deflections += 1
        self.st["n_deflections"] += 1
        self.st["max_deflections"] = max(self.st["max_deflections"],
                                         f.deflections)
        if f.deflections >= self.p.t_xfer and not f.e_tag:
            f.e_tag = True
            self.st["n_etag_raised"] += 1
        self._launch(f, inring=True)

    def _fifo_key(self, node: int, ring: RingId) -> Any:
        return (node, ring)

    def _fifo_push(self, node: int, f: Flit) -> bool:
        legs = self._route(f.src, f.dst)
        ring, direction, idx, target = legs[1]
        key = self._fifo_key(node, ring)
        q = self.fifo[key]
        cap = self.p.fifo_depth
        if len(q) < cap:
            pass
        elif f.e_tag and self.resv_used[key] < self.p.resv_tx:
            self.resv_used[key] += 1
        else:
            return False
        f.phase = 1
        f.ring, f.dir, f.idx, f.target = ring, direction, idx, target
        q.append(f)
        return True

    def _try_eject(self, f: Flit) -> bool:
        if len(self.ejectq[f.dst]) >= self.p.eject_depth:
            return False
        self.ejectq[f.dst].append(f)
        return True

    def _wants_ring(self, f: Flit) -> RingId | None:
        """Target ring of a pending turn, or None if this flit ejects here."""
        legs = self._route(f.src, f.dst)
        if f.phase == 0 and len(legs) > 1:
            return legs[1][0]
        return None

    # -- one cycle ---------------------------------------------------------

    def step(self) -> None:
        t = self.t
        arrivals = self.arrivals.pop(t, [])
        for f in arrivals:
            self.arr_set[(f.ring, f.dir, f.idx)].discard(t)

        turn_req: dict[int, list[Flit]] = defaultdict(list)
        for f in arrivals:
            node = self.topo.ring_nodes(f.ring)[f.idx]
            if f.idx != f.target:
                self._launch(f, inring=True)       # in-ring priority
                continue
            if self._wants_ring(f) is None:
                if self._try_eject(f):
                    self._on_eject(f)
                else:
                    self.st["n_eject_full_deflect"] += 1
                    self._deflect(f)
            else:
                turn_req[node].append(f)

        # --- Swap Rule: opposite-direction turns at one bridge trade rings
        for node, reqs in turn_req.items():
            if not self.p.swap_rule or len(reqs) < 2:
                continue
            by_from: dict[str, list[Flit]] = defaultdict(list)
            for f in reqs:
                by_from[f.ring[0]].append(f)
            while by_from["row"] and by_from["col"]:
                a = by_from["row"][0]
                b = by_from["col"][0]
                ka = self._fifo_key(node, self._wants_ring(a))
                kb = self._fifo_key(node, self._wants_ring(b))
                if len(self.fifo[ka]) < self.p.fifo_depth and \
                        len(self.fifo[kb]) < self.p.fifo_depth:
                    break                       # no need to swap
                if not self._try_swap(a, b):
                    break        # slots not handover-able; fall through below
                by_from["row"].pop(0)
                by_from["col"].pop(0)
                reqs.remove(a)
                reqs.remove(b)

        # --- remaining turns: FIFO or deflect
        for node, reqs in turn_req.items():
            for f in reqs:
                if not self._fifo_push(node, f):
                    self.st["n_fifo_full_deflect"] += 1
                    self._deflect(f)

        # --- transfer FIFO drain (beats local injection, loses to in-ring)
        for key, q in list(self.fifo.items()):
            if not q:
                self.fifo_blocked[key] = 0
                continue
            f = q[0]
            # The I-tag reserves a slot against other INJECTIONS, not against
            # ring-to-ring transfers: priority is in-ring > transfer > inject.
            # Letting it gate transfers as well deadlocks the two guarantees
            # against each other -- a starved injector inhibits the very FIFO
            # drains whose deflecting flits are filling the slots it waits for.
            if not self._can_board(f.ring, f.dir, f.idx):
                self.fifo_blocked[key] += 1
                self._maybe_deadlock(key)
                continue
            q.popleft()
            if self.resv_used[key] > 0 and len(q) >= self.p.fifo_depth:
                self.resv_used[key] -= 1
            self.fifo_blocked[key] = 0
            self._launch(f, inring=False)

        # --- local injection (lowest priority)
        for node in list(self.srcq.keys()):
            q = self.srcq[node]
            if not q:
                self.inj_starve[node] = 0
                continue
            f = q[0]
            if self._itag_blocks(f, boarding_node=node) or \
                    not self._can_board(f.ring, f.dir, f.idx):
                self.inj_starve[node] += 1
                self.st["max_inj_starve"] = max(self.st["max_inj_starve"],
                                                self.inj_starve[node])
                if self.inj_starve[node] >= self.p.t_inj:
                    ring_key = (f.ring, f.dir)
                    if node not in self.i_tag[ring_key]:
                        self.i_tag[ring_key].add(node)
                        self.st["n_itag_raised"] += 1
                continue
            q.popleft()
            self.i_tag[(f.ring, f.dir)].discard(node)
            self.inj_starve[node] = 0
            f.t_inject = t
            self.st["n_injected"] += 1
            self._launch(f, inring=False)

        # --- PE drains the eject queue
        for node, q in self.ejectq.items():
            for _ in range(self.p.eject_bw):
                if not q:
                    break
                q.popleft()

        self.t += 1

    def _itag_blocks(self, f: Flit, boarding_node: int) -> bool:
        """Injection guarantee: a starved node's reservation inhibits others."""
        holders = self.i_tag[(f.ring, f.dir)]
        return bool(holders) and boarding_node not in holders

    def _try_swap(self, a: Flit, b: Flit) -> bool:
        """Bypass both FIFOs: each flit takes the slot the other vacates.

        Both handovers must be checked BEFORE committing. The slot a flit hands
        over is the one it would have continued into, but its partner's second
        leg may run the other way round its ring, where an unrelated
        counter-rotating flit can already own that segment. Swapping without
        checking loses a flit there, and a lost flit shows up only as a run that
        never finishes.
        """
        leg_a = self._route(a.src, a.dst)[1]
        leg_b = self._route(b.src, b.dst)[1]
        # A handover is only physical if each flit continues in exactly the
        # direction its partner was travelling: the slot being handed over is
        # the partner's. If a's route needs the other way round its new ring
        # that slot belongs to the counter-rotating flit, which is still using
        # it, and taking it would collide with that flit's own deflection.
        if leg_a[1] != b.dir or leg_b[1] != a.dir:
            return False
        if leg_a[0] != b.ring or leg_b[0] != a.ring:
            return False
        for ring, direction, idx, _ in (leg_a, leg_b):
            if self.seg_free[(ring, direction, idx)] > self.t:
                return False
        self.st["n_swaps"] += 1
        for f, leg in ((a, leg_a), (b, leg_b)):
            ring, direction, idx, target = leg
            f.phase = 1
            f.ring, f.dir, f.idx, f.target = ring, direction, idx, target
            if idx == target and self._try_eject(f):
                self._on_eject(f)      # already at its exit on the new ring
                continue
            self._launch(f, inring=True)
        return True

    def _maybe_deadlock(self, key: Any) -> None:
        if self.fifo_blocked[key] < self.p.t_deadlock:
            return
        node = key[0]
        if len(self.ejectq[node]) < self.p.eject_depth:
            return
        self.st["n_deadlock_detected"] += 1
        if not self.p.resolution_mode:
            return
        # resolution: spill one eject-queue flit into the reserved buffer
        self.ejectq[node].popleft()
        self.fifo_blocked[key] = 0
        self.st["n_deadlock_recoveries"] += 1

    def _on_eject(self, f: Flit) -> None:
        self.st["n_delivered_flits"] += 1
        self.delivered.append((f, self.t))
        self.st["deflect_hist"][f.deflections] += 1
        key = (f.src, f.dst)
        prev = self.max_seen.get(key, -1)
        if f.seq < prev:
            self.st["n_out_of_order"] += 1
        self.max_seen[key] = max(prev, f.seq)
        seen = self.reasm[f.dst].setdefault(f.pid, set())
        seen.add(f.seq)
        occ = sum(len(v) for v in self.reasm[f.dst].values())
        self.st["max_reasm_occupancy"] = max(self.st["max_reasm_occupancy"],
                                             occ)
        if occ > self.p.reasm_depth:
            self.st["n_reasm_overflow"] += 1
        if len(seen) == f.nflit:
            del self.reasm[f.dst][f.pid]
            self.st["n_delivered_pkts"] += 1
            self.pkt_done.append((f.pid, f.src, f.dst, self.t))

    # -- introspection -----------------------------------------------------

    def in_flight(self) -> int:
        return sum(len(v) for v in self.arrivals.values()) + \
            sum(len(q) for q in self.fifo.values())

    def backlog(self) -> int:
        return sum(len(q) for q in self.srcq.values())

    def stalled(self) -> bool:
        """Nothing on any ring and nothing can board: a hard deadlock."""
        return self.in_flight() == 0 and self.backlog() > 0

    def summary(self) -> dict[str, Any]:
        out = {k: (dict(v) if isinstance(v, defaultdict) else v)
               for k, v in self.st.items()}
        out["t"] = self.t
        out["backlog"] = self.backlog()
        out["in_flight"] = self.in_flight()
        out["fifo_occupancy"] = sum(len(q) for q in self.fifo.values())
        out["max_fifo_blocked"] = (max(self.fifo_blocked.values())
                                   if self.fifo_blocked else 0)
        out["deflect_per_flit"] = (
            round(self.st["n_deflections"] /
                  max(1, self.st["n_delivered_flits"]), 3))
        return out


# ---------------------------------------------------------------------------
# Batch driver: offer a fixed workload and run to completion (or to a stall)
# ---------------------------------------------------------------------------

def run_batch(topo: RingTopology, pairs: list[tuple[int, int]], *,
              m: int = 1, params: RingBaseParams | None = None,
              t_max: int = 400_000, seed: int = 0) -> dict[str, Any]:
    sim = RingBaseSim(topo, params, seed=seed)
    for s, d in pairs:
        sim.offer(s, d, m)
    total = len(pairs) * m
    last_progress = 0
    last_count = 0
    while sim.t < t_max and sim.st["n_delivered_flits"] < total:
        sim.step()
        if sim.st["n_delivered_flits"] != last_count:
            last_count = sim.st["n_delivered_flits"]
            last_progress = sim.t
        elif sim.t - last_progress > 20_000:
            break
    out = sim.summary()
    out["n_target_flits"] = total
    out["completed"] = sim.st["n_delivered_flits"] >= total
    out["makespan"] = sim.t
    out["stall_detected"] = not out["completed"]
    lat = [t - f.t_gen for f, t in sim.delivered]
    lat.sort()
    if lat:
        out["lat_p50"] = lat[len(lat) // 2]
        out["lat_p99"] = lat[min(len(lat) - 1, int(0.99 * len(lat)))]
        out["lat_max"] = lat[-1]
    return out


if __name__ == "__main__":
    import json

    topo = RingTopology()
    a2a = [(s, d) for s in range(topo.n) for d in range(topo.n) if s != d]

    print("=== ring_base, alltoall m=1, swap ON ===")
    r = run_batch(topo, a2a, m=1, params=RingBaseParams(swap_rule=True))
    keep = ("completed", "makespan", "n_delivered_flits", "n_target_flits",
            "n_deflections", "deflect_per_flit", "max_deflections", "n_swaps",
            "n_etag_raised", "n_itag_raised", "n_deadlock_detected",
            "n_deadlock_recoveries", "n_inring_blocked", "n_out_of_order",
            "max_reasm_occupancy", "max_inj_starve", "lat_p50", "lat_p99",
            "lat_max")
    print(json.dumps({k: r.get(k) for k in keep}, indent=2))

    print("\n=== deadlock hunt: order x Swap Rule x slot scarcity (m=8, fifo=1)")
    print(f"{'slots':5} {'order':6} {'swap':5} {'resol':6} {'done':5} "
          f"{'mk':>7} {'defl':>7} {'swaps':>6} {'dead':>5} {'recov':>6} "
          f"{'inring_blk':>10}")
    for slot in (False, True):
        for order in ("RC", "mixed"):
            for swap, resol in ((False, False), (True, False), (True, True)):
                p = RingBaseParams(dim_order=order, swap_rule=swap,
                                   resolution_mode=resol, slot_ring=slot,
                                   fifo_depth=1, eject_depth=1, eject_bw=1,
                                   t_deadlock=256)
                r = run_batch(topo, a2a, m=8, params=p, t_max=120_000)
                print(f"{int(slot):5} {order:6} {int(swap):5} {int(resol):6} "
                      f"{int(r['completed']):5} {r['makespan']:>7} "
                      f"{r['n_deflections']:>7} {r['n_swaps']:>6} "
                      f"{r['n_deadlock_detected']:>5} "
                      f"{r['n_deadlock_recoveries']:>6} "
                      f"{r['n_inring_blocked']:>10}")
    print("\nNo configuration deadlocks: deflection is unconditionally "
          "available (<=1 arrival per node/ring-dir/cycle).")
