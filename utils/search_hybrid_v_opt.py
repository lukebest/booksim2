#!/usr/bin/env python3
"""Search for hybrid_v B=2 bi 0-buffer packings below a target makespan.

16×16 default; rigid footprint from sched_zerobuf_compare.fp_hybrid_v.
Bottleneck is link cap=1 (not down-ramp bw); solo inject@0 lower bound = 332 cy.
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict

import sched_zerobuf_compare as S


def _max_down_rel(footprints):
    out = {}
    for s, slots in footprints.items():
        out[s] = max(rel for kind, _key, rel in slots if kind == "D")
    return out


class OccTracker:
    """Incremental link/up/down occupancy for one packing."""

    def __init__(self, footprints, ramp_bw):
        self.footprints = footprints
        self.ramp_bw = ramp_bw
        self.link = defaultdict(int)
        self.up = defaultdict(int)
        self.down = defaultdict(int)
        self.mk = 0

    def _touch(self, kind, key, cyc, delta):
        k = (kind, key, cyc)
        if kind == "L":
            self.link[k] += delta
            return self.link[k] <= 1
        if kind == "U":
            self.up[k] += delta
            return self.up[k] <= self.ramp_bw
        self.down[k] += delta
        if self.down[k] > self.ramp_bw:
            return False
        if delta > 0:
            self.mk = max(self.mk, cyc + S.RAMP - 1 + 1)  # flits=1
        return True

    def add(self, s, off):
        for kind, key, rel in self.footprints[s]:
            if not self._touch(kind, key, off + rel, 1):
                self._rollback(s, off)
                return False
        return True

    def _rollback(self, s, off):
        for kind, key, rel in self.footprints[s]:
            self._touch(kind, key, off + rel, -1)
        self._recompute_mk()

    def remove(self, s, off):
        for kind, key, rel in self.footprints[s]:
            self._touch(kind, key, off + rel, -1)
        self._recompute_mk()

    def _recompute_mk(self):
        self.mk = max(
            (k[2] + S.RAMP - 1 + 1 for k, v in self.down.items() for _ in range(v)),
            default=0,
        )


def greedy_pack(footprints, ramp_bw, src_order, target_mk):
    max_rel = _max_down_rel(footprints)
    occ = OccTracker(footprints, ramp_bw)
    assign = {}
    for s in src_order:
        ub = max(0, target_mk - max_rel[s] - S.RAMP + 1)
        placed = False
        for off in range(ub + 1):
            if occ.add(s, off):
                assign[s] = off
                placed = True
                break
            occ.remove(s, off)
        if not placed:
            return None, None
    return occ.mk, assign


def baseline_bw1_reuse(B=2, bidir=True, ramp_bw=2):
    S.cfg(16, 16, 4, 6)
    S.init_ring()
    S.init_quadrants()
    foot = {s: S.fp_hybrid_v(s, B, bidir, 1) for s in range(S.N)}
    best = None
    for name, gen in S.SRC_ORDERS.items():
        _, _, _, inj, _ = S.export_events(foot, 1, gen())
        mk, _, busy = S.apply_offsets(foot, inj, gen(), ramp_bw)
        if S.verify(busy, ramp_bw) and (best is None or mk < best[0]):
            best = (mk, name, dict(inj))
    return best


def solo_lower_bound(B=2, bidir=True, ramp_bw=1):
    S.cfg(16, 16, 4, 6)
    S.init_ring()
    S.init_quadrants()
    foot = {s: S.fp_hybrid_v(s, B, bidir, ramp_bw) for s in range(S.N)}
    mx = max(
        rel for s in range(S.N) for kind, _key, rel in foot[s] if kind == "D"
    )
    return mx + S.RAMP


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=int, default=333, help="makespan target to test")
    ap.add_argument("--random-trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    S.cfg(16, 16, 4, 6)
    S.init_ring()
    S.init_quadrants()
    B, bidir, rb = 2, True, 2
    foot = {s: S.fp_hybrid_v(s, B, bidir, 1) for s in range(S.N)}
    solo = solo_lower_bound(B, bidir, 1)
    best_mk, best_name, _ = baseline_bw1_reuse(B, bidir, rb)

    print(f"hybrid_v B={B} bi @ ramp={rb}")
    print(f"  solo inject@0 lower bound     : {solo} cy")
    print(f"  eject bandwidth lower bound     : {(S.N - 1 + rb - 1) // rb} cy")
    print(f"  current best (BW1 off reuse)    : {best_mk} cy ({best_name})")

    order = sorted(range(S.N), key=lambda s: -_max_down_rel(foot)[s])
    mk, _ = greedy_pack(foot, rb, order, args.target)
    print(f"  greedy max-rel @ target<={args.target}: "
          f"{'OK mk=' + str(mk) if mk is not None else 'FAILED'}")

    random.seed(args.seed)
    hit = None
    for t in range(args.random_trials):
        ord2 = list(range(S.N))
        random.shuffle(ord2)
        mk, assign = greedy_pack(foot, rb, ord2, args.target)
        if mk is not None and (hit is None or mk < hit[0]):
            hit = (mk, t)
    if hit:
        print(f"  random greedy best @ target<={args.target}: mk={hit[0]} (trial {hit[1]})")
    else:
        print(f"  random greedy: no packing found with mk<={args.target}")

    if best_mk <= args.target:
        print(f"\n=> target {args.target} already met by baseline.")
    elif solo >= args.target:
        print(f"\n=> target {args.target} below solo bound {solo}: impossible for this footprint.")
    else:
        print(f"\n=> target {args.target}: not found this round; need stronger search (CP-SAT).")


if __name__ == "__main__":
    main()
