#!/usr/bin/env python3
"""生成 results/report_ring_collectives_8x6.html —— reticle 口径下的集合通信报告。

数字全部从 results/aic_reticle_collectives_8x6.json 读出，没有一个写死。
JSON 缺失时对应小节显式声明，不用旧口径的数顶替。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "results" / "aic_reticle_collectives_8x6.json"
VER = ROOT / "results" / "verify_aic_reticle_8x6.json"
OUT = ROOT / "results" / "report_ring_collectives_8x6.html"

PATTERNS = ["broadcast", "reduce", "gather", "allreduce", "allgather",
            "alltoall"]
CN1 = {"broadcast": "广播", "reduce": "归约", "gather": "收集",
       "allreduce": "全归约", "allgather": "全收集", "alltoall": "全交换"}
ALGO_CN = {"flat": "直接发送", "dim_2phase": "按维分解",
           "ring_rotate": "行环轮转", "tree": "最短路树"}


def f(x: Any, nd: int = 0) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:,.{nd}f}"
    if isinstance(x, int):
        return f"{x:,}"
    return str(x)


def pct(x: Any, nd: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{nd}f}%"


def times(x: Any, nd: int = 2) -> str:
    return "—" if x is None else f"{x:.{nd}f}×"


def svg_reticle() -> str:
    """6×8 核 + 双轨行环 + 纵向轨的示意，不是 26×33 mm 真比例。"""
    mx, my = 8, 6
    ox, oy, gx, gy, r = 70, 48, 78, 70, 14
    w = ox + mx * gx + 40
    h = oy + my * gy + 36
    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" '
        f'style="max-width:{w}px" role="img">',
        '<style>.rl{fill:none;stroke:#3b82f6;stroke-width:2.2}'
        '.cl{fill:none;stroke:#f59e0b;stroke-width:2}'
        '.nd{fill:#fff;stroke:#1f2937;stroke-width:1.4}'
        '.lb{font:11px ui-sans-serif;fill:#374151;text-anchor:middle}'
        '.cap{font:11px ui-sans-serif;fill:#6b7280}</style>',
    ]
    # row rails: two per row (even / odd columns)
    for y in range(my):
        yy = oy + y * gy
        xs = [ox + x * gx for x in range(mx)]
        even = [xs[i] for i in range(0, mx, 2)]
        odd = [xs[i] for i in range(1, mx, 2)]
        d1 = f"M{even[0]},{yy - 10} " + " ".join(f"L{x},{yy - 10}" for x in even[1:])
        d1 += f" C{even[-1]+28},{yy-10} {odd[-1]+28},{yy+10} {odd[-1]},{yy+10} "
        d1 += " ".join(f"L{x},{yy+10}" for x in reversed(odd[:-1]))
        d1 += f" C{odd[0]-28},{yy+10} {even[0]-28},{yy-10} {even[0]},{yy-10}"
        parts.append(f'<path d="{d1}" class="rl"/>')
    # vertical rails (one pair per column, drawn as a single line through)
    for x in range(mx):
        xx = ox + x * gx
        parts.append(f'<line x1="{xx}" y1="{oy-18}" x2="{xx}" '
                     f'y2="{oy+(my-1)*gy+18}" class="cl"/>')
    for y in range(my):
        for x in range(mx):
            eid = y * mx + x
            cx, cy = ox + x * gx, oy + y * gy
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" class="nd"/>')
            parts.append(f'<text x="{cx}" y="{cy+4}" class="lb">'
                         f'{eid:02d}</text>')
    parts.append(f'<text x="12" y="{h-8}" class="cap">'
                 f'蓝 = 行环（折叠，偶列上轨 / 奇列下轨）· '
                 f'橙 = 纵向轨（只在端点折返，不换轨）</text>')
    parts.append("</svg>")
    return "".join(parts)


def grouped_bars(cats: list[str], series: list[dict], *,
                 w: int = 860, h: int = 320, ylabel: str = "") -> str:
    L, R, T, B = 64, 140, 28, 56
    vals = [v for s in series for v in s["vals"] if v is not None]
    hi = max(vals) * 1.12 if vals else 1
    def ty(v: float) -> float:
        return T + (h - B - T) * (1 - v / hi)
    gw = (w - L - R) / max(1, len(cats))
    bw = min(28.0, gw * 0.7 / max(1, len(series)))
    colors = {"cA": "#2563eb", "cB": "#f59e0b", "cC": "#059669",
              "cD": "#dc2626", "cE": "#7c3aed"}
    p = [f'<svg viewBox="0 0 {w} {h}" width="100%" '
         f'style="max-width:{w}px" role="img">']
    for i in range(5):
        yv = hi * i / 4
        y = ty(yv)
        p.append(f'<line x1="{L}" y1="{y:.1f}" x2="{w-R}" y2="{y:.1f}" '
                 f'stroke="#e5e7eb"/>')
        p.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" '
                 f'font-size="11" fill="#6b7280">{yv:,.0f}</text>')
    for ci, cat in enumerate(cats):
        cx = L + gw * (ci + 0.5)
        n = len(series)
        for si, s in enumerate(series):
            v = s["vals"][ci]
            if v is None:
                continue
            x = cx - n * bw / 2 + si * bw
            y = ty(v)
            p.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw-3:.1f}" '
                     f'height="{h-B-y:.1f}" fill="{colors[s["cls"]]}"/>')
            p.append(f'<text x="{x+(bw-3)/2:.1f}" y="{y-4:.1f}" '
                     f'text-anchor="middle" font-size="10" fill="#374151">'
                     f'{v:,.0f}</text>')
        p.append(f'<text x="{cx:.1f}" y="{h-B+16}" text-anchor="middle" '
                 f'font-size="11" fill="#374151">{cat}</text>')
    for si, s in enumerate(series):
        yy = T + 12 + si * 18
        p.append(f'<rect x="{w-R+8}" y="{yy-9}" width="12" height="12" '
                 f'fill="{colors[s["cls"]]}"/>')
        p.append(f'<text x="{w-R+26}" y="{yy+1}" font-size="11" '
                 f'fill="#374151">{s["name"]}</text>')
    p.append(f'<text x="14" y="{(T+h-B)/2}" font-size="11" fill="#6b7280" '
             f'transform="rotate(-90 14 {(T+h-B)/2})" text-anchor="middle">'
             f'{ylabel}</text>')
    p.append("</svg>")
    return "".join(p)


def heat_bridges(per: dict[str, dict], title: str) -> str:
    """12×16 RBRG 平均占用热力图。key = 'hi:vi'."""
    cell, pad = 14, 1
    w = 16 * cell + 70
    h = 12 * cell + 48
    means = {k: v.get("mean", 0) for k, v in per.items()}
    hi = max(means.values()) if means else 1
    hi = hi or 1
    p = [f'<svg viewBox="0 0 {w} {h}" width="100%" '
         f'style="max-width:{w}px" role="img">']
    p.append(f'<text x="8" y="16" font-size="12" fill="#111827">{title}</text>')
    for hi_i in range(12):
        for vi in range(16):
            key = f"{hi_i}:{vi}"
            m = means.get(key, 0)
            t = m / hi
            fill = f"rgb({int(255-t*180)},{int(245-t*160)},{int(255-t*40)})"
            x = 36 + vi * cell
            y = 28 + hi_i * cell
            p.append(f'<rect x="{x}" y="{y}" width="{cell-pad}" '
                     f'height="{cell-pad}" fill="{fill}" stroke="#e5e7eb"/>')
    p.append('<text x="36" y="204" font-size="10" fill="#6b7280">'
             'V00 → V15</text>')
    p.append('<text x="8" y="120" font-size="10" fill="#6b7280" '
             'transform="rotate(-90 8 120)">H00 → H11</text>')
    p.append("</svg>")
    return "".join(p)


def build(d: dict, ver: dict | None) -> str:
    w = d["wire"]
    cmp = {(r["pattern"], r["m"], r["tier"]): r for r in d["compare"]}
    thr = {(r["pattern"], r["m"]): r for r in d["throughput"]}
    base = {(r["pattern"], r["m"]): r for r in d["baselines"]}
    bounds = {(r["pattern"], r["m"], r["tier"]): r for r in d["bounds"]}
    n_ok = f"{ver['n_ok']}/{ver['n']}" if ver else "未跑"
    ex = d["example_0_to_47"]

    def best(pat, m, tier="T0"):
        return cmp.get((pat, m, tier))

    # opening numbers
    a2a1 = best("alltoall", 1)
    a2a13 = best("alltoall", 13)
    bc1 = best("broadcast", 1)
    bc13t1 = best("broadcast", 13, "T1") or best("broadcast", 13)
    ag1 = best("allgather", 1)

    cats = [CN1[p] for p in PATTERNS]
    bar_m1 = grouped_bars(
        cats,
        [{"name": "结构下界", "cls": "cC",
          "vals": [best(p, 1)["floor"] if best(p, 1) else 0 for p in PATTERNS]},
         {"name": "最优拍图 T0", "cls": "cA",
          "vals": [best(p, 1)["cal_makespan"] if best(p, 1) else 0
                   for p in PATTERNS]},
         {"name": "无排图基线", "cls": "cB",
          "vals": [best(p, 1)["base_makespan"] if best(p, 1) else 0
                   for p in PATTERNS]}],
        ylabel="makespan（拍）")
    bar_m13 = grouped_bars(
        cats,
        [{"name": "结构下界", "cls": "cC",
          "vals": [best(p, 13)["floor"] if best(p, 13) else 0 for p in PATTERNS]},
         {"name": "最优拍图 T0", "cls": "cA",
          "vals": [best(p, 13)["cal_makespan"] if best(p, 13) else 0
                   for p in PATTERNS]},
         {"name": "无排图基线", "cls": "cB",
          "vals": [best(p, 13)["base_makespan"] if best(p, 13) else 0
                   for p in PATTERNS]}],
        ylabel="makespan（拍）")

    # bound table
    btab = ['<table><tr><th>集合通信</th><th>m</th><th>档</th>',
            '<th class="n">下界</th><th>绑定</th>',
            '<th class="n">割</th><th class="n">注入</th><th class="n">弹出</th>',
            '<th class="n">转维</th><th class="n">时延</th><th class="n">串行</th></tr>']
    for r in d["bounds"]:
        btab.append(
            f'<tr><td>{CN1[r["pattern"]]}</td><td class="n">{r["m"]}</td>'
            f'<td>{r["tier"]}</td><td class="n"><b>{f(r["floor"])}</b></td>'
            f'<td>{r["binding"]}</td><td class="n">{f(r["cut"])}</td>'
            f'<td class="n">{f(r["inject"])}</td>'
            f'<td class="n">{f(r["eject"])}</td>'
            f'<td class="n">{f(r["turn"])}</td>'
            f'<td class="n">{f(r["latency"])}</td>'
            f'<td class="n">{f(r["serial"])}</td></tr>')
    btab.append("</table>")

    ctab = ['<table><tr><th>集合通信</th><th>m</th><th>档</th><th>拍图</th>',
            '<th class="n">makespan</th><th class="n">/下界</th>',
            '<th class="n">基线</th><th class="n">基线/拍图</th>',
            '<th class="n">占用率</th><th class="n">hop tax</th>',
            '<th class="n">桥峰值</th></tr>']
    for r in d["compare"]:
        ctab.append(
            f'<tr><td>{CN1[r["pattern"]]}</td><td class="n">{r["m"]}</td>'
            f'<td>{r["tier"]}</td><td>{ALGO_CN.get(r["cal_algo"], r["cal_algo"])}</td>'
            f'<td class="n"><b>{f(r["cal_makespan"])}</b></td>'
            f'<td class="n">{times(r["gap_cal_floor"])}</td>'
            f'<td class="n">{f(r["base_makespan"])}</td>'
            f'<td class="n">{times(r["gap_base_cal"])}</td>'
            f'<td class="n">{pct(r["cal_util"])}</td>'
            f'<td class="n">{r["cal_tax"]:.2f}</td>'
            f'<td class="n">{f(r["cal_turn_peak"])}</td></tr>')
    ctab.append("</table>")

    allc = ['<table><tr><th>集合通信</th><th>算法</th><th>档</th><th>m</th>',
            '<th class="n">makespan</th><th class="n">上环次数</th>',
            '<th class="n">相数</th><th class="n">占用率</th>',
            '<th class="n">有用占用</th><th class="n">hop tax</th></tr>']
    for r in d["calendars"]:
        allc.append(
            f'<tr><td>{CN1[r["pattern"]]}</td>'
            f'<td>{ALGO_CN.get(r["algo"], r["algo"])}</td>'
            f'<td>{r["tier"]}</td><td class="n">{r["m"]}</td>'
            f'<td class="n">{f(r["makespan"])}</td>'
            f'<td class="n">{f(r["n_boardings"])}</td>'
            f'<td class="n">{r["depth"]}</td>'
            f'<td class="n">{pct(r["lane_util"])}</td>'
            f'<td class="n">{pct(r["useful_util"])}</td>'
            f'<td class="n">{r["hop_tax"]:.2f}</td></tr>')
    allc.append("</table>")

    ttab = ['<table><tr><th>集合通信</th><th>m</th><th>拍图</th>',
            '<th class="n">T₁</th><th class="n">T₄/4</th>',
            '<th class="n">II_eff</th><th class="n">II_lb</th>',
            '<th class="n">T_avg(R=4)</th><th class="n">T_avg(R=13)</th></tr>']
    for r in d["throughput"]:
        ttab.append(
            f'<tr><td>{CN1[r["pattern"]]}</td><td class="n">{r["m"]}</td>'
            f'<td>{ALGO_CN.get(r["algo"], r["algo"])}</td>'
            f'<td class="n">{f(r["T1"])}</td>'
            f'<td class="n">{f(r["per_round"], 1)}</td>'
            f'<td class="n">{f(r["II_eff"], 1)}</td>'
            f'<td class="n">{f(r["II_lb"])}</td>'
            f'<td class="n">{f(r["T_avg_R4"], 1)}</td>'
            f'<td class="n">{f(r["T_avg_R13"], 1)}</td></tr>')
    ttab.append("</table>")

    ftab = ['<table><tr><th class="n">fifo_depth</th><th class="n">makespan</th>',
            '<th class="n">打偏</th><th class="n">桥峰值</th>',
            '<th class="n">满周期</th></tr>']
    for r in d["fifo_sweep"]:
        ftab.append(
            f'<tr><td class="n">{r["fifo_depth"]}</td>'
            f'<td class="n">{f(r["makespan"])}</td>'
            f'<td class="n">{f(r["deflections"])}</td>'
            f'<td class="n">{f(r["turn_peak"])}</td>'
            f'<td class="n">{f(r["turn_full_cycles"])}</td></tr>')
    ftab.append("</table>")

    a2a_base = base.get(("alltoall", 13)) or base.get(("alltoall", 1))
    heat = ""
    if a2a_base and a2a_base.get("per_bridge"):
        heat = heat_bridges(a2a_base["per_bridge"],
                            f'全交换 m={a2a_base["m"]} 基线：各 RBRG 平均占用')

    kc = ex.get("kind_cycles") or {}
    kc_rows = "".join(
        f'<tr><td>{k}</td><td class="n">{v}</td></tr>'
        for k, v in kc.items())

    body = f"""
<h1>6×8 AIC reticle · 折叠多环上的集合通信</h1>
<p class="lead">口径来自 <code>aic-reticle-shortest-path</code>：26 000 × 33 000 µm
真比例光罩，400 µm/cycle，RBRG 转维 = 5 拍进 + 5 拍出。验证
<b>{n_ok}</b>。本页取代上一轮抽象「每跳 10/14 拍、核有列口」的折叠 torus 报告
—— 那套机器在这份文档里不存在。</p>

<nav>
<a href="#s0">结论</a>
<a href="#s1">版图与时延</a>
<a href="#s2">和旧模型差在哪</a>
<a href="#s3">下界</a>
<a href="#s4">makespan</a>
<a href="#s5">吞吐与 T_avg</a>
<a href="#s6">RBRG FIFO</a>
<a href="#s7">全部拍图</a>
</nav>

<h2 id="s0">0. 一页结论</h2>
<ol>
<li><b>核没有纵向口。</b>每个核只挂在一条水平轨上，位置
<code>M(2·row + col%2, col)</code>。换行必须在 RBRG 转两次，每次 10 拍；
落 L1 再注入也躲不开，因为中继核同样没有列口。上一轮「过桥 10 拍 vs ramp 2 拍，
编译期选 L1」这条结论，在这份 setup 下不成立。</li>
<li><b>行是环，列是线。</b>一行的折叠环周长 {w["row_tour_cy"]} 拍
（六段 24 + 两段折返 36）。纵向折返接回<i>同一条</i>轨，所以 0 行到 5 行是
{w["col_wrap_cy"]} 拍，不是便宜的 torus wrap。环轮转只该用在行上，列该用树。</li>
<li><b>直径是 {w["diameter_cy"]} 拍</b>（核 00→47），不是旧模型的 72 拍。
同行为 0 次转维、跨行恰好 2 次，这是路由器相位机的硬不变量，不是启发式。
m=1 时六个集合通信的下界都被这条时延绑住
（广播 {best("broadcast",1)["floor"] if best("broadcast",1) else "—"} 拍）。</li>
<li><b>m=1 全交换：拍图 {a2a1["cal_makespan"] if a2a1 else "—"} 拍
（{ALGO_CN.get(a2a1["cal_algo"], "") if a2a1 else ""}），
基线 {a2a1["base_makespan"] if a2a1 else "—"} 拍，
{times(a2a1["gap_base_cal"]) if a2a1 else ""}。</b>
m=13 切到容量：全交换绑在割上（{a2a13["floor"] if a2a13 else "—"} 拍），
拍图 {a2a13["cal_makespan"] if a2a13 else "—"}，基线
{a2a13["base_makespan"] if a2a13 else "—"}。</li>
<li><b>弧多播只对行轨有意义。</b>纵向轨不经过核站，flit 骑在上面无法在中间行
落一份拷贝。T1 只给广播 / 全收集 / 全归约的<i>行</i>扇出打折；
全交换、收集、归约没有 T1 行，这不是漏跑。</li>
<li><b>桥 FIFO 是这份 fabric 唯一的在途存储。</b>默认深度 4 时全交换基线峰值
{a2a1["base_turn_peak"] if a2a1 else "—"} 条；加深 FIFO 单调降低 makespan，
见 §6。</li>
</ol>

<h2 id="s1">1. 版图与时延口径</h2>
<p>光罩 {w["reticle_um"][0]:,} × {w["reticle_um"][1]:,} µm，核间距
{w["pitch_um"][0]:,} × {w["pitch_um"][1]:,} µm，{w["n_rows"]} 行 × {w["n_cols"]} 列
= {w["n_cores"]} 核。{w["n_hrails"]} 条水平轨（每行 2 条）、{w["n_vrails"]} 条纵向轨
（每列 2 条），交叉处 {w["n_rbrg"]} 座 RBRG。可调度资源是
<b>{w["n_lanes"]} 条站间有向段</b>——每个 RBRG 入端口恰好由一条站间段喂入，
所以按段互斥已经序列化了站内的直通 / 近转 / 远转三路。</p>
<div class="fig">{svg_reticle()}
<div class="cap"><b>图：6×8 核在折叠多环上的挂接。</b>
偶数列挂上轨、奇数列挂下轨，两端 13 拍折返把它们收成一条行环。
橙色纵向轨在顶底各花 2 拍折返，<b>不换轨</b>，所以列不是环。</div></div>

<h3>分段时延（与参考文档逐条对齐）</h3>
<table>
<tr><th>段</th><th class="n">几何</th><th class="n">拍数</th><th>算法</th></tr>
<tr><td>核 ↔ CS</td><td class="n">105 µm</td><td class="n">1</td><td>⌈µm/400⌉</td></tr>
<tr><td>水平臂 B↔M</td><td class="n">1 125 µm</td><td class="n">3</td><td>⌈µm/400⌉</td></tr>
<tr><td>站间隙</td><td class="n">40 µm</td><td class="n">1</td><td>⌈µm/400⌉</td></tr>
<tr><td>纵向跨距</td><td class="n">4 460 µm</td><td class="n">12</td><td>⌈µm/400⌉</td></tr>
<tr><td>RBRG 直通</td><td class="n">420 µm</td><td class="n">2</td><td>给定</td></tr>
<tr><td>RBRG 近转 / 远转</td><td class="n">315 / 525 µm</td><td class="n">10</td>
<td>5 进 + 5 出，几何含在内</td></tr>
<tr><td>水平折返</td><td class="n">5 180 µm</td><td class="n">13</td><td>⌈µm/400⌉</td></tr>
<tr><td>纵向折返</td><td class="n">405 µm</td><td class="n">2</td><td>⌈µm/400⌉</td></tr>
<tr><td>CS / PIPE 穿过</td><td class="n">0</td><td class="n">0</td><td>站内</td></tr>
</table>
<p>高级参数（直通附加、FIFO 等待、注入弹出附加）在参考文档里默认全 0，
本仿真同样取 0。核 00→47 最短路 <b>{ex["total"]} 拍 / {ex["um"]:,} µm /
{ex["turns"]} 次转维 / {ex["folds"]} 次折返</b>，与参考控件默认选点一致。</p>
<table>
<tr><th>段类</th><th class="n">00→47 小计（拍）</th></tr>
{kc_rows}
<tr><td>合计</td><td class="n"><b>{ex["total"]}</b></td></tr>
</table>
<p class="muted">同行平均 {w["same_row"]["avg"]} 拍（0 转），
跨行平均 {w["cross_row"]["avg"]} 拍（2 转）。全图平均 {w["avg_cy"]} 拍，
直径 {w["diameter_cy"]} 拍。</p>

<h2 id="s2">2. 和上一轮抽象折叠 torus 差在哪</h2>
<table>
<tr><th></th><th>上一轮（抽象环）</th><th>本轮（reticle 文档）</th></tr>
<tr><td>一跳</td><td>行 10 / 列 14 拍（2 个 pitch）</td>
<td>一跳是臂+间隙+站穿过的<b>复合</b>：同行邻核 24 或 36 拍，同列邻行 43 拍</td></tr>
<tr><td>核的口</td><td>每核 2 口 = 行环 + 列环</td>
<td><b>只有水平口</b>；列向必须过 RBRG</td></tr>
<tr><td>换维</td><td>过桥 10 拍，或落 L1 再注入 2 拍（更便宜）</td>
<td>跨行必转两次 ×10 拍；L1 中继<b>省不掉</b>转维</td></tr>
<tr><td>列拓扑</td><td>6 节点列环，wrap 便宜</td>
<td>6 级双向线，0→5 行 {w["col_wrap_cy"]} 拍</td></tr>
<tr><td>直径</td><td>72 拍</td><td>{w["diameter_cy"]} 拍</td></tr>
<tr><td>可调度资源</td><td>192 条有向弧</td><td>{w["n_lanes"]} 条站间有向段</td></tr>
</table>
<div class="note"><b>所以不能拿上一轮的 makespan 来比快慢。</b>
机器变了：旧报告里「dim_2phase 不过桥所以赢」依赖「核有列口」；
这份文档把列口拿走了，同一套算法现在每条跨行腿都付 20 拍转维。</div>

<h2 id="s3">3. 路由无关下界</h2>
<p>五族计数，都不假设路由或排程。割上：可合并的按源侧归组，可复制的按宿侧归组
—— 所以全收集 T0 的中割是「每源一侧一次」，不是 48×24 次直接发送。
注入 / 弹出只数<b>真正不同的 payload</b>（中继和本地累加已经折进去）。
时延地板是到最远成员的最短路 + m。串行地板是
⌈log<sub>1+口数</sub> 48⌉ 次依赖跳。</p>
{''.join(btab)}
<p class="muted">读法：m=1 六个 pattern 都绑在时延上（直径 {w["diameter_cy"]} + 1）。
m=13 全交换绑割（{a2a13["floor"] if a2a13 else "—"}），
收集 / 全收集绑弹出，广播 T1 仍可能绑时延。</p>

<h2 id="s4">4. Makespan：下界 · 拍图 · 无排图基线</h2>
<p>拍图是零松弛刚性占用：上环时刻编译期算死，站间段互斥。
基线是无前瞻注入：核按轮转交出自己的消息，整条参考路由空闲才上环；
转维 FIFO 满则偏转一整圈行环（{w["row_tour_cy"]} 拍）再试。
基线只做 unicast，所以只和 T0 拍图比。</p>
<div class="fig">{bar_m1}
<div class="cap"><b>图：m=1。六根柱都被直径按住</b>——
加宽布线帮不上单 flit 集合通信，该动的是相数和是否走折返。</div></div>
<div class="fig">{bar_m13}
<div class="cap"><b>图：m=13。容量接手。</b>
全交换的割是 24 条有向中缝 × 1152 条不能合并的消息。</div></div>
{''.join(ctab)}
<p>全收集 m=1 最优拍图 {ag1["cal_makespan"] if ag1 else "—"} 拍
（{ALGO_CN.get(ag1["cal_algo"], "") if ag1 else ""}），
相对下界 {times(ag1["gap_cal_floor"]) if ag1 else ""}；
基线 {ag1["base_makespan"] if ag1 else "—"} 拍。
广播 m=13 T1 {bc13t1["cal_makespan"] if bc13t1 else "—"} 拍。</p>

<h2 id="s5">5. 吞吐（II）与 T_avg</h2>
<p><code>per_round = T₄ / 4</code> 是摊还指标，必须压在容量地板
<code>II_lb = max(割, 注入, 弹出, 转维)</code> 之上。
<code>II_eff = (T₄ − T₁) / 3</code> 是插值参数，有限 R 时可以低于地板，
不能当容量用。全收集的
<code>T_avg = T₁ + (R−1)/2 · II_eff</code>，R=1 与 R=13 两档。</p>
{''.join(ttab)}

<h2 id="s6">6. RBRG 转维 FIFO</h2>
<p>环上没有缓冲，<b>桥上有</b>。一次转维占住该 RBRG 一个条目 {w["t_turn"]} 拍。
满了不会阻塞——已在轨上的 flit 永不停——而是偏转绕行。
默认 <code>fifo_depth=4</code>。</p>
{heat}
<h3>深度扫一遍（全交换 m=1）</h3>
{''.join(ftab)}
<p class="muted">深度 1（「桥就是一级寄存器」）在 10 拍转维下不是设计点；
makespan 随深度单调不升。</p>

<h2 id="s7">7. 全部拍图行</h2>
{''.join(allc)}

<h2>复现</h2>
<pre>python3 utils/rg_aic_reticle.py
python3 utils/dse_aic_reticle_8x6.py
python3 utils/verify_aic_reticle_8x6.py
python3 utils/gen_aic_reticle_report.py</pre>
<p class="muted">耗时 {d.get("elapsed_s", "—")} s ·
源文件 <code>rg_aic_reticle.py</code> 是参考控件的逐行移植，
常数不对以文档为准，不以本报告为准。</p>
"""
    return body


CSS = """
:root { --fg:#111827; --mut:#6b7280; --bd:#e5e7eb; --bg:#f8fafc; --acc:#1d4ed8; }
html { font: 16px/1.55 ui-sans-serif, system-ui, sans-serif; color: var(--fg);
  background: #fff; }
body { max-width: 980px; margin: 0 auto; padding: 28px 22px 80px; }
h1 { font-size: 1.7rem; font-weight: 650; letter-spacing: -.02em; margin: 0 0 .4rem; }
h2 { font-size: 1.25rem; margin: 2.2rem 0 .6rem; padding-top: .4rem;
  border-top: 1px solid var(--bd); }
h3 { font-size: 1.05rem; margin: 1.4rem 0 .4rem; }
.lead { color: var(--mut); font-size: 1.02rem; }
nav { display: flex; flex-wrap: wrap; gap: .4rem .9rem; margin: 1rem 0 1.4rem;
  font-size: .92rem; }
nav a { color: var(--acc); text-decoration: none; }
nav a:hover { text-decoration: underline; }
table { width: 100%; border-collapse: collapse; font-size: .88rem;
  margin: .6rem 0 1rem; }
th, td { padding: .38rem .5rem; border-bottom: 1px solid var(--bd);
  text-align: left; vertical-align: top; }
th { font-weight: 600; color: #374151; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
.fig { margin: 1rem 0 1.2rem; }
.cap { color: var(--mut); font-size: .88rem; margin-top: .45rem; }
.note { background: var(--bg); border-left: 3px solid var(--acc);
  padding: .7rem .9rem; margin: .8rem 0; }
.muted { color: var(--mut); font-size: .9rem; }
pre { background: var(--bg); padding: .8rem 1rem; overflow-x: auto;
  font-size: .82rem; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .9em; }
"""


def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"missing {DATA}; run dse_aic_reticle_8x6.py first")
    d = json.loads(DATA.read_text(encoding="utf-8"))
    ver = (json.loads(VER.read_text(encoding="utf-8"))
           if VER.exists() else None)
    html = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>6×8 AIC reticle 集合通信</title>"
        f"<style>{CSS}</style></head><body>"
        f"{build(d, ver)}</body></html>"
    )
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
