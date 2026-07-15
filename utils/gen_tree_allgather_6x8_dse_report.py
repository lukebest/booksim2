#!/usr/bin/env python3
"""Generate the formal-bound and Pareto report for tree allgather DSE."""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "tree_allgather_6x8_dse.json"
HTML_PATH = ROOT / "results" / "report_tree_allgather_6x8_dse.html"

LABELS = {
    "dim_xy": "dim-XY",
    "dim_yx": "dim-YX",
    "axis_ccw": "axis+CCW",
    "nec3": "NEC-3",
    "nec2": "NEC-2",
    "comb_fixed_west": "fixed-west comb",
    "hamilton_bi_tree": "Hamilton bi-tree",
    "hamilton_uni_tree": "Hamilton uni-tree",
}


def esc(value) -> str:
    return html.escape(str(value))


def lower_bound_table(data: dict) -> str:
    rows = []
    for m, rec in data["formal_lower_bounds"].items():
        rows.append(
            "<tr>"
            f"<td>{m}</td><td><b>{rec['T_lb']}</b></td>"
            f"<td>{rec['diameter_serialization']}</td>"
            f"<td>{rec['receiver_release']}</td>"
            f"<td>{rec['eject_duration']}</td>"
            f"<td>{rec['corner_cut']}</td><td>{rec['bisection']}</td>"
            f"<td class='l'>{esc(', '.join(rec['binding']))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>m</th><th>形式化 T<sub>LB</sub></th>"
        "<th>直径+串行</th><th>receiver release</th><th>eject duration</th>"
        "<th>corner cut</th><th>bisection</th><th>binding</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def makespan_table(data: dict) -> str:
    schemes = data["schemes"]
    names = list(schemes)
    header = "".join(f"<th>{esc(LABELS[n])}</th>" for n in names)
    rows = []
    for m, lb in data["formal_lower_bounds"].items():
        values = {n: schemes[n]["messages"][m]["makespan"] for n in names}
        best = min(values.values())
        cells = "".join(
            f"<td class='{'win' if values[n] == best else ''}'>{values[n]}</td>"
            for n in names
        )
        rows.append(f"<tr><td>{m}</td><td>{lb['T_lb']}</td>{cells}</tr>")
    return (
        f"<div class='wide'><table><thead><tr><th>m</th><th>T<sub>LB</sub></th>{header}</tr>"
        f"</thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def uarch_table(data: dict) -> str:
    rows = []
    for name, scheme in data["schemes"].items():
        m1 = scheme["messages"]["1"]
        micro = m1["microarchitecture"]
        direct = scheme["architectures"]["sparse_direct"]
        replay = scheme["architectures"]["sparse_replay_m1"]
        template = scheme["architectures"]["template_direct"]
        rows.append(
            "<tr>"
            f"<td class='l'><b>{esc(LABELS[name])}</b></td>"
            f"<td>{m1['tree']['max_mesh_fanout']}</td>"
            f"<td>{micro['calendar_issue_width']}</td>"
            f"<td>{micro['crossbar_outputs_peak']}</td>"
            f"<td>{micro['topology_period_max']}</td>"
            f"<td>{micro['down_ramp_peak']}</td>"
            f"<td>{direct['normalized_total']:.3f}</td>"
            f"<td>{replay['normalized_total']:.3f}</td>"
            f"<td>{template['normalized_total']:.3f}</td>"
            "</tr>"
        )
    return (
        "<div class='wide'><table><thead><tr><th>方案</th><th>单输入 mesh fanout</th>"
        "<th>calendar issue width</th><th>crossbar 输出峰值</th><th>Pmax</th>"
        "<th>down 峰值</th><th>Sparse direct 面积</th><th>Sparse replay 面积</th>"
        "<th>Template 面积</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def pareto_tables(data: dict, key: str) -> str:
    blocks = []
    for m, points in data["pareto"][key].items():
        rows = "".join(
            "<tr>"
            f"<td class='l'>{esc(LABELS[p['scheme']])}</td>"
            f"<td class='l'>{esc(p['architecture'])}</td>"
            f"<td>{p['area']:.3f}</td><td>{p['makespan']}</td>"
            f"<td>{p['slowdown_vs_lb']:.2f}×</td>"
            "</tr>"
            for p in points
        )
        blocks.append(
            f"<div class='pareto'><h3>m={m}</h3><table><thead><tr><th>方案</th>"
            "<th>实现</th><th>归一化面积</th><th>makespan</th><th>/ TLB</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    return f"<div class='pareto-grid'>{''.join(blocks)}</div>"


def main() -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    generated = esc(data["generated_at"])
    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>6×8 树形 Allgather：形式化下界与 Router DSE</title>
<style>
:root{{--bg:#f8fafc;--card:#fff;--text:#0f172a;--muted:#64748b;--line:#cbd5e1;--accent:#1d4ed8;--win:#dcfce7;}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);
margin:0;padding:28px 32px 64px;line-height:1.55;max-width:1240px}}
h1{{font-size:1.55rem;margin:0 0 4px}} h2{{font-size:1.18rem;color:#1e3a8a;margin:0 0 12px}}
h3{{font-size:.98rem;margin:8px 0}} .sub,.note{{color:var(--muted);font-size:.86rem}}
.card{{background:var(--card);border:1px solid #e2e8f0;border-radius:10px;padding:18px 22px;margin:16px 0}}
.hero{{border-color:#93c5fd}} .lead{{font-size:1.02rem}} .formula{{font-family:ui-monospace,monospace;
background:#f1f5f9;border-radius:6px;padding:9px 12px;margin:7px 0}}
table{{border-collapse:collapse;width:100%;font-size:.8rem}} th,td{{border:1px solid var(--line);
padding:5px 7px;text-align:center;white-space:nowrap}} th{{background:#e2e8f0}} td.l{{text-align:left}}
td.win{{background:var(--win);font-weight:700}} .wide{{overflow-x:auto}}
.pareto-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px}}
.pareto{{border:1px solid #e2e8f0;border-radius:8px;padding:8px 10px}}
ul{{margin:6px 0;padding-left:21px}} li{{margin:5px 0}}
code{{background:#f1f5f9;padding:1px 4px;border-radius:4px}}
</style></head><body>
<h1>6×8 Mesh 树形 Allgather：形式化下界、Router 面积与 Makespan DSE</h1>
<p class="sub">rb=2 · H=7 · V=9 · ramp=1 · m=1..5 · 生成 {generated}</p>

<div class="card hero">
<h2>结论</h2>
<ul>
<li><b>形式化下界</b>为 m=1..5：<b>100 / 101 / 102 / 107 / 129 cy</b>。
m=4、5 的 receiver-release 下界比旧的 max(diameter,eject,cut) 更强。</li>
<li><b>短消息树 Pareto</b>：NEC-3 取得 114/174 cy（m=1/2）；NEC-2 把 mesh fanout
压到 2，但在已接受的固定面积 CalFork 模型下不节省面积，因此是时序备选而非面积 Pareto 点。</li>
<li><b>m≥3</b>：Hamilton bi-tree 的深流水 214/215/216 cy 进入并最终主导性能 Pareto；
代价是 m=1 很慢（212 cy）。</li>
<li><b>axis+CCW 被 NEC-3 严格支配</b>：两者均需 2-way calendar issue，
且固定 CalFork 面积相同；但 NEC-3 fanout 3、Pmax 3，所有 m 的严格 makespan也更低。</li>
</ul>
</div>

<div class="card">
<h2>1. 形式化问题与下界</h2>
<div class="formula">T* ≥ max(T_diameter, T_receiver-release, T_eject-duration,
T_corner-cut, T_bisection, T_injection)</div>
<ul>
<li><b>Diameter</b>：任意合法方案必须把对角源的第 m 个 flit 送过
5×7+7×9=98 cy 的最短距离，再加上下 ramp。</li>
<li><b>Receiver release</b>：对每个接收点，把每个 (source,flit) 当作 release time =
1+Manhattan(s,d)+flit_index 的单位任务；在两条 down-ramp lane 上做最早可行排程。
这是删除所有网络耦合后的松弛问题，因此其最优值仍是原问题下界。</li>
<li><b>Cut bounds</b>：任意 cut 外的每个源至少有一份 flit 穿入 cut；corner cut 容量为 2，
直线二分 cut 分别有 8 或 6 条有向链路。</li>
</ul>
{lower_bound_table(data)}
<p class="note">这些是必要条件，不宣称可同时达到。最坏 receiver 始终为四个角。</p>
</div>

<div class="card">
<h2>2. 已验证树排图的可行 Makespan 上界</h2>
{makespan_table(data)}
<p class="note">每棵树均检查 N−1 条相邻边、root indegree=0、其余节点 indegree=1、
全节点可达；排图检查每条有向链路≤1、up/down ramp≤2、每节点收到 47m flit。
Rigid pack 只搜索仓库现有 source-order 集合，因此这些是可行上界，不是全局最优值。</p>
</div>

<div class="card">
<h2>3. Router 微架构诉求与解析面积</h2>
{uarch_table(data)}
<ul>
<li>面积归一化到 IQ-XY=1.0；公共部分采用 Arch-A5：
crossbar 0.380 + BG buffer 0.139 + control 0.193。</li>
<li>Sparse direct 为支持 m≤5，calendar 需要 48m=240 actions/router，向上取 depth=256；
每周期最多 2 个输入动作，因此 calendar 宽度乘 2。</li>
<li>CalFork 严格遵循 ADR-005：凡单输入可能选择多个输出，统一计 0.025；
现有证据不支持按 fanout popcount 缩放面积。增量项按 ±30% 做敏感性，不是综合 PPA。</li>
<li><b>实现缺口：</b>当前 Arch-A5 schema 每个 (router,slot) 只允许一个事件；
除 Hamilton uni-tree 外，本次高性能 witness 的 issue width 都为 2，必须双 issue、
加宽事件格式，或重新排图成单 issue。</li>
<li>Template direct 用坐标规则计算 fork，只保留 depth=8 的注入控制，并加入 0.003
route decoder 代理面积。它是研究候选，不替代已接受的 SparseCal ADR。</li>
</ul>
</div>

<div class="card">
<h2>4. SparseCal 约束下的 Pareto front</h2>
{pareto_tables(data, "sparse_only")}
<p class="note">Sparse replay-m1 仅保存 m=1 calendar 并重复 m 次；面积较低，但长消息性能可能显著下降。</p>
</div>

<div class="card">
<h2>5. 加入坐标模板解码后的扩展 Pareto</h2>
{pareto_tables(data, "expanded_with_template")}
<p class="note">Template 面积优势依赖 route decoder 代理模型。RTL 综合前，不能把约 0.005×
的差异视为显著；NEC-3 与 Hamilton 两个性能结构拐点不依赖该小差异。</p>
</div>

<div class="card">
<h2>6. 决策建议</h2>
<ul>
<li>若工作负载以 <b>m≤2</b> 为主：选 NEC-3；若时序要求禁止 fanout 3，选 NEC-2。</li>
<li>若工作负载以 <b>m≥3</b> 为主：选 Hamilton bi-tree，或在编译器中按 m 切换
NEC-3 / Hamilton bi-tree。</li>
<li>下一阶段应综合三个 CalFork 版本（fanout 2、fanout 3、full mask）并验证双 issue
calendar 读口；这是当前 Pareto 面积排序的最大不确定性。</li>
</ul>
</div>
</body></html>"""
    HTML_PATH.write_text(body, encoding="utf-8")
    print(f"Wrote {HTML_PATH}")


if __name__ == "__main__":
    main()
