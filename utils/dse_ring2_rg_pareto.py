#!/usr/bin/env python3
"""Area / makespan Pareto for request-grant on the 20-node dual-plane ring.

Axes
----
y   makespan = makespan_des + t_sched_cycles   (scheduler delay charged back)
x   area_norm  = shared credit+I/E-tag datapath + arbiter + control-plane
                (IQ-XY router = 1.0, amortized per node)

S0–S13 are plotted as reference points so the front is cross-scheme.
S11 adds a same-cycle hop mutex; S12 adds dest-then-hop request-grant;
S13 prefers the shorter remaining path on hop-grant.
`--refine` densifies around the current knee and tries leftover algorithm
variants; used by the self-paced search loop.

Writes results/ring2_rg_pareto.json and results/ring2_rg_pareto.png.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from rg_ring2_aimd import run_batch as run_aimd
from rg_ring2_base import Ring2BaseParams, run_batch as run_base
from rg_ring2_dist import (
    Ring2DistParams, run_batch as run_dist, s5_params, s6_params,
    s7_params, s8_params, s9_params, s10_params, s11_params, s12_params,
    s13_params,
)
from rg_ring2_pop import run_batch as run_pop
from rg_ring2_rg import RING2_ALGOS, RGConfig, run_batch as run_rg
from rg_ring2_topo import Ring2Topology, build_allpairs, build_uniform
from rg_sched_cost import (
    area_from_bits, distributed_cost, pareto_front, sched_cost,
)

OUT = Path(__file__).resolve().parents[1] / "results" / "ring2_rg_pareto.json"
PNG = OUT.with_suffix(".png")

CTRL_NOC = {
    "central": 0.08,
    "per_plane": 0.05,
    "distributed_token": 0.03,
}


def _tag(r: dict[str, Any]) -> str:
    if r["scheme"] != "S2":
        return r["scheme"]
    return (f"{r['algo']}/I{r['iters']}/{r['spatial_reuse'][:3]}/"
            f"{r['conflict_domain'][:3]}/{r['arbiter'][:3]}/"
            f"{r['voq_granularity'][:3]}")


def _s0_s1_refs(topo: Ring2Topology, txns, *, m_resp: int) -> list[dict]:
    out = []
    p = Ring2BaseParams(plane_sel="least_occupied")
    for scheme, runner, cfg, params in (
            ("S0", run_base, "ring2_base", p),
            ("S1", run_aimd, "ring2_aimd", p),
            ("S3", run_pop, "ring2_pop", p),
            ("S4", run_dist, "ring2_dist",
             Ring2DistParams(plane_sel="least_occupied", leave_useful=True)),
            ("S5", run_dist, "ring2_ej",
             s5_params(plane_sel="least_occupied")),
            ("S6", run_dist, "ring2_ej",
             s6_params(plane_sel="least_occupied")),
            ("S7", run_dist, "ring2_ej",
             s7_params(plane_sel="least_occupied")),
            ("S8", run_dist, "ring2_ej",
             s8_params(plane_sel="least_occupied")),
            ("S9", run_dist, "ring2_ej",
             s9_params(plane_sel="least_occupied")),
            ("S10", run_dist, "ring2_ej",
             s10_params(plane_sel="least_occupied")),
            ("S11", run_dist, "ring2_ej",
             s11_params(plane_sel="least_occupied")),
            ("S12", run_dist, "ring2_ej",
             s12_params(plane_sel="least_occupied")),
            ("S13", run_dist, "ring2_ej",
             s13_params(plane_sel="least_occupied"))):
        r = runner(topo, txns, params=params, seed=0)
        d = distributed_cost(cfg, n_nodes=topo.n, n_planes=topo.n_planes,
                             eject_depth=p.eject_depth, resv_ej=p.resv_ej,
                             reasm_depth=m_resp)
        area = area_from_bits(d["bits"], topo)
        out.append({
            "scheme": scheme, "algo": cfg, "iters": 0,
            "spatial_reuse": "-", "conflict_domain": "-",
            "arbiter": "-", "voq_granularity": "-",
            "makespan_des": r["makespan"],
            "t_sched_cycles": 0,
            "makespan": r["makespan"],
            "area_norm": area,
            "area_bits": d["bits"],
            "completed": r["completed"],
            "n_deflections": r.get("n_deflections", 0),
            "tag": scheme,
        })
    return out


def _s2_row(topo: Ring2Topology, txns, cfg: RGConfig) -> dict[str, Any]:
    r = run_rg(topo, txns, cfg=cfg)
    n_flows = 2 * len(txns)
    n_rounds = r.get("n_rounds_est", max(1, n_flows // max(1, topo.n_cores)))
    cost = sched_cost(
        f"{cfg.algo}_ring2", topo, n_flows, iters=cfg.iters,
        n_rounds=n_rounds, mean_hops=5.0,
        conflict_domain=cfg.conflict_domain,
        voq_granularity=cfg.voq_granularity,
        arbiter=cfg.arbiter)
    d = distributed_cost("ring2_rg", n_nodes=topo.n, n_planes=topo.n_planes)
    # S2 keeps the shared credit + I/E-tag datapath; arbiter is extra
    area = (area_from_bits(d["bits"], topo) + cost["area_norm"]
            + CTRL_NOC.get(cfg.arbiter, 0.08))
    mk = r["makespan_des"] + cost["t_sched_cycles"]
    row = {
        "scheme": "S2", "algo": cfg.algo, "iters": cfg.iters,
        "spatial_reuse": cfg.spatial_reuse,
        "conflict_domain": cfg.conflict_domain,
        "arbiter": cfg.arbiter,
        "voq_granularity": cfg.voq_granularity,
        "plane_sel": cfg.plane_sel,
        "t_rtt": cfg.t_rtt,
        "pipeline_depth": cfg.pipeline_depth,
        "makespan_des": r["makespan_des"],
        "t_sched_cycles": cost["t_sched_cycles"],
        "makespan": mk,
        "area_norm": round(area, 4),
        "area_bits": cost["bits"] + d["bits"],
        "gate_levels": cost["gate_levels"],
        "completed": r["completed"],
        "replay_ok": r.get("replay_ok"),
        "n_conflicts": r.get("n_conflicts", 0),
    }
    row["tag"] = _tag(row)
    return row


def _space(*, refine: bool, prior: list[dict] | None) -> list[RGConfig]:
    cfgs: list[RGConfig] = []
    if not refine:
        for algo in RING2_ALGOS:
            iters_set = (1, 2, 4) if algo in ("islip", "pim") else (1,)
            for I in iters_set:
                for reuse in ("arc", "whole_ring"):
                    for dom in ("interval", "free_at"):
                        for arb in ("central", "per_plane"):
                            cfgs.append(RGConfig(
                                algo=algo, iters=I, spatial_reuse=reuse,
                                conflict_domain=dom, arbiter=arb,
                                voq_granularity="per_dst",
                                plane_sel="least_occupied"))
        # a thinner extra slice: voq granularity + token arbiter + rtt
        for algo in ("islip", "greedy_ff"):
            for vg in ("per_plane_dir", "grouped"):
                cfgs.append(RGConfig(algo=algo, iters=2, spatial_reuse="arc",
                                     conflict_domain="interval",
                                     arbiter="central", voq_granularity=vg))
            cfgs.append(RGConfig(algo=algo, iters=2, spatial_reuse="arc",
                                 conflict_domain="interval",
                                 arbiter="distributed_token"))
            cfgs.append(RGConfig(algo=algo, iters=2, spatial_reuse="arc",
                                 conflict_domain="interval",
                                 arbiter="central", t_rtt=8,
                                 pipeline_depth=2))
        return cfgs

    # refine: densify around current S2 front + leftover variants
    front_algos = set()
    if prior:
        pts = [(r["area_norm"], r["makespan"], r)
               for r in prior if r.get("scheme") == "S2"
               and r.get("makespan") is not None]
        for _, _, r in pareto_front(pts):
            front_algos.add(r.get("algo"))
    if not front_algos:
        front_algos = {"islip", "greedy_ff", "wavefront"}
    for algo in front_algos:
        for I in (1, 2, 3, 4):
            if algo not in ("islip", "pim") and I != 1:
                continue
            for reuse in ("arc", "whole_ring"):
                for vg in ("per_dst", "per_plane_dir", "grouped"):
                    for arb in ("central", "per_plane", "distributed_token"):
                        for ps in ("least_occupied", "req_resp_split"):
                            cfgs.append(RGConfig(
                                algo=algo, iters=I, spatial_reuse=reuse,
                                conflict_domain="interval", arbiter=arb,
                                voq_granularity=vg, plane_sel=ps))
    # always try the algorithms that have not been on a front yet
    leftover = [a for a in RING2_ALGOS if a not in front_algos]
    for algo in leftover:
        cfgs.append(RGConfig(algo=algo, iters=2 if algo in ("islip", "pim")
                             else 1, spatial_reuse="arc",
                             conflict_domain="interval",
                             arbiter="central"))
    # dedup by frozen cfg fields
    seen = set()
    uniq = []
    for c in cfgs:
        key = (c.algo, c.iters, c.spatial_reuse, c.conflict_domain,
               c.arbiter, c.voq_granularity, c.plane_sel, c.t_rtt,
               c.pipeline_depth)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def sweep(*, quick: bool = False, refine: bool = False,
          prior_path: Path | None = None) -> dict[str, Any]:
    topo = Ring2Topology()
    # Same closed batch for every point, including --refine: mixing m
    # would put S0 (m=1) and a refined S2 (m=2) on one Pareto.
    txns = build_allpairs(m=1, m_resp=4)
    prior_rows: list[dict] = []
    if refine and prior_path and prior_path.exists():
        prior_rows = json.loads(prior_path.read_text()).get("rows", [])

    t0 = time.perf_counter()
    rows: list[dict[str, Any]] = []
    if not refine:
        rows.extend(_s0_s1_refs(topo, txns, m_resp=4))
        by_sch = {r["scheme"]: r["makespan"] for r in rows}
        print(f"  refs S0 mk={by_sch.get('S0')} "
              f"S1 mk={by_sch.get('S1')} "
              f"S3 mk={by_sch.get('S3')} "
              f"S4 mk={by_sch.get('S4')} "
              f"S5 mk={by_sch.get('S5')} "
              f"S6 mk={by_sch.get('S6')} "
              f"S7 mk={by_sch.get('S7')} "
              f"S8 mk={by_sch.get('S8')} "
              f"S9 mk={by_sch.get('S9')} "
              f"S10 mk={by_sch.get('S10')} "
              f"S11 mk={by_sch.get('S11')} "
              f"S12 mk={by_sch.get('S12')} "
              f"S13 mk={by_sch.get('S13')}", flush=True)
    else:
        rows.extend([r for r in prior_rows
                     if r.get("scheme") in ("S0", "S1", "S3", "S4", "S5",
                                            "S6", "S7", "S8", "S9",
                                            "S10", "S11", "S12", "S13")])

    cfgs = _space(refine=refine, prior=prior_rows)
    if quick:
        cfgs = [RGConfig(algo=a, iters=2 if a in ("islip", "pim") else 1,
                         spatial_reuse="arc", conflict_domain="interval",
                         arbiter="central")
                for a in ("islip", "pim", "greedy_ff", "wavefront",
                          "rr_oldest", "lqf")]
        cfgs.append(RGConfig(algo="islip", iters=1, spatial_reuse="whole_ring",
                             conflict_domain="free_at", arbiter="per_plane"))

    seen_tags = {r.get("tag") for r in rows}
    for cfg in cfgs:
        row = _s2_row(topo, txns, cfg)
        if row["tag"] in seen_tags:
            continue
        seen_tags.add(row["tag"])
        rows.append(row)
        print(f"  S2 {row['tag']:40} mk={row['makespan']:<7} "
              f"des={row['makespan_des']:<6} Tsch={row['t_sched_cycles']:<5} "
              f"A={row['area_norm']:<7} ok={row['completed']}", flush=True)

    if refine:
        # keep previous S2 rows that we did not re-run (same tag)
        for r in prior_rows:
            if r.get("scheme") == "S2" and r.get("tag") not in seen_tags:
                rows.append(r)
                seen_tags.add(r.get("tag"))

    pts = [(r["area_norm"], r["makespan"], r["tag"])
           for r in rows if r.get("makespan") is not None
           and r.get("completed")]
    front = pareto_front(pts)
    front_tags = {t for _, _, t in front}
    for r in rows:
        r["on_front"] = r.get("tag") in front_tags

    return {
        "meta": {
            "n": topo.n, "n_txns": len(txns), "quick": quick,
            "refine": refine,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "rows": rows,
        "pareto": [{"area_norm": a, "makespan": m, "tag": t}
                   for a, m, t in front],
        "n_front": len(front),
        "wall_secs": round(time.perf_counter() - t0, 1),
    }


def plot(res: dict[str, Any], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.2, 5.4))
    by = {"S0": ([], [], []), "S1": ([], [], []),
          "S2": ([], [], []), "S3": ([], [], []),
          "S4": ([], [], []), "S5": ([], [], []),
          "S6": ([], [], []),
          "S7": ([], [], []),
          "S8": ([], [], []),
          "S9": ([], [], []),
          "S10": ([], [], []),
          "S11": ([], [], []),
          "S12": ([], [], []),
          "S13": ([], [], [])}
    for r in res["rows"]:
        if r.get("makespan") is None:
            continue
        x, y, t = r["area_norm"], r["makespan"], r.get("tag", "")
        bucket = by.setdefault(r["scheme"], ([], [], []))
        bucket[0].append(x)
        bucket[1].append(y)
        bucket[2].append(t)
    ax.scatter(by["S2"][0], by["S2"][1], s=22, c="#64748b", alpha=0.7,
               label="S2 request-grant", zorder=2)
    if by["S0"][0]:
        ax.scatter(by["S0"][0], by["S0"][1], s=80, c="#2563eb", marker="D",
                   label="S0 baseline", zorder=4)
    if by["S1"][0]:
        ax.scatter(by["S1"][0], by["S1"][1], s=80, c="#16a34a", marker="s",
                   label="S1 AIMD", zorder=4)
    if by["S3"][0]:
        ax.scatter(by["S3"][0], by["S3"][1], s=80, c="#9333ea", marker="^",
                   label="S3 push-on-pull", zorder=4)
    if by["S4"][0]:
        ax.scatter(by["S4"][0], by["S4"][1], s=90, c="#ea580c", marker="p",
                   label="S4 kind-aware leave", zorder=4)
    if by["S5"][0]:
        ax.scatter(by["S5"][0], by["S5"][1], s=90, c="#0d9488", marker="h",
                   label="S5 leave-slot lock", zorder=4)
    if by["S6"][0]:
        ax.scatter(by["S6"][0], by["S6"][1], s=70, c="#c026d3", marker="*",
                   label="S6 oldest dest clash", zorder=5)
    if by["S7"][0]:
        ax.scatter(by["S7"][0], by["S7"][1], s=80, c="#7c3aed", marker="P",
                   label="S7 hop bounce", zorder=6)
    if by["S8"][0]:
        ax.scatter(by["S8"][0], by["S8"][1], s=80, c="#ca8a04", marker="X",
                   label="S8 late plane", zorder=6)
    if by["S9"][0]:
        ax.scatter(by["S9"][0], by["S9"][1], s=80, c="#be123c", marker="v",
                   label="S9 late dir", zorder=7)
    if by["S10"][0]:
        ax.scatter(by["S10"][0], by["S10"][1], s=90, c="#047857", marker="<",
                   label="S10 resp late dir", zorder=8)
    if by["S11"][0]:
        ax.scatter(by["S11"][0], by["S11"][1], s=90, c="#9a3412", marker=">",
                   label="S11 hop hold", zorder=9)
    if by["S12"][0]:
        ax.scatter(by["S12"][0], by["S12"][1], s=90, c="#4338ca", marker="d",
                   label="S12 hop islip", zorder=10)
    if by["S13"][0]:
        ax.scatter(by["S13"][0], by["S13"][1], s=90, c="#0369a1", marker="H",
                   label="S13 hop short", zorder=11)
    front = res.get("pareto") or []
    if front:
        xs = [p["area_norm"] for p in front]
        ys = [p["makespan"] for p in front]
        ax.plot(xs, ys, "-o", c="#dc2626", ms=5, lw=1.4, label="Pareto",
                zorder=5)
        for p in front:
            ax.annotate(p["tag"], (p["area_norm"], p["makespan"]),
                        textcoords="offset points", xytext=(4, 4),
                        fontsize=7, color="#111827")
    ax.set_xlabel("area_norm  (IQ-XY router = 1.0, per node)")
    ax.set_ylabel("makespan  (DES + t_sched_cycles)")
    ax.set_title("2-full-ring 20-node  request-grant area / makespan")
    ax.grid(True, ls=":", alpha=0.5)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--refine", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    res = sweep(quick=args.quick, refine=args.refine, prior_path=args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, default=str))
    plot(res, args.out.with_suffix(".png"))
    print(f"\nwrote {args.out}  rows={len(res['rows'])}  "
          f"front={res['n_front']}  {res['wall_secs']}s")
    print(f"{'tag':48} {'area':>8} {'mk':>8}")
    for p in res["pareto"]:
        print(f"{p['tag']:48} {p['area_norm']:>8} {p['makespan']:>8}")


if __name__ == "__main__":
    main()
