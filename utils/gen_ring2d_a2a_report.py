#!/usr/bin/env python3
"""Concise 2D-ring report: all-to-all makespan and the area Pareto.

Same workload as results/report_islip2d_8x6.html section 7: one all-to-all
of 2,256 flows on the dimension-sliced ring. Makespan is read from
results/islip2d_8x6.json. Area is the arbiter state from rg_sched_cost,
free_at versus interval. Path mode, fill order and port count do not change
that state, so they share an x-coordinate.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utils"))

from rg_ring_topo import RingTopology
from rg_sched_cost import distributed_cost, pareto_front, sched_cost

BATCH = ROOT / "results" / "islip2d_8x6.json"
OUT = ROOT / "results" / "report_ring2d_a2a.html"


def _label(r: dict) -> str:
    mode = r.get("path_mode") or "fixed"
    g = r.get("grants_per_src")
    b, lv = r.get("board_ports"), r.get("leave_ports")
    spatial = r.get("spatial_reuse") or "arc"
    domain = r.get("conflict_domain") or "free_at"
    fill = r.get("fill") or "arc_desc"
    bits = [mode, f"g={g}"]
    if not (b == 1 and lv == 1):
        bits.append(f"board={b}/leave={lv}")
    if spatial != "arc":
        bits.append(spatial)
    if domain != "free_at":
        bits.append(domain)
    if fill != "arc_desc":
        bits.append(f"fill={fill}")
    return " · ".join(bits)


def _key(r: dict) -> tuple:
    return (
        r.get("path_mode") or "fixed",
        r.get("grants_per_src"),
        r.get("board_ports"),
        r.get("leave_ports"),
        r.get("spatial_reuse") or "arc",
        r.get("conflict_domain") or "free_at",
        r.get("fill") or "arc_desc",
    )


def load_rows() -> list[dict]:
    with open(BATCH, encoding="utf-8") as f:
        doc = json.load(f)
    seen = set()
    out = []
    for r in doc["rows"]:
        if (r.get("fabric") != "ring" or r.get("pattern") != "alltoall"
                or r.get("m") != 1 or r.get("sigma") != 1):
            continue
        k = _key(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def main() -> None:
    rows = load_rows()
    topo = RingTopology()
    cost = {
        dom: sched_cost("islip2d_ring", topo, 2256, iters=1, n_rounds=69,
                        conflict_domain=dom)
        for dom in ("free_at", "interval")
    }
    base_bits = distributed_cost("ring_base")["bits"]
    pts = []
    for r in rows:
        dom = r.get("conflict_domain") or "free_at"
        c = cost[dom]
        pts.append({
            "name": _label(r),
            "makespan": r["makespan"],
            "data_span": r["data_span"],
            "rounds": r["n_rounds"],
            "lb": r["round_lb"],
            "ratio": r["round_ratio"],
            "hot": r["max_link_load"],
            "bits": c["bits"],
            "domain": dom,
            "t_sched": c["t_sched_cycles"],
        })
    pts.sort(key=lambda p: (p["makespan"], p["bits"], p["name"]))
    front = pareto_front([(p["bits"], p["makespan"], p["name"]) for p in pts])
    front_names = {name for _, _, name in front}

    def svg() -> str:
        W, H = 880, 460
        x0, y0, pw, ph = 78, 36, 760, 340
        xs = [p["bits"] for p in pts]
        ys = [p["makespan"] for p in pts]
        xmin, xmax = min(xs) / 1.4, max(xs) * 1.35
        ymin, ymax = min(ys) / 1.35, max(ys) * 1.25

        def X(v: float) -> float:
            return x0 + (math.log10(v) - math.log10(xmin)) / (
                math.log10(xmax) - math.log10(xmin)) * pw

        def Y(v: float) -> float:
            return y0 + ph - (math.log10(v) - math.log10(ymin)) / (
                math.log10(ymax) - math.log10(ymin)) * ph

        body = [
            f'<svg viewBox="0 0 {W} {H}" width="100%" '
            f'preserveAspectRatio="xMidYMid meet" role="img">',
            f'<rect class="plot" x="{x0}" y="{y0}" width="{pw}" height="{ph}"/>',
        ]
        for g in (200, 500, 1000, 2000, 5000):
            if not (ymin <= g <= ymax):
                continue
            yy = Y(g)
            body.append(f'<line class="gl" x1="{x0}" y1="{yy:.1f}" '
                        f'x2="{x0+pw}" y2="{yy:.1f}"/>')
            body.append(f'<text class="tick" x="{x0-8}" y="{yy+4:.1f}" '
                        f'text-anchor="end">{g:g}</text>')
        for p10 in range(math.ceil(math.log10(xmin)),
                         math.floor(math.log10(xmax)) + 1):
            xx = X(10 ** p10)
            body.append(f'<text class="tick" x="{xx:.1f}" y="{y0+ph+18}" '
                        f'text-anchor="middle">{10**p10/1000:.0f}k</text>')
        ordered = sorted(front, key=lambda t: t[0])
        if len(ordered) >= 2:
            d = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b, _ in ordered)
            body.append(f'<polyline class="cv cB" points="{d}" fill="none"/>')
        # Every free_at scheme has the same state, so they share one x.
        # Log makespan packs 1.5k–2.1k into a few pixels; a short tick per
        # scheme keeps the column honest instead of pretending they differ
        # in area.
        for p in pts:
            on = p["name"] in front_names
            cx, cy = X(p["bits"]), Y(p["makespan"])
            if p["domain"] == "interval":
                col = "#6ee7a8"
            elif "whole_ring" in p["name"]:
                col = "#f0a0a0"
            else:
                col = "#7eb6ff"
            if not on:
                body.append(
                    f'<line x1="{cx-9:.1f}" y1="{cy:.1f}" x2="{cx+9:.1f}" '
                    f'y2="{cy:.1f}" stroke="{col}" stroke-width="2" '
                    f'opacity="0.85"/>')
            body.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{"6" if on else "3.2"}" '
                f'fill="{col}"/>')
        free = [p for p in pts if p["domain"] == "free_at"
                and "whole_ring" not in p["name"]]
        lo = min(free, key=lambda p: p["makespan"])
        rest = [p for p in free if p["name"] != lo["name"]]
        hi = max(rest, key=lambda p: p["makespan"])
        nxt = min(rest, key=lambda p: p["makespan"])
        fx = X(lo["bits"])
        body.append(
            f'<text class="tick" x="{fx+14:.1f}" y="{Y(lo["makespan"])+18:.1f}" '
            f'fill="#e8ecf4">{lo["name"]}  {lo["makespan"]:,}</text>')
        body.append(
            f'<text class="tick" x="{fx+14:.1f}" y="{Y(hi["makespan"])-10:.1f}" '
            f'fill="#9aa3b5">其余 free_at  {nxt["makespan"]:,}–{hi["makespan"]:,}'
            f'，同一列</text>')
        whole = next(p for p in pts if "whole_ring" in p["name"])
        body.append(
            f'<text class="tick" x="{X(whole["bits"])+14:.1f}" '
            f'y="{Y(whole["makespan"])-6:.1f}" fill="#f0a0a0">'
            f'{whole["name"]}  {whole["makespan"]:,}</text>')
        iv = next(p for p in pts if p["domain"] == "interval")
        body.append(
            f'<text class="tick" x="{X(iv["bits"])-12:.1f}" '
            f'y="{Y(iv["makespan"])-8:.1f}" text-anchor="end" fill="#6ee7a8">'
            f'{iv["name"]}  {iv["makespan"]:,}</text>')
        body.append(
            f'<text class="axl" x="{x0+pw/2}" y="{y0+ph+40}" '
            f'text-anchor="middle">仲裁器状态（bit，对数）</text>')
        body.append(
            f'<text class="axl" x="18" y="{y0+ph/2}" text-anchor="middle" '
            f'transform="rotate(-90 18 {y0+ph/2})">makespan（拍，对数）</text>')
        body.append("</svg>")
        return "".join(body)

    body_rows = []
    for p in pts:
        on = p["name"] in front_names
        cls = ' class="hl"' if on else ""
        mk = (f"<b class='win'>{p['makespan']:,}</b>" if on
              else f"{p['makespan']:,}")
        body_rows.append(
            f"<tr{cls}><td>{p['name']}</td><td>{p['rounds']}</td>"
            f"<td>{p['lb']}</td><td>{p['ratio']:.3f}</td>"
            f"<td>{p['data_span']:,}</td><td>{mk}</td>"
            f"<td>{p['hot']}</td><td>{p['bits']:,}</td></tr>"
        )
    best = pts[0]
    iv = next(p for p in pts if p["domain"] == "interval")
    free_best = min((p for p in pts if p["domain"] == "free_at"),
                    key=lambda p: p["makespan"])
    whole = next(p for p in pts if "whole_ring" in p["name"])
    front_txt = "、".join(
        f"{name}（{mk:,} 拍 / {bits:,} bit）"
        for bits, mk, name in sorted(front, key=lambda t: t[0]))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>2D 环 · all-to-all makespan 与面积</title>
<style>
body {{ font-family: "Segoe UI", "Noto Sans SC", system-ui, sans-serif;
       margin: 0; background: #0b1020; color: #e8ecf4; line-height: 1.62; }}
.wrap {{ max-width: 980px; margin: 0 auto; padding: 28px 34px 72px; }}
h1 {{ font-size: 1.5rem; color: #f0f4ff; border-bottom: 1px solid #2a3555;
     padding-bottom: .45rem; }}
h2 {{ margin-top: 1.8rem; font-size: 1.15rem; color: #f0f4ff;
     border-left: 4px solid #7eb6ff; padding-left: .55rem; }}
p {{ margin: .55rem 0; }}
.muted {{ color: #9aa3b5; font-size: .85rem; }}
.pill {{ display: inline-block; background: #1a2340; border: 1px solid #2a3555;
        border-radius: 999px; padding: 2px 11px; font-size: .78rem;
        margin: 0 6px 6px 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: .86rem;
        margin: .7rem 0 1.1rem; }}
th, td {{ border: 1px solid #2a3555; padding: 6px 9px; text-align: left; }}
th {{ background: #1a2340; }}
tr:nth-child(even) {{ background: #12192c; }}
tr.hl td {{ background: #17243d; }}
.win {{ color: #6ee7a8; font-weight: 600; }}
.fig {{ background: #0e1425; border: 1px solid #2a3555; border-radius: 10px;
       padding: 14px 12px 8px; margin: 1rem 0; }}
.fig .cap {{ color: #9aa3b5; font-size: .82rem; margin-top: .45rem; }}
.note {{ background: #141b2f; border-left: 3px solid #d9a03c;
        border-radius: 0 8px 8px 0; padding: 10px 15px; margin: .8rem 0;
        font-size: .9rem; }}
svg text {{ font-family: "Segoe UI","Noto Sans SC",system-ui,sans-serif; }}
.plot {{ fill: #0b142a; stroke: #2a3555; }}
.gl {{ stroke: #1f2a47; }}
.tick {{ fill: #8b95ab; font-size: 11px; }}
.axl {{ fill: #9aa3b5; font-size: 12px; }}
.cv {{ fill: none; stroke-width: 2.4; }}
.cB {{ stroke: #6ee7a8; }}
code {{ background: #1a2340; padding: 1px 5px; border-radius: 4px; }}
</style>
</head>
<body><div class="wrap">
<h1>2D 环：同一 all-to-all 的 makespan 与面积</h1>
<p class="muted">只保留维度切片 2D 环。mesh 不在这页。
数字来自 <code>results/islip2d_8x6.json</code> 的 ring · alltoall · m=1 · σ=1，
面积来自 <code>utils/rg_sched_cost.py</code> 的仲裁器状态。</p>
<p>
<span class="pill">48 节点 · 6 行环 × 8 + 8 列环 × 6</span>
<span class="pill">192 条有向链路</span>
<span class="pill">2,256 条 all-to-all 流</span>
<span class="pill">H=7 / V=9</span>
</p>

<h2>1 · makespan</h2>
<p>每个方案把同一批 2,256 条流排完。makespan 是最后一条流结束的拍，
比 data_span 多 16 拍，那是一趟请求-授权时延。
轮数下界是最热链路负载；<code>whole_ring</code> 把下界从 60 抬到 192，
因为整环互斥、不能空分复用。</p>
<p>最短的是 <b class="win">{best['name']}</b>，{best['makespan']:,} 拍。
同面积里最短的 free_at 方案是 {free_best['name']}，{free_best['makespan']:,} 拍。
<code>whole_ring</code> 是 {whole['makespan']:,} 拍，最慢。</p>
<table>
<thead><tr><th>方案</th><th>轮数</th><th>下界</th><th>轮数/下界</th>
<th>data_span</th><th>makespan</th><th>最热链路</th><th>仲裁器 bit</th></tr></thead>
<tbody>
{''.join(body_rows)}
</tbody></table>
<p class="muted">高亮行是面积–makespan 前沿。路径模式、补齐序、上/下环口数不改仲裁器状态，
所以 free_at 各行的 bit 都是 {cost['free_at']['bits']:,}。
interval 多一张区间表，{cost['interval']['bits']:,} bit，其中区间表
{cost['interval']['bits_breakdown']['interval_tables']:,} bit。
环基线的分布式存储是 {base_bits:,} bit，那一套没有这批 all-to-all 的 makespan，不进表。</p>

<h2>2 · 面积 / makespan Pareto</h2>
<p>两轴都是越小越好：面积向左，makespan 向下。绿线连非支配点。
蓝点共用一块 free_at 仲裁器，所以叠在同一条竖线上；绿点是 interval，红点是 whole_ring。</p>
<div class="fig">{svg()}
<div class="cap">前沿：{front_txt}。
interval 用 {iv['bits']/free_best['bits']:.1f}× 的仲裁器状态，
把 makespan 从 {free_best['makespan']:,} 收到 {iv['makespan']:,}
（{free_best['makespan']/iv['makespan']:.1f}×）。
其余 free_at 方案和 whole_ring 都在这条前沿内侧。</div></div>
<div class="note">
批量调度按完全流水计，表里的 makespan 没有再加 T_sched。
free_at 的 T_sched 是 {cost['free_at']['t_sched_cycles']} 拍，
interval 是 {cost['interval']['t_sched_cycles']} 拍。
即便把 interval 的 {cost['interval']['t_sched_cycles']} 拍一次加上，
合计 {iv['makespan'] + iv['t_sched']:,} 拍仍然短于 free_at 的 {free_best['makespan']:,}。
</div>
<p class="muted">重新生成：<code>python3 utils/gen_ring2d_a2a_report.py</code></p>
</div></body></html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  schemes={len(pts)}  "
          f"front={len(front)}")


if __name__ == "__main__":
    main()
