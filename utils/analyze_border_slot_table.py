#!/usr/bin/env python3
"""Per-router slot-table depth analysis for the border short-arc AFIFO<=5 scheme.

"Slot-table depth" of a router = minimum period P of its per-cycle crossbar
connection pattern over the E/W/S/N (+Local) ports.  We rebuild the exact
`border_d5` schedule used in results/dataflow_zerobuf.html, then for every
router and cycle compute the SET of (in_dir -> out_dir) connections active that
cycle, and look for the smallest P that makes the (non-empty) pattern periodic.
"""
from collections import defaultdict

import sim_fused_rings as fr
import sched_ring_zerobuf as S
from sweep_quad_ring_shapes import make_quads

MX = MY = 16
BORDER_BI_CFG = (("vflip", 1), ("rect", 1), ("rect", 3), ("vflip", 3))


def dir_of(p, c):
    """Direction (E/W/S/N) of the link p->c as seen leaving router p."""
    px, py = p % MX, p // MX
    cx, cy = c % MX, c // MX
    if cx == px + 1:
        return "E"
    if cx == px - 1:
        return "W"
    if cy == py + 1:
        return "S"
    if cy == py - 1:
        return "N"
    return f"?({cx-px},{cy-py})"


def build_events():
    fr.cfg(MX, MY, 4, 6, cross=fr.CROSS_LAT)
    quads = make_quads(BORDER_BI_CFG)
    deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
    r = S.schedule_atomic(MX, True, 2, deliv, afifo_cap=5, order="natural",
                          record_events=True, quads=quads)
    return r


def main():
    r = build_events()
    ev = r["events"]
    mk = r["makespan"]
    print(f"border_d5: makespan={mk}  AFIFO_link={r['afifo_depth']}  "
          f"AFIFO_bal={r['afifo_balanced']['peak']}  ok={r['ok']}  n_ev={len(ev)}")

    # index arrivals: (s, node) -> arrive cycle  (to recover input port)
    arrive = {}
    for (s, p, c, t, lat, arr, kind) in ev:
        arrive[(s, c)] = arr

    # per (router, cycle) -> set of (in_dir, out_dir) connections (topology only)
    conn = defaultdict(set)               # (p, t) -> set of (indir, outdir)
    # also track multiplicity / source-agnostic
    for (s, p, c, t, lat, arr, kind) in ev:
        out_d = dir_of(p, c)
        # input port at p for this source's flit (flit departs p at cycle t)
        a_in = arrive.get((s, p))
        if a_in is None:
            in_d = "L"                    # p is the source -> local inject
        else:
            # find the parent hop (x, p) for source s whose arr == a_in
            in_d = None
            for (s2, p2, c2, t2, lat2, arr2, k2) in ev:
                if s2 == s and c2 == p and arr2 == a_in:
                    in_d = {"E": "W", "W": "E", "S": "N", "N": "S"}.get(dir_of(p2, p), "?")
                    break
            if in_d is None:
                in_d = "L"
        conn[(p, t)].add((in_d, out_d))

    # per router: time series of frozenset configs
    series = {p: [frozenset() for _ in range(mk + 1)] for p in range(MX * MY)}
    for (p, t), cset in conn.items():
        if 0 <= t <= mk:
            series[p][t] = frozenset(cset)

    def min_period(seq):
        """Smallest P>0 s.t. seq[i]==seq[i+P] for all i in the active window.
        Active window = [first nonempty, last nonempty]."""
        idx = [i for i, x in enumerate(seq) if x]
        if not idx:
            return 0, 0, 0
        lo, hi = idx[0], idx[-1]
        win = seq[lo:hi + 1]
        L = len(win)
        for P in range(1, L + 1):
            if all(win[i] == win[i + P] for i in range(L - P)):
                return P, L, len(set(win))
        return L, L, len(set(win))

    rows = []
    for p in range(MX * MY):
        P, span, ndist = min_period(series[p])
        rows.append((p, P, span, ndist))

    # summary
    nonzero = [row for row in rows if row[2] > 0]
    print(f"\nrouters with any traffic: {len(nonzero)}/{MX*MY}")
    import statistics as st
    periods = [row[1] for row in nonzero]
    spans = [row[2] for row in nonzero]
    ndists = [row[3] for row in nonzero]
    print(f"min-period P:   min={min(periods)} max={max(periods)} "
          f"mean={st.mean(periods):.1f} median={st.median(periods)}")
    print(f"active span:    min={min(spans)} max={max(spans)} "
          f"mean={st.mean(spans):.1f}")
    print(f"distinct configs: min={min(ndists)} max={max(ndists)} "
          f"mean={st.mean(ndists):.1f}")

    # how many routers are truly periodic (P < span)?
    periodic = [row for row in nonzero if row[1] < row[2]]
    print(f"routers with P < span (genuinely periodic): {len(periodic)}")

    # distribution of distinct-config counts
    from collections import Counter
    dist = Counter(ndists)
    print("\ndistinct-config-count histogram (n_configs: n_routers):")
    for k in sorted(dist):
        print(f"  {k:3d} configs : {dist[k]:3d} routers")

    # show the 4 corner / center sample routers
    print("\nsample routers (id (x,y)): min_period span distinct_configs")
    for (x, y) in [(0, 0), (7, 7), (8, 8), (15, 15), (7, 0), (8, 0), (0, 7), (0, 8)]:
        p = fr.nid(x, y)
        P, span, nd = min_period(series[p])
        print(f"  id={p:3d} ({x:2d},{y:2d}) : P={P:3d} span={span:3d} distinct={nd}")


if __name__ == "__main__":
    main()
