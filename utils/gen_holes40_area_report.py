#!/usr/bin/env python3
"""HTML report: 40-compute / 8-hole allgather makespan × area Pareto."""

from __future__ import annotations

import html
import json
from pathlib import Path

import ppa_analytic_model as PPA
from dse_axis_area_makespan import A_FLIT, CROSSBAR_PORT, GATHER_DEPTH
from dse_tree_allgather_6x8 import nid
from dse_holes_40_allgather import svg_wing_comb

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "holes40_area_makespan.json"
HTML_PATH = ROOT / "results" / "report_holes40_area_makespan.html"


def esc(v) -> str:
    return html.escape(str(v))


def main() -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    lb = data["model"]["lower_bound_m1"]
    pts = data["points"]
    front = data["global_pareto"]
    meta = data["scheme_meta"]
    floors = data["scheme_floors"]
    gen = esc(data["generated_at"])
    amap = data["model"]["ascii_map"].replace("\n", "<br>")
    svg = data.get("demo_svg", "")
    demo_xy = data.get("demo_svg_source", [0, 0])
    wing_src = nid(1, 1)
    wing_svg = svg_wing_comb(wing_src, bridge_y=5)
    on_pareto = sorted({p["scheme"] for p in front})
    other_floor = min(floors[k]["t1"] for k in floors if k != "axis_ccw")
    wing_floor = floors.get("wing_comb_y5", {})

    scheme_rows = "".join(
        f"<tr><td class='l'>{esc(meta[k]['label'])}</td>"
        f"<td>{meta[k]['pmax']}</td><td>{meta[k]['issue']}</td>"
        f"<td>{meta[k]['dilation']}</td>"
        f"<td>{floors[k]['t1']}</td><td>{floors[k]['area_at_t1']:.4f}</td>"
        f"<td>{floors[k]['t5']}</td>"
        f"<td>{'是' if k in on_pareto else '否'}</td></tr>"
        for k in meta
    )
    front_rows = "".join(
        f"<tr><td>{p['area_total']:.4f}</td>"
        f"<td class='{'win' if p['makespan']==lb else ''}'>{p['makespan']}</td>"
        f"<td class='l'>{esc(p['label'])}</td>"
        f"<td>W{p['W']}/E{p['E']}/B{p['B']}</td></tr>"
        for p in front
    )
    mf_avg = data.get("pareto_area_tavg", [])
    mf_t15 = data.get("pareto_t1_t5", [])
    mf_avg_rows = "".join(
        f"<tr><td>{p['area_total']:.4f}</td><td>{p['t_avg']}</td>"
        f"<td>{p['t1']}</td><td>{p.get('ii_eff')}</td><td>{p['t5']}</td>"
        f"<td class='l'>{esc(p['label'])}</td>"
        f"<td>W{p['W']}/E{p['E']}/B{p['B']}</td></tr>"
        for p in mf_avg
    )
    mf_t15_rows = "".join(
        f"<tr><td>{p['t1']}</td><td>{p['t5']}</td><td>{p.get('ii_eff')}</td>"
        f"<td>{p.get('delta2_min')}/{p.get('delta2_avg')}/{p.get('delta2_max')}</td>"
        f"<td class='l'>{esc(p['label'])}</td>"
        f"<td>W{p['W']}/E{p['E']}/B{p['B']}</td></tr>"
        for p in mf_t15
    )

    best_t1_key = min(floors, key=lambda k: floors[k]["t1"])
    best_t5_key = min(floors, key=lambda k: floors[k]["t5"])
    # Competitive T5 among schemes that stay near 1-flit LB (T1 ≤ LB+30)
    near_lb = [k for k in floors if floors[k]["t1"] <= lb + 30]
    best_t5_near = min(near_lb, key=lambda k: floors[k]["t5"]) if near_lb else best_t5_key
    unc = int(PPA.CALIBRATION_UNCERTAINTY * 100)
    baseline_total = (PPA.BASELINE_CROSSBAR + PPA.BASELINE_BUFFERS
                      + PPA.BASELINE_CONTROL)
    sram_port_coeff = 0.5 * A_FLIT * GATHER_DEPTH

    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>40-compute / 8-hole：makespan × 面积 Pareto</title>
<style>
:root{{--bg:#f8fafc;--card:#fff;--text:#0f172a;--muted:#64748b;--line:#cbd5e1;--win:#dcfce7;}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
margin:0;padding:28px 32px 64px;line-height:1.55;max-width:1120px}}
h1{{font-size:1.5rem;margin:0 0 4px}} h2{{font-size:1.15rem;color:#1e3a8a;margin:0 0 12px}}
h3{{font-size:.98rem;color:#334155;margin:16px 0 6px}}
.sub,.note{{color:var(--muted);font-size:.86rem}}
.card{{background:var(--card);border:1px solid #e2e8f0;border-radius:10px;padding:18px 22px;margin:16px 0}}
.hero{{border-color:#93c5fd;background:linear-gradient(180deg,#eff6ff,#fff)}}
table{{border-collapse:collapse;width:100%;font-size:.82rem;margin:8px 0}}
th,td{{border:1px solid var(--line);padding:6px 9px;text-align:center}}
th{{background:#e2e8f0}} td.l{{text-align:left}} td.win{{background:var(--win);font-weight:700}}
ul{{margin:6px 0;padding-left:22px}} li{{margin:5px 0}}
.formula{{background:#f1f5f9;border-left:3px solid #93c5fd;border-radius:6px;padding:10px 14px;
margin:8px 0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;
white-space:pre-wrap;line-height:1.5}}
code{{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:.9em}}
img{{max-width:100%;border:1px solid #e2e8f0;border-radius:8px;margin:8px 0}}
.map{{font-family:ui-monospace,monospace;background:#f1f5f9;padding:12px;border-radius:8px;line-height:1.4}}
.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}}
.kpi div{{background:#f1f5f9;border-radius:8px;padding:12px 14px}}
.kpi b{{display:block;font-size:1.3rem;color:#1d4ed8}} .kpi span{{font-size:.78rem;color:var(--muted)}}
</style></head><body>

<h1>40 计算节点 / 8 非计算节点：allgather makespan × 面积 Pareto（E≡2）</h1>
<p class="sub">8×6 mesh · H=7 · V=9 · <b>Eject E≡2</b>（仅扫 W/B）·
非计算节点 = 1-indexed 第4–5列 × 第1–4行（<b>非坏点</b>：router/链路可用，仅不 inject/eject）·
{len(pts)} 个 (scheme,W,E=2,B) 点 · 数据 <code>holes40_area_makespan.json</code> · 生成 {gen}</p>

<div class="card hero">
<h2>核心结论</h2>
<div class="kpi">
  <div><b>{lb}</b><span>1-flit 形式下界</span></div>
  <div><b>{floors[best_t1_key]['t1']}</b><span>最优 T1（{esc(meta[best_t1_key]['label'])}）</span></div>
  <div><b>{floors[best_t5_near]['t5']}</b><span>近 LB 最优 T5（{esc(meta[best_t5_near]['label'])}）</span></div>
  <div><b>{len(on_pareto)}</b><span>触及全局 Pareto 的方案数</span></div>
</div>
<ul>
<li><b>1-flit 达界</b>：{esc(meta[best_t1_key]['label'])} 在合适 (W,E,B) 下可到
T1={floors[best_t1_key]['t1']}（= LB）。注入序「距非计算块中心最远优先」是关键；
排图可自由使用 H 上仍可用的 router/链路作中转。</li>
<li><b>低时延带 [{lb}, {other_floor})</b> 由 axis+CCW pruned 独占（与全 mesh 报告同构）。</li>
<li><b>5-flit（近 LB）</b>：T1≤LB+30 时最优 T5 为 {esc(meta[best_t5_near]['label'])}
= {floors[best_t5_near]['t5']}；全局 T5 地板 {floors[best_t5_key]['t5']}
属 {esc(meta[best_t5_key]['label'])}，但其 T1={floors[best_t5_key]['t1']} 过高，不宜单独作吞吐选型。</li>
<li>双翼方案仅在<strong>极低面积 / 高 makespan</strong>端进入全局 Pareto；贴 LB 时仍是 axis。</li>
</ul>
</div>

<div class="card">
<h2>1. 拓扑</h2>
<p><b>C</b> = 计算节点（allgather inject/eject 端点）；
<b>H</b> = 非计算节点——<b>不是坏点</b>：该处 router 与相邻链路仍完全可用，只是没有参与
本次集合通信的 PE，故不 inject / 不 eject，仅作中转。</p>
<ul>
<li>全 mesh 的物理连通性不变；形式下界仍按 Manhattan（允许穿 H）计算。</li>
<li>橙虚线 = 路径经过<strong>活着的</strong> H router，不是绕开故障。</li>
</ul>
<div class="map">{amap}<br>x=0⋯7 →</div>
<div style="margin-top:12px">{svg}
<p class="note">示意：axis+CCW 剪枝树，源 ({demo_xy[0]},{demo_xy[1]})；蓝=C↔C，橙虚线=经 H router（H≠故障）。</p>
</div>
</div>

<div class="card">
<h2>1b. dual-wing comb（wing_comb_y5）示意</h2>
<p>把 40 个<strong>计算端点</strong>分成<strong>左翼</strong>（x≤2）与<strong>右翼</strong>（x≥5）；
中间列（x=3,4，y=0…3）是非计算节点——<b>router/链路仍在</b>，只是本方案选择不在其上
汇聚跨翼流量。跨翼流量一律先走到顶行 <code>y=5</code>（整行皆为 C），沿该行东西桥接，
再沿目标列竖直下探——形如梳子：顶行是脊（bridge spine），各列是齿（teeth）。</p>
<ul>
<li>单源路径：源列 ↑ 到 y=5 → 沿 bridge 到目的列 → ↓ 到目的行；再对路径并取 BFS 成树。</li>
<li>与 <code>wing_bridge_y4</code> 对比：后者桥在 y=4，跨翼会经过 H 的<strong>可用</strong> router；
comb 把桥抬到 y=5，是<strong>路由形态选择</strong>（集中到 C 行），不是因为 H 不可用。</li>
<li>代价：dilation / T1 较高（本扫点地板 T1={wing_floor.get('t1','?')}），
但 issue 可低至 2 → 日历面积略省，故能出现在极低面积端的 1-flit Pareto。</li>
</ul>
<div style="margin-top:8px;overflow-x:auto">{wing_svg}</div>
<p class="note">上图源 = (1,1)（左翼）；金黄带 = bridge y=5；蓝竖线 = 齿；L/R 底色区分双翼；
灰框 = 非计算区（router 仍可用，本树未使用）。</p>
</div>

<div class="card">
<h2>2. 散点图与全局 Pareto（1-flit）</h2>
<img src="holes40_area_makespan.png" alt="holes40 makespan vs area">
<p class="note">细线=各方案自身前沿；黑线=全局 Pareto；蓝虚线=LB={lb}。</p>
</div>

<div class="card">
<h2>3. 各方案特征与地板</h2>
<table>
<thead><tr><th>方案</th><th>Pmax*</th><th>issue*</th><th>dilation</th>
<th>T1 地板</th><th>T1 地板面积</th><th>T5 地板</th><th>全局Pareto</th></tr></thead>
<tbody>{scheme_rows}</tbody></table>
<p class="note">* Pmax/issue 由 alive 树在 offset=0 叠加下估计（用于日历面积），非综合结果。
面积 = 1.0 + 时隙表(Pmax,issue) + CalFork({data['model']['multicast_delta']}) + eject(W,E,B)。</p>
</div>

<div class="card">
<h2>4. 全局 Pareto 明细（1-flit）</h2>
<table>
<thead><tr><th>面积(归一)</th><th>makespan</th><th>方案</th><th>W/E/B</th></tr></thead>
<tbody>{front_rows}</tbody></table>
</div>

<div class="card">
<h2>5. 多 flit（R=5）实测 Pareto</h2>
<img src="holes40_multiflit_area_makespan.png" alt="holes40 multiflit">
<p class="note">T5 / II_eff / delta2 由自由多轮打包测得；T_avg=(T1+T5)/2。
cyclic 链路复用下界≠逐源第2 flit 间隔（delta2）。</p>
<table>
<thead><tr><th>面积</th><th>T_avg</th><th>T1</th><th>II_eff</th><th>T5</th><th>方案</th><th>W/E/B</th></tr></thead>
<tbody>{mf_avg_rows}</tbody></table>
<table>
<thead><tr><th>T1</th><th>T5</th><th>II_eff</th><th>delta2 min/avg/max</th><th>方案</th><th>W/E/B</th></tr></thead>
<tbody>{mf_t15_rows}</tbody></table>
</div>

<div class="card">
<h2>6. 建议</h2>
<ul>
<li><b>贴 1-flit LB={lb}</b>：axis+CCW pruned（可用 H router 作中转）+ 远非计算块优先注入序；
E≡2 下配足 W/B（达界面积≈{floors[best_t1_key]['area_at_t1']:.4f}）。</li>
<li><b>多 flit（近 LB）</b>：axis T5={floors['axis_ccw']['t5']}，NEC-3 T5={floors['nec3']['t5']}；
看 delta2 / II_eff，勿把 cyclic 链路复用下界当成 II。</li>
<li><b>面积极省</b>：可看 dual-wing 低端 Pareto 点，但 T1≥141，远离 LB。</li>
</ul>
</div>

<div class="card">
<h2>7. 面积模型（与全 mesh 报告同口径）</h2>
<ul>
<li>归一到 5 端口 512b IQ-XY = 1.00；解析模型（非综合）；不绑定具体工艺节点。</li>
<li>标定：CalFork 多播 +{PPA.CALFORK_MC_DELTA}；增量不确定度 ±{unc}%。</li>
</ul>
<div class="formula">IQ-XY = crossbar {PPA.BASELINE_CROSSBAR} + buffers {PPA.BASELINE_BUFFERS} + control {PPA.BASELINE_CONTROL} = {baseline_total:.2f}
总面积 = 1.00 + calendar(Pmax)·issue + CalFork + Δcrossbar(W) + Δbuffer(W,E,B) + Δsram(E)
Δcrossbar = {CROSSBAR_PORT}·(W−1)
Δbuffer   = {A_FLIT}·B·(W+E)/2
Δsram     = {sram_port_coeff:.4f}·(E−1)</div>
</div>

<p class="note">生成：<code>utils/gen_holes40_area_report.py</code> ·
DSE：<code>utils/dse_holes40_area_makespan.py</code>（复用 holes_40 树 / multi_area 面积模型）</p>
</body></html>"""
    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
