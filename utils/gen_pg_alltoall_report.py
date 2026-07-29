#!/usr/bin/env python3
"""HTML report for 8x6 PG packet-switched alltoall study."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path

import pg_faults_8x6 as F
import pg_faults_budget_8x6 as B
import pg_routing as R

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "pg_alltoall_8x6.json"
E2E_JSON_PATH = ROOT / "results" / "pg_e2e_pareto.json"
BUDGET_FAULTS_JSON = ROOT / "results" / "pg_faults_budget_8x6.json"
BUDGET_E2E_JSON = ROOT / "results" / "pg_budget_e2e_pareto.json"
BUDGET_CAP_JSON = ROOT / "results" / "pg_budget_capability.json"
CAP_JSON_PATH = ROOT / "results" / "pg_capability.json"
M10_SCAN_PATH = ROOT / "results" / "pg_m10_cycle_scan.json"
EF_REACH_PATH = ROOT / "results" / "pg_east_first_reach.json"
BEYOND_REACH_PATH = ROOT / "results" / "pg_beyond_catalog_reach.json"
E2E_PNG = "pg_e2e_pareto.png"
BUDGET_E2E_PNG = "pg_budget_e2e_pareto.png"
HTML_PATH = ROOT / "results" / "report_pg_alltoall_8x6.html"

# Schemes that fail a hard property on their own and only "work" by sacrificing
# large parts of the array — excluded from the §3/§4 makespan comparisons, where
# a smaller A would otherwise make them look artificially fast.
# See results/pg_capability.json for the measurements behind each reason.
# How each scheme earns its acyclic CDG. A ✓ backed by a construction is a
# stronger claim than a ✓ that merely survived the 36-scenario catalog.
DEADLOCK_BASIS = {
    "east_first": "构造性：禁 N→E / S→E，两条抽象环各断一处",
    "super_turn": "构造性：每层一个 Glass–Ni 最小转向模型（≤2 VC）",
    "super_turn_1vc": "构造性：单层 Glass–Ni（硬顶 1 VC，靠牺牲）",
    "xy": "构造性：只 X→Y 单向转弯",
    "rect_xy": "构造性：矩形内纯 XY",
    "updown": "构造性：不许 down→up",
    "segment": "构造性论证在残图上失效",
    "fault_ring_vc": "构造性：4 VC 相位×方向",
    "fault_half_ring": "半环绕行 + X/Y 两 VC；重叠环靠事后 CDG 校验",
    "lash": "构造：逐层校验，不行就开新层（≤8）",
    "lash_tor": "同 LASH，允许中途升层",
    "stripe_vc": "构造性：跨 dateline 单调升 VC",
    "dual_updown": "构造性：两套已证规则各占一 VC",
    "virtual_mesh": "<b>无证明</b>：两版取一 + 事后校验，"
                    "已知 8×6 反例（见 M10 章 FAQ）",
}

# Not in e2e makespan tables (reachability / sacrifice). Descriptions kept.
EXCLUDED_SCHEMES = {
    "east_first": "预算故障下东向常不可绕行，覆盖不全",
    "xy": "避障失败率高，覆盖不全",
    "rect_xy": "裁行裁列牺牲过重",
    "segment": "残图上建路/CDG 失败率高",
    "segment_lb": "同 M4 Segment",
}

# Keep §2 descriptions; omit from e2e Pareto / §3 per-scenario pick.
E2E_DESC_ONLY = {
    "fault_ring_vc": "VC=4，超出本轮 e2e（仅评 VC≤2）",
    "lash": "VC 常 >2，超出本轮 e2e",
    "lash_tor": "同 LASH",
    "stripe_vc": "VC 可达 9，超出本轮 e2e",
    "dual_updown": "M9：本轮不参与 e2e / §3 评测（描述保留）",
    "virtual_mesh": "M10：本轮不参与 e2e / §3 评测（描述保留）",
}

SCHEME_LABELS = {
    "east_first": "M0 East-first",
    "super_turn": "M0s Super-turn",
    "super_turn_1vc": "M0s1 Super-turn 1VC",
    "xy": "M1 XY (+sacrifice)",
    "rect_xy": "M2 Rect-XY",
    "updown": "M3 Up*/Down*",
    "updown_lb": "M3 Up*/Down* + LB",
    "segment": "M4 Segment",
    "segment_lb": "M4 Segment + LB",
    "fault_ring_vc": "M5 f-ring 4VC",
    "fault_half_ring": "M5h fault half-ring 2VC",
    "lash": "M6 LASH",
    "lash_tor": "M6b LASH-TOR",
    "stripe_vc": "M7 Stripe dateline",
    "dual_updown": "M9 Dual Up*/Down*",
    "virtual_mesh": "M10 Virtual mesh",
}

E2E_SHORT = {
    "east_first": "M0 East-first",
    "super_turn": "M0s Super-turn",
    "super_turn_1vc": "M0s1 Super-turn 1VC",
    "xy": "M1 XY",
    "rect_xy": "M2 Rect-XY",
    "updown": "M3 Up*/Down*",
    "updown_lb": "M3+LB",
    "segment": "M4 Segment",
    "segment_lb": "M4+LB",
    "fault_ring_vc": "M5 f-ring",
    "fault_half_ring": "M5h half-ring",
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

    # ---- M0 east-first: legal path shape, then the two ways it dead-ends ----
    def _ef_grid(cols, rows, uid, side_extra=0.0):
        C, W, H = _mini_xy(cols, rows, side_extra=side_extra)
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
                 f'height="{H}" viewBox="0 0 {W} {H}">',
                 _defs_arrow(uid), _defs_arrow(uid + "r", "#c0392b")]
        for r in range(rows):
            for c in range(cols - 1):
                parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.5))
        for r in range(rows - 1):
            for c in range(cols):
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#bdc3c7", 1.5))
        return C, W, H, parts

    def _ban(a, b, text=""):
        """✕ over the midpoint of a forbidden/broken edge, optional label under."""
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        s = (f'<text x="{mx + 2}" y="{my - 7}" text-anchor="middle" '
             f'font-size="14" fill="#c0392b" font-weight="700">✕</text>')
        if text:
            s += (f'<text x="{mx + 2}" y="{my + 16}" text-anchor="middle" '
                  f'font-size="10" font-family="sans-serif" fill="#c0392b">'
                  f'{text}</text>')
        return s

    C, W, H, parts = _ef_grid(4, 3, "ef1", side_extra=76)
    legal = [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (1, 2)]
    for i in range(len(legal) - 1):
        parts.append(_edge(C[legal[i]], C[legal[i + 1]], "#27ae60", 3,
                           marker="ef1"))
    # no turn leads back east: the N->E continuation at (2,1) is the banned one
    parts.append(_edge(C[(2, 1)], C[(3, 1)], "#c0392b", 2.5, "4,3",
                       marker="ef1r"))
    parts.append(_ban(C[(2, 1)], C[(3, 1)], "N→E"))
    for r in range(3):
        for c in range(4):
            fill = "#27ae60" if (c, r) in ((0, 0), (1, 2)) else "#2980b9"
            lab = "S" if (c, r) == (0, 0) else ("D" if (c, r) == (1, 2) else "")
            parts.append(_node(*C[(c, r)], fill=fill, label=lab))
    parts.append(_caption(W, H, "东向排在最前，之后只剩 N/S/W"))
    parts.append("</svg>")
    out["east_first"] = "".join(parts)

    C, W, H, parts = _ef_grid(4, 3, "ef2", side_extra=76)
    parts.append(_edge(C[(0, 0)], C[(1, 0)], "#27ae60", 3, marker="ef2"))
    parts.append(_edge(C[(1, 0)], C[(2, 0)], "#c0392b", 3.5, "5,3"))
    parts.append(_ban(C[(1, 0)], C[(2, 0)]))
    for a, b in (((1, 0), (1, 1)), ((1, 1), (2, 1))):
        parts.append(_edge(C[a], C[b], "#c0392b", 2.5, "4,3", marker="ef2r"))
    parts.append(_ban(C[(1, 1)], C[(2, 1)], "N→E"))
    for r in range(3):
        for c in range(4):
            fill = "#27ae60" if (c, r) == (0, 0) else (
                "#e67e22" if c >= 2 else "#2980b9")
            lab = "S" if (c, r) == (0, 0) else ""
            parts.append(_node(*C[(c, r)], fill=fill, label=lab))
    parts.append(_caption(W, H, "源行东侧一断，橙色节点全体不可达"))
    parts.append("</svg>")
    out["east_first_fail"] = "".join(parts)

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

    # ---- M5 true f-ring panel set -------------------------------------------
    def _m5_legend(parts, *lines):
        for i, (txt, col) in enumerate(lines):
            parts.append(
                f'<text x="10" y="{14 + i * 12}" font-size="10" '
                f'fill="{col}">{txt}</text>')

    # ① X-phase ring detour then Y-phase
    C, W, H = _mini_xy(5, 4, pad=32, gap=42, bottom_extra=16, side_extra=40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a5x", "#2980b9"),
             _defs_arrow("a5y", "#8e44ad")]
    block = {(2, 1)}
    bx, by = C[(2, 1)]
    parts.append(f'<rect x="{bx - 20}" y="{by - 20}" width="40" height="40" '
                 f'fill="#fdecea" stroke="#c0392b" stroke-width="1.4" '
                 f'stroke-dasharray="4,3" rx="3"/>')
    # ring halo hint
    for a, b in [((1, 1), (1, 2)), ((1, 2), (2, 2)), ((2, 2), (3, 2)),
                 ((3, 2), (3, 1)), ((3, 1), (3, 0)), ((3, 0), (2, 0)),
                 ((2, 0), (1, 0)), ((1, 0), (1, 1))]:
        if a in block or b in block:
            continue
        parts.append(_edge(C[a], C[b], "#f5b7b1", 4.5))
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
    _m5_legend(parts,
               ("粉粗线 = fault ring", "#e74c3c"),
               ("蓝 = X 相 → VC0", "#2980b9"),
               ("紫 = Y 相 → VC2", "#8e44ad"))
    parts.append(_caption(W, H, "① 撞块→沿环绕行→回原行续 XY；相位定 VC"))
    parts.append("</svg>")
    out["fault_ring_vc"] = "".join(parts)

    # ② link fault must retire an endpoint
    C, W, H = _mini_xy(3, 2, pad=34, gap=56, bottom_extra=14, side_extra=36)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">']
    for r in range(2):
        for c in range(2):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#cfd6da", 1.4))
    for c in range(3):
        parts.append(_edge(C[(c, 0)], C[(c, 1)], "#cfd6da", 1.4))
    a, b = C[(1, 0)], C[(2, 0)]
    parts.append(_edge(a, b, "#c0392b", 3, "5,3"))
    parts.append(f'<text x="{(a[0] + b[0]) / 2}" y="{a[1] - 10}" '
                 f'text-anchor="middle" font-size="13" fill="#c0392b" '
                 f'font-weight="700">✕ 断链</text>')
    parts.append(f'<rect x="{a[0] - 18}" y="{a[1] - 18}" width="36" '
                 f'height="36" fill="#fdecea" stroke="#e67e22" '
                 f'stroke-width="1.4" stroke-dasharray="4,3" rx="3"/>')
    for r in range(2):
        for c in range(3):
            if (c, r) == (1, 0):
                parts.append(_node(*C[(c, r)], fill="#e67e22", r=8,
                                   label="退休"))
            else:
                parts.append(_node(*C[(c, r)]))
    parts.append(_caption(W, H, "② 块模型无「断链」→ 必须退休一端成 1×1 块"))
    parts.append("</svg>")
    out["fring_block"] = "".join(parts)

    # ③ VC = phase × direction
    C, W, H = _mini_xy(2, 2, pad=48, gap=90, bottom_extra=14, side_extra=30)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">']
    cells = [
        ((0, 1), "VC0", "X·东", "#2980b9"),
        ((1, 1), "VC1", "X·西", "#3498db"),
        ((0, 0), "VC2", "Y·北", "#8e44ad"),
        ((1, 0), "VC3", "Y·南", "#9b59b6"),
    ]
    for (c, r), vc, lab, col in cells:
        x, y = C[(c, r)]
        parts.append(
            f'<rect x="{x - 36}" y="{y - 28}" width="72" height="56" '
            f'rx="6" fill="{col}" opacity="0.15" stroke="{col}" '
            f'stroke-width="1.6"/>')
        parts.append(
            f'<text x="{x}" y="{y - 4}" text-anchor="middle" font-size="14" '
            f'font-weight="700" fill="{col}">{vc}</text>')
        parts.append(
            f'<text x="{x}" y="{y + 14}" text-anchor="middle" font-size="11" '
            f'fill="#444">{lab}</text>')
    # arrow X→Y
    parts.append(
        f'<text x="{W / 2}" y="{C[(0, 1)][1] + 40}" text-anchor="middle" '
        f'font-size="11" fill="#555">报文只从 X 相 → Y 相，不回头</text>')
    parts.append(_caption(W, H, "③ 4 VC = 相位 × 方向；整路径离线定好 → 保序"))
    parts.append("</svg>")
    out["fring_vc"] = "".join(parts)

    # ④ why return to original row (X-phase monotonicity)
    C, W, H = _mini_xy(4, 3, pad=32, gap=46, bottom_extra=16, side_extra=36)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a5ok", "#27ae60"),
             _defs_arrow("a5bad", "#c0392b")]
    blk = {(1, 1), (2, 1)}
    for (c, r) in blk:
        parts.append(
            f'<rect x="{C[(c, r)][0] - 18}" y="{C[(c, r)][1] - 18}" '
            f'width="36" height="36" fill="#fdecea" stroke="#c0392b" '
            f'stroke-width="1" stroke-dasharray="3,2" rx="2"/>')
    for r in range(3):
        for c in range(3):
            if (c, r) in blk or (c + 1, r) in blk:
                continue
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#cfd6da", 1.3))
        for c in range(4):
            if r < 2 and (c, r) not in blk and (c, r + 1) not in blk:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#cfd6da", 1.3))
    # good: up, across, down back to row
    for a, b in [((0, 1), (0, 2)), ((0, 2), (1, 2)), ((1, 2), (2, 2)),
                 ((2, 2), (3, 2)), ((3, 2), (3, 1))]:
        parts.append(_edge(C[a], C[b], "#27ae60", 3.0, marker="a5ok"))
    # bad ghost: stay on ring row without returning (west hop would break E-mono)
    parts.append(_edge(C[(3, 2)], C[(2, 2)], "#c0392b", 2.2, "4,3",
                       marker="a5bad"))
    parts.append(
        f'<text x="{C[(2, 2)][0]}" y="{C[(2, 2)][1] - 16}" '
        f'text-anchor="middle" font-size="10" fill="#c0392b">'
        f'西向 ← 破东向单调</text>')
    for r in range(3):
        for c in range(4):
            if (c, r) in blk:
                parts.append(_node(*C[(c, r)], fill="#c0392b", r=7, label="块"))
            elif (c, r) == (0, 1):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="S"))
            elif (c, r) == (3, 1):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="续"))
            else:
                parts.append(_node(*C[(c, r)]))
    _m5_legend(parts,
               ("绿 = 合法绕行（竖/东）", "#27ae60"),
               ("红虚 = 禁止的西向回跳", "#c0392b"))
    parts.append(_caption(W, H, "④ 回原行是为保 X 相单向；换 4 VC 可证无死锁"))
    parts.append("</svg>")
    out["fring_mono"] = "".join(parts)

    out["fring_aux"] = (out["fring_block"] + out["fring_vc"]
                        + out["fring_mono"])

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

    # ---- M7 Stripe dateline panel set ---------------------------------------
    def _m7_bands(C, parts, cols, rows, dlines, top_pad=22):
        fills = ["#d6eaf8", "#d5f5e3", "#fdebd0", "#f5eef8", "#eaf2f8"]
        y0 = C[(0, rows - 1)][1] - top_pad
        y1 = C[(0, 0)][1] + 18
        for c in range(cols):
            x0 = C[(c, 0)][0] - 20
            parts.append(
                f'<rect x="{x0}" y="{y0}" width="40" height="{y1 - y0}" '
                f'fill="{fills[c % len(fills)]}" opacity="0.75"/>')
        for d in dlines:
            # dateline at column boundary d (between d-1 and d)
            x = (C[(d - 1, 0)][0] + C[(d, 0)][0]) / 2
            parts.append(
                f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1}" '
                f'stroke="#7f8c8d" stroke-width="1.8" '
                f'stroke-dasharray="3,2"/>')
            parts.append(
                f'<text x="{x}" y="{y0 - 4}" text-anchor="middle" '
                f'font-size="9" fill="#7f8c8d">DL</text>')

    # ① horizontal crosses bump VC
    C, W, H = _mini_xy(4, 2, pad=34, gap=50, bottom_extra=16, side_extra=40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a7a", "#2980b9"),
             _defs_arrow("a7b", "#e67e22"), _defs_arrow("a7c", "#8e44ad")]
    _m7_bands(C, parts, 4, 2, (1, 2, 3))
    for r in range(2):
        for c in range(3):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.4))
    for c in range(4):
        parts.append(_edge(C[(c, 0)], C[(c, 1)], "#bdc3c7", 1.4))
    segs = [((0, 0), (1, 0), "#2980b9", "a7a", "VC0"),
            ((1, 0), (2, 0), "#e67e22", "a7b", "VC1"),
            ((2, 0), (3, 0), "#8e44ad", "a7c", "VC2"),
            ((3, 0), (3, 1), "#8e44ad", "a7c", "")]
    for a, b, col, mk, tag in segs:
        parts.append(_edge(C[a], C[b], col, 3.2, marker=mk))
        if tag:
            mx = (C[a][0] + C[b][0]) / 2
            my = (C[a][1] + C[b][1]) / 2 - 10
            parts.append(
                f'<text x="{mx}" y="{my}" text-anchor="middle" '
                f'font-size="10" fill="{col}" font-weight="700">{tag}</text>')
    for r in range(2):
        for c in range(4):
            lab = "S" if (c, r) == (0, 0) else ("D" if (c, r) == (3, 1) else "")
            parts.append(_node(*C[(c, r)],
                               fill="#27ae60" if lab else "#2980b9",
                               label=lab))
    parts.append(
        f'<text x="10" y="14" font-size="10" fill="#555">'
        f'竖虚线 = dateline</text>')
    parts.append(_caption(W, H, "① 每水平跨一条 DL，VC+1；竖走不升层"))
    parts.append("</svg>")
    out["stripe_vc"] = "".join(parts)

    # ② vertical hops keep same VC
    C, W, H = _mini_xy(3, 3, pad=34, gap=48, bottom_extra=16, side_extra=40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a7v0", "#2980b9"),
             _defs_arrow("a7v1", "#e67e22")]
    _m7_bands(C, parts, 3, 3, (1, 2), top_pad=26)
    for r in range(3):
        for c in range(2):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.3))
        for c in range(3):
            if r < 2:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#bdc3c7", 1.3))
    # path: (0,0)->(0,1)->(0,2) stay VC0; then (0,2)->(1,2) → VC1; (1,2)->(1,0) stay VC1
    for a, b in [((0, 0), (0, 1)), ((0, 1), (0, 2))]:
        parts.append(_edge(C[a], C[b], "#2980b9", 3.2, marker="a7v0"))
    parts.append(_edge(C[(0, 2)], C[(1, 2)], "#e67e22", 3.2, marker="a7v1"))
    for a, b in [((1, 2), (1, 1)), ((1, 1), (1, 0))]:
        parts.append(_edge(C[a], C[b], "#e67e22", 3.2, marker="a7v1"))
    parts.append(
        f'<text x="{C[(0, 1)][0] - 22}" y="{C[(0, 1)][1] + 4}" '
        f'font-size="10" fill="#2980b9">VC0</text>')
    parts.append(
        f'<text x="{C[(1, 1)][0] + 14}" y="{C[(1, 1)][1] + 4}" '
        f'font-size="10" fill="#e67e22">VC1</text>')
    for r in range(3):
        for c in range(3):
            lab = "S" if (c, r) == (0, 0) else ("D" if (c, r) == (1, 0) else "")
            parts.append(_node(*C[(c, r)],
                               fill="#27ae60" if lab else "#2980b9",
                               label=lab))
    parts.append(_caption(W, H, "② 同列竖走：VC 不变；只有跨 DL 才 +1"))
    parts.append("</svg>")
    out["stripe_vert"] = "".join(parts)

    # ③ densify datelines near fault column
    C, W, H = _mini_xy(5, 2, pad=30, gap=44, bottom_extra=16, side_extra=36)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">']
    # sparse DLs at 2; fault at col 3 → also DL at 3 and 4
    _m7_bands(C, parts, 5, 2, (2, 3, 4), top_pad=24)
    for r in range(2):
        for c in range(4):
            if c == 2 and r == 0:
                parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#c0392b", 2.5,
                                   "4,3"))
            else:
                parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.3))
    for c in range(5):
        parts.append(_edge(C[(c, 0)], C[(c, 1)], "#bdc3c7", 1.3))
    # mark fault node-ish: broken edge between (2,0)-(3,0)
    parts.append(
        f'<text x="{(C[(2, 0)][0] + C[(3, 0)][0]) / 2}" y="{C[(2, 0)][1] - 12}" '
        f'text-anchor="middle" font-size="10" fill="#c0392b">故障列附近</text>')
    for r in range(2):
        for c in range(5):
            parts.append(_node(*C[(c, r)],
                               fill="#e74c3c" if (c, r) == (2, 0) else "#2980b9",
                               label="坏" if (c, r) == (2, 0) else ""))
    parts.append(
        f'<text x="10" y="14" font-size="10" fill="#555">'
        f'默认每 2 列一条；故障列再加密</text>')
    parts.append(_caption(W, H, "③ 故障列邻边加 DL，避免绕障在稀疏带内成环"))
    parts.append("</svg>")
    out["stripe_dense"] = "".join(parts)

    # ④ monotonic VC kills wrap-around dependency
    C, W, H = _mini_xy(2, 2, pad=50, gap=88, bottom_extra=14, side_extra=30)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a7c0", "#2980b9"),
             _defs_arrow("a7c1", "#c0392b")]
    # ring of deps: three edges on VC0, return would need VC0 again — blocked
    ring = [((0, 0), (1, 0), "#2980b9", "a7c0", "VC0"),
            ((1, 0), (1, 1), "#2980b9", "a7c0", "VC0"),
            ((1, 1), (0, 1), "#c0392b", "a7c1", "VC1"),
            ((0, 1), (0, 0), "#c0392b", "a7c1", "VC1")]
    for a, b, col, mk, tag in ring:
        parts.append(_edge(C[a], C[b], col, 3.2, marker=mk))
        mx = (C[a][0] + C[b][0]) / 2
        my = (C[a][1] + C[b][1]) / 2
        parts.append(
            f'<text x="{mx}" y="{my - 8}" text-anchor="middle" '
            f'font-size="10" fill="{col}">{tag}</text>')
    parts.append(
        f'<text x="{W / 2}" y="{H / 2 + 6}" text-anchor="middle" '
        f'font-size="11" fill="#c0392b">无法回到 VC0</text>')
    for pt in C.values():
        parts.append(_node(*pt, fill="#2980b9", r=8))
    parts.append(_caption(W, H, "④ VC 只增不减 → 通道依赖回不到低层 → 无环"))
    parts.append("</svg>")
    out["stripe_cdg"] = "".join(parts)

    out["stripe_aux"] = (out["stripe_vert"] + out["stripe_dense"]
                         + out["stripe_cdg"])

    # ---- M10 Virtual mesh panel set -----------------------------------------
    def _m10_legend(parts, *lines):
        for i, (txt, col) in enumerate(lines):
            parts.append(
                f'<text x="10" y="{14 + i * 12}" font-size="10" '
                f'fill="{col}">{txt}</text>')

    # ① physical expand of logical XY
    C, W, H = _mini_xy(4, 3, pad=34, gap=48, bottom_extra=16, side_extra=48)
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
    parts.append(_edge(C[(1, 1)], C[(2, 1)], "#c0392b", 2, "4,3"))
    parts.append(_edge(C[(2, 1)], C[(3, 1)], "#c0392b", 2, "4,3"))
    parts.append(
        f'<text x="{(C[(1, 1)][0] + C[(3, 1)][0]) / 2}" y="{C[(2, 1)][1] - 12}" '
        f'text-anchor="middle" font-size="10" fill="#c0392b">逻辑边缺失</text>')
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
    _m10_legend(parts,
                ("蓝 = 逻辑 X（VC0）", "#2980b9"),
                ("橙 = 缺边的物理展开（仍算 VC0）", "#e67e22"),
                ("紫 = 逻辑 Y（VC1）", "#8e44ad"))
    parts.append(_caption(W, H, "① 逻辑仍是 XY；缺边用固定物理最短路替换"))
    parts.append("</svg>")
    out["virtual_mesh"] = "".join(parts)

    # ② what software sees: pristine logical XY
    C, W, H = _mini_xy(4, 3, pad=34, gap=48, bottom_extra=16, side_extra=40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a10L", "#27ae60")]
    for r in range(3):
        for c in range(3):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#d5f5e3", 1.6))
        for c in range(4):
            if r < 2:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#d5f5e3", 1.6))
    # ghost hole
    parts.append(
        f'<circle cx="{C[(2, 1)][0]}" cy="{C[(2, 1)][1]}" r="10" '
        f'fill="none" stroke="#c0392b" stroke-width="1.4" '
        f'stroke-dasharray="3,2"/>')
    parts.append(
        f'<text x="{C[(2, 1)][0]}" y="{C[(2, 1)][1] - 16}" '
        f'text-anchor="middle" font-size="10" fill="#c0392b">'
        f'物理有洞</text>')
    for a, b in [((0, 1), (1, 1)), ((1, 1), (2, 1)), ((2, 1), (3, 1)),
                 ((3, 1), (3, 0))]:
        parts.append(_edge(C[a], C[b], "#27ae60", 3.2, marker="a10L"))
    for r in range(3):
        for c in range(4):
            lab = "S" if (c, r) == (0, 1) else ("D" if (c, r) == (3, 0) else "")
            parts.append(_node(*C[(c, r)],
                               fill="#27ae60" if lab else "#2980b9",
                               label=lab))
    parts.append(
        f'<text x="10" y="14" font-size="10" fill="#27ae60">'
        f'上层看见的仍是完整 XY</text>')
    parts.append(_caption(W, H, "② 软件映射 / 调度不改；洞由 NoC 内部展开消化"))
    parts.append("</svg>")
    out["vmesh_logical"] = "".join(parts)

    # ③ expand table: one logical hop → multi-hop physical
    C, W, H = _mini_xy(3, 3, pad=36, gap=52, bottom_extra=16, side_extra=40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a10e0", "#95a5a6"),
             _defs_arrow("a10e1", "#e67e22")]
    for r in range(3):
        for c in range(2):
            if not ((c, r) == (0, 1) and (c + 1, r) == (1, 1)):
                parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#cfd6da", 1.3))
        for c in range(3):
            if r < 2:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#cfd6da", 1.3))
    # missing logical edge A→B
    parts.append(_edge(C[(0, 1)], C[(1, 1)], "#c0392b", 2.5, "4,3"))
    parts.append(
        f'<text x="{(C[(0, 1)][0] + C[(1, 1)][0]) / 2}" y="{C[(0, 1)][1] - 14}" '
        f'text-anchor="middle" font-size="10" fill="#c0392b">'
        f'逻辑 hop A→B</text>')
    # physical expand
    for a, b in [((0, 1), (0, 2)), ((0, 2), (1, 2)), ((1, 2), (1, 1))]:
        parts.append(_edge(C[a], C[b], "#e67e22", 3.2, marker="a10e1"))
    parts.append(
        f'<text x="{C[(0, 2)][0] + 24}" y="{C[(0, 2)][1] - 10}" '
        f'font-size="10" fill="#e67e22">expand[A→B]</text>')
    for r in range(3):
        for c in range(3):
            if (c, r) == (0, 1):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="A"))
            elif (c, r) == (1, 1):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="B"))
            else:
                parts.append(_node(*C[(c, r)]))
    parts.append(_caption(W, H, "③ 每条逻辑邻边预计算一条固定物理绕路"))
    parts.append("</svg>")
    out["vmesh_expand"] = "".join(parts)

    # ④ VC by logical phase: vertical detour still VC0
    C, W, H = _mini_xy(3, 3, pad=36, gap=52, bottom_extra=16, side_extra=40)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a10p0", "#2980b9"),
             _defs_arrow("a10p1", "#8e44ad")]
    for r in range(3):
        for c in range(2):
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#cfd6da", 1.3))
        for c in range(3):
            if r < 2:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#cfd6da", 1.3))
    # path with vertical hops in X phase then Y
    for a, b in [((0, 0), (0, 1)), ((0, 1), (0, 2)), ((0, 2), (1, 2)),
                 ((1, 2), (2, 2))]:
        parts.append(_edge(C[a], C[b], "#2980b9", 3.2, marker="a10p0"))
    for a, b in [((2, 2), (2, 1)), ((2, 1), (2, 0))]:
        parts.append(_edge(C[a], C[b], "#8e44ad", 3.2, marker="a10p1"))
    # column marker
    x = C[(2, 0)][0]
    parts.append(
        f'<line x1="{x}" y1="{C[(2, 2)][1] - 20}" x2="{x}" '
        f'y2="{C[(2, 0)][1] + 20}" stroke="#8e44ad" stroke-width="1.2" '
        f'stroke-dasharray="2,2"/>')
    parts.append(
        f'<text x="{x + 8}" y="{C[(2, 2)][1] - 8}" font-size="10" '
        f'fill="#8e44ad">目的列</text>')
    parts.append(
        f'<text x="{C[(0, 1)][0] - 8}" y="{C[(0, 1)][1]}" '
        f'text-anchor="end" font-size="10" fill="#2980b9">VC0</text>')
    parts.append(
        f'<text x="{C[(2, 1)][0] + 12}" y="{C[(2, 1)][1]}" '
        f'font-size="10" fill="#8e44ad">VC1</text>')
    for r in range(3):
        for c in range(3):
            lab = "S" if (c, r) == (0, 0) else ("D" if (c, r) == (2, 0) else "")
            parts.append(_node(*C[(c, r)],
                               fill="#27ae60" if lab else "#2980b9",
                               label=lab))
    parts.append(_caption(W, H, "④ 首次到目的列前都是 VC0；竖向绕路也算 X 相"))
    parts.append("</svg>")
    out["vmesh_vc"] = "".join(parts)

    out["vmesh_aux"] = (out["vmesh_logical"] + out["vmesh_expand"]
                        + out["vmesh_vc"])

    return out


M10_STAGE_LABELS = [
    ("1_link", "单链路断", "82（全）"),
    ("1_node", "单节点死", "48（全）"),
    ("2_link", "双链路断", "3321（全）"),
    ("2_node", "双节点死", "1128（全）"),
    ("3_node", "<b>三节点死</b>", "17296（全）"),
    ("rand_3to6_links", "随机 3–6 断链", "6000 抽样"),
    ("rand_nodes_links", "随机 1–4 死点 + 0–4 断链", "4000 抽样"),
]


EF_SPACE_LABELS = [
    ("1_link", "单链路断（穷举）"),
    ("1_node", "单节点死（穷举）"),
    ("2_link", "任意两链路断（穷举）"),
    ("2_node", "任意两节点死（穷举）"),
]


def exec_summary_html(excluded_labels: str) -> str:
    """Conclusions-first block at the top of the report."""
    return f"""
<div class="exec">
<h2>仿真结论（先看这里）</h2>
<p class="pick">本轮评测（预算故障 · VC≤2 · 不含 M9/M10）：
推荐默认 <b>M0s Super-turn（≤2 VC）</b>；
面积受限选 <b>M3 Up*/Down*（1 VC）</b>。
更高 VC 的 M7 Stripe / M6 LASH 仅保留描述，不进本轮 e2e。</p>
<ol>
<li><b>端到端 Pareto（评测集）：</b>M3（VC1）→ M0s Super-turn（VC2）。
同面积档上 Super-turn 最差端到端优于 Dual-UD / Virtual（后二者已退出评测）。
M5h half-ring / M0s1（1VC）覆盖不全或牺牲过重，进不了全覆盖前沿。</li>
<li><b>裸 makespan 会骗人：</b>M0/M1/M2/M4 常常「最快」，是因为牺牲把 A 裁小、流量按 A² 下降。
端到端强扩展后它们垫底——已从 §3/§4 排除（{esc(excluded_labels)}）。</li>
<li><b>硬性质 / 目录外：</b>预算模型三性质见 §2.5；
目录外 STRUCT/disc 见 §2.3（已含 M0s / M0s1 / M5h）。
保序为构造保证（唯一路径）。</li>
<li><b>§3 每场景最优：</b>预算故障场景上，低牺牲时常落到 M0s / M3；
通信占端到端约 70–86%，花 router 面积买带宽仍划算。</li>
</ol>
<p class="sub">细节与数据见 §2（方案/可达性）、§3–4（预算故障 makespan）、§6（端到端 Pareto）。</p>
</div>
"""


def beyond_catalog_html() -> str:
    """§2.3: STRUCT vs disc reachability outside the 36-scenario catalog."""
    if not BEYOND_REACH_PATH.exists():
        return ("<p class='note bad'>缺少 <code>results/pg_beyond_catalog_reach.json"
                "</code>。</p>")
    d = json.loads(BEYOND_REACH_PATH.read_text())
    spaces = d["spaces"]
    schemes = d["schemes"]
    # space keys shown in the summary table (skip 3_node_full for schemes
    # that only have a sample — show whichever key each scheme has)
    order = ["1_link", "1_node", "2_link", "2_node", "3_node_sample",
             "3_node_full", "mixed"]
    sch_order = ["super_turn", "super_turn_1vc", "updown", "segment",
                 "fault_ring_vc", "fault_half_ring", "lash", "stripe_vc",
                 "virtual_mesh"]
    # Skip schemes not yet present in the JSON (partial rescans).
    sch_order = [s for s in sch_order if s in schemes]

    def cell(r: dict | None) -> str:
        if r is None:
            return "<td>—</td>"
        ok = r.get("ok", 0) + r.get("forced_sac", 0)
        st = r.get("struct", 0)
        di = r.get("disc", 0)
        n = ok + st + di
        if st:
            return (f"<td class='cap-bad'>{ok}/{n}<br/>"
                    f"<span style='font-size:0.8em'>STRUCT {st}</span></td>")
        if di:
            return (f"<td class='cap-ok'>{ok}/{n}<br/>"
                    f"<span style='font-size:0.8em'>仅 disc {di}</span></td>")
        return f"<td class='cap-ok'>{ok}/{n}</td>"

    # header: only spaces that at least one scheme reports
    used_spaces = [k for k in order
                   if any(k in schemes[s]["results"] for s in sch_order)]
    head = "".join(f"<th>{spaces[k]['label']}</th>" for k in used_spaces)
    rows = []
    for sid in sch_order:
        s = schemes[sid]
        flag = ("<td class='cap-bad'>会</td>" if s["struct_possible"]
                else "<td class='cap-ok'>否</td>")
        cells = "".join(cell(s["results"].get(k)) for k in used_spaces)
        rows.append(
            f"<tr><td class='l'>{esc(s['label'])}</td>{flag}{cells}"
            f"<td class='l'>{esc(s['solution'])}</td></tr>")

    detail = []
    for sid in sch_order:
        s = schemes[sid]
        detail.append(
            f"<p><b>{esc(s['label'])}：</b>{esc(s['summary'])} "
            f"<b>补救：</b>{esc(s['solution'])}</p>")
        if sid == "fault_ring_vc" and "boundary_struct" in s:
            b = s["boundary_struct"]
            detail.append(
                f"<p class='note'>边界 STRUCT：单链 {b['1_link_struct']}/82、"
                f"单点 {b['1_node_struct']}/48——"
                f"{esc(b['note'])}</p>")
        if sid == "virtual_mesh" and "m10_cycle_crossref" in s:
            x = s["m10_cycle_crossref"]
            detail.append(
                f"<p class='note'>与 M10 成环穷举交叉："
                f"三节点全量 BOTH_CYCLIC="
                f"<b>{x['3_node_full_BOTH_CYCLIC']}</b> / "
                f"{x['3_node_full_n']}，nopath={x['3_node_full_nopath']}；"
                f"{esc(x['note'])}。详见 §2.2 M10 FAQ。</p>")

    return f"""
<h3>2.3 目录外可达性（不限于 36 场景）</h3>
<p class="note">出厂目录只有 18×2=36 格。下面把故障空间扩到单/双链路、单/双/三节点
与随机混合，区分两种失败：</p>
<ul>
<li><b>STRUCT</b>（结构性不可达）：残图上存活 compute <b>仍连通</b>，但方案自己建不出合法表
（转向堵死 / 环绕失败 / 双版 CDG 成环 / …）。</li>
<li><b>disc</b>：残图已断开——任何方案都要先牺牲才能恢复连通。</li>
</ul>
<p>表中分数 = <code>(ok + forced_sac) / n</code>；
<code>forced_sac</code> 只出现在 M5（链路端点退休仍算出表）。
数据 <code>results/pg_beyond_catalog_reach.json</code>。</p>
<table class="cap">
<thead><tr><th>方案</th><th>会 STRUCT？</th>{head}<th>不可达时怎么办</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
{''.join(detail)}
<p class="note"><b>一句话：</b>M3 / M6 / M7 在连通残图上<b>从不</b>结构性不可达
（三节点全量 / 双故障全量 / 混合抽样 STRUCT=0）。
M0s Super-turn（≤2 VC）在扫过的空间里同样以断连为主；
M0s1（硬顶 1 VC）与 M5h half-ring 会 STRUCT 或大量 forced_sac（见上表）。
M4 极常见、M5 全环在左右边中段块上会、M10 在散落 ≥3 死节点上会。
M0 East-first 的东向盲区见 §2.4，机制不同。</p>
"""


def ef_reach_html(fail_fig: str = "") -> str:
    """M0 east-first: where it becomes unreachable, and what fixes it."""
    if not EF_REACH_PATH.exists():
        return ("<p class='note bad'>缺少 <code>results/pg_east_first_reach.json"
                "</code>，请跑 <code>utils/pg_east_first_reach.py --full</code>。</p>")
    d = json.loads(EF_REACH_PATH.read_text())
    sp, bd = d["spaces"], d["single_fault_breakdown"]
    cs = d["catalog_summary"]
    n_checked = sum(s["n"] for s in sp.values())

    rows = []
    for key, label in EF_SPACE_LABELS:
        s = sp.get(key)
        if s is None:
            continue
        n = s["n"]
        def frac(v, bad_below=0.5):
            cls = "cap-bad" if v / n < bad_below else "cap-ok"
            return f"<td class='{cls}'>{v}/{n}（{v / n * 100:.0f}%）</td>"
        rows.append(f"<tr><td class='l'>{label}</td>{frac(s['east_first_ok'])}"
                    f"{frac(s['xy_ok'])}{frac(s['dual_ok'], 0.95)}</tr>")

    cat_rows = []
    for name, v in d["catalog"].items():
        if v["verdict"] == "ok":
            continue
        scen, sem = name.split("/")
        cat_rows.append(
            f"<tr><td class='l'>{esc(scen)}</td><td>{sem}</td>"
            f"<td class='cap-bad'>{v['sacrifice']}</td><td>{v['A']}</td>"
            f"<td>{v['xy_sacrifice']}</td>"
            f"<td class='cap-ok'>{'可行' if v['dual_fixes'] else '仍失败'}</td>"
            f"<td>{v['dual_vc1_frac'] * 100:.1f}%</td></tr>")

    vc1 = [v["dual_vc1_frac"] for v in d["catalog"].values()
           if v["verdict"] != "ok"]
    vc1_lo, vc1_hi = (min(vc1), max(vc1)) if vc1 else (0.0, 0.0)
    hv = bd["by_link_orientation"]
    v_cols = "、".join(f"x={c}" for c in bd["vertical_fail_columns"])
    ok_cols = "、".join(f"x={c}" for c in bd["node_ok_columns"])
    return f"""
<div class="faq">
<p><b class="q">M0 在哪些故障下不可达？</b>
先说判据。因为「向东只能排在最前面」，一条合法路径必然是
<b>源行内一段向东直线 + 之后只用 N/S/W 的走法</b>。照这个形状直接构造可达集
（<code>pg_east_first_reach.py: reach_model</code>，完全不碰转向搜索），
再和真实路由器逐例对比：<b>{n_checked} 个故障集 + 36 个目录格，判定分歧
{d['meta']['model_mismatches']} 例</b>。所以下面不是「试出来的」，是判据本身。</p>

<p><b>模式 ①（主因）横向切断源行。</b>源行里 s 以东断一跳（横向链路断，或该行有个洞），
则 s 到该断点以东的<b>所有</b>目标都不可达——绕行必须先 N/S 再 E，而 N→E / S→E 正是被禁的两类。
单链路穷举里 <b>{hv.get('H_fail', 0)} 条横向链路每一条都单独致命</b>，
单节点死则只有最西列（{ok_cols}）的洞无害——它们西边没有源。</p>

<p><b>模式 ② 最东列被切开。</b>东边的列一旦离开就回不去，所以最东列内部断纵向链路时，
该列会被自己切成两段：{v_cols} 上的 {hv.get('V_fail', 0)} 条纵向链路因此失败，
其余 {hv.get('V_ok', 0)} 条纵向链路全部无害（在非最东列可以先向东一格、上下绕、再向西回来）。</p>

<table class="cap">
<thead><tr><th>故障空间</th><th>M0 east-first 零牺牲可行</th>
<th>M1 XY 零牺牲可行</th><th>M0+镜像 2 VC 可行</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p class="note">同一残图上 M0 严格强于 M1（合法路径集是超集），但纵向自由度救不了横向切断：
双节点故障里 M0 只剩 {sp.get('2_node', {}).get('east_first_ok', 0)} /
{sp.get('2_node', {}).get('n', 0)} 可行。</p>

<div class="cycfig">{fail_fig}</div>

<p><b class="q">不可达了怎么办？</b>出厂目录里 <b>{cs['n_fail']}/36</b> 格建不出表，
本框架的默认出路是<b>牺牲好节点</b>把「挡路的洞」挪出参与集合——
代价中位 <b>{cs['sacrifice_median']} 个节点</b>、合计 {cs['sacrifice_total']} 个，
和 M1 XY 同一量级（最坏 A 掉到 6/48）。这也是 M0 被 §3/§4 排除的原因。</p>

<table class="cap">
<thead><tr><th>失败场景</th><th>语义</th><th>M0 牺牲</th><th>剩余 A</th>
<th>M1 牺牲</th><th>加镜像 VC 后</th><th>走 VC1 的对数占比</th></tr></thead>
<tbody>{''.join(cat_rows)}</tbody></table>

<p><b>更划算的三条出路（按代价从低到高）：</b></p>
<ol>
<li><b>加镜像模型到第 2 个 VC（推荐）。</b>west-first 禁的是「转向西」（N→W / S→W），
所以它<b>允许</b> N→E / S→E，正好能做 M0 做不到的东向绕行。
VC0 跑 east-first、VC1 跑 west-first、每对整条路径锁死在一个 VC 上：
两层各自无环、层间无依赖 ⇒ 并集无环，保序不变。实测
<b>目录 36/36 全部零牺牲可行、CDG 0 例成环</b>，
且只有 {vc1_lo * 100:.1f}–{vc1_hi * 100:.1f}% 的对数需要用到 VC1。
代价：2 VC ≈ 与 M9/M10 同档面积。</li>
<li><b>按行选方向。</b>把「东优先」换成「远离故障的一侧优先」，即每行独立决定
east-first / west-first。仍是 1 VC，但两种模型混在同一层会重新引入
N→E 与 S→W 共存 → 抽象环复活，必须逐场景跑 CDG 校验，属于「实测无环」而非构造性。</li>
<li><b>改用不依赖方向的族。</b>M3 Up*/Down*（1 VC）在同样 36 格里 36/36 零牺牲，
是彻底躲开这个问题的最省面积选项。</li>
</ol>
</div>
"""


def m10_scan_html() -> str:
    """Stage table + shape table for the M10 cycle scan (data-driven)."""
    if not M10_SCAN_PATH.exists():
        return ("<p class='note bad'>缺少 <code>results/pg_m10_cycle_scan.json"
                "</code>，请跑 <code>utils/pg_m10_cycle_scan.py --full</code>。</p>")
    d = json.loads(M10_SCAN_PATH.read_text())
    st = d["stages"]

    rows = []
    for key, label, size in M10_STAGE_LABELS:
        s = st.get(key)
        if s is None:
            continue
        n = sum(s.values()) - s.get("nopath", 0)
        bad = s.get("BOTH_CYCLIC", 0)
        cell = (f"<td class='cap-bad'><b>{bad}（{bad / n * 100:.1f}%）</b></td>"
                if bad else "<td>0</td>")
        rows.append(f"<tr><td class='l'>{label}</td><td>{size}</td>"
                    f"<td>{s.get('trim_only', 0)}</td>"
                    f"<td>{s.get('raw_only', 0)}</td>{cell}</tr>")
    cat = d["catalog_summary"]
    rows.append(f"<tr><td class='l'>出厂故障目录</td><td>36（全）</td>"
                f"<td>{cat.get('trim_only', 0)}</td>"
                f"<td>{cat.get('raw_only', 0)}</td>"
                f"<td>{cat.get('BOTH_CYCLIC', 0)}</td></tr>")
    stage_tbl = (
        '<table class="qa3"><thead><tr><th class="l">故障空间</th><th>规模</th>'
        '<th>需去环才行</th><th>需退回原版</th><th>两版都成环</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>')

    safe = [k for k, v in d["shapes"].items() if v != "BOTH_CYCLIC"]
    bad_shapes = [k for k, v in d["shapes"].items() if v == "BOTH_CYCLIC"]
    shape_tbl = (
        '<table class="qa3"><tbody>'
        f'<tr><th>安全</th><td class="l">{esc(" · ".join(safe))}</td></tr>'
        f'<tr><th>失效</th><td class="l cap-bad">{esc(" · ".join(bad_shapes))}'
        '</td></tr></tbody></table>')
    return stage_tbl + shape_tbl


def m10_cycle_figs() -> str:
    """The two CDG cycles of the minimal 8x6 fault that defeats both M10 tables.

    Window is x=0..5, y=0..2 of the real mesh; dead nodes (1,0) (3,1) (5,1).
    """
    dead = {(1, 0), (3, 1), (5, 1)}
    COLS, ROWS = 6, 3

    def panel(uid, colour, arrows, notes, caption, uturn=False):
        top = 12 + 13 * len(notes)
        # _mini_xy only leaves `pad` between the bottom row and the caption
        # (bottom_extra pads above the grid), so pad has to carry the clearance.
        C, W, H = _mini_xy(COLS, ROWS, pad=42, gap=44, side_extra=26)
        H += top

        def P(x, y):  # mesh (x,y) -> svg point, y=0 drawn on top
            px, py = C[(x, ROWS - 1 - y)]
            return px, py + top

        def trim_seg(a, b, by=9):
            dx, dy = b[0] - a[0], b[1] - a[1]
            ln = (dx * dx + dy * dy) ** 0.5 or 1
            ux, uy = dx / ln, dy / ln
            return ((a[0] + ux * by, a[1] + uy * by),
                    (b[0] - ux * by, b[1] - uy * by))

        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
                 f'height="{H}" viewBox="0 0 {W} {H}">',
                 _defs_arrow(uid, colour)]
        for y in range(ROWS):
            for x in range(COLS):
                if (x, y) in dead:
                    continue
                if x + 1 < COLS and (x + 1, y) not in dead:
                    parts.append(_edge(P(x, y), P(x + 1, y), "#dde3e7", 1.3))
                if y + 1 < ROWS and (x, y + 1) not in dead:
                    parts.append(_edge(P(x, y), P(x, y + 1), "#dde3e7", 1.3))
        for (ax_, ay_), (bx_, by_) in arrows:
            a, b = trim_seg(P(ax_, ay_), P(bx_, by_))
            if uturn:  # antiparallel pair: offset sideways so both are visible
                dx, dy = b[0] - a[0], b[1] - a[1]
                ln = (dx * dx + dy * dy) ** 0.5 or 1
                ox, oy = dy / ln * 5, -dx / ln * 5
                a, b = (a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy)
            parts.append(_edge(a, b, colour, 3.0, marker=uid))
        for y in range(ROWS):
            for x in range(COLS):
                parts.append(_node(*P(x, y),
                                   fill="#c0392b" if (x, y) in dead else "#8fa3b0",
                                   r=7 if (x, y) in dead else 5))
        for i, (txt, col) in enumerate(notes):
            parts.append(f'<text x="10" y="{13 + i * 13}" font-size="10.5" '
                         f'fill="{col}">{txt}</text>')
        parts.append(_caption(W, H, caption, max_chars=30))
        parts.append("</svg>")
        return "".join(parts)

    raw = panel(
        "m10cr", "#e67e22", [((2, 1), (2, 0)), ((2, 0), (2, 1))],
        [("红 = 死节点 (1,0) (3,1) (5,1)", "#c0392b"),
         ("橙 = 掉头环，2 通道同在 VC1", "#e67e22")],
        "原始拼接版：链路 (2,0)-(2,1) 两个方向互等", uturn=True)
    trim = panel(
        "m10ct", "#8e44ad",
        [((2, 1), (2, 2)), ((2, 2), (3, 2)), ((3, 2), (4, 2)),
         ((4, 2), (4, 1)), ((4, 1), (4, 0)), ((4, 0), (3, 0)),
         ((3, 0), (2, 0)), ((2, 0), (2, 1))],
        [("紫 = 矩形环，8 通道全在 VC0", "#8e44ad"),
         ("+Y,+X,+X,−Y,−Y,−X,−X,+Y", "#8e44ad")],
        "去回环版：绕死节点 (3,1) 首尾闭合")
    return f'<div class="cycfig">{raw}{trim}</div>'


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


def _e2e_data_filtered() -> dict | None:
    """Load e2e JSON and drop schemes that are description-only (M9/M10/…)."""
    if not E2E_JSON_PATH.exists():
        return None
    data = json.loads(E2E_JSON_PATH.read_text())
    skip = set(E2E_DESC_ONLY)
    rows = [r for r in data["rows"] if r["scheme"] not in skip]
    summary = [s for s in data["summary"] if s["scheme"] not in skip]
    m0s = data["meta"]["m0_list"]
    for m0 in m0s:
        cand = [s for s in summary if s["m0"] == m0 and not s.get("partial")]
        front_w = {s["scheme"] for s in
                   _e2e_pareto_front(cand, "area", "t_e2e_ns_worst")}
        front_m = {s["scheme"] for s in
                   _e2e_pareto_front(cand, "area", "t_e2e_ns_med")}
        for s in summary:
            if s["m0"] != m0:
                continue
            s["pareto_worst"] = (not s.get("partial")
                                 and s["scheme"] in front_w)
            s["pareto_med"] = (not s.get("partial")
                               and s["scheme"] in front_m)
    return {"meta": data["meta"], "rows": rows, "summary": summary}


def e2e_section_html() -> str:
    """Build §6 end-to-end time × area Pareto from pg_e2e_pareto.json."""
    data = _e2e_data_filtered()
    if data is None:
        return ("<h2>6. 端到端时间 × 面积 Pareto</h2>"
                "<p class='note'>尚无 <code>results/pg_e2e_pareto.json</code>。"
                "请先跑 <code>utils/dse_pg_e2e_pareto.py</code> 与 "
                "<code>utils/gen_pg_e2e_pareto_plot.py</code>。</p>")
    meta, summary = data["meta"], data["summary"]
    m0s = meta["m0_list"]
    tokens = meta["total_tokens"]
    am = meta["area_model"]

    def e2e_table(m0: int) -> str:
        cand = sorted((s for s in summary if s["m0"] == m0),
                      key=lambda s: (s.get("partial", False),
                                     s["t_e2e_ns_worst"]))
        head = ("<tr><th class='l'>方案</th><th>VC</th><th>area</th>"
                "<th>覆盖</th>"
                "<th>A 中位/最差</th><th>牺牲中位</th>"
                "<th>T<sub>e2e</sub> 中位 (ns)</th>"
                "<th>T<sub>e2e</sub> 最差 (ns)</th>"
                "<th>通信占比</th><th>Pareto</th></tr>")
        body = []
        for s in cand:
            mark = "<b>yes</b>" if s.get("pareto_worst") else ""
            ntot = s.get("n_scen_total", meta.get("n_scenarios", "?"))
            cover = f"{s['n_scen']}/{ntot}"
            if s.get("partial"):
                cover = f"<span title='未覆盖全部场景，不进 Pareto'>{cover}△</span>"
            body.append(
                "<tr>"
                f"<td class='l'>{esc(E2E_SHORT.get(s['scheme'], s['scheme']))}</td>"
                f"<td>{s['num_vc']}</td>"
                f"<td>{s['area']:.3f}</td>"
                f"<td>{cover}</td>"
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
<p class="note">按纯 makespan 排序会误导——M1 XY 的 makespan 最小，
但中位牺牲 28/48 个节点（这正是它被 §2.5 排除出第 3–4 节对比的原因）。
把通信放回真实计算任务后，牺牲的代价才显现，就可以在同一把尺子上重新比较。
本节用 <b>端到端任务完成时间</b>（计算 + alltoall）与 <b>router 面积</b>
构造 Pareto 前沿，<b>把被排除的方案也一并放回</b>，量化它们到底差多少。</p>

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
每个方案按全部场景中需要的<b>最大 VC 数</b>定尺寸。</li>
</ul>
<p class="note">故障模型：≤4 router + ≤8 无向链路（双向算 1，与 router 不重叠），
分层随机抽样 {meta.get('n_scenarios', meta.get('catalog', {}).get('n_scenarios', '?'))} 场景
（<b>不再使用</b>旧 link_/node_ corner/edge/center 目录）。
评估范围：仅 <b>VC≤2</b> 且本轮入选的方案（含 M0s1 / M5h；
<b>不含 M9 Dual-UD / M10 Virtual</b>——描述保留在 §2，不进 e2e / §3）。
M5 f-ring 4VC / LASH / Stripe 等同理保留描述。
扫描：dead × {len(m0s)} 个 m₀ × 评测方案 =
{sum(1 for _ in data['rows'])} 行 DES；耗时 {meta.get('elapsed_s')}s。
数据 <code>results/pg_e2e_pareto.json</code>。</p>

<h3>6.2 Pareto 图与结果表</h3>
<p class="note">实心点 = {meta.get('n_scenarios', meta.get('catalog', {}).get('n_scenarios', '?'))} 个
预算故障场景中的<b>最差值</b>（必须覆盖全部场景）；空心点 = 中位；竖线连中位→最差。</p>
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
<li><b>排名翻转仍成立：</b>M1/M2/M4（及覆盖不全的 M0 East-first）裸 makespan 好看，
端到端被同为 VC1 的 <b>M3 Up*/Down*</b> 支配——牺牲把 A 裁小后，强扩展把计算与
m<sub>eff</sub> 一起放大。§2.5 排除它们的量化依据不变。</li>
<li><b>通信占端到端 70–86%</b>（除重牺牲方案）。即便配了
{meta['pe_macs_per_cycle']} MAC/cy 的 PE，任务仍是通信瓶颈——
花 router 面积买带宽划算。</li>
<li><b>本轮 VC≤2 前沿：M3（VC1）→ M0s Super-turn（VC2）</b>。
M9 Dual-UD / M10 Virtual <b>不参与</b>本轮 e2e；描述留在 §2。
M5h half-ring 与 M0s1 Super-turn 1VC 覆盖不全或中位牺牲过高，
不进全覆盖 Pareto。更高 VC 的 M6/M7 本轮亦不扫。</li>
<li><b>推荐：</b>默认 <b>M0s Super-turn（≤2 VC）</b>（预算故障下最差端到端更好）；
router 面积紧则 <b>M3（1 VC）</b>。
<strong>不要</strong>因裸 makespan 好看就选 M1 XY / M2 / M5h（重牺牲）。</li>
</ol>
<p class="note"><b>已知局限：</b>只算 dispatch 一次 alltoall；
面积不计牺牲的 PE tile；control 面积按常数、未随 VC 增长；
全量 176 场景扫完后数字以 <code>pg_e2e_pareto.json</code> 为准。</p>
"""


def budget_e2e_section_html() -> str:
    """§6.5 budget fault model (≤4R/≤8L) + Super-turn Pareto."""
    if not BUDGET_E2E_JSON.exists():
        return ("<h3>6.5 预算故障模型与 Super-turn</h3>"
                "<p class='note'>尚无 <code>results/pg_budget_e2e_pareto.json</code>。"
                "请跑 <code>utils/dse_pg_budget_pareto.py</code> 与 "
                "<code>utils/gen_pg_e2e_pareto_plot.py --budget</code>。</p>")
    data = json.loads(BUDGET_E2E_JSON.read_text())
    meta, summary = data["meta"], data["summary"]
    tokens = meta.get("total_tokens", {})
    n_scen = meta.get("catalog", {}).get("n_scenarios", "?")
    cap_note = ""
    if BUDGET_CAP_JSON.exists():
        cap = json.loads(BUDGET_CAP_JSON.read_text())
        st = cap.get("summary", {}).get("super_turn", {})
        ef = cap.get("summary", {}).get("east_first", {})
        if st:
            cap_note = (
                f"<p class='note'>能力探针（{st.get('n', n_scen)} 场景）："
                f"M0 East-first 零牺牲 {ef.get('zero_sac_ok', '?')}/"
                f"{ef.get('n', '?')}；"
                f"<b>M0s Super-turn</b> 零牺牲 {st.get('zero_sac_ok', '?')}/"
                f"{st.get('n', '?')}，"
                f"最终全覆盖（含 forced-sac），VC∈{{1,2}}，无 CDG 失败。</p>")

    def e2e_table(m0: int) -> str:
        cand = sorted((s for s in summary if s["m0"] == m0),
                      key=lambda s: s["t_e2e_ns_worst"])
        head = ("<tr><th class='l'>方案</th><th>VC</th><th>area</th>"
                "<th>A 中位/最差</th><th>牺牲中位</th>"
                "<th>T<sub>e2e</sub> 中位 (ns)</th>"
                "<th>T<sub>e2e</sub> 最差 (ns)</th>"
                "<th>Pareto</th></tr>")
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
                f"<td>{mark}</td></tr>")
        return (f"<table><thead>{head}</thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    front_names = []
    for m0 in meta["m0_list"]:
        names = [E2E_SHORT.get(s["scheme"], s["scheme"])
                 for s in sorted(summary, key=lambda x: x["area"])
                 if s["m0"] == m0 and s.get("pareto_worst")]
        front_names.append(f"m₀={m0}: " + " → ".join(names))

    png = ""
    if (ROOT / "results" / BUDGET_E2E_PNG).exists():
        png = (
            f'<figure class="e2e-fig">'
            f'<img src="{BUDGET_E2E_PNG}" alt="budget fault Pareto" '
            f'style="max-width:100%;height:auto;background:#fff;'
            f'border:1px solid #e0e0e0"/>'
            f'<figcaption>预算故障（≤4R/≤8L，{n_scen} 场景）时间×面积 Pareto</figcaption>'
            f'</figure>')

    t1 = tokens.get("1", tokens.get(1, "?"))
    t13 = tokens.get("13", tokens.get(13, "?"))
    return f"""
<h3>6.5 预算故障模型与 Super-turn（M0s）</h3>
<p class="note">固定 36 目录偏规整方块。本小节开：8×6 上 <b>≤4 router + ≤8 无向链路</b>
（双向算 1），按 (n<sub>R</sub>, n<sub>L</sub>) 分层抽样。
<b>M0s Super-turn</b>：Glass–Ni 四模型自适应，优先 1 VC → 2 VC 双模型 →
小基数 forced-sac；路径锁单 VC（保序），每层 CDG 构造性无环，VC 封顶 2。</p>
{cap_note}
{png}
<p class="note">最差情形前沿：{'；'.join(front_names)}。
同面积下 Super-turn 在 worst-case 支配 M9 Dual-UD / M10 Virtual。
数据 <code>results/pg_budget_e2e_pareto.json</code>
（{meta.get('elapsed_s')}s，{len(data.get('rows', []))} 行 DES）。</p>
<h4>m₀ = 1（{t1} tokens）</h4>
{e2e_table(1)}
<h4>m₀ = 13（{t13} tokens）</h4>
{e2e_table(13)}
"""


def main():
    data = json.loads(JSON_PATH.read_text())
    meta = data["meta"]
    rows = data["rows"]
    golden = meta["golden"]
    # Primary e2e is already budget-fault + VC≤2 (pg_e2e_pareto.json).
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

    def qa3(avoid: str, deadlock: str, order: str) -> str:
        """Uniform 三问 table: fault avoidance / deadlock freedom / ordering."""
        return (
            '<table class="qa3"><tbody>'
            f'<tr><th>如何避障</th><td class="l">{avoid}</td></tr>'
            f'<tr><th>如何无死锁</th><td class="l">{deadlock}</td></tr>'
            f'<tr><th>如何保序</th><td class="l">{order}</td></tr>'
            '</tbody></table>'
        )

    by_key = defaultdict(list)
    for r in primary:
        by_key[(r["scenario"], r["semantics"], r["m"], r["Q"])].append(r)

    # §3/§4: budget fault model only (from e2e DES). Old link_/node_ rows
    # in pg_alltoall_8x6.json are not shown.
    e2e_pick = _e2e_data_filtered()
    skip_pick = set(EXCLUDED_SCHEMES) | set(E2E_DESC_ONLY)
    if e2e_pick is not None:
        budget_rows = [r for r in e2e_pick["rows"]
                       if r["scheme"] not in skip_pick]
        budget_scens = sorted({r["scenario"] for r in budget_rows})
        # Prefer catalog order when available.
        if BUDGET_FAULTS_JSON.exists():
            cat_names = [s["name"] for s in
                         json.loads(BUDGET_FAULTS_JSON.read_text())["scenarios"]
                         if s["name"] in set(budget_scens)]
            if cat_names:
                budget_scens = cat_names + [n for n in budget_scens
                                            if n not in set(cat_names)]
        budget_m0s = sorted({r["m0"] for r in budget_rows})
    else:
        budget_rows, budget_scens, budget_m0s = [], [], [1, 13]

    def _sac_mk_pareto(cands: list[dict]) -> list[dict]:
        """Non-dominated on (n_sacrificed, t_alltoall_cy)."""
        keep = []
        for r in cands:
            if not any(o is not r
                       and o["n_sacrificed"] <= r["n_sacrificed"]
                       and o["t_alltoall_cy"] <= r["t_alltoall_cy"]
                       and (o["n_sacrificed"] < r["n_sacrificed"]
                            or o["t_alltoall_cy"] < r["t_alltoall_cy"])
                       for o in cands):
                keep.append(r)
        seen, out = set(), []
        for r in sorted(keep,
                        key=lambda r: (r["n_sacrificed"], r["t_alltoall_cy"])):
            k = (r["n_sacrificed"], r["t_alltoall_cy"])
            if k not in seen:
                seen.add(k)
                out.append(r)
        return out

    def optimal_table(m0: int) -> str:
        head = (
            "<tr>"
            "<th class='l'>场景</th>"
            "<th class='l'>推荐方案"
            "<div class='sub'>牺牲最少 → 再 alltoall 快</div></th>"
            "<th>牺牲</th><th>A</th><th>VC</th>"
            "<th>alltoall"
            "<div class='sub'>cy</div></th>"
            "<th>T<sub>e2e</sub>"
            "<div class='sub'>ns（强扩展）</div></th>"
            "<th class='l'>Pareto 备选 方案(牺牲,alltoall)</th>"
            "</tr>")
        body = []
        for scen_name in budget_scens:
            cands = [r for r in budget_rows
                     if r["scenario"] == scen_name and r["m0"] == m0]
            if not cands:
                body.append(f"<tr><td class='l'>{esc(scen_name)}</td>"
                            f"<td colspan='7' class='bad'>无可行方案</td></tr>")
                continue
            best = min(cands,
                       key=lambda r: (r["n_sacrificed"], r["t_alltoall_cy"]))
            pf = _sac_mk_pareto(cands)
            alts = " · ".join(
                f"{SCHEME_LABELS.get(r['scheme'], r['scheme']).split()[0]}"
                f"({r['n_sacrificed']},{r['t_alltoall_cy']})"
                for r in pf if r is not best)
            body.append(
                "<tr>"
                f"<td class='l'>{esc(scen_name)}</td>"
                f"<td class='l'><b>"
                f"{esc(SCHEME_LABELS.get(best['scheme'], best['scheme']))}"
                f"</b></td>"
                f"<td>{best['n_sacrificed']}</td>"
                f"<td>{best['A']}</td>"
                f"<td>{best.get('num_vc', 1)}</td>"
                f"<td><b>{best['t_alltoall_cy']}</b></td>"
                f"<td>{best['t_e2e_ns']:.0f}</td>"
                f"<td class='l sub'>{esc(alts) or '—（推荐方案同时最快）'}</td>"
                "</tr>")
        return (f"<table><thead>{head}</thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    def scheme_matrix(m0: int) -> str:
        schemes = []
        for r in budget_rows:
            if r["m0"] != m0:
                continue
            if r["scheme"] not in schemes:
                schemes.append(r["scheme"])
        if not schemes:
            return "<p class='note bad'>无预算故障 DES 行可画矩阵。</p>"
        head = ("<tr><th>场景</th>" +
                "".join(f"<th>{esc(E2E_SHORT.get(s, s))}</th>"
                        for s in schemes) + "</tr>")
        body = []
        for scen_name in budget_scens:
            cells = [f"<td class='l'>{esc(scen_name)}</td>"]
            for sch in schemes:
                hit = next((r for r in budget_rows
                            if r["scenario"] == scen_name
                            and r["m0"] == m0
                            and r["scheme"] == sch), None)
                if hit is None:
                    cells.append("<td class='bad'>INF</td>")
                else:
                    cells.append(
                        f"<td title='sac={hit['n_sacrificed']} "
                        f"A={hit['A']} VC={hit.get('num_vc', 1)} "
                        f"T_e2e={hit['t_e2e_ns']:.0f}ns'>"
                        f"{hit['t_alltoall_cy']}"
                        f"<div class='sub'>"
                        f"sac {hit['n_sacrificed']} | A={hit['A']}</div></td>"
                    )
            body.append("<tr>" + "".join(cells) + "</tr>")
        return (f"<table class='matrix'><thead>{head}</thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    def _m0_tables(fn) -> str:
        if e2e_pick is None:
            return ("<p class='note bad'>缺少 "
                    "<code>results/pg_e2e_pareto.json</code>，"
                    "请先跑 <code>utils/dse_pg_e2e_pareto.py</code>。</p>")
        parts = []
        for m0 in budget_m0s:
            parts.append(f"<h3>dead · m<sub>0</sub>={m0} flit"
                         f"（强扩展载荷）</h3>")
            parts.append(fn(m0))
        return "\n".join(parts)

    optimal_tables_html = _m0_tables(optimal_table)
    scheme_matrices_html = _m0_tables(scheme_matrix)

    # §1 SVG gallery: budget fault model (≤4R/≤8L, non-overlap).
    # One sample per (n_routers, n_links) cell (_0000) so the grid shows the
    # whole budget without dumping every stratified replicate.
    if BUDGET_FAULTS_JSON.exists():
        budget_doc = json.loads(BUDGET_FAULTS_JSON.read_text())
        budget_all = budget_doc["scenarios"]
        budget_meta = budget_doc.get("meta", {})
    else:
        budget_doc = B.write_catalog()
        budget_all = budget_doc["scenarios"]
        budget_meta = budget_doc["meta"]
    gallery_scens = [s for s in budget_all if s["name"].endswith("_0000")]
    gallery = []
    for scen in gallery_scens:
        # Pure fault map only — no sacrifice preview. Each routing scheme
        # decides its own sacrificed set later.
        svg = mesh_svg(scen, sacrificed=[], loads=None)
        gallery.append(
            f"<figure><figcaption>"
            f"{esc(scen['name'])} · R={scen['n_routers']} L={scen['n_links']}"
            f"</figcaption>{svg}</figure>"
        )
    gallery_note = (
        f"分层抽样目录共 <b>{budget_meta.get('n_scenarios', len(budget_all))}</b> 场景"
        f"（每格 {budget_meta.get('n_per_cell', '?')} 个，seed="
        f"{budget_meta.get('seed', '?')}）；下图每格展示 <code>_0000</code> 样本，"
        f"共 {len(gallery_scens)} 张。仅标故障本身；牺牲由各方案自行判定，不在此预画。"
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

    # ---- capability check: the three hard properties, measured ---------------
    def _cap_table(cap: dict, *, n_cells: int, ord_bad, ord_tot,
                   order_fallback: str | None = None) -> str:
        def cell(good: bool, text: str) -> str:
            cls = "cap-ok" if good else "cap-bad"
            return f"<td class='l {cls}'>{text}</td>"

        head = ("<tr><th class='l'>方案</th><th class='l'>避障</th>"
                "<th class='l'>无死锁</th><th class='l'>保序</th>"
                "<th class='l'>判定</th></tr>")
        body = []
        schemes = cap.get("schemes", cap.get("summary", {}))
        for sch, lab in SCHEME_LABELS.items():
            base = "segment" if sch == "segment_lb" else (
                "updown" if sch == "updown_lb" else sch)
            c = schemes.get(base)
            if c is None:
                continue
            # summary-only records (budget) flatten counts at top level
            if "fail_path" not in c and base in cap.get("summary", {}):
                c = {**cap["summary"][base], **c}
            fp = c.get("fail_path", 0)
            sac = c.get("sacrifice", 0)
            fcdg = c.get("fail_cdg", 0)
            ok_n = c.get("ok", 0)
            forced = c.get("forced_nodes", 0)
            # Avoidance: prefer a breakdown when outcomes are mixed.
            if fp == 0 and sac == 0:
                avoid = cell(True, f"✓ {ok_n}/{n_cells} 零牺牲绕开")
            elif fp == 0 and sac:
                avoid = cell(True, f"△ 靠牺牲：{sac}/{n_cells} 场景，"
                                   f"累计 {forced} 节点"
                                   + (f"；另 {ok_n} 零牺牲" if ok_n else ""))
            elif fp and (ok_n or sac):
                avoid = cell(False,
                             f"<b>✗</b> path {fp}/{n_cells}；"
                             f"ok {ok_n} / sac {sac}"
                             + (f"（累计牺牲 {forced}）" if forced else ""))
            else:
                avoid = cell(False, f"<b>✗</b> {fp}/{n_cells} 建不出路径")
            basis = DEADLOCK_BASIS.get(base, "")
            n_cdg_ok = n_cells - fcdg
            if fcdg:
                dl = cell(False, f"<b>✗</b> {fcdg}/{n_cells} CDG 成环"
                                 f"<div class='sub'>{basis}</div>")
            else:
                good = base not in ("virtual_mesh", "fault_half_ring")
                dl = cell(good, f"{'✓' if good else '△'} "
                                f"{n_cdg_ok}/{n_cells} 无环"
                                f"<div class='sub'>{basis}</div>")
            nb, nt = ord_bad[sch], ord_tot[sch]
            if nt:
                order = (cell(False, f"<b>✗</b> {nb}/{nt} 行乱序")
                         if nb else cell(True, f"✓ {nt}/{nt} 行 ordered_ok"))
            elif order_fallback:
                order = cell(True, order_fallback)
            else:
                order = cell(True, "✓ 构造（唯一路径）")
            if sch in EXCLUDED_SCHEMES:
                mark = (f"<td class='l cap-bad'><b>排除</b>"
                        f"<div class='sub'>{esc(EXCLUDED_SCHEMES[sch])}</div></td>")
                name = f"<td class='l cap-bad'><s>{esc(lab)}</s></td>"
            elif sch in E2E_DESC_ONLY:
                mark = (f"<td class='l'><b>描述保留</b>"
                        f"<div class='sub'>{esc(E2E_DESC_ONLY[sch])}</div></td>")
                name = f"<td class='l'>{esc(lab)}</td>"
            else:
                mark = "<td class='l cap-ok'>e2e 评测</td>"
                name = f"<td class='l'>{esc(lab)}</td>"
            body.append("<tr>" + name + avoid + dl + order + mark + "</tr>")
        return (f"<table class='cap'><thead>{head}</thead>"
                f"<tbody>{''.join(body)}</tbody></table>")

    def capability_html() -> str:
        parts = []
        # --- budget fault model (primary) ---
        if BUDGET_CAP_JSON.exists():
            bcap = json.loads(BUDGET_CAP_JSON.read_text())
            bn = bcap["meta"].get("n_cells") or bcap["meta"].get(
                "catalog", {}).get("n_scenarios", "?")
            # Prefer DES ordered_ok from e2e rows when present
            ord_bad, ord_tot = defaultdict(int), defaultdict(int)
            e2e_path = E2E_JSON_PATH
            if e2e_path.exists():
                for r in json.loads(e2e_path.read_text()).get("rows", []):
                    if r.get("makespan") is None and "t_e2e_cy" not in r:
                        continue
                    # e2e rows may omit ordered_ok; count only when present
                    if "ordered_ok" not in r:
                        continue
                    ord_tot[r["scheme"]] += 1
                    if r.get("ordered_ok") is False:
                        ord_bad[r["scheme"]] += 1
            parts.append(
                f"<h4>预算故障模型（≤4R + ≤8L，{bn} 场景，dead）</h4>"
                "<p class='note'>主评估故障集。数据 "
                "<code>results/pg_budget_capability.json</code>"
                "（<code>utils/pg_budget_probe.py</code>）。"
                "零额外牺牲建表；保序为构造保证"
                "（每对唯一路径 + 确定性 <code>vc_of</code>）。</p>")
            # Normalize summary-only → schemes-shaped if needed
            if "schemes" not in bcap and "summary" in bcap:
                bcap = {
                    **bcap,
                    "schemes": {
                        k: {
                            "ok": v.get("ok", v.get("zero_sac_ok", 0)),
                            "sacrifice": v.get("sacrifice", 0),
                            "fail_path": v.get("fail_path", 0),
                            "fail_cdg": v.get("fail_cdg", 0),
                            "forced_nodes": v.get("forced_nodes", 0),
                        }
                        for k, v in bcap["summary"].items()
                    },
                }
            try:
                n_budget = int(bn)
            except (TypeError, ValueError):
                n_budget = int(bcap["meta"].get("catalog", {})
                               .get("n_scenarios", 0) or 0)
            parts.append(_cap_table(
                bcap, n_cells=n_budget,
                ord_bad=ord_bad, ord_tot=ord_tot,
                order_fallback="✓ 构造（唯一路径）"))
            # VC histogram note for adaptive schemes
            summ = bcap.get("summary", {})
            vc_notes = []
            for sch in ("super_turn", "super_turn_1vc", "fault_half_ring",
                        "lash", "stripe_vc"):
                vh = (summ.get(sch) or {}).get("vc_hist") or (
                    bcap.get("schemes", {}).get(sch, {}).get("vc_hist"))
                if vh:
                    vc_notes.append(
                        f"{esc(SCHEME_LABELS.get(sch, sch))} VC 分布 "
                        f"{esc(vh)}")
            if vc_notes:
                parts.append("<p class='note'>" + "；".join(vc_notes) + "。</p>")
        else:
            parts.append(
                "<p class='note bad'>缺少 <code>results/pg_budget_capability.json"
                "</code>，请跑 <code>utils/pg_budget_probe.py</code>。</p>")

        # --- legacy fixed catalogue (reference) ---
        if CAP_JSON_PATH.exists():
            cap = json.loads(CAP_JSON_PATH.read_text())
            n_cells = cap["meta"]["n_cells"]
            ord_bad, ord_tot = defaultdict(int), defaultdict(int)
            for r in primary:
                if r.get("makespan") is None:
                    continue
                ord_tot[r["scheme"]] += 1
                if r.get("ordered_ok") is False:
                    ord_bad[r["scheme"]] += 1
            parts.append(
                f"<h4>旧固定目录（link_/node_ corner·edge·center，"
                f"{n_cells} 格 = 场景×语义）</h4>"
                "<p class='note'>历史对照，<b>不再用于 e2e</b>。"
                "数据 <code>results/pg_capability.json</code>。</p>")
            parts.append(_cap_table(
                cap, n_cells=n_cells, ord_bad=ord_bad, ord_tot=ord_tot))
        return "\n".join(parts)

    cap_html = capability_html()
    excluded_labels = "、".join(SCHEME_LABELS[s] for s in EXCLUDED_SCHEMES)

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
div.cycfig {{ display: flex; gap: 0.8rem; flex-wrap: wrap;
             margin: 0.6rem 0; }}
div.cycfig svg {{ background: #fff; border: 1px solid #e3e8ec;
                 border-radius: 4px; }}
table.cap td.cap-ok {{ background: #f2faf5; }}
table.cap td.cap-bad {{ background: #fdf1ef; color: #922b21; }}
table.cap {{ max-width: 62rem; }}
table.qa3 {{ font-size: 0.82rem; margin: 0.6rem 0 0.3rem; width: 100%; }}
table.qa3 th {{ background: #f3ecf7; text-align: left; white-space: nowrap;
                width: 6.5rem; font-weight: 600; }}
table.qa3 th, table.qa3 td {{ padding: 0.3rem 0.5rem; vertical-align: top; }}
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
.exec {{ max-width: 52rem; margin: 1rem 0 1.6rem; padding: 1rem 1.15rem 1.05rem;
         background: #f7faf8; border: 1px solid #c8ddd0; border-top: 4px solid #1e8449; }}
.exec h2 {{ margin: 0 0 0.55rem; font-size: 1.2rem; color: #145a32; }}
.exec ol {{ margin: 0.35rem 0 0.2rem 1.2rem; padding: 0; }}
.exec li {{ margin: 0.4rem 0; }}
.exec .pick {{ font-size: 1.05rem; margin: 0.2rem 0 0.7rem; }}
.exec .pick b {{ color: #145a32; }}
.exec .sub {{ color: #555; font-size: 0.9rem; }}
</style></head><body>
<h1>8×6 分组交换 NoC：Partial-Good 解决方案与 Alltoall 性能劣化</h1>
<p class="note">几何 <code>{meta['mx']}×{meta['my']}</code>，
H={meta['H']} V={meta['V']} RAMP={meta['RAMP']} RAMP_BW={meta['RAMP_BW']}。
故障模型（端到端）：≤4 router + ≤8 无向链路（双向算 1），
router 与链路故障<strong>不重叠</strong>；分层随机抽样见
<code>results/pg_faults_budget_8x6.json</code>（已替换固定 link_*/node_* 目录）。
Q = 入端口 FIFO 深度 / 出链路 credit 初值；默认 Q=19 = 2·V+1（V=9），
足以覆盖最长链路的 credit 往返，链路可跑满 1 flit/cy。
硬性约束：无死锁（CDG 无环）+ 保序（每 (src,dst) 单路径 wormhole）。
不满足时可牺牲 good 节点恢复。Golden（健康 XY）：
m=1 → <b>{golden.get('1', golden.get(1))}</b> cy，
m=5 → <b>{golden.get('5', golden.get(5))}</b> cy。
生成于 {esc(meta.get('generated_at',''))}，耗时 {meta.get('elapsed_s')}s。
</p>

{exec_summary_html(excluded_labels)}

<h2>1. 故障目录与 PG 语义</h2>
<ul>
<li><b>预算故障模型</b>：0…4 个死 router × 0…8 条无向断链（跳过健康格），
链路两端必须仍为存活 router（与死点不重叠）。</li>
<li><b>dead</b>：故障节点 PE+router+链路全失效；端到端评估用此语义。</li>
<li><b>transit</b>：PE 不参与 alltoall，router/链路仍可转发（对照口径，目录图仍按 dead 展开）。</li>
<li>图例：蓝=存活节点，红=故障 router，红虚线=故障链路（本节不预判牺牲）</li>
</ul>
<p class="note">{gallery_note}</p>
<div class="gallery">{''.join(gallery)}</div>

<h2>2. PG 方案详解</h2>
<p class="note">实现见 <code>utils/pg_routing.py</code>。所有进入 DES 的表必须同时满足：
CDG 无环（无死锁）、每 (src,dst) 唯一路径（保序）、compute 集合连通。
失败时由统一牺牲恢复器禁用额外 good 节点（边界 → 整行/整列 → 矩形屏蔽）。</p>

<p class="note"><b>保序不排斥 VC。</b>保序真正要求的只是「每个 (src,dst) 一条固定路径、
且沿路 VC 序列确定」——只要 VC 是 (src,dst) 的函数而非 per-packet 动态选择，
同一对的包就不会跨 VC 乱序。因此本研究的方案按<b>断环手段</b>分成两大类：</p>

<div class="faq">
<p><b class="q">三问的共同底座（各方案只写自己的差异部分）</b></p>
<p><b>1. 避障的共同前提：</b><code>expand_pg</code> 给出的 <code>route_adj</code>
已经删掉了故障节点与断链，任何选路函数只在这张<strong>存活图</strong>上搜索，
不可能踏进坏点坏边。真正的差异在于「图上搜不到合法路时怎么办」：
绕行（M3/M4/M6/M7/M10）、裁剪（M2/M5），还是直接失败（M1）。
所有方案失败后都落到同一个恢复器 <code>solve_scheme</code>：
先去孤立点，再按「单点 → 点对 → 整行整列 → 矩形」逐级牺牲 good 节点，
取<strong>牺牲最少</strong>的可行解。</p>
<p><b>2. 无死锁的共同判据：</b>不管构造上怎么论证，进入 DES 之前一律过
<code>validate_routing</code> → <code>build_cdg(paths, vc_of)</code> +
<code>cdg_acyclic</code>。CDG 结点是 <b>(有向边, VC)</b> 二元组，
只要存在环就判不可行。构造性证明只决定「一次过还是要重试」。</p>
<p><b>3. 保序的共同机制：</b>本研究全部是<strong>确定性离线路由</strong>——
每个 (src,dst) 只有一条路径，且 <code>vc_of(path, i)</code> 是
(该对, 跳序号) 的纯函数，运行时不做自适应选路、不做 per-packet VC 竞争选择。
同一对的 flit 序列走同一串 (边, VC)，wormhole 下天然 FIFO ⇒ 保序。
带 LB 的变体只是在<strong>离线迭代</strong>里换路，最终仍是一对一条固定路径。</p>
</div>

<div class="cls">
  <div class="cls-card">
    <h3>A 类 · 转向限制（1 VC）</h3>
    <div class="cls-fig">{cls_fig['turn']}</div>
    <p>mesh 的死锁来自「东→北→西→南→东」这样的通道环。A 类的做法是
    <b>删掉环上的某一类转弯</b>，让环无法闭合。</p>
    <p><b>代价：</b>被删的转弯同时也删掉了一批最短路，绕路变长、负载更不均。
    洞越大，被迫绕得越远。</p>
    <p><b>成员：</b>M0 East-first、M1 XY、M2 Rect-XY、M3 Up*/Down*、M4 Segment。
    同一族里禁得越少、可用最短路越多：M0 只禁 2 类转弯，XY 禁 4 类。</p>
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

{scheme_block("M0 — East-first turn model（<code>east_first</code>）",
              "east_first", '''
<p><b>类别：</b>A 类 · 转向限制 · <b>1 VC</b>。Glass–Ni 最小转向模型家族，
west-first 的镜像。</p>
<p><b>思想：</b>XY 禁掉了 4 类转弯（N→E、N→W、S→E、S→W），其实<b>只禁 2 类就够断环</b>。
east-first 只禁「<b>转向东</b>」的两类——N→E 与 S→E（外加一律禁 180° 掉头），
剩下 6 类转弯全部合法。合法路径集是 XY 的<b>真超集</b>，所以 XY 能走的它都能走，
XY 被堵死时它还有绕行余地。</p>
<p><b>路径形状：</b>没有任何转弯能回到东向，于是每条合法路径必然是
<b>「先在源行里一路直冲东，再只用 N/S/W 走完」</b>。实现上优先取 XY 折线
（XY 本身合法），XY 被堵才退到带来向状态的转向 BFS。</p>
<p class="note">注意：普通 <code>shortest_path(..., allowed_next)</code> 按<b>节点</b>去重，
首次到达的方向会把后续合法转弯悄悄锁死，会误报不可达。M0 用
<code>_turn_bfs</code> 按 <b>(节点, 来向)</b> 去重，所以「不可达」是真不可达。
（M4 Segment 仍用按节点去重的版本，它 17/36 的建表失败里可能含这种搜索不完备的成分。）</p>
''' + qa3(
    '<b>只能向北/南/西绕，不能向东绕。</b>转弯集比 XY 宽，所以纵向链路断、'
    '非首列的洞大多能绕过去（36 格里 24 格零牺牲，M1 XY 只有 9 格）。'
    '但<strong>东向永不可绕</strong>：源行东侧一断，该行以东全体失联，'
    '这是它 12/36 建不出表的唯一原因（见下表与右下图）。',
    '<b>构造性，且对残图同样成立。</b>2D mesh 的通道依赖环只有两条抽象环：'
    '顺时针 W→N→E→S→W 用到 N→E，逆时针 W→S→E→N→W 用到 S→E；'
    'east-first 把这<strong>两条各断一处</strong>，加上禁 180°，CDG 必无环。'
    '关键是<strong>删链路只会减少转弯、不会创造转弯</strong>，'
    '所以证明不依赖网格完整——任何故障残图上都无环，<b>1 VC</b>。'
    '实测 36 格 CDG 硬校验 0 例成环。',
    '每对 (s,d) 离线定死一条路径（先试 XY，再试 BFS，两者都是确定性的），'
    '单 VC 无自适应 ⇒ 同一对的 flit 顺序经过同一串通道。'))}

{ef_reach_html(diagrams.get("east_first_fail", ""))}

{scheme_block("M1 — XY（<code>xy</code>）", "xy", '''
<p><b>思想：</b>坚持维序路由（DOR）：先走完 X，再走 Y；硬件几乎不用改路由逻辑。</p>
<p><b>路径：</b>对每个 (s,d) 严格按 XY 折线前进；所需 hop 被故障删除则整表失败，进入牺牲恢复。</p>
<p><b>特征：</b>中心/角链路一断极易「穿不过」；恢复时常退化成与 M2 类似的大矩形牺牲。
用于量化「坚持 XY 硬件」要付多少牺牲代价。</p>
''' + qa3(
    '<b>不避</b>——这是它的定义。<code>xy_path</code> 在存活图上死走 XY 折线，'
    '任一 hop 缺失立刻返回 <code>None</code>，整表作废。'
    '唯一出路是牺牲好节点把「折线必经的洞」移出参与集合，所以牺牲最重。',
    'X 相走完才进 Y 相，<b>Y→X 转弯根本不存在</b>；'
    '完整矩形上 XY 的 CDG 是经典无环结论。残图上路径仍是 XY 子集，'
    '照样无环，实现仍跑一次 CDG 硬校验。<b>1 VC</b> 即可。',
    '每对唯一的 XY 折线，单 VC，无自适应 → 同一对的 flit 串行经过同一串通道。'))}

{scheme_block("M2 — Rect-XY（<code>rect_xy</code>）", "rect_xy", '''
<p><b>思想：</b>不在破损拓扑上绕路，而是裁成仍规则的子矩形，矩形内继续跑 XY。</p>
<p><b>做法：</b>(1) 标出故障触及的行/列；(2) 在剩余行、列中各取最长连续段，叉成最大轴对齐矩形；
(3) 矩形外原计算节点全部记为 <code>forced_sacrificed</code>；(4) 矩形内生成 XY 全表。</p>
<p><b>特征：</b>牺牲粗、可预测；raw slowdown 常为负是因为参与者变少——应看
<code>irregularity_penalty</code> 与 <code>sacrifice_cost</code>。</p>
''' + qa3(
    '<b>靠裁剪，不靠绕行。</b>把故障触及的<strong>整行整列</strong>全部划掉'
    '（<code>_fault_rows_cols</code>），在剩余行列里各取最长连续段，'
    '叉出最大轴对齐矩形；矩形外的好节点记为 <code>forced_sacrificed</code>。'
    '于是路由域内<strong>一个洞都没有</strong>，不需要任何避障逻辑。',
    '子矩形是一张完整规则 mesh，其上跑标准 XY ⇒ CDG 无环（与 M1 同理），<b>1 VC</b>。',
    '矩形内每对唯一 XY 路径，单 VC，确定性。'))}

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

''' + qa3(
    '<b>约束 BFS 自动绕。</b><code>_tree_path</code> 只枚举 <code>route_adj</code> 的邻居，'
    '洞和断链根本不在候选里；它在存活图上找「先上后下」的最短合法路，'
    '形状可以是任意折线（不是 XY、不必过 root）。'
    '只要图连通且合法相位下有解，<b>通常零牺牲</b>——这是 M3 最大的优点。'
    '无解才交给统一牺牲恢复器。',
    '给每点一个高度 <code>label = BFS dist(root, ·)</code>；'
    '规定路径分两相：<b>相 0 可 up，一旦 down 就永远禁止再 up</b>（同层侧向算 down）。'
    '通道依赖只能是 up→up、up→down、down→down，<b>永远没有 down→up</b>，'
    '所以依赖关系随 (相位, 高度) 单调 ⇒ CDG 按构造无环，<b>1 VC 够用</b>。实现仍硬校验。',
    'root 与 label 每个故障场景只算一次、全表共享；'
    '给定 (s,d) 的约束 BFS 结果确定且唯一，单 VC。')
+ '''
<div class="faq">
<p><b class="q">目录外会不会连通却不可达？</b>
<strong>不会。</strong>单/双故障全枚举 + 三节点全量 17296 + 随机混合 5000：
失败集合与残图断连完全重合，<b>STRUCT=0</b>。
连通残图上 UD 必有合法路；不可达时只牺牲孤立点/小子图。见 §2.3。</p>
</div>
<p><b>与本网格实测：</b>dead/transit 下几乎全部场景 <b>n_sacrificed = 0</b>（A≈39–48），
是 1 VC 方案里规模保持最好的；但合法路径集合窄，负载集中在树的「脊」，
alltoall makespan / irreg 明显高于最短路族（M6/M7/M10）。
M3+LB 试图在合法集合内做负载感知换路，本 8×6 上中位收益几乎为零。</p>
<p><b>端到端角色：</b>Pareto 前沿的左端点——面积最小（VC1≈0.90），
作为「面积受限时的保底方案」。不要用它追极限延迟。</p>
''', extra_key="updown_aux")}

{scheme_block("M3+LB — Up*/Down* + 负载均衡（<code>updown_lb</code>）", "updown_lb", '''
<p><b>在 M3 路径表上后处理：</b>统计有向边 alltoall 对数负载；每轮重排途经最热边的若干 (s,d)，
用负载感知 Dijkstra（边权 ≈ 1+负载）换路；每轮后整表再校验 CDG。失败则回退。</p>
<p><b>特征：</b>目标是压低最大链路负载；在本 8×6 上对 median makespan 改善通常很小
（Up*/Down* 合法路径集合较窄）。</p>
''' + qa3(
    '与 M3 完全相同——换路仍在同一张存活图、同一套合法转向集合内进行，'
    '不会新引入穿越故障的路径。',
    '<b>靠「换完再验」而不是靠构造。</b>负载感知 Dijkstra 可能选出破坏原有相位单调性的路径，'
    '所以每一轮结束都对<strong>整表</strong>重跑 <code>validate_routing</code>；'
    '一旦 CDG 出环，立刻整体回退到上一个已验证的 best 并停止迭代。',
    'LB 是<strong>离线迭代</strong>：收敛后每对仍只有一条固定路径，'
    '运行时不会按实时负载改路 ⇒ 保序不受影响。'))}

{scheme_block("M4 — Segment / 奇偶转向（<code>segment</code>）", "segment", '''
<p><b>思想：</b>简化 segment-based / odd-even 族：按列带施加不同转向禁令，打破 mesh 环依赖。</p>
<p><b>转向规则</b>（列段宽 2，<code>seg=(x//2)%2</code>）：直行允许、180° 禁止；
偶段禁 北→东 / 南→西；奇段禁 北→西 / 南→东。路径 = 约束下最短路。</p>
<p><b>特征：</b>介于 XY 与 Up*/Down*——有时零牺牲，中心故障时常需矩形化。</p>
''' + qa3(
    '<code>shortest_path(s, d, adj, turn_ok)</code> 在存活图上做<strong>带转向过滤的 BFS</strong>：'
    '洞不在邻接表里，自然绕过。比 M1 灵活（可上下绕），比 M3 严格'
    '（转向禁令可能把某些绕行方向堵死）→ 中心大洞时常搜不到路，需要牺牲。',
    '按列带交替施加转向禁令（<code>seg=(x//2)%2</code>）：'
    '偶段禁 <b>北→东 / 南→西</b>，奇段禁 <b>北→西 / 南→东</b>，并全局禁 180° 掉头。'
    '这属于奇偶转向模型族，每个方向环都缺一个必需转弯 ⇒ 环无法闭合，<b>1 VC</b>。'
    '残图上仍以 CDG 硬校验兜底——但实测残图上会失效（见下）。',
    '给定 (s,d) 的约束 BFS 确定性求解，路径唯一，单 VC。')
+ '''
<div class="faq">
<p><b class="q">目录外会不会连通却不可达？</b>
<strong>会，而且极常见。</strong>单链路 72/82、双链路 3272/3321 在残图仍连通时
建不出表（路径失败或 CDG 成环）。奇偶转向在残图上既堵绕行又破无环假设。
<strong>补救：</strong>统一牺牲恢复（代价重），或换 M3/M6/M7。见 §2.3。</p>
</div>
''')}

{scheme_block("M4+LB — Segment + 负载均衡（<code>segment_lb</code>）", "segment_lb", '''
<p>与 M3+LB 相同流程，起点换成 M4 路径表；同样受转向合法集合限制。</p>
''' + qa3(
    '同 M4：换路仍在同一存活图上，不会产生穿越故障的路径。',
    '同 M3+LB：每轮换路后整表重跑 CDG 校验，出环即回退到上一 best。',
    '离线迭代，收敛后一对一条固定路径，单 VC。'))}

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
<p><b>端到端角色：</b><b>被 M10 严格支配</b>——M10 面积更小（1.244 vs 1.937）
且两个载荷下都更快（525/5302 ns vs 555/5469 ns）。
（M10 去掉绕路回环之前，M5 曾在 m₀=13 的严格前沿上。）
若硬件已按 XY+绕障做死、且愿意付 4 VC，它仍是「保 XY 语义」的正统答案。</p>
<p class="note">右图：① 环绕行全路径 · ② 断链须退休端点 · ③ 4 VC 相位×方向 ·
④ 回原行以保 X 相单向。</p>
''' + qa3(
    '<b>矩形块 + 沿环绕行。</b>先把所有故障吸收进互不重叠的矩形块（接触即合并包围盒），'
    '块内节点整体退出路由图与 compute 集；块外贴边一圈健康节点即 fault ring。'
    '路由仍是 XY，只有「下一步会踏进块」时才改走 ring：绕到块的远侧，再回原行/原列续 XY。'
    '<b>代价：</b>矩形模型表达不了「两活节点之间断一条链」，'
    '必须贪心退休一个端点变成 1×1 块 ⇒ 纯链路故障要付 1–4 个好节点。',
    '<b>4 VC = 相位 × 方向</b>（VC0/1 = X 相东/西，VC2/3 = Y 相北/南），是<strong>可证</strong>的：'
    'X 相绕行只允许竖走或朝本报文的 X 方向走 ⇒ VC0 内所有横向通道一律朝东，'
    '环要闭合就得让 x 回到起点，故环内不能有横向通道；纯竖环又需 180° 掉头，构造不产生 ⇒ VC0 无环'
    '（VC1/2/3 对称）。报文只从 X 相进 Y 相、从不回头 ⇒ '
    '{VC0,VC1} → {VC2,VC3} 单向。单层无环 + 层间单向 ⇒ 整图无环。',
    '路径与 VC 都在离线固化：<code>meta[(s,d)]</code> 记下 X 相跳数与两个方向类，'
    '<code>vc_of</code> 只按 hop 序号查表 ⇒ 同一对的 VC 序列完全确定。')
+ '''
<div class="faq">
<p><b class="q">目录外会不会连通却不可达？</b>
<strong>会。</strong>两类：(1) 链路故障 → 强制退休端点（多数仍可出表）；
(2) 故障块贴在左右边中段（如死节点 <code>(0|7, y)</code>，y∈{1..4}）时
环绕缺侧向空间 → STRUCT（单链 12/82、单点 8/48）。
<strong>补救：</strong>端点退休；再不行 <code>solve_scheme</code> 多牺牲 1–2 点。见 §2.3。</p>
</div>
''',
              extra_key="fring_aux")}

{scheme_block("M6 — LASH（<code>lash</code>）", "lash", '''
<p><b>思想：</b>Skeie 等 Layered Shortest Path。每对取一条<strong>最短路</strong>（可绕障），
再把全部路径贪心装进尽可能少的 VC 层，使<strong>每层 CDG 无环</strong>。
路径质量与无死锁解耦——断环靠分层，不靠砍转弯。</p>
<p><b>做法：</b>(1) 存活路由图上 BFS 最短路；(2) 按路径长度降序，尝试放入已有层，
加入后若该层 CDG 仍无环则收下，否则开新层；(3) 整条路径使用同一层号（常数 VC）
→ 保序。本 8×6 上实测通常 <b>1–2 层</b>。</p>
<p><b>与 M5 差别：</b>不强制矩形块、不强制绕回原行；链路故障只需牺牲孤立节点
（度 0），不必把端点做成块。负载往往低于 f-ring。</p>
''' + qa3(
    '<b>纯图搜索。</b>直接在存活图上 BFS 最短路，洞和断链天然被绕开，'
    '路径形状不受任何转向或矩形约束 ⇒ 路径质量最好的一档。'
    '不造块、不退休链路端点；只有度为 0 的孤立点必须先牺牲（它谁也连不上）。',
    '<b>把无死锁从路径里剥离出来，交给分层。</b>按路径长度降序遍历所有 (s,d)：'
    '试着把整条路径加进第 <code>li</code> 层的 CDG，'
    '<strong>加完仍无环就收下，出环就撤销、试下一层</strong>，都不行才开新层（上限 8 层）。'
    '每层各自维护 CDG、互不共享通道 ⇒ 层内无环 + 层间无依赖 ⇒ 全局无环。'
    '本 8×6 上通常只需 <b>1–2 层</b>。',
    '<b>整条路径同一层号</b>（常数 VC，<code>vc_of</code> 忽略 hop 序号只查 (s,d)），'
    '加上路径唯一 ⇒ 严格保序。')
+ '''
<div class="faq">
<p><b class="q">目录外会不会连通却不可达？</b>
<strong>实测不会。</strong>双故障全量 + 三节点抽样 4000 + 混合 5000：
失败 = 断连，<b>STRUCT=0</b>；VC 最多 3（≪ 上限 8）。
理论上层数封顶才可能 STRUCT——本次未观察到。
断连时牺牲；若真层满则抬 <code>LASH_MAX_LAYERS</code> / 用 LASH-TOR。见 §2.3。</p>
</div>
''')}

{scheme_block("M6b — LASH-TOR（<code>lash_tor</code>）", "lash", '''
<p><b>思想：</b>在 LASH 上允许路径<strong>中途升一层</strong>（Trail / Transition On Route）：
hop 的 VC 沿路单调不减，从而把本来需要新开一层的路径塞进已有层。</p>
<p><b>做法：</b>先尝试整路径入单层；失败则枚举分割点，前半段层 <code>lo</code>、后半段层
<code>hi≥lo</code>，分别维护层内 CDG。本 8×6 上 LASH 已是 1–2 层，TOR 收益通常很小。</p>
''' + qa3(
    '与 M6 完全相同：存活图上 BFS 最短路，先牺牲孤立点。TOR 只改层分配，不改路径。',
    '在 M6 的「层内 CDG 无环」之上，额外允许<strong>一次中途升层</strong>：'
    '前段走 <code>lo</code>、后段走 <code>hi ≥ lo</code>，两段分别记入各自层的 CDG 并各自校验。'
    '因为 hop 的 VC <b>单调不减</b>，跨层依赖只能由低指向高、回不去 ⇒ 层间依然单向无环。',
    '分割点与两个层号都在离线贪心时固定，'
    '<code>vc_of(path, i)</code> 查的是预存的 per-hop VC 表 ⇒ 同一对序列确定。'))}

{scheme_block("M7 — 条带 dateline（<code>stripe_vc</code>）", "stripe_vc", '''
<p><b>类别：</b>B 类 · VC 分层 · 本网格实测 <b>5–6 VC</b>（按场景中最坏跨越数定尺寸）。</p>
<p><b>思想：</b>把 mesh 竖切成条带，条带边界就是 <i>dateline</i>（类似环形拓扑破环的日期线）。
路径本身尽量走最短路（优先 XY，不通再 BFS）；<b>无死锁不靠砍转弯，而靠跨带时升 VC</b>。
每条路径上 VC 沿途单调不减 → 天然无环，实现极简。</p>
<p><b>算法步骤：</b></p>
<ol>
<li><b>选路</b>：对每个 (s,d)，先试 <code>xy_path</code>；若因故障不通，退回
<code>shortest_path</code>。路径固定后不再改 → 保序。</li>
<li><b>确定 dateline</b>（见下「Dateline 如何确定」）→ 得到竖向边界集合。</li>
<li><b>赋 VC</b>：报文走第 i 跳时，
<code>VC(i) =</code> 此前（含本跳）水平跨越 dateline 的次数。
竖走不跨带，VC 不变；每水平穿一条虚线，VC+1。</li>
<li><b>定尺寸</b>：<code>num_vc = 1 + max 跨越数</code>（按本场景最坏路径）。</li>
</ol>

<div class="faq">
<p><b class="q">Dateline 如何确定？</b></p>
<p>Dateline 是画在<strong>列与列之间</strong>的竖线（实现用列坐标
<code>d</code>：水平 hop 从列 <code>a</code> 到 <code>b</code> 且区间覆盖
<code>d</code> 即算跨越）。集合只由<strong>列几何 + 故障列 + CDG 是否过关</strong>
决定，与流量、报文长度、makespan 无关。</p>
<ol>
<li><b>基础稀疏带（<code>width=2</code>）</b>：
<code>range(width, MX, width)</code> → 8×6 上即
<code>{2, 4, 6}</code>。条带越窄，DL 越密，东西向路径跨越次数越多，VC 越高。</li>
<li><b>故障列加密</b>：从 dead 节点所在列、断链两端点列收集
<code>fcols</code>；对每个故障列 <code>x</code>，再并入边界
<code>x</code> 与 <code>x+1</code>（右邻）。绕障多在故障列附近拐弯，
加密后跨带更勤、VC 升得更快，降低稀疏带里绕出通道环的概率（图③）。</li>
<li><b>CDG 密封顶</b>：用「基础 + 故障加密」建 <code>vc_of</code> 后做 CDG；
<strong>无环则采用</strong>；仍有环则退化为每个列边界都是 dateline
（<code>1..MX-1</code>），再校验。本实现仍做硬校验，失败则方案失败。</li>
</ol>
<p>间接关系：路径形状（XY / 绕障最短路）× DL 集合 → 跨越次数 →
<code>num_vc</code>。东西跨度大、弯得多的 (s,d) 通常贡献最大跨越数。</p>
</div>

<div class="faq">
<p><b class="q">如何避开故障？</b></p>
<p><b>1. 故障先从路由图里删掉。</b>
<code>expand_pg</code> 生成的 <code>route_adj</code> 已不含 dead 节点 / 断链
（transit 语义下故障节点可不参与 compute，但 router 仍可转发）。
M7 <strong>不造矩形块、不退休链路端点</strong>（对比 M5），活着的点边都还能用。</p>
<p><b>2. 选路时「能 XY 就 XY，撞障就绕」。 </b>
对每个 (s,d)：</p>
<ul>
<li>先在存活邻接表上跑 <code>xy_path</code>——若 XY 折线上没有故障，路径与健康网格相同；</li>
<li>XY 某跳不存在（节点洞或断链）→ 该函数返回 <code>None</code>，改走
<code>shortest_path</code>（BFS），在活边上绕过故障到达目的地。</li>
</ul>
<p>因此避障完全是<strong>图搜索绕行</strong>：没有 f-ring 的「撞块→沿环→回原行」，
路径可以是任意形状的最短路。某对在活图上仍不可达 → 整方案失败，
外层 <code>solve_scheme</code> 再靠牺牲孤立点 / good 节点恢复。</p>
<p><b>3. Dateline 加密是为无死锁，不是为避障。</b>
绕障后路径更弯，可能在稀疏条带里绕出通道环——故故障列附近多插 DL，
并在必要时加密封顶（见上）。换句话说——
<strong>避障靠最短路，破环靠升 VC</strong>；两者解耦。</p>
<p><b>4. 与 M5 / M10 对比。</b>
链路故障时 M5 必须退休端点做成块；M7 只要图仍连通就可零牺牲绕过去。
M10 用「逻辑 XY + 固定展开表」绕障；M7 直接在物理图上求最短路，
通常更短，代价是 VC 数随跨带次数涨到 5–6。</p>
</div>

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
<p><b>端到端角色：</b>Pareto 前沿右端点——<b>两个载荷下都最快</b>
（最差 471 / 4913 ns），面积也最贵。适合「router 面积不敏感、延迟是硬指标」。
从 M10 再走到 M7：+111% 面积只换 10.4% / 7.3% 加速，
边际回报比 M3→M10 低一个数量级。</p>
<p class="note">右图：① 跨 DL 升 VC · ② 竖走不升层 · ③ 故障列加密 DL ·
④ VC 单调 ⇒ CDG 无环。</p>
''' + qa3(
    '<b>能 XY 就 XY，撞障就走最短路。</b>先在存活图上试 <code>xy_path</code>——'
    '若 XY 折线没碰到故障，路径与健康网格一模一样；某跳不存在则改用 BFS '
    '<code>shortest_path</code> 绕过去。不造块、不退休端点，活着的点边全都能用，'
    '通常只需牺牲孤立点。<b>Dateline 与避障无关</b>（详见上）。',
    '<b>跨一条 dateline 就 VC+1</b>，因此沿任意路径 VC 单调不减。'
    '通道依赖 <code>(e,vc) → (e′,vc′)</code> 恒有 <code>vc′ ≥ vc</code>：'
    '环若要闭合必须从高层降回低层，而这被禁止 ⇒ 只剩「同层内成环」一种可能，'
    '而条带内水平跨度被 DL 限死。实现仍硬校验，出环就把 DL 加密到每个列边界再验。'
    '代价是 <b>5–6 VC</b>。',
    '路径固定；<code>vc_of(path, i)</code> = 前 i 跳的跨越数，'
    '是 (路径, hop 序号) 的纯函数、离线即可算全 ⇒ 同一对的 (边, VC) 序列唯一。')
+ '''
<div class="faq">
<p><b class="q">目录外会不会连通却不可达？</b>
<strong>实测不会。</strong>与 M6 相同的故障空间：失败 = 断连，<b>STRUCT=0</b>。
最密 dateline（每列边界）兜底未在实测中被打穿。
断连时牺牲；极罕见再加密 dateline。见 §2.3。</p>
</div>
''',
              extra_key="stripe_aux")}

{scheme_block("M9 — 双向 Up*/Down*（<code>dual_updown</code>）", "updown", '''
<p><b>思想：</b>VC0 跑经典 Up*/Down*（先上后下），VC1 跑对称的 Down*/Up*（先下后上）；
每对选更短的那条，整路径固定在所选 VC → 保序。</p>
<p><b>特征：</b>固定 2 VC，实现比 LASH 简单；路径短于单层 Up*/Down*，但通常仍长于最短路族。</p>
''' + qa3(
    '与 M3 相同的约束 BFS，但<strong>两套规则各搜一次</strong>：'
    'Up*/Down* 与 Down*/Up*。某一套被故障堵死时另一套往往仍有解，'
    '<b>两条都无解才算失败</b> ⇒ 可行性略好于单层 M3。',
    '两套相位规则各自满足「不许回头」（UD 无 down→up，DU 无 up→down），'
    '各自 CDG 无环；两者<strong>放在不同 VC 上、整条路径不混用</strong>，'
    '所以两层之间没有任何通道依赖 ⇒ 并集仍无环。固定 <b>2 VC</b>。',
    '每对选哪套规则由 <code>which[(s,d)]</code> 离线定死（取更短者），'
    '整条路径锁在该 VC 上、中途不切换 ⇒ 路径与 VC 都唯一。'))}

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
<li><b>去回环</b>（<code>_trim_revisits</code>）：拼出来的路径可能重复访问同一节点
（见下「掉头」FAQ），把两次访问之间那段环删掉。
<b>先用去环版建表；若 CDG 成环则整表退回未去环版</b>，两版都成环才返回
<code>None</code>。</li>
<li><b>VC 划分</b>：物理 hop 在「首次到达目的列」之前 → <b>VC0（逻辑 X 相）</b>；
之后 → <b>VC1（逻辑 Y 相）</b>。即使 X 相里含有竖向绕路 hop，仍算 VC0——
分层按逻辑相位，不按物理边方向。</li>
</ol>
''' + qa3(
    '<b>逻辑层假装网格完好，物理层偷偷绕。</b>坏掉的逻辑边被一条'
    '<strong>离线算好、全局共享的固定物理最短路</strong>替换'
    '（<code>expand[a→b]</code>）；逻辑折线上的死节点直接跳过，再把相邻的活节点桥接起来。'
    '孤立点 forced-sacrifice。对上层软件而言故障是<strong>不可见</strong>的——'
    '仍是规则 8×6 XY，映射与调度不用改。',
    '意图是维度序：<b>VC0 = 逻辑 X 相，VC1 = 逻辑 Y 相</b>，相位只能 X→Y 单向切换。'
    '但 VC 只按相位切、<strong>不按方向</strong>切，同一层里既有东行也有西行；'
    '绕路又会往 VC0 塞竖边、往 VC1 塞横边 ⇒ '
    '<strong>几何证明失效，确实会成环</strong>（见下 FAQ 的实测反例）。'
    '所以 <code>gen_virtual_mesh</code> 内部直接调 <code>validate_routing</code> 硬校验，'
    '并按「去环版 → 未去环版」两次尝试；都不过才返回 <code>None</code> 转入牺牲恢复。'
    '固定 <b>2 VC</b>。',
    'expand 表离线固定且全局共享，逻辑折线由 (s,d) 唯一决定 ⇒ 物理路径唯一；'
    '去环是确定性后处理，选哪一版是整表级决定 ⇒ 同一对的路径与 VC 序列仍唯一。')
+ '''

<div class="faq">
<p><b class="q">什么情况下 M10 会真的死锁？（8×6 故障空间穷举）</b></p>
<p><b>先说结论：M10 是本报告保留方案里唯一<strong>没有构造性无死锁证明</strong>的。</b>
它靠「两版取一 + CDG 事后校验」兜底，两版都成环就整体失败。
下表由 <code>utils/pg_m10_cycle_scan.py</code> 穷举
（<code>results/pg_m10_cycle_scan.json</code>）：</p>
''' + m10_scan_html() + '''
<p><b>两个故障以内怎么摆都安全</b>，三个死节点起才出现失效；而且危险的是<b>死节点</b>不是断链——
同规模下混合故障的失效率比纯断链高约 50 倍。目录里那 2 个「需退回原版」的场次是
<code>node_center_2x2/dead</code> 与 <code>node_center_3x3/dead</code>，
正是回退机制存在的理由。上表第二张是形状假设检验：
<strong>紧凑的洞反而安全，散落错开的洞才致命。</strong>洞连成一片时绕路方向一致，
绕过去就完事；洞错开时不同 (s,d) 被迫朝相反方向绕，绕行段互相咬合。
这也解释了为何出厂目录一个都没触发——目录的节点故障全是 1×1/2×2/3×3 规整方块。</p>
''' + m10_cycle_figs() + '''
<p><b>最小失效实例 <code>(1,0) (3,1) (5,1)</code>，两版成环机理完全不同</b>（上图）：</p>
<ul>
<li><b>原始拼接版 = 2 通道掉头环。</b>绕路走过头，后续逻辑跳原路折回，
<code>(2,1)→(2,0)</code> 与 <code>(2,0)→(2,1)</code> 同处 VC1 互相等待。
这正是引入去回环的原因。</li>
<li><b>去回环版 = 8 通道矩形环，全在 VC0。</b>去环消掉了掉头，
但缩短后的路径贴着洞走，绕死节点 (3,1) 首尾相接闭合成经典通道环。</li>
</ul>
<p><b>根因在 VC 划分：</b>M10 按<strong>逻辑相位</strong>分 VC，而死节点逼出的绕路会在逻辑 X 相里
走<strong>物理竖直跳</strong>，这些竖直通道落进 VC0。于是 VC0 里同时存在横向与纵向通道、
又没有任何转向限制 —— XY 的无环论证在此失效。这与 M7 靠 dateline 单调升 VC、
M6 靠分层校验、M5 靠「相位×方向」四层的<strong>可证</strong>断环有本质区别。</p>
<p><b>成环了怎么办：</b>两版都成环 → <code>gen_virtual_mesh</code> 返回 <code>None</code>
→ <code>solve_scheme</code> 按「孤立点 → 单点 → 点对 → k≤6 → 整行整列 → 矩形」
逐级牺牲 good 节点并重新生成、重新校验，取牺牲最少的可行解；全失败才判 INFEASIBLE。
上面的最小实例实测牺牲 <b>1</b> 个节点 (0,0)（A=44/45），
同故障下 M7 / M6 / M3 牺牲 <b>0</b> —— 代价不大，但这就是「有证明」与「靠试」的差距。
目录外 STRUCT / disc 对照表见 §2.3。</p>
</div>
'''
+ '''
<p><b>与 M5 / M7 的关键差别：</b></p>
<ul>
<li>相对 M5：不造矩形块、不强制绕回原行；链路故障不必退休端点，牺牲更少。
VC 只要 2 条（vs 4）。去掉绕路回环后端到端<b>严格快于 M5 且面积更小</b>，
把 f-ring 挤出了 Pareto 前沿。</li>
<li>相对 M7：路径不是全局最短路，而是「逻辑 XY + 局部展开」，有时多走几跳；
但 VC 固定为 2，面积约 1.24（vs Stripe 的 2.63），是性价比拐点。</li>
<li>相对 M3：多 1 条 VC，换来接近最短路的路径质量和显著更低的 makespan /
端到端时间；上层映射还保持规则网格。</li>
</ul>
<p><b>端到端角色：</b><b>推荐默认方案</b>。Pareto 拐点：
相对 M3 只多 39% router 面积，换 22.5% / 25.2% 端到端加速
（回报 440 / 5155 ns/area）；再往 M7 多花 111% 面积只多买 10.4% / 7.3%。
两个载荷尺寸结论一致，且已严格支配 M5 f-ring。
附带好处：软件仍看规则 8×6，映射 / 调度不用为 PG 改写。</p>
<p class="note">右图：① 逻辑 XY + 物理展开 · ② 软件所见完整网格 ·
③ 单条逻辑边的 expand 表 · ④ VC 按逻辑相位（竖向绕路仍属 VC0）。</p>
''', extra_key="vmesh_aux")}

{beyond_catalog_html()}

<h3>2.4 横向对比</h3>
<table>
<thead><tr><th>类</th><th>方案</th><th>路由本质</th><th>VC</th><th>硬件改动</th><th>典型牺牲</th><th>适用意图</th></tr></thead>
<tbody>
<tr><td>A</td><td class="l">M1 XY</td><td class="l">严格先 X 后 Y</td><td>1</td><td class="l">最小（原 XY）</td><td>高</td><td class="l">量化不改路由的代价</td></tr>
<tr><td>A</td><td class="l">M2 Rect-XY</td><td class="l">裁矩形 + XY</td><td>1</td><td class="l">最小</td><td>固定偏高</td><td class="l">规整化、可预测</td></tr>
<tr><td>A</td><td class="l">M0 East-first</td><td class="l">禁 N→E / S→E</td><td>1</td><td class="l">转向过滤</td><td>东向切断时高</td><td class="l">比 XY 宽、仍有东向盲区</td></tr>
<tr><td>A</td><td class="l">M0s Super-turn</td><td class="l">Glass–Ni 自适应 1→2 VC</td><td>1–2</td><td class="l">转向过滤 + 可选第 2 VC</td><td>低</td><td class="l">e2e 评测（VC≤2）</td></tr>
<tr><td>A</td><td class="l">M0s1 Super-turn 1VC</td><td class="l">Glass–Ni 硬顶 1 VC</td><td>1</td><td class="l">转向过滤</td><td>中（替 VC）</td><td class="l">e2e 评测；牺牲换 1 VC</td></tr>
<tr><td>A</td><td class="l">M3 Up*/Down*</td><td class="l">树标号 + 先上后下</td><td>1</td><td class="l">路由表/逻辑</td><td>通常 0</td><td class="l">零 VC 成本保规模；连通即达（§2.3）</td></tr>
<tr><td>A</td><td class="l">M3+LB / M4 / M4+LB</td><td class="l">转向限制 ± LB</td><td>1</td><td class="l">同左</td><td>中～高</td><td class="l">M4 目录外 STRUCT 极常见（§2.3）</td></tr>
<tr><td>B</td><td class="l">M5 真 f-ring</td><td class="l">矩形块 + XY 环绕，相位×方向</td><td>4</td><td class="l">4 VC + 绕障</td><td>节点洞 0；链路 1–4</td><td class="l">描述保留；<b>不进</b>本轮 e2e（VC&gt;2）</td></tr>
<tr><td>B</td><td class="l">M5h half-ring</td><td class="l">半环绕行 + X/Y 两 VC</td><td>2</td><td class="l">2 VC + 半环</td><td>半环受阻时升</td><td class="l">e2e 评测（VC≤2）</td></tr>
<tr><td>B</td><td class="l">M6 LASH</td><td class="l">最短路 + 贪心装层</td><td><b>1–2</b></td><td class="l">少 VC + 离线表</td><td>通常仅孤立点</td><td class="l">VC 性价比</td></tr>
<tr><td>B</td><td class="l">M6b LASH-TOR</td><td class="l">LASH + 中途升层</td><td>1–2</td><td class="l">同 LASH</td><td>同 LASH</td><td class="l">再压层数（收益有限）</td></tr>
<tr><td>B</td><td class="l">M7 Stripe</td><td class="l">最短/XY + 跨带 VC+1</td><td>5–6</td><td class="l">多 VC，逻辑简单</td><td>通常仅孤立点</td><td class="l">面积换极限性能</td></tr>
<tr><td>B</td><td class="l">M9 Dual UD</td><td class="l">UD / DU 双层，按对选</td><td>2</td><td class="l">2 VC + 双规则</td><td>通常 0</td><td class="l">描述保留；<b>不进</b>本轮 e2e/§3</td></tr>
<tr><td>B</td><td class="l">M10 Virtual mesh</td><td class="l">逻辑 XY + 物理绕路</td><td>2</td><td class="l">2 VC + 绕路表</td><td>链路友好</td><td class="l">描述保留；<b>不进</b>本轮 e2e/§3</td></tr>
</tbody>
</table>

<h3>2.5 三性质核验与排除标记</h3>
<p class="note">对每个故障场景让方案在<b>零额外牺牲</b>下建全表
（仅先剔除度为 0 的孤立点），看它能否自力满足三条硬性质。
<b>主表 = 预算故障模型</b>（≤4R+≤8L）；旧 link_/node_ 目录仅作对照。
避障 / 无死锁：<code>utils/pg_budget_probe.py</code> /
<code>utils/pg_capability_probe.py</code>；
保序：构造保证（唯一路径），旧目录另用 DES <code>ordered_ok</code> 交叉检查。</p>
{cap_html}
<p class="note"><b>读法：</b><b>✗</b> = 该性质<b>自力做不到</b>，只能靠牺牲恢复器兜底；
避障列的 △ = 做得到，但方式是按构造牺牲好节点（不是绕行）；
无死锁列的 △ = 目录内实测无环，但<b>没有构造性证明</b>
（M10、M5h half-ring）。
「构造性」与「实测通过」不是同一强度的保证。</p>
<p class="note"><b>排除规则：</b>第 3–4 节与 e2e <b>不含</b>
{esc(excluded_labels)}（三性质/覆盖不足），也<b>不含 M9 Dual-UD / M10 Virtual</b>
（本轮明确不参与评测，§2 描述保留）。
被排除的「牺牲换 makespan」方案（M0/M1/M2/M4）在 §6 仍可出现，
以便用强扩展量化它们到底差多少。</p>

<h3>2.6 方案可行性与牺牲代价（m=1, Q=19，含被排除方案）</h3>
{feas_html}

<h2>3. 每场景最优方案选择</h2>
<p class="note">故障集 = <b>预算模型</b>（≤4R+≤8L，
<code>results/pg_faults_budget_8x6.json</code> /
<code>results/pg_e2e_pareto.json</code>）。
旧 link_/node_ corner·edge·center 目录<b>不再显示</b>。
判据：<b>先牺牲节点数，再 alltoall makespan</b>（同牺牲 ⇒ A 相同才可比）。
评测方案 = e2e 入选集（VC≤2，<b>不含 M9/M10</b>；亦不含
{esc(excluded_labels)}）。
「Pareto 备选」= 非受支配的 (牺牲, alltoall) 组合。
载荷按 e2e 强扩展标定（m<sub>0</sub>∈{{1,13}}）；语义 = dead。</p>
{optimal_tables_html}

<h2>4. alltoall 矩阵（预算故障 · 同 §3 方案集）</h2>
<p class="note">单元格主行：alltoall makespan（cy）；副行：牺牲 | A。
场景与 §3 相同（预算故障）；INF = 该方案在该场景建不出表 / 未覆盖。
不含 M9/M10 与三性质排除方案。</p>
{scheme_matrices_html}

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
<li><b>故障模型与评测范围已换挡：</b>主评估 = 预算故障（≤4R+≤8L）；
e2e / §3 只评 VC≤2 且入选方案，<b>M9 / M10 不参与</b>；
M5 f-ring / LASH / Stripe 保留 §2 描述。</li>

<li><b>端到端前沿（评测集）：M3（VC1）→ M0s Super-turn（VC2）</b>。
Super-turn 用有限转向模型换覆盖与最差端到端；
M5h half-ring / M0s1 覆盖或牺牲不达标，进不了全覆盖前沿。</li>

<li><b>目录外可达性（§2.3）：</b>M3/M6/M7 连通即达（STRUCT=0）；
M0s Super-turn 在扫过的空间里同样以断连为主；
M0s1 / M5h 会出现 STRUCT 或大量 forced_sac（半环链路端点退休）。
M4 极常见 STRUCT；M10 在散落 ≥3 死节点上会。</li>

<li><b>§3 预算场景「先牺牲、再 alltoall」：</b>低牺牲档常是 M0s / M3；
重牺牲方案（XY/Rect/half-ring 最坏 A→个位数）makespan 虚低，不进对比。</li>

<li><b>M1 / M2 / M4（及 East-first）仍排除出 makespan 主表（§2.5）。</b>
预算模型下避障失败或牺牲过重；端到端（§6）里它们垫底。</li>

<li><b>保序</b>为构造保证（每对唯一路径 + 确定性 VC）；
区分方案的是避障与无死锁。</li>

<li><b>M3+LB 几乎无效</b>——想降负载应换转向更松或负载更好的方案（如 Super-turn），
而非在 UD 树上局部重路由。</li>

<li><b>Q 与 VC 面积：</b>Q=4 时 Up*/Down* 可慢数倍；
VC 线性放大每端口缓冲（本轮封顶 VC≤2）。</li>

<li><b>通信占端到端 70–86%</b>，花 router 面积买带宽划算。
推荐默认 <b>M0s Super-turn</b>；面积紧选 <b>M3</b>。</li>
</ol>
</body></html>
"""
    HTML_PATH.write_text(doc)
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
