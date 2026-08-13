#!/usr/bin/env python3
"""Lower bounds, initiation interval and bandwidth utilization for the six
collectives on the 8x6 **bufferless folded 2D torus**.

The fabric's link set is exactly a folded 8x6 torus (row rings + column rings,
every node a bridge, verified against `rg_topo`'s torus in
verify_ring_collectives_8x6.py); what is NOT a torus is the node, which has no
buffers and no crossbar, only a ring station. So the structural bounds below are
torus bounds, and both transports -- the paper's deflection mechanism and the
static calendar -- have to obey them.

Two bounds, not one. Reporting a single "lower bound" is what made the earlier
tables confusing, because the two questions have different answers:

  capacity bound   work / rate on the busiest resource: the cut, the core's ring
                   ports, the L1 ramp. This bounds the INITIATION INTERVAL of a
                   pipelined stream of collectives, and it is model-agnostic --
                   an arc passes one flit per sigma cycles and an L1 moves
                   RAMP_BW flits per cycle under either transport.
  latency floor    the longest src->dst shortest-path delay the pattern forces,
                   plus the (m-1)*sigma the tail flits cost on the last arc.
                   This bounds ONE instance's makespan and says nothing about
                   throughput.

  makespan_lb = max(capacity, latency)        II_lb = capacity

At m=1 the six collectives are latency bound by a wide margin and no schedule can
fix that; under pipelining the latency floor drops out entirely and II is what
the hardware is actually asked to deliver. That is why the 1-flit makespan and
the II have to be quoted as two numbers rather than averaged into one.

The floors here are deliberately the WEAKEST ones that survive every algorithm,
which is what makes them safe to plot next to measurements. In particular relays
are allowed: a core may forward what it received, and may combine it with its own
data first, because that is arithmetic in an AI core's L1 and no transport
feature is needed for it. Two consequences worth stating out loud:

  * the floors are the same at T0 and T1. Arc multicast and L1 reduction do not
    move them -- relaying already achieves one crossing per distinct item, and
    local combining already collapses a reduction. What T1 buys is reaching the
    floor with fewer phases and less port pressure, which shows up in the
    measurements, not in the bound.
  * they are weaker than the demand model in `rg_ring_attach` (which charges the
    flat algorithm's fan-out, no relaying, so that attachment schemes can be
    ranked against one traffic convention). Tree algorithms legitimately finish
    below that one; nothing may finish below these.

Measured, for every (pattern, algo, tier):

  T1        single-instance makespan
  T_R       makespan of R instances packed freely (`multiround`)
  II        (T_R - T1) / (R - 1), the same definition the T_avg study uses. It
            is an interpolation parameter (it makes T_avg = (T1+T_R)/2 exact),
            not an asymptote, so at finite R it can sit a few percent BELOW the
            capacity floor -- the first instance already did some of the work
            that is being amortised away. Measured worst case here is 3%.
  per_round T_R / R, the amortised cost of one instance in the pipeline. This is
            the one that must clear the floor, and it does, everywhere.
  util      arc-busy cycles / (192 arcs * makespan) over the whole R-round run:
            directly measured, so it is <= 1 by construction. One formula for
            both legs -- the calendar sums its footprints, the sim counts every
            hop it actually takes, deflection detours included. `useful_util`
            recounts the same run with every flit charged its minimum hop count,
            so util - useful_util is bandwidth spent on deflection rather than
            on delivery.

Outputs results/ring_throughput_8x6.json.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from dse_ring_collectives_8x6 import ROOT, run_base_collective
from rg_ring_calendar import build_calendar
from rg_ring_collectives import (
    all_configs, build_ring_collective, multiround, replay,
)
from rg_ring_topo import RingTopology
from rg_topo import RAMP_BW, coord

OUT = Path(__file__).resolve().parent.parent / "results"
ROUNDS: tuple[int, ...] = (1, 5, 13)
M_LIST: tuple[int, ...] = (1, 13)
N_ITEMS = 47                   # items a core owes / is owed, = N - 1
PATTERNS = ["broadcast", "reduce", "gather", "allreduce", "allgather",
            "alltoall"]


# ---------------------------------------------------------------------------
# 1. Structural capacity: cuts and per-core rates
# ---------------------------------------------------------------------------

def cut_capacity(topo: RingTopology) -> dict[str, list[dict[str, Any]]]:
    """Directed segments crossing every straight cut, per axis.

    A row ring crosses a vertical cut twice -- once on a regular segment, once
    on its wrap -- which is where the folded torus's second lane comes from and
    why the cut is 2*my per direction instead of the mesh's my.
    """
    out: dict[str, list[dict[str, Any]]] = {"x": [], "y": []}
    for axis, lim in (("x", topo.mx), ("y", topo.my)):
        for k in range(1, lim):
            lo = lambda n, k=k, axis=axis: (              # noqa: E731
                coord(n, topo.mx)[0] < k if axis == "x"
                else coord(n, topo.mx)[1] < k)
            pos = [e for e in topo.directed_links if lo(e[0]) and not lo(e[1])]
            neg = [e for e in topo.directed_links if not lo(e[0]) and lo(e[1])]
            out[axis].append({"at": k, "segments_pos": len(pos),
                              "segments_neg": len(neg),
                              "cap_per_dir": min(len(pos), len(neg)) / topo.sigma,
                              "nodes_low": sum(1 for n in range(topo.n)
                                               if lo(n))})
    return out


def hop_latency(topo: RingTopology, s: int, d: int) -> int:
    """Zero-contention delay of the shortest s->d path, turns free.

    Turns are free on purpose: the sim crosses a bridge for free and the
    calendar charges t_turn, so the turn-free number is the one floor that is
    valid for both. Every row hop costs H and every column hop costs V whichever
    way round the ring it goes -- that uniformity is what folding buys.
    """
    sx, sy = coord(s, topo.mx)
    dx, dy = coord(d, topo.mx)
    hx = min((dx - sx) % topo.mx, (sx - dx) % topo.mx)
    hy = min((dy - sy) % topo.my, (sy - dy) % topo.my)
    return hx * topo.H + hy * topo.V


def latency_floor(topo: RingTopology, pattern: str, m: int) -> dict[str, Any]:
    """Longest shortest-path delay the pattern forces, + the tail's (m-1)*sigma.

    Relaying through an intermediate cannot beat it: shortest-path delay is a
    metric, so any store-and-forward chain is at least as long as the direct
    path. The tail term is the m flits of one message serializing onto the last
    arc at one flit per sigma.
    """
    nodes = range(topo.n)
    if pattern in ("broadcast",):
        d = max(hop_latency(topo, ROOT, v) for v in nodes)
        wit = f"root {ROOT} 到最远节点"
    elif pattern in ("reduce", "gather"):
        d = max(hop_latency(topo, v, ROOT) for v in nodes)
        wit = f"最远节点到 root {ROOT}"
    else:
        d = max(hop_latency(topo, u, v) for u in nodes for v in nodes)
        wit = "全对全，取直径"
    return {"distance_cy": d, "tail_cy": (m - 1) * topo.sigma,
            "latency_floor": d + (m - 1) * topo.sigma, "witness": wit}


def must_cross(pattern: str, a: int, b: int) -> int:
    """Distinct items that must cross a cut, from the `a` side to the `b` side.

    Relaying is allowed, so an item crosses ONCE and is re-sent on the far side;
    that is why only alltoall keeps a product term. Its messages are distinct per
    (src, dst) pair, so a*b of them are pinned to the crossing and no amount of
    forwarding or copying can merge them.
    """
    if pattern == "alltoall":
        return a * b
    if pattern in ("allgather",):
        return a                       # each item crosses once, then relays
    if pattern == "gather":
        return b                       # b distinct items owe the root side
    if pattern in ("broadcast", "reduce", "allreduce"):
        return 1                       # one copy / one partial sum suffices
    raise ValueError(pattern)


def must_absorb(pattern: str) -> tuple[int, int]:
    """(items a core must originate, items a core must end up holding), m=1.

    Only the eject side ever bites: a core that must HOLD 47 distinct items has
    to write all 47 through its L1 port however they arrived, and neither
    multicast nor reduction can forge them. Where the result is a single value
    (broadcast, reduce, allreduce) an upstream core can hand it over as one
    item, so the floor is 1 -- weak on purpose.
    """
    k = N_ITEMS
    if pattern == "alltoall":
        return k, k                    # distinct payload per (src, dst)
    if pattern in ("allgather",):
        return 1, k
    if pattern == "gather":
        return 1, k                    # k at the root only
    if pattern in ("broadcast", "reduce", "allreduce"):
        return 1, 1
    raise ValueError(pattern)


def capacity_bound(topo: RingTopology, pattern: str, m: int, *,
                   cuts: dict[str, list[dict[str, Any]]], ports: int = 2
                   ) -> dict[str, Any]:
    """max(cut, core port, L1 ramp) in cycles -- the II floor, model-agnostic.

    Tier does not appear: see the module docstring. These floors hold for T0 and
    T1 alike, so the same horizontal line can be drawn under both.
    """
    rx, ry = coord(ROOT, topo.mx)
    cut_lb = 0
    wit = None
    for axis in ("x", "y"):
        for row in cuts[axis]:
            lo = row["nodes_low"]
            root_lo = (rx < row["at"]) if axis == "x" else (ry < row["at"])
            a, b = (lo, topo.n - lo) if root_lo else (topo.n - lo, lo)
            need = must_cross(pattern, a, b) * m
            lb = math.ceil(need / row["cap_per_dir"])
            if lb > cut_lb:
                cut_lb = lb
                wit = (f'{axis}={row["at"]}：容量 {row["cap_per_dir"]:g} '
                       f'flit/cy，必须过 {need} flit')
    inj, ej = must_absorb(pattern)
    inj, ej = inj * m, ej * m
    port_lb = math.ceil(max(inj, ej) * topo.sigma / ports)
    ramp_lb = math.ceil(max(inj, ej) / RAMP_BW)
    cand = {"cut": cut_lb, "port": port_lb, "ramp": ramp_lb}
    return {"cut_lb": cut_lb, "cut_witness": wit,
            "max_originate": inj, "max_absorb": ej,
            "port_lb": port_lb, "ramp_lb": ramp_lb,
            "capacity_lb": max(cand.values()),
            "binding_capacity": max(cand, key=lambda k: cand[k])}


def theory(topo: RingTopology) -> list[dict[str, Any]]:
    cuts = cut_capacity(topo)
    out: list[dict[str, Any]] = []
    for pattern in PATTERNS:
        for m in M_LIST:
            cap = capacity_bound(topo, pattern, m, cuts=cuts)
            lat = latency_floor(topo, pattern, m)
            mk = max(cap["capacity_lb"], lat["latency_floor"])
            out.append({
                "pattern": pattern, "m": m,
                **cap, **{f"lat_{k}": v for k, v in lat.items()},
                "II_lb": cap["capacity_lb"],
                "makespan_lb": mk,
                "binding": ("latency"
                            if lat["latency_floor"] >= cap["capacity_lb"]
                            else cap["binding_capacity"]),
                "latency_frac": round(lat["latency_floor"] / max(1, mk), 3),
            })
    return out


# ---------------------------------------------------------------------------
# 2. Measured: II and utilization, both legs
# ---------------------------------------------------------------------------

def util_from(link_busy: dict[Any, int], topo: RingTopology, span: float,
              rounds: int) -> dict[str, Any]:
    """Arc-busy cycles over (192 arcs * span), measured over the whole run.

    `span` is that run's own makespan, so the ratio is a measured duty cycle and
    cannot exceed 1. Dividing per-round work by II instead would be the
    asymptotic version, and at small R it prints >100% -- II is still being
    approached from below there, so that form is not reported.
    """
    n = len(topo.directed_links)
    tot = sum(link_busy.values())
    peak = max(link_busy.values()) if link_busy else 0
    span = max(1.0, float(span))
    return {"link_cycles": tot, "round_link_cycles": round(tot / rounds, 1),
            "n_links_used": len(link_busy),
            "global_util": round(tot / (n * span), 4),
            "critical_arc_util": round(peak / span, 4),
            "critical_arc_cycles": peak}


def cal_leg(topo: RingTopology, pattern: str, algo: str, tier: str, m: int
            ) -> dict[str, Any]:
    base = build_ring_collective(topo, pattern, m=m, tier=tier, algo=algo,
                                 root=ROOT, bidir=True)
    if not replay(base)["ok"]:
        raise AssertionError(f"replay failed: {pattern}/{algo}/{tier}")
    by: dict[str, Any] = {}
    t1 = None
    for R in ROUNDS:
        cal = build_calendar(topo, base if R == 1 else multiround(base, R))
        per_link: dict[Any, int] = {}
        for fp in cal.fps.values():
            for e, _ in fp.links:
                per_link[e] = per_link.get(e, 0) + fp.dur
        if R == 1:
            t1 = cal.makespan
        ii = None if R == 1 else round((cal.makespan - t1) / (R - 1), 2)
        by[str(R)] = {"T_R": cal.makespan, "II_eff": ii,
                      "per_round": round(cal.makespan / R, 2),
                      "T_avg": round((t1 + cal.makespan) / 2, 1),
                      "util": util_from(per_link, topo, cal.makespan, R)}
    return {"leg": "calendar", "T1": t1, "by_rounds": by}


def base_leg(topo: RingTopology, pattern: str, algo: str, tier: str, m: int
             ) -> dict[str, Any]:
    """The paper mechanism, no schedule. T1 has no meaning here (unicast only)."""
    if tier == "T1":
        return {"leg": "ring_base", "T1": None,
                "skipped": "paper 机制只有 unicast，T1 行无可比基线"}
    base = build_ring_collective(topo, pattern, m=m, tier=tier, algo=algo,
                                 root=ROOT, bidir=True)
    by: dict[str, Any] = {}
    t1 = None
    for R in ROUNDS:
        col = base if R == 1 else multiround(base, R)
        lb: dict[Any, int] = {}
        r = run_base_collective(topo, col, link_busy=lb)
        if R == 1:
            t1 = r["makespan"]
        ii = None if R == 1 else round((r["makespan"] - t1) / (R - 1), 2)
        by[str(R)] = {
            "T_R": r["makespan"], "II_eff": ii,
            "per_round": round(r["makespan"] / R, 2),
            "T_avg": round((t1 + r["makespan"]) / 2, 1),
            "completed": r["completed"],
            "deflect_per_flit": r["deflect_per_flit"],
            "n_out_of_order": r["n_out_of_order"],
            "max_reasm_occupancy": r["max_reasm_occupancy"],
            "n_reasm_overflow": r["n_reasm_overflow"],
            "util": util_from(lb, topo, r["makespan"], R),
        }
    return {"leg": "ring_base", "T1": t1, "by_rounds": by}


def useful_link_cycles(topo: RingTopology, pattern: str, algo: str, tier: str,
                       m: int) -> int:
    """Minimal-hop arc cycles this flow set needs, deflection-free.

    Per transfer, not per delivery: a copy-and-continue multicast occupies one
    arc no matter how many readers sit on it, so the reference charges the
    farthest reader only. That keeps this quantity a floor on occupancy for both
    tiers, and the gap to what a leg actually spends is then unambiguously
    waste -- deflection detours for `ring_base`, nothing at all for the calendar.
    """
    col = build_ring_collective(topo, pattern, m=m, tier=tier, algo=algo,
                                root=ROOT, bidir=True)

    def hops(s: int, d: int) -> int:
        sx, sy = coord(s, topo.mx)
        dx, dy = coord(d, topo.mx)
        return (min((dx - sx) % topo.mx, (sx - dx) % topo.mx)
                + min((dy - sy) % topo.my, (sy - dy) % topo.my))
    tot = 0
    for x in col.xfers:
        h = (hops(x.src, x.dsts[0]) if len(x.dsts) == 1
             else max(hops(x.src, d) for d in x.dsts))
        tot += x.nflit * h * topo.sigma
    return tot


def sweep(topo: RingTopology) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern, algo, tier in all_configs(PATTERNS):
        for m in M_LIST:
            t0 = time.perf_counter()
            cal = cal_leg(topo, pattern, algo, tier, m)
            bs = base_leg(topo, pattern, algo, tier, m)
            row = {"pattern": pattern, "algo": algo, "tier": tier, "m": m,
                   "calendar": cal, "ring_base": bs,
                   "useful_link_cycles": useful_link_cycles(
                       topo, pattern, algo, tier, m),
                   "wall_s": round(time.perf_counter() - t0, 2)}
            b13 = bs["by_rounds"]["13"] if bs["T1"] else None
            c13 = cal["by_rounds"]["13"]
            u = max(1, row["useful_link_cycles"])
            n_arc = len(topo.directed_links)
            for leg, tag in ((cal, "cal"), (bs, "base")):
                if leg["T1"] is None:
                    continue
                for R, v in leg["by_rounds"].items():
                    v["util"]["useful_global"] = round(
                        u * int(R) / (n_arc * v["T_R"]), 4)
                    v["util"]["hop_tax"] = round(
                        v["util"]["round_link_cycles"] / u, 4)
                row[f"{tag}_hop_tax"] = leg["by_rounds"]["13"]["util"]["hop_tax"]
            rows.append(row)
            print(f'  m={m:<3} {pattern:10} {algo:17} {tier} '
                  f'cal T1={cal["T1"]:>6} pr={c13["per_round"]:>8} '
                  f'U={c13["util"]["global_util"]:.3f} | '
                  f'base T1={bs["T1"] if bs["T1"] else "-":>6} '
                  f'pr={b13["per_round"] if b13 else "-":>8} '
                  f'U={b13["util"]["global_util"] if b13 else 0:.3f} '
                  f'(useful {b13["util"]["useful_global"] if b13 else 0:.3f}) '
                  f'{row["wall_s"]:.1f}s', flush=True)
    return rows


def headline(rows: list[dict[str, Any]], th: list[dict[str, Any]]
             ) -> list[dict[str, Any]]:
    """Per pattern, the best each leg can do, and how far each is from its floor.

    "Best" is per leg and per metric: the algorithm that minimises T1 is often
    not the one that minimises II (the rotation schedules are the extreme case),
    and hiding that behind one winner is how a fabric gets mis-sized.
    """
    def pick(rs: list[dict[str, Any]], leg: str) -> dict[str, Any] | None:
        cand = [r for r in rs if r[leg]["T1"] is not None]
        if not cand:
            return None
        bt = min(cand, key=lambda r: r[leg]["T1"])
        bi = min(cand, key=lambda r: r[leg]["by_rounds"]["13"]["per_round"])
        b13, b1 = bi[leg]["by_rounds"]["13"], bi[leg]["by_rounds"]["1"]
        e = {
            "best_T1": {"algo": bt["algo"], "tier": bt["tier"],
                        "T1": bt[leg]["T1"],
                        "util": (bt[leg]["by_rounds"]["1"]["util"]
                                 ["global_util"]),
                        "crit": (bt[leg]["by_rounds"]["1"]["util"]
                                 ["critical_arc_util"])},
            "best_II": {"algo": bi["algo"], "tier": bi["tier"],
                        "II": b13["II_eff"], "T13": b13["T_R"],
                        "per_round": b13["per_round"],
                        "util": b13["util"]["global_util"],
                        "useful_util": b13["util"]["useful_global"],
                        "crit": b13["util"]["critical_arc_util"],
                        "hop_tax": b13["util"]["hop_tax"],
                        "util_1shot": b1["util"]["global_util"]},
        }
        if leg == "ring_base":
            e["best_II"]["deflect_per_flit"] = b13["deflect_per_flit"]
            e["best_II"]["max_reasm_occupancy"] = b13["max_reasm_occupancy"]
        return e

    out: list[dict[str, Any]] = []
    for pat in PATTERNS:
        for m in M_LIST:
            rs = [r for r in rows if r["pattern"] == pat and r["m"] == m]
            t = next(x for x in th if x["pattern"] == pat and x["m"] == m)
            rec: dict[str, Any] = {
                "pattern": pat, "m": m,
                "bound": {"makespan_lb": t["makespan_lb"], "II_lb": t["II_lb"],
                          "binding": t["binding"],
                          "latency_floor": t["lat_latency_floor"]}}
            legs = {"cal_T0": pick([r for r in rs if r["tier"] == "T0"],
                                   "calendar"),
                    "cal_T1": pick(rs, "calendar"),
                    "base": pick(rs, "ring_base")}
            for name, e in legs.items():
                if not e:
                    continue
                e["T1_over_lb"] = round(
                    e["best_T1"]["T1"] / max(1, t["makespan_lb"]), 3)
                e["per_round_over_lb"] = round(
                    e["best_II"]["per_round"] / max(1, t["II_lb"]), 3)
                rec[name] = e
            b, c = legs["base"], legs["cal_T0"]
            if b and c:
                rec["base_over_cal_T0"] = {
                    "T1": round(b["best_T1"]["T1"] / c["best_T1"]["T1"], 3),
                    "per_round": round(b["best_II"]["per_round"]
                                       / c["best_II"]["per_round"], 3)}
            out.append(rec)
    return out


def main() -> None:
    topo = RingTopology()
    th = theory(topo)
    print("theory: pattern m  cut port ramp latency -> makespan_lb / II_lb")
    for t in th:
        print(f'  {t["pattern"]:10} m={t["m"]:<3} '
              f'cut={t["cut_lb"]:>4} port={t["port_lb"]:>4} '
              f'ram={t["ramp_lb"]:>4} lat={t["lat_latency_floor"]:>4} '
              f'-> mk={t["makespan_lb"]:>4} II={t["II_lb"]:>4} '
              f'({t["binding"]})')
    print("\nmeasured:")
    t0 = time.perf_counter()
    rows = sweep(topo)
    hl = headline(rows, th)
    doc = {
        "topology": {**topo.audit(),
                     "reading": "无缓冲折叠 2D torus：链路集与折叠 torus 相同，"
                                "节点是无缓冲环站而非 torus 路由器"},
        "root": ROOT, "rounds": list(ROUNDS), "m_list": list(M_LIST),
        "definitions": {
            "II_eff": "(T_R - T1)/(R-1)，R 轮自由流水打包实测；它是让 "
                      "T_avg=(T1+T_R)/2 成立的插值参数，不是渐近值，"
                      "有限 R 下可低于容量界（R=5 最狠 0.73×，R=13 收窄到 "
                      "0.91×），所以校验下界要用 per_round 而不是它",
            "per_round": "T_R / R，流水下一轮的均摊时间；这一项恒 >= 容量界",
            "II_lb": "容量界 max(cut, 核端口, L1 ramp)；允许中继与本地合并，"
                     "因此 T0/T1 同界，任何算法都不得低于它",
            "makespan_lb": "max(容量界, 时延地板)",
            "util": "该次运行自身口径：弧占用周期 / (192 * 该次 makespan)，"
                    "R=1 为单发、R=13 为流水稳态，构造上 <= 1",
            "hop_tax": "实际弧占用 / 最小跳数弧占用，>1 即偏转或绕路的浪费",
        },
        "cuts": cut_capacity(topo),
        "theory": th, "rows": rows, "headline": hl,
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    p = OUT / "ring_throughput_8x6.json"
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {p}  ({p.stat().st_size / 1024:.0f} KB, "
          f"{doc['wall_s']:.0f}s)")


if __name__ == "__main__":
    main()
