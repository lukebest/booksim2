#!/usr/bin/env python3
"""Render the 16x16 ZERO-BUFFER allgather comparison report from cached JSON.

Every scheme in this report is a strict 0-buffer (router zero-buffer, rigid
offset) schedule; only the 0-buffer makespan is compared, at down-ramp bandwidth
1 and 2 flit/cycle.
"""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "zerobuf_16x16.json"
HTML_PATH = ROOT / "results" / "report_16x16.html"


def get(d, key):
    return d[key]["makespan"]


def hyb_best(d, prefix):
    """Best (min makespan) over direction & band-count for a hybrid family."""
    items = []
    for dirn, suf in (("单", "_uni"), ("双", "_bi")):
        for B, v in d.get(prefix + suf, {}).items():
            items.append((v["makespan"], dirn, int(B)))
    mk, dirn, B = min(items)
    return mk, f"{dirn}向 B={B}"


def bar_chart(title, labels, values, lb=None):
    width = max(620, 78 * len(labels))
    height = 300
    margin = 54
    plot_h = height - 2 * margin
    ymax = max(values) * 1.12
    bw = (width - 2 * margin) / len(labels)
    best = min(values)
    p = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
         f'<text x="{margin}" y="22" font-size="14" font-weight="bold">{html.escape(title)}</text>',
         f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#64748b"/>',
         f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#64748b"/>']
    if lb:
        ly = height - margin - (lb / ymax) * plot_h
        p.append(f'<line x1="{margin}" y1="{ly:.1f}" x2="{width-margin}" y2="{ly:.1f}" stroke="#dc2626" stroke-dasharray="5 4"/>')
        p.append(f'<text x="{width-margin-2:.0f}" y="{ly-4:.1f}" font-size="10" fill="#dc2626" text-anchor="end">eject LB={lb}</text>')
    for i, (lab, val) in enumerate(zip(labels, values)):
        bh = (val / ymax) * plot_h
        x = margin + i * bw + bw * 0.14
        y = height - margin - bh
        col = "#10b981" if val == best else "#60a5fa"
        p.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.72:.1f}" height="{bh:.1f}" fill="{col}"/>')
        p.append(f'<text x="{x+bw*0.36:.1f}" y="{y-5:.1f}" font-size="11" font-weight="bold" text-anchor="middle">{val}</text>')
        for j, line in enumerate(lab.split("\n")):
            p.append(f'<text x="{x+bw*0.36:.1f}" y="{height-margin+15+12*j:.1f}" font-size="10" text-anchor="middle">{html.escape(line)}</text>')
    p.append("</svg>")
    return "\n".join(p)


# --------------------------------------------------------------------------
# Scheme structure diagrams (inline SVG over a 16x16 grid)
# --------------------------------------------------------------------------
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
    cell = 22

    # (1a) hybrid horizontal: 4 horizontal bands + vertical tree
    shade = [(0, b * 4, 16, 4, BAND_BG[b]) for b in range(4)]
    W, Ht, px, py, el = _grid(16, 16, cell, shade)
    for b in range(4):
        el.append(_poly(fr.ham_cycle_rect(0, b * 4, 16, 4), px, py, coord, DIA_PAL[b]))
    for x in (1, 5, 9, 13):
        el.append(_arrow(px(x), py(0), px(x), py(15), "ah-o", "#ea580c", both=True, dash="4 3"))
    for b in range(4):
        el.append(f'<text x="6" y="{py(b*4+2):.1f}" font-size="10" fill="#475569">带{b}</text>')
    dia_h = _svg("①a hybrid 横带环 + 纵向树（B=4）", W, Ht, el)

    # (1b) hybrid vertical: 4 vertical bands + horizontal tree   (NEW)
    shade = [(b * 4, 0, 4, 16, BAND_BG[b]) for b in range(4)]
    W, Ht, px, py, el = _grid(16, 16, cell, shade)
    for b in range(4):
        el.append(_poly(fr.ham_cycle_vband(4, b * 4), px, py, coord, DIA_PAL[b]))
    for y in (1, 5, 9, 13):
        el.append(_arrow(px(0), py(y), px(15), py(y), "ah-o", "#ea580c", both=True, dash="4 3"))
    for b in range(4):
        el.append(f'<text x="{px(b*4+1):.1f}" y="46" font-size="10" fill="#475569">带{b}</text>')
    dia_v = _svg("①b hybrid 纵带环 + 横向树（B=4，新增）", W, Ht, el)

    # (2) quad: 4 rings + central 4-ring exchange
    qspec = [(0, 0), (8, 0), (0, 8), (8, 8)]
    shade = [(qx, qy, 8, 8, BAND_BG[i]) for i, (qx, qy) in enumerate(qspec)]
    W, Ht, px, py, el = _grid(16, 16, cell, shade)
    for i, (qx, qy) in enumerate(qspec):
        el.append(_poly(fr.ham_cycle_rect(qx, qy, 8, 8), px, py, coord, DIA_PAL[i]))
    ring4 = [(7, 7), (8, 7), (8, 8), (7, 8)]
    for k in range(4):
        ax, ay = ring4[k]
        bx, by = ring4[(k + 1) % 4]
        el.append(_arrow(px(ax), py(ay), px(bx), py(by), "ah-r", "#dc2626"))
    for cx, cy in ring4:
        el.append(f'<circle cx="{px(cx):.1f}" cy="{py(cy):.1f}" r="3.6" fill="#dc2626"/>')
    dia_q = _svg("② quad 4×(8×8) 环 + 中心交换", W, Ht, el)

    # (3) border: 4 rings + multi-point border injection
    W, Ht, px, py, el = _grid(16, 16, cell, shade)
    for i, (qx, qy) in enumerate(qspec):
        el.append(_poly(fr.ham_cycle_rect(qx, qy, 8, 8), px, py, coord, DIA_PAL[i]))
    for y in range(0, 16, 2):
        el.append(_arrow(px(7), py(y), px(8), py(y), "ah-g", "#15803d", both=True))
    for x in range(0, 16, 2):
        el.append(_arrow(px(x), py(7), px(x), py(8), "ah-g", "#15803d", both=True))
    dia_b = _svg("③ border 4×(8×8) 环 + 边界多点注入", W, Ht, el)

    out = ["<div class='card'><h2>方案结构示意图（16×16 mesh）</h2>",
           "<p>灰点＝节点；彩色实线环＝各子区的 Hamilton 环投递；橙虚线/箭头＝跨带/跨象限的多播注入。</p>",
           "<div style='display:flex;flex-wrap:wrap;gap:18px;align-items:flex-start'>"]
    out.append("<figure style='margin:0;max-width:380px'>" + dia_h +
               "<figcaption style='font-size:12px;color:#475569'><b>横带环 + 纵树</b>：4 个水平带各跑局部 Hamilton 环"
               "（彩色，①）；每列再向上下相邻带做<b>树广播</b>（橙虚线，②）。环脊走横向（便宜 H），树走纵向（贵 V）。</figcaption></figure>")
    out.append("<figure style='margin:0;max-width:380px'>" + dia_v +
               "<figcaption style='font-size:12px;color:#475569'><b>纵带环 + 横树（新增）</b>：转置版——4 个垂直带各跑局部 "
               "Hamilton 环（彩色，①）；每行再向左右相邻带做<b>树广播</b>（橙虚线，②）。树走横向（便宜 H=4）"
               "→ 在 H&lt;V 下 0-buffer 最优。</figcaption></figure>")
    out.append("<figure style='margin:0;max-width:380px'>" + dia_q +
               "<figcaption style='font-size:12px;color:#475569'>4 象限各跑 8×8 环；4 个最内角构成<b>中心 4-环</b>（红箭头），"
               "象限块经中心互传，进入对端后<b>再绕近一圈</b>分发。</figcaption></figure>")
    out.append("<figure style='margin:0;max-width:380px'>" + dia_b +
               "<figcaption style='font-size:12px;color:#475569'>同样 4 象限环，但外部数据沿两条<b>共享边界的多个点</b>"
               "跨界（绿色箭头），对端沿行/列<b>短弧</b>分发，对角象限经水平邻居二跳到达。</figcaption></figure>")
    out.append("</div></div>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Per-scheme 0-buffer makespan rows.  BW2 is corrected to min(bw2, bw1):
# a BW=1 0-buffer schedule is always feasible at BW=2 (ramp cap only relaxes),
# so more down-ramp bandwidth can never increase the achievable makespan.
# --------------------------------------------------------------------------
def collect(payload):
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]
    h1, h1t = hyb_best(d1, "hybrid")
    h2, h2t = hyb_best(d2, "hybrid")
    v1, v1t = hyb_best(d1, "hybrid_v")
    v2, v2t = hyb_best(d2, "hybrid_v")
    rows = [
        ("dimensional multi-tree", get(d1, "multitree"), get(d2, "multitree"), "", ""),
        ("纯 Hamilton 环（单向）", get(d1, "ring_uni"), get(d2, "ring_uni"), "", ""),
        ("纯 Hamilton 环（双向）", get(d1, "ring_bi"), get(d2, "ring_bi"), "", ""),
        ("hybrid 横带环 + 纵向树", h1, h2, h1t, h2t),
        ("hybrid 纵带环 + 横向树（新增）", v1, v2, v1t, v2t),
        ("quad 中心交换（单向）", get(d1, "quad_uni"), get(d2, "quad_uni"), "", ""),
        ("quad 中心交换（双向）", get(d1, "quad_bi"), get(d2, "quad_bi"), "", ""),
        ("border 边界多点（单向）", get(d1, "border_uni"), get(d2, "border_uni"), "", ""),
        ("border 边界多点（双向）", get(d1, "border_bi"), get(d2, "border_bi"), "", ""),
    ]
    # apply BW2 monotonic correction
    out = []
    for label, mk1, mk2, t1, t2 in rows:
        mk2c = min(mk1, mk2)
        borrowed = mk2c < mk2
        out.append((label, mk1, mk2c, t1, (t1 + " ←沿用BW1解" if borrowed else t2)))
    return out


def insights_section(payload):
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]
    rows = {r[0]: r for r in collect(payload)}
    hH = rows["hybrid 横带环 + 纵向树"]
    hV = rows["hybrid 纵带环 + 横向树（新增）"]
    mt = rows["dimensional multi-tree"]
    out = ["<div class='card'><h2>数据洞察</h2><ol>"]
    out.append(
        f"<li><b>朝向决定胜负（H=4 &lt; V=6）：纵带环 + 横向树最优</b>。BW=1 它做到 <b>{hV[1]}</b>，"
        f"优于横带环+纵树 {hH[1]}、multi-tree {mt[1]}；BW=2 同样以 <b>{hV[2]}</b> 领先（{hH[2]}/{mt[2]}）。"
        "原因：把“最长的那段树”放到<b>便宜的水平方向</b>——纵带版树跨度最多 12×H=48，横带版树跨度 12×V=72，"
        "刚性错开更省。环脊则相反（纵环脊走贵的 V），但综合下来横向树的节省占优。</li>")
    out.append(
        f"<li><b>带数 B 很敏感</b>。纵带环+横向树最优在 B≈2~4（C=8~4），横带环+纵向树最优在 B≈2（BW1）/B=8（BW2）；"
        "选错 B 可差 2~3 倍（见明细表）。把局部环做小、树做短，整体更易刚性错开。</li>")
    out.append(
        f"<li><b>multi-tree 在 0-buffer 下退化</b>：{mt[1]}/{mt[2]}（≈{mt[1]/d1['eject_lb']:.1f}× 下界）。"
        f"每源足迹横跨整网（直径 {(16-1)*4+(16-1)*6} cycle），又宽又满，256 个宽足迹难以单偏移彼此错开。</li>")
    out.append(
        f"<li><b>方向是双刃剑</b>。横带 hybrid 双向在 BW1 有利；但 quad 双向 0-buffer 反而更差"
        f"（单向 {get(d1,'quad_uni')} vs 双向 {get(d1,'quad_bi')}，BW1）——双向中心交换制造更多链路冲突。</li>")
    out.append(
        f"<li><b>带宽 1→2 只帮“受下泄约束”的方案</b>：multi-tree {get(d1,'multitree')}→{get(d2,'multitree')}、"
        f"quad 双向 {get(d1,'quad_bi')}→{get(d2,'quad_bi')} 明显下降；而纯环（{get(d1,'ring_uni')}/{get(d1,'ring_bi')}）、"
        "quad 单向（恒 717）受延迟约束，加带宽无效。最优的纵带 hybrid 两带宽同为最优，说明它已不受下泄瓶颈限制。</li>")
    out.append("</ol></div>")
    return "\n".join(out)


def conclusions_section(payload):
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]
    rows = {r[0]: r for r in collect(payload)}
    hV = rows["hybrid 纵带环 + 横向树（新增）"]
    hH = rows["hybrid 横带环 + 纵向树"]
    mt = rows["dimensional multi-tree"]
    out = ["<div class='card'><h2>结论（仅 0-buffer）</h2><ul>"]
    out.append(
        f"<li><b>0-buffer 最优方案：hybrid 纵带环 + 横向树</b>。BW=1 / BW=2 均为 <b>{hV[1]} / {hV[2]}</b>，"
        f"优于横带环+纵向树（{hH[1]}/{hH[2]}）、multi-tree（{mt[1]}/{mt[2]}）、"
        f"quad（{get(d1,'quad_uni')}/{get(d2,'quad_bi')}）、border（{get(d1,'border_uni')}/{get(d2,'border_bi')}）、"
        f"纯环（{get(d1,'ring_uni')}/{get(d1,'ring_bi')}）。</li>")
    out.append(
        "<li><b>关键洞察：在 H&lt;V 的网格上，应让“树/多播”走便宜的方向</b>。本例 H=4&lt;V=6，"
        "故纵向 Hamilton 环 + 横向多播树优于横向环 + 纵向树。若 H&gt;V 则结论相反。</li>")
    out.append(
        f"<li><b>multi-tree</b> 虽在可缓存模型最强，但严格 0-buffer 下退化到 {mt[1]}/{mt[2]}（足迹太宽，难刚性错开）。</li>")
    out.append(
        f"<li><b>quad / border</b> 结构规整、4 象限对称，0-buffer 可行但偏慢"
        f"（quad 单向恒 {get(d1,'quad_uni')}；border 单向 {get(d1,'border_uni')}/{get(d2,'border_uni')}）——"
        "跨象限通道单一/绕行较长。</li>")
    out.append(
        "<li><b>选型（硬性 0-buffer）</b>：首选 <b>hybrid 纵带环 + 横向树</b>；"
        "要规整 4 象限版图可用 quad/border 作折中。</li>")
    out.append("</ul></div>")
    return "\n".join(out)


def hyb_sweep_table(payload, prefix, dim_label):
    """B-sweep detail for one hybrid orientation, both bandwidths."""
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]
    allB = sorted({int(b) for b in list(d1.get(prefix + "_uni", {})) +
                   list(d1.get(prefix + "_bi", {})) +
                   list(d2.get(prefix + "_uni", {})) + list(d2.get(prefix + "_bi", {}))})
    out = [f"<table><tr><th>B 带数</th><th>{dim_label}</th>"
           "<th>BW1 单向</th><th>BW1 双向</th><th>BW2 单向</th><th>BW2 双向</th></tr>"]

    def g(d, key, B):
        return d.get(key, {}).get(str(B), {}).get("makespan")
    for B in allB:
        sub = 16 // B
        cells = []
        for d in (d1, d2):
            for suf in ("_uni", "_bi"):
                v = g(d, prefix + suf, B)
                cells.append(str(v) if v is not None else "—")
        out.append(f"<tr><td>{B}</td><td>{sub}</td>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def render():
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    mx, my, h, v, n = payload["mx"], payload["my"], payload["h"], payload["v"], payload["n"]

    s = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         "<title>16x16 Zero-buffer Allgather Comparison</title>",
         "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;max-width:1100px;}"
         "h1,h2{color:#1e3a8a;}table{border-collapse:collapse;margin:12px 0;width:100%;}"
         "td,th{border:1px solid #cbd5e1;padding:6px 8px;font-size:13px;}th{background:#e2e8f0;}"
         ".card{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}"
         ".win{background:#dcfce7;font-weight:bold;}code{background:#f1f5f9;padding:2px 4px;border-radius:4px;}"
         "ol li,ul li{margin:6px 0;}</style></head><body>"]
    s.append(f"<h1>{mx}×{my} Mesh Allgather：0-buffer 方案对比（H={h}, V={v}）</h1>")

    s.append("<div class='card'><h2>问题设定</h2>"
             f"<p>{mx}×{my} mesh（{n} 节点）。横向 link delay <b>H={h}</b> cycle，纵向 <b>V={v}</b> cycle，"
             "PE↔router ramp 延迟 1 cycle，msg_size=1。下 Ramp（eject）带宽取 <b>1</b> 与 <b>2</b> flit/cycle 两种场景。</p>"
             "<p><b>硬约束：所有方案必须严格 0-buffer</b>——所有时隙离线编排，"
             "<b>无冲突</b>（每条有向 link 每 cycle ≤1 flit、每节点上/下 ramp 每 cycle ≤ ramp 带宽）、"
             "<b>无阻塞</b>（全离线、无运行期阻塞）、<b>路由器零缓存</b>（flit 一旦注入即按固定时刻逐跳前进，"
             "中间节点绝不等待；多播扇出按“到达即组合复制/直通”，扇出计入单跳延迟）。唯一自由度是每源的"
             "<b>注入偏移</b>（数据暂存于源 PE/SRAM，<i>非</i>路由器 buffer）。"
             "需缓存才能更快的“时分/流水”方案<b>已全部移除</b>，本报告只比较 0-buffer makespan。"
             "脚本 <code>utils/sched_zerobuf_compare.py</code>，数据 <code>results/zerobuf_16x16.json</code>。</p>"
             f"<p><b>通用下界</b>：每节点都要经其单条下 ramp 下泄 N−1={n-1} 条 flit，"
             "故 makespan ≥ (N−1)/ramp_bw + 最小投递延迟（BW=1→255，BW=2→128）。</p></div>")

    s.append("<div class='card'><h2>参与比较的方案（均 0-buffer）</h2><ul>"
             "<li><b>dimensional multi-tree</b>：每源 X-then-Y 维序多播树（行脊 + 各列分支，网内 fork）。</li>"
             "<li><b>纯 Hamilton 环</b>：全局一个蛇形 comb 闭环，单向 / 双向。</li>"
             "<li><b>hybrid 横带环 + 纵向树</b>：切 B 个<b>水平</b>带（每带 R=MY/B 行），带内跑<b>横向</b> Hamilton 环 allgather，"
             "再每<b>列</b>向上下相邻带做<b>纵向</b>树广播。</li>"
             "<li><b>hybrid 纵带环 + 横向树（新增）</b>：转置版——切 B 个<b>垂直</b>带（每带 C=MX/B 列），带内跑<b>纵向</b> "
             "Hamilton 环 allgather，再每<b>行</b>向左右相邻带做<b>横向</b>树广播。</li>"
             "<li><b>quad 4×(8×8) 环 + 中心交换</b>：4 象限各跑环，最内 4 角构成中心 4-环互传，对端再绕环二次分发。</li>"
             "<li><b>border 4×(8×8) 环 + 边界多点注入</b>：4 象限各跑环，沿共享边界多点跨界 + 短弧分发（亦为多播）。</li>"
             "</ul></div>")

    s.append(scheme_diagrams())
    s.append("\n".join(_summary_card(payload)))
    s.append(insights_section(payload))
    s.append(conclusions_section(payload))

    # bar charts per bandwidth
    rows = collect(payload)
    s.append("<div class='card'><h2>0-buffer makespan 柱状对比</h2>")
    labels = ["multi\ntree", "ring\n单", "ring\n双", "hyb横\n带环", "hyb纵\n带环", "quad\n单", "quad\n双", "border\n单", "border\n双"]
    vals1 = [r[1] for r in rows]
    vals2 = [r[2] for r in rows]
    s.append(bar_chart("BW=1（越低越好）", labels, vals1, lb=payload["bw"]["1"]["eject_lb"]))
    s.append(bar_chart("BW=2（越低越好）", labels, vals2, lb=payload["bw"]["2"]["eject_lb"]))
    s.append("</div>")

    # hybrid B-sweep detail (both orientations)
    s.append("<div class='card'><h2>附：hybrid 两种朝向的 B 扫描明细（0-buffer makespan）</h2>")
    s.append("<h3>横带环 + 纵向树（B 个水平带，R=行/带）</h3>")
    s.append(hyb_sweep_table(payload, "hybrid", "R 行/带"))
    s.append("<h3>纵带环 + 横向树（B 个垂直带，C=列/带）</h3>")
    s.append(hyb_sweep_table(payload, "hybrid_v", "C 列/带"))
    s.append("<p style='color:#64748b;font-size:12px'>“—”表示该带数下子区退化为单行/单列无闭环，未计入。</p>")
    s.append("</div>")

    s.append("</body></html>")
    HTML_PATH.write_text("\n".join(s), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


def _summary_card(payload):
    """Render the summary card HTML (kept separate so collect() is single-sourced)."""
    lb1 = payload["bw"]["1"]["eject_lb"]
    lb2 = payload["bw"]["2"]["eject_lb"]
    rows = collect(payload)
    min1 = min(r[1] for r in rows)
    min2 = min(r[2] for r in rows)

    def cell(val, is_min):
        return f"<td class='win'>{val}</td>" if is_min else f"<td>{val}</td>"

    out = ["<div class='card'><h2>汇总：各方案 0-buffer makespan（按下 Ramp 带宽）</h2>",
           "<p>所有方案均为<b>严格 0-buffer</b>（路由器零缓存，仅源端注入偏移）；只比较 0-buffer makespan"
           "（cycle，越低越好）。绿底＝该带宽最优；hybrid 取最优朝向 / 带数（明细见文末）。</p>",
           "<table><tr><th>方案</th>"
           f"<th>BW=1 makespan<br>(下界 {lb1})</th><th>÷下界</th>"
           f"<th>BW=2 makespan<br>(下界 {lb2})</th><th>÷下界</th><th>最优配置</th></tr>"]
    for label, mk1, mk2, t1, t2 in rows:
        tag = f"BW1: {t1}<br>BW2: {t2}" if (t1 or t2) else ""
        out.append(f"<tr><td>{html.escape(label)}</td>"
                   f"{cell(mk1, mk1 == min1)}<td>{mk1/lb1:.2f}×</td>"
                   f"{cell(mk2, mk2 == min2)}<td>{mk2/lb2:.2f}×</td>"
                   f"<td style='font-size:11px;color:#64748b'>{tag}</td></tr>")
    out.append("</table>")
    out.append("<p style='color:#64748b;font-size:12px'>注：BW=2 列取 min(本带宽解, BW=1 解)——BW=1 的 0-buffer "
               "调度在下泄带宽放宽到 2 时必然仍可行，故更大带宽不会使可达 makespan 变差。</p>")
    out.append("</div>")
    return out


if __name__ == "__main__":
    render()
