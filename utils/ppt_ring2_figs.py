#!/usr/bin/env python3
"""Deck-only figures for the architecture review PPT (ppt/index.html).

The report figures are correct but were drawn for a scrolling HTML page: the
Pareto chart labels in a long English column, and the trade-off is a dense
three-panel derivation. On a projected slide both are unreadable. This script
re-renders the same numbers from the same JSON with slide geometry: Chinese
labels, numbered markers plus a key, and one conclusion per panel.

No new analysis. Every value is read from results/*.json.

Usage:
    python3 utils/ppt_ring2_figs.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
OUT = ROOT / "ppt" / "images"

RED = "#c7000b"
GREY = "#8b939e"
INK = "#22252b"
BLUE = "#1f4e79"


def _use_cjk_font() -> None:
    from matplotlib import font_manager as fm
    wanted = ("micro hei", "cjk", "noto sans sc", "source han sans")
    for f in fm.fontManager.ttflist:
        if any(w in f.name.lower() for w in wanted):
            plt.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def fig_tradeoff() -> None:
    """One panel: the exact R(J) frontier, the two acceptance lines, S0/S1."""
    d = json.loads((RES / "tradeoff_ring2_cc.json").read_text())
    pts = [(p["jain_target"], p["bw_monotone"]) for p in d["jain_curve"]]
    pts.sort()
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    r_max, r_fair = d["r_max"], d["r_fair"]
    s0_bw = d["s0_bw"]
    inv = {round(p["jain_target"], 3): p["bw_monotone"] for p in d["inverse"]}
    meas = {m["name"]: m for m in d["measured"]}

    fig, ax = plt.subplots(figsize=(11.6, 5.6))
    ax.fill_between([0.99, 1.0], 0.99 * s0_bw, r_max + 0.2,
                    color="#2f9e44", alpha=0.10, zorder=0)
    ax.plot(xs, ys, "-", c=RED, lw=2.8, zorder=3,
            label="理想拥塞控制的精确前沿 R(J)（SOCP 解）")

    ax.axhline(r_max, c=GREY, ls="--", lw=1.1)
    ax.axhline(r_fair, c=GREY, ls=":", lw=1.1)
    ax.axhline(s0_bw, c=BLUE, ls="-.", lw=1.2)
    ax.axvline(0.99, c="#2f9e44", ls=":", lw=1.2)

    ax.annotate(f"最大吞吐 R_max = {r_max:.4f}（饿死 2 个核，Jain 0.914）",
                xy=(0.868, r_max + 0.05), fontsize=9.5, color="#5b636d",
                va="bottom")
    ax.annotate(f"严格等速率 R* = {r_fair:.4f}",
                xy=(0.868, r_fair + 0.03), fontsize=9.5, color="#5b636d")
    ax.annotate(f"S0 实测 {s0_bw:.4f}", xy=(0.868, s0_bw + 0.03),
                fontsize=9.5, color=BLUE)

    j99 = inv[0.99]
    ax.scatter([0.99], [j99], s=180, marker="*", c="#2f9e44",
               zorder=5, edgecolors="k", linewidths=0.6)
    ax.annotate(f"J = 0.99 处前沿 = {j99:.4f}\n比 S0 还高 +9.81%",
                xy=(0.99, j99), xytext=(0.930, 6.12), fontsize=11,
                color="#1a7f37", fontweight="bold",
                arrowprops=dict(arrowstyle="->", color="#2f9e44", lw=1.4))

    for nm, lbl, col in (("S0 baseline", "S0", BLUE),
                         ("S1 AIMD", "S1", "#d97706"),
                         ("S1T AIMD dir-split", "S1T", "#d97706")):
        m = meas.get(nm)
        if not m:
            continue
        ax.scatter([m["jain_bin"]], [m["bw"]], s=90, c=col, zorder=5,
                   edgecolors="k", linewidths=0.5)
        ax.annotate(lbl, xy=(m["jain_bin"], m["bw"]), xytext=(4, -13),
                    textcoords="offset points", fontsize=10.5, color=col,
                    fontweight="bold")

    ax.text(0.9925, 4.62, "验收区\nJain>0.99\n带宽≥99%·S0", fontsize=9.5,
            color="#1a7f37", ha="left", va="bottom")

    ax.set_xlim(0.865, 1.003)
    ax.set_ylim(4.2, 6.62)
    ax.set_xlabel("公平性：50 拍分箱 Jain")
    ax.set_ylabel("总写带宽 flit/cycle")
    ax.set_title("公平性 — 总带宽的精确交换曲线：验收区在前沿之下，两条线不互斥",
                 fontsize=13, fontweight="bold")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=10, loc="lower left")
    fig.tight_layout()
    p = OUT / "16-tradeoff.png"
    fig.savefig(p, dpi=170)
    print(f"wrote {p}")


def fig_pareto() -> None:
    """Numbered markers plus a key column: readable at projector distance."""
    import sys
    sys.path.insert(0, str(ROOT / "utils"))
    from pareto_ring2_cc import frontier

    reg = json.loads((RES / "pareto_ring2_cc.json").read_text())
    rows = sorted(reg["schemes"], key=lambda r: -r["eta"])
    ideal = reg["ideal"]
    front = {r["name"] for r in frontier(reg["schemes"])}
    need = (0.99 * 0.99 * ideal["s0_thr"]) / ideal["u"]

    fig = plt.figure(figsize=(13.6, 6.4))
    ax = fig.add_axes([0.055, 0.10, 0.505, 0.79])
    key = fig.add_axes([0.585, 0.02, 0.405, 0.94])
    key.axis("off")

    for i, r in enumerate(rows, 1):
        feas = r.get("bus_rule_ok", True)
        x, y = max(r["hw_cost"], 1), r["eta"]
        if not feas:
            c, m, s = GREY, "X", 130
        elif r["name"] in front:
            c, m, s = RED, "o", 150
        else:
            c, m, s = "#5b636d", "o", 80
        ax.scatter(x, y, s=s, c=c, marker=m, zorder=3,
                   edgecolors="k", linewidths=0.6)
        # Several schemes share a hardware cost or land within 0.002 of each
        # other in eta; a fixed offset stacks their numbers on top of one
        # another. Fan them out by rank parity instead.
        dx, dy = ((0, 10), (-11, 3), (11, 3), (0, -14))[i % 4]
        ax.annotate(str(i), xy=(x, y), xytext=(dx, dy),
                    textcoords="offset points", fontsize=9,
                    ha="center", color=INK, fontweight="bold")

    fpts = [(max(r["hw_cost"], 1), r["eta"]) for r in frontier(reg["schemes"])]
    ax.plot([p[0] for p in fpts], [p[1] for p in fpts], "--", c=RED, lw=1.4,
            alpha=0.8, label="可实现 Pareto 前沿")
    ax.axhline(1.0, c="#b34700", lw=1.6,
               label=f"理想控制器 η = 1.0（{ideal['bw']:.4f} flit/cycle，"
                     f"Jain {ideal['jain_bin']:.4f}）")
    ax.axhline(need, c="#2f9e44", ls=":", lw=1.5,
               label=f"双线达标需 η ≥ {need:.3f}")
    ax.axhline(reg_s0(rows), c=GREY, ls="-.", lw=1.1,
               label=f"S0 基线 η = {reg_s0(rows):.3f}")

    ax.set_xscale("log")
    ax.set_xlim(0.6, 4e6)
    ax.set_ylim(0.33, 1.06)
    ax.set_xlabel("新增硬件状态（FF 等效，对数轴）→ 越贵")
    ax.set_ylabel("η = （总带宽 × 分箱 Jain）/ 理想控制器同项")
    ax.set_title("收益 — 硬件开销 Pareto（写，uniform，K=2000）\n"
                 "灰 X = 需要快于 30 拍的总线，不可实现；红 = 前沿",
                 fontsize=12, fontweight="bold")
    ax.grid(alpha=0.25, which="both")
    ax.legend(fontsize=8.5, loc="lower left")

    cols = ((0.00, "#"), (0.055, "方案（按 η 降序）"), (0.60, "η"),
            (0.71, "Jain"), (0.83, "带宽/R*"), (1.00, "FF-eq"))
    aligns = ("left", "left", "right", "right", "right", "right")
    key.text(0.0, 0.985, "图例", fontsize=10.5, fontweight="bold",
             color=INK, va="top")
    for (x, t), al in zip(cols, aligns):
        key.text(x, 0.938, t, fontsize=8.8, color="#5b636d", va="top",
                 ha=al, fontweight="bold")
    for i, r in enumerate(rows, 1):
        feas = r.get("bus_rule_ok", True)
        col = GREY if not feas else (RED if r["name"] in front else INK)
        nm = r["name"]
        nm = nm if len(nm) <= 34 else nm[:33] + "…"
        y = 0.938 - i * 0.0545
        vals = (str(i), nm, f"{r['eta']:.4f}", f"{r['jain_bin']:.4f}",
                f"{r['bw_vs_ideal']:.3f}", f"{r['hw_cost']:,}")
        for (x, _), al, v in zip(cols, aligns, vals):
            key.text(x, y, v, fontsize=8.8, color=col, va="top", ha=al)

    p = OUT / "18-pareto.png"
    fig.savefig(p, dpi=170)
    print(f"wrote {p}")


def reg_s0(rows: list[dict]) -> float:
    return next(r["eta"] for r in rows if r["name"].startswith("S0"))


def fig_s1_effect() -> None:
    """S1's own trade: per-core bandwidth, and where the two knobs land."""
    d = json.loads((RES / "ring2_write_fair.json").read_text())
    sch = d["patterns"]["uniform"]["schemes"]
    cores = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]

    def bw(name: str) -> list[float]:
        f = sch[name]["fairness"]["bw_by_core"]
        return [f[str(c)] for c in cores]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.4, 5.0),
                                 gridspec_kw={"width_ratios": [1.32, 1.0]})

    w = 0.27
    xs = list(range(len(cores)))
    for k, (nm, lbl, col) in enumerate(
            (("S0", "S0 无流控", BLUE),
             ("S1", "S1 拥塞等级 AIMD", RED),
             ("S1T", "S1T 每向预算（调参后）", "#d97706"))):
        a1.bar([x + (k - 1) * w for x in xs], bw(nm), width=w, label=lbl,
               color=col, alpha=0.92)
    a1.set_xticks(xs)
    a1.set_xticklabels([f"C{c}" for c in cores])
    a1.set_ylim(0.30, 0.78)
    a1.set_ylabel("每核写带宽 flit/cycle")
    a1.set_xlabel("AI core（C0 / C8 / C10 / C18 邻 mem = 1）")
    a1.set_title("S1 把受害核压得更低：C0/C8/C10/C18 被自己的乘性减罚下去",
                 fontsize=11.5, fontweight="bold")
    a1.grid(axis="y", alpha=0.25)
    a1.legend(fontsize=9.5, loc="upper center", ncol=3)

    pts = [("S0", sch["S0"], BLUE), ("S1", sch["S1"], RED),
           ("S1T", sch["S1T"], "#d97706")]
    off = {"S0": (8, 6), "S1": (8, 6), "S1T": (8, -16)}
    for lbl, r, col in pts:
        f = r["fairness"]
        a2.scatter([f["throughput"]], [f["jain_bin"]["jain_bin_mean"]],
                   s=170, c=col, edgecolors="k", linewidths=0.6, zorder=4)
        a2.annotate(lbl, xy=(f["throughput"],
                             f["jain_bin"]["jain_bin_mean"]),
                    xytext=off[lbl], textcoords="offset points",
                    fontsize=12, fontweight="bold", color=col)
    s0 = sch["S0"]["fairness"]["throughput"]
    a2.axvline(s0, c=GREY, ls="-.", lw=1.0)
    a2.axvline(0.99 * s0, c="#2f9e44", ls=":", lw=1.2)
    a2.axhline(0.99, c="#2f9e44", ls=":", lw=1.2)
    a2.fill_between([0.99 * s0, 5.75], 0.99, 1.005, color="#2f9e44",
                    alpha=0.12)
    a2.text(5.42, 0.9935, "验收区", fontsize=10.5, color="#1a7f37",
            ha="right")
    a2.annotate("", xy=(sch["S1"]["fairness"]["throughput"], 0.9469),
                xytext=(s0, 0.87865),
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.6))
    a2.text(5.03, 0.884, "带宽 −14.3%\n换 Jain +0.068", fontsize=10.5,
            color=RED, ha="center")
    a2.set_xlim(4.45, 5.75)
    a2.set_ylim(0.855, 1.005)
    a2.set_xlabel("总写带宽 flit/cycle")
    a2.set_ylabel("50 拍分箱平均 Jain")
    a2.set_title("两个旋钮各拿一头，都进不了验收区", fontsize=11.5,
                 fontweight="bold")
    a2.grid(alpha=0.25)

    fig.tight_layout()
    p = OUT / "12-s1-effect.png"
    fig.savefig(p, dpi=170)
    print(f"wrote {p}")


def main() -> None:
    _use_cjk_font()
    OUT.mkdir(parents=True, exist_ok=True)
    fig_tradeoff()
    fig_pareto()
    fig_s1_effect()


if __name__ == "__main__":
    main()
