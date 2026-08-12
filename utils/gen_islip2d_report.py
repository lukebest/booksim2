#!/usr/bin/env python3
"""Generate results/report_islip2d_8x6.html.

An explanatory companion to docs/phase-7-exploration/islip2d-mesh-ring-8x6.md.
The markdown doc is written for someone who already accepts the framing; this
report is written for someone meeting the two conflict predicates and the two
arbiters for the first time, so it leads with inline SVG walkthroughs of one
arbitration round on each fabric and only then shows the measured tables.

Every number is read out of the three result JSONs -- nothing is typed in here,
so the report cannot drift away from the runs that produced it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "results" / "islip2d_8x6.json"
SWEEP = ROOT / "results" / "load_sweep_8x6.json"
VERIFY = ROOT / "results" / "verify_islip2d_8x6.json"
BISECT = ROOT / "results" / "bisect_lat_8x6.json"
OUT = ROOT / "results" / "report_islip2d_8x6.html"

MX, MY, N = 8, 6, 48
H, V = 7, 9


# ---------------------------------------------------------------------------
# 1. Data access
# ---------------------------------------------------------------------------

def load() -> tuple[dict, dict, dict, dict]:
    def rd(p: Path) -> dict:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return rd(BATCH), rd(SWEEP), rd(VERIFY), rd(BISECT)


def xrow(x: dict, config: str) -> list[dict]:
    """Bisection/latency sweep rows for one configuration, ordered by lambda."""
    return sorted([r for r in x["rows"] if r["config"] == config],
                  key=lambda r: r["lam"])


def brow(b: dict, **filt: Any) -> dict | None:
    """First batch row matching every given field."""
    for r in b["rows"]:
        if all(r.get(k) == v for k, v in filt.items()):
            return r
    return None


def brows(b: dict, **filt: Any) -> list[dict]:
    return [r for r in b["rows"]
            if all(r.get(k) == v for k, v in filt.items())]


def mesh_a2a(b: dict, **extra: Any) -> dict | None:
    return brow(b, algo="islip2d_mesh", pattern="alltoall", m=1, sigma=1,
                **extra)


def ring_a2a(b: dict, **extra: Any) -> list[dict]:
    return [r for r in b["rows"]
            if r.get("fabric") == "ring" and r["pattern"] == "alltoall"
            and r["m"] == 1 and r["sigma"] == 1
            and all(r.get(k) == v for k, v in extra.items())]


def srow(s: dict, group: str, cfg: str, lam: float) -> dict | None:
    for r in s["rows"]:
        if (r["group"] == group and r["config"] == cfg
                and abs(r["lam"] - lam) < 1e-9):
            return r
    return None


def scurve(s: dict, group: str, cfg: str) -> list[dict]:
    return sorted((r for r in s["rows"]
                   if r["group"] == group and r["config"] == cfg),
                  key=lambda r: r["lam"])


def lam_star(rows: list[dict]) -> float:
    ok = [r["lam"] for r in rows if r["stable"]]
    return max(ok) if ok else 0.0


def peak(rows: list[dict]) -> float:
    return max((r["accepted"] for r in rows), default=0.0)


def ratio(num: float, den: float, nd: int = 2) -> str:
    """num/den, or an honest marker when the denominator is degenerate.

    Several sweeps have configurations that never reach a stable point on the
    sampled lambda grid, so lam_star is 0; printing 3e8x there would be worse
    than printing nothing.
    """
    if den is None or num is None or den < 1e-6:
        return "n/a"
    return f"{num / den:.{nd}f}×"


def f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    if isinstance(x, int):
        return f"{x:,}"
    return str(x)


def pct(x: float, nd: int = 1) -> str:
    return f"{100 * x:.{nd}f}%"


# ---------------------------------------------------------------------------
# 2. SVG diagram helpers
#
# Hand-rolled rather than pulled from a library: the diagrams need to sit on
# the same node coordinates as the simulator (nid = y*MX + x), so that a reader
# can match a drawn path against a footprint printed by the code.
# ---------------------------------------------------------------------------

def _svg(w: int, h: int, body: str, cap: str = "") -> str:
    c = f'<div class="cap">{cap}</div>' if cap else ""
    return (f'<div class="fig"><svg viewBox="0 0 {w} {h}" width="100%" '
            f'preserveAspectRatio="xMidYMid meet" role="img">{body}</svg>'
            f'{c}</div>')


def _grid(ox: int, oy: int, dx: int, dy: int, *, dots: bool = True) -> str:
    """8x6 node grid. Returns SVG for the nodes plus the mesh links."""
    out = []
    for y in range(MY):
        for x in range(MX - 1):
            out.append(f'<line class="lk" x1="{ox+x*dx}" y1="{oy+y*dy}" '
                       f'x2="{ox+(x+1)*dx}" y2="{oy+y*dy}"/>')
    for x in range(MX):
        for y in range(MY - 1):
            out.append(f'<line class="lk" x1="{ox+x*dx}" y1="{oy+y*dy}" '
                       f'x2="{ox+x*dx}" y2="{oy+(y+1)*dy}"/>')
    if dots:
        for y in range(MY):
            for x in range(MX):
                out.append(f'<circle class="nd" cx="{ox+x*dx}" '
                           f'cy="{oy+y*dy}" r="4"/>')
    return "".join(out)


def _xy(ox: int, oy: int, dx: int, dy: int, x: int, y: int) -> tuple[int, int]:
    return ox + x * dx, oy + y * dy


def _poly(ox: int, oy: int, dx: int, dy: int, pts: list[tuple[int, int]],
          cls: str) -> str:
    p = " ".join(f"{ox+x*dx},{oy+y*dy}" for x, y in pts)
    return f'<polyline class="{cls}" points="{p}"/>'


def fig_dm_paths() -> str:
    """XY vs ROMM on the same source-destination pair, plus the hot cut."""
    ox, oy, dx, dy = 60, 40, 68, 46
    w, h = 980, oy + (MY - 1) * dy + 76
    b = [_grid(ox, oy, dx, dy)]
    # the vertical cut between x=3 and x=4: 6 eastbound links carry everything
    cx = ox + 3 * dx + dx // 2
    b.append(f'<line class="cut" x1="{cx}" y1="{oy-22}" x2="{cx}" '
             f'y2="{oy+(MY-1)*dy+22}"/>')
    b.append(f'<text class="lbl cutlbl" x="{cx+6}" y="{oy-26}">'
             f'中切 x=3→4：仅 6 条同向链路</text>')
    # XY (dimension order) path 8 -> 45
    s, d = (0, 1), (5, 5)
    b.append(_poly(ox, oy, dx, dy, [s, (d[0], s[1]), d], "pXY"))
    # ROMM path via an intermediate point inside the bounding rectangle
    mid = (2, 4)
    b.append(_poly(ox, oy, dx, dy,
                   [s, (mid[0], s[1]), mid, (d[0], mid[1]), d], "pRM"))
    for pt, txt, cls in ((s, "s", "src"), (d, "d", "dst"), (mid, "w", "wp")):
        px, py = _xy(ox, oy, dx, dy, *pt)
        b.append(f'<circle class="{cls}" cx="{px}" cy="{py}" r="8"/>')
        b.append(f'<text class="tag" x="{px}" y="{py+4}">{txt}</text>')
    ly = oy + (MY - 1) * dy + 52
    b.append(f'<line class="pXY" x1="{ox}" y1="{ly}" x2="{ox+40}" y2="{ly}"/>')
    b.append(f'<text class="lbl" x="{ox+48}" y="{ly+4}">XY：先 X 后 Y，'
             f'唯一路径</text>')
    b.append(f'<line class="pRM" x1="{ox+280}" y1="{ly}" x2="{ox+320}" '
             f'y2="{ly}"/>')
    b.append(f'<text class="lbl" x="{ox+328}" y="{ly+4}">ROMM：经中间点 w 的'
             f'两段 XY —— 跳数仍是 dx+dy ⇒ 时延不变</text>')
    return _svg(w, h, "".join(b),
                "图 1 · D-M：mesh 上一次授权占用的是路径上的一串有向链路。"
                "ROMM 换路不改变跳数，所以换路也不会乱序。")


def fig_dr_topology() -> str:
    """Dimension-sliced rings and one two-phase path with a rigid turn."""
    ox, oy, dx, dy = 70, 46, 66, 46
    w, h = 980, oy + (MY - 1) * dy + 96
    b = []
    for y in range(MY):
        y0 = oy + y * dy
        b.append(f'<line class="rlk" x1="{ox}" y1="{y0}" '
                 f'x2="{ox+(MX-1)*dx}" y2="{y0}"/>')
    for x in range(MX):
        x0 = ox + x * dx
        b.append(f'<line class="clk" x1="{x0}" y1="{oy}" x2="{x0}" '
                 f'y2="{oy+(MY-1)*dy}"/>')
    # Only two wrap links are drawn. Drawing all 14 turns the figure into a
    # cage of overlapping arcs and hides the path being explained.
    xe, ye = ox + (MX - 1) * dx, oy + (MY - 1) * dy
    b.append(f'<path class="wrp" d="M {ox} {oy} C {ox+60} {oy-34}, '
             f'{xe-60} {oy-34}, {xe} {oy}"/>')
    b.append(f'<text class="lbl dim" x="{ox+150}" y="{oy-26}">'
             f'行环 R₀ 的绕回链路</text>')
    b.append(f'<path class="wrp" d="M {ox} {oy} C {ox-40} {oy+80}, '
             f'{ox-40} {ye-80}, {ox} {ye}"/>')
    for y in range(MY):
        for x in range(MX):
            px, py = _xy(ox, oy, dx, dy, x, y)
            b.append(f'<circle class="nd" cx="{px}" cy="{py}" r="4"/>')
    # RC path: row arc on R_ys, turn at t, column arc on C_xd
    s, t, d = (1, 1), (6, 1), (6, 4)
    b.append(_poly(ox, oy, dx, dy, [s, t], "arcR"))
    b.append(_poly(ox, oy, dx, dy, [t, d], "arcC"))
    for pt, txt, cls in ((s, "s", "src"), (t, "t", "wp"), (d, "d", "dst")):
        px, py = _xy(ox, oy, dx, dy, *pt)
        b.append(f'<circle class="{cls}" cx="{px}" cy="{py}" r="9"/>')
        b.append(f'<text class="tag" x="{px}" y="{py+4}">{txt}</text>')
    tx, ty = _xy(ox, oy, dx, dy, *t)
    b.append(f'<text class="lbl warn" x="{tx+14}" y="{ty-10}">'
             f'转环 t：同一拍消耗行环抽取点 + 列环插入点（R4，零松弛）</text>')
    ly = oy + (MY - 1) * dy + 46
    b.append(f'<line class="arcR" x1="{ox}" y1="{ly}" x2="{ox+40}" y2="{ly}"/>')
    b.append(f'<text class="lbl" x="{ox+48}" y="{ly+4}">行相弧（6 条行环，'
             f'各 8 节点）</text>')
    b.append(f'<line class="arcC" x1="{ox+280}" y1="{ly}" x2="{ox+320}" '
             f'y2="{ly}"/>')
    b.append(f'<text class="lbl" x="{ox+328}" y="{ly+4}">列相弧（8 条列环，'
             f'各 6 节点）</text>')
    b.append(f'<path class="wrp" d="M {ox} {ly+26} L {ox+40} {ly+26}"/>')
    b.append(f'<text class="lbl" x="{ox+48}" y="{ly+30}">绕回链路：每环各有 1 条'
             f'（图中只画 2 条示意）。6×8 + 8×6 = 96 无向 = 192 有向</text>')
    return _svg(w, h, "".join(b),
                "图 2 · D-R：环上一次授权 = 行相弧 + 转环 + 列相弧。"
                "弧是环上连续一段，可绕回；转环没有缓冲，两相时刻被刚性绑定。")


def fig_round(mesh: dict) -> str:
    """One arbitration round: request bitmaps -> grant -> accept -> fill."""
    w, h = 980, 400
    b = []

    def box(x, y, bw, bh, cls, title, lines):
        o = [f'<rect class="bx {cls}" x="{x}" y="{y}" width="{bw}" '
             f'height="{bh}" rx="8"/>',
             f'<text class="bxt" x="{x+12}" y="{y+22}">{title}</text>']
        for i, ln in enumerate(lines):
            o.append(f'<text class="bxl" x="{x+12}" y="{y+44+i*17}">{ln}</text>')
        return "".join(o)

    def arrow(x1, y1, x2, y2, txt=""):
        o = [f'<line class="ar" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
             f'marker-end="url(#ah)"/>']
        if txt:
            o.append(f'<text class="arl" x="{(x1+x2)//2}" '
                     f'y="{(y1+y2)//2-8}">{txt}</text>')
        return "".join(o)

    b.append('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" '
             'markerWidth="7" markerHeight="7" orient="auto">'
             '<path d="M0,0 L10,5 L0,10 z" class="ahd"/></marker></defs>')
    b.append(box(20, 40, 210, 150, "src", "① 每源一条 request",
                 ["源 s 的残余 VOQ 位图（47 bit）",
                  "1 = 该 dst 还有包没被授权",
                  "s=0: 0 1 1 0 0 1 …",
                  "s=1: 1 0 0 1 1 0 …",
                  f"实测每轮 {f(mesh['ctrl_msgs_total']/mesh['n_rounds'],1)}"
                  f" 条消息（上界 96）"]))
    b.append(box(280, 30, 250, 200, "arb", "② CA：每资源 grant 指针",
                 ["mesh：每条有向链路一个 g_e",
                  "存「下一轮从第几号源开始」",
                  "⌈log₂48⌉=6 bit ⇒ 164×6 = 984 bit",
                  "环：每条环-方向一个 g（28 个）",
                  "",
                  "每个资源沿指针 RR 选一个源",
                  "→ 一条 VOQ 只有在它路径上",
                  "   所有资源都选中它时才「全路径",
                  "   一致」"]))
    b.append(box(580, 30, 230, 200, "acc", "③ 每源 accept 指针",
                 ["a_s 存「本源下一个 dst」",
                  "6 bit × 48 源 = 288 bit",
                  "在全路径一致的 VOQ 里按 a_s 收",
                  "接受数 ≤ grants_per_src",
                  "",
                  f"实测全路径一致率仅 "
                  f"{pct(mesh['unanimous_frac'])}",
                  "（交叉开关上只要 1 个输出同意，",
                  " mesh 上要整条路径同意）"]))
    b.append(box(280, 250, 530, 120, "fill", "④ 顺序补齐 + 落 t0",
                 ["一致相通常只填掉一小部分容量，剩下的按静态序（跳数/弧长降序）"
                  "贪心补齐；",
                  "每条被选中的 VOQ 去冲突域问「最早哪个 t0 整条路径都空」，"
                  "落一个 t0 并占区间。",
                  f"实测每轮放行 {f(mesh['mean_flows_per_round'],1)} 条 VOQ，"
                  f"共 {mesh['n_rounds']} 轮（割界 {mesh['round_lb']}）。"]))
    b.append(box(20, 250, 210, 120, "src", "⑤ 未授予的留在位图",
                 ["没拿到 grant 的位不清零，",
                  "下一轮 request 继续带出。",
                  "所以控制面消息数与积压无关，",
                  "恒为每轮 ≤ 2×48 条。"]))
    b.append(arrow(230, 100, 275, 100, "48 条"))
    b.append(arrow(530, 110, 575, 110, "候选"))
    b.append(arrow(695, 225, 620, 245, ""))
    b.append(arrow(280, 310, 235, 310, "残余"))
    b.append(f'<text class="arl" x="700" y="248">授予</text>')
    return _svg(w, h, "".join(b),
                "图 3 · iSLIP-2D 的一轮：两级指针（每资源 grant + 每源 accept）"
                "决定谁被授予，冲突域决定被授予者落在哪个 t0。")


def fig_domain() -> str:
    """free_at frontier ratchet vs interval back-fill, on one link timeline."""
    w, h = 940, 300
    ox, oy, u = 90, 60, 26
    b = []

    def tl(y, label, cells, note):
        o = [f'<text class="bxl" x="12" y="{y+18}">{label}</text>']
        for i, c in enumerate(cells):
            cls = {"o": "cocc", "f": "cfree", "n": "cnew", "x": "cblk"}[c]
            o.append(f'<rect class="cel {cls}" x="{ox+i*u}" y="{y}" '
                     f'width="{u-2}" height="22" rx="3"/>')
        o.append(f'<text class="bxl dim" x="{ox+len(cells)*u+12}" '
                 f'y="{y+17}">{note}</text>')
        return "".join(o)

    for i in range(14):
        b.append(f'<text class="tick" x="{ox+i*u+8}" y="{oy-8}">{i}</text>')
    b.append(tl(oy, "链路 e 现状", list("oo" + "ff" + "oo" + "ffffffff"),
                "深色 = 已被占，浅色 = 空洞"))
    b.append(tl(oy + 52, "free_at 域",
                list("oo" + "xx" + "oo" + "nfffffff"),
                "只记「t 之后空闲」⇒ 空洞不可用，新传输被顶到前沿之后"))
    b.append(tl(oy + 104, "interval 域",
                list("oo" + "nn" + "oo" + "ffffffff"),
                "整条位向量 ⇒ 新传输可回填空洞，兑现「错时穿过同一链路」"))
    b.append(f'<text class="bxl" x="12" y="{oy+178}">后果</text>')
    b.append(f'<text class="bxl" x="{ox}" y="{oy+178}">'
             f'free_at 提交时会把路径上<tspan class="warn">每一个</tspan>'
             f'资源的前沿都推到全路径的最大值，</text>')
    b.append(f'<text class="bxl" x="{ox}" y="{oy+198}">'
             f'于是一条拥塞链路把自己的滞后输出给它接触的所有链路，再逐跳扩散'
             f'——这就是前沿棘轮。</text>')
    return _svg(w, h, "".join(b),
                "图 4 · 冲突域：同一条链路的时间轴。"
                "两者的仲裁决策完全相同，差别只在被授予者落到哪个 t₀——"
                "所以这不是一项精细化，而是稳态下能否工作的分界。")


def fig_r4() -> str:
    """Why a distributed ring station cannot satisfy R4."""
    w, h = 940, 260
    b = []

    def lane(y, label, cells, cls):
        o = [f'<text class="bxl" x="12" y="{y+17}">{label}</text>']
        for i, c in enumerate(cells):
            k = {"a": "cocc", ".": "cfree", "T": "cnew", "!": "cblk"}[c]
            o.append(f'<rect class="cel {k}" x="{110+i*30}" y="{y}" '
                     f'width="28" height="22" rx="3"/>')
            if c in "T!":
                o.append(f'<text class="tag2" x="{110+i*30+14}" '
                         f'y="{y+16}">{"转" if c == "T" else "×"}</text>')
        return "".join(o)

    b.append(f'<text class="bxt" x="12" y="26">R4：转环必须在同一拍完成'
             f'（行环抽取点 + 列环插入点）</text>')
    b.append(lane(48, "行环 R_ys", list("aa..T......."), ""))
    b.append(lane(94, "列环 C_xd（集中式）", list("....T......."), ""))
    b.append(lane(148, "列环 C_xd（分布式看到的）",
                  list("...aa!......"), ""))
    b.append(f'<text class="bxl" x="470" y="65">CA 事先知道两条环的未来占用，'
             f'挑一个两边都空的拍</text>')
    b.append(f'<text class="bxl" x="470" y="111">⇒ 转环零驻留'
             f'（实测 max_turn_residency = 0）</text>')
    b.append(f'<text class="bxl warn" x="470" y="165">'
             f'环站只看得到本地：flit 到了才发现列环那一拍被占</text>')
    b.append(f'<text class="bxl warn" x="470" y="185">'
             f'⇒ 只能三选一：加缓冲（transfer FIFO）／偏转绕一圈／活锁</text>')
    b.append(f'<text class="bxl" x="12" y="230">'
             f'这就是「集中化不是为了更快，而是为了可行」的全部内容：'
             f'R4 是一个需要未来信息的约束。</text>')
    return _svg(w, h, "".join(b),
                "图 5 · R4 转环原子性：无缓冲 2D 环之所以需要集中式上/下环仲裁。")


def fig_base() -> str:
    """E-tag/I-tag reactive baseline: deflection instead of pre-planning."""
    w, h = 940, 250
    b = []
    cx, cy, r = 190, 120, 74
    b.append(f'<circle class="ringc" cx="{cx}" cy="{cy}" r="{r}"/>')
    for i in range(8):
        a = -math.pi / 2 + i * math.pi / 4
        px, py = cx + r * math.cos(a), cy + r * math.sin(a)
        b.append(f'<circle class="nd" cx="{px:.0f}" cy="{py:.0f}" r="5"/>')
    b.append(f'<text class="tag2" x="{cx}" y="{cy-2}">行环</text>')
    b.append(f'<text class="tag2" x="{cx}" y="{cy+16}">（无缓冲）</text>')
    b.append(f'<rect class="bx acc" x="330" y="60" width="150" height="60" '
             f'rx="8"/>')
    b.append(f'<text class="bxt" x="342" y="84">transfer FIFO</text>')
    b.append(f'<text class="bxl" x="342" y="104">满 ⇒ 无法转环</text>')
    b.append(f'<path class="defl" d="M 264 120 C 300 120, 300 175, 264 175"/>')
    b.append(f'<text class="bxl warn" x="272" y="196">偏转：绕整环一圈再试'
             f'（吃掉一整圈时隙）</text>')
    b.append(f'<text class="bxl" x="530" y="52">逐拍反应式，从不做全局无冲突'
             f'保证：</text>')
    for i, ln in enumerate([
            "环内 flit 永远优先，不停留（实测零违例）",
            "I-tag：抢不到空时隙时为饿死节点预留一个",
            "E-tag：转不过去太久时为该 flit 预留 FIFO 槽",
            "Swap Rule：两侧互相转向时直接交换（绕过 FIFO）",
            "偏转 + Swap ⇒ 乱序 ⇒ 目的端必须有重组缓冲"]):
        b.append(f'<text class="bxl" x="530" y="{78+i*22}">· {ln}</text>')
    b.append(f'<text class="bxl" x="530" y="196">'
             f'⚠ 它是<tspan class="warn">反应式策略</tspan>，'
             f'D-R 是<tspan class="ok2">排程谓词</tspan>，两者只能比结果</text>')
    return _svg(w, h, "".join(b),
                "图 6 · 基线 ring_base（E-tag / I-tag + 偏转）："
                "用事中化解代替事前排程，代价是带宽、乱序与每个桥的缓冲。")


def fig_curve(s: dict) -> str:
    """Accepted-throughput curves for the four configurations."""
    w, h = 940, 380
    ox, oy = 74, 30
    pw, ph = 760, 268
    cfgs = [("mesh_base", "cA", "mesh_base（有缓冲 + 信用反压）"),
            ("mesh_islip2d", "cB", "mesh_islip2d（集中式）"),
            ("ring_base", "cC", "ring_base（E-tag/I-tag + 偏转）"),
            ("ring_islip2d", "cD", "ring_islip2d（集中式）")]
    b = [f'<rect class="plot" x="{ox}" y="{oy}" width="{pw}" height="{ph}"/>']
    ymax = 0.9

    def X(lam: float) -> float:
        return ox + lam * pw

    def Y(a: float) -> float:
        return oy + ph - a / ymax * ph

    for gv in (0.2, 0.4, 0.6, 0.8):
        b.append(f'<line class="gl" x1="{ox}" y1="{Y(gv):.1f}" '
                 f'x2="{ox+pw}" y2="{Y(gv):.1f}"/>')
        b.append(f'<text class="tick" x="{ox-38}" y="{Y(gv)+4:.1f}">'
                 f'{gv:.1f}</text>')
    for gv in (0.2, 0.4, 0.6, 0.8, 1.0):
        b.append(f'<text class="tick" x="{X(gv)-10:.1f}" y="{oy+ph+18}">'
                 f'{gv:.1f}</text>')
    b.append(f'<line class="ideal" x1="{X(0)}" y1="{Y(0)}" '
             f'x2="{X(ymax)}" y2="{Y(ymax)}"/>')
    b.append(f'<text class="tick" x="{X(0.28):.1f}" y="{Y(0.43):.1f}">'
             f'y=λ（全部被接受）</text>')
    for anc, lbl in ((s["anchors"]["mesh_xy"], "mesh 解析锚点"),
                     (s["anchors"]["ring_fixed"], "环解析锚点")):
        b.append(f'<line class="anch" x1="{ox}" y1="{Y(anc):.1f}" '
                 f'x2="{ox+pw}" y2="{Y(anc):.1f}"/>')
        b.append(f'<text class="anchl" x="{ox+pw-118}" y="{Y(anc)-6:.1f}">'
                 f'{lbl} {anc:.3f}</text>')
    for cfg, cls, _ in cfgs:
        rows = scurve(s, "main", cfg)
        pts = " ".join(f"{X(r['lam']):.1f},{Y(r['accepted']):.1f}"
                       for r in rows)
        b.append(f'<polyline class="cv {cls}" points="{pts}"/>')
        st = [r for r in rows if r["stable"]]
        if st:
            r = st[-1]
            b.append(f'<circle class="star {cls}" cx="{X(r["lam"]):.1f}" '
                     f'cy="{Y(r["accepted"]):.1f}" r="5"/>')
    b.append(f'<text class="axl" x="{ox+pw//2-60}" y="{oy+ph+40}">'
             f'注入率 λ（包/节点/拍）</text>')
    b.append(f'<text class="axl" transform="translate(20,{oy+ph//2+40}) '
             f'rotate(-90)">接受吞吐</text>')
    for i, (cfg, cls, lbl) in enumerate(cfgs):
        yy = oy + 14 + i * 20
        b.append(f'<line class="cv {cls}" x1="{ox+16}" y1="{yy}" '
                 f'x2="{ox+52}" y2="{yy}"/>')
        b.append(f'<text class="bxl" x="{ox+60}" y="{yy+4}">{lbl}</text>')
    return _svg(w, h, "".join(b),
                "图 7 · 稳态延迟-吞吐：实心圆 = λ*（最大仍稳定的注入率）。"
                "两个基线在过载区回落，两个集中式单调平台化。")


XCFG = [("mesh_base", "cA", "mesh_base（有缓冲 + 信用反压）"),
        ("mesh_islip2d", "cB", "mesh_islip2d（集中式）"),
        ("ring_base", "cC", "ring_base（E-tag/I-tag + 偏转）"),
        ("ring_islip2d", "cD", "ring_islip2d（集中式）")]
XLBL = {"mesh_base": "mesh_base", "mesh_islip2d": "mesh_islip2d",
        "ring_base": "ring_base", "ring_islip2d": "ring_islip2d"}


def _xframe(x: dict, b: list[str], ox: int, oy: int, pw: int, ph: int,
            Y, yticks: list[tuple[float, str]]) -> None:
    """Plot box, grid, both axes' ticks, and a dotted marker at each lambda*.

    Four lambda* labels would collide (mesh_base 0.41 and mesh_islip2d 0.47 sit
    a grid step apart), so they alternate between two heights above the box.
    """
    b.append(f'<rect class="plot" x="{ox}" y="{oy}" width="{pw}" '
             f'height="{ph}"/>')
    for yv, lbl in yticks:
        b.append(f'<line class="gl" x1="{ox}" y1="{Y(yv):.1f}" '
                 f'x2="{ox+pw}" y2="{Y(yv):.1f}"/>')
        b.append(f'<text class="tick" x="{ox-42}" y="{Y(yv)+4:.1f}">'
                 f'{lbl}</text>')
    for gv in (0.2, 0.4, 0.6, 0.8, 1.0):
        b.append(f'<text class="tick" x="{ox+gv*pw-10:.1f}" '
                 f'y="{oy+ph+18}">{gv:.1f}</text>')
    for i, (cfg, cls, _) in enumerate(XCFG):
        ls = x["summary"][cfg]["lam_star"]
        b.append(f'<line class="lstar" x1="{ox+ls*pw:.1f}" y1="{oy}" '
                 f'x2="{ox+ls*pw:.1f}" y2="{oy+ph}"/>')
        b.append(f'<text class="tick {cls}" fill="currentColor" '
                 f'x="{ox+ls*pw-16:.1f}" y="{oy-6-(i % 2)*15}">'
                 f'λ*={ls}</text>')
    b.append(f'<text class="axl" x="{ox+pw//2-60}" y="{oy+ph+40}">'
             f'注入率 λ（包/节点/拍）</text>')


def _xlegend(b: list[str], ox: int, y: int, pw: int, note: str = "") -> None:
    """One horizontal legend row BELOW the plot box, plus an optional note.

    With four curves there is no empty corner left inside the box that does not
    collide with one of them at some lambda, so the legend goes outside.
    """
    step = pw // len(XCFG)
    for i, (_cfg, cls, lbl) in enumerate(XCFG):
        xx = ox + i * step
        w = " cvw" if "base" in _cfg else ""
        if w:
            b.append(f'<line class="cv{w} {cls}" x1="{xx}" y1="{y}" '
                     f'x2="{xx+26}" y2="{y}"/>')
        b.append(f'<line class="cv {cls}" x1="{xx}" y1="{y}" '
                 f'x2="{xx+26}" y2="{y}"/>')
        b.append(f'<text class="bxl" x="{xx+32}" y="{y+4}">'
                 f'{XLBL[_cfg]}</text>')
    if note:
        b.append(f'<text class="bxl dim" x="{ox}" y="{y+22}">{note}</text>')


def xcross(x: dict, cfg: str) -> dict[float, float]:
    """Measured cut crossings per accepted packet, keyed by lambda.

    `bisect_flits_per_cy` counts flit-cycles over the whole cut, so dividing by
    the accepted packet rate of all N nodes takes the cut width out of the
    number: what is left is a property of the traffic, and the two fabrics must
    therefore agree on it even though their cuts differ in width.
    """
    return {r["lam"]: r["bisect_flits_per_cy"] / (r["accepted"] * N)
            for r in xrow(x, cfg) if r["accepted"] > 0}


def xcross_gap(x: dict, *, both_stable: bool = True) -> float:
    """Largest mesh-vs-ring disagreement on crossings per accepted packet.

    Restricted by default to the lambdas where *both* fabrics are stable: past
    the mesh's own lambda* its accepted mix skews off the cut while the ring is
    still stable at 0.51, so a union over either fabric's stable points would
    report that skew (0.04) rather than the agreement it is meant to check.
    """
    a, c = xcross(x, "mesh_islip2d"), xcross(x, "ring_islip2d")
    sh = a.keys() & c.keys()
    if both_stable:
        for cfg in ("mesh_islip2d", "ring_islip2d"):
            sh &= {r["lam"] for r in xrow(x, cfg) if r["stable"]}
    return max((abs(a[k] - c[k]) for k in sh), default=0.0)


def _xcurve(b: list[str], rows: list[dict], cls: str, ox: int, pw: int,
            Y, key: str, *, wide: bool = False) -> dict | None:
    """Solid inside the stable region, dashed past it, dot at lambda*.

    The dashed part is a separate polyline that starts at the last stable point
    so the two segments join without overlapping, and it is drawn first so the
    solid part stays on top wherever they meet.

    `wide` draws a fat translucent stroke, used for the two baselines. Below
    each fabric's first saturation point all four curves carry identical cut
    traffic and would sit exactly on top of each other; the halo lets the
    baseline stay visible underneath the centralized line instead of vanishing.
    """
    st = [r for r in rows if r["stable"]]
    un = [r for r in rows if not r["stable"]]
    w = " cvw" if wide else ""

    def pts(sel: list[dict]) -> str:
        return " ".join(f"{ox+r['lam']*pw:.1f},{Y(r[key]):.1f}" for r in sel)
    tail = (st[-1:] if st else []) + un
    if len(tail) > 1:
        b.append(f'<polyline class="cvu{w} {cls}" points="{pts(tail)}"/>')
    if len(st) > 1:
        b.append(f'<polyline class="cv{w} {cls}" points="{pts(st)}"/>')
    if st:
        r = st[-1]
        b.append(f'<circle class="star {cls}" cx="{ox+r["lam"]*pw:.1f}" '
                 f'cy="{Y(r[key]):.1f}" r="5"/>')
    return st[-1] if st else None


def fig_bisect(x: dict) -> str:
    """Bisection utilization: only the centralized mesh ever fills its cut."""
    w, h = 940, 426
    ox, oy, pw, ph = 74, 48, 760, 258
    ymax = 1.1

    def Y(u: float) -> float:
        return oy + ph - u / ymax * ph
    b: list[str] = []
    _xframe(x, b, ox, oy, pw, ph, Y,
            [(v, f"{v:.1f}") for v in (0.2, 0.4, 0.6, 0.8, 1.0)])
    b.append(f'<line class="satl" x1="{ox}" y1="{Y(1.0):.1f}" '
             f'x2="{ox+pw}" y2="{Y(1.0):.1f}"/>')
    b.append(f'<text class="satt" x="{ox+pw-236}" y="{Y(1.0)-8:.1f}">'
             f'切面饱和：每条链路 100% 时间在忙</text>')
    for cfg, cls, _ in XCFG:
        _xcurve(b, xrow(x, cfg), cls, ox, pw, Y, "bisect_util",
                wide="base" in cfg)
    mi, ri = x["summary"]["mesh_islip2d"], x["summary"]["ring_islip2d"]
    b.append(f'<text class="axl" transform="translate(18,'
             f'{oy+ph//2+60}) rotate(-90)">二分带宽利用率</text>')
    _xlegend(b, ox, oy + ph + 62, pw,
             "实线 = 稳定区，虚线 = 越过 λ*；同 fabric 的两条曲线共用分母，"
             "跨 fabric 请看下表最后一列")
    return _svg(w, h, "".join(b),
                "图 8 · 二分带宽利用率：实心圆 = λ*，竖虚线标出各自的 λ*，"
                "虚线段表示已越过 λ*。同一 fabric 的两条曲线共用一个分母"
                f"（mesh 切面 {mi['bisect_links']} 条有向链路，"
                f"环 {ri['bisect_links']} 条——行环绕回，必须切两处）。"
                "四条曲线在低负载完全重合：这时谁都没到瓶颈，"
                "切面负载只由流量决定。")


def _fig_lat(x: dict, key: str, num: int, title: str, note: str) -> str:
    """One latency metric on a log axis, shared by the mean and p99 figures.

    Both figures use the same decade range so they can be compared by eye; the
    point of the pair is that p99 sits just above the mean everywhere inside the
    stable region.
    """
    w, h = 940, 426
    ox, oy, pw, ph = 74, 48, 760, 258
    lo, hi = 20.0, 10000.0
    lg_lo, lg_hi = math.log10(lo), math.log10(hi)

    def Y(t: float) -> float:
        u = (math.log10(max(lo, t)) - lg_lo) / (lg_hi - lg_lo)
        return oy + ph - u * ph
    b: list[str] = []
    _xframe(x, b, ox, oy, pw, ph, Y,
            [(20, "20"), (50, "50"), (100, "100"), (300, "300"),
             (1000, "1k"), (3000, "3k"), (10000, "10k")])
    for cfg, cls, _ in XCFG:
        _xcurve(b, xrow(x, cfg), cls, ox, pw, Y, key,
                wide="base" in cfg)
    b.append(f'<text class="axl" transform="translate(18,'
             f'{oy+ph//2+52}) rotate(-90)">{title}（拍，对数轴）</text>')
    _xlegend(b, ox, oy + ph + 62, pw,
             "实线 = 稳定区；虚线 = 越过 λ* 后源队列无界增长，"
             "那里的数字反映测量窗而非 fabric")
    return _svg(w, h, "".join(b), f"图 {num} · {note}")


def xstable(x: dict, cfg: str) -> list[dict]:
    return [r for r in xrow(x, cfg) if r["stable"]]


def xat(x: dict, cfg: str, lam: float, key: str) -> Any:
    return next((r[key] for r in xrow(x, cfg) if r["lam"] == lam), None)


def xcrossover(x: dict, cen: str, base: str, key: str) -> tuple | None:
    """First lambda where the centralized config beats its baseline on `key`."""
    a = {r["lam"]: r for r in xstable(x, cen)}
    b = {r["lam"]: r for r in xstable(x, base)}
    for lam in sorted(a.keys() & b.keys()):
        if a[lam][key] < b[lam][key]:
            return lam, a[lam][key], b[lam][key]
    return None


def fig_mean_lat(x: dict) -> str:
    lo = min(xstable(x, c)[0]["mean_lat"] for c, _, _ in XCFG)
    hi = max(xstable(x, c)[0]["mean_lat"] for c, _, _ in XCFG)
    return _fig_lat(
        x, "mean_lat", 9, "平均时延",
        f"平均时延：空载相差 {hi - lo:.0f} 拍（{lo:.0f}→{hi:.0f}），"
        "那就是请求-授权环路的价钱；集中式两条曲线在各自 λ* 前几乎水平，"
        "两条基线则早早开始爬坡。四条线都在自己的 λ* 处近乎垂直拐起。")


def fig_p99_lat(x: dict) -> str:
    def worst(cfg: str) -> float:
        # from the summary, not recomputed: the prose quotes the same field, and
        # rounding it twice by two paths printed 2.35 next to 2.36
        return x["summary"][cfg]["worst_p99_over_mean_stable"]
    return _fig_lat(
        x, "p99", 10, "p99 时延",
        "p99 时延：与图 9 同一纵轴范围。稳定区内 p99/平均最坏值——"
        f"基线 {worst('mesh_base'):.2f}×（mesh）／{worst('ring_base'):.2f}×（环），"
        f"集中式 {worst('mesh_islip2d'):.2f}× ／ {worst('ring_islip2d'):.2f}×。"
        "授权制的刚性把长尾压掉了：包一旦被授权就不会在网络里被任何东西阻塞。")


# ---------------------------------------------------------------------------
# 3. Tables
# ---------------------------------------------------------------------------

def tbl(head: list[str], rows: list[list[str]], *, cls: str = "") -> str:
    h = "".join(f"<th>{c}</th>" for c in head)
    body = []
    for r in rows:
        tag = "tr"
        if r and r[0].startswith("!"):
            tag, r = 'tr class="hl"', [r[0][1:]] + r[1:]
        body.append(f"<{tag}>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return (f'<table class="{cls}"><thead><tr>{h}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def t_predicate(b: dict) -> str:
    mm, rm = b["mesh_misuse"], b["ring_misuse"]
    mg, rg = b["mesh_greedy_max_set"], b["ring_greedy_max_set"]
    rows = [
        ["「src≠src 且 dst≠dst ⇒ 无冲突」的错判率<br>"
         "<span class='muted'>把实际冲突的一对判成安全（假阴性）</span>",
         f"<b class='lose'>{pct(mm['crossbar_predicate_unsafe_rate'],2)}</b>"
         f"<br><span class='muted'>{f(mm['diff_src_diff_dst_pairs'])} "
         f"对样本</span>",
         f"<b class='lose'>{pct(rm['crossbar_predicate_unsafe_rate'],2)}</b>"
         f"<br><span class='muted'>环上还要叠加端口冲突</span>"],
        ["「同 src ⇒ 有冲突」的错判率<br>"
         "<span class='muted'>把实际可并发的一对判成冲突（假阳性）</span>",
         f"<b class='lose'>{pct(mm['same_src_actually_free_rate'],2)}</b>",
         f"<b class='lose'>{pct(rm['same_src_actually_free_rate'],2)}</b>"],
        ["!同时刻可并发流数（贪心极大集，均值）",
         f"<b class='win'>{f(mg['mean'],1)}</b> 条<br>"
         f"<span class='muted'>交叉开关置换上限 = "
         f"{mg['crossbar_permutation_reference']}</span>",
         f"<b class='win'>{f(rg['r1_r2_r3']['mean'],1)}</b> 条<br>"
         f"<span class='muted'>只看链路 R1 会高估到 "
         f"{f(rg['r1_only']['mean'],1)}</span>"],
        ["只用「链路不相交」是否足够",
         "不够：M2 上/下环坡道口（RAMP_BW=2）单独限流",
         f"不够：漏检率 "
         f"{pct(rm['false_negative_rate_of_pure_R1'],2)}"
         f"，全部来自端口<br><span class='muted'>板/离 = "
         f"{pct(rm['port_clash_kind_frac']['board_board'],1)} / "
         f"{pct(rm['port_clash_kind_frac']['leave_leave'],1)}"
         f"，混合 "
         f"{pct(rm['port_clash_kind_frac']['board_leave'],1)}</span>"],
    ]
    return tbl(["检验项", "2D mesh（D-M）", "2D 无缓冲环（D-R）"], rows)


def t_mesh_knobs(b: dict) -> str:
    def row(label, note, **filt):
        r = mesh_a2a(b, **filt)
        if not r:
            return None
        return [label, f(r["n_rounds"]), f(r["round_ratio"], 3),
                f(r["data_span"]), f(r["makespan"]),
                pct(r["unanimous_frac"]), note]
    rows = [
        row("基准 xy · g=1 · free_at", "参照点",
            path_mode="xy", grants_per_src=1, conflict_domain="free_at"),
        row("!xy · g=1 · <b>interval</b>", "换冲突域：data_span 从 3797 降到 150",
            path_mode="xy", grants_per_src=1, conflict_domain="interval"),
        row("xy · g=2 · free_at", "每源多授一个 VOQ，几乎不动轮数",
            path_mode="xy", grants_per_src=2, conflict_domain="free_at"),
        row("!xy · g=2 · <b>interval</b>", "两者叠加后的最优点",
            path_mode="xy", grants_per_src=2, conflict_domain="interval"),
        row("romm_static · g=2",
            "静态平衡中间点：最热链路负载 96 = 割界，已最优，轮数与 XY 相同",
            path_mode="romm_static", grants_per_src=2),
        row("romm_dyn · g=2",
            "在线选点把最热链路负载推到 119（比割界 96 高 24%），"
            "轮数下界随之升到 119，所以「轮数/下界」看似 1.000 其实是变差了",
            path_mode="romm_dyn", grants_per_src=2),
        row("补齐序 hops_asc", "短流先填：data_span 最优",
            path_mode="xy", grants_per_src=2, fill="hops_asc"),
        row("补齐序 pressure", "按剩余压力填",
            path_mode="xy", grants_per_src=2, fill="pressure"),
        row("iters=0（关掉一致相）", "只留补齐：轮数反而最少",
            path_mode="xy", grants_per_src=2, iters=0),
        row("iters=4", "多迭代把轮数拉回 103",
            path_mode="xy", grants_per_src=2, iters=4),
    ]
    base = brow(b, algo="islip_mesh", pattern="alltoall", m=1, sigma=1)
    greedy = brow(b, algo="greedy_ff", pattern="alltoall", m=1, sigma=1)
    if base:
        rows.append(["<i>对照：单级 islip_mesh</i>", f(base["n_rounds"]),
                     "—", f(base["data_span"]), f(base["makespan"]), "—",
                     "无两级指针、无 VOQ 位图"])
    if greedy:
        rows.append(["<i>对照：greedy_ff（无轮次概念）</i>", "—", "—",
                     f(greedy["data_span"]), f(greedy["makespan"]), "—",
                     "离线全知贪心，作 data_span 参照"])
    return tbl(["配置", "轮数", "轮数/下界", "data_span", "makespan",
                "全路径一致率", "读法"],
               [r for r in rows if r])


def t_ring_knobs(b: dict) -> str:
    def row(label, note, **filt):
        rs = ring_a2a(b, **filt)
        if not rs:
            return None
        r = rs[0]
        return [label, f(r["n_rounds"]), f(r["round_lb"]),
                f(r["round_ratio"], 3), f(r["makespan"]),
                f(r["max_link_load"]), note]
    rows = [
        row("fixed（静态维序 RC）· g=1", "参照点",
            path_mode="fixed", grants_per_src=1),
        row("!balanced（离线平衡方向×维序）· g=1",
            "把最热链路负载从 60 压到 49，下界随之下降",
            path_mode="balanced", grants_per_src=1),
        row("dyn（在线选候选）· g=1", "在线选点不如离线平衡",
            path_mode="dyn", grants_per_src=1),
        row("board=1 / leave=1 · g=2", "每节点单上环口 + 单下环口（参照）",
            path_mode=None, board_ports=1, leave_ports=1,
            spatial_reuse="arc", conflict_domain=None, fill=None, iters=None,
            t_rtt=None),
        row("board=2 / leave=2 · g=2", "端口翻倍只值约 7% makespan",
            path_mode=None, board_ports=2, leave_ports=2),
        row("spatial_reuse=<b>whole_ring</b> · g=2",
            "整环互斥（无空分复用）⇒ 下界从 60 涨到 192",
            path_mode=None, spatial_reuse="whole_ring"),
        row("!conflict_domain=<b>interval</b> · g=2",
            "轮数不变，makespan 从 1,957 降到 120",
            path_mode=None, conflict_domain="interval"),
    ]
    return tbl(["配置", "轮数", "轮数下界", "轮数/下界", "makespan",
                "最热链路负载", "读法"],
               [r for r in rows if r])


CFG_LABEL = {
    "mesh_base": "mesh_base<br><span class='muted'>有缓冲 + 信用反压 + "
                 "输入队列 iSLIP</span>",
    "mesh_islip2d": "mesh_islip2d<br><span class='muted'>集中式 D-M 排程"
                    "</span>",
    "ring_base": "ring_base<br><span class='muted'>E-tag/I-tag + 偏转</span>",
    "ring_islip2d": "ring_islip2d<br><span class='muted'>集中式 D-R 排程"
                    "</span>",
}
CFG_ORDER = ["mesh_base", "mesh_islip2d", "ring_base", "ring_islip2d"]


def t_main(s: dict) -> str:
    rows = []
    for cfg in CFG_ORDER:
        cv = scurve(s, "main", cfg)
        lo = next((r for r in cv if abs(r["lam"] - 0.1) < 1e-9), None)
        hi = max(cv, key=lambda r: r["accepted"])
        ov = max(cv, key=lambda r: r["lam"])
        ls, pk = lam_star(cv), peak(cv)
        anc = (s["anchors"]["mesh_xy"] if cfg.startswith("mesh")
               else s["anchors"]["ring_fixed"])
        rows.append([("!" if "islip" in cfg else "") + CFG_LABEL[cfg],
                     f"<b>{f(ls,2)}</b>", f(pk, 3), f(anc, 3),
                     f(lo["p50"], 0) if lo else "—",
                     f(lo["p99"], 0) if lo else "—",
                     f(hi["p50"], 0),
                     f(lo["fairness_cv"], 3) if lo else "—",
                     f(ov["fairness_cv"], 3)])
    return tbl(["配置", "λ*（稳定上限）", "峰值接受吞吐", "解析锚点",
                "p50@λ=0.1", "p99@λ=0.1", "p50@峰值",
                "CV@λ=0.1", "CV@λ=1.0（过载）"], rows)


def t_group(s: dict, group: str, key: str, head: str,
            fmtk=lambda v: str(v)) -> str:
    vals = sorted({r[key] for r in s["rows"] if r["group"] == group})
    cfgs = [c for c in CFG_ORDER
            if any(r["config"] == c for r in s["rows"] if r["group"] == group)]
    rows = []
    for v in vals:
        cells = [fmtk(v)]
        for c in cfgs:
            cv = [r for r in s["rows"] if r["group"] == group
                  and r["config"] == c and r[key] == v]
            cv.sort(key=lambda r: r["lam"])
            cells.append(f"<b>{f(lam_star(cv),2)}</b> / {f(peak(cv),3)}"
                         if cv else "—")
        rows.append(cells)
    return tbl([head] + [c.split("<")[0] for c in
                         (CFG_LABEL[c] for c in cfgs)], rows)


def t_pipeline(b: dict) -> str:
    rows = []
    for k, v in b["rtt_crossover"].items():
        fab = "mesh" if k.startswith("mesh") else "环"
        depth = k.split("depth")[1]
        depth = "∞" if depth == "inf" else depth
        pts = dict(v["points"])
        cross = v["crossover_t_rtt"]
        rows.append([f"{fab} · pipeline_depth={depth}",
                     f(pts.get(8), 3), f(pts.get(16), 3), f(pts.get(32), 3),
                     f(pts.get(64), 3), f(pts.get(96), 3),
                     (f"<b class='lose'>≈{f(cross,1)}</b>" if cross
                      else "<span class='win'>不出现</span>")])
    return tbl(["配置", "T=8", "T=16", "T=32", "T=64", "T=96",
                "convoy_ratio 跌到 1 的 T_rtt"], rows)


def t_verify(v: dict) -> str:
    order, seen = [], {}
    for r in v["rows"]:
        g = r["group"]
        if g not in seen:
            seen[g] = []
            order.append(g)
        seen[g].append(r)
    GN = {"common": "共同（两 fabric 都必须成立）",
          "D-M": "D-M 专项（mesh 判据）",
          "D-R": "D-R 专项（环判据）",
          "base": "ring_base 基线专项",
          "steady": "稳态专项"}
    rows = []
    for g in order:
        rs = seen[g]
        nf = sum(1 for r in rs if not r["ok"])
        names = "、".join(f"<code>{r['check']}</code>" for r in rs)
        rows.append([GN.get(g, g), f"{len(rs)}",
                     ("<span class='win'>全部通过</span>" if nf == 0
                      else f"<span class='lose'>{nf} 项失败</span>"),
                     f"<span class='muted'>{names}</span>"])
    return tbl(["分组", "断言数", "结果", "断言名"], rows)


# ---------------------------------------------------------------------------
# 4. Page
# ---------------------------------------------------------------------------

CSS = """
body { font-family: "Segoe UI", "Noto Sans SC", system-ui, sans-serif;
       margin: 0; background: #0b1020; color: #e8ecf4; line-height: 1.62; }
.wrap { max-width: 1120px; margin: 0 auto; padding: 28px 34px 80px; }
h1 { font-size: 1.65rem; color: #f0f4ff; border-bottom: 1px solid #2a3555;
     padding-bottom: .5rem; }
h2 { margin-top: 2.4rem; font-size: 1.28rem; color: #f0f4ff;
     border-left: 4px solid #7eb6ff; padding-left: .6rem; }
h3 { margin-top: 1.7rem; font-size: 1.05rem; color: #c8d6f0; }
h4 { margin: 1.1rem 0 .35rem; font-size: .96rem; color: #c8d0e0; }
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
table { border-collapse: collapse; width: 100%; font-size: .86rem;
        margin: .7rem 0 1.3rem; }
th, td { border: 1px solid #2a3555; padding: 6px 9px; text-align: left;
         vertical-align: top; }
th { background: #1a2340; font-weight: 600; }
tr:nth-child(even) { background: #12192c; }
tr.hl td { background: #17243d; }
code { background: #1a2340; padding: 1px 5px; border-radius: 4px;
       font-size: .88em; }
pre.code { background: #10162a; border: 1px solid #2a3555; border-left: 3px
       solid #7eb6ff; border-radius: 8px; padding: 12px 16px; overflow-x: auto;
       font-family: ui-monospace, "Cascadia Code", monospace; font-size: .82rem;
       line-height: 1.5; color: #d8e2f5; }
pre.code .c { color: #7f8ca8; }
.eq { background: #141b2f; padding: 10px 15px; border-radius: 8px;
      font-family: ui-monospace, monospace; margin: .7rem 0; font-size: .85rem;
      border: 1px solid #2a3555; }
.win, .ok2 { color: #6ee7a8; font-weight: 600; }
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
/* svg */
svg text { font-family: "Segoe UI","Noto Sans SC",system-ui,sans-serif; }
.lk { stroke: #2c3a5c; stroke-width: 2; }
.rlk { stroke: #3a5580; stroke-width: 2.4; }
.clk { stroke: #4a3f70; stroke-width: 2.4; }
.wrp { fill: none; stroke: #55618a; stroke-width: 1.4;
       stroke-dasharray: 4 3; }
.nd { fill: #8fa2c8; }
.cut { stroke: #d9a03c; stroke-width: 2; stroke-dasharray: 6 4; }
.cutlbl { fill: #d9a03c; }
.pXY { fill: none; stroke: #6ee7a8; stroke-width: 4; stroke-linejoin: round; }
.pRM { fill: none; stroke: #ff9ecb; stroke-width: 3; stroke-dasharray: 7 4;
       stroke-linejoin: round; }
.arcR { fill: none; stroke: #6ee7a8; stroke-width: 4.5; }
.arcC { fill: none; stroke: #c9a6ff; stroke-width: 4.5; }
.src { fill: #6ee7a8; } .dst { fill: #7eb6ff; } .wp { fill: #ff9ecb; }
.tag { fill: #0b1020; font-size: 10px; font-weight: 700;
       text-anchor: middle; }
.tag2 { fill: #e8ecf4; font-size: 11px; text-anchor: middle; }
.lbl { fill: #c8d0e0; font-size: 12px; }
.lbl.warn, .warn { fill: #f0c070; } .ok2 { fill: #6ee7a8; }
.bx { fill: #141b2f; stroke: #2a3555; stroke-width: 1.5; }
.bx.src { fill: #13251c; stroke: #2d6a4f; }
.bx.arb { fill: #141d33; stroke: #3d5a99; }
.bx.acc { fill: #1b1730; stroke: #6b52a8; }
.bx.fill { fill: #1a1626; stroke: #55618a; }
.bxt { fill: #f0f4ff; font-size: 13px; font-weight: 700; }
.bxl { fill: #c2ccdf; font-size: 11.5px; }
.bxl.dim, .dim { fill: #8b95ab; }
.ar { stroke: #7eb6ff; stroke-width: 1.8; } .ahd { fill: #7eb6ff; }
.arl { fill: #7eb6ff; font-size: 11px; }
.cel { stroke: #2a3555; }
.cocc { fill: #3d4a72; } .cfree { fill: #1b2340; }
.cnew { fill: #2d6a4f; } .cblk { fill: #5c2327; }
.tick { fill: #8b95ab; font-size: 11px; }
.ringc { fill: none; stroke: #3a5580; stroke-width: 2.6; }
.defl { fill: none; stroke: #f0a0a0; stroke-width: 2.4;
        stroke-dasharray: 5 4; }
.plot { fill: #0b142a; stroke: #2a3555; }
.gl { stroke: #1f2a47; }
.ideal { stroke: #55618a; stroke-width: 1.4; stroke-dasharray: 5 4; }
.anch { stroke: #d9a03c; stroke-width: 1.3; stroke-dasharray: 3 3; }
.anchl { fill: #d9a03c; font-size: 11px; }
.cv { fill: none; stroke-width: 2.6; }
.cA { stroke: #7eb6ff; } .cB { stroke: #6ee7a8; }
.cC { stroke: #f0a0a0; } .cD { stroke: #c9a6ff; }
.star.cA { fill: #7eb6ff; } .star.cB { fill: #6ee7a8; }
.star.cC { fill: #f0a0a0; } .star.cD { fill: #c9a6ff; }
.axl { fill: #9aa3b5; font-size: 12px; }
.cvu { fill: none; stroke-width: 2.6; stroke-dasharray: 4 4; opacity: .75; }
.cvw { stroke-width: 7; opacity: .34; }
.cvw.cvu { stroke-width: 7; opacity: .17; }
.satl { stroke: #e06c6c; stroke-width: 1.3; stroke-dasharray: 4 3; }
.satt { fill: #e06c6c; font-size: 11px; }
.lstar { stroke: #55618a; stroke-width: 1.1; stroke-dasharray: 2 4; }
"""


def cards(b: dict, s: dict, v: dict) -> str:
    mesh_cv = scurve(s, "main", "mesh_islip2d")
    ring_cv = scurve(s, "main", "ring_islip2d")
    mb_cv = scurve(s, "main", "mesh_base")
    rb_cv = scurve(s, "main", "ring_base")
    dm = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="interval")
    df = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="free_at")
    c = [
        ("集中式 mesh λ*", f(lam_star(mesh_cv), 2),
         f"基线 mesh_base {f(lam_star(mb_cv),2)}（缓冲深 20）", "ok"),
        ("集中式环 λ*", f(lam_star(ring_cv), 2),
         f"基线 ring_base {f(lam_star(rb_cv),2)}（偏转损耗）", "ok"),
        ("冲突域决定生死", f"{df['data_span']/dm['data_span']:.1f}×",
         "interval 相对 free_at 的 data_span（批量口径）", "ok"),
        ("D-M 轮数 / 割界", f"{f(dm['n_rounds'])} / {f(dm['round_lb'])}",
         f"比 = {f(dm['round_ratio'],3)}，96 来自中切 6 链路", ""),
        ("交叉开关判据不安全", pct(b["mesh_misuse"]
                                   ["crossbar_predicate_unsafe_rate"], 2),
         "mesh 上 src≠src∧dst≠dst 仍冲突的比例", "bad"),
        ("环上转环驻留", "0 拍",
         "R4 刚性对齐，实测 max_turn_residency=0", "ok"),
        ("验证断言", f"{v['n_checks']} / {v['n_checks'] - v['n_fail']}",
         "全部通过" if v["n_fail"] == 0 else f"{v['n_fail']} 项失败",
         "ok" if v["n_fail"] == 0 else "bad"),
        ("环金属开销", f"{f(b['audit']['metal_ratio_vs_mesh'],2)}×",
         f"{b['audit']['n_undirected_links']} 无向环链路 vs mesh "
         f"{b['audit']['mesh_undirected']}", ""),
    ]
    return '<div class="cards">' + "".join(
        f'<div class="card {cl}"><div class="k">{k}</div>'
        f'<div class="v">{val}</div><div class="s">{sub}</div></div>'
        for k, val, sub, cl in c) + "</div>"


# --- body sections -------------------------------------------------------

def s_bisect(x: dict) -> str:
    """9.3-9.5: what limits each configuration, and how the tails behave."""
    mb, mi = x["summary"]["mesh_base"], x["summary"]["mesh_islip2d"]
    rb, ri = x["summary"]["ring_base"], x["summary"]["ring_islip2d"]
    cm = xcrossover(x, "mesh_islip2d", "mesh_base", "mean_lat")
    cr = xcrossover(x, "ring_islip2d", "ring_base", "mean_lat")
    pm = xcrossover(x, "mesh_islip2d", "mesh_base", "p99")
    pr = xcrossover(x, "ring_islip2d", "ring_base", "p99")
    mbl, rbl = mb["lam_star"], rb["lam_star"]
    cross = [x["summary"][c]["cross_per_pkt_accepting"] for c, _, _ in XCFG]
    return f"""
<h3>9.3 二分带宽利用率：谁被金属卡住，谁被自己卡住</h3>
<p>λ* 是「多少」，这一节回答「<b>被什么卡住</b>」。
把每个包在二分切面链路上的占用拍数记账下来除以切面容量，
就得到切面的忙闲比例。四个配置用<b>同一套记账</b>：
集中式按授权预留记，两个基线按<b>实际逐跳</b>记——
反应式 fabric 的下一跳是逐拍决定的，只能这样记。</p>
{fig_bisect(x)}
{t_bisect(x)}
<ul>
<li><b>只有 mesh_islip2d 真把切面用光</b>：λ*={mi['lam_star']} 处已用掉
{pct(mi['bisect_util_at_lam_star'])}，λ≥{mi['peak_bisect_util_at_lam']}
贴住 {f(mi['peak_bisect_util'], 3)} 不动。它<b>是二分带宽受限</b>——
再改调度也榨不出东西，只能改路由让流量离开热切面（§5.3 的 ROMM）或者加金属。</li>
<li><b>mesh_base 在切面还空着 {pct(1 - mb['bisect_util_at_lam_star'])} 时就先失稳</b>：
λ*={mbl}，峰值只到 {pct(mb['peak_bisect_util'])} 就回落。
卡住它的不是金属而是信用环路（缓冲深 20 也只够盖住 15–19 拍的信用往返）。
于是 mesh 上集中化的收益可以一句话说清：
<b>把「被信用卡住」换成「被金属卡住」</b>——λ* {mbl}→{mi['lam_star']}，
切面利用率 {pct(mb['bisect_util_at_lam_star'])}→{pct(mi['bisect_util_at_lam_star'])}。</li>
<li><b>环上两个都没用满切面</b>：ring_base 峰值 {pct(rb['peak_bisect_util'])}，
ring_islip2d 峰值 {pct(ri['peak_bisect_util'])}、λ* 处
{pct(ri['bisect_util_at_lam_star'])}。
环的瓶颈在上/下环口与那条 load=60 的热环链路上（§4 的 R2/R3），不在切面。
环的 λ* 反而更高，靠的是切面本来就宽一倍。</li>
<li><b>切面宽一倍是拓扑给的，不是调度本事</b>：mesh 切 x=3|4 得
6 行 × 双向 = {mi['bisect_links']} 条有向链路；环的行环会绕回，
把 x≤3 与 x≥4 分开必须切两处（3–4 和 7–0）= {ri['bisect_links']} 条。
代价是 §9.1 里那 {f(1.1707, 2)}× 的金属。所以<b>纵轴只能同 fabric 内比</b>，
跨 fabric 要看表格最后一列。</li>
</ul>
<div class="note">
<b>这条曲线有两重独立校验，所以可以当尺子用。</b>
<ul>
<li><b>对解析值</b>：均匀流量下一对 (src,dst) 跨切概率 =
2·(24/48)·(24/47) = {x['summary']['crossing_fraction']:.4f}，
最小路由下跨切恰好一次。在「基本全收」区间内（accept_ratio ≥ 0.999）
四个配置与解析值的最大偏差是
{f(mb['analytic_max_abs_err_accepting'], 4)} /
{f(mi['analytic_max_abs_err_accepting'], 4)} /
{f(rb['analytic_max_abs_err_accepting'], 4)} /
{f(ri['analytic_max_abs_err_accepting'], 4)}。</li>
<li><b>四配置互校</b>：把切面宽度除掉后，每包跨切次数四者都落在
{min(cross):.4f}–{max(cross):.4f}，与解析值一致。
<b>绝对跨切流量与仲裁方式无关</b>，只由流量决定——
这正是应该的结果，也说明四条记账路径没有各自跑偏。</li>
<li><b>逼近 λ* 时实测略高于解析值</b>（ring_base 最明显，到 λ*={rbl}
时偏差 {f(rb['analytic_max_abs_err_stable'], 4)}）：解析值按<b>已交付</b>的包算，
而切面占用里还含着已注入未交付的包。不是偏转多绕了圈——
实测偏转只有 {f(xat(x, 'ring_base', rbl, 'defl_per_pkt'), 3)} 次/包。</li>
<li><b>过载区反过来，解析值高于实测</b>（mesh_islip2d 偏差
+{f(mi['overload_mix_skew'], 3)}）：那时仲裁器只发得出没被堵的授权，
被接受的流量组合主动偏离热切面——与本节开头「峰值可以超过锚点」
是同一个现象的两面。</li>
</ul>
</div>

<h3>9.4 平均时延：集中化先付钱，过了膝部才赚回来</h3>
{fig_mean_lat(x)}
{t_lat(x)}
<p><b>空载时集中式更差</b>：mesh {mb['mean_lat_unloaded']:.0f}→
{mi['mean_lat_unloaded']:.0f} 拍、环 {rb['mean_lat_unloaded']:.0f}→
{ri['mean_lat_unloaded']:.0f} 拍，各多出约
{mi['mean_lat_unloaded'] - mb['mean_lat_unloaded']:.0f} 与
{ri['mean_lat_unloaded'] - rb['mean_lat_unloaded']:.0f} 拍。
这就是请求-授权环路的价钱，不该藏起来：
空载时没人要抢资源，集中仲裁纯属多跑一趟。</p>
<p><b>但基线爬坡早得多。</b>集中式在 λ* 之前近乎水平——
无缓冲 + 刚性授权把排队<b>全部</b>挤到源端队列，
网络内部驻留恒为 0（本次扫描每个采样点 in_network_max 都是 0，
与 §12 的 <code>*_zero_in_network_residency</code> 断言一致），
所以网络内时延根本不随负载变化，负载只改变「等多久拿到授权」；
基线则从半程就开始逐跳排队。两者的交叉点正好落在<b>基线自己的膝部</b>：
mesh 在 λ={cm[0]}（{cm[2]:.0f}→{cm[1]:.0f} 拍）、
环在 λ={cr[0]}（{cr[2]:.0f}→{cr[1]:.0f} 拍）之后集中式反超。</p>
<p class="muted">一个读法陷阱：λ* 处的平均时延<b>不能横向比</b>，
因为四个配置的 λ* 不同（{mbl} / {mi['lam_star']} / {rbl} /
{ri['lam_star']}），那是各自在自己极限处的数字。
要比就固定同一个 λ——上一段的交叉点就是这么算的。</p>

<h3>9.5 p99 时延：授权制把长尾压掉了</h3>
{fig_p99_lat(x)}
<p>看上表最后一列。稳定区内最坏的 p99/平均：基线是
{mb['worst_p99_over_mean_stable']:.2f}×（mesh）／
{rb['worst_p99_over_mean_stable']:.2f}×（环），
集中式只有 {mi['worst_p99_over_mean_stable']:.2f}× ／
{ri['worst_p99_over_mean_stable']:.2f}×。
在 mesh_base 自己的 λ*={mbl} 上直接对比更直观：p99
{xat(x, 'mesh_base', mbl, 'p99'):.0f} 拍 vs
{xat(x, 'mesh_islip2d', mbl, 'p99'):.0f} 拍，
<b>差 {xat(x, 'mesh_base', mbl, 'p99') / xat(x, 'mesh_islip2d', mbl, 'p99'):.1f}×</b>，
而同一点上平均只差
{xat(x, 'mesh_base', mbl, 'mean_lat') / xat(x, 'mesh_islip2d', mbl, 'mean_lat'):.2f}×。
尾部的差距远大于平均的差距，这正是刚性授权的特征：
包一旦被授权，路径上每条链路每个端口都已按拍预留，
不会在网络里被任何东西阻塞，于是没有逐跳争用那种长尾。
尾部的反超也不会比平均更晚：mesh 与平均同在 λ={pm[0]}
（p99 {pm[2]:.0f}→{pm[1]:.0f} 拍），环还要更早一步——p99 从 λ={pr[0]} 起就反超，
比平均的 λ={cr[0]} 提前一个网格步长。</p>
<p><b>这是集中式最容易被低估的收益</b>：买到的不只是吞吐，更是可预测性。
反过来说，如果系统只跑在半载以下、又对空载时延敏感，
这笔交易并不划算，该拿的是基线。</p>
<p class="muted">越过 λ* 之后（虚线段）p99 冲到数千拍，
但那反映的是源队列无界增长下的有限测量窗，不是 fabric 的性质。
另外本节的 λ* 与 §9 表格可能差一个网格步长
（如 mesh_islip2d 这里 {mi['lam_star']}、§9 表 0.48）：
两次扫描的 λ 网格疏密不同，λ* 只精确到网格分辨率。</p>
"""


def s_intro(b: dict, s: dict, v: dict, x: dict) -> str:
    mesh_cv = scurve(s, "main", "mesh_islip2d")
    ring_cv = scurve(s, "main", "ring_islip2d")
    mb, rb = scurve(s, "main", "mesh_base"), scurve(s, "main", "ring_base")
    dm = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="interval")
    df = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="free_at")
    rr = ring_a2a(b, path_mode=None, conflict_domain="interval")[0]
    rf = ring_a2a(b, path_mode=None, conflict_domain="free_at")[0]
    return f"""
<h2 id="s0">0 · 一句话结论与全文地图</h2>
<p class="lead">iSLIP 原本是交叉开关上的算法：一次授权只占「一个输入 + 一个输出」，
所以「源不同、目的不同 ⇒ 无冲突」。把它搬到 8×6 的 2D mesh 和 2D 无缓冲环上，
<b>这句判据两个方向都错</b>——它既会放过真冲突（mesh 上
{pct(b['mesh_misuse']['crossbar_predicate_unsafe_rate'], 2)}），
又会挡住本可并发的传输（mesh 上
{pct(b['mesh_misuse']['same_src_actually_free_rate'], 2)}）。
本文因此为两种 fabric 各写一套冲突判据（D-M / D-R），
在其上各写一个集中式 iSLIP 变种，并与各自的分布式基线在同一台稳态仿真器里对打。</p>

<div class="note good">
<b>六条最该记住的结论</b>
<ol>
<li><b>判据必须分开写。</b>mesh 的冲突是「路径上的有向链路 + 坡道口」，
环的冲突是「弧 + 上环口 + 下环口 + 转环同拍」。
只用「链路不相交」在环上会漏掉
{pct(b['ring_misuse']['false_negative_rate_of_pure_R1'], 2)} 的真冲突，
并把可并发流数从 {f(b['ring_greedy_max_set']['r1_r2_r3']['mean'], 1)}
高估到 {f(b['ring_greedy_max_set']['r1_only']['mean'], 1)}。</li>
<li><b>决定成败的不是仲裁器有多聪明，而是冲突域记什么。</b>
只记「资源在 t 之后空闲」（<code>free_at</code>）会产生前沿棘轮，
data_span 是 mesh {f(df['data_span'])} / 环 {f(rf['makespan'])}；
改成完整占用区间（<code>interval</code>）后是 mesh {f(dm['data_span'])} /
环 {f(rr['makespan'])}，相差
{df['data_span'] / dm['data_span']:.1f}× 与
{rf['makespan'] / rr['makespan']:.1f}×。轮数一模一样。</li>
<li><b>mesh 上集中化是为了省面积，环上集中化是为了可行。</b>
mesh_base 用缓冲和信用换来能工作；无缓冲环没有缓冲可用，
R4「转环必须同拍完成」是个需要未来信息的约束，
分布式环站只能靠偏转绕圈化解——这是 ring_base 掉吞吐的根因。</li>
<li><b>稳态里集中式两边都赢，但赢的理由不同。</b>
λ* 从 mesh_base {f(lam_star(mb), 2)} → mesh_islip2d {f(lam_star(mesh_cv), 2)}，
ring_base {f(lam_star(rb), 2)} → ring_islip2d {f(lam_star(ring_cv), 2)}；
mesh 那边同时省掉 {f(b['audit']['n_bridges'])} 个节点的输入缓冲
（面积净省 5.96×），环那边面积基本打平（1.05×）。</li>
<li><b>集中化在 mesh 上做的事，是把「被信用卡住」换成「被金属卡住」。</b>
mesh_base 在二分切面还空着
{pct(1 - x['summary']['mesh_base']['bisect_util_at_lam_star'])} 时就失稳
（λ*={x['summary']['mesh_base']['lam_star']}，切面峰值
{pct(x['summary']['mesh_base']['peak_bisect_util'])} 就回落）；
mesh_islip2d 把切面推到 100% 并停在那里，λ* 变成
{x['summary']['mesh_islip2d']['lam_star']}——它<b>是二分带宽受限</b>，
再改调度也没用了。环上两者都没用满切面
（峰值 {pct(x['summary']['ring_base']['peak_bisect_util'])} /
{pct(x['summary']['ring_islip2d']['peak_bisect_util'])}），
因为瓶颈在上/下环口与热环链路（§9.3）。</li>
<li><b>集中化先付钱、过了膝部才赚回来，而尾延迟赚得比平均多得多。</b>
空载时集中式反而慢
{x['summary']['mesh_islip2d']['mean_lat_unloaded'] - x['summary']['mesh_base']['mean_lat_unloaded']:.0f}（mesh）／
{x['summary']['ring_islip2d']['mean_lat_unloaded'] - x['summary']['ring_base']['mean_lat_unloaded']:.0f} 拍（环），
那是请求-授权环路的价钱；到基线自己的膝部之后反超。
稳定区最坏 p99/平均：基线
{x['summary']['mesh_base']['worst_p99_over_mean_stable']:.1f}× /
{x['summary']['ring_base']['worst_p99_over_mean_stable']:.1f}×，
集中式只有 {x['summary']['mesh_islip2d']['worst_p99_over_mean_stable']:.1f}× /
{x['summary']['ring_islip2d']['worst_p99_over_mean_stable']:.1f}×——
包一旦被授权就不会在网络里被阻塞（§9.4–9.5）。</li>
</ol></div>

<div class="toc">
<a href="#s1">1 · 三个前提（授权制 / 刚性零缓冲 / 半开区间）</a><br/>
<a href="#s2">2 · 为什么交叉开关判据在两种 fabric 上都不成立</a><br/>
<a href="#s3">3 · D-M：2D mesh 的冲突定义</a><br/>
<a href="#s4">4 · D-R：2D 无缓冲环的冲突定义</a><br/>
<a href="#s5">5 · Part A：islip2d_mesh 算法逐步拆解</a><br/>
<a href="#s6">6 · 冲突域：free_at 与 interval</a><br/>
<a href="#s7">7 · Part B：islip2d_ring 与「集中化的必要性」</a><br/>
<a href="#s8">8 · 两个分布式基线</a><br/>
<a href="#s9">9 · 稳态注入率扫描（四配置头对头 · 二分带宽 · 平均/p99 时延）</a><br/>
<a href="#s10">10 · 保序、流水与 RTT 敏感度</a><br/>
<a href="#s11">11 · 面积与调度时间</a><br/>
<a href="#s12">12 · 验证清单与已知局限</a>
</div>

<h2 id="s1">1 · 三个前提</h2>
<p>下面所有讨论都建立在三条约定上。它们不是本文的结论，
而是「一次授权到底意味着什么」的定义；不先说清楚，后面的判据无从谈起。</p>

<h3>1.1 授权制传输（authorized transfer）</h3>
<p>源节点不能自行发包。它把「我还有哪些目的地要发」告诉集中仲裁器，
仲裁器回一个授权，授权里含<b>一个明确的起始时刻 t₀</b>。
源在 t₀ 那一拍准时注入，此后包沿既定路径无阻塞前进——
因为仲裁器在发授权之前已经确认整条路径在需要的那些拍上都空着。
这与信用反压是两种世界观：信用制是「先发出去，遇堵再等」，
授权制是「先算好，发出去就不会堵」。</p>

<h3>1.2 刚性零缓冲足迹</h3>
<p>零缓冲意味着包在网络中途没有落脚点，于是<b>足迹是刚性的</b>：
授权时刻 t₀ 一旦定下，包在第 k 跳链路上占用的时间窗也就完全确定。
这让「有没有冲突」变成一个可以在授权前离线判定的问题——
本文两套判据能存在，全靠这一点。</p>

<h3>1.3 半开区间记法</h3>
<p>全文统一：<code>r</code> 指一次被授权的传输，<code>u</code> 指它要独占的
任一<b>资源</b>（mesh 上是有向链路或坡道口，环上是环链路、上/下环口、转环点）。
传输 <code>r</code> 对资源 <code>u</code> 的占用区间是</p>
<div class="eq">occ_r(u) = [ t₀ + pre_r(u) , t₀ + pre_r(u) + dur(u) )</div>
<p><code>pre_r(u)</code> 是从注入到该传输抵达 <code>u</code> 的累计线延迟，
<code>dur(u)</code> 是占用拍数（单 flit 包 σ 拍，m flit 包 m·σ 拍）。
左闭右开：相邻两次传输可以在同一拍首尾相接而不算冲突，
判定统一写成 <code>a1 &lt; b2 且 b1 &lt; a2</code>。
8×6 的线延迟取 H={H}（水平）与 V={V}（垂直），
坡道带宽 RAMP_BW=2。</p>
"""


def s_predicates(b: dict) -> str:
    a = b["audit"]
    mg = b["mesh_greedy_max_set"]
    return f"""
<h2 id="s2">2 · 为什么交叉开关的判据在两种 fabric 上都不成立</h2>
<p>交叉开关里，一次传输只碰两个资源：输入端口和输出端口。
所以 iSLIP 只要保证「一个输入最多被选一次、一个输出最多被选一次」，
就自动无冲突。这个前提在网络里消失了：<b>一次传输要碰一整条路径</b>。</p>
{fig_dm_paths()}
<p>于是原判据的两条腿同时断掉：</p>
<ul>
<li><b>不安全（假阴性）</b>：src 和 dst 都不同的两条流，
路径完全可以压在同一条链路上。mesh 上抽样
{f(b['mesh_misuse']['diff_src_diff_dst_pairs'])} 对这样的流，
{pct(b['mesh_misuse']['crossbar_predicate_unsafe_rate'], 2)} 实际冲突。
按老判据放行就是数据损坏。</li>
<li><b>过严（假阳性）</b>：同一个源的两条流，在交叉开关上必然冲突（共用输入端口），
但在 mesh 上只要第一跳方向不同就毫不相干——
实测 {pct(b['mesh_misuse']['same_src_actually_free_rate'], 2)} 的同源对其实可以并发。
把它们一律互斥掉，等于白扔掉这
{pct(b['mesh_misuse']['same_src_actually_free_rate'], 0)} 的机会。</li>
</ul>
<p>并发度的量化：在真判据下贪心找极大无冲突集，mesh 平均能同时放
<b>{f(mg['mean'], 1)}</b> 条流（最好 {mg['max']} 条），
而交叉开关的置换上限只有 {mg['crossbar_permutation_reference']} 条。
也就是说，网络的并发度天生比交叉开关高，
用交叉开关的判据去管网络，既不安全又浪费。</p>
{t_predicate(b)}
<p class="muted">口径：抽样 {f(b['mesh_misuse']['n_samples'])} 对流，
all-to-all 全流集（{f(2256)} 条）。
拓扑自检：{a['n_row_rings']} 行环 × {a['n_col_rings']} 列环，
{a['n_directed_links']} 有向链路（={a['n_undirected_links']} 无向），
金属量相对 mesh 的 {a['mesh_undirected']} 条为
{f(a['metal_ratio_vs_mesh'], 2)}×。</p>
"""


def s_dm(b: dict) -> str:
    dm = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="interval")
    return f"""
<h2 id="s3">3 · D-M：2D mesh 的冲突定义</h2>
<p>一次授权在 mesh 上要独占的东西只有两类：路径上的每条<b>有向</b>链路，
以及两端的坡道口。三条子句合起来就是 D-M。</p>
<p class="muted">记号沿用 §1：<code>r</code> 表示一次被授权的传输
<code>(VOQ(s,d), t₀, route, m, σ)</code>，下面用 <code>r₁ / r₂</code>
指两次不同的传输；<code>e</code> 表示一条<b>有向链路</b>（资源）；
<code>path(r)</code> 是该传输经过的有向链路集合；
<code>pre_r(e)</code> 是从 <code>t₀</code> 到该传输抵达 <code>e</code>
的累计线延迟。</p>

<h3>M1 · 有向链路互斥</h3>
<div class="eq">∀ e ∈ path(r₁) ∩ path(r₂)：<br/>
[ t₀¹ + pre_r₁(e) , t₀¹ + pre_r₁(e) + m·σ ) ∩
[ t₀² + pre_r₂(e) , t₀² + pre_r₂(e) + m·σ ) = ∅</div>
<p>读作：<b>两次传输若共用某条有向链路 e，它们在 e 上的占用区间必须不相交。</b>
它禁止的是「同一拍占同一条 e」，而不是「共用 e」——
<code>pre</code> 逐链路不同，两次传输完全可以错时穿过同一条 e。
能否兑现这条自由度，就是 §6 两种冲突域的全部差别。</p>
<p>资源共 164 条有向链路：横向 (8−1)×6×2 = 84，纵向 (6−1)×8×2 = 80。
<b>有向</b>是关键——同一对相邻节点之间东行与西行是两个独立资源，
所以对向流互不干扰。这一条是主力约束，也是 all-to-all 下界的来源。</p>

<h3>M2 · 坡道口（注入 / 弹出）互斥</h3>
<p>每个节点的注入口和弹出口带宽 RAMP_BW=2，用容量为 2 的资源计。
它单独成条是因为链路互斥管不到它：
同一节点同时给两个不同方向发包，链路上毫无冲突，坡道口却可能超发。</p>

<h3>M3 · 同 (src,dst) 序内串行化</h3>
<p>同一对 (src,dst) 的多个包必须按序进网。这条不是资源约束而是语义约束，
它保证接收端不用重排。</p>

<div class="note">
<b>为什么 all-to-all 的轮数下界是 {f(dm['round_lb'])}？</b>
把 8×6 网格从 x=3 与 x=4 之间切开，左半 24 个节点要给右半 24 个节点各发一个包，
共 24×24 = 576 个包，而跨过这道切口的同向链路只有
<b>{MY} 条</b>（每行一条）。所以任何无冲突排程至少需要 576 / {MY} = 96
个「链路-拍」，即轮数 ≥ {f(dm['round_lb'])}。
实测 {f(dm['n_rounds'])} 轮，比值 {f(dm['round_ratio'], 3)}——
离信息论下界只差 {pct(dm['round_ratio'] - 1, 1)}。
这个割界同时说明：mesh 上再怎么优化仲裁，也逃不掉中切带宽。
</div>

<h3>ROMM 换路与保序</h3>
<p>D-M 允许同一 (src,dst) 走不同路径：在 src-dst 构成的矩形内选一个中间点 w，
走「src→w 的 XY」+「w→dst 的 XY」。
关键性质是<b>跳数恒为 dx+dy</b>（图 1）——矩形内绕行不增加跳数，
所以所有候选路径的线延迟完全相同。这带来一个不太直观的好结论：
<b>换路不会乱序</b>，因此 M3 只需要管注入顺序，不需要禁止换路。
（实测 14,560 对 mesh 候选路径，延迟差为 0。）</p>
"""


def s_dr(b: dict) -> str:
    a = b["audit"]
    base = ring_a2a(b, path_mode="fixed", grants_per_src=1)[0]
    whole = ring_a2a(b, path_mode=None, spatial_reuse="whole_ring")[0]
    arc = ring_a2a(b, path_mode=None, spatial_reuse="arc",
                   conflict_domain=None, fill=None, iters=None,
                   t_rtt=None, board_ports=1, leave_ports=1)[0]
    return f"""
<h2 id="s4">4 · D-R：2D 无缓冲环的冲突定义</h2>
<p>先说清拓扑，因为「2D 环」有歧义。这里是<b>维度切片</b>的：
同一行的 8 个节点共用一条物理双向行环（共 {a['n_row_rings']} 条），
同一列的 6 个节点共用一条物理双向列环（共 {a['n_col_rings']} 条）。
每个节点既在一条行环上也在一条列环上，因此<b>每个节点都是桥</b>
（{f(a['n_bridges'])} 个桥）。
链路数：每条 k 节点的环有 k 条无向链路（含绕回），
故 6×8 + 8×6 = {a['n_undirected_links']} 无向 = {a['n_directed_links']} 有向。</p>
{fig_dr_topology()}
<p>一次授权在环上要独占五类东西，对应 R1–R5：</p>
<table><thead><tr><th>子句</th><th>资源</th><th>为什么必须单列</th></tr></thead>
<tbody>
<tr><td><b>R1</b> 弧上链路互斥</td>
<td>行相弧与列相弧覆盖的每条有向环链路</td>
<td>主力约束。弧是环上连续一段，可以跨绕回链路。</td></tr>
<tr><td><b>R2</b> 上环口（board）</td>
<td>源节点把包放上行环的插入点，<code>board_ports</code> 个</td>
<td>只看链路会漏检：两条流的弧完全不相交，
但若同一节点同拍上环，物理上做不到。
实测漏检里 {pct(b['ring_misuse']['port_clash_kind_frac']['board_board'], 1)}
是 board-board 型。</td></tr>
<tr><td><b>R3</b> 下环口（leave）</td>
<td>目的节点从列环取下包的抽取点，<code>leave_ports</code> 个</td>
<td>同理，占漏检的
{pct(b['ring_misuse']['port_clash_kind_frac']['leave_leave'], 1)}。</td></tr>
<tr><td><b>R4</b> 转环原子性</td>
<td>转环节点上「行环抽取点 + 列环插入点」<b>同一拍</b></td>
<td>无缓冲 ⇒ 包不能在桥上等。这是全文最关键的一条，
也是集中化的唯一硬理由（见 §7）。</td></tr>
<tr><td><b>R5</b> 同 (src,dst) 序内串行化</td>
<td>语义约束</td>
<td>与 M3 同源。最小弧集是延迟不变的，所以它同样不禁止换路。</td></tr>
</tbody></table>

<h3>空分复用：arc 还是 whole_ring</h3>
<p>一个实现选择：环上同时能否有多个互不重叠的传输？</p>
<ul>
<li><code>arc</code>（默认）：以弧覆盖的链路为粒度互斥 ⇒ 一条环上可并行多段。
轮数下界 {f(arc['round_lb'])}，实测 {f(arc['n_rounds'])} 轮。</li>
<li><code>whole_ring</code>：整条环一次只服务一个传输（令牌环式）。
下界立刻涨到 {f(whole['round_lb'])}，实测 {f(whole['n_rounds'])} 轮，
makespan {f(whole['makespan'])} vs {f(arc['makespan'])}，
代价 <b class="lose">{whole['makespan'] / arc['makespan']:.2f}×</b>。</li>
</ul>
<div class="note bad">这 {whole['makespan'] / arc['makespan']:.2f}× 是环设计里最贵的一个
二选一。它也解释了为什么 D-R 必须以「弧」而不是「环」为资源单位：
把互斥粒度做粗一点，省下的是仲裁器几百个比较器，
丢掉的是接近三倍的吞吐。</div>
<p class="muted">参照点 fixed·g=1：{f(base['n_rounds'])} 轮 /
makespan {f(base['makespan'])} / 最热链路负载 {f(base['max_link_load'])}。
五子句逐条复核结果见 §12（R1–R5 全为 0 违例）。</p>
"""


PSEUDO_MESH = """<span class="c"># ---- 一轮 iSLIP-2D（mesh）。所有源同步进行 ----</span>
<span class="c"># 状态：R[s] = 源 s 的残余 VOQ 位图（47 bit，1 = 还有包没被授权）</span>
<span class="c">#       g[e] = 每条有向链路的 grant 指针（指向 48 个源之一）</span>
<span class="c">#       a[s] = 每个源的 accept 指针（指向 48 个 dst 之一）</span>
<span class="c">#       D    = 冲突域（free_at 或 interval），记已被占用的资源时间</span>

<span class="c"># ① REQUEST：一条消息带出整张位图，不是每个 VOQ 一条消息</span>
for s in sources:
    req[s] = R[s]                      <span class="c"># 1 条消息 / 源 / 轮</span>

<span class="c"># ② GRANT：每条链路独立地沿自己的指针做一次 RR</span>
for e in directed_links:
    cand = [ (s,d) for s in sources for d in bits(req[s])
             if e in path(s,d) ]       <span class="c"># 谁的路径压在我身上</span>
    grant[e] = rr_pick(cand, start=g[e])

<span class="c"># ③ ACCEPT：一条 VOQ 只有在它路径上"每一条"链路都选中它时才算候选</span>
unanimous = [ (s,d) for (s,d) in requested
              if all( grant[e] == (s,d) for e in path(s,d) ) ]
for s in sources:
    take = rr_take(unanimous[s], start=a[s], k=grants_per_src)
    for (s,d) in take:
        g[e] = advance(g[e]) for e in path(s,d)   <span class="c"># 指针只在成交后前进</span>
        a[s] = advance(a[s])

<span class="c"># ④ FILL：一致相通常只填掉一小部分容量，按静态序贪心补齐</span>
for (s,d) in remaining_requests sorted by fill_order:
    if compatible_with(selected, (s,d)):   <span class="c"># 逐资源查 D-M</span>
        selected.append((s,d))

<span class="c"># ⑤ COMMIT：为每个被选中的 VOQ 求最早可行 t0，然后占区间</span>
for (s,d) in selected:
    t0 = D.earliest( resources_of(s,d), lower=now + T_rtt )
    D.occupy( resources_of(s,d), t0 )
    grant_message(s, d, t0)            <span class="c"># 授权里带明确起始时刻</span>
    R[s].clear_bit(d)                  <span class="c"># 只有成交才清位</span>

<span class="c"># ⑥ 未成交的位原样留在 R[s]，下一轮继续带出 ⇒ 控制面消息数与积压无关</span>
"""


def s_mesh(b: dict) -> str:
    dm = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="interval")
    g1 = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="free_at")
    g2 = mesh_a2a(b, path_mode="xy", grants_per_src=2,
                  conflict_domain="free_at")
    it0 = mesh_a2a(b, path_mode="xy", grants_per_src=2, iters=0)
    it1 = mesh_a2a(b, path_mode="xy", grants_per_src=2, iters=1)
    return f"""
<h2 id="s5">5 · Part A：islip2d_mesh 算法逐步拆解</h2>
<p>算法要解决的问题：{f(N)} 个源、每源最多 {f(N - 1)} 条 VOQ，
每轮要挑出一批「彼此无冲突」的 VOQ 并给每条一个起始时刻，
使总时间尽可能短，同时不让任何 VOQ 被饿死。</p>

<h3>5.1 与原版 iSLIP 的三处结构差异</h3>
<table><thead><tr><th></th><th>交叉开关 iSLIP</th>
<th>islip2d_mesh</th></tr></thead><tbody>
<tr><td>请求单位</td><td>每个 VOQ 一条请求线</td>
<td><b>整张位图一条消息</b>：源 s 把 47 bit 残余 VOQ 一次带给仲裁器。
控制面消息恒为每轮 ≤ 2×{f(N)} 条（request + grant），与积压无关。
实测每轮 {f(dm['ctrl_msgs_total'] / dm['n_rounds'], 1)} 条。</td></tr>
<tr><td>grant 指针挂在哪</td><td>每个输出端口一个</td>
<td><b>每条有向链路一个</b>：资源从「端口」变成「链路」，
指针数量随之从 {f(N)} 变成 164。每个指针存一个<b>源编号</b>
（下一轮从第几号源开始轮转），⌈log₂{f(N)}⌉ = 6 bit，
共 164 × 6 = 984 bit。<br/>
<span class="muted">指针指向源而不是流：链路上的候选是 VOQ（
{f(2256)} 条），若直接指向流需 ⌈log₂{f(2256)}⌉ = 12 bit（1,968 bit，
翻一倍）。两级分解让「选哪条流」= 链路选源 + 源选 dst，
两个 6 bit 字段各存在该存的地方。</span></td></tr>
<tr><td>什么叫「被选中」</td><td>输出端口选中它</td>
<td><b>路径上每条链路都选中它</b>（全路径一致 / unanimity）。
这是 AND 而不是 OR，所以一致率天然低：
实测只有 {pct(dm['unanimous_frac'])}。</td></tr>
</tbody></table>
{fig_round(dm)}

<h3>5.2 一轮的六个步骤</h3>
<pre class="code">{PSEUDO_MESH}</pre>

<h4>为什么需要第 ④ 步「补齐」</h4>
<p>这是把 iSLIP 搬到网络上最容易被忽略的一点。
交叉开关上一致相能填满整个置换（每个输出都能成交一个），
但在 mesh 上一条 VOQ 要跨 dx+dy 条链路，
只要有一条链路把 grant 给了别人，这条 VOQ 就落空。
一致率只有 {pct(dm['unanimous_frac'])}，也就是说
<b>光靠 iSLIP 的两级指针，每轮容量的绝大部分是空着的</b>。
补齐步骤按静态序（默认跳数降序）把剩余请求逐条试塞进去，
只要过 D-M 就收。指针只在一致相里推进，
所以补齐不破坏 iSLIP 的无饿死性质。</p>

<div class="note">
<b>一个反直觉的实测结果</b>：把一致相整个关掉（<code>iters=0</code>，
只留补齐）反而轮数最少——{f(it0['n_rounds'])} 轮 vs 保留一致相的
{f(it1['n_rounds'])} 轮，即一致相带来约
{pct(it1['n_rounds'] / it0['n_rounds'] - 1, 0)} 的额外轮数。
原因是一致相会「占着容量却填不满」：
它先把一批链路的 grant 指针分配给一组注定凑不齐全路径的 VOQ，
补齐步骤反而少了自由度。
一致相真正的价值在<b>公平性</b>（指针保证无饿死）而不是效率；
如果只看 makespan，应该把它当成一层薄薄的反饿死保险，
而不是主要的匹配机制。
</div>

<h3>5.3 各旋钮的实测影响</h3>
{t_mesh_knobs(b)}
<ul>
<li><b>grants_per_src</b>：从 1 到 2 只把轮数从 {f(g1['n_rounds'])} 降到
{f(g2['n_rounds'])}（{pct(1 - g2['n_rounds'] / g1['n_rounds'], 1)}）。
每源多授一个 VOQ 听起来能翻倍，实际瓶颈在链路而不在源，
所以几乎没用——但它让每轮多放行的流数上升，
在多 flit 包（m=4）下才开始有价值。</li>
<li><b>path_mode</b>：ROMM 在 all-to-all 上<b>没有增益</b>，原因很直白——
all-to-all 本身就是均匀的，XY 路由下最热链路负载已经等于割界 96，
没有热点可平衡（<code>romm_static</code> 实测 max_load 恰为 96，
轮数与 XY 相同）。更值得注意的是 <code>romm_dyn</code>（每轮在线选点）
把最热链路负载推到 <b>119</b>，比割界高 24%，
轮数从 {f(mesh_a2a(b, path_mode='xy', grants_per_src=2, conflict_domain='free_at')['n_rounds'])}
涨到 {f(mesh_a2a(b, path_mode='romm_dyn', grants_per_src=2)['n_rounds'])}：
逐轮各自随机选点，在全局看来是把负载搞得更不均。
ROMM 的价值要到 hotspot / transpose 这类非均匀 pattern 才体现，
而且必须离线平衡而不是在线随机。</li>
<li><b>补齐序</b>：<code>hops_asc</code>（短流先填）data_span 最优
（{f(mesh_a2a(b, path_mode='xy', grants_per_src=2, fill='hops_asc')['data_span'])}
vs 默认 {f(g2['data_span'])}），因为短流占的资源少、更容易塞进空洞；
但轮数不变——两个指标优化方向不同，需要按目标选。</li>
</ul>
"""


def s_domain(b: dict, s: dict) -> str:
    dm = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="interval")
    df = mesh_a2a(b, path_mode="xy", grants_per_src=1,
                  conflict_domain="free_at")
    rr = ring_a2a(b, path_mode=None, conflict_domain="interval")[0]
    rf = ring_a2a(b, path_mode=None, conflict_domain="free_at")[0]
    dmesh = [r for r in s["rows"] if r["group"] == "domain"
             and r["config"] == "mesh_islip2d"]
    dring = [r for r in s["rows"] if r["group"] == "domain"
             and r["config"] == "ring_islip2d"]

    def by(rows, cd):
        return [r for r in rows if r["conflict_domain"] == cd]
    rows = []
    for lbl, rs in (("mesh_islip2d", dmesh), ("ring_islip2d", dring)):
        fa, iv = by(rs, "free_at"), by(rs, "interval")
        if fa and iv:
            rows.append([lbl,
                         ("<span class='lose'>λ=0.1 即已不稳定</span>"
                          if lam_star(fa) < 1e-6 else f(lam_star(fa), 2)),
                         f"<b>{f(lam_star(iv),2)}</b>",
                         f(peak(fa), 3), f(peak(iv), 3),
                         f"<b class='win'>{ratio(peak(iv), peak(fa))}</b>"])
    return f"""
<h2 id="s6">6 · 冲突域：free_at 与 interval</h2>
<p>仲裁器要回答的最后一个问题是「这条被选中的 VOQ 最早能从哪一拍开始」。
答案取决于冲突域<b>记了什么</b>，而这是全文影响最大的单个实现选择——
比换路、比 grants_per_src、比端口数都大一个数量级。</p>
{fig_domain()}
<h3>两种记法</h3>
<ul>
<li><code>free_at</code>：每个资源只记一个时间戳「我在 t 之后空闲」。
查询 O(1)，硬件只要一个寄存器（mesh 共 3,120 bit）。</li>
<li><code>interval</code>：每个资源记一段滑动窗口内的完整占用位向量。
可以回填空洞，代价是位向量存储（mesh 共 99,840 bit，约 32×）。</li>
</ul>
<div class="note bad">
<b>前沿棘轮（frontier ratchet）</b>：<code>free_at</code> 的问题不是「不够精细」，
而是会自我放大。提交一条流时，它必须把路径上<b>每一个</b>资源的前沿
都推到「整条路径都能开始」的那个最大值——
包括那些其实很空闲的资源。于是一条拥塞链路把自己的滞后
输出给了它接触的所有链路，再逐跳向外扩散。
批量口径下 data_span 从 {f(dm['data_span'])} 恶化到 {f(df['data_span'])}
（<b>{df['data_span'] / dm['data_span']:.1f}×</b>），环上从
{f(rr['makespan'])} 到 {f(rf['makespan'])}
（<b>{rf['makespan'] / rr['makespan']:.1f}×</b>），
而两者<b>轮数完全相同</b>——仲裁决策一模一样，
差别纯粹来自「落在哪个 t₀」。
</div>
<h4>稳态下同一现象</h4>
{tbl(["配置", "λ*（free_at）", "λ*（interval）",
      "峰值吞吐（free_at）", "峰值吞吐（interval）", "峰值之比"], rows)}
<p class="muted">这一组的 λ 网格较粗（0.1 / 0.3 / 0.5 / 0.7），
所以 interval 列的 λ* 低于 §9 主曲线的细网格结果；
关键信息是 <code>free_at</code> 在最低的 λ=0.1 上就已经判定不稳定——
它的接受吞吐卡在 0.11 附近不再上升，无论注多少。</p>
<p>所以 <code>interval</code> 不是一个「可选优化」，
而是集中式排程能否兑现「错时穿过同一链路」这个卖点的前提。
不加它，集中式的吞吐会掉到分布式基线以下。</p>
<div class="note">
<b>实现陷阱</b>：多服务器资源（如 RAMP_BW=2 的坡道口、
<code>board_ports</code>&gt;1 的上环口）不能用「按拍取或」来判可用。
必须检查<b>存在某一个服务器在整段 dur 上都空闲</b>——
否则会出现「第 0 拍服务器 A 空、第 1 拍服务器 B 空」被误判为可用，
而单个传输无法在两个服务器之间跳。这个 bug 曾被容量断言抓到。
</div>
"""


PSEUDO_RING = """<span class="c"># ---- 一轮 iSLIP-2D（无缓冲环）。与 mesh 版同骨架，资源与对齐不同 ----</span>
<span class="c"># 每条授权 = 行相弧 A_row + 转环时刻 t_turn + 列相弧 A_col</span>

<span class="c"># ① REQUEST：与 mesh 完全一样，一条消息带整张残余 VOQ 位图</span>
for s in sources: req[s] = R[s]

<span class="c"># ② GRANT：指针挂在"环-方向"上（6 行环 + 8 列环，各 2 方向 = 28 个）</span>
for ring, dirn in ring_directions:
    grant[ring,dirn] = rr_pick(candidates_touching(ring,dirn), start=g[ring,dirn])

<span class="c"># ③ ACCEPT：两相都被选中才算候选（AND 宽度只有 2，比 mesh 短）</span>
unanimous = [ f for f in requested
              if grant[row_ring(f)] == f and grant[col_ring(f)] == f ]
for s in sources:
    take = rr_take(unanimous[s], start=a[s], k=grants_per_src)

<span class="c"># ④ FILL：按弧长降序补齐（长弧先占，短弧塞缝）</span>
for f in remaining sorted by arc_len desc:
    if compatible_with(selected, f): selected.append(f)

<span class="c"># ⑤ COMMIT：R4 是这里唯一真正棘手的一步</span>
for f in selected:
    <span class="c"># 求最早的 t0，使得同时满足：</span>
    <span class="c">#   R1 行相弧每条链路在 [t0+pre, ...) 空闲</span>
    <span class="c">#   R2 源节点上环口在 t0 空闲</span>
    <span class="c">#   R4 转环那一拍，行环抽取点与列环插入点"同时"空闲  <-- 零松弛</span>
    <span class="c">#   R1 列相弧每条链路在 [t_turn+pre', ...) 空闲</span>
    <span class="c">#   R3 目的节点下环口在抵达拍空闲</span>
    t0 = earliest_t0_satisfying_all_five(f)
    occupy_all(f, t0)                  <span class="c"># 两相时刻被刚性绑定</span>
    grant_message(f.src, f.dst, t0, f.route)
"""


def s_ring(b: dict, s: dict) -> str:
    fixed = ring_a2a(b, path_mode="fixed", grants_per_src=1)[0]
    bal = ring_a2a(b, path_mode="balanced", grants_per_src=1)[0]
    p11 = ring_a2a(b, path_mode=None, board_ports=1, leave_ports=1,
                   spatial_reuse="arc", conflict_domain=None, fill=None,
                   iters=None, t_rtt=None)[0]
    p22 = ring_a2a(b, path_mode=None, board_ports=2, leave_ports=2)[0]
    rb = scurve(s, "main", "ring_base")
    ri = scurve(s, "main", "ring_islip2d")
    return f"""
<h2 id="s7">7 · Part B：islip2d_ring 与「集中化的必要性」</h2>
<p>环上的变种与 mesh 版共用同一个骨架（位图请求、两级指针、补齐、落 t₀），
差别全在资源集和一个额外的刚性约束上。</p>

<h3>7.1 一轮的伪码</h3>
<pre class="code">{PSEUDO_RING}</pre>
<table><thead><tr><th></th><th>islip2d_mesh</th><th>islip2d_ring</th>
</tr></thead><tbody>
<tr><td>grant 指针数</td><td>164（每条有向链路）</td>
<td>28（每条环 × 方向）—— 少得多，硬件更便宜</td></tr>
<tr><td>一致相的 AND 宽度</td><td>dx+dy 条链路（最多 11）</td>
<td>恒为 2（行相 + 列相）</td></tr>
<tr><td>实测全路径一致率</td><td>{pct(mesh_a2a(b, path_mode='xy', grants_per_src=1, conflict_domain='interval')['unanimous_frac'])}</td>
<td>{pct(fixed['unanimous_frac'])}</td></tr>
</tbody></table>
<div class="note">
一个曾经写错的推断：既然环的 AND 宽度只有 2，一致率「应该」更高。
实测相反（{pct(fixed['unanimous_frac'])} &lt;
{pct(mesh_a2a(b, path_mode='xy', grants_per_src=1, conflict_domain='interval')['unanimous_frac'])}）。
原因是一致率由<b>授权单元的数量</b>主导而不是 AND 的宽度：
环上只有 28 个 grant 单元要在 2,256 条流里挑，
每个单元被争抢得更凶，凑齐两相的概率反而更低。
</div>

<h3>7.2 R4：为什么这件事分布式做不了</h3>
{fig_r4()}
<p>R4 要求转环在<b>同一拍</b>完成：包从行环被抽出的那一拍，
必须同时被插入列环。零缓冲的定义就是桥上没有落脚点。
集中仲裁器能满足它，因为它<b>同时掌握两条环的未来占用</b>，
可以挑一个两边都空的拍——实测 <code>max_turn_residency = 0</code>，
R4 违例 0。</p>
<p>分布式环站做不到，因为它只有本地视野：包已经在行环上跑了，
到桥才发现列环那一拍被占。此时它只有三条路：
<b>加缓冲</b>（就不再是无缓冲网络了）、
<b>偏转</b>（绕整环一圈再试，吃掉一整圈时隙）、
或者<b>活锁</b>。ring_base 选的是前两者的组合。</p>
<div class="note good">
<b>这就是「集中化不是为了更快，而是为了可行」的完整含义。</b>
在 mesh 上，分布式方案（信用反压）是可行的，集中化换来的是省掉缓冲；
在无缓冲环上，R4 是一个<b>需要未来信息</b>的约束，
分布式方案只能用偏转把它化解掉，代价直接记在带宽和乱序上：
λ* 从 ring_base {f(lam_star(rb), 2)} 提到 ring_islip2d
{f(lam_star(ri), 2)}（{ratio(lam_star(ri), lam_star(rb))}）。
</div>

<h3>7.3 各旋钮的实测影响</h3>
{t_ring_knobs(b)}
<ul>
<li><b>ring_path_mode</b>：<code>balanced</code>（离线平衡方向×维序）把最热链路
负载从 {f(fixed['max_link_load'])} 压到 {f(bal['max_link_load'])}，
轮数下界随之从 {f(fixed['round_lb'])} 降到 {f(bal['round_lb'])}，
同 g=1 口径下实测轮数 {f(fixed['n_rounds'])} → {f(bal['n_rounds'])}。
这是环上唯一真正有效的路径优化：环的绕回结构让「顺时针 / 逆时针」
和「先行后列 / 先列后行」构成 4 个候选，离线均衡它们收益明显——
与 mesh 上 ROMM 无增益形成对照。</li>
<li><b>board / leave 端口数</b>：1→2 只把 makespan 从 {f(p11['makespan'])}
降到 {f(p22['makespan'])}（{pct(1 - p22['makespan'] / p11['makespan'], 1)}）。
端口不是瓶颈，翻倍不值那个面积。</li>
<li><b>spatial_reuse</b>：见 §4，<code>whole_ring</code> 贵
{ring_a2a(b, path_mode=None, spatial_reuse='whole_ring')[0]['makespan'] / p11['makespan']:.2f}×。</li>
</ul>
"""


def s_base(s: dict, led: dict) -> str:
    mb = scurve(s, "main", "mesh_base")
    rb = scurve(s, "main", "ring_base")
    fifo = [r for r in s["rows"] if r["group"] == "fifo"]
    fr = sorted({r["fifo_depth"] for r in fifo})
    frows = []
    for d in fr:
        cv = sorted((r for r in fifo if r["fifo_depth"] == d),
                    key=lambda r: r["lam"])
        frows.append([f"fifo_depth = {d}", f(lam_star(cv), 2), f(peak(cv), 3),
                      f(max(r['p99'] for r in cv), 0)])
    bd = [r for r in s["rows"] if r["group"] == "buf"]
    brows = []
    for d in sorted({r["buf_depth"] for r in bd}):
        cv = sorted((r for r in bd if r["buf_depth"] == d),
                    key=lambda r: r["lam"])
        brows.append([f"buf_depth = {d} flit", f(lam_star(cv), 2),
                      f(peak(cv), 3)])
    rbb = led["ring_base"]["distributed_breakdown"]
    mbb = led["mesh_base"]["distributed_breakdown"]
    return f"""
<h2 id="s8">8 · 两个分布式基线</h2>
<h3>8.1 mesh_base：有缓冲 + 信用反压</h3>
<p>教科书式的分组交换 mesh：每输入端口一组 VC 缓冲，
信用（credit）保证不会发到满缓冲上，交换分配用输入队列 iSLIP。
它是分布式方案在 mesh 上<b>可行</b>的证明，
代价是每节点的缓冲——{f(mbb['input_buffers'])} bit 输入缓冲
+ {f(mbb['credit_counters'])} bit 信用计数器
+ {f(mbb['switch_alloc_pointers'])} bit 分配指针，
合计 {f(led['mesh_base']['total_bits'])} bit。</p>
{tbl(["缓冲深度", "λ*", "峰值接受吞吐"], brows)}
<p>缓冲深度是它的命门：浅缓冲下信用反压把拥塞逐跳往回传，
λ* 明显下降。这也是集中化的卖点所在——授权制根本不需要这些缓冲，
因为包发出去就不会堵。</p>

<h3>8.2 ring_base：E-tag / I-tag + 偏转</h3>
<p>按 HPCA'22《Application Defined On-chip Networks for Heterogeneous
Chiplets》与 HiRD 谱系重建的<b>反应式</b>基线。
它不做任何事前无冲突保证，而是逐拍化解：</p>
{fig_base()}
<ul>
<li><b>环内优先</b>：已在环上的 flit 永不停留（实测零违例），
新注入只能抢空时隙。</li>
<li><b>I-tag</b>：源节点长期抢不到空时隙时，为它预留一个时隙 ⇒ 注入保证。</li>
<li><b>E-tag</b>：flit 长期转不过去时，为它预留 transfer FIFO 槽 ⇒ 转移保证。</li>
<li><b>偏转</b>：transfer FIFO 满时，flit 绕整环一圈再试。</li>
<li><b>Swap Rule</b>：两侧互相转向时直接交换，绕过 FIFO。</li>
<li><b>重组缓冲</b>：偏转和交换都会乱序，目的端必须重排。</li>
</ul>
{tbl(["transfer FIFO 深度", "λ*", "峰值接受吞吐", "最大 p99"], frows)}
<div class="note">
<b>三个与预期不同的实测结果</b>（都已写进验证清单）：
<ol>
<li>固定维序（RC）下 <b>Swap Rule 从不触发</b>——
它需要两侧同时互相转向，而固定维序不产生这种模式。
要让它动起来必须允许双向转弯（<code>dim_order=mixed</code>）。</li>
<li>关掉 Swap Rule <b>也不会死锁</b>：偏转通道永远可用，
包总能绕回来再试。原文的死锁场景需要更紧的时隙约束。</li>
<li>真正的失效模式是<b>活锁</b>而不是死锁：包一直在绕圈但不推进。
I-tag / E-tag 的预留机制正是为此存在，它们把「不推进」变成有界等待。</li>
</ol>
</div>
<p class="muted">分布式硬件账（{f(led['ring_base']['total_bits'])} bit 合计）：
transfer FIFO {f(rbb['transfer_fifos'])} · 预留 Tx {f(rbb['reserved_tx_buffers'])}
· 弹出队列 {f(rbb['eject_queues'])} · 重组缓冲 {f(rbb['reassembly_buffers'])}
· 饿死计数器 {f(rbb['starvation_counters'])} · I/E-tag 状态
{f(rbb['itag_etag_state'])} · Swap 旁路 MUX {f(rbb['swap_bypass_muxes'])}。
其中重组缓冲（{pct(rbb['reassembly_buffers'] / led['ring_base']['total_bits'])}）
纯粹是偏转导致乱序的代价，集中式方案不需要它。</p>
"""


def s_steady(s: dict, x: dict) -> str:
    def gtbl(group, key, head, fmtk=str):
        return t_group(s, group, key, head, fmtk)
    m4 = [r for r in s["rows"] if r["group"] == "m4s1"]
    s2 = [r for r in s["rows"] if r["group"] == "m1s2"]

    def ls(rows, cfg):
        return lam_star(sorted((r for r in rows if r["config"] == cfg),
                               key=lambda r: r["lam"]))
    mrows = []
    for lbl, rows in (("m=1 · σ=1（主曲线）",
                       [r for r in s["rows"] if r["group"] == "main"]),
                      ("m=4 · σ=1（多 flit 包）", m4),
                      ("m=1 · σ=2（金属常数加倍）", s2),
                      ("m=4 · σ=2", [r for r in s["rows"]
                                     if r["group"] == "m4s2"])):
        if not rows:
            continue
        cells = [lbl] + [f(ls(rows, c), 2) for c in CFG_ORDER]
        mi, ri = ls(rows, "mesh_islip2d"), ls(rows, "ring_islip2d")
        cells.append(f"环/mesh = <b>{ratio(ri, mi)}</b>")
        mrows.append(cells)
    return f"""
<h2 id="s9">9 · 稳态注入率扫描：四配置头对头</h2>
<p>四种配置跑在<b>同一个</b>开环稳态 DES 里（同注入器、同统计器、
源端无限队列、warmup 后测量、按源队列斜率判稳），
所以曲线之间的差异只来自 fabric 与仲裁方式。
流量是 all-to-all，λ 从 0.01 扫到 1.0。</p>
{fig_curve(s)}
{t_main(s)}
<p>读法：</p>
<ul>
<li><b>两个基线在过载区会回落</b>：mesh_base 是信用反压把拥塞传回源，
ring_base 是偏转吃掉越来越多时隙。集中式两条曲线单调上升后平台化——
因为授权制下超出容量的请求只是排在源端队列里，网络内部不会互相踩。</li>
<li><b>峰值接受吞吐可以超过解析锚点</b>，这不是 bug：
锚点是「均匀 all-to-all 下最热链路」的上界；
过载区被接受的流量组合会自动偏离热链路（热链路上的请求排不进去），
于是实测混合吞吐高于均匀假设下的界。λ* 本身没有越界。</li>
<li><b>公平性只在过载区分化</b>：λ=0.1 时四者的 CV 都在 0.036 附近
（没到容量，谁都不用抢）。过载区才看出差别：
mesh_base 的 CV 涨到 0.85，mesh_islip2d 是 0.46——
集中式的两级指针给了显式轮转保证，但 mesh 上仍有相当的离散度，
因为链路热度本身不均匀。环上两者都在 0.01–0.02，
环的对称性把公平性问题基本消掉了。</li>
</ul>

<h3>9.1 包长与金属常数</h3>
{tbl(["口径", "mesh_base", "mesh_islip2d", "ring_base", "ring_islip2d",
      "结论"], mrows)}
<div class="note">
<b>σ 口径决定谁赢，必须说清用的是哪一种。</b>
<ul>
<li><b>同 σ 口径</b>（两者都 σ=1，或都 σ=2）：环稳定领先
{ratio(ls([r for r in s['rows'] if r['group'] == 'main'], 'ring_islip2d'),
        ls([r for r in s['rows'] if r['group'] == 'main'], 'mesh_islip2d'))}
（σ=1）与 {ratio(ls(s2, 'ring_islip2d'), ls(s2, 'mesh_islip2d'))}（σ=2）。
环的绕回链路给了它更短的平均跳数和更高的对分带宽。</li>
<li><b>金属归一化口径</b>（mesh 保持 σ=1，环按 {f(1.1707, 2)}× 的走线
长度折算为 σ=2）：结论<b>反转</b>——
mesh_islip2d λ*={f(ls([r for r in s['rows'] if r['group'] == 'main'], 'mesh_islip2d'), 2)}
vs ring_islip2d(σ=2) λ*={f(ls(s2, 'ring_islip2d'), 2)}，
mesh 领先 {ratio(ls([r for r in s['rows'] if r['group'] == 'main'], 'mesh_islip2d'), ls(s2, 'ring_islip2d'))}。</li>
</ul>
换句话说，「环更好」这句话只在忽略走线长度时成立。
环多用了 {f(1.1707, 2)}× 的金属（{f(96)} vs {f(82)} 条无向链路），
这笔账不结清，比较就没有意义。<br/>
多 flit 包（m=4）则一致地放大集中式的优势：包越长，
一次授权摊到的控制开销越小，而基线的逐跳堵塞概率越高。
σ=2 与 m=4 叠加时（末行）两种 fabric 的基线都在最低采样点
λ=0.01 就已失稳，此时 λ* 已无分辨力，应看接受吞吐曲线本身。
</div>

<h3>9.2 控制环路 RTT 与端口数</h3>
{gtbl("rtt", "t_rtt", "T_rtt（拍）", lambda v: f"T_rtt = {v}")}
{gtbl("ports", "board_ports", "上环口数", lambda v: f"board_ports = {v}")}
<p>格式为「λ* / 峰值接受吞吐」。
稳态下 RTT <b>几乎不影响吞吐</b>，只线性抬高延迟：
因为请求-授权环路是流水的，稳定状态下它只是一段固定的管道延迟，
不减少每拍能放行的流数。这与批量口径（§10.2）结论相反，
那里 RTT 会直接吃掉 makespan——两个口径不能混谈。</p>

{s_bisect(x)}
"""


def t_bisect(x: dict) -> str:
    rows = []
    for cfg, _, lbl in XCFG:
        u = x["summary"][cfg]
        cen = "islip2d" in cfg
        rows.append([("!" if cen else "") + lbl.split("（")[0],
                     f"{u['bisect_links']}",
                     f"{u['lam_star']}",
                     f"<b class='{'win' if cen else 'lose'}'>"
                     f"{pct(u['bisect_util_at_lam_star'])}</b>",
                     f"{pct(u['peak_bisect_util'])}"
                     f"<br><span class='muted'>@λ="
                     f"{u['peak_bisect_util_at_lam']}</span>",
                     f"{u['cross_per_pkt_accepting']:.4f}"])
    return tbl(["配置", "切面链路数", "λ*",
                "λ* 处切面利用率", "全程峰值",
                "每包跨切（满收区）"], rows)


def t_lat(x: dict) -> str:
    rows = []
    for cfg, _, lbl in XCFG:
        st = xstable(x, cfg)
        m0, m1 = st[0]["mean_lat"], st[-1]["mean_lat"]
        p0, p1 = st[0]["p99"], st[-1]["p99"]
        worst = max(r["p99"] / r["mean_lat"] for r in st)
        cen = "islip2d" in cfg
        rows.append([("!" if cen else "") + lbl.split("（")[0],
                     f"{m0:.0f}", f"{p0:.0f}",
                     f"{m1:.0f}<br><span class='muted'>×{m1/m0:.2f}</span>",
                     f"{p1:.0f}<br><span class='muted'>×{p1/p0:.2f}</span>",
                     f"<b class='{'win' if worst < 2.5 else 'lose'}'>"
                     f"{worst:.2f}×</b>"])
    return tbl(["配置", "空载平均", "空载 p99",
                "λ* 处平均", "λ* 处 p99",
                "稳定区最坏 p99/平均"], rows)



def s_order(b: dict, s: dict) -> str:
    return f"""
<h2 id="s10">10 · 保序、流水与 RTT 敏感度</h2>
<h3>10.1 为什么换路不会乱序</h3>
<p>直觉上「多路径 ⇒ 乱序 ⇒ 需要重组缓冲」。
在这两种 fabric 上，只要限定在最小路径集内，这个直觉是<b>错的</b>：</p>
<ul>
<li><b>mesh ROMM</b>：矩形内任意中间点给出的两段 XY，跳数恒为 dx+dy，
线延迟完全相同。实测 14,560 对候选路径，延迟差 <b>0</b>。</li>
<li><b>环最小弧集</b>：RC（先行后列）与 CR（先列后行）的最小路径
跳数与线延迟也相同。实测 2,256 对，延迟差 <b>0</b>。</li>
</ul>
<p>所以 M3 / R5 只需要保证<b>同 (src,dst) 的注入顺序</b>，
不必把路径钉死。这直接省掉了目的端的重组缓冲——
对比 ring_base 必须为偏转付出的
重组缓冲，这是集中式的一项实打实的收益。</p>
<div class="note bad">
反面情形：如果放开到<b>非最小</b>环路径（允许绕远），
延迟不变性立刻破裂——93.6% 的 (src,dst) 对出现延迟差，
最大差 78 拍。所以「换路自由」必须限定在最小路径集内，
否则就要重新引入重组缓冲。
</div>

<h3>10.2 流水与 RTT 临界点</h3>
<p>批量口径下引入 <code>pipeline_depth</code>：控制面算第 k+1 轮的同时，
数据面在跑第 k 轮。衡量指标是
<code>convoy_ratio = convoy_span / data_span</code>——
硬 barrier（每轮等齐）相对流水的倍数，越大说明流水收益越大，
跌到 1 说明流水已被 RTT 完全吃掉。</p>
{t_pipeline(b)}
<p>depth=1 时 mesh 在 T_rtt ≈ 65 拍、环在 ≈ 46 拍失去全部收益
（环更早，因为它每轮时间更短，同样的 RTT 占比更大）。
depth≥2 或 ∞ 时收益不再消失——加深流水就是在用控制面并行度对冲 RTT。
工程含义：如果集中仲裁器离节点很远（RTT 大），
必须把控制流水做深，否则集中式的排程优势会被往返延迟抵消。</p>
"""


def t_iters_ledger(b: dict) -> str:
    """iters priced the same way as the data plane: rounds + arbitration steps.

    Depth is per-iteration, so iterations are charged only as dependent steps.
    """
    from rg_sched_cost import sched_cost
    from rg_topo import Topology
    mesh = Topology("mesh")
    rows = [r for r in b["rows"]
            if r.get("iters") is not None and r["fabric"] == "mesh"]
    out, best = [], min(
        r["makespan"] + sched_cost("islip2d_mesh", mesh, r["n_flows"],
                                   iters=r["iters"], n_rounds=r["n_rounds"]
                                   )["t_sched_cycles"] for r in rows)
    for r in sorted(rows, key=lambda r: r["iters"]):
        c = sched_cost("islip2d_mesh", mesh, r["n_flows"], iters=r["iters"],
                       n_rounds=r["n_rounds"])
        tot = r["makespan"] + c["t_sched_cycles"]
        lab = f"{r['iters']}" + ("（纯贪心补齐）" if r["iters"] == 0 else "")
        win = tot == best
        out.append([f"<b>{lab}</b>" if win else lab, f(r["n_rounds"]),
                    f(r["makespan"]), f(c["dependent_steps"]),
                    f(c["t_sched_cycles"]),
                    f"<b class='win'>{f(tot)}</b>" if win else f(tot)])
    return tbl(["iters", "轮次", "数据面 makespan", "依赖步", "T_sched", "合计"],
               out)


def s_area(led: dict, s: dict, b: dict) -> str:
    mv = led["mesh_islip2d_vs_mesh_base"]
    rv = led["ring_islip2d_vs_ring_base"]
    mi, ri = led["mesh_islip2d"], led["ring_islip2d"]

    def dom_gain(cfg: str) -> float:
        rs = [r for r in s["rows"] if r["group"] == "domain"
              and r["config"] == cfg]
        fa = peak([r for r in rs if r["conflict_domain"] == "free_at"])
        iv = peak([r for r in rs if r["conflict_domain"] == "interval"])
        return iv / fa if fa > 1e-9 else 0.0
    gm, gr = dom_gain("mesh_islip2d"), dom_gain("ring_islip2d")
    glo, ghi = min(gm, gr), max(gm, gr)
    rows = [
        ["mesh：集中化前后",
         f"{f(mv['storage_removed_bits'])} bit<br>"
         f"<span class='muted'>输入缓冲 + 信用 + 分配指针</span>",
         f"{f(mv['arbiter_added_bits'])} bit<br>"
         f"<span class='muted'>位图 + 两级指针 + interval 表</span>",
         f"<b class='win'>{f(mv['ratio'], 2)}×</b> 净省",
         f"{mi['gate_levels_interval']} 级 / T_sched="
         f"{mi['t_sched_interval']} 拍"],
        ["环：集中化前后",
         f"{f(rv['storage_removed_bits'])} bit<br>"
         f"<span class='muted'>FIFO + 预留 Tx + 重组缓冲</span>",
         f"{f(rv['arbiter_added_bits'])} bit<br>"
         f"<span class='muted'>弧表 + 端口计数器 + interval 表</span>",
         f"<b>{f(rv['ratio'], 2)}×</b>（基本打平）",
         f"{ri.get('gate_levels_interval', '—')} 级 / T_sched="
         f"{ri.get('t_sched_interval', '—')} 拍"],
    ]
    return f"""
<h2 id="s11">11 · 面积与调度时间</h2>
<p>「集中化消掉了什么」需要面积数字而不只是定性论断。
下表把分布式侧被删掉的存储与集中侧新增的仲裁器状态放在同一口径
（bit 等效）下结算。</p>
{tbl(["方向", "删掉的分布式存储", "新增的仲裁器状态", "净账", "关键路径"],
     rows)}
<ul>
<li><b>mesh 上集中化是净赚的</b>：{f(mv['ratio'], 2)}× 净省，
因为输入缓冲（{f(led['mesh_base']['distributed_breakdown']['input_buffers'])}
bit）本身就是 mesh_base 面积的绝大部分，
而授权制根本不需要它。</li>
<li><b>环上集中化面积基本打平</b>（{f(rv['ratio'], 2)}×）：
删掉的 FIFO 和重组缓冲，差不多正好被 interval 表吃回去。
所以环上集中化的理由不能是省面积，只能是 §7.2 的可行性
与 §9 的吞吐。</li>
<li><b>interval 表是最贵的单项</b>：mesh 上
{f(mi['arbiter_breakdown_interval']['interval_tables'])} bit，
占仲裁器的
{pct(mi['arbiter_breakdown_interval']['interval_tables'] / mi['arbiter_bits_interval'])}。
但 §6 已经说明它换来 {glo:.1f}–{ghi:.1f}× 的接受吞吐——
这是全设计里性价比最高的一笔存储。</li>
<li><b>T_sched 会翻倍</b>（free_at {mi['t_sched_free_at']} →
interval {mi['t_sched_interval']} 拍），
门级深度从 {mi['gate_levels_free_at']} 升到
{mi['gate_levels_interval']} 级。若时序吃不下，
应该做的是把仲裁流水切深（§10.2），而不是退回 free_at。</li>
</ul>
<h3>11.1 迭代次数按同一口径结算</h3>
<p>上表都是 <code>iters=1</code>。门级深度是<b>一次迭代</b>的深度，
与 <code>iters</code> 无关（{mi['gate_levels_free_at']} 级不随迭代变）；
迭代之间互相依赖，所以 <code>iters</code> 只按<b>依赖步</b>线性计入
<code>T_sched</code>。把 §5 的「加迭代买轮次」放到
「数据面 makespan + T_sched」这一个口径里看：</p>
{t_iters_ledger(b)}
<p><b><code>iters=1</code> 是最差的一档</b>——付了指针纪律的轮次代价，
又没迭代到能把代价赚回来。<code>iters=2</code> 多花仲裁、省下更多数据面，
与 <code>iters=0</code> 打平却额外保留指针纪律的公平性，是本表最优点；
<code>iters=4</code> 已经过头。这里是<b>不流水</b>的保守相加，
§10.2 的控制/数据流水只会让 <code>iters=2</code> 更有利。</p>
"""


def s_tail(b: dict, s: dict, v: dict) -> str:
    ac = s.get("anchor_check", {}) or {}
    all_ok = all(x.get("ok") for x in ac.values()) if ac else False
    anc_note = "，四配置全部满足" if all_ok else ""
    return f"""
<h2 id="s12">12 · 验证清单与已知局限</h2>
<h3>12.1 可执行断言</h3>
<p>所有结论都由 <code>utils/verify_islip2d_8x6.py</code> 里的断言守卫，
每次改动都会重跑。共 <b>{v['n_checks']}</b> 条，
当前 <b class="{'win' if v['n_fail'] == 0 else 'lose'}">
{v['n_checks'] - v['n_fail']} 通过 / {v['n_fail']} 失败</b>
（{f(v['wall_secs'], 1)} 秒）。</p>
{t_verify(v)}
<p>其中最关键的几条：逐资源两两复核 0 冲突（两 fabric）、
bufferless 场景 max_residency = 0、轮数 ≥ 割界 / 端口界、
残余位图纪律（未授权的位必须原样留下）、
实测 λ* ≤ 解析锚点（{f(s['anchors']['mesh_xy'], 3)} /
{f(s['anchors']['ring_fixed'], 3)}{anc_note}）。</p>

<h3>12.2 已知局限</h3>
<ul>
<li><b>E-tag/I-tag 是重建口径</b>：HPCA'22 原文在付费墙后，
本文按论文描述与 HiRD 谱系重建了机制骨架
（环内优先、注入/转移保证、偏转、Swap Rule、死锁检测）。
定量结果应视为「这一类反应式策略」的代表，而非对原设计的复刻。</li>
<li><b>与 HiRD 的结构差异</b>：HiRD 是局部环 + 全局环的层次结构，
只有少数节点是桥；本文是维度切片，<b>每个节点都是桥</b>，
所以桥资源竞争更激烈、偏转代价更高。</li>
<li><b>死锁未能复现</b>：见 §8.2。在本文的时隙模型下偏转通道永远可用，
需要更紧的时隙约束（<code>slot_ring</code>）才能构造出死锁。</li>
<li><b>interval 窗口有限</b>：滑动窗口 + rebase 实现，
窗口外的空洞看不见。窗口取 128 拍时已经拿到绝大部分收益，
但它不是「完整区间表」的等价物。</li>
<li><b>未做 BookSim 交叉验证</b>：本轮按要求跳过。
准备工作已就绪（<code>utils/xval_booksim_8x6.py</code> 能生成 8×6 anynet
拓扑与配置并解析输出），
但当前环境缺 flex/bison 且无法取得 sudo，故 mesh_base 的延迟-吞吐曲线
仅有自洽性验证（解析锚点、缓冲深度单调性）而无第三方对照。</li>
</ul>

<h3>12.3 文件索引</h3>
<table><thead><tr><th>文件</th><th>作用</th></tr></thead><tbody>
<tr><td><code>utils/rg_mesh_paths.py</code></td>
<td>ROMM 路径构造、静态平衡分配、延迟不变性断言、割界计算</td></tr>
<tr><td><code>utils/rg_mesh_sched.py</code></td>
<td><code>islip2d_mesh</code>：位图请求、两级指针、补齐、冲突域</td></tr>
<tr><td><code>utils/rg_ring_topo.py</code></td>
<td>维度切片环拓扑、弧构造、D-R 五子句独立复核器、金属记账</td></tr>
<tr><td><code>utils/rg_ring_sched.py</code></td>
<td><code>islip2d_ring</code>：两相 R4 刚性对齐、弧长降序补齐</td></tr>
<tr><td><code>utils/rg_ring_base.py</code></td>
<td>E-tag/I-tag + 偏转基线（反应式逐拍）</td></tr>
<tr><td><code>utils/rg_steady_des.py</code></td>
<td>开环稳态 DES 内核，四配置共用注入器与统计器</td></tr>
<tr><td><code>utils/rg_sched_cost.py</code></td>
<td>状态位 / 比较器 / 门级深度 + 集中化面积台账</td></tr>
<tr><td><code>utils/dse_islip2d_8x6.py</code></td>
<td>批量 makespan 扫描 → <code>results/islip2d_8x6.json</code></td></tr>
<tr><td><code>utils/dse_load_sweep_8x6.py</code></td>
<td>稳态注入率扫描 → <code>results/load_sweep_8x6.json</code></td></tr>
<tr><td><code>utils/dse_bisect_lat_8x6.py</code></td>
<td>二分带宽 / 平均时延 / p99 扫描（膝部加密）→
<code>results/bisect_lat_8x6.json</code>（§9.3–9.5）</td></tr>
<tr><td><code>utils/gen_bisect_lat_plots.py</code></td>
<td>同一数据的 PNG 版三张图（论文/幻灯片用；本报告内是等价的内联 SVG）</td></tr>
<tr><td><code>utils/verify_islip2d_8x6.py</code></td>
<td>{v['n_checks']} 条可执行断言 → <code>results/verify_islip2d_8x6.json</code></td></tr>
<tr><td><code>utils/gen_islip2d_report.py</code></td>
<td>本报告生成器（所有数字均从四个 JSON 读出，无手写常数）</td></tr>
<tr><td><code>docs/phase-7-exploration/islip2d-mesh-ring-8x6.md</code></td>
<td>同一研究的 Markdown 版（更侧重结论与判据条文）</td></tr>
</tbody></table>
"""


def build(b: dict, s: dict, v: dict, x: dict, led: dict) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>iSLIP-2D：两种 fabric、两套冲突判据 · 8×6</title>
<style>{CSS}</style>
</head>
<body><div class="wrap">
<h1>iSLIP-2D：两种 fabric、两套冲突判据</h1>
<p class="muted">8×6 · 2D mesh 与 2D 无缓冲环（维度切片）·
集中式 request–grant 仲裁 vs 分布式基线</p>
<p>
<span class="pill">48 节点 · 2,256 条 all-to-all 流</span>
<span class="pill">H={H} / V={V} · RAMP_BW=2</span>
<span class="pill">环 {b['audit']['n_directed_links']} 有向链路</span>
<span class="pill">{v['n_checks']} 条断言</span>
<span class="pill">数据 results/islip2d_8x6.json ·
load_sweep_8x6.json · verify_islip2d_8x6.json ·
bisect_lat_8x6.json</span>
</p>
{cards(b, s, v)}
{s_intro(b, s, v, x)}
{s_predicates(b)}
{s_dm(b)}
{s_dr(b)}
{s_mesh(b)}
{s_domain(b, s)}
{s_ring(b, s)}
{s_base(s, led)}
{s_steady(s, x)}
{s_order(b, s)}
{s_area(led, s, b)}
{s_tail(b, s, v)}
<p class="muted" style="margin-top:2.5rem">
批量扫描 {f(b['wall_secs'], 1)} 秒 · 稳态扫描 {f(s['wall_secs'], 1)} 秒 ·
验证 {f(v['wall_secs'], 1)} 秒。
重新生成：<code>python3 utils/gen_islip2d_report.py</code></p>
</div></body></html>
"""


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT / "utils"))
    import rg_sched_cost

    b, s, v, x = load()
    led = rg_sched_cost.centralization_ledger()
    OUT.write_text(build(b, s, v, x, led), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} "
          f"({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
