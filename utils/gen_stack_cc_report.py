#!/usr/bin/env python3
"""HTML report: congestion control on the 3D-stacked top-die / bottom-die NoC.

Same hardware as `report_ring2_stack_write_fairness.html` -- six 20-node
dual-plane top-die rings over one bottom die of 96 HAs -- and the same tiled
workload, run twice: a uniform write batch and a uniform read batch, never
mixed. The question here is not whether the fabric drains but *which
congestion-control scheme drains it fastest*, so every scheme appears at the
configuration a sweep chose for it rather than at a quoted operating point.

The figure the report is built around is the same for every scheme: cumulative
retired DAT flits per top-die group against time. Six curves that finish
together mean the fabric shared itself out; six curves that fan out mean some
group waited on another. Makespan is the right-hand edge of the slowest curve.

Inputs, all produced by other scripts:
    results/stack_cc_focus.json      dse_stack_cc_sweep.py --emit
    results/stack_cc_area.json       stack_cc_area.py
    results/stack_cc_rootcause.json  stack_cc_rootcause.py

Usage:
    python3 gen_stack_cc_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from gen_stack_write_report import (_cjk, _f, _t, bottom_die_link_table,
                                    bottom_die_setup_table, plot_binding,
                                    plot_top_die, plot_topology,
                                    top_die_hop_table, top_die_setup_table)

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "results"
FOCUS = ROOT / "results" / "stack_cc_focus.json"
AREA = ROOT / "results" / "stack_cc_area.json"
RCAUSE = ROOT / "results" / "stack_cc_rootcause.json"
OUT = ROOT / "results" / "report_stack_cc_schemes.html"

ORDER = ("s0", "s1", "s22", "s16g", "s16")
COLOR = {"s0": "#dc2626", "s1": "#f59e0b", "s22": "#16a34a",
         "s16": "#7c3aed", "s16g": "#2563eb"}
OPS = ("write", "read")
OP_CN = {"write": "均匀写", "read": "均匀读"}
DAT_CN = {"write": "WriteData", "read": "CompData"}
DIE_COLOR = ["#1d4ed8", "#dc2626", "#0891b2", "#ea580c", "#4338ca", "#65a30d"]


# ---------------------------------------------------------------------------
# small helpers over the blob
# ---------------------------------------------------------------------------

def schemes_present(b: dict) -> list[str]:
    got = (b.get("schemes") or {}).get("write") or {}
    return [s for s in ORDER if s in got]


def rec(b: dict, op: str, s: str) -> dict[str, Any]:
    return ((b.get("schemes") or {}).get(op) or {}).get(s) or {}


def series(b: dict, op: str, s: str) -> dict[str, Any]:
    return (rec(b, op, s).get("full") or {}).get("done_series") or {}


def label(b: dict, s: str) -> str:
    return ((b.get("meta") or {}).get("label") or {}).get(s, s.upper())


def chosen(b: dict, s: str) -> str:
    return ((b.get("meta") or {}).get("chosen") or {}).get(s, "base")


def knob_txt(b: dict, s: str, cfg: str) -> str:
    kw = ((b.get("grid") or {}).get(s) or {}).get(cfg)
    if not kw:
        return "默认参数"
    return ", ".join(f"<code>{k}={v}</code>" for k, v in sorted(kw.items()))


def makespan(b: dict, op: str, s: str) -> int:
    return int(rec(b, op, s).get("makespan") or 0)


def spread(b: dict, op: str, s: str) -> float:
    fin = [v for v in (series(b, op, s).get("finish_by_group") or {}).values()
           if v]
    return round(max(fin) / min(fin), 4) if fin else 0.0


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def plot_done(b: dict, s: str, path: Path) -> None:
    """Per-group cumulative retired DAT flits, write and read side by side."""
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.1))
    for ax, op in zip(axes, OPS):
        ser = series(b, op, s)
        cum = ser.get("cum_by_group") or {}
        ts = ser.get("t") or []
        for g in sorted(cum, key=int):
            ax.plot(ts, cum[g], lw=1.5, color=DIE_COLOR[int(g) % 6],
                    label=f"group {g}")
        fin = ser.get("finish_by_group") or {}
        if fin:
            lo, hi = min(fin.values()), max(fin.values())
            ax.axvspan(lo, hi, color="#94a3b8", alpha=0.16, zorder=0)
            ax.axvline(hi, color="#475569", lw=1.0, ls="--")
            top = max((max(v) for v in cum.values()), default=1)
            ax.annotate(f"makespan {hi:,}", (hi, 0.5 * top),
                        xytext=(-6, 0), textcoords="offset points",
                        fontsize=7.5, color="#475569", ha="right")
        ax.set_title(f"{OP_CN[op]}（{DAT_CN[op]}）  "
                     f"最慢/最快 = {spread(b, op, s):.3f}", fontsize=9.5)
        ax.set_xlabel("时间（cycle）")
        ax.set_ylabel("该 group 已完成的 DAT flit 数")
        ax.grid(alpha=0.3)
        ax.margins(y=0.12)
        ax.legend(fontsize=7, ncol=2, loc="upper left", framealpha=0.9)
    fig.suptitle(f"{label(b, s)}　配置 {chosen(b, s)}", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_overlay(b: dict, path: Path) -> None:
    """Every scheme's slowest and fastest group, on one pair of axes.

    Two curves per scheme bound the band the six groups live in, so the
    figure shows both speed (how far right the band ends) and fairness (how
    wide it is) without six times five lines.
    """
    ss = schemes_present(b)
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.1))
    for ax, op in zip(axes, OPS):
        for s in ss:
            ser = series(b, op, s)
            cum = ser.get("cum_by_group") or {}
            ts = ser.get("t") or []
            if not cum:
                continue
            fin = ser.get("finish_by_group") or {}
            slow = max(fin, key=lambda g: fin[g]) if fin else None
            fast = min(fin, key=lambda g: fin[g]) if fin else None
            ax.plot(ts, cum[slow], lw=1.7, color=COLOR[s],
                    label=f"{s.upper()} 最慢组")
            ax.plot(ts, cum[fast], lw=0.9, color=COLOR[s], ls=":", alpha=0.8)
        ax.set_title(f"{OP_CN[op]}：各方案最慢组（实线）与最快组（点线）",
                     fontsize=9.5)
        ax.set_xlabel("时间（cycle）")
        ax.set_ylabel("已完成的 DAT flit 数")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_makespan(b: dict, path: Path) -> None:
    ss = schemes_present(b)
    fig, ax = plt.subplots(figsize=(8.4, 3.6))
    w = 0.38
    xs = range(len(ss))
    for i, op in enumerate(OPS):
        vals = [makespan(b, op, s) for s in ss]
        off = (i - 0.5) * w
        bars = ax.bar([x + off for x in xs], vals, w,
                      label=OP_CN[op],
                      color="#2563eb" if op == "write" else "#f59e0b")
        base = makespan(b, op, "s0") or 1
        for r, v in zip(bars, vals):
            ax.text(r.get_x() + r.get_width() / 2, v,
                    f"{v:,}\n{(v / base - 1) * 100:+.1f}%", ha="center",
                    va="bottom", fontsize=7)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([s.upper() for s in ss])
    ax.set_ylabel("makespan（cycle）")
    ax.set_title("同一流量 pattern 下的 makespan（百分比相对 S0）",
                 fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=8)
    ax.margins(y=0.18)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_cost(b: dict, area: dict, path: Path) -> None:
    """Makespan against added silicon. Down-and-left is better."""
    rows = {r["scheme"]: r for r in (area.get("rows") or [])}
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.0))
    for ax, op in zip(axes, OPS):
        base = makespan(b, op, "s0") or 1
        for s in schemes_present(b):
            r = rows.get(s)
            if r is None:
                continue
            x = max(r["cost_ff"], 1)
            y = makespan(b, op, s) / base
            ax.scatter([x], [y], s=70, color=COLOR[s], zorder=3)
            ax.annotate(f"{s.upper()}", (x, y), fontsize=8.5,
                        xytext=(6, 5), textcoords="offset points")
        ax.axhline(1.0, color="#dc2626", lw=1.0, ls="--")
        ax.set_xscale("log")
        ax.set_xlabel("新增状态（FF 等效，对数轴；S0 记 1）")
        ax.set_ylabel(f"{OP_CN[op]} makespan / S0")
        ax.set_title(f"{OP_CN[op]}：效果 vs 面积", fontsize=10)
        ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def plot_rootcause(b: dict, rc: dict, path: Path) -> None:
    """Structural bottleneck per group against when that group finished."""
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.0))
    for ax, op in zip(axes, OPS):
        res = ((rc.get("ops") or {}).get(op)) or {}
        rows = res.get("groups") or []
        if not rows:
            continue
        gs = [r["group"] for r in rows]
        crit = [r["crit_load"] for r in rows]
        ser = series(b, op, "s0")
        fin = ser.get("finish_by_group") or {}
        ax.bar([g - 0.2 for g in gs], crit, 0.4, color="#94a3b8",
               label="最热链路上的解析负载（flit）")
        ax2 = ax.twinx()
        ax2.bar([g + 0.2 for g in gs], [fin.get(str(g), 0) for g in gs], 0.4,
                color="#dc2626", label="S0 实测完成时刻（cycle）")
        ax.set_xticks(gs)
        ax.set_xlabel("top die group")
        ax.set_ylabel("解析负载（flit）")
        ax2.set_ylabel("完成时刻（cycle）")
        ax.margins(y=0.30)
        ax2.margins(y=0.30)
        m = res.get("measured") or {}
        rho = m.get("spearman_crit_finish")
        ax.set_title(f"{OP_CN[op]}：结构瓶颈 vs 实测完成"
                     + (f"（ρ={rho}）" if rho is not None else ""),
                     fontsize=10)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=7.5, loc="upper center",
                  framealpha=0.9)
        ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def sweep_table(b: dict, s: str) -> str:
    """Every configuration of one scheme at sweep scale, best first."""
    sw = b.get("sweep") or {}
    st = int((b.get("meta") or {}).get("sweep_tiles") or 1)
    rows: list[list[Any]] = []
    acc: dict[str, dict[str, Any]] = {}
    for key, r in sw.items():
        if r.get("scheme") != s or r.get("tiles") != st:
            continue
        acc.setdefault(r["config"], {})[r["op"]] = r
    pick = chosen(b, s)
    ranked = sorted(
        acc.items(),
        key=lambda kv: (not all(kv[1].get(op, {}).get("completed")
                                for op in OPS),
                        sum(kv[1].get(op, {}).get("makespan", 10 ** 9)
                            for op in OPS)))
    for cfg, per in ranked:
        ok = all(per.get(op, {}).get("completed") for op in OPS)
        star = " ★" if cfg == pick else ""
        rows.append([
            f"<code>{cfg}</code>{star}",
            knob_txt(b, s, cfg),
            f"{per.get('write', {}).get('makespan', 0):,}" if ok else "未排空",
            f"{per.get('read', {}).get('makespan', 0):,}" if ok else "未排空",
            f"{sum(per.get(op, {}).get('makespan', 0) for op in OPS):,}"
            if ok else "—",
            _f(per.get("write", {}).get("finish_spread"), 3),
            _f(per.get("read", {}).get("finish_spread"), 3),
        ])
    return _t(["配置", "参数", f"写 makespan（{st} tile）", "读 makespan",
               "写+读", "写 组间倍差", "读 组间倍差"], rows)


def bounds(b: dict, op: str) -> dict[str, Any]:
    """The composite lower bound; identical for every scheme on one batch."""
    for s in schemes_present(b):
        bd = (rec(b, op, s).get("full") or {}).get("bounds")
        if bd:
            return bd
    return {}


def bound_table(b: dict) -> str:
    rows = []
    for op in OPS:
        bd = bounds(b, op)
        if not bd:
            continue
        t0 = makespan(b, op, "s0")
        rows.append([
            OP_CN[op],
            f"{bd.get('link_lb', 0):,}",
            f"{bd.get('port_lb', 0):,}",
            f"{bd.get('cut_lb', 0):,}",
            f"{bd.get('txn_lb', 0):,}",
            f"<b>{bd.get('bound', 0):,}</b>",
            f"{t0:,}",
            f"<b>{100 * bd.get('bound', 0) / max(1, t0):.1f}%</b>",
        ])
    return _t(["批次", "单链路", "站点端口", "织物对分", "单事务时延",
               "合成下界", "S0 实测 makespan", "S0 达成率"], rows)


def final_table(b: dict) -> str:
    ss = schemes_present(b)
    rows = []
    for s in ss:
        r = {op: rec(b, op, s) for op in OPS}
        base = {op: makespan(b, op, "s0") or 1 for op in OPS}
        eff = []
        for op in OPS:
            bd = bounds(b, op).get("bound", 0)
            eff.append(100 * bd / max(1, makespan(b, op, s)))
        rows.append([
            f"<b>{label(b, s)}</b>",
            f"<code>{chosen(b, s)}</code>",
            f"{makespan(b, 'write', s):,}",
            f"{(makespan(b, 'write', s) / base['write'] - 1) * 100:+.1f}%",
            f"{makespan(b, 'read', s):,}",
            f"{(makespan(b, 'read', s) / base['read'] - 1) * 100:+.1f}%",
            _f(spread(b, "write", s), 3),
            _f(spread(b, "read", s), 3),
            f"{eff[0]:.1f}% / {eff[1]:.1f}%",
        ])
    return _t(["方案", "配置", "写 makespan", "对 S0", "读 makespan", "对 S0",
               "写 组间倍差", "读 组间倍差", "达成率 写/读"], rows)


def finish_table(b: dict, op: str) -> str:
    ss = schemes_present(b)
    rows = []
    for s in ss:
        fin = series(b, op, s).get("finish_by_group") or {}
        if not fin:
            continue
        vals = [fin.get(str(g), 0) for g in range(6)]
        rows.append([f"<b>{s.upper()}</b>"] + [f"{v:,}" for v in vals]
                    + [f"{max(vals) - min(vals):,}",
                       _f(max(vals) / min(vals), 3) if min(vals) else "—"])
    return _t(["方案"] + [f"group {g}" for g in range(6)] + ["极差", "倍差"],
              rows)


def area_table(area: dict) -> str:
    rows = []
    for r in area.get("rows") or []:
        brk = r["breakdown"]
        rows.append([
            f"<b>{r['label']}</b>",
            f"<code>{r['config']}</code>",
            f"{r['cost_ff']:,}",
            f"{brk.get('bus', 0):,}", f"{brk.get('table', 0):,}",
            f"{brk.get('counters', 0):,}", f"{brk.get('arith', 0):,}",
            f"{brk.get('queue', 0):,}",
            r["note"],
        ])
    return _t(["方案", "配置", "合计 FF 等效", "总线", "表", "计数器",
               "算术", "队列", "说明"], rows)


def rootcause_table(rc: dict, op: str) -> str:
    res = ((rc.get("ops") or {}).get(op)) or {}
    rows = []
    for r in res.get("groups") or []:
        rows.append([
            f"group {r['group']}",
            f"{r['flit_hops_total']:,}",
            " / ".join(f"{k}={v:,}" for k, v in r["flit_hops"].items()),
            f"{r['crit_fabric']}:{r['crit_vc']}",
            f"{r['crit_load']:,}",
            f"{100 * r['crit_own_frac']:.1f}%",
            f"{r['d2d']['down']['total']:,} / {r['d2d']['up']['total']:,}",
            _f(r["d2d"]["up"]["spread"], 3),
        ])
    return _t(["group", "解析 flit·hop", "按织物拆分", "最热链路",
               "该链路解析负载", "其中本组占比", "D2D 下行/上行 flit",
               "组内 8 条 D2D 上行倍差"], rows)


# ---------------------------------------------------------------------------
# narrative fragments that have to follow the data
# ---------------------------------------------------------------------------

def rootcause_text(b: dict, rc: dict) -> str:
    out = []
    for op in OPS:
        res = ((rc.get("ops") or {}).get(op)) or {}
        rows = res.get("groups") or []
        m = res.get("measured") or {}
        if not rows:
            continue
        crit = [r["crit_load"] for r in rows]
        own = [r["crit_own_frac"] for r in rows]
        cs = max(crit) / min(crit) if min(crit) else 0.0
        d2d_ok = all(r["d2d"]["up"]["spread"] <= 1.001
                     and r["d2d"]["down"]["spread"] <= 1.001 for r in rows)
        out.append(f"""<h4>{OP_CN[op]}</h4>
<p>六个 group 的解析需求几乎相同（flit·hop 极差
{max(r['flit_hops_total'] for r in rows) - min(r['flit_hops_total'] for r in rows):,}，
约 {100 * (max(r['flit_hops_total'] for r in rows) / min(r['flit_hops_total'] for r in rows) - 1):.1f}%），
{"每个 group 自己的 8 条 D2D 上下行也完全均衡（倍差 1.000）"
 if d2d_ok else "组内 D2D 已经不均衡"}，
所以差异不在“谁的活多”，也不在跨 die 那一跳。
真正分开它们的是<b>各自路径上最热的那条有向链路要驮多少 flit</b>：
最热 {max(crit):,}、最冷 {min(crit):,}，倍差 <b>{cs:.3f}</b>；
本组在这条链路上的占比从 {100 * min(own):.1f}% 到 {100 * max(own):.1f}%
{"——占比低意味着这条链路主要是<b>别人的</b>流量，该组只能排队"
 if min(own) < 0.9 else "——链路基本是本组独占"}。
实测 S0 完成时刻 {m.get('finish')}，
倍差 {_f(m.get('finish_spread'), 3)}，与结构瓶颈的 Spearman
ρ = <b>{_f(m.get('spearman_crit_finish'), 3)}</b>
（Pearson {_f(m.get('pearson_crit_finish'), 3)}）；
与“活的多少”只有 Pearson {_f(m.get('pearson_demand_finish'), 3)}。</p>""")
    return "\n".join(out)


def effect_text(b: dict, area: dict) -> str:
    rows = {r["scheme"]: r for r in (area.get("rows") or [])}
    items = []
    for s in schemes_present(b):
        if s == "s0":
            continue
        cost = (rows.get(s) or {}).get("cost_ff", 0)
        dw = makespan(b, "write", s) / max(1, makespan(b, "write", "s0")) - 1
        dr = makespan(b, "read", s) / max(1, makespan(b, "read", "s0")) - 1
        verdict = ("两个批次都比 S0 快" if dw < 0 and dr < 0 else
                   "写更快、读更慢" if dw < 0 else
                   "读更快、写更慢" if dr < 0 else
                   "两个批次都没有比 S0 快")
        items.append(
            f"<li><b>{label(b, s)}</b>（<code>{chosen(b, s)}</code>）："
            f"写 {dw * 100:+.1f}%、读 {dr * 100:+.1f}%，{verdict}；"
            f"新增状态 <b>{cost:,}</b> FF 等效。</li>")
    return "<ul>" + "\n".join(items) + "</ul>"


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------

def build(b: dict, area: dict, rc: dict) -> str:
    t, m = b["topology"], b["meta"]
    ss = schemes_present(b)
    n_txn = m.get("n_txn", 0)
    k_core = m.get("txn_per_core", 0)
    burst = m.get("burst_len", 128)
    stride = m.get("stride", 4096)
    tile = m.get("tiling_size", 65536) // 1024
    st = m.get("sweep_tiles", 1)
    ct = m.get("tiles", 4)

    _cjk()
    plot_top_die(b, IMG / "cc_top_die.png")
    plot_topology(b, IMG / "cc_topology.png")
    plot_binding(b, IMG / "cc_binding.png")
    for s in ss:
        plot_done(b, s, IMG / f"cc_done_{s}.png")
    plot_overlay(b, IMG / "cc_overlay.png")
    plot_makespan(b, IMG / "cc_makespan.png")
    if area.get("rows"):
        plot_cost(b, area, IMG / "cc_cost.png")
    if rc.get("ops"):
        plot_rootcause(b, rc, IMG / "cc_rootcause.png")

    sec = {}
    for s in ss:
        if s == "s0":
            continue
        sec[s] = f"""
<h3>{label(b, s)}</h3>
<p>参数 sweep（{st} tile，{len(((b.get('grid') or {}).get(s) or {}))} 组配置，
写读各跑一遍，按写+读 makespan 之和排序；★ 是被 {ct} tile 确认后选中的配置）：</p>
{sweep_table(b, s)}
<p>选中配置：<code>{chosen(b, s)}</code>，{knob_txt(b, s, chosen(b, s))}。
在 {ct} tile 全量批次上，写 makespan
<b>{makespan(b, 'write', s):,}</b>、读 makespan
<b>{makespan(b, 'read', s):,}</b>。</p>
<img src="cc_done_{s}.png" alt="{s} 各 group 完成曲线">"""

    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>3D 堆叠 NoC 拥塞控制方案研究</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, "WenQuanYi Micro Hei",
        sans-serif; max-width: 1000px; margin: 1.5rem auto; padding: 0 1rem;
        line-height: 1.55; color: #111; }}
h1 {{ font-size: 1.45rem; }} h2 {{ font-size: 1.2rem; margin-top: 2rem; }}
h3 {{ font-size: 1.05rem; }} h4 {{ font-size: 0.97rem; margin-bottom: .2rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.86rem;
         margin: 0.6rem 0 1rem; }}
th, td {{ border: 1px solid #d4d4d8; padding: 0.28rem 0.45rem;
          text-align: left; vertical-align: top; }}
th {{ background: #f4f4f5; }}
.def {{ background: #eff6ff; border-left: 4px solid #2563eb;
        padding: 0.6rem 0.9rem; margin: 0.8rem 0; }}
.good {{ background: #f0fdf4; border-left-color: #16a34a; }}
.warn {{ background: #fffbeb; border-left-color: #f59e0b; }}
img {{ max-width: 100%; height: auto; margin: 0.6rem 0 1rem; }}
code {{ font-size: 0.86em; }}
</style></head><body>

<h1>3D 堆叠 NoC 的拥塞控制方案：S0 / S1 / S22 / S16</h1>
<p>硬件与 <code>report_ring2_stack_write_fairness.html</code>
<b>完全一致</b>：六个 top die，每个是 20 节点双向 full ring × 2 plane
（10 个 AI core、8 个 D2D bridge、2 个非终端节点，逐边 hop 时延沿用单环研究）；
一个 bottom die，96 个 HA 排成 12 行 × {t['n_cols']} 列，
6 横 + 8 纵双向 full ring，48 个挂接点即 D2D landing。
每个 top die 的 <b>10 个 core 记为一个 group</b>，共 6 个 group。</p>
<p>流量：每核 {burst} B burst / {stride} B stride /
{tile} KB tile × {m.get('n_tiles', ct)}，地址按 burst 交织到全部
{t['n_has']} 个 HA。<b>写批次和读批次分开跑</b>，各
{n_txn:,} 笔（每核 {k_core:,} 笔），同一套地址、两次独立仿真，从不混合。
每核 outstanding {m['core_outstanding']}，每个 HA 跟踪表
{m.get('pos_depth')} 项（满了走 CHI 请求 retry，不是源端流控）。</p>
<div class="def"><b>怎么读这份报告。</b>
每个方案一张图，纵轴是该 group 已经<b>完成</b>的 DAT flit 数
（写数 WriteData，读数 CompData，都按事务退休的那一拍计），横轴是时间。
六条线并在一起 = 织物公平；散开 = 有 group 在等别人。
最慢那条线的右端就是 makespan，也是全文唯一的效果指标。
灰带标出最快组与最慢组完成时刻之间的差。</div>

<h2>0　硬件 setup</h2>
<h3>0.1　Top die（六个，互相独立）</h3>
{top_die_setup_table(b)}
<h4>逐边 hop 时延</h4>
{top_die_hop_table(b)}
<img src="cc_top_die.png" alt="top die 拓扑">
<h3>0.2　Bottom die（一个，承载全部 96 个 HA）</h3>
{bottom_die_setup_table(b)}
<h4>链路时延</h4>
{bottom_die_link_table(b)}
<img src="cc_topology.png" alt="堆叠拓扑">
<h4>HA 到 D2D bridge 的绑定</h4>
<p>路由不是自由最短路：目的 HA 决定了出 die 的那一跳走哪个 bridge，
所以“走哪条路”是硬件规定的，不是拥塞控制能改的。</p>
<img src="cc_binding.png" alt="HA 与 bridge 绑定">

<h2>1　方法：两阶段参数 sweep</h2>
<p>一个 {ct} tile 批次是 {n_txn:,} 笔事务，跑一次要几十分钟，
把三个方案的全部配置都按这个规模跑一遍不现实。所以分两阶段：</p>
<ul>
<li><b>阶段一</b>（{st} tile）：每个方案的<b>全部</b>配置，写读各一遍。
同样的 60 个核覆盖同样的 96 个 HA，只是事务数少四分之一，
用来定<b>配置之间的排序</b>。</li>
<li><b>阶段二</b>（{ct} tile）：阶段一每个方案的前二名，按全量规模重跑，
取写+读 makespan 之和最小的那个作为该方案的代表。</li>
</ul>
<p><b>报告里所有曲线、所有最终数字都来自阶段二</b>；阶段一只决定谁有资格出现，
并且下面把整张网格都列出来，选择过程可以复核。
累计仿真开销 {(m.get('wall_s', 0) / 3600):.1f} 机时。</p>

<h2>2　S0：没有源端流控</h2>
<p>S0 是对照组：只要 outstanding 有空位、环上有 slot 就发，
没有任何窗口、赤字或授权。它给出的组间差异就是<b>织物本身</b>的差异。</p>
<img src="cc_done_s0.png" alt="S0 各 group 完成曲线">
<h4>各 group 完成时刻</h4>
<h5>写</h5>
{finish_table(b, "write")}
<h5>读</h5>
{finish_table(b, "read")}
<p>写批次六个 group 基本同时收尾（倍差
{_f(spread(b, 'write', 's0'), 3)}），读批次却明显散开（倍差
{_f(spread(b, 'read', 's0'), 3)}）。第 6 节给出这个差异的根因，
结论是它不是调度不公，而是结构性的。</p>

<h2>3　S1：源端 AIMD 控速</h2>
<p>每个 core 维持一个发送窗口，按 6 bit 拥塞总线广播回来的等级
乘性减、加性增。总线延迟 30 cycle；受控节点表让 core 看到自己路径上
每个站点的等级并取最大。</p>
{sec.get("s1", "")}

<h2>4　S22：赤字流控（细化）</h2>
<p>S22 记的不是拥塞等级而是<b>赤字</b>：每个成员在一个窗口里实际拿到的
带宽与它应得的份额之差。赤字为正（拿多了）的成员在两处让步：</p>
<ul>
<li><b>让行（scoped yield）</b>：只在会挤到赤字为负的成员的那一跳上
放弃注入，不是全局降速。让行的判定用精确路径跨度，
不是“同一个环上就算”。</li>
<li><b>绕行（dodge look-ahead）</b>：注入仲裁不再死守队头，
在队列前 <code>dfc_dodge</code> 项里挑一个不会加剧赤字的 flit 先上环。
这一项要在数据通路上开一个前瞻窗口，是 S22 唯一动到注入端的地方。</li>
</ul>
<div class="warn"><b>粒度决定了执行机构落在哪里。</b>
按 core 记赤字时，让行有意义（同一个 top 环上十个核互相挤），
但绕行没有意义——一个核队列里的 flit 都属于同一个成员。
按 group 记赤字时正相反：一个 top 环<b>就是</b>一个 group，
组间在环上根本不相遇，让行恒不触发；有意义的执行点在 HA 侧，
六个 group 的响应在那里共用端口，绕行按目的地排序。
网格里这两条路线是分开扫的，参数配对也是按这个结构定的。</div>
{sec.get("s22", "")}

<h2>5　S16：目的端授权（改进为 per-group 粒度）</h2>
<p>Homa 式接收端授权：HA 不再见到 REQ 就回 DBIDResp，
而是按自己的服务计数挑一个类别授权，授权本身搭在既有的
DBIDResp（写）/ CompData（读）上，不加总线、不加新报文。
原方案按 <b>core</b> 仲裁，每个 HA 要维护 60 项服务计数；
改进后按 <b>group</b> 仲裁——先选累计服务最少的 group，
组内再轮转——每个 HA 只需要 6 项，并且可选按组预留配额下限。</p>
{sec.get("s16g", "")}{sec.get("s16", "")}

<h2>6　对比与总结</h2>
<h3>6.1　这块织物还剩多少余量</h3>
<p>在比较方案之前先要知道能比出多少。下界取四项里的最大：
任意一条有向链路每 VC 每拍过 1 个 flit、任意一个站点端口每拍上/下 1 个 flit、
每类织物的总 flit·hop 除以它的链路数、以及一笔事务本身的串行时延。</p>
{bound_table(b)}
<h3>6.2　同一流量 pattern 下的 makespan</h3>
<img src="cc_makespan.png" alt="makespan 对比">
{final_table(b)}
<img src="cc_overlay.png" alt="各方案最慢/最快组对比">
<h3>6.3　S0 各 group 带宽差异的根因</h3>
<p>把每一笔事务的正反向路由都走一遍，按发起 core 所属 group 记账，
得到三个纯结构量：该 group 的解析 flit·hop 需求（活有多少）、
它自己那 8 条 D2D 上下行的负载（跨 die 那一跳有没有偏）、
以及<b>它路径上最热的那条有向链路一共要驮多少 flit</b>
（一条有向链路每 VC 每拍只过 1 个 flit，所以这个数就是该 group 的
结构下界，单位直接是 cycle）。</p>
{rootcause_text(b, rc)}
<img src="cc_rootcause.png" alt="结构瓶颈与实测完成时刻">
<h4>写</h4>
{rootcause_table(rc, "write")}
<h4>读</h4>
{rootcause_table(rc, "read")}
<div class="def"><b>根因。</b>
写的 DAT 从挂接点<b>发散</b>到整行 HA，每个 group 路径上最热的链路
基本由本组独占，六个 group 的下界几乎相等，所以 S0 的写本来就齐；
读的 CompData 从整行 HA <b>汇聚</b>回本组挂接点，
汇聚段被相邻 group 共用，靠中间的两个 group 撞在负载较轻的段上、
其余四个撞在同一条重段上，结构下界就分成了两档。
这一档差距不是仲裁造成的，任何只在源端做文章的方案都消不掉它——
要消掉得改绑定或改路由。</div>
<h3>6.4　效果与芯片面积成本</h3>
<p>面积按 FF 等效计：总线是每个要听的站点latch 一份广播；
表是每个站点对其他成员的视图；计数器是窗口、赤字、服务计数；
算术把比较器、加法器和求均值的归约树折算成面积；
队列是<b>超出</b>现有注入队列深度的那部分，按 flit 宽度
{(area.get('geometry') or {}).get('flit_bits', 288)} bit 计。
凡是仿真能测的量都用实测值定尺寸（S1 的路径表用实测平均路径节点数，
S22 的赤字表用它实际跑的成员数，S16 的服务表用它的仲裁粒度）。</p>
{area_table(area)}
<img src="cc_cost.png" alt="效果与面积">
{effect_text(b, area)}
<h3>6.5　结论</h3>
<div class="def good">
<ol>
<li><b>S0 的写已经接近结构下界，读的组间差异是拓扑造成的，不是调度造成的。</b>
读的最热链路负载在六个 group 之间有一档真实的差距，
与实测完成顺序高度相关；而 D2D 跨 die 那一跳、以及各组的总工作量，
都是均衡的，都不是根因。</li>
<li><b>源端方案（S1）在这个 pattern 上没有可利用的空间。</b>
它花在总线和路径表上的面积买到的是“少发一点”，
但瓶颈链路的总负载不变，makespan 只能持平或变差。</li>
<li><b>S22 的价值在注入排序而不在降速。</b>
让行在组粒度上结构性失效，真正起作用的是按目的地的绕行前瞻；
一旦为它加深注入队列，队列 SRAM 就会以两个数量级压过整个控制器的面积，
这是它在成本轴上唯一需要小心的地方。</li>
<li><b>S16 从 per-core 改成 per-group，是本文里性价比最好的一处改动。</b>
仲裁轴从 60 项缩到 6 项，每个 HA 的服务计数表和比较树都跟着缩一个数量级，
授权仍然搭在既有 DBIDResp / CompData 上，不加报文也不加总线。</li>
</ol>
</div>

<h2>附录　完整网格</h2>
{"".join(f"<h4>{label(b, s)}</h4>{sweep_table(b, s)}" for s in ss if s != "s0")}
</body></html>"""
    return html


def main() -> None:
    if not FOCUS.exists():
        raise SystemExit(f"missing {FOCUS}; run dse_stack_cc_sweep.py --emit")
    b = json.loads(FOCUS.read_text())
    area = json.loads(AREA.read_text()) if AREA.exists() else {}
    rc = json.loads(RCAUSE.read_text()) if RCAUSE.exists() else {}
    OUT.write_text(build(b, area, rc))
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
