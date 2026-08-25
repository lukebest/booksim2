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
LABEL = {"s0": "S0 基线（无源端流控）", "s1": "S1 源端 AIMD 控速",
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
    axes[0].set_title("可靠性：能不能排空", fontsize=10.5)
    axes[0].set_ylim(-0.2, nseed + 0.3)
    axes[1].set_ylabel("txn/cycle（仅统计排空的运行）", fontsize=9)
    axes[1].set_title("吞吐", fontsize=10.5)
    axes[2].set_ylabel(f"Jain 指数（{nseed} 种子均值）", fontsize=9)
    axes[2].set_title("公平性", fontsize=10.5)
    axes[2].axhline(0.99, color="#2563eb", ls=":", lw=1.3)
    axes[2].text(max(r["outstanding"] for r in st), 0.9903,
                 "公平线 0.99", fontsize=7.8, ha="right", va="bottom",
                 color="#2563eb")
    # everything to the right of this drains only on some seeds, so the
    # throughput drawn there is survivor-biased and must not be read as a gain
    edge = max((r["outstanding"] for r in st
                if r["h_assign"] == "split"
                and r["n_completed"] == r["n_runs"]), default=0)
    hi = max(r["outstanding"] for r in st)
    rec = b["_rec"]["oc"]
    for ax in axes:
        ax.axvspan(edge, hi, color="#fca5a5", alpha=0.20, zorder=0)
        ax.axvline(rec, color="#2563eb", ls="--", lw=1.2)
        ax.set_xlabel("每 core outstanding 上限", fontsize=9)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8, loc="best")
    axes[1].text((edge + hi) / 2, min(r["thr_mean_ok"] for r in st
                                      if r["n_completed"]),
                 "红区：只有部分种子排空，\n这里的吞吐是“幸存者偏差”",
                 fontsize=7.6, ha="center", va="bottom", color="#b91c1c")
    fig.suptitle("跨 %d 个随机种子扫并发度：蓝色虚线是推荐的 %d；"
                 "红色区域（>%d）已经无法保证排空" % (nseed, rec, edge),
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


def plot_retry(b: dict, path: Path) -> None:
    """Nominal outstanding is not effective outstanding."""
    rows = [r for r in b["retry_sweep"] if r["pos_depth"] > 0]
    rows = sorted(rows, key=lambda r: r["pos_depth"])
    unl = next((r for r in b["retry_sweep"] if r["pos_depth"] == 0), None)
    xs = [r["pos_depth"] for r in rows]
    _cjk()
    fig, axes = plt.subplots(1, 3, figsize=(14.4, 4.3))

    ax = axes[0]
    ax.plot(xs, [100 * r["eff_frac"] for r in rows], "o-", color="#2563eb",
            lw=1.5, ms=6, label="有效并发 / 名义并发")
    if unl:
        ax.axhline(100, color="#94a3b8", ls="--", lw=1.2,
                   label="完成方不受限时（=100%）")
    ax.set_ylabel("有效并发占名义并发的比例 (%)", fontsize=9)
    ax.set_ylim(0, 108)
    ax.legend(fontsize=8, loc="lower right")

    ax = axes[1]
    ax.plot(xs, [r["park_mean"] for r in rows], "o-", color="#dc2626",
            lw=1.5, ms=6, label="平均等待 P-Credit 的时间")
    ax.plot(xs, [r["park_p99"] for r in rows], "s--", color="#f59e0b",
            lw=1.3, ms=5, label="p99")
    ax.set_ylabel("被挂起的时间 (cycle)", fontsize=9)
    ax.legend(fontsize=8)

    ax = axes[2]
    for r in rows:
        ax.plot([r["pos_depth"]], [r["thr"]], "o", ms=9,
                color="#16a34a" if r["completed"] else "white",
                mec="#16a34a", mew=1.6, zorder=3)
    ax.plot(xs, [r["thr"] for r in rows], "-", color="#16a34a", lw=1.4)
    if unl:
        ax.plot([max(xs)], [unl["thr"]], "x", ms=10, color="#64748b",
                mew=2, label="完成方不受限")
        ax.legend(fontsize=8)
    ax.set_ylabel("吞吐 (txn/cycle)", fontsize=9)

    for ax in axes:
        ax.set_xscale("log", base=2)
        ax.set_xticks(xs)
        ax.set_xticklabels([str(v) for v in xs], fontsize=8)
        ax.set_xlabel("每个 HA 的请求跟踪表项数", fontsize=9)
        ax.grid(alpha=0.25)
    fig.suptitle("名义 outstanding 恒为 %d。跟踪表越浅，RetryAck 越多，"
                 "被挂起而不推进的请求越多——实心点 = 批次排空"
                 % b["meta"]["core_outstanding"], fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_scenario(b: dict, path: Path) -> None:
    """The safe range at full load does not overlap the useful range light."""
    sc = b["scenario"]
    rows, best = sc["rows"], sc["best_static"]
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(13.0, 4.5))
    cols = {"all 6 dies": "#dc2626", "3 dies": "#f59e0b", "1 die": "#2563eb"}

    ax = axes[0]
    for lbl in sc["scenarios"]:
        sub = sorted((r for r in rows if r["scenario"] == lbl
                      and r["scheme"] == "s0"),
                     key=lambda r: r["outstanding"])
        bt = max(1e-9, best.get(lbl, {}).get("thr", 0.0))
        c = cols.get(lbl, "#64748b")
        n = sub[0]["n_cores"] if sub else 0
        ax.plot([r["outstanding"] for r in sub],
                [100 * r["thr"] / bt for r in sub], "-", color=c, lw=1.5,
                label=f"{lbl}（{n} 个 core 有流量）")
        for r in sub:
            ax.plot([r["outstanding"]], [100 * r["thr"] / bt], "o", ms=7,
                    color=c if r["completed"] else "white", mec=c, mew=1.5,
                    zorder=3)
    ax.axhline(100, color="#94a3b8", ls=":", lw=1.1)
    ax.set_ylabel("吞吐（占该场景最好静态配置的 %）", fontsize=9)
    ax.set_title("同一个静态 outstanding，在两个场景里要求相反", fontsize=10)
    ax.legend(fontsize=8, loc="lower left")

    ax = axes[1]
    worst = sc["worst_rel"]
    st = [(k, v) for k, v in worst.items() if k.startswith("static_")]
    dy = [(k, v) for k, v in worst.items() if not k.startswith("static_")]
    labs = [k.replace("static_oc", "静态 oc=") for k, _ in st] + \
           [k.replace("_slack", " slack=").upper() for k, _ in dy]
    vals = [100 * v for _, v in st] + [100 * v for _, v in dy]
    cs = ["#94a3b8"] * len(st) + ["#16a34a"] * len(dy)
    ax.barh(range(len(vals)), vals, color=cs)
    ax.set_yticks(range(len(vals)))
    ax.set_yticklabels(labs, fontsize=8)
    ax.invert_yaxis()
    for i, v in enumerate(vals):
        ax.text(v + 1.2, i, f"{v:.0f}%", va="center", fontsize=8)
    ax.set_xlabel("最差场景下能拿到的吞吐（占该场景最好静态配置的 %）",
                  fontsize=9)
    ax.set_xlim(0, max(vals) * 1.22)
    ax.set_title("跨场景的下限：静态值（灰）vs 自适应（绿）", fontsize=10)

    for ax in (axes[0],):
        ax.set_xscale("log")
        xs = sorted({r["outstanding"] for r in rows if r["scheme"] == "s0"})
        ax.set_xticks(xs)
        ax.set_xticklabels([str(v) for v in xs], fontsize=8)
        ax.set_xlabel("每 core outstanding 上限", fontsize=9)
    for ax in axes:
        ax.grid(alpha=0.25, axis="x")
    fig.suptitle("空心点 = 批次没排空。自适应方案在两个场景都没有重新配过参数",
                 fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_group(b: dict, path: Path) -> None:
    """Write bandwidth per top die, which is where the asymmetry is visible."""
    g = b["group"]
    ocs = sorted({r["outstanding"] for r in g}, reverse=True)
    _cjk()
    fig, axes = plt.subplots(1, len(ocs), figsize=(7.2 * len(ocs), 5.1),
                             squeeze=False)
    for ax, oc in zip(axes[0], ocs):
        sub = [r for r in g if r["outstanding"] == oc]
        dies = sorted(sub[0]["goodput_by_group"], key=int) if sub else []
        w = 0.8 / max(1, len(sub))
        for i, r in enumerate(sub):
            vals = [r["goodput_by_group"][d] for d in dies]
            ax.bar([j + i * w for j in range(len(dies))], vals, w,
                   label="%s%s（max/min %s）"
                         % (r["scheme"].upper(),
                            "" if r["completed"] else " 崩溃",
                            _f(r["goodput_max_min"], 2)))
        ax.set_xticks([j + 0.4 - w / 2 for j in range(len(dies))])
        ax.set_xticklabels([f"die {d}" for d in dies], fontsize=8)
        ax.set_ylabel("该 die 10 个 core 合计的写吞吐 (flit/cycle)", fontsize=9)
        # A 88x spread is invisible on a linear axis next to a scheme that
        # drained, and the spread is the whole point of this panel.
        wide = max((r["goodput_max_min"] for r in sub
                    if r["goodput_max_min"] != float("inf")), default=1) > 10
        if wide:
            ax.set_yscale("log")
        ax.set_title("每 core outstanding = %s%s%s"
                     % (oc, "（规定值）" if oc == max(ocs) else "（推荐值）",
                        "，纵轴对数" if wide else ""), fontsize=10)
        ax.legend(fontsize=7, ncol=3, loc="upper center",
                  bbox_to_anchor=(0.5, -0.08), frameon=False)
        ax.grid(alpha=0.25, axis="y")
    drained = all(r.get("completed") for r in g)
    fig.suptitle("以 top die 为单位（每组 10 个 AI core）"
                 + ("。批次排空后六个 die 完成量相同"
                    if drained else
                    "。die 0/2/4 的远端流量并入纵环时位于下游，崩溃时几乎被饿死"),
                 fontsize=11.5)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


def plot_group_series(b: dict, path: Path) -> None:
    """Write bandwidth vs time, one curve per top-die group, S0 vs S1."""
    gs = b.get("group_series") or {}
    _cjk()
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 4.8), sharey=True)
    colors = ["#2563eb", "#dc2626", "#0891b2", "#ea5800", "#7c3aed", "#16a34a"]
    for ax, name, title in ((axes[0], "s0", "S0 基线（无源端流控）"),
                            (axes[1], "s1", "S1 源端 AIMD 控速")):
        ser = gs.get(name) or {}
        t = ser.get("t") or []
        bw = ser.get("bw_by_group") or {}
        w = ser.get("window", 50)
        for i, d in enumerate(sorted(bw, key=int)):
            ax.plot(t, bw[d], "-", color=colors[i % len(colors)], lw=1.5,
                    label=f"die {d}（10 个 AI core）")
        ax.set_title(title, fontsize=11)
        ax.set_xlabel("时间 (cycle)", fontsize=9)
        ax.set_ylabel(f"组写带宽 (WriteData flit / cycle，窗={w})", fontsize=9)
        ax.grid(alpha=0.25)
        ax.legend(fontsize=7.5, ncol=2, loc="upper right")
    fig.suptitle("每个 top die 一组：该组 10 个 AI core 合计的写带宽随时间变化",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=132)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def retry_table(b: dict) -> str:
    rows = []
    for r in sorted(b["retry_sweep"], key=lambda r: -r["pos_depth"]):
        rows.append([
            "不限" if r["pos_depth"] == 0 else r["pos_depth"],
            _ok(r["completed"]),
            f"{r['n_txn_done']:,}/{r['n_txn']:,}",
            _f(r["thr"], 3), f"{r['n_retry']:,}", _f(r["retry_per_txn"], 3),
            _f(r["nom_conc"], 1), _f(r["eff_conc"], 1),
            _pct(r["eff_frac"]), r["park_mean"], r["park_p99"],
            _f(r["jain"]), _f(r["group_jain"]),
        ])
    return _t(["HA 跟踪表深度", "排空", "完成事务", "吞吐<br>txn/cycle",
               "RetryAck 次数", "每笔<br>retry", "名义并发", "有效并发",
               "有效/名义", "挂起<br>均值", "挂起<br>p99",
               "Jain<br>（按 core）", "Jain<br>（按 die）"], rows)


def scenario_table(b: dict) -> str:
    sc = b["scenario"]
    best = sc["best_static"]
    rows = []
    for lbl in sc["scenarios"]:
        bt = max(1e-9, best.get(lbl, {}).get("thr", 0.0))
        sub = sorted((r for r in sc["rows"] if r["scenario"] == lbl
                      and r["scheme"] == "s0"),
                     key=lambda r: r["outstanding"])
        for r in sub:
            rows.append([lbl, r["n_cores"], f"静态 oc={r['outstanding']}",
                         _ok(r["completed"]), _f(r["thr"], 3),
                         _pct(r["thr"] / bt), "—", _f(r["jain"]),
                         _f(r["group_jain"])])
        for r in sorted((r for r in sc["rows"] if r["scenario"] == lbl
                         and r["scheme"] != "s0"),
                        key=lambda r: (r["scheme"], r.get("rtt_slack", 0))):
            rows.append([lbl, r["n_cores"],
                         "%s slack=%s" % (r["scheme"].upper(),
                                          r.get("rtt_slack")),
                         _ok(r["completed"]), _f(r["thr"], 3),
                         _pct(r["rel_best"]),
                         "%s..%s" % (_f(r["win_lo"], 0), _f(r["win_hi"], 0)),
                         _f(r["jain"]), _f(r["group_jain"])])
    return _t(["场景", "有流量的<br>core 数", "配置", "排空",
               "吞吐<br>txn/cycle", "占该场景<br>最好静态值",
               "实测收敛的<br>窗口范围", "Jain<br>（按 core）",
               "Jain<br>（按 die）"], rows)


def group_table(b: dict) -> str:
    rows = []
    # The adaptive schemes set their own limit, so they appear once under
    # every static setting swept. Keep one row for them, listed last.
    ad = {}
    static = []
    for r in b["group"]:
        if r["scheme"] in ("s18", "s19"):
            ad.setdefault(r["scheme"], r)
        else:
            static.append(r)
    ordered = sorted(static, key=lambda r: (-r["outstanding"], r["scheme"]))
    ordered += [ad[k] for k in sorted(ad)]
    for r in ordered:
        gp = r["goodput_by_group"]
        rows.append([
            r["outstanding"] if r["scheme"] not in ("s18", "s19")
            else "自适应", r["scheme"].upper(), _ok(r["completed"]),
            " / ".join(_f(v, 3) for v in
                       [gp[d] for d in sorted(gp, key=int)]),
            _f(r["goodput_total"], 3), _f(r["goodput_max_min"], 2),
            _f(r["group_jain"]), _f(r["group_max_min"], 2),
            _f(r["group_cov"], 3), f"die {r['worst_group']}",
            _f(r["core_jain"]), _f(r["jain_within_worst"]),
        ])
    return _t(["outstanding", "方案", "排空",
               "各 die 写吞吐 (flit/cycle)<br>die 0 / 1 / 2 / 3 / 4 / 5",
               "合计", "die 间<br>max/min<br>（按完成量）",
               "die 间 Jain<br>（竞争窗口）", "die 间<br>max/min",
               "die 间 CoV", "最差 die",
               "Jain<br>（按 core）", "最差 die 内部<br>的 Jain"], rows)


def mod4_table(b: dict) -> str:
    bm = b["binding_mod4"]
    rows = []
    for r in bm["rows"]:
        if r["die"] != 0:
            continue
        rows.append([r["col"], "本 die 的 4 列" if r["in_own_half"]
                     else "另一半的 4 列", r["bridge"], r["pos"],
                     r["group_pos"], r["col_mod4"],
                     "近端横环 H%d" % r["hring"] if r["in_own_half"]
                     else "远端横环 H%d" % r["hring"],
                     _ok(r["matches_mod4"])])
    return _t(["目标 HA 所在列", "位置", "用的 D2D bridge 节点号",
               "该 bridge 在 8 个中的序号", "组内位置",
               "目标列 mod 4", "落到哪条横环", "符合 mod-4 规则"], rows)


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
         "按最长无拥塞写 RTT 设定：从 REQ 上环到 Comp 回来，"
         "一个额度要盖住这段往返"],
        ["HA 请求跟踪表", f"<b>{m.get('pos_depth', m['fabric'].get('ha_pos_depth', 0))}</b> 项 / HA",
         "超出后对后续 REQ 回 RetryAck，再发 PCrdGrant 后重传"],
        ["转向 / D2D FIFO 深度",
         f"{m['fabric']['turn_depth']} / {m['fabric']['d2d_depth']}",
         "唯一允许缓冲的地方；链路本身严格无缓冲"],
        ["workload",
         f"每 core <b>{m['k']}</b> 个写请求，均匀写全部 {t['n_has']} 个 HA",
         "这是请求总数，不是在途上限；共 "
         f"{m['k'] * t['n_cores']:,} 笔。需求对称，不均衡来自织物"],
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
         "SerDes + 跨时钟域"],
        ["bottom die 横环节点间", f"<b>{t.get('h_hop_lat', t.get('bot_hop_lat', 4))}</b> cycle",
         "挂接点 → 挂接点，单向 half ring"],
        ["bottom die 纵环节点间", f"<b>{t.get('v_hop_lat', 6)}</b> cycle",
         "HA ↔ 挂接点，单向 half ring"],
        ["挂接点转向", f"<b>{t.get('turn_lat', 5)}</b> cycle",
         "横环 ↔ 纵环，经转向 FIFO，不计入 D2D 落地"],
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
         "<b>改一个已有的配置寄存器；负载形态已知且不变时的首选</b>"],
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
        ["S18 时延驱动的自适应窗口（本文推荐）",
         f"{n_core} 个 core 各 3 个寄存器"
         "（窗口 / rtt_min / 累加器）+ 每个在途表项 1 个时间戳",
         "0（复用已有的 Comp 时刻，不新增报文）", "无",
         "纯本地，只看自己的往返时延",
         "唯一能同时应对满载崩溃与轻载欠载的方案；不改完成方"],
        ["S19 = S18 + S17",
         "上面两项之和", "0", "无", "两处互不干涉",
         "按 top die 粒度验收时代价最小的组合"],
    ]
    return _t(["方案", "存储/逻辑代价", "带宽代价", "额外互连", "运行时状态",
               "评价"], rows)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def write_focus_report(b: dict) -> None:
    """Report for the RTT-sized outstanding + HA retry operating point."""
    t, m = b["topology"], b["meta"]
    rtt = m.get("rtt") or t.get("rtt") or {}
    mand = b["schemes"]["mandated"]
    s0, s1 = mand["s0"], mand["s1"]
    n_txn = mand["n_txn"]
    bd = mand["bounds"]
    oc = m["core_outstanding"]
    pos = m.get("pos_depth", 16)
    q0, q1 = s0.get("retry", {}), s1.get("retry", {})
    g0, g1 = s0["group"], s1["group"]
    f0, f1 = s0["fairness"], s1["fairness"]
    peak0 = s0.get("max_core_outstanding", 0)
    binds = peak0 >= oc
    bind_txt = (
        f"本批次每 core {m['k']} 笔，大于窗口 {oc}，实测峰值占用 {peak0}，"
        f"<b>outstanding 上限已经打满</b>——S0 能发出去的条件只剩 outstanding 空位和 ring slot。"
        if binds else
        f"本批次每 core {m['k']} 笔，实测峰值占用 {peak0}，"
        f"<b>{oc} 这个上限本身没有打满</b>——它保证的是再长的往返也不会把 core 饿在等 Comp 上。"
    )

    plot_topology(b, IMG / "stack_topology.png")
    plot_binding(b, IMG / "stack_binding.png")
    plot_group_series(b, IMG / "stack_group_bw_series.png")
    if b.get("group"):
        plot_group(b, IMG / "stack_group.png")

    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>3D 堆叠 NoC 上的 per-group 写带宽</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, "WenQuanYi Micro Hei",
        sans-serif; max-width: 980px; margin: 1.5rem auto; padding: 0 1rem;
        line-height: 1.55; color: #111; }}
h1 {{ font-size: 1.45rem; }} h2 {{ font-size: 1.2rem; margin-top: 2rem; }}
h3 {{ font-size: 1.05rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem;
         margin: 0.6rem 0 1rem; }}
th, td {{ border: 1px solid #d4d4d8; padding: 0.28rem 0.45rem;
          text-align: left; vertical-align: top; }}
th {{ background: #f4f4f5; }}
.key {{ background: #f8fafc; border: 1px solid #e4e4e7; padding: 0.2rem 1.1rem; }}
.def {{ background: #eff6ff; border-left: 4px solid #2563eb;
        padding: 0.6rem 0.9rem; margin: 0.8rem 0; }}
.good {{ background: #f0fdf4; border-left-color: #16a34a; }}
img {{ max-width: 100%; height: auto; margin: 0.6rem 0 1rem; }}
code {{ font-size: 0.86em; }}
</style></head><body>
<h1>3D 堆叠 NoC：按 top die 分组的写带宽</h1>
<p>bottom die 横环 {t.get('h_hop_lat', 4)} cycle / 纵环 {t.get('v_hop_lat', 6)} cycle /
挂接点转向 {t.get('turn_lat', 5)} cycle。
每 core 请求数 = <b>{m['k']}</b>（closed batch，共 {n_txn:,} 笔），
每 core outstanding = <b>{oc}</b>（最长写 RTT，在途上限），
每个 HA 跟踪表 <b>{pos}</b> 项，超出回 RetryAck。</p>
<div class="def"><b>k 和 outstanding 不是一回事。</b>
k = {m['k']} 是每个 AI core 这一批要发的写请求总数。
outstanding = {oc} 是同时能挂在网上的笔数：一笔从 REQ 上环占到 Comp 回来。
S0 <b>没有源端流控</b>——只要该 core 还有 outstanding 空位、注入口当前 cycle
ring 上有空 slot，就把下一条 REQ 发出；HA 跟踪表满回 RetryAck，那是完成方反压，不是源端控速。
S1 在同样的 outstanding + slot 条件之上，再加 AIMD 源端注入预算。</div>

<h2>结论</h2>
<div class="key"><ol>
<li><b>outstanding 按最长写 RTT 取值是 {oc}。</b>
无拥塞的 WriteNoSnp 往返 = REQ 去程 + DBID 回程 + WriteData 去程
（{m['m_wdata']} flit 流水，最后一拍比第一拍晚 {m['m_wdata'] - 1} cycle）+ Comp 回程。
最坏一对 (core {rtt.get('core')}, HA {rtt.get('ha')}) 的去程
{rtt.get('fwd')} cycle、回程 {rtt.get('rev')} cycle，合计
<b>{rtt.get('rtt')} cycle</b>。额度从 REQ 上环一直占到 Comp 回来，
所以寄存器就写成这个数。{bind_txt}</li>

<li><b>HA 跟踪表满了就 retry。S0 不另加源端控速，S1 才控速。</b>
每个 HA 同时只能收 {pos} 个请求；再来的 REQ 走
RetryAck → PCrdGrant → 重发 REQ，占真实 RSP/REQ 带宽。
S0 完成 <b>{s0['n_txn_done']:,}/{n_txn:,}</b>，makespan {s0['makespan']:,} cycle，
RetryAck {q0.get('n_retry', 0):,} 次，有效并发是名义值的
{_pct(q0.get('eff_frac', 1))}；
S1 完成 {s1['n_txn_done']:,}/{n_txn:,}，{s1['makespan']:,} cycle，
retry {q1.get('n_retry', 0):,}。
{"两个方案都排空，没有活锁、没有停在停滞检测器上。"
 if s0["completed"] and s1["completed"]
 else "有方案没有排空，见下表。"}</li>

<li><b>按 top die 一组（10 个 AI core）看写带宽随时间的变化。</b>
S0 组间完成量 max/min = {_f(g0.get('goodput_max_min'), 2)}，
组间 Jain {_f(g0.get('jain'))}；
S1 为 {_f(g1.get('goodput_max_min'), 2)} / {_f(g1.get('jain'))}。
下图是各 group 写带宽随时间的曲线。</li>
</ol></div>

<img src="stack_group_bw_series.png" alt="S0 与 S1 各 group 写带宽随时间">
<div class="def">{scheme_table(b, "mandated")}
<b>怎么读曲线。</b>
纵轴是该 top die 10 个 AI core 合计的 WriteData 上环速率
（{b['group_series']['s0']['window']} cycle 滑窗）。
六条线若始终缠在一起，说明分组公平；若某条线长期贴底，
就是这个 die 被饿死。
S0 瞬时带宽只受 outstanding、ring slot 和 HA retry 成形；
S1 再叠一层源端 AIMD。
S0 每核 Jain {_f(f0.get('jain'))}、组间 CoV {_f(g0.get('cov'), 3)}；
S1 每核 Jain {_f(f1.get('jain'))}、组间 CoV {_f(g1.get('cov'), 3)}。</div>
<img src="stack_group.png" alt="各 group 整次运行写吞吐">

<h2>1　拓扑与硬件 setup</h2>
<h3>1.1 总体结构</h3>
{setup_table(b)}
<h3>1.2 链路延迟</h3>
{link_table(b)}
<div class="def">横环一跳 {t.get('h_hop_lat', 4)} cycle，纵环一跳
{t.get('v_hop_lat', 6)} cycle，挂接点 H↔V 转向另加
{t.get('turn_lat', 5)} cycle。
D2D 落地不再加转向时延——那一跳已经算在 D2D 的 {t['d2d_lat']} cycle 里。
纵环单向且最长 17 跳，所以回程往往比去程贵：最坏回程
{rtt.get('rev')} cycle，去程只有 {rtt.get('fwd')} cycle。</div>
<img src="stack_topology.png" alt="bottom die 与挂接点分组">
<h3>1.3 HA 与 D2D bridge 绑定（mod-4）</h3>
<img src="stack_binding.png" alt="HA 到 bridge 的绑定">
{mod4_table(b)}

<h2>2　按 top die 分组的写带宽</h2>
<p>一个 top die 的 10 个 AI core 共用一条环、一组 8 个挂接点和 8 条 D2D，
是不可再往下调度的单位。下表是整次运行的合计；上图是同一指标的时间展开。</p>
{group_table(b)}

<h2>3　理论下界</h2>
{bounds_table(bd)}
<p class="note">S0 效率 {s0.get('eff', 0):.2f}，S1 {s1.get('eff', 0):.2f}
（下界 / makespan）。</p>

<h2>4　复现</h2>
<div class="def">
<code>python3 utils/dse_stack_write_fair.py --focus --k {m['k']}</code><br>
<code>python3 utils/gen_stack_write_report.py</code>
<br><br>
仿真 {m.get('wall_s', 0):.0f} s。outstanding = {oc}，HA POS = {pos}。
</div>
</body></html>
"""
    OUT.write_text(html)
    print(f"wrote {OUT}")


def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"missing {DATA}; run dse_stack_write_fair.py first")
    b = json.loads(DATA.read_text())
    t, m = b["topology"], b["meta"]
    if "group_series" in b and "stability" not in b:
        write_focus_report(b)
        return
    for k in ("stability", "depth"):
        if k not in b:
            raise SystemExit(f"missing {k} scan; run dse_stack_stability.py")
    need = b["depth"]["need"]
    deepest = max(r["turn_depth"] for r in b["depth"]["rows"])
    need_txt = "；".join(
        f"outstanding {k} 需要 {v} flit" if v
        else f"outstanding {k} 需要 &gt;{deepest} flit"
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
    # The next step up usually misses the line only marginally while buying
    # real throughput, so quote it rather than burying the trade-off.
    alt = min((r for r in drain_ok if r["outstanding"] > rec["outstanding"]),
              key=lambda r: r["outstanding"], default=None)
    fastest = max(drain_ok, key=lambda r: r["thr_mean_ok"])
    # With deterministic arbitration the boundary is a reliability edge rather
    # than a hard wall: the first limit that fails on *some* seed, and then a
    # region where draining is luck-dependent before it collapses outright.
    flaky = min((r["outstanding"] for r in split
                 if 0 < r["n_completed"] < r["n_runs"]), default=None)
    dead = min((r["outstanding"] for r in split if r["n_completed"] == 0),
               default=None)
    edge_txt = (f"{flaky}（{len(st['seeds'])} 个种子里有的排空、有的不排空）"
                if flaky else "扫描范围内未出现")
    b["_rec"] = {"oc": rec["outstanding"]}

    plot_topology(b, IMG / "stack_topology.png")
    plot_binding(b, IMG / "stack_binding.png")
    plot_v_profile(b, IMG / "stack_v_profile.png")
    plot_oc(b, IMG / "stack_oc.png")
    plot_schemes(b, IMG / "stack_schemes.png")
    plot_root_cause(b, IMG / "stack_root_cause.png")
    plot_hassign(b, IMG / "stack_hassign.png")
    plot_stability(b, IMG / "stack_stability.png")
    plot_retry(b, IMG / "stack_retry.png")
    plot_scenario(b, IMG / "stack_scenario.png")
    plot_group(b, IMG / "stack_group.png")

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

    def _st(name):
        return sorted((r for r in st["rows"] if r["h_assign"] == name),
                      key=lambda r: r["outstanding"])

    edge_split = max((r["outstanding"] for r in _st("split")
                      if r["n_completed"] == r["n_runs"]), default=0)
    # above the shared ceiling, compare how many seeds each still drains
    degrade = [(r["outstanding"], r["n_completed"], o["n_completed"],
                r["n_runs"])
               for r, o in zip(_st("split"), _st("stack"))
               if r["outstanding"] > edge_split]
    degrade_txt = "，".join(
        f"outstanding {oc} 时 split 还能排空 {a}/{n} 个种子、stack 只有 {c}/{n}"
        for oc, a, c, n in degrade) or "扫描范围内没有可比的档位"
    ha_j_split = next((r["jain_mean"] for r in _st("split")
                       if r["outstanding"] == rec["outstanding"]), None)
    ha_j_stack = next((r["jain_mean"] for r in _st("stack")
                       if r["outstanding"] == rec["outstanding"]), None)

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

    # -- completer retry: what the nominal limit is actually worth ---------
    rs = sorted(b["retry_sweep"], key=lambda r: -r["pos_depth"])
    rs_lim = [r for r in rs if r["pos_depth"] > 0]
    rs_unl = next((r for r in rs if r["pos_depth"] == 0), None)
    # the shallowest tracker still shown, and the first depth whose
    # backpressure is strong enough to drain the batch on its own
    rs_drain = [r for r in rs_lim if r["completed"]]
    rs_rescue = max(rs_drain, key=lambda r: r["pos_depth"]) \
        if rs_drain else None
    rs_tight = min(rs_lim, key=lambda r: r["eff_frac"])

    # -- one static limit for every scenario? ------------------------------
    sc = b["scenario"]
    sc_best = sc["best_static"]
    sc_names = sc["scenarios"]
    sc_full, sc_light = sc_names[0], sc_names[-1]
    oc_full = sc_best.get(sc_full, {}).get("outstanding")
    oc_light = sc_best.get(sc_light, {}).get("outstanding")
    wr = sc["worst_rel"]
    st_worst = {k: v for k, v in wr.items() if k.startswith("static_")}
    dy_worst = {k: v for k, v in wr.items() if not k.startswith("static_")}
    best_static_key = max(st_worst, key=lambda k: st_worst[k]) \
        if st_worst else None
    best_dyn_key = max(dy_worst, key=lambda k: dy_worst[k]) \
        if dy_worst else None
    best_static_oc = (best_static_key or "").replace("static_oc", "")
    best_dyn_name = (best_dyn_key or "").split("_")[0].upper()
    best_dyn_slack = (best_dyn_key or "").split("slack")[-1]
    # how far the adaptive window actually travelled between scenarios
    def _win(scen, name, sl):
        return next((r for r in sc["rows"] if r["scenario"] == scen
                     and r["scheme"] == name
                     and str(r.get("rtt_slack")) == str(sl)), None)
    w_full = _win(sc_full, best_dyn_name.lower(), best_dyn_slack)
    w_light = _win(sc_light, best_dyn_name.lower(), best_dyn_slack)

    # -- per-top-die grouping ---------------------------------------------
    gr = b["group"]
    g_mand = [r for r in gr if r["outstanding"] == oc_mand]
    g_work = [r for r in gr if r["outstanding"] == oc_work]
    g_m0 = next((r for r in g_mand if r["scheme"] == "s0"), None)
    g_w0 = next((r for r in g_work if r["scheme"] == "s0"), None)
    g_s19 = next((r for r in gr if r["scheme"] == "s19"), None)
    g_s18 = next((r for r in gr if r["scheme"] == "s18"), None)
    # which dies lose, and are they the same half every time
    if g_m0:
        gp = g_m0["goodput_by_group"]
        lo_dies = sorted(gp, key=lambda d: gp[d])[:3]
        hi_dies = sorted(gp, key=lambda d: gp[d])[-3:]
        starved = "、".join(f"die {d}" for d in sorted(lo_dies, key=int))
        fed = "、".join(f"die {d}" for d in sorted(hi_dies, key=int))
        same_half = {int(d) % 2 for d in lo_dies} == {0}
    else:
        starved = fed = "—"
        same_half = False

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
而且只调节“怎么发”的流控一个也救不回来。</b>
在规定的 outstanding={oc_mand} 下，{n_txn:,} 笔事务只完成
<b>{m0['n_txn_done']:,} 笔（{100 * m0['n_txn_done'] / n_txn:.0f}%）</b>，
批次根本排不空；把 S1、S16、S17 分别加上去，
<b>这四个方案全部崩溃</b>（§7）——
它们都只调节“怎么发”，没有一个限制“同时能有多少笔在飞”，
而后者才是崩溃的原因。
本文新增的 S18 正是补上这一点，它在同样的 outstanding={oc_mand} 下
<b>能把批次排空</b>（见第 4 条）。
实测的边界很窄：跨 {len(st['seeds'])} 个种子，
到 <b>outstanding = {max(r['outstanding'] for r in drain_ok)}</b>
为止每个种子都能排空，
从 <b>{edge_txt}</b> 起就变成<b>时好时坏</b>，
再往上（32、{oc_mand}）则每次都崩溃。
可用区间的上沿就在个位数，
而<b>能不能排空在边沿上取决于运气</b>，这本身就不能作为设计点。
换算下来，规定值比织物能承受的并发度高了约
<b>{oc_mand // max(1, max(r['outstanding'] for r in drain_ok))} 倍</b>——
{t['n_cores']} 个 core × {oc_mand} =
<b>{n_flight_mand:,}</b> 笔并发写，而整个 bottom die 的纵向织物
只有 {t['capacity']['v']} 条有向链路。
多出来的在途报文不产生任何吞吐，只是变成互相阻塞的偏转流量：
偏转次数从 {min(r['n_deflections'] for r in ocs):,} 涨到
{max(r['n_deflections'] for r in ocs):,}。</li>

<li><b>换成以 top die 为单位统计，失衡比按 core 看严重一个量级。</b>
一个 top die 的 10 个 core 共用一个环、一个挂接组、一套 8 个 bridge，
是真正不可再内部调度的单位。
在 outstanding={oc_mand} 下，按 core 的 Jain 是
{_f(m0['fairness']['jain'])}（看起来只是“略有不均”），
但按 die 的完成量最快与最慢相差
<b>{_f(g_m0['goodput_max_min'], 0)} 倍</b>：
{starved} 几乎完全被饿死，{fed} 拿走几乎全部吞吐。
按 core 看不出来，是因为崩溃时同一个 die 内部的核<b>同样惨</b>
（最差 die 内部 Jain 仍有 {_f(g_m0['jain_within_worst'])}），
失衡整体落在 die <b>之间</b>。
{"被饿死的正好是三个左半区 die" if same_half else "被饿死的 die 见 §6.2"}——
它们的远端流量在纵环上并入的位置处于<b>下游</b>，
而这个劣势对该 die 的全部 8 列同向，
所以整组 10 个 core 一起受损，不会在目的地之间被平均掉。
好消息是把并发度调对之后它<b>完全消失</b>：
die 间完成量的 max/min 从 {_f(g_m0['goodput_max_min'], 0)} 倍降到
{_f(g_w0['goodput_max_min'], 2)}（六个 die 完成量<b>完全相同</b>），
竞争窗口内的带宽 max/min 也从 {_f(g_m0['group_max_min'], 2)} 降到
{_f(g_w0['group_max_min'], 2)}。
<b>所以 die 粒度的巨大失衡是拥塞崩溃的产物，不是拓扑的固有上限；
织物本身对六个 die 是对称的，只有过载时它才把对称性交给
“谁在纵环上游”这条规则。</b></li>

<li><b>“配 {oc_mand}”和“真的能用 {oc_mand}”是两件事：完成方会把请求退回来。</b>
HA 的请求跟踪表满了不能排队，CHI 要求它回 <code>RetryAck</code>
把请求退回发起方，再用 <code>PCrdGrant</code> 放行重发。
这条回路要真实带宽（2 个 RSP + 一次 REQ 重新过网）、
把请求打乱顺序，而且<b>被挂起的事务仍然占着 outstanding 额度却毫无进展</b>。
实测跟踪表深 {rs_tight['pos_depth']} 时，
<b>有效并发只剩名义值的 {_pct(rs_tight['eff_frac'])}</b>
（{_f(rs_tight['nom_conc'], 1)} → {_f(rs_tight['eff_conc'], 1)} 笔），
平均挂起 {rs_tight['park_mean']} cycle。
反直觉的是这<b>反而救了织物</b>：深度 {rs_rescue['pos_depth']} 时批次
完整排空（Jain {_f(rs_rescue['jain'])}），
而跟踪表不限时只完成
{rs_unl['n_txn_done']:,}/{rs_unl['n_txn']:,}。
原因是 RetryAck 本质上是一种<b>非自愿的准入控制</b>，
把吃不下的请求挡在织物外面。
所以正确的结论不是“retry 有害”，而是
<b>这个织物必须有准入控制；用一个明确的窗口去做，
可以省下那段挂起延迟</b>（§5.5）。</li>

<li><b>一个静态 outstanding 伺候不了两个场景，所以动态流控是必要的而不是锦上添花。</b>
outstanding 有两个方向相反的失效模式：太大则忙时崩溃，
太小则闲时<b>盖不住往返时延</b>。
实测这两个区间<b>没有交集</b>：
{t['n_cores']} 个 core 全在写时最好的静态值是 <b>oc={oc_full}</b>
（再往上崩溃），只有 10 个 core 在写时是 <b>oc={oc_light}</b>。
把所有场景一起看最差的那个，
最好的静态配置也只能拿到
{_pct(st_worst[best_static_key]) if best_static_key else "—"}。
本文因此给出 <b>S18</b>：发起方用<b>自己测到的往返时延</b>
（Comp 减去 REQ 发出时刻，本来就知道）当拥塞信号，
低于 <code>rtt_min×(1+slack)</code> 就涨窗口、越线就按比例回退、
收到 RetryAck 直接折半。
同一套参数不重配，窗口自己从
{_f(w_full['win_mean'], 1)}（满载）走到
{_f(w_light['win_mean'], 1)}（轻载），
跨场景下限提高到
<b>{_pct(dy_worst[best_dyn_key]) if best_dyn_key else "—"}</b>。
代价是每 core 三个寄存器，<b>没有</b>广播总线、
<b>没有</b>新报文类型、<b>不改</b>完成方（§7.5）。
要如实说明：在<b>已知不变</b>的负载上，调对的静态值仍然更好
（S18 只到最好静态值的 {_pct(w_full['rel_best'])}），
S18 换来的是不掉悬崖、也不在轻载白扔带宽。</li>

<li><b>如果负载形态是已知且固定的，最实用的一步就一句话：把 outstanding 从
{oc_mand} 改成 {rec['outstanding']}，代价为零。</b>
outstanding 上限本来就是一个已有的配置寄存器，改它不花任何硬件。
跨 {len(st['seeds'])} 个种子实测，outstanding={rec['outstanding']} 时：
{rec['n_completed']}/{rec['n_runs']} 个种子全部排空，
吞吐 <b>{rec['thr_mean_ok']:.3f} txn/cycle</b>
（达理论下界 <b>{_pct(rec['eff_mean_ok'])}</b>），
Jain <b>{rec['jain_mean']}</b>，最差 max/min
<b>{_f(rec['mm_worst'], 2)}</b>——
这是<b>唯一严格满足 §2 那条公平线（Jain ≥ 0.99 且 max/min ≤ 1.5）</b>
的可用配置。
代价要说清楚：稳定区内吞吐峰值在
outstanding={fastest['outstanding']}（{fastest['thr_mean_ok']:.3f}
txn/cycle），{rec['outstanding']} 只拿到它的
{_pct(rec['thr_mean_ok'] / fastest['thr_mean_ok'])}，
而峰值那一档公平性不达标
（Jain {fastest['jain_mean']}、max/min {_f(fastest['mm_worst'], 2)}）。
<b>如果愿意让一点公平性换吞吐，outstanding={alt['outstanding']}
是更好的工程折中</b>：Jain {alt['jain_mean']} 仍在 0.99 以上，
只是最差 max/min {_f(alt['mm_worst'], 2)} 略微越过 1.5 这条线，
换来 {alt['thr_mean_ok']:.3f} txn/cycle
（峰值的 {_pct(alt['thr_mean_ok'] / fastest['thr_mean_ok'])}，
比 {rec['outstanding']} 高 {_pct(alt['thr_mean_ok'] / rec['thr_mean_ok'] - 1)}）。
两者都比规定的 {oc_mand} 好几个数量级。
<b>但这条建议的前提是负载形态不变</b>——上一条已经说明它不总成立，
所以它和 S18 是<b>互补的两个答案</b>，不是二选一的竞争关系。</li>

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
<b>{t['n_cols']} 列中的 {t['group_cols']} 列</b>；
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

<li><b>一个免费的拓扑选择：同一行间隙里两条横环怎么分工，会改变越过上沿之后的劣化方式。</b>
两种分配搬运的 flit·跳完全相同，<b>解析下界一模一样</b>
（都是 {ha_rows[0]['bound']:,} cycle），
所以任何静态分析都区分不出来。
实测两者<b>可靠上沿相同</b>（都到 outstanding={edge_split}），
差别在越过上沿之后的<b>劣化方式</b>和<b>同并发度下的公平性</b>：
{degrade_txt}；
而在推荐点 outstanding={rec['outstanding']} 上，
split 的 Jain 是 {ha_j_split}、stack 是 {ha_j_stack}。
原因是前者让两个 die 在同一列上<b>落在不同的挂接点</b>，
后者把它们挤到同一个挂接点上抢同一条出边。
收益不大但不花任何硬件，只是布线时的一个选择。</li>

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
而所需深度随并发度陡升（{need_txt}）。
<b>把并发度压下来同时省了面积。</b></li>

<li><b>三个原有流控方案的结论：S1 能跑但很贵，S16 打不到痛点，
S17 收益不稳定。</b>
在 outstanding={oc_work} 上跨 {len(m['seeds'])} 个种子：
S1 Jain 均值 {sd['s1']['jain_mean']}、
S16 {sd['s16']['jain_mean']}、
S17 {sd['s17']['jain_mean']}，
基线 S0 {s0s['jain_mean']}。
S16 的控制点在 HA 侧的授权发放，而瓶颈在纵环的<b>并入仲裁</b>上，
控制点和瓶颈不在一处，所以它的预算在正常负载下几乎不生效（§7.2）；
S17 直接针对挂接点的转向仲裁，机理是对的，
按 core 看跨种子收益不稳定（§7.3），
但换到 <b>top die 粒度</b>它的作用就清楚了：
在 outstanding={oc_work} 上它把 die 间 Jain 从
{_f(g_w0['group_jain'])} 提到
{_f(next(r for r in g_work if r['scheme'] == 's17')['group_jain'])}、
max/min 从 {_f(g_w0['group_max_min'], 2)} 降到
{_f(next(r for r in g_work if r['scheme'] == 's17')['group_max_min'], 2)}。
<b>结论是：这三个方案都不如“把并发度控住”，
而控住并发度的两个办法（静态调对、或 S18 自己找）才是主线；
S17 只在按 die 验收时值得叠加上去（§7.6）。</b></li>

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
<p><b>用哪个 bridge 不需要查表，是一条布线规则：</b>
一个 die 的 8 个 bridge 落在自己那 4 列上、每列两次
（近端横环一次、远端横环一次）。
去本半区的列走近端横环那一个，去另一半区的列走远端横环那一个，
而<b>组内的位置都等于目标列 mod {t['group_cols']}</b>。
以 die 0（拥有第 0~3 列）为例，它去右半区第 4~7 列时，
分别取组内第 {" / ".join(str(c % 4) for c in range(4, 8))} 个 bridge：</p>
{mod4_table(b)}
<p class="note">全部 {b['binding_mod4']['n_checked']} 个
(die, 列) 组合都满足这条 mod-{t['group_cols']} 规则
（{_ok(b['binding_mod4']['holds'])}），6 个 die 无一例外。</p>
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
可靠区的上沿是 {max(r['outstanding'] for r in drain_ok)}（全排空），
{edge_txt} 起变成时好时坏，
到 32 与 {oc_mand} 则每次都崩溃。
但<b>“能排空”不等于“公平”</b>：
到 {fastest['outstanding']} 时吞吐最高
（{fastest['thr_mean_ok']:.3f} txn/cycle），
Jain 却只有 {fastest['jain_mean']}、max/min {_f(fastest['mm_worst'], 2)}，
达不到公平线。
严格满足两者的最大并发度是 <b>{rec['outstanding']}</b>：
Jain {rec['jain_mean']}、max/min {_f(rec['mm_worst'], 2)}、
吞吐 {rec['thr_mean_ok']:.3f} txn/cycle
（峰值的 {_pct(rec['thr_mean_ok'] / fastest['thr_mean_ok'])}）。
紧邻的 {alt['outstanding']} 只是 max/min
（{_f(alt['mm_worst'], 2)}）刚刚越线，
吞吐却高 {_pct(alt['thr_mean_ok'] / rec['thr_mean_ok'] - 1)}——
<b>这条线画在 1.5 还是 1.6，决定推荐 {rec['outstanding']} 还是
{alt['outstanding']}，所以两个数都列出来</b>，
由能接受的最差核间差异来定。
无论选哪个，代价都只是一个已有寄存器的取值。</div>

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
静态分析无法区分；实测可靠上沿也相同，
但 split（两个 die 各用一条横环走远端流量）
让它们在同一列上落在<b>不同</b>挂接点，
越过上沿之后劣化得更慢（{degrade_txt}）。
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
还把这 {t['n_attach']} 个挂接点上的缓冲需求降到
<b>{need.get(str(rec['outstanding']))} flit</b>——
而在 outstanding={max(int(k) for k in need)} 上连 {deepest} flit 都不够。
<b>两个建议是同一个方向。</b></div>

<h3>5.5 名义 outstanding 不等于有效 outstanding</h3>
<p>前面把 outstanding 当成一个纯粹的注入闸门，但完成方也有自己的额度。
CHI 里 HA 的请求跟踪表满了之后<b>不能把请求排队</b>：
它必须回一个 <code>RetryAck</code> 把请求<b>退回</b>发起方，
之后再用 <code>PCrdGrant</code> 发一个协议信用，
发起方收到信用才能<b>重发</b>这笔请求。
这条回路有三个后果，本节把它们分开测量：</p>
<ul>
<li><b>它要真实带宽</b>：一次退回 = 1 个 RSP（RetryAck）+ 1 个 RSP
（PCrdGrant）+ 一次 REQ <b>重新过网</b>。</li>
<li><b>它把请求打乱</b>：被退回的那笔要排到后发的请求之后，
所以到达顺序不再是发出顺序。</li>
<li><b>它吃掉 outstanding 额度而不产生进展</b>：被挂起等信用的事务
仍然占着发起方的额度，却没有任何一个 flit 在路上。
所以<b>名义并发（额度占用）大于有效并发（真正在推进的）</b>，
而决定一个 core 能不能盖住往返时延的是后者。</li>
</ul>
<img src="stack_retry.png" alt="名义并发与有效并发">
{retry_table(b)}
<div class="def"><b>怎么读。</b>
名义 outstanding 全程固定为 {oc_mand}，只改 HA 跟踪表深度。
跟踪表越浅，退回越多：到深度 {rs_tight['pos_depth']} 时
<b>有效并发只剩名义值的 {_pct(rs_tight['eff_frac'])}</b>
（{_f(rs_tight['nom_conc'], 1)} → {_f(rs_tight['eff_conc'], 1)} 笔），
每笔事务平均被退回 {_f(rs_tight['retry_per_txn'], 2)} 次，
挂起等信用的时间均值 {rs_tight['park_mean']} cycle、
p99 {rs_tight['park_p99']} cycle。
这就是“outstanding 配了 {oc_mand}，实际能用的远不到 {oc_mand}”的量化形式。</div>
<div class="def good"><b>但这里有一个反直觉的结果，而且它是本节的重点。</b>
把跟踪表做<b>浅</b>反而救了这个织物：
深度 {rs_rescue['pos_depth']} 时批次<b>完整排空</b>
（{rs_rescue['n_txn_done']:,}/{rs_rescue['n_txn']:,}），
吞吐 {_f(rs_rescue['thr'], 3)} txn/cycle、
Jain {_f(rs_rescue['jain'])}，
而跟踪表<b>不限</b>时只完成
{rs_unl['n_txn_done']:,}/{rs_unl['n_txn']:,}、Jain {_f(rs_unl['jain'])}。
原因很直接：RetryAck 是一种<b>非自愿的准入控制</b>——
完成方把自己吃不下的请求挡在了织物<b>外面</b>，
挂在发起方那里是不占链路的。
所以“有效并发被压低”在这里不是缺陷，而正是它有效的原因。
<b>代价是延迟</b>：请求平均要在发起方等 {rs_rescue['park_mean']} cycle
才拿到信用，这段时间对上层就是纯粹的等待。
用一个明确的窗口去做同一件事（§5.1 的 outstanding={rec['outstanding']}）
既能排空、又不用付这段挂起时间，
所以正确的读法是：<b>这个织物需要准入控制，
问题只是由谁、在哪一层、用什么代价来做。</b></div>

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

<h3>6.2 以 top die 为单位统计：失衡比按 core 看要严重得多</h3>
<p>按 core 统计回答的是“有没有哪一个核被饿死”。
但一个 core 不是可调度的单位，<b>一个 top die 才是</b>：
它的 10 个 AI core 共用同一个 die 的环、同一个 8 个挂接点的组、
同一套 8 个 D2D bridge。如果一个 die 的带宽不够，
在这个 die 内部再怎么调度也补不回来。
所以下表把每个 top die 的 10 个 core 合成一组：</p>
<img src="stack_group.png" alt="按 top die 分组的写带宽">
{group_table(b)}
<div class="def bad"><b>这是本次新增分析里最重要的一条。</b>
在规定的 outstanding={oc_mand} 下，按 core 看 Jain 是
{_f(m0['fairness']['jain'])}，听起来只是“略有不均”；
但按 top die 看完成量，最快与最慢的 die 相差
<b>{_f(g_m0['goodput_max_min'], 0)} 倍</b>——
{starved} 几乎完全拿不到带宽，
{fed} 拿走了几乎全部吞吐。
按 core 的 Jain 之所以看不出来，是因为崩溃时
<b>每个 die 内部的核彼此都同样惨</b>
（最差 die 内部的 Jain 仍有 {_f(g_m0['jain_within_worst'])}），
失衡整体落在 die 之间，而 Jain 对“少数组全灭”本来就不敏感。
<b>换句话说，之前按 core 得到的“失衡温和”的印象，
在真正该关心的粒度上是不成立的。</b></div>
<div class="def">{"<b>被饿死的三个 die 完全对应列的左半区。</b>" if same_half else "<b>被饿死的 die 与列半区的对应关系见下。</b>"}
这不是巧合，而是 §6 那条机理在 die 粒度上的直接体现：
一个 die 的远端流量要从<b>远端横环</b>并入纵环，
而同一个行间隙里两个 die 的挂接点在纵环上一个在<b>上游</b>、
一个在<b>下游</b>；“在环优先”意味着下游那个只能用上游剩下的槽。
这个劣势对该 die 的<b>全部 8 列都是同向的</b>，
所以它不会在目的地之间被平均掉，
而是<b>整组 10 个 core 一起</b>受影响——
这正是它在 die 粒度上放大、在 core 粒度上被稀释的原因。</div>
<div class="def good"><b>好消息：把并发度调对之后，die 之间的失衡基本消失。</b>
在 outstanding={oc_work} 上，die 间 max/min 从
{_f(g_m0['group_max_min'], 2)} 降到 {_f(g_w0['group_max_min'], 2)}、
die 间 Jain {_f(g_m0['group_jain'])} → {_f(g_w0['group_jain'])}，
每个 die 完成的事务数<b>完全相同</b>。
也就是说 die 粒度上的巨大失衡<b>是拥塞崩溃的产物，不是拓扑的固有上限</b>：
织物本身对六个 die 是对称的，只有在过载时它才把对称性交给了
“谁在纵环上游”这条规则。
残余的 die 间差异只体现在<b>谁先跑完</b>
（各 die 完成时刻 {" / ".join(str(v) for v in g_w0['finish_by_group'].values())} cycle），
不再体现在带宽上。</div>

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
（按 top die 的粒度上它的收益更清楚，见 §7.6）
但跨 {len(m['seeds'])} 个种子它的均值是
{sd['s17']['jain_mean']}，相对基线 {s0s['jain_mean']}
<b>并不构成稳定收益</b>。
机理是对的，收益不稳定，因此本文<b>不</b>把它作为推荐方案；
如果要继续做，方向是让让行的判据带上下游负载信息，
而不只是本地饥饿计数。</div>

<h3>7.4 为什么必须做动态流控：一个静态值伺候不了两个场景</h3>
<p>到这里推荐值是一个<b>静态</b>数字（outstanding={rec['outstanding']}），
它成立的前提是“负载形态不变”。这个前提要检验，因为 outstanding
有<b>两个方向相反</b>的失效模式：
配得太大，忙的时候拥塞崩溃；
配得太小，闲的时候<b>盖不住往返时延</b>，core 只能空等 Comp。
决定“静态值到底行不行”的，是这两个区间还有没有交集。
下面把有流量的 core 数从 {t['n_cores']} 降到 10（只有一个 top die 在写），
其余一切不变：</p>
<img src="stack_scenario.png" alt="不同场景对 outstanding 的要求">
{scenario_table(b)}
<div class="def bad"><b>两个区间没有交集。</b>
{sc_full}（{t['n_cores']} 个 core）最好的静态值是
<b>oc={oc_full}</b>，再往上就崩溃；
而 {sc_light}（10 个 core）最好的静态值是
<b>oc={oc_light}</b>——比前者高
{oc_light // max(1, oc_full)} 倍还多，
因为此时织物空着，少量在途事务根本填不满往返时延。
于是任何一个固定值都必然在某个场景上大幅让步：
把全部场景放在一起看最差的那个，
<b>最好的静态配置（{best_static_key.replace("static_oc", "oc=") if best_static_key else "—"}）
也只能拿到 {_pct(st_worst[best_static_key]) if best_static_key else "—"}</b>。
这不是调参没调好，而是<b>被要求用一个数去满足两个互相排斥的约束</b>。
这也正是“实际有效的 outstanding 需要动态控制”的直接证据。
中间那档（30 个 core）最好的静态值是
oc={sc_best.get(sc_names[1], {}).get('outstanding')}，
正好落在两者之间——所以这不是两个特例，而是一条随负载移动的曲线。</div>
<div class="def"><b>顺便纠正一个可能的误读：规定的 {oc_mand} 本身并不“错”。</b>
在只有一个 top die 有流量时，
outstanding={oc_mand} 也能拿到该场景最好静态值的
{_pct(next(r['thr'] for r in sc['rows'] if r['scenario'] == sc_light and r['scheme'] == 's0' and r['outstanding'] == oc_mand) / max(1e-9, sc_best[sc_light]['thr']))}，
批次照样排空——因为织物空着，多出来的额度根本用不上，
也就不会互相阻塞。
它<b>只在多个 die 同时满负荷写的时候才是灾难性的</b>
（{_pct(next(r['thr'] for r in sc['rows'] if r['scenario'] == sc_full and r['scheme'] == 's0' and r['outstanding'] == oc_mand) / max(1e-9, sc_best[sc_full]['thr']))}）。
这恰恰说明问题的性质：<b>它不是一个取值选错了，
而是“用一个固定取值”这件事本身在这里不成立。</b></div>

<h3>7.5 S18：用往返时延自己找窗口（本文推荐的新方案）</h3>
<p>既然要动态，就得先选一个反馈信号。RetryAck 看起来最现成，
但 §5.5 已经说明它<b>来得太晚</b>：这个织物的瓶颈在环上的并入仲裁，
HA 的跟踪表在被压垮之前<b>根本填不满</b>，
所以只靠 RetryAck 的控制器会一路把窗口涨过悬崖。
真正能反映织物拥塞的免费信号是<b>往返时延本身</b>：
发起方本来就知道自己什么时候发出 REQ、什么时候收到 Comp。</p>
<p>S18 的控制环完全落在发起方本地：</p>
<ul>
<li><b>测</b>：每笔写的往返时延；历史最小值 <code>rtt_min</code>
就是这个 core 自己的“无拥塞基线”，
它同时也<b>定义了窗口的下限</b>——要盖住往返，窗口不能比
带宽时延积更小。</li>
<li><b>判</b>：目标线 = <code>rtt_min × (1 + slack)</code>。
低于它说明织物还有余量，高于它说明这个 core 正在为自己制造排队。</li>
<li><b>调</b>：明显没拥塞时窗口<b>几何增长</b>（否则跨不过
{oc_full}→{oc_light} 这个量级差就已经过了这一相），
接近目标线时改为加性小步；越线则按超出比例回退；
收到 RetryAck 直接<b>折半</b>。</li>
</ul>
<div class="def good"><b>实测：它自己走到了两个场景各自该在的位置。</b>
同一套参数、<b>没有为任何场景重新配过</b>，
{best_dyn_name}（slack={best_dyn_slack}）的窗口在
{sc_full} 收敛到 {_f(w_full['win_lo'], 0)}~{_f(w_full['win_hi'], 0)}
（均值 {_f(w_full['win_mean'], 1)}），
在 {sc_light} 收敛到 {_f(w_light['win_lo'], 0)}~{_f(w_light['win_hi'], 0)}
（均值 {_f(w_light['win_mean'], 1)}）——
方向和量级都对上了实测的最好静态值（{oc_full} 与 {oc_light}）。
跨场景的<b>下限</b>因此从最好静态值的
{_pct(st_worst[best_static_key]) if best_static_key else "—"}
提高到 <b>{_pct(dy_worst[best_dyn_key]) if best_dyn_key else "—"}</b>。
测到的 rtt_min 约 {_f(w_full['rtt_min'], 0)} cycle，
这也是“窗口不能太小”的定量依据。</div>
<div class="def"><b>代价，以及要如实说明的两点。</b>
每个 core 三个寄存器（窗口、rtt_min、累加器）
外加每个在途表项一个时间戳——而重传超时本来就需要这个时间戳。
<b>没有</b>广播总线（对比 S1 的
{b['schemes']['work']['s1'].get('fc', {}).get('bus_bits', 0):,} bit 广播）、
<b>没有</b>新报文类型、<b>不改</b>完成方。
两点让步：
其一，在<b>已知且不变</b>的负载上，调对的静态值仍然更好——
{sc_full} 上 S18 只到最好静态值的
{_pct(w_full['rel_best'])}，因为它要花时间收敛，
而静态值一开始就在正确位置；
其二，各 core 依自己的 rtt_min 独立决策，窗口会有差异
（{_f(w_full['win_lo'], 0)}~{_f(w_full['win_hi'], 0)}），
按 core 的 Jain 因此不如静态值
（{_f(w_full['jain'])} vs {rec['jain_mean']}）。
<b>所以 S18 的价值不是峰值，而是不会掉下悬崖、
也不会在轻载时白扔一半带宽。</b></div>

<h3>7.6 S19 = S18 + S17：两个问题分别用两个便宜的办法</h3>
<p>S18 修的是“流量总量”，S17 修的是“谁先走”，两者互不干涉，
可以直接叠加。这也刚好对上 §6.2：
die 粒度的失衡来自挂接点的并入优先级，
而这正是 S17 的作用点。</p>
<div class="def good">在按 top die 的粒度上，叠加是有意义的：
{("S19 把 die <b>之间</b>的 max/min 从 %s 压到 %s、"
  "die 间 Jain 从 %s 提到 %s，代价是完成时间长 %s（%s → %s cycle）。"
  % (_f(g_s18['group_max_min'], 2), _f(g_s19['group_max_min'], 2),
     _f(g_s18['group_jain']), _f(g_s19['group_jain']),
     _pct(g_s19['makespan'] / max(1, g_s18['makespan']) - 1),
     f"{g_s18['makespan']:,}", f"{g_s19['makespan']:,}")
  if g_s18 and g_s19 else "见上表。")}
{("<b>但要说清楚方向：这是把公平性从 die 内部挪到 die 之间，"
  "不是净增。</b>最差 die <b>内部</b>的 Jain 反而从 %s 掉到 %s——"
  "S17 让转向 FIFO 插队，受益的是被压住的那个挂接点整体，"
  "而同一个挂接点上先到的 core 因此更占优。"
  "所以只有当验收指标确实是按 die 时，这笔交换才划算。"
  % (_f(g_s18['jain_within_worst']), _f(g_s19['jain_within_worst']))
  if g_s18 and g_s19 and
  g_s19['jain_within_worst'] < g_s18['jain_within_worst'] else "")}
如果最终的验收指标是<b>按 top die</b> 的均衡（本次的要求就是如此），
S19 是这份报告里代价最小、又同时覆盖两个失效模式的组合。</div>

<h2>8　代价对比</h2>
{cost_table(b)}
<div class="def good"><b>建议分两种情况，因为它们的最优解不同。</b>
共同的前提：路由已被 HA↔bridge 绑定钉死，不是可调项；
转向 FIFO 必须给够深度（§5.4）；
S1 贵、S16 打不到痛点、S17 单独用收益不稳。
<br><br>
<b>其一，负载形态已知且基本不变</b>——直接把每 core 的 outstanding 从
{oc_mand} 调到 {rec['outstanding']}，代价为零：
批次从“永远排不空”变成全部排空，
吞吐 {at_mand['thr']:.3f} → {rec['thr_mean_ok']:.3f} txn/cycle
（<b>{rec['thr_mean_ok'] / max(1e-9, at_mand['thr']):.0f} 倍</b>），
按 core 的 Jain {at_mand['jain']} → {rec['jain_mean']}、
max/min {_f(at_mand['max_min'], 2)} → {_f(rec['mm_worst'], 2)}，
按 top die 的 max/min {_f(g_m0['group_max_min'], 2)} →
{_f(g_w0['group_max_min'], 2)}。
<br><br>
<b>其二，负载形态会变（更接近真实场景）</b>——静态值不成立，
因为满载的安全区间（≤{oc_full}）与轻载的可用区间（≈{oc_light}）
<b>没有交集</b>（§7.4）。此时用 <b>S18</b>：
每 core 三个寄存器，拿自己测到的往返时延当信号，
不重配参数就能在两个场景各自收敛到正确的量级，
跨场景下限从最好静态值的
{_pct(st_worst[best_static_key]) if best_static_key else "—"}
提高到 {_pct(dy_worst[best_dyn_key]) if best_dyn_key else "—"}。
如果验收指标是<b>按 top die</b> 的均衡，再叠加 S17 成为 S19（§7.6）。
如果还有布线自由度，顺手选 split 式的横环分工。</div>

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
