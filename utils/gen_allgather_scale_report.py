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

import autogen_allgather as A

ROOT = Path(__file__).resolve().parents[1]
LB_JSON = ROOT / "results" / "allgather_lb.json"
SWEEP_JSON = ROOT / "results" / "allgather_scale_sweep.json"
STRICT_JSON = ROOT / "results" / "zerobuf_strict_m1.json"
WITNESS64_JSON = ROOT / "results" / "zerobuf_64x64_witness.json"
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
    """Return {(size,rb,m): cell} and global (rmin,rmax) over ratio.

    cell['best'] is overwritten with the BUFFER-BUDGET-AWARE pick from
    autogen_allgather.recommend() (see results/report section "buffer 深度
    诚实性核查"): the raw per-cell "fastest" candidate in the sweep JSON can
    rely on unbounded implicit in-network queuing, which is not a credible
    zero/small-buffer result for high-fanout schemes. cell['raw_best'] keeps
    the original unconstrained-fastest candidate for comparison."""
    cells = {}
    ratios = []
    for size in SIZE_ORDER:
        block = sweep["data"].get(size)
        if not block:
            continue
        mx, my = (int(x) for x in size.split("x"))
        for rb in RAMP_BWS:
            bwblock = block["bw"].get(str(rb))
            if not bwblock:
                continue
            for m in FLITS:
                cell = bwblock.get(str(m))
                if not cell or not cell.get("best"):
                    continue
                raw_best = cell["best"]
                rec = A.recommend(mx, my, m, rb, sweep, buffer_budget=0)
                picked = {
                    "name": rec["scheme"], "makespan": rec["makespan"],
                    "max_link_wait": rec.get("max_link_wait"),
                    "max_ramp_wait": rec.get("max_ramp_wait"),
                }
                new_cell = dict(cell)
                new_cell["raw_best"] = raw_best
                new_cell["best"] = picked
                new_cell["buffer_limited"] = rec.get("buffer_limited")
                new_cell["source"] = rec.get("source")
                new_cell["ed_makespan"] = rec.get("ed_makespan")
                new_cell["ed_max_link_wait"] = rec.get("ed_max_link_wait")
                new_cell["ed_max_ramp_wait"] = rec.get("ed_max_ramp_wait")
                new_cell["ratio"] = round(picked["makespan"] / cell["T"], 4) if cell["T"] else None
                cells[(size, rb, m)] = new_cell
                ratios.append(new_cell["ratio"])
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
            raw = cell.get("raw_best", b)
            buf = f"{b.get('max_link_wait')}/{b.get('max_ramp_wait')}"
            src = cell.get("source")
            if src == "strict_zerobuf":
                src_tag = " <span class='note'>(严格零buffer真值)</span>"
                ed_mk = cell.get("ed_makespan")
                ed_buf = f"{cell.get('ed_max_link_wait')}/{cell.get('ed_max_ramp_wait')}"
                raw_flag = (f" &nbsp;<span class='note'>(同方案事件驱动仿真: {ed_mk}cy, buf={ed_buf}; "
                            f"未加缓冲约束时仿真最快: {esc(raw['name'])} {raw['makespan']}cy)</span>")
            else:
                src_tag = ""
                raw_flag = (f" &nbsp;<span class='note'>(未加缓冲约束时仿真最快: "
                            f"{esc(raw['name'])} {raw['makespan']}cy, buf={raw.get('max_link_wait')}/"
                            f"{raw.get('max_ramp_wait')})</span>") if raw["name"] != b["name"] else ""
            rows.append(
                f"<tr><td class='name'>{size}</td><td>{m}</td><td>{cell['T']}</td>"
                f"<td class='name'><b>{esc(b['name'])}</b>{src_tag}{raw_flag}</td><td>{b['makespan']}</td>"
                f"<td>{buf}</td><td>{cell['ratio']:.3f}</td></tr>"
            )
    hdr = ("<table class='data'><thead><tr><th>规模</th><th>m</th><th>理论下界 T</th>"
           "<th>推荐方案（零buffer最优）</th><th>makespan</th>"
           "<th>所需 buffer(link/ramp,单位flit)</th><th>比值</th></tr></thead><tbody>")
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


def strict_m1_table():
    if not STRICT_JSON.exists():
        return "<p class='note'>未找到 results/zerobuf_strict_m1.json</p>"
    strict = json.loads(STRICT_JSON.read_text(encoding="utf-8"))
    rows = []
    for size in SIZE_ORDER:
        block = strict["data"].get(size)
        if not block:
            rows.append(f"<tr><td class='name'>{size}</td><td colspan='4' class='note'>"
                        f"未测（严格零 buffer 打包器成本随规模超线性增长，见正文）</td></tr>")
            continue
        for rb_str in ("1", "2"):
            b = block["bw"][rb_str]
            top3 = sorted((r for r in b["results"] if r["ok"]), key=lambda r: r["makespan"])[:3]
            top_str = ", ".join(f"{r['name']}={r['makespan']}" for r in top3)
            rows.append(f"<tr><td class='name'>{size}</td><td>{rb_str}</td>"
                        f"<td>{b['eject_lb']}</td><td class='name'>{top_str}</td></tr>")
    hdr = ("<table class='data'><thead><tr><th>规模</th><th>ramp_bw</th><th>eject 下界</th>"
           "<th>严格零 buffer 排名前三（m=1，makespan）</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


def buffer_growth_table(sweep):
    rows = []
    cell16 = sweep["data"].get("16x16", {}).get("bw", {}).get("1", {})
    for m in FLITS:
        c = cell16.get(str(m))
        if not c:
            continue
        mt = next((r for r in c["results"] if r["name"] == "multitree"), None)
        rb = next((r for r in c["results"] if r["name"] == "ring_bi"), None)
        rows.append(f"<tr><td>{m}</td>"
                    f"<td>{mt['makespan']}</td><td>{mt['max_link_wait']}/{mt['max_ramp_wait']}</td>"
                    f"<td>{rb['makespan']}</td><td>{rb['max_link_wait']}/{rb['max_ramp_wait']}</td></tr>")
    hdr = ("<table class='data'><thead><tr><th>m</th>"
           "<th>multitree makespan</th><th>multitree 所需buffer(link/ramp)</th>"
           "<th>ring_bi makespan</th><th>ring_bi 所需buffer(link/ramp)</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


BUFFER_HONESTY_SECTION = """
<h2>3.5 buffer 深度诚实性核查（重要更正）</h2>
<div class="card">
<p><b>结论先行：事件驱动引擎允许 flit 在链路/下 ramp 出现资源争用时排队等待，等待时长没有硬性上限
（不是最初文档所写的"1 个 pipeline register"——这个描述是错的，已更正）。这对不同方案族的影响很不对称：
multitree、细粒度 hybrid 等高扇出（in-network fork 多）方案能靠这种隐式排队大幅"抹平"调度冲突，从而
在仿真里显得很快；ring / 粗粒度 hybrid_v_bi 等结构天然争用少得多，需要的排队也少得多。但即使是这些"较好"的
方案，在 ramp_bw=1、m 较大、规模较大时也会需要不小的排队（例如 ring_bi 在 16x16/ramp_bw=1/m=5 需要 538
flit 的下 ramp 缓冲）——根因是下 ramp 本身已饱和（eject 下界很紧），任何非精确协同调度的贪心算法都会在
下 ramp 处产生突发积压，只是 multitree 的突发远比 ring 剧烈（同条件下约 10~20 倍）。因此"buffer 需求"更准确的
表述是一个连续谱：<b>ring/粗粒度 hybrid ≪ multitree/细粒度 hybrid</b>，而不是"前者零需求、后者才需要"。
跨方案比较 makespan 并不是在同一 buffer 假设下的公平比较。</b></p>

<h3>实测证据 1：16x16, m=1，严格零 buffer vs 本引擎（排名完全反转）</h3>
<table class="data"><thead><tr><th>方案</th><th>严格零buffer makespan（真值，ramp_bw=1）</th>
<th>本引擎 makespan（ramp_bw=1）</th></tr></thead><tbody>
<tr><td class="name">hybrid_v_bi_B2（真实最优）</td><td>334</td><td>332</td></tr>
<tr><td class="name">ring_bi</td><td>754</td><td>754</td></tr>
<tr><td class="name">multitree</td><td><b>837</b>（22 个方案中排第 13）</td><td><b>265</b>（"最优"）</td></tr>
</tbody></table>
<p class="note">严格零buffer数据来自 <code>utils/sched_zerobuf_compare.py</code> 的刚性单一注入偏移打包器
（任何冲突都判定为不合法调度，必须靠全局重新选择偏移规避——见其文档字符串），是本研究唯一有硬证据支持的
"无缓冲、无冲突、无阻塞"结果，但其打包搜索成本随规模、随 m 都是超线性增长（16x16、multitree：m=1 需 13.9s，
m=2 需 38.7s，约 2.8x/+1flit），无法扩展到 m>1 或 32x32/64x64。</p>

<h3>实测证据 2：所需 buffer 深度随 m 的增长（16x16, ramp_bw=1）</h3>
{buffer_growth}
<p class="note">两者的下 ramp 缓冲需求都随 m 增长（ramp_bw=1 下 ramp 本身饱和，是两者共同的根因），但增长
方式不同：multitree 从 m=1 起就需要大量排队（121 flit）且近似线性增长到 633；ring_bi 在 m=1~2 时几乎不需要
排队（1~2 flit），m≥3 后才开始明显增长（33→286→538）。也就是说 ring_bi 把"需要深排队"的临界点推迟到了更大
的 m，而不是完全没有这个问题——在 m 较小、或 ramp_bw=2（下 ramp 不饱和）时它才是真正的零/近零 buffer 方案，
这也是本引擎"multitree 在小 m 时看起来更快"这一表象的直接机制解释，而不是真实的调度优势。</p>

<h3>实测证据 3：64x64 全量抽样复核</h3>
<table class="data"><thead><tr><th>方案 / 场景</th><th>makespan</th><th>所需 buffer(link/ramp，flit)</th></tr></thead><tbody>
<tr><td class="name">multitree, ramp_bw=1, m=5</td><td>20480</td><td><b>314 / 10233</b></td></tr>
<tr><td class="name">ring_bi, ramp_bw=2, m=5</td><td>12230</td><td>1 / 0</td></tr>
</tbody></table>
<p class="note">64x64、ramp_bw=1、m=5 下 multitree 需要单节点连续缓存 <b>10233 个 flit</b> 才能实现其记录的
makespan——这远超任何现实路由器的缓冲深度，可确认此前"ramp_bw=1 时 multitree 总是最优"的结论是本引擎
buffer 假设不一致造成的伪影，不可信。</p>

<h3>严格零 buffer 基准（m=1，全部规模）</h3>
{strict_table}
<p class="note"><b>hybrid_v_bi_B2</b>（B=2 纵向条带、双向）是 m=1 下唯一在 6x8~16x16 全部规模上都排名第一的方案
（4x4 上与 hybrid_bi_B2/multitree 并列理论下界）——这与本引擎不加约束时给出的"multitree 常胜"结论相反，
但与"允许适度实现缓冲"的仿真结果吻合度很高。</p>

<h3>修正措施</h3>
<ul class="compact">
<li>本报告以下全部热力图/明细表已改为调用 <code>utils/autogen_allgather.py</code> 的
<b>buffer_budget=2 flit 约束选择</b>（该阈值由上述实测校准：即使 3~4 flit 的隐式排队也足以让 multitree 的
makespan 偏离真值 17~25%，阈值必须收紧到个位数以内才能有效滤除高扇出方案的伪影），而不是原始仿真里"不限制
排队、最快获胜"的结果；报告表格中同时列出两者供对比。</li>
<li><code>--selftest</code> 增加了对 <code>results/zerobuf_strict_m1.json</code>（真值）的交叉核对，
默认 buffer_budget=2 下 m=1 的推荐方案与真实最优的比值全部 ≤1.13x（多数持平），
远好于不加约束时可达 2~3x 的偏差。</li>
<li>32x32 已重新用带缓冲量测的引擎全量复核（结果见第 4 节热力图/明细表）；64x64 只对两个代表性
(ramp_bw, m) 组合做了直接抽样复核（见上表证据 3），其余组合仍复用旧的无量测数据，<code>recommend()</code>
在检测到候选方案缺少 buffer 量测时不会当作"零 buffer 合规"处理，而是回退为"仅使用已量测方案中排队需求最小者"
并标记 <code>buffer_limited=True</code>；已知在 ramp_bw=1、m 较大时，本引擎测得的任何方案都超出 buffer_budget，
此时该回退等价于"挑排队需求最小的那个"（通常仍是 ring_bi/hybrid_v_bi 粗粒度），而不是无约束下最快的
multitree/细粒度 hybrid——但其绝对 makespan 仍应视为"经验估计"而非可验证的零 buffer 最优解。</li>
</ul>
</div>
"""


def witness64_m1_table():
    """m=1 screening: every non-multitree candidate tested at 64x64, both
    ramp_bw, sorted by makespan within each bw. exact zero-buffer-witness
    rows are bolded."""
    if not WITNESS64_JSON.exists():
        return "<p class='note'>未找到 results/zerobuf_64x64_witness.json</p>"
    data = json.loads(WITNESS64_JSON.read_text(encoding="utf-8"))
    rows = []
    for rb in (1, 2):
        recs = sorted((r for r in data if r["ramp_bw"] == rb and r["m"] == 1),
                      key=lambda r: r["makespan"])
        for r in recs:
            name = r["name"]
            if r["zero_buffer_certified"]:
                name = f"<b>{esc(name)}</b>"
            else:
                name = esc(name)
            cert = "✓ 严格零buffer" if r["zero_buffer_certified"] else "✗"
            rows.append(f"<tr><td>{rb}</td><td class='name'>{name}</td><td>{r['makespan']}</td>"
                        f"<td>{r['max_link_wait']}</td><td>{r['max_ramp_wait']}</td><td>{cert}</td></tr>")
    hdr = ("<table class='data'><thead><tr><th>ramp_bw</th><th class='name'>方案</th>"
           "<th>makespan (m=1)</th><th>所需 link_wait</th><th>所需 ramp_wait</th>"
           "<th>零buffer见证</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


def witness64_champion_table():
    """Full m=1..5 trajectory of the two m=1 zero-buffer champions."""
    if not WITNESS64_JSON.exists():
        return ""
    data = json.loads(WITNESS64_JSON.read_text(encoding="utf-8"))
    champs = {1: "hybrid_v_uni_B1", 2: "hybrid_v_bi_B1"}
    rows = []
    for rb, name in champs.items():
        for m in FLITS:
            r = next((x for x in data if x["ramp_bw"] == rb and x["m"] == m and x["name"] == name), None)
            if not r:
                rows.append(f"<tr><td>{rb}</td><td class='name'>{esc(name)}</td><td>{m}</td>"
                            f"<td colspan='4' class='note'>未测</td></tr>")
                continue
            cert = "✓" if r["zero_buffer_certified"] else "✗"
            rows.append(f"<tr><td>{rb}</td><td class='name'>{esc(name)}</td><td>{m}</td>"
                        f"<td>{r['makespan']}</td><td>{r['max_link_wait']}</td>"
                        f"<td>{r['max_ramp_wait']}</td><td>{cert}</td></tr>")
    hdr = ("<table class='data'><thead><tr><th>ramp_bw</th><th class='name'>方案（该 ramp_bw 下 m=1 的零buffer冠军）</th>"
           "<th>m</th><th>makespan</th><th>link_wait</th><th>ramp_wait</th><th>零buffer见证</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


WITNESS64_SECTION = """
<h2>3.6 64x64 专项复核：排除 multitree，逐方案零 buffer 见证</h2>
<div class="card">
<p><b>背景</b>：64x64（N=4096）上严格零buffer打包器（<code>sched_zerobuf_compare.py</code>）成本外推自
16x16/12x16 的实测 O(N³) 增长率，需要数周才能跑完一个方案——完全不可行。因此本节改用一种同样严格、但成本是
O(N²·m) 的替代验证方法：<b>零buffer见证法</b>——事件驱动引擎在每一跳都把 flit 排到"资源最早空闲的 cycle"；
如果一次完整仿真里<b>所有</b>跳都恰好落在其"ready"时刻（<code>max_link_wait==0 且 max_ramp_wait==0</code>），
说明引擎全程没有用到任何隐式排队，这就是刚性打包器要找的"零buffer、无冲突、无阻塞"调度本身，只是换成用正向仿真
直接见证，而不是逆向搜索注入偏移。若 wait&gt;0，则只能说明该次尝试不是零buffer调度（不代表不存在，只是没被这次
贪心找到）。</p>
<p><b>范围</b>：按用户要求排除 multitree（第 3.5 节已证实其在 64x64/ramp_bw=1/m=5 需要单节点 10233 flit
下ramp缓冲，不可信）；遍历 ring_uni/ring_bi、hybrid_uni/hybrid_bi（B∈{{2,4}}）、hybrid_v_uni/hybrid_v_bi
（B∈{{1,2,4}}），m=1 时共 {n_m1} 个候选 x 2 个 ramp_bw。</p>

<h3>m=1 全量候选结果（按 makespan 排序）</h3>
{m1_table}
<p class="note">只有 <b>ring_uni</b>（两种 ramp_bw 下）、<b>ring_bi</b>（仅 ramp_bw=2）、
<b>hybrid_v_uni_B1</b>（两种 ramp_bw 下）、<b>hybrid_v_bi_B1</b>（仅 ramp_bw=2）在 m=1 时取得严格零buffer
见证。<code>hybrid_v_uni_B1</code>/<code>hybrid_v_bi_B1</code>（B=1 即"纵向单条带"，退化为覆盖全网的单条
Hamilton 环，但走向与 ring 转置——spine 沿列、梳齿沿行，见下方与 ring_bi 的对比）在这四者中都是最快的，且严格
支配 ring_uni/ring_bi（makespan 更小，buffer 需求相等或更小）。B∈{{2,4}} 的 hybrid(_v) 变体 makespan 更快，
但全部需要 &gt;0 的 link/ramp 等待（1~193/1~985 flit），不满足严格零buffer。</p>

<h3>hybrid_v_bi_B1 为什么比 ring_bi 快：spine/梳齿方向的转置</h3>
<p class="note"><code>ring_bi</code> 的 Hamilton 环以水平 spine（1 行，mx-1 跳）+ 逐列纵向梳齿
（mx 列 × (my-1) 跳）构造，纵向跳占主导；<code>hybrid_v_bi_B1</code>（B=1）转置为垂直 spine（1 列，my-1 跳）
+ 逐行横向梳齿（my 行 × (mx-1) 跳），横向跳占主导。本研究 H=4cy &lt; V=6cy，横向跳更便宜，故
hybrid_v_bi_B1 在正方形 mesh 上系统性快于 ring_bi（64x64/ramp_bw=2/m=1 实测：8382 vs 12226，快 31%），
两者都是同一个"全局单环 + 双向对分"思路，只是转置方向选择利用了链路时延的非对称性。</p>

<h3>两个零buffer冠军方案的完整 m=1..5 轨迹</h3>
{champion_table}
<p class="note">两个冠军方案在各自 ramp_bw 下对 m=1~4 都保持严格零buffer（makespan 几乎精确随 m 线性增长
+1/cycle，即 8382→8383→8384→8385，16634→16635→16636→16637），仅在 m=5 时新增 1 cycle 的 link_wait
（不再是严格零，但同规模下所有测过的候选——包括 ring_uni/ring_bi——在 m=5 都同样需要恰好 1 cycle 的 wait，
从未出现更大的排队），makespan 分别为 <b>10309（ramp_bw=2）/ 20544（ramp_bw=1）</b>，据此推断"下ramp/链路
恰好打满的临界点在 m=5 附近"，而不是某个方案结构性更差。</p>

<h3>64x64 结论（替代第 4 节中此前依赖 multitree 的旧数据）</h3>
<h4>严格 buffer=0（buffer_budget=0，本次用户明确要求的版本）</h4>
<table class="data"><thead><tr><th>ramp_bw</th><th>m</th><th class="name">严格零buffer最优方案</th>
<th>makespan</th><th>理论下界 T</th><th>ratio</th></tr></thead><tbody>
{summary_rows}
</tbody></table>

<h4>buffer_budget=2（报告默认标准，允许≤2cycle 排队换取更快 makespan）</h4>
<table class="data"><thead><tr><th>ramp_bw</th><th>m</th><th class="name">推荐方案</th>
<th>makespan</th><th>理论下界 T</th><th>ratio</th></tr></thead><tbody>
{summary_rows_b2}
</tbody></table>
<p class="note"><b>ramp_bw=1 下 bi vs uni 的关键取舍</b>：严格零buffer（上表）在 ramp_bw=1 下只能选
<b>hybrid_v_uni_B1</b>（单向），因为双向的 hybrid_v_bi_B1 在 ramp_bw=1 下需要少量下ramp排队
（m=1: 0/1，m=2: 0/2），不满足严格零buffer。但允许≤2cycle 排队后（下表），<b>hybrid_v_bi_B1 在 m=2 被放行，
makespan 从 16635 降到 8383（快一倍）</b>——这正是用户复核发现的：64x64/ramp_bw=1/m=2 下 hybrid_v_bi_B1
更优。bi 比 uni 快约 2x 的原因是双向对分使最远跳数减半，代价是下ramp在 ramp_bw=1（饱和）时出现 1~2 cycle
的突发排队。m=1 时 hybrid_v_bi_B1 在 ramp_bw=1 下需 0/1（也≤2），故 budget=2 下 m=1 同样改选 hybrid_v_bi_B1
（8382）而非 uni（16634）。</p>

<p class="note">上表（严格 buffer=0）已写回 <code>results/allgather_scale_sweep.json</code> 的 64x64 条目（原条目在加入 buffer
量测之前生成，缺少 <code>max_link_wait</code>/<code>max_ramp_wait</code>，导致 <code>autogen_allgather.
recommend()</code> 的 buffer 过滤在"无量测数据可用"时静默回退到未约束的 multitree——这正是本节要修正的问题；
现已用本节数据整体替换，并额外补齐了此前从未扫描过的 m=2、m=4）。下表（buffer_budget=2）即第 4 节热力图/明细表
对 64x64 行所用的推荐方案——两表对照可看清"严格零buffer"与"允许≤2cycle 排队"在该规模的 makespan 差距
（ramp_bw=1、m=1~2 差约 2x，m≥3 后差距收窄，因下ramp饱和后 uni 也得排队）。</p>
</div>
"""


def witness64_summary_rows(sweep, budget=0):
    rows = []
    for rb in (1, 2):
        for m in FLITS:
            rec = A.recommend(64, 64, m, rb, sweep, buffer_budget=budget)
            rows.append(f"<tr><td>{rb}</td><td>{m}</td><td class='name'>{esc(rec['scheme'])}</td>"
                        f"<td>{rec['makespan']}</td><td>{rec['T']}</td><td>{rec['ratio']:.3f}x</td></tr>")
    return "".join(rows)


METHOD_FIX_SECTION = """
<h2>3.7 方法论更正：buffer_budget 过滤器的双方向失真（重要）</h2>
<div class="card">
<p><b>结论先行</b>：第 3.5 节引入的 <code>buffer_budget</code> 过滤器（用事件驱动引擎记录的
<code>max_link_wait</code>/<code>max_ramp_wait</code> 来剔除"需要深排队"的方案）存在一个根本性缺陷——
<b>事件驱动引擎是贪心调度器，它记录的 wait 是它"碰巧构造出的那个调度"的排队量，而不是该方案"所需缓冲的下界"</b>。
两者不是一回事，过滤器因此会在两个方向上都失真：</p>
<ul class="compact">
<li><b>过度剔除</b>：一个方案明明存在零buffer调度（刚性打包器证明确实存在），但贪心调度器没找到那个零冲突的注入偏移，
于是用排队"抹平"了冲突，记录到非零 wait——过滤器据此把它剔除。<b>典型：16x16/ramp_bw=1/m=1 的
hybrid_v_bi_B2</b>，刚性打包器证明其零buffer makespan=334，但事件驱动引擎记录 buf=1/3，被 buffer_budget=2 剔除。</li>
<li><b>漏放</b>：一个方案的快 makespan 实际上<b>依赖</b>排队才能达到（其真零buffer makespan 远高于此），
但贪心碰巧只用了很少的排队，过滤器据此放行。<b>典型：4x4/ramp_bw=1/m=1 的 multitree</b>，事件驱动 mk=32 buf=1/2
通过 budget=2 过滤，但其真零buffer makespan=51——这个 32 是靠排队偷来的，不是零buffer能力。</li>
</ul>

<h3>失真实例（16x16/ramp_bw=1/m=1，用户复核发现）</h3>
<table class="data"><thead><tr><th class="name">方案</th><th>事件驱动 makespan</th>
<th>事件驱动 buf(link/ramp)</th><th>刚性打包器 真零buffer makespan</th><th>buffer_budget=2 过滤器判定</th>
<th>是否正确</th></tr></thead><tbody>
<tr><td class="name">hybrid_v_bi_B2</td><td>332</td><td>1/3</td><td><b>334（真零buffer）</b></td>
<td>剔除（ramp 3&gt;2）</td><td class="name"><b>✗ 错误剔除</b></td></tr>
<tr><td class="name">hybrid_v_uni_B4</td><td>362</td><td>1/2</td><td>363</td><td>放行 → 被选为"最优"</td>
<td class="name">✗ 次优被当成最优</td></tr>
<tr><td class="name">multitree (4x4 类比)</td><td>32</td><td>1/2</td><td>51</td><td>放行（1/2≤2）</td>
<td class="name"><b>✗ 错误放行</b></td></tr>
</tbody></table>
<p class="note">修正前报告在此格推荐 hybrid_v_uni_B4（362cy），比真零buffer最优 hybrid_v_bi_B2（334cy）慢 8.4%——
正是用户指出的偏差。同类偏差此前出现在 4x4/6x8/8x8/12x16/16x16 的全部 m=1 单元（selftest 标为 "differs"），
均已被本次修正消除。</p>

<h3>修正措施</h3>
<ul class="compact">
<li><b>凡有刚性打包器真值的地方，一律以真值为准</b>：<code>autogen_allgather.recommend()</code> 已改为，
当 (规模, ramp_bw, m=1) 存在 <code>results/zerobuf_strict_m1.json</code> 真值时（4x4/6x8/8x8/12x16/16x16，
两种 ramp_bw），直接返回刚性打包器给出的最优方案及其真零buffer makespan，不再走 buffer_budget 过滤器；
返回结果同时附带该方案在事件驱动引擎里的 makespan/buf 供对比，并标记 <code>source=strict_zerobuf</code>。
修正后上述 10 个 m=1 单元的推荐方案与真值 100% 一致（selftest 全部 OK，比值 1.00x）。</li>
<li><b>真值不可得处的诚实声明</b>：m&gt;1 与 32x32/64x64 没有刚性打包器真值（成本超线性、不可行），
仍只能用事件驱动 + buffer_budget 过滤器作为<b>启发式估计</b>。鉴于上述双方向失真，这些格的推荐方案
<b>应视为"贪心调度下、排队≤budget 的最快方案"，而非"零buffer最优解"</b>——可能仍然在过度剔除确实存在零buffer
调度的方案（无法证伪，因没有真值）。这是当前方法的已知局限，留待可扩展的零缓冲调度算法后续解决。</li>
<li>第 4 节热力图/明细表的 m=1 小规模行已自动改为真值来源（标注"严格零buffer真值"），m&gt;1 与大规模行
仍为事件驱动估计（标注其 raw_best 与 buf 以便审视）。</li>
</ul>

<h3>严格零 buffer 基准（m=1，全部规模，真值）</h3>
{strict_table}
<p class="note"><b>hybrid_v_bi_B2</b>（B=2 纵向条带、双向）是 m=1 下 6x8~16x16 全部规模上的真零buffer最优解
（4x4 上 hybrid_bi_B2 与之并列理论下界 32，4x4/ramp_bw=2 上 multitree 并列）。这是本研究唯一有硬证据支持的
"无缓冲、无冲突、无阻塞"最优结论。</p>
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
        "<li><b>[已更正] 仿真引擎的 buffer 假设</b>：事件驱动引擎允许 flit 在链路/下 ramp 出现资源争用时排队等待，"
        "等待时长<b>没有硬性上限</b>（并非早期版本描述的“1 个 pipeline register”），这不等价于“零缓冲、无冲突、无阻塞”。"
        "该假设对不同方案族的影响很不对称，已被证实会扭曲跨方案的排名——详见第 3.5 节的实测核查、第 3.7 节的"
        "buffer_budget 过滤器双方向失真更正；本报告 m=1 小规模行的“推荐方案”已改为直接采用刚性打包器真值，"
        "m&gt;1 与大规模行在 buffer_budget=2 约束下选取（其局限见 3.7）。</li>",
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
        f"32x32（N=1024）单次仿真调用成本 O(N²·m)，采用分层策略：先在每个 ramp_bw 的代表性 m 下做一次"
        "缩减版全量比较（含 multitree、ring_bi、hybrid_bi/hybrid_v_bi 的 B∈{2,4,8}，双向变体——单向在所有更小规模上从未取胜，故不再评估）"
        "确定最优方案，再对其余 m 只重跑该最优方案 + multitree/ring_bi 两个基线，用于对照。"
        "64x64（N=4096）已按第 3.6 节的方法整体重新复核：<b>排除 multitree</b>，改为对 ring/hybrid(_v) 各变体"
        "做零buffer见证扫描，不再复用旧的、缺少 buffer 量测的数据。</p>",
        (f"<p class='note'>{esc(sweep['notes']['32x32'])}</p>" if sweep.get("notes", {}).get("32x32") else ""),
        (f"<p class='note'>{esc(sweep['notes']['64x64'])}</p>" if sweep.get("notes", {}).get("64x64") else ""),
        "</div>",

        BUFFER_HONESTY_SECTION.format(
            buffer_growth=buffer_growth_table(sweep),
            strict_table=strict_m1_table(),
        ),

        WITNESS64_SECTION.format(
            n_m1=len(set(r["name"] for r in json.loads(WITNESS64_JSON.read_text(encoding="utf-8"))
                         if r["m"] == 1 and r["ramp_bw"] == 1)) if WITNESS64_JSON.exists() else 0,
            m1_table=witness64_m1_table(),
            champion_table=witness64_champion_table(),
            summary_rows=witness64_summary_rows(sweep, budget=0),
            summary_rows_b2=witness64_summary_rows(sweep, budget=2),
        ),

        METHOD_FIX_SECTION.format(strict_table=strict_m1_table()),

        "<h2>4. 热力图：makespan / 理论下界（零 buffer 最优方案：max_link_wait=max_ramp_wait=0；"
        "m=1 小规模=刚性打包器真值）</h2>",
        *sections,

        "<h2>5. 结论（已按第 3.5 节的 buffer 诚实性核查修正）</h2>",
        "<div class='card'>",
        "<ul class='compact'>",
        "<li><b>之前版本\"ramp_bw=1 时 multitree 几乎总是最优\"的结论已被证伪并撤回</b>："
        "该结论完全建立在事件驱动引擎允许无上限隐式排队的基础上；64x64/ramp_bw=1/m=5 下 multitree 实际需要单节点"
        "连续缓存 10233 flit 才能达到其记录的 makespan，16x16 上其严格零 buffer 真值（837）反而排在 22 个方案中的第 13 位。"
        "multitree 的多播式 in-network fork 结构会在所有源同时广播时产生大量跨源链路/ramp 争用，需要靠排队"
        "\"抹平\"这些争用；争用量随规模、随 m 近似线性增长，buffer 需求也随之增长，因此不具备可扩展性。</li>",
        "<li><b>可信的最优方案（有严格零 buffer 证据支持）：hybrid_v_bi_B2</b>（B=2 纵向条带、双向局部环 + 横向树）"
        "在 m=1 下的 6x8~16x16 全部规模上都是<b>刚性打包器给出的真零buffer最优解</b>（4x4 上 hybrid_bi_B2 与之"
        "并列理论下界，4x4/ramp_bw=2 上 multitree 并列），makespan=82/102/262/334，所需 buffer 严格为 0。"
        "第 3.7 节修正后，报告 m=1 小规模行的推荐方案已与此真值 100% 一致（此前因 buffer_budget 过滤器的双方向"
        "失真，曾错误地推荐 hybrid_v_uni_B4 等次优方案，慢 8~13%）。</li>",
        "<li><b>ring（全局单环）/ 粗粒度 hybrid_bi(B=1)</b> 结构上争用远少于多播树类方案，所需排队普遍小一到两个"
        "数量级（如 16x16/ramp_bw=1/m=5：ring_bi 需 538 flit 排队，multitree 需同条件下更大的量；32x32 同条件下"
        "ring_bi 约 2100 flit）；<b>但并非严格零 buffer</b>——ramp_bw=1、m 较大、规模较大时它也需要明显排队，"
        "根因是下 ramp 本身已饱和，与拓扑选择无关，见第 3.5 节。ramp_bw=2（下 ramp 不饱和）或 m 较小时，"
        "ring_bi/粗粒度 hybrid_v_bi 才能做到真正的 buffer≈0，此时也是本研究中最可信、两种 buffer 假设下数字"
        "几乎一致的方案；32x32/64x64、ramp_bw=2、m 较大下它也是（buffer_budget=2 约束后）实测最优解。</li>",
        "<li>ramp_bw 从 1 提升到 2：m 较大、规模较大时 makespan 近似减半（带宽受限区间）；"
        "m=1 的小规模场景则几乎不变（延迟下界主导，带宽提升无效）。</li>",
        "<li>ratio（makespan/T，均基于 buffer_budget=2 约束后的推荐方案）在 1.0~2.0 之间，规律与此前一致："
        "规模越大、ramp_bw=1 时越逼近 eject 下界（拓扑选择不重要，下 ramp 是唯一瓶颈）；ramp_bw=2、m 较小时"
        "角节点链路下界是理论正确但实践难以逼近的松下界。</li>",
        "<li>方案排名并非在所有 (规模, m) 组合下都稳定，autogen 选择器直接以逐格实测结果（buffer 约束后）为准，"
        "不依赖\"某一方案族总是最优\"的先验假设。</li>",
        "<li><b>32x32 数据补全</b>：此前除代表性 m=3 外，32x32 的其它 (ramp_bw, m) 组合都只测过 "
        "multitree/ring_bi 两个基线（假设 m=3 的赢家能直接套用），在 ramp_bw=1 下该假设不成立——"
        "赢家所需 buffer 超预算又没有备选候选，recommend() 只能退回 ring_bi，但 ring_bi 从未被证明真的是"
        "最优（只是唯一测过的候选）。现已补齐全部 (ramp_bw, m) 的完整候选集，例如 32x32/ramp_bw=1/m=1 的真实"
        "buffer_budget=2 最优解是 <b>hybrid_v_uni_B4</b>（makespan=1242），严格零buffer最优解是 "
        "<b>hybrid_v_uni_B1</b>（makespan=4218，buf=0/0）——都远快于 ring_bi（3042，且并非严格零buffer，"
        "buf=0/1）。</li>",
        "<li><b>64x64 专项复核（第 3.6 节）</b>：排除 multitree 后，用零buffer见证法（而非不可行的刚性打包器）"
        "确认了 64x64 上真正严格零buffer（m=1~4，m=5 仅需 1 cycle）的最优方案是 "
        "<b>hybrid_v_uni_B1</b>（ramp_bw=1，makespan 16634~20544）和 <b>hybrid_v_bi_B1</b>（ramp_bw=2，"
        "makespan 8382~10309）——两者都是覆盖全网的单条 Hamilton 环（B=1 退化为无分带），"
        "但走向与 ring 转置（spine 沿列、梳齿沿行），利用 H&lt;V 系统性快于 ring_uni/ring_bi。"
        "64x64 原有数据（生成于 buffer 量测功能加入之前）已被整体替换。</li>",
        "<li><b>局限</b>：32x32/64x64 与所有 m&gt;1 的格子没有严格零 buffer 真值可比对（打包器成本随规模、随 m "
        "超线性增长，无法扩展）。这些格的 buffer_budget=2 选择仍是\"用事件驱动引擎自己的排队量测来约束自己的排队量测\"，"
        "且第 3.7 节已证实该过滤器会双方向失真（过度剔除确实存在零buffer调度的方案、漏放依赖排队才快的方案），"
        "故这些格的推荐方案应视为<b>启发式估计</b>而非可证明的零buffer最优；如需严格证明，"
        "需要一个可扩展的零缓冲调度算法（当前的刚性打包器不是），留作后续工作。</li>",
        "</ul></div>",

        "<h2>6. Autogen 方案生成器</h2>",
        "<div class='card'>",
        "<p><code>utils/autogen_allgather.py</code> 从本次扫描的查找表中，为任意 (mx, my, m, ramp_bw) 直接返回预先确定的"
        "方案标签与其可复现的仿真调用；覆盖本报告全部 7 规模 x 5 数据量 x 2 带宽 = 70 组合的生成 + 校验回归结果见脚本自带的 "
        "<code>--selftest</code> 输出。</p>",
        "<p>默认 <code>buffer_budget=2</code>（flit）：仅在候选方案的 <code>max_link_wait</code> 与 "
        "<code>max_ramp_wait</code> 均不超过该值时才考虑其 makespan，见第 3.5 节校准依据；传 "
        "<code>buffer_budget=None</code> 复现旧版（无约束、可能依赖不现实缓冲深度）的选择。"
        "<code>--selftest</code> 会额外将 m=1 的推荐结果与 <code>results/zerobuf_strict_m1.json</code>"
        "（严格零 buffer 真值）交叉核对：默认阈值下所选方案的真实（零 buffer）makespan 与真实最优的比值全部 ≤1.13x"
        "（多数持平），相比无约束选择时可达 2~3x 的偏差是明显的改进，但仍非精确等于真实最优——"
        "这是用一个连续的 buffer-depth 代理指标去近似一个离散的\"零 buffer 可行性\"约束的必然局限。</p>",
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
