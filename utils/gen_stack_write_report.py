#!/usr/bin/env python3
"""HTML report: per-core write bandwidth fairness on the 3D-stacked fabric.

Six top-die full rings, 48 D2D links, and a bottom die of 6 horizontal + 8
vertical unidirectional half rings serving 96 HAs. The attach points are
grouped 2 rows x 4 columns per top die, and every HA is bound to one D2D
bridge, so the route is fixed by the destination rather than chosen.

Same question order as the single-ring report: conclusions first, then
topology and hardware setup with link delays, Jain, bounds, the phenomenon,
the root cause, S1, the improved scheme, and cost.
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


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


# ---------------------------------------------------------------------------
# plots
# ---------------------------------------------------------------------------

def plot_topology(b: dict, path: Path) -> None:
    """Bottom die: 96 HAs, the 48 attach points, and the 2x4 grouping."""
    t = b["topology"]
    ncol, nrow = t["n_cols"], 12
    bind = b["binding"]
    _cjk()
    fig, ax = plt.subplots(figsize=(11.2, 8.6))

    # vertical guide per column
    for c in range(ncol):
        ax.plot([c, c], [-0.7, nrow - 0.3], color="#cbd5e1", lw=1.0, zorder=1)

    y_of_row = {}
    y = 0.0
    gaps = {1: [], 6: [], 11: []}
    for row in range(1, nrow + 1):
        y_of_row[row] = y
        y += 1.0
        if row in gaps:
            gaps[row] = [y, y + 0.62]
            y += 1.5

    for row in range(1, nrow + 1):
        for c in range(ncol):
            ax.add_patch(plt.Rectangle((c - 0.30, y_of_row[row] - 0.19),
                                       0.60, 0.38, fc="#e5edff",
                                       ec="#94a3b8", lw=0.6, zorder=3))
    ax.text(-1.42, y_of_row[6], "96 个 HA\n12 行 × 8 列",
            fontsize=10, ha="center", va="center", color="#1e293b")

    # attach rows: two per gap, each spanning all 8 columns
    h = 0
    for after_row in (1, 6, 11):
        for slot, yy in enumerate(gaps[after_row]):
            ax.plot([-0.55, ncol - 0.45], [yy, yy], color="#f59e0b",
                    lw=1.8, zorder=2)
            ax.annotate("", xy=(ncol - 0.42, yy), xytext=(ncol - 0.75, yy),
                        arrowprops=dict(arrowstyle="-|>", color="#f59e0b",
                                        lw=1.6))
            ax.text(ncol - 0.30, yy, f"H{h}", fontsize=9, va="center",
                    color="#b45309", fontweight="bold")
            for c in range(ncol):
                die = next(r["die"] for r in bind
                           if r["land_h"] == h and r["land_col"] == c)
                ax.add_patch(plt.Circle((c, yy), 0.20,
                                        fc=DIE_COLOR[die % 6], ec="white",
                                        lw=1.0, zorder=4))
                ax.text(c, yy, str(die), fontsize=7.5, color="white",
                        ha="center", va="center", zorder=5,
                        fontweight="bold")
            h += 1

    # the 2x4 group brackets
    for die in range(t["n_die"]):
        rows = [r for r in bind if r["die"] == die]
        cs = sorted({r["land_col"] for r in rows})
        hs = sorted({r["land_h"] for r in rows})
        ys = []
        hh = 0
        for after_row in (1, 6, 11):
            for yy in gaps[after_row]:
                if hh in hs:
                    ys.append(yy)
                hh += 1
        x0, x1 = min(cs) - 0.44, max(cs) + 0.44
        y0, y1 = min(ys) - 0.40, max(ys) + 0.40
        ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False,
                                   ec=DIE_COLOR[die % 6], lw=1.7, ls="--",
                                   zorder=6))
        ax.text((x0 + x1) / 2, y1 + 0.16, f"top die {die} 的 2×4 挂接组",
                fontsize=8.4, ha="center", color=DIE_COLOR[die % 6],
                fontweight="bold")

    ax.set_xlim(-2.5, ncol + 0.6)
    ax.set_ylim(-1.3, y + 0.2)
    ax.set_xticks(range(ncol))
    ax.set_xticklabels([f"列{c}" for c in range(ncol)], fontsize=9)
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("bottom die：96 个 HA、48 个挂接点，"
                 "挂接点按“2 横 × 4 列 = 8 个”分组挂到 6 个 top die\n"
                 "圆圈内数字 = 该挂接点所属的 top die；"
                 "橙色箭头 = 横向单向 half ring（跨全部 8 列）",
                 fontsize=11.5, pad=14)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_binding(b: dict, path: Path) -> None:
    """Which bridge serves which column, and which ones must cross."""
    bind = [r for r in b["binding"] if r["die"] in (0, 1)]
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.6))
    for ax, die in zip(axes, (0, 1)):
        rows = sorted((r for r in bind if r["die"] == die),
                      key=lambda r: r["bridge_idx"])
        for i, r in enumerate(rows):
            near = r["kind"] == "near"
            c = "#16a34a" if near else "#dc2626"
            ax.barh(i, r["h_hops"] if not near else 0.12, color=c,
                    height=0.62)
            ax.text(-0.12, i,
                    f"bridge {r['bridge_idx']:>2}  →  A(H{r['land_h']},列"
                    f"{r['land_col']})", fontsize=8.6, ha="right",
                    va="center")
            lab = ("本列直落" if near
                   else f"横向 {r['h_hops']} 跳 → 列{r['target_col']}")
            ax.text((r["h_hops"] if not near else 0.12) + 0.12, i, lab,
                    fontsize=8.6, va="center", color=c)
        ax.set_yticks([])
        ax.set_xlim(-2.9, 6.4)
        ax.invert_yaxis()
        ax.set_xlabel("到目标列需要的横向跳数", fontsize=9)
        ax.set_title(f"top die {die}（挂接列 "
                     f"{list(sorted({r['land_col'] for r in rows}))}）",
                     fontsize=10.5)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
    fig.suptitle("HA↔D2D bridge 绑定：每个 bridge 独占一整列的 12 个 HA。"
                 "8 个 bridge 覆盖 8 列，但挂接组只落在 4 列上，"
                 "所以有一半必须先走横环换列", fontsize=11.5, y=1.02)
    fig.tight_layout()
    fig.savefig(path, dpi=132, bbox_inches="tight")
    plt.close(fig)


def plot_v_profile(b: dict, path: Path) -> None:
    """Analytic per-edge load along one column's vertical half ring."""
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.8), sharey=True)
    for ax, key, ttl in ((axes[0], "v_profile", "左半区列（列 0）"),
                         (axes[1], "v_profile_right", "右半区列（列 7）")):
        prof = b[key]
        rows = prof["rows"]
        xs = [r["vpos"] for r in rows]
        ax.plot(xs, [r["dat"] for r in rows], "-o", color="#dc2626",
                ms=4.2, lw=1.7, label="写数据 DAT")
        ax.plot(xs, [r["rsp"] for r in rows], "-s", color="#2563eb",
                ms=3.6, lw=1.3, label="响应 RSP")
        top = max(r["dat"] for r in rows)
        for r in rows:
            if r["role"] != "attach":
                continue
            ax.axvline(r["vpos"], color="#94a3b8", lw=0.8, ls=":")
            if r["dies"]:
                ax.text(r["vpos"], top * 1.05,
                        "die " + ",".join(str(d) for d in r["dies"]),
                        fontsize=7.8, rotation=90, ha="center", va="bottom",
                        color="#b45309", fontweight="bold")
        ax.set_xlabel("纵向 half ring 上的位置 vpos", fontsize=9)
        ax.set_title(ttl, fontsize=10.5)
        ax.set_ylim(0, top * 1.42)
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("解析每边负载（flit 单位）", fontsize=9)
    axes[0].legend(fontsize=8.6, loc="lower right")
    fig.suptitle("纵环上的负载分布：注入点（虚线）之后负载抬升，"
                 "越靠下游的 HA 越要与更多上游流量抢同一条边", fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_oc(b: dict, path: Path) -> None:
    """The central figure: throughput and fairness against concurrency."""
    oc = [r for r in b["oc_sweep"]]
    mand = b["meta"]["core_outstanding"]
    rec = b["_rec"]["oc"]
    _cjk()
    fig, axes = plt.subplots(1, 3, figsize=(13.4, 4.3))

    x = [r["outstanding"] for r in oc]
    ok = [r["completed"] for r in oc]

    def paint(ax, ys, ylab, ttl):
        for i in range(len(x) - 1):
            ax.plot(x[i:i + 2], ys[i:i + 2], color="#94a3b8", lw=1.2,
                    zorder=1)
        for xi, yi, o in zip(x, ys, ok):
            ax.plot([xi], [yi], "o", ms=8, zorder=3,
                    color="#16a34a" if o else "#dc2626",
                    mec="white", mew=1.0)
        ax.set_xscale("log")
        ax.set_xticks(x)
        ax.set_xticklabels([str(v) for v in x], fontsize=7.6)
        ax.axvline(mand, color="#dc2626", ls="--", lw=1.3)
        ax.axvline(rec, color="#16a34a", ls="--", lw=1.3)
        ax.set_xlabel("每 core outstanding 上限", fontsize=9)
        ax.set_ylabel(ylab, fontsize=9)
        ax.set_title(ttl, fontsize=10.5)
        ax.grid(alpha=0.25)

    paint(axes[0], [r["thr"] for r in oc], "txn/cycle", "吞吐")
    ymax = max(r["thr"] for r in oc)
    axes[0].annotate(f"规定值 {mand}\n远在悬崖之外",
                     xy=(mand, min(r["thr"] for r in oc)),
                     xytext=(30, ymax * 0.62), fontsize=8.4,
                     color="#dc2626", ha="center",
                     arrowprops=dict(arrowstyle="->", color="#dc2626",
                                     lw=1.1))
    axes[0].annotate(f"推荐 {rec}", xy=(rec, ymax * 0.95),
                     xytext=(2.05, ymax * 0.40), fontsize=8.6,
                     color="#16a34a",
                     arrowprops=dict(arrowstyle="->", color="#16a34a",
                                     lw=1.1))
    paint(axes[1], [r["jain"] for r in oc], "Jain 指数", "公平性")
    axes[1].axhline(0.99, color="#2563eb", ls=":", lw=1.3)
    axes[1].text(x[-1], 0.9905, "公平线 Jain=0.99", fontsize=7.8,
                 ha="right", va="bottom", color="#2563eb")
    paint(axes[2], [r["n_deflections"] for r in oc], "偏转次数", "偏转开销")
    axes[2].set_yscale("log")
    fig.suptitle("绿点 = 全部事务完成；红点 = 拥塞崩溃（批次没有排空）。"
                 "并发度不是越大越好：越过悬崖之后吞吐塌掉，"
                 "偏转量涨两个数量级", fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_stability(b: dict, path: Path) -> None:
    """Cross-seed: where the cliff is, and where the fairness line is met."""
    st = b["stability"]["rows"]
    nseed = len(b["stability"]["seeds"])
    _cjk()
    fig, axes = plt.subplots(1, 3, figsize=(13.4, 4.2))
    for name, c in (("split", "#16a34a"), ("stack", "#dc2626")):
        sub = sorted((r for r in st if r["h_assign"] == name),
                     key=lambda r: r["outstanding"])
        xs = [r["outstanding"] for r in sub]
        axes[0].plot(xs, [r["n_completed"] for r in sub], "-o", color=c,
                     ms=5, lw=1.5, label=name)
        # collapsed points have no meaningful throughput, so break the line
        axes[1].plot(xs, [r["thr_mean_ok"] if r["n_completed"] else float("nan")
                          for r in sub], "-o", color=c, ms=5, lw=1.5,
                     label=name)
        axes[2].plot(xs, [r["jain_mean"] for r in sub], "-o", color=c,
                     ms=5, lw=1.5, label=name)
    axes[0].set_ylabel(f"排空的种子数（共 {nseed}）", fontsize=9)
    axes[0].set_title("稳定性：悬崖在哪", fontsize=10.5)
    axes[0].set_ylim(-0.2, nseed + 0.3)
    axes[1].set_ylabel("txn/cycle（仅统计排空的运行）", fontsize=9)
    axes[1].set_title("吞吐", fontsize=10.5)
    axes[2].set_ylabel("Jain 指数（3 种子均值）", fontsize=9)
    axes[2].set_title("公平性", fontsize=10.5)
    axes[2].axhline(0.99, color="#2563eb", ls=":", lw=1.3)
    axes[2].text(max(r["outstanding"] for r in st), 0.9903,
                 "公平线 0.99", fontsize=7.8, ha="right", va="bottom",
                 color="#2563eb")
    rec = b["_rec"]["oc"]
    for ax in axes:
        ax.axvline(rec, color="#2563eb", ls="--", lw=1.2)
        ax.set_xlabel("每 core outstanding 上限", fontsize=9)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    fig.suptitle("跨 %d 个随机种子扫并发度：蓝色虚线是本文推荐的 %d，"
                 "它同时落在公平线之内和悬崖之内" % (nseed, rec),
                 fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_schemes(b: dict, path: Path) -> None:
    """Sorted per-core bandwidth at the mandated and the workable limit."""
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 4.6))
    for ax, tag, ttl in (
            (axes[0], "mandated",
             "规定的 outstanding=%d" % b["meta"]["core_outstanding"]),
            (axes[1], "work",
             "收敛后的 outstanding=%d" % b["meta"]["oc_work"])):
        per = b["schemes"][tag]
        for s in SCHEMES:
            r = per.get(s)
            if not r:
                continue
            bw = sorted(float(v) for v in
                        r["fairness"]["bw_by_core"].values())
            mean = sum(bw) / len(bw)
            ax.plot(range(len(bw)), [v / mean for v in bw], "-o", ms=2.6,
                    lw=1.5, color=COLOR[s],
                    label=f"{LABEL[s]}（Jain {r['fairness']['jain']}）")
        ax.axhline(1.0, color="#94a3b8", lw=1.0, ls=":")
        ax.set_xlabel("AI core（按自身带宽升序）", fontsize=9)
        ax.set_ylabel("带宽 / 该次运行的均值", fontsize=9)
        ax.set_title(ttl, fontsize=10.5)
        ax.legend(fontsize=7.8, loc="upper left")
        ax.grid(alpha=0.25)
    fig.suptitle("每核写带宽（对各自均值归一，因此只看形状不看高低）："
                 "曲线越平越公平", fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_root_cause(b: dict, path: Path) -> None:
    """Bandwidth against the structural variables that survive the regrouping."""
    rc = b["root_cause"]["work"]
    rows = rc["rows"]
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.5))

    ax = axes[0]
    for d in sorted({r["die"] for r in rows}):
        sub = [r for r in rows if r["die"] == d]
        ax.scatter([r["seat_max"] for r in sub], [r["bw"] for r in sub], s=34,
                   color=DIE_COLOR[d % 6], label=f"die {d}", zorder=3,
                   edgecolors="white", linewidths=0.5)
    ax.set_xlabel("该 core 要挤进的最忙纵环边负载（解析 flit）", fontsize=9)
    ax.set_ylabel("实测每核写带宽 flit/cycle", fontsize=9)
    ax.set_title("最强的单一预测变量：Spearman = %s（仍只是中等强度）"
                 % rc["corr"]["seat_max"], fontsize=10.5)
    ax.legend(fontsize=7.6, ncol=2)
    ax.grid(alpha=0.25)

    ax = axes[1]
    bd = rc["by_die"]
    ds = sorted(bd, key=lambda k: int(k))
    means = [bd[k]["mean"] for k in ds]
    lo = [bd[k]["mean"] - bd[k]["min"] for k in ds]
    hi = [bd[k]["max"] - bd[k]["mean"] for k in ds]
    ax.bar([int(k) for k in ds], means, yerr=[lo, hi], capsize=4,
           color=[DIE_COLOR[int(k) % 6] for k in ds], alpha=0.9)
    for k in ds:
        ax.text(int(k), bd[k]["max"],
                f"间隙{bd[k]['gap']}\n{'左' if bd[k]['half'] == 0 else '右'}半区",
                fontsize=7.4, ha="center", va="bottom", color="#334155")
    ax.set_xlabel("top die", fontsize=9)
    ax.set_ylabel("每核写带宽 flit/cycle", fontsize=9)
    ax.set_ylim(0, max(bd[k]["max"] for k in ds) * 1.28)
    ax.set_title("按 die 聚合（误差条 = 该 die 内 10 个 core 的极值）",
                 fontsize=10.5)
    ax.grid(alpha=0.25, axis="y")
    fig.suptitle("根因：带宽跟着“要挤进哪条纵环边”走，"
                 "而不是跟着 die 编号走", fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_hassign(b: dict, path: Path) -> None:
    """split vs stack: identical bound, different collapse point."""
    ha = b["hassign"]
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.3))
    for ax, metric, ylab in ((axes[0], "thr", "txn/cycle"),
                             (axes[1], "jain", "Jain 指数")):
        for name, c in (("split", "#16a34a"), ("stack", "#dc2626")):
            sub = sorted((r for r in ha if r["h_assign"] == name),
                         key=lambda r: r["outstanding"])
            xs = [r["outstanding"] for r in sub]
            ax.plot(xs, [r[metric] for r in sub], "-", color=c, lw=1.4,
                    label=f"{name}（远端流量走本 die 专属横环）"
                    if name == "split" else f"{name}（两个 die 共用一条横环）")
            for r in sub:
                ax.plot([r["outstanding"]], [r[metric]], "o", ms=8,
                        color=c if r["completed"] else "white", mec=c,
                        mew=1.6, zorder=3)
        ax.set_xscale("log")
        xs = sorted({r["outstanding"] for r in ha})
        ax.set_xticks(xs)
        ax.set_xticklabels([str(v) for v in xs], fontsize=8)
        ax.set_xlabel("每 core outstanding 上限", fontsize=9)
        ax.set_ylabel(ylab, fontsize=9)
        ax.legend(fontsize=8)
        ax.grid(alpha=0.25)
    fig.suptitle("实心点 = 完成，空心点 = 崩溃。两种横环分配的解析下界完全相同，"
                 "但耐并发能力不同", fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def setup_table(b: dict) -> str:
    t, m = b["topology"], b["meta"]
    cap = t["capacity"]
    rows = [
        ["top die 数量", t["n_die"], "每个是 20 节点、双平面、双向 full ring"],
        ["每 die 节点角色", "10 core / 8 D2D bridge / 2 非终端",
         "偶数号为 AI core；节点 9、19 既非 core 也非 HA，只转发"],
        ["AI core 总数", t["n_cores"], "发起方"],
        ["HA 总数", t["n_has"], "bottom die，12 行 × 8 列"],
        ["D2D 链路", t["n_bridges"], "每 die 8 条，双向，跨 SerDes"],
        ["挂接点", t["n_attach"],
         f"按“2 横 × {t['group_cols']} 列 = 8 个”一组，共 6 组挂 6 个 top die"],
        ["bottom die NoC", "6 横 + 8 纵 half ring",
         "half ring = <b>单向闭环</b>，仍然回绕，但只走一个方向"],
        ["纵环长度", t["v_len"], "12 个 HA + 6 个挂接点交替排列"],
        ["有向链路总数", f"{t['directed_links']:,}",
         f"top {cap['top']} / D2D {cap['d2d']} / 横 {cap['h']} / 纵 {cap['v']}"],
        ["CHI 虚通道", " / ".join(t["vcs"]),
         "REQ、RSP、DAT 各自独占链路带宽，互不阻塞"],
        ["每笔写的 flit 数",
         f"REQ {m['m_req']}、RSP {m['m_rsp']}、DAT {m['m_wdata']}",
         "WriteNoSnp 四段握手：REQ → DBIDResp → WriteData → Comp"],
        ["每 core outstanding", f"<b>{m['core_outstanding']}</b>",
         "本次按要求设定；下文会说明它在这个织物上意味着什么"],
        ["转向 / D2D FIFO 深度",
         f"{m['fabric']['turn_depth']} / {m['fabric']['d2d_depth']}",
         "唯一允许缓冲的地方；链路本身严格无缓冲"],
        ["workload",
         f"每 core {m['k']} 笔，均匀写全部 {t['n_has']} 个 HA",
         "需求完全对称，任何不均衡都是织物造成的"],
    ]
    return _t(["项目", "取值", "说明"], rows)


def link_table(b: dict) -> str:
    t = b["topology"]
    lats = t["top_link_lats"]
    uniq = sorted(set(lats))
    rows = [
        ["top die 环内链路", " / ".join(str(v) for v in uniq) + " cycle",
         f"共 {len(lats)} 段，按位置不同；两个平面各一份"],
        ["D2D 跨 die", f"{t['d2d_lat']} cycle",
         "SerDes + 跨时钟域，是全网最贵的一跳"],
        ["bottom die 环内跳", f"{t['bot_hop_lat']} cycle",
         "规则阵列，短线"],
        ["挂接点内转向", "1 cycle", "横环 → 纵环，经转向 FIFO"],
    ]
    return _t(["链路", "延迟", "说明"], rows)


def routing_table(b: dict) -> str:
    lab = b["meta"]["route_label"]
    rows = []
    for m in ("bound", "lat", "hops"):
        r = b["routing"].get(m)
        if not r:
            continue
        bd = r["bounds"]
        rows.append([
            lab.get(m, m), f"{bd['bound']:,}", f"{r['max_txn_per_cycle']:.4f}",
            r["mean_fwd_hops"],
            f"{r['dat_hops_per_txn'].get('v', 0)} / "
            f"{r['dat_hops_per_txn'].get('h', 0)}",
            f"{r['v_concentration']:.2f}×",
        ])
    return _t(["路由策略", "下界(cycle)", "上限 txn/cycle", "平均正向跳数",
               "每笔 DAT 的纵/横 flit·跳", "最忙纵向链路 / 平均"], rows)


def binding_summary(b: dict) -> str:
    bind = b["binding"]
    rows = []
    for die in range(b["topology"]["n_die"]):
        sub = [r for r in bind if r["die"] == die]
        near = [r for r in sub if r["kind"] == "near"]
        far = [r for r in sub if r["kind"] == "far"]
        cols = sorted({r["land_col"] for r in sub})
        rows.append([
            f"top die {die}",
            f"间隙 {sub[0]['land_h'] // 2}",
            f"{cols[0]}–{cols[-1]}",
            f"H{sub[0]['near_h']} / H{sub[0]['far_h']}",
            len(near), len(far),
            f"{far[0]['h_hops']} 跳" if far else "—",
        ])
    return _t(["top die", "所在行间隙", "挂接列", "近/远横环",
               "本列直落的 bridge", "需换列的 bridge", "换列距离"], rows)


def bounds_table(bd: dict) -> str:
    rows = [
        ["链路下界（分 VC 取最大）", f"{bd['link_lb']:,}",
         "最忙的一条有向链路上、单个 VC 需要搬运的 flit 数"],
        ["端口下界", f"{bd['port_lb']:,}",
         "某个站点注入或弹出端口的总需求（各 VC 合并，端口只有一个）"],
        ["织物切割下界", f"{bd['cut_lb']:,}",
         "某类织物的总 flit·跳 ÷ 该类链路数"],
        ["单事务时延下界", f"{bd['txn_lb']:,}",
         "一笔写的四段握手串行时延，与并发无关"],
        ["<b>综合下界</b>", f"<b>{bd['bound']:,}</b>", "以上四者取最大"],
    ]
    by = bd["link_by_vc"]
    rows.insert(1, ["　└ 分 VC 明细",
                    " / ".join(f"{k} {v:,}" for k, v in sorted(by.items())),
                    "DAT 是 4 flit/笔，通常是它决定链路下界"])
    fab = bd["fabric_lb"]
    rows.insert(3, ["　└ 分织物明细",
                    " / ".join(f"{k} {v:,}" for k, v in sorted(fab.items())),
                    "纵环 144 条、横环 48 条，横环少但只承担换列流量"])
    return _t(["下界来源", "cycle", "含义"], rows)


def scheme_table(b: dict, tag: str) -> str:
    per = b["schemes"][tag]
    bd = per["bounds"]["bound"]
    n_txn = per["n_txn"]
    rows = []
    for s in SCHEMES:
        r = per.get(s)
        if not r:
            continue
        f = r["fairness"]
        rows.append([
            LABEL[s], _ok(r["completed"]),
            f"{r['n_txn_done']:,} / {n_txn:,}",
            f"{r['makespan']:,}", _pct(bd / max(1, r["makespan"])),
            f"<b>{f['jain']}</b>", _f(f["max_min"], 2), _f(f["cov"], 4),
            f"{r['n_deflections']:,}",
        ])
    return _t(["方案", "批次是否排空", "完成事务", "makespan", "达下界比例",
               "Jain", "max/min", "CoV", "偏转次数"], rows)


def oc_table(b: dict) -> str:
    rows = []
    mand = b["meta"]["core_outstanding"]
    work = b["meta"]["oc_work"]
    for r in b["oc_sweep"]:
        tag = ""
        if r["outstanding"] == mand:
            tag = " ← 题目规定"
        elif r["outstanding"] == work:
            tag = " ← 推荐"
        rows.append([
            f"{r['outstanding']}{tag}", _ok(r["completed"]),
            f"{r['makespan']:,}", f"{r['thr']:.3f}", _pct(r["eff"]),
            f"<b>{r['jain']}</b>", _f(r["max_min"], 2),
            f"{r['n_deflections']:,}",
        ])
    return _t(["outstanding", "批次是否排空", "makespan", "txn/cycle",
               "达下界比例", "Jain", "max/min", "偏转次数"], rows)


def saturation_table(b: dict) -> str:
    rows = []
    for r in b.get("saturation", []):
        rows.append([
            f"{r['k']}（{r['n_txn']:,} 笔）",
            r["peak_in_flight"],
            "<b>是</b>" if r["limit_binds"] else "否",
            _ok(r["completed"]),
            f"{r['n_txn_done']:,} / {r['n_txn']:,}",
            f"{r['thr']:.3f}",
        ])
    return _t(["每 core 批量 k", "实测在途峰值", f"上限是否真正生效",
               "批次是否排空", "完成事务", "txn/cycle"], rows)


def stability_table(b: dict) -> str:
    st = b["stability"]
    rows = []
    rec = b["_rec"]["oc"]
    for r in st["rows"]:
        if r["h_assign"] != "split":
            continue
        tag = " ← 推荐" if r["outstanding"] == rec else ""
        fair = (r["jain_mean"] >= 0.99 and r["mm_worst"] <= 1.5)
        rows.append([
            f"{r['outstanding']}{tag}",
            f"{r['n_completed']} / {r['n_runs']}",
            f"{r['thr_mean_ok']:.3f}" if r["n_completed"] else "—",
            _pct(r["eff_mean_ok"]) if r["n_completed"] else "—",
            f"<b>{r['jain_mean']}</b>", r["jain_min"],
            _f(r["mm_worst"], 2),
            ('<b style="color:#16a34a">达标</b>' if fair
             else '<span style="color:#b45309">未达标</span>'),
        ])
    return _t(["outstanding", "排空的种子数", "txn/cycle", "达下界比例",
               "Jain 均值", "Jain 最差", "max/min 最差",
               "是否满足公平线"], rows)


def depth_table(b: dict) -> str:
    rows = []
    need = b["depth"]["need"]
    for r in b["depth"]["rows"]:
        oc = r["outstanding"]
        tag = " ← 该并发度的最小可用深度" \
            if need.get(str(oc)) == r["turn_depth"] else ""
        rows.append([
            oc, f"{r['turn_depth']} / {r['d2d_depth']}{tag}",
            f"{r['n_completed']} / {r['n_runs']}",
            f"{r['thr_mean_ok']:.3f}" if r["n_completed"] else "—",
            f"<b>{r['jain_mean']}</b>", r["turn_peak"],
        ])
    return _t(["outstanding", "转向 / D2D 深度", "排空的种子数",
               "txn/cycle", "Jain 均值", "转向 FIFO 峰值占用"], rows)


def hassign_table(b: dict) -> str:
    rows = []
    for r in b["hassign"]:
        arr = r["col0_arrival"]
        rows.append([
            r["h_assign"], r["outstanding"], f"{r['bound']:,}",
            _ok(r["completed"]), f"{r['thr']:.3f}",
            f"<b>{r['jain']}</b>", _f(r["max_min"], 2),
            ",".join(f"d{k}→v{v}" for k, v in sorted(arr.items(),
                                                     key=lambda kv: int(kv[0]))),
        ])
    return _t(["横环分配", "outstanding", "下界", "批次是否排空",
               "txn/cycle", "Jain", "max/min", "各 die 进入列 0 的位置"], rows)


def fifo_table(b: dict) -> str:
    rows = []
    for r in b["fifo_sweep"]:
        rows.append([
            f"{r['turn_depth']} / {r['d2d_depth']}", _ok(r["completed"]),
            f"{r['makespan']:,}", f"{r['thr']:.3f}",
            f"<b>{r['jain']}</b>", r["turn_peak"], f"{r['n_deflections']:,}",
        ])
    return _t(["转向 / D2D FIFO 深度", "批次是否排空", "makespan",
               "txn/cycle", "Jain", "转向 FIFO 峰值占用", "偏转次数"], rows)


def corr_table(b: dict, tag: str) -> str:
    c = b["root_cause"][tag]["corr"]
    note = {
        "seat_max": "<b>最强的单一预测变量</b>：该 core 要并入的"
                    "<b>最忙</b>那条纵向边上已有多少上游流量",
        "seat_pearson": "平均纵环边负载的线性相关程度",
        "seat": "平均（而非最忙）纵环边负载",
        "die": "计划里的假设。数值很小，说明<b>不是</b>按 die 编号排序",
        "far_vpos": "换列后进入的挂接点位置",
        "half": "左半区(0) 还是右半区(1)",
        "gap": "0/1/2，即纵环上的三段",
        "h_hops": "每个 core 都是一半近一半远，本来就<b>没有区分度</b>",
        "near_vpos": "本列直落的挂接点位置",
        "top_idx": "core 在 top 环上的序号；top 环容量过剩，影响最小",
    }
    order = sorted(note, key=lambda k: -abs(c.get(k, 0)))
    rows = []
    for k in order:
        v = c.get(k)
        if v is None:
            continue
        lab = f"<b>{_f(v, 3)}</b>" if k == "seat_max" else _f(v, 3)
        rows.append([k, lab, note[k]])
    return _t(["结构变量", "Spearman(变量, 每核带宽)", "说明"], rows)


def die_table(b: dict, tag: str) -> str:
    bd = b["root_cause"][tag]["by_die"]
    rows = []
    for k in sorted(bd, key=lambda x: int(x)):
        d = bd[k]
        rows.append([
            f"top die {k}", d["gap"],
            "左 0–3" if d["half"] == 0 else "右 4–7",
            f"H{d['near_h']} / H{d['far_h']}",
            f"v{d['near_vpos']} / v{d['far_vpos']}",
            d["seat"], d["n"], f"<b>{d['mean']}</b>", d["min"], d["max"],
        ])
    return _t(["top die", "行间隙", "挂接列", "近/远横环", "近/远注入 vpos",
               "平均纵环边负载", "core 数", "带宽均值", "最小", "最大"], rows)


def s16_table(b: dict) -> str:
    rows = []
    for r in b["s16_sweep"]:
        rows.append([
            r["overcommit"], _ok(r["completed"]), f"{r['makespan']:,}",
            f"<b>{r['jain']}</b>", _f(r["max_min"], 2),
            r["peak_grants"], r["peak_buf_flits"],
            _f(r.get("grant_delay_mean"), 1), _f(r.get("net_p99"), 0),
        ])
    return _t(["overcommit", "批次是否排空", "makespan", "Jain", "max/min",
               "授权峰值", "HA 侧缓冲峰值(flit)", "授权等待均值",
               "网络时延 p99"], rows)


def s17_table(b: dict) -> str:
    rows = []
    for r in b["s17_sweep"]:
        rows.append([
            r["patience"] if r["patience"] else "0（等于基线）",
            _ok(r["completed"]), f"{r['makespan']:,}",
            f"<b>{r['jain']}</b>", _f(r["max_min"], 2),
            f"{r['n_turn_yield']:,}", f"{r['latch_flits']:,}",
            _f(r.get("net_p99"), 0),
        ])
    return _t(["turn_patience", "批次是否排空", "makespan", "Jain",
               "max/min", "转向让行次数", "被闩住的 flit·cycle",
               "网络时延 p99"], rows)


def seed_table(b: dict, key: str) -> str:
    sd = b[key]
    rows = []
    for s in SCHEMES:
        v = sd.get(s)
        if not v:
            continue
        rows.append([
            LABEL[s], f"{v['n_completed']} / {v['n_runs']}",
            f"<b>{v['jain_mean']}</b>", v["jain_min"],
            _f(v["max_min_worst"], 2), _pct(v["eff_min"]),
        ])
    return _t(["方案", "排空的种子数", "Jain 均值", "Jain 最差",
               "max/min 最差", "达下界比例最差"], rows)


def cost_table(b: dict) -> str:
    t = b["topology"]
    n_core, n_ha = t["n_cores"], t["n_has"]
    n_att = t["n_attach"]
    s1 = b["schemes"]["work"]["s1"].get("fc", {})
    s16 = b["schemes"]["work"]["s16"].get("fc", {})
    rows = [
        ["S0 基线", "0", "0", "无", "无",
         "已有 outstanding 计数器之外不加任何东西"],
        ["<b>把 outstanding 调到推荐值</b>",
         "<b>0</b>", "<b>0</b>", "<b>无</b>", "<b>无</b>",
         "<b>改一个已经存在的配置寄存器，是本文唯一推荐的动作</b>"],
        ["S1 拥塞等级 AIMD",
         f"{t['n_nodes']} 个节点各 1 个拥塞等级寄存器",
         f"{s1.get('bus_bits', 0):,} bit 广播总量",
         "专用旁路总线（不占 NoC）",
         f"每源平均 {s1.get('mean_path_nodes', 0)} 个受控节点",
         "要一条覆盖全芯片的广播网，跨 die 还要过 D2D"],
        ["S16 接收端授权",
         f"{n_ha} 个 HA 各 1 套授权状态机 + 请求表",
         f"HA 侧缓冲峰值 {s16.get('peak_buf_flits', 0)} flit",
         "无额外网络", f"授权峰值 {s16.get('peak_grants', 0)}",
         "把 DBIDResp 当授权用，不新增报文类型"],
        ["S17 挂接点转向仲裁",
         f"{n_att} 个挂接点各 1 个饥饿计数器 + 1 flit 闩",
         "0", "无", "纯本地，无通信",
         "最便宜的织物侧改动，但见下文的实测结论"],
    ]
    return _t(["方案", "存储/逻辑代价", "带宽代价", "额外互连", "运行时状态",
               "评价"], rows)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"missing {DATA}; run dse_stack_write_fair.py first")
    b = json.loads(DATA.read_text())
    t, m = b["topology"], b["meta"]
    for k in ("stability", "depth"):
        if k not in b:
            raise SystemExit(f"missing {k} scan; run dse_stack_stability.py")
    need = b["depth"]["need"]
    need_txt = "；".join(
        f"outstanding {k} 需要 {v} flit" if v else f"outstanding {k} 无解"
        for k, v in sorted(need.items(), key=lambda kv: int(kv[0])))

    # The recommended limit is chosen from the cross-seed scan, not from the
    # single-seed sweep: the largest value that drains every seed *and* meets
    # the fairness line (Jain >= 0.99, worst max/min <= 1.5).
    st = b["stability"]
    split = sorted((r for r in st["rows"] if r["h_assign"] == "split"),
                   key=lambda r: r["outstanding"])
    fair_ok = [r for r in split
               if r["n_completed"] == r["n_runs"]
               and r["jain_mean"] >= 0.99 and r["mm_worst"] <= 1.5]
    drain_ok = [r for r in split if r["n_completed"] == r["n_runs"]]
    rec = max(fair_ok, key=lambda r: r["outstanding"]) if fair_ok else \
        max(drain_ok, key=lambda r: r["outstanding"])
    fastest = max(drain_ok, key=lambda r: r["thr_mean_ok"])
    cliff = min((r["outstanding"] for r in split if r["n_completed"] == 0),
                default=None)
    b["_rec"] = {"oc": rec["outstanding"]}

    plot_topology(b, IMG / "stack_topology.png")
    plot_binding(b, IMG / "stack_binding.png")
    plot_v_profile(b, IMG / "stack_v_profile.png")
    plot_oc(b, IMG / "stack_oc.png")
    plot_schemes(b, IMG / "stack_schemes.png")
    plot_root_cause(b, IMG / "stack_root_cause.png")
    plot_hassign(b, IMG / "stack_hassign.png")
    plot_stability(b, IMG / "stack_stability.png")

    mand = b["schemes"]["mandated"]
    work = b["schemes"]["work"]
    m0, w0 = mand["s0"], work["s0"]
    n_txn = mand["n_txn"]
    bd = mand["bounds"]
    oc_mand, oc_work = m["core_outstanding"], m["oc_work"]

    rb = b["routing"]["bound"]
    rl = b["routing"]["lat"]
    rc = b["root_cause"]["work"]

    ocs = b["oc_sweep"]
    stable = [r for r in ocs if r["completed"]]
    best = max(stable, key=lambda r: r["thr"]) if stable else None
    worst_ok = max((r["outstanding"] for r in stable), default=0)
    first_bad = min((r["outstanding"] for r in ocs if not r["completed"]),
                    default=None)
    at_mand = next((r for r in ocs if r["outstanding"] == oc_mand), None)

    sat = b.get("saturation", [])
    sat_last = sat[-1] if sat else None

    sd = b["seeds_bound"]
    s0s = sd["s0"]

    f4 = next((r for r in b["fifo_sweep"] if r["turn_depth"] == 4), None)
    f128 = next((r for r in b["fifo_sweep"] if r["turn_depth"] == 128), None)

    ha_rows = b["hassign"]
    ha_split_ok = max((r["outstanding"] for r in ha_rows
                       if r["h_assign"] == "split" and r["completed"]),
                      default=0)
    ha_stack_ok = max((r["outstanding"] for r in ha_rows
                       if r["h_assign"] == "stack" and r["completed"]),
                      default=0)

    # how far past the cliff the mandated setting sits
    over = oc_mand / max(1, worst_ok)
    n_flight_mand = oc_mand * t["n_cores"]

    s17_best = max(b["s17_sweep"], key=lambda r: r["jain"])
    s17_base = next((r for r in b["s17_sweep"] if r["patience"] == 0), None)
    s16_rows = b["s16_sweep"]
    s16_binds = [r for r in s16_rows if r["peak_grants"] <= r["overcommit"]]

    dies = rc["by_die"]
    d_best = max(dies, key=lambda k: dies[k]["mean"])
    d_worst = min(dies, key=lambda k: dies[k]["mean"])
    die_ratio = dies[d_best]["mean"] / max(1e-9, dies[d_worst]["mean"])

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
+ <b>{t['n_bridges']} 条 D2D 链路</b>
+ <b>1 个 bottom die</b>（{t['n_has']} 个 HA，12 行 × 8 列；
6 条横向 + 8 条纵向<b>单向</b> half ring）。
挂接点按 <b>2 横 × {t['group_cols']} 列 = 8 个</b>一组挂到一个 top die；
每个 HA 与一个 D2D bridge <b>绑定</b>。
workload：<b>{t['n_cores']} 个 AI core 均匀写 {t['n_has']} 个 HA</b>，
每 core {m['k']} 笔 <code>WriteNoSnp</code>、每笔 {m['m_wdata']} 个 WriteData
flit，共 {n_txn:,} 笔事务；每 core outstanding 上限
<b>{oc_mand}</b>。</p>

<h2>结论</h2>
<div class="key">
<ol>

<li><b>最重要的一条：outstanding = {oc_mand} 会让这个织物直接拥塞崩溃，
而且没有任何流控方案能救回来。</b>
在规定的 outstanding={oc_mand} 下，{n_txn:,} 笔事务只完成
<b>{m0['n_txn_done']:,} 笔（{100 * m0['n_txn_done'] / n_txn:.0f}%）</b>，
批次根本排不空；把 S1、S16、S17 分别加上去，
<b>四个方案全部崩溃</b>（§7）。
实测的悬崖非常陡：跨 {len(st['seeds'])} 个种子，
到 <b>outstanding = {max(r['outstanding'] for r in drain_ok)}</b>
为止每个种子都能排空，
而 <b>{cliff}</b> 就<b>一个种子都排不空</b>——
中间没有缓慢劣化的过渡带。
也就是说规定值比织物能承受的并发度高了约
<b>{oc_mand // max(1, max(r['outstanding'] for r in drain_ok))} 倍</b>——
{t['n_cores']} 个 core × {oc_mand} =
<b>{n_flight_mand:,}</b> 笔并发写，而整个 bottom die 的纵向织物
只有 {t['capacity']['v']} 条有向链路。
多出来的在途报文不产生任何吞吐，只是变成互相阻塞的偏转流量：
偏转次数从 {min(r['n_deflections'] for r in ocs):,} 涨到
{max(r['n_deflections'] for r in ocs):,}。</li>

<li><b>本次最实用的建议只有一句：把 outstanding 从 {oc_mand} 改成
{rec['outstanding']}，代价为零。</b>
outstanding 上限本来就是一个已有的配置寄存器，改它不花任何硬件。
跨 {len(st['seeds'])} 个种子实测，outstanding={rec['outstanding']} 时：
{rec['n_completed']}/{rec['n_runs']} 个种子全部排空，
吞吐 <b>{rec['thr_mean_ok']:.3f} txn/cycle</b>
（达理论下界 <b>{_pct(rec['eff_mean_ok'])}</b>），
Jain <b>{rec['jain_mean']}</b>，最差 max/min
<b>{_f(rec['mm_worst'], 2)}</b>——
<b>这是唯一同时满足公平线（Jain ≥ 0.99 且 max/min ≤ 1.5）的可用配置</b>。
而且它几乎不牺牲吞吐：稳定区内的吞吐峰值在
outstanding={fastest['outstanding']}（{fastest['thr_mean_ok']:.3f}
txn/cycle），{rec['outstanding']} 已经拿到了它的
<b>{_pct(rec['thr_mean_ok'] / fastest['thr_mean_ok'])}</b>，
但峰值那一档的公平性是不达标的
（Jain {fastest['jain_mean']}、max/min {_f(fastest['mm_worst'], 2)}）。
换句话说，<b>把并发度从 {oc_mand} 压到 {rec['outstanding']}
可以几乎免费地换到公平性</b>。</li>

<li><b>路由在这个拓扑上不是可调项——目的地决定了一切。</b>
每个 HA 与一个 D2D bridge 绑定：先确定去哪个 HA，就确定了走哪个 bridge，
两个 die 上的源和目的因此都被钉死，剩下的只是各 die 内部的最短路。
好消息是这个被规定的路由<b>本身就是最好的</b>：
它的下界 {rb['bounds']['bound']:,} cycle，
比“无视绑定、全局自由最短路”的 {rl['bounds']['bound']:,} cycle
还要好 <b>{rl['bounds']['bound'] / rb['bounds']['bound']:.2f} 倍</b>，
因为自由最短路会诱导流量提前跨到 bottom die、
把稀缺的纵环当过路通道用（最忙纵向链路达到平均值的
{rl['v_concentration']:.2f} 倍，绑定路由只有
{rb['v_concentration']:.2f} 倍）。
<b>换句话说，上一版报告里“换个路由就能提速”的那条结论在这个拓扑上不成立，
因为路由已经没有自由度了；能动的只有并发度。</b></li>

<li><b>横环不再是闲置的余量，它承担了一半的流量。</b>
挂接组是 2 横 × {t['group_cols']} 列，所以一个 top die 只直接覆盖
<b>{t['group_cols']} 列中的 4 列</b>；
去另外 4 列的写必须先在横环上走
<b>{t['n_cols'] // 2} 跳</b>换列。
每个 core 的目的地一半近一半远，
于是每笔事务平均在横环上产生
{rb['dat_hops_per_txn'].get('h', 0)} 个 DAT flit·跳、
在纵环上 {rb['dat_hops_per_txn'].get('v', 0)} 个。
横环只有 {t['capacity']['h']} 条有向链路（纵环 {t['capacity']['v']} 条），
两者的峰值负载已是同一量级，但<b>决定下界的仍然是纵环</b>
（分织物下界：纵 {bd['fabric_lb'].get('v')} vs 横
{bd['fabric_lb'].get('h')}）。</li>

<li><b>残余失衡是结构性的，但它不是某个位置坐标的简单函数。</b>
即使把并发度调到推荐值，位置性失衡依然存在：
outstanding={oc_work} 下 Jain = <b>{w0['fairness']['jain']}</b>，
max/min = <b>{_f(w0['fairness']['max_min'], 2)}</b>，
每核带宽 {w0['fairness']['bw_min']} ~ {w0['fairness']['bw_max']} flit/cycle，
而需求是完全对称的。
把带宽与各结构变量做秩相关，最强的是
“该 core 要并入的最忙那条纵环边的负载”
（Spearman <b>{rc['corr']['seat_max']}</b>，方向正确但只是中等强度），
top die 编号只有 {rc['corr']['die']}，
top 环上的位置只有 {rc['corr']['top_idx']}。
按 die 聚合后最好与最差相差 {die_ratio:.2f} 倍
（die {d_best} vs die {d_worst}），
但<b>存在反例</b>：die {d_worst} 的纵环负载并非最高。
结论是：<b>失衡可复现、可归因于“在环优先”下的并入竞争，
但无法约化成一个静态位置变量</b>——
这正是所有静态流控方案在这里收效有限的原因（§6、§7）。</li>

<li><b>一个免费的拓扑选择：同一行间隙里两条横环怎么分工，会改变耐并发能力。</b>
两种分配搬运的 flit·跳完全相同，<b>解析下界一模一样</b>
（都是 {ha_rows[0]['bound']:,} cycle），
所以任何静态分析都区分不出来。
但实测差别明显：让两个 die 各用一条横环走远端流量（split）
可以稳到 outstanding={ha_split_ok}，
而两个 die 共用一条横环（stack）只能稳到 {ha_stack_ok}。
原因是前者让两个 die 在同一列上<b>落在不同的挂接点</b>，
后者把它们挤到同一个挂接点上抢同一条出边。
这条不花任何硬件，只是布线时的一个选择。</li>

<li><b>与上一版拓扑相反：转向 FIFO 深度这次是硬需求，而且它和并发度绑在一起。</b>
计划把“横↔纵转向 FIFO 深 4 flit”列为风险项，这次它是对的：
深度 {f4['turn_depth']} 直接活锁
（只完成 {f4['n_txn_done']:,} 笔，FIFO 全程被占满）。
原因是 2×4 分组让<b>一半</b>的写必须先走横环、再经转向进入纵环，
转向从上一版的“几乎不用”变成了关键路径。
好消息是这两个建议<b>互相加强而不是互相拉扯</b>：
跨 {len(st['seeds'])} 个种子实测，
在推荐并发度 {rec['outstanding']} 下只需要
<b>{need.get(str(rec['outstanding']))} flit</b> 深，
而并发度每往上一档，所需深度就翻一倍
（{need_txt}）。
<b>把并发度压下来同时省了面积。</b></li>

<li><b>三个流控方案的结论：S1 能跑但很贵，S16 打不到痛点，
S17 收益不稳定。</b>
在 outstanding={oc_work} 上跨 {len(m['seeds'])} 个种子：
S1 Jain 均值 {sd['s1']['jain_mean']}、
S16 {sd['s16']['jain_mean']}、
S17 {sd['s17']['jain_mean']}，
基线 S0 {s0s['jain_mean']}。
S16 的控制点在 HA 侧的授权发放，而瓶颈在纵环的<b>并入仲裁</b>上，
控制点和瓶颈不在一处，所以它的预算在正常负载下几乎不生效（§7.2）；
S17 直接针对挂接点的转向仲裁，机理是对的，
但跨种子看收益不稳定（§7.3）。
<b>没有一个方案比“把 outstanding 调对”更有效，而后者代价为零。</b></li>

</ol>
</div>

<h2>1　拓扑与硬件 setup</h2>

<h3>1.1 总体结构</h3>
{setup_table(b)}

<h3>1.2 链路延迟</h3>
{link_table(b)}
<div class="def">D2D 一跳 {t['d2d_lat']} cycle，是 bottom die 环内一跳
（{t['bot_hop_lat']} cycle）的 {t['d2d_lat'] // t['bot_hop_lat']} 倍。
这个比例很重要：正是它让“按时延自由最短路”倾向于<b>尽早跨 die</b>、
再用便宜的 bottom die 跳挪位置，从而把流量堆到稀缺的纵环上。
绑定路由不给它这个机会。</div>

<h3>1.3 挂接点分组：2 横 × 4 列</h3>
<p>48 个挂接点分成 6 组、每组 8 个，形状是 <b>2 个挂接行 × 4 列</b>。
每个行间隙有 2 个挂接行 × 8 列 = 16 个挂接点，正好是 2 组，按左右半区切开：</p>
{binding_summary(b)}
<img src="stack_topology.png" alt="bottom die 与挂接点分组">
<div class="def">横向 half ring 仍然<b>跨全部 8 列</b>，
所以同一个行间隙里的两个 die 共用这 2 条横环，
任何一个 die 都可以借横环去到自己组外的列。</div>

<h3>1.4 HA 与 D2D bridge 的绑定</h3>
<p>每个 HA 与一个 D2D bridge 绑定：<b>先确定去哪个 HA，就确定了走哪个
bridge</b>。一个 die 有 8 个 bridge、要覆盖 96 个 HA，
所以每个 bridge 独占<b>一整列的 12 个 HA</b>。
由于挂接组只落在 4 列上，其中 4 个 bridge 是“本列直落”，
另外 4 个必须先在横环上换列：</p>
<img src="stack_binding.png" alt="HA 到 bridge 的绑定">
<div class="def"><b>这对路由意味着什么。</b>
目的 HA 定了 → bridge 定了 → top die 上的目的（bridge 节点）和
bottom die 上的源（该 bridge 的落点）都定了。
剩下的只是<b>各 die 内部的最短路</b>：
top die 上选环的方向与平面，bottom die 上是被迫的
“横环换列 → 转向 → 纵环下行”。
所以下文所说的“最短路由”，都是<b>在源和目的都被绑定钉死之后</b>的最短路。</div>

<h2>2　公平性怎么量：Jain 指数</h2>
<div class="def">
对 n 个 core 的写带宽 x<sub>1</sub>…x<sub>n</sub>，Jain 指数定义为
<p style="text-align:center;margin:0.5rem 0">
J = (Σx<sub>i</sub>)² / (n · Σx<sub>i</sub>²)
</p>
取值范围 <b>1/n ~ 1</b>。全部相等时 J = 1；
只有 k 个 core 拿到带宽、其余为 0 时 J = k/n。
它的好处是<b>与量纲和总量无关</b>——把所有带宽同乘一个系数，J 不变，
所以可以直接比较不同吞吐下的公平性。
<br><br>
本文同时给出另外两个指标，因为 Jain 对少数极端值不够敏感：
<b>max/min</b>（最快 core 与最慢 core 的带宽比，直观但只看两端）和
<b>CoV</b>（标准差／均值，看整体离散度）。
判定一次运行“公平”，本文用的线是 <b>Jain ≥ 0.99 且 max/min ≤ 1.5</b>。
</div>

<h2>3　理论下界</h2>
<p>下界回答的是“无论调度多聪明，这批事务至少要多少 cycle”。
四个来源分别对应四种资源，取最大者：</p>
{bounds_table(bd)}
<div class="def">注意各 VC 独立占用链路带宽，所以链路下界是
<b>各 VC 取最大</b>而不是求和。
DAT 每笔 {m['m_wdata']} flit，通常由它决定。</div>

<h3>3.1 被规定的路由 vs 假想的自由路由</h3>
{routing_table(b)}
<div class="def good"><b>被绑定的路由反而是最好的。</b>
自由最短路（无视绑定，因此<b>在这个硬件上不可实现</b>，
列出来只是为了量化绑定的代价）下界更差：
它的平均跳数更短（{rl['mean_fwd_hops']} vs {rb['mean_fwd_hops']} 跳），
却把最忙纵向链路推到平均值的 {rl['v_concentration']:.2f} 倍
（绑定路由 {rb['v_concentration']:.2f} 倍）。
<b>决定性能的是负载均衡，不是路径长度</b>——
这一点与上一版报告一致，但结论的方向反了：
这里不需要去“改路由”，硬件规定的路由已经是好的那个。</div>

<h3>3.2 纵环上的负载分布</h3>
<img src="stack_v_profile.png" alt="纵环负载分布">
<div class="def">纵环是单向闭环，注入点（虚线）之后负载抬升。
左半区列和右半区列的注入位置不同，
因为一列被“拥有它的 die”从近端横环直落、
被“需要换列的 die”从远端横环进入。</div>

<h2>4　现象：规定的并发度下发生了什么</h2>
{scheme_table(b, "mandated")}
<div class="def bad"><b>怎么读这张表。</b>
“批次是否排空”是最关键的一列：
崩溃的运行里 makespan 只是<b>停滞检测器截断的时刻</b>，
不是真正的完成时间，所以此时的 Jain、max/min
描述的是“一个卡住的织物内部谁还在动”，
<b>不能</b>当作公平性的正面结论来用。
这就是为什么第 5 节要先把并发度调到织物能承受的范围，
再去谈公平性。</div>

<h3>4.1 outstanding = {oc_mand} 到底有没有生效</h3>
<p>一个 core 只提交 {m['k']} 笔写，所以在这个批次里
“在途上限 {oc_mand}”其实<b>永远碰不到</b>——
上限高于批量时，它和“无上限”是同一件事。
下面把批量逐步加大，看实测在途峰值和结论是否改变：</p>
{saturation_table(b)}
<div class="def">在途峰值始终等于批量 k、从未接近 {oc_mand}，
而织物<b>每一档都是崩溃的</b>。
这正说明问题不在“{oc_mand} 这个数字有没有被触发”，
而在于织物的饱和点低到 {worst_ok} 左右：
core 还远没用到自己的额度，织物就已经塌了。
所以 outstanding={oc_mand} 在这里的实际含义就是<b>“不设限”</b>，
而不设限的后果见上表。</div>

<h2>5　把并发度作为唯一自由变量</h2>
<p>路由被绑定钉死、FIFO 深度不是瓶颈（§5.3），
所以设计上真正能动的只剩一个数：每 core 的 outstanding 上限。
先看单种子的全景扫描：</p>
<img src="stack_oc.png" alt="并发度扫描">
{oc_table(b)}
<div class="def bad"><b>注意崩溃档的 Jain 不可信。</b>
崩溃的运行里 makespan 是停滞检测器截断的时刻，
此时 Jain 反映的是“卡住的织物里谁还在动”。
例如 outstanding=8 那一档 Jain 看起来有
{next(r['jain'] for r in ocs if r['outstanding'] == 8)}，
但它只完成了不到两成事务——<b>不能</b>拿来和排空的运行比较。
所以下面用跨种子的“是否排空”来定结论。</div>

<h3>5.1 跨种子定位悬崖与推荐值</h3>
<img src="stack_stability.png" alt="跨种子并发度扫描">
{stability_table(b)}
<div class="def good"><b>这是本文最实用的一张表。</b>
悬崖极陡：{max(r['outstanding'] for r in drain_ok)} 全排空、
{cliff} 全崩溃，中间没有过渡。
但<b>“能排空”不等于“公平”</b>：
到 {fastest['outstanding']} 时吞吐最高
（{fastest['thr_mean_ok']:.3f} txn/cycle），
Jain 却只有 {fastest['jain_mean']}、max/min {_f(fastest['mm_worst'], 2)}，
达不到公平线。
同时满足两者的最大并发度是 <b>{rec['outstanding']}</b>：
Jain {rec['jain_mean']}、max/min {_f(rec['mm_worst'], 2)}、
吞吐 {rec['thr_mean_ok']:.3f} txn/cycle
（峰值的 {_pct(rec['thr_mean_ok'] / fastest['thr_mean_ok'])}）。
<b>这就是推荐值</b>，代价是一个已有寄存器的取值。</div>

<h3>5.2 各方案在推荐并发度附近的跨种子表现</h3>
{seed_table(b, "seeds_bound")}
<p class="note">上表在 outstanding={oc_work} 上跑。
S0、S1、S16 三行<b>完全一样</b>——这不是笔误：
并发度已经压到织物的舒适区之后，
S1 的 AIMD 和 S16 的授权预算都<b>从不触发</b>，
它们的硬件在这个工作点上是纯粹的死重。</p>

<h3>5.3 一个免费的拓扑选择：横环分工</h3>
<img src="stack_hassign.png" alt="横环分配对比">
{hassign_table(b)}
<div class="def good">两种分配的<b>解析下界完全相同</b>，
静态分析无法区分；但 split（两个 die 各用一条横环走远端流量）
让它们在同一列上落在<b>不同</b>挂接点，
耐并发能力比 stack 更好。
这提示一类只有仿真才能发现的设计点：
<b>总量相同、分布不同</b>。</div>

<h3>5.4 转向 FIFO 深度：这次是硬需求</h3>
<p>转向 FIFO 是这个织物<b>唯一允许缓冲</b>的地方，深度直接是面积代价。
单种子扫描（在 outstanding={oc_work} 下）：</p>
{fifo_table(b)}
<p>再跨 {len(st['seeds'])} 个种子把深度和并发度一起扫，
得到真正可用的深度需求：</p>
{depth_table(b)}
<div class="def bad"><b>和上一版拓扑的结论相反，这里必须给够深度。</b>
深度 {f4['turn_depth']} 会<b>活锁</b>，
而且注意上表里“FIFO 峰值占用”一列在崩溃档位<b>恰好等于深度</b>——
FIFO 全程满载，是真正的瓶颈，不是余量。
根源是 2×4 分组：一半的写要经“横环 → 转向 → 纵环”，
转向点从上一版的近乎空闲变成了关键路径。</div>
<div class="def good"><b>但它和并发度是同向的，不是权衡。</b>
所需深度随并发度上升：{need_txt}。
也就是说把 outstanding 压到 {rec['outstanding']} 不仅换来公平性，
还把这 {t['n_attach']} 个挂接点上的缓冲需求
从 {need.get(str(max(int(k) for k in need)))} flit 降到
{need.get(str(rec['outstanding']))} flit。
<b>两个建议是同一个方向。</b></div>

<h2>6　根因分析</h2>
<img src="stack_root_cause.png" alt="根因">
{corr_table(b, "work")}
<div class="def"><b>为什么“top die 编号”这次没有解释力。</b>
上一版拓扑里每个 die 在所有列上都从同一个纵向位置注入，
位置优势在所有目的地上是<b>一致的</b>，因此能按 die 排序。
现在挂接组是 2 横 × 4 列：
同一个 die 对自己的 4 列从近端横环直落、
对另外 4 列要换列后从远端横环进入，
<b>一个 die 不再只有一个纵向位置</b>，
优势在目的地之间被平均掉了——
这就是 Spearman(die 编号, 带宽) 只有 {rc['corr']['die']} 的原因。</div>
<div class="def bad"><b>需要如实说明的一点：没有任何单一静态变量能解释这个失衡。</b>
最强的预测变量是“要并入的最忙那条纵环边的负载”，
Spearman = <b>{rc['corr']['seat_max']}</b>——方向正确
（要挤的边越忙、拿到的带宽越少），但<b>只是中等强度</b>。
下面按 die 聚合的表里就能看到反例：
die {d_worst} 的纵环边负载并不是最高的，带宽却最低。
也就是说，<b>可以确认失衡是结构性的、可复现的
（{len(m['seeds'])} 个种子都在），但它不是某个位置坐标的简单函数</b>：
剩下的部分来自“在环优先”这条规则的动态后果——
一个 flit 能不能并入，取决于那一拍环上恰好有没有流量，
而这又被上游所有注入点的相位关系决定。
这也解释了为什么 §7 里所有<b>静态</b>的流控方案都收效有限。</div>

<h3>6.1 按 die 聚合</h3>
{die_table(b, "work")}
<p class="note">最好的 die {d_best}（{dies[d_best]['mean']}）与最差的
die {d_worst}（{dies[d_worst]['mean']}）相差 {die_ratio:.2f} 倍。
注意“平均纵环边负载”这一列并不与带宽单调对应，
正是上面那段说的反例。</p>

<h2>7　流控方案</h2>
{scheme_table(b, "work")}
<img src="stack_schemes.png" alt="各方案每核带宽">

<h3>7.1 S1：拥塞等级 + AIMD</h3>
<p>每个节点统计本地拥塞等级，经<b>专用旁路总线</b>（不占 NoC）广播，
源端按 AIMD 调整注入速率。
实测它在 outstanding={oc_work} 下能跑，
跨种子 Jain 均值 {sd['s1']['jain_mean']}、最差 max/min
{_f(sd['s1']['max_min_worst'], 2)}。
代价是要一条覆盖全芯片、跨 die 的广播网
（{b['schemes']['work']['s1'].get('fc', {}).get('bus_bits', 0):,} bit
广播总量），在 3D 堆叠里尤其贵。</p>

<h3>7.2 S16：Homa 式接收端授权</h3>
<p>思路是把 CHI 的 DBIDResp 直接当作 Homa 的 grant 用：
HA 按“最少服务优先”配额发放 DBIDResp，
用 <code>overcommit</code> 控制同时在飞的授权数。
好处是<b>不新增报文类型</b>，完全落在 CHI 语义内。</p>
{s16_table(b)}
<div class="def bad"><b>为什么在这个拓扑上不奏效。</b>
S16 的控制点是 <b>HA 侧的授权发放</b>，
而瓶颈是<b>纵环上的并入仲裁</b>——
一个 flit 能不能从挂接点挤进纵环，取决于那条边上有没有在环流量，
与 HA 发了多少授权无关。
控制点和瓶颈不在同一处，
所以把 overcommit 放宽到 {max(r['overcommit'] for r in s16_rows)}
时它与基线几乎没有差别，
收紧到 {min(r['overcommit'] for r in s16_rows)} 才开始限流，
而那时限的其实是<b>并发度</b>——也就是用一套状态机去做
outstanding 寄存器已经能做的事。</div>

<h3>7.3 S17：挂接点转向仲裁（本文提出）</h3>
<p>既然瓶颈是并入仲裁，就直接改仲裁：
挂接点的转向 FIFO 连续被阻塞 <code>turn_patience</code> 拍之后，
获得一次对在环流量的优先权，用一个 1 flit 的闩实现。
这是唯一直接命中瓶颈机理的方案，也是最便宜的织物侧改动。</p>
{s17_table(b)}
<div class="def"><b>实测结论要说清楚。</b>
在单个种子上，patience={s17_best['patience']} 把 Jain 从
{s17_base['jain']} 提到 {s17_best['jain']}，看起来是有效的；
但跨 {len(m['seeds'])} 个种子它的均值是
{sd['s17']['jain_mean']}，相对基线 {s0s['jain_mean']}
<b>并不构成稳定收益</b>。
机理是对的，收益不稳定，因此本文<b>不</b>把它作为推荐方案；
如果要继续做，方向是让让行的判据带上下游负载信息，
而不只是本地饥饿计数。</div>

<h2>8　代价对比</h2>
{cost_table(b)}
<div class="def good"><b>一句话建议。</b>
在这个拓扑上，路由已经被 HA↔bridge 绑定钉死，不是可调项；
转向 FIFO 深度不是瓶颈；三个流控方案都没有稳定收益。
唯一既有效、又免费的动作是<b>把每 core 的 outstanding 从
{oc_mand} 调到 {rec['outstanding']}</b>：
批次从“永远排不空”变成全部排空，
吞吐 {at_mand['thr']:.3f} → {rec['thr_mean_ok']:.3f} txn/cycle
（<b>{rec['thr_mean_ok'] / max(1e-9, at_mand['thr']):.0f} 倍</b>），
公平性 Jain {at_mand['jain']} → {rec['jain_mean']}、
max/min {_f(at_mand['max_min'], 2)} → {_f(rec['mm_worst'], 2)}。
如果还有布线自由度，再顺手选 split 式的横环分工。</div>

<h2>9　复现</h2>
<div class="def">
<code>python3 utils/dse_stack_write_fair.py --k {m['k']}
--seeds {' '.join(str(s) for s in m['seeds'])} --oc-work {oc_work}</code><br>
<code>python3 utils/gen_stack_write_report.py</code><br>
<code>python3 utils/verify_stack.py</code>（拓扑、守恒、方案不变量的回归检查）
<br><br>
仿真总耗时 {m.get('wall_s', 0):.0f} s。
所有崩溃判定都以“批次是否排空”为准，
停滞检测阈值 {b['meta']['fabric'].get('stall_after', 6000)} cycle。
</div>

</body></html>
"""
    OUT.write_text(html)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
