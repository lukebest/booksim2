#!/usr/bin/env python3
"""HTML summary: optimal fork schemes per message size m=2..5."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "buffer_pareto_msg_size.json"
HTML_PATH = ROOT / "results" / "report_fork_msg_size.html"

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; }
body { font-family: system-ui, sans-serif; margin:0; padding:24px 32px 48px;
       background:var(--bg); color:var(--text); line-height:1.55; max-width:1100px; }
h1 { font-size:1.6rem; margin:0 0 8px; }
h2 { font-size:1.15rem; margin:20px 0 10px; color:#1e3a8a; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:10px;
        padding:18px 22px; margin:16px 0; }
.meta { color:var(--muted); font-size:.9rem; }
table { border-collapse:collapse; width:100%; font-size:.88rem; margin:12px 0; }
th, td { border:1px solid #e2e8f0; padding:7px 10px; text-align:center; }
th { background:#f1f5f9; }
td:first-child { text-align:left; }
tr.best td { background:#ecfdf5; font-weight:600; }
.note { color:var(--muted); font-size:.88rem; }
"""


def esc(s):
    return html.escape(str(s))


def best_row(rows):
    feas = [r for r in rows if r.get("makespan")]
    return min(feas, key=lambda r: r["makespan"]) if feas else None


def summary_table(data):
    rows = []
    for m in data["msg_sizes"]:
        block = data["by_msg_size"][str(m)]
        for rb in data["ramp_bws"]:
            key = str(rb)
            elb = block["burst_pareto"][key]["eject_lb"]
            bs = best_row(block["strict_afifo5"][key])
            bp = block["burst_pareto"][key]["by_ramp_burst"].get("6")
            rows.append((m, rb, elb, bs, bp))

    body = []
    for m, rb, elb, bs, bp in rows:
        if not bs:
            continue
        cls = ""
        body.append(
            f"<tr{cls}><td>{m}</td><td>{rb}</td><td>{elb}</td>"
            f"<td><b>{esc(bs['name'])}</b> ({bs['dir']})</td><td>{bs['makespan']}</td>"
            f"<td>{bs.get('afifo', 0)}</td>"
            f"<td>{esc(bp['scheme']) if bp else '—'} ({bp['dir'] if bp else '—'})</td>"
            f"<td>{bp['makespan'] if bp else '—'}</td></tr>"
        )
    hdr = ("<table><thead><tr><th>m (flit)</th><th>下 ramp</th><th>eject 下界</th>"
           "<th>§1 最优方案</th><th>§1 mk</th><th>AFIFO</th>"
           "<th>§2 R=6 最优</th><th>§2 mk</th></tr></thead><tbody>")
    return hdr + "".join(body) + "</tbody></table>"


def detail_section(m, block, ramp_bws):
    parts = [f"<h2>m = {m} flit</h2>"]
    for rb in ramp_bws:
        key = str(rb)
        elb = block["burst_pareto"][key]["eject_lb"]
        strict = sorted(block["strict_afifo5"][key],
                        key=lambda r: r.get("makespan") or 1 << 30)
        best_mk = min((r["makespan"] for r in strict if r.get("makespan")), default=None)
        parts.append(f"<h3>下 ramp = {rb}（eject 下界 {elb} cy）</h3>")
        parts.append("<table><thead><tr><th>方案</th><th>方向</th><th>§1 mk</th>"
                     "<th>AFIFO</th><th>§2 pipe</th><th>link</th><th>ramp</th></tr></thead><tbody>")
        pipe_map = {(s["name"], s["dir"]): s for s in block["schemes"][key]}
        for r in strict[:12]:
            mk = r.get("makespan")
            cls = " class='best'" if mk == best_mk else ""
            ps = pipe_map.get((r["name"], r["dir"]), {})
            parts.append(
                f"<tr{cls}><td>{esc(r['name'])}</td><td>{r['dir']}</td>"
                f"<td>{mk if mk else '—'}</td><td>{r.get('afifo', '—')}</td>"
                f"<td>{ps.get('pipe', '—')}</td><td>{ps.get('link_buf', '—')}</td>"
                f"<td>{ps.get('ramp_buf', '—')}</td></tr>"
            )
        parts.append("</tbody></table>")
    return "\n".join(parts)


def build(data):
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>",
        "<title>Allgather 最优方案 · m=2~5 flit</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>16×16 Allgather：m = 2~5 flit 最优方案</h1>",
        f"<p class='meta'>wormhole 报文，flit = {data.get('flit_bytes', 64)} B。"
        f" §1 = router 零缓冲 + 边界 AFIFO ≤ 5；"
        f" §2 = link_buf ≤ 6 + 下 ramp 突发 0~6 flit。"
        f" 更新 {esc(data.get('updated', ''))}。</p>",
        "<div class='card'><h2>最优方案汇总</h2>",
        summary_table(data),
        "<p class='note'>§2 列取 R=6 时 burst 约束下的最优 pipelined makespan。"
        " 复现：<code>python3 utils/sweep_fork_msg_size.py</code></p></div>",
    ]
    for m in data["msg_sizes"]:
        parts.append("<div class='card'>" + detail_section(m, data["by_msg_size"][str(m)],
                                                             data["ramp_bws"]) + "</div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def main():
    if not JSON_PATH.exists():
        raise SystemExit(f"Missing {JSON_PATH}; run utils/sweep_fork_msg_size.py first")
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    HTML_PATH.write_text(build(data), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
