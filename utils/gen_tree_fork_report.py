#!/usr/bin/env python3
"""HTML report: tree fork position vs makespan."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "tree_fork_research.json"
HTML_PATH = ROOT / "results" / "report_tree_fork.html"

FORK_ORDER = [
    "ring_uni", "ring_bi_2fork", "dim_xy", "dim_yx",
    "row_spine", "col_spine", "border_3level", "quad_4ring",
    "border_rigid", "border_short_arc",
]


def load():
    if not JSON_PATH.exists():
        import tree_fork_research as t
        return t.run()
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def bar_chart(title, labels, values, lb=None, width=None):
    width = width or max(620, 52 * len(labels))
    height = 320
    margin = 54
    plot_h = height - 2 * margin
    ymax = max(v for v in values if v) * 1.12
    bw = (width - 2 * margin) / max(len(labels), 1)
    best = min(v for v in values if v)
    p = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
         f'<text x="{margin}" y="22" font-size="14" font-weight="bold">{html.escape(title)}</text>',
         f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#64748b"/>',
         f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#64748b"/>']
    if lb:
        ly = height - margin - (lb / ymax) * plot_h
        p.append(f'<line x1="{margin}" y1="{ly:.1f}" x2="{width-margin}" y2="{ly:.1f}" stroke="#dc2626" stroke-dasharray="5 4"/>')
        p.append(f'<text x="{width-margin-4}" y="{ly-4}" font-size="10" fill="#dc2626" text-anchor="end">eject LB={lb}</text>')
    for i, (lab, val) in enumerate(zip(labels, values)):
        if not val:
            continue
        bh = (val / ymax) * plot_h
        x = margin + i * bw + bw * 0.1
        y = height - margin - bh
        col = "#10b981" if val == best else ("#f59e0b" if "border_short" in lab else "#60a5fa")
        p.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.8:.1f}" height="{bh:.1f}" fill="{col}"/>')
        p.append(f'<text x="{x+bw*0.4:.1f}" y="{y-4}" font-size="9" font-weight="bold" text-anchor="middle">{val}</text>')
        for j, ln in enumerate(lab.replace("_", " ").split()):
            p.append(f'<text x="{x+bw*0.4:.1f}" y="{height-margin+14+11*j}" font-size="8" text-anchor="middle">{html.escape(ln)}</text>')
    p.append("</svg>")
    return "\n".join(p)


def table_for_size(data, sz_key, tag):
    block = data["sizes"].get(sz_key, {})
    rows = []
    for k, v in sorted(block.get("strategies", {}).items()):
        if not k.endswith(f"_{tag}") or v.get("error"):
            continue
        fork = v.get("fork", {})
        rows.append((v.get("makespan") or 99999, k, v, fork))
    rows.sort()
    trs = []
    for _, k, v, fork in rows:
        mk = v.get("makespan", "—")
        ok = "✓" if v.get("ok") else ("~" if v.get("scheduler") else "✗")
        levels = fork.get("levels", "?")
        desc = fork.get("desc", "")
        extra = ""
        if v.get("afifo_balanced") is not None:
            extra = f" AFIFO={v.get('afifo_depth')}/{v.get('afifo_balanced')}"
        trs.append(f"<tr><td class='l'>{html.escape(k)}</td><td>{levels}</td>"
                   f"<td class='l'>{html.escape(desc)}</td><td><b>{mk}</b></td>"
                   f"<td>{ok}{extra}</td></tr>")
    return ("<table><tr><th>策略</th><th>分叉级</th><th>拓扑说明</th><th>makespan</th><th>ok</th></tr>"
            + "".join(trs) + "</table>")


def main():
    data = load()
    sections = []
    for sz in ("4x4", "8x8", "16x16"):
        if sz not in data.get("sizes", {}):
            continue
        block = data["sizes"][sz]
        cmp_ = block.get("border_compare_bi", {})
        if cmp_.get("border_3level"):
            sections.append(
                f"<div class='card'><h3>{sz} border 对比（双向）</h3><ul>"
                f"<li>border_3level 显式树（0-buffer）：<b>{cmp_['border_3level']}</b> cy</li>"
                f"<li>border 短弧 AFIFO≤5：<b>{cmp_['border_short_arc']}</b> cy</li>"
                f"<li>border 刚性 fp_border：<b>{cmp_['border_rigid']}</b> cy</li>"
                f"</ul><p class='note'>{html.escape(cmp_.get('note',''))}</p></div>")
    for sz in ("4x4", "8x8", "16x16"):
        if sz not in data.get("sizes", {}):
            continue
        block = data["sizes"][sz]
        lb = 128 if sz == "16x16" else (32 if sz == "8x8" else 8)
        labels, vals = [], []
        for prefix in FORK_ORDER:
            k = f"{prefix}_bi"
            v = block.get("strategies", {}).get(k, {})
            if v.get("makespan"):
                labels.append(k)
                vals.append(v["makespan"])
        for k, v in sorted(block.get("strategies", {}).items()):
            if k.endswith("_bi") and k not in labels and v.get("makespan") and v.get("ok"):
                labels.append(k)
                vals.append(v["makespan"])
        sections.append(f"<div class='card'><h2>{sz} 双向 @ ramp=2</h2>"
                        f"{bar_chart(f'{sz} 树形分叉 makespan（刚性 0-buffer + border AFIFO≤5）', labels, vals, lb=lb)}"
                        f"<h3>单向 @ ramp=1</h3>{table_for_size(data, sz, 'uni')}"
                        f"<h3>双向 @ ramp=2</h3>{table_for_size(data, sz, 'bi')}</div>")

    body = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>树形 Allgather 多播分叉 vs Makespan</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;max-width:1100px;line-height:1.5;}}
h1,h2,h3{{color:#1e3a8a;}} .card{{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}}
table{{border-collapse:collapse;width:100%;font-size:12px;}} td,th{{border:1px solid #cbd5e1;padding:5px 8px;}}
th{{background:#e2e8f0;}} td.l{{text-align:left;}} .note{{color:#64748b;font-size:12px;}}
</style></head><body>
<h1>树形 Allgather：多播分叉位置与 Makespan</h1>
<p class='note'>更新 {html.escape(data.get('updated',''))} · router 无缓冲无阻塞无冲突（刚性 pack）；
border 短弧另允许边界 AFIFO≤5 + 环内链路时分。忽略下 ramp 多播带宽差异以外的 eject 容量。</p>
<div class='card'><h2>统一视角</h2>
<ul>
<li><b>双向 Hamilton 环</b> = 源点 2 路分叉、环上无再分叉的浅树；靠全局链路时分叠加。</li>
<li><b>维序多树 dim_xy</b> = 第一级 X 脊多播 + 第二级各列 Y 分叉；Y-first 为 dim_yx。</li>
<li><b>border 短弧</b> = L1 本象限 Hamilton 环（无分叉）+ L2 邻象限边界短弧 + L3 对角短弧；AFIFO≤5。</li>
<li><b>border_3level 显式树</b> = 同三级逻辑但用多播树边表达（刚性 0-buffer，无 AFIFO）。</li>
<li><b>dim_xy_late_y</b> = 延迟 Y 分叉到源附近行带，探索分叉深度对 makespan 的影响。</li>
</ul></div>
{''.join(sections)}
</body></html>"""
    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
