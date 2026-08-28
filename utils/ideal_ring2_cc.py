#!/usr/bin/env python3
"""The ideal congestion controller: a formal ceiling for this exact fabric.

Why this replaces the null model
--------------------------------
Until now the per-bin Jain acceptance line was judged against `jain_bin_null`,
the score a **fair but memoryless** arbiter earns through a 50-cycle window:
multinomial counting noise alone caps it at N/(N+n-1), about 0.970 here. That is
a floor for *randomised* fairness, and it answers the wrong question. A real
controller is allowed to be regular, not just unbiased, and a deterministic
schedule beats the multinomial null trivially. So the null both understates what
is achievable and gives no bandwidth reference at all.

The right reference is the best *any* congestion controller could do on this
fabric. That is well defined, because a congestion controller has exactly one
lever and several things it is not allowed to touch.

What the ideal controller may and may not do
--------------------------------------------
It may choose, for every cycle and every core, whether that core injects -- with
perfect global knowledge and zero feedback delay. It may not:

  * change routing. Direction is a function of (src, dst) fixed by the
    latency-shortest rule, and the link delays are asymmetric, so the per-core
    hop footprint is a given.
  * change the destination mix. The tiled-write hash decides which HA each
    transaction goes to.
  * change link, port or buffer capacity, or the 1 flit/cycle/VC/direction rule.
  * escape the CHI handshake: every write costs 1 REQ flit and W DAT flits
    core -> HA, plus 2 RSP flits (DBIDResp, Comp) HA -> core.

Since routing and the destination mix are fixed, the load a core places on every
resource is *linear* in its transaction rate. So the ideal controller's reachable
set is a polytope and its ceiling is a linear program:

    variables   lambda_c >= 0                 transaction rate of core c
    load        L_r = sum_c lambda_c a_{c,r}  for every resource r
    capacity    L_r <= 1                      every (hop, VC, dir), every
                                              up-ring and down-ring port
    a_{c,r}     = sum_h f_{c,h} * m_v * [r on the path of that (c,h,v) flow]

with m_req = 1, m_dat = W, m_rsp = 2, and f_{c,h} the measured destination mix.
Write bandwidth is the DAT flit rate, W * sum_c lambda_c.

Three solves give the whole story:

  (a) `max_total`  -- maximise sum lambda_c. No fairness constraint. This is the
      absolute bandwidth ceiling of the fabric, fair or not.
  (b) `equal_rate` -- force lambda_c = lambda for all c. This is the ceiling of a
      *perfectly fair* controller. Reduces to lambda* = 1 / max_r sum_c a_{c,r}.
  (c) the frontier -- maximise sum lambda_c subject to
      min_c lambda_c >= theta * mean_c lambda_c, for theta in [0, 1]. At
      theta = 1 this is (b), at theta = 0 it is (a), and in between it traces
      **the exact price of fairness on this fabric**, with no mechanism, no
      feedback delay and no arbitration loss in the way.

If (b) < (a) then a fairness/bandwidth tradeoff is a property of the fabric and
every controller must pay it. If (b) = (a) then any observed tradeoff is a
mechanism artifact and is in principle removable.

The per-bin Jain ceiling
------------------------
The ideal controller is deterministic, so within a bin it splits the bin's total
as evenly as *integers* allow -- not as evenly as a random draw would. With N
flits in a bin over n cores, r = N mod n cores get ceil(N/n) and the rest
floor(N/n), giving

    J_ideal(N) = N^2 / ( n * [ r*ceil(N/n)^2 + (n-r)*floor(N/n)^2 ] )

which is 1 - O(1/N^2) rather than the null's 1 - (n-1)/N. At N ~ 286 this is
0.9999, so **the ideal's per-bin Jain is essentially 1.0** and the 0.99
acceptance line sits just below it rather than far above it, as it did against
the null. One more consequence worth stating: under the ideal, every core runs at
the same rate, so in this closed batch they all finish together -- there is no
drain tail and no late-phase uptick.

Forecast, written before running:
  * `equal_rate` reproduces the report's R* = 40/7 = 5.7143 flit/cycle
    (lambda* = 2/7 per core). This is the check that the LP is modelling the same
    fabric the simulator does.
  * `max_total` comes out **above** R*, by roughly 1-3%, and the extra goes to
    the six cores that are not adjacent to the HA-less nodes 9 and 19. Reason:
    the binding hop is loaded unevenly across cores, so relaxing equality lets
    the LP push traffic onto slack hops.
  * Therefore the fairness price is real but small, and it should land near the
    -1.45% that S22's measured frontier crosses Jain = 0.99 at. If those two
    numbers agree, the -1.45% is a fabric property rather than a tuning failure,
    which would settle phase 3's central question.
  * The binding resource is a ring hop, not an up-ring or down-ring port.
  * Falsifier: `equal_rate` disagreeing with 40/7 by more than rounding means the
    LP's flow model (multiplicities, direction choice, or destination mix) does
    not match the simulator, and every number here is void.

Usage:
    python3 ideal_ring2_cc.py            # solve and write results JSON
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import BIN_W, CORE_NODES, MEM_NODES, W_FLITS
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology, build_tiled_write

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "ideal_ring2_cc.json")

# Flits per write transaction on each VC. REQ and DAT run core -> HA, RSP
# (DBIDResp then Comp) runs HA -> core.
MULT = {"req": 1, "dat": W_FLITS, "rsp": 2}
REVERSE = {"rsp"}          # VCs that travel HA -> core


def dest_mix(k: int) -> dict[int, dict[int, float]]:
    """Measured per-core destination distribution, from the real txn list."""
    txns = build_tiled_write(k=k, m_wdata=W_FLITS, mem=list(MEM_NODES),
                             core_set=list(CORE_NODES))
    cnt: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        cnt[t.core][t.ha] += 1
    out = {}
    for c, row in cnt.items():
        tot = sum(row.values())
        out[c] = {h: v / tot for h, v in sorted(row.items())}
    return dict(sorted(out.items()))


def coefficients(topo: Ring2Topology, mix: dict[int, dict[int, float]]
                 ) -> tuple[list[int], list[str], np.ndarray]:
    """a[c][r]: load on resource r per unit transaction rate of core c.

    Resources, all capacity 1 flit/cycle:
      ("hop", u, v, vc)    transit over the directed ring hop u -> v
      ("up",  node, vc, d) up-ring (injection) port, per direction
      ("down", node, vc)   down-ring (ejection) port, the read side of the
                           two-write-one-read buffer
    """
    cores = sorted(mix)
    res: dict[tuple, int] = {}

    def idx(key: tuple) -> int:
        if key not in res:
            res[key] = len(res)
        return res[key]

    rows: list[dict[int, float]] = [defaultdict(float) for _ in cores]
    for ci, c in enumerate(cores):
        for h, f in mix[c].items():
            for vc, m in MULT.items():
                src, dst = (h, c) if vc in REVERSE else (c, h)
                # The simulator's own direction choice, so the model cannot
                # drift from the fabric it is meant to bound.
                path = topo.make_path(src, dst, 0)
                w = f * m
                rows[ci][idx(("up", src, vc, path.dir))] += w
                rows[ci][idx(("down", dst, vc))] += w
                for u, v in zip(path.nodes, path.nodes[1:]):
                    rows[ci][idx(("hop", u, v, vc))] += w

    names = [None] * len(res)
    for key, i in res.items():
        names[i] = ":".join(str(x) for x in key)
    a = np.zeros((len(cores), len(res)))
    for ci, row in enumerate(rows):
        for j, v in row.items():
            a[ci, j] = v
    return cores, names, a


def solve_max_total(a: np.ndarray) -> np.ndarray:
    n = a.shape[0]
    r = linprog(c=-np.ones(n), A_ub=a.T, b_ub=np.ones(a.shape[1]),
                bounds=[(0, None)] * n, method="highs")
    assert r.success, r.message
    return r.x


def solve_theta(a: np.ndarray, theta: float) -> np.ndarray:
    """max sum lambda s.t. min_c lambda_c >= theta * mean_c lambda_c."""
    n = a.shape[0]
    # -lambda_c + (theta/n) * sum lambda <= 0
    extra = (theta / n) * np.ones((n, n)) - np.eye(n)
    A = np.vstack([a.T, extra])
    b = np.concatenate([np.ones(a.shape[1]), np.zeros(n)])
    r = linprog(c=-np.ones(n), A_ub=A, b_ub=b, bounds=[(0, None)] * n,
                method="highs")
    assert r.success, f"theta={theta}: {r.message}"
    return r.x


def jain(xs) -> float:
    s = float(sum(xs))
    sq = float(sum(x * x for x in xs))
    return (s * s) / (len(xs) * sq) if sq > 0 else 1.0


def jain_ideal_bin(total_rate: float, n: int, bin_w: int,
                   gran: int = 1) -> float:
    """Per-bin Jain of a deterministic, perfectly even integer schedule.

    `gran` is the scheduling quantum in flits. `gran=1` lets the ideal place
    single flits; `gran=W` is the conservative variant in which a WriteData
    burst must stay contiguous, so a core's per-bin count can only move in
    steps of W and exact equality is coarser.
    """
    N = total_rate * bin_w
    lo = gran * int((N / n) // gran)
    hi = lo + gran
    # Fraction of cores that must take the high value for the mean to come out
    # right; a deterministic schedule rotates which cores those are.
    r = 0.0 if hi == lo else n * (N / n - lo) / gran
    sq = r * hi * hi + (n - r) * lo * lo
    return (N * N) / (n * sq) if sq > 0 else 1.0


def max_bw_at_jain(a: np.ndarray, target: float) -> tuple[float, float, float]:
    """Most bandwidth the ideal can carry while keeping rate-Jain >= target.

    Bisects on theta, which is monotone in both bandwidth (down) and Jain (up).
    """
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if jain(solve_theta(a, mid)) < target:
            lo = mid
        else:
            hi = mid
    lam = solve_theta(a, hi)
    return W_FLITS * float(lam.sum()), jain(lam), hi


def binding(a: np.ndarray, lam: np.ndarray, names: list[str],
            top: int = 6) -> list[tuple[str, float]]:
    load = a.T @ lam
    order = np.argsort(-load)
    return [(names[i], round(float(load[i]), 5)) for i in order[:top]]


def main() -> None:
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    mix = dest_mix(k=1600)              # 1600 = 100 tiles, exact hash coverage
    cores, names, a = coefficients(topo, mix)
    n = len(cores)

    lam_max = solve_max_total(a)
    lam_fair = solve_theta(a, 1.0)
    bw = lambda lam: W_FLITS * float(lam.sum())

    r_max, r_fair = bw(lam_max), bw(lam_fair)
    print(f"cores={n}  resources={len(names)}  W={W_FLITS}\n")
    print(f"(a) max_total   : {r_max:.6f} flit/cycle   "
          f"per-core lambda {np.round(lam_max, 5).tolist()}")
    print(f"    Jain of rates = {jain(lam_max):.5f}")
    print(f"    binding: {binding(a, lam_max, names, 4)}\n")
    print(f"(b) equal_rate  : {r_fair:.6f} flit/cycle   "
          f"lambda* = {lam_fair[0]:.6f} = {Fraction(lam_fair[0]).limit_denominator(64)}")
    print(f"    binding: {binding(a, lam_fair, names, 4)}\n")
    price = 100 * (r_max - r_fair) / r_max
    starved = [c for c, x in zip(cores, lam_max) if x < 1e-9]
    print(f"fairness price at the LP level: {price:.3f}%  "
          f"({r_max:.4f} -> {r_fair:.4f})")
    print(f"  but (a) starves cores {starved} to exactly zero, so it never "
          f"completes a closed batch -> NOT a feasible operating point here\n")

    jb = jain_ideal_bin(r_fair, n, BIN_W)
    jbw = jain_ideal_bin(r_fair, n, BIN_W, gran=W_FLITS)
    print(f"ideal per-bin Jain (deterministic, integer-limited) = {jb:.6f}")
    print(f"  with atomic {W_FLITS}-flit WriteData bursts   = {jbw:.6f}")
    print(f"  N per {BIN_W}-cycle bin = {r_fair * BIN_W:.1f} flits\n")

    # The comparison that settles whether the two acceptance lines conflict.
    # At the LP optimum every binding hop sits at exactly 1.0, so realising an
    # equal-rate flow there would need a zero-slack schedule -- the one place a
    # genuine integrality gap could hide. Scale the whole equal-rate solution
    # down to the bandwidth S0 actually achieves: by linearity it stays
    # feasible, now with real slack on every hop, and it is still perfectly
    # equal-rate. So this point needs no scheduling miracle.
    s0_thr = 5.4681
    util = s0_thr / r_fair
    jb_s0 = jain_ideal_bin(s0_thr, n, BIN_W)
    jb_s0_burst = jain_ideal_bin(s0_thr, n, BIN_W, gran=W_FLITS)
    print(f"ideal at S0's own bandwidth ({s0_thr} flit/cycle = "
          f"{100 * util:.2f}% of R*, so {100 * (1 - util):.2f}% slack "
          f"on every hop):")
    print(f"  equal-rate is feasible => rate-Jain = 1.0, "
          f"per-bin Jain = {jb_s0:.6f} ({jb_s0_burst:.6f} with atomic bursts)")
    print(f"  => Jain > 0.99 at ZERO bandwidth cost against S0 is not "
          f"forbidden by any capacity argument\n")

    bw99, j99, th99 = max_bw_at_jain(a, 0.99)
    print(f"ideal frontier at rate-Jain = 0.99: bw={bw99:.4f} flit/cycle "
          f"(theta={th99:.4f}, Jain={j99:.5f})")
    print(f"  = {100 * bw99 / r_fair:.2f}% of the equal-rate optimum, so the "
          f"ideal buys Jain 0.99 at *negative* bandwidth cost\n")

    front = []
    for th in [0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.98, 0.99, 1.0]:
        lam = solve_theta(a, th)
        front.append({"theta": th, "bw": round(bw(lam), 6),
                      "jain_rate": round(jain(lam), 6),
                      "pct_of_max": round(100 * bw(lam) / r_max, 4),
                      "min_over_mean": round(float(lam.min() / lam.mean()), 5)})
        print(f"  theta={th:<5} bw={front[-1]['bw']:.4f} "
              f"({front[-1]['pct_of_max']:.2f}% of max)  "
              f"Jain(rates)={front[-1]['jain_rate']:.5f}")

    OUT.write_text(json.dumps({
        "w_flits": W_FLITS, "bin_w": BIN_W, "n_cores": n,
        "cores": cores,
        "r_max": r_max, "r_fair": r_fair,
        "lambda_max": [round(float(x), 6) for x in lam_max],
        "lambda_fair": round(float(lam_fair[0]), 6),
        "jain_rates_at_max": round(jain(lam_max), 6),
        "fairness_price_pct": round(price, 4),
        "starved_at_max": starved,
        "jain_bin_ideal": round(jb, 6),
        "jain_bin_ideal_burst": round(jbw, 6),
        "bw_at_jain99": round(bw99, 6),
        "theta_at_jain99": round(th99, 5),
        "s0_thr_ref": s0_thr,
        "s0_util_of_rfair": round(util, 5),
        "jain_bin_ideal_at_s0_bw": round(jb_s0, 6),
        "jain_bin_ideal_at_s0_bw_burst": round(jb_s0_burst, 6),
        "flits_per_bin": round(r_fair * BIN_W, 2),
        "binding_max": binding(a, lam_max, names, 8),
        "binding_fair": binding(a, lam_fair, names, 8),
        "frontier": front,
    }, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
