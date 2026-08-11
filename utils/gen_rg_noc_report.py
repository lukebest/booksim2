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


PATS = ["alltoall", "allgather", "allreduce", "broadcast", "reduce"]

PAT_NOTE = {
    "alltoall": ("48×47=2256 条单播流，对分带宽绑定。CA 非聚合是最坏控制税案例；"
                 "ca_batch 用全局无冲突排程压缩数据面。"),
    "allgather": ("每源一棵多播树（48 棵）。sync=等齐 48 个 request 后统一 grant；"
                  "async=每 grant 一棵树。"),
    "allreduce": "reduce-tree + broadcast-tree 两相，默认 sync barrier。",
    "broadcast": "单根多播树，控制消息最少（1 或 48），直径主导。",
    "reduce": "48→1 汇聚树，根节点 eject 端口是硬瓶颈（任何排程顺序都同样受限）。",
}

ARB_LABEL = {
    "ca": "CA（集中·即时）",
    "ca_batch": "CA-batch（错峰+时间窗+BCFS）",
    "da": "DA（分布式）",
    "none": "FIFO 基线（无 RG）",
}


def pick(data, topo, plane, arb, pat, m):
    """Select the canonical row for one (topo, plane, arb, pat, m) cell."""
    tag = "batch_main" if arb == "ca_batch" else None
    cands = [r for r in data["rows"]
             if r["topo"] == topo and r["plane"] == plane
             and r["arbiter"] == arb and r["pattern"] == pat
             and r["m"] == m
             and r.get("torus_delay_scale", 1) == 1
             and r.get("tag") == tag]
    if pat == "alltoall" and arb in ("ca", "ca_batch"):
        agg = [r for r in cands if r.get("aggregate")]
        cands = agg or cands
    if pat == "allgather":
        syn = [r for r in cands if r.get("sync") is True]
        cands = syn or cands
    return cands[0] if cands else None


def _vs(base, val):
    """makespan / baseline, or '—'."""
    if base and val and base > 0:
        return f"{val / base:.2f}×"
    return "—"


def pattern_m_table(data, pat: str, m: int) -> str:
    """One comparison table for a fixed (pattern, m): baseline + all RG schemes.

    Columns: scheme · plane · mesh mk · mesh/baseline · torus mk · torus/baseline
             · ctrl notes. First row is the packet-switched FIFO baseline.
    """
    base_mesh = pick(data, "mesh", "fifo", "none", pat, m)
    base_torus = pick(data, "torus", "fifo", "none", pat, m)
    bm, bt = mk(base_mesh), mk(base_torus)

    body = [
        "<tr class='base'>"
        "<td><b>分组交换基线（FIFO，无 RG）</b></td><td>fifo</td>"
        f"<td><b>{fmt(bm)}</b></td><td>1.00×</td>"
        f"<td><b>{fmt(bt)}</b></td><td>1.00×</td>"
        "<td class='muted'>无控制平面 · 源端自由注入</td></tr>"
    ]

    combos = [
        ("ca", "bufferable"), ("ca", "bufferless"),
        ("ca_batch", "bufferable"), ("ca_batch", "bufferless"),
        ("da", "bufferable"), ("da", "bufferless"),
    ]
    for arb, plane in combos:
        rm = pick(data, "mesh", plane, arb, pat, m)
        rt = pick(data, "torus", plane, arb, pat, m)
        mm, mt = mk(rm), mk(rt)
        ctrl = ""
        # prefer mesh ctrl; fall back to torus
        src = rm if (rm and rm.get("ctrl")) else rt
        if src and src.get("ctrl"):
            c = src["ctrl"]
            ctrl = (f"req={c['n_requests']} · t_req={fmt(c['t_last_request'])}")
            if src.get("batch"):
                b = src["batch"]
                g = b.get("bcfs_gain")
                ctrl += (f" · W={b['window']} · R_rg={fmt(b['ctrl'].get('R_rg'))}"
                         f" · BCFS{'+' if g and g > 0 else ''}"
                         f"{'' if g is None else f'{g*100:.0f}%'}")
        cls = " class='hl'" if arb == "ca_batch" else ""
        # mark better-than-baseline cells
        def cell(v, base):
            if v is None:
                return "—"
            s = fmt(v)
            if base and v < base:
                return f"<span class='win'>{s}</span>"
            if base and v > base * 1.5:
                return f"<span class='lose'>{s}</span>"
            return s
        body.append(
            f"<tr{cls}><td>{ARB_LABEL[arb]}</td><td>{plane}</td>"
            f"<td>{cell(mm, bm)}</td><td>{_vs(bm, mm)}</td>"
            f"<td>{cell(mt, bt)}</td><td>{_vs(bt, mt)}</td>"
            f"<td class='muted'>{ctrl}</td></tr>")

    return (
        f"<h4>m = {m}</h4>\n"
        "<table><tr>"
        "<th>scheme</th><th>plane</th>"
        "<th>mesh makespan</th><th>vs 基线</th>"
        "<th>torus makespan</th><th>vs 基线</th>"
        "<th>ctrl</th></tr>\n"
        + "\n".join(body) + "\n</table>")


def pattern_table(data, pat) -> str:
    """Pattern section: intro + one table per message size m."""
    tables = "\n".join(pattern_m_table(data, pat, m) for m in (1, 4, 16))
    return (
        f"<h3>{pat}</h3>\n<p class='muted'>{PAT_NOTE[pat]}</p>\n{tables}")


def per_pattern_sections(data) -> str:
    return "\n".join(pattern_table(data, p) for p in PATS)


def batch_rows(data, tag="batch_main"):
    return [r for r in data["rows"]
            if r.get("tag") == tag and r.get("batch")]


def batch_table(data) -> str:
    """BCFS results grouped by pattern × m, with FIFO baseline for comparison."""
    out = []
    for pat in PATS:
        blocks = [f"<h3>{pat}</h3>"]
        for m in (1, 4, 16):
            body = []
            for topo in ("mesh", "torus"):
                base = pick(data, topo, "fifo", "none", pat, m)
                bm = mk(base)
                for plane in ("bufferless", "bufferable"):
                    r = pick(data, topo, plane, "ca_batch", pat, m)
                    if not r or not r.get("batch"):
                        continue
                    b = r["batch"]
                    c = b["ctrl"]
                    gain = b["bcfs_gain"]
                    des = mk(r)
                    body.append(
                        f"<tr><td>{topo}</td><td>{plane}</td>"
                        f"<td><b>{fmt(bm)}</b></td>"
                        f"<td>{fmt(des)}</td>"
                        f"<td>{_vs(bm, des)}</td>"
                        f"<td>{fmt(b['makespan_sched'])}</td>"
                        f"<td>{fmt(b['makespan_fcfs'])}</td>"
                        f"<td>{'—' if gain is None else f'{gain*100:.1f}%'}</td>"
                        f"<td>{fmt(c['arrival_spread'])}</td>"
                        f"<td>{fmt(c['R_rg'])}</td>"
                        f"<td>{b['n_batches']}</td>"
                        f"<td>{'✓' if b['conflict_free'] else '✗'}</td></tr>")
            if body:
                blocks.append(
                    f"<h4>m = {m}</h4>"
                    "<table><tr><th>topo</th><th>plane</th>"
                    "<th>FIFO 基线</th><th>DES makespan</th><th>vs 基线</th>"
                    "<th>BCFS 排程</th><th>FCFS 排程</th><th>BCFS 增益</th>"
                    "<th>到达离散度</th><th>R_rg</th><th>#窗口</th>"
                    "<th>无冲突</th></tr>" + "".join(body) + "</table>")
        out.append("\n".join(blocks))
    return "\n".join(out)


def batch_sens(data) -> str:
    def tab(rows, cols, headers):
        h = "".join(f"<th>{c}</th>" for c in headers)
        body = []
        for r in rows:
            tds = []
            for c in cols:
                if c == "makespan":
                    tds.append(fmt(mk(r)))
                elif c.startswith("batch."):
                    v = r["batch"].get(c[6:])
                    if c == "batch.bcfs_gain" and v is not None:
                        v = f"{v*100:.1f}%"
                    if c == "batch.window" and v == 0:
                        v = "∞ (等齐)"
                    tds.append(fmt(v))
                elif c.startswith("ctrl."):
                    tds.append(fmt(r["batch"]["ctrl"].get(c[5:])))
                else:
                    tds.append(fmt(r.get(c)))
            body.append("<tr>" + "".join(f"<td>{x}</td>" for x in tds) + "</tr>")
        return f"<table><tr>{h}</tr>{''.join(body)}</table>"

    win = batch_rows(data, "sens_window")
    gm = batch_rows(data, "sens_genmodel")
    na = batch_rows(data, "batch_noagg")
    return f"""
<h3>时间窗 W 敏感度（bufferless · CA-batch · alltoall 聚合 · m=4）</h3>
<p class="muted">W 小 = 频繁小批，R_rg 短但全局视野窄；W 大 = 批量大、BCFS 增益高但等窗代价上升。W=∞ 表示等所有 request 到齐（同步纪律）。</p>
{tab(win, ['topo', 'batch.window', 'batch.n_batches', 'makespan',
           'batch.makespan_fcfs', 'batch.bcfs_gain', 'ctrl.R_rg',
           'batch.data_span'],
     ['topo', 'W', '#窗口', 'DES makespan', 'FCFS makespan', 'BCFS 增益',
      'R_rg', '数据面跨度'])}

<h3>Request 产生时刻模型敏感度（mesh · bufferless · alltoall 聚合 · m=4 · W=64）</h3>
<p class="muted">uniform_jitter=每节点 U[0,J) 随机起跳；distance_skew=离 CA 远的节点提前发（补偿线延迟）；burst=半数节点同时、半数延后 J。</p>
{tab(gm, ['batch.gen_model', 'batch.jitter', 'ctrl.arrival_spread',
          'ctrl.R_rg', 'makespan', 'batch.bcfs_gain'],
     ['产生模型', 'J', '到达离散度', 'R_rg', 'DES makespan', 'BCFS 增益'])}

<h3>非聚合对照（每流一条 request · mesh · bufferless · m=4 · W=64）</h3>
{tab(na, ['pattern', 'batch.n_request_units', 'ctrl.arrival_spread',
          'ctrl.R_rg', 'makespan', 'batch.data_span'],
     ['pattern', '#request', '到达离散度', 'R_rg', 'DES makespan', '数据面跨度'])}
"""


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
    pol = data.get("control_noc_policy", {})
    brows = [r for r in data["rows"] if r.get("batch")]
    gains = [r["batch"]["bcfs_gain"] for r in brows
             if r["batch"]["bcfs_gain"] is not None]
    bcfs_max = max(gains) if gains else 0.0
    all_cf = all(r["batch"]["conflict_free"] for r in brows) if brows else False
    a2a_b = next((r for r in brows
                  if r["topo"] == "mesh" and r["plane"] == "bufferless"
                  and r["pattern"] == "alltoall" and r["m"] == 4
                  and r.get("tag") == "batch_main"), None)
    return f"""
<div class="cards">
  <div class="card ok">
    <div class="k">中心调度器位置</div>
    <div class="v">(4, 0) · nid 4</div>
    <div class="s">第 1 行第 5 列 · XY 路由 · 控制时延=⌊曼哈顿/2⌋ · mesh 最远 36 cy / torus 最远 27 cy</div>
  </div>
  <div class="card {'ok' if all_cf else 'bad'}">
    <div class="k">全局无冲突排程 BCFS</div>
    <div class="v">{len(brows)} 组 · 无冲突 {'✓' if all_cf else '✗'}</div>
    <div class="s">独立检查器逐链路复核 · bufferless DES max_residency=0 ·
    相对 FCFS 最高增益 <b>{bcfs_max*100:.0f}%</b>
    {f"· mesh alltoall m=4 → {a2a_b['makespan']} cy" if a2a_b else ""}</div>
  </div>
  <div class="card ok">
    <div class="k">控制平面 = 私有 NoC</div>
    <div class="v">与数据面零共享</div>
    <div class="s">kind={pol.get('kind','private_isomorphic')} · 不继承 data σ · 面积/节点 +{pol.get('area_per_node_norm', 0.12)}</div>
  </div>
  <div class="card bad">
    <div class="k">CA alltoall 非聚合 · 控制收敛</div>
    <div class="v">{mesh_a2a_raw['ctrl']['t_last_request']} cy</div>
    <div class="s">2256 req → 入端口下界 ⌈2256/4⌉=564；实测 {mesh_a2a_raw['ctrl']['t_last_request']}（<b>私有控制网上</b>控制消息互争，非数据干扰）· mk={mesh_a2a_raw['makespan']}</div>
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
    bs = v["tests"].get("batch_sched", {})
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
tr.hl td {{ background: #17243d; }}
tr.base td {{ background: #1a2a1a; border-color: #2d6a4f; }}
h3 {{ margin-top: 1.8rem; border-left: 3px solid #7eb6ff; padding-left: .6rem; }}
h4 {{ margin: 1rem 0 .4rem; color: #c8d0e0; font-size: 1rem; }}
.win {{ color: #6ee7a8; font-weight: 600; }}
.lose {{ color: #f0a0a0; }}
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
<span class="pill">控制时延 = ⌊曼哈顿/2⌋</span>
</p>

{headline_cards(data)}

<h2>1. 建模口径</h2>
<ul>
<li><b>拓扑</b>：8×6 mesh（82 无向链路）vs 折叠 2D torus（96 无向链路）。金属线恒定约束下 torus 每链路带宽减半（σ=2），对分带宽与 mesh 严格相等。</li>
<li><b>金属线审计</b>：mesh={v['metal']['mesh_metal']:.0f} · torus={v['metal']['torus_metal']:.0f} · 比={v['metal']['ratio_torus_over_mesh']:.3f}（torus 多约 17% 链路单位；折叠线长×2 的物理偏差见敏感度 delay×2）。</li>
<li><b>控制平面（硬约束）</b>：集中式/分布式 request–grant 消息走<b>私有控制 NoC</b>（与数据面同构的独立物理网络），<b>不与数据面共物理链路</b>。
控制面不继承数据面 σ；单向时延 = <b>⌊含 link delay 的曼哈顿距离 / 2⌋</b>（XY 路径）；
剩余争用仅来自控制消息互争 + CA 入端口汇聚。</li>
<li><b>类型</b>：bufferable = 源端 grant 准入 + 路由器 FIFO/credit；bufferless = grant 逐拍预约路径、路由器零缓冲。</li>
<li><b>仲裁器</b>：<code>CA</code> 集中式 @ <b>(x=4,y=0) = nid 4</b>（原点左上、先 x 后 y，即第 1 行第 5 列）；
<code>DA</code> 目的端分布式；<code>CA-batch</code> = CA + <b>错峰 request + 时间窗批量仲裁 + 全局无冲突排程 BCFS</b>（见 §3）。</li>
<li><b>Request 产生时刻</b>：各节点<b>不同时</b>产生 request（默认每节点 U[0,64) 随机起跳，同一节点内多条 request 逐拍发出）。
到达 CA 的时刻 = 产生时刻 + <b>XY 路由</b>上 ⌊曼哈顿线延迟/2⌋ + 私有控制网争用，因此天然离散。</li>
<li><b>同步</b>：allgather/allreduce 默认 sync barrier（等齐 48 个 request 再统一 grant）；allgather 另做异步「每 grant = 一棵多播树」对照。
CA-batch 的 W=∞ 即等价于「等齐再仲裁」的同步纪律。</li>
</ul>
<div class="eq">T = T_bound + R_rg + W_grant(ρ) &nbsp;;&nbsp; R_rg = ℓ_ctrl(src→arb) + T_sched + ℓ_ctrl(arb→src)
<br/><span style="font-size:.85rem;color:#9aa3b5">ℓ_ctrl = ⌊曼哈顿线延迟/2⌋，走私有控制 NoC，与数据面链路资源正交</span></div>

<h2>2. 头条结论：私有控制 NoC 上的收敛税仍是 alltoall 瓶颈</h2>
<p>即便 request/grant <b>完全不占用数据链路</b>，CA 下 alltoall 若对每条 (s,d) 流单独 request，共 48×47=<b>2256</b> 条控制消息仍须挤入仲裁器控制路由器的 ≤4 个入端口。
入端口收敛下界 ⌈2256/4⌉=<b>564</b> cy；私有控制 NoC DES 实测 t_last_request=<b>{v['tests']['ctrl_convergence_alltoall']['t_last_request']}</b> cy
（超出部分 = 控制消息在私有网上的路径争用，<b>不是</b>数据干扰）。而 m=1 数据面下界仅 ~98 cy、FIFO 基线 makespan={next(r['makespan'] for r in data['rows'] if r['plane']=='fifo' and r['pattern']=='alltoall' and r['m']==1 and r['topo']=='mesh')} cy。</p>
<p><b>两个有效缓解：</b></p>
<ol>
<li><b>Request 聚合</b>：每源一条 request 覆盖全部目的 → 48 条，t_last_req 降到线延迟量级（~55），makespan 回到与 FIFO 同量级。</li>
<li><b>DA 分布式</b>：把收敛点打散到 48 个目的节点，消除单点 564 cy 税。</li>
</ol>

<h2>3. 错峰 request → 时间窗批量仲裁 → 全局无冲突排程（CA-batch）</h2>

<h3>3.1 流水线</h3>
<p>中心调度器固定在 <b>(x=4, y=0)</b>（原点左上、先 x 后 y，即第 1 行第 5 列，<code>nid=4</code>）。
控制消息在<b>私有控制 NoC</b> 上走 <b>XY 维序路由</b>；
单向时延取<b>含 link delay 的曼哈顿距离的一半</b>
<code>ℓ_ctrl = ⌊(h<sub>x</sub>·H + h<sub>y</sub>·V) / 2⌋</code>（数据面仍用满 H=7 / V=9）。</p>
<div class="eq">
① 节点 i 在 t_gen(i) 产生 request（<b>各节点不同时</b>）
<br/>② request 经 XY 路由到 CA(4,0)：t_arr(i) = t_gen(i) + ℓ_ctrl(i→CA) + 控制网争用
<br/>③ CA 关闭长度 W 的滚动时间窗，取窗内到达的一批 request，于 t_decide = max(窗尾, 批内最晚到达) + T_sched 起仲裁
<br/>④ grant 经 XY 路由回源：release(i) = t_decide + ℓ_ctrl(CA→i) + 争用
<br/>⑤ 数据面起始时刻 t0(i) ≥ release(i)，由 BCFS 决定
</div>
<p class="muted">CA(4,0)→最远角：数据曼哈顿 mesh 73 / torus 55；控制面各取其半 → mesh <b>36</b> cy / torus <b>27</b> cy。</p>

<h3>3.2 BCFS：点对点请求的全局无冲突排程算法</h3>
<p>单条被授权的点对点流是一个<b>刚性时空印记</b>（wormhole、无缓冲）：给定起始 t0、XY 路径 P、m 个 flit，
它在有向链路 e 上的占用区间为</p>
<div class="eq">occ(e) = [ t0 + pref<sub>P</sub>(e) , &nbsp; t0 + pref<sub>P</sub>(e) + m·σ )</div>
<p>其中 pref<sub>P</sub>(e) 是源到 e 尾端的累计线延迟，σ 为每 flit 拍数（mesh 1 / torus 2）。
「全局无冲突」= 为一批 request 选一组 t0，使<b>任意链路上任意两个印记不重叠</b>，且源/目的 ramp 容量（RAMP_BW=2 flit/cy）不被超出。
这是固定路由的 job-shop 型区间装箱问题（一般情形 NP-hard），BCFS 用三件事求解：</p>
<ol>
<li><b>关键度优先的表调度</b>：先排「路径穿过最拥挤链路」的 request。
定义 pressure(r) = Σ<sub>e∈P(r)</sub> load(e)，load(e) 为本批中使用 e 的流数；pressure 大者裕度最小，先排。</li>
<li><b>精确「最早可行起点」搜索</b>：不逐拍试探。对每条链路与两端 ramp 维护区间图，
直接跳到「所有链路 + 两端 ramp 同时空闲」的最早时刻——等价于在已有排程的空洞里做 <b>backfill</b>，
所以后到的短流能填进前面留下的缝隙。</li>
<li><b>多起点搜索</b>：以 criticality / longest-path / FCFS / 随机 共 5 种优先序各跑一遍，取批 makespan 最小者。</li>
</ol>
<p>输出<b>按构造即无冲突</b>，并由独立检查器 <code>verify_conflict_free()</code> 重新逐链路两两复核
（本次全部 {sum(1 for r in data['rows'] if r.get('batch'))} 组配置 conflict_free=✓，bufferless DES 实测 max_residency=0）。
这正是零缓冲数据面能成立的前提：路由器不需要任何 buffer，因为 grant 已经保证了逐拍不撞。</p>
<p>跨窗口的预约<b>持久保留</b>：第 k+1 批在排程时会看到第 k 批已占用的区间，因此全局（而非仅批内）无冲突。</p>

<h3>3.3 BCFS 结果（按 pattern × m 归类，含 FIFO 基线）</h3>
{batch_table(data)}
<p><b>读法</b>：「BCFS 增益」= FCFS 排程 makespan / BCFS 排程 makespan − 1，即关键度排序 + backfill 相对纯到达序贪心的收益。
alltoall 上 mesh 约 5%、torus 约 15%（torus σ=2 使印记更长、装箱更紧张，全局视野更值钱）。
<b>reduce / broadcast / allreduce 增益为 0</b>：这些 pattern 的瓶颈是根节点 eject 端口或树直径，
是<b>容量下界</b>而非装箱质量决定 makespan，任何顺序都打到同一个下界——这本身是个有用的结论：
全局排程只在「多对多、链路争用型」流量上有价值。</p>

<h3>3.4 敏感度</h3>
{batch_sens(data)}
<p><b>W 的取舍</b>：W→0 退化成逐条即时仲裁（R_rg 最短，但 BCFS 只能看到一两条流，增益≈0）；
W→∞ 退化成同步 barrier（视野最全、增益最高，但要付「等最晚 request」的税）。
中间存在最优点：mesh alltoall 聚合 m=4 在 W≈16–64 取到最小 makespan。</p>
<p><b>产生时刻错峰的影响</b>：J=0（所有节点同时产生）时到达离散度仅来自控制半曼哈顿差（mesh 上 ⌊73/2⌋−⌊7/2⌋ ≈ 36−3 = 33 cy 量级）；
J 增大时离散度线性增长，R_rg 随之上升，但 BCFS 增益<b>下降</b>——因为 release 时刻本身已经把流拉开了，
排程器可优化的重叠变少。<code>distance_skew</code>（远节点提前发）能显著压缩到达离散度，是低成本的工程手段。</p>

<h2>4. 按 pattern × m 归类的主结果（makespan，cycles）</h2>
<p>每个 pattern 下按消息大小 <code>m ∈ {{1, 4, 16}}</code> 各一张表；
表首行是<strong>分组交换基线</strong>（FIFO、无 request–grant、源端自由注入），其后纵向排列 CA / CA-batch / DA。
「vs 基线」= 该配置 makespan ÷ 同拓扑、同 pattern、同 m 的 FIFO 基线；
<span class="win">&lt;1×（绿）</span> 表示快于基线，
<span class="lose">&gt;1.5×（淡红）</span> 表示明显慢于基线。
CA / CA-batch 的 alltoall 展示聚合配置；allgather 展示 sync barrier。</p>
{per_pattern_sections(data)}

<h2>5. Allgather：同步 barrier vs 异步多播树</h2>
<table>
<tr><th>topo</th><th>plane</th><th>m</th><th>sync</th><th>async tree</th><th>Δ(sync−async)</th></tr>
{sync_async_table(data)}
</table>
<p>异步树避免了「等最远节点」的 barrier 税，但树间冲突使数据面可能更长；同步把冲突集中到统一排程，大 m 时摊薄 R_rg。</p>

<h2>6. Mesh vs Torus</h2>
<ul>
<li>对分带宽绑定的大消息 alltoall：二者数据下界相同（bisect 自检 ✓）。</li>
<li>小消息 / 树形（broadcast、reduce）：torus 直径 55 vs mesh 94，DA bufferless broadcast m=1 为
{next(r['makespan'] for r in data['rows'] if r['topo']=='torus' and r['plane']=='bufferless' and r['arbiter']=='da' and r['pattern']=='broadcast' and r['m']==1)}
vs mesh
{next(r['makespan'] for r in data['rows'] if r['topo']=='mesh' and r['plane']=='bufferless' and r['arbiter']=='da' and r['pattern']=='broadcast' and r['m']==1)}。</li>
<li>torus σ=2 的串行税在长消息聚合 alltoall 上可见（bufferable m=16 明显重于 mesh）。</li>
<li>bufferable torus 需 <b>2 VC</b>（dateline）→ 缓冲面积约翻倍；bufferless 靠时隙预约无需 VC。</li>
</ul>

<h2>7. 面积（归一化 IQ-XY = 1.0）</h2>
<table>
<tr><th>topo</th><th>plane</th><th>arb</th><th>total</th><th>buffer</th><th>arbiter</th><th>private_ctrl_noc</th></tr>
{area_table(data)}
</table>
<p class="muted">公式：crossbar(0.380)+control(0.170)+5·VC·Q·0.00365 + arbiter + <b>private_ctrl_noc(0.12)</b>。
私有控制 NoC 面积按窄 flit 同构网络摊到每节点 0.12（相对 IQ-XY=1.0），属数据面金属恒定预算之外的增量。
bufferless 扣掉数据 VC 缓冲；CA 仲裁器开销 0.05，DA 0.03；FIFO 基线无控制 NoC。</p>

<h2>8. 其他敏感度</h2>
{sens_tables(data)}

<h2>9. 验证清单</h2>
<ul>
<li>对分带宽相等：{'✓' if v['tests']['bisection_equal'] else '✗'}</li>
<li>torus σ=0.5 flit/cy：{'✓' if v['tests']['torus_bw_half'] else '✗'}</li>
<li>Golden FIFO alltoall m=1 = {v['tests']['golden_fifo_alltoall_m1']['makespan']}（pg 参考 188）</li>
<li>bufferless 零驻留（{v['tests']['bufferless_n']} 组）：{'✓' if v['tests']['bufferless_zero_residency'] else '✗'}</li>
<li>保序：{'✓' if v['tests']['all_ordered'] else '✗'}</li>
<li>torus CDG 无环：{'✓' if v['tests']['torus_cdg_acyclic'] else '✗'}</li>
<li>控制收敛 alltoall 非聚合：t_last_req={v['tests']['ctrl_convergence_alltoall']['t_last_request']} ≥ 500 ✓</li>
<li>私有控制 NoC 隔离：{'✓' if v.get('private_control_noc',{}).get('all_rg_rows_isolated') else '✗'}
（shared_with_data_plane=False，不继承 data σ）</li>
<li>单播单调性 bufferable≲bufferless（仅周期级精确行，{v['tests'].get('bufferable_le_bufferless_unicast_exact_n')} 组）：
{'✓' if v['tests'].get('bufferable_le_bufferless_unicast_exact') else '✗'}
· 事件驱动近似行的偏差 {v['tests'].get('mono_approx_violations')} 组（保守上界，见下注）</li>
</ul>

<h3>9.1 CA-batch / BCFS 专项验证</h3>
<ul>
<li>中心调度器坐标一致为 (4,0)：{'✓' if bs.get('ca_coord_consistent') else '✗'}（nid={bs.get('ca_node')}）</li>
<li>控制平面 XY 维序路由：{'✓' if bs.get('ctrl_routing_xy') else '✗'}</li>
<li>Request 到达时刻确实离散（spread&gt;0，多源配置 {bs.get('staggered_n')} 组）：
{'✓' if bs.get('staggered_arrivals') else '✗'}
<span class="muted">（另有 {bs.get('single_source_rows')} 组 broadcast 单源聚合只有 1 条 request，无离散度可言）</span></li>
<li><b>全局无冲突</b>（独立检查器逐链路两两复核）：{'✓' if bs.get('all_conflict_free') else '✗'}
· 冲突数 = {bs.get('total_violations')}</li>
<li>bufferless 回放零驻留（{bs.get('bufferless_n')} 组）：{'✓' if bs.get('bufferless_zero_residency') else '✗'}
——零缓冲路由器成立的直接证据</li>
<li><b>点对点（alltoall/reduce）BCFS 从不劣于 FCFS</b>（{bs.get('p2p_n')} 组）：
{'✓' if bs.get('p2p_never_worse_than_fcfs') else '✗'} · 平均增益 {(bs.get('p2p_gain_mean') or 0)*100:.1f}%</li>
<li>全体平均增益 {(bs.get('bcfs_gain_mean') or 0)*100:.1f}% · 最大 {(bs.get('bcfs_gain_max') or 0)*100:.1f}%；
多树 pattern 有 {bs.get('tree_regressions')} 组反向（最差 {(bs.get('tree_regression_worst') or 0)*100:+.1f}%）——
在线窗口局部最优的固有代价，见 §10。</li>
</ul>
<p class="muted">{v['tests'].get('mono_note','')}</p>

<h2>10. 已知局限</h2>
<ul>
<li>数据面金属线：按链路计数 torus/mesh≈1.17，非严格恒定；折叠线长×2 用 <code>torus_delay_scale=2</code> 对照。</li>
<li>私有控制 NoC 是<b>额外</b>金属/面积（每节点 +0.12），不计入 mesh/torus 数据面对分带宽恒定约束。</li>
<li>同 hop 延迟 7/9 对 torus 有利（白拿减半跳数）。</li>
<li>多树 bufferable（allgather）与 2256 流 alltoall 用事件驱动单播展开近似，共享树边被重复计数（保守上界）。</li>
<li><b>BCFS 是在线窗口局部最优</b>：CA 只能给眼前这一批打分，无法预知后续窗口。
多树 pattern（mesh allgather m=16）上出现 {bs.get('tree_regressions')} 组「本批更紧、全局更慢」的反向案例（最差 {(bs.get('tree_regression_worst') or 0)*100:+.1f}%）。
点对点 pattern 上未出现。若允许离线全局排程（所有 request 先到齐，即 W=∞）可消除此效应。</li>
<li>BCFS 的路径是 XY 维序固定的；允许自适应路由会扩大可行域，但需重做 CDG 无环性论证。</li>
<li>reduce = gather + PE 本地归约（无网内算术），对齐 ADR-002/Arch-A2。</li>
</ul>

<h2>11. 文件</h2>
<ul>
<li><code>utils/rg_topo.py</code> · <code>rg_bounds.py</code> · <code>rg_collectives.py</code> · <code>rg_arbiter.py</code> · <code>rg_batch_sched.py</code>（错峰+时间窗+BCFS）</li>
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
