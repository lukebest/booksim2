#!/usr/bin/env python3
"""Loop tick 3: push replayable-pipeline II toward the link lower bound.

1) Randomized-restart modular (cyclic) packing for the II-relevant schemes.
2) New tree: pair-snake comb (col_comb3 layout, column pairs filled as one
   snake so the return column uses opposite-direction links).
Updates results/tree_m1_uarch_dse.json in place.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict

import dse_m1_tree_uarch as M
from dse_m1_tree_uarch import (
    MX, MY, H, V, N, RAMP, RAMP_BW, OUT, coord, nid,
)
from dse_tree_allgather_6x8 import footprint, validate_tree
import sched_zerobuf_compare as S


def pair_snake_comb(s: int) -> list[tuple[int, int]]:
    """col_comb3 layout with column-pair snake fill: source column both ways,
    nearest boundary row spine; adjacent column pairs are filled by one branch
    that runs inward along the first column, crosses at the far row, and
    returns down the second column (reverse-direction vertical links)."""
    sx, sy = coord(s)
    edge = 0 if sy <= (MY - 1) // 2 else MY - 1
    step = 1 if edge == 0 else -1
    far = MY - 1 - edge
    e: list[tuple[int, int]] = []
    for y in range(sy - 1, -1, -1):
        e.append((nid(sx, y + 1), nid(sx, y)))
    for y in range(sy + 1, MY):
        e.append((nid(sx, y - 1), nid(sx, y)))
    for x in range(sx - 1, -1, -1):
        e.append((nid(x + 1, edge), nid(x, edge)))
    for x in range(sx + 1, MX):
        e.append((nid(x - 1, edge), nid(x, edge)))
    cols = [x for x in range(MX) if x != sx]
    i = 0
    while i < len(cols):
        if i + 1 < len(cols) and cols[i + 1] == cols[i] + 1:
            x1, x2 = cols[i], cols[i + 1]
            i += 2
            y = edge
            while y != far:
                e.append((nid(x1, y), nid(x1, y + step)))
                y += step
            e.append((nid(x1, far), nid(x2, far)))
            y = far
            while y != edge + step:
                e.append((nid(x2, y), nid(x2, y - step)))
                y -= step
        else:
            x = cols[i]
            i += 1
            y = edge
            while y != far:
                e.append((nid(x, y), nid(x, y + step)))
                y += step
    return e


def cyclic_pack_rand(footprints, ii, buffer_depth, order, rng):
    link_res = defaultdict(set)
    up_fold = defaultdict(lambda: [0] * ii)
    down_fold = defaultdict(lambda: [0] * ii)
    offs = {}
    for s in order:
        slots = footprints[s]
        feasible = []
        for o in range(ii):
            ok = True
            for kind, key, rel in slots:
                r = (o + rel) % ii
                if kind == "L" and r in link_res[key]:
                    ok = False
                    break
                if kind == "U" and up_fold[key][r] >= RAMP_BW:
                    ok = False
                    break
            if not ok:
                continue
            for kind, key, rel in slots:
                if kind != "D":
                    continue
                fold = down_fold[key][:]
                fold[(o + rel) % ii] += 1
                if M._queue_peak(fold) > buffer_depth:
                    ok = False
                    break
            if ok:
                feasible.append(o)
        if not feasible:
            return None
        chosen = rng.choice(feasible) if rng else feasible[0]
        offs[s] = chosen
        for kind, key, rel in slots:
            r = (chosen + rel) % ii
            if kind == "L":
                link_res[key].add(r)
            elif kind == "U":
                up_fold[key][r] += 1
            else:
                down_fold[key][r] += 1
    mk = max(offs[s] + rel + RAMP
             for s in offs for kind, _, rel in footprints[s] if kind == "D")
    return mk, offs


def optimize_ii(footprints, start_ii, buffer_depth, restarts=40, seed=1):
    """Descend from a known-feasible II using randomized restarts."""
    rng = random.Random(seed)
    corner = sorted(range(N), key=lambda s: min(
        coord(s)[0] * H + coord(s)[1] * V,
        (MX - 1 - coord(s)[0]) * H + coord(s)[1] * V,
        coord(s)[0] * H + (MY - 1 - coord(s)[1]) * V,
        (MX - 1 - coord(s)[0]) * H + (MY - 1 - coord(s)[1]) * V),
        reverse=True)
    best = None
    ii = start_ii
    while ii >= (N - 1 + RAMP_BW - 1) // RAMP_BW:
        found = None
        res = cyclic_pack_rand(footprints, ii, buffer_depth, corner, None)
        if res is not None:
            found = res
        else:
            for _ in range(restarts):
                order = corner[:]
                rng.shuffle(order)
                res = cyclic_pack_rand(footprints, ii, buffer_depth,
                                       order, rng)
                if res is not None:
                    found = res
                    break
        if found is None:
            break
        best = {"ii": ii, "first_round_mk": found[0]}
        ii -= 1
    return best


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    data = json.loads(OUT.read_text(encoding="utf-8"))
    schemes = data["schemes"]

    print("evaluating pair_snake_comb ...", flush=True)
    rec = M.evaluate("pair_snake_comb", pair_snake_comb)
    schemes["pair_snake_comb"] = rec
    print(f"pair_snake_comb mk={rec['makespan']} "
          f"IIlb={rec['steady_ii_by_buffer']['link_lb']} "
          f"cyc={ {b: (v and v['ii']) for b, v in rec['cyclic_pack_ii'].items()} }",
          flush=True)

    targets = ["col_comb3", "nec3", "nec2", "hamilton_bi_tree", "axis_ccw",
               "pair_snake_comb"]
    builders = {**M.SCHEME_SET, "pair_snake_comb": pair_snake_comb}
    for name in targets:
        fps = {}
        for s in range(N):
            edges = builders[name](s)
            chk = validate_tree(s, edges)
            fps[s] = footprint(s, edges, chk)
        opt = {}
        for b in (0, 4):
            base = schemes[name]["cyclic_pack_ii"].get(str(b))
            start = base["ii"] if base else 6 * schemes[name][
                "steady_ii_by_buffer"]["link_lb"]
            opt[str(b)] = optimize_ii(fps, start, b)
        schemes[name]["cyclic_pack_ii_opt"] = opt
        print(f"{name:18s} opt_II b0={opt['0'] and opt['0']['ii']} "
              f"(rd1 {opt['0'] and opt['0']['first_round_mk']}) "
              f"b4={opt['4'] and opt['4']['ii']} "
              f"(rd1 {opt['4'] and opt['4']['first_round_mk']})", flush=True)

    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
