#!/usr/bin/env python3
"""Export a FULL per-link send trace (every directed mesh link, not just
cross-reticle ones) for the 4x4 allgather study, in a simple plain-text format
that the SystemC testbench (sc/mesh_tb.cpp) can parse without a JSON library.

Two schemes, same golden config as the 16x16 study but scaled to 4x4 (reticle =
quadrant = 2x2, boundary at col/row 1|2):
  ring     : global Hamilton bidirectional ring (sim_hamilton_ring.simulate)
  hybrid_v : hybrid B=2 vertical-band ring + horizontal tree, 0-buffer rigid
             packer (sched_zerobuf_compare.fp_hybrid_v + export_events)

Why the FULL link trace (not just cross-reticle): the SystemC testbench derives
each destination node's "downstream free slot" (used by the slot-gated AFIFO
read policy) from how busy that node's own OUTGOING links already are under the
original rigid schedule -- this requires knowing occupancy of every link in the
mesh, not only the ones that cross a reticle boundary.

Output format (plain text, whitespace-separated, '#' starts a comment line):
  MX <int>
  MY <int>
  H <int>
  V <int>
  RAMP <int>
  MAKESPAN <int>
  NEVENTS <int>
  # p c send src           (NEVENTS data lines follow)
  <p> <c> <send> <src>
  ...
"""

import argparse
import os

MX = MY = 4
H, V, RAMP = 4, 6, 1


def write_trace(path, mx, my, h, v, ramp, makespan, events):
    """events: iterable of (p, c, send, src)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"MX {mx}\n")
        f.write(f"MY {my}\n")
        f.write(f"H {h}\n")
        f.write(f"V {v}\n")
        f.write(f"RAMP {ramp}\n")
        f.write(f"MAKESPAN {makespan}\n")
        ev = sorted(events, key=lambda e: (e[2], e[0], e[1]))
        f.write(f"NEVENTS {len(ev)}\n")
        f.write("# p c send src\n")
        for p, c, send, src in ev:
            f.write(f"{p} {c} {send} {src}\n")


def export_ring(mx=MX, my=MY, h=H, v=V, ramp=RAMP, msg_size=1):
    import hamilton_ring as hr
    import sim_hamilton_ring as R
    order = hr.snake_cycle(mx, my)
    r = R.simulate(order, True, "bi", mx=mx, my=my, h=h, vlat=v, ramp=ramp,
                   msg_size=msg_size, collect=True)
    events = [(p, c, send, src) for src, p, c, send, _arr in r["edges"]]
    return r["makespan"], events


def export_hybrid_v(mx=MX, my=MY, h=H, v=V, B=2):
    import sched_zerobuf_compare as S
    S.cfg(mx, my, h, v)
    S.init_ring()
    S.init_quadrants()
    build = lambda s: S.fp_hybrid_v(s, B, True, 1)
    mk, _mo, order_name, ok = S.run_scheme(build, 1)
    if not ok:
        raise RuntimeError(f"hybrid_v B={B} packer infeasible at {mx}x{my}")
    src_order = S.SRC_ORDERS[order_name]()
    foot = {s: build(s) for s in range(S.N)}
    mk2, _mo2, _busy, _inj, ev = S.export_events(foot, 1, src_order, flits=1)
    events = [(p, c, send, src) for src, p, c, send, _lat, _arr, _kind in ev]
    return mk, events


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int, default=MX)
    ap.add_argument("--my", type=int, default=MY)
    ap.add_argument("--h", type=int, default=H)
    ap.add_argument("--v", type=int, default=V)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = args.out_dir or os.path.normpath(os.path.join(here, "..", "results"))
    os.makedirs(out_dir, exist_ok=True)

    mk_r, ev_r = export_ring(args.mx, args.my, args.h, args.v)
    p1 = os.path.join(out_dir, f"sc_trace_ring_{args.mx}x{args.my}.trace")
    write_trace(p1, args.mx, args.my, args.h, args.v, RAMP, mk_r, ev_r)
    print(f"ring     : makespan={mk_r:4d}  events={len(ev_r):4d}  -> {p1}")

    mk_h, ev_h = export_hybrid_v(args.mx, args.my, args.h, args.v)
    p2 = os.path.join(out_dir, f"sc_trace_hybrid_{args.mx}x{args.my}.trace")
    write_trace(p2, args.mx, args.my, args.h, args.v, RAMP, mk_h, ev_h)
    print(f"hybrid_v : makespan={mk_h:4d}  events={len(ev_h):4d}  -> {p2}")


if __name__ == "__main__":
    main()
