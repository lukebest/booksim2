"""Scratch: S22 deficit-triggered scoped yield. Does moving the mechanism
from 'withhold from the sender' to 'who wins the hop' close the trade?"""
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
for th in (0.5, 1.0, 2.0, 4.0):
    for hold in (4, 16, 0):
        cases.append((f"S22 th={th} hold={hold or 'inf'}", "S22",
                      {"dfc_thresh": th, "dfc_hold": hold}))
cases += [
    ("S22 th=1 w=32", "S22", {"dfc_thresh": 1.0, "dfc_window": 32}),
    ("S22 th=1 w=16", "S22", {"dfc_thresh": 1.0, "dfc_window": 16}),
    ("S22 th=1 plane", "S22", {"dfc_thresh": 1.0, "dfc_scope": "plane"}),
    ("S22 th=1 buslat=1", "S22", {"dfc_thresh": 1.0, "dfc_bus_lat": 1}),
]
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
          f"mm={f['max_min']:<7} p05={jb['jain_bin_p05']:<8} "
          f"req={fc.get('n_dfc_req', 0):<7} yield={fc.get('n_dfc_yield', 0):<8}"
          f" |D|={fc.get('mean_abs_deficit')} {ok}", flush=True)
print("\nfrontier (Jain at >= -1% throughput):")
for j, d, lab in sorted((x for x in rows if x[1] > -1.0), reverse=True)[:6]:
    print(f"  {lab:<24} Jbin={j} thr {d:+.2f}%")
