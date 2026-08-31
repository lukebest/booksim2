"""Scratch: give the dodge look-ahead more candidates to work with.

A deeper per-direction inject Q was inert for plain S0 -- the arbiter only
ever looks at the head, so extra entries did nothing. With the look-ahead it
should matter a lot: every extra entry is another chance to find a flit that
leaves the ring before the requester, which is what turns a wasted yield
into a free one.
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

cases = [("S0 baseline", "S0", {}),
         ("S0 dirq=32", "S0", {"dir_inj_depth": 32})]
for w in (2, 4):
    for dq in (8, 16, 32):
        for hold in (4, 16):
            cases.append(
                (f"S22 w={w} dirq={dq} hold={hold}", "S22",
                 {"dfc_window": w, "dfc_bus_lat": 1, "dfc_thresh": 0.5,
                  "dfc_hold": hold, "dfc_dodge": dq, "dir_inj_depth": dq,
                  "inj_depth": max(12, dq)}))
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
    print(f"  {lab:<30} Jbin={j:<9} thr={f['throughput']:<8}({d:+6.2f}%)  "
          f"mm={f['max_min']:<7} p05={jb['jain_bin_p05']:<8} "
          f"yield={fc.get('n_dfc_yield', 0):<7} "
          f"dodge={fc.get('n_dfc_dodge', 0):<7} {ok}", flush=True)
print("\nfrontier (Jain at >= -1% throughput):")
for j, d, lab in sorted((x for x in rows if x[1] > -1.0), reverse=True)[:6]:
    print(f"  {lab:<30} Jbin={j} thr {d:+.2f}%")
