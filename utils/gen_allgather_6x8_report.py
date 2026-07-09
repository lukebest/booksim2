#!/usr/bin/env python3
"""Generate HTML report: 6×8 allgather — rigid 0-buffer schedules, H=7/V=9.

Output: results/report_allgather_6x8.html
"""

import html
from pathlib import Path

import allgather_6x8_rigid as R

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "results" / "report_allgather_6x8.html"

H, V = 7, 9
MX, MY = R.MX, R.MY
N = MX * MY
FLITS = [1, 2, 3, 4, 5]
RBS = [1, 2]

SCHEMES = [
    "row_col", "border_bi_Q4", "border_uni_Q4",
    "hybrid_v_bi_B2", "multitree", "ring_bi", "ring_uni",
]
SCHEME_LABEL = {
    "row_col": "row→col",
    "border_bi_Q4": "hybrid_bi_Q4",
    "border_uni_Q4": "hybrid_uni_Q4",
    "hybrid_v_bi_B2": "hybrid_v_bi_B2",
    "multitree": "multitree",
    "ring_bi": "ring_bi",
    "ring_uni": "ring_uni",
}
SCHEME_DESC = {
    "row_col": "先按行 allgather，再按列 allgather（两阶段）",
    "border_bi_Q4": "border (Q=4)：4 象限环（长边贴中心边界）+ 边界短弧双向注入",
    "border_uni_Q4": "border (Q=4)：4 象限环 + 边界短弧单向注入",
    "hybrid_v_bi_B2": "2 个纵向条带内双向环 + 逐行横向 fork",
    "multitree": "每源 X→Y 维序双向多播树",
    "ring_bi": "全局 48 点 Hamilton 双向环",
    "ring_uni": "全局 48 点 Hamilton 单向环",
}
SCHEME_COLOR = {
    "row_col": "#7c3aed",
    "border_bi_Q4": "#b45309",
    "border_uni_Q4": "#d97706",
    "hybrid_v_bi_B2": "#dc2626",
    "multitree": "#2563eb",
    "ring_bi": "#059669",
    "ring_uni": "#94a3b8",
}

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --line:#e2e8f0; }
body { font-family: system-ui, -apple-system, sans-serif; margin:0; padding:28px 32px 64px;
       background:var(--bg); color:var(--text); line-height:1.65; max-width:1040px; }
h1 { font-size:1.6rem; margin:0 0 4px; }
h2 { font-size:1.18rem; margin:0 0 14px; color:#1e3a8a; }
h3 { font-size:1.0rem; margin:2px 0 8px; color:#334155; }
.sub { color:var(--muted); font-size:.92rem; margin:0 0 20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:22px 26px; margin:18px 0; }
.card.hero { border-color:#c7d2fe; background:linear-gradient(180deg,#fbfcff,#fff); }
.lead { font-size:1.05rem; margin:0 0 16px; }
.note { color:var(--muted); font-size:.86rem; margin:8px 0 0; }
code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:.85em; }
table.data { border-collapse:collapse; font-size:.85rem; margin:6px 0; width:100%; }
table.data th, table.data td { border:1px solid var(--line); padding:7px 10px; text-align:center; }
table.data th { background:#f1f5f9; font-weight:600; }
table.data td.name { text-align:left; font-weight:600; }
table.data tr.best td { background:#ecfdf5; }
td.win { background:#dcfce7 !important; font-weight:700; }
.dash { color:#cbd5e1; }
.tag { display:inline-block; font-size:.72rem; padding:1px 7px; border-radius:20px; }
.tag-ok { background:#dcfce7; color:#166534; }
.tag-sram { background:#ede9fe; color:#5b21b6; }
ul.compact { margin:6px 0; padding-left:20px; }
ul.compact li { margin:5px 0; }
.formula { font-family: ui-monospace, monospace; background:#f8fafc; border:1px solid var(--line);
           border-radius:6px; padding:9px 13px; margin:10px 0; font-size:.85rem; }
.rank { display:flex; gap:10px; flex-wrap:wrap; margin:4px 0 0; }
.chip { display:flex; align-items:center; gap:7px; font-size:.9rem; padding:6px 12px;
        border:1px solid var(--line); border-radius:20px; background:#fff; }
.dot { width:11px; height:11px; border-radius:50%; }
.clist { list-style:none; padding:0; margin:0; display:grid; gap:10px; }
.crow { display:grid; grid-template-columns:150px 1fr; gap:14px; align-items:start;
        padding:12px 14px; border:1px solid var(--line); border-radius:9px; background:#fff; }
.crow .h { display:flex; align-items:center; gap:8px; font-weight:700; }
.crow .b { font-size:.9rem; color:#334155; }
.crow .b b { color:#1e3a8a; }
@media (max-width:640px){ .crow{ grid-template-columns:1fr; } }
"""


def esc(s):
    return html.escape(str(s))


def compute():
    """data[rb][m][scheme] -> record; row_col carries T1/T2/turnaround/sram."""
    data = {}
    for rb in RBS:
        data[rb] = {}
        for m in FLITS:
            R.setup(H, V)
            T = R.theory_t(m, rb, H, V)
            rec = {"T": T, "schemes": {}}
            for s in SCHEMES:
                rec["schemes"][s] = R.scheme_makespan(s, rb, m)
            data[rb][m] = rec
    return data


def fmt(v):
    return str(v) if v is not None else '<span class="dash">—</span>'


def master_table(data, rb):
    """One table per ramp_bw: rows = m, cols = schemes, cells = makespan."""
    head = "".join(
        f'<th style="color:{SCHEME_COLOR[s]}">{esc(SCHEME_LABEL[s])}</th>'
        for s in SCHEMES
    )
    body = []
    for m in FLITS:
        d = data[rb][m]
        mks = {s: d["schemes"][s]["makespan"] for s in SCHEMES}
        valid = [v for v in mks.values() if v is not None]
        best = min(valid) if valid else None
        cells = []
        for s in SCHEMES:
            v = mks[s]
            cls = "win" if (v is not None and v == best) else ""
            cells.append(f'<td class="{cls}">{fmt(v)}</td>')
        body.append(
            f"<tr><td>{m}</td><td>{d['T']}</td>" + "".join(cells) + "</tr>"
        )
    return (
        "<table class='data'><thead><tr>"
        "<th>m (flit)</th><th>下界 T</th>" + head +
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def winner_chips(data):
    """Fastest scheme at each (rb=2) m."""
    chips = []
    for m in FLITS:
        d = data[2][m]
        mks = {s: d["schemes"][s]["makespan"] for s in SCHEMES}
        valid = {s: v for s, v in mks.items() if v is not None}
        best_s = min(valid, key=valid.get)
        chips.append(
            f'<div class="chip"><span class="dot" style="background:{SCHEME_COLOR[best_s]}"></span>'
            f'm={m} → <b>{esc(SCHEME_LABEL[best_s])}</b> ({valid[best_s]} cy)</div>'
        )
    return '<div class="rank">' + "".join(chips) + "</div>"


def conclusions(data):
    def mk(s, rb, m):
        return data[rb][m]["schemes"][s]["makespan"]

    rc1 = data[2][1]["schemes"]["row_col"]
    bbi1 = data[2][1]["schemes"]["border_bi_Q4"]
    items = [
        ("border_bi_Q4",
         f"<b>rb=2, m=1 全局最快（{bbi1['makespan']} cy）</b>，略优于 row→col（{mk('row_col',2,1)} cy）。"
         f"即 border (Q=4) 四象限环 + 边界短弧；对每个 m 在 m×m=1 / 分轮 batch 中取最小。"
         f"rb=1 或无 bidirectional 时无优势。"),
        ("row_col",
         f"m≥2 @rb=2 最快（m=2 <b>{mk('row_col',2,2)} cy</b>）。router 全程 0 buffer，"
         f"但需 <b>{rc1['sram']}m flit/节点 SRAM</b>，且两相之间有 <b>{rc1['turnaround']} cy</b> "
         f"下ramp→SRAM→上ramp 往返开销。"),
        ("border_uni_Q4",
         f"border (Q=4) 单向版（m=1@rb=2 <b>{mk('border_uni_Q4',2,1)} cy</b>），"
         f"始终慢于 bidirectional 版与 row→col。"),
        ("hybrid_v_bi_B2",
         f"纵带环 + 横向 fork（m=1@rb=2 <b>{mk('hybrid_v_bi_B2',2,1)} cy</b>）。"
         f"每个 m 在全部 rigid pack 策略中取最小。"),
        ("multitree",
         f"结构简单、任意 m 均可 0-buffer（m=1@rb=2 <b>{mk('multitree',2,1)} cy</b>）。"
         f"makespan 居中，随 m 线性上升。"),
        ("ring_bi",
         f"rb=1 大 m 可用单轮 TDM（m=3 仅 <b>{mk('ring_bi',1,3)} cy</b>）；"
         f"m=1@rb=2 <b>{mk('ring_bi',2,1)} cy</b>。对 ramp_bw 不敏感。"),
        ("ring_uni",
         f"最慢但最规整（m=1@rb=2 <b>{mk('ring_uni',2,1)} cy</b>）。环长主导，"
         f"几乎不随 ramp_bw 改善。"),
    ]
    rows = []
    for s, text in items:
        rows.append(
            f'<li class="crow"><div class="h">'
            f'<span class="dot" style="background:{SCHEME_COLOR[s]}"></span>{esc(SCHEME_LABEL[s])}</div>'
            f'<div class="b">{text}</div></li>'
        )
    return '<ul class="clist">' + "".join(rows) + "</ul>"


def row_col_table(data):
    body = []
    for rb in RBS:
        for m in FLITS:
            rc = data[rb][m]["schemes"]["row_col"]
            body.append(
                f"<tr><td>{rb}</td><td>{m}</td><td>{rc['T1']}</td>"
                f"<td>{rc['turnaround']}</td><td>{rc['T2']}</td>"
                f"<td><b>{rc['makespan']}</b></td><td>{rc['sram']}</td></tr>"
            )
    return (
        "<table class='data'><thead><tr>"
        "<th>ramp_bw</th><th>m</th><th>T1 行相</th>"
        "<th>SRAM 往返</th><th>T2 列相</th><th>Ttotal</th><th>SRAM/节点 (flit)</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def build_html(data):
    lat_m1 = R.theory_t(1, 1, H, V)
    scheme_cards = ""
    for s in [x for x in SCHEMES if x != "row_col"]:
        scheme_cards += f"""
<div class="card">
<h3><span class="dot" style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{SCHEME_COLOR[s]};margin-right:6px"></span>{esc(SCHEME_LABEL[s])}</h3>
<p class="note" style="margin-top:0">{esc(SCHEME_DESC[s])}</p>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>6×8 Mesh Allgather（H=7, V=9）</title>
<style>{CSS}</style>
</head>
<body>
<h1>6×8 Mesh Allgather 方案对比</h1>
<p class="sub">Mesh {MX}×{MY}（N={N}）· 横向 link <b>H={H} cy</b> · 纵向 link <b>V={V} cy</b> ·
上/下 ramp 各 1 cy · 数据量 m∈{{1..5}} flit/节点 · 调度：rigid 0-buffer 打包器（router 零排队）。</p>

<div class="card hero">
<h2>结论速览</h2>
<p class="lead">各方案在 6×8、H={H}/V={V} 下的 rigid 0-buffer makespan（越小越好）。
<b>hybrid_bi_Q4</b>（border Q=4）在 rb=2、m=1 最快；<b>row→col</b> 在 m≥2 领先；
<b>ring</b> 系列最慢但结构最规整。</p>
{conclusions(data)}
<p class="note">每个 m 下的最快方案（ramp_bw=2）：</p>
{winner_chips(data)}
</div>

<div class="card">
<h2>总对比表</h2>
<p class="note" style="margin-top:0">单元格为 makespan（cy），绿色为该 m 行最快。row→col 已含 SRAM 往返开销。</p>
<h3>ramp_bw = 1</h3>
{master_table(data, 1)}
<h3>ramp_bw = 2</h3>
{master_table(data, 2)}
</div>

<div class="card">
<h2>row→col 时序拆解</h2>
<p class="note" style="margin-top:0">
Ttotal = T1（行相）+ SRAM 往返 + T2（列相），三段严格串行。
行相收齐整行数据后须<b>下 ramp 存入 SRAM</b>，列相再<b>上 ramp 送出</b>，
这一往返固定 <b>{R.SRAM_TURNAROUND} cy</b>；另需每节点 (MX−1)m = 5m flit 的 SRAM 暂存。</p>
{row_col_table(data)}
<p class="note"><span class="tag tag-sram">SRAM</span> 是本地存储开销，不是 router buffer；其他方案全程直通转发，无此暂存与往返。</p>
</div>

<div class="card">
<h2>七种方案简介</h2>
<p class="note" style="margin-top:0"><code>hybrid_*_Q4</code> 即 <code>border (Q=4)</code>（<code>fp_border</code>），
与水平条带 hybrid B=4 不同。</p>
<ul class="compact">
<li><b>row→col</b>：{esc(SCHEME_DESC['row_col'])}。router 0-buffer，但有 SRAM 暂存 + 往返。</li>
<li><b>hybrid_bi_Q4</b>：{esc(SCHEME_DESC['border_bi_Q4'])}。</li>
<li><b>hybrid_uni_Q4</b>：{esc(SCHEME_DESC['border_uni_Q4'])}。</li>
<li><b>hybrid_v_bi_B2</b>：{esc(SCHEME_DESC['hybrid_v_bi_B2'])}。</li>
<li><b>multitree</b>：{esc(SCHEME_DESC['multitree'])}。</li>
<li><b>ring_bi</b>：{esc(SCHEME_DESC['ring_bi'])}。</li>
<li><b>ring_uni</b>：{esc(SCHEME_DESC['ring_uni'])}。</li>
</ul>
</div>

<div class="card">
<h2>物理模型与调度规则</h2>
<ul class="compact">
<li>每条有向链路容量 1 flit/cy；下 ramp 容量 = ramp_bw flit/cy/节点。</li>
<li><b>rigid 0-buffer</b>：每源分配唯一注入偏移，任意时刻每条链路/ramp 最多 1 个 flit，by construction 无 router 排队。</li>
<li><b>ring_uni / ring_bi / hybrid_v_bi_B2 / hybrid_*_Q4</b>：link delay 变化后重新 rigid pack；
对每个 m 枚举单轮 direct、单轮 TDM（ring/hybrid_v）、分轮组合、m×m=1 等全部可行策略，取 makespan 最小值。</li>
<li><b>hybrid_bi_Q4 @ rb=2</b>：m=1 时 115 cy，全局最快；m≥2 时 row→col 更快。</li>
<li><b>hybrid_bi_Q4 @ rb=1</b>：无 batch 收益时退化为 m×m=1（如 m=2 → 358 cy）。</li>
<li><b>ring_bi @ rb=1</b>：m≥3 时单轮 TDM 往往优于分轮 [2,1]（如 m=3 仅 217 cy）。</li>
<li><b>hybrid_v_bi_B2 @ rb=1</b>：m=2 仍用 2×m=1；m≥3 单轮 TDM 优于分轮。</li>
<li><b>row→col</b>：两阶段串行 + {R.SRAM_TURNAROUND} cy SRAM 往返。</li>
</ul>
<div class="formula">理论下界 T = max(弹出, 角节点, 延迟, 二分割)；m=1, rb=1 时 T = {lat_m1} cy（延迟地板主导）。</div>
</div>

<p class="note">Generated by <code>utils/gen_allgather_6x8_report.py</code> · H={H}, V={V} · SRAM 往返 {R.SRAM_TURNAROUND} cy</p>
</body>
</html>"""


def main():
    print(f"Computing 6×8 rigid schedules (H={H}, V={V})...")
    data = compute()
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(build_html(data), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
