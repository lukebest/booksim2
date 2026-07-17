#!/usr/bin/env python3
"""HTML report: multi-scheme makespan vs implementation-area Pareto (8x6)."""

from __future__ import annotations

import html
import json
from pathlib import Path

import ppa_analytic_model as PPA
from dse_axis_area_makespan import A_FLIT, CROSSBAR_PORT, GATHER_DEPTH
from dse_tree_allgather_6x8 import MX, MY, coord, nid, col_comb_tree

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "multi_area_makespan.json"
MF_JSON_PATH = ROOT / "results" / "multiflit_area_makespan.json"
PB_JSON_PATH = ROOT / "results" / "portbuf_area_makespan.json"
HTML_PATH = ROOT / "results" / "report_multi_area_makespan.html"


def esc(v) -> str:
    return html.escape(str(v))


_CELL, _R, _MARGIN, _TOP = 44, 7, 24, 6
_C_COL, _C_SPINE, _C_COMB, _C_SRC = "#475569", "#ea580c", "#2563eb", "#dc2626"


def _svg_col_comb(s: int) -> str:
    """Render the col-comb3 arborescence of source s (y-up)."""
    sx, sy = coord(s)
    edge = 0 if sy <= (MY - 1) // 2 else MY - 1
    edges = col_comb_tree(s)
    w = _MARGIN * 2 + (MX - 1) * _CELL
    h = _TOP + _MARGIN * 2 + (MY - 1) * _CELL

    def px(x):
        return _MARGIN + x * _CELL

    def py(y):
        return _TOP + _MARGIN + (MY - 1 - y) * _CELL

    defs = "".join(
        f'<marker id="cb{cid}" markerWidth="7" markerHeight="7" refX="6" '
        f'refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="{col}"/>'
        f"</marker>"
        for cid, col in (("c", _C_COL), ("s", _C_SPINE), ("f", _C_COMB))
    )
    lines = []
    for p, c in edges:
        pxx, pyy = coord(p)
        cxx, cyy = coord(c)
        if pxx == sx and cxx == sx:
            col, mid = _C_COL, "c"
        elif pyy == edge and cyy == edge:
            col, mid = _C_SPINE, "s"
        else:
            col, mid = _C_COMB, "f"
        x1, y1, x2, y2 = px(pxx), py(pyy), px(cxx), py(cyy)
        dx, dy = x2 - x1, y2 - y1
        d = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / d, dy / d
        lines.append(
            f'<line x1="{x1 + ux * (_R + 1):.1f}" y1="{y1 + uy * (_R + 1):.1f}" '
            f'x2="{x2 - ux * (_R + 4):.1f}" y2="{y2 - uy * (_R + 4):.1f}" '
            f'stroke="{col}" stroke-width="2" marker-end="url(#cb{mid})"/>'
        )
    nodes = []
    for y in range(MY):
        for x in range(MX):
            src = (x == sx and y == sy)
            fill = _C_SRC if src else "#fff"
            stroke = _C_SRC if src else "#94a3b8"
            nodes.append(
                f'<circle cx="{px(x)}" cy="{py(y)}" r="{_R}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{2 if src else 1}"/>'
            )
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'xmlns="http://www.w3.org/2000/svg"><defs>{defs}</defs>'
        f'{"".join(lines)}{"".join(nodes)}</svg>'
    )


def main() -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    lb = data["model"]["lower_bound_rb2"]
    pts = data["points"]
    front = data["global_pareto"]
    meta = data["scheme_meta"]
    gen = esc(data["generated_at"])

    on_pareto = sorted({p["scheme"] for p in front})
    # per-scheme floor makespan + cheapest area achieving it
    floors = {}
    for key in meta:
        sp = [p for p in pts if p["scheme"] == key and p["makespan"]]
        fl = min(p["makespan"] for p in sp)
        area_at_fl = min(p["area_total"] for p in sp if p["makespan"] == fl)
        floors[key] = (fl, area_at_fl)

    # axis exclusive low-makespan band: [lb, next best scheme floor)
    other_floor = min(floors[k][0] for k in meta if k != "axis_ccw")

    scheme_rows = "".join(
        f"<tr><td class='l'>{esc(meta[k]['label'])}</td>"
        f"<td>{meta[k]['pmax']}</td><td>{meta[k]['issue']}</td>"
        f"<td>{meta[k]['dilation']}</td>"
        f"<td>{floors[k][0]}</td><td>{floors[k][1]:.4f}</td>"
        f"<td>{'是' if k in on_pareto else '否'}</td></tr>"
        for k in meta
    )

    front_rows = "".join(
        f"<tr><td>{p['area_total']:.4f}</td>"
        f"<td class='{'win' if p['makespan']==lb else ''}'>{p['makespan']}</td>"
        f"<td class='l'>{esc(p['label'])}</td>"
        f"<td>W{p['W']}/E{p['E']}/B{p['B']}</td></tr>"
        for p in front
    )

    label_of = {k: meta[k]["label"] for k in meta}
    views = data.get("lambda_views", [])
    smallest = data.get("smallest_lambda_low_pmax_le118", {})
    conv = data.get("convergence", {})
    LOW_BAND = 118

    def own_str(view):
        parts = []
        for sc, mks in view["ownership"].items():
            parts.append(f"{esc(label_of.get(sc, sc))}: {mks}")
        return "; ".join(parts)

    lam_rows = "".join(
        f"<tr><td>{v['lambda']}×</td>"
        f"<td class='l'>{esc(', '.join(str(m) for m in v['axis_owns']))}</td>"
        f"<td class='l'>{esc(', '.join(label_of.get(s, s) for s in v['low_band_nonaxis_owners']) or '—')}</td>"
        f"<td class='l'>{own_str(v)}</td></tr>"
        for v in views
    )

    # ---- multi-flit (R rounds) pipelined section ----
    mf = json.loads(MF_JSON_PATH.read_text(encoding="utf-8"))
    R = mf["model"]["rounds"]
    mf_pts = mf["points"]
    mf_meta = mf["scheme_meta"]
    mf_lbl = {k: mf_meta[k]["label"] for k in mf_meta}
    front_avg = mf["pareto_area_tavg"]
    front_t5 = mf["pareto_t1_t5"]

    def mf_floor(key, field):
        vals = [p[field] for p in mf_pts if p["scheme"] == key and p[field] is not None]
        return min(vals) if vals else None

    # order schemes by best T5 (throughput champion first)
    mf_order = sorted(mf_meta, key=lambda k: (mf_floor(k, "t5") or 1e9))
    mf_rows = "".join(
        f"<tr><td class='l'>{esc(mf_lbl[k])}</td>"
        f"<td>{mf_meta[k]['link_reuse']}</td>"
        f"<td>{mf_floor(k, 't1')}</td>"
        f"<td>{mf_floor(k, 'ii')}</td>"
        f"<td class='{'win' if mf_floor(k,'t5')==mf_floor(mf_order[0],'t5') else ''}'>{mf_floor(k, 't5')}</td>"
        f"<td>{mf_floor(k, 't_avg')}</td></tr>"
        for k in mf_order
    )
    mf_avg_rows = "".join(
        f"<tr><td>{p['area_total']:.4f}</td><td>{p['t_avg']}</td>"
        f"<td>{p['t1']}</td><td>{p['ii']}</td><td>{p['t5']}</td>"
        f"<td class='l'>{esc(mf_lbl[p['scheme']])}</td>"
        f"<td>W{p['W']}/E{p['E']}/B{p['B']}</td></tr>"
        for p in front_avg
    )
    mf_t5_rows = "".join(
        f"<tr><td>{p['t1']}</td><td>{p['t5']}</td><td>{p['ii']}</td>"
        f"<td class='l'>{esc(mf_lbl[p['scheme']])}</td>"
        f"<td>W{p['W']}/E{p['E']}/B{p['B']}</td></tr>"
        for p in front_t5
    )
    axis_t1 = mf_floor("axis_ccw", "t1")
    axis_t5 = mf_floor("axis_ccw", "t5")
    best_t5_key = mf_order[0]
    best_t5 = mf_floor(best_t5_key, "t5")

    # ---- port-buffered dynamic router section ----
    pb = json.loads(PB_JSON_PATH.read_text(encoding="utf-8"))
    pb_pts = pb["buffered_points"]
    pb_front = pb["pareto_global"]
    pb_meta = pb["scheme_meta"]

    def pb_best(key):
        """(best makespan, config) among buffered points of a scheme."""
        cand = [p for p in pb_pts if p["scheme"] == key and p["makespan"]]
        if not cand:
            return None
        b = min(cand, key=lambda p: (p["makespan"], p["area_total"]))
        return b

    pb_rows = []
    for k in pb_meta:
        b = pb_best(k)
        qmin_ok = min((p["Q"] for p in pb_pts
                       if p["scheme"] == k and p["makespan"]), default=None)
        cfg = f"Q{b['Q']}/W{b['W']}/E{b['E']}/B{b['B']}" if b else "—"
        area_s = f"{b['area_total']:.4f}" if b else "—"
        pb_rows.append(
            f"<tr><td class='l'>{esc(pb_meta[k]['label'])}</td>"
            f"<td>{pb_meta[k]['fanout_max']}</td>"
            f"<td>{qmin_ok}</td>"
            f"<td>{b['makespan'] if b else '—'}</td>"
            f"<td>{cfg}</td><td>{area_s}</td></tr>"
        )
    pb_rows = "".join(pb_rows)

    pb_front_rows = "".join(
        f"<tr><td>{p['area_total']:.4f}</td><td>{p['makespan']}</td>"
        f"<td class='l'>{esc(p['label'])}</td>"
        f"<td>{'动态buffer' if p['mode'] == 'buffered' else '刚性日历'}</td></tr>"
        for p in pb_front
    )

    # col-comb3 diagrams: one lower-half source, one upper-half source
    cc_s1 = nid(3, 1)
    cc_s2 = nid(5, 4)
    cc_svg1 = _svg_col_comb(cc_s1)
    cc_svg2 = _svg_col_comb(cc_s2)

    kbit = PPA.K_CTRL
    baseline_total = (PPA.BASELINE_CROSSBAR + PPA.BASELINE_BUFFERS
                      + PPA.BASELINE_CONTROL)
    cal_bits = PPA.SPARSE_CAL_BANKS * PPA.SPARSE_CAL_DEPTH * PPA.SPARSE_CAL_EVENT_BITS
    unc = int(PPA.CALIBRATION_UNCERTAINTY * 100)
    sram_port_coeff = 0.5 * A_FLIT * GATHER_DEPTH

    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Allgather 多方案：makespan × 实现面积 Pareto</title>
<style>
:root{{--bg:#f8fafc;--card:#fff;--text:#0f172a;--muted:#64748b;--line:#cbd5e1;--win:#dcfce7;}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
margin:0;padding:28px 32px 64px;line-height:1.55;max-width:1120px}}
h1{{font-size:1.5rem;margin:0 0 4px}} h2{{font-size:1.15rem;color:#1e3a8a;margin:0 0 12px}}
.sub,.note{{color:var(--muted);font-size:.86rem}}
.card{{background:var(--card);border:1px solid #e2e8f0;border-radius:10px;padding:18px 22px;margin:16px 0}}
.hero{{border-color:#93c5fd;background:linear-gradient(180deg,#eff6ff,#fff)}}
table{{border-collapse:collapse;width:100%;font-size:.82rem;margin:8px 0}}
th,td{{border:1px solid var(--line);padding:6px 9px;text-align:center}}
th{{background:#e2e8f0}} td.l{{text-align:left}} td.win{{background:var(--win);font-weight:700}}
ul{{margin:6px 0;padding-left:22px}} li{{margin:5px 0}}
h3{{font-size:.98rem;color:#334155;margin:16px 0 6px}}
.formula{{background:#f1f5f9;border-left:3px solid #93c5fd;border-radius:6px;padding:10px 14px;
margin:8px 0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;
white-space:pre-wrap;line-height:1.5;color:#0f172a}}
code{{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:.9em}}
.trees{{display:flex;flex-wrap:wrap;gap:16px;margin:10px 0}}
.treecard{{margin:0;flex:1 1 320px;background:#fff;border:1px solid #e2e8f0;
border-radius:8px;padding:10px;text-align:center}}
.treecard figcaption{{margin-bottom:4px}}
.treecard figcaption b{{color:#1e3a8a}}
.treecard figcaption span{{display:block;font-size:.76rem;color:var(--muted)}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;font-size:.8rem;color:var(--muted);margin:6px 2px}}
.legend i{{display:inline-block;width:14px;height:8px;margin-right:5px;vertical-align:middle}}
img{{max-width:100%;border:1px solid #e2e8f0;border-radius:8px;margin:8px 0}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:12px 0}}
.kpi div{{background:#f1f5f9;border-radius:8px;padding:12px 14px}}
.kpi b{{display:block;font-size:1.3rem;color:#1d4ed8}} .kpi span{{font-size:.78rem;color:var(--muted)}}
</style></head><body>

<h1>Allgather 多方案：makespan × 芯片实现面积 Pareto</h1>
<p class="sub">8×6 mesh · H=7 · V=9 · 方案 axis+CCW / dim-XY / dim-YX / col-comb3 / NEC-3 / NEC-2 / Hamilton-bi ·
每方案扫 W/E/B eject 通路变量 · {len(pts)} 个设计点 · 含<b>多 flit（R={R}）流水 Pareto（§5）</b> ·
数据 <code>multi_area_makespan.json</code> / <code>multiflit_area_makespan.json</code> · 生成 {gen}</p>

<div class="card hero">
<h2>核心结论</h2>
<div class="kpi">
  <div><b>{lb}</b><span>makespan 下限（仅 axis 可达）</span></div>
  <div><b>[{lb}, {other_floor})</b><span>axis+CCW 独占的低时延区间</span></div>
  <div><b>{len(on_pareto)}</b><span>触及全局 Pareto 的方案数</span></div>
  <div><b>≤2%</b><span>非 axis 方案的最大面积优势</span></div>
</div>
<ul>
<li><b>低时延区（{lb}–{other_floor-1} cy）由 axis+CCW 独占</b>：其余方案的 makespan 结构性地下不去
（floor：col-comb3 114、NEC-3 118、dim-YX 120、dim-XY 126、NEC-2 145、Hamilton 210），
再大 eject 带宽/buffer 也无法进入该区。</li>
<li><b>其他方案只在中/高 makespan 段触及 Pareto，且面积优势极小</b>：低 Pmax（3–5）带来的时隙表面积节省
只有 ~0.1–0.2%（NEC-3 在 118 cy 处最多省约 2%）。因为归一化面积里时隙表/日历项本就很小
（~0.002），远小于 eject 通路（W/E/B）的 0.08–0.36。</li>
<li><b>实用结论</b>：要低 makespan 选 axis+CCW；只有在<b>无法承担宽 crossbar eject（大 W）</b>时，
才用 NEC-3 换到 ~118 cy 的更省 eject 档（省的是 W 带宽，不是时隙表面积）。</li>
<li>dim-XY/dim-YX/col-comb3 仅在最廉价角（W1/E1/B0）以 ~0.1% 面积差挂到 Pareto，工程上可忽略。</li>
</ul>
</div>

<div class="card">
<h2>1. 散点图与 Pareto 前沿</h2>
<img src="multi_area_makespan.png" alt="multi-scheme makespan vs area">
<p class="note">细线=各方案自身前沿；黑线=全局 Pareto；蓝虚线=rb=2 下限 {lb}。
每点一个 (scheme,W,E,B)。axis 曲线整体压在其他方案左下方（更低 makespan / 相近面积）。</p>
</div>

<div class="card">
<h2>2. 各方案特征与 makespan 地板</h2>
<table>
<thead><tr><th>方案</th><th>Pmax</th><th>issue</th><th>树 dilation</th>
<th>makespan 地板*</th><th>地板处面积</th><th>触及全局Pareto</th></tr></thead>
<tbody>{scheme_rows}</tbody></table>
<p class="note">* 地板 = 在最大 eject 配置（W=4,E=1,B=11 等）下可达的最低 makespan；受树结构/链路拥塞限制。
面积 = 1.0 核 + 时隙表(Pmax,issue) + CalFork 多播({data['model']['multicast_delta']}) + eject(W,E,B) 增量。</p>
</div>

<div class="card">
<h2>3. 全局 Pareto 前沿明细</h2>
<table>
<thead><tr><th>面积(归一)</th><th>makespan</th><th>方案</th><th>eject 配置 W/E/B</th></tr></thead>
<tbody>{front_rows}</tbody></table>
<ul>
<li>低 makespan 段（96–110）全部是 <b>axis+CCW</b>。</li>
<li>中段（118–124）出现 <b>NEC-3</b> 的几点：在这些 makespan 上它比同 makespan 的 axis 配置略省
（因 axis 达到相近 makespan 需要更大 W/B）。</li>
<li>最廉价角（155/197）是 <b>dim / col-comb3</b>：靠更浅时隙表省下 ~0.1% 面积，代价是大 makespan。</li>
</ul>
</div>

<div class="card">
<h2>4. 时隙表成本敏感度（λ 视图）</h2>
<p class="note">若担心低 Pmax 的真实价值（控制复杂度/时序）被本模型低估，可把时隙表(日历)面积人为放大 λ 倍，
观察谁在何 makespan 段夺走 Pareto。axis+CCW 的 Pmax=15 最深，最先受惩罚。</p>
<img src="multi_area_makespan.png" alt="lambda sensitivity (right panel)">
<table>
<thead><tr><th>λ（时隙表面积倍数）</th><th>axis+CCW 独占的 makespan</th>
<th>≤{LOW_BAND} 段的非axis占有者</th><th>各方案在 Pareto 上占有的 makespan</th></tr></thead>
<tbody>{lam_rows}</tbody></table>
<ul>
<li><b>axis+CCW 结构性锁定深时延段</b>：即便 λ=100（时隙表面积×100），axis 仍独占
makespan ≤110 的所有 Pareto 点——低 Pmax 方案<b>永远进不了</b>低时延带。</li>
<li><b>非 axis 首次拿到 ≤{LOW_BAND} 点的最小 λ = {esc(smallest.get('lambda'))}</b>
（{esc(label_of.get(smallest.get('scheme'), smallest.get('scheme')))} @ {esc(smallest.get('makespan'))} cy）：
即在<b>标称成本（λ=1）</b>下 NEC-3 就已在 118 cy 处 Pareto 最优；这属于 114–118 的“次低”段，并非深时延段。</li>
<li><b>col-comb3（114 cy）需 λ≥20 才夺点</b>：λ=1 时它虽在全局 Pareto 上，但只在高 makespan 处；
把 axis 的日历面积放大约 20× 后，axis 的 110 cy 配置面积才被 col-comb3 的 114 cy 反超。</li>
<li><b>含义</b>：只有当你认定“深时隙表”的隐性代价 ≥ 标称面积的 20 倍时，低 Pmax 才在中时延段有意义；
对深时延段（≤110）则任何 λ 都改变不了结论。</li>
</ul>
</div>

<div class="card">
<h2>5. 多 flit（R={R} 轮）流水 Pareto：为什么要组合 1-flit 与 5-flit makespan</h2>

<h3>5.1 组合指标的动机</h3>
<p>5-flit allgather 原则上是 <b>{R} 轮 1-flit 方案</b>，但第 k+1 个 flit 不必等第 k 个全部完成——只要
共享资源（链路、下 ramp 写 SRAM 带宽）空出来，就可尽早叠上去。因此有两个都重要、但侧重不同的时延：</p>
<ul>
<li><b>T1（1-flit makespan）= 流水“填充”时延</b>：第一块数据多快就绪。
tile 编程可以在<b>某个 1-flit allgather 一完成就立刻开始该 tile 的计算</b>，做细粒度 comm/compute 流水。
所以 T1 决定计算流水<b>何时能启动</b>；对<b>计算重</b>的 tile，稳态各轮被计算掩盖，T1 是唯一暴露的通信项。</li>
<li><b>T5（5-flit makespan）= 流水“吞吐”</b>：T5 = T1 + (R−1)·II。对<b>计算轻</b>的 tile，
决定总时间的是每轮初始间隔 II（吞吐），而不是单轮时延。</li>
</ul>
<p>细粒度流水每轮就绪一块、就绪即可算，因此真正该优化的是<b>各轮平均就绪时延</b>：</p>
<div class="formula">T_avg = (1/R) · Σ_{{k=0..R−1}} (T1 + k·II) = T1 + (R−1)/2 · II   → R={R} 时为 T1 + 2·II
II 的资源下界 = max( 链路复用次数 ,  ⌈(N−1)/E⌉ )
  · 一条有向链路一轮被走 r 次 → 相邻轮在该链路上至少隔 r 拍；
  · 每个 PE 每轮要把 N−1 个汇聚 flit 以 E flit/拍写入 gather SRAM → 相邻轮至少隔 ⌈(N−1)/E⌉ 拍。</div>
<p class="note">T_avg 同时惩罚高填充时延（T1）和低吞吐（大 II），且<b>与计算量无关</b>（不假设 compute 开销），
是细粒度流水下最中立的单一指标。II 是资源下界，与 W/B 无关：突发 buffer B 与 crossbar 写宽 W
只帮助<b>逼近</b>该速率并决定 T1，无法突破 II。故 T5 为理想重叠下界。</p>

<h3>5.2 Pareto 图</h3>
<img src="multiflit_area_makespan.png" alt="multi-flit pareto">
<p class="note">左：面积 × 组合指标 T_avg 的全局 Pareto；右：T1（填充）× T5（吞吐）散点——
可见<b>最优方案发生翻转</b>。</p>

<h3>5.3 各方案的填充/吞吐地板</h3>
<table>
<thead><tr><th>方案</th><th>链路复用</th><th>T1 地板（填充）</th>
<th>II 地板</th><th>T5 地板（吞吐）</th><th>T_avg 地板</th></tr></thead>
<tbody>{mf_rows}</tbody></table>
<p class="note">II 地板 = 链路复用（在 E≥2 时链路复用是瓶颈；E=1 时 ⌈47/E⌉=47 反而更大，所有方案 II=47）。
Hamilton 复用最低（24）却因 T1 太差（填充 210）而 T5 被淘汰。</p>

<h3>5.4 结论：填充冠军 ≠ 吞吐冠军</h3>
<ul>
<li><b>axis+CCW 赢“填充”</b>：T1={axis_t1}（贴下限），但它把流量高度集中，
<b>链路复用高达 {mf_meta['axis_ccw']['link_reuse']}</b> → II 卡在 42 → T5={axis_t5}。</li>
<li><b>{esc(mf_lbl[best_t5_key])} 赢“吞吐”</b>：链路复用只有 {mf_meta[best_t5_key]['link_reuse']}，
II 更小 → <b>T5={best_t5}</b>（在所有方案中最低），代价是 T1 稍大。</li>
<li>因此 T1/T5 前沿由 <b>axis+CCW（低 T1）</b> 与 <b>{esc(mf_lbl[best_t5_key])}（低 T5）</b> 两端把持；
组合指标 T_avg 的 Pareto 则由 {esc('、'.join(mf_lbl[k] for k in sorted({p['scheme'] for p in front_avg})))} 共同占据。</li>
<li><b>吞吐要靠 E（写 SRAM 带宽）和“扁平/低复用”的树</b>；填充要靠 axis 的短树 + 足够 W/B。
两者对硬件的诉求不同，这正是要用组合指标做 Pareto 的原因。</li>
</ul>

<h3>5.5 组合指标 T_avg 的全局 Pareto 明细</h3>
<table>
<thead><tr><th>面积</th><th>T_avg</th><th>T1</th><th>II</th><th>T5</th><th>方案</th><th>W/E/B</th></tr></thead>
<tbody>{mf_avg_rows}</tbody></table>

<h3>5.6 T1–T5（填充–吞吐）前沿明细</h3>
<table>
<thead><tr><th>T1</th><th>T5</th><th>II</th><th>方案</th><th>W/E/B</th></tr></thead>
<tbody>{mf_t5_rows}</tbody></table>
</div>

<div class="card">
<h2>6. 允许每 port 少量 buffer：读写控制、调度与含 port-buffer 成本的 Pareto</h2>

<h3>6.1 buffer 的写入控制（无仲裁、不会溢出）</h3>
<ul>
<li><b>每输入 port 一个专属浅 FIFO</b>（Q 项 × 512b，4 个 mesh 入口；注入口缓冲归 PE/NI 侧）。
一条链路每拍最多送达 1 flit，且只写它自己的 FIFO——<b>写口天然无争用</b>，单写口寄存器堆/SRAM + 环形写指针即可。</li>
<li><b>空间由 credit 流控保证</b>：上游每发一个 flit 先扣减该方向 credit（初值 = Q）；
flit 在下游<b>出队（整个输出 mask 服务完）</b>时经反向 sideband 归还 credit，归还延迟 ≈ 链路时延。
因此写入永不溢出，无需写仲裁或丢弃逻辑。</li>
<li><b>代价是 credit 往返（RTT）限速</b>：链路时延 L 的满速运转需要 Q ≥ 2L+1
（H 链路 ≈15、V 链路 ≈19）；小 Q 把单链路有效带宽压到约 Q/(2L)。
这是"少量 buffer"的核心物理约束，仿真中清晰可见。</li>
</ul>

<h3>6.2 buffer 的读出与调度（两种模式）</h3>
<ul>
<li><b>模式 A：日历兜底（保留时隙表）</b>——仍按刚性时隙表在预定拍读出/直通，
FIFO 只吸收 ±几拍的到达抖动（PVT、上游 stall、跨时钟域）。Q=1–2 足够，makespan 与 0-buffer
刚性排图<b>完全一致</b>；面积只增 4×Q×{A_FLIT}（Q=2 约 +0.029）。buffer 是鲁棒性保险，不参与调度。</li>
<li><b>模式 B：全动态仲裁（丢掉时隙表）</b>——HOL flit 以源 id 查<b>路由 LUT</b>（48×5bit 出口掩码），
向掩码中的输出口发请求；<b>每个输出口一个 oldest-first 仲裁器</b>；多播 fork 允许一拍同时占多个输出口
（CalFork 交叉开关），掩码全部服务完才出队并归还 credit。控制简化为「LUT + 仲裁器 + credit 计数器」，
<b>无需全网排图与对时</b>；代价是 HOL 阻塞、credit 限速与（小 Q 时的）多播死锁风险。</li>
</ul>

<h3>6.3 动态模式仿真结果（逐拍 DES，含 eject W/E/B 通路）</h3>
<table>
<thead><tr><th>方案</th><th>最大扇出</th><th>最小可行 Q</th>
<th>最优 makespan</th><th>最优配置</th><th>对应面积</th></tr></thead>
<tbody>{pb_rows}</tbody></table>
<p class="note">"最小可行 Q"以下会<b>死锁</b>：多播 flit 占住 HOL 等待全部分支 credit，跨树形成循环等待
（如 axis+CCW 在 Q≤4、col-comb3/NEC-3 在 Q≤2 的多数配置死锁）；扇出小的 Hamilton 链 Q=2 即可行。
生产实现需按 turn-model/逃逸 VC 或保守 credit 预留来消除死锁——本模型直接把死锁点判为不可行。</p>

<h3>6.4 含 port buffer 成本的 Pareto</h3>
<img src="portbuf_area_makespan.png" alt="port-buffered vs rigid pareto">
<table>
<thead><tr><th>面积(归一)</th><th>makespan</th><th>设计点</th><th>控制方式</th></tr></thead>
<tbody>{pb_front_rows}</tbody></table>
<ul>
<li><b>动态 buffered 路由进入了全局 Pareto 的两端</b>：
NEC-3 <b>Q4</b>/W1/E1/B0（151 cy，面积 ≈1.089，省掉时隙表与 eject 加宽）占据低成本段；
<b>dim-YX Q8/W1/E1/B0 以面积 ≈1.147 达到 LB=96</b>——比刚性日历的达界点
（axis+CCW W4/E1/B11 ≈1.356）<b>便宜 ~15%</b>。原因：动态路由的自节流把 eject 突发自然摊平，
不再需要宽 crossbar eject（W4）和深突发 buffer（B11），port FIFO（4×8×{A_FLIT}≈0.117）换掉了它们。</li>
<li><b>中段（106–124 cy）仍由刚性日历占优</b>：动态模式在此区间要么死锁（Q 小）要么 Q 成本高于日历方案。</li>
<li><b>面积构成（动态模式）</b>：1.0 核 + 路由 LUT（48×5bit，≈0.0004）+ 仲裁/credit 控制（0.005）
+ CalFork 多播（0.025）+ 4×Q×{A_FLIT}（port FIFO）+ eject(W,E,B) 增量；<b>无时隙表</b>。</li>
<li><b>工程含义</b>：若愿意付 Q=8 的 port buffer（~0.12 面积）并解决死锁规避，动态路由是达 LB 的最省路径，
且完全免去逐拍排图/对时的软件与验证成本；若要 96–110 cy 且面积敏感，刚性日历 axis+CCW 仍是主力。</li>
</ul>
</div>

<div class="card">
<h2>7. col-comb3 方案示意图与说明</h2>
<div class="trees">
<figure class="treecard"><figcaption><b>源在下半区 (3,1)</b>
<span>就近选下边界行 y=0 作横向 spine</span></figcaption>{cc_svg1}</figure>
<figure class="treecard"><figcaption><b>源在上半区 (5,4)</b>
<span>就近选上边界行 y=5 作横向 spine</span></figcaption>{cc_svg2}</figure>
</div>
<div class="legend">
<span><i style="background:{_C_COL}"></i>①源列纵臂</span>
<span><i style="background:{_C_SPINE}"></i>②边界行横向 spine</span>
<span><i style="background:{_C_COMB}"></i>③每列向内梳齿链</span>
<span><i style="background:{_C_SRC};border-radius:50%"></i>源节点</span>
</div>
<p><b>结构（NEC-3 的转置，三段式）</b>：</p>
<ul>
<li><b>① 源列纵臂</b>：源沿自己所在列向上、向下两个方向覆盖整列（源扇出 ≤2）。</li>
<li><b>② 边界行 spine</b>：纵臂到达<b>就近的水平边界行</b>（源在下半区选 y=0，上半区选 y={MY - 1}）后，
由 (sx, edge) 沿该行向东、向西双向展开，横穿全部 8 列。</li>
<li><b>③ 每列梳齿</b>：spine 上每个节点 (x, edge) 向网格内部拉一条单向纵链，逐格覆盖该列其余 5 个节点。</li>
</ul>
<p><b>关键性质</b>（数据取自 §2/§5 表）：</p>
<ul>
<li><b>扇出 ≤3</b>（源在边界行时=列 1 + spine 2；普通 spine 节点=spine 续 1 + 梳齿 1），
crossbar fork 需求低于 axis+CCW（扇出 4）。</li>
<li><b>Pmax=3</b>：每个 router 的非空日历模式极少（梳齿链是纯直通），时隙表最浅、控制最简单。</li>
<li><b>链路复用最低的实用方案（26）</b>：流量分散在 8 条列梳齿上，没有 axis+CCW 的十字臂热点（42）。
这直接给出全场最优吞吐：II 地板 26 → <b>T5=218、T_avg=166（双双第一，§5）</b>。</li>
<li><b>代价是填充时延</b>：T1 地板 114 > axis+CCW 的 96——每个 flit 都要先绕到边界行再进列，
路径有非最短段（树 dilation 114 > 96）。</li>
<li><b>适用场景</b>：多 flit / 细粒度流水、计算轻（吞吐主导）时的首选；单次 1-flit 延迟敏感时让位给 axis+CCW。</li>
</ul>
</div>

<div class="card">
<h2>8. 建议</h2>
<ul>
<li><b>贴下限 {lb}</b>：axis+CCW，W4/E1/B11（面积约 1.356）。唯一能到 {lb} 的方案。</li>
<li><b>性价比区间（106–121 cy）</b>：仍是 axis+CCW（W2–3/E1/B4–8）。</li>
<li><b>eject 带宽受限（只能 W≤2）且可接受 ~118 cy</b>：NEC-3（低 Pmax、窄 eject 即可）是更省 eject 的选择。</li>
<li><b>不建议</b>为了时隙表面积去选 dim/col/NEC：在本归一模型里时隙表占比 ~0.2%，节省可忽略，
而 makespan 代价显著。低 Pmax 的真正价值在<b>控制复杂度/时序收敛</b>，不在面积。</li>
<li><b>多 flit / 细粒度流水（见 §5）</b>：若 tile 走“1-flit 就绪即算”的细粒度流水且计算轻，
选 <b>{esc(mf_lbl[best_t5_key])}</b>（低链路复用、高吞吐，T5={best_t5}）并把 E 提到 ≥2；
若计算重（稳态被计算掩盖），仍选 <b>axis+CCW</b>（T1={axis_t1} 填充最快）。组合指标 T_avg 给出中间取舍。</li>
<li><b>允许每 port 少量 buffer（见 §6）</b>：Q=1–2 作日历兜底（吸收抖动，makespan 不变，+0.03 面积）；
若能付 Q=8（+0.12 面积）并做死锁规避，<b>dim-YX 动态路由以 ≈1.147 面积达 LB=96</b>，
比刚性达界点便宜 ~15%，且免去逐拍排图/对时。</li>
</ul>
</div>

<div class="card">
<h2>9. 芯片实现面积：工艺信息与评估方法</h2>

<h3>9.1 工艺信息（重要口径）</h3>
<ul>
<li><b>相对/归一面积，非绝对 mm²</b>：所有面积以<b>五端口、512-bit flit 的输入队列 XY 路由器（IQ-XY）= 1.00</b>
为基准归一。图/表里的数字是“相对该基准路由器的倍数”。</li>
<li><b>不绑定具体工艺节点</b>：本 DSE <b>刻意不假设</b>特定制程（如 x nm 库），
需求 REQ-P-004/P-005 明确将“工艺节点、绝对 PPA 目标、标定误差界”列为未指定项。
因此结论是<b>工艺无关的相对趋势</b>，跨节点稳健。</li>
<li><b>解析模型，非综合</b>：面积由“门/位宽/端口”解析式给出，<b>未跑 RTL 综合</b>（无 Yosys/DC/布局布线）。</li>
<li><b>标定锚点</b>：多播增量以 FlooNoC 路由器 delta 为锚（多播 ~+5.8%、并行归约 ~+2.7%、宽+DCA ~+16.9%），
本设计的 CalFork 精益多播实现取 <b>{PPA.CALFORK_MC_DELTA}</b>（≈+2.5%）。</li>
<li><b>flit 宽度 512 bit</b>；突发 buffer / gather SRAM 的位面积由缓冲标定推得。</li>
<li><b>映射到绝对面积</b>：把本表数值 × “目标库在选定节点下的 5 端口 512-bit IQ-XY 路由器实测面积”即可估算 mm²；
增量项带 <b>±{unc}%</b> 不确定度。</li>
</ul>

<h3>9.2 基准分解（IQ-XY = 1.00）</h3>
<div class="formula">crossbar {PPA.BASELINE_CROSSBAR} + buffers {PPA.BASELINE_BUFFERS} + control {PPA.BASELINE_CONTROL} = {baseline_total:.2f}
每比特控制 SRAM 面积系数 K = 稠密日历锚点 0.040 /(2×1024×13 bit) = {kbit:.3e}
SparseCal 时隙表 = {PPA.SPARSE_CAL_BANKS}×{PPA.SPARSE_CAL_DEPTH}×{PPA.SPARSE_CAL_EVENT_BITS} = {cal_bits} bit（0.009 量级）</div>

<h3>9.3 本报告的总面积构成</h3>
<div class="formula">总面积 = 1.00（IQ-XY 核）
        + 时隙表(Pmax)   = 2 bank × 2^⌈log2 Pmax⌉ × {PPA.SPARSE_CAL_EVENT_BITS} bit × issue × K
        + 多播 CalFork    = {PPA.CALFORK_MC_DELTA}（扇出&gt;1 固定一份）
        + eject 通路增量  = Δcrossbar + Δbuffer + Δsram

Δcrossbar(W) = {CROSSBAR_PORT} × (W−1)          （下ramp 多 W−1 个 eject 输出列；{CROSSBAR_PORT}=crossbar 0.380/5）
Δbuffer(W,E,B) = {A_FLIT} × B × (W+E)/2        （深 B、512b；写口 W+读口 E 的多端口因子；{A_FLIT}=每 512b flit 1W1R 位面积）
Δsram(E)   = {sram_port_coeff:.4f} × (E−1)          （gather SRAM {GATHER_DEPTH} flit；每多一写口 +50% 单口阵列面积）</div>

<h3>9.4 方法覆盖与边界</h3>
<ul>
<li><b>已建模</b>：crossbar 交叉点/端口数、VC/突发 buffer 的 flit 位数与端口数、时隙表深度×位宽×bank、
多播掩码扩展、gather SRAM 多写口；动态 buffered 模式的 port FIFO（4×Q×flit 位面积）、
路由 LUT 与仲裁/credit 控制增量（见 §6.4）。</li>
<li><b>未建模</b>：布线/布局面积、漏电/功耗随节点变化、时序收敛与频率、SRAM 编译器台阶效应（bitcell 量子化）、
Pmax 带来的<b>控制复杂度</b>（本模型仅记其微小 SRAM 面积，不记控制逻辑/验证成本）。</li>
<li><b>不确定度</b>：增量项统一 ±{unc}%；因此 &lt;2% 的方案间面积差在本模型内不具判别力（见第 3 节结论）。</li>
</ul>
</div>

<p class="note">生成脚本：<code>utils/gen_multi_area_report.py</code> ·
DSE/绘图：<code>utils/dse_multi_area_makespan.py</code>、<code>utils/dse_multiflit_area_makespan.py</code>、
<code>utils/dse_portbuf_area_makespan.py</code>（复用
<code>dse_burst_sweep_8x6.py</code>、<code>dse_axis_area_makespan.py</code>、<code>ppa_analytic_model.py</code>）</p>
</body></html>"""
    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
