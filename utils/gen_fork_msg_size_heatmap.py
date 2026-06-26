#!/usr/bin/env python3
"""Heatmap: optimal strict makespan / eject lower bound vs message size and ramp.

Reads results/buffer_pareto_msg_size.json (from sweep_fork_msg_size.py).
X-axis: message size m (flit).  Y-axis: down-ramp bandwidth (1 or 2 flit/cy/node).
Cell color: min strict §1 makespan / eject_lb among all scheme families.

Output: results/report_fork_msg_size_heatmap.html
"""

import html
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "buffer_pareto_msg_size.json"
HTML_PATH = ROOT / "results" / "report_fork_msg_size_heatmap.html"

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; }
body { font-family: system-ui, sans-serif; margin:0; padding:24px 32px 48px;
       background:var(--bg); color:var(--text); line-height:1.55; max-width:960px; }
h1 { font-size:1.55rem; margin:0 0 8px; }
h2 { font-size:1.05rem; margin:18px 0 8px; color:#1e3a8a; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:10px;
        padding:18px 22px; margin:16px 0; }
.meta { color:var(--muted); font-size:.9rem; margin-bottom:16px; }
.note { color:var(--muted); font-size:.88rem; }
table.data { border-collapse:collapse; font-size:.82rem; margin-top:16px; }
table.data th, table.data td { border:1px solid #e2e8f0; padding:6px 8px; text-align:center; }
table.data th { background:#f1f5f9; }
.legend { display:flex; align-items:center; gap:8px; margin:12px 0; font-size:.85rem; }
.legend-bar { width:220px; height:14px; border-radius:4px; border:1px solid #cbd5e1; }
"""


def esc(s):
    return html.escape(str(s))


def best_strict(rows):
    feas = [r for r in rows if r.get("makespan")]
    return min(feas, key=lambda r: r["makespan"]) if feas else None


def ratio_color(r, rmin, rmax):
    """Green (low ratio ≈ tight to LB) → amber → red (high overhead)."""
    if rmax <= rmin:
        t = 0.5
    else:
        t = (r - rmin) / (rmax - rmin)
        t = max(0.0, min(1.0, t))
    # HSL: 145° green → 0° red
    hue = 145 * (1 - t)
    return f"hsl({hue:.0f}, 72%, {42 + 18 * (1 - t):.0f}%)"


def build_matrix(data):
    msg_sizes = [int(m) for m in data.get("msg_sizes", [])]
    ramp_bws = list(data.get("ramp_bws", [1, 2]))
    cells = {}
    details = {}
    ratios = []
    for rb in ramp_bws:
        for m in msg_sizes:
            block = data["by_msg_size"].get(str(m))
            if not block:
                continue
            key = str(rb)
            elb = block["burst_pareto"][key]["eject_lb"]
            best = best_strict(block["strict_afifo5"][key])
            if not best:
                continue
            ratio = best["makespan"] / elb
            ratios.append(ratio)
            cells[(rb, m)] = ratio
            details[(rb, m)] = dict(
                makespan=best["makespan"],
                eject_lb=elb,
                ratio=ratio,
                scheme=best["name"],
                dir=best["dir"],
            )
    return msg_sizes, ramp_bws, cells, details, ratios


def heatmap_svg(msg_sizes, ramp_bws, cells, details, ratios):
    rmin = min(ratios) if ratios else 1.0
    rmax = max(ratios) if ratios else 2.0
    pad_l, pad_t, pad_r, pad_b = 72, 36, 24, 52
    cw, ch = 88, 56
    W = pad_l + len(msg_sizes) * cw + pad_r
    H = pad_t + len(ramp_bws) * ch + pad_b
    parts = [
        f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;height:auto;display:block">',
        f'<text x="{pad_l + len(msg_sizes)*cw/2:.0f}" y="22" text-anchor="middle" '
        f'font-size="13" font-weight="600" fill="#334155">报文大小 m (flit)</text>',
    ]
    for j, m in enumerate(msg_sizes):
        x = pad_l + j * cw + cw / 2
        parts.append(
            f'<text x="{x:.0f}" y="{pad_t - 8}" text-anchor="middle" '
            f'font-size="12" fill="#475569">{m}</text>'
        )
    for i, rb in enumerate(ramp_bws):
        y = pad_t + i * ch
        parts.append(
            f'<text x="{pad_l - 10}" y="{y + ch/2 + 4:.0f}" text-anchor="end" '
            f'font-size="12" fill="#475569">{rb} flit/cy</text>'
        )
        for j, m in enumerate(msg_sizes):
            d = details.get((rb, m))
            if not d:
                continue
            x = pad_l + j * cw
            col = ratio_color(d["ratio"], rmin, rmax)
            parts.append(
                f'<rect x="{x+2:.0f}" y="{y+2:.0f}" width="{cw-4:.0f}" height="{ch-4:.0f}" '
                f'rx="6" fill="{col}" stroke="#e2e8f0"/>'
            )
            parts.append(
                f'<text x="{x + cw/2:.0f}" y="{y + ch/2 - 2:.0f}" text-anchor="middle" '
                f'font-size="13" font-weight="700" fill="#0f172a">{d["ratio"]:.2f}×</text>'
            )
            label = f'{d["scheme"]} ({d["dir"]})'
            if len(label) > 22:
                label = label[:20] + "…"
            parts.append(
                f'<text x="{x + cw/2:.0f}" y="{y + ch/2 + 12:.0f}" text-anchor="middle" '
                f'font-size="9" fill="#334155">{esc(label)}</text>'
            )
            parts.append(
                f'<text x="{x + cw/2:.0f}" y="{y + ch - 6:.0f}" text-anchor="middle" '
                f'font-size="9" fill="#64748b">{d["makespan"]}/{d["eject_lb"]} cy</text>'
            )
    parts.append(
        f'<text x="{pad_l + len(msg_sizes)*cw/2:.0f}" y="{H - 8}" text-anchor="middle" '
        f'font-size="11" fill="#64748b">m (flit)</text>'
    )
    parts.append("</svg>")
    legend = (
        f'<div class="legend"><span>{rmin:.2f}×</span>'
        f'<div class="legend-bar" style="background:linear-gradient(90deg,'
        f'hsl(145,72%,60%),hsl(45,72%,50%),hsl(0,72%,45%))"></div>'
        f'<span>{rmax:.2f}×</span><span class="note">（绿≈贴近 eject 下界，红=开销大）</span></div>'
    )
    return "\n".join(parts), legend, rmin, rmax


def detail_table(msg_sizes, ramp_bws, details):
    rows = []
    for rb in ramp_bws:
        for m in msg_sizes:
            d = details.get((rb, m))
            if not d:
                rows.append(f"<tr><td>{rb}</td><td>{m}</td><td colspan='5'>—</td></tr>")
                continue
            rows.append(
                f"<tr><td>{rb}</td><td>{m}</td><td>{d['eject_lb']}</td>"
                f"<td><b>{esc(d['scheme'])}</b> ({d['dir']})</td><td>{d['makespan']}</td>"
                f"<td>{d['ratio']:.3f}</td><td>{d['ratio']:.2f}×</td></tr>"
            )
    hdr = ("<table class='data'><thead><tr><th>下 ramp</th><th>m</th><th>eject 下界</th>"
           "<th>§1 最优方案</th><th>makespan</th><th>比值</th><th>标注</th></tr></thead><tbody>")
    return hdr + "".join(rows) + "</tbody></table>"


def build_report(data):
    msg_sizes, ramp_bws, cells, details, ratios = build_matrix(data)
    if not ratios:
        raise SystemExit("No sweep data; run sweep_fork_msg_size.py first")
    svg, legend, rmin, rmax = heatmap_svg(msg_sizes, ramp_bws, cells, details, ratios)
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>",
        "<title>Allgather 最优 makespan / eject 下界 — 热力图</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>16×16 多方案 Allgather：makespan / eject 下界</h1>",
        f"<p class='meta'>§1 严格 0 router buffer + 边界 AFIFO ≤ 5；"
        f"每格取全方案族最小 makespan ÷ eject 下界 ⌈(N−1)·m / ramp⌉。"
        f" 数据 <code>buffer_pareto_msg_size.json</code>，"
        f"更新 {esc(data.get('updated', ''))}。</p>",
        "<div class='card'>",
        "<h2>热力图（纵轴 = 下 ramp 带宽，横轴 = 报文 m flit）</h2>",
        legend,
        svg,
        "<p class='note'>格内：<b>比值</b> · 最优方案 · makespan/下界 (cy)。"
        f" 比值范围 [{rmin:.2f}, {rmax:.2f}]×。</p>",
        detail_table(msg_sizes, ramp_bws, details),
        "<p class='note'>复现：<code>python3 utils/sweep_fork_msg_size.py</code> → "
        "<code>python3 utils/gen_fork_msg_size_heatmap.py</code></p>",
        "</div></body></html>",
    ]
    return "\n".join(parts)


def main():
    if not JSON_PATH.exists():
        raise SystemExit(f"Missing {JSON_PATH}")
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    expected = {str(m) for m in data.get("msg_sizes", [2, 3, 4, 5])}
    have = set(data.get("by_msg_size", {}).keys())
    missing = sorted(expected - have, key=int)
    if missing:
        raise SystemExit(f"Sweep incomplete; missing m={missing}. Wait for sweep_fork_msg_size.py.")
    HTML_PATH.write_text(build_report(data), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
