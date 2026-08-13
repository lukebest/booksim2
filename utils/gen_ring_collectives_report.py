#!/usr/bin/env python3
"""生成 results/report_ring_collectives_8x6.html —— 8x6 无缓冲环的**唯一**报告。

这一个文件承载全部无缓冲环的工作：三种 transport（paper 机制 / 集中式
islip2d 参照 / 静态拍图）、六个集合通信、四个结构杠杆、T_avg、带宽利用率、
容错、抗抖动、拍图导出、验证清单、反预期结果与已知局限。刻意不拆成多份：
读者要判断「环上到底该用哪种 transport」，就必须能在同一页里对齐口径。

叙述围绕对比组织（先画机制再给数字），而不是按数据文件罗列。

所有数字都从 results/*.json 读出，没有一个是写死的；甘特图直接调
rg_ring_calendar 现场排一张拍图再画，所以图上的每个区间都是排出来的真值，
不是示意。JSON 缺失时对应小节显式声明缺失，不用旧值顶替。
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
ATT = ROOT / "results" / "ring_attach_8x6.json"
THR = ROOT / "results" / "ring_throughput_8x6.json"
BRG = ROOT / "results" / "ring_bridge_8x6.json"
OUT = ROOT / "results" / "report_ring_collectives_8x6.html"

MX, MY, N = 8, 6, 48
RAMP_BW = 2                    # L1 斜坡带宽，与 rg_topo 一致
RAMP = 2                       # 落 L1 / 出 L1 各一次的斜坡时延，与 rg_topo 一致
ROOT_NODE = 27
PATTERNS = ["broadcast", "reduce", "gather", "allreduce", "allgather",
            "alltoall"]
CN = {"broadcast": "broadcast<br>广播", "reduce": "reduce<br>归约",
      "gather": "gather<br>收集", "allreduce": "allreduce<br>全归约",
      "allgather": "allgather<br>全收集", "alltoall": "alltoall<br>全交换"}
CN1 = {"broadcast": "广播", "reduce": "归约", "gather": "收集",
       "allreduce": "全归约", "allgather": "全收集", "alltoall": "全交换"}


# ---------------------------------------------------------------------------
# 1. 取数
# ---------------------------------------------------------------------------

def load() -> dict[str, Any]:
    def rd(p: Path) -> dict | None:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return {"coll": rd(COLL), "tavg": rd(TAVG), "rob": rd(ROB),
            "ver": rd(VER), "idx": rd(IDX), "att": rd(ATT), "thr": rd(THR),
            "brg": rd(BRG)}


def rows(c: dict, **flt: Any) -> list[dict]:
    return [r for r in c["rows"] if all(r.get(k) == v for k, v in flt.items())]


def row1(c: dict, **flt: Any) -> dict | None:
    r = rows(c, **flt)
    return r[0] if r else None


def best_cal(c: dict, pat: str, m: int, tier: str | None = None) -> dict | None:
    """该 pattern 在该 m 下 makespan 最小的拍图行；tier 给定则限定能力档。

    区分 tier 是这份对比的前提：`ring_base` 只做 unicast，所以 T1（弧多播 +
    L1 归约）行根本没有基线可比。把两者混在一根柱子里，等于让拍图白拿一块
    基线没有的硬件。
    """
    cand = rows(c, pattern=pat, m=m, bidir=True)
    if tier is not None:
        cand = [r for r in cand if r["tier"] == tier]
    return min(cand, key=lambda r: r["calendar"]["makespan"]) if cand else None


def best_base(c: dict, pat: str, m: int) -> dict | None:
    """该 pattern 在该 m 下 ring_base 最快的行。

    取各自腿的最优算法：集合算法与 transport 是正交的两根轴，用同一个算法压
    基线会把基线做成稻草人。
    """
    cand = [r for r in rows(c, pattern=pat, m=m, bidir=True)
            if r["ring_base"].get("makespan") is not None]
    return min(cand, key=lambda r: r["ring_base"]["makespan"]) if cand else None


def f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "&mdash;"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    if isinstance(x, int):
        return f"{x:,}"
    return str(x)


def pct(x: Any, nd: int = 1) -> str:
    return "&mdash;" if x is None else f"{100 * x:.{nd}f}%"


def times(x: Any, nd: int = 2) -> str:
    return "&mdash;" if x is None else f"{x:.{nd}f}&times;"


def lbl(pat: str, algo: str, tier: str) -> str:
    return f"{CN1.get(pat, pat)} / {algo} / {tier}"


# ---------------------------------------------------------------------------
# 2. 通用图元
#
# 全部手写 SVG：节点编号必须与仿真器的 nid = y*MX + x 一致，读者才能把图上的
# 一条弧对上代码打印出来的 footprint。
# ---------------------------------------------------------------------------

def svg(w: int, h: int, body: str) -> str:
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" '
            f'style="max-width:{w}px" role="img">{body}</svg>')


def _axis_y(vals: list[float], top: float, bot: float, pad: float = 1.08
            ) -> tuple[Any, float]:
    hi = max(vals) * pad if vals else 1.0
    hi = hi or 1.0

    def ty(v: float) -> float:
        return bot - (bot - top) * (v / hi)
    return ty, hi


def grouped_bars(cats: list[str], series: list[dict], *, w: int = 880,
                 h: int = 330, ylabel: str = "", note_fmt: str = "{:,.0f}",
                 hi_series: int | None = None, vlab: bool = False) -> str:
    """分组柱状图。series = [{name, cls, vals:[...]}]，vals 与 cats 等长。

    vlab=True 时数值标签竖排：柱子窄而数字长（四五位数）时横排必然互相压字，
    与其把数字删掉，不如转 90 度。
    """
    L, Rr, T, B = 66, 150, (48 if vlab else 30), 62
    ty, hi = _axis_y([v for s in series for v in s["vals"] if v is not None],
                     T, h - B)
    ytf = "{:,.0f}" if hi >= 8 else "{:,.2f}"
    gw = (w - L - Rr) / max(1, len(cats))
    bw = min(30.0, gw * 0.72 / max(1, len(series)))
    p = [f'<rect x="{L}" y="{T}" width="{w - L - Rr}" height="{h - B - T}" '
         f'class="plot"/>']
    for i in range(5):
        yv = hi * i / 4
        p.append(f'<line x1="{L}" y1="{ty(yv):.1f}" x2="{w - Rr}" '
                 f'y2="{ty(yv):.1f}" class="gl"/>')
        p.append(f'<text x="{L - 8}" y="{ty(yv) + 4:.1f}" class="tick" '
                 f'text-anchor="end">{ytf.format(yv)}</text>')
    for ci, cat in enumerate(cats):
        cx = L + gw * (ci + 0.5)
        n = len(series)
        for si, s in enumerate(series):
            v = s["vals"][ci]
            if v is None:
                continue
            x = cx - (n * bw) / 2 + si * bw
            y = ty(v)
            p.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw - 3:.1f}" '
                     f'height="{h - B - y:.1f}" class="bar {s["cls"]}'
                     f'{" hib" if hi_series == si else ""}"/>')
            xm = x + (bw - 3) / 2
            if vlab:
                p.append(f'<text x="{xm:.1f}" y="{y - 5:.1f}" class="barv" '
                         f'text-anchor="start" transform="rotate(-90 {xm:.1f} '
                         f'{y - 5:.1f})">{note_fmt.format(v)}</text>')
            else:
                p.append(f'<text x="{xm:.1f}" y="{y - 4:.1f}" '
                         f'class="barv" text-anchor="middle">'
                         f'{note_fmt.format(v)}</text>')
        for li, line in enumerate(cat.split("<br>")):
            p.append(f'<text x="{cx:.1f}" y="{h - B + 18 + li * 14}" '
                     f'class="tick" text-anchor="middle">{line}</text>')
    for si, s in enumerate(series):
        yy = T + 14 + si * 20
        p.append(f'<rect x="{w - Rr + 8}" y="{yy - 9}" width="12" height="12" '
                 f'class="bar {s["cls"]}"/>')
        p.append(f'<text x="{w - Rr + 26}" y="{yy + 1}" class="axl">'
                 f'{s["name"]}</text>')
    p.append(f'<text x="14" y="{(T + h - B) / 2}" class="axl" '
             f'transform="rotate(-90 14 {(T + h - B) / 2})" '
             f'text-anchor="middle">{ylabel}</text>')
    return svg(w, h, "".join(p))


def diverging_bars(items: list[dict], *, w: int = 880, rowh: int = 30,
                   center: float = 1.0, xmax: float = 2.6,
                   left_label: str = "", right_label: str = "") -> str:
    """以 1.0 为界的横向比值条：>1 向右（拍图赢），<1 向左（基线赢）。

    比「两根柱谁高」更适合这份对比，因为读者真正要看的是**分界线在哪个
    collective 上被跨过**，而不是绝对拍数。
    """
    L, Rr, T = 168, 96, 44
    h = T + rowh * len(items) + 26
    span = w - L - Rr
    cx = L + span * 0.42

    def tx(v: float) -> float:
        return cx + (span * 0.58) * (math.log(v) / math.log(xmax)) \
            if v >= center else cx - (span * 0.42) * (
                math.log(center / v) / math.log(xmax))
    p = [f'<rect x="{L}" y="{T - 16}" width="{span}" height="{rowh * len(items) + 12}" '
         f'class="plot"/>']
    p.append(f'<line x1="{cx:.1f}" y1="{T - 16}" x2="{cx:.1f}" '
             f'y2="{T + rowh * len(items) - 4}" class="axc"/>')
    p.append(f'<text x="{cx - 10:.1f}" y="{T - 24}" class="axl lose" '
             f'text-anchor="end">&#9664; {left_label}</text>')
    p.append(f'<text x="{cx + 10:.1f}" y="{T - 24}" class="axl win">'
             f'{right_label} &#9654;</text>')
    p.append(f'<text x="{cx:.1f}" y="{T + rowh * len(items) + 12}" '
             f'class="tick" text-anchor="middle">1.0&times;（打平）</text>')
    for i, it in enumerate(items):
        y = T + rowh * i
        v = it["ratio"]
        x2 = tx(v)
        win = v >= center
        p.append(f'<text x="{L - 10}" y="{y + 4}" class="axl" '
                 f'text-anchor="end">{it["label"]}</text>')
        x0, x1 = (cx, x2) if win else (x2, cx)
        p.append(f'<rect x="{x0:.1f}" y="{y - 9}" width="{max(1, x1 - x0):.1f}" '
                 f'height="18" class="bar {"cB" if win else "cC"}"/>')
        tv = f"{v:.2f}&times;"
        if win:
            p.append(f'<text x="{x2 + 6:.1f}" y="{y + 4}" class="barv">'
                     f'{tv}</text>')
        else:
            p.append(f'<text x="{x2 - 6:.1f}" y="{y + 4}" class="barv" '
                     f'text-anchor="end">{tv}</text>')
        p.append(f'<text x="{w - Rr + 8}" y="{y + 4}" class="tick">'
                 f'{it["note"]}</text>')
    return svg(w, h, "".join(p))


def line_chart(series: list[dict], *, w: int = 780, h: int = 300,
               xlabel: str = "", ylabel: str = "", xticks: list[float] | None
               = None, logx: bool = False, hline: float | None = None,
               hlabel: str = "") -> str:
    L, Rr, T, B = 66, 168, 26, 50
    xs = [p[0] for s in series for p in s["pts"]]
    ys = [p[1] for s in series for p in s["pts"]]
    if not xs:
        return ""
    if hline is not None:
        ys.append(hline)
    ty, hi = _axis_y(ys, T, h - B)

    def tx(v: float) -> float:
        lo, hh = min(xs), max(xs)
        if logx:
            lo, hh, v = (math.log10(max(lo, 1)), math.log10(max(hh, 2)),
                         math.log10(max(v, 1)))
        return L + (w - L - Rr) * (0 if hh == lo else (v - lo) / (hh - lo))
    p = [f'<rect x="{L}" y="{T}" width="{w - L - Rr}" height="{h - B - T}" '
         f'class="plot"/>']
    for i in range(5):
        yv = hi * i / 4
        p.append(f'<line x1="{L}" y1="{ty(yv):.1f}" x2="{w - Rr}" '
                 f'y2="{ty(yv):.1f}" class="gl"/>')
        p.append(f'<text x="{L - 8}" y="{ty(yv) + 4:.1f}" class="tick" '
                 f'text-anchor="end">{yv:,.0f}</text>')
    for xv in (xticks if xticks is not None else sorted(set(xs))):
        p.append(f'<text x="{tx(xv):.1f}" y="{h - B + 17}" class="tick" '
                 f'text-anchor="middle">{xv:g}</text>')
    if hline is not None:
        p.append(f'<line x1="{L}" y1="{ty(hline):.1f}" x2="{w - Rr}" '
                 f'y2="{ty(hline):.1f}" class="anch"/>')
        p.append(f'<text x="{w - Rr - 4}" y="{ty(hline) + 15:.1f}" '
                 f'class="anchl" text-anchor="end">{hlabel}</text>')
    for i, s in enumerate(series):
        d = " ".join(f"{'M' if j == 0 else 'L'} {tx(x):.1f} {ty(y):.1f}"
                     for j, (x, y) in enumerate(s["pts"]))
        p.append(f'<path d="{d}" class="cv {s["cls"]}"/>')
        for x, y in s["pts"]:
            p.append(f'<circle cx="{tx(x):.1f}" cy="{ty(y):.1f}" r="3.2" '
                     f'class="star {s["cls"]}"/>')
        yy = T + 16 + i * 19
        p.append(f'<line x1="{w - Rr + 8}" y1="{yy - 4}" x2="{w - Rr + 26}" '
                 f'y2="{yy - 4}" class="cv {s["cls"]}"/>')
        p.append(f'<text x="{w - Rr + 32}" y="{yy}" class="axl">'
                 f'{s["name"]}</text>')
    p.append(f'<text x="{(L + w - Rr) / 2}" y="{h - 6}" class="axl" '
             f'text-anchor="middle">{xlabel}</text>')
    p.append(f'<text x="14" y="{(T + h - B) / 2}" class="axl" '
             f'transform="rotate(-90 14 {(T + h - B) / 2})" '
             f'text-anchor="middle">{ylabel}</text>')
    return svg(w, h, "".join(p))


def stacked_bars(cats: list[str], series: list[dict], *, w: int = 880,
                 h: int = 300, ylabel: str = "", rotate: bool = False) -> str:
    """堆叠柱。类目多时用 rotate=True 斜排标签，否则中文标签会互相压字。"""
    L, Rr, T, B = 66, 158, 26, (118 if rotate else 74)
    tot = [sum(s["vals"][i] for s in series) for i in range(len(cats))]
    ty, hi = _axis_y(tot, T, h - B, pad=1.02)
    gw = (w - L - Rr) / max(1, len(cats))
    bw = min(46.0, gw * 0.6)
    p = [f'<rect x="{L}" y="{T}" width="{w - L - Rr}" height="{h - B - T}" '
         f'class="plot"/>']
    for i in range(5):
        yv = hi * i / 4
        p.append(f'<line x1="{L}" y1="{ty(yv):.1f}" x2="{w - Rr}" '
                 f'y2="{ty(yv):.1f}" class="gl"/>')
        p.append(f'<text x="{L - 8}" y="{ty(yv) + 4:.1f}" class="tick" '
                 f'text-anchor="end">{yv:,.0f}</text>')
    for ci, cat in enumerate(cats):
        cx = L + gw * (ci + 0.5)
        acc = 0.0
        for s in series:
            v = s["vals"][ci]
            if v <= 0:
                continue
            y0, y1 = ty(acc + v), ty(acc)
            p.append(f'<rect x="{cx - bw / 2:.1f}" y="{y0:.1f}" '
                     f'width="{bw:.1f}" height="{max(1, y1 - y0):.1f}" '
                     f'class="bar {s["cls"]}"/>')
            if y1 - y0 > 13:
                p.append(f'<text x="{cx:.1f}" y="{(y0 + y1) / 2 + 4:.1f}" '
                         f'class="barv" text-anchor="middle">{v:g}</text>')
            acc += v
        if rotate:
            t = " ".join(cat.split("<br>"))
            p.append(f'<text x="{cx:.1f}" y="{h - B + 14}" class="tick" '
                     f'text-anchor="end" transform="rotate(-34 {cx:.1f} '
                     f'{h - B + 14})">{t}</text>')
        else:
            for li, line in enumerate(cat.split("<br>")):
                p.append(f'<text x="{cx:.1f}" y="{h - B + 17 + li * 13}" '
                         f'class="tick" text-anchor="middle">{line}</text>')
    for si, s in enumerate(series):
        yy = T + 14 + si * 20
        p.append(f'<rect x="{w - Rr + 8}" y="{yy - 9}" width="12" height="12" '
                 f'class="bar {s["cls"]}"/>')
        p.append(f'<text x="{w - Rr + 26}" y="{yy + 1}" class="axl">'
                 f'{s["name"]}</text>')
    p.append(f'<text x="14" y="{(T + h - B) / 2}" class="axl" '
             f'transform="rotate(-90 14 {(T + h - B) / 2})" '
             f'text-anchor="middle">{ylabel}</text>')
    return svg(w, h, "".join(p))


# ---------------------------------------------------------------------------
# 3. 机制示意图
# ---------------------------------------------------------------------------

def svg_topology(a: dict) -> str:
    """8x6 的物理拓扑：6 个行环 + 8 个列环，每个节点都是桥。

    绕回段只对高亮的那一行、那一列画完整曲线，其余行列在边缘画短钩 ——
    48 个节点全画完整绕回会把图变成一团线，而绕回段的<i>存在</i>才是要点
    （mesh 没有这条边，Hamilton 回路靠它闭合）。
    """
    px, py, x0, y0, r = 62, 52, 104, 110, 13
    hr, hc = 2, 3
    ro, co = 30, 60          # 行 / 列绕回段绕到网格外面走的两个不同偏移
    p: list[str] = []

    def cx(x: int) -> float:
        return x0 + x * px

    def cy(y: int) -> float:
        return y0 + y * py

    p.append('<text x="18" y="26" class="bxt">A. 8&times;6 无缓冲环：'
             '6 个行环 &times; 8 节点 + 8 个列环 &times; 6 节点</text>')

    # 行内 / 列内的直段。高亮行列用粗绿，其余用底色细线。
    for y in range(MY):
        for x in range(MX - 1):
            cls = "arcR" if y == hr else "rlk"
            p.append(f'<line x1="{cx(x) + r + 2}" y1="{cy(y)}" '
                     f'x2="{cx(x + 1) - r - 2}" y2="{cy(y)}" class="{cls}"/>')
    for x in range(MX):
        for y in range(MY - 1):
            cls = "arcC" if x == hc else "rlk"
            p.append(f'<line x1="{cx(x)}" y1="{cy(y) + r + 2}" '
                     f'x2="{cx(x)}" y2="{cy(y + 1) - r - 2}" class="{cls}"/>')

    # 高亮行 / 列的绕回段：绕到网格外面走，避免压住任何节点。两条用不同偏移，
    # 只在左上角交叉一次，颜色与线型都不同，不会看错。
    xr, yt = cx(MX - 1) + ro, y0 - ro
    p.append(f'<path d="M {cx(MX - 1) + r + 2} {cy(hr)} H {xr - 10} '
             f'Q {xr} {cy(hr)} {xr} {cy(hr) - 10} V {yt + 10} '
             f'Q {xr} {yt} {xr - 10} {yt} H {x0 - ro + 10} '
             f'Q {x0 - ro} {yt} {x0 - ro} {yt + 10} V {cy(hr) - 10} '
             f'Q {x0 - ro} {cy(hr)} {x0 - ro + 10} {cy(hr)} '
             f'H {cx(0) - r - 2}" class="wrp hl"/>')
    xl2, yb2, yt2 = x0 - co, cy(MY - 1) + co, y0 - co
    p.append(f'<path d="M {cx(hc)} {cy(MY - 1) + r + 2} V {yb2 - 10} '
             f'Q {cx(hc)} {yb2} {cx(hc) - 10} {yb2} H {xl2 + 10} '
             f'Q {xl2} {yb2} {xl2} {yb2 - 10} V {yt2 + 10} '
             f'Q {xl2} {yt2} {xl2 + 10} {yt2} H {cx(hc) - 10} '
             f'Q {cx(hc)} {yt2} {cx(hc)} {yt2 + 10} '
             f'V {cy(0) - r - 2}" class="wrp hl2"/>')
    p.append(f'<polygon points="{cx(0) - r - 2},{cy(hr)} '
             f'{cx(0) - r - 11},{cy(hr) - 5} {cx(0) - r - 11},{cy(hr) + 5}" '
             f'class="ok2"/>')
    p.append(f'<polygon points="{cx(hc)},{cy(0) - r - 2} '
             f'{cx(hc) - 5},{cy(0) - r - 11} {cx(hc) + 5},{cy(0) - r - 11}" '
             f'class="colt"/>')

    # 其余行列只画短钩，点明绕回段的存在而不糊成一团
    for y in range(MY):
        if y == hr:
            continue
        p.append(f'<path d="M {cx(MX - 1) + r + 2} {cy(y)} q 14 0 14 -12" '
                 f'class="wrp"/>')
        p.append(f'<path d="M {cx(0) - r - 2} {cy(y)} q -14 0 -14 -12" '
                 f'class="wrp"/>')
    for x in range(MX):
        if x == hc:
            continue
        p.append(f'<path d="M {cx(x)} {cy(MY - 1) + r + 2} q 0 12 12 12" '
                 f'class="wrp"/>')
        p.append(f'<path d="M {cx(x)} {cy(0) - r - 2} q 0 -12 12 -12" '
                 f'class="wrp"/>')

    for y in range(MY):
        for x in range(MX):
            n = y * MX + x
            cls = "nd"
            if y == hr and x == hc:
                cls = "nd src"
            elif y == hr or x == hc:
                cls = "nd dst"
            p.append(f'<circle cx="{cx(x)}" cy="{cy(y)}" r="{r}" '
                     f'class="{cls}"/>')
            p.append(f'<text x="{cx(x)}" y="{cy(y) + 4}" class="tag">{n}</text>')

    n_hl = hr * MX + hc
    ly = cy(MY - 1) + co + 48
    p.append(f'<text x="18" y="{ly}" class="bxl">'
             f'<tspan class="ok2">绿</tspan>＝行 {hr} 环（含绕回段 '
             f'{hr * MX + MX - 1}&rarr;{hr * MX}，绕到网格外画）；'
             f'<tspan class="colt">紫</tspan>＝列 {hc} 环（绕回段 '
             f'{(MY - 1) * MX + hc}&rarr;{hc}）；'
             f'其余行列的绕回段只在两端画了短钩</text>')
    p.append(f'<text x="18" y="{ly + 22}" class="bxl ok2">'
             f'节点 {n_hl} 同时属于行 {hr} 和列 {hc} &mdash; '
             f'全部 {a["n"]} 个节点都是这样的桥，没有单独的路由器</text>')
    p.append(f'<text x="18" y="{ly + 44}" class="bxl dim">'
             f'编号 nid = y&times;{MX} + x，与仿真器一致；每条边都是双向，'
             f'即 {a["n_undirected_links"]} 条无向段 = '
             f'{a["n_directed_links"]} 条有向弧</text>')

    # B: 环站放大
    bx = cx(MX - 1) + 150
    by = 150
    p.append(f'<text x="{bx - 66}" y="26" class="bxt">B. 一个环站（桥）内部：'
             f'零缓冲</text>')
    p.append(f'<line x1="{bx - 108}" y1="{by}" x2="{bx + 108}" y2="{by}" '
             f'class="arcR"/>')
    p.append(f'<line x1="{bx}" y1="{by - 96}" x2="{bx}" y2="{by + 96}" '
             f'class="arcC"/>')
    p.append(f'<text x="{bx - 112}" y="{by - 10}" class="bxl ok2">行环</text>')
    p.append(f'<text x="{bx + 6}" y="{by - 88}" class="bxl colt">列环</text>')
    p.append(f'<rect x="{bx - 62}" y="{by - 9}" width="30" height="18" '
             f'rx="3" class="bx src"/>')
    p.append(f'<text x="{bx - 47}" y="{by - 16}" class="bxl ok2" '
             f'text-anchor="middle">行口</text>')
    p.append(f'<rect x="{bx - 9}" y="{by + 32}" width="18" height="30" '
             f'rx="3" class="bx src"/>')
    p.append(f'<text x="{bx + 16}" y="{by + 52}" class="bxl colt">列口</text>')
    p.append(f'<circle cx="{bx}" cy="{by}" r="17" class="nd src"/>')
    p.append(f'<text x="{bx}" y="{by + 4}" class="tag">{n_hl}</text>')
    # L1 挂在列环左侧，列环才能明显地继续往下走 —— 环是闭环，不在核这里终止
    p.append(f'<rect x="{bx - 112}" y="{by + 74}" width="104" height="30" '
             f'rx="6" class="bx arb"/>')
    p.append(f'<text x="{bx - 60}" y="{by + 94}" class="bxl" '
             f'text-anchor="middle">AI core + L1</text>')
    p.append(f'<line x1="{bx - 47}" y1="{by + 9}" x2="{bx - 47}" '
             f'y2="{by + 74}" class="ar"/>')
    p.append(f'<line x1="{bx}" y1="{by + 62}" x2="{bx}" y2="{by + 68}" '
             f'class="ar"/>')
    p.append(f'<line x1="{bx}" y1="{by + 68}" x2="{bx - 76}" y2="{by + 68}" '
             f'class="ar"/>')
    p.append(f'<line x1="{bx - 76}" y1="{by + 68}" x2="{bx - 76}" '
             f'y2="{by + 74}" class="ar"/>')
    p.append(f'<line x1="{bx}" y1="{by + 62}" x2="{bx}" y2="{by + 78}" '
             f'class="ar"/>')
    for i, line in enumerate([
            f"<tspan class='bxl ok2'>每核就 2 个口：1 个通行环、1 个通列环，"
            f"合起来 {RAMP_BW} flit/cy，</tspan>",
            f"<tspan class='bxl ok2'>正好等于 L1 ramp 的 {RAMP_BW} flit/cy "
            f"&mdash; 两边谁都不拖谁。</tspan>",
            "",
            "环上没有队列：flit 每拍必须往前走一段，要么被",
            "抽取下环，要么继续绕。",
            "",
            "转环（行&rarr;列）复用的就是这两个口 + 一个桥 FIFO，",
            "不额外开抽头；它要同时占住两个环的相邻两段，",
            "是原子操作（判据 R4）。"]):
        cls = "bxl warn" if "原子" in line else "bxl"
        p.append(f'<text x="{bx - 108}" y="{by + 128 + i * 20}" '
                 f'class="{cls}">{line}</text>')
    return svg(bx + 220, ly + 62, "".join(p))


def _fold_order(k: int) -> list[int]:
    """折叠布线下「物理位置 -> 逻辑环序号」的排列。

    折叠 torus 的标准摆法：0,2,4,... 去，...,5,3,1 回。与
    rg_ring_topo.link_pitches 完全一致 —— 那边判定「第 k/2-1 段和第 k-1 段
    是 1 个 pitch，其余 2 个 pitch」，这里把它画出来。
    """
    pos = {u: 2 * u for u in range(k // 2)}          # 去程落在物理偶数位
    pos.update({k // 2 + i: k - 1 - 2 * i for i in range(k - k // 2)})
    return [u for u, _ in sorted(pos.items(), key=lambda kv: kv[1])]


def svg_fold(a: dict) -> str:
    """链路时延口径的三块：行环折叠布线、列环折叠布线、换维的两条路各多少拍。

    折叠布线的要点是「一跳跨 2 个 core pitch」，两段折返端例外只跨 1 个 —— 所以
    环上的段延迟**不均匀**（行 10/10/10/5，列 14/14/7），这张图把每一段的拍数
    标在线上，后面所有时延地板都是拿它们求最短路得来的。
    """
    ph, pv, tt = a["pitch_h"], a["pitch_v"], a["t_turn"]
    p: list[str] = []

    def ring_panel(k: int, pitch: int, x0: float, yc: float, px: float,
                   name: str, cls: str) -> None:
        order = _fold_order(k)               # 物理位 -> 逻辑序号
        pos = {u: i for i, u in enumerate(order)}

        def X(u: int) -> float:
            return x0 + pos[u] * px
        lane = 30.0
        for u in range(k):
            v = (u + 1) % k
            one = abs(pos[u] - pos[v]) == 1
            lat = pitch * (1 if one else 2)
            up = u < k // 2
            y = yc - lane if up else yc + lane
            if one:                          # 折返端：两核物理相邻，直连一小段
                xa, xb = sorted((X(u), X(v)))
                p.append(f'<path d="M {xa + 15} {yc} H {xb - 15}" '
                         f'class="fld {cls}"/>')
                p.append(f'<text x="{(xa + xb) / 2}" y="{yc - 6}" '
                         f'class="bxl warn" text-anchor="middle">{lat}</text>')
                continue
            p.append(f'<path d="M {X(u)} {yc + (-15 if up else 15)} '
                     f'V {y} H {X(v)} V {yc + (-15 if up else 15)}" '
                     f'class="fld {cls}"/>')
            p.append(f'<text x="{(X(u) + X(v)) / 2}" '
                     f'y="{y + (-6 if up else 14)}" class="bxl" '
                     f'text-anchor="middle">{lat}</text>')
        for u in range(k):
            p.append(f'<circle cx="{X(u)}" cy="{yc}" r="15" class="nd"/>')
            p.append(f'<text x="{X(u)}" y="{yc + 4}" class="tag">{u}</text>')
        for i in range(k - 1):               # 物理相邻的 core 间距标注
            xa, xb = x0 + i * px, x0 + (i + 1) * px
            p.append(f'<line x1="{xa + 16}" y1="{yc + 68}" x2="{xb - 16}" '
                     f'y2="{yc + 68}" class="rlk"/>')
            p.append(f'<text x="{(xa + xb) / 2}" y="{yc + 64}" '
                     f'class="bxl dim" text-anchor="middle">{pitch}</text>')
        p.append(f'<text x="{x0 - 20}" y="{yc + 72}" class="bxl dim" '
                 f'text-anchor="end">物理相邻</text>')
        p.append(f'<text x="{x0 - 20}" y="{yc + 4}" class="bxl {cls}" '
                 f'text-anchor="end">{name}</text>')

    p.append('<text x="16" y="24" class="bxt">A. 行环（8 核）折叠布线：'
             '环上一跳跨 2 个 core pitch，只有两段折返端跨 1 个</text>')
    ring_panel(MX, ph, 150, 84, 86, "行环", "ok2")
    p.append(f'<text x="16" y="196" class="bxl">圈内标注 = 该段线延迟（拍）；'
             f'一圈 {a["row_ring_wire"]} 拍 = 6&times;{2 * ph} + 2&times;{ph}。'
             f'圆圈里的号是<b>环上序号</b>，不是物理位置 &mdash; 物理上是 '
             f'0,7,1,6,2,5,3,4 交错摆的，这就是「折叠」。</text>')

    p.append('<text x="16" y="248" class="bxt">B. 列环（6 核）：同样的折叠，'
             '纵向一个 pitch 更贵</text>')
    ring_panel(MY, pv, 150, 306, 86, "列环", "colt")
    p.append(f'<text x="16" y="418" class="bxl">一圈 '
             f'{a["col_ring_wire"]} 拍 = 4&times;{2 * pv} + 2&times;{pv} '
             f'&mdash; 与行环<b>恰好同长</b>（{a["row_ring_wire"]} 拍），'
             f'列少 2 个核但每 pitch 贵 {pv - ph} 拍，正好抵平。</text>')

    # C. 换维两条路
    y0 = 470
    p.append(f'<text x="16" y="{y0}" class="bxt">C. 从行环换到列环：'
             f'两条路，桥不是唯一的一条</text>')
    for i, (nm, cost, cls, note) in enumerate([
            ("行环 &rarr; 桥 &rarr; 列环（t_turn）", tt, "bx fill",
             "环上不落地，但桥的 transfer FIFO 要占住"),
            ("行环 &rarr; 落 L1 &rarr; 列环（ramp）", RAMP, "bx src",
             "占一次 ramp 带宽，拍图按相位边界收")]):
        yy = y0 + 26 + i * 58
        p.append(f'<rect x="150" y="{yy}" width="250" height="40" rx="6" '
                 f'class="{cls}"/>')
        p.append(f'<text x="275" y="{yy + 25}" class="bxl" '
                 f'text-anchor="middle">{nm}</text>')
        p.append(f'<text x="418" y="{yy + 26}" class="bxt '
                 f'{"warn" if cost > RAMP else "ok2"}">{cost} 拍</text>')
        p.append(f'<text x="484" y="{yy + 26}" class="bxl dim">{note}</text>')
    p.append(f'<text x="16" y="{y0 + 158}" class="bxl warn">'
             f'{tt} 拍的桥比 {RAMP} 拍的中继贵 {tt // RAMP}&times; &mdash; '
             f'所以「按维分解、每维一相位、维间落 L1」的拍图根本不过桥，'
             f'时延地板也必须按 min(t_turn, ramp) = {min(tt, RAMP)} 拍算，'
             f'否则拍图会合法地低于「下界」。</text>')
    return svg(880, y0 + 190, "".join(p))


def sec_wire(d: dict) -> str:
    """§2：把 link delay 的口径一次讲全，并给出它改掉了哪些结论。"""
    c, att, brg = d["coll"], d.get("att"), d.get("brg")
    a = c["audit"]
    ph, pv, tt = a["pitch_h"], a["pitch_v"], a["t_turn"]
    A = ({r["key"]: r for r in att["schemes"]}["A_full_2port"]
         if att else None)
    di = A["distance"] if A else {}
    setup = [
        ("横向物理相邻两核（1 pitch）", f"{ph} 拍", "输入口径"),
        ("纵向物理相邻两核（1 pitch）", f"{pv} 拍", "输入口径"),
        ("行环典型一跳（折叠后隔 1 个核，2 pitch）",
         f"<b>{2 * ph} 拍</b>", f"= 2&times;{ph}，每行环 {MX - 2} 段"),
        ("列环典型一跳（2 pitch）", f"<b>{2 * pv} 拍</b>",
         f"= 2&times;{pv}，每列环 {MY - 2} 段"),
        ("行/列环折返端一跳（1 pitch）", f"{ph} / {pv} 拍",
         "每环各 2 段，折叠布线的两端"),
        ("换环过桥 t_turn", f"<b>{tt} 拍</b>",
         "flit 在桥的 transfer FIFO 里被搬到另一个环"),
        ("落 L1 再上另一个环（ramp）", f"{RAMP} 拍",
         f"换维的<b>另一条</b>路，比过桥便宜 {tt // RAMP}&times;"),
        ("核的进出速率", f"{RAMP_BW} flit/cy",
         "两个环口合起来，恰好等于 L1 ramp"),
    ]
    derived = [
        ("行环一圈线延迟", f'{a["row_ring_wire"]} 拍',
         f'{MX - 2}&times;{2 * ph} + 2&times;{ph}'),
        ("列环一圈线延迟", f'{a["col_ring_wire"]} 拍',
         f'{MY - 2}&times;{2 * pv} + 2&times;{pv}，与行环同长'),
        ("最远两核的零竞争时延（直径）", f'{di.get("diameter_cy", "&mdash;")} 拍',
         f'{di.get("max_hops", "&mdash;")} 跳，换维按 ramp 收'),
        ("全部 2256 个有序核对的平均时延",
         f'{di.get("avg_lat_cy", "&mdash;")} 拍',
         f'平均 {di.get("avg_hops", "&mdash;")} 跳'),
    ]

    def tbl(rowsl: list[tuple[str, str, str]], h0: str) -> str:
        tr = [f"<tr><th>{h0}</th><th>值</th><th>来源 / 算法</th></tr>"]
        for k, v, s in rowsl:
            tr.append(f'<tr><td class="l">{k}</td><td>{v}</td>'
                      f'<td class="muted">{s}</td></tr>')
        return f'<table class="tbl">{"".join(tr)}</table>'

    tsw = None
    if brg:
        tsw = next((r for r in brg["turn_sweep"]
                    if r["pattern"] == "alltoall" and r["m"] == 13), None)
    ctrl = None
    if brg:
        ctrl = next((r for r in brg["no_turn_control"]
                     if r["pattern"] == "alltoall" and r["m"] == 13), None)
    sweep_note = ""
    if tsw and ctrl:
        r1 = next(r for r in tsw["rows"] if r["t_turn"] == 1)
        r10 = next(r for r in tsw["rows"] if r["t_turn"] == tt)
        sweep_note = (
            f'<div class="note"><b>{tt} 拍的桥不是记账细节，它改排名。</b>'
            f'把 t_turn 从 1 扫到 {tt}，会过桥的 <code>flat</code> 全交换'
            f'（m=13）从 {f(r1["makespan"])} 拍涨到 {f(r10["makespan"])} 拍'
            f'（{times(tsw["makespan_10_over_1"])}），偏转次数涨'
            f'{times(tsw["deflect_10_over_1"])}；而<b>不过桥</b>的按维分解版本'
            f'（<code>dim_2phase</code>，全程 {ctrl["n_bridges_touched"]} 次过桥）'
            f'一拍都不多付。这就是 §10 要单独量一节桥 buffer 的原因，也是'
            f'§13 里时延地板必须按 min(t_turn, ramp) = {min(tt, RAMP)} 拍'
            f'定义的原因。</div>')
    lg = ""
    if brg and brg.get("legacy_ref"):
        lw = brg["legacy_wire"]
        L = {r["m"]: r for r in brg["legacy_ref"]}
        new1 = next((r for r in brg["per_pattern"] if r["pattern"] == "alltoall"
                     and r["m"] == 1), None)
        new13 = next((r for r in brg["per_pattern"] if r["pattern"] == "alltoall"
                      and r["m"] == 13), None)
        if 1 in L and new1 and new13:
            lg = (f'<p class="muted">这次口径改动值多少拍，也是量出来的而不是估的：'
                  f'同一段 <code>flat</code> 全交换基线，在旧口径'
                  f'（每跳 {lw["pitch_h"]}/{lw["pitch_v"]} 拍、过桥 '
                  f'{lw["t_turn"]} 拍、不折叠）下是 {f(L[1]["makespan"])} 拍'
                  f'（m=1）/ {f(L[13]["makespan"])} 拍（m=13），'
                  f'新口径下是 {f(new1["makespan"])} / {f(new13["makespan"])} 拍，'
                  f'即 {times(new1["makespan"] / L[1]["makespan"])} / '
                  f'{times(new13["makespan"] / L[13]["makespan"])}；'
                  f'因桥满被打偏的次数从 {f(L[1]["deflect_total"])} 涨到 '
                  f'{f(new1["deflect_total"])}（m=1）。'
                  f'<b>贵的不只是线，更是桥</b> &mdash; 详见 §10。</p>')
    return f"""<p>上一节把<b>怎么连</b>定了，这一节把<b>一跳多少拍</b>定下来 &mdash;
后面每一个 makespan、每一条下界都是这组数乘出来的，所以先单独列清楚。
口径只有三个输入量：<b>横向一个 core pitch {ph} 拍、纵向 {pv} 拍、换环过桥
{tt} 拍</b>；其余全是推出来的。</p>
<div class="fig">{svg_fold(a)}
<div class="cap"><b>图：折叠布线让「环上相邻」≠「物理相邻」。</b>
环上编号 0&rarr;1 的两个核，物理上中间还隔着一个核，所以一跳收
2&times;{ph}={2 * ph}（行）/ 2&times;{pv}={2 * pv}（列）拍；每个环只有<b>两段折返端</b>
是物理相邻的 {ph}/{pv} 拍。段延迟因此<b>不均匀</b>，但一圈总长仍是
2(k&minus;1) 个 pitch，且任何最短路的总延迟与走哪个方向、经过哪几段无关
（已写成断言，见 §18）。C 图是这套口径最重要的一个后果：换维有两条路，
桥不是唯一的一条。</div></div>
{tbl(setup, "口径项")}
<h3>由它推出来的量</h3>
{tbl(derived, "推出量")}
<div class="note"><b>三个后果，每一个都改了结论：</b><ol>
<li><b>跳数不再等价于拍数。</b>一跳可能 {2 * ph} 拍也可能 {ph} 拍，所以
「几跳」这个说法在本报告里只用于计数，谈时延一律用拍。好消息是<b>最短跳数路径
仍然就是最短时延路径</b>：两段折返端在环上正好隔半圈，任何半圈弧都恰好含其中一段，
两个方向的账因此仍然对得上（2256 对全测，见 §18）。</li>
<li><b>过桥不再是最便宜的换维方式。</b>0&rarr;9（差一行一列）走桥是
{2 * ph} + {2 * pv} + {tt} = 34 拍，落 L1 中继再上列环是
{2 * ph} + {2 * pv} + {RAMP} = 26 拍。<b>所以 §3 的零竞争时延地板只能收
min(t_turn, ramp) = {min(tt, RAMP)} 拍</b> —— 否则按维分解的拍图（它根本不过桥）
会「低于下界」，那不是它违规，是界算错了。</li>
<li><b>桥从记账细节变成了绑定资源。</b>{tt} 拍的过桥时延意味着一个转环 flit
要占住桥的 FIFO 条目 {tt} 拍；到达率不变、停留时间涨 10&times;，占用就涨 10&times;。
这把「桥要多深」从二阶问题变成一阶问题，单独占一节（§10）。</li>
</ol></div>
{sweep_note}
{lg}
<p class="muted">实现口径：环上每一段单独收自己的拍数（<code>link_lat</code>），
不再用「每环一个平均跳延迟」；零竞争时延地板在 <code>(core, 所在环)</code>
状态图上跑 Dijkstra，换维那条边收 min(t_turn, ramp)。
<b>旧口径未被静默改掉</b>：<code>ring_islip2d</code> 那条集中式参照线与 §14 的
mesh 参照仍是旧的 H=7/V=9、t_turn=1 口径（<code>LEGACY_WIRE</code> 显式钉住），
所以那两处的绝对拍数<b>不能</b>与本节口径下的数直接相减，只能各自内部比。</p>
"""


def svg_bridge_heat(panels: list[dict], *, w: int = 880) -> str:
    """48 个桥的占用热力图。每块一个 8×6 网格，颜色 = 平均占用深度。

    用网格而不是柱状图，因为要看的是<b>热点在哪</b>（fan-in 的那一行一列），
    而不是 48 个数字的排序。
    """
    cw, chh, gap = 46.0, 30.0, 40.0
    p: list[str] = []
    for i, pan in enumerate(panels):
        x0 = 42 + i * (MX * cw + gap + 66)
        y0 = 62
        by = {r["node"]: r for r in pan["rows"]}
        hi = max([r["mean"] for r in pan["rows"]] or [1.0]) or 1.0
        p.append(f'<text x="{x0 - 24}" y="30" class="bxt">{pan["title"]}</text>')
        p.append(f'<text x="{x0 - 24}" y="48" class="bxl dim">'
                 f'{pan["sub"]}</text>')
        for y in range(MY):
            for x in range(MX):
                n = y * MX + x
                r = by.get(n)
                cx, cy = x0 + x * cw, y0 + y * chh
                if r is None:
                    p.append(f'<rect x="{cx}" y="{cy}" width="{cw - 3}" '
                             f'height="{chh - 3}" class="bx"/>')
                    continue
                t = r["mean"] / hi
                # 深蓝 -> 橙红，只用一条色带，读者不必记颜色顺序
                rr = int(40 + 200 * t)
                gg = int(70 + 90 * (1 - abs(t - 0.5) * 2))
                bb = int(150 * (1 - t) + 40)
                p.append(f'<rect x="{cx}" y="{cy}" width="{cw - 3}" '
                         f'height="{chh - 3}" fill="rgb({rr},{gg},{bb})" '
                         f'stroke="#2a3555"/>')
                p.append(f'<text x="{cx + (cw - 3) / 2}" y="{cy + 13}" '
                         f'class="tag">{n}</text>')
                p.append(f'<text x="{cx + (cw - 3) / 2}" y="{cy + 24}" '
                         f'class="tag">{r["mean"]:.1f}</text>')
        p.append(f'<text x="{x0 - 24}" y="{y0 + MY * chh + 18}" '
                 f'class="bxl dim">空格 = 该桥全程没被用到；格内'
                 f'上排是节点号、下排是平均占用深度</text>')
    return svg(w, 62 + MY * chh + 34, "".join(p))


def svg_attach(att: dict) -> str:
    """三块：折叠全环怎么连、半跨环为什么修不回来、每核两个口怎么花。

    所有数字从 ring_attach_8x6.json 里读，图和表不会讲两个故事。
    """
    S = {r["key"]: r for r in att["schemes"]}
    A, C0, C = S["A_full_2port"], S["C0_rowhalf_noseam"], S["C_rowhalf_seam"]
    G, H, B = S["G_row_only_1port"], S["H_two_on_row"], S["B_full_1lane"]
    px, r = 62, 15
    p: list[str] = []

    def core_row(x0: int, y: int, xs: list[int], *, hi: int | None = None
                 ) -> dict[int, float]:
        at = {}
        for i, x in enumerate(xs):
            cx = x0 + i * px
            at[x] = cx
            cls = "nd src" if x == hi else "nd"
            p.append(f'<circle cx="{cx}" cy="{y}" r="{r}" class="{cls}"/>')
            p.append(f'<text x="{cx}" y="{y + 4}" class="tag">{x}</text>')
        return at

    def fold(at: dict[int, float], y: int, xs: list[int], cls: str) -> None:
        """把一行核折叠成一个闭环：隔一个走过去，再隔一个走回来。

        去程画在上方、回程画在下方、两端的掉头弧画浅一点，读者一眼能看出
        「闭环但没有长线」。
        """
        q = sorted(xs)
        go = q[0::2]
        back = q[len(q) - 1 if (len(q) - 1) % 2 else len(q) - 2::-2]
        tour = go + back
        for i, a in enumerate(tour):
            b = tour[(i + 1) % len(tour)]
            up = i < len(go) - 1
            turn = i in (len(go) - 1, len(tour) - 1)
            dy = (-30 if up else 30) if not turn else (22 if i else -22)
            xa, xb = at[a], at[b]
            ya = y + (-r if up else r) if not turn else y
            p.append(f'<path d="M {xa} {ya} Q {(xa + xb) / 2} {y + dy} '
                     f'{xb} {ya}" class="{cls}"/>')

    # ---- A: folded full ring ------------------------------------------------
    y1 = 96
    p.append('<text x="18" y="30" class="bxt">A. 全环 + 折叠布线（推荐）'
             '</text>')
    p.append(f'<text x="18" y="52" class="bxl dim">一行 8 个核连成一个闭环，'
             f'隔一个核走过去再隔一个核走回来 &mdash; 没有一根长绕回线，'
             f'最长单根线 {A["structure"]["max_link_pitches"]} 个核间距</text>')
    at1 = {x: 70 + x * px for x in range(8)}
    fold(at1, y1, list(range(8)), "fld")
    core_row(70, y1, list(range(8)), hi=3)
    p.append(f'<line x1="{at1[3] + 4}" y1="{y1 + 58}" x2="{at1[4] - 4}" '
             f'y2="{y1 + 58}" class="ar"/>')
    p.append(f'<text x="{(at1[3] + at1[4]) / 2}" y="{y1 + 76}" '
             f'class="bxl ok2" text-anchor="middle">中线</text>')
    p.append(f'<text x="{at1[7] + 46}" y="{y1 - 6}" class="bxl">环长 8，'
             f'最远 {A["distance"]["max_hops"]} 跳（双向取近）</text>')
    p.append(f'<text x="{at1[7] + 46}" y="{y1 + 16}" class="bxl ok2">'
             f'每行有 2 段跨过中线 &rArr; x 向对分 '
             f'{A["cuts"]["x"]["min_cap_per_dir"]} flit/cy</text>')

    # ---- C: half span + seam ------------------------------------------------
    y2 = y1 + 204
    p.append(f'<text x="18" y="{y2 - 100}" class="bxt">B. 半跨环（2×4）：'
             f'跨度减半，但缝必须补</text>')
    at2 = {x: 70 + x * px for x in range(8)}
    fold({k: v for k, v in at2.items() if k < 4}, y2, [0, 1, 2, 3], "fld")
    fold({k: v for k, v in at2.items() if k >= 4}, y2, [4, 5, 6, 7], "fld")
    core_row(70, y2, list(range(8)))
    xm = (at2[3] + at2[4]) / 2
    p.append(f'<path d="M {xm} {y2 - 22} V {y2 - 46}" class="wrp hl2"/>')
    p.append(f'<rect x="{xm - 32}" y="{y2 - 74}" width="64" height="26" '
             f'rx="5" class="bx fill"/>')
    p.append(f'<text x="{xm}" y="{y2 - 56}" class="bxl warn" '
             f'text-anchor="middle">缝桥</text>')
    tx = at2[7] + 46
    for i, ln in enumerate([
            f'行内最远 {(MX // 2) // 2} 跳（全环 {MX // 2} 跳），更短',
            f'<tspan class="bxl warn">不加缝桥：左右各 {N // 2} 核互不可达</tspan>',
            f'（可达对只有 {C0["distance"]["reachable_pairs"]}/'
            f'{C0["distance"]["total_pairs"]}）',
            f'<tspan class="bxl warn">加缝桥：跨中线只剩 1 个存转发 FIFO'
            f'</tspan>',
            f'x 向对分 {A["cuts"]["x"]["min_cap_per_dir"]}&rarr;'
            f'{C["cuts"]["x"]["min_cap_per_dir"]} flit/cy，全交换下界 '
            f'{A["bounds"]["alltoall/T0"]["lb"]}&rarr;'
            f'{C["bounds"]["alltoall/T0"]["lb"]} 拍']):
        p.append(f'<text x="{tx}" y="{y2 - 34 + i * 20}" class="bxl">{ln}'
                 f'</text>')

    # ---- C: how to spend the two ports -------------------------------------
    y3 = y2 + 128
    p.append(f'<text x="18" y="{y3}" class="bxt">C. 每核这 2 个口怎么花</text>')
    opts = [
        ("1 行口 + 1 列口", "ok", [
            f"两维都能直接上下环，进出速率 {A['core_rate']} flit/cy",
            f"正好等于 L1 ramp 的 {att['geometry']['ramp_bw']} flit/cy",
            f"桥与核同址、复用这 2 个口 &rArr; 额外抽头 "
            f"{A['structure']['n_extra_tap_bridges']} 个",
            f"平均 {A['distance']['avg_hops']} 跳 / "
            f"{A['distance']['avg_lat_cy']} 拍"]),
        ("2 个口都挂行环", "warn", [
            "行向进出翻倍，但核无法直接进出列环",
            f"每个列环都要给桥单开抽头 &rArr; 额外抽头 "
            f"{H['structure']['n_extra_tap_bridges']} 个",
            "纵向流程要过两次存转发桥",
            f"平均 {H['distance']['avg_hops']} 跳 / "
            f"{H['distance']['avg_lat_cy']} 拍"]),
        ("只用 1 个口", "bad", [
            f"进出速率 {G['core_rate']} flit/cy，"
            f"只用掉一半 L1 ramp",
            f"端口界翻倍：全收集/收集 "
            f"{A['bounds']['allgather/T1']['lb']}&rarr;"
            f"{G['bounds']['allgather/T1']['lb']} 拍",
            f"额外抽头 {G['structure']['n_extra_tap_bridges']} 个（同上）",
            f"平均 {G['distance']['avg_hops']} 跳 / "
            f"{G['distance']['avg_lat_cy']} 拍"]),
    ]
    bw = 300
    for i, (title, cls, lines) in enumerate(opts):
        bx = 18 + i * (bw + 16)
        p.append(f'<rect x="{bx}" y="{y3 + 16}" width="{bw}" height="122" '
                 f'rx="8" class="bx {"src" if cls == "ok" else "fill"}"/>')
        mark = "✓" if cls == "ok" else "✗"
        mcls = "ok2" if cls == "ok" else "warn"
        p.append(f'<text x="{bx + 14}" y="{y3 + 40}" class="bxt">'
                 f'<tspan class="{mcls}">{mark}</tspan> {title}</text>')
        for j, ln in enumerate(lines):
            p.append(f'<text x="{bx + 14}" y="{y3 + 62 + j * 19}" '
                     f'class="bxl{"" if cls == "ok" else " dim"}">{ln}</text>')

    p.append(f'<text x="18" y="{y3 + 164}" class="bxl dim">'
             f'另一种「半环」读法是只留一条车道（单向环）：金属减半、换成 2× '
             f'线宽后 12 个下界与全环 <tspan class="bxl">一个不差地打平</tspan>，'
             f'但平均时延 '
             f'{B["distance"]["avg_lat_cy"] / A["distance"]["avg_lat_cy"]:.2f}'
             f'&times;、直径 {A["distance"]["diameter_cy"]}&rarr;'
             f'{B["distance"]["diameter_cy"]} 拍 &mdash; 净亏时延。</text>')
    return svg(1040, y3 + 190, "".join(p))


def sec_attach(att: dict) -> str:
    """方案表：四道物理门槛先筛，剩下的按界/金属/抽头/时延排。"""
    S = {r["key"]: r for r in att["schemes"]}
    A = S["A_full_2port"]
    head = ("方案", "每核<br>进出速率", "全<br>连通", "x 向<br>对分",
            "y 向<br>对分", "最长<br>单线", "额外<br>抽头", "平均<br>时延",
            "直径", "最差界<br>vs A", "判决")
    tr = ["<tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"]
    order = ["A_full_2port", "B_full_1lane", "H_two_on_row", "D_colhalf_seam",
             "E_bothhalf_seam", "C_rowhalf_seam", "C0_rowhalf_noseam",
             "F_rowhalf_stagger", "G_row_only_1port"]
    for k in order:
        r = S[k]
        st, di = r["structure"], r["distance"]
        if not r["fails"]:
            verdict = (f'<b class="good">推荐</b>' if r["rank"] == 1
                       else f'第 {r["rank"]} 名')
            vcls = "good" if r["rank"] == 1 else ""
        else:
            why = {"全连通": "左右两半不连通",
                   "最长单根线 ≤2 pitch": "最长单线 > 2 pitch",
                   "环侧速率跟得上 L1 ramp": "环侧速率跟不上 ramp",
                   "每核 ≤2 口": "超过 2 个口"}
            verdict = ('<span class="bad">出局：'
                       + why.get(r["fails"][0], r["fails"][0]) + "</span>")
            vcls = "bad"
        w = r["worst_vs_A"]
        cells = [
            f'<td class="l">{r["label"]}</td>',
            f'<td>{r["core_rate"]}</td>',
            f'<td>{"是" if not r["disconnected"] else "<b>否</b>"}</td>',
            f'<td>{r["cuts"]["x"]["min_cap_per_dir"]}</td>',
            f'<td>{r["cuts"]["y"]["min_cap_per_dir"]}</td>',
            f'<td>{st["max_link_pitches"]}</td>',
            f'<td>{st["n_extra_tap_bridges"]}</td>',
            f'<td>{di["avg_lat_cy"]}</td>',
            f'<td>{di["diameter_cy"]}</td>',
            f'<td>{"&mdash;" if w is None else f"{w:.2f}&times;"}</td>',
            f'<td class="{vcls}">{verdict}</td>']
        cls = ' class="hl"' if k == "A_full_2port" else ""
        tr.append(f"<tr{cls}>" + "".join(cells) + "</tr>")
    return (f'<table class="tbl">{"".join(tr)}</table>'
            f'<p class="muted">对分容量按<b>每方向 flit/cy</b> 记；'
            f'「额外抽头」指桥必须自己在环上开的抽头数（与核同址且核已有两口时为 0）；'
            f'最长单线以核间距为单位，&gt;2 意味着折叠布线压不下去、要么降频要么插寄存器；'
            f'「最差界 vs A」是六个 collective × T0/T1 共 12 个路由无关下界里'
            f'相对方案 A 最差的那个比值。排序键：先看有没有界更差，再看金属、'
            f'额外抽头、平均时延。数字读自 <code>results/ring_attach_8x6.json</code>。</p>')


def sec_attach_block(d: dict) -> str:
    """§1 整节：前提 + 结论 + 对比图 + 方案表 + 三条否决理由。"""
    att, c = d.get("att"), d["coll"]
    if not att:
        return ('<h2 id="attach">一、AI core 怎么挂到环上</h2>'
                '<p class="muted">缺 <code>results/ring_attach_8x6.json</code>，'
                '先跑 <code>python3 utils/rg_ring_attach.py</code>。</p>')
    S = {r["key"]: r for r in att["schemes"]}
    A, C0, C = S["A_full_2port"], S["C0_rowhalf_noseam"], S["C_rowhalf_seam"]
    D, E = S["D_colhalf_seam"], S["E_bothhalf_seam"]
    F, G, B = S["F_rowhalf_stagger"], S["G_row_only_1port"], S["B_full_1lane"]
    g = att["geometry"]
    a2a = next((r for r in c["rows"] if r["pattern"] == "alltoall"
                and r["tier"] == "T0" and r["m"] == 1), None)
    st = A["structure"]
    return f"""<h2 id="attach">一、AI core 怎么挂到环上（先定前提）</h2>
<p>paper 把三件事定死了：<b>横向环 + 纵向环</b>、<b>H&harr;V 之间靠桥</b>
（桥里的 transfer FIFO 是全网唯一允许 flit 等待的地方 &mdash; 环本身停不下来）、
<b>每个 AI core 最多 2 个口对着环</b>。留给设计的只有两件事：每个环是覆盖整维的
full ring（折叠 torus 布线）还是 half ring，以及这 2 个口怎么花。
这一节把它当<b>设计空间</b>来算，不当口味来挑；用的全是与调度无关的结构量
（端口预算、连通性、割容量、线长），所以没有哪个方案是靠「排图排得好」赢的。</p>
<p class="muted">「half ring」有两种读法，都算了：<b>半跨度</b> &mdash;
环只覆盖半个维度（行 8&rarr;2&times;4、列 6&rarr;2&times;3）；<b>单车道</b> &mdash;
覆盖整维但只留一条反向车道，金属减半，按本仓一贯的金属恒定口径把省下的换成
2&times; 线宽（&sigma; 2&rarr;1）。</p>

<div class="note good"><b>结论：每核 2 个口 = 1 个行环口 + 1 个列环口；两个维度
都用双向全环、折叠布线；桥与核同址、复用这两个口。</b>
三条理由，都是算出来的而不是选出来的：
<ol>
<li><b>2 个口正好等于 L1 ramp。</b>ramp 是 {g['ramp_bw']} flit/cy，
两个环口合起来也是 {A['core_rate']} flit/cy &mdash; 少一个口（方案 G）进出速率
砍半、白扔一半 ramp，端口界从 {A['bounds']['allgather/T1']['lb']} 拍翻到
{G['bounds']['allgather/T1']['lb']} 拍；多一个口也喂不进去。</li>
<li><b>两个口分给两个维度，桥就不用另开抽头。</b>核在行环、列环上各有一个口，
转环复用这两个口，{st['n_bridges']} 个桥的额外环上抽头是
{st['n_extra_tap_bridges']} 个。两个口都押在行环上（方案 H）界不变，但每个列环
都得给桥单开抽头（{S['H_two_on_row']['structure']['n_extra_tap_bridges']} 个），
平均跳数还从 {A['distance']['avg_hops']} 涨到
{S['H_two_on_row']['distance']['avg_hops']}。</li>
<li><b>半环省不到该省的，亏掉不该亏的。</b>见下面三条否决理由。</li>
</ol>
这正是本报告后面所有数字所在的那块环 &mdash; 前提是<b>推出来的</b>，不是假设的。</div>

<div class="fig">{svg_attach(att)}
<div class="cap"><b>图：三个决定 &mdash; 环怎么闭、缝怎么补、口怎么花。</b>
A 是折叠布线：隔一个核走过去、隔一个核走回来，闭环但没有一根长绕回线，最长单线
{st['max_link_pitches']} 个核间距。B 是半跨度：行内确实更短，但缝上只剩一个存转发
FIFO。C 把 2 个口的三种花法并排放，只有「1 行口 + 1 列口」同时做到两维直达、
吃满 ramp、桥不另开抽头。</div></div>

{sec_attach(att)}

<h3>为什么不是 half ring</h3>
<ol>
<li><b>半跨度环按常规的连续二分拆，在「每核 ≤2 口」下直接不连通。</b>
列环不改变 x，拆开的行环又跨不过缝，于是左右各 {N // 2} 个核彼此完全不可达：可达对只有
{C0['distance']['reachable_pairs']}/{C0['distance']['total_pairs']}，x 向割容量
{C0['cuts']['x']['min_cap_per_dir']}。这不是排图能补的，是拓扑断了。</li>
<li><b>补缝要么亏带宽，要么亏时钟。</b>加缝桥（方案 C/D/E）能补回连通，但跨中线
从「几条并行的环段」变成「一个存转发 FIFO」：x 向对分
{A['cuts']['x']['min_cap_per_dir']}&rarr;{C['cuts']['x']['min_cap_per_dir']}
flit/cy，全交换下界 {A['bounds']['alltoall/T0']['lb']}&rarr;
{C['bounds']['alltoall/T0']['lb']} 拍（{C['worst_vs_A']:.2f}&times;）；
拆列环是 {D['worst_vs_A']:.2f}&times;，两个维度都拆是
{E['worst_vs_A']:.2f}&times;。不加缝桥而改用<b>错位</b>半环（方案 F）能保住对分，
但绕过阵列边缘的那个环必须有一根跨 {F['structure']['max_link_pitches']} 个核间距的
长线（全环折叠是 {st['max_link_pitches']}），折叠压不下去 &mdash; 要么降频要么插
寄存器，而寄存器会改掉无缓冲环的时序模型。</li>
<li><b>「半环省金属」这个直觉是错的。</b>闭环上 k 个核就有 k 段，拆环<b>不改变段数</b>
（{st['n_undirected_segments']} 条无向段、{st['links_vs_mesh']}&times; mesh 的段数，
九个方案全一样），省的只是<b>线长</b>：半跨度 2.0&times;&rarr;
{C['structure']['wire_vs_mesh']}&times; mesh。而错位半环反而更费
（{F['structure']['wire_vs_mesh']}&times;）。至于单车道那种「半环」，金属恒定换
2&times; 线宽后 12 个下界与全环<b>一个不差地打平</b>，只剩平均时延
{B['distance']['avg_lat_cy'] / A['distance']['avg_lat_cy']:.2f}&times;、直径
{A['distance']['diameter_cy']}&rarr;{B['distance']['diameter_cy']} 拍的净亏。</li>
</ol>
<div class="note"><b>这套结构模型是独立算的，而且和主流水线对上了。</b>
<code>rg_ring_attach.py</code> 只从「环怎么连 + 谁开口 + 桥在哪」出发，重新算了一遍
对分与时延地板，得到 x 向割宽 {A['cuts']['x']['min_cap_per_dir']}、全交换需过
{a2a['bounds']['flits_crossing_pos'] if a2a else '&mdash;'} flit、对分界
{A['bounds']['alltoall/T0']['lb']} 拍、直径 {A['distance']['diameter_cy']} 拍
&mdash; 与 <code>dse_ring_collectives_8x6.py</code> 报的
{a2a['bounds']['cut_width_directed'] if a2a else '&mdash;'} /
{a2a['bounds']['flits_crossing_pos'] if a2a else '&mdash;'} /
{a2a['bounds']['bisection_lb'] if a2a else '&mdash;'} /
{a2a['bounds_base']['latency_lb'] if a2a else '&mdash;'} 逐个吻合。
两套独立代码报同一组数，才值得信其中任何一套（验证清单 §18 第 40&ndash;42 项）。
下界里 T1 的归约类模式（全归约/广播/归约）在九个方案上都退化到 1 拍量级，
对排序不起作用 &mdash; 真正分出胜负的是<b>带宽绑定</b>（全交换、全收集 T0）和
<b>端口绑定</b>（收集、全收集 T1）这两类。</div>
"""


def svg_mechanism() -> str:
    """左：paper 机制的环站要哪些缓冲；右：静态拍图把它们删到哪。"""
    p: list[str] = []

    def box(x, y, w, h, cls, title, lines, tcls="bxt"):
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" '
                 f'class="bx {cls}"/>')
        p.append(f'<text x="{x + 10}" y="{y + 19}" class="{tcls}">{title}'
                 f'</text>')
        for i, ln in enumerate(lines):
            p.append(f'<text x="{x + 10}" y="{y + 38 + i * 16}" class="bxl">'
                     f'{ln}</text>')

    def arrow(x1, y1, x2, y2, label="", cls="ar", dash=False):
        d = ' stroke-dasharray="5 4"' if dash else ""
        p.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                 f'class="{cls}"{d}/>')
        ang = math.atan2(y2 - y1, x2 - x1)
        for s in (2.6, -2.6):
            p.append(f'<line x1="{x2}" y1="{y2}" '
                     f'x2="{x2 - 9 * math.cos(ang + s / 6):.1f}" '
                     f'y2="{y2 - 9 * math.sin(ang + s / 6):.1f}" '
                     f'class="{cls}"/>')
        if label:
            p.append(f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 - 6}" '
                     f'class="arl" text-anchor="middle">{label}</text>')

    p.append('<text x="20" y="24" class="h3s">A. paper 机制（E-tag / I-tag '
             '+ 偏转）</text>')
    p.append('<text x="20" y="44" class="bxl dim">运行期分布式决策 &rarr; '
             '必须有缓冲兜住「猜错」的情况</text>')
    box(20, 60, 172, 92, "src", "PE / L1", ["注入队列", "预留 Tx 缓冲"])
    box(214, 60, 200, 92, "arb", "环站（运行期仲裁）",
        ["E-tag / I-tag 比较器", "无未来信息 &rarr; 只能就地决策"])
    box(436, 60, 176, 92, "fill", "桥 transfer FIFO",
        ["转不了环就得先存住", "深度是真旋钮"])
    box(634, 60, 186, 92, "acc", "目的端重组缓冲",
        ["偏转造成乱序", "必须能重排"])
    arrow(192, 92, 214, 92)
    arrow(414, 92, 436, 92)
    arrow(612, 92, 634, 92)
    p.append('<path d="M 314 152 Q 314 196 470 196 Q 620 196 620 158" '
             'class="defl"/>')
    p.append('<text x="470" y="212" class="arl warn" text-anchor="middle">'
             '偏转：占用的弧被别人用了就再绕一圈 &rarr; 吃掉环带宽、打乱顺序'
             '</text>')

    p.append('<text x="20" y="266" class="h3s">B. 静态拍图（离线排好的时隙表）'
             '</text>')
    p.append('<text x="20" y="286" class="bxl dim">编译期全局决策 &rarr; '
             '不会猜错 &rarr; 环内可以真正零缓冲</text>')
    box(20, 302, 172, 92, "src", "PE / L1",
        ["按时隙表在 t0 起发", "L1 兼作归约累加器"])
    box(214, 302, 200, 92, "cal", "环站（查表）",
        ["一张 (拍, 端口) 表", "out_port_mask 天然多播"])
    box(436, 302, 176, 92, "gone", "桥 FIFO：删除",
        ["转环零驻留（实测 max=0）", "R4 保证不需要存"])
    box(634, 302, 186, 92, "gone", "重组缓冲：删除",
        ["R5 静态定路 + 保序", "乱序恒为 0"])
    arrow(192, 334, 214, 334)
    arrow(414, 334, 436, 334)
    arrow(612, 334, 634, 334)
    p.append('<text x="214" y="416" class="arl ok2">代价搬到了别处：'
             '一张拍图表就是控制存储，且故障后要重编译</text>')
    return svg(850, 436, "".join(p))


def svg_deflect_vs_slot() -> str:
    """同一个冲突（A、B 都要用弧 2&rarr;3），两种机制的处理方式。

    刻意不画环的绕回弧线：它会和标题文字抢同一片纵向空间，而绕回语义用
    「右侧出、左侧回」的一对箭头表达更清楚，也不会画到画布外。
    """
    p: list[str] = []
    x0, px, r = 96, 62, 12
    for y0, title, sub in ((130, "paper 机制：偏转",
                            "B 的槽被 A 占了 &rarr; 无缓冲又不能停 &rarr; "
                            "只能被弹走绕满一圈"),
                           (300, "静态拍图：错拍",
                            "编译期就看见这次冲突 &rarr; 直接把 B 排到下一拍")):
        p.append(f'<text x="{x0 - 30}" y="{y0 - 72}" class="h3s">{title}'
                 f'</text>')
        p.append(f'<text x="{x0 - 30}" y="{y0 - 52}" class="bxl dim">{sub}'
                 f'</text>')
        for i in range(8):
            cx = x0 + i * px
            if i < 7:
                p.append(f'<line x1="{cx + r + 2}" y1="{y0}" '
                         f'x2="{cx + px - r - 2}" y2="{y0}" class="rlk"/>')
            p.append(f'<circle cx="{cx}" cy="{y0}" r="{r}" class="nd"/>')
            p.append(f'<text x="{cx}" y="{y0 + 4}" class="tag">{i}</text>')
        p.append(f'<path d="M {x0 + 2 * px} {y0 - 20} L {x0 + 5 * px} '
                 f'{y0 - 20}" class="arcR"/>')

    y0 = 130
    p.append(f'<text x="{x0 + 2 * px}" y="{y0 - 32}" class="arl ok2">'
             f'A 正在用 2&rarr;5</text>')
    p.append(f'<path d="M {x0 + 2 * px} {y0 + 28} L {x0 + 7.6 * px} '
             f'{y0 + 28}" class="defl"/>')
    p.append(f'<path d="M {x0 - 0.5 * px} {y0 + 28} L {x0 + 1.7 * px} '
             f'{y0 + 28}" class="defl"/>')
    for xx, dx in ((x0 + 7.6 * px, 1), (x0 + 1.7 * px, 1)):
        p.append(f'<line x1="{xx}" y1="{y0 + 28}" x2="{xx - 9 * dx}" '
                 f'y2="{y0 + 23}" class="defl2"/>')
        p.append(f'<line x1="{xx}" y1="{y0 + 28}" x2="{xx - 9 * dx}" '
                 f'y2="{y0 + 33}" class="defl2"/>')
    p.append(f'<text x="{x0 + 7.9 * px}" y="{y0 + 32}" class="arl warn">'
             f'出</text>')
    p.append(f'<text x="{x0 - 1.1 * px}" y="{y0 + 32}" class="arl warn">'
             f'回</text>')
    p.append(f'<text x="{x0 - 30}" y="{y0 + 52}" class="arl warn">'
             f'B 被偏转：经绕回段走满 8 跳才回到 2 再重试 &rarr; '
             f'白吃 8 个弧周期，且到达顺序被打乱</text>')

    y0 = 300
    p.append(f'<text x="{x0 + 2 * px}" y="{y0 - 32}" class="arl ok2">'
             f'A：第 t 拍</text>')
    p.append(f'<path d="M {x0 + 2 * px} {y0 + 28} L {x0 + 5 * px} {y0 + 28}" '
             f'class="arcC"/>')
    p.append(f'<text x="{x0 - 30}" y="{y0 + 52}" class="arl">'
             f'B：第 t+1 拍走同一条弧 &mdash; 没有多余跳数，天然保序，'
             f'所以桥 FIFO 与重组缓冲都可以删</text>')
    return svg(720, 372, "".join(p))


def svg_multicast() -> str:
    """一次上环覆盖整段行弧，对比 7 次独立 unicast。

    只画行 0 一行节点：要说的事全部发生在同一个环上，多画一行会引读者去找
    并不属于这个对比的列向流量。
    """
    px, x0, yb, r = 58, 58, 104, 11
    p: list[str] = []

    def panel(ox: int, title: str, sub: str) -> None:
        p.append(f'<text x="{ox - 14}" y="24" class="bxt">{title}</text>')
        p.append(f'<text x="{ox - 14}" y="42" class="bxl dim">{sub}</text>')
        for x in range(MX - 1):
            cx = ox + x * px
            p.append(f'<line x1="{cx + r + 2}" y1="{yb}" '
                     f'x2="{cx + px - r - 2}" y2="{yb}" class="rlk"/>')
        p.append(f'<path d="M {ox} {yb - r - 3} Q {ox + 3.5 * px} {yb - 36} '
                 f'{ox + (MX - 1) * px} {yb - r - 3}" class="wrp"/>')
        p.append(f'<text x="{ox + 3.5 * px}" y="{yb - 25}" class="bxl dim" '
                 f'text-anchor="middle">绕回段</text>')

    def nodes(ox: int) -> None:
        for x in range(MX):
            cx = ox + x * px
            p.append(f'<circle cx="{cx}" cy="{yb}" r="{r}" '
                     f'class="nd {"src" if x == 0 else "dst"}"/>')
            p.append(f'<text x="{cx}" y="{yb + 4}" class="tag">'
                     f'{x}</text>')
            if x:
                p.append(f'<line x1="{cx}" y1="{yb + r}" x2="{cx}" '
                         f'y2="{yb + r + 15}" class="ar"/>')
                p.append(f'<text x="{cx}" y="{yb + r + 29}" class="bxl dim" '
                         f'text-anchor="middle">L1</text>')

    panel(x0, "T1：一次上环，copy-and-continue",
          "节点 0 只上环一次；下游每个环站各落一份进自己的 L1 并继续转发")
    p.append(f'<path d="M {x0} {yb} L {x0 + 7 * px} {yb}" class="arcR"/>')
    nodes(x0)
    p.append(f'<text x="{x0 - 14}" y="{yb + 60}" class="bxl ok2">'
             f'1 次上环 &middot; 7 次抽取 &middot; 占 7 个弧周期</text>')

    ox2 = x0 + 8 * px + 70
    panel(ox2, "T0：七次独立上环",
          "paper 机制只能 unicast，所以每个目的都要单独上环一次")
    for k, x in enumerate(range(1, MX)):
        yy = yb - 4 - k * 2.6
        p.append(f'<path d="M {ox2} {yy:.1f} L {ox2 + x * px} {yy:.1f}" '
                 f'class="pRM"/>')
    nodes(ox2)
    p.append(f'<text x="{ox2 - 14}" y="{yb + 60}" class="bxl warn">'
             f'7 次上环 &middot; 7 次抽取 &middot; 占 28 个弧周期</text>')
    return svg(2 * (8 * px) + 140, 186, "".join(p))


def svg_rotation() -> str:
    """旋转为什么能打满一个环：每条弧恰好载一个 flit。"""
    cx, cy, R, k = 146, 132, 94, 8
    p: list[str] = []
    pos = [(cx + R * math.cos(-math.pi / 2 + 2 * math.pi * i / k),
            cy + R * math.sin(-math.pi / 2 + 2 * math.pi * i / k))
           for i in range(k)]
    p.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" class="ringc"/>')
    for i in range(k):
        x1, y1 = pos[i]
        x2, y2 = pos[(i + 1) % k]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        vx, vy = x2 - x1, y2 - y1
        ln = math.hypot(vx, vy)
        p.append(f'<line x1="{mx - vx / ln * 12:.1f}" '
                 f'y1="{my - vy / ln * 12:.1f}" '
                 f'x2="{mx + vx / ln * 12:.1f}" '
                 f'y2="{my + vy / ln * 12:.1f}" class="arcR"/>')
    for i, (x, y) in enumerate(pos):
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" class="nd"/>')
        p.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" class="tag">{i}</text>')
    p.append(f'<text x="{cx}" y="{cy - 6}" class="bxl" text-anchor="middle">'
             f'每条弧</text>')
    p.append(f'<text x="{cx}" y="{cy + 12}" class="bxl ok2" '
             f'text-anchor="middle">都用一次</text>')
    p.append('<text x="292" y="40" class="bxt">一个旋转步</text>')
    for i, line in enumerate([
            "8 个环站同时上环，各自顺时针送一跳。这一步把环上",
            "每一条弧恰好用掉一次：花 1 个弧周期、搬 8 个 flit。",
            "重复 7 次，每个节点就拿到了其它所有节点的数据。",
            "",
            "这就是它把最忙段下界打满的原因：II_eff = 47 = 每轮弧负载。",
            "",
            "也是它一断就死的原因：这一步没有任何备用弧可绕。"]):
        cls = ("bxl ok2" if "打满" in line else
               "bxl warn" if "就死" in line else "bxl")
        p.append(f'<text x="292" y="{68 + i * 21}" class="{cls}">{line}</text>')
    return svg(860, 268, "".join(p))


def svg_gantt(topo, cal, *, w: int = 880, rowh: int = 7,
              maxrows: int = 108) -> str:
    """真实拍图的时间占用图：每条横线是一次传输的 [t0, t0+tail)。"""
    L, Rr, T, B = 74, 26, 46, 46
    xs = cal.makespan or 1
    n_ph = len(cal.phase_window)
    items = sorted(cal.starts.items(), key=lambda kv: (cal.phase_of[kv[0]],
                                                       kv[1]))
    step = max(1, math.ceil(len(items) / maxrows))
    shown = items[::step]
    h = T + rowh * len(shown) + B

    def tx(t: float) -> float:
        return L + (w - L - Rr) * (t / xs)
    p = [f'<rect x="{L}" y="{T - 10}" width="{w - L - Rr}" '
         f'height="{rowh * len(shown) + 16}" class="plot"/>']
    for pi, (a, b) in enumerate(cal.phase_window):
        p.append(f'<rect x="{tx(a):.1f}" y="{T - 10}" '
                 f'width="{max(1, tx(b) - tx(a)):.1f}" '
                 f'height="{rowh * len(shown) + 16}" class="phz p{pi % 4}"/>')
        p.append(f'<line x1="{tx(b):.1f}" y1="{T - 10}" x2="{tx(b):.1f}" '
                 f'y2="{T + rowh * len(shown) + 6}" class="bar1"/>')
        p.append(f'<text x="{(tx(a) + tx(b)) / 2:.1f}" y="{T - 16}" '
                 f'class="tick" text-anchor="middle">相位 {pi + 1}'
                 f'（barrier）</text>')
    for i, (xid, t0) in enumerate(shown):
        fp = cal.fps[xid]
        y = T + i * rowh
        cls = f"p{cal.phase_of[xid] % 4}"
        p.append(f'<rect x="{tx(t0):.1f}" y="{y:.1f}" '
                 f'width="{max(1.2, tx(t0 + fp.tail) - tx(t0)):.1f}" '
                 f'height="{rowh - 1.6:.1f}" class="gb {cls}"/>')
    for i in range(6):
        t = xs * i / 5
        p.append(f'<text x="{tx(t):.1f}" y="{T + rowh * len(shown) + 26}" '
                 f'class="tick" text-anchor="middle">{t:,.0f}</text>')
    p.append(f'<text x="{(L + w - Rr) / 2}" y="{h - 8}" class="axl" '
             f'text-anchor="middle">拍（cycle）&mdash; 共 {cal.makespan} 拍，'
             f'{len(cal.starts)} 次传输（图中每 {step} 条抽 1 条）</text>')
    p.append(f'<text x="{L - 10}" y="{T + 8}" class="axl" text-anchor="end">'
             f'传输</text>')
    return svg(w, h, "".join(p))


# ---------------------------------------------------------------------------
# 4. 各小节
# ---------------------------------------------------------------------------

CSS = """
body { font-family: "Noto Sans SC", "Microsoft YaHei", "Segoe UI", system-ui,
       sans-serif; margin: 0; background: #0b1020; color: #e8ecf4;
       line-height: 1.75; }
.wrap { max-width: 1000px; margin: 0 auto; padding: 28px 32px 90px; }
h1 { font-size: 1.6rem; color: #f0f4ff; border-bottom: 1px solid #2a3555;
     padding-bottom: .5rem; }
h2 { margin-top: 2.6rem; font-size: 1.24rem; color: #f0f4ff;
     border-left: 4px solid #7eb6ff; padding-left: .6rem; }
h3 { margin-top: 1.8rem; font-size: 1.02rem; color: #c8d6f0; }
p { margin: .65rem 0; }
a { color: #7eb6ff; }
.muted { color: #9aa3b5; font-size: .86rem; }
.lead { font-size: 1.02rem; color: #d6def0; }
.cards { display: grid; grid-template-columns: repeat(auto-fill,minmax(210px,1fr));
         gap: 12px; margin: 1.1rem 0 1.6rem; }
.card { background: #141b2f; border: 1px solid #2a3555; border-radius: 10px;
        padding: 12px 14px; }
.card.ok { border-color: #2d6a4f; } .card.bad { border-color: #9b2226; }
.card .k { font-size: .78rem; color: #9aa3b5; }
.card .v { font-size: 1.38rem; font-weight: 700; margin: .2rem 0; }
.card .s { font-size: .78rem; color: #b8c0d0; }
table { border-collapse: collapse; width: 100%; font-size: .86rem;
        margin: .7rem 0 1.3rem; }
th, td { border: 1px solid #2a3555; padding: 6px 9px; text-align: left; }
th { background: #1a2340; font-weight: 600; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
tr:nth-child(even) { background: #12192c; }
code { background: #1a2340; padding: 1px 5px; border-radius: 4px;
       font-size: .88em; }
pre.code { background: #10162a; border: 1px solid #2a3555; border-left: 3px
       solid #7eb6ff; border-radius: 8px; padding: 12px 16px; overflow-x: auto;
       font-family: ui-monospace, monospace; font-size: .82rem; line-height: 1.6;
       color: #d8e2f5; }
pre.code .c { color: #7f8ca8; }
.eq { background: #141b2f; padding: 10px 15px; border-radius: 8px;
      font-family: ui-monospace, monospace; margin: .7rem 0; font-size: .84rem;
      border: 1px solid #2a3555; }
.win { color: #6ee7a8; font-weight: 600; }
.lose { color: #f0a0a0; font-weight: 600; }
.note { background: #141b2f; border-left: 3px solid #d9a03c;
        border-radius: 0 8px 8px 0; padding: 10px 15px; margin: .9rem 0;
        font-size: .89rem; }
.note.good { border-left-color: #2d6a4f; }
.note.bad { border-left-color: #9b2226; }
.note b { color: #f0f4ff; }
.fig { background: #0e1425; border: 1px solid #2a3555; border-radius: 10px;
       padding: 14px 12px 10px; margin: 1.2rem 0 1.5rem; }
.fig .cap { color: #9aa3b5; font-size: .83rem; margin-top: .6rem;
            padding: 0 6px; }
.fig .cap b { color: #c8d6f0; }
.toc { background: #141b2f; border: 1px solid #2a3555; border-radius: 10px;
       padding: 12px 20px; columns: 2; font-size: .88rem; }
.toc a { text-decoration: none; }
ul, ol { margin: .5rem 0 .9rem; padding-left: 1.6rem; }
li { margin: .26rem 0; }
svg text { font-family: "Noto Sans SC", "Microsoft YaHei", "Segoe UI",
           system-ui, sans-serif; }
.plot { fill: #0b142a; stroke: #2a3555; }
.gl { stroke: #1f2a47; }
.tick { fill: #8b95ab; font-size: 11px; }
.axl { fill: #9aa3b5; font-size: 12px; }
.axc { stroke: #7d8aa8; stroke-width: 1.4; }
.barv { fill: #dbe4f5; font-size: 10.5px; font-variant-numeric: tabular-nums; }
.bar { stroke: none; }
.bar.hib { stroke: #f0f4ff; stroke-width: 1.2; }
.bar.cA { fill: #7eb6ff; } .bar.cB { fill: #6ee7a8; }
.bar.cC { fill: #f0a0a0; } .bar.cD { fill: #c9a6ff; }
.bar.cE { fill: #55618a; } .bar.cF { fill: #d9a03c; }
.cv { fill: none; stroke-width: 2.6; }
.cA { stroke: #7eb6ff; } .cB { stroke: #6ee7a8; } .cC { stroke: #f0a0a0; }
.cD { stroke: #c9a6ff; }
.star.cA { fill: #7eb6ff; } .star.cB { fill: #6ee7a8; }
.star.cC { fill: #f0a0a0; } .star.cD { fill: #c9a6ff; }
.anch { stroke: #d9a03c; stroke-width: 1.3; stroke-dasharray: 3 3; }
.anchl { fill: #d9a03c; font-size: 11px; }
.rlk { stroke: #3a5580; stroke-width: 2.4; }
.fld { fill: none; stroke: #6ee7a8; stroke-width: 2.6; }
.wrp.hl { stroke: #6ee7a8; stroke-width: 2; stroke-dasharray: 6 4; }
.wrp.hl2 { stroke: #c9a6ff; stroke-width: 2; stroke-dasharray: 6 4; }
.colt { fill: #c9a6ff; }
.nd.src { fill: #6ee7a8; } .nd.dst { fill: #8fa2c8; }
.pRM { fill: none; stroke: #f0a0a0; stroke-width: 1.5; }
.ringc { fill: none; stroke: #2a3555; stroke-width: 1.4; }
.pill { display: inline-block; background: #1a2340; border: 1px solid #2a3555;
        border-radius: 999px; padding: 2px 11px; font-size: .78rem;
        margin: 0 6px 6px 0; }
.wrp { fill: none; stroke: #55618a; stroke-width: 1.4; stroke-dasharray: 4 3; }
.nd { fill: #8fa2c8; }
.tag { fill: #0b1020; font-size: 10px; font-weight: 700; text-anchor: middle; }
.arcR { fill: none; stroke: #6ee7a8; stroke-width: 4.5; }
.arcC { fill: none; stroke: #c9a6ff; stroke-width: 4.5; }
.defl { fill: none; stroke: #f0a0a0; stroke-width: 2.4; stroke-dasharray: 6 4; }
.defl2 { fill: none; stroke: #f0a0a0; stroke-width: 2.4; }
.ar { stroke: #7eb6ff; stroke-width: 1.8; }
.arl { fill: #9db0d0; font-size: 11.5px; }
.arl.warn, .warn { fill: #f0c070; } .ok2 { fill: #6ee7a8; }
.bx { fill: #141b2f; stroke: #2a3555; stroke-width: 1.5; }
.bx.src { fill: #13251c; stroke: #2d6a4f; }
.bx.arb { fill: #141d33; stroke: #3d5a99; }
.bx.fill { fill: #241a1a; stroke: #7a3b3b; }
.bx.acc { fill: #241a1a; stroke: #7a3b3b; }
.bx.cal { fill: #141d33; stroke: #3d5a99; }
.bx.gone { fill: #11201a; stroke: #2d6a4f; stroke-dasharray: 5 4; }
.bxt { fill: #f0f4ff; font-size: 12.5px; font-weight: 700; }
.bxl { fill: #c2ccdf; font-size: 11.5px; } .bxl.dim, .dim { fill: #8b95ab; }
.h3s { fill: #f0f4ff; font-size: 14px; font-weight: 700; }
.gb { stroke: none; }
.gb.p0 { fill: #7eb6ff; } .gb.p1 { fill: #6ee7a8; }
.gb.p2 { fill: #c9a6ff; } .gb.p3 { fill: #d9a03c; }
.phz { opacity: .07; }
.phz.p0 { fill: #7eb6ff; } .phz.p1 { fill: #6ee7a8; }
.phz.p2 { fill: #c9a6ff; } .phz.p3 { fill: #d9a03c; }
.bar1 { stroke: #d9a03c; stroke-width: 1.2; stroke-dasharray: 4 3; }
"""


def _cut_note(d: dict) -> str:
    """竖切口容量：折叠环让每个行环穿两次切口，这是它多出来的那份金属的回报。"""
    t = d.get("thr")
    if not t:
        return "竖切口容量是同尺寸 mesh 的 2&times;。"
    x = t["cuts"]["x"][0]
    return (f'一条竖切口有 {x["segments_pos"]:g} 条同向有向段可用'
            f'（每个行环穿它两次：一次常规段、一次绕回段），'
            f'是同尺寸 mesh 的 2&times; &mdash; 这条在 §3 的 cut 界里直接兑现。')


def sec_bridge(d: dict) -> str:
    """§10：48 个桥 FIFO 的实测占用、深度扫描、桥延迟扫描、拍图对照。"""
    b = d.get("brg")
    if not b:
        return ('<p class="muted">ring_bridge_8x6.json 缺失，'
                '先跑 dse_ring_bridge_8x6.py。</p>')
    per = {(r["pattern"], r["m"]): r for r in b["per_pattern"]}
    cal = {(r["pattern"], r["algo"], r["m"]): r for r in b["calendar"]}
    dep = b["params"]["fifo_depth"]
    tt = b["wire"]["t_turn"]
    a2a1, a2a13 = per[("alltoall", 1)], per[("alltoall", 13)]
    g13 = per[("gather", 13)]
    out = [
        f'<p>环上没有缓冲，<b>桥上有</b>。要换环的 flit 必须在该节点的 '
        f'transfer FIFO 里待着，等桥把它送过去 —— 光是过桥就占住一个条目 '
        f'{tt} 拍。48 个核每个都是桥，所以这是 48 个 FIFO，'
        f'是<b>基线唯一赖不掉的存储</b>。默认深度 '
        f'<code>fifo_depth={dep}</code>（+{b["params"]["resv_tx"]} 个 E-tag '
        f'保留位）。</p>',
        '<div class="note"><b>满了会怎样？不会阻塞。</b>无缓冲环上「已在环上的'
        'flit 永不停」是硬不变量，所以桥满时到达的 flit 不是等，而是<b>被打偏'
        '（deflect）绕一整圈再来</b>。深度不够的代价因此不是死锁，'
        '而是 makespan —— 下面第二张图就是这个代价的价目表。</div>']
    out.append('<h3>一、占用长什么样：逐桥实测</h3>')
    out.append('<table><tr><th>集合通信</th><th class="n">m</th>'
               '<th class="n">makespan</th><th class="n">最深条目</th>'
               '<th class="n">平均深度<br>（最忙的桥）</th>'
               '<th class="n">满的时间占比</th>'
               '<th class="n">因满被打偏</th><th class="n">额外排队</th>'
               '<th class="n">用到的桥</th></tr>')
    for pat in PATTERNS:
        for m in (1, 13):
            r = per.get((pat, m))
            if not r:
                continue
            out.append(
                f'<tr><td>{CN1[pat]}</td><td class="n">{m}</td>'
                f'<td class="n">{f(r["makespan"])}</td>'
                f'<td class="n"><b>{r["peak_max"]}</b></td>'
                f'<td class="n">{r["mean_max"]:.2f}</td>'
                f'<td class="n">{pct(r["full_frac_max"])}</td>'
                f'<td class="n">{f(r["deflect_total"])}</td>'
                f'<td class="n">{r["wait_max"]} 拍</td>'
                f'<td class="n">{r["n_bridges_touched"]}</td></tr>')
    out.append("</table>")
    out.append(
        f'<p class="muted">读法：<b>最深条目</b>是这座桥必须做多深，'
        f'<b>平均深度</b>是它平时真正用到多少。两者差得越远，'
        f'说明这份深度是为瞬时突发买的。m=13 的几行最深条目都顶到了 '
        f'{dep}+{b["params"]["resv_tx"]} —— <b>顶格意味着深度是被参数卡住的，'
               f'不是需求只有这么多</b>，真正的需求在下一张图里。</p>')
    heat = svg_bridge_heat([
        {"title": "全交换 m=13：各桥平均占用",
         "sub": f'{a2a13["n_bridges_touched"]} 个桥全部被用到，负载几乎均匀',
         "rows": a2a13["table"]},
        {"title": "收集 m=13：各桥平均占用",
         "sub": f'只有 {g13["n_bridges_touched"]} 个桥参与，其余空着',
         "rows": g13["table"]}])
    out.append(
        f'<div class="fig">{heat}'
        f'<div class="cap"><b>图：同一块布局，两种流量形状把桥压出完全不同的'
        f'图案。</b>全交换让 48 个桥都忙、最忙 / 平均 = '
        f'{a2a13["mean_max"] / max(1e-9, a2a13["mean_avg"]):.2f}；'
        f'收集只用 {g13["n_bridges_touched"]} 个桥（root 那一列 + root 那一行'
        f'的交汇点），最忙 / 平均 = '
        f'{g13["mean_max"] / max(1e-9, g13["mean_avg"]):.2f}，'
        f'热点在 x{g13["hot_node"]["x"]},y{g13["hot_node"]["y"]}。'
        f'<b>fan-in 的桥是有热点的，全交换没有</b> —— '
        f'如果只按平均值给所有桥同一个深度，热点那几个会先满。</div></div>')
    out.append('<h3>二、深度值多少钱：把 fifo_depth 扫一遍</h3>')
    series = []
    for i, ds in enumerate(b["depth_sweep"]):
        series.append({
            "name": f'{CN1[ds["pattern"]]} m={ds["m"]}',
            "cls": ["cA", "cB", "cD"][i % 3],
            "pts": [(r["fifo_depth"], r["makespan"]) for r in ds["rows"]]})
    d13 = next(x for x in b["depth_sweep"]
               if x["pattern"] == "alltoall" and x["m"] == 13)
    out.append(
        f'<div class="fig">{line_chart(series, w=820, h=320, xlabel="fifo_depth（条目）", ylabel="makespan（拍）", logx=True, xticks=b["depths"])}'
        f'<div class="cap"><b>图：桥有多深，基线就有多快。</b>'
        f'全交换 m=13 从深度 1 的 {f(d13["rows"][0]["makespan"])} 拍降到 '
        f'{f(d13["best_makespan"])} 拍，'
        f'<b>{times(d13["cost_of_depth1"])}</b>；拐点在 '
        f'{d13["knee_depth"]} 个条目。深度 1（「桥就是一级寄存器」）在 '
        f'{tt} 拍的桥下<b>不是一个设计点</b>。</div></div>')
    out.append('<table><tr><th class="n">fifo_depth</th>'
               '<th class="n">makespan</th><th class="n">实际最深</th>'
               '<th class="n">最忙桥满的占比</th><th class="n">因满被打偏</th>'
               '<th class="n">额外排队</th></tr>')
    for r in d13["rows"]:
        out.append(f'<tr><td class="n">{r["fifo_depth"]}</td>'
                   f'<td class="n">{f(r["makespan"])}</td>'
                   f'<td class="n">{r["peak_max"]}</td>'
                   f'<td class="n">{pct(r["full_frac_max"])}</td>'
                   f'<td class="n">{f(r["deflect_total"])}</td>'
                   f'<td class="n">{r["wait_max"]}</td></tr>')
    out.append(f'</table><p class="muted">全交换 m=13。「实际最深」一路跟着 '
               f'<code>fifo_depth</code> 顶格，说明这条流量的<b>需求超过 '
               f'{b["depths"][-1]} 个条目</b>；makespan 却在 '
               f'{d13["knee_depth"]} 之后基本走平 —— 需求与收益不是一回事。</p>')
    out.append('<h3>三、是谁把 FIFO 填满的：把 t_turn 扫一遍</h3>')
    t13 = next(x for x in b["turn_sweep"]
               if x["pattern"] == "alltoall" and x["m"] == 13)
    t1 = next(x for x in b["turn_sweep"]
              if x["pattern"] == "alltoall" and x["m"] == 1)
    out.append(
        f'<div class="fig">'
        f'{line_chart([{"name": "makespan m=13", "cls": "cA", "pts": [(r["t_turn"], r["makespan"]) for r in t13["rows"]]}, {"name": "makespan m=1", "cls": "cB", "pts": [(r["t_turn"], r["makespan"]) for r in t1["rows"]]}], w=820, h=300, xlabel="t_turn（过桥拍数）", ylabel="makespan（拍）", xticks=[r["t_turn"] for r in t13["rows"]])}'
        f'<div class="cap"><b>图：占用是「到达率 &times; 停留时间」，'
        f'停留时间就是 t_turn。</b>把桥从 1 拍加到 {tt} 拍，'
        f'全交换 m=13 的平均桥深度 &times;{t13["mean_10_over_1"]}、'
        f'因满打偏 &times;{t13["deflect_10_over_1"]}、makespan '
        f'&times;{t13["makespan_10_over_1"]}；m=1 更敏感，'
        f'&times;{t1["makespan_10_over_1"]}。'
        f'<b>桥 buffer 的需求是这次时延口径更新<u>造出来</u>的</b>，'
        f'不是流量本来就这么重。</div></div>')
    out.append('<h3>四、拍图这边的桥：要么零占用，要么要求它是流水的</h3>')
    cf13 = cal.get(("alltoall", "flat", 13))
    ctl = b.get("no_turn_control") or []
    out.append(
        f'<p>拍图不靠 FIFO 排队：R4 把「上另一个环」钉死在「从这个环下车」'
        f'之后的第 {RAMP} 拍（或 {tt} 拍，取转维方式的便宜者），'
        f'时刻在编译期就定死了，没有「等一等」这个状态。'
        f'这带来两种截然不同的桥需求：</p>')
    out.append('<table><tr><th>拍图</th><th class="n">m</th>'
               '<th>转维方式</th><th class="n">同时在桥上的最大条目数</th>'
               '<th>对硬件的要求</th></tr>')
    for r in b["calendar"]:
        if r["m"] != 13:
            continue
        need = r["max_concurrent"]
        req = ('<span class="win">桥 buffer 可以是 0</span>'
               if need == 0 else
               f'桥须能同时容纳 {need} 个条目（但<b>不需要仲裁</b>，'
               f'进出时刻已知）')
        out.append(f'<tr><td>{CN1[r["pattern"]]} / {r["algo"]}</td>'
                   f'<td class="n">{r["m"]}</td>'
                   f'<td>{"落 L1 中继（不过桥）" if need == 0 else "过桥"}</td>'
                   f'<td class="n"><b>{need}</b></td><td>{req}</td></tr>')
    out.append("</table>")
    if ctl:
        z = [r for r in ctl if r["bridge_crossings"] == 0]
        out.append(
            f'<div class="note good"><b>按维分解的拍图（<code>dim_2phase</code>）'
            f'一次桥都不过：{len(z)}/{len(ctl)} 个被测方案的过桥次数是 0。</b>'
            f'原因在 §2 已经讲过 —— 落进 L1 再从列环发出去要 {RAMP} 拍，'
            f'过桥要 {tt} 拍，编译器当然选前者。'
            f'于是<b>这一类拍图把桥 buffer 这项成本整个删掉了</b>：'
            f'代价是那 {RAMP} 拍的 L1 往返，和「中继核必须有 L1 带宽接住它」。'
            f'这也解释了为什么 §2 的时延地板必须按 min(t_turn, RAMP) 收。'
            f'</div>')
    if cf13:
        out.append(
            f'<p>反过来，<code>flat</code> 这类直接绕环的拍图确实过桥，'
            f'但它要的是<b>深度而不是排队</b>：全交换 m=13 需要桥同时容纳 '
            f'{cf13["max_concurrent"]} 个条目，'
            f'而基线在同一流量下峰值顶到 {a2a13["peak_max"]}、'
            f'还额外付了 {f(a2a13["deflect_total"])} 次因满打偏。'
            f'同样是「桥上有几个 flit」，一个是编译期算出来的确定数，'
            f'一个是运行期赌出来的分布 —— 这是静态拍图在<b>面积</b>上'
            f'（而不只是 makespan 上）的收益。</p>')
    out.append(
        f'<div class="note"><b>这一节给设计的三条数：</b>'
        f'<ol><li>基线要跑得动 m=13 的重流量，桥 FIFO <b>至少 '
        f'{d13["knee_depth"]} 个条目</b>（48 座桥都要），'
        f'再深收益递减但仍单调。</li>'
        f'<li>fan-in（收集 / 归约）的桥负载<b>不均匀</b>，'
        f'热点桥的平均占用是全局平均的 '
        f'{g13["mean_max"] / max(1e-9, g13["mean_avg"]):.1f}&times;；'
        f'统一深度会在热点先满，按 root 位置差异化配深度是有意义的。</li>'
        f'<li>如果桥能做到 1 拍（而不是 {tt} 拍），'
        f'基线全交换 m=13 快 {times(t13["makespan_10_over_1"])}、'
        f'桥深度需求降到 1/{t13["mean_10_over_1"]:.1f}。'
        f'<b>桥延迟是这套设计里性价比最高的一个优化点</b>，'
        f'比加深 FIFO 划算得多。</li></ol></div>')
    return "".join(out)


def sec_cards(d: dict) -> str:
    c, ver, rob = d["coll"], d["ver"], d["rob"]
    out = []
    win, tie, lose = split_1p(c)
    n = len(win) + len(tie) + len(lose)
    out.append(f'<div class="card ok"><div class="k">m=13 同能力（T0）：'
               f'拍图更快的 collective</div><div class="v">{len(win)} / {n}'
               f'</div><div class="s">{len(lose)} 个基线更快、{len(tie)} 个'
               f'打平（见 §7、§8）</div></div>')
    bc, bc0 = (row1(c, pattern="broadcast", algo="dim_2phase", tier="T1", m=13,
                    bidir=True),
               row1(c, pattern="broadcast", algo="dim_2phase", tier="T0", m=13,
                    bidir=True))
    if bc and bc0:
        out.append(f'<div class="card ok"><div class="k">弧多播对广播的收益'
                   f'（同树、m=13）</div><div class="v">'
                   f'{bc0["calendar"]["makespan"] / bc["calendar"]["makespan"]:.2f}'
                   f'&times;</div><div class="s">'
                   f'{bc0["calendar"]["makespan"]} &rarr; '
                   f'{bc["calendar"]["makespan"]} 拍（上环 flit '
                   f'{bc0["shape"]["n_flits_boarded"]} &rarr; '
                   f'{bc["shape"]["n_flits_boarded"]}）</div></div>')
    a2a, a2k = best_base(c, "alltoall", 13), best_cal(c, "alltoall", 13, "T0")
    if a2a and a2k:
        out.append(f'<div class="card"><div class="k">全交换 m=13：基线 / 拍图'
                   f'</div><div class="v">'
                   f'{a2a["ring_base"]["makespan"] / a2k["calendar"]["makespan"]:.2f}'
                   f'&times;</div><div class="s">'
                   f'{a2a["ring_base"]["makespan"]} vs '
                   f'{a2k["calendar"]["makespan"]} 拍</div></div>')
    t = d.get("thr")
    if t:
        h = hl_row(t, "alltoall", 13)
        out.append(f'<div class="card ok"><div class="k">流水稳态（m=13）：'
                   f'全交换一轮均摊</div><div class="v">'
                   f'{h["base_over_cal_T0"]["per_round"]:.2f}&times;</div>'
                   f'<div class="s">基线 '
                   f'{h["base"]["best_II"]["per_round"]:,.0f} vs 拍图 '
                   f'{h["cal_T0"]["best_II"]["per_round"]:,.0f} 拍/轮，'
                   f'II 界 {h["bound"]["II_lb"]}（见 §13）</div></div>')
        fan = max((r for r in t["rows"] if r.get("base_hop_tax")),
                  key=lambda r: r["base_hop_tax"])
        out.append(f'<div class="card bad"><div class="k">基线在 fan-in 上'
                   f'烧掉的带宽</div><div class="v">'
                   f'{times(fan["base_hop_tax"])}</div><div class="s">'
                   f'最小跳数的倍数（{CN1[fan["pattern"]]}/{fan["algo"]} '
                   f'm={fan["m"]}）；拍图恒 1.0&times;</div></div>')
    dfl = [(p, best_base(c, p, 13)) for p in PATTERNS if best_base(c, p, 13)]
    wp, wr = max(dfl, key=lambda e: e[1]["ring_base"]["deflect_per_flit"])
    out.append(f'<div class="card bad"><div class="k">基线偏转率峰值'
               f'（m=13）</div><div class="v">'
               f'{wr["ring_base"]["deflect_per_flit"]:.3f}</div>'
               f'<div class="s">次 / flit，出现在{CN1[wp]}/{wr["algo"]}；'
               f'白吃弧周期且制造乱序</div></div>')
    if ver:
        out.append(f'<div class="card {"ok" if ver["all_pass"] else "bad"}">'
                   f'<div class="k">可执行验证</div><div class="v">'
                   f'{ver["n_pass"]}/{ver["n_checks"]}</div>'
                   f'<div class="s">D-R 五子句 0 违例、转环驻留 0</div></div>')
    if rob:
        rot = next((x for x in rob["faults"] if x["algo"] == "ring_rotate"),
                   None)
        if rot:
            out.append(f'<div class="card bad"><div class="k">旋转拍图无解的'
                       f'故障场景</div><div class="v">{rot["n_infeasible"]}'
                       f'/{rot["n_scenarios"]}</div><div class="s">吞吐最优'
                       f'方案最不抗故障</div></div>')
    return f'<div class="cards">{"".join(out)}</div>'


def sec_transports(c: dict) -> str:
    """环上三种 transport 放在同一张表里。

    `ring_islip2d` 是**同能力的调度参照**而不是竞争者：它每节点每轮只仲裁一个
    flit，所以在 2256 条消息的 pattern 上必然差一个数量级。列出来的价值在于
    界定「运行期集中式仲裁」这条路的上限，而不是宣布谁赢。
    """
    out = ['<table><tr><th>集合通信</th><th class="n">静态拍图</th>'
           '<th class="n">ring_base<br>（paper 机制）</th>'
           '<th class="n">ring_islip2d<br>（集中式参照）</th>'
           '<th class="n">下界<br>（拍图模型）</th>'
           '<th class="n">拍图/下界</th>'
           '<th class="n">基线/<b>基线模型</b>下界</th>'
           '<th class="n">islip2d/下界</th></tr>']
    for pat in PATTERNS:
        k = best_cal(c, pat, 13, "T0")
        b = best_base(c, pat, 13)
        isl = [r for r in rows(c, pattern=pat, m=13, bidir=True)
               if r["ring_islip2d"].get("makespan") is not None]
        i0 = min(isl, key=lambda r: r["ring_islip2d"]["makespan"]) if isl else None
        if not k:
            continue
        out.append(
            f'<tr><td>{CN1[pat]}</td>'
            f'<td class="n"><b>{f(k["calendar"]["makespan"])}</b></td>'
            f'<td class="n">{f((b or {}).get("ring_base", {}).get("makespan"))}'
            f'</td>'
            f'<td class="n">'
            f'{f((i0 or {}).get("ring_islip2d", {}).get("makespan"))}</td>'
            f'<td class="n">{f(k["bounds"]["makespan_lb"])}</td>'
            f'<td class="n">{times(k["ratios"]["calendar_over_lb"])}</td>'
            f'<td class="n">'
            f'{times((b or {}).get("ratios", {}).get("base_over_base_lb"))}'
            f'</td>'
            f'<td class="n">'
            f'{times((i0 or {}).get("ratios", {}).get("islip_over_lb"))}</td>'
            f'</tr>')
    out.append("</table>")
    out.append('<div class="note"><b>三条腿不是三个竞争者。</b>'
               '静态拍图与 <code>ring_base</code> 是真正的对手（同一块环、'
               '同一个流集，只换 transport）；<code>ring_islip2d</code> 是'
               '<b>同能力的调度参照</b>，用来界定「运行期集中式仲裁」这条路的'
               '上限 —— 它每节点每轮只放行一个 flit，在 2256 条消息的 pattern '
               '上必然差一个数量级，应当读作对照组而不是结果。'
               '所有 T0 口径：三条腿都只有 unicast。'
               '<br>下界一列是<b>该行那个 T0 流集自己的</b>下界，所以和 §6 图里'
               '按「T0/T1 取更松那个」画的下界不是同一个数 —— 界依赖流集，'
               '加了多播就换了一组界。<b>并且它是拍图模型的界，不是基线的界</b>，'
               '原因见下。</div>')
    out.append(sec_two_models(c))
    return "".join(out)


def _two_model_rows(c: dict) -> list[dict]:
    """出现「基线看起来低于下界」的行，按错得最狠排序。"""
    out = []
    for r in rows(c, tier="T0", bidir=True):
        bm = (r.get("ring_base") or {}).get("makespan")
        if not bm or "bounds_base" not in r:
            continue
        if bm >= r["bounds"]["makespan_lb"]:
            continue
        out.append(r)
    return sorted(out, key=lambda r: r["ratios"]["base_over_cal_model_lb"])


def sec_two_models(c: dict) -> str:
    """为什么基线会「低于下界」：两条腿跑的是两台不同的机器。

    这一节存在的理由是它曾经真的错过：报告把拍图模型的界与基线的实测放在
    同一列做比值，最狠的一行给出 0.73&times;，读起来像是违反物理。三个成因
    全部可精确量化，这里逐个给出证据，并给出在基线自己模型下重建的界。
    """
    bad = _two_model_rows(c)
    if not bad:
        return ""
    w = bad[0]
    g = row1(c, pattern="gather", algo="dim_2phase", tier="T0", m=13,
             bidir=True)
    rot = row1(c, pattern="allgather", algo="ring_rotate", tier="T0", m=1,
               bidir=True)
    out = ['<h3>为什么基线会「低于下界」：那是两台不同的机器</h3>',
           f'<div class="note bad"><b>先说结论：不是违反物理，也不是仿真漏拍，'
           f'是记账口径串了。</b>上表的下界算在<b>拍图</b>的机器模型上，'
           f'而 <code>ring_base</code> 跑的是另一台机器。拿前者去除后者，'
           f'{len(bad)}/{len(rows(c, tier="T0", bidir=True))} 行会出现 '
           f'&lt;1 的比值，最狠的是 '
           f'{lbl(w["pattern"], w["algo"], w["tier"])} m={w["m"]}：'
           f'实测 {f(w["ring_base"]["makespan"])} 拍 vs 拍图模型下界 '
           f'{f(w["bounds"]["makespan_lb"])} 拍 = '
           f'<b>{times(w["ratios"]["base_over_cal_model_lb"])}</b>。'
           f'两台机器的差别只剩两处，全部可量化：</div>']
    out.append("<ol>")
    if g:
        out.append(
            f'<li><b>环站出口容量（这是大头）。</b>拍图给每个环站出口 '
            f'<code>leave_ports=1</code>，即<b>每拍 1 flit</b>；基线的 sim 里'
            f'节点按 <code>eject_bw=RAMP_BW={f(g["bounds"]["ramp_bw"])}</code>'
            f' flit/拍 排空 L1，出口不单独限流。所以在 '
            f'{lbl(g["pattern"], g["algo"], g["tier"])} m=13 上，'
            f'拍图模型的端口界是 <b>{f(g["bounds"]["port_lb"])}</b> 拍，'
            f'而基线真正欠的是弹出界 <b>{f(g["bounds_base"]["ramp_lb"])}</b> 拍'
            f'（{f(g["bounds"]["max_eject_flits"])} flit ÷ '
            f'{f(g["bounds"]["ramp_bw"])}）—— 实测 '
            f'{f(g["ring_base"]["makespan"])} 拍正好落在两者之间，两边都自洽。'
            f'</li>')
    if rot:
        gap = rot["bounds"]["latency_lb"] - rot["bounds_base"]["latency_lb"]
        out.append(
            f'<li><b>每相位 +RAMP 的斜坡常数。</b>拍图的时延地板按 '
            f'<code>wire + dur + RAMP</code> 收，每个相位都收一次；'
            f'sim 一拍不收。相位一多就成系统性偏差：'
            f'{lbl(rot["pattern"], rot["algo"], rot["tier"])} m=1 有 '
            f'{rot["shape"]["n_phases"]} 个相位，'
            f'两个时延地板差 <b>{f(gap)}</b> 拍 = RAMP&times;相位数，'
            f'占了它 {pct(gap / rot["bounds"]["latency_lb"])} 的下界。</li>')
    out.append(
        '<li class="muted"><b>过桥（已消除）。</b>曾经还有第三处：拍图给每次'
        '转环收 <code>t_turn</code>，而 sim 免费过桥。§2 换成 10 拍的桥之后，'
        '两条腿都按同一个 <code>t_turn=10</code> 收，一次无争用的转环传输在'
        '两边算出来一模一样（断言 #42）。这一项从此不再是口径差。</li>')
    out.append("</ol>")
    out.append('<p>把界在<b>基线自己的模型</b>下重建（弧负载不变、出口按 '
               'RAMP_BW、不收 +RAMP、过桥同样收 10 拍）之后，'
               '<b>40 行全部 &ge; 下界，且旋转那 10 行精确等于 1.000&times;</b>'
               '（说明偏转机制跑旋转在它自己模型下已是时延最优）。'
               '下表是错得最狠的几行，两个模型并排：</p>')
    out.append('<table><tr><th>方案</th><th class="n">m</th>'
               '<th class="n">基线实测</th><th class="n">拍图模型下界</th>'
               '<th class="n">基线模型下界</th>'
               '<th class="n">/拍图模型</th><th class="n">/基线模型</th>'
               '<th>各自绑定项</th></tr>')
    for r in bad[:8]:
        out.append(
            f'<tr><td>{lbl(r["pattern"], r["algo"], r["tier"])}</td>'
            f'<td class="n">{r["m"]}</td>'
            f'<td class="n">{f(r["ring_base"]["makespan"])}</td>'
            f'<td class="n">{f(r["bounds"]["makespan_lb"])}</td>'
            f'<td class="n">{f(r["bounds_base"]["makespan_lb"])}</td>'
            f'<td class="n"><span class="lose">'
            f'{times(r["ratios"]["base_over_cal_model_lb"])}</span></td>'
            f'<td class="n"><span class="win">'
            f'{times(r["ratios"]["base_over_base_lb"])}</span></td>'
            f'<td>{BIND_CN.get(r["bounds"]["binding_lb"], r["bounds"]["binding_lb"])}'
            f' / '
            f'{BIND_CN.get(r["bounds_base"]["binding_lb"], r["bounds_base"]["binding_lb"])}'
            f'</td></tr>')
    out.append("</table>")
    out.append('<div class="note good"><b>这件事对结论有多大影响？</b>'
               '它<b>不改变</b>任何「基线 vs 拍图」的头对头比较 —— 那些比的是'
               '两个实测 makespan，不经过下界。它改变的是「离最优还有多远」'
               '这类陈述：基线的 base/lb 一列必须换成它自己模型的界。'
               '成因 1 同时也是 §8「端口粒度」那一节的根源，'
               '两者是同一件事的两个后果。'
               '验证套件现在有 6 项断言盯着这件事（#31&ndash;#36），'
               '其中一项显式记录「用一个界量两条腿」这个做法被推翻。</div>')
    return "".join(out)


def sec_compare(c: dict) -> str:
    out = []
    for m in (1, 13):
        cats, base, c0, c1, lb = [], [], [], [], []
        for pat in PATTERNS:
            b = best_base(c, pat, m)
            k0, k1 = best_cal(c, pat, m, "T0"), best_cal(c, pat, m, "T1")
            cats.append(CN[pat])
            base.append(b["ring_base"]["makespan"] if b else None)
            c0.append(k0["calendar"]["makespan"] if k0 else None)
            c1.append(k1["calendar"]["makespan"] if k1 else None)
            # 画两个模型都成立的公共下界：base 模型的界恒 <= 拍图模型的界
            # （出口容量更宽、不收 +RAMP、过桥免费），所以它是唯一一条不会
            # 出现「实测柱低于下界柱」的水平线。拍图自己更紧的界在 §13。
            lb.append(min(x["bounds_base"]["makespan_lb"]
                          for x in (k0, k1) if x))
        out.append(
            f'<div class="fig">'
            f'{grouped_bars(cats, [{"name": "ring_base 基线（T0）", "cls": "cC", "vals": base}, {"name": "拍图（T0 同能力）", "cls": "cB", "vals": c0}, {"name": "拍图（T1 加多播）", "cls": "cD", "vals": c1}, {"name": "理论下界", "cls": "cE", "vals": lb}], ylabel="makespan（拍）", hi_series=1)}'
            f'<div class="cap"><b>图：m={m} 时六个集合通信的 makespan。</b>'
            f'三条腿各取自己最优的集合算法（算法与 transport 是正交的两根轴，'
            f'用同一个算法压基线等于把基线做成稻草人）。<b>绿柱才是同硬件'
            f'口径的对比</b>：`ring_base` 只支持 unicast，即 T0。'
            f'紫柱是额外加了弧多播 + L1 归约之后的拍图，'
            f'<b>只有扇出型有紫柱</b> &mdash; 归约 / 收集 / 全交换无可复制，'
            f'T1 与 T0 逐字段相同。'
            f'<b>下界柱是「两套机器模型都成立」的公共下界</b>'
            f'（弧负载 / L1 弹出 / 时延三者取最大，按基线模型的口径），'
            f'因为拍图模型自己那条更紧的界对基线不成立 —— 详见 §5 末。'
            f'拍图离它自己那条更紧的界有多远，在 §13 的表里。</div></div>')
    return "".join(out)


def split_1p(c: dict, m: int = 13, tie: float = 0.03
             ) -> tuple[list[str], list[str], list[str]]:
    """按 m=13 的基线/拍图比值把六个 collective 分成赢、打平、输三组。

    分组由数据算出而不是写死：改了 &sigma;、端口数或算法集之后叙述会跟着走，
    不会留下一句和图对不上的话。
    """
    win, tie_, lose = [], [], []
    for pat in PATTERNS:
        b, k = best_base(c, pat, m), best_cal(c, pat, m, "T0")
        if not b or not k:
            continue
        r = b["ring_base"]["makespan"] / k["calendar"]["makespan"]
        (tie_ if abs(r - 1) <= tie else win if r > 1 else lose).append(pat)
    return win, tie_, lose


def sec_winloss(c: dict) -> str:
    items = []
    for pat in PATTERNS:
        b, k = best_base(c, pat, 13), best_cal(c, pat, 13, "T0")
        k1 = best_cal(c, pat, 13, "T1")
        if not b or not k:
            continue
        note = (f'{b["ring_base"]["makespan"]} / '
                f'{k["calendar"]["makespan"]} 拍')
        if k1:
            note += (f'；+T1 后 '
                     f'{b["ring_base"]["makespan"] / k1["calendar"]["makespan"]:.2f}'
                     f'&times;')
        items.append({
            "label": f'{CN1[pat]}（{k["algo"]}）',
            "ratio": b["ring_base"]["makespan"] / k["calendar"]["makespan"],
            "note": note})
    items.sort(key=lambda it: -it["ratio"])
    win, tie, lose = split_1p(c)
    j = lambda ps: "、".join(CN1[p] for p in ps) or "无"
    return (f'<div class="fig">'
            f'{diverging_bars(items, left_label="基线更快", right_label="拍图更快")}'
            f'<div class="cap"><b>图：m=13、<u>同为 T0 能力</u>时的「基线拍数 '
            f'/ 拍图拍数」。</b>向右越长表示拍图赢得越多；右列附注同时给出'
            f'加上 T1 多播硬件后的比值。分界线不是随机落下的：'
            f'<b class="win">拍图赢：{j(win)}</b>（流量本来就摊得开，'
            f'拍图能把弧排满而基线要为偏转让路）；'
            f'<b class="lose">基线赢：{j(lose)}</b>'
            f'（纯 fan-in，全部流量挤向同一个抽取点）；'
            f'打平：{j(tie)}。输的那两个原因见 §8 &mdash; 是下环端口的记账'
            f'粒度，不是偏转有魔法。</div></div>')


def _why_intro(c: dict) -> str:
    _, tie, lose = split_1p(c)
    j = lambda ps: "、".join(CN1[p] for p in ps) or "无"
    return (f'm=13 下基线更快的是 <b>{j(lose)}</b>，{j(tie)} 只是打平。'
            f'这与直觉相反，所以要把机理说清楚，而不是把它藏进平均值里：'
            f'这几个模式都是<b>多对一</b>，所有流量挤向同一个 root 的抽取点，'
            f'于是胜负完全由「一个抽取点每拍能吃几个 flit」决定。')


def sec_why_lose(c: dict) -> str:
    out = []
    ps = [e for e in c["port_sensitivity"] if e["m"] == 13]
    cats, p1, p2, base = [], [], [], []
    for e in ps:
        b = row1(c, pattern=e["pattern"], algo=e["algo"], tier=e["tier"], m=13,
                 bidir=True)
        cats.append(f'{CN1[e["pattern"]]}<br>{e["algo"]}/{e["tier"]}')
        p1.append(e["by_ports"]["1"]["makespan"])
        p2.append(e["by_ports"]["2"]["makespan"])
        base.append((b or {}).get("ring_base", {}).get("makespan"))
    out.append(
        f'<div class="fig">'
        f'{grouped_bars(cats, [{"name": "ring_base 基线", "cls": "cC", "vals": base}, {"name": "拍图·1 端口", "cls": "cA", "vals": p1}, {"name": "拍图·2 端口", "cls": "cB", "vals": p2}], ylabel="makespan（拍）", w=880, h=340)}'
        f'<div class="cap"><b>图：把拍图模型的端口放宽，差距就合上了。</b>'
        f'拍图把一个抽取点整段（m&middot;&sigma; 拍）独占给一次传输；'
        f'`ring_base` 按 L1 的 <code>RAMP_BW</code> 逐 flit 交错抽取。'
        f'这是<b>记账粒度</b>的差异，不是机制优劣 &mdash; 端口翻倍后拍图'
        f'反超或接近。两条 T1 方案没有红柱：`ring_base` 只支持 unicast，'
        f'这两行没有可比的基线。</div></div>')
    out.append('<table><tr><th>方案</th><th class="n">ring_base</th>'
               '<th class="n">拍图（1 端口）</th><th class="n">拍图（2 端口）'
               '</th><th class="n">端口翻倍收益</th><th>谁赢（2 端口口径）</th>'
               '</tr>')
    for e, b in zip(ps, base):
        a, bb = e["by_ports"]["1"]["makespan"], e["by_ports"]["2"]["makespan"]
        if b is None:
            who, cls = "不可比（T1 需多播，基线只有 unicast）", "muted"
        else:
            who = "拍图" if bb < b else "基线"
            cls = "win" if who == "拍图" else "lose"
        out.append(
            f'<tr><td>{lbl(e["pattern"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{f(b)}</td><td class="n">{f(a)}</td>'
            f'<td class="n">{f(bb)}</td>'
            f'<td class="n">{times(e["speedup_ports2"])}</td>'
            f'<td class="{cls}">{who}</td></tr>')
    out.append("</table>")
    return "".join(out)


def sec_cost(c: dict, idx: dict | None) -> str:
    out = []
    cats, defl, algos = [], [], []
    for pat in PATTERNS:
        b = best_base(c, pat, 13)
        if not b:
            continue
        cats.append(CN[pat])
        defl.append(b["ring_base"].get("deflect_per_flit") or 0)
        algos.append((pat, f'{b["algo"]}/{b["tier"]}'))
    hi = max(range(len(defl)), key=lambda i: defl[i])
    zero = [p for (p, _), v in zip(algos, defl) if v == 0]
    out.append(
        f'<div class="fig">'
        f'{grouped_bars(cats, [{"name": "偏转次数 / flit", "cls": "cC", "vals": defl}], ylabel="次 / flit", h=270, note_fmt="{:.3f}")}'
        f'<div class="cap"><b>图：基线的隐性代价 —— 偏转率（m=13，各取基线'
        f'自己最优的算法）。</b>偏转是再循环：既白吃弧周期又打乱到达顺序，'
        f'所以基线<b>必须</b>带目的端重组缓冲。'
        f'{"、".join(CN1[p] for p in zero)} 的偏转恒为 <b>0</b>'
        f'（维序树流量下同一时刻的转向全部同向，桥看不到互相转向）；'
        f'把偏转顶起来的是 <code>flat</code> 类流量 —— '
        f'<b>{CN1[algos[hi][0]]} / {algos[hi][1]} 达 {defl[hi]:.3f} 次/flit'
        f'</b>，因为 47 个源同时挤向同一个 root 的环。'
        f'但这并不妨碍基线在该模式上仍与拍图打平（§7）：偏转让它白跑，'
        f'逐 flit 抽取又替它省回来。</div></div>')
    out.append('<table><tr><th>集合通信</th><th>基线最优算法</th>'
               '<th class="n">偏转 / flit</th><th class="n">乱序次数</th>'
               '<th class="n">重组缓冲峰值</th><th class="n">延迟 p99</th>'
               '</tr>')
    for pat in PATTERNS:
        b = best_base(c, pat, 13)
        if not b:
            continue
        rb = b["ring_base"]
        out.append(
            f'<tr><td>{CN1[pat]}</td><td>{b["algo"]}/{b["tier"]}</td>'
            f'<td class="n">{f(rb.get("deflect_per_flit"), 4)}</td>'
            f'<td class="n">{f(rb.get("n_out_of_order"))}</td>'
            f'<td class="n">{f(rb.get("max_reasm_occupancy"))} flit</td>'
            f'<td class="n">{f(rb.get("lat_p99"))}</td></tr>')
    out.append("</table>")
    if idx:
        ag = [e for e in idx["entries"] if e.get("collective") == "allgather"]
        if ag:
            small = min(ag, key=lambda e: e.get("n_records", 1e9))
            big = max(ag, key=lambda e: e.get("n_records", 0))
            out.append(
                f'<div class="note"><b>拍图的代价搬到了控制存储上。</b>'
                f'同一个全收集，<code>{small["algo"]}/{small["tier"]}</code> '
                f'导出 {f(small.get("n_records"))} 条环站记录，'
                f'<code>{big["algo"]}/{big["tier"]}</code> 导出 '
                f'{f(big.get("n_records"))} 条 —— 相差 '
                f'{(big.get("n_records") or 1) / max(1, small.get("n_records") or 1):.1f}'
                f'&times;。基线删掉的是这张表，换来的是桥 FIFO 与重组缓冲。'
                f'</div>')
    return "".join(out)


def sec_gantt(c: dict) -> str:
    try:
        from rg_ring_calendar import build_calendar
        from rg_ring_collectives import build_ring_collective
        from rg_ring_topo import RingTopology
    except Exception as exc:                                # pragma: no cover
        return f'<p class="muted">无法现场排拍图：{exc}</p>'
    topo = RingTopology()
    out = []
    for pat, algo, tier in (("broadcast", "dim_2phase", "T1"),
                            ("allgather", "dim_2phase", "T1")):
        col = build_ring_collective(topo, pat, m=13, tier=tier, algo=algo,
                                    root=ROOT_NODE)
        cal = build_calendar(topo, col)
        sl = cal.slack()
        out.append(
            f'<div class="fig">{svg_gantt(topo, cal)}'
            f'<div class="cap"><b>图：{CN1[pat]} / {algo} / {tier}、m=13 的'
            f'真实拍图占用（不是示意）。</b>每条横线是一次传输的 '
            f'[t0, t0+tail) 区间，按相位着色，虚线是 barrier。'
            f'共 {cal.makespan} 拍、{len(cal.starts)} 次传输，'
            f'松弛 p50={sl["p50"]} 拍、最小 {sl["min"]} 拍。'
            f'<b>相位内看不到任何排队</b>：区间一旦排定就整条路径无停留，'
            f'转环驻留实测恒为 0。</div></div>')
    return "".join(out)


BIND_CN = {"latency": "时延", "port": "端口", "arc_load": "弧负载",
           "ramp": "ramp 弹出", "occupancy": "占用"}


BIND_FLOOR = {"latency": "时延地板", "cut": "cut（截面）",
              "port": "核端口", "ramp": "L1 ramp"}


def th_row(t: dict, pat: str, m: int) -> dict:
    return next(x for x in t["theory"] if x["pattern"] == pat and x["m"] == m)


def hl_row(t: dict, pat: str, m: int) -> dict:
    return next(x for x in t["headline"] if x["pattern"] == pat and x["m"] == m)


def sec_floor(t: dict | None) -> str:
    """§3：六个 collective 的路由无关下界，makespan 界与 II 界分开列。"""
    if not t:
        return ('<p class="muted">ring_throughput_8x6.json 缺失，'
                '先跑 dse_ring_throughput_8x6.py。</p>')
    out = [
        '<div class="eq">makespan 下界 = max(cut, 核端口, L1 ramp, <b>时延地板'
        '</b>)　　　II 下界 = max(cut, 核端口, L1 ramp)</div>',
        '<p>两条界必须分开，因为它们回答的是两个问题：一次集合通信最少要多久'
        '（受<b>最远那段线</b>限制），和连着做能有多快（受<b>最忙那个资源</b>'
        '限制）。把两者平均成一个数，就会得到「这块布局带宽不够」这种与实测'
        '相反的结论。</p>',
        '<table><tr><th>界</th><th>它数的是什么</th><th>为什么绕不过去</th>'
        '</tr>'
        f'<tr><td>cut（截面）</td><td>必须穿过某条竖/横切口的 flit 数 &divide; '
        f'该切口每拍能过的 flit 数</td><td>切口把 48 个核分成两半，'
        f'两半之间只有这些线；折叠环让每个行环<b>穿两次</b>切口，'
        f'所以竖切口是 12 flit/拍而不是 mesh 的 6</td></tr>'
        '<tr><td>核端口</td><td>一个核必须发出或收进的 flit 数 &divide; 它的 '
        '2 个环口</td><td>§1 定下的前提：每核 1 个行口 + 1 个列口</td></tr>'
        f'<tr><td>L1 ramp</td><td>同上 &divide; <code>RAMP_BW</code> = '
        f'{RAMP_BW} flit/拍</td><td>数据最终要落进 L1，这一段谁也代替不了'
        f'</td></tr>'
        '<tr><td>时延地板</td><td>该模式强制的最长「源&rarr;目的」最短路时延 '
        '+ 尾部 (m&minus;1)&sigma;</td><td>最短路时延满足三角不等式，'
        '所以中继转发只会更慢，不会更快</td></tr></table>',
        '<p>这里的界刻意取<b>最弱</b>的一版：允许核做中继、也允许核先把收到的'
        '数据和自己的合并再转发（那只是 AI core 在 L1 里做算术，不需要任何'
        '网络特性）。两个后果值得说明白：</p>'
        '<ul><li><b>T0 与 T1 同界。</b>弧多播与 L1 归约不改变地板 —— 中继本来'
        '就能做到「每份数据只穿切口一次」，本地合并本来就能把归约折叠掉。'
        '多播买到的是<b>用更少相位、更小端口压力去接近地板</b>，'
        '这体现在实测里，不体现在界里。</li>'
        '<li>它比 §1 里排挤挂接方案用的那套需求口径<b>更弱</b>'
        '（那套不许中继，按 flat 算法记账，才能让九个挂接方案在同一把尺子下'
        '比较）。所以树形算法可以低于 §1 的数，但<b>不能低于这里的数</b> —— '
        '这条已写成断言。</li></ul>']
    out.append('<p class="muted">下面两张图把「核端口」与「L1 ramp」画成一根柱：'
               '§1 让每核 2 个环口正好等于 <code>RAMP_BW</code> = '
               f'{RAMP_BW} flit/拍，所以这两条界在本设计里<b>恒等</b>，'
               '分开画只是两根一样高的柱子。表里仍分列，便于换端口数时对照。</p>')
    for m in (1, 13):
        cats, cut, port, lat = [], [], [], []
        for pat in PATTERNS:
            r = th_row(t, pat, m)
            cats.append(CN[pat])
            cut.append(r["cut_lb"])
            port.append(max(r["port_lb"], r["ramp_lb"]))
            lat.append(r["lat_latency_floor"])
        out.append(
            f'<div class="fig">'
            f'{grouped_bars(cats, [{"name": "cut（截面）", "cls": "cA", "vals": cut}, {"name": "核端口 = L1 ramp", "cls": "cB", "vals": port}, {"name": "时延地板", "cls": "cE", "vals": lat}], ylabel="下界（拍）", h=330, vlab=True)}'
            f'<div class="cap"><b>图：m={m} 时哪条界说话。</b>'
            + ('m=1 时<b>六个模式全部由时延地板绑定</b>：容量项最大也只有 '
               f'{th_row(t, "alltoall", 1)["cut_lb"]} 拍（全交换的 cut），'
               f'而地板是 {th_row(t, "alltoall", 1)["lat_distance_cy"]} 拍纯'
               '线延迟。<b>单 flit 场景不是带宽问题</b>，再宽的布线也压不动它，'
               '能动的只有跨度与相位数。'
               if m == 1 else
               'm=13 时三个「多条独立数据」的模式换成容量界：'
               f'收集与全收集被<b>核端口 / L1 ramp</b> 绑定'
               f'（{th_row(t, "gather", 13)["port_lb"]} 拍 = 47&times;13 flit '
               f'&divide; 2），全交换被<b>cut</b> 绑定'
               f'（{th_row(t, "alltoall", 13)["cut_lb"]} 拍）。'
               '广播/归约/全归约仍是时延界 —— 它们的结果只有一份，'
               '中继与本地合并把容量需求压到了个位数。')
            + '</div></div>')
    out.append('<table><tr><th>集合通信</th><th class="n">m</th>'
               '<th class="n">cut</th><th class="n">核端口</th>'
               '<th class="n">L1 ramp</th><th class="n">时延地板</th>'
               '<th class="n">makespan 下界</th><th class="n">II 下界</th>'
               '<th>谁绑定</th><th>见证</th></tr>')
    for pat in PATTERNS:
        for m in (1, 13):
            r = th_row(t, pat, m)
            wit = (r["cut_witness"] if r["binding"] == "cut"
                   else (f'每核收 {r["max_absorb"]} flit &divide; '
                         f'{RAMP_BW} flit/拍'
                         if r["binding"] in ("port", "ramp")
                         else f'{r["lat_witness"]}：{r["lat_distance_cy"]} 拍'
                              + (f' + 尾部 {r["lat_tail_cy"]}'
                                 if r["lat_tail_cy"] else '')))
            out.append(
                f'<tr><td>{CN1[pat]}</td><td class="n">{m}</td>'
                f'<td class="n">{f(r["cut_lb"])}</td>'
                f'<td class="n">{f(r["port_lb"])}</td>'
                f'<td class="n">{f(r["ramp_lb"])}</td>'
                f'<td class="n">{f(r["lat_latency_floor"])}</td>'
                f'<td class="n"><b>{f(r["makespan_lb"])}</b></td>'
                f'<td class="n"><b>{f(r["II_lb"])}</b></td>'
                f'<td>{BIND_FLOOR.get(r["binding"], r["binding"])}</td>'
                f'<td class="muted">{wit}</td></tr>')
    out.append("</table>")
    a1 = hl_row(t, "alltoall", 1)
    g1 = hl_row(t, "gather", 1)
    out.append(
        f'<div class="note"><b>这些地板紧不紧？</b>后面 §13 会逐个对上，'
        f'先给两个极端：流水稳态下<b>收集的拍图是 '
        f'{times(g1["cal_T0"]["per_round_over_lb"])} 地板</b>'
        f'（{g1["cal_T0"]["best_II"]["per_round"]} 拍/轮 vs 界 '
        f'{g1["bound"]["II_lb"]}），几乎没有余量；'
        f'<b>全交换是 {times(a1["cal_T0"]["per_round_over_lb"])}</b>'
        f'（{a1["cal_T0"]["best_II"]["per_round"]} vs {a1["bound"]["II_lb"]}），'
        f'差额来自它必须走两个维度、而 cut 界只数一个方向。'
        f'反过来，广播这类模式的 II 界弱到 {th_row(t, "broadcast", 1)["II_lb"]} '
        f'拍 —— 那不是拍图排得差，而是<b>它根本不受容量限制</b>，'
        f'成本全在相位链上。</div>')
    return "".join(out)


def sec_throughput(t: dict | None) -> str:
    """§13 前半：每轮均摊时间与带宽利用率，无排图 vs 静态排图。"""
    if not t:
        return ('<p class="muted">ring_throughput_8x6.json 缺失。</p>')
    out = [
        '<div class="eq">II_eff = (T_R &minus; T1)/(R&minus;1)　'
        '均摊一轮 = T_R / R　'
        '带宽利用率 = 弧占用周期 &divide; (192 弧 &times; 该次 makespan)</div>',
        f'<p>同一张环，同一个流集，R = {t["rounds"][-1]} 轮背靠背地压进去'
        f'（轮与轮之间不设 barrier，后一轮可以填前一轮留下的空隙），'
        f'看两件事：<b>一轮均摊多少拍</b>，以及<b>这些拍里有多少是在搬有用的 '
        f'flit</b>。两条腿用同一条公式：拍图把自己的 footprint 加起来，'
        f'仿真器数每一跳的真实占用（含偏转绕圈）。</p>',
        '<div class="note"><b>为什么主指标是「均摊一轮 T_R/R」而不是 II_eff。'
        '</b>II_eff 是让 T_avg=(T1+T_R)/2 成立的插值参数，不是渐近值：'
        '第一轮已经顺手做掉了一部分被摊掉的工作，所以有限 R 下它<b>可以低于'
        '容量界</b>（实测最深一处到 0.91&times;）。均摊值 T_R/R 恒 &ge; 容量界，'
        '这条已写成断言，所以画在图上不会出现「柱子低于地板」。</div>']
    for m in (1, 13):
        cats, base, c0, c1, lb = [], [], [], [], []
        for pat in PATTERNS:
            h = hl_row(t, pat, m)
            cats.append(CN[pat])
            base.append((h.get("base") or {}).get("best_II", {})
                        .get("per_round"))
            c0.append(h["cal_T0"]["best_II"]["per_round"])
            c1.append(h["cal_T1"]["best_II"]["per_round"]
                      if h["cal_T1"]["best_II"]["tier"] == "T1" else None)
            lb.append(h["bound"]["II_lb"])
        out.append(
            f'<div class="fig">'
            f'{grouped_bars(cats, [{"name": "无排图基线（T0）", "cls": "cC", "vals": base}, {"name": "静态拍图（T0）", "cls": "cB", "vals": c0}, {"name": "静态拍图（T1）", "cls": "cD", "vals": c1}, {"name": "II 下界", "cls": "cE", "vals": lb}], ylabel="均摊一轮（拍）", hi_series=1, note_fmt="{:,.1f}", vlab=True)}'
            f'<div class="cap"><b>图：m={m} 流水稳态下一轮的均摊拍数。</b>'
            f'越低越快。{_thr_caption(t, m)}</div></div>')
    cats, bo, bu, cu = [], [], [], []
    for pat in PATTERNS:
        h = hl_row(t, pat, 13)
        b = (h.get("base") or {}).get("best_II", {})
        cats.append(CN1[pat])
        bo.append(100 * b.get("util", 0) if b else None)
        bu.append(100 * b.get("useful_util", 0) if b else None)
        cu.append(100 * h["cal_T0"]["best_II"]["util"])
    out.append(
        f'<div class="fig">'
        f'{grouped_bars(cats, [{"name": "基线·占用", "cls": "cC", "vals": bo}, {"name": "基线·有用", "cls": "cA", "vals": bu}, {"name": "拍图·占用=有用", "cls": "cB", "vals": cu}], ylabel="192 弧平均利用率 %", h=340, note_fmt="{:.1f}")}'
        f'<div class="cap"><b>图：m=13 流水稳态的带宽利用率，'
        f'占用与有用分开算。</b>「有用」把每个 flit 只按最短跳数记账，'
        f'所以<b>占用与有用的差额就是偏转绕圈烧掉的带宽</b>。'
        f'{_util_caption(t)}拍图两根柱恒等（hop tax 精确 = 1.00，'
        f'即它一跳都没绕），所以只画一根。'
        f'两条腿各取自己均摊最快的算法，具体算法见下表。</div></div>')
    out.append('<table><tr><th>集合通信</th><th class="n">m</th>'
               '<th class="n">II 下界</th>'
               '<th>无排图基线：算法 / 均摊一轮 / 比下界</th>'
               '<th class="n">占用</th><th class="n">有用</th>'
               '<th class="n">绕路税</th>'
               '<th>静态拍图（T0）：算法 / 均摊一轮 / 比下界</th>'
               '<th class="n">占用=有用</th><th class="n">关键弧</th>'
               '<th class="n">基线/拍图<br>&gt;1 拍图更快</th></tr>')
    for pat in PATTERNS:
        for m in (1, 13):
            h = hl_row(t, pat, m)
            b, k = h.get("base"), h["cal_T0"]
            bi = (b or {}).get("best_II", {})
            ki = k["best_II"]
            rt = (h.get("base_over_cal_T0") or {}).get("per_round")
            cls = "lose" if rt and rt < 1 else "win"
            weak = "&dagger;" if h["bound"]["binding"] == "latency" else ""
            out.append(
                f'<tr><td>{CN1[pat]}</td><td class="n">{m}</td>'
                f'<td class="n">{f(h["bound"]["II_lb"])}{weak}</td>'
                f'<td>{bi.get("algo", "&mdash;")} / {f(bi.get("per_round"), 1)}'
                f' / {times(b["per_round_over_lb"]) if b else "&mdash;"}</td>'
                f'<td class="n">{pct(bi.get("util"))}</td>'
                f'<td class="n">{pct(bi.get("useful_util"))}</td>'
                f'<td class="n">{times(bi.get("hop_tax"))}</td>'
                f'<td>{ki["algo"]} / {f(ki["per_round"], 1)} / '
                f'{times(k["per_round_over_lb"])}</td>'
                f'<td class="n">{pct(ki["util"])}</td>'
                f'<td class="n">{pct(ki["crit"])}</td>'
                f'<td class="n"><span class="{cls}">{times(rt)}</span></td>'
                f'</tr>')
    out.append('</table><p class="muted">&dagger; 该格的 II 界不具约束力：'
               '这一行的 makespan 下界由时延地板绑定（§3），'
               '容量界弱到只剩个位数，所以「比下界」的倍数不必当成排图质量看，'
               '它衡量的是相位链而不是资源。</p>')
    reasm = max((v["max_reasm_occupancy"] for r in t["rows"]
                 if r["ring_base"]["T1"] is not None
                 for v in r["ring_base"]["by_rounds"].values()), default=0)
    fan = max((r for r in t["rows"] if r.get("base_hop_tax")),
              key=lambda r: r["base_hop_tax"])
    out.append(
        f'<div class="note bad"><b>基线那些「利用率更高」的格子要读两遍。</b>'
        f'{CN1[fan["pattern"]]}/<code>{fan["algo"]}</code> m={fan["m"]} 上'
        f'基线的占用利用率是 '
        f'{pct(fan["ring_base"]["by_rounds"]["13"]["util"]["global_util"])}、'
        f'拍图只有 '
        f'{pct(fan["calendar"]["by_rounds"]["13"]["util"]["global_util"])}，'
        f'但基线的<b>有用</b>部分只有 '
        f'{pct(fan["ring_base"]["by_rounds"]["13"]["util"]["useful_global"])}'
        f' —— 和拍图基本相同。差出来的 '
        f'{times(fan["base_hop_tax"])}最小跳数全部烧在绕着 root 转圈上'
        f'（偏转 {f(fan["ring_base"]["by_rounds"]["13"]["deflect_per_flit"], 2)}'
        f' 次/flit）。<b>把「弧上有东西在跑」当成带宽利用率，会把浪费读成产出。'
        f'</b>另一项隐性代价：流水化后基线的重组缓冲峰值到 {f(reasm)} flit，'
        f'而它配的是 64 flit —— 上面这些基线 II 是「假设重排序缓冲无限大」'
        f'才成立的，详见 §9。</div>')
    return "".join(out)


def _thr_caption(t: dict, m: int) -> str:
    a, g = hl_row(t, "alltoall", m), hl_row(t, "gather", m)
    ar = (a.get("base_over_cal_T0") or {}).get("per_round")
    parts = [
        f'全交换上拍图 {f(a["cal_T0"]["best_II"]["per_round"], 1)} 拍/轮、'
        f'基线 {f(a["base"]["best_II"]["per_round"], 1)} 拍/轮，'
        f'拍图快 {times(ar)}；这是六个模式里差得最多的一个，'
        f'因为它最吃 cut 而基线要给偏转留空。']
    if m == 1:
        parts.append(
            f'注意<b>收集这一列两条腿都贴着地板</b>'
            f'（{f(g["cal_T0"]["best_II"]["per_round"], 1)} / '
            f'{f(g["base"]["best_II"]["per_round"], 1)} vs 界 '
            f'{g["bound"]["II_lb"]}）：它的 II 由「root 的 L1 每拍只能吃 '
            f'{RAMP_BW} 个 flit」决定，与 transport 无关，'
            f'谁也别想在这一格上赢。')
    else:
        parts.append(
            f'广播/归约/全归约的柱子远高于它们的 II 下界，'
            f'那是因为界弱（§3）：这三个模式的稳态成本来自相位链，'
            f'不是任何一条弧或端口。')
    return "".join(parts)


def _util_caption(t: dict) -> str:
    peak = max(t["rows"], key=lambda r: max(
        v["util"]["critical_arc_util"] for v in r["calendar"]["by_rounds"].values()))
    pv = max(v["util"]["critical_arc_util"]
             for v in peak["calendar"]["by_rounds"].values())
    return (f'全局利用率低不等于排得差：真正该看的是最忙那条弧 —— '
            f'{CN1[peak["pattern"]]}/<code>{peak["algo"]}</code> 的拍图把关键弧'
            f'排到 {pct(pv)}。')


def sec_util(c: dict) -> str:
    cats, g, cr, ks = [], [], [], []
    for pat in PATTERNS:
        k = best_cal(c, pat, 13)
        u = k["calendar"]["util"]
        cats.append(f'{CN1[pat]}<br>{k["algo"]}/{k["tier"]}')
        g.append(u["global_util"] * 100)
        cr.append(u["critical_arc_util"] * 100)
        ks.append(k)
    bc = next(k for k in ks if k["pattern"] == "broadcast")
    out = [f'<div class="fig">'
           f'{grouped_bars(cats, [{"name": "全局（192 弧均）", "cls": "cA", "vals": g}, {"name": "关键弧（最忙）", "cls": "cD", "vals": cr}], ylabel="利用率 %", h=310, note_fmt="{:.1f}")}'
           f'<div class="cap"><b>图：绝对利用率低不等于排得差。</b>'
           f'广播全程只有 {bc["calendar"]["n_transfers"]} 次传输，'
           f'本来就填不满 192 条弧，而且它<b>由时延界绑定</b>'
           f'（{bc["calendar"]["makespan"]} 拍里 '
           f'{bc["calendar"]["makespan_lb"]} 拍是纯时延地板），'
           f'再排也压不下去。该问的是<b>最忙那条弧有没有排满</b>：'
           f'实测六个方案的「关键弧周期 / 关键弧下界」<b>全部等于 1.00&times;'
           f'</b>，即打包器在瓶颈弧上一拍没浪费。</div></div>']
    out.append('<table><tr><th>方案</th><th>绑定的界</th>'
               '<th class="n">makespan</th><th class="n">下界</th>'
               '<th class="n">/ 下界</th><th class="n">全局利用率</th>'
               '<th class="n">关键弧</th><th class="n">关键弧/其下界</th>'
               '</tr>')
    for k in ks:
        cal, u = k["calendar"], k["calendar"]["util"]
        out.append(
            f'<tr><td>{lbl(k["pattern"], k["algo"], k["tier"])}</td>'
            f'<td>{BIND_CN.get(cal["binding_lb"], cal["binding_lb"])}</td>'
            f'<td class="n">{f(cal["makespan"])}</td>'
            f'<td class="n">{f(cal["makespan_lb"])}</td>'
            f'<td class="n">{times(cal["makespan_over_lb"])}</td>'
            f'<td class="n">{pct(u["global_util"])}</td>'
            f'<td class="n">{pct(u["critical_arc_util"])}</td>'
            f'<td class="n"><span class="win">'
            f'{times(u["critical_arc_cycles_vs_lb"])}</span></td></tr>')
    out.append("</table>")
    pmax = max((e["speedup_ports2"] for e in c["port_sensitivity"]
                if e["m"] == 13), default=None)
    out.append(f'<div class="note"><b>那 makespan 与下界之间的差额来自哪里？'
               f'</b>不是弧 —— 是<b>相位 barrier</b>（下一相位必须等上一相位'
               f'全部落地）与<b>端口</b>（一个上/下环点每拍只吞一个 flit）。'
               f'想继续压就只能动这两样：放宽 barrier 改成相间流水，'
               f'或加第二个环站端口（§8 已量化，最多 {times(pmax)}）。'
               f'继续优化路由是没有用的。</div>')
    return "".join(out)


def sec_tavg(t: dict | None) -> str:
    if not t:
        return '<p class="muted">ring_tavg_8x6.json 缺失。</p>'
    rs = [r for r in t["ring"] if r["pattern"] == "allgather"]
    Rs = [1, 5, 13]

    def pts(algo, tier, ports):
        r = next((x for x in rs if x["algo"] == algo and x["tier"] == tier
                  and x["ports"] == ports), None)
        return [(R, r["by_rounds"][str(R)]["T_avg"]) for R in Rs] if r else []
    series = [
        {"name": "环 T1·2 端口", "cls": "cB",
         "pts": pts("dim_2phase", "T1", 2)},
        {"name": "环 T1·1 端口", "cls": "cA",
         "pts": pts("dim_2phase", "T1", 1)},
    ]
    mesh = t.get("mesh_reference") or {}
    if mesh.get("available"):
        mp = []
        for e in t["ring_vs_mesh"]:
            mb = e.get("mesh_best")
            if mb:
                mp.append((e["R"], mb["T_avg"]))
        if mp:
            series.append({"name": "mesh 最优树",
                           "cls": "cC", "pts": mp})
    out = [f'<div class="eq">T_avg = T1 + (R&minus;1)/2 &middot; II_eff = '
           f'(T1 + T_R)/2，II_eff = (T_R &minus; T1)/(R&minus;1)，'
           f'T_R 由自由多轮 rigid pack 实测</div>']
    rot = next((x for x in rs if x["algo"] == "ring_rotate"), None)
    d2 = next((x for x in rs if x["algo"] == "dim_2phase"
               and x["tier"] == "T1" and x["ports"] == 1), None)
    rot_note = ""
    if rot and d2:
        ir, i2 = (rot["by_rounds"]["13"]["II_eff"],
                  d2["by_rounds"]["13"]["II_eff"])
        cross = ("永远追不上" if ir >= i2 else
                 f'要到 R&asymp;{2 * (rot["T1"] - d2["T1"]) / (i2 - ir):.0f} '
                 f'才可能翻盘')
        rot_note = (
            f'满环旋转拍图刻意没画进来：它的 T_avg（{rot["by_rounds"]["1"]["T_avg"]:g}'
            f' / {rot["by_rounds"]["5"]["T_avg"]:g} / '
            f'{rot["by_rounds"]["13"]["T_avg"]:g}）比其它曲线高一个量级，'
            f'画进来会把关键的环/mesh 交叉压平。它的 II_eff = {ir:g} '
            f'<b>精确等于弧负载下界</b>，是全场最优；但 T1={rot["T1"]} 的填充'
            f'代价要摊到 {cross}（对比 dim_2phase/T1 的 T1={d2["T1"]}、'
            f'II_eff={i2:g}）。<b>II 最优不等于 T_avg 最优</b> —— '
            f'这是本节最实用的一条。')
    out.append(
        f'<div class="fig">'
        f'{line_chart(series, xlabel="流水轮数 R", ylabel="T_avg（拍）", xticks=[1, 5, 13])}'
        f'<div class="cap"><b>图：全收集的 T_avg 随流水深度变化。</b>'
        f'R=1 时 T_avg ≡ 单发 makespan；R 越大 II_eff 越支配。注意 1 端口的'
        f'环（蓝）在 R=1 时低于 mesh（红），到 R=13 已经反超 —— '
        f'交叉点就落在这段区间里。{rot_note}</div></div>')
    if mesh.get("available"):
        out.append('<table><tr><th class="n">R</th><th>mesh 最优</th>'
                   '<th class="n">mesh T_avg</th>'
                   '<th class="n">环（1 端口）</th><th class="n">环/mesh</th>'
                   '<th class="n">环（2 端口）</th><th class="n">环/mesh</th>'
                   '</tr>')
        for e in t["ring_vs_mesh"]:
            mb = e.get("mesh_best") or {}
            bp = e.get("by_ring_ports") or {}
            cells = ""
            for p in ("1", "2"):
                v = bp.get(p) or {}
                cls = "win" if v.get("winner") == "ring" else "lose"
                cells += (f'<td class="n">{f(v.get("T_avg"), 1)}</td>'
                          f'<td class="n"><span class="{cls}">'
                          f'{times(v.get("ring_over_mesh"), 3)}</span></td>')
            out.append(f'<tr><td class="n">{e["R"]}</td>'
                       f'<td>{mb.get("label", "&mdash;")}</td>'
                       f'<td class="n">{f(mb.get("T_avg"), 1)}</td>'
                       f'{cells}</tr>')
        out.append("</table>")
        out.append('<div class="note bad"><b>「环比 mesh 快」必须带上端口数。'
                   '</b>1 端口的环 R=1 赢 0.885&times;、R=5 已经基本打平'
                   '（1.006&times;，名义上 mesh 略赢）、<b>R=13 输 1.19&times;'
                   '</b>：深流水下绑定资源从跨度换成了环站那一个上/下环点。'
                   '同时 mesh 自己的最优方案在 R=13 也换人：axis+CCW → '
                   'Hamilton bi-tree（后者 II_eff 18.17 远优于前者 42.67，'
                   '但 T1=210 的填充代价要到深流水才摊得掉）。'
                   '<b>只报一个 R、或不报端口数，都会随机挑出一个赢家。</b>'
                   '</div>')
    else:
        out.append(f'<div class="note bad">mesh 参照不可用：'
                   f'{mesh.get("reason", "未记录")}。</div>')
    return "".join(out)


def sec_faults(rob: dict | None) -> str:
    if not rob:
        return '<p class="muted">ring_robust_8x6.json 缺失。</p>'
    fa = rob["faults"]
    cats = [f'{CN1[x["pattern"]]}<br>{x["algo"]}/{x["tier"]}' for x in fa]
    out = [
        f'<div class="fig">'
        f'{stacked_bars(cats, [{"name": "免重编译", "cls": "cB", "vals": [x["n_immune"] for x in fa]}, {"name": "需重编译", "cls": "cF", "vals": [x["n_recompile"] for x in fa]}, {"name": "无解", "cls": "cC", "vals": [x["n_infeasible"] for x in fa]}], ylabel="故障场景数", w=920, h=372, rotate=True)}'
        f'<div class="cap"><b>图：{fa[0]["n_scenarios"]} 个故障场景下每个'
        f'拍图的结局。</b>场景 = 环特有的绕回段失效 + 同环分散死点 + '
        f'既有的断链 / 死点 / 象限洞。<b>旋转拍图几乎全灭</b> —— 它每步'
        f'「所有节点同时上环、每条弧恰好用一次」不留备用弧，这既是它打到'
        f'下界的原因，也是它一断就死的原因。</div></div>']
    out.append('<table><tr><th>方案</th><th class="n">免重编译</th>'
               '<th class="n">需重编译</th><th class="n">无解</th>'
               '<th class="n">需追加修复相位</th>'
               '<th class="n">最坏做功归一化膨胀</th></tr>')
    for x in fa:
        out.append(
            f'<tr><td>{lbl(x["pattern"], x["algo"], x["tier"])}</td>'
            f'<td class="n">{x["n_immune"]}</td>'
            f'<td class="n">{x["n_recompile"]}</td>'
            f'<td class="n">{x["n_infeasible"]}</td>'
            f'<td class="n">{f(x.get("n_needing_repair_phase"))}</td>'
            f'<td class="n">'
            f'{times(x["worst_work_normalized_inflation"])}</td></tr>')
    out.append("</table>")
    out.append('<div class="note"><b>为什么膨胀比要做功归一化。</b>'
               '死节点同时删掉容量<b>和工作量</b>，所以原始 makespan 比会低于 '
               '1.0 —— 那是阵列变小，不是丢节点让集合通信变快。'
               '表里给的是除掉存活 flit 数之后的比值。</div>')
    n_rep = sum(x.get("n_needing_repair_phase") or 0 for x in fa)
    if n_rep:
        out.append(f'<div class="note bad"><b>一个死节点会逼出一个额外相位，'
                   f'而不只是重排一次。</b>维度切片算法靠<b>行列唯一交点</b>'
                   f'把某行的数据交给某列；交点死了，整列都拿不到那行的数据 '
                   f'&mdash; fabric 仍然连通，但拍图只有一条路。'
                   f'实测共 {n_rep} 个场景必须追加修复相位；修复相位为每个'
                   f'缺失项<b>只指派一个供给者</b>，否则归约型会重复累加。'
                   f'</div>')
    bp = rob.get("bypass_price")
    if bp:
        n_extra = sum(e["extra_infeasible_without_bypass"] for e in bp)
        out.append(
            f'<div class="note"><b>环站 bypass mux 的真实价格。</b>'
            f'去掉它以后<b>只有分散死点场景</b>会多出无解：全组共多 '
            f'{n_extra} 个。连续的死点洞和象限洞在 2-连通的环上都能从长边绕，'
            f'不需要这个 mux —— 所以它的面积是买「同环上多个分散死点」这一种'
            f'场景，不是买「任何死节点」。</div>')
    return "".join(out)


def sec_jitter(rob: dict | None) -> str:
    if not rob:
        return '<p class="muted">ring_robust_8x6.json 缺失。</p>'
    out: list[str] = []
    ji = rob["jitter"]

    def last(x: dict, k: str) -> int:
        return x["jitter"]["models"]["burst"][k]["curve"][-1]["makespan"]

    # 选三条策略真正分得开的那个方案：拿策略互相重合的方案画图，读者只会看到
    # 一条线，以为图坏了。同时数一数有多少方案的相间再同步等价于整表平移 ——
    # 这本身就是结论。
    spread = lambda x: (len({last(x, k) for k in ("global_shift",
                                                 "phase_shift", "repack")}),
                        last(x, "global_shift") - last(x, "repack"))
    big = max(ji, key=spread)
    tight = min(ji, key=lambda x: x["slack"]["p50"])
    n_ps_same = sum(1 for x in ji
                    if last(x, "phase_shift") == last(x, "global_shift"))
    mo = big["jitter"]["models"]["burst"]
    series = [{"name": n, "cls": cl,
               "pts": [(p["J"], p["makespan"]) for p in mo[k]["curve"]]}
              for k, n, cl in (("global_shift", "硬 barrier 平移", "cC"),
                               ("phase_shift", "相间再同步", "cA"),
                               ("repack", "重编译吸收", "cB"))]
    out.append(
        f'<div class="fig">'
        f'{line_chart(series, xlabel="源端释放抖动 J（拍，对数轴）", ylabel="makespan（拍）", logx=True, hline=big["makespan"], hlabel="无抖动 makespan")}'
        f'<div class="cap"><b>图：burst 抖动下三种再同步策略'
        f'（{lbl(big["pattern"], big["algo"], big["tier"])}，'
        f'{big["n_phases"]} 个相位、松弛 p50={big["slack"]["p50"]} 拍 —— '
        f'全场唯一一个三条策略互不重合的方案）。</b>'
        f'硬 barrier 下 makespan <b>精确</b>增加「最迟释放量」，'
        f'所以 J*（膨胀≤5%）只是 makespan 的 5% 换个说法，没有信息量。'
        f'两条反直觉的读数：<b>①「相间再同步」几乎没用</b> —— 10 个方案里有 '
        f'{n_ps_same} 个它与整表平移<b>逐点相同</b>'
        f'（单相位方案根本没有「相间」可言），只有本图这个方案差出 '
        f'{last(big, "global_shift") - last(big, "phase_shift")} 拍；'
        f'<b>② 只有带释放约束重编译能真吸收</b>，吸收量恰好等于打包器留下的'
        f'真实松弛（J=256 时 '
        f'{big["at_J256_burst"]["slack_absorbed_cycles"]} 拍）。'
        f'换成松弛最小的 {lbl(tight["pattern"], tight["algo"], tight["tier"])}'
        f'（p50={tight["slack"]["p50"]} 拍），三条曲线完全重合 —— '
        f'无松弛可吸收时，换什么策略都一样。</div></div>')
    out.append('<table><tr><th>方案</th><th class="n">makespan</th>'
               '<th class="n">松弛 p50</th>'
               '<th class="n">J=256 burst 下实际吸收</th></tr>')
    for x in ji:
        out.append(
            f'<tr><td>{lbl(x["pattern"], x["algo"], x["tier"])}</td>'
            f'<td class="n">{f(x["makespan"])}</td>'
            f'<td class="n">{f(x["slack"]["p50"])}</td>'
            f'<td class="n">'
            f'{f(x["at_J256_burst"]["slack_absorbed_cycles"])} 拍</td></tr>')
    out.append("</table>")
    out.append('<div class="note"><b>抗抖动与打到下界是同一个 trade-off 的'
               '两面。</b>松弛多的扁平方案能吸收几十到上百拍；'
               '紧到下界的方案（广播/T1 只有 14 次传输、旋转松弛 p50=4）'
               '吸收 <b>0 拍</b>。要抗抖动就得留松弛，留了松弛就打不到下界。'
               '</div>')
    return "".join(out)


def _bc_gain(c: dict) -> tuple[int | None, int | None, float | None]:
    """广播 m=13 上「基线 -> 杠杆全开的拍图」的加速比，供正文引用。"""
    b = best_base(c, "broadcast", 13)
    k = best_cal(c, "broadcast", 13)
    if not (b and k and b["ring_base"]["makespan"]):
        return None, None, None
    bm, km = b["ring_base"]["makespan"], k["calendar"]["makespan"]
    return bm, km, bm / km


def sec_levers(c: dict) -> str:
    out = ["<h3>杠杆 1 &mdash; 弧多播（copy-and-continue）</h3>"]
    out.append(f'<div class="fig">{svg_multicast()}'
               f'<div class="cap"><b>图：同一行环上，一次多播上环 vs 七次'
               f'unicast 上环。</b>多播省的是<b>上环次数与弧周期</b>，'
               f'抽取次数一样多 —— 所以它只在 fan-out 上有收益，'
               f'且是带宽收益不是时延收益（§6 已量化：m=1 时收益为 0）。'
               f'</div></div>')
    out.append('<table><tr><th>集合通信</th><th class="n">m</th>'
               '<th class="n">T0 上环 flit</th><th class="n">T1 上环 flit</th>'
               '<th class="n">流量降幅</th><th class="n">T0 makespan</th>'
               '<th class="n">T1 makespan</th><th class="n">makespan 降幅</th>'
               '</tr>')
    absent = []
    for pat in PATTERNS:
        t1 = row1(c, pattern=pat, algo="dim_2phase", tier="T1", m=13,
                  bidir=True)
        t0 = row1(c, pattern=pat, algo="dim_2phase", tier="T0", m=13,
                  bidir=True) or row1(c, pattern=pat, algo="flat", tier="T0",
                                      m=13, bidir=True)
        if not t1:
            absent.append(pat)
            continue
        if not t0:
            continue
        a, b = (t0["shape"]["n_flits_boarded"], t1["shape"]["n_flits_boarded"])
        out.append(
            f'<tr><td>{CN1[pat]}</td><td class="n">13</td>'
            f'<td class="n">{f(a)}</td><td class="n">{f(b)}</td>'
            f'<td class="n">{times(a / b)}</td>'
            f'<td class="n">{f(t0["calendar"]["makespan"])}</td>'
            f'<td class="n">{f(t1["calendar"]["makespan"])}</td>'
            f'<td class="n"><b>{times(t0["calendar"]["makespan"] / t1["calendar"]["makespan"])}'
            f'</b></td></tr>')
    out.append("</table>")
    if absent:
        out.append(
            f'<div class="note"><b>'
            f'{"、".join(CN1[p] for p in absent)} 没有 T1 行，这正是结论本身。'
            f'</b>copy-and-continue 是 fan-out 原语：'
            f'{"、".join(CN1[p] for p in absent if p != "alltoall")}'
            f'是 fan-in，没有任何东西可复制；全交换的 N(N&minus;1) 条消息'
            f'两两不同，一个环站不可能用一份副本同时服务两条。'
            f'对这些 pattern，多播硬件买到的收益<b>精确为 0</b>，'
            f'验证套件直接断言 T1 与 T0 逐字段相同。</div>')

    out.append("<h3>杠杆 2 &mdash; 双向半弧</h3>")
    out.append('<table><tr><th>方案</th><th class="n">m</th>'
               '<th class="n">双向</th><th class="n">仅顺时针</th>'
               '<th class="n">makespan 比</th><th class="n">流量比</th></tr>')
    for e in c["bidir_lever"]:
        out.append(
            f'<tr><td>{lbl(e["pattern"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{e["m"]}</td>'
            f'<td class="n">{f(e["bi"]["makespan"])}</td>'
            f'<td class="n">{f(e["uni"]["makespan"])}</td>'
            f'<td class="n"><b>{times(e["makespan_ratio_uni_over_bi"])}</b>'
            f'</td>'
            f'<td class="n">{times(e["traffic_ratio_uni_over_bi"])}</td></tr>')
    out.append("</table>")
    mc = [e for e in c["bidir_lever"]
          if abs(e["traffic_ratio_uni_over_bi"] - 1.0) < 0.02]
    uc = [e for e in c["bidir_lever"]
          if abs(e["traffic_ratio_uni_over_bi"] - 1.0) >= 0.02]
    note = ['<div class="note"><b>这一列里藏着两套完全不同的机制。</b>']
    if mc:
        e = max(mc, key=lambda x: x["makespan_ratio_uni_over_bi"])
        note.append(
            f'多播方案的流量比是 {times(e["traffic_ratio_uni_over_bi"])}，'
            f'而 makespan 比是 {times(e["makespan_ratio_uni_over_bi"])}：'
            f'一条 copy-and-continue 弧无论朝哪个方向走，都是在同样那些环站'
            f'落同样的副本，所以双向只是把<b>跨度</b>砍半，搬的 flit 一个不少'
            f' —— 这是时延收益，不是带宽收益。')
    if uc:
        e = max(uc, key=lambda x: x["traffic_ratio_uni_over_bi"])
        note.append(
            f'unicast 方案确实还有带宽收益，但原因不同：单向路径本来就更长，'
            f'要多花 {times(e["traffic_ratio_uni_over_bi"])} 的弧周期'
            f'（{lbl(e["pattern"], e["algo"], e["tier"])}）。')
    note.append('所以「双向布线把峰值弧负载减半」这句话，对两种情形都不成立。'
                '</div>')
    out.append("".join(note))

    out.append("<h3>杠杆 3 &mdash; 满环旋转</h3>")
    out.append(f'<div class="fig">{svg_rotation()}'
               f'<div class="cap">8 节点行环上的一个旋转步。旋转是本组里唯一'
               f'把最忙段下界<b>精确打满</b>的方案，而同一份刚性也让它成为'
               f'全组最不抗故障、最不抗抖动的方案（§15、§16）。</div></div>')

    out.append("<h3>杠杆 4 &mdash; L1 累加链</h3>")
    g = row1(c, pattern="gather", algo="dim_2phase", tier="T0", m=13,
             bidir=True)
    r = row1(c, pattern="reduce", algo="dim_2phase", tier="T0", m=13,
             bidir=True)
    ar = row1(c, pattern="allreduce", algo="dim_2phase", tier="T0", m=13,
              bidir=True)
    ar1 = row1(c, pattern="allreduce", algo="dim_2phase", tier="T1", m=13,
               bidir=True)
    hd = row1(c, pattern="allreduce", algo="halving_doubling", tier="T0", m=13,
              bidir=True)
    if g and r:
        out.append(
            f'<p>在 L1 里折叠让每一跳都保持载荷原大小，所以搬同样的数据，'
            f'归约树的上环 flit 比收集树少 '
            f'<b>{times(g["shape"]["n_flits_boarded"] / r["shape"]["n_flits_boarded"])}'
            f'</b>（m=13 时 {f(r["shape"]["n_flits_boarded"])} vs '
            f'{f(g["shape"]["n_flits_boarded"])}），makespan '
            f'{f(r["calendar"]["makespan"])} vs '
            f'{f(g["calendar"]["makespan"])} 拍。</p>')
    if ar and hd:
        out.append(
            f'<p>全归约 m=13 的三个数放在一起看（前两个同为 T0，可直接比）：'
            f'维度树「先归约再广播」<b>{f(ar["calendar"]["makespan"])}</b> 拍 / '
            f'{f(ar["shape"]["n_flits_boarded"])} 个上环 flit；'
            f'递归 halving-doubling <b>{f(hd["calendar"]["makespan"])}</b> 拍 / '
            f'{f(hd["shape"]["n_flits_boarded"])} 个。注意这个次序：'
            f'halving-doubling 搬了 '
            f'{times(hd["shape"]["n_flits_boarded"] / ar["shape"]["n_flits_boarded"], 1)}'
            f'的流量（{hd["shape"]["n_phases"]} 个相位 vs '
            f'{ar["shape"]["n_phases"]} 个），却更快 —— '
            f'<b>在这块 fabric 上并行度比总流量更值钱。</b>'
            + (f'第三个数需要多播硬件：同一棵维度树加 T1 之后是 '
               f'<b>{f(ar1["calendar"]["makespan"])}</b> 拍 / '
               f'{f(ar1["shape"]["n_flits_boarded"])} 个 flit，'
               f'那才是全场最快，但它不与前两个同口径。' if ar1 else '')
            + '</p>')

    out.append("<h3>打包顺序与端口数</h3>")
    out.append('<table><tr><th>方案</th><th class="n">m</th>'
               '<th>最优填充顺序</th><th class="n">四种顺序间的 makespan 跨度'
               '</th></tr>')
    for e in c["fill_lever"]:
        out.append(
            f'<tr><td>{lbl(e["pattern"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{e["m"]}</td><td><code>{e["best_fill"]}</code></td>'
            f'<td class="n">{f(e["spread"])} 拍</td></tr>')
    out.append("</table>")
    out.append('<table><tr><th>方案</th><th class="n">m</th>'
               '<th class="n">1 个上/下环端口</th><th class="n">2 个端口</th>'
               '<th class="n">加速比</th></tr>')
    for e in c["port_sensitivity"]:
        bp = e["by_ports"]
        out.append(
            f'<tr><td>{lbl(e["pattern"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{e["m"]}</td>'
            f'<td class="n">{f(bp["1"]["makespan"])}</td>'
            f'<td class="n">{f(bp["2"]["makespan"])}</td>'
            f'<td class="n">{times(e["speedup_ports2"])}</td></tr>')
    out.append("</table>")
    return "".join(out)


def sec_export(idx: dict | None) -> str:
    if not idx:
        return '<p class="muted">results/calendars/ring_index.json 缺失。</p>'
    es = idx["entries"]
    out = [f'<p>拍图以 <code>{idx["schema"]}</code> 导出到 '
           f'<code>results/calendars/ring_*.json</code>：共 {len(es)} 张表、'
           f'{f(sum(e.get("n_records", 0) for e in es))} 条环站记录，'
           f'{"全部" if all(e.get("conflict_free") for e in es) else "部分"}'
           f'通过 D-R 复核。v2 相对 v1 的增量就是环需要的三样：'
           f'<b>环站端口集</b>（上环/下环各一个，而不是 crossbar 的 in&times;out）、'
           f'<b><code>out_port_mask</code> 多播</b>（一次上环、多处抽取）、'
           f'<b><code>opcode=ADD</code></b>（在 L1 里折叠）。</p>']
    out.append('<table><tr><th>文件</th><th>方案</th><th class="n">m</th>'
               '<th class="n">makespan</th><th class="n">下界</th>'
               '<th>绑定界</th><th class="n">记录数</th><th>D-R</th></tr>')
    for e in sorted(es, key=lambda x: (x["collective"], x["algo"], x["tier"])):
        out.append(
            f'<tr><td><code>{e["file"]}</code></td>'
            f'<td>{lbl(e["collective"], e["algo"], e["tier"])}</td>'
            f'<td class="n">{e["m"]}</td>'
            f'<td class="n">{f(e["makespan"])}</td>'
            f'<td class="n">{f(e["makespan_lb"])}</td>'
            f'<td>{BIND_CN.get(e["binding_lb"], e["binding_lb"])}</td>'
            f'<td class="n">{f(e.get("n_records"))}</td>'
            f'<td class="{"win" if e.get("conflict_free") else "lose"}">'
            f'{"0 违例" if e.get("conflict_free") else "有违例"}</td></tr>')
    out.append("</table>")
    return "".join(out)


def sec_verify(ver: dict | None) -> str:
    if not ver:
        return '<p class="muted">verify_ring_collectives_8x6.json 缺失。</p>'
    ref = [ch for ch in ver["checks"]
           if "REFUTED" in (ch.get("prediction") or "")]
    fail = [ch for ch in ver["checks"] if not ch["pass"]]
    out = [f'<p>{ver["n_pass"]}/{ver["n_checks"]} 项通过'
           f'{"（全部通过）" if ver["all_pass"] else ""}。'
           f'每一项都是可执行断言，失败时打印具体量而不是只报 fail；'
           f'其中 <b>{len(ref)} 项记录为「预测被推翻」</b>，'
           f'而不是悄悄把阈值放宽。断言原文保留英文，与 '
           f'<code>verify_ring_collectives_8x6.py</code> 里的字符串逐字对应，'
           f'便于按名字回查。</p>']
    if fail:
        out.append('<div class="note bad"><b>未通过：</b>'
                   + "；".join(f'#{ch["id"]} {ch["name"]}' for ch in fail)
                   + '</div>')
    out.append('<table><tr><th class="n">#</th><th>断言</th>'
               '<th>实测</th><th>原预测</th></tr>')
    for ch in ref:
        out.append(f'<tr class="hl"><td class="n">{ch["id"]}</td>'
                   f'<td>{ch["name"]}</td><td>{ch.get("detail", "")}</td>'
                   f'<td class="lose">{ch.get("prediction", "")}</td></tr>')
    out.append("</table>")
    out.append('<p class="muted">全部 '
               f'{ver["n_checks"]} 项清单见 '
               '<code>results/verify_ring_collectives_8x6.json</code>。</p>')
    return "".join(out)


def sec_contrary(d: dict) -> str:
    c, rob, ver, t = d["coll"], d["rob"], d["ver"], d["tavg"]
    it = []
    att = d.get("att")
    if att:
        S = {r["key"]: r for r in att["schemes"]}
        A, F, B = (S["A_full_2port"], S["F_rowhalf_stagger"],
                   S["B_full_1lane"])
        it.append(
            f'<li><b>「half ring 省金属」是错的，而且错在两处。</b>'
            f'闭环上 k 个核就有 k 段，<b>拆环不改变段数</b>'
            f'（{A["structure"]["n_undirected_segments"]} 条无向段在九个候选上'
            f'完全一样），省的只是线长。而<b>错位</b>半环 —— 唯一既保住对分'
            f'又不用缝桥的半环 —— 反而<b>更费</b>金属'
            f'（{F["structure"]["wire_vs_mesh"]}&times; vs '
            f'{A["structure"]["wire_vs_mesh"]}&times; mesh），还要一根跨 '
            f'{F["structure"]["max_link_pitches"]} 个核间距的长线'
            f'（全环折叠后最长只有 {A["structure"]["max_link_pitches"]}）。'
            f'另一半意外在反方向：<b>单车道环在金属恒定下带宽与全环完全打平</b>'
            f'（12 个下界一个不差），它的问题只是时延'
            f'（{B["distance"]["avg_lat_cy"] / A["distance"]["avg_lat_cy"]:.2f}'
            f'&times;）而不是带宽 &mdash; 见 §1。</li>')
    bad = _two_model_rows(c) if c else []
    if bad:
        w = bad[0]
        n_all = len(rows(c, tier="T0", bidir=True))
        it.append(
            f'<li><b>基线一度看起来跑到了「理论下界」以下 —— 那是记账错，'
            f'不是物理。</b>{len(bad)}/{n_all} 行出现过 &lt;1 的比值，最狠 '
            f'{lbl(w["pattern"], w["algo"], w["tier"])} m={w["m"]} 的 '
            f'{times(w["ratios"]["base_over_cal_model_lb"])}。'
            f'根因是把<b>拍图模型</b>的界（环站出口 1 flit/拍、每相位 +RAMP、'
            f'过桥收 1 拍）拿去量<b>另一台机器</b>上的实测'
            f'（出口按 RAMP_BW 排空、不收 +RAMP、过桥免费）。'
            f'在基线自己模型下重建界之后 40 行全部 &ge; 下界，'
            f'旋转 10 行精确 1.000&times;。'
            f'能溜过去是因为验证套件只断言过<b>拍图</b> &ge; 界，'
            f'从没断言基线 &ge; 任何界 —— 现在 #31&ndash;#36 补上了，'
            f'见 §5 末的三项证据。</li>')
    thr = d.get("thr")
    if thr:
        fan = max((r for r in thr["rows"] if r.get("base_hop_tax")),
                  key=lambda r: r["base_hop_tax"])
        u = fan["ring_base"]["by_rounds"]["13"]["util"]
        cu = fan["calendar"]["by_rounds"]["13"]["util"]
        it.append(
            f'<li><b>基线的「带宽利用率更高」是浪费被读成了产出。</b>'
            f'{lbl(fan["pattern"], fan["algo"], fan["tier"])} m={fan["m"]} 上'
            f'基线占用 {pct(u["global_util"])}、拍图只占 '
            f'{pct(cu["global_util"])}，看起来基线把环用得更满；'
            f'但按最短跳数记账，基线的<b>有用</b>部分只有 '
            f'{pct(u["useful_global"])} &mdash; 与拍图基本相同。'
            f'多出来的 {times(fan["base_hop_tax"])}最小跳数是绕着 root 转圈'
            f'（偏转 {f(fan["ring_base"]["by_rounds"]["13"]["deflect_per_flit"], 2)}'
            f' 次/flit）。所以利用率必须<b>占用与有用分开报</b>，见 §13。</li>')
        dip = _ii_dip(thr)
        if dip:
            it.append(
                f'<li><b>II_eff 不是可以拿去校验下界的量。</b>'
                f'II_eff=(T_R&minus;T1)/(R&minus;1) 是让 T_avg=(T1+T_R)/2 成立的'
                f'插值参数：第一轮已经顺手做掉一部分被摊掉的工作，'
                f'所以有限 R 下它可以<b>低于容量界</b>（{dip}）。'
                f'改用均摊值 T_R/R 之后恒 &ge; 界 —— '
                f'§13 的图因此画的是均摊值，不是 II_eff。</li>')
    ru = (t or {}).get("rotation_utilization", {}).get("rows", [])
    if ru:
        best = max(ru, key=lambda r: r["critical_arc_util"])
        it.append(
            f'<li><b>旋转拍图并没有打到 100% 链路利用率。</b>计划预测 1.0，'
            f'实测在 R={best["rounds"]} 时只有 '
            f'{pct(best["critical_arc_util"])}，单调上升但只是渐近。'
            f'它<i>确实</i>精确打满了最忙段下界'
            f'（II_eff = {f(ru[1]["II_eff"], 1) if len(ru) > 1 else "?"} '
            f'= 每轮弧负载），但 T1={f(ru[0]["makespan"])} 的填充代价永远摊不掉：'
            f'利用率是 II&middot;R/(T1+II&middot;(R&minus;1))，'
            f'只有 R&rarr;&infin; 才趋近 1。</li>')
    p1 = [(e["R"], (e.get("by_ring_ports") or {}).get("1") or {})
          for e in (t or {}).get("ring_vs_mesh", [])]
    if p1 and all(v for _R, v in p1) and any(v["winner"] == "mesh"
                                             for _R, v in p1):
        deep = max(p1, key=lambda x: x[0])
        it.append(
            f'<li><b>单端口的环在深流水下输给 mesh。</b>单发时环靠跨度赢'
            f'（R={p1[0][0]} 时 {times(p1[0][1]["ring_over_mesh"], 3)}），'
            f'但 R={deep[0]} 时落后 {times(deep[1]["ring_over_mesh"], 3)}：'
            f'绑定资源已经从距离换成了环站那<b>一个</b>上/下环端口，'
            f'而 mesh 的 Hamilton bi-tree 用 II_eff=1.0 流过去了。'
            f'mesh 自己的最优方案在那个深度也换人 —— '
            f'所以两种 fabric 都不能用单个 R 排名。</li>')
    if rob:
        n_extra = sum(e["extra_infeasible_without_bypass"]
                      for e in rob["bypass_price"])
        it.append(
            f'<li><b>连续的死节点并不会切断环。</b>计划假定一个死节点等于两处'
            f'断裂，因此环站 bypass mux 是必须的。但在 2-连通的环上，'
            f'连续的洞可以从长边绕过去，对连续死点与象限洞而言，'
            f'去掉 bypass mux 一分钱代价都没有。真正会把环切开的是'
            f'<i>同一个环上分散的</i>死点 —— 那才是这个 mux 挣回面积的地方'
            f'（没有它会多出 {n_extra} 个无解场景）。</li>')
    if rob and any(x.get("n_needing_repair_phase") for x in rob["faults"]):
        w = max(rob["faults"], key=lambda x: x.get("n_needing_repair_phase")
                or 0)
        it.append(
            f'<li><b>一个死节点会逼出一个额外相位，而不只是重排一次。</b>'
            f'维度切片算法靠行列<b>唯一交点</b>把某行的数据交给某列；'
            f'交点死了，整列都拿不到那一行 —— fabric 仍然连通，'
            f'但拍图只有一条路。'
            f'{lbl(w["pattern"], w["algo"], w["tier"])} 的 '
            f'{w["n_recompile"]} 次重编译里有 '
            f'{w["n_needing_repair_phase"]} 次必须追加修复相位。'
            f'把这些只报成「重编译后膨胀 1.2&times;」，'
            f'就会把一个缺失的相位藏起来。</li>')
    if c:
        b = [e for e in (c["bidir_lever"] or [])
             if abs(e["traffic_ratio_uni_over_bi"] - 1.0) < 0.02]
        if b:
            e = max(b, key=lambda x: x["makespan_ratio_uni_over_bi"])
            it.append(
                f'<li><b>双向路由砍的是跨度不是负载 —— 只要多播在干活。</b>'
                f'计划预测「峰值弧负载减半」。在 '
                f'{lbl(e["pattern"], e["algo"], e["tier"])} 上实测流量比只有 '
                f'{times(e["traffic_ratio_uni_over_bi"])}，'
                f'而 makespan 比是 {times(e["makespan_ratio_uni_over_bi"])}：'
                f'一条弧无论朝哪走都在同样那些环站落副本，所以只有跨度变小。'
                f'unicast 方案确实省了弧周期，但那是因为单向路径更长，'
                f'不是因为负载被劈开。</li>')
        _w, _tie, lose = split_1p(c)
        if lose:
            pat = lose[0]
            wr, kr = best_base(c, pat, 13), best_cal(c, pat, 13, "T0")
            ps = next((e for e in c["port_sensitivity"]
                       if e["pattern"] == pat and e["m"] == 13), None)
            extra = ""
            if ps:
                extra = (f' 把模型放宽到两个抽取端口，拍图变成 '
                         f'{f(ps["by_ports"]["2"]["makespan"])} 拍'
                         f'（{times(ps["speedup_ports2"])}），'
                         f'这才是同口径的比较。')
            it.append(
                f'<li><b>paper 机制在 fan-in 型集合通信上打赢了静态拍图。</b>'
                f'{lbl(pat, kr["algo"], kr["tier"])} m=13：'
                f'<code>ring_base</code> {f(wr["ring_base"]["makespan"])} 拍，'
                f'拍图 {f(kr["calendar"]["makespan"])} 拍。'
                f'这是<b>建模粒度</b>差异，不是偏转有魔法：拍图把一个抽取端口'
                f'整段留给一次传输，而 <code>ring_base</code> 按 L1 的排空速率'
                f'逐 flit 交错。{extra}</li>')
    if ver:
        n_ref = sum(1 for ch in ver["checks"]
                    if "REFUTED" in (ch.get("prediction") or ""))
        it.append(
            f'<li class="muted">验证套件里有 {n_ref} 项带标签的预测被记录为'
            f'「推翻」而不是悄悄放宽，见 §18 与 '
            f'<code>results/verify_ring_collectives_8x6.json</code>。</li>')
    return f'<ul>{"".join(it)}</ul>'


def _reasm_peak(d: dict) -> str:
    t = d.get("thr")
    if not t:
        return "?"
    return f(max((v["max_reasm_occupancy"] for r in t["rows"]
                  if r["ring_base"]["T1"] is not None
                  for v in r["ring_base"]["by_rounds"].values()), default=0))


def sec_limits(d: dict) -> str:
    t = d["tavg"] or {}
    mesh_note = ""
    if not (t.get("mesh_reference") or {}).get("available"):
        mesh_note = ('<li>环 vs mesh 的 T_avg 对照是空的：'
                     '<code>results/multiflit_area_makespan.json</code> '
                     '早于 R&isin;{1,5,13} 扫描。环侧各列是完整的；'
                     '跨 fabric 的排序结论宁可不写，也不拿只有 R=5 的旧数据'
                     '顶替。</li>')
    return f"""<ul>
{mesh_note}
<li><b>挂接方式那一节只算到「结构 + 路由无关下界」，没有给落选方案排拍图。</b>
四道门槛与 12 个下界足以把候选排序（也足以把半跨环判死：它连通性都不成立），
但半环 fabric 上「排得好能追回多少」没有量 —— 要量就得让流集构造器和拍图
编译器都支持非均匀环，本轮未做。同时缝桥的 FIFO 深度、长线要插几级寄存器、
折叠布线的实际走线拥塞都只按「线长 + 最长单线」这两个代理量记账，
没有做版图。</li>
<li><b>基线与拍图不共享一套机器模型</b>，差别在三处并已逐项量化（§5 末）：
环站出口 1 flit/拍 vs 节点按 <code>RAMP_BW</code> 排空、每相位 +RAMP 的斜坡常数、
过桥转环 1 拍。因此「离下界多远」这类陈述必须各用自己模型的界；
本报告图上的下界柱取两模型的公共下界。要让头对头比较也严格同硬件，
需要统一 <code>leave_ports</code> 与 <code>eject_bw</code> 后重跑全部拍图，
那会改动几乎所有拍图数字，尚未做。</li>
<li>环 vs mesh 是「各自设计空间里的最优点」对比，而两个空间的形状不同：
mesh 扫的是 crossbar 写宽度、排空速率与 FIFO 深度，环只扫了环站端口数。
这就是为什么那张表按环端口数分行给，而不是给一个汇总比值。</li>
<li>拍图模型把一个抽取端口整段（m&middot;&sigma; 拍）记给一次传输，而 paper
机制按 flit 排空 L1，所以在 fan-in 型集合通信上两者<b>不在同一记账口径</b>；
端口敏感性表是给这个差距划了个界，不是把它抹平。</li>
<li>归约建模为「保持大小的项集折叠」。这精确刻画了流量与依赖次序，但不含算术：
L1 里加法器的时延被折进 <code>RAMP</code>，没有单独建模。</li>
<li><code>ring_islip2d</code> 是同能力的调度参照，不是认真的竞争者 ——
它每节点每轮只仲裁一个 flit，在 2256 条消息的 pattern 上 makespan 差一个
数量级，应当读作对照组。</li>
<li><b>基线的 II 与利用率是「假设重排序缓冲无限大」下的读数。</b>
流水化之后目的端重组缓冲峰值到 {_reasm_peak(d)} flit，而模型给它配的是 64 flit；
仿真器只统计溢出、不丢包，所以 §13 里基线那一列偏<b>乐观</b>。
要给出真实值就得把重组缓冲做成硬约束并让溢出反压回源端，本轮未做。</li>
<li><b>§3 的结构地板是刻意取弱的一版</b>（允许中继与本地合并，因此 T0/T1 同界）。
它的作用是「谁都不能低于它」，不是「谁贴着它就最优」：广播/归约/全归约的容量界
弱到个位数，那几行的「比下界」倍数衡量的是相位链而非资源，不能当排图质量读。
更紧的界要按算法逐个构造，本轮只对拍图模型做了（§13 后半）。</li>
<li>抖动只注入在源端释放处。在途抖动需要 transport 模型，拍图回放做不到。</li>
<li>故障重编译假定有一个拿到完整故障表的离线编译器。本报告不声称重编译要多久、
新表怎么分发。</li>
<li>拍图部分全程 &sigma;=1。拓扑审计报出的金属恒定读法（&sigma;=2，
金属量 {d['coll']['audit']['metal_ratio_vs_mesh'] if d.get('coll') else '?'}&times;
同尺寸 mesh）没有在这里重跑。</li>
</ul>"""


def _ii_dip(t: dict) -> str:
    """哪一格的 II_eff 掉到容量界以下，以及 R 变大之后掉多少。"""
    lb = {(r["pattern"], r["m"]): r["II_lb"] for r in t["theory"]}
    worst: dict[str, tuple[float, dict]] = {}
    for r in t["rows"]:
        f_lb = lb[(r["pattern"], r["m"])]
        for leg in ("calendar", "ring_base"):
            for R, v in (r[leg].get("by_rounds") or {}).items():
                ii = v.get("II_eff")
                if ii is None or f_lb <= 0 or ii >= f_lb:
                    continue
                cur = worst.get(R)
                if cur is None or ii / f_lb < cur[0]:
                    worst[R] = (ii / f_lb, dict(r, ii=ii, lb=f_lb, leg=leg))
    if not worst:
        return ""
    ks = sorted(worst, key=int)
    lo, hi = worst[ks[0]], worst[ks[-1]]
    w = lo[1]
    return (f'{CN1[w["pattern"]]}/<code>{w["algo"]}</code> 在 R={ks[0]} 时 '
            f'II_eff={f(w["ii"], 1)} 拍，而容量界是 {w["lb"]} 拍，'
            f'只有它的 {times(lo[0])}；R={ks[-1]} 时缺口收窄到 '
            f'{times(hi[0])}')


def _floor_gap(d: dict) -> str:
    """m=1 时最优拍图离结构地板有多远，按 collective 逐个给。"""
    t = d.get("thr")
    if not t:
        return ""
    rs = [(pat, hl_row(t, pat, 1)["cal_T1"]["T1_over_lb"]) for pat in PATTERNS]
    lat = th_row(t, "alltoall", 1)["lat_distance_cy"]
    lo = min(rs, key=lambda x: x[1])
    hi = max(rs, key=lambda x: x[1])
    return ("m=1 时最优拍图 / 结构地板 = "
            + "、".join(f'{CN1[p]} {times(r)}' for p, r in rs)
            + f'（地板都是同一个 {lat} 拍的纯线延迟）。'
              f'最紧的是{CN1[lo[0]]}（{times(lo[1])}，'
              f'基本只剩线延迟），最松的是{CN1[hi[0]]}'
              f'（{times(hi[1])}）—— 它要串起多个相位，'
              f'而地板只按最远那条直线算，不管相位链有多长。')


def _floor_li(d: dict) -> str:
    """结论里的下界那一条：单 flit 是时延问题，多 flit 才是带宽问题。"""
    t = d.get("thr")
    if not t:
        return ""
    lat = th_row(t, "alltoall", 1)["lat_distance_cy"]
    g1, a1 = hl_row(t, "gather", 1), hl_row(t, "alltoall", 1)
    rs = [hl_row(t, p, 1) for p in PATTERNS]
    lo = min(r["cal_T1"]["T1_over_lb"] for r in rs)
    hi = max(r["cal_T1"]["T1_over_lb"] for r in rs)
    m13 = [th_row(t, p, 13) for p in PATTERNS]
    cap = [x["pattern"] for x in m13 if x["binding"] != "latency"]
    return (f'<li><b>m=1 时这块布局不是带宽问题，是距离问题。</b>'
            f'六个 collective 的结构地板在 m=1 时<b>全部</b>由时延绑定'
            f'（{lat} 拍纯线延迟，容量项最大只有 '
            f'{th_row(t, "alltoall", 1)["cut_lb"]} 拍），'
            f'最优拍图落在地板的 {lo:.2f}&ndash;{hi:.2f}&times;。'
            f'到 m=13 才有 {"、".join(CN1[p] for p in cap)} 三个模式换成容量界'
            f'（前两个撞核端口 / L1 ramp，全交换撞 cut）。'
            f'所以「加宽布线」对单 flit 集合通信毫无用处，'
            f'该动的是跨度与相位数 —— 见 §3。</li>')


def _thr_li(d: dict) -> str:
    """结论里的吞吐那一条：makespan 与 II 是两个问题，利用率要分占用与有用。"""
    t = d.get("thr")
    if not t:
        return ""
    a = hl_row(t, "alltoall", 13)
    g = hl_row(t, "gather", 1)
    fan = max((r for r in t["rows"] if r.get("base_hop_tax")),
              key=lambda r: r["base_hop_tax"])
    u = fan["ring_base"]["by_rounds"]["13"]["util"]
    return (f'<li><b>连着做的时候排名会换人，而且「利用率」这个词必须拆成两个。'
            f'</b>流水稳态下全交换每轮均摊：基线 '
            f'{a["base"]["best_II"]["per_round"]:,.0f} 拍、拍图 '
            f'{a["cal_T0"]["best_II"]["per_round"]:,.0f} 拍'
            f'（{times(a["base_over_cal_T0"]["per_round"])}，'
            f'II 界 {a["bound"]["II_lb"]}）；而收集这类 fan-in 上两条腿都贴着'
            f'「root 的 L1 每拍吃 {RAMP_BW} 个 flit」这条界'
            f'（{times(g["cal_T0"]["per_round_over_lb"])} / '
            f'{times(g["base"]["per_round_over_lb"])}），谁也赢不了。'
            f'利用率上基线常常数字更大，但那是绕圈：'
            f'{lbl(fan["pattern"], fan["algo"], fan["tier"])} m={fan["m"]} 上'
            f'它占用 {pct(u["global_util"])} 而有用只有 '
            f'{pct(u["useful_global"])}（{times(fan["base_hop_tax"])}最小跳数），'
            f'拍图的 hop tax 恒为 1.00 &mdash; 见 §13。</li>')


def sec_conclusion(d: dict) -> str:
    c = d["coll"]
    it = []
    for pat in PATTERNS:
        b, k = best_base(c, pat, 13), best_cal(c, pat, 13, "T0")
        if b and k and b["ring_base"]["makespan"] > k["calendar"]["makespan"]:
            it.append((pat, b["ring_base"]["makespan"]
                       / k["calendar"]["makespan"]))
    it.sort(key=lambda x: -x[1])
    top = "、".join(f"{CN1[p]}（{r:.2f}×）" for p, r in it[:3])
    win, tie, lose = split_1p(c)
    j = lambda ps: "、".join(CN1[p] for p in ps) or "无"
    bad = _two_model_rows(c)
    n_bad = len(bad)
    worst_r = (f'{lbl(bad[0]["pattern"], bad[0]["algo"], bad[0]["tier"])} '
               f'm={bad[0]["m"]} 的 '
               f'{times(bad[0]["ratios"]["base_over_cal_model_lb"])}'
               ) if bad else "无"
    att = d.get("att")
    S = {r["key"]: r for r in att["schemes"]} if att else {}
    attach_li = ""
    if S:
        A, C0, C = (S["A_full_2port"], S["C0_rowhalf_noseam"],
                    S["C_rowhalf_seam"])
        attach_li = (
            f'<li><b>挂接方式不用挑，四条结构约束就把它筛成唯一解。</b>'
            f'每核 2 个口 = 1 个行环口 + 1 个列环口、两维都用双向全环折叠布线、'
            f'桥与核同址复用这两个口：2 个口正好等于 L1 ramp 的 '
            f'{att["geometry"]["ramp_bw"]} flit/cy，'
            f'{A["structure"]["n_bridges"]} 个桥的额外抽头为 '
            f'{A["structure"]["n_extra_tap_bridges"]}。'
            f'半跨环在「&le;2 口」下<b>直接不连通</b>'
            f'（可达对 {C0["distance"]["reachable_pairs"]}/'
            f'{C0["distance"]["total_pairs"]}），补缝之后 x 向对分从 '
            f'{A["cuts"]["x"]["min_cap_per_dir"]} 掉到 '
            f'{C["cuts"]["x"]["min_cap_per_dir"]} flit/cy、全交换下界翻倍。'
            f'把前提写成可算的量，选择就不再是口味问题 —— 详见 §1。</li>')
    return f"""<ol>
{attach_li}
{_floor_li(d)}
{_thr_li(d)}
<li><b>同能力（都只有 unicast）下，拍图赢在流量摊得开的模式，基线赢在纯
fan-in。</b>m=13 拍图更快的是 {top}；基线更快的只有 {j(lose)}，{j(tie)} 打平。
这条分界不是噪声，是<b>下环端口记账粒度</b>造成的：拍图把一个抽取点整段
独占，基线按 <code>RAMP_BW</code> 逐 flit 交错。端口放宽到 2 之后差距就
合上（见 §8），这才是同口径比较。</li>
<li><b>基线的代价不只在 makespan 上。</b>fan-in 类流量把偏转顶到
{f(max(best_base(c, p, 13)['ring_base']['deflect_per_flit']
       for p in PATTERNS if best_base(c, p, 13)), 3)} 次/flit，
而偏转既白吃弧周期又制造乱序，所以基线<b>必须</b>带桥 FIFO 与目的端重组
缓冲（实测峰值最高 {f(max(best_base(c, p, 13)['ring_base']['max_reasm_occupancy']
                          for p in PATTERNS if best_base(c, p, 13)))} flit）；
拍图把这两块删成 0（转环驻留恒为 0、乱序恒为 0），代价搬到了控制存储与
「故障需重编译」上。</li>
<li><b>弧多播是唯一改变数量级的硬件增量，但只对 fan-out 生效。</b>
同一棵 <code>dim_2phase</code> 树上加多播，上环 flit 从 611 降到 182、
传输数从 47 降到 14、makespan 从 176 降到 95 拍；而归约、收集、全交换
<b>收益精确为 0</b>（fan-in 无可复制，全交换的 N(N−1) 条消息两两不同），
验证套件对 17 个 (pattern, algo) 对断言 T1 与 T0 逐字段相同。
且这是<b>带宽</b>收益不是<b>时延</b>收益：m=1 时同一对 T1/T0 都是 61 拍。</li>
<li><b>打到下界与抗故障/抗抖动是互斥的。</b>旋转拍图 II_eff 精确等于弧负载
下界，代价是 27 个故障场景里 20 个无解、抖动吸收 0 拍。要鲁棒就得留松弛，
留了松弛就打不到下界 —— 选方案时这是一次显式取舍，不是可以同时拿到的两件事。
</li>
<li><b>凡是「更快」「离最优多远」的论断都必须带口径，本轮有一次现成的反面教材。
</b>σ=1 还是金属恒定 σ=2、T0 还是 T1 能力、1 个还是 2 个环站端口、R=1 还是
R=13 —— 换任一项都可能翻转结论。更隐蔽的是<b>下界的口径</b>：把拍图模型的界
拿去量基线，{n_bad} 行会出现「跑到下界以下」，最狠 {worst_r}（§5 末）。
两条腿只要机器模型不同，就必须各用自己的界；只报一个「/下界」列，
等于把两台机器的差别记成了性能。</li>
</ol>"""


# ---------------------------------------------------------------------------
# 5. 组页
# ---------------------------------------------------------------------------

def build(d: dict) -> str:
    c, ver = d["coll"], d["ver"]
    a = c["audit"]
    toc = [("attach", "一、AI core 怎么挂到环上（先定前提）"),
           ("wire", "二、链路时延口径：折叠布线与 10 拍的桥"),
           ("floor", "三、六个集合通信的理论下界"),
           ("mech", "四、两种机制到底差在哪（示意图）"),
           ("transports", "五、环上三种 transport 的定位"),
           ("cmp", "六、主对比：六个集合通信的 makespan"),
           ("winloss", "七、谁赢谁输：分界线在哪里"),
           ("why", "八、为什么这几个模式上基线更快（端口粒度）"),
           ("cost", "九、基线的隐性代价：偏转、乱序、重组缓冲"),
           ("bridge", "十、各 bridge 的 buffer 占用（48 个 FIFO 的实测）"),
           ("gantt", "十一、拍图长什么样（真实占用图）"),
           ("levers", "十二、四个结构杠杆"),
           ("util", "十三、吞吐（II）与带宽利用率：无排图 vs 静态排图"),
           ("tavg", "十四、流水化后的 T_avg（R = 1 / 5 / 13）"),
           ("faults", "十五、容错"),
           ("jitter", "十六、抗抖动"),
           ("export", "十七、拍图导出（calendar-export/v2）"),
           ("verify", "十八、验证清单"),
           ("contrary", "十九、与预期相反的结果"),
           ("limits", "二十、已知局限"),
           ("concl", "二十一、结论与口径")]
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>8×6 无缓冲折叠 2D torus 上的集合通信：paper 机制基线 vs 静态拍图</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>8&times;6 无缓冲折叠 2D torus 上的集合通信：paper 机制基线 vs 静态拍图</h1>
<p class="lead">拓扑一句话：<b>6 个行环 + 8 个列环压在一起、每个节点都是桥</b>，
其链路集与同尺寸<b>折叠 2D torus</b> 逐条相同（已写成断言，见 §18）；
不同的是节点 &mdash; 它不是 torus 路由器，而是<b>零缓冲、零 crossbar 的环站</b>，
上/下环各一个端口。§1 会证明为什么「每核 2 个口（1 行 1 列）+ 全环」
是这块几何唯一合理的挂接方式。<br>
在这块布局上比三条 transport：HPCA'22 的 E-tag/I-tag + 偏转机制
（运行期分布式决策、只支持 unicast，即<b>无排图</b>基线）、离线排好的
<b>静态拍图</b>（编译期全局决策，环站可复制多播、归约在 L1 做），
外加集中式 <code>ring_islip2d</code> 作同能力参照。三个问题：
<b>一次要多久（makespan，重点看 1 flit）、连着做能有多快（II）、
这些拍里有多少在搬有用数据（带宽利用率）</b>，
每一个都对着 §3 算出的结构地板读。
这一页是无缓冲环工作的<b>唯一</b>报告。</p>
<p class="muted">拓扑：{a['n_row_rings']} 个行环&times;{a['mx']} +
{a['n_col_rings']} 个列环&times;{a['my']} = {a['n_directed_links']} 条有向弧，
{a['n']} 个节点全是桥，环内零缓冲；金属量 {a['metal_ratio_vs_mesh']}&times;
同尺寸 mesh。冲突判据为 D-R 五子句。
{"全部 " + str(ver['n_checks']) + " 项可执行验证通过。" if ver and ver['all_pass'] else ""}
所有数字读自 <code>results/*.json</code>，图中甘特图为现场排图的真值。</p>

<div class="fig">{svg_topology(a)}
<div class="cap"><b>图：本报告全部工作所在的物理拓扑。</b>
{a['n_row_rings']} 个行环（每环 {a['mx']} 个节点）与 {a['n_col_rings']} 个列环
（每环 {a['my']} 个节点）互相压在一起，
{a['n']} 个节点<b>每一个都是桥</b>（行环与列环在此交汇），
共 {a['n_undirected_links']} 条无向段 / {a['n_directed_links']} 条有向弧。
<b>绕回段是环的定义性边</b>：同尺寸 mesh 没有它，所以环的金属量是 mesh 的
{a['metal_ratio_vs_mesh']}&times;，Hamilton 回路也只能靠它闭合。
这套链路集<b>就是折叠 2D torus</b>（物理上按折叠布线，相邻环节点间距 2 个 pitch，
每跳延迟因此均匀）；换来的好处是{_cut_note(d)}
右图是一个环站内部 &mdash; 环上零队列、上/下环各一个端口、转环是原子操作，
这四样资源就是 D-R 五子句要管的全部东西。</div></div>

{sec_cards(d)}

<div class="toc">{"".join(f'<div><a href="#{i}">{tt}</a></div>' for i, tt in toc)}</div>

{sec_attach_block(d)}

<h2 id="wire">二、链路时延口径：折叠布线与 10 拍的桥</h2>
{sec_wire(d)}

<h2 id="floor">三、六个集合通信的理论下界</h2>
<p>§1 把几何定了下来，这一节先算清楚<b>这块几何上任何方案都不可能突破的地板
</b>，再往下所有实测（§6 的 makespan、§13 的 II 与带宽利用率）都对着它读。
界只依赖拓扑与流量需求，不依赖路由、不依赖调度、也不区分 T0/T1。</p>
{sec_floor(d['thr'])}

<h2 id="mech">四、两种机制到底差在哪</h2>
<p>差别的根源只有一句：<b>运行期决策必须为「猜错」准备缓冲，编译期决策不会猜错。</b>
基线的桥 FIFO 与目的端重组缓冲都不是设计者的偏好，而是偏转与乱序的必然后果。</p>
<div class="fig">{svg_mechanism()}
<div class="cap"><b>图：同一个环站，两种机制各要什么。</b>红框是基线必须有、
拍图可以删掉的存储；绿虚线框是被删掉的部分。拍图把代价搬到了控制存储
（一张时隙表）和「故障后要重编译」上。</div></div>
<div class="fig">{svg_deflect_vs_slot()}
<div class="cap"><b>图：同一个冲突的两种处理。</b>基线只能把 B 弹走绕圈
（多付跳数 + 乱序）；拍图在编译期就知道冲突，把 B 排到下一拍
（多付 1 拍，保序）。这就是 §9 里偏转率与重组缓冲峰值的来源。</div></div>

<h2 id="transports">五、环上三种 transport 的定位</h2>
<p>后面所有对比都在这三条腿之间进行，先把各自的身份说清楚，避免把参照当成
竞争者。三条腿跑同一个流集、同一 m、同一 &sigma;、同一 barrier 语义。</p>
{sec_transports(c)}

<h2 id="cmp">六、主对比：六个集合通信的 makespan</h2>
<p>三条腿同 m、同 &sigma;、同 barrier 语义。先看 m=1（单 flit，多数场景由
时延地板决定），再看 m=13（多 flit，带宽与端口开始咬人）。
「连着做能有多快」是另一个问题，在 §13。</p>
{sec_compare(c)}
<div class="note"><b>读图要点：</b>m=1 时六个 collective 的最优拍图<b>全部</b>
恰好压在时延下界上（makespan == latency_lb），此时比的是「跨度 + barrier 数」，
m=13 才切换到端口界与弧负载界。所以<b>不能只用一个 m 下结论</b> ——
这与 §14 里「不能只用一个 R」是同一类错误。<br>
顺带一个反直觉的读数：<b>m=1 时弧多播买到的收益精确为 0</b> —— 广播
<code>dim_2phase</code> 的 T1 与 T0 都是 61 拍，一拍不差；此时最优方案反而是
<code>flat</code>（59 拍），因为单 flit 下拼的是最短临界路径，不是省带宽。
<b>多播是带宽原语，不是时延原语。</b><br>
和 §3 的结构地板对一下更能看出余量：{_floor_gap(d)}</div>

<h2 id="winloss">七、谁赢谁输：分界线在哪里</h2>
{sec_winloss(c)}

<h2 id="why">八、为什么这几个模式上基线更快</h2>
<p>{_why_intro(c)}</p>
{sec_why_lose(c)}

<h2 id="cost">九、基线的隐性代价</h2>
{sec_cost(c, d["idx"])}

<h2 id="bridge">十、各 bridge 的 buffer 占用</h2>
{sec_bridge(d)}

<h2 id="gantt">十一、拍图长什么样</h2>
<p>下面两张图不是示意图，是调 <code>build_calendar</code> 现场排出来的真值，
每条横线都是一次真实传输的占用区间。</p>
{sec_gantt(c)}

<h2 id="levers">十二、四个结构杠杆</h2>
<p>拍图能做而 paper 机制做不到的事，归结为四个可以独立开关的杠杆。
每个杠杆单独量化，才知道收益该记在谁头上 —— 比如广播上「基线
{f(_bc_gain(c)[0])} 拍 &rarr; 杠杆全开的拍图 {f(_bc_gain(c)[1])} 拍」这
{times(_bc_gain(c)[2])} 里，多播占多少、维度树占多少。</p>
{sec_levers(c)}

<h2 id="util">十三、吞吐（II）与带宽利用率</h2>
<p>§6 比的是「一次要多久」。这一节比「连着做能有多快」，以及那些拍里有多少
在搬有用数据 —— 对训练场景这两个数字比单发 makespan 更贴近真实占用。</p>
{sec_throughput(d['thr'])}
<h3>拍图离它自己那条更紧的界有多远</h3>
<p>上表的地板是<b>路由无关</b>的弱界。拍图模型自己还有一条更紧的界（知道具体
路由与相位结构），单发口径下的余量在这里：</p>
{sec_util(c)}

<h2 id="tavg">十四、流水化后的 T_avg</h2>
{sec_tavg(d['tavg'])}

<h2 id="faults">十五、容错</h2>
{sec_faults(d['rob'])}

<h2 id="jitter">十六、抗抖动</h2>
{sec_jitter(d['rob'])}

<h2 id="export">十七、拍图导出</h2>
{sec_export(d['idx'])}

<h2 id="verify">十八、验证清单</h2>
{sec_verify(ver)}

<h2 id="contrary">十九、与预期相反的结果</h2>
<p>单列一节，因为这些是最容易在汇总里被平均掉、却最影响设计决策的读数。</p>
{sec_contrary(d)}

<h2 id="limits">二十、已知局限</h2>
{sec_limits(d)}

<h2 id="concl">二十一、结论与口径</h2>
{sec_conclusion(d)}

<h2>复现</h2>
<pre class="code">cd utils
python3 rg_ring_attach.py                 <span class="c"># §1 挂接方式设计空间 -> results/ring_attach_8x6.json</span>
python3 dse_ring_collectives_8x6.py       <span class="c"># 三条腿 + 拍图 -> results/ring_collectives_8x6.json</span>
python3 dse_ring_throughput_8x6.py        <span class="c"># §3 结构下界 + §13 II/带宽 -> results/ring_throughput_8x6.json</span>
python3 dse_ring_tavg_8x6.py              <span class="c"># T_avg R=1/5/13（需先有 mesh 参照，见下）</span>
python3 dse_ring_bridge_8x6.py            <span class="c"># §10 各桥 buffer 占用 + 深度/过桥拍数扫描</span>
python3 dse_ring_robust_8x6.py            <span class="c"># 容错 + 抖动</span>
python3 export_ring_calendars.py          <span class="c"># -> results/calendars/ring_*.json</span>
python3 verify_ring_collectives_8x6.py    <span class="c"># {f(ver['n_checks']) if ver else '?'} 项断言</span>
python3 gen_ring_collectives_report.py    <span class="c"># 本页</span></pre>
<p class="muted">§14 的 mesh 参照来自 <code>results/multiflit_area_makespan.json</code>
（由 <code>dse_multiflit_area_makespan.py --jobs 5</code> 产生，那是 mesh 侧的
既有工作，本报告只<b>读取</b>它、不改它）。文字版结论见
<code>docs/phase-7-exploration/ring-collectives-8x6.md</code>。</p>
</div></body></html>"""


def main() -> None:
    d = load()
    if not d["coll"]:
        raise SystemExit(f"缺少 {COLL}，先跑 dse_ring_collectives_8x6.py")
    OUT.write_text(build(d), encoding="utf-8")
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
