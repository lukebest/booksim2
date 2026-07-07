#!/usr/bin/env python3
"""Lower-bound analysis for 0-buffer allgather on a 2D mesh.

Physical model (matches sched_zerobuf_compare.py):
  * directed mesh link:  <= 1 flit/cycle
  * down-ramp (PE eject): <= ramp_bw flit/cycle/node
  * horizontal hop (same row) latency H cycles, vertical hop (same column)
    latency V cycles, plus a fixed RAMP=1 cycle down-ramp latency
  * message size m flit/node; every node must receive m flits from every
    other node -> (N-1)*m flits ejected per node over the run.

Four independent lower bounds on the makespan T; the true optimum is
>= max of all four (a *necessary* condition, not always achievable):

  1. eject_lb    : down-ramp throughput.  ceil((N-1)*m / ramp_bw)
  2. corner_lb   : a mesh corner has only 2 incoming physical links (1 H + 1 V)
                   through which ALL other nodes' data must arrive before it
                   can even be forwarded to the corner's own down-ramp.
                   ceil((N-1)*m / 2), independent of ramp_bw.
  3. latency_lb  : the farthest source-destination pair's physical delivery
                   time (dimension-routed hop latency) plus (m-1) cycles to
                   serialize an m-flit message over its last link, plus the
                   fixed down-ramp RAMP latency.
  4. bisect_lb   : any straight mesh cut separates the N nodes into two halves;
                   every flit originating on one side that a node on the other
                   side needs crosses the cut link set exactly once (mesh
                   routing is monotonic), and a source's m-flit message only
                   needs to cross ONCE (then fans out locally via in-network
                   multicast) -> ceil((N/2)*m / #cut-links). Evaluated for
                   both the vertical cut (#links = MY) and horizontal cut
                   (#links = MX); we report the max of the two.

T = max(eject_lb, corner_lb, latency_lb, bisect_lb).

Outputs results/allgather_lb.json: per (mesh, ramp_bw, m) all four bounds + T.
"""

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "allgather_lb.json"

H_DEFAULT, V_DEFAULT, RAMP = 4, 6, 1

SIZES = [(4, 4), (6, 8), (8, 8), (12, 16), (16, 16), (32, 32), (64, 64)]
FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]


def eject_lb(n, m, ramp_bw):
    return math.ceil((n - 1) * m / ramp_bw)


def corner_lb(n, m):
    """Corner node: exactly 2 incoming mesh links (independent of ramp_bw)."""
    return math.ceil((n - 1) * m / 2)


def latency_lb(mx, my, h, v, m):
    max_dist = (mx - 1) * h + (my - 1) * v
    return RAMP + max_dist + (m - 1) + RAMP


def bisect_lb(mx, my, m):
    """Max of vertical-cut and horizontal-cut crossing bounds."""
    n = mx * my
    vcut = math.ceil((n // 2) * m / my)   # my parallel links cross a vertical cut
    hcut = math.ceil((n // 2) * m / mx)   # mx parallel links cross a horizontal cut
    return max(vcut, hcut)


def bounds_for(mx, my, h, v, m, ramp_bw):
    n = mx * my
    b_eject = eject_lb(n, m, ramp_bw)
    b_corner = corner_lb(n, m)
    b_lat = latency_lb(mx, my, h, v, m)
    b_bisect = bisect_lb(mx, my, m)
    t = max(b_eject, b_corner, b_lat, b_bisect)
    binding = [name for name, val in (
        ("eject", b_eject), ("corner", b_corner),
        ("latency", b_lat), ("bisect", b_bisect)) if val == t]
    return {
        "eject_lb": b_eject,
        "corner_lb": b_corner,
        "latency_lb": b_lat,
        "bisect_lb": b_bisect,
        "T": t,
        "binding": binding,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--h", type=int, default=H_DEFAULT)
    ap.add_argument("--v", type=int, default=V_DEFAULT)
    ap.add_argument("--json", default=str(JSON_PATH))
    args = ap.parse_args()

    payload = {
        "h": args.h, "v": args.v, "ramp": RAMP,
        "sizes": [f"{mx}x{my}" for mx, my in SIZES],
        "flits": FLITS, "ramp_bws": RAMP_BWS,
        "data": {},
    }
    for mx, my in SIZES:
        key = f"{mx}x{my}"
        payload["data"][key] = {"mx": mx, "my": my, "n": mx * my, "bw": {}}
        for rb in RAMP_BWS:
            payload["data"][key]["bw"][rb] = {}
            for m in FLITS:
                payload["data"][key]["bw"][rb][m] = bounds_for(mx, my, args.h, args.v, m, rb)

    print(f"{'mesh':>8} {'bw':>3} {'m':>2} {'eject':>7} {'corner':>7} "
          f"{'latency':>8} {'bisect':>7} {'T':>7}  binding")
    for mx, my in SIZES:
        key = f"{mx}x{my}"
        for rb in RAMP_BWS:
            for m in FLITS:
                d = payload["data"][key]["bw"][rb][m]
                print(f"{key:>8} {rb:>3} {m:>2} {d['eject_lb']:>7} {d['corner_lb']:>7} "
                      f"{d['latency_lb']:>8} {d['bisect_lb']:>7} {d['T']:>7}  {'+'.join(d['binding'])}")

    out_path = Path(args.json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
