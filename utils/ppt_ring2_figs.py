#!/usr/bin/env python3
"""Deck-only figures for the architecture review PPT.

The report figures are correct but were drawn for a scrolling HTML page. On a
projected slide they are unreadable, and a few of them make comparisons the
review explicitly does not want -- notably plotting a *binned* bandwidth
against a bound that only holds on the *average*, which lets a simulated curve
sit above a theoretical line and look like a bug.

Every value here is read from results/*.json. No new analysis, except the
regrouping of bins that the window-width panel needs, which is arithmetic on
counts already measured.

Usage:
    python3 utils/ppt_ring2_figs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
OUT = ROOT / "ppt" / "images"
sys.path.insert(0, str(ROOT / "utils"))

RED = "#c7000b"
GREY = "#8b939e"
INK = "#22252b"
BLUE = "#1f4e79"
GREEN = "#1a7f37"
AMBER = "#d97706"
PANEL = "#eef2f7"


def _use_cjk_font() -> None:
    from matplotlib import font_manager as fm
    wanted = ("micro hei", "cjk", "noto sans sc", "source han sans")
    for f in fm.fontManager.ttflist:
        if any(w in f.name.lower() for w in wanted):
            plt.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def deck() -> dict:
    return json.loads((RES / "deck_ring2_data.json").read_text())


def jain(v) -> float:
    s = sum(v)
    q = sum(x * x for x in v)
    return (s * s) / (len(v) * q) if q else 1.0


def jain_ideal(n_flits: int, n_cores: int) -> float:
    if n_flits <= 0:
        return 1.0
    lo, r = divmod(int(n_flits), n_cores)
    sq = r * (lo + 1) ** 2 + (n_cores - r) * lo * lo
    return (n_flits * n_flits) / (n_cores * sq) if sq else 1.0


def save(fig, name: str) -> None:
    p = OUT / name
    fig.savefig(p, dpi=170)
    plt.close(fig)
    print(f"wrote {p}")


# --------------------------------------------------------------- slide 08
def fig_saturation() -> None:
    """S0 is bandwidth-saturated, and the residual gap is accounted for.

    Neither panel puts a simulated bandwidth on the same axis as R*. The left
    one measures how full the ring's links are, which is bounded by 1 by
    construction; the right one spends the binding link's cycle budget, which
    adds up to the makespan identically. A per-bin bandwidth trace is
    deliberately absent: R* bounds the long-run average only, so a 100-cycle
    bin may legitimately sit above it, and showing the two together reads as
    the simulation beating the theory.
    """
    d = deck()
    s0 = d["write"]["S0"]
    r_fair = d["ideal"]["r_fair"]
    g = s0["ceiling_gap"]
    util = s0["hop_util"]

    fig, (ax, bx) = plt.subplots(
        1, 2, figsize=(13.6, 4.05), gridspec_kw={"width_ratios": [1.24, 1.0]})

    # DAT and RSP carry two flits per write over identical paths, so their
    # occupancy curves coincide exactly -- drawing both would hide one.
    for vcs, col, lab in ((("dat", "rsp"), RED, "DAT / RSP（每笔写各 2 flit，"
                                                "两条曲线完全重合）"),
                          (("req",), GREY, "REQ（每笔写 1 flit）")):
        ys = sorted((v for k, v in util.items()
                     if k.rsplit(":", 1)[1] == vcs[0]), reverse=True)
        ax.plot(range(1, len(ys) + 1), ys, lw=2.2, color=col, label=lab)
    ax.axhline(1.0, color="#b34700", ls="--", lw=1.5,
               label="物理上限：每条链路每拍 1 flit")
    top = g["util"]
    ax.annotate(f"最忙的 8 条链路都已 {100 * top:.2f}% 满",
                xy=(2.6, top), xytext=(15, 0.84), fontsize=11,
                color=RED, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.4))
    ax.set_xlabel("同一 VC 的 40 条有向链路，按占用率降序")
    ax.set_ylabel("链路占用率 = 该链路被占用的拍数 / 总拍数")
    ax.set_ylim(0, 1.10)
    ax.set_title("最忙的链路已经接近全满 —— 带宽是被链路卡住的",
                 fontsize=12, fontweight="bold")
    ax.grid(alpha=0.22)
    ax.legend(fontsize=8.6, loc="lower left")

    # --- right: the binding link's cycle budget ------------------------------
    floor, sur, idle = g["floor"], g["surcharge"], g["idle"]
    segs = [(floor, BLUE, f"真的在搬数据：{floor:,} 拍"),
            (sur, AMBER, f"绕环重发多占的：{sur:,} 拍"),
            (idle, GREY, f"空转：{idle:,} 拍")]
    left = 0
    for v, c, lab in segs:
        if v <= 0:
            continue
        bx.barh([0], [v], left=left, color=c, height=0.34, label=lab)
        left += v
    bx.barh([0.55], [floor], color=BLUE, height=0.34)
    bx.text(floor / 2, 0.55, f"{floor:,} 拍，一拍不浪费", ha="center",
            va="center", fontsize=10, color="white", fontweight="bold")
    bx.set_ylim(-0.92, 1.02)
    bx.set_yticks([0.55, 0])
    bx.set_yticklabels([f"理论上限 R* = {r_fair:.4f}\n所需拍数",
                        f"S0 实测 = {s0['throughput']:.4f}\n实际拍数"],
                       fontsize=10)
    bx.set_xlabel("最忙的那条链路搬完同样多的数据各花了多少拍")
    bx.set_xlim(0, left * 1.02)
    pct = 100 * s0["throughput"] / r_fair
    bx.set_title(f"多花的 {100 - pct:.2f}% 几乎全是空转，不是被谁抢走了",
                 fontsize=12, fontweight="bold")
    bx.legend(fontsize=9, loc="lower center", framealpha=0.95,
              bbox_to_anchor=(0.5, -0.02))
    bx.grid(axis="x", alpha=0.22)
    for s in ("top", "right", "left"):
        bx.spines[s].set_visible(False)

    fig.tight_layout()
    save(fig, "07-totalbw.png")


# --------------------------------------------------------------- slide 10
def fig_instbal() -> None:
    """The fairness defect: per-bin Jain over time, and how wide a window
    you have to open before it goes away."""
    d = deck()
    s0 = d["write"]["S0"]
    bw = d["meta"]["bin_w"]
    n = d["meta"]["n_cores"]
    jb = s0["jain_bin"]
    reg = s0["regular"]

    cnt = {c: [round(x * bw) for x in v]
           for c, v in s0["per_core_binned"].items()}
    cs = sorted(cnt, key=int)
    nb = len(cnt[cs[0]])
    per_bin = [jain([cnt[c][b] for c in cs]) for b in range(nb)]

    fig, (ax, bx) = plt.subplots(
        1, 2, figsize=(13.6, 4.15), gridspec_kw={"width_ratios": [1.5, 1.0]})

    xs = s0["bin_t"]
    ax.plot(xs, per_bin, lw=0.5, color=GREY, alpha=0.9,
            label=f"每 {bw} 拍窗的 Jain")
    win = 40
    sm = [sum(per_bin[max(0, i - win):i + win + 1])
          / len(per_bin[max(0, i - win):i + win + 1]) for i in range(nb)]
    ax.plot(xs, sm, lw=2.2, color=BLUE, label="滑动平均")
    ax.axhline(jb["jain_bin_mean"], color=RED, ls="--", lw=1.6,
               label=f"均值 = {jb['jain_bin_mean']:.5f}")
    ax.axhline(reg["jain_regular"], color=GREEN, ls=":", lw=1.8,
               label=f"抖动抹平后的上限 = {reg['jain_regular']:.5f}")
    ax.set_xlabel("cycle")
    ax.set_ylabel(f"{bw} 拍窗内 10 个核的 Jain")
    ax.set_ylim(min(per_bin) - 0.02, 1.005)
    ax.set_title("不是个别坏箱：整段都低，而且低得很稳",
                 fontsize=12, fontweight="bold")
    ax.grid(alpha=0.22)
    ax.legend(fontsize=9, loc="lower right", ncol=2)

    groups = [1, 2, 4, 8, 16, 32, 64]
    obs, idl, wid = [], [], []
    for g in groups:
        m = nb // g
        if m < 8:
            break
        o, i2 = [], []
        for b in range(m):
            v = [sum(cnt[c][b * g:(b + 1) * g]) for c in cs]
            o.append(jain(v))
            i2.append(jain_ideal(sum(v), n))
        obs.append(sum(o) / m)
        idl.append(sum(i2) / m)
        wid.append(g * bw)
    bx.plot(wid, obs, "o-", color=RED, lw=2.0, ms=5, label="S0 实测")
    bx.plot(wid, idl, "s--", color=GREY, lw=1.4, ms=4, label="理想控制器")
    bx.axhline(reg["jain_regular"], color=GREEN, ls=":", lw=1.6,
               label="只整时机的天花板")
    bx.set_xscale("log", base=2)
    bx.set_xlabel("观察窗宽度（拍，对数轴）")
    bx.set_ylabel("窗内 Jain 均值")
    bx.set_title("窗放得再宽也追不上理想：抖动能消，速率差消不掉",
                 fontsize=12, fontweight="bold")
    bx.grid(alpha=0.22, which="both")
    bx.legend(fontsize=9, loc="lower right")

    fig.tight_layout()
    save(fig, "09-s0-instbal.png")


# --------------------------------------------------------------- slide 14
def fig_s1_effect() -> None:
    """S1's own trade: per-core bandwidth, and where the knobs land."""
    d = deck()
    w = d["write"]
    bw = d["meta"]["bin_w"]
    cores = d["meta"]["cores"]

    def by_core(nm):
        f = w[nm]["bw_by_core"]
        return [f[str(c)] for c in cores]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.6, 4.35),
                                 gridspec_kw={"width_ratios": [1.34, 1.0]})
    width = 0.27
    xs = list(range(len(cores)))
    for k, (nm, lbl, col) in enumerate(
            (("S0", "S0 无流控", BLUE),
             ("S1", "S1 拥塞等级 AIMD", RED),
             ("S1T", "S1T 每向预算（调参后）", AMBER))):
        a1.bar([x + (k - 1) * width for x in xs], by_core(nm), width=width,
               label=lbl, color=col, alpha=0.92)
    a1.set_xticks(xs)
    a1.set_xticklabels([f"C{c}" for c in cores])
    lo = min(min(by_core(n)) for n in ("S0", "S1", "S1T"))
    hi = max(max(by_core(n)) for n in ("S0", "S1", "S1T"))
    a1.set_ylim(lo - 0.10, hi + 0.09)
    a1.set_ylabel("每核写带宽 flit/cycle")
    a1.set_xlabel("AI core（C0 / C8 / C10 / C18 邻 mem = 1）")
    a1.set_title("S1 把十个核一起压低；S1T 把带宽拿回来后又变回 S0",
                 fontsize=11.5, fontweight="bold")
    a1.grid(axis="y", alpha=0.25)
    a1.legend(fontsize=9.5, loc="upper center", ncol=3)

    off = {"S0": (9, -16), "S1": (10, -4), "S1T": (9, 9)}
    for lbl, col in (("S0", BLUE), ("S1", RED), ("S1T", AMBER)):
        r = w[lbl]
        x, y = r["throughput"], r["jain_bin"]["jain_bin_mean"]
        a2.scatter([x], [y], s=180, c=col, edgecolors="k", linewidths=0.6,
                   zorder=4)
        a2.annotate(lbl, xy=(x, y), xytext=off[lbl],
                    textcoords="offset points", fontsize=12,
                    fontweight="bold", color=col)
    s0x = w["S0"]["throughput"]
    s0y = w["S0"]["jain_bin"]["jain_bin_mean"]
    a2.axvline(s0x, c=GREY, ls="-.", lw=1.0)
    a2.annotate("", xy=(w["S1"]["throughput"],
                        w["S1"]["jain_bin"]["jain_bin_mean"]),
                xytext=(s0x, s0y),
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.8))
    dpct = 100 * (w["S1"]["throughput"] / s0x - 1)
    dj = w["S1"]["jain_bin"]["jain_bin_mean"] - s0y
    a2.text((s0x + w["S1"]["throughput"]) / 2, s0y - 0.011,
            f"带宽 {dpct:+.1f}%\n换 Jain {dj:+.3f}", fontsize=11,
            color=RED, ha="center", va="top")
    a2.set_xlim(min(w[n]["throughput"] for n in ("S0", "S1", "S1T")) - 0.25,
                max(w[n]["throughput"] for n in ("S0", "S1", "S1T")) + 0.25)
    a2.set_ylim(0.905, 0.975)
    a2.set_xlabel("总写带宽 flit/cycle")
    a2.set_ylabel(f"{bw} 拍分箱平均 Jain")
    a2.set_title("两个旋钮各拿一头，没有一个点同时变好",
                 fontsize=11.5, fontweight="bold")
    a2.grid(alpha=0.25)

    fig.tight_layout()
    save(fig, "12-s1-effect.png")


# --------------------------------------------------------------- slide 17
def fig_tradeoff() -> None:
    """The exact R(J) frontier, with every symbol on the slide named."""
    d = json.loads((RES / "tradeoff_ring2_cc.json").read_text())
    dk = deck()
    pts = sorted((p["jain_target"], p["bw_monotone"]) for p in d["jain_curve"])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    r_max, r_fair = d["r_max"], d["r_fair"]
    s0 = dk["write"]["S0"]
    s0_bw = s0["throughput"]
    s0_j = s0["jain_bin"]["jain_bin_mean"]
    inv = {round(p["jain_target"], 3): p["bw_monotone"] for p in d["inverse"]}

    fig, ax = plt.subplots(figsize=(11.6, 5.5))
    ax.plot(xs, ys, "-", c=RED, lw=2.8, zorder=3,
            label="R(J)：要求公平度至少 J 时，总带宽最多能做到多少")
    ax.axhline(r_max, c=GREY, ls="--", lw=1.1)
    ax.axhline(r_fair, c=GREY, ls=":", lw=1.1)
    ax.axhline(s0_bw, c=BLUE, ls="-.", lw=1.2)

    ax.annotate(f"R_max = {r_max:.4f}：完全不管公平时的最大总带宽"
                f"（代价是 2 个核被饿死）",
                xy=(0.868, r_max + 0.05), fontsize=9.5, color="#5b636d",
                va="bottom")
    ax.annotate(f"R* = {r_fair:.4f}：十个核严格等速率时的总带宽（曲线最右端）",
                xy=(0.868, r_fair + 0.04), fontsize=9.5, color="#5b636d")
    ax.annotate(f"S0 实测 {s0_bw:.4f}", xy=(0.868, s0_bw - 0.16),
                fontsize=9.5, color=BLUE)

    ax.annotate("", xy=(0.999, r_fair), xytext=(0.999, r_max),
                arrowprops=dict(arrowstyle="<->", color=GREEN, lw=1.8))
    ax.text(0.9945, (r_fair + r_max) / 2,
            f"公平的固有代价\n−{100 * (1 - r_fair / r_max):.2f}%",
            fontsize=11, color=GREEN, fontweight="bold", ha="right",
            va="center")

    ax.scatter([s0_j], [s0_bw], s=130, c=BLUE, zorder=5, edgecolors="k",
               linewidths=0.5)
    ax.annotate("S0", xy=(s0_j, s0_bw), xytext=(6, -15),
                textcoords="offset points", fontsize=11, color=BLUE,
                fontweight="bold")
    reach = max(y for x, y in pts if x <= s0_j)
    ax.annotate("S0 落在曲线下方：在它自己那个公平度上，\n"
                f"理想控制器还能多拿 {100 * (reach / s0_bw - 1):.1f}% 带宽 —— "
                "\n所以 S0 既不最快、也不最公平",
                xy=(s0_j, s0_bw), xytext=(0.888, 4.66), fontsize=10,
                color=BLUE,
                arrowprops=dict(arrowstyle="->", color=BLUE, lw=1.1))

    knee = inv[0.99]
    ax.annotate(f"曲线在这里开始变陡：\n最后 0.01 的 Jain 单价是前面的 3.6 倍",
                xy=(0.99, knee), xytext=(0.900, 6.22), fontsize=10,
                color=AMBER, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=AMBER, lw=1.2))

    ax.set_xlim(0.865, 1.003)
    ax.set_ylim(4.45, 6.62)
    ax.set_xlabel(f"公平度 J = {dk['meta']['bin_w']} 拍窗内十个核带宽的 Jain 指数"
                  "（1 = 完全均等）→ 越往右越公平")
    ax.set_ylabel("总写带宽 R  flit/cycle")
    ax.set_title("要多一点公平，到底要付多少带宽",
                 fontsize=13, fontweight="bold")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=10, loc="lower left")
    fig.tight_layout()
    save(fig, "16-tradeoff.png")


# --------------------------------------------------------------- slide 20
def fig_pareto() -> None:
    """Numbered markers plus a key column: readable at projector distance."""
    from pareto_ring2_cc import frontier

    reg = json.loads((RES / "pareto_ring2_cc.json").read_text())
    rows = sorted(reg["schemes"], key=lambda r: -r["eta"])
    ideal = reg["ideal"]
    front = {r["name"] for r in frontier(reg["schemes"])}

    fig = plt.figure(figsize=(9.9, 6.3))
    ax = fig.add_axes([0.085, 0.105, 0.455, 0.760])
    key = fig.add_axes([0.585, 0.02, 0.405, 0.94])
    key.axis("off")

    for i, r in enumerate(rows, 1):
        x, y = max(r["hw_cost"], 1), r["eta"]
        if r["name"] in front:
            c, s = RED, 150
        else:
            c, s = "#5b636d", 80
        ax.scatter(x, y, s=s, c=c, marker="o", zorder=3, edgecolors="k",
                   linewidths=0.6)
        dx, dy = ((0, 10), (-11, 3), (11, 3), (0, -14))[i % 4]
        ax.annotate(str(i), xy=(x, y), xytext=(dx, dy),
                    textcoords="offset points", fontsize=9, ha="center",
                    color=INK, fontweight="bold")

    fpts = [(max(r["hw_cost"], 1), r["eta"]) for r in frontier(reg["schemes"])]
    ax.plot([p[0] for p in fpts], [p[1] for p in fpts], "--", c=RED, lw=1.4,
            alpha=0.8, label="Pareto 前沿")
    ax.axhline(1.0, c="#b34700", lw=1.6,
               label=f"理想控制器 η = 1.0（{ideal['bw']:.4f} flit/cycle，"
                     f"Jain {ideal['jain_bin']:.4f}）")
    s0eta = next(r["eta"] for r in rows if r["name"].startswith("S0"))
    ax.axhline(s0eta, c=GREY, ls="-.", lw=1.1,
               label=f"S0 基线 η = {s0eta:.3f}")

    ax.set_xscale("log")
    ax.set_xlim(0.6, 4e6)
    ax.set_ylim(0.35, 1.06)
    ax.set_xlabel("新增硬件状态（FF 等效 = 折算成触发器个数，对数轴）→ 越贵")
    ax.set_ylabel("η = （总带宽 × 分箱 Jain）/ 理想控制器同项")
    ax.set_title("收益 — 硬件开销 Pareto（写，uniform，K=2000）\n"
                 "η 越高越接近理想控制器；红 = 前沿，即同价位无人能超",
                 fontsize=12, fontweight="bold")
    ax.grid(alpha=0.25, which="both")
    ax.legend(fontsize=8.5, loc="lower right")

    cols = ((0.00, "#"), (0.050, "方案（按 η 降序）"), (0.545, "η"),
            (0.675, "Jain"), (0.815, "带宽/R*"), (1.00, "FF 等效"))
    aligns = ("left", "left", "right", "right", "right", "right")
    key.text(0.0, 0.985, "图例", fontsize=12, fontweight="bold", color=INK,
             va="top")
    for (x, t), al in zip(cols, aligns):
        key.text(x, 0.935, t, fontsize=9.6, color="#5b636d", va="top", ha=al,
                 fontweight="bold")
    for i, r in enumerate(rows, 1):
        col = RED if r["name"] in front else INK
        nm = r["name"]
        nm = nm if len(nm) <= 22 else nm[:21] + "…"
        y = 0.935 - i * 0.0575
        vals = (str(i), nm, f"{r['eta']:.4f}", f"{r['jain_bin']:.4f}",
                f"{r['bw_vs_ideal']:.3f}", f"{r['hw_cost']:,}")
        for (x, _), al, v in zip(cols, aligns, vals):
            key.text(x, y, v, fontsize=9.6, color=col, va="top", ha=al)

    save(fig, "18-pareto.png")


# --------------------------------------------------------------- slide 21
def fig_hot() -> None:
    """Non-uniform traffic, bandwidth only.

    Instantaneous fairness is not plotted here on purpose: under `hot` the
    equal-rate optimum equals the max-total optimum, so fairness costs nothing
    and the interesting axis is whether a controller can hold the bandwidth at
    all when the congestion moves to the destination.
    """
    from pareto_ring2_cc import frontier

    d = json.loads((RES / "probe_ring2_hotbw.json").read_text())
    cap = str(d.get("cap") or 32)
    rows = d["passes"][cap] if "passes" in d else d["rows"]
    r_star = d["ideal"]["r_fair"]
    rows = sorted(rows, key=lambda r: -r["bw_vs_ideal"])
    for r in rows:                       # bandwidth is the only axis here
        r["eta"] = r["u"] = r["bw_vs_ideal"]
    front = {r["name"] for r in frontier(rows)}

    fig = plt.figure(figsize=(9.9, 6.3))
    ax = fig.add_axes([0.090, 0.115, 0.450, 0.745])
    key = fig.add_axes([0.590, 0.02, 0.400, 0.94])
    key.axis("off")

    for i, r in enumerate(rows, 1):
        x, y = max(r["hw_cost"], 1), r["bw_vs_ideal"]
        c, s = (RED, 150) if r["name"] in front else ("#5b636d", 80)
        ax.scatter(x, y, s=s, c=c, zorder=3, edgecolors="k", linewidths=0.6)
        dx, dy = ((0, 10), (-11, 3), (11, 3), (0, -14))[i % 4]
        ax.annotate(str(i), xy=(x, y), xytext=(dx, dy),
                    textcoords="offset points", fontsize=9, ha="center",
                    color=INK, fontweight="bold")
    fpts = [(max(r["hw_cost"], 1), r["bw_vs_ideal"]) for r in frontier(rows)]
    ax.plot([p[0] for p in fpts], [p[1] for p in fpts], "--", c=RED, lw=1.4,
            alpha=0.8, label="Pareto 前沿")
    ax.axhline(1.0, c="#b34700", lw=1.6,
               label=f"该 pattern 自己的 R* = {r_star:.4f} flit/cycle")
    s0 = next(r["bw_vs_ideal"] for r in rows if r["name"].startswith("S0"))
    ax.axhline(s0, c=GREY, ls="-.", lw=1.1, label=f"S0 基线 = {s0:.4f} R*")
    ax.set_xscale("log")
    ax.set_xlim(0.6, 4e6)
    ax.set_xlabel("新增硬件状态（FF 等效，对数轴）→ 越贵")
    ax.set_ylabel("总写带宽 / 该 pattern 自己的 R*")
    ax.set_title("固定非均匀流量（十个核全写 HA 11/13）：只看总带宽\n"
                 f"每核在飞上限 = {cap}，K = {d['k']}",
                 fontsize=12, fontweight="bold")
    ax.grid(alpha=0.25, which="both")
    ax.legend(fontsize=8.5, loc="lower right")

    cols = ((0.00, "#"), (0.050, "方案（按带宽降序）"), (0.640, "带宽/R*"),
            (0.815, "vs S0"), (1.00, "FF 等效"))
    aligns = ("left", "left", "right", "right", "right")
    key.text(0.0, 0.985, "图例", fontsize=12, fontweight="bold", color=INK,
             va="top")
    for (x, t), al in zip(cols, aligns):
        key.text(x, 0.935, t, fontsize=9.6, color="#5b636d", va="top", ha=al,
                 fontweight="bold")
    for i, r in enumerate(rows, 1):
        col = RED if r["name"] in front else INK
        nm = r["name"]
        nm = nm if len(nm) <= 22 else nm[:21] + "…"
        y = 0.935 - i * 0.0575
        vals = (str(i), nm, f"{r['bw_vs_ideal']:.4f}",
                f"{r['delta_vs_s0_pct']:+.2f}%", f"{r['hw_cost']:,}")
        for (x, _), al, v in zip(cols, aligns, vals):
            key.text(x, y, v, fontsize=9.6, color=col, va="top", ha=al)

    save(fig, "20-hot-pareto.png")


# ------------------------------------------------------- schematic helpers
def _box(ax, x, y, w, h, text, fc="white", ec=GREY, tc=INK, fs=9,
         bold=False, lw=1.2):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
        fc=fc, ec=ec, lw=lw, zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, zorder=3, fontweight="bold" if bold else "normal",
            linespacing=1.45)


def _arrow(ax, p, q, color=INK, lw=1.4, style="-|>", ls="-", rad=0.0):
    ax.add_patch(FancyArrowPatch(
        p, q, arrowstyle=style, mutation_scale=13, color=color, lw=lw,
        linestyle=ls, shrinkA=2, shrinkB=2, zorder=4,
        connectionstyle=f"arc3,rad={rad}"))


def _panel(ax, title, sub):
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.0, 1.005, title, fontsize=12.5, fontweight="bold", color=INK,
            va="bottom")
    ax.text(1.0, 1.010, sub, fontsize=9.5, color=GREY, va="bottom", ha="right")


# --------------------------------------------------------------- slide 23
def _s16_row(ax, title, sub, ha_lines, pick, ret, note):
    """One request/grant/data row: cores, the HA's state, and the choice."""
    _panel(ax, title, sub)
    _box(ax, 0.01, 0.56, 0.155, 0.36, "10 个 AI core\n发 REQ", fc=PANEL, fs=10)
    _box(ax, 0.235, 0.44, 0.365, 0.50, ha_lines, fc="white", ec=RED, lw=1.8,
         fs=9.6)
    _box(ax, 0.645, 0.54, 0.195, 0.40, pick, fc="#fdeaec", ec=RED, tc=RED,
         fs=9.6, bold=True)
    _box(ax, 0.870, 0.50, 0.115, 0.44, "环\n(ring)", fc=PANEL, fs=9.5)
    _arrow(ax, (0.165, 0.72), (0.235, 0.72))
    _arrow(ax, (0.600, 0.72), (0.645, 0.72), color=RED)
    _arrow(ax, (0.840, 0.72), (0.870, 0.72), color=RED)
    _arrow(ax, (0.905, 0.50), (0.088, 0.50), color=RED, rad=-0.13, lw=1.3)
    ax.text(0.46, 0.335, ret, fontsize=9.2, color=RED, ha="center")
    _box(ax, 0.01, 0.01, 0.99, 0.27, note, fc=PANEL, ec=PANEL, fs=9.6)


def fig_s16_diagram() -> None:
    """Where S16 sits on the write path and on the read path."""
    oc = 16
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85))
    fig.subplots_adjust(left=0.015, right=0.985, top=0.925, bottom=0.02,
                        hspace=0.20)

    _s16_row(
        ax, "写（WriteNoSnp）：把协议本来就要发的 DBIDResp 当授权用",
        "无新报文 · 无总线",
        "memory HA（completer）\n\n"
        "① 到达的 REQ 按源 core 排队\n"
        "② 每核累计已服务量计数器\n"
        f"③ 同时在飞授权 ≤ overcommit = {oc}",
        "选「迄今服务最少」\n的那个核\n→ 发 DBIDResp",
        "DBIDResp 回到被选中的 core，它才能发 WriteData",
        "协议本来就规定：拿到 DBIDResp 的核才能发 WriteData。"
        "所以「谁先拿到授权」= 「谁先占住环上的 DAT 槽」——\n"
        "公平性的决策点本来就在 HA 手里，只是从来没被用过。")

    _s16_row(
        bx, "读（ReadNoSnp）：同一套机制可以照搬 —— 但实测不需要做",
        "机制可迁移 · 但没有待解决的问题",
        "memory HA（数据源）\n\n"
        "① 到达的 REQ 按目的 core 排队\n"
        "② 每核累计已收 CompData 计数器\n"
        f"③ 同时在飞的 CompData burst ≤ {oc}",
        "选「迄今收得最少」\n的那个核\n→ 发 CompData×2",
        "最后一个 CompData 落地时释放一个名额",
        "读的大流量是 HA 自己发出去的，注入点分散在 8 个 HA 上，"
        "十个核不争同一个入环口 ——\n"
        "所以 S0 什么都不做，十个核的读带宽也只差 0.36%。"
        "S16 读侧只多 0.47% 带宽，不建议为此动事务层。")

    save(fig, "22-s16-diagram.png")


# --------------------------------------------------------------- slide 24
def _bars_vs(ax, names, vals, colors, ylabel, title, fmt="{:.4f}",
             ref=None, ref_label=None, pad_lo=0.06, pad_hi=0.10):
    xs = list(range(len(names)))
    ax.bar(xs, vals, width=0.58, color=colors, alpha=0.93)
    for x, v in zip(xs, vals):
        ax.text(x, v, fmt.format(v), ha="center", va="bottom", fontsize=10,
                fontweight="bold", color=INK)
    if ref is not None:
        ax.axhline(ref, color="#b34700", ls="--", lw=1.4, label=ref_label)
        ax.legend(fontsize=8.8, loc="lower left", framealpha=0.95)
    lo, hi = min(vals), max(vals if ref is None else vals + [ref])
    ax.set_ylim(max(0, lo - (hi - lo) * pad_lo - 0.02),
                hi + (hi - lo) * pad_hi + 0.02)
    ax.set_xticks(xs)
    ax.set_xticklabels(names, fontsize=10)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=11.5, fontweight="bold")
    ax.grid(axis="y", alpha=0.25)


def fig_s16_compare() -> None:
    """S16 against S0 and S1, on writes and on reads, both axes."""
    d = deck()
    w, r = d["write"], d["read"]
    bw = d["meta"]["bin_w"]
    r_fair = d["ideal"]["r_fair"]
    r_read = d["ideal"]["read_r_fair"]
    cols = [BLUE, AMBER, RED]

    fig, axes = plt.subplots(1, 4, figsize=(14.6, 4.5))
    _bars_vs(axes[0], ["S0", "S1", "S16"],
             [w["S0"]["throughput"], w["S1"]["throughput"],
              w["S16"]["throughput"]], cols,
             "总写带宽 flit/cycle", f"写 · 带宽（K={d['meta']['k_write']}）",
             ref=r_fair, ref_label=f"R* = {r_fair:.4f}")
    _bars_vs(axes[1], ["S0", "S1", "S16"],
             [w["S0"]["jain_bin"]["jain_bin_mean"],
              w["S1"]["jain_bin"]["jain_bin_mean"],
              w["S16"]["jain_bin"]["jain_bin_mean"]], cols,
             f"每 {bw} 拍算一次 Jain，再取平均", "写 · 瞬时均衡度", fmt="{:.4f}")
    _bars_vs(axes[2], ["S0", "S1-R", "S16-R"],
             [r["S0"]["throughput"], r["S1-R"]["throughput"],
              r["S16-R"]["throughput"]], cols,
             "总读带宽 flit/cycle", f"读 · 带宽（K={d['meta']['k_read']}）",
             ref=r_read, ref_label=f"R* = {r_read:.4f}")
    _bars_vs(axes[3], ["S0", "S1-R", "S16-R"],
             [r["S0"]["jain_bin"]["jain_bin_mean"],
              r["S1-R"]["jain_bin"]["jain_bin_mean"],
              r["S16-R"]["jain_bin"]["jain_bin_mean"]], cols,
             f"每 {bw} 拍算一次 Jain，再取平均", "读 · 瞬时均衡度", fmt="{:.4f}")
    fig.suptitle("写侧 S16 两条都赢；读侧 S0 本来就齐，S16 只多 0.47% 带宽 —— "
                 "读不建议做", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "23-s16-compare.png")


# --------------------------------------------------------------- slide 25
def fig_s22_diagram() -> None:
    """S22: broadcast progress on S1's own bus, then yield rather than gate."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85),
                                 gridspec_kw={"height_ratios": [1.0, 1.06]})
    fig.subplots_adjust(left=0.015, right=0.985, top=0.925, bottom=0.02,
                        hspace=0.22)

    _panel(ax, "信号：复用 S1 那条 6 bit 总线，但播的是「进度」",
           "位宽不变 · 仍按 30 拍计")
    for i, lab in enumerate(("C0", "C2", "…", "C18")):
        _box(ax, 0.02 + i * 0.135, 0.68, 0.11, 0.24, lab, fc=PANEL, fs=10)
        _arrow(ax, (0.075 + i * 0.135, 0.68), (0.075 + i * 0.135, 0.545),
               lw=1.1)
    ax.text(0.56, 0.74, "每个节点播出「我这窗成功上环了多少 flit」，饱和到 6 bit",
            fontsize=9.2, color=GREY)
    _box(ax, 0.02, 0.40, 0.70, 0.145,
         "6 bit 广播总线（30 拍延迟，不占 NoC hop）", fc="#fdeaec", ec=RED,
         tc=RED, fs=10, bold=True)
    _box(ax, 0.755, 0.36, 0.235, 0.28,
         "每个节点收到 10 项进度\n赤字 = 均值 − 自己 → 越线就举手",
         fc="white", ec=GREY, fs=9.4)
    _arrow(ax, (0.72, 0.47), (0.755, 0.47), color=RED)
    _box(ax, 0.02, 0.02, 0.97, 0.30,
         "和 S1 的差别只有一处，但是决定性的：S1 播的是「我这里有多堵」，"
         "落后的核会因为自己上环失败多而自己降速；\n"
         "S22 播的是「我做了多少」，于是落后与领先可以直接比较，"
         "让路的方向永远从领先者流向落后者。",
         fc=PANEL, ec=PANEL, fs=9.6)

    _panel(bx, "执行：让位，不是门控", "margin = 3.0 拒掉「差不多齐」的让路")
    ring_y = 0.80
    bx.plot([0.04, 0.96], [ring_y, ring_y], color=GREY, lw=2.6, zorder=1)
    for x, lab, col in ((0.20, "领先节点", INK), (0.55, "中间 hop", GREY),
                        (0.86, "落后节点", RED)):
        bx.scatter([x], [ring_y], s=200, c="white", edgecolors=col, zorder=3,
                   linewidths=1.8)
        bx.text(x, ring_y + 0.07, lab, ha="center", fontsize=10, color=col,
                fontweight="bold")
    _arrow(bx, (0.84, 0.715), (0.24, 0.715), color=RED, ls="--", rad=-0.055)
    bx.text(0.54, 0.583, "① 举手：只对「会骑过我出向 hop」的上游喊",
            ha="center", fontsize=9.4, color=RED)
    _box(bx, 0.03, 0.33, 0.46, 0.21,
         "② 领先节点让出一个 slot\n（不是关掉整个方向）", fc="white",
         ec=RED, fs=9.6)
    _box(bx, 0.51, 0.33, 0.46, 0.21,
         "③ 同拍前瞻改发一个会更早\n下环的 flit → 自己不空转",
         fc="white", ec=RED, fs=9.6)
    _box(bx, 0.03, 0.02, 0.94, 0.24,
         "门控（S1 的令牌桶）在无缓存环上会白扔槽位：让出的空隙沿途"
         "任何节点都能吃掉。\n让位是指名的，所以同等公平度下带宽代价小一个量级。",
         fc=PANEL, ec=PANEL, fs=9.6)

    save(fig, "24-s22-diagram.png")


# --------------------------------------------------------------- slide 26
def fig_s22_compare() -> None:
    """S22 against S0 / S1 / S16 on uniform writes."""
    d = deck()
    w = d["write"]
    bw = d["meta"]["bin_w"]
    r_fair = d["ideal"]["r_fair"]
    names = ["S0", "S1", "S16", "S22"]
    cols = [BLUE, AMBER, GREY, RED]

    fig, axes = plt.subplots(1, 3, figsize=(14.2, 4.5))
    _bars_vs(axes[0], names, [w[n]["throughput"] for n in names], cols,
             "总写带宽 flit/cycle", f"带宽（uniform 写，K={d['meta']['k_write']}）",
             ref=r_fair, ref_label=f"R* = {r_fair:.4f}")
    _bars_vs(axes[1], names,
             [w[n]["jain_bin"]["jain_bin_mean"] for n in names], cols,
             f"每 {bw} 拍算一次 Jain，再取平均",
             "瞬时均衡度：任意 100 拍内十个核齐不齐", fmt="{:.4f}")
    _bars_vs(axes[2], names, [w[n]["max_min"] for n in names], cols,
             "整窗 最快核带宽 / 最慢核带宽",
             "长期速率差：有没有核被长期拖慢", fmt="{:.4f}")
    fig.suptitle("S22 确实改善了均衡度，但要付带宽，而且比 S16 贵 15 倍",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "25-s22-compare.png")


def fig_itag_diagram() -> None:
    """I-tag reserve mode: detect local starvation, then deliver one slot."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85),
                                 gridspec_kw={"height_ratios": [1.0, 1.05]})
    fig.subplots_adjust(left=0.015, right=0.985, top=0.925, bottom=0.02,
                        hspace=0.23)

    _panel(ax, "检测：本节点连续 2 拍有 flit、却没能上环", "t_inj = 2 · 本地触发")
    _box(ax, 0.02, 0.55, 0.18, 0.35, "请求者 r\n本地注入队列非空",
         fc="white", ec=RED, fs=10, bold=True)
    _box(ax, 0.27, 0.55, 0.20, 0.35, "同方向 / 同 VC\n出向 hop 连续被占",
         fc=PANEL, fs=10)
    _box(ax, 0.55, 0.55, 0.18, 0.35, "饥饿计数\n达到 2 拍",
         fc="#fdeaec", ec=RED, tc=RED, fs=10, bold=True)
    _box(ax, 0.80, 0.55, 0.18, 0.35, "举 I-tag\n标明请求者 r",
         fc="white", ec=RED, tc=RED, fs=10, bold=True)
    for p, q in [((0.20, 0.72), (0.27, 0.72)),
                 ((0.47, 0.72), (0.55, 0.72)),
                 ((0.73, 0.72), (0.80, 0.72))]:
        _arrow(ax, p, q, color=RED)
    _box(ax, 0.02, 0.06, 0.96, 0.31,
         "I-tag 不是看队列长度，也不预测全局拥塞。它只回答一个本地事实："
         "「我的 flit 已经连续两拍抢不到自己的出向 hop」。\n"
         "标记按 plane / 方向 / VC 隔离，不影响不会经过该瓶颈的流。",
         fc=PANEL, ec=PANEL, fs=9.7)

    _panel(bx, "执行：最近的上游 donor 只让出一个 slot", "reserve 模式 · hold = 2")
    ring_y = 0.78
    bx.plot([0.04, 0.96], [ring_y, ring_y], color=GREY, lw=2.6, zorder=1)
    for x, lab, col in ((0.17, "上游 donor", RED), (0.49, "气泡沿环前进", GREY),
                        (0.84, "请求者 r", RED)):
        bx.scatter([x], [ring_y], s=190, c="white", edgecolors=col,
                   linewidths=1.8, zorder=3)
        bx.text(x, ring_y + 0.075, lab, ha="center", fontsize=10,
                color=col, fontweight="bold")
    _arrow(bx, (0.21, 0.70), (0.80, 0.70), color=RED, ls="--", lw=1.6)
    _box(bx, 0.04, 0.38, 0.27, 0.20,
         "① 找最近上游节点\n其待发 flit 会跨过 r 的 hop", fc="white",
         ec=RED, fs=9.4)
    _box(bx, 0.365, 0.38, 0.27, 0.20,
         "② donor 暂停这一拍\n制造一个可追踪的空 slot", fc="white",
         ec=RED, fs=9.4)
    _box(bx, 0.69, 0.38, 0.27, 0.20,
         "③ 空 slot 到达 r\nr 用它把自己的 flit 上环", fc="white",
         ec=RED, fs=9.4)
    _box(bx, 0.04, 0.04, 0.92, 0.24,
         "hold = 2 把 I-tag 变成 2 拍占空比：标记两拍后自动失效，"
         "避免上游长期停发。一次标记只预约一个 slot，"
         "不会像整段广播门控那样持续制造气泡。",
         fc=PANEL, ec=PANEL, fs=9.6)
    save(fig, "26-itag-diagram.png")


def fig_itag_compare() -> None:
    """I-tag against the requested S0 and S1 references."""
    d = deck()
    w, bw = d["write"], d["meta"]["bin_w"]
    names = ["S0", "S1", "I-tag"]
    keys = ["S0", "S1", "ITAG"]
    cols = [BLUE, AMBER, RED]
    fig, axes = plt.subplots(1, 3, figsize=(14.2, 4.5))
    _bars_vs(axes[0], names, [w[n]["throughput"] for n in keys], cols,
             "总写带宽 flit/cycle",
             f"带宽（uniform 写，K={d['meta']['k_write']}）",
             ref=d["ideal"]["r_fair"],
             ref_label=f"R* = {d['ideal']['r_fair']:.4f}")
    _bars_vs(axes[1], names,
             [w[n]["jain_bin"]["jain_bin_mean"] for n in keys], cols,
             f"每 {bw} 拍算一次 Jain，再取平均", "瞬时均衡度", fmt="{:.4f}")
    _bars_vs(axes[2], names, [w[n]["max_min"] for n in keys], cols,
             "整窗 最快核带宽 / 最慢核带宽",
             "长期速率差（越接近 1 越好）", fmt="{:.4f}")
    fig.suptitle("I-tag t_inj=2 / hold=2：用本地让位改善公平性，不依赖流量先验",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "27-itag-compare.png")


def fig_window_diagram() -> None:
    """Shared S19/S20 window actuator with their two feedback signals."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85))
    fig.subplots_adjust(left=0.015, right=0.985, top=0.925, bottom=0.02,
                        hspace=0.22)

    _panel(ax, "共同执行器：每个 core 一扇动态 outstanding 窗口",
           "初值 16 · 下限 8 · 硬上限 32")
    _box(ax, 0.02, 0.54, 0.18, 0.37, "core 发起端\n待发 WriteNoSnp",
         fc=PANEL, fs=10)
    _box(ax, 0.27, 0.50, 0.25, 0.45,
         "每核动态窗口 Wc\n\n在飞事务数 < Wc\n才允许发新的 REQ",
         fc="white", ec=RED, tc=RED, fs=10, bold=True)
    _box(ax, 0.60, 0.54, 0.16, 0.37, "环 + HA\n事务完成后\n归还一个名额",
         fc=PANEL, fs=9.7)
    _box(ax, 0.83, 0.54, 0.15, 0.37, "反馈样本\n回到同一个 core",
         fc="white", ec=RED, fs=9.7)
    _arrow(ax, (0.20, 0.72), (0.27, 0.72), color=RED)
    _arrow(ax, (0.52, 0.72), (0.60, 0.72), color=RED)
    _arrow(ax, (0.76, 0.72), (0.83, 0.72), color=RED)
    _arrow(ax, (0.90, 0.53), (0.40, 0.44), color=RED, rad=-0.17)
    _box(ax, 0.02, 0.05, 0.96, 0.29,
         "窗口只限制新 REQ；Retry 重发不被拦。Wc 不能超过静态 core_outstanding=32。"
         "与速率门控不同，窗口有名额时可以突发，名额耗尽才停。",
         fc=PANEL, ec=PANEL, fs=9.7)

    _panel(bx, "同一个窗口，两种反馈", "S19 看 RTT · S20 看 HA tracker ECN")
    _box(bx, 0.02, 0.48, 0.19, 0.42, "S19 · Swift\n时延反馈",
         fc="#fdeaec", ec=RED, tc=RED, fs=10.5, bold=True)
    _box(bx, 0.25, 0.48, 0.30, 0.42,
         "REQ 上环 → DBIDResp 回到 core\n直接得到 RTT（含 Retry 往返）\n\n"
         "RTT ≤ target：W += 1/W\nRTT > target：按超额比例缩窗",
         fc="white", ec=RED, fs=9.2)
    _box(bx, 0.60, 0.48, 0.16, 0.42, "S20 · DCTCP\nECN 反馈",
         fc="#fdeaec", ec=RED, tc=RED, fs=10.5, bold=True)
    _box(bx, 0.80, 0.48, 0.18, 0.42,
         "HA tracker 高占用\n→ DBIDResp 带 1 bit mark\nRetryAck 视为 mark\n\n"
         "有标记：W × (1−α/2)\n无标记：W += 1/W",
         fc="white", ec=RED, fs=8.9)
    _box(bx, 0.02, 0.05, 0.96, 0.28,
         "两者都不新增报文：S19 复用协议天然 RTT；S20 在既有 DBIDResp 上带 1 bit。"
         "区别只在拥塞信号，执行器和硬件成本相同（约 5,840 FF-eq）。",
         fc=PANEL, ec=PANEL, fs=9.7)
    save(fig, "28-window-diagram.png")


def fig_window_compare() -> None:
    """S19 and S20 against the requested S0 and S1 references."""
    d = deck()
    w, bw = d["write"], d["meta"]["bin_w"]
    names = ["S0", "S1", "S19", "S20"]
    cols = [BLUE, AMBER, GREY, RED]
    fig, axes = plt.subplots(1, 3, figsize=(14.2, 4.5))
    _bars_vs(axes[0], names, [w[n]["throughput"] for n in names], cols,
             "总写带宽 flit/cycle",
             f"带宽（uniform 写，K={d['meta']['k_write']}）",
             ref=d["ideal"]["r_fair"],
             ref_label=f"R* = {d['ideal']['r_fair']:.4f}")
    _bars_vs(axes[1], names,
             [w[n]["jain_bin"]["jain_bin_mean"] for n in names], cols,
             f"每 {bw} 拍算一次 Jain，再取平均", "瞬时均衡度", fmt="{:.4f}")
    _bars_vs(axes[2], names, [w[n]["max_min"] for n in names], cols,
             "整窗 最快核带宽 / 最慢核带宽",
             "长期速率差（越接近 1 越好）", fmt="{:.4f}")
    fig.suptitle("S19 / S20：不同信号驱动同一动态窗口；当前工作点结果接近",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "29-window-compare.png")


def main() -> None:
    _use_cjk_font()
    OUT.mkdir(parents=True, exist_ok=True)
    fig_saturation()
    fig_instbal()
    fig_s1_effect()
    fig_tradeoff()
    fig_pareto()
    fig_hot()
    fig_s16_diagram()
    fig_s16_compare()
    fig_itag_diagram()
    fig_itag_compare()
    fig_window_diagram()
    fig_window_compare()
    fig_s22_diagram()
    fig_s22_compare()


if __name__ == "__main__":
    main()
