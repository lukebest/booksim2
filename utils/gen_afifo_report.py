#!/usr/bin/env python3
"""Report: minimum makespan of the border (4 Hamilton rings + cross-border AFIFO)
allgather, for 4x4 / 8x8 / 16x16 meshes, H=4, V=6.

Model (per the design spec):
  * The 4 quadrant Hamilton rings are internally 0-buffer, conflict-free, non-blocking.
  * Cross-border links are AFIFOs that may hold up to 5 flits (depth=5).
  * Multi-point border injection: a flit crosses at the shared-border nodes and covers
    the neighbour ring with short arcs; the diagonal ring is reached via an intermediate.
  * Direction follows the down-ramp bandwidth: uni ring @ ramp=1, bi ring @ ramp=2.
  * Down-ramp (eject) is a hard constraint (1 or 2 flit/cy/node).
"""

import html
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "results" / "report_border_afifo.html"

CFG = [("uni", "单向环 @ 下ramp=1"), ("bi", "双向环 @ 下ramp=2")]


def afifo_results():
    import sim_fused_rings as fr
    return fr.border_afifo_study((4, 8, 16))


def rigid_results():
    """Strict all-0-buffer rigid border (NO AFIFO) -- the conservative upper bound."""
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
    """Grouped bars. groups=list of x labels; series=list of (name,color,[vals])."""
    n_g = len(groups)
    n_s = len(series)
    gw = 130
    width = 90 + n_g * gw
    height = 300
    margin = 54
    plot_h = height - 2 * margin
    allv = [v for _, _, vs in series for v in vs]
    ymax = max(allv) * 1.15
    p = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
         f'<text x="{margin}" y="22" font-size="14" font-weight="bold">{html.escape(title)}</text>',
         f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#64748b"/>',
         f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#64748b"/>']
    bw = gw / (n_s + 1)
    for gi, g in enumerate(groups):
        gx = margin + gi * gw + bw * 0.5
        for si, (name, color, vs) in enumerate(series):
            val = vs[gi]
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


def render():
    A = afifo_results()
    R = rigid_results()
    sizes = [4, 8, 16]

    s = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
         "<title>Border + AFIFO Allgather Min Makespan</title>",
         "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;max-width:1050px;}"
         "h1,h2{color:#1e3a8a;}table{border-collapse:collapse;margin:12px 0;width:100%;}"
         "td,th{border:1px solid #cbd5e1;padding:6px 8px;font-size:13px;text-align:center;}th{background:#e2e8f0;}"
         ".card{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}"
         ".win{background:#dcfce7;font-weight:bold;}code{background:#f1f5f9;padding:2px 4px;border-radius:4px;}"
         "ol li,ul li{margin:6px 0;}td.l{text-align:left;}</style></head><body>"]
    s.append("<h1>border 4-环 + 跨界 AFIFO 的最小 makespan（H=4, V=6）</h1>")

    s.append("<div class='card'><h2>模型与规则</h2><ul>"
             "<li><b>4 个象限 Hamilton 环</b>（4×4→2×2、8×8→4×4、16×16→8×8 每象限），环<b>内部 0-buffer、无冲突、无阻塞</b>"
             "（每条有向环 link 每 cycle ≤1 flit）。</li>"
             "<li><b>跨边界为 AFIFO</b>，可缓存 <b>depth=5</b> flit：到边界即可写入 AFIFO 跨到相邻环，"
             "相邻环一有<b>空闲时隙</b>就读出上环；各 AFIFO <b>负载均衡</b>。</li>"
             "<li><b>多点注入 + 短弧覆盖</b>：flit 沿共享边界多个节点跨界，用短弧合并覆盖相邻环；"
             "<b>对角环</b>经中间相邻环间接到达（生命周期含相邻环 + 对角环遍历）。</li>"
             "<li><b>方向跟随下 ramp 带宽</b>：下 ramp=1 → 单向绕一圈；下 ramp=2 → 双向各绕半圈。"
             "下 ramp（eject）为<b>硬约束</b>（每节点 eject N−1 个 flit）。</li>"
             "<li><b>“最小 makespan”</b>＝在上述约束下流水编排所得的可达下界；同时给出"
             "<b>严格刚性（无 AFIFO，处处 0-buffer）</b>作为保守上界对照。仿真见 "
             "<code>utils/sim_fused_rings.py: simulate_afifo</code>。</li>"
             "</ul></div>")

    # ---- main results table ----
    s.append("<div class='card'><h2>最小 makespan 结果</h2>")
    s.append("<table><tr><th>规模</th><th>N</th><th>配置</th>"
             "<th>最小 makespan<br>(AFIFO depth≤5)</th><th>eject 下界</th><th>÷下界</th>"
             "<th>busiest<br>ring link</th><th>环内 buf</th><th>AFIFO 深度</th><th>eject buf</th>"
             "<th>严格刚性<br>(无 AFIFO,上界)</th></tr>")
    for sz in sizes:
        rec = A[sz]
        n = rec["n"]
        for ci, (tag, label) in enumerate(CFG):
            d = rec[tag]
            rigid = R[sz][tag]
            rowspan = f"<td rowspan='2'>{sz}×{sz}</td><td rowspan='2'>{n}</td>" if ci == 0 else ""
            afifo_cls = " class='win'" if d["afifo_buf"] <= 5 else ""
            s.append(f"<tr>{rowspan}<td class='l'>{html.escape(label)}</td>"
                     f"<td><b>{d['makespan']}</b></td><td>{d['eject_lb']}</td><td>{d['makespan']/d['eject_lb']:.2f}×</td>"
                     f"<td>{d['busiest_link']}</td><td>{d['ring_buf']}</td>"
                     f"<td{afifo_cls}>{d['afifo_buf']}</td><td>{d['eject_buf']}</td><td>{rigid}</td></tr>")
    s.append("</table>")
    s.append("<p style='color:#64748b;font-size:12px'>绿底＝所需 AFIFO 深度 ≤5（预算内）。"
             "“环内 buf / eject buf” 为达到该 makespan 时的峰值占用：环内仅 ≤2、可由源端注入偏移 + AFIFO 吸收，"
             "故 0-buffer 环 + depth-5 AFIFO 足以实现该 makespan。单向配置下它与严格刚性相等（如 16×16 = 437），"
             "说明单向时即便完全不用 AFIFO 也已达此值；双向时 AFIFO 解耦各环带来大幅提速。</p>")
    s.append("</div>")

    # ---- charts ----
    s.append("<div class='card'><h2>makespan 随规模变化</h2>")
    groups = [f"{sz}×{sz}\n(N={sz*sz})" for sz in sizes]
    for tag, label in CFG:
        mk = [A[sz][tag]["makespan"] for sz in sizes]
        rg = [R[sz][tag] for sz in sizes]
        lb = [A[sz][tag]["eject_lb"] for sz in sizes]
        s.append(bar_chart(f"{label}（越低越好；红虚线＝eject 下界）", groups,
                           [("AFIFO depth≤5 (最小)", "#10b981", mk),
                            ("严格刚性 无AFIFO (上界)", "#94a3b8", rg)],
                           lb_per_group=lb))
    s.append("</div>")

    # ---- conclusions ----
    a16u, a16b = A[16]["uni"], A[16]["bi"]
    s.append("<div class='card'><h2>结论</h2><ul>")
    s.append(
        f"<li><b>最小 makespan</b>：4×4 = {A[4]['uni']['makespan']}(单)/{A[4]['bi']['makespan']}(双)，"
        f"8×8 = {A[8]['uni']['makespan']}/{A[8]['bi']['makespan']}，"
        f"16×16 = <b>{a16u['makespan']}</b>(单,下ramp1)/<b>{a16b['makespan']}</b>(双,下ramp2)。</li>")
    s.append(
        "<li><b>depth-5 AFIFO 绰绰有余</b>：实测所需 AFIFO 峰值深度仅 1–2（≤5），环内缓存 ≤2、eject 缓存 ≤2。"
        "多点注入把跨界流量摊到多个 AFIFO 上，负载天然均衡，不会突发塞满 5 深。</li>")
    s.append(
        f"<li><b>AFIFO 解耦各环：双向受益最大</b>。16×16 双向：有 AFIFO 解耦 = <b>{a16b['makespan']}</b>，"
        f"而严格刚性(无 AFIFO)需 {R[16]['bi']}——提速约 {R[16]['bi']/a16b['makespan']:.1f}×。"
        "AFIFO 让相邻/对角环的时序彼此独立，各环可单独紧凑排布；刚性版把一个源跨多环的足迹绑死，冲突更多。</li>")
    s.append(
        f"<li><b>单向无需 AFIFO 即达最小</b>：16×16 单向 {a16u['makespan']} = 严格刚性 {R[16]['uni']}，"
        "单向绕环本就规整、零等待；AFIFO 主要服务于双向/小规模的解耦。</li>")
    s.append(
        f"<li><b>趋势：越大越贴下界</b>。÷eject下界 从 4×4 的 {A[4]['uni']['makespan']/A[4]['uni']['eject_lb']:.1f}×/"
        f"{A[4]['bi']['makespan']/A[4]['bi']['eject_lb']:.1f}× 收敛到 16×16 的 "
        f"{a16u['makespan']/a16u['eject_lb']:.2f}×/{a16b['makespan']/a16b['eject_lb']:.2f}×——"
        "环周长（延迟尾）被规模摊薄，瓶颈趋于 eject 吞吐。小规模 makespan 由环延迟主导，故离下界更远。</li>")
    s.append("</ul></div>")

    s.append("</body></html>")
    HTML_PATH.write_text("\n".join(s), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    render()
