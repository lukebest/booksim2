#!/usr/bin/env python3
"""Generate self-contained HTML report for 16x16 allreduce study.

Reads results/allreduce_results.csv and scheme/bound data from
allreduce_bound + sim_allreduce_16x16, writes results/allreduce_report.html.
"""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path

import allreduce_bound as ab
import hamilton_ring as hr
import sim_allreduce_16x16 as sa

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "results" / "allreduce_results.csv"
DEFAULT_HTML = ROOT / "results" / "allreduce_report.html"
MX, MY, H, V, RAMP = 16, 16, 4, 6, 1
R_LAT = 2


def esc(s):
    return html.escape(str(s))


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def svg_mesh(dead_nodes=(), dead_links=(), highlight_nodes=(), cell=14):
    pad = 18
    w = pad * 2 + (MX - 1) * cell
    h = pad * 2 + (MY - 1) * cell
    dead_nodes = set(dead_nodes)
    dead_links = {frozenset(l) for l in dead_links}
    highlight_nodes = set(highlight_nodes)

    def px(x):
        return pad + x * cell

    def py(y):
        return pad + y * cell

    p = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']
    for y in range(MY):
        for x in range(MX):
            n = hr.nid(x, y, MX)
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = x + dx, y + dy
                if nx < MX and ny < MY:
                    stroke = "#e2e8f0"
                    if frozenset((n, hr.nid(nx, ny, MX))) in dead_links:
                        stroke = "#dc2626"
                    p.append(f'<line x1="{px(x)}" y1="{py(y)}" x2="{px(nx)}" y2="{py(ny)}" '
                             f'stroke="{stroke}" stroke-width="{"3" if stroke != "#e2e8f0" else "1"}" '
                             f'stroke-dasharray="{"3,2" if stroke != "#e2e8f0" else "none"}"/>')
    for y in range(MY):
        for x in range(MX):
            n = hr.nid(x, y, MX)
            if n in dead_nodes:
                p.append(f'<rect x="{px(x)-4}" y="{py(y)-4}" width="8" height="8" fill="#dc2626"/>')
            elif n in highlight_nodes:
                p.append(f'<circle cx="{px(x)}" cy="{py(y)}" r="3.5" fill="#2563eb"/>')
            else:
                p.append(f'<circle cx="{px(x)}" cy="{py(y)}" r="2.2" fill="#1e293b"/>')
    p.append("</svg>")
    return "\n".join(p)


def bound_table_html():
    rows = ab.bound_table()
    out = ["<table><tr><th>M</th><th>树形延迟</th><th>RS+AG ramp</th><th>下行ramp</th>"
           "<th>二分</th><th>直径对</th><th>通用LB</th><th>RS+AG LB</th></tr>"]
    for r in rows:
        out.append(
            f"<tr><td>{r['M']}</td><td>{r['tree_latency']}</td><td>{r['downramp_rsag']}</td>"
            f"<td>{r['downramp_final']}</td><td>{r['bisection']}</td><td>{r['diameter_pair']}</td>"
            f"<td><b>{r['combined']}</b></td><td>{r['combined_rsag']}</td></tr>")
    out.append("</table>")
    return "\n".join(out)


def scheme_table_html():
    json_path = ROOT / "results" / "allreduce_schemes.json"
    if not json_path.exists():
        return "<p>（运行 utils/dump_allreduce_schemes.py 生成方案对比数据）</p>"
    import json
    rows = json.loads(json_path.read_text(encoding="utf-8"))
    by_name = {}
    for r in rows:
        by_name.setdefault(r["name"], {})[r["M"]] = r
    names = sorted(by_name.keys())
    out = ["<table><tr><th>方案</th>"]
    for M in range(1, 7):
        out[0] += f"<th>M={M} mk</th><th>效率</th>"
    out[0] += "</tr>"
    for name in names:
        row = f"<tr><td>{esc(name)}</td>"
        for M in range(1, 7):
            r = by_name[name].get(M)
            if r and r.get("makespan"):
                lb = ab.allreduce_bounds(M, R_LAT)["combined"]
                eff = r["makespan"] / lb
                mark = "" if r.get("ok") else " *"
                row += f"<td>{r['makespan']}{mark}</td><td>{eff:.3f}</td>"
            else:
                row += "<td>-</td><td>-</td>"
        row += "</tr>"
        out.append(row)
    out.append("</table><p class='legend'>* = packer 验证未通过</p>")
    return "\n".join(out)


def fault_table_html(rows, M_filter="1"):
    fr = [r for r in rows if r.get("M") == M_filter and r.get("fault_class") != "healthy"]
    out = ["<table><tr><th>故障类</th><th>区域</th><th>规模</th><th>描述</th>"
           "<th>makespan</th><th>劣化%</th><th>可行</th></tr>"]
    for r in sorted(fr, key=lambda x: (x["fault_class"], x["region"], x["detail"])):
        ms = r.get("makespan") or "N/A"
        sp = r.get("slowdown_pct") or "-"
        out.append(
            f"<tr><td>{esc(r['fault_class'])}</td><td>{esc(r['region'])}</td>"
            f"<td>{esc(r['detail'])}</td><td>{esc(r['fault_desc'][:45])}</td>"
            f"<td>{ms}</td><td>{sp}</td><td>{esc(r.get('feasible',''))}</td></tr>")
    out.append("</table>")
    return "\n".join(out)


def rb_vs_rsag_section():
    return """
<h2>5. Reduce+Broadcast vs Reduce-Scatter+AllGather 选型</h2>
<p>两类 allreduce 形态在通信量上等价，但瓶颈不同：<b>RB 是延迟型</b>（树深 + 根 ramp），
<b>RS+AG 是带宽型</b>（每链路搬运约 2M/N 数据，需足够大的 M 才能摊薄固定步数）。
下表结合本报告 16&times;16 实测与通用 mesh 规律给出选型条件。</p>
<table>
<tr><th>条件</th><th>有利于 Reduce+Broadcast (RB)</th><th>有利于 RS+AG</th></tr>
<tr><td>数据大小 M</td>
 <td>M 小（M &lesssim; N），延迟主导；本 mesh M=1..6 时 RB makespan 197&rarr;212，几乎平坦</td>
 <td>M 大（M &gg; N），带宽主导；需 M/N &gg; 1 才能发挥每链路只搬 M/N 的优势</td></tr>
<tr><td>2D mesh 规模 N</td>
 <td>大 mesh：RB 延迟随直径 O(&radic;N)（16&times;16 直径 150 cy）</td>
 <td>小 mesh / 短环；大 mesh 上应使用<b>按维分解</b> RS+AG（O(X+Y) 步），单 Hamilton 环 O(N) 步在大 mesh 上竞争力弱</td></tr>
<tr><td>Router 在网 reduce</td>
 <td><b>必须支持</b>：RB reduce 依赖沿途 combine（R_LAT）；否则部分和须弹出 PE 再注入，根 ramp 接近 N&middot;M</td>
 <td><b>不需要</b>：归约在每步接收端 PE 完成，router 纯转发即可</td></tr>
<tr><td>无成本多播 (fork)</td>
 <td><b>必须支持</b>：broadcast 依赖 router 一进多出复制；否则根须对 N&minus;1 目的地单播，退化为 O(N&middot;M)</td>
 <td><b>不需要</b>：AG 为逐跳单播转发（eject + forward），比多播要求弱</td></tr>
</table>

<h3>5.1 数据大小 M</h3>
<ul>
 <li><b>RB</b>：makespan &asymp; 2&times;(树深 + 每跳 R_LAT) + 2(M&minus;1)；瓶颈在根 ramp（M 进 + M 出），对 M 斜率约 2。
     本 mesh 实测 197 (M=1) &rarr; 212 (M=6)。</li>
 <li><b>RS+AG</b>：经典带宽最优需把数据切成 N 份；M &lt; N 时切不动，环上每步至少搬 1 flit，
     makespan 退化为 &asymp; 2(N&minus;1) &times; 步延迟。本 mesh 双向环 M=1 为 559 cy（RB 的 2.8&times;）。</li>
 <li><b>交叉点</b>：大致在 M &asymp; N 量级；M 每节点仅数个 flit 时 RB 占优；M/N &gg; 1 且链路带宽成瓶颈时 RS+AG（尤其按维分解形态）更优。</li>
</ul>

<h3>5.2 拓扑规模</h3>
<ul>
 <li>RB 延迟项随 mesh <b>直径</b>增长 O(&radic;N)。</li>
 <li>单环 RS+AG 步数随 Hamilton 环长 O(N)；mesh 越大相对 RB 越劣（本报告环方案 M=1 已差 2.8&times;）。</li>
 <li>大 mesh 上 RS+AG 的合理形态是<b>维序分解</b>（先行内 RS+AG 再列内），步数 O(X+Y)，大 M 下才与 RB 竞争。</li>
</ul>

<h3>5.3 硬件能力：在网 reduce 与多播</h3>
<ul>
 <li><b>无在网 reduce</b>：RB reduce 阶段失效 &rarr; 选 RS+AG（或端点归约 + 自定义 schedule）。</li>
 <li><b>无在网 fork</b>：RB broadcast 退化为根 O(N) 次单播 &rarr; 选 RS+AG。</li>
 <li><b>两者均支持</b>（本报告模型：router combine R_LAT=2 + dimensional multicast）：RB 在 M 小、mesh 大时为最优；本 16&times;16 场景效率 1.005。</li>
</ul>

<p><b>一句话</b>：RB 适合<b>小数据 + 大 mesh + 在网 reduce/多播</b>；RS+AG 适合<b>大数据（M &gg; N）或哑 router（无 combine/fork）</b>，
在大 mesh 上应采用按维分解而非单环。</p>
<p class="legend">本报告实测（M=1）：tree_reduce_bcast = 197 cy，ring_bi_rs_ag = 559 cy，ring_uni_rs_ag = 1083 cy；
与上表一致，当前参数下 RB 全面优于单环 RS+AG。</p>
"""


def render(rows):
    healthy_m1 = next(r for r in rows if r["fault_class"] == "healthy" and r["M"] == "1")
    g_mk = healthy_m1["makespan"]
    g_lb = healthy_m1["theo_bound"]
    g_eff = healthy_m1["efficiency"]

    parts = ["""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>16x16 AllReduce 零缓冲仿真报告</title>
<style>
 body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
   margin:0 auto;max-width:1100px;padding:24px;color:#0f172a;line-height:1.55}
 h1{border-bottom:3px solid #2563eb;padding-bottom:8px}
 h2{margin-top:34px;border-bottom:1px solid #cbd5e1;padding-bottom:4px}
 code{background:#f1f5f9;padding:1px 5px;border-radius:4px}
 table{border-collapse:collapse;margin:12px 0;font-size:14px}
 th,td{border:1px solid #cbd5e1;padding:6px 10px;text-align:left}
 th{background:#f1f5f9}
 .kpi{display:inline-block;background:#eff6ff;border:1px solid #bfdbfe;
   border-radius:8px;padding:8px 14px;margin:6px 8px 6px 0}
 .legend{font-size:13px;color:#475569}
 .card{border:1px solid #cbd5e1;border-radius:8px;padding:8px;margin:8px 0}
</style></head><body>"""]

    parts.append("<h1>16x16 AllReduce：理论下界、最优零缓冲方案与故障分析</h1>")

    parts.append("<h2>1. 模型假设</h2>")
    parts.append(f"""<p>拓扑：16&times;16 mesh（N=256），H={H} cycle（横向），V={V} cycle（纵向），
PE&harr;router ramp={RAMP} cycle，有向链路 1 flit/cycle，router 0 buffer。
归约时延 R_LAT={R_LAT} cycle（参数化，默认 2）。消息规模 M sweep 1&ndash;6。
Reticle 故障定义为 8&times;8 象限整体失效（64 节点）。</p>
<div>
 <span class="kpi">healthy makespan (M=1) = <b>{g_mk}</b> cy</span>
 <span class="kpi">通用理论下界 = <b>{g_lb}</b> cy</span>
 <span class="kpi">效率 = <b>{g_eff}</b></span>
 <span class="kpi">最优方案 = <b>{esc(healthy_m1['scheme'])}</b></span>
</div>""")

    parts.append("<h2>2. 理论下界</h2>")
    parts.append("""<p>取各类必要约束的最大值作为通用下界（不含 RS+AG 专用 ramp 约束）：</p>
<ul>
 <li><b>树形延迟</b>：顺序 reduce + broadcast，路径延迟 + 中间归约 R_LAT。</li>
 <li><b>下行 ramp</b>：每节点最终吸收 M 个 flit。</li>
 <li><b>二分带宽</b>：至少一半数据跨越最小割。</li>
 <li><b>直径对</b>：最远节点信息必须相遇。</li>
 <li><b>RS+AG 专用</b>：环上 reduce-scatter + allgather 的 ramp 占用（仅适用于环方案）。</li>
</ul>""")
    parts.append(bound_table_html())

    parts.append("<h2>3. 候选方案对比（零缓冲 packer 验证）</h2>")
    parts.append("""<p>三类候选：① 环 RS+AG（Hamilton snake）；② 树形 reduce+broadcast（中心根）；
③ 维序多树 / 混合带（横带局部 RS + 全局树）。</p>""")
    parts.append(scheme_table_html())

    parts.append("<h2>4. 最优方案说明</h2>")
    parts.append(f"""<p>健康 mesh 下，<b>{esc(healthy_m1['scheme'])}</b> 在 M=1..6 均达到最小 makespan。
算法：各节点沿 X&ndash;Y 维序最短路向中心根 ({sa.DEFAULT_ROOT}) reduce（每跳合并 R_LAT=2），
根 down-ramp 弹出后再 dimensional multicast 广播到所有节点。两阶段顺序执行，0-buffer
rigid footprint + 离线 offset packer 保证链路/ramp 无冲突。</p>
<div class="card"><h4>中心根与 reduce 树示意（蓝点=根）</h4>
{svg_mesh(highlight_nodes=[sa.DEFAULT_ROOT])}</div>""")

    parts.append(rb_vs_rsag_section())

    parts.append("<h2>6. 故障处理算法</h2>")
    parts.append("""<h3>6.1 链路故障</h3>
<p>从存活图 BFS 重建 latency tree；各源沿最短路向新根 reduce，再 broadcast。
链路切断仅改变路径，不改变 0-buffer packer 框架。</p>
<h3>6.2 节点故障</h3>
<p>死节点退出集合；重选几何中心为根。1&times;1 / 3&times;3 奇数洞破坏 Hamilton 环，
树方案仍可行；环 RS+AG 可退化为开放路径双向 AG（需闭合环的 uni 不可行）。</p>
<h3>6.3 Reticle（象限）故障</h3>
<p>整 8&times;8 象限失效（192 存活节点）；在剩余子 mesh 上运行同一 tree reduce+bcast，
根重定位到存活区中心。</p>""")

    parts.append("<h2>7. 故障扫描结果 (M=1)</h2>")
    parts.append(fault_table_html(rows, "1"))

    parts.append("<h2>8. 故障扫描结果 (M=6 对照)</h2>")
    parts.append(fault_table_html(rows, "6"))

    # sample fault SVGs
    parts.append("<h2>9. 故障拓扑示意</h2><div class='grid'>")
    samples = [
        ("link_corner_1", "链路故障 corner"),
        ("node_corner_1x1", "节点 1x1 corner"),
        ("quad_Q0", "象限 Q0 全失效"),
    ]
    sc_map = {sc["name"]: sc for sc in hr.all_scenarios(MX, MY)}
    for key, title in samples:
        sc = sc_map.get(key)
        if not sc:
            continue
        parts.append(f'<div class="card"><h4>{esc(title)}</h4>'
                     f'{svg_mesh(sc["dead_nodes"], sc["dead_links"])}</div>')
    parts.append("</div></body></html>")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--html", type=Path, default=DEFAULT_HTML)
    args = ap.parse_args()
    rows = load_rows(args.csv)
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(render(rows), encoding="utf-8")
    print(f"Wrote {args.html}")


if __name__ == "__main__":
    main()
