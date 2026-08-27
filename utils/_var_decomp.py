"""Scratch: is per-bin unfairness persistent (rate) or bursty (timing)?

The phase-3 target (mean Jain over 50-cycle bins > 0.99) is only reachable
at no bandwidth cost if the unfairness is timing jitter around equal
long-run rates. If instead the cores have genuinely different achievable
rates, equalising them costs throughput.
"""
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, binned_jain,
                                  build_pattern, fairness_stats, jain,
                                  run_scheme)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
K = int(sys.argv[1]) if len(sys.argv) > 1 else 2500
tx = build_pattern("uniform", k=K, W=W_FLITS, seed=0)

for lab, over in (("S0 rr", {}), ("S0 free_slot", {"inj_sel": "free_slot"})):
    cfg = dict(FABRIC)
    cfg.update(over)
    r = run_scheme("S0", topo, tx, seed=0, cfg=cfg, quiet=True)
    inj = r["wr_inject_by_core"]
    f = fairness_stats(inj, r["makespan"], K * W_FLITS)
    tf, cores = f["t_fair"], sorted(inj)
    nb = tf // BIN_W
    cnt = {c: [0] * nb for c in cores}
    for c in cores:
        for t in inj[c]:
            b = t // BIN_W
            if b < nb:
                cnt[c][b] += 1
    mean_c = {c: st.mean(cnt[c]) for c in cores}
    var_between = st.pvariance(list(mean_c.values()))
    var_within = st.mean([st.pvariance(cnt[c]) for c in cores])
    jb = binned_jain(inj, BIN_W, tf)
    print(f"{lab:14s} thr={f['throughput']:<8} maxmin={f['max_min']:<7} "
          f"Jbin={jb['jain_bin_mean']} null={jb['jain_bin_null']}")
    print(f"   per-core per-bin mean={st.mean(mean_c.values()):.2f}  "
          f"var_between={var_between:.3f}  var_within={var_within:.3f}  "
          f"within share={var_within / (var_within + var_between):.3f}")
    print(f"   Jain if timing were perfectly regular = "
          f"{jain(list(mean_c.values())):.5f}")
    print(f"   per-core bin means: {[round(mean_c[c], 2) for c in cores]}")
