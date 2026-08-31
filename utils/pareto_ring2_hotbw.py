#!/usr/bin/env python3
"""Bandwidth against hardware cost on the fixed non-uniform load.

Two figures, because the honest answer needs both:

  `pareto_ring2_hotbw.png`  -- the Pareto itself: total write bandwidth as a
      fraction of that load's own ideal bound R*, against added hardware state.
      Drawn at the *corrected* baseline (per-core outstanding cap 32), because
      drawing it at the study's default cap of 128 credits the controllers with a
      gain that a free knob change delivers on its own.

  `hotbw_decomp.png` -- why the correction matters: for every scheme, how much of
      its bandwidth over the default-cap S0 comes from the cap and how much from
      the controller. The second bar is the one that has to justify the hardware.

Usage:
    python3 pareto_ring2_hotbw.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "probe_ring2_hotbw.json"
OUT_P = ROOT / "results" / "pareto_ring2_hotbw.png"
OUT_D = ROOT / "results" / "hotbw_decomp.png"


def _use_cjk_font() -> None:
    from matplotlib import font_manager as fm
    wanted = ("micro hei", "cjk", "noto sans sc", "source han sans")
    for f in fm.fontManager.ttflist:
        if any(w in f.name.lower() for w in wanted):
            plt.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def frontier(rows: list[dict]) -> list[dict]:
    """Cheapest-first scan, buildable points only, on the bandwidth axis."""
    best, out = -1e9, []
    for r in sorted((x for x in rows if x.get("bus_rule_ok", True)),
                    key=lambda x: x["hw_cost"]):
        if r["bw_vs_ideal"] > best:
            out.append(r)
            best = r["bw_vs_ideal"]
    return out


def plot_pareto(d: dict) -> None:
    rows = d["passes"]["32"]
    s0 = next(r for r in rows if r["name"].startswith("S0"))
    fig, ax = plt.subplots(figsize=(15.0, 7.6))
    fig.subplots_adjust(left=0.065, right=0.60, top=0.885, bottom=0.10)

    for r in rows:
        feas = r.get("bus_rule_ok", True)
        c, m = ("#bbbbbb", "x") if not feas else ("#1f6feb", "o")
        ax.scatter(max(r["hw_cost"], 1), r["bw_vs_ideal"], s=95, c=c, marker=m,
                   zorder=3, edgecolors="k" if feas else None, linewidths=0.6)

    order = sorted(rows, key=lambda r: -r["bw_vs_ideal"])
    for i, r in enumerate(order):
        feas = r.get("bus_rule_ok", True)
        gain = r["bw_vs_ideal"] - s0["bw_vs_ideal"]
        ax.annotate(
            f"{r['name']}   bw/R*={r['bw_vs_ideal']:.4f}  "
            f"({gain:+.4f} vs 免费基线)  J={r['jain_bin']:.3f}  "
            f"{r['hw_cost']:,} FF-eq",
            xy=(max(r["hw_cost"], 1), r["bw_vs_ideal"]), xycoords="data",
            xytext=(1.035, 0.985 - i * (0.97 / max(1, len(order) - 1))),
            textcoords="axes fraction", fontsize=7.6,
            color="#777777" if not feas else "#111111", va="center", zorder=4,
            arrowprops=dict(arrowstyle="-", lw=0.5, color="#c8c8c8",
                            shrinkA=0, shrinkB=3))

    fp = [(max(r["hw_cost"], 1), r["bw_vs_ideal"]) for r in frontier(rows)]
    if len(fp) > 1:
        ax.plot([p[0] for p in fp], [p[1] for p in fp], "--", c="#1f6feb",
                lw=1.3, alpha=0.75, label="Pareto 前沿（可实现点）")

    ax.axhline(1.0, color="#d1242f", lw=1.6,
               label=f"理想上限 R* = {d['ideal']['r_fair']:.4f} flit/cycle"
                     "（该流量自己的 LP 解）")
    ax.axhline(s0["bw_vs_ideal"], color="#1a7f37", ls="-.", lw=1.4,
               label=f"免费基线：S0 + 静态在飞上限 32 = "
                     f"{s0['bw_vs_ideal']:.4f}（0 FF-eq）")
    d128 = next(r for r in d["passes"]["128"] if r["name"].startswith("S0"))
    ax.axhline(d128["bw_vs_ideal"], color="#9a6700", ls=":", lw=1.4,
               label=f"原默认基线：S0 + 上限 128 = "
                     f"{d128['bw_vs_ideal']:.4f}")

    ax.set_xscale("log")
    ax.set_xlabel("新增硬件状态（FF 等效，对数轴）→ 更贵"
                  "　　　（S0 = 0，画在 1 处）")
    ax.set_ylabel("总写带宽 / 该流量的理想上限 R*")
    ax.set_title("固定非均匀流量（全部写 → HA 11/13）下的带宽 vs 硬件开销\n"
                 "灰 x = 需要快于 30 拍的总线，不可实现；"
                 "所有旋钮冻结在 uniform 上调好的值，未重新调参",
                 fontsize=11)
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left", fontsize=8.2)
    fig.savefig(OUT_P, dpi=130)
    print(f"wrote {OUT_P}")


def plot_decomp(d: dict) -> None:
    """Split each scheme's bandwidth into the free part and the paid part."""
    a = {r["name"]: r for r in d["passes"]["128"]}
    b = {r["name"]: r for r in d["passes"]["32"]}
    base = a["S0 baseline"]["bw_vs_ideal"]
    free = b["S0 baseline"]["bw_vs_ideal"] - base
    names = sorted(b, key=lambda n: -b[n]["bw_vs_ideal"])
    paid = [b[n]["bw_vs_ideal"] - b["S0 baseline"]["bw_vs_ideal"] for n in names]

    fig, ax = plt.subplots(figsize=(13.0, 7.6))
    fig.subplots_adjust(left=0.30, right=0.80, top=0.865, bottom=0.155)
    ys = list(range(len(names)))
    ax.barh(ys, [free] * len(names), color="#1a7f37", alpha=0.85,
            label=f"上限 128→32 带来的（免费，0 FF-eq）：{free:+.4f}")
    ax.barh(ys, paid, left=free, color="#1f6feb", alpha=0.85,
            label="控制器在正确基线之上再带来的（要付硬件）")
    lo = min(0.0, free + min(paid)) - 0.02
    hi = free + max(paid)
    # One aligned column of labels rather than following each bar end, so the
    # negative bars cannot collide with their own text.
    for i, n in enumerate(names):
        ax.text(hi + 0.012, i, f"{paid[i]:+.4f}   {b[n]['hw_cost']:,} FF-eq",
                va="center", ha="left", fontsize=7.6,
                color="#1a7f37" if paid[i] > 0 else "#555555")
    ax.set_xlim(lo, hi + 0.075)
    ax.set_yticks(ys)
    ax.set_yticklabels([n[:44] for n in names], fontsize=7.8)
    ax.invert_yaxis()
    ax.axvline(0, color="#333", lw=0.9)
    ax.axvline(free, color="#1a7f37", ls=":", lw=1.1)
    ax.set_xlabel("带宽 / R* 相对「原默认基线 S0 + 上限 128」的增量")
    ax.set_title("非均匀流量下的带宽增益分解：\n"
                 "免费的那一段（绿）占绝大部分，付了硬件的那一段（蓝）"
                 "只有 S16 为正", fontsize=11)
    ax.grid(alpha=0.3, axis="x")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.085), ncol=2,
              fontsize=8.4, frameon=False)
    fig.savefig(OUT_D, dpi=130)
    print(f"wrote {OUT_D}")


def plot_skew() -> None:
    """Is any of this specific to full collapse? Sweep the skew and see."""
    src = ROOT / "results" / "probe_ring2_midskew.json"
    if not src.exists():
        print(f"skipping skew plot, no {src}")
        return
    d = json.loads(src.read_text())
    caps = d["sweeps"]["cap"]
    fs = sorted(float(f) for f in caps)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14.2, 6.0))
    fig.subplots_adjust(left=0.06, right=0.985, top=0.855, bottom=0.115,
                        wspace=0.22)

    for cap, mk in ((128, "s"), (64, "^"), (32, "o"), (16, "v")):
        ys = [caps[f"{f}"]["by_cap"][str(cap)] for f in fs]
        a1.plot(fs, ys, marker=mk, lw=1.6 if cap == 32 else 1.1,
                ms=6 if cap == 32 else 4.5,
                color="#1a7f37" if cap == 32 else None,
                label=f"在飞上限 {cap}" + ("（选定值）" if cap == 32 else ""))
    a1.set_xlabel("倾斜度 f（0 = uniform，1 = 全部写入 HA 11/13）")
    a1.set_ylabel("总写带宽 / 该 f 下的理想上限 R*")
    a1.set_title("静态在飞上限：32 在整个倾斜范围上都近最优\n"
                 "而原默认的 128 在每个 f 上都最差", fontsize=10.5)
    a1.grid(alpha=0.3)
    a1.legend(fontsize=8.4, loc="lower left")

    sch = d["sweeps"]["schemes"]
    base = sch["S0 (free baseline)"]
    for name, row in sch.items():
        ys = [row[f"{f}"]["bw_vs_ideal"] - base[f"{f}"]["bw_vs_ideal"]
              for f in fs]
        hot = name.startswith("S16")
        a2.plot(fs, ys, marker="o" if hot else ".", lw=2.0 if hot else 1.0,
                ms=7 if hot else 5, color="#1a7f37" if hot else None,
                zorder=5 if hot else 2,
                label=name + ("（唯一处处不为负）" if hot else ""))
    a2.axhline(0, color="#333", lw=1.2, ls="-.",
               label="免费基线（S0 + 上限 32，0 FF-eq）")
    a2.set_xlabel("倾斜度 f")
    a2.set_ylabel("带宽 / R* 相对免费基线的增量")
    a2.set_title("在正确基线之上，控制器还能挣到多少\n"
                 "最难的区间是中等倾斜 f ≈ 0.25，不是全塌缩", fontsize=10.5)
    a2.grid(alpha=0.3)
    a2.legend(fontsize=7.8, loc="lower left")
    out = ROOT / "results" / "hotbw_skew.png"
    fig.savefig(out, dpi=130)
    print(f"wrote {out}")


def main() -> None:
    _use_cjk_font()
    d = json.loads(SRC.read_text())
    plot_pareto(d)
    plot_decomp(d)
    plot_skew()


if __name__ == "__main__":
    main()
