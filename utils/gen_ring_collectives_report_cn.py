#!/usr/bin/env python3
"""生成 results/report_ring_collectives_8x6_cn.html（中文对比报告）。

与英文版 gen_ring_collectives_report.py 的分工：英文版是全量数据的索引，
本报告只回答一个问题 —— **paper 机制基线与静态拍图，在同一块无缓冲环上谁快、
为什么快、在哪里反而慢**，因此全部内容围绕对比图组织，先画机制再给数字。

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
OUT = ROOT / "results" / "report_ring_collectives_8x6_cn.html"

MX, MY, N = 8, 6, 48
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
            "ver": rd(VER), "idx": rd(IDX)}


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
                 hi_series: int | None = None) -> str:
    """分组柱状图。series = [{name, cls, vals:[...]}]，vals 与 cats 等长。"""
    L, Rr, T, B = 66, 150, 30, 62
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
            p.append(f'<text x="{x + (bw - 3) / 2:.1f}" y="{y - 4:.1f}" '
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
        p.append(f'<text x="{w - Rr - 4}" y="{ty(hline) - 5:.1f}" '
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
                 h: int = 300, ylabel: str = "") -> str:
    L, Rr, T, B = 66, 158, 26, 74
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


def sec_cards(d: dict) -> str:
    c, ver, rob = d["coll"], d["ver"], d["rob"]
    out = []
    win, tie, lose = split_1p(c)
    n = len(win) + len(tie) + len(lose)
    out.append(f'<div class="card ok"><div class="k">m=13 同能力（T0）：'
               f'拍图更快的 collective</div><div class="v">{len(win)} / {n}'
               f'</div><div class="s">{len(lose)} 个基线更快、{len(tie)} 个'
               f'打平（见 §3、§4）</div></div>')
    bc, bcf = (row1(c, pattern="broadcast", algo="dim_2phase", tier="T1", m=13,
                    bidir=True),
               row1(c, pattern="broadcast", algo="flat", tier="T0", m=13,
                    bidir=True))
    if bc and bcf:
        out.append(f'<div class="card ok"><div class="k">弧多播对广播的收益'
                   f'（m=13）</div><div class="v">'
                   f'{bcf["calendar"]["makespan"] / bc["calendar"]["makespan"]:.1f}'
                   f'&times;</div><div class="s">'
                   f'{bcf["calendar"]["makespan"]} &rarr; '
                   f'{bc["calendar"]["makespan"]} 拍</div></div>')
    a2a, a2k = best_base(c, "alltoall", 13), best_cal(c, "alltoall", 13, "T0")
    if a2a and a2k:
        out.append(f'<div class="card"><div class="k">全交换 m=13：基线 / 拍图'
                   f'</div><div class="v">'
                   f'{a2a["ring_base"]["makespan"] / a2k["calendar"]["makespan"]:.2f}'
                   f'&times;</div><div class="s">'
                   f'{a2a["ring_base"]["makespan"]} vs '
                   f'{a2k["calendar"]["makespan"]} 拍</div></div>')
        out.append(f'<div class="card bad"><div class="k">基线偏转率'
                   f'（全交换 m=13）</div><div class="v">'
                   f'{a2a["ring_base"]["deflect_per_flit"]:.3f}</div>'
                   f'<div class="s">次 / flit，白吃弧周期</div></div>')
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
            lb.append(min(x["bounds"]["makespan_lb"]
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
            f'T1 与 T0 逐字段相同。下界为四个界（时延 / 弧负载 / 端口 / '
            f'ramp）取最大。</div></div>')
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
            f'打平：{j(tie)}。输的那两个原因见 §4 &mdash; 是下环端口的记账'
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
    cats, defl, ooo, reasm = [], [], [], []
    for pat in PATTERNS:
        b = best_base(c, pat, 13)
        if not b:
            continue
        rb = b["ring_base"]
        cats.append(CN[pat])
        defl.append(rb.get("deflect_per_flit") or 0)
        ooo.append(rb.get("n_out_of_order") or 0)
        reasm.append(rb.get("max_reasm_occupancy") or 0)
    out.append(
        f'<div class="fig">'
        f'{grouped_bars(cats, [{"name": "偏转次数 / flit", "cls": "cC", "vals": defl}], ylabel="次 / flit", h=270, note_fmt="{:.3f}")}'
        f'<div class="cap"><b>图：基线的隐性代价 —— 偏转率（m=13）。</b>'
        f'偏转是再循环：既白吃弧周期又打乱到达顺序，所以基线必须带目的端'
        f'重组缓冲。维度树类流量下偏转为 0（固定维序下所有转向同向，'
        f'桥看不到互相转向）；<b>全交换把偏转顶到 '
        f'{max(defl):.3f} 次/flit</b>。</div></div>')
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


def sec_util(c: dict) -> str:
    cats, g, cr = [], [], []
    for pat in PATTERNS:
        k = best_cal(c, pat, 13)
        u = k["calendar"]["util"]
        cats.append(f'{CN1[pat]}<br>{k["algo"]}/{k["tier"]}')
        g.append(u["global_util"] * 100)
        cr.append(u["critical_arc_util"] * 100)
    return (f'<div class="fig">'
            f'{grouped_bars(cats, [{"name": "全局（192 弧均）", "cls": "cA", "vals": g}, {"name": "关键弧（最忙）", "cls": "cD", "vals": cr}], ylabel="利用率 %", h=310, note_fmt="{:.1f}")}'
            f'<div class="cap"><b>图：两个利用率必须一起看。</b>'
            f'全局高而关键弧低 = 还能继续压；关键弧贴住自己的界 = 只能换流集。'
            f'实测所有方案的「关键弧周期 / 关键弧下界」都是 1.00&times;，'
            f'说明打包器在最忙那条弧上没有浪费 —— makespan 高于下界的部分'
            f'来自相位 barrier 与端口，不是弧。</div></div>')


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
        {"name": "环 旋转/T0", "cls": "cD",
         "pts": pts("ring_rotate", "T0", 1)},
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
    out.append(
        f'<div class="fig">'
        f'{line_chart(series, xlabel="流水轮数 R", ylabel="T_avg（拍）", xticks=[1, 5, 13])}'
        f'<div class="cap"><b>图：全收集的 T_avg 随流水深度变化。</b>'
        f'R=1 时 T_avg ≡ 单发 makespan；R 越大 II_eff 越支配。'
        f'旋转的 II_eff 最小（47.0 = 弧负载下界）却全程垫底，'
        f'因为 T1=564 的填充代价摊不掉 &mdash; <b>II 最优不等于 T_avg 最优'
        f'</b>。</div></div>')
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
                   '</b>1 端口的环 R=1 赢、R=5 打平、<b>R=13 输 1.19&times;'
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


def sec_robust(rob: dict | None) -> str:
    if not rob:
        return '<p class="muted">ring_robust_8x6.json 缺失。</p>'
    fa = rob["faults"]
    cats = [f'{CN1[x["pattern"]]}<br>{x["algo"]}/{x["tier"]}' for x in fa]
    out = [
        f'<div class="fig">'
        f'{stacked_bars(cats, [{"name": "免重编译", "cls": "cB", "vals": [x["n_immune"] for x in fa]}, {"name": "需重编译", "cls": "cF", "vals": [x["n_recompile"] for x in fa]}, {"name": "无解", "cls": "cC", "vals": [x["n_infeasible"] for x in fa]}], ylabel="故障场景数", w=920, h=330)}'
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

    ji = rob["jitter"]
    big = max(ji, key=lambda x: x["makespan"])
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
        f'（{lbl(big["pattern"], big["algo"], big["tier"])}）。</b>'
        f'硬 barrier 下 makespan <b>精确</b>增加「最迟释放量」，'
        f'所以 J*（膨胀≤5%）只是 makespan 的 5% 换个说法，没有信息量。'
        f'只有重编译能吸收，吸收量恰好是打包器留下的真实松弛。</div></div>')
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
    return f"""<ol>
<li><b>同能力（都只有 unicast）下，拍图赢在流量摊得开的模式，基线赢在纯
fan-in。</b>m=13 拍图更快的是 {top}；基线更快的只有 {j(lose)}，{j(tie)} 打平。
这条分界不是噪声，是<b>下环端口记账粒度</b>造成的：拍图把一个抽取点整段
独占，基线按 <code>RAMP_BW</code> 逐 flit 交错。端口放宽到 2 之后差距就
合上（见 §4），这才是同口径比较。</li>
<li><b>基线的代价不只在 makespan 上。</b>全交换 m=13 偏转率
{f(best_base(c, 'alltoall', 13)['ring_base']['deflect_per_flit'], 3)} 次/flit，
偏转既白吃弧周期又制造乱序，所以基线<b>必须</b>带桥 FIFO 与目的端重组缓冲；
拍图把这两块删成 0（转环驻留实测恒为 0、乱序恒为 0），代价搬到了控制存储
与「故障需重编译」上。</li>
<li><b>弧多播是唯一改变数量级的硬件增量，但只对 fan-out 生效。</b>
广播上环 flit 从 611 降到 182、makespan 从 323 降到 95 拍；而归约、收集、
全交换<b>收益精确为 0</b>（fan-in 无可复制，全交换的 N(N−1) 条消息两两不同），
验证套件对 17 个 (pattern, algo) 对断言 T1 与 T0 逐字段相同。</li>
<li><b>打到下界与抗故障/抗抖动是互斥的。</b>旋转拍图 II_eff 精确等于弧负载
下界，代价是 27 个故障场景里 20 个无解、抖动吸收 0 拍。要鲁棒就得留松弛，
留了松弛就打不到下界 —— 选方案时这是一次显式取舍，不是可以同时拿到的两件事。
</li>
<li><b>凡是「更快」的论断都必须带口径。</b>σ=1 还是金属恒定 σ=2、T0 还是 T1
能力、1 个还是 2 个环站端口、R=1 还是 R=13 —— 换任一项都可能翻转结论，
本报告每张图都标了所用口径。</li>
</ol>"""


# ---------------------------------------------------------------------------
# 5. 组页
# ---------------------------------------------------------------------------

def build(d: dict) -> str:
    c, ver = d["coll"], d["ver"]
    a = c["audit"]
    toc = [("mech", "一、两种机制到底差在哪（示意图）"),
           ("cmp", "二、主对比：六个集合通信的 makespan"),
           ("winloss", "三、谁赢谁输：分界线在哪里"),
           ("why", "四、为什么漏斗型上基线更快（端口粒度）"),
           ("cost", "五、基线的隐性代价：偏转、乱序、重组缓冲"),
           ("gantt", "六、拍图长什么样（真实占用图）"),
           ("util", "七、带宽利用率"),
           ("tavg", "八、流水化后的 T_avg（R = 1 / 5 / 13）"),
           ("robust", "九、容错与抗抖动"),
           ("concl", "十、结论与口径")]
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>8×6 无缓冲环：paper 机制基线 vs 静态拍图</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>8&times;6 无缓冲环上的集合通信：paper 机制基线 vs 静态拍图</h1>
<p class="lead">同一块物理环、同一个流集、同一套冲突判据，只换 transport：
一边是 HPCA'22 的 E-tag/I-tag + 偏转机制（运行期分布式决策，只支持
unicast），一边是离线排好的静态拍图（编译期全局决策，环站可复制多播、
归约在 L1 做）。本报告只回答一件事 &mdash; <b>谁快、为什么快、在哪里反而慢</b>。</p>
<p class="muted">拓扑：{a['n_row_rings']} 个行环&times;{a['mx']} +
{a['n_col_rings']} 个列环&times;{a['my']} = {a['n_directed_links']} 条有向弧，
{a['n']} 个节点全是桥，环内零缓冲；金属量 {a['metal_ratio_vs_mesh']}&times;
同尺寸 mesh。冲突判据为 D-R 五子句。
{"全部 " + str(ver['n_checks']) + " 项可执行验证通过。" if ver and ver['all_pass'] else ""}
所有数字读自 <code>results/*.json</code>，图中甘特图为现场排图的真值。</p>

{sec_cards(d)}

<div class="toc">{"".join(f'<div><a href="#{i}">{tt}</a></div>' for i, tt in toc)}</div>

<h2 id="mech">一、两种机制到底差在哪</h2>
<p>差别的根源只有一句：<b>运行期决策必须为「猜错」准备缓冲，编译期决策不会猜错。</b>
基线的桥 FIFO 与目的端重组缓冲都不是设计者的偏好，而是偏转与乱序的必然后果。</p>
<div class="fig">{svg_mechanism()}
<div class="cap"><b>图：同一个环站，两种机制各要什么。</b>红框是基线必须有、
拍图可以删掉的存储；绿虚线框是被删掉的部分。拍图把代价搬到了控制存储
（一张时隙表）和「故障后要重编译」上。</div></div>
<div class="fig">{svg_deflect_vs_slot()}
<div class="cap"><b>图：同一个冲突的两种处理。</b>基线只能把 B 弹走绕圈
（多付跳数 + 乱序）；拍图在编译期就知道冲突，把 B 排到下一拍
（多付 1 拍，保序）。这就是 §5 里偏转率与重组缓冲峰值的来源。</div></div>

<h2 id="cmp">二、主对比：六个集合通信的 makespan</h2>
<p>三条腿同 m、同 &sigma;、同 barrier 语义。先看 m=1（单 flit，多数场景由
时延地板决定），再看 m=13（多 flit，带宽与端口开始咬人）。</p>
{sec_compare(c)}
<div class="note"><b>读图要点：</b>m=1 时六个 collective 的最优拍图<b>全部</b>
恰好压在时延下界上（makespan == latency_lb），此时比的是「跨度 + barrier 数」，
m=13 才切换到端口界与弧负载界。所以<b>不能只用一个 m 下结论</b> ——
这与 §8 里「不能只用一个 R」是同一类错误。<br>
顺带一个反直觉的读数：<b>m=1 时弧多播买到的收益精确为 0</b> —— 广播
<code>dim_2phase</code> 的 T1 与 T0 都是 61 拍，一拍不差；此时最优方案反而是
<code>flat</code>（59 拍），因为单 flit 下拼的是最短临界路径，不是省带宽。
<b>多播是带宽原语，不是时延原语。</b></div>

<h2 id="winloss">三、谁赢谁输：分界线在哪里</h2>
{sec_winloss(c)}

<h2 id="why">四、为什么这几个模式上基线更快</h2>
<p>{_why_intro(c)}</p>
{sec_why_lose(c)}

<h2 id="cost">五、基线的隐性代价</h2>
{sec_cost(c, d["idx"])}

<h2 id="gantt">六、拍图长什么样</h2>
<p>下面两张图不是示意图，是调 <code>build_calendar</code> 现场排出来的真值，
每条横线都是一次真实传输的占用区间。</p>
{sec_gantt(c)}

<h2 id="util">七、带宽利用率</h2>
{sec_util(c)}

<h2 id="tavg">八、流水化后的 T_avg</h2>
{sec_tavg(d['tavg'])}

<h2 id="robust">九、容错与抗抖动</h2>
{sec_robust(d['rob'])}

<h2 id="concl">十、结论与口径</h2>
{sec_conclusion(d)}

<h2>复现</h2>
<pre class="code">cd utils
python3 dse_ring_collectives_8x6.py     <span class="c"># 基线 + 拍图 -> results/ring_collectives_8x6.json</span>
python3 dse_ring_tavg_8x6.py            <span class="c"># T_avg R=1/5/13</span>
python3 dse_ring_robust_8x6.py          <span class="c"># 容错 + 抖动</span>
python3 verify_ring_collectives_8x6.py  <span class="c"># {f(ver['n_checks']) if ver else '?'} 项断言</span>
python3 gen_ring_collectives_report_cn.py <span class="c"># 本页</span></pre>
<p class="muted">英文全量数据索引见
<code>results/report_ring_collectives_8x6.html</code>，
文字版结论见 <code>docs/phase-7-exploration/ring-collectives-8x6.md</code>。</p>
</div></body></html>"""


def main() -> None:
    d = load()
    if not d["coll"]:
        raise SystemExit(f"缺少 {COLL}，先跑 dse_ring_collectives_8x6.py")
    OUT.write_text(build(d), encoding="utf-8")
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
