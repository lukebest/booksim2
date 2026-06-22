#!/usr/bin/env python3
"""Self-contained HTML report: tree fork-position analysis + buffer-budget Pareto.

Reads results/buffer_pareto_16x16.json (from sweep_buffer_pareto.py) and optional
results/border_afifo_search.json.  Writes results/report_fork_analysis.html.
"""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARETO_JSON = ROOT / "results" / "buffer_pareto_16x16.json"
AFIFO_JSON = ROOT / "results" / "border_afifo_search.json"
HTML_PATH = ROOT / "results" / "report_fork_analysis.html"

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --accent:#2563eb; --ok:#059669; --warn:#d97706; }
* { box-sizing:border-box; }
body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin:0; padding:24px 32px 48px;
       background:var(--bg); color:var(--text); line-height:1.55; max-width:1180px; }
h1 { font-size:1.65rem; margin:0 0 8px; }
h2 { font-size:1.2rem; margin:0 0 12px; color:#1e3a8a; border-bottom:2px solid #e2e8f0; padding-bottom:6px; }
h3 { font-size:1rem; margin:16px 0 8px; color:#334155; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:10px; padding:18px 22px; margin:18px 0;
        box-shadow:0 1px 3px rgba(15,23,42,.06); }
.meta { color:var(--muted); font-size:.9rem; margin-bottom:20px; }
table { border-collapse:collapse; width:100%; font-size:.88rem; margin:10px 0; }
th, td { border:1px solid #e2e8f0; padding:6px 10px; text-align:right; }
th { background:#f1f5f9; text-align:center; font-weight:600; }
td:first-child, th:first-child { text-align:left; }
tr.best td { background:#ecfdf5; font-weight:600; }
tr.near td { background:#fffbeb; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.85em; }
.formula { background:#f1f5f9; padding:10px 14px; border-radius:6px; margin:10px 0; overflow-x:auto; }
ul, ol { margin:8px 0 8px 22px; }
.chart-row { display:flex; flex-wrap:wrap; gap:20px; align-items:flex-start; }
.insight { border-left:4px solid var(--accent); padding-left:12px; margin:12px 0; }
.tag { display:inline-block; padding:2px 8px; border-radius:4px; font-size:.75rem; font-weight:600; }
.tag-ok { background:#d1fae5; color:#065f46; }
.tag-warn { background:#fef3c7; color:#92400e; }
"""


def esc(s):
    return html.escape(str(s))


def pareto_svg(title, frontier, eject_lb, k_display_max=65):
    """Step chart: buffer budget K vs optimal makespan."""
    if not frontier:
        return ""
    pts = [p for p in frontier if p["K"] <= k_display_max]
    if not pts:
        pts = frontier[:8]
    width, height = 560, 320
    ml, mr, mt, mb = 58, 24, 36, 52
    pw, ph = width - ml - mr, height - mt - mb
    ymax = max(p["makespan"] for p in frontier) * 1.08
    ymin = min(eject_lb * 0.85, min(p["makespan"] for p in frontier) * 0.9)
    ymax = max(ymax, eject_lb * 1.15)
    kmax = max(p["K"] for p in pts) or 1

    def xk(k):
        return ml + (k / kmax) * pw

    def ym(v):
        return mt + ph - ((v - ymin) / (ymax - ymin)) * ph

    lines = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        f'<text x="{ml}" y="22" font-size="13" font-weight="bold">{esc(title)}</text>',
        f'<line x1="{ml}" y1="{mt+ph:.1f}" x2="{ml+pw:.1f}" y2="{mt+ph:.1f}" stroke="#64748b"/>',
        f'<line x1="{ml}" y1="{mt:.1f}" x2="{ml}" y2="{mt+ph:.1f}" stroke="#64748b"/>',
    ]
    # eject LB
    ly = ym(eject_lb)
    lines.append(f'<line x1="{ml}" y1="{ly:.1f}" x2="{ml+pw:.1f}" y2="{ly:.1f}" '
                 f'stroke="#dc2626" stroke-dasharray="6 4"/>')
    lines.append(f'<text x="{ml+pw-4}" y="{ly-5:.1f}" font-size="10" fill="#dc2626" text-anchor="end">'
                 f'eject LB={eject_lb}</text>')

    # step path
    prev_k, prev_mk = pts[0]["K"], pts[0]["makespan"]
    path = [f"M {xk(prev_k):.1f},{ym(prev_mk):.1f}"]
    for p in pts[1:]:
        path.append(f"L {xk(p['K']):.1f},{ym(prev_mk):.1f}")
        path.append(f"L {xk(p['K']):.1f},{ym(p['makespan']):.1f}")
        prev_mk = p["makespan"]
    path.append(f"L {xk(pts[-1]['K']):.1f},{ym(prev_mk):.1f}")
    lines.append(f'<path d="{" ".join(path)}" fill="none" stroke="#2563eb" stroke-width="2.5"/>')

    for p in pts:
        cx, cy = xk(p["K"]), ym(p["makespan"])
        lines.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="#2563eb"/>')
        if p["K"] <= 30 or p == pts[-1]:
            lab = f"K≤{p['K']}"
            lines.append(f'<text x="{cx:.1f}" y="{cy-8:.1f}" font-size="9" text-anchor="middle" fill="#334155">{lab}</text>')

    for tick in range(0, kmax + 1, max(1, kmax // 8)):
        lines.append(f'<text x="{xk(tick):.1f}" y="{mt+ph+16:.1f}" font-size="9" text-anchor="middle" fill="#64748b">{tick}</text>')
    lines.append(f'<text x="{ml+pw/2:.1f}" y="{height-8:.1f}" font-size="11" text-anchor="middle" fill="#475569">'
                 f'Router buffer budget K (max(link_buf, ramp_buf) ≤ K)</text>')
    lines.append(f'<text x="14" y="{mt+ph/2:.1f}" font-size="11" fill="#475569" transform="rotate(-90 14 {mt+ph/2:.1f})">'
                 f'makespan (cycles)</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def pareto_table(frontier, eject_lb):
    rows = []
    for p in frontier:
        eff = eject_lb / p["makespan"] * 100
        rows.append(
            f"<tr><td>K ≤ {p['K']}</td><td><b>{p['makespan']}</b></td>"
            f"<td>{esc(p['scheme'])}</td><td>{esc(p['dir'])}</td>"
            f"<td>{p['link_buf']}</td><td>{p['ramp_buf']}</td>"
            f"<td>{p['fill']}</td><td>{p['dom']}</td><td>{eff:.1f}%</td></tr>"
        )
    hdr = ("<table><thead><tr><th>K</th><th>makespan</th><th>最优方案</th><th>方向</th>"
           "<th>link_buf</th><th>ramp_buf</th><th>fill</th><th>主导项</th><th>相对 eject LB</th></tr></thead><tbody>")
    return hdr + "\n".join(rows) + "</tbody></table>"


def scheme_table(schemes, top_n=20):
    rows = []
    for i, s in enumerate(schemes[:top_n]):
        cls = ""
        if i == 0:
            cls = " class='best'"
        elif s["buf_peak"] <= 2:
            cls = " class='near'"
        rows.append(
            f"<tr{cls}><td>{esc(s['name'])}</td><td>{esc(s['dir'])}</td>"
            f"<td>{s['pipe']}</td><td>{s['fill']}</td><td>{s['Lmax']}</td>"
            f"<td>{s['eject']}</td><td>{s['link_buf']}</td><td>{s['ramp_buf']}</td>"
            f"<td>{s['buf_peak']}</td><td>{s['dom']}</td></tr>"
        )
    hdr = ("<table><thead><tr><th>方案</th><th>方向</th><th>pipe_mk</th><th>fill</th>"
           "<th>Lmax</th><th>eject</th><th>link_buf</th><th>ramp_buf</th><th>peak</th><th>主导</th></tr></thead><tbody>")
    return hdr + "\n".join(rows) + "</tbody></table>"


def afifo_section():
    if not AFIFO_JSON.exists():
        return "<p><i>未找到 border_afifo_search.json（严格 0-buffer + AFIFO≤5 调度结果）。</i></p>"
    data = json.loads(AFIFO_JSON.read_text(encoding="utf-8"))
    rows = []
    for key in ("16x16", "8x8", "4x4"):
        if key not in data.get("configs", {}):
            continue
        for mode, tag in (("uni", "单向"), ("bi", "双向")):
            c = data["configs"][key][mode]
            sb = c.get("strict_balanced") or {}
            sa = c.get("strict_any") or {}
            rows.append(
                f"<tr><td>{key}</td><td>{tag}</td><td>{c.get('eject_lb','')}</td>"
                f"<td>{sb.get('makespan','—')}</td><td>{sb.get('afifo_balanced','—')}</td>"
                f"<td>{sa.get('makespan','—')}</td><td>{sa.get('afifo_balanced','—')}</td></tr>"
            )
    tbl = ("<table><thead><tr><th>规模</th><th>方向</th><th>eject LB</th>"
           "<th>strict_bal mk</th><th>AFIFO bal</th>"
           "<th>strict_any mk</th><th>AFIFO bal</th></tr></thead><tbody>"
           + "\n".join(rows) + "</tbody></table>")
    return (
        f"<p>在 <b>router 严格 0 缓冲</b>（ring_buf=eject_buf=0）且边界 AFIFO ≤ {data.get('afifo_cap',5)} 的约束下，"
        f"border 方案经形状优化 + 原子/严格调度器得到的 makespan（高于流水线允许缓冲时的 267/437）：</p>" + tbl
    )


def build_report(data):
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>",
        f"<title>树形 Allgather 分叉位置与缓冲预算分析 — {esc(data['mesh'])}</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>树形 Allgather：多播分叉位置 × 缓冲预算 × Ramp 带宽</h1>",
        f"<p class='meta'>Mesh {esc(data['mesh'])}，N={data['n']}，H={data['H']} V={data['V']} cycle/link，"
        f"ramp={data['ramp']} cycle，M=1 flit/源。"
        f" 生成时间 {esc(data.get('updated',''))}。"
        f" 数据来自 <code>sweep_buffer_pareto.py</code>（流水线冲突无关 TDM 日历 + 实测 peak buffer）。</p>",

        "<div class='card'><h2>执行摘要</h2>",
        "<ol>",
        "<li><b>三条下界</b>：makespan ≳ max(<i>fill</i> 树深, <i>L<sub>max</sub></i> 峰值链路载荷, <i>T<sub>eject</sub></i> 下 ramp 带宽地板)。"
        " 分叉越靠根 → fill↓ 但 L<sub>max</sub>↑ 且 eject 口突发 → router 缓冲↑。</li>",
        "<li><b>严格 0 router 缓冲</b>（K=0）：单环最优（ramp_bw=2/4 → 754 bi；ramp_bw=1 → 1474 uni）。"
        " <span class='tag tag-ok'>K≤2</span> 时 ramp_bw=2 → border 267；ramp_bw=1 → hybrid B=2 418（border 需 K≤74 才到 283）。</li>",
        "<li><b>允许大缓冲</b>：grid 8×2 在 ramp_bw=2、K≥56 时 mk=189，逼近 fill；eject 地板 128 仍不可破。</li>",
        "<li><b>最优切分层数</b> Q* ≈ H = 4：再细切（8×2）需 K≈30~56 才值得。</li>",
        "</ol></div>",

        "<div class='card'><h2>1. 形式化模型</h2>",
        "<ul>",
        "<li>每条有向 mesh 链路：≤1 flit/cycle 发射槽，latency H 或 V（流水线，可同时 in-flight L 个 flit）。</li>",
        "<li>每节点下 ramp：≤ ramp_bw flit/cycle 到达/eject；上 ramp 注入源 flit。</li>",
        "<li>Router 内 in-network fork：共享输入链路只计 1 次载荷；下 ramp <b>不做</b>多播复制。</li>",
        "<li><b>Router 缓冲预算 K</b>：max(link_buf, ramp_buf) ≤ K；边界 AFIFO 单独计数（本报告 Pareto 只约束 router 缓冲）。</li>",
        "<li><b>pipe_mk</b>：允许足够缓冲时的冲突无关流水线 makespan（<code>sim_fused_rings.simulate</code>）；"
        " peak buffer 由 <code>measure_buffers</code> 统计。</li>",
        "</ul>",
        "<div class='formula'>",
        "T<sub>eject</sub> = ⌈(N−1) / ramp_bw⌉ &nbsp;&nbsp;|&nbsp;&nbsp;",
        "L<sub>max</sub> = max<sub>有向边 e</sub> |{源 s : s 的树路径经过 e}| &nbsp;&nbsp;|&nbsp;&nbsp;",
        "fill = max<sub>s,v</sub> latency(inject→v) + 2·ramp",
        "</div>",
        "<p>无冲突 makespan 下界：<b>max(fill, L<sub>max</sub>, T<sub>eject</sub>)</b>。实测 pipe_mk 紧贴该 max（见明细表「主导」列）。</p>",
        "</div>",

        "<div class='card'><h2>2. 分叉位置参数化</h2>",
        "<table><thead><tr><th>族</th><th>分叉结构</th><th>典型 fill 主导?</th><th>缓冲特征</th></tr></thead><tbody>",
        "<tr><td>ring (Q=1)</td><td>根处双向/单向分叉，之后无分叉</td><td>是（≈半周长）</td><td>link=0, ramp≈0</td></tr>",
        "<tr><td>border / grid 2×2 (Q=4)</td><td>子环 + 2/3 级边界多播 + 短弧</td><td>中等</td><td>link≤2；ramp 随 rb 变化</td></tr>",
        "<tr><td>hybrid B=k</td><td>水平带环 + 列向树</td><td>B 大时 fill↓</td><td>B≥4 时 ramp 突发</td></tr>",
        "<tr><td>grid Qx×Qy</td><td>通用区域网格边界展开</td><td>Q 大时 fill↓</td><td>Q&gt;2 需 K≫2</td></tr>",
        "<tr><td>multitree (Q=N)</td><td>根处行+列全分叉</td><td>否（eject 主导）</td><td>ramp_buf≈120</td></tr>",
        "</tbody></table>",
        "<div class='insight'><b>Q* ≈ H 定理（工程判据）</b>：子环 fill ≈ H·(N/Q)/2。令 ≈ T<sub>eject</sub> 得 Q* ≈ H。"
        " 对 H=4，四象限 border 是无缓冲前沿；更细 grid 8×2 需 K≥56 才换到 mk=189。</div>",
        "</div>",

        "<div class='card'><h2>3. 按缓冲预算 K 的最优 makespan（Pareto 前沿）</h2>",
        "<p>对每个 ramp_bw ∈ {1,2,4} flit/cycle/node，在 max(link_buf,ramp_buf)≤K 的可行方案中取最小 pipe_mk。"
        " 方向 uni/bi 取最优者。</p>",
        "<div class='chart-row'>",
    ]

    for rb in data["ramp_bws"]:
        key = str(rb)
        pf = data["pareto"][key]
        parts.append(
            f"<div><h3>ramp_bw = {rb}（eject LB = {pf['eject_lb']}）</h3>"
            + pareto_svg(f"ramp_bw={rb}", pf["frontier"], pf["eject_lb"])
            + pareto_table(pf["frontier"], pf["eject_lb"]) + "</div>"
        )
    parts.append("</div></div>")

    parts.append("<div class='card'><h2>4. 关键拐点解读</h2><ul>")
    insights = [
        ("K=0", "仅 peak_buf=0：ramp_bw=1 → 单环 uni 1474；ramp_bw=2/4 → 单环 bi 754。"),
        ("K≤2", "ramp_bw=2 → border Q=4 bi 267；ramp_bw=1 → hybrid B=2 bi 418（border bi 需 ramp_buf=74）。"),
        ("K≤2, rb=4", "与 rb=2 相同拐点：border bi 267（更高 ramp_bw 吸收了 eject 突发，peak_buf 仍≤2）。"),
        ("K≈10~30", "hybrid B=16 / grid 4×2 开始领先 border：mk 251~201。"),
        ("K≥56", "grid 8×2 bi 最优 mk=189（rb=2/4）；仍高于 eject LB 128/64。"),
        ("eject 地板", "ramp_bw=1→255，2→128，4→64；仅当 fill/Lmax 已贴地板时 ramp_bw 才成为主导瓶颈。"),
    ]
    for title, text in insights:
        parts.append(f"<li><b>{title}</b>：{text}</li>")
    parts.append("</ul></div>")

    for rb in data["ramp_bws"]:
        schemes = data["schemes"][str(rb)]
        parts.append(
            f"<div class='card'><h2>5. 全方案扫描（ramp_bw={rb}，按 pipe_mk 排序 Top 20）</h2>"
            + scheme_table(schemes, 20) + "</div>"
        )

    parts.append(
        "<div class='card'><h2>6. 严格 0 router 缓冲 + 边界 AFIFO ≤ 5</h2>"
        + afifo_section()
        + "<p class='meta'>说明：此节与 §3 Pareto（允许 router 缓冲）正交。"
        " border 在 AFIFO≤5 下 16×16 双向 strict_balanced mk≈387，高于流水线 K≤2 的 267。</p></div>"
    )

    parts.append(
        "<div class='card'><h2>7. 结论</h2>"
        "<ol>"
        "<li>在<b>仅允许 router 缓冲 ≤K</b> 时，不存在比 Pareto 前沿更小的 makespan；"
        " 更靠根的分叉（grid 8×2、multitree）确实更快，但<b>必须付出 K 级缓冲</b>。</li>"
        "<li><b>K≤2 的工程最优</b>：ramp_bw=2/4 → border Q=4（267）；"
        " ramp_bw=1 → hybrid B=2（418）或 border uni（437）。</li>"
        "<li><b>K 充足时</b>：grid 8×2 双向在 ramp_bw=2/4 下 mk=189；再往下受 T<sub>eject</sub> 限制"
        "（128/64），需提高 ramp_bw 而非继续加深分叉。</li>"
        "<li>eject 突发缓冲在<b>目的节点下 ramp</b>，无法转移到边界 AFIFO——这是 Q*≈H 的根本原因。</li>"
        "</ol>"
        "<p>复现：<code>python3 utils/sweep_buffer_pareto.py</code> → "
        "<code>python3 utils/gen_fork_analysis_report.py</code></p></div>"
    )

    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    if not PARETO_JSON.exists():
        raise SystemExit(f"Missing {PARETO_JSON}; run utils/sweep_buffer_pareto.py first")
    data = json.loads(PARETO_JSON.read_text(encoding="utf-8"))
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(build_report(data), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
