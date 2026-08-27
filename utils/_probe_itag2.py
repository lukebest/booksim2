"""Scratch: scoped I-tag as a duty cycle -- starve `t_inj`, block
`itag_hold`. A tag cannot stop transit, so a node starved by transit holds
it for a long time and idles upstream injectors for nothing; bounding the
hold should buy back most of the bandwidth the lockout costs.
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

cases = [("S0 baseline", {})]
for ti in (1, 2, 4):
    for hold in (1, 2, 3, 4, 6, 8, 16, 0):
        cases.append((f"seg t_inj={ti} hold={hold or 'inf'}",
                      {"itag_scope": "segment", "t_inj": ti,
                       "itag_hold": hold}))
base = None
best = []
for lab, over in cases:
    cfg = dict(FABRIC)
    cfg.update(over)
    r = run_scheme("S0", topo, tx, seed=0, cfg=cfg, quiet=True)
    inj = r["wr_inject_by_core"]
    f = fairness_stats(inj, r["makespan"], K * W_FLITS)
    jb = binned_jain(inj, BIN_W, f["t_fair"])
    if base is None:
        base = f["throughput"]
    d = 100.0 * (f["throughput"] - base) / base
    j = jb["jain_bin_mean"]
    ok = "PASS" if j > 0.99 and d > -1.0 else ""
    best.append((j, d, lab))
    print(f"  {lab:<28} Jbin={j:<9} thr={f['throughput']:<8}({d:+6.2f}%)  "
          f"mm={f['max_min']:<7} p05={jb['jain_bin_p05']:<8} "
          f"itag={r['n_itag_raised']:<7} {ok}", flush=True)
print("\nfrontier (Jain at >= -1% throughput):")
for j, d, lab in sorted((x for x in best if x[1] > -1.0), reverse=True)[:5]:
    print(f"  {lab:<28} Jbin={j} thr {d:+.2f}%")
