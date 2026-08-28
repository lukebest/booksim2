#!/usr/bin/env python3
"""Redo the variance decomposition on the corrected fabric. Is Jain > 0.99 reachable?

This is the measurement that decides whether phase 3's fairness line is a tuning
problem or an impossibility, and the old answer is void. On the shared-port
fabric the per-bin unfairness was **99.9% within-core timing jitter** around
near-equal long-run rates (`JITTER_DECOMP`), which is why the plan was to
regularise *when* each core gets its slots and pay nothing in bandwidth.

Per-direction ports changed the premise. 3.2.0 showed the ten cores split into a
fixed six-fast / four-slow group with whole-window max/min 1.69, and the four
slow ones are structurally determined (they sit at the exits of the two HA-less
nodes and must board the ring's busiest hops). That is a *persistent rate
difference*, not jitter -- and the two have different prices. Jitter can be
smoothed for free; a rate difference can only be removed by moving bandwidth
from the fast cores to the slow ones, and the slow ones are blocked by a hop
that is already 97% busy, so the total has to fall.

The decomposition splits each core's per-bin flit count into:

  * `var_between` -- variance across cores of their own per-bin means. This is
    persistent: core A is simply faster than core B for the whole window.
  * `var_within`  -- variance across bins within one core, averaged over cores.
    This is jitter: same long-run rate, lumpy arrival.

The number that actually answers the question is `jain_regular`: replace every
core's per-bin count with that core's own mean, i.e. remove all jitter and keep
only the rate differences, then re-measure binned Jain. **That is the ceiling any
timing-regularising mechanism can reach.** If it is below 0.99, no amount of
pacing, dodging or scoped yielding gets there without taking rate away from the
fast cores, and the -1.45% on S22's frontier is a floor rather than a tuning
artifact.

Forecast, written before running: `jain_regular` for S0 comes out around
0.93-0.96 -- well below 0.99 -- and `var_between` is no longer negligible but
still the minority of total variance (say 10-30%), because 50-cycle bins are
short enough that Poisson-ish counting noise dominates any variance measure even
when the underlying rates differ by 1.69x. Both halves matter: a low
`jain_regular` proves the rate difference is what binds, while a still-small
`var_between` share would show that the *variance* split is the wrong statistic
to reason about it with -- which is exactly the trap the old round fell into.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_jitter2.py [K]
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, K_PER_CORE, S22_CFG, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_jitter2.json")


def _jain(xs) -> float:
    xs = [x for x in xs]
    s = sum(xs)
    sq = sum(x * x for x in xs)
    return (s * s) / (len(xs) * sq) if sq > 0 else 1.0


def _counts(inj: dict[int, list[int]], bin_w: int, t_end: int
            ) -> dict[int, list[int]]:
    nb = max(1, int(t_end // bin_w))
    out = {}
    for c, ts in inj.items():
        row = [0] * nb
        for t in ts:
            b = int(t // bin_w)
            if 0 <= b < nb:
                row[b] += 1
        out[c] = row
    return out


def _decompose(cnt: dict[int, list[int]]) -> dict[str, float]:
    cores = sorted(cnt)
    nb = len(cnt[cores[0]])
    means = {c: st.fmean(cnt[c]) for c in cores}
    grand = st.fmean(means.values())
    var_between = st.pvariance(list(means.values())) if len(cores) > 1 else 0.0
    var_within = st.fmean([st.pvariance(cnt[c]) if nb > 1 else 0.0
                           for c in cores])
    tot = var_between + var_within
    # The ceiling for any mechanism that only fixes *timing*: give every core
    # its own mean in every bin, so jitter is gone and rate gaps remain.
    jain_regular = st.fmean([_jain([means[c] for c in cores])
                             for _ in range(1)])
    # And the mirror image: keep the jitter, equalise the rates.
    jain_bins_actual = st.fmean([_jain([cnt[c][b] for c in cores])
                                 for b in range(nb)])
    return {
        "n_bins": nb, "per_core_per_bin": round(grand, 2),
        "var_between": round(var_between, 3),
        "var_within": round(var_within, 3),
        "within_share": round(var_within / tot, 4) if tot else 1.0,
        "between_share": round(var_between / tot, 4) if tot else 0.0,
        "jain_regular": round(jain_regular, 5),
        "jain_bin_mean": round(jain_bins_actual, 5),
        "rate_min": round(min(means.values()), 3),
        "rate_max": round(max(means.values()), 3),
        "rate_ratio": round(max(means.values()) / min(means.values()), 4),
    }


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else K_PER_CORE
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS
    print(f"K={k}  bin_w={BIN_W}\n", flush=True)

    cases = [("S0", dict(FABRIC)),
             ("S22", {**FABRIC, **S22_CFG})]
    rows = []
    for scheme, cfg in cases:
        r = run_scheme(scheme, topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        tf = f.get("t_fair") or 0
        d = _decompose(_counts(inj, BIN_W, tf))
        d["scheme"] = scheme
        d["thr"] = f["throughput"]
        d["jain_bin_report"] = binned_jain(inj, BIN_W, tf)["jain_bin_mean"]
        rows.append(d)
        print(f"  {scheme}: thr={d['thr']}  Jbin={d['jain_bin_report']}\n"
              f"      每核每箱 {d['per_core_per_bin']} flit，"
              f"核间速率 {d['rate_min']}–{d['rate_max']}"
              f"（比 {d['rate_ratio']}）\n"
              f"      var_between={d['var_between']} "
              f"var_within={d['var_within']}  "
              f"within 占 {100 * d['within_share']:.1f}%\n"
              f"      <b>把抖动完全抹平后的 Jain 上限 = "
              f"{d['jain_regular']}</b>", flush=True)

    OUT.write_text(json.dumps({"k": k, "bin_w": BIN_W, "rows": rows},
                              indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
