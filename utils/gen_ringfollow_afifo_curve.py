#!/usr/bin/env python3
"""Plot makespan vs border AFIFO depth cap for the RING-FOLLOW scheme.

Reads results/ringfollow_afifo_depth_sweep.json (produced by
`python3 sweep_afifo_depth.py --scheme ringfollow`) and writes
results/report_ringfollow_afifo_depth_curve.html.

Reuses the SVG line-chart + table helpers from gen_afifo_depth_curve so the
visual style matches the border report; the narrative is derived from the data
(plateau makespan, eject lower bound, first cap reaching the minimum).
"""

import html
import json
from pathlib import Path

from gen_afifo_depth_curve import SERIES, line_chart, table_rows

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "ringfollow_afifo_depth_sweep.json"
HTML_PATH = ROOT / "results" / "report_ringfollow_afifo_depth_curve.html"


def load():
    if not JSON_PATH.exists():
        import sweep_afifo_depth as sw
        return sw.run(scheme="ringfollow")
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def first_min_cap(points):
    mks = [p["makespan"] for p in points if p.get("makespan") is not None]
    if not mks:
        return None, None
    mn = min(mks)
    for p in points:
        if p.get("makespan") == mn:
            return p["cap"], p
    return None, None


def analysis_rows(data):
    rows = []
    for key, label, _ in SERIES:
        cfg = data["configs"].get(key)
        if not cfg:
            continue
        pts = cfg["points"]
        by_cap = {p["cap"]: p for p in pts}
        mk0 = by_cap.get(0, {}).get("makespan", "—")
        cap_min, pmin = first_min_cap(pts)
        mkmin = pmin["makespan"] if pmin else "—"
        det = (pmin or {}).get("detail") or {}
        depth = det.get("afifo_depth", "?")
        method = det.get("method", "?")
        lb = cfg.get("eject_lb", "?")
        ratio = f"{mkmin/lb:.2f}×" if isinstance(mkmin, int) and isinstance(lb, int) and lb else "—"
        rows.append(
            f"<tr><td class='l'>{html.escape(label)}</td>"
            f"<td>{cfg.get('ramp_bw','?')}</td>"
            f"<td>{lb}</td><td>{mk0}</td><td>{mkmin}</td>"
            f"<td>{cap_min}</td><td>{depth}</td><td>{method}</td><td>{ratio}</td></tr>"
        )
    hdr = ("<tr><th>配置</th><th>下 ramp</th><th>eject 下界</th>"
           "<th>mk@cap0</th><th>最小 mk</th><th>首达深度</th>"
           "<th>实测 AFIFO 峰值</th><th>最优方法</th><th>/下界</th></tr>")
    return f"<table>{hdr}{''.join(rows)}</table>"


def main():
    data = load()
    caps = data["caps"]
    bi_only, uni_only = [], []
    for key, label, color in SERIES:
        cfg = data["configs"].get(key)
        if not cfg:
            continue
        pts = cfg["points"]
        (bi_only if key.endswith("_bi") else uni_only).append((label, color, pts))

    body = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Ring-Follow · AFIFO 深度 vs Makespan</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;max-width:960px;}}
h1,h2{{color:#1e3a8a;}} .card{{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}}
table{{border-collapse:collapse;width:100%;font-size:12px;}} td,th{{border:1px solid #cbd5e1;padding:5px 6px;text-align:center;}}
th{{background:#e2e8f0;}} td.l{{text-align:left;}} .note{{color:#64748b;font-size:12px;}}
</style></head><body>
<h1>Ring-Follow（环跟随）：AFIFO 深度 vs AllGather Makespan</h1>
<p class='note'>方案：每个源先跑本象限 Hamilton 环，再沿 X/Y/对角跨界把环「跟随」延伸进其余 3 个象限。<br>
模型：router 零 buffer · 无阻塞 · 无冲突 · H=4, V=6 · 环形状按各尺寸/方向单独优化<br>
更新：{html.escape(data.get('updated', ''))} · 数据 <code>results/ringfollow_afifo_depth_sweep.json</code></p>

<div class='card'><h2>双向环 @ 下 ramp = 2 flit/cycle/node</h2>
{line_chart(caps, bi_only, "ring-follow makespan vs 边界 AFIFO 深度上限（双向, ramp=2）")}
</div>
<div class='card'><h2>单向环 @ 下 ramp = 1 flit/cycle/node</h2>
{line_chart(caps, uni_only, "ring-follow makespan vs 边界 AFIFO 深度上限（单向, ramp=1）")}
</div>

<div class='card'><h2>数值表（每格 = 该 AFIFO 深度上限下搜索到的最小 makespan）</h2>
{table_rows(data)}
<p class='note'>per-link peak ≤ cap 过滤；合并全部 spread 候选与各 cap 下 atomic 结果，保证 cap 增大时 makespan 不升。
cap=0 表示跨界不允许在 AFIFO 中等待。</p>
</div>

<div class='card'><h2>小结</h2>
{analysis_rows(data)}
<p class='note'>下 ramp = 每节点每周期可吞吐的 flit 数（eject 带宽）。eject 下界 = ⌈(N−1)/ramp⌉。
ring-follow 把环延伸进邻象限，因此跨界后下游仍是「环传送带」，AFIFO 通常只需很浅即可达到各自方案的最优 makespan。</p>
</div>
</body></html>"""

    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
