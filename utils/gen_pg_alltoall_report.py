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
E2E_JSON_PATH = ROOT / "results" / "pg_e2e_pareto.json"
E2E_PNG = "pg_e2e_pareto.png"
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

E2E_SHORT = {
    "xy": "M1 XY",
    "rect_xy": "M2 Rect-XY",
    "updown": "M3 Up*/Down*",
    "updown_lb": "M3+LB",
    "segment": "M4 Segment",
    "segment_lb": "M4+LB",
    "fault_ring_vc": "M5 f-ring",
    "lash": "M6 LASH",
    "lash_tor": "M6b LASH-TOR",
    "stripe_vc": "M7 Stripe",
    "dual_updown": "M9 Dual UD",
    "virtual_mesh": "M10 Virtual",
}


def esc(v) -> str:
    return html.escape(str(v))


def pct(x) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.1f}%"


def _mini_xy(cols: int, rows: int, pad: float = 28, gap: float = 36,
             bottom_extra: float = 0, side_extra: float = 0):
    """Return (cell centers dict (c,r)->(x,y), width, height). row 0 at bottom.

    Base height reserves room for a 2-line caption (~14px/line) so wrapped
    Chinese footnotes are not clipped by the SVG viewBox.
    """
    w = pad * 2 + (cols - 1) * gap + 20 + side_extra
    h = pad * 2 + (rows - 1) * gap + 44 + bottom_extra
    # Keep the grid centred when side_extra widens the canvas for captions.
    x0 = pad + side_extra / 2
    centers = {}
    for r in range(rows):
        for c in range(cols):
            centers[(c, r)] = (x0 + c * gap, h - pad - r * gap)
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


def _wrap_caption(text: str, max_chars: int = 18) -> list[str]:
    """Split a caption so it fits a narrow SVG (CJK ≈ 1 char wide)."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    # Prefer semantic breaks; allow the first line a little over budget.
    for sep in ("；", "。", "，", "、", "：", ":", ";"):
        if sep not in text:
            continue
        left, right = text.split(sep, 1)
        left, right = left + sep, right.strip()
        if right and 4 <= len(left) <= max_chars + 4:
            return [left] + _wrap_caption(right, max_chars)
    # Hard wrap; keep a short lead-in (e.g. "③ ") with the next chunk.
    if len(text) > max_chars:
        cut = max_chars
        # Prefer breaking after a space near the cut.
        sp = text.rfind(" ", 0, cut + 1)
        if sp >= 4:
            cut = sp + 1
        return [text[:cut].rstrip()] + _wrap_caption(text[cut:].lstrip(),
                                                      max_chars)
    return [text]


def _caption(w, h, text, max_chars: int = 18):
    lines = _wrap_caption(text, max_chars=max_chars)
    n = len(lines)
    # Bottom-most line sits at h-5; earlier lines stack upward.
    out = []
    for i, line in enumerate(lines):
        y = h - 5 - (n - 1 - i) * 13
        out.append(
            f'<text x="{w / 2}" y="{y}" text-anchor="middle" '
            f'font-size="11" font-family="sans-serif" fill="#444">{line}</text>')
    return "".join(out)


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

    # ---- M3 Up*/Down* panel set ------------------------------------------------
    def _m3_grid(C, parts, hole=(), gray_edges=True):
        for r in range(3):
            for c in range(2):
                if (c, r) in hole or (c + 1, r) in hole:
                    continue
                if gray_edges:
                    parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.5))
            for c in range(3):
                if r < 2 and (c, r) not in hole and (c, r + 1) not in hole:
                    if gray_edges:
                        parts.append(_edge(C[(c, r)], C[(c, r + 1)],
                                           "#bdc3c7", 1.5))

    def _m3_labels_text(C, parts, labels, skip=()):
        for (c, r), lab in labels.items():
            if (c, r) in skip:
                continue
            parts.append(
                f'<text x="{C[(c, r)][0] + 11}" y="{C[(c, r)][1] + 4}" '
                f'font-size="10" fill="#7f8c8d">d={lab}</text>')

    labels3 = {(c, r): abs(c - 1) + abs(r - 1)
               for c in range(3) for r in range(3)}

    def _m3_canvas():
        # Extra width/height so Chinese captions & 2-line legends are not clipped.
        return _mini_xy(3, 3, pad=36, gap=48, bottom_extra=20, side_extra=56)

    def _m3_legend(parts, left, right, c_left, c_right):
        parts.append(
            f'<text x="10" y="14" font-size="10" fill="{c_left}">{left}</text>'
            f'<text x="10" y="26" font-size="10" fill="{c_right}">{right}</text>')

    # (1) Main: height labels + path that happens to pass near root
    C, W, H = _m3_canvas()
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a3u", "#2980b9"),
             _defs_arrow("a3d", "#e67e22")]
    _m3_grid(C, parts)
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
    _m3_labels_text(C, parts, labels3)
    _m3_legend(parts, "蓝 = up（label↓）", "橙 = down（label↑）",
               "#2980b9", "#e67e22")
    parts.append(_caption(W, H, "① 先 up 再 down；此例恰好过根，根不是中转站"))
    parts.append("</svg>")
    out["updown"] = "".join(parts)

    # (2) Same root/labels; path never visits root
    C, W, H = _m3_canvas()
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a3nu", "#2980b9"),
             _defs_arrow("a3nd", "#e67e22")]
    _m3_grid(C, parts)
    # S(0,2) d=2 → (1,2) d=1 → D(2,2) d=2 : up then down, skip root (1,1)
    parts.append(_edge(C[(0, 2)], C[(1, 2)], "#2980b9", 3.2, marker="a3nu"))
    parts.append(_edge(C[(1, 2)], C[(2, 2)], "#e67e22", 3.2, marker="a3nd"))
    for r in range(3):
        for c in range(3):
            if (c, r) == (1, 1):
                fill, lab = "#8e44ad", "根"
            elif (c, r) == (0, 2):
                fill, lab = "#27ae60", "S"
            elif (c, r) == (2, 2):
                fill, lab = "#27ae60", "D"
            else:
                fill, lab = "#2980b9", ""
            parts.append(_node(*C[(c, r)], fill=fill, label=lab,
                               r=8 if (c, r) == (1, 1) else 7))
    _m3_labels_text(C, parts, labels3)
    _m3_legend(parts, "根只提供高度坐标", "路径：d=2 → 1 → 2",
               "#8e44ad", "#555")
    parts.append(_caption(W, H, "② 同一套 label；合法最短路可以完全不经过根"))
    parts.append("</svg>")
    out["updown_noroot"] = "".join(parts)

    # (3) One root shared by two (s,d) pairs
    C, W, H = _m3_canvas()
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a3s1u", "#2980b9"),
             _defs_arrow("a3s1d", "#e67e22"),
             _defs_arrow("a3s2u", "#16a085"),
             _defs_arrow("a3s2d", "#d35400")]
    _m3_grid(C, parts)
    # pair1: (0,0)->(0,1)->(1,1)->(2,1)->(2,2)
    for a, b in [((0, 0), (0, 1)), ((0, 1), (1, 1))]:
        parts.append(_edge(C[a], C[b], "#2980b9", 3.0, marker="a3s1u"))
    for a, b in [((1, 1), (2, 1)), ((2, 1), (2, 2))]:
        parts.append(_edge(C[a], C[b], "#e67e22", 3.0, marker="a3s1d"))
    # pair2: (2,0)->(1,0)->(1,1)->(1,2)->(0,2)  (offset slightly via dash)
    for a, b in [((2, 0), (1, 0)), ((1, 0), (1, 1))]:
        parts.append(_edge(C[a], C[b], "#16a085", 2.6, "4,2", marker="a3s2u"))
    for a, b in [((1, 1), (1, 2)), ((1, 2), (0, 2))]:
        parts.append(_edge(C[a], C[b], "#d35400", 2.6, "4,2", marker="a3s2d"))
    for r in range(3):
        for c in range(3):
            if (c, r) == (1, 1):
                fill, lab = "#8e44ad", "根"
            elif (c, r) == (0, 0):
                fill, lab = "#27ae60", "S₁"
            elif (c, r) == (2, 2):
                fill, lab = "#27ae60", "D₁"
            elif (c, r) == (2, 0):
                fill, lab = "#1abc9c", "S₂"
            elif (c, r) == (0, 2):
                fill, lab = "#1abc9c", "D₂"
            else:
                fill, lab = "#2980b9", ""
            parts.append(_node(*C[(c, r)], fill=fill, label=lab))
    _m3_labels_text(C, parts, labels3)
    _m3_legend(parts, "实线 = S₁→D₁", "虚线 = S₂→D₂",
               "#2980b9", "#16a085")
    parts.append(_caption(W, H, "③ 每种故障场景只选一个 root；所有 (s,d) 共用"))
    parts.append("</svg>")
    out["updown_share"] = "".join(parts)

    # (4) Not DOR + fault pruned from adj
    C, W, H = _m3_canvas()
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a3fu", "#2980b9"),
             _defs_arrow("a3fd", "#e67e22")]
    hole = {(1, 0)}
    _m3_grid(C, parts, hole=hole)
    # ghost XY that would hit the hole: (0,0)->(1,0)->(2,0)->(2,2)
    for a, b in [((0, 0), (1, 0)), ((1, 0), (2, 0)), ((2, 0), (2, 1)),
                 ((2, 1), (2, 2))]:
        parts.append(_edge(C[a], C[b], "#c0392b", 2.0, "3,3"))
    parts.append(
        f'<text x="{C[(1, 0)][0]}" y="{C[(1, 0)][1] - 22}" '
        f'text-anchor="middle" font-size="10" fill="#c0392b">'
        f'XY/DOR 撞洞 ✕</text>')
    # Up*/Down* around: (0,0)->(0,1)->(1,1)->(2,1)->(2,2)
    for a, b in [((0, 0), (0, 1)), ((0, 1), (1, 1))]:
        parts.append(_edge(C[a], C[b], "#2980b9", 3.2, marker="a3fu"))
    for a, b in [((1, 1), (2, 1)), ((2, 1), (2, 2))]:
        parts.append(_edge(C[a], C[b], "#e67e22", 3.2, marker="a3fd"))
    for r in range(3):
        for c in range(3):
            if (c, r) in hole:
                parts.append(_node(*C[(c, r)], fill="#c0392b", r=8, label="坏"))
            elif (c, r) == (1, 1):
                parts.append(_node(*C[(c, r)], fill="#8e44ad", label="根"))
            elif (c, r) == (0, 0):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="S"))
            elif (c, r) == (2, 2):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="D"))
            else:
                parts.append(_node(*C[(c, r)]))
    _m3_labels_text(C, parts, labels3, skip=hole)
    _m3_legend(parts, "红虚线 = DOR", "彩实线 = Up*/Down* BFS",
               "#c0392b", "#2980b9")
    parts.append(_caption(W, H, "④ 非 DOR；坏节点已从邻接表删除，只在活边上搜"))
    parts.append("</svg>")
    out["updown_fault"] = "".join(parts)

    out["updown_aux"] = (out["updown_noroot"] + out["updown_share"]
                         + out["updown_fault"])

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

    # ---- M10 Virtual mesh: logical XY with a hole; missing hops physically ----
    C, W, H = _mini_xy(4, 3, pad=30, gap=42)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a10x", "#2980b9"),
             _defs_arrow("a10y", "#8e44ad"), _defs_arrow("a10d", "#e67e22")]
    hole = {(2, 1)}
    for r in range(3):
        for c in range(3):
            if (c, r) in hole or (c + 1, r) in hole:
                continue
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#cfd6da", 1.4))
        for c in range(4):
            if r < 2 and (c, r) not in hole and (c, r + 1) not in hole:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#cfd6da", 1.4))
    # S(0,1)→D(3,0): logical XY through hole (2,1); physical expands the gap
    # (1,1)⇢(3,1) as (1,1)→(1,2)→(2,2)→(3,2)→(3,1), then Y down to (3,0).
    parts.append(_edge(C[(1, 1)], C[(2, 1)], "#c0392b", 2, "4,3"))
    parts.append(_edge(C[(2, 1)], C[(3, 1)], "#c0392b", 2, "4,3"))
    parts.append(
        f'<text x="{(C[(1, 1)][0] + C[(3, 1)][0]) / 2}" y="{C[(2, 1)][1] - 10}" '
        f'text-anchor="middle" font-size="10" fill="#c0392b">逻辑边缺失</text>')
    # X 相直到首次到达目的列；途中 (1,1)⇢(3,1) 的逻辑跨越用橙色散开
    for a, b in [((0, 1), (1, 1))]:
        parts.append(_edge(C[a], C[b], "#2980b9", 3.2, marker="a10x"))
    for a, b in [((1, 1), (1, 2)), ((1, 2), (2, 2)), ((2, 2), (3, 2))]:
        parts.append(_edge(C[a], C[b], "#e67e22", 3.2, marker="a10d"))
    for a, b in [((3, 2), (3, 1)), ((3, 1), (3, 0))]:
        parts.append(_edge(C[a], C[b], "#8e44ad", 3.2, marker="a10y"))
    for r in range(3):
        for c in range(4):
            if (c, r) in hole:
                parts.append(_node(*C[(c, r)], fill="#c0392b", r=8, label="洞"))
            elif (c, r) == (0, 1):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="S"))
            elif (c, r) == (3, 0):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="D"))
            else:
                parts.append(_node(*C[(c, r)]))
    parts.append(
        f'<text x="8" y="14" font-size="10" fill="#2980b9">蓝=逻辑 X</text>'
        f'<text x="78" y="14" font-size="10" fill="#e67e22">橙=物理绕路</text>'
        f'<text x="168" y="14" font-size="10" fill="#8e44ad">紫=逻辑 Y</text>')
    parts.append(_caption(W, H, "逻辑仍是 XY；缺边用固定物理最短路替换"))
    parts.append("</svg>")
    out["virtual_mesh"] = "".join(parts)

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


def _e2e_pareto_front(pts: list[dict], xk: str, yk: str) -> list[dict]:
    out = [p for p in pts
           if not any(o is not p and o[xk] <= p[xk] and o[yk] <= p[yk]
                      and (o[xk] < p[xk] or o[yk] < p[yk]) for o in pts)]
    return sorted(out, key=lambda p: p[xk])


def e2e_section_html() -> str:
    """Build §6 end-to-end time × area Pareto from pg_e2e_pareto.json."""
    if not E2E_JSON_PATH.exists():
        return ("<h2>6. 端到端时间 × 面积 Pareto</h2>"
                "<p class='note'>尚无 <code>results/pg_e2e_pareto.json</code>。"
                "请先跑 <code>utils/dse_pg_e2e_pareto.py</code> 与 "
                "<code>utils/gen_pg_e2e_pareto_plot.py</code>。</p>")
    data = json.loads(E2E_JSON_PATH.read_text())
    meta, summary = data["meta"], data["summary"]
    m0s = meta["m0_list"]
    tokens = meta["total_tokens"]
    am = meta["area_model"]

    def e2e_table(m0: int) -> str:
        cand = sorted((s for s in summary if s["m0"] == m0),
                      key=lambda s: s["t_e2e_ns_worst"])
        head = ("<tr><th class='l'>方案</th><th>VC</th><th>area</th>"
                "<th>A 中位/最差</th><th>牺牲中位</th>"
                "<th>T<sub>e2e</sub> 中位 (ns)</th>"
                "<th>T<sub>e2e</sub> 最差 (ns)</th>"
                "<th>通信占比</th><th>Pareto</th></tr>")
        body = []
        for s in cand:
            mark = "<b>yes</b>" if s.get("pareto_worst") else ""
            body.append(
                "<tr>"
                f"<td class='l'>{esc(E2E_SHORT.get(s['scheme'], s['scheme']))}</td>"
                f"<td>{s['num_vc']}</td>"
                f"<td>{s['area']:.3f}</td>"
                f"<td>{s['A_med']}/{s['A_worst']}</td>"
                f"<td>{s['sac_med']}</td>"
                f"<td>{s['t_e2e_ns_med']:.0f}</td>"
                f"<td><b>{s['t_e2e_ns_worst']:.0f}</b></td>"
                f"<td>{s['comm_frac_med']:.2f}</td>"
                f"<td>{mark}</td>"
                "</tr>")
        return (f"<table><thead>{head}</thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    # Marginal returns on the worst-case convex hull (dedupe same area/time)
    def marginal_html(m0: int) -> str:
        raw = [s for s in summary if s["m0"] == m0 and s.get("pareto_worst")]
        front = _e2e_pareto_front(raw, "area", "t_e2e_ns_worst")
        # merge identical (area, worst) corners
        merged: list[dict] = []
        for s in front:
            if (merged and abs(merged[-1]["area"] - s["area"]) < 1e-9
                    and abs(merged[-1]["t"] - s["t_e2e_ns_worst"]) < 0.5):
                merged[-1]["names"].append(
                    E2E_SHORT.get(s["scheme"], s["scheme"]))
            else:
                merged.append({
                    "area": s["area"],
                    "t": s["t_e2e_ns_worst"],
                    "vc": s["num_vc"],
                    "names": [E2E_SHORT.get(s["scheme"], s["scheme"])],
                })
        if len(merged) < 2:
            return "<p class='note'>前沿点数不足，无法算边际回报。</p>"
        rows_h = ("<tr><th class='l'>台阶</th><th>Δarea</th>"
                  f"<th>ΔT (m₀={m0})</th><th>回报 (ns/area)</th></tr>")
        body = []
        for i in range(1, len(merged)):
            a, b = merged[i - 1], merged[i]
            da = b["area"] - a["area"]
            dt = a["t"] - b["t"]
            body.append(
                "<tr>"
                f"<td class='l'>{esc(' / '.join(a['names']))} → "
                f"{esc(' / '.join(b['names']))}</td>"
                f"<td>+{100 * da / a['area']:.1f}% "
                f"<span class='sub'>({a['area']:.3f}→{b['area']:.3f})</span></td>"
                f"<td>−{100 * dt / a['t']:.1f}% "
                f"<span class='sub'>({a['t']:.0f}→{b['t']:.0f} ns)</span></td>"
                f"<td><b>{dt / da:.0f}</b></td>"
                "</tr>")
        return (f"<table><thead>{rows_h}</thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    png_note = ""
    if not (ROOT / "results" / E2E_PNG).exists():
        png_note = ("<p class='note bad'>缺少 "
                    f"<code>results/{E2E_PNG}</code>，"
                    "请跑 <code>gen_pg_e2e_pareto_plot.py</code>。</p>")

    return f"""
<h2>6. 端到端时间 × 面积 Pareto</h2>
<p class="note">第 3–4 节按纯 makespan 排序会误导——M1 XY 的 makespan 最小，
但中位牺牲 28/48 个节点。把通信放回真实计算任务后，牺牲的代价才显现。
本节用 <b>端到端任务完成时间</b>（计算 + alltoall）与 <b>router 面积</b>
构造 Pareto 前沿，评估选型。</p>

<h3>6.1 模型</h3>
<ul class="note">
<li><b>任务</b>：MoE 专家并行 FFN 的 dispatch 半程 —
<code>alltoall → 专家 FFN</code>，<b>串行不重叠</b>
（完整层还有对称的 combine alltoall，会让通信项翻倍；此处略去以对齐「一次 alltoall」口径）。</li>
<li><b>计算</b>：PE 每拍一次 <code>8×64×16</code> matmul = {meta['pe_macs_per_cycle']} MAC/cy
@ <b>{meta['freq_ghz']} GHz</b>。FFN <code>d_model={meta['d_model']}</code>、
<code>d_ff={meta['d_ff']}</code>、fp16 —— <code>8×64×16</code> 恰好整除两层 matmul，
无 tile 量化浪费，<b>{meta['cycles_per_token']:.0f} 拍/token</b>。
token = {meta['token_bytes']} B = {meta['token_bytes'] // meta['flit_bytes']} flits
（flit = {meta['flit_bytes']} B）。</li>
<li><b>强扩展</b>：总 token 钉在健康 48-PE 配置（每对载荷 = 标称 m₀）：
<code>T_total = 48²·m₀·64B/128B = 1152·m₀</code> tokens。
只剩 A 个存活 PE 时两项同时变重：
<code>T_compute = ceil(4·T_total/A)</code>；
<code>m_eff = ceil(m₀·(48/A)²)</code>（通信也必须重标定，否则重牺牲方案白拿 1/A² 流量减免）；
<code>T_e2e = T_compute + T_alltoall(m_eff)</code> → ns @ {meta['freq_ghz']} GHz。</li>
<li><b>面积（仅 router）</b>：牺牲的 PE 只计时间不计面积；48 个 router 始终物理存在，
唯一杠杆是 VC 数。归一化到 IQ-XY 基线 = 1.0：
<code>area = crossbar({am['crossbar']}) + control({am['control']})
 + {am['ports']} port × VC × Q({meta['Q']}) × {am['a_flit']:.5f}</code>。
DES 中 Q 是<b>每 VC</b> 深度，故 VC 数线性放大缓冲。
每个方案按 18 场景中需要的<b>最大 VC 数</b>定尺寸。</li>
</ul>
<p class="note">扫描：dead 语义 × {len(m0s)} 个 m₀ × 12 方案 × 18 场景 =
{sum(1 for _ in data['rows'])} 行 DES；耗时 {meta.get('elapsed_s')}s。
数据 <code>results/pg_e2e_pareto.json</code>。</p>

<h3>6.2 Pareto 图与结果表</h3>
<p class="note">实心点 = 18 个 dead 场景中的<b>最差值</b>（PG 必须覆盖全部场景，
这才是设计点）；空心点 = 中位；竖线连中位→最差。</p>
{png_note}
<figure class="e2e-fig">
<img src="{E2E_PNG}" alt="end-to-end time vs router area Pareto"
     style="max-width:100%;height:auto;background:#fff;border:1px solid #e0e0e0"/>
<figcaption>端到端 MoE FFN 时间 vs router 面积（左 m₀=1 / {int(float(tokens['1']))} tokens；
右 m₀=13 / {int(float(tokens['13']))} tokens）</figcaption>
</figure>

<h4>m₀ = 1 flit（{int(float(tokens['1']))} tokens）</h4>
{e2e_table(1)}
{marginal_html(1)}

<h4>m₀ = 13 flit（{int(float(tokens['13']))} tokens）</h4>
{e2e_table(13)}
{marginal_html(13)}

<h3>6.3 选型结论</h3>
<ol>
<li><b>排名翻转：</b>M1 XY / M2 Rect-XY 在第 3–4 节 makespan 矩阵中最快（中位 ~62 cy），
端到端却被同为 VC1、面积相同的 <b>M3 Up*/Down*</b> 严格支配
（最差 678 ns vs XY 的 820 ns）。原因全在牺牲：XY 最差场景只剩 6/48 PE，
计算涨 8×、每对载荷涨 64×。M4 Segment 同理更糟。</li>
<li><b>通信占端到端 70–86%</b>（除重牺牲的 XY/Rect-XY）。即便配了
{meta['pe_macs_per_cycle']} MAC/cy 的 PE，任务仍是通信瓶颈——
花 router 面积买带宽划算。</li>
<li><b>凸包只剩三点：M3（VC1）→ M10（VC2）→ M7（VC6）</b>。
M3→M10 多 39% 面积换约 20% 加速（回报 381 / 4205 ns/area）；
M10→M7 再多 111% 面积只多买 ~13%（回报低一个数量级）。
M5 f-ring 虽在 m₀=13 严格前沿上，但 <code>M10→M5</code> 边际回报低于
<code>M5→M7</code>，不在凸包——理性选择会跳过它。</li>
<li><b>推荐：M10 虚拟规则网格（2 VC）</b>——拐点干净，两个载荷尺寸结论一致；
上层软件仍见规则 8×6 mesh。若 router 面积在系统中占比很小、延迟是硬指标，
直接上 <b>M7 Stripe（6 VC）</b>（两个载荷下都最快）。
<strong>不要</strong>因 makespan 矩阵好看就选 M1 XY。</li>
</ol>
<p class="note"><b>已知局限：</b>只算 dispatch 一次 alltoall（加 combine 更利好 M7）；
面积不计牺牲的 PE tile（计入会进一步惩罚 M1/M2/M4）；
control 面积按常数、未随 VC 增长（对 6 VC 的 M7 偏乐观，更利好 M10）。</p>
"""


def main():
    data = json.loads(JSON_PATH.read_text())
    meta = data["meta"]
    rows = data["rows"]
    golden = meta["golden"]
    e2e_html = e2e_section_html()

    # Index primary rows (not q_sensitivity)
    primary = [r for r in rows if not r.get("q_sensitivity")]
    qrows = [r for r in rows if r.get("q_sensitivity")]
    diagrams = scheme_diagrams()
    cls_fig = class_diagrams()

    def scheme_block(title_html: str, key: str, body_html: str,
                     extra_key: str | None = None,
                     extra_keys: list[str] | None = None) -> str:
        fig = diagrams.get(key, "")
        for k in ([extra_key] if extra_key else []) + (extra_keys or []):
            fig += diagrams.get(k, "")
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
                        f"raw={pct(hit.get('raw_slowdown'))}'>"
                        f"{hit['makespan']}"
                        f"<div class='sub'>"
                        f"{pct(hit.get('irregularity_penalty'))} "
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
               padding: 0.45rem; border-radius: 4px;
               display: flex; flex-direction: column; gap: 0.65rem;
               max-width: 28rem; }}
.scheme-fig svg {{ display: block; background: #fff;
                   border: 1px solid #eef0f2; border-radius: 3px;
                   overflow: visible; max-width: 100%; height: auto; }}
.faq {{ background: #f8fafc; border-left: 3px solid #8e44ad;
        padding: 0.45rem 0.75rem; margin: 0.5rem 0; font-size: 0.9rem; }}
.faq p {{ margin: 0.3rem 0; }}
.faq b.q {{ color: #6c3483; }}
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
figure.e2e-fig {{ max-width: 72rem; margin: 1rem 0; padding: 0.75rem; }}
figcaption {{ font-size: 0.75rem; margin: 0.35rem 0 0; color: #555; }}
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
<p><b>类别：</b>A 类 · 转向限制 · <b>1 VC</b>（无需虚拟通道分层）。</p>
<p><b>思想：</b>在任意连通的存活路由图上，先建一棵以某个根为中心的生成树标号，
再强制报文只走「先朝根（up）、再离根（down）」的路径。
这是不规则拓扑上最经典的无死锁确定性路由（Glass–Ni / Autonet Up*/Down*）。
故障后只要图仍连通，通常就能零牺牲跑通全表——本研究里它是「保住计算规模」的基线。</p>
<p><b>算法步骤：</b></p>
<ol>
<li><b>选根</b>：在存活路由邻接表上取度最大的节点（并列取编号小者），尽量让树更「中心」。
<strong>每种故障场景只选一次</strong>，所有 (s,d) 共用（见图③）。</li>
<li><b>标号</b>：从根做 BFS，<code>label(n) = dist(root, n)</code>。
朝根走（label 减小）= <b>up</b>；离根走（label 增大）或同层侧向 = <b>down</b>。</li>
<li><b>转向规则</b>：路径分两相——相 0 允许 up；一旦走出 down，进入相 1，
此后<b>禁止再 up</b>。同层侧向也算 down，因此不能「下完又上」。</li>
<li><b>选路</b>：在上述合法转移上对 <code>route_adj</code> 做 BFS，
得到每对 (s,d) 的最短合法路径——<strong>不是 XY/DOR，也不是「先走到根再离开」</strong>。
整表确定性、唯一路径 → 同源同目的保序。</li>
</ol>

<div class="faq">
<p><b class="q">Q1. root 是每种故障一个，还是每个 (src,dst) 一个？</b><br/>
<strong>每种故障场景（一份存活路由图）只选一个 root。</strong>
换 (s,d) 不换 root；换故障 / 牺牲后图变了，root 才可能变（度最大点可能换人）。
见图③：S₁→D₁ 与 S₂→D₂ 共用同一套 <code>d=…</code> 标号。</p>
<p><b class="q">Q2. src→root、root→dst 还是 DOR 吗？</b><br/>
<strong>都不是。</strong>实现是「约束 BFS」：在活边上枚举邻居，只按 label 升降判断
up/down 是否合法，取最短跳数路径。跳的方向可以是任意 mesh 邻边（东/西/南/北），
与「先 X 后 Y」无关。见图④红虚线（DOR 撞洞）vs 彩实线（Up*/Down* 绕开）。</p>
<p><b class="q">Q3. 路径必须经过 root 吗？</b><br/>
<strong>不必。</strong>Root 只提供高度坐标系，不是中转站。
路径可以过根（图①，S、D 分居两侧时常见），也可以完全不过根
（图②：<code>d=2→1→2</code>，只在顶行走）。示意图画「S→根→D」是为展示两相，
不是算法字面步骤。</p>
<p><b class="q">Q4. 怎么避开故障节点？</b><br/>
<strong>建图时就删掉了。</strong><code>expand_pg</code> 生成的 <code>route_adj</code>
不含 dead 节点及其入射边（transit 语义下故障节点不当 compute，但 router 可仍在图中）。
<code>_tree_path</code> 只遍历 <code>adj.get(u)</code>，不可能踏进已删除的点/边。
若图被割裂或「先上后下」下无合法路，则该场景失败，外层再靠牺牲好节点恢复。</p>
</div>

<p><b>无死锁证明要点：</b>任何合法路径上，通道依赖只能经历
「up 边 → up 边 → … → down 边 → down 边」。
不可能出现 down→up，因此 CDG 按构造无环（实现里仍做一次硬校验）。</p>
<p><b>与本网格实测：</b>dead/transit 下几乎全部场景 <b>n_sacrificed = 0</b>（A≈39–48），
是 1 VC 方案里规模保持最好的；但合法路径集合窄，负载集中在树的「脊」，
alltoall makespan / irreg 明显高于最短路族（M6/M7/M10）。
M3+LB 试图在合法集合内做负载感知换路，本 8×6 上中位收益几乎为零。</p>
<p><b>端到端角色：</b>Pareto 凸包的左端点——面积最小（VC1≈0.90），
作为「面积受限时的保底方案」。不要用它追极限延迟。</p>
''', extra_key="updown_aux")}

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
<p><b>类别：</b>B 类 · VC 分层 · <b>固定 4 VC</b>。保留 XY（e-cube）硬件语义的容错路由。</p>
<p><b>思想：</b>Boppana–Chalasani 式真 <i>fault-ring</i>。把故障吸收进若干互不重叠的
<b>矩形故障块</b>；块外围一圈健康节点构成 fault ring。底层路由仍是「先 X 后 Y」：
不撞块就走普通 XY；下一步会踏进块时，改沿 ring 绕到块的对面，再回到原行/原列继续 XY。</p>
<p><b>块怎么造：</b></p>
<ol>
<li>种子 = 故障节点 ∪（链路故障贪心选出的端点，见下）∪ 路由死节点。</li>
<li>每个种子先成 1×1 矩形；若两矩形接触或重叠则合并包围盒，直到稳定。</li>
<li>块内全部节点从路由图剔除；compute 集也去掉这些点（forced sacrifice）。</li>
</ol>
<p><b>链路故障的固有代价：</b>矩形块模型没有「两活节点之间断一条链」的状态，
必须退休一个端点把它变成 1×1 块（辅图）。实现用贪心集合覆盖：每轮选覆盖最多残留坏链的端点。
因此——<b>纯节点故障通常零额外牺牲；纯链路故障要付 1–4 个好节点</b>。这是相对 M3/M7/M10 的固有税。</p>
<p><b>路径构造（<code>_fring_path</code>）：</b></p>
<ol>
<li><b>X 相：</b>朝目的列一步步走。下一步坐标落在块内 → 调用 <code>_x_detour</code>：
先竖走到环的上/下边（优先朝目的行一侧），再水平走到块远侧列，必要时竖走回原行。
若目的列本身落在块内，则水平停在目的列（避免冲过块再也收敛不了）。</li>
<li><b>Y 相：</b>X 相结束后（可能已被绕行带到别的行），按当前行→目的行方向走；
撞块则 <code>_y_detour</code> 左右绕，回到原列后再继续。</li>
</ol>
<p><b>四条 VC（相位 × 方向，整路径确定性 → 保序）：</b></p>
<table class="vctab"><tbody>
<tr><td>VC0</td><td class="l">X 相 · 东行</td><td>VC1</td><td class="l">X 相 · 西行</td></tr>
<tr><td>VC2</td><td class="l">Y 相 · 北行</td><td>VC3</td><td class="l">Y 相 · 南行</td></tr>
</tbody></table>
<p>X 相 hop 数 = 到达目的列之前的物理跳数；之后全部进 Y 相 VC。
方向类按「源→目的的 X 符号 / Y 相实际行进符号」在离线时固化进 <code>vc_of</code>。</p>
<p><b>无死锁（可证）：</b></p>
<ul>
<li>X 相绕行只允许「竖走」或「朝本报文的 X 方向走」→ VC0 内每条横向通道都朝东。
环要闭合必须让 x 回到起点，于是环内不能含横向通道；纯竖环又需要 180° 掉头，
而构造不产生掉头 → VC0 无环（VC1 对称）。</li>
<li>Y 相镜像：VC2 内每条竖向通道朝北（VC3 朝南）。</li>
<li>报文只从 X 相进 Y 相、从不回头 → 依赖 {VC0,VC1}→{VC2,VC3} 单向。
单 VC 无环 + 组间单向 ⇒ 整张 CDG 无环。</li>
</ul>
<p><b>固有绕路：</b>绕行后常要回到原行再续 XY（图中竖上环、横穿、再竖下），
这是换取「X 相严格单调 ⇒ 4 VC 可证」所付的代价；负载通常高于 M7/M10。</p>
<p><b>端到端角色：</b>m₀=13 时落在严格 Pareto 前沿，但不在凸包上
（M10→M5 边际回报低于 M5→M7）——理性选择会跳过它。
若硬件已按 XY+绕障做死、且愿意付 4 VC，它仍是「保 XY 语义」的正统答案。</p>
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
<p><b>类别：</b>B 类 · VC 分层 · 本网格实测 <b>5–6 VC</b>（按场景中最坏跨越数定尺寸）。</p>
<p><b>思想：</b>把 mesh 竖切成条带，条带边界就是 <i>dateline</i>（类似环形拓扑破环的日期线）。
路径本身尽量走最短路（优先 XY，不通再 BFS）；<b>无死锁不靠砍转弯，而靠跨带时升 VC</b>。
每条路径上 VC 沿途单调不减 → 天然无环，实现极简。</p>
<p><b>算法步骤：</b></p>
<ol>
<li><b>选路</b>：对每个 (s,d)，先试 <code>xy_path</code>；若因故障不通，退回
<code>shortest_path</code>。路径固定后不再改 → 保序。</li>
<li><b>布 dateline</b>（优先稀疏）：列边界 <code>x ∈ {{2,4,6}}</code>（每 2 列一条），
再并上所有故障列及其右邻列边界（故障附近加密，避免绕障路径在稀疏带内绕出环）。</li>
<li><b>赋 VC</b>：报文走第 i 跳时，
<code>VC(i) =</code> 此前（含本跳）水平跨越 dateline 的次数。
竖走不跨带，VC 不变；每水平穿一条虚线，VC+1。</li>
<li><b>校验 / 加密</b>：用稀疏 dateline 建 <code>vc_of</code> 后做 CDG 校验；
若仍有环，退化为「每个列边界都是 dateline」（<code>1..MX-1</code>），再校验。
<code>num_vc = 1 + max 跨越数</code>。</li>
</ol>
<p><b>无死锁：</b>沿任意路径 VC 只增不减。通道依赖
<code>(e, vc) → (e′, vc′)</code> 满足 <code>vc′ ≥ vc</code>；
同层内若路径本身无环依赖，跨层又只能「升」——整体 CDG 无环。
本实现仍做硬校验，失败则加密封顶。</p>
<p><b>与 M5 / M6 / M10 的差别：</b></p>
<ul>
<li>相对 M5：不强制矩形块、不强制绕回原行；链路故障通常只牺牲孤立点，不必退休端点。
路径更短、负载更低，但 VC 更多（5–6 vs 4）。</li>
<li>相对 M6 LASH：不需要离线贪心装层；逻辑就是「跨带 +1」，硬件几乎只是计数器。
代价是 VC 上限更高（LASH 常 1–2 层就够）。</li>
<li>相对 M10：路径质量相近或更好（真最短路），但面积贵一截（VC6≈2.63 vs VC2≈1.24）。</li>
</ul>
<p><b>端到端角色：</b>Pareto 凸包右端点——<b>两个载荷下都最快</b>
（最差 471 / 4913 ns），面积也最贵。适合「router 面积不敏感、延迟是硬指标」。
从 M10 再走到 M7：+111% 面积只换约 13% 加速，边际回报比 M3→M10 低一个数量级。</p>
''')}

{scheme_block("M9 — 双向 Up*/Down*（<code>dual_updown</code>）", "updown", '''
<p><b>思想：</b>VC0 跑经典 Up*/Down*（先上后下），VC1 跑对称的 Down*/Up*（先下后上）；
每对选更短的那条，整路径固定在所选 VC → 保序。</p>
<p><b>无死锁：</b>两套规则各自 CDG 无环，且路径不跨 VC 混用。</p>
<p><b>特征：</b>固定 2 VC，实现比 LASH 简单；路径短于单层 Up*/Down*，但通常仍长于最短路族。</p>
''')}

{scheme_block("M10 — 虚拟规则网格（<code>virtual_mesh</code>）", "virtual_mesh", '''
<p><b>类别：</b>B 类 · VC 分层 · <b>固定 2 VC</b>。上层软件仍看见完整规则 8×6 XY mesh。</p>
<p><b>思想：</b>把「逻辑拓扑」和「物理走线」拆开。逻辑层永远是健康的 8×6 全网格 + 经典 XY；
物理上某条逻辑边坏了（节点洞或链路断），就用一条<b>预先算好的、固定的物理最短绕路</b>替换它。
对编译器 / 映射层来说，集合通信调度、坐标寻址都不用改——缺的只是 NoC 内部把逻辑 hop 展开。</p>
<p><b>算法步骤：</b></p>
<ol>
<li><b>存活集</b>：至少有一条活链路的节点才算逻辑路由器；孤立点 forced-sacrifice。</li>
<li><b>预计算展开表</b>：对每一对逻辑相邻的活节点 (a,b)，若物理直连存在则
<code>expand[a→b]=[a,b]</code>；否则 <code>shortest_path(a,b)</code> 作为该逻辑边的固定绕路。
绕路离线算一次、全局共享 → 确定性。</li>
<li><b>逻辑 XY</b>：忽略故障，在完整网格上走先 X 后 Y，得到逻辑折线
<code>full = _logical_xy(s,d)</code>。</li>
<li><b>物理展开</b>：丢掉折线上的死节点得 <code>way</code>（须仍以 s 开头、d 结尾）；
依次把 <code>way[i]→way[i+1]</code> 用展开表（或临时最短路）拼成物理路径。</li>
<li><b>VC 划分</b>：物理 hop 在「首次到达目的列」之前 → <b>VC0（逻辑 X 相）</b>；
之后 → <b>VC1（逻辑 Y 相）</b>。即使 X 相里含有竖向绕路 hop，仍算 VC0——
分层按逻辑相位，不按物理边方向。</li>
</ol>
<p><b>无死锁：</b>依赖「逻辑 X→逻辑 Y」的单向相位切换（类似维度序）。
绕路可能在 VC0 内引入竖边、在 VC1 内引入横边，理论上有可能成环，
因此实现里对整表做 CDG 硬校验；失败则该 PG 场景判定不可行（再走牺牲恢复）。
本 8×6 的 18 个 dead 场景上全部通过，通常只需去掉孤立点。</p>
<p><b>与 M5 / M7 的关键差别：</b></p>
<ul>
<li>相对 M5：不造矩形块、不强制绕回原行；链路故障不必退休端点，牺牲更少。
VC 只要 2 条（vs 4）。大洞时绕路可能比 f-ring 更「自由」，也可能更绕——以实测负载为准。</li>
<li>相对 M7：路径不是全局最短路，而是「逻辑 XY + 局部展开」，有时多走几跳；
但 VC 固定为 2，面积约 1.24（vs Stripe 的 2.63），是性价比拐点。</li>
<li>相对 M3：多 1 条 VC，换来接近最短路的路径质量和显著更低的 makespan /
端到端时间；上层映射还保持规则网格。</li>
</ul>
<p><b>端到端角色：</b><b>推荐默认方案</b>。Pareto 凸包拐点：
相对 M3 只多 39% router 面积，换约 20% 端到端加速（回报 381 / 4205 ns/area）；
再往 M7 多花 111% 面积只多买 ~13%。两个载荷尺寸结论一致。
附带好处：软件仍看规则 8×6，映射 / 调度不用为 PG 改写。</p>
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
上<strong>与路由无关的真下界</strong>（割下界带宽项 / 注入 / 延迟三者取 max）的额外开销。
例如 <code>+9.8%</code> 表示在「这些节点无论怎么路由都至少要跑这么久」之上，又慢了约 10%——
来自绕路、负载不均、死锁约束等。比 raw 更适合比较路由质量，且<b>恒 ≥ 0</b>。</li>
<li>百分比由比值减 1 再 ×100 显示。raw 可能为负（牺牲后 A 变小所致）；irreg 不会为负。</li>
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
<p class="note">单元格主行：makespan（cy）；副行：<b>irreg</b>（相对同 A 下界的额外开销）
| 牺牲节点数。这里用 irreg 而非 raw：不同方案牺牲数不同、参与者 A 不同，
raw 会因 A 变小而虚低；irreg 以各自 A 的下界为分母，跨方案可比。
raw 值仍保留在单元格 tooltip 里。
INF = 牺牲预算内仍无可行无死锁保序路由，或 DES 死锁。</p>
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

{e2e_html}

<h2>7. 指标定义</h2>
<ul>
<li><b>raw / raw_slowdown</b> = <code>mk / mk_golden − 1</code>（与 ring_report 同口径）。
基准是健康 mesh 的 XY alltoall。表中写成百分比，如 <code>+91.0%</code> = 慢 91%。
负值多半来自 A 变小，解读时要对照「牺牲」列。</li>
<li><b>irreg / irregularity_penalty</b> = <code>mk / LB_same_A − 1</code>，其中
<code>LB_same_A = max(minimax_load_lb·m, inj_term, lat_lb)</code>，
在<strong>同一存活集合</strong>上计算，且<strong>与路由无关</strong>：
<ul>
<li><code>minimax_load_lb</code>：对所有轴对齐割 (S,S̄)，S→S̄ 的
<code>|S∩C|·|S̄∩C|</code> 对必须挤过 S 的出边，取
<code>ceil(需求/出边数)</code> 的最大值。任何路由都无法低于它
（健康 8×6 上该值 = 96，正是 XY 的实际负载，说明界是紧的）。</li>
<li><code>lat_lb</code>：按 H/V 加权的最短路直径 + <code>2·RAMP + (m−1)</code>。</li>
<li><code>inj_term</code>：<code>ceil((A−1)·m / RAMP_BW)</code>。</li>
</ul>
因为最忙链路至少要搬 <code>minimax_load_lb·m</code> 个 flit，
所以 <code>mk ≥ LB_same_A</code> 恒成立，<b>irreg 不会为负</b>。
（早期版本分母用的是 XY 随手装填的可达负载，不是下界，故会出现负值。）</li>
<li><code>sacrifice_cost = n_sacrificed / n_originally_good</code></li>
</ul>

<h2>8. 主要观察</h2>
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

<li><b>端到端时间×面积（第 6 节）翻转了 makespan 排名：</b>
M1/M2 被同面积的 M3 严格支配；凸包为 <b>M3 → M10 → M7</b>。
推荐默认选 <b>M10（2 VC）</b>；延迟硬指标再上 M7。
通信占端到端 70–86%，花 router 面积买带宽划算。</li>
</ol>
</body></html>
"""
    HTML_PATH.write_text(doc)
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
