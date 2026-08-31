"""Scratch: scoped I-tag. Does narrowing the lockout keep the fairness the
blunt version buys, without paying its bandwidth?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
K = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
tx = build_pattern("uniform", k=K, W=W_FLITS, seed=0)
base = None
cases = [("S0 baseline", {})]
for scope in ("plane", "segment"):
    for ti in (1, 2, 3, 4, 6, 8, 16):
        cases.append((f"itag {scope} t_inj={ti}",
                      {"itag_scope": scope, "t_inj": ti}))
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
    ok = "PASS" if jb["jain_bin_mean"] > 0.99 and d > -1.0 else ""
    print(f"  {lab:<28} Jbin={jb['jain_bin_mean']:<9} "
          f"thr={f['throughput']:<8}({d:+6.2f}%)  mm={f['max_min']:<7} "
          f"itag={r['n_itag_raised']:<8} {ok}", flush=True)
