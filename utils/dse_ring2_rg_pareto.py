#!/usr/bin/env python3
"""Area / makespan Pareto for request-grant on the 20-node dual-plane ring.

Axes
----
y   makespan = makespan_des + t_sched_cycles   (scheduler delay charged back)
x   area_norm  = shared credit+I/E-tag datapath + arbiter + control-plane
                (IQ-XY router = 1.0, amortized per node)

S0–S14 are plotted as reference points so the front is cross-scheme.
S11 adds a same-cycle hop mutex; S12 adds dest-then-hop request-grant;
S13 prefers the shorter remaining path on hop-grant; S14 yields the
sibling HA srcq off a late_plane first-hop clash.
`--refine` densifies around the current knee and tries leftover algorithm
variants; used by the self-paced search loop.

Writes results/ring2_rg_pareto.json and results/ring2_rg_pareto.png.
"""

from __future__ import annotations

import argparse
import json
import math
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
    s13_params, s14_params,
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
             s13_params(plane_sel="least_occupied")),
            ("S14", run_dist, "ring2_ej",
             s14_params(plane_sel="least_occupied"))):
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
              f"S13 mk={by_sch.get('S13')} "
              f"S14 mk={by_sch.get('S14')}", flush=True)
    else:
        rows.extend([r for r in prior_rows
                     if r.get("scheme") in ("S0", "S1", "S3", "S4", "S5",
                                            "S6", "S7", "S8", "S9",
                                            "S10", "S11", "S12", "S13",
                                            "S14")])

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


# Same-area S5–S14 sit on x=0.0458; S14 stays on the true x (Pareto vertex).
_EJ_SPREAD = ("S5", "S6", "S7", "S8", "S9", "S10", "S11", "S12", "S13", "S14")
_STYLE = {
    "S0":  ("#2563eb", "D", "S0 baseline"),
    "S1":  ("#16a34a", "s", "S1 AIMD"),
    "S3":  ("#9333ea", "^", "S3 push-on-pull"),
    "S4":  ("#ea580c", "p", "S4 kind-aware leave"),
    "S5":  ("#0d9488", "h", "S5 leave-slot lock"),
    "S6":  ("#c026d3", "*", "S6 oldest dest clash"),
    "S7":  ("#7c3aed", "P", "S7 hop bounce"),
    "S8":  ("#ca8a04", "X", "S8 late plane"),
    "S9":  ("#be123c", "v", "S9 late dir"),
    "S10": ("#047857", "<", "S10 resp late dir"),
    "S11": ("#9a3412", ">", "S11 hop hold"),
    "S12": ("#4338ca", "d", "S12 hop islip"),
    "S13": ("#0369a1", "H", "S13 hop short"),
    "S14": ("#db2777", "8", "S14 HA sib plane"),
}


def _display_xy(scheme: str, x: float, y: float) -> tuple[float, float]:
    """Visual offset only. Pareto line still uses true (area, makespan).

    S5–S14 share area_norm=0.0458. Keep S14 on the true x (Pareto vertex)
    and fan the rest to the right so they do not sit on the S1→S14 edge.
    """
    if scheme == "S14":
        return x, y
    if scheme in _EJ_SPREAD:
        i = _EJ_SPREAD.index(scheme)
        return x + (i + 1) * 0.00012, y
    if scheme == "S0":
        return x - 0.00014, y
    return x, y


def _s2_nearest_front(res: dict[str, Any]) -> dict[str, Any] | None:
    """S2 row with the smallest full-span distance to the S0–S14 front."""
    front = [(p["area_norm"], p["makespan"])
             for p in (res.get("pareto") or []) if p.get("tag") in _STYLE]
    s2 = [r for r in res.get("rows") or []
          if r.get("scheme") == "S2" and r.get("completed")
          and r.get("makespan") is not None]
    if not front or not s2:
        return None
    pts = [r for r in res["rows"]
           if r.get("completed") and r.get("makespan") is not None]
    ax = max(r["area_norm"] for r in pts) - min(r["area_norm"] for r in pts)
    ay = max(r["makespan"] for r in pts) - min(r["makespan"] for r in pts)
    ax = ax or 1.0
    ay = ay or 1.0
    verts = [(a / ax, m / ay) for a, m in front]

    def dist(r: dict[str, Any]) -> float:
        px, py = r["area_norm"] / ax, r["makespan"] / ay
        best = 1e18
        for i, (vx, vy) in enumerate(verts):
            best = min(best, math.hypot(px - vx, py - vy))
            if i + 1 >= len(verts):
                continue
            x0, y0 = verts[i]
            x1, y1 = verts[i + 1]
            dx, dy = x1 - x0, y1 - y0
            l2 = dx * dx + dy * dy
            t = (0.0 if l2 == 0 else
                 max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2)))
            best = min(best, math.hypot(px - (x0 + t * dx),
                                        py - (y0 + t * dy)))
        return best

    return min(s2, key=lambda r: (dist(r), r["area_norm"], r["makespan"],
                                  r.get("tag", "")))


def plot(res: dict[str, Any], path: Path) -> None:
    s2n = _s2_nearest_front(res)
    fig = plt.figure(figsize=(9.6, 5.6))
    gs = fig.add_gridspec(1, 2, width_ratios=[3.7, 1.05], wspace=0.12)
    ax = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1]) if s2n else None

    refs: dict[str, tuple[float, float]] = {}
    for r in res["rows"]:
        if r.get("makespan") is None or not r.get("completed"):
            continue
        sch = r.get("scheme", "")
        if sch == "S2" or sch not in _STYLE:
            continue
        refs[sch] = (r["area_norm"], r["makespan"])

    z = 4
    for sch, (color, marker, label) in _STYLE.items():
        if sch not in refs:
            continue
        x, y = refs[sch]
        xd, yd = _display_xy(sch, x, y)
        ax.scatter([xd], [yd], s=92, c=color, marker=marker, label=label,
                   zorder=z, edgecolors="white", linewidths=0.4)
        z += 1

    front = [p for p in (res.get("pareto") or [])
             if p.get("tag") in _STYLE]
    if front:
        xs = [p["area_norm"] for p in front]
        ys = [p["makespan"] for p in front]
        ax.plot(xs, ys, "-", c="#dc2626", lw=1.5, label="Pareto", zorder=3)
        ax.scatter(xs, ys, s=28, c="#dc2626", zorder=12)

    label_off = {
        "S0": (-18, 8), "S1": (6, 8), "S3": (6, 8), "S4": (-22, -12),
        "S5": (6, 5), "S6": (6, -11), "S7": (6, 4), "S8": (6, -10),
        "S9": (6, 6), "S10": (6, 6), "S11": (-22, -11), "S12": (6, 6),
        "S13": (6, -11), "S14": (-22, -11),
    }
    for sch, (x, y) in refs.items():
        xd, yd = _display_xy(sch, x, y)
        dx, dy = label_off.get(sch, (5, 4))
        ax.annotate(sch, (xd, yd), textcoords="offset points",
                    xytext=(dx, dy), fontsize=8, color="#111827",
                    zorder=13)

    xs_all = [_display_xy(s, x, y)[0] for s, (x, y) in refs.items()]
    ys_all = [y for _, y in refs.values()]
    pad_x = 0.00035
    ax.set_xlim(min(xs_all) - pad_x, max(xs_all) + 0.00085)
    ax.set_ylim(min(ys_all) - 8, max(ys_all) + 12)
    ax.set_xlabel("area_norm  (IQ-XY router = 1.0, per node)")
    ax.set_ylabel("makespan  (last resp drain + t_sched; not mean E2E lat)")
    ax.grid(True, ls=":", alpha=0.5)
    ax.legend(frameon=False, fontsize=7.5, loc="lower left")
    ax.spines["right"].set_visible(False)

    if ax2 is not None and s2n is not None:
        x, y = s2n["area_norm"], s2n["makespan"]
        short = str(s2n.get("tag", "S2")).split("/")[0]
        ax2.scatter([x], [y], s=110, c="#64748b", marker="o",
                    zorder=4, edgecolors="white", linewidths=0.4)
        ax2.annotate(f"S2 {short}\nmk={y}", (x, y),
                     textcoords="offset points", xytext=(-52, 10),
                     fontsize=8, color="#111827")
        ax2.set_xlim(x - 0.008, x + 0.008)
        ax2.set_ylim(y - 18, y + 18)
        ax2.set_xlabel("area_norm")
        ax2.grid(True, ls=":", alpha=0.5)
        ax2.spines["left"].set_visible(False)
        ax2.tick_params(left=False, labelleft=True)
        d = 0.018
        kw = dict(color="#111827", clip_on=False, lw=0.9)
        ax.plot((1 - d, 1 + d), (-d, +d), transform=ax.transAxes, **kw)
        ax.plot((1 - d, 1 + d), (1 - d, 1 + d), transform=ax.transAxes, **kw)
        ax2.plot((-d, +d), (-d, +d), transform=ax2.transAxes, **kw)
        ax2.plot((-d, +d), (1 - d, 1 + d), transform=ax2.transAxes, **kw)

    fig.suptitle("2-full-ring 20-node  S0–S14 knee  (nearest S2 kept)",
                 fontsize=12, y=0.99)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.90, bottom=0.12,
                        wspace=0.14)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--refine", action="store_true")
    ap.add_argument("--plot-only", action="store_true",
                    help="redraw PNG from existing JSON, do not resweep")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    if args.plot_only:
        res = json.loads(args.out.read_text())
        plot(res, args.out.with_suffix(".png"))
        print(f"rewrote {args.out.with_suffix('.png')}  "
              f"rows={len(res.get('rows') or [])}  "
              f"front={res.get('n_front')}")
        return
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
