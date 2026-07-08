#!/usr/bin/env python3
"""Generate HTML report: 6x8 2D mesh allgather — three scheme families compared.

Reads results/allgather_scale_sweep.json for ring/hybrid/multitree numbers and
computes row→col two-phase schedules via sched_zerobuf_compare rigid packer.

Output: results/report_allgather_6x8.html
"""

import html
import json
from pathlib import Path

import sched_zerobuf_compare as S

ROOT = Path(__file__).resolve().parents[1]
SWEEP_JSON = ROOT / "results" / "allgather_scale_sweep.json"
HTML_PATH = ROOT / "results" / "report_allgather_6x8.html"

MX, MY = 6, 8
N = MX * MY
FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]
SCHEMES = ["multitree", "ring_uni", "ring_bi", "hybrid_v_bi_B2"]

SCHEME_LABEL = {
    "multitree": "方案一：multitree（X→Y 维序树）",
    "ring_uni": "方案二：ring_uni（全局单向 Hamilton 环）",
    "ring_bi": "方案二：ring_bi（全局双向 Hamilton 环）",
    "hybrid_v_bi_B2": "方案二：hybrid_v_bi_B2（2 纵带环 + 横向 fork）",
    "row_col": "方案三：row→col 二阶段 allgather",
}

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; }
body { font-family: system-ui, -apple-system, sans-serif; margin:0; padding:24px 32px 56px;
       background:var(--bg); color:var(--text); line-height:1.65; max-width:1080px; }
h1 { font-size:1.55rem; margin:0 0 6px; }
h2 { font-size:1.12rem; margin:28px 0 10px; color:#1e3a8a; border-top:1px solid #e2e8f0; padding-top:20px; }
h3 { font-size:1.0rem; margin:16px 0 8px; color:#334155; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:10px;
        padding:20px 24px; margin:16px 0; }
.meta { color:var(--muted); font-size:.9rem; }
.note { color:var(--muted); font-size:.87rem; }
code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:.85em; }
table.data { border-collapse:collapse; font-size:.82rem; margin:12px 0; width:100%; }
table.data th, table.data td { border:1px solid #e2e8f0; padding:6px 10px; text-align:center; }
table.data th { background:#f1f5f9; font-weight:600; }
table.data td.name { text-align:left; }
table.data tr.best td { background:#ecfdf5; font-weight:600; }
table.data tr.zbuf td { background:#eff6ff; }
.tag { display:inline-block; font-size:.72rem; padding:1px 6px; border-radius:4px; margin-left:4px; vertical-align:1px; }
.tag-ok { background:#dcfce7; color:#166534; }
.tag-warn { background:#fef3c7; color:#92400e; }
.tag-info { background:#dbeafe; color:#1e40af; }
ul.compact li { margin:4px 0; }
.formula { font-family: ui-monospace, monospace; background:#f8fafc; border:1px solid #e2e8f0;
           border-radius:6px; padding:8px 12px; margin:8px 0; font-size:.86rem; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:760px) { .grid2 { grid-template-columns:1fr; } }
.bar-wrap { display:flex; align-items:center; gap:8px; margin:4px 0; font-size:.82rem; }
.bar { height:18px; border-radius:4px; min-width:2px; }
.legend-row { display:flex; flex-wrap:wrap; gap:14px; margin:10px 0; font-size:.85rem; }
.legend-row span { display:flex; align-items:center; gap:5px; }
.swatch { width:14px; height:14px; border-radius:3px; display:inline-block; }
"""


def esc(s):
    return html.escape(str(s))


def row_col_schedule(h, v, ramp_bw, m):
    S.cfg(MX, 1, h, v)
    S.init_ring()
    mk1, _, _, ok1 = S.run_scheme(S.fp_multitree, ramp_bw, flits=m)
    S.cfg(1, MY, h, v)
    S.init_ring()
    mk2, _, _, ok2 = S.run_scheme(S.fp_multitree, ramp_bw, flits=MX * m)
    assert ok1 and ok2
    return {
        "T1": mk1,
        "T2": mk2,
        "Ttotal": mk1 + mk2,
        "sram": (MX - 1) * m,
    }


def load_data():
    sweep = json.loads(SWEEP_JSON.read_text(encoding="utf-8"))
    h, v = sweep["h"], sweep["v"]
    out = {}
    for rb in RAMP_BWS:
        out[rb] = {}
        cell_root = sweep["data"]["6x8"]["bw"][str(rb)]
        for m in FLITS:
            cell = cell_root[str(m)]
            res = {r["name"]: r for r in cell["results"]}
            rc = row_col_schedule(h, v, rb, m)
            out[rb][m] = {
                "T": cell["T"],
                "schemes": {nm: res[nm] for nm in SCHEMES},
                "row_col": rc,
            }
    return out, h, v


def buf_cell(link_w, ramp_w, sram=None, router_zero=False):
    parts = []
    if router_zero or (link_w == 0 and ramp_w == 0):
        parts.append('<span class="tag tag-ok">router 0</span>')
    else:
        parts.append(f"link {link_w} / ramp {ramp_w}")
    if sram:
        parts.append(f'<br><span class="tag tag-info">SRAM {sram} flit</span>')
    return "".join(parts)


def scheme_table(data, rb, scheme_key, extra_cols=None):
    rows = []
    for m in FLITS:
        d = data[rb][m]
        if scheme_key == "row_col":
            rc = d["row_col"]
            mk = rc["Ttotal"]
            buf = buf_cell(0, 0, sram=rc["sram"], router_zero=True)
            extra = f"<td>{rc['T1']}</td><td>{rc['T2']}</td><td>{rc['sram']}</td>"
        else:
            r = d["schemes"][scheme_key]
            mk = r["makespan"]
            lw, rw = r["max_link_wait"], r["max_ramp_wait"]
            zbuf = lw == 0 and rw == 0
            buf = buf_cell(lw, rw, router_zero=zbuf)
            extra = ""
        ratio = mk / d["T"] if d["T"] else None
        cls = ""
        rows.append(
            f"<tr class='{cls}'><td>{m}</td><td>{d['T']}</td><td><b>{mk}</b></td>"
            f"{extra}<td>{buf}</td><td>{ratio:.3f}</td></tr>"
        )
    if scheme_key == "row_col":
        hdr = (
            "<table class='data'><thead><tr>"
            "<th>m (flit)</th><th>理论下界 T</th><th>Ttotal</th>"
            "<th>T1 行相</th><th>T2 列相</th><th>SRAM/节点</th>"
            "<th>Buffer 诉求</th><th>mk/T</th></tr></thead><tbody>"
        )
    else:
        hdr = (
            "<table class='data'><thead><tr>"
            "<th>m (flit)</th><th>理论下界 T</th><th>makespan</th>"
            "<th>Buffer 诉求 (link/ramp flit)</th><th>mk/T</th></tr></thead><tbody>"
        )
    return hdr + "".join(rows) + "</tbody></table>"


def compare_table_m1(data):
    """Summary table for ramp_bw=2, m=1."""
    d = data[2][1]
    entries = [
        ("multitree", d["schemes"]["multitree"]["makespan"], 0, 0, 0, "刚性 0-buffer"),
        ("ring_uni", d["schemes"]["ring_uni"]["makespan"], 0, 0, 0, "事件驱动"),
        ("ring_bi", d["schemes"]["ring_bi"]["makespan"], 0, 0, 0, "事件驱动"),
        ("hybrid_v_bi_B2", d["schemes"]["hybrid_v_bi_B2"]["makespan"], 0, 0, 0, "事件驱动"),
        ("row→col", d["row_col"]["Ttotal"], 0, 0, d["row_col"]["sram"], "刚性 0-buffer"),
    ]
    best_mk = min(e[1] for e in entries)
    rows = []
    for name, mk, lw, rw, sram, src in entries:
        cls = "best" if mk == best_mk else ""
        buf = f"router 0" if lw == 0 and rw == 0 else f"link {lw}/ramp {rw}"
        if sram:
            buf += f" + SRAM {sram} flit"
        rows.append(
            f"<tr class='{cls}'><td class='name'>{esc(name)}</td><td>{mk}</td>"
            f"<td>{buf}</td><td class='note'>{src}</td></tr>"
        )
    hdr = (
        "<table class='data'><thead><tr>"
        "<th>方案</th><th>makespan (cy)</th><th>Buffer</th><th>数据来源</th>"
        "</tr></thead><tbody>"
    )
    return hdr + "".join(rows) + "</tbody></table>"


def makespan_bar_svg(data, rb):
    """Grouped bar chart: makespan vs m for all schemes."""
    schemes_plot = [
        ("multitree", "#2563eb"),
        ("ring_uni", "#94a3b8"),
        ("ring_bi", "#059669"),
        ("hybrid_v_bi_B2", "#dc2626"),
        ("row_col", "#7c3aed"),
    ]
    pad_l, pad_t, pad_r, pad_b = 48, 24, 16, 36
    group_w = 88
    bar_w = 14
    gap = 2
    max_mk = max(
        data[rb][m]["schemes"]["multitree"]["makespan"]
        if s != "row_col"
        else data[rb][m]["row_col"]["Ttotal"]
        for m in FLITS
        for s, _ in schemes_plot
    )
    plot_h = 220
    W = pad_l + len(FLITS) * group_w + pad_r
    H = pad_t + plot_h + pad_b
    parts = [
        f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;height:auto;display:block">',
        f'<text x="{pad_l + len(FLITS)*group_w/2:.0f}" y="16" text-anchor="middle" '
        f'font-size="12" font-weight="600" fill="#334155">makespan vs m (ramp_bw={rb})</text>',
    ]
    for i, m in enumerate(FLITS):
        gx = pad_l + i * group_w + group_w / 2
        parts.append(
            f'<text x="{gx:.0f}" y="{H - 8}" text-anchor="middle" '
            f'font-size="11" fill="#475569">m={m}</text>'
        )
        for j, (skey, color) in enumerate(schemes_plot):
            if skey == "row_col":
                mk = data[rb][m]["row_col"]["Ttotal"]
            else:
                mk = data[rb][m]["schemes"][skey]["makespan"]
            bh = (mk / max_mk) * plot_h
            bx = pad_l + i * group_w + 8 + j * (bar_w + gap)
            by = pad_t + plot_h - bh
            parts.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w}" height="{bh:.1f}" '
                f'fill="{color}" rx="2" opacity="0.9"/>'
            )
    parts.append("</svg>")
    legend = '<div class="legend-row">' + "".join(
        f'<span><span class="swatch" style="background:{c}"></span>{esc(s.replace("_"," "))}</span>'
        for s, c in schemes_plot
    ) + "</div>"
    return "\n".join(parts) + legend


def build_html(data, h, v):
    compare_m1 = compare_table_m1(data)
    bars1 = makespan_bar_svg(data, 1)
    bars2 = makespan_bar_svg(data, 2)

    scheme_sections = ""
    for key in ["multitree", "ring_uni", "ring_bi", "hybrid_v_bi_B2"]:
        scheme_sections += f"""
<div class="card">
<h3>{esc(SCHEME_LABEL[key])}</h3>
<h4>ramp_bw = 1</h4>
{scheme_table(data, 1, key)}
<h4>ramp_bw = 2</h4>
{scheme_table(data, 2, key)}
</div>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>6×8 Mesh Allgather 三方案分析</title>
<style>{CSS}</style>
</head>
<body>
<h1>6×8 2D Mesh Allgather 三方案分析</h1>
<p class="meta">Mesh {MX}×{MY}（N={N}），H={h} cy，V={v} cy，上/下 ramp 各 1 cy，下环带宽 ramp_bw ∈ {{1, 2}} flit/cy/节点，数据量 m ∈ {{1..5}} flit/节点。</p>

<div class="card">
<h2>物理模型与 Buffer 定义</h2>
<ul class="compact">
<li><b>刚性 0-buffer 打包器</b>（<code>sched_zerobuf_compare.py</code>）：每个源分配唯一注入偏移，使任意时刻每条有向链路 / 上环 / 下环最多被一个源占用——<b>by construction 不需要 router 内部排队</b>。</li>
<li><b>事件驱动仿真</b>（<code>allgather_fast_sim.py</code>）：允许 router 无限深队列，逐跳按最早可用周期转发；记录的 <code>max_link_wait</code> / <code>max_ramp_wait</code> 表示该热点资源为不丢包所需的最深排队（flit 数）。</li>
<li><b>节点 SRAM 暂存</b>（方案三独有）：行相结束后节点须本地攒够整行数据包 (MX−1)×m flit，再二次上环做列相——<b>不是 router buffer</b>，但是真实的本地存储与同步开销。</li>
</ul>
<div class="formula">理论下界 T = max(弹出下界, 角节点下界, 延迟下界, 二分带宽下界)；6×8 在 m=1,ramp_bw=1 时 T=64 cy（角节点下界紧）。</div>
</div>

<div class="card">
<h2>Executive Summary（ramp_bw=2, m=1）</h2>
{compare_m1}
<p class="note">绿色高亮为全场最快。<b>row→col</b> 在 m=1、高下环带宽下 makespan 最优（71 cy），且 router 严格 0 buffer，仅需 5 flit/节点 SRAM 暂存。</p>
</div>

<div class="card">
<h2>Makespan 随 m 变化</h2>
{bars1}
{bars2}
</div>

<h2>方案一：Tree（multitree）+ 无阻塞·无冲突·router 无 buffer</h2>
<div class="card">
<p>每个源 s 沿本行向左右 fork，再沿每列向上下 fork（X→Y 维序双向树）。用刚性偏移打包使链路/ramp 全程无重叠。</p>
<ul class="compact">
<li><b>m=1</b>：严格 0-buffer 成立（multitree makespan 149@rb=1 / 96@rb=2，max_link_wait=max_ramp_wait=0）。</li>
<li><b>m≥2</b>：高扇出结构下仅靠"每源一个全局偏移"已无法保证 0 重叠；事件驱动仿真显示下环口排队深度随 m 线性上升（rb=1 时 m=5 需 ramp 113 flit 深）。</li>
<li><b>结论</b>：tree 的"0 buffer"承诺是 <b>m=1 专属</b>；数据量增大后要么接受 router 排队，要么退回更慢但仍 0-buffer 的刚性调度。</li>
</ul>
<h4>ramp_bw = 1</h4>
{scheme_table(data, 1, "multitree")}
<h4>ramp_bw = 2</h4>
{scheme_table(data, 2, "multitree")}
</div>

<h2>方案二：ring_uni / ring_bi / hybrid_v_bi_B2</h2>
<div class="card">
<ul class="compact">
<li><b>ring_uni</b>：48 点 Hamilton 单向环，每跳 1 前驱 1 后继，<b>几乎恒 0 buffer</b>（link≤1, ramp=0），但 makespan 最差且几乎不随 ramp_bw 改善。</li>
<li><b>ring_bi</b>：双向环，下环口 2 路汇合；link 侧仍近 0，ramp 侧随 m 增大出现排队（rb=1 最高 104 flit）；<b>rb=2 时 ramp 排队被带宽翻倍压回 0</b>。</li>
<li><b>hybrid_v_bi_B2</b>：2 个 3 列纵带内局部环 + 逐行横向 fork；makespan 全场最优（m=1: 82 cy），但 buffer 诉求也最大（m=5@rb=1: link 56 / ramp 140 flit）。</li>
</ul>
<p class="note">规律：扇出/汇聚度越高 → makespan 越短、router 排队越深。buffer 需求 ring_uni &lt; ring_bi &lt; hybrid_v_bi_B2，与 makespan 优劣正好相反。</p>
</div>
{scheme_sections}

<h2>方案三：先 Row Allgather，后 Column Allgather</h2>
<div class="card">
<h3>算法</h3>
<ol class="compact">
<li><b>行相</b>：每行独立 allgather（6 节点），各节点获得整行 6m flit。</li>
<li><b>列相</b>：每列独立 allgather，转发整行包（6m flit/次注入），完成后每节点持有 48m flit。</li>
</ol>
<h3>Buffer 诉求（两层）</h3>
<ul class="compact">
<li><b>网络内（router/link）</b>：严格 <span class="tag tag-ok">0 buffer</span>，对任意 m 成立——1D  fork 结构 + 刚性偏移在 6×1 / 1×8 虚拟网格上天然无重叠。</li>
<li><b>节点 SRAM</b>：<span class="tag tag-info">(MX−1)×m = 5m flit/节点</span>——第二阶段须等整行到齐、本地落地后再二次上环；tree/ring/hybrid 全程直通转发，无此暂存。</li>
</ul>
<h3>时序（Ttotal = T1 + T2，两阶段严格串行）</h3>
<h4>ramp_bw = 1</h4>
{scheme_table(data, 1, "row_col")}
<h4>ramp_bw = 2</h4>
{scheme_table(data, 2, "row_col")}
<p class="note">rb=2,m=1 时 Ttotal=71 cy 优于全部其他方案；m≥2 因无流水重叠迅速落后（如 rb=1,m=3 时 255 cy vs multitree 146 / hybrid_v_bi_B2 148）。</p>
</div>

<div class="card">
<h2>综合结论</h2>
<table class="data">
<thead><tr><th>维度</th><th>方案一 tree</th><th>方案二 ring/hybrid</th><th>方案三 row→col</th></tr></thead>
<tbody>
<tr><td class="name">m=1 最优 makespan@rb=2</td><td>96 cy</td><td>82 cy (hybrid_v_bi_B2)</td><td><b>71 cy</b></td></tr>
<tr><td class="name">router 0-buffer 适用范围</td><td>仅 m=1</td><td>ring_uni 几乎全程；hybrid 需深队列</td><td>任意 m</td></tr>
<tr><td class="name">额外本地暂存</td><td>0</td><td>0</td><td>5m flit/节点</td></tr>
<tr><td class="name">m 增大趋势</td><td>排队深度线性升</td><td>hybrid 排队最深、makespan 最优</td><td>两阶段串行，大 m 落后</td></tr>
<tr><td class="name">适用场景</td><td>小 payload + 可接受深 router 队列</td><td>追求低 makespan、可开 router buffer</td><td>小 m + 高下环带宽 + 可开 SRAM</td></tr>
</tbody>
</table>
</div>

<p class="meta">Generated by <code>utils/gen_allgather_6x8_report.py</code> · sweep: <code>results/allgather_scale_sweep.json</code></p>
</body>
</html>
"""


def main():
    data, h, v = load_data()
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(build_html(data, h, v), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
