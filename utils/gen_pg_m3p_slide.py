#!/usr/bin/env python3
"""M3' Up*/Down* best-root deck: mechanism, proof of maximal fault tolerance,
and how far a 1-VC scheme can push load balance.

Diagrams and every number are driven by the real routing code — the figures come
from pg_routing directly, the measured claims from results/pg_m3p_analysis.json
(regenerate with `python3 utils/pg_m3p_analysis.py`), so the deck cannot drift
from the implementation.

  .venv-ppt/bin/python utils/gen_pg_m3p_slide.py --pptx
  python3 utils/gen_pg_m3p_slide.py --png
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

import pg_faults_8x6 as F
from gen_pg_fault_deadlock_slide import (
    BLUE, CARD_BG, CARD_LN, GREEN, GREY, GREY_L, HDR_BG, INK, ORANGE, RED,
    SLIDE_H, SLIDE_W, WHITE, Deck, card, emit_png, emit_pptx, p,
)
from pg_routing import (
    _tree_path, _updown_labels, _updown_table, link_loads, max_link_load,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_PPTX = ROOT / "results" / "pg_m3p_updown_slide.pptx"
OUT_PNG = ROOT / "results" / "pg_m3p_updown_slide.png"
OUT_PNG_PROOF = ROOT / "results" / "pg_m3p_proof_slide.png"
OUT_PNG_SYMBOL = ROOT / "results" / "pg_m3p_symbols_slide.png"
OUT_PNG_THEOREM = ROOT / "results" / "pg_m3p_theorem_slide.png"
OUT_PNG_LIMIT = ROOT / "results" / "pg_m3p_limit_slide.png"
ANALYSIS = ROOT / "results" / "pg_m3p_analysis.json"

DEAD_NODES = [(3, 2), (4, 2)]
DEAD_LINK = ((1, 4), (2, 4))
LABEL_BANDS = ["DCE7F5", "AFD0EA", "7FB4DE", "4E94CC", "2A6FA8"]


def demo_pg() -> dict:
    scen = {
        "name": "m3p_demo",
        "fault_class": "mixed",
        "region": "center",
        "detail": "demo",
        "dead_nodes": [F.nid(x, y) for x, y in DEAD_NODES],
        "dead_links": [(F.nid(*DEAD_LINK[0]), F.nid(*DEAD_LINK[1]))],
        "desc": "2 node holes + 1 link cut",
    }
    return F.expand_pg(scen, "dead")


_DEMO: dict | None = None


def demo_adj() -> dict[int, list[int]]:
    global _DEMO
    if _DEMO is None:
        _DEMO = demo_pg()
    return _DEMO["route_adj"]


def best_root(pg: dict) -> tuple[int, int, int, int]:
    """Return (best_root, best_load, m3_root, m3_load) on this residual graph."""
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    m3_root = max(adj.keys(), key=lambda n: (len(adj[n]), -n))
    m3_load = max_link_load(_updown_table(adj, compute, m3_root, "ud") or {})
    best_r, best_l = m3_root, m3_load
    for r in sorted(adj):
        t = _updown_table(adj, compute, r, "ud")
        if not t:
            continue
        ld = max_link_load(t)
        if ld < best_l:
            best_r, best_l = r, ld
    return best_r, best_l, m3_root, m3_load


def analysis() -> dict:
    if not ANALYSIS.exists():
        raise SystemExit(
            "missing %s — run `python3 utils/pg_m3p_analysis.py` first"
            % ANALYSIS.relative_to(ROOT))
    return json.loads(ANALYSIS.read_text())


# --------------------------------------------------------------------------
# Shared primitives: heat ramp, colour bar, table
# --------------------------------------------------------------------------

HEAT_STOPS = [(0.00, "E9EEF3"), (0.28, "A9C9E4"), (0.52, "F2CF4A"),
              (0.76, "E5822A"), (1.00, "AE2318")]


def heat(t: float) -> str:
    t = min(max(t, 0.0), 1.0)
    for i in range(len(HEAT_STOPS) - 1):
        t0, c0 = HEAT_STOPS[i]
        t1, c1 = HEAT_STOPS[i + 1]
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return "".join(
                "%02X" % round(int(c0[k:k + 2], 16) * (1 - f)
                               + int(c1[k:k + 2], 16) * f)
                for k in (0, 2, 4))
    return HEAT_STOPS[-1][1]


def colorbar_frac(d: Deck, x0, y0, w, h, ticks: list[tuple[float, str]]) -> None:
    """Colour bar whose axis is dimensionless (load ÷ cut bound)."""
    n = 40
    for i in range(n):
        d.rect(x0 + w * i / n, y0, w / n * 1.02, h,
               fill=heat(i / (n - 1.0)), line=None)
    d.rect(x0, y0, w, h, fill=None, line=GREY_L, lw=0.5)
    for frac, lab in ticks:
        tx = x0 + w * min(max(frac, 0.0), 1.0)
        d.line(tx, y0 + h, tx, y0 + h + 0.05, color=GREY, lw=0.6)
        d.text(tx - 0.42, y0 + h + 0.05, 0.84, 0.16,
               [p(lab, size=6.6, color=GREY, align="c", space=0)])


def colorbar(d: Deck, x0, y0, w, h, vmax: int, ticks: list[int]) -> None:
    n = 40
    for i in range(n):
        d.rect(x0 + w * i / n, y0, w / n * 1.02, h,
               fill=heat(i / (n - 1.0)), line=None)
    d.rect(x0, y0, w, h, fill=None, line=GREY_L, lw=0.5)
    for tv in ticks:
        tx = x0 + w * min(tv / vmax, 1.0)
        d.line(tx, y0 + h, tx, y0 + h + 0.05, color=GREY, lw=0.6)
        d.text(tx - 0.30, y0 + h + 0.05, 0.60, 0.16,
               [p(str(tv), size=6.6, color=GREY, align="c", space=0)])


def table(d: Deck, x0, y0, widths, header, rows, *, accent=BLUE, fs=7.4,
          hdr_h=0.26, row_h=0.235, aligns=None, mark=()) -> float:
    """Light grid: header band + one band per row. Returns total height."""
    aligns = aligns or (["l"] + ["c"] * (len(widths) - 1))
    total = sum(widths)
    d.rect(x0, y0, total, hdr_h, fill=HDR_BG, line=None)
    cx = x0
    for wd, htxt, al in zip(widths, header, aligns):
        d.text(cx + 0.04, y0, wd - 0.08, hdr_h,
               [p(htxt, size=fs - 0.2, bold=True, color=INK, align=al,
                  space=0)], valign="middle")
        cx += wd
    y = y0 + hdr_h
    for ri, row in enumerate(rows):
        hl = ri in mark
        d.rect(x0, y, total, row_h,
               fill=("FFF6E8" if hl else (WHITE if ri % 2 == 0 else CARD_BG)),
               line=None)
        if hl:
            d.rect(x0, y, 0.035, row_h, fill=accent, line=None)
        cx = x0
        for ci, (wd, cell, al) in enumerate(zip(widths, row, aligns)):
            col = INK if (ci == 0 or hl) else GREY
            d.text(cx + 0.05, y, wd - 0.10, row_h,
                   [p(str(cell), size=fs, bold=(ci == 0 or hl), color=col,
                      align=al, space=0)], valign="middle")
            cx += wd
        d.line(x0, y, x0 + total, y, color=CARD_LN, lw=0.4)
        y += row_h
    d.rect(x0, y0, total, y - y0, fill=None, line=CARD_LN, lw=0.6)
    return y - y0


def fig_heat(d: Deck, x0, y0, w, h, loads: dict[str, int],
             adj: dict[int, list[int]], vmax: int, *, title: str, sub: str,
             accent: str, root: int | None = None, holes: tuple = (),
             cuts: tuple = (), live: set[int] | None = None,
             sub2: str | None = None) -> None:
    """8x6 grid, one link coloured by its all-to-all load (max of both dirs).

    holes = dead routers (grey cross), cuts = severed links (dashed grey),
    live  = nodes that actually take part in the traffic; everything else is
    drawn hollow so a sacrificed subset is visible at a glance.
    """
    nx, ny = F.MX, F.MY
    gh = h - (0.66 if sub2 else 0.46)
    sx = w / (nx - 1 + 1.3)
    sy = gh / (ny - 1 + 1.3)
    ox, oy = x0 + sx * 0.65, y0 + sy * 0.75
    r = min(sx, sy) * 0.15

    def pos(n):
        cx, cy = F.coord(n)
        return ox + cx * sx, oy + cy * sy

    def val(u, v):
        return max(loads.get("%d-%d" % (u, v), 0),
                   loads.get("%d-%d" % (v, u), 0))

    d.rect(x0, y0 + 0.21, w, gh + 0.06, fill="FDFDFE", line="E1E6EB", lw=0.5)
    d.text(x0, y0, w, 0.20,
           [p(title, size=8.4, bold=True, color=accent, align="c", space=0)])
    segs = []
    for u in sorted(adj):
        for v in adj[u]:
            if v < u:
                continue
            segs.append((val(u, v), u, v))
    for load, u, v in sorted(segs):
        t = load / vmax if vmax else 0.0
        ux, uy = pos(u)
        vx, vy = pos(v)
        d.line(ux, uy, vx, vy,
               color=(heat(t) if load else "DDE3E9"), lw=0.8 + 2.5 * t)
    for u, v in cuts:
        ux, uy = pos(u)
        vx, vy = pos(v)
        d.line(ux, uy, vx, vy, color="9AA5B1", lw=0.9, dash=True)
        mx_, my_ = (ux + vx) / 2, (uy + vy) / 2
        d.line(mx_ - r * 0.7, my_ - r * 0.7, mx_ + r * 0.7, my_ + r * 0.7,
               color=RED, lw=0.9)
        d.line(mx_ - r * 0.7, my_ + r * 0.7, mx_ + r * 0.7, my_ - r * 0.7,
               color=RED, lw=0.9)
    for n in sorted(adj):
        px, py = pos(n)
        if live is None or n in live:
            d.oval(px, py, r, fill="8C99A6", line=None, lw=0)
        else:
            d.oval(px, py, r * 0.92, fill=WHITE, line="B8C1CA", lw=0.7)
    for n in holes:
        px, py = pos(n)
        d.line(px - r, py - r, px + r, py + r, color="6E7A86", lw=1.1)
        d.line(px - r, py + r, px + r, py - r, color="6E7A86", lw=1.1)
    if root is not None:
        rx, ry = pos(root)
        d.star(rx, ry, r * 2.5, fill=RED)
    d.text(x0, y0 + h - (0.44 if sub2 else 0.24), w, 0.24,
           [p(sub, size=7.3, color=GREY, align="c", space=0)])
    if sub2:
        d.text(x0, y0 + h - 0.22, w, 0.22,
               [p(sub2, size=7.3, bold=True, color=accent, align="c",
                  space=0)])


def bfs_tree(adj: dict[int, list[int]], root: int) -> set[frozenset[int]]:
    seen = {root}
    q = deque([root])
    edges = set()
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                edges.add(frozenset((u, v)))
                q.append(v)
    return edges


# --------------------------------------------------------------------------
# Figure A: residual graph, BFS heights, tree re-grown around the holes
# --------------------------------------------------------------------------

def fig_heights(d: Deck, x0, y0, w, h, pg: dict, root: int):
    adj = pg["route_adj"]
    labels = _updown_labels(adj, root) or {}
    tree = bfs_tree(adj, root)
    dead = {F.nid(x, y) for x, y in DEAD_NODES}
    cut = frozenset((F.nid(*DEAD_LINK[0]), F.nid(*DEAD_LINK[1])))

    nx, ny = F.MX, F.MY
    sx = w / (nx - 1 + 1.4)
    sy = (h - 0.34) / (ny - 1 + 1.2)
    ox, oy = x0 + sx * 0.7, y0 + sy * 0.5
    r = min(sx, sy) * 0.21
    lmax = max(labels.values()) if labels else 1

    def pos(n):
        cx, cy = F.coord(n)
        return ox + cx * sx, oy + cy * sy

    for u in sorted(adj):
        for v in adj[u]:
            if v < u:
                continue
            e = frozenset((u, v))
            ux, uy = pos(u)
            vx, vy = pos(v)
            if e in tree:
                d.line(ux, uy, vx, vy, color="7FB4DE", lw=1.5)
            else:
                d.line(ux, uy, vx, vy, color="DCE1E6", lw=0.7)

    ux, uy = pos(F.nid(*DEAD_LINK[0]))
    vx, vy = pos(F.nid(*DEAD_LINK[1]))
    d.line(ux, uy, vx, vy, color=RED, lw=1.1, dash=True)
    d.cross((ux + vx) / 2, (uy + vy) / 2, r * 0.55, color=RED, lw=1.2)

    for n in range(F.N):
        px, py = pos(n)
        if n in dead:
            d.oval(px, py, r, fill=WHITE, line=RED, lw=1.1)
            d.cross(px, py, r * 0.62, color=RED, lw=1.3)
            continue
        lab = labels.get(n)
        band = LABEL_BANDS[min(int(lab / max(lmax, 1) * 4.999), 4)] \
            if lab is not None else "E4E7EB"
        d.oval(px, py, r * 0.92, fill=band, line="FFFFFF", lw=0.5)

    rx, ry = pos(root)
    d.star(rx, ry, r * 1.25, fill=RED)
    d.text(rx + r * 1.6, ry - 0.10, 1.30, 0.20,
           [p("根 = BFS 源点", size=7.6, bold=True, color=RED, space=0)])

    d.text(x0, y0 + h - 0.30, w, 0.32, [
        p("蓝边 = BFS 树（只给高度）　浅灰 = 其余存活链路（路径可用）",
          size=7.8, color=GREY, align="c", space=1.0),
        p("节点颜色由浅到深 = 高度 label 0 → %d" % lmax,
          size=7.8, color=GREY, align="c", space=0)])


# --------------------------------------------------------------------------
# Figure B: up* then down*, and the one forbidden turn
# --------------------------------------------------------------------------

def fig_updown(d: Deck, x0, y0, w, h):
    nx, ny = 4, 3
    sx = w / (nx - 1 + 1.5)
    gh = h - 0.52
    sy = gh / (ny - 1 + 1.4)
    ox, oy = x0 + sx * 0.75, y0 + sy * 0.70
    r = min(sx, sy) * 0.25

    def pos(cx, cy):
        return ox + cx * sx, oy + cy * sy

    def gap(a, b, pad):
        (ax_, ay_), (bx_, by_) = pos(*a), pos(*b)
        dx, dy = bx_ - ax_, by_ - ay_
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / length, dy / length
        return (ax_ + ux * pad, ay_ + uy * pad,
                bx_ - ux * pad, by_ - uy * pad)

    for cy in range(ny):
        for cx in range(nx):
            for dx, dy in ((1, 0), (0, 1)):
                bx, by = cx + dx, cy + dy
                if bx >= nx or by >= ny:
                    continue
                ax_, ay_ = pos(cx, cy)
                bx_, by_ = pos(bx, by)
                d.line(ax_, ay_, bx_, by_, color="DCE1E6", lw=0.8)

    up_seg = [(3, 0), (2, 0), (1, 0)]
    dn_seg = [(1, 0), (1, 1), (1, 2)]
    for seg, col in ((up_seg, BLUE), (dn_seg, ORANGE)):
        for i in range(len(seg) - 1):
            d.line(*gap(seg[i], seg[i + 1], r * 1.12), color=col, lw=2.2,
                   arrow=True)

    bad_a, bad_b = (1, 1), (0, 1)
    ax_, ay_, bx_, by_ = gap(bad_a, bad_b, r * 1.12)
    d.line(ax_, ay_, bx_, by_, color=GREY_L, lw=1.6, dash=True, arrow=True)
    d.cross((ax_ + bx_) / 2, (ay_ + by_) / 2, 0.075, color=RED, lw=1.9)

    for cy in range(ny):
        for cx in range(nx):
            px, py = pos(cx, cy)
            lab = cx + cy
            fill = LABEL_BANDS[min(lab, 4)]
            d.oval(px, py, r, fill=fill, line="FFFFFF", lw=0.6)
            d.text(px - r, py - r * 0.62, r * 2, r * 1.3,
                   [p(str(lab), size=8.2, bold=True,
                      color=INK if lab < 3 else WHITE, align="c", space=0)])

    rx, ry = pos(0, 0)
    d.star(rx - r * 1.30, ry - r * 1.30, r * 0.58, fill=RED)
    d.text(rx - r - 0.62, ry - r * 2.0 - 0.06, 0.60, 0.18,
           [p("根", size=7.6, bold=True, color=RED, align="r", space=0)])

    sxp, syp = pos(*up_seg[0])
    dxp, dyp = pos(*dn_seg[-1])
    d.text(sxp - 0.30, syp - r - 0.22, 0.60, 0.18,
           [p("S", size=8.4, bold=True, color=BLUE, align="c", space=0)])
    d.text(dxp - 0.30, dyp + r + 0.04, 0.60, 0.18,
           [p("D", size=8.4, bold=True, color=ORANGE, align="c", space=0)])
    tx, ty = pos(*up_seg[-1])
    d.text(tx - 0.55, ty - r - 0.22, 1.10, 0.18,
           [p("唯一拐点", size=7.6, bold=True, color=GREEN, align="c",
              space=0)])
    d.text(bx_ - 0.66, by_ + r * 0.9, 1.24, 0.20,
           [p("禁 down→up", size=7.6, bold=True, color=RED, align="c",
              space=0)])

    d.text(x0, y0 + h - 0.44, w, 0.46, [
        p("蓝 = up（label 递减，朝根）　橙 = down（label 递增，离根）",
          size=7.8, color=GREY, align="c", space=1.0),
        p("合法路径形如 up* → down*，拐点最多一个",
          size=8.4, bold=True, color=INK, align="c", space=0)])


# --------------------------------------------------------------------------
# Figure C: same construction, two roots, different link load
# --------------------------------------------------------------------------

def fig_root_compare(d: Deck, x0, y0, w, h, pg: dict, root: int, load: int,
                     *, title: str, accent: str):
    adj, compute = pg["route_adj"], pg["compute_nodes"]
    paths = _updown_table(adj, compute, root, "ud") or {}
    ld = link_loads(paths)
    both = {}
    for u in sorted(adj):
        for v in adj[u]:
            if v < u:
                continue
            both[(u, v)] = ld.get((u, v), 0) + ld.get((v, u), 0)
    peak = max(both.values()) if both else 1
    dead = {F.nid(x, y) for x, y in DEAD_NODES}

    nx, ny = F.MX, F.MY
    gh = h - 0.26
    sx = w / (nx - 1 + 1.4)
    sy = gh / (ny - 1 + 1.4)
    ox, oy = x0 + sx * 0.7, y0 + sy * 0.7
    r = min(sx, sy) * 0.17

    def pos(n):
        cx, cy = F.coord(n)
        return ox + cx * sx, oy + cy * sy

    hot = []
    for (u, v), load_uv in both.items():
        ux, uy = pos(u)
        vx, vy = pos(v)
        if load_uv >= 0.8 * peak:
            hot.append((ux, uy, vx, vy))
        else:
            d.line(ux, uy, vx, vy, color="E4E7EB", lw=0.6)
    for ux, uy, vx, vy in hot:
        d.line(ux, uy, vx, vy, color=RED, lw=1.9)

    for n in range(F.N):
        if n in dead:
            continue
        px, py = pos(n)
        d.oval(px, py, r, fill="AEB8C2", line=None, lw=0)
    for x, y in DEAD_NODES:
        px, py = pos(F.nid(x, y))
        d.cross(px, py, r * 1.1, color=RED, lw=1.0)

    rx, ry = pos(root)
    d.star(rx, ry, r * 2.3, fill=accent)

    d.text(x0, y0 + h - 0.26, w, 0.30, [
        p(title, size=8.0, bold=True, color=accent, align="c", space=0.5),
        p("峰值链路负载 %d" % load, size=8.0, bold=True, color=accent,
          align="c", space=0)])


# --------------------------------------------------------------------------
# Slide composition
# --------------------------------------------------------------------------

def build() -> Deck:
    pg = demo_pg()
    br, bl, m3r, m3l = best_root(pg)
    gain = round(100 * (m3l - bl) / m3l)

    d = Deck()
    d.rect(0, 0, SLIDE_W, SLIDE_H, fill=WHITE, line=None)

    d.rect(0, 0, SLIDE_W, 0.92, fill=INK, line=None)
    d.rect(0, 0, 0.10, 0.92, fill=RED, line=None)
    d.text(0.34, 0.10, 11.4, 0.44,
           [p("M3′ Up*/Down* best-root：怎么容错、怎么解死锁",
              size=21, bold=True, color=WHITE, space=0)])
    d.text(0.36, 0.55, 12.6, 0.30,
           [p("单一机制：给每个 router 一个「到根的高度」，只禁一种转向 —— "
              "物理 1 VC · 无需虚通道 · 8×6 预算故障 44 场景",
              size=10.0, color="C3CBD4", space=0)])

    ry, rh = 1.06, 3.32
    cw1 = 6.28
    card(d, 0.30, ry, cw1, rh, "① 如何容错：高度重算，洞自动被绕开", accent=GREEN)
    fig_heights(d, 0.44, ry + 0.40, 2.72, 2.66, pg, br)
    d.text(3.28, ry + 0.42, 3.16, rh - 0.52, [
        p("① 只在残图上做 BFS", size=9.6, bold=True, color=GREEN, space=1.0),
        p("死 router / 断链不进邻接表，label = 到根的跳数由残图算出，"
          "树自然从洞的两侧绕过去。", size=8.8, color=GREY, space=4.0),
        p("② 不依赖方向，所以能绕", size=9.6, bold=True, color=GREEN, space=1.0),
        p("XY / East-first 把某类方向写死，洞挡住即无解；Up*/Down* 只看"
          "高度差，四个方向都能走 ⇒ 残图连通就建得出表。",
          size=8.8, color=GREY, space=4.0),
        p("③ 路径不限于树边", size=9.6, bold=True, color=GREEN, space=1.0),
        p("树只用来定高度，任意存活链路都能走，不会退化成树上绕远。",
          size=8.8, color=GREY, space=4.0),
        p("④ 只有残图断开才牺牲", size=9.6, bold=True, color=ORANGE,
          space=1.0),
        p("44 场景牺牲中位 0 / 最差 4；36/36 目录格零牺牲。",
          size=8.8, color=ORANGE, space=0),
    ])

    cx2 = 0.30 + cw1 + 0.16
    cw2 = SLIDE_W - cx2 - 0.30
    card(d, cx2, ry, cw2, rh, "② 如何解死锁：禁 down→up，高度严格单调",
         accent=RED)
    fig_updown(d, cx2 + 0.14, ry + 0.40, 2.66, 2.66)
    d.text(cx2 + 2.96, ry + 0.42, cw2 - 3.12, rh - 0.52, [
        p("唯一规则", size=9.6, bold=True, color=RED, space=1.0),
        p("先沿 label 递减方向走（up），一旦开始递增（down）就再也不许 up。"
          "实现是带相位的 BFS：相位 1 里禁掉所有 up 边。",
          size=8.8, color=GREY, space=4.0),
        p("为什么这样就无死锁", size=9.6, bold=True, color=RED, space=1.0),
        p("mesh 按 x+y 奇偶是二分图 ⇒ 相邻节点 label 必差 1，没有同层边，"
          "于是 up 通道 label 严格递减、down 严格递增。",
          size=8.8, color=GREY, space=3.0),
        p("CDG 只剩 up→up、up→down、down→down：段内 label 单调、跨段只能 "
          "up→down 单向 ⇒ 依赖链回不到起点，无环（构造性，1 VC）。",
          size=8.8, color=GREY, space=3.0),
        p("→ 实测：48 个根 × 健康图与故障残图，同层边 0 条、CDG 成环 0 例。",
          size=8.8, bold=True, color=GREEN, space=0),
    ])

    ty, th = 4.52, 1.72
    card(d, 0.30, ty, SLIDE_W - 0.60, th,
         "③ M3′ 相对 M3 的唯一增量：把「根」当自由参数搜一遍（可行性与无死锁完全不变）",
         accent=BLUE, hdr_h=0.32)
    fig_root_compare(d, 0.46, ty + 0.34, 2.05, th - 0.40, pg, m3r, m3l,
                     title="M3：根 = 度最大点", accent=GREY)
    fig_root_compare(d, 2.62, ty + 0.34, 2.05, th - 0.40, pg, br, bl,
                     title="M3′：根 = 负载最优", accent=BLUE)
    d.text(4.90, ty + 0.38, 4.05, th - 0.46, [
        p("搜索方式", size=9.4, bold=True, color=BLUE, space=1.0),
        p("枚举残图上每个存活 router 当根 → 各建一张完整表 → 全表校验 → "
          "按 (max_link_load, 总跳数, root id) 取最小。",
          size=8.8, color=GREY, space=3.0),
        p("离线一次算完，运行时仍是一张静态表，不增加任何硬件。",
          size=8.8, color=INK, space=0),
    ])
    d.text(9.10, ty + 0.38, SLIDE_W - 9.10 - 0.46, th - 0.46, [
        p("为什么换根有效", size=9.4, bold=True, color=BLUE, space=1.0),
        p("根附近的链路要承担所有跨区流量。根放在度最大的中心点时，"
          "全阵流量挤过中心「脊」；把根挪到边角，高度层沿对角展开，"
          "峰值链路负载显著下降。",
          size=8.8, color=GREY, space=3.0),
        p("本例（2 洞 + 1 断链）：峰值 %d → %d，低 %d%%。"
          % (m3l, bl, gain), size=8.8, bold=True, color=GREEN, space=0),
    ])

    by, bh = 6.40, 0.90
    d.rect(0.30, by, SLIDE_W - 0.60, bh, fill="FBF0F0", line=RED, lw=0.9,
           round_=0.04)
    d.rect(0.30, by, 0.055, bh, fill=RED, line=None)
    d.text(0.48, by + 0.10, 5.80, bh - 0.16, [
        p("第三条性质：保序", size=9.6, bold=True, color=RED, space=1.5),
        p("每对源宿的路径由离线 BFS 唯一确定，运行时不自适应、单 VC 不换道 "
          "⇒ 同一对的 flit 天然按序到达。", size=8.8, color=INK, space=0),
    ])
    d.text(6.55, by + 0.10, SLIDE_W - 6.55 - 0.45, bh - 0.16, [
        p("端到端收益（44 场景，物理 1 VC，area 0.897，与 M3 同硬件）",
          size=9.6, bold=True, color=RED, space=1.5),
        p("最差 T_e2e：M3 893 ns → M3′ 790 ns（−11.5%）；中位 621 → 574 ns。"
          "轻载 m0=1 时 M3′ 是 1 VC 方案里的 Pareto 前沿点。",
          size=8.8, color=INK, space=0),
    ])

    return d


# --------------------------------------------------------------------------
# Slide 2: the proof, and the price paid in load balance
# --------------------------------------------------------------------------

def hot_spot_stats(loads: dict[str, int], adj, root: int, k: int = 10) -> dict:
    labels = _updown_labels(adj, root) or {}
    segs = []
    for u in sorted(adj):
        for v in adj[u]:
            if v < u:
                continue
            segs.append((max(loads.get("%d-%d" % (u, v), 0),
                             loads.get("%d-%d" % (v, u), 0)), u, v))
    segs.sort(reverse=True)
    top = segs[:k]
    mins = [min(labels.get(u, 99), labels.get(v, 99)) for _, u, v in top]
    return {
        "near_root": sum(1 for m in mins if m <= 2),
        "k": k,
        "lo": min(mins),
        "hi": max(mins),
        "mid": sum(1 for m in mins if 3 <= m <= 6),
        "lmax": max(labels.values()) if labels else 0,
        "peak_label": mins[0],
    }


def build_proof() -> Deck:
    A = analysis()
    H, DM = A["healthy"], A["demo"]
    S, SD = H["schemes"], DM["schemes"]
    T44 = A["theorem44"]
    TM = A["turnmodel44"]
    MX_ = A["maximality"]["healthy"]
    healthy = F.healthy_pg()
    adj = healthy["route_adj"]
    root = H["root"]
    lb = H["lb"]
    XB = DM["xy_best"]
    dadj = demo_adj()
    dholes = tuple(DM["dead_nodes"])
    dcuts = tuple(tuple(e) for e in DM["dead_links"])
    droot, dlb = DM["root"], DM["lb"]
    hsd = hot_spot_stats(DM["loads"]["m3p"], dadj, droot)
    vmax = SD["m3p"]["peak"]

    d = Deck()
    d.rect(0, 0, SLIDE_W, SLIDE_H, fill=WHITE, line=None)
    d.rect(0, 0, SLIDE_W, 0.92, fill=INK, line=None)
    d.rect(0, 0, 0.10, 0.92, fill=GREEN, line=None)
    d.text(0.34, 0.10, 12.6, 0.44,
           [p("M3′ vs 方位型 turn model：同一残图的负载热点（同为 1 VC）",
              size=21, bold=True, color=WHITE, space=0)])
    d.text(0.36, 0.55, 12.6, 0.30,
           [p("44 场景零牺牲：XY %d、west-first %d、neg-first %d、M3′ %d　·　"
              "M3′ 的代价：峰值 %.2f× 割界，min-max 选路降到 %.2f×"
              % (TM["xy"]["zero_sacrifice"],
                 TM["west_first"]["zero_sacrifice"],
                 TM["negative_first"]["zero_sacrifice"],
                 TM["m3p"]["zero_sacrifice"],
                 SD["m3p"]["peak_over_lb"], SD["m3p_minmax"]["peak_over_lb"]),
              size=10.0, color="C3CBD4", space=0)])

    # --- row 1: four heat panels on the same fault scenario ---------------
    hy, hh = 1.00, 3.04
    card(d, 0.30, hy, SLIDE_W - 0.60, hh,
         "① all-to-all 链路负载热点（1 VC）：后三幅同一 partial good，46 个好节点"
         "全参与、牺牲 0；各按自身割界归一", accent=RED, hdr_h=0.30)
    pw, ph, gap = 2.95, 2.12, 0.22
    px0, py0 = 0.44, hy + 0.38
    fig_heat(d, px0, py0, pw, ph, H["loads"]["west_first"], adj, 2 * lb,
             title="参照：west-first（无故障）", accent=GREY,
             sub="方位型里最均衡：峰值 %d = %.2f× 割界"
                 % (S["west_first"]["peak"], S["west_first"]["peak_over_lb"]),
             sub2="但残图上不可路由：零牺牲 %d/%d"
                  % (TM["west_first"]["zero_sacrifice"], TM["xy"]["n"]))
    fig_heat(d, px0 + (pw + gap), py0, pw, ph, DM["loads"]["negative_first"],
             dadj, 2 * dlb, title="negative-first，同一残图", accent=BLUE,
             holes=dholes, cuts=dcuts,
             sub="方位型里唯一扛住本场景的，零牺牲 %d/%d"
                 % (TM["negative_first"]["zero_sacrifice"], TM["xy"]["n"]),
             sub2="峰值 %d = %.2f× 割界 %d"
                  % (SD["negative_first"]["peak"],
                     SD["negative_first"]["peak_over_lb"], dlb))
    fig_heat(d, px0 + 2 * (pw + gap), py0, pw, ph, DM["loads"]["m3p"], dadj,
             2 * dlb, title="M3′ best-root，同一残图", accent=RED, root=droot,
             holes=dholes, cuts=dcuts,
             sub="★ = 根 (7,5)；零牺牲 %d/%d"
                 % (TM["m3p"]["zero_sacrifice"], TM["xy"]["n"]),
             sub2="峰值 %d = %.2f× 割界（走廊拥塞）"
                  % (SD["m3p"]["peak"], SD["m3p"]["peak_over_lb"]))
    fig_heat(d, px0 + 3 * (pw + gap), py0, pw, ph, DM["loads"]["m3p_minmax"],
             dadj, 2 * dlb, title="M3′ + min-max 选路，同一残图", accent=GREEN,
             root=droot, holes=dholes, cuts=dcuts,
             sub="同一转向集内换路径，容错不变",
             sub2="峰值 %d = %.2f× 割界（−%.0f%%）"
                  % (SD["m3p_minmax"]["peak"], SD["m3p_minmax"]["peak_over_lb"],
                     100 * (1 - SD["m3p_minmax"]["peak"] / SD["m3p"]["peak"])))
    colorbar_frac(d, 0.66, hy + 2.58, 2.90, 0.12,
                  [(0.0, "0"), (0.25, "0.5×"), (0.5, "1.0× 割界"),
                   (0.75, "1.5×"), (1.0, "≥2.0×")])
    d.text(4.06, hy + 2.54, 8.84, 0.42, [
        p("色标 = 该链路承载的 (s,d) 对数 ÷ 本场景割界（两方向取大者）；割界 = "
          "任何路由都突破不了的最小可能峰值，所以「÷割界」才是跨场景可比的量。"
          "× = 死 router (3,2)/(4,2)，红叉虚线 = 断链 (1,4)–(2,4)，★ = 根。"
          "XY 与 west-first 在这张残图上无法覆盖全部 (s,d)，只能牺牲好节点，故无图。"
          "M3′ 最热 %d 条链路的 ℓ 落在 %d–%d（其中 %d 条在 ℓ=3–6，ℓ 最大 %d）："
          "拥塞不在根的邻边，而在洞左侧那条通往根的主干走廊上。"
          % (hsd["k"], hsd["lo"], hsd["hi"], hsd["mid"], hsd["lmax"]),
          size=7.0, color=GREY, space=0),
    ])

    ty, th = 4.14, 2.06
    tw = 8.10
    card(d, 0.30, ty, tw, th,
         "② 同为 1 VC 的 turn model 横向对比", accent=RED, hdr_h=0.30)
    widths = [1.66, 1.26, 0.96, 0.58, 0.50, 0.50, 0.56, 0.58, 1.05]
    header = ["方案", "禁令怎么定", "参与节点", "峰值", "÷割界", "CV",
              "mk1", "mk13", "44 场景零牺牲"]

    def row(tag, ban, s, nodes, sac):
        def mk(k):
            v = s.get(k)
            return "%s" % v if v else "—"

        return [tag, ban, nodes, s["peak"], "%.2f×" % s["peak_over_lb"],
                "%.2f" % s["cv"], mk("makespan_m1"), mk("makespan_m13"), sac]

    n44 = TM["xy"]["n"]
    rows = [
        row("XY，无故障", "N→E/W、S→E/W", S["xy"], "48/48",
            "%d/%d" % (TM["xy"]["zero_sacrifice"], n44)),
        row("west-first，无故障", "N→W、S→W", S["west_first"], "48/48",
            "%d/%d" % (TM["west_first"]["zero_sacrifice"], n44)),
        row("neg-first，无故障", "E→S、N→W", S["negative_first"],
            "48/48", "%d/%d" % (TM["negative_first"]["zero_sacrifice"], n44)),
        row("neg-first，残图", "同上（与洞无关）", SD["negative_first"],
            "46/46", "%d/%d" % (TM["negative_first"]["zero_sacrifice"], n44)),
        row("M3′，残图", "down→up（按 ℓ）", SD["m3p"], "46/46",
            "%d/%d" % (TM["m3p"]["zero_sacrifice"], n44)),
        row("M3′+min-max，残图", "同上 + 换选路", SD["m3p_minmax"], "46/46",
            "%d/%d" % (TM["m3p"]["zero_sacrifice"], n44)),
        ["XY / wf，残图", "方位型", "牺牲 %d/%d"
         % (XB["n_sacrificed"], XB["n_good"]), "—", "—", "—", "—", "—",
         "0/44"],
    ]
    table(d, 0.42, ty + 0.34, widths, header, rows, accent=RED, fs=7.0,
          hdr_h=0.24, row_h=0.208, mark=(5,))

    cx3 = 0.30 + tw + 0.16
    cw3 = SLIDE_W - cx3 - 0.30
    card(d, cx3, ty, cw3, th, "③ 这四幅图该怎么读", accent=BLUE, hdr_h=0.30)
    d.text(cx3 + 0.14, ty + 0.36, cw3 - 0.28, th - 0.42, [
        p("方位型 turn model 更均衡，但容错靠不住。", size=8.4, bold=True,
          color=BLUE, space=1.0),
        p("禁令写死在方向上、与洞无关：残图一有洞就可能整片 (s,d) 断供 —— "
          "XY / west-first 零牺牲 0/44、neg-first %d/44；M3′ 的禁令按 ℓ 随残图重算，"
          "%d/44（余 3 例残图本身断开）。"
          % (TM["negative_first"]["zero_sacrifice"],
             TM["m3p"]["zero_sacrifice"]),
          size=8.0, color=GREY, space=2.0),
        p("M3′ 的代价不是绕远，是选路集中。", size=8.4, bold=True, color=RED,
          space=1.0),
        p("无故障时 M3′ 与 XY 总跳数都是 %d、平均 %.2f 跳（都走最短路）⇒ 不均衡纯粹"
          "来自「同一最短路集合里怎么挑」，换选路即可改善（第 5 页）。"
          % (S["m3p"]["hops"], S["m3p"]["avg_hops"]),
          size=8.0, color=GREY, space=2.0),
        p("坦白说：本场景重载 neg-first %s cy 快于 M3′+min-max %s cy，但它只在 "
          "%d/%d 场景可用。"
          % (SD["negative_first"]["makespan_m13"],
             SD["m3p_minmax"]["makespan_m13"],
             TM["negative_first"]["zero_sacrifice"], TM["xy"]["n"]),
          size=8.0, bold=True, color=ORANGE, space=0),
    ])

    by, bh = 6.28, 1.02
    d.rect(0.30, by, SLIDE_W - 0.60, bh, fill="F2F7F4", line=GREEN, lw=0.9,
           round_=0.04)
    d.rect(0.30, by, 0.055, bh, fill=GREEN, line=None)
    d.text(0.48, by + 0.10, 6.10, bh - 0.18, [
        p("定理 1 实测校核（%d 场景 ≤4 router / ≤8 link）" % T44["n"],
          size=9.4, bold=True, color=GREEN, space=1.2),
        p("%d 个残图连通的场景全部零牺牲、CDG 成环 0 例、反例 %d 个；余下 %d 个"
          "场景残图本身断开（分别孤立 %s 个好节点），牺牲量等于信息论下界。同批"
          "场景 XY 零牺牲 %d/%d。"
          % (T44["n_connected"], T44["violations"], T44["n_disconnected"],
             "/".join(str(x) for x in T44["disconnected_isolated"]),
             T44["xy_zero_sacrifice"], T44["n"]),
          size=8.2, color=INK, space=0),
    ])
    d.text(6.80, by + 0.10, 3.30, bh - 0.18, [
        p("定理 2（极大性）＝ 没有更宽松的 turn model", size=9.4, bold=True,
          color=GREEN, space=1.2),
        p("M3′ 允许 %d/%d 个转向，与 west-first / neg-first 同级；再放开任一条 "
          "down→up 即成环。"
          % (MX_["permitted_min"], MX_["total_turns"]),
          size=8.2, color=INK, space=0),
    ])
    d.text(10.30, by + 0.10, SLIDE_W - 10.30 - 0.45, bh - 0.18, [
        p("下一页", size=9.4, bold=True, color=GREEN, space=1.2),
        p("符号（u/v/w、通道势能 Φ）见第 3 页，两条定理的逐步推导见第 4 页；"
          "min-max 算法与 1 VC 边界见第 5 页。", size=8.2, color=INK, space=0),
    ])
    return d


# --------------------------------------------------------------------------
# Slide 3: what every symbol in the two theorems means, step by step
# --------------------------------------------------------------------------

def phi_example(root: int, src_xy=(0, 0), dst_xy=(3, 4)) -> dict:
    """One real up*·down* path with its labels and channel potentials.

    Taken straight from the routing code, then checked here: the potential must
    increase strictly along the path, which is exactly the deadlock argument.
    """
    adj = F.healthy_pg()["route_adj"]
    lab = _updown_labels(adj, root) or {}
    s, dst = F.nid(*src_xy), F.nid(*dst_xy)
    path = _tree_path(s, dst, adj, lab, "ud")
    rows = []
    for u, v in zip(path, path[1:]):
        up = lab[v] < lab[u]
        rows.append({"u": u, "v": v, "up": up, "lu": lab[u], "lv": lab[v],
                     "phi": (0, -lab[v]) if up else (1, lab[v])})
    ok = all(a["phi"] < b["phi"] for a, b in zip(rows, rows[1:]))
    turn = next((i for i, r in enumerate(rows) if not r["up"]), len(rows))
    return {"path": path, "labels": [lab[n] for n in path], "rows": rows,
            "strict": ok, "turn_at": rows[turn - 1]["lv"] if turn else None,
            "root_label": 0, "lmax": max(lab.values())}


def fig_stairs(d: Deck, x0, y0, w, h, ex: dict) -> None:
    """Label profile of the example path: up* segment then down* segment."""
    ls = ex["labels"]
    n = len(ls)
    lo, hi = min(ls), max(ls)
    sx = w / (n - 1 + 0.6)
    gh = h - 0.50
    sy = gh / max(hi - lo, 1)
    ox = x0 + sx * 0.3
    oy = y0 + 0.24
    r = min(sx * 0.30, 0.085)

    def pos(i):
        return ox + i * sx, oy + (ls[i] - lo) * sy

    for i in range(n - 1):
        ax, ay = pos(i)
        bx, by = pos(i + 1)
        up = ls[i + 1] < ls[i]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) ** 0.5 or 1.0
        k = r * 1.25 / L
        d.line(ax + dx * k, ay + dy * k, bx - dx * k, by - dy * k,
               color=(GREEN if up else ORANGE), lw=1.6, arrow=True)
    for i in range(n):
        px, py = pos(i)
        band = LABEL_BANDS[min(int(ls[i] / max(ex["lmax"], 1) * 4.999), 4)]
        d.oval(px, py, r, fill=band, line=WHITE, lw=0.6)
        d.text(px - r, py - r * 0.66, r * 2, r * 1.4,
               [p(str(ls[i]), size=6.2, bold=True,
                  color=(WHITE if ls[i] >= 4 else INK), align="c", space=0)])
    d.text(x0, y0 + h - 0.22, w * 0.5, 0.20,
           [p("↑ up：ℓ−1", size=7.0, bold=True, color=GREEN, align="c",
              space=0)])
    d.text(x0 + w * 0.5, y0 + h - 0.22, w * 0.5, 0.20,
           [p("↓ down：ℓ+1", size=7.0, bold=True, color=ORANGE, align="c",
              space=0)])


def fig_turn3(d: Deck, x0, y0, w, h) -> None:
    """What (u->v->w) means: two back-to-back channels = one turn at v."""
    r = min(w * 0.10, h * 0.20)
    cy = y0 + h * 0.42
    pts = [(x0 + w * 0.12, cy), (x0 + w * 0.50, cy),
           (x0 + w * 0.88, cy - h * 0.30)]
    for (ax, ay), (bx, by), col, lab in (
            (pts[0], pts[1], ORANGE, "down"),
            (pts[1], pts[2], GREEN, "up")):
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) ** 0.5 or 1.0
        k = r * 1.3 / L
        d.line(ax + dx * k, ay + dy * k, bx - dx * k, by - dy * k, color=col,
               lw=1.5, arrow=True)
        d.text((ax + bx) / 2 - 0.24, (ay + by) / 2 - r * 1.9, 0.48, 0.16,
               [p(lab, size=6.4, bold=True, color=col, align="c", space=0)])
    for (px, py), tag in zip(pts, ("u", "v", "w")):
        d.oval(px, py, r, fill=("4E94CC" if tag == "v" else "AFD0EA"),
               line=WHITE, lw=0.6)
        d.text(px - r, py - r * 0.66, r * 2, r * 1.4,
               [p(tag, size=6.8, bold=True,
                  color=(WHITE if tag == "v" else INK), align="c", space=0)])
    d.text(x0, y0 + h - 0.20, w, 0.20,
           [p("在 v 处的一个转向 (u→v→w)", size=6.8, color=GREY, align="c",
              space=0)])


def fig_cdg_ring(d: Deck, x0, y0, w, h) -> None:
    """The dependency cycle that appears the moment one down->up is released."""
    wb, hb = w * 0.44, 0.26
    boxes = {
        "A": (x0, y0 + 0.04, "(v→w) up", GREEN),
        "B": (x0 + w - wb, y0 + 0.04, "(·→r) up", GREEN),
        "C": (x0 + w - wb, y0 + h - 0.46, "(r→·) down", ORANGE),
        "D": (x0, y0 + h - 0.46, "(u→v) down", ORANGE),
    }
    for bx, by, lab, col in boxes.values():
        d.rect(bx, by, wb, hb, fill="FDFDFE", line=col, lw=0.8)
        d.text(bx, by + 0.04, wb, 0.20,
               [p(lab, size=6.6, bold=True, color=col, align="c", space=0)])
    mid_y = y0 + h * 0.42
    ax, ay, _, _ = boxes["A"]
    cx, cy, _, _ = boxes["C"]
    d.line(ax + wb + 0.03, ay + hb * 0.5, cx - 0.03, ay + hb * 0.5,
           color=GREY, lw=1.2, arrow=True)
    d.line(cx + wb * 0.5, ay + hb + 0.03, cx + wb * 0.5, cy - 0.03,
           color=GREY, lw=1.2, arrow=True)
    d.line(cx - 0.03, cy + hb * 0.5, ax + wb + 0.03, cy + hb * 0.5,
           color=GREY, lw=1.2, arrow=True)
    d.line(ax + wb * 0.5, cy - 0.03, ax + wb * 0.5, ay + hb + 0.03,
           color=RED, lw=1.6, dash=True, arrow=True)
    for tag, tx, ty_ in (("①", ax + wb + (cx - ax - wb) * 0.5 - 0.07,
                          ay + hb * 0.5 - 0.20),
                         ("②", cx + wb * 0.5 + 0.03, mid_y - 0.10),
                         ("③", ax + wb + (cx - ax - wb) * 0.5 - 0.07,
                          cy + hb * 0.5 + 0.02),
                         ("④", ax + wb * 0.5 - 0.20, mid_y - 0.10)):
        d.text(tx, ty_, 0.20, 0.16,
               [p(tag, size=7.0, bold=True,
                  color=(RED if tag == "④" else GREY), align="c", space=0)])
    d.text(x0, y0 + h - 0.18, w, 0.18,
           [p("④ 一放开 ⇒ 闭环", size=6.8, bold=True, color=RED, align="c",
              space=0)])


def build_symbols() -> Deck:
    A = analysis()
    H = A["healthy"]
    DM = A["demo"]
    ex = phi_example(H["root"])

    d = Deck()
    d.rect(0, 0, SLIDE_W, SLIDE_H, fill=WHITE, line=None)
    d.rect(0, 0, SLIDE_W, 0.92, fill=INK, line=None)
    d.rect(0, 0, 0.10, 0.92, fill=BLUE, line=None)
    d.text(0.34, 0.10, 12.6, 0.44,
           [p("符号与概念：G′ · r · ℓ(v) · 转向 (u→v→w) · 通道势能 Φ(c)",
              size=20, bold=True, color=WHITE, space=0)])
    d.text(0.36, 0.55, 12.6, 0.30,
           [p("下一页两条定理只用到这五个记号；其中 (u→v→w) 就是「拐弯」，"
              "Φ(c) 是只为证明服务的编号，硬件里并不存在",
              size=10.0, color="C3CBD4", space=0)])

    # --- ① symbols -------------------------------------------------------
    ry, rh = 1.00, 2.90
    cw1 = 6.20
    card(d, 0.30, ry, cw1, rh, "① 五个记号（数字取自本例：8×6、2 洞 + 1 断链）",
         accent=BLUE, hdr_h=0.30)
    d.text(0.42, ry + 0.36, 2.86, rh - 0.42, [
        p("G=(V,E)　原始 8×6 mesh：|V|=48 个 router，|E|=82 条链路，"
          "即 164 条有向通道。", size=7.8, color=GREY, space=2.0),
        p("F　故障集：死 router ∪ 断链（本例 2 个 router + 1 条链路）。",
          size=7.8, color=GREY, space=2.0),
        p("G′=(V′,E′)　残图 = 从 G 删掉 F 及其附属链路后剩下的图（本例 |V′|=%d、"
          "|E′|=%d）。所有推导都在 G′ 上进行。" % (DM["n_compute"], 74),
          size=7.8, bold=True, color=INK, space=2.0),
        p("r ∈ V′　根：一个普通存活 router，被选作 BFS 源点。best-root 把 %d 个候选"
          "全试一遍，取峰值最小者（本例 (7,5)）。" % A["maximality"]["healthy"]["n_roots"],
          size=7.8, bold=True, color=INK, space=2.0),
        p("ℓ(v)　高度：G′ 上 r 到 v 的 BFS 跳数，ℓ(r)=0（本例 0…%d）。"
          "它只是个整数标号，不是坐标、也不是延迟。" % ex["lmax"],
          size=7.8, bold=True, color=INK, space=0),
    ])
    d.text(3.38, ry + 0.36, 2.94, 1.44, [
        p("u, v, w　残图里任意三个节点，v 与 u、w 都相邻。(u→v) 与 (v→w) 是两条"
          "首尾相接的有向通道，合起来就是「报文在 v 处拐的那个弯」，记作转向 "
          "(u→v→w)。8×6 残图上共 %d 个转向。" % DM["total_turns"],
          size=7.8, bold=True, color=INK, space=2.0),
        p("up / down　给每条有向通道贴的标签：(u→v) 若 ℓ(v)=ℓ(u)−1 记 up（朝根），"
          "若 ℓ(v)=ℓ(u)+1 记 down（离根）。mesh 按 x+y 奇偶二分 ⇒ 相邻点 ℓ 必差 1，"
          "不存在 ℓ 相等的「横向」边。", size=7.8, color=GREY, space=0),
    ])
    fig_turn3(d, 3.44, ry + 1.86, 1.60, 0.86)
    d.text(5.10, ry + 1.88, 1.24, 0.86, [
        p("M3′ 的唯一禁令：(u→v)=down 且 (v→w)=up，即「下完又往上拐」。",
          size=7.2, color=RED, space=0),
    ])

    # --- ② the channel potential ------------------------------------------
    cx2 = 0.30 + cw1 + 0.14
    cw2 = SLIDE_W - cx2 - 0.30
    card(d, cx2, ry, cw2, rh, "② 「通道势能 Φ(c)」是什么、为什么需要它",
         accent=ORANGE, hdr_h=0.30)
    d.text(cx2 + 0.14, ry + 0.36, cw2 - 0.28, rh - 0.42, [
        p("一句话　Φ 是给每一条有向通道（不是节点）人为编的一个「序号」，"
          "纯粹为证明服务：不进硬件、不占存储、运行时不存在。",
          size=8.0, bold=True, color=ORANGE, space=2.0),
        p("为什么需要　死锁 ⇔ 通道依赖图 CDG 里有环。要证明「无环」，最省事的办法"
          "是找一个沿依赖边单调变化的量：只要每一次合法衔接都让 Φ 严格变大，就"
          "永远回不到起点，环自然不存在 —— 相当于「只上不下的楼梯不可能绕回原地」"
          "（形式上即势能 / Lyapunov 函数）。", size=8.0, color=GREY, space=2.0),
        p("M3′ 的取法　Φ(u→v) = (0, −ℓ(v)) 若该通道是 up；(1, +ℓ(v)) 若是 down。"
          "两位数按字典序比较：先比第一位，相同才比第二位。",
          size=8.0, bold=True, color=INK, space=2.0),
        p("两位各管一件事　第一位是「阶段位」：0 = 还在上行，1 = 已转入下行 —— "
          "它保证一旦下行就再不能上行。第二位是同阶段内的先后：上行段里 ℓ 越小越"
          "靠后（越接近根），下行段里 ℓ 越大越靠后（越远离根）。",
          size=8.0, color=GREY, space=2.0),
        p("于是三种合法衔接都严格升 Φ：up→up 是 (0,−ℓ) → (0,−ℓ+1)，"
          "up→down 是 (0,·) → (1,·)，down→down 是 (1,ℓ) → (1,ℓ+1)；"
          "唯一被禁的 down→up 恰好是 (1,·) → (0,·)，即会让 Φ 变小的那一种。",
          size=8.0, color=GREY, space=0),
    ])

    # --- ③ worked example -------------------------------------------------
    ey, eh = 4.02, 3.24
    ew = 8.40
    card(d, 0.30, ey, ew, eh,
         "③ 实例校核：从真实路由表里取 (0,0)→(3,4)，逐跳看 ℓ 与 Φ",
         accent=GREEN, hdr_h=0.30)
    fig_stairs(d, 0.44, ey + 0.40, 3.10, eh - 0.52, ex)
    xs = 3.72
    nch = len(ex["rows"])
    widths = [1.02] + [0.54] * nch
    header = ["通道 c"] + ["c%d" % (i + 1) for i in range(nch)]
    rows = [
        ["ℓ：u→v"] + ["%d→%d" % (r["lu"], r["lv"]) for r in ex["rows"]],
        ["类型"] + ["up" if r["up"] else "down" for r in ex["rows"]],
        ["Φ(c) 第一位"] + [str(r["phi"][0]) for r in ex["rows"]],
        ["Φ(c) 第二位"] + ["%+d" % r["phi"][1] for r in ex["rows"]],
    ]
    table(d, xs, ey + 0.40, widths, header, rows, accent=GREEN, fs=6.8,
          hdr_h=0.24, row_h=0.26, aligns=["l"] + ["c"] * nch)
    d.text(xs, ey + 1.80, ew - xs + 0.20, eh - 1.92, [
        p("路径：%s（%d 跳）；ℓ 序列 %s。"
          % (" → ".join("(%d,%d)" % F.coord(n) for n in ex["path"]),
             len(ex["path"]) - 1,
             "→".join(str(v) for v in ex["labels"])),
          size=7.4, color=GREY, space=1.8),
        p("Φ 按字典序严格递增（代码里的断言 strict=%s）：%s < …，一路只升不降 ⇒ "
          "这条路径不可能参与任何依赖环。"
          % (ex["strict"],
             " < ".join("(%d,%+d)" % r["phi"] for r in ex["rows"][:4])),
          size=7.4, bold=True, color=GREEN, space=1.8),
        p("注意转折点在 ℓ=%d，并没有爬到根（ℓ=0）：up*·down* 只要求「先升后降」，"
          "并不要求经过根。定理 1 第 2 步里那条「爬到根再下来」的路径只是用来证明"
          "「存在解」，实际选路会取更短的那条。" % ex["turn_at"],
          size=7.4, color=GREY, space=0),
    ])

    # --- ④ two traps ------------------------------------------------------
    cx4 = 0.30 + ew + 0.14
    cw4 = SLIDE_W - cx4 - 0.30
    card(d, cx4, ey, cw4, eh, "④ 读这些符号时的两个坑", accent=GREY,
         hdr_h=0.30)
    d.text(cx4 + 0.14, ey + 0.36, cw4 - 0.28, eh - 0.42, [
        p("坑 1：ℓ 是无权跳数，不是线延迟。", size=8.2, bold=True, color=INK,
          space=1.4),
        p("本项目实际链路延迟横向 H=%d cy、纵向 V=%d cy，两者并不相等；但 ℓ 只承担"
          "「定序、保证 CDG 无环」的职责，用跳数即可。线延迟只影响端到端时间，"
          "不影响两条定理是否成立。" % (A["meta"]["H"], A["meta"]["V"]),
          size=7.8, color=GREY, space=2.4),
        p("坑 2：Φ 不是任何物理量。", size=8.2, bold=True, color=INK, space=1.4),
        p("它不是缓冲占用、也不是优先级，硬件里查不到这个字段。它只是证明用的"
          "排序函数：换一组同样单调的 Φ，结论完全一样。真正落到硬件里的只有一张"
          "静态转向许可表 + 一张 (s,d)→出端口表，1 个 VC。",
          size=7.8, color=GREY, space=2.4),
        p("代码对应：ℓ = _updown_labels，路径 = _tree_path，无环复验 = build_cdg + "
          "cdg_acyclic，Φ 单调性由本页实例断言。", size=7.4, color=BLUE,
          space=0),
    ])
    return d


def build_theorems() -> Deck:
    A = analysis()
    H = A["healthy"]
    S = H["schemes"]
    MX_ = A["maximality"]["healthy"]
    T44 = A["theorem44"]
    TM = A["turnmodel44"]
    RM = A["random_maximal_healthy"]

    d = Deck()
    d.rect(0, 0, SLIDE_W, SLIDE_H, fill=WHITE, line=None)
    d.rect(0, 0, SLIDE_W, 0.92, fill=INK, line=None)
    d.rect(0, 0, 0.10, 0.92, fill=GREEN, line=None)
    d.text(0.34, 0.10, 12.6, 0.44,
           [p("定理 1 / 定理 2 的逐步推导：零牺牲 · 无死锁 · 转向集已极大",
              size=20, bold=True, color=WHITE, space=0)])
    d.text(0.36, 0.55, 12.6, 0.30,
           [p("定理 1 = 只要残图连通，表一定建得出且 1 VC 无死锁；"
              "定理 2 = 转向集是极大无环集，等价于「不存在比 M3′ 更宽松的 "
              "turn model」", size=10.0, color="C3CBD4", space=0)])

    # --- ① theorem 1 ------------------------------------------------------
    ry, rh = 1.00, 3.32
    cw1 = 6.20
    card(d, 0.30, ry, cw1, rh, "① 定理 1：残图连通 ⇒ 零牺牲 + 无死锁 + 保序",
         accent=GREEN, hdr_h=0.30)
    d.text(0.42, ry + 0.36, cw1 - 0.24, rh - 0.42, [
        p("陈述　对任意故障集 F：G′ 连通 ⇔ 存在覆盖全部 (s,d) 的合法路由表且"
          "牺牲集 S=∅。（⇐ 方向显然：残图断开时跨分量物理不可达，任何路由都做不到；"
          "以下证 ⇒。）", size=7.8, bold=True, color=GREEN, space=1.8),
        p("第 1 步 · ℓ 存在　G′ 连通 ⇒ 从任一 r 出发的 BFS 能到达全部节点，"
          "ℓ(v)=dist(r,v) 有限；且每个 v≠r 至少有一个邻居 u 满足 ℓ(u)=ℓ(v)−1，"
          "称为 v 的父节点。", size=7.7, color=GREY, space=1.8),
        p("第 2 步 · 每对都有合法路径　对任意 (s,d)：从 s 沿父链上行到 r（全是 up），"
          "再沿 d 的父链下行到 d（全是 down），拼成 up*·down*，长度 ≤ ℓ(s)+ℓ(d) 且"
          "不含 down→up ⇒ 表必然建得出、S=∅。", size=7.7, color=GREY, space=1.8),
        p("第 3 步 · 无死锁　路径里只可能出现 up→up、up→down、down→down 三种衔接，"
          "而它们都严格升 Φ（第 3 页 ②）。CDG 的每条边都升 Φ，而 Φ 取值在有限集合里 "
          "⇒ CDG 无环 ⇒ 按 Dally–Seitz，1 个 VC 就无死锁。",
          size=7.7, color=GREY, space=1.8),
        p("第 4 步 · 保序　每对 (s,d) 只有一条静态路径，单 VC 不存在换道，"
          "每跳先进先出 ⇒ 同一包的 flit 不会乱序，无需重排序缓冲。",
          size=7.7, color=GREY, space=1.8),
        p("第 5 步 · 与根无关　第 1–4 步对任意 r∈V′ 都成立 ⇒ %d 个候选根的表全都"
          "合法；best-root 只是在合法解里挑峰值最小的那个，不影响容错。"
          % MX_["n_roots"], size=7.7, color=GREY, space=1.8),
        p("实测校核　44 个预算场景（≤4 死 router / ≤8 断链）：牺牲 0、CDG 成环 0、"
          "反例 %d；仅 3 例残图本身断开，牺牲量等于信息论下界。"
          % T44["violations"], size=7.7, bold=True, color=GREEN, space=0),
    ])

    # --- ② theorem 2 ------------------------------------------------------
    cx2 = 0.30 + cw1 + 0.14
    cw2 = SLIDE_W - cx2 - 0.30
    card(d, cx2, ry, cw2, rh, "② 定理 2：转向集极大 —— 再放开任一条即成环",
         accent=RED, hdr_h=0.30)
    d.text(cx2 + 0.14, ry + 0.36, cw2 - 0.28, 0.62, [
        p("陈述　T_UD = 全部 up→up / up→down / down→down 转向。任取一条不在 T_UD "
          "里的转向 t（必然是某个 down→up），则 T_UD ∪ {t} 的 CDG 一定含环。",
          size=7.8, bold=True, color=RED, space=0),
    ])
    fig_cdg_ring(d, cx2 + 0.16, ry + 1.02, 2.50, 1.34)
    tx = cx2 + 2.80
    d.text(tx, ry + 1.00, cw2 - 2.94, 1.44, [
        p("设 t 把通道 (u→v)（down）接到 (v→w)（up）。构造环只需四段，"
          "前三段全部用 T_UD 里的合法衔接：", size=7.6, color=INK, space=1.6),
        p("① 从 (v→w) 出发继续沿父链上行，ℓ 每跳 −1，有限步必达根（ℓ=0），"
          "沿途衔接全是 up→up。", size=7.5, color=GREY, space=1.4),
        p("② 在根处拐头：一次 up→down 衔接，合法。", size=7.5, color=GREY,
          space=1.4),
        p("③ 沿 u 的父链下行到 u，衔接全是 down→down，于是到达 (u→v)。",
          size=7.5, color=GREY, space=1.4),
        p("④ 最后用新放开的 t，从 (u→v) 接回 (v→w) —— 回到起点，环闭合（证毕）。",
          size=7.5, bold=True, color=RED, space=0),
    ])
    d.text(cx2 + 0.14, ry + 2.72, cw2 - 0.28, rh - 2.78, [
        p("要点　这一步只用到「r 到任何点都有父链」，也就是定理 1 第 1 步的同一个"
          "事实 ⇒ 只要残图连通，禁令就一条都不能少。", size=7.6, color=GREY,
          space=1.6),
        p("实测　%d 个候选根 × 共 %d 条被禁转向，逐条放开后重建 CDG 检测："
          "可安全加入 %d 条。极大性对每个根都成立。"
          % (MX_["n_roots"], MX_["forbidden_tested"], MX_["addable_total"]),
          size=7.6, bold=True, color=RED, space=0),
    ])

    # --- ③ turn-model mapping --------------------------------------------
    my, mh = 4.44, 2.82
    mw = 7.90
    card(d, 0.30, my, mw, mh,
         "③ 映射到 turn model 语境：M3′ = 禁令按 ℓ 定义的 turn model",
         accent=BLUE, hdr_h=0.30)
    d.text(0.44, my + 0.38, mw * 0.50 - 0.20, mh - 0.46, [
        p("同一件事，两种写法。", size=8.4, bold=True, color=BLUE, space=1.4),
        p("XY / west-first / negative-first 把禁令写成「(入方向, 出方向)」的方位对，"
          "例如 XY 禁 N→E、N→W、S→E、S→W。M3′ 先按 ℓ 给每条链路贴 up/down 标签，"
          "再禁掉一整类衔接 down→up。两者最终都落成同一种东西：一张静态转向许可表，"
          "硬件实现、面积、VC 数完全一样（查表 + 1 VC）。",
          size=7.8, color=GREY, space=2.0),
        p("宽松程度也在同一量级（8×6 共 %d 个转向）。" % MX_["total_turns"],
          size=8.4, bold=True, color=BLUE, space=1.4),
        p("XY 允许 %d（%.0f%%）、west-first %d、negative-first %d、"
          "M3′ %d（%.0f%%）—— M3′ 与最灵活的 Glass–Ni 类模型同级，"
          "并不比它们「保守」。"
          % (S["xy"]["permitted"],
             100 * S["xy"]["permitted"] / MX_["total_turns"],
             S["west_first"]["permitted"], S["negative_first"]["permitted"],
             MX_["permitted_min"], 100 * MX_["permitted_frac"]),
          size=7.8, color=GREY, space=0),
    ])
    d.text(0.44 + mw * 0.50, my + 0.38, mw * 0.50 - 0.28, mh - 0.46, [
        p("定理 2 的 turn-model 版本：不存在更宽松的 turn model。",
          size=8.4, bold=True, color=RED, space=1.4),
        p("在这张残图上，M3′ 的转向集已经是极大无环集：再放开任何一条被禁转向，"
          "CDG 立刻成环（上面 ② 的构造对每条禁令都适用）。所以「找一个转向集严格"
          "包含 M3′ 的 turn model」是不可能的 —— 唯一的自由度是把同样多的禁令"
          "挪到别处，这正是第 5 页 L4「禁令换位」在搜的东西。",
          size=7.8, color=GREY, space=2.0),
        p("差别不在宽松度，而在禁令「放在哪」。", size=8.4, bold=True,
          color=ORANGE, space=1.4),
        p("方位型禁令写死在方向上，与洞的位置无关：残图一有洞，某些 (s,d) 就整片"
          "断供 —— 44 场景里 XY / west-first 零牺牲 %d 次、negative-first 只 %d 次。"
          "ℓ 型禁令随残图重新生成，因此「连通即可达」（%d/%d）。这就是 M3′ 用同样"
          "多的禁令换来容错的原因。"
          % (TM["xy"]["zero_sacrifice"],
             TM["negative_first"]["zero_sacrifice"],
             TM["m3p"]["zero_sacrifice"], TM["xy"]["n"]),
          size=7.8, color=GREY, space=0),
    ])

    # --- ④ misreadings ----------------------------------------------------
    cx4 = 0.30 + mw + 0.14
    cw4 = SLIDE_W - cx4 - 0.30
    card(d, cx4, my, cw4, mh, "④ 两个容易读错的结论", accent=GREY, hdr_h=0.30)
    d.text(cx4 + 0.14, my + 0.38, cw4 - 0.28, mh - 0.46, [
        p("① 极大 ≠ 最大（maximal ≠ maximum）。", size=8.2, bold=True,
          color=INK, space=1.4),
        p("定理 2 只说「不能再单独加一条」，并不说 M3′ 是所有无环转向集里最大的"
          "那个。随机贪心能生成大量别的极大集，大小 %d–%d 条不等（M3′ 是 %d 条）。"
          "这些集合就是第 5 页 L4 的搜索空间：总量相当、位置不同。"
          % (RM["min"], RM["max"], MX_["permitted_min"]),
          size=7.8, color=GREY, space=2.2),
        p("② 零牺牲 ≠ 零性能损失。", size=8.2, bold=True, color=INK, space=1.4),
        p("定理 1 只保证「连通即可达」，负载均衡完全不在结论里：M3′ 会把流挤到"
          "通往根的主干走廊上，峰值可达 %.2f× 割界（第 2 页热点图）。改善它要靠"
          "同一转向集内换选路（第 5 页 L3 min-max），而不是改禁令。"
          % A["demo"]["schemes"]["m3p"]["peak_over_lb"],
          size=7.8, color=GREY, space=0),
    ])
    return d


def fig_cycle(d: Deck, x0, y0, w, h) -> None:
    """Releasing one down->up turn always closes a dependency cycle."""
    r = min(w, h) * 0.085
    pts = {"v": (x0 + w * 0.50, y0 + h * 0.12),
           "u": (x0 + w * 0.16, y0 + h * 0.46),
           "w": (x0 + w * 0.84, y0 + h * 0.46),
           "m": (x0 + w * 0.50, y0 + h * 0.80)}

    def arrow(a, b, color, lw=1.7, dash=False):
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / L, dy / L
        pad = r * 1.5
        d.line(ax + ux * pad, ay + uy * pad, bx - ux * pad, by - uy * pad,
               color=color, lw=lw, dash=dash, arrow=True)

    arrow("u", "v", ORANGE)
    arrow("v", "w", RED, lw=1.9, dash=True)
    arrow("w", "m", BLUE)
    arrow("m", "u", ORANGE)
    for tag, fill, ink in (("v", "4E94CC", WHITE), ("u", "AFD0EA", INK),
                           ("w", "AFD0EA", INK), ("m", "DCE7F5", INK)):
        cx, cy = pts[tag]
        d.oval(cx, cy, r, fill=fill, line=WHITE, lw=0.6)
        d.text(cx - r, cy - r * 0.62, r * 2, r * 1.3,
               [p(tag, size=7.0, bold=True, color=ink, align="c", space=0)])
    vx, vy = pts["v"]
    wx, wy = pts["w"]
    d.cross((vx + wx) / 2 + 0.02, (vy + wy) / 2 - 0.03, 0.055, color=RED,
            lw=1.6)
    d.text(x0 - 0.10, y0 + h - 0.16, w + 0.20, 0.18,
           [p("必成环", size=7.4, bold=True, color=RED, align="c", space=0)])


def build_limit() -> Deck:
    A = analysis()
    H, DM = A["healthy"], A["demo"]
    SH, SD = H["schemes"], DM["schemes"]
    SW = A["swap_search"]
    MXh = A["maximality"]["healthy"]
    RM = A["random_maximal_healthy"]

    def cell(scheme: dict | None, key: str, fmt="%.2f×") -> str:
        if not scheme or not scheme.get("routable", True):
            return "不可路由"
        v = scheme.get(key)
        return "—" if v is None else (fmt % v if "%" in fmt else str(v))

    d = Deck()
    d.rect(0, 0, SLIDE_W, SLIDE_H, fill=WHITE, line=None)
    d.rect(0, 0, SLIDE_W, 0.92, fill=INK, line=None)
    d.rect(0, 0, 0.10, 0.92, fill=BLUE, line=None)
    nfb = MXh["total_turns"] - MXh["permitted_min"]
    d.text(0.34, 0.10, 12.6, 0.44,
           [p("M3′ 有超集吗？—— 转向不能再放宽，但选路可以：1 VC 的可行边界",
              size=21, bold=True, color=WHITE, space=0)])
    d.text(0.36, 0.55, 12.6, 0.30,
           [p("① 转向不能再放宽（定理 2，第 4 页 ③）；② 同一转向集内 min-max 选路"
              "是唯一「免费」的超集：峰值 %.2f×→%.2f× 割界；③ 禁令换位更均衡却"
              "更慢 ⇒ 不推荐"
              % (SH["m3p"]["peak_over_lb"], SH["m3p_minmax"]["peak_over_lb"]),
              size=10.0, color="C3CBD4", space=0)])

    ry, rh = 1.02, 2.42
    card(d, 0.30, ry, SLIDE_W - 0.60, rh,
         "① 五层设计空间，硬件同为物理 1 VC，自由参数逐层增加"
         "（mk = all-to-all DES makespan, cy）", accent=BLUE, hdr_h=0.30)
    widths = [2.24, 1.72, 0.94, 0.80, 0.86, 1.10, 0.90, 1.00, 2.64]
    header = ["层次", "自由参数（搜索空间）", "无故障 ÷割界", "mk m=1",
              "mk m=13", "2洞1断链 ÷割界", "mk m=1", "有洞可用", "结论"]
    aligns = ["l", "l", "c", "c", "c", "c", "c", "c", "l"]

    def five(sh, sd, sd_peak=None):
        return [cell(sh, "peak_over_lb"), "%s" % sh.get("makespan_m1", "—"),
                "%s" % sh.get("makespan_m13", "—"),
                sd_peak or cell(sd, "peak_over_lb"),
                "%s" % (sd.get("makespan_m1") or "—")]

    rows = [
        ["L0  方位型 turn model", "无：禁令写死在方向上"]
        + five(SH["xy"], SD["xy"])
        + ["0/44（neg-first 10）", "最均衡也最快，但残图上要牺牲好节点"],
        ["L1  M3 Up*/Down*", "根 = 度最大点（1 个）"]
        + five(SH["m3"], SD["m3"]) + ["41/44", "容错满分，最不均衡"],
        ["L2  M3′ best-root", "根（%d 个候选）" % MXh["n_roots"]]
        + five(SH["m3p"], SD["m3p"]) + ["41/44", "现方案，容错满分"],
        ["L3  L2 + min-max 选路", "路径：∏(s,d) 合法路径数"]
        + five(SH["m3p_minmax"], SD["m3p_minmax"])
        + ["41/44", "✔ 免费，证明一字不改"],
        ["L4  L3 + 禁令换位", "禁令位置（%d 条）：极大无环集" % nfb]
        + five(SW["healthy"], SW["demo"],
               sd_peak="%.2f×（无改进）" % SW["demo"]["peak_over_lb"])
        + ["41/44（兜底）", "✘ 峰值更低，端到端反而更慢"],
        ["割界（任何路由的下界）", "—", "1.00×", "—", "—", "1.00×", "—", "—",
         "XY 达到，但不容错"],
    ]
    table(d, 0.44, ry + 0.34, widths, header, rows, accent=BLUE, fs=7.4,
          aligns=aligns, mark=(3,))

    my, mh = 3.56, 2.42
    c1w = 6.30
    card(d, 0.30, my, c1w, mh,
         "② L3 min-max 选路：算法步骤", accent=GREEN, hdr_h=0.30)
    d.text(0.44, my + 0.36, c1w - 0.28, mh - 0.44, [
        p("输入：M3′ 的转向许可表 T（禁 down→up）+ M3′ 原路由表。"
          "输出：同一个 T 内的另一组路径 —— 禁令、VC 数、表规模都不动。",
          size=7.7, bold=True, color=GREEN, space=1.6),
        p("① 统计负载　对每条有向链路 e 记 load(e) = 当前经过它的 (s,d) 对数。",
          size=7.6, color=GREY, space=1.4),
        p("② 拆（rip-up）　按固定顺序取一对 (s,d)，把它现有路径上每条链路的 "
          "load 减 1，相当于先把这条流从网络里抽走。", size=7.6, color=GREY,
          space=1.4),
        p("③ 重路由（re-route）　在 T 内跑 Dijkstra 重新给它选路，链路代价 "
          "c(e) = (load(e)+1)³；只允许 T 许可的衔接，因此绝不会产生 down→up。"
          "选出的新路径再把 load 加回去。", size=7.6, color=GREY, space=1.4),
        p("④ 为什么用三次方　凸代价让「再往热链路塞一条流」的边际成本远高于"
          "绕到冷链路，于是流自动摊开 —— 这就是 min-max 的来源。若指数取 1，"
          "只会最小化总跳数，起不到均衡作用。", size=7.6, color=GREY, space=1.4),
        p("⑤ 迭代到收敛　全部 %d 对流过一遍算一轮，共 6 轮；峰值不再下降即停，"
          "离线约 1 s。" % (48 * 47), size=7.6, color=GREY, space=1.4),
        p("⑥ 复验后才交付　全对可达 + CDG 无环 + 每对唯一路径（保序）+ 牺牲仍为 0；"
          "任一项不过就整体回退到 M3′ 原表（兜底）。", size=7.6, color=GREY,
          space=1.4),
        p("收益：峰值 %d→%d（无故障）、%d→%d（残图）；残图轻载 makespan %s→%s cy。"
          % (SH["m3p"]["peak"], SH["m3p_minmax"]["peak"], SD["m3p"]["peak"],
             SD["m3p_minmax"]["peak"], SD["m3p"]["makespan_m1"],
             SD["m3p_minmax"]["makespan_m1"]),
          size=7.6, bold=True, color=GREEN, space=0),
    ])

    cx2 = 0.30 + c1w + 0.14
    c2w = SLIDE_W - cx2 - 0.30
    card(d, cx2, my, c2w, mh, "③ L4「禁令换位」：做法、结果、为什么不推荐",
         accent=RED, hdr_h=0.30)
    d.text(cx2 + 0.14, my + 0.36, c2w - 0.28, mh - 0.44, [
        p("禁令换位是什么　禁令总数不变，只把它们挪个位置：",
          size=8.0, bold=True, color=INK, space=1.4),
        p("① 找出当前最热的那条链路；② 在与它相关的被禁转向里挑一条放开；"
          "③ 放开必然成环（第 4 页 ②），就在这个环上挑一条「被路径用得最少」的"
          "转向改判为禁令，把环重新打断；④ 重验全对可达 + CDG 无环，峰值下降就"
          "保留、否则回退。如此反复若干轮。", size=7.7, color=GREY, space=1.6),
        p("结果：更均衡，但更慢。", size=8.0, bold=True, color=RED, space=1.4),
        p("无故障峰值 %d→%d（%.2f× 割界，几乎贴住理论下界），可是 all-to-all 重载 "
          "makespan 从 %s 涨到 %s cy（+%.0f%%）；残图上峰值一点没降（%d→%d）。"
          % (SW["healthy"]["start_peak"], SW["healthy"]["peak"],
             SW["healthy"]["peak_over_lb"], SH["m3p_minmax"]["makespan_m13"],
             SW["healthy"]["makespan_m13"],
             100 * (SW["healthy"]["makespan_m13"]
                    / SH["m3p_minmax"]["makespan_m13"] - 1),
             SW["demo"]["start_peak"], SW["demo"]["peak"]),
          size=7.7, color=GREY, space=1.6),
        p("原因（一句话）　1 个 VC 意味着每个输入口只有一个共享队列：转向越自由，"
          "不同流在同一个队列里交织越多，队头阻塞（HOL）比链路负载更决定速度。"
          "west-first 峰值 %s（%.2f× 割界）却要 %s cy，是同一现象。"
          % (SH["west_first"]["peak"], SH["west_first"]["peak_over_lb"],
             SH["west_first"]["makespan_m13"]), size=7.7, color=GREY,
          space=1.6),
        p("所以真正该看的是这两个指标：", size=8.0, bold=True, color=ORANGE,
          space=1.4),
        p("① 牺牲了几个计算节点 —— 少牺牲就是多算力，每节点分到的活更少，端到端"
          "任务完成更快；② 实测 makespan。峰值只能当诊断量：最均衡的 XY 型放法"
          "恰恰在残图上不可路由、必须牺牲好节点。", size=7.7, color=INK, space=0),
    ])

    by, bh = 6.10, 1.16
    card(d, 0.30, by, 7.36, bh, "⑤ 结论与落地建议", accent=GREEN, hdr_h=0.28)
    d.text(0.44, by + 0.32, 7.10, bh - 0.38, [
        p("① 上 L3（M3′ + min-max 选路）：零硬件改动、表规模不变、离线 1 s，峰值 "
          "−%.0f%%、故障场景轻载 −%.0f%%，三条证明一字不改 —— 没有理由不做。"
          % (100 * (1 - SH["m3p_minmax"]["peak"] / SH["m3p"]["peak"]),
             100 * (1 - SD["m3p_minmax"]["makespan_m1"]
                    / SD["m3p"]["makespan_m1"])),
          size=8.2, color=INK, space=2.0),
        p("② 不上 L4：峰值虽再降 %.0f%% 到 %.2f× 割界（已接近 XY），但重载 "
          "makespan 反而 +%.0f%%，且每个故障图都要重跑数分钟离线搜索 —— 收益为负。"
          % (100 * (1 - SW["healthy"]["peak"] / SH["m3p_minmax"]["peak"]),
             SW["healthy"]["peak_over_lb"],
             100 * (SW["healthy"]["makespan_m13"]
                    / SH["m3p_minmax"]["makespan_m13"] - 1)),
          size=8.2, color=INK, space=2.0),
        p("③ 想再提升重载吞吐，1 VC 已到边界：只能加 VC（LASH / Dual-UD）或时间"
          "分批（BB UD policy）。", size=8.2, color=INK, space=0),
    ])

    cx4 = 0.30 + 7.36 + 0.14
    card(d, cx4, by, SLIDE_W - cx4 - 0.30, bh, "⑥ 出处", accent=GREY,
         hdr_h=0.28)
    d.text(cx4 + 0.14, by + 0.32, SLIDE_W - cx4 - 0.58, bh - 0.38, [
        p("Up*/Down* 原始出处：Schroeder 等，Autonet，IEEE JSAC 9(8), 1991。"
          "无环 CDG 判据：Dally & Seitz，IEEE TC C-36(5), 1987。",
          size=8.0, color=GREY, space=2.0),
        p("「禁令放哪」的系统性方法：Mejía 等，Segment-based Routing，"
          "IPDPS 2006；Starobinski 等，Turn Prohibition，IEEE/ACM ToN 11(3), "
          "2003；DFS 建树的 up*/down*：Sancho 等，Euro-Par 2000。",
          size=8.0, color=GREY, space=0),
    ])
    return d


def main() -> None:
    global ANALYSIS
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", action="store_true")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--analysis", type=Path, default=ANALYSIS,
                    help="measured-numbers JSON from pg_m3p_analysis.py")
    args = ap.parse_args()
    ANALYSIS = args.analysis
    if not (args.pptx or args.png):
        args.pptx = args.png = True

    decks = [build(), build_proof(), build_symbols(), build_theorems(),
             build_limit()]
    if args.pptx:
        emit_pptx(decks, OUT_PPTX)
    if args.png:
        for deck, path in zip(decks, (OUT_PNG, OUT_PNG_PROOF, OUT_PNG_SYMBOL,
                                      OUT_PNG_THEOREM, OUT_PNG_LIMIT)):
            emit_png(deck, path)


if __name__ == "__main__":
    main()
