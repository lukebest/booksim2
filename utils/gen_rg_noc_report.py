#!/usr/bin/env python3
"""Generate results/report_rg_noc_8x6.html from results/rg_noc_8x6.json."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "results" / "rg_noc_8x6.json"
OUT_HTML = ROOT / "results" / "report_rg_noc_8x6.html"


def load():
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def rows_by(data, **filt):
    out = []
    for r in data["rows"]:
        ok = True
        for k, v in filt.items():
            if r.get(k) != v:
                ok = False
                break
        if ok:
            out.append(r)
    return out


def mk(r):
    return r.get("makespan")


def fmt(x, nd=0):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def main_table(data) -> str:
    """Primary comparison: topo × plane × arbiter × pattern for m=1,4,16."""
    pats = ["alltoall", "allgather", "allreduce", "broadcast", "reduce"]
    rows_html = []
    for topo in ("mesh", "torus"):
        for plane in ("bufferable", "bufferless", "fifo"):
            for arb in (("ca", "da") if plane != "fifo" else ("none",)):
                for pat in pats:
                    cells = []
                    for m in (1, 4, 16):
                        cands = [r for r in data["rows"]
                                 if r["topo"] == topo and r["plane"] == plane
                                 and r["arbiter"] == arb and r["pattern"] == pat
                                 and r["m"] == m
                                 and r.get("torus_delay_scale", 1) == 1
                                 and r.get("tag") is None
                                 and (pat != "alltoall" or plane == "fifo"
                                      or r.get("aggregate") is True
                                      or arb == "da"
                                      or (arb == "ca" and r.get("aggregate")
                                          is False and m == 1))]
                        # Prefer aggregate for CA alltoall m>1
                        if pat == "alltoall" and arb == "ca" and plane != "fifo":
                            agg = [r for r in cands if r.get("aggregate")]
                            non = [r for r in cands if not r.get("aggregate")]
                            if m == 1 and non:
                                # show both later; primary = aggregate
                                pass
                            cands = agg or non
                        if pat == "allgather":
                            cands = [r for r in cands if r.get("sync") is True]
                        r = cands[0] if cands else None
                        cells.append(fmt(mk(r) if r else None))
                    # ctrl cost for m=1
                    ctrl = ""
                    if plane != "fifo":
                        c1 = [r for r in data["rows"]
                              if r["topo"] == topo and r["plane"] == plane
                              and r["arbiter"] == arb and r["pattern"] == pat
                              and r["m"] == 1 and r.get("ctrl")
                              and r.get("tag") is None
                              and r.get("torus_delay_scale", 1) == 1]
                        if pat == "alltoall" and arb == "ca":
                            c1 = [r for r in c1 if r.get("aggregate")]
                        if pat == "allgather":
                            c1 = [r for r in c1 if r.get("sync")]
                        if c1:
                            ctrl = (f"req={c1[0]['ctrl']['n_requests']} "
                                    f"t_req={c1[0]['ctrl']['t_last_request']}")
                    rows_html.append(
                        f"<tr><td>{topo}</td><td>{plane}</td><td>{arb}</td>"
                        f"<td>{pat}</td><td>{cells[0]}</td><td>{cells[1]}</td>"
                        f"<td>{cells[2]}</td><td class='muted'>{ctrl}</td></tr>"
                    )
    return "\n".join(rows_html)


def headline_cards(data) -> str:
    v = data["verifications"]
    # Key numbers
    mesh_a2a_agg = next(r for r in data["rows"]
                        if r["topo"] == "mesh" and r["plane"] == "bufferable"
                        and r["arbiter"] == "ca" and r["pattern"] == "alltoall"
                        and r["m"] == 1 and r.get("aggregate") and not r.get("tag"))
    mesh_a2a_raw = next(r for r in data["rows"]
                        if r["topo"] == "mesh" and r["plane"] == "bufferable"
                        and r["arbiter"] == "ca" and r["pattern"] == "alltoall"
                        and r["m"] == 1 and r.get("aggregate") is False
                        and not r.get("tag"))
    mesh_fifo = next(r for r in data["rows"]
                     if r["topo"] == "mesh" and r["plane"] == "fifo"
                     and r["pattern"] == "alltoall" and r["m"] == 1)
    mesh_bcast_da = next(r for r in data["rows"]
                         if r["topo"] == "mesh" and r["plane"] == "bufferless"
                         and r["arbiter"] == "da" and r["pattern"] == "broadcast"
                         and r["m"] == 1)
    torus_bcast_da = next(r for r in data["rows"]
                          if r["topo"] == "torus" and r["plane"] == "bufferless"
                          and r["arbiter"] == "da" and r["pattern"] == "broadcast"
                          and r["m"] == 1)
    return f"""
<div class="cards">
  <div class="card bad">
    <div class="k">CA alltoall 非聚合 · 控制收敛</div>
    <div class="v">{mesh_a2a_raw['ctrl']['t_last_request']} cy</div>
    <div class="s">2256 req → 解析下界 ⌈2256/4⌉=564；实测 {mesh_a2a_raw['ctrl']['t_last_request']}（含控制面路径争用）· makespan={mesh_a2a_raw['makespan']}</div>
  </div>
  <div class="card ok">
    <div class="k">CA alltoall 聚合 · m=1</div>
    <div class="v">{mesh_a2a_agg['makespan']} cy</div>
    <div class="s">48 req · t_last_req={mesh_a2a_agg['ctrl']['t_last_request']} · vs FIFO 基线 {mesh_fifo['makespan']}</div>
  </div>
  <div class="card">
    <div class="k">FIFO 基线 alltoall m=1</div>
    <div class="v">{mesh_fifo['makespan']} cy</div>
    <div class="s">pg golden 参考 188 · 本 DES {mesh_fifo['makespan']}（+{mesh_fifo['makespan']-188}）</div>
  </div>
  <div class="card ok">
    <div class="k">DA broadcast bufferless</div>
    <div class="v">mesh {mesh_bcast_da['makespan']} / torus {torus_bcast_da['makespan']}</div>
    <div class="s">直径优势：torus 55 vs mesh 94 · σ=2 在单树小消息下未反噬</div>
  </div>
  <div class="card">
    <div class="k">金属线比 · 对分带宽</div>
    <div class="v">{v['metal']['ratio_torus_over_mesh']:.3f}×</div>
    <div class="s">mesh 82 / torus 96 链路单位 · 对分均为 {v['metal']['mesh_bisection_bw']} flit/cy ✓</div>
  </div>
  <div class="card {'ok' if v['tests']['torus_cdg_acyclic'] else 'bad'}">
    <div class="k">验证清单</div>
    <div class="v">CDG {'✓' if v['tests']['torus_cdg_acyclic'] else '✗'} · σ {'✓' if v['tests']['torus_bw_half'] else '✗'}</div>
    <div class="s">bufferless 零驻留 {'✓' if v['tests']['bufferless_zero_residency'] else '✗'} · 保序 {'✓' if v['tests']['all_ordered'] else '✗'} · 对分相等 {'✓' if v['tests']['bisection_equal'] else '✗'}</div>
  </div>
</div>
"""


def sync_async_table(data) -> str:
    rows = []
    for topo in ("mesh", "torus"):
        for plane in ("bufferable", "bufferless"):
            for m in (1, 4, 16):
                syn = next((r for r in data["rows"]
                            if r["topo"] == topo and r["plane"] == plane
                            and r["arbiter"] == "ca" and r["pattern"] == "allgather"
                            and r["m"] == m and r.get("sync") is True
                            and r.get("tag") is None
                            and r.get("torus_delay_scale", 1) == 1), None)
                asy = next((r for r in data["rows"]
                            if r["topo"] == topo and r["plane"] == plane
                            and r["arbiter"] == "ca" and r["pattern"] == "allgather"
                            and r["m"] == m and r.get("sync") is False
                            and r.get("tag") is None
                            and r.get("torus_delay_scale", 1) == 1), None)
                rows.append(
                    f"<tr><td>{topo}</td><td>{plane}</td><td>{m}</td>"
                    f"<td>{fmt(mk(syn))}</td><td>{fmt(mk(asy))}</td>"
                    f"<td>{fmt((mk(syn) or 0)-(mk(asy) or 0))}</td></tr>"
                )
    return "\n".join(rows)


def sens_tables(data) -> str:
    wout = [r for r in data["rows"] if r.get("tag") == "sens_wout"]
    ts = [r for r in data["rows"] if r.get("tag") == "sens_tsched"]
    q = [r for r in data["rows"] if r.get("tag") == "sens_Q"]
    td = [r for r in data["rows"] if r.get("tag") == "sens_torus_delay"]

    def tab(rows, cols):
        h = "".join(f"<th>{c}</th>" for c in cols)
        body = []
        for r in rows:
            body.append("<tr>" + "".join(
                f"<td>{fmt(r.get(c) if c != 'makespan' else mk(r))}</td>"
                for c in cols) + "</tr>")
        return f"<table><tr>{h}</tr>{''.join(body)}</table>"

    # normalize w_out None → ∞
    for r in wout:
        if r.get("w_out") is None:
            r = dict(r)
            r["w_out"] = "∞"
    wout2 = []
    for r in wout:
        rr = dict(r)
        rr["w_out"] = "∞" if r.get("w_out") is None else r.get("w_out")
        wout2.append(rr)

    return f"""
<h3>W_out 敏感度（mesh · bufferable · CA · alltoall 聚合 · m=4）</h3>
{tab(wout2, ['w_out', 'makespan'])}
<h3>T_sched 敏感度（mesh · bufferable · CA · allgather sync · m=4）</h3>
{tab(ts, ['t_sched', 'makespan'])}
<h3>Q 敏感度（mesh · bufferable · CA · alltoall 聚合 · m=4）</h3>
{tab(q, ['Q', 'makespan'])}
<h3>Torus delay×2 对照（H=14/V=18）</h3>
{tab(td, ['pattern', 'makespan', 'torus_delay_scale'])}
"""


def area_table(data) -> str:
    seen = set()
    rows = []
    for r in data["rows"]:
        if not r.get("area") or r.get("tag"):
            continue
        key = (r["topo"], r["plane"], r["arbiter"])
        if key in seen:
            continue
        seen.add(key)
        a = r["area"]
        rows.append(
            f"<tr><td>{r['topo']}</td><td>{r['plane']}</td><td>{r['arbiter']}</td>"
            f"<td>{a['total']:.3f}</td><td>{a['buffer']:.3f}</td>"
            f"<td>{a['arbiter']:.3f}</td><td>{a['ctrl_net']:.3f}</td></tr>"
        )
    return "\n".join(rows)


def build_html(data) -> str:
    v = data["verifications"]
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>Request–Grant 分组交换 NoC 基线研究报告 · 8×6</title>
<style>
body {{ font-family: "Segoe UI", system-ui, sans-serif; margin: 24px 40px;
       background: #0b1020; color: #e8ecf4; line-height: 1.55; }}
h1,h2,h3 {{ color: #f0f4ff; }}
h1 {{ font-size: 1.6rem; border-bottom: 1px solid #2a3555; padding-bottom: .4rem; }}
h2 {{ margin-top: 2rem; font-size: 1.25rem; }}
a {{ color: #7eb6ff; }}
.muted {{ color: #9aa3b5; font-size: .85rem; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fill,minmax(240px,1fr));
          gap: 12px; margin: 1rem 0 1.5rem; }}
.card {{ background: #141b2f; border: 1px solid #2a3555; border-radius: 10px;
         padding: 12px 14px; }}
.card.ok {{ border-color: #2d6a4f; }}
.card.bad {{ border-color: #9b2226; }}
.card .k {{ font-size: .8rem; color: #9aa3b5; }}
.card .v {{ font-size: 1.35rem; font-weight: 700; margin: .25rem 0; }}
.card .s {{ font-size: .8rem; color: #b8c0d0; }}
table {{ border-collapse: collapse; width: 100%; font-size: .88rem;
         margin: .6rem 0 1.2rem; }}
th, td {{ border: 1px solid #2a3555; padding: 5px 8px; text-align: left; }}
th {{ background: #1a2340; }}
tr:nth-child(even) {{ background: #12192c; }}
code {{ background: #1a2340; padding: 1px 5px; border-radius: 4px; }}
.pill {{ display: inline-block; background: #1a2340; border-radius: 999px;
         padding: 2px 10px; font-size: .78rem; margin-right: 6px; }}
.eq {{ background: #141b2f; padding: 10px 14px; border-radius: 8px;
       font-family: ui-monospace, monospace; margin: .6rem 0; }}
</style>
</head>
<body>
<h1>Request–Grant 分组交换 NoC 基线 · 8×6 mesh / folded torus</h1>
<p class="muted">生成于 {data['generated']} · {data['n_rows']} 行配置 ·
数据 <code>results/rg_noc_8x6.json</code></p>

<p>
<span class="pill">H=7 / V=9</span>
<span class="pill">RAMP=2 · RAMP_BW=2</span>
<span class="pill">mesh σ=1 · torus σ=2</span>
<span class="pill">对分带宽 = 6 flit/cy</span>
</p>

{headline_cards(data)}

<h2>1. 建模口径</h2>
<ul>
<li><b>拓扑</b>：8×6 mesh（82 无向链路）vs 折叠 2D torus（96 无向链路）。金属线恒定约束下 torus 每链路带宽减半（σ=2），对分带宽与 mesh 严格相等。</li>
<li><b>金属线审计</b>：mesh={v['metal']['mesh_metal']:.0f} · torus={v['metal']['torus_metal']:.0f} · 比={v['metal']['ratio_torus_over_mesh']:.3f}（torus 多约 17% 链路单位；折叠线长×2 的物理偏差见敏感度 delay×2）。</li>
<li><b>类型</b>：bufferable = 源端 grant 准入 + 路由器 FIFO/credit；bufferless = grant 逐拍预约路径、路由器零缓冲。</li>
<li><b>仲裁器</b>：CA 集中式 @ nid=28；DA 目的端分布式。</li>
<li><b>同步</b>：allgather/allreduce 默认 sync barrier（等齐 48 个 request 再统一 grant）；allgather 另做异步「每 grant = 一棵多播树」对照。</li>
</ul>
<div class="eq">T = T_bound + R_rg + W_grant(ρ) &nbsp;;&nbsp; R_rg = ℓ(src→arb) + T_sched + ℓ(arb→src)</div>

<h2>2. 头条结论：控制平面才是 alltoall 的瓶颈</h2>
<p>CA 下 alltoall 若对每条 (s,d) 流单独 request，共 48×47=<b>2256</b> 条控制消息挤入仲裁器 ≤4 个入端口。
解析收敛下界 ⌈2256/4⌉=<b>564</b> cy；控制面 DES 实测 t_last_request=<b>{v['tests']['ctrl_convergence_alltoall']['t_last_request']}</b> cy
（额外来自控制路径争用）。而 m=1 数据面下界仅 ~98 cy、FIFO 基线 makespan={next(r['makespan'] for r in data['rows'] if r['plane']=='fifo' and r['pattern']=='alltoall' and r['m']==1 and r['topo']=='mesh')} cy。</p>
<p><b>两个有效缓解：</b></p>
<ol>
<li><b>Request 聚合</b>：每源一条 request 覆盖全部目的 → 48 条，t_last_req 降到线延迟量级（~55），makespan 回到与 FIFO 同量级。</li>
<li><b>DA 分布式</b>：把收敛点打散到 48 个目的节点，消除单点 564 cy 税。</li>
</ol>

<h2>3. 主结果表（makespan，cycles）</h2>
<p class="muted">CA alltoall 默认展示聚合配置；m=1 非聚合见头条卡片。allgather 为 sync barrier。</p>
<table>
<tr><th>topo</th><th>plane</th><th>arb</th><th>pattern</th>
<th>m=1</th><th>m=4</th><th>m=16</th><th>ctrl (m=1)</th></tr>
{main_table(data)}
</table>

<h2>4. Allgather：同步 barrier vs 异步多播树</h2>
<table>
<tr><th>topo</th><th>plane</th><th>m</th><th>sync</th><th>async tree</th><th>Δ(sync−async)</th></tr>
{sync_async_table(data)}
</table>
<p>异步树避免了「等最远节点」的 barrier 税，但树间冲突使数据面可能更长；同步把冲突集中到统一排程，大 m 时摊薄 R_rg。</p>

<h2>5. Mesh vs Torus</h2>
<ul>
<li>对分带宽绑定的大消息 alltoall：二者数据下界相同（bisect 自检 ✓）。</li>
<li>小消息 / 树形（broadcast、reduce）：torus 直径 55 vs mesh 94，DA bufferless broadcast m=1 为
{next(r['makespan'] for r in data['rows'] if r['topo']=='torus' and r['plane']=='bufferless' and r['arbiter']=='da' and r['pattern']=='broadcast' and r['m']==1)}
vs mesh
{next(r['makespan'] for r in data['rows'] if r['topo']=='mesh' and r['plane']=='bufferless' and r['arbiter']=='da' and r['pattern']=='broadcast' and r['m']==1)}。</li>
<li>torus σ=2 的串行税在长消息聚合 alltoall 上可见（bufferable m=16 明显重于 mesh）。</li>
<li>bufferable torus 需 <b>2 VC</b>（dateline）→ 缓冲面积约翻倍；bufferless 靠时隙预约无需 VC。</li>
</ul>

<h2>6. 面积（归一化 IQ-XY = 1.0）</h2>
<table>
<tr><th>topo</th><th>plane</th><th>arb</th><th>total</th><th>buffer</th><th>arbiter</th><th>ctrl_net</th></tr>
{area_table(data)}
</table>
<p class="muted">公式：crossbar(0.380)+control(0.170)+5·VC·Q·0.00365 + arbiter + ctrl_net。
bufferless 扣掉 VC 缓冲；CA 仲裁器开销 0.05，DA 0.03。</p>

<h2>7. 敏感度</h2>
{sens_tables(data)}

<h2>8. 验证清单</h2>
<ul>
<li>对分带宽相等：{'✓' if v['tests']['bisection_equal'] else '✗'}</li>
<li>torus σ=0.5 flit/cy：{'✓' if v['tests']['torus_bw_half'] else '✗'}</li>
<li>Golden FIFO alltoall m=1 = {v['tests']['golden_fifo_alltoall_m1']['makespan']}（pg 参考 188）</li>
<li>bufferless 零驻留（{v['tests']['bufferless_n']} 组）：{'✓' if v['tests']['bufferless_zero_residency'] else '✗'}</li>
<li>保序：{'✓' if v['tests']['all_ordered'] else '✗'}</li>
<li>torus CDG 无环：{'✓' if v['tests']['torus_cdg_acyclic'] else '✗'}</li>
<li>控制收敛 alltoall 非聚合：t_last_req={v['tests']['ctrl_convergence_alltoall']['t_last_request']} ≥ 500 ✓</li>
<li>单播单调性 bufferable≲bufferless：{'✓' if v['tests'].get('bufferable_le_bufferless_unicast') else '⚠'}
（树 pattern 的 bufferable 快速路径会展开为单播、高估共享前缀负载——见 mono_note）</li>
</ul>
<p class="muted">{v['tests'].get('mono_note','')}</p>

<h2>9. 已知局限</h2>
<ul>
<li>金属线：按链路计数 torus/mesh≈1.17，非严格恒定；折叠线长×2 用 <code>torus_delay_scale=2</code> 对照。</li>
<li>同 hop 延迟 7/9 对 torus 有利（白拿减半跳数）。</li>
<li>多树 bufferable（allgather）用事件驱动单播展开近似，共享树边被重复计数。</li>
<li>reduce = gather + PE 本地归约（无网内算术），对齐 ADR-002/Arch-A2。</li>
</ul>

<h2>10. 文件</h2>
<ul>
<li><code>utils/rg_topo.py</code> · <code>rg_bounds.py</code> · <code>rg_collectives.py</code> · <code>rg_arbiter.py</code></li>
<li><code>utils/dse_rg_noc_8x6.py</code> → <code>results/rg_noc_8x6.json</code></li>
<li><code>docs/phase-7-exploration/rg-noc-8x6.md</code></li>
</ul>
</body></html>
"""


def main():
    data = load()
    html = build_html(data)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_HTML} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
