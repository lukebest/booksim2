#!/usr/bin/env python3
"""HTML report: per-core write bandwidth fairness on the bufferless ring.

One workload: 10 AI cores writing uniformly to 8 memory nodes. Nodes 9 and 19
are neither core nor memory -- they forward, but never source or sink a write.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "results" / "ring2_write_fair.json"
OUT = ROOT / "results" / "report_ring2_write_fairness.html"
IMG = ROOT / "results"

SCHEMES = ("S0", "S1", "S15", "S16")
COLOR = {"S0": "#dc2626", "S1": "#f59e0b", "S15": "#2563eb",
         "S16": "#16a34a"}
LABEL = {"S0": "S0 基线（无流控）", "S1": "S1 拥塞等级 AIMD",
         "S15": "S15 公平份额 + 槽预约",
         "S16": "S16 接收端授权（Homa 式）",
         "S17": "S17 TIMELY（RTT 梯度）",
         "S18": "S18 DCQCN（tracker ECN）"}


def _use_cjk_font() -> None:
    from matplotlib import font_manager as fm
    wanted = ("micro hei", "cjk", "noto sans sc", "source han sans")
    for f in fm.fontManager.ttflist:
        name = f.name.lower()
        if any(w in name for w in wanted):
            plt.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def _table(headers: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = ["<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
            for r in rows]
    return (f"<table><thead><tr>{th}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def _cores(pat: dict) -> list[str]:
    return sorted(pat["schemes"][SCHEMES[0]]["fairness"]["bw_by_core"],
                  key=int)


def _role(i: int, meta: dict) -> str:
    if i in meta["core_nodes"]:
        return "core"
    if i in meta["mem_nodes"]:
        return "mem"
    return "other"


# ---------------------------------------------------------------------------
# plots
# ---------------------------------------------------------------------------

def plot_topology(meta: dict, path: Path) -> None:
    """The ring itself: roles, per-edge hop delay, and the two dead spots."""
    _use_cjk_font()
    lats = meta["link_lats"]
    n = len(lats)
    fig, ax = plt.subplots(figsize=(8.6, 8.6))
    ax.set_aspect("equal")
    ax.axis("off")
    pts = []
    for i in range(n):
        a = -math.pi / 2 + i * 2 * math.pi / n
        pts.append((math.cos(a), math.sin(a)))
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        wrap = i == n - 1
        ax.plot([x0, x1], [y0, y1], color="#be123c" if wrap else "#64748b",
                lw=3.0 if wrap else 1.8, zorder=1, solid_capstyle="round")
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        mag = math.hypot(mx, my) or 1.0
        ax.text(mx * (1 + 0.16 / mag), my * (1 + 0.16 / mag), str(lats[i]),
                ha="center", va="center", fontsize=10, fontweight="700",
                color="#9f1239" if wrap else "#0f172a",
                bbox=dict(boxstyle="round,pad=0.15", fc="white",
                          ec="#fecaca" if wrap else "#e2e8f0", lw=0.8),
                zorder=3)
    face = {"core": "#2563eb", "mem": "#ea580c", "other": "#94a3b8"}
    for i, (x, y) in enumerate(pts):
        r = _role(i, meta)
        ax.add_patch(plt.Circle((x, y), 0.108, fc=face[r], ec="white",
                                lw=1.6, zorder=4))
        tag = {"core": f"C{i}", "mem": f"M{i}"}.get(r, f"N{i}")
        ax.text(x, y, tag, ha="center", va="center", fontsize=7.2,
                color="white", fontweight="700", zorder=5)
    ax.text(0, 0.10, "plane ×2", ha="center", fontsize=11, color="#334155")
    ax.text(0, -0.02, "双向全环 · 最短路", ha="center", fontsize=11,
            color="#334155")
    ax.text(0, -0.14, "灰 = 非终端（不收发写）", ha="center", fontsize=9.5,
            color="#64748b")
    ax.set_xlim(-1.45, 1.45)
    ax.set_ylim(-1.45, 1.45)
    ax.set_title("20 节点双 plane 环 · 边上数字 = 该边 hop 时延（拍）\n"
                 "蓝 = AI core（10），橙 = memory（8），灰 = 节点 9 / 19",
                 fontsize=12, pad=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_bw_bars(pat: dict, path: Path) -> None:
    """Per-core write bandwidth, one group of bars per scheme.

    The unbounded-tracker baseline is drawn alongside, hatched, because with a
    finite tracker every scheme lands inside a couple of percent of every
    other one and the panel would otherwise look like a flat wall. That
    flatness is itself the finding, but it is only legible next to the run
    where the ring really is the constraint.
    """
    _use_cjk_font()
    cs = _cores(pat)
    x = range(len(cs))
    ref = pat.get("s0_unbounded")
    series = [(s, pat["schemes"][s]["fairness"], COLOR[s], None, LABEL[s])
              for s in SCHEMES]
    if ref:
        series.insert(0, ("REF", ref["fairness"], "#64748b", "//",
                          "S0，tracker = ∞（参照：环受限）"))
    n = len(series)
    w = 0.82 / n
    off = (n - 1) / 2.0
    fig, ax = plt.subplots(figsize=(11.6, 4.9))
    for i, (_s, f, col, hatch, lab) in enumerate(series):
        vals = [f["bw_by_core"][c] for c in cs]
        ax.bar([v + (i - off) * w for v in x], vals, w,
               label=f"{lab}  max/min={f['max_min']:.3f}"
                     f"  吞吐={f['throughput']:.3f}",
               color=col, edgecolor="white", linewidth=0.6, hatch=hatch)
        ax.axhline(sum(vals) / len(vals), color=col, ls=":", lw=1.0)
    # Bandwidth bars start at zero, so a 4% spread is invisible. Clip the
    # bottom to just below the worst core to make the spread readable, and say
    # so on the axis rather than letting the reader assume a zero baseline.
    lo = min(min(f["bw_by_core"][c] for c in cs) for _s, f, *_ in series)
    hi = max(max(f["bw_by_core"][c] for c in cs) for _s, f, *_ in series)
    pad = 0.08 * (hi - lo)
    ax.set_ylim(max(0.0, lo - pad), hi + pad)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"C{c}" for c in cs])
    ax.set_xlabel("AI core")
    ax.set_ylabel("写带宽（WriteData flit/cycle）")
    ax.set_title("每 core 写带宽（争用窗口内），虚线 = 该方案均值，"
                 "纵轴已截断以显示差异")
    # The truncated axis leaves no room inside the panel, so the legend goes
    # underneath rather than on top of the bars.
    ax.legend(fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.16),
              ncol=3, frameon=False)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_bw_panels(pat: dict, path: Path) -> None:
    """Per-core write-inject rate over time, one panel per scheme."""
    _use_cjk_font()
    cs = _cores(pat)
    fig, axes = plt.subplots(1, len(SCHEMES), figsize=(13.6, 3.6),
                             sharey=True)
    cmap = plt.get_cmap("viridis")
    for ax, s in zip(axes, SCHEMES):
        sch = pat["schemes"][s]
        for j, c in enumerate(cs):
            b = sch["wr_binned"][c]
            ax.plot(b["t"], b["rate"], lw=1.0,
                    color=cmap(j / max(1, len(cs) - 1)), alpha=0.9)
        f = sch["fairness"]
        ax.set_title(f"{s}  mk={sch['makespan']}  "
                     f"max/min={f['max_min']:.3f}", fontsize=10)
        ax.set_xlabel("cycle")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("写注入率 flit/cycle")
    fig.suptitle("每 core 写注入率随时间（颜色 = core index）", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_bw_overlay(pat: dict, path: Path) -> None:
    """Slowest and fastest core of the baseline, tracked across schemes."""
    _use_cjk_font()
    f0 = pat["schemes"]["S0"]["fairness"]["bw_by_core"]
    lo = min(f0, key=lambda c: f0[c])
    hi = max(f0, key=lambda c: f0[c])
    fig, ax = plt.subplots(figsize=(10.2, 3.8))
    for s in SCHEMES:
        for c, ls in ((lo, "-"), (hi, "--")):
            b = pat["schemes"][s]["wr_binned"][c]
            tag = f"最慢 C{c}" if ls == "-" else f"最快 C{c}"
            ax.plot(b["t"], b["rate"], ls, lw=1.4, color=COLOR[s],
                    alpha=0.9, label=f"{s} {tag}")
    ax.set_xlabel("cycle")
    ax.set_ylabel("写注入率 flit/cycle")
    ax.set_title(f"基线最慢 core C{lo} 与最快 core C{hi} 的对比")
    ax.legend(fontsize=8, ncol=3)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_scatter(pat: dict, path: Path) -> None:
    """Bandwidth against the two candidate explanations."""
    _use_cjk_font()
    rc = pat["root_cause"]
    rows = rc["rows"]
    fig, axes = plt.subplots(1, 3, figsize=(13.6, 3.9))
    for ax, key, xl, r, sp in (
        (axes[0], "adj_mem", "相邻 mem 个数", rc["corr_bw_adjmem"],
         rc["rank_bw_adjmem"]),
        (axes[1], "mean_hop_to_mem", "到 8 个 mem 的平均跳数",
         rc["corr_bw_meanhop"], None),
        (axes[2], "succ_rate", "上环成功率 ok/(ok+fail)",
         rc["corr_bw_succ"], rc["rank_bw_succ"]),
    ):
        ax.scatter([x[key] for x in rows], [x["bw"] for x in rows], s=64,
                   color="#dc2626", zorder=3)
        for x in rows:
            ax.annotate(f"C{x['core']}", (x[key], x["bw"]),
                        textcoords="offset points", xytext=(5, 4), fontsize=8)
        title = f"r={r:.3f}" + (f"  Spearman={sp:.3f}" if sp is not None
                                else "（距离完全相同，无解释力）")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel(xl)
        ax.set_ylabel("S0 实测写带宽")
        ax.grid(alpha=0.3)
    fig.suptitle("位置依赖的确切形式：决定带宽的是“身边有几个 mem”，"
                 "不是“离 mem 多远”", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_hop_bw(pat: dict, cap: int, path: Path) -> None:
    """Ring-wide hop bandwidth against the 3-VC cap."""
    _use_cjk_font()
    fig, ax = plt.subplots(figsize=(10.2, 3.6))
    for s in SCHEMES:
        hb = pat["schemes"][s]["hop_bw"]
        ax.plot(hb["t"], hb["rate"], lw=1.3, color=COLOR[s], label=LABEL[s])
    ax.axhline(cap, color="black", ls=":", lw=1.4,
               label=f"3 VC hop 容量 {cap} flit/cycle")
    ax.set_xlabel("cycle")
    ax.set_ylabel("全环 hop 带宽 flit/cycle")
    ax.set_title("全环 hop 带宽与 3 VC 上限")
    ax.legend(fontsize=8.5)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_s1_trace(pat: dict, path: Path) -> None:
    """S1's own control signals: budget, own level, received level."""
    _use_cjk_font()
    tr = pat["schemes"]["S1"]["fc"]["trace"]
    nodes = [str(x) for x in tr["nodes"]]
    f0 = pat["schemes"]["S0"]["fairness"]["bw_by_core"]
    cand = [c for c in nodes if c in f0]
    lo = min(cand, key=lambda c: f0[c])
    hi = max(cand, key=lambda c: f0[c])
    idx = {c: i for i, c in enumerate(nodes)}
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 3.6), sharex=True)
    for c, col, tag in ((lo, "#dc2626", "最慢"), (hi, "#2563eb", "最快")):
        i = idx[c]
        axes[0].plot(tr["t"], [b[i] for b in tr["budget"]], lw=1.4,
                     color=col, label=f"{tag} C{c}")
        axes[1].plot(tr["t"], [l[i] for l in tr["level"]], lw=1.4,
                     color=col, label=f"{tag} C{c} 自身最终等级")
        axes[1].plot(tr["t"], [l[i] for l in tr["recv"]], lw=1.0, ls="--",
                     color=col, alpha=0.7, label=f"{tag} C{c} 收到的最大等级")
    axes[0].set_ylabel("每窗口注入预算（flit）")
    axes[0].set_title("AIMD 预算", fontsize=10)
    axes[1].set_ylabel("拥塞等级 0-7")
    axes[1].set_title("拥塞等级", fontsize=10)
    for ax in axes:
        ax.set_xlabel("cycle")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    fig.suptitle("S1 控制回路：谁在挨罚", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


RETRY_COL = {"S0": "#dc2626", "S16": "#16a34a",
             "S17": "#2563eb", "S18": "#a855f7"}


def _rows_of(study: dict, key: str, **eq) -> list[dict]:
    return [r for r in study.get(key, [])
            if all(r.get(k) == v for k, v in eq.items())]


def plot_outst_sweep(study: dict, path: Path) -> None:
    """The U curve, and the fact that its bottom moves with the workload."""
    _use_cjk_font()
    pats = study["meta"]["patterns"]
    panels = (
        ("throughput", "写吞吐 flit/cycle", False),
        ("outst_eff", "有效 outstanding（在推进的槽位）", False),
        ("retry_per_txn", "每笔事务的 RetryAck 次数", False),
        ("max_min", "最快 / 最慢 core", False),
    )
    fig, axes = plt.subplots(len(panels), len(pats),
                             figsize=(5.4 * len(pats), 3.0 * len(panels)),
                             sharex=True, squeeze=False)
    for col, pat in enumerate(pats):
        for row, (field, ylab, _) in enumerate(panels):
            ax = axes[row][col]
            for scheme in study["meta"]["schemes"]:
                rs = sorted(_rows_of(study, "sweep_outst", pattern=pat,
                                     scheme=scheme),
                            key=lambda r: r["core_outstanding"])
                if not rs:
                    continue
                # The baseline goes on thick and underneath: the other three
                # sit right on top of it wherever they change nothing.
                wide = scheme == "S0"
                ax.plot([r["core_outstanding"] for r in rs],
                        [r[field] for r in rs], marker="o",
                        ms=5.0 if wide else 3.4, lw=2.8 if wide else 1.4,
                        alpha=0.5 if wide else 1.0,
                        color=RETRY_COL.get(scheme, "#64748b"),
                        label=LABEL.get(scheme, scheme))
                if field == "throughput":
                    best = max(rs, key=lambda r: r["throughput"])
                    ax.plot([best["core_outstanding"]], [best["throughput"]],
                            marker="*", ms=13, mfc="none", mew=1.4,
                            color=RETRY_COL.get(scheme, "#64748b"))
            ax.set_xscale("log", base=2)
            ax.set_ylabel(ylab, fontsize=9)
            ax.grid(alpha=0.3)
            if row == 0:
                ax.set_title(f"{pat}", fontsize=11)
                ax.legend(fontsize=8)
            if row == len(panels) - 1:
                ax.set_xlabel("每 core outstanding 上限（标称）")
    # The nominal cap, for contrast with the effective count under it. Log y,
    # or the diagonal flattens everything the panel is about.
    for col, pat in enumerate(pats):
        rs = sorted(_rows_of(study, "sweep_outst", pattern=pat, scheme="S0"),
                    key=lambda r: r["core_outstanding"])
        xs = [r["core_outstanding"] for r in rs]
        axes[1][col].plot(xs, xs, ls=":", lw=1.2, color="#94a3b8",
                          label="标称上限（= y=x）")
        axes[1][col].set_yscale("log", base=2)
        axes[1][col].legend(fontsize=8, loc="upper left")
    fig.suptitle("outstanding 扫描：标称越大不等于有效越大，"
                 "★ 为该方案的吞吐最优点", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_retry_track(study: dict, path: Path) -> None:
    """Retry pressure and reordering as the completer's tracker shrinks."""
    _use_cjk_font()
    rs = _rows_of(study, "sweep_track")
    # These are six discrete design points, one of them unbounded, so the x
    # axis is categorical. A log axis cannot hold "unlimited" honestly, and a
    # twin y axis on top of one puts its series in a different place.
    fin = sorted((r for r in rs if r["ha_track"]),
                 key=lambda r: r["ha_track"]) + \
        [r for r in rs if not r["ha_track"]]
    lab = [str(r["ha_track"]) if r["ha_track"] else "∞" for r in fin]
    xs = list(range(len(fin)))
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 3.5))
    axes[0].plot(xs, [r["retry_per_txn"] for r in fin], marker="o",
                 color="#dc2626", lw=1.5, label="每笔事务重试次数")
    axes[0].set_ylabel("RetryAck / 事务")
    axes[0].set_title("重试压力", fontsize=10)
    axes[1].plot(xs, [r["outst_used"] for r in fin], marker="o",
                 color="#94a3b8", lw=1.5, label="已分配槽位")
    axes[1].plot(xs, [r["outst_eff"] for r in fin], marker="o",
                 color="#2563eb", lw=1.5, label="有效槽位")
    axes[1].plot(xs, [r["outst_park"] for r in fin], marker="o",
                 color="#f59e0b", lw=1.3, ls="--", label="停摆槽位")
    axes[1].set_ylabel("槽位数")
    axes[1].set_title("outstanding 去哪了", fontsize=10)
    axes[2].plot(xs, [r["ooo_frac"] for r in fin], marker="o",
                 color="#a855f7", lw=1.5, label="被后发者超越的比例")
    ax2 = axes[2].twinx()
    ax2.plot(xs, [r["ooo_max_disp"] for r in fin], marker="s", ms=3.5,
             color="#0891b2", lw=1.2, ls="--", label="最大位移")
    ax2.set_ylabel("最大位移（笔）", fontsize=9)
    ax2.legend(fontsize=8, loc="lower right")
    axes[2].set_ylabel("乱序比例")
    axes[2].set_title("乱序程度", fontsize=10)
    for ax in axes:
        ax.set_xticks(xs)
        ax.set_xticklabels(lab)
        # The rightmost column is the unbounded tracker, i.e. the model used
        # in sections 1-8. Shade it so it reads as the reference, not as one
        # more point on a scale.
        ax.axvspan(xs[-1] - 0.4, xs[-1] + 0.4, color="#e2e8f0", zorder=0)
        ax.set_xlim(xs[0] - 0.4, xs[-1] + 0.4)
        ax.set_xlabel("每 completer 的请求 tracker 表项")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="upper right")
    fig.suptitle(f"S0，outstanding 固定 "
                 f"{fin[0]['core_outstanding']}：completer 资源越紧，"
                 f"重试越多、有效 outstanding 越少", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_rate_trace(study: dict, path: Path) -> None:
    """What the two controllers actually do, and what it buys."""
    _use_cjk_font()
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 3.5))
    tr = (study.get("rate_trace") or {})
    for scheme, col in (("S17", RETRY_COL["S17"]), ("S18", RETRY_COL["S18"])):
        t = tr.get(scheme)
        if t:
            nc = len(t["nodes"])
            axes[0].plot(t["t"], [sum(r) / nc for r in t["rate"]], lw=1.4,
                         color=col, label=f"{LABEL.get(scheme, scheme)} 均值")
            axes[0].fill_between(t["t"], [min(r) for r in t["rate"]],
                                 [max(r) for r in t["rate"]], color=col,
                                 alpha=0.16, lw=0)
            rtt = [[v for v in r if v > 0] or [0.0] for r in t["rtt"]]
            axes[1].plot(t["t"], [sum(r) / len(r) for r in rtt], lw=1.3,
                         color=col, label=LABEL.get(scheme, scheme))
    axes[0].set_ylabel("注入速率（REQ/cycle/core）")
    axes[0].set_yscale("log")
    axes[0].set_title("速率轨迹（阴影为 core 间极差）", fontsize=10)
    axes[1].set_ylabel("实测 RTT（拍）")
    axes[1].set_title("REQ→DBIDResp 往返（含重试）", fontsize=10)
    for ax in axes[:2]:
        ax.set_xlabel("cycle")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)

    # The third panel is the control experiment: pin the rate, no controller.
    # Its peak is the ceiling a rate-based scheme could reach with perfect
    # foresight, so the horizontal lines show what being reactive costs.
    pat = study["meta"]["patterns"][0]
    oc = study["meta"].get("headline_outst")
    sr = sorted(study.get("sweep_rate") or [], key=lambda r: r["pace"])
    if sr:
        axes[2].plot([r["pace"] for r in sr], [r["throughput"] for r in sr],
                     marker="o", ms=4, lw=1.6, color="#0f766e",
                     label="钉死速率（无控制器）")
        b = max(sr, key=lambda r: r["throughput"])
        axes[2].plot([b["pace"]], [b["throughput"]], marker="*", ms=15,
                     mfc="none", mew=1.6, color="#0f766e")
        axes[2].set_xscale("log")
    for scheme in ("S0", "S17", "S18"):
        rs = [r for r in _rows_of(study, "sweep_outst", pattern=pat,
                                 scheme=scheme)
              if r["core_outstanding"] == oc]
        if rs:
            axes[2].axhline(rs[0]["throughput"], ls="--", lw=1.2,
                            color=RETRY_COL[scheme],
                            label=f"{scheme} 实际达到")
    axes[2].set_xlabel("注入速率 REQ/cycle/core")
    axes[2].set_ylabel("写吞吐 flit/cycle")
    axes[2].set_title(f"{pat}，outstanding={oc}：最优速率很窄", fontsize=10)
    axes[2].grid(alpha=0.3)
    axes[2].legend(fontsize=7.5, loc="lower center")
    fig.suptitle("S17 TIMELY 与 S18 DCQCN：源端限速把重试压下去，"
                 "但反应式控制追不上最优速率", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def _setup_table(meta: dict) -> str:
    rows = [
        ["节点数 / plane 数", f"{len(meta['link_lats'])} / {meta['n_planes']}",
         "两个独立的双向 ring plane，共用同一套几何"],
        ["AI core", f"{len(meta['core_nodes'])} 个：" +
         ", ".join(f"C{c}" for c in meta["core_nodes"]), "写发起方（CHI RN）"],
        ["memory 节点", f"{len(meta['mem_nodes'])} 个：" +
         ", ".join(f"M{h}" for h in meta["mem_nodes"]), "写目的地（completer）"],
        ["非终端节点", ", ".join(f"N{x}" for x in meta["non_terminal"]),
         "在环上转发，但既不发起也不接收写"],
        ["路由", "最短路（跳数平局走 CW）", "S0 及全部方案一致"],
        ["plane 选择", meta["plane_sel"], "两个 plane 之间做负载均衡"],
        ["每 core outstanding", meta["core_outstanding"],
         "同时在飞的写事务上限"],
        ["CHI VC", " / ".join(meta["vcs"]).upper(),
         "REQ、RSP、DAT 三条独立 VC，各自独立信用"],
        ["hop 容量", f"{meta['hop_bw_cap']} flit/cycle",
         f"{len(meta['link_lats'])} 节点 × 2 方向 × {meta['n_planes']} plane "
         f"× {meta['n_vc']} VC，σ={meta['sigma']}"],
        ["端口", f"inject {meta['board_ports']} / leave "
                 f"{meta['leave_ports']}（每 node 每 plane）",
         "三条 VC 共享同一个上下环端口"],
        ["上环队列深度", meta["inj_depth"], "每 (node, plane, VC)"],
        ["下环队列深度", meta["eject_depth"], "每 (node, plane)"],
        ["I-tag 门限 t_inj", meta["t_inj"], "限制注入饥饿时长"],
        ["E-tag 门限 t_xfer", meta["t_xfer"], "限制偏转次数"],
    ]
    return _table(["项目", "取值", "说明"], rows)


def _link_table(meta: dict) -> str:
    lats = meta["link_lats"]
    n = len(lats)

    def tag(i: int) -> str:
        r = _role(i, meta)
        return {"core": f"C{i}", "mem": f"M{i}"}.get(r, f"N{i}")

    rows = []
    for i, lat in enumerate(lats):
        note = "闭合边（N19 ↔ C0）" if i == n - 1 else ""
        rows.append([f"{tag(i)} — {tag((i + 1) % n)}", lat, note])
    return _table(["无向边", "hop 时延（拍）", "备注"], rows)


def _bounds_table(b: dict) -> str:
    rows = [
        ["LB_link 每 VC 独立链路", b["link_lb"],
         "REQ/RSP/DAT 各占一条 VC，取三者最大"],
        ["LB_port 端口合并", b["port_lb"],
         "inject / leave 每 (node, plane) 只有一个端口，三 VC 共享"],
        ["LB_cut 割集", b["cut_lb"], "跨割面的流量除以割面上的有向链路数"],
        ["LB_txn 事务串行链", b["txn_lb"],
         "REQ→DBIDResp→WriteData→Comp 两个来回"],
        ["<b>bound</b>", f"<b>{b['bound']}</b>", "以上取最大"],
    ]
    return _table(["下界", "cycle", "含义"], rows)


def _summary_table(pat: dict) -> str:
    rows = []
    thr0 = pat["schemes"]["S0"]["fairness"]["throughput"]
    for s in SCHEMES:
        sch = pat["schemes"][s]
        f = sch["fairness"]
        d = 100.0 * (f["throughput"] - thr0) / thr0
        q = sch.get("retry") or {}
        rows.append([
            LABEL[s], sch["makespan"], f["max_min"],
            f["bw_min"], f["bw_max"], f["throughput"],
            f"{d:+.1f}%", q.get("retry_per_txn", "—"),
            sch["n_deflections"], sch["n_board_fail"],
        ])
    return _table(["方案", "makespan", "max/min",
                   "最低 BW", "最高 BW", "吞吐 flit/cycle", "吞吐差",
                   "重试/事务", "偏转", "上环失败"], rows)


def _track_table(pat: dict) -> str:
    """The same baseline either side of the completer's request tracker.

    Without it the ring is the only thing a core competes for and position
    decides the outcome. With it the completer is, and the completer treats
    every core alike -- which flatters the fairness numbers and costs
    throughput.
    """
    ref = pat.get("s0_unbounded")
    if not ref:
        return ""
    rows = []
    for tag, d in (("∞（环受限）", ref), ("32（本报告基线）", pat["schemes"]["S0"])):
        f = d["fairness"]
        q = d.get("retry") or {}
        rows.append([tag, d["makespan"], f["throughput"], f["max_min"],
                     f["bw_min"], f["bw_max"], q.get("retry_per_txn", 0.0),
                     q.get("max_ha_used"), q.get("outst_eff_mean", "—"),
                     d.get("lat_p99")])
    return _table(["每 completer 的请求 tracker", "makespan", "吞吐",
                   "max/min", "最低 BW", "最高 BW", "重试/事务",
                   "峰值占用表项", "有效 outstanding", "延迟 p99"], rows)


def _rc_table(pat: dict) -> str:
    rows = []
    for r in pat["root_cause"]["rows"]:
        rows.append([f"C{r['core']}", r.get("adj_mem", "—"),
                     r.get("mean_hop_to_mem", "—"), r["bw"], r["succ_rate"],
                     r["hop_busy"], r["itag"], r["outstanding"],
                     r["lat_out"]])
    return _table(["core", "相邻 mem 数", "到 mem 平均跳数", "S0 写带宽",
                   "上环成功率", "hop_busy 失败", "I-tag 失败",
                   "outstanding 失败", "出向平均 λ"], rows)


def _sweep_table(pat: dict) -> str:
    rows = [[s["window"], s["band"], s["makespan"], s["max_min"],
             s["bw_min"], s["bw_max"], s["throughput"]]
            for s in pat["sweep"]]
    return _table(["window", "α/β 档位", "makespan", "max/min",
                   "最低 BW", "最高 BW", "吞吐"], rows)


def _seed_table(pat: dict) -> str:
    rows = []
    for r in pat.get("seed_sweep", []):
        a = r.get("S0")
        if not a:
            continue
        row = [r["seed"], a["max_min"], a["throughput"]]
        for s in ("S15", "S16"):
            b = r.get(s)
            row += ([b["max_min"], f"{b['thr_delta_pct']:+.2f}%"]
                    if b else ["—", "—"])
        rows.append(row)
    if not rows:
        return ""
    return _table(["seed", "S0 max/min", "S0 吞吐",
                   "S15 max/min", "S15 吞吐差",
                   "S16 max/min", "S16 吞吐差"], rows)


def _oc_table(pat: dict) -> str:
    rows = []
    for r in pat.get("sweep_oc", []):
        oc = r["overcommit"]
        rows.append([
            "∞（= S0 的授权策略）" if oc is None else oc,
            r["makespan"], r["max_min"], r["throughput"],
            r.get("peak_grants"), r.get("grant_delay_mean"),
            r.get("lat_p99"),
        ])
    if not rows:
        return ""
    return _table(["overcommit", "makespan", "max/min", "吞吐",
                   "实测峰值授权", "授权等待均值", "事务延迟 p99"], rows)


def _ablate_table(pat: dict) -> str:
    rows = [[r["variant"], r["makespan"], r["max_min"],
             r["throughput"], r.get("grant_delay_mean")]
            for r in pat.get("ablate", [])]
    if not rows:
        return ""
    return _table(["变体", "makespan", "max/min", "吞吐",
                   "授权等待均值"], rows)


def _best_outst(study: dict, pattern: str, scheme: str) -> dict:
    rs = _rows_of(study, "sweep_outst", pattern=pattern, scheme=scheme)
    return max(rs, key=lambda r: r["throughput"]) if rs else {}


def _outst_table(study: dict, pattern: str, scheme: str = "S0") -> str:
    rs = sorted(_rows_of(study, "sweep_outst", pattern=pattern,
                         scheme=scheme), key=lambda r: r["core_outstanding"])
    best = _best_outst(study, pattern, scheme)
    rows = []
    for r in rs:
        star = " ★" if r is best else ""
        rows.append([f"{r['core_outstanding']}{star}", r["makespan"],
                     r["throughput"], r["outst_eff"], r["outst_used"],
                     r["outst_park"], r["retry_per_txn"], r["ooo_frac"],
                     r["ooo_max_disp"], r["max_min"], r.get("lat_p99")])
    if not rows:
        return ""
    return _table(["标称 outstanding", "makespan", "吞吐", "有效 outstanding",
                   "已分配均值", "其中停摆", "重试/事务", "乱序比例",
                   "最大位移", "max/min", "延迟 p99"], rows)


def _drift_table(study: dict) -> str:
    """Where each workload's best cap sits -- they are not the same place."""
    rows = []
    for pattern in study["meta"]["patterns"]:
        for scheme in study["meta"]["schemes"]:
            b = _best_outst(study, pattern, scheme)
            if not b:
                continue
            hl = [r for r in _rows_of(study, "sweep_outst", pattern=pattern,
                                      scheme=scheme)
                  if r["core_outstanding"] == study["meta"]["headline_outst"]]
            h = hl[0] if hl else b
            loss = 100.0 * (h["throughput"] - b["throughput"]) \
                / max(1e-9, b["throughput"])
            rows.append([pattern, LABEL.get(scheme, scheme),
                         b["core_outstanding"], b["throughput"],
                         b["outst_eff"], b["retry_per_txn"],
                         h["throughput"], f"{loss:+.1f}%"])
    if not rows:
        return ""
    return _table(["workload", "方案", "最优标称 outstanding", "该点吞吐",
                   "该点有效 outstanding", "该点重试/事务",
                   f"固定 {study['meta']['headline_outst']} 的吞吐",
                   "固定值的损失"], rows)


def _order_table(study: dict) -> str:
    rows = []
    for r in study.get("ablate_order", []):
        rows.append([
            "∞" if not r["ha_track"] else r["ha_track"],
            "按序" if r["inorder_retire"] else "乱序",
            r["makespan"], r["throughput"], r["outst_used"], r["outst_park"],
            r["outst_hol"], r["outst_eff"], r["max_hol_hold"],
            r["ooo_frac"], r["retire_ooo"]])
    if not rows:
        return ""
    return _table(["tracker", "退休方式", "makespan", "吞吐", "已分配槽位",
                   "停摆（等信用）", "队头阻塞（等前序）", "有效槽位",
                   "峰值滞留", "接受乱序", "退休乱序"], rows)


def _rate_table(study: dict) -> str:
    pattern = study["meta"]["patterns"][0]
    oc = study["meta"]["headline_outst"]
    rows = []
    for scheme in study["meta"]["schemes"]:
        rs = [r for r in _rows_of(study, "sweep_outst", pattern=pattern,
                                  scheme=scheme)
              if r["core_outstanding"] == oc]
        if not rs:
            continue
        r = rs[0]
        rows.append([LABEL.get(scheme, scheme), r["makespan"], r["throughput"],
                     r["max_min"], r["retry_per_txn"],
                     r["outst_eff"], r["ooo_frac"], r.get("lat_p99"),
                     r.get("rate_mean") or "—", r.get("n_mark") or "—"])
    if not rows:
        return ""
    return _table(["方案", "makespan", "吞吐", "max/min", "重试/事务",
                   "有效 outstanding", "乱序比例", "延迟 p99",
                   "平均注入速率", "ECN 标记数"], rows)


def _static_rate_table(f: dict) -> str:
    """No controller at all: what does pinning the rate buy?"""
    best = f.get("rate_best") or {}
    rows = []
    for r in f.get("rate_rows") or []:
        star = " ★" if r is best else ""
        rows.append([f"{r['pace']}{star}", r["makespan"], r["throughput"],
                     r["retry_per_txn"], r["outst_eff"], r["outst_used"],
                     r["max_min"], r.get("lat_p99")])
    if not rows:
        return ""
    return _table(["钉死的注入速率 REQ/cycle/core", "makespan", "吞吐",
                   "重试/事务", "有效 outstanding", "已分配均值", "max/min",
                   "延迟 p99"], rows)


def _cost_table(pat: dict, s0: dict) -> str:
    """What each scheme actually costs in hardware."""
    oc = {r["overcommit"]: r for r in pat.get("sweep_oc", [])}
    base_peak = (oc.get(None) or {}).get("peak_grants")
    fc15 = pat["schemes"].get("S15", {}).get("fc") or {}
    fc16 = pat["schemes"].get("S16", {}).get("fc") or {}
    posts = max(1, fc15.get("bus_posts", 1))
    rows = [
        ["专用拥塞总线", "无", f"有，{fc15.get('bus_bits', 0) // posts} bit "
                              f"× {fc15.get('bus_posts')} 次", "无", "无",
         "无"],
        ["环上槽预约逻辑", "无", f"有，{fc15.get('n_reserved', 0)} 次预约",
         "无", "无", "无"],
        ["新增报文类型", "无", "无（走总线）", "无（复用 DBIDResp）",
         "无（RTT 从 DBIDResp 量）",
         "无（标记位搭 DBIDResp / RetryAck，不需要 CNP）"],
        ["completer 写缓冲（峰值授权）",
         f"{base_peak}（≈{(base_peak or 0) * 4} flit，由 tracker 夹住）",
         f"{base_peak}（同基线，不额外约束）",
         f"{fc16.get('overcommit')}（≈{fc16.get('peak_buf_flits')} flit，"
         f"主动压到 tracker 之下）",
         f"{base_peak}（同基线）", f"{base_peak}（同基线）"],
        ["核内速率控制器", "无", "每 (node,VC) AIMD 预算 + 累计欠账", "无",
         "每 core：漏桶 + minRTT + RTT 梯度 EWMA",
         "每 core：漏桶 + α EWMA + 两个定时器"],
        ["completer 侧状态", "无", "无", "每源 core 的授权队列 + 累计服务量",
         "无", "tracker 占用率比较器 + RED 随机数"],
        ["需要精确时间戳", "否", "否", "否",
         "<b>是</b>（RTT 是唯一信号）", "否"],
    ]
    return _table(["代价项", "S0", "S15", "S16", "S17", "S18"], rows)


def _fc_table(pat: dict) -> str:
    rows = []
    for s in ("S1", "S15"):
        fc = pat["schemes"][s].get("fc") or {}
        posts = max(1, fc.get("bus_posts", 1))
        rows.append([LABEL[s], fc.get("window"), fc.get("bus_posts"),
                     fc.get("bus_bits", 0) // posts, fc.get("bus_bits"),
                     fc.get("n_fc_deny"), fc.get("n_aimd_decrease"),
                     fc.get("n_aimd_increase"), fc.get("n_reserved", 0),
                     fc.get("n_reserve_used", 0)])
    return _table(["方案", "window", "广播次数", "每次 bit", "总 bit",
                   "预算拒绝", "AIMD 降", "AIMD 升", "预约槽",
                   "预约命中"], rows)


def _retry_facts(study: dict) -> dict:
    """The handful of numbers sections 9 and 10 and the conclusion share."""
    m = study["meta"]
    pats = m["patterns"]
    oc = m["headline_outst"]
    f: dict = {"oc": oc, "track": m["ha_track"], "pats": pats,
               "s16_oc": m.get("s16_overcommit")}

    def at(pattern: str, scheme: str, cap: int) -> dict:
        rs = [r for r in _rows_of(study, "sweep_outst", pattern=pattern,
                                 scheme=scheme) if r["core_outstanding"] == cap]
        return rs[0] if rs else {}

    for pattern in pats:
        rs = sorted(_rows_of(study, "sweep_outst", pattern=pattern,
                             scheme="S0"), key=lambda r: r["core_outstanding"])
        best = max(rs, key=lambda r: r["throughput"])
        f[pattern] = {
            "best": best, "lo": rs[0], "hi": rs[-1], "rows": rs,
            "drop": 100.0 * (rs[-1]["throughput"] - best["throughput"])
            / max(1e-9, best["throughput"]),
            "head": at(pattern, "S0", oc),
        }
    f["drift"] = f[pats[0]]["best"]["core_outstanding"] != \
        f[pats[-1]]["best"]["core_outstanding"]
    f["rate"] = {}
    base = at(pats[0], "S0", oc)
    f["base"] = base
    for scheme in ("S16", "S17", "S18"):
        r = at(pats[0], scheme, oc)
        if not r:
            continue
        f["rate"][scheme] = dict(
            r, d_thr=100.0 * (r["throughput"] - base["throughput"])
            / max(1e-9, base["throughput"]),
            d_retry=100.0 * (r["retry_per_txn"] - base["retry_per_txn"])
            / max(1e-9, base["retry_per_txn"]))
    sr = sorted(study.get("sweep_rate") or [], key=lambda r: r["pace"])
    f["rate_rows"] = sr
    if sr:
        f["rate_best"] = b = max(sr, key=lambda r: r["throughput"])
        b["d_thr"] = 100.0 * (b["throughput"] - base["throughput"]) \
            / max(1e-9, base["throughput"])
        for scheme in ("S17", "S18"):
            if scheme in f["rate"]:
                f["rate"][scheme]["gap"] = 100.0 * (
                    f["rate"][scheme]["throughput"] - b["throughput"]
                ) / max(1e-9, b["throughput"])
    tr = study.get("sweep_track") or []
    f["track_tight"] = min(tr, key=lambda r: r["ha_track"] or 1 << 30) if tr \
        else {}
    f["track_inf"] = next((r for r in tr if not r["ha_track"]), {})
    ab = {(r["ha_track"], r["inorder_retire"]): r
          for r in study.get("ablate_order") or []}
    f["ab"] = ab
    return f


def _retry_conclusion(f: dict) -> str:
    """The second reason flow control is needed, for the summary box."""
    u, oc = f["pats"][0], f["oc"]
    a, b = f[u]["best"], f[u]["hi"]
    other = f["pats"][-1]
    hd = f["base"]
    s16 = f["rate"].get("S16", {})
    s17 = f["rate"].get("S17", {})
    s18 = f["rate"].get("S18", {})
    rb = f.get("rate_best") or {}
    return f"""
<li><b>流控的第二个理由：没有流控时 outstanding 开大反而更慢，
因为 completer 会 RetryAck。</b>
给每个 completer 一个 {f['track']} 表项的 CHI 请求 tracker 之后，
S0 的吞吐对标称 outstanding 呈<b>倒 U 形</b>：
{a['core_outstanding']} 是最优点（{a['throughput']} flit/cycle），
继续开到 {b['core_outstanding']} 反而掉到 {b['throughput']}
（<b>{f[u]['drop']:+.1f}%</b>）。原因不是环挤了，
而是 <b>outstanding 槽位被停摆的事务占住了</b>：
在 outstanding={oc} 时，平均 <b>{hd['outst_used']}</b> 个槽位被分配，
其中 <b>{hd['outst_park']}</b> 个正在等 PCrdGrant，
真正在推进的只有 <b>{hd['outst_eff']}</b> 个
（<b>标称的 {100.0 * hd['outst_eff'] / oc:.0f}%</b>）。
每笔事务平均要被退回 <b>{hd['retry_per_txn']}</b> 次，
接受顺序里 <b>{100 * hd['ooo_frac']:.0f}%</b> 的事务被后发的事务超越。</li>

<li><b>最优 outstanding 随场景漂移，所以静态值调不出来。</b>
同一套硬件上，{u} 的最优点在 <b>{a['core_outstanding']}</b>，
而 {other} 的最优点在
<b>{f[other]['best']['core_outstanding']}</b>
（{f[other]['best']['throughput']} flit/cycle）。
{'两者不重合' if f['drift'] else '两者恰好重合'}——
{'把任何一个值写死，另一个场景就要付吞吐' if f['drift'] else ''}。
这正是需要<b>动态流控</b>而不是一个调好的常数的原因。</li>

<li><b>重试是纯浪费：只要把注入速率限对，吞吐反而涨
{rb.get('d_thr', 0):+.0f}%。</b>
把漏桶速率钉成常数、不用任何控制器，扫一遍发现
<b>{rb.get('pace')} REQ/cycle/core</b> 这一点吞吐
{rb.get('throughput')}（S0 是 {hd['throughput']}），
同时重试从 {hd['retry_per_txn']} 掉到 {rb.get('retry_per_txn')}。
<b>但这个窗口窄到 ±30% 的误差就吃掉全部收益</b>，
而最优值同时取决于 tracker 大小和 workload。
<b>好速率存在但猜不到</b>，这是"必须动态"最直接的证据。</li>

<li><b>TIMELY（S17）与 DCQCN（S18）能自动找到那个速率，
但反应式控制吃不到全部收益。</b>
在 outstanding={oc}、tracker={f['track']} 下，
S17 把重试从 {hd['retry_per_txn']} 降到
<b>{s17.get('retry_per_txn')}</b>，吞吐
<b>{s17.get('d_thr', 0):+.1f}%</b>；
S18 降到 <b>{s18.get('retry_per_txn')}</b>，吞吐
<b>{s18.get('d_thr', 0):+.1f}%</b>。
两者的<b>平均</b>速率都逼近最优点，但一直在振荡，
所以比钉死最优速率还差 {abs(s17.get('gap', 0)):.0f}% /
{abs(s18.get('gap', 0)):.0f}%。
信号都不要新报文：TIMELY 量的是协议本来就要发的
<code>DBIDResp</code> 往返，DCQCN 的标记算在 completer 的 tracker
占用率上、搭 1 bit 在同一个 <code>DBIDResp</code> / <code>RetryAck</code>
上，<b>连 CNP 都不需要</b>。
<span class="note">重要前提：<b>两篇论文的阈值常数必须重设</b>。
照搬 <code>T_high = 4·minRTT</code> 会让 S17 的吞吐掉到 S0 的 1/40
——本环 RTT 的主要成分是 completer 该有的服务队列，
不是不该有的网络排队（见 10.3.1）。</span></li>

<li><b>但速率控制管不了公平：它改的是<u>速率</u>，
不是<u>谁能用这一拍的槽位</u>。</b>
S17 / S18 的 max/min 是 {s17.get('max_min')} / {s18.get('max_min')}，
几乎就是 S0 的 {hd['max_min']}，而 S16 是 {s16.get('max_min')}。
在环绝对优先下源端限速造不出槽位（第 5.2 节）。
<b>所以两者互补</b>：S16 管公平与缓冲上限，
S17/S18 管"把标称 outstanding 自动压到有效 outstanding 附近"。</li>
"""


def _retry_sections(study: dict, imgs: dict, meta: dict, pat: dict) -> str:
    f = _retry_facts(study)
    m = study["meta"]
    kn = m.get("knobs") or {}
    peak = max((r.get("max_ha_used") or 0
                for r in study.get("sweep_track") or []), default=0)
    u, other = f["pats"][0], f["pats"][-1]
    oc, track = f["oc"], f["track"]
    hd, lo = f["base"], f[u]["lo"]
    ub, ob = f[u]["best"], f[other]["best"]
    tt, ti = f["track_tight"], f["track_inf"]
    ab_inf_o, ab_inf_i = f["ab"].get((0, False), {}), f["ab"].get((0, True), {})
    ab_fin_o, ab_fin_i = f["ab"].get((track, False), {}), \
        f["ab"].get((track, True), {})
    s17 = f["rate"].get("S17", {})
    s18 = f["rate"].get("S18", {})
    s16 = f["rate"].get("S16", {})
    rb = f.get("rate_best") or {}
    # The two rates either side of the best one, to show how narrow it is.
    rr = f.get("rate_rows") or [{}]
    i = rr.index(rb) if rb in rr else 0
    rlo, rhi = rr[max(0, i - 1)], rr[min(len(rr) - 1, i + 1)]
    return f"""
<h2>9. 第二个理由：outstanding 开大之后重试爆炸，有效 outstanding 反而变少</h2>
<p>第 3 节已经给出了基线（tracker = {study['meta']['ha_track']}）
与放开 tracker 的对照：<b>让 completer 变成无限接收资源，
基线策略实测峰值会同时压着 {peak} 个未完成请求</b>，
真实的 HA 不可能有那么大的请求 tracker。
前面几节关心的是这个压力对<b>公平性</b>做了什么，
本节关心它对<b>效率</b>做了什么——为什么把 outstanding 开大不再有收益，
以及为什么最优值不是一个可以静态写死的常数。</p>

<h3>9.1 CHI RetryAck / PCrdGrant 机制与建模</h3>
<div class="def"><b>CHI 对"completer 满了"的回答不是排队，而是退回。</b>
completer 的请求 tracker 没有空位时，它回一个 <code>RetryAck</code>
把请求方打发走；请求方<b>不得自行重发</b>，必须等到一个
<code>PCrdGrant</code>（protocol credit grant）才能再送一次。
两者都是单 flit 的 RSP，<b>不需要新增 VC，也不需要新增总线</b>。</div>

<p>建模要点，以及每一条为什么必须这样：</p>
<ul>
<li><b>信用是<u>预留</u>的</b>：发出 <code>PCrdGrant</code> 的那一刻就把
tracker 表项记在被授信者名下，重发的 REQ 到达时无条件接受。
否则一个新发的 REQ 会抢走这个表项，被授信者再次被退回，
形成活锁。</li>
<li><b>重发的 REQ 不再占一个 outstanding 槽位</b>：它从第一次上环起就一直
占着同一个槽位。若重发时再检查 outstanding 上限，
当所有槽位都被停摆事务占满时，能释放槽位的那次重发反而永远上不了环——死锁。</li>
<li><b>重发的 REQ 走上环端口的<u>优先</u>通路</b>：上环队列里排着的是
"还没发出去的活"，而重发是"已经发出去、手上有信用的活"。
把它排在后面同样会死锁，因为前面那些请求正被 outstanding 上限拒绝。
物理上这就是 AIC 的 outstanding tracker 直接驱动上环端口的一个 mux。</li>
<li><b>tracker 表项在 completer 发出 <code>Comp</code> 时释放</b>，
随即把信用交给等待队列的队首。</li>
<li><b>一笔重试的净开销</b>：白跑一趟的 REQ + 1 个 RetryAck +
1 个 PCrdGrant + 重发的 REQ，四份环上带宽，
一个字节的写数据都没搬动；加上整个往返期间那个 outstanding 槽位零进展。</li>
<li><b><code>ha_track = 0</code> 时全部逻辑惰性</b>，
与第 3 节那张对照表里"tracker = ∞"那一行完全等价（回归
<code>retry_off_equals_baseline</code> 逐位比对 makespan、
上环时刻、板载失败数），所以两个基线之间唯一的差别就是这一个参数。</li>
</ul>

<h3>9.2 outstanding 扫描：倒 U 形曲线</h3>
<img src="{imgs.get('outst', '')}" alt="outstanding sweep">
<p>S0 在 {u} 上的逐点数据（★ 为吞吐最优点）：</p>
{_outst_table(study, u)}
<div class="def bad"><b>两端都不好，原因完全不同。</b>
太小（{lo['core_outstanding']}）时一次重试都没有
（{lo['retry_per_txn']}），每个槽位都是有效槽位
（有效 {lo['outst_eff']} ≈ 已分配 {lo['outst_used']}），
但吞吐只有 {lo['throughput']}——<b>在飞的事务不够覆盖往返时延</b>。
太大（{f[u]['hi']['core_outstanding']}）时吞吐反而跌到
{f[u]['hi']['throughput']}（{f[u]['drop']:+.1f}%）：
已分配槽位涨到 {f[u]['hi']['outst_used']}，
其中 {f[u]['hi']['outst_park']} 个在等信用，
<b>有效槽位钉在 {f[u]['hi']['outst_eff']} 一动不动</b>。
最优点在 <b>{ub['core_outstanding']}</b>。</div>
<p class="note">注意<b>有效 outstanding 在拐点之后就饱和了</b>，
这是整节的核心：它的上限由 completer 的 tracker 决定
（{len(meta['mem_nodes'])} 个 completer × {track} 表项，
分给 {len(meta['core_nodes'])} 个 core），
标称值超过这条线之后，多出来的每一个槽位都只是多一个停摆的槽位。</p>

<h3>9.3 有效 outstanding：槽位到底去哪了</h3>
<div class="def">同一时刻，一个 core 手上的 outstanding 槽位分三类：
<b>（a）在推进</b>——请求已被接受，正在走 DBIDResp / WriteData / Comp；
<b>（b）停摆</b>——被 RetryAck 退回，在等 PCrdGrant，零进展；
<b>（c）队头阻塞</b>——事务其实已经做完了，但更老的事务还没退休，
槽位放不掉（只在按序退休时存在）。<br>
<b>有效 outstanding = 时间平均(已分配 − 停摆 − 队头阻塞)</b>，
每 {m['outst_sample']} 拍采样一次。</div>

<p>把 tracker 从紧到松扫一遍，看重试压力与乱序怎么跟着走：</p>
<img src="{imgs.get('retry', '')}" alt="retry vs tracker">
<div class="def">tracker = {tt.get('ha_track')} 时每笔事务要退回
{tt.get('retry_per_txn')} 次，有效槽位只剩 {tt.get('outst_eff')}；
tracker = ∞ 时一次不退，{ti.get('outst_eff')} 个槽位全部有效。
<b>重试不是网络拥塞，是接收端资源不足</b>——
这也说明为什么限制源端速率能缓解它。</div>

<h3>9.4 乱序的两个来源，以及按序退休的代价</h3>
<p>本模型里乱序有两个独立来源，必须分开说，否则会把网络本身的乱序
记到重试头上：</p>
<ul>
<li><b>双 plane 负载均衡</b>：一笔事务的 REQ 只走一个 plane，
两个 plane 的上环队列排空速度不同，所以<b>即使一次重试都没有</b>，
接受顺序也已经偏离发起顺序——
{u} 在 outstanding={lo['core_outstanding']} 时
乱序比例已有 {lo['ooo_frac']}，最大位移 {lo['ooo_max_disp']} 笔。
这是既有设计的固有属性，不是本节引入的。</li>
<li><b>重试</b>：在此之上叠加。同一 workload 开到
outstanding={oc} 时乱序比例升到 {hd['ooo_frac']}，
最大位移升到 {hd['ooo_max_disp']} 笔。
<b>增量才是重试的账</b>。</li>
</ul>
<p>如果 core 必须<b>按发起顺序</b>释放槽位（in-order 完成队列，
真实 AIC 常见），乱序就直接变成队头阻塞：</p>
{_order_table(study)}
<div class="def">读法分两段。<b>tracker = ∞</b>（没有重试）：
按序退休滞留 {ab_inf_i.get('outst_hol')} 个已完成事务的槽位
（峰值 {ab_inf_i.get('max_hol_hold')} 个），
有效槽位从 {ab_inf_o.get('outst_eff')} 掉到
{ab_inf_i.get('outst_eff')}；但吞吐几乎不动
（{ab_inf_o.get('throughput')} → {ab_inf_i.get('throughput')}），
因为剩下的并行度仍然够用——<b>浪费槽位不等于浪费吞吐</b>。<br>
<b>tracker = {track}</b>（有重试）：这里出现了本节最干净的一个结果。
乱序退休时浪费 = 停摆 {ab_fin_o.get('outst_park')}；
按序退休时浪费 = 停摆 {ab_fin_i.get('outst_park')} + 队头阻塞
{ab_fin_i.get('outst_hol')}。<b>两者的总和几乎相等</b>
（{ab_fin_o.get('outst_park') + ab_fin_o.get('outst_hol'):.1f} vs
{ab_fin_i.get('outst_park') + ab_fin_i.get('outst_hol'):.1f}），
有效槽位也几乎相等（{ab_fin_o.get('outst_eff')} vs
{ab_fin_i.get('outst_eff')}），吞吐同样几乎相等
（{ab_fin_o.get('throughput')} → {ab_fin_i.get('throughput')}）。<br>
<b>结论：浪费掉多少 outstanding 是 completer 的 tracker 决定的，
退休规则只决定这些浪费记在哪个账上</b>——
按序退休把"等信用"的停摆换成了"等前序"的队头阻塞，
总量不变。所以要提高有效 outstanding，
只能去动 tracker 侧的压力（限速或授权），改 core 的退休规则没用。</div>
<p class="note">建模时踩到一个真实的坑，值得记下来：
按序退休时<b>不能用"未完成事务计数"来做 outstanding 门槛</b>。
计数会被<b>更年轻的、已完成但不许退休的</b>事务填满，
而唯一能解开它们的那笔老事务就再也发不出去——真死锁。
正确的门槛是<b>发起序号的连续窗口</b>
（<code>seq &lt; retire_head + outstanding</code>），
也就是 reorder buffer 本来的样子。回归
<code>inorder_retire_never_better</code> 用一个足够长的批次钉住这一点。</p>

<h3>9.5 场景漂移：为什么必须动态</h3>
<p><code>{other}</code> 保持同样的角色分配，
只把所有写集中到 {len(m.get('hot_has', []))} 个相邻的 memory 节点上
（M{'、M'.join(str(x) for x in m.get('hot_has', []))}），
completer 侧压力大得多。</p>
{_drift_table(study)}
<div class="def {'bad' if f['drift'] else ''}">
{u} 的最优标称 outstanding 是 <b>{ub['core_outstanding']}</b>，
{other} 的是 <b>{ob['core_outstanding']}</b>。
<b>{'两者不重合' if f['drift'] else '两者重合'}</b>，
而且两个场景在最优点上的<b>有效</b> outstanding 差得更远
（{ub['outst_eff']} vs {ob['outst_eff']}）——
{other} 需要更大的标称值，才能换来更小的有效值。
表里最后两列是"把 outstanding 写死在 {oc}"要付的吞吐。
<b>一个静态常数没法同时服务两个场景，这就是需要动态流控的直接证据。</b></div>

<h2>10. rate-based 对照：TIMELY 与 DCQCN</h2>
<p>S15 和 S16 动的都是<b>谁能用这一拍</b>。数据中心传输领域从另一头解决同一个
问题：仲裁不动，<b>把源端的发送速率压下去</b>，让拥塞根本不形成。
这条路上有两个定义性方案，而且都能<b>不加任何新报文</b>地映射到 CHI。</p>

<h3>10.1 信号映射：两个都不需要新报文</h3>
<ul>
<li><b>S17 TIMELY（延迟型）</b>。信号是 RTT，而 CHI 本来就在量一个：
<code>WriteNoSnp</code> 规定拿到 <code>DBIDResp</code> 之前不许发数据，
所以<b>"REQ 上环 → DBIDResp 被排空"就是一个 RTT 样本</b>，
量在协议本来就要发的报文上，<b>零额外开销</b>。
TIMELY 的洞见是 <b>RTT 的梯度比绝对值更早</b>：队列还没堆起来时梯度就已经转正。
更新式用论文原式，阈值以实测 minRTT 的倍数表示
（<code>T_low = {kn.get('t_low_mult')}·minRTT</code>、
<code>T_high = {kn.get('t_high_mult')}·minRTT</code>、
β = {kn.get('timely_beta')}、δ = {kn.get('delta'):.5f}、HAI 门槛
{kn.get('hai_n')} 次）。
关键是<b>样本跨越重试往返</b>，所以被退回的请求会表现为一个很大的 RTT，
控制器看得见。</li>
<li><b>S18 DCQCN（ECN 型）</b>。无缓存环上<b>没有队列可以标记</b>——
按定义环上不存在占用率会越过阈值的缓冲。但产生重试的拥塞根本不在环上，
而在 <b>completer 的请求 tracker</b>，那个是有占用率的。
所以标记算在那里（RED：占用率低于 {kn.get('k_min')}·tracker 不标，
到 {kn.get('k_max')}·tracker 线性升到 {kn.get('p_max')}），
而<b>一个 RetryAck 就是概率 1 的标记</b>——completer 明说自己满了。
标记位搭在本来就要发的 <code>DBIDResp</code> / <code>RetryAck</code>
上（+1 bit），<b>连 CNP 报文都不需要</b>，
比真实 DCQCN 还便宜。速率侧是标准 QCN 状态机
（α 的 EWMA g = {kn.get('g'):.5f}、每 {kn.get('alpha_timer')} 拍最多降一次、
fast recovery {kn.get('fast_recovery')} 轮 → additive → hyper）。</li>
</ul>
<div class="def"><b>执行端两者相同</b>：REQ 上环前的一个漏桶，
令牌单位是 REQ/cycle。选 REQ 而不选 WriteData 有两个原因：
冲垮 tracker 的是 REQ 的到达率；而且没有 DBIDResp 就发不出 WriteData，
压住请求就等于压住数据。<b>outstanding 上限不动</b>，
所以这里量的是"同样的标称预算下，速率控制能捞回多少<b>有效</b>
outstanding"。速率钉在物理上限（每 plane 一个上环端口 = 2 REQ/cycle）时
S17 / S18 逐位复现 S0，回归 <code>rate_pinned_equals_s0</code> 保证了
漏桶是它们唯一改动的东西。</div>

<h3>10.2 先做对照实验：把速率钉死，不要控制器</h3>
<p>在评价两个控制器之前，先问一个更基本的问题：
<b>存在一个好的注入速率吗？</b>把漏桶的速率钉成常数
（<code>pace_min = pace_init = pace_max</code>，控制器完全不动），
扫一遍：</p>
{_static_rate_table(f)}
<div class="def good"><b>存在，而且收益很大。</b>钉在
<b>{rb.get('pace')} REQ/cycle/core</b> 时吞吐
<b>{rb.get('throughput')}</b>（比 S0 <b>{rb.get('d_thr', 0):+.1f}%</b>），
同时把重试从 {hd['retry_per_txn']} 压到 <b>{rb.get('retry_per_txn')}</b>，
max/min 也从 {hd['max_min']} 收到 {rb.get('max_min')}。
<b>这说明重试确实是纯浪费</b>——只要不去撞 tracker，
省下来的环上带宽直接变成吞吐。</div>
<div class="def bad"><b>但这个窗口非常窄。</b>速率再低一档
（{rlo.get('pace')}）吞吐掉到 {rlo.get('throughput')}，
<b>反而低于 S0 的 {hd['throughput']}</b>——completer 开始空转；
再高一档（{rhi.get('pace')}）重试立刻回到
{rhi.get('retry_per_txn')}，吞吐 {rhi.get('throughput')}。
<b>±{100 * (rhi.get('pace', 1) - rb.get('pace', 1))
/ max(1e-9, rb.get('pace', 1)):.0f}% 的速率误差就吃掉全部收益</b>，
而这个最优速率既取决于 tracker 大小，也取决于 workload（9.5 节）。
所以它必须被<b>自动找到</b>，不能写死——
这就是下面两个控制器要做的事。</div>

<h3>10.3 两个控制器的结果</h3>
<img src="{imgs.get('rate', '')}" alt="rate control traces">
<p>{u}，outstanding={oc}、tracker={track}
（S16 的 overcommit = {f['s16_oc']}，理由见 10.4）：</p>
{_rate_table(study)}
<div class="def">两个方案都把注入压到
{s17.get('rate_mean')} / {s18.get('rate_mean')} REQ/cycle/core
——<b>均值离最优的 {rb.get('pace')} 已经很近</b>——
重试从 {hd['retry_per_txn']} 降到
{s17.get('retry_per_txn')} / {s18.get('retry_per_txn')}，
吞吐 <b>{s17.get('d_thr', 0):+.1f}%</b> /
<b>{s18.get('d_thr', 0):+.1f}%</b>。
<b>方向对了，而且是自动找到的</b>，不需要知道 tracker 有多大。</div>
<div class="def bad"><b>但都没吃到全部收益</b>：
和钉死最优速率的 {rb.get('throughput')} 相比，
S17 差 <b>{s17.get('gap', 0):+.1f}%</b>、
S18 差 <b>{s18.get('gap', 0):+.1f}%</b>。
原因在左图看得很清楚：<b>均值对了，但一直在振荡</b>，
而 10.2 的曲线两侧都很陡，所以在最优点附近来回摆动的平均收益
低于稳定停在最优点。<b>这就是"反应式"的代价，
不是调参能消掉的</b>。<br>
公平性方面 max/min 基本没动（S0 {hd['max_min']} →
{s17.get('max_min')} / {s18.get('max_min')}），
远不如 S16 的 {s16.get('max_min')}。</div>

<h3>10.3.1 论文里的常数搬不过来</h3>
<div class="def"><b>直接用 TIMELY / DCQCN 论文的阈值会把系统限死。</b>
两者的默认值都假设"RTT 超出 minRTT / 队列非空"本身就是坏事。
在这里不是：RTT 的主要成分是 <b>completer 自己的服务队列</b>，
而那个队列<b>应该</b>非空——空了就是 completer 在空转。
本环空载 RTT 约 20 拍，而高效工作点的 RTT 在 150 拍附近，
所以 <code>T_high = 4·minRTT</code> 等于宣布"永久拥塞"，
控制器一路降到地板。实测：用论文值时 S17 的吞吐只有 0.117
（比 S0 差 40 倍），rate 被压到 1/512 就再也上不来。<br>
第二个陷阱是<b>反馈依附在流量上</b>：速率降到接近零之后，
带回 RTT 样本 / ECN 标记的报文也几乎没有了，
控制器<b>自己饿死了自己</b>，升不回去。所以速率地板
<code>pace_min = {kn.get('pace_min'):.4f}</code> 不是随便设的，
它必须高到让反馈回路继续有输入。<br>
第三个是<b>时间尺度</b>：QCN 的定时器在数据中心是微秒级，
搬到这里 <code>rate_timer = 300</code> 拍意味着控制器要连续两个往返
待在它已知安全的速率之下。改成 {kn.get('rate_timer')} 拍后
S18 从 4.03 抬到 {s18.get('throughput')}。<br>
<b>本节采用的阈值</b>：T_low/T_high = {kn.get('t_low_mult')}/{kn.get('t_high_mult')}
倍 minRTT，RED 区间 [{kn.get('k_min')}, {kn.get('k_max')}]·tracker、
p_max = {kn.get('p_max')}。都在
<code>rg_ring2_rate.py</code> 里连同理由一起记着。</div>

<h3>10.4 有限 tracker 补上了 S16 论证里的一个洞</h3>
<div class="def bad">之前的 S16 分析有一处不诚实：<code>gq</code> 可以无限排队 REQ。
tracker 有限之后，代价被诚实拆成两笔——
<b>便宜的 tracker 表项</b>（地址 + srcID + 少量状态）和
<b>贵的写数据缓冲</b>（每笔 {meta['W']} flit），
而 S0 把两者 1:1 绑死。S16 只压住后者。<br>
更要紧的是：<b>overcommit ≥ tracker 时 S16 完全退化成 S0</b>
——completer 手上的已接受请求本来就不可能超过 tracker 表项数，
授权泵永远不需要扣住任何授权。所以本节把 S16 的 overcommit 设为
{f['s16_oc']}（tracker 的一半）它才起作用；
而它起作用的方式是<b>让请求在已经占着 tracker 表项的状态下等授权</b>，
于是重试反而比 S0 <b>更多</b>
（{hd['retry_per_txn']} → {s16.get('retry_per_txn')}）。
两条都由回归 <code>s16_grants_below_tracker</code> 钉住。<br>
<b>这是一个真实的取舍，不是 S16 的反例</b>：S16 用更多的廉价 tracker
压力换来 1/N 的昂贵数据缓冲。但它说明 S16 的 overcommit
必须和 completer 的 tracker 一起选，不能各自独立调。</div>

<h3>10.5 rate-based 只解决一半</h3>
<ol>
<li><b>反应式，必然过冲。</b>控制器只能在 RTT 已经涨上去、
或者请求已经被退回之后才降速，每一次都是先付出代价再纠正。
10.3 已经把这笔账量出来了：均值找对了，振荡还要吃掉
{abs(s17.get('gap', 0)):.0f}%~{abs(s18.get('gap', 0)):.0f}%。
S16 的授权是<b>先申请后使用</b>，结构上不存在过冲。</li>
<li><b>在环绝对优先下，源端限速造不出槽位。</b>这正是第 5.2(c) 节
S1 失败的同一条理由：让上游少发，让出来的空拍会被下一个过路 flit
顺手拿走，弱者拿不到。所以速率控制能减少<b>浪费</b>
（少退回、少白跑），但不能改变<b>分配</b>——
表里 max/min 几乎不动就是这一点的直接证据。</li>
<li><b>它们管的是错误的量。</b>需要被限制的是 completer
的接收资源占用；rate-based 通过限制源端速率<b>间接</b>影响它，
S16 直接控制它。间接的代价就是 10.2 里那点残余重试。</li>
</ol>
<div class="def good"><b>因此两者互补，不是竞争。</b>
S16（授权调度）负责<b>公平与缓冲上限</b>，
S17/S18（速率控制）负责<b>把标称 outstanding 压到有效 outstanding
附近，省掉白跑的重试</b>。而 9.5 已经证明这个"附近"随场景漂移，
必须动态确定——这就是速率控制在这套系统里真正的位置：
不是替代授权，而是<b>自动找到那个不该写死的 outstanding</b>。</div>

<h3>10.6 代价对比（含 S17 / S18）</h3>
{_cost_table(pat, {})}
<p class="note">S17 唯一的额外要求是<b>精确时间戳</b>：RTT 是它唯一的信号，
时钟域或测量点的抖动会直接变成误判。S18 不需要时间戳，
但需要在 completer 侧加一个占用率比较器和一个随机数源。
两者都不需要新报文、不需要总线、不碰环上仲裁。</p>
"""


# ---------------------------------------------------------------------------

def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"missing {DATA}; run utils/dse_ring2_write_fair.py")
    d = json.loads(DATA.read_text())
    meta = d["meta"]
    pat = d["patterns"]["uniform"]
    cap = meta["hop_bw_cap"]

    imgs = {}
    p = IMG / "ring2_wfair_topo.png"
    plot_topology(meta, p)
    imgs["topo"] = p.name
    for tag, fn in (("bars", plot_bw_bars), ("panels", plot_bw_panels),
                    ("overlay", plot_bw_overlay), ("scatter", plot_scatter)):
        p = IMG / f"ring2_wfair_{tag}.png"
        fn(pat, p)
        imgs[tag] = p.name
    p = IMG / "ring2_wfair_hopbw.png"
    plot_hop_bw(pat, cap, p)
    imgs["hopbw"] = p.name
    if (pat["schemes"].get("S1") or {}).get("fc", {}).get("trace"):
        p = IMG / "ring2_wfair_s1trace.png"
        plot_s1_trace(pat, p)
        imgs["s1trace"] = p.name
    study = d.get("retry_study")
    if study:
        for tag, fn in (("outst", plot_outst_sweep), ("retry", plot_retry_track),
                        ("rate", plot_rate_trace)):
            p = IMG / f"ring2_wfair_{tag}.png"
            fn(study, p)
            imgs[tag] = p.name

    s0 = pat["schemes"]["S0"]["fairness"]
    s1 = pat["schemes"]["S1"]["fairness"]
    s15 = pat["schemes"]["S15"]["fairness"]
    s16 = pat["schemes"]["S16"]["fairness"]
    rc = pat["root_cause"]
    t1 = 100.0 * (s1["throughput"] - s0["throughput"]) / s0["throughput"]
    t15 = 100.0 * (s15["throughput"] - s0["throughput"]) / s0["throughput"]
    t16 = 100.0 * (s16["throughput"] - s0["throughput"]) / s0["throughput"]
    fc16 = pat["schemes"]["S16"].get("fc") or {}
    oc_rows = {r["overcommit"]: r for r in pat.get("sweep_oc", [])}
    base_peak = (oc_rows.get(None) or {}).get("peak_grants") or 0
    buf_ratio = base_peak / max(1, fc16.get("overcommit") or 1)
    lat0 = pat["schemes"]["S0"].get("lat_p99")
    lat16 = pat["schemes"]["S16"].get("lat_p99")
    lat15 = pat["schemes"]["S15"].get("lat_p99")

    # The same baseline with an unlimited tracker: the ring-limited reference
    # the finite tracker is compared against.
    ref = pat.get("s0_unbounded") or {}
    sref = ref.get("fairness") or s0
    rcref = pat.get("root_cause_unbounded") or rc
    q0 = pat["schemes"]["S0"].get("retry") or {}
    qref = ref.get("retry") or {}
    t_ref = 100.0 * (s0["throughput"] - sref["throughput"]) \
        / max(1e-9, sref["throughput"])

    bw0 = sref["bw_by_core"]
    adj = {str(r["core"]): r.get("adj_mem") for r in rcref["rows"]}
    losers = sorted((c for c in bw0 if adj.get(c) == 1), key=int)
    winners = sorted((c for c in bw0 if adj.get(c) == 2), key=int)
    lo_s = "、".join(f"C{c}" for c in losers)
    hi_s = "、".join(f"C{c}" for c in winners)
    lo_bw = max(bw0[c] for c in losers) if losers else 0.0
    hi_bw = min(bw0[c] for c in winners) if winners else 0.0
    mean_hop = rc["rows"][0].get("mean_hop_to_mem", 0.0)

    # Judge on the whole seed sweep, not just the headline seed: the
    # reservation mechanism is discrete and one seed can flatter a tuning.
    def _verdict(scheme: str, fall: dict, tfall: float) -> dict:
        """Judge on the whole seed sweep, not just the headline seed."""
        sw = [r for r in pat.get("seed_sweep", []) if r.get(scheme)]
        ms = [r[scheme]["max_min"] for r in sw] or [fall["max_min"]]
        ts = [r[scheme]["thr_delta_pct"] for r in sw] or [tfall]
        hit = (max(ms) <= 1.05, min(ts) >= -1.0)
        names = ("max/min ≤ 1.05", "吞吐差 ≤ 1%")
        good = [n for n, v in zip(names, hit) if v]
        bad = [n for n, v in zip(names, hit) if not v]
        return {
            "n": len(sw), "hit": hit, "bad": bad,
            "verdict": ("全部达标" if not bad else
                        (("达成 " + "、".join(good) + "；") if good else "") +
                        "未达成 " + "、".join(bad)),
            "rng_m": f"{min(ms):.3f} ~ {max(ms):.3f}",
            "rng_t": f"{max(ts):+.1f}% ~ {min(ts):+.1f}%",
            "t_worst": min(ts), "m_worst": max(ms),
        }

    v15 = _verdict("S15", s15, t15)
    v16 = _verdict("S16", s16, t16)
    verdict, bad = v15["verdict"], v15["bad"]
    n_seed = v15["n"]
    rng_m, rng_t = v15["rng_m"], v15["rng_t"]

    # Name the binding bound from the data so the prose cannot go stale.
    _lb_txt = {
        "link_lb": "最忙的那条有向链路上、DAT VC 的容量",
        "port_lb": "每 (node, plane) 只有一个上下环端口，三条 VC 共享",
        "cut_lb": "跨割面的流量除以割面上的有向链路数",
        "txn_lb": "单笔事务四拍握手的串行链",
    }
    b = pat["bounds"]
    bind_key = max(_lb_txt, key=lambda k: b.get(k, 0))
    bind_lb = {"link_lb": "LB_link", "port_lb": "LB_port",
               "cut_lb": "LB_cut", "txn_lb": "LB_txn"}[bind_key]
    bind_txt = _lb_txt[bind_key]

    demo = [1.0] * 9 + [0.1]
    jain_demo = sum(demo) ** 2 / (len(demo) * sum(v * v for v in demo))
    # Why Jain was dropped, measured rather than asserted: across the four
    # schemes it moves by a fraction of a percent while max/min moves by tens.
    _js = [pat["schemes"][s]["fairness"]["jain"] for s in SCHEMES]
    _ms = [pat["schemes"][s]["fairness"]["max_min"] for s in SCHEMES]
    j_spread = 100.0 * (max(_js) - min(_js)) / max(1e-9, min(_js))
    m_spread = 100.0 * (max(_ms) - min(_ms)) / max(1e-9, min(_ms))

    sec9 = _retry_sections(study, imgs, meta, pat) if study else ""
    concl_retry = _retry_conclusion(_retry_facts(study)) if study else ""

    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>无缓存环上的 per-core 写带宽公平性</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, "WenQuanYi Micro Hei",
       "Noto Sans CJK SC", sans-serif;
       margin: 2rem auto; max-width: 980px; color: #111; line-height: 1.65; }}
h1,h2,h3 {{ font-weight: 650; }}
h2 {{ margin-top: 2.2rem; border-bottom: 2px solid #e2e8f0;
      padding-bottom: 0.25rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
th,td {{ border: 1px solid #e5e7eb; padding: 0.35rem 0.5rem; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
td:last-child, th:last-child {{ text-align: left; }}
th {{ background: #f8fafc; }}
code {{ background: #f1f5f9; padding: 0.1rem 0.3rem; }}
img {{ max-width: 100%; border: 1px solid #e5e7eb; }}
.note {{ color: #475569; font-size: 0.9rem; }}
.def {{ background: #f8fafc; border-left: 3px solid #94a3b8;
        padding: 0.5rem 0.9rem; margin: 0.7rem 0; font-size: 0.93rem; }}
.bad {{ border-left-color: #dc2626; background: #fef2f2; }}
.good {{ border-left-color: #16a34a; background: #f0fdf4; }}
.key {{ background: #eff6ff; border: 1px solid #bfdbfe; border-left: 4px solid
        #2563eb; padding: 0.8rem 1.1rem; margin: 1rem 0; border-radius: 4px; }}
.key ol {{ margin: 0.4rem 0 0 1.1rem; }}
.key li {{ margin: 0.45rem 0; }}
</style></head><body>

<h1>无缓存环上的 per-core 写带宽公平性</h1>
<p class="note">workload：<b>{len(meta['core_nodes'])} 个 AI core 对
{len(meta['mem_nodes'])} 个 memory 节点做均匀写</b>，每 core
{meta['K']} 笔 <code>WriteNoSnp</code>、每笔 {meta['W']} 个 WriteData flit
（每 core {pat['flits_per_core']} 个数据 flit，共 {pat['n_txn']} 笔事务）。
节点 9、19 既不是 memory 也不是 AI core。</p>

<h2>结论</h2>
<div class="key">
<ol>
<li><b>基线现在带一个有限的 completer 请求 tracker（{meta.get('ha_track')}
个表项），这改变了整份报告的主线。</b>
tracker 满时 completer 必须回 <code>RetryAck</code>，
在 outstanding = {meta.get('core_outstanding')} 下
<b>几乎每笔事务都要被弹回一次</b>（{q0.get('retry_per_txn')} 次/事务）。
瓶颈因此从"环上的槽位"移到了"completer 的表项"，
吞吐从放开 tracker 时的 {sref['throughput']} 掉到
<b>{s0['throughput']}</b> flit/cycle（<b>{t_ref:+.1f}%</b>）。</li>

<li><b>位置相关的失衡真实存在，但只在环受限时才显露。</b>
放开 tracker（环是唯一约束）时 max/min = <b>{sref['max_min']}</b>，
最慢 {sref['bw_min']} vs 最快 {sref['bw_max']} flit/cycle，
而需求完全对称。<b>加上有限 tracker 之后它被压到
{s0['max_min']}</b>——不是被修好，而是 retry 背压
把所有 core 一起拖慢，位置优势换不到一个 tracker 表项（见 4.4）。
<b>这是本报告最重要的一条：公平性指标变好可能只是因为大家一起变慢了，
所以公平性必须和吞吐一起读。</b></li>

<li><b>根因是“身边有几个 mem”，不是“离 mem 多远”。</b>
9 和 19 在环上正好对顶，把这一对从 memory 里去掉之后，
每个 core 到 8 个 mem 的<b>平均跳数仍然全部等于 {mean_hop} 跳</b>，
r(带宽, 平均跳数) = <b>{rcref['corr_bw_meanhop']}</b>，没有解释力。
真正决定带宽的是<b>紧邻的 mem 个数</b>：
r = <b>{rcref['corr_bw_adjmem']}</b>（Spearman
{rcref['rank_bw_adjmem']}）。
{lo_s} 各只有 1 个相邻 mem（另一侧正对着非终端的 9 或 19），
带宽全部 ≤ {lo_bw}；其余 {hi_s} 两侧都是 mem，带宽全部 ≥ {hi_bw}。
链条是：紧邻 mem 多 → 短程写多 → 过路流量少 → 上环成功率高。</li>

<li><b>S1（拥塞等级 + AIMD）不但没修好，还把两个指标同时弄坏。</b>
max/min {s0['max_min']} → <b>{s1['max_min']}</b>，
吞吐 <b>{t1:+.1f}%</b>。三条失效机理都被数据证实，且都不是调参能修的：
<b>(a)</b> max 聚合让同一通路上的 core 乘同一个 α，等比缩小不改变贫富比值；
<b>(b)</b> 差值规则让受害者惩罚自己——它 <code>own_total_fail</code> 高，
而赢家的 <code>net_fail</code> 低，于是差值大的反倒是受害者；
<b>(c)</b> 在环流量绝对优先，源端限速让出的空拍立刻被过路 flit 吃掉，
<b>造不出槽位</b>。</li>

<li><b>S15 / S16 在这个基线上都变成了"用吞吐买一点公平"的差交易。</b>
S0 本身已经接近均衡（跨 {n_seed} 个种子 max/min
{min(r['S0']['max_min'] for r in pat['seed_sweep']):.3f} ~
{max(r['S0']['max_min'] for r in pat['seed_sweep']):.3f}），
留给公平性方案的空间很小：
S16 把 max/min 稳定收到 {v16['rng_m']}（唯一稳定有效的），
S15 是 {rng_m}（<b>并不稳定，个别种子上还不如 S0</b>），
而两者的吞吐代价都是 {rng_t} / {v16['rng_t']}。
<b>按验收线（max/min ≤ 1.05 且吞吐差 ≤ 1%）判定：
S15 {verdict}；S16 {v16['verdict']}。</b>
在无限 tracker 的参照上这两个方案都明显更值
（那里有 max/min = {sref['max_min']} 的不公平可供消除），
<b>一旦承认 completer 有限，该优先解决的就不是公平性了。</b></li>

<li><b>真正该花力气的地方是那 {abs(t_ref):.0f}% 的重试浪费。</b>
它与公平性无关，也不是任何一个公平性方案能碰到的：
一笔被弹回的事务白跑一个 REQ、一个 RetryAck、一个 PCrdGrant，
并且在整个往返期间占着 outstanding 槽位零进展——
标称 {meta.get('core_outstanding')} 个槽位里只有
{q0.get('outst_eff_mean')} 个真正在推进。第 9、10 节专讲这件事。</li>

<li><b>在这两个方案之间，授权仍然比槽预约便宜。</b>
预约一个环上槽位意味着<b>禁止</b>上游注入该槽，
预约者若没用上，这一拍就白扔了；扣住一个授权只是让占优的 core
<b>手上暂时没数据</b>，槽位仍然归"谁能用谁用"。
所以在同样的吞吐代价下 S16 换到的公平性更多
（max/min {s16['max_min']} vs S15 的 {s15['max_min']}），
而且不需要总线、不需要槽预约逻辑。
<span class="note">注意 S16 在无限 tracker 的参照上是<b>吞吐更高</b>的
（拉平速率消掉了尾部拖延）；在有限 tracker 的基线上这份收益被
retry 背压提前吃掉了，只剩下 {t16:+.1f}% 的净代价，见 7.3。</span></li>

{concl_retry}
<li><b>全程严格无缓存。</b>所有方案的
<code>n_inring_blocked = 0</code>、<code>max_inring_hold = 0</code>：
S15 的预约只压制<b>上游注入</b>，从不停住已经在环上的 flit；
S16 根本不碰环上仲裁。两者都不需要在环上增加缓冲。</li>
</ol>
</div>

<p class="note"><b>四个方案一眼看完</b>（seed {meta['seed']}，
括号内为相对基线的吞吐差）：
S0 max/min {s0['max_min']} ·
S1 {s1['max_min']}（{t1:+.1f}%）·
S15 {s15['max_min']}（{t15:+.1f}%）·
<b>S16 {s16['max_min']}（{t16:+.1f}%）</b>。</p>

<h2>1. 拓扑与硬件配置</h2>
<img src="{imgs['topo']}" alt="topology">
{_setup_table(meta)}

<h3>1.1 每条边的 hop 时延</h3>
<p>边上的数字是<b>该无向边的 hop 时延（拍）</b>，两个方向相同。
节点 index 顺时针递增（+1 = CW）。</p>
{_link_table(meta)}

<h3>1.2 协议：CHI WriteNoSnp 四拍握手</h3>
<p>一笔写 = <code>REQ(core→mem)</code> → <code>DBIDResp(mem→core)</code>
→ <code>WriteData×{meta['W']}(core→mem)</code> →
<code>Comp(mem→core)</code>，因此实例化 REQ / RSP / DAT
三条独立 CHI VC。<b>per-core 写带宽 = 该 core 在争用窗口内成功上环的
WriteData flit / cycle。</b></p>

<h3>1.3 前提：环是无缓存的，在环流量绝对优先</h3>
<p><code>_launch</code> 从不阻塞已在环上的 flit，只占用槽位；本地注入由
<code>_can_board</code> 拒绝——要么该有向 hop 的这条 VC 被占，要么
<code>arr_set</code> 显示 σ 拍内有在环 flit 即将到达。</p>
<div class="def">在环流量<b>先于</b>本地注入预定槽位。一个节点想上环，
必须等到一个没有任何过路 flit 经过的空拍。
关键推论：<b>源端限速无法凭空造出槽位</b>——让上游少发，
让出来的空拍会被下一个过路 flit 顺手拿走。这决定了第 5 节 S1 为什么失败。</div>

<h3>1.4 前提：completer 的接收资源是有限的</h3>
<p>每个 completer 有一个 <b>{meta.get('ha_track')} 表项的请求 tracker</b>，
一个 REQ 从被接受起占用一个表项，直到该 completer 发出 <code>Comp</code>
才释放。<b>表项用完时 completer 不排队，而是按 CHI 规定回
<code>RetryAck</code> 把请求方打发走</b>，请求方必须等到一个
<code>PCrdGrant</code> 才能重发（机制细节见 9.1）。</p>
<div class="def">这是<b>基线的一部分，不是某个方案的功能</b>：
S0 / S1 / S15 / S16 全部在同一个 tracker 预算下测量。
把它做成有限的理由很直接——放开之后，基线策略实测峰值会同时压着
<b>{qref.get('max_ha_used')}</b> 个未完成请求
（每个还对应 {meta['W']} flit 的写数据缓冲），真实的 HA 不会有那么大的
tracker。每 core 的 outstanding 上限是
<b>{meta.get('core_outstanding')}</b>，远大于 tracker，
所以本报告的基线是一个<b>重试压力饱和</b>的工作点：
平均每笔事务被退回 {q0.get('retry_per_txn')} 次。
第 3 节量化它对公平性的影响，第 9 节量化它对效率的影响。</div>

<h2>2. 两个指标：max/min 与吞吐</h2>
<p>设 <i>n</i> 个 core 实测到的写带宽为
<i>x</i><sub>1</sub>, …, <i>x</i><sub>n</sub>（单位 WriteData flit/cycle，
统计窗口是所有 core 都还在发的争用窗口）。全文只用两个数：</p>
<ul>
<li><b>max/min</b> = max <i>x<sub>i</sub></i> / min <i>x<sub>i</sub></i>。
公平性看这一个，因为它<b>直接读最坏的那个 core</b>：
1.0 是完全均等，1.2 就是最慢的 core 只有最快的 83%。</li>
<li><b>吞吐</b> = Σ <i>x<sub>i</sub></i>，全环每拍搬走的 WriteData flit。
效率看这一个。公平性可以靠“把所有人一起压慢”买到，
所以任何公平性改善都必须和吞吐一起报。</li>
</ul>
<div class="def">验收线沿用两条：<b>max/min ≤ 1.05</b>（最慢的 core
不低于最快的 95%）且<b>吞吐相对基线不下降超过 1%</b>。
两条都按最坏随机种子判定，不看单一种子。</div>

<h3>2.1 为什么不用 Jain 指数</h3>
<p>Jain 指数 J = (Σ<i>x<sub>i</sub></i>)<sup>2</sup> /
(<i>n</i>·Σ<i>x<sub>i</sub></i><sup>2</sup>) 是这类研究的常用指标，
本研究<b>实测它区分不出方案</b>，因此不再列入表格。</p>
<div class="def bad">同一批数据上，四个方案的 Jain 只差
<b>{j_spread:.1f}%</b>（{min(_js):.5f} ~ {max(_js):.5f}），
而 max/min 差 <b>{m_spread:.0f}%</b>（{min(_ms):.3f} ~ {max(_ms):.3f}）。
Jain 把所有方案都压在 0.99 以上，读不出差别。</div>
<p>原因是 Jain 是<b>二次</b>指标，由多数节点主导，
少数被饿死的节点对它影响有限：10 个 core 里 9 个完全均等、
剩下 1 个只有其余的 1/10，Jain 仍有 <b>{jain_demo:.4f}</b>，
而 max/min 已经是 <b>10</b>。变异系数 CoV 与它是同一个信息的两种写法
（J = 1/(1+CoV<sup>2</sup>)），所以一并去掉。</p>
<p class="note">Jain 还有一条性质与第 5 节直接相关：
所有 <i>x<sub>i</sub></i> 同乘一个常数 J 不变。也就是说
<b>整体限速不改变 Jain</b>——S1 之所以“看起来没把公平性搞坏”，
一部分就是这个数学假象，换成 max/min 就暴露了。</p>

<h2>3. 下界与失衡现象</h2>
{_bounds_table(pat['bounds'])}
<p class="note">makespan 下界 {pat['bounds']['bound']} 拍，由 <b>{bind_lb}</b> 决定，
即<b>{bind_txt}</b>。</p>

<h3>3.1 基线 S0 下各核是否不均</h3>
{_summary_table(pat)}
<p>答案取决于<b>此刻谁是瓶颈</b>，所以要把两个 S0 并排看：
同一条环、同一份 workload，只改 completer 的请求 tracker。</p>
{_track_table(pat)}
<div class="def bad"><b>环受限时，失衡是真实且显著的。</b>
把 tracker 放开（无限接收资源，环是唯一约束），
需求完全对称而结果并不对称：max/min = <b>{sref['max_min']}</b>，
最慢的 core 只有最快的 {1 / sref['max_min'] * 100:.0f}%，
最慢 {sref['bw_min']} vs 最快 {sref['bw_max']} flit/cycle。
这就是第 4 节要归因的现象。</div>
<div class="def"><b>而在本报告的基线（tracker = {meta.get('ha_track')}）上，
这个失衡被大幅压平了：max/min 只有 {s0['max_min']}</b>，
已经落在 1.05 的验收线附近。<b>但这不是被修好了，是瓶颈换了地方。</b>
每笔事务平均被 RetryAck <b>{q0.get('retry_per_txn')}</b> 次，
瓶颈从“环上的槽位”移到了“completer 的 tracker 表项”，
而后者对所有 core 一视同仁——谁的位置好也不能多要一个表项。
代价是吞吐从 {sref['throughput']} 掉到 <b>{s0['throughput']}</b>
（<b>{t_ref:+.1f}%</b>）。</div>
<div class="key"><b>所以本研究有两个不同的问题，不要混为一谈：</b>
<ol>
<li><b>位置相关的不均</b>（第 4~7 节）：环受限时出现，
S15 / S16 是针对它的解法。在有限 tracker 下它被 retry 背压掩盖，
但只要 tracker 放宽、或 outstanding 调小到不触发重试，它就会回来。</li>
<li><b>重试造成的浪费</b>（第 9~10 节）：有限 tracker 下才出现，
吃掉了 {abs(t_ref):.0f}% 的吞吐，与公平性无关，
要靠动态流控压住 outstanding 才能解决。</li>
</ol></div>
<img src="{imgs['bars']}" alt="per-core BW">
<p class="note">带斜纹的是放开 tracker 的参照，它的高低差一眼可见；
基线（tracker = {meta.get('ha_track')}）与两个公平性方案在这张图上
几乎是一堵平墙——<b>这堵平墙就是上面说的"被压平"</b>。
注意纵轴已截断，否则 4% 的差异在 0 起点上完全看不出来。</p>
<img src="{imgs['panels']}" alt="per-core BW over time">
<p class="note">时间轴上，各 core 的注入率在有限 tracker 下彼此贴得很近，
且全程都低于放开 tracker 时的水平——所有人一起被 retry 背压按住。</p>
<img src="{imgs['overlay']}" alt="slowest vs fastest">

<h2>4. 根因</h2>
<p class="note">本节的归因全部在<b>无限 tracker</b>（环受限）的参照上做，
因为只有那里环是唯一约束、位置效应没有被 retry 背压压平；
4.4 再回到有限 tracker 的基线，说明这条因果链被什么盖住了。</p>
{_rc_table(pat)}
<img src="{imgs['scatter']}" alt="bw vs explanations">

<h3>4.1 先排除“离 mem 更远”</h3>
<div class="def">9 和 19 在环上正好对顶，把这一对从 memory 里拿掉之后，
每个 core 到 8 个 mem 的<b>平均跳数全部等于 {mean_hop} 跳</b>，
连距离的多重集分布都只是重排。实测
r(带宽, 平均跳数) = <b>{rcref['corr_bw_meanhop']}</b>，精确为零。
<b>失衡的来源不是距离。</b></div>

<h3>4.2 真正的判据：紧邻的 mem 有几个</h3>
<p>写到隔壁 mem 的 flit 只占用一段链路就下环了；写到远处的 flit
要一路占着沿途每个节点的出向槽位，在“在环优先”下既更多地挡住别人，
也更多地被别人挡住。去掉 9、19 之后：</p>
<ul>
<li>{hi_s} 两侧都是 mem → <b>相邻 mem = 2</b>，
2/{len(meta['mem_nodes'])} = {2 / len(meta['mem_nodes']) * 100:.1f}%
的写只走一跳；</li>
<li>{lo_s} 有一侧正对非终端节点 → <b>相邻 mem = 1</b>，
只有 {1 / len(meta['mem_nodes']) * 100:.1f}%。</li>
</ul>
<div class="def bad">带宽与<b>相邻 mem 个数</b>的相关系数
<b>r = {rcref['corr_bw_adjmem']}</b>（Spearman
{rcref['rank_bw_adjmem']}），两档之间<b>完全不重叠</b>：
相邻 2 个的最低带宽 {hi_bw} ＞ 相邻 1 个的最高带宽 {lo_bw}。
<b>这就是位置依赖的确切形式。</b></div>

<h3>4.3 落到硬件上：上环成功率</h3>
<p>带宽与实测上环成功率的相关是
<b>r = {rcref['corr_bw_succ']}</b>（Spearman {rcref['rank_bw_succ']}），
与 <code>hop_busy</code> 失败次数强负相关；
与解析过路流量的相关很弱（r = {rcref['corr_bw_pt_eff']}）——
过路流量的<b>总量</b>差别不大，差别在于它<b>什么时候</b>正好卡住本地注入。
I-tag 类失败占比很小：<code>_itag_blocks</code> 只压制<b>竞争的其他注入者</b>，
对在环 flit 无效，所以它能限制饥饿时长，却造不出槽位。</p>

<h3>4.4 有限 tracker 为什么把这条因果链盖住</h3>
<p>上面三小节说的是：<b>能不能上环</b>决定了一个 core 的带宽，
而能不能上环取决于它身边的过路流量，也就是位置。
这条链有一个前提——<b>上了环就一定被接收</b>。
把 completer 的请求 tracker 收到 {meta.get('ha_track')} 个表项之后，
这个前提不再成立。</p>
<ul>
<li>占优的 core 仍然更容易抢到环上的槽位，它的 REQ 仍然更快到达 completer；</li>
<li>但 tracker 满了以后，<b>先到的那个 REQ 一样被 RetryAck 弹回来</b>，
位置优势换不到一个表项；</li>
<li>被弹回的事务在整个 PCrdGrant 往返期间<b>占着 outstanding 槽位却零进展</b>，
于是占优的 core 也推进不下去，被迫慢下来等信用。</li>
</ul>
<div class="def">结果就是 3.1 里那张表：max/min 从 {sref['max_min']}
压到 {s0['max_min']}，代价是全环吞吐 {t_ref:+.1f}%。
<b>retry 背压是一个“把所有人一起拖慢”的均衡器</b>——它确实让各 core
更接近，但用的是第 2 节点明的那种最廉价的公平：降低所有人的速度。
这也解释了为什么本报告要把公平性和吞吐一起报，
只看公平性指标会把这种退化误读成改进。</div>
<p class="note">这条因果链在有限 tracker 下并没有消失，只是被压低：
本节开头那张 per-core 明细表就是基线（tracker = {meta.get('ha_track')}）
的数据，其中带宽与相邻 mem 个数的相关仍然为正，
r = {rc['corr_bw_adjmem']}，而无限 tracker 下是
r = {rcref['corr_bw_adjmem']}。</p>

<h2>5. S1：按规格实现的拥塞等级 AIMD</h2>
<ul>
<li><b>拥塞检测</b>：每节点每窗口分别统计上环失败（up）与 eject 偏转（down）的
<code>total_fail</code> 与 <code>net_fail</code>（只计纯粹由在环占用造成的失败），
等级 <code>= min(7, count // 8)</code>。</li>
<li><b>拥塞传递</b>：<code>CongestionBus</code> 专用广播总线，不占环上 hop，
延迟 {pat['schemes']['S1']['fc']['bus_lat']} 拍。</li>
<li><b>拥塞反馈</b>：每节点维护自己的<b>通路节点</b>表，对该集合取 <b>max</b>。</li>
<li><b>流量控制</b>：最终等级 <code>= level_of(own_total_fail −
max_received_net_fail)</code>；罚则 <code>budget ← max(min, ⌊budget·α⌋)</code>，
α = 0.75 / 0.5 / 0.25；奖励 <code>budget ← min(window, budget + β)</code>，
β = 16 / 8 / 2。</li>
</ul>
{_sweep_table(pat)}
<p class="note">window × α/β 档位扫描。</p>

<h3>5.1 结果：既没拉平，又欠吞吐</h3>
<div class="def bad">S1 把 max/min 从 {s0['max_min']}
<b>升到 {s1['max_min']}</b>（更不均），吞吐 <b>{t1:+.1f}%</b>，
makespan 从 {pat['schemes']['S0']['makespan']} 拉长到
{pat['schemes']['S1']['makespan']} 拍。
<b>两个指标同时变坏</b>，上面整张扫描表里没有一个参数点能同时改善两者。</div>
<img src="{imgs.get('s1trace', '')}" alt="S1 control trace">

<h3>5.2 为什么效果不好</h3>
<p>三条机理，都被数据证实，而且都不是调参能修的——
它们来自 S1 的<b>聚合方式</b>与<b>执行端</b>，不是来自 α/β 的取值。</p>
<p><b>(a) max 聚合保住了贫富比例。</b>共享同一条通路的 core 收到同一个
拥塞等级，于是乘以同一个 α。<b>等比缩小不改变贫富比值</b>：
所有人的预算一起乘 0.75，最快与最慢的<b>比</b>一分不变，
只有总量下降。这就是第 2 节那条“同乘常数 Jain 不变”的性质在起作用——
也正因为如此，用 Jain 看 S1 会觉得“公平性没坏”，
换成 max/min 才看得到它其实更差了。</p>
<p><b>(b) 差值规则把信号搞反了。</b>被饿死的 core
<code>own_total_fail</code> 很高；而占优的 core 正在<b>赢</b>，
它的 <code>net_fail</code> 很低，于是受害者收到的
<code>max_received_net_fail</code> 很小、差值很大 →
<b>受害者惩罚自己</b>；赢家差值 ≈ 0，继续 <code>+β</code>。
这是放大不公平的正反馈。</p>
<p><b>(c) 源端速率造不出槽位。</b>在环优先是绝对的，被限速的 core
让出的空拍立刻被过路 flit 吃掉，被饿死的 core 一无所获。
所以 S1 只是把总量压下去。</p>

<h2>6. S15：最大最小公平份额 + 槽预约</h2>
<p>保留专用总线和窗口结构，换掉<b>聚合什么</b>，并加一个仲裁钩子。</p>
<ul>
<li><b>检测</b>：额外记录成功上环数、累计成功数与 active 标志。</li>
<li><b>传递</b>：同一条总线多播 <code>(等级, 本窗口成功数, 累计成功数,
active, 各出向公平份额)</code>。</li>
<li><b>反馈</b>：用<b>最大最小公平份额</b>替代 max-of-levels。每个共享资源
（有向 hop、以及目的 mem 的 leave 端口）按<b>观测到的吞吐峰值</b>作为容量，
除以其上的活跃竞争者，广播一个份额；节点取自己路径上所有资源份额的最小值。
容量取实测峰值而非理论值，回路因此自校准，不会把吞吐一路压下去。</li>
<li><b>控制</b>：AIMD 跟踪这个目标，并按<b>累计欠账</b>而非瞬时速率修正，
避免开局阶段的抢占决定全局。</li>
<li><b>槽预约（真正的修复）</b>：落后于全环平均累计进度超过
<code>reserve_gap</code> 的节点，通过总线预约未来若干拍的
<code>(plane, dir, VC)</code> 槽；<b>上游节点不得注入会在预约窗口内到达该槽的
flit</b>。资格用<b>全环累计量</b>判定而不是各自的本地目标——按本地判定时几乎
每个节点都认为自己落后，预约互相抵消，全环白白付出上万次让路。</li>
<li><b>只在真的不公平时介入</b>：总线上的累计进度离散度低于
<code>fair_tol</code> 时控制器完全不接管，公平的场景下零代价。</li>
</ul>

<div class="def">预约只压制<b>注入</b>，从不停住已经在环上的 flit，
所以不需要任何缓冲：<code>n_inring_blocked</code> 与
<code>max_inring_hold</code> 全程为 0，无缓存前提没有被偷偷放弃。</div>

<h3>6.1 结果</h3>
<div class="def {'good' if s15['max_min'] < s0['max_min'] else 'bad'}">
max/min <b>{s0['max_min']} → {s15['max_min']}</b>，
每 core 带宽收敛到 <b>{s15['bw_min']} ~ {s15['bw_max']}</b>，
吞吐 <b>{t15:+.1f}%</b>。
{'在这个种子上 S15 的 max/min 反而比基线略差' if
 s15['max_min'] >= s0['max_min'] else '公平性有改善'}——
基线本身已经被 retry 背压压到 {s0['max_min']}，
留给槽预约的空间几乎没有了。</div>
<img src="{imgs['bars']}" alt="per-core BW">
<img src="{imgs['hopbw']}" alt="hop bandwidth vs cap">
<p class="note">吞吐这点损失来自预约压制上游注入时留下的空拍。
在环受限的参照上这笔钱是花得值的（那里 max/min 是 {sref['max_min']}，
最慢 core 能被实实在在抬上来）；
在有限 tracker 的基线上最慢 core 只从 {s0['bw_min']} 变成
{s15['bw_min']}（{s15['bw_min'] / s0['bw_min']:.2f} 倍），
<b>钱花了，货没买到多少</b>。</p>

<h3>6.2 换种子还成立吗</h3>
<p>预约是离散机制，单一种子容易把某个参数点衬托得过好，
所以把 S0 与 S15 在多个随机种子上重跑。</p>
{_seed_table(pat)}
<div class="def bad">
S15 的 max/min 落在 {rng_m}，而同样这几个种子上 S0 是
{min(r['S0']['max_min'] for r in pat['seed_sweep']):.3f} ~
{max(r['S0']['max_min'] for r in pat['seed_sweep']):.3f}，
两个区间<b>互相重叠</b>——在有限 tracker 的基线上
<b>S15 的公平性改善已经不稳定了</b>，有的种子上好、有的种子上反而差，
而吞吐代价 {rng_t} 是每个种子都要付的。
<b>按最坏种子对照验收线：{verdict}。</b></div>
<p>结论要分两句说清楚，因为它们指向不同的事：</p>
<ul>
<li><b>机制本身是有效的。</b>在环受限的参照上（max/min
{sref['max_min']}）槽预约确实能把最慢 core 抬起来，
第 4 节归因的那个位置效应它是对症的。</li>
<li><b>但在这个基线上它不划算。</b>retry 背压已经把 max/min 压到
{s0['max_min']}，剩下的余量比 S15 自己的抖动还小，
于是那 {abs(v15['t_worst']):.1f}% 的吞吐买不回等价的公平性。</li>
</ul>
<p class="note">吞吐代价的来源没有变：在严格无缓存、在环绝对优先的环上，
唯一能把槽位让给弱者的手段就是让强者的上游空一拍，
这一拍在强者本来能用满的时候就是净损失。</p>

<h2>7. S16：接收端驱动的授权（Homa 式），代价压到最低</h2>
<p>S15 的问题不在于不公平，而在于<b>为公平付的钱太贵</b>：
一条专用广播总线、每 (node, VC) 的 AIMD 状态机、
再加上环上的槽预约逻辑，换来 {abs(t15):.1f}% 的吞吐下降，
而且换到的公平性还不稳定。下面这条路几乎不花硬件。</p>

<div class="def"><b>关键观察：CHI 里已经有 Homa 的 GRANT 了。</b>
Homa 的核心是<b>接收端驱动</b>——发送端在收到接收端的 GRANT 之前不得发送
被调度的数据，接收端同时授权给若干发送端（overcommitment），
使自己的入口链路不会因为某个发送端反应慢而空转。
而 <code>WriteNoSnp</code> 明文规定：<b>拿到 <code>DBIDResp</code>
之前不许发 WriteData</b>。也就是说，
<b>completer 本来就掌握着"哪个 core、什么时候可以把写数据放上环"的授权权</b>，
它就是 Homa 的 GRANT。基线把这个权力浪费了——REQ 一到就立刻授权。
S16 不改任何报文格式，只改<b>发放时机与发放顺序</b>。</div>

<h3>7.1 机制</h3>
<ul>
<li><b>排队而非即授</b>：REQ 到达 completer 后进入按源 core 分开的授权队列。</li>
<li><b>overcommitment</b>：一个 completer 最多同时持有
<code>overcommit</code> 个未完成授权。这是 Homa 的过量授权度，
也对应 Homa 的 RTTbytes：太小则 completer 自己空转，
太大则退化成无管控的基线。<b>这是唯一的旋钮。</b></li>
<li><b>调度顺序</b>：在排队的请求方中，选<b>累计被服务最少</b>的那个。
写请求都是 {meta['W']} 个 flit 的等长报文，
Homa 的 SRPT 在等长下退化为公平排队，所以直接均衡累计授权量。</li>
<li><b>eager 授权</b>：completer 未饱和时立即授权，
低负载下不引入任何额外延迟。这是 Homa 的 unscheduled bytes 的对应物
（CHI 无法真正表达"未授权就发"，所以只能用这种方式近似）。</li>
</ul>

<div class="def good"><b>为什么这能拉平带宽。</b>
每个 core 均匀写全部 {len(meta['mem_nodes'])} 个 mem，
若每个 mem 都在自己的请求方之间均分授权，
那么 core <i>i</i> 得到的授权率 =
Σ<sub>mem</sub>（该 mem 的授权率 / {len(meta['core_nodes'])}），
<b>对所有 core 相同</b>。占优的 core（邻接 2 个 mem）虽然能更快地把
授权用掉，但调度器只在它重新变成"被服务最少"时才再给它授权，
<b>所以它跑不到前面去</b>。位置优势被授权配额直接抵消，
不需要知道任何拓扑信息。</div>

<h3>7.2 overcommit 扫描：唯一的旋钮，且必须低于 tracker</h3>
{_oc_table(pat)}
<div class="def">读法有三层：</div>
<ul>
<li><b>吞吐随 overcommit 单调上升然后走平</b>——太小的话 completer
手上没有足够多的活跃请求方，自己的 leave 端口就会空转。</li>
<li><b>公平性在能起作用的区间里几乎不动</b>：
授权配额决定了带宽，与 overcommit 的具体取值无关。</li>
<li><b>关键：<code>overcommit ≥ {meta.get('ha_track')}</code>
（= 请求 tracker 的表项数）之后，整行数字与 S0 逐位相同。</b>
原因是 S16 唯一的动作是<b>扣住</b>授权，
而它只能从 tracker 之下扣——一个 REQ 既然被 tracker 收下了，
在配额高于 tracker 时就一定是可授权的，S16 于是退化成"一到就授权"，
也就是 S0 换了个名字。最后一行 <code>overcommit = ∞</code>
就是基线策略，max/min 回到 {oc_rows.get(None, {}).get('max_min')}。</li>
</ul>
<div class="def">所以本报告把 S16 的 overcommit 定在
<b>{fc16.get('overcommit')}</b>（低于 tracker 的
{meta.get('ha_track')}），这是它能真正扣住授权的前提。
这一点由回归 <code>test_s16_grants_below_the_tracker</code> 钉住，
避免以后有人把它调到 tracker 之上、得到一份"S16 == S0"的假结果。</div>

<h3>7.3 结果</h3>
<div class="def {'good' if not v16['bad'] else ''}">
max/min <b>{s0['max_min']} → {s16['max_min']}</b>
（{len(meta['core_nodes'])} 个 core 收敛到
{s16['bw_min']} ~ {s16['bw_max']} flit/cycle），
吞吐 <b>{t16:+.1f}%</b>，事务延迟 p99 <b>{lat0} → {lat16}</b>。
跨 {v16['n']} 个种子：max/min {v16['rng_m']}、吞吐差 {v16['rng_t']}。
<b>按最坏种子判定：{v16['verdict']}。</b></div>

<p><b>吞吐这一点损失是从哪来的？</b>在有限 tracker 的基线上，
S16 不再像无限 tracker 时那样"更公平又更快"。
原因是<b>它要压制的那个不公平已经被 retry 背压压掉了大半</b>
（4.4 节），剩下的位置优势只值 {s0['max_min']} → {s16['max_min']}
这一小段，而把 overcommit 压到 {fc16.get('overcommit')}
（tracker 的一半）就会让 completer 在切换请求方的间隙偶尔空转，
这部分是净损失。
<b>换句话说，tracker 已经替 S16 做了一部分工作，
S16 能再赚到的公平性变少了，但它要付的空转代价没变。</b></p>
<p class="note">这也是为什么 S16 在无限 tracker 的参照上更好看：
那里 max/min 是 {sref['max_min']}，有大量不公平可供消除，
拉平尾部换来的 makespan 收益盖得住空转损失。</p>

<h3>7.4 拆开看哪一部分在起作用</h3>
{_ablate_table(pat)}
<p class="note">把"累计被服务最少优先"换成朴素轮询，公平性会退一档
——说明<b>跟踪累计服务量</b>是必要的，仅靠顺序轮转不够，
因为不同 core 把授权兑现成上环的速度本来就不一样。
关掉 eager 授权在满载下没有区别（满载时本来就没有"未饱和"的时刻），
它只影响轻载延迟，所以是免费的。</p>

<h3>7.5 代价对比</h3>
{_cost_table(pat, s0)}
<div class="def">S16 仍然不需要总线、不需要新报文、不需要槽预约，
但它在写缓冲上的优势<b>比无限 tracker 时小得多</b>，
因为有限 tracker 已经替它做了一半：
一个未完成的 DBID 就是 completer 上一块已经承诺出去的写数据缓冲，
而 tracker 只有 {meta.get('ha_track')} 个表项，
所以基线的峰值授权已经被硬性夹在 <b>{base_peak}</b> 个
（≈{base_peak * 4} flit）。S16 把它进一步钉在
<b>{fc16.get('overcommit')}</b> 个（≈{fc16.get('peak_buf_flits')} flit），
<b>缓冲需求是基线的 1/{buf_ratio:.1f}</b>。</div>
<div class="def">这里有一个值得单独记下的结论：
<b>"给 completer 的写缓冲加一个上限"这件事，
有限 tracker 本身就做到了，而且是免费的</b>——
它是协议已经要求的资源，不是新加的机制。
无限 tracker 的参照里这个峰值是失控的：
<b>同一个 S0 在放开 tracker 后实测峰值要 {qref.get('max_ha_used')}
个表项</b>，那才是 S16 当初能把缓冲砍到 1/5 以上的来源。
换句话说，S16 的"负代价"论点在承认 completer 有限之后，
大部分归功于 tracker，剩下给 S16 的是再砍一半。</div>

<h3>7.6 哪些 Homa 的东西用不上</h3>
<ul>
<li><b>SRPT 优先级</b>：Homa 靠"短报文优先"压低小报文延迟。
这里所有写都是 {meta['W']} 个 flit 的等长报文，SRPT 退化为公平排队。
如果将来引入不等长写（例如部分写 / 原子操作），
这一条会重新变得有意义。</li>
<li><b>网络内多级优先级</b>：Homa 依赖交换机的多个优先级队列。
本环是<b>严格无缓存</b>的，环上没有队列可供排序，
在环流量绝对优先是硬性规则——这条搬不过来。</li>
<li><b>未授权先发（unscheduled bytes）</b>：Homa 允许先发 RTTbytes
再等 GRANT。CHI 不允许在 DBIDResp 之前发 WriteData，
所以只能用 7.1 里的 eager 授权近似，代价是低负载下多一个环回延迟。</li>
</ul>

<h2>8. 总线的代价</h2>
{_fc_table(pat)}
<p class="note">专用广播总线不占用任何环上 hop，按窗口边界发一次。
S1 每次 6 bit（两个 3 bit 等级）；S15 增加 8 bit 本窗口成功数、
16 bit 累计数、1 bit active，以及 6 个 8 bit 出向公平份额。
窗口 {pat['schemes']['S15']['fc']['window']} 拍发一次，
折算到每拍的线上开销可以忽略；面积在
<code>rg_sched_cost.py</code> 中单列。
<b>S16 不在这张表里，因为它没有总线</b>——它的控制信号就是协议本来要发的 <code>DBIDResp</code>。</p>
{sec9}

<p class="note" style="margin-top:2rem">
数据：<code>results/ring2_write_fair.json</code>（K={meta['K']}、
W={meta['W']}、seed={meta['seed']}，生成于 {meta['generated_at']}）。
回归：<code>utils/verify_ring2_20.py</code>。</p>
</body></html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
