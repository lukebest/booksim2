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
import math
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


def cov(v) -> float:
    """Coefficient of variation (population std / mean) of per-core shares."""
    n = len(v)
    m = sum(v) / n
    if m <= 0:
        return 0.0
    return math.sqrt(sum((x - m) ** 2 for x in v) / n) / m


def j2cov(j: float) -> float:
    """Jain -> CoV. Exact for one set of shares (J = 1/(1+CoV^2)); for a run
    the stored value is the window-mean Jain, so this is the run's CoV under the
    aggregation 1/(1+CoV^2) = mean over windows, the same convention the LP
    frontier and metric_ring2_cc use."""
    j = min(max(j, 1e-9), 1.0)
    return math.sqrt((1.0 - j) / j)


def cov_bin(row: dict) -> float:
    """Run-level CoV of a deck row, from its stored window-mean Jain."""
    return j2cov(row["jain_bin"]["jain_bin_mean"])


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
    per_bin = [cov([cnt[c][b] for c in cs]) for b in range(nb)]
    run_cov = j2cov(jb["jain_bin_mean"])
    reg_cov = j2cov(reg["jain_regular"])

    fig, (ax, bx) = plt.subplots(
        1, 2, figsize=(13.6, 4.15), gridspec_kw={"width_ratios": [1.5, 1.0]})

    xs = s0["bin_t"]
    ax.plot(xs, per_bin, lw=0.5, color=GREY, alpha=0.9,
            label=f"每 {bw} 拍窗的 CoV")
    win = 40
    sm = [sum(per_bin[max(0, i - win):i + win + 1])
          / len(per_bin[max(0, i - win):i + win + 1]) for i in range(nb)]
    ax.plot(xs, sm, lw=2.2, color=BLUE, label="滑动平均")
    ax.axhline(run_cov, color=RED, ls="--", lw=1.6,
               label=f"全程 CoV = {run_cov:.4f}")
    ax.axhline(reg_cov, color=GREEN, ls=":", lw=1.8,
               label=f"抖动抹平后的下限 = {reg_cov:.4f}")
    ax.set_xlabel("cycle")
    ax.set_ylabel(f"{bw} 拍窗内 10 核带宽的 CoV（标准差 / 均值）")
    ax.set_ylim(0, max(per_bin) + 0.03)
    ax.set_title("不是个别坏箱：整段都高，而且高得很稳",
                 fontsize=12, fontweight="bold")
    ax.grid(alpha=0.22)
    ax.legend(fontsize=9, loc="upper right", ncol=2)

    groups = [1, 2, 4, 8, 16, 32, 64]
    obs, idl, wid = [], [], []
    for g in groups:
        m = nb // g
        if m < 8:
            break
        o, i2 = [], []
        for b in range(m):
            v = [sum(cnt[c][b * g:(b + 1) * g]) for c in cs]
            o.append(cov(v))
            i2.append(j2cov(jain_ideal(sum(v), n)))
        obs.append(sum(o) / m)
        idl.append(sum(i2) / m)
        wid.append(g * bw)
    bx.plot(wid, obs, "o-", color=RED, lw=2.0, ms=5, label="S0 实测")
    bx.plot(wid, idl, "s--", color=GREY, lw=1.4, ms=4, label="理想控制器")
    bx.axhline(reg_cov, color=GREEN, ls=":", lw=1.6,
               label="只整时机的地板")
    bx.set_xscale("log", base=2)
    bx.set_xlabel("观察窗宽度（拍，对数轴）")
    bx.set_ylabel("窗内 CoV 均值")
    bx.set_ylim(0, max(obs) + 0.03)
    bx.set_title("窗放得再宽也降不到理想：抖动能消，速率差消不掉",
                 fontsize=12, fontweight="bold")
    bx.grid(alpha=0.22, which="both")
    bx.legend(fontsize=9, loc="upper right")

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
    a1.set_title("S0 / S1 / S1T 各核写带宽",
                 fontsize=11.5, fontweight="bold")
    a1.grid(axis="y", alpha=0.25)
    a1.legend(fontsize=9.5, loc="upper center", ncol=3)

    off = {"S0": (9, -16), "S1": (10, -4), "S1T": (9, 9)}
    for lbl, col in (("S0", BLUE), ("S1", RED), ("S1T", AMBER)):
        r = w[lbl]
        x, y = r["throughput"], cov_bin(r)
        a2.scatter([x], [y], s=180, c=col, edgecolors="k", linewidths=0.6,
                   zorder=4)
        a2.annotate(lbl, xy=(x, y), xytext=off[lbl],
                    textcoords="offset points", fontsize=12,
                    fontweight="bold", color=col)
    s0x = w["S0"]["throughput"]
    s0y = cov_bin(w["S0"])
    a2.axvline(s0x, c=GREY, ls="-.", lw=1.0)
    a2.annotate("", xy=(w["S1"]["throughput"], cov_bin(w["S1"])),
                xytext=(s0x, s0y),
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.8))
    dpct = 100 * (w["S1"]["throughput"] / s0x - 1)
    dj = cov_bin(w["S1"]) - s0y
    a2.text((s0x + w["S1"]["throughput"]) / 2, s0y - 0.008,
            f"带宽 {dpct:+.1f}%\n换 CoV {dj:+.3f}", fontsize=11,
            color=RED, ha="center", va="top")
    a2.set_xlim(min(w[n]["throughput"] for n in ("S0", "S1", "S1T")) - 0.25,
                max(w[n]["throughput"] for n in ("S0", "S1", "S1T")) + 0.25)
    a2.set_ylim(0.17, 0.32)
    a2.set_xlabel("总写带宽 flit/cycle")
    a2.set_ylabel(f"{bw} 拍窗不均衡度 CoV（越低越均衡）")
    a2.set_title("三个工作点在带宽—CoV 平面上的位置",
                 fontsize=11.5, fontweight="bold")
    a2.grid(alpha=0.25)

    fig.tight_layout()
    save(fig, "12-s1-effect.png")


# --------------------------------------------------------------- slide 17
def fig_tradeoff() -> None:
    """The ideal R(J) upper bound with every official deck scheme overlaid."""
    d = json.loads((RES / "tradeoff_ring2_cc.json").read_text())
    dk = deck()
    # x = CoV of the ideal scheduler's 100-cycle shares, the same statistic
    # the measured markers use, so curve and points share one definition.
    pts = sorted((j2cov(p["jain_bin"]), p["bw_monotone"]) for p in d["jain_curve"])
    r_max, r_fair = d["r_max"], d["r_fair"]
    # past the CoV where the LP hits R_max the fairness constraint is slack and
    # the bound is flat at R_max
    pts.append((0.385, r_max))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w = dk["write"]

    labels = {
        "S0": "S0 基线", "S1": "S1 AIMD", "S1T": "S1T 分向",
        "S16": "S16 授权保留", "ITAG": "S0 I-tag 调参",
        "S19": "S19 Swift", "S20": "S20 DCTCP",
        "S22": "S22 赤字让路", "S26": "S26 自适应路由",
        "S27": "S27 逐跳背压", "S28": "S28 显式速率",
        "S28S": "S28S 等分速率", "S29": "S29 日历让路",
    }
    new = {"S26", "S27", "S28", "S28S", "S29"}
    rows = []
    for key, lab in labels.items():
        r = w[key]
        rows.append({"key": key, "name": lab, "cov": cov_bin(r),
                     "bw": r["throughput"]})
    rows.sort(key=lambda r: -r["bw"])

    fig = plt.figure(figsize=(12.4, 6.15))
    ax = fig.add_axes([0.072, 0.11, 0.50, 0.78])
    key = fig.add_axes([0.595, 0.02, 0.395, 0.94])
    key.axis("off")

    lo = min(r["bw"] for r in rows) - 0.18
    ax.plot(xs, ys, "-", c=RED, lw=2.6, zorder=3,
            label="理论上限 R(CoV)")
    ax.fill_between(xs, lo, ys, color=RED, alpha=0.045, zorder=0)
    ax.axhline(r_max, c=GREY, ls="--", lw=1.0)
    ax.annotate(f"R_max = {r_max:.4f}（CoV ≥ 0.306 后不再受公平约束）",
                xy=(0.003, r_max), xytext=(0, -13), textcoords="offset points",
                fontsize=8.6, color="#5b636d")
    ax.scatter([0.0], [r_fair], s=70, facecolors="white", edgecolors=RED,
               linewidths=1.6, zorder=5)
    ax.annotate(f"R* = {r_fair:.4f}",
                xy=(0.0, r_fair), xytext=(8, 10), textcoords="offset points",
                fontsize=8.8, color=RED, ha="left")

    for i, r in enumerate(rows, 1):
        if r["key"] == "S16":
            c, s = RED, 160
        elif r["key"] == "S0":
            c, s = BLUE, 130
        elif r["key"] == "S1":
            c, s = AMBER, 130
        elif r["key"] in new:
            c, s = GREEN, 120
        else:
            c, s = "#5b636d", 90
        ax.scatter([r["cov"]], [r["bw"]], s=s, c=c, zorder=6,
                   edgecolors="k", linewidths=0.55)
        dx, dy = ((0, 9), (-10, 3), (10, 3), (0, -12))[i % 4]
        ax.annotate(str(i), xy=(r["cov"], r["bw"]), xytext=(dx, dy),
                    textcoords="offset points", fontsize=8.6, ha="center",
                    color=INK, fontweight="bold")

    ax.set_xlim(-0.012, 0.385)
    ax.set_ylim(lo, 6.62)
    ax.set_xlabel(f"不均衡度 CoV = 十核 {dk['meta']['bin_w']} 拍窗带宽的标准差 / 均值"
                  "（0 = 完全均等）→")
    ax.set_ylabel("总写带宽 R  flit/cycle")
    ax.set_title("红线是理论上限；全部官方 K=20000 方案",
                 fontsize=12, fontweight="bold")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8.5, loc="center right")

    cols = ((0.00, "#"), (0.055, "方案（按带宽降序）"), (0.62, "CoV"),
            (0.80, "带宽"), (1.00, ""))
    aligns = ("left", "left", "right", "right", "right")
    key.text(0.0, 0.985, "图例 · 绿 = 本次补齐", fontsize=12,
             fontweight="bold", color=INK, va="top")
    step, pt = _key_metrics(len(rows))
    for (x, t), al in zip(cols, aligns):
        key.text(x, 0.935, t, fontsize=pt, color="#5b636d", va="top", ha=al,
                 fontweight="bold")
    front = {"S16", "S0"}
    for i, r in enumerate(rows, 1):
        col = RED if r["key"] in front else (GREEN if r["key"] in new else INK)
        y = 0.935 - i * step
        vals = (str(i), r["name"], f"{r['cov']:.4f}", f"{r['bw']:.3f}", "")
        for (x, _), al, v in zip(cols, aligns, vals):
            key.text(x, y, v, fontsize=pt, color=col, va="top", ha=al)
    key.text(0.0, 0.935 - (len(rows) + 1.25) * step,
             "点到红线的竖直距离 = 同等不均衡度下损失的带宽。\n"
             "红线在 CoV 坐标下是直线 R* + κ·CoV（κ = 2.18）。\n"
             "绿点 = 本次补齐的四类；I-tag 只是 S0 调参。",
             fontsize=8.6, color="#5b636d", va="top")

    save(fig, "16-tradeoff.png")


# --------------------------------------------------------------- slide 20
def _key_metrics(n: int) -> tuple[float, float]:
    """Row pitch and font for the key column, so the roster can grow.

    The column has ~0.90 of the axes height below its header. Fourteen schemes
    fitted at a fixed pitch; twenty do not, and a key that runs off the bottom
    silently drops exactly the rows a reader came to check.
    """
    step = min(0.0575, 0.90 / max(1, n))
    return step, min(9.6, 9.6 * step / 0.0575 + 1.6)


def fig_pareto() -> None:
    """Numbered markers plus a key column: readable at projector distance."""
    from pareto_ring2_cc import frontier

    reg = json.loads((RES / "pareto_ring2_cc.json").read_text())
    ideal = reg["ideal"]
    kappa = _metric()["kappa"]
    # Score every screened point with phi = (R - kappa*CoV)/R*; frontier()
    # ranks on the "eta" key, so overwrite it with phi.
    for r in reg["schemes"]:
        r["cov"] = j2cov(r["jain_bin"])
        r["phi"] = r["bw_vs_ideal"] - kappa * r["cov"] / ideal["bw"]
        r["eta"] = r["phi"]
    rows = sorted(reg["schemes"], key=lambda r: -r["phi"])
    front = {r["name"] for r in frontier(reg["schemes"])}

    fig = plt.figure(figsize=(9.9, 6.3))
    ax = fig.add_axes([0.085, 0.105, 0.455, 0.760])
    key = fig.add_axes([0.585, 0.02, 0.405, 0.94])
    key.axis("off")

    for i, r in enumerate(rows, 1):
        x, y = max(r["hw_cost"], 1), r["phi"]
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

    fpts = [(max(r["hw_cost"], 1), r["phi"]) for r in frontier(reg["schemes"])]
    ax.plot([p[0] for p in fpts], [p[1] for p in fpts], "--", c=RED, lw=1.4,
            alpha=0.8, label="Pareto 前沿")
    ax.axhline(1.0, c="#b34700", lw=1.6,
               label=f"理想控制器 φ = 1.0（R* = {ideal['bw']:.4f} flit/cycle，CoV = 0）")
    s0eta = next(r["phi"] for r in rows if r["name"].startswith("S0"))
    ax.axhline(s0eta, c=GREY, ls="-.", lw=1.1,
               label=f"S0 基线 φ = {s0eta:.3f}")

    ax.set_xscale("log")
    ax.set_xlim(0.6, 4e6)
    # Floor well below the worst scheme so the legend has a band of its own:
    # at 0.35 the legend box sat on top of S17, which is the study's worst
    # point and therefore one a reader specifically looks for.
    ax.set_ylim(0.14, 1.06)
    ax.set_xlabel("新增硬件状态（FF 等效 = 折算成触发器个数，对数轴）→ 越贵")
    ax.set_ylabel(f"φ = (R − κ·CoV) / R*，κ = {kappa:.2f}")
    ax.set_title("收益 — 硬件开销 Pareto（写，uniform，K=2000）\n"
                 "φ 越高越接近理想控制器；红 = 前沿，即同价位无人能超",
                 fontsize=12, fontweight="bold")
    ax.grid(alpha=0.25, which="both")
    # Lower-left, not lower-right: the cheap end of the axis is empty at low
    # eta, whereas the mid-cost column now runs all the way down to S17.
    ax.legend(fontsize=8.5, loc="lower left")

    cols = ((0.00, "#"), (0.050, "方案（按 φ 降序）"), (0.545, "φ"),
            (0.675, "CoV"), (0.815, "带宽/R*"), (1.00, "FF 等效"))
    aligns = ("left", "left", "right", "right", "right", "right")
    key.text(0.0, 0.985, "图例", fontsize=12, fontweight="bold", color=INK,
             va="top")
    step, pt = _key_metrics(len(rows))
    for (x, t), al in zip(cols, aligns):
        key.text(x, 0.935, t, fontsize=pt, color="#5b636d", va="top", ha=al,
                 fontweight="bold")
    for i, r in enumerate(rows, 1):
        col = RED if r["name"] in front else INK
        nm = r["name"]
        nm = nm if len(nm) <= 22 else nm[:21] + "…"
        y = 0.935 - i * step
        vals = (str(i), nm, f"{r['phi']:.4f}", f"{r['cov']:.4f}",
                f"{r['bw_vs_ideal']:.3f}", f"{r['hw_cost']:,}")
        for (x, _), al, v in zip(cols, aligns, vals):
            key.text(x, y, v, fontsize=pt, color=col, va="top", ha=al)

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
    cap = str(deck()["meta"]["core_outstanding"])
    rows = d["passes"][cap] if "passes" in d else d["rows"]
    # t_inj=2 / hold=2 is the same I-tag mechanism already present in S0,
    # not an independently buildable controller, so it is a parameter point
    # rather than a scheme on the hardware Pareto chart.
    rows = [r for r in rows if not r["name"].startswith("I-tag")]
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
    # Same reason as the uniform chart: reserve a band under the worst scheme
    # so the legend cannot sit on top of a data point.
    lo = min(r["bw_vs_ideal"] for r in rows)
    ax.set_ylim(lo - 0.10, 1.015)
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
    step, pt = _key_metrics(len(rows))
    for (x, t), al in zip(cols, aligns):
        key.text(x, 0.935, t, fontsize=pt, color="#5b636d", va="top", ha=al,
                 fontweight="bold")
    for i, r in enumerate(rows, 1):
        col = RED if r["name"] in front else INK
        nm = r["name"]
        nm = nm if len(nm) <= 22 else nm[:21] + "…"
        y = 0.935 - i * step
        vals = (str(i), nm, f"{r['bw_vs_ideal']:.4f}",
                f"{r['delta_vs_s0_pct']:+.2f}%", f"{r['hw_cost']:,}")
        for (x, _), al, v in zip(cols, aligns, vals):
            key.text(x, y, v, fontsize=pt, color=col, va="top", ha=al)

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
    oc = int(deck()["meta"].get("s16_overcommit") or 20)
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


def _cov_bars(ax, names, rows, colors, title="瞬时不均衡度", bw=100,
              ideal=None):
    """CoV bars from deck rows; the axis starts at 0 (= perfectly equal)."""
    vals = [cov_bin(r) for r in rows]
    _bars_vs(ax, names, vals, colors,
             f"{bw} 拍窗内十核带宽的 CoV（0 = 完全均等）", title, fmt="{:.4f}",
             ref=ideal, ref_label=None if ideal is None else f"理想控制器 {ideal:.4f}")
    ax.set_ylim(0, max(vals) * 1.22)


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
    _cov_bars(axes[1], ["S0", "S1", "S16"], [w["S0"], w["S1"], w["S16"]], cols,
              "写 · 瞬时不均衡度", bw)
    _bars_vs(axes[2], ["S0", "S1-R", "S16-R"],
             [r["S0"]["throughput"], r["S1-R"]["throughput"],
              r["S16-R"]["throughput"]], cols,
             "总读带宽 flit/cycle", f"读 · 带宽（K={d['meta']['k_read']}）",
             ref=r_read, ref_label=f"R* = {r_read:.4f}")
    _cov_bars(axes[3], ["S0", "S1-R", "S16-R"], [r["S0"], r["S1-R"], r["S16-R"]],
              cols, "读 · 瞬时不均衡度", bw)
    fig.suptitle("写侧 S16：CoV 与总带宽；读侧 S0 本来就齐，S16 只多 0.47% 带宽",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "23-s16-compare.png")


# ------------------------------------------------- new section: CC taxonomy
# Where every congestion-control family acts, and what it listens to. The two
# axes are the only two things that distinguish the families from each other:
# a family is "the same scheme" as another exactly when both agree, which is
# why S17/S18 and S19/S20 sit in the same column as pairs.
TAX_POINTS = [
    # (control point, signal, label, state)
    #   state: "have" = a scheme in this study occupies the cell
    #          "s1"   = the cell S1 occupies
    #          "new"  = a cell that was empty and is now filled
    ("路径选择", "本地观测", "S26", "new"),
    ("逐跳背压", "链路占用", "S27", "new"),
    ("源端速率", "本地观测", "S21", "have"),
    ("源端速率", "时延", "S17", "have"),
    ("源端速率", "ECN 标记", "S18", "have"),
    ("源端速率", "显式等级", "S1 / S1T / S15", "s1"),
    ("源端速率", "显式速率", "S28 / S28S", "new"),
    ("源端窗口", "时延", "S19", "have"),
    ("源端窗口", "ECN 标记", "S20", "have"),
    ("环上仲裁", "本地观测", "I-tag / E-tag", "have"),
    ("环上仲裁", "显式等级", "S22", "have"),
    ("预约调度", "无信号 / 1 bit 需求", "S29", "new"),
    ("接收端授权", "本地观测", "S16", "have"),
]
TAX_X = ["路径选择", "逐跳背压", "源端速率", "源端窗口", "环上仲裁",
         "预约调度", "接收端授权"]
TAX_Y = ["无信号 / 1 bit 需求", "本地观测", "链路占用", "时延", "ECN 标记",
         "显式等级", "显式速率"]


def fig_cc_taxonomy() -> None:
    """The design space as a control-point x signal grid, with S1 located.

    The grid is the argument, not decoration. Reading down a column gives
    every scheme that acts at the same place and therefore has the same
    ceiling; reading across a row gives every scheme that listens to the same
    thing and therefore inherits the same blind spot. S1's cell is
    highlighted because the deck's finding about it -- that its signal cannot
    separate "I am too greedy" from "I am being squeezed" -- is a property of
    the *row*, shared by everything that triggers on the node's own failures.
    """
    fig, ax = plt.subplots(figsize=(12.6, 5.35))
    ax.set_xlim(-0.6, len(TAX_X) - 0.4)
    ax.set_ylim(-0.9, len(TAX_Y) - 0.3)

    for i in range(len(TAX_X)):
        ax.axvline(i, color="#e4e9f0", lw=1.0, zorder=0)
    for j in range(len(TAX_Y)):
        ax.axhline(j, color="#e4e9f0", lw=1.0, zorder=0)

    for cp, sig, lab, state in TAX_POINTS:
        x, y = TAX_X.index(cp), TAX_Y.index(sig)
        if state == "s1":
            fc, ec, tc, lw, fs = "#fdeaec", RED, RED, 2.4, 10.5
        elif state == "new":
            fc, ec, tc, lw, fs = "white", GREEN, GREEN, 2.0, 10.0
        else:
            fc, ec, tc, lw, fs = PANEL, GREY, INK, 1.2, 10.0
        w = 0.86 if len(lab) < 9 else 0.98
        ax.add_patch(FancyBboxPatch(
            (x - w / 2, y - 0.20), w, 0.40,
            boxstyle="round,pad=0.02,rounding_size=0.06",
            fc=fc, ec=ec, lw=lw, zorder=3))
        ax.text(x, y, lab, ha="center", va="center", fontsize=fs, color=tc,
                fontweight="bold", zorder=4)

    ax.set_xticks(range(len(TAX_X)))
    ax.set_xticklabels(TAX_X, fontsize=11, fontweight="bold")
    ax.set_yticks(range(len(TAX_Y)))
    ax.set_yticklabels(TAX_Y, fontsize=10.5)
    ax.set_xlabel("控制点：这一类在哪里动手（决定它的天花板）",
                  fontsize=11.5, labelpad=9)
    ax.set_ylabel("拥塞信号：它听什么（决定它的盲区）", fontsize=11.5,
                  labelpad=6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)

    ax.annotate("S1 在这里：源端限速 + 显式拥塞等级",
                xy=(2.5, 5.0), xytext=(3.15, 6.35), fontsize=11.5,
                color=RED, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.6))
    ax.text(-0.55, -0.60,
            "灰底 = 本研究已有方案　　绿框 = 本次补齐的四类（原为空列 / 空格）"
            "　　红框 = S1 所在的格",
            fontsize=10, color=GREY, va="center")
    ax.set_title("拥塞控制的两个维度：在哪里动手 × 听什么信号",
                 fontsize=13.5, fontweight="bold", pad=12)
    fig.tight_layout()
    save(fig, "16a-cc-taxonomy.png")


def fig_gap_diagram() -> None:
    """The four newly implemented families, one mechanism panel each."""
    fig, axes = plt.subplots(2, 2, figsize=(12.2, 6.0))
    fig.subplots_adjust(left=0.012, right=0.988, top=0.905, bottom=0.015,
                        hspace=0.30, wspace=0.05)
    (ax, bx), (cx, dx) = axes

    _panel(ax, "S26 自适应路由 · UGAL / Valiant 类", "控制点：路径选择")
    ax.plot([0.06, 0.94], [0.66, 0.66], color=GREY, lw=2.4, zorder=1)
    for x, lab in ((0.20, "core"), (0.52, "最短方向\n(已 97% 满)"),
                   (0.84, "目的 HA")):
        ax.scatter([x], [0.66], s=180, c="white", edgecolors=GREY, zorder=3,
                   linewidths=1.6)
        ax.text(x, 0.755, lab, ha="center", fontsize=9.2, color=GREY)
    _arrow(ax, (0.20, 0.60), (0.84, 0.60), color=GREEN, ls="--", rad=-0.45)
    ax.text(0.52, 0.365, "反向绕远：多走 (n − h) 跳", ha="center",
            fontsize=9.6, color=GREEN, fontweight="bold")
    _box(ax, 0.02, 0.02, 0.96, 0.30,
         "本地 EWMA：出向 hop 上环失败率。最短方向比反向差 0.05 以上，\n"
         "且绕远不超过 2 跳，就改走反向。无信号、无总线、无新报文。",
         fc=PANEL, ec=PANEL, fs=9.5)

    _panel(bx, "S27 逐跳背压 · Credit FC / PFC 类", "控制点：上游链路")
    bx.plot([0.06, 0.94], [0.66, 0.66], color=GREY, lw=2.4, zorder=1)
    for x in (0.22, 0.44, 0.66, 0.88):
        bx.scatter([x], [0.66], s=150, c="white", edgecolors=GREY, zorder=3,
                   linewidths=1.5)
    bx.text(0.88, 0.775, "占用率 ≥ 0.90\n→ 拉 XOFF", ha="center", fontsize=9.2,
            color=RED, fontweight="bold")
    for a, b in ((0.88, 0.66), (0.66, 0.44)):
        _arrow(bx, (a - 0.02, 0.575), (b + 0.02, 0.575), color=RED)
    bx.text(0.47, 0.44, "每跳 1 拍逐跳上传，reach = 2 跳", ha="center",
            fontsize=9.6, color=RED)
    _box(bx, 0.02, 0.02, 0.96, 0.30,
         "路径穿过被 XOFF 的 hop 就不许上环。不是广播总线：一根线只连相邻节点。\n"
         "无缓存环上「保护链路」= 让链路空转，让出的槽不会被记账保留。",
         fc=PANEL, ec=PANEL, fs=9.5)

    _panel(cx, "S28 显式速率反馈 · XCP / RCP 类", "控制点：瓶颈 hop 算速率")
    _box(cx, 0.02, 0.60, 0.30, 0.32,
         "每个 hop 自己算：\nN = 本窗跨过它的核数\ny = 本窗占用率",
         fc="white", ec=GREEN, fs=9.2)
    _box(cx, 0.36, 0.60, 0.30, 0.32,
         "RCP 更新\nshare ← share ×\n[1 + α(C−y)/(C·N)]",
         fc="#eaf6ec", ec=GREEN, tc=GREEN, fs=9.2, bold=True)
    _box(cx, 0.70, 0.60, 0.28, 0.32,
         "6 bit × 40 hop\n广播（30 拍）",
         fc="white", ec=GREEN, fs=9.2)
    _arrow(cx, (0.32, 0.76), (0.36, 0.76), color=GREEN)
    _arrow(cx, (0.66, 0.76), (0.70, 0.76), color=GREEN)
    cx.text(0.50, 0.475,
            "核取 min over 路径 hop 的 share / 自己在该 hop 的流量占比",
            ha="center", fontsize=9.5, color=GREEN, fontweight="bold")
    _box(cx, 0.02, 0.02, 0.96, 0.30,
         "S1 播拥塞等级，源端用 AIMD 调预算；S28 播 hop 算出的 share，"
         "对该 hop 上所有核都是同一个数。",
         fc=PANEL, ec=PANEL, fs=9.5)

    _panel(dx, "S29 预约 / 调度式 · Fastpass / TDMA 类", "控制点：预分配时隙")
    for i, lab in enumerate(("C0", "C2", "C4", "…", "C18")):
        x = 0.04 + i * 0.187
        fc, ec, tc = (("#eaf6ec", GREEN, GREEN) if i == 1 else
                      ("white", GREY, INK))
        _box(dx, x, 0.62, 0.165, 0.26, lab, fc=fc, ec=ec, tc=tc, fs=9.6,
             bold=(i == 1))
    dx.text(0.50, 0.545, "帧 = 2 拍 × 10 核 = 20 拍；100 拍窗内每核保底 5 次",
            ha="center", fontsize=9.5, color=GREEN, fontweight="bold")
    _box(dx, 0.02, 0.34, 0.96, 0.17,
         "轮到某核时，会骑过它出向 hop 的其他核让位（与 S22 同一个执行器）",
         fc="white", ec=GREEN, fs=9.4)
    _box(dx, 0.02, 0.02, 0.96, 0.28,
         "纯日历不需要任何拥塞信号。只加一条：每核 1 bit「我有 WriteData 排队」，\n"
         "空闲核的时隙不被浪费 —— 这就是 TDMA 与预约式的区别，10 bit / 16 拍。",
         fc=PANEL, ec=PANEL, fs=9.5)

    fig.suptitle("补齐的四类：各自的机制与它在环上的落点",
                 fontsize=13.5, fontweight="bold")
    save(fig, "16b-gap-diagram.png")


def fig_gap_compare() -> None:
    """The four new families against S0 and S1, on the deck's three axes."""
    d = deck()
    w, bw = d["write"], d["meta"]["bin_w"]
    keys = ["S0", "S1", "S26", "S27", "S28", "S28S", "S29"]
    names = keys
    cols = [BLUE, AMBER, GREY, GREY, GREY, "#8fb8a0", GREEN]
    fig, axes = plt.subplots(1, 3, figsize=(14.8, 4.9))
    _bars_vs(axes[0], names, [w[k]["throughput"] for k in keys], cols,
             "总写带宽 flit/cycle",
             f"① 总带宽（uniform 写，K={d['meta']['k_write']}，越高越好）",
             ref=d["ideal"]["r_fair"],
             ref_label=f"R* = {d['ideal']['r_fair']:.4f}", fmt="{:.3f}")
    _cov_bars(axes[1], names, [w[k] for k in keys], cols,
              "② 不均衡度 CoV（瞬时，越低越好）", bw)
    _bars_vs(axes[2], names, [w[k]["max_min"] for k in keys], cols,
             "整窗 最快核带宽 / 最慢核带宽",
             "③ 长期速率比（越接近 1 越好）", fmt="{:.3f}")
    # An S1 line on all three axes so the existing scheme is a visible
    # reference without subtracting labels.
    for ax, v in ((axes[0], w["S1"]["throughput"]), (axes[1], cov_bin(w["S1"])),
                  (axes[2], w["S1"]["max_min"])):
        ax.axhline(v, color=AMBER, ls=":", lw=1.5, zorder=0, label="S1 水平")
        ax.legend(fontsize=8.6, loc="upper left" if ax is axes[1] else "lower left",
                  framealpha=0.95)
    fig.suptitle("补齐的四类与 S0 / S1 同口径：总带宽、不均衡度、长期速率比",
                 fontsize=12.5, fontweight="bold")
    fig.text(0.5, 0.012,
             "S26 自适应路由 · S27 逐跳背压 · S28 显式速率（RCP 反馈）· "
             "S28S 显式速率（每 hop 静态等分）· S29 预约 / 调度式；"
             "S0 无控制、S1 源端速率 + 显式拥塞等级",
             ha="center", fontsize=9.6, color=GREY)
    fig.tight_layout(rect=(0, 0.035, 1, 0.905))
    save(fig, "16c-gap-compare.png")


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
         "S1 播的是本窗拥塞等级；S22 播的是本窗成功上环数。\n"
         "后者让落后与领先可以直接比较，让路方向从领先者流向落后者。",
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
         "S1 令牌桶：没额度本拍不上环。S22 让位是指名的，"
         "只让出具体某一拍上的具体某个位置。",
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
    _cov_bars(axes[1], names, [w[n] for n in names], cols,
              "瞬时不均衡度 CoV：任意 100 拍内十个核齐不齐", bw)
    _bars_vs(axes[2], names, [w[n]["max_min"] for n in names], cols,
             "整窗 最快核带宽 / 最慢核带宽",
             "长期速率差：有没有核被长期拖慢", fmt="{:.4f}")
    fig.suptitle("S22 与 S0 / S1 / S16 同口径：总带宽、瞬时 CoV、长期速率比",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "25-s22-compare.png")


def fig_s29_diagram() -> None:
    """S29: the same yield actuator as S22, driven by a calendar, not a measure."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85),
                                 gridspec_kw={"height_ratios": [1.0, 1.06]})
    fig.subplots_adjust(left=0.015, right=0.985, top=0.925, bottom=0.02,
                        hspace=0.22)

    _panel(ax, "触发：一张固定日历，外加每核 1 bit「我有活干」",
           "帧 = 2 拍 × 10 核 = 20 拍")
    slots = ("C0", "C2", "C4", "C6", "C8", "C10", "C12", "C14", "C16", "C18")
    for i, lab in enumerate(slots):
        x = 0.02 + i * 0.0965
        own = i == 2
        _box(ax, x, 0.66, 0.088, 0.24, lab,
             fc="#fdeaec" if own else PANEL, ec=RED if own else GREY,
             tc=RED if own else INK, fs=9, bold=own)
    ax.annotate("当前时隙：C4 有路权", xy=(0.245, 0.655), xytext=(0.30, 0.545),
                fontsize=9.4, color=RED, fontweight="bold",
                arrowprops=dict(arrowstyle="-|>", lw=1.2, color=RED))
    _box(ax, 0.02, 0.34, 0.60, 0.145,
         "日历是常量：无需测量、无需收敛、无控制环路", fc="white", ec=GREY,
         fs=9.6)
    _box(ax, 0.655, 0.30, 0.335, 0.22,
         "10 bit 需求字（每核 1 bit）\n空闲核的时隙立即让给别人",
         fc="#fdeaec", ec=RED, tc=RED, fs=9.4, bold=True)
    _box(ax, 0.02, 0.02, 0.97, 0.25,
         "纯 TDMA 的固有缺陷是「为空闲者保留的时隙被浪费」。"
         "这里用每核 1 bit、每 16 拍刷新一次的需求位把它补掉：\n"
         "轮到一个已经排空的核时，它的时隙不会空转。"
         "这 10 bit 是 S29 全部的信号开销 —— 没有拥塞等级，也没有速率。",
         fc=PANEL, ec=PANEL, fs=9.6)

    _panel(bx, "执行：与 S22 逐字相同的「限域让位」，只换了触发来源",
           "队列深度维持出厂 8 / 12")
    ring_y = 0.80
    bx.plot([0.04, 0.96], [ring_y, ring_y], color=GREY, lw=2.6, zorder=1)
    for x, lab, col in ((0.22, "骑过它的上游核", INK), (0.55, "出向 hop", GREY),
                        (0.86, "本时隙的持有者", RED)):
        bx.scatter([x], [ring_y], s=200, c="white", edgecolors=col, zorder=3,
                   linewidths=1.8)
        bx.text(x, ring_y + 0.07, lab, ha="center", fontsize=9.6, color=col,
                fontweight="bold")
    _arrow(bx, (0.84, 0.715), (0.26, 0.715), color=RED, ls="--", rad=-0.055)
    bx.text(0.55, 0.583, "① 到点即举手：不看拥塞，只看日历",
            ha="center", fontsize=9.4, color=RED)
    _box(bx, 0.03, 0.33, 0.46, 0.21,
         "② 上游让出一个 slot\n（与 S22 同一套仲裁改动）", fc="white", ec=RED,
         fs=9.6)
    _box(bx, 0.51, 0.33, 0.46, 0.21,
         "③ 同拍前瞻改发一个会更早\n下环的 flit → 自己不空转", fc="white",
         ec=RED, fs=9.6)
    _box(bx, 0.03, 0.02, 0.94, 0.24,
         "与 S22 的差别只有触发：S22 要播 10 项进度、算均值、比赤字"
         "（6 bit 总线 + 10 项表 + 10 输入加法树）；\n"
         "S29 的触发是一个常量计数器，于是表和加法树全部消失 —— "
         "同一个执行器，硬件从 13,920 降到 4,440 FF-eq。",
         fc=PANEL, ec=PANEL, fs=9.6)

    save(fig, "30-s29-diagram.png")


def fig_s29_compare() -> None:
    """S29 against S0 / S1, plus the two schemes it is competing with."""
    d = deck()
    w = d["write"]
    bw = d["meta"]["bin_w"]
    r_fair = d["ideal"]["r_fair"]
    names = ["S0", "S1", "S22", "S16", "S29"]
    cols = [BLUE, AMBER, GREY, GREY, RED]

    fig, axes = plt.subplots(1, 3, figsize=(14.2, 4.7))
    _bars_vs(axes[0], names, [w[n]["throughput"] for n in names], cols,
             "总写带宽 flit/cycle", f"带宽（uniform 写，K={d['meta']['k_write']}）",
             ref=r_fair, ref_label=f"R* = {r_fair:.4f}")
    _cov_bars(axes[1], names, [w[n] for n in names], cols,
              "瞬时不均衡度 CoV：任意 100 拍内十个核齐不齐", bw)
    _bars_vs(axes[2], names, [w[n]["max_min"] for n in names], cols,
             "整窗 最快核带宽 / 最慢核带宽",
             "长期速率差：有没有核被长期拖慢", fmt="{:.4f}")
    # S1's bar is the shortest one on the bandwidth axis, so a lower-left
    # legend lands on its value label; the CoV axis is tallest on the left.
    for ax, v, loc in ((axes[0], w["S1"]["throughput"], "lower right"),
                       (axes[1], cov_bin(w["S1"]), "upper right"),
                       (axes[2], w["S1"]["max_min"], "lower left")):
        ax.axhline(v, color=AMBER, ls=":", lw=1.5, zorder=0, label="S1 水平")
        ax.legend(fontsize=8.6, loc=loc, framealpha=0.95)
    fig.suptitle("S29 与 S0 / S1 / S22 / S16 同口径：总带宽、不均衡度、长期速率比",
                 fontsize=12.5, fontweight="bold")
    fig.text(0.5, 0.012,
             "S22 = 环仲裁 + 进度总线（13,920 FF-eq）；S16 = HA 授权保留"
             "（900 FF-eq）；S29 = 环仲裁 + 日历（4,440 FF-eq）",
             ha="center", fontsize=9.6, color=GREY)
    fig.tight_layout(rect=(0, 0.035, 1, 0.93))
    save(fig, "31-s29-compare.png")


# ---------------------------------------------------- microarchitecture pages
# Every block below is a piece of state or logic that exists in the simulator
# source (rg_ring2_grant / rg_ring2_dfc / rg_ring2_tdma). Grey = the baseline
# already has it, red = the scheme adds it. Widths are the ones the Pareto cost
# model charges (sweep_ring2_cc_family.HW_*), so the FF-eq lines agree with the
# Pareto slides.
NEW_FC = "#fdeaec"


def _hw(ax, x, y, w, h, text, new=False, fs=8.6, bold=False):
    _box(ax, x, y, w, h, text, fc=NEW_FC if new else PANEL,
         ec=RED if new else GREY, tc=RED if (new and bold) else INK, fs=fs,
         bold=bold, lw=1.5 if new else 1.2)


def _frame(ax, x, y, w, h, title):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.008",
                                fc="white", ec=INK, lw=1.2, zorder=1))
    ax.text(x + 0.012, y + h - 0.035, title, fontsize=9.6, fontweight="bold",
            color=INK, va="center", zorder=3)


def _lab(ax, x, y, s, color=INK, fs=8.4, ha="center", bold=False):
    ax.text(x, y, s, fontsize=fs, color=color, ha=ha, va="center", zorder=5,
            fontweight="bold" if bold else "normal",
            bbox=dict(fc="white", ec="none", pad=0.8, alpha=0.92))


def fig_s16_uarch() -> None:
    """S16 inside one HA: the added state, the decision, the two triggers."""
    fig, ax = plt.subplots(figsize=(9.7, 5.85))
    fig.subplots_adjust(left=0.01, right=0.99, top=0.93, bottom=0.01)
    _panel(ax, "S16 微架构：改动全部落在 HA（completer）内部",
           "灰 = 已有部件 · 红 = 新增 · 无总线、无新报文、线格式不变")

    _hw(ax, 0.01, 0.26, 0.095, 0.60,
        "环\n\nREQ 下环 →\n\n\n\n末 flit\n落地 →", fs=8.6)
    _frame(ax, 0.14, 0.17, 0.85, 0.71, "memory HA · completer（每个 HA 一份）")

    _hw(ax, 0.16, 0.55, 0.20, 0.26,
        "Request tracker\n（已有，512 条目）\n\n条目新增 1 bit\n「已授权」标志", fs=8.6)
    _hw(ax, 0.40, 0.55, 0.31, 0.26,
        f"授权决策\n\n直通：在飞 < {int(deck()['meta'].get('s16_overcommit') or 20)} 且无人等待\n→ 到达即授权\n"
        "排队：有等待的核里选 served 最小\n（同值取小核号），取其最老条目",
        new=True, bold=True, fs=8.3)
    _hw(ax, 0.75, 0.55, 0.22, 0.26,
        "DBIDResp 发送器（已有）\n\nt_ha_service 后放到 RSP 通道\n"
        "回到被选中的核；报文不变", fs=8.4)

    _hw(ax, 0.16, 0.22, 0.20, 0.25,
        "末 flit 落地事件\n（已有：一笔写收齐）\n\n→ outstanding −1\n→ 触发一次补授权",
        fs=8.6)
    _hw(ax, 0.40, 0.22, 0.145, 0.25,
        f"outstanding\n{max(5, int(deck()['meta'].get('s16_overcommit') or 20).bit_length())} bit\n"
        "在飞授权数\n授权 +1\n落地 −1", new=True, fs=8.4)
    _hw(ax, 0.565, 0.22, 0.145, 0.25,
        "served[c]\n10 × 10 bit\n每授权 +2 flit\n定期同减\n最小值", new=True,
        fs=8.4)
    _hw(ax, 0.75, 0.22, 0.22, 0.25,
        "每核待授权计数\n10 × 6 bit\n入队 +1 / 授权 −1\n（仿真里是每核一条 FIFO）",
        new=True, fs=8.4)

    _arrow(ax, (0.105, 0.72), (0.16, 0.72))
    _lab(ax, 0.132, 0.765, "REQ", fs=7.8, color=GREY)
    _arrow(ax, (0.105, 0.34), (0.16, 0.34))
    _arrow(ax, (0.36, 0.68), (0.40, 0.68), color=RED)
    _arrow(ax, (0.71, 0.68), (0.75, 0.68), color=RED)
    _arrow(ax, (0.36, 0.345), (0.40, 0.345), color=RED)
    _lab(ax, 0.38, 0.39, "−1", fs=7.8, color=RED)
    _arrow(ax, (0.47, 0.47), (0.47, 0.55), color=RED, style="<|-|>")
    _arrow(ax, (0.64, 0.47), (0.64, 0.55), color=RED, style="<|-|>")
    _arrow(ax, (0.80, 0.47), (0.69, 0.55), color=RED, style="<|-|>", rad=0.18)

    _box(ax, 0.01, 0.01, 0.98, 0.13,
         "只有两个触发事件，没有周期性控制环：① REQ 到达 → 直通授权或登记等待；"
         "② 一笔写的末 flit 落地 → outstanding −1 → 补发一个授权。\n"
         "成本口径（与 Pareto 图相同）：10 bit 计数 + 2 比较 + 1 加法，×10 = "
         "900 FF-eq；served 表与标志位挂在 tracker 条目上，未单列。",
         fc=PANEL, ec=PANEL, fs=9.0)
    save(fig, "32-s16-uarch.png")


def fig_s16_flow() -> None:
    """Two cores contending for one HA, step by step, with the HA's tables."""
    fig, ax = plt.subplots(figsize=(9.7, 5.85))
    fig.subplots_adjust(left=0.01, right=0.99, top=0.93, bottom=0.01)
    _panel(ax, "S16 工作示例：两个核争一个 HA（示意 overcommit = 2；"
           f"实际 {int(deck()['meta'].get('s16_overcommit') or 20)}，逻辑逐字相同）",
           "REQ 灰 · DBIDResp 红 · WriteData 蓝")
    lanes = (("C0（快核）", 0.10, False), ("C2（慢核）", 0.30, False),
             ("HA", 0.50, True))
    for lab, x, ha in lanes:
        _box(ax, x - 0.06, 0.86, 0.12, 0.075, lab, fc=NEW_FC if ha else PANEL,
             ec=RED if ha else GREY, tc=RED if ha else INK, fs=9.4, bold=True)
        ax.plot([x, x], [0.14, 0.86], color=GREY, lw=1.0, ls=":", zorder=1)

    ys = (0.81, 0.72, 0.63, 0.54, 0.415, 0.265)
    C0, C2, HA = 0.10, 0.30, 0.50

    def msg(y, a, b, text, color, ls="-"):
        _arrow(ax, (a, y), (b, y), color=color, ls=ls, lw=1.3)
        _lab(ax, (a + b) / 2, y + 0.026, text, color=color, fs=8.0)

    def note(y, text, color=GREY, bold=False):
        _lab(ax, HA - 0.008, y, text, color=color, fs=7.8, ha="right",
             bold=bold)

    msg(ys[0], C0, HA, "REQ a", GREY)
    msg(ys[0] - 0.045, HA, C0, "DBIDResp a（直通）", RED)
    msg(ys[1], C0, HA, "REQ b", GREY)
    msg(ys[1] - 0.045, HA, C0, "DBIDResp b（直通）", RED)
    msg(ys[2], C2, HA, "REQ c", GREY)
    note(ys[2] - 0.04, "在飞 = 2 已满 → 登记等待")
    msg(ys[3], C0, HA, "REQ d", GREY)
    note(ys[3] - 0.04, "有人在等 → 登记等待")
    msg(ys[4], C0, HA, "WriteData a 末 flit", BLUE, ls="--")
    msg(ys[4] - 0.045, HA, C2, "DBIDResp c", RED)
    note(ys[4] - 0.082, "名额空 1 → served: C2 = 0 < C0 = 4 → 给 C2", RED,
         bold=True)
    msg(ys[5], C0, HA, "WriteData b 末 flit", BLUE, ls="--")
    msg(ys[5] - 0.045, HA, C0, "DBIDResp d", RED)
    note(ys[5] - 0.082, "名额空 1 → 只剩 C0 在等 → 给 C0")

    for i, y in enumerate(ys):
        ax.text(0.035, y, f"{i + 1}", fontsize=9, color=RED, fontweight="bold",
                ha="center", va="center", zorder=5,
                bbox=dict(boxstyle="circle,pad=0.25", fc="white", ec=RED))

    # the HA's tables after each step
    cols = (("步", 0.635), ("在飞", 0.695), ("served\nC0", 0.765),
            ("served\nC2", 0.840), ("等待中", 0.935))
    ax.add_patch(FancyBboxPatch((0.605, 0.13), 0.385, 0.76,
                                boxstyle="round,pad=0.006", fc="white",
                                ec=GREY, lw=1.0, zorder=1))
    for lab, x in cols:
        ax.text(x, 0.855, lab, fontsize=8.6, color=GREY, ha="center",
                va="center", fontweight="bold", linespacing=1.1)
    ax.plot([0.615, 0.98], [0.815, 0.815], color=GREY, lw=0.8)
    rows = (("1", "1", "2", "0", "—"), ("2", "2", "4", "0", "—"),
            ("3", "2", "4", "0", "c"), ("4", "2", "4", "0", "c, d"),
            ("5", "2", "4", "2", "d"), ("6", "2", "6", "2", "—"))
    for y, row in zip(ys, rows):
        for (lab, x), v in zip(cols, row):
            hot = (y == ys[4] and lab.startswith("served\nC2"))
            ax.text(x, y - 0.02, v, fontsize=9.6,
                    color=RED if hot else INK, ha="center", va="center",
                    fontweight="bold" if hot else "normal")

    _box(ax, 0.01, 0.01, 0.98, 0.10,
         "看点：d 比 c 晚到，C0 又是快核，但第 5 步空出的名额给了 served 最小的 C2 —— "
         "这就是「迄今被服务最少者优先」。\n名额一空立刻补，在飞授权始终顶在上限，"
         "HA 的下环口没有一拍空转；改的是「谁」拿授权，不是「多少」授权。",
         fc=PANEL, ec=PANEL, fs=9.0)
    save(fig, "33-s16-flow.png")


def _arb_pipeline(bx, cross_text, dodge_text, board_text, note_text,
                  cross_fs=7.9):
    """The inject-port arbiter S22 and S29 share: FIFO head -> I-tag ->
    crossing test -> dodge -> free slot -> board. Only the crossing test's
    operand differs between the two schemes."""
    y, h = 0.44, 0.38
    xs = (0.01, 0.155, 0.30, 0.50, 0.70, 0.845)
    ws = (0.125, 0.125, 0.18, 0.18, 0.125, 0.145)
    _hw(bx, xs[0], y, ws[0], h, "FIFO 头 flit\n（已有）")
    _hw(bx, xs[1], y, ws[1], h, "基线 I-tag 判定\n（已有）")
    _hw(bx, xs[2], y, ws[2], h, cross_text, new=True, bold=True, fs=cross_fs)
    _hw(bx, xs[3], y, ws[3], h, dodge_text, new=True, fs=8.2)
    _hw(bx, xs[4], y, ws[4], h, "出向槽空？\n（已有）")
    _hw(bx, xs[5], y, ws[5], h, board_text, new=True, fs=8.2)
    mid = y + h / 2
    for i in range(len(xs) - 1):
        _arrow(bx, (xs[i] + ws[i], mid), (xs[i + 1], mid),
               color=RED if i >= 1 else INK)
    _lab(bx, 0.49, mid + 0.05, "跨 → 前瞻", color=RED, fs=7.8)
    # the no-cross path skips the dodge stage: arc over it
    _arrow(bx, (xs[2] + ws[2] / 2, y + h), (xs[4] + ws[4] / 2, y + h),
           color=INK, rad=-0.14)
    _lab(bx, (xs[2] + xs[4] + ws[4]) / 2 + 0.04, y + h + 0.14, "不跨 → 照常",
         color=INK, fs=7.8)
    _hw(bx, xs[3], 0.13, ws[3], 0.20,
        "都跨 → 本拍让位\n这个端口不注入", new=True, fs=8.2)
    _arrow(bx, (xs[3] + ws[3] / 2, y), (xs[3] + ws[3] / 2, 0.33), color=RED)
    _box(bx, 0.01, 0.0, 0.98, 0.105, note_text, fc=PANEL, ec=PANEL, fs=8.5)


def fig_s22_uarch() -> None:
    """S22 per node: the progress-bus signal path and the arbiter insert."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85),
                                 gridspec_kw={"height_ratios": [1.12, 1.0]})
    fig.subplots_adjust(left=0.015, right=0.985, top=0.93, bottom=0.02,
                        hspace=0.26)
    _panel(ax, "信号侧（每节点一份）：播进度 → 存表 → 算赤字 → 举请求",
           "灰 = 已有 · 红 = 新增 · 总线复用 S1 的 6 bit 线")
    # serpentine: row 1 left -> right, row 2 right -> left
    r1, r2, h = 0.58, 0.10, 0.34
    xs = (0.01, 0.26, 0.51, 0.76)
    w = 0.225
    _hw(ax, xs[0], r1, w, h, "上环成功事件\n（已有：DAT flit 注入）")
    _hw(ax, xs[1], r1, w, h, "ok_win\n6 bit 饱和计数\n本窗上环数 +1", new=True)
    _hw(ax, xs[2], r1, w, h, "窗末发送\nt mod 64 = 63\n6 bit 上总线，然后清零",
        new=True)
    _hw(ax, xs[3], r1, w, h, "6 bit 广播总线\n30 拍延迟\n复用 S1 的线", new=True)
    _hw(ax, xs[3], r2, w, h, "cum_bus 表\n10 × 8 bit\n每窗累加到达的 10 项",
        new=True)
    _hw(ax, xs[2], r2, w, h, "10 输入加法树\n÷10（移位近似）\n→ 环均值", new=True)
    _hw(ax, xs[1], r2, w, h, "deficit[n] = 均值 − 表[n]\n×10，钳位 ±64",
        new=True)
    _hw(ax, xs[0], r2, w, h, "请求 FSM ×10\n≥ 0.5 举 · ≤ 0 撤\nhold 16 拍到期强制撤",
        new=True, bold=True)
    m1, m2 = r1 + h / 2, r2 + h / 2
    for i in range(3):
        _arrow(ax, (xs[i] + w, m1), (xs[i + 1], m1),
               color=INK if i == 0 else RED)
        _arrow(ax, (xs[3 - i], m2), (xs[3 - i] - (xs[1] - xs[0] - w), m2),
               color=RED)
    _arrow(ax, (xs[3] + w / 2, r1), (xs[3] + w / 2, r2 + h), color=RED)
    _lab(ax, 0.905, 0.52, "30 拍后送达", color=RED, fs=7.8)

    _panel(bx, "仲裁侧（注入端口，每拍对每个 DAT 候选 flit）",
           "在已有的 I-tag 判定之后插入两级")
    _arb_pipeline(
        bx,
        "跨越判定（新）\n∃ 请求者 h：\ndeficit[h] ≥ 我的 + 3.0\n且 h 在此 flit 剩余路径上",
        "前瞻 dodge ≤ 8（新）\n往后找一个目的地不同、\n不跨任何请求者的 flit",
        "上环（新增动作）\nok_win +1\ndeficit −1\n≤ 0 → 撤请求",
        "成本（Pareto 同口径）：总线 6 bit × 20 = 120 · 表 10 × 8 bit × 20 = 1,600 · "
        "计数 10 bit × 20 = 200 · 运算（加法树 360 + 2 加法 80 + 8 比较 160）× 20 = "
        "12,000 → 13,920 FF-eq。\n运算占 86%，几乎全是那棵加法树。"
        "实现注记：请求位在仿真中即时可见；硬件按同一张表本地重算即可得到同一集合。")
    save(fig, "34-s22-uarch.png")


def fig_s22_flow() -> None:
    """One 64-cycle control window, and the deficit trajectory it produces."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85),
                                 gridspec_kw={"height_ratios": [1.0, 1.05]})
    fig.subplots_adjust(left=0.06, right=0.985, top=0.93, bottom=0.10,
                        hspace=0.34)
    _panel(ax, "一个控制窗的时间线（窗 64 · 总线 30 · 举 0.5 · 撤 0 · hold 16 · margin 3.0）",
           "示例数值：C4 落后，其余 9 核领先")
    # Piecewise time axis: the window itself is uneventful, the 16 cycles
    # after the bus lands are where everything happens.
    knots = ((0, 0.03), (63, 0.26), (93, 0.44), (135, 0.97))

    def X(t):
        for (ta, xa), (tb, xb) in zip(knots, knots[1:]):
            if t <= tb:
                return xa + (t - ta) / (tb - ta) * (xb - xa)
        return knots[-1][1]

    yl = 0.50
    ax.plot([X(0), X(135)], [yl, yl], color=INK, lw=1.8, zorder=1)
    ax.annotate("", xy=(X(135) + 0.012, yl), xytext=(X(133), yl),
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.8))
    ax.plot([X(0), X(63)], [yl + 0.26, yl + 0.26], color=GREY, lw=1.0)
    for t in (0, 63):
        ax.plot([X(t), X(t)], [yl + 0.23, yl + 0.29], color=GREY, lw=1.0)
    ax.text(X(31), yl + 0.31, "本窗计数（t = 0–63）：C4 只上环 3 个 flit，其余 9 核各 8 个",
            ha="center", va="bottom", fontsize=8.4, color=GREY)
    _lab(ax, X(78), yl + 0.045, "总线 30 拍", color=GREY, fs=7.6)
    events = (
        (63, 1, "right", "t=63 全部核把\n6 bit 计数放上总线", GREY),
        (93, -1, "right", "t=93 送达 → 表累加，均值 7.5\n"
                          "C4 赤字 +4.5 ≥ 0.5 → 举请求\nC2 赤字 −0.5（领先）", RED),
        (94, 1, "center", "t=94 上游 C2 的 flit 会骑过 C4 出向 hop\n"
                          "4.5 ≥ −0.5 + 3.0 → 让位 / 前瞻改发", RED),
        (103, -1, "left", "t=95…103 C4 每上环 1 个 −1\n到 −0.5 ≤ 0 → 撤请求", RED),
        (109, 1, "center", "t=109 hold 上限\n被 transit 卡住时\n到此强制撤", GREY),
        (127, -1, "center", "t=127\n下一窗末再播", GREY),
    )
    for t, side, ha, txt, col in events:
        ax.plot([X(t), X(t)], [yl - 0.04, yl + 0.04], color=col, lw=1.6,
                zorder=3)
        ax.scatter([X(t)], [yl], s=28, c=col, zorder=4)
        yy = yl + 0.10 if side > 0 else yl - 0.10
        dx = {"left": 0.012, "right": -0.012, "center": 0.0}[ha]
        ax.text(X(t) + dx, yy, txt, ha=ha, va="bottom" if side > 0 else "top",
                fontsize=7.9, color=col, linespacing=1.25)
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.05, 1.05)

    bx.set_title("落后核 C4 与领先核 C2 的赤字轨迹（示例值）", fontsize=10.5,
                 fontweight="bold", color=INK, loc="left")
    ts = [0, 93, 95, 97, 99, 101, 103, 135]
    c4 = [0, 4.5, 3.5, 2.5, 1.5, 0.5, -0.5, -0.5]
    bx.step(ts, c4, where="post", color=RED, lw=2.0, label="C4（落后）赤字")
    bx.step([0, 93, 135], [0, -0.5, -0.5], where="post", color=INK, lw=1.6,
            label="C2（领先）赤字")
    bx.axhline(0.5, color=RED, ls=":", lw=1.1)
    bx.axhline(0.0, color=GREY, ls=":", lw=1.1)
    bx.text(1.5, 0.62, "举请求线 0.5", fontsize=8, color=RED)
    bx.text(1.5, -0.42, "撤请求线 0", fontsize=8, color=GREY, va="top")
    bx.axvspan(93, 103, color=RED, alpha=0.08, lw=0)
    bx.text(98, 5.0, "C4 请求有效", fontsize=8.2, color=RED, ha="center")
    bx.axvline(109, color=GREY, ls="--", lw=1.0)
    bx.text(110.5, 3.4, "hold 到期 109\n（本例未用到）", fontsize=8, color=GREY,
            linespacing=1.2)
    bx.axvline(63, color=GREY, ls="--", lw=0.8)
    bx.text(64, 3.4, "播出 63", fontsize=8, color=GREY)
    bx.text(46, 2.2, "63 → 93：信号在路上，\n这 30 拍里没有人让位", fontsize=8.4,
            color=GREY, ha="center")
    bx.set_xlim(0, 135)
    bx.set_ylim(-1.2, 5.6)
    bx.set_xlabel("拍（cycle）", fontsize=9)
    bx.set_ylabel("赤字（flit）", fontsize=9)
    bx.tick_params(labelsize=8)
    bx.legend(fontsize=8.2, loc="upper left", framealpha=0.95)
    bx.grid(alpha=0.25)
    save(fig, "35-s22-flow.png")


def fig_s29_uarch() -> None:
    """S29 per node: the calendar trigger, and S22's arbiter reused verbatim."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85),
                                 gridspec_kw={"height_ratios": [1.12, 1.0]})
    fig.subplots_adjust(left=0.015, right=0.985, top=0.93, bottom=0.02,
                        hspace=0.26)
    _panel(ax, "触发侧（每节点一份）：日历 + 需求位，没有任何测量",
           "灰 = 已有 · 红 = 新增 · 总线复用 S1 的线，只走 1 bit/核")
    r1, r2, h = 0.58, 0.10, 0.34
    xs = (0.01, 0.26, 0.51, 0.76)
    w = 0.225
    _hw(ax, xs[0], r1, w, h, "5 bit 帧计数器\nt mod 20\n自由跑，不需要复位", new=True)
    _hw(ax, xs[1], r1, w, h, "时隙 → 持有者\ncores[cnt >> 1]\n（10 项常量表）", new=True)
    _hw(ax, xs[2], r1, w, h, "路权有效 =\ndemand[持有者]\n（查 1 bit）", new=True,
        bold=True)
    _hw(ax, xs[3], r1, w, h, "demand 寄存器\n10 bit\n到达即整字替换", new=True)
    _hw(ax, xs[0], r2, w, h, "本地 DAT 队列占用\n（已有队列）\n≥ 1 → 有需求")
    _hw(ax, xs[1], r2, w, h, "每 16 拍采样\nt mod 16 = 15\n1 bit 上总线", new=True)
    _hw(ax, xs[2], r2, w, h, "10 bit 广播总线\n30 拍延迟\n复用 S1 的线", new=True)
    _hw(ax, xs[3], r2, w, h, "送达：10 核的\n需求位整字到达", new=True)
    m1, m2 = r1 + h / 2, r2 + h / 2
    _arrow(ax, (xs[0] + w, m1), (xs[1], m1), color=RED)
    _arrow(ax, (xs[1] + w, m1), (xs[2], m1), color=RED)
    _arrow(ax, (xs[3], m1), (xs[2] + w, m1), color=RED)
    for i in range(3):
        _arrow(ax, (xs[i] + w, m2), (xs[i + 1], m2),
               color=INK if i == 0 else RED)
    _arrow(ax, (xs[3] + w / 2, r2 + h), (xs[3] + w / 2, r1), color=RED)
    _lab(ax, 0.62, 0.52, "→ 仲裁侧：本拍持有者是谁、是否有效", color=RED, fs=8.0)

    _panel(bx, "仲裁侧（注入端口，每拍对每个 DAT 候选 flit）",
           "与 S22 同一块逻辑，只换第一个操作数")
    _arb_pipeline(
        bx,
        "跨越判定（与 S22 同一块）\n持有者有效 且 我 ≠ 持有者\n且 持有者在此 flit\n剩余路径上；HA 不让位",
        "前瞻 dodge ≤ 32（新）\n往后找一个目的地不同、\n不跨持有者 hop 的 flit",
        "上环（无新增动作）\n不计数、不扣减\n没有反馈回路",
        "成本（Pareto 同口径）：总线 10 bit × 20 = 200 · 计数 (5 + 7) bit × 20 = 240 · "
        "运算（8 比较 160 + 1 加法 40）× 20 = 4,000 → 4,440 FF-eq。\n"
        "相对 S22 删掉：10 项表 1,600、加法树与赤字运算 8,000；仲裁侧一个 bit 都没改。",
        cross_fs=7.7)
    save(fig, "36-s29-uarch.png")


def fig_s29_flow() -> None:
    """Two frames of the calendar, the demand-word timing, one yield."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85),
                                 gridspec_kw={"height_ratios": [1.0, 1.0]})
    fig.subplots_adjust(left=0.015, right=0.985, top=0.93, bottom=0.02,
                        hspace=0.30)
    _panel(ax, "日历：帧 20 拍 = 10 核 × 2 拍，两帧示意；需求字每 16 拍采样、30 拍后生效",
           "红 = 有需求的持有者 · 灰斜线 = 无需求，时隙作废")
    cores = tuple(range(0, 20, 2))
    demand = {0, 2, 6, 8, 10, 12, 16, 18}          # C4 / C14 drained
    x0, sw = 0.015, 0.0485
    for fr in range(2):
        for i, c in enumerate(cores):
            x = x0 + (fr * 10 + i) * sw
            own = c in demand
            ax.add_patch(FancyBboxPatch(
                (x, 0.62), sw - 0.004, 0.20, boxstyle="square,pad=0",
                fc=NEW_FC if own else "white", ec=RED if own else GREY,
                lw=0.9, hatch=None if own else "///", zorder=2))
            ax.text(x + sw / 2 - 0.002, 0.72, f"C{c}", fontsize=7.6,
                    color=RED if own else GREY, ha="center", va="center",
                    fontweight="bold" if own else "normal", zorder=3)
        ax.text(x0 + (fr * 10 + 6.5) * sw, 0.87,
                f"帧 {fr}：t = {fr * 20}–{fr * 20 + 19}", fontsize=8.6,
                color=INK, ha="center")
    ax.annotate("C4 无需求 → 这 2 拍无人让位，谁都可以上",
                xy=(x0 + 2.5 * sw, 0.62), xytext=(x0 + 3.2 * sw, 0.47),
                fontsize=8.2, color=GREY,
                arrowprops=dict(arrowstyle="-|>", lw=1.0, color=GREY))
    ax.annotate("C6 时隙 t = 6, 7：下图放大", xy=(x0 + 3.5 * sw, 0.82),
                xytext=(x0 + 3.5 * sw, 0.94), fontsize=8.4, color=RED,
                fontweight="bold", ha="center",
                arrowprops=dict(arrowstyle="-|>", lw=1.1, color=RED))
    yb = 0.22
    ax.plot([0.03, 0.97], [yb, yb], color=INK, lw=1.4)
    for t, lab, col in ((15, "t=15 采样需求位", GREY), (31, "t=31 采样", GREY),
                        (45, "t=45 生效（管 t=45–60）", RED),
                        (61, "t=61 生效", RED)):
        x = 0.03 + t / 64 * 0.94
        ax.plot([x, x], [yb - 0.035, yb + 0.035], color=col, lw=1.4)
        ax.text(x, yb - 0.07, lab, fontsize=7.9, color=col, ha="center",
                va="top")
    for t in (15, 31):
        _arrow(ax, (0.03 + t / 64 * 0.94, yb + 0.05),
               (0.03 + (t + 30) / 64 * 0.94, yb + 0.05), color=RED, rad=-0.25,
               lw=1.0)
    ax.text(0.03 + 30 / 64 * 0.94, yb + 0.16, "30 拍总线", fontsize=7.9,
            color=RED, ha="center")
    ax.text(0.03, yb + 0.05, "生效中的需求字总是\n30–46 拍前的快照", fontsize=8.0,
            color=GREY, ha="left", va="bottom", linespacing=1.25)

    _panel(bx, "执行：时隙 C6 的两拍里，上游 C2 的两个候选 flit（顺时针方向）",
           "让位只发生在「会骑过持有者出向 hop」的 flit 上")
    nodes = ("C2", "H3", "C4", "H5", "C6", "H7", "C8", "H9", "C10")
    ry = 0.66
    bx.plot([0.04, 0.96], [ry, ry], color=GREY, lw=2.4, zorder=1)
    xs = [0.06 + i * 0.11 for i in range(len(nodes))]
    for x, lab in zip(xs, nodes):
        own = lab == "C6"
        up = lab == "C2"
        col = RED if own else (BLUE if up else GREY)
        bx.scatter([x], [ry], s=230 if (own or up) else 150, c="white",
                   edgecolors=col, linewidths=1.9 if (own or up) else 1.2,
                   zorder=3)
        bx.text(x, ry + 0.085, lab, ha="center", fontsize=9, color=col,
                fontweight="bold" if (own or up) else "normal")
    bx.plot([xs[4] + 0.012, xs[5] - 0.012], [ry, ry], color=RED, lw=5,
            zorder=2, solid_capstyle="butt")
    bx.text((xs[4] + xs[5]) / 2, ry - 0.075, "持有者的出向 hop", fontsize=8.2,
            color=RED, ha="center")
    _arrow(bx, (xs[0], ry + 0.15), (xs[8], ry + 0.15), color=RED, ls="--",
           rad=-0.12, lw=1.2)
    bx.text(xs[4], ry + 0.30, "候选 ①：C2 → C10，路径含 C6→H7 → 跨越 → 不能上",
            fontsize=8.4, color=RED, ha="center", fontweight="bold")
    _arrow(bx, (xs[0], ry - 0.15), (xs[3], ry - 0.15), color=BLUE, ls="--",
           rad=0.16, lw=1.2)
    bx.text((xs[0] + xs[3]) / 2, ry - 0.30, "候选 ②：C2 → H5，在 C6 之前下环 → 不跨 → 前瞻改发它",
            fontsize=8.4, color=BLUE, ha="center", fontweight="bold")
    bx.text(xs[1], ry - 0.13, "HA 节点不参与让位", fontsize=7.8, color=GREY,
            ha="center")
    _box(bx, 0.02, 0.01, 0.96, 0.20,
         "每核每 20 拍保证 2 拍路权 → 100 拍公平窗里 5 次，这是硬保证，不靠收敛。"
         "但日历不知道谁落后：让位方向按核号轮转，不按赤字，\n"
         "所以领先核也会拿到时隙 —— 这就是 S29 对 S22 带宽 −6.04% vs −2.21% 的全部来源。",
         fc=PANEL, ec=PANEL, fs=8.8)
    save(fig, "37-s29-flow.png")


def fig_window_diagram() -> None:
    """Shared S19/S20 window actuator with their two feedback signals."""
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(9.7, 5.85))
    fig.subplots_adjust(left=0.015, right=0.985, top=0.925, bottom=0.02,
                        hspace=0.22)

    _cap = int(deck()["meta"]["core_outstanding"])
    _panel(ax, "共同执行器：每个 core 一扇动态 outstanding 窗口",
           f"初值 16 · 下限 8 · 硬上限 {_cap}")
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
         f"窗口只限制新 REQ；Retry 重发不被拦。Wc 不能超过静态 core_outstanding={_cap}。"
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
    _cov_bars(axes[1], names, [w[n] for n in names], cols, "瞬时不均衡度 CoV", bw)
    _bars_vs(axes[2], names, [w[n]["max_min"] for n in names], cols,
             "整窗 最快核带宽 / 最慢核带宽",
             "长期速率差（越接近 1 越好）", fmt="{:.4f}")
    fig.suptitle("S19 / S20：不同信号驱动同一动态窗口；当前工作点结果接近",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "29-window-compare.png")


# ------------------------------------------------- S1 signal split (item 1)
def _per_bin_cov(row: dict, bw: int) -> tuple[list[int], list[float]]:
    cnt = {c: [round(x * bw) for x in v] for c, v in row["per_core_binned"].items()}
    cs = sorted(cnt, key=int)
    nb = len(cnt[cs[0]])
    return row["bin_t"], [cov([cnt[c][b] for c in cs]) for b in range(nb)]


def _smooth(ys: list[float], win: int) -> list[float]:
    return [sum(ys[max(0, i - win):i + win + 1]) / len(ys[max(0, i - win):i + win + 1])
            for i in range(len(ys))]


def _cum_curves(ax, row: dict, cores: list[int], total: int, title: str,
                slow: set[int] | None = None, legend: bool = False,
                xmax: float | None = None, fs: float = 9.0) -> None:
    """Ten per-core cumulative curves, normalised to each core's own quota."""
    cum = row["cum"]
    ts = cum["t"]
    fin = {int(c): v for c, v in row["finish_by_core"].items()}
    for c in cores:
        ys = [v / total for v in cum["by_core"][str(c)]]
        col = RED if slow and c in slow else BLUE
        ax.plot(ts, ys, lw=1.25, color=col, alpha=0.85 if col == BLUE else 1.0,
                label=("邻 mem = 1 的核" if c == min(slow) else None) if slow and c in slow
                else ("其余六核" if c == min(set(cores) - (slow or set())) else None))
        ax.plot([fin[c]], [1.0], "o", ms=3.2, color=col)
    lo, hi = min(fin.values()), max(fin.values())
    ax.axvline(lo, color=GREY, ls=":", lw=0.9)
    ax.axvline(hi, color=GREY, ls=":", lw=0.9)
    ax.text(0.97, 0.05, f"最早 {lo:,} · 最晚 {hi:,}\n最晚 / 最早 = {hi / lo:.3f}",
            transform=ax.transAxes, fontsize=fs - 0.6, va="bottom", ha="right",
            color=INK, bbox=dict(fc="white", ec="none", alpha=0.85, pad=1.5))
    ax.set_ylim(0, 1.06)
    if xmax:
        ax.set_xlim(0, xmax)
    ax.set_title(title, fontsize=fs + 1.2, fontweight="bold")
    ax.grid(alpha=0.22)
    ax.tick_params(labelsize=fs - 1)
    if legend:
        ax.legend(fontsize=fs - 1, loc="upper left")


SLOW = {0, 8, 10, 18}


def fig_s1_signal() -> None:
    """S1 with only board failures, only eject failures, or both as its signal."""
    d = deck()
    w, bw = d["write"], d["meta"]["bin_w"]
    names = ["S0", "S1D", "S1U", "S1"]
    labels = ["S0", "S1D\n只下环", "S1U\n只上环", "S1\n都计"]
    cols = [BLUE, GREEN, AMBER, RED]
    fig, axes = plt.subplots(1, 4, figsize=(15.0, 4.55),
                             gridspec_kw={"width_ratios": [1, 1, 1.55, 1.2]})
    _bars_vs(axes[0], labels, [w[n]["throughput"] for n in names], cols,
             "总写带宽 flit/cycle", "总带宽", ref=d["ideal"]["r_fair"],
             ref_label=f"R* = {d['ideal']['r_fair']:.4f}")
    _cov_bars(axes[1], labels, [w[n] for n in names], cols, "瞬时不均衡度 CoV", bw)
    for a in axes[:2]:
        a.tick_params(axis="x", labelsize=8.6)

    ax = axes[2]
    for n, lab, col in (("S1D", "S1D（与 S0 逐拍相同）", BLUE),
                        ("S1U", "S1U（与 S1 逐拍相同）", RED)):
        xs, ys = _per_bin_cov(w[n], bw)
        ax.plot(xs, ys, lw=0.45, color=col, alpha=0.35)
        ax.plot(xs, _smooth(ys, 40), lw=2.0, color=col, label=lab)
    ax.set_ylim(0.0, 0.46)
    ax.set_xlabel("cycle")
    ax.set_ylabel(f"{bw} 拍窗内 10 核带宽的 CoV")
    ax.set_title("瞬时不均衡度随时间（细线 = 每箱，粗线 = 滑动平均）",
                 fontsize=11.5, fontweight="bold")
    ax.grid(alpha=0.22)
    ax.legend(fontsize=9, loc="upper right")

    ax = axes[3]
    ss = w["S1"]["fc"]["signal_sum"]
    cores = d["meta"]["cores"]
    up = [ss["up"][str(c)] for c in cores]
    dn = [max(ss["down"][str(c)], 0.5) for c in cores]
    xs = list(range(len(cores)))
    ax.bar([x - 0.2 for x in xs], up, width=0.4, color=AMBER, label="上环失败（全程累计）")
    ax.bar([x + 0.2 for x in xs], dn, width=0.4, color=GREEN, label="下环失败（全程累计）")
    ax.set_yscale("log")
    ax.set_ylim(0.3, 3e7)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"C{c}" for c in cores], fontsize=8.5)
    ax.set_ylabel("S1 每核的两路原始信号（对数轴）")
    n_win = ss["windows"][str(cores[0])]
    ax.set_title(f"S1 信号来源：上环 {sum(up):,} 次 vs 下环 "
                 f"{sum(ss['down'].values())} 次", fontsize=11.5, fontweight="bold")
    ax.text(0.02, 0.96, f"{n_win} 个 64 拍窗 × 10 核：\n上环等级 > 0 的窗 "
            f"{sum(ss['up_lv'].values()):,} 个；下环等级 > 0 的窗 0 个",
            transform=ax.transAxes, fontsize=8.6, color=INK, va="top")
    ax.legend(fontsize=8.4, loc="upper right", bbox_to_anchor=(1.0, 0.86))
    ax.grid(axis="y", alpha=0.22)
    fig.suptitle("S1 的拥塞等级只由上环失败驱动：只计下环 = S0，只计上环 = S1，"
                 "两者都计 = S1", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "38-s1-signal.png")


def fig_s1_signal_finish() -> None:
    """Per-core completion curves of the three S1 signal variants."""
    d = deck()
    w = d["write"]
    cores = d["meta"]["cores"]
    total = d["meta"]["k_write"] * d["meta"]["w_flits"]
    fig, axes = plt.subplots(1, 3, figsize=(13.6, 4.5),
                             gridspec_kw={"width_ratios": [1, 1, 1.15]})
    xmax = max(max(int(v) for v in w[n]["finish_by_core"].values())
               for n in ("S1D", "S1U", "S1")) * 1.04
    _cum_curves(axes[0], w["S1D"], cores, total, "S1D 只计下环失败（= S0）",
                slow=SLOW, legend=True, xmax=xmax)
    _cum_curves(axes[1], w["S1U"], cores, total, "S1U 只计上环失败（= S1）",
                slow=SLOW, xmax=xmax)
    axes[0].set_ylabel("已上环 WriteData / 本核配额")
    for a in axes[:2]:
        a.set_xlabel("cycle")
    ax = axes[2]
    xs = list(range(len(cores)))
    for k, (n, lab, col) in enumerate((("S1D", "S1D", GREEN), ("S1U", "S1U", AMBER),
                                       ("S1", "S1 两者都计", RED))):
        fin = [w[n]["finish_by_core"][str(c)] / 1000 for c in cores]
        ax.bar([x + (k - 1) * 0.27 for x in xs], fin, width=0.27, color=col,
               label=lab, alpha=0.92)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"C{c}" for c in cores], fontsize=8.5)
    ax.set_ylabel("本核最后一个 WriteData 上环的拍（千拍）")
    ax.set_ylim(50, 92)
    ax.set_title("各核完成时间：S1U 与 S1 完全重合", fontsize=10.5, fontweight="bold")
    ax.legend(fontsize=8.8, loc="upper center", ncol=3)
    ax.grid(axis="y", alpha=0.22)
    fig.suptitle("各核完成时间曲线：红 = 邻 mem = 1 的四核（C0 / C8 / C10 / C18）",
                 fontsize=12.5, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "39-s1-signal-finish.png")


# ------------------------------------------------- read payload (item 2)
def _read_per_bin_cov(row: dict, bw: int) -> tuple[list[int], list[float]]:
    rb = row["recv_binned"]
    cs = sorted(rb, key=int)
    ts = rb[cs[0]]["t"]
    t_fair = row["t_fair"]
    idx = [i for i, t in enumerate(ts) if t + bw <= t_fair]
    ys = [cov([round(rb[c]["rate"][i] * bw) for c in cs]) for i in idx]
    return [ts[i] for i in idx], ys


def fig_read_payload() -> None:
    """S0 read side with CompData = 1 / 2 / 4 flits."""
    d = deck()
    rp, bw = d["read_payload"], d["meta"]["bin_w"]
    cores = d["meta"]["cores"]
    ms = d["meta"]["read_payloads"]
    cols = {1: RED, 2: BLUE, 4: AMBER}
    fig, axes = plt.subplots(1, 4, figsize=(15.0, 4.55),
                             gridspec_kw={"width_ratios": [1.6, 0.9, 0.9, 1.4]})
    ax = axes[0]
    xs = list(range(len(cores)))
    for k, m in enumerate(ms):
        r = rp[f"S0-m{m}"]
        ax.bar([x + (k - 1) * 0.27 for x in xs],
               [r["bw_by_core"][str(c)] for c in cores], width=0.27,
               color=cols[m], label=f"CompData = {m} flit", alpha=0.92)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"C{c}" for c in cores], fontsize=8.5)
    ax.set_ylim(0.28, 0.72)
    ax.set_ylabel("每核读带宽 flit/cycle（争用窗内）")
    ax.set_title("每核读带宽：1 flit 时六快四慢重现", fontsize=11.5, fontweight="bold")
    ax.legend(fontsize=8.6, loc="lower center", ncol=3, framealpha=0.95)
    ax.grid(axis="y", alpha=0.22)
    names = [f"{m} flit" for m in ms]
    cl = [cols[m] for m in ms]
    _cov_bars(axes[1], names, [rp[f"S0-m{m}"] for m in ms], cl, "瞬时不均衡度 CoV", bw)
    _bars_vs(axes[2], names, [rp[f"S0-m{m}"]["max_min"] for m in ms], cl,
             "整窗 最快核 / 最慢核", "长期速率比", fmt="{:.4f}")
    ax = axes[3]
    for m in ms:
        r = rp[f"S0-m{m}"]
        xs2, ys = _read_per_bin_cov(r, bw)
        tf = r["t_fair"]
        ax.plot([x / tf for x in xs2], ys, lw=0.4, color=cols[m], alpha=0.3)
        ax.plot([x / tf for x in xs2], _smooth(ys, 12), lw=1.9, color=cols[m],
                label=f"{m} flit（争用窗 {tf:,} 拍）")
    ax.set_ylim(0.0, 0.42)
    ax.set_xlabel("时间 / 本例争用窗长度")
    ax.set_ylabel(f"{bw} 拍窗内 10 核带宽的 CoV")
    ax.set_title("瞬时不均衡度随时间", fontsize=11.5, fontweight="bold")
    ax.legend(fontsize=8.6, loc="upper right")
    ax.grid(alpha=0.22)
    fig.suptitle(f"S0 读侧 · CompData 1 / 2 / 4 flit（K = {d['meta']['k_read']} 笔/核）："
                 "1 flit 不均最大，4 flit 长期均等但箱内抖动大于 2 flit",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    save(fig, "40-read-payload.png")


def fig_read_payload_finish() -> None:
    d = deck()
    rp = d["read_payload"]
    cores = d["meta"]["cores"]
    ms = d["meta"]["read_payloads"]
    fig, axes = plt.subplots(1, 3, figsize=(13.6, 4.4))
    for ax, m in zip(axes, ms):
        r = rp[f"S0-m{m}"]
        total = d["meta"]["k_read"] * m
        hops = ", ".join(sorted({h.split(":")[-1].upper() for h, *_ in r["busiest_hops"]}))
        _cum_curves(ax, r, cores, total,
                    f"CompData = {m} flit · 最忙 VC：{hops}（占用 {r['busiest_hops'][0][1]:.3f}）",
                    slow=SLOW, legend=(m == ms[0]), fs=9.0)
        ax.set_xlabel("cycle")
    axes[0].set_ylabel("已收到 CompData / 本核配额")
    fig.suptitle("S0 读侧各核完成时间曲线：1 flit 时 REQ VC 与 DAT VC 并列绑定，"
                 "core→HA 方向的几何差异回到读侧", fontsize=12.5, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    save(fig, "41-read-payload-finish.png")


# ------------------------------------------------- scalar metric (items 3, 4)
def _metric() -> dict:
    return json.loads((RES / "metric_ring2_cc.json").read_text())


def _cov(j: float) -> float:
    return ((1 - j) / j) ** 0.5


def fig_metric() -> None:
    """The frontier in (CoV, R), the fit, and the scalar it induces."""
    m = _metric()
    tr = json.loads((RES / "tradeoff_ring2_cc.json").read_text())
    r_fair, kappa = m["r_fair"], m["kappa"]
    pts = sorted({(round(p["jain_bin"], 6), round(p["bw_monotone"], 6))
                  for p in tr["jain_curve"]})
    cx = [_cov(j) for j, _ in pts]
    cy = [bw for _, bw in pts]
    fig = plt.figure(figsize=(13.6, 5.9))
    ax = fig.add_axes([0.055, 0.105, 0.50, 0.80])
    bx = fig.add_axes([0.625, 0.105, 0.36, 0.80])

    xs = [x / 100 for x in range(0, 41)]
    ax.scatter(cx, cy, s=14, color=RED, zorder=4, label="LP 上界 R(CoV) 的 80 个点")
    ax.plot(xs, [r_fair + kappa * x for x in xs], color=RED, lw=2.0, zorder=3,
            label=f"拟合  R = R* + κ·CoV，κ = {kappa:.3f}")
    # slope triangle on the frontier: one unit of CoV buys kappa of bandwidth
    x0, x1 = 0.20, 0.30
    ax.plot([x0, x1, x1], [r_fair + kappa * x0] * 2 + [r_fair + kappa * x1],
            color=RED, lw=1.0, ls="-", alpha=0.7, zorder=2)
    ax.annotate(f"斜率 κ = {kappa:.2f}：每 +0.1 CoV 换 +{kappa * 0.1:.2f} flit/cycle",
                (x0 + 0.012, r_fair + kappa * x0 - 0.09), fontsize=8.0, color=RED,
                va="top", ha="left")
    # construction for S0: the parallel through S0 hits CoV = 0 at Phi
    s0 = m["schemes"]["S0"]
    phi_abs = s0["bw"] - kappa * s0["cov"]
    ax.plot([0, s0["cov"]], [phi_abs, s0["bw"]], color=BLUE, lw=1.3, ls="--", zorder=3,
            label="S0 的等 φ 线：滑到 CoV = 0 处读出 Φ(S0)")
    ax.scatter([0], [phi_abs], s=60, color=BLUE, edgecolors="k", linewidths=0.5, zorder=6)
    ax.annotate(f"Φ(S0) = R − κ·CoV\n= {phi_abs:.3f}", (-0.03, phi_abs), xytext=(0, -8),
                textcoords="offset points", fontsize=8.2, color=BLUE, va="top", ha="center")
    ax.annotate("", (-0.03, r_fair), (-0.03, phi_abs),
                arrowprops=dict(arrowstyle="<->", color=INK, lw=1.0))
    ax.plot([-0.03, 0], [r_fair, r_fair], color=INK, lw=0.6, ls=":")
    ax.plot([-0.03, 0], [phi_abs, phi_abs], color=INK, lw=0.6, ls=":")
    ax.text(-0.025, (r_fair + phi_abs) / 2, f"R* − Φ\n= (1−φ)·R*\n= {r_fair - phi_abs:.3f}",
            fontsize=7.8, color=INK, va="center", ha="left",
            bbox=dict(fc="white", ec="none", alpha=0.9, pad=1.0))
    for phi in (0.95, 0.90, 0.85, 0.75):
        ax.plot(xs, [phi * r_fair + kappa * x for x in xs], color=GREY, lw=0.9,
                ls="--", zorder=1)
        ax.text(0.405, phi * r_fair + kappa * 0.405 - 0.04, f"φ = {phi:.2f}",
                fontsize=8.2, color=GREY, va="top")
    ax.plot([], [], color=GREY, lw=0.9, ls="--", label="等 φ 线：R − κ·CoV = 常数（与上界平行）")
    show = ["S0", "S1", "S1T", "S16", "ITAG", "S22", "S29", "S28", "S26", "S19", "S20",
            "S28S", "S27"]
    off = {"S0": (6, -12), "S1": (6, -12), "S1T": (6, 4), "S16": (6, -12), "ITAG": (6, 4),
           "S22": (6, -12), "S29": (-30, -12), "S28": (6, 4), "S26": (6, -12),
           "S19": (6, 5), "S20": (-30, 5), "S28S": (6, -12), "S27": (6, -12)}
    for nm in show:
        s = m["schemes"][nm]
        col = RED if nm == "S16" else BLUE if nm == "S0" else AMBER if nm == "S1" else INK
        ax.scatter([s["cov"]], [s["bw"]], s=54, color=col, edgecolors="k",
                   linewidths=0.5, zorder=6)
        ax.annotate(nm, (s["cov"], s["bw"]), xytext=off[nm], textcoords="offset points",
                    fontsize=8.6, color=col, fontweight="bold")
    ax.scatter([0], [r_fair], s=90, facecolors="white", edgecolors=RED, linewidths=1.6,
               zorder=5)
    ax.annotate(f"R* = {r_fair:.4f}\n（CoV = 0：十核完全等速率）", (-0.03, r_fair),
                xytext=(0, 9), textcoords="offset points", fontsize=8.2, color=RED,
                ha="center", va="bottom")
    ax.set_xlim(-0.075, 0.46)
    ax.set_ylim(3.1, 6.7)
    ax.set_xlabel("不均衡度 CoV = 十核 100 拍窗带宽的标准差 / 均值（0 = 完全均等）")
    ax.set_ylabel("总写带宽 R  flit/cycle")
    ax.set_title("上界在 CoV 坐标下是一条直线；等 φ 线与它平行",
                 fontsize=11.5, fontweight="bold")
    ax.grid(alpha=0.22)
    ax.legend(fontsize=8.6, loc="lower right")

    order = sorted(show, key=lambda n: -m["schemes"][n]["phi"])
    ys = list(range(len(order)))[::-1]
    gb = [m["schemes"][n]["gap_bw"] for n in order]
    gf = [m["schemes"][n]["gap_fair"] for n in order]
    bx.barh(ys, gb, color=BLUE, height=0.62, label="带宽缺口 (R* − R)/R*")
    bx.barh(ys, gf, left=gb, color=AMBER, height=0.62,
            label="不均衡按 κ 折成带宽 κ·CoV/R*")
    for y, n, a, b in zip(ys, order, gb, gf):
        bx.text(a + b + 0.006, y, f"φ = {m['schemes'][n]['phi']:.3f}", va="center",
                fontsize=8.6, color=INK)
    bx.set_yticks(ys)
    bx.set_yticklabels(order, fontsize=9)
    bx.set_xlim(0, 0.62)
    bx.set_xlabel("1 − φ（离理想控制器的总缺口，带宽单位）")
    bx.set_title("1 − φ 的两项分解", fontsize=11.5, fontweight="bold")
    bx.legend(fontsize=8.4, loc="upper right")
    bx.grid(axis="x", alpha=0.22)
    save(fig, "42-metric-derivation.png")


def _knob_lab(name: str, val) -> str:
    if name == "S0" and isinstance(val, (int, float)) and val >= 1e8:
        return "off"
    if name == "S1":
        return (str(val).replace("gentle·", "g").replace("spec·", "s")
                .replace("harsh·", "h"))
    if isinstance(val, float):
        return f"{val:g}"
    return str(val)


def fig_metric_knobs() -> None:
    """All 13 official schemes' knob trajectories in the (CoV, R) plane."""
    sw = json.loads((RES / "probe_ring2_knob13.json").read_text())
    m = _metric()
    r_fair, kappa, r_max = m["r_fair"], m["kappa"], m["r_max"]
    titles = {
        "S0": "S0 · t_inj", "S1": "S1 · band×cap", "S1T": "S1T · cap",
        "S16": "S16 · overcommit", "ITAG": "ITAG · t_inj",
        "S19": "S19 · swift_t_mult", "S20": "S20 · win_max",
        "S22": "S22 · dfc_margin", "S26": "S26 · extra hops",
        "S27": "S27 · XOFF", "S28": "S28 · α", "S28S": "S28S · target",
        "S29": "S29 · slot",
    }
    fig, axes = plt.subplots(3, 5, figsize=(15.4, 8.55), sharex=False, sharey=False)
    xs = [x / 100 for x in range(0, 55)]
    frontier = [min(r_max, r_fair + kappa * x) for x in xs]
    for ax, swp in zip(axes.flat, sw["sweeps"]):
        nm = swp["name"]
        rows = list(swp["rows"])
        ax.plot(xs, frontier, color=RED, lw=1.15, alpha=0.75, zorder=1)
        if nm == "S1":
            drawn = []
            for band, col in (("gentle", BLUE), ("spec", AMBER), ("harsh", RED)):
                sub = [r for r in rows if str(r["val"]).startswith(band)]
                sub.sort(key=lambda r: -float(str(r["val"]).split("·")[1]))
                ax.plot([r["cov"] for r in sub], [r["thr"] for r in sub],
                        "-o", color=col, lw=1.45, ms=3.8, zorder=4, label=band)
                drawn.extend(sub)
            ax.legend(fontsize=6.2, loc="lower left", framealpha=0.92,
                      handlelength=1.2, borderpad=0.25)
            rows = drawn
        else:
            def _key(r):
                v = r["val"]
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0
            rows = sorted(rows, key=_key)
            ax.plot([r["cov"] for r in rows], [r["thr"] for r in rows],
                    "-o", color=INK, lw=1.45, ms=3.8, zorder=4)
        seen: set[tuple[float, float]] = set()
        for r in rows:
            key = (round(r["cov"], 3), round(r["thr"], 2))
            if key in seen:
                continue
            seen.add(key)
            ax.annotate(_knob_lab(nm, r["val"]), (r["cov"], r["thr"]),
                        xytext=(3, 2), textcoords="offset points",
                        fontsize=6.0, color=INK, zorder=5)
        official = [r for r in rows if r["val"] == swp["anchor"]]
        if official:
            o = official[0]
            ax.scatter([o["cov"]], [o["thr"]], s=78, marker="*", color=RED,
                       zorder=6, edgecolors="k", linewidths=0.4)
        ax.set_title(titles[nm], fontsize=10, fontweight="bold")
        ax.set_xlim(-0.02, 0.54)
        ax.set_ylim(2.85, 6.58)
        ax.grid(alpha=0.22)
        ax.tick_params(labelsize=7.2)
    how, why = axes.flat[13], axes.flat[14]
    for ax, title, body in (
        (how, "怎么读",
         "每个面板 = 该方案自己的一个旋钮。\n"
         "横轴：100 拍窗 CoV（越左越均衡）\n"
         "纵轴：总写带宽 R（越高越好）\n"
         "红线：LP 上界，唯一的理想直线\n"
         "折线：拧这个旋钮走出的轨迹\n"
         "星：官方工作点\n"
         f"K = {sw['k']}（与筛选轮相同）\n"
         "S1 按 band 拆成三条，不是一条"),
        (why, "为什么大多不是直线",
         "理想直线只有一个自由度：\n"
         "把速率从 4 个慢核挪给 6 个快核。\n"
         "R 与 σ 都 ∝ δ，所以是直线。\n"
         "\n"
         "旋钮不是这个 δ。它改阈值 / 窗口\n"
         "/ 日历 / 背压，同时搅动份额、\n"
         "空转和短窗抖动。\n"
         "\n"
         "于是走出折线、竖线或一团点，\n"
         "而不是汇率恒定的直线。"),
    ):
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        ax.text(0.04, 0.96, title, fontsize=11, fontweight="bold", color=INK,
                transform=ax.transAxes, va="top")
        ax.text(0.04, 0.82, body, fontsize=8.0, color=INK,
                transform=ax.transAxes, va="top", linespacing=1.35)
    axes[0, 0].set_ylabel("总写带宽 R  flit/cycle")
    axes[1, 0].set_ylabel("总写带宽 R  flit/cycle")
    axes[2, 0].set_ylabel("总写带宽 R  flit/cycle")
    for ax in (axes[2, 0], axes[2, 1], axes[2, 2]):
        ax.set_xlabel("100 拍窗 CoV")
    fig.suptitle("13 个方案各自扫一个旋钮：每条曲线是该方案自己的调参轨迹",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    save(fig, "43-metric-knobs.png")


# ------------------------------------------------- completion curves (item 5)
FINISH_ORDER = ["S0", "S1", "S1T", "S16", "ITAG", "S19", "S20", "S22", "S26", "S27",
                "S28", "S28S", "S29"]
FINISH_LABEL = {"S0": "S0 基线", "S1": "S1 AIMD", "S1T": "S1T 分向", "S16": "S16 授权保留",
                "ITAG": "S0 I-tag 调参", "S19": "S19 Swift", "S20": "S20 DCTCP",
                "S22": "S22 赤字让路", "S26": "S26 自适应路由", "S27": "S27 逐跳背压",
                "S28": "S28 显式速率", "S28S": "S28S 等分速率", "S29": "S29 日历让路"}


def fig_finish_all() -> None:
    d = deck()
    w = d["write"]
    cores = d["meta"]["cores"]
    total = d["meta"]["k_write"] * d["meta"]["w_flits"]
    fig, axes = plt.subplots(3, 5, figsize=(15.0, 8.4))
    flat = list(axes.flat)
    xmax = max(int(v) for n in FINISH_ORDER for v in w[n]["finish_by_core"].values())
    for ax, nm in zip(flat, FINISH_ORDER):
        _cum_curves(ax, w[nm], cores, total, FINISH_LABEL[nm], slow=SLOW, fs=8.4,
                    xmax=xmax * 1.02, legend=(nm == "S0"))
        ax.set_xticks([0, 40_000, 80_000, 120_000])
        ax.set_xticklabels(["0", "40k", "80k", "120k"])
    for ax in flat[len(FINISH_ORDER):]:
        ax.axis("off")
    flat[-1].text(0.0, 0.85, "读法", fontsize=11, fontweight="bold", color=INK,
                  transform=flat[-1].transAxes)
    flat[-1].text(0.0, 0.78,
                  "每条线 = 一个核已上环的 WriteData 占本核配额\n"
                  "（K = 20000 笔 × 2 flit）的比例；线走平的横坐标\n"
                  "就是该核的完成时刻，圆点标出。\n\n"
                  "红 = 邻 mem = 1 的四核（C0 / C8 / C10 / C18），\n"
                  "蓝 = 其余六核。\n\n"
                  "斜率 = 该核的瞬时带宽；十条线越贴合、\n"
                  "最晚 / 最早 越接近 1，长期公平越好。\n"
                  "S1U / S1D 与 S1 / S0 逐拍相同，不重复画。",
                  fontsize=8.6, color=INK, va="top", transform=flat[-1].transAxes,
                  linespacing=1.35)
    for ax in axes[:, 0]:
        ax.set_ylabel("进度 / 配额", fontsize=8.5)
    for ax in axes[2, :]:
        ax.set_xlabel("cycle", fontsize=8.5)
    fig.suptitle("十三个方案的各核完成时间曲线（uniform 写，K = 20000，同一 fabric）",
                 fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    save(fig, "44-finish-all.png")


def fig_finish_spread() -> None:
    d = deck()
    w = d["write"]
    rows = []
    for nm in FINISH_ORDER:
        fin = [int(v) for v in w[nm]["finish_by_core"].values()]
        rows.append((nm, min(fin), max(fin), max(fin) / min(fin), w[nm]["throughput"],
                     cov_bin(w[nm])))
    rows.sort(key=lambda r: r[3])
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(13.6, 4.5),
                                 gridspec_kw={"width_ratios": [1.35, 1.0]})
    ys = list(range(len(rows)))[::-1]
    for y, (nm, lo, hi, ratio, thr, j) in zip(ys, rows):
        col = RED if nm == "S16" else AMBER if nm == "S1" else BLUE if nm == "S0" else GREY
        ax.plot([lo / 1000, hi / 1000], [y, y], color=col, lw=5, solid_capstyle="butt",
                alpha=0.85)
        ax.plot([lo / 1000], [y], "|", color=INK, ms=11, mew=1.4)
        ax.plot([hi / 1000], [y], "|", color=INK, ms=11, mew=1.4)
        ax.text(hi / 1000 + 1.2, y, f"×{ratio:.3f}", va="center", fontsize=8.8, color=INK)
    ax.set_yticks(ys)
    ax.set_yticklabels([FINISH_LABEL[r[0]] for r in rows], fontsize=9)
    ax.set_xlabel("最早完成核 → 最晚完成核（千拍）")
    ax.set_xlim(45, 128)
    ax.set_title("完成时间跨度（横条 = 十核从最早到最晚），右侧 = 最晚 / 最早",
                 fontsize=11, fontweight="bold")
    ax.grid(axis="x", alpha=0.22)
    for nm, lo, hi, ratio, thr, j in rows:
        col = RED if nm == "S16" else AMBER if nm == "S1" else BLUE if nm == "S0" else GREY
        bx.scatter([j], [ratio], s=60, color=col, edgecolors="k", linewidths=0.5, zorder=4)
        bx.annotate(nm, (j, ratio), xytext=(5, 3), textcoords="offset points",
                    fontsize=8.4, color=INK)
    bx.set_xlabel(f"{d['meta']['bin_w']} 拍窗 CoV（瞬时不均衡度，越左越均衡）")
    bx.set_ylabel("完成时间 最晚 / 最早")
    bx.set_title("瞬时不均衡度 vs 完成时间偏斜：两者不是同一件事",
                 fontsize=11, fontweight="bold")
    bx.grid(alpha=0.22)
    fig.tight_layout()
    save(fig, "45-finish-spread.png")


def main() -> None:
    _use_cjk_font()
    OUT.mkdir(parents=True, exist_ok=True)
    fig_s1_signal()
    fig_s1_signal_finish()
    fig_read_payload()
    fig_read_payload_finish()
    fig_metric()
    fig_metric_knobs()
    fig_finish_all()
    fig_finish_spread()
    fig_saturation()
    fig_instbal()
    fig_s1_effect()
    fig_cc_taxonomy()
    fig_gap_diagram()
    fig_gap_compare()
    fig_tradeoff()
    fig_pareto()
    fig_hot()
    fig_s16_diagram()
    fig_s16_compare()
    fig_window_diagram()
    fig_window_compare()
    fig_s22_diagram()
    fig_s22_compare()
    fig_s29_diagram()
    fig_s29_compare()
    fig_s16_uarch()
    fig_s16_flow()
    fig_s22_uarch()
    fig_s22_flow()
    fig_s29_uarch()
    fig_s29_flow()


if __name__ == "__main__":
    main()
