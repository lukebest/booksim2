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
DOWN_BUF = 4  # eject burst buffer depth (flits) for the buffered comparison

SCHEMES = [
    "row_col", "border_bi_Q4", "border_uni_Q4", "axis_ccw",
    "hybrid_v_bi_B2", "multitree", "ring_bi", "ring_uni",
]
SCHEME_LABEL = {
    "row_col": "row→col",
    "border_bi_Q4": "hybrid_bi_Q4",
    "border_uni_Q4": "hybrid_uni_Q4",
    "axis_ccw": "axis+CCW",
    "hybrid_v_bi_B2": "hybrid_v_bi_B2",
    "multitree": "multitree",
    "ring_bi": "ring_bi",
    "ring_uni": "ring_uni",
}
SCHEME_DESC = {
    "row_col": "先按行 allgather，再按列 allgather（两阶段）",
    "border_bi_Q4": "border (Q=4)：4 象限环（长边贴中心边界）+ 边界短弧双向注入",
    "border_uni_Q4": "border (Q=4)：4 象限环 + 边界短弧单向注入",
    "axis_ccw": "源点十字轴多播，再沿各臂逆时针 90° 扇出（右→上、左→下、上→左、下→右）",
    "hybrid_v_bi_B2": "2 个纵向条带内双向环 + 逐行横向 fork",
    "multitree": "每源 X→Y 维序双向多播树",
    "ring_bi": "全局 48 点 Hamilton 双向环",
    "ring_uni": "全局 48 点 Hamilton 单向环",
}
SCHEME_COLOR = {
    "row_col": "#7c3aed",
    "border_bi_Q4": "#b45309",
    "border_uni_Q4": "#d97706",
    "axis_ccw": "#0891b2",
    "hybrid_v_bi_B2": "#dc2626",
    "multitree": "#2563eb",
    "ring_bi": "#059669",
    "ring_uni": "#94a3b8",
}

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --line:#e2e8f0; }
body { font-family: system-ui, -apple-system, sans-serif; margin:0; padding:28px 32px 64px;
       background:var(--bg); color:var(--text); line-height:1.65; max-width:1100px; }
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
table.data { border-collapse:collapse; font-size:.82rem; margin:6px 0; width:100%; }
table.data th, table.data td { border:1px solid var(--line); padding:6px 8px; text-align:center; }
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
.table-wrap { overflow-x:auto; }
@media (max-width:640px){ .crow{ grid-template-columns:1fr; } }
"""


def esc(s):
    return html.escape(str(s))


def compute():
    """data[rb][m][scheme] -> record; also data[rb][m]['schemes_d4'] for down_cap=4."""
    data = {}
    for rb in RBS:
        data[rb] = {}
        for m in FLITS:
            R.setup(H, V)
            T = R.theory_t(m, rb, H, V)
            rec = {"T": T, "schemes": {}, "schemes_d4": {}}
            for s in SCHEMES:
                print(f"  rb={rb} m={m} {s} (strict) ...", flush=True)
                rec["schemes"][s] = R.scheme_makespan(s, rb, m)
                print(f"  rb={rb} m={m} {s} (down_buf={DOWN_BUF}) ...", flush=True)
                rec["schemes_d4"][s] = R.scheme_makespan(
                    s, rb, m, down_cap=DOWN_BUF)
            data[rb][m] = rec
    return data


def fmt(v):
    return str(v) if v is not None else '<span class="dash">—</span>'


def fmt_avg(v):
    if v is None:
        return '<span class="dash">—</span>'
    return f"{v:.3f}"


def scheme_ramp(rec):
    return rec.get("ramp") or {}


def master_table(data, rb, key="schemes"):
    """One table per ramp_bw: rows = m, cols = schemes, cells = makespan."""
    head = "".join(
        f'<th style="color:{SCHEME_COLOR[s]}">{esc(SCHEME_LABEL[s])}</th>'
        for s in SCHEMES
    )
    body = []
    for m in FLITS:
        d = data[rb][m]
        mks = {s: d[key][s]["makespan"] for s in SCHEMES}
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
        "<div class='table-wrap'><table class='data'><thead><tr>"
        "<th>m (flit)</th><th>下界 T</th>" + head +
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


def delta_table(data, rb):
    """strict vs down_buf=4: show both + Δ (negative = buffered faster)."""
    head = "".join(
        f'<th colspan="3" style="color:{SCHEME_COLOR[s]}">{esc(SCHEME_LABEL[s])}</th>'
        for s in SCHEMES
    )
    sub = "".join("<th>严格</th><th>buf4</th><th>Δ</th>" for _ in SCHEMES)
    body = []
    for m in FLITS:
        d = data[rb][m]
        cells = []
        for s in SCHEMES:
            a = d["schemes"][s]["makespan"]
            b = d["schemes_d4"][s]["makespan"]
            if a is None or b is None:
                cells.append(
                    f"<td>{fmt(a)}</td><td>{fmt(b)}</td>"
                    f"<td><span class='dash'>—</span></td>")
            else:
                delta = b - a
                # highlight improvement (buf4 faster)
                dcls = "win" if delta < 0 else ""
                sign = f"{delta:+d}" if delta != 0 else "0"
                cells.append(
                    f"<td>{a}</td><td class='{dcls}'>{b}</td>"
                    f"<td class='{dcls}'>{sign}</td>")
        body.append(f"<tr><td>{m}</td>" + "".join(cells) + "</tr>")
    return (
        "<div class='table-wrap'><table class='data'><thead>"
        f"<tr><th rowspan='2'>m</th>{head}</tr>"
        f"<tr>{sub}</tr>"
        "</thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


def ramp_table(data, rb, kind, key="schemes"):
    """kind in {'avg','peak'}; show up/down ramp bandwidth per scheme."""
    if kind == "avg":
        ukey, dkey = "up_avg", "down_avg"
        title_u, title_d = "上ramp均值", "下ramp均值"
        note = ("均值 = 总 flit·cycle / (N × makespan)，单位 flit/cy/节点。"
                "反映整窗平均占用；下 ramp 理论地板 ≈ (N−1)m / (N·T)。")
    else:
        ukey, dkey = "up_peak", "down_peak"
        title_u, title_d = "上ramp峰值", "下ramp峰值"
        if key == "schemes_d4":
            note = (f"峰值 = 任意节点任意 cycle 的最大并发 flit 数。"
                    f"下 ramp 允许 ≤{DOWN_BUF}（突发缓冲）；上 ramp / 链路仍按 ramp_bw / 1。")
        else:
            note = ("峰值 = 任意节点任意 cycle 的最大并发 flit 数。"
                    "严格 0-buffer 下峰值 ≤ 配置的 ramp_bw。")

    head = "".join(
        f'<th colspan="2" style="color:{SCHEME_COLOR[s]}">{esc(SCHEME_LABEL[s])}</th>'
        for s in SCHEMES
    )
    sub = "".join(
        f"<th>{title_u}</th><th>{title_d}</th>" for _ in SCHEMES
    )
    body = []
    for m in FLITS:
        d = data[rb][m]
        cells = []
        for s in SCHEMES:
            r = scheme_ramp(d[key][s])
            uv, dv = r.get(ukey), r.get(dkey)
            if kind == "avg":
                cells.append(f"<td>{fmt_avg(uv)}</td><td>{fmt_avg(dv)}</td>")
            else:
                cells.append(f"<td>{fmt(uv)}</td><td>{fmt(dv)}</td>")
        body.append(f"<tr><td>{m}</td>" + "".join(cells) + "</tr>")
    return (
        f"<p class='note' style='margin-top:0'>{note}</p>"
        "<div class='table-wrap'><table class='data'><thead>"
        f"<tr><th rowspan='2'>m</th>{head}</tr>"
        f"<tr>{sub}</tr>"
        "</thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


def winner_chips(data, key="schemes"):
    """Fastest scheme at each (rb=2) m."""
    chips = []
    for m in FLITS:
        d = data[2][m]
        mks = {s: d[key][s]["makespan"] for s in SCHEMES}
        valid = {s: v for s, v in mks.items() if v is not None}
        best_s = min(valid, key=valid.get)
        chips.append(
            f'<div class="chip"><span class="dot" style="background:{SCHEME_COLOR[best_s]}"></span>'
            f'm={m} → <b>{esc(SCHEME_LABEL[best_s])}</b> ({valid[best_s]} cy)</div>'
        )
    return '<div class="rank">' + "".join(chips) + "</div>"


def conclusions(data):
    def mk(s, rb, m, key="schemes"):
        return data[rb][m][key][s]["makespan"]

    rc1 = data[2][1]["schemes"]["row_col"]
    bbi1 = data[2][1]["schemes"]["border_bi_Q4"]
    ax1 = data[2][1]["schemes"]["axis_ccw"]
    ax1d = data[2][1]["schemes_d4"]["axis_ccw"]
    # biggest gain at rb=1 m=1
    gains = []
    for s in SCHEMES:
        a = mk(s, 1, 1)
        b = mk(s, 1, 1, "schemes_d4")
        if a is not None and b is not None and b < a:
            gains.append((s, a - b, a, b))
    gains.sort(key=lambda x: -x[1])
    gain_txt = "；".join(
        f"{SCHEME_LABEL[s]} {a}→{b} (−{d})" for s, d, a, b in gains[:3]
    ) if gains else "多数方案收益有限"

    items = [
        ("border_bi_Q4",
         f"<b>rb=2, m=1 严格最快（{bbi1['makespan']} cy）</b>，略优于 row→col（{mk('row_col',2,1)} cy）。"
         f"即 border (Q=4) 四象限环 + 边界短弧。"),
        ("axis_ccw",
         f"十字轴 + 逆时针 90° 扇出。严格 m=1@rb=2 <b>{ax1['makespan']} cy</b>；"
         f"下 ramp 允许 {DOWN_BUF}-flit 突发后可到 <b>{ax1d['makespan']} cy</b>"
         f"（贴近延迟下界）。rb=1 时收益最大。"),
        ("row_col",
         f"m≥2 @rb=2 严格最快（m=2 <b>{mk('row_col',2,2)} cy</b>）。"
         f"需 <b>{rc1['sram']}m flit/节点 SRAM</b> + <b>{rc1['turnaround']} cy</b> 往返。"),
        ("border_uni_Q4",
         f"border (Q=4) 单向版（m=1@rb=2 严格 <b>{mk('border_uni_Q4',2,1)} cy</b>）。"),
        ("hybrid_v_bi_B2",
         f"纵带环 + 横向 fork（m=1@rb=2 <b>{mk('hybrid_v_bi_B2',2,1)} cy</b>）。"),
        ("multitree",
         f"维序树（m=1@rb=2 <b>{mk('multitree',2,1)} cy</b>）。"),
        ("ring_bi",
         f"rb=1 大 m 可用单轮 TDM（m=3 严格 <b>{mk('ring_bi',1,3)} cy</b>）。"),
        ("ring_uni",
         f"最慢但最规整（m=1@rb=2 <b>{mk('ring_uni',2,1)} cy</b>）。"),
    ]
    rows = []
    for s, text in items:
        rows.append(
            f'<li class="crow"><div class="h">'
            f'<span class="dot" style="background:{SCHEME_COLOR[s]}"></span>{esc(SCHEME_LABEL[s])}</div>'
            f'<div class="b">{text}</div></li>'
        )
    d4_mks = {s: mk(s, 2, 1, "schemes_d4") for s in SCHEMES}
    d4_best = min(d4_mks, key=lambda s: d4_mks[s] if d4_mks[s] is not None else 10**9)
    extra = (
        f'<li class="crow"><div class="h">下ramp buf={DOWN_BUF}</div>'
        f'<div class="b">链路 / 上 ramp 仍严格；仅下 ramp 每节点每 cycle 可并发 ≤{DOWN_BUF} flit'
        f'（建模为 eject 突发缓冲）。rb=1 最大收益：{gain_txt}。'
        f'buf4 下 m=1@rb=2 最快：'
        f'<b>{SCHEME_LABEL[d4_best]} ({d4_mks[d4_best]} cy)</b>。</div></li>'
    )
    return '<ul class="clist">' + "".join(rows) + extra + "</ul>"


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
上/下 ramp 各 1 cy · 数据量 m∈{{1..5}} flit/节点 · 调度：rigid packer（链路 0 冲突）。</p>

<div class="card hero">
<h2>结论速览</h2>
<p class="lead">严格 0-buffer（下 ramp 容量 = ramp_bw）与
<b>下 ramp 突发缓冲 {DOWN_BUF} flit</b>（down_cap={DOWN_BUF}，链路/上 ramp 仍严格）两套 makespan 对比。</p>
{conclusions(data)}
<p class="note">严格 0-buffer · 每个 m 最快（ramp_bw=2）：</p>
{winner_chips(data, "schemes")}
<p class="note">下 ramp buf={DOWN_BUF} · 每个 m 最快（ramp_bw=2）：</p>
{winner_chips(data, "schemes_d4")}
</div>

<div class="card">
<h2>总对比表（严格 0-buffer）</h2>
<p class="note" style="margin-top:0">下 ramp 容量 = ramp_bw；绿色为该 m 行最快。row→col 已含 SRAM 往返。</p>
<h3>ramp_bw = 1</h3>
{master_table(data, 1, "schemes")}
<h3>ramp_bw = 2</h3>
{master_table(data, 2, "schemes")}
</div>

<div class="card">
<h2>总对比表（下 ramp 突发缓冲 {DOWN_BUF} flit）</h2>
<p class="note" style="margin-top:0">
建模：每节点下 ramp 每 cycle 可并发 ≤{DOWN_BUF} flit（吸收 eject 突发）；
链路仍 1 flit/cy、上 ramp 仍 = ramp_bw。与严格调度取较优（严格调度在 buf={DOWN_BUF} 下仍合法）。</p>
<h3>ramp_bw = 1</h3>
{master_table(data, 1, "schemes_d4")}
<h3>ramp_bw = 2</h3>
{master_table(data, 2, "schemes_d4")}
</div>

<div class="card">
<h2>严格 vs buf={DOWN_BUF} 对照（Δ = buf4 − 严格）</h2>
<p class="note" style="margin-top:0">Δ&lt;0 表示突发缓冲缩短了 makespan（绿色）。</p>
<h3>ramp_bw = 1</h3>
{delta_table(data, 1)}
<h3>ramp_bw = 2</h3>
{delta_table(data, 2)}
</div>

<div class="card">
<h2>Ramp 带宽：平均值（严格）</h2>
<p class="note" style="margin-top:0">每个方案在最优 makespan 调度下的上/下 ramp 平均带宽（flit/cy/节点）。</p>
<h3>ramp_bw = 1</h3>
{ramp_table(data, 1, "avg", "schemes")}
<h3>ramp_bw = 2</h3>
{ramp_table(data, 2, "avg", "schemes")}
</div>

<div class="card">
<h2>Ramp 带宽：突发峰值（严格）</h2>
<p class="note" style="margin-top:0">任意节点任意 cycle 的最大并发 flit 数（上/下 ramp 分开统计）。</p>
<h3>ramp_bw = 1</h3>
{ramp_table(data, 1, "peak", "schemes")}
<h3>ramp_bw = 2</h3>
{ramp_table(data, 2, "peak", "schemes")}
</div>

<div class="card">
<h2>Ramp 带宽：突发峰值（下 ramp buf={DOWN_BUF}）</h2>
<p class="note" style="margin-top:0">buf={DOWN_BUF} 调度下的峰值；下 ramp 峰值可达 {DOWN_BUF}。</p>
<h3>ramp_bw = 1</h3>
{ramp_table(data, 1, "peak", "schemes_d4")}
<h3>ramp_bw = 2</h3>
{ramp_table(data, 2, "peak", "schemes_d4")}
</div>

<div class="card">
<h2>row→col 时序拆解（严格）</h2>
<p class="note" style="margin-top:0">
Ttotal = T1（行相）+ SRAM 往返 + T2（列相），三段严格串行。
行相收齐整行数据后须<b>下 ramp 存入 SRAM</b>，列相再<b>上 ramp 送出</b>，
这一往返固定 <b>{R.SRAM_TURNAROUND} cy</b>；另需每节点 (MX−1)m = 5m flit 的 SRAM 暂存。</p>
{row_col_table(data)}
<p class="note"><span class="tag tag-sram">SRAM</span> 是本地存储开销，不是 router buffer；其他方案全程直通转发，无此暂存与往返。</p>
</div>

<div class="card">
<h2>八种方案简介</h2>
<p class="note" style="margin-top:0"><code>hybrid_*_Q4</code> 即 <code>border (Q=4)</code>（<code>fp_border</code>），
与水平条带 hybrid B=4 不同。<code>axis+CCW</code> 为十字轴 + 逆时针扇出方案。</p>
<ul class="compact">
<li><b>row→col</b>：{esc(SCHEME_DESC['row_col'])}。router 0-buffer，但有 SRAM 暂存 + 往返。</li>
<li><b>hybrid_bi_Q4</b>：{esc(SCHEME_DESC['border_bi_Q4'])}。</li>
<li><b>hybrid_uni_Q4</b>：{esc(SCHEME_DESC['border_uni_Q4'])}。</li>
<li><b>axis+CCW</b>：{esc(SCHEME_DESC['axis_ccw'])}。</li>
<li><b>hybrid_v_bi_B2</b>：{esc(SCHEME_DESC['hybrid_v_bi_B2'])}。</li>
<li><b>multitree</b>：{esc(SCHEME_DESC['multitree'])}。</li>
<li><b>ring_bi</b>：{esc(SCHEME_DESC['ring_bi'])}。</li>
<li><b>ring_uni</b>：{esc(SCHEME_DESC['ring_uni'])}。</li>
</ul>
</div>

<div class="card">
<h2>物理模型与调度规则</h2>
<ul class="compact">
<li>每条有向链路容量 1 flit/cy；上 ramp 容量 = ramp_bw flit/cy/节点。</li>
<li><b>严格 0-buffer</b>：下 ramp 容量 = ramp_bw；任意时刻每条链路/ramp 不超容量，by construction 无 router 排队。</li>
<li><b>下 ramp 突发缓冲 {DOWN_BUF}</b>：仅放宽下 ramp 容量至 {DOWN_BUF} flit/cy/节点（吸收 eject 突发）；
链路与上 ramp 约束不变。等价于「到达后可在 eject 队列暂存，深度 ≤{DOWN_BUF}」的刚性近似。</li>
<li><b>axis+CCW</b>：源先沿行/列四臂多播；各臂节点再按臂方向逆时针 90° 扇出覆盖象限；源内链路无重复。</li>
<li><b>ring / hybrid / border</b>：对每个 m 枚举单轮 / TDM / 分轮 / m×m=1，取 makespan 最小。</li>
<li><b>row→col</b>：两阶段串行 + {R.SRAM_TURNAROUND} cy SRAM 往返。</li>
</ul>
<div class="formula">理论下界 T = max(弹出, 角节点, 延迟, 二分割)；m=1, rb=1 时 T = {lat_m1} cy（延迟地板主导）。</div>
</div>

<p class="note">Generated by <code>utils/gen_allgather_6x8_report.py</code> · H={H}, V={V} ·
SRAM 往返 {R.SRAM_TURNAROUND} cy · 下 ramp 突发缓冲对比 depth={DOWN_BUF}</p>
</body>
</html>"""


def main():
    print(f"Computing 6×8 rigid schedules (H={H}, V={V}, down_buf={DOWN_BUF})...")
    data = compute()
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(build_html(data), encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
