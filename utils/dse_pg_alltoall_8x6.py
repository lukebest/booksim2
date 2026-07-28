#!/usr/bin/env python3
"""Cycle-accurate credit/FIFO DES for 8x6 packet-switched alltoall under PG.

Geometry: MX,MY=8,6  H=7 V=9  RAMP=2  RAMP_BW=2
Traffic: one-shot alltoall among compute_nodes_used; m in {1,5} wormhole
         packets; deterministic single path per (src,dst) → in-order.
Router:  in-port FIFO depth Q, credit FC, oldest-first output arb, HOL block.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pg_faults_8x6 as F
import pg_routing as R

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "pg_alltoall_8x6.json"

MX, MY, N = F.MX, F.MY, F.N
H, V = R.H, R.V
RAMP, RAMP_BW = R.RAMP, R.RAMP_BW

DIRS = R.DIRS  # E W N S
INJ = 4
T_MAX = 200_000
STALL_LIMIT = 2_000

SCHEMES = ["east_first", "xy", "rect_xy", "updown", "segment",
           "fault_ring_vc", "lash", "lash_tor", "stripe_vc",
           "dual_updown", "virtual_mesh"]
# updown_lb / segment_lb produced inside solve_all
MSG_SIZES = [1, 5]
Q_RANGE = [4, 8, 19]
DEFAULT_Q = 19


class Flit:
    __slots__ = ("src", "dst", "pkt", "fi", "m", "arrival", "path", "hop",
                 "vc", "served_out")

    def __init__(self, src, dst, pkt, fi, m, arrival, path, hop, vc):
        self.src = src
        self.dst = dst
        self.pkt = pkt
        self.fi = fi
        self.m = m
        self.arrival = arrival
        self.path = path          # node list
        self.hop = hop            # index in path (current node = path[hop])
        self.vc = vc
        self.served_out = False

    @property
    def at_dest(self) -> bool:
        return self.hop >= len(self.path) - 1

    def out_dir(self) -> int | None:
        if self.at_dest:
            return None
        return R.dir_of(self.path[self.hop], self.path[self.hop + 1])


def _vc_for(path: list[int], hop: int,
            vc_of: Callable | None) -> int:
    if vc_of is None or hop >= len(path) - 1:
        return 0
    return int(vc_of(path, hop))


def simulate_alltoall(paths: dict[tuple[int, int], list[int]],
                      compute: list[int],
                      adj: dict[int, list[int]],
                      m: int = 1,
                      Q: int = DEFAULT_Q,
                      num_vc: int = 1,
                      vc_of: Callable | None = None,
                      check_order: bool = True) -> dict[str, Any] | None:
    """Return {makespan, ordered_ok, ...} or None on deadlock/timeout."""
    if len(compute) < 2:
        return None
    compute_set = set(compute)
    # Per-node in-port FIFOs: ports 0..3 mesh, 4 = inject.
    # With num_vc>1 each port has per-VC queues (true VC buffering); credits
    # are per (out_dir, vc). num_vc==1 is a single physical FIFO per port.
    fifos: list[list[list[deque]]] = [
        [[deque() for _ in range(num_vc)] for _ in range(5)] for _ in range(N)
    ]
    credits = [[[Q] * num_vc for _ in range(4)] for _ in range(N)]
    arrive: dict[int, list] = defaultdict(list)   # t -> [(node, port, Flit)]
    cred_ret: dict[int, list] = defaultdict(list)  # t -> [(node, dir, vc)]

    # Injection state: per source, round-robin dest list and next flit to send
    dests = {}
    for s in compute:
        ds = [d for d in compute if d != s]
        # round-robin by rotating starting offset with src id
        rot = s % len(ds)
        dests[s] = ds[rot:] + ds[:rot]
    inj_state = {s: {"di": 0, "fi": 0, "pkt": 0} for s in compute}
    # pkt id per (src,dst)
    pkt_id = {}
    pid = 0
    for s in compute:
        for d in dests[s]:
            pkt_id[(s, d)] = pid
            pid += 1

    ejected = defaultdict(list)   # (src,dst) -> list of fi completion times
    n_eject_need = len(compute) * (len(compute) - 1) * m
    n_ejected = 0
    last_eject_t = 0

    # Pre-place: nothing until ramp. Injection happens in the loop.
    # Wire pipeline uses arrive[].

    live = 0  # flits currently in fifos or in flight on wires
    last_activity = 0
    t = 0

    def neighbor(node: int, d: int) -> int | None:
        x, y = F.coord(node)
        nx, ny = x + DIRS[d][0], y + DIRS[d][1]
        if 0 <= nx < MX and 0 <= ny < MY:
            nb = F.nid(nx, ny)
            # link must exist in adj
            if nb in adj.get(node, ()):
                return nb
        return None

    while t <= T_MAX:
        activity = False

        # credit returns
        for node, d, vc in cred_ret.pop(t, ()):
            credits[node][d][vc] += 1
            activity = True

        # wire arrivals
        for node, port, fl in arrive.pop(t, ()):
            vc = fl.vc if fl.vc < num_vc else 0
            fifos[node][port][vc].append(fl)
            activity = True

        # injection: up to RAMP_BW flits/cycle per compute source (after RAMP)
        if t >= RAMP:
            for s in compute:
                budget = RAMP_BW
                st = inj_state[s]
                while budget > 0 and st["di"] < len(dests[s]):
                    d = dests[s][st["di"]]
                    path = paths[(s, d)]
                    fi = st["fi"]
                    hop = 0
                    vc = _vc_for(path, hop, vc_of) if num_vc > 1 else 0
                    # inject into local INJ FIFO (unlimited for inject side;
                    # throttle by whether we can leave toward first hop or
                    # just enqueue — enqueue always, throttle by not
                    # overflowing a soft inject queue of depth Q)
                    if sum(len(fifos[s][INJ][v]) for v in range(num_vc)) >= Q * 4:
                        break
                    fl = Flit(s, d, pkt_id[(s, d)], fi, m, t, path, hop, vc)
                    fifos[s][INJ][vc].append(fl)
                    live += 1
                    activity = True
                    budget -= 1
                    st["fi"] += 1
                    if st["fi"] >= m:
                        st["fi"] = 0
                        st["di"] += 1
                        st["pkt"] += 1

        # arbitration per node
        for node in list(adj.keys()):
            # mesh outputs
            for d in range(4):
                nb = neighbor(node, d)
                if nb is None:
                    continue
                # pick oldest HOL across ports×VCs that wants this out dir
                best = None
                best_port = -1
                best_vcq = 0
                for port in range(5):
                    for vcq in range(num_vc):
                        q = fifos[node][port][vcq]
                        if not q:
                            continue
                        fl = q[0]
                        if fl.served_out or fl.at_dest:
                            continue
                        if fl.out_dir() != d:
                            continue
                        vc = (_vc_for(fl.path, fl.hop, vc_of)
                              if num_vc > 1 else 0)
                        fl.vc = vc
                        if credits[node][d][vc] <= 0:
                            continue
                        if (best is None
                                or fl.arrival < best.arrival
                                or (fl.arrival == best.arrival
                                    and (fl.src, fl.dst, fl.fi)
                                    < (best.src, best.dst, best.fi))):
                            best = fl
                            best_port = port
                            best_vcq = vcq
                if best is None:
                    continue
                # Channel VC for this hop (node -> nb)
                chan_vc = (_vc_for(best.path, best.hop, vc_of)
                           if num_vc > 1 else 0)
                credits[node][d][chan_vc] -= 1
                best.served_out = True
                new_hop = best.hop + 1
                nxt = Flit(best.src, best.dst, best.pkt, best.fi, best.m,
                           t + R.link_lat(node, nb), best.path, new_hop,
                           chan_vc)  # arrive into chan_vc buffer downstream
                lat = R.link_lat(node, nb)
                arrive[t + lat].append((nb, d ^ 1, nxt))
                fifos[node][best_port][best_vcq].popleft()
                activity = True
                if best_port != INJ:
                    up = neighbor(node, best_port)
                    if up is not None:
                        # free the VC buffer this flit occupied at `node`
                        cred_ret[t + R.link_lat(node, up)].append(
                            (up, best_port ^ 1, best_vcq))

            # eject
            if node in compute_set:
                drained_here = 0
                for port in range(5):
                    if drained_here >= RAMP_BW:
                        break
                    for vcq in range(num_vc):
                        if drained_here >= RAMP_BW:
                            break
                        q = fifos[node][port][vcq]
                        while q and drained_here < RAMP_BW:
                            fl = q[0]
                            if not fl.at_dest or fl.served_out:
                                break
                            q.popleft()
                            live -= 1
                            ejected[(fl.src, fl.dst)].append(fl.fi)
                            n_ejected += 1
                            last_eject_t = t
                            drained_here += 1
                            activity = True
                            if port != INJ:
                                up = neighbor(node, port)
                                if up is not None:
                                    cred_ret[t + R.link_lat(node, up)].append(
                                        (up, port ^ 1, vcq))

        # Also handle nodes that are compute but not in adj? shouldn't happen
        for node in compute:
            if node in adj:
                continue
            # isolated — only local inject/eject of zero-hop (impossible for A2A)

        if activity:
            last_activity = t
        if (n_ejected >= n_eject_need and not arrive and not any(
                fifos[n][p][v]
                for n in range(N) for p in range(5) for v in range(num_vc))):
            break
        if t - last_activity > STALL_LIMIT:
            return None
        t += 1
    else:
        return None

    # order check: for each (s,d), ejected fi sequence must be 0..m-1
    ordered_ok = True
    if check_order:
        for s in compute:
            for d in compute:
                if s == d:
                    continue
                seq = ejected.get((s, d), [])
                if seq != list(range(m)):
                    ordered_ok = False
                    break
            if not ordered_ok:
                break

    makespan = last_eject_t + RAMP
    return {
        "makespan": makespan,
        "ordered_ok": ordered_ok,
        "n_ejected": n_ejected,
        "cycles": t,
    }


def metrics(mk: int | None, golden_mk: int, lb: dict, n_flits: int,
            n_sac: int, n_good: int) -> dict:
    if mk is None or golden_mk <= 0:
        return {
            "raw_slowdown": None,
            "irregularity_penalty": None,
            "throughput_ratio": None,
            "sacrifice_cost": n_sac / n_good if n_good else 0.0,
        }
    # Routing-independent minimax lower bound on the same compute set, so the
    # penalty is always >= 0 (the old unbound_bw_lb was an achievable XY load,
    # not a bound, and schemes that beat it produced negative penalties).
    denom = max(lb.get("true_lb") or 0, 1)
    thr = n_flits / mk
    thr_g = n_flits / golden_mk if golden_mk else thr
    # golden throughput uses golden's flit count — compare per-node rates later
    return {
        "raw_slowdown": mk / golden_mk - 1.0,
        "irregularity_penalty": mk / denom - 1.0,
        "throughput": thr,
        "throughput_ratio": thr / thr_g if thr_g else None,
        "sacrifice_cost": n_sac / n_good if n_good else 0.0,
    }


_SOL_CACHE: dict[tuple, dict] = {}


def get_solution(pg: dict, scheme: str, do_balance: bool = True) -> dict:
    key = (pg["name"], pg["semantics"], scheme,
           tuple(pg["dead_nodes"]), tuple(tuple(l) for l in pg["dead_links"]))
    if key in _SOL_CACHE:
        return _SOL_CACHE[key]
    if scheme.endswith("_lb"):
        base_sch = scheme[:-3]
        sol = R.solve_scheme(pg, base_sch)
        if sol["feasible"] and do_balance:
            sol = R.load_balance_paths(sol, rounds=6)
            unb = R.unbound_minimax_load(sol["compute_nodes"], sol["route_adj"])
            sol["unbound_max_load"] = unb
            sol["max_load"] = R.max_link_load(sol["paths"])
            # CP-SAT only when A is small (enumeration stays cheap)
            if (sol["max_load"] > 1.15 * max(unb, 1)
                    and sol["n_compute_used"] <= 24):
                sol = R.try_cpsat_balance(sol, time_limit_s=2.0)
                sol["unbound_max_load"] = unb
                sol["max_load"] = R.max_link_load(sol["paths"])
        elif sol["feasible"]:
            sol["unbound_max_load"] = R.unbound_minimax_load(
                sol["compute_nodes"], sol["route_adj"])
            sol["max_load"] = R.max_link_load(sol["paths"])
    else:
        sol = R.solve_scheme(pg, scheme)
        if sol["feasible"]:
            sol["unbound_max_load"] = R.unbound_minimax_load(
                sol["compute_nodes"], sol["route_adj"])
            sol["max_load"] = R.max_link_load(sol["paths"])
    _SOL_CACHE[key] = sol
    return sol


def run_one(pg: dict, scheme: str, m: int, Q: int,
            do_balance: bool = True) -> dict[str, Any]:
    sol = get_solution(pg, scheme, do_balance=do_balance)

    rec = {
        "scenario": pg["name"],
        "semantics": pg["semantics"],
        "fault_class": pg["fault_class"],
        "region": pg["region"],
        "detail": pg["detail"],
        "scheme": scheme,
        "m": m,
        "Q": Q,
        "feasible": sol["feasible"],
        "n_sacrificed": sol["n_sacrificed"],
        "sacrificed": sol["sacrificed"],
        "n_compute_used": sol["n_compute_used"],
        "n_originally_good": sol["n_originally_good"],
        "reason": sol.get("reason"),
        "num_vc": sol.get("num_vc", 1),
        "makespan": None,
        "ordered_ok": None,
        "max_load": sol.get("max_load"),
        "bounds": None,
        "des_s": None,
    }
    if not sol["feasible"]:
        return rec

    bounds = R.analytical_lb(
        sol["paths"], sol["compute_nodes"], m=m, adj=sol["route_adj"],
        unbound_max_load=sol.get("unbound_max_load"),
        compute_unbound=False)
    rec["bounds"] = {k: v for k, v in bounds.items()}
    rec["max_load"] = bounds["max_load"]

    num_vc = sol.get("num_vc", 1)
    t0 = time.time()
    sim = simulate_alltoall(
        sol["paths"], sol["compute_nodes"], sol["route_adj"],
        m=m, Q=Q, num_vc=num_vc, vc_of=sol.get("vc_of"),
    )
    rec["des_s"] = round(time.time() - t0, 3)
    if sim is None:
        rec["reason"] = "DES_DEADLOCK"
        rec["feasible"] = False
        return rec
    rec["makespan"] = sim["makespan"]
    rec["ordered_ok"] = sim["ordered_ok"]
    if not sim["ordered_ok"]:
        rec["reason"] = "ORDER_VIOLATION"
    return rec


def golden_makespan(m: int, Q: int = DEFAULT_Q) -> tuple[int, dict]:
    pg = F.healthy_pg()
    sol = R.solve_scheme(pg, "xy")
    assert sol["feasible"]
    sim = simulate_alltoall(sol["paths"], sol["compute_nodes"], sol["route_adj"],
                            m=m, Q=Q)
    assert sim is not None, "healthy XY DES failed"
    unb = R.max_link_load(sol["paths"])  # XY is the unbound seed on healthy
    bounds = R.analytical_lb(sol["paths"], sol["compute_nodes"], m=m,
                             unbound_max_load=unb)
    return sim["makespan"], bounds


def run_sweep(q_list: list[int] | None = None,
              schemes: list[str] | None = None,
              semantics_list: list[str] | None = None,
              quick: bool = False) -> dict:
    # Full sweep defaults to Q=19; Q sensitivity is a separate tagged pass
    # appended for a small scenario subset when not --quick.
    q_list = q_list or [DEFAULT_Q]
    schemes = schemes or (SCHEMES + ["updown_lb", "segment_lb"])
    semantics_list = semantics_list or ["dead", "transit"]
    scenarios = F.all_scenarios()
    q_sense_scenarios = []
    if quick:
        scenarios = [s for s in scenarios if s["name"] in (
            "link_center_1", "node_center_1x1", "node_corner_2x2",
            "node_edge_2x2")]
        schemes = list(SCHEMES)
        q_list = [DEFAULT_Q]
    elif q_list == [DEFAULT_Q]:
        q_sense_scenarios = [s for s in scenarios if s["name"] in (
            "link_center_1", "node_center_1x1", "node_corner_2x2")]

    print("Computing golden makespans...", flush=True)
    golden = {}
    golden_bounds = {}
    for m in MSG_SIZES:
        mk, b = golden_makespan(m, DEFAULT_Q)
        golden[m] = mk
        golden_bounds[m] = b
        print(f"  golden m={m}: makespan={mk}  lb={b['lb']}  "
              f"max_load={b['max_load']}", flush=True)

    rows = []
    total = (len(scenarios) * len(semantics_list) * len(schemes)
             * len(MSG_SIZES) * len(q_list))
    done = 0
    t_all = time.time()
    for scen in scenarios:
        for sem in semantics_list:
            pg = F.expand_pg(scen, sem)
            for sch in schemes:
                for m in MSG_SIZES:
                    for Q in q_list:
                        done += 1
                        rec = run_one(pg, sch, m, Q)
                        gmk = golden[m]
                        n_flits = (rec["n_compute_used"]
                                   * max(0, rec["n_compute_used"] - 1) * m)
                        met = metrics(
                            rec["makespan"], gmk, rec["bounds"] or {},
                            n_flits, rec["n_sacrificed"],
                            rec["n_originally_good"])
                        rec.update(met)
                        rec["golden_makespan"] = gmk
                        rows.append(rec)
                        tag = ("OK" if rec["feasible"] and rec["makespan"]
                               else rec["reason"])
                        print(f"[{done}/{total}] {scen['name']:22s} {sem:7s} "
                              f"{sch:16s} m={m} Q={Q} -> {tag} "
                              f"mk={rec['makespan']} sac={rec['n_sacrificed']} "
                              f"({rec['des_s']}s)", flush=True)

    # Q sensitivity on a small subset (Q in {4,8,19}), dead semantics, m=1
    if q_sense_scenarios:
        print("Q-sensitivity subset...")
        for scen in q_sense_scenarios:
            pg = F.expand_pg(scen, "dead")
            for sch in ("xy", "updown", "segment"):
                for Q in (4, 8, 19):
                    if Q == DEFAULT_Q:
                        continue  # already have Q=19 from main sweep
                    rec = run_one(pg, sch, m=1, Q=Q)
                    gmk = golden[1]
                    n_flits = (rec["n_compute_used"]
                               * max(0, rec["n_compute_used"] - 1))
                    met = metrics(
                        rec["makespan"], gmk, rec["bounds"] or {},
                        n_flits, rec["n_sacrificed"],
                        rec["n_originally_good"])
                    rec.update(met)
                    rec["golden_makespan"] = gmk
                    rec["q_sensitivity"] = True
                    rows.append(rec)
                    print(f"[Q] {scen['name']:22s} {sch:16s} Q={Q} -> "
                          f"mk={rec['makespan']}")

    out = {
        "meta": {
            "mx": MX, "my": MY, "H": H, "V": V,
            "RAMP": RAMP, "RAMP_BW": RAMP_BW,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "golden": golden,
            "golden_bounds": {str(k): v for k, v in golden_bounds.items()},
            "schemes": schemes,
            "q_list": q_list,
            "quick": quick,
            "elapsed_s": round(time.time() - t_all, 2),
        },
        "rows": rows,
    }
    # JSON-serialize: drop non-serializable
    def scrub(o):
        if isinstance(o, dict):
            return {str(k): scrub(v) for k, v in o.items()
                    if callable(v) is False and k != "paths"
                    and k != "route_adj" and k != "vc_of"}
        if isinstance(o, list):
            return [scrub(x) for x in o]
        if isinstance(o, (int, float, str, bool)) or o is None:
            return o
        return str(o)

    OUT_JSON.write_text(json.dumps(scrub(out), indent=1))
    print(f"Wrote {OUT_JSON}  ({len(rows)} rows, "
          f"{out['meta']['elapsed_s']}s)")
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="4 scenarios, Q=19 only")
    ap.add_argument("--q", type=int, nargs="*", default=None)
    args = ap.parse_args()
    run_sweep(q_list=args.q, quick=args.quick)


if __name__ == "__main__":
    main()
