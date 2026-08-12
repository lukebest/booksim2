#!/usr/bin/env python3
"""T1 / T_R / II_eff / T_avg for ring collectives at R = 1, 5, 13 rounds.

Same definition as the mesh trees in `dse_multiflit_area_makespan`:

    II_eff = (T_R - T1) / (R - 1)          from a FREE multi-round pack
    T_avg  = (T1 + T_R) / 2  =  T1 + (R-1)/2 * II_eff

"Free" is the load-bearing word. T_R is measured by packing R rounds together
with no barrier between rounds, not extrapolated from T1 plus a link-reuse
figure. Round 2 gets to use the slack round 1 left behind, and on the ring there
is a lot of it: a rotation step occupies each segment for one cycle out of the
eleven the hop delay takes, so the analytic estimate would be badly wrong.

The rotation utilization claim is checked here rather than asserted. A single
rotation round uses each segment of its cycle exactly once, which sounds like
perfect packing, but one round is 47 serial hops and each segment is busy for 1
cycle in 12. Utilization can only approach 1 if enough rounds are in flight to
fill the pipeline, so the honest test is to sweep R and watch the critical-arc
utilization, which is what `rotation_utilization` does.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

from rg_ring_calendar import build_calendar
from rg_ring_collectives import build_ring_collective, multiround
from rg_ring_topo import RingTopology, verify_dr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "ring_tavg_8x6.json"
MESH_JSON = ROOT / "results" / "multiflit_area_makespan.json"

ROUNDS_LIST = (1, 5, 13)
ROOT_NODE = 27

# The candidate calendars worth carrying through the pipelining study. Each is
# one of the four structural levers, so a reversal in the R sweep points at a
# lever rather than at a tuning accident.
CANDIDATES: tuple[tuple[str, str, str, int], ...] = (
    ("allgather", "flat", "T0", 1),
    ("allgather", "dim_2phase", "T0", 1),
    ("allgather", "dim_2phase", "T1", 1),
    ("allgather", "ring_rotate", "T0", 1),
    ("allgather", "halving_doubling", "T0", 1),
    ("allgather", "dim_2phase", "T1", 2),
    ("allgather", "flat", "T0", 2),
)


def tavg_curve(topo: RingTopology, pattern: str, algo: str, tier: str, *,
               m: int = 1, rounds: Sequence[int] = ROUNDS_LIST,
               root: int | None = ROOT_NODE) -> dict[str, Any]:
    base = build_ring_collective(topo, pattern, m=m, tier=tier, algo=algo,
                                 root=root)
    cal1 = build_calendar(topo, base)
    t1 = cal1.makespan
    by: dict[str, Any] = {}
    for R in rounds:
        if R == 1:
            by["1"] = {"T_R": t1, "II_eff": None, "T_avg": t1,
                       "util": cal1.utilization(topo),
                       "conflict_free": verify_dr(topo, cal1.items
                                                  )["conflict_free"]}
            continue
        col = multiround(base, R)
        cal = build_calendar(topo, col)
        v = verify_dr(topo, cal.items)
        by[str(R)] = {
            "T_R": cal.makespan,
            "II_eff": round((cal.makespan - t1) / (R - 1), 2),
            "T_avg": round((t1 + cal.makespan) / 2, 1),
            "util": cal.utilization(topo),
            "makespan_lb": cal.bounds["makespan_lb"],
            "binding_lb": cal.bounds["binding_lb"],
            "conflict_free": v["conflict_free"],
        }
    return {"pattern": pattern, "algo": algo, "tier": tier, "m": m,
            "T1": t1, "by_rounds": by,
            "T1_lb": cal1.bounds["makespan_lb"],
            "T1_binding_lb": cal1.bounds["binding_lb"]}


def rotation_utilization(topo: RingTopology, *,
                         rounds: Sequence[int] = (1, 2, 5, 13, 26, 47)
                         ) -> dict[str, Any]:
    """Does a rotation calendar reach the arc-load bound once pipelined?

    Predicted: yes, because a rotation round uses every segment of its
    Hamiltonian cycle exactly once, so R rounds packed back to back should
    saturate those segments. The failure mode to watch for is the barrier: 47
    barriered steps mean round r+1's step 0 cannot start until round r's step 0
    finishes, which caps utilization at 1/hop_delay however many rounds are in
    flight.
    """
    out: list[dict[str, Any]] = []
    base = build_ring_collective(topo, "allgather", m=1, tier="T0",
                                 algo="ring_rotate")
    t1 = build_calendar(topo, base).makespan
    for R in rounds:
        cal = build_calendar(topo, multiround(base, R))
        u = cal.utilization(topo)
        out.append({
            "rounds": R, "makespan": cal.makespan,
            "II_eff": (round((cal.makespan - t1) / (R - 1), 2) if R > 1
                       else None),
            "critical_arc_util": u["critical_arc_util"],
            "global_util": u["global_util"],
            "used_link_util": u["used_link_util"],
            "n_links_used": u["n_links_used"],
            "arc_load_lb": cal.bounds["arc_load_lb"],
            "makespan_lb": cal.bounds["makespan_lb"],
            "binding_lb": cal.bounds["binding_lb"],
        })
    return {"rows": out,
            "reaches_unit_utilization": any(
                r["critical_arc_util"] >= 0.99 for r in out),
            "best_critical_arc_util": max(r["critical_arc_util"] for r in out)}


def mesh_reference() -> dict[str, Any]:
    """Best mesh tree per R from `multiflit_area_makespan.json`.

    Read rather than recomputed: the mesh numbers belong to the mesh study and
    duplicating its packer here would be a second implementation of the same
    metric, which is how two "same" numbers start disagreeing.
    """
    if not MESH_JSON.exists():
        return {"available": False,
                "reason": f"{MESH_JSON.name} not found; run "
                          "dse_multiflit_area_makespan.py first"}
    data = json.loads(MESH_JSON.read_text())
    pts = data.get("points", [])
    if not pts or "by_rounds" not in pts[0]:
        return {"available": False,
                "reason": "multiflit_area_makespan.json predates the R sweep; "
                          "rerun dse_multiflit_area_makespan.py"}
    per_scheme: dict[str, Any] = {}
    for p in pts:
        br = p.get("by_rounds") or {}
        rec = per_scheme.setdefault(p["scheme"], {"label": p.get("label"),
                                                  "by_rounds": {}})
        for R, v in br.items():
            if not v:
                continue
            cur = rec["by_rounds"].get(R)
            if cur is None or v["T_avg"] < cur["T_avg"]:
                rec["by_rounds"][R] = {**v, "W": p["W"], "E": p["E"],
                                       "B": p["B"],
                                       "area_total": p["area_total"]}
    return {"available": True, "rounds_list": data["model"].get("rounds_list"),
            "schemes": per_scheme}


def main() -> None:
    topo = RingTopology()
    t_start = time.perf_counter()
    print("=== ring T_avg at R = 1 / 5 / 13 ===")
    rows: list[dict[str, Any]] = []
    print(f"{'pattern':10} {'algo':17} {'tier':4} {'ports':5} "
          + " ".join(f"{'R=' + str(R):>22}" for R in ROUNDS_LIST))
    for pattern, algo, tier, ports in CANDIDATES:
        tp = RingTopology(board_ports=ports, leave_ports=ports)
        t0 = time.perf_counter()
        rec = tavg_curve(tp, pattern, algo, tier)
        rec["ports"] = ports
        rows.append(rec)
        cells = []
        for R in ROUNDS_LIST:
            v = rec["by_rounds"][str(R)]
            cells.append(f"T={v['T_R']:>5} II={str(v['II_eff']):>6} "
                         f"avg={v['T_avg']:>6}")
        print(f"{pattern:10} {algo:17} {tier:4} {ports:5} "
              + " ".join(f"{c:>22}" for c in cells)
              + f"  {time.perf_counter()-t0:.1f}s", flush=True)

    print("\n--- does a pipelined rotation reach the arc-load bound? ---")
    rot = rotation_utilization(topo)
    for r in rot["rows"]:
        print(f"  R={r['rounds']:<3} mk={r['makespan']:>6} "
              f"II={str(r['II_eff']):>7} crit_util={r['critical_arc_util']:<7} "
              f"global={r['global_util']:<7} used={r['used_link_util']:<7} "
              f"bind={r['binding_lb']}")
    print(f"  reaches 1.0: {rot['reaches_unit_utilization']} "
          f"(best {rot['best_critical_arc_util']})")

    mesh = mesh_reference()
    print("\n--- mesh reference (same T_avg definition) ---")
    if mesh["available"]:
        for name, rec in mesh["schemes"].items():
            cells = []
            for R in ROUNDS_LIST:
                v = rec["by_rounds"].get(str(R))
                cells.append("n/a" if v is None else
                             f"T={v['T_R']:>5} II={str(v['II_eff']):>6} "
                             f"avg={v['T_avg']:>6}")
            print(f"  {rec['label'] or name:16} "
                  + " ".join(f"{c:>22}" for c in cells))
    else:
        print(f"  {mesh['reason']}")

    # Ring vs mesh at each R, on the collective both studies compute: allgather
    cmp_rows: list[dict[str, Any]] = []
    for R in ROUNDS_LIST:
        best_ring = min(rows, key=lambda r: r["by_rounds"][str(R)]["T_avg"])
        entry: dict[str, Any] = {
            "R": R,
            "ring_best": {
                "algo": best_ring["algo"], "tier": best_ring["tier"],
                "ports": best_ring["ports"],
                **{k: best_ring["by_rounds"][str(R)][k]
                   for k in ("T_R", "II_eff", "T_avg")}},
        }
        if mesh["available"]:
            cands = [(n, v) for n, rec in mesh["schemes"].items()
                     for v in [rec["by_rounds"].get(str(R))] if v]
            if cands:
                name, v = min(cands, key=lambda t: t[1]["T_avg"])
                entry["mesh_best"] = {"scheme": name,
                                      **{k: v[k] for k in
                                         ("T_R", "II_eff", "T_avg")}}
                entry["ring_over_mesh_T_avg"] = round(
                    entry["ring_best"]["T_avg"] / v["T_avg"], 3)
        cmp_rows.append(entry)

    print("\n--- ring vs mesh, allgather T_avg ---")
    for e in cmp_rows:
        rb = e["ring_best"]
        mb = e.get("mesh_best")
        print(f"  R={e['R']:<3} ring {rb['algo']}/{rb['tier']}/p{rb['ports']}"
              f" T_avg={rb['T_avg']:<8} "
              + (f"mesh {mb['scheme']} T_avg={mb['T_avg']:<8} "
                 f"ring/mesh={e['ring_over_mesh_T_avg']}" if mb else
                 "mesh n/a"))

    payload = {
        "definition": "II_eff=(T_R-T1)/(R-1) from a free multi-round rigid "
                      "pack; T_avg=(T1+T_R)/2",
        "rounds_list": list(ROUNDS_LIST),
        "root": ROOT_NODE,
        "audit": topo.audit(),
        "ring": rows,
        "rotation_utilization": rot,
        "mesh_reference": mesh,
        "ring_vs_mesh": cmp_rows,
        "wall_s": round(time.perf_counter() - t_start, 1),
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    print(f"\nwrote {OUT} ({payload['wall_s']}s)")


if __name__ == "__main__":
    main()
