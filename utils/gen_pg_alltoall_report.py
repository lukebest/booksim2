#!/usr/bin/env python3
"""HTML report for 8x6 PG packet-switched alltoall study."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path

import pg_faults_8x6 as F
import pg_routing as R

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "pg_alltoall_8x6.json"
HTML_PATH = ROOT / "results" / "report_pg_alltoall_8x6.html"

SCHEME_LABELS = {
    "xy": "M1 XY (+sacrifice)",
    "rect_xy": "M2 Rect-XY",
    "updown": "M3 Up*/Down*",
    "updown_lb": "M3 Up*/Down* + LB",
    "segment": "M4 Segment",
    "segment_lb": "M4 Segment + LB",
    "fault_ring_vc": "M5 f-ring 4VC",
    "lash": "M6 LASH",
    "lash_tor": "M6b LASH-TOR",
    "stripe_vc": "M7 Stripe dateline",
    "dual_updown": "M9 Dual Up*/Down*",
    "virtual_mesh": "M10 Virtual mesh",
}


def esc(v) -> str:
    return html.escape(str(v))


def pct(x) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.1f}%"


def _mini_xy(cols: int, rows: int, pad: float = 28, gap: float = 36):
    """Return (cell centers dict (c,r)->(x,y), width, height). row 0 at bottom."""
    w = pad * 2 + (cols - 1) * gap + 20
    h = pad * 2 + (rows - 1) * gap + 28
    centers = {}
    for r in range(rows):
        for c in range(cols):
            centers[(c, r)] = (pad + c * gap, h - pad - r * gap)
    return centers, w, h


def _node(cx, cy, fill="#2980b9", r=7, label="", label_dy=-12):
    s = (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" '
         f'stroke="#2c3e50" stroke-width="1.2"/>')
    if label:
        s += (f'<text x="{cx}" y="{cy + label_dy}" text-anchor="middle" '
              f'font-size="11" font-family="sans-serif" fill="#222">{label}</text>')
    return s


def _edge(a, b, stroke="#7f8c8d", width=2, dash="", marker=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    m = f' marker-end="url(#{marker})"' if marker else ""
    return (f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" '
            f'stroke="{stroke}" stroke-width="{width}"{d}{m}/>')


def _caption(w, h, text):
    return (f'<text x="{w / 2}" y="{h - 6}" text-anchor="middle" '
            f'font-size="11" font-family="sans-serif" fill="#444">{text}</text>')


def _defs_arrow(uid="arr", fill="#27ae60"):
    return (f'<defs><marker id="{uid}" markerWidth="7" markerHeight="7" '
            f'refX="6" refY="3.5" orient="auto">'
            f'<polygon points="0 0, 7 3.5, 0 7" fill="{fill}"/></marker></defs>')


def class_diagrams() -> dict[str, str]:
    """Two figures contrasting how each family kills the dependency cycle."""
    out = {}

    # SVG marker fill cannot inherit the line stroke (context-stroke is not
    # portable), so emit one marker per colour actually used.
    def square(uid: str, colors: list[str]):
        C, W, H = _mini_xy(2, 2, pad=44, gap=76)
        defs = "".join(
            f'<marker id="{uid}{i}" markerWidth="8" markerHeight="8" '
            f'refX="7" refY="4" orient="auto">'
            f'<polygon points="0 0, 8 4, 0 8" fill="{c}"/></marker>'
            for i, c in enumerate(colors))
        return C, W, H, [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}"><defs>{defs}</defs>']

    def arrow(a, b, color, uid, colors, shrink=11):
        import math
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        ax, ay = a[0] + dx / L * shrink, a[1] + dy / L * shrink
        bx, by = b[0] - dx / L * shrink, b[1] - dy / L * shrink
        return (f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" '
                f'y2="{by:.1f}" stroke="{color}" stroke-width="3.2" '
                f'marker-end="url(#{uid}{colors.index(color)})"/>')

    # --- turn-restriction: forbid one turn, ring is broken ---
    pal = ["#34495e", "#c0392b"]
    C, W, H, parts = square("t1", pal)
    ring = [((0, 0), (1, 0)), ((1, 0), (1, 1)),
            ((1, 1), (0, 1)), ((0, 1), (0, 0))]
    for i, (a, b) in enumerate(ring):
        col = "#c0392b" if i == 2 else "#34495e"
        parts.append(arrow(C[a], C[b], col, "t1", pal))
    p = C[(1, 1)]
    parts.append(f'<text x="{p[0] - 4}" y="{p[1] - 16}" font-size="19" '
                 f'fill="#c0392b" font-weight="700">✕</text>')
    parts.append(f'<text x="{W / 2}" y="{H / 2 + 4}" text-anchor="middle" '
                 f'font-size="11" fill="#7f8c8d">通道环依赖</text>')
    for (c, r), pt in C.items():
        parts.append(_node(*pt, fill="#2980b9", r=8))
    parts.append(_caption(W, H, "禁掉一类转弯 → 环断，但最短路也少了"))
    parts.append("</svg>")
    out["turn"] = "".join(parts)

    # --- VC layering: same paths, layer index only increases ---
    pal = ["#2980b9", "#c0392b"]
    C, W, H, parts = square("t2", pal)
    cols = ["#2980b9", "#2980b9", "#c0392b", "#c0392b"]
    for i, (a, b) in enumerate(ring):
        parts.append(arrow(C[a], C[b], cols[i], "t2", pal))
    lab = [("VC0", (0, 0), (1, 0)), ("VC1", (1, 1), (0, 1))]
    for txt, a, b in lab:
        mx = (C[a][0] + C[b][0]) / 2
        my = (C[a][1] + C[b][1]) / 2
        parts.append(f'<text x="{mx}" y="{my - 8}" text-anchor="middle" '
                     f'font-size="10" fill="#555">{txt}</text>')
    parts.append(f'<text x="{W / 2}" y="{H / 2 - 2}" text-anchor="middle" '
                 f'font-size="11" fill="#c0392b">VC 只增不减</text>')
    parts.append(f'<text x="{W / 2}" y="{H / 2 + 12}" text-anchor="middle" '
                 f'font-size="11" fill="#7f8c8d">→ 回不到 VC0</text>')
    for (c, r), pt in C.items():
        parts.append(_node(*pt, fill="#2980b9", r=8))
    parts.append(_caption(W, H, "路径不动，靠层序断环（代价：缓冲×层数）"))
    parts.append("</svg>")
    out["vc"] = "".join(parts)
    return out


def scheme_diagrams() -> dict[str, str]:
    """Compact educational SVGs keyed by scheme id."""
    out = {}

    # ---- M1 XY: L-shaped path; broken link on the XY route ----
    C, W, H = _mini_xy(4, 3)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a1")]
    # grid
    for r in range(3):
        for c in range(3):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.5))
        for c in range(4):
            if r < 2:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#bdc3c7", 1.5))
    # XY path S(0,0)->(1,0)->(2,0)->(2,1)->(2,2)=D ; break (1,0)-(2,0)
    path = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2)]
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        if a == (1, 0) and b == (2, 0):
            parts.append(_edge(C[a], C[b], "#c0392b", 3.5, "5,3"))
            mx = (C[a][0] + C[b][0]) / 2
            my = (C[a][1] + C[b][1]) / 2
            parts.append(f'<text x="{mx}" y="{my - 8}" text-anchor="middle" '
                         f'font-size="14" fill="#c0392b" font-weight="700">✕</text>')
        else:
            parts.append(_edge(C[a], C[b], "#27ae60", 3, marker="a1"))
    for r in range(3):
        for c in range(4):
            fill = "#27ae60" if (c, r) in ((0, 0), (2, 2)) else "#2980b9"
            lab = "S" if (c, r) == (0, 0) else ("D" if (c, r) == (2, 2) else "")
            parts.append(_node(*C[(c, r)], fill=fill, label=lab))
    parts.append(_caption(W, H, "先横后纵；XY 上断一跳 → 失败"))
    parts.append("</svg>")
    out["xy"] = "".join(parts)

    # ---- M2 Rect-XY: mask row/col, keep green rectangle ----
    C, W, H = _mini_xy(4, 3)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a2")]
    # sacrificed: col 1 and fault node hint
    keep = {(c, r) for c in (2, 3) for r in (0, 1, 2)}
    for r in range(3):
        for c in range(3):
            ok = (c, r) in keep and (c + 1, r) in keep
            parts.append(_edge(C[(c, r)], C[(c + 1, r)],
                               "#27ae60" if ok else "#e0e0e0", 1.8 if ok else 1))
        for c in range(4):
            if r < 2:
                ok = (c, r) in keep and (c, r + 1) in keep
                parts.append(_edge(C[(c, r)], C[(c, r + 1)],
                                   "#27ae60" if ok else "#e0e0e0", 1.8 if ok else 1))
    # XY inside rect
    for a, b in [((2, 0), (3, 0)), ((3, 0), (3, 1)), ((3, 1), (3, 2))]:
        parts.append(_edge(C[a], C[b], "#1e8449", 3, marker="a2"))
    # fault mark at (1,1)
    parts.append(_edge(C[(1, 0)], C[(1, 1)], "#c0392b", 2.5, "4,3"))
    for r in range(3):
        for c in range(4):
            if (c, r) == (1, 1):
                fill = "#c0392b"
                lab = "坏"
            elif (c, r) not in keep:
                fill = "#e67e22"
                lab = ""
            elif (c, r) == (2, 0):
                fill, lab = "#27ae60", "S"
            elif (c, r) == (3, 2):
                fill, lab = "#27ae60", "D"
            else:
                fill, lab = "#2980b9", ""
            parts.append(_node(*C[(c, r)], fill=fill, label=lab))
    parts.append(_caption(W, H, "橙=牺牲行/列；绿矩形内继续 XY"))
    parts.append("</svg>")
    out["rect_xy"] = "".join(parts)

    # ---- M3 Up*/Down*: root + labels, path up then down ----
    C, W, H = _mini_xy(3, 3, pad=32, gap=42)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a3u", "#2980b9"),
             _defs_arrow("a3d", "#e67e22")]
    for r in range(3):
        for c in range(2):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.5))
        for c in range(3):
            if r < 2:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#bdc3c7", 1.5))
    # labels = manhattan to root (1,1)
    labels = {(c, r): abs(c - 1) + abs(r - 1) for c in range(3) for r in range(3)}
    # path S(0,0) up to root (1,1) then down to D(2,2): (0,0)->(0,1)->(1,1)->(2,1)->(2,2)
    path = [(0, 0), (0, 1), (1, 1), (2, 1), (2, 2)]
    for i in range(2):
        parts.append(_edge(C[path[i]], C[path[i + 1]], "#2980b9", 3.2,
                           marker="a3u"))
    for i in range(2, 4):
        parts.append(_edge(C[path[i]], C[path[i + 1]], "#e67e22", 3.2,
                           marker="a3d"))
    for r in range(3):
        for c in range(3):
            if (c, r) == (1, 1):
                fill, lab = "#8e44ad", "根"
            elif (c, r) == (0, 0):
                fill, lab = "#27ae60", "S"
            elif (c, r) == (2, 2):
                fill, lab = "#27ae60", "D"
            else:
                fill, lab = "#2980b9", ""
            parts.append(_node(*C[(c, r)], fill=fill, label=lab))
            parts.append(
                f'<text x="{C[(c, r)][0] + 12}" y="{C[(c, r)][1] + 4}" '
                f'font-size="10" fill="#7f8c8d">d={labels[(c, r)]}</text>')
    # legend
    parts.append(
        f'<text x="8" y="14" font-size="10" fill="#2980b9">蓝=up(朝根)</text>'
        f'<text x="{W/2}" y="14" font-size="10" fill="#e67e22">橙=down(离根)</text>')
    parts.append(_caption(W, H, "合法路径：先 up，再 down（不能回头 up）"))
    parts.append("</svg>")
    out["updown"] = "".join(parts)

    # ---- M3+LB: congested edge vs detour ----
    C, W, H = _mini_xy(3, 2, pad=30, gap=48)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a3b")]
    for r in range(2):
        for c in range(2):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.5))
    for c in range(3):
        parts.append(_edge(C[(c, 0)], C[(c, 1)], "#bdc3c7", 1.5))
    # hot edge middle bottom
    parts.append(_edge(C[(0, 0)], C[(1, 0)], "#c0392b", 6))
    parts.append(
        f'<text x="{(C[(0,0)][0]+C[(1,0)][0])/2}" y="{C[(0,0)][1]+16}" '
        f'text-anchor="middle" font-size="10" fill="#c0392b">热点过载</text>')
    # detour via top
    for a, b in [((0, 0), (0, 1)), ((0, 1), (1, 1)), ((1, 1), (2, 1)), ((2, 1), (2, 0))]:
        parts.append(_edge(C[a], C[b], "#27ae60", 2.8, "4,2", marker="a3b"))
    parts.append(
        f'<text x="{(C[(0,1)][0]+C[(1,1)][0])/2}" y="{C[(0,1)][1]-14}" '
        f'text-anchor="middle" font-size="10" fill="#27ae60">改走旁路</text>')
    for r in range(2):
        for c in range(3):
            lab = "S" if (c, r) == (0, 0) else ("D" if (c, r) == (2, 0) else "")
            fill = "#27ae60" if lab else "#2980b9"
            parts.append(_node(*C[(c, r)], fill=fill, label=lab))
    parts.append(_caption(W, H, "把经过最热边的流改到较空闲路径"))
    parts.append("</svg>")
    out["updown_lb"] = "".join(parts)

    # ---- M4 Segment: column bands + forbidden turns ----
    C, W, H = _mini_xy(4, 3, pad=30, gap=38)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">']
    # band backgrounds
    for c in range(4):
        x0 = C[(c, 0)][0] - 16
        band = (c // 2) % 2
        fill = "#d6eaf8" if band == 0 else "#fdebd0"
        parts.append(
            f'<rect x="{x0}" y="{C[(0, 2)][1] - 16}" width="32" '
            f'height="{C[(0, 0)][1] - C[(0, 2)][1] + 32}" fill="{fill}" opacity="0.7"/>')
    for r in range(3):
        for c in range(3):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#95a5a6", 1.5))
        for c in range(4):
            if r < 2:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#95a5a6", 1.5))
    # forbidden turn at (1,1): N->E in even band (col1 is seg0)
    # draw incoming from south and attempted NE with X
    p = C[(1, 1)]
    parts.append(
        f'<path d="M {p[0]} {p[1] + 18} L {p[0]} {p[1]} L {p[0] + 18} {p[1]}" '
        f'fill="none" stroke="#c0392b" stroke-width="2.5"/>')
    parts.append(f'<text x="{p[0] + 14}" y="{p[1] - 10}" font-size="14" '
                 f'fill="#c0392b" font-weight="700">✕</text>')
    # allowed turn elsewhere green
    q = C[(2, 1)]
    parts.append(
        f'<path d="M {q[0]} {q[1] + 18} L {q[0]} {q[1]} L {q[0] + 18} {q[1]}" '
        f'fill="none" stroke="#27ae60" stroke-width="2.5"/>')
    parts.append(f'<text x="{q[0] + 14}" y="{q[1] - 10}" font-size="12" '
                 f'fill="#27ae60">✓</text>')
    for r in range(3):
        for c in range(4):
            parts.append(_node(*C[(c, r)]))
    parts.append(
        f'<text x="8" y="14" font-size="10" fill="#2980b9">蓝带=偶段</text>'
        f'<text x="{W/2}" y="14" font-size="10" fill="#d35400">橙带=奇段</text>')
    parts.append(_caption(W, H, "不同列带禁止不同转弯 → 打破环依赖"))
    parts.append("</svg>")
    out["segment"] = "".join(parts)

    # ---- M4+LB: reuse LB idea with band tint ----
    C, W, H = _mini_xy(3, 2, pad=30, gap=48)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a4b")]
    for c in range(3):
        x0 = C[(c, 0)][0] - 18
        fill = "#d6eaf8" if (c // 2) % 2 == 0 else "#fdebd0"
        parts.append(
            f'<rect x="{x0}" y="{C[(0,1)][1]-18}" width="36" '
            f'height="{C[(0,0)][1]-C[(0,1)][1]+36}" fill="{fill}" opacity="0.6"/>')
    for r in range(2):
        for c in range(2):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.5))
    for c in range(3):
        parts.append(_edge(C[(c, 0)], C[(c, 1)], "#bdc3c7", 1.5))
    parts.append(_edge(C[(0, 0)], C[(1, 0)], "#c0392b", 5))
    for a, b in [((0, 0), (0, 1)), ((0, 1), (1, 1)), ((1, 1), (2, 1)), ((2, 1), (2, 0))]:
        parts.append(_edge(C[a], C[b], "#27ae60", 2.8, "4,2", marker="a4b"))
    for r in range(2):
        for c in range(3):
            lab = "S" if (c, r) == (0, 0) else ("D" if (c, r) == (2, 0) else "")
            parts.append(_node(*C[(c, r)],
                               fill="#27ae60" if lab else "#2980b9", label=lab))
    parts.append(_caption(W, H, "在转向合法集合内，躲开热点边"))
    parts.append("</svg>")
    out["segment_lb"] = "".join(parts)

    # ---- M5 true f-ring, 4 VC: exactly what _fring_path emits for
    #      S(0,1)->D(4,3) with a 1x1 block at (2,1) ----
    C, W, H = _mini_xy(5, 4, pad=30, gap=40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a5x", "#2980b9"),
             _defs_arrow("a5y", "#8e44ad")]
    block = {(2, 1)}
    # fault block halo = the fault ring
    bx, by = C[(2, 1)]
    parts.append(f'<rect x="{bx - 20}" y="{by - 20}" width="40" height="40" '
                 f'fill="#fdecea" stroke="#c0392b" stroke-width="1.4" '
                 f'stroke-dasharray="4,3" rx="3"/>')
    for r in range(4):
        for c in range(4):
            if (c, r) in block or (c + 1, r) in block:
                continue
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#cfd6da", 1.4))
    for r in range(3):
        for c in range(5):
            if (c, r) in block or (c, r + 1) in block:
                continue
            parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#cfd6da", 1.4))
    xph = [(0, 1), (1, 1), (1, 2), (2, 2), (3, 2), (3, 1), (4, 1)]
    yph = [(4, 1), (4, 2), (4, 3)]
    for i in range(len(xph) - 1):
        parts.append(_edge(C[xph[i]], C[xph[i + 1]], "#2980b9", 3.2,
                           marker="a5x"))
    for i in range(len(yph) - 1):
        parts.append(_edge(C[yph[i]], C[yph[i + 1]], "#8e44ad", 3.2,
                           marker="a5y"))
    for r in range(4):
        for c in range(5):
            if (c, r) in block:
                parts.append(_node(*C[(c, r)], fill="#c0392b", r=8, label="块"))
            elif (c, r) == (0, 1):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="S"))
            elif (c, r) == (4, 3):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="D"))
            else:
                parts.append(_node(*C[(c, r)]))
    parts.append(
        f'<text x="8" y="14" font-size="10" fill="#2980b9">'
        f'蓝=X 相（东行→VC0）</text>'
        f'<text x="{W / 2 - 10}" y="14" font-size="10" fill="#8e44ad">'
        f'紫=Y 相（北行→VC2）</text>')
    parts.append(_caption(W, H, "撞块→沿环绕行→回原行续 XY；相位定 VC"))
    parts.append("</svg>")
    out["fault_ring_vc"] = "".join(parts)

    # ---- M5 aux: a broken link must retire an endpoint to become a block ----
    C, W, H = _mini_xy(3, 2, pad=30, gap=52)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">']
    for r in range(2):
        for c in range(2):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#cfd6da", 1.4))
    for c in range(3):
        parts.append(_edge(C[(c, 0)], C[(c, 1)], "#cfd6da", 1.4))
    a, b = C[(1, 0)], C[(2, 0)]
    parts.append(_edge(a, b, "#c0392b", 3, "5,3"))
    parts.append(f'<text x="{(a[0] + b[0]) / 2}" y="{a[1] - 8}" '
                 f'text-anchor="middle" font-size="13" fill="#c0392b" '
                 f'font-weight="700">✕</text>')
    parts.append(f'<rect x="{a[0] - 17}" y="{a[1] - 17}" width="34" '
                 f'height="34" fill="#fdecea" stroke="#e67e22" '
                 f'stroke-width="1.4" stroke-dasharray="4,3" rx="3"/>')
    for r in range(2):
        for c in range(3):
            if (c, r) == (1, 0):
                parts.append(_node(*C[(c, r)], fill="#e67e22", r=8,
                                   label="退休"))
            else:
                parts.append(_node(*C[(c, r)]))
    parts.append(_caption(W, H, "块模型没有「断链」概念 → 必须退休一个端点"))
    parts.append("</svg>")
    out["fring_block"] = "".join(parts)

    # ---- M6 LASH: shortest paths painted into 2 layers ----
    C, W, H = _mini_xy(3, 3, pad=30, gap=40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a6a", "#2980b9"),
             _defs_arrow("a6b", "#c0392b")]
    for r in range(3):
        for c in range(2):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#cfd6da", 1.4))
        for c in range(3):
            if r < 2:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#cfd6da", 1.4))
    # layer0 path (blue): (0,0)->(1,0)->(2,0)->(2,1)
    for a, b in [((0, 0), (1, 0)), ((1, 0), (2, 0)), ((2, 0), (2, 1))]:
        parts.append(_edge(C[a], C[b], "#2980b9", 3, marker="a6a"))
    # layer1 path (red): (0,2)->(0,1)->(1,1)->(1,2)->(2,2) — different layer
    for a, b in [((0, 2), (0, 1)), ((0, 1), (1, 1)), ((1, 1), (2, 1)),
                 ((2, 1), (2, 2))]:
        parts.append(_edge(C[a], C[b], "#c0392b", 2.6, "4,2", marker="a6b"))
    # hole
    parts.append(_node(*C[(1, 0)], fill="#c0392b", r=6, label=""))
    for r in range(3):
        for c in range(3):
            if (c, r) == (1, 0):
                continue
            lab = "S" if (c, r) in ((0, 0), (0, 2)) else (
                "D" if (c, r) in ((2, 1), (2, 2)) else "")
            parts.append(_node(*C[(c, r)],
                               fill="#27ae60" if lab else "#2980b9",
                               label=lab))
    parts.append(
        f'<text x="8" y="14" font-size="10" fill="#2980b9">蓝=层0</text>'
        f'<text x="70" y="14" font-size="10" fill="#c0392b">红=层1</text>')
    parts.append(_caption(W, H, "每对一条最短路；装进无环的最少 VC 层"))
    parts.append("</svg>")
    out["lash"] = "".join(parts)

    # ---- M7 Stripe dateline ----
    C, W, H = _mini_xy(4, 2, pad=28, gap=44)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a7a", "#2980b9"),
             _defs_arrow("a7b", "#e67e22"), _defs_arrow("a7c", "#8e44ad")]
    # band backgrounds
    for c in range(4):
        x0 = C[(c, 0)][0] - 18
        fill = ["#d6eaf8", "#d5f5e3", "#fdebd0", "#f5eef8"][c]
        parts.append(
            f'<rect x="{x0}" y="{C[(0,1)][1]-20}" width="36" '
            f'height="{C[(0,0)][1]-C[(0,1)][1]+40}" fill="{fill}" '
            f'opacity="0.75"/>')
    # datelines between cols
    for c in (1, 2, 3):
        x = (C[(c - 1, 0)][0] + C[(c, 0)][0]) / 2
        parts.append(
            f'<line x1="{x}" y1="{C[(0,1)][1]-22}" x2="{x}" '
            f'y2="{C[(0,0)][1]+18}" stroke="#7f8c8d" stroke-width="1.5" '
            f'stroke-dasharray="3,2"/>')
    for r in range(2):
        for c in range(3):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.4))
    for c in range(4):
        parts.append(_edge(C[(c, 0)], C[(c, 1)], "#bdc3c7", 1.4))
    # path S(0,0)->(1,0)->(2,0)->(3,0)->(3,1) with VC 0,1,2
    segs = [((0, 0), (1, 0), "#2980b9", "a7a"),
            ((1, 0), (2, 0), "#e67e22", "a7b"),
            ((2, 0), (3, 0), "#8e44ad", "a7c"),
            ((3, 0), (3, 1), "#8e44ad", "a7c")]
    for a, b, col, mk in segs:
        parts.append(_edge(C[a], C[b], col, 3.2, marker=mk))
    for r in range(2):
        for c in range(4):
            lab = "S" if (c, r) == (0, 0) else ("D" if (c, r) == (3, 1) else "")
            parts.append(_node(*C[(c, r)],
                               fill="#27ae60" if lab else "#2980b9",
                               label=lab))
    parts.append(
        f'<text x="8" y="14" font-size="10" fill="#2980b9">VC0</text>'
        f'<text x="48" y="14" font-size="10" fill="#e67e22">VC1</text>'
        f'<text x="88" y="14" font-size="10" fill="#8e44ad">VC2…</text>')
    parts.append(_caption(W, H, "竖条带；每跨一条 dateline，VC +1"))
    parts.append("</svg>")
    out["stripe_vc"] = "".join(parts)

    return out


def mesh_svg(scenario: dict, sacrificed: list[int], loads: dict | None = None,
             w: int = 320, h: int = 260) -> str:
    mx, my = F.MX, F.MY
    pad = 20
    cw = (w - 2 * pad) / mx
    ch = (h - 2 * pad) / my
    dead = set(scenario.get("dead_nodes", []))
    sac = set(sacrificed or [])
    dead_links = {frozenset(l) for l in scenario.get("dead_links", [])}
    max_ld = max(loads.values()) if loads else 1

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
             f'viewBox="0 0 {w} {h}">']
    # links
    for y in range(my):
        for x in range(mx):
            a = F.nid(x, y)
            for b in F.grid_neighbors(a):
                if b < a:
                    continue
                bx, by = F.coord(b)
                x1, y1 = pad + (x + 0.5) * cw, pad + (y + 0.5) * ch
                x2, y2 = pad + (bx + 0.5) * cw, pad + (by + 0.5) * ch
                if frozenset((a, b)) in dead_links:
                    parts.append(
                        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                        f'stroke="#c0392b" stroke-width="2" '
                        f'stroke-dasharray="4,3"/>')
                else:
                    ld = 0
                    if loads:
                        ld = max(loads.get((a, b), 0), loads.get((b, a), 0))
                    alpha = 0.15 + 0.85 * (ld / max_ld) if loads else 0.35
                    parts.append(
                        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                        f'stroke="rgba(52,73,94,{alpha:.2f})" '
                        f'stroke-width="1.5"/>')
    # nodes
    for y in range(my):
        for x in range(mx):
            n = F.nid(x, y)
            cx, cy = pad + (x + 0.5) * cw, pad + (y + 0.5) * ch
            r = min(cw, ch) * 0.28
            if n in dead:
                fill = "#c0392b"
            elif n in sac:
                fill = "#e67e22"
            else:
                fill = "#2980b9"
            parts.append(
                f'<rect x="{cx - r}" y="{cy - r}" width="{2 * r}" '
                f'height="{2 * r}" rx="2" fill="{fill}" '
                f'stroke="#2c3e50" stroke-width="0.8"/>')
    parts.append("</svg>")
    return "".join(parts)


def main():
    data = json.loads(JSON_PATH.read_text())
    meta = data["meta"]
    rows = data["rows"]
    golden = meta["golden"]

    # Index primary rows (not q_sensitivity)
    primary = [r for r in rows if not r.get("q_sensitivity")]
    qrows = [r for r in rows if r.get("q_sensitivity")]
    diagrams = scheme_diagrams()
    cls_fig = class_diagrams()

    def scheme_block(title_html: str, key: str, body_html: str,
                     extra_key: str | None = None) -> str:
        fig = diagrams.get(key, "")
        if extra_key:
            fig += diagrams.get(extra_key, "")
        return (
            f'<div class="scheme"><h4>{title_html}</h4>'
            f'<div class="scheme-body"><div class="scheme-text">{body_html}</div>'
            f'<div class="scheme-fig">{fig}</div></div></div>'
        )

    by_key = defaultdict(list)
    for r in primary:
        by_key[(r["scenario"], r["semantics"], r["m"], r["Q"])].append(r)

    scenarios = F.all_scenarios()
    scen_map = {s["name"]: s for s in scenarios}

    def feasible_for(scen_name: str, sem: str, m: int) -> list[dict]:
        return [r for r in primary
                if r["scenario"] == scen_name and r["semantics"] == sem
                and r["m"] == m and r["Q"] == 19
                and r.get("makespan") is not None]

    def pareto(cands: list[dict]) -> list[dict]:
        """Non-dominated on (n_sacrificed, makespan) — both minimised."""
        keep = []
        for r in cands:
            if not any(o is not r
                       and o["n_sacrificed"] <= r["n_sacrificed"]
                       and o["makespan"] <= r["makespan"]
                       and (o["n_sacrificed"] < r["n_sacrificed"]
                            or o["makespan"] < r["makespan"])
                       for o in cands):
                keep.append(r)
        # one entry per (sac, mk) corner, cheapest scheme label wins
        seen, out = set(), []
        for r in sorted(keep, key=lambda r: (r["n_sacrificed"], r["makespan"])):
            k = (r["n_sacrificed"], r["makespan"])
            if k not in seen:
                seen.add(k)
                out.append(r)
        return out

    def optimal_table(sem: str, m: int) -> str:
        head = (
            "<tr>"
            "<th class='l'>场景</th>"
            "<th class='l'>推荐方案"
            "<div class='sub'>牺牲最少 → 再快</div></th>"
            "<th>牺牲</th><th>A</th><th>VC</th><th>makespan</th>"
            "<th title='raw_slowdown = mk/mk_golden − 1'>"
            "raw"
            "<div class='sub'>相对健康 XY</div></th>"
            "<th title='irregularity_penalty = mk/LB_same_A − 1'>"
            "irreg"
            "<div class='sub'>相对同 A 下界</div></th>"
            "<th class='l'>Pareto 备选 方案(牺牲,makespan)</th>"
            "</tr>")
        body = []
        for scen in scenarios:
            cands = feasible_for(scen["name"], sem, m)
            if not cands:
                body.append(f"<tr><td class='l'>{esc(scen['name'])}</td>"
                            f"<td colspan='8' class='bad'>无可行方案</td></tr>")
                continue
            best = min(cands, key=lambda r: (r["n_sacrificed"], r["makespan"]))
            pf = pareto(cands)
            alts = " · ".join(
                f"{SCHEME_LABELS.get(r['scheme'], r['scheme']).split()[0]}"
                f"({r['n_sacrificed']},{r['makespan']})"
                for r in pf if r is not best)
            body.append(
                "<tr>"
                f"<td class='l'>{esc(scen['name'])}</td>"
                f"<td class='l'><b>"
                f"{esc(SCHEME_LABELS.get(best['scheme'], best['scheme']))}"
                f"</b></td>"
                f"<td>{best['n_sacrificed']}</td>"
                f"<td>{best['n_compute_used']}</td>"
                f"<td>{best.get('num_vc', 1)}</td>"
                f"<td><b>{best['makespan']}</b></td>"
                f"<td>{pct(best.get('raw_slowdown'))}</td>"
                f"<td>{pct(best.get('irregularity_penalty'))}</td>"
                f"<td class='l sub'>{esc(alts) or '—（推荐方案同时最快）'}</td>"
                "</tr>")
        return (f"<table><thead>{head}</thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    def scheme_matrix(sem: str, m: int) -> str:
        schemes = []
        for r in primary:
            if r["scheme"] not in schemes:
                schemes.append(r["scheme"])
        head = ("<tr><th>场景</th>" +
                "".join(f"<th>{esc(SCHEME_LABELS.get(s, s))}</th>"
                        for s in schemes) + "</tr>")
        body = []
        for scen in scenarios:
            cells = [f"<td class='l'>{esc(scen['name'])}</td>"]
            for sch in schemes:
                hit = next((r for r in primary
                            if r["scenario"] == scen["name"]
                            and r["semantics"] == sem
                            and r["m"] == m and r["Q"] == 19
                            and r["scheme"] == sch), None)
                if hit is None or hit.get("makespan") is None:
                    cells.append("<td class='bad'>INF</td>")
                else:
                    cells.append(
                        f"<td title='sac={hit['n_sacrificed']} "
                        f"A={hit['n_compute_used']} "
                        f"VC={hit.get('num_vc', 1)} "
                        f"max_load={hit.get('max_load')} "
                        f"irreg={pct(hit.get('irregularity_penalty'))}'>"
                        f"{hit['makespan']}"
                        f"<div class='sub'>{pct(hit.get('raw_slowdown'))} "
                        f"| sac {hit['n_sacrificed']}</div></td>"
                    )
            body.append("<tr>" + "".join(cells) + "</tr>")
        return (f"<table class='matrix'><thead>{head}</thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    # SVG gallery: one per scenario under dead + updown
    gallery = []
    for scen in scenarios:
        hit = next((r for r in primary
                    if r["scenario"] == scen["name"]
                    and r["semantics"] == "dead"
                    and r["scheme"] == "updown"
                    and r["m"] == 1 and r["Q"] == 19), None)
        sac = hit["sacrificed"] if hit else []
        # rebuild loads for updown
        pg = F.expand_pg(scen, "dead")
        sol = R.solve_scheme(pg, "updown")
        loads = R.link_loads(sol["paths"]) if sol["feasible"] else None
        svg = mesh_svg(scen, sol.get("sacrificed", sac), loads)
        mk = hit["makespan"] if hit else "—"
        gallery.append(
            f"<figure><figcaption>{esc(scen['name'])} · updown · "
            f"mk={mk} · sac={sol.get('n_sacrificed', 0)}</figcaption>"
            f"{svg}</figure>"
        )

    q_table = ""
    if qrows:
        head = ("<tr><th>场景</th><th>方案</th><th>Q=4</th>"
                "<th>Q=8</th><th>Q=19</th></tr>")
        # also pull Q=19 from primary
        grouped = defaultdict(dict)
        for r in qrows + [r for r in primary if r["m"] == 1
                          and r["semantics"] == "dead"
                          and r["scheme"] in ("xy", "updown", "segment")
                              and r["scenario"] in {
                                  "link_center_1", "node_center_1x1",
                                  "node_corner_2x2"}]:
            if r.get("makespan") is None and r["Q"] != 19:
                pass
            grouped[(r["scenario"], r["scheme"])][r["Q"]] = r.get("makespan")
        body = []
        for (sc, sch), qm in sorted(grouped.items()):
            body.append(
                f"<tr><td class='l'>{esc(sc)}</td>"
                f"<td class='l'>{esc(SCHEME_LABELS.get(sch, sch))}</td>"
                f"<td>{qm.get(4, '—')}</td>"
                f"<td>{qm.get(8, '—')}</td>"
                f"<td>{qm.get(19, '—')}</td></tr>"
            )
        q_table = (f"<table><thead>{head}</thead>"
                   f"<tbody>{''.join(body)}</tbody></table>")

    # Feasibility / sacrifice stats
    feas_counts = defaultdict(lambda: [0, 0])
    for r in primary:
        if r["Q"] != 19 or r["m"] != 1:
            continue
        a, b = feas_counts[r["scheme"]]
        if r.get("makespan") is not None:
            feas_counts[r["scheme"]] = [a + 1, b + r["n_sacrificed"]]
        else:
            feas_counts[r["scheme"]] = [a, b]

    feas_html = "<ul>" + "".join(
        f"<li><b>{esc(SCHEME_LABELS.get(s, s))}</b>: "
        f"{feas_counts[s][0]} feasible rows (m=1,Q=19), "
        f"total sac-node-instances={feas_counts[s][1]}</li>"
        for s in SCHEME_LABELS if s in feas_counts
    ) + "</ul>"

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>8×6 PG 分组交换 Alltoall</title>
<style>
body {{ font-family: "IBM Plex Sans", "Noto Sans SC", sans-serif;
       margin: 2rem; color: #1a1a1a; background: #fafafa; line-height: 1.45; }}
h1,h2,h3 {{ font-family: "IBM Plex Serif", "Noto Serif SC", serif; }}
table {{ border-collapse: collapse; margin: 1rem 0; font-size: 0.85rem;
        background: #fff; }}
th,td {{ border: 1px solid #ddd; padding: 0.35rem 0.5rem; text-align: right; }}
th {{ background: #eef2f5; }}
td.l, th.l {{ text-align: left; }}
td.bad {{ color: #c0392b; font-weight: 600; }}
.sub {{ font-size: 0.7rem; color: #666; }}
.note {{ color: #555; max-width: 52rem; }}
.scheme {{ max-width: 56rem; margin: 0.8rem 0 1.2rem; padding: 0.75rem 1rem;
           background: #fff; border: 1px solid #e0e0e0; border-left: 3px solid #2980b9; }}
.scheme h4 {{ margin: 0 0 0.4rem; }}
.scheme p {{ margin: 0.35rem 0; }}
.scheme-body {{ display: flex; flex-wrap: wrap; gap: 1rem; align-items: flex-start; }}
.scheme-text {{ flex: 1 1 16rem; min-width: 14rem; }}
.scheme-fig {{ flex: 0 0 auto; background: #fafbfc; border: 1px solid #e8e8e8;
               padding: 0.35rem; border-radius: 4px; }}
.scheme-fig svg {{ display: block; }}
.cls {{ display: flex; flex-wrap: wrap; gap: 1rem; max-width: 60rem;
        margin: 1rem 0 1.4rem; }}
.cls-card {{ flex: 1 1 24rem; background: #fff; border: 1px solid #e0e0e0;
             border-top: 3px solid #8e44ad; padding: 0.75rem 1rem; }}
.cls-card h3 {{ margin: 0 0 0.4rem; font-size: 1rem; }}
.cls-card p {{ margin: 0.35rem 0; font-size: 0.9rem; }}
.cls-fig {{ text-align: center; margin: 0.3rem 0 0.5rem; }}
.cls-fig svg {{ display: inline-block; }}
table.vctab {{ font-size: 0.8rem; margin: 0.3rem 0; }}
table.vctab td {{ padding: 0.15rem 0.45rem; }}
.gallery {{ display: flex; flex-wrap: wrap; gap: 1rem; }}
figure {{ margin: 0; background: #fff; padding: 0.5rem;
         border: 1px solid #e0e0e0; }}
figcaption {{ font-size: 0.75rem; margin-bottom: 0.25rem; }}
code {{ background: #eee; padding: 0.1rem 0.3rem; }}
</style></head><body>
<h1>8×6 分组交换 NoC：Partial-Good 解决方案与 Alltoall 性能劣化</h1>
<p class="note">几何 <code>{meta['mx']}×{meta['my']}</code>，
H={meta['H']} V={meta['V']} RAMP={meta['RAMP']} RAMP_BW={meta['RAMP_BW']}。
故障模型：link 1/2/3 × corner/edge/center，node 1×1/2×2/3×3（不含 quadrant）。
corner 链路故障在角节点 (0,0) 的入射边：(0,0)-(1,0)、(0,0)-(0,1)，第 3 条为 (1,0)-(1,1)。
Q = 入端口 FIFO 深度 / 出链路 credit 初值；默认 Q=19 = 2·V+1（V=9），
足以覆盖最长链路的 credit 往返，链路可跑满 1 flit/cy。
硬性约束：无死锁（CDG 无环）+ 保序（每 (src,dst) 单路径 wormhole）。
不满足时可牺牲 good 节点恢复。Golden（健康 XY）：
m=1 → <b>{golden.get('1', golden.get(1))}</b> cy，
m=5 → <b>{golden.get('5', golden.get(5))}</b> cy。
生成于 {esc(meta.get('generated_at',''))}，耗时 {meta.get('elapsed_s')}s。
</p>

<h2>1. 故障目录与 PG 语义</h2>
<ul>
<li><b>dead</b>：故障节点 PE+router+链路全失效（严格 ring_report）</li>
<li><b>transit</b>：PE 不参与 alltoall，router/链路仍可转发</li>
<li>图例：蓝=存活计算节点，红=故障节点，橙=牺牲的 good 节点，红虚线=故障链路，
链路透明度∝ updown 方案有向负载</li>
</ul>
<div class="gallery">{''.join(gallery)}</div>

<h2>2. PG 方案详解</h2>
<p class="note">实现见 <code>utils/pg_routing.py</code>。所有进入 DES 的表必须同时满足：
CDG 无环（无死锁）、每 (src,dst) 唯一路径（保序）、compute 集合连通。
失败时由统一牺牲恢复器禁用额外 good 节点（边界 → 整行/整列 → 矩形屏蔽）。</p>

<p class="note"><b>保序不排斥 VC。</b>保序真正要求的只是「每个 (src,dst) 一条固定路径、
且沿路 VC 序列确定」——只要 VC 是 (src,dst) 的函数而非 per-packet 动态选择，
同一对的包就不会跨 VC 乱序。因此本研究的方案按<b>断环手段</b>分成两大类：</p>

<div class="cls">
  <div class="cls-card">
    <h3>A 类 · 转向限制（1 VC）</h3>
    <div class="cls-fig">{cls_fig['turn']}</div>
    <p>mesh 的死锁来自「东→北→西→南→东」这样的通道环。A 类的做法是
    <b>删掉环上的某一类转弯</b>，让环无法闭合。</p>
    <p><b>代价：</b>被删的转弯同时也删掉了一批最短路，绕路变长、负载更不均。
    洞越大，被迫绕得越远。</p>
    <p><b>成员：</b>M1 XY、M2 Rect-XY、M3 Up*/Down*、M4 Segment。</p>
  </div>
  <div class="cls-card">
    <h3>B 类 · VC 分层（多 VC）</h3>
    <div class="cls-fig">{cls_fig['vc']}</div>
    <p>不动路径，而是给通道<b>编层号</b>，规定报文沿路层号<b>只增不减</b>。
    环要闭合就必须从高层回到低层，而这被禁止 → 无环。</p>
    <p><b>代价：</b>每层要独立缓冲与 credit，面积随层数放大；
    换来的是路径可以贴近最短路。</p>
    <p><b>成员：</b>M5 真 f-ring（4 VC）、M6 LASH / M6b LASH-TOR（1–2 VC）、
    M7 条带 dateline（约 5–6 VC）、M9 双向 Up*/Down*（2 VC）、
    M10 虚拟规则网格（2 VC）。</p>
  </div>
</div>

<h3>2.1 A 类：转向限制</h3>

{scheme_block("M1 — XY（<code>xy</code>）", "xy", '''
<p><b>思想：</b>坚持维序路由（DOR）：先走完 X，再走 Y；硬件几乎不用改路由逻辑。</p>
<p><b>路径：</b>对每个 (s,d) 严格按 XY 折线前进；所需 hop 被故障删除则整表失败，进入牺牲恢复。</p>
<p><b>无死锁：</b>完整矩形上 XY 的 CDG 无环；残图上仍以 CDG 硬校验。</p>
<p><b>特征：</b>中心/角链路一断极易「穿不过」；恢复时常退化成与 M2 类似的大矩形牺牲。
用于量化「坚持 XY 硬件」要付多少牺牲代价。</p>
''')}

{scheme_block("M2 — Rect-XY（<code>rect_xy</code>）", "rect_xy", '''
<p><b>思想：</b>不在破损拓扑上绕路，而是裁成仍规则的子矩形，矩形内继续跑 XY。</p>
<p><b>做法：</b>(1) 标出故障触及的行/列；(2) 在剩余行、列中各取最长连续段，叉成最大轴对齐矩形；
(3) 矩形外原计算节点全部记为 <code>forced_sacrificed</code>；(4) 矩形内生成 XY 全表。</p>
<p><b>无死锁：</b>子矩形上经典 XY，CDG 无环。</p>
<p><b>特征：</b>牺牲粗、可预测；raw slowdown 常为负是因为参与者变少——应看
<code>irregularity_penalty</code> 与 <code>sacrifice_cost</code>。</p>
''')}

{scheme_block("M3 — Up*/Down*（<code>updown</code>）", "updown", '''
<p><b>思想：</b>在存活路由图上建 BFS 生成树，用「先上后下」限制转向，保证不规则连通图上的无死锁确定性路由。</p>
<p><b>做法：</b>(1) 根 = 路由图中度最大节点；(2) <code>label(n)</code> = 到根 BFS 距离，
朝根为 up、离根为 down、同层侧向视为 down；(3) 合法路径 = 若干 up 之后只能 down；
(4) 约束下 BFS 取最短合法路径。</p>
<p><b>无死锁：</b>Up*/Down* 按构造 CDG 无环。</p>
<p><b>特征：</b>link/node 故障下通常<strong>零牺牲</strong>即可全表可行，是保住计算规模的主推荐；
路径往往比 XY 更绕、负载更不均，故 raw slowdown 较高。</p>
''')}

{scheme_block("M3+LB — Up*/Down* + 负载均衡（<code>updown_lb</code>）", "updown_lb", '''
<p><b>在 M3 路径表上后处理：</b>统计有向边 alltoall 对数负载；每轮重排途经最热边的若干 (s,d)，
用负载感知 Dijkstra（边权 ≈ 1+负载）换路；每轮后整表再校验 CDG。失败则回退。</p>
<p><b>特征：</b>目标是压低最大链路负载；在本 8×6 上对 median makespan 改善通常很小
（Up*/Down* 合法路径集合较窄）。</p>
''')}

{scheme_block("M4 — Segment / 奇偶转向（<code>segment</code>）", "segment", '''
<p><b>思想：</b>简化 segment-based / odd-even 族：按列带施加不同转向禁令，打破 mesh 环依赖。</p>
<p><b>转向规则</b>（列段宽 2，<code>seg=(x//2)%2</code>）：直行允许、180° 禁止；
偶段禁 北→东 / 南→西；奇段禁 北→西 / 南→东。路径 = 约束下最短路。</p>
<p><b>无死锁：</b>完整 mesh 上属奇偶转向模型族；破损后仍 CDG 硬校验，不通则牺牲恢复。</p>
<p><b>特征：</b>介于 XY 与 Up*/Down*——有时零牺牲，中心故障时常需矩形化。</p>
''')}

{scheme_block("M4+LB — Segment + 负载均衡（<code>segment_lb</code>）", "segment_lb", '''
<p>与 M3+LB 相同流程，起点换成 M4 路径表；同样受转向合法集合限制。</p>
''')}

<h3>2.2 B 类：VC 分层</h3>

{scheme_block("M5 — 真 Fault-ring + 4 VC（<code>fault_ring_vc</code>）",
              "fault_ring_vc", '''
<p><b>思想：</b>Boppana–Chalasani 式容错 e-cube。把所有故障吸收进<b>矩形故障块</b>，
块周围一圈健康节点构成 <i>fault ring</i>；底层仍是原封不动的 XY，
只有撞上块的报文才沿环绕到对面，然后接着走 XY。</p>
<p><b>四条 VC 怎么分：</b>按<b>相位 × 方向</b>，整条路径上确定，因此保序。</p>
<table class="vctab"><tbody>
<tr><td>VC0</td><td class="l">X 相 · 东行</td><td>VC1</td><td class="l">X 相 · 西行</td></tr>
<tr><td>VC2</td><td class="l">Y 相 · 北行</td><td>VC3</td><td class="l">Y 相 · 南行</td></tr>
</tbody></table>
<p><b>为什么无死锁（可证）：</b>X 相的绕行只会「竖着走」或「朝本报文的 X 方向走」，
所以 VC0 里<b>每条横向通道都朝东</b>。环要闭合就得让 x 回到原点，于是环里不能有横向通道；
剩下纯竖向的环又需要 180° 掉头，而构造上不产生掉头 → VC0 无环（VC1 对称）。
Y 相镜像同理，VC2 内每条竖向通道都朝北。又因为报文只会从 X 相走向 Y 相、绝不回头，
依赖只会从 VC0/VC1 流向 VC2/VC3 → 整张 CDG 无环。</p>
<p><b>链路故障要付牺牲：</b>块模型里没有「两个活节点之间断了一条链」这种状态，
必须退休一个端点把它变成 1×1 块（右下图）。这是 M5 相对 M3 的固有代价：
纯节点故障零牺牲，纯链路故障牺牲 1–4 个好节点。</p>
<p><b>固有开销：</b>绕行后要回到原来那一行才继续 XY（图中 (1,2)→(3,2) 再下到 (3,1)），
这正是换取「X 相严格单调 ⇒ 4 VC 可证无死锁」所付的绕路。</p>
''', extra_key="fring_block")}

{scheme_block("M6 — LASH（<code>lash</code>）", "lash", '''
<p><b>思想：</b>Skeie 等 Layered Shortest Path。每对取一条<strong>最短路</strong>（可绕障），
再把全部路径贪心装进尽可能少的 VC 层，使<strong>每层 CDG 无环</strong>。
路径质量与无死锁解耦——断环靠分层，不靠砍转弯。</p>
<p><b>做法：</b>(1) 存活路由图上 BFS 最短路；(2) 按路径长度降序，尝试放入已有层，
加入后若该层 CDG 仍无环则收下，否则开新层；(3) 整条路径使用同一层号（常数 VC）
→ 保序。本 8×6 上实测通常 <b>1–2 层</b>。</p>
<p><b>与 M5 差别：</b>不强制矩形块、不强制绕回原行；链路故障只需牺牲孤立节点
（度 0），不必把端点做成块。负载往往低于 f-ring。</p>
''')}

{scheme_block("M6b — LASH-TOR（<code>lash_tor</code>）", "lash", '''
<p><b>思想：</b>在 LASH 上允许路径<strong>中途升一层</strong>（Trail / Transition On Route）：
hop 的 VC 沿路单调不减，从而把本来需要新开一层的路径塞进已有层。</p>
<p><b>做法：</b>先尝试整路径入单层；失败则枚举分割点，前半段层 <code>lo</code>、后半段层
<code>hi≥lo</code>，分别维护层内 CDG。本 8×6 上 LASH 已是 1–2 层，TOR 收益通常很小。</p>
''')}

{scheme_block("M7 — 条带 dateline（<code>stripe_vc</code>）", "stripe_vc", '''
<p><b>思想：</b>竖向条带 + 边界 dateline。路径优先 XY，不通则最短路；
每水平穿过一条 dateline，<code>VC += 1</code>（沿路单调不减）。</p>
<p><b>做法：</b>dateline 放在每 2 列边界，并叠加故障列邻边；若稀疏 dateline 下
CDG 仍有环，则加密到每个列边界。VC 数 ≈ 跨越数+1（本网格约 5–6）。</p>
<p><b>特征：</b>实现极简、路径贴近最短；VC 数高于 LASH/f-ring，缓冲面积更大。</p>
''')}

{scheme_block("M9 — 双向 Up*/Down*（<code>dual_updown</code>）", "updown", '''
<p><b>思想：</b>VC0 跑经典 Up*/Down*（先上后下），VC1 跑对称的 Down*/Up*（先下后上）；
每对选更短的那条，整路径固定在所选 VC → 保序。</p>
<p><b>无死锁：</b>两套规则各自 CDG 无环，且路径不跨 VC 混用。</p>
<p><b>特征：</b>固定 2 VC，实现比 LASH 简单；路径短于单层 Up*/Down*，但通常仍长于最短路族。</p>
''')}

{scheme_block("M10 — 虚拟规则网格（<code>virtual_mesh</code>）", "xy", '''
<p><b>思想：</b>上层仍看完整 8×6 逻辑 mesh 与 XY；物理上缺失的逻辑边用<strong>固定最短绕路</strong>
替换。逻辑路径遇洞则跳过死节点并桥接。</p>
<p><b>VC：</b>到达目的列之前的物理 hop → VC0（逻辑 X 相），之后 → VC1（逻辑 Y 相）。</p>
<p><b>特征：</b>软件映射可保持规则 mesh；链路故障友好；大洞时绕路变长，CDG 硬校验。</p>
''')}

<h3>2.3 横向对比</h3>
<table>
<thead><tr><th>类</th><th>方案</th><th>路由本质</th><th>VC</th><th>硬件改动</th><th>典型牺牲</th><th>适用意图</th></tr></thead>
<tbody>
<tr><td>A</td><td class="l">M1 XY</td><td class="l">严格先 X 后 Y</td><td>1</td><td class="l">最小（原 XY）</td><td>高</td><td class="l">量化不改路由的代价</td></tr>
<tr><td>A</td><td class="l">M2 Rect-XY</td><td class="l">裁矩形 + XY</td><td>1</td><td class="l">最小</td><td>固定偏高</td><td class="l">规整化、可预测</td></tr>
<tr><td>A</td><td class="l">M3 Up*/Down*</td><td class="l">树标号 + 先上后下</td><td>1</td><td class="l">路由表/逻辑</td><td>通常 0</td><td class="l">零 VC 成本保规模</td></tr>
<tr><td>A</td><td class="l">M3+LB / M4 / M4+LB</td><td class="l">转向限制 ± LB</td><td>1</td><td class="l">同左</td><td>中～高</td><td class="l">折中</td></tr>
<tr><td>B</td><td class="l">M5 真 f-ring</td><td class="l">矩形块 + XY 环绕，相位×方向</td><td>4</td><td class="l">4 VC + 绕障</td><td>节点洞 0；链路 1–4</td><td class="l">保 XY 硬件语义</td></tr>
<tr><td>B</td><td class="l">M6 LASH</td><td class="l">最短路 + 贪心装层</td><td><b>1–2</b></td><td class="l">少 VC + 离线表</td><td>通常仅孤立点</td><td class="l">VC 性价比</td></tr>
<tr><td>B</td><td class="l">M6b LASH-TOR</td><td class="l">LASH + 中途升层</td><td>1–2</td><td class="l">同 LASH</td><td>同 LASH</td><td class="l">再压层数（收益有限）</td></tr>
<tr><td>B</td><td class="l">M7 Stripe</td><td class="l">最短/XY + 跨带 VC+1</td><td>5–6</td><td class="l">多 VC，逻辑简单</td><td>通常仅孤立点</td><td class="l">面积换极限性能</td></tr>
<tr><td>B</td><td class="l">M9 Dual UD</td><td class="l">UD / DU 双层，按对选</td><td>2</td><td class="l">2 VC + 双规则</td><td>通常 0</td><td class="l">易实现的树路由增强</td></tr>
<tr><td>B</td><td class="l">M10 Virtual mesh</td><td class="l">逻辑 XY + 物理绕路</td><td>2</td><td class="l">2 VC + 绕路表</td><td>链路友好</td><td class="l">保留规则映射</td></tr>
</tbody>
</table>

<h3>2.4 方案可行性与牺牲代价（m=1, Q=19）</h3>
{feas_html}

<h2>3. 每场景最优方案选择</h2>
<p class="note">判据按用户口径：<b>先看牺牲节点数，再看 makespan</b>。这也让比较更公平——
牺牲数相同意味着参与 alltoall 的节点数 A 相同，makespan 才可直接对比
（A 变小会让 makespan 无偿变好，见 M2）。
「Pareto 备选」列出所有<b>非受支配</b>的 (牺牲, makespan) 组合：
若愿意多牺牲若干节点换更快，就从这里挑。</p>
<p class="note"><b>表头 raw / irreg 百分比含义：</b></p>
<ul class="note">
<li><b>raw</b>（<code>raw_slowdown = mk / mk_golden − 1</code>）：相对<strong>健康 8×6 XY</strong> golden 的变慢比例。
例如 <code>+20.0%</code> 表示 makespan 比 golden 长 20%；
<code>−30.0%</code> 表示比 golden 还短——通常因为牺牲后参与者 A 变少、总流量按 A² 下降，
<strong>不是</strong>路由变好。跨场景比「路由质量」时不要只看 raw。</li>
<li><b>irreg</b>（<code>irregularity_penalty = mk / LB_same_A − 1</code>）：相对<strong>同一存活集合 A</strong>
上解析下界（带宽/注入/延迟三项取 max）的额外开销。
例如 <code>+9.8%</code> 表示在「这些节点本来就该跑多久」之上，又慢了约 10%——
主要来自绕路、负载不均、死锁约束等。比 raw 更适合比较不同方案的路由质量。</li>
<li>百分比由比值减 1 再 ×100 显示；正=更慢，负=更快（对 raw 常见于高牺牲）。</li>
</ul>
<h3>3.1 dead · m=1 flit</h3>
{optimal_table('dead', 1)}
<h3>3.2 dead · m=5 flit（同源同目的保序 wormhole）</h3>
{optimal_table('dead', 5)}
<h3>3.3 transit · m=1 flit</h3>
{optimal_table('transit', 1)}
<h3>3.4 transit · m=5 flit</h3>
{optimal_table('transit', 5)}

<h2>4. 全方案 makespan 矩阵</h2>
<p class="note">单元格主行：makespan（cy）；副行：<b>raw</b>（相对健康 XY 的 slowdown 百分比）
| 牺牲节点数。INF = 牺牲预算内仍无可行无死锁保序路由，或 DES 死锁。
raw / irreg 定义见第 3 节与第 6 节。</p>
<h3>4.1 dead · m=1</h3>
{scheme_matrix('dead', 1)}
<h3>4.2 transit · m=1</h3>
{scheme_matrix('transit', 1)}
<h3>4.3 dead · m=5</h3>
{scheme_matrix('dead', 5)}
<h3>4.4 transit · m=5</h3>
{scheme_matrix('transit', 5)}

<h2>5. Q 敏感度（子集，m=1, dead）</h2>
{q_table or '<p class="note">无 Q 敏感度数据</p>'}

<h2>6. 指标定义</h2>
<ul>
<li><b>raw / raw_slowdown</b> = <code>mk / mk_golden − 1</code>（与 ring_report 同口径）。
基准是健康 mesh 的 XY alltoall。表中写成百分比，如 <code>+91.0%</code> = 慢 91%。
负值多半来自 A 变小，解读时要对照「牺牲」列。</li>
<li><b>irreg / irregularity_penalty</b> = <code>mk / LB_same_A − 1</code>。
<code>LB_same_A</code> = 同一 compute 集合上
<code>max(unbound_bw, inj_term, lat_term)</code>。
衡量「去掉死锁/绕路约束后还该多慢」——越小说明路由越接近该规模下的带宽/延迟极限。</li>
<li><code>sacrifice_cost = n_sacrificed / n_originally_good</code></li>
</ul>

<h2>7. 主要观察</h2>
<ol>
<li><b>全 B 类就位后，按「牺牲→makespan」仍几乎全是 M7 Stripe</b>
（约 70/72 场）。dead·m=1 中位 mk：Stripe 194 &lt; Virtual 240 &lt; f-ring 248
&lt; LASH/TOR 257 &lt; Dual-UD 342 ≈ Up*/Down* 344。</li>

<li><b>M6b LASH-TOR 与 M6 中位完全相同</b>（本网格 LASH 已是 1–2 层，升层无额外收益）。
<strong>M9 Dual-UD</strong> 相对 M3 几乎无改善。
<strong>M10 Virtual mesh</strong> 以 2 VC 夹在 f-ring 与 LASH 之间，适合要保留规则映射的场景。</li>

<li><b>链路故障：</b>M5 须退休端点；LASH/Stripe/Virtual 通常只需拿掉孤立点，
且中心链路可零牺牲。</li>

<li><b>按「先牺牲、再 makespan」：M7 Stripe 几乎通吃</b>
（dead/transit × m=1/5 合计约 70/72 场）。仅个别场次 M5/M6 并列或略胜。
代价是 5–6 VC。若看 Pareto 上「同牺牲、更少 VC」，常落到 M6 LASH。</li>

<li><b>M2 Rect-XY 仍在 Pareto 极端点</b>（高牺牲换极低 makespan），但那是 A 变小所致；
看 irreg. 并不占优。只有上层本就不需要满规模时才有意义。</li>

<li><b>M3+LB / M4+LB 几乎无效</b>——想降负载应换 B 类。</li>

<li><b>Q 与 VC 面积：</b>Q=4 时 Up*/Down* 慢 3–4×。多 VC 独立缓冲面积随层数放大
（Stripe 最贵，LASH 最省）；共享池可压低，需另评。</li>

<li>全部可行 DES 行 <code>ordered_ok=True</code>（含 LASH / Stripe / f-ring）。</li>
</ol>
</body></html>
"""
    HTML_PATH.write_text(doc)
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
