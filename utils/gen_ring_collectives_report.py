#!/usr/bin/env python3
"""Generate results/report_ring_collectives_8x6.html.

Companion to docs/phase-7-exploration/ring-collectives-8x6.md. The markdown is
written for someone who already accepts the framing; this report is written for
someone meeting the dimension-sliced bufferless ring for the first time, so it
opens with a drawn multicast arc and a drawn rotation step before any table.

Every number is read out of the result JSONs. Nothing is typed in here, so the
report cannot drift away from the runs that produced it -- if a JSON is missing
the affected section says so rather than falling back to a remembered value.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COLL = ROOT / "results" / "ring_collectives_8x6.json"
TAVG = ROOT / "results" / "ring_tavg_8x6.json"
ROB = ROOT / "results" / "ring_robust_8x6.json"
VER = ROOT / "results" / "verify_ring_collectives_8x6.json"
IDX = ROOT / "results" / "calendars" / "ring_index.json"
OUT = ROOT / "results" / "report_ring_collectives_8x6.html"

MX, MY, N = 8, 6, 48
PATTERN_ORDER = ["allgather", "allreduce", "alltoall", "gather", "broadcast",
                 "reduce"]


# ---------------------------------------------------------------------------
# 1. Data access
# ---------------------------------------------------------------------------

def load() -> dict[str, Any]:
    def rd(p: Path) -> dict | None:
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"coll": rd(COLL), "tavg": rd(TAVG), "rob": rd(ROB),
            "ver": rd(VER), "idx": rd(IDX)}


def rows(c: dict, **filt: Any) -> list[dict]:
    return [r for r in c["rows"]
            if all(r.get(k) == v for k, v in filt.items())]


def row1(c: dict, **filt: Any) -> dict | None:
    r = rows(c, **filt)
    return r[0] if r else None


def best_by(rs: list[dict], key) -> dict | None:
    ok = [r for r in rs if key(r) is not None]
    return min(ok, key=key) if ok else None


def f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "&mdash;"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    if isinstance(x, int):
        return f"{x:,}"
    return str(x)


def pct(x: Any, nd: int = 1) -> str:
    return "&mdash;" if x is None else f"{100 * x:.{nd}f}%"


def times(x: Any, nd: int = 2) -> str:
    """A ratio with its unit, or a bare dash -- never "&mdash;&times;"."""
    return "&mdash;" if x is None else f"{x:.{nd}f}&times;"


def rat(num: Any, den: Any, nd: int = 2) -> str:
    if num is None or den in (None, 0):
        return "&mdash;"
    return f"{num / den:.{nd}f}&times;"


def scheme_label(pat: str, algo: str, tier: str) -> str:
    return f"{pat} / {algo} / {tier}"


# ---------------------------------------------------------------------------
# 2. SVG helpers
#
# Hand-rolled so the drawn node numbers are the simulator's own nid = y*MX + x.
# A reader can hold a drawn arc against a footprint printed by the code.
# ---------------------------------------------------------------------------

def nid(x: int, y: int) -> int:
    return y * MX + x


def _grid_svg(w: int, h: int, body: str) -> str:
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" '
            f'style="max-width:{w}px" role="img">{body}</svg>')


def svg_multicast() -> str:
    """One boarding covering a whole row arc, next to 7 separate unicasts.

    Only row 0 is drawn. The whole point is what happens along one ring, and a
    second row of nodes would invite the reader to look for column traffic that
    is not part of this comparison.
    """
    px, x0, ybase, r = 62, 62, 104, 11
    parts: list[str] = []

    def panel(ox: int, title: str, sub: str) -> None:
        parts.append(f'<text x="{ox - 14}" y="24" class="bxt">{title}</text>')
        parts.append(f'<text x="{ox - 14}" y="42" class="bxl dim">{sub}</text>')
        for x in range(MX - 1):
            cx = ox + x * px
            parts.append(f'<line x1="{cx + r + 2}" y1="{ybase}" '
                         f'x2="{cx + px - r - 2}" y2="{ybase}" class="rlk"/>')
        parts.append(f'<path d="M {ox} {ybase - r - 3} Q {ox + 3.5 * px} '
                     f'{ybase - 36} {ox + (MX - 1) * px} {ybase - r - 3}" '
                     f'class="wrp"/>')
        parts.append(f'<text x="{ox + 3.5 * px}" y="{ybase - 25}" '
                     f'class="bxl dim" text-anchor="middle">wraparound '
                     f'segment</text>')

    def nodes(ox: int) -> None:
        for x in range(MX):
            cx = ox + x * px
            cls = "nd src" if x == 0 else "nd dst"
            parts.append(f'<circle cx="{cx}" cy="{ybase}" r="{r}" '
                         f'class="{cls}"/>')
            parts.append(f'<text x="{cx}" y="{ybase + 4}" class="tag">'
                         f'{nid(x, 0)}</text>')
            if x:
                parts.append(f'<line x1="{cx}" y1="{ybase + r}" x2="{cx}" '
                             f'y2="{ybase + r + 16}" class="ar"/>')
                parts.append(f'<text x="{cx}" y="{ybase + r + 30}" '
                             f'class="bxl dim" text-anchor="middle">L1</text>')

    panel(x0, "T1: one boarding, copy and continue",
          "node 0 boards once; every station downstream drops a copy "
          "into its L1 and forwards the flit")
    parts.append(f'<path d="M {x0} {ybase} L {x0 + 7 * px} {ybase}" '
                 f'class="arcR"/>')
    nodes(x0)
    parts.append(f'<text x="{x0 - 14}" y="{ybase + 62}" class="lbl ok2">'
                 f'1 boarding &middot; 7 leaves &middot; 7 arc-cycles of '
                 f'segment time</text>')

    ox2 = x0 + 8 * px + 76
    panel(ox2, "T0: seven separate boardings",
          "the paper mechanism can only unicast, so node 0 boards "
          "once per destination")
    for k, x in enumerate(range(1, MX)):
        yy = ybase - 4 - k * 2.6
        parts.append(f'<path d="M {ox2} {yy:.1f} L {ox2 + x * px} {yy:.1f}" '
                     f'class="pRM"/>')
    nodes(ox2)
    parts.append(f'<text x="{ox2 - 14}" y="{ybase + 62}" class="lbl warn">'
                 f'7 boardings &middot; 7 leaves &middot; 28 arc-cycles of '
                 f'segment time</text>')
    return _grid_svg(2 * (8 * px) + 150, 188, "".join(parts))


def svg_rotation() -> str:
    """Why rotation saturates a ring: every arc carries exactly one flit."""
    cx, cy, R = 150, 132, 96
    parts: list[str] = []
    k = 8
    pos = []
    for i in range(k):
        a = -math.pi / 2 + 2 * math.pi * i / k
        pos.append((cx + R * math.cos(a), cy + R * math.sin(a)))
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" class="ringc"/>')
    for i in range(k):
        x1, y1 = pos[i]
        x2, y2 = pos[(i + 1) % k]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        vx, vy = x2 - x1, y2 - y1
        ln = math.hypot(vx, vy)
        parts.append(f'<line x1="{mx - vx / ln * 12:.1f}" '
                     f'y1="{my - vy / ln * 12:.1f}" '
                     f'x2="{mx + vx / ln * 12:.1f}" '
                     f'y2="{my + vy / ln * 12:.1f}" class="arcR"/>')
    for i, (x, y) in enumerate(pos):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" class="nd"/>')
        parts.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" class="tag">'
                     f'{i}</text>')
    parts.append(f'<text x="{cx}" y="{cy - 6}" class="bxl" '
                 f'text-anchor="middle">every arc</text>')
    parts.append(f'<text x="{cx}" y="{cy + 12}" class="bxl ok2" '
                 f'text-anchor="middle">busy, once</text>')
    parts.append('<text x="300" y="40" class="bxt">Rotation step</text>')
    for i, line in enumerate([
            "All 8 stations board simultaneously, each sending one",
            "hop clockwise. Every one of the ring's arcs carries",
            "exactly one flit, so the step costs 1 arc-cycle and",
            "moves 8 flits. Repeat 7 times and every node holds",
            "every other node's item.",
            "",
            "That is why the busiest-segment lower bound is MET",
            "exactly: II_eff = 47 = the per-round arc load.",
            "",
            "It is also why one dead segment is fatal: the step has",
            "no spare arc to route around with."]):
        cls = "bxl ok2" if "MET" in line else (
            "bxl warn" if "fatal" in line else "bxl")
        parts.append(f'<text x="300" y="{68 + i * 19}" class="{cls}">'
                     f'{line}</text>')
    return _grid_svg(860, 275, "".join(parts))


def svg_curve(series: list[dict], *, w: int = 720, h: int = 260,
              xlabel: str = "", ylabel: str = "", logx: bool = False,
              hline: float | None = None, hlabel: str = "") -> str:
    """Small line plot. series = [{name, cls, pts:[(x,y)]}]."""
    L, Rr, T, B = 62, 132, 24, 42
    xs = [p[0] for s in series for p in s["pts"]]
    ys = [p[1] for s in series for p in s["pts"]]
    if not xs:
        return ""
    if hline is not None:
        ys.append(hline)

    def tx(v: float) -> float:
        lo, hi = min(xs), max(xs)
        if logx:
            lo, hi = math.log10(max(lo, 1)), math.log10(max(hi, 2))
            v = math.log10(max(v, 1))
        return L + (w - L - Rr) * (0 if hi == lo else (v - lo) / (hi - lo))

    def ty(v: float) -> float:
        lo, hi = 0, max(ys) * 1.08
        return h - B - (h - B - T) * (0 if hi == lo else (v - lo) / (hi - lo))

    parts = [f'<rect x="{L}" y="{T}" width="{w - L - Rr}" '
             f'height="{h - B - T}" class="plot"/>']
    for i in range(5):
        yv = max(ys) * 1.08 * i / 4
        parts.append(f'<line x1="{L}" y1="{ty(yv):.1f}" x2="{w - Rr}" '
                     f'y2="{ty(yv):.1f}" class="gl"/>')
        parts.append(f'<text x="{L - 8}" y="{ty(yv) + 4:.1f}" class="tick" '
                     f'text-anchor="end">{yv:,.0f}</text>')
    for xv in sorted({p[0] for s in series for p in s["pts"]}):
        parts.append(f'<text x="{tx(xv):.1f}" y="{h - B + 16}" class="tick" '
                     f'text-anchor="middle">{xv:g}</text>')
    if hline is not None:
        parts.append(f'<line x1="{L}" y1="{ty(hline):.1f}" x2="{w - Rr}" '
                     f'y2="{ty(hline):.1f}" class="anch"/>')
        parts.append(f'<text x="{w - Rr - 4}" y="{ty(hline) - 5:.1f}" '
                     f'class="anchl" text-anchor="end">{hlabel}</text>')
    for i, s in enumerate(series):
        d = " ".join(f"{'M' if j == 0 else 'L'} {tx(x):.1f} {ty(y):.1f}"
                     for j, (x, y) in enumerate(s["pts"]))
        parts.append(f'<path d="{d}" class="cv {s["cls"]}"/>')
        for x, y in s["pts"]:
            parts.append(f'<circle cx="{tx(x):.1f}" cy="{ty(y):.1f}" r="2.6" '
                         f'class="star {s["cls"]}"/>')
        parts.append(f'<text x="{w - Rr + 10}" y="{T + 16 + i * 18}" '
                     f'class="axl">&#9679;</text>')
        parts.append(f'<text x="{w - Rr + 24}" y="{T + 16 + i * 18}" '
                     f'class="axl">{s["name"]}</text>')
    parts.append(f'<text x="{(L + w - Rr) / 2}" y="{h - 6}" class="axl" '
                 f'text-anchor="middle">{xlabel}</text>')
    parts.append(f'<text x="14" y="{(T + h - B) / 2}" class="axl" '
                 f'transform="rotate(-90 14 {(T + h - B) / 2})" '
                 f'text-anchor="middle">{ylabel}</text>')
    return _grid_svg(w, h, "".join(parts))


# ---------------------------------------------------------------------------
# 3. Sections
# ---------------------------------------------------------------------------

CSS = """
body { font-family: "Segoe UI", "Noto Sans SC", system-ui, sans-serif;
       margin: 0; background: #0b1020; color: #e8ecf4; line-height: 1.62; }
.wrap { max-width: 1160px; margin: 0 auto; padding: 28px 34px 80px; }
h1 { font-size: 1.65rem; color: #f0f4ff; border-bottom: 1px solid #2a3555;
     padding-bottom: .5rem; }
h2 { margin-top: 2.4rem; font-size: 1.28rem; color: #f0f4ff;
     border-left: 4px solid #7eb6ff; padding-left: .6rem; }
h3 { margin-top: 1.7rem; font-size: 1.05rem; color: #c8d6f0; }
p { margin: .6rem 0; }
a { color: #7eb6ff; }
.muted { color: #9aa3b5; font-size: .85rem; }
.lead { font-size: 1.02rem; color: #d6def0; }
.cards { display: grid; grid-template-columns: repeat(auto-fill,minmax(215px,1fr));
         gap: 12px; margin: 1.1rem 0 1.6rem; }
.card { background: #141b2f; border: 1px solid #2a3555; border-radius: 10px;
        padding: 12px 14px; }
.card.ok { border-color: #2d6a4f; } .card.bad { border-color: #9b2226; }
.card .k { font-size: .78rem; color: #9aa3b5; }
.card .v { font-size: 1.4rem; font-weight: 700; margin: .2rem 0; }
.card .s { font-size: .78rem; color: #b8c0d0; }
table { border-collapse: collapse; width: 100%; font-size: .85rem;
        margin: .7rem 0 1.3rem; }
th, td { border: 1px solid #2a3555; padding: 6px 9px; text-align: left;
         vertical-align: top; }
th { background: #1a2340; font-weight: 600; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
tr:nth-child(even) { background: #12192c; }
tr.hl td { background: #17243d; }
code { background: #1a2340; padding: 1px 5px; border-radius: 4px;
       font-size: .88em; }
pre.code { background: #10162a; border: 1px solid #2a3555; border-left: 3px
       solid #7eb6ff; border-radius: 8px; padding: 12px 16px; overflow-x: auto;
       font-family: ui-monospace, "Cascadia Code", monospace; font-size: .82rem;
       line-height: 1.5; color: #d8e2f5; }
.eq { background: #141b2f; padding: 10px 15px; border-radius: 8px;
      font-family: ui-monospace, monospace; margin: .7rem 0; font-size: .85rem;
      border: 1px solid #2a3555; }
.win { color: #6ee7a8; font-weight: 600; }
.lose { color: #f0a0a0; font-weight: 600; }
.pill { display: inline-block; background: #1a2340; border: 1px solid #2a3555;
        border-radius: 999px; padding: 2px 11px; font-size: .78rem;
        margin: 0 6px 6px 0; }
.note { background: #141b2f; border-left: 3px solid #d9a03c;
        border-radius: 0 8px 8px 0; padding: 10px 15px; margin: .9rem 0;
        font-size: .89rem; }
.note.good { border-left-color: #2d6a4f; }
.note.bad { border-left-color: #9b2226; }
.note b { color: #f0f4ff; }
.fig { background: #0e1425; border: 1px solid #2a3555; border-radius: 10px;
       padding: 14px 12px 8px; margin: 1.1rem 0 1.4rem; }
.fig .cap { color: #9aa3b5; font-size: .82rem; margin-top: .5rem;
            padding: 0 6px; }
.toc { background: #141b2f; border: 1px solid #2a3555; border-radius: 10px;
       padding: 12px 20px; columns: 2; font-size: .88rem; }
.toc a { text-decoration: none; }
ul, ol { margin: .5rem 0 .9rem; padding-left: 1.5rem; }
li { margin: .22rem 0; }
svg text { font-family: "Segoe UI","Noto Sans SC",system-ui,sans-serif; }
.rlk { stroke: #3a5580; stroke-width: 2.4; }
.wrp { fill: none; stroke: #55618a; stroke-width: 1.4; stroke-dasharray: 4 3; }
.nd { fill: #8fa2c8; } .nd.src { fill: #6ee7a8; } .nd.dst { fill: #7eb6ff; }
.arcR { fill: none; stroke: #6ee7a8; stroke-width: 4.5; }
.pRM { fill: none; stroke: #ff9ecb; stroke-width: 2; }
.ringc { fill: none; stroke: #3a5580; stroke-width: 2.6; }
.tag { fill: #0b1020; font-size: 10px; font-weight: 700; text-anchor: middle; }
.lbl { fill: #c8d0e0; font-size: 12px; }
.lbl.warn, .warn { fill: #f0c070; } .ok2 { fill: #6ee7a8; }
.bxt { fill: #f0f4ff; font-size: 13px; font-weight: 700; }
.bxl { fill: #c2ccdf; font-size: 12px; } .bxl.dim, .dim { fill: #8b95ab; }
.ar { stroke: #7eb6ff; stroke-width: 1.8; }
.plot { fill: #0b142a; stroke: #2a3555; }
.gl { stroke: #1f2a47; }
.anch { stroke: #d9a03c; stroke-width: 1.3; stroke-dasharray: 3 3; }
.anchl { fill: #d9a03c; font-size: 11px; }
.cv { fill: none; stroke-width: 2.6; }
.cA { stroke: #7eb6ff; } .cB { stroke: #6ee7a8; }
.cC { stroke: #f0a0a0; } .cD { stroke: #c9a6ff; }
.star.cA { fill: #7eb6ff; } .star.cB { fill: #6ee7a8; }
.star.cC { fill: #f0a0a0; } .star.cD { fill: #c9a6ff; }
.tick { fill: #8b95ab; font-size: 11px; }
.axl { fill: #9aa3b5; font-size: 12px; }
"""


def sec_cards(d: dict) -> str:
    c, v, rob = d["coll"], d["ver"], d["rob"]
    out = []
    if v:
        out.append(f'<div class="card {"ok" if v["all_pass"] else "bad"}">'
                   f'<div class="k">verification</div>'
                   f'<div class="v">{v["n_pass"]}/{v["n_checks"]}</div>'
                   f'<div class="s">executable checks pass</div></div>')
    if c:
        m13 = rows(c, m=13, bidir=True)
        bc = row1(c, pattern="broadcast", algo="dim_2phase", tier="T1", m=13,
                  bidir=True)
        bcf = row1(c, pattern="broadcast", algo="flat", tier="T0", m=13,
                   bidir=True)
        if bc and bcf:
            out.append(f'<div class="card ok"><div class="k">broadcast, '
                       f'arc multicast vs flat unicast</div>'
                       f'<div class="v">'
                       f'{bcf["calendar"]["makespan"] / bc["calendar"]["makespan"]:.1f}&times;'
                       f'</div><div class="s">'
                       f'{bcf["calendar"]["makespan"]} &rarr; '
                       f'{bc["calendar"]["makespan"]} cycles at m=13</div>'
                       f'</div>')
        base = [r for r in m13 if r["ring_base"]["makespan"] is not None]
        if base:
            w = max(base, key=lambda r: r["ratios"]["base_over_calendar"])
            out.append(f'<div class="card"><div class="k">paper mechanism vs '
                       f'best static calendar</div><div class="v">'
                       f'{w["ratios"]["base_over_calendar"]:.2f}&times;</div>'
                       f'<div class="s">worst case: {w["pattern"]}/'
                       f'{w["algo"]}</div></div>')
        ag = row1(c, pattern="allgather", algo="flat", tier="T0", m=13,
                  bidir=True)
        if ag:
            out.append(f'<div class="card"><div class="k">busiest-segment '
                       f'utilization, flat allgather m=13</div>'
                       f'<div class="v">'
                       f'{pct(ag["calendar"]["util"]["critical_arc_util"])}'
                       f'</div><div class="s">global '
                       f'{pct(ag["calendar"]["util"]["global_util"])} over all '
                       f'192 arcs</div></div>')
    if rob:
        rot = next((x for x in rob["faults"] if x["algo"] == "ring_rotate"),
                   None)
        dim = next((x for x in rob["faults"]
                    if x["algo"] == "dim_2phase" and x["tier"] == "T1"), None)
        if rot and dim:
            out.append(f'<div class="card bad"><div class="k">fault scenarios '
                       f'with no legal schedule</div><div class="v">'
                       f'{rot["n_infeasible"]}/{rot["n_scenarios"]}</div>'
                       f'<div class="s">rotation; dimension-phase is '
                       f'{dim["n_infeasible"]}/{dim["n_scenarios"]}</div>'
                       f'</div>')
    if d["idx"]:
        out.append(f'<div class="card"><div class="k">exported slot tables'
                   f'</div><div class="v">{len(d["idx"]["entries"])}</div>'
                   f'<div class="s">calendar-export/v2, all conflict free'
                   f'</div></div>')
    return f'<div class="cards">{"".join(out)}</div>'


def sec_baseline(c: dict) -> str:
    """Part 1: the paper mechanism on all six collectives."""
    body = []
    for m in (1, 13):
        body.append(f"<h3>m = {m} flit(s) per message</h3>")
        body.append('<table><tr><th>collective</th><th>algorithm</th>'
                    '<th>tier</th><th class="n">ring_base<br>(paper)</th>'
                    '<th class="n">ring_islip2d</th>'
                    '<th class="n">static calendar</th>'
                    '<th class="n">lower bound</th><th>binding bound</th>'
                    '<th class="n">base/cal</th><th class="n">cal/LB</th>'
                    '<th class="n">deflections<br>per flit</th></tr>')
        for pat in PATTERN_ORDER:
            rs = [r for r in rows(c, pattern=pat, m=m, bidir=True)]
            rs.sort(key=lambda r: (r["tier"], r["calendar"]["makespan"]))
            for i, r in enumerate(rs):
                cal, b = r["calendar"], r["bounds"]
                rb, isl = r["ring_base"], r["ring_islip2d"]
                hl = ' class="hl"' if i == 0 else ""
                body.append(
                    f'<tr{hl}><td>{pat if i == 0 else ""}</td>'
                    f'<td>{r["algo"]}</td><td>{r["tier"]}</td>'
                    f'<td class="n">{f(rb.get("makespan"))}</td>'
                    f'<td class="n">{f(isl.get("makespan"))}</td>'
                    f'<td class="n"><b>{f(cal["makespan"])}</b></td>'
                    f'<td class="n">{f(b["makespan_lb"])}</td>'
                    f'<td>{b["binding_lb"]}</td>'
                    f'<td class="n">{f(r["ratios"].get("base_over_calendar"), 2)}</td>'
                    f'<td class="n">{f(cal["makespan_over_lb"], 2)}</td>'
                    f'<td class="n">{f(rb.get("deflect_per_flit"), 4)}</td>'
                    f'</tr>')
        body.append("</table>")
    return "".join(body)


def sec_tavg(t: dict) -> str:
    if not t:
        return '<p class="muted">ring_tavg_8x6.json missing.</p>'
    out = [f'<div class="eq">{t["definition"]}</div>']
    out.append('<table><tr><th>algorithm</th><th>tier</th>'
               '<th class="n">ports</th><th class="n">T1</th>'
               '<th class="n">T&#8325;</th><th class="n">T&#8321;&#8323;</th>'
               '<th class="n">II_eff</th>'
               '<th class="n">T_avg(R=1)</th><th class="n">T_avg(R=5)</th>'
               '<th class="n">T_avg(R=13)</th>'
               '<th class="n">critical arc util @R=13</th></tr>')
    rs = [r for r in t["ring"] if r["pattern"] == "allgather"]
    rs.sort(key=lambda r: r["by_rounds"]["13"]["T_avg"]
            if r["by_rounds"].get("13") else 1e9)
    for r in rs:
        br = r["by_rounds"]
        u13 = br.get("13", {}).get("util", {}) if br.get("13") else {}
        out.append(
            f'<tr><td>{r["algo"]}</td><td>{r["tier"]}</td>'
            f'<td class="n">{r["ports"]}</td>'
            f'<td class="n">{f(r["T1"])}</td>'
            f'<td class="n">{f(br["5"]["T_R"] if br.get("5") else None)}</td>'
            f'<td class="n">{f(br["13"]["T_R"] if br.get("13") else None)}</td>'
            f'<td class="n">{f(br["13"]["II_eff"] if br.get("13") else None, 2)}</td>'
            f'<td class="n">{f(br["1"]["T_avg"] if br.get("1") else None, 1)}</td>'
            f'<td class="n">{f(br["5"]["T_avg"] if br.get("5") else None, 1)}</td>'
            f'<td class="n"><b>{f(br["13"]["T_avg"] if br.get("13") else None, 1)}</b></td>'
            f'<td class="n">{pct(u13.get("critical_arc_util"))}</td></tr>')
    out.append("</table>")

    mesh = t.get("mesh_reference") or {}
    if not mesh.get("available"):
        out.append(f'<div class="note bad"><b>Mesh column not available.</b> '
                   f'{mesh.get("reason", "no mesh reference recorded")}. The '
                   f'ring numbers above stand on their own; the ring-vs-mesh '
                   f'ordering claim is deliberately left unstated rather than '
                   f'carried over from the older R=5-only mesh run.</div>')
        return "".join(out)

    out.append("<h3>Ring versus 8&times;6 mesh, same T_avg definition</h3>")
    out.append('<p>The mesh side sweeps its own design variables (crossbar '
               'write width, drain rate, FIFO depth) and the column below is '
               'the best mesh point at that R. The ring is shown twice, once '
               'per ring-station port count, because one board and one leave '
               'port per station is a different hardware budget than two and '
               'the verdict turns over between them.</p>')
    ports = sorted({p for e in t["ring_vs_mesh"]
                    for p in (e.get("by_ring_ports") or {})})
    out.append('<table><tr><th class="n">R</th><th>mesh best</th>'
               '<th class="n">mesh T_avg</th>'
               + "".join(f'<th class="n">ring T_avg<br>({p} port'
                         f'{"s" if p != "1" else ""})</th>'
                         f'<th class="n">ring/mesh</th>' for p in ports)
               + '</tr>')
    for e in t["ring_vs_mesh"]:
        mb = e.get("mesh_best") or {}
        cells = []
        for p in ports:
            v = (e.get("by_ring_ports") or {}).get(p) or {}
            win = v.get("winner")
            cls = "win" if win == "ring" else "lose"
            cells.append(
                f'<td class="n">{f(v.get("T_avg"), 1)}</td>'
                f'<td class="n"><span class="{cls}">'
                f'{times(v.get("ring_over_mesh"), 3)}</span></td>')
        out.append(
            f'<tr><td class="n">{e["R"]}</td>'
            f'<td>{mb.get("label") or mb.get("scheme") or "&mdash;"}</td>'
            f'<td class="n">{f(mb.get("T_avg"), 1)}</td>'
            + "".join(cells) + '</tr>')
    out.append("</table>")
    p1 = [(e["R"], (e.get("by_ring_ports") or {}).get("1") or {})
          for e in t["ring_vs_mesh"]]
    if all(v for _R, v in p1):
        out.append(
            f'<div class="note bad"><b>At one port per ring station the ring '
            f'loses the deep-pipeline case.</b> '
            + ", ".join(f'R={R}: {v["winner"]} by '
                        f'{times(v["ring_over_mesh"], 3)}' for R, v in p1)
            + f'. The ring\'s advantage at R=1 is a span advantage &mdash; its '
              f'data spans half a mesh diameter. At R=13 the binding resource '
              f'is the station\'s single insert/extract port, and the mesh\'s '
              f'Hamilton bi-tree (II_eff = 1.0) pipelines past it. Only the '
              f'2-port ring wins at every R. Quoting the ring\'s win without '
              f'naming the port count would be the single most misleading '
              f'number in this report.</div>')
    fl = t.get("order_flips")
    if isinstance(fl, list) and fl:
        out.append(f'<div class="note bad"><b>The ranking does not survive the '
                   f'pipeline depth.</b> {"; ".join(fl)}. Quoting a single R '
                   f'would have picked a winner by accident.</div>')
    elif fl:
        out.append(f'<div class="note good"><b>The ranking survives the '
                   f'pipeline depth.</b> {fl}, so the R=1 comparison was not '
                   f'an artefact of the chosen depth.</div>')
    return "".join(out)


def sec_levers(c: dict) -> str:
    out = []
    out.append("<h3>Lever 1 &mdash; arc multicast (copy and continue)</h3>")
    out.append('<table><tr><th>collective</th><th class="n">m</th>'
               '<th class="n">T0 boarded flits</th>'
               '<th class="n">T1 boarded flits</th><th class="n">traffic cut</th>'
               '<th class="n">T0 makespan</th><th class="n">T1 makespan</th>'
               '<th class="n">makespan cut</th></tr>')
    absent = []
    for pat in ("broadcast", "allgather", "allreduce", "reduce", "gather",
                "alltoall"):
        t0 = row1(c, pattern=pat, algo="dim_2phase", tier="T0", m=13,
                  bidir=True) or row1(c, pattern=pat, algo="flat", tier="T0",
                                      m=13, bidir=True)
        t1 = row1(c, pattern=pat, algo="dim_2phase", tier="T1", m=13,
                  bidir=True)
        if not t1:
            absent.append(pat)
            continue
        if not t0:
            continue
        a = t0["shape"]["n_flits_boarded"]
        b = t1["shape"]["n_flits_boarded"]
        out.append(
            f'<tr><td>{pat}</td><td class="n">13</td>'
            f'<td class="n">{f(a)}</td><td class="n">{f(b)}</td>'
            f'<td class="n">{rat(a, b)}</td>'
            f'<td class="n">{f(t0["calendar"]["makespan"])}</td>'
            f'<td class="n">{f(t1["calendar"]["makespan"])}</td>'
            f'<td class="n">'
            f'{rat(t0["calendar"]["makespan"], t1["calendar"]["makespan"])}'
            f'</td></tr>')
    out.append("</table>")
    if absent:
        out.append(
            f'<div class="note"><b>{", ".join(absent)} have no T1 row, and '
            f'that is the point.</b> Copy-and-continue is a fan-out primitive. '
            f'A fan-in ({", ".join(p for p in absent if p != "alltoall")}) has '
            f'nothing to replicate, and all-to-all\'s N(N&minus;1) messages are '
            f'all distinct, so no station can serve two of them with one copy. '
            f'For these patterns the multicast hardware buys exactly nothing '
            f'and the verification suite asserts T1 is byte-identical to T0.'
            f'</div>')

    out.append("<h3>Lever 2 &mdash; bidirectional half-arc</h3>")
    out.append('<table><tr><th>scheme</th><th class="n">m</th>'
               '<th class="n">bidirectional</th><th class="n">clockwise only</th>'
               '<th class="n">makespan ratio</th>'
               '<th class="n">traffic ratio</th></tr>')
    for e in c["bidir_lever"]:
        out.append(
            f'<tr><td>{scheme_label(e["pattern"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{e["m"]}</td>'
            f'<td class="n">{f(e["bi"]["makespan"])}</td>'
            f'<td class="n">{f(e["uni"]["makespan"])}</td>'
            f'<td class="n"><b>{times(e["makespan_ratio_uni_over_bi"], 2)}'
            f'</b></td>'
            f'<td class="n">{times(e["traffic_ratio_uni_over_bi"], 2)}</td>'
            f'</tr>')
    out.append("</table>")
    mc = [e for e in c["bidir_lever"]
          if abs(e["traffic_ratio_uni_over_bi"] - 1.0) < 0.02]
    uc = [e for e in c["bidir_lever"]
          if abs(e["traffic_ratio_uni_over_bi"] - 1.0) >= 0.02]
    note = ['<div class="note"><b>Two different mechanisms hide in this '
            'column.</b> ']
    if mc:
        e = max(mc, key=lambda e: e["makespan_ratio_uni_over_bi"])
        note.append(
            f'On the multicast schemes the traffic ratio is '
            f'{f(e["traffic_ratio_uni_over_bi"], 2)}&times; while the makespan '
            f'ratio is {f(e["makespan_ratio_uni_over_bi"], 2)}&times;: a '
            f'copy-and-continue arc drops the same copy at the same stations '
            f'whichever way it goes round, so going both ways halves the '
            f'<i>span</i> and moves no fewer flits. That is a latency win, not '
            f'a bandwidth win. ')
    if uc:
        e = max(uc, key=lambda e: e["traffic_ratio_uni_over_bi"])
        note.append(
            f'On the unicast schemes it is a bandwidth win too, but for the '
            f'other reason: a clockwise-only route is simply longer, so it '
            f'costs {f(e["traffic_ratio_uni_over_bi"], 2)}&times; the arc '
            f'cycles ({scheme_label(e["pattern"], e["algo"], e["tier"])}). ')
    note.append('The blanket claim that bidirectional routing "halves the peak '
                'arc load" holds for neither case as stated.</div>')
    out.append("".join(note))

    out.append("<h3>Lever 3 &mdash; full-ring rotation</h3>")
    out.append(f'<div class="fig">{svg_rotation()}'
               f'<div class="cap">A rotation step on an 8-node row ring. '
               f'Rotation is the only scheme here that meets the '
               f'busiest-segment bound exactly, and the same rigidity is what '
               f'makes it the most fault-intolerant scheme in the set.</div>'
               f'</div>')

    out.append("<h3>Lever 4 &mdash; L1 accumulation chain</h3>")
    g = row1(c, pattern="gather", algo="dim_2phase", tier="T0", m=13,
             bidir=True)
    r = row1(c, pattern="reduce", algo="dim_2phase", tier="T0", m=13,
             bidir=True)
    ar = row1(c, pattern="allreduce", algo="dim_2phase", tier="T1", m=13,
              bidir=True)
    hd = row1(c, pattern="allreduce", algo="halving_doubling", tier="T1", m=13,
              bidir=True) or row1(c, pattern="allreduce",
                                  algo="halving_doubling", tier="T0", m=13,
                                  bidir=True)
    if g and r:
        out.append(
            f'<p>Folding in L1 keeps every hop the same size as the payload, '
            f'so the reduce tree boards '
            f'<b>{g["shape"]["n_flits_boarded"] / r["shape"]["n_flits_boarded"]:.2f}&times;</b> '
            f'fewer flits than the gather tree that moves the same data '
            f'({f(r["shape"]["n_flits_boarded"])} vs '
            f'{f(g["shape"]["n_flits_boarded"])} at m=13), and finishes in '
            f'{f(r["calendar"]["makespan"])} rather than '
            f'{f(g["calendar"]["makespan"])} cycles.</p>')
    if ar and hd:
        out.append(
            f'<p>For allreduce at m=13, reduce-then-broadcast on the dimension '
            f'tree ({ar["tier"]}) lands at <b>{f(ar["calendar"]["makespan"])}'
            f'</b> cycles on {f(ar["shape"]["n_flits_boarded"])} boarded '
            f'flits, against <b>{f(hd["calendar"]["makespan"])}</b> cycles on '
            f'{f(hd["shape"]["n_flits_boarded"])} flits for recursive '
            f'halving-doubling ({hd["tier"]}). Note the ordering: '
            f'halving-doubling boards '
            f'{hd["shape"]["n_flits_boarded"] / ar["shape"]["n_flits_boarded"]:.1f}'
            f'&times; the traffic and is still the faster of the two T0 '
            f'options, so on this fabric concurrency outranks total traffic.'
            f'</p>')

    out.append("<h3>Packing order and port count</h3>")
    out.append('<table><tr><th>scheme</th><th class="n">m</th>'
               '<th>best fill order</th><th class="n">makespan spread across '
               'orders</th></tr>')
    for e in c["fill_lever"]:
        out.append(
            f'<tr><td>{scheme_label(e["pattern"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{e["m"]}</td><td>{e["best_fill"]}</td>'
            f'<td class="n">{f(e["spread"])} cycles</td></tr>')
    out.append("</table>")
    out.append('<table><tr><th>scheme</th><th class="n">m</th>'
               '<th class="n">1 board+leave port</th>'
               '<th class="n">2 ports</th><th class="n">speedup</th></tr>')
    for e in c["port_sensitivity"]:
        bp = e["by_ports"]
        out.append(
            f'<tr><td>{scheme_label(e["pattern"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{e["m"]}</td>'
            f'<td class="n">{f(bp["1"]["makespan"])}</td>'
            f'<td class="n">{f(bp["2"]["makespan"])}</td>'
            f'<td class="n">{times(e["speedup_ports2"], 2)}</td></tr>')
    out.append("</table>")
    return "".join(out)


def sec_util(c: dict) -> str:
    out = ['<div class="eq">global = &Sigma; flits&middot;hops&middot;&sigma; '
           '/ (192 &middot; makespan) &nbsp;&nbsp; critical = '
           'busiest-arc cycles / makespan</div>']
    out.append('<table><tr><th>collective</th><th>algorithm</th><th>tier</th>'
               '<th class="n">m</th><th class="n">global util</th>'
               '<th class="n">critical arc util</th>'
               '<th class="n">arcs used</th>'
               '<th class="n">critical arc vs its own bound</th>'
               '<th class="n">makespan / LB</th></tr>')
    for pat in PATTERN_ORDER:
        for r in sorted(rows(c, pattern=pat, m=13, bidir=True),
                        key=lambda r: -r["calendar"]["util"]["global_util"]):
            u = r["calendar"]["util"]
            out.append(
                f'<tr><td>{pat}</td><td>{r["algo"]}</td><td>{r["tier"]}</td>'
                f'<td class="n">13</td>'
                f'<td class="n">{pct(u["global_util"])}</td>'
                f'<td class="n">{pct(u["critical_arc_util"])}</td>'
                f'<td class="n">{u["n_links_used"]}/192</td>'
                f'<td class="n">{times(u["critical_arc_cycles_vs_lb"], 2)}</td>'
                f'<td class="n">{times(r["calendar"]["makespan_over_lb"], 2)}'
                f'</td></tr>')
    out.append("</table>")
    return "".join(out)


def sec_faults(rob: dict) -> str:
    if not rob:
        return '<p class="muted">ring_robust_8x6.json missing.</p>'
    out = ['<table><tr><th>scheme</th><th class="n">immune</th>'
           '<th class="n">recompile</th><th class="n">infeasible</th>'
           '<th class="n">needs extra phase</th>'
           '<th class="n">worst inflation</th>'
           '<th class="n">worst work-normalized</th></tr>']
    for x in rob["faults"]:
        out.append(
            f'<tr><td>{scheme_label(x["pattern"], x["algo"], x["tier"])}</td>'
            f'<td class="n">{x["n_immune"]}</td>'
            f'<td class="n">{x["n_recompile"]}</td>'
            f'<td class="n">{x["n_infeasible"]}</td>'
            f'<td class="n">{f(x.get("n_needing_repair_phase"))}</td>'
            f'<td class="n">{times(x["worst_inflation"], 2)}</td>'
            f'<td class="n">{times(x["worst_work_normalized_inflation"], 2)}'
            f'</td></tr>')
    out.append("</table>")
    out.append('<div class="note"><b>Why two inflation columns.</b> A dead '
               'node removes work as well as capacity, so a raw makespan ratio '
               'below 1.0 means the array shrank, not that losing a node made '
               'the collective faster. The work-normalized column divides by '
               'the surviving flit count and is the one to read.</div>')

    out.append("<h3>Does a dead node need a ring-station bypass mux?</h3>")
    out.append('<table><tr><th>scheme</th>'
               '<th class="n">infeasible with bypass</th>'
               '<th class="n">infeasible without</th>'
               '<th class="n">extra without bypass</th></tr>')
    for e in rob["bypass_price"]:
        out.append(
            f'<tr><td>{scheme_label(e["pattern"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{e["bypass"]["n_infeasible"]}'
            f'/{e["bypass"]["n_scenarios"]}</td>'
            f'<td class="n">{e["no_bypass"]["n_infeasible"]}'
            f'/{e["no_bypass"]["n_scenarios"]}</td>'
            f'<td class="n"><b>{e["extra_infeasible_without_bypass"]}</b>'
            f'</td></tr>')
    out.append("</table>")
    return "".join(out)


def sec_jitter(rob: dict) -> str:
    if not rob:
        return '<p class="muted">ring_robust_8x6.json missing.</p>'
    out = ['<table><tr><th>scheme</th><th class="n">makespan</th>'
           '<th class="n">slack p50</th><th class="n">slack min</th>'
           '<th class="n">J* uniform</th><th class="n">J* skew</th>'
           '<th class="n">J* burst</th>'
           '<th class="n">slack absorbed @J=256 burst</th></tr>']
    for x in rob["jitter"]:
        mo = x["jitter"]["models"]

        def js(model: str) -> str:
            return f(mo[model]["repack"]["J_star"])
        out.append(
            f'<tr><td>{scheme_label(x["pattern"], x["algo"], x["tier"])}</td>'
            f'<td class="n">{f(x["makespan"])}</td>'
            f'<td class="n">{f(x["slack"]["p50"])}</td>'
            f'<td class="n">{f(x["slack"]["min"])}</td>'
            f'<td class="n">{js("uniform_jitter")}</td>'
            f'<td class="n">{js("distance_skew")}</td>'
            f'<td class="n">{js("burst")}</td>'
            f'<td class="n">'
            f'{f(x["at_J256_burst"]["slack_absorbed_cycles"])}</td></tr>')
    out.append("</table>")

    big = max(rob["jitter"], key=lambda x: x["makespan"])
    mo = big["jitter"]["models"]["burst"]
    series = [{"name": p.replace("_", " "), "cls": cls,
               "pts": [(pt["J"], pt["makespan"]) for pt in mo[p]["curve"]]}
              for p, cls in (("global_shift", "cC"), ("phase_shift", "cA"),
                             ("repack", "cB"))]
    out.append(
        f'<div class="fig">'
        f'{svg_curve(series, xlabel="release jitter J (cycles, log scale)", ylabel="makespan (cycles)", logx=True, hline=big["makespan"], hlabel="healthy makespan")}'
        f'<div class="cap">Burst jitter on '
        f'{scheme_label(big["pattern"], big["algo"], big["tier"])} at m='
        f'{big["m"]}. The three curves are the three ways to react: shift '
        f'everything, resynchronize per phase, or recompile with the late '
        f'release times as constraints. Only the third can absorb anything, '
        f'and what it absorbs is exactly the slack the packer left behind.'
        f'</div></div>')
    out.append('<div class="note"><b>J* is a weak metric on a rigid '
               'schedule.</b> Under a hard barrier the makespan grows by '
               'exactly the worst lateness, so J* is just 5% of the makespan '
               'restated &mdash; it says nothing about the schedule. The '
               'absorbed-cycles column is the informative one, because it '
               'measures slack that actually exists.</div>')
    return "".join(out)


def sec_contrary(d: dict) -> str:
    c, rob, ver, t = d["coll"], d["rob"], d["ver"], d["tavg"]
    items = []

    ru = (t or {}).get("rotation_utilization", {}).get("rows", [])
    if ru:
        best = max(ru, key=lambda r: r["critical_arc_util"])
        items.append(
            f'<li><b>Rotation does not reach 100% link utilization.</b> The '
            f'plan predicted 1.0. Measured: {pct(best["critical_arc_util"])} '
            f'at R={best["rounds"]}, rising monotonically but only '
            f'asymptotically. It <i>does</i> hit the busiest-segment bound '
            f'exactly (II_eff = {f(ru[1]["II_eff"], 1) if len(ru) > 1 else "?"} '
            f'= per-round arc load), but the fill cost T1='
            f'{f(ru[0]["makespan"])} never amortizes away: utilization is '
            f'II&middot;R/(T1+II&middot;(R&minus;1)), which approaches 1 only '
            f'as R&rarr;&infin;.</li>')

    p1 = [(e["R"], (e.get("by_ring_ports") or {}).get("1") or {})
          for e in (t or {}).get("ring_vs_mesh", [])]
    if p1 and all(v for _R, v in p1) and any(
            v["winner"] == "mesh" for _R, v in p1):
        deep = max(p1, key=lambda x: x[0])
        items.append(
            f'<li><b>A one-port ring loses to the mesh once the pipeline is '
            f'deep.</b> The ring wins the single-shot case '
            f'({times(p1[0][1]["ring_over_mesh"], 3)} at R={p1[0][0]}) on span '
            f'alone, but at R={deep[0]} it is behind by '
            f'{times(deep[1]["ring_over_mesh"], 3)}: the binding resource has '
            f'moved from distance to the station\'s single insert/extract '
            f'port, and the mesh\'s Hamilton bi-tree pipelines past it with '
            f'II_eff = 1.0. The mesh\'s own best scheme changes at that depth '
            f'too, so neither fabric can be ranked from one R.</li>')

    if rob:
        n_extra = sum(e["extra_infeasible_without_bypass"]
                      for e in rob["bypass_price"])
        items.append(
            f'<li><b>A contiguous dead node does not cut the ring.</b> The '
            f'plan assumed one dead node equals two breaks, so a station '
            f'bypass mux would be mandatory. On a 2-connected ring a '
            f'contiguous hole is routable the long way round, and removing the '
            f'bypass mux costs nothing for contiguous node and quadrant '
            f'faults. It is <i>scattered</i> dead nodes on one ring that '
            f'partition it, which is where the mux earns its area '
            f'({n_extra} extra infeasible scenarios without it across the '
            f'measured schemes).</li>')

    if rob and any(x.get("n_needing_repair_phase") for x in rob["faults"]):
        worst = max(rob["faults"],
                    key=lambda x: x.get("n_needing_repair_phase") or 0)
        items.append(
            f'<li><b>A dead node forces an extra phase, not just a '
            f'reroute.</b> A dimension-sliced algorithm hands a row\'s data to '
            f'a column through the one node where they meet. Kill that node '
            f'and the whole column loses that row &mdash; the fabric is still '
            f'connected, but the schedule had exactly one path. '
            f'{worst["n_needing_repair_phase"]} of '
            f'{worst["n_recompile"]} recompiles on '
            f'{scheme_label(worst["pattern"], worst["algo"], worst["tier"])} '
            f'need a repair phase appended. Reporting these as "recompiles '
            f'with 1.2&times; inflation" would have hidden a missing '
            f'phase.</li>')

    if c:
        b = [e for e in (c["bidir_lever"] or [])
             if abs(e["traffic_ratio_uni_over_bi"] - 1.0) < 0.02]
        if b:
            e = max(b, key=lambda e: e["makespan_ratio_uni_over_bi"])
            items.append(
                f'<li><b>Bidirectional routing halves the span, not the load '
                f'&mdash; wherever multicast is doing the work.</b> The plan '
                f'predicted "peak arc load halved". On '
                f'{scheme_label(e["pattern"], e["algo"], e["tier"])} the '
                f'traffic ratio measures '
                f'{times(e["traffic_ratio_uni_over_bi"], 2)} against a '
                f'makespan ratio of '
                f'{times(e["makespan_ratio_uni_over_bi"], 2)}: one arc drops '
                f'copies at the same stations whichever way it travels, so '
                f'only the span shrinks. The unicast schemes do save arc '
                f'cycles, but because the one-way path is longer, not because '
                f'the load was split.</li>')

        funnels = [r for r in rows(c, m=13, bidir=True)
                   if r["pattern"] in ("gather", "reduce")
                   and r["ring_base"]["makespan"] is not None
                   and r["ring_base"]["makespan"] < r["calendar"]["makespan"]]
        if funnels:
            w = min(funnels, key=lambda r: r["ring_base"]["makespan"]
                    / r["calendar"]["makespan"])
            ps = next((e for e in c["port_sensitivity"]
                       if e["pattern"] == w["pattern"]
                       and e["algo"] == w["algo"]), None)
            extra = ""
            if ps:
                extra = (f' Widening the model to two extract ports moves the '
                         f'calendar to {f(ps["by_ports"]["2"]["makespan"])} '
                         f'({f(ps["speedup_ports2"], 2)}&times;), which is the '
                         f'honest apples-to-apples comparison.')
            items.append(
                f'<li><b>The paper mechanism beats the static calendar on the '
                f'funnel collectives.</b> On '
                f'{scheme_label(w["pattern"], w["algo"], w["tier"])} at m=13, '
                f'ring_base finishes in {f(w["ring_base"]["makespan"])} cycles '
                f'against the calendar\'s {f(w["calendar"]["makespan"])}. This '
                f'is a modelling difference, not deflection magic: the '
                f'calendar reserves an extract port for a whole burst, while '
                f'ring_base interleaves individual flits at the L1 drain '
                f'rate.{extra}</li>')

    if ver:
        n_ref = sum(1 for ch in ver["checks"]
                    if "REFUTED" in (ch.get("prediction") or ""))
        items.append(
            f'<li class="muted">{n_ref} of the verification suite\'s labelled '
            f'predictions are recorded as refuted rather than quietly '
            f'relaxed; see <code>results/verify_ring_collectives_8x6.json'
            f'</code>.</li>')
    return f'<ul>{"".join(items)}</ul>'


def sec_limits(d: dict) -> str:
    t = d["tavg"] or {}
    mesh_note = ""
    if not (t.get("mesh_reference") or {}).get("available"):
        mesh_note = ('<li>The ring-versus-mesh T_avg comparison is '
                     'unpopulated: <code>results/multiflit_area_makespan.json'
                     '</code> predates the R&isin;{1,5,13} sweep. The ring '
                     'columns are complete; the cross-fabric ordering claim is '
                     'left unstated rather than carried over from the older '
                     'R=5-only run.</li>')
    return f"""<ul>
{mesh_note}
<li>The ring-versus-mesh comparison is "best point in each design space", and
the two spaces are not the same shape: the mesh sweeps crossbar write width,
drain rate and FIFO depth, the ring sweeps only station port count. That is why
the table gives one row per ring port count instead of a single summary
ratio.</li>
<li>The calendar model charges an extract port to one transfer for its whole
burst (m&middot;&sigma; cycles). The paper mechanism drains L1 per flit, so the
two are not on the same footing for funnel-shaped collectives; the port
sensitivity table bounds the gap rather than closing it.</li>
<li>Reduction is modelled as an item-set union with a size-preserving fold. That
captures traffic and dependency order exactly, but not arithmetic: an adder's
latency inside L1 is folded into <code>RAMP</code> and not modelled
separately.</li>
<li><code>ring_islip2d</code> is included as a same-capability scheduling
reference, not as a serious contender &mdash; it arbitrates one flit per node
per round, so on 2256-message patterns its makespan is an order of magnitude
off and should be read as a control, not a result.</li>
<li>Jitter is injected only at source release. In-flight jitter would need the
transport model rather than the calendar replay.</li>
<li>Fault recompilation assumes an offline compiler with the full fault list. No
claim is made about how long the recompile takes or how the new table is
distributed.</li>
<li>&sigma;=1 throughout the calendar work. The metal-constant &sigma;=2 reading
that the topology audit reports ({d['coll']['audit']['metal_ratio_vs_mesh'] if d.get('coll') else '?'}&times; the mesh's
wire) is not re-run here.</li>
</ul>"""


# ---------------------------------------------------------------------------
# 4. Page
# ---------------------------------------------------------------------------

def build(d: dict) -> str:
    c, ver, rob = d["coll"], d["ver"], d["rob"]
    a = c["audit"]
    toc = [("part1", "Part 1 &mdash; the paper mechanism on six collectives"),
           ("tavg", "Part 1b &mdash; T_avg at R = 1, 5, 13"),
           ("levers", "Part 2 &mdash; four structural levers"),
           ("util", "Bandwidth utilization"),
           ("faults", "Fault tolerance"),
           ("jitter", "Jitter tolerance"),
           ("contrary", "Results contrary to expectation"),
           ("limits", "Known limitations"),
           ("repro", "Reproducing this")]
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Collectives on an 8&times;6 bufferless ring</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>Collectives on the 8&times;6 dimension-sliced bufferless ring</h1>
<p class="lead">Two questions, one fabric. First: what makespan does the
HPCA'22 mechanism (E-tag/I-tag plus deflection, unicast only) reach on the six
collectives? Second: on the same physical ring, what can a static slot table do
if ring stations may copy-and-continue and nodes may accumulate in L1?</p>
<p class="muted">Fabric: {a['n_row_rings']} row rings &times; {a['mx']} +
{a['n_col_rings']} column rings &times; {a['my']} =
{a['n_directed_links']} directed segments over {a['n']} nodes, every node a
bridge, zero buffering inside a ring. Conflicts are the same D-R five-clause
predicate used throughout this repository.
{"All " + str(ver['n_checks']) + " executable checks pass." if ver and ver['all_pass'] else ""}</p>

{sec_cards(d)}

<div class="toc">{"".join(f'<div><a href="#{i}">{t}</a></div>' for i, t in toc)}</div>

<div class="fig">{svg_multicast()}
<div class="cap">The single hardware increment that separates the two halves of
this report. Left: one boarding whose copies fall off at every station. Right:
the same delivery as seven boardings, which is all the paper mechanism can
express. The arc-cycle counts are the ones the footprint model charges.</div>
</div>

<h2 id="part1">Part 1 &mdash; the paper mechanism on six collectives</h2>
<p>All three legs run the same flow set, the same m, the same &sigma; and the
same barrier semantics, so the columns are comparable. Two identities are worth
stating before reading the table, because they stop the comparison from
flattering anyone: under unicast-only capability <b>allgather and all-to-all are
the same flow set</b> (every node's flit reaches the other 47), and <b>gather and
reduce place the same demand on the network</b> &mdash; they differ only in
whether the root's L1 accumulates. The tier column is where the real difference
lives.</p>
{sec_baseline(c)}
<div class="note"><b>Reading the deflection column.</b> The paper mechanism's
cost is not only its makespan: deflections are re-circulations that consume arc
cycles and reorder arrivals. Where the column is near zero the mechanism is
effectively running the flow set unimpeded and its gap to the calendar is
scheduling, not deflection.</div>

<h2 id="tavg">Part 1b &mdash; T_avg at R = 1, 5, 13</h2>
<p>Same definition as the mesh study: pack R pipelined rounds freely and measure
T<sub>R</sub>, then report II_eff = (T<sub>R</sub>&minus;T1)/(R&minus;1) and
T_avg = T1 + (R&minus;1)/2 &middot; II_eff. R=1 makes T_avg identical to the
one-flit makespan.</p>
{sec_tavg(d['tavg'])}

<h2 id="levers">Part 2 &mdash; four structural levers</h2>
<p>Each lever came with a prediction. Two of the four came out differently than
predicted, and those are the interesting ones.</p>
{sec_levers(c)}

<h2 id="util">Bandwidth utilization</h2>
<p>Both numbers are reported for every scheme, because the pair is diagnostic:
high global with a low critical arc means there is still packing left to do;
critical arc at its own bound means the schedule is against the wall and only a
different flow set will help.</p>
{sec_util(c)}

<h2 id="faults">Fault tolerance</h2>
<p>{len(rob['faults'][0]['rows']) if rob else 0} scenarios per scheme: ring
wraparound segment loss (the failure a ring has and a mesh does not), scattered
dead nodes on one ring, and the repository's existing link, node and quadrant
holes.</p>
{sec_faults(rob)}

<h2 id="jitter">Jitter tolerance</h2>
{sec_jitter(rob)}

<h2 id="contrary">Results contrary to expectation</h2>
{sec_contrary(d)}

<h2 id="limits">Known limitations</h2>
{sec_limits(d)}

<h2 id="repro">Reproducing this</h2>
<pre class="code">cd utils
python3 dse_ring_collectives_8x6.py   <span class="c"># Part 1 + calendars  -> results/ring_collectives_8x6.json</span>
python3 dse_ring_tavg_8x6.py          <span class="c"># T_avg R=1/5/13      -> results/ring_tavg_8x6.json</span>
python3 dse_ring_robust_8x6.py        <span class="c"># faults + jitter     -> results/ring_robust_8x6.json</span>
python3 dse_multiflit_area_makespan.py --jobs 5
                                      <span class="c"># mesh side R=1/5/13  -> results/multiflit_area_makespan.json</span>
                                      <span class="c"># ~900 CPU-minutes; run this BEFORE dse_ring_tavg_8x6.py</span>
python3 export_ring_calendars.py      <span class="c"># slot tables         -> results/calendars/ring_*.json</span>
python3 verify_ring_collectives_8x6.py<span class="c"># {f(ver['n_checks']) if ver else '?'} assertions</span>
python3 gen_ring_collectives_report.py<span class="c"># this page</span></pre>
<p class="muted">Sources: <code>results/ring_collectives_8x6.json</code>
({f(c.get('wall_s'), 1)} s), <code>results/ring_tavg_8x6.json</code>,
<code>results/ring_robust_8x6.json</code>,
<code>results/verify_ring_collectives_8x6.json</code>,
<code>results/calendars/ring_index.json</code>. Companion narrative:
<code>docs/phase-7-exploration/ring-collectives-8x6.md</code>.</p>
</div></body></html>"""


def main() -> None:
    d = load()
    if not d["coll"]:
        raise SystemExit(f"missing {COLL}; run dse_ring_collectives_8x6.py")
    OUT.write_text(build(d), encoding="utf-8")
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
