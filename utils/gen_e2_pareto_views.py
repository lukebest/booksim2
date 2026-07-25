#!/usr/bin/env python3
"""Filter existing multi-area / multiflit / portbuf DSE points to E=2 and
recompute Pareto fronts + PNGs for the report's E≡2 appendix section.

E = down-ramp eject drain bandwidth (flits/cycle/node).  W and B remain free
subject to the usual coherence constraints (W>=E; B=0 only when W==E).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from dse_axis_area_makespan import pareto
from dse_multi_area_makespan import SCHEMES

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "e2_pareto_views.json"
PNG_RIGID = ROOT / "results" / "e2_multi_area_makespan.png"
PNG_MF = ROOT / "results" / "e2_multiflit_area_makespan.png"
PNG_PB = ROOT / "results" / "e2_portbuf_area_makespan.png"

E_FIXED = 2


def _load(name: str) -> dict:
    return json.loads((ROOT / "results" / name).read_text(encoding="utf-8"))


def plot_rigid(points, front, lb: int) -> None:
    fig, ax = plt.subplots(figsize=(9.2, 6.0))
    cmap = plt.get_cmap("tab10")
    for i, (key, (label, _)) in enumerate(SCHEMES.items()):
        pts = [p for p in points if p["scheme"] == key]
        ax.scatter([p["area_total"] for p in pts],
                   [p["makespan"] for p in pts],
                   s=28, color=cmap(i), alpha=0.5, edgecolor="none",
                   label=label)
        fr = pareto(pts, "area_total", "makespan")
        if fr:
            ax.plot([p["area_total"] for p in fr],
                    [p["makespan"] for p in fr],
                    "-", color=cmap(i), lw=1.0, alpha=0.7)
    ax.plot([p["area_total"] for p in front],
            [p["makespan"] for p in front],
            "-o", color="#111827", lw=2.3, ms=6, zorder=5,
            label="global Pareto (E=2)")
    for p in front:
        ax.annotate(
            f"{p['label']}\nW{p['W']}/E{p['E']}/B{p['B']}",
            (p["area_total"], p["makespan"]),
            textcoords="offset points", xytext=(6, 4),
            fontsize=6.5, color="#111827")
    ax.axhline(lb, color="#2563eb", ls="--", lw=1)
    ax.text(max(p["area_total"] for p in points), lb + 1,
            f"LB={lb}", color="#2563eb", fontsize=8, ha="right")
    ax.set_xlabel("chip implementation area (IQ-XY=1.0)")
    ax.set_ylabel("makespan (cycles)")
    ax.set_title(f"E≡{E_FIXED}: rigid-calendar makespan vs area")
    ax.grid(True, ls=":", alpha=0.5)
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(PNG_RIGID, dpi=130)
    plt.close(fig)


def plot_multiflit(points, front_avg, front_t15, lb1: int) -> None:
    fig, (a0, a1) = plt.subplots(1, 2, figsize=(13.8, 6.0))
    cmap = plt.get_cmap("tab10")
    keys = list(SCHEMES)
    kidx = {k: i for i, k in enumerate(keys)}
    for k in keys:
        pp = [p for p in points if p["scheme"] == k]
        a0.scatter([p["area_total"] for p in pp], [p["t_avg"] for p in pp],
                   s=28, color=cmap(kidx[k]), alpha=0.5, edgecolor="none",
                   label=SCHEMES[k][0])
        a1.scatter([p["t1"] for p in pp], [p["t5"] for p in pp],
                   s=28, color=cmap(kidx[k]), alpha=0.5, edgecolor="none",
                   label=SCHEMES[k][0])
    a0.plot([p["area_total"] for p in front_avg],
            [p["t_avg"] for p in front_avg],
            "-o", color="#111827", lw=2.3, ms=5, zorder=5,
            label="global Pareto")
    for p in front_avg:
        a0.annotate(
            f"{SCHEMES[p['scheme']][0]}\nW{p['W']}/E{p['E']}/B{p['B']}",
            (p["area_total"], p["t_avg"]),
            textcoords="offset points", xytext=(6, 4), fontsize=6)
    a0.set_xlabel("area (IQ-XY=1.0)")
    a0.set_ylabel("T_avg = T1 + 2·II (cycles)")
    a0.set_title(f"E≡{E_FIXED}: area vs T_avg (R=5)")
    a0.grid(True, ls=":", alpha=0.5)
    a0.legend(loc="upper right", fontsize=7, ncol=2)

    a1.plot([p["t1"] for p in front_t15], [p["t5"] for p in front_t15],
            "-o", color="#111827", lw=2.0, ms=5, zorder=5,
            label="T1–T5 Pareto")
    a1.axvline(lb1, color="#2563eb", ls="--", lw=1)
    a1.set_xlabel("T1 (fill)")
    a1.set_ylabel("T5 = T1 + 4·II (throughput)")
    a1.set_title(f"E≡{E_FIXED}: fill vs throughput")
    a1.grid(True, ls=":", alpha=0.5)
    a1.legend(loc="upper left", fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(PNG_MF, dpi=130)
    plt.close(fig)


def plot_portbuf(points, front) -> None:
    fig, ax = plt.subplots(figsize=(10.4, 6.4))
    cmap = plt.get_cmap("tab10")
    kidx = {k: i for i, k in enumerate(SCHEMES)}
    for k, (label, _) in SCHEMES.items():
        rp = [p for p in points if p["scheme"] == k and p["mode"] == "rigid"]
        bp = [p for p in points if p["scheme"] == k and p["mode"] == "buffered"]
        ax.scatter([p["area_total"] for p in rp],
                   [p["makespan"] for p in rp],
                   s=24, marker="o", color=cmap(kidx[k]), alpha=0.4,
                   edgecolor="none")
        ax.scatter([p["area_total"] for p in bp],
                   [p["makespan"] for p in bp],
                   s=32, marker="^", color=cmap(kidx[k]), alpha=0.75,
                   edgecolor="none", label=label)
    ax.plot([p["area_total"] for p in front],
            [p["makespan"] for p in front],
            "-o", color="#111827", lw=2.3, ms=5, zorder=6,
            label="global Pareto (E=2)")
    for p in front:
        tag = (f"Q{p['Q']}" if p["mode"] == "buffered" else "cal") + \
            f" W{p['W']}/E{p['E']}/B{p['B']}"
        ax.annotate(f"{SCHEMES[p['scheme']][0]} {tag}",
                    (p["area_total"], p["makespan"]),
                    textcoords="offset points", xytext=(6, 4),
                    fontsize=6, color="#111827")
    ax.set_xlabel("area incl. port buffers (IQ-XY=1.0)")
    ax.set_ylabel("makespan (cycles)")
    ax.set_title(f"E≡{E_FIXED}: rigid (o) vs port-buffered (^)")
    ax.set_yscale("log")
    ax.grid(True, ls=":", alpha=0.5, which="both")
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(PNG_PB, dpi=130)
    plt.close(fig)


def main() -> None:
    rigid = _load("multi_area_makespan.json")
    mf = _load("multiflit_area_makespan.json")
    pb = _load("portbuf_area_makespan.json")
    lb = rigid["model"]["lower_bound_rb2"]

    rpts = [p for p in rigid["points"]
            if p["E"] == E_FIXED and p["makespan"] is not None]
    rfront = pareto(rpts, "area_total", "makespan")
    rfront.sort(key=lambda p: p["area_total"])
    plot_rigid(rpts, rfront, lb)

    mpts = [p for p in mf["points"]
            if p["E"] == E_FIXED and p["t_avg"] is not None]
    mfront_avg = pareto(mpts, "area_total", "t_avg")
    mfront_avg.sort(key=lambda p: p["area_total"])
    mfront_t15 = pareto(mpts, "t1", "t5")
    mfront_t15.sort(key=lambda p: p["t1"])
    lb1 = min(p["t1"] for p in mpts)
    plot_multiflit(mpts, mfront_avg, mfront_t15, lb1)

    floors = {}
    for k, meta in mf["scheme_meta"].items():
        sp = [p for p in mpts if p["scheme"] == k]
        floors[k] = {
            "label": meta["label"],
            "link_reuse": meta["link_reuse"],
            "t1": min(p["t1"] for p in sp),
            "ii": min(p["ii"] for p in sp),
            "t5": min(p["t5"] for p in sp),
            "t_avg": min(p["t_avg"] for p in sp),
        }

    rigid_e2 = [
        {**p, "mode": "rigid", "Q": 0,
         "label": f"{p['label']} rigid W{p['W']}/E{p['E']}/B{p['B']}"}
        for p in rpts
    ]
    buf_e2 = [p for p in pb["buffered_points"]
              if p["E"] == E_FIXED and p["makespan"] is not None]
    pb_pts = rigid_e2 + buf_e2
    pb_front = pareto(pb_pts, "area_total", "makespan")
    pb_front.sort(key=lambda p: p["area_total"])
    plot_portbuf(pb_pts, pb_front)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "E_fixed": E_FIXED,
        "note": "Filtered from full DSE; W,B free under W>=E and "
                "B=0 only when W==E. Area includes E=2 SRAM multiwrite cost.",
        "rigid": {
            "n_points": len(rpts),
            "pareto": rfront,
            "plot": str(PNG_RIGID.relative_to(ROOT)),
            "scheme_floors": {
                k: {
                    "label": rigid["scheme_meta"][k]["label"],
                    "floor": min(p["makespan"] for p in rpts
                                 if p["scheme"] == k),
                    "area_at_floor": min(
                        p["area_total"] for p in rpts
                        if p["scheme"] == k and p["makespan"] == min(
                            q["makespan"] for q in rpts if q["scheme"] == k)),
                }
                for k in rigid["scheme_meta"]
            },
        },
        "multiflit": {
            "n_points": len(mpts),
            "pareto_area_tavg": mfront_avg,
            "pareto_t1_t5": mfront_t15,
            "scheme_floors": floors,
            "plot": str(PNG_MF.relative_to(ROOT)),
        },
        "portbuf": {
            "n_points": len(pb_pts),
            "pareto_global": pb_front,
            "plot": str(PNG_PB.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"rigid Pareto: {[(p['label'], p['makespan']) for p in rfront]}")
    print(f"T_avg Pareto: {[(p['label'], p['t_avg']) for p in mfront_avg]}")
    print(f"portbuf Pareto: {[(p['label'], p['makespan']) for p in pb_front]}")
    print(f"Wrote {OUT_JSON}\nWrote {PNG_RIGID}\nWrote {PNG_MF}\nWrote {PNG_PB}")


if __name__ == "__main__":
    main()
