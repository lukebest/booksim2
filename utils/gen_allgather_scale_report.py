#!/usr/bin/env python3
"""Research report: near-optimal allgather across mesh scale x message size x
down-ramp bandwidth.

Reads results/allgather_lb.json (lower bounds) and
results/allgather_scale_sweep.json (event-driven scheme sweep), renders:
  - one makespan/lower-bound-ratio heatmap per ramp_bw (x=m, y=mesh size)
  - one winning-scheme-family heatmap per ramp_bw (same grid, categorical color)
  - the full lower-bound derivation and per-size detail tables

Output: results/report_allgather_scale.html
"""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LB_JSON = ROOT / "results" / "allgather_lb.json"
SWEEP_JSON = ROOT / "results" / "allgather_scale_sweep.json"
HTML_PATH = ROOT / "results" / "report_allgather_scale.html"

SIZE_ORDER = ["4x4", "6x8", "8x8", "12x16", "16x16", "32x32", "64x64"]
FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]

FAMILY_COLOR = {
    "multitree": "#2563eb",
    "ring": "#059669",
    "hybrid": "#d97706",
    "hybrid_v": "#dc2626",
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
.legend { display:flex; align-items:center; gap:8px; margin:10px 0; font-size:.85rem; }
.legend-bar { width:220px; height:14px; border-radius:4px; border:1px solid #cbd5e1; }
.chip { display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:6px; vertical-align:-1px; }
ul.compact li { margin:3px 0; }
.formula { font-family: ui-monospace, monospace; background:#f8fafc; border:1px solid #e2e8f0;
           border-radius:6px; padding:8px 12px; margin:6px 0; font-size:.86rem; }
</style>
"""


def esc(s):
    return html.escape(str(s))


def family_of(name):
    if name.startswith("hybrid_v"):
        return "hybrid_v"
    if name.startswith("hybrid"):
        return "hybrid"
    if name.startswith("ring"):
        return "ring"
    return "multitree"


def ratio_color(r, rmin, rmax):
    if rmax <= rmin:
        t = 0.0
    else:
        t = max(0.0, min(1.0, (r - rmin) / (rmax - rmin)))
    hue = 145 * (1 - t)
    return f"hsl({hue:.0f}, 72%, {42 + 18 * (1 - t):.0f}%)"


def collect_cells(sweep):
    """Return {(size,rb,m): cell} and global (rmin,rmax) over ratio."""
    cells = {}
    ratios = []
    for size in SIZE_ORDER:
        block = sweep["data"].get(size)
        if not block:
            continue
        for rb in RAMP_BWS:
            bwblock = block["bw"].get(str(rb))
            if not bwblock:
                continue
            for m in FLITS:
                cell = bwblock.get(str(m))
                if not cell or not cell.get("best"):
                    continue
                cells[(size, rb, m)] = cell
                ratios.append(cell["ratio"])
    return cells, ratios


def heatmap_svg(cells, ratios, rb, value_fn, label_fn, color_fn, legend_label):
    sizes = [s for s in SIZE_ORDER if (s, rb, 1) in cells]
    pad_l, pad_t, pad_r, pad_b = 92, 34, 20, 40
    cw, ch = 96, 46
    W = pad_l + len(FLITS) * cw + pad_r
    H = pad_t + len(sizes) * ch + pad_b
    parts = [
        f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;height:auto;display:block">',
        f'<text x="{pad_l + len(FLITS)*cw/2:.0f}" y="20" text-anchor="middle" '
        f'font-size="12.5" font-weight="600" fill="#334155">数据大小 m (flit)</text>',
    ]
    for j, m in enumerate(FLITS):
        x = pad_l + j * cw + cw / 2
        parts.append(f'<text x="{x:.0f}" y="{pad_t - 8}" text-anchor="middle" '
                     f'font-size="12" fill="#475569">{m}</text>')
    for i, size in enumerate(sizes):
        y = pad_t + i * ch
        parts.append(f'<text x="{pad_l - 10}" y="{y + ch/2 + 4:.0f}" text-anchor="end" '
                     f'font-size="12" fill="#475569">{size}</text>')
        for j, m in enumerate(FLITS):
            cell = cells.get((size, rb, m))
            if not cell:
                continue
            x = pad_l + j * cw
            col = color_fn(cell)
            parts.append(f'<rect x="{x+2:.0f}" y="{y+2:.0f}" width="{cw-4:.0f}" height="{ch-4:.0f}" '
                         f'rx="6" fill="{col}" stroke="#e2e8f0"/>')
            top, bot = label_fn(cell)
            parts.append(f'<text x="{x+cw/2:.0f}" y="{y+ch/2-3:.0f}" text-anchor="middle" '
                         f'font-size="13" font-weight="700" fill="#fff">{esc(top)}</text>')
            parts.append(f'<text x="{x+cw/2:.0f}" y="{y+ch/2+12:.0f}" text-anchor="middle" '
                         f'font-size="9" fill="#f1f5f9">{esc(bot)}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def ratio_heatmap(cells, ratios, rb):
    rmin = min(ratios) if ratios else 1.0
    rmax = max(ratios) if ratios else 2.0

    def color_fn(cell):
        return ratio_color(cell["ratio"], rmin, rmax)

    def label_fn(cell):
        return f"{cell['ratio']:.2f}x", f"{cell['best']['makespan']}/{cell['T']}cy"

    svg = heatmap_svg(cells, ratios, rb, None, label_fn, color_fn, "ratio")
    legend = (
        f'<div class="legend"><span>{rmin:.2f}x</span>'
        f'<div class="legend-bar" style="background:linear-gradient(90deg,'
        f'hsl(145,72%,60%),hsl(72,72%,52%),hsl(0,72%,45%))"></div>'
        f'<span>{rmax:.2f}x</span><span class="note">(绿 = 贴近理论下界 T，红 = 偏离较大)</span></div>'
    )
    return svg, legend, rmin, rmax


def scheme_heatmap(cells, ratios, rb):
    def color_fn(cell):
        fam = family_of(cell["best"]["name"])
        return FAMILY_COLOR[fam]

    def label_fn(cell):
        name = cell["best"]["name"]
        short = name.replace("hybrid_v_bi_B", "hv_bi B=").replace("hybrid_bi_B", "h_bi B=") \
                     .replace("hybrid_v_uni_B", "hv_uni B=").replace("hybrid_uni_B", "h_uni B=") \
                     .replace("multitree", "multitree").replace("ring_bi", "ring_bi") \
                     .replace("ring_uni", "ring_uni")
        return short, ""

    svg = heatmap_svg(cells, ratios, rb, None, label_fn, color_fn, "scheme")
    legend = "".join(
        f'<span style="margin-right:16px"><span class="chip" style="background:{c}"></span>{esc(fam)}</span>'
        for fam, c in FAMILY_COLOR.items()
    )
    return svg, f'<div class="legend">{legend}</div>'


def detail_table(cells, rb):
    rows = []
    for size in SIZE_ORDER:
        for m in FLITS:
            cell = cells.get((size, rb, m))
            if not cell:
                continue
            b = cell["best"]
            rows.append(
                f"<tr><td class='name'>{size}</td><td>{m}</td><td>{cell['T']}</td>"
                f"<td class='name'><b>{esc(b['name'])}</b></td><td>{b['makespan']}</td>"
                f"<td>{cell['ratio']:.3f}</td></tr>"
            )
    hdr = ("<table class='data'><thead><tr><th>规模</th><th>m</th><th>理论下界 T</th>"
           "<th>最优方案</th><th>makespan</th><th>比值</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


def lb_table(lb):
    rows = []
    for size in SIZE_ORDER:
        block = lb["data"].get(size)
        if not block:
            continue
        for rb in RAMP_BWS:
            for m in FLITS:
                d = block["bw"][str(rb)][str(m)]
                rows.append(
                    f"<tr><td class='name'>{size}</td><td>{rb}</td><td>{m}</td>"
                    f"<td>{d['eject_lb']}</td><td>{d['corner_lb']}</td>"
                    f"<td>{d['latency_lb']}</td><td>{d['bisect_lb']}</td>"
                    f"<td><b>{d['T']}</b></td><td>{'+'.join(d['binding'])}</td></tr>"
                )
    hdr = ("<table class='data'><thead><tr><th>规模</th><th>ramp_bw</th><th>m</th>"
           "<th>弹出下界</th><th>角节点下界</th><th>延迟下界</th><th>二分带宽下界</th>"
           "<th>T=max</th><th>紧约束</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


SCHEME_DIAGRAMS = """
<div class="card">
<h3>方案族原理</h3>
<ul class="compact">
<li><b>multitree</b>：每个源沿 X 方向先构建一条行内双向链，再从该行每个节点沿 Y 方向双向展开——是一棵覆盖全部 N-1 个目的节点的维序双向树，路径上任意中间节点原地下 ramp 收取一份拷贝并继续转发（in-network fork），不做二次注入。</li>
<li><b>ring / ring_bi</b>：全局单条 Hamilton 环（boustrophedon 蛇形构造），uni 单向绕环一圈，bi 从两侧同时出发各走半圈，最远仅需 ceil((L-1)/2) 跳。</li>
<li><b>hybrid（横带）</b>：将 MY 行切成 B 个横向条带，条带内跑局部 Hamilton 环做子群 allgather，随后条带的顶/底行沿纵向对外二次转发到其它条带（每列一棵纵向树）。</li>
<li><b>hybrid_v（纵带）</b>：hybrid 的转置——将 MX 列切成 B 个纵向条带，条带内局部环 + 每行沿横向二次转发到其它条带。历史数据显示这是 16x16 上表现最好的方案族。</li>
</ul>
<p class="note">quad（4 象限环+中心交换）与 border（象限环+边界多点注入）在 16x16 上已经系统性落后于 hybrid/hybrid_v（quad_bi=1097/523cy、border_bi=540cy，对比 hybrid_v_bi=334cy，见 <code>results/zerobuf_16x16.json</code>），本次多规模扫描不再重复评估，仅在 16x16 处引用作参考。</p>
</div>
"""


def build_report(lb, sweep):
    cells, ratios = collect_cells(sweep)
    sections = []
    for rb in RAMP_BWS:
        rsvg, rlegend, rmin, rmax = ratio_heatmap(cells, ratios, rb)
        ssvg, slegend = scheme_heatmap(cells, ratios, rb)
        sections.append(f"""
<div class="card">
<h3>下 ramp 带宽 = {rb} flit/cycle/node — makespan / 理论下界 T</h3>
{rlegend}
{rsvg}
</div>
<div class="card">
<h3>下 ramp 带宽 = {rb} flit/cycle/node — 最优方案分布</h3>
{slegend}
{ssvg}
</div>
<div class="card">
<h3>下 ramp 带宽 = {rb} flit/cycle/node — 详细数据</h3>
{detail_table(cells, rb)}
</div>
""")

    huge_sizes = [s for s in SIZE_ORDER if sweep["data"].get(s, {}).get("huge")]
    small_sizes = [s for s in SIZE_ORDER if s in sweep["data"] and not sweep["data"][s].get("huge")]

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>",
        "<title>Near-optimal Allgather 研究报告</title>",
        f"<style>{CSS}</head><body>",
        "<h1>Near-optimal Allgather 研究报告</h1>",
        "<p class='meta'>2D mesh allgather：下界分析 + 事件驱动仿真扫描 + 热力图 + autogen 方案生成器</p>",

        "<h2>1. 物理模型与假设</h2>",
        "<div class='card'>",
        "<ul class='compact'>",
        "<li>链路带宽：1 flit/cycle/方向（每条有向 mesh 链路）</li>",
        "<li>横向（同行）link delay H = 4 cycle；纵向（同列）link delay V = 6 cycle</li>",
        "<li>下 ramp（PE 收取）带宽 ramp_bw ∈ {1, 2} flit/cycle/node；上 ramp（PE 注入）同带宽</li>",
        "<li>固定 ramp 时延 RAMP = 1 cycle（下 ramp 出口的最后一段延迟）</li>",
        "<li>规模：4x4, 6x8, 8x8, 12x16, 16x16, 32x32, 64x64（正方形部分按边长 x2 步进）</li>",
        "<li>数据大小 m ∈ {1..5} flit/节点；allgather 语义：每节点须从其余 N-1 个节点各收到 m flit</li>",
        "<li>调度目标：makespan = 最后一个 flit 完成下 ramp 出口的 cycle</li>",
        "<li>本研究的仿真引擎是<b>事件驱动流水线模型</b>（允许 flit 在路由器内部因出端口短暂繁忙而等待 1 个 pipeline register，"
        "不允许专用 SRAM 缓冲）——与更严格的“零缓冲刚性单一注入偏移”模型（<code>sched_zerobuf_compare.py</code>，仅用于 16x16 参考）"
        "相比更贴近真实 wormhole/VC 路由器实现，且可扩展到 64x64（详见 <code>utils/allgather_fast_sim.py</code> 文档字符串）。"
        "两者在 16x16 上分别列出对比。</li>",
        "</ul></div>",

        "<h2>2. 下界分析</h2>",
        "<div class='card'>",
        "<p>对每个 (规模, ramp_bw, m) 计算四类独立下界，取其 <b>最大值 T</b> 作为理论最优 makespan 的必要条件：</p>",
        "<div class='formula'>eject_lb = ceil((N-1)·m / ramp_bw) — 每节点必须经其唯一下 ramp 收满 (N-1)·m flit</div>",
        "<div class='formula'>corner_lb = ceil((N-1)·m / 2) — mesh 角节点仅有 2 条入向物理链路（1 横 + 1 纵），"
        "其余 N-1 个节点的数据都必须先经这 2 条链路到达，与 ramp_bw 无关；ramp_bw≥2 时与 eject_lb 取值相同，成为紧约束</div>",
        "<div class='formula'>latency_lb = RAMP + [(MX-1)·H + (MY-1)·V] + (m-1) + RAMP — 最远节点对的维序最短路时延，"
        "加上 m flit 在最后一段链路上串行化所需的 (m-1) 额外 cycle</div>",
        "<div class='formula'>bisect_lb = max(ceil((N/2)·m / MY), ceil((N/2)·m / MX)) — 任意一条竖直/水平切割把 mesh "
        "分成两半，一侧的每个源只需让自己的 m flit 跨切割一次（之后网内多播分发），故跨切割流量为 (N/2)·m，"
        "受限于切割处并行链路数 MY 或 MX</div>",
        "<p class='note'>m 较小、ramp_bw=1 时 eject_lb 通常最紧；m 较小、ramp_bw=2 时 corner_lb 与 eject_lb 相等且共同主导；"
        "小规模（如 4x4）m 较小时 latency_lb 反而最紧（网络直径本身就是瓶颈）；bisect_lb 在本次研究的规模/m 范围内始终未成为主导项，"
        "但方形度较低的 6x8/12x16 上距离其它下界更近，随规模进一步增大会趋近主导。</p>",
        lb_table(lb),
        "</div>",

        "<h2>3. 方案族与仿真扫描</h2>",
        SCHEME_DIAGRAMS,
        "<div class='card'>",
        f"<p>全量比较（含全部方案族 x B 值 x 单/双向）覆盖规模：{', '.join(small_sizes)}。"
        f"32x32、64x64（N≥1024）单次仿真调用成本 O(N²·m)，采用分层策略：先在每个 ramp_bw 的代表性 m 下做一次"
        "缩减版全量比较（含 multitree、ring_bi、hybrid_bi/hybrid_v_bi 的 B∈{2,4,8}，双向变体——单向在所有更小规模上从未取胜，故不再评估）"
        "确定最优方案，再对其余 m 只重跑该最优方案 + multitree/ring_bi 两个基线，用于对照。</p>",
        (f"<p class='note'>{esc(sweep['notes']['64x64'])}</p>" if sweep.get("notes", {}).get("64x64") else ""),
        "</div>",

        "<h2>4. 热力图：makespan / 理论下界</h2>",
        *sections,

        "<h2>5. 结论</h2>",
        "<div class='card'>",
        "<ul class='compact'>",
        "<li><b>hybrid_v（纵带局部环 + 横向树）双向变体</b>在几乎所有中大规模、双 ramp_bw 组合下都是或接近最优方案，"
        "B 值最优点通常在 2~8 之间，随规模增大略微上移。</li>",
        "<li><b>multitree</b> 在极小规模（4x4、6x8）与 m=1 时经常直接达到理论下界（ratio=1.0），"
        "因为此时延迟下界主导而非带宽下界，维序双向树的最短路径特性正好命中。</li>",
        "<li><b>ring（全局单环）</b>在所有规模上都明显落后于 hybrid/hybrid_v（条带化把长距离环拆成局部环 + 树形广播，"
        "显著缩短关键路径），仅作为下界基线保留。</li>",
        "<li>ramp_bw 从 1 提升到 2：m 较大、规模较大时 makespan 近似减半（带宽受限区间）；"
        "m=1 的小规模场景则几乎不变（延迟下界主导，带宽提升无效）。</li>",
        "<li>ratio（makespan/T）总体在 1.0~2.0 之间。一个明显的规律：<b>规模越大、ramp_bw=1 时，几乎任意合理方案都逼近 eject 下界"
        "（64x64 @ ramp_bw=1 全部 m 上 ratio ≤ 1.0024）</b>——此时下 ramp 出口几乎永不空闲，是唯一瓶颈，拓扑选择反而不重要；"
        "而 ramp_bw=2 时下界不再总是可达（如 64x64,m=1,ramp_bw=2 的角节点下界 T=2048 只能压到 4047，ratio≈1.98），"
        "说明角节点链路下界在 ramp_bw≥2、m 较小时是一个理论上正确但实践中难以逼近的松下界。</li>",
        "<li>方案排名并非在所有 (规模, m) 组合下都稳定：64x64、ramp_bw=2、m=5 的实测最优反而是最简单的全局 ring_bi"
        "（优于 multitree 与 hybrid_v_bi_B2），提示大规模 + 大数据量 + 较宽带宽区间下，简单拓扑的均匀负载分布有时比精细分带更优——"
        "本报告的 autogen 选择器直接以逐格实测结果为准，而不依赖\"某一方案族总是最优\"的先验假设。</li>",
        "</ul></div>",

        "<h2>6. Autogen 方案生成器</h2>",
        "<div class='card'>",
        "<p><code>utils/autogen_allgather.py</code> 从本次扫描的查找表中，为任意 (mx, my, m, ramp_bw) 直接返回预先确定的"
        "最优方案标签与其可复现的仿真调用；覆盖本报告全部 7 规模 x 5 数据量 x 2 带宽 = 70 组合的生成 + 校验回归结果见脚本自带的 "
        "<code>--selftest</code> 输出。</p>",
        "</div>",

        f"<p class='note'>复现：<code>python3 utils/allgather_lower_bounds.py</code> → "
        f"<code>python3 utils/sweep_allgather_scale.py</code> → "
        f"<code>python3 utils/gen_allgather_scale_report.py</code></p>",
        "</body></html>",
    ]
    return "\n".join(parts)


def main():
    lb = json.loads(LB_JSON.read_text(encoding="utf-8"))
    sweep = json.loads(SWEEP_JSON.read_text(encoding="utf-8"))
    HTML_PATH.write_text(build_report(lb, sweep), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
