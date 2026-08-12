#!/usr/bin/env python3
"""One-slide deep dive on M3' Up*/Down* best-root: fault tolerance + deadlock freedom.

Diagrams are driven by the real routing code (labels, paths, link loads), so the
slide cannot drift from the implementation.

  .venv-ppt/bin/python utils/gen_pg_m3p_slide.py --pptx
  python3 utils/gen_pg_m3p_slide.py --png
"""
from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path

import pg_faults_8x6 as F
from gen_pg_fault_deadlock_slide import (
    BLUE, GREEN, GREY, GREY_L, INK, ORANGE, RED, SLIDE_H, SLIDE_W, WHITE,
    Deck, card, emit_png, emit_pptx, p,
)
from pg_routing import _updown_labels, _updown_table, link_loads, max_link_load

ROOT = Path(__file__).resolve().parents[1]
OUT_PPTX = ROOT / "results" / "pg_m3p_updown_slide.pptx"
OUT_PNG = ROOT / "results" / "pg_m3p_updown_slide.png"

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", action="store_true")
    ap.add_argument("--png", action="store_true")
    args = ap.parse_args()
    if not (args.pptx or args.png):
        args.pptx = args.png = True

    deck = build()
    if args.pptx:
        emit_pptx(deck, OUT_PPTX)
    if args.png:
        emit_png(deck, OUT_PNG)


if __name__ == "__main__":
    main()
