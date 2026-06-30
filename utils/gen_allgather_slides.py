#!/usr/bin/env python3
"""Generate self-contained Chinese HTML slide deck for 16x16 AllGather schemes.

Output: results/allgather_slides.html
"""

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utils"))

import hamilton_ring as hr
import sched_ring_zerobuf as S
import sched_zerobuf_compare as Z
import sim_fused_rings as fr
import sim_hamilton_ring as sr
import slide_metrics as sm
from sweep_afifo_depth import shape_cfg as sweep_shape_cfg
from sweep_quad_ring_shapes import make_quads

MX = MY = 16
H, V, RAMP, CROSS = 4, 6, 1, 6
N = MX * MY
AFIFO_CAP = 5
MK_JSON = ROOT / "results" / "allgather_makespan.json"
BORDER_SWEEP = ROOT / "results" / "border_afifo_depth_sweep.json"
RINGFOLLOW_SWEEP = ROOT / "results" / "ringfollow_afifo_depth_sweep.json"
OUT = ROOT / "results" / "allgather_slides.html"

DIA_RING = "#2563eb"
QUAD_BG = ["#eff6ff", "#f0fdf4", "#fff7ed", "#faf5ff"]


def esc(s):
    return html.escape(str(s))


def setup():
    fr.cfg(MX, MY, H, V, cross=CROSS)
    Z.cfg(MX, MY, H, V)


def quad_shape_cfg(scheme):
    return sweep_shape_cfg(MX, scheme, "bi")


def q1_ring_order():
    """Horizontal-first boustrophedon (226 H-hops, 30 V-hops on 16x16)."""
    return hr.snake_cycle(MX, MY)


def _ring_px_py(cell=12, pad=20, top=18):
    px = lambda x: pad + x * cell + cell / 2
    py = lambda y: top + pad + (MY - 1 - y) * cell + cell / 2
    W = MX * cell + 2 * pad
    Ht = MY * cell + 2 * pad + top
    return px, py, W, Ht, pad, top, cell


def svg_global_ring(order):
    """Draw Hamilton ring with horizontal hops blue, vertical hops orange."""
    COL_H, COL_V = "#2563eb", "#ea580c"
    px, py, W, Ht, pad, top, cell = _ring_px_py()
    n = len(order)
    lines = [
        f'<svg width="{W}" height="{Ht}" viewBox="0 0 {W} {Ht}" '
        f'xmlns="http://www.w3.org/2000/svg">',
        '<defs>',
        f'<marker id="ah-h" markerWidth="6" markerHeight="6" refX="5" refY="3" '
        f'orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="{COL_H}"/></marker>',
        f'<marker id="ah-v" markerWidth="6" markerHeight="6" refX="5" refY="3" '
        f'orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="{COL_V}"/></marker>',
        '</defs>',
        f'<rect width="100%" height="100%" fill="#fff"/>',
        f'<text x="8" y="14" font-size="11" font-weight="bold" fill="#1e3a8a">'
        f'水平 snake · 226×H + 30×V</text>',
    ]
    for qi, (qx, qy) in enumerate([(0, 0), (8, 0), (0, 8), (8, 8)]):
        lines.append(
            f'<rect x="{pad+qx*cell:.1f}" y="{top+pad+(MY-qy-8)*cell:.1f}" '
            f'width="{8*cell}" height="{8*cell}" fill="{QUAD_BG[qi]}" '
            f'stroke="#cbd5e1" stroke-width="0.5"/>')
    for y in range(MY):
        for x in range(MX):
            lines.append(
                f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="1.2" fill="#94a3b8"/>')
    nh = nv = 0
    for i in range(n):
        u, v = order[i], order[(i + 1) % n]
        x1, y1 = px(u % MX), py(u // MX)
        x2, y2 = px(v % MX), py(v // MX)
        if u // MX == v // MX:
            col, mk = COL_H, "ah-h"
            nh += 1
        else:
            col, mk = COL_V, "ah-v"
            nv += 1
        lines.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{col}" stroke-width="1.6" marker-end="url(#{mk})"/>')
    x0, y0 = px(order[0] % MX), py(order[0] // MX)
    lines.append(f'<circle cx="{x0:.1f}" cy="{y0:.1f}" r="3.5" fill="{COL_H}"/>')
    lines.append(
        f'<rect x="{pad}" y="{Ht-16}" width="10" height="10" fill="{COL_H}"/>'
        f'<text x="{pad+14}" y="{Ht-7}" font-size="10" fill="#334155">'
        f'水平 hop H={H}（{nh} 段）</text>'
        f'<rect x="{pad+120}" y="{Ht-16}" width="10" height="10" fill="{COL_V}"/>'
        f'<text x="{pad+134}" y="{Ht-7}" font-size="10" fill="#334155">'
        f'垂直 hop V={V}（{nv} 段）</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def build_q1_schedule(bidir, flits=1):
    setup()
    order = q1_ring_order()
    pos = {nd: k for k, nd in enumerate(order)}
    ramp = 1 if not bidir else 2
    foot = {s: Z.fp_ring(s, order, pos, bidir, ramp) for s in range(N)}
    mk, mo, busy, inj, events = Z.export_events(foot, ramp, list(range(N)), flits=flits)
    util = sm.utilization_from_busy(busy, N, ramp, mk)
    slot = sm.slot_table_depth(events, MX, MY, mk)
    return dict(makespan=mk, busy=busy, events=events, util=util, slot=slot,
                order=order, bidir=bidir, ramp=ramp)


def build_quad_schedule(scheme, flits=1):
    setup()
    cfg = quad_shape_cfg(scheme)
    quads = make_quads(cfg)
    if scheme == "border":
        deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
        order, cap = "natural", AFIFO_CAP
    else:
        deliv = lambda s, b, q=quads: S.deliv_ringfollow_quads(s, b, q)
        order, cap = "quad", 3
    r = S.schedule_atomic(MX, True, 2, deliv, afifo_cap=cap,
                          order=order, quads=quads, flits=flits,
                          record_events=True)
    if not r.get("ok"):
        return None
    util = sm.utilization_from_events(r["events"], N, 2, r["makespan"], MX)
    slot = sm.slot_table_depth(r["events"], MX, MY, r["makespan"])
    afifo_series = sm.afifo_occupancy_series(r.get("afifo_profile"), r["makespan"])
    return dict(makespan=r["makespan"], events=r["events"], util=util, slot=slot,
                afifo_depth=r["afifo_depth"],
                afifo_balanced=r["afifo_balanced"]["peak"],
                afifo_series=afifo_series, cfg=cfg, quads=quads)


def load_makespan():
    if MK_JSON.exists():
        return json.loads(MK_JSON.read_text(encoding="utf-8"))
    return {"schemes": {}}


def load_afifo_sweep(path, key="16x16_bi"):
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    pts = data.get("configs", {}).get(key, {}).get("points", [])
    return [(p["cap"], p["makespan"], p["detail"].get("afifo_depth", 0),
             p["detail"].get("afifo_balanced", 0)) for p in pts if p.get("feasible")]


def svg_fault_ring(order, is_cycle, dead_nodes, dead_links, sacrificed=(), cell=14):
    pad = 16
    w = pad * 2 + (MX - 1) * cell
    h = pad * 2 + (MY - 1) * cell
    sacrificed = set(sacrificed)
    dead_nodes = set(dead_nodes) - sacrificed
    dead_links = {frozenset(l) for l in dead_links}

    def px(x):
        return pad + x * cell

    def py(y):
        return pad + (MY - 1 - y) * cell

    p = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
         f'xmlns="http://www.w3.org/2000/svg">',
         f'<rect width="100%" height="100%" fill="#ffffff"/>']
    for y in range(MY):
        for x in range(MX):
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = x + dx, y + dy
                if nx < MX and ny < MY:
                    p.append(f'<line x1="{px(x)}" y1="{py(y)}" x2="{px(nx)}" '
                             f'y2="{py(ny)}" stroke="#e2e8f0" stroke-width="1"/>')
    for l in dead_links:
        a, b = tuple(l)
        ax, ay = a % MX, a // MX
        bx, by = b % MX, b // MX
        p.append(f'<line x1="{px(ax)}" y1="{py(ay)}" x2="{px(bx)}" y2="{py(by)}" '
                 f'stroke="#dc2626" stroke-width="2" stroke-dasharray="3,2"/>')
    pts = [(px(n % MX), py(n // MX)) for n in order]
    if is_cycle and pts:
        pts = pts + [pts[0]]
    poly = " ".join(f"{x},{y}" for x, y in pts)
    p.append(f'<polyline points="{poly}" fill="none" stroke="#2563eb" stroke-width="1.5"/>')
    for y in range(MY):
        for x in range(MX):
            n = hr.nid(x, y, MX)
            if n in dead_nodes:
                p.append(f'<rect x="{px(x)-3}" y="{py(y)-3}" width="6" height="6" fill="#dc2626"/>')
            elif n in sacrificed:
                p.append(f'<rect x="{px(x)-3}" y="{py(y)-3}" width="6" height="6" fill="#f59e0b"/>')
            else:
                p.append(f'<circle cx="{px(x)}" cy="{py(y)}" r="1.8" fill="#1e293b"/>')
    p.append("</svg>")
    return "\n".join(p)


def run_fault_scenarios():
    rows = []
    golden_order = hr.snake_cycle(MX, MY)
    su = sr.simulate(golden_order, True, "uni", mx=MX, my=MY, h=H, vlat=V, ramp=RAMP)
    sb = sr.simulate(golden_order, True, "bi", mx=MX, my=MY, h=H, vlat=V, ramp=RAMP)
    rows.append({
        "name": "golden", "desc": "健康拓扑 boustrophedon snake",
        "feasible": True, "is_cycle": True, "order": golden_order,
        "dead_nodes": [], "dead_links": [], "sacrificed": [],
        "uni": su["makespan"], "bi": sb["makespan"], "ring_len": len(golden_order),
    })
    scenarios = hr.all_scenarios(MX, MY) + hr.rebalanced_node_scenarios(MX, MY)
    for sc in scenarios:
        r = hr.find_ring(MX, MY, sc["dead_nodes"], sc["dead_links"], time_budget=25.0)
        rec = dict(name=sc["name"], desc=sc["desc"], feasible=r["feasible"],
                   is_cycle=r["is_cycle"], order=r["order"],
                   dead_nodes=sc["dead_nodes"], dead_links=sc["dead_links"],
                   sacrificed=sc.get("sacrificed", []),
                   ring_len=len(r["order"]) if r["order"] else 0)
        if r["feasible"] and r["order"]:
            if r["is_cycle"]:
                su = sr.simulate(r["order"], True, "uni", mx=MX, my=MY, h=H, vlat=V)
                rec["uni"] = su["makespan"]
            else:
                rec["uni"] = None
            sb = sr.simulate(r["order"], r["is_cycle"], "bi", mx=MX, my=MY, h=H, vlat=V)
            rec["bi"] = sb["makespan"]
        else:
            rec["uni"] = rec["bi"] = None
        rows.append(rec)
    return rows


def slot_table_html(slot_info, mx, my, sample_coords=None):
    pr = slot_info["per_router"]
    sample_coords = sample_coords or [(0, 0), (7, 7), (8, 8), (15, 15), (7, 0), (0, 7)]
    rows = []
    for x, y in sample_coords:
        p = x + mx * y
        r = pr[p]
        rows.append(
            f"<tr><td>({x},{y})</td><td>{p}</td><td>{r['period']}</td>"
            f"<td>{r['span']}</td><td>{r['distinct']}</td></tr>")
    summary = (f"深度 P：min={slot_info['min_period']} max={slot_info['max_period']} "
               f"mean={slot_info['mean_period']:.1f}")
    heat = sm.svg_depth_heatmap(pr, mx, my, "period", cell=12)
    tbl = (
        f"<p class='note'>{esc(summary)}</p>"
        f"<div class='two-col'><div>{heat}</div>"
        f"<table><tr><th>坐标</th><th>id</th><th>深度P</th><th>活跃跨度</th>"
        f"<th>不同配置数</th></tr>{''.join(rows)}</table></div>"
    )
    return tbl


CSS = """
:root { --bg:#0f172a; --slide:#1e293b; --text:#f1f5f9; --muted:#94a3b8;
        --accent:#38bdf8; --card:#334155; }
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
       background:var(--bg); color:var(--text); }
nav { position:fixed; top:0; left:0; right:0; z-index:100; background:#0f172acc;
      backdrop-filter:blur(8px); padding:8px 16px; display:flex; gap:12px;
      flex-wrap:wrap; border-bottom:1px solid #334155; }
nav a { color:var(--accent); text-decoration:none; font-size:.85rem; }
.slide { min-height:100vh; padding:72px 40px 48px; max-width:1100px; margin:0 auto; }
.slide h1 { font-size:1.8rem; margin:0 0 8px; color:#fff; }
.slide h2 { font-size:1.25rem; margin:24px 0 12px; color:var(--accent);
            border-bottom:1px solid #475569; padding-bottom:6px; }
.slide h3 { font-size:1.05rem; margin:16px 0 8px; color:#e2e8f0; }
.card { background:var(--slide); border:1px solid #475569; border-radius:12px;
        padding:20px 24px; margin:16px 0; }
.note { color:var(--muted); font-size:.88rem; line-height:1.55; }
pre { background:#0b1021; color:#e6edf3; padding:16px; border-radius:8px;
      overflow-x:auto; font-size:.82rem; line-height:1.5; }
table { border-collapse:collapse; width:100%; font-size:.85rem; margin:12px 0; }
th, td { border:1px solid #475569; padding:6px 10px; text-align:center; }
th { background:#334155; }
td:first-child { text-align:left; }
.two-col { display:grid; grid-template-columns:1fr 1fr; gap:20px; align-items:start; }
.grid-fault { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; }
.fault-card { background:var(--card); border-radius:8px; padding:8px; font-size:.75rem; }
.fault-card svg { width:100%; height:auto; }
@media (max-width:800px) { .two-col { grid-template-columns:1fr; } }
.kpi { display:flex; gap:24px; flex-wrap:wrap; margin:12px 0; }
.kpi div { background:#334155; padding:12px 20px; border-radius:8px; }
.kpi strong { font-size:1.4rem; color:var(--accent); display:block; }
"""


FAULT_ALGO_FORMAL = """find_ring(mx, my, dead_nodes, dead_links):
  IF 无故障: RETURN snake_cycle(mx, my)
  adj ← 存活图 G' = 网格 − 故障节点 − 故障链路
  IF 不连通: RETURN infeasible
  色平衡检查 → 目标 cycle（平衡）或 path（|diff|≤1）
  IF c0==c1 且 min_degree≥2:
    DFS+Warnsdorff+连通性剪枝+closure 寻 Hamilton cycle
  IF |c0-c1|≤1:
    从 majority 色低度节点出发寻 Hamilton path
  find_ring_rebalanced: 牺牲 d=|c0-c1| 个 majority 色边界节点恢复平衡后再寻 cycle"""


ALGO_Q1_FORMAL = """snake_cycle(mx, my):   // my 必须为偶数；H < V 时最优健康环
  order ← [(x,0) for x in 0..mx-1]           // 第 0 行脊，左→右（水平）
  for y in 1..my-1:                          // 各行在列 1..mx-1 间蛇形
    if y odd:  append (mx-1,y)..(1,y)        // 右→左
    else:      append (1,y)..(mx-1,y)        // 左→右
  order += [(0,y) for y in my-1..1]          // 第 0 列回程脊，闭合到 (0,0)
  // 16×16: 226 段水平 hop + 30 段垂直 hop，周长延迟 1084 cy"""


ALGO_Q1_NL = """
全局最优 Hamilton 环采用水平 boustrophedon（snake_cycle）：第 0 行作为水平脊
从左到右遍历，第 1..my-1 行在列 1..mx-1 间交替左右蛇形，最后沿第 0 列垂直
回程闭合。在 H=4 < V=6 的 mesh 上，该环含 226 段水平跳与仅 30 段垂直跳
（周长延迟 1084 cy），远优于竖 comb 形 ham_cycle_rect（30H+226V=1476 cy）。
AllGather 在环上双向流水线传播，零 router buffer。"""


ALGO_Q4_NL = """
将 16×16 划分为四个 8×8 reticle（象限 Q0–Q3），每个 reticle 内维持独立 Hamilton
环且 router 零 buffer。源节点先在 home reticle 完成一圈本地 allgather，再通过
跨 reticle 边界链路（AFIFO 缓冲，深度 ≤5）将 flit 送入邻域，Q4（border 短弧）
以行/列短弧扩散至 foreign 节点；B2（ringfollow）跨界后沿 foreign reticle 完整
环传播。小 message（<4 flit）时四 reticle 并行 + 少量跨界，makespan 远低于全局环。"""


FAULT_ALGO_NL = """
网格 G 为二部图，色 c(x,y)=(x+y) mod 2。Hamilton 环要求两色类节点数相等；1×1/3×3
节点孔洞破坏平衡，仅存在 Hamilton 路径。链路故障不改变色平衡，仍可寻 closed cycle。
搜索：Warnsdorff（优先扩展可选邻居最少的点）+ 二部交替剪枝 + 未访问子图连通性剪枝
+ snake 次序 tie-break。对无法成环的孔洞，牺牲孔洞边界多数色节点恢复平衡后再寻环。"""


def makespan_chart(mkdata):
    schemes = mkdata.get("schemes", {})
    cats = [str(m) for m in range(1, 7)]
    q1u = [schemes.get("q1_ring_uni", {}).get(c, {}).get("makespan") for c in cats]
    q1b = [schemes.get("q1_ring_bi", {}).get(c, {}).get("makespan") for c in cats]
    q4 = [schemes.get("q4_border_bi", {}).get(c, {}).get("makespan") for c in cats]
    b2 = [schemes.get("b2_ringfollow_bi", {}).get(c, {}).get("makespan") for c in cats]
    return sm.svg_bar_chart(cats, [q1u, q1b, q4, b2],
                            ["Q1 uni", "Q1 bi", "Q4 border", "B2 ringfollow"],
                            width=800, height=280)


def afifo_depth_chart(pts, title=""):
    caps = [p[0] for p in pts if p[0] <= 20]
    mks = [p[1] for p in pts if p[0] <= 20]
    depths = [p[2] for p in pts if p[0] <= 20]
    w, h = 640, 200
    pad_l, pad_t, pad_b = 48, 20, 36
    iw, ih = w - 64, h - 56
    vmax = max(mks) if mks else 1
    lines = [
        f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">',
        f'<rect width="100%" height="100%" fill="#fff"/>',
        f'<text x="{w//2}" y="14" text-anchor="middle" font-size="11" fill="#334155">{esc(title)}</text>',
    ]
    for i, (cap, mk, dep) in enumerate(zip(caps, mks, depths)):
        x = pad_l + (cap / max(caps[-1], 1)) * iw
        y = pad_t + ih - (mk / vmax) * ih
        col = "#2563eb" if dep <= 5 else "#ea580c"
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{col}"/>')
        if dep <= 5:
            lines.append(f'<text x="{x:.1f}" y="{h-8}" font-size="8" text-anchor="middle">{cap}</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def build_html():
    print("Loading makespan...", flush=True)
    mkdata = load_makespan()
    print("Building Q1 schedule (m=1 bi)...", flush=True)
    q1 = build_q1_schedule(True, 1)
    print("Building Q4 schedule...", flush=True)
    q4 = build_quad_schedule("border", 1)
    print("Building B2 schedule...", flush=True)
    b2 = build_quad_schedule("ringfollow", 1)
    print("Running fault scenarios (16x16)...", flush=True)
    faults = run_fault_scenarios()
    border_pts = load_afifo_sweep(BORDER_SWEEP)
    ringfollow_pts = load_afifo_sweep(RINGFOLLOW_SWEEP)

    order = q1_ring_order()
    ring_svg = svg_global_ring(order)

    util_chart_q1 = sm.svg_line_chart(
        [q1["util"]["eject_series"], q1["util"]["link_series"]],
        ["平均接收利用率", "链路容量利用率"], width=760, height=240, ymax=1.0)
    util_chart_q4 = sm.svg_line_chart(
        [q4["util"]["eject_series"], q4["util"]["link_series"]],
        ["平均接收利用率", "链路容量利用率"], width=760, height=240, ymax=1.0) if q4 else ""
    afifo_chart_q4 = sm.svg_line_chart(
        [q4["afifo_series"]], ["AFIFO 占用深度"], width=760, height=180,
        ymax=max(q4["afifo_series"]) if q4 and q4["afifo_series"] else 5,
        colors=("#ea580c",)) if q4 else ""

    slot_q1 = slot_table_html(q1["slot"], MX, MY)
    slot_q4 = slot_table_html(q4["slot"], MX, MY) if q4 else ""

    mk = mkdata.get("schemes", {})
    q1_bi_m1 = mk.get("q1_ring_bi", {}).get("1", {}).get("makespan", 754)
    q4_m1 = mk.get("q4_border_bi", {}).get("1", {}).get("makespan", 379)
    b2_m1 = mk.get("b2_ringfollow_bi", {}).get("1", {}).get("makespan", 426)
    q4_eject = q4["util"]["avg_eject_util"] if q4 else 0.0
    q4_link = q4["util"]["avg_link_util"] if q4 else 0.0
    q4_afifo_d = q4["afifo_depth"] if q4 else "N/A"
    q4_afifo_b = q4["afifo_balanced"] if q4 else "N/A"

    fault_cards = []
    for f in faults:
        if f["name"] == "golden":
            continue
        if not f.get("order"):
            continue
        svg = svg_fault_ring(f["order"], f["is_cycle"], f["dead_nodes"],
                             f["dead_links"], f.get("sacrificed", []), cell=12)
        kind = "环" if f["is_cycle"] else "路径"
        uni = f.get("uni")
        bi = f.get("bi")
        fault_cards.append(
            f'<div class="fault-card"><div>{esc(f["name"])} ({kind}, N={f["ring_len"]})</div>'
            f'<div>uni={uni if uni else "N/A"} bi={bi if bi else "N/A"}</div>{svg}</div>')

    golden = faults[0]
    golden_svg = svg_fault_ring(golden["order"], True, [], [], cell=12)

    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>16×16 AllGather Hamilton Ring 方案</title>
<style>{CSS}</style></head><body>
<nav>
  <a href="#title">封面</a><a href="#q1">Q1 全局环</a><a href="#q4">Q4/B2 Reticle</a>
  <a href="#fault">故障感知</a>
</nav>

<section class="slide" id="title">
<h1>16×16 Mesh AllGather：Hamilton Ring 方案</h1>
<p class="note">拓扑 16×16 (256 节点)，H=4 cy，V=6 cy，ramp=1 cy，链路 1 flit/cy，router 零 buffer</p>
<div class="kpi">
  <div><strong>{q1_bi_m1}</strong>Q1 全局环 bi m=1 (cy)</div>
  <div><strong>{q4_m1}</strong>Q4 border bi m=1 (cy)</div>
  <div><strong>{b2_m1}</strong>B2 ringfollow bi m=1 (cy)</div>
</div>
</section>

<section class="slide" id="q1">
<h1>Part 1：Q1 全局 Hamilton Ring AllGather</h1>

<h2>1.1 最优全局 Hamilton 环生成算法</h2>
<div class="card">
<h3>形式化描述</h3>
<pre>{esc(ALGO_Q1_FORMAL)}</pre>
<h3>自然语言</h3>
<p>{ALGO_Q1_NL}</p>
<h3>原理</h3>
<p class="note">在 H &lt; V 的 mesh 上，snake_cycle 以 226 段水平 hop（蓝）和 30 段
垂直 hop（橙）最小化环周长延迟。零 buffer 刚性 pack：每源 inject_s 偏移使链路
≤1 flit/cy、down-ramp ≤ ramp_bw。</p>
</div>

<h2>1.2 Hamilton Ring 示意图</h2>
<div class="card">{ring_svg}
<p class="note">蓝色=水平 hop (H=4)，橙色=垂直 hop (V=6)。水平 snake（snake_cycle）：
第 0 行脊 + 行间蛇形 + 第 0 列回程。四象限底色标注 reticle 边界。</p>
</div>

<h2>1.3 Makespan vs Message Size (1–6 flit)</h2>
<div class="card">{makespan_chart(mkdata)}
<p class="note">Q1 uni @ ramp=1；Q1 bi / Q4 / B2 @ ramp=2。小 message 时 Q1 受环延迟 bound；
m≥4 时 wormhole 扩展，Q4/B2 优势缩小。</p>
</div>

<h2>1.4 利用率分析</h2>
<div class="card">
<p class="note">平均接收利用率 = 各 cycle 全节点 down-ramp 流出 flit 数 / (N×ramp_bw)。
链路容量利用率 = 该 cycle 有发送的 directed link 数 / 曾使用的 link 总数。
横轴 cycle，纵轴利用率 [0,1]。</p>
<p>Q1 bi m=1：平均接收利用率 {q1['util']['avg_eject_util']:.3f}，
平均链路利用率 {q1['util']['avg_link_util']:.3f}</p>
{util_chart_q1}
</div>

<h2>1.5 Q1 各 Router 时隙表与深度</h2>
<div class="card">
<p class="note">时隙表深度 P = router 交叉开关 (in_dir→out_dir) 连接模式的最小重复周期。
左：256 router 深度热力图；右：代表性 router 明细。</p>
{slot_q1}
</div>
</section>

<section class="slide" id="q4">
<h1>Part 2：Reticle 局部 Hamilton Ring + 短弧 (Q4 / B2)</h1>

<h2>2.1 算法原理（Q4 border 短弧 + B2 ringfollow）</h2>
<div class="card">
<p>{ALGO_Q4_NL}</p>
<ul>
<li><strong>Q4 (border)</strong>：跨界后以行/列短弧扩散，不沿 foreign 环完整绕行 → 小 size 延迟最低</li>
<li><strong>B2 (ringfollow)</strong>：跨界后沿 destination reticle 完整 Hamilton 环传播 → 路由更简单</li>
<li>Reticle 内 router 维持零 buffer；跨 reticle 等待仅发生在 AFIFO（cap≤5）</li>
</ul>
</div>

<h2>2.2 小 Size Makespan 对比</h2>
<div class="card">{makespan_chart(mkdata)}
<p class="note">m&lt;4 flit 时 Q4/B2 相对 Q1 全局环 makespan 大幅降低（约 2×）。
m≥4 时全局环 wormhole 效率提升，可能出现 crossover。</p>
</div>

<h2>2.3 AFIFO 最大占用深度（3/4 flit 证明）</h2>
<div class="card two-col">
<div>{afifo_depth_chart(border_pts, "Q4 border: makespan vs AFIFO cap")}</div>
<div>{afifo_depth_chart(ringfollow_pts, "B2 ringfollow: makespan vs AFIFO cap")}</div>
</div>
<div class="card">
<p class="note">AFIFO cap≥3 时 Q4 border bi peak=3 flit、balanced=2；B2 ringfollow peak=3。
cap=5 为硬件预算，m=1 时 makespan 进入平台区。下方为 Q4 m=1 逐 cycle AFIFO 全局占用。</p>
{afifo_chart_q4 if q4 else ""}
<p>Q4 m=1：AFIFO peak={q4_afifo_d}，balanced peak={q4_afifo_b}</p>
</div>

<h2>2.4 Q4 利用率分析</h2>
<div class="card">
<p>Q4 border bi m=1：平均接收利用率 {q4_eject:.3f}，平均链路利用率 {q4_link:.3f}</p>
{util_chart_q4}
</div>

<h2>2.5 Q4 各 Router 时隙表（无冲突无阻塞）与深度</h2>
<div class="card">{slot_q4}</div>
</section>

<section class="slide" id="fault">
<h1>Part 3：故障感知 Hamilton Ring 查找（16×16）</h1>

<h2>3.1 算法描述</h2>
<div class="card">
<h3>形式化</h3>
<pre>{esc(FAULT_ALGO_FORMAL)}</pre>
<h3>自然语言与原理</h3>
<p>{FAULT_ALGO_NL}</p>
</div>

<h2>3.2 健康拓扑 Golden Ring</h2>
<div class="card two-col">
<div>{golden_svg}</div>
<div>
<p>健康 snake 环：N={golden['ring_len']}，uni={golden['uni']} cy，bi={golden['bi']} cy</p>
<p class="note">V=6（与 AllGather 方案一致，区别于 12×16 报告的 V=8）</p>
</div>
</div>

<h2>3.3 各故障场景 Hamilton Ring 示意图</h2>
<div class="card">
<p class="note">红方块=故障节点，橙方块=rebalance 牺牲节点，红虚线=故障链路，蓝线=恢复的 ring/path。</p>
<div class="grid-fault">{''.join(fault_cards)}</div>
</div>
</section>

<footer style="text-align:center;padding:32px;color:#64748b;font-size:.8rem">
Generated by utils/gen_allgather_slides.py · BookSim2 AllGather Study
</footer>
</body></html>""")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


def main():
    build_html()


if __name__ == "__main__":
    main()
