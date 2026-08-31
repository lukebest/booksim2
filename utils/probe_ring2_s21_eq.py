#!/usr/bin/env python3
"""S21 + `pace_equalise` under the 30-cycle bus rule: the leading candidate.

Why this shape is the right one to try next
-------------------------------------------
Three results from this round narrow the design space hard:

  * The bus costs 30 cycles, unchangeably. S22's published point uses
    `dfc_bus_lat = 1`; at `window=2, bus_lat=30` it does not merely degrade, it
    goes **worse than no control at all** -- Jain 0.740 against S0's 0.879, and
    -15% bandwidth (`probe_ring2_buslat30.py`). A controller steering on a
    15-window-stale signal actively misfires.
  * 3.2.2: the unfairness that has to be removed is a *persistent* rate split
    (six fast / four slow, long-run rates 1.69x apart). With timing jitter
    perfectly removed S0 still only reaches Jain 0.953, so **any scheme that
    only regularises timing cannot pass**, and rate has to actually move.
  * A persistent quantity does not decorrelate in 30 cycles. So the 30-cycle bus
    is only fatal to controllers whose control window is *shorter* than the
    delay. A scheme measuring long-run rate over a 64+ cycle window can pay 30
    cycles and barely notice.

S21 fits all three. Its pacer is **bus-free and sender-driven** (a local credit
counter, self-clocked from what the ring already granted it), so the part that
runs every cycle pays no bus delay at all. `pace_equalise` then adds exactly the
one globally-informed step the 3.2.2 result demands -- a core running more than
`pace_tol` above the slowest active core trims its rate -- over the *same* 3-bit
broadcast S1 uses, already charged at `pace_bus_lat = 30`.

It is also cheap in the place that matters. S22's real hardware cost is not its
arithmetic, it is the inject queue: depth 8 -> 32 per direction so `dfc_dodge`
has candidates to overtake, plus destination-tag comparators for ordering. S21
runs at stock depth with no reordering.

One likely trap, same class as the bug already fixed in the regression suite:
`pace_init = 1.0` was "unthrottled" when a shared port capped a core at 1
flit/cycle. With per-direction ports a core can board 2 DAT flits/cycle, so the
default is now a 2x throttle rather than a neutral start. The sweep varies it.

Forecast, written before running:
  * Plain S21 (no equalise) tops out near Jain 0.95, i.e. at the 3.2.2 ceiling
    for timing-only mechanisms, confirming that prediction from the other side.
  * S21 + equalise clears Jain 0.99. Whether it clears the 1% bandwidth line is
    genuinely open; trimming the fast cores to the slowest core's rate is the
    same rate transfer S22 makes, so I expect a similar -1% to -2%, with the
    tightest `pace_tol` costing the most.
  * `pace_init >= 2.0` is worth >= 2% throughput over the 1.0 default.
  * Falsifier: if equalise cannot beat Jain 0.97, then trimming against the
    *slowest* core is too blunt on this fabric and the next step is a
    receiver-driven trigger instead of a sender-side rate trim.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_s21_eq.py [K]
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_s21_eq.json")


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS

    base = run_scheme("S0", topo, tx, cfg=dict(FABRIC), quiet=True)
    binj = {int(c): v for c, v in (base.get("wr_inject_by_core") or {}).items()}
    bf = fairness_stats(binj, base["makespan"] or 1, fpc)
    s0 = bf["throughput"]
    s0j = binned_jain(binj, BIN_W, bf.get("t_fair") or 0)["jain_bin_mean"]
    print(f"K={k}  S0 thr={s0} Jbin={s0j} U={s0 * s0j:.4f}\n", flush=True)

    # The first sweep found S21 at -61% throughput: its EWMA feeds back on the
    # rate it *achieved* while the pacer itself was withholding credit, so a
    # slot-limited core ratchets down toward `pace_floor` and never climbs back.
    # `pace_headroom` is the only knob that can arrest that -- it is the factor
    # by which the target over-provisions the measurement -- and `pace_gain`
    # sets how fast the ratchet runs. Sweep both before touching the code.
    cases = []
    for hr, g in product((1.05, 1.2, 1.5, 2.0), (0.25, 0.05)):
        cases.append((f"S21 无 eq: headroom {hr} gain {g}",
                      {"pace_headroom": hr, "pace_gain": g,
                       "pace_equalise": False}))
    # Then equalise on top of the best-behaved pacer settings, at the mandated
    # 30-cycle bus.
    for hr, tol, win in product((1.5, 2.0), (0.02, 0.08), (64,)):
        cases.append((
            f"S21+eq headroom {hr} tol {tol} win {win}",
            {"pace_headroom": hr, "pace_gain": 0.25, "pace_equalise": True,
             "pace_tol": tol, "pace_window": win, "pace_bus_lat": 30}))

    rows = []
    for name, over in cases:
        cfg = dict(FABRIC)
        cfg.update({"pace_burst": 1.0})
        cfg.update(over)
        r = run_scheme("S21", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr = f["throughput"]
        d = round(100 * (thr - s0) / s0, 2)
        jm = jb["jain_bin_mean"]
        ok = jm > 0.99 and abs(d) < 1.0
        rows.append({"case": name, "over": over, "thr": thr, "delta_pct": d,
                     "jain_bin": jm, "maxmin": f["max_min"],
                     "u": round(thr * jm, 4), "pass": ok})
        print(f"  {name:<44} Jbin={jm:<9} thr={thr:<8} ({d:+.2f}%) "
              f"U={thr * jm:.4f}{'   <== PASS' if ok else ''}", flush=True)

    npass = sum(1 for r in rows if r["pass"])
    print(f"\n{npass}/{len(rows)} pass both lines")
    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "s0_jbin": s0j,
                               "s0_u": round(s0 * s0j, 4), "rows": rows},
                              indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
