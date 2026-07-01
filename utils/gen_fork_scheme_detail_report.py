#!/usr/bin/env python3
"""Detailed 0-buffer + AFIFO≤5 scheme report with diagrams and occupancy curves.

Output: results/report_fork_scheme_detail.html

Schemes (16×16, H=4, V=6, cross=6, ramp=1 flit/cy/node, m=1):
  * global Hamilton ring (Q=1)
  * border 4-quad ring + short arc (Q=4)
  * grid 1×2 (top/bottom half) ring + short arc
  * grid 2×1 (left/right half) ring + short arc
"""

import html
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utils"))

import hamilton_ring as hr
import sched_ring_zerobuf as S
import sched_zerobuf_compare as Z
import sim_fused_rings as fr
import slide_metrics as sm
from optimize_quad_shapes import quads_for
from sweep_buffer_pareto import build_grid_border, ch_to_edges, _best_ring_zerobuf
from tree_fork_research import fp_from_edges

OUT = ROOT / "results" / "report_fork_scheme_detail.html"
MX = MY = 16
H, V, RAMP_BW, AFIFO_CAP = 4, 6, 1, 5
N = MX * MY
BORDER_BI_CFG = (("vflip", 1), ("rect", 1), ("rect", 3), ("vflip", 3))


def esc(s):
    return html.escape(str(s))


def setup():
    fr.cfg(MX, MY, H, V, cross=fr.CROSS_LAT)
    Z.cfg(MX, MY, H, V)
    Z.init_ring()
    Z.init_quadrants()


def run_ring(bidir):
    order = hr.snake_cycle(MX, MY)
    pos = {nd: k for k, nd in enumerate(order)}
    foot = {s: Z.fp_ring(s, order, pos, bidir, RAMP_BW) for s in range(N)}
    best = None
    for order_name, gen in Z.SRC_ORDERS.items():
        mk, mo, busy, inj, events = Z.export_events(foot, RAMP_BW, gen(), flits=1)
        ok = Z.verify(busy, RAMP_BW)
        rec = dict(mk=mk, method=f"pack:{order_name}", ok=ok, busy=busy,
                   events=events, afifo_peak=0, afifo_series=[],
                   mode="zerobuf_rigid", order=order, edges=None)
        if best is None or mk < best["mk"]:
            best = rec
    return best


def run_hybrid(B, bidir):
    """Rigid 0-buffer hybrid: B horizontal bands, local bi ring + vertical tree."""
    foot = {s: Z.fp_hybrid(s, B, bidir, RAMP_BW) for s in range(N)}
    best = None
    for order_name, gen in Z.SRC_ORDERS.items():
        mk, mo, busy, inj, events = Z.export_events(foot, RAMP_BW, gen(), flits=1)
        ok = Z.verify(busy, RAMP_BW)
        rec = dict(mk=mk, method=f"pack:{order_name}", ok=ok, busy=busy,
                   events=events, afifo_peak=0, afifo_series=[],
                   mode="zerobuf_rigid", order=None, edges=None, max_off=mo, B=B)
        if best is None or mk < best["mk"]:
            best = rec
    return best


def run_hybrid_v(B, bidir):
    """Rigid 0-buffer hybrid: B VERTICAL bands, local vertical ring + horizontal tree."""
    foot = {s: Z.fp_hybrid_v(s, B, bidir, RAMP_BW) for s in range(N)}
    best = None
    for order_name, gen in Z.SRC_ORDERS.items():
        mk, mo, busy, inj, events = Z.export_events(foot, RAMP_BW, gen(), flits=1)
        ok = Z.verify(busy, RAMP_BW)
        rec = dict(mk=mk, method=f"pack:{order_name}", ok=ok, busy=busy,
                   events=events, afifo_peak=0, afifo_series=[],
                   mode="zerobuf_rigid", order=None, edges=None, max_off=mo, B=B)
        if best is None or mk < best["mk"]:
            best = rec
    return best


def run_border(bidir):
    tag = "bi" if bidir else "uni"
    quads = quads_for(MX, "border", tag)
    if bidir:
        from sweep_quad_ring_shapes import make_quads
        quads = make_quads(BORDER_BI_CFG)
    deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
    best = None
    for order in ("natural", "interleave", "quad"):
        r = S.schedule_atomic(MX, bidir, RAMP_BW, deliv, afifo_cap=AFIFO_CAP,
                              order=order, record_events=True, quads=quads, flits=1)
        if not r.get("ok") or r["afifo_balanced"]["peak"] > AFIFO_CAP:
            continue
        rec = dict(mk=r["makespan"], method=f"atomic:{order}", ok=True,
                   events=r["events"], afifo_peak=r["afifo_balanced"]["peak"],
                   afifo_series=r["afifo_profile"]["global"],
                   mode="ring_zerobuf", order=None, edges=None, raw=r)
        if best is None or rec["mk"] < best["mk"]:
            best = rec
    return best


def run_grid(Qx, Qy, bidir):
    deliv = lambda s, b, Qx=Qx, Qy=Qy: build_grid_border(s, Qx, Qy, b)
    best = None
    for order in ("natural", "interleave", "quad"):
        r = S.schedule_atomic(MX, bidir, RAMP_BW, deliv, afifo_cap=AFIFO_CAP,
                              order=order, record_events=True, flits=1)
        if not r.get("ok") or r["afifo_balanced"]["peak"] > AFIFO_CAP:
            continue
        rec = dict(mk=r["makespan"], method=f"atomic:{order}", ok=True,
                   events=r["events"], afifo_peak=r["afifo_balanced"]["peak"],
                   afifo_series=r["afifo_profile"]["global"],
                   mode="ring_zerobuf", order=None, edges=None, raw=r,
                   Qx=Qx, Qy=Qy)
        if best is None or rec["mk"] < best["mk"]:
            best = rec
    return best


def delivery_edges(ch):
    return [(p, c) for p, kids in ch.items() for c in kids]


def classify_edges(edges, ring_edge_set):
    ring, arc, cross = [], [], []
    for p, c in edges:
        if fr.quad_of(p) != fr.quad_of(c):
            cross.append((p, c))
        elif (p, c) in ring_edge_set:
            ring.append((p, c))
        else:
            arc.append((p, c))
    return ring, arc, cross


def grid_ring_edges(Qx, Qy):
    wx, wy = MX // Qx, MY // Qy
    ring = set()
    for ry in range(Qy):
        for rx in range(Qx):
            x0, y0 = rx * wx, ry * wy
            order = fr.ham_cycle_rect(x0, y0, wx, wy)
            for i in range(len(order)):
                u, v = order[i], order[(i + 1) % len(order)]
                ring.add((u, v))
    return ring


def border_ring_edges():
    hw, hh = MX // 2, MY // 2
    ring = set()
    for qx, qy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        order = fr.ham_cycle_rect(qx * hw, qy * hh, hw, hh)
        for i in range(len(order)):
            u, v = order[i], order[(i + 1) % len(order)]
            ring.add((u, v))
    return ring


def px_py(cell=14, pad=24, top=22):
    px = lambda x: pad + x * cell + cell / 2
    py = lambda y: top + pad + (MY - 1 - y) * cell + cell / 2
    w = MX * cell + 2 * pad
    h = MY * cell + 2 * pad + top
    return px, py, w, h, cell, pad, top


def draw_edges(lines, edges, px, py, color, width=1.2, opacity=0.55, marker=None):
    mk = f' marker-end="url(#{marker})"' if marker else ""
    for p, c in edges:
        x1, y1 = px(p % MX), py(p // MX)
        x2, y2 = px(c % MX), py(c // MX)
        lines.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="{width}" opacity="{opacity}"{mk}/>')


def svg_ring_global(order):
    px, py, w, h, cell, pad, top = px_py()
    ring_e = set((order[i], order[(i + 1) % len(order)]) for i in range(len(order)))
    nh = nv = 0
    lines = [
        f'<svg width="{w}" height="{h+20}" viewBox="0 0 {w} {h+20}" xmlns="http://www.w3.org/2000/svg">',
        '<defs><marker id="ah" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#2563eb"/></marker></defs>',
        f'<text x="8" y="14" font-size="11" font-weight="bold" fill="#1e3a8a">'
        f'全局 Hamilton 环 (snake_cycle)</text>',
    ]
    for y in range(MY):
        for x in range(MX):
            lines.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="1.1" fill="#cbd5e1"/>')
    for p, c in ring_e:
        x1, y1 = px(p % MX), py(p // MX)
        x2, y2 = px(c % MX), py(c // MX)
        if p // MX == c // MX:
            col, nh = "#2563eb", nh + 1
        else:
            col, nv = "#ea580c", nv + 1
        lines.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{col}" stroke-width="1.4" marker-end="url(#ah)"/>')
    lines.append(
        f'<text x="{pad}" y="{h+12}" font-size="10" fill="#475569">'
        f'H-hop {nh} · V-hop {nv} · 无短弧/无 AFIFO</text></svg>')
    return "\n".join(lines)


def svg_mesh_scheme(title, region_rects, ring_e, arc_e, cross_e=None):
    """region_rects: list of (x0,y0,w,h,color,label)."""
    px, py, w, h, cell, pad, top = px_py()
    lines = [
        f'<svg width="{w}" height="{h+36}" viewBox="0 0 {w} {h+36}" xmlns="http://www.w3.org/2000/svg">',
        '<defs>',
        '<marker id="mr" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#2563eb"/></marker>',
        '<marker id="ma" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#ea580c"/></marker>',
        '</defs>',
        f'<text x="8" y="14" font-size="11" font-weight="bold" fill="#1e3a8a">{esc(title)}</text>',
    ]
    for x0, y0, rw, rh, col, lab in region_rects:
        lines.append(
            f'<rect x="{pad+x0*cell:.1f}" y="{top+pad+(MY-y0-rh)*cell:.1f}" '
            f'width="{rw*cell:.1f}" height="{rh*cell:.1f}" fill="{col}" '
            f'stroke="#94a3b8" stroke-width="0.8" opacity="0.55"/>')
        lines.append(
            f'<text x="{pad+(x0+rw/2)*cell:.1f}" y="{top+pad+(MY-y0-rh/2)*cell:.1f}" '
            f'text-anchor="middle" font-size="9" fill="#334155">{esc(lab)}</text>')
    for y in range(MY):
        for x in range(MX):
            lines.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="1.0" fill="#94a3b8"/>')
    draw_edges(lines, ring_e, px, py, "#2563eb", 1.3, 0.7, "mr")
    draw_edges(lines, arc_e, px, py, "#ea580c", 1.8, 0.85, "ma")
    if cross_e:
        draw_edges(lines, cross_e, px, py, "#9333ea", 1.0, 0.4)
    lines += [
        f'<rect x="{pad}" y="{h+4}" width="10" height="10" fill="#2563eb"/>'
        f'<text x="{pad+14}" y="{h+13}" font-size="10">Hamilton 环段</text>',
        f'<rect x="{pad+100}" y="{h+4}" width="10" height="10" fill="#ea580c"/>'
        f'<text x="{pad+114}" y="{h+13}" font-size="10">短弧</text>',
        f'<text x="{pad+170}" y="{h+13}" font-size="10" fill="#64748b">'
        f'橙虚线=分区边界 (AFIFO)</text>',
    ]
    # partition dashed lines
    if len(region_rects) == 2:
        _, y0, rw, rh, _, _ = region_rects[0]
        x0a = region_rects[1][0]
        if rh < MY:  # horizontal split
            yb = min(y0, region_rects[1][1])
            yy = top + pad + (MY - yb) * cell
            lines.append(
                f'<line x1="{pad}" y1="{yy:.1f}" x2="{pad+MX*cell:.1f}" y2="{yy:.1f}" '
                f'stroke="#dc2626" stroke-width="1.5" stroke-dasharray="5 4"/>')
        elif rw < MX:
            xb = min(x0a, x0)
            xx = pad + xb * cell
            lines.append(
                f'<line x1="{xx:.1f}" y1="{top+pad}" x2="{xx:.1f}" y2="{top+pad+MY*cell:.1f}" '
                f'stroke="#dc2626" stroke-width="1.5" stroke-dasharray="5 4"/>')
    lines.append("</svg>")
    return "\n".join(lines)


def svg_border():
    deliveries = {s: fr.build_border_delivery(s, True) for s in range(N)}
    all_e = set()
    for ch in deliveries.values():
        all_e.update(delivery_edges(ch))
    ring_e = border_ring_edges()
    ring, arc, cross = classify_edges(all_e, ring_e)
    rects = [
        (0, 0, 8, 8, "#eff6ff", "Q0"),
        (8, 0, 8, 8, "#f0fdf4", "Q1"),
        (0, 8, 8, 8, "#fff7ed", "Q2"),
        (8, 8, 8, 8, "#faf5ff", "Q3"),
    ]
    return svg_mesh_scheme("border (Q=4) · 四象限环 + 边界短弧", rects, ring, arc, cross)


def svg_grid(Qx, Qy, title):
    deliveries = {s: build_grid_border(s, Qx, Qy, True) for s in range(N)}
    all_e = set()
    for ch in deliveries.values():
        all_e.update(delivery_edges(ch))
    ring_e = grid_ring_edges(Qx, Qy)
    ring, arc, cross = classify_edges(all_e, ring_e)
    wx, wy = MX // Qx, MY // Qy
    rects = []
    colors = ["#eff6ff", "#f0fdf4", "#fff7ed", "#faf5ff"]
    i = 0
    for ry in range(Qy):
        for rx in range(Qx):
            rects.append((rx * wx, ry * wy, wx, wy, colors[i % 4], f"R{rx},{ry}"))
            i += 1
    return svg_mesh_scheme(title, rects, ring, arc, cross)


def svg_hybrid(B):
    """Two horizontal bands: local Hamilton ring (blue) + vertical tree (orange)."""
    R = MY // B
    px, py, w, h, cell, pad, top = px_py()
    lines = [
        f'<svg width="{w}" height="{h+36}" viewBox="0 0 {w} {h+36}" xmlns="http://www.w3.org/2000/svg">',
        '<defs>'
        '<marker id="hr" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#2563eb"/></marker>',
        '<marker id="ht" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#ea580c"/></marker></defs>',
        f'<text x="8" y="14" font-size="11" font-weight="bold" fill="#1e3a8a">'
        f'hybrid B={B} · {B} 条 {MX}×{R} 水平带：带内双向环 + 跨带纵向树</text>',
    ]
    # band rects
    band_colors = ["#eff6ff", "#f0fdf4", "#fff7ed", "#faf5ff"]
    for b in range(B):
        y0 = b * R
        lines.append(
            f'<rect x="{pad}" y="{top+pad+(MY-y0-R)*cell:.1f}" width="{MX*cell:.1f}" '
            f'height="{R*cell:.1f}" fill="{band_colors[b % 4]}" stroke="#94a3b8" '
            f'stroke-width="0.8" opacity="0.5"/>')
        lines.append(
            f'<text x="{pad+4}" y="{top+pad+(MY-y0-R/2)*cell:.1f}" font-size="9" '
            f'fill="#334155">band {b} · {MX}×{R}</text>')
    # nodes
    for y in range(MY):
        for x in range(MX):
            lines.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="1.0" fill="#94a3b8"/>')
    # local ring per band (sample from band 0 order)
    for b in range(B):
        order = Z.ham_cycle_band(R, b * R)
        for i in range(len(order)):
            u, v = order[i], order[(i + 1) % len(order)]
            x1, y1 = px(u % MX), py(u // MX)
            x2, y2 = px(v % MX), py(v // MX)
            lines.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="#2563eb" stroke-width="1.0" opacity="0.45"/>')
    # vertical tree arrows: sample columns 0, 5, 10, 15, between adjacent bands
    cols = [0, 5, 10, 15]
    for b in range(B - 1):
        y_top = b * R + R - 1        # bottom row of upper band
        y_bot = (b + 1) * R          # top row of lower band
        for x in cols:
            x1, y1 = px(x), py(y_top)
            x2, y2 = px(x), py(y_bot)
            lines.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="#ea580c" stroke-width="1.6" opacity="0.8" marker-end="url(#ht)"/>')
    # partition dashed lines
    for b in range(1, B):
        yy = top + pad + (MY - b * R) * cell
        lines.append(
            f'<line x1="{pad}" y1="{yy:.1f}" x2="{pad+MX*cell:.1f}" y2="{yy:.1f}" '
            f'stroke="#dc2626" stroke-width="1.5" stroke-dasharray="5 4"/>')
    lines += [
        f'<rect x="{pad}" y="{h+4}" width="10" height="10" fill="#2563eb"/>'
        f'<text x="{pad+14}" y="{h+13}" font-size="10">带内 Hamilton 环（双向半弧）</text>',
        f'<rect x="{pad+150}" y="{h+4}" width="10" height="10" fill="#ea580c"/>'
        f'<text x="{pad+164}" y="{h+13}" font-size="10">跨带纵向树（phase B）</text>',
        "</svg>",
    ]
    return "\n".join(lines)


def svg_hybrid_v(B):
    """B VERTICAL bands: local vertical Hamilton ring (blue) + horizontal tree (orange)."""
    C = MX // B
    px, py, w, h, cell, pad, top = px_py()
    lines = [
        f'<svg width="{w}" height="{h+36}" viewBox="0 0 {w} {h+36}" xmlns="http://www.w3.org/2000/svg">',
        '<defs>'
        '<marker id="vr" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#2563eb"/></marker>',
        '<marker id="vt" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
        '<path d="M0,0 L6,3 L0,6 z" fill="#ea580c"/></marker></defs>',
        f'<text x="8" y="14" font-size="11" font-weight="bold" fill="#1e3a8a">'
        f'hybrid B={B} (vband) · {B} 条 {C}×{MY} 纵向带：带内纵向环 + 跨带横向树</text>',
    ]
    band_colors = ["#eff6ff", "#f0fdf4", "#fff7ed", "#faf5ff"]
    for b in range(B):
        x0 = b * C
        lines.append(
            f'<rect x="{pad+x0*cell:.1f}" y="{top+pad}" width="{C*cell:.1f}" '
            f'height="{MY*cell:.1f}" fill="{band_colors[b % 4]}" stroke="#94a3b8" '
            f'stroke-width="0.8" opacity="0.5"/>')
        lines.append(
            f'<text x="{pad+(x0+C/2)*cell:.1f}" y="{top+pad+10}" font-size="9" '
            f'text-anchor="middle" fill="#334155">band {b} · {C}×{MY}</text>')
    for y in range(MY):
        for x in range(MX):
            lines.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="1.0" fill="#94a3b8"/>')
    # local vertical ring per band
    for b in range(B):
        order = Z.ham_cycle_vband(C, b * C)
        for i in range(len(order)):
            u, v = order[i], order[(i + 1) % len(order)]
            x1, y1 = px(u % MX), py(u // MX)
            x2, y2 = px(v % MX), py(v // MX)
            lines.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="#2563eb" stroke-width="1.0" opacity="0.45"/>')
    # horizontal tree arrows between adjacent bands (sample rows)
    rows = [0, 5, 10, 15]
    for b in range(B - 1):
        x_left = b * C + C - 1      # rightmost col of left band
        x_right = (b + 1) * C       # leftmost col of right band
        for y in rows:
            x1, y1 = px(x_left), py(y)
            x2, y2 = px(x_right), py(y)
            lines.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="#ea580c" stroke-width="1.6" opacity="0.8" marker-end="url(#vt)"/>')
    # partition dashed lines
    for b in range(1, B):
        xx = pad + b * C * cell
        lines.append(
            f'<line x1="{xx:.1f}" y1="{top+pad}" x2="{xx:.1f}" y2="{top+pad+MY*cell:.1f}" '
            f'stroke="#dc2626" stroke-width="1.5" stroke-dasharray="5 4"/>')
    lines += [
        f'<rect x="{pad}" y="{h+4}" width="10" height="10" fill="#2563eb"/>'
        f'<text x="{pad+14}" y="{h+13}" font-size="10">带内纵向 Hamilton 环（双向半弧）</text>',
        f'<rect x="{pad+170}" y="{h+4}" width="10" height="10" fill="#ea580c"/>'
        f'<text x="{pad+184}" y="{h+13}" font-size="10">跨带横向树（phase B，H=4）</text>',
        "</svg>",
    ]
    return "\n".join(lines)


def svg_conflict_resolution(hybrid_rec):
    """双向环 ramp=1 冲突消解示意图：d2 上 ramp 错开 + 不相交半弧 + packer 偏移。"""
    B = hybrid_rec["B"]
    R = MY // B
    n_band = MX * R
    a = n_band // 2
    bb = (n_band - 1) - a
    max_off = hybrid_rec.get("max_off", 0)
    mk = hybrid_rec["mk"]
    # --- (1) up-ramp d2 stagger timeline ---
    tl = [
        '<svg width="520" height="150" viewBox="0 0 520 150" xmlns="http://www.w3.org/2000/svg">',
        '<text x="8" y="16" font-size="11" font-weight="bold" fill="#1e3a8a">'
        '(1) 上 ramp 容量=1：双向两 flit 用 d2=1 错开注入</text>',
    ]
    # ramp capacity bars
    for cyc in range(6):
        xx = 60 + cyc * 36
        tl.append(f'<rect x="{xx}" y="40" width="32" height="46" fill="#f1f5f9" stroke="#cbd5e1"/>')
        tl.append(f'<text x="{xx+16}" y="100" font-size="9" fill="#64748b" text-anchor="middle">{cyc}</text>')
    # fwd inject at rel 0
    tl.append('<rect x="62" y="44" width="28" height="38" fill="#2563eb" opacity="0.85"/>')
    tl.append('<text x="76" y="38" font-size="9" fill="#2563eb" text-anchor="middle">fwd</text>')
    # bwd inject at rel 1 (d2=1)
    tl.append('<rect x="98" y="44" width="28" height="38" fill="#ea580c" opacity="0.85"/>')
    tl.append('<text x="112" y="38" font-size="9" fill="#ea580c" text-anchor="middle">bwd</text>')
    tl.append('<text x="76" y="120" font-size="9" fill="#475569">rel=0</text>')
    tl.append('<text x="112" y="120" font-size="9" fill="#475569">rel=1 (d2)</text>')
    tl.append('<text x="200" y="60" font-size="10" fill="#334155">'
              'ramp=1 → d2=1；ramp≥2 → d2=0（同拍注入）</text>')
    tl.append('<text x="200" y="78" font-size="10" fill="#334155">'
              '上 ramp 每 cycle ≤1 flit，满足容量。</text>')
    tl.append('</svg>')
    # --- (2) disjoint half-arc mini ring ---
    mr = [
        '<svg width="520" height="170" viewBox="0 0 520 170" xmlns="http://www.w3.org/2000/svg">',
        '<text x="8" y="16" font-size="11" font-weight="bold" fill="#1e3a8a">'
        '(2) 下 ramp：双向拆不相交半弧，每目的恰收 1 flit</text>',
    ]
    import math
    cx, cy, rad = 150, 95, 55
    # draw two arcs: fwd (blue) upper, bwd (orange) lower
    n = n_band
    s_idx = 0
    pts = [(cx + rad * math.cos(2 * math.pi * k / n - math.pi / 2),
            cy + rad * math.sin(2 * math.pi * k / n - math.pi / 2)) for k in range(n)]
    # fwd half: indices 0..a ; bwd half: 0,-1..-bb
    fwd_idx = list(range(a + 1))
    bwd_idx = [(-k) % n for k in range(bb + 1)]
    # fwd arc path
    fp = " ".join(f"{pts[k][0]:.1f},{pts[k][1]:.1f}" for k in fwd_idx)
    bp = " ".join(f"{pts[k][0]:.1f},{pts[k][1]:.1f}" for k in bwd_idx)
    mr.append(f'<polyline points="{fp}" fill="none" stroke="#2563eb" stroke-width="3"/>')
    mr.append(f'<polyline points="{bp}" fill="none" stroke="#ea580c" stroke-width="3"/>')
    # source node
    sx, sy = pts[0]
    mr.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5" fill="#dc2626"/>')
    mr.append(f'<text x="{sx+8:.1f}" y="{sy-8:.1f}" font-size="10" fill="#dc2626">s</text>')
    # sample dest on fwd
    dk = fwd_idx[len(fwd_idx) // 2]
    mr.append(f'<circle cx="{pts[dk][0]:.1f}" cy="{pts[dk][1]:.1f}" r="4" fill="#2563eb"/>')
    mr.append(f'<text x="{pts[dk][0]+6:.1f}" y="{pts[dk][1]+4:.1f}" font-size="9" fill="#2563eb">d (fwd)</text>')
    dk2 = bwd_idx[len(bwd_idx) // 2]
    mr.append(f'<circle cx="{pts[dk2][0]:.1f}" cy="{pts[dk2][1]:.1f}" r="4" fill="#ea580c"/>')
    mr.append(f'<text x="{pts[dk2][0]-40:.1f}" y="{pts[dk2][1]+4:.1f}" font-size="9" fill="#ea580c">d (bwd)</text>')
    mr.append(f'<text x="220" y="60" font-size="10" fill="#334155">'
              f'带环 n={n}：fwd 半弧 {a+1} 节点，bwd 半弧 {bb+1} 节点</text>')
    mr.append('<text x="220" y="80" font-size="10" fill="#334155">'
              '两半弧仅在 s 处相交，其余节点不相交 →</text>')
    mr.append('<text x="220" y="98" font-size="10" fill="#334155">'
              '每个目的 d 从 s 恰收到 1 个 flit，单源下 ramp 不冲突。</text>')
    mr.append('<text x="220" y="120" font-size="10" fill="#64748b">'
              '蓝=fwd 半弧，橙=bwd 半弧，红=源 s</text>')
    mr.append('</svg>')
    # --- (3) packer explanation ---
    explain = f"""
<svg width="520" height="150" viewBox="0 0 520 150" xmlns="http://www.w3.org/2000/svg">
<text x="8" y="16" font-size="11" font-weight="bold" fill="#1e3a8a">
(3) 源间冲突：刚性偏移 packer（_pack_core）逐源选注入偏移 off</text>
<text x="8" y="40" font-size="10" fill="#334155">
为每个源 s 选最小 off，使其所有 eject/link 时刻不与已放置源在任一</text>
<text x="8" y="56" font-size="10" fill="#334155">
节点下 ramp（容量 1）或任一链路（容量 1）上撞 cycle。</text>
<text x="8" y="80" font-size="10" fill="#334155">
双向半弧把每源在每条下 ramp 上占用的时间窗减半 → 256 源密排偏移仅 {max_off} cy。</text>
<text x="8" y="100" font-size="10" fill="#334155">
实测 hybrid B={B} bi：max delivery ≈ {mk - max_off} cy，packer 偏移 {max_off} cy → makespan {mk} cy。</text>
<text x="8" y="122" font-size="10" fill="#64748b">
对照：单向全局环每源占全弧 → 密排需 1474 cy；双向全局环 754 cy。</text>
</svg>"""
    return "\n".join(tl) + "\n" + "\n".join(mr) + "\n" + explain


def afifo_chart(series, mk, width=720, height=200):
    if not series:
        return "<p class='note'>无 AFIFO 等待（刚性 pack）。</p>"
    n = min(len(series), mk + 1)
    s = series[:n]
    ymax = max(max(s), AFIFO_CAP) or 1
    pad_l, pad_r, pad_t, pad_b = 48, 16, 16, 32
    iw = width - pad_l - pad_r
    ih = height - pad_t - pad_b
    pts = []
    for t, v in enumerate(s):
        px = pad_l + (t / max(n - 1, 1)) * iw
        py = pad_t + ih - (v / ymax) * ih
        pts.append(f"{px:.1f},{py:.1f}")
    cap_y = pad_t + ih - (AFIFO_CAP / ymax) * ih
    return "\n".join([
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">',
        f'<line x1="{pad_l}" y1="{pad_t+ih}" x2="{pad_l+iw}" y2="{pad_t+ih}" stroke="#cbd5e1"/>',
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t+ih}" stroke="#cbd5e1"/>',
        f'<line x1="{pad_l}" y1="{cap_y:.1f}" x2="{pad_l+iw}" y2="{cap_y:.1f}" '
        f'stroke="#dc2626" stroke-dasharray="4 3"/>',
        f'<text x="{pad_l+4}" y="{cap_y-4:.1f}" font-size="9" fill="#dc2626">cap={AFIFO_CAP}</text>',
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="#ea580c" stroke-width="1.5"/>',
        f'<text x="{pad_l+iw//2}" y="{height-4}" font-size="10" fill="#64748b" text-anchor="middle">cycle</text>',
        f'<text x="8" y="{pad_t+ih//2}" font-size="10" fill="#64748b" transform="rotate(-90 8,{pad_t+ih//2})">AFIFO 占用</text>',
        "</svg>",
    ])


def buffer_chart(series, label, width=720, height=160, ymax=None):
    if not series:
        return ""
    n = len(series)
    ymax = ymax or max(max(series), 1)
    pad_l, pad_r, pad_t, pad_b = 48, 16, 16, 28
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b
    pts = []
    for t, v in enumerate(series):
        px = pad_l + (t / max(n - 1, 1)) * iw
        py = pad_t + ih - (v / ymax) * ih
        pts.append(f"{px:.1f},{py:.1f}")
    return "\n".join([
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">',
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="#2563eb" stroke-width="1.5"/>',
        f'<text x="8" y="{pad_t+ih//2}" font-size="10" fill="#64748b" transform="rotate(-90 8,{pad_t+ih//2})">{esc(label)}</text>',
        f'<text x="{pad_l+iw//2}" y="{height-4}" font-size="10" fill="#64748b" text-anchor="middle">cycle</text>',
        "</svg>",
    ])


def events_to_std(events):
    """Normalize events to (s,p,c,t,lat,arr,kind) tuples."""
    out = []
    for ev in events:
        if len(ev) == 7:
            out.append(ev)
        else:
            s, p, c, t, lat, arr, kind = ev
            out.append((s, p, c, t, lat, arr, kind))
    return out


def is_afifo_hop(kind):
    """Cross-partition hops wait in boundary AFIFO, not router buffer."""
    return kind == 2 or kind == "cross" or kind == "afifo"


def router_hold_proof(events, makespan):
    """Max cycles a flit waits inside router after arrival (mesh hops only).

    AFIFO waits (kind==2 cross sends) are excluded — those are boundary buffers.
    """
    evs = events_to_std(events)
    arrive = {}
    for s, p, c, t, lat, arr, kind in evs:
        arrive[(s, c)] = arr
    max_hold = 0
    worst = None
    per_router_max = defaultdict(int)
    mesh_evs = []
    for s, p, c, t, lat, arr, kind in evs:
        if is_afifo_hop(kind):
            continue
        mesh_evs.append((s, p, c, t, lat, arr, kind))
        a_in = arrive.get((s, p))
        if a_in is None:
            continue
        hold = t - a_in
        if hold > max_hold:
            max_hold = hold
            worst = (s, p, t, a_in)
        if hold > per_router_max[p]:
            per_router_max[p] = hold
    mk = makespan + 1
    hold_series = [0] * mk
    router_occ = [0] * mk
    for t in range(mk):
        cnt = 0
        peak = 0
        for s, p, c, ts, lat, arr, kind in mesh_evs:
            a_in = arrive.get((s, p))
            if a_in is None:
                continue
            if a_in < t < ts:
                cnt += 1
            if ts == t:
                peak = max(peak, ts - a_in)
        router_occ[t] = cnt
        hold_series[t] = peak
    return dict(max_hold=max_hold, worst=worst, series=hold_series,
                router_occ=router_occ,
                max_per_router=max(per_router_max.values()) if per_router_max else 0)


def per_link_peak(events):
    """Peak sends per directed mesh link per cycle (0-buffer => ≤1)."""
    evs = events_to_std(events)
    link_peak = defaultdict(int)
    sends = defaultdict(lambda: defaultdict(int))
    for s, p, c, t, lat, arr, kind in evs:
        if is_afifo_hop(kind):
            continue
        lk = (p, c)
        sends[lk][t] += 1
        if sends[lk][t] > link_peak[lk]:
            link_peak[lk] = sends[lk][t]
    return max(link_peak.values()) if link_peak else 0, link_peak


def link_occupancy_series(events, makespan):
    """Per-cycle max send count on any single directed mesh link."""
    evs = events_to_std(events)
    mk = makespan + 1
    per_link = defaultdict(lambda: [0] * mk)
    for s, p, c, t, lat, arr, kind in evs:
        if is_afifo_hop(kind):
            continue
        if 0 <= t < mk:
            per_link[(p, c)][t] += 1
    occ = [0] * mk
    for series in per_link.values():
        for t, v in enumerate(series):
            occ[t] = max(occ[t], v)
    return occ


def total_directed_links():
    """Directed mesh links (each carries 1 flit/cy). 2*(MX*(MY-1)+MY*(MX-1))."""
    return 2 * (MX * (MY - 1) + MY * (MX - 1))


def utilization_series(events, makespan):
    """Average receive (eject) util and link-capacity util per cycle.

    recv_util[t]  = ejects arriving at t  / (N * RAMP_BW)   (down-ramp capacity)
    link_util[t]  = link sends at t       / total_directed_links
    """
    evs = events_to_std(events)
    mk = makespan + 1
    sends = [0] * mk
    ejects = [0] * mk
    for s, p, c, t, lat, arr, kind in evs:
        if is_afifo_hop(kind):
            continue
        if 0 <= t < mk:
            sends[t] += 1
        if 0 <= arr < mk:
            ejects[arr] += 1
    ncap = N * RAMP_BW
    lcap = total_directed_links()
    recv = [ejects[t] / ncap for t in range(mk)]
    link = [sends[t] / lcap for t in range(mk)]
    return (recv, link, max(recv), max(link), lcap, ejects, sends)


def topo_cfg_str(cset):
    return "+".join(f"{i}→{o}" for i, o in sorted(cset))


def slot_table_rows(slot_info, samples=None, full=False):
    samples = samples or [(0, 0), (7, 7), (8, 8), (15, 15), (7, 0), (0, 7)]
    pr = slot_info["per_router"]
    coords = [(x, y) for y in range(MY) for x in range(MX)] if full else samples
    rows = []
    for x, y in coords:
        p = x + MX * y
        r = pr[p]
        ser = [x for x in slot_info["series"][p] if x]
        cfgs = sorted(set(ser), key=lambda z: str(z))
        cfg_strs = "; ".join(topo_cfg_str(c) for c in cfgs)
        rows.append(
            f"<tr><td>({x},{y})</td><td>{p}</td><td>{r['period']}</td>"
            f"<td>{r['distinct']}</td><td>{len(ser)}</td>"
            f"<td style='text-align:left;font-size:.78rem'>{esc(cfg_strs[:200])}</td></tr>")
    summary = (f"P：min={slot_info['min_period']} max={slot_info['max_period']} "
               f"mean={slot_info['mean_period']:.1f}")
    return summary, rows


def slot_table_html(slot_info, full_table=False):
    summary, sample_rows = slot_table_rows(slot_info)
    heat = sm.svg_depth_heatmap(slot_info["per_router"], MX, MY, "period", cell=10)
    sample_tbl = (
        f"<table><tr><th>坐标</th><th>id</th><th>深度P</th><th>不同配置</th>"
        f"<th>非空步数</th><th>非空 (in→out) 配置</th></tr>{''.join(sample_rows)}</table>"
    )
    full_html = ""
    if full_table:
        _, all_rows = slot_table_rows(slot_info, full=True)
        full_html = (
            f"<details><summary>展开全部 {MX*MY} 个 router 时隙表（仅非空 cycle）</summary>"
            f"<div style='max-height:480px;overflow:auto'>"
            f"<table><tr><th>坐标</th><th>id</th><th>深度P</th><th>不同配置</th>"
            f"<th>非空步数</th><th>非空 (in→out) 配置</th></tr>{''.join(all_rows)}</table>"
            f"</div></details>"
        )
    return summary, heat, sample_tbl, full_html


def scheme_section(key, title, rec, diagram_svg, bidir):
    if rec is None:
        return f"<div class='card'><h2>{esc(title)}</h2><p>调度失败</p></div>"
    tag = "bi" if bidir else "uni"
    evs = events_to_std(rec["events"])
    slot = sm.slot_table_depth(evs, MX, MY, rec["mk"])
    hold = router_hold_proof(evs, rec["mk"])
    link_occ = link_occupancy_series(evs, rec["mk"])
    peak_link, _ = per_link_peak(evs)
    peak_link_ts = max(link_occ) if link_occ else 0
    slot_sum, heat, sample_tbl, full_tbl = slot_table_html(slot, full_table=True)
    buf_chart = buffer_chart(hold['router_occ'], 'router 内 flit 数')
    link_chart = buffer_chart(link_occ, '单链路 max 占用')
    recv, linku, peak_recv, peak_linku, lcap, ejects, sends = utilization_series(evs, rec["mk"])
    recv_chart = buffer_chart(recv, '平均接收利用率', height=150, ymax=1.0)
    linku_chart = buffer_chart(linku, '链路容量利用率', height=150, ymax=1.0)
    avg_recv = sum(recv) / max(len(recv), 1)
    avg_linku = sum(linku) / max(len(linku), 1)

    return f"""
<div class="card" id="{esc(key)}">
<h2>{esc(title)} <span class="tag">{tag}</span></h2>
<table class="kv">
<tr><th>makespan</th><td><b>{rec['mk']}</b> cy</td>
    <th>AFIFO 峰值</th><td>{rec['afifo_peak']} (≤{AFIFO_CAP})</td></tr>
<tr><th>调度</th><td>{esc(rec['mode'])} · {esc(rec['method'])}</td>
    <th>eject 下界</th><td>255 cy</td></tr>
<tr><th>router 内最大滞留</th><td>{hold['max_hold']} cy</td>
    <th>单链路并发峰值</th><td>{peak_link} flit（0-buffer 要求 ≤1）</td></tr>
<tr><th>平均接收利用率</th><td>{avg_recv*100:.1f}% (峰 {peak_recv*100:.1f}%)</td>
    <th>平均链路利用率</th><td>{avg_linku*100:.1f}% (峰 {peak_linku*100:.1f}%)</td></tr>
</table>
<div class="two-col">
<div>{diagram_svg}</div>
<div>
<h3>AFIFO 占用随时间</h3>
{afifo_chart(rec['afifo_series'], rec['mk'])}
</div>
</div>
<div class="two-col">
<div>
<h3>Router 缓冲占用（全网 max 在途 flit 数）</h3>
{buf_chart or '<p class="note">恒为 0</p>'}
</div>
<div>
<h3>单链路 max 发送并发随时间</h3>
{link_chart or '<p class="note">恒为 ≤1</p>'}
</div>
</div>
<div class="two-col">
<div>
<h3>平均接收利用率（eject / (N·ramp_bw)）随时间</h3>
{recv_chart}
<p class="note">每 cycle 全网 eject 数 ÷ (256×1=256)。峰值 {peak_recv*100:.1f}%，均值 {avg_recv*100:.1f}%。</p>
</div>
<div>
<h3>链路容量利用率（sends / {lcap} 定向链路）随时间</h3>
{linku_chart}
<p class="note">每 cycle 全网链路发送数 ÷ {lcap}。峰值 {peak_linku*100:.1f}%，均值 {avg_linku*100:.1f}%。</p>
</div>
</div>
<h3>0-buffer 证明</h3>
<ul class="note">
<li>调度器断言 ok={'通过' if rec['ok'] else '失败'}（calendar 无 router/link 冲突）</li>
<li>mesh hop 上 router 内滞留 max={hold['max_hold']} cy（不含 AFIFO 边界等待）</li>
<li>任一 directed mesh 链路每 cycle 发送峰值={peak_link}；逐 cycle 单链路 max={peak_link_ts}（≤1 即无链路冲突）</li>
<li>AFIFO 峰值 {rec['afifo_peak']} ≤ {AFIFO_CAP}；{'刚性 pack 无边界 AFIFO 等待' if not rec['afifo_series'] else '跨区等待在边界 AFIFO，不在 router'}</li>
</ul>
<h3>时隙表深度 P（非空 cycle · mesh in→out 拓扑）</h3>
<p class="note">{esc(slot_sum)}。P=1 表示 router 每周期至多一种转发配置（无缓冲排队所需的配置切换深度）。</p>
<div class="two-col">
<div><p class="note">P 深度热力图（16×16）</p>{heat}</div>
<div>{sample_tbl}</div>
</div>
{full_tbl}
</div>"""


CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --accent:#2563eb; }
body { font-family: system-ui, Segoe UI, sans-serif; margin:0; padding:24px 32px 48px;
       background:var(--bg); color:var(--text); line-height:1.55; max-width:1200px; }
h1 { font-size:1.65rem; margin:0 0 8px; }
h2 { font-size:1.15rem; margin:0 0 10px; color:#1e3a8a; }
h3 { font-size:1rem; margin:14px 0 8px; color:#334155; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:10px; padding:18px 22px; margin:18px 0; }
.meta { color:var(--muted); font-size:.9rem; }
.note { color:var(--muted); font-size:.88rem; }
.tag { font-size:.75rem; background:#e0e7ff; color:#3730a3; padding:2px 8px; border-radius:999px; margin-left:8px; }
table { border-collapse:collapse; width:100%; font-size:.86rem; margin:8px 0; }
th, td { border:1px solid #e2e8f0; padding:6px 10px; text-align:center; }
th { background:#f1f5f9; }
td:first-child, th:first-child { text-align:left; }
tr.best td { background:#ecfdf5; font-weight:600; }
.two-col { display:grid; grid-template-columns:1fr 1fr; gap:20px; align-items:start; }
table.kv th { width:140px; text-align:left; }
code { font-family: ui-monospace, Menlo, monospace; font-size:.85em; background:#f1f5f9; padding:2px 4px; border-radius:4px; }
@media (max-width:900px) { .two-col { grid-template-columns:1fr; } }
"""


def summary_table(rows):
    lines = [
        "<table><thead><tr><th>方案</th><th>方向</th><th>makespan</th>"
        "<th>AFIFO 峰值</th><th>router 滞留</th><th>调度</th></tr></thead><tbody>"
    ]
    best_mk = min(r["mk"] for r in rows)
    for r in rows:
        cls = " class='best'" if r["mk"] == best_mk else ""
        lines.append(
            f"<tr{cls}><td>{esc(r['name'])}</td><td>{r['tag']}</td><td>{r['mk']}</td>"
            f"<td>{r['afifo']}</td><td>{r['hold']}</td>"
            f"<td>{esc(r['method'])}</td></tr>")
    lines.append("</tbody></table>")
    return "\n".join(lines)


def main():
    setup()
    print("Running schemes...", flush=True)

    schemes = []
    # Ring bi (best ring)
    r_ring = run_ring(True)
    schemes.append(("ring_bi", "全局 Hamilton 环 (Q=1)", r_ring, True,
                    svg_ring_global(hr.snake_cycle(MX, MY))))

    # Hybrid B=2 v-band bi (global best @ ramp=1, AFIFO=0; vband < hband)
    r_hyb2 = run_hybrid_v(2, True)
    schemes.append(("hybrid_b2_bi", "hybrid B=2 纵向带环 + 横向树 (vband)", r_hyb2, True,
                    svg_hybrid_v(2)))

    # Border uni (best @ ramp=1)
    r_border_u = run_border(False)
    schemes.append(("border_uni", "border (Q=4) 四象限环 + 短弧", r_border_u, False, svg_border()))

    r_border_b = run_border(True)
    schemes.append(("border_bi", "border (Q=4) 四象限环 + 短弧", r_border_b, True, svg_border()))

    # Grid halves
    r_g12u = run_grid(1, 2, False)
    schemes.append(("grid_1x2_uni", "上下分 grid 1×2（各 16×8 半环 + 短弧）", r_g12u, False,
                    svg_grid(1, 2, "grid 1×2 上下分 · 16×8 + 16×8")))

    r_g12b = run_grid(1, 2, True)
    schemes.append(("grid_1x2_bi", "上下分 grid 1×2", r_g12b, True,
                    svg_grid(1, 2, "grid 1×2 上下分 · bi")))

    r_g21u = run_grid(2, 1, False)
    schemes.append(("grid_2x1_uni", "左右分 grid 2×1（各 8×16 半环 + 短弧）", r_g21u, False,
                    svg_grid(2, 1, "grid 2×1 左右分 · 8×16 + 8×16")))

    r_g21b = run_grid(2, 1, True)
    schemes.append(("grid_2x1_bi", "左右分 grid 2×1", r_g21b, True,
                    svg_grid(2, 1, "grid 2×1 左右分 · bi")))

    summary_rows = []
    sections = []
    for key, title, rec, bidir, svg in schemes:
        if rec:
            hold = router_hold_proof(events_to_std(rec["events"]), rec["mk"])
            summary_rows.append(dict(
                name=title.split("(")[0].strip(), tag="bi" if bidir else "uni",
                mk=rec["mk"], afifo=rec["afifo_peak"], hold=hold["max_hold"],
                method=rec["method"]))
        sections.append(scheme_section(key, title, rec, svg, bidir))

    html_doc = f"""<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>
<title>0-buffer 分叉方案详解 · 16×16</title>
<style>{CSS}</style></head><body>
<h1>16×16 Allgather：0-buffer + AFIFO≤5 方案详解</h1>
<p class="meta">Mesh 16×16，N=256，H=4 V=6 cy，跨 reticle 链路 6 cy。
下 ramp = <b>1 flit/cycle/node</b>，m=1，router 零缓冲，边界 AFIFO ≤ 5。
生成：<code>utils/gen_fork_scheme_detail_report.py</code></p>

<div class="card">
<h2>总览</h2>
<p class="note">半环+短弧：每个分区内部跑 Hamilton 环，跨分区用行/列短弧（边界 AFIFO）。
全局环：单 snake_cycle，刚性 pack，无 AFIFO。</p>
{summary_table(summary_rows)}
</div>

{''.join(sections)}

<div class="card">
<h2>双向环 ramp=1 冲突消解机制</h2>
<p class="note">以 hybrid B=2 (vband) bi 为例说明双向 Hamilton 环在下 ramp=1 flit/cy 下如何不破规则。
机制分三层：上 ramp 用 d2 错开；下 ramp 用不相交半弧；源间用刚性偏移 packer。
vband（8×16 纵向带 + 横向树 H=4）比 hband（16×8 横向带 + 纵向树 V=6）更优：带环半周长 299&lt;364，
跨带树 32&lt;48，故 makespan 334 &lt; 416。</p>
{svg_conflict_resolution(r_hyb2)}
<h3>对照数值</h3>
<table>
<tr><th>方案</th><th>方向</th><th>每源占用弧长</th><th>最大 delivery</th><th>packer 偏移</th><th>makespan</th></tr>
<tr><td>全局环 Q=1</td><td>uni</td><td>全周长 ~1084 cy</td><td>~1084 cy</td><td>大</td><td>1474 cy</td></tr>
<tr><td>全局环 Q=1</td><td>bi</td><td>半周长 ~542 cy</td><td>~542 cy</td><td>中</td><td>754 cy</td></tr>
<tr><td>hybrid B=2 hband</td><td>bi</td><td>带半周长 364 cy</td><td>364+48=412 cy</td><td>2 cy</td><td>416 cy</td></tr>
<tr class='best'><td>hybrid B=2 vband</td><td>bi</td><td>带半周长 299 cy</td><td>299+32=331 cy</td><td>{r_hyb2.get('max_off',0)} cy</td><td>{r_hyb2['mk']} cy</td></tr>
</table>
<p class="note">eject 下界 = 255 cy。hybrid B=2 vband bi 的 makespan {r_hyb2['mk']} &gt; 255，说明该方案是
<b>delivery 延迟受限</b>而非 ramp 受限——这正是它能大幅领先全局环的根本原因。
vband 把长边（16）放在廉价的 H=4 链路上做横向树，短边（8）做纵向环脊，进一步压缩 delivery。</p>
</div>

<div class="card">
<h2>说明</h2>
<ul class="note">
<li><b>时隙表深度 P</b>：非空 cycle 上 mesh (in→out) 拓扑配置的最小重复周期（见 <code>slide_metrics.slot_table_depth</code>）。</li>
<li><b>Router 缓冲曲线</b>：任一 flit 到达 router 到再次发送的滞留；0-buffer 模型下恒为 0；AFIFO 等待不计入 router。</li>
<li><b>AFIFO 曲线</b>：全网边界 AFIFO 排队 flit 数随 cycle 变化；仅 border/grid 方案非零。</li>
</ul>
</div>
</body></html>"""

    OUT.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB)", flush=True)


if __name__ == "__main__":
    main()
