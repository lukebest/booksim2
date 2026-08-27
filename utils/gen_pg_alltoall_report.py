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
FULL_COVER_JSON = ROOT / "results" / "pg_full_cover.json"
BUDGET_CAP_JSON = ROOT / "results" / "pg_budget_capability.json"
CAP_JSON_PATH = ROOT / "results" / "pg_capability.json"
M10_SCAN_PATH = ROOT / "results" / "pg_m10_cycle_scan.json"
EF_REACH_PATH = ROOT / "results" / "pg_east_first_reach.json"
BEYOND_REACH_PATH = ROOT / "results" / "pg_beyond_catalog_reach.json"
RECOVERY_JSON = ROOT / "results" / "pg_recovery_e2e.json"
RECOVERY_TDD_JSON = ROOT / "results" / "pg_recovery_tdd.json"
E2E_PNG = "pg_e2e_pareto.png"
BUDGET_E2E_PNG = "pg_budget_e2e_pareto.png"
RECOVERY_PNG = "pg_recovery_pareto.png"
SINGLE_JSON = ROOT / "results" / "pg_single_router_e2e.json"
SINGLE_PNG = "pg_single_router_pareto.png"
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
    "updown_best_root": "构造性：同 M3，根选自负载搜索",
    "bb_ud_bal2": "调度：2 张独立 UD 表分批（无多 VC 母方案）",
    "bb_ud_bal3": "调度：3 张独立 UD 表分批（无多 VC 母方案）",
    "bb_ud_policy": "调度：轻→UD×2 / 重→UD×3（无多 VC 母方案）",
    "bb_lash": "M6 LASH 各 VC 层串行→1VC + 同步",
    "bb_dual": "M9 Dual-UD 两 VC 层串行→1VC + 同步",
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
# Virtual / Stripe need many physical VCs — out of scope this round.
E2E_DESC_ONLY = {
    "fault_ring_vc": "VC=4；本轮不考虑多物理 VC 极限方案",
    "lash_tor": "同 LASH，近重复",
    "stripe_vc": "需多物理 VC（≈5–9）；本轮不考虑",
    "virtual_mesh": "需 2 物理 VC 绕路；本轮不考虑",
}

SCHEME_LABELS = {
    "east_first": "M0 East-first",
    "super_turn": "M0s Super-turn",
    "super_turn_1vc": "M0s1 Super-turn 1VC",
    "xy": "M1 XY (+sacrifice)",
    "rect_xy": "M2 Rect-XY",
    "updown": "M3 Up*/Down*",
    "updown_best_root": "M3′ Up*/Down* best-root",
    "updown_lb": "M3 Up*/Down* + LB",
    "bb_ud_bal2": "Batch-Barrier UD×2",
    "bb_ud_bal3": "Batch-Barrier UD×3",
    "bb_ud_policy": "Batch-Barrier UD policy",
    "bb_lash": "Batch-Barrier LASH→1VC",
    "bb_dual": "Batch-Barrier DualUD→1VC",
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
    "updown_best_root": "M3′ best-root",
    "updown_lb": "M3+LB",
    "bb_ud_bal2": "BB UD×2",
    "bb_ud_bal3": "BB UD×3",
    "bb_ud_policy": "BB UD policy",
    "bb_lash": "BB LASH→1VC",
    "bb_dual": "BB DualUD→1VC",
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
    front_bits = []
    pick = ("本轮评测（预算故障 · 有限 VC + 分批屏障；不含 Stripe/Virtual）："
            "见 §6 Pareto；单表保底 <b>M3′</b> / M3。")
    e2e = _e2e_data_filtered()
    if e2e is not None:
        syn = (e2e["meta"].get("batch_barrier", {}) or {}).get("sync_model", "")
        for m0 in e2e["meta"].get("m0_list", [1, 13]):
            raw = [s for s in e2e["summary"]
                   if s["m0"] == m0 and s.get("pareto_worst")]
            names = " → ".join(
                E2E_SHORT.get(s["scheme"], s["scheme"])
                for s in sorted(raw, key=lambda s: s["area"]))
            if names:
                front_bits.append(f"m₀={m0}：{names}")
        if front_bits:
            pick = ("本轮评测（预算故障 · ≤3 VC + 分批屏障 1VC；"
                    "<b>不含</b>多物理 VC 的 Stripe / Virtual）："
                    "Pareto 前沿 "
                    + "；".join(front_bits)
                    + "。分批屏障 = 无死锁子集串行 + 图中心 barrier。")
        sync_note = (
            "<li><b>批间同步：</b>"
            "<code>T_sync=2·radius_wire</code>（图中心 gather+broadcast；"
            "Dijkstra + <code>link_lat</code> H=7/V=9，与 DES 一致；"
            "本目录中位约 110 cy，旧 hop 模型约 14 cy）；"
            "每批 DES 已含排空，同步只计 PE 集合 barrier。</li>"
            if syn else "")
    else:
        sync_note = ""
    fronts = ("；".join(front_bits)
              if front_bits else "见 §6（数据未生成时先跑 batch-barrier DSE）")
    return f"""
<div class="exec">
<h2>仿真结论（先看这里）</h2>
<p class="pick">{pick}</p>
<ol>
<li><b>端到端 Pareto：</b>{esc(fronts)}。</li>
{sync_note}
<li><b>裸 makespan 会骗人：</b>M0/M1/M2/M4 常常「最快」，是因为牺牲把 A 裁小、流量按 A² 下降。
端到端强扩展后它们垫底——已从 §3/§4 排除（{esc(excluded_labels)}）。</li>
<li><b>硬性质 / 目录外：</b>预算模型三性质见 §2.5；
目录外 STRUCT/disc 见 §2.3（已含 M0s / M0s1 / M5h）。
保序为构造保证（唯一路径；分批方案在批内唯一路径 + 批间屏障）。</li>
<li><b>§3 每场景最优：</b>预算故障场景上，低牺牲时常落到 M0s / M3 族 / 多 VC；
通信占端到端约 70–86%，花 router 面积买带宽仍划算。</li>
<li><b>另一条路——死锁恢复（§7）：</b>把转向限制拿掉、用 Static Bubble / SPIN /
SWAP 在运行时解环，可做到 <b>零牺牲、1 VC、面积 +0.6%–15%</b>。
关键在<b>配哪种路由</b>：<b>M3′ 天然无死锁，叠恢复机制净收益为零</b>
（R2 = M3′ + 兜底规则，兜底一次没用上、检测器一次没响，时间与 M3′ 完全相同）；
<b>负载最优但满是环的路由（R1）反而最慢</b>，<b>把 Super-turn 压到 1 VC（R3）更差</b>
——它的无死锁性全在 VC 分层里，拿掉 VC 不是「小概率死锁」而是全丢。
恢复开销由 CDG 环数决定，不由峰值负载决定。
机制排序恒为 SB ≪ SPIN &lt; SWAP，SWAP 破坏保序。</li>
</ol>
<p class="sub">细节与数据见 §2（方案/可达性）、§3–4（预算故障 makespan）、
§6（端到端 Pareto，死锁<b>避免</b>类）、§7（死锁<b>恢复</b>类，独立 Pareto）、
§10（均匀注入率 λ=0.10…0.70：健康 XY 与 ≤2R+≤4L Super-turn 的时延 / 有效带宽 / 开销）。</p>
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
        if not s["struct_possible"]:
            flag = "<td class='cap-ok'>否</td>"
        elif sid == "super_turn":
            # ≤2-fault STRUCT=0; only rare mixed STRUCT → 偶发, not 会
            flag = "<td class='cap-warn'>偶发</td>"
        else:
            flag = "<td class='cap-bad'>会</td>"
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
<p class="note"><b>一句话：</b>M3 / M6 / M7 在连通残图上<b>从不</b>结构性不可达。
M0s Super-turn：≤2 故障 STRUCT=0，混合抽样偶发（8/1000）；
M0s1 / M5h 会大量 STRUCT 或 forced_sac。
M4 极常见、M5 全环在左右边中段会、M10 在散落 ≥3 死节点上会。
M0 East-first 东向盲区见 §2.4。</p>
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
                "<th>A 中位/最差</th><th>牺牲中位/最差</th>"
                "<th title='需要放宽牺牲预算（solve_scheme_fc 升级）才可行的场景数'>"
                "FC</th>"
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
            n_fc = s.get("n_fc", 0)
            fc_cell = (f"<b>{n_fc}</b>" if n_fc else "0")
            body.append(
                "<tr>"
                f"<td class='l'>{esc(E2E_SHORT.get(s['scheme'], s['scheme']))}</td>"
                f"<td>{s['num_vc']}</td>"
                f"<td>{s['area']:.3f}</td>"
                f"<td>{cover}</td>"
                f"<td>{s['A_med']}/{s['A_worst']}</td>"
                f"<td>{s['sac_med']}/{s.get('sac_worst', '?')}</td>"
                f"<td>{fc_cell}</td>"
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
评估范围：M3 族 / Super-turn / Dual-UD / LASH（≤3 VC）与
<b>分批屏障</b> 1VC（<code>bb_*</code>）。
<strong>不含</strong>需多物理 VC 的 Virtual mesh / Stripe / f-ring（§2 描述保留）。
批间同步：图中心 gather+broadcast，
<code>T_sync=2·radius_wire</code>（Dijkstra + <code>link_lat</code>，H=7/V=9，与 DES 一致）。
数据 <code>results/pg_e2e_pareto.json</code> /
<code>results/pg_batch_barrier_e2e.json</code>。
共 {sum(1 for _ in data['rows'])} 行（含描述外方案原始行时以表为准）。</p>

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
<li><b>排名翻转仍成立：</b>M1/M2/M4 裸 makespan 好看，端到端被低牺牲方案支配。</li>
<li><b>通信占端到端 70–86%</b>（除重牺牲方案）。花 router 面积买带宽划算。</li>
<li><b>分批屏障 vs 有限 VC：</b>
批间同步为图中心 gather+broadcast，
<code>T_sync=2·radius_wire</code>（按 <code>link_lat</code> H=7/V=9，与 DES 同口径；
健康 8×6 上约百拍量级，远高于旧 hop 模型的 ≈14 cy）。
<code>bb_ud_*</code> 相对单表 M3′ 的优势以同步税计入后的 §6 数字为准。
把 Dual/LASH 按 OD 整层切开串成 1VC（<code>bb_dual</code>/<code>bb_lash</code>）
相对母方案更慢是预期；同面积下与 BB UD 比较见 Pareto。
Stripe/Virtual 中途升 VC、不能按 OD 切开，本轮不做 BB、不进选型。</li>
<li><b>推荐：</b>轻载荷（m₀=1）1VC 保底 <b>M3′</b>，压延迟可上
<b>Super-turn（≤2 VC）</b>——计入线延迟同步税后 BB UD 不再支配单表；
重载荷（m₀=13）推 <b>BB UD policy（×3）</b>。
<strong>不要</strong>选 M0s1 / M5h 换「全覆盖」。</li>
</ol>
<p class="note"><b>已知局限：</b>只算 dispatch 一次 alltoall；
面积不计牺牲的 PE tile；control 面积按常数、未随 VC 增长；
全量 176 场景扫完后数字以 <code>pg_e2e_pareto.json</code> 为准。</p>
{full_cover_html()}
"""


def full_cover_html() -> str:
    """§6.4: does allowing more sacrifice buy full scenario coverage?"""
    if not FULL_COVER_JSON.exists():
        return ""
    d = json.loads(FULL_COVER_JSON.read_text())
    schemes = d["schemes"]
    order = [s for s in ["updown", "super_turn", "super_turn_1vc",
                         "fault_half_ring"] if s in schemes]
    if not order:
        return ""
    # Merge e2e m₀=1 sac / worst T_e2e (full-cover run).
    e2e_by: dict[str, dict] = {}
    e2e = _e2e_data_filtered()
    if e2e is not None:
        for s in e2e["summary"]:
            if s["m0"] == 1 and s["scheme"] in order:
                e2e_by[s["scheme"]] = s
    rows = []
    for sid in order:
        s = schemes[sid]
        n = s["n_scen"]
        esc_n = s["greedy_grow"] + s["coarse"]
        verdict = ("<td class='cap-ok'>不需要升级</td>" if esc_n == 0
                   and s["infeasible"] == 0
                   else ("<td class='cap-warn'>升级后全覆盖</td>"
                         if s["infeasible"] == 0
                         else "<td class='cap-bad'>仍不全覆盖</td>"))
        e = e2e_by.get(sid)
        if e is not None:
            sac = f"{e['sac_med']} / {e.get('sac_worst', '?')}"
            te2e = f"<b>{e['t_e2e_ns_worst']:.0f}</b>"
        else:
            sac, te2e = "—", "—"
        rows.append(
            f"<tr><td class='l'>{esc(E2E_SHORT.get(sid, sid))}</td>"
            f"<td>{s['solve_scheme']}/{n}</td>"
            f"<td>{s['greedy_grow']}</td><td>{s['coarse']}</td>"
            f"<td>{s['infeasible']}</td>{verdict}"
            f"<td>{sac}</td><td>{te2e}</td></tr>")

    def _e(sid: str, key: str, default: str = "?"):
        e = e2e_by.get(sid)
        if e is None:
            return default
        v = e.get(key, default)
        return f"{v:.0f}" if isinstance(v, float) else str(v)

    return f"""
<h3>6.4 放宽牺牲预算能否换来全覆盖？</h3>
<p class="note"><code>solve_scheme</code> 只在小候选池里找<b>最小基数</b>恢复，
所以对约束更紧的方案会直接判 INFEASIBLE。本小节问的是另一个问题：
<b>如果允许牺牲更多好节点，合法表究竟存不存在</b>。
升级阶梯（<code>solve_scheme_fc</code>）：
① 原 <code>solve_scheme</code> → ② 沿「离故障最近」顺序逐点贪心增长 →
③ 最大健康矩形 → ④ 单行 / 单列（线形拓扑对任何转向模型都合法）。
牺牲 / T<sub>e2e</sub> 取自全覆盖 e2e（m₀=1，quick 44）。
数据 <code>results/pg_full_cover.json</code> +
<code>results/pg_e2e_pareto.json</code>。</p>
<table class="cap">
<thead><tr><th class='l'>方案</th><th>原 solve_scheme</th>
<th>贪心增长</th><th>矩形/整行整列</th><th>仍不可行</th>
<th>结论</th>
<th>牺牲中位/最差</th>
<th>最差 T<sub>e2e</sub> (ns)</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
<p class="note"><b>结论（quick 44 场景，m₀=1）：</b>四个方案<b>都能全覆盖</b>。
M3：牺牲中位/最差 <b>{_e('updown','sac_med')} / {_e('updown','sac_worst')}</b>，
最差 T<sub>e2e</sub> <b>{_e('updown','t_e2e_ns_worst')}</b> ns。
M0s：牺牲 <b>{_e('super_turn','sac_med')} / {_e('super_turn','sac_worst')}</b>，
最差 T<sub>e2e</sub> <b>{_e('super_turn','t_e2e_ns_worst')}</b> ns（无需升级）。
M0s1：forced≤40 后 <code>solve_scheme</code> 即 44/44；
牺牲 <b>{_e('super_turn_1vc','sac_med')} / {_e('super_turn_1vc','sac_worst')}</b>，
最差 T<sub>e2e</sub> <b>{_e('super_turn_1vc','t_e2e_ns_worst')}</b> ns。
M5h：40/44 原求解可行，其余 4 个靠整行/整列；
牺牲 <b>{_e('fault_half_ring','sac_med')} / {_e('fault_half_ring','sac_worst')}</b>，
最差 T<sub>e2e</sub> <b>{_e('fault_half_ring','t_e2e_ns_worst')}</b> ns。
<strong>强扩展下重牺牲反噬端到端</strong>——M0s1 / M5h 最差 T<sub>e2e</sub>
明显高于 M3 / M0s。「能全覆盖」≠「值得选」。</p>
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
<b>M0s Super-turn</b> 算法、避障、无死锁与牺牲分布见 <b>§2.1 M0s</b>。
本轮 e2e 默认 <code>full_cover</code>：M0s1 / M5h 在 quick 44 上已 44/44
（见 §6.4）；旧口径「零额外牺牲 / 紧预算」下 M0s1、M5h 曾覆盖不全。</p>
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


REC_LABEL = {
    "none": "不装恢复机制（对照）",
    "sb": "Static Bubble",
    "spin": "SPIN",
    "swap": "SWAP",
}
RT_LABEL = {
    "xy_detour": "R0 基线 XY + 最小绕障",
    "minmax": "R1 无转向模型 · min-max 负载均衡",
    "updown_relax": "R2 M3′ best-root + 非法转向兜底",
    "super_turn_1vc": "R3 Super-turn 转向集合压到 1 VC",
}
RT_SHORT = {"xy_detour": "R0 XY+绕障", "minmax": "R1 min-max",
            "updown_relax": "R2 M3′+兜底",
            "super_turn_1vc": "R3 Super-turn/1VC"}
RT_NOTE = {
    "xy_detour": "L 型完好就走 XY，被挡住才绕最小弯",
    "minmax": "只要最短+均衡，完全不管转向 —— 纯性能最优的避障路由",
    "updown_relax": "Up*/Down* 树路径为主；树到不了的对才走非法路径"
                    "（实测从未发生，见下）",
    "super_turn_1vc": "M0s 的两层 Glass–Ni 转向模型合并到 1 个物理 VC，"
                      "剩下的环交给恢复机制",
}
REC_CITE = {
    "sb": "Ramrakhyani &amp; Krishna, <i>Static Bubble: A Framework for "
          "Deadlock-Free Irregular On-chip Topologies</i>, HPCA 2017, "
          "pp. 253–264",
    "spin": "Ramrakhyani, Gratz &amp; Krishna, <i>Synchronized Progress in "
            "Interconnection Networks (SPIN): A New Theory for Deadlock "
            "Freedom</i>, ISCA 2018, pp. 699–711",
    "swap": "Parasar, Enright Jerger, Gratz, San Miguel &amp; Krishna, "
            "<i>SWAP: Synchronized Weaving of Adjacent Packets for Network "
            "Deadlock Resolution</i>, MICRO 2019, pp. 873–885",
}


def _mode_hist(rows: list[dict], top: int = 2) -> list[str]:
    """Most frequently chosen turn-model pair, as 'name xN' strings."""
    hist: dict[str, int] = {}
    for r in rows:
        m = r.get("turn_mode")
        if m:
            hist[m] = hist.get(m, 0) + 1
    return ["%s ×%d" % (k, v) for k, v in
            sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]


def single_router_section_html() -> str:
    """§6.5 Pareto restricted to at most one dead router (corner/edge/center)."""
    if not SINGLE_JSON.exists():
        return ("<h2>6.5 最多 1 个 router 坏（角 / 边 / 中心）</h2>"
                "<p class='note'>尚无 <code>results/pg_single_router_e2e.json</code>。"
                "请跑 <code>utils/dse_pg_single_router_pareto.py --jobs 6</code> 与 "
                "<code>utils/gen_pg_single_router_pareto_plot.py</code>。</p>")
    data = json.loads(SINGLE_JSON.read_text())
    meta = data["meta"]
    n_scen = meta["n_scenarios"]
    m0s = meta["m0_list"]
    tokens = meta.get("total_tokens", {})
    avoid = list(data["summary_avoid"])
    rec = data.get("summary_recovery", [])
    scen = meta.get("scenarios", [])
    loc = defaultdict(list)
    for s in scen:
        loc[s.get("region", "?")].append(s["name"])

    def tbl(m0: int) -> str:
        rows = []
        cand = sorted((s for s in avoid if s["m0"] == m0),
                      key=lambda s: (s.get("partial", False),
                                     s["t_e2e_ns_worst"]))
        ft = [s for s in cand if s.get("sac_worst", 0) <= 1
              and not s.get("partial")]
        front = {s["scheme"] for s in _e2e_pareto_front(
            ft, "area", "t_e2e_ns_worst")}
        for s in cand:
            mark = "<b>yes</b>" if s["scheme"] in front else ""
            rows.append(
                "<tr><td class='l'>%s</td><td>%s</td><td>%.3f</td>"
                "<td>%d/%d</td><td>%s/%s</td><td>%.0f</td>"
                "<td><b>%.0f</b></td><td>%s</td></tr>"
                % (esc(E2E_SHORT.get(s["scheme"], s["scheme"])),
                   s["num_vc"], s["area"], s["A_med"], s["A_worst"],
                   s["sac_med"], s.get("sac_worst", "?"),
                   s["t_e2e_ns_med"], s["t_e2e_ns_worst"], mark))
        rec_rows = [s for s in rec if s["m0"] == m0 and s.get("n_ok")]
        rec_rows.sort(key=lambda s: s.get("t_e2e_ns_worst", 1e18))
        rt_lab = {"xy_detour": "R0 XY+绕障", "minmax": "R1 min-max",
                  "updown_relax": "R2 M3′+兜底",
                  "super_turn_1vc": "R3 Super-turn/1VC"}
        kn_lab = {"none": "无恢复", "sb": "Static Bubble",
                  "spin": "SPIN", "swap": "SWAP"}
        for s in rec_rows:
            name = "%s + %s" % (rt_lab.get(s["routing"], s["routing"]),
                                kn_lab.get(s["kind"], s["kind"]))
            rows.append(
                "<tr><td class='l'>%s <span class='sub'>恢复类</span></td>"
                "<td>1</td><td>%.3f</td><td>%s/%s</td><td>%s/%s</td>"
                "<td>%.0f</td><td><b>%.0f</b></td><td></td></tr>"
                % (esc(name), s.get("area", 0),
                   s.get("A_med", "?"), s.get("A_worst", "?"),
                   s.get("sac_med", "?"), s.get("sac_worst", "?"),
                   s.get("t_e2e_ns_med", 0), s.get("t_e2e_ns_worst", 0)))
        head = ("<tr><th class='l'>方案</th><th>VC</th><th>area</th>"
                "<th>A 中位/最差</th><th>牺牲中位/最差</th>"
                "<th>T<sub>e2e</sub> 中位 (ns)</th>"
                "<th>T<sub>e2e</sub> 最差 (ns)</th><th>Pareto</th></tr>")
        return ("<table><thead>%s</thead><tbody>%s</tbody></table>"
                % (head, "".join(rows)))

    png = ""
    if (ROOT / "results" / SINGLE_PNG).exists():
        png = (
            f'<figure class="e2e-fig"><img src="{SINGLE_PNG}" '
            'alt="single-router Pareto" '
            'style="max-width:100%;height:auto;background:#fff;'
            'border:1px solid #e0e0e0"/>'
            f"<figcaption>最多 1 个 router 坏（{n_scen} 个位置分层场景），"
            "含 M4–M10。"
            "避免类（菱形）与恢复类（圆/三角/方/倒三角）同轴。"
            "实心=最差，空心=中位。</figcaption></figure>")
    loc_note = "、".join(
        "%s %d 个" % ({"healthy": "健康", "corner": "角",
                       "edge": "边中点", "center": "中心"}.get(k, k),
                      len(v))
        for k, v in (("healthy", loc.get("healthy", [])),
                     ("corner", loc.get("corner", [])),
                     ("edge", loc.get("edge", [])),
                     ("center", loc.get("center", []))))
    def _ft(s):
        return s.get("sac_worst", 0) <= 1

    bullets = []
    for m0 in m0s:
        av = [s for s in avoid if s["m0"] == m0 and _ft(s)]
        if not av:
            continue
        b = min(av, key=lambda s: (s["area"], s["t_e2e_ns_worst"]))
        fast = min(av, key=lambda s: s["t_e2e_ns_worst"])
        bullets.append(
            "<li>m₀=%d、只计「残图上零额外牺牲」的方案：1VC 最快是 <b>%s</b>"
            "（最差 %s ns，area %.3f）；绝对最快是 <b>%s</b>"
            "（最差 %s ns，VC %s）。</li>"
            % (m0, esc(E2E_SHORT.get(b["scheme"], b["scheme"])),
               f"{b['t_e2e_ns_worst']:.0f}", b["area"],
               esc(E2E_SHORT.get(fast["scheme"], fast["scheme"])),
               f"{fast['t_e2e_ns_worst']:.0f}", fast["num_vc"]))
    xy13 = next((s for s in avoid if s["m0"] == 13 and s["scheme"] == "xy"),
                None)
    m3_13 = next((s for s in avoid
                  if s["m0"] == 13 and s["scheme"] == "updown_best_root"), None)
    st13 = next((s for s in avoid
                 if s["m0"] == 13 and s["scheme"] == "super_turn"), None)
    bb13 = next((s for s in avoid
                 if s["m0"] == 13 and s["scheme"] == "bb_ud_policy"), None)
    r2 = next((s for s in rec if s["m0"] == 13 and s.get("routing") ==
               "updown_relax" and s.get("kind") == "none"), None)
    r0sb = next((s for s in rec if s["m0"] == 13 and s.get("routing") ==
                 "xy_detour" and s.get("kind") == "sb"), None)
    return f"""
<h2>6.5 最多 1 个 router 坏（角 / 边 / 中心）</h2>
<p class="note">§6 的 44 场景把故障预算拉到 ≤4 router + ≤8 链路，最差时间被
<b>多点同时坏</b>主导。本节把预算收成「<b>最多一个 router 坏、不断额外链路</b>」，
位置显式覆盖角、边、中心（外加健康 mesh），看各容错方案在更接近单点失效
的口径下 Pareto 是否翻转。</p>
<p class="note">目录 {n_scen} 个场景：{esc(loc_note)}。
图中红色前沿<b>只在「额外牺牲 ≤ 1」的方案里</b>计算——
M0/M1/M2/M4 在中心孔上会砍掉 35 个好节点（A=12），
最差时间被强扩展的 1/A² 流量减免「看快」，不进容错前沿。
数据 <code>results/pg_single_router_e2e.json</code>
（{meta.get('elapsed_s')}s）。</p>
{png}
<h4>m₀ = 1 flit（{int(float(tokens.get('1', 0) or 0))} tokens）</h4>
{tbl(1)}
<h4>m₀ = 13 flit（{int(float(tokens.get('13', 0) or 0))} tokens）</h4>
{tbl(13)}
<ul class="note">
{''.join(bullets)}
<li><b>难的是中心孔，不是角。</b>角/边死一个 router，XY 只再弃 5–7 个节点；
两个中心点（(3,2)/(4,3)）上 M1 XY 弃 35 个（A=12，m₀=13 最差
{xy13['t_e2e_ns_worst']:.0f} ns），而 M3′ / Super-turn / BB 全部保住
A=47、额外牺牲 0。中心孔把固定转向模型的「矩形裁剪」代价完全暴露出来。</li>
<li><b>1VC 避免类：BB UD policy / ×3 最差
{bb13['t_e2e_ns_worst']:.0f} ns，M3′ {m3_13['t_e2e_ns_worst']:.0f} ns</b>
（{bb13['t_e2e_ns_worst']/m3_13['t_e2e_ns_worst']:.2f}×）。
花 2VC 上 Super-turn 换到 {st13['t_e2e_ns_worst']:.0f} ns
（相对 M3′ −{(1-st13['t_e2e_ns_worst']/m3_13['t_e2e_ns_worst'])*100:.0f}%），
面积 1.244 = 1.39×。</li>
<li><b>恢复类在单点失效下仍然帮不上忙。</b>
R2（M3′ 核心）{r2['n_ok'] if r2 else '?'}/{n_scen} 完成、检测器不响，
最差 {r2['t_e2e_ns_worst']:.0f} ns，与避免类 M3′ 逐拍相同。
中心孔上 XY+绕障 / min-max / Super-turn-1VC 的 <code>none</code> 会死锁；
叠 Static Bubble 之后 XY+绕障最差 {r0sb['t_e2e_ns_worst']:.0f} ns
= M3′ 的 {r0sb['t_e2e_ns_worst']/m3_13['t_e2e_ns_worst']:.1f}×。
角和边上不装恢复也能跑完，中心孔才是恢复机制真正被调用的地方——
而那里 M3′ 根本不需要它。</li>
<li><b>M4–M10 补进同图之后：</b>
M4 Segment 在中心孔上与 XY 一样砍到 A=12，最差最慢一档（m₀=13 9396 ns），
不进容错前沿。M5h half-ring（2VC）中心/边仍会牺牲（A 最差 20）。
<b>M5 f-ring（4VC）/ M7 Stripe（5VC）/ M10 Virtual（2VC）</b>
零额外牺牲（M5 最差 A=45，多弃 2 个），m₀=13 最差
3244 / 3275 / 3461 ns，比 1VC 的 BB/M3′ 快，但面积分别是
1.94 / 2.28 / 1.24。M6 LASH / M6b / M9 Dual-UD 都是 2VC、A=47，
最差 5459 / 5459 / 5121 ns，被同面积的 M10 / Super-turn 支配。
1VC 保底不变（BB UD / M3′）；多 VC 换来的是 M10 与 M5/M7 这条右边的前沿。</li>
<li>与 §6 全预算对照：把 M5–M10 放回来之后，单点失效下
<b>多 VC 方案第一次真正进前沿</b>（全预算里它们被 4R+8L 的覆盖/面积挡在外面）。
1VC 保底仍是 M3′ / BB UD，恢复类仍不是更快的替代。</li>
</ul>
"""


def recovery_section_html() -> str:
    """§7 deadlock recovery on baseline XY (separate Pareto from avoidance)."""
    if not RECOVERY_JSON.exists():
        return ("<h2>7. 死锁恢复类方案（Static Bubble / SPIN / SWAP）</h2>"
                "<p class='note'>尚无 <code>results/pg_recovery_e2e.json</code>。"
                "请跑 <code>utils/dse_pg_recovery_pareto.py --jobs 6</code> 与 "
                "<code>utils/gen_pg_recovery_pareto_plot.py</code>。</p>")
    doc = json.loads(RECOVERY_JSON.read_text())
    meta, rows, summary = doc["meta"], doc["rows"], doc["summary"]
    mech = meta["mech"]
    n_scen = meta["n_scenarios"]
    m0s = meta["m0_list"]
    routings = meta["routings"]
    best_rt = routings[-1]
    avoid = {(s["scheme"], s["m0"]): s
             for s in doc.get("avoidance_reference", [])}

    def srow(kind: str, m0: int, routing: str = best_rt) -> dict:
        return next(s for s in summary if s["kind"] == kind
                    and s["m0"] == m0 and s["routing"] == routing)

    # Recovery-event statistics only exist where recovery actually fires, so
    # anything about rings / laps / detections defaults to R0.
    def med(kind: str, m0: int, key: str, routing: str = "xy_detour"):
        v = sorted(r[key] for r in rows
                   if r["kind"] == kind and r["m0"] == m0
                   and r["routing"] == routing and r.get(key))
        return v[len(v) // 2] if v else None

    def best_of(m0: int, routing: str) -> dict:
        """Fastest mechanism (worst-case) for one routing."""
        cand = [s for s in summary if s["m0"] == m0 and s["routing"] == routing
                and s["kind"] != "none" and s["n_ok"]]
        return min(cand, key=lambda s: s["t_e2e_ns_worst"])

    # ---- 7.1 routing choice --------------------------------------------
    static = {}
    for rt in routings:
        rr = [r for r in rows if r["routing"] == rt and r["kind"] == "sb"
              and r["m0"] == m0s[0]]
        ok = [r for r in rr if r.get("feasible")]
        ld = sorted(r["max_load"] for r in ok)
        rat = sorted(r["max_load"] / max(r.get("load_lb") or 1, 1)
                     for r in ok)
        hp = sorted(r["hops"] for r in ok)
        cyf = sorted((r.get("cdg_cycle_channels") or 0)
                     / max(r.get("cdg_channels") or 1, 1) for r in ok)
        cyc_ch = sorted(r.get("cdg_cycle_channels") or 0 for r in ok)
        static[rt] = {
            "n": len(rr), "ok": len(ok),
            "cyc_ch_med": cyc_ch[len(cyc_ch) // 2] if cyc_ch else 0,
            "cyc_frac_med": cyf[len(cyf) // 2] if cyf else 0,
            "cyc_frac_worst": cyf[-1] if cyf else 0,
            "mode": _mode_hist(ok),
            "sac0": sum(1 for r in ok if not r["n_sacrificed"]),
            "sac_tot": sum(r["n_sacrificed"] for r in ok),
            "sac_worst": max((r["n_sacrificed"] for r in ok), default=0),
            "cyc": sum(1 for r in ok if not r["cdg_acyclic"]),
            "load_med": ld[len(ld) // 2] if ld else 0,
            "load_worst": ld[-1] if ld else 0,
            "rat_med": rat[len(rat) // 2] if rat else 0,
            "rat_worst": rat[-1] if rat else 0,
            "hop_med": hp[len(hp) // 2] if hp else 0,
            "free_worst": max((r["n_free_pairs"] for r in ok), default=0),
            "detour_med": sorted(r["n_detour_pairs"] for r in ok)[len(ok) // 2]
            if ok else 0,
        }
    base = [r for r in rows if r["routing"] == best_rt and r["kind"] == "sb"
            and r["m0"] == m0s[0]]
    n_sac0 = static[best_rt]["sac0"]
    n_cyc = static[best_rt]["cyc"]
    sac_bad = [r["scenario"] for r in base if r.get("n_sacrificed")]

    rt_rows = []
    for rt in routings:
        st = static[rt]
        nb = ["%s %d/%d" % (("m₀=%d" % m0),
                            srow("none", m0, rt).get("n_ok", 0), n_scen)
              for m0 in m0s]
        rt_rows.append(
            "<tr><td class='l'><b>%s</b><div class='sub'>%s</div></td>"
            "<td>%d / %d</td><td>%.2f / %.2f</td><td>%d</td>"
            "<td class='%s'>%d/%d</td><td class='%s'>%.0f%% / %.0f%%</td>"
            "<td>%d</td><td class='l'>%s</td></tr>"
            % (esc(RT_LABEL[rt]), esc(RT_NOTE[rt]),
               st["load_med"], st["load_worst"], st["rat_med"],
               st["rat_worst"], st["hop_med"],
               "cap-ok" if st["cyc"] == 0 else "cap-bad",
               st["cyc"], st["ok"],
               "cap-ok" if st["cyc_frac_med"] == 0 else "cap-warn",
               100 * st["cyc_frac_med"], 100 * st["cyc_frac_worst"],
               st["sac_worst"], "，".join(nb)))
    m3_av0 = avoid.get(("updown_best_root", m0s[0]))
    rt_rows.append(
        "<tr class='ref'><td class='l'>（对照）M3′ best-root 本体"
        "<div class='sub'>转向限制完整保留，<b>天然无死锁，不需要恢复机制</b>"
        "</div></td>"
        "<td>%d / %d</td><td>%.2f / %.2f</td><td>%d</td>"
        "<td>0/%d</td><td>0%% / 0%%</td><td>%d</td>"
        "<td class='l'>全部场景无死锁</td></tr>"
        % (static["updown_relax"]["load_med"],
           static["updown_relax"]["load_worst"],
           static["updown_relax"]["rat_med"],
           static["updown_relax"]["rat_worst"],
           static["updown_relax"]["hop_med"], n_scen,
           m3_av0["sac_worst"] if m3_av0 else 0))
    rt_tbl = ("<table class='cap'><thead><tr><th class='l'>路由</th>"
              "<th>峰值链路负载 中位/最差</th><th>负载/理论下界 中位/最差</th>"
              "<th>总跳数 中位</th><th>CDG 成环场景</th>"
              "<th>环上通道占比 中位/最差</th><th>牺牲最差</th>"
              "<th class='l'>不装恢复机制时完成的场景</th>"
              "</tr></thead><tbody>%s</tbody></table>" % "".join(rt_rows))

    none_bits = []
    for m0 in m0s:
        s = srow("none", m0, "xy_detour")
        me = sorted({r["m_eff"] for r in rows
                     if r["m0"] == m0 and "m_eff" in r})
        none_bits.append(
            "m₀=%d（m<sub>eff</sub>≈%s）：%d/%d 场景死锁"
            % (m0, me[len(me) // 2] if me else "?",
               n_scen - s.get("n_ok", 0), n_scen))
    xy_av = avoid.get(("xy", m0s[0]))
    m3_av = avoid.get(("updown_best_root", m0s[0]))

    # ---- 7.2 mechanism table -------------------------------------------
    mech_rows = []
    for kind in ("sb", "spin", "swap"):
        m = mech[kind]
        mech_rows.append(
            "<tr><td class='l'><b>%s</b><br><span class='sub'>%s</span></td>"
            "<td class='l'>%s</td><td class='l'>%s</td>"
            "<td class='l'>%s</td></tr>"
            % (REC_LABEL[kind], REC_CITE[kind], esc(m["detect"]),
               esc(m["action"]),
               "路径不变 ⇒ 保序" if kind != "swap"
               else "<b>回退改路径 ⇒ 破坏保序</b>"))
    mech_tbl = ("<table><thead><tr><th class='l'>机制</th>"
                "<th class='l'>死锁检测</th><th class='l'>恢复动作</th>"
                "<th class='l'>对路径/保序的影响</th></tr></thead>"
                "<tbody>%s</tbody></table>" % "".join(mech_rows))

    # ---- 7.3 per-m0 result table ---------------------------------------
    def res_table(m0: int) -> str:
        head = ("<tr><th class='l'>路由</th><th class='l'>机制</th>"
                "<th>area</th><th>A 中位/最差</th>"
                "<th>牺牲最差</th><th>保序</th>"
                "<th>T<sub>e2e</sub> 中位 (ns)</th>"
                "<th>T<sub>e2e</sub> 最差 (ns)</th><th>检测次数 中位/最差</th>"
                "<th>恢复动作最差</th><th>停摆占比</th><th>完成场景</th></tr>")
        body = []
        for rt in routings:
            for j, kind in enumerate(meta["kinds"]):
                s = srow(kind, m0, rt)
                rt_cell = ("<td class='l' rowspan='%d'><b>%s</b></td>"
                           % (len(meta["kinds"]), esc(RT_SHORT[rt]))
                           if j == 0 else "")
                if not s["n_ok"]:
                    body.append(
                        "<tr>%s<td class='l'>%s</td>"
                        "<td colspan='9' class='l cap-bad'>全部场景未完成：%s"
                        "</td><td>0/%d</td></tr>"
                        % (rt_cell, REC_LABEL[kind],
                           esc(", ".join(x for x in s["reasons"] if x)),
                           s["n_scen_total"]))
                    continue
                body.append(
                    "<tr>%s<td class='l'>%s</td><td>%.3f</td><td>%d/%d</td>"
                    "<td>%d</td><td>%s</td><td>%.0f</td><td><b>%.0f</b></td>"
                    "<td>%s/%s</td><td>%s</td><td>%.0f%%</td>"
                    "<td>%d/%d</td></tr>"
                    % (rt_cell, REC_LABEL[kind], s["area"], s["A_med"],
                       s["A_worst"], s["sac_worst"],
                       ("%d/%d" % (s["n_ordered_ok"], s["n_ok"])
                        if s["n_ordered_ok"] == s["n_ok"]
                        else "<b>%d/%d</b>" % (s["n_ordered_ok"], s["n_ok"])),
                       s["t_e2e_ns_med"], s["t_e2e_ns_worst"],
                       s["detect_med"], s["detect_worst"], s["recover_worst"],
                       100 * s["stall_frac_med"], s["n_ok"],
                       s["n_scen_total"]))
        for sch in ("xy", "updown_best_root"):
            a = avoid.get((sch, m0))
            if not a:
                continue
            body.append(
                "<tr class='ref'><td class='l'>避免类</td><td class='l'>%s</td>"
                "<td>%.3f</td><td>%d/%d</td><td>%d</td><td>%d/%d</td>"
                "<td>%.0f</td><td><b>%.0f</b></td><td>—</td><td>—</td>"
                "<td>0%%</td><td>%d/%d</td></tr>"
                % (esc(E2E_SHORT.get(sch, sch)), a["area"], a["A_med"],
                   a["A_worst"], a.get("sac_worst", 0), a["n_scen"],
                   a["n_scen"], a["t_e2e_ns_med"], a["t_e2e_ns_worst"],
                   a["n_scen"], a["n_scen_total"]))
        return ("<table><thead>%s</thead><tbody>%s</tbody></table>"
                % (head, "".join(body)))

    # ---- 7.4 hardware table --------------------------------------------
    hw_rows = []
    for kind in ("sb", "spin", "swap"):
        m = mech[kind]
        # R0 is the worst case for SWAP's reorder buffer, so price it there.
        s = srow(kind, m0s[-1], "xy_detour")
        extra = "%.0f bit/router" % m["extra_bits"]
        if m["extra_flits"]:
            extra += " + %.2f flit 缓冲/router（全片 %d 个包缓冲）" % (
                m["extra_flits"], meta["n_sb_nodes"])
        if kind == "swap" and s.get("reorder_depth_max"):
            extra += " + <b>重排序缓冲 ≥%d flit/目的</b>" % s[
                "reorder_depth_max"]
        hw_rows.append(
            "<tr><td class='l'><b>%s</b></td><td class='l'>%s</td>"
            "<td class='l'>%s</td><td>%.3f</td><td>%.3f</td></tr>"
            % (REC_LABEL[kind], extra, esc(m["pct_src"]),
               s["area"], s["area_hi"]))
    hw_tbl = ("<table><thead><tr><th class='l'>机制</th>"
              "<th class='l'>本项目口径可算的新增存储</th>"
              "<th class='l'>控制逻辑面积（各文自报 / 第三方复现）</th>"
              "<th>area（自报）</th><th>area（第三方上限）</th>"
              "</tr></thead><tbody>%s</tbody></table>" % "".join(hw_rows))

    # ---- 7.5 three hard properties, same format as §2.5 -----------------
    cap_rows = []
    for rt in routings:
        st = static[rt]
        for j, kind in enumerate(("sb", "spin", "swap")):
            s = srow(kind, m0s[-1], rt)
            ordered = s["n_ok"] and s["n_ordered_ok"] == s["n_ok"]
            first = ""
            if j == 0:
                first = (
                    "<td class='l' rowspan='3'><b>%s</b></td>"
                    "<td class='l cap-ok' rowspan='3'>✓ %d/%d 零牺牲"
                    "<div class='sub'>另 %d 场景好节点被物理断连，"
                    "累计弃 %d 个；峰值负载 %d（下界的 %.2f×）</div></td>"
                    % (esc(RT_SHORT[rt]), st["sac0"], st["ok"],
                       st["ok"] - st["sac0"], st["sac_tot"],
                       st["load_med"], st["rat_med"]))
            if st["cyc"] == 0 and kind == "swap":
                dl = ("<td class='l cap-ok'>✓ CDG 0/%d 成环<div class='sub'>"
                      "路由本身已合法，但 SWAP 不做检测，照常交换"
                      "（最差 %s 次）⇒ 白付乱序代价</div></td>"
                      % (st["ok"], format(s["recover_worst"], ",")))
            elif st["cyc"] == 0:
                dl = ("<td class='l cap-ok'>✓ CDG 0/%d 成环<div class='sub'>"
                      "检测器在 %d 场景里<b>一次都没响</b>，"
                      "等于白拿一层从不出险的保险</div></td>"
                      % (st["ok"], st["ok"]))
            elif not s["n_ok"]:
                dl = ("<td class='l cap-bad'><b>✗</b> CDG %d/%d 成环"
                      "<div class='sub'>恢复机制未能在 %d 拍内跑完</div></td>"
                      % (st["cyc"], st["ok"], meta.get("t_max", 1500000)))
            else:
                dl = ("<td class='l cap-warn'>△ CDG %d/%d 成环"
                      "<div class='sub'>靠运行时恢复保证「不永久死锁」："
                      "%d/%d 场景完成，%s</div></td>"
                      % (st["cyc"], st["ok"], s["n_ok"], s["n_scen_total"],
                         ("无检测，靠 TDM 周期性交换（最差 %s 次）"
                          % format(s["recover_worst"], ",") if kind == "swap"
                          else "最差 %d 次检测 / %d 次恢复动作"
                               % (s["detect_worst"], s["recover_worst"]))))
            cap_rows.append(
                "<tr>%s<td class='l'>%s</td>%s"
                "<td class='l %s'>%s<div class='sub'>%s</div></td>"
                "<td class='l %s'>%s</td></tr>"
                % (first, REC_LABEL[kind], dl,
                   "cap-ok" if ordered else "cap-bad",
                   "✓ %d/%d 保序" % (s["n_ordered_ok"], s["n_ok"]) if ordered
                   else "<b>✗</b> %d/%d 乱序"
                        % (s["n_ok"] - s["n_ordered_ok"], s["n_ok"]),
                   "不改路径、不回退" if ordered
                   else "u-turn 回退 ⇒ 需重排序缓冲",
                   "cap-ok" if ordered else "cap-bad",
                   "可用" if ordered else "<b>需额外重排序硬件</b>"))
    cap_tbl = ("<table class='cap'><thead><tr><th class='l'>路由</th>"
               "<th class='l'>避障</th><th class='l'>机制</th>"
               "<th class='l'>无死锁</th>"
               "<th class='l'>保序</th><th class='l'>判定</th></tr></thead>"
               "<tbody>%s</tbody></table>" % "".join(cap_rows))

    png = ""
    if (ROOT / "results" / RECOVERY_PNG).exists():
        png = (
            '<figure class="e2e-fig"><img src="%s" alt="recovery Pareto" '
            'style="max-width:100%%;height:auto;background:#fff;'
            'border:1px solid #e0e0e0"/><figcaption>死锁<b>恢复</b>类独立 '
            'Pareto（%d 场景，1 VC，零牺牲）：3 种机制 × 4 种路由。'
            '面积只由机制决定，所以同一机制的四种路由落在同一 x 上，'
            '用<b>点形</b>区分（○ R0 XY+绕障，△ R1 min-max，□ R2 M3′+兜底，'
            '▽ R3 Super-turn/1VC）。<b>横向短线</b>= 同一个设计的面积区间：'
            '左端 = 各论文自报的控制逻辑开销，右端 = 第三方复现测得的更大开销'
            '（SB 自报 &lt;0.5%% vs 第三方 10%%，SPIN 自报 4%% vs 第三方 ~15%%，'
            'SWAP 只有一个来源故无短线）。'
            '实心=44 场景最差，空心=中位；灰菱形为 1VC <b>避免</b>类同轴对照，'
            '前沿分开计算。纵轴对数。</figcaption></figure>'
            % (RECOVERY_PNG, n_scen))

    tdd_tbl = ""
    if RECOVERY_TDD_JSON.exists():
        td = json.loads(RECOVERY_TDD_JSON.read_text())
        by: dict[tuple[str, str], dict[int, object]] = defaultdict(dict)
        for r in td["rows"]:
            by[(r["kind"], r["scenario"])][r["t_dd"]] = (
                r.get("makespan") or r.get("reason"))
        tl = td["meta"]["t_dd_list"]
        head = ("<tr><th class='l'>机制 / 场景</th>"
                + "".join("<th>t<sub>DD</sub>=%d</th>" % t for t in tl)
                + "</tr>")
        body = []
        for (kind, scen), d in sorted(by.items()):
            body.append("<tr><td class='l'>%s / %s</td>%s</tr>"
                        % (REC_LABEL[kind], scen,
                           "".join("<td>%s</td>" % d.get(t, "—") for t in tl)))
        # Does the SB-vs-SPIN verdict survive any detection threshold?
        gap = []
        for scen in td["meta"]["scenarios"]:
            for t in tl:
                a = by.get(("sb", scen), {}).get(t)
                b = by.get(("spin", scen), {}).get(t)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    gap.append(b / a)
        swing: dict[str, list[float]] = defaultdict(list)
        for (kind, scen), d in by.items():
            v = [d[t] for t in tl if isinstance(d.get(t), (int, float))]
            if len(v) > 1 and min(v):
                swing[kind].append(max(v) / min(v))
        sw_all = [x for v in swing.values() for x in v]
        tdd_tbl = (
            "<h3>7.7 检测阈值敏感度（结论不依赖 t<sub>DD</sub>）</h3>"
            "<p class='note'>论文取值 SB=34 / SPIN=128 是按<b>单拍链路</b>调的；"
            "本设计链路 7–9 拍，等效阈值本应更大，所以有必要确认"
            "「SPIN 慢」不是阈值选错造成的。下表是 alltoall makespan（cy，"
            "m<sub>eff</sub>≈2，路由取 R0——R2 根本不触发恢复，阈值无从体现）"
            "在 t<sub>DD</sub>∈{%s} 上的全扫描：</p>"
            "<ul><li><b>阈值本身确实有影响，但方向对 SPIN 无益：</b>"
            "从 %d 调到 %d，makespan 整体变长，最大 %.2f×"
            "（个别场景在 34↔128 之间有 ±13%% 抖动：更早探测偶尔会换掉解锁顺序）。"
            "检测次数<b>完全不变</b>（同一批环、同一批依赖），"
            "多等的时间几乎线性地进了「停摆」里 —— 也就是说"
            "「阈值调小」只能省掉这份等待，救不了绕环本身的开销："
            "SB 的绝对值小、占比大（最多省 %.0f%%），"
            "SPIN 的时间几乎全在绕环上（最多省 %.0f%%）。</li>"
            "<li><b>排序从未翻转：</b>SPIN/SB 的 makespan 比值在<b>所有</b>"
            "阈值上都是 %.1f×–%.1f×。所以 7.6 的结论与该参数无关，"
            "根因是「一次 spin 只让环前进 1 跳，却要付 2 趟绕环」，"
            "这是机制固有的，调参调不掉。</li></ul>"
            "<table><thead>%s</thead><tbody>%s</tbody></table>"
            % (", ".join(str(t) for t in tl), tl[0], tl[-1],
               max(sw_all) if sw_all else 1,
               100 * (1 - 1 / max(swing["sb"])) if swing["sb"] else 0,
               100 * (1 - 1 / max(swing["spin"])) if swing["spin"] else 0,
               min(gap) if gap else 0, max(gap) if gap else 0,
               head, "".join(body)))

    sb1, sp1, sw1 = (srow(k, m0s[-1], "xy_detour")
                     for k in ("sb", "spin", "swap"))
    r2sb = srow("sb", m0s[-1], "updown_relax")
    r2sw = srow("swap", m0s[-1], "updown_relax")
    r1sb = srow("sb", m0s[-1], "minmax")
    r3sb = srow("sb", m0s[-1], "super_turn_1vc")
    base_1vc = meta["area_model"]["base_1vc"]
    a_lo = min(srow(k, m0s[-1])["area"] for k in ("sb", "spin", "swap"))
    a_hi = max(srow(k, m0s[-1])["area_hi"] for k in ("sb", "spin", "swap"))
    pct_lo, pct_hi = 100 * (a_lo / base_1vc - 1), 100 * (a_hi / base_1vc - 1)
    m3_hi = avoid.get(("updown_best_root", m0s[-1]))
    xy_hi = avoid.get(("xy", m0s[-1]))
    ratio = worse = better = ""
    if m3_hi:
        ratio = ("最好的恢复方案（%s）最差 T<sub>e2e</sub> 仍是 M3′ 的 %.1f 倍"
                 "（中位 %.1f 倍）"
                 % (REC_LABEL["sb"],
                    sb1["t_e2e_ns_worst"] / m3_hi["t_e2e_ns_worst"],
                    sb1["t_e2e_ns_med"] / m3_hi["t_e2e_ns_med"]))
        worse = ("%.1f×/%.1f×（最差/中位）"
                 % (sp1["t_e2e_ns_worst"] / m3_hi["t_e2e_ns_worst"],
                    sp1["t_e2e_ns_med"] / m3_hi["t_e2e_ns_med"]))
    if xy_hi:
        better = ("%.0f ns vs %.0f ns" % (sb1["t_e2e_ns_med"],
                                          xy_hi["t_e2e_ns_med"]))

    return f"""
<h2>7. 死锁恢复类方案（Static Bubble / SPIN / SWAP）× 路由选择</h2>
<p class="note">§2–§6 全是<b>死锁避免</b>：用转向限制（A 类）或 VC 分层（B 类）
让 CDG 先天无环，代价是绕路、多 VC 或<b>牺牲计算节点</b>。本节换第三条路
——<b>死锁恢复</b>：把「无死锁」这个约束从路由里<b>拿掉</b>，允许 CDG 成环，
再用运行时机制把死锁解开。按要求，本节 Pareto 与避免类<b>分开</b>作图
（同坐标轴，便于对照）。</p>
<p class="note">恢复机制一旦到位，路由就<b>自由</b>了，于是「配哪种路由」本身成了
一个设计变量。本节扫四种，全是 1 VC、全都只放弃残图<b>物理断连</b>的好节点。
其中 <b>R3</b> 专门检验一个很自然的想法：<i>M3/M3′ 天然无死锁，恢复机制在它上面是浪费；
那就换成 Super-turn（M0s）的转向集合，只给 1 个物理 VC，把它本来靠 VC 分层
换来的无死锁性丢掉，赌「剩下的死锁概率很小」，再用恢复机制兜底</i>。
下面用「<b>环上通道占比</b>」（CDG 中落在某个环里的通道数 / 总通道数，
Tarjan SCC 精确统计）来量化这个「概率很小」到底成不成立。</p>

<h3>7.1 先回答：有故障的 2D mesh 上最优的避障路由是哪个？</h3>
<p class="note"><b>先说避免类内部的答案（§6 的数据，m₀={m0s[-1]}）。</b>1 VC 这一档里最优的路由是
<b>M3′ best-root Up*/Down*（min-max 选根）</b>：44 场景最差
{m3_hi['t_e2e_ns_worst'] if m3_hi else '?':.0f} ns、面积
{m3_hi['area'] if m3_hi else 0:.3f}，worst-case 优于 M3 原版（7678 ns）、
M4 Segment 与各种固定 turn model。固定转向模型（M1 XY / M0 East-first /
M4 Segment）在预算故障下只能保住
<b>A 中位 {xy_av['A_med'] if xy_av else '?'}/48、最差
{xy_av['A_worst'] if xy_av else '?'}/48</b> 个好节点，强扩展下
m<sub>eff</sub>∝(48/A)² 暴涨，端到端反而最慢（最差 25 μs 量级）。
比 M3′ 更快的只有多 VC 方案（M11 Stripe-VC 6.1 μs 最差，但面积 3.67 = 4.1×），
不在「1 VC」这一档；§5 的 BB-UD 系列能做到 7061 ns，但那是<b>调度层</b>
（batch barrier）的改进，路由仍然是 M3′。
<b>所以「最优避障路由」= M3′ best-root。</b></p>
<p class="note"><b>但请注意「避障最优」有两种口径</b>，这正是本节要拆开的地方：
① <i>无死锁前提下</i>最优 = M3′（要付转向限制的绕路代价 + 牺牲阶梯）；
② <i>不要求无死锁</i>时最优 = 纯 min-max 最短路（R1，负载可以压到理论下界的
{static['minmax']['rat_med']:.2f}×，比 M3′ 的 {static['updown_relax']['rat_med']:.2f}× 好一大截）。
恢复机制解锁的正是口径 ②。四种路由的静态指标：</p>
{rt_tbl}
<ul>
<li><b>零牺牲（四种路由都成立）：</b>{n_sac0}/{n_scen} 场景牺牲 0 个好节点；
仅 {esc(', '.join(sac_bad)) or '无'} 因残图把好节点<b>物理断连</b>而必须放弃。
对照避免类：M1 XY 最差只剩 A={xy_av['A_worst'] if xy_av else '?'} 个节点，
M3′ best-root 最差 A={m3_av['A_worst'] if m3_av else '?'}。</li>
<li><b>R0 / R1 的 CDG 一定成环</b>（{static['xy_detour']['cyc']}/{n_scen} 与
{static['minmax']['cyc']}/{n_scen}）：非 XY 序的转弯一出现就闭环，
所以这两套表<b>本身是非法的</b>，必须配恢复机制才能用。</li>
<li><b>R3 的「死锁概率很小」不成立——它比基线 XY 还危险。</b>
把 Super-turn 的两层 Glass–Ni 模型合并到 1 个 VC 之后，
{static['super_turn_1vc']['cyc']}/{n_scen} 场景成环，环上通道占比中位
<b>{100 * static['super_turn_1vc']['cyc_frac_med']:.0f}%</b>
（R0 只有 {100 * static['xy_detour']['cyc_frac_med']:.0f}%、
R1 {100 * static['minmax']['cyc_frac_med']:.0f}%、R2 0%）。
原因是结构性的：<b>Super-turn 的无死锁性全部来自 VC 分层，而不是来自转向集合</b>——
它挑的互补对（44 场景里 {esc('、'.join(static['super_turn_1vc']['mode']))}）
两层合起来<b>几乎不禁任何转向</b>（east-first ∪ west-first 的并集除了 U-turn 之外全放开），
所以压到 1 VC 就退化成「近似无转向模型」，只是路径比 R1 短一点、负载还更差
（{static['super_turn_1vc']['load_med']} vs R1 的 {static['minmax']['load_med']}）。
这正是 A 类（转向限制）与 B 类（VC 分层）不能混着省的地方：
<b>拿掉 VC 就等于拿掉了它的全部合法性，不是「小概率残留」。</b></li>
<li><b>但 R2 的 CDG 是 {static['updown_relax']['cyc']}/{n_scen} 成环，而且这不是运气。</b>
只要残图连通，BFS 高度函数就保证「先沿树上行到公共祖先、再下行」这条路径一定存在
（§3 的定理 1/2），所以「树到不了的对」<b>永远不会出现</b>——实测
{n_scen}/{n_scen} 场景里需要补非法路径的对数
= {static['updown_relax']['free_worst']}。换句话说
<b>R2 恒等于 M3′ 本体，恢复机制在这条路由上是一层永远不会触发的保险</b>
（逐场景核对：R2 的 alltoall makespan 与 §6 的 M3′ 在 86/88 个
（场景×m₀）组合上<b>逐拍相同</b>）。</li>
<li><b>唯一的差别来自「先剪掉物理断连的节点、再做牺牲搜索」这个实现细节，与恢复机制无关。</b>
在 <code>b_r4_l3</code> 上 §6 的 M3′ 求解器弃了 4 个节点（7/23/31/47），
而先把物理断连的 23/31 剪掉之后，root=40 的 Up*/Down* 表能覆盖剩下
42 个节点且 CDG 无环（已 <code>validate_routing</code> 通过）——
即 §6 M3′ 最差牺牲 {m3_av['sac_worst'] if m3_av else '?'} 个里有
{(m3_av['sac_worst'] if m3_av else 0) - static['updown_relax']['sac_worst']} 个是
<b>贪心牺牲阶梯过于保守</b>，不是 Up*/Down* 的固有代价。
这是避免类求解器可以直接修的一处，不能记在恢复机制的功劳簿上。</li>
<li><b>控制实验（R0，无恢复机制）：</b>{'；'.join(none_bits)}。
低载荷下成环但不死锁，正是三篇论文的立论前提（Static Bubble 实测
「多数不规则拓扑只在 0.1–0.3 flit/node/cycle 以上才死锁，比真实应用高一个量级」）；
但 <b>all-to-all 是一次性满注入的 collective</b>，直接落在会死锁的一侧。</li>
</ul>

<h3>7.2 三种机制与建模</h3>
{mech_tbl}
<p class="note"><b>本设计的关键放大器：</b>SB 与 SPIN 的 probe / disable / move /
check_probe 都要沿死锁环<b>绕一整趟</b>。论文里链路 1 拍，一趟 ≈ 环长×2 拍；
本设计 H=7 / V=9 拍，一趟 = Σ(1+link_lat) ≈ 环长×8–10 拍。
实测（m₀={m0s[-1]}，R0 路由）平均环长中位 {med('sb', m0s[-1], 'ring_avg')} 个转向、最长
{srow('sb', m0s[-1], 'xy_detour').get('ring_max')}，
<b>一趟 = {med('sb', m0s[-1], 'lap_avg')} 拍</b>
（SPIN 的一次 spin 要 2 趟 ≈ {2 * (med('spin', m0s[-1], 'lap_avg') or 0):.0f} 拍，
只换来环整体前进 1 跳）。恢复延迟因此比论文语境放大近一个数量级。</p>
<p class="note"><b>建模口径与偏差（<code>utils/pg_deadlock_recovery.py</code>）：</b>
① DES 是 <code>dse_pg_alltoall_8x6.simulate_alltoall</code> 的 fork，
<code>selftest()</code> 逐周期校验二者在无死锁输入上完全一致，故与 §6 数字可比；
② 1 VC 下依赖链唯一 ⇒ probe 走链即精确判环，<b>无假阴性</b>，
「假阳性」只来自 probe 绕环期间依赖已自行解开（表中单列）；
③ 全局同时只允许一个恢复 FSM（SPIN 的轮转优先级/epoch 与 SWAP 的
「全网同时只一次交换」本就如此，SB 略偏保守）；
④ 本项目一个包=1 flit，故 SB 的 packet-sized 气泡记为 1 flit；
⑤ SB 的 <code>is_deadlock</code>/<code>IO_priority</code>（禁止其他包进入环上转向）
与 SPIN 的按 VC 冻结都已建模；⑥ SWAP 按 (cycle/m)%(K·N)=router_id 的 TDM
触发，K=1 ⇒ 每个 router 每 {meta['swap_period_cy']} 拍轮到一次、
且只在队头确实被下游堵住时才发起，握手 4 拍。</p>
<p class="note"><b>一条必须核验的前提（SB）：</b>Static Bubble 的正确性依赖
「<b>任何环都至少经过一个带气泡的 router</b>」，HPCA'17 是在<b>完好</b> mesh 上证明这条
放置规则的。本项目的残图上气泡节点本身也可能坏掉，所以这条不再自动成立。
实测：全部 {sum(r['n_detect'] for r in rows if r['kind'] == 'sb' and r.get('feasible'))}
次检测到的环<b>都</b>至少含一个存活气泡（<code>no_bubble</code>=
{sum(r['no_bubble'] for r in rows if r['kind'] == 'sb' and r.get('feasible'))}），
故 8×6 + 预算故障下 15 个气泡的放置依然充分；若出现反例，SB 会直接退化为
「检测到但无法恢复」，这也是代码里单列该计数器的原因。</p>

<h3>7.3 端到端时间、恢复开销与保序</h3>
{''.join('<h4>m₀ = %d</h4>%s' % (m0, res_table(m0)) for m0 in m0s)}
<p class="note">「停摆占比」= 整个 alltoall 期间<b>全网无任何活动</b>的周期占比
⚠ <b>「不装恢复机制（对照）」各行的统计只覆盖它自己跑完的那几个场景</b>
（死锁的场景没有 makespan），所以它的时间/牺牲列<b>不能</b>与其余行横向比——
它在表里只是用来说明「不装恢复机制会死锁多少场景」。<br>
「停摆占比」= 整个 alltoall 期间<b>全网无任何活动</b>的周期占比
（等检测 + 等绕环确认）。SB / SPIN 的时间主要花在这里；SWAP 无检测所以此列低，
但它把时间花在最多 {sw1.get('recover_worst', 0):,} 次交换与同样多次<b>回退</b>上
（见 7.6 第 2 条）。「恢复动作」= grant（SB）/ spin（SPIN）/ swap（SWAP）次数，
三者语义不同，不可横向比大小，只能各自看趋势。</p>

<h3>7.4 硬件代价</h3>
<p class="note">本项目 area 口径只对缓冲/表这类结构可算（A<sub>flit</sub>/flit-slot，
ROM 位按 0.15 折算）；FSM、probe 单元、mux/u-turn 这类控制逻辑只有各论文自己的
综合数据可用，故按「基线 1VC router 面积的百分比」计入，并给出第三方复现的上限
（图中横向短线即这段区间）。三者<b>都不增加 VC</b>，所以缓冲面积与 1VC 基线相同
——这正是恢复类最大的卖点。</p>
{hw_tbl}

<h3>7.5 三性质核验（与 §2.5 同口径）</h3>
<p class="note">恢复类在「无死锁」这一列的性质<b>与避免类不同</b>：它不保证 CDG 无环，
只保证<b>不会永久卡死</b>（活性由恢复机制提供，安全性由「环上至少一个包能动」提供），
所以 R0 / R1 / R3 这一列只能标 △。<b>R2 是例外</b>：它的路由本身就合法，
这一列回到 ✓，代价是恢复机制变成纯冗余——
换句话说，这张表里「△ 换零牺牲」的交易只在 R0 / R1 / R3 成立，
而它们的零牺牲 R2 也有。</p>
{cap_tbl}

<h3>7.6 独立 Pareto 与结论</h3>
{png}
<ol>
<li><b>直接回答「用最优避障路由叠加恢复机制」：净收益为零，只多付面积。</b>
R2（= 最优避障路由 M3′ best-root，外加「树到不了就走非法路径」这条兜底规则）
+ SB：m₀={m0s[-1]} 最差 {r2sb['t_e2e_ns_worst']:.0f} ns / 中位
{r2sb['t_e2e_ns_med']:.0f} ns，{r2sb['n_ordered_ok']}/{r2sb['n_ok']} 保序，
面积 {r2sb['area']:.3f}（+{100 * (r2sb['area'] / base_1vc - 1):.1f}%，
第三方复现口径最多 +{100 * (r2sb['area_hi'] / base_1vc - 1):.1f}%）。
避免类 M3′ 本体：{m3_hi['t_e2e_ns_worst'] if m3_hi else '?'} ns 最差、
面积 {m3_hi['area'] if m3_hi else 0:.3f}。
<b>时间一模一样（{r2sb['t_e2e_ns_worst'] / m3_hi['t_e2e_ns_worst'] if m3_hi else 0:.2f}×），
因为兜底规则一次都没用上、检测器一次都没响
（{static['updown_relax']['free_worst']} 对非法路径、{r2sb['detect_worst']} 次检测）。</b>
原因是结构性的、不是运气：Up*/Down* 在任何连通残图上都覆盖全部节点且 CDG 无环，
所以<b>最优避障路由根本不给恢复机制留活干</b>。
这条正好也说明恢复类的适用边界：<b>它只能救「路由本身不合法」，
而最优避障路由的定义就是「合法」。</b></li>
<li><b>R3（Super-turn 转向集合压到 1 VC）也不行，而且失败得更彻底：</b>
它的初衷是「M3′ 天然无死锁 ⇒ 恢复机制没用武之地，那就换个更灵活的转向集合，
留一点小概率死锁给恢复机制」。但 Super-turn 的合法性<b>全部来自 VC 分层</b>：
它挑的两层是互补的 Glass–Ni 模型，并集除 U-turn 外几乎不禁转向，
拿掉 VC 就等于没有转向模型。实测环上通道占比中位
<b>{100 * static['super_turn_1vc']['cyc_frac_med']:.0f}%</b>（R0 只有
{100 * static['xy_detour']['cyc_frac_med']:.0f}%），
{static['super_turn_1vc']['cyc']}/{n_scen} 场景成环，
m₀={m0s[-1]} 上 +SB 最差 {r3sb['t_e2e_ns_worst']:.0f} ns
= R2 的 {r3sb['t_e2e_ns_worst'] / r2sb['t_e2e_ns_worst']:.1f}×、
R0 的 {r3sb['t_e2e_ns_worst'] / sb1['t_e2e_ns_worst']:.1f}×，
峰值负载也没换到好处（{static['super_turn_1vc']['load_med']} vs R2 的
{static['updown_relax']['load_med']}）。
<b>结论：A 类（转向限制）与 B 类（VC 分层）不能混着省——
省掉 VC 不是「留一点小概率死锁」，而是把无死锁性整个丢掉。</b>
顺带也试过「优先选环最少的转向集合」（允许少数对走模型外路径）：
环上通道占比只降到中位 45%，负载还更差，同样救不回来。</li>
<li><b>反直觉的核心结论：恢复类的成败不由「负载均衡」决定，而由「CDG 里有多少环」决定。</b>
R1（纯 min-max）把峰值链路负载压到理论下界的
{static['minmax']['rat_med']:.2f}×，比 R0 的 {static['xy_detour']['rat_med']:.2f}×
和 R2 的 {static['updown_relax']['rat_med']:.2f}× 都好，
按纯带宽模型它<b>应该</b>最快；实测却最慢
（SB 上 m₀={m0s[-1]} 最差 {r1sb['t_e2e_ns_worst']:.0f} ns，
是 R2 的 {r1sb['t_e2e_ns_worst'] / r2sb['t_e2e_ns_worst']:.1f}×、R0 的
{r1sb['t_e2e_ns_worst'] / sb1['t_e2e_ns_worst']:.1f}×；
检测次数中位 {r1sb['detect_med']} vs R0 的 {sb1['detect_med']} vs R2 的
{r2sb['detect_med']}）。
原因：无转向模型 ⇒ 每条链路上都有各方向的转弯 ⇒ 环极多且一解开就重建，
而<b>每次恢复都要按 {med('sb', m0s[-1], 'lap_avg', 'xy_detour')} 拍的量级绕环一趟</b>。
均衡省下的带宽（约 {100 * (1 - static['minmax']['rat_med'] / static['xy_detour']['rat_med']):.0f}%）
远不够补这份开销。<b>所以「先把路由做成最优负载、再拿恢复机制兜底」是个陷阱；
正确做法反过来：让路由尽量保持无环，只在它救不了的地方才动用恢复。</b></li>
<li><b>恢复类内部排序 <span style="letter-spacing:.02em">SB ≪ SPIN &lt; SWAP</span>，
差距达一个数量级</b>（R0 上，m₀={m0s[-1]} 最差 T<sub>e2e</sub>：
{sb1['t_e2e_ns_worst']:.0f} / {sp1['t_e2e_ns_worst']:.0f} /
{sw1['t_e2e_ns_worst']:.0f} ns；中位 {sb1['t_e2e_ns_med']:.0f} /
{sp1['t_e2e_ns_med']:.0f} / {sw1['t_e2e_ns_med']:.0f} ns）。机制层面的原因：
<ul>
<li><b>SB</b> 一次 grant 就把环<b>彻底拆掉</b>：disable 沿环走一趟，期间禁止新包进入
环上转向（<code>is_deadlock</code>），气泡吸收后环不会立刻重建 ⇒ 检测次数最少
（中位 {sb1['detect_med']}、最差 {sb1['detect_worst']}），停摆占比
{100 * sb1['stall_frac_med']:.0f}%。</li>
<li><b>SPIN</b> 一次 spin 只让环整体<b>前进 1 跳</b>，但要付 2 趟绕环
（≈{2 * (med('spin', m0s[-1], 'lap_avg') or 0):.0f} 拍）；饱和注入下环立刻由后续包重建，
于是「检测→自旋→重建」反复循环（最差 {sp1['detect_worst']} 次检测、
{sp1['recover_worst']} 次恢复动作），停摆占比高达
{100 * sp1['stall_frac_med']:.0f}% ⇒ 整体被拖慢 {worse or 'N/A'}。</li>
<li><b>SWAP</b> 不检测，只要轮到自己（每个 router 每 {meta['swap_period_cy']} 拍一次，
全网每拍至多一次）且队头<b>被堵住</b>就交换。饱和注入下「被堵住」几乎恒成立，
而其中绝大多数只是<b>普通拥塞、并非死锁</b>，于是它把交换当成常态操作：
最差 {sw1['recover_worst']:,} 次交换 = 同样多次<b>回退一跳</b>，
纯粹的带宽浪费（往回走的那一跳还要再走一遍）。
换句话说，SWAP 的代价与<b>拥塞程度</b>成正比，而与<b>死锁次数</b>无关，
这在 collective 场景是最坏的组合。</li>
</ul></li>
<li><b>就算配最差的路由（R0，即原来那版「基线 XY + 绕障」），恢复类也没有全面落败：</b>
{ratio}，可是 <b>R0+SB 的中位时间已经优于避免类的 M1 XY</b>（{better}）。
原因不在网络本身，而在强扩展：M1 XY 为保证 CDG 无环必须弃掉大量好节点
（A 中位仅 {xy_hi['A_med'] if xy_hi else '?'}），m<sub>eff</sub> 按 (48/A)² 暴涨，
省下来的时间远不够补。<b>所以在「XY 路由表不能改」的工程约束下，
加一套 SB 也比用转向限制去砍节点更划算。</b></li>
<li><b>SWAP 破坏保序，这是本项目的硬性质，而且换路由也救不了：</b>
被换回的包 u-turn 回退一跳，同一 (s,d) 的后发包可能抢到前面。
实测 m₀={m0s[-1]}：R0 上 {sw1.get('n_ordered_ok', 0)}/{sw1.get('n_ok', 0)} 保序、
R2 上 {r2sw['n_ordered_ok']}/{r2sw['n_ok']} 保序——<b>即使 CDG 无环、
一次死锁都没有，SWAP 依然照常交换</b>（subactive 机制，只看队头是否被堵），
所以它在健康 mesh 上也会乱序。最坏乱序对数
{max((r['n_pairs_out_of_order'] for r in rows
      if r['kind'] == 'swap' and r.get('feasible')), default=0)}、
需要 ≥{sw1.get('reorder_depth_max', 0)} flit/目的 的重排序缓冲（已计入其 area）。
7.5 的三性质表里这一列直接判否（口径同 §2.5）。
SB / SPIN 不改路径也不回退，保序不受影响。</li>
<li><b>选型结论（按约束条件分档）：</b>
<ul>
<li><b>能自由设计路由</b> ⇒ <b>M3′ best-root 本体，不要加恢复机制</b>
（1VC，面积 {m3_hi['area'] if m3_hi else 0:.3f}，保序，无检测逻辑、无运行时开销）。
再叠 SB/SPIN/SWAP 只会多 +{100 * (r2sb['area'] / base_1vc - 1):.1f}%
（第三方口径至多 +{100 * (max(srow(k, m0s[-1], 'updown_relax')['area_hi'] for k in ('sb', 'spin', 'swap')) / base_1vc - 1):.1f}%）
面积换 0 收益。想把 §6 里那
{(m3_hi['sac_worst'] if m3_hi else 0) - r2sb['sac_worst']} 个多余牺牲救回来，
改牺牲搜索（先剪物理断连节点）就够了，不需要任何硬件。</li>
<li><b>路由表被写死成 XY（例如复用现成 IP、不许改路由计算）</b> ⇒ R0 + Static Bubble：
慢 {sb1['t_e2e_ns_worst'] / (m3_hi['t_e2e_ns_worst'] if m3_hi else 1):.1f}×（最差）
但零牺牲、保序、面积几乎不变，仍优于「用固定转向模型硬砍节点」。
这是恢复类在本工作负载下唯一站得住的用法。</li>
<li><b>任何情况下都别选</b>：R1（纯负载最优路由）、R3（Super-turn 压到 1 VC）
配任何机制，以及 SWAP（破坏保序）。</li>
</ul>
另外，恢复类真正的舞台仍是<b>低注入率</b>的通用 cache 流量：那时恢复几乎不触发，
它退化成「零牺牲 + 1VC + 最短路低时延」。本节 <code>none</code> 行即此极限：
在<b>那 {srow('none', m0s[0], 'xy_detour')['n_ok']}/{n_scen} 个本来就不死锁的场景</b>里，
无转向限制的 R0 只要 {srow('none', m0s[0], 'xy_detour')['t_e2e_ns_med']:.0f} ns，
比同场景任何避免类都快。这与 Static Bubble 论文自己的观察一致：
PARSEC 下压根没观察到死锁；而本项目是 <b>collective 饱和注入</b>，
前提被破坏，结论就反过来。</li>
</ol>
{tdd_tbl}
<p class="note">数据：<code>results/pg_recovery_e2e.json</code>
（{n_scen} 场景 × {len(routings)} 路由 × {len(meta['kinds'])} 机制
× m₀∈{{{', '.join(str(m) for m in m0s)}}}，
{meta.get('elapsed_s')}s，{len(rows)} 行 DES）；
代码 <code>utils/pg_deadlock_recovery.py</code>、
<code>utils/dse_pg_recovery_pareto.py</code>、
<code>utils/gen_pg_recovery_pareto_plot.py</code>。
静态气泡位置按 HPCA'17 的放置规则（x&gt;0,y&gt;0 且 x%4≡y%4 或 (1,3) 或 (3,1)），
8×8 得 21 个、16×16 得 89 个（与论文一致），本 8×6 得
{meta['n_sb_nodes']} 个。</p>
"""


def main():
    data = json.loads(JSON_PATH.read_text())
    meta = data["meta"]
    rows = data["rows"]
    golden = meta["golden"]
    # Primary e2e is already budget-fault + VC≤2 (pg_e2e_pareto.json).
    e2e_html = e2e_section_html()
    single_html = single_router_section_html()
    recovery_html = recovery_section_html()
    from gen_pg_a2a_lambda_report import lambda_section_html
    lambda_html = lambda_section_html()

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
tr.ref td {{ background: #f6f7f9; color: #4b5563; }}
table.cap td.cap-ok {{ background: #f2faf5; }}
table.cap td.cap-bad {{ background: #fdf1ef; color: #922b21; }}
table.cap td.cap-warn {{ background: #fdf8ec; color: #8a6d1f; }}
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

{scheme_block("M0s — Super-turn（<code>super_turn</code>）", "east_first", '''
<p><b>类别：</b>A 类 · 转向限制 · <b>≤2 VC</b>（本轮默认推荐）。
不是固定路由，而是四个 Glass–Ni 最小转向模型上的自适应选择器。</p>
<p><b>四个最小模型</b>（每个禁 2 个转向 + 全部 180° 掉头）：</p>
<ul>
<li><code>east_first</code>：禁 N→E、S→E</li>
<li><code>west_first</code>：禁 N→W、S→W</li>
<li><code>north_last</code>：禁 E→N、W→N</li>
<li><code>south_last</code>：禁 E→S、W→S</li>
</ul>
<p><b>升级阶梯</b>（<code>gen_super_turn</code>，硬顶 2 VC）：</p>
<ol>
<li><b>1 VC</b>：四个模型各全局建一次表，取总跳数最少且验证通过的那个。</li>
<li><b>2 VC</b>：不行则试 6 种双模型组合（互补优先：east+west / north+south，再交叉）。
每个 <code>(s,d)</code> 独立选路径更短的那一层，端到端锁在该层 VC。</li>
<li><b>牺牲</b>：最好的 dual 仍漏 OD 对时，对漏掉端点做贪心 hit-set，
<strong>每轮强制退休 1 个节点</strong>，最多 8 轮后重建。
宁可牺牲也不开第 3、4 个 VC，把硅上 VC 预算钉在 2。</li>
</ol>
''' + qa3(
    '每个 OD 对先试 XY；XY 被故障打断或违反当前模型时，退到转向感知 BFS'
    '（按 (节点, 来向) 去重）。'
    '<b>四个模型的盲区互不重叠</b>——M0 East-first 的东向盲区，'
    '换到 west-first / north-last 层即可重新打开绕行方向。'
    '这是它相对单模型 M0 从预算目录 3/176 跳到大量零牺牲的全部原因。',
    '<b>构造性，且对残图单调。</b>2D mesh CDG 只有两条抽象环；'
    '每个最小模型从两条环各拆一个转向 ⇒ 层内无环。'
    '删链路只会减少转弯、不会创造转弯，故障残图上同样成立。'
    '两层用不相交 VC，跨层无依赖边。实现仍在 (channel, VC) 上硬校验。',
    '每 (s,d) 单一路径 + 端到端锁定同一 VC'
    '（<code>vc_of</code> 只看路径首尾）⇒ 保序。')
+ '''
<div class="faq">
<p><b class="q">会不会连通却建不出表（STRUCT）？</b><br/>
<strong>偶发。</strong>目录外扫描（§2.3）：≤2 故障 STRUCT=0；
混合多重故障抽样 1000 里仅 STRUCT 8 / disc 5。
连通残图上双 VC 转向集几乎总够用。</p>
<p><b class="q">「覆盖 X/N」是什么意思？</b><br/>
分母 = 预算故障目录场景数（quick N=44 或 full N=176）。
分子 = 经生成器 + <code>solve_scheme</code>（e2e 默认再经
<code>solve_scheme_fc</code>）后仍能产出合法无死锁保序表的场景数。
<strong>放宽牺牲后</strong>（§6.4）：M3 / M0s / M0s1 / M5h 在 quick 44 上均为
<b>44/44</b>。紧预算 / 少轮 forced 时 M0s1、M5h 会留下 INFEASIBLE——
那是预算不够，不是图论上不存在解。</p>
<p><b class="q">不可达时牺牲多少？</b></p>
<ul>
<li><b>M0s（m₀=1, quick 44）</b>：牺牲中位 1、最差 8；约半场景零牺牲；
VC 多为 2。</li>
<li><b>M0s1 全覆盖代价</b>：牺牲中位 20、最差 39（A 中位 26 / 最差 6）——
硬顶 1 VC 用节点换转向空间。</li>
<li><b>M5h 全覆盖代价</b>：牺牲中位 30、最差 40；其中 4 个场景靠整行/整列
（A=6–8）。</li>
</ul>
</div>
<p><b>端到端角色：</b>有限 VC（≤2）并发候选；与 BB UD（1VC 分批）的取舍见 §6。</p>
<p class="note">右图借用 M0 转向示意；M0s 在此基础上按 OD 对切换模型 / VC 层。
目录外 STRUCT/disc 见 §2.3；端到端选型见 §6.3。</p>
''')}

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
M3+LB 试图在合法集合内做负载感知换路，本 8×6 上中位收益几乎为零。
换根搜索（M3′）与分批屏障（Batch-Barrier）见下两节。</p>
<p><b>端到端角色：</b>库存「度最大根」基线。同面积上通常被 <b>M3′</b> 支配；
分批屏障计入同步后是否仍优，见 §6。</p>
''', extra_key="updown_aux")}

{scheme_block("M3′ — Up*/Down* best-root（<code>updown_best_root</code>）", "updown", '''
<p><b>类别：</b>A 类 · 与 M3 同构的转向限制 · <b>1 VC</b>。</p>
<p><b>思想：</b>Up*/Down* 的唯一自由参数是生成树根（高度坐标系原点）。
库存 M3 取「度最大」启发式；M3′ 在<strong>全部存活 router</strong>上穷举根，
对每张候选全表算 alltoall 最大链路负载，取
<code>(max_link_load, total_hops, root_id)</code> 字典序最小者。
死锁自由与避障论证与 M3 完全相同——只是换了一套标号。</p>
<p><b>算法步骤：</b></p>
<ol>
<li>对每个候选 root ∈ 存活邻接表，跑与 M3 相同的约束 BFS 建全表；CDG 校验失败则丢弃。</li>
<li>在合法表上统计有向边 alltoall 对数负载，记录 <code>max_link_load</code> 与总跳数。</li>
<li>选最优 root；运行时仍是<strong>单张静态路径表</strong>（每对唯一路径）。</li>
</ol>
<p><b>与本网格实测（预算故障 44 场景）：</b>相对库存 M3，
最差端到端 m₀=1：<b>893→790 ns</b>（−11.6%）；m₀=13：<b>7678→7573 ns</b>（−1.4%）。
中位收益更大（负载更均衡），但最差场景仍受「单高度函数」瓶颈限制。</p>
<p><b>端到端角色：</b>「仍坚持单表、单波 alltoall」时的最佳 1VC 点。
分批屏障（计入批间同步）是否更快见 §6。</p>
''' + qa3(
    '与 M3 相同：只在 <code>route_adj</code> 上约束 BFS；根搜索不改变存活图。',
    '每张候选表仍禁止 down→up，按构造 1VC 无死锁；实现仍硬校验。',
    '选定根后全表唯一路径，与 M3 相同。'))}

{scheme_block("M3+LB — Up*/Down* + 负载均衡（<code>updown_lb</code>）", "updown_lb", '''
<p><b>在 M3 路径表上后处理：</b>统计有向边 alltoall 对数负载；每轮重排途经最热边的若干 (s,d)，
用负载感知 Dijkstra（边权 ≈ 1+负载）换路；每轮后整表再校验 CDG。失败则回退。</p>
<p><b>特征：</b>目标是压低最大链路负载；在本 8×6 上对 median makespan 改善通常很小
（Up*/Down* 合法路径集合较窄）。相对「换根」几乎无额外收益——合法集合已被根钉死。</p>
''' + qa3(
    '与 M3 完全相同——换路仍在同一张存活图、同一套合法转向集合内进行，'
    '不会新引入穿越故障的路径。',
    '<b>靠「换完再验」而不是靠构造。</b>负载感知 Dijkstra 可能选出破坏原有相位单调性的路径，'
    '所以每一轮结束都对<strong>整表</strong>重跑 <code>validate_routing</code>；'
    '一旦 CDG 出环，立刻整体回退到上一个已验证的 best 并停止迭代。',
    'LB 是<strong>离线迭代</strong>：收敛后每对仍只有一条固定路径，'
    '运行时不会按实时负载改路 ⇒ 保序不受影响。'))}

{scheme_block("Batch-Barrier — 分批屏障（<code>bb_*</code>）", "updown", '''
<p><b>类别：</b>调度层 · 运行时物理 <b>1 VC</b> ·
<strong>不是</strong>链路 TDM，也<strong>不是</strong>「把一张会死锁的整表硬切开」。
统一时序：
<code>T_comm = Σ<sub>i</sub> makespan(batch<sub>i</sub>) + (K−1)·T_sync</code>，
其中 <code>T_sync = 2·radius<sub>wire</sub></code>：残图上选最小化
<strong>线延迟离心率</strong>的中心，gather→broadcast；
距离用与 DES 相同的 <code>link_lat</code>（横向 H=7、纵向 V=9），Dijkstra 最短路。
旧 hop 模型 <code>2·radius<sub>hops</sub></code>≈14 cy 低估同步税约一个数量级。
每批 DES 已含排空。把多 VC 母方案串成 1VC 必然更慢（层无法再并发）——对照的意义是
<strong>省面积后相对其他 1VC 方案是否仍优</strong>，不是和母方案比速度。</p>

<p><b>何谓「按 OD 整层切开」：</b>
母方案若满足——每个 OD 对 <code>(s,d)</code> 整条路径锁在<strong>同一层</strong>
（同一 VC / 同一路由表），从不中途换层——则层号构成 OD 集合的一个划分：
<code>batch_k = {全部 which[(s,d)]=k 的 OD}</code>。
每批是完整 OD 子集，批内路径自洽、可单独在 1VC 上验证 CDG。
这就是「按 OD 整层切开 → 一层一批」。Dual-UD、LASH（整路径锁层）、多根 UD 表分配都满足。
<strong>不满足</strong>的例子：Stripe / Virtual / LASH-TOR——VC 随跳数在路径上递增或切换，
层标签标在<strong>边/跳</strong>上而非 OD 上；一个 OD 会同时出现在多层，无法拆成「整 OD 进一批」。
本轮因此不做它们的 BB 变体。</p>

<p><b>两类来源：</b></p>
<table>
<thead><tr>
<th class="l">BB 方案</th><th>物理多 VC 母方案？</th>
<th>批的划分从哪来</th><th>批数 K</th><th class="l">含义</th>
</tr></thead>
<tbody>
<tr>
<td class="l"><b>BB UD×2 / ×3 / policy</b></td>
<td><b>无</b>（本身即 1VC）</td>
<td>多张独立 Up*/Down* 表（不同根）；OD 分到各表</td>
<td>2 / 3 / 按 m₀</td>
<td class="l">多高度坐标调度分批，不是拆 VC</td>
</tr>
<tr>
<td class="l"><b>BB DualUD→1VC</b></td>
<td><b>M9 Dual Up*/Down*</b>（2 VC）</td>
<td>按 OD 切开：VC0=UD 层、VC1=DU 层</td>
<td>= 2</td>
<td class="l">母方案 OD 层划分 → 时间串行</td>
</tr>
<tr>
<td class="l"><b>BB LASH→1VC</b></td>
<td><b>M6 LASH</b>（常 2–3 VC）</td>
<td>按 OD 切开：每个 LASH 层一批</td>
<td>= LASH 层数</td>
<td class="l">同上</td>
</tr>
</tbody>
</table>

<h4>BB UD×2（<code>bb_ud_bal2</code>）</h4>
<p><b>母方案：</b>无。路由硬件同 M3/M3′（单 VC Up*/Down*）；
仅离线准备 R=2 张表并在运行时分两批注入。</p>
<ol>
<li>存活图上枚举各根 UD 全表，按 <code>max_link_load</code> 取最优 2 张
（两套高度函数）。每张表单独 1VC 无死锁。</li>
<li>全部 OD 排序后 round-robin：对优先表 i，无路则顺延到其他表。</li>
<li>批次 0 注入表 0 的 OD → 图中心 barrier → 批次 1。</li>
</ol>
<p>单表 UD 合法路径易挤在「脊」上；两套标号给出不同绕行走廊，批内峰值负载更低。
代价是两批 makespan 相加 + 一次 <code>T_sync</code>。
计入 <code>T_sync≈110 cy</code> 后，本网格最差端到端约 <b>828 / 7202 ns</b>（m₀=1 / 13）。
轻载荷上已慢于单表 M3′（790 ns）——同步税吃掉分批收益。</p>

<h4>BB UD×3（<code>bb_ud_bal3</code>）</h4>
<p><b>母方案：</b>同 ×2，R=3（两次 barrier，<code>sync_total≈220 cy</code>）。
轻载荷更不划算（最差约 919 &gt; ×2 的 828）；
重载荷分摊拥塞仍值（最差约 <b>7061 ns</b> &lt; ×2 的 7202）。</p>

<h4>BB UD policy（<code>bb_ud_policy</code>）</h4>
<p><b>母方案：</b>无。部署规则：<strong>m₀=1 → ×2；m₀=13 → ×3</strong>（编译期已知载荷，
无 per-scene 启发式）。最差约 <b>828 / 7061 ns</b>。
重载荷时仍是 1VC Pareto 点；轻载荷推荐改用 M3′ / Super-turn（见 §6）。</p>

<h4>BB DualUD→1VC（<code>bb_dual</code>）</h4>
<p><b>母方案：M9 Dual Up*/Down*</b>——物理 2 VC，VC0 跑 UD、VC1 跑 DU；
每 <code>(s,d)</code> 选更短一层并<strong>整路径锁在该 VC</strong>（满足按 OD 整层切开）。</p>
<p><b>分批：</b><code>batch_k = {which[(s,d)]=k}</code>，k∈{0,1}；
每批单物理 VC 只跑该层路径表，批间图中心 barrier。面积按 1VC。
与并发 Dual 比必然更慢（预期）；与同面积的 BB UD 比，多根分批通常更均衡
（最差约 960 / 7535 ns）。</p>

<h4>BB LASH→1VC（<code>bb_lash</code>）</h4>
<p><b>母方案：M6 LASH</b>——最短路贪心装入最少无环 CDG 层，
每对<strong>整路径锁一层</strong>（可按 OD 切开；层数=物理 VC，本目录常 2–3）。</p>
<p><b>分批：</b>一层一批，1VC 串行 + barrier。相对并发 LASH 更慢是预期；
相对 BB UD，层负载更不均、长尾层拖垮总和（最差约 1048 / 8663 ns）。</p>

<p><b>端到端角色：</b>线延迟同步税下，<b>重载荷推 BB UD policy</b>；轻载荷分批不划算。
BB Dual / BB LASH 仅作「OD 可切多层 → 1VC」对照。</p>
''' + qa3(
    'BB UD：每批是一张存活图上的 UD 表；BB Dual/LASH：每批是母方案某一 VC 层上的完整 OD 子集。',
    '批内 CDG 无环（1VC）；批间屏障保证排空后再注入——不把多批依赖叠进同一 CDG。',
    '批内每对唯一路径；批间由屏障分隔，不要求单次全局同时注入。'))}

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
<p><b>端到端角色：</b><b>本轮不参与选型</b>（需 5–9 条物理 VC）。
描述保留；延迟极限好但面积贵，不符合「少物理 VC」约束。</p>
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
<p><b>端到端角色：</b><b>本轮不参与选型</b>（需 2 条物理 VC 绕路语义）。
描述保留；若硬件已按多 VC 绕路做死，历史扫中延迟很好，但不进本轮 Pareto。</p>
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
<tr><td>A</td><td class="l">M3 Up*/Down*</td><td class="l">树标号 + 先上后下</td><td>1</td><td class="l">路由表/逻辑</td><td>通常 0</td><td class="l">库存度最大根；连通即达（§2.3）</td></tr>
<tr><td>A</td><td class="l">M3′ best-root</td><td class="l">同 UD，穷举根降负载</td><td>1</td><td class="l">离线根搜索 + 单表</td><td>通常 0</td><td class="l">单表 1VC 优选</td></tr>
<tr><td>A</td><td class="l">Batch-Barrier</td><td class="l">无死锁子集串行 + 图中心 barrier（wire）</td><td>1</td><td class="l">UD×R=多表调度；Dual/LASH=多 VC 串行化</td><td>通常 0</td><td class="l">重载荷推 BB UD policy；轻载荷不如 M3′</td></tr>
<tr><td>A</td><td class="l">M3+LB / M4 / M4+LB</td><td class="l">转向限制 ± LB</td><td>1</td><td class="l">同左</td><td>中～高</td><td class="l">M4 目录外 STRUCT 极常见（§2.3）</td></tr>
<tr><td>B</td><td class="l">M5 真 f-ring</td><td class="l">矩形块 + XY 环绕，相位×方向</td><td>4</td><td class="l">4 VC + 绕障</td><td>节点洞 0；链路 1–4</td><td class="l">描述保留；<b>不进</b>本轮 e2e（VC&gt;2）</td></tr>
<tr><td>B</td><td class="l">M5h half-ring</td><td class="l">半环绕行 + X/Y 两 VC</td><td>2</td><td class="l">2 VC + 半环</td><td>半环受阻时升</td><td class="l">e2e 评测（VC≤2）</td></tr>
<tr><td>B</td><td class="l">M6 LASH</td><td class="l">最短路 + 贪心装层</td><td><b>1–2</b></td><td class="l">少 VC + 离线表</td><td>通常仅孤立点</td><td class="l">VC 性价比</td></tr>
<tr><td>B</td><td class="l">M6b LASH-TOR</td><td class="l">LASH + 中途升层</td><td>1–2</td><td class="l">同 LASH</td><td>同 LASH</td><td class="l">再压层数（收益有限）</td></tr>
<tr><td>B</td><td class="l">M7 Stripe</td><td class="l">最短/XY + 跨带 VC+1</td><td>5–9</td><td class="l">多物理 VC</td><td>通常仅孤立点</td><td class="l">描述保留；<b>不进</b>本轮选型</td></tr>
<tr><td>B</td><td class="l">M9 Dual UD</td><td class="l">UD / DU 双层，按对选</td><td>2</td><td class="l">2 VC + 双规则</td><td>通常 0</td><td class="l">e2e 评测（≤2 VC）</td></tr>
<tr><td>B</td><td class="l">M10 Virtual mesh</td><td class="l">逻辑 XY + 物理绕路</td><td>2</td><td class="l">2 VC + 绕路表</td><td>链路友好</td><td class="l">描述保留；<b>不进</b>本轮选型</td></tr>
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
<p class="note"><b>排除规则：</b>第 3–4 节 makespan 主表不含
{esc(excluded_labels)}（三性质/覆盖不足）。
本轮 e2e（§6）评 M3 族 / Super-turn / Dual-UD / LASH 与分批屏障
<code>bb_*</code>；<b>不含</b>需多物理 VC 的 Stripe / Virtual / f-ring（§2 描述保留）。
被排除的「牺牲换 makespan」方案（M0/M1/M2/M4）在 §6 仍可出现。</p>

<h3>2.6 方案可行性与牺牲代价（m=1, Q=19，含被排除方案）</h3>
{feas_html}

<h2>3. 每场景最优方案选择</h2>
<p class="note">故障集 = <b>预算模型</b>（≤4R+≤8L，
<code>results/pg_faults_budget_8x6.json</code> /
<code>results/pg_e2e_pareto.json</code>）。
旧 link_/node_ corner·edge·center 目录<b>不再显示</b>。
判据：<b>先牺牲节点数，再 alltoall makespan</b>（同牺牲 ⇒ A 相同才可比）。
评测方案 = e2e 入选集（≤3 VC + <code>bb_*</code>；不含 Stripe/Virtual/f-ring，
亦不含 {esc(excluded_labels)}）。
「Pareto 备选」= 非受支配的 (牺牲, alltoall) 组合。
载荷按 e2e 强扩展标定（m<sub>0</sub>∈{{1,13}}）；语义 = dead。</p>
{optimal_tables_html}

<h2>4. alltoall 矩阵（预算故障 · 同 §3 方案集）</h2>
<p class="note">单元格主行：alltoall makespan（cy）；副行：牺牲 | A。
场景与 §3 相同（预算故障）；INF = 该方案在该场景建不出表 / 未覆盖。
不含三性质排除方案与 Stripe/Virtual；含 Dual/LASH 与分批屏障。</p>
{scheme_matrices_html}

<h2>5. Q 敏感度（子集，m=1, dead）</h2>
{q_table or '<p class="note">无 Q 敏感度数据</p>'}

{e2e_html}

{single_html}

{recovery_html}

{lambda_html}

<h2>8. 指标定义</h2>
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

<h2>9. 主要观察</h2>
<ol>
<li><b>故障模型与评测范围：</b>主评估 = 预算故障（≤4R+≤8L）；
e2e 评有限 VC（Super-turn/Dual/LASH）与分批屏障 1VC；
<strong>不考虑</strong>多物理 VC 的 Stripe / Virtual / f-ring。</li>

<li><b>端到端前沿：</b>见开篇与 §6。中心 hub 同步下 BB UD 常占 VC1 左端；
轻载荷可加 Super-turn。M5h / M0s1 进不了前沿。</li>

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

<li><b>M3+LB 几乎无效</b>——同合法集合内局部换路不如换根（M3′）或上多 VC。</li>

<li><b>Q 与 VC 面积：</b>Q=4 时 Up*/Down* 可慢数倍；VC 线性放大每端口缓冲。</li>

<li><b>通信占端到端 70–86%</b>。能分批优先 BB UD；否则 M3′ / Super-turn。</li>

<li><b>死锁恢复类（§7，Static Bubble / SPIN / SWAP × 四种路由）：</b>
换掉「先天无环」这个前提后，牺牲维度上完胜（零牺牲、1 VC、面积 +0.6%–15%），
三者在全部场景都解开了死锁。<b>但成败取决于路由选择</b>：
恢复开销 ∝ CDG 环数 × 绕环一趟的拍数，而不是 ∝ 峰值负载，所以
①「负载最优但无转向模型」（R1）最慢；
②「Super-turn 转向集合压到 1 VC 赌小概率死锁」（R3）也不行——
它的无死锁性来自 VC 分层而非转向集合，环上通道占比反而比基线 XY 更高；
③ 最优避障路由 M3′ <b>天然无死锁</b>，叠恢复机制净收益为零（检测器一次没响）。
机制排序恒为 SB ≪ SPIN &lt; SWAP；SWAP 即使在无环路由上也破坏保序。</li>
</ol>
</body></html>
"""
    HTML_PATH.write_text(doc)
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
