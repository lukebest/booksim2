#!/usr/bin/env python3
"""The fairness / total-bandwidth trade-off, derived rather than measured.

So far the two acceptance lines have been treated as two separate numbers. They
are not independent: on this fabric the cores are *not* interchangeable (HAs sit
at odd nodes except the non-terminal 9/19, so cores 0/8/10/18 sit next to a gap),
and any demand that all cores run at the same rate has to be paid for in total
throughput. This script derives that exchange rate exactly, as a curve.

FORMALISATION
=============
Let lambda_c >= 0 be core c's transaction rate and A the (resource x core) load
matrix from `ideal_ring2_cc.coefficients` -- one row per (hop, direction, VC) and
per up/down-ring port, every capacity 1 flit/cycle. Parametrise fairness by

    theta = min_c lambda_c / mean_c lambda_c   in [0, 1],

so theta = 1 is exactly equal rates and theta = 0 is unconstrained. The bandwidth
frontier is the parametric LP

    R(theta) = W * max { 1'lambda : A' lambda <= 1, lambda >= (theta/n) 1'lambda,
                         lambda >= 0 }.                                      (P)

**Normalisation.** (P) is homogeneous in lambda apart from the capacity row, so
write lambda = s * mu with 1'mu = 1 and s = 1'lambda the total transaction rate.
The fairness constraint becomes mu_c >= theta/n, which no longer involves s, and
capacity becomes s <= 1 / max_r a_r'mu. Hence

    R(theta) / W = max { s } = 1 / g(theta),
    g(theta) = min { max_r a_r'mu : mu in Simplex, mu >= (theta/n) 1 }.       (N)

**Structure of the frontier -- three provable facts.**

1. *g is convex and non-decreasing in theta, and piecewise linear.* Let mu_1,
   mu_2 be minimisers at theta_1, theta_2. For t in [0,1] the blend
   mu_t = t mu_1 + (1-t) mu_2 sums to 1 and satisfies
   mu_t >= (t theta_1 + (1-t) theta_2)/n, so it is feasible at the blended theta.
   Since max_r a_r'mu is convex in mu,

       g(t theta_1 + (1-t) theta_2) <= max_r a_r'mu_t
                                    <= t g(theta_1) + (1-t) g(theta_2),

   which is convexity. Monotonicity is immediate because the feasible set shrinks
   as theta grows, and piecewise linearity because g is the value of an LP whose
   right-hand side moves affinely in theta.

2. *Therefore 1/R is convex in theta, and R is convex on each linear piece of g
   with **concave kinks** where the binding resource changes.* Because
   1/R = g/W exactly, the clean global statement is about the reciprocal of
   bandwidth, not bandwidth itself: on a linear piece g'' = 0 so
   (1/g)'' = 2g'^2/g^3 > 0, but at a breakpoint g' jumps up so (1/g)' jumps down.
   In particular there is a threshold theta_0 below which the fairness constraint
   is slack and R = R_max exactly, and R(theta) has a concave kink there
   (slope 0 -> negative). An earlier draft of this file claimed R itself was
   globally convex; the numerical check below disproved it, and the kink at
   theta_0 is precisely the counterexample.

3. *Closed form at the fair end.* At theta = 1 the constraint mu >= 1/n together
   with 1'mu = 1 forces mu = u = 1/n, so g(1) = max_r a_r'u and

       lambda* = 1 / (n * max_r abar_r),   R* = W / max_r abar_r,
       abar_r = mean_c a_{r,c}.                                               (C)

   The equal-rate ceiling is set by whichever resource carries the largest
   *average* per-core load -- no optimisation is involved, just one max over
   resources, which is why R* comes out as the exact rational 40/7.

**The fairness axis that is actually being graded** is not theta but the 50-cycle
per-bin Jain. For an ideal (deterministic, globally scheduling) controller running
at rate vector lambda, a bin of width B holds W*lambda_c*B flits from core c, so
the achievable per-bin Jain is the Jain of those counts rounded to integers ->
J_rate as B grows, minus an O(1/N^2) integrality term. Both are reported, and the
curve is plotted against the per-bin value so measured schemes can be overlaid on
the same axes.

**Reference fairness operating points.** Beyond the theta family the standard
alpha-fair objective max sum lambda_c^(1-alpha)/(1-alpha) is solved for several
alpha (alpha = 0 -> max throughput, 1 -> proportional fair, large -> max-min
fair), plus max-min fairness exactly by progressive filling. These land *on* the
frontier and say which point a textbook fairness criterion would pick.

Forecast, written before running (and how it fared):
  * R(theta) flat at R_max = 6.400 up to theta_0 in 0.6-0.7, then decreasing to
    R* = 40/7 = 5.7143. **Held**: theta_0 = 0.625.
  * R(theta) globally convex. **Wrong** -- see fact 2 above; the check reported
    min d(slope) = -2.0 at exactly theta_0. Convexity holds for 1/R, and that is
    what the check now verifies.
  * R at per-bin Jain 0.99 above R*, and therefore above S0's 5.4681, so the
    ideal controller can be both fairer *and* faster than S0. **Held**: 5.807,
    which is +1.6% on R* and +7.7% on S0.
  * Max-min fairness strictly above the equal-rate point. **Wrong**: it lands
    exactly on it (Jain 1.0, R = R*), which means the disadvantaged cores share a
    saturated resource with every other core, so nobody can be raised once they
    are pinned. This makes R* the canonical fair operating point rather than an
    arbitrary choice, and is asserted in the regression.
  * Numerical hygiene learned the hard way: SLSQP on lambda^(1-alpha)/(1-alpha) at
    large alpha returns *infeasible* points (an early run put alpha = 10 above the
    frontier, which is impossible). Every reference point is now rescaled to make
    max_r a_r'lambda = 1 exactly -- legitimate because the constraint set is a
    scaled cone and rescaling leaves Jain untouched -- and then asserted feasible
    and on-or-under the frontier.

Usage:
    PYTHONHASHSEED=0 python3 tradeoff_ring2_cc.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import linprog, minimize

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import BIN_W, W_FLITS
from ideal_ring2_cc import coefficients, dest_mix, jain, solve_max_total, solve_theta
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "tradeoff_ring2_cc.json"
PNG = ROOT / "results" / "tradeoff_ring2_cc.png"
REG = ROOT / "results" / "pareto_ring2_cc.json"


def _use_cjk_font() -> None:
    from matplotlib import font_manager as fm
    wanted = ("micro hei", "cjk", "noto sans sc", "source han sans")
    for f in fm.fontManager.ttflist:
        if any(w in f.name.lower() for w in wanted):
            plt.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def jain_bin_ceiling(lam: np.ndarray, bin_w: int = BIN_W) -> float:
    """Per-bin Jain an ideal deterministic scheduler reaches at rate vector `lam`.

    Counts in one bin are W*lambda_c*bin_w. A deterministic schedule realises the
    integer part exactly and rotates which cores absorb the remainder, so the
    reachable Jain is the Jain of the rounded counts; the residual gap to
    `jain(lam)` is the O(1/N^2) integrality term.
    """
    cnt = np.round(W_FLITS * np.asarray(lam, dtype=float) * bin_w)
    return jain(cnt) if cnt.sum() > 0 else 1.0


def solve_jain(a: np.ndarray, j_target: float) -> np.ndarray:
    """max total rate s.t. capacity holds and Jain(lambda) >= j_target. Exact.

    The Jain constraint looks non-convex but is not:

        J(lam) = (1'lam)^2 / (n ||lam||_2^2) >= J
            <=> ||lam||_2 <= (1'lam) / sqrt(J n),

    a second-order cone constraint -- an L2 norm bounded by a linear function. So
    maximising the linear objective over {A'lam <= 1} intersected with it is a
    convex program, and the frontier R(J) it traces is *exact* rather than an
    inner bound. Normalising lam = s*mu with mu in the simplex makes the cone
    constraint ||mu||_2 <= 1/sqrt(J n) independent of the scale s, leaving

        R(J)/W = 1 / min { max_r a_r'mu : mu in Simplex, ||mu||_2 <= 1/sqrt(Jn) }.

    At J = 1 the ball radius 1/sqrt(n) is exactly the minimum of ||mu||_2 over the
    simplex, attained only at the uniform mu, so R(1) = R* recovers closed form
    (C) -- which is the consistency check worth remembering.
    """
    n = a.shape[0]
    rad = 1.0 / np.sqrt(max(j_target, 1e-12) * n)

    def neg_rate(x):                       # x = [mu (n), t]
        return x[-1]

    cons = [
        {"type": "eq", "fun": lambda x: x[:n].sum() - 1.0},
        {"type": "ineq", "fun": lambda x: x[-1] - a.T @ x[:n]},
        {"type": "ineq",
         "fun": lambda x: rad - float(np.linalg.norm(x[:n]))},
    ]
    x0 = np.concatenate([np.full(n, 1.0 / n), [float(a.mean(axis=0).max())]])
    r = minimize(neg_rate, x0, method="SLSQP",
                 bounds=[(0.0, None)] * n + [(1e-12, None)],
                 constraints=cons, options={"maxiter": 800, "ftol": 1e-14})
    mu = np.maximum(r.x[:n], 0.0)
    mu = mu / mu.sum()
    # Scale back onto the capacity boundary; `tighten` keeps Jain untouched.
    return tighten(a, mu)


def tighten(a: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """Rescale `lam` so the most loaded resource sits exactly at capacity.

    The feasible set {lambda >= 0 : A'lambda <= 1} is a scaled cone, so dividing
    by max_r a_r'lambda always lands on the boundary, and since it is a uniform
    scaling it leaves Jain -- and every fairness ratio -- unchanged. That makes it
    a safe repair for a solver that returned a slightly infeasible or slightly
    slack point, and every alpha-fair optimum is on the boundary anyway.
    """
    load = float((a.T @ lam).max())
    return lam / load if load > 0 else lam


def alpha_fair(a: np.ndarray, alpha: float) -> np.ndarray:
    """max sum lambda^(1-alpha)/(1-alpha) s.t. A'lambda <= 1 (log when alpha=1).

    The returned point is tightened onto the capacity boundary: at large alpha the
    objective is stiff enough that SLSQP drifts outside the feasible set, and an
    infeasible "reference point" plotted above the frontier is worse than useless.
    """
    n = a.shape[0]
    lam0 = solve_theta(a, 1.0)

    def neg(x):
        x = np.maximum(x, 1e-12)
        return -float(np.log(x).sum() if abs(alpha - 1) < 1e-12
                      else (x ** (1 - alpha)).sum() / (1 - alpha))

    r = minimize(neg, lam0, method="SLSQP",
                 bounds=[(1e-9, None)] * n,
                 constraints=[{"type": "ineq",
                               "fun": lambda x: 1.0 - a.T @ x}],
                 options={"maxiter": 500, "ftol": 1e-12})
    return tighten(a, np.maximum(r.x, 1e-12))


def max_min_fair(a: np.ndarray) -> np.ndarray:
    """Max-min fair rates by progressive filling.

    Repeatedly raise the floor on every core that is still free; whichever cores
    cannot be raised further are frozen at the level reached, and the fill
    continues on the rest. This is the textbook definition, and unlike the
    equal-rate point it lets an advantaged core keep going once the starved ones
    are pinned.
    """
    n, m = a.shape
    lam = np.zeros(n)
    frozen = np.zeros(n, dtype=bool)
    for _ in range(n + 1):
        free = ~frozen
        if not free.any():
            break
        # max t s.t. lambda_free >= t, capacity holds, frozen cores fixed.
        nv = int(free.sum())
        # variables: [lambda_free (nv), t]
        c = np.zeros(nv + 1)
        c[-1] = -1.0
        cap = np.hstack([a[free].T, np.zeros((m, 1))])
        b_cap = 1.0 - a[frozen].T @ lam[frozen] if frozen.any() else np.ones(m)
        floor = np.hstack([-np.eye(nv), np.ones((nv, 1))])
        r = linprog(c=c, A_ub=np.vstack([cap, floor]),
                    b_ub=np.concatenate([b_cap, np.zeros(nv)]),
                    bounds=[(0, None)] * nv + [(0, None)], method="highs")
        assert r.success, r.message
        t = r.x[-1]
        lam[free] = t
        # A free core is frozen when raising it alone is impossible, i.e. it sits
        # on a saturated resource.
        load = a.T @ lam
        tight = load >= 1.0 - 1e-9
        for ci in np.where(free)[0]:
            if (a[ci][tight] > 1e-12).any():
                frozen[ci] = True
        if not tight.any():                      # nothing binds: done
            break
    return lam


def main() -> None:
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    cores, names, a = coefficients(topo, dest_mix(k=1600))
    n = len(cores)

    lam_max = solve_max_total(a)
    r_max = W_FLITS * float(lam_max.sum())
    lam_fair = solve_theta(a, 1.0)
    r_fair = W_FLITS * float(lam_fair.sum())

    # --- the closed form (C): R* from a single max over resources -------------
    abar = a.mean(axis=0)                        # abar_r = mean per-core load
    r_star_closed = W_FLITS / float(abar.max())
    bind_fair = names[int(abar.argmax())]

    # --- the frontier, finely ------------------------------------------------
    thetas = sorted(set(np.round(np.concatenate([
        np.linspace(0.0, 1.0, 101), np.linspace(0.55, 0.75, 41),
        np.linspace(0.97, 1.0, 31)]), 6)))
    curve = []
    for th in thetas:
        lam = solve_theta(a, float(th))
        bw = W_FLITS * float(lam.sum())
        curve.append({"theta": float(th), "bw": bw,
                      "jain_rate": jain(lam),
                      "jain_bin": jain_bin_ceiling(lam),
                      "min_over_mean": float(lam.min() / lam.mean())
                      if lam.mean() else 0.0})

    bw = np.array([p["bw"] for p in curve])
    th = np.array([p["theta"] for p in curve])
    # theta_0: last theta at which the fairness constraint is still slack.
    slack = np.where(bw >= r_max - 1e-7)[0]
    theta_0 = float(th[slack[-1]]) if len(slack) else 0.0
    # Fact 2 says 1/R = g/W is convex, while R itself is not (it has a concave
    # kink at theta_0). Check the provable statement, and report the counterexample
    # in R so the distinction stays visible rather than being quietly dropped.
    inv_slopes = np.diff(1.0 / bw) / np.diff(th)
    worst_convex_inv = float(np.min(np.diff(inv_slopes)))
    slopes = np.diff(bw) / np.diff(th)
    worst_convex_bw = float(np.min(np.diff(slopes)))
    assert worst_convex_inv > -1e-9, (
        f"1/R must be convex in theta (fact 2), got {worst_convex_inv:+.3e}")
    assert np.all(np.diff(bw) <= 1e-9), "R(theta) must be non-increasing"

    # --- reference fairness criteria -----------------------------------------
    refs = []
    lam_mm = max_min_fair(a)
    refs.append(("max-min fair (progressive filling)", lam_mm))
    for al in (0.0, 0.5, 1.0, 2.0, 4.0, 10.0):
        refs.append((f"alpha-fair alpha={al:g}"
                     + (" (proportional fair)" if al == 1.0 else "")
                     + (" (max throughput)" if al == 0.0 else ""),
                     alpha_fair(a, al)))
    ref_rows = []
    bw_of_theta = {p["theta"]: p["bw"] for p in curve}
    for label, lam in refs:
        lam = tighten(a, np.maximum(lam, 0.0))
        b = W_FLITS * float(lam.sum())
        mom = float(lam.min() / lam.mean()) if lam.mean() else 0.0
        # A reference point must be feasible and cannot beat the frontier at its
        # own fairness level -- that is what caught the bad alpha=10 solve.
        assert (a.T @ lam).max() <= 1 + 1e-9, label
        cap = max(v for t, v in bw_of_theta.items() if t <= mom + 1e-6)
        assert b <= cap + 1e-6, f"{label}: bw {b:.4f} > frontier {cap:.4f}"
        ref_rows.append({"criterion": label, "bw": b,
                         "jain_rate": jain(lam),
                         "jain_bin": jain_bin_ceiling(lam),
                         "min_over_mean": mom,
                         "pct_of_max": 100.0 * b / r_max})

    # --- the exact trade-off curve R(J), by the SOCP above -------------------
    j_lo = jain(lam_max)
    j_grid = sorted(set(np.round(np.concatenate([
        np.linspace(max(0.80, j_lo - 0.02), 1.0, 121),
        np.linspace(0.985, 1.0, 31)]), 6)))
    jcurve = []
    for jt in j_grid:
        lam = solve_jain(a, float(jt))
        assert (a.T @ lam).max() <= 1 + 1e-9, jt
        b = W_FLITS * float(lam.sum())
        jcurve.append({"jain_target": float(jt), "bw": b,
                       "jain_rate": jain(lam),
                       "jain_bin": jain_bin_ceiling(lam)})
    # The SOCP is solved per point, so enforce the shape the theory guarantees
    # (non-increasing in J) and clean up any solver noise by running a cummin.
    best = float("inf")
    for p in jcurve:
        best = min(best, p["bw"])
        p["bw_monotone"] = best
    inverse = [p for p in jcurve
               if any(abs(p["jain_target"] - t) < 1e-9
                      for t in (0.95, 0.98, 0.99, 0.995, 0.999, 1.0))]

    # --- measured schemes, for the overlay -----------------------------------
    meas = []
    if REG.exists():
        reg = json.loads(REG.read_text())
        s0 = next((r for r in reg["schemes"] if r["name"].startswith("S0")), None)
        for r in reg["schemes"]:
            meas.append({"name": r["name"], "bw": r["thr"],
                         "jain_bin": r["jain_bin"],
                         "buildable": r.get("bus_rule_ok", True)})
        s0_bw = s0["thr"] if s0 else None
    else:
        s0_bw = None

    data = {"n_cores": n, "w_flits": W_FLITS, "bin_w": BIN_W,
            "r_max": r_max, "r_fair": r_fair,
            "r_fair_closed_form": r_star_closed,
            "binding_resource_fair": bind_fair,
            "theta_0": theta_0,
            "convexity_min_slope_delta_inv_bw": worst_convex_inv,
            "convexity_min_slope_delta_bw": worst_convex_bw,
            "price_of_fairness_pct": 100.0 * (1 - r_fair / r_max),
            "curve": curve, "jain_curve": jcurve,
            "reference_points": ref_rows,
            "inverse": inverse, "measured": meas, "s0_bw": s0_bw}
    OUT.write_text(json.dumps(data, indent=2))

    # ---------------------------------------------------------------- report --
    print(f"n={n}  W={W_FLITS}  bin={BIN_W}")
    print(f"R_max            = {r_max:.4f} flit/cycle  (theta unconstrained)")
    print(f"R* (theta=1)     = {r_fair:.4f}   closed form W/max_r abar_r = "
          f"{r_star_closed:.4f}  [{bind_fair}]")
    print(f"price of fairness= {100 * (1 - r_fair / r_max):.2f}%")
    print(f"theta_0 (slack)  = {theta_0:.3f}")
    print(f"convexity: 1/R min d(slope) = {worst_convex_inv:+.3e} "
          f"({'convex, fact 2 holds' if worst_convex_inv > -1e-9 else 'FAILED'})")
    print(f"           R   min d(slope) = {worst_convex_bw:+.3e} "
          f"(concave kink at theta_0, as fact 2 predicts)\n")
    print(f"{'fairness criterion':<44}{'bw':>9}{'% of max':>10}"
          f"{'Jain_rate':>11}{'Jain_bin':>10}{'min/mean':>10}")
    for r in ref_rows:
        print(f"{r['criterion']:<44}{r['bw']:>9.4f}{r['pct_of_max']:>10.2f}"
              f"{r['jain_rate']:>11.5f}{r['jain_bin']:>10.5f}"
              f"{r['min_over_mean']:>10.4f}")
    print(f"\nexact frontier R(J) (SOCP):")
    print(f"{'required Jain':<16}{'max bw':>10}{'achieved J':>12}"
          f"{'vs R*':>9}{'vs S0':>9}")
    for r in inverse:
        vs0 = (f"{100 * (r['bw'] / s0_bw - 1):+.2f}%" if s0_bw else "—")
        print(f"{r['jain_target']:<16.3f}{r['bw']:>10.4f}"
              f"{r['jain_rate']:>12.5f}"
              f"{100 * (r['bw'] / r_fair - 1):>+8.2f}%{vs0:>9}")
    print(f"\nwrote {OUT}")
    plot(data)


def plot(d: dict) -> None:
    _use_cjk_font()
    cur = d["curve"]
    fig, axes = plt.subplots(1, 3, figsize=(17.5, 5.6))
    jb = [p["jain_bin"] for p in cur]
    bw = [p["bw"] for p in cur]
    th = [p["theta"] for p in cur]

    # -- panel 1: the trade-off itself, with measured schemes on the same axes -
    ax = axes[0]
    jc = d["jain_curve"]
    jx = [p["jain_rate"] for p in jc]
    jy = [p["bw_monotone"] for p in jc]
    ax.plot(jx, jy, "-", c="#b34700", lw=2.4, zorder=4,
            label="理想 CC 精确前沿 R(J)（二阶锥）")
    ax.fill_between(jx, 0, jy, color="#b34700", alpha=0.05)
    # Only past theta_0: below it the LP optimum is not unique (the whole
    # max-throughput face is optimal), so the Jain of whichever vertex HiGHS
    # returns is arbitrary and the curve would wander for no reason.
    tj = [(p["jain_bin"], p["bw"]) for p in cur if p["theta"] >= d["theta_0"]]
    ax.plot([x for x, _ in tj], [y for _, y in tj], "--", c="#c9a227", lw=1.5,
            zorder=3, label="θ = min λ/mean λ 参数化（内界，非最优）")
    for r in d["reference_points"]:
        if "max-min" in r["criterion"] or "proportional" in r["criterion"]:
            ax.scatter([r["jain_bin"]], [r["bw"]], s=74, marker="D",
                       c="#6f42c1", edgecolors="k", linewidths=0.5, zorder=6)
            ax.annotate(r["criterion"].split(" (")[0],
                        (r["jain_bin"], r["bw"]), textcoords="offset points",
                        xytext=(-6, 11), fontsize=7.5, ha="right",
                        color="#6f42c1")
    ok = [m for m in d["measured"] if m["buildable"]]
    no = [m for m in d["measured"] if not m["buildable"]]
    ax.scatter([m["jain_bin"] for m in ok], [m["bw"] for m in ok], s=46,
               c="#1f6feb", edgecolors="k", linewidths=0.5, zorder=5,
               label="实测方案（可实现）")
    ax.scatter([m["jain_bin"] for m in no], [m["bw"] for m in no], s=46,
               marker="x", c="#999999", zorder=5, label="实测（总线 <30 拍，不可实现）")
    lo_y, hi_y = 4.55, 6.62
    if d.get("s0_bw"):
        s0b = d["s0_bw"]
        # The acceptance region: Jain > 0.99 and bandwidth within 1% of S0.
        ax.add_patch(plt.Rectangle((0.99, 0.99 * s0b), 1.01 - 0.99,
                                   hi_y - 0.99 * s0b, fc="#1a7f37", alpha=0.10,
                                   ec="#1a7f37", lw=1.1, ls=":", zorder=1,
                                   label="验收区间：Jain>0.99 且带宽≥99%·S0"))
        ax.axhline(s0b, ls="-.", c="#666", lw=1.1, label=f"S0 带宽 {s0b:.4f}")
    inv99 = next((r for r in d["inverse"] if abs(r["jain_target"] - 0.99) < 1e-9),
                 None)
    if inv99 and d.get("s0_bw"):
        ax.annotate(f"前沿在 Jain=0.99 处 R = {inv99['bw']:.4f}\n"
                    f"比 S0 高 {100 * (inv99['bw'] / d['s0_bw'] - 1):.2f}%\n"
                    f"→ 理想控制器可以同时更公平且更快",
                    xy=(0.99, inv99["bw"]), xycoords="data",
                    xytext=(0.03, 0.70), textcoords="axes fraction",
                    fontsize=8.2,
                    arrowprops=dict(arrowstyle="->", lw=0.9, color="#1a7f37"),
                    bbox=dict(fc="#eaf6ec", ec="#1a7f37", lw=0.7))
    off = [m for m in d["measured"] if m["bw"] < lo_y]
    if off:
        ax.annotate(f"另有 {len(off)} 个方案带宽低于 {lo_y}，超出画幅",
                    (0.985, 0.015), xycoords="axes fraction", fontsize=7,
                    color="#888", ha="right")
    ax.set_xlim(0.86, 1.004)
    ax.set_ylim(lo_y, hi_y)
    ax.set_xlabel("公平性：50 拍分箱 Jain")
    ax.set_ylabel("总写带宽 flit/cycle")
    ax.set_title("公平性–带宽 trade-off：前沿与实测方案", fontsize=10.5)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7.6, loc="lower left")

    # -- panel 2: R(theta), the parametric LP, and its convexity --------------
    ax = axes[1]
    ax.plot(th, bw, "-", c="#b34700", lw=2.2)
    ax.axhline(d["r_max"], ls="--", c="#888", lw=1.1,
               label=f"R_max = {d['r_max']:.4f}")
    ax.axhline(d["r_fair"], ls="--", c="#1a7f37", lw=1.1,
               label=f"R* = W/max_r ā_r = {d['r_fair']:.4f}")
    ax.axvline(d["theta_0"], ls=":", c="#1f6feb", lw=1.4,
               label=f"θ0 = {d['theta_0']:.3f}（约束开始生效，凹折点）")
    ax.set_xlabel("θ = min λ / mean λ（等速率程度）")
    ax.set_ylabel("总写带宽 flit/cycle")
    ax.set_title("参数化 LP：R(θ) 非增，1/R 凸\n"
                 f"（1/R 凸性检验 min Δslope = "
                 f"{d['convexity_min_slope_delta_inv_bw']:+.1e}，"
                 f"R 本身在 θ0 处有凹折点）", fontsize=10.5)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="lower left")
    # The reference criteria, on the same theta axis.
    for r in d["reference_points"]:
        if "max-min" in r["criterion"] or "proportional" in r["criterion"]:
            ax.scatter([r["min_over_mean"]], [r["bw"]], s=70, marker="D",
                       c="#6f42c1", edgecolors="k", linewidths=0.5, zorder=6)
            ax.annotate(r["criterion"].split(" (")[0],
                        (r["min_over_mean"], r["bw"]),
                        textcoords="offset points", xytext=(-8, 10),
                        fontsize=7.5, ha="right", color="#6f42c1")

    # -- panel 3: marginal cost -- what the last bit of fairness costs --------
    ax = axes[2]
    j = np.array([p["jain_rate"] for p in jc])
    b = np.array([p["bw_monotone"] for p in jc])
    keep = np.argsort(j)
    j, b = j[keep], b[keep]
    uniq = np.concatenate([[True], np.diff(j) > 1e-6])
    j, b = j[uniq], b[uniq]
    dbdj = np.maximum(-np.gradient(b, j), 1e-6)
    ax.plot(j, dbdj, "-", c="#6f42c1", lw=2.0)
    ax.axvline(0.99, ls=":", c="#1a7f37", lw=1.6)
    ax.set_yscale("log")
    ax.set_xlim(0.86, 1.002)
    ax.set_xlabel("公平性：50 拍分箱 Jain")
    ax.set_ylabel("边际代价 −dR/dJ  (flit/cycle per unit Jain)")
    ax.set_title("公平性的边际价格单调上升\n最后那一点公平最贵", fontsize=10.5)
    ax.grid(alpha=0.25, which="both")

    fig.tight_layout()
    fig.savefig(PNG, dpi=150)
    print(f"wrote {PNG}")


if __name__ == "__main__":
    main()
