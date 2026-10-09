#!/usr/bin/env python3
"""HTML report: Homa-style distributed request-grant, 8x6 mesh and ring.

Reads results/write_homa_8x6.json (+ write_dbid_8x6.json for the v1 DBID-pool
ablation and verify_write_homa_8x6.json) and writes one self-contained HTML
file with inline SVG charts:

    results/report_write_homa_8x6.html

Every number in the prose is computed here from the JSON, not typed in.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from gen_write_dbid_report import chart

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
OUT = RES / "report_write_homa_8x6.html"

D = json.loads((RES / "write_homa_8x6.json").read_text())
V1 = json.loads((RES / "write_dbid_8x6.json").read_text())
VER = json.loads((RES / "verify_write_homa_8x6.json").read_text())
ROWS = D["rows"]
FAB = {"mesh": "2D mesh", "ring": "2D ring"}
COL = {"base": "#6b7280", "central": "#e07a1f", "homa": "#1f9d55",
       "tp": "#0e7490", "pool": "#9ca3af"}


def S(fab: str, cfg: str, grp: str = "main", **kw) -> list[dict[str, Any]]:
    return sorted([r for r in ROWS if r["config"] == f"{fab}_{cfg}"
                   and r["group"] == grp
                   and all(r.get(k) == v for k, v in kw.items())],
                  key=lambda r: r["lam"])


def at(rs, lam):
    return [r for r in rs if abs(r["lam"] - lam) < 1e-9][0]


def lam_star(rs):
    ok = [r["lam"] for r in rs if r["stable_gp"]]
    return max(ok) if ok else None


def peak(rs):
    return max(r["goodput"] for r in rs)


SCHEMES = (("base", "base", "main", "基线"),
           ("central", "islip2d", "main", "类 iSLIP 集中仲裁"),
           ("homa", "homa", "main", "分布式 Homa 类（均衡点）"),
           ("tp", "homa", "tp", "分布式 Homa 类（吞吐优先点）"),
           ("pool", "dbid", "pool", "v1 逐包 DBID 池（K=64）"))


def wd(fab, key="best"):
    w, d = D[key][fab]
    return f"W={w}, D={d}"


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def fig_goodput(fab: str) -> str:
    curves = []
    for key, cfg, grp, name in SCHEMES:
        if key == "homa":
            name += f"（{wd(fab)}）"
        if key == "tp":
            name += f"（{wd(fab, 'tp')}）"
        rs = S(fab, cfg, grp)
        curves.append((name, COL[key],
                       [(r["lam"], r["goodput"], r["stable_gp"]) for r in rs],
                       key in ("tp", "pool")))
    anc = D["anchors"]["mesh_xy" if fab == "mesh" else "ring_fixed"]
    return chart(f"{FAB[fab]}：goodput 与注入率（消息负载，1/8/64 包）",
                 "注入率 λ（包 / 节点 / 周期）", "goodput（flit / 节点 / 周期）",
                 curves, xmax=1.0, ymax=1.0,
                 hlines=[(anc, f"均匀流量解析上限 {anc:.3f}", "#b91c1c")],
                 diag=True, w=820, h=500)


def fig_fct(fab: str, L: int) -> str:
    curves = []
    for key, cfg, grp, name in SCHEMES[:4]:
        rs = [r for r in S(fab, cfg, grp) if r.get(f"fct_mean_L{L}")]
        curves.append((name.split("（")[0] + ("（吞吐优先）" if key == "tp" else ""),
                       COL[key],
                       [(r["lam"], r[f"fct_mean_L{L}"], r["stable_gp"])
                        for r in rs], key == "tp"))
    return chart(f"{FAB[fab]}：{L} 包消息的平均完成时间（FCT）",
                 "注入率 λ（包 / 节点 / 周期）", "平均 FCT（周期，对数轴）",
                 curves, xmax=1.0, ymax=10000, ymin=10, ylog=True,
                 yticks=[10, 30, 100, 300, 1000, 3000, 10000], w=700, h=430)


def fig_grid_tradeoff(fab: str) -> str:
    """goodput(lam=1.0) vs low-load FCT of 64-packet messages, one dot per (W,D)."""
    pts = {}
    for r in ROWS:
        if r["config"] == f"{fab}_homa" and r["group"] == "grid":
            k = (r["homa_window"], r["homa_degree"])
            pts.setdefault(k, {})[r["lam"]] = r
    base = at(S(fab, "base"), 0.3).get("fct_mean_L64")
    L, R, T, B = 66, 18, 40, 58
    w, h = 700, 430
    pw, ph = w - L - R, h - T - B
    xmax, ymax = 3000.0, 0.6

    def px(x):
        return L + pw * min(x, xmax) / xmax

    def py(y):
        return T + ph * (1 - y / ymax)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
         f'width="{w}" height="{h}" font-family="sans-serif" font-size="12">',
         f'<rect width="{w}" height="{h}" fill="#fff"/>',
         f'<text x="{w/2}" y="22" text-anchor="middle" font-size="15" '
         f'font-weight="bold">{FAB[fab]}：(W, D) 网格的吞吐—长消息延迟折中</text>']
    for x in range(0, 3001, 500):
        o.append(f'<line x1="{px(x)}" y1="{T}" x2="{px(x)}" y2="{T+ph}" '
                 f'stroke="#e5e7eb"/><text x="{px(x)}" y="{T+ph+16}" '
                 f'text-anchor="middle">{x}</text>')
    for i in range(7):
        y = i * 0.1
        o.append(f'<line x1="{L}" y1="{py(y)}" x2="{L+pw}" y2="{py(y)}" '
                 f'stroke="#e5e7eb"/><text x="{L-6}" y="{py(y)+4}" '
                 f'text-anchor="end">{y:.1f}</text>')
    o.append(f'<rect x="{L}" y="{T}" width="{pw}" height="{ph}" fill="none" '
             f'stroke="#374151"/>')
    o.append(f'<text x="{L+pw/2}" y="{h-12}" text-anchor="middle">λ=0.3 时 64 包'
             f'消息的平均 FCT（周期；右端截断）</text>')
    o.append(f'<text transform="translate(16,{T+ph/2}) rotate(-90)" '
             f'text-anchor="middle">λ=1.0 的 goodput（flit/节点/周期）</text>')
    if base:
        o.append(f'<line x1="{px(2*base)}" y1="{T}" x2="{px(2*base)}" '
                 f'y2="{T+ph}" stroke="#b91c1c" stroke-dasharray="6 4"/>'
                 f'<text x="{px(2*base)+4}" y="{T+12}" fill="#b91c1c">'
                 f'基线 FCT 的 2×（均衡点的约束）</text>')
    bp = peak(S(fab, "base"))
    o.append(f'<line x1="{L}" y1="{py(bp)}" x2="{L+pw}" y2="{py(bp)}" '
             f'stroke="{COL["base"]}" stroke-dasharray="6 4"/>'
             f'<text x="{L+pw-4}" y="{py(bp)-4}" text-anchor="end" '
             f'fill="{COL["base"]}">基线峰值 {bp:.3f}</text>')
    shade = {1: "#0e7490", 2: "#2563eb", 4: "#7c3aed", 8: "#1f9d55",
             16: "#e07a1f"}
    for (wv, dv), v in sorted(pts.items()):
        f64 = v[0.3].get("fct_mean_L64") or xmax
        g = v[1.0]["goodput"]
        sel = (wv, dv) in (tuple(D["best"][fab]), tuple(D["tp"][fab]))
        o.append(f'<circle cx="{px(f64):.1f}" cy="{py(g):.1f}" '
                 f'r="{7 if sel else 4.5}" fill="{shade[wv]}" '
                 f'fill-opacity="{0.95 if sel else 0.55}" '
                 f'stroke="{"#111" if sel else "none"}"/>')
        if sel:
            o.append(f'<text x="{px(f64)+9:.1f}" y="{py(g)+4:.1f}">'
                     f'W={wv},D={dv}</text>')
    for i, (wv, c) in enumerate(shade.items()):
        o.append(f'<circle cx="{L+pw-90}" cy="{T+ph-14-16*i}" r="5" '
                 f'fill="{c}"/><text x="{L+pw-80}" y="{T+ph-10-16*i}">'
                 f'W={wv}</text>')
    o.append("</svg>")
    return "\n".join(o)


def fig_sequence() -> str:
    cols = {"RN": 90, "HA": 330, "fab": 570}
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 330" '
         'width="680" height="330" font-family="sans-serif" font-size="12">',
         '<rect width="680" height="330" fill="#fff"/>',
         '<defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" '
         'refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#374151"/>'
         '</marker></defs>']
    for k, x in cols.items():
        nm = {"RN": "RN（源，持有写数据）", "HA": "HA（目的，接收端调度）",
              "fab": "数据面（基线 fabric）"}[k]
        o.append(f'<text x="{x}" y="22" text-anchor="middle" '
                 f'font-weight="bold">{nm}</text>')
        o.append(f'<line x1="{x}" y1="32" x2="{x}" y2="318" stroke="#9ca3af" '
                 f'stroke-dasharray="3 4"/>')

    def arrow(x1, x2, y, txt, col="#374151", dash=""):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o.append(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{col}" '
                 f'stroke-width="1.8"{d} marker-end="url(#a)"/>')
        o.append(f'<text x="{(x1+x2)/2}" y="{y-6}" text-anchor="middle" '
                 f'fill="{col}">{txt}</text>')
    arrow(90, 570, 62, "① 前 U 个包直接发（unscheduled，无往返）", "#1f9d55")
    arrow(570, 330, 96, "② 首包到达：HA 获知消息长度 L", "#1f9d55")
    o.append('<rect x="250" y="112" width="160" height="40" rx="5" '
             'fill="#ecfdf5" stroke="#1f9d55"/><text x="330" y="129" '
             'text-anchor="middle">按剩余量 SRPT 排序</text><text x="330" '
             'y="145" text-anchor="middle">取前 D 条消息 overcommit</text>')
    arrow(330, 90, 182, "③ GRANT：该消息窗口内「已授权未收到」＜ W", "#e07a1f")
    arrow(90, 570, 214, "④ 收到 GRANT 才发这一个包", "#e07a1f")
    arrow(570, 330, 246, "⑤ 包送达 → 窗口腾出一格 → 立刻再授权（③）", "#e07a1f")
    o.append('<text x="340" y="296" text-anchor="middle" fill="#6b7280">'
             'L ≤ U 的短消息只走 ①，不产生任何控制消息</text>')
    o.append("</svg>")
    return "\n".join(o)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def tbl(head: list[str], body: list[list[str]], cls: str = "") -> str:
    h = "".join(f"<th>{x}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
                for row in body)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def heat(fab: str) -> str:
    cells: dict[tuple[int, int], list[float]] = {}
    f64: dict[tuple[int, int], float | None] = {}
    for r in ROWS:
        if r["config"] == f"{fab}_homa" and r["group"] == "grid":
            k = (r["homa_window"], r["homa_degree"])
            cells.setdefault(k, []).append(r["goodput"])
            if r["lam"] == 0.3:
                f64[k] = r.get("fct_mean_L64")
    ws = D["meta"]["w_grid"]
    ds = D["meta"]["d_grid"]
    allv = [sum(v) / len(v) for v in cells.values()]
    lo, hi = min(allv), max(allv)
    best = tuple(D["best"][fab])
    tp = tuple(D["tp"][fab])
    head = ["窗口 W \\ 度 D"] + [f"D={d}" for d in ds]
    body = []
    for w in ws:
        row = [f"<b>W={w}</b>"]
        for d in ds:
            m = sum(cells[(w, d)]) / len(cells[(w, d)])
            a = (m - lo) / (hi - lo + 1e-9)
            bg = f"rgba(31,157,85,{0.08 + 0.6 * a:.2f})"
            mark = " ◆" if (w, d) == best else (" ●" if (w, d) == tp else "")
            f = f64.get((w, d))
            ft = f"{f:.0f}" if f else "—"
            row.append(f'<span style="display:block;background:{bg};'
                       f'padding:4px;border-radius:3px">{m:.3f}{mark}<br>'
                       f'<small>FCT₆₄={ft}</small></span>')
        body.append(row)
    return tbl(head, body, "heat")


def star_table(fab: str) -> str:
    base_pk = peak(S(fab, "base"))
    body = []
    for key, cfg, grp, name in SCHEMES:
        rs = S(fab, cfg, grp)
        ls = lam_star(rs)
        pk = peak(rs)
        end = at(rs, 1.0)["goodput"]
        body.append([name, "—" if ls is None else f"{ls:g}", f"{pk:.3f}",
                     f"{(pk / base_pk - 1) * 100:+.0f}%", f"{end:.3f}",
                     f"{at(rs, 0.05)['p50']:.0f}",
                     f"{at(rs, 0.3).get('fct_mean_L1', float('nan')):.0f}",
                     f"{at(rs, 0.3).get('fct_mean_L64', float('nan')):.0f}"])
    return tbl(["方案", "λ*", "峰值 goodput", "相对基线", "λ=1.0 goodput",
                "p50（λ=0.05）", "FCT₁（λ=0.3）", "FCT₆₄（λ=0.3）"], body)


def curve_table(fab: str) -> str:
    sch = [(k, S(fab, c, g)) for k, c, g, _ in SCHEMES]
    body = []
    for lam in D["meta"]["lams"]:
        row = [f"{lam:g}"]
        for _k, rs in sch:
            r = at(rs, lam)
            row.append(f"{r['goodput']:.3f}"
                       + ("" if r["stable_gp"] else "<sup>✗</sup>"))
        body.append(row)
    return tbl(["λ", "基线", "集中式", "Homa 均衡", "Homa 吞吐优先",
                "v1 池"], body, "num")


def fct_table(fab: str, lam: float) -> str:
    body = []
    for key, cfg, grp, name in SCHEMES[:4]:
        r = at(S(fab, cfg, grp), lam)
        body.append([name] + [f"{r.get(f'fct_mean_L{L}', float('nan')):.0f} / "
                              f"{r.get(f'fct_p99_L{L}', float('nan')):.0f}"
                              for L in (1, 8, 64)])
    return tbl(["方案", "1 包 均值/p99", "8 包 均值/p99", "64 包 均值/p99"],
               body)


def ablation_table(fab: str) -> str:
    body = []
    labels = (("main", "均衡点（SRPT，U=W）"), ("fcfs", "FCFS 代替 SRPT"),
              ("nounsched", "无 unscheduled 前缀（U=0）"),
              ("halflat", "控制时延减半"))
    for grp, name in labels:
        row = [name]
        for lam in (0.3, 0.5, 0.8):
            rs = S(fab, "homa", grp)
            if not rs or not any(abs(r["lam"] - lam) < 1e-9 for r in rs):
                row.append("—")
                continue
            r = at(rs, lam)
            row.append(f"{r['goodput']:.3f} / {r.get('fct_mean_L1', 0):.0f}"
                       f" / {r.get('fct_mean_L64', 0):.0f}")
        body.append(row)
    return tbl(["变体", "λ=0.3", "λ=0.5", "λ=0.8"], body)


def srpt_table(fab: str) -> str:
    body = []
    for sch in ("srpt", "fcfs"):
        row = [sch.upper() + "（W=2, D=4）"]
        for lam in (0.5, 0.8):
            rs = S(fab, "homa", f"{sch}_w2")
            r = at(rs, lam)
            row.append(f"{r['goodput']:.3f} / {r['fct_mean_L8']:.0f} / "
                       f"{r['fct_mean_L64']:.0f}")
        body.append(row)
    return tbl(["策略", "λ=0.5 goodput / FCT₈ / FCT₆₄",
                "λ=0.8 goodput / FCT₈ / FCT₆₄"], body)


def rtt_table(fab: str) -> str:
    body = []
    for lam in (0.2, 0.3, 0.5, 0.8):
        a = at(S(fab, "islip2d"), lam)
        b = at(S(fab, "islip2d", "rtt64"), lam)
        body.append([f"{lam:g}", f"{a['goodput']:.3f}", f"{b['goodput']:.3f}",
                     f"{a['fct_mean_L1']:.0f}", f"{b['fct_mean_L1']:.0f}"])
    return tbl(["λ", "goodput T_rtt=16", "goodput T_rtt=64", "FCT₁ T_rtt=16",
                "FCT₁ T_rtt=64"], body, "num")


def single_table() -> str:
    body = []
    for fab in ("mesh", "ring"):
        b = S(fab, "base", "single")
        h = S(fab, "homa", "single")
        dg = max(abs(x["goodput"] - y["goodput"]) for x, y in zip(b, h))
        body.append([FAB[fab], f"{peak(b):.3f}", f"{peak(h):.3f}",
                     f"{dg:.4f}", f"{h[0]['n_grants']}"])
    return tbl(["拓扑", "基线峰值", "Homa 峰值", "逐点最大差", "grant 数"],
               body)


def v1_table() -> str:
    body = []
    for fab in ("mesh", "ring"):
        rows = V1["rows"]

        def sr(cfg, grp="main", **kw):
            return sorted([r for r in rows if r["config"] == f"{fab}_{cfg}"
                           and r["group"] == grp
                           and all(r.get(k) == v for k, v in kw.items())],
                          key=lambda r: r["lam"])
        b, c, d = sr("base"), sr("islip2d"), sr("dbid")
        body.append([FAB[fab],
                     f"{peak(b):.3f}", f"{peak(c):.3f}", f"{peak(d):.3f}",
                     f"{at(b, 0.01)['p50']:.0f} → {at(d, 0.01)['p50']:.0f}"])
    return tbl(["拓扑", "基线峰值", "集中式峰值", "v1 池峰值",
                "低载 p50（基线 → v1）"], body)


def ver_table() -> str:
    body = [[html.escape(c["name"]), "✓" if c["ok"] else "<b>✗</b>",
             html.escape(c["detail"])] for c in VER]
    return tbl(["检查", "结果", "细节"], body)


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

CSS = """
body{font:15px/1.65 -apple-system,'Segoe UI','Noto Sans CJK SC',sans-serif;
max-width:1040px;margin:0 auto;padding:24px 28px 80px;color:#1f2937}
h1{font-size:26px;margin:.2em 0}h2{margin-top:2.2em;border-bottom:2px solid #e5e7eb;
padding-bottom:6px}h3{margin-top:1.6em}
table{border-collapse:collapse;margin:12px 0;font-size:13.5px}
th,td{border:1px solid #d1d5db;padding:5px 9px;text-align:left;vertical-align:top}
th{background:#f3f4f6}table.num td{text-align:right}table.heat td{padding:3px}
.box{background:#f9fafb;border:1px solid #e5e7eb;border-left:4px solid #1f9d55;
padding:10px 16px;margin:14px 0}.warn{border-left-color:#e07a1f;background:#fffbeb}
.fig{margin:14px 0;overflow-x:auto}.fig figcaption{font-size:13px;color:#6b7280}
code{background:#f3f4f6;padding:1px 5px;border-radius:3px;font-size:13px}
.meta{color:#6b7280;font-size:13.5px}nav a{margin-right:14px}
.two{display:flex;flex-wrap:wrap;gap:8px}sup{color:#b91c1c}
"""


def fig(svg: str, cap: str) -> str:
    return f'<figure class="fig">{svg}<figcaption>{cap}</figcaption></figure>'


def fabric_section(fab: str, prose: str) -> str:
    return f"""
<h2 id="{fab}">{'3' if fab == 'mesh' else '4'}. {FAB[fab]}</h2>
{prose}
<h3>goodput 与注入率</h3>
{fig(fig_goodput(fab), "实心点 = 稳定，空心点 = 队列发散。点线 y=x 是「全部送达」，红色虚线是均匀流量的解析上限；集中式在过载区超过该上限，是被调度出的流量构成漂移所致，不是稳定吞吐。")}
{star_table(fab)}
<p class="meta">λ* = 最大稳定注入率；FCT = 消息完成时间（含 REQ/授权/排队），单位周期。✗ = 队列发散。</p>
<details><summary>逐点数据（goodput）</summary>{curve_table(fab)}</details>
<h3>消息完成时间</h3>
<div class="two">{fig(fig_fct(fab, 1), "1 包消息")}{fig(fig_fct(fab, 64), "64 包消息")}</div>
<p class="meta">过载区只统计「已完成」的消息，存在幸存者偏差，不能把过载点的 FCT 当作排队后的真实值；λ* 以下的点可比。</p>
<p>λ=0.3（三者都稳定）时按消息长度分的 FCT（均值 / p99）：</p>
{fct_table(fab, 0.3)}
<p>λ=0.5（基线已发散）：</p>
{fct_table(fab, 0.5)}
<h3>消融（goodput / FCT₁ / FCT₆₄）</h3>
{ablation_table(fab)}
<p>SRPT 只有在多于一个长度类同时被调度时才起作用（均衡点 U=W=8 时 8 包消息整条都是 unscheduled，只有 64 包消息被调度）。把窗口缩到 W=2 让 8 包与 64 包同时竞争授权：</p>
{srpt_table(fab)}
<h3>集中式的控制往返</h3>
{rtt_table(fab)}
"""


def build() -> str:
    mesh_b = S("mesh", "base")
    ring_b = S("ring", "base")

    def g(fab, key, f=lambda r: r["goodput"], how=peak):
        cfg, grp = {"base": ("base", "main"), "central": ("islip2d", "main"),
                    "homa": ("homa", "main"), "tp": ("homa", "tp"),
                    "pool": ("dbid", "pool")}[key]
        return how(S(fab, cfg, grp))
    gm = {k: g("mesh", k) for k in COL}
    gr = {k: g("ring", k) for k in COL}

    def rel(a, b):
        return f"{(a / b - 1) * 100:+.0f}%"

    summary = tbl(
        ["", "基线", "集中式", "Homa 均衡点", "Homa 吞吐优先点", "v1 逐包池"],
        [[FAB[f]] + [f"{x[k]:.3f}（{rel(x[k], x['base'])}）" if k != "base"
                     else f"{x[k]:.3f}" for k in
                     ("base", "central", "homa", "tp", "pool")]
         for f, x in (("mesh", gm), ("ring", gr))])

    f1m = {k: at(S("mesh", c, gp), 0.3)["fct_mean_L1"]
           for k, c, gp, _ in SCHEMES[:4]}
    f1r = {k: at(S("ring", c, gp), 0.3)["fct_mean_L1"]
           for k, c, gp, _ in SCHEMES[:4]}
    f64m = {k: at(S("mesh", c, gp), 0.3)["fct_mean_L64"]
            for k, c, gp, _ in SCHEMES[:4]}
    f64r = {k: at(S("ring", c, gp), 0.3)["fct_mean_L64"]
            for k, c, gp, _ in SCHEMES[:4]}
    ls = {(f, k): lam_star(S(f, c, gp)) for f in ("mesh", "ring")
          for k, c, gp, _ in SCHEMES}
    ctrl = {f: at(S(f, "homa"), 0.3) for f in ("mesh", "ring")}
    lsm = {k: ls[("mesh", k)] for k in COL}
    lsr = {k: ls[("ring", k)] for k in COL}
    cfgs = {k: (c, gp) for k, c, gp, _ in SCHEMES}
    e1m = {k: at(S("mesh", *cfgs[k]), 1.0)["goodput"] for k in COL}
    e1r = {k: at(S("ring", *cfgs[k]), 1.0)["goodput"] for k in COL}
    anc = D["anchors"]
    wdm, wdr = wd("mesh"), wd("ring")
    tpm, tpr = wd("mesh", "tp"), wd("ring", "tp")

    relm = {k: rel(gm[k], gm["base"]) for k in COL}
    relr = {k: rel(gr[k], gr["base"]) for k in COL}
    mix = [tuple(map(float, x.split(":"))) for x in D["mix"].split(",")]
    mean_len = sum(l * w for l, w in mix) / sum(w for _, w in mix)
    mesh_collapse = (1 - e1m["base"] / gm["base"]) * 100
    ring_collapse = (1 - e1r["base"] / gr["base"]) * 100
    tp_f64m = at(S("mesh", "homa", "tp"), 0.3)["fct_mean_L64"]
    tp_ratio_m = tp_f64m / at(S("mesh", "base"), 0.3)["fct_mean_L64"]
    mesh_c035 = at(S("mesh", "islip2d"), 0.35)["goodput"]
    ring_c05 = at(S("ring", "islip2d"), 0.5)["goodput"]
    mesh_c05_f1 = at(S("mesh", "islip2d"), 0.5)["fct_mean_L1"]
    mesh_b05_f1 = at(S("mesh", "base"), 0.5)["fct_mean_L1"]

    def ab(fab, grp, lam, key):
        return at(S(fab, "homa", grp), lam)[key]
    mesh_fct1_main = ab("mesh", "main", 0.3, "fct_mean_L1")
    mesh_fct1_nounsched = ab("mesh", "nounsched", 0.3, "fct_mean_L1")
    mesh_fct64_main = ab("mesh", "main", 0.3, "fct_mean_L64")
    mesh_fct64_half = ab("mesh", "halflat", 0.3, "fct_mean_L64")
    ring_fct1_main = ab("ring", "main", 0.3, "fct_mean_L1")
    ring_fct1_nounsched = ab("ring", "nounsched", 0.3, "fct_mean_L1")
    ring_fct64_main = ab("ring", "main", 0.3, "fct_mean_L64")
    ring_fct64_half = ab("ring", "halflat", 0.3, "fct_mean_L64")
    mesh_srpt8 = ab("mesh", "srpt_w2", 0.5, "fct_mean_L8")
    mesh_fcfs8 = ab("mesh", "fcfs_w2", 0.5, "fct_mean_L8")
    mesh_srpt_g = ab("mesh", "srpt_w2", 0.5, "goodput")
    mesh_fcfs_g = ab("mesh", "fcfs_w2", 0.5, "goodput")
    ring_srpt8 = ab("ring", "srpt_w2", 0.5, "fct_mean_L8")
    ring_fcfs8 = ab("ring", "fcfs_w2", 0.5, "fct_mean_L8")
    homa_state_kbit = 48 * ctrl["mesh"]["ha_active_max"] * 48 / 1000
    pool_f1m = at(S("mesh", "dbid", "pool"), 0.3)["fct_mean_L1"]
    pool_f1r = at(S("ring", "dbid", "pool"), 0.3)["fct_mean_L1"]
    v1rows = V1["rows"]

    def v1pk(cfg):
        return max(r["goodput"] for r in v1rows if r["config"] == cfg
                   and r["group"] == "main")
    v1_mesh_pk, v1_mesh_base = v1pk("mesh_dbid"), v1pk("mesh_base")

    P_MESH = PROSE_MESH.format(**locals())
    P_RING = PROSE_RING.format(**locals())
    P_SUM = PROSE_SUMMARY.format(**locals())
    P_LIM = PROSE_LIMITS.format(**locals())
    P_ABS = PROSE_ABSTRACT.format(**locals())
    P_V1 = PROSE_V1.format(**locals())
    P_PARAM = PROSE_PARAM.format(**locals())
    npass = sum(1 for c in VER if c["ok"])

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Homa 类分布式 request-grant：8×6 mesh / ring</title>
<style>{CSS}</style></head><body>
<h1>Homa 类分布式 request-grant：8×6 mesh 与 ring 的 goodput–注入率对比</h1>
<p class="meta">8×6，48 节点；H=7，V=9；RAMP=2，RAMP_BW=2 · 开环 alltoall 写消息 · 稳态仿真
（warmup {D['meta']['warmup']}，measure {D['meta']['measure']}，单种子）· 校验 {npass}/{len(VER)} 通过</p>
<nav><a href="#abs">摘要</a><a href="#mech">机制</a><a href="#setup">口径</a>
<a href="#param">参数</a><a href="#mesh">2D mesh</a><a href="#ring">2D ring</a>
<a href="#cross">总结</a><a href="#v1">v1 对照</a><a href="#ver">验证</a>
<a href="#lim">局限</a><a href="#files">文件</a></nav>

<h2 id="abs">0. 摘要</h2>
{P_ABS}
<p>峰值 goodput（flit / 节点 / 周期，括号内为相对基线）：</p>
{summary}

<h2 id="mech">1. 机制：HA 在回 DBID 时做 Homa 式调度</h2>
{PROSE_MECH}
{fig(fig_sequence(), "Homa 类写调度的消息流。橙色部分只对长于未调度前缀的消息发生。")}
{PROSE_MAP}

<h2 id="setup">2. 负载与口径</h2>
{PROSE_SETUP}

<h2 id="param">3. 参数：窗口 W 与超额授权度 D</h2>
{P_PARAM}
<div class="two">{fig(fig_grid_tradeoff('mesh'), "mesh")}{fig(fig_grid_tradeoff('ring'), "ring")}</div>
<p>mesh：每格 = 三个 λ（0.3 / 0.5 / 1.0）的平均 goodput；FCT₆₄ = λ=0.3 时 64 包消息平均完成时间。◆ = 均衡点，● = 吞吐优先点。</p>
{heat('mesh')}
<p>ring：</p>
{heat('ring')}

{fabric_section('mesh', P_MESH)}
{fabric_section('ring', P_RING)}

<h2 id="cross">5. mesh 与 ring 横向总结</h2>
{P_SUM}

<h2 id="v1">6. 与 v1（逐包 DBID 池）的对比</h2>
{P_V1}
<p>v1 在<b>单包</b>负载下的结果（上一轮，<code>write_dbid_8x6.json</code>）：</p>
{v1_table()}
<p>本轮单包负载下的 Homa 退化检查：</p>
{single_table()}

<h2 id="ver">7. 验证</h2>
{ver_table()}

<h2 id="lim">8. 已知局限</h2>
{P_LIM}

<h2 id="files">9. 文件</h2>
{PROSE_FILES}
</body></html>"""


# prose blocks are defined in gen_write_homa_prose.py to keep this file about
# mechanics; they are str.format templates over the names built in build().
from gen_write_homa_prose import (PROSE_ABSTRACT, PROSE_FILES, PROSE_LIMITS,  # noqa: E402
                                  PROSE_MAP, PROSE_MECH, PROSE_MESH,
                                  PROSE_PARAM, PROSE_RING, PROSE_SETUP,
                                  PROSE_SUMMARY, PROSE_V1)

if __name__ == "__main__":
    OUT.write_text(build())
    print("wrote", OUT, f"({OUT.stat().st_size // 1024} KB)")
