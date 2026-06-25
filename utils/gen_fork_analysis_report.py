#!/usr/bin/env python3
"""HTML report: fork-position allgather under two buffer regimes (16×16 only).

Reads results/buffer_pareto_16x16.json from sweep_buffer_pareto.py.
Down-ramp bandwidth: 1 or 2 flit/cycle/node only.

Sections:
  1. Strict router_buf=0 + border AFIFO depth <= 5
  2. Pipelined calendar with link_buf <= 6 and down-ramp burst 0..6 flit
"""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARETO_JSON = ROOT / "results" / "buffer_pareto_16x16.json"
HTML_PATH = ROOT / "results" / "report_fork_analysis.html"

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --accent:#2563eb; }
body { font-family: system-ui, Segoe UI, sans-serif; margin:0; padding:24px 32px 48px;
       background:var(--bg); color:var(--text); line-height:1.55; max-width:1180px; }
h1 { font-size:1.65rem; margin:0 0 8px; }
h2 { font-size:1.2rem; margin:0 0 12px; color:#1e3a8a; border-bottom:2px solid #e2e8f0; padding-bottom:6px; }
h3 { font-size:1rem; margin:16px 0 8px; color:#334155; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:10px; padding:18px 22px; margin:18px 0;
        box-shadow:0 1px 3px rgba(15,23,42,.06); }
.meta { color:var(--muted); font-size:.9rem; margin-bottom:20px; }
table { border-collapse:collapse; width:100%; font-size:.86rem; margin:10px 0; }
th, td { border:1px solid #e2e8f0; padding:6px 10px; text-align:right; }
th { background:#f1f5f9; text-align:center; font-weight:600; }
td:first-child, th:first-child { text-align:left; }
tr.best td { background:#ecfdf5; font-weight:600; }
code { font-family: ui-monospace, Menlo, monospace; font-size:.85em; background:#f1f5f9; padding:2px 4px; border-radius:4px; }
.note { color:var(--muted); font-size:.88rem; }
.chart-row { display:flex; flex-wrap:wrap; gap:24px; align-items:flex-start; }
"""


def esc(s):
    return html.escape(str(s))


def burst_svg(title, by_burst, eject_lb, width=560, height=320):
    pts = [(int(R), v["makespan"]) for R, v in sorted(by_burst.items(), key=lambda x: int(x[0]))
           if v is not None]
    if not pts:
        return ""
    ml, mr, mt, mb = 58, 24, 36, 52
    pw, ph = width - ml - mr, height - mt - mb
    ymax = max(m for _, m in pts) * 1.08
    ymin = min(min(m for _, m in pts) * 0.85, eject_lb * 0.9)
    ymax = max(ymax, eject_lb * 1.12)
    rmax = max(r for r, _ in pts) or 1

    def xk(r):
        return ml + (r / rmax) * pw

    def ym(v):
        return mt + ph - ((v - ymin) / (ymax - ymin)) * ph

    lines = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        f'<text x="{ml}" y="22" font-size="13" font-weight="bold">{esc(title)}</text>',
        f'<line x1="{ml}" y1="{mt+ph:.1f}" x2="{ml+pw:.1f}" y2="{mt+ph:.1f}" stroke="#64748b"/>',
        f'<line x1="{ml}" y1="{mt:.1f}" x2="{ml}" y2="{mt+ph:.1f}" stroke="#64748b"/>',
    ]
    ly = ym(eject_lb)
    lines += [
        f'<line x1="{ml}" y1="{ly:.1f}" x2="{ml+pw:.1f}" y2="{ly:.1f}" stroke="#dc2626" stroke-dasharray="6 4"/>',
        f'<text x="{ml+pw-4}" y="{ly-5:.1f}" font-size="10" fill="#dc2626" text-anchor="end">eject LB={eject_lb}</text>',
    ]
    path = []
    for i, (r, mk) in enumerate(pts):
        x, y = xk(r), ym(mk)
        if i == 0:
            path.append(f"M {x:.1f},{y:.1f}")
        else:
            pr, pmk = pts[i - 1]
            path.append(f"L {x:.1f},{ym(pmk):.1f} L {x:.1f},{y:.1f}")
    lines.append(f'<path d="{" ".join(path)}" fill="none" stroke="#2563eb" stroke-width="2.5"/>')
    for r, mk in pts:
        lines.append(f'<circle cx="{xk(r):.1f}" cy="{ym(mk):.1f}" r="4" fill="#2563eb"/>')
    for r in range(0, rmax + 1):
        lines.append(f'<text x="{xk(r):.1f}" y="{mt+ph+16:.1f}" font-size="9" text-anchor="middle" fill="#64748b">{r}</text>')
    lines += [
        f'<text x="{ml+pw/2:.1f}" y="{height-8:.1f}" font-size="11" text-anchor="middle" fill="#475569">'
        f'下 ramp 突发缓冲 R (flit)</text>',
        f'<text x="14" y="{mt+ph/2:.1f}" font-size="11" fill="#475569" transform="rotate(-90 14 {mt+ph/2:.1f})">'
        f'makespan (cy)</text>',
        "</svg>",
    ]
    return "\n".join(lines)


def strict_table(rows, eject_lb):
    body = []
    best_mk = min((r["makespan"] for r in rows if r.get("makespan")), default=None)
    for r in sorted(rows, key=lambda x: (x.get("makespan") or 1 << 30)):
        mk = r.get("makespan")
        cls = " class='best'" if mk == best_mk else ""
        af = r.get("afifo", "—")
        body.append(
            f"<tr{cls}><td>{esc(r['name'])}</td><td>{esc(r['dir'])}</td>"
            f"<td>{mk if mk else '—'}</td><td>{af if af is not None else '—'}</td>"
            f"<td>{esc(r.get('mode', ''))}</td><td>{esc(r.get('method', ''))}</td></tr>"
        )
    hdr = ("<table><thead><tr><th>方案</th><th>方向</th><th>makespan</th>"
           "<th>AFIFO 均衡峰值</th><th>调度模型</th><th>方法</th></tr></thead><tbody>")
    note = (f"<p class='note'>eject 下界 = {eject_lb} cy。"
            " border 方案用 <code>sched_ring_zerobuf</code>（AFIFO≤5）；"
            " 其余方案用刚性 0-buffer pack（无边界 AFIFO 等待）。</p>")
    return note + hdr + "".join(body) + "</tbody></table>"


def burst_table(by_burst, eject_lb):
    rows = []
    best_mk = min((v["makespan"] for v in by_burst.values() if v), default=None)
    for R in sorted(by_burst, key=int):
        p = by_burst[R]
        if not p:
            rows.append(f"<tr><td>R ≤ {R}</td><td colspan='6'>—（无可行方案）</td></tr>")
            continue
        cls = " class='best'" if p["makespan"] == best_mk else ""
        rows.append(
            f"<tr{cls}><td>R ≤ {R}</td><td><b>{p['makespan']}</b></td>"
            f"<td>{esc(p['scheme'])}</td><td>{esc(p['dir'])}</td>"
            f"<td>{p['link_buf']}</td><td>{p['ramp_buf']}</td><td>{p['dom']}</td></tr>"
        )
    hdr = ("<table><thead><tr><th>下 ramp 突发缓冲</th><th>makespan</th><th>最优方案</th><th>方向</th>"
           "<th>link_buf</th><th>ramp_buf</th><th>主导项</th></tr></thead><tbody>")
    note = (f"<p class='note'>约束：每 router 输出 port link_buf ≤ 6 flit，"
            f"下 ramp 突发缓冲 ramp_buf ≤ R；流水线 TDM 日历（<code>measure_buffers</code>）。"
            f" eject 下界 = {eject_lb} cy。</p>")
    return note + hdr + "".join(rows) + "</tbody></table>"


def pipelined_detail(schemes, top_n=15):
    rows = []
    for i, s in enumerate(schemes[:top_n]):
        cls = " class='best'" if i == 0 else ""
        rows.append(
            f"<tr{cls}><td>{esc(s['name'])}</td><td>{esc(s['dir'])}</td>"
            f"<td>{s['pipe']}</td><td>{s['link_buf']}</td><td>{s['ramp_buf']}</td>"
            f"<td>{s['fill']}</td><td>{s['dom']}</td></tr>"
        )
    hdr = ("<table><thead><tr><th>方案</th><th>方向</th><th>pipe_mk</th>"
           "<th>link_buf</th><th>ramp_buf</th><th>fill</th><th>主导</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


def build_report(data):
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>",
        f"<title>Allgather 分叉方案 · 缓冲约束对比 — {esc(data['mesh'])}</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>16×16 Allgather：分叉方案 × 缓冲约束</h1>",
        f"<p class='meta'>Mesh {esc(data['mesh'])}，N={data['n']}，H={data['H']} V={data['V']} cycle/link。"
        f" 下 ramp 带宽：<b>1 或 2 flit/cycle/node</b>（单向原生=1，双向原生=2）。"
        f" 更新 {esc(data.get('updated', ''))}。"
        f" 数据 <code>sweep_buffer_pareto.py</code> → <code>buffer_pareto_16x16.json</code>。</p>",

        "<div class='card'><h2>模型概要</h2>",
        "<ul class='note'>",
        "<li><b>§1 严格 0-buffer</b>：router 内无排队；border/短弧方案允许边界 AFIFO ≤ 5 flit（6 cy 链路延迟）；"
        "调度器 <code>sched_ring_zerobuf</code> 或刚性 <code>sched_zerobuf_compare.pack</code>。</li>",
        "<li><b>§2 允许 router 缓冲</b>：冲突无关流水线 TDM 日历；"
        "每输出 port link_buf ≤ 6 flit；下 ramp 突发缓冲 ramp_buf ∈ [0,6] flit"
        "（持续 eject 仍 ≤ ramp_bw flit/cy，缓冲仅吸收到达突发）。</li>",
        "<li>方案族：ring / quad-center / border / multitree / hybrid B=k / grid Qx×Qy（与 Pareto sweep 一致）。</li>",
        "</ul></div>",
    ]

    # Section 1
    parts.append("<div class='card'><h2>1. 严格 0 router 缓冲 + 边界 AFIFO ≤ 5</h2>")
    parts.append("<p>各方案在 router 零缓冲、边界 AFIFO 深度 ≤ 5 flit 下的最优 allgather makespan。</p>")
    for rb in data["ramp_bws"]:
        key = str(rb)
        elb = (data["n"] - 1 + rb - 1) // rb
        rows = data["strict_afifo5"][key]
        feas = [r for r in rows if r.get("makespan")]
        if feas:
            b = min(feas, key=lambda r: r["makespan"])
            parts.append(f"<h3>下 ramp = {rb} flit/cycle/node（eject 下界 {elb} cy）</h3>")
            parts.append(f"<p class='note'>全局最优：<b>{esc(b['name'])}</b> ({b['dir']}) "
                         f"makespan = <b>{b['makespan']}</b> cy，AFIFO = {b.get('afifo', 0)}。</p>")
            parts.append(strict_table(rows, elb))
    parts.append("</div>")

    # Section 2
    parts.append("<div class='card'><h2>2. link_buf ≤ 6 + 下 ramp 突发缓冲 0~6 flit</h2>")
    parts.append("<p>允许 router 每 port 缓冲 ≤ 6 flit；下 ramp 突发能力 R = 0…6 flit。"
                 "对每个 R 在可行方案中取最小 pipelined makespan。</p>")
    parts.append("<div class='chart-row'>")
    for rb in data["ramp_bws"]:
        key = str(rb)
        bp = data["burst_pareto"][key]
        elb = bp["eject_lb"]
        by = bp["by_ramp_burst"]
        parts.append(
            f"<div><h3>下 ramp = {rb} flit/cycle/node</h3>"
            + burst_svg(f"最优 makespan vs 下 ramp 突发 R (link≤6)", by, elb)
            + burst_table(by, elb)
            + "</div>"
        )
    parts.append("</div></div>")

    # Appendix: full pipelined scan
    parts.append("<div class='card'><h2>附录：流水线 makespan 全方案（无缓冲上限）</h2>")
    parts.append("<p class='note'>供对照 §2 约束前的原始 pipe_mk / link_buf / ramp_buf。</p>")
    for rb in data["ramp_bws"]:
        schemes = data["schemes"][str(rb)]
        parts.append(f"<h3>下 ramp = {rb}（Top 15 by pipe_mk）</h3>")
        parts.append(pipelined_detail(schemes, 15))
    parts.append(
        "<p class='note'>复现：<code>python3 utils/sweep_buffer_pareto.py</code> → "
        "<code>python3 utils/gen_fork_analysis_report.py</code></p></div>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    if not PARETO_JSON.exists():
        raise SystemExit(f"Missing {PARETO_JSON}; run utils/sweep_buffer_pareto.py first")
    data = json.loads(PARETO_JSON.read_text(encoding="utf-8"))
    data["ramp_bws"] = [rb for rb in data.get("ramp_bws", [1, 2]) if rb in (1, 2)]
    HTML_PATH.write_text(build_report(data), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
