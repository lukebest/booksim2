#!/usr/bin/env python3
"""Hybrid allgather: local Hamilton rings + a global tree broadcast.

Idea (hierarchical / two-phase allgather on the 12x16 mesh):

  Phase A (local Hamilton ring):  partition the mesh into B horizontal bands of
    R = MY/B rows each. Inside every band (an R x MX sub-mesh) run a Hamilton
    ring allgather, all bands in parallel on disjoint links/ramps. Afterwards
    every node holds the full block of its band (n_b = R*MX messages).

  Phase B (global tree broadcast):  the B band-blocks are now replicated inside
    each band, so they only need to be exchanged ACROSS bands along the vertical
    axis. We model this as an allgather among B band-super-nodes spaced R rows
    apart (inter-band hop latency R*V), forwarded in-network both ways (a global
    broadcast tree on the vertical line). Every node ends with all N messages.

This interpolates between the two reference schemes:
  * B = 1  -> one global Hamilton ring        (== pure ring allgather)
  * B = MY -> 1-row local rings + column tree  (ring rows + tree columns)

Makespan = makespan(A) + makespan(B): the phases are dependent (a band cannot
broadcast its block until its local ring allgather has finished), so they run
sequentially. The two phases together still eject exactly (N-1) flits/node
((n_b-1) local + (B-1)*n_b global), so the down-ramp floor (N-1)/ramp_bw is
split across the phases; the hybrid pays both phases' latencies but each local
ring is short.

Assumptions (stated for fairness): inter-band hop = R*V; once a band block
reaches a band, its intra-band re-spread is overlapped/ignored (optimistic by a
small local-tree term); phase B reuses links after phase A (sequential).
"""

import heapq
from collections import defaultdict

import hamilton_ring as hr
import sim_hamilton_ring as sr

MX, MY, H, V, RAMP = 12, 16, 4, 8, 1
N = MX * MY


def path_allgather(num, hop, msg_size, ramp, ramp_bw):
    """Allgather among `num` nodes on a line (positions 0..num-1), uniform hop
    latency, in-network bidirectional forwarding (a linear broadcast tree).
    Returns (makespan, eject_ok). num<=1 -> 0."""
    if num <= 1:
        return 0, True
    chains = {}
    for i in range(num):
        cs = []
        fwd = list(range(i, num))
        bwd = list(range(i, -1, -1))
        if len(fwd) > 1:
            cs.append(fwd)
        if len(bwd) > 1:
            cs.append(bwd)
        chains[i] = cs

    link = sr.Calendar(1)
    up = sr.Calendar(ramp_bw)
    down = sr.Calendar(ramp_bw)
    pq = []
    seq = 0
    for src, cs in chains.items():
        for ci, ch in enumerate(cs):
            for k in range(msg_size):
                inj = up.reserve(src, k)
                heapq.heappush(pq, (inj + ramp, seq, src, ci, 0, k))
                seq += 1

    makespan = 0
    ej = defaultdict(int)
    while pq:
        ready, _, src, ci, idx, k = heapq.heappop(pq)
        ch = chains[src][ci]
        p, c = ch[idx], ch[idx + 1]
        send = link.reserve((p, c), ready)
        arrive = send + hop
        e = down.reserve(c, arrive)
        makespan = max(makespan, e + ramp)
        ej[c] += 1
        if idx + 2 < len(ch):
            heapq.heappush(pq, (arrive, seq, src, ci, idx + 1, k))
            seq += 1

    exp = (num - 1) * msg_size
    ok = all(ej[i] == exp for i in range(num))
    return makespan, ok


def band_ring(mx, R):
    """Return (order, is_cycle) for a band's local Hamilton ring (R x mx)."""
    if R == 1:
        return list(range(mx)), False          # single row -> open path
    return hr.snake_cycle(mx, R), True          # R even -> closed cycle


def hybrid(B, mode, mx=MX, my=MY, h=H, vlat=V, ramp=RAMP):
    """One hybrid configuration with B horizontal bands.

    Returns a dict with phase makespans and the total, or feasible=False when
    the local ring is impossible for this mode (a 1-row band has no cycle, so
    the unidirectional ring is undefined there).
    """
    if my % B != 0:
        return {"feasible": False, "reason": f"B={B} does not divide MY={my}"}
    R = my // B
    n_b = R * mx
    ramp_bw = 1 if mode == "uni" else 2

    order, is_cycle = band_ring(mx, R)
    if mode == "uni" and not is_cycle:
        return {"feasible": False, "B": B, "R": R,
                "reason": "uni needs a cycle; a 1-row band is only a path"}

    a = sr.simulate(order, is_cycle, mode, mx=mx, my=R, h=h, vlat=vlat,
                    ramp=ramp, ramp_bw=ramp_bw, msg_size=1)
    ms_a = a["makespan"]

    # Phase B: allgather of B band blocks (each n_b flits) along the vertical
    # axis, adjacent bands R*V apart.
    ms_b, ok_b = path_allgather(B, hop=R * vlat, msg_size=n_b, ramp=ramp,
                                ramp_bw=ramp_bw)

    return {
        "feasible": True, "mode": mode, "B": B, "R": R, "n_b": n_b,
        "ramp_bw": ramp_bw,
        "phaseA_local_ring": ms_a, "phaseA_eject_ok": a["eject_ok"],
        "phaseB_global_tree": ms_b, "phaseB_eject_ok": ok_b,
        "makespan": ms_a + ms_b,
    }


def sweep(mode, bands=(1, 2, 4, 8, 16)):
    return [hybrid(B, mode) for B in bands]


def reference_makespans():
    """Pure-ring and dimensional multi-tree references (M=1)."""
    g = hr.snake_cycle(MX, MY)
    ring_uni = sr.simulate(g, True, "uni")["makespan"]
    ring_bi = sr.simulate(g, True, "bi")["makespan"]
    try:
        import sim_dim_multitree as smt
        multitree = smt.simulate(1, verbose=False)
    except Exception:
        multitree = 205
    return {"ring_uni": ring_uni, "ring_bi": ring_bi, "multitree": multitree}


def main():
    ref = reference_makespans()
    print("Reference (M=1):")
    print(f"  dimensional multi-tree : {ref['multitree']}")
    print(f"  pure Hamilton ring uni : {ref['ring_uni']}")
    print(f"  pure Hamilton ring bi  : {ref['ring_bi']}")
    for mode in ("uni", "bi"):
        print(f"\nHybrid (local ring + global tree), mode={mode}:")
        print("  B   R   n_b   phaseA  phaseB  total")
        for r in sweep(mode):
            if not r["feasible"]:
                print(f"  {r.get('B','?'):<3} -- {r['reason']}")
                continue
            print(f"  {r['B']:<3} {r['R']:<3} {r['n_b']:<5} "
                  f"{r['phaseA_local_ring']:<7} {r['phaseB_global_tree']:<7} "
                  f"{r['makespan']}")


if __name__ == "__main__":
    main()
