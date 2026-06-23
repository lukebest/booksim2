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
from sched_eject_bw import packing_lb_bw, latency_floor


def greedy_arrivals(mx, my, h, vv, ramp, wcap, bw=1):
    """Greedy E=0 + bounded-W schedule; return (makespan, arrivals dict a[(s,d)]).

    Mirrors sched_no_eject_buffer.schedule but records every node's arrival cycle
    so it can warm-start CP-SAT. Far-from-center source order (the good one).
    down-ramp may eject up to bw flits per cycle (E=0 throughout)."""
    n = mx * my
    trees = {s: tree_children(s, mx, my) for s in range(n)}
    link_busy = defaultdict(set)
    down_cnt = defaultdict(lambda: defaultdict(int))  # node -> cycle -> #ejects
    cx0, cy0 = (mx - 1) / 2, (my - 1) / 2
    srcs = sorted(range(n),
                  key=lambda s: -(abs(coord(s, mx)[0] - cx0) + abs(coord(s, mx)[1] - cy0)))
    arr = {}
    makespan = 0
    for s in srcs:
        off = 0
        while True:
            tent_l, tent_d, tl = [], [], set()
            tent_dcnt = defaultdict(lambda: defaultdict(int))
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
                            if down_cnt[c][arrive] + tent_dcnt[c][arrive] < bw:
                                found = True; break
                        t += 1
                    if not found:
                        ok = False; break
                    tent_l.append((lk, t)); tl.add((lk, t))
                    tent_d.append((c, arrive)); tent_dcnt[c][arrive] += 1
                    avail[c] = arrive; order.append(c)
            if ok:
                for (lk, t) in tent_l:
                    link_busy[lk].add(t)
                for (d, ej) in tent_d:
                    down_cnt[d][ej] += 1
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


def solve(mx, my, h, vv, ramp, wcap, horizon, time_limit, workers, warmstart=False,
          bw=1, serfork=False):
    n = mx * my
    trees, parents = build_trees(mx, my)

    warm_mk, warm_arr = (None, None)
    if warmstart:
        warm_mk, warm_arr = greedy_arrivals(mx, my, h, vv, ramp, wcap, bw)
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

    # serialize fork: each router forwards <=1 flit per cycle (fan-out <=1 port
    # per cycle). Group EVERY outgoing forwarding send of node p (all directions,
    # all sources) and force distinct send cycles. send_cycle(s,p->c)=a[s,c]-lat.
    # (down-ramp eject is a separate path, governed by bw, not counted here.)
    if serfork:
        node_sends = defaultdict(list)
        for s in range(n):
            for p, kids in trees[s].items():
                for c in kids:
                    lat = link_lat(p, c, mx, h, vv)
                    sv = model.NewIntVar(0, horizon, f"snd_{s}_{p}_{c}")
                    model.Add(sv == a[(s, c)] - lat)
                    node_sends[p].append(sv)
        for p, lst in node_sends.items():
            if len(lst) > 1:
                model.AddAllDifferent(lst)

    # down-ramp eject + zero eject buffer (E=0): <= bw ejects per cycle per node.
    # bw==1 -> AllDifferent (1/cycle). bw>1 -> cumulative: unit-duration task at
    # start=a[s,d], demand 1, capacity bw (wider down-ramp consumes bw/cycle).
    for d in range(n):
        grp = [a[(s, d)] for s in range(n) if s != d]
        if bw == 1:
            model.AddAllDifferent(grp)
        else:
            iv = [model.NewFixedSizeIntervalVar(v, 1, f"ej_{d}_{i}")
                  for i, v in enumerate(grp)]
            model.AddCumulative(iv, [1] * len(iv), bw)

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
        # verify links (always 1/cycle); eject strict-distinct only when bw==1
        res["verified"] = _verify(solver, a, link_users, n, ramp, bw,
                                  trees, mx, h, vv, serfork)
        res["arrivals"] = {(s, d): int(solver.Value(a[(s, d)]))
                           for s in range(n) for d in range(n)}
        res["trees"] = trees
    return res


def _verify(solver, a, link_users, n, ramp, bw=1,
            trees=None, mx=None, h=None, vv=None, serfork=False):
    # link: distinct send-equivalent arrivals (always 1/cycle/directed-link)
    for pc, vars_ in link_users.items():
        vals = [solver.Value(v) for v in vars_]
        if len(vals) != len(set(vals)):
            return False
    # eject: <= bw per cycle per node (E=0: eject == arrival)
    from collections import Counter
    for d in range(n):
        cnt = Counter(solver.Value(a[(s, d)]) for s in range(n) if s != d)
        if max(cnt.values()) > bw:
            return False
    # serialize-fork: each node forwards <=1 flit per cycle (fan-out <=1 port)
    if serfork and trees is not None:
        node_sends = defaultdict(list)
        for s in range(n):
            for p, kids in trees[s].items():
                for c in kids:
                    lat = link_lat(p, c, mx, h, vv)
                    node_sends[p].append(int(solver.Value(a[(s, c)])) - lat)
        for p, lst in node_sends.items():
            if len(lst) != len(set(lst)):
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
    ap.add_argument("--bw", type=int, default=1, help="down-ramp eject bw (flit/cycle)")
    ap.add_argument("--serfork", action="store_true",
                    help="serialize router fan-out: <=1 forwarded flit per node per cycle")
    args = ap.parse_args()
    mx, my, h, vv, ramp = args.mx, args.my, args.h, args.v, args.ramp

    lb, B = packing_lb_bw(mx, my, h, vv, ramp, args.bw)
    bx, by = coord(B, mx)
    lfloor = latency_floor(mx, my, h, vv, ramp)
    horizon = args.horizon or (lb * 3)
    wlabel = "inf" if args.w is None else str(args.w)
    print(f"mesh {mx}x{my}  N={mx*my}  bw={args.bw}  "
          f"LB*(bw)={lb} @({bx},{by})  latency_floor={lfloor}  "
          f"W={wlabel}  horizon={horizon}  t<={args.time}s  warmstart={args.warmstart}")
    res = solve(mx, my, h, vv, ramp, args.w, horizon, args.time, args.workers,
                warmstart=args.warmstart, bw=args.bw, serfork=args.serfork)
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
