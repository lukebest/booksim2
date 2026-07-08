#!/usr/bin/env python3
"""Render the exact multitree_children(s, mx, my) delivery structure (from
allgather_fast_sim.py) for one source on a small mesh, as an SVG diagram.
Not an illustration -- every edge drawn is read directly from the real
topology-builder function, so it is guaranteed to match the code.

Usage: python3 gen_multitree_diagram.py [--mx 4] [--my 4] [--s 5]
"""
import argparse
from pathlib import Path

import allgather_fast_sim as F

ROOT = Path(__file__).resolve().parents[1]


def build_svg(mx, my, s):
    cell = 70
    pad = 50
    W = max(pad * 2 + (mx - 1) * cell + 30, 760)
    H = pad * 2 + (my - 1) * cell + 60

    def pt(node):
        x, y = F.coord(node, mx)
        return pad + x * cell, pad + y * cell

    children = F.multitree_children(s, mx, my)
    sx, sy = F.coord(s, mx)

    parts = [f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="background:#fff;font-family:system-ui,sans-serif">']

    # faint full mesh grid links for context
    for y in range(my):
        for x in range(mx):
            n = F.nid(x, y, mx)
            if x + 1 < mx:
                x1, y1 = pt(n)
                x2, y2 = pt(F.nid(x + 1, y, mx))
                parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                             f'stroke="#e2e8f0" stroke-width="2"/>')
            if y + 1 < my:
                x1, y1 = pt(n)
                x2, y2 = pt(F.nid(x, y + 1, mx))
                parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                             f'stroke="#e2e8f0" stroke-width="2"/>')

    parts.append('<defs>'
                  '<marker id="ah" markerWidth="8" markerHeight="8" refX="6" refY="3" '
                  'orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#0f172a"/></marker>'
                  '<marker id="ahs" markerWidth="8" markerHeight="8" refX="6" refY="3" '
                  'orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#2563eb"/></marker>'
                  '</defs>')

    # multitree edges: spine (same row as source) in blue, ribs (vertical) in dark
    for p, cs in children.items():
        px, py = pt(p)
        _, prow = F.coord(p, mx)
        for c in cs:
            cx, cy = pt(c)
            same_row = (prow == sy)
            color = "#2563eb" if same_row else "#0f172a"
            marker = "ahs" if same_row else "ah"
            dx, dy = cx - px, cy - py
            dist = (dx**2 + dy**2) ** 0.5
            ux, uy = dx / dist, dy / dist
            x1, y1 = px + ux * 16, py + uy * 16
            x2, y2 = cx - ux * 16, cy - uy * 16
            parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                         f'stroke="{color}" stroke-width="2.5" marker-end="url(#{marker})"/>')

    for y in range(my):
        for x in range(mx):
            n = F.nid(x, y, mx)
            cx, cy = pt(n)
            is_src = (n == s)
            is_spine = (y == sy)
            fill = "#dc2626" if is_src else ("#2563eb" if is_spine else "#334155")
            r = 16 if is_src else 13
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"/>')
            parts.append(f'<text x="{cx}" y="{cy+4}" text-anchor="middle" font-size="11" '
                         f'fill="#fff" font-weight="700">{n}</text>')

    parts.append(f'<text x="{pad}" y="{H-32}" font-size="13" fill="#64748b">'
                 f'source s={s} at ({sx},{sy})；蓝色=source 所在行"脊"上的双向水平转发</text>')
    parts.append(f'<text x="{pad}" y="{H-12}" font-size="13" fill="#64748b">'
                 f'黑色=每一列以脊节点为根，向上/向下的独立双向垂直转发链</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mx", type=int, default=4)
    ap.add_argument("--my", type=int, default=4)
    ap.add_argument("--s", type=int, default=None, help="source node id (default: center-ish)")
    ap.add_argument("--out", default=str(ROOT / "results" / "multitree_diagram.html"))
    args = ap.parse_args()

    s = args.s if args.s is not None else F.nid(args.mx // 2 - (1 if args.mx > 1 else 0),
                                                  args.my // 2 - (1 if args.my > 1 else 0), args.mx)
    svg = build_svg(args.mx, args.my, s)
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/>
<title>multitree_children diagram</title></head>
<body style="padding:24px;font-family:system-ui,sans-serif">
<h2>multitree_children(s={s}, mx={args.mx}, my={args.my}) —— 由代码直接生成，非示意画图</h2>
{svg}
</body></html>"""
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
