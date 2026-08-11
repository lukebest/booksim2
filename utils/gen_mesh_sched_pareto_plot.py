#!/usr/bin/env python3
"""Pareto plots for the mesh scheduler family (makespan vs scheduler area).

Reads results/mesh_sched_pareto.json and writes

    results/mesh_sched_pareto.png            5 pattern x 3 m panel grid
    results/mesh_sched_pareto_<pattern>.png  one large plot per pattern
    results/mesh_sched_lambda.png            trade-off weight sensitivity

    python3 utils/gen_mesh_sched_pareto_plot.py [--topo mesh] [--plane bufferless]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "mesh_sched_pareto.json"

PATTERNS = ("alltoall", "allgather", "allreduce", "broadcast", "reduce")
MS = (1, 4, 16)

# batch/slot = circle, incremental/slot = triangle, pipelined = square
STYLE = {
    "bvn_mesh":   ("#2563eb", "o", "BvN"),
    "mwm_mesh":   ("#7c3aed", "o", "MWM"),
    "latin_mesh": ("#0891b2", "o", "Latin"),
    "bcfs":       ("#dc2626", "s", "BCFS"),
    "islip_mesh": ("#ea580c", "^", "iSLIP"),
    "pim_mesh":   ("#65a30d", "^", "PIM"),
    "greedy_ff":  ("#be123c", "s", "GreedyFF"),
}


def pareto(pts: list[dict], xk: str, yk: str) -> list[dict]:
    out = [p for p in pts
           if not any(o is not p and o[xk] <= p[xk] and o[yk] <= p[yk]
                      and (o[xk] < p[xk] or o[yk] < p[yk]) for o in pts)]
    return sorted(out, key=lambda p: (p[xk], p[yk]))


def _label(r: dict) -> str:
    tag = STYLE[r["algo"]][2]
    if r["algo"] in ("islip_mesh", "pim_mesh") and r["iters"] != 1:
        tag += f"·I{r['iters']}"
    return tag


def _panel(ax, sel: list[dict], title: str, fifo: int | None,
           show_labels: bool = True) -> None:
    if not sel:
        ax.set_visible(False)
        return
    front = pareto(sel, "area_norm", "makespan")
    fids = {(r["algo"], r["iters"]) for r in front}
    for r in sel:
        col, mk, _ = STYLE[r["algo"]]
        on = (r["algo"], r["iters"]) in fids
        ax.scatter([r["area_norm"]], [r["makespan"]],
                   s=104 if on else 44, marker=mk, color=col,
                   edgecolor="#111827" if on else "#9ca3af",
                   lw=1.3 if on else 0.6, zorder=3 if on else 2,
                   alpha=1.0 if on else 0.65)
    ax.plot([r["area_norm"] for r in front], [r["makespan"] for r in front],
            color="#111827", lw=1.3, ls="--", zorder=2, label="Pareto front")
    if show_labels:
        for r in front:
            ax.annotate(_label(r), (r["area_norm"], r["makespan"]),
                        textcoords="offset points", xytext=(6, 5),
                        fontsize=7.5, color="#111827")
    if fifo:
        ax.axhline(fifo, color="#6b7280", lw=1.0, ls=":", zorder=1,
                   label=f"FIFO baseline {fifo}")
    lb = min(r["data_span"] for r in sel)
    ax.axhline(lb, color="#059669", lw=1.0, ls="-.", zorder=1,
               label=f"best data span {lb}")
    ax.set_yscale("log")
    ax.set_title(title, fontsize=9.5)
    ax.grid(alpha=0.25, lw=0.5)
    ax.tick_params(labelsize=8)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--topo", default="mesh")
    ap.add_argument("--plane", default="bufferless")
    a = ap.parse_args()

    data = json.loads(a.src.read_text())
    rows = [r for r in data["rows"]
            if r["topo"] == a.topo and r["plane"] == a.plane]
    if not rows:
        raise SystemExit(f"no rows for topo={a.topo} plane={a.plane}")
    pats = [p for p in PATTERNS if any(r["pattern"] == p for r in rows)]
    ms = [m for m in MS if any(r["m"] == m for r in rows)]

    # ---- main panel grid -------------------------------------------------
    fig, axes = plt.subplots(len(pats), len(ms),
                             figsize=(4.3 * len(ms), 2.9 * len(pats)),
                             squeeze=False)
    for i, pat in enumerate(pats):
        for j, m in enumerate(ms):
            sel = [r for r in rows if r["pattern"] == pat and r["m"] == m]
            fifo = sel[0]["fifo_baseline"] if sel else None
            _panel(axes[i][j], sel, f"{pat}  m={m}", fifo,
                   show_labels=False)
            if j == 0:
                axes[i][j].set_ylabel("makespan (cy, log)", fontsize=8.5)
            if i == len(pats) - 1:
                axes[i][j].set_xlabel("scheduler area (norm/node)",
                                      fontsize=8.5)
    handles = [plt.Line2D([], [], color=c, marker=mk, ls="", ms=7, label=lb)
               for c, mk, lb in STYLE.values()]
    handles += [
        plt.Line2D([], [], color="#111827", ls="--", label="Pareto front"),
        plt.Line2D([], [], color="#6b7280", ls=":", label="FIFO baseline"),
        plt.Line2D([], [], color="#059669", ls="-.", label="best data span"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=5, fontsize=8.5,
               frameon=False, bbox_to_anchor=(0.5, 1.0))
    fig.suptitle(f"8x6 {a.topo} / {a.plane}: scheduler area vs makespan "
                 f"(T_sched charged back), Pareto per (pattern, m)",
                 fontsize=11, y=1.035)
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    out = ROOT / "results" / "mesh_sched_pareto.png"
    fig.savefig(out, dpi=145, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")

    # ---- one big figure per pattern --------------------------------------
    for pat in pats:
        fig, axes = plt.subplots(1, len(ms), figsize=(5.2 * len(ms), 4.6),
                                 squeeze=False)
        for j, m in enumerate(ms):
            sel = [r for r in rows if r["pattern"] == pat and r["m"] == m]
            fifo = sel[0]["fifo_baseline"] if sel else None
            _panel(axes[0][j], sel, f"{pat}  m={m}", fifo, show_labels=True)
            axes[0][j].set_xlabel("scheduler area (norm/node)", fontsize=9)
            if j == 0:
                axes[0][j].set_ylabel("makespan (cy, log)", fontsize=9)
                axes[0][j].legend(fontsize=7.5, loc="upper right",
                                  frameon=False)
        fig.suptitle(f"8x6 {a.topo} / {a.plane} — {pat}", fontsize=12)
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        out = ROOT / "results" / f"mesh_sched_pareto_{pat}.png"
        fig.savefig(out, dpi=145, bbox_inches="tight")
        plt.close(fig)
        print(f"wrote {out}")

    # ---- lambda sensitivity ---------------------------------------------
    lams = [float(x) for x in data["meta"]["lambdas"]]
    lam_tbl = data["lambda_sensitivity"]
    fig, axes = plt.subplots(1, len(ms), figsize=(5.0 * len(ms), 4.4),
                             squeeze=False)
    algos = list(STYLE)
    for j, m in enumerate(ms):
        ax = axes[0][j]
        for i, pat in enumerate(pats):
            key = f"{a.topo}|{a.plane}|{pat}|m{m}"
            rec = lam_tbl.get(key)
            if not rec:
                continue
            ys = [algos.index(rec[str(l)]["algo"]) for l in lams]
            ax.plot(lams, ys, marker="o", ms=5, lw=1.5, label=pat,
                    color=plt.cm.tab10(i))
        ax.set_xscale("symlog", linthresh=0.25)
        ax.set_yticks(range(len(algos)))
        ax.set_yticklabels([STYLE[k][2] for k in algos], fontsize=8.5)
        ax.set_xlabel("λ  (normalized cycles paid per unit area)", fontsize=9)
        ax.set_title(f"m={m}", fontsize=10)
        ax.grid(alpha=0.25, lw=0.5)
        if j == 0:
            ax.legend(fontsize=8, frameon=False, loc="lower right")
    fig.suptitle(f"8x6 {a.topo} / {a.plane}: winner of "
                 f"min(makespan/makespan* + λ·area) vs λ", fontsize=11.5)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    out = ROOT / "results" / "mesh_sched_lambda.png"
    fig.savefig(out, dpi=145, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
