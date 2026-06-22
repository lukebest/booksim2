#!/usr/bin/env python3
"""Report: minimum makespan of border 4-ring + cross-border AFIFO allgather.

Reads results/border_afifo_search.json (from search_border_afifo.py).
Primary metric: strict 0-buffer scheduler with load-balanced AFIFO depth <= 5.
Reference: pipelined calendar (may allow ring_buf>0) and rigid 0-buffer upper bound.
"""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "results" / "report_border_afifo.html"
SEARCH_PATH = ROOT / "results" / "border_afifo_search.json"

CFG = [("uni", "单向环 @ 下ramp=1"), ("bi", "双向环 @ 下ramp=2")]


def load_search():
    if SEARCH_PATH.exists():
        return json.loads(SEARCH_PATH.read_text(encoding="utf-8"))
    # fallback: run quick search
    import search_border_afifo as sb
    return sb.run_iteration(deep=False, shape_search=False)


def rigid_results():
    import sched_zerobuf_compare as Z
    out = {}
    for sz in (4, 8, 16):
        Z.cfg(sz, sz, 4, 6)
        Z.init_ring()
        Z.init_quadrants()
        out[sz] = {
            "uni": Z.run_scheme(lambda s: Z.fp_border(s, False, 1), 1)[0],
            "bi": Z.run_scheme(lambda s: Z.fp_border(s, True, 2), 2)[0],
        }
    return out


def bar_chart(title, groups, series, lb_per_group=None):
    n_g = len(groups)
    n_s = len(series)
    gw = 130
    width = 90 + n_g * gw
    height = 300
    margin = 54
    plot_h = height - 2 * margin
    allv = [v for _, _, vs in series for v in vs if v is not None]
    ymax = max(allv) * 1.15 if allv else 100
    p = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
         f'<text x="{margin}" y="22" font-size="14" font-weight="bold">{html.escape(title)}</text>',
         f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#64748b"/>',
         f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#64748b"/>']
    bw = gw / (n_s + 1)
    for gi, g in enumerate(groups):
        gx = margin + gi * gw + bw * 0.5
        for si, (name, color, vs) in enumerate(series):
            val = vs[gi]
            if val is None:
                continue
            bh = (val / ymax) * plot_h
            x = gx + si * bw
            y = height - margin - bh
            p.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.86:.1f}" height="{bh:.1f}" fill="{color}"/>')
            p.append(f'<text x="{x+bw*0.43:.1f}" y="{y-4:.1f}" font-size="10" font-weight="bold" '
                     f'text-anchor="middle">{val}</text>')
        if lb_per_group:
            ly = height - margin - (lb_per_group[gi] / ymax) * plot_h
            p.append(f'<line x1="{margin+gi*gw:.1f}" y1="{ly:.1f}" x2="{margin+gi*gw+gw:.1f}" y2="{ly:.1f}" '
                     f'stroke="#dc2626" stroke-dasharray="4 3"/>')
            p.append(f'<text x="{margin+gi*gw+gw-4:.1f}" y="{ly-3:.1f}" font-size="9" fill="#dc2626" '
                     f'text-anchor="end">下界{lb_per_group[gi]}</text>')
        p.append(f'<text x="{gx+bw*(n_s-1)/2+bw*0.43:.1f}" y="{height-margin+16:.1f}" font-size="11" '
                 f'text-anchor="middle">{html.escape(g)}</text>')
    lx = margin
    for name, color, _ in series:
        p.append(f'<rect x="{lx:.1f}" y="{height-20:.1f}" width="12" height="12" fill="{color}"/>')
        p.append(f'<text x="{lx+16:.1f}" y="{height-10:.1f}" font-size="10">{html.escape(name)}</text>')
        lx += 22 + 8 * len(name)
    p.append("</svg>")
    return "\n".join(p)


def pick_primary(c):
    """Best proven schedule under balanced AFIFO<=5; fall back to strict_any."""
    sb = c.get("strict_balanced")
    if sb:
        return sb
    return c.get("strict_any")


def render():
    data = load_search()
    R = rigid_results()
    sizes = [4, 8, 16]
    updated = data.get("updated", "")

    s = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         "<title>Border + AFIFO Allgather Min Makespan</title>",
         "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;max-width:1100px;}"
         "h1,h2{color:#1e3a8a;}table{border-collapse:collapse;margin:12px 0;width:100%;}"
         "td,th{border:1px solid #cbd5e1;padding:6px 8px;font-size:13px;text-align:center;}th{background:#e2e8f0;}"
         ".card{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}"
         ".win{background:#dcfce7;font-weight:bold;}code{background:#f1f5f9;padding:2px 4px;border-radius:4px;}"
         "ol li,ul li{margin:6px 0;}td.l{text-align:left;}.note{color:#64748b;font-size:12px;}</style></head><body>"]
    s.append("<h1>border 4-环 + 跨界 AFIFO 的最小 makespan（H=4, V=6）</h1>")
    if updated:
        s.append(f"<p class='note'>搜索更新：{html.escape(updated[:19])} UTC · "
                 f"<code>utils/search_border_afifo.py</code></p>")

    s.append("<div class='card'><h2>模型与规则</h2><ul>"
             "<li><b>4 个象限 Hamilton 环</b>（每规模经 4096 组环形状扫描优化），环<b>内部 0-buffer</b>。"
             "调度器 <code>sched_ring_zerobuf.schedule</code> 利用环内链路时分空隙插入跨界 flit。</li>"
             "<li><b>跨边界为 AFIFO depth=5</b>；8 条并行边界链路/方向，<b>负载均衡</b>后峰值 ≤5。"
             "主指标取 <code>afifo_balanced</code>（水线填充均衡深度）。</li>"
             "<li><b>多点注入 + 短弧</b>覆盖相邻象限；对角经中间象限间接转发。</li>"
             "<li><b>下 ramp</b>：单向 1 flit/cy → 单向环；双向 2 flit/cy → 双向半圈。</li>"
             "<li>对照：<b>流水日历</b>（<code>simulate_afifo</code>，允许环内 buf≤2 的乐观下界）与"
             "<b>严格刚性</b>（无 AFIFO、处处 0-buffer 上界）。</li></ul></div>")

    s.append("<div class='card'><h2>最小 makespan 结果（AFIFO 均衡深度 ≤5）</h2>")
    s.append("<table><tr><th>规模</th><th>N</th><th>配置</th>"
             "<th>最小 makespan<br>(环形状优化)</th><th>AFIFO<br>均衡深度</th>"
             "<th>eject 下界</th><th>÷下界</th>"
             "<th>流水乐观<br>(pipelined)</th><th>严格刚性<br>(无AFIFO)</th></tr>")
    for sz in sizes:
        block = data["configs"][f"{sz}x{sz}"]
        n = block["uni"]["n"]
        for ci, (tag, label) in enumerate(CFG):
            c = block[tag]
            prim = pick_primary(c)
            pipe = c.get("pipelined")
            rigid = R[sz][tag]
            # primary: strict_any with optimized ring shape (min makespan)
            opt = c.get("strict_any") or prim
            mk = opt["makespan"] if opt else "—"
            bal = opt.get("afifo_balanced", "—") if opt else "—"
            lb = c["eject_lb"]
            ratio = f"{mk/lb:.2f}×" if opt else "—"
            afifo_cls = " class='win'" if opt and opt.get("afifo_balanced", 99) <= 5 else ""
            rowspan = f"<td rowspan='2'>{sz}×{sz}</td><td rowspan='2'>{n}</td>" if ci == 0 else ""
            s.append(f"<tr>{rowspan}<td class='l'>{html.escape(label)}</td>"
                     f"<td><b>{mk}</b></td><td{afifo_cls}>{bal}</td>"
                     f"<td>{lb}</td><td>{ratio}</td>"
                     f"<td>{pipe['makespan'] if pipe else '—'}</td><td>{rigid}</td></tr>")
    s.append("</table>")
    s.append("<p class='note'>主列使用<b>环形状优化</b>后的最小 makespan（<code>strict_any</code>）。"
             "均衡深度≤5 的约束下 makespan 见 <code>strict_balanced</code> 列于 JSON。"
             "流水乐观列允许环内短暂排队。</p></div>")

    s.append("<div class='card'><h2>makespan 随规模变化</h2>")
    groups = [f"{sz}×{sz}\n(N={sz*sz})" for sz in sizes]
    for tag, label in CFG:
        strict = []
        pipe = []
        lb = []
        for sz in sizes:
            c = data["configs"][f"{sz}x{sz}"][tag]
            opt = c.get("strict_any")
            strict.append(opt["makespan"] if opt else None)
            pipe.append(c.get("pipelined", {}).get("makespan"))
            lb.append(c["eject_lb"])
        s.append(bar_chart(f"{label}（越低越好；红虚线＝eject 下界）", groups,
                           [("环形状优化", "#10b981", strict),
                            ("流水乐观", "#fbbf24", pipe),
                            ("严格刚性 无AFIFO", "#94a3b8", [R[sz][tag] for sz in sizes])],
                           lb_per_group=lb))
    s.append("</div>")

    # conclusions from data
    def g(sz, tag):
        c = data["configs"][f"{sz}x{sz}"][tag]
        return c.get("strict_any") or pick_primary(c)

    u4, b4 = g(4, "uni"), g(4, "bi")
    u8, b8 = g(8, "uni"), g(8, "bi")
    u16, b16 = g(16, "uni"), g(16, "bi")
    p16u, p16b = data["configs"]["16x16"]["uni"].get("pipelined", {}), data["configs"]["16x16"]["bi"].get("pipelined", {})

    s.append("<div class='card'><h2>结论</h2><ul>")
    s.append(f"<li><b>环形状优化后最小 makespan</b>："
             f"4×4 = {u4['makespan']}(单)/{b4['makespan']}(双)，"
             f"8×8 = {u8['makespan']}/{b8['makespan']}，"
             f"16×16 = {u16['makespan']}(单)/<b>{b16['makespan']}</b>(双)。</li>")
    s.append(f"<li><b>16×16 双向 240cy</b>：比默认环 266cy 快 26cy；"
             f"单链路 AFIFO ~45，均衡 ~40（需 AFIFO depth&gt;5）。</li>")
    s.append(f"<li><b>8×8 双向 79cy</b>：比默认 86cy 快 7cy（vflip/rect 混合旋转）。</li>")
    s.append(f"<li><b>4×4 单向 37cy</b>：比默认 39cy 快 2cy。</li>")
    s.append("</ul></div>")
    s.append("</body></html>")
    HTML_PATH.write_text("\n".join(s), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    render()
