#!/usr/bin/env python3
"""Compare ring / multi-ring allgather against the dimensional multi-tree.

Pure analytic timing model (orders of magnitude + exact latencies), used to
answer: can k time-multiplexed local Hamiltonian rings beat the multi-tree?

Key quantities per mesh:
  - eject bandwidth floor:  (N-1)*M + ramp   (every node ejects N-1 flits @1/cy)
  - broadcast fill:         farthest dim-ordered path latency + ramps
  - single snake ring:      ramp + circumference (last block loops the whole ring)
  - k row-band rings:        intra-band ring makespan (then inter-band phase adds on)
"""

import argparse


def analyze(mx, my, h, vv, ramp, M=1):
    n = mx * my
    floor = (n - 1) * M + ramp
    # broadcast fill: corner-to-corner dimension-ordered latency
    fill = (mx - 1) * h + (my - 1) * vv + 2 * ramp

    # snake Hamiltonian path circumference (open ring): traverse every row fully
    # in H, transition rows in V. H-hops = my*(mx-1), V-hops between rows = my-1.
    h_hops = my * (mx - 1)
    v_hops = my - 1
    circ = h_hops * h + v_hops * vv
    single_ring = ramp + circ + ramp  # last block must loop the whole path

    # k=4 row-band local rings: 4 bands of (my/4) rows x mx cols, each a snake ring
    out = {
        "N": n,
        "eject_floor": floor,
        "broadcast_fill": fill,
        "snake_circumference": circ,
        "single_ring_makespan": single_ring,
    }
    for k in (4,):
        if my % k:
            continue
        rb = my // k                       # rows per band
        bh = rb * (mx - 1)                  # H-hops in one band snake
        bv = rb - 1                         # V-hops in one band snake
        band_circ = bh * h + bv * vv
        intra = ramp + band_circ + ramp    # intra-band allgather (band's N/k blocks)
        # inter-band: 4 bands stacked vertically; aggregated blocks must cross
        # (k-1) band boundaries along columns -> a vertical ring over k bands.
        # boundary traversal latency (one band height) ~ rb*vv; ring over k bands
        inter_fill = (k - 1) * rb * vv + 2 * ramp
        hier = intra + inter_fill
        out[f"{k}ring_intra"] = intra
        out[f"{k}ring_hier_estimate"] = hier
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ramp", type=int, default=1)
    args = ap.parse_args()
    cases = [(6, 8, 4, 8, 78, None), (12, 16, 4, 8, 205, 330)]
    for mx, my, h, vv, tree_opt, tree_zb in cases:
        r = analyze(mx, my, h, vv, args.ramp)
        print(f"\n=== mesh {mx}x{my} (N={r['N']}) ===")
        print(f"  eject bandwidth FLOOR (any scheme)   : {r['eject_floor']}")
        print(f"  broadcast fill (1 source -> all)     : {r['broadcast_fill']}")
        print(f"  -- dimensional multi-tree --")
        print(f"  buffer-rich makespan                 : {tree_opt}")
        if tree_zb:
            print(f"  zero-eject-buffer (W=8) makespan     : {tree_zb}")
        print(f"  -- ring family --")
        print(f"  snake ring circumference             : {r['snake_circumference']}")
        print(f"  single global ring makespan          : {r['single_ring_makespan']}")
        print(f"  4 row-band rings: intra-band          : {r['4ring_intra']}")
        print(f"  4 row-band rings: + inter-band (est)  : {r['4ring_hier_estimate']}")


if __name__ == "__main__":
    main()
