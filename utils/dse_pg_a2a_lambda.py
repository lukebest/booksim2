#!/usr/bin/env python3
"""Open-loop all-to-all injection-rate sweep for healthy XY and Super-turn.

Traffic: every live compute node offers a packet with probability λ each cycle,
destination uniform over the other live nodes (same λ at every source).
That is the project's existing mesh_base all-to-all injector
(`rg_steady_des.Injector`), reused on the PG residual graph.

Metrics per (scenario, λ):
  mean / max packet latency  (t_eject − t_gen, packets born after warmup)
  effective BW               delivered flits / measure cycles  (network-wide)
  accepted                   delivered packets / (A · measure)
  buffer / table / source-routing costs  (scheme constants; also per scenario)

Fault catalogue (partial-good): ≤2 dead routers + ≤4 undirected links,
router–link faults non-overlapping, one sample per (nr, nl) cell except (0,0).

  python3 utils/dse_pg_a2a_lambda.py --quick
  python3 utils/dse_pg_a2a_lambda.py
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import multiprocessing as mp
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import dse_pg_alltoall_8x6 as D
import pg_faults_8x6 as F
import pg_faults_budget_8x6 as B
import pg_routing as R
import ppa_analytic_model as PPA

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_a2a_lambda.json"

def default_lams() -> list[float]:
    """0.10–0.35 step 0.05, then 0.36–0.50 step 0.01."""
    coarse = [round(0.10 + 0.05 * i, 2) for i in range(6)]  # 0.10 .. 0.35
    fine = [round(0.36 + 0.01 * i, 2) for i in range(15)]   # 0.36 .. 0.50
    return coarse + fine


LAMS = default_lams()
Q = D.DEFAULT_Q
FLIT_BITS = PPA.FLIT_BITS
A_FLIT = PPA.ARCH_A3_BUFFERS / PPA.ARCH_A3_INTERIOR_FLITS
PORTS = 5
WARM, MEAS = 1500, 4000
# Zero-load latency of the longest Manhattan pair on the full 8×6
# (any shortest path): (MX−1)·H + (MY−1)·V + 2·RAMP + (m−1).
MAX_ZERO_LATENCY = (F.MX - 1) * R.H + (F.MY - 1) * R.V + 2 * R.RAMP


def annotate_lat_ratio(rec: dict, t0: int = MAX_ZERO_LATENCY) -> dict:
    rec["max_zero_latency"] = t0
    mx = rec.get("max_lat")
    rec["max_lat_over_t0"] = (round(mx / t0, 4) if mx is not None and t0 else None)
    return rec


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

def hop_max(paths: dict) -> int:
    return max((len(p) - 1) for p in paths.values()) if paths else 0


def dest_table_conflicts(paths: dict) -> int:
    """How many (router, dest) keys need two different next hops."""
    vd: dict[tuple[int, int], set[int]] = defaultdict(set)
    for (_s, d), path in paths.items():
        for i, u in enumerate(path[:-1]):
            vd[(u, d)].add(path[i + 1])
    return sum(1 for nxts in vd.values() if len(nxts) > 1)


# Silicon VC budget of the *scheme*, not the VC count a particular
# residual graph happened to use. Super-turn (M0s) always instantiates 2 VC.
SCHEME_VC_SILICON = {"xy": 1, "super_turn": 2}


def hw_cost(scheme: str, num_vc: int, paths: dict, n_live: int) -> dict:
    """Buffer / dest-table / source-routing overheads. Independent of λ.

    Super-turn is billed at 2 VC even when one Glass–Ni model already
    covers the residual graph (num_vc_used may be 1).
    """
    used = int(num_vc)
    billed = SCHEME_VC_SILICON.get(scheme, used)
    buf_slots = PORTS * billed * Q
    buf_bits = buf_slots * FLIT_BITS
    buf_area = buf_slots * A_FLIT
    xy_buf_area = PORTS * 1 * Q * A_FLIT
    router_area = PPA.BASELINE_CROSSBAR + PPA.BASELINE_CONTROL + buf_area

    n_dest = max(1, n_live - 1)
    conflicts = dest_table_conflicts(paths) if paths else 0
    # 2 bit / dest if dest-only is legal; else dest×src is not stored —
    # fall back to source routing. Combinational XY / single turn-model
    # still *can* be a dest table of the same size on a residual graph.
    table_bits = 0 if conflicts else n_dest * 2
    table_ok = conflicts == 0

    hmax = hop_max(paths)
    len_bits = max(1, math.ceil(math.log2(hmax + 1))) if hmax else 1
    vc_bit = 1 if billed > 1 else 0
    sr_bits = len_bits + 2 * hmax + vc_bit

    # (src, dest) → next+VC at each transit router, if dest-only is illegal.
    per_r: dict[int, set] = defaultdict(set)
    for (s, d), path in (paths or {}).items():
        for u in path[:-1]:
            per_r[u].add((s, d))
    entry_bits = 2 + vc_bit
    src_bits = [len(ks) * entry_bits for ks in per_r.values()]
    src_bits.sort()

    combo = 0
    if scheme == "xy" and n_live == F.N:
        combo = 0  # healthy XY is a comparator
    # residual XY / turn-model: next hop is not a closed form of coordinates
    # once links/nodes are missing, so the dest table is the cheap store.

    return {
        "num_vc": billed,
        "num_vc_used": used,
        "num_vc_billed": billed,
        "Q": Q,
        "buffer_slots_per_router": buf_slots,
        "buffer_bits_per_router": buf_bits,
        "buffer_area_norm": round(buf_area, 4),
        "buffer_area_vs_xy1vc": round(buf_area / xy_buf_area, 4),
        "router_area_norm": round(router_area, 4),
        "table_dest_only_ok": table_ok,
        "table_conflicts": conflicts,
        "table_bits_per_router": table_bits if table_ok else None,
        "table_combo_bits": combo,
        "table_src_aware_bits_med": (src_bits[len(src_bits) // 2]
                                     if src_bits else 0),
        "table_src_aware_bits_max": (src_bits[-1] if src_bits else 0),
        "sr_hmax": hmax,
        "sr_header_bits": sr_bits,
        "sr_frac_of_flit": round(sr_bits / FLIT_BITS, 5),
        "sr_extra_flit": sr_bits > FLIT_BITS,
        "n_live": n_live,
        "note": ("dest table = (A-1)×2 bit if dest-only; "
                 "src-aware table = n_transit_OD × (2+[VC]) bit/router; "
                 "source routing = ceil(log2(H+1)) + 2H + [1 VC bit]; "
                 "healthy XY combinational table = 0"),
    }


# ---------------------------------------------------------------------------
# Path-aware credit mesh (MeshBaseSim + residual graph + VC)
# ---------------------------------------------------------------------------

@dataclass
class _Flit:
    pid: int
    src: int
    dst: int
    t_gen: int
    path: list[int]
    hop: int = 0
    vc: int = 0

    @property
    def nxt(self) -> int | None:
        return self.path[self.hop + 1] if self.hop + 1 < len(self.path) else None


class PathMeshSteady:
    """IQ credit-FC mesh on a residual graph with locked per-packet VC."""

    def __init__(self, adj: dict[int, list[int]],
                 paths: dict[tuple[int, int], list[int]],
                 compute: list[int],
                 num_vc: int = 1,
                 vc_of: Callable | None = None,
                 buf_depth: int = Q,
                 warmup: int = WARM,
                 measure: int = MEAS):
        self.adj = adj
        self.paths = paths
        self.compute = list(compute)
        self.cset = set(compute)
        self.live = list(adj.keys())
        self.num_vc = num_vc
        self.vc_of = vc_of
        self.Q = buf_depth
        self.warmup = warmup
        self.measure = measure
        self.t = 0
        self.buf: dict[tuple[int, int, int], deque] = defaultdict(deque)
        self.credit: dict[tuple[int, int, int], int] = defaultdict(
            lambda: buf_depth)
        self.arrive: dict[int, list] = defaultdict(list)
        self.cred_ret: dict[int, list] = defaultdict(list)
        self.srcq: dict[int, deque] = defaultdict(deque)
        self.out_free: dict[tuple[int, int], int] = defaultdict(int)
        self.rr: dict[tuple[int, int], int] = defaultdict(int)
        self.pkt_done: list[tuple[int, int, int, int, int]] = []
        self._pid = 0
        self.n_credit_stall = 0

    def offer(self, src: int, dst: int) -> None:
        path = self.paths[(src, dst)]
        vc = 0
        if self.num_vc > 1 and self.vc_of is not None:
            vc = int(self.vc_of(path, 0))
        self.srcq[src].append(_Flit(self._pid, src, dst, self.t, path, 0, vc))
        self._pid += 1

    def backlog(self) -> int:
        return sum(len(q) for q in self.srcq.values())

    def in_network(self) -> int:
        return (sum(len(q) for q in self.buf.values())
                + sum(len(v) for v in self.arrive.values()))

    def step(self) -> None:
        t = self.t
        for node, in_port, f in self.arrive.pop(t, []):
            self.buf[(node, in_port, f.vc)].append(f)
        for node, out, vc in self.cred_ret.pop(t, []):
            self.credit[(node, out, vc)] += 1
        self._switch()
        self._inject()
        self.t += 1

    def _switch(self) -> None:
        for node in self.live:
            # candidates: (in_port, vc) HOL flits
            want: dict[int, list[tuple[int, int]]] = defaultdict(list)
            ports = [-1] + list(self.adj.get(node, ()))
            for ip in ports:
                for vc in range(self.num_vc):
                    q = self.buf.get((node, ip, vc))
                    if not q:
                        continue
                    f = q[0]
                    want[-1 if f.nxt is None else f.nxt].append((ip, vc))

            for out, cands in want.items():
                if out == -1:
                    n = 0
                    for ip, vc in cands:
                        if n >= R.RAMP_BW:
                            break
                        if node not in self.cset:
                            break
                        f = self.buf[(node, ip, vc)].popleft()
                        self._free(node, ip, vc)
                        self.pkt_done.append(
                            (f.pid, f.src, f.dst, f.t_gen, self.t + R.RAMP))
                        n += 1
                    continue
                if out not in self.adj.get(node, ()):
                    continue
                if self.out_free[(node, out)] > self.t:
                    continue
                # one flit / output / cycle; pick a credited candidate
                ptr = self.rr[(node, out)]
                def key(cv, _p=ptr):
                    ip, vc = cv
                    return ((hash((ip, vc)) - _p) % 1024, ip, vc)
                cands = sorted(cands, key=key)
                picked = None
                for ip, vc in cands:
                    if self.credit[(node, out, vc)] < 1:
                        self.n_credit_stall += 1
                        continue
                    picked = (ip, vc)
                    break
                if picked is None:
                    continue
                ip, vc = picked
                f = self.buf[(node, ip, vc)].popleft()
                self._free(node, ip, vc)
                self.rr[(node, out)] += 1
                self.credit[(node, out, vc)] -= 1
                self.out_free[(node, out)] = self.t + 1
                f.hop += 1
                lat = R.link_lat(node, out)
                self.arrive[self.t + lat].append((out, node, f))

    def _free(self, node: int, in_port: int, vc: int) -> None:
        if in_port < 0:
            return
        lat = R.link_lat(in_port, node)
        self.cred_ret[self.t + lat].append((in_port, node, vc))

    def _inject(self) -> None:
        for node, q in self.srcq.items():
            n = 0
            while q and n < R.RAMP_BW:
                f = q[0]
                key = (node, -1, f.vc)
                if len(self.buf[key]) >= self.Q:
                    break
                self.buf[key].append(q.popleft())
                n += 1


class NodeInjector:
    """Bernoulli(λ) on an arbitrary node id list (not necessarily 0..n-1)."""

    def __init__(self, nodes: list[int], lam: float, seed: int):
        self.nodes = list(nodes)
        self.lam = lam
        self.rng = random.Random(seed)
        self.n = len(self.nodes)

    def arrivals(self) -> list[tuple[int, int]]:
        out = []
        nodes = self.nodes
        n = self.n
        if n < 2:
            return out
        for si, s in enumerate(nodes):
            if self.rng.random() < self.lam:
                j = self.rng.randrange(n - 1)
                if j >= si:
                    j += 1
                out.append((s, nodes[j]))
        return out


def run_one(paths, compute, adj, lam, *, num_vc=1, vc_of=None,
            warmup=WARM, measure=MEAS, seed=0, Q=Q,
            max_backlog=200_000) -> dict[str, Any]:
    sim = PathMeshSteady(adj, paths, compute, num_vc=num_vc, vc_of=vc_of,
                         buf_depth=Q, warmup=warmup, measure=measure)
    inj = NodeInjector(compute, lam, seed=seed + 1)
    a = len(compute)
    lats: list[int] = []
    n_del = 0
    n_off = 0
    backlog_tr: list[tuple[int, int]] = []
    total = warmup + measure
    trace_every = max(1, measure // 200)
    bail = 0
    for t in range(total):
        for s, d in inj.arrivals():
            if (s, d) not in paths:
                continue
            sim.offer(s, d)
            if t >= warmup:
                n_off += 1
        sim.step()
        while sim.pkt_done:
            _pid, _s, _d, t_gen, t_done = sim.pkt_done.pop()
            if t_gen >= warmup:
                lats.append(t_done - t_gen)
                n_del += 1
        if t >= warmup and (t - warmup) % trace_every == 0:
            backlog_tr.append((t, sim.backlog()))
        if sim.backlog() > max_backlog:
            bail = t
            break

    # Drain packets born in the measure window so accepted rate is not
    # short by one network latency at the cutoff.
    t_end_gen = warmup + measure
    drain_cap = 4000
    drained = 0
    if not bail:
        while drained < drain_cap and (
                sim.backlog() or sim.in_network() or sim.pkt_done):
            sim.step()
            drained += 1
            while sim.pkt_done:
                _pid, _s, _d, t_gen, t_done = sim.pkt_done.pop()
                if warmup <= t_gen < t_end_gen:
                    lats.append(t_done - t_gen)
                    n_del += 1
            if not sim.backlog() and not sim.in_network():
                break

    slope = 0.0
    if len(backlog_tr) >= 3:
        ts = [x[0] for x in backlog_tr]
        bs = [x[1] for x in backlog_tr]
        mt, mb = sum(ts) / len(ts), sum(bs) / len(bs)
        den = sum((t - mt) ** 2 for t in ts)
        if den:
            slope = sum((t - mt) * (b - mb)
                        for t, b in zip(ts, bs)) / den / max(a, 1)
    acc = n_del / (a * measure) if a and measure else 0.0
    ratio = acc / lam if lam > 0 else 0.0
    flits = n_del  # m = 1
    return {
        "lam": lam, "A": a, "warmup": warmup, "measure": measure,
        "n_offered": n_off, "n_delivered": n_del,
        "accepted_per_node": round(acc, 5),
        "accept_ratio": round(ratio, 4),
        "bw_eff_flits_per_cy": round(flits / measure, 4),
        "bw_eff_bits_per_cy": flits / measure * FLIT_BITS,
        "mean_lat": (round(sum(lats) / len(lats), 2) if lats else None),
        "max_lat": (max(lats) if lats else None),
        "p50_lat": (sorted(lats)[len(lats) // 2] if lats else None),
        "p99_lat": (sorted(lats)[int(0.99 * (len(lats) - 1))] if lats else None),
        "n_samples": len(lats),
        "backlog_slope": round(slope, 5),
        "stable": bool(slope < 0.002 and ratio >= 0.95 and not bail),
        "backlog_end": sim.backlog(),
        "bail_at": bail or None,
        "n_credit_stall": sim.n_credit_stall,
    }


# ---------------------------------------------------------------------------
# Catalogues + solve
# ---------------------------------------------------------------------------

def catalog_2r4l(n_per_cell: int = 1, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for nr, nl in itertools.product(range(3), range(5)):
        if nr == 0 and nl == 0:
            continue
        for i in range(n_per_cell):
            out.append(B.sample_scenario(rng, nr, nl, i))
    return out


def solve(pg: dict, scheme: str) -> dict[str, Any] | None:
    sol = R.solve_scheme(pg, scheme)
    if not sol or not sol.get("feasible"):
        return None
    return sol


def _job(args):
    tag, scen_name, scheme, lam, seed, warmup, measure, sol_pack = args
    paths = {tuple(k): v for k, v in sol_pack["paths"]}
    compute = sol_pack["compute"]
    adj = {int(u): list(vs) for u, vs in sol_pack["adj"]}
    num_vc = sol_pack["num_vc"]
    # vc_of cannot be pickled as a closure; reconstruct from pair→vc map
    which = {tuple(k): int(v) for k, v in sol_pack.get("which", [])}
    vc_of = (lambda path, i, _w=which: _w[(path[0], path[-1])]) if which else None
    t0 = time.perf_counter()
    rec = run_one(paths, compute, adj, lam, num_vc=num_vc, vc_of=vc_of,
                  warmup=warmup, measure=measure, seed=seed)
    rec["secs"] = round(time.perf_counter() - t0, 2)
    rec["tag"] = tag
    rec["scenario"] = scen_name
    rec["scheme"] = scheme
    rec["num_vc"] = num_vc
    rec["A"] = len(compute)
    rec["n_sacrificed"] = sol_pack.get("n_sac", 0)
    rec["turn_mode"] = sol_pack.get("turn_mode")
    return annotate_lat_ratio(rec)


def pack_sol(sol: dict) -> dict:
    paths = sol["paths"]
    which = []
    vc_of = sol.get("vc_of")
    if vc_of is not None and sol.get("num_vc", 1) > 1:
        for (s, d), p in paths.items():
            which.append(((s, d), int(vc_of(p, 0))))
    return {
        "paths": [((s, d), p) for (s, d), p in paths.items()],
        "compute": list(sol["compute_nodes"]),
        "adj": [(u, list(vs)) for u, vs in sol["route_adj"].items()],
        "num_vc": int(sol.get("num_vc", 1)),
        "which": which,
        "n_sac": int(sol.get("n_sacrificed", 0)),
        "turn_mode": sol.get("turn_mode"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="λ ∈ {0.1,0.35,0.50}, warmup/measure halved, 1 seed")
    ap.add_argument("--reuse", action="store_true", default=True,
                    help="keep matching rows already in -o (default on)")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore existing -o and rerun every λ")
    ap.add_argument("--workers", type=int, default=min(8, mp.cpu_count() or 4))
    ap.add_argument("-o", type=Path, default=OUT)
    args = ap.parse_args()

    lams = [0.10, 0.35, 0.50] if args.quick else LAMS
    warm, meas = (800, 2000) if args.quick else (WARM, MEAS)
    t0 = MAX_ZERO_LATENCY

    healthy = F.healthy_pg()
    xy = solve(healthy, "xy")
    assert xy and xy["feasible"]
    st = solve(healthy, "super_turn")  # cost reference on healthy
    hw_xy = hw_cost("xy", 1, xy["paths"], 48)
    hw_st_h = hw_cost("super_turn", st["num_vc"], st["paths"], 48) if st else None

    cat = catalog_2r4l(n_per_cell=1, seed=0)
    pg_sols = []
    skipped = []
    for s in cat:
        pg = B.expand_budget(s, "dead")
        sol = solve(pg, "super_turn")
        if sol is None:
            skipped.append(s["name"])
            print("SKIP", s["name"], "super_turn infeasible", flush=True)
            continue
        # attach compute if sacrifice changed it
        if "compute_nodes" not in sol:
            sol["compute_nodes"] = pg["compute_nodes"]
            sol["route_adj"] = pg["route_adj"]
        if "n_sacrificed" not in sol:
            sol["n_sacrificed"] = len(sol.get("forced_sacrificed") or [])
        pg_sols.append((s, sol, hw_cost("super_turn", sol.get("num_vc", 1),
                                        sol["paths"],
                                        len(sol["compute_nodes"]))))
        print("SOLVE", s["name"], "A", sol.get("n_compute_used",
                                                len(sol["compute_nodes"])),
              "vc", sol.get("num_vc"), "mode", sol.get("turn_mode"),
              "sac", sol.get("n_sacrificed", 0), flush=True)

    jobs = []
    pack_xy = pack_sol(xy)
    for lam in lams:
        jobs.append(("all_good", "healthy", "xy", lam, 0, warm, meas, pack_xy))
    for s, sol, _hw in pg_sols:
        pack = pack_sol(sol)
        for lam in lams:
            jobs.append(("partial_good", s["name"], "super_turn",
                         lam, 0, warm, meas, pack))

    kept: list[dict] = []
    if args.reuse and not args.fresh and args.o.exists():
        prev = json.loads(args.o.read_text())
        want = {(round(lam, 2), warm, meas) for lam in lams}
        for r in prev.get("rows") or []:
            key = (round(r["lam"], 2), r.get("warmup"), r.get("measure"))
            if key in want:
                kept.append(annotate_lat_ratio(r, t0))
        have = {(r["tag"], r["scenario"], round(r["lam"], 2)) for r in kept}
        jobs = [j for j in jobs
                if (j[0], j[1], round(j[3], 2)) not in have]
        print("reuse", len(kept), "new jobs", len(jobs), flush=True)

    print("jobs", len(jobs), "workers", args.workers, flush=True)
    rows = list(kept)
    if jobs:
        ctx = mp.get_context("fork")
        with ctx.Pool(args.workers) as pool:
            for i, rec in enumerate(pool.imap_unordered(_job, jobs), 1):
                rows.append(rec)
                if (i % 10 == 0 or rec["lam"] in (0.1, 0.35, 0.5)
                        or rec.get("bail_at")):
                    print("[%d/%d] %s %s λ=%.2f A=%d acc=%.3f mean=%s max=%s "
                          "max/T0=%s bw=%.2f stab=%s %.1fs"
                          % (i, len(jobs), rec["tag"], rec["scenario"], rec["lam"],
                             rec["A"], rec["accepted_per_node"],
                             rec["mean_lat"], rec["max_lat"],
                             rec.get("max_lat_over_t0"),
                             rec["bw_eff_flits_per_cy"], rec["stable"],
                             rec["secs"]), flush=True)

    def summarize(tag, scheme):
        sel = [r for r in rows if r["tag"] == tag and r["scheme"] == scheme]
        by = defaultdict(list)
        for r in sel:
            by[r["lam"]].append(r)
        out = []
        for lam in lams:
            g = by.get(lam, [])
            if not g:
                continue

            def med(k):
                xs = [r[k] for r in g if r[k] is not None]
                return None if not xs else sorted(xs)[len(xs) // 2]

            def mx(k):
                xs = [r[k] for r in g if r[k] is not None]
                return None if not xs else max(xs)

            out.append({
                "lam": lam, "n": len(g),
                "mean_lat_med": med("mean_lat"),
                "mean_lat_worst": mx("mean_lat"),
                "max_lat_med": med("max_lat"),
                "max_lat_worst": mx("max_lat"),
                "bw_eff_med": med("bw_eff_flits_per_cy"),
                "bw_eff_worst": (min(r["bw_eff_flits_per_cy"] for r in g)
                                 if g else None),
                "accepted_med": med("accepted_per_node"),
                "n_stable": sum(1 for r in g if r["stable"]),
                "max_over_t0_med": med("max_lat_over_t0"),
                "max_over_t0_worst": mx("max_lat_over_t0"),
            })
        return out

    hw_pg = [h for _s, _sol, h in pg_sols]
    doc = {
        "meta": {
            "lams": lams, "warmup": warm, "measure": meas, "Q": Q,
            "flit_bits": FLIT_BITS, "m": 1,
            "traffic": "Bernoulli(λ) per live node, dest uniform over others",
            "latency": "t_eject − t_gen for packets generated after warmup",
            "bw_eff": "delivered flits / measure cycles (network-wide)",
            "accepted": "delivered packets / (A · measure)",
            "all_good_scheme": "xy",
            "partial_scheme": "super_turn (M0s, ≤2 VC)",
            "partial_catalog": "≤2R + ≤4L, non-overlap, n_per_cell=1, seed=0",
            "n_partial_scenarios": len(pg_sols),
            "skipped": skipped,
            "n_jobs": len(jobs) + len(kept),
            "n_reused": len(kept),
            "n_ran": len(jobs),
            "mx": F.MX, "my": F.MY, "H": R.H, "V": R.V,
            "ramp": R.RAMP, "ramp_bw": R.RAMP_BW,
            "ports": PORTS, "seed": 0,
            "max_manhattan_hops": (F.MX - 1) + (F.MY - 1),
            "max_manhattan_wire": (F.MX - 1) * R.H + (F.MY - 1) * R.V,
            "max_zero_latency": t0,
            "max_zero_latency_formula":
                "(MX-1)*H + (MY-1)*V + 2*RAMP + (m-1)",
            "credit": "IQ, credit init = Q, 1 flit / output / cycle",
            "inject": "Bernoulli(λ) each cycle per live node; dest uniform "
                      "over other live nodes; m=1 flit",
            "des": "PathMeshSteady residual-graph credit mesh; locked VC",
        },
        "hw_all_good": hw_xy,
        "hw_super_turn_healthy": hw_st_h,
        "hw_partial_per_scenario": [
            {"scenario": s["name"], "n_routers": s["n_routers"],
             "n_links": s["n_links"], **h}
            for s, sol, h in pg_sols
        ],
        "hw_partial_summary": {
            "buffer_bits_per_router_med":
                sorted(h["buffer_bits_per_router"] for h in hw_pg)[len(hw_pg)//2]
                if hw_pg else None,
            "buffer_bits_per_router_max":
                max((h["buffer_bits_per_router"] for h in hw_pg), default=None),
            "table_bits_med":
                sorted(h["table_bits_per_router"] or 0 for h in hw_pg)[len(hw_pg)//2]
                if hw_pg else None,
            "table_bits_max":
                max((h["table_bits_per_router"] or 0 for h in hw_pg), default=None),
            "sr_header_bits_med":
                sorted(h["sr_header_bits"] for h in hw_pg)[len(hw_pg)//2]
                if hw_pg else None,
            "sr_header_bits_max":
                max((h["sr_header_bits"] for h in hw_pg), default=None),
            "num_vc_max": max((h["num_vc"] for h in hw_pg), default=None),
            "n_dest_only_ok": sum(1 for h in hw_pg if h["table_dest_only_ok"]),
        },
        "summary_all_good": summarize("all_good", "xy"),
        "summary_partial": summarize("partial_good", "super_turn"),
        "rows": sorted(rows, key=lambda r: (r["tag"], r["scenario"], r["lam"])),
    }
    args.o.write_text(json.dumps(doc, indent=1))
    print("wrote", args.o, "rows", len(rows))
    print("=== all-good XY ===")
    for s in doc["summary_all_good"]:
        print("  λ=%.2f  mean=%s  max=%s  max/T0=%s  bw=%.2f  acc=%.3f  stable=%s"
              % (s["lam"], s["mean_lat_med"], s["max_lat_med"],
                 s.get("max_over_t0_med"),
                 s["bw_eff_med"] or 0, s["accepted_med"] or 0, s["n_stable"]))
    print("=== partial Super-turn (med / worst mean_lat) ===")
    for s in doc["summary_partial"]:
        print("  λ=%.2f  mean %s/%s  max %s/%s  bw %s  stable %d/%d"
              % (s["lam"], s["mean_lat_med"], s["mean_lat_worst"],
                 s["max_lat_med"], s["max_lat_worst"],
                 s["bw_eff_med"], s["n_stable"], s["n"]))
    print("hw XY", {k: hw_xy[k] for k in
                    ("buffer_bits_per_router", "table_bits_per_router",
                     "sr_header_bits", "router_area_norm")})
    print("hw partial", doc["hw_partial_summary"])


if __name__ == "__main__":
    main()
