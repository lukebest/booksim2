"""Scratch: separate the pacer's mechanism from its control loop.

The self-clocked pacer lost 18% of the bandwidth, but that run conflated two
things: whether a deterministic interval can carry the load at all, and
whether a rate estimated from what the ring granted converges to the right
place. Pinning the rate (`pace_gain=0`) answers the first question alone.

If a fixed-rate pacer at the achievable rate is both regular and lossless,
then the design problem is only rate discovery, which is what a controller
is for. If it is lossy even at the right rate, gating injection on a credit
is the wrong mechanism and the fix has to live in the arbiter.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
K = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
tx = build_pattern("uniform", k=K, W=W_FLITS, seed=0)

cases = [("S0 baseline", "S0", {})]
for rate in (0.50, 0.53, 0.56, 0.60, 0.70, 0.85, 1.0):
    for burst in (1.0, 2.0):
        cases.append((f"fixed rate={rate} burst={burst}", "S21",
                      {"pace_gain": 0.0, "pace_init": rate,
                       "pace_burst": burst, "pace_equalise": False}))
base = None
for lab, scheme, over in cases:
    cfg = dict(FABRIC)
    cfg.update(over)
    r = run_scheme(scheme, topo, tx, seed=0, cfg=cfg, quiet=True)
    inj = r["wr_inject_by_core"]
    f = fairness_stats(inj, r["makespan"], K * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"])
    if base is None:
        base = f["throughput"]
    d = 100.0 * (f["throughput"] - base) / base
    j = jb["jain_bin_mean"]
    ok = "PASS" if j > 0.99 and d > -1.0 else ""
    print(f"  {lab:<32} Jbin={j:<9} thr={f['throughput']:<8}({d:+6.2f}%)  "
          f"mm={f['max_min']:<7} p05={jb['jain_bin_p05']:<8} "
          f"deny={(r.get('fc') or {}).get('n_pace_deny', 0):<9} {ok}",
          flush=True)
