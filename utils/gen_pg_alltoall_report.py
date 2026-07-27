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
    "fault_ring_vc": "M5 Fault-ring 2VC",
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


def _defs_arrow(uid="arr"):
    return (f'<defs><marker id="{uid}" markerWidth="7" markerHeight="7" '
            f'refX="6" refY="3.5" orient="auto">'
            f'<polygon points="0 0, 7 3.5, 0 7" fill="#27ae60"/></marker></defs>')


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
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a3")]
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
        parts.append(_edge(C[path[i]], C[path[i + 1]], "#2980b9", 3.2, marker="a3"))
    for i in range(2, 4):
        parts.append(_edge(C[path[i]], C[path[i + 1]], "#e67e22", 3.2, marker="a3"))
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

    # ---- M5 Fault-ring 2VC ----
    C, W, H = _mini_xy(4, 3, pad=28, gap=36)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}">', _defs_arrow("a5")]
    fault = {(1, 1), (2, 1)}
    dlx = (C[(1, 0)][0] + C[(2, 0)][0]) / 2
    parts.append(
        f'<line x1="{dlx}" y1="{C[(0, 2)][1] - 20}" x2="{dlx}" '
        f'y2="{C[(0, 0)][1] + 20}" stroke="#8e44ad" stroke-width="2" '
        f'stroke-dasharray="4,3"/>')
    parts.append(
        f'<text x="{dlx + 4}" y="{C[(0, 2)][1] - 8}" font-size="10" '
        f'fill="#8e44ad">dateline</text>')
    for r in range(3):
        for c in range(3):
            if (c, r) in fault or (c + 1, r) in fault:
                continue
            parts.append(_edge(C[(c, r)], C[(c + 1, r)], "#bdc3c7", 1.5))
        for c in range(4):
            if r < 2 and (c, r) not in fault and (c, r + 1) not in fault:
                parts.append(_edge(C[(c, r)], C[(c, r + 1)], "#bdc3c7", 1.5))
    # around the hole along the top, then down
    path = [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2), (3, 2), (3, 1), (3, 0)]
    crossed = False
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        hop_crosses = a[0] <= 1 and b[0] >= 2
        if hop_crosses or crossed:
            col = "#e74c3c"
        else:
            col = "#3498db"
        parts.append(_edge(C[a], C[b], col, 3, marker="a5"))
        if hop_crosses:
            crossed = True
    for r in range(3):
        for c in range(4):
            if (c, r) in fault:
                parts.append(_node(*C[(c, r)], fill="#c0392b", label="洞"))
            elif (c, r) == (0, 0):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="S"))
            elif (c, r) == (3, 0):
                parts.append(_node(*C[(c, r)], fill="#27ae60", label="D"))
            else:
                parts.append(_node(*C[(c, r)]))
    parts.append(
        f'<text x="8" y="14" font-size="10" fill="#3498db">蓝=VC0</text>'
        f'<text x="70" y="14" font-size="10" fill="#e74c3c">'
        f'红=VC1（过线翻转）</text>')
    parts.append(_caption(W, H, "禁止穿洞，绕行；过 dateline 换 VC"))
    parts.append("</svg>")
    out["fault_ring_vc"] = "".join(parts)

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

    def scheme_block(title_html: str, key: str, body_html: str) -> str:
        fig = diagrams.get(key, "")
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

    # Summary: best scheme per (scenario, semantics, m) at Q=19
    summary_rows = []
    for scen in scenarios:
        for sem in ("dead", "transit"):
            for m in (1, 5):
                cands = [r for r in primary
                         if r["scenario"] == scen["name"]
                         and r["semantics"] == sem
                         and r["m"] == m and r["Q"] == 19
                         and r.get("makespan") is not None]
                if not cands:
                    continue
                best = min(cands, key=lambda r: (
                    r["makespan"], r["n_sacrificed"]))
                summary_rows.append(best)

    def summary_table(m: int) -> str:
        head = ("<tr><th>场景</th><th>语义</th><th>最佳方案</th>"
                "<th>makespan</th><th>golden</th><th>raw slowdown</th>"
                "<th>irreg. penalty</th><th>牺牲</th><th>A</th></tr>")
        body = []
        for r in summary_rows:
            if r["m"] != m:
                continue
            body.append(
                "<tr>"
                f"<td class='l'>{esc(r['scenario'])}</td>"
                f"<td>{esc(r['semantics'])}</td>"
                f"<td class='l'>{esc(SCHEME_LABELS.get(r['scheme'], r['scheme']))}</td>"
                f"<td>{r['makespan']}</td>"
                f"<td>{r['golden_makespan']}</td>"
                f"<td>{pct(r.get('raw_slowdown'))}</td>"
                f"<td>{pct(r.get('irregularity_penalty'))}</td>"
                f"<td>{r['n_sacrificed']}</td>"
                f"<td>{r['n_compute_used']}</td>"
                "</tr>"
            )
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

{scheme_block("M5 — Fault-ring + 2 VC（<code>fault_ring_vc</code>）", "fault_ring_vc", '''
<p><b>思想：</b>强制绕开故障矩形（即使 transit 下洞内 router 仍活着也不穿洞），
在穿孔图上用 Up*/Down* 选路，并用 2 VC + 垂线 dateline 加强隔离。</p>
<p><b>做法：</b>(1) 节点故障：取故障 bbox，内部节点从路由图剔除；纯链路故障无 bbox，
只在已断链图上路由；(2) 剩余图上跑 Up*/Down*；(3) dateline = 故障中心列
（链路故障用 mx//2）；路径每水平穿过该列一次，VC 奇偶翻转。DES 每端口按 VC 分队列与 credit。</p>
<p><b>与 M3 差别：</b>transit 节点洞上 M3 可穿洞转发，M5 禁止；M5 多 2 VC 硬件假设。</p>
<p><b>特征：</b>零牺牲场景多，makespan 常接近 M3；大洞绕行时可能略差于可穿洞的 transit-M3。</p>
''')}

<table>
<thead><tr><th>方案</th><th>路由本质</th><th>硬件改动</th><th>典型牺牲</th><th>适用意图</th></tr></thead>
<tbody>
<tr><td class="l">M1 XY</td><td class="l">严格先 X 后 Y</td><td class="l">最小（原 XY）</td><td>高</td><td class="l">量化不改路由的代价</td></tr>
<tr><td class="l">M2 Rect-XY</td><td class="l">裁矩形 + XY</td><td class="l">最小</td><td>固定偏高</td><td class="l">规整化、可预测</td></tr>
<tr><td class="l">M3 Up*/Down*</td><td class="l">树标号 + 先上后下</td><td class="l">路由表/逻辑</td><td>通常 0</td><td class="l"><b>保规模主方案</b></td></tr>
<tr><td class="l">M3+LB</td><td class="l">M3 + 热点重路由</td><td class="l">同 M3</td><td>同 M3</td><td class="l">压最大链路负载</td></tr>
<tr><td class="l">M4 Segment</td><td class="l">列带奇偶转向</td><td class="l">转向限制</td><td>中高</td><td class="l">折中绕路能力</td></tr>
<tr><td class="l">M4+LB</td><td class="l">M4 + LB</td><td class="l">同 M4</td><td>同 M4</td><td class="l">同左</td></tr>
<tr><td class="l">M5 Fault-ring 2VC</td><td class="l">禁穿洞 + Up*/Down* + dateline VC</td><td class="l">2 VC + 绕障表</td><td>通常 0</td><td class="l">强制隔离故障区</td></tr>
</tbody>
</table>

<h3>2.1 方案可行性与牺牲代价（m=1, Q=19）</h3>
{feas_html}

<h2>3. 每场景最佳方案（按 makespan，并列取少牺牲）</h2>
<h3>3.1 m=1 flit</h3>
{summary_table(1)}
<h3>3.2 m=5 flit（同源同目的保序 wormhole）</h3>
{summary_table(5)}

<h2>4. 全方案 makespan 矩阵</h2>
<p class="note">单元格：makespan；副行：raw slowdown | 牺牲节点数。
INF = 在牺牲预算内仍无法得到无死锁保序路由，或 DES 死锁。</p>
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
<li><code>raw_slowdown = mk / mk_golden − 1</code>（与 ring_report 同口径）</li>
<li><code>irregularity_penalty = mk / LB_same_A − 1</code>（同存活集合、无死锁约束参考负载下界）</li>
<li><code>sacrifice_cost = n_sacrificed / n_originally_good</code></li>
</ul>

<h2>7. 主要观察</h2>
<ul>
<li>Up*/Down*（M3）在 link/node 故障下通常以 <b>零牺牲</b> 给出无死锁保序路由，是保住计算规模时的主推荐。</li>
<li>XY / Rect-XY 中位牺牲 24–28 节点，raw slowdown 可到 <b>−71%</b>（参与者变少），但 irregularity_penalty 仍约 +19%——读劣化请看后者。</li>
<li>Fault-ring 2VC 与 Up*/Down* 接近（绕开故障 bbox + dateline VC），零牺牲。</li>
<li>transit 下 XY 约半数场景可零牺牲；Up*/Down* 中位略优于 dead。</li>
<li>Q=4 时 Up*/Down* 可慢 3–4×（H/V 长线需要 Q≈19）；小矩形 XY 对 Q 不敏感。</li>
<li>全部 DES 行 <code>ordered_ok=True</code>；m=5 带宽项放大后 Up*/Down* raw ≈ +150%。</li>
</ul>
</body></html>
"""
    HTML_PATH.write_text(doc)
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
