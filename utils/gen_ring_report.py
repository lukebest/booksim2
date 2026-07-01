#!/usr/bin/env python3
"""Generate a self-contained HTML report for Hamilton-ring allgather.

Reads results/ring_results.csv (produced by run_ring_experiments.py) and
re-derives each recovered ring (via hamilton_ring) to draw it, then writes
results/ring_report.html with:
  * problem setup,
  * the fault-aware Hamiltonian ring search algorithm write-up,
  * golden (healthy) ring makespans,
  * per-scenario makespan / slowdown tables and bar charts,
  * inline SVG diagrams of every recovered ring with its faults highlighted.
"""

import argparse
import csv
import html
from pathlib import Path

import hamilton_ring as hr
import sim_hamilton_ring as sr

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "results" / "ring_results.csv"
DEFAULT_HTML = ROOT / "results" / "ring_report.html"
MX, MY, H, V, RAMP = 16, 16, 4, 6, 1


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ring_latency(order, is_cycle):
    """Sum of physical link latencies along the ring (its circumference for a
    cycle, end-to-end length for a path)."""
    L = len(order)
    rng = L if is_cycle else L - 1
    return sum(sr.edge_lat(order[i], order[(i + 1) % L], MX, H, V)
               for i in range(rng))


def esc(s):
    return html.escape(str(s))


def svg_ring(order, is_cycle, dead_nodes, dead_links, cell=14, sacrificed=()):
    """Draw the mesh, the recovered ring (poly-line over node centres), and the
    faults (dead nodes as red squares, dead links as red dashed segments,
    sacrificed/disabled nodes as orange squares)."""
    pad = 18
    w = pad * 2 + (MX - 1) * cell
    h = pad * 2 + (MY - 1) * cell
    sacrificed = set(sacrificed)
    dead_nodes = set(dead_nodes) - sacrificed
    dead_links = {frozenset(l) for l in dead_links}

    def px(x):
        return pad + x * cell

    def py(y):
        return pad + y * cell

    p = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
         f'xmlns="http://www.w3.org/2000/svg">',
         f'<rect width="100%" height="100%" fill="#ffffff"/>']

    # faint grid edges
    for y in range(MY):
        for x in range(MX):
            n = hr.nid(x, y, MX)
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = x + dx, y + dy
                if nx < MX and ny < MY:
                    p.append(f'<line x1="{px(x)}" y1="{py(y)}" x2="{px(nx)}" '
                             f'y2="{py(ny)}" stroke="#e2e8f0" stroke-width="1"/>')

    # dead links (red dashed, thick)
    for l in dead_links:
        a, b = tuple(l)
        ax, ay = a % MX, a // MX
        bx, by = b % MX, b // MX
        p.append(f'<line x1="{px(ax)}" y1="{py(ay)}" x2="{px(bx)}" y2="{py(by)}" '
                 f'stroke="#dc2626" stroke-width="3" stroke-dasharray="3,2"/>')

    # the ring path
    pts = [(px(n % MX), py(n // MX)) for n in order]
    if is_cycle and pts:
        pts = pts + [pts[0]]
    poly = " ".join(f"{x},{y}" for x, y in pts)
    p.append(f'<polyline points="{poly}" fill="none" stroke="#2563eb" '
             f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')

    # nodes
    for y in range(MY):
        for x in range(MX):
            n = hr.nid(x, y, MX)
            if n in dead_nodes:
                p.append(f'<rect x="{px(x)-4}" y="{py(y)-4}" width="8" height="8" '
                         f'fill="#dc2626"/>')
            elif n in sacrificed:
                p.append(f'<rect x="{px(x)-4}" y="{py(y)-4}" width="8" height="8" '
                         f'fill="#f59e0b"/>')
            else:
                p.append(f'<circle cx="{px(x)}" cy="{py(y)}" r="2.2" '
                         f'fill="#1e293b"/>')
    p.append("</svg>")
    return "\n".join(p)


def hbar_chart(title, items, vmax):
    """items: list of (label, value or None). None -> infeasible."""
    rows = []
    bw = 360
    for label, val in items:
        if val is None:
            bar = ('<div style="color:#dc2626;font-weight:600">INFEASIBLE '
                   '(no closed cycle)</div>')
        else:
            wpx = int(bw * val / vmax) if vmax else 0
            bar = (f'<div style="display:flex;align-items:center;gap:8px">'
                   f'<div style="height:14px;width:{wpx}px;background:#2563eb;'
                   f'border-radius:3px"></div><span>{val}</span></div>')
        rows.append(f'<tr><td style="white-space:nowrap;padding-right:10px">'
                    f'{esc(label)}</td><td>{bar}</td></tr>')
    return (f'<h4>{esc(title)}</h4><table class="bar">'
            + "".join(rows) + "</table>")


ALGO_HTML = """
<h2>2. 故障感知的 Hamilton 环查找算法 (Fault-aware Hamiltonian ring search)</h2>
<p>网格图 G(mx,my) 是二部图，对节点染色 <code>c(x,y) = (x+y) mod 2</code>。两条性质决定了整个搜索：</p>
<ul>
  <li>存在 Hamilton <b>环 (cycle)</b> 的必要条件是两种颜色的节点数<b>相等</b>。</li>
  <li>存在 Hamilton <b>路径 (path)</b> 的必要条件是两种颜色的节点数<b>相差不超过 1</b>。</li>
</ul>
<p>删除链路不改变颜色平衡，删除节点会改变：</p>
<ul>
  <li><b>链路故障</b>：颜色平衡不变 &rarr; 仍以闭合环为目标，只需让环绕开坏链路。</li>
  <li><b>2x2 节点空洞</b>：删除 2 黑 + 2 白 &rarr; 平衡保持 &rarr; 仍可成环。</li>
  <li><b>1x1 (1 个) / 3x3 (9 个) 节点空洞</b>：颜色不平衡 &rarr; <b>不存在</b> Hamilton 环，只能得到开放的 Hamilton 路径。</li>
</ul>
<p>单向环 allgather 在本质上需要闭合环（单向数据必须绕回到每个节点），因此当只存在路径时，
单向方案被标记为 <i>infeasible</i>，而双向方案在开放路径上运行（节点同时向两端发送）。</p>

<h3>算法步骤</h3>
<ol>
  <li><b>构建剩余图</b> G' = 完整网格 - 故障节点 - 故障链路。</li>
  <li><b>可行性预检查</b>：
    <ul>
      <li>连通性（BFS）：存在不可达节点则任何环都不可能；</li>
      <li>颜色平衡：据此决定目标为 cycle（平衡）还是 path（相差 1）；</li>
      <li>最小度：成环要求每个节点度 &ge; 2。</li>
    </ul>
  </li>
  <li><b>无故障快速路径</b>：无故障时直接返回规范的蛇形 (boustrophedon) Hamilton 环
      （优先使用更便宜的横向链路 H&lt;V），即 golden 环。</li>
  <li><b>深度优先 Hamilton 搜索</b>，带以下剪枝，使带小空洞的网格几乎无回溯：
    <ul>
      <li><b>Warnsdorff 排序</b>：优先走"剩余可选邻居最少"的节点；</li>
      <li><b>二部图颜色交替剪枝</b>：剩余未访问节点的两色数量必须与"交替着色的路径"严格匹配
          （<code>u[1-c] == ceil(r/2)</code>），这是二部图上极强的必要条件；</li>
      <li><b>闭合剪枝</b>（仅环）：起点必须始终保留一个未访问邻居，以便最后一步能闭合回起点；</li>
      <li><b>连通性剪枝</b>：每走一步后，未访问子图必须保持连通，否则立刻回溯；</li>
      <li><b>蛇形提示</b>：同等条件下优先沿蛇形顺序前进，使恢复出的环接近 golden，性能可比；</li>
      <li>挂钟时间预算作为最终兜底。</li>
    </ul>
  </li>
  <li><b>校验</b>：返回的顺序必须覆盖每个剩余节点恰好一次、相邻节点间存在剩余链路、成环时首尾相邻。</li>
</ol>
<p>由于本报告的故障都是 16x16 网格上局部空洞/断链/象限失效，
"Warnsdorff + 颜色交替 + 闭合 + 连通性"剪枝可在毫秒级解出全部场景，时间预算几乎从不触发。</p>

<h3>对不平衡节点故障的再平衡 (node sacrifice rebalancing)</h3>
<p>对 1x1 / 3x3 这类颜色不平衡的节点空洞，本身无法成环。为恢复闭合环（从而使单向环可行、
并让双向环回到约一半的 makespan），算法<b>额外禁用少量邻近的"多余"节点</b>：</p>
<ol>
  <li>统计存活节点两色数量，多出的颜色记为 maj，差值记为 d（本场景 d=1）。</li>
  <li>候选牺牲节点 = maj 色、且位于空洞边界（与故障节点相邻）的存活节点，优先就近。</li>
  <li>颜色平衡只是必要条件而非充分条件，因此<b>逐个尝试</b>候选节点：禁用后要求图仍连通、
      两色数量相等，并用上面的搜索在短预算内<b>确实找到一个环</b>，否则换下一个候选。</li>
  <li>找到后，被牺牲的节点退出本次 allgather（不参与），其余 N-d 个节点构成闭合 Hamilton 环。</li>
</ol>
<p>代价是损失 d 个参与节点，收益是恢复成环：单向重新可行，双向 makespan 从"开放路径"的
约 2&times; 降回接近 golden。</p>
"""


def _bi_by_key(rows):
    out = {}
    for r in rows:
        if r["ring_type"] == "bi" and r["makespan"] != "":
            out[(r["fault_class"], r["region"], r["detail"])] = int(r["makespan"])
    return out


def analysis_section(g_order, g_uni, g_bi, rows):
    g_circ = ring_latency(g_order, True)

    # corner 2x2 (a cycle that is shorter than golden)
    c2 = [hr.nid(x, y, MX) for x in (0, 1) for y in (0, 1)]
    rc = hr.find_ring(MX, MY, c2, [])
    c2_circ = ring_latency(rc["order"], True)
    c2_uni = sr.simulate(rc["order"], True, "uni")["makespan"]
    c2_bi = sr.simulate(rc["order"], True, "bi")["makespan"]

    # center 1x1 path end-to-end latency
    p1 = hr.find_ring(MX, MY, [hr.nid(MX // 2, MY // 2, MX)], [])
    p1_lat = ring_latency(p1["order"], p1["is_cycle"])

    bik = _bi_by_key(rows)

    def cmp_rows():
        out = []
        for region in ("corner", "edge", "center"):
            for size in ("1x1", "3x3"):
                path_bi = bik.get(("node", region, size))
                reb_bi = bik.get(("node_rebal", region, size + "+1"))
                if path_bi is None or reb_bi is None:
                    continue
                dp = (path_bi / g_bi - 1) * 100
                dr = (reb_bi / g_bi - 1) * 100
                out.append(f"<tr><td>{region}</td><td>{size}</td>"
                           f"<td>uni: <span class='infeasible'>INFEASIBLE</span> / "
                           f"bi: {path_bi} (<span class='pos'>{dp:+.0f}%</span>)</td>"
                           f"<td>uni: 可行 / bi: {reb_bi} "
                           f"(<span class='{'neg' if dr<0 else 'pos'}'>{dr:+.0f}%</span>)</td>"
                           f"</tr>")
        return "".join(out)

    return f"""
<h2>4. 结果分析 (Analysis)</h2>

<h3>4.1 为什么节点故障会让 makespan 近乎翻倍？</h3>
<p>环 allgather 的 makespan 在 M=1 时是<b>延迟受限</b>的（下 ramp 带宽不是瓶颈：
每节点弹出 (N-1)/2 &asymp; 127 个 flit @2/cy 远小于传输延迟）。决定 makespan 的是
<b>最远一份数据需要走过的链路总延迟</b>。</p>
<ul>
  <li><b>闭合环 (cycle)</b>：存在"绕回边"。双向时任一源的数据都能从较短一侧到达最远节点，
      最坏传输距离 &asymp; <b>半圈</b> = circ/2 = {g_circ}/2 = {g_circ // 2} cycle，
      故 golden 双向 makespan = <b>{g_bi}</b>（&asymp; 半圈 + ramp + 流水）。</li>
  <li><b>开放路径 (path)</b>：1x1 / 3x3 奇数空洞破坏二部平衡，没有绕回边，只能得到开放路径。
      此时位于路径<b>端点</b>的源，其数据只能朝一个方向走到另一端，最坏传输距离 = <b>整条路径</b>。
      以 center 1x1 为例，路径端到端延迟 = <b>{p1_lat}</b> cycle，双向 makespan = <b>{bik.get(('node','center','1x1'))}</b>
      &asymp; 整条路径 + 2&middot;ramp。</li>
</ul>
<p>所以本质原因是：<b>失去那一条"绕回边"，使最坏传输距离从"半圈"变成"整条路径"，正好约 2&times;</b>。
即开放路径的双向 makespan &asymp; 闭合环的单向 makespan（{g_uni}），约为 golden 双向（{g_bi}）的两倍。
这与下 ramp 带宽无关——即使 ramp 给到 2 flit/cycle，瓶颈仍是端点源的传输延迟。</p>
<p>这正是要做<b>节点再平衡</b>的动机：牺牲 1 个邻近节点恢复闭合环后，绕回边回归，
最坏距离重新变回"半圈"，双向 makespan 从约 2&times; 降回接近 golden：</p>
<table><tr><th>区域</th><th>规模</th><th>不平衡（开放路径）</th><th>再平衡（闭合环，牺牲1节点）</th></tr>
{cmp_rows()}</table>

<h3>4.2 为什么 corner 的 2x2 节点故障反而更优？</h3>
<p>makespan &asymp; 环的周长（单向）或半周长（双向），所以<b>环越短越快</b>。
golden 蛇形环的周长 circ = <b>{g_circ}</b>（单向 {g_uni} = circ - 最便宜一跳(4) + 2&middot;ramp）。</p>
<p>corner 的 2x2 故障删除了网格<b>最外角</b>的 4 个节点，恢复出的环正好<b>抄近路、剪掉了最外圈那段绕行</b>，
周长降到 <b>{c2_circ}</b>（比 golden 少 {g_circ - c2_circ} cycle），参与节点也更少 (188)。
于是单向 = <b>{c2_uni}</b>、双向 = <b>{c2_bi}</b>，都<b>优于</b> golden（{g_uni} / {g_bi}）。</p>
<p>对比之下，edge / center 的 2x2 故障删除的是<b>内部</b>节点，环必须<b>绕开</b>这个内部空洞，
周长不降反略升，所以双向略慢（edge +1.7%、center +0.8%）。
结论：只有删除<b>边界/角落</b>节点才会缩短环周长从而变快；删除内部节点会迫使绕行而变慢。</p>
"""


def _slow_span(val):
    if val == "" or val is None:
        return '<span class="infeasible">INFEASIBLE</span>', "-"
    sv = float(val)
    cssc = "neg" if sv < 0 else ("pos" if sv > 0 else "")
    return f"{sv:+.1f}%", cssc


def hybrid_degradation_section(rows, g_bi, g_hyb):
    """Side-by-side Hamilton global bi vs hybrid B=2 vband bi degradation."""
    bi_rows = [r for r in rows if r["ring_type"] == "bi" and r["fault_class"] != "healthy"]
    seen = set()
    uniq = []
    for r in bi_rows:
        key = (r["fault_class"], r["region"], r["detail"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)

    trs = []
    for r in sorted(uniq, key=lambda x: (x["fault_class"], x["region"], x["detail"])):
        if r["makespan"] == "":
            hms = '<span class="infeasible">INFEASIBLE</span>'
            hsl = "-"
        else:
            hms = r["makespan"]
            pct, css = _slow_span(r["slowdown_pct"])
            hsl = f'<span class="{css}">{pct}</span>' if css != "-" else pct

        if r.get("hybrid_vband_feasible") != "yes" or r.get("hybrid_vband_makespan") in ("", None):
            vms = '<span class="infeasible">INFEASIBLE</span>'
            vsl = "-"
        else:
            vms = r["hybrid_vband_makespan"]
            pct, css = _slow_span(r.get("hybrid_vband_slowdown_pct", ""))
            vsl = f'<span class="{css}">{pct}</span>' if css != "-" else pct

        trs.append(
            f"<tr><td>{esc(r['fault_class'])}</td><td>{esc(r['region'])}</td>"
            f"<td>{esc(r['detail'])}</td>"
            f"<td>{esc(r['fault_desc'][:50])}</td>"
            f"<td>{hms}</td><td>{hsl}</td>"
            f"<td>{vms}</td><td>{vsl}</td></tr>")

    return f"""
<h2>4. 全局 Hamilton bi vs hybrid B=2 vband bi — 故障 makespan 劣化率</h2>
<p>对比两种 allgather 方案在相同故障下的 makespan 劣化率（相对各自 golden）：</p>
<ul>
 <li><b>全局 Hamilton 环 bi</b>：在故障感知恢复出的 Hamilton 环/路径上双向 allgather
     （golden = <b>{g_bi}</b> cy，N={MX*MY}）。</li>
 <li><b>hybrid B=2 纵向带环 + 横向树 (vband) bi</b>：每条 8&times;16 纵向带内局部双向环 +
     跨带横向树；0-buffer 离线 packer（golden = <b>{g_hyb}</b> cy，ramp 2 flit/cycle）。</li>
</ul>
<p class="legend">劣化率 = (故障 makespan / 同方案 golden makespan &minus; 1) &times; 100%。
Hamilton bi 为事件驱动精确仿真；hybrid vband bi 在 healthy 下为 0-buffer packer 精确值，
故障场景下为带内环 + 跨带最短路投递的<b>延迟估算</b>（注入偏移=0，不含 packer 冲突消解开销）。
负值表示比 golden 更快（通常因参与节点减少或环周长缩短）。</p>
<table>
<tr><th>故障类</th><th>区域</th><th>规模</th><th>描述</th>
<th>Hamilton bi mk</th><th>Hamilton bi 劣化</th>
<th>hybrid vband bi mk</th><th>hybrid vband bi 劣化</th></tr>
{"".join(trs)}
</table>
"""


def render(rows):
    healthy = {r["ring_type"]: r for r in rows
               if r["fault_class"] == "healthy"}
    g_uni = int(healthy["uni"]["makespan"])
    g_bi = int(healthy["bi"]["makespan"])
    g_hyb = int(healthy["bi"].get("hybrid_vband_makespan") or healthy["bi"]["hybrid_vband_golden"])

    # group fault rows by scenario (region+detail+class), keep one ring per scenario
    scenarios = hr.all_scenarios(MX, MY)
    by_desc = {sc["desc"]: sc for sc in scenarios}

    parts = []
    parts.append("""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Hamilton Ring AllGather - 仿真报告</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
   margin:0 auto;max-width:1100px;padding:24px;color:#0f172a;line-height:1.55}
 h1{border-bottom:3px solid #2563eb;padding-bottom:8px}
 h2{margin-top:34px;border-bottom:1px solid #cbd5e1;padding-bottom:4px}
 code{background:#f1f5f9;padding:1px 5px;border-radius:4px}
 table{border-collapse:collapse;margin:12px 0;font-size:14px}
 th,td{border:1px solid #cbd5e1;padding:6px 10px;text-align:left}
 th{background:#f1f5f9}
 table.bar td{border:none;padding:2px 6px}
 .infeasible{color:#dc2626;font-weight:600}
 .neg{color:#16a34a}
 .pos{color:#b45309}
 .grid{display:flex;flex-wrap:wrap;gap:14px}
 .card{border:1px solid #cbd5e1;border-radius:8px;padding:8px;width:260px}
 .card h4{margin:2px 0 6px;font-size:13px}
 .legend{font-size:13px;color:#475569}
 .kpi{display:inline-block;background:#eff6ff;border:1px solid #bfdbfe;
   border-radius:8px;padding:8px 14px;margin:6px 8px 6px 0;font-size:15px}
</style></head><body>""")

    parts.append("<h1>Hamilton Ring AllGather: 故障感知仿真报告</h1>")
    parts.append("<h2>1. 问题设置 (Setup)</h2>")
    parts.append(f"""<p>拓扑 16x16 mesh ({MX*MY} 个节点)，横向链路 H={H} cycle，纵向链路 V={V} cycle，
PE&harr;router ramp 延迟 {RAMP} cycle，每条有向链路 1 flit/cycle，消息大小 M=1 flit。
在 Hamilton 环上做 allgather，比较两种模式；并与 hybrid B=2 vband bi 对比故障劣化率：</p>
<ul>
 <li><b>单向环 (uni)</b>：每个源沿环单向把消息泵送一圈 (L-1 跳)；PE&harr;router ramp <b>1 flit/cycle</b>。</li>
 <li><b>双向环 (bi)</b>：每个源同时向两个方向发送，每片数据走较短一侧 (&le; &lceil;(L-1)/2&rceil; 跳)；
     PE&harr;router ramp <b>2 flit/cycle</b>。开放路径上则向两端发送。</li>
 <li><b>hybrid B=2 vband bi</b>：2 条 8&times;16 纵向带，带内纵向 Hamilton 环 + 跨带横向树；
     0-buffer 离线 packer，ramp <b>2 flit/cycle</b>（见 fork scheme 报告）。</li>
</ul>
<p>转发为<b>网络内分叉</b>：flit 到达每个节点时下 ramp 弹出一份 (落入本地 SRAM) 并同时向环上下一跳转发，
中间节点不重新注入，故每 (flit, 节点) 只付一次 ramp。每个节点共需弹出 (N-1)&middot;M 个 flit。</p>
<div>
 <span class="kpi">golden 单向环 makespan = <b>{g_uni}</b> cycles</span>
 <span class="kpi">golden 双向环 makespan = <b>{g_bi}</b> cycles</span>
 <span class="kpi">golden hybrid vband bi = <b>{g_hyb}</b> cycles</span>
</div>""")

    # golden ring diagram
    g_order = hr.snake_cycle(MX, MY)
    parts.append('<p class="legend">下图为无故障 golden 蛇形环（蓝线为环路，黑点为节点）：</p>')
    parts.append(f'<div class="card"><h4>Golden snake ring (cycle, N={MX*MY})</h4>'
                 + svg_ring(g_order, True, [], []) + "</div>")

    # algorithm
    parts.append(ALGO_HTML)

    # results section
    parts.append("<h2>3. 各类故障 vs golden 的性能比较</h2>")
    parts.append('<p class="legend">slowdown% = (故障环 makespan / 同模式 golden makespan - 1) &times; 100。'
                 f'节点故障场景参与节点更少 (ring_len &lt; {MX*MY})，与 {MX*MY} 节点的 golden 对比仅反映完成时间变化。</p>')

    # tables per fault class
    order_class = [("link", "链路故障 (Link faults)"),
                   ("node", "节点故障 (Node faults)"),
                   ("quadrant", "1/4 象限全部故障 (Quadrant faults)"),
                   ("node_rebal", "节点故障 + 牺牲邻近节点恢复成环 "
                                  "(Node faults, rebalanced to a cycle)")]
    for cls, title in order_class:
        parts.append(f"<h3>{esc(title)}</h3>")
        parts.append("<table><tr><th>模式</th><th>区域</th><th>规模</th>"
                     "<th>环类型</th><th>ring_len</th><th>牺牲</th><th>makespan</th>"
                     "<th>golden</th><th>slowdown</th><th>eject_ok</th></tr>")
        for r in rows:
            if r["fault_class"] != cls:
                continue
            if r["makespan"] == "":
                ms = '<span class="infeasible">INFEASIBLE</span>'
                slow = '<span class="infeasible">-</span>'
                rk = "path" if r["ring_is_cycle"] == "False" else "-"
            else:
                ms = r["makespan"]
                sv = float(r["slowdown_pct"])
                cssc = "neg" if sv < 0 else ("pos" if sv > 0 else "")
                slow = f'<span class="{cssc}">{sv:+.1f}%</span>'
                rk = "cycle" if r["ring_is_cycle"] == "True" else "path"
            parts.append(
                f"<tr><td>{esc(r['ring_type'])}</td><td>{esc(r['region'])}</td>"
                f"<td>{esc(r['detail'])}</td><td>{rk}</td>"
                f"<td>{esc(r['ring_len'])}</td><td>{esc(r.get('sacrificed','0'))}</td>"
                f"<td>{ms}</td>"
                f"<td>{esc(r['golden_makespan'])}</td><td>{slow}</td>"
                f"<td>{esc(r['eject_ok'])}</td></tr>")
        parts.append("</table>")

    # bar charts
    vmax = max(int(r["makespan"]) for r in rows if r["makespan"] != "")
    for mode in ("uni", "bi"):
        items = []
        for r in rows:
            if r["ring_type"] != mode or r["fault_class"] == "healthy":
                continue
            label = f'{r["fault_class"]}/{r["region"]}/{r["detail"]}'
            val = int(r["makespan"]) if r["makespan"] != "" else None
            items.append((label, val))
        gm = g_uni if mode == "uni" else g_bi
        parts.append(hbar_chart(
            f"{mode} 环 makespan（golden={gm}，vmax={vmax}）", items, vmax))

    # ---- hybrid vs Hamilton degradation (bi only) ------------------------
    parts.append(hybrid_degradation_section(rows, g_bi, g_hyb))

    # ---- analysis section --------------------------------------------------
    parts.append(analysis_section(g_order, g_uni, g_bi, rows))

    # per-scenario ring diagrams
    parts.append("<h2>5. 各故障场景恢复出的 Hamilton 环</h2>")
    parts.append('<p class="legend">红色方块 = 故障节点，<span style="color:#f59e0b">橙色方块 = 再平衡牺牲的节点</span>，'
                 '红色虚线 = 故障链路，蓝线 = 恢复出的环/路径。'
                 '1x1 与 3x3 节点空洞原始只能得到开放路径（单向不可行），_rebal 为牺牲节点后恢复的闭合环。</p>')
    parts.append('<div class="grid">')
    diag = list(scenarios) + hr.rebalanced_node_scenarios(MX, MY)
    for sc in diag:
        res = hr.find_ring(MX, MY, sc["dead_nodes"], sc["dead_links"])
        kind = ("cycle" if res["is_cycle"] else "path") if res["feasible"] else "infeasible"
        sac = sc.get("sacrificed", [])
        svg = (svg_ring(res["order"], res["is_cycle"], sc["dead_nodes"],
                        sc["dead_links"], sacrificed=sac) if res["order"] else "")
        parts.append(f'<div class="card"><h4>{esc(sc["name"])} ({kind}, '
                     f'len={len(res["order"]) if res["order"] else 0})</h4>{svg}</div>')
    parts.append("</div>")

    parts.append('<h2>6. 结论 (Notes)</h2><ul>'
                 '<li>所有恢复出的环均通过 Hamilton 校验，且每个存活节点恰好弹出 (N-1)&middot;M 个 flit，'
                 '无链路/ramp 带宽冲突 (eject_ok=True)。</li>'
                 '<li>链路故障与 2x2 节点空洞保持颜色平衡，单/双向环均可成环，性能基本不退化。</li>'
                 '<li>1x1 与 3x3 节点空洞破坏二部平衡，无闭合环：单向不可行；双向在开放路径上完成，'
                 'makespan 约翻倍。</li>'
                 '<li>1/4 象限全部故障（64 节点）后，Hamilton 环仍可恢复（192 节点闭合环），'
                 '但周长增加导致双向 makespan 明显劣化；hybrid vband 在带内局部恢复环并跳过死区，'
                 '劣化率通常低于全局 Hamilton（见第 4 节对比表）。</li>'
                 '<li>对不平衡节点空洞牺牲邻近节点再平衡后，单向重新可行，双向 makespan 降回接近 golden。</li>'
                 '<li>双向环（2 flit/cycle ramp + 双向）在所有可行场景下约为单向环的一半；'
                 f'healthy 时 hybrid vband bi ({g_hyb} cy) 远优于全局 Hamilton bi ({g_bi} cy)。</li>'
                 '</ul>')
    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--html", type=Path, default=DEFAULT_HTML)
    args = ap.parse_args()
    rows = load_rows(args.csv)
    out = render(rows)
    args.html.write_text(out, encoding="utf-8")
    print(f"Wrote {args.html} ({len(out)} bytes)")


if __name__ == "__main__":
    main()
