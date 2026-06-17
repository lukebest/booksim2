#!/usr/bin/env python3
"""Render the 16x16 zero-buffer allgather comparison report from cached JSON."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "zerobuf_16x16.json"
HTML_PATH = ROOT / "results" / "report_16x16.html"

SCHEME_LABEL = {
    "multitree": "dimensional multi-tree",
    "ring_uni": "纯 Hamilton 环 (单向)",
    "ring_bi": "纯 Hamilton 环 (双向)",
    "hybrid_uni": "hybrid 局部环+全局树 (单向局部环)",
    "hybrid_bi": "hybrid 局部环+全局树 (双向局部环)",
    "quad_uni": "quad 4×(8×8)环 + 中心交换 (单向)",
    "quad_bi": "quad 4×(8×8)环 + 中心交换 (双向)",
}


def best_hybrid(d, key):
    items = [(int(B), v["makespan"]) for B, v in d[key].items()]
    B, mk = min(items, key=lambda t: t[1])
    return B, mk


def bar_chart(title, labels, values, lb=None):
    width = max(560, 90 * len(labels))
    height = 300
    margin = 54
    plot_h = height - 2 * margin
    ymax = max(values) * 1.12
    bw = (width - 2 * margin) / len(labels)
    p = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
         f'<text x="{margin}" y="22" font-size="14" font-weight="bold">{html.escape(title)}</text>',
         f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#64748b"/>',
         f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#64748b"/>']
    if lb:
        ly = height - margin - (lb / ymax) * plot_h
        p.append(f'<line x1="{margin}" y1="{ly:.1f}" x2="{width-margin}" y2="{ly:.1f}" stroke="#dc2626" stroke-dasharray="5 4"/>')
        p.append(f'<text x="{width-margin-2:.0f}" y="{ly-4:.1f}" font-size="10" fill="#dc2626" text-anchor="end">eject LB={lb}</text>')
    palette = ["#94a3b8", "#fca5a5", "#f87171", "#34d399", "#10b981"]
    for i, (lab, val) in enumerate(zip(labels, values)):
        bh = (val / ymax) * plot_h
        x = margin + i * bw + bw * 0.14
        y = height - margin - bh
        p.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.72:.1f}" height="{bh:.1f}" fill="{palette[i%len(palette)]}"/>')
        p.append(f'<text x="{x+bw*0.36:.1f}" y="{y-5:.1f}" font-size="11" font-weight="bold" text-anchor="middle">{val}</text>')
        for j, line in enumerate(lab.split("\n")):
            p.append(f'<text x="{x+bw*0.36:.1f}" y="{height-margin+15+12*j:.1f}" font-size="10" text-anchor="middle">{html.escape(line)}</text>')
    p.append("</svg>")
    return "\n".join(p)


DIA_PAL = ["#2563eb", "#16a34a", "#d97706", "#9333ea"]
BAND_BG = ["#eff6ff", "#f0fdf4", "#fff7ed", "#faf5ff"]


def _dia_defs():
    m = []
    for name, col in (("r", "#dc2626"), ("g", "#15803d"), ("o", "#ea580c")):
        m.append(f'<marker id="ah-{name}" markerWidth="8" markerHeight="8" refX="5.5" refY="2.5" '
                 f'orient="auto"><path d="M0,0 L5.5,2.5 L0,5 z" fill="{col}"/></marker>')
    return "<defs>" + "".join(m) + "</defs>"


def _grid(MX, MY, cell, shade):
    pad, topgap = 28, 24
    W = MX * cell + 2 * pad
    Ht = MY * cell + 2 * pad + topgap
    px = lambda x: pad + x * cell + cell / 2
    py = lambda y: topgap + pad + (MY - 1 - y) * cell + cell / 2
    el = []
    for (sx, sy, sw, sh, col) in shade:
        x = pad + sx * cell
        y = topgap + pad + (MY - sy - sh) * cell
        el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{sw*cell:.1f}" height="{sh*cell:.1f}" '
                  f'fill="{col}" stroke="#e2e8f0"/>')
    for yy in range(MY):
        for xx in range(MX):
            el.append(f'<circle cx="{px(xx):.1f}" cy="{py(yy):.1f}" r="1.7" fill="#cbd5e1"/>')
    return W, Ht, px, py, el


def _poly(order, px, py, coord, color, close=True):
    pts = []
    for nd in order:
        x, y = coord(nd)
        pts.append(f"{px(x):.1f},{py(y):.1f}")
    if close:
        x, y = coord(order[0])
        pts.append(f"{px(x):.1f},{py(y):.1f}")
    return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.7"/>'


def _arrow(x1, y1, x2, y2, marker, color, both=False, dash=""):
    ms = f' marker-start="url(#{marker})"' if both else ""
    da = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" '
            f'stroke-width="1.3" marker-end="url(#{marker})"{ms}{da}/>')


def _svg(title, W, Ht, body):
    return (f'<svg width="{W}" height="{Ht}" xmlns="http://www.w3.org/2000/svg">' + _dia_defs() +
            f'<text x="10" y="16" font-size="12" font-weight="bold" fill="#1e3a8a">{html.escape(title)}</text>' +
            "".join(body) + "</svg>")


def scheme_diagrams():
    import sim_fused_rings as fr
    fr.cfg(16, 16, 4, 6)
    coord = fr.coord
    cell = 23

    # ---- (1) hybrid, illustrated with B=4 bands ----
    shade = [(0, b * 4, 16, 4, BAND_BG[b]) for b in range(4)]
    W, Ht, px, py, el = _grid(16, 16, cell, shade)
    for b in range(4):
        el.append(_poly(fr.ham_cycle_rect(0, b * 4, 16, 4), px, py, coord, DIA_PAL[b]))
    for x in (1, 5, 9, 13):                       # per-column vertical tree (phase B)
        el.append(_arrow(px(x), py(0), px(x), py(15), "ah-o", "#ea580c", both=True, dash="4 3"))
    for b in range(4):
        el.append(f'<text x="6" y="{py(b*4+2):.1f}" font-size="10" fill="#475569">带{b}</text>')
    dia1 = _svg("① hybrid 局部环 + 全局树（示例 B=4）", W, Ht, el)

    # ---- (2) quad: 4 rings + central 4-ring exchange ----
    qspec = [(0, 0), (8, 0), (0, 8), (8, 8)]
    shade = [(qx, qy, 8, 8, BAND_BG[i]) for i, (qx, qy) in enumerate(qspec)]
    W, Ht, px, py, el = _grid(16, 16, cell, shade)
    for i, (qx, qy) in enumerate(qspec):
        el.append(_poly(fr.ham_cycle_rect(qx, qy, 8, 8), px, py, coord, DIA_PAL[i]))
    ring4 = [(7, 7), (8, 7), (8, 8), (7, 8)]      # central 4-cycle of inner corners
    for k in range(4):
        ax, ay = ring4[k]
        bx, by = ring4[(k + 1) % 4]
        el.append(_arrow(px(ax), py(ay), px(bx), py(by), "ah-r", "#dc2626"))
    for cx, cy in ring4:
        el.append(f'<circle cx="{px(cx):.1f}" cy="{py(cy):.1f}" r="3.6" fill="#dc2626"/>')
    dia2 = _svg("② quad 4×(8×8) 环 + 中心交换", W, Ht, el)

    # ---- (3) border: 4 rings + multi-point border injection ----
    W, Ht, px, py, el = _grid(16, 16, cell, shade)
    for i, (qx, qy) in enumerate(qspec):
        el.append(_poly(fr.ham_cycle_rect(qx, qy, 8, 8), px, py, coord, DIA_PAL[i]))
    for y in range(0, 16, 2):                     # vertical shared border (x:7<->8)
        el.append(_arrow(px(7), py(y), px(8), py(y), "ah-g", "#15803d", both=True))
    for x in range(0, 16, 2):                     # horizontal shared border (y:7<->8)
        el.append(_arrow(px(x), py(7), px(x), py(8), "ah-g", "#15803d", both=True))
    dia3 = _svg("③ border 4×(8×8) 环 + 边界多点注入", W, Ht, el)

    out = ["<div class='card'><h2>三种方案结构示意图（16×16 mesh）</h2>",
           "<p>灰点＝节点；彩色实线环＝各子区的 Hamilton 环投递；箭头＝跨带 / 跨象限的数据注入。</p>",
           "<div style='display:flex;flex-wrap:wrap;gap:20px;align-items:flex-start'>"]
    out.append("<figure style='margin:0;max-width:400px'>" + dia1 +
               "<figcaption style='font-size:12px;color:#475569'>4 个水平带各跑局部 Hamilton 环（彩色，①阶段）；"
               "每列再向上下相邻带做<b>树广播</b>（橙色虚线双向箭头，②阶段）互换各带块。实际取最优 B（见上表）。</figcaption></figure>")
    out.append("<figure style='margin:0;max-width:400px'>" + dia2 +
               "<figcaption style='font-size:12px;color:#475569'>4 象限各跑 8×8 环；4 个最内角 (7,7)(8,7)(8,8)(7,8) "
               "构成<b>中心 4-环</b>（红箭头），象限块经中心<b>时分</b>互传，进入对端后需<b>再绕近一圈</b>分发"
               "→ 延迟≈两圈。</figcaption></figure>")
    out.append("<figure style='margin:0;max-width:400px'>" + dia3 +
               "<figcaption style='font-size:12px;color:#475569'>同样 4 象限环，但外部数据沿两条<b>共享边界的多个点</b>"
               "跨界（绿色双向箭头遍布边界），对端只需沿该行/列<b>短弧</b>分发（≪一圈），对角象限经水平邻居二跳到达"
               "→ 延迟≈一圈+尾。</figcaption></figure>")
    out.append("</div></div>")
    return "\n".join(out)


def scheme_row(name, mk, lb, mt):
    ratio_lb = f"{mk/lb:.2f}×"
    ratio_mt = "基准" if name == "multitree" else f"{mk/mt:.2f}×"
    return f"<tr><td>{html.escape(SCHEME_LABEL.get(name, name))}</td><td>{mk}</td><td>{ratio_lb}</td><td>{ratio_mt}</td></tr>"


def fused_section():
    """时分流水模型下的 8x8 环单元 与 4 环中心交换融合 的总时延分析。"""
    import sim_fused_rings as fr

    fr.cfg(8, 8, 4, 6)
    o8 = fr.ham_cycle_rect(0, 0, 8, 8)
    r8u = fr.ring_allgather(o8, 1, False)
    r8b = fr.ring_allgather(o8, 1, True)
    fr.cfg(16, 16, 4, 6)
    fu = fr.fused_4ring(1, False)
    fb = fr.fused_4ring(1, True)
    bu = fr.border_fused_4ring(1, False)
    bb1 = fr.border_fused_4ring(1, True)
    bb2 = fr.border_fused_4ring(2, True)

    out = ["<div class='card'><h2>时分融合分析：8×8 环单元 + 4 环中心交换的“一圈 / 两圈”问题</h2>"]
    out.append(
        "<p><b>模型</b>：事件驱动的全局 link-time calendar（无冲突、网内 fork、每条 link 1 flit/cy、"
        "每节点下 ramp ramp_bw flit/cy），允许同一条 link 被不同源的 flit <b>时分复用</b>"
        "（即“时分地插入其他环的数据”）。这是<b>流水/时分</b>的理想时延；严格 0-buffer 刚性版即上文的 "
        "quad（717/1097）。脚本 <code>utils/sim_fused_rings.py</code>。</p>")

    out.append("<h3>① 8×8 Hamilton 环 allgather 单元（64 节点，独立）</h3><table>"
               "<tr><th>方向</th><th>makespan</th><th>一圈周长</th><th>busiest link</th><th>说明</th></tr>")
    out.append(f"<tr><td>单向</td><td>{r8u['makespan']}</td><td>{r8u['circ']}</td><td>{r8u['busiest_link']}</td>"
               f"<td>≈ <b>一整圈</b>（{r8u['makespan']}≈周长{r8u['circ']}）；带宽 63≪周长，延迟主导</td></tr>")
    out.append(f"<tr><td>双向</td><td>{r8b['makespan']}</td><td>{r8b['circ']}</td><td>{r8b['busiest_link']}</td>"
               f"<td>≈ <b>半圈</b>（两侧各走一半）</td></tr>")
    out.append("</table>")

    out.append("<h3>② 16×16 分 4 个 8×8 环：中心交换 vs 边界多点注入（256 节点）</h3>"
               "<p><b>(a) 中心单点交换</b>：外部数据只从中心 4 个角注入，进入对端环后再绕近一圈分发。<br>"
               "<b>(b) 边界多点注入</b>：本方案——外部数据沿象限<b>共享边界的 8 个点</b>跨界，"
               "进入对端后只需沿该行/列<b>短弧</b>分发（≪一圈），对角象限经水平邻居二跳到达。</p>"
               "<table><tr><th>方案</th><th>方向</th><th>总 makespan</th><th>busiest ring-link</th>"
               "<th>busiest down-ramp</th><th>≈ 圈数</th></tr>")
    out.append(f"<tr><td>(a) 中心单点</td><td>单向</td><td>{fu['makespan']}</td><td>{fu['busiest_link']}</td>"
               f"<td>{fu['busiest_down']}</td><td>≈ 2 圈（≈2×{r8u['makespan']}）</td></tr>")
    out.append(f"<tr><td>(a) 中心单点</td><td>双向</td><td>{fb['makespan']}</td><td>{fb['busiest_link']}</td>"
               f"<td>{fb['busiest_down']}</td><td>≈ 2 半圈</td></tr>")
    out.append(f"<tr class='win'><td><b>(b) 边界多点</b></td><td>单向</td><td><b>{bu['makespan']}</b></td>"
               f"<td>{bu['busiest_link']}</td><td>{bu['busiest_down']}</td>"
               f"<td>≈ <b>1 圈 + 尾</b>（{bu['makespan']}≈{r8u['makespan']}+{bu['makespan']-r8u['makespan']}）</td></tr>")
    out.append(f"<tr class='win'><td><b>(b) 边界多点</b></td><td>双向</td><td><b>{bb1['makespan']}</b></td>"
               f"<td>{bb1['busiest_link']}</td><td>{bb1['busiest_down']}</td>"
               f"<td>≈ 半圈 + 尾（BW=2 时 {bb2['makespan']}）</td></tr>")
    out.append("</table>")
    out.append("<p style='color:#64748b;font-size:12px'>注：中心方案在 ramp_bw=1/2 下 makespan 不变（延迟受限）；"
               f"边界双向在 BW=1→2 时 {bb1['makespan']}→{bb2['makespan']}（接近 eject 下界，下泄略有作用）。</p>")

    out.append(
        "<h3>关键结论：中心单点 = 两圈；边界多点 = 一圈 + 尾</h3><ul>"
        "<li><b>带宽上一圈本就够</b>：每条环 link 仅需承载 局部 63 + 外部 192 = 255 flit < 一圈容量 "
        f"{r8u['circ']}，带宽不是瓶颈。</li>"
        "<li><b>中心单点注入 → 延迟必须两圈</b>：外部数据只能从中心 4 角进入，进对端环后要覆盖其 64 个节点"
        f"必须再绕近一圈，最坏路径 ≈ 自身环 1 圈 + 对端环 1 圈 = 2 圈（实测单向 {fu['makespan']}≈2×{r8u['makespan']}）。</li>"
        f"<li><b>边界多点注入 → 真正做到“一圈 + 尾”</b>：外部数据沿共享边界 8 点同时跨界，"
        f"对端只需沿行/列短弧（≤7 跳）分发。最坏路径 ≈ 自身环 1 圈 + 跨界 + 短弧 ≈ 一圈 + 小尾。"
        f"实测单向 <b>{bu['makespan']}</b>（≈8×8 一圈 {r8u['makespan']} + {bu['makespan']-r8u['makespan']} 尾），"
        f"较中心方案 {fu['makespan']} <b>提速 {fu['makespan']/bu['makespan']:.2f}×</b>；"
        f"busiest link 从 {fu['busiest_link']} 降到 {bu['busiest_link']}（负载也更均衡）。</li>"
        f"<li><b>边界 + 双向是象限局部方案里的最优</b>：BW=1 {bb1['makespan']}、BW=2 <b>{bb2['makespan']}</b>，"
        "远优于<b>同模型</b>下的中心单点（413/715）与纯环（754/1474）；不过在时分模型里它仍略逊于 "
        "multi-tree 与行带 hybrid（≈255–265，见上方汇总表）——后两者已贴 eject 下界。"
        "<b>（注意：不要把时分 border 与严格 0-buffer 方案直接比，两者模型不同。）</b></li>"
        "<li><b>代价</b>：边界多点注入用满了象限共享边界的 8 条跨界链路并在对端做行/列分发——"
        "本质上是把“环 + 局部树”结合，结构比“中心 4 环”略复杂，但仍是规整的象限化布局。</li>"
        "</ul></div>")
    return "\n".join(out)


def _td_makespans():
    """Time-division (buffered/pipelined, conflict-free) makespan of every scheme,
    computed in ONE common engine so the schemes are directly comparable."""
    import sim_fused_rings as fr
    fr.cfg(16, 16, 4, 6)
    return {rb: fr.all_schemes_timediv(rb) for rb in (1, 2)}


def _cell(v, is_min):
    if v is None:
        return "<td style='color:#94a3b8'>—</td>"
    return f"<td class='win'>{v}</td>" if is_min else f"<td>{v}</td>"


def summary_table(payload, td, border_zb):
    """The centerpiece: every scheme's makespan for each ramp bandwidth, with the
    strict-0-buffer column and the time-division column side by side."""
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]

    def zb_best_hybrid(d):
        items = ([(int(B), v["makespan"], "双") for B, v in d["hybrid_bi"].items()] +
                 [(int(B), v["makespan"], "单") for B, v in d["hybrid_uni"].items()])
        B, mk, dirn = min(items, key=lambda t: t[1])
        return mk, f"{dirn}向 B={B}"

    zb1h, zb1h_tag = zb_best_hybrid(d1)
    zb2h, zb2h_tag = zb_best_hybrid(d2)
    td1h = min(td[1]["hybrid_uni"], td[1]["hybrid_bi"])
    td2h = min(td[2]["hybrid_uni"], td[2]["hybrid_bi"])

    # rows: (label, zb_bw1, td_bw1, zb_bw2, td_bw2)
    rows = [
        ("dimensional multi-tree", d1["multitree"]["makespan"], td[1]["multitree"],
         d2["multitree"]["makespan"], td[2]["multitree"]),
        ("纯 Hamilton 环（单向）", d1["ring_uni"]["makespan"], td[1]["ring_uni"],
         d2["ring_uni"]["makespan"], td[2]["ring_uni"]),
        ("纯 Hamilton 环（双向）", d1["ring_bi"]["makespan"], td[1]["ring_bi"],
         d2["ring_bi"]["makespan"], td[2]["ring_bi"]),
        (f"hybrid 行带（最优）", zb1h, td1h, zb2h, td2h),
        ("quad 中心交换（单向）", d1["quad_uni"]["makespan"], td[1]["quad_uni"],
         d2["quad_uni"]["makespan"], td[2]["quad_uni"]),
        ("quad 中心交换（双向）", d1["quad_bi"]["makespan"], td[1]["quad_bi"],
         d2["quad_bi"]["makespan"], td[2]["quad_bi"]),
        ("border 边界多点（单向）", border_zb[(1, "uni")], td[1]["border_uni"],
         border_zb[(2, "uni")], td[2]["border_uni"]),
        ("border 边界多点（双向）", border_zb[(1, "bi")], td[1]["border_bi"],
         border_zb[(2, "bi")], td[2]["border_bi"]),
    ]
    cols = [[r[i] for r in rows if r[i] is not None] for i in range(1, 5)]
    mins = [min(c) for c in cols]

    out = ["<div class='card'><h2>汇总：各方案 makespan（按下 Ramp 带宽 × 调度模型）</h2>",
           "<p>两套模型分开看：<b>0-buf 刚性</b>＝严格 0-buffer、刚性时隙（路由器零缓存，仅源端可缓存）；"
           "<b>时分</b>＝允许网内时分复用 + 源端缓存的流水/理想时延（同一引擎计算，可直接比较）。"
           "同一方案两列之差 = <b>0-buffer 的代价</b>。绿底＝该列最小（越低越好）。</p>",
           "<table><tr><th rowspan='2'>方案</th>"
           "<th colspan='2'>下 Ramp 带宽 = 1（eject 下界 255）</th>"
           "<th colspan='2'>下 Ramp 带宽 = 2（eject 下界 128）</th></tr>"
           "<tr><th>0-buf 刚性</th><th>时分/流水</th><th>0-buf 刚性</th><th>时分/流水</th></tr>"]
    for label, *vals in rows:
        cells = "".join(_cell(v, v is not None and v == mins[i]) for i, v in enumerate(vals))
        note = ""
        if label.startswith("hybrid"):
            note = f" <span style='color:#64748b;font-size:11px'>(0-buf {zb1h_tag} / {zb2h_tag})</span>"
        out.append(f"<tr><td>{html.escape(label)}{note}</td>{cells}</tr>")
    out.append("</table>")
    out.append("<p style='color:#64748b;font-size:12px'>注：所有方案（含 border）都有可行的严格 0-buf 刚性调度；"
               "8×8 独立环参考：单向一圈 354、双向半圈 186 cycle。</p>")
    out.append("</div>")
    return "\n".join(out), (zb1h, zb2h, td1h, td2h)


def insights_section(payload, td):
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]
    mt = (d1["multitree"]["makespan"], d2["multitree"]["makespan"])
    out = ["<div class='card'><h2>数据洞察</h2><ol>"]
    out.append(
        "<li><b>0-buffer 的代价 = 足迹局部性</b>。同一方案“0-buf÷时分”的倍率：multi-tree "
        f"{mt[0]}/{td[1]['multitree']}≈<b>{mt[0]/td[1]['multitree']:.1f}×</b>（足迹横跨整网，刚性最难错开）；"
        f"hybrid 行带 ≈<b>1.6×</b>（足迹最紧凑，最抗 0-buffer）；"
        f"quad 单向 {d1['quad_uni']['makespan']}/{td[1]['quad_uni']}≈<b>1.0×</b>"
        "（本就延迟受限，刚性化几乎不再变差）。局部性越强 → 越抗 0-buffer。</li>")
    out.append(
        "<li><b>时分模型里最优是 multi-tree 与 hybrid，不是 border</b>。时分 BW=1：multi-tree "
        f"{td[1]['multitree']}、hybrid {min(td[1]['hybrid_uni'], td[1]['hybrid_bi'])} 已贴 eject 下界 255；"
        f"border 双向 {td[1]['border_bi']} 紧随其后，是“环局部性”系列里的最优，但仍略逊于树/行带。"
        "（此前把时分 border 与 0-buf 方案混比的说法已纠正。）</li>")
    out.append(
        "<li><b>下 Ramp 带宽 1→2 是否有用，取决于瓶颈</b>。受“下泄”约束者才受益："
        f"0-buf multi-tree {mt[0]}→{mt[1]}、0-buf quad 双向 {d1['quad_bi']['makespan']}→{d2['quad_bi']['makespan']}、"
        f"时分 border 双向 {td[1]['border_bi']}→{td[2]['border_bi']}；"
        "受“延迟/链路”约束者几乎不变：纯环恒 1474/754、quad 中心恒 715/717、"
        f"时分 multi-tree {td[1]['multitree']}→{td[2]['multitree']}（已链路受限）。"
        "<b>一旦调度逼近延迟最优，2 flit/cy 下泄几乎白费。</b></li>")
    out.append(
        "<li><b>方向（单/双）是双刃剑</b>。时分里双向普遍把最坏路径减半（纯环 1474→754、border 437→283、"
        f"quad 中心 715→413）；但 0-buf 刚性里双向中心交换<b>有害</b>——quad 单向 {d1['quad_uni']['makespan']} "
        f"vs 双向 {d1['quad_bi']['makespan']}（BW=1），双向制造更多链路冲突反而更慢。</li>")
    out.append(
        "<li><b>“一圈 + 尾”得到验证</b>。8×8 独立环单向 354、双向 186；border 16×16 单向 "
        f"{td[1]['border_uni']}≈354+尾、双向 {td[1]['border_bi']}≈186+尾；中心单点 {td[1]['quad_uni']}≈2×354 是两圈。"
        "每条环 link 仅需承载 255 flit < 一圈容量 356，<b>带宽够、是延迟（圈数）决定胜负</b>。</li>")
    out.append("</ol></div>")
    return "\n".join(out)


def _border_zb():
    """Strict 0-buffer (rigid packer) makespan for the border scheme."""
    import sched_zerobuf_compare as Z
    Z.cfg(16, 16, 4, 6)
    Z.init_ring()
    Z.init_quadrants()
    out = {}
    for rb in (1, 2):
        for bidir, key in ((False, "uni"), (True, "bi")):
            out[(rb, key)] = Z.run_scheme(lambda s, bd=bidir, r=rb: Z.fp_border(s, bd, r), rb)[0]
    return out


def buffer_section(payload, border_zb):
    """Can hybrid / quad / border be 0-buffer, and how many flits of buffer do the
    faster time-division schedules consume?"""
    import sim_fused_rings as fr
    fr.cfg(16, 16, 4, 6)
    full = fr.ham_cycle_rect(0, 0, 16, 16)
    quads, ring4 = fr.quad_setup()

    def meas(builder, rb):
        return fr.measure_buffers({s: builder(s) for s in range(256)}, rb)

    def hybrid_meas(rb):
        best = None
        for B in (2, 4, 8):
            r = meas(lambda s, B=B: fr.build_hybrid_delivery(s, B, True), rb)
            if best is None or r[0] < best[0]:
                best = r
        return best

    out = ["<div class='card'><h2>这些方案能做到 0-buffer 吗？需要多少 buffer（flit）？</h2>"]
    out.append("<p><b>结论先行</b>：hybrid、quad 中心交换、border 边界多点<b>都能</b>做到严格 0-buffer"
               "（路由器零缓存，仅靠源端注入偏移调度）——即上方“0-buf 刚性”列；代价是 makespan 偏高。"
               "只有为了拿到更快的<b>时分</b> makespan 才需要路由器 buffer，所需量主要由<b>下 Ramp 带宽</b>决定。</p>")
    out.append("<p>buffer 分两处：<b>link buffer</b>＝某输出端口同时排队等链路的 flit 数（在网）；"
               "<b>eject buffer</b>＝某节点到达后等下 ramp 下泄的 flit 数（NI 队列）。"
               "下表为达到时分 makespan 时的<b>峰值</b>占用。</p>")
    out.append("<table><tr><th>方案</th><th>下Ramp带宽</th><th>时分 makespan</th>"
               "<th>link buffer<br>(flit/端口)</th><th>eject buffer<br>(flit/节点)</th>"
               "<th>同方案严格 0-buf makespan</th></tr>")
    for rb in (1, 2):
        zb = payload["bw"][str(rb)]
        hyb_zb = min([x["makespan"] for x in zb["hybrid_uni"].values()] +
                     [x["makespan"] for x in zb["hybrid_bi"].values()])
        rows = [
            ("dimensional multi-tree", meas(fr.build_multitree_delivery, rb), zb["multitree"]["makespan"]),
            ("hybrid 行带（双向,最优）", hybrid_meas(rb), hyb_zb),
            ("quad 中心交换（单向）", meas(lambda s: fr.build_quad_delivery(s, False, quads, ring4), rb), zb["quad_uni"]["makespan"]),
            ("quad 中心交换（双向）", meas(lambda s: fr.build_quad_delivery(s, True, quads, ring4), rb), zb["quad_bi"]["makespan"]),
            ("border 边界多点（单向）", meas(lambda s: fr.build_border_delivery(s, False), rb), border_zb[(rb, "uni")]),
            ("border 边界多点（双向）", meas(lambda s: fr.build_border_delivery(s, True), rb), border_zb[(rb, "bi")]),
            ("纯 Hamilton 环（双向,参考）", meas(lambda s: fr.build_ring_delivery(full, s, True), rb), zb["ring_bi"]["makespan"]),
        ]
        for label, (mk, lb, eb), zbmk in rows:
            hot = " class='win'" if (lb <= 2 and eb <= 2) else ""
            out.append(f"<tr{hot}><td>{html.escape(label)}</td><td>{rb}</td><td>{mk}</td>"
                       f"<td>{lb}</td><td>{eb}</td><td>{zbmk}</td></tr>")
    out.append("</table>")
    out.append("<p style='color:#64748b;font-size:12px'>绿底＝该 makespan 几乎无需路由器 buffer（link 与 eject 均 ≤2 flit）。</p>")
    out.append(
        "<ul>"
        "<li><b>都能 0-buffer</b>：三方案的“严格 0-buf makespan”均为已验证可行的刚性调度"
        "（无冲突、无阻塞、每节点下泄 N−1）。所以答案是——<b>做得到 0-buffer，只是更慢</b>。</li>"
        "<li><b>真正吃 buffer 的是 eject 队列，且由带宽决定</b>：BW=1 要逼近 eject 下界 255，"
        "flit 到达速率远超 1 flit/cy 的下泄，节点被迫排队——multi-tree / hybrid 需 <b>~120 flit</b> 的 eject buffer，"
        "quad / border 双向需 <b>~74 flit</b>；BW=2 下泄翻倍后，eject buffer 骤降到 <b>≤20 flit</b>。"
        "这是“逼近下界”的内在代价，与具体拓扑无关。</li>"
        "<li><b>border 最省 buffer</b>：BW=2 双向仅 ~2 flit 即达 267；单向几乎 0-buffer（≤2 flit）就能流水到 437——"
        "投递沿短弧均匀铺开、不突发。</li>"
        "<li><b>quad 中心双向 link buffer 偏高（44 flit）</b>：中心 4-环是唯一跨象限通道，被多源时分挤占，"
        "链路前排起 ~44 flit 队列——这是“单点交换”的结构代价。</li>"
        "<li><b>纯环天然 0-buffer</b>：link/eject buffer ≈0（节奏自然均匀），但慢（754）。"
        "<b>规律：投递越均匀/越慢越省 buffer；越快越逼近下界越费 buffer。</b></li>"
        "</ul></div>")
    return "\n".join(out)


def render():
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    mx, my, h, v, n = payload["mx"], payload["my"], payload["h"], payload["v"], payload["n"]
    bw_keys = sorted(payload["bw"].keys(), key=int)
    td = _td_makespans()
    border_zb = _border_zb()

    s = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         "<title>16x16 Allgather Comparison</title>",
         "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;max-width:1100px;}"
         "h1,h2{color:#1e3a8a;}table{border-collapse:collapse;margin:12px 0;width:100%;}"
         "td,th{border:1px solid #cbd5e1;padding:6px 8px;font-size:13px;}th{background:#e2e8f0;}"
         ".card{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}"
         ".win{background:#dcfce7;font-weight:bold;}code{background:#f1f5f9;padding:2px 4px;border-radius:4px;}"
         "ol li,ul li{margin:6px 0;}</style></head><body>"]
    s.append(f"<h1>{mx}×{my} Mesh Allgather 方案对比（H={h}, V={v}）</h1>")

    s.append("<div class='card'><h2>问题设定</h2>"
             f"<p>{mx}×{my} mesh（{n} 节点）。横向 link delay <b>H={h}</b> cycle，纵向 <b>V={v}</b> cycle，"
             "PE↔router ramp 延迟 1 cycle。下 Ramp（eject）带宽分别取 <b>1</b> 与 <b>2</b> flit/cycle 两种场景，msg_size=1。</p>"
             "<p><b>两套调度模型</b>（报告中始终分开比较）：</p><ul>"
             "<li><b>严格 0-buffer 刚性</b>：所有时隙离线编排，无冲突（每条有向 link 每 cycle ≤1 flit、每节点下/上 ramp "
             "≤ramp 带宽）、无阻塞（全离线）、<b>路由器零缓存</b>——flit 一旦注入即按固定时刻逐跳前进，中间绝不等待；"
             "唯一自由度是每源<b>注入偏移</b>（数据暂存在源 PE/SRAM，非路由器 buffer），贪心打包出可行上界。"
             "脚本 <code>utils/sched_zerobuf_compare.py</code>。</li>"
             "<li><b>时分 / 流水</b>：事件驱动全局 link-time calendar，允许同一条 link 被不同源 flit <b>时分复用</b>、"
             "并允许网内缓存——这是<b>流水的理想时延</b>（下界侧）。脚本 <code>utils/sim_fused_rings.py</code>。</li>"
             "</ul>"
             f"<p><b>通用下界</b>：每节点都要经其单条下 ramp 下泄 N−1={n-1} 条 flit，"
             "故任何方案 makespan ≥ (N−1)/ramp_bw + 最小投递延迟（BW=1→255，BW=2→128）。</p></div>")

    s.append("<div class='card'><h2>五类方案</h2><ul>"
             "<li><b>dimensional multi-tree</b>：每源用 X-then-Y 维序多播树（行脊 + 各列分支，网内 fork），带 buffer 时可命中下界。</li>"
             "<li><b>纯 Hamilton 环</b>：全局一个蛇形 comb 闭环，单向 / 双向。</li>"
             "<li><b>hybrid 局部环 + 全局树</b>：按行切 B 个水平带（每带 R=MY/B 行）；①带内跑局部 Hamilton 环 allgather（并行）；"
             "②每列向上下相邻带做树状广播互换各带块。</li>"
             "<li><b>quad 4×(8×8) 环 + 中心交换</b>：切成 4 个 8×8 象限各跑环；4 象限最内角在中心构成 4-环，"
             "象限块经中心<b>时分</b>互传，对端再绕环二次分发。</li>"
             "<li><b>border 4×(8×8) 环 + 边界多点注入</b>：同样切 4 象限跑环，但外部数据沿象限<b>共享边界的多点</b>跨界，"
             "对端只需沿行/列<b>短弧</b>分发（≪一圈），逼近“一圈 + 尾”。</li>"
             "</ul></div>")

    s.append(scheme_diagrams())

    summary_html, _ = summary_table(payload, td, border_zb)
    s.append(summary_html)
    s.append(insights_section(payload, td))
    s.append(buffer_section(payload, border_zb))

    # corrected conclusions
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]
    mt1, mt2 = d1["multitree"]["makespan"], d2["multitree"]["makespan"]
    b1B, b1 = min([(int(B), x["makespan"]) for B, x in d1["hybrid_bi"].items()] +
                  [(int(B), x["makespan"]) for B, x in d1["hybrid_uni"].items()], key=lambda t: t[1])
    bu2_B, bu2 = best_hybrid(d2, "hybrid_uni")
    s.append("<div class='card'><h2>结论（已复核）</h2><ul>"
             "<li><b>允许 buffer / 时分时：multi-tree 与 hybrid 行带并列最优</b>，"
             f"均贴近 eject 下界（BW=1 约 {td[1]['multitree']}/{min(td[1]['hybrid_uni'], td[1]['hybrid_bi'])}，"
             f"BW=2 约 {td[2]['multitree']}/{min(td[2]['hybrid_uni'], td[2]['hybrid_bi'])}）；"
             f"<b>border 边界多点</b>（{td[1]['border_bi']}/{td[2]['border_bi']}，双向）是象限局部方案中的最优、紧随其后；"
             "中心单点（413/715）与纯环（754/1474）最差。</li>"
             f"<li><b>严格 0-buffer 刚性时：hybrid 行带整体最优</b>。BW=1 ≈ <b>{b1}</b>(B={b1B})，"
             f"比 multi-tree 的 {mt1} 快 <b>{mt1/b1:.2f}×</b>；BW=2 ≈ <b>{bu2}</b>(单向 B={bu2_B})，优于 multi-tree {mt2}。"
             "原因：行带足迹时间跨度小、占用稀疏，最易刚性错开。</li>"
             f"<li><b>multi-tree 在 0-buffer 下退化</b>（{mt1}/{mt2}）：每源足迹横跨整网"
             f"（直径 {(mx-1)*h+(my-1)*v} cycle），又宽又满，256 个宽足迹难以单偏移错开 → 约 3× 下界。</li>"
             f"<li><b>quad 中心交换居中</b>：单向恒 {d1['quad_uni']['makespan']}（延迟受象限环周长主导，加带宽无效），"
             f"双向 0-buf 反而更差（BW=1 {d1['quad_bi']['makespan']}）。可行、规整、对称，但中心 4-环是唯一跨象限通道，受两段绕行延迟约束。</li>"
             "<li><b>0-buffer 可行性</b>：三种方案都能做到严格 0-buffer（仅源端注入偏移），代价是更高 makespan；"
             "要达到时分快档才需路由器 buffer，量主要由下 Ramp 带宽决定——BW=1 逼近下界需 ~120 flit eject 队列，"
             "BW=2 降到 ≤20 flit，border 最省（≤2 flit）。详见下节。</li>"
             "<li><b>选型</b>：可缓存 → multi-tree / hybrid；硬性 0-buffer 刚性 → <b>hybrid 行带</b>；"
             "要规整 4 象限版图又接近最优延迟、且 buffer 预算小 → <b>border 边界多点</b>（时分）。</li>"
             "</ul></div>")

    # ---- appendix: detailed 0-buffer tables / B-sweep / charts ----
    s.append("<div class='card'><h2>附：严格 0-buffer 明细</h2>")
    for bk in bw_keys:
        d = payload["bw"][bk]
        rb = int(bk)
        lb = d["eject_lb"]
        mt = d["multitree"]["makespan"]
        bu_B, bu = best_hybrid(d, "hybrid_uni")
        bb_B, bb = best_hybrid(d, "hybrid_bi")
        qu = d["quad_uni"]["makespan"]
        qb = d["quad_bi"]["makespan"]
        best_overall = min(mt, d["ring_uni"]["makespan"], d["ring_bi"]["makespan"], bu, bb, qu, qb)

        s.append(f"<h3>下 Ramp 带宽 = {rb}（eject 下界 = {lb}）</h3>")
        s.append("<table><tr><th>方案</th><th>0-buffer makespan</th><th>vs 下界</th><th>vs multi-tree</th></tr>")
        for name, mk in [("multitree", mt), ("ring_uni", d["ring_uni"]["makespan"]),
                         ("ring_bi", d["ring_bi"]["makespan"]),
                         ("hybrid_uni", bu), ("hybrid_bi", bb),
                         ("quad_uni", qu), ("quad_bi", qb)]:
            row = scheme_row(name, mk, lb, mt)
            if mk == best_overall:
                row = row.replace("<tr>", "<tr class='win'>")
            if name.startswith("hybrid"):
                Bopt = bu_B if name == "hybrid_uni" else bb_B
                row = row.replace("</td>", f" (最优 B={Bopt})</td>", 1)
            s.append(row)
        s.append("</table>")

        s.append("<table><tr><th>hybrid B 带数</th><th>R 行/带</th>"
                 "<th>单向局部环</th><th>双向局部环</th></tr>")
        allB = sorted({int(b) for b in list(d["hybrid_uni"]) + list(d["hybrid_bi"])})
        for B in allB:
            ru = d["hybrid_uni"].get(str(B), {}).get("makespan")
            rbi = d["hybrid_bi"].get(str(B), {}).get("makespan")
            s.append(f"<tr><td>{B}</td><td>{my//B}</td>"
                     f"<td>{ru if ru is not None else '—（单行无环）'}</td>"
                     f"<td>{rbi if rbi is not None else '—'}</td></tr>")
        s.append("</table>")

        labels = ["multi-tree", "ring 双向", f"hybrid B{bu_B}单", f"hybrid B{bb_B}双",
                  "quad 单向", "quad 双向"]
        values = [mt, d["ring_bi"]["makespan"], bu, bb, qu, qb]
        s.append(bar_chart(f"0-buffer makespan @ ramp_bw={rb}（越低越好）", labels, values, lb=lb))
    s.append("</div>")

    s.append(fused_section())

    s.append("</body></html>")
    HTML_PATH.write_text("\n".join(s), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    render()
