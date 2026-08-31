"""Scratch: per bin and per core, is DAT injection supply-limited or
contention-limited?

The buffer census says the cores' DAT shared FIFO averages 0.88 of 12
entries, which would mean a core injects a WriteData flit almost as soon as
it has one -- and then the per-bin jitter the fairness metric sees is jitter
in *when the core has work*, not in ring arbitration. That distinction
decides what phase 3 can possibly fix: a pacer can only reshape a backlog it
actually has.
"""
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, W_FLITS, build_pattern,
                                  fairness_stats, make_sim)
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology, is_core

K = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE)
tx = build_pattern("uniform", k=K, W=W_FLITS, seed=0)
sim = make_sim("S0", topo, seed=0, cfg=dict(FABRIC))
cores = [n for n in range(topo.n) if is_core(n)]

# Per core: cycles the DAT source had nothing to board vs had something and
# lost, sampled every cycle.
dry = defaultdict(int)
blocked = defaultdict(int)
inj = defaultdict(int)
dry_bin = defaultdict(lambda: defaultdict(int))
sim.offer_batch(tx)
while sim.t < 2_000_000 and not sim.done():
    before = {c: sim.st["n_injected"] for c in ()}
    for c in cores:
        q = (len(sim.srcq[(c, 0, "dat")])
             + sum(len(sim.srcq[(c, 0, "dat", d)]) for d in (1, -1)))
        if q == 0:
            dry[c] += 1
            dry_bin[c][sim.t // BIN_W] += 1
    sim.step()
    _ = before
r = sim.summary()
f = fairness_stats(r["wr_inject_by_core"], r["makespan"], K * W_FLITS)
mk = r["makespan"]
print(f"K={K} mk={mk} thr={f['throughput']}")
print(f"{'core':>5} {'dry%':>7} {'inj/cyc':>8} {'dry-per-bin var':>16}")
tf = f["t_fair"]
for c in cores:
    got = len([t for t in r["wr_inject_by_core"][c] if t <= tf])
    dv = [dry_bin[c][b] for b in range(tf // BIN_W)]
    print(f"{c:>5} {100.0 * dry[c] / mk:>7.2f} {got / tf:>8.4f} "
          f"{st.pvariance(dv):>16.2f}")
# How much of the per-bin injection variance is explained by dry cycles?
allv, ally = [], []
for c in cores:
    cnt = defaultdict(int)
    for t in r["wr_inject_by_core"][c]:
        cnt[t // BIN_W] += 1
    for b in range(tf // BIN_W):
        allv.append(dry_bin[c][b])
        ally.append(cnt[b])
mx, my = st.mean(allv), st.mean(ally)
cov = sum((a - mx) * (b - my) for a, b in zip(allv, ally)) / len(allv)
rho = cov / (st.pstdev(allv) * st.pstdev(ally))
print(f"\nper-bin dry cycles: mean={mx:.2f} of {BIN_W}   "
      f"injections: mean={my:.2f}  corr(dry, injected)={rho:.4f}  "
      f"r2={rho * rho:.4f}")
