#!/usr/bin/env python3
"""Plot makespan vs border AFIFO depth cap from border_afifo_depth_sweep.json."""

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "border_afifo_depth_sweep.json"
ROUTER_JSON_PATH = ROOT / "results" / "router_afifo_depth_sweep.json"
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


def line_chart(caps, series_data, title, width=720, height=380):
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
    parts.append(f'<text x="{margin_l+plot_w/2:.1f}" y="{height-8}" font-size="11" text-anchor="middle">边界 AFIFO 深度上限（per-link peak）</text>')
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
    """Explain 16×16 bi drop from ~379 to 240 near high AFIFO caps."""
    cfg = data.get("configs", {}).get("16x16_bi", {})
    pts = {p["cap"]: p for p in cfg.get("points", [])}
    d40 = pts.get(40, {}).get("detail") or {}
    d46 = pts.get(46, {}).get("detail") or pts.get(48, {}).get("detail") or {}
    mk40 = pts.get(40, {}).get("makespan", "—")
    mk46 = pts.get(46, {}).get("makespan") or pts.get(48, {}).get("makespan", "—")
    depth46 = d46.get("afifo_depth", 46)
    return f"""
<div class='card'><h2>案例：16×16 双向为何在 AFIFO≈46 后 makespan 骤降至 240？</h2>
<p>表中可见：cap≤40 时 makespan 稳定在 <b>{mk40} cy</b>；cap≥46 时降至 <b>{mk46} cy</b>。
若曲线只在 40 与 48 两点采样，会<strong>看起来像「到 48 才掉」</strong>——实际是<strong>门槛在 46</strong>（本 sweep 已补 45/46/47 采样点）。</p>

<h3>1. 为什么会下降？——两种调度范式切换</h3>
<p>cap≤40 时的最优方案来自 <code>schedule_atomic</code>（相位错开 / pacing）：</p>
<ul>
<li>每个源作为整体原子放置；若某次跨界会使任意边界 AFIFO 在任意 cycle 超过 cap，就<strong>整体推迟该源的注入</strong>。</li>
<li>在 AFIFO 预算很小（深度≤3）时，只能大量「等」在边界，注入偏移累积 → makespan ≈379 cy，但 AFIFO 峰值仅 3。</li>
<li>这是<strong>保守、低缓冲</strong>策略：用更长时间换更浅的 AFIFO。</li>
</ul>
<p>cap≥46 时，另一类方案变得可行：<code>schedule</code> 的 <strong>spread=0</strong>（环上链路时分插入）：</p>
<ul>
<li>四象限 Hamilton 环先按固有节拍跑（Pass1 本象限 home 子树）；跨界 flit 插入环内链路空闲 send 槽（Pass2），允许在 AFIFO 中<strong>短暂排队</strong>。</li>
<li>不强制 per-source 原子 pacing，跨界 burst 与环内 conveyer 更自然对齐 → makespan <b>240 cy</b>（约 −37%）。</li>
<li>代价：单链路 AFIFO 峰值约 <b>{depth46}</b>（均衡深度约 40），必须用更深的边界 FIFO 承受等待。</li>
</ul>
<p><b>结论：</b>下降不是「缓冲越深传输越快」的连续渐变，而是<strong>低深度下只能用 atomic 保守调度；深度够深后 spread=0 的环时分调度才合法并显著更优</strong>。</p>

<h3>2. 为什么门槛在 46（而不是 40 或 5）？</h3>
<p>对每个候选调度，我们要求 <code>afifo_depth ≤ cap</code>（单链路峰值）。</p>
<ul>
<li><code>spread=0</code> 方案的实测峰值 AFIFO = <b>{depth46}</b>（环形状优化：vflip+90° / rect+90° / rect+270° / rect+90°）。</li>
<li>cap=45 时该方案<strong>不被允许</strong>（46&gt;45），搜索只能在 atomic 等低深度方案里选 → 仍约 379 cy。</li>
<li>cap=46 时 <code>spread=0</code> 首次入选候选集，240 &lt; 379，曲线<strong>断崖式</strong>下探。</li>
</ul>
<p>cap=5 时虽也允许「深度≤5」，但 spread=0（峰值 {depth46}）仍不可行；atomic 在 cap=3 已到其自身最优 379，故 cap=5~40 平台不变。</p>

<h3>3. 与 eject 下界的关系</h3>
<p>16×16 双向 eject 下界 = (N−1)/2 = <b>128 cy</b>。240 cy 约为下界的 1.88×，仍远高于下界——瓶颈在<strong>跨界+环内链路时分</strong>与 AFIFO 等待，而非下 ramp 带宽。</p>

<p class='note'>cap≤40 最优：atomic/natural，mk={mk40}，AFIFO峰值={d40.get('afifo_depth','?')}。
cap≥46 最优：schedule spread=0，mk={mk46}，AFIFO峰值={depth46}，均衡深度≈{d46.get('afifo_balanced','?')}。</p>
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
        for k in (1, 2, 3, 4):
            for p in grid.get(str(k), grid.get(k, [])):
                d = p.get("detail") or {}
                if d.get("method") == "pipelined":
                    return d
        return {}

    pu, pb = pip_info(grid_u), pip_info(grid_b)
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


def main():
    data = load()
    rdata = load_router()
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
<p class='note'>模型：router 零 buffer · 无阻塞 · 无冲突 · H=4, V=6 · 环形状优化<br>
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
{router_buffer_section(rdata)}
</body></html>"""

    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
