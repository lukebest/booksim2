#!/usr/bin/env python3
"""The flow-control bus costs 30 cycles, unchangeably. Does S22 survive it?

S1 and S15 were always charged `FC_BUS_LAT = 30`. S22 was not: its operating
point uses `dfc_bus_lat = 1`, and the hardware table already calls that out as
"S22 唯一比 S1 更苛刻的地方". Under the fixed 30-cycle rule that operating point
is not buildable, so every S22 number in the report is now a candidate rather
than a result, and this probe re-prices it.

The naive expectation is that S22 collapses: it controls on a `dfc_window = 2`
window, so a 30-cycle-stale deficit is fifteen windows old, i.e. the controller
is steering on a signal from a different era.

But 3.2.2 argues the opposite, and that is the interesting prediction here. The
unfairness S22 has to remove is **persistent**, not jitter: the ten cores split
into a fixed six-fast / four-slow group whose long-run rates differ by 1.69x,
and with timing jitter perfectly removed S0 still only reaches Jain 0.953. A
quantity that is stable over the whole run does not decorrelate in 30 cycles.
So a stale signal should still point the right way -- *provided the window is
long enough that the staleness is a fraction of a window rather than many
windows*.

That makes the fix structural rather than a re-tune: widen `dfc_window` until it
is comfortably longer than the 30-cycle transport delay, and re-tune `dfc_margin`
at that window.

Forecast, written before running:
  * `window=2, bus_lat=30` loses most of S22's fairness gain -- Jain falls back
    to 0.93-0.96 (from 0.98878), because at 15 windows of lag the yield decisions
    are close to uncorrelated with the deficit that triggered them.
  * `window >= 32, bus_lat=30` recovers most of it, landing within ~0.005 Jain
    of the `bus_lat=1` point, because 30 cycles is then <= 1 window and the
    deficit it reports is a long-run rate difference that is genuinely still
    there.
  * The bandwidth cost at the recovered point is *no worse* than at `bus_lat=1`,
    and plausibly better: a longer window averages away the near-level gaps that
    `dfc_margin` exists to refuse, so fewer pointless yields.
  * Falsifier: if `window >= 32` at `bus_lat=30` cannot get Jain above ~0.97,
    then the mechanism really does need instantaneous feedback and the whole S22
    line is dead under the 30-cycle rule, which would make a bus-free in-band
    scheme the only remaining direction.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_buslat30.py [K]
"""
from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, S22_CFG, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_buslat30.json")


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
    print(f"K={k}  S0 thr={s0} Jbin={s0j}\n", flush=True)

    rows = []
    # The reference point that the 30-cycle rule forbids, then the grid.
    cases = [("参照：window 2, bus 1（规则不允许）", 2, 1, 4.0)]
    cases += [(f"window {w}, bus 30, margin {m}", w, 30, m)
              for w, m in product((2, 8, 16, 32, 64), (2.0, 3.0, 4.0))]

    for name, w, lat, margin in cases:
        cfg = dict(FABRIC)
        cfg.update(S22_CFG)
        cfg.update({"dfc_window": w, "dfc_bus_lat": lat, "dfc_margin": margin})
        r = run_scheme("S22", topo, tx, cfg=cfg, quiet=True)
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr = f["throughput"]
        d = round(100 * (thr - s0) / s0, 2)
        jm = jb["jain_bin_mean"]
        ok = jm > 0.99 and abs(d) < 1.0
        rows.append({"case": name, "window": w, "bus_lat": lat,
                     "margin": margin, "thr": thr, "delta_pct": d,
                     "jain_bin": jm, "maxmin": f["max_min"],
                     "u": round(thr * jm, 4), "pass": ok})
        print(f"  {name:<34} Jbin={jm:<9} thr={thr:<8} ({d:+.2f}%) "
              f"U={thr * jm:.4f}{'   <== PASS' if ok else ''}", flush=True)

    npass = sum(1 for r in rows if r["pass"])
    print(f"\n{npass}/{len(rows)} pass both lines")
    OUT.write_text(json.dumps({"k": k, "s0_thr": s0, "s0_jbin": s0j,
                               "s0_u": round(s0 * s0j, 4), "rows": rows},
                              indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
