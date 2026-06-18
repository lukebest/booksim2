#!/usr/bin/env python3
"""Generate a high-performance cycle-by-cycle HTML animation for the
16×16 border 4-ring bidirectional allgather (H=4, V=6, ramp_bw=2).

Schedule: sim_fused_rings.build_border_delivery + greedy link-time calendar
(makespan 267). Output: results/dataflow_border_16x16.html

Open in a browser; use play / slider / source or quadrant filter.
Canvas rendering + per-cycle start/end buckets for O(active) updates.
"""

import json
import heapq
from collections import defaultdict
from pathlib import Path

import sim_fused_rings as fr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "dataflow_border_16x16.html"

MX, MY, H, V, RAMP, RAMP_BW = 16, 16, 4, 6, 1, 2

# 象限注入错开：左上 Q2=0, 右上 Q3=1, 右下 Q1=2, 左下 Q0=3
QUAD_STAGGER = {2: 0, 3: 1, 1: 2, 0: 3}
QUAD_LABEL = {0: "Q0 左下", 1: "Q1 右下", 2: "Q2 左上", 3: "Q3 右上"}


def _ring_edge_sets(paths_fn):
    hw, hh = MX // 2, MY // 2
    out = []
    for qi, (qx, qy) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
        x0, y0 = qx * hw, qy * hh
        order = paths_fn(x0, y0, hw, hh, qi)
        out.append(set((order[k], order[(k + 1) % len(order)]) for k in range(len(order))))
    return out


def _border_paths(x0, y0, w, h, _qi):
    return fr.quad_ring_horizontal(x0, y0, w, h)


def _quad_std_paths(x0, y0, w, h, _qi):
    return fr.ham_cycle_rect(x0, y0, w, h)


def ring_paths(paths_fn=_border_paths):
    hw, hh = MX // 2, MY // 2
    return [paths_fn(qx * hw, qy * hh, hw, hh, qi)
            for qi, (qx, qy) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1)))]


def build_border_deliveries(bidir=True):
    fr.cfg(MX, MY, H, V)
    return {s: fr.build_border_delivery(s, bidir) for s in range(MX * MY)}


def build_quad_lap_deliveries(bidir=True):
    fr.cfg(MX, MY, H, V)
    quads, _ = fr.quad_setup()
    return {s: fr.build_quad_border_lap_delivery(s, bidir, quads) for s in range(MX * MY)}


def inj_offset_zero(_s):
    return 0


def inj_offset_quad_stagger(s):
    return QUAD_STAGGER[fr.quad_of(s)]


def trace_scheme(deliveries, inj_offset_fn, paths_fn=_border_paths, ramp_bw=RAMP_BW):
    """Greedy link calendar sim with per-source injection offset; record conflicts."""
    fr.cfg(MX, MY, H, V)
    ring_edges = _ring_edge_sets(paths_fn)

    link = fr.Cal(1)
    down = fr.Cal(ramp_bw)
    up = fr.Cal(ramp_bw)
    link_owner = defaultdict(dict)
    pq = []
    seq = 0
    avail = {}
    ev_s, ev_p, ev_c, ev_t, ev_lat, ev_arr, ev_ready, ev_kind, ev_inj = [], [], [], [], [], [], [], [], []

    for s, ch in deliveries.items():
        off = inj_offset_fn(s)
        inj = up.reserve(s, off)
        avail[(s, s)] = inj + RAMP
        for c in ch.get(s, []):
            heapq.heappush(pq, (avail[(s, s)], seq, s, s, c))
            seq += 1

    cf_cy, cf_send, cf_p, cf_c, cf_ev, cf_blk, cf_delay = [], [], [], [], [], [], []
    cf_kind, cf_bkind, cf_s, cf_bs = [], [], [], []

    makespan = 0
    while pq:
        ready, _, s, p, c = heapq.heappop(pq)
        lk = (p, c)
        send = link.reserve(lk, ready)
        lat = fr.edge_lat(p, c)
        arrive = send + lat
        e = down.reserve(c, arrive)
        makespan = max(makespan, e + RAMP)

        ev_idx = len(ev_s)
        kind = _hop_kind(p, c, ring_edges)

        if send > ready:
            blk = link_owner[lk].get(ready, -1)
            cf_cy.append(ready)
            cf_send.append(send)
            cf_p.append(p)
            cf_c.append(c)
            cf_ev.append(ev_idx)
            cf_blk.append(blk)
            cf_delay.append(send - ready)
            cf_kind.append(kind)
            cf_s.append(s)
            if blk >= 0:
                cf_bkind.append(ev_kind[blk])
                cf_bs.append(ev_s[blk])
            else:
                cf_bkind.append(-1)
                cf_bs.append(-1)

        link_owner[lk][send] = ev_idx

        ev_s.append(s)
        ev_p.append(p)
        ev_c.append(c)
        ev_t.append(send)
        ev_lat.append(lat)
        ev_arr.append(arrive)
        ev_ready.append(ready)
        ev_kind.append(kind)
        ev_inj.append(inj_offset_fn(s))

        avail[(s, c)] = arrive
        for g in deliveries[s].get(c, []):
            heapq.heappush(pq, (arrive, seq, s, c, g))
            seq += 1

    conflicts = {
        "n": len(cf_cy), "cy": cf_cy, "send": cf_send,
        "p": cf_p, "c": cf_c, "ev": cf_ev, "blk": cf_blk,
        "delay": cf_delay, "kind": cf_kind, "bkind": cf_bkind,
        "s": cf_s, "bs": cf_bs,
    }
    events = {
        "s": ev_s, "p": ev_p, "c": ev_c,
        "t": ev_t, "lat": ev_lat, "arr": ev_arr,
        "ready": ev_ready, "kind": ev_kind, "inj": ev_inj,
        "n_ev": len(ev_s),
    }
    return makespan, events, conflicts


def trace_border(bidir=True, ramp_bw=RAMP_BW):
    deliveries = build_border_deliveries(bidir)
    return trace_scheme(deliveries, inj_offset_zero, _border_paths, ramp_bw)


def trace_quad_lap(bidir=True, ramp_bw=RAMP_BW):
    deliveries = build_quad_lap_deliveries(bidir)
    return trace_scheme(deliveries, inj_offset_quad_stagger, _border_paths, ramp_bw)


def build_conflict_buckets(conflicts, makespan):
    """conf_at[k] = conflict indices where attempt (ready) happens at cycle k."""
    conf_at = [[] for _ in range(makespan + 2)]
    resolved_at = [[] for _ in range(makespan + 2)]
    for i in range(conflicts["n"]):
        cy = conflicts["cy"][i]
        if cy <= makespan:
            conf_at[cy].append(i)
        sd = conflicts["send"][i]
        if sd <= makespan:
            resolved_at[sd].append(i)
    return conf_at, resolved_at


def _hop_kind(p, c, ring_edges):
    if fr.quad_of(p) != fr.quad_of(c):
        return 2  # cross / center exchange
    q = fr.quad_of(p)
    return 0 if (p, c) in ring_edges[q] else 1  # ring vs short arc


def pack_scheme(name, deliveries, inj_fn, paths_fn, ramp_bw=RAMP_BW):
    """Build one embeddable scheme dict for the HTML viewer."""
    makespan, events, conflicts = trace_scheme(deliveries, inj_fn, paths_fn, ramp_bw)
    start_at, end_at = build_buckets(events, makespan)
    conf_at, resolved_at = build_conflict_buckets(conflicts, makespan)
    return {
        "name": name,
        "makespan": makespan,
        "events": events,
        "conflicts": conflicts,
        "start_at": start_at,
        "end_at": end_at,
        "conf_at": conf_at,
        "resolved_at": resolved_at,
        "ring_paths": ring_paths(paths_fn),
        "t_lap_bi": 184,
        "tau": makespan - 184,
        "n_conflicts": conflicts["n"],
    }


def node_positions(cell, pad):
    pos = []
    for i in range(MX * MY):
        x, y = i % MX, i // MX
        pos.append([pad + x * cell, pad + y * cell])
    return pos


def build_buckets(events, makespan):
    """startAt[k] = indices starting move at k; endAt[k] = indices finishing at k."""
    start_at = [[] for _ in range(makespan + 2)]
    end_at = [[] for _ in range(makespan + 2)]
    n = events["n_ev"]
    for i in range(n):
        t = events["t"][i]
        a = events["arr"][i]
        if t <= makespan:
            start_at[t].append(i)
        if a <= makespan:
            end_at[a].append(i)
    return start_at, end_at


def schematic_svg():
    """Static 4-quadrant routing diagram."""
    w, h = 420, 420
    pad, gw = 36, 348
    qsz = gw // 2 - 4
    colors = ["#dbeafe", "#dcfce7", "#ffedd5", "#f3e8ff"]
    stroke = ["#2563eb", "#16a34a", "#ea580c", "#9333ea"]
    parts = [
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;background:#fff;border-radius:8px">',
        f'<text x="12" y="20" font-size="13" font-weight="bold" fill="#1e3a8a">'
        f'Border 4 环走法示意（16×16 → 4×8×8）</text>',
    ]
    labels = ["Q0 左下", "Q1 右下", "Q2 左上", "Q3 右上"]
    for qi, (qx, qy) in enumerate(((0, 1), (1, 1), (0, 0), (1, 0))):
        ox = pad + qx * (qsz + 8)
        oy = 28 + qy * (qsz + 8)
        parts.append(
            f'<rect x="{ox}" y="{oy}" width="{qsz}" height="{qsz}" '
            f'fill="{colors[qi]}" stroke="{stroke[qi]}" stroke-width="2" rx="4"/>'
        )
        parts.append(
            f'<text x="{ox + qsz/2}" y="{oy + 14}" text-anchor="middle" '
            f'font-size="10" font-weight="bold" fill="{stroke[qi]}">{labels[qi]}</text>'
        )
        # mini comb path inside quadrant
        _mini_comb(parts, ox + 8, oy + 22, qsz - 16, stroke[qi])

    cx, cy = pad + gw // 2, 28 + gw // 2
    parts.append(f'<line x1="{pad}" y1="{cy}" x2="{pad+gw}" y2="{cy}" stroke="#dc2626" stroke-width="2" stroke-dasharray="6 4"/>')
    parts.append(f'<line x1="{cx}" y1="28" x2="{cx}" y2="{28+gw}" stroke="#dc2626" stroke-width="2" stroke-dasharray="6 4"/>')
    parts.append(f'<text x="{cx}" y="{cy-6}" text-anchor="middle" font-size="9" fill="#dc2626">AFIFO 边界</text>')

    # arrows: bi half-lap + short arcs
    parts += [
        f'<text x="12" y="{h-52}" font-size="10" fill="#475569">① 本环双向半圈（ramp=2，各走 ~32 节点）</text>',
        f'<text x="12" y="{h-36}" font-size="10" fill="#475569">② 边界 fork → 行/列短弧扩散到邻象限</text>',
        f'<text x="12" y="{h-20}" font-size="10" fill="#475569">③ 四环并行 + 时分注入 → makespan = 267 cy</text>',
        '</svg>',
    ]
    return "\n".join(parts)


def _mini_comb(parts, x0, y0, sz, color):
    rows, cols = 4, 4
    cw = sz / (cols - 1)
    rh = (sz - 8) / (rows - 1)
    pts = []
    for r in range(rows):
        ys = y0 + r * rh
        xs = [x0 + c * cw for c in (range(cols) if r % 2 == 0 else range(cols - 1, -1, -1))]
        pts.extend((x, ys) for x in xs)
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5" opacity=".85"/>')


def schematic_quad_svg():
    """Schematic: full-border injection + partitioned foreign Hamilton lap."""
    w, h = 420, 440
    pad, gw = 36, 348
    qsz = gw // 2 - 4
    colors = ["#dbeafe", "#dcfce7", "#ffedd5", "#f3e8ff"]
    stroke = ["#2563eb", "#16a34a", "#ea580c", "#9333ea"]
    layout = [(2, 0, 1), (3, 1, 1), (0, 0, 0), (1, 1, 0)]
    labels = {0: "Q0 左下", 1: "Q1 右下", 2: "Q2 左上", 3: "Q3 右上"}
    parts = [
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="max-width:100%;background:#fff;border-radius:8px">',
        f'<text x="12" y="20" font-size="13" font-weight="bold" fill="#1e3a8a">'
        f'单次跨界 AFIFO + foreign Hamilton 整圈</text>',
    ]
    for qi, qx, qy in layout:
        ox = pad + qx * (qsz + 8)
        oy = 28 + qy * (qsz + 8)
        off = QUAD_STAGGER[qi]
        parts.append(
            f'<rect x="{ox}" y="{oy}" width="{qsz}" height="{qsz}" '
            f'fill="{colors[qi]}" stroke="{stroke[qi]}" stroke-width="2" rx="4"/>'
        )
        parts.append(
            f'<text x="{ox + qsz/2}" y="{oy + 14}" text-anchor="middle" '
            f'font-size="10" font-weight="bold" fill="{stroke[qi]}">{labels[qi]}</text>'
        )
        parts.append(
            f'<text x="{ox + qsz/2}" y="{oy + 28}" text-anchor="middle" '
            f'font-size="11" font-weight="bold" fill="#b45309">inject @ cy{off}</text>'
        )
        _mini_comb(parts, ox + 8, oy + 34, qsz - 16, stroke[qi])
    cx, cy = pad + gw // 2, 28 + gw // 2
    parts.append(f'<line x1="{pad}" y1="{cy}" x2="{pad+gw}" y2="{cy}" stroke="#dc2626" stroke-width="2" stroke-dasharray="6 4"/>')
    parts.append(f'<text x="{cx}" y="{cy-6}" text-anchor="middle" font-size="9" fill="#dc2626">8+8 边界注入点</text>')
    parts.append(f'<line x1="{cx}" y1="28" x2="{cx}" y2="{28+gw}" stroke="#dc2626" stroke-width="2" stroke-dasharray="6 4"/>')
    # tick marks on borders (8 inject points per edge)
    for i in range(8):
        t = pad + (i + 0.5) * (gw / 8)
        parts.append(f'<circle cx="{t}" cy="{cy}" r="2.5" fill="#dc2626"/>')
        parts.append(f'<circle cx="{cx}" cy="{28 + (i + 0.5) * (gw / 8)}" r="2.5" fill="#dc2626"/>')
    parts += [
        f'<text x="12" y="{h-84}" font-size="10" fill="#475569">① 每环只 1 份数据双向半圈，落盘后释放时隙</text>',
        f'<text x="12" y="{h-68}" font-size="10" fill="#475569">② 每条边界只复制 1 次进 AFIFO（按源的行/列分散）</text>',
        f'<text x="12" y="{h-52}" font-size="10" fill="#475569">③ 64 源把跨界分摊到 8 条边界 link（各 8 次）</text>',
        f'<text x="12" y="{h-36}" font-size="10" fill="#475569">④ 接收环空闲时隙读出 AFIFO → 绕整圈</text>',
        f'<text x="12" y="{h-20}" font-size="10" fill="#475569">⑤ 对角象限再单次跨界一次；inject 错开 0/1/2/3</text>',
        '</svg>',
    ]
    return "\n".join(parts)


def render(out_path=OUT):
    fr.cfg(MX, MY, H, V)
    bidir, rb = True, RAMP_BW

    sch_border = pack_scheme(
        "border 短弧 + AFIFO",
        build_border_deliveries(bidir), inj_offset_zero, _border_paths, rb,
    )
    sch_quad = pack_scheme(
        "单次跨界 AFIFO + foreign Hamilton 整圈 + inject 错开",
        build_quad_lap_deliveries(bidir), inj_offset_quad_stagger, _border_paths, rb,
    )

    cell, pad = 34, 44
    W = pad * 2 + (MX - 1) * cell
    canvas_h = pad * 2 + (MY - 1) * cell

    cfg = {
        "mx": MX, "my": MY, "h_lat": H, "v_lat": V, "ramp_bw": RAMP_BW,
        "n": MX * MY, "pos": node_positions(cell, pad),
        "cell": cell, "pad": pad, "W": W, "H": canvas_h,
        "qmap": [fr.quad_of(i) for i in range(MX * MY)],
        "quad_stagger": {str(k): v for k, v in QUAD_STAGGER.items()},
        "default_scheme": "border",
        "schemes": {"border": sch_border, "quad_lap": sch_quad},
        "schematic": {"border": schematic_svg(), "quad_lap": schematic_quad_svg()},
    }
    data = json.dumps(cfg, separators=(",", ":"))

    html = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>16×16 AllGather 逐步演示（多方案）</title>
<style>
:root{{--bg:#0f172a;--panel:#1e293b;--text:#e2e8f0;--accent:#38bdf8;--muted:#94a3b8;}}
*{{box-sizing:border-box;}}
body{{font-family:'Segoe UI',system-ui,sans-serif;margin:0;background:#f1f5f9;color:#0f172a;}}
header{{background:linear-gradient(135deg,#1e3a8a,#1e40af);color:#fff;padding:16px 24px;}}
header h1{{margin:0 0 6px;font-size:20px;}}
header p{{margin:0;font-size:13px;opacity:.9;line-height:1.5;}}
.layout{{display:grid;grid-template-columns:1fr 320px 300px;gap:16px;padding:16px;max-width:1600px;margin:0 auto;}}
@media(max-width:1300px){{.layout{{grid-template-columns:1fr 1fr;}} .side-col{{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;gap:16px;}}}}
@media(max-width:900px){{.layout{{grid-template-columns:1fr;}} .side-col{{grid-template-columns:1fr;}}}}
.panel{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);}}
.panel h2{{margin:0 0 10px;font-size:15px;color:#1e3a8a;}}
#cvwrap{{position:relative;overflow:auto;border-radius:8px;background:#f8fafc;}}
canvas{{display:block;}}
.ctl{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0;font-size:13px;}}
button{{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:7px 14px;cursor:pointer;font-size:13px;}}
button:hover{{background:#1d4ed8;}}
button.sec{{background:#64748b;}}
input[type=range]{{flex:1;min-width:120px;}}
select{{font-size:13px;padding:4px 6px;border-radius:4px;border:1px solid #cbd5e1;}}
#cyc{{font-weight:700;color:#1e3a8a;font-variant-numeric:tabular-nums;min-width:3em;display:inline-block;}}
.statgrid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px;}}
.statgrid div{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px;}}
.statgrid b{{display:block;font-size:18px;color:#1e3a8a;}}
.phase{{height:8px;border-radius:4px;background:#e2e8f0;margin:8px 0;overflow:hidden;}}
.phase>div{{height:100%;background:linear-gradient(90deg,#3b82f6,#8b5cf6,#10b981);width:0%;transition:width .05s;}}
.legend{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}}
.lg{{font-size:10px;padding:2px 6px;border-radius:4px;color:#fff;cursor:pointer;opacity:.35;user-select:none;}}
.lg.on{{opacity:1;}}
.note{{font-size:11px;color:#64748b;line-height:1.5;margin-top:8px;}}
#conf_spark{{width:100%;height:44px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;display:block;margin:6px 0;}}
#conf_list{{max-height:340px;overflow-y:auto;font-size:11px;border:1px solid #e2e8f0;border-radius:6px;}}
#conf_list table{{width:100%;border-collapse:collapse;}}
#conf_list th{{position:sticky;top:0;background:#fef3c7;z-index:1;font-size:10px;padding:4px 5px;border-bottom:1px solid #fcd34d;}}
#conf_list td{{padding:4px 5px;border-bottom:1px solid #f1f5f9;vertical-align:top;}}
#conf_list tr:hover{{background:#fffbeb;}}
.tag{{display:inline-block;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:600;color:#fff;}}
.tag-ring{{background:#2563eb;}} .tag-arc{{background:#ea580c;}} .tag-x{{background:#dc2626;}}
.delay{{color:#b45309;font-weight:700;}}
.side-col{{display:flex;flex-direction:column;gap:16px;}}
</style></head><body>
<header>
<h1 id="hdr_title">16×16 AllGather — cycle-by-cycle 演示</h1>
<p id="hdr_desc">H=4, V=6, down-ramp=2 · makespan=<b id="hdr_mk"></b> cy · 冲突尝试=<b id="hdr_cf"></b></p>
<div class="ctl" style="margin-top:10px">
<label style="color:#fff;font-size:13px">方案
<select id="scheme" style="min-width:280px">
<option value="border">border 短弧 + AFIFO（267 cy）</option>
<option value="quad_lap">单次跨界 AFIFO + Hamilton 整圈 + inject 错开</option>
</select></label>
</div>
</header>
<div class="layout">
<div>
<div class="panel">
<h2>实时 mesh 动画（Canvas）</h2>
<div id="cvwrap"><canvas id="cv"></canvas></div>
<div class="phase"><div id="phase_bar"></div></div>
<div class="ctl">
<button id="play">▶ 播放</button>
<button id="step" class="sec">单步 ▶|</button>
<button id="reset" class="sec">⟲ 复位</button>
<span>cycle <span id="cyc">0</span> / <span id="mk"></span></span>
</div>
<div class="ctl"><input type="range" id="slider" min="0" value="0" step="1"></div>
<div class="ctl">
<label>速度 <input type="range" id="speed" min="20" max="500" value="120" step="10"></label>
<label>模式
<select id="mode">
<option value="single">单源（默认）</option>
<option value="quad">单象限 64 源</option>
<option value="heatmap">全源热力图</option>
<option value="conflict">冲突检测（双占用）</option>
<option value="all">全源圆点（慢）</option>
</select></label>
</div>
<div class="ctl" id="filter_row">
<label>源节点 <select id="src"></select></label>
<label>象限 <select id="quad">
<option value="-1">全部</option>
<option value="0">Q0 左下</option>
<option value="1">Q1 右下</option>
<option value="2">Q2 左上</option>
<option value="3">Q3 右上</option>
</select></label>
</div>
<div class="statgrid">
<div>飞行中 flit<b id="st_fly">0</b></div>
<div>本 cycle eject<b id="st_ej">0</b></div>
<div>可见源数<b id="st_src">1</b></div>
<div>阶段<b id="st_phase">本环半圈</b></div>
<div>本cy冲突尝试<b id="st_conf">0</b></div>
</div>
<p class="note" id="mode_note">圆点颜色=源节点 · 粗描边=正在 eject · 红虚线=象限边界/AFIFO · 默认「单源」模式保证 16×16 流畅播放。</p>
</div>

<div class="panel">
<h2>同 link 双占用尝试 · calendar 错开</h2>
<p class="note" style="margin:0 0 6px">当 flit 在 cycle <b>ready</b> 想用 link 但已被占用 → calendar 推迟到 <b>send</b>。橙虚线=尝试时刻；紫实线=推迟后发出；绿圈=占位的阻塞流。</p>
<canvas id="conf_spark" width="288" height="44"></canvas>
<div class="statgrid" style="grid-template-columns:1fr 1fr 1fr">
<div>本 cycle 尝试<b id="cf_try">0</b></div>
<div>本 cycle 推迟发出<b id="cf_res">0</b></div>
<div>累计冲突<b id="cf_tot">0</b></div>
</div>
<div id="conf_list"><table><thead><tr>
<th>link</th><th>尝试→发出</th><th>阻塞流</th><th>被延迟流</th><th>Δ</th>
</tr></thead><tbody id="conf_body"></tbody></table></div>
<p class="note">共 <b id="cf_all">0</b> 次双占用尝试；无一次同 cycle 双发（硬约束满足）。</p>
</div>
</div>
<div class="side-col">
<div class="panel"><h2>走法示意图</h2><div id="schematic_box"></div></div>
<div class="panel"><h2>Hamilton 环路径</h2>
<p class="note" style="margin-top:0" id="ring_note">叠加 4 象限 comb 环（随方案切换）。</p>
<svg id="ring_svg" style="width:100%;max-width:320px;display:block;margin:0 auto"></svg>
</div>
<div class="panel"><h2>时间线</h2>
<p class="note" style="margin-top:0" id="timeline_box">
<b>T<sub>lap</sub><sup>bi</sup> ≈ 184</b>：四环并行双向半圈<br>
<b>τ</b>：<span id="tl_tau">—</span><br>
<b>B<sub>eject</sub> = 128</b>：下 ramp 吞吐下界<br>
<b>T = <span id="tl_mk">—</span></b>：makespan
</p></div>
</div>
</div>
<script>
const D = __DATA__;
let schemeKey = D.default_scheme;
let S = D.schemes[schemeKey];
const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
cv.width = D.W; cv.height = D.H;

function syncSchemeUi(){{
  document.getElementById('mk').textContent = S.makespan;
  document.getElementById('hdr_mk').textContent = S.makespan;
  document.getElementById('hdr_cf').textContent = S.n_conflicts;
  document.getElementById('slider').max = S.makespan;
  document.getElementById('cf_all').textContent = S.conflicts.n;
  document.getElementById('tl_mk').textContent = S.makespan;
  document.getElementById('tl_tau').textContent = S.tau + ' cy';
  document.getElementById('schematic_box').innerHTML = D.schematic[schemeKey];
  const rn=document.getElementById('ring_note');
  if(rn) rn.textContent = schemeKey==='border'
    ? 'border：下方两环脊贴中心边界，跨界走短弧。'
    : 'quad_lap：每条边界单次跨界进 AFIFO（按源行/列分散）；foreign 象限读出后绕整圈。inject：Q2@0 Q3@1 Q1@2 Q0@3。';
  document.getElementById('hdr_title').textContent = schemeKey==='border'
    ? '16×16 Border 短弧 AllGather'
    : '16×16 单次跨界 AFIFO + Hamilton 整圈';
  const sel=document.getElementById('scheme');
  if(sel && sel.options.length>=2){{
    sel.options[0].text='border 短弧 + AFIFO（'+D.schemes.border.makespan+' cy, '+D.schemes.border.n_conflicts+' 冲突）';
    sel.options[1].text='单次跨界 AFIFO + Hamilton 整圈（'+D.schemes.quad_lap.makespan+' cy, '+D.schemes.quad_lap.n_conflicts+' 冲突）';
  }}
}}
syncSchemeUi();

const QCOL = ['#3b82f6','#22c55e','#f97316','#a855f7'];
const QBG  = ['#eff6ff','#f0fdf4','#fff7ed','#faf5ff'];

function hue(s){{ return `hsl(${{Math.round(s*360/D.n)}},72%,48%)`; }}
function coord(i){{ return [i%D.mx, (i/D.mx)|0]; }}
function pos(i){{ return D.pos[i]; }}
function isCross(p,c){{ return D.qmap[p] !== D.qmap[c]; }}

// --- static ring overlay svg (rebuilt on scheme change) ---
function drawRingSvg(){
  const NS='http://www.w3.org/2000/svg';
  const svg=document.getElementById('ring_svg');
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  const sc=16, pad=14, W=pad*2+(D.mx-1)*sc, Ht=pad*2+(D.my-1)*sc;
  svg.setAttribute('viewBox',`0 0 ${W} ${Ht}`);
  svg.setAttribute('width',W); svg.setAttribute('height',Ht);
  for(let y=0;y<D.my;y++)for(let x=0;x<D.mx;x++){
    const i=x+D.mx*y, p=pad+x*sc, q=pad+y*sc;
    const r=document.createElementNS(NS,'circle');
    r.setAttribute('cx',p); r.setAttribute('cy',q); r.setAttribute('r',3);
    r.setAttribute('fill',QBG[D.qmap[i]]); r.setAttribute('stroke','#cbd5e1');
    svg.appendChild(r);
  }
  S.ring_paths.forEach((path,qi)=>{
    let d='';
    path.forEach((nd,k)=>{
      const p=pad+(nd%D.mx)*sc, q=pad+((nd/D.mx)|0)*sc;
      d += (k?'L':'M')+p+','+q;
    });
    d+='Z';
    const el=document.createElementNS(NS,'path');
    el.setAttribute('d',d); el.setAttribute('fill','none');
    el.setAttribute('stroke',QCOL[qi]); el.setAttribute('stroke-width','1.8');
    el.setAttribute('opacity','0.85');
    svg.appendChild(el);
  });
  const hw=D.mx/2, hh=D.my/2;
  [['M',pad,pad+hh*sc,'L',pad+(D.mx-1)*sc,pad+hh*sc],
   ['M',pad+hw*sc,pad,'L',pad+hw*sc,pad+(D.my-1)*sc]].forEach(a=>{
    const l=document.createElementNS(NS,'line');
    l.setAttribute('x1',a[1]);l.setAttribute('y1',a[2]);
    l.setAttribute('x2',a[3]);l.setAttribute('y2',a[4]);
    l.setAttribute('stroke', '#dc2626');
    l.setAttribute('stroke-dasharray', '4 3');
    l.setAttribute('stroke-width','1.5'); svg.appendChild(l);
  });
  if(schemeKey==='quad_lap'){
    const hw=D.mx/2, hh=D.my/2, sc=16, pad=14;
    for(let y=0;y<hh;y++){
      const px=pad+hw*sc, py=pad+y*sc;
      const m=document.createElementNS(NS,'circle');
      m.setAttribute('cx',px); m.setAttribute('cy',py); m.setAttribute('r',2.5);
      m.setAttribute('fill','#dc2626'); svg.appendChild(m);
    }
    for(let x=0;x<hw;x++){
      const px=pad+x*sc, py=pad+hh*sc;
      const m=document.createElementNS(NS,'circle');
      m.setAttribute('cx',px); m.setAttribute('cy',py); m.setAttribute('r',2.5);
      m.setAttribute('fill','#dc2626'); svg.appendChild(m);
    }
  }
}
drawRingSvg();

// --- source select ---
const srcSel=document.getElementById('src');
for(let s=0;s<D.n;s++){
  const [x,y]=coord(s);
  const o=document.createElement('option');
  o.value=s;
  const inj=S.events.inj && S.events.inj[s]!==undefined ? S.events.inj[s] : 0;
  o.textContent='('+x+','+y+') id='+s+' Q'+D.qmap[s]+' inj@'+inj;
  srcSel.appendChild(o);
}
srcSel.value='0';

document.getElementById('scheme').onchange=()=>{
  schemeKey=document.getElementById('scheme').value;
  S=D.schemes[schemeKey];
  staticCv=null;
  active.clear();
  cur=0;
  syncSchemeUi();
  drawRingSvg();
  // refresh source labels with inj offset
  for(let s=0;s<D.n;s++){
    const inj=S.events.inj[s];
    srcSel.options[s].textContent=srcSel.options[s].textContent.replace(/ inj@\\d+$/,'')+' inj@'+inj;
  }
  draw(0);
};

const KIND=['Hamilton环','短弧','跨界AFIFO'];
const KIND_CLS=['tag-ring','tag-arc','tag-x'];

function kindTag(k){{
  return `<span class="tag ${{KIND_CLS[k]||''}}">${{KIND[k]||'?'}}</span>`;
}}
function ndLabel(n){{ const [x,y]=coord(n); return `(${x},${y})`; }}

function confVisible(i){{
  const C=S.conflicts;
  if(mode==='single') return C.s[i]===parseInt(srcSel.value,10)||C.bs[i]===parseInt(srcSel.value,10);
  if(mode==='quad') return D.qmap[C.s[i]]===filterQuad||D.qmap[C.bs[i]]===filterQuad;
  if(filterQuad>=0) return D.qmap[C.s[i]]===filterQuad||D.qmap[C.bs[i]]===filterQuad;
  return true;
}}

function drawSparkline(){{
  const sp=document.getElementById('conf_spark');
  const g=sp.getContext('2d');
  const W=sp.width,H=sp.height;
  g.clearRect(0,0,W,H);
  const hist=new Array(S.makespan+1).fill(0);
  for(let i=0;i<S.conflicts.n;i++) hist[S.conflicts.cy[i]]++;
  const mx=Math.max(1,...hist);
  g.fillStyle='#fef9c3'; g.fillRect(0,0,W,H);
  for(let k=0;k<=S.makespan;k++){
    const h=(hist[k]/mx)*(H-6);
    if(!h) continue;
    g.fillStyle=k===cur?'#f59e0b':'#fbbf24';
    g.fillRect((k/S.makespan)*W, H-h-2, Math.max(1,W/S.makespan), h);
  }
  g.strokeStyle='#dc2626'; g.lineWidth=1.5;
  const x=(cur/S.makespan)*W;
  g.beginPath(); g.moveTo(x,0); g.lineTo(x,H); g.stroke();
}}

function updateConfPanel(k){{
  const C=S.conflicts;
  const tries=(S.conf_at[k]||[]).filter(confVisible);
  const resolved=(S.resolved_at[k]||[]).filter(confVisible);
  let cum=0;
  for(let i=0;i<C.n;i++) if(C.cy[i]<=k && confVisible(i)) cum++;
  document.getElementById('cf_try').textContent=tries.length;
  document.getElementById('cf_res').textContent=resolved.length;
  document.getElementById('cf_tot').textContent=cum;
  document.getElementById('st_conf').textContent=tries.length;
  document.getElementById('cf_all').textContent=C.n;
  const tb=document.getElementById('conf_body');
  tb.innerHTML='';
  const rows=[...tries.map(i=>['try',i]), ...resolved.map(i=>['res',i])];
  if(!rows.length){{
    tb.innerHTML='<tr><td colspan="5" style="color:#94a3b8;text-align:center;padding:12px">本 cycle 无可见冲突</td></tr>';
    return;
  }}
  rows.slice(0,80).forEach(([typ,i])=>{{
    const p=C.p[i], c=C.c[i], tr=document.createElement('tr');
    const blk=C.blk[i]>=0
      ? `${{ndLabel(C.bs[i])}} #${{C.bs[i]}} ${{kindTag(C.bkind[i])}}`
      : '<span style="color:#94a3b8">(未知)</span>';
    tr.innerHTML=`<td>${{ndLabel(p)}}→${{ndLabel(c)}}</td>`
      +`<td>${{typ==='try'?`<b>${{C.cy[i]}}</b>→${{C.send[i]}}`:`推迟@${{C.send[i]}}`}}</td>`
      +`<td>${{blk}}</td>`
      +`<td>${{ndLabel(C.s[i])}} #${{C.s[i]}} ${{kindTag(C.kind[i])}}</td>`
      +`<td class="delay">+${{C.delay[i]}}</td>`;
    tb.appendChild(tr);
  }});
  if(rows.length>80){{
    const tr=document.createElement('tr');
    tr.innerHTML=`<td colspan="5" style="color:#94a3b8">… 还有 ${{rows.length-80}} 条</td>`;
    tb.appendChild(tr);
  }}
}}

function drawConflictOverlay(k){{
  const C=S.conflicts;
  const tries=(S.conf_at[k]||[]).filter(confVisible);
  const resolved=(S.resolved_at[k]||[]).filter(confVisible);
  // blocked attempts at k: orange dashed link + wait badge
  tries.forEach(i=>{{
    const p=C.p[i], c=C.c[i];
    const a=pos(p), b=pos(c);
    ctx.save();
    ctx.strokeStyle='rgba(245,158,11,0.95)'; ctx.lineWidth=6;
    ctx.setLineDash([8,5]);
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
    ctx.setLineDash([]);
    const mx=(a[0]+b[0])/2, my=(a[1]+b[1])/2;
    ctx.fillStyle='#fff7ed'; ctx.strokeStyle='#b45309'; ctx.lineWidth=2;
    ctx.beginPath(); ctx.arc(mx,my,11,0,Math.PI*2); ctx.fill(); ctx.stroke();
    ctx.fillStyle='#b45309'; ctx.font='bold 10px sans-serif'; ctx.textAlign='center';
    ctx.fillText('+'+C.delay[i], mx, my+4);
    ctx.restore();
    // blocker sending at k
    if(C.blk[i]>=0){{
      const bi=C.blk[i], bp=pos(S.events.p[bi]), bc=pos(S.events.c[bi]);
      if(S.events.t[bi]===k){
        ctx.beginPath(); ctx.arc(bp[0],bp[1],8,0,Math.PI*2);
        ctx.strokeStyle='#16a34a'; ctx.lineWidth=3; ctx.stroke();
        const frac=0.35;
        const x=bp[0]+(bc[0]-bp[0])*frac, y=bp[1]+(bc[1]-bp[1])*frac;
        ctx.beginPath(); ctx.arc(x,y,6,0,Math.PI*2);
        ctx.fillStyle='rgba(22,163,74,0.35)'; ctx.fill();
        ctx.strokeStyle='#16a34a'; ctx.lineWidth=2; ctx.stroke();
      }}
    }}
  }});
  // delayed flit finally sent at k
  resolved.forEach(i=>{{
    const p=C.p[i], c=C.c[i];
    const a=pos(p), b=pos(c);
    ctx.strokeStyle='rgba(147,51,234,0.9)'; ctx.lineWidth=5;
    ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
    ctx.beginPath(); ctx.arc(a[0],a[1],7,0,Math.PI*2);
    ctx.fillStyle=hue(C.s[i]); ctx.fill();
    ctx.strokeStyle='#7e22ce'; ctx.lineWidth=2; ctx.stroke();
  }});
}}

// --- active set maintained incrementally ---
let cur=0, active=new Set(), timer=null;
let mode='single', filterSrc=-1, filterQuad=-1;

function visible(s){
  if(mode==='single') return s===parseInt(srcSel.value,10);
  if(mode==='quad') return D.qmap[s]===filterQuad;
  if(filterQuad>=0) return D.qmap[s]===filterQuad;
  return true;
}

let staticCv=null;
function ensureStatic(){
  if(staticCv) return;
  staticCv=document.createElement('canvas');
  staticCv.width=D.W; staticCv.height=D.H;
  const sctx=staticCv.getContext('2d');
  const c=sctx;
  c.clearRect(0,0,D.W,D.H);
  const hw=D.mx/2, hh=D.my/2;
  [[0,0,hw,hh,0],[hw,0,hw,hh,1],[0,hh,hw,hh,2],[hw,hh,hw,hh,3]].forEach(([x0,y0,w,h,qi])=>{
    c.fillStyle=QBG[qi];
    c.fillRect(D.pad+x0*D.cell-14, D.pad+y0*D.cell-14, (w-1)*D.cell+28, (h-1)*D.cell+28);
  });
  c.strokeStyle='#e2e8f0'; c.lineWidth=2;
  for(let y=0;y<D.my;y++)for(let x=0;x<D.mx;x++){
    const i=x+D.mx*y, [px,py]=pos(i);
    if(x+1<D.mx){ const j=i+1,[qx,qy]=pos(j); c.beginPath(); c.moveTo(px,py); c.lineTo(qx,qy); c.stroke(); }
    if(y+1<D.my){ const j=i+D.mx,[qx,qy]=pos(j); c.beginPath(); c.moveTo(px,py); c.lineTo(qx,qy); c.stroke(); }
  }
  c.setLineDash([6,4]);
  c.strokeStyle= schemeKey==='border' ? '#dc2626' : '#94a3b8';
  c.lineWidth= schemeKey==='border' ? 2 : 1;
  const midx=D.pad+(hw-0.5)*D.cell, midy=D.pad+(hh-0.5)*D.cell;
  c.beginPath(); c.moveTo(D.pad-10,midy); c.lineTo(D.W-D.pad+10,midy); c.stroke();
  c.beginPath(); c.moveTo(midx,D.pad-10); c.lineTo(midx,D.H-D.pad+10); c.stroke();
  c.setLineDash([]);
  c.lineWidth=1.2;
  S.ring_paths.forEach((path,qi)=>{
    c.strokeStyle=QCOL[qi]; c.globalAlpha=0.25;
    c.beginPath();
    path.forEach((nd,k)=>{ const p=pos(nd); if(k)c.lineTo(p[0],p[1]); else c.moveTo(p[0],p[1]); });
    c.closePath(); c.stroke();
  });
  c.globalAlpha=1;
  for(let i=0;i<D.n;i++){
    const [px,py]=pos(i);
    c.beginPath(); c.arc(px,py,5,0,Math.PI*2);
    c.fillStyle='#fff'; c.fill();
    c.strokeStyle='#94a3b8'; c.lineWidth=1.5; c.stroke();
  }
}

function drawStatic(){
  ensureStatic();
  ctx.clearRect(0,0,D.W,D.H);
  ctx.drawImage(staticCv,0,0);
}

function rebuildActive(k){
  active.clear();
  const ev=S.events;
  for(let i=0;i<ev.n_ev;i++){
    const t=ev.t[i], a=ev.arr[i];
    if(k>=t && k<a && visible(ev.s[i])) active.add(i);
  }
}

function drawQuadInject(k){
  if(schemeKey!=='quad_lap') return;
  const stagger=[3,2,0,1]; // Q0,Q1,Q2,Q3 inject cy
  const hw=D.mx/2, hh=D.my/2;
  [[0,8,0],[8,8,1],[0,0,2],[8,0,3]].forEach(([x0,y0,qi])=>{
    const off=stagger[qi];
    if(k!==off) return;
    const vx = x0===0 ? x0+hw-1 : x0;
    const hy = y0===0 ? y0+hh-1 : y0;
    ctx.save();
    ctx.strokeStyle='rgba(220,38,38,0.85)'; ctx.lineWidth=3;
    for(let y=0;y<hh;y++){
      const px=D.pad+vx*D.cell, py=D.pad+(y0+y)*D.cell;
      ctx.beginPath(); ctx.arc(px,py,5,0,Math.PI*2); ctx.stroke();
    }
    for(let x=0;x<hw;x++){
      const px=D.pad+(x0+x)*D.cell, py=D.pad+hy*D.cell;
      ctx.beginPath(); ctx.arc(px,py,5,0,Math.PI*2); ctx.stroke();
    }
    ctx.fillStyle='#b45309'; ctx.font='bold 12px sans-serif'; ctx.textAlign='center';
    ctx.fillText('单次跨界@'+off, D.pad+x0*D.cell+(hw-1)*D.cell/2, D.pad+y0*D.cell-18);
    ctx.restore();
  });
}

function draw(k){
  cur=k;
  document.getElementById('cyc').textContent=k;
  document.getElementById('slider').value=k;
  document.getElementById('phase_bar').style.width=(100*k/S.makespan)+'%';
  const phaseLabel = schemeKey==='border'
    ? (k < S.t_lap_bi ? '本环半圈' : (k < S.makespan-10 ? '跨界短弧' : '收尾 eject'))
    : (k < 4 ? '四环 inject 错开' : (k < S.t_lap_bi+4 ? '本环+单次跨界+foreign 整圈' : '收尾'));
  document.getElementById('st_phase').textContent = phaseLabel;
  updateConfPanel(k);
  drawSparkline();

  drawStatic();
  drawQuadInject(k);
  const ev=S.events;
  let fly=0, ej=0;
  const ejNodes={{}}; const heatLink={{}}; const heatNode={{}};

  if(mode==='heatmap'){{
    for(let i=0;i<ev.n_ev;i++){{
      const s=ev.s[i], t=ev.t[i], a=ev.arr[i], lat=ev.lat[i];
      if(k<t||k>=a) continue;
      if(!visible(s)) continue;
      const key=ev.p[i]+','+ev.c[i];
      heatLink[key]=(heatLink[key]||0)+1;
      if(k===a-1||k===ev.arr[i]-1){{}}
    }}
    for(let i=0;i<ev.n_ev;i++){{
      if(ev.arr[i]!==k) continue;
      if(!visible(ev.s[i])) continue;
      heatNode[ev.c[i]]=(heatNode[ev.c[i]]||0)+1;
      ej++;
    }}
    const maxL=Math.max(1,...Object.values(heatLink));
    Object.entries(heatLink).forEach(([key,cnt])=>{{
      const [p,c]=key.split(',').map(Number);
      const a=pos(p), b=pos(c);
      const alpha=0.15+0.75*cnt/maxL;
      ctx.strokeStyle=`rgba(239,68,68,${{alpha}})`;
      ctx.lineWidth=3+4*cnt/maxL;
      ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke();
    }});
    const maxN=Math.max(1,...Object.values(heatNode));
    Object.entries(heatNode).forEach(([nd,cnt])=>{{
      const p=pos(+nd);
      const r=5+10*cnt/maxN;
      ctx.beginPath(); ctx.arc(p[0],p[1],r,0,Math.PI*2);
      ctx.fillStyle=`rgba(16,185,129,${{0.3+0.6*cnt/maxN}})`; ctx.fill();
    }});
    fly=Object.keys(heatLink).length;
  }} else if(mode==='conflict'){{
    rebuildActive(k);
    for(const i of active){{
      if(!visible(S.events.s[i])) continue;
      const s=S.events.s[i], p=S.events.p[i], c=S.events.c[i], t=S.events.t[i], lat=S.events.lat[i];
      const frac=lat>0?(k-t)/lat:1;
      const a=pos(p), b=pos(c);
      const x=a[0]+(b[0]-a[0])*frac, y=a[1]+(b[1]-a[1])*frac;
      fly++;
      ctx.beginPath(); ctx.arc(x,y,4,0,Math.PI*2);
      ctx.fillStyle=hue(s); ctx.globalAlpha=0.45; ctx.fill(); ctx.globalAlpha=1;
    }}
    drawConflictOverlay(k);
  }} else {{
    if(mode!=='all' && active.size===0) rebuildActive(k);
    if(mode==='all') rebuildActive(k);
    for(const i of active){{
      const s=ev.s[i], p=ev.p[i], c=ev.c[i], t=ev.t[i], lat=ev.lat[i];
      const frac=lat>0?(k-t)/lat:1;
      const a=pos(p), b=pos(c);
      const x=a[0]+(b[0]-a[0])*frac, y=a[1]+(b[1]-a[1])*frac;
      fly++;
      ctx.beginPath(); ctx.arc(x,y, mode==='all'?3:5, 0, Math.PI*2);
      ctx.fillStyle=hue(s);
      if(isCross(p,c)){{ ctx.strokeStyle='#dc2626'; ctx.lineWidth=2; ctx.stroke(); }}
      ctx.fill();
    }}
    for(let i=0;i<ev.n_ev;i++){{
      if(ev.arr[i]!==k) continue;
      if(!visible(ev.s[i])) continue;
      ej++;
      ejNodes[ev.c[i]]=hue(ev.s[i]);
    }}
    for(const [nd,col] of Object.entries(ejNodes)){{
      const p=pos(+nd);
      ctx.beginPath(); ctx.arc(p[0],p[1],9,0,Math.PI*2);
      ctx.strokeStyle=col; ctx.lineWidth=3; ctx.stroke();
    }}
  }}

  document.getElementById('st_fly').textContent=fly;
  document.getElementById('st_ej').textContent=ej;
  let ns=1;
  if(mode==='single') ns=1;
  else if(filterQuad>=0) ns=64;
  else if(mode==='quad') ns=64;
  else if(mode==='conflict') ns=256;
  else ns=256;
  document.getElementById('st_src').textContent=ns;
  const mn=document.getElementById('mode_note');
  if(mn) mn.textContent = mode==='conflict'
    ? '冲突模式：橙虚线=本 cycle 双占用尝试 · 绿圈=阻塞流正在发出 · 紫线=被延迟流在本 cycle 终于发出 · 右栏列表详情'
    : '圆点颜色=源节点 · 粗描边=正在 eject · 红虚线=象限边界/AFIFO · 右栏冲突列随 cycle 同步更新';
}}

function stepActive(from,to){{
  if(mode==='single'||mode==='quad'||mode==='all'||mode==='conflict'){{
    if(to===from+1 && from>=0){{
      (S.start_at[to]||[]).forEach(i=>{ if(visible(S.events.s[i])) active.add(i); });
      (S.end_at[to]||[]).forEach(i=>active.delete(i));
      return;
    }}
  }}
  rebuildActive(to);
}}

document.getElementById('mode').onchange=()=>{
  mode=document.getElementById('mode').value;
  filterQuad=parseInt(document.getElementById('quad').value,10);
  if(mode==='quad' && filterQuad<0){ document.getElementById('quad').value='0'; filterQuad=0; }
  active.clear(); draw(cur);
};
document.getElementById('quad').onchange=()=>{{
  filterQuad=parseInt(document.getElementById('quad').value);
  if(mode==='quad' && filterQuad<0){{ document.getElementById('quad').value='0'; filterQuad=0; }}
  active.clear(); draw(cur);
}};
srcSel.onchange=()=>{{ active.clear(); draw(cur); }};

document.getElementById('slider').oninput=()=>{
  const nk=parseInt(document.getElementById('slider').value,10);
  if(Math.abs(nk-cur)>1){ active.clear(); rebuildActive(nk); }
  else stepActive(cur,nk);
  draw(nk);
};
document.getElementById('step').onclick=()=>{{
  const nk=Math.min(cur+1,S.makespan);
  stepActive(cur,nk); draw(nk);
}};
document.getElementById('reset').onclick=()=>{{ stop(); active.clear(); draw(0); }};
function stop(){{ if(timer){{clearInterval(timer);timer=null;document.getElementById('play').textContent='▶ 播放';}} }}
document.getElementById('play').onclick=()=>{{
  if(timer){{stop();return;}}
  document.getElementById('play').textContent='⏸ 暂停';
  timer=setInterval(()=>{{
    let nk=cur+1; if(nk>S.makespan) nk=0;
    stepActive(cur,nk); draw(nk);
  }}, 520-(+document.getElementById('speed').value));
}};
document.getElementById('speed').oninput=()=>{{ if(timer){{stop();document.getElementById('play').click();}} }};

active.clear(); draw(0);
</script>
</body></html>"""
    html = html.replace("{{", "{").replace("}}", "}")
    html = html.replace("__DATA__", data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path}  border={sch_border['makespan']}cy/{sch_border['n_conflicts']}cf"
          f"  quad_lap={sch_quad['makespan']}cy/{sch_quad['n_conflicts']}cf")


if __name__ == "__main__":
    render()
