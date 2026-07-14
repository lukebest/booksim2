#!/usr/bin/env python3
"""Flit-level DES to validate NC bounds from nc_mesh_analysis.py (CAL_SOFT).

Model matches the NC assumptions exactly:
* directed mesh links, 1 flit/cycle, wire delay H=7 (X) / V=9 (Y);
* calendar (HP) traffic occupies each link for load[l] contiguous cycles at
  the start of every period (worst-case solid busy run, globally aligned);
* BG: per-node Bernoulli packet injection rate lam/m_b (so flit rate = lam),
  uniform destinations, packets of m_b flits, XY routing, per-link FIFO,
  strict priority (link unusable during calendar cycles);
* measures BG end-to-end packet delay (inject -> last flit at dest ramp)
  and max per-link queue occupancy.

Check: sim_p100 <= NC bg_delay bound, sim max queue <= NC backlog bound.
"""

import argparse
import json
import random
from collections import defaultdict, deque
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nc_mesh_analysis import Mesh, makespan_bound, analyze

ROOT = Path(__file__).resolve().parents[1]


def run_des(mesh, cload, period, lam, m_b, cycles, seed=1, shaped_m=0,
            burst=1):
    """shaped_m>0: spread each link's calendar load in runs of shaped_m flits
    evenly over the period (CAL_SHAPED) instead of one solid run (CAL_SOFT).
    burst>1: aligned adversarial BG injection — every node injects a burst of
    `burst` packets simultaneously every burst*m_b/lam cycles (same mean rate).
    """
    rng = random.Random(seed)
    N = mesh.n
    pkt_rate = lam / m_b
    burst_gap = max(1, int(round(burst * m_b / lam)))

    def cal_busy(l, t):
        L = cload.get(l, 0.0)
        if not L:
            return False
        ph = t % period
        if not shaped_m:
            return ph < L
        nrun = max(1, int(L // shaped_m))
        gap = period / nrun
        return (ph % gap) < shaped_m

    # queue[link] = deque of (pkt_id, flit_idx)
    queue = defaultdict(deque)
    inflight = []  # (arrive_cycle, link, pkt, fi) wire pipeline
    pkt_info = {}  # id -> dict(links, t_inject, dst, flits_left)
    delays = []
    maxq = defaultdict(int)
    next_id = 0
    ramp_free = [0] * N  # next cycle the up-ramp of node s can push a flit

    for t in range(cycles):
        # injection
        for s in range(N):
            npk = 0
            if burst > 1:
                if t % burst_gap == 0:
                    npk = burst
            elif rng.random() < pkt_rate:
                npk = 1
            for _ in range(npk):
                d = rng.randrange(N - 1)
                if d >= s:
                    d += 1
                links = mesh.xy_path_links(s, d)
                pkt_info[next_id] = dict(links=links, t=t, left=m_b)
                # up-ramp serializes at 1 flit/cycle (ramp_bw=1)
                start = max(t, ramp_free[s])
                for fi in range(m_b):
                    inflight.append([start + fi + mesh.ramp, 0, next_id, fi])
                ramp_free[s] = start + m_b
                next_id += 1

        # wire/pipe arrivals -> enqueue
        still = []
        for rec in inflight:
            if rec[0] <= t:
                arr, hop, pid, fi = rec
                links = pkt_info[pid]["links"]
                if hop < len(links):
                    queue[links[hop]].append((pid, fi, hop))
                else:  # ejected at dest ramp
                    pkt_info[pid]["left"] -= 1
                    if pkt_info[pid]["left"] == 0:
                        delays.append(t - pkt_info[pid]["t"])
                        del pkt_info[pid]
            else:
                still.append(rec)
        inflight = still

        # service: each link serves 1 flit unless calendar-busy
        for l, q in queue.items():
            if maxq[l] < len(q):
                maxq[l] = len(q)
            if cal_busy(l, t):
                continue  # HP calendar owns the link this cycle
            if q:
                pid, fi, hop = q.popleft()
                w = mesh.wire_delay(l)
                inflight.append([t + w, hop + 1, pid, fi])

    return delays, (max(maxq.values()) if maxq else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=200000)
    ap.add_argument("--mb", type=int, default=4)
    ap.add_argument("--m", type=int, default=4)
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args()

    mesh = Mesh(6, 8)
    points = [
        # (kind, f_c, lam, design, bg burst)
        ("broadcast", 0.2, 0.10, "CAL_SOFT", 1),
        ("gather", 0.2, 0.10, "CAL_SOFT", 1),
        ("allgather", 0.2, 0.10, "CAL_SOFT", 1),
        ("alltoall", 0.2, 0.10, "CAL_SOFT", 1),
        ("alltoall", 0.4, 0.20, "CAL_SOFT", 1),
        ("gather", 0.05, 0.30, "CAL_SOFT", 1),
        # shaped-calendar variants
        ("alltoall", 0.4, 0.20, "CAL_SHAPED", 1),
        ("gather", 0.2, 0.10, "CAL_SHAPED", 1),
        # adversarial aligned-burst BG (tightness probe)
        ("alltoall", 0.4, 0.20, "CAL_SOFT", 8),
        ("gather", 0.2, 0.30, "CAL_SOFT", 8),
    ]
    out = []
    print(f"{'kind':10s} {'f_c':>4} {'lam':>5} {'design':>10s} {'bst':>3} | "
          f"{'sim p99':>8} {'sim p100':>8} "
          f"{'NC delay':>9} | {'sim maxQ':>8} {'NC buf':>7} | verdict")
    for kind, f_c, lam, design, burst in points:
        mk, cload, _ = makespan_bound(mesh, kind, 6, args.m)
        Lmax = max(cload.values())
        period = max(Lmax / f_c, mk)
        r = analyze(mesh, kind, 6, args.m, period, lam, args.mb, design)
        alld, allq = [], 0
        for s in range(args.seeds):
            d, q = run_des(mesh, cload, int(round(period)), lam, args.mb,
                           args.cycles, seed=s + 1,
                           shaped_m=(args.m if design == "CAL_SHAPED" else 0),
                           burst=burst)
            alld += d
            allq = max(allq, q)
        alld.sort()
        p99 = alld[int(0.99 * len(alld))]
        p100 = alld[-1]
        ok = (p100 <= r["bg_delay"]) and (allq <= r["max_port_buf"])
        print(f"{kind:10s} {f_c:4.2f} {lam:5.2f} {design:>10s} {burst:3d} | "
              f"{p99:8d} {p100:8d} "
              f"{r['bg_delay']:9.0f} | {allq:8d} {r['max_port_buf']:7.0f} | "
              f"{'OK' if ok else 'PIERCED'}")
        out.append(dict(kind=kind, f_c=f_c, lam=lam, design=design,
                        burst=burst, sim_p99=p99,
                        sim_p100=p100, nc_delay=r["bg_delay"],
                        sim_maxq=allq, nc_buf=r["max_port_buf"], ok=ok,
                        n_pkts=len(alld)))
    Path(ROOT / "results" / "nc_validate_des.json").write_text(
        json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
