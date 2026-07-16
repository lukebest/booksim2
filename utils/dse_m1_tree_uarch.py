#!/usr/bin/env python3
"""Per-router microarchitecture DSE for m=1 tree allgather on 8x6 (H=7,V=9,rb=2).

Metrics per scheme (all from a verified strict rigid pack unless noted):
  * makespan          : strict 0-buffer feasible upper bound (down_cap=rb=2)
  * slot_entries      : calendar actions per router (max over routers)
  * issue_width       : max concurrent calendar input actions per (router,cycle)
  * topo_period_max   : slot-table topology config min repeat period (Pmax)
  * xbar_ramp_peak    : peak flits/cycle into one node's down-ramp exit
  * xbar_out_peak     : peak mesh+local outputs per (router,cycle)
  * burst_buf_c4      : eject queue depth needed if down_cap=4 pack is drained
                        at the physical rb=2 rate (strict pack needs 0)
  * mk_c4             : makespan of the down_cap=4 pack
  * delta2            : earliest second-round overlap - minimal shift D such
                        that the same schedule repeated at +D stays conflict
                        free (links<=1, ramps<=2).  k rounds cost mk+(k-1)*D.
  * area              : Arch-A5 analytic router area (IQ-XY=1.0), sparse
                        calendar depth 64 (48 entries), CalFork per ADR-005.

Extra schemes beyond axis_ccw/nec3/nec2 are registered in EXTRA_SCHEMES;
loop ticks append new candidates there.  Results accumulate in
results/tree_m1_uarch_dse.json.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import ppa_analytic_model as PPA
import sched_zerobuf_compare as S
import slide_metrics as SM
from dse_tree_allgather_6x8 import (
    MX, MY, H, V, N, RAMP, RAMP_BW,
    SCHEMES as BASE_SCHEMES, coord, footprint, nid, validate_tree,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "tree_m1_uarch_8x6_dse.json"


# ---------------------------------------------------------------------------
# Extra tree candidates (this loop's exploration set)
# ---------------------------------------------------------------------------
def dual_comb_tree(s: int) -> list[tuple[int, int]]:
    """Source row both directions; BOTH boundary columns as spines; each
    non-source row filled inward half from the left edge, half from the right.
    Boundary sources fall back to a single opposite-edge comb."""
    sx, sy = coord(s)
    e: list[tuple[int, int]] = []
    if sx in (0, MX - 1):
        edge = MX - 1 - sx
        step = 1 if edge > sx else -1
        x = sx
        while x != edge:
            e.append((nid(x, sy), nid(x + step, sy)))
            x += step
        for y in range(sy - 1, -1, -1):
            e.append((nid(edge, y + 1), nid(edge, y)))
        for y in range(sy + 1, MY):
            e.append((nid(edge, y - 1), nid(edge, y)))
        xs = range(1, MX) if edge == 0 else range(MX - 2, -1, -1)
        for y in range(MY):
            if y == sy:
                continue
            p = edge
            for x in xs:
                e.append((nid(p, y), nid(x, y)))
                p = x
        return e
    for x in range(sx - 1, -1, -1):
        e.append((nid(x + 1, sy), nid(x, sy)))
    for x in range(sx + 1, MX):
        e.append((nid(x - 1, sy), nid(x, sy)))
    for edge in (0, MX - 1):
        for y in range(sy - 1, -1, -1):
            e.append((nid(edge, y + 1), nid(edge, y)))
        for y in range(sy + 1, MY):
            e.append((nid(edge, y - 1), nid(edge, y)))
    mid = MX // 2
    for y in range(MY):
        if y == sy:
            continue
        for x in range(1, mid):
            e.append((nid(x - 1, y), nid(x, y)))
        for x in range(MX - 2, mid - 1, -1):
            e.append((nid(x + 1, y), nid(x, y)))
    return e


def col_comb_tree(s: int) -> list[tuple[int, int]]:
    """Transposed NEC-3: source column both ways, nearest horizontal boundary
    row as spine, per-column inward fill (V-spine variant)."""
    sx, sy = coord(s)
    edge = 0 if sy <= (MY - 1) // 2 else MY - 1
    e = []
    for y in range(sy - 1, -1, -1):
        e.append((nid(sx, y + 1), nid(sx, y)))
    for y in range(sy + 1, MY):
        e.append((nid(sx, y - 1), nid(sx, y)))
    for x in range(sx - 1, -1, -1):
        e.append((nid(x + 1, edge), nid(x, edge)))
    for x in range(sx + 1, MX):
        e.append((nid(x - 1, edge), nid(x, edge)))
    ys = range(1, MY) if edge == 0 else range(MY - 2, -1, -1)
    for x in range(MX):
        if x == sx:
            continue
        p = edge
        for y in ys:
            e.append((nid(x, p), nid(x, y)))
            p = y
    return e


EXTRA_SCHEMES = {
    "dual_comb": dual_comb_tree,
    "col_comb3": col_comb_tree,
}

SCHEME_SET = {
    "axis_ccw": BASE_SCHEMES["axis_ccw"],
    "nec3": BASE_SCHEMES["nec3"],
    "nec2": BASE_SCHEMES["nec2"],
    "dim_xy": BASE_SCHEMES["dim_xy"],
    "dim_yx": BASE_SCHEMES["dim_yx"],
    "hamilton_bi_tree": BASE_SCHEMES["hamilton_bi_tree"],
    **EXTRA_SCHEMES,
}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def pack_best(footprints: dict, down_cap: int | None):
    best = None
    for name, gen in S.SRC_ORDERS.items():
        try:
            order = gen()
        except TypeError:
            continue
        rec = S.export_events(footprints, RAMP_BW, order, flits=1,
                              down_cap=down_cap)
        if not S.verify(rec[2], RAMP_BW, flits=1, down_cap=down_cap):
            continue
        if best is None or rec[0] < best[0]:
            best = (*rec, name)
    return best


def delta2_min(busy, makespan: int) -> int:
    """Minimal shift D so schedule + (schedule shifted by D) is conflict free."""
    link_busy, up_busy, down_busy = busy
    link_sets = [set(d) for d in link_busy.values()]
    ramp_tables = list(up_busy.values()) + list(down_busy.values())
    for delta in range(1, makespan + 2):
        ok = True
        for cycles in link_sets:
            if any((t - delta) in cycles for t in cycles):
                ok = False
                break
        if ok:
            for tab in ramp_tables:
                if any(ct + tab.get(t - delta, 0) > RAMP_BW
                       for t, ct in tab.items()):
                    ok = False
                    break
        if ok:
            return delta
    return makespan + 1


def multi_round_free(footprints: dict, rounds: int):
    """Pack `rounds` independent 1-flit rounds; every (source, round) copy gets
    its own offset (rounds may interleave).  Returns (makespan, per-source
    2nd-flit overlap stats) for the best of round-major / source-major orders."""
    fp = {(s, r): footprints[s] for s in range(N) for r in range(rounds)}
    corner = sorted(range(N), key=lambda s: min(
        coord(s)[0] * H + coord(s)[1] * V,
        (MX - 1 - coord(s)[0]) * H + coord(s)[1] * V,
        coord(s)[0] * H + (MY - 1 - coord(s)[1]) * V,
        (MX - 1 - coord(s)[0]) * H + (MY - 1 - coord(s)[1]) * V), reverse=True)
    orders = {
        "round_major": [(s, r) for r in range(rounds) for s in corner],
        "source_major": [(s, r) for s in corner for r in range(rounds)],
    }
    best = None
    for oname, order in orders.items():
        mk, _, busy, offs, _ = S.export_events(fp, RAMP_BW, order, flits=1)
        if not S.verify(busy, RAMP_BW, flits=rounds):
            continue
        deltas = [offs[(s, 1)] - offs[(s, 0)] for s in range(N)] \
            if rounds >= 2 else [0]
        rec = (mk, oname, min(deltas), sum(deltas) / len(deltas), max(deltas))
        if best is None or mk < best[0]:
            best = rec
    return best


def steady_ii(busy, makespan: int, buffers=(0, 2, 4, 8, 16)) -> dict:
    """Sustainable per-round initiation interval II for an infinitely replayed
    slot table (rounds shifted by k*II, table stays at 48 entries).

    Feasibility at interval II:
      * every directed link's busy cycles are pairwise distinct mod II,
      * up-ramp folded occupancy <= RAMP_BW,
      * down-ramp folded arrivals, drained at RAMP_BW/cy, need a queue no
        deeper than the allowed burst buffer.
    Returns {buffer_depth: min_II}.
    """
    link_busy, up_busy, down_busy = busy
    link_sets = [sorted(d) for d in link_busy.values()]
    lb = max((N - 1 + RAMP_BW - 1) // RAMP_BW,
             max(len(c) for c in link_sets))

    def links_ok(ii: int) -> bool:
        for cycles in link_sets:
            seen = set()
            for t in cycles:
                r = t % ii
                if r in seen:
                    return False
                seen.add(r)
        return True

    def up_ok(ii: int) -> bool:
        for tab in up_busy.values():
            fold = defaultdict(int)
            for t, ct in tab.items():
                fold[t % ii] += ct
                if fold[t % ii] > RAMP_BW:
                    return False
        return True

    def down_queue_peak(ii: int) -> int:
        peak = 0
        for tab in down_busy.values():
            fold = [0] * ii
            for t, ct in tab.items():
                fold[t % ii] += ct
            occ = 0
            for tau in list(range(ii)) * 3:  # reach cyclic steady state
                occ = max(0, occ + fold[tau] - RAMP_BW)
                peak = max(peak, occ)
        return peak

    result = {}
    remaining = sorted(set(buffers))
    for ii in range(lb, makespan + 2):
        if not remaining:
            break
        if not links_ok(ii) or not up_ok(ii):
            continue
        need = down_queue_peak(ii)
        for b in list(remaining):
            if need <= b:
                result[b] = ii
                remaining.remove(b)
    for b in remaining:
        result[b] = None
    result["link_lb"] = max(len(c) for c in link_sets)
    return result


def _queue_peak(fold: list[int]) -> int:
    occ = peak = 0
    for a in fold * 3:
        occ = max(0, occ + a - RAMP_BW)
        peak = max(peak, occ)
    return peak


def cyclic_pack(footprints: dict, ii: int, buffer_depth: int, order):
    """Greedy modular pack: pick each source's offset in [0, II) so that link
    residues never collide, up-ramp folded load stays <= RAMP_BW and every
    down-ramp queue (drained at RAMP_BW/cy) stays <= buffer_depth.  Returns
    (first_round_makespan, offsets) or None."""
    link_res = defaultdict(set)
    up_fold = defaultdict(lambda: [0] * ii)
    down_fold = defaultdict(lambda: [0] * ii)
    offs = {}
    for s in order:
        slots = footprints[s]
        chosen = None
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
                if _queue_peak(fold) > buffer_depth:
                    ok = False
                    break
            if ok:
                chosen = o
                break
        if chosen is None:
            return None
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


def cyclic_ii_search(footprints: dict, link_lb: int,
                     buffers=(0, 2, 4, 8)) -> dict:
    """Minimal II achieved by the greedy cyclic pack per burst-buffer depth."""
    corner = sorted(range(N), key=lambda s: min(
        coord(s)[0] * H + coord(s)[1] * V,
        (MX - 1 - coord(s)[0]) * H + coord(s)[1] * V,
        coord(s)[0] * H + (MY - 1 - coord(s)[1]) * V,
        (MX - 1 - coord(s)[0]) * H + (MY - 1 - coord(s)[1]) * V),
        reverse=True)
    out = {}
    lb = max(link_lb, (N - 1 + RAMP_BW - 1) // RAMP_BW)
    for b in buffers:
        found = None
        for ii in range(lb, 6 * lb):
            res = cyclic_pack(footprints, ii, b, corner)
            if res is not None:
                found = {"ii": ii, "first_round_mk": res[0]}
                break
        out[str(b)] = found
    return out


def burst_buffer_needed(down_busy, drain: int = RAMP_BW) -> int:
    """Peak eject queue depth when arrivals are drained at `drain` flits/cy."""
    peak = 0
    for cycles in down_busy.values():
        occ = 0
        for t in range(min(cycles), max(cycles) + 1):
            occ = max(0, occ + cycles.get(t, 0) - drain)
            peak = max(peak, occ)
    return peak


def area_total(issue_width: int, fanout: int, burst_depth: int = 0) -> float:
    """Arch-A5 analytic area; optional per-router eject burst FIFO of `burst_depth`."""
    common = PPA.BASELINE_CROSSBAR + PPA.ARCH_A5_BUFFERS + PPA.ARCH_A5_CONTROL
    calendar = PPA.sparse_calendar_area(64) * issue_width
    mc = PPA.CALFORK_MC_DELTA if fanout > 1 else 0.0
    burst = 0.0
    if burst_depth > 0:
        burst = round(
            PPA.pool_buffers(PPA.ARCH_A5_INTERIOR_FLITS + burst_depth)
            - PPA.ARCH_A5_BUFFERS, 4)
    return round(common + calendar + mc + burst, 4)


def second_flit_by_down_cap(footprints: dict) -> dict:
    """Earliest 2nd-flit overlap under increasing down-ramp cycle caps.

    down_cap=rb models zero burst absorption; larger caps approximate a
    PE-side eject burst path that can accept >rb arrivals in a cycle
    (queue depth reported separately via burst_buf_*).
    """
    out = {}
    for dc in (2, 3, 4):
        fp = {(s, r): footprints[s] for s in range(N) for r in range(2)}
        corner = sorted(range(N), key=lambda s: min(
            coord(s)[0] * H + coord(s)[1] * V,
            (MX - 1 - coord(s)[0]) * H + coord(s)[1] * V,
            coord(s)[0] * H + (MY - 1 - coord(s)[1]) * V,
            (MX - 1 - coord(s)[0]) * H + (MY - 1 - coord(s)[1]) * V),
            reverse=True)
        best = None
        for oname, order in {
            "round_major": [(s, r) for r in range(2) for s in corner],
            "source_major": [(s, r) for s in corner for r in range(2)],
        }.items():
            mk, _, busy, offs, _ = S.export_events(
                fp, RAMP_BW, order, flits=1, down_cap=dc)
            if not S.verify(busy, RAMP_BW, flits=2, down_cap=dc):
                continue
            deltas = [offs[(s, 1)] - offs[(s, 0)] for s in range(N)]
            q = burst_buffer_needed(busy[2], drain=RAMP_BW)
            rec = {
                "makespan": mk, "order": oname,
                "second_flit_min": min(deltas),
                "second_flit_mean": round(sum(deltas) / len(deltas), 1),
                "queue_depth_if_drain_rb": q,
            }
            if best is None or mk < best["makespan"] or (
                    mk == best["makespan"]
                    and rec["second_flit_min"] < best["second_flit_min"]):
                best = rec
        out[str(dc)] = best
    return out


def evaluate(name: str, builder) -> dict:
    trees, footprints = {}, {}
    for s in range(N):
        edges = builder(s)
        chk = validate_tree(s, edges)
        if not chk["ok"]:
            raise ValueError(f"{name} s={s}: {chk['errors']}")
        trees[s] = chk
        footprints[s] = footprint(s, edges, chk)

    mk, _, busy, offs, events, order = pack_best(footprints, down_cap=None)
    link_busy, up_busy, down_busy = busy

    per_router = [0] * N
    concurrent = defaultdict(int)
    outputs = defaultdict(int)
    for s in range(N):
        dist, children = trees[s]["distance"], trees[s]["children"]
        for node in range(N):
            t = offs[s] + RAMP + dist[node]
            per_router[node] += 1
            concurrent[node, t] += 1
            fanout = len(children.get(node, [])) + (0 if node == s else 1)
            outputs[node, t] += fanout
    slot = SM.slot_table_depth(events, MX, MY, mk)

    c4 = pack_best(footprints, down_cap=4)
    mk4, busy4 = c4[0], c4[2]

    issue = max(concurrent.values())
    fan = max(t["max_mesh_fanout"] for t in trees.values())
    d2 = delta2_min(busy, mk)
    r2 = multi_round_free(footprints, 2)
    r5 = multi_round_free(footprints, 5)
    ii = steady_ii(busy, mk)
    cyc = cyclic_ii_search(footprints, ii["link_lb"])
    f2_dc = second_flit_by_down_cap(footprints)
    burst_need = burst_buffer_needed(busy4[2])
    return {
        "makespan": mk,
        "source_order": order,
        "slot_entries_max": max(per_router),
        "issue_width": issue,
        "topo_period_max": slot["max_period"],
        "topo_period_mean": round(slot["mean_period"], 3),
        "mesh_fanout_max": fan,
        "xbar_ramp_peak": max(ct for d in down_busy.values()
                              for ct in d.values()),
        "xbar_out_peak": max(outputs.values()),
        "burst_buf_strict": 0,
        "mk_c4": mk4,
        "burst_buf_c4": burst_need,
        "delta2": d2,
        "flits5_pipelined": mk + 4 * d2,
        "rounds2": {"makespan": r2[0], "order": r2[1],
                    "second_flit_min": r2[2],
                    "second_flit_mean": round(r2[3], 1),
                    "second_flit_max": r2[4]} if r2 else None,
        "rounds5_makespan": r5[0] if r5 else None,
        "second_flit_by_down_cap": f2_dc,
        "steady_ii_by_buffer": {str(k): v for k, v in ii.items()},
        "cyclic_pack_ii": cyc,
        "area": area_total(issue, fan, 0),
        "area_with_burst_c4": area_total(issue, fan, burst_need),
    }


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    prior_doc = {}
    if OUT.exists():
        prior_doc = json.loads(OUT.read_text(encoding="utf-8"))
    schemes = {}
    for name, builder in SCHEME_SET.items():
        rec = evaluate(name, builder)
        # keep prior CP-SAT pipeline numbers if present
        old = (prior_doc.get("schemes") or {}).get(name) or {}
        if "cpsat_pipeline" in old:
            rec["cpsat_pipeline"] = old["cpsat_pipeline"]
        schemes[name] = rec
        r2 = rec["rounds2"]
        f2 = (rec.get("second_flit_by_down_cap") or {}).get("2") or {}
        print(f"{name:18s} mk={rec['makespan']:4d} "
              f"slot={rec['slot_entries_max']} P={rec['topo_period_max']:2d} "
              f"ramp={rec['xbar_ramp_peak']} out={rec['xbar_out_peak']} "
              f"burst4={rec['burst_buf_c4']} "
              f"f2min={r2['second_flit_min'] if r2 else '-'} "
              f"f2@dc2={f2.get('second_flit_min', '-')} "
              f"cyc0={rec['cyclic_pack_ii'].get('0') and rec['cyclic_pack_ii']['0']['ii']} "
              f"area={rec['area']}", flush=True)

    # Rank and recommendation (m=1 oneshot + microarch friendliness)
    ranked = sorted(schemes.items(), key=lambda kv: (
        kv[1]["makespan"],
        kv[1]["topo_period_max"],
        kv[1]["xbar_out_peak"],
        kv[1]["burst_buf_c4"],
        kv[1]["area"],
    ))
    best = ranked[0][0]
    recommendation = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mesh": [MX, MY], "H": H, "V": V, "rb": RAMP_BW,
        "default_single_scheme": best,
        "primary": {
            "m1_oneshot": {
                "scheme": best,
                "makespan": schemes[best]["makespan"],
                "slot_entries_max": schemes[best]["slot_entries_max"],
                "xbar_ramp_peak": schemes[best]["xbar_ramp_peak"],
                "burst_buf_c4": schemes[best]["burst_buf_c4"],
                "second_flit_min": (schemes[best]["rounds2"] or {}).get(
                    "second_flit_min"),
                "area": schemes[best]["area"],
                "rationale": (
                    "Lowest m=1 makespan among validated trees; "
                    "prefer low Pmax/xbar/burst when tied"),
            },
        },
        "ranking_m1": [
            {"scheme": n, "makespan": r["makespan"],
             "slot": r["slot_entries_max"], "Pmax": r["topo_period_max"],
             "ramp_peak": r["xbar_ramp_peak"], "out_peak": r["xbar_out_peak"],
             "burst4": r["burst_buf_c4"],
             "f2min": (r["rounds2"] or {}).get("second_flit_min"),
             "area": r["area"]}
            for n, r in ranked
        ],
    }
    # Preserve CP-SAT regimes from prior if still present
    if "recommendation" in prior_doc:
        for k in ("pipelines_cpsat", "k_flit_regimes", "long_replay48",
                  "short_replay48", "runners_up"):
            if k in prior_doc["recommendation"]:
                recommendation[k] = prior_doc["recommendation"][k]
        prev_p = prior_doc["recommendation"].get("primary") or {}
        for k in ("short_replay48", "long_replay48"):
            if k in prev_p:
                recommendation["primary"][k] = prev_p[k]

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {"mesh": [MX, MY], "H": H, "V": V, "rb": RAMP_BW,
                  "m": 1, "strict_zero_buffer": True},
        "schemes": schemes,
        "recommendation": recommendation,
    }, indent=2), encoding="utf-8")
    print(f"BEST m=1: {best} mk={schemes[best]['makespan']}")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
