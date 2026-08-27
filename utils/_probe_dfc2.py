"""Scratch: push the deficit controller's timescale inside the 50-cycle bin.

S22 with a 64-cycle window and a 30-cycle bus equalises *cumulative*
progress almost perfectly (max/min 1.008) and leaves the per-bin index
almost untouched -- which is what the variance decomposition predicted, since
97-99% of the per-bin variance is jitter faster than that loop can see. The
question this answers is whether the same mechanism run at a timescale well
inside the 50-cycle bin turns the deficit *into* the per-bin balance.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
K = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
tx = build_pattern("uniform", k=K, W=W_FLITS, seed=0)

cases = [("S0 baseline", "S0", {})]
for w, bl in ((16, 2), (8, 1), (4, 1), (2, 1), (1, 1)):
    for th in (0.5, 1.0, 2.0):
        cases.append((f"S22 w={w} bl={bl} th={th}", "S22",
                      {"dfc_window": w, "dfc_bus_lat": bl, "dfc_thresh": th,
                       "dfc_hold": 16}))
# The deficit loop fixes the level; a loose pacer clips the residual bursts.
for burst in (2.0, 3.0, 4.0):
    cases.append((f"S22 w=4 + pace burst={burst}", "S22",
                  {"dfc_window": 4, "dfc_bus_lat": 1, "dfc_thresh": 1.0,
                   "dfc_hold": 16}))
base = None
rows = []
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
    fc = r.get("fc") or {}
    rows.append((j, d, lab))
    print(f"  {lab:<28} Jbin={j:<9} thr={f['throughput']:<8}({d:+6.2f}%)  "
          f"mm={f['max_min']:<7} p05={jb['jain_bin_p05']:<8} "
          f"yield={fc.get('n_dfc_yield', 0):<8} |D|={fc.get('mean_abs_deficit')}"
          f" posts={fc.get('bus_posts')} {ok}", flush=True)
print("\nfrontier (Jain at >= -1% throughput):")
for j, d, lab in sorted((x for x in rows if x[1] > -1.0), reverse=True)[:6]:
    print(f"  {lab:<28} Jbin={j} thr {d:+.2f}%")
