#!/usr/bin/env python3
"""Executable assertions for the AIC reticle collectives pipeline.

Every check names the quantity it guards. Failures are bugs in the port or
in the DSE, not 'known limitations'. The reference widget is the arbiter for
geometry and per-segment cycle costs.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rg_aic_collectives import (ALGOS, LANE_KINDS, N_LANES, PATTERNS,
                                T1_PATTERNS, _first_zero, _spread,
                                build_calendar, cut_lanes, lower_bounds,
                                pair_demand, unicast_step)
from rg_aic_reticle import (CYC, CYC_TURN, N_COLS, N_CORES, N_HRAIL, N_ROWS,
                            N_VRAIL, UM_ACCESS, UM_ARM, UM_FAR, UM_GAP,
                            UM_HFOLD, UM_NEAR, UM_PER_CYCLE, UM_STRAIGHT,
                            UM_VFOLD, UM_VSPAN, Fabric, all_routes, cyc_of,
                            ring_order, route)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "results" / "aic_reticle_collectives_8x6.json"
OUT = ROOT / "results" / "verify_aic_reticle_8x6.json"


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def main() -> int:
    fab = Fabric().build()
    rs = all_routes(fab)
    d = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else None
    rows: list[dict] = []

    # -- fabric transcription ----------------------------------------------
    rows.append(check("192 RBRG stations",
                      sum(1 for s in fab.station.values()
                          if s.type == "rbrg") == N_HRAIL * N_VRAIL,
                      f"{N_HRAIL}x{N_VRAIL}"))
    rows.append(check("48 CS + 48 PIPE",
                      sum(1 for s in fab.station.values() if s.type == "cs")
                      == N_CORES
                      and sum(1 for s in fab.station.values()
                              if s.type == "pipe") == N_CORES))
    n_edges = sum(len(v) for v in fab.adj.values())
    rows.append(check("3456 directed micro-edges", n_edges == 3456,
                      str(n_edges)))
    n_lanes = sum(1 for lst in fab.adj.values() for e in lst
                  if e.kind in LANE_KINDS)
    rows.append(check("960 inter-station lanes", n_lanes == N_LANES,
                      str(n_lanes)))

    expected = {
        "access": cyc_of(UM_ACCESS), "harm": cyc_of(UM_ARM),
        "gap": cyc_of(UM_GAP), "vspan": cyc_of(UM_VSPAN),
        "straight": 2, "near": 10, "far": 10,
        "hfold": cyc_of(UM_HFOLD), "vfold": cyc_of(UM_VFOLD),
        "cs": 0, "pipe": 0,
    }
    rows.append(check("segment costs match ceil(um/400) and the 10-cycle turn",
                      CYC == expected, str(CYC)))
    rows.append(check("turn is 5+5 = 10, geometry inclusive",
                      CYC_TURN == 10 and CYC["near"] == 10 and CYC["far"] == 10))
    rows.append(check("400 um/cycle", UM_PER_CYCLE == 400))

    # -- routing invariants ------------------------------------------------
    same_t = {r.turns for (s, dst), r in rs.items()
              if s // N_COLS == dst // N_COLS}
    cross_t = {r.turns for (s, dst), r in rs.items()
               if s // N_COLS != dst // N_COLS}
    rows.append(check("same-row routes never turn", same_t == {0}, str(same_t)))
    rows.append(check("cross-row routes turn exactly twice",
                      cross_t == {2}, str(cross_t)))
    asym = sum(1 for (s, dst) in rs
               if rs[(s, dst)].total != rs[(dst, s)].total)
    rows.append(check("every pair is latency-symmetric", asym == 0, str(asym)))
    r047 = route(fab, 0, 47)
    rows.append(check("corner 0→47 is 194 cycles / 2 turns / 0 folds",
                      r047.total == 194 and r047.turns == 2 and r047.folds == 0,
                      f"{r047.total}/{r047.turns}/{r047.folds}"))
    tour = ring_order()
    rows.append(check("folded row tour is 0,2,4,6,7,5,3,1",
                      tour == [0, 2, 4, 6, 7, 5, 3, 1], str(tour)))
    adj = [rs[(tour[i], tour[(i + 1) % 8])].total for i in range(8)]
    rows.append(check("row-adjacent hops are 24 (typical) or 36 (fold)",
                      adj == [24, 24, 24, 36, 24, 24, 24, 36], str(adj)))
    col = [rs[(r * 8, (r + 1) * 8)].total for r in range(5)]
    rows.append(check("column-adjacent same-col hop is 43 cycles",
                      col == [43] * 5, str(col)))
    wrap = [rs[(40 + c, c)].total for c in range(8)]
    rows.append(check("vertical 'wrap' is 111 cycles (no cheap torus wrap)",
                      wrap == [111] * 8, str(wrap)))
    rows.append(check("diameter is the 0↔47 pair",
                      max(r.total for r in rs.values()) == 194))

    # -- resource identity -------------------------------------------------
    inn: dict[str, int] = {}
    for lst in fab.adj.values():
        for e in lst:
            if e.kind in LANE_KINDS:
                inn[e.to] = inn.get(e.to, 0) + 1
    rows.append(check("each RBRG in-port is fed by at most one inter-station lane",
                      max(inn.values()) == 1, str(max(inn.values()))))

    cuts = cut_lanes(fab)
    rows.append(check("every column cut has 24 directed lanes",
                      set(cuts["col"].values()) == {24}, str(cuts["col"])))
    rows.append(check("every row cut has 32 directed lanes",
                      set(cuts["row"].values()) == {32}, str(cuts["row"])))

    # -- helpers -----------------------------------------------------------
    for dlt, expect in ((1, [20]), (2, [19, 20]), (5, list(range(16, 21)))):
        bits = [i for i in range(30) if (_spread(1 << 20, dlt) >> i) & 1]
        rows.append(check(f"_spread(1<<20, {dlt}) window",
                          bits == expect, str(bits)))
    rows.append(check("_first_zero skips a run of ones",
                      _first_zero(0b0111, 0) == 3))
    rows.append(check("_first_zero on empty mask is t",
                      _first_zero(0, 12) == 12))

    # -- bounds vs measured ------------------------------------------------
    if d is None:
        rows.append(check("DSE json present", False, str(DATA)))
    else:
        rows.append(check("DSE json present", True, str(DATA)))
        w = d["wire"]
        rows.append(check("DSE wire matches the transcribed constants",
                          w["t_turn"] == 10 and w["n_rbrg"] == 192
                          and w["n_lanes"] == 960
                          and w["diameter_cy"] == 194))
        for b in d["bounds"]:
            rows.append(check(
                f"bound families non-negative {b['pattern']} m={b['m']} {b['tier']}",
                all(b[k] >= 0 for k in
                    ("cut", "inject", "eject", "turn", "latency", "serial"))))
            rows.append(check(
                f"floor is the max family {b['pattern']} m={b['m']} {b['tier']}",
                b["floor"] == max(b[k] for k in
                                  ("cut", "inject", "eject", "turn",
                                   "latency", "serial"))))
        # allgather T0 cut must use the relay discount (one crossing per origin)
        ag1 = next(x for x in d["bounds"]
                   if x["pattern"] == "allgather" and x["m"] == 1
                   and x["tier"] == "T0")
        rows.append(check(
            "allgather T0 cut is 2 (relay, not 48 direct sends)",
            ag1["cut"] == 2, str(ag1["cut"])))
        a2a13 = next(x for x in d["bounds"]
                     if x["pattern"] == "alltoall" and x["m"] == 13)
        rows.append(check(
            "alltoall m=13 is cut-bound (distinct payloads cannot share)",
            a2a13["binding"] == "cut" and a2a13["cut"] == 624,
            f"{a2a13['binding']} {a2a13['cut']}"))

        for r in d["calendars"]:
            fl = next(x for x in d["bounds"]
                      if x["pattern"] == r["pattern"] and x["m"] == r["m"]
                      and x["tier"] == r["tier"])
            rows.append(check(
                f"calendar ≥ floor  {r['pattern']}/{r['algo']}/{r['tier']} m={r['m']}",
                r["makespan"] >= fl["floor"],
                f"{r['makespan']} vs {fl['floor']}"))
        for r in d["baselines"]:
            fl = next(x for x in d["bounds"]
                      if x["pattern"] == r["pattern"] and x["m"] == r["m"]
                      and x["tier"] == "T0")
            rows.append(check(
                f"baseline ≥ floor  {r['pattern']} m={r['m']}",
                r["makespan"] >= fl["floor"],
                f"{r['makespan']} vs {fl['floor']}"))

        # T1 never slower than T0 on the same algo (multicast is optional)
        by = {(r["pattern"], r["algo"], r["m"], r["tier"]): r
              for r in d["calendars"]}
        for pat in T1_PATTERNS:
            for algo in ALGOS[pat]:
                for m in (1, 13):
                    a, b = by.get((pat, algo, m, "T0")), by.get((pat, algo, m, "T1"))
                    if a and b:
                        rows.append(check(
                            f"T1 ≤ T0  {pat}/{algo} m={m}",
                            b["makespan"] <= a["makespan"],
                            f"{b['makespan']} vs {a['makespan']}"))

        # alltoall has no T1 row
        rows.append(check(
            "alltoall has no T1 calendar (payloads are all distinct)",
            all(r["tier"] == "T0" for r in d["calendars"]
                if r["pattern"] == "alltoall")))

        # hop tax ≥ 1
        rows.append(check(
            "every calendar hop-tax ≥ 1",
            all(r["hop_tax"] >= 1 - 1e-9 for r in d["calendars"])))

        # FIFO sweep is monotone
        sweep = sorted(d["fifo_sweep"], key=lambda r: r["fifo_depth"])
        rows.append(check(
            "deeper FIFO never raises alltoall m=1 makespan",
            all(sweep[i]["makespan"] >= sweep[i + 1]["makespan"]
                for i in range(len(sweep) - 1)),
            str([(r["fifo_depth"], r["makespan"]) for r in sweep])))

        # per_round clears the capacity floor
        for t in d["throughput"]:
            rows.append(check(
                f"per_round ≥ II_lb  {t['pattern']} m={t['m']}",
                t["per_round"] + 1e-6 >= t["II_lb"],
                f"{t['per_round']} vs {t['II_lb']}"))

        # example matches live router
        ex = d["example_0_to_47"]
        rows.append(check("DSE 0→47 matches live router",
                          ex["total"] == r047.total and ex["um"] == r047.um))

    # -- live calendar: growing sizes --------------------------------------
    c = build_calendar(fab, rs, "allgather", "dim_2phase", 13, tier="T0")
    # column phase boardings should occupy 8*13 = 104 cycles on their first lane
    # we only check makespan exceeds the T0 allgather floor
    fl = lower_bounds(fab, "allgather", 13, tier="T0", rs=rs)
    rows.append(check("live allgather/dim_2phase m=13 ≥ floor",
                      c.makespan >= fl["floor"],
                      f"{c.makespan} vs {fl['floor']}"))
    rows.append(check("pair_demand alltoall is 48*47",
                      len(pair_demand("alltoall")) == N_CORES * (N_CORES - 1)))
    st = unicast_step(rs, 0, 2)
    rows.append(check("cached unicast step is identical",
                      unicast_step(rs, 0, 2) is st))

    n_ok = sum(1 for r in rows if r["ok"])
    payload = {"n": len(rows), "n_ok": n_ok, "n_fail": len(rows) - n_ok,
               "checks": rows}
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    print(f"{n_ok}/{len(rows)} passed")
    for r in rows:
        if not r["ok"]:
            print(f"  FAIL  {r['name']}: {r['detail']}")
    return 0 if n_ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
