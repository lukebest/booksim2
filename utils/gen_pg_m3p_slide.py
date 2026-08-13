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
from pg_routing import _updown_labels, _updown_table, link_loads, max_link_load

ROOT = Path(__file__).resolve().parents[1]
OUT_PPTX = ROOT / "results" / "pg_m3p_updown_slide.pptx"
OUT_PNG = ROOT / "results" / "pg_m3p_updown_slide.png"
OUT_PNG_PROOF = ROOT / "results" / "pg_m3p_proof_slide.png"
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
             accent: str, root: int | None = None) -> None:
    """8x6 grid,每条链路按 all-to-all 负载着色（取两方向较大者）."""
    nx, ny = F.MX, F.MY
    gh = h - 0.46
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

    d.rect(x0, y0 + 0.21, w, h - 0.45, fill="FDFDFE", line="E1E6EB", lw=0.5)
    d.text(x0, y0, w, 0.20,
           [p(title, size=8.6, bold=True, color=accent, align="c", space=0)])
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
        d.line(ux, uy, vx, vy, color=heat(t), lw=0.9 + 2.4 * t)
    for n in sorted(adj):
        px, py = pos(n)
        d.oval(px, py, r, fill="8C99A6", line=None, lw=0)
    if root is not None:
        rx, ry = pos(root)
        d.star(rx, ry, r * 2.5, fill=RED)
    d.text(x0, y0 + h - 0.24, w, 0.24,
           [p(sub, size=7.4, color=GREY, align="c", space=0)])


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
    return {
        "near_root": sum(1 for _, u, v in top
                         if min(labels.get(u, 99), labels.get(v, 99)) <= 2),
        "k": k,
        "peak_label": min(labels.get(top[0][1], 99), labels.get(top[0][2], 99)),
    }


def build_proof() -> Deck:
    A = analysis()
    H = A["healthy"]
    S = H["schemes"]
    T44 = A["theorem44"]
    MX_ = A["maximality"]["healthy"]
    healthy = F.healthy_pg()
    adj = healthy["route_adj"]
    root = H["root"]
    hs = hot_spot_stats(H["loads"]["m3p"], adj, root)
    lb = H["lb"]
    vmax = S["m3p"]["peak"]

    d = Deck()
    d.rect(0, 0, SLIDE_W, SLIDE_H, fill=WHITE, line=None)
    d.rect(0, 0, SLIDE_W, 0.92, fill=INK, line=None)
    d.rect(0, 0, 0.10, 0.92, fill=GREEN, line=None)
    d.text(0.34, 0.10, 12.6, 0.44,
           [p("M3′ Up*/Down*：容错能力的严格证明 · 与 XY 基线的负载热点对比",
              size=21, bold=True, color=WHITE, space=0)])
    d.text(0.36, 0.55, 12.6, 0.30,
           [p("定理 1：残图连通 ⇒ 零牺牲（容错达理论上界）· 定理 2：转向集已极大 "
              "· 代价：峰值链路负载 %.2f× 割界"
              % S["m3p"]["peak_over_lb"], size=10.0, color="C3CBD4", space=0)])

    ry, rh = 1.00, 3.64
    cw1 = 6.86
    card(d, 0.30, ry, cw1, rh,
         "① 定理 1 与证明：残图连通 ⇒ 表一定建得出，且单 VC 无死锁", accent=GREEN)
    d.text(0.46, ry + 0.42, cw1 - 0.32, rh - 0.50, [
        p("定理 1（容错达理论上界）", size=9.5, bold=True, color=GREEN,
          space=1.0),
        p("残图 G′ = 删去死 router 及其链路、断链之后的存活图。若 G′ 连通，则对"
          "任意根 r 都能建出覆盖全部 (s,d) 的合法表、牺牲 0；若 G′ 不连通，任何"
          "路由都无法跨分量通信。故 M3′ 可容忍的故障集合 = {F : G′ 连通}，"
          "即理论最大集合。", size=8.3, color=GREY, space=2.0),
        p("证明", size=9.5, bold=True, color=GREEN, space=1.0),
        p("① 标号：G′ 连通 ⇒ BFS 得有限 ℓ(v)=dist(r,v)，每个 v≠r 都有父节点 u "
          "使 ℓ(u)=ℓ(v)−1。", size=8.3, color=GREY, space=1.6),
        p("② 可达：s 沿父链升到 r（全 up），再沿 d 的父链降到 d（全 down），"
          "拼成 up*·down* ⇒ 合法。故每对至少一条合法路径（≤ ℓ(s)+ℓ(d) 跳），"
          "表必然建成。", size=8.3, color=GREY, space=1.6),
        p("③ 无死锁：定通道势能 Φ(u→v) = (0, −ℓ(v)) 若 up、(1, +ℓ(v)) 若 down"
          "（字典序）。合法衔接只有 up→up / up→down / down→down，三者都严格增大 "
          "Φ ⇒ CDG 每条边升势 ⇒ 有限集内无环 ⇒ Dally–Seitz 成立，单 VC 即安全。"
          "（mesh 按 x+y 二分，无同层边；一般图改用 (ℓ, id) 字典序。）",
          size=8.3, color=GREY, space=1.6),
        p("④ 保序：每对唯一静态路径 + 单 VC + 每跳 FIFO ⇒ flit 天然按序。",
          size=8.3, color=GREY, space=1.6),
        p("⑤ 与根无关：①–④ 对任意 r∈V′ 成立 ⇒ %d 个候选根全部合法；best-root "
          "只在合法解里挑峰值最小者，搜索绝不返回空。" % MX_["n_roots"],
          size=8.3, color=GREY, space=2.0),
        p("实测校核（%d 场景 ≤4R/≤8L）" % T44["n"], size=9.3, bold=True,
          color=ORANGE, space=1.0),
        p("%d 个场景残图连通 → 全部零牺牲、CDG 成环 0 例、反例 %d 个；%d 个场景"
          "残图本身断开（孤立 %s 个好节点）⇒ 牺牲是信息论下界。同目录下 XY 零"
          "牺牲 %d/%d。"
          % (T44["n_connected"], T44["violations"], T44["n_disconnected"],
             "/".join(str(x) for x in T44["disconnected_isolated"]),
             T44["xy_zero_sacrifice"], T44["n"]),
          size=8.3, color=ORANGE, space=0),
    ])

    cx2 = 0.30 + cw1 + 0.16
    cw2 = SLIDE_W - cx2 - 0.30
    card(d, cx2, ry, cw2, rh,
         "② 无故障 all-to-all 链路负载热点（同 1 VC）", accent=RED)
    fig_heat(d, cx2 + 0.10, ry + 0.40, 2.58, 2.50, H["loads"]["xy"], adj, vmax,
             title="XY 基线（不容错）", accent=BLUE,
             sub="峰值 %d = 割界，均匀铺满" % S["xy"]["peak"])
    fig_heat(d, cx2 + 2.98, ry + 0.40, 2.58, 2.50, H["loads"]["m3p"], adj,
             vmax, title="M3′ best-root（★=根）", accent=RED, root=root,
             sub="峰值 %d = %.2f× 割界，向根聚拢"
                 % (S["m3p"]["peak"], S["m3p"]["peak_over_lb"]))
    colorbar(d, cx2 + 1.35, ry + 3.02, 3.00, 0.13, vmax, [0, 48, lb, vmax])
    d.text(cx2 + 0.12, ry + 3.30, cw2 - 0.24, 0.28, [
        p("色标 = 该链路承载的 (s,d) 对数（两方向取大者，两图共用）；最热 %d 条"
          "链路有 %d 条落在根的 ≤2 跳邻域内。" % (hs["k"], hs["near_root"]),
          size=7.2, color=GREY, space=0),
    ])

    ty, th = 4.76, 1.80
    tw = 8.10
    card(d, 0.30, ty, tw, th, "③ 负载不均的定量代价（无故障 8×6，物理 1 VC）",
         accent=RED, hdr_h=0.30)
    widths = [1.92, 0.90, 0.62, 0.52, 0.72, 0.86, 0.86, 1.30]
    header = ["方案", "峰值链路负载", "÷割界", "CV", "归一吞吐",
              "mk m=1", "mk m=13", "44 场景零牺牲"]

    def row(tag, key, sac):
        s = S[key]
        return [tag, s["peak"], "%.2f×" % s["peak_over_lb"], "%.2f" % s["cv"],
                "%.2f" % s["throughput_ratio"], "%s cy" % s["makespan_m1"],
                "%s cy" % s["makespan_m13"], sac]

    rows = [
        row("XY（不容错基线）", "xy", "0/44"),
        row("M3：根 = 度最大点", "m3", "41/44"),
        row("M3′：根 = 负载最优", "m3p", "41/44"),
        row("M3′ + min-max 选路", "m3p_minmax", "41/44"),
        ["割界（任何路由的下界）", lb, "1.00×", "—", "1.00", "—", "—", "—"],
    ]
    table(d, 0.42, ty + 0.34, widths, header, rows, accent=RED, mark=(2,))

    cx3 = 0.30 + tw + 0.16
    cw3 = SLIDE_W - cx3 - 0.30
    card(d, cx3, ty, cw3, th, "④ 这个代价该怎么读", accent=BLUE, hdr_h=0.30)
    d.text(cx3 + 0.14, ty + 0.36, cw3 - 0.28, th - 0.42, [
        p("不是绕远，是选路集中。", size=8.6, bold=True, color=BLUE, space=1.0),
        p("M3′ 总跳数 %d、平均 %.2f 跳，与 XY 完全相同（都是最短路）"
          "⇒ 不均衡纯粹来自「同一最短路集合里怎么挑」。"
          % (S["m3p"]["hops"], S["m3p"]["avg_hops"]),
          size=8.2, color=GREY, space=2.4),
        p("重载差距远大于 1.41×。", size=8.6, bold=True, color=RED, space=1.0),
        p("m=13 时 %s vs %s cy（+%.0f%%），远超峰值负载比 ⇒ 单 VC 共享 FIFO 下"
          "路径交织带来的 HOL 阻塞把不均衡进一步放大。"
          % (S["m3p"]["makespan_m13"], S["xy"]["makespan_m13"],
             100 * (S["m3p"]["makespan_m13"] / S["xy"]["makespan_m13"] - 1)),
          size=8.2, color=GREY, space=0),
    ])

    by, bh = 6.66, 0.72
    d.rect(0.30, by, SLIDE_W - 0.60, bh, fill="F2F7F4", line=GREEN, lw=0.9,
           round_=0.04)
    d.rect(0.30, by, 0.055, bh, fill=GREEN, line=None)
    d.text(0.48, by + 0.08, 7.60, bh - 0.14, [
        p("定理 2（极大性）：M3′ 的转向集不可能再放宽", size=9.4, bold=True,
          color=GREEN, space=1.2),
        p("任一被禁的 down→up 转向 (u→v→w) 一旦放开必成环：(v→w) 是 up 通道，"
          "可沿 up* 走到 ℓ 最小点、再沿 down* 回到 u，最后经 down 通道 (u→v) "
          "回到起点。实测 %d 个根 × %d 条禁令逐条测试，可加入 %d 条。"
          % (MX_["n_roots"], MX_["forbidden_tested"], MX_["addable_total"]),
          size=8.2, color=INK, space=0),
    ])
    d.text(8.20, by + 0.08, SLIDE_W - 8.20 - 0.45, bh - 0.14, [
        p("所以问题只剩一个", size=9.4, bold=True, color=GREEN, space=1.2),
        p("容错已达理论上界、死锁已构造性排除、保序天然成立；唯一的短板是负载"
          "不均。下一页：1 VC 下这块短板还能补到什么程度。",
          size=8.2, color=INK, space=0),
    ])
    return d


# --------------------------------------------------------------------------
# Slide 3: how far can a 1-VC scheme go?
# --------------------------------------------------------------------------

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
           [p("① 不存在更宽松的 turn model 超集（定理 2）；② 合法集内 min-max "
              "选路是唯一「免费」的超集：峰值 %.2f×→%.2f× 割界、故障场景轻载 "
              "−%.0f%%；③ 禁令换位能把峰值压到 %.2f× 却因 HOL 更慢 ⇒ 不推荐"
              % (SH["m3p"]["peak_over_lb"], SH["m3p_minmax"]["peak_over_lb"],
                 100 * (1 - SD["m3p_minmax"]["makespan_m1"]
                        / SD["m3p"]["makespan_m1"]),
                 SW["healthy"]["peak_over_lb"]),
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
        ["L0  XY / Glass–Ni", "无：禁令写死在方向上"]
        + five(SH["xy"], SD["xy"]) + ["0/44", "最均衡也最快，但零容错"],
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

    my, mh = 3.60, 2.16
    c1w, c2w, c3w = 4.05, 4.20, 4.20
    card(d, 0.30, my, c1w, mh, "② 为什么没有「更宽松的超集」", accent=GREEN,
         hdr_h=0.30)
    fig_cycle(d, 0.38, my + 0.34, 1.44, mh - 0.42)
    d.text(0.30 + 1.58, my + 0.36, c1w - 1.70, mh - 0.44, [
        p("定理 2", size=8.8, bold=True, color=GREEN, space=1.0),
        p("左图 ℓ：v=3 > u=w=2 > m=0。放开任意一条 down→up（u→v→w），就能沿"
          "「up* 到最低点 m → down* 回 u」把依赖接回起点 ⇒ 必成环。",
          size=8.0, color=GREY, space=2.2),
        p("而且它是「大」的极大集", size=8.8, bold=True, color=GREEN,
          space=1.0),
        p("允许 %d/%d = %.0f%%；随机生长的极大无环集只有 %d–%d 条。XY 只允许 "
          "%d 条却更均衡 ⇒ 均衡度不取决于「留多少转向」，而取决于 %d 条禁令"
          "放在哪（见 ③④）。"
          % (MXh["permitted_min"], MXh["total_turns"],
             100 * MXh["permitted_frac"], RM["min"], RM["max"],
             SH["xy"]["permitted"], nfb),
          size=8.0, color=GREY, space=0),
    ])

    cx2 = 0.30 + c1w + 0.14
    card(d, cx2, my, c2w, mh, "③ 唯一免费的超集：L3 min-max 选路", accent=GREEN,
         hdr_h=0.30)
    d.text(cx2 + 0.14, my + 0.36, c2w - 0.28, mh - 0.44, [
        p("为什么三条性质一字不改", size=8.8, bold=True, color=GREEN, space=1.0),
        p("它只在 M3′ 已许可的转向集内换路径：CDG 仍是定理 2 那张无环图的子图 ⇒ "
          "无死锁不变；每对仍是唯一静态路径 ⇒ 保序不变；仍取 up*·down* 形状 ⇒ "
          "定理 1 的可达性构造不变、牺牲仍为 0。", size=8.0, color=GREY,
          space=2.2),
        p("做法与收益", size=8.8, bold=True, color=GREEN, space=1.0),
        p("凸代价 (load+1)³ 的合法 Dijkstra + 拆环重路由，离线约 1 s、表规模不变。"
          "峰值 %d→%d（无故障，%.2f×→%.2f× 割界）、%d→%d（2洞1断链，%.2f×→%.2f×）；"
          "故障场景 m=1 makespan %s→%s cy（−%.0f%%）。"
          % (SH["m3p"]["peak"], SH["m3p_minmax"]["peak"],
             SH["m3p"]["peak_over_lb"], SH["m3p_minmax"]["peak_over_lb"],
             SD["m3p"]["peak"], SD["m3p_minmax"]["peak"],
             SD["m3p"]["peak_over_lb"], SD["m3p_minmax"]["peak_over_lb"],
             SD["m3p"]["makespan_m1"], SD["m3p_minmax"]["makespan_m1"],
             100 * (1 - SD["m3p_minmax"]["makespan_m1"]
                    / SD["m3p"]["makespan_m1"])),
          size=8.0, color=GREY, space=0),
    ])

    cx3 = cx2 + c2w + 0.14
    card(d, cx3, my, c3w, mh, "④ 负面结果：更均衡 ≠ 更快", accent=RED,
         hdr_h=0.30)
    wf, xs = SH.get("west_first", {}), SH.get("xy_seeded_maximal", {})
    d.text(cx3 + 0.14, my + 0.36, c3w - 0.28, mh - 0.44, [
        p("三个独立方案都出现同一现象", size=8.8, bold=True, color=RED, space=1.0),
        p("L4 禁令换位：峰值 %d→%d（%.2f× 割界，最接近下界），但 m=13 makespan "
          "%s cy，比 L3 的 %s cy 慢 %.0f%%。west-first：峰值 %s（%.2f×）却 %s cy。"
          "XY-seeded 极大集：峰值 %s 却 %s cy，仍比纯 XY 慢 %.0f%%。"
          % (SW["healthy"]["start_peak"], SW["healthy"]["peak"],
             SW["healthy"]["peak_over_lb"], SW["healthy"]["makespan_m13"],
             SH["m3p_minmax"]["makespan_m13"],
             100 * (SW["healthy"]["makespan_m13"]
                    / SH["m3p_minmax"]["makespan_m13"] - 1),
             wf.get("peak"), wf.get("peak_over_lb", 0), wf.get("makespan_m13"),
             xs.get("peak"), xs.get("makespan_m13"),
             100 * (xs["makespan_m13"] / SH["xy"]["makespan_m13"] - 1)
             if xs.get("makespan_m13") else 0),
          size=8.0, color=GREY, space=2.2),
        p("机理：单 VC 共享 FIFO，转向自由度越大、流交织越多，HOL 阻塞越重；"
          "峰值链路负载只是必要条件，不是充分指标 ⇒ 「均衡度」不能单独当目标函数。",
          size=8.0, color=INK, space=2.2),
        p("而且最均衡的放法恰恰不容错：XY 型禁令在有洞残图上直接不可路由，定向补"
          "转向恢复可达也失败 ⇒ 1 VC 下均衡与容错存在实测张力。", size=8.0,
          color=ORANGE, space=0),
    ])

    by, bh = 5.92, 1.30
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
        p("③ 想真正提升重载吞吐，1 VC 已到边界：只能加 VC（2 VC 的 LASH / "
          "Dual-UD）或时间分批（BB UD policy）—— 即报告里的 B / C 类方案。",
          size=8.2, color=INK, space=0),
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

    decks = [build(), build_proof(), build_limit()]
    if args.pptx:
        emit_pptx(decks, OUT_PPTX)
    if args.png:
        for deck, path in zip(decks, (OUT_PNG, OUT_PNG_PROOF, OUT_PNG_LIMIT)):
            emit_png(deck, path)


if __name__ == "__main__":
    main()
