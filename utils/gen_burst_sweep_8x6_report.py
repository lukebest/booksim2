#!/usr/bin/env python3
"""Generate HTML report for 8x6 allgather LB=96 tree + burst-buffer DSE."""

from __future__ import annotations

import html
import json
from pathlib import Path

import sched_zerobuf_compare as S
from dse_tree_allgather_6x8 import MX, MY, H, V, coord, nid, axis_ccw_tree
from explore_sp_trees_96 import axis_cw, quad_balanced

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "burst_sweep_8x6.json"
HTML_PATH = ROOT / "results" / "report_burst_sweep_8x6.html"

LABELS = {
    "axis_ccw": "axis+CCW",
    "axis_cw": "axis+CW",
    "quad_balanced": "quad-balanced",
    "quad_nearest": "quad-nearest",
    "dim_xy": "dim-XY",
    "dim_yx": "dim-YX",
    "col_comb3": "col-comb3",
    "nec3": "NEC-3",
    "nec2": "NEC-2",
    "hamilton_bi_tree": "Hamilton bi-tree",
}


def esc(v) -> str:
    return html.escape(str(v))


def mk_cell(mk, lb: int) -> str:
    if mk is None:
        return "<td>—</td>"
    cls = " win" if mk == lb else ""
    return f"<td class='{cls.strip()}'>{mk}</td>" if cls else f"<td>{mk}</td>"


def buffer_table(data: dict) -> str:
    lb = data["model"]["lower_bound"]
    bufs = data["model"]["buffers"]
    head = "".join(f"<th>B={b}</th>" for b in bufs)
    rows = []
    for name, rec in data["schemes"].items():
        dil = rec["tree_dilation"]
        sp = "是" if rec["shortest_path"] else "否"
        cells = []
        for b in bufs:
            entry = rec["makespan_by_buffer"].get(str(b))
            mk = entry["makespan"] if isinstance(entry, dict) else entry
            cells.append(mk_cell(mk, lb))
        minb = rec["min_buffer_for_lb"]
        minb_s = str(minb) if minb is not None else "—"
        rows.append(
            f"<tr><td class='l'>{esc(LABELS.get(name, name))}</td>"
            f"<td>{dil}</td><td>{sp}</td>{''.join(cells)}"
            f"<td>{minb_s}</td></tr>"
        )
    return (
        "<table><thead><tr><th>方案</th><th>树 dilation</th><th>最短路</th>"
        f"{head}<th>达 LB 最小 B</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def sp_table(data: dict) -> str:
    sp = data.get("sp_exploration", {}).get("candidates", {})
    if not sp:
        return "<p class='note'>无数据</p>"
    lb = data["model"]["lower_bound"]
    bufs = data["model"]["buffers"]
    head = "".join(f"<th>B={b}</th>" for b in bufs)
    rows = []
    for name, m in sp.items():
        cells = [mk_cell(m["makespan_by_buffer"].get(str(b)), lb) for b in bufs]
        minb = m["min_buffer_for_lb"]
        rows.append(
            f"<tr><td class='l'>{esc(LABELS.get(name, name))}</td>"
            f"<td>{m['dilation']}</td><td>{m['max_link_multiplicity']}</td>"
            f"<td>{m['topo_period_max']}</td><td>{m['crossbar_out_peak']}</td>"
            f"{''.join(cells)}"
            f"<td>{minb if minb is not None else '—'}</td></tr>"
        )
    return (
        "<table><thead><tr><th>方案</th><th>dilation</th><th>链路复用</th>"
        f"<th>Pmax</th><th>出口峰值*</th>{head}<th>达 LB 最小 B</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        "<p class='note'>* 此处出口峰值为严格 rb=2 非达界排图上的峰值；"
        "贴界 96 排图实测需要 crossbar 出口 8（见下节）。</p>"
    )


_CELL, _MARGIN, _R, _TOP = 46, 26, 8, 30
_C_ARM, _C_V, _C_H, _C_SRC = "#334155", "#2563eb", "#ea580c", "#dc2626"


def _svg_tree(edges, s: int) -> str:
    """Render a single allgather arborescence as an SVG (y-up).

    Arm edges = slate; vertical-fill = blue; horizontal-fill = orange.
    The blue/orange quadrant pattern is what distinguishes CCW from CW.
    """
    sx, sy = coord(s)
    w = _MARGIN * 2 + (MX - 1) * _CELL
    h = _TOP + _MARGIN * 2 + (MY - 1) * _CELL

    def px(x):
        return _MARGIN + x * _CELL

    def py(y):
        return _TOP + _MARGIN + (MY - 1 - y) * _CELL

    defs = "".join(
        f'<marker id="a{cid}" markerWidth="7" markerHeight="7" refX="6" '
        f'refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="{col}"/>'
        f"</marker>"
        for cid, col in (("g", _C_ARM), ("v", _C_V), ("h", _C_H))
    )
    lines = []
    for p, c in edges:
        pxx, pyy = coord(p)
        cxx, cyy = coord(c)
        arm = (cxx == sx) or (cyy == sy)
        if arm:
            col, mid = _C_ARM, "g"
        elif pxx == cxx:
            col, mid = _C_V, "v"
        else:
            col, mid = _C_H, "h"
        x1, y1, x2, y2 = px(pxx), py(pyy), px(cxx), py(cyy)
        dx, dy = x2 - x1, y2 - y1
        d = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / d, dy / d
        sxp, syp = x1 + ux * (_R + 1), y1 + uy * (_R + 1)
        exp, eyp = x2 - ux * (_R + 4), y2 - uy * (_R + 4)
        lines.append(
            f'<line x1="{sxp:.1f}" y1="{syp:.1f}" x2="{exp:.1f}" y2="{eyp:.1f}"'
            f' stroke="{col}" stroke-width="2" marker-end="url(#a{mid})"/>'
        )
    nodes = []
    for y in range(MY):
        for x in range(MX):
            src = (x == sx and y == sy)
            fill = _C_SRC if src else "#fff"
            stroke = _C_SRC if src else "#94a3b8"
            nodes.append(
                f'<circle cx="{px(x)}" cy="{py(y)}" r="{_R}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{2 if src else 1}"/>'
            )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'xmlns="http://www.w3.org/2000/svg"><defs>{defs}</defs>'
        f'{"".join(lines)}{"".join(nodes)}</svg>'
    )


def tree_diagrams() -> str:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    s = nid(3, 2)
    panels = [
        ("axis+CCW", "UR·LL 水平填充 / UL·LR 垂直填充", axis_ccw_tree(s)),
        ("axis+CW", "UR·LL 垂直填充 / UL·LR 水平填充（CCW 的镜像）", axis_cw(s)),
        ("quad-balanced", "与 axis+CW 逐边相同", quad_balanced(s)),
    ]
    cards = "".join(
        f'<figure class="treecard"><figcaption><b>{esc(t)}</b>'
        f'<span>{esc(sub)}</span></figcaption>{_svg_tree(e, s)}</figure>'
        for t, sub, e in panels
    )
    return (
        f'<div class="trees">{cards}</div>'
        '<div class="legend">'
        f'<span><i style="background:{_C_ARM}"></i>十字臂（源行/列）</span>'
        f'<span><i style="background:{_C_V}"></i>垂直填充边</span>'
        f'<span><i style="background:{_C_H}"></i>水平填充边</span>'
        f'<span><i style="background:{_C_SRC};border-radius:50%"></i>源节点 (3,2)</span>'
        "</div>"
    )


def grid_table(data: dict) -> str:
    grid = data.get("outcap_buffer_grid", {}).get("makespan_by_outcap_buffer", {})
    if not grid:
        return "<p class='note'>无数据</p>"
    lb = data["model"]["lower_bound"]
    bufs = data["model"]["buffers"]
    head = "".join(f"<th>B={b}</th>" for b in bufs)
    rows = []
    for oc in sorted(grid, key=int):
        cells = [mk_cell(grid[oc].get(str(b)), lb) for b in bufs]
        rows.append(f"<tr><td>out={oc}</td>{''.join(cells)}</tr>")
    return (
        "<table><thead><tr><th>crossbar 出口上限</th>"
        f"{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def main() -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    m = data["model"]
    lb = m["lower_bound"]
    cheap = data.get("outcap_buffer_grid", {}).get("cheapest_lb_config", {})
    gen = esc(data["generated_at"])
    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>8×6 Allgather 达界 96：树排图 × 突发 Buffer × Crossbar 出口 DSE</title>
<style>
:root{{--bg:#f8fafc;--card:#fff;--text:#0f172a;--muted:#64748b;--line:#cbd5e1;
--accent:#1d4ed8;--win:#dcfce7;--warn:#fef3c7;}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
margin:0;padding:28px 32px 64px;line-height:1.55;max-width:1180px}}
h1{{font-size:1.5rem;margin:0 0 4px}} h2{{font-size:1.15rem;color:#1e3a8a;margin:0 0 12px}}
h3{{font-size:1rem;margin:14px 0 8px;color:#334155}}
.sub,.note{{color:var(--muted);font-size:.86rem}}
.card{{background:var(--card);border:1px solid #e2e8f0;border-radius:10px;
padding:18px 22px;margin:16px 0}}
.hero{{border-color:#93c5fd;background:linear-gradient(180deg,#eff6ff,#fff)}}
.formula{{font-family:ui-monospace,Menlo,monospace;background:#f1f5f9;border-radius:6px;
padding:10px 12px;margin:8px 0;font-size:.88rem}}
table{{border-collapse:collapse;width:100%;font-size:.82rem;margin:8px 0}}
th,td{{border:1px solid var(--line);padding:6px 8px;text-align:center}}
th{{background:#e2e8f0}} td.l{{text-align:left}} td.win{{background:var(--win);font-weight:700}}
ul{{margin:6px 0;padding-left:22px}} li{{margin:6px 0}}
code{{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:.9em}}
.diag{{background:#f8fafc;border:1px dashed #94a3b8;border-radius:8px;padding:14px 16px;
font-family:ui-monospace,Menlo,monospace;font-size:.8rem;white-space:pre;line-height:1.45;
overflow-x:auto;margin:10px 0}}
.callout{{background:var(--warn);border-left:4px solid #d97706;padding:10px 14px;
border-radius:0 8px 8px 0;margin:12px 0}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:12px 0}}
.kpi div{{background:#f1f5f9;border-radius:8px;padding:12px 14px}}
.kpi b{{display:block;font-size:1.35rem;color:#1d4ed8}}
.kpi span{{font-size:.8rem;color:var(--muted)}}
.trees{{display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;margin:10px 0}}
.treecard{{margin:0;flex:1 1 300px;background:#fff;border:1px solid #e2e8f0;
border-radius:8px;padding:8px 10px 10px;text-align:center}}
.treecard figcaption{{margin-bottom:4px}}
.treecard figcaption b{{color:#1e3a8a}}
.treecard figcaption span{{display:block;font-size:.76rem;color:var(--muted)}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;font-size:.8rem;color:var(--muted);margin:6px 2px}}
.legend i{{display:inline-block;width:14px;height:8px;margin-right:5px;vertical-align:middle}}
</style></head><body>

<h1>8×6 Mesh Allgather：达形式化下界 96 的树排图与微架构代价</h1>
<p class="sub">H=7 · V=9 · rb=2 · m=1 · crossbar→FIFO 写宽≤4 · FIFO→PE 排空=2 ·
数据源 <code>burst_sweep_8x6.json</code> · 生成 {gen}</p>

<div class="card hero">
<h2>核心结论</h2>
<div class="kpi">
  <div><b>{lb}</b><span>形式化下界 T<sub>LB</sub>（cy）</span></div>
  <div><b>{cheap.get('out_cap', 8)}</b><span>达界所需 crossbar 出口/拍</span></div>
  <div><b>{cheap.get('buffer', 2)}</b><span>达界最小突发 FIFO 深度</span></div>
  <div><b>15 / 42</b><span>Pmax / 最大链路复用</span></div>
</div>
<ul>
<li><b>唯一可达下界的树家族</b>：最短路 axis 族（axis+CCW / axis+CW / quad-balanced），三者性能与代价等价。</li>
<li><b>最省达界配置</b>：crossbar 出口带宽 <b>8/拍</b> + eject 突发 buffer 深度 <b>2</b> → makespan=<b>96</b>。两项缺一不可。</li>
<li><b>突发 buffer 只对 axis 族有效</b>：B=0→110，B=1→98，B≥2→96；col-comb3 / NEC / dim / Hamilton 对 B 完全不敏感。</li>
<li><b>微架构代价</b>：Pmax=15（时隙表深度）、链路复用=42、峰值 crossbar 出口=8（=4 mesh 转发 + 4 eject 写入）。</li>
<li>若不允许宽 eject 写入（严格 down_cap=rb=2）：改选 <b>col-comb3</b>（makespan 114），无法达 96。</li>
</ul>
</div>

<div class="card">
<h2>1. 微架构模型</h2>
<div class="diag">每拍、每个路由器：
  mesh 入口 / 本地注入
           │
           ▼
     ┌───────────┐     ≤4 flit/拍 写入
     │  crossbar │ ──────────────────► eject FIFO（深度 B）
     └───────────┘                          │
           │                                │  PE / 下 ramp 以 rb=2 排空
           ▼                                ▼
     ≤1 flit/拍/有向链路                 本地 PE
</div>
<ul>
<li><code>B=0</code> ≡ 严格每拍 eject 到达 ≤2（无突发吸收）。</li>
<li><code>B≥1</code> 允许 crossbar 一拍写入最多 4 个 flit，多余部分由 FIFO 暂存。</li>
<li>completion = 最后一次 PE 取走 flit 的周期 + ramp。</li>
<li>形式化下界：<code>T_LB = max(diameter_serialization, receiver_release, …) = 96</code>
（8×6、H=7、V=9、m=1、rb=2）。</li>
</ul>
</div>

<div class="card">
<h2>2. 突发 Buffer 扫描（B ∈ {{0,1,2,4,8,11}}）</h2>
{buffer_table(data)}
<ul>
<li>只有 <b>axis+CCW</b> 达到 LB=96，且最小 buffer 深度为 <b>2</b>。</li>
<li>dim-XY / dim-YX 虽也是最短路（dilation=96），但被有向链路拥塞卡在 126 / 120，加 buffer 无效。</li>
<li>col-comb3（114）、NEC-3（118）等 dilation &gt; 96，无论多大 buffer 都不可能到 96。</li>
</ul>
</div>

<div class="card">
<h2>3. 最短路树探索（寻找更省的达界方案）</h2>
{sp_table(data)}
<ul>
<li>axis+CW、quad-balanced 与 axis+CCW <b>完全打平</b>（同 makespan / 同 B / 同 Pmax / 同链路复用）。</li>
<li>quad-nearest 把链路复用降到 30、Pmax 降到 10，但 makespan 卡在 <b>104</b>（差 8 cy），说明
“降低拥塞”与“贴下界”存在结构冲突。</li>
</ul>
</div>

<div class="card">
<h2>3b. 三种达界最短树示意（源 = (3,2)）</h2>
<p>三者都由“十字臂（源行+源列洪泛）+ 四象限单向填充”构成，<b>差别只在象限的填充朝向</b>。
下图按填充方向着色：<span style="color:{_C_V}">蓝=垂直填充</span>、
<span style="color:{_C_H}">橙=水平填充</span>、灰=十字臂。</p>
{tree_diagrams()}
<ul>
<li><b>axis+CCW</b>：右/上臂逆时针 90° 展开 → UR、LL 象限<b>水平</b>填充，UL、LR 象限<b>垂直</b>填充。</li>
<li><b>axis+CW</b>：顺时针 90° 展开，正好是 CCW 的镜像 → 蓝橙象限<b>整体对调</b>（UR、LL 变垂直，UL、LR 变水平）。</li>
<li><b>quad-balanced</b>：按“象限平衡”规则挑父臂，结果与 axis+CW <b>逐边完全相同</b>——即 CW 手性。
因此达界最短树本质只有 <b>两种手性</b>（CCW / CW），三条命名里 axis+CW≡quad-balanced。</li>
<li>两种手性关于源点中心对称，聚合指标（makespan、Pmax=15、链路复用=42、达界最小 B=2）完全一致，
可按布线/热点方向任选其一。</li>
</ul>
<p class="note">早前 <code>explore_sp_trees_96.py</code> 的 axis_cw 因象限赋值笔误退化成 CCW（与 CCW 同图），
现已修正为真正的 CW 镜像。</p>
</div>

<div class="card">
<h2>4. Crossbar 出口带宽 × Buffer 联合扫描（axis+CCW）</h2>
<p>定义每拍出口数 = <b>mesh 转发条数 + eject FIFO 写入条数</b>。贴界 96 排图实测峰值恒为
<b>4 + 4 = 8</b>。</p>
{grid_table(data)}
<div class="callout">
<b>达 96 的最省配置：out_cap = {cheap.get('out_cap', 8)}，buffer = {cheap.get('buffer', 2)}。</b><br>
· out&lt;8：永远达不到 96（out=7 停在 98，out=6 停在 100，out=5 停在 107）。<br>
· out=8 但 B&lt;2：达不到 96（B=0→110，B=1→98）。<br>
· out≥8 且 B≥2：96，再加大无收益。
</div>
</div>

<div class="card">
<h2>5. “crossbar 出口 8/拍”如何理解</h2>
<p>指同一拍内，一个路由器的 crossbar 需要同时驱动 <b>8 个输出 flit</b>。贴界 schedule 的峰值构成固定为：</p>
<div class="formula">8 = 4（N/E/S/W 四条 mesh 出链路满载） + 4（写入 eject FIFO）</div>
<ul>
<li><b>4 mesh 转发</b>：该 router 替 4 棵不同源的 allgather 树同时接力（每条有向链路 ≤1/拍）。</li>
<li><b>4 eject 写入</b>：同一拍有 4 个“目的地是本节点”的 flit 到达并写入 FIFO；
PE 仍按 <b>2/拍</b> 读走，多出的 2 靠深度-2 FIFO 吸收。</li>
<li>普通 5 端口路由器一拍最多约 5 出口（4 mesh + 1 eject）；这里要求 <b>eject 写侧 4 宽</b>，
总并发从 5 提到 8。</li>
<li>注意：严格 rb=2 非达界排图上曾测到出口峰值 6（4 mesh + 2 eject）；那是 makespan=110
的 schedule，<b>不是</b>贴界 96 所需带宽。</li>
</ul>
</div>

<div class="card">
<h2>6. Pmax=15 与链路复用=42 如何理解</h2>
<h3>链路复用 = 42</h3>
<ul>
<li>48 个源各自一棵树；<b>最忙的一条有向链路被 42 棵树使用</b>。</li>
<li>实测最忙链路示例：<code>(6,5)→(7,5)</code>（通往最右列的水平边）。</li>
<li>因每条链路每拍 ≤1 flit，这 42 个 flit 必须错开到不同周期 → 直接推高可重放流水的 II 下界，
并迫使各源错峰注入。</li>
</ul>
<h3>Pmax = 15</h3>
<ul>
<li>每个路由器把随时间变化的 crossbar 连接模式（in_dir→out_dir，不含本地 inject/eject）
压成最短可重复周期；Pmax 取全网最大。</li>
<li>实测最差路由器在 <code>(3,2)</code>：span 内出现 15 个互异连接配置，最短周期 = 15。</li>
<li><b>Pmax 越大 → 时隙表/日历越深、控制越复杂</b>。对照 col-comb3 / NEC-3 的 Pmax≈3，
axis 族用更深日历换取贴界时延。</li>
</ul>
<div class="formula">贴界代价轴：时延 96  ↔  Pmax=15 + 链路复用=42 + out=8 + FIFO=2
省时隙表/降拥塞（Pmax≈3、复用≈27）↔  makespan ≥114，无法达 96</div>
</div>

<div class="card">
<h2>7. 决策建议</h2>
<ul>
<li><b>目标贴下界、可接受宽 eject 写口</b>：选 axis+CCW（或等价的 axis+CW / quad-balanced）；
实现 crossbar 出口 8/拍 + eject FIFO 深度 2；接受 Pmax=15、链路复用 42。</li>
<li><b>严格 rb=2、不允许突发写入</b>：选 col-comb3（114）；NEC-3（118）为次选；
axis+CCW 在此约束下约 110，仍不及 col-comb3 的微架构友好度（Pmax=3）。</li>
<li><b>长消息循环重放</b>：若锁定 48 项槽表，另见 <code>tree_m1_uarch_8x6_dse.json</code>
中的 CP-SAT II（col-comb3≈41、NEC-3≈40、Hamilton-bi≈33）；与本报告的 m=1 贴界问题正交。</li>
<li>下一阶段建议：RTL 评估 “4-wide eject write + depth-2 FIFO” 相对标准 2-wide eject 的面积增量，
并验证双 issue / 深度≥15 的 SparseCal 是否可接受。</li>
</ul>
</div>

<p class="note">生成脚本：<code>utils/gen_burst_sweep_8x6_report.py</code> ·
排图/扫描：<code>utils/dse_burst_sweep_8x6.py</code>、<code>utils/explore_sp_trees_96.py</code>、
<code>utils/explore_outcap_96.py</code></p>
</body></html>"""
    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
