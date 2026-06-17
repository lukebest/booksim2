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

    s.append("<div class='card'><h2>三种方案</h2><ul>"
             "<li><b>dimensional multi-tree</b>：每个源用 X-then-Y 维序多播树（行脊 + 各列分支，网内 fork），"
             "带 buffer 时可命中下界，是已有最优方案。</li>"
             "<li><b>纯 Hamilton 环</b>：全局一个 Hamilton 环（蛇形 comb 闭环），单向 / 双向。</li>"
             "<li><b>hybrid 局部环 + 全局树</b>：按行切 B 个水平带（每带 R=MY/B 行），"
             "①带内跑局部 Hamilton 环 allgather（各带并行）；②每列向上下相邻带做树状广播，把各带块互换。</li>"
             "</ul></div>")

    # per-bw tables
    for bk in bw_keys:
        d = payload["bw"][bk]
        rb = int(bk)
        lb = d["eject_lb"]
        mt = d["multitree"]["makespan"]
        bu_B, bu = best_hybrid(d, "hybrid_uni")
        bb_B, bb = best_hybrid(d, "hybrid_bi")
        best_overall = min(mt, d["ring_uni"]["makespan"], d["ring_bi"]["makespan"], bu, bb)

        s.append(f"<div class='card'><h2>结果：下 Ramp 带宽 = {rb} flit/cycle（eject 下界 = {lb}）</h2>")
        s.append("<table><tr><th>方案</th><th>0-buffer makespan</th><th>vs 下界</th><th>vs multi-tree</th></tr>")
        for name, mk in [("multitree", mt), ("ring_uni", d["ring_uni"]["makespan"]),
                         ("ring_bi", d["ring_bi"]["makespan"]),
                         (f"hybrid_uni", bu), (f"hybrid_bi", bb)]:
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

        labels = ["multi-tree", "ring 单向", "ring 双向",
                  f"hybrid 单向\nB={bu_B}", f"hybrid 双向\nB={bb_B}"]
        values = [mt, d["ring_uni"]["makespan"], d["ring_bi"]["makespan"], bu, bb]
        s.append(bar_chart(f"0-buffer makespan @ ramp_bw={rb}（越低越好）", labels, values, lb=lb))
        s.append("</div>")

    # conclusions
    d1, d2 = payload["bw"]["1"], payload["bw"]["2"]
    mt1, mt2 = d1["multitree"]["makespan"], d2["multitree"]["makespan"]
    b1B, b1 = min([(int(B), v["makespan"]) for B, v in d1["hybrid_bi"].items()] +
                  [(int(B), v["makespan"]) for B, v in d1["hybrid_uni"].items()], key=lambda t: t[1])
    bu2_B, bu2 = best_hybrid(d2, "hybrid_uni")
    s.append("<div class='card'><h2>结论</h2><ul>"
             f"<li><b>0-buffer 约束下，hybrid 反而最优</b>：BW=1 时 hybrid 最优 ≈ <b>{b1}</b> "
             f"(B={b1B})，比 multi-tree 的 {mt1} 快 <b>{mt1/b1:.2f}×</b>，比纯环（{d1['ring_uni']['makespan']}/{d1['ring_bi']['makespan']}）更快；"
             f"BW=2 时 hybrid 最优 ≈ <b>{bu2}</b> (单向 B={bu2_B})，优于 multi-tree {mt2}。</li>"
             "<li><b>为什么带 buffer 最强的 multi-tree 在 0-buffer 下变弱？</b> multi-tree 每个源的足迹横跨整张网"
             f"（直径 = {(mx-1)*h+(my-1)*v} cycle），刚性时隙铺得又宽又满，单偏移很难把 256 个宽足迹彼此错开，"
             "贪心打包后被迫拉大注入偏移 → makespan 远离下界（约 3× 下界）。</li>"
             "<li><b>hybrid 赢在“局部性”</b>：带内小环 + 短纵向树的足迹紧凑（时间跨度小、占用稀疏），"
             "刚性打包时彼此更易错开，因此更接近 eject 下界（最优档约 1.6× 下界）。"
             "存在最优带数：B 太小→局部环过长（足迹宽），B 太大→全局树纵向跨度与下泄量上升；二者权衡出谷底。</li>"
             "<li><b>纯环</b>：单向环足迹长（绕行整周），始终最差（1474）；双向环减半（754），但仍不及 hybrid。</li>"
             "<li><b>带宽加倍（1→2）</b>对所有方案都有效（下界 255→128），multi-tree 与 hybrid 均显著下降；"
             "hybrid 的最优带数随带宽变化（BW=1 偏向较少带、双向局部环；BW=2 偏向较多带、单向局部环）。</li>"
             "<li><b>选型</b>：若网络<b>允许路由器 buffer</b>，multi-tree（带 buffer 命中下界）最优；"
             "若硬性要求<b>0 buffer / 完全刚性时隙</b>，<b>hybrid 局部环+全局树</b>是更好的选择——"
             "局部性让它在无缓存约束下既可调度又更快。</li>"
             "</ul>"
             "<p style='color:#64748b;font-size:12px'>注：0-buffer makespan 为“多种源排序贪心刚性打包”的可行上界（已验证无冲突 + 0 buffer + 每节点下泄 N−1）；"
             "脚本 <code>utils/sched_zerobuf_compare.py</code>，数据缓存 <code>results/zerobuf_16x16.json</code>。</p></div>")

    s.append("</body></html>")
    HTML_PATH.write_text("\n".join(s), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    render()
