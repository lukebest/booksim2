#!/usr/bin/env python3
"""Theoretical lower bounds for allreduce on a 2D mesh (default 16x16).

Model: H/V per-hop latency, ramp=1, optional reduce latency R_LAT at merge
points, pipelined M flits per node.  Bounds are parameterised over M and R_LAT.
"""

from __future__ import annotations

MX, MY, H, V, RAMP = 16, 16, 4, 6, 1
N = MX * MY
DEFAULT_ROOT = 8 + 8 * MX  # mesh centre (8,8)
R_LAT_DEFAULT = 2


def cfg(mx=MX, my=MY, h=H, v=V, ramp=RAMP):
    global MX, MY, H, V, RAMP, N
    MX, MY, H, V, RAMP = mx, my, h, v, ramp
    N = mx * my


def nid(x, y):
    return x + MX * y


def coord(n):
    return n % MX, n // MX


def manh(a, b):
    ax, ay = coord(a)
    bx, by = coord(b)
    return abs(ax - bx) * H + abs(ay - by) * V


def hop_count(a, b):
    ax, ay = coord(a)
    bx, by = coord(b)
    return abs(ax - bx) + abs(ay - by)


def mesh_diameter():
    return (MX - 1) * H + (MY - 1) * V


def bisection_links():
    return min(MX, MY)


def bisection_capacity():
    return max((MX // 2) * V, (MY // 2) * H)


def tree_phase_latency(root=DEFAULT_ROOT, r_lat=R_LAT_DEFAULT):
    """Single reduce or broadcast tree phase (latency-dominated, M=1)."""
    max_path = max(manh(n, root) for n in range(N))
    max_hops = max(hop_count(n, root) for n in range(N))
    reduce_ops = max(0, max_hops - 1)
    return max_path + 2 * RAMP + reduce_ops * r_lat


def bound_tree_latency(M: int, r_lat: int = R_LAT_DEFAULT, root=DEFAULT_ROOT) -> int:
    """Sequential reduce phase + broadcast phase (M pipelined flits)."""
    alive = range(N)
    red = 0
    for s in alive:
        if s == root:
            red = max(red, RAMP + r_lat)
        else:
            hops = hop_count(s, root)
            red = max(red, RAMP + manh(s, root) + max(0, hops - 1) * r_lat + r_lat)
    red += M - 1
    bcast = RAMP + max(manh(root, n) for n in alive) + RAMP
    return red + 1 + bcast + M - 1


def bound_downramp_rsag(M: int, ramp_bw: int = 1) -> int:
    """RS+AG view: each node must send and receive ~2 flits per step over ramp."""
    if ramp_bw < 1:
        ramp_bw = 1
    rs = ((N - 1) * M + ramp_bw - 1) // ramp_bw
    ag = ((N - 1) * M + ramp_bw - 1) // ramp_bw
    ring_lat = mesh_diameter()
    return rs + ag + ring_lat


def bound_downramp_final(M: int, ramp_bw: int = 1) -> int:
    """Each node ingests M reduced flits on its down-ramp."""
    if ramp_bw < 1:
        ramp_bw = 1
    return (M + ramp_bw - 1) // ramp_bw + mesh_diameter()


def bound_bisection(M: int) -> int:
    """At least half the aggregate data crosses a minimum bisection cut."""
    links = bisection_links()
    return ((N // 2) * M + links - 1) // links


def bound_diameter_pair(r_lat: int = R_LAT_DEFAULT) -> int:
    """Farthest pair must exchange reduced information (meet-in-the-middle)."""
    d = mesh_diameter()
    hops = (MX - 1) + (MY - 1)
    return 2 * (d // 2 + RAMP) + hops * r_lat


def allreduce_bounds(M: int, r_lat: int = R_LAT_DEFAULT, ramp_bw: int = 1,
                     root=DEFAULT_ROOT) -> dict:
    """Return individual bounds and the combined tight lower bound."""
    b = {
        "M": M,
        "r_lat": r_lat,
        "ramp_bw": ramp_bw,
        "tree_latency": bound_tree_latency(M, r_lat, root),
        "downramp_rsag": bound_downramp_rsag(M, ramp_bw),
        "downramp_final": bound_downramp_final(M, ramp_bw),
        "bisection": bound_bisection(M),
        "diameter_pair": bound_diameter_pair(r_lat) + M - 1,
        "mesh_diameter": mesh_diameter(),
        "tree_phase_m1": tree_phase_latency(root, r_lat),
    }
    b["combined"] = max(
        b["tree_latency"],
        b["downramp_final"],
        b["bisection"],
    )
    b["combined_rsag"] = max(b["downramp_rsag"], b["bisection"], b["diameter_pair"])
    return b


def bound_table(M_values=(1, 2, 3, 4, 5, 6), r_lat=R_LAT_DEFAULT, ramp_bw=1):
    return [allreduce_bounds(M, r_lat, ramp_bw) for M in M_values]


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--r-lat", type=int, default=R_LAT_DEFAULT)
    ap.add_argument("--ramp-bw", type=int, default=1)
    args = ap.parse_args()
    print(f"16x16 allreduce bounds  H={H} V={V} ramp={RAMP} R_LAT={args.r_lat}\n")
    print(f"{'M':>3}  {'tree':>6}  {'rs+ag':>6}  {'dramp':>6}  "
          f"{'bisec':>6}  {'diam':>6}  {'LB':>6}")
    for row in bound_table(r_lat=args.r_lat, ramp_bw=args.ramp_bw):
        print(f"{row['M']:3d}  {row['tree_latency']:6d}  {row['downramp_rsag']:6d}  "
              f"{row['downramp_final']:6d}  {row['bisection']:6d}  "
              f"{row['diameter_pair']:6d}  {row['combined']:6d}")


if __name__ == "__main__":
    main()
