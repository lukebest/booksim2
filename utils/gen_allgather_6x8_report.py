#!/usr/bin/env python3
"""Generate HTML report: 6x8 2D mesh allgather — three scheme families compared.

Makespan / buffer numbers use TRUE 0-buffer rigid packer (sched_zerobuf_compare)
where pack succeeds; otherwise falls back to event-driven sweep with buffer noted.

Output: results/report_allgather_6x8.html
"""

import html
import json
from pathlib import Path

import export_booksim_trace as ET
import export_zbuf_booksim_6x8 as ZB

ROOT = Path(__file__).resolve().parents[1]
SWEEP_JSON = ROOT / "results" / "allgather_scale_sweep.json"
BOOKSIM_JSON = ROOT / "results" / "booksim_zbuf_6x8_sweep.json"
HTML_PATH = ROOT / "results" / "report_allgather_6x8.html"

MX, MY = 6, 8
N = MX * MY
FLITS = [1, 2, 3, 4, 5]
RAMP_BWS = [1, 2]
SCHEMES = ["multitree", "ring_uni", "ring_bi", "hybrid_v_bi_B2"]

SCHEME_LABEL = {
    "multitree": "方案一：multitree（X→Y 维序树）",
    "ring_uni": "方案二：ring_uni（全局单向 Hamilton 环）",
    "ring_bi": "方案二：ring_bi（全局双向 Hamilton 环）",
    "hybrid_v_bi_B2": "方案二：hybrid_v_bi_B2（2 纵带环 + 横向 fork）",
    "row_col": "方案三：row→col 二阶段 allgather",
}

CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; }
body { font-family: system-ui, -apple-system, sans-serif; margin:0; padding:24px 32px 56px;
       background:var(--bg); color:var(--text); line-height:1.65; max-width:1080px; }
h1 { font-size:1.55rem; margin:0 0 6px; }
h2 { font-size:1.12rem; margin:28px 0 10px; color:#1e3a8a; border-top:1px solid #e2e8f0; padding-top:20px; }
h3 { font-size:1.0rem; margin:16px 0 8px; color:#334155; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:10px;
        padding:20px 24px; margin:16px 0; }
.meta { color:var(--muted); font-size:.9rem; }
.note { color:var(--muted); font-size:.87rem; }
code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:.85em; }
table.data { border-collapse:collapse; font-size:.82rem; margin:12px 0; width:100%; }
table.data th, table.data td { border:1px solid #e2e8f0; padding:6px 10px; text-align:center; }
table.data th { background:#f1f5f9; font-weight:600; }
table.data td.name { text-align:left; }
table.data tr.best td { background:#ecfdf5; font-weight:600; }
table.data tr.zbuf td { background:#eff6ff; }
.tag { display:inline-block; font-size:.72rem; padding:1px 6px; border-radius:4px; margin-left:4px; vertical-align:1px; }
.tag-ok { background:#dcfce7; color:#166534; }
.tag-warn { background:#fef3c7; color:#92400e; }
.tag-info { background:#dbeafe; color:#1e40af; }
ul.compact li { margin:4px 0; }
.formula { font-family: ui-monospace, monospace; background:#f8fafc; border:1px solid #e2e8f0;
           border-radius:6px; padding:8px 12px; margin:8px 0; font-size:.86rem; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:760px) { .grid2 { grid-template-columns:1fr; } }
.bar-wrap { display:flex; align-items:center; gap:8px; margin:4px 0; font-size:.82rem; }
.bar { height:18px; border-radius:4px; min-width:2px; }
.legend-row { display:flex; flex-wrap:wrap; gap:14px; margin:10px 0; font-size:.85rem; }
.legend-row span { display:flex; align-items:center; gap:5px; }
.swatch { width:14px; height:14px; border-radius:3px; display:inline-block; }
"""


BUILDERS = {n: b for n, b in ET.scheme_builders()}


def rigid_record(scheme, ramp_bw, m):
    if scheme == "ring_bi":
        packed = ET.pack_ring_bi(ramp_bw, m)
        if packed:
            rec = {
                "makespan": packed["makespan"],
                "max_link_wait": 0,
                "max_ramp_wait": 0,
                "source": "rigid_0buf",
                "zbuf": True,
            }
            if packed.get("batches"):
                rec["batches"] = packed["batches"]
            return rec
    if scheme == "hybrid_v_bi_B2":
        packed = ET.pack_hybrid_v_bi_B2(ramp_bw, m)
        if packed:
            rec = {
                "makespan": packed["makespan"],
                "max_link_wait": 0,
                "max_ramp_wait": 0,
                "source": "rigid_0buf",
                "zbuf": True,
            }
            if packed.get("batches"):
                rec["batches"] = packed["batches"]
            return rec
    if scheme in BUILDERS:
        packed = ET.pack_scheme(
            lambda s, rb, bf=BUILDERS[scheme]: bf(s, rb), ramp_bw, m)
        if packed:
            return {
                "makespan": packed["makespan"],
                "max_link_wait": 0,
                "max_ramp_wait": 0,
                "source": "rigid_0buf",
                "zbuf": True,
            }
    return None


def load_booksim_zbuf():
    if not BOOKSIM_JSON.exists():
        return {}
    rows = json.loads(BOOKSIM_JSON.read_text(encoding="utf-8"))
    out = {}
    for r in rows:
        if r.get("route") != "B_tree":
            continue
        k = (r["scheme"], r["ramp_bw"], r["m"])
        out[k] = r
    return out


def booksim_cell(bsim, scheme, rb, m, zbuf):
    if not zbuf:
        return "—", '<span class="tag tag-warn">无 rigid 0-buf</span>'
    r = bsim.get((scheme, rb, m))
    if not r:
        return "—", ""
    mk = r.get("sim_makespan")
    stalls = r.get("buffer_full_stalls")
    ok = r.get("ok")
    if mk is None:
        return "—", ""
    stall_note = f" stalls={stalls}" if stalls not in (None, 0) else ""
    cls = "tag-ok" if ok and stalls == 0 else "tag-warn"
    return f"<b>{mk}</b>", f'<span class="tag {cls}">{("ok" if ok else "fail")}{stall_note}</span>'


def row_col_schedule(h, v, ramp_bw, m):
    packed = ZB.row_col_packed(ramp_bw, m)
    assert packed is not None
    return {
        "T1": packed["T1"],
        "T2": packed["T2"],
        "Ttotal": packed["makespan"],
        "sram": packed["sram_per_node"],
        "source": "rigid_0buf",
        "zbuf": True,
    }


def esc(s):
    return html.escape(str(s))


def load_data():
    sweep = json.loads(SWEEP_JSON.read_text(encoding="utf-8"))
    h, v = sweep["h"], sweep["v"]
    out = {}
    for rb in RAMP_BWS:
        out[rb] = {}
        cell_root = sweep["data"]["6x8"]["bw"][str(rb)]
        for m in FLITS:
            cell = cell_root[str(m)]
            res = {r["name"]: r for r in cell["results"]}
            rc = row_col_schedule(h, v, rb, m)
            schemes = {}
            for nm in SCHEMES:
                rigid = rigid_record(nm, rb, m)
                if rigid:
                    schemes[nm] = rigid
                else:
                    ed = res[nm]
                    schemes[nm] = {
                        "makespan": ed["makespan"],
                        "max_link_wait": ed["max_link_wait"],
                        "max_ramp_wait": ed["max_ramp_wait"],
                        "source": "event_driven",
                        "zbuf": False,
                        "ed_note": True,
                    }
            out[rb][m] = {
                "T": cell["T"],
                "schemes": schemes,
                "row_col": rc,
            }
    return out, h, v


def buf_cell(link_w, ramp_w, sram=None, router_zero=False):
    parts = []
    if router_zero or (link_w == 0 and ramp_w == 0):
        parts.append('<span class="tag tag-ok">router 0</span>')
    else:
        parts.append(f"link {link_w} / ramp {ramp_w}")
    if sram:
        parts.append(f'<br><span class="tag tag-info">SRAM {sram} flit</span>')
    return "".join(parts)


def source_tag(rec):
    if rec.get("source") == "rigid_0buf":
        return '<span class="tag tag-ok">rigid 0-buf</span>'
    return '<span class="tag tag-warn">ED 需 buffer</span>'


def scheme_table(data, rb, scheme_key, bsim):
    rows = []
    for m in FLITS:
        d = data[rb][m]
        sk = "row_col" if scheme_key == "row_col" else scheme_key
        if scheme_key == "row_col":
            rc = d["row_col"]
            mk = rc["Ttotal"]
            zbuf = rc.get("zbuf", True)
            buf = buf_cell(0, 0, sram=rc["sram"], router_zero=True)
            src = source_tag(rc)
            extra = f"<td>{rc['T1']}</td><td>{rc['T2']}</td><td>{rc['sram']}</td>"
        else:
            r = d["schemes"][scheme_key]
            mk = r["makespan"]
            lw, rw = r["max_link_wait"], r["max_ramp_wait"]
            zbuf = r.get("zbuf", lw == 0 and rw == 0)
            buf = buf_cell(lw, rw, router_zero=zbuf)
            src = source_tag(r)
            extra = f"<td>{src}</td>"
        bmk, btag = booksim_cell(bsim, sk, rb, m, zbuf)
        cls = "zbuf" if zbuf else ""
        ratio = mk / d["T"] if d["T"] else None
        rows.append(
            f"<tr class='{cls}'><td>{m}</td><td>{d['T']}</td><td><b>{mk}</b></td>"
            f"{extra}<td>{buf}</td><td>{bmk}<br>{btag}</td><td>{ratio:.3f}</td></tr>"
        )
    if scheme_key == "row_col":
        hdr = (
            "<table class='data'><thead><tr>"
            "<th>m (flit)</th><th>理论下界 T</th><th>Ttotal</th>"
            "<th>T1 行相</th><th>T2 列相</th><th>SRAM/节点</th>"
            "<th>Buffer</th><th>BookSim B mk</th><th>mk/T</th></tr></thead><tbody>"
        )
    else:
        hdr = (
            "<table class='data'><thead><tr>"
            "<th>m (flit)</th><th>理论下界 T</th><th>makespan</th>"
            "<th>调度来源</th><th>Buffer</th>"
            "<th>BookSim B mk</th><th>mk/T</th></tr></thead><tbody>"
        )
    return hdr + "".join(rows) + "</tbody></table>"


def compare_table_m1(data, bsim):
    """Summary table for ramp_bw=2, m=1."""
    d = data[2][1]
    scheme_keys = [
        ("multitree", "multitree"),
        ("ring_uni", "ring_uni"),
        ("ring_bi", "ring_bi"),
        ("hybrid_v_bi_B2", "hybrid_v_bi_B2"),
        ("row→col", "row_col"),
    ]
    entries = []
    for label, key in scheme_keys:
        if key == "row_col":
            rec = d["row_col"]
            mk, sram, src = rec["Ttotal"], rec["sram"], rec["source"]
        else:
            rec = d["schemes"][key]
            mk, sram = rec["makespan"], 0
            src = rec["source"]
        bmk, _ = booksim_cell(bsim, key, 2, 1, rec.get("zbuf", True))
        entries.append((label, mk, sram, src, bmk))
    best_mk = min(e[1] for e in entries)
    rows = []
    for name, mk, sram, src, bmk in entries:
        cls = "best" if mk == best_mk else ""
        buf = "router 0"
        if sram:
            buf += f" + SRAM {sram} flit"
        rows.append(
            f"<tr class='{cls}'><td class='name'>{esc(name)}</td><td>{mk}</td>"
            f"<td>{bmk}</td><td>{buf}</td>"
            f"<td>{'rigid 0-buf' if src == 'rigid_0buf' else 'ED'}</td></tr>"
        )
    hdr = (
        "<table class='data'><thead><tr>"
        "<th>方案</th><th>Python mk (cy)</th><th>BookSim B mk (cy)</th>"
        "<th>Buffer</th><th>数据来源</th>"
        "</tr></thead><tbody>"
    )
    return hdr + "".join(rows) + "</tbody></table>"


def makespan_bar_svg(data, rb):
    """Grouped bar chart: makespan vs m for all schemes."""
    schemes_plot = [
        ("multitree", "#2563eb"),
        ("ring_uni", "#94a3b8"),
        ("ring_bi", "#059669"),
        ("hybrid_v_bi_B2", "#dc2626"),
        ("row_col", "#7c3aed"),
    ]
    pad_l, pad_t, pad_r, pad_b = 48, 24, 16, 36
    group_w = 88
    bar_w = 14
    gap = 2
    max_mk = max(
        data[rb][m]["schemes"]["multitree"]["makespan"]
        if s != "row_col"
        else data[rb][m]["row_col"]["Ttotal"]
        for m in FLITS
        for s, _ in schemes_plot
    )
    plot_h = 220
    W = pad_l + len(FLITS) * group_w + pad_r
    H = pad_t + plot_h + pad_b
    parts = [
        f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;height:auto;display:block">',
        f'<text x="{pad_l + len(FLITS)*group_w/2:.0f}" y="16" text-anchor="middle" '
        f'font-size="12" font-weight="600" fill="#334155">makespan vs m (ramp_bw={rb})</text>',
    ]
    for i, m in enumerate(FLITS):
        gx = pad_l + i * group_w + group_w / 2
        parts.append(
            f'<text x="{gx:.0f}" y="{H - 8}" text-anchor="middle" '
            f'font-size="11" fill="#475569">m={m}</text>'
        )
        for j, (skey, color) in enumerate(schemes_plot):
            if skey == "row_col":
                mk = data[rb][m]["row_col"]["Ttotal"]
            else:
                mk = data[rb][m]["schemes"][skey]["makespan"]
            bh = (mk / max_mk) * plot_h
            bx = pad_l + i * group_w + 8 + j * (bar_w + gap)
            by = pad_t + plot_h - bh
            parts.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w}" height="{bh:.1f}" '
                f'fill="{color}" rx="2" opacity="0.9"/>'
            )
    parts.append("</svg>")
    legend = '<div class="legend-row">' + "".join(
        f'<span><span class="swatch" style="background:{c}"></span>{esc(s.replace("_"," "))}</span>'
        for s, c in schemes_plot
    ) + "</div>"
    return "\n".join(parts) + legend


def build_html(data, h, v, bsim):
    compare_m1 = compare_table_m1(data, bsim)
    bars1 = makespan_bar_svg(data, 1)
    bars2 = makespan_bar_svg(data, 2)
    bsim_note = (
        "Makespan / Buffer 来自 <code>sched_zerobuf_compare.py</code> 刚性 0-buffer 打包器"
        "（pack 成功则 by construction router/link/ramp 零排队）；pack 失败格回退 ED 并标黄。"
        " BookSim Route B 仅覆盖 rigid 0-buffer 格。"
        if bsim else ""
    )

    scheme_sections = ""
    for key in ["multitree", "ring_uni", "ring_bi", "hybrid_v_bi_B2"]:
        scheme_sections += f"""
<div class="card">
<h3>{esc(SCHEME_LABEL[key])}</h3>
<h4>ramp_bw = 1</h4>
{scheme_table(data, 1, key, bsim)}
<h4>ramp_bw = 2</h4>
{scheme_table(data, 2, key, bsim)}
</div>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>6×8 Mesh Allgather 三方案分析</title>
<style>{CSS}</style>
</head>
<body>
<h1>6×8 2D Mesh Allgather 三方案分析</h1>
<p class="meta">Mesh {MX}×{MY}（N={N}），H={h} cy，V={v} cy，上/下 ramp 各 1 cy，下环带宽 ramp_bw ∈ {{1, 2}} flit/cy/节点，数据量 m ∈ {{1..5}} flit/节点。</p>

<div class="card">
<h2>物理模型与 Buffer 定义</h2>
<ul class="compact">
<li><b>刚性 0-buffer 打包器</b>（<code>sched_zerobuf_compare.py</code>）：每个源分配唯一注入偏移，任意时刻每条有向链路 / 上环 / 下环最多被一个 flit 占用——<b>by construction 零 router/link/ramp 排队</b>。本报告 makespan 优先采用此数据。</li>
<li><b>事件驱动仿真</b>（<code>allgather_fast_sim.py</code>）：贪心“链路一空就发”，makespan 更低但可能需排队；仅作 rigid pack 失败时的回退参考。</li>
<li><b>节点 SRAM</b>（row→col 独有）：行相结束后须本地攒够 (MX−1)×m flit 再做列相。</li>
</ul>
<div class="formula">理论下界 T = max(弹出下界, 角节点下界, 延迟下界, 二分带宽下界)；6×8 在 m=1,ramp_bw=1 时 T=64 cy（角节点下界紧）。</div>
<p class="note">{bsim_note}</p>
</div>

<div class="card">
<h2>Executive Summary（ramp_bw=2, m=1，刚性 0-buffer）</h2>
{compare_m1}
<p class="note">绿色高亮为 rigid 0-buffer makespan 全场最快。<b>row→col</b> 71 cy 最优；BookSim Route B 与 rigid 模型偏差约 2 cy。</p>
</div>

<div class="card">
<h2>Makespan 随 m 变化</h2>
{bars1}
{bars2}
</div>

<h2>方案一：Tree（multitree）</h2>
<div class="card">
<p>6×8 上 rigid pack 对全部 m 均可构造 0-buffer 调度（m 越大 makespan 越高；ED 贪心可更低但需 router 排队）。</p>
<h4>ramp_bw = 1</h4>
{scheme_table(data, 1, "multitree", bsim)}
<h4>ramp_bw = 2</h4>
{scheme_table(data, 2, "multitree", bsim)}
</div>

<h2>方案二：ring_uni / ring_bi / hybrid_v_bi_B2</h2>
<div class="card">
<ul class="compact">
<li><b>ring_uni</b>：任意 m 均有 rigid 0-buffer 调度（如 m=5@rb=1：rigid mk=309 vs ED 贪心 mk=274+link wait）。</li>
<li><b>ring_bi</b>：rb=1 时前/后环 TDM + 分轮（m=2 单轮双 flit；m=3→2+1，m=4→2+2，m=5→2+2+1）均可 rigid 0-buffer；rb=2 全部 m 可单轮 pack。</li>
<li><b>hybrid_v_bi_B2</b>：rb=1 时 m=2 前/后环 TDM <b>可行</b>（单轮 mk=264）；采用与 ring_bi 相同分轮 [2]/[2,1]/…；注：m=2 若改 m×m=1 串行仅 164 cy 更优但需多轮注入。</li>
</ul>
</div>
{scheme_sections}

<h2>方案三：先 Row Allgather，后 Column Allgather</h2>
<div class="card">
<h3>算法</h3>
<ol class="compact">
<li><b>行相</b>：每行独立 allgather（6 节点），各节点获得整行 6m flit。</li>
<li><b>列相</b>：每列独立 allgather，转发整行包（6m flit/次注入），完成后每节点持有 48m flit。</li>
</ol>
<h3>Buffer 诉求（两层）</h3>
<ul class="compact">
<li><b>网络内（router/link）</b>：严格 <span class="tag tag-ok">0 buffer</span>，对任意 m 成立——1D  fork 结构 + 刚性偏移在 6×1 / 1×8 虚拟网格上天然无重叠。</li>
<li><b>节点 SRAM</b>：<span class="tag tag-info">(MX−1)×m = 5m flit/节点</span>——第二阶段须等整行到齐、本地落地后再二次上环；tree/ring/hybrid 全程直通转发，无此暂存。</li>
</ul>
<h3>时序（Ttotal = T1 + T2，两阶段严格串行）</h3>
<h4>ramp_bw = 1</h4>
{scheme_table(data, 1, "row_col", bsim)}
<h4>ramp_bw = 2</h4>
{scheme_table(data, 2, "row_col", bsim)}
<p class="note">rb=2,m=1 时 Ttotal=71 cy 为 rigid 0-buffer 全场最快。</p>
</div>

<div class="card">
<h2>综合结论</h2>
<table class="data">
<thead><tr><th>维度</th><th>方案一 tree</th><th>方案二 ring/hybrid</th><th>方案三 row→col</th></tr></thead>
<tbody>
<tr><td class="name">m=1 最优 makespan@rb=2</td><td>96 cy</td><td>126 cy (hybrid rigid) / 140 (ring_bi)</td><td><b>71 cy</b></td></tr>
<tr><td class="name">rigid 0-buffer 适用范围</td><td>6×8 全部 m</td><td>ring_uni / ring_bi / hybrid_v_bi_B2 全部 m（rb=1 分轮 TDM）</td><td>任意 m</td></tr>
<tr><td class="name">额外本地暂存</td><td>0</td><td>0</td><td>5m flit/节点</td></tr>
<tr><td class="name">m 增大趋势</td><td>rigid mk 线性升</td><td>ring 最慢；hybrid rigid 较快但 rb=1 受限</td><td>两阶段串行，小 m 最优</td></tr>
<tr><td class="name">适用场景</td><td>可接受较长 rigid mk</td><td>ring 简单 0-buffer；hybrid 要快且能开 rb=2</td><td>小 m + 高下环带宽 + SRAM</td></tr>
</tbody>
</table>
</div>

<p class="meta">Generated by <code>utils/gen_allgather_6x8_report.py</code> · rigid pack + <code>results/allgather_scale_sweep.json</code> (ED fallback) · BookSim: <code>results/booksim_zbuf_6x8_sweep.json</code></p>
</body>
</html>
"""


def main():
    data, h, v = load_data()
    bsim = load_booksim_zbuf()
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(build_html(data, h, v, bsim), encoding="utf-8")
    print(f"Wrote {HTML_PATH} (BookSim rows: {len(bsim)})")


if __name__ == "__main__":
    main()
