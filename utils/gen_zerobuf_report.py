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
        f"<li><b>边界 + 双向最佳</b>：BW=1 {bb1['makespan']}、BW=2 <b>{bb2['makespan']}</b>，"
        f"已逼近 eject 下界区间（{(256-1)//1}/{(256-1+1)//2}），并优于时分中心方案与严格 0-buffer 各方案。</li>"
        "<li><b>代价</b>：边界多点注入用满了象限共享边界的 8 条跨界链路并在对端做行/列分发——"
        "本质上是把“环 + 局部树”结合，结构比“中心 4 环”略复杂，但仍是规整的象限化布局。</li>"
        "</ul></div>")
    return "\n".join(out)


def render():
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    mx, my, h, v, n = payload["mx"], payload["my"], payload["h"], payload["v"], payload["n"]
    bw_keys = sorted(payload["bw"].keys(), key=int)

    s = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         "<title>16x16 Zero-buffer Allgather Comparison</title>",
         "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;max-width:1100px;}"
         "h1,h2{color:#1e3a8a;}table{border-collapse:collapse;margin:12px 0;width:100%;}"
         "td,th{border:1px solid #cbd5e1;padding:6px 8px;font-size:13px;}th{background:#e2e8f0;}"
         ".card{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}"
         ".win{background:#dcfce7;font-weight:bold;}code{background:#f1f5f9;padding:2px 4px;border-radius:4px;}</style></head><body>"]
    s.append(f"<h1>{mx}×{my} Mesh Allgather：局部 Hamilton 环 + 全局树广播 对比</h1>")

    s.append("<div class='card'><h2>问题设定</h2>"
             f"<p>{mx}×{my} mesh（{n} 节点）。横向 link delay <b>H={h}</b> cycle，纵向 <b>V={v}</b> cycle，"
             "PE↔router ramp 延迟 1 cycle。下 Ramp（eject）带宽分别取 <b>1</b> 与 <b>2</b> flit/cycle 两种场景。"
             "msg_size=1。</p>"
             "<p><b>硬约束（三方案均满足）</b>：所有时隙离线编排，"
             "<b>无冲突</b>（每条有向 link 每 cycle ≤1 flit、每节点下/上 ramp 每 cycle ≤ ramp 带宽）、"
             "<b>无阻塞</b>（全离线、无运行期阻塞/死锁）、"
             "<b>0 buffer</b>（网络内部路由器零缓存：每条 flit 一旦注入即按固定时刻逐跳前进，中间节点绝不等待）。</p>"
             "<p><b>0-buffer 刚性模型</b>：在单个源的投递结构（环/树）内，link(p→c) 恒在 "
             "<code>inject<sub>s</sub>+ramp+dist(s,p)</code> 占用、节点 d 恒在 <code>inject<sub>s</sub>+ramp+dist(s,d)</code> 下泄，"
             "其中 dist 为该结构上的真实 H/V 跳延迟之和；唯一自由度是每个源的<b>注入偏移 inject<sub>s</sub></b>"
             "（数据暂存在源 PE/SRAM，<i>非</i>路由器 buffer）。贪心地为每个源选择最小的、与已排源不冲突的偏移，"
             "即<b>构造性地</b>保证无冲突 + 无阻塞 + 0 buffer（取多种排序中的最优结果）。</p>"
             f"<p><b>通用下界</b>：每个节点都必须经其单条下 ramp 下泄 N−1={n-1} 条 flit，"
             "故任何方案 makespan ≥ (N−1)/ramp_bw + 最小投递延迟。</p></div>")

    s.append("<div class='card'><h2>四类方案</h2><ul>"
             "<li><b>dimensional multi-tree</b>：每个源用 X-then-Y 维序多播树（行脊 + 各列分支，网内 fork），"
             "带 buffer 时可命中下界，是已有最优方案。</li>"
             "<li><b>纯 Hamilton 环</b>：全局一个 Hamilton 环（蛇形 comb 闭环），单向 / 双向。</li>"
             "<li><b>hybrid 局部环 + 全局树</b>：按行切 B 个水平带（每带 R=MY/B 行），"
             "①带内跑局部 Hamilton 环 allgather（各带并行）；②每列向上下相邻带做树状广播，把各带块互换。</li>"
             "<li><b>quad 4×(8×8) 环 + 中心交换</b>：把 16×16 切成 4 个 8×8 象限，各自构造 Hamilton 环做象限内 allgather；"
             "4 个象限的<b>最内角节点</b> (7,7)/(8,7)/(8,8)/(7,8) 在中心恰好构成一个 4-环，"
             "各象限块经此中心 4-环<b>时分</b>互传（每条中心链路逐 cycle 轮流承载不同源的 flit），"
             "对端象限再沿本象限环二次环绕分发，使每个节点都拿到全部 4 象限数据。</li>"
             "</ul></div>")

    # per-bw tables
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

        s.append(f"<div class='card'><h2>结果：下 Ramp 带宽 = {rb} flit/cycle（eject 下界 = {lb}）</h2>")
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

        # B sweep
        s.append("<h3>hybrid 带数 B 扫描</h3><table><tr><th>B 带数</th><th>R 行/带</th>"
                 "<th>单向局部环</th><th>双向局部环</th></tr>")
        allB = sorted({int(b) for b in list(d["hybrid_uni"]) + list(d["hybrid_bi"])})
        for B in allB:
            ru = d["hybrid_uni"].get(str(B), {}).get("makespan")
            rbi = d["hybrid_bi"].get(str(B), {}).get("makespan")
            s.append(f"<tr><td>{B}</td><td>{my//B}</td>"
                     f"<td>{ru if ru is not None else '—（单行无环）'}</td>"
                     f"<td>{rbi if rbi is not None else '—'}</td></tr>")
        s.append("</table>")

        labels = ["multi-tree", "ring\n双向", f"hybrid\nB={bu_B}(单)", f"hybrid\nB={bb_B}(双)",
                  "quad 单向", "quad 双向"]
        values = [mt, d["ring_bi"]["makespan"], bu, bb, qu, qb]
        s.append(bar_chart(f"0-buffer makespan @ ramp_bw={rb}（越低越好）", labels, values, lb=lb))
        s.append("</div>")

    s.append(fused_section())

    # conclusions
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]
    mt1, mt2 = d1["multitree"]["makespan"], d2["multitree"]["makespan"]
    b1B, b1 = min([(int(B), v["makespan"]) for B, v in d1["hybrid_bi"].items()] +
                  [(int(B), v["makespan"]) for B, v in d1["hybrid_uni"].items()], key=lambda t: t[1])
    bu2_B, bu2 = best_hybrid(d2, "hybrid_uni")
    q1 = min(d1["quad_uni"]["makespan"], d1["quad_bi"]["makespan"])
    q2 = min(d2["quad_uni"]["makespan"], d2["quad_bi"]["makespan"])
    s.append("<div class='card'><h2>结论</h2><ul>"
             f"<li><b>0-buffer 约束下，hybrid（行带）整体最优</b>：BW=1 时 hybrid 最优 ≈ <b>{b1}</b> "
             f"(B={b1B})，比 multi-tree 的 {mt1} 快 <b>{mt1/b1:.2f}×</b>，比纯环（{d1['ring_uni']['makespan']}/{d1['ring_bi']['makespan']}）更快；"
             f"BW=2 时 hybrid 最优 ≈ <b>{bu2}</b> (单向 B={bu2_B})，优于 multi-tree {mt2}。</li>"
             f"<li><b>quad 4×(8×8)环 + 中心交换：可行且居中</b>。单向 {d1['quad_uni']['makespan']}（BW 无关），"
             f"双向 BW=1 {d1['quad_bi']['makespan']} / BW=2 {d2['quad_bi']['makespan']}。"
             f"它优于纯环、与 multi-tree 同档（BW=1 时 quad 单向 {d1['quad_uni']['makespan']} 还略快于 multi-tree {mt1}），"
             f"但不及最佳行带 hybrid。原因：<b>中心 4-环是唯一的跨象限通道</b>——每条中心链路要时分承载 ~2 个象限块（≈128 flit），"
             "且每条 flit 要走“本象限环 + 中心跳 + 对端象限环”两段绕行，<b>延迟受象限环周长主导</b>；"
             "单向 quad 因此被绕行延迟卡在 717（加带宽也不降，瓶颈是延迟非下泄）。</li>"
             "<li><b>为什么带 buffer 最强的 multi-tree 在 0-buffer 下变弱？</b> multi-tree 每个源的足迹横跨整张网"
             f"（直径 = {(mx-1)*h+(my-1)*v} cycle），刚性时隙铺得又宽又满，单偏移很难把 256 个宽足迹彼此错开，"
             "贪心打包后被迫拉大注入偏移 → makespan 远离下界（约 3× 下界）。</li>"
             "<li><b>共同规律——局部性决定 0-buffer 表现</b>：足迹越紧凑（时间跨度小、占用稀疏）越易刚性错开。"
             "行带 hybrid 足迹最紧凑（最优档 ≈1.6× 下界）；quad 次之（两段象限环绕行偏长）；"
             "multi-tree 足迹最宽（≈3× 下界）。</li>"
             "<li><b>纯环</b>：单向环足迹长（绕行整周），始终最差（1474）；双向环减半（754）。</li>"
             "<li><b>带宽加倍（1→2）</b>仅对“受下泄约束”的方案有效（下界 255→128）：multi-tree、hybrid、quad-双向 均下降；"
             "而 quad-单向受<b>延迟</b>约束，加带宽无效（恒 717）。</li>"
             "<li><b>选型</b>：允许路由器 buffer → multi-tree（命中下界）最优；硬性 0-buffer/刚性时隙 → "
             "<b>行带 hybrid</b> 最优，<b>quad 4×(8×8)+中心交换</b> 是结构规整、布局对称的次优折中（适合物理上天然 4 象限的版图）。</li>"
             "</ul>"
             "<p style='color:#64748b;font-size:12px'>注：0-buffer makespan 为“多种源排序贪心刚性打包”的可行上界（已验证无冲突 + 0 buffer + 每节点下泄 N−1）；"
             "脚本 <code>utils/sched_zerobuf_compare.py</code>，数据缓存 <code>results/zerobuf_16x16.json</code>。</p></div>")

    s.append("</body></html>")
    HTML_PATH.write_text("\n".join(s), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    render()
