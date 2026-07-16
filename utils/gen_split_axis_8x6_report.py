#!/usr/bin/env python3
"""HTML report for the split-half axis+CCW allgather DSE on 8x6."""

from __future__ import annotations

import html
import json
from pathlib import Path

import sched_zerobuf_compare as S
from dse_tree_allgather_6x8 import MX, MY, H, V, coord, nid, axis_ccw_tree
from dse_split_axis_8x6 import (
    VMID, HMID, split_vertical, split_horizontal,
)

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "split_axis_8x6.json"
HTML_PATH = ROOT / "results" / "report_split_axis_8x6.html"

LABELS = {
    "axis_ccw": "axis+CCW（整片）",
    "split_vertical_4x6": "split 2×(4×6)（竖切）",
    "split_horizontal_8x3": "split 2×(8×3)（横切）",
}

_CELL, _MARGIN, _R, _TOP = 46, 26, 8, 30
_C_ARM, _C_V, _C_H = "#334155", "#2563eb", "#ea580c"
_C_CROSS, _C_RUN, _C_SRC = "#c026d3", "#16a34a", "#dc2626"


def esc(v) -> str:
    return html.escape(str(v))


def _edge_kind(p, c, s, split):
    """Classify an edge for colouring within a split diagram."""
    sx, sy = coord(s)
    px, py = coord(p)
    cx, cy = coord(c)
    if split == "v":
        p_left, c_left = px < VMID, cx < VMID
        src_left = sx < VMID
        if p_left != c_left:
            return "cross"
        if (px < VMID) != src_left:
            return "run"
    elif split == "h":
        p_bot, c_bot = py < HMID, cy < HMID
        src_bot = sy < HMID
        if p_bot != c_bot:
            return "cross"
        if (py < HMID) != src_bot:
            return "run"
    # in the source half: arm / vertical fill / horizontal fill
    if cx == sx or cy == sy:
        return "arm"
    return "vfill" if px == cx else "hfill"


_COLMAP = {"arm": (_C_ARM, "g"), "vfill": (_C_V, "v"), "hfill": (_C_H, "h"),
           "cross": (_C_CROSS, "x"), "run": (_C_RUN, "r")}


def _svg(edges, s, split=None) -> str:
    sx, sy = coord(s)
    w = _MARGIN * 2 + (MX - 1) * _CELL
    h = _TOP + _MARGIN * 2 + (MY - 1) * _CELL

    def px(x):
        return _MARGIN + x * _CELL

    def py(y):
        return _TOP + _MARGIN + (MY - 1 - y) * _CELL

    defs = "".join(
        f'<marker id="s{cid}" markerWidth="7" markerHeight="7" refX="6" '
        f'refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="{col}"/>'
        f"</marker>"
        for col, cid in _COLMAP.values()
    )
    divider = ""
    if split == "v":
        xln = (px(VMID - 1) + px(VMID)) / 2
        divider = (f'<line x1="{xln:.1f}" y1="{py(MY-1)-16:.0f}" x2="{xln:.1f}" '
                   f'y2="{py(0)+16:.0f}" stroke="#94a3b8" stroke-width="1.5" '
                   f'stroke-dasharray="5 4"/>')
    elif split == "h":
        yln = (py(HMID - 1) + py(HMID)) / 2
        divider = (f'<line x1="{px(0)-16:.0f}" y1="{yln:.1f}" x2="{px(MX-1)+16:.0f}" '
                   f'y2="{yln:.1f}" stroke="#94a3b8" stroke-width="1.5" '
                   f'stroke-dasharray="5 4"/>')
    lines = []
    for p, c in edges:
        kind = _edge_kind(p, c, s, split) if split else (
            "arm" if (coord(c)[0] == sx or coord(c)[1] == sy) else
            ("vfill" if coord(p)[0] == coord(c)[0] else "hfill"))
        col, mid = _COLMAP[kind]
        x1, y1 = px(coord(p)[0]), py(coord(p)[1])
        x2, y2 = px(coord(c)[0]), py(coord(c)[1])
        dx, dy = x2 - x1, y2 - y1
        d = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / d, dy / d
        wdt = 2.6 if kind == "cross" else 2
        lines.append(
            f'<line x1="{x1+ux*(_R+1):.1f}" y1="{y1+uy*(_R+1):.1f}" '
            f'x2="{x2-ux*(_R+4):.1f}" y2="{y2-uy*(_R+4):.1f}" '
            f'stroke="{col}" stroke-width="{wdt}" marker-end="url(#s{mid})"/>'
        )
    nodes = []
    for y in range(MY):
        for x in range(MX):
            src = (x == sx and y == sy)
            nodes.append(
                f'<circle cx="{px(x)}" cy="{py(y)}" r="{_R}" '
                f'fill="{_C_SRC if src else "#fff"}" '
                f'stroke="{_C_SRC if src else "#94a3b8"}" '
                f'stroke-width="{2 if src else 1}"/>'
            )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'xmlns="http://www.w3.org/2000/svg"><defs>{defs}</defs>'
        f'{divider}{"".join(lines)}{"".join(nodes)}</svg>'
    )


def diagrams() -> str:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    panels = [
        ("axis+CCW（整片基线）", "源 (3,2)：十字臂 + 四象限 CCW 填充",
         _svg(axis_ccw_tree(nid(3, 2)), nid(3, 2), None)),
        ("split 2×(4×6) 竖切", "源 (1,2) 在左半：左半 axis+CCW，每行跨界后向右走到头",
         _svg(split_vertical(nid(1, 2)), nid(1, 2), "v")),
        ("split 2×(8×3) 横切", "源 (3,1) 在下半：下半 axis+CCW，每列跨界后向上走到头",
         _svg(split_horizontal(nid(3, 1)), nid(3, 1), "h")),
    ]
    cards = "".join(
        f'<figure class="tc"><figcaption><b>{esc(t)}</b><span>{esc(sub)}</span>'
        f'</figcaption>{svg}</figure>'
        for t, sub, svg in panels
    )
    legend = (
        '<div class="legend">'
        f'<span><i style="background:{_C_ARM}"></i>本半十字臂</span>'
        f'<span><i style="background:{_C_V}"></i>本半垂直填充</span>'
        f'<span><i style="background:{_C_H}"></i>本半水平填充</span>'
        f'<span><i style="background:{_C_CROSS}"></i>跨界多播复制</span>'
        f'<span><i style="background:{_C_RUN}"></i>对半走到头</span>'
        f'<span><i style="background:{_C_SRC};border-radius:50%"></i>源节点</span>'
        '</div>'
    )
    return f'<div class="trees">{cards}</div>{legend}'


def mk_cell(v, lb):
    if v is None:
        return "<td>—</td>"
    return f"<td class='win'>{v}</td>" if v == lb else f"<td>{v}</td>"


def strict_table(data):
    lbs = data["formal_lower_bounds"]
    ms = [str(m) for m in range(1, 6)]
    head = "".join(f"<th>m={m}</th>" for m in ms)
    lbrow = "".join(f"<td>{lbs[m]['T_lb']}</td>" for m in ms)
    rows = [f"<tr><td class='l'>形式化下界 T_LB</td>{lbrow}"
            "<td>—</td><td>—</td><td>—</td><td>—</td></tr>"]
    for name, sc in data["schemes"].items():
        cells = []
        for m in ms:
            mk = sc["messages"][m]["makespan"]
            cells.append(mk_cell(mk, lbs[m]["T_lb"]))
        m1 = sc["messages"]["1"]
        mi = m1["microarchitecture"]
        rows.append(
            f"<tr><td class='l'>{esc(LABELS.get(name, name))}</td>{''.join(cells)}"
            f"<td>{mi['topology_period_max']}</td>"
            f"<td>{m1['routing_lower_bounds']['directed_link_congestion']}</td>"
            f"<td>{mi['crossbar_outputs_peak']}</td>"
            f"<td>{m1['tree']['max_mesh_fanout']}</td></tr>"
        )
    return (
        "<table><thead><tr><th>方案</th>" + head +
        "<th>Pmax</th><th>链路复用</th><th>出口峰值</th><th>mesh 扇出</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        "<p class='note'>makespan 为严格 rb=2（down_cap=2、无突发吸收）刚性打包上界；"
        "微架构列取 m=1。</p>"
    )


def burst_table(data):
    lb = data["model"]["lower_bound_m1"]
    bufs = data["model"]["buffers"]
    head = "".join(f"<th>B={b}</th>" for b in bufs)
    rows = []
    for name, sc in data["schemes"].items():
        sw = sc["burst_sweep"]
        cells = [mk_cell(sw["makespan_by_buffer"][str(b)], lb) for b in bufs]
        minb = sw["min_buffer_for_lb"]
        rows.append(
            f"<tr><td class='l'>{esc(LABELS.get(name, name))}</td>"
            f"<td>{sw['tree_dilation']}</td><td>{'是' if sw['shortest_path'] else '否'}</td>"
            f"{''.join(cells)}<td>{minb if minb is not None else '—'}</td></tr>"
        )
    return (
        "<table><thead><tr><th>方案</th><th>树 dilation</th><th>最短路</th>" +
        head + "<th>达 LB 最小 B</th></tr></thead><tbody>" +
        "".join(rows) + "</tbody></table>"
        "<p class='note'>宽 eject 模型：crossbar→eject 写宽 4/拍、FIFO 深度 B、"
        "PE 排空 2/拍（与 report_burst_sweep_8x6 同模型）。</p>"
    )


def area_table(data):
    rows = []
    for name, sc in data["schemes"].items():
        a = sc["architectures"]
        rows.append(
            f"<tr><td class='l'>{esc(LABELS.get(name, name))}</td>"
            f"<td>{a['sparse_direct']['normalized_total']}</td>"
            f"<td>{a['sparse_direct']['calendar_depth']}</td>"
            f"<td>{a['sparse_direct']['calendar_issue_width']}</td>"
            f"<td>{a['template_direct']['normalized_total']}</td></tr>"
        )
    return (
        "<table><thead><tr><th>方案</th><th>SparseCal 面积*</th>"
        "<th>日历深度</th><th>issue 宽</th><th>Template 面积*</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        "<p class='note'>* 归一化到 IQ-XY=1.0 的 Arch-A5 解析模型；增量项 ±30% 不确定度。</p>"
    )


def main():
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    lb = data["model"]["lower_bound_m1"]
    gen = esc(data["generated_at"])
    sv = data["schemes"]["split_vertical_4x6"]
    sh = data["schemes"]["split_horizontal_8x3"]
    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>8×6 Allgather：半区 axis+CCW + 边界多播梳齿</title>
<style>
:root{{--bg:#f8fafc;--card:#fff;--text:#0f172a;--muted:#64748b;--line:#cbd5e1;--win:#dcfce7;}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
margin:0;padding:28px 32px 64px;line-height:1.55;max-width:1180px}}
h1{{font-size:1.5rem;margin:0 0 4px}} h2{{font-size:1.15rem;color:#1e3a8a;margin:0 0 12px}}
.sub,.note{{color:var(--muted);font-size:.86rem}}
.card{{background:var(--card);border:1px solid #e2e8f0;border-radius:10px;padding:18px 22px;margin:16px 0}}
.hero{{border-color:#93c5fd;background:linear-gradient(180deg,#eff6ff,#fff)}}
table{{border-collapse:collapse;width:100%;font-size:.82rem;margin:8px 0}}
th,td{{border:1px solid var(--line);padding:6px 8px;text-align:center}}
th{{background:#e2e8f0}} td.l{{text-align:left}} td.win{{background:var(--win);font-weight:700}}
ul{{margin:6px 0;padding-left:22px}} li{{margin:6px 0}}
code{{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:.9em}}
.trees{{display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;margin:10px 0}}
.tc{{margin:0;flex:1 1 300px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;
padding:8px 10px 10px;text-align:center}}
.tc figcaption{{margin-bottom:4px}} .tc figcaption b{{color:#1e3a8a}}
.tc figcaption span{{display:block;font-size:.76rem;color:var(--muted)}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;font-size:.8rem;color:var(--muted);margin:6px 2px}}
.legend i{{display:inline-block;width:14px;height:8px;margin-right:5px;vertical-align:middle}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}}
.kpi div{{background:#f1f5f9;border-radius:8px;padding:12px 14px}}
.kpi b{{display:block;font-size:1.3rem;color:#1d4ed8}} .kpi span{{font-size:.78rem;color:var(--muted)}}
</style></head><body>

<h1>8×6 Allgather：半区 axis+CCW + 边界多播梳齿</h1>
<p class="sub">把 8×6 平均切成两半，半区内 axis+CCW，到边界向另一半多播并沿垂直方向走到头 ·
H=7 · V=9 · rb=2 · 数据源 <code>split_axis_8x6.json</code> · 生成 {gen}</p>

<div class="card hero">
<h2>核心结论</h2>
<div class="kpi">
  <div><b>{lb}</b><span>形式化下界 T_LB (m=1)</span></div>
  <div><b>114</b><span>竖切最优 makespan（B≥1）</span></div>
  <div><b>126</b><span>横切最优 makespan（B≥1）</span></div>
  <div><b>13 / 11</b><span>竖切 / 横切 Pmax（基线 15）</span></div>
</div>
<ul>
<li><b>都是合法最短路树</b>（dilation=96），微架构显著更省：竖切 Pmax=13、横切 Pmax=11、
横切出口峰值降到 5、链路复用降到 40。</li>
<li><b>但都达不到下界 96</b>：竖切卡在 <b>114</b>、横切卡在 <b>126</b>。瓶颈从 eject 带宽
转移到<b>边界梳齿的链路串行化</b>——对半的每条“走到头”长链把远端 flit 压到很晚，
多源在同一批 run 链路上必须错峰，突发 buffer 无法缓解（B≥1 后不再下降）。</li>
<li><b>取舍</b>：牺牲 ~19%(竖) / ~31%(横) 时延，换取更浅日历与更低出口带宽。
若首要目标是贴界 96，仍应选整片 axis+CCW；若首要目标是压 Pmax/出口带宽且能接受时延，
竖切是更平衡的一档。</li>
</ul>
</div>

<div class="card">
<h2>1. 方案示意（三图对比）</h2>
<p>半区内是标准 axis+CCW（<span style="color:{_C_ARM}">灰=臂</span>、
<span style="color:{_C_V}">蓝=垂直填充</span>、<span style="color:{_C_H}">橙=水平填充</span>）；
到边界列/行后<span style="color:{_C_CROSS}"><b>紫色跨界多播复制</b></span>到另一半，
再<span style="color:{_C_RUN}"><b>绿色沿边界垂直方向走到头</b></span>，每条边界线喂一把梳齿。</p>
{diagrams()}
<ul>
<li><b>竖切</b>：边界是竖线（x=3|4），垂直于它的方向是水平；左半每行 (3,y) 复制到 (4,y)，
再 (4,y)→(5,y)→(6,y)→(7,y) 走到最右列。右半被 6 把水平梳齿覆盖。</li>
<li><b>横切</b>：边界是横线（y=2|3），垂直于它的方向是竖直；下半每列 (x,2) 复制到 (x,3)，
再 (x,3)→(x,4)→(x,5) 走到最上行。上半被 8 把竖直梳齿覆盖。</li>
<li>两种切分都保证对半每个节点恰好一个父亲、根入度 0、47 条相邻有向边，为合法生成树。</li>
</ul>
</div>

<div class="card">
<h2>2. Makespan（严格 rb=2，m=1..5）与微架构代价</h2>
{strict_table(data)}
<ul>
<li>竖切与整片基线严格 makespan 打平（m=1 均 121），但 Pmax 从 15 降到 <b>13</b>。</li>
<li>横切 makespan 略高（127），但 Pmax 最低（<b>11</b>）、出口峰值最低（<b>5</b>）、链路复用最低（<b>40</b>）。</li>
</ul>
</div>

<div class="card">
<h2>3. 宽 eject 突发 Buffer 扫描（m=1，B∈{{0,1,2,4,8,11}}）</h2>
{burst_table(data)}
<ul>
<li>只有整片 axis+CCW 能在 B≥2 达到 <b>96</b>；两种切分对 B 几乎不敏感
（竖切 B=0→120、B≥1→114；横切 B≥1→126），说明其瓶颈是链路而非 eject。</li>
<li>竖切最短路 dilation 仍是 96，但结构性链路串行使实测停在 114——
“最短路”是达界的必要条件而非充分条件。</li>
</ul>
</div>

<div class="card">
<h2>4. 面积对比（Arch-A5 解析模型）</h2>
{area_table(data)}
<ul>
<li>三者 mesh 扇出同为 4、multicast 走同一份 CalFork 固定面积，主要差异在<b>日历深度</b>随 Pmax 变化。</li>
<li>切分方案的更浅日历（Pmax 13/11 vs 15）带来的 SparseCal 面积收益有限但方向正确。</li>
</ul>
</div>

<div class="card">
<h2>5. 建议</h2>
<ul>
<li><b>目标贴下界 96</b>：维持整片 axis+CCW（Pmax=15、出口 8、FIFO≥2）。切分无法达界。</li>
<li><b>目标压时隙表/出口带宽、可接受 &lt;20% 时延损失</b>：选竖切 2×(4×6)
（makespan 114、Pmax 13、出口 6），微架构更友好。</li>
<li><b>最激进省资源</b>：横切 2×(8×3)（Pmax 11、出口 5、链路复用 40），代价是 makespan 126（+31%）。</li>
<li>后续可探索：给对半的“走到头”长链做<b>分段多点注入</b>或<b>四分区（2×2）</b>以缩短 run 链，
有望在保持低 Pmax 的同时把 makespan 拉回接近界。</li>
</ul>
</div>

<p class="note">生成脚本：<code>utils/gen_split_axis_8x6_report.py</code> ·
排图/扫描：<code>utils/dse_split_axis_8x6.py</code>（复用
<code>dse_tree_allgather_6x8.py</code>、<code>dse_burst_sweep_8x6.py</code>）</p>
</body></html>"""
    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
