#!/usr/bin/env python3
"""Plot makespan vs border AFIFO depth cap from border_afifo_depth_sweep.json."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "border_afifo_depth_sweep.json"
ROUTER_JSON_PATH = ROOT / "results" / "router_afifo_depth_sweep.json"
BAL_JSON_PATH = ROOT / "results" / "border_bal_afifo_depth_sweep.json"
RAMP4_JSON_PATH = ROOT / "results" / "ramp4_afifo_depth_sweep.json"
SIZE_JSON_PATH = ROOT / "results" / "msg_size_sweep.json"
HTML_PATH = ROOT / "results" / "report_afifo_depth_curve.html"

ROUTER_CAPS = (0, 1, 2, 3, 4)
ROUTER_COLORS = ["#94a3b8", "#3b82f6", "#059669", "#f59e0b", "#dc2626"]

SERIES = [
    ("4x4_uni", "4×4 单向", "#3b82f6"),
    ("4x4_bi", "4×4 双向", "#60a5fa"),
    ("8x8_uni", "8×8 单向", "#059669"),
    ("8x8_bi", "8×8 双向", "#34d399"),
    ("16x16_uni", "16×16 单向", "#dc2626"),
    ("16x16_bi", "16×16 双向", "#f87171"),
]


def load():
    if not JSON_PATH.exists():
        import sweep_afifo_depth as sw
        return sw.run()
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def line_chart(caps, series_data, title, width=720, height=380,
               xlabel="边界 AFIFO 深度上限（per-link peak）"):
    margin_l, margin_r, margin_t, margin_b = 58, 24, 36, 52
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b
    all_mk = [p["makespan"] for _, _, pts in series_data for p in pts if p.get("makespan")]
    ymax = max(all_mk) * 1.08 if all_mk else 100
    xmin, xmax = min(caps), max(caps)
    xspan = xmax - xmin or 1

    def xy(cap, mk):
        x = margin_l + (cap - xmin) / xspan * plot_w
        y = margin_t + plot_h - (mk / ymax) * plot_h
        return x, y

    parts = [
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">',
        f'<text x="{margin_l}" y="22" font-size="14" font-weight="bold">{html.escape(title)}</text>',
        f'<line x1="{margin_l}" y1="{margin_t+plot_h}" x2="{margin_l+plot_w}" y2="{margin_t+plot_h}" stroke="#64748b"/>',
        f'<line x1="{margin_l}" y1="{margin_t}" x2="{margin_l}" y2="{margin_t+plot_h}" stroke="#64748b"/>',
    ]
    # Y ticks
    for i in range(6):
        v = ymax * i / 5
        y = margin_t + plot_h - v / ymax * plot_h
        parts.append(f'<line x1="{margin_l-4}" y1="{y:.1f}" x2="{margin_l+plot_w}" y2="{y:.1f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{margin_l-8}" y="{y+4:.1f}" font-size="10" text-anchor="end">{int(v)}</text>')
    # X ticks
    for cap in caps:
        x = margin_l + (cap - xmin) / xspan * plot_w
        parts.append(f'<line x1="{x:.1f}" y1="{margin_t+plot_h}" x2="{x:.1f}" y2="{margin_t+plot_h+4}" stroke="#64748b"/>')
        parts.append(f'<text x="{x:.1f}" y="{margin_t+plot_h+18}" font-size="10" text-anchor="middle">{cap}</text>')
    parts.append(f'<text x="{margin_l+plot_w/2:.1f}" y="{height-8}" font-size="11" text-anchor="middle">{html.escape(xlabel)}</text>')
    parts.append(f'<text x="14" y="{margin_t+plot_h/2:.1f}" font-size="11" text-anchor="middle" transform="rotate(-90 14 {margin_t+plot_h/2:.1f})">makespan (cy)</text>')

    lx = margin_l + 8
    for label, color, pts in series_data:
        coords = []
        for p in pts:
            if p.get("makespan") is None:
                continue
            coords.append(xy(p["cap"], p["makespan"]))
        if len(coords) < 2:
            continue
        d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for x, y in coords:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
        parts.append(f'<rect x="{lx:.0f}" y="{margin_t+4:.0f}" width="14" height="3" fill="{color}"/>')
        parts.append(f'<text x="{lx+18:.0f}" y="{margin_t+10:.0f}" font-size="10">{html.escape(label)}</text>')
        lx += 100

    parts.append("</svg>")
    return "\n".join(parts)


def table_rows(data):
    caps = data["caps"]
    rows = []
    for key, label, _ in SERIES:
        cfg = data["configs"].get(key)
        if not cfg:
            continue
        by_cap = {p["cap"]: p for p in cfg["points"]}
        cells = "".join(
            f"<td>{by_cap[c]['makespan'] if by_cap.get(c, {}).get('makespan') else '—'}</td>"
            for c in caps
        )
        rows.append(f"<tr><td class='l'>{html.escape(label)}</td>{cells}</tr>")
    hdr = "".join(f"<th>{c}</th>" for c in caps)
    return f"<table><tr><th>配置</th>{hdr}</tr>{''.join(rows)}</table>"


def cliff_note_16x16_bi(data):
    """Explain the 16×16 bi makespan cliff (atomic plateau -> spread=0) using the
    actual sweep numbers, so the prose stays correct when data is regenerated."""
    cfg = data.get("configs", {}).get("16x16_bi", {})
    pts = [p for p in cfg.get("points", []) if p.get("makespan") is not None]
    by_cap = {p["cap"]: p for p in pts}
    if not pts:
        return ""
    mkmin = min(p["makespan"] for p in pts)
    # first cap that reaches the minimum (the observed cliff), and the cap just
    # before it (the top of the plateau)
    thr_cap = next(p["cap"] for p in pts if p["makespan"] == mkmin)
    plateau_pts = [p for p in pts if p["cap"] < thr_cap]
    plateau = plateau_pts[-1] if plateau_pts else pts[0]
    mk_plat = plateau["makespan"]
    d_plat = (plateau.get("detail") or {}).get("afifo_depth", "?")
    prev_cap = plateau["cap"]
    dmin = by_cap[thr_cap].get("detail") or {}
    depth = dmin.get("afifo_depth", "?")
    bal = dmin.get("afifo_balanced", "?")
    n = cfg.get("n", 256)
    ramp = cfg.get("ramp_bw", 2)
    elb = cfg.get("eject_lb", (n - 1 + ramp - 1) // ramp)
    drop = f"{(mk_plat - mkmin) / mk_plat * 100:.0f}%" if isinstance(mk_plat, int) else "?"
    ratio = f"{mkmin / elb:.2f}×" if isinstance(mkmin, int) and isinstance(elb, int) and elb else "—"
    return f"""
<div class='card'><h2>案例：16×16 双向为何在深 AFIFO 后 makespan 骤降至 {mkmin}？</h2>
<p>表中可见：cap≤{prev_cap} 时 makespan 稳定在 <b>{mk_plat} cy</b>（AFIFO 仅需 {d_plat}）；
cap≥{thr_cap} 时降至 <b>{mkmin} cy</b>（约 −{drop}）。本 sweep 在 {prev_cap} 与 {thr_cap} 之间未采样，
故断崖<strong>首次出现在 cap={thr_cap}</strong>——真实门槛是下面这套 <code>spread=0</code> 调度的 AFIFO 峰值深度 <b>{depth}</b>。</p>

<h3>1. 为什么会下降？——两种调度范式切换</h3>
<p>cap≤{prev_cap} 时的最优方案来自 <code>schedule_atomic</code>（相位错开 / pacing）：</p>
<ul>
<li>每个源作为整体原子放置；若某次跨界会使任意边界 AFIFO 在任意 cycle 超过 cap，就<strong>整体推迟该源的注入</strong>。</li>
<li>在 AFIFO 预算很小（深度≤{d_plat}）时，只能大量「等」在边界，注入偏移累积 → makespan ≈{mk_plat} cy，但 AFIFO 峰值仅 {d_plat}。</li>
<li>这是<strong>保守、低缓冲</strong>策略：用更长时间换更浅的 AFIFO。</li>
</ul>
<p>cap≥{depth} 时，另一类方案变得可行：<code>schedule</code> 的 <strong>spread=0</strong>（环上链路时分插入）：</p>
<ul>
<li>四象限 Hamilton 环先按固有节拍跑（Pass1 本象限 home 子树）；跨界 flit 插入环内链路空闲 send 槽（Pass2），允许在 AFIFO 中<strong>短暂排队</strong>。</li>
<li>不强制 per-source 原子 pacing，跨界 burst 与环内 conveyer 更自然对齐 → makespan <b>{mkmin} cy</b>（约 −{drop}）。</li>
<li>代价：单链路 AFIFO 峰值约 <b>{depth}</b>（8 路并行均衡后约 {bal}），必须用更深的边界 FIFO 承受等待。</li>
</ul>
<p><b>结论：</b>下降不是「缓冲越深传输越快」的连续渐变，而是<strong>低深度下只能用 atomic 保守调度；深度够深后 spread=0 的环时分调度才合法并显著更优</strong>。</p>

<h3>2. 为什么门槛在 {depth}（而不是 {prev_cap} 或 {d_plat}）？</h3>
<p>对每个候选调度，我们要求 <code>afifo_depth ≤ cap</code>（单链路峰值）。</p>
<ul>
<li><code>spread=0</code> 方案的实测峰值 AFIFO = <b>{depth}</b>。</li>
<li>cap&lt;{depth} 时该方案<strong>不被允许</strong>，搜索只能在 atomic 等低深度方案里选 → 仍约 {mk_plat} cy。</li>
<li>cap≥{depth} 时 <code>spread=0</code> 首次入选候选集，{mkmin} &lt; {mk_plat}，曲线<strong>断崖式</strong>下探（本 sweep 首个满足的采样点为 cap={thr_cap}）。</li>
</ul>
<p>cap={d_plat} 时虽也允许「深度≤{d_plat}」，但 spread=0（峰值 {depth}）仍不可行；atomic 在 cap={d_plat} 已到其自身最优 {mk_plat}，故 cap={d_plat}~{prev_cap} 平台不变。</p>

<h3>3. 与 eject 下界的关系</h3>
<p>16×16 双向 eject 下界 = ⌈(N−1)/{ramp}⌉ = <b>{elb} cy</b>。{mkmin} cy 约为下界的 {ratio}，仍远高于下界——瓶颈在<strong>跨界+环内链路时分</strong>与 AFIFO 等待，而非下 ramp 带宽。</p>

<p class='note'>cap≤{prev_cap} 最优：atomic/natural，mk={mk_plat}，AFIFO峰值={d_plat}。
cap≥{thr_cap} 最优：schedule spread=0，mk={mkmin}，AFIFO峰值={depth}，均衡深度≈{bal}。</p>
</div>"""


def load_router():
    if not ROUTER_JSON_PATH.exists():
        return None
    return json.loads(ROUTER_JSON_PATH.read_text(encoding="utf-8"))


def router_curves_for_key(rdata, key, title):
    """Multi-line chart: each router_cap K is a series over afifo_caps."""
    cfg = rdata["configs"].get(key, {})
    afifo_caps = rdata["afifo_caps"]
    grid = cfg.get("grid", {})
    series = []
    for k in rdata["router_caps"]:
        row = grid.get(str(k), grid.get(k, []))
        by_a = {p["afifo_cap"]: p for p in row}
        pts = [{"cap": a, "makespan": by_a.get(a, {}).get("makespan")} for a in afifo_caps]
        color = ROUTER_COLORS[k] if k < len(ROUTER_COLORS) else "#64748b"
        series.append((f"router K={k}", color, pts))
    return line_chart(afifo_caps, series, title)


def router_heatmap_table(rdata, key):
    afifo_caps = rdata["afifo_caps"]
    grid = rdata["configs"][key]["grid"]
    hdr = "".join(f"<th>A={a}</th>" for a in afifo_caps)
    rows = []
    for k in rdata["router_caps"]:
        row = grid.get(str(k), grid.get(k, []))
        by_a = {p["afifo_cap"]: p for p in row}
        cells = "".join(
            f"<td>{by_a[a]['makespan'] if by_a.get(a, {}).get('makespan') else '—'}</td>"
            for a in afifo_caps
        )
        rows.append(f"<tr><td class='l'>K={k}</td>{cells}</tr>")
    return f"<table><tr><th>router↓ AFIFO→</th>{hdr}</tr>{''.join(rows)}</table>"


def router_buffer_section(rdata):
    if not rdata:
        return ""
    u16 = rdata["configs"].get("16x16_uni", {})
    b16 = rdata["configs"].get("16x16_bi", {})
    grid_u = u16.get("grid", {})
    grid_b = b16.get("grid", {})
    # pipelined peaks from K=1 row at max A (same mk as best pipelined if feasible)
    def pip_info(grid):
        for k in rdata["router_caps"]:
            for p in grid.get(str(k), grid.get(k, [])):
                d = p.get("detail") or {}
                if d.get("method") == "pipelined":
                    return d
        return {}

    pu = rdata["configs"].get("16x16_uni", {}).get("pipelined") or pip_info(grid_u)
    pb = rdata["configs"].get("16x16_bi", {}).get("pipelined") or pip_info(grid_b)
    af_caps = rdata["afifo_caps"]
    a_hi = af_caps[-1]
    def mk_at(grid, k, a):
        row = grid.get(str(k), grid.get(k, []))
        for p in row:
            if p["afifo_cap"] == a:
                return p.get("makespan")
        return "—"

    mk_u0 = mk_at(grid_u, 0, a_hi)
    mk_u3 = mk_at(grid_u, 3, a_hi)
    mk_b0 = mk_at(grid_b, 0, a_hi)
    mk_b4 = mk_at(grid_b, 4, a_hi)

    small_rows = []
    for key, label, _ in SERIES:
        g = rdata["configs"][key]["grid"]
        cells = "".join(
            f"<td>{mk_at(g, k, a_hi)}</td>" for k in rdata["router_caps"]
        )
        small_rows.append(f"<tr><td class='l'>{html.escape(label)}</td>{cells}</tr>")
    k_hdr = "".join(f"<th>K={k}</th>" for k in rdata["router_caps"])
    small_tbl = (
        f"<table><tr><th>配置 (A={a_hi})</th>{k_hdr}</tr>{''.join(small_rows)}</table>"
    )

    return f"""
<div class='card'><h2>Router 每 port 缓冲 K × 边界 AFIFO 深度 A</h2>
<p class='note'>更新：{html.escape(rdata.get('updated', ''))} ·
<code>results/router_afifo_depth_sweep.json</code> · 生成 <code>sweep_router_afifo_depth.py</code></p>
<p>在现有「router 零 buffer（K=0）」曲线基础上，允许<strong>每个 router 输出 port / 下 ramp port</strong>
峰值排队深度 ≤ <b>K</b>（flit），同时边界 AFIFO 单链路峰值 ≤ <b>A</b>。
候选调度：strict（spread/atomic，K=0 时与上图相同）+ <strong>pipelined TDM</strong>（环内链路可短暂排队）。</p>

<h3>机制要点</h3>
<ul>
<li><b>K=0</b>：与上文「router 零 buffer」完全相同；等待只能发生在边界 AFIFO（或源 PE 注入偏移）。</li>
<li><b>K≥1</b>：环内 Hamilton 链路可「就绪但链路忙」时在 router 输出 port 排队；下 ramp 同理（<code>eject_buf</code>）。
pipelined 典型需求 <code>ring_buf≈1</code>、<code>eject_buf≈2~3</code> → 往往需 <b>K≥2</b>（16×16 单向 pipelined 需 <b>K≥3</b>）。</li>
<li><b>AFIFO 与 router buffer 解耦</b>：跨界等待仍在 AFIFO；加深 K 主要缓解<strong>象限内环</strong>与<strong>eject 对齐</strong>，不能替代深 AFIFO 对 spread=0 大跨界调度的需求。</li>
</ul>

<h3>各尺寸 @ AFIFO={a_hi}：makespan vs router cap K</h3>
{small_tbl}
<p class='note'>4×4 单向 K≥2：37→36；8×8 单向 K≥2：117→114；16×16 单向 K≥3：651→366；
16×16 双向 K≤4 与 K=0 相同（pipelined 需 ring_buf≈{pb.get('ring_buf','?')}）。</p>

<h3>16×16 单向：K≥3 带来最大收益</h3>
<p>K=0 时 A={a_hi} 最优 <b>{mk_u0} cy</b>（atomic）；K≥3 时 pipelined <b>{mk_u3} cy</b>
（ring_buf={pu.get('ring_buf','?')}, eject_buf={pu.get('eject_buf','?')}, AFIFO={pu.get('afifo_depth','?')}）——
<strong>约 −44%</strong>。K=1~2 仍不足以容纳 pipelined 的 eject 排队。</p>
{router_curves_for_key(rdata, "16x16_uni", "16×16 单向：makespan vs AFIFO（每条线 = router cap K）")}

<h3>16×16 双向：K≤4 几乎不改变平台</h3>
<p>pipelined 需 <code>ring_buf≈{pb.get('ring_buf','?')}</code>（远超 K=4），故 K=1~4 与 K=0 重合：
A={a_hi} 时 <b>{mk_b4} cy</b>（spread=0）；浅 router FIFO 无法替代 AFIFO≈46 的断崖。</p>
{router_curves_for_key(rdata, "16x16_bi", "16×16 双向：makespan vs AFIFO（每条线 = router cap K）")}

<h3>16×16 双向 · K×A 数值表（makespan）</h3>
{router_heatmap_table(rdata, "16x16_bi")}
</div>"""


def load_bal():
    if not BAL_JSON_PATH.exists():
        return None
    return json.loads(BAL_JSON_PATH.read_text(encoding="utf-8"))


def balanced_diag_section(data, bdata):
    """Compare single-path (all diagonal via QH) vs balanced diagonal (half via
    QH, half via QV)."""
    if not bdata:
        return ""

    def best(pts):
        feas = [p for p in pts if p.get("makespan")]
        bm = min(p["makespan"] for p in feas)
        for p in feas:
            if p["makespan"] == bm:
                return bm, p["cap"], (p["detail"] or {}).get("afifo_depth")
        return bm, None, None

    def at(pts, cap):
        for p in pts:
            if p["cap"] == cap:
                return p["makespan"], (p["detail"] or {}).get("afifo_depth")
        return None, None

    rows = []
    for key, label, _ in SERIES:
        ob = data["configs"].get(key, {}).get("points")
        bb = bdata["configs"].get(key, {}).get("points")
        if not ob or not bb:
            continue
        o5_mk, o5_d = at(ob, 5)
        b5_mk, b5_d = at(bb, 5)
        obest = best(ob)
        bbest = best(bb)
        rows.append(
            f"<tr><td class='l'>{html.escape(label)}</td>"
            f"<td>{o5_mk} / {o5_d}</td><td>{b5_mk} / {b5_d}</td>"
            f"<td>{obest[0]} @{obest[1]}/{obest[2]}</td>"
            f"<td>{bbest[0]} @{bbest[1]}/{bbest[2]}</td></tr>"
        )
    tbl = (
        "<table><tr><th>配置</th>"
        "<th>原始 mk/afifo @cap5</th><th>均衡 mk/afifo @cap5</th>"
        "<th>原始 最优 mk@cap/afifo</th><th>均衡 最优 mk@cap/afifo</th></tr>"
        f"{''.join(rows)}</table>"
    )

    # overlay curve for 16x16 bi (most affected)
    caps = data["caps"]
    ob = data["configs"]["16x16_bi"]["points"]
    bb = bdata["configs"]["16x16_bi"]["points"]
    overlay = [
        ("原始 单路径(全经 QH)", "#dc2626", ob),
        ("均衡 (半 QH / 半 QV)", "#2563eb", bb),
    ]
    chart = line_chart(caps, overlay, "16×16 双向：原始 vs 对角均衡（makespan vs AFIFO 上限）")

    return f"""
<div class='card'><h2>对角象限均衡：半经水平邻居、半经垂直邻居</h2>
<p class='note'>更新：{html.escape(bdata.get('updated', ''))} ·
<code>results/border_bal_afifo_depth_sweep.json</code> · <code>sched_ring_zerobuf.deliv_border_bal_quads</code></p>

<h3>路由改动</h3>
<p>原始 border 短弧把<strong>整个对角象限</strong>（QD，如左上源的 64 个目的点）全部经
<strong>水平相邻象限 QH</strong> 投递（QD 每一列都从中线 y-border 进入）。
均衡方案把 QD 的 64 个目的点<strong>对半拆分</strong>：</p>
<ul>
<li><b>上半 32 个</b>（QD 上半行）经 <strong>QH</strong>：从中线 y-border 进入，沿列向下短弧。</li>
<li><b>下半 32 个</b>（QD 下半行）经 <strong>垂直相邻象限 QV</strong>：沿 QV 列向下后跨中线 x-border 进入 QD，沿行横向短弧。</li>
</ul>
<p>这样原本只承载 QH 流量的中线 x-border 也分担一半对角流量，两条中线边界负载更对称。</p>

<h3>AFIFO 深度 vs makespan 的变化</h3>
{tbl}
<p class='note'>表中「mk/afifo」= 该 AFIFO 上限下最优 makespan 与对应单链路 AFIFO 峰值；
「最优」列为全 cap 扫描中的最小 makespan 及其首次达到的 cap/afifo 峰值。</p>

<h3>结论</h3>
<ul>
<li><b>AFIFO 峰值下降</b>：在浅缓冲（atomic）区间，均衡把单链路 AFIFO 峰值显著降低。
16×16 双向 cap=5 时：原始需 afifo=<b>5</b>（mk=404），均衡只需 afifo=<b>2</b>（mk=423）——峰值 −60%。
8×8 单向 cap≥2：3→2；16×16 双向 cap=3~4：3→2。</li>
<li><b>makespan 略升</b>：对角下半经 QV 要多走垂直象限的整列短弧，关键路径变长，双向配置约 +5%
（16×16 双向最优 244→267，8×8 双向 89→93）。跨界时延=10cy 放大了这一代价。</li>
<li><b>本质是权衡</b>：把对角流量摊到两条中线边界，<strong>用更浅的 AFIFO（更小的边界 FIFO 面积）换取约 5% 的 makespan</strong>。
若 AFIFO 深度是受限/昂贵资源（如 GALS 边界异步 FIFO），均衡方案更优；若追求最短 makespan 且 FIFO 充足，原始单路径更快。</li>
</ul>

<h3>16×16 双向曲线对比</h3>
<div>{chart}</div>
<p class='note'>低 cap 段两者接近（均衡略慢但 AFIFO 更浅）；高 cap 段原始 spread=0 在 cap≈45 探到 244，
均衡在 cap≈40 探到 267（更早达平台但平台更高）。</p>
</div>"""


def load_ramp4():
    if not RAMP4_JSON_PATH.exists():
        return None
    return json.loads(RAMP4_JSON_PATH.read_text(encoding="utf-8"))


def load_size():
    if not SIZE_JSON_PATH.exists():
        return None
    return json.loads(SIZE_JSON_PATH.read_text(encoding="utf-8"))


def _best_point(points):
    feas = [p for p in points if p.get("makespan") is not None]
    if not feas:
        return None, None, None
    pmin = min(feas, key=lambda p: p["makespan"])
    return pmin["makespan"], pmin["cap"], (pmin.get("detail") or {}).get("afifo_depth")


def ramp4_section(data, r4data):
    """New 下 ramp=4 curve: makespan vs AFIFO depth at eject bandwidth 4,
    plus a comparison against each configuration's native down-ramp (uni=1,
    bi=2)."""
    if not r4data:
        return ""
    caps = r4data["caps"]
    bi_only, uni_only = [], []
    for key, label, color in SERIES:
        cfg = r4data["configs"].get(key)
        if not cfg:
            continue
        pts = cfg["points"]
        (bi_only if key.endswith("_bi") else uni_only).append((label, color, pts))

    # comparison table: native-ramp best vs ramp=4 best
    rows = []
    for key, label, _ in SERIES:
        nc = data["configs"].get(key)
        rc = r4data["configs"].get(key)
        if not nc or not rc:
            continue
        native_ramp = nc.get("ramp_bw", 2 if key.endswith("_bi") else 1)
        n = nc.get("n", "?")
        nmk, ncap, _ = _best_point(nc["points"])
        rmk, rcap, _ = _best_point(rc["points"])
        lb4 = rc.get("eject_lb", "?")
        ratio = f"{rmk/lb4:.2f}×" if isinstance(rmk, int) and isinstance(lb4, int) and lb4 else "—"
        drop = f"−{(nmk-rmk)/nmk*100:.0f}%" if isinstance(nmk, int) and isinstance(rmk, int) and nmk else "—"
        rows.append(
            f"<tr><td class='l'>{html.escape(label)}</td><td>{n}</td>"
            f"<td>{native_ramp}</td><td>{nmk} @cap{ncap}</td>"
            f"<td>{rmk} @cap{rcap}</td><td>{lb4}</td><td>{ratio}</td><td>{drop}</td></tr>"
        )
    tbl = (
        "<table><tr><th>配置</th><th>N</th><th>原下 ramp</th>"
        "<th>原最优 mk</th><th>ramp=4 最优 mk</th><th>ramp=4 eject 下界</th>"
        "<th>mk/下界</th><th>对比原 ramp</th></tr>"
        f"{''.join(rows)}</table>"
    )

    return f"""
<div class='card'><h2>下 ramp = 4 flit/cycle/node</h2>
<p class='note'>更新：{html.escape(r4data.get('updated', ''))} ·
<code>results/ramp4_afifo_depth_sweep.json</code> · 生成 <code>sweep_ramp4_size.py --only ramp4</code></p>
<p>把每节点 eject（下 ramp）带宽提到 <b>4 flit/cy</b>（其余模型不变：router 零 buffer、跨界 AFIFO=10cy、环形状同上）。
下 ramp 是<strong>每节点每周期能落地的 flit 数</strong>，与环方向无关，故单/双向环都给出 ramp=4 曲线。
eject 下界 = ⌈(N−1)/4⌉，比 ramp=1/2 更低，注入相位更易错开。</p>

<h3>双向环 @ 下 ramp=4</h3>
{line_chart(caps, bi_only, "makespan vs 边界 AFIFO 深度上限（双向, ramp=4）")}
<h3>单向环 @ 下 ramp=4</h3>
{line_chart(caps, uni_only, "makespan vs 边界 AFIFO 深度上限（单向, ramp=4）")}

<h3>数值表（每格 = 该 AFIFO 上限下最小 makespan, ramp=4）</h3>
{table_rows(r4data)}

<h3>与原下 ramp 对比（最优 makespan）</h3>
{tbl}
<p class='note'>下 ramp 加宽主要帮助 <strong>eject 受限</strong> 的配置：单向环（原 ramp=1，eject 下界最大）受益最明显；
小尺寸或已被「跨界+环内链路时分」限制的配置，下 ramp 从 2→4 收益有限。</p>
</div>"""


def size_section(sdata):
    """Makespan vs message (data) size m=1..5 flit. Wormhole, 0 router buffer:
    a message occupies m consecutive cycles on every link and m eject cycles."""
    if not sdata:
        return ""
    ms = sdata["msg_sizes"]
    cap = sdata.get("cap", 48)
    cross = sdata.get("cross_lat", 6)
    ramps = sdata.get("ramps", (1, 2))

    def pts_for(key, ramp):
        cfg = sdata["configs"].get(key, {})
        row = cfg.get("by_ramp", {}).get(str(ramp))
        if not row:
            return None
        return [{"cap": m, "makespan": mk} for m, mk in zip(ms, row)]

    def native_ramp(key):
        return 2 if key.endswith("_bi") else 1

    # chart 1: all configs at native down-ramp (uni=1, bi=2)
    series_nat = []
    for key, label, color in SERIES:
        p = pts_for(key, native_ramp(key))
        if p:
            series_nat.append((label, color, p))
    chart_nat = line_chart(ms, series_nat,
                           "makespan vs 数据大小（各配置原生下 ramp：单向=1，双向=2）",
                           xlabel="数据大小 m (flit/message)")

    # table: configs × m at native ramp + eject LB
    rows = []
    for key, label, _ in SERIES:
        cfg = sdata["configs"].get(key)
        if not cfg:
            continue
        rb = native_ramp(key)
        row = cfg.get("by_ramp", {}).get(str(rb), [])
        lb = cfg.get("eject_lb", {}).get(str(rb), [])
        cells = "".join(f"<td>{mk}</td>" for mk in row)
        lbcell = "/".join(str(x) for x in lb)
        rows.append(
            f"<tr><td class='l'>{html.escape(label)} (ramp={rb})</td>{cells}<td>{lbcell}</td></tr>"
        )
    hdr = "".join(f"<th>m={m}</th>" for m in ms)
    tbl = (f"<table><tr><th>配置</th>{hdr}<th>eject 下界 m=1..5</th></tr>"
           f"{''.join(rows)}</table>")

    # chart 2: representative configs, ramp 1/2 overlay
    overlay_blocks = []
    for rep_key, rep_label in (("16x16_bi", "16×16 双向"), ("8x8_bi", "8×8 双向")):
        ramp_colors = {1: "#dc2626", 2: "#f59e0b"}
        ser = []
        for rb in ramps:
            p = pts_for(rep_key, rb)
            if p:
                ser.append((f"下 ramp={rb}", ramp_colors.get(rb, "#64748b"), p))
        if ser:
            overlay_blocks.append(
                f"<h3>{rep_label}：makespan vs 数据大小（下 ramp=1/2）</h3>"
                + line_chart(ms, ser, f"{rep_label}：下 ramp 吸收报文长度",
                             xlabel="数据大小 m (flit/message)")
            )

    return f"""
<div class='card'><h2>数据大小（每报文 flit 数）vs makespan（边界 AFIFO ≤ {cap} flit）</h2>
<p class='note'>更新：{html.escape(sdata.get('updated', ''))} ·
<code>results/msg_size_sweep.json</code> · 生成 <code>sweep_ramp4_size.py --only size</code></p>
<p>把每个 src→dst 投递从 1 flit 改为 <b>m flit</b> 的 wormhole 报文（router 零 buffer：
报文在每条链路占 <b>m</b> 个连续周期，下 ramp 每周期至多吞吐 ramp_bw 个 flit）。
跨界 AFIFO link = <b>{cross} cy</b>（H=4, V=6）。<strong>约束边界 AFIFO 深度 ≤ {cap} flit</strong>（按 flit 精确计）。
下 ramp 带宽取 <b>1、2 flit/cycle/node</b>（单向原生=1，双向原生=2）。</p>

<h3>所有配置 @ 原生下 ramp</h3>
{chart_nat}
<h3>数值表</h3>
{tbl}
<p class='note'>eject 下界 = ⌈(N−1)·m / ramp⌉。</p>

{''.join(overlay_blocks)}
{size_note(sdata, ms, cap)}
</div>"""


def size_note(sdata, ms, cap):
    """Data-driven analysis of the AFIFO<=cap makespan-vs-size curves."""
    def val(key, ramp, m):
        cfg = sdata["configs"].get(key, {})
        row = cfg.get("by_ramp", {}).get(str(ramp))
        if not row or m not in ms:
            return None
        return row[ms.index(m)]

    def lb(key, ramp, m):
        cfg = sdata["configs"].get(key, {})
        row = cfg.get("eject_lb", {}).get(str(ramp))
        if not row or m not in ms:
            return None
        return row[ms.index(m)]

    mmax = ms[-1]
    cross = sdata.get("cross_lat", 6)
    # eject-limited gain (uni big ring, ramp1 -> ramp2), m=1
    u_lo, u_hi = val("16x16_uni", 1, 1), val("16x16_uni", 2, 1)
    u_pct = f"−{round((1 - u_hi / u_lo) * 100)}%" if (u_lo and u_hi) else ""
    # AFIFO-pacing blow-up at large m (compare to eject lower bound)
    bk = "16x16_bi"
    b_mk = val(bk, 2, mmax)
    b_lb = lb(bk, 2, mmax)
    b_ratio = f"{b_mk / b_lb:.1f}×" if (b_mk and b_lb) else ""
    # ramp 1 vs 2 at largest m on 16x16 bi
    r1, r2 = val(bk, 1, mmax), val(bk, 2, mmax)
    # growth of makespan with m at native ramp=2
    g1, g5 = val(bk, 2, 1), val(bk, 2, mmax)
    g_factor = f"{g5 / g1:.1f}×" if (g1 and g5) else ""

    return f"""<p class='note'>关键规律（跨界 AFIFO={cross} cy，边界 AFIFO ≤ {cap} flit，下 ramp=1/2）：</p>
<ul class='note'>
<li><b>报文越大 makespan 增长越快（且超线性）</b>：除了「每链路串行 m flit + 每节点 eject (N−1)·m flit」两项随 m 线性增长外，
<strong>AFIFO 只有 {cap} 槽</strong>——一个 m-flit 报文在边界最多占 min(m, 等待) 个槽，m 越大能并发等待的报文越少，
被迫拉大注入间隔（atomic pacing）。16×16 双向 @ramp2：m=1→m={mmax} 的 makespan 增长约 <b>{g_factor}</b>（{g1} → {g5}），明显快于 {mmax}×。</li>
<li><b>下 ramp 的收益主要在 eject 受限区</b>：低 ramp、单向大环时 makespan≈eject 时间，加宽下 ramp 近似按倍数缩短——
16×16 单向 m=1 从 ramp1 的 <b>{u_lo}</b> 降到 ramp2 的 <b>{u_hi}</b>（{u_pct}）。</li>
<li><b>大报文下瓶颈是链路 TDM + AFIFO pacing，不再是 eject</b>：16×16 双向 m={mmax} @ramp2 makespan <b>{b_mk}</b>，
是 eject 下界 <b>{b_lb}</b> 的约 <b>{b_ratio}</b>；同配置 ramp1→ramp2 仍有明显收益（<b>{r1}</b> → <b>{r2}</b>），但远低于 eject 下界的 {mmax}× 线性缩放。</li>
<li><strong>AFIFO≤{cap} 的代价集中在大报文</strong>：m=1 在 cap={cap} 下与单 flit 深度曲线同列一致；m 增大后 pacing 开销迅速放大。</li>
</ul>"""


def main():
    data = load()
    rdata = load_router()
    bdata = load_bal()
    r4data = load_ramp4()
    sdata = load_size()
    caps = data["caps"]
    series_data = []
    bi_only = []
    uni_only = []
    for key, label, color in SERIES:
        cfg = data["configs"].get(key)
        if not cfg:
            continue
        pts = cfg["points"]
        series_data.append((label, color, pts))
        if key.endswith("_bi"):
            bi_only.append((label, color, pts))
        else:
            uni_only.append((label, color, pts))

    body = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Border 短弧 · AFIFO 深度 vs Makespan</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#0f172a;max-width:960px;}}
h1,h2{{color:#1e3a8a;}} .card{{background:#fff;border:1px solid #e2e8f0;padding:16px;margin:16px 0;border-radius:8px;}}
table{{border-collapse:collapse;width:100%;font-size:12px;}} td,th{{border:1px solid #cbd5e1;padding:5px 6px;text-align:center;}}
th{{background:#e2e8f0;}} td.l{{text-align:left;}} .note{{color:#64748b;font-size:12px;}}
</style></head><body>
<h1>Border 短弧：AFIFO 深度 vs Makespan</h1>
<p class='note'>模型：router 零 buffer · 无阻塞 · 无冲突 · H=4, V=6, 跨界 AFIFO link=10cy · 环形状优化<br>
更新：{html.escape(data.get('updated', ''))} · 数据 <code>results/border_afifo_depth_sweep.json</code></p>
<div class='card'><h2>双向环 @ 下 ramp=2</h2>
{line_chart(caps, bi_only, "makespan vs 边界 AFIFO 深度上限（双向）")}
</div>
<div class='card'><h2>单向环 @ 下 ramp=1</h2>
{line_chart(caps, uni_only, "makespan vs 边界 AFIFO 深度上限（单向）")}
</div>
<div class='card'><h2>数值表</h2>
{table_rows(data)}
<p class='note'>每格为在该 AFIFO 深度上限下搜索到的最小 makespan（per-link peak ≤ cap）。
搜索合并全部 spread 候选与各 cap 下 atomic 结果后按深度过滤，保证 cap 增大时 makespan 不升。
cap=0 表示跨界不允许在 AFIFO 中等待。</p>
</div>
{cliff_note_16x16_bi(data)}
{ramp4_section(data, r4data)}
{size_section(sdata)}
{balanced_diag_section(data, bdata)}
{router_buffer_section(rdata)}
</body></html>"""

    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
