#!/usr/bin/env python3
"""HTML report: per-core write bandwidth fairness on the 3D-stacked fabric.

Six top-die full rings, 48 D2D links, and a bottom die of 6 horizontal + 8
vertical unidirectional half rings serving 96 HAs. Same question order as the
single-ring report: conclusions first, then topology and hardware setup with
link delays, Jain, bounds, the phenomenon, the root cause, S1, the improved
scheme, and cost.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "results" / "dse_stack_write_fair.json"
OC_DATA = ROOT / "results" / "dse_stack_oc_seeds.json"
S17_DATA = ROOT / "results" / "dse_stack_s17_seeds.json"
OUT = ROOT / "results" / "report_ring2_stack_write_fairness.html"
IMG = ROOT / "results"

SCHEMES = ("s0", "s1", "s16", "s17")
COLOR = {"s0": "#dc2626", "s1": "#f59e0b", "s16": "#2563eb", "s17": "#16a34a"}
LABEL = {"s0": "S0 基线（无流控）", "s1": "S1 拥塞等级 AIMD",
         "s16": "S16 接收端授权（Homa 式）",
         "s17": "S17 挂接点转向仲裁（本文提出）"}
DIE_COLOR = ["#1d4ed8", "#dc2626", "#0891b2", "#ea580c", "#4338ca", "#b91c1c"]


def _cjk() -> None:
    from matplotlib import font_manager as fm
    for f in fm.fontManager.ttflist:
        n = f.name.lower()
        if any(w in n for w in ("micro hei", "cjk", "noto sans sc",
                                "source han sans")):
            plt.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def _t(headers: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = ["<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
            for r in rows]
    return (f"<table><thead><tr>{th}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def _f(x, nd=4, dash="—"):
    if x is None:
        return dash
    if isinstance(x, float) and x == float("inf"):
        return "∞"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _ok(b: bool) -> str:
    return ('<b style="color:#16a34a">完成</b>' if b
            else '<b style="color:#dc2626">拥塞崩溃</b>')


# ---------------------------------------------------------------------------
# plots
# ---------------------------------------------------------------------------


def plot_topology(b: dict, path: Path) -> None:
    """Bottom-die grid with the 48 attach points and the 6 H-ring gaps."""
    _cjk()
    fig, ax = plt.subplots(figsize=(10.4, 6.6))
    rows = b["v_profile"]["rows"]
    ncol = b["topology"]["n_cols"]
    y = 0
    ylab = []
    for r in rows[::-1]:
        for c in range(ncol):
            if r["role"] == "ha":
                ax.add_patch(plt.Rectangle((c - 0.36, y - 0.3), 0.72, 0.6,
                                           fc="#e2e8f0", ec="#94a3b8",
                                           lw=0.7))
            else:
                ax.add_patch(plt.Rectangle((c - 0.42, y - 0.32), 0.84, 0.64,
                                           fc=DIE_COLOR[r["die"] % 6],
                                           ec="#111", lw=0.9, alpha=0.88))
                ax.text(c, y, str(r["die"]), ha="center", va="center",
                        color="white", fontsize=7.5, fontweight="bold")
        tag = "" if r["role"] == "ha" else \
            ("← 每对的第一个" if r["die"] % 2 == 0 else "← 第二个（下游）")
        ylab.append((y, f"v{r['vpos']:<2d} {r['label']}", tag,
                     r["die"] if r["role"] == "attach" else None))
        y += 1
    for yy, lab, tag, die in ylab:
        ax.text(-1.9, yy, lab, ha="left", va="center", fontsize=7.4,
                color="#334155")
        if tag:
            ax.text(8.05, yy, tag, ha="left", va="center", fontsize=7.4,
                    color=DIE_COLOR[die % 6], fontweight="bold")
    ax.annotate("", xy=(10.5, -0.4), xytext=(10.5, y - 0.6),
                arrowprops=dict(arrowstyle="-|>", color="#0f766e", lw=1.6))
    ax.text(10.7, (y - 1) / 2, "纵向 half ring 单向行进方向", rotation=90,
            va="center", fontsize=8, color="#0f766e")
    ax.set_xlim(-2.0, 11.3)
    ax.set_ylim(-0.9, y - 0.2)
    ax.set_xticks(range(ncol))
    ax.set_xticklabels([f"col {c}" for c in range(ncol)], fontsize=8)
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("bottom die：96 HA（灰）+ 48 个挂接点（彩色＝所属 top die）\n"
                 "每列一条 18 节点纵向 half ring；挂接点成对相邻，"
                 "第二个永远在第一个的正下游",
                 fontsize=9.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_v_profile(b: dict, path: Path) -> None:
    """Analytic per-edge load along one vertical half ring."""
    _cjk()
    rows = b["v_profile"]["rows"]
    xs = [r["vpos"] for r in rows]
    fig, ax = plt.subplots(figsize=(9.4, 4.0))
    top = max(r["dat"] for r in rows)
    ax.bar([x - 0.2 for x in xs], [r["dat"] for r in rows], width=0.4,
           label="DAT（WriteData，4 flit/笔）", color="#2563eb")
    ax.bar([x + 0.2 for x in xs], [r["rsp"] for r in rows], width=0.4,
           label="RSP（DBIDResp+Comp）", color="#f59e0b")
    for r in rows:
        if r["role"] == "attach":
            ax.axvline(r["vpos"], color=DIE_COLOR[r["die"] % 6], lw=1.0,
                       ls=":", alpha=0.85)
            ax.text(r["vpos"], r["dat"] + top * 0.035,
                    f"die{r['die']}\n{r['dat']}", ha="center", fontsize=7.2,
                    color=DIE_COLOR[r["die"] % 6], fontweight="bold")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{r['vpos']}\n{r['label']}" for r in rows],
                       fontsize=6.6)
    ax.set_ylim(0, top * 1.28)
    ax.set_xlabel("纵向 half ring 上的位置（该点出边 vpos→vpos+1 的负载）")
    ax.set_ylabel("相对流量单位")
    ax.set_title("成对挂接点的注入边负载相差恰好 48 = 12 HA × 4 flit"
                 "（一个 die 的完整写数据输出）", fontsize=10)
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_bw_by_die(b: dict, path: Path) -> None:
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.0))
    for ax, route in zip(axes, ("lat", "dor")):
        rc = b["root_cause"][route]
        dies = sorted(rc["by_die"], key=int)
        die_of = {r["core"]: r["die"] for r in rc["rows"]}
        n = len(SCHEMES)
        w = 0.82 / n
        for i, s in enumerate(SCHEMES):
            sc = b["schemes"][route].get(s)
            if not sc:
                continue
            per: dict[int, list[float]] = {}
            for c, v in sc["fairness"]["bw_by_core"].items():
                d = die_of.get(int(c))
                if d is not None:
                    per.setdefault(d, []).append(float(v))
            # Normalised to each run's own mean. Absolute bandwidth is not
            # comparable across schemes because the fairness window t_fair
            # differs; the share is what Jain actually measures.
            mean = sc["fairness"]["bw_mean"] or 1.0
            vals = [(sum(per[int(d)]) / len(per[int(d)]) / mean)
                    if per.get(int(d)) else 0.0 for d in dies]
            ax.bar([j + (i - (n - 1) / 2) * w for j in range(len(dies))],
                   vals, width=w, label=LABEL[s], color=COLOR[s])
        ax.axhline(1.0, color="#111", lw=1.0, ls="--")
        ax.text(len(dies) - 0.45, 1.02, "公平线", fontsize=7.5, ha="right")
        ax.set_xticks(range(len(dies)))
        ax.set_xticklabels(
            [f"die {d}\nv{rc['by_die'][d]['vpos']}"
             + ("\n第一个" if int(d) % 2 == 0 else "\n第二个")
             for d in dies], fontsize=7.2)
        ax.set_ylabel("每核带宽 / 该方案自身均值")
        ax.set_title(b["meta"]["route_label"][route], fontsize=10)
        ax.grid(axis="y", alpha=0.3)
    axes[0].legend(fontsize=7)
    fig.suptitle("每核写带宽份额按 top die 分组："
                 "不是按 die 编号单调，而是“第一个 / 第二个”的成对锯齿",
                 fontsize=10.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_scatter(b: dict, path: Path) -> None:
    """Per-core bandwidth against the load on its own injection edge."""
    _cjk()
    rc = b["root_cause"]["dor"]
    fig, ax = plt.subplots(figsize=(9.0, 4.4))
    for d in range(b["topology"]["n_die"]):
        rows = [r for r in rc["rows"] if r["die"] == d]
        if not rows:
            continue
        ax.scatter([r["inj_dat"] for r in rows], [r["bw"] for r in rows],
                   s=34, color=DIE_COLOR[d % 6], alpha=0.85,
                   marker="o" if d % 2 == 0 else "^",
                   label=f"die {d} (v{rows[0]['vpos']}, "
                         + ("第一个" if d % 2 == 0 else "第二个") + ")")
    # die 0 and die 5 share the same injection load but not the same rank in
    # their pair: the gap between them is the pair effect on its own.
    tie = [r for r in rc["rows"] if r["die"] in (0, 5)]
    if tie:
        x = tie[0]["inj_dat"]
        hi = max(r["bw"] for r in tie if r["die"] == 0)
        lo = min(r["bw"] for r in tie if r["die"] == 5)
        ax.annotate("", xy=(x + 3, hi), xytext=(x + 3, lo),
                    arrowprops=dict(arrowstyle="<->", color="#111", lw=1.2))
        ax.text(x + 5, (hi + lo) / 2,
                "同样的注入边负载 168，\ndie 0（第一个）远高于 die 5（第二个）\n"
                "——成对次序本身就是一个独立因素",
                fontsize=7.6, va="center", color="#111")
    ax.set_xlabel("该 die 挂接点自己那条纵向出边上的 DAT 负载（解析值）")
    ax.set_ylabel("每 core 写带宽 (flit/cycle)")
    ax.set_title(f"Spearman(注入边负载, 带宽) = {rc['corr']['inj_dat']}，"
                 f"而 Spearman(die 编号, 带宽) = {rc['corr']['die']}",
                 fontsize=10)
    ax.legend(fontsize=7.5, loc="lower left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_sweeps(b: dict, oc: list, path: Path) -> None:
    _cjk()
    fig, axes = plt.subplots(1, 3, figsize=(13.0, 3.8))

    ax = axes[0]
    for route, c in (("lat", "#dc2626"), ("dor", "#16a34a")):
        rows = [r for r in b["fifo_sweep"] if r["route"] == route]
        ax.plot([r["turn_depth"] for r in rows], [r["thr"] for r in rows],
                "o-", color=c, label=b["meta"]["route_label"][route])
    ax.set_xscale("log", base=2)
    ax.set_xlabel("转向 FIFO 深度 (flit)")
    ax.set_ylabel("吞吐 (txn/cycle)")
    ax.set_title("① FIFO 深度：维序路由下几乎是平的", fontsize=9.5)
    ax.legend(fontsize=7.5)
    ax.grid(alpha=0.3)

    ax = axes[1]
    for route, c in (("lat", "#dc2626"), ("dor", "#16a34a")):
        rows = [r for r in b["oc_sweep"] if r["route"] == route]
        ax.plot([r["outstanding"] for r in rows], [r["thr"] for r in rows],
                "o-", color=c, label=b["meta"]["route_label"][route])
    ax.set_xscale("log", base=2)
    ax.set_xlabel("每 core outstanding 上限")
    ax.set_ylabel("吞吐 (txn/cycle)")
    ax.set_title("② 并发度：最短路径有崩溃悬崖", fontsize=9.5)
    ax.legend(fontsize=7.5)
    ax.grid(alpha=0.3)

    ax = axes[2]
    s0 = [r for r in oc if r["scheme"] == "s0"]
    ax.plot([r["oc"] for r in s0], [r["eff_mean"] for r in s0], "o-",
            color="#2563eb", label="对下界效率（均值）")
    ax.plot([r["oc"] for r in s0], [r["jain_mean"] for r in s0], "s-",
            color="#16a34a", label="Jain（均值）")
    best = max(s0, key=lambda r: (r["jain_mean"], r["eff_mean"]))
    ax.axvline(best["oc"], color="#111", ls=":", lw=1.2)
    ax.annotate(f"oc={best['oc']}：吞吐与公平同时最优",
                xy=(best["oc"], best["eff_mean"]),
                xytext=(best["oc"] * 1.25, 0.875), fontsize=7.5,
                ha="left", arrowprops=dict(arrowstyle="->", lw=0.9))
    ax.set_xscale("log", base=2)
    ax.set_xlabel("每 core outstanding 上限（维序路由，3 个种子均值）")
    ax.set_title("③ 并发度存在最优点，不只是悬崖", fontsize=9.5)
    ax.legend(fontsize=7.5, loc="lower right")
    ax.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_bw_sorted(b: dict, path: Path) -> None:
    _cjk()
    fig, ax = plt.subplots(figsize=(9.4, 4.0))
    for s in SCHEMES:
        sc = b["schemes"]["dor"].get(s)
        if not sc:
            continue
        vals = sorted(float(v) for v in sc["fairness"]["bw_by_core"].values())
        ax.plot(range(len(vals)), vals, "-", color=COLOR[s], lw=1.7,
                label=f"{LABEL[s]}  (Jain {sc['fairness']['jain']}, "
                      f"max/min {sc['fairness']['max_min']})")
    ax.axvspan(0, 29, color="#fecaca", alpha=0.25)
    ax.text(0.24, 0.04, "慢的一半：几乎全是“每对的第二个”挂接点所属的 die",
            transform=ax.transAxes, ha="center", fontsize=8, color="#7f1d1d")
    ax.set_xlabel("60 个 AI core，按带宽升序排列")
    ax.set_ylabel("写带宽 (flit/cycle)")
    ax.set_title("维序路由、outstanding=128 下每核写带宽分布（越平越公平）",
                 fontsize=10)
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


def setup_table(b: dict) -> str:
    t, m = b["topology"], b["meta"]
    f = m["fabric"]
    cap = t["capacity"]
    rows = [
        ["top die 数量", t["n_die"], "每个是 20 节点、双平面、双向 full ring"],
        ["AI core", t["n_cores"], "每 top die 10 个，环序号 0,2,…,18"],
        ["D2D bridge", t["n_bridges"],
         f"每 top die 8 个，环序号 {t['top_bridges']}，依次对应 bottom die 第 0–7 列"],
        ["非终端节点", t["n_die"] * 2,
         "每 top die 的 9、19：只转发，不发起也不接收"],
        ["HA（内存）", t["n_has"], "bottom die，12 行 × 8 列，全部在纵向 half ring 上"],
        ["挂接点", t["n_attach"],
         "6 条横向 half ring × 8 列；既在横环上也在该列纵环上，兼作转向点与 D2D 落地点"],
        ["节点总数", t["n_nodes"],
         f"{t['n_die']}×20 + {t['n_has']} + {t['n_attach']}；"
         f"8 列 × {t['v_len']} = 144 = 96 + 48，自洽"],
        ["有向链路", t["directed_links"],
         f"top {cap['top']} / D2D {cap['d2d']} / 横 {cap['h']} / 纵 {cap['v']}"],
        ["CHI VC", " / ".join(v.upper() for v in t["vcs"]),
         "三条独立 VC；每条有向链路每 VC 每拍 1 flit（σ=1）"],
        ["每 core outstanding", m["core_outstanding"],
         "REQ 已注入、Comp 未回收的事务数上限（题目规定 128；见 §4.2，这个取值并非最优）"],
        ["写事务",
         f"REQ×{m['m_req']} → DBIDResp×1 → WriteData×{m['m_wdata']} → Comp×1",
         "CHI WriteNoSnp 四拍握手"],
        ["每 core 事务数", m["k"],
         f"均匀散布到全部 {t['n_has']} 个 HA，共 {m['k'] * t['n_cores']:,} 笔"],
        ["转向 FIFO 深度", f["turn_depth"],
         "横↔纵换环时经过；见 §4.1——维序路由下 4 flit 即够，此处取大值只为留出余量"],
        ["D2D FIFO 深度", f["d2d_depth"], "跨 die 时经过"],
        ["上下环端口", "1 / 站点 / 平面",
         f"注入队列 {f['inj_depth']} 深，落地队列 {f['eject_depth']} 深，"
         f"PE 每拍取 {f['eject_bw']} flit"],
    ]
    return _t(["项目", "取值", "说明"], rows)


def link_table(b: dict) -> str:
    t = b["topology"]
    lats = t["top_link_lats"]
    rows = []
    for i in range(20):
        j = (i + 1) % 20
        role = "含非终端 9/19" if i in (8, 9, 18, 19) else \
            ("core→bridge" if i % 2 == 0 else "bridge→core")
        rows.append([f"{i} ↔ {j}", lats[i], role])
    body = _t(["top die 环内链路", "延迟 (cycle)", "两端角色"], rows)
    extra = _t(["其他链路", "延迟 (cycle)", "说明"], [
        ["bottom die 横向 half ring", t["bot_hop_lat"],
         "单向，8 个挂接点成一个闭环"],
        ["bottom die 纵向 half ring", t["bot_hop_lat"],
         f"单向，{t['v_len']} 个节点成一个闭环（12 HA + 6 挂接点）"],
        ["D2D 跨 die", t["d2d_lat"], "SerDes + 跨时钟域，双向各一条"],
    ])
    return body + extra


def routing_table(b: dict) -> str:
    rows = []
    for m in ("lat", "hops", "dor"):
        r = b["routing"][m]
        d = r["dat_hops_per_txn"]
        rows.append([
            b["meta"]["route_label"][m], r["bounds"]["bound"],
            _f(r["max_txn_per_cycle"], 3), _f(r["mean_fwd_hops"], 2),
            d.get("v", "—"), d.get("h", 0), d.get("top", "—"),
            f"{r['v_dat_max']} / {r['v_dat_mean']}",
            f"<b>{_f(r['v_concentration'], 2)}×</b>",
        ])
    return _t(["路由策略", "下界 (cycle)", "上限 txn/cycle", "平均跳数",
               "纵环 DAT 跳/笔", "横环 DAT 跳/笔", "top 环 DAT 跳/笔",
               "纵环单链路 DAT 最大/平均", "集中度"], rows)


def hot_table(b: dict) -> str:
    rows = []
    for route in ("lat", "dor"):
        for r in b["hot_edges"][route][:3]:
            rows.append([b["meta"]["route_label"][route], r["fabric"],
                         r["vc"].upper(), f"{r['src']} → {r['dst']}",
                         f"{r['flits']:,}"])
    return _t(["路由", "所属织物", "VC", "链路", "该链路 flit 数"], rows)


def bounds_table(bd: dict) -> str:
    fl = bd["fabric_lb"]
    rows = [
        ["LB_link", bd["link_lb"],
         "最忙的那条有向链路、单条 VC 的容量（DAT "
         f"{bd['link_by_vc']['dat']} / RSP {bd['link_by_vc']['rsp']} / "
         f"REQ {bd['link_by_vc']['req']}，取最大）"],
        ["LB_port", bd["port_lb"], "每站点一个上下环端口，三条 VC 相加共享"],
        ["LB_fabric", bd["cut_lb"],
         "某层织物的总 flit·跳 ÷ 该层链路数（"
         + "，".join(f"{k} {v}" for k, v in fl.items()) + "）"],
        ["LB_txn", bd["txn_lb"], "单笔事务四拍握手的串行链"],
        ["<b>下界</b>", f"<b>{bd['bound']}</b>", "以上四者取最大"],
    ]
    return _t(["下界", "cycle", "含义"], rows)


def scheme_table(b: dict, route: str) -> str:
    per = b["schemes"][route]
    bound = per["bounds"]["bound"]
    rows = []
    for s in SCHEMES:
        sc = per.get(s)
        if not sc:
            continue
        f = sc["fairness"]
        rows.append([
            LABEL[s], _ok(sc["completed"]),
            f"{sc['n_txn_done']:,}/{per['n_txn']:,}", sc["makespan"],
            f"{100 * bound / max(1, sc['makespan']):.0f}%",
            f"<b>{_f(f.get('jain'))}</b>", _f(f.get("max_min"), 2),
            _f(f.get("cov"), 3),
            f"{_f(f.get('bw_min'))} ~ {_f(f.get('bw_max'))}",
            f"{sc['n_deflections']:,}",
        ])
    return _t(["方案", "状态", "完成事务", "makespan", "对下界效率", "Jain",
               "max/min", "CoV", "每核带宽区间", "偏转次数"], rows)


def die_table(b: dict, route: str) -> str:
    rc = b["root_cause"][route]
    rows = []
    for d in sorted(rc["by_die"], key=int):
        r = rc["by_die"][d]
        rows.append([f"die {d}", r["vpos"], r["inj_dat"],
                     "第一个" if int(d) % 2 == 0 else "<b>第二个（下游）</b>",
                     _f(r["mean"]), _f(r["min"]), _f(r["max"])])
    return _t(["top die", "挂接点 vpos", "注入边 DAT 负载（解析）",
               "行间隙内次序", "平均带宽", "最小", "最大"], rows)


def corr_table(b: dict, route: str) -> str:
    c = b["root_cause"][route]["corr"]
    rows = [
        ["行间隙内是第几个挂接点", f"<b>{_f(c['pair'], 3)}</b>",
         "<b>主因</b>：第二个永远在第一个的正下游"],
        ["挂接点出边的 DAT 负载", f"<b>{_f(c['inj_dat'], 3)}</b>",
         "<b>主因的物理量</b>：注入时必须挤进这条边"],
        ["行间隙编号（0/1/2）", _f(c["gap"], 3),
         "次要：越靠下游越好，上游 HA 已经吸收了一部分流量"],
        ["core 在 top 环上的序号", _f(c["top_idx"], 3),
         "几乎无关——top 环容量过剩，片内位置不构成瓶颈"],
        ["top die 编号", _f(c["die"], 3),
         "<b>≈0，计划中的假设被否定</b>"],
        ["纵环插入位置 vpos", _f(c["vpos"], 3),
         "≈0，与 die 编号同序，单调性被成对结构打断"],
    ]
    return _t(["候选解释变量", "Spearman(变量, 每核带宽)", "读法"], rows)


def fifo_table(b: dict) -> str:
    rows = []
    for r in b["fifo_sweep"]:
        rows.append([b["meta"]["route_label"][r["route"]], r["turn_depth"],
                     r["d2d_depth"], _ok(r["completed"]),
                     f"{r['n_txn_done']:,}", r["makespan"], _f(r["thr"], 3),
                     f"{r['n_deflections']:,}", r["turn_peak"]])
    return _t(["路由", "转向 FIFO 深度", "D2D FIFO 深度", "状态", "完成事务",
               "makespan", "吞吐 txn/cycle", "偏转次数", "实测峰值占用"], rows)


def oc_table(b: dict) -> str:
    rows = []
    for r in b["oc_sweep"]:
        rows.append([b["meta"]["route_label"][r["route"]], r["outstanding"],
                     _ok(r["completed"]), r["makespan"],
                     f"{100 * r['eff']:.0f}%", _f(r["thr"], 3),
                     _f(r["jain"]), _f(r["max_min"], 2),
                     f"{r['n_deflections']:,}"])
    return _t(["路由", "每 core outstanding", "状态", "makespan", "对下界效率",
               "吞吐 txn/cycle", "Jain", "max/min", "偏转次数"], rows)


def oc_seed_table(oc: list) -> str:
    rows = []
    for r in oc:
        star = " ★" if r["oc"] == 32 else ""
        rows.append([("S0 基线" if r["scheme"] == "s0" else "S1 AIMD")
                     + f"，oc={r['oc']}{star}",
                     _f(r["jain_min"]), _f(r["jain_mean"]),
                     _f(r["mm_worst"], 3),
                     f"{100 * r['eff_min']:.1f}%",
                     f"{100 * r['eff_mean']:.1f}%"])
    return _t(["配置", "最差 Jain", "平均 Jain", "最差 max/min",
               "最差效率", "平均效率"], rows)


def s16_table(b: dict) -> str:
    rows = []
    for r in b["s16_sweep"]:
        rows.append([r["overcommit"], _ok(r["completed"]), r["makespan"],
                     _f(r["jain"]), _f(r["max_min"], 2), _f(r["cov"], 3),
                     r["peak_grants"], r["peak_buf_flits"],
                     _f(r.get("grant_delay_mean"), 1)])
    return _t(["overcommit", "状态", "makespan", "Jain", "max/min", "CoV",
               "峰值挂起授权", "峰值写缓冲 (flit)", "平均授权等待"], rows)


def s17_table(b: dict) -> str:
    rows = []
    for r in b["s17_sweep"]:
        rows.append([r["patience"] or "0（等同基线）", _ok(r["completed"]),
                     r["makespan"], _f(r["jain"]), _f(r["max_min"], 2),
                     _f(r["cov"], 3), f"{r['n_turn_yield']:,}",
                     f"{r['n_turn_win']:,}", r["latch_flits"]])
    return _t(["耐心阈值 (cycle)", "状态", "makespan", "Jain", "max/min",
               "CoV", "让位次数", "让位成功", "闩存深度 (flit)"], rows)


def s17_seed_table(s17: dict) -> str:
    """S17 against the baseline at both concurrency settings, 3 seeds each."""
    rows = []
    for oc in (128, 32):
        base = s17.get(f"oc{oc}_S0")
        for name, d in ((k.split("_", 1)[1], v) for k, v in s17.items()
                        if k.startswith(f"oc{oc}_")):
            if base and d is not base:
                dj = d["jain_mean"] - base["jain_mean"]
                dm = d["mm_worst"] - base["mm_worst"]
                verdict = ("<b style='color:#16a34a'>略优</b>"
                           if dj > 0.002 and dm <= 0 else
                           "<b style='color:#dc2626'>更差</b>"
                           if dj < -0.002 else "持平（噪声内）")
                delta = f"{dj:+.5f} / {dm:+.3f}"
            else:
                verdict, delta = "—（基准）", "—"
            rows.append([f"oc={oc}，{name}", _f(d["jain_mean"], 5),
                         _f(d["jain_min"], 5), _f(d["mm_worst"], 3),
                         _f(d["cov_mean"], 4), d["t_mean"], delta, verdict])
    return _t(["配置", "平均 Jain", "最差 Jain", "最差 max/min", "平均 CoV",
               "平均 makespan", "ΔJain / Δmax-min", "判定"], rows)


def seed_table(b: dict, key: str) -> str:
    rows = []
    for s in SCHEMES:
        d = b[key].get(s)
        if not d:
            continue
        rows.append([LABEL[s], f"{d['n_completed']}/{d['n_runs']}",
                     _f(d["jain_min"]), _f(d["jain_mean"]),
                     _f(d["max_min_worst"], 3),
                     f"{100 * d['eff_min']:.1f}%"])
    return _t(["方案", "完成/总次数", "最差 Jain", "平均 Jain",
               "最差 max/min", "最差效率"], rows)


def cost_table(b: dict) -> str:
    per = b["schemes"]["dor"]
    s1 = per["s1"].get("fc", {})
    s16 = per["s16"].get("fc", {})
    s17 = per["s17"].get("fc", {})
    rows = [
        ["专用广播总线", "无", "无",
         f"<b>有</b>（{s1.get('bus_posts', 0):,} 次广播，"
         f"{s1.get('bus_bits', 0):,} bit）", "无", "无"],
        ["新增报文类型", "无", "无", "无（走专用总线）",
         "无（复用 DBIDResp 的时机）", "无"],
        ["每站点状态", "无", "无（只改一个已有寄存器的取值）",
         "3bit×2 等级 + 受控节点表（平均 "
         f"{s1.get('mean_path_nodes', 0)} 项）+ 预算寄存器",
         "每 HA：授权计数 + 每请求方已服务计数",
         f"每挂接点每出环 1 个计数器（共 {s17.get('n_counters', 0)} 个）"],
        ["额外缓冲", "无", "无", "无",
         f"完成端写缓冲，峰值 {s16.get('peak_buf_flits', 0)} flit",
         f"每出边 {s17.get('latch_flits', 0)} flit 闩存"],
        ["是否破坏链路无缓存性", "否", "否", "否", "否",
         f"是，但有界（实测 {s17.get('max_inring_hold', 0)} flit）"],
        ["本拓扑实测收益（3 种子）", "—",
         "<b>吞吐与公平同时改善</b>",
         "公平小幅但可靠改善，吞吐持平",
         "<b>无效</b>（预算几乎从不绑定）",
         "<b>无效</b>（噪声内，max/min 更差）"],
    ]
    return _t(["代价项", "S0", "outstanding=32", "S1", "S16", "S17"], rows)


# ---------------------------------------------------------------------------

def main() -> None:
    b = json.loads(DATA.read_text())
    oc = json.loads(OC_DATA.read_text()) if OC_DATA.exists() else []
    s17 = json.loads(S17_DATA.read_text()) if S17_DATA.exists() else {}
    IMG.mkdir(parents=True, exist_ok=True)
    p_topo, p_vprof = IMG / "stack_topology.png", IMG / "stack_v_profile.png"
    p_die, p_sc = IMG / "stack_bw_by_die.png", IMG / "stack_scatter.png"
    p_sw, p_srt = IMG / "stack_sweeps.png", IMG / "stack_bw_sorted.png"
    plot_topology(b, p_topo)
    plot_v_profile(b, p_vprof)
    plot_bw_by_die(b, p_die)
    plot_scatter(b, p_sc)
    plot_sweeps(b, oc, p_sw)
    plot_bw_sorted(b, p_srt)

    m, t = b["meta"], b["topology"]
    lat, dor = b["schemes"]["lat"], b["schemes"]["dor"]
    l0, d0 = lat["s0"], dor["s0"]
    d1, d16, d17 = dor["s1"], dor["s16"], dor["s17"]
    lf0, df0 = l0["fairness"], d0["fairness"]
    df1, df16, df17 = (d1["fairness"], d16["fairness"], d17["fairness"])
    rl, rd = b["routing"]["lat"], b["routing"]["dor"]
    rc = b["root_cause"]["dor"]
    pe = rc["pair_effect"]
    n_txn = dor["n_txn"]
    eff_l0 = 100 * lat["bounds"]["bound"] / max(1, l0["makespan"])
    eff_d0 = 100 * dor["bounds"]["bound"] / max(1, d0["makespan"])
    thr_l0 = l0["n_txn_done"] / max(1, l0["makespan"])
    thr_d0 = d0["n_txn_done"] / max(1, d0["makespan"])
    # Comparing a collapsed run's throughput against a healthy one would just
    # be measuring the stall timeout. Each routing is given its own best
    # concurrency instead, which is the honest capacity ratio.
    best_lat = max((r["thr"] for r in b["oc_sweep"]
                    if r["route"] == "lat" and r["completed"]), default=0.0)
    best_dor = max((r["thr"] for r in b["oc_sweep"]
                    if r["route"] == "dor" and r["completed"]), default=0.0)
    speed = best_dor / max(1e-9, best_lat)
    bd = dor["bounds"]
    bind = max(("link_lb", "port_lb", "cut_lb", "txn_lb"),
               key=lambda k: bd.get(k, 0))
    bind_name = {"link_lb": "LB_link", "port_lb": "LB_port",
                 "cut_lb": "LB_fabric", "txn_lb": "LB_txn"}[bind]
    f4 = next(r for r in b["fifo_sweep"]
              if r["route"] == "dor" and r["turn_depth"] == 4)
    f128 = next(r for r in b["fifo_sweep"]
                if r["route"] == "dor" and r["turn_depth"] == 128)
    knee = [r for r in b["oc_sweep"] if r["route"] == "lat"]
    knee_ok = max((r["outstanding"] for r in knee if r["completed"]),
                  default=0)
    knee_bad = min((r["outstanding"] for r in knee if not r["completed"]),
                   default=0)
    demo = [1.0] * (t["n_cores"] - 1) + [0.1]
    jain_demo = sum(demo) ** 2 / (len(demo) * sum(v * v for v in demo))
    best17 = max(b["s17_sweep"], key=lambda r: (r["jain"], -r["max_min"]))
    oc4 = next((r for r in b["oc_sweep"]
                if r["route"] == "dor" and r["outstanding"] == 4), None)
    s0oc = {r["oc"]: r for r in oc if r["scheme"] == "s0"}
    s1oc = {r["oc"]: r for r in oc if r["scheme"] == "s1"}
    o32, o128 = s0oc.get(32), s0oc.get(128)
    sd = b["seeds_dor"]
    s17oc128 = {k.split("_", 1)[1]: v for k, v in s17.items()
                if k.startswith("oc128_")}
    s16fc = d16.get("fc", {})
    eager = s16fc.get("n_grant_eager", 0)
    paced = s16fc.get("n_grant_paced", 0)

    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>3D 堆叠 NoC 上的 per-core 写带宽公平性</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, "WenQuanYi Micro Hei",
       "Noto Sans CJK SC", sans-serif;
       margin: 2rem auto; max-width: 1060px; color: #111; line-height: 1.65;
       padding: 0 1rem; }}
h1,h2,h3 {{ font-weight: 650; }}
h2 {{ margin-top: 2.2rem; border-bottom: 2px solid #e2e8f0;
      padding-bottom: 0.25rem; }}
h3 {{ margin-top: 1.4rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem;
         margin: 0.6rem 0; }}
th,td {{ border: 1px solid #e5e7eb; padding: 0.34rem 0.5rem;
         text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
td:last-child, th:last-child {{ text-align: left; }}
th {{ background: #f8fafc; }}
code {{ background: #f1f5f9; padding: 0.1rem 0.3rem; }}
img {{ max-width: 100%; border: 1px solid #e5e7eb; margin: 0.5rem 0; }}
.note {{ color: #475569; font-size: 0.9rem; }}
.def {{ background: #f8fafc; border-left: 3px solid #94a3b8;
        padding: 0.5rem 0.9rem; margin: 0.7rem 0; font-size: 0.93rem; }}
.bad {{ border-left-color: #dc2626; background: #fef2f2; }}
.good {{ border-left-color: #16a34a; background: #f0fdf4; }}
.key {{ background: #eff6ff; border: 1px solid #bfdbfe;
        border-left: 4px solid #2563eb; padding: 0.8rem 1.1rem;
        margin: 1rem 0; border-radius: 4px; }}
.key ol {{ margin: 0.4rem 0 0 1.1rem; }}
.key li {{ margin: 0.55rem 0; }}
</style></head><body>

<h1>3D 堆叠 NoC 上的 per-core 写带宽公平性</h1>
<p class="note">拓扑：<b>{t['n_die']} 个 top die</b>（每个 20 节点、双平面、
双向 full ring：10 个 AI core + 8 个 D2D bridge + 2 个非终端节点）
+ <b>{t['n_attach']} 条 D2D 链路</b>
+ <b>1 个 bottom die</b>（{t['n_has']} 个 HA，12 行 × 8 列；
6 条横向 + 8 条纵向<b>单向</b> half ring）。
workload：<b>{t['n_cores']} 个 AI core 均匀写 {t['n_has']} 个 HA</b>，
每 core {m['k']} 笔 <code>WriteNoSnp</code>、每笔 {m['m_wdata']} 个 WriteData
flit，共 {n_txn:,} 笔事务；每 core outstanding 上限
<b>{m['core_outstanding']}</b>。</p>

<h2>结论</h2>
<div class="key">
<ol>

<li><b>最重要的一条：在这个拓扑上，路由策略的影响远大于任何流控方案。</b>
按题目字面实现的 S0（每笔走<b>最短路径</b>、双平面负载均衡），
在规定的 outstanding={m['core_outstanding']} 下<b>直接拥塞崩溃</b>：
{n_txn:,} 笔事务只完成 {l0['n_txn_done']:,} 笔
（{100 * l0['n_txn_done'] / n_txn:.0f}%），且已有 core 的带宽掉到
0（max/min = ∞）。
换成<b>维序路由</b>（在目的列自己的 bridge 上跨 die，之后只走该列纵环，
一个横向跳都不走）——同一份硬件、同一个 workload、同样 outstanding：
<b>全部完成</b>，吞吐 {thr_d0:.3f} txn/cycle，
达到理论下界的 <b>{eff_d0:.0f}%</b>。
即使给最短路径挑它自己最好的并发度（outstanding={knee_ok}，
{best_lat:.3f} txn/cycle），维序路由在自己的最优点
（{best_dor:.3f} txn/cycle）仍然快 <b>{speed:.1f} 倍</b>。
而且 S1、S16、S17 加在最短路径版本上<b>全都救不回来</b>（§5.1），
因为流控只能决定“注入多少”，改变不了“流量往哪里挤”。</li>

<li><b>最短路径为什么反而更慢：它拿稀缺资源去换充裕资源。</b>
bottom die 跳延迟 {t['bot_hop_lat']} cycle，top die 环内
{min(t['top_link_lats'])}~{max(t['top_link_lats'])} cycle，
所以“按时延最短”会诱导流量<b>尽早跨到 bottom die</b>、再用横环横向挪位，
甚至<b>把纵环当作横环之间的过路通道</b>。
但容量模型显示纵环才是全局瓶颈：每笔事务的写数据要在纵环上走
{rd['dat_hops_per_txn'].get('v')} 个 flit·跳，
而 top 环容量（{t['capacity']['top']} 条链路）相对需求过剩十倍以上。
结果最忙的一条纵向链路承担了平均值的
<b>{rl['v_concentration']:.2f} 倍</b>（{n_txn:,} flit，
全压在第 0 列），维序路由把它压到 <b>{rd['v_concentration']:.2f} 倍</b>。
注意维序路由的平均跳数其实<b>更长</b>
（{rd['mean_fwd_hops']} vs {rl['mean_fwd_hops']} 跳），
却把下界抬高了 {rl['bounds']['bound'] / rd['bounds']['bound']:.2f} 倍
——<b>决定性能的是负载均衡，不是路径长度</b>。</li>

<li><b>修正计划中的一个预期：转向 FIFO 深度不是悬崖，4 flit 就够。</b>
计划把“横↔纵转向 FIFO 深 4 flit”列为需要验证的风险项。
实测在维序路由下，深度 4 与深度 128 的吞吐只差
{100 * (f128['thr'] / f4['thr'] - 1):.1f}%
（{f4['thr']:.3f} → {f128['thr']:.3f} txn/cycle），
全部事务都能完成。<b>此前观察到的“深度 4 活锁”其实是最短路径路由的热点，
不是缓冲不足</b>：最短路径下把 FIFO 加深到 128 依然崩溃
（只完成 {next(r for r in b['fifo_sweep'] if r['route'] == 'lat' and r['turn_depth'] == 128)['n_txn_done']:,}
笔）。也就是说这里<b>不需要</b>为 bottom die 付大缓冲的代价。</li>

<li><b>公平性最便宜的解法根本不是流控，而是把 outstanding 从 128 调到 32。</b>
这是本文最实用的发现。跨 3 个随机种子，维序路由下：
outstanding=128 得到平均效率 {100 * o128['eff_mean']:.1f}%、
Jain {o128['jain_mean']}、最差 max/min {o128['mm_worst']}；
outstanding=32 得到平均效率 <b>{100 * o32['eff_mean']:.1f}%</b>、
Jain <b>{o32['jain_mean']}</b>、最差 max/min <b>{o32['mm_worst']}</b>。
也就是说<b>吞吐和公平性同时变好</b>，而代价是<b>零</b>——
outstanding 上限本来就是一个已有的寄存器。
反过来说，题目规定的 128 把织物推过了最优点：
多出来的在途报文并不产生吞吐，只是变成互相阻塞的偏转流量。
并发度不是“越大越好、到某点崩溃”，而是<b>存在一个真正的最优点</b>
（{knee_ok} 以下欠载、32 附近最优、128 已经过载）。</li>

<li><b>把路由和并发度都调对之后，位置相关的失衡仍然存在。</b>
维序路由、outstanding={m['core_outstanding']}：
Jain = <b>{df0['jain']}</b>，max/min = <b>{df0['max_min']}</b>，
每核带宽 {df0['bw_min']} ~ {df0['bw_max']} flit/cycle。
需求是完全对称的（每 core 发一样多、目的地分布一样），
差异<b>纯粹来自位置</b>。</li>

<li><b>根因不是“top die 编号”——计划里的假设被数据否定。</b>
计划预期带宽按 top die 编号（即纵环插入位置 1,2,8,9,15,16）<b>单调</b>排序。
实测 Spearman(die 编号, 带宽) = <b>{rc['corr']['die']}</b>，
几乎为零。真实结构是<b>成对锯齿</b>：
每个行间隙放<b>两个相邻</b>的挂接点，第二个永远在第一个的正下游。
在“在环优先”绝对优先的规则下，第二个挂接点注入时，
它要挤进的那条出边上已经载着第一个挂接点<b>全部</b>的写数据。
解析值把机理钉死了——成对的注入边负载相差恰好 <b>48 个单位</b>
（168↔216、144↔192、120↔168），
而 48 = 12 HA × {m['m_wdata']} flit = <b>一个 die 的完整写数据输出</b>。
偶数 die 平均 <b>{pe['first_mean']}</b>、奇数 die 平均
<b>{pe['second_mean']}</b>，相差 <b>{pe['ratio']:.2f} 倍</b>；
Spearman(成对次序, 带宽) = <b>{rc['corr']['pair']}</b>，
Spearman(注入边 DAT 负载, 带宽) = <b>{rc['corr']['inj_dat']}</b>。
而 core 在 top 环上的位置几乎没有影响（{rc['corr']['top_idx']}）——
top 环容量过剩，片内位置不构成瓶颈。</li>

<li><b>S1（拥塞等级 + AIMD）方向正确、收益真实但有限，且代价不低。</b>
Jain {df0['jain']} → <b>{df1['jain']}</b>，
max/min {df0['max_min']} → <b>{df1['max_min']}</b>，
吞吐还略有改善（makespan {d0['makespan']} → {d1['makespan']}）。
跨种子也稳定（最差 Jain {sd['s1']['jain_min']}、
最差 max/min {sd['s1']['max_min_worst']}），是三个流控方案里<b>最可靠</b>的。
但它需要一条<b>专用广播总线</b>（实测 {d1['fc']['bus_posts']:,} 次广播、
{d1['fc']['bus_bits']:,} bit）和每源平均 {d1['fc']['mean_path_nodes']}
项的受控节点表。而且一旦 outstanding 已经调到 32，
S1 的增量就只剩 Jain {s0oc[32]['jain_mean']} → {s1oc[32]['jain_mean']}
（最差 max/min 都是 {s0oc[32]['mm_worst']}）——<b>几乎被“调寄存器”取代了</b>。</li>

<li><b>S16（接收端授权，Homa 式）在这个拓扑上<u>无效</u>，这是一个有价值的负面结论。</b>
CHI 的 <code>DBIDResp</code> 确实是天然的 GRANT，完成端（HA）也确实坐在
瓶颈纵环上，看起来是理想的控制点。但实测在 overcommit={s16fc.get('overcommit')}
下 <b>{eager:,} 笔授权是立即发出的，只有 {paced} 笔真正被节流</b>——
预算<b>从来没有绑定</b>，所以结果与基线基本相同
（Jain {df16['jain']} vs {df0['jain']}）。
把 overcommit 压小到会绑定的程度，公平性反而<b>急剧恶化</b>
（overcommit=4：Jain {b['s16_sweep'][0]['jain']}、
max/min {b['s16_sweep'][0]['max_min']}）。
原因是控制点<b>不在瓶颈上</b>：失衡发生在<b>注入侧</b>
（挂接点抢纵环槽位），而 S16 管的是<b>接收侧</b>灌入量。
扣住占优 die 的授权只是让它手上没数据，
在环优先会立刻把空出的槽位交给下一个过路 flit，starved 的挂接点<b>拿不到</b>。
<b>接收端驱动的拥塞控制能管“谁往瓶颈里灌多少”，
管不了“瓶颈处的仲裁不公”</b>——单环研究里两者恰好重合，这里分离了。</li>

<li><b>S17（挂接点转向仲裁）是本文提出的方案，但实测<u>不成立</u>——如实报告为负面结果。</b>
它的思路是最对症的：既然根因是挂接点的转向 FIFO 被过路流量无限期压住，
就给这个 FIFO 一个<b>有界耐心</b>阈值，
连续被抢走槽位超过阈值后让一个过路 flit 在闩存里等一拍。
单个种子上看确实有效（Jain {df0['jain']} → {best17['jain']}）。
但把阈值 1/2/8 各跑 3 个种子后，这个收益<b>消失了</b>：
唯一不比基线差的阈值是 8，平均 Jain
{s17oc128['S0']['jain_mean']} → {s17oc128['S17 pat=8']['jain_mean']}
（差别在噪声量级），而<b>最差 max/min 反而从
{s17oc128['S0']['mm_worst']} 恶化到 {s17oc128['S17 pat=8']['mm_worst']}</b>；
阈值 1 明显更差（Jain {s17oc128['S17 pat=1']['jain_mean']}、
max/min {s17oc128['S17 pat=1']['mm_worst']}）。
在 outstanding=32 上重复，结论相同。
<b>先前的单种子结论是噪声</b>。机理上的解释是：让位把槽位交给了转向 FIFO 的
<b>队首</b>，而队首未必属于那个被饿死的 core——
局部仲裁公平并不等于端到端公平。</li>

<li><b>三个流控方案里只有 S1 站得住，而它又被一个免费的旋钮基本取代。</b>
把四条结论并排看：<b>路由</b>决定织物能不能用（崩溃 vs 完成，各自最优并发度下也差 {speed:.1f} 倍）；
<b>outstanding</b> 免费地同时改善吞吐与公平；
<b>S1</b> 有可靠但不大的增量，代价是一条专用广播总线；
<b>S16 与 S17 都无效</b>。
最好的组合是 S1 + outstanding=32（平均 Jain
{s1oc[32]['jain_mean']}、最差 max/min {s1oc[32]['mm_worst']}、
平均效率 {100 * s1oc[32]['eff_mean']:.1f}%），
但它相对“只调 outstanding”（{s0oc[32]['jain_mean']} /
{s0oc[32]['mm_worst']}）的增量很小。
<b>工程结论：先调路由和 outstanding，再考虑是否值得为 S1 付一条总线。</b></li>

<li><b>所有运行时方案都没有达到验收线，真正的解法在布局层面。</b>
验收线是 Jain ≥ 0.98、max/min ≤ 1.05；
维序路由下最好的运行时组合只到 Jain ≈ {s1oc[32]['jain_mean']}、
max/min ≈ {s1oc[32]['mm_worst']}。唯一达标的点是把 outstanding 压到
{oc4['outstanding'] if oc4 else 4}（Jain {oc4['jain'] if oc4 else '—'}、
max/min {oc4['max_min'] if oc4 else '—'}），
但效率掉到 {100 * oc4['eff']:.0f}%——那是用吞吐换公平，不是解决问题。
根本原因是<b>成对相邻的挂接点布局把不对称写进了拓扑</b>：
无论怎么仲裁，第二个挂接点永远在第一个的下游。
彻底的修法是<b>改布局</b>——把同一行间隙的两个挂接点<b>分散</b>到
纵环上相距较远的位置，或让同一间隙的两条横环<b>反向</b>行进，
使“第二个永远在下游”这个前提不再成立。
这样位置失衡会从结构上消失，不需要任何运行时机制。</li>

</ol>
</div>

<div class="def good"><b>一句话建议</b>：
<b>维序路由（必须，决定崩溃与否）+ outstanding≈32（免费，
吞吐与公平同时更优）+ 转向 FIFO 4~8 flit（够用，不必加深）</b>。
这三项都不需要新增流控硬件。若仍需更公平，只有 S1 值得考虑
（代价是一条专用广播总线，增量有限）；<b>S16 与 S17 实测无效</b>。
若能改版图，优先<b>分散成对挂接点</b>，这比任何运行时机制都彻底。</div>

<h2>1. 拓扑与硬件配置</h2>
<p>bottom die 的结构是理解全部结论的前提。每一列是一条<b>单向</b>纵向
half ring，{t['v_len']} 个节点按行进顺序排列：12 个 HA 与 6 个挂接点交错，
6 个挂接点<b>成对</b>出现在 3 个行间隙里（vpos 1&amp;2、8&amp;9、15&amp;16）。
两个关键结构事实：</p>
<ul>
<li><b>横环 <i>t</i> 在每一列都切在同一个纵向位置</b>，
所以 top die <i>t</i> 在全部 8 列都从同一个 vpos 注入——
它的位置优劣对所有目的地是<b>同相</b>的，不会在目的地上被平均掉。</li>
<li><b>挂接点成对相邻</b>，于是每对里的第二个永远在第一个的正下游。
这正是 §6 的根因。</li>
</ul>
<img src="{p_topo.name}" alt="bottom die 布局">
{setup_table(b)}

<h3>1.1 链路延迟</h3>
{link_table(b)}

<h3>1.2 路由与平面</h3>
<p>两个平面只存在于 top die；D2D 与 bottom die 的链路为两个平面共享。
平面选择按“最少占用”做负载均衡。三种路由策略在同一份拓扑上的对比：</p>
{routing_table(b)}
<div class="def bad"><b>怎么读这张表。</b>
维序路由的<b>平均跳数更长</b>，纵环 DAT 跳数<b>完全一样</b>
（{rd['dat_hops_per_txn'].get('v')} 跳/笔），
横环跳数降到 <b>0</b>，top 环跳数从
{rl['dat_hops_per_txn'].get('top')} 涨到
{rd['dat_hops_per_txn'].get('top')}。
它没有缩短路径，只是把工作量从<b>稀缺</b>的 bottom die 挪到<b>充裕</b>的
top die，并把纵环负载摊平（集中度
{rl['v_concentration']:.2f}× → {rd['v_concentration']:.2f}×），
换来下界抬高 {rl['bounds']['bound'] / rd['bounds']['bound']:.2f} 倍。
还要注意：每个 top die 对 8 列各有一个 bridge，
所以 core↔HA 流量<b>根本不需要横环</b>——
横环在这个 workload 下是纯粹多余的通路，而“按时延最短”偏偏要去用它。</div>
<p>最忙的几条链路，直接印证上面的集中度：</p>
{hot_table(b)}
<p class="note">最短路径下三条最忙链路<b>全在第 0 列</b>，
最忙那条正好等于全部 {n_txn:,} 笔事务的 DAT；
维序路由下最忙的链路分散在各列，且恰好是
<b>每对里第二个挂接点的注入边</b>（A(h1)@v2 → HA(r2)），这就是 §6 的根因。</p>

<h2>2. 公平性指标：Jain 指数怎么算、怎么读</h2>
<div class="def">
<p>对 <i>n</i> 个 core 的带宽 <i>x</i><sub>1</sub>…<i>x<sub>n</sub></i>：</p>
<p style="text-align:center; font-size:1.05rem">
J(x) = ( Σ<i>x<sub>i</sub></i> )<sup>2</sup> /
( <i>n</i> · Σ<i>x<sub>i</sub></i><sup>2</sup> )</p>
<p>它就是<b>算术平均的平方除以平方平均</b>，等价于
1 / (1 + CV<sup>2</sup>)，其中 CV 是变异系数。因此：</p>
<ul>
<li><b>取值范围 [1/<i>n</i>, 1]</b>。全部相等时 J = 1；
只有一个 core 拿到全部带宽时 J = 1/<i>n</i>
（本文 <i>n</i> = {t['n_cores']}，下限 {1 / t['n_cores']:.4f}）。</li>
<li><b>与量纲无关</b>：所有带宽同乘一个常数，J 不变。
所以它衡量的是“分布形状”，不是绝对性能——
一个吞吐极低但人人平等的方案 J 也等于 1。
这正是第 4 条结论里 outstanding=4 的陷阱，
也是本文每张表都把 <b>Jain 与对下界效率并列</b>的原因。</li>
<li><b>可解释为“有效份额人数”</b>：J ≈ <i>k/n</i> 意味着
效果相当于带宽被 <i>k</i> 个 core 平分、其余基本饿死。</li>
<li><b>对少数离群点不敏感</b>，这是它的弱点：
{t['n_cores'] - 1} 个 core 相等、1 个只有 1/10，
J 仍然高达 {jain_demo:.4f}。所以必须同时看
<b>max/min</b>（最快 ÷ 最慢，直接暴露最差个体）和
<b>CoV</b>（相对离散度）。本文三个指标一起给。</li>
</ul>
</div>
<p class="note"><b>带宽的测量窗口。</b>闭合批次里每个 core 最终都会发出同样多的
WriteData flit，跑完再数必然相等、毫无信息。因此统计窗口取
<code>t_fair</code> = <b>第一个 core 把自己的活干完的时刻</b>；
在此之前全部 {t['n_cores']} 个 core 都在争抢同一批资源，
这段区间内的每核 flit 数才是真实份额。</p>

<h2>3. 下界</h2>
<p>维序路由下，{n_txn:,} 笔事务的 makespan 下界：</p>
{bounds_table(bd)}
<p>本例的约束来自 <b>{bind_name}</b>，即<b>纵向 half ring 这一层织物</b>的总容量
——{t['capacity']['v']} 条纵向有向链路要承载全部写数据。
三条 CHI VC 相互独立，所以链路下界取各 VC 的<b>最大值</b>而不是求和；
上下环端口每站点只有一个、被三条 VC 共享，所以端口下界要把各 VC 相加。</p>

<h2>4. 现象一：织物可用性（路由、缓冲、并发度）</h2>
<p>讨论公平性之前必须先确认织物<b>能不能跑</b>。这里有三个旋钮，
但只有两个真正重要。</p>

<h3>4.1 转向 FIFO 深度：不是悬崖</h3>
{fifo_table(b)}
<div class="def good"><b>与计划预期相反。</b>
维序路由下深度 4 与 128 只差
{100 * (f128['thr'] / f4['thr'] - 1):.1f}% 吞吐，
全部事务都完成，实测峰值占用也就是深度本身。
而最短路径下<b>加深到 128 仍然崩溃</b>。
所以“深度 4 会活锁”的现象<b>归因于路由热点，不是缓冲不足</b>：
一旦某条纵向链路被要求承载 {rl['v_concentration']:.2f} 倍平均负载，
任何有限缓冲都只是延后崩溃。
好消息是：修好路由之后，bottom die <b>不需要</b>付大缓冲的代价。</div>

<h3>4.2 并发度：存在最优点，不只是悬崖</h3>
{oc_table(b)}
<p>单种子容易被噪声误导，所以对关键取值做了 3 个种子的重复
（维序路由；★ 为推荐取值）：</p>
{oc_seed_table(oc)}
<div class="def good"><b>这是本文最实用的一张表。</b>
S0 从 outstanding=128 调到 32，平均效率
{100 * o128['eff_mean']:.1f}% → <b>{100 * o32['eff_mean']:.1f}%</b>、
Jain {o128['jain_mean']} → <b>{o32['jain_mean']}</b>、
最差 max/min {o128['mm_worst']} → <b>{o32['mm_worst']}</b>，
<b>三项全部改善，硬件代价为零</b>。
机理是“在路上的包会互相阻碍”的定量版本：
并发度越高，注入失败与落环偏转越多，
而单向 {t['v_len']} 节点纵环上偏转一次要烧掉 {t['v_len']} 个槽位。
超过最优点之后，新增的在途报文不产生吞吐，只产生互相阻塞。
注意 oc=16 反而更差（Jain {s0oc[16]['jain_mean']}）：
并发太低时少数 core 会把窗口跑完、样本变短，Jain 也不好。
所以是<b>最优点</b>，不是单调关系。</div>
<div class="def bad">最短路径下则是纯悬崖：outstanding ≤{knee_ok} 可用、
≥{knee_bad} 崩溃，且崩溃后效率锁在 15% 左右。
这个悬崖还与<b>负载持续时间</b>有关：批次较小时（每 core ≲20 笔）
即使 outstanding=128 也能跑完，
只有在<b>持续饱和</b>下正反馈才会失控——本文的
{m['k']} 笔/core 正是题目要求的“把访存带宽用满”的工况。</div>

<h2>5. 现象二：位置相关的每核写带宽失衡</h2>
<img src="{p_die.name}" alt="按 die 分组的带宽">
<p class="note">纵轴是<b>份额</b>（每核带宽 ÷ 该方案自身均值），
不是绝对带宽：不同方案的公平窗口 <code>t_fair</code> 长度不同，
绝对值不可横向比较，而份额正是 Jain 所度量的东西。
右图可以直接看出 S1（橙）把饿死的 die 1 从 0.63 抬到 0.81、
同时压低领先者，这就是它 Jain 更高的来源；
S16（蓝）与 S0（红）几乎重合，说明它没有生效。</p>

<h3>5.1 最短路径（题目字面实现）：四个方案全部崩溃</h3>
{scheme_table(b, 'lat')}
<div class="def bad"><b>流控救不了路由。</b>
热点是<b>某一条具体链路</b>被路由反复选中，
而流控只能决定“注入多少”、不能决定“走哪里”。
少注入只是让崩溃来得慢一点，不改变第 0 列纵环
{rl['v_concentration']:.2f} 倍的负载集中。
这一行数据的意义在于：<b>先修路由，再谈公平</b>。</div>

<h3>5.2 维序路由：织物健康，失衡显现</h3>
{scheme_table(b, 'dor')}
<img src="{p_srt.name}" alt="每核带宽分布">
<p>曲线越平越公平。四条曲线的左半段几乎全部由
“每对里第二个挂接点”所属的 die（1、3、5）的 core 构成，
这直接引出下一节的根因。</p>

<h2>6. 根因</h2>

<h3>6.1 计划中的假设被否定</h3>
<p>原假设：横环 <i>t</i> 在所有列切在同一纵向位置，
所以 top die <i>t</i> 的偏置是同相的，
带宽应当按 die 编号（vpos 1,2,8,9,15,16）<b>单调</b>排序。
<b>同相这一点是对的，单调这一点是错的</b>：</p>
{corr_table(b, 'dor')}

<h3>6.2 真实机理：成对相邻的挂接点</h3>
<p>每个行间隙放两个<b>相邻</b>的挂接点。
第二个永远在第一个的正下游，于是它注入时要挤进的那条纵向出边上，
已经载着第一个挂接点<b>全部</b>的写数据流量。</p>
<img src="{p_vprof.name}" alt="纵环解析负载">
{die_table(b, 'dor')}
<div class="def">解析值把机理钉死了：成对的两个挂接点，注入边 DAT 负载相差恰好
<b>48 个单位</b>（168↔216、144↔192、120↔168），
而 48 = 12 HA × {m['m_wdata']} flit =
<b>一个 die 的完整写数据输出</b>。
次要效应是行间隙的位置：越靠下游（die 4/5）越轻，
因为上游的 HA 已经把一部分流量吸收走了
（Spearman(行间隙编号, 带宽) = {rc['corr']['gap']}）。
两个效应叠加，就是 {pe['first_mean']} vs {pe['second_mean']}
（{pe['ratio']:.2f} 倍）的成对锯齿结构。
另一个佐证：die 0 与 die 5 的注入边负载相同（都是 168），
但 die 0 平均 {rc['by_die']['0']['mean']}、
die 5 只有 {rc['by_die']['5']['mean']}——
差别就在于 die 5 是“第二个”。这也解释了为什么
Spearman(成对次序) = {rc['corr']['pair']} 比
Spearman(注入边负载) = {rc['corr']['inj_dat']} 更强。</div>
<img src="{p_sc.name}" alt="带宽 vs 注入边负载">

<h2>7. 三个流控方案</h2>

<h3>7.1 S1：按规格实现的拥塞等级 AIMD</h3>
<p>四个部分按规格实现：<b>检测</b>（每站点每窗口统计上环失败与落环偏转，
等级 = min(7, 次数/8)）、<b>传播</b>（专用 3bit 广播总线，不占 NoC）、
<b>反馈</b>（每个源维护受控节点表，取其中最大等级）、
<b>控制</b>（终值 = level_of(自身失败 − 8×收到的最大净等级)，
对整数注入预算做 AIMD）。
唯一改动是受控节点表的构造：单环上是 <code>(idx+dir) % n</code> 走 hops 步，
这里改为沿路由的边表收集途经站点——机械替换，不是策略变化。</p>
<p>结果：Jain {df0['jain']} → <b>{df1['jain']}</b>，
max/min {df0['max_min']} → <b>{df1['max_min']}</b>，
makespan {d0['makespan']} → {d1['makespan']}（略快）。
跨种子稳定，是三个方案里最可靠的。
局限在于 max 聚合让同一条纵环上的所有 core 收到相同的拥塞等级，
而真正要区别对待的是“同一行间隙里的第一个 vs 第二个”——
所以它只能压掉一部分差距，不能对症。</p>

<h3>7.2 S16：接收端驱动的授权（Homa 式）</h3>
<p><code>WriteNoSnp</code> 规定拿到 <code>DBIDResp</code> 之前不许发 WriteData，
所以完成端本来就握有“谁、何时可以把写数据放上织物”的权力，
基线只是一到就授权、把它浪费了。S16 不加报文、不加总线，
只改授权的<b>时机与顺序</b>（每完成端最多挂起 overcommit 个授权，
按已服务量最少优先）。在本拓扑上完成端（HA）还正好坐在瓶颈纵环上，
看起来是理想的控制点。</p>
{s16_table(b)}
<div class="def bad"><b>但它在这里无效，而且原因很具体。</b>
overcommit={s16fc.get('overcommit')} 时
<b>{eager:,} 笔授权立即发出、只有 {paced} 笔被节流</b>——
自然并发度（峰值挂起授权 {s16fc.get('peak_grants')}）本来就低于预算，
<b>预算从未绑定</b>，所以结果与基线几乎逐位相同。
把 overcommit 压到会绑定的程度，公平性<b>急剧恶化</b>
（overcommit=4：Jain {b['s16_sweep'][0]['jain']}、
max/min {b['s16_sweep'][0]['max_min']}）：
全局并发度被压低后，公平窗口 <code>t_fair</code> 变短、每核样本变少，
而位置劣势依旧。<br>
根本原因是<b>控制点不在瓶颈上</b>：
失衡发生在<b>注入侧</b>（挂接点抢纵环槽位），
S16 管的是<b>接收侧</b>的灌入量。
扣住占优 die 的授权只是让它手上没数据，
在环优先会立刻把空出的槽位交给下一个过路 flit，
starved 的挂接点<b>拿不到</b>这个槽位。<br>
这与单环研究的结论并不矛盾：单环上瓶颈就是完成端所在的那条环段，
控制灌入量等价于控制仲裁；这里两者<b>分离</b>了。</div>

<h3>7.3 S17：挂接点转向仲裁（本文提出）</h3>
<p>既然根因是挂接点的转向 FIFO 被过路流量无限期压住，
最小的修法就是就地限制这个“无限期”：给转向 FIFO 一个<b>有界耐心</b>阈值，
连续被过路流量抢走槽位超过阈值后，
让一个过路 flit 在闩存里等一拍，把槽位交给转向 FIFO。</p>
{s17_table(b)}
<img src="{p_sw.name}" alt="三组扫描">
<p>硬件代价确实是三个方案里最小的：每挂接点每出环 1 个计数器
（共 {d17['fc']['n_counters']} 个）+ 每出边
{d17['fc']['latch_flits']} flit 闩存，没有广播总线、没有新报文。
它确实<b>局部破坏了链路无缓存性</b>，但破坏<b>有界且可计量</b>：
实测闩存深度 {d17['fc']['max_inring_hold']} flit。
上表（单种子）看起来阈值 1 效果最好。
<b>但这个结论经不起换种子。</b></p>
{s17_seed_table(s17)}
<div class="def bad"><b>S17 不成立，如实报告。</b>
3 个种子的平均值显示：唯一不比基线差的阈值是 8，
而它带来的 Jain 变化（{s17oc128['S0']['jain_mean']} →
{s17oc128['S17 pat=8']['jain_mean']}）在噪声量级，
<b>最差 max/min 反而变差</b>
（{s17oc128['S0']['mm_worst']} → {s17oc128['S17 pat=8']['mm_worst']}）；
阈值 1、2 则明显更差。把 outstanding 换成 32 重做一遍，结论相同。
所以<b>前面单种子上看到的改善是噪声</b>。<br>
为什么对症的方案也会失效？让位把槽位交给了转向 FIFO 的<b>队首</b>，
而队首未必属于那个被饿死的 core——
转向 FIFO 里混装了来自同一个 die 全部 10 个 core 的 flit。
<b>局部仲裁公平不等于端到端公平</b>：
要真正定向补偿，仲裁就得按源 core 区分，
那就退化成在每个挂接点维护 60 路状态，代价不再“最小”。</div>

<h3>7.4 跨随机种子的稳定性</h3>
<p>维序路由、outstanding={m['core_outstanding']}，
{sd['s0']['n_runs']} 个种子（目的地序列不同）：</p>
{seed_table(b, 'seeds_dor')}
<div class="def bad"><b>按最差种子判定，只有 S1 站得住。</b>
S1 最差 Jain {sd['s1']['jain_min']}、最差 max/min
{sd['s1']['max_min_worst']}，明显优于基线
（{sd['s0']['jain_min']} / {sd['s0']['max_min_worst']}）。
S16 与基线在 3 个种子中有 2 个<b>数值完全相同</b>，再次印证它没有生效。
S17 最差 Jain {sd['s17']['jain_min']}，低于基线的 {sd['s0']['jain_min']}。
<b>三个流控方案里只有 S1 的收益是可靠的</b>，
而它需要一条专用广播总线，且一旦 outstanding 调到 32，
增量就只剩 {s0oc[32]['jain_mean']} → {s1oc[32]['jain_mean']}。</div>

<h3>7.5 改进方案：推荐的组合</h3>
<p>把上面全部结果综合成一个可落地的配置。
注意其中<b>前三项都不是流控机制</b>——它们是路由策略与两个已有参数的取值，
却贡献了绝大部分收益。</p>
{_t(["优先级", "措施", "类型", "实测效果（维序路由，3 种子）", "硬件代价"], [
    ["<b>1（必须）</b>", "维序路由：在目的列自己的 bridge 上跨 die，"
     "之后只走该列纵环，不走横环", "路由策略",
     f"从拥塞崩溃（完成 {100 * l0['n_txn_done'] / n_txn:.0f}%）变为全部完成、"
     f"效率 {eff_d0:.0f}%；各自最优并发度下吞吐 {best_lat:.3f} → "
     f"{best_dor:.3f} txn/cycle（<b>{speed:.1f} 倍</b>）",
     "无（只改路由表/路由函数）"],
    ["<b>2（免费）</b>", f"每 core outstanding 由 {m['core_outstanding']} 降到 32",
     "已有寄存器取值",
     f"平均效率 {100 * o128['eff_mean']:.1f}% → "
     f"<b>{100 * o32['eff_mean']:.1f}%</b>，"
     f"Jain {o128['jain_mean']} → <b>{o32['jain_mean']}</b>，"
     f"最差 max/min {o128['mm_worst']} → <b>{o32['mm_worst']}</b>",
     "<b>零</b>"],
    ["3（省成本）", "转向 FIFO 保持 4~8 flit，不必加深", "缓冲配置",
     f"深度 4 与 64 的吞吐仅差 "
     f"{100 * (f128['thr'] / f4['thr'] - 1):.1f}%",
     "比原先设想的 64 flit <b>省 16 倍</b>"],
    ["4（可选）", "S1 拥塞等级 AIMD", "流控机制",
     f"在 oc=32 基础上 Jain {s0oc[32]['jain_mean']} → "
     f"{s1oc[32]['jain_mean']}，最差 max/min 同为 {s1oc[32]['mm_worst']}",
     "<b>专用 3bit 广播总线</b> + 每源受控节点表"],
    ["<b>5（最彻底）</b>", "改版图：分散成对挂接点，或让同间隙两条横环反向",
     "拓扑布局", "从结构上消除“第二个永远在下游”这一前提，"
     "预期可直接达标", "版图改动，运行时零开销"],
])}
<div class="def good">若只能做一件事，做第 1 项；
若能做两件，做第 1、2 项——<b>这两项加起来不需要任何新增硬件</b>，
就把织物从崩溃状态带到效率 {100 * o32['eff_mean']:.1f}%、
Jain {o32['jain_mean']}。S1 是唯一值得考虑的流控增量，
但要先确认那条广播总线的代价换得来那点增量。</div>

<h2>8. 代价对比</h2>
{cost_table(b)}
<div class="def good"><b>推荐组合</b>：
<b>维序路由（必须）+ outstanding≈32（免费）+ 转向 FIFO 4~8 flit（够用）</b>。
第一项决定织物能不能用（崩溃 vs 完成），
第二项同时改善吞吐与公平且不花一个门，
第三项说明不必为 bottom die 付大缓冲。
这三项<b>全都不需要新增流控硬件</b>。
在此之上若仍需更高公平性，只有 S1 值得考虑，
且要先接受一条专用广播总线的代价换取有限的增量；
<b>S16 与 S17 在本拓扑实测无效</b>。</div>
<div class="def"><b>更彻底的方案在布局层面。</b>
成对相邻的挂接点把不对称直接写进了拓扑：
无论怎么仲裁，第二个挂接点始终在第一个的下游。
若把同一行间隙的两个挂接点<b>分散</b>到纵环上相距较远的位置，
或让同一间隙的两条横环<b>反向</b>行进，
“第二个永远在下游”这个前提就不成立了，
位置失衡会从结构上消失，而不需要任何运行时机制。</div>

<h2>9. 复现</h2>
<pre style="background:#f8fafc;border:1px solid #e5e7eb;padding:0.7rem;
font-size:0.85rem;overflow-x:auto">PYTHONPATH=utils python3 utils/dse_stack_write_fair.py
PYTHONPATH=utils python3 utils/gen_stack_write_report.py
PYTHONPATH=utils python3 utils/verify_stack.py
PYTHONPATH=utils python3 utils/verify_ring2_20.py</pre>
<p class="note">数据：<code>results/dse_stack_write_fair.json</code>
（仿真耗时 {m.get('wall_s')} s，K={m['k']}/core，
种子 {', '.join(str(s) for s in m['seeds'])}）与
<code>results/dse_stack_oc_seeds.json</code>。
四拍守恒、无 flit 丢失、makespan ≥ 下界、FIFO 占用有界、
维序路由不使用横环、S16 无预算时与 S0 逐位相同
等校验见 <code>utils/verify_stack.py</code>。</p>

</body></html>
"""
    OUT.write_text(html)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
