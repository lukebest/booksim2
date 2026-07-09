#!/usr/bin/env python3
"""Research report: near-optimal allreduce across mesh scale x message size.

Reads results/allreduce_scale_sweep.json and results/allreduce_lb.json,
renders heatmaps, quadrant analysis, and recommendation tables.

Output: results/report_allreduce_scale.html
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWEEP_JSON = ROOT / "results" / "allreduce_scale_sweep.json"
LB_JSON = ROOT / "results" / "allreduce_lb.json"
HTML_PATH = ROOT / "results" / "report_allreduce_scale.html"

SIZE_ORDER = ["4x4", "6x8", "8x8", "12x16", "16x16"]
FLITS = [1, 2, 3, 4, 5]

QUAD_COLOR = {
    "inc/tree_bcast": "#2563eb",
    "inc/rs_ag": "#059669",
    "node/tree_bcast": "#d97706",
    "node/rs_ag": "#dc2626",
}

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; }
body { font-family: system-ui, -apple-system, sans-serif; margin:0; padding:24px 32px 56px;
       background:var(--bg); color:var(--text); line-height:1.6; max-width:1120px; }
h1 { font-size:1.6rem; margin:0 0 6px; }
h2 { font-size:1.15rem; margin:28px 0 10px; color:#1e3a8a; border-top:1px solid #e2e8f0; padding-top:20px; }
h3 { font-size:1.0rem; margin:16px 0 8px; color:#334155; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:10px;
        padding:20px 24px; margin:16px 0; }
.meta { color:var(--muted); font-size:.9rem; }
.note { color:var(--muted); font-size:.87rem; }
code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:.85em; }
table.data { border-collapse:collapse; font-size:.82rem; margin:12px 0; width:100%; }
table.data th, table.data td { border:1px solid #e2e8f0; padding:5px 8px; text-align:center; }
table.data th { background:#f1f5f9; }
table.data td.name { text-align:left; }
.table-wrap { overflow-x:auto; margin:12px 0; }
.legend { display:flex; align-items:center; gap:8px; margin:10px 0; font-size:.85rem; flex-wrap:wrap; }
.legend-bar { width:220px; height:14px; border-radius:4px; border:1px solid #cbd5e1; }
.chip { display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:6px; vertical-align:-1px; }
ul.compact li { margin:3px 0; }
.formula { font-family: ui-monospace, monospace; background:#f8fafc; border:1px solid #e2e8f0;
           border-radius:6px; padding:8px 12px; margin:6px 0; font-size:.86rem; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:800px) { .grid2 { grid-template-columns:1fr; } }
"""


def esc(s):
    return html.escape(str(s))


def ratio_color(r, rmin, rmax):
    if rmax <= rmin:
        t = 0.0
    else:
        t = max(0.0, min(1.0, (r - rmin) / (rmax - rmin)))
    hue = 145 * (1 - t)
    return f"hsl({hue:.0f}, 72%, {42 + 18 * (1 - t):.0f}%)"


def quad_key(cell):
    bo = cell.get("best_overall")
    if not bo:
        return None
    return f"{bo.get('reduce_mode','?')}/{bo.get('algo','?')}"


def cell_ratio(cell, mode=None):
    """Pick algorithm-appropriate lower bound for ratio display."""
    if mode:
        md = cell.get(mode, {})
        b = md.get("best")
        if not b:
            return None, None
        lb = md["lower_bound_rsag"] if b.get("algo") == "rs_ag" else md["lower_bound"]
        return b["makespan"] / lb if lb else None, lb

    bo = cell.get("best_overall")
    if not bo:
        return None, None
    md = cell.get(bo["reduce_mode"], {})
    lb = md["lower_bound_rsag"] if bo.get("algo") == "rs_ag" else md["lower_bound"]
    return bo["makespan"] / lb if lb else None, lb


def heatmap_svg(col_labels, row_labels, cell_fn, title):
    pad_l, pad_t, pad_r, pad_b = 100, 34, 20, 40
    cw, ch = 96, 46
    ncols, nrows = len(col_labels), len(row_labels)
    W = pad_l + ncols * cw + pad_r
    H = pad_t + nrows * ch + pad_b
    title_x = pad_l + ncols * cw / 2
    parts = [
        f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;height:auto;display:block">',
        f'<text x="{title_x:.0f}" y="20" text-anchor="middle" '
        f'font-size="12.5" font-weight="600" fill="#334155">{esc(title)}</text>',
        f'<text x="18" y="{pad_t + nrows * ch / 2:.0f}" text-anchor="middle" '
        f'font-size="11" fill="#64748b" transform="rotate(-90 18,{pad_t + nrows * ch / 2:.0f})">'
        f'mesh 规模</text>',
    ]
    for j, cl in enumerate(col_labels):
        x = pad_l + j * cw + cw / 2
        parts.append(f'<text x="{x:.0f}" y="{pad_t - 8}" text-anchor="middle" '
                     f'font-size="12" fill="#475569">{esc(cl)}</text>')
    for i, rl in enumerate(row_labels):
        y = pad_t + i * ch
        parts.append(f'<text x="{pad_l - 10}" y="{y + ch/2 + 4:.0f}" text-anchor="end" '
                     f'font-size="12" fill="#475569">{esc(rl)}</text>')
        for j, cl in enumerate(col_labels):
            cell = cell_fn(rl, cl)
            if not cell:
                continue
            x = pad_l + j * cw
            col, top, bot = cell
            parts.append(f'<rect x="{x+2:.0f}" y="{y+2:.0f}" width="{cw-4:.0f}" height="{ch-4:.0f}" '
                         f'rx="6" fill="{col}" stroke="#e2e8f0"/>')
            parts.append(f'<text x="{x+cw/2:.0f}" y="{y+ch/2-3:.0f}" text-anchor="middle" '
                         f'font-size="13" font-weight="700" fill="#fff">{esc(top)}</text>')
            parts.append(f'<text x="{x+cw/2:.0f}" y="{y+ch/2+12:.0f}" text-anchor="middle" '
                         f'font-size="9" fill="#f1f5f9">{esc(bot)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def collect_cells(sweep):
    cells = {}
    ratios = []
    for size in SIZE_ORDER:
        block = sweep["data"].get(size)
        if not block:
            continue
        for m in FLITS:
            cell = block["flits"].get(str(m))
            if not cell:
                continue
            cells[(size, m)] = cell
            r, _ = cell_ratio(cell)
            if r is not None:
                ratios.append(r)
    return cells, ratios


def ratio_heatmap(cells, ratios):
    rmin = min(ratios) if ratios else 1.0
    rmax = max(ratios) if ratios else 2.0

    def cell_fn(size, m):
        cell = cells.get((size, int(m)))
        if not cell or not cell.get("best_overall"):
            return None
        bo = cell["best_overall"]
        r, lb = cell_ratio(cell)
        if r is None:
            return None
        return ratio_color(r, rmin, rmax), f"{r:.2f}x", f"{bo['makespan']}/{lb}cy"

    svg = heatmap_svg([str(m) for m in FLITS], SIZE_ORDER, cell_fn,
                      "数据大小 m (flit)")
    legend = (
        f'<div class="legend"><span>{rmin:.2f}x</span>'
        f'<div class="legend-bar" style="background:linear-gradient(90deg,'
        f'hsl(145,72%,60%),hsl(72,72%,52%),hsl(0,72%,45%))"></div>'
        f'<span>{rmax:.2f}x</span><span class="note">(绿 = 贴近理论下界，红 = 偏离较大)</span></div>'
    )
    return svg, legend


def quadrant_heatmap(cells):
    def cell_fn(size, m):
        cell = cells.get((size, int(m)))
        if not cell:
            return None
        qk = quad_key(cell)
        if not qk:
            return None
        col = QUAD_COLOR.get(qk, "#64748b")
        bo = cell["best_overall"]
        short = bo["name"].replace("ring_bi_rs_optag", "RS+AG").replace(
            "ring_uni_rs_optag", "RS+AG").replace("tree_reduce_bcast", "RB")
        return col, short, qk.replace("/", "+")

    svg = heatmap_svg([str(m) for m in FLITS], SIZE_ORDER, cell_fn,
                      "全局最优方案（四象限）")
    legend = "".join(
        f'<span style="margin-right:16px"><span class="chip" style="background:{c}"></span>'
        f'{esc(k.replace("/", " + "))}</span>'
        for k, c in QUAD_COLOR.items()
    )
    return svg, f'<div class="legend">{legend}</div>'


def inc_vs_node_heatmap(cells, mode):
    ratios = []
    for size in SIZE_ORDER:
        for m in FLITS:
            cell = cells.get((size, m))
            if cell:
                r, _ = cell_ratio(cell, mode)
                if r is not None:
                    ratios.append(r)
    rmin = min(ratios) if ratios else 1.0
    rmax = max(ratios) if ratios else 2.0

    def cell_fn(size, m):
        cell = cells.get((size, int(m)))
        if not cell or mode not in cell:
            return None
        md = cell[mode]
        b = md.get("best")
        if not b:
            return None
        r, lb = cell_ratio(cell, mode)
        if r is None:
            return None
        return ratio_color(r, rmin, rmax), f"{r:.2f}x", f"{b['makespan']}/{lb}cy"

    label = "INC reduce" if mode == "inc" else "无 INC (node reduce)"
    svg = heatmap_svg([str(m) for m in FLITS], SIZE_ORDER, cell_fn,
                      f"{label} — makespan/下界")
    return svg


def detail_table(cells):
    rows = []
    for size in SIZE_ORDER:
        for m in FLITS:
            cell = cells.get((size, m))
            if not cell:
                continue
            bo = cell.get("best_overall")
            if not bo:
                continue
            inc = cell.get("inc", {})
            node = cell.get("node", {})
            inc_b = inc.get("best") or {}
            node_b = node.get("best") or {}
            r, lb = cell_ratio(cell)
            rows.append(
                f"<tr><td class='name'>{size}</td><td>{m}</td>"
                f"<td><b>{esc(bo['name'])}</b></td>"
                f"<td>{bo['makespan']}</td><td>{r:.3f}</td><td>{lb}</td>"
                f"<td>{esc(bo.get('reduce_mode',''))}</td>"
                f"<td>{esc(bo.get('algo',''))}</td>"
                f"<td>{inc_b.get('makespan','-')}</td>"
                f"<td>{node_b.get('makespan','-')}</td></tr>"
            )
    hdr = ("<table class='data'><thead><tr>"
           "<th>规模</th><th>m</th><th>全局最优方案</th><th>makespan</th><th>比值</th><th>下界T</th>"
           "<th>reduce模式</th><th>算法结构</th>"
           "<th>INC最优mk</th><th>无INC最优mk</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


def quadrant_table(cells):
    rows = []
    for size in SIZE_ORDER:
        for m in FLITS:
            cell = cells.get((size, m))
            if not cell:
                continue
            for mode in ("inc", "node"):
                md = cell.get(mode, {})
                tb = md.get("best_tree_bcast") or {}
                ra = md.get("best_rs_ag") or {}
                winner = "RB" if (tb.get("makespan") or 10**9) <= (ra.get("makespan") or 10**9) else "RS+AG"
                rows.append(
                    f"<tr><td class='name'>{size}</td><td>{m}</td>"
                    f"<td>{'INC' if mode=='inc' else '无INC'}</td>"
                    f"<td>{tb.get('makespan','-')}</td>"
                    f"<td class='name'>{esc(tb.get('name','-'))}</td>"
                    f"<td>{ra.get('makespan','-')}</td>"
                    f"<td class='name'>{esc(ra.get('name','-'))}</td>"
                    f"<td><b>{winner}</b></td></tr>"
                )
    hdr = ("<table class='data'><thead><tr>"
           "<th>规模</th><th>m</th><th>reduce模式</th>"
           "<th>RB makespan</th><th>RB方案</th>"
           "<th>RS+AG makespan</th><th>RS+AG方案</th><th>胜者</th>"
           "</tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


def lb_table(lb):
    rows = []
    for size in SIZE_ORDER:
        block = lb["data"].get(size)
        if not block:
            continue
        for mode in ("inc", "node"):
            for m in FLITS:
                d = block["modes"][mode][str(m)]
                rows.append(
                    f"<tr><td class='name'>{size}</td>"
                    f"<td>{'INC' if mode=='inc' else '无INC'}</td><td>{m}</td>"
                    f"<td>{d['tree_latency']}</td><td>{d['downramp_final']}</td>"
                    f"<td>{d['bisection']}</td><td>{d['downramp_rsag']}</td>"
                    f"<td><b>{d['combined']}</b></td></tr>"
                )
    hdr = ("<table class='data'><thead><tr>"
           "<th>规模</th><th>reduce模式</th><th>m</th>"
           "<th>树形延迟下界</th><th>下行ramp下界</th><th>二分带宽下界</th>"
           "<th>RS+AG下界</th><th>T=max</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


def analyze_findings(cells):
    inc_wins = 0
    node_wins = 0
    rb_wins = 0
    rsag_wins = 0
    small_rsag = 0  # 4x4,6x8,8x8
    large_rb = 0    # 12x16,16x16

    for (size, m), cell in cells.items():
        bo = cell.get("best_overall")
        if not bo:
            continue
        if bo.get("reduce_mode") == "inc":
            inc_wins += 1
        else:
            node_wins += 1
        if bo.get("algo") == "tree_bcast":
            rb_wins += 1
        else:
            rsag_wins += 1
        if size in ("4x4", "6x8", "8x8") and bo.get("algo") == "rs_ag":
            small_rsag += 1
        if size in ("12x16", "16x16") and bo.get("algo") == "tree_bcast":
            large_rb += 1

    total = len(cells)
    return f"""
<ul class="compact">
<li><b>全局最优 reduce 模式</b>：INC 在 {inc_wins}/{total} 个配置中胜出，无 INC 在 {node_wins}/{total} 个配置中胜出。
    INC 的 per-merge 代价为 3 cycle/flit，无 INC 需 12 cycle 完整 ramp 绕行；当 merge 次数少（小 mesh + 树形 RB）时 INC 优势不明显，
    但当 RS 阶段需要沿环逐跳 merge（N-1 次）时，INC 可将 RS 阶段从数百 cycle 压到与最优 AG 可拼接的量级。</li>
<li><b>算法结构选型</b>：Reduce+Broadcast (RB) 在 {rb_wins}/{total} 格胜出，RS+AG 在 {rsag_wins}/{total} 格胜出。
    小 mesh（4x4/6x8/8x8）上 RS+AG 在 INC 模式下占 {small_rsag}/15 格；
    大 mesh（12x16/16x16）上 RB 在 INC 模式下占 {large_rb}/10 格。</li>
<li><b>临界点</b>：当 mesh 直径 &gt; ~90 cycle 且 m &le; 5 时，单 Hamilton 环 RS 的 O(N) 步延迟超过树形 RB 的 O(&radic;N) 直径延迟，
    即使 AG 段采用库中最优方案也无法挽回；此时 tree_reduce_bcast 稳定为 makespan 最优（比值 ~1.00-1.03）。</li>
<li><b>无 INC 场景</b>：所有规模上 tree_reduce_bcast 均为该模式下的最优方案（RS+AG 的 RS 段因 12cy/merge 代价过高）。
    大 mesh + 大 m 时 makespan 比值可达 2.0（16x16, m=5），表明无 INC 时 reduce 代价成为主导瓶颈。</li>
<li><b>推荐配置</b>：
    <ul>
    <li>有 INC + 小/中 mesh + 小 m &rarr; <b>ring RS + 最优 AG</b></li>
    <li>有 INC + 大 mesh + m&le;5 &rarr; <b>tree reduce + broadcast</b></li>
    <li>无 INC（任意规模）&rarr; <b>tree reduce + broadcast</b>（避免环上多次 12cy 绕行）</li>
    </ul>
</li>
</ul>
"""


def build_html(sweep, lb):
    cells, ratios = collect_cells(sweep)
    ratio_svg, ratio_legend = ratio_heatmap(cells, ratios)
    quad_svg, quad_legend = quadrant_heatmap(cells)
    inc_svg = inc_vs_node_heatmap(cells, "inc")
    node_svg = inc_vs_node_heatmap(cells, "node")

    inc_lat = sweep.get("inc_lat", 3)
    node_red_lat = sweep.get("node_red_lat", 12)

    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8"/>
<title>Near-optimal Allreduce 研究报告</title>
<style>{CSS}</style>
</head><body>
<h1>Near-optimal Allreduce 研究报告</h1>
<p class="meta">2D mesh（非 torus）&middot; H={sweep['h']} V={sweep['v']} RAMP=1 ramp_bw=1
&middot; INC_LAT={inc_lat}cy &middot; NODE_RED_LAT={node_red_lat}cy
&middot; 规模 {', '.join(SIZE_ORDER)} &middot; m=1&ndash;5 flit</p>

<div class="card">
<h2>1. 物理模型与设计空间</h2>
<p>每个 mesh 节点通过 router 与 4 邻连接。链路延迟：同行 H={sweep['h']} cycle，同列 V={sweep['v']} cycle；
PE&harr;router ramp 各 1 cycle，链路带宽 1 flit/cycle。</p>
<p>Allreduce 需在全部 N 个节点完成归约并分发最终结果。本研究探索 <b>2&times;2 设计空间</b>：</p>
<table class="data">
<tr><th></th><th>Reduce + Broadcast</th><th>Reduce-Scatter + AllGather</th></tr>
<tr><td class="name"><b>INC reduce</b><br><span class="note">路由器内 merge，{inc_lat} cy/flit</span></td>
 <td>维度树 reduce 到中心 root，再多播树 broadcast</td>
 <td>Hamilton 环 RS + 库中最优 AG（<code>allgather_scale_sweep.json</code>）</td></tr>
<tr><td class="name"><b>无 INC</b><br><span class="note">下 ramp&rarr;node 计算&rarr;上 ramp，共 {node_red_lat} cy</span></td>
 <td>同上，但每跳 merge 需 node 绕行</td>
 <td>环 RS（高 merge 代价）+ 最优 AG</td></tr>
</table>
<p class="note">调度采用刚性零缓冲打包器（<code>sched_zerobuf_compare.py</code>）：每链路每 cycle 至多 1 flit，
每 ramp 每 cycle 至多 1 flit。RS+AG 中 AG 段 makespan 取自已有 allgather 最优零缓冲结果。</p>
</div>

<div class="card">
<h2>2. 理论下界</h2>
<p>对每种 (规模, m, reduce 模式) 计算四类下界取 max：</p>
<div class="formula">T = max(tree_latency, downramp_final, bisection)</div>
<div class="formula">T_rsag = max(downramp_rsag, bisection, diameter_pair)</div>
<p class="note">tree_latency 含顺序 reduce + broadcast 两阶段；merge 代价按 INC_LAT 或 NODE_RED_LAT 计入关键路径。</p>
<div class="table-wrap">
{lb_table(lb)}
</div>
</div>

<div class="card">
<h2>3. 全局最优 makespan / 下界 比值</h2>
<p>在全部四象限中取 makespan 最小的方案（每格一种）。</p>
{ratio_legend}
{ratio_svg}
</div>

<div class="card">
<h2>4. 四象限胜者分布</h2>
{quad_legend}
{quad_svg}
</div>

<div class="card grid2">
<div>
<h3>INC reduce — 比值热力图</h3>
{inc_svg}
</div>
<div>
<h3>无 INC — 比值热力图</h3>
{node_svg}
</div>
</div>

<div class="card">
<h2>5. 四象限详细对比（RB vs RS+AG）</h2>
<div class="table-wrap">
{quadrant_table(cells)}
</div>
</div>

<div class="card">
<h2>6. 全局最优方案一览</h2>
<div class="table-wrap">
{detail_table(cells)}
</div>
</div>

<div class="card">
<h2>7. 分析结论与推荐</h2>
{analyze_findings(cells)}
<h3>方案族说明</h3>
<ul class="compact">
<li><b>tree_reduce_bcast</b>：每源沿维度序路径 reduce 到 mesh 中心 root（{inc_lat}cy/merge 或 {node_red_lat}cy 绕行），root 弹出后再沿维序多播树 broadcast 到全部节点。</li>
<li><b>ring_*_rs_optag</b>：全局 Hamilton 环（蛇形构造）做 reduce-scatter，每节点沿环发送并逐跳 merge；完成后拼接库中该规模的最优 allgather 方案（如 hybrid_v_bi_B2、ring_bi 等）。</li>
<li><b>hybrid_hB</b>（横带局部环 RS + 全局树 RB）：在本次扫描中均未超越 tree_reduce_bcast，未进入任何最优格。</li>
</ul>
</div>

<p class="meta" style="margin-top:32px">数据来源：
<code>results/allreduce_scale_sweep.json</code>、
<code>results/allreduce_lb.json</code>、
<code>results/allgather_scale_sweep.json</code>
&middot; 生成脚本 <code>utils/gen_allreduce_scale_report.py</code></p>
</body></html>"""
    return body


def main():
    sweep = json.loads(SWEEP_JSON.read_text(encoding="utf-8"))
    lb = json.loads(LB_JSON.read_text(encoding="utf-8"))
    HTML_PATH.write_text(build_html(sweep, lb), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
