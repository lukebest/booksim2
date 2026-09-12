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
BW97 = ROOT / "results" / "stack_bw97_focus.json"
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
    """Speed and fairness together: neither number means much alone."""
    ss = schemes_present(b)
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 3.8))
    w = 0.38
    xs = range(len(ss))
    ax = axes[0]
    for i, op in enumerate(OPS):
        vals = [makespan(b, op, s) for s in ss]
        bars = ax.bar([x + (i - 0.5) * w for x in xs], vals, w,
                      label=OP_CN[op],
                      color="#2563eb" if op == "write" else "#f59e0b")
        base = makespan(b, op, "s0") or 1
        for r, v in zip(bars, vals):
            ax.text(r.get_x() + r.get_width() / 2, v,
                    f"{v:,}\n{(v / base - 1) * 100:+.1f}%", ha="center",
                    va="bottom", fontsize=7)
    ax.set_ylabel("makespan（cycle）")
    ax.set_title("makespan（百分比相对 S0）", fontsize=10)
    ax = axes[1]
    for i, op in enumerate(OPS):
        vals = [spread(b, op, s) for s in ss]
        bars = ax.bar([x + (i - 0.5) * w for x in xs], vals, w,
                      label=OP_CN[op],
                      color="#2563eb" if op == "write" else "#f59e0b")
        for r, v in zip(bars, vals):
            ax.text(r.get_x() + r.get_width() / 2, v, f"{v:.3f}",
                    ha="center", va="bottom", fontsize=7)
    ax.axhline(1.0, color="#16a34a", lw=1.0, ls="--")
    ax.set_ylabel("最慢组 / 最快组完成时刻")
    ax.set_title("组间不均衡（1.000 = 六组同时收尾）", fontsize=10)
    for ax in axes:
        ax.set_xticks(list(xs))
        ax.set_xticklabels([s.upper() for s in ss])
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
        # Points land on top of each other on the log axis, so they are
        # named in the legend rather than beside the marker.
        for s in schemes_present(b):
            if s not in rows:
                continue
            ax.scatter([max(rows[s]["cost_ff"], 1)],
                       [makespan(b, op, s) / base], s=80, color=COLOR[s],
                       zorder=3, label=f"{s.upper()} ({rows[s]['cost_ff']:,})")
        ax.axhline(1.0, color="#dc2626", lw=1.0, ls="--")
        ax.set_xscale("log")
        ax.set_xlabel("新增状态（FF 等效，对数轴；S0 记 1）")
        ax.set_ylabel(f"{OP_CN[op]} makespan / S0")
        ax.set_title(f"{OP_CN[op]}：效果 vs 面积（越靠左下越好）",
                     fontsize=10)
        ax.grid(alpha=0.3, which="both")
        ax.legend(fontsize=7.5, loc="upper left")
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
        d2d = max(max(r["d2d"]["up"]["spread"], r["d2d"]["down"]["spread"])
                  for r in rows)
        dem = [r["flit_hops_total"] for r in rows]
        fs = m.get("finish_spread") or 0.0
        shared = ("，占比低意味着这条链路主要是<b>别人的</b>流量"
                  if min(own) < 0.9 else "，链路基本是本组独占")
        # A correlation against a quantity that barely varies is arithmetic
        # on noise, so say which case this is before quoting the number.
        verdict = (
            f"""结构下界在六个 group 之间只差 {100 * (cs - 1):.1f}%，
而实测完成时刻差 {100 * (fs - 1):.1f}%，
<b>量级对不上</b>：这一批的组间先后是仲裁的随机性，不是织物的形状。
相关系数（Spearman ρ = {_f(m.get('spearman_crit_finish'), 3)}）
是在一个几乎不变的量上算出来的，不能当作证据。"""
            if cs < 1.05 else
            f"""结构下界在六个 group 之间差 <b>{100 * (cs - 1):.0f}%</b>，
实测完成时刻差 {100 * (fs - 1):.0f}%，两者<b>同向且同量级</b>：
Spearman ρ = <b>{_f(m.get('spearman_crit_finish'), 3)}</b>
（Pearson {_f(m.get('pearson_crit_finish'), 3)}）。
“本组占比”这一项的相关性同样强而反向
（ρ = {_f(m.get('spearman_own_finish'), 3)}）——
一个 group 在自己最热的那条链路上占得越少，它排在越后面完成，
因为它等的是<b>别人的</b> flit 过完。
相比之下“活的多少”几乎不解释什么
（Pearson {_f(m.get('pearson_demand_finish'), 3)}，
而六个 group 的解析需求本来就只差
{100 * (max(dem) / min(dem) - 1):.1f}%）。""")
        out.append(f"""<h4>{OP_CN[op]}</h4>
<p>先排除两个常见的怀疑对象。六个 group 的解析需求几乎相同
（flit·hop 最多差 {100 * (max(dem) / min(dem) - 1):.1f}%），
每个 group 自己那 8 条 D2D 上下行也是均衡的
（组内最大倍差 {d2d:.3f}），所以差异不在“谁的活多”，
也不在跨 die 那一跳。</p>
<p>剩下的量是<b>各自路径上最热的那条有向链路要驮多少 flit</b>：
最热 {max(crit):,}、最冷 {min(crit):,}，倍差 <b>{cs:.3f}</b>；
本组在这条链路上的占比从 {100 * min(own):.1f}% 到 {100 * max(own):.1f}%{shared}。
实测 S0 完成时刻 {m.get('finish')}。{verdict}</p>""")
    return "\n".join(out)


NOISE = 0.01     # below this, a makespan difference is not a result
BW97_TARGET = 0.97


def _bw97_rec(bw: dict, cfg: str, op: str, slot: str = "confirm") -> dict:
    return ((bw.get(slot) or {}).get(cfg) or {}).get(op) or {}


def _bw97_eff(bw: dict, cfg: str, op: str, slot: str = "confirm") -> float:
    r = _bw97_rec(bw, cfg, op, slot)
    return float(r.get("eff") or 0)


def _bw97_knob_txt(kw: dict) -> str:
    if not kw:
        return "与 §0 相同（未加宽）"
    names = {
        "core_outstanding": "outstanding",
        "d2d_bw": "D2D 链路宽",
        "h_bw": "横环宽",
        "v_bw": "纵环宽",
        "top_bw": "top 环宽",
        "bridge_bw": "bridge 上环宽",
        "turn_bw": "H↔V 转向宽",
        "inject_bw": "注入口宽",
        "eject_bw": "弹出宽",
    }
    return "，".join(f"{names.get(k, k)} = {v}" for k, v in sorted(kw.items()))


def plot_bw97_done(bw: dict, cfg: str, path: Path) -> None:
    """Same axes as plot_done, but the series live under confirm[cfg][op]."""
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.1))
    for ax, op in zip(axes, OPS):
        rec = _bw97_rec(bw, cfg, op)
        ser = (rec.get("full") or {}).get("done_series") or {}
        cum = ser.get("cum_by_group") or {}
        ts = ser.get("t") or []
        for g in sorted(cum, key=int):
            ax.plot(ts, cum[g], lw=1.5, color=DIE_COLOR[int(g) % 6],
                    label=f"group {g}")
        fin = ser.get("finish_by_group") or rec.get("group_finish") or {}
        fin = {str(k): v for k, v in fin.items()}
        if fin:
            lo, hi = min(fin.values()), max(fin.values())
            ax.axvspan(lo, hi, color="#94a3b8", alpha=0.16, zorder=0)
            ax.axvline(hi, color="#475569", lw=1.0, ls="--")
            top = max((max(v) for v in cum.values()), default=1)
            ax.annotate(f"makespan {hi:,}", (hi, 0.5 * top),
                        xytext=(-6, 0), textcoords="offset points",
                        fontsize=7.5, color="#475569", ha="right")
        vals = [v for v in fin.values() if v]
        sp = (max(vals) / min(vals)) if vals else 0
        ax.set_title(f"{OP_CN[op]}  最慢/最快 = {sp:.3f}  "
                     f"达成率 {_bw97_eff(bw, cfg, op):.1%}", fontsize=9.5)
        ax.set_xlabel("时间（cycle）")
        ax.set_ylabel("该 group 已完成的 DAT flit 数")
        ax.grid(alpha=0.3)
        ax.margins(y=0.12)
        ax.legend(fontsize=7, ncol=2, loc="upper left", framealpha=0.9)
    fig.suptitle(f"加宽 setup　{cfg}", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def bw97_setup_table(bw: dict) -> str:
    win = (bw.get("meta") or {}).get("winner") or "—"
    knobs = (bw.get("meta") or {}).get("winner_knobs") or {}
    base = {
        "core_outstanding": 128, "d2d_bw": 1, "h_bw": 1, "v_bw": 1,
        "top_bw": 1, "bridge_bw": 1, "turn_bw": 1, "inject_bw": 1, "eject_bw": 1,
        "turn_depth": 64, "d2d_depth": 128, "d2d_land_depth": 16,
        "inj_depth": 12, "dir_inj_depth": 8,
    }
    new = dict(base)
    new.update(knobs)
    rows = []
    labels = [
        ("core_outstanding", "每核 outstanding"),
        ("d2d_bw", "D2D 链路宽（flit/拍/VC）"),
        ("bridge_bw", "bridge / 落地 上环宽"),
        ("turn_bw", "H↔V 转向宽（离开 tap + FIFO 下环）"),
        ("h_bw", "底 die 横环宽"),
        ("v_bw", "底 die 纵环宽"),
        ("top_bw", "top die 环宽"),
        ("inject_bw", "注入口宽"),
        ("eject_bw", "弹出宽"),
        ("turn_depth", "转向 FIFO（未改）"),
        ("d2d_depth", "D2D FIFO（未改）"),
        ("d2d_land_depth", "落地 buffer（未改）"),
        ("inj_depth", "注入 FIFO（未改）"),
    ]
    for k, lab in labels:
        a, b = base[k], new.get(k, base[k])
        mark = "—" if a == b else f"<b>{a} → {b}</b>"
        rows.append([lab, str(a), str(b), mark])
    return _t(["项目", "§0 原 setup", f"加宽 setup（{win}）", "变化"], rows)


def bw97_sweep_table(bw: dict) -> str:
    grid = bw.get("grid") or {}
    rows = []
    for cfg, kw in grid.items():
        wr = _bw97_rec(bw, cfg, "write", "sweep")
        rd = _bw97_rec(bw, cfg, "read", "sweep")
        if not wr and not rd:
            continue
        star = " ★" if cfg == (bw.get("meta") or {}).get("winner") else ""
        rows.append([
            f"<code>{cfg}</code>{star}",
            _bw97_knob_txt(kw),
            f"{wr.get('makespan', 0):,}" if wr else "—",
            f"{100 * float(wr.get('eff') or 0):.1f}%" if wr else "—",
            f"{rd.get('makespan', 0):,}" if rd else "—",
            f"{100 * float(rd.get('eff') or 0):.1f}%" if rd else "—",
        ])
    return _t(["配置", "加宽项", "写 makespan（1 tile）", "写达成率",
               "读 makespan", "读达成率"], rows)


def _bw97_n(rec: dict, *keys, default: int = 0) -> int:
    cur: Any = rec
    for k in keys:
        cur = (cur or {}).get(k) if isinstance(cur, dict) else None
    return int(cur or default)


def bw97_final_table(bw: dict) -> str:
    rows = []
    win = (bw.get("meta") or {}).get("winner")
    confirm = bw.get("confirm") or {}
    cfgs: list[str] = []
    if "base" in confirm:
        cfgs.append("base")
    for cfg in bw.get("grid") or {}:
        if cfg != "base" and cfg in confirm:
            cfgs.append(cfg)
    for extra in confirm:
        if extra not in cfgs:
            cfgs.append(extra)
    for cfg in cfgs:
        wr, rd = _bw97_rec(bw, cfg, "write"), _bw97_rec(bw, cfg, "read")
        if not wr and not rd:
            continue
        star = " ★" if cfg == win else ""
        rows.append([
            f"<b>{cfg}</b>{star}",
            _bw97_knob_txt((bw.get("grid") or {}).get(cfg) or {}),
            f"{_bw97_n(wr, 'makespan'):,}",
            f"{_bw97_n(wr, 'bounds', 'bound'):,}",
            f"<b>{100 * float(wr.get('eff') or 0):.1f}%</b>",
            f"{_bw97_n(rd, 'makespan'):,}",
            f"{_bw97_n(rd, 'bounds', 'bound'):,}",
            f"<b>{100 * float(rd.get('eff') or 0):.1f}%</b>",
        ])
    return _t(["配置", "加宽项", "写 makespan", "写下界", "写达成率",
               "读 makespan", "读下界", "读达成率"], rows)


def _bw97_knobs(bw: dict, cfg: str) -> dict:
    return dict((bw.get("grid") or {}).get(cfg) or {})


def _bw97_published_bound(kw: dict) -> bool:
    """True when H/V/top stay at width 1, so the §0 h:dat floor is unchanged."""
    return (int(kw.get("h_bw", 1)) == 1
            and int(kw.get("v_bw", 1)) == 1
            and int(kw.get("top_bw", 1)) == 1)


def bw97_oc_pareto_table(bw: dict) -> str:
    """Outstanding-only ladder on the published bound (D2D×2, bridge×2)."""
    rows = []
    for cfg in bw.get("grid") or {}:
        kw = _bw97_knobs(bw, cfg)
        if cfg == "base" or not _bw97_published_bound(kw):
            continue
        if int(kw.get("d2d_bw", 1)) != 2 or int(kw.get("bridge_bw", 1)) != 2:
            continue
        wr, rd = _bw97_rec(bw, cfg, "write"), _bw97_rec(bw, cfg, "read")
        if not wr or not rd:
            continue
        oc = int(kw.get("core_outstanding", 128))
        turn = int(kw.get("turn_bw", 1))
        rows.append((oc, turn, cfg, wr, rd))
    if not rows:
        return ""
    rows.sort()
    win = (bw.get("meta") or {}).get("winner")
    out = []
    for oc, turn, cfg, wr, rd in rows:
        star = " ★" if cfg == win else ""
        out.append([
            f"{oc}",
            f"{turn}",
            f"<code>{cfg}</code>{star}",
            f"{_bw97_n(wr, 'makespan'):,}",
            f"<b>{100 * float(wr.get('eff') or 0):.1f}%</b>",
            f"{_bw97_n(rd, 'makespan'):,}",
            f"<b>{100 * float(rd.get('eff') or 0):.1f}%</b>",
            f"{float(wr.get('finish_spread') or 0):.3f}",
            f"{float(rd.get('finish_spread') or 0):.3f}",
        ])
    return _t(["outstanding", "转向宽", "配置", "写 makespan", "写达成率",
               "读 makespan", "读达成率", "写组间倍差", "读组间倍差"], out)


def bw97_bound_shift_note(bw: dict) -> str:
    """Why doubling the bound-setting fabric loses the ratio."""
    specs = (
        ("oc256-d2d2-br2", "横环仍是 1，下界停在 §0 的 h:dat"),
        ("oc256-d2d2-br2-h2", "横环×2 之后写下界改由 v:dat 决定"),
        ("oc256-d2d2-br2-h2-v2", "再把纵环×2，下界回到更窄的 h:dat"),
        ("oc256-all2", "四条织物一起×2，下界再掉一档"),
    )
    rows = []
    for cfg, why in specs:
        wr, rd = _bw97_rec(bw, cfg, "write"), _bw97_rec(bw, cfg, "read")
        if not wr or not rd:
            continue
        rows.append([
            f"<code>{cfg}</code>",
            why,
            f"{_bw97_n(wr, 'bounds', 'bound'):,}",
            f"{_bw97_n(wr, 'makespan'):,}",
            f"<b>{100 * float(wr.get('eff') or 0):.1f}%</b>",
            f"{_bw97_n(rd, 'bounds', 'bound'):,}",
            f"{_bw97_n(rd, 'makespan'):,}",
            f"<b>{100 * float(rd.get('eff') or 0):.1f}%</b>",
        ])
    if not rows:
        return ""
    table = _t(["配置", "下界怎么动", "写下界", "写 makespan", "写达成率",
                "读下界", "读 makespan", "读达成率"], rows)
    return f"""<div class="def"><b>加宽正在定下界的那条边，达成率通常会掉。</b>
解析下界是「最热那条边的占用 / 该边宽度」。横环×2 把写的
<i>h:dat</i> 30752 打成 15376，但纵环 DAT 仍要 20496 拍，下界只降到
20496；实测写只降到 23728，组 0/1 还卡在纵环上（倍差 1.43），达成率
从 89% 掉到 86%。再把纵环×2，下界跟到 15376，makespan 只跟到 18454，
又掉到 83%。读在横环×2 时已经 98.9%，再加宽纵环反而把四组重新叠到
更窄的横环上，倍差回到 1.75。所以允许的带宽旋钮里，<b>不能</b>靠
把定下界的织物翻倍来抬达成率；只能在下界不动的前提下挤握手和转向。</div>
{table}"""


def bw97_confirm_note(bw: dict) -> str:
    items = []
    for cfg in bw.get("grid") or {}:
        wr, rd = _bw97_rec(bw, cfg, "write"), _bw97_rec(bw, cfg, "read")
        if cfg == "base" or not wr or not rd:
            continue
        sp = float(wr.get("finish_spread") or 0)
        tail = (f"，写组间倍差 {sp:.3f}" if sp > 1.05 else "")
        items.append(
            f"<li><code>{cfg}</code>：写 "
            f"{_bw97_n(wr, 'makespan'):,} / {_bw97_n(wr, 'bounds', 'bound'):,} "
            f"= {100 * float(wr.get('eff') or 0):.1f}%，读 "
            f"{_bw97_n(rd, 'makespan'):,} / {_bw97_n(rd, 'bounds', 'bound'):,} "
            f"= {100 * float(rd.get('eff') or 0):.1f}%{tail}</li>")
    if not items:
        return "<p>4 tile 确认还在跑。</p>"
    return "<ul>" + "".join(items) + "</ul>"


def bw97_section(bw: dict) -> str:
    if not bw:
        return ""
    win = (bw.get("meta") or {}).get("winner") or "—"
    knobs = (bw.get("meta") or {}).get("winner_knobs") or {}
    wr = _bw97_rec(bw, win, "write")
    rd = _bw97_rec(bw, win, "read")
    ok_w = float(wr.get("eff") or 0) >= BW97_TARGET
    ok_r = float(rd.get("eff") or 0) >= BW97_TARGET
    if wr.get("full") or _bw97_rec(bw, "base", "write").get("full"):
        plot_bw97_done(bw, "base", IMG / "cc_done_bw97_base.png")
        if wr.get("full"):
            plot_bw97_done(bw, win, IMG / "cc_done_bw97.png")
        figs = """<img src="cc_done_bw97_base.png" alt="原 setup 各 group 完成曲线">
<img src="cc_done_bw97.png" alt="加宽 setup 各 group 完成曲线">"""
    else:
        figs = "<p>4 tile 确认曲线还在跑，下表是 1 tile 扫描。</p>"
    has_wide = any(c != "base" and _bw97_rec(bw, c, "write")
                   for c in (bw.get("grid") or {}))
    best_w = best_r = ""
    confirm = bw.get("confirm") or {}
    wr_best = rd_best = (0.0, "")
    for cfg, ops in confirm.items():
        if cfg == "base":
            continue
        w, r = (ops or {}).get("write") or {}, (ops or {}).get("read") or {}
        if w.get("makespan") and float(w.get("eff") or 0) > wr_best[0]:
            wr_best = (float(w["eff"]), cfg)
        if r.get("makespan") and float(r.get("eff") or 0) > rd_best[0]:
            rd_best = (float(r["eff"]), cfg)
    if wr_best[1]:
        best_w = (f"单侧写最好是 <code>{wr_best[1]}</code> "
                  f"{100 * wr_best[0]:.1f}%")
    if rd_best[1]:
        best_r = (f"单侧读最好是 <code>{rd_best[1]}</code> "
                  f"{100 * rd_best[0]:.1f}%")
    side = "；".join(x for x in (best_w, best_r) if x)
    if ok_w and ok_r and win != "base":
        verdict = "读写都到了 97% 以上"
    elif not has_wide:
        verdict = "1 tile 扫描没有组合同时过 97%（写被握手相对下界卡住）；4 tile 确认在跑"
    else:
        verdict = ("还没两边都到 97%，★ 是目前最差一侧达成率最高的加宽组合"
                   + (f"。{side}" if side else ""))
    pareto = bw97_oc_pareto_table(bw)
    shift = bw97_bound_shift_note(bw)
    extra = ""
    if pareto:
        extra += f"""<h3>7.4　outstanding 对冲</h3>
<p>下界不动时（横/纵/top 仍是 1，D2D 和 bridge 已×2），写要<b>低</b>
outstanding，读要<b>高</b> outstanding。97% 对应写 makespan ≤ 31,703、
读 ≤ 34,256。读在 outstanding 320 已经跨过 97%；写在 80 附近封顶，
还差约 200 cycle，再往下开窗口（64）并不更快。没有一个窗口能让
两边同时 ≥ 97%。</p>
{pareto}"""
    if shift:
        extra += f"""<h3>7.5　加宽定界织物</h3>
{shift}"""
    return f"""<h2>7　加宽 setup：把读写达成率推过 97%</h2>
<p>§0–§6 的硬件一字未改。这一节<b>单独</b>换了一套加宽 setup，
FIFO 深度全部不动（转向 64 / D2D 128 / 落地 16 / 注入 12+8），
只动 outstanding、D2D 链路宽、bridge 上环宽、H↔V 转向宽，
以及必要时的横/纵/top / 注入 / 弹出宽。
目标是 S0 在同一套均匀写 / 均匀读上，对<b>该 setup 自己的</b>解析下界
达成率都 ≥ 97%。选中的配置是 <code>{win}</code>：{_bw97_knob_txt(knobs)}。
{verdict}。</p>
<h3>7.1　和 §0 差在哪</h3>
{bw97_setup_table(bw)}
<div class="def"><b>为什么不动 buffer、动带宽。</b>
写差的 8% 里握手只占约 2%，其余是落地口和注入口喂不饱独占的
<i>h:dat</i> 边；读差的 13% 里有 POS retry，但下界本身是四组叠在
同一条横环重段上。加深队列只堆库存，加宽 D2D / bridge / 横环才改
每拍能过的 flit 数。outstanding 是覆盖 413 cycle 写 RTT 的计分板，
不是加队列；写在窗口加大之后达成率反而掉，因为多余的在途 flit
堵在转向和 D2D 口。横环×2 之后 σ=1 的目的 hop 每拍能走 2 条 flit，
H↔V tap 仍是每站每拍 1 条，所以转向宽也要一起加。</div>
<h3>7.2　1 tile 扫描</h3>
{bw97_sweep_table(bw)}
<h3>7.3　4 tile 确认</h3>
{figs}
{bw97_confirm_note(bw)}
{bw97_final_table(bw)}
{extra}
"""


def _verdict(dw: float, dr: float) -> str:
    """Describe a makespan pair without dressing up sub-1% differences."""
    def one(d: float) -> str:
        return "持平" if abs(d) < NOISE else ("更快" if d < 0 else "更慢")
    w, r = one(dw), one(dr)
    if w == r == "持平":
        return "两个批次都在 1% 以内，等于没动"
    if w == r:
        return f"两个批次都{w}"
    return f"写{w}、读{r}"


def effect_text(b: dict, area: dict) -> str:
    rows = {r["scheme"]: r for r in (area.get("rows") or [])}
    items = []
    for s in schemes_present(b):
        if s == "s0":
            continue
        cost = (rows.get(s) or {}).get("cost_ff", 0)
        dw = makespan(b, "write", s) / max(1, makespan(b, "write", "s0")) - 1
        dr = makespan(b, "read", s) / max(1, makespan(b, "read", "s0")) - 1
        sr = spread(b, "read", s)
        s0r = spread(b, "read", "s0")
        fair = (f"读的组间倍差从 {s0r:.3f} 降到 <b>{sr:.3f}</b>"
                if sr < s0r - 0.02 else
                f"读的组间倍差仍是 {sr:.3f}，没有改善")
        items.append(
            f"<li><b>{label(b, s)}</b>（<code>{chosen(b, s)}</code>）："
            f"写 {dw * 100:+.1f}%、读 {dr * 100:+.1f}%，{_verdict(dw, dr)}；"
            f"{fair}；新增状态 <b>{cost:,}</b> FF 等效。</li>")
    return "<ul>" + "\n".join(items) + "</ul>"


def grant_table(b: dict) -> str:
    """Every confirmed receiver-grant configuration, both grains together.

    The two grains have to be read against each other at *matched fairness*,
    not at matched knob value: equalising the six groups is exactly what
    costs throughput, so a scheme that equalises less looks faster for a
    reason that has nothing to do with its granularity.
    """
    cf = b.get("confirm") or {}
    ct = int((b.get("meta") or {}).get("tiles") or 4)
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for r in cf.values():
        if r.get("scheme") in ("s16", "s16g") and r.get("tiles") == ct:
            acc.setdefault((r["scheme"], r["config"]), {})[r["op"]] = r
    rows = []
    for (s, cfg), per in sorted(acc.items(),
                                key=lambda kv: (kv[0][0] != "s16g",
                                                kv[0][1])):
        if len(per) < 2:
            continue
        rows.append([
            "per-group" if s == "s16g" else "per-core",
            f"<code>{cfg}</code>",
            "6" if s == "s16g" else "60",
            f"{per['write']['makespan']:,}",
            _f(per["write"].get("finish_spread"), 3),
            f"{per['read']['makespan']:,}",
            _f(per["read"].get("finish_spread"), 3),
        ])
    return _t(["仲裁粒度", "配置", "每 HA 表项", "写 makespan", "写 倍差",
               "读 makespan", "读 倍差"], rows)


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------

def _fairest(b: dict) -> dict[str, tuple[float, str, int, int]]:
    """Per grain, the confirmed config with the tightest read spread."""
    cf = b.get("confirm") or {}
    ct = int((b.get("meta") or {}).get("tiles") or 4)
    best: dict[str, tuple[float, str, int, int]] = {}
    for r in cf.values():
        if r.get("scheme") not in ("s16", "s16g") or r.get("tiles") != ct:
            continue
        if r.get("op") != "read":
            continue
        sp = r.get("finish_spread") or 0.0
        cur = best.get(r["scheme"])
        if cur is None or sp < cur[0]:
            w = cf.get(f"{r['scheme']}|{r['config']}|write|{ct}") or {}
            best[r["scheme"]] = (sp, r["config"], r["makespan"],
                                 w.get("makespan", 0))
    return best


def _matched_gain(b: dict) -> str:
    best = _fairest(b)
    g, c = best.get("s16g"), best.get("s16")
    if not g or not c or not c[2]:
        return "—"
    return f"{100 * (1 - g[2] / c[2]):.1f}%"


def matched_text(b: dict) -> str:
    """Compare the two grains at the closest thing to equal read fairness."""
    best = _fairest(b)
    g, c = best.get("s16g"), best.get("s16")
    if not g or not c:
        return ""
    return f"""<div class="def good">
<b>在同等公平下，per-group 严格更好。</b>
把两个粒度各自最公平的配置放在一起：per-group 的
<code>{g[1]}</code> 把读的组间倍差压到 {g[0]:.3f}，makespan
<b>{g[2]:,}</b>；per-core 的 <code>{c[1]}</code> 压到 {c[0]:.3f}，
makespan <b>{c[2]:,}</b>。<b>公平度相同，per-group 快
{100 * (1 - g[2] / max(1, c[2])):.1f}%</b>，写批次同样快
{100 * (1 - g[3] / max(1, c[3])):.1f}%，而每个 HA 的服务计数表只有
六分之一。原因很直接：per-core 要在 60 个类别之间轮转，
一个 HA 的授权窗口被切成 60 份，谁都拿不到足够的并发把自己的
纵环喂满；per-group 只切成 6 份，组内仍是先到先服务。</div>"""


def build(b: dict, area: dict, rc: dict, bw97: dict | None = None) -> str:
    t, m = b["topology"], b["meta"]
    ss = schemes_present(b)
    n_txn = m.get("n_txn", 0)
    k_core = m.get("txn_per_core", 0)
    burst = m.get("burst_len", 128)
    stride = m.get("stride", 4096)
    tile = m.get("tiling_size", 65536) // 1024
    st = m.get("sweep_tiles", 1)
    ct = m.get("tiles", 4)

    # Numbers the conclusions quote. Read them from the data rather than
    # from the draft, so a rerun cannot leave the prose behind.
    rd_rows = (((rc.get("ops") or {}).get("read")) or {}).get("groups") or []
    crit = [r["crit_load"] for r in rd_rows] or [0, 0]
    rc_hi, rc_lo = f"{max(crit):,}", f"{min(crit):,}"
    rc_gap = f"{100 * (max(crit) / max(1, min(crit)) - 1):.0f}%"
    d2d_sp = "{:.3f}".format(
        max((max(r["d2d"]["up"]["spread"], r["d2d"]["down"]["spread"])
             for r in rd_rows), default=1.0))
    sp_w0 = f"{spread(b, 'write', 's0'):.3f}"
    sp_r0 = f"{spread(b, 'read', 's0'):.3f}"
    sp_r1 = f"{spread(b, 'read', 's1'):.3f}"
    sp_r22 = f"{spread(b, 'read', 's22'):.3f}"
    sp_r16g = f"{spread(b, 'read', 's16g'):.3f}"

    def _eff(op: str) -> str:
        lb = bounds(b, op).get("bound", 0)
        return f"{100 * lb / max(1, makespan(b, op, 's0')):.0f}%"

    eff_w0, eff_r0 = _eff("write"), _eff("read")
    r0, r16g = makespan(b, "read", "s0"), makespan(b, "read", "s16g")
    d_r16g = f"{100 * (1 - r16g / max(1, r0)):.1f}%"
    cost = {r["scheme"]: r["cost_ff"] for r in (area.get("rows") or [])}
    c16g = max(1, cost.get("s16g", 0))
    cost_16g = f"{cost.get('s16g', 0):,}"
    cost_ratio = f"{cost.get('s16', 0) / c16g:.0f}"
    cost_ratio_s1 = f"{cost.get('s1', 0) / c16g:.0f}"
    matched_gain = _matched_gain(b)

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
最慢那条线的右端就是 <b>makespan</b>，这是全文比较效果的主指标；
灰带宽度是最快组与最慢组完成时刻之差，用“最慢/最快”的倍差记，
它回答的是各 group 带宽差异那个问题。两个指标必须一起看：
把六个 group 拉齐本身就要压住跑得快的组，只看其中一个会得出相反的结论。</div>

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
把 {len(ss) - 1} 个方案的全部 {sum(len(g) for k, g in (b.get('grid') or {}).items())}
组配置都按这个规模跑一遍不现实。所以分两阶段：</p>
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
<h3>5.1　{label(b, "s16g")}</h3>
{sec.get("s16g", "")}
<h3>5.2　{label(b, "s16")}：作为对照的原粒度</h3>
{sec.get("s16", "")}
<h3>5.3　两种粒度必须在“同等公平”下比</h3>
<p>目的端授权是<b>唯一会主动扣住授权</b>的方案，所以它天然存在
公平与吞吐的取舍：越是把六个 group 拉齐，就越要压住本来能跑快的组。
只比 makespan 会奖励“拉得不够齐”的配置，所以下表把两个粒度的
全部确认配置放在一起，让公平度和 makespan 同时可见。</p>
{grant_table(b)}
{matched_text(b)}

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
<li><b>读的组间差异是拓扑造成的，不是调度造成的。</b>
六个 group 的工作量只差 4%，各自八条 D2D 上下行也基本均衡（倍差 {d2d_sp}），
但各自路径上最热链路的负载分成 {rc_hi} 和 {rc_lo} 两档（差 {rc_gap}），
与实测完成顺序同向同量级。写批次没有这一档差距，
所以写本来就齐（倍差 {sp_w0}），读本来就不齐（倍差 {sp_r0}）。</li>
<li><b>这块织物的 makespan 余量本来就不到一成，源端方案分不到。</b>
S0 已经跑到写下界的 {eff_w0}、读下界的 {eff_r0}。
S1 与 S22 都只动源端：S1 少发一点，S22 让行加绕行，
两者对 makespan 的影响都在 ±1% 以内，对读的组间倍差<b>完全没有改善</b>
（S1 {sp_r1}、S22 {sp_r22}，S0 是 {sp_r0}）。
瓶颈是一条<b>过境</b>链路上别人的 flit，源端排序改不了它要驮的总量。</li>
<li><b>S22 的组粒度在这个 fabric 上结构性失效，这是拓扑的结论而不是调参的结论。</b>
一个 top 环恰好就是一个 group，组间在环上永远不相遇，让行的判定恒为假；
唯一同时握有多个 group 流量的注入口在 HA，而 HA 的注入队列几乎总是空的
（96 个 HA 摊 122,880 笔），没有东西可重排。
S22 “绝不扣住空槽”的原则，正是它在这里帮不上忙的原因——
要缓解一条过境热链路，必须有人真的少发。</li>
<li><b>目的端授权是唯一真的动了读的方案，而 per-group 是它该有的粒度。</b>
S16G 把读 makespan 降了 {d_r16g}，同时把组间倍差从 {sp_r0} 压到 {sp_r16g}；
在同等公平度下它比 per-core 快 {matched_gain}。
面积上它也是全场最便宜的：{cost_16g} FF 等效，
只有 per-core 的 1/{cost_ratio}、S1 的 1/{cost_ratio_s1}，
因为仲裁轴从 60 项缩到 6 项，而授权本身仍然搭在既有的
DBIDResp / CompData 上，不加报文、不加总线、不加缓存。</li>
<li><b>要再往下就得改绑定或改路由。</b>
读的结构下界 {rc_hi} 是四个 group 共用一条横环重段的结果，
而这条重段是“HA 按列绑定到 D2D bridge”这条硬件规则的直接后果。
任何拥塞控制都只能在这条下界之上分配，消不掉它。</li>
</ol>
</div>

{bw97_section(bw97 or {})}
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
    bw97 = json.loads(BW97.read_text()) if BW97.exists() else {}
    OUT.write_text(build(b, area, rc, bw97))
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
