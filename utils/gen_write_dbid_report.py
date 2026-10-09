#!/usr/bin/env python3
"""Figures + number tables for the HA-DBID distributed request-grant study.

Reads results/write_dbid_8x6.json and writes, with no plotting dependency:

  results/write_dbid_goodput_mesh.svg   goodput vs injection rate, 3 schemes
  results/write_dbid_goodput_ring.svg   same, ring
  results/write_dbid_latency_{mesh,ring}.svg   p50 latency vs injection rate
  results/write_dbid_k_sweep.svg        goodput vs DBID pool size K
  results/write_dbid_tables.md          every table quoted in the report

Hand-rolled SVG, same approach as gen_islip2d_report.py.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "write_dbid_8x6.json"
OUT = ROOT / "results"

COL = {"base": "#6b7280", "islip2d": "#e07a1f", "dbid": "#1f9d55",
       "control": "#8fd1a8"}
NAME = {"base": "基线", "islip2d": "类 iSLIP 集中仲裁", "dbid": "分布式 HA-DBID 仲裁",
        "control": "分布式 K=∞（对照）"}
FAB = {"mesh": "2D mesh", "ring": "2D ring"}


def load() -> dict[str, Any]:
    return json.loads(SRC.read_text())


def series(rows, cfg, group="main", **match):
    rs = [r for r in rows if r["config"] == cfg and r["group"] == group
          and all(r.get(k) == v for k, v in match.items())]
    return sorted(rs, key=lambda r: r["lam"])


def lam_star(rs, key="stable_gp"):
    ok = [r["lam"] for r in rs if r[key]]
    return max(ok) if ok else None


# ---------------------------------------------------------------------------
# SVG line chart
# ---------------------------------------------------------------------------

def chart(title, xlabel, ylabel, curves, *, xmax, ymax, hlines=(), diag=False,
          w=760, h=470, ylog=False, ymin=0.0, xticks=None, yticks=None,
          xlabels=None):
    """curves: [(label, color, [(x, y, solid)], dashed)]"""
    L, R, T, B = 66, 18, 40, 58
    pw, ph = w - L - R, h - T - B

    def px(x):
        return L + pw * x / xmax

    def py(y):
        if ylog:
            y = max(y, ymin)
            return T + ph * (1 - (math.log10(y) - math.log10(ymin))
                             / (math.log10(ymax) - math.log10(ymin)))
        return T + ph * (1 - (y - ymin) / (ymax - ymin))

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
         f'width="{w}" height="{h}" font-family="sans-serif" font-size="12">',
         f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
         f'<text x="{w/2}" y="22" text-anchor="middle" font-size="15" '
         f'font-weight="bold">{title}</text>']
    xt = xticks or [i / 10 * xmax for i in range(0, 11, 2)]
    for x in xt:
        o.append(f'<line x1="{px(x)}" y1="{T}" x2="{px(x)}" y2="{T+ph}" '
                 f'stroke="#e5e7eb"/>')
        o.append(f'<text x="{px(x)}" y="{T+ph+16}" text-anchor="middle">'
                 f'{(xlabels or {}).get(x, f"{x:g}")}</text>')
    yt = yticks or [ymin + (ymax - ymin) * i / 5 for i in range(6)]
    for y in yt:
        o.append(f'<line x1="{L}" y1="{py(y)}" x2="{L+pw}" y2="{py(y)}" '
                 f'stroke="#e5e7eb"/>')
        o.append(f'<text x="{L-6}" y="{py(y)+4}" text-anchor="end">'
                 f'{y:g}</text>')
    o.append(f'<rect x="{L}" y="{T}" width="{pw}" height="{ph}" fill="none" '
             f'stroke="#374151"/>')
    o.append(f'<text x="{L+pw/2}" y="{h-12}" text-anchor="middle">{xlabel}'
             f'</text>')
    o.append(f'<text transform="translate(16,{T+ph/2}) rotate(-90)" '
             f'text-anchor="middle">{ylabel}</text>')
    if diag:
        o.append(f'<line x1="{px(0)}" y1="{py(0)}" x2="{px(min(xmax, ymax))}" '
                 f'y2="{py(min(xmax, ymax))}" stroke="#9ca3af" '
                 f'stroke-dasharray="2 4"/>')
    for y, lab, c in hlines:
        o.append(f'<line x1="{L}" y1="{py(y)}" x2="{L+pw}" y2="{py(y)}" '
                 f'stroke="{c}" stroke-dasharray="6 4"/>')
        o.append(f'<text x="{L+pw-4}" y="{py(y)-4}" text-anchor="end" '
                 f'fill="{c}">{lab}</text>')
    for label, color, pts, dashed in curves:
        pts = [p for p in pts if p[1] is not None]
        if not pts:
            continue
        d = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y, _ in pts)
        da = ' stroke-dasharray="5 3"' if dashed else ""
        o.append(f'<polyline points="{d}" fill="none" stroke="{color}" '
                 f'stroke-width="2"{da}/>')
        for x, y, solid in pts:
            fill = color if solid else "#ffffff"
            o.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3.6" '
                     f'fill="{fill}" stroke="{color}" stroke-width="1.6"/>')
    # legend
    lx, ly = L + 12, T + 14
    for i, (label, color, _p, dashed) in enumerate(curves):
        y = ly + 18 * i
        da = ' stroke-dasharray="5 3"' if dashed else ""
        o.append(f'<line x1="{lx}" y1="{y}" x2="{lx+26}" y2="{y}" '
                 f'stroke="{color}" stroke-width="2"{da}/>')
        o.append(f'<text x="{lx+32}" y="{y+4}">{label}</text>')
    o.append('<text x="{}" y="{}" font-size="11" fill="#6b7280">'
             '实心=稳定  空心=队列发散</text>'.format(L + pw - 150, T + ph - 8))
    o.append("</svg>")
    return "\n".join(o)


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def fig_goodput(d, fabric):
    rows = d["rows"]
    curves = []
    for sch in ("base", "islip2d", "dbid"):
        rs = series(rows, f"{fabric}_{sch}")
        lab = NAME[sch]
        if sch == "dbid":
            lab += f"（K={d['k_star'][fabric]}）"
        curves.append((lab, COL[sch],
                       [(r["lam"], r["goodput"], r["stable_gp"]) for r in rs],
                       False))
    rs = series(rows, f"{fabric}_dbid", group="control")
    curves.append((NAME["control"], COL["control"],
                   [(r["lam"], r["goodput"], r["stable_gp"]) for r in rs],
                   True))
    anc = d["anchors"]["mesh_xy" if fabric == "mesh" else "ring_fixed"]
    return chart(f"{FAB[fabric]}：goodput 与注入率（8×6，alltoall 写，m=1）",
                 "注入率 λ（包 / 节点 / 周期）",
                 "goodput（flit / 节点 / 周期）", curves, xmax=1.0, ymax=1.0,
                 hlines=[(anc, f"解析上限 {anc:.3f}", "#b91c1c")], diag=True)


def fig_latency(d, fabric):
    rows = d["rows"]
    curves = []
    for sch in ("base", "islip2d", "dbid"):
        rs = series(rows, f"{fabric}_{sch}")
        curves.append((NAME[sch], COL[sch],
                       [(r["lam"], r["p50"], r["stable_gp"]) for r in rs],
                       False))
    return chart(f"{FAB[fabric]}：p50 延迟与注入率（含 REQ→DBID 往返）",
                 "注入率 λ（包 / 节点 / 周期）", "p50 延迟（周期，对数轴）",
                 curves, xmax=1.0, ymax=10000, ymin=10, ylog=True,
                 yticks=[10, 30, 100, 300, 1000, 3000, 10000])


def fig_ksweep(d):
    rows = [r for r in d["rows"] if r["group"] == "ksweep"]
    ks = [k for k in d["meta"]["k_grid"] if k]
    kx = {k: i for i, k in enumerate(ks + [0])}
    panels = []
    palette = ["#2563eb", "#7c3aed", "#db2777", "#e07a1f", "#1f9d55",
               "#6b7280"]
    for fabric in ("mesh", "ring"):
        curves = []
        for i, lam in enumerate((0.3, 0.4, 0.5, 0.6, 0.8, 1.0)):
            pts = []
            for k in ks + [0]:
                m = [r for r in rows if r["config"] == f"{fabric}_dbid"
                     and r["lam"] == lam and r["dbid_k"] == k]
                if m:
                    pts.append((kx[k], m[0]["goodput"], m[0]["stable_gp"]))
            curves.append((f"λ={lam:g}", palette[i], pts, False))
        base = series(d["rows"], f"{fabric}_base")
        svg = chart(f"{FAB[fabric]}：goodput 与 HA DBID 池大小 K",
                    "K（每个 HA 的在途写数；最右 = 不限）", "goodput（flit/节点/周期）",
                    curves, xmax=len(ks), ymax=0.8 if fabric == "ring" else 0.6,
                    xticks=list(range(len(ks) + 1)), w=640, h=440,
                    xlabels={i: ('∞' if k == 0 else str(k)) for k, i in kx.items()})
        panels.append(svg)
    return panels


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def tables(d) -> str:
    rows = d["rows"]
    o = []
    anc = d["anchors"]
    for fabric in ("mesh", "ring"):
        a = anc["mesh_xy" if fabric == "mesh" else "ring_fixed"]
        o.append(f"### {FAB[fabric]}：三方案饱和点与过载行为\n")
        o.append("| 方案 | λ\\*（goodput 判据） | λ\\*（accepted 判据） | 峰值 goodput | "
                 "λ=1.0 goodput | 回落 | p50@λ\\* | p99@λ\\* |")
        o.append("|---|---|---|---|---|---|---|---|")
        for sch in ("base", "islip2d", "dbid"):
            rs = series(rows, f"{fabric}_{sch}")
            ls = lam_star(rs)
            ls2 = lam_star(rs, "stable")
            pk = max(r["goodput"] for r in rs)
            end = [r for r in rs if r["lam"] == 1.0][0]["goodput"]
            at = [r for r in rs if r["lam"] == ls]
            p50 = f"{at[0]['p50']:.0f}" if at else "—"
            p99 = f"{at[0]['p99']:.0f}" if at else "—"
            nm = NAME[sch] + (f"（K={d['k_star'][fabric]}）"
                              if sch == "dbid" else "")
            o.append(f"| {nm} | {ls} | {ls2} | {pk:.3f} | {end:.3f} | "
                     f"{(end / pk - 1) * 100:+.0f}% | {p50} | {p99} |")
        o.append(f"\n解析上限 λ\\* ≤ {a:.3f}。\n")

        o.append(f"### {FAB[fabric]}：主曲线数据（goodput / p50）\n")
        o.append("| λ | 基线 | 集中式 | 分布式 | 分布式 K=∞ | "
                 "基线 p50 | 集中式 p50 | 分布式 p50 |")
        o.append("|---|---|---|---|---|---|---|---|")
        bs = {r["lam"]: r for r in series(rows, f"{fabric}_base")}
        cs = {r["lam"]: r for r in series(rows, f"{fabric}_islip2d")}
        ds = {r["lam"]: r for r in series(rows, f"{fabric}_dbid")}
        ks = {r["lam"]: r for r in series(rows, f"{fabric}_dbid",
                                          group="control")}

        def g(r):
            return f"{r['goodput']:.4f}{'' if r['stable_gp'] else ' ✗'}"
        for lam in sorted(bs):
            o.append(f"| {lam:g} | {g(bs[lam])} | {g(cs[lam])} | {g(ds[lam])} "
                     f"| {g(ks[lam])} | {bs[lam]['p50']:.0f} | "
                     f"{cs[lam]['p50']:.0f} | {ds[lam]['p50']:.0f} |")
        o.append("\n✗ = 队列发散。\n")

        o.append(f"### {FAB[fabric]}：K 扫描（goodput，flit/节点/周期）\n")
        grid = [k for k in d["meta"]["k_grid"]]
        o.append("| K | " + " | ".join(f"λ={x:g}" for x in
                                       (0.3, 0.4, 0.5, 0.6, 0.8, 1.0))
                 + " | 均值 | HA 在途峰值 |")
        o.append("|---|" + "---|" * 8)
        for k in grid:
            vals, outm = [], 0
            for lam in (0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
                m = [r for r in rows if r["group"] == "ksweep"
                     and r["config"] == f"{fabric}_dbid" and r["lam"] == lam
                     and r["dbid_k"] == k]
                vals.append(m[0]["goodput"])
                outm = max(outm, m[0]["ha_out_max"])
            o.append(f"| {'∞' if k == 0 else k} | "
                     + " | ".join(f"{v:.4f}" for v in vals)
                     + f" | {sum(vals) / len(vals):.4f} | {outm} |")
        o.append("")

        o.append(f"### {FAB[fabric]}：策略 / 限速 / 控制时延 / 集中式 T_rtt\n")
        o.append("| 变体 | λ=0.4 | λ=0.6 | λ=1.0 |")
        o.append("|---|---|---|---|")

        def row(label, grp, cfg, **kw):
            v = []
            for lam in (0.4, 0.6, 1.0):
                m = [r for r in rows if r["group"] == grp
                     and r["config"] == cfg and r["lam"] == lam
                     and all(r.get(k) == x for k, x in kw.items())]
                v.append(f"{m[0]['goodput']:.4f}" if m else "—")
            o.append(f"| {label} | " + " | ".join(v) + " |")
        dc = f"{fabric}_dbid"
        row("分布式 least_served", "policy", dc, dbid_policy="least_served")
        row("分布式 round_robin", "policy", dc, dbid_policy="round_robin")
        row("分布式 pace=2.0", "pace", dc, dbid_pace=2.0)
        row("分布式 pace=2.5", "pace", dc, dbid_pace=2.5)
        row("分布式 控制时延减半", "halflat", dc, ctrl_lat_mode="half")
        ic = f"{fabric}_islip2d"
        row("集中式 T_rtt=16", "rtt", ic, t_rtt=16)
        row("集中式 T_rtt=64", "rtt", ic, t_rtt=64)
        o.append("")

        o.append(f"### {FAB[fabric]}：m=4（goodput flit/节点/周期）\n")
        o.append("| λ（包） | 基线 | 集中式 | 分布式 |")
        o.append("|---|---|---|---|")
        m4 = {sch: {r["lam"]: r for r in series(rows, f"{fabric}_{sch}",
                                                group="m4")}
              for sch in ("base", "islip2d", "dbid")}
        for lam in sorted(m4["base"]):
            o.append(f"| {lam:g} | " + " | ".join(
                f"{m4[s][lam]['goodput']:.4f}"
                f"{'' if m4[s][lam]['stable_gp'] else ' ✗'}"
                for s in ("base", "islip2d", "dbid")) + " |")
        o.append("")
    return "\n".join(o)


def main():
    d = load()
    for f in ("mesh", "ring"):
        (OUT / f"write_dbid_goodput_{f}.svg").write_text(fig_goodput(d, f))
        (OUT / f"write_dbid_latency_{f}.svg").write_text(fig_latency(d, f))
    pm, pr = fig_ksweep(d)
    (OUT / "write_dbid_k_sweep_mesh.svg").write_text(pm)
    (OUT / "write_dbid_k_sweep_ring.svg").write_text(pr)
    (OUT / "write_dbid_tables.md").write_text(tables(d))
    # PNG copies for viewers that do not render SVG (optional, needs rsvg-convert)
    import shutil
    import subprocess
    if shutil.which("rsvg-convert"):
        for svg in OUT.glob("write_dbid_*.svg"):
            subprocess.run(["rsvg-convert", "-b", "white", str(svg), "-o",
                            str(svg.with_suffix(".png"))], check=True)
    print("wrote figures + tables to", OUT)


if __name__ == "__main__":
    main()
