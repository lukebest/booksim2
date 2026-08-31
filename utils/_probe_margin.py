"""Scratch: the deficit margin, to close the last few tenths of a point.

At the official K the frontier lands astride the acceptance line: Jain
0.98949 at -1.07% and 0.99094 at -1.34%. `dfc_margin` should buy back the
difference by dropping the yields that swap two nodes which are already
level -- those cost a hop and move the index almost not at all.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
K = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
tx = build_pattern("uniform", k=K, W=W_FLITS, seed=0)
DEEP = {"dfc_dodge": 32, "dir_inj_depth": 32, "inj_depth": 32,
        "dfc_bus_lat": 1, "dfc_thresh": 0.5}

cases = [("S0 baseline", "S0", {})]
for w, hold in ((2, 16), (3, 16), (4, 16)):
    for m in (0.0, 0.5, 1.0, 2.0):
        cases.append((f"w={w} h={hold} margin={m}", "S22",
                      {"dfc_window": w, "dfc_hold": hold, "dfc_margin": m,
                       **DEEP}))
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
    print(f"  {lab:<24} Jbin={j:<9} thr={f['throughput']:<8}({d:+6.2f}%)  "
          f"mm={f['max_min']:<7} yield={fc.get('n_dfc_yield', 0):<7} "
          f"dodge={fc.get('n_dfc_dodge', 0):<7} {ok}", flush=True)
print("\nfrontier (Jain at >= -1% throughput):")
for j, d, lab in sorted((x for x in rows if x[1] > -1.0), reverse=True)[:6]:
    print(f"  {lab:<24} Jbin={j} thr {d:+.2f}%")
