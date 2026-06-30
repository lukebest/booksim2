#!/usr/bin/env python3
"""HTML: optimal-makespan generation algorithm for global Hamilton-ring allgather
across message sizes m.

Model: rigid 0-buffer pack (sched_zerobuf_compare); router_buf=0, NO AFIFO
waiting; cross-reticle hops have fixed latency 6 cy (H=4 / V=6 intra-reticle).

Output: results/report_global_ring_algo.html
"""

import html
import json
from pathlib import Path

import sim_fused_rings as fr
import sched_zerobuf_compare as Z

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "global_ring_msg_size.json"
HTML_PATH = ROOT / "results" / "report_global_ring_algo.html"

SZ, H, V, CROSS = 16, 4, 6, 6
N = SZ * SZ
DIA_RING = "#2563eb"
DIA_CROSS = "#ea580c"
QUAD_BG = ["#eff6ff", "#f0fdf4", "#fff7ed", "#faf5ff"]


def esc(s):
    return html.escape(str(s))


CSS = """
:root { --bg:#f7f8fb; --card:#fff; --text:#0f172a; --muted:#64748b;
        --accent:#1e3a8a; --code:#0b1021; --codetx:#e6edf3; }
* { box-sizing:border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin:0; padding:28px 36px 64px; background:var(--bg); color:var(--text);
       line-height:1.62; max-width:1160px; }
h1 { font-size:1.7rem; margin:0 0 6px; }
h2 { font-size:1.22rem; margin:26px 0 10px; color:var(--accent);
     border-bottom:2px solid #e2e8f0; padding-bottom:5px; }
h3 { font-size:1.04rem; margin:18px 0 8px; color:#334155; }
.card { background:var(--card); border:1px solid #e2e8f0; border-radius:12px;
        padding:20px 24px; margin:16px 0; box-shadow:0 1px 2px rgba(0,0,0,.03); }
.meta { color:var(--muted); font-size:.9rem; }
table { border-collapse:collapse; width:100%; font-size:.9rem; margin:12px 0; }
th, td { border:1px solid #e2e8f0; padding:7px 10px; text-align:center; }
th { background:#f1f5f9; }
td:first-child { text-align:left; }
tr.best td { background:#ecfdf5; font-weight:600; }
.note { color:var(--muted); font-size:.86rem; }
code { font-family:"SF Mono",Menlo,Consolas,monospace; font-size:.86em;
       background:#eef2f7; padding:1px 5px; border-radius:4px; }
pre { background:var(--code); color:var(--codetx); padding:16px 18px;
      border-radius:10px; overflow-x:auto; font-size:.84rem; line-height:1.5;
      font-family:"SF Mono",Menlo,Consolas,monospace; }
pre .kw { color:#ff7b72; } pre .fn { color:#d2a8ff; }
pre .cm { color:#8b949e; font-style:italic; }
.shape-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:20px; }
figure.shape { margin:0; }
figure.shape figcaption { font-size:.82rem; color:#475569; margin-top:8px; line-height:1.45; }
.def { background:#f8fafc; border-left:3px solid #94a3b8; padding:8px 14px;
       margin:10px 0; border-radius:0 8px 8px 0; }
ul.steps { margin:8px 0; padding-left:20px; }
ul.steps li { margin:5px 0; }
.formula { background:#fbfdff; border:1px solid #e2e8f0; border-radius:8px;
           padding:12px 16px; margin:10px 0; font-size:.95rem; overflow-x:auto; }
"""

MATHJAX = """
<script>
MathJax = { tex: { inlineMath: [['\\\\(','\\\\)']], displayMath: [['\\\\[','\\\\]']] } };
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js" async></script>
"""


def setup():
    fr.cfg(SZ, SZ, H, V, cross=CROSS)
    Z.cfg(SZ, SZ, H, V)
    Z.edge_lat = fr.link_lat


def ring_shapes():
    return {
        "rect": fr.ham_cycle_rect(0, 0, SZ, SZ),
        "vflip": fr.ham_cycle_rect_vflip(0, 0, SZ, SZ),
        "vband": fr.ham_cycle_vband(SZ, 0),
    }


SHAPE_DESC = {
    "rect": "水平 snake：底行 (y=0) 为脊，逐列向上/向下梳齿闭合。",
    "vflip": "rect 的垂直镜像：顶行 (y=15) 为脊。",
    "vband": "竖脊 comb：左列 (x=0) 为脊，逐行向右/向左梳齿闭合。",
}


SRC_ORDERS = {
    "natural": lambda: list(range(N)),
    "corner": lambda: sorted(
        range(N),
        key=lambda s: -(abs(fr.coord(s)[0] - (SZ - 1) / 2) + abs(fr.coord(s)[1] - (SZ - 1) / 2)),
    ),
    "rev": lambda: list(range(N - 1, -1, -1)),
}


def eval_shape(order, bidir, ramp, flits=1):
    pos = {nd: k for k, nd in enumerate(order)}
    foot = {
        s: Z.fp_ring(s, order, pos, bidir, ramp)
        for s in range(N)
    }
    best = None
    for name, gen in SRC_ORDERS.items():
        mk, mo, busy = Z.pack(foot, ramp, gen(), flits=flits)
        ok = Z.verify(busy, ramp, flits=flits)
        if best is None or (ok and mk < best["makespan"]):
            best = {"makespan": mk, "max_offset": mo, "src_order": name, "ok": ok}
    return best


def eval_all(flits):
    shapes = ring_shapes()
    out = {}
    for sname, order in shapes.items():
        out[sname] = {}
        for bidir, tag in ((False, "uni"), (True, "bi")):
            ramp = 1 if not bidir else 2
            out[sname][tag] = eval_shape(order, bidir, ramp, flits)
    return out


def replay_bound(mk1, flits):
    return flits * mk1


def best_overall(all_m):
    picks = []
    for m, shapes in all_m.items():
        for sname, dirs in shapes.items():
            for tag, rec in dirs.items():
                if not rec.get("ok"):
                    continue
                ramp = 1 if tag == "uni" else 2
                mk1 = all_m[1][sname][tag]["makespan"]
                rep = replay_bound(mk1, m)
                final = min(rec["makespan"], rep)
                picks.append({
                    "m": m, "ramp": ramp, "dir": tag, "shape": sname,
                    "makespan": rec["makespan"], "mk_final": final,
                    "replay_bound": rep,
                    "bound_source": "replay" if final == rep < rec["makespan"] else "pack",
                    "src_order": rec["src_order"],
                    "ok": rec["ok"],
                })
    return picks


# ---------------------------------------------------------------------------
# SVG diagrams (16×16)
# ---------------------------------------------------------------------------
def _dia_defs():
    return (
        "<defs>"
        '<marker id="ah-ring" markerWidth="8" markerHeight="8" refX="5.5" refY="2.5" '
        'orient="auto"><path d="M0,0 L5.5,2.5 L0,5 z" fill="' + DIA_RING + '"/></marker>'
        '<marker id="ah-cross" markerWidth="8" markerHeight="8" refX="5.5" refY="2.5" '
        'orient="auto"><path d="M0,0 L5.5,2.5 L0,5 z" fill="' + DIA_CROSS + '"/></marker>'
        "</defs>"
    )


def _grid(cell=14):
    pad, topgap = 24, 22
    W = SZ * cell + 2 * pad
    Ht = SZ * cell + 2 * pad + topgap
    px = lambda x: pad + x * cell + cell / 2
    py = lambda y: topgap + pad + (SZ - 1 - y) * cell + cell / 2
    el = []
    for qi, (qx, qy) in enumerate([(0, 0), (8, 0), (0, 8), (8, 8)]):
        x = pad + qx * cell
        y = topgap + pad + (SZ - qy - 8) * cell
        el.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{8*cell:.1f}" height="{8*cell:.1f}" '
            f'fill="{QUAD_BG[qi]}" stroke="#cbd5e1" stroke-width="0.6"/>'
        )
    for yy in range(SZ):
        for xx in range(SZ):
            el.append(f'<circle cx="{px(xx):.1f}" cy="{py(yy):.1f}" r="1.4" fill="#94a3b8"/>')
    # reticle boundaries (cols 7|8, rows 7|8)
    bx = pad + 7.5 * cell
    by = topgap + pad + (SZ - 8) * cell - 0.5 * cell
    el.append(f'<line x1="{bx:.1f}" y1="{topgap+pad:.1f}" x2="{bx:.1f}" '
              f'y2="{topgap+pad+SZ*cell:.1f}" stroke="#64748b" stroke-width="1.2" stroke-dasharray="4 3"/>')
    el.append(f'<line x1="{pad:.1f}" y1="{by:.1f}" x2="{pad+SZ*cell:.1f}" '
              f'y2="{by:.1f}" stroke="#64748b" stroke-width="1.2" stroke-dasharray="4 3"/>')
    return W, Ht, px, py, el


def _poly_pts(order, px, py):
    return " ".join(f"{px(fr.coord(nd)[0]):.1f},{py(fr.coord(nd)[1]):.1f}" for nd in order)


def shape_svg(name, order):
    W, Ht, px, py, el = _grid()
    n = len(order)
    cross_edges = []
    for i in range(n):
        u, v = order[i], order[(i + 1) % n]
        x1, y1 = px(fr.coord(u)[0]), py(fr.coord(u)[1])
        x2, y2 = px(fr.coord(v)[0]), py(fr.coord(v)[1])
        if fr.quad_of(u) != fr.quad_of(v):
            cross_edges.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{DIA_CROSS}" stroke-width="2.2" marker-end="url(#ah-cross)"/>'
            )
    pts = _poly_pts(order, px, py)
    x0, y0 = px(fr.coord(order[0])[0]), py(fr.coord(order[0])[1])
    el.append(
        f'<polyline points="{pts}" fill="none" stroke="{DIA_RING}" stroke-width="1.5" '
        f'opacity="0.35"/>'
    )
    el.extend(cross_edges)
    el.append(
        f'<polyline points="{pts}" fill="none" stroke="{DIA_RING}" stroke-width="1.0" '
        f'marker-end="url(#ah-ring)"/>'
    )
    el.append(f'<circle cx="{x0:.1f}" cy="{y0:.1f}" r="3.5" fill="{DIA_RING}"/>')
    nc = sum(1 for i in range(n) if fr.quad_of(order[i]) != fr.quad_of(order[(i + 1) % n]))
    titles = {"rect": "rect · 水平 snake", "vflip": "vflip · 垂直镜像", "vband": "vband · 竖脊 comb"}
    cap = (
        f"{SHAPE_DESC[name]} 虚线＝reticle 边界；"
        f"橙箭头＝跨 reticle hop（固定 {CROSS} cy，无 AFIFO 等待）；"
        f"蓝线＝Hamilton 环走向（{nc} 个跨区 hop）。"
    )
    return (
        f'<figure class="shape"><svg width="{W}" height="{Ht}" viewBox="0 0 {W} {Ht}" '
        f'xmlns="http://www.w3.org/2000/svg" style="max-width:100%">'
        f'{_dia_defs()}'
        f'<text x="8" y="14" font-size="11" font-weight="bold" fill="#1e3a8a">'
        f'{esc(titles[name])}</text>'
        + "".join(el)
        + "</svg>"
        f"<figcaption>{esc(cap)}</figcaption></figure>"
    )


def shape_diagrams_section(all_m1):
    shapes = ring_shapes()
    figs = [shape_svg(name, order) for name, order in shapes.items()]
    mk_rows = []
    for name in shapes:
        u = all_m1[name]["uni"]["makespan"]
        b = all_m1[name]["bi"]["makespan"]
        mk_rows.append(f"<tr><td><code>{name}</code></td><td>{u}</td><td>{b}</td></tr>")
    return (
        "<div class='shape-grid'>" + "".join(figs) + "</div>"
        "<table><thead><tr><th>形状</th><th>uni @ ramp=1</th><th>bi @ ramp=2</th></tr></thead>"
        "<tbody>" + "".join(mk_rows) + "</tbody></table>"
    )


def results_table(picks):
    key_order = [(4, 1), (4, 2), (5, 1), (5, 2)]
    by_key = {(p["m"], p["ramp"]): p for p in picks}
    body = []
    for m, ramp in key_order:
        p = by_key.get((m, ramp))
        if not p:
            continue
        elb = (255 * m + ramp - 1) // ramp
        ratio = p["mk_final"] / elb
        body.append(
            f"<tr class='best'><td>m = {m}</td><td>{ramp}</td><td>{p['dir']}</td>"
            f"<td><code>{esc(p['shape'])}</code></td><td>{esc(p['src_order'])}</td>"
            f"<td><b>{p['mk_final']}</b></td><td>{p['makespan']}</td><td>{p['replay_bound']}</td>"
            f"<td>{elb}</td><td>{ratio:.2f}×</td></tr>"
        )
    return (
        "<table><thead><tr><th>报文</th><th>下 ramp</th><th>方向</th>"
        "<th>Hamilton 形状</th><th>pack 源序</th><th>mk*</th><th>pack</th>"
        "<th>m×mk(1)</th><th>eject 下界</th><th>比值</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table>"
    )


def build(all_m1, all_m4, all_m5, picks):
    updated = "rigid pack, cross=6"
    try:
        updated = json.loads(JSON_PATH.read_text()).get("updated", updated)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    meta = (
        f"16×16 mesh，N=256。 H={H} cy / V={V} cy；"
        f"跨 reticle 边界链路固定 {CROSS} cy（<b>无 AFIFO 等待</b>）；"
        f"router_buf=0，刚性 offset pack。 数值由 <code>sched_zerobuf_compare.pack</code> 复算。"
    )

    formal = r"""
<div class="def"><b>定义（输入）。</b>
mesh \(G=(V,E)\)，\(|V|=N\)；水平链路时延 \(H\)、垂直时延 \(V_{\!}\)；
reticle 划分 \(\mathcal R\)（16×16 时为四个 8×8 象限）；
跨 reticle 物理链路经 AFIFO 接口，<b>传播时延固定为 \(L_x\)</b>（本例 \(L_x=6\) cy），
<b>不允许在 AFIFO 中排队等待</b>；
报文长度 \(m\) flit（wormhole）；下 ramp 带宽 \(\rho\) flit/cy/node。</div>

<div class="formula">\[
\ell(u,v)=\begin{cases} L_x & \mathrm{ret}(u)\neq\mathrm{ret}(v)\ \text{（跨 reticle，固定延迟）}\\
H & \text{同 reticle，水平}\\ V_{\!} & \text{同 reticle，垂直}\end{cases}
\]</div>

<p><b>刚性 0-buffer 模型。</b> 每个源 \(s\) 的投递结构（沿 Hamilton 环转发）一旦确定，
各链路与 eject 的相对时刻即固定；唯一可调参数是源侧注入偏移 \(\mathit{inject}_s\)
（数据暂存于 PE/SRAM，<b>非 router 缓冲</b>）。 滑动 \(\mathit{inject}_s\) 使全局日历满足：</p>

<div class="formula">\[
\begin{aligned}
&\text{(L)}\ \forall e,t:\ \text{链路 }e\text{ 在 }[t,t{+}m)\ \text{至多 1 flit},\\
&\text{(R)}\ \forall c,t:\ \text{节点 }c\text{ 下 ramp 在 }[t,t{+}m)\ \text{至多 }\rho\text{ flit},\\
&\text{(C)}\ \forall c:\ \text{eject@}c = (N-1)\,m .
\end{aligned}
\]</div>

<p>全局 Hamilton 环 \(\pi=(v_0,\ldots,v_{N-1})\) 闭合且相邻为 mesh 邻居。
源 \(s\) 在环上注入后沿 \(\pi\) 逐 hop 转发；hop 延迟累加 \(\ell(\cdot,\cdot)\)。
makespan 为最晚 eject 完成时刻。</p>

<div class="formula">\[
\boxed{\ \mathrm{MK}^\star(m)=\min\Big\{\min_{\pi,\,dir,\,\sigma}\mathrm{MK}(\pi,\sigma),\ \ m\cdot \mathrm{MK}^\star(1)\Big\}\ }
\qquad
\mathrm{MK}^\star(m)\ge \Big\lceil \tfrac{(N-1)m}{\rho}\Big\rceil
\]</div>
"""

    pseudo = """<pre><span class="cm"># 刚性 pack —— 跨 message size 最优 makespan</span>
<span class="kw">def</span> <span class="fn">best_makespan</span>(G, H, V, Lx, m, ramp):
    Cands &larr; { rect, vflip, vband, ... }           <span class="cm"># 合法 Hamilton 环</span>
    best &larr; &infin;
    <span class="kw">for</span> order <span class="kw">in</span> Cands:
        foot &larr; { s : fp_ring(order, s, dir) <span class="kw">for</span> s &isin; V }   <span class="cm"># hop 用 &ell;(u,v)</span>
        <span class="kw">for</span> src_order <span class="kw">in</span> {natural, corner, ring, ...}:
            mk &larr; pack(foot, ramp, src_order, flits=m)   <span class="cm"># 滑动 inject_s</span>
            best &larr; min(best, mk)
    <span class="kw">return</span> min(best, m &times; best_makespan(..., m=1, ...))</pre>"""

    nat = r"""
<ol class="steps">
<li><b>候选 Hamilton 环。</b> 在 16×16 上生成合法闭合环（见 §2 示意图）：
<code>rect</code>、<code>vflip</code>、<code>vband</code> 等；验证相邻节点为 mesh 邻居。</li>
<li><b>刚性 footprint。</b> 对每个源 \(s\)，按选定环顺序构造
<code>fp_ring</code>：记录每条有向链路 \((p\!\to\!c)\) 的发送时刻与每个节点的 eject 时刻；
相邻 hop 间隔 \(\ell(p,c)\)——同 reticle 内为 \(H\) 或 \(V_{\!}\)，跨 reticle 为固定 \(L_x=6\)。
<b>中间 router 不缓冲</b>，不存在 AFIFO 排队。</li>
<li><b>offset pack。</b> 按源序逐个放置 footprint：为源 \(s\) 找最小非负 \(\mathit{inject}_s\)，
使新增 footprint 不与已有链路/下 ramp 日历冲突（wormhole 占连续 \(m\) 周期）。
冲突则 \(\mathit{inject}_s\mathrel{+}=1\)。 多试几种源序（natural / corner / ring …），取最小 makespan。</li>
<li><b>跨 message size。</b> 对每个 \(m\)，重复 pack（flits=\(m\)）；
再与 \(m\cdot\mathrm{MK}^\star(1)\)（串行重放 \(m\) 次 \(m{=}1\) 最优 allgather）取小。</li>
<li><b>输出。</b> 返回 \(\mathrm{MK}^\star(m)\) 及对应的 \((\pi, dir, src\_order)\)。</li>
</ol>

<h3>形状选择规律（16×16，\(L_x=6\)）</h3>
<ul class="steps">
<li>不同 Hamilton 走法改变环上各链路的负载与跨 reticle hop 的时空分布；pack 在<b>无 AFIFO 等待</b>约束下
必须为跨区 hop 留出刚性时间窗，故形状直接影响可行性与 makespan。</li>
<li><code>vband</code> 与 <code>rect</code>/<code>vflip</code> 的跨区 hop 数量与位置不同，
在 \(m\) 较大时可能导致不同的 pack 紧密度（见 §4 数值表）。</li>
<li>ramp=2 时双向环（bi）通常优于单向（uni），因 eject 带宽翻倍。</li>
</ul>
"""

    shapes_html = shape_diagrams_section(all_m1)

    page = f"""<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>全局 Hamilton 环 Allgather · 刚性 pack 最优 makespan 算法</title>
{MATHJAX}<style>{CSS}</style></head><body>
<h1>全局 Hamilton 环 Allgather：跨 message size 的最优 makespan 生成算法</h1>
<p class='meta'>{meta}</p>

<div class='card'>
<h2>1. 模型与问题</h2>
<p>在 16×16 mesh 上用<b>单条全局 Hamilton 环</b>（Q=1）做 allgather。
本模型为<b>刚性 0-buffer pack</b>（与 <code>sched_zerobuf_compare</code> 一致）：
router 零缓冲、链路无排队；跨 reticle 边界链路<b>仅增加固定 6 cycle 传播延迟</b>，
<b>不在 AFIFO 中等待</b>。 对 wormhole 报文 \(m\) flit，求最小 makespan 及最优环走法。</p>
{formal}
</div>

<div class='card'>
<h2>2. Hamilton 环形状示意图（16×16）</h2>
<p class='note'>四象限底色＝8×8 reticle；灰点＝节点；蓝线＝环路径；橙色箭头＝跨 reticle hop（ℓ=L<sub>x</sub>=6 cy）。</p>
{shapes_html}
</div>

<div class='card'>
<h2>3. 形式化算法</h2>
{pseudo}
<p class='note'>实现：<code>utils/sched_zerobuf_compare.py</code> 的 <code>fp_ring</code> + <code>pack</code>；
跨区 hop 用 <code>sim_fused_rings.link_lat</code>（cross={CROSS}）。 本页：
<code>utils/gen_global_ring_algo.py</code>。</p>
</div>

<div class='card'>
<h2>4. 自然语言算法</h2>
{nat}
</div>

<div class='card'>
<h2>5. 最优 makespan（刚性 pack，cross={CROSS} cy）</h2>
{results_table(picks)}
<p class='note'>mk* = min(pack, m×mk(1))。 eject 下界 = ⌈255·m / ramp⌉。 每个 (m, ramp) 取 uni@1 / bi@2 中 pack 更优方向。</p>
</div>

<div class='card'>
<h2>6. 与 fork 报告 ring (Q=1) 的差异</h2>
<p><code>report_fork_msg_size.html</code> 中 ring (Q=1) 同为刚性 pack，但默认跨区时延 cross=10 cy。
本模型将跨 reticle AFIFO 链路时延改为 <b>6 cy</b> 且强调<b>无 AFIFO 等待</b>（纯固定延迟累加）。
两者均非 <code>sched_ring_zerobuf</code> 的 AFIFO 排队调度。</p>
</div>

</body></html>"""
    return page


def main():
    setup()
    all_m1 = eval_all(1)
    all_m4 = eval_all(4)
    all_m5 = eval_all(5)
    picks = []
    for m, data in ((4, all_m4), (5, all_m5)):
        for sname, dirs in data.items():
            for tag, rec in dirs.items():
                if not rec.get("ok"):
                    continue
                ramp = 1 if tag == "uni" else 2
                mk1 = all_m1[sname][tag]["makespan"]
                rep = replay_bound(mk1, m)
                final = min(rec["makespan"], rep)
                picks.append({
                    "m": m, "ramp": ramp, "dir": tag, "shape": sname,
                    "makespan": rec["makespan"], "mk_final": final,
                    "replay_bound": rep,
                    "bound_source": "replay" if final == rep < rec["makespan"] else "pack",
                    "src_order": rec["src_order"], "ok": rec["ok"],
                })
    # per (m,ramp) keep best over shapes and dirs
    best = {}
    for p in picks:
        k = (p["m"], p["ramp"])
        if k not in best or p["mk_final"] < best[k]["mk_final"]:
            best[k] = p
    HTML_PATH.write_text(build(all_m1, all_m4, all_m5, list(best.values())))
    print(f"wrote {HTML_PATH}")
    for p in sorted(best.values(), key=lambda x: (x["m"], x["ramp"])):
        print(f"  m={p['m']} ramp={p['ramp']} {p['dir']} {p['shape']} mk={p['mk_final']}")


if __name__ == "__main__":
    main()
