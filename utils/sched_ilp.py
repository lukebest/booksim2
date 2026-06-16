#!/usr/bin/env python3
"""CP-SAT (constraint-programming) scheduler for dimensional multi-tree allgather.

Generates a schedule that is simultaneously:
  - buffer-free at the down-ramp (E=0): eject cycle == arrival cycle,
  - conflict-free: each directed link and each down-ramp carries <=1 flit/cycle,
  - non-blocking: per-hop in-network wait bounded by W (no indefinite hold that
    would trample following flits),
and minimizes makespan.

Formulation (OR-Tools CP-SAT):
  vars  a[s,d] = arrival cycle of source s's flit at node d (d in s's X-then-Y
        fork tree, which spans all nodes). a[s,s] = inj[s] + RAMP, inj[s] >= 0.
  edge  for tree edge p->c:  a[s,p]+lat <= a[s,c] <= a[s,p]+lat+W
        (send cycle = a[s,c]-lat in [a[s,p], a[s,p]+W])
  link  for each physical directed link (p,c): AllDifferent({a[s,c] : s uses pc})
        (distinct send cycles  <=>  distinct arrivals, lat constant per link)
  eject for each node d: AllDifferent({a[s,d] : s != d})   (E=0, 1 eject/cycle)
  obj   minimize  max_{s,d} a[s,d]   (makespan = that + RAMP)

Compares against release-packing LB* (sched_zero_eject_v2.packing_lb).
"""

import argparse
from collections import defaultdict

from ortools.sat.python import cp_model

from sched_no_eject_buffer import coord, nid, link_lat, tree_children
from sched_zero_eject_v2 import packing_lb


def greedy_arrivals(mx, my, h, vv, ramp, wcap):
    """Greedy E=0 + bounded-W schedule; return (makespan, arrivals dict a[(s,d)]).

    Mirrors sched_no_eject_buffer.schedule but records every node's arrival cycle
    so it can warm-start CP-SAT. Far-from-center source order (the good one)."""
    n = mx * my
    trees = {s: tree_children(s, mx, my) for s in range(n)}
    link_busy = defaultdict(set)
    down_busy = defaultdict(set)
    cx0, cy0 = (mx - 1) / 2, (my - 1) / 2
    srcs = sorted(range(n),
                  key=lambda s: -(abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))
    arr = {}
    makespan = 0
    for s in srcs:
        off = 0
        while True:
            tent_l, tent_d, tl, td = [], [], set(), set()
            avail = {s: off + ramp}
            order = [s]; qi = 0; ok = True
            while qi < len(order) and ok:
                p = order[qi]; qi += 1
                for c in trees[s][p]:
                    ready = avail[p]; lk = p * 100000 + c
                    lat = link_lat(p, c, mx, h, vv)
                    t = ready; found = False
                    while wcap is None or t - ready <= wcap:
                        if t not in link_busy[lk] and (lk, t) not in tl:
                            arrive = t + lat
                            if arrive not in down_busy[c] and (c, arrive) not in td:
                                found = True; break
                        t += 1
                    if not found:
                        ok = False; break
                    tent_l.append((lk, t)); tl.add((lk, t))
                    tent_d.append((c, arrive)); td.add((c, arrive))
                    avail[c] = arrive; order.append(c)
            if ok:
                for (lk, t) in tent_l:
                    link_busy[lk].add(t)
                for (d, ej) in tent_d:
                    down_busy[d].add(ej)
                    makespan = max(makespan, ej + ramp)
                for d, av in avail.items():
                    arr[(s, d)] = av
                break
            off += 1
    return makespan, arr


def build_trees(mx, my):
    """Return per-source {parent: [children]} and per-source parent map."""
    n = mx * my
    trees = {}
    parents = {}
    for s in range(n):
        adj = tree_children(s, mx, my)
        trees[s] = adj
        par = {s: None}
        order = [s]; qi = 0
        while qi < len(order):
            p = order[qi]; qi += 1
            for c in adj[p]:
                par[c] = p
                order.append(c)
        parents[s] = par
    return trees, parents


def solve(mx, my, h, vv, ramp, wcap, horizon, time_limit, workers, warmstart=False):
    n = mx * my
    trees, parents = build_trees(mx, my)

    warm_mk, warm_arr = (None, None)
    if warmstart:
        warm_mk, warm_arr = greedy_arrivals(mx, my, h, vv, ramp, wcap)
        # tighten horizon to the greedy makespan -> smaller domains
        horizon = min(horizon, warm_mk)

    model = cp_model.CpModel()
    a = {}
    for s in range(n):
        for d in range(n):
            a[(s, d)] = model.NewIntVar(0, horizon, f"a_{s}_{d}")

    # injection: a[s,s] = inj + ramp, inj >= 0
    for s in range(n):
        model.Add(a[(s, s)] >= ramp)

    # edge causality + bounded per-hop wait
    link_users = defaultdict(list)  # (p,c) -> [a vars] for distinct send cycles
    for s in range(n):
        for p, kids in trees[s].items():
            for c in kids:
                lat = link_lat(p, c, mx, h, vv)
                model.Add(a[(s, c)] >= a[(s, p)] + lat)
                if wcap is not None:
                    model.Add(a[(s, c)] <= a[(s, p)] + lat + wcap)
                link_users[(p, c)].append(a[(s, c)])

    # link conflict-free: distinct send cycles per physical directed link
    for pc, vars_ in link_users.items():
        if len(vars_) > 1:
            model.AddAllDifferent(vars_)

    # down-ramp conflict-free + zero eject buffer: distinct arrivals per node
    for d in range(n):
        grp = [a[(s, d)] for s in range(n) if s != d]
        model.AddAllDifferent(grp)

    # objective: minimize makespan
    mk = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(mk, [a[(s, d)] for s in range(n) for d in range(n)])
    model.Minimize(mk)

    if warmstart and warm_arr is not None:
        for key, val in warm_arr.items():
            if val <= horizon:
                model.AddHint(a[key], val)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = workers
    solver.parameters.log_search_progress = False
    status = solver.Solve(model)

    res = {
        "status": solver.StatusName(status),
        "makespan": None,
        "best_bound": None,
        "wall": solver.WallTime(),
        "warm": warm_mk,
    }
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        res["makespan"] = int(solver.Value(mk)) + ramp
        res["best_bound"] = int(solver.BestObjectiveBound()) + ramp
        # verify zero eject buffer + conflict-free from the solution
        res["verified"] = _verify(solver, a, link_users, n, ramp)
    return res


def _verify(solver, a, link_users, n, ramp):
    # link: distinct send-equivalent arrivals
    for pc, vars_ in link_users.items():
        vals = [solver.Value(v) for v in vars_]
        if len(vals) != len(set(vals)):
            return False
    # eject: distinct arrivals per node (E=0 implied: eject == arrival)
    for d in range(n):
        vals = [solver.Value(a[(s, d)]) for s in range(n) if s != d]
        if len(vals) != len(set(vals)):
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mx", type=int, default=6)
    ap.add_argument("--my", type=int, default=8)
    ap.add_argument("--h", type=int, default=4)
    ap.add_argument("--v", type=int, default=8)
    ap.add_argument("--ramp", type=int, default=1)
    ap.add_argument("--w", type=int, default=None, help="per-hop wait cap (default unbounded)")
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--time", type=float, default=60.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--warmstart", action="store_true",
                    help="seed CP-SAT with greedy schedule + tighten horizon")
    args = ap.parse_args()
    mx, my, h, vv, ramp = args.mx, args.my, args.h, args.v, args.ramp

    lb, B, _ = packing_lb(mx, my, h, vv, ramp)
    bx, by = coord(B, mx)
    horizon = args.horizon or (lb * 3)
    wlabel = "inf" if args.w is None else str(args.w)
    print(f"mesh {mx}x{my}  N={mx*my}  "
          f"LB*={lb} @({bx},{by})  W={wlabel}  horizon={horizon}  t<={args.time}s"
          f"  warmstart={args.warmstart}")
    res = solve(mx, my, h, vv, ramp, args.w, horizon, args.time, args.workers,
                warmstart=args.warmstart)
    print(f"  status   : {res['status']}  ({res['wall']:.1f}s)")
    if res.get("warm") is not None:
        print(f"  warmstart: greedy makespan = {res['warm']}")
    if res["makespan"] is not None:
        gap = res["makespan"] - lb
        print(f"  makespan : {res['makespan']}  (LB*={lb}, gap=+{gap}, "
              f"{res['makespan']/lb:.3f}x)")
        print(f"  bound    : {res['best_bound']}  (proven lower bound)")
        print(f"  verified : E=0 + conflict-free = {res['verified']}")
    else:
        print("  no feasible solution within limits")


if __name__ == "__main__":
    main()
