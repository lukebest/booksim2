#!/usr/bin/env python3
"""End-to-end (compute + alltoall) time vs router area Pareto for 8x6 PG.

Workload: the dispatch half of a MoE expert-parallel FFN layer --
  alltoall dispatch -> expert FFN, run back to back (no overlap).
  A full layer would add a symmetric combine alltoall; that doubles the
  communication term and is left out so the reported time matches the
  "one alltoall of m flits" framing.

Compute model
  PE does one 8x64x16 matmul per cycle = 8192 MAC/cycle @ 1.5 GHz.
  FFN d_model=64, d_ff=256, fp16. Per token 2*64*256 = 32768 MAC = 4 cycles.
  The 8x64x16 tile divides both FFN matmuls exactly, so there is no tile
  quantization waste (verified: tile count == MAC count == 4 cy/token).

Strong scaling
  Total token count is pinned at the healthy 48-PE config, where the per-pair
  alltoall payload is the nominal m0 flits:
      T_total = 48^2 * m0 * 64 B / 128 B = 1152 * m0 tokens
  With only A survivors the same tokens are spread over A PEs, so BOTH terms
  grow: compute as 1/A, and the per-pair payload as (48/A)^2 (each of the A^2
  pair slots must carry more).  Holding m fixed at m0 instead would hand
  heavy-sacrifice schemes a free 1/A^2 traffic cut, which is why m is rescaled.

Area model (router only, per the study scope)
  Sacrificed PEs cost time, not area -- all 48 routers are physically present
  in every scheme, so the only area lever is VC count:
      area = crossbar + control + 5 ports * VC * Q flits * A_FLIT
  Normalized to the IQ-XY baseline router (= 1.0) via ppa_analytic_model.
  A scheme is sized for the worst VC count it needs across all scenarios.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path

import dse_pg_alltoall_8x6 as D
import pg_faults_8x6 as F
import ppa_analytic_model as PPA

FREQ_GHZ = 1.5
PE_MACS_PER_CYCLE = 8 * 64 * 16          # 8x64x16 matmul per cycle
D_MODEL, D_FF, ELEM_BYTES = 64, 256, 2   # fp16 FFN
FLIT_BYTES = 64                          # 512-bit flit, project-wide
TOKEN_BYTES = D_MODEL * ELEM_BYTES       # 128 B = 2 flits
CYCLES_PER_TOKEN = (2 * D_MODEL * D_FF) / PE_MACS_PER_CYCLE  # 4.0

A_FULL = 48
M0_LIST = [1, 13]
SCHEMES = D.SCHEMES + ["updown_lb", "segment_lb"]
SEMANTICS = "dead"
Q = D.DEFAULT_Q

# Router area, normalized to the IQ-XY baseline (crossbar+buffers+control=1.0)
A_FLIT = PPA.ARCH_A3_BUFFERS / PPA.ARCH_A3_INTERIOR_FLITS   # per 512b flit
PORTS = 5


def total_tokens(m0: int) -> float:
    """Tokens in the layer, pinned at the healthy 48-PE config."""
    return A_FULL * A_FULL * m0 * FLIT_BYTES / TOKEN_BYTES


def m_effective(a: int, m0: int) -> int:
    """Per-pair flits needed to move the same total tokens over A PEs."""
    return max(1, math.ceil(m0 * (A_FULL / a) ** 2))


def compute_cycles(a: int, m0: int) -> int:
    return math.ceil(CYCLES_PER_TOKEN * total_tokens(m0) / a)


def router_area(num_vc: int) -> float:
    buffers = PORTS * num_vc * Q * A_FLIT
    return PPA.BASELINE_CROSSBAR + PPA.BASELINE_CONTROL + buffers


def pareto(points: list[dict], xk: str, yk: str) -> list[dict]:
    """Non-dominated on (x, y), both minimised."""
    out = []
    for p in points:
        if not any(o is not p and o[xk] <= p[xk] and o[yk] <= p[yk]
                   and (o[xk] < p[xk] or o[yk] < p[yk]) for o in points):
            out.append(p)
    return sorted(out, key=lambda p: p[xk])


def run() -> dict:
    scenarios = [s for s in F.all_scenarios()]
    rows = []
    t0 = time.time()
    total = len(scenarios) * len(SCHEMES) * len(M0_LIST)
    i = 0
    for scen in scenarios:
        pg = F.expand_pg(scen, SEMANTICS)
        for sch in SCHEMES:
            for m0 in M0_LIST:
                i += 1
                base = D.get_solution(pg, sch)
                if not base["feasible"]:
                    print(f"[{i}/{total}] {scen['name']:22s} {sch:16s} "
                          f"m0={m0:2d} -> INFEASIBLE")
                    continue
                a = base["n_compute_used"]
                me = m_effective(a, m0)
                rec = D.run_one(pg, sch, me, Q)
                if not rec["feasible"] or rec["makespan"] is None:
                    print(f"[{i}/{total}] {scen['name']:22s} {sch:16s} "
                          f"m0={m0:2d} -> {rec.get('reason')}")
                    continue
                t_comp = compute_cycles(a, m0)
                t_comm = rec["makespan"]
                t_tot = t_comp + t_comm
                rows.append({
                    "scenario": scen["name"],
                    "scheme": sch,
                    "m0": m0,
                    "m_eff": me,
                    "A": a,
                    "n_sacrificed": rec["n_sacrificed"],
                    "num_vc": rec["num_vc"],
                    "t_compute_cy": t_comp,
                    "t_alltoall_cy": t_comm,
                    "t_e2e_cy": t_tot,
                    "t_e2e_ns": t_tot / FREQ_GHZ,
                    "comm_frac": t_comm / t_tot,
                })
                print(f"[{i}/{total}] {scen['name']:22s} {sch:16s} "
                      f"m0={m0:2d} A={a:2d} m_eff={me:4d} "
                      f"comp={t_comp:6d} a2a={t_comm:6d} "
                      f"e2e={t_tot / FREQ_GHZ:9.1f}ns")

    # Size each scheme's router for the worst VC count it ever needs.
    vc_req: dict[str, int] = defaultdict(int)
    for r in rows:
        vc_req[r["scheme"]] = max(vc_req[r["scheme"]], r["num_vc"])

    summary = []
    for m0 in M0_LIST:
        for sch in SCHEMES:
            sel = [r for r in rows if r["scheme"] == sch and r["m0"] == m0]
            if len(sel) < len(scenarios):
                continue  # scheme must cover every scenario to be a candidate
            ts = sorted(r["t_e2e_ns"] for r in sel)
            summary.append({
                "scheme": sch,
                "m0": m0,
                "num_vc": vc_req[sch],
                "area": round(router_area(vc_req[sch]), 4),
                "n_scen": len(sel),
                "t_e2e_ns_med": round(ts[len(ts) // 2], 1),
                "t_e2e_ns_worst": round(ts[-1], 1),
                "t_e2e_ns_best": round(ts[0], 1),
                "A_med": sorted(r["A"] for r in sel)[len(sel) // 2],
                "A_worst": min(r["A"] for r in sel),
                "sac_med": sorted(r["n_sacrificed"]
                                  for r in sel)[len(sel) // 2],
                "comm_frac_med": round(
                    sorted(r["comm_frac"] for r in sel)[len(sel) // 2], 3),
            })

    for m0 in M0_LIST:
        cand = [s for s in summary if s["m0"] == m0]
        front_w = {s["scheme"] for s in pareto(cand, "area", "t_e2e_ns_worst")}
        front_m = {s["scheme"] for s in pareto(cand, "area", "t_e2e_ns_med")}
        for s in cand:
            s["pareto_worst"] = s["scheme"] in front_w
            s["pareto_med"] = s["scheme"] in front_m

    meta = {
        "freq_ghz": FREQ_GHZ,
        "pe_macs_per_cycle": PE_MACS_PER_CYCLE,
        "d_model": D_MODEL, "d_ff": D_FF, "elem_bytes": ELEM_BYTES,
        "flit_bytes": FLIT_BYTES, "token_bytes": TOKEN_BYTES,
        "cycles_per_token": CYCLES_PER_TOKEN,
        "semantics": SEMANTICS, "Q": Q,
        "m0_list": M0_LIST,
        "total_tokens": {str(m): total_tokens(m) for m in M0_LIST},
        "area_model": {
            "a_flit": A_FLIT, "ports": PORTS,
            "crossbar": PPA.BASELINE_CROSSBAR,
            "control": PPA.BASELINE_CONTROL,
            "note": "normalized to IQ-XY baseline router = 1.0; "
                    "48 routers present in every scheme, so only VC matters",
        },
        "elapsed_s": round(time.time() - t0, 1),
    }
    return {"meta": meta, "rows": rows, "summary": summary}


def main() -> None:
    out = run()
    p = Path(__file__).resolve().parents[1] / "results" / "pg_e2e_pareto.json"
    p.write_text(json.dumps(out, indent=1))
    print(f"Wrote {p}  ({len(out['rows'])} rows, {out['meta']['elapsed_s']}s)")


if __name__ == "__main__":
    main()
