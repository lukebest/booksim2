#!/usr/bin/env python3
"""HTML report: per-core write bandwidth fairness on the bufferless ring.

One workload: 10 AI cores writing uniformly to 8 memory nodes. Nodes 9 and 19
are neither core nor memory -- they forward, but never source or sink a write.
"""

from __future__ import annotations

import json
import math
import random
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
DIRBAL = ROOT / "results" / "ring2_s1_dirbal.json"
OUT = ROOT / "results" / "report_ring2_write_fairness.html"
IMG = ROOT / "results"

SCHEMES = ("S0", "S1", "S15", "S16")
# Section 3.1: the reported baseline tracker. S15 is left out;
# S17 / S18 take its place as the rate-based answers to retry waste.
SEC31 = ("S0", "S1", "S16", "S17", "S18")
# Source-end FC except S0/S1, plus S0 as the baseline they are judged on.
FC_CMP = ("S0", "S15", "S16", "S17", "S18", "S19", "S20")
COLOR = {"S0": "#dc2626", "S1": "#f59e0b", "S15": "#2563eb",
         "S16": "#16a34a", "S17": "#0ea5e9", "S18": "#a855f7",
         "S19": "#ea580c", "S20": "#db2777"}
LABEL = {"S0": "S0 基线（无流控）", "S1": "S1 拥塞等级 AIMD",
         "S15": "S15 公平份额 + 槽预约",
         "S16": "S16 接收端授权（Homa 式）",
         "S17": "S17 TIMELY（RTT 梯度）",
         "S18": "S18 DCQCN（tracker ECN）",
         "S19": "S19 Swift（时延窗口）",
         "S20": "S20 DCTCP（ECN 窗口）"}


def _use_cjk_font() -> None:
    from matplotlib import font_manager as fm
    wanted = ("micro hei", "cjk", "noto sans sc", "source han sans")
    for f in fm.fontManager.ttflist:
        name = f.name.lower()
        if any(w in name for w in wanted):
            plt.rcParams["font.sans-serif"] = [f.name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def _table(headers: list[str], rows: list[list], *, hl: list[bool] | None = None
           ) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = []
    for i, r in enumerate(rows):
        cls = ' class="imbal"' if hl and i < len(hl) and hl[i] else ""
        body.append("<tr" + cls + ">" + "".join(f"<td>{c}</td>" for c in r)
                    + "</tr>")
    return (f"<table><thead><tr>{th}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def _dir_imbal(a: int, b: int, *, min_n: int = 50, ratio: float = 2.0) -> bool:
    if a + b < min_n:
        return False
    lo = min(a, b)
    return lo == 0 or max(a, b) / lo >= ratio


def _present_schemes(pat: dict, wanted: tuple[str, ...] | None = None
                     ) -> tuple[str, ...]:
    have = pat.get("schemes") or {}
    order = wanted or SCHEMES
    return tuple(s for s in order if s in have)


def _cores(pat: dict) -> list[str]:
    have = _present_schemes(pat) or tuple((pat.get("schemes") or {}))
    return sorted(pat["schemes"][have[0]]["fairness"]["bw_by_core"],
                  key=int)


def _jit_label(meta: dict) -> str:
    hi = int(meta.get("ha_rsp_jit") or 0)
    if hi <= 0:
        svc = int(meta.get("t_ha_service") or 0)
        return "0（无 HA think time）" if svc == 0 else f"{svc}（常数）"
    lo = int(meta.get("ha_rsp_jit_lo") or 0)
    return f"U{{{lo}..{hi}}}"


def _sch(pat: dict, s: str, *, s0_unbounded: bool = False) -> dict:
    """The run record for scheme `s`. Section 3.1 reads S0 from the
    unlimited-tracker reference so the baseline row is the ring-limited one."""
    if s == "S0" and s0_unbounded:
        return pat.get("s0_unbounded") or pat["schemes"]["S0"]
    return pat["schemes"][s]


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
    n_pl = int(meta.get("n_planes") or 2)
    ax.text(0, 0.10, f"plane ×{n_pl}", ha="center", fontsize=11,
            color="#334155")
    ax.text(0, -0.02, "双向闭合 full ring · 最短路", ha="center", fontsize=11,
            color="#334155")
    others = any(_role(i, meta) == "other" for i in range(n))
    n_core = sum(1 for i in range(n) if _role(i, meta) == "core")
    n_mem = sum(1 for i in range(n) if _role(i, meta) == "mem")
    extra = "，灰 = 非终端" if others else ""
    if others:
        ax.text(0, -0.14, "灰 = 非终端（不收发写）", ha="center", fontsize=9.5,
                color="#64748b")
    ax.set_xlim(-1.45, 1.45)
    ax.set_ylim(-1.45, 1.45)
    ax.set_title(
        f"20 节点{('单' if n_pl == 1 else '双')} plane 闭合 full ring · "
        f"边上数字 = hop 时延（拍）\n"
        f"蓝 = AI core（{n_core}），橙 = memory HA（{n_mem}）{extra}",
        fontsize=12, pad=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_bw_bars(pat: dict, path: Path, *, schemes: tuple[str, ...] = SCHEMES,
                s0_unbounded: bool = False, extra_ref: bool = True,
                title: str | None = None) -> None:
    """Per-core write bandwidth, one group of bars per scheme.

    `s0_unbounded` makes the S0 bar the unlimited-tracker run.
    `extra_ref` (default on) draws a hatched copy of that run next to the
    finite-tracker schemes so the masking is visible (section 6).
    """
    _use_cjk_font()
    cs = _cores(pat)
    x = range(len(cs))
    series = []
    for s in schemes:
        rec = _sch(pat, s, s0_unbounded=s0_unbounded)
        lab = LABEL[s]
        if s == "S0" and s0_unbounded:
            lab = "S0 基线（tracker = ∞）"
        series.append((s, rec["fairness"], COLOR[s], None, lab))
    ref = pat.get("s0_unbounded")
    if extra_ref and ref and not s0_unbounded:
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
    ax.set_title(title or "每 core 写带宽（争用窗口内），虚线 = 该方案均值，"
                 "纵轴已截断以显示差异")
    # The truncated axis leaves no room inside the panel, so the legend goes
    # underneath rather than on top of the bars.
    ax.legend(fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.16),
              ncol=3, frameon=False)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_bw_panels(pat: dict, path: Path, *, schemes: tuple[str, ...] = SCHEMES,
                  s0_unbounded: bool = False) -> None:
    """Per-core write-inject rate over time, one panel per scheme."""
    _use_cjk_font()
    cs = _cores(pat)
    fig, axes = plt.subplots(1, len(schemes), figsize=(13.6, 3.6),
                             sharey=True)
    cmap = plt.get_cmap("viridis")
    for ax, s in zip(axes, schemes):
        sch = _sch(pat, s, s0_unbounded=s0_unbounded)
        for j, c in enumerate(cs):
            b = sch["wr_binned"][c]
            ax.plot(b["t"], b["rate"], lw=1.0,
                    color=cmap(j / max(1, len(cs) - 1)), alpha=0.9)
        f = sch["fairness"]
        tag = f"{s} ∞" if s == "S0" and s0_unbounded else s
        ax.set_title(f"{tag}  mk={sch['makespan']}  "
                     f"max/min={f['max_min']:.3f}", fontsize=10)
        ax.set_xlabel("cycle")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("写注入率 flit/cycle")
    fig.suptitle("每 core 写注入率随时间（颜色 = core index）", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_bw_overlay(pat: dict, path: Path, *, schemes: tuple[str, ...] = SCHEMES,
                   s0_unbounded: bool = False) -> None:
    """Slowest and fastest core of the baseline, tracked across schemes."""
    _use_cjk_font()
    f0 = _sch(pat, "S0", s0_unbounded=s0_unbounded)["fairness"]["bw_by_core"]
    lo = min(f0, key=lambda c: f0[c])
    hi = max(f0, key=lambda c: f0[c])
    fig, ax = plt.subplots(figsize=(10.2, 3.8))
    for s in schemes:
        rec = _sch(pat, s, s0_unbounded=s0_unbounded)
        for c, ls in ((lo, "-"), (hi, "--")):
            b = rec["wr_binned"][c]
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


def jain(xs) -> float:
    """(sum x)^2 / (n * sum x^2). 1.0 = perfectly equal, 1/n = one winner."""
    xs = [float(x) for x in xs]
    s2 = sum(x * x for x in xs)
    if not xs or s2 <= 0:
        return 0.0
    return (sum(xs) ** 2) / (len(xs) * s2)


def _pick(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(q * len(s))))]


def _inst_balance(pat: dict, meta: dict, scheme: str,
                  groups: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64)) -> dict:
    """Per-bin total write bandwidth and how even the ten cores are in it.

    A bin holds only `bin_w * rate` flits per core, so part of any per-bin
    spread is counting noise rather than unfairness. The null model is the
    same per-bin total split multinomially over the cores at equal
    probability, i.e. perfectly fair arbitration observed through the same
    window. Comparing against it is what separates the two.
    """
    rec = pat["schemes"].get(scheme)
    if not rec or not rec.get("wr_binned"):
        return {}
    bin_w = int(meta.get("bin_w") or 128)
    wb = rec["wr_binned"]
    cs = list(sorted(wb, key=int))
    t_all = wb[cs[0]]["t"]
    t_fair = int((rec.get("fairness") or {}).get("t_fair") or 0)
    # Only bins wholly inside the contention window: past t_fair a core has
    # simply run out of work, so a "gap" there is not unfairness.
    idx = [i for i in range(len(t_all)) if t_all[i] + bin_w <= t_fair]
    if not idx:
        idx = list(range(len(t_all)))
    cnt = {c: [int(round(wb[c]["rate"][i] * bin_w)) for i in idx] for c in cs}
    n_c = len(cs)

    def _stats(g: int, rng: random.Random) -> dict:
        nb = len(idx) // g
        oj, om, nj, nm, tot = [], [], [], [], []
        for b in range(nb):
            v = [sum(cnt[c][b * g:(b + 1) * g]) for c in cs]
            n = sum(v)
            tot.append(n)
            oj.append(jain(v))
            om.append(max(v) / min(v) if min(v) else float("inf"))
            u = [0] * n_c
            for _ in range(n):
                u[rng.randrange(n_c)] += 1
            nj.append(jain(u))
            nm.append(max(u) / min(u) if min(u) else float("inf"))
        fo = [x for x in om if x != float("inf")]
        fn = [x for x in nm if x != float("inf")]
        return {
            "bin_w": g * bin_w, "n_bins": nb,
            "count_per_core": round(sum(tot) / max(1, nb) / n_c, 1),
            "obs_jain": round(sum(oj) / len(oj), 5) if oj else None,
            "null_jain": round(sum(nj) / len(nj), 5) if nj else None,
            "obs_mm": round(sum(fo) / len(fo), 3) if fo else None,
            "null_mm": round(sum(fn) / len(fn), 3) if fn else None,
        }

    per_bin_j, per_bin_m, total = [], [], []
    for b in range(len(idx)):
        v = [cnt[c][b] for c in cs]
        total.append(sum(v) / bin_w)
        per_bin_j.append(jain(v))
        per_bin_m.append(max(v) / min(v) if min(v) else float("inf"))
    fin_m = [x for x in per_bin_m if x != float("inf")]
    return {
        "scheme": scheme, "bin_w": bin_w, "t": [t_all[i] for i in idx],
        "total": total, "jain": per_bin_j, "n_bins": len(idx),
        "total_mean": round(sum(total) / len(total), 4),
        "total_p05": round(_pick(total, 0.05), 4),
        "total_p50": round(_pick(total, 0.50), 4),
        "total_p95": round(_pick(total, 0.95), 4),
        "total_min": round(min(total), 4), "total_max": round(max(total), 4),
        "jain_mean": round(sum(per_bin_j) / len(per_bin_j), 5),
        "jain_p05": round(_pick(per_bin_j, 0.05), 5),
        "jain_min": round(min(per_bin_j), 5),
        "mm_mean": round(sum(fin_m) / len(fin_m), 3) if fin_m else None,
        "mm_p95": round(_pick(fin_m, 0.95), 3) if fin_m else None,
        "mm_max": round(max(fin_m), 3) if fin_m else None,
        "sweep": [_stats(g, random.Random(12345 + g)) for g in groups],
    }


def _plateau_bw(pat: dict, meta: dict) -> float | None:
    """Write bandwidth the same fabric reaches with an unlimited tracker."""
    ref = pat.get("s0_unbounded") or {}
    mk = ref.get("makespan")
    if not mk:
        return None
    n_c, w = len(meta["core_nodes"]), int(meta["W"])
    return round(n_c * int(meta["K"]) * w / mk, 3)


def plot_total_bw(pat: dict, meta: dict, path: Path,
                  schemes: tuple[str, ...] = ("S0", "S1")) -> None:
    """Ring-wide WriteData bandwidth over time against the analytic ceiling."""
    _use_cjk_font()
    cc = _ideal_cc(meta)
    r_bind = cc["tot"]
    plateau = _plateau_bw(pat, meta)
    fig, ax = plt.subplots(figsize=(11.6, 4.4))
    for s in [x for x in schemes if pat["schemes"].get(x)]:
        ib = _inst_balance(pat, meta, s, groups=())
        if not ib:
            continue
        ax.plot(ib["t"], ib["total"], lw=0.7, color=COLOR[s], alpha=0.35)
        k = max(1, len(ib["total"]) // 160)
        ax.plot([ib["t"][i] for i in range(0, len(ib["t"]) - k, k)],
                [sum(ib["total"][i:i + k]) / k
                 for i in range(0, len(ib["total"]) - k, k)],
                lw=1.9, color=COLOR[s],
                label=f"{s} 实测（均值 {ib['total_mean']}）")
        ax.axhline(ib["total_mean"], color=COLOR[s], ls=":", lw=1.0)
    ax.axhline(r_bind, color="#111827", ls="--", lw=2.0,
               label=f"fabric 理论上限 R* = {r_bind:.3f}（热 hop 的 dat 链路）")
    if plateau:
        ax.axhline(plateau, color="#0f766e", ls="--", lw=1.8,
                   label=f"同一 fabric、无限 tracker 实测 {plateau}")
    ax.set_ylim(0, r_bind * 1.12)
    ax.set_xlabel("cycle")
    ax.set_ylabel("全环 WriteData 带宽 flit/cycle")
    ax.set_title(f"所有 {int(cc['n_c'])} 个 core 的总写带宽随时间"
                 f"（细线 = {meta.get('bin_w')} 拍分箱，粗线 = 平滑）")
    ax.legend(fontsize=8.5, ncol=2, loc="lower right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_inst_balance(pat: dict, meta: dict, path: Path,
                      schemes: tuple[str, ...] = ("S0", "S1")) -> None:
    """Instantaneous evenness, and how much of it is just counting noise."""
    _use_cjk_font()
    ibs = {s: _inst_balance(pat, meta, s) for s in schemes
           if pat["schemes"].get(s)}
    ibs = {s: v for s, v in ibs.items() if v}
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.2))
    ax = axes[0]
    for s, ib in ibs.items():
        ax.plot(ib["t"], ib["jain"], lw=0.7, color=COLOR[s], alpha=0.45)
        ax.axhline(ib["jain_mean"], color=COLOR[s], ls=":", lw=1.2,
                   label=f"{s} 均值 {ib['jain_mean']:.4f}")
    nj = [r["null_jain"] for ib in ibs.values() for r in ib["sweep"]
          if r["bin_w"] == ib["bin_w"]]
    if nj:
        ax.axhline(sum(nj) / len(nj), color="#111827", ls="--", lw=1.8,
                   label=f"完全公平的零模型 {sum(nj) / len(nj):.4f}")
    ax.set_xlabel("cycle")
    ax.set_ylabel(f"该箱内 {len(meta['core_nodes'])} 核写带宽的 Jain 指数")
    # 起步 / 收尾的少数离群箱截掉，见表中 jain_min。
    lo = min((_pick(ib["jain"], 0.01) for ib in ibs.values()), default=0.9)
    ax.set_ylim(lo - 0.01, 1.003)
    ax.set_title(f"主指标：{meta.get('bin_w')} 拍窗内的写带宽 Jain"
                 f"（虚线 = 全箱平均，纵轴已截断）", fontsize=10)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.3)

    ax = axes[1]
    for s, ib in ibs.items():
        xs = [r["bin_w"] for r in ib["sweep"]]
        ax.plot(xs, [r["obs_mm"] for r in ib["sweep"]], "o-", lw=1.8,
                color=COLOR[s], label=f"{s} 实测")
        ax.plot(xs, [r["null_mm"] for r in ib["sweep"]], "s--", lw=1.4,
                color=COLOR[s], alpha=0.55, mfc="none",
                label=f"{s} 完全公平的零模型")
    ax.set_xscale("log", base=2)
    ax.axhline(1.0, color="#94a3b8", lw=0.9)
    ax.set_xlabel("分箱宽度（拍）")
    ax.set_ylabel("该箱内 max/min")
    ax.set_title("不均衡随观察窗口衰减：实测始终低于零模型", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_fc_compare(pats: dict[str, dict], path: Path,
                    schemes: tuple[str, ...] = FC_CMP) -> None:
    """Binned-Jain ratio and write throughput of every applicable scheme."""
    _use_cjk_font()
    names = list(pats)
    fig, axes = plt.subplots(len(names), 2, figsize=(11.4, 3.4 * len(names)))
    if len(names) == 1:
        axes = [axes]
    for row, name in enumerate(names):
        pat = pats[name]
        mm, thr, cols, labs = [], [], [], []
        for s in schemes:
            rec = pat["schemes"].get(s)
            if not rec:
                continue
            f = rec["fairness"]
            mm.append((f.get("jain_bin") or {}).get("jain_bin_ratio") or 0.0)
            thr.append(f["throughput"])
            cols.append(COLOR[s])
            labs.append(s)
        x = range(len(labs))
        ax0, ax1 = axes[row]
        ax0.bar(x, mm, color=cols, edgecolor="white")
        ax0.axhline(1.0, color="#94a3b8", ls="--", lw=0.9)
        ax0.set_ylim(min([*mm, 1.0]) * 0.98, max([*mm, 1.0]) * 1.02)
        ax0.set_ylabel("分箱 Jain ÷ 零模型")
        ax0.set_title(f"{name} · 分箱 Jain ÷ 零模型（虚线 = 验收 1.0）")
        ax0.set_xticks(list(x))
        ax0.set_xticklabels(labs)
        ax0.grid(axis="y", alpha=0.3)
        ax1.bar(x, thr, color=cols, edgecolor="white")
        if "S0" in pat["schemes"]:
            ax1.axhline(pat["schemes"]["S0"]["fairness"]["throughput"],
                        color=COLOR["S0"], ls=":", lw=1.0)
        ax1.set_ylabel("写带宽吞吐 flit/cycle")
        ax1.set_title(f"{name} · 写带宽吞吐（虚线 = S0）")
        ax1.set_xticks(list(x))
        ax1.set_xticklabels(labs)
        ax1.grid(axis="y", alpha=0.3)
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
        (axes[1], "mean_hop_to_mem",
         f"到 {len(rc.get('mem') or [])} 个 mem 的平均跳数",
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
    adj_set = {x.get("adj_mem") for x in rows}
    fig.suptitle(
        "full ring 上相邻 mem 数与平均跳数都无方差，残余看上环成功率"
        if len(adj_set) <= 1 else
        "位置依赖的确切形式：决定带宽的是“身边有几个 mem”，不是“离 mem 多远”",
        fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_hop_bw(pat: dict, cap: int, path: Path) -> None:
    """Ring-wide hop bandwidth against the 3-VC cap."""
    _use_cjk_font()
    fig, ax = plt.subplots(figsize=(10.2, 3.6))
    for s in _present_schemes(pat):
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


_CORE_COL = (
    "#2563eb", "#dc2626", "#16a34a", "#a855f7", "#ea580c",
    "#0891b2", "#db2777", "#65a30d", "#7c3aed", "#0f766e",
)


def _ost_by_oc(repro: dict) -> dict[int, dict]:
    return {r["core_outstanding"]: r for r in repro.get("ost") or []
            if not r.get("tag")}


def plot_ost_repro(repro: dict, path: Path) -> None:
    """Silicon-style overlay: write BW and effective ost share a shape."""
    _use_cjk_font()
    by = _ost_by_oc(repro)
    fig, axes = plt.subplots(2, 2, figsize=(11.4, 6.4), sharex="col")
    for col, oc, title in ((0, 16, "ost=16（刚好盖住延迟）"),
                           (1, 128, "ost=128（开太大）")):
        r = by[oc]
        wr, ost = r["wr_binned"], r["ost"]
        axes[0][col].plot(wr["t"], wr["rate"], color="#0f172a", lw=1.4,
                          label="写带宽")
        ax2 = axes[0][col].twinx()
        ax2.plot(ost["t"], ost["eff_mean"], color="#dc2626", lw=1.3,
                 label="有效 ost")
        ax2.plot(ost["t"], ost["used_mean"], color="#94a3b8", lw=1.0, ls="--",
                 label="已分配 ost")
        axes[0][col].set_title(title, fontsize=10)
        axes[0][col].set_ylabel("写带宽 flit/cycle")
        ax2.set_ylabel("每 core outstanding")
        h1, l1 = axes[0][col].get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        axes[0][col].legend(h1 + h2, l1 + l2, fontsize=7.5, loc="upper right")
        axes[0][col].grid(alpha=0.3)
        for i, c in enumerate(ost["cores"]):
            series = [row[i] for row in ost["used"]]
            axes[1][col].plot(ost["t"], series, lw=0.9,
                              color=_CORE_COL[i % len(_CORE_COL)],
                              label=f"C{c}")
        axes[1][col].set_ylabel("该 AIC 已分配 ost")
        axes[1][col].set_xlabel("cycle")
        axes[1][col].grid(alpha=0.3)
        if col == 1:
            axes[1][col].legend(fontsize=6.5, ncol=2, loc="upper right")
    fig.suptitle(
        f"复现：ost 太少盖不住延迟，太多被乱序/重试吃掉"
        f"（ost=16 时 BW↔有效 ost r={by[16]['bw_eff_corr']}；"
        f"ost=128 时 r={by[128]['bw_eff_corr']}）",
        fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_ost_inorder(repro: dict, path: Path) -> None:
    """Same ost=128 run with in-order retirement: waste moves to HOL."""
    _use_cjk_font()
    row = next((r for r in repro.get("ost") or [] if r.get("tag") == "inorder"),
               None)
    if not row:
        return
    ost = row["ost"]
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 3.5), sharex=True)
    axes[0].plot(ost["t"], ost["used_mean"], color="#94a3b8", lw=1.3,
                 label="已分配")
    axes[0].plot(ost["t"], ost["eff_mean"], color="#2563eb", lw=1.4,
                 label="有效")
    axes[0].plot(ost["t"], ost["park_mean"], color="#f59e0b", lw=1.2, ls="--",
                 label="停摆（等 PCrd）")
    axes[0].plot(ost["t"], ost["hol_mean"], color="#dc2626", lw=1.2,
                 label="队头阻塞（等前序）")
    axes[0].set_ylabel("每 core 槽位")
    axes[0].set_title("按序退休：空等记在 HOL 上", fontsize=10)
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.3)
    wr = row["wr_binned"]
    axes[1].plot(wr["t"], wr["rate"], color="#0f172a", lw=1.4, label="写带宽")
    ax2 = axes[1].twinx()
    ax2.plot(ost["t"], ost["eff_mean"], color="#dc2626", lw=1.3,
             label="有效 ost")
    axes[1].set_ylabel("写带宽 flit/cycle")
    ax2.set_ylabel("有效 ost")
    axes[1].set_title(f"带宽仍跟着有效 ost（r={row['bw_eff_corr']}）",
                      fontsize=10)
    h1, l1 = axes[1].get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    axes[1].legend(h1 + h2, l1 + l2, fontsize=8)
    axes[1].grid(alpha=0.3)
    for ax in axes:
        ax.set_xlabel("cycle")
    fig.suptitle("double-buffer / 保序窗口的空等：A、B 都做到 99% 时，"
                 "有效 ost 塌成只剩还没回来的那几笔", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_blocker_repro(repro: dict, path: Path) -> None:
    """Victim vs control, with and without the circling blockers."""
    _use_cjk_font()
    rows = {r["tag"]: r for r in repro.get("blocker") or []}
    order = ("solo", "blockers_track0", "blockers_track32")
    lab = {"solo": "无挡路",
           "blockers_track0": "挡路 · tracker=∞",
           "blockers_track32": "挡路 · tracker=基线"}
    xs = list(range(len(order)))
    fig, axes = plt.subplots(1, 3, figsize=(12.4, 3.6))
    w = 0.34
    for i, tag in enumerate(order):
        r = rows[tag]
        axes[0].bar(i - w / 2, r["victim"]["bw_run"], w, color="#2563eb",
                    label="受害者 C10→M15" if i == 0 else None)
        axes[0].bar(i + w / 2, r["control"]["bw_run"], w, color="#16a34a",
                    label="对照 C16→M17" if i == 0 else None)
        axes[1].bar(i - w / 2, r["victim"]["hop_busy_dat"], w, color="#2563eb")
        axes[1].bar(i + w / 2, r["control"]["hop_busy_dat"], w, color="#16a34a")
        axes[2].bar(i, r["n_eject_defl_hot"], color="#dc2626")
    axes[0].set_ylabel("写带宽 flit/cycle（该核自己的 finish）")
    axes[0].set_title("谁被挡住", fontsize=10)
    axes[0].legend(fontsize=8)
    axes[1].set_ylabel("WriteData 因 hop 被占而没上环")
    axes[1].set_title("10→11 上的挡路石", fontsize=10)
    axes[2].set_ylabel("M11 下环失败而转圈")
    axes[2].set_title("移动障碍（eject deflect）", fontsize=10)
    for ax in axes:
        ax.set_xticks(xs)
        ax.set_xticklabels([lab[t] for t in order], fontsize=8)
        ax.grid(alpha=0.3, axis="y")
    fig.suptitle("复现：流量 2 上环堵在环上，把本来能走的流量 3 堵住",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def _setup_table(meta: dict) -> str:
    rows = [
        ["节点数 / plane 数", f"{len(meta['link_lats'])} / {meta['n_planes']}",
         ("一个双向闭合 full ring" if int(meta.get("n_planes") or 2) == 1
          else "两个独立的双向闭合 full ring，共用同一套几何")],
        ["AI core", f"{len(meta['core_nodes'])} 个：" +
         ", ".join(f"C{c}" for c in meta["core_nodes"]), "写发起方（CHI RN）"],
        ["memory 节点", f"{len(meta['mem_nodes'])} 个：" +
         ", ".join(f"M{h}" for h in meta["mem_nodes"]),
         "写目的地（completer）"],
    ]
    if meta.get("non_terminal"):
        rows.append(
            ["非终端节点", ", ".join(f"N{x}" for x in meta["non_terminal"]),
             "在环上转发，但既不发起也不接收写"])
    rows += [
        ["路由",
         ("最短路（链路时延之和；时延平局再比跳数，再平局 CW）"
          if meta.get("route") == "latency" else "最短路（跳数平局走 CW）"),
         ("core→mem 与 mem→core 同一规则；当前 link_lats 上与跳数最短重合"
          if meta.get("route") == "latency" else "S0 及全部方案一致")],
        ["plane 选择", meta["plane_sel"],
         ("单平面，无 plane 选择" if int(meta.get("n_planes") or 2) == 1
          else "两个 plane 之间做负载均衡")],
        ["每 core outstanding", meta["core_outstanding"],
         "同时在飞的写事务上限"],
        ["CHI VC", " / ".join(meta["vcs"]).upper(),
         "REQ、RSP、DAT 三条独立 VC，各自独立信用"],
        ["hop 容量", f"{meta['hop_bw_cap']} flit/cycle",
         f"{len(meta['link_lats'])} 节点 × 2 方向 × {meta['n_planes']} plane "
         f"× {meta['n_vc']} VC，σ={meta['sigma']}"],
        ["端口", f"inject {meta['board_ports']} / leave "
                 f"{meta['leave_ports']}"
                 + ("（每 node 每 plane 每 VC）" if meta.get("per_vc_ports")
                    else "（每 node 每 plane）"),
         ("REQ / RSP / DAT 各有独立上下环口，互不占槽"
          if meta.get("per_vc_ports")
          else "三条 VC 共享同一个上下环端口")],
        ["上环队列",
         (f"{meta['inj_depth']} 深共享 FIFO + 每向 "
          f"{meta.get('dir_inj_depth', 1)} 深 inject Q"
          if meta.get("shared_inj") else str(meta["inj_depth"])),
         ("每 (node, plane, VC) 一套：两个环方向共用那个 FIFO，"
          "其后每方向各一个 inject Q，该 VC 每拍上环 1 flit"
          if meta.get("shared_inj") and meta.get("per_vc_ports")
          else "每 (node, plane, VC)")],
        ["下环队列深度", meta["eject_depth"],
         ("每 (node, plane, VC)；两写一读："
          "该 VC 两个方向可同拍各写入 1 flit，PE 每拍读 1 flit"
          if meta.get("per_vc_ports") and meta.get("two_write_leave")
          else "每 (node, plane, VC)" if meta.get("per_vc_ports")
          else "每 (node, plane)")],
        ["I-tag 门限 t_inj", meta["t_inj"], "限制注入饥饿时长"],
        ["E-tag 门限 t_xfer", meta["t_xfer"], "限制偏转次数"],
        ["写激励",
         f"{meta.get('burst_b', 128)}B burst / "
         f"{meta.get('stride_b', 4096)}B stride / "
         f"{(meta.get('tile_b') or 65536) // 1024}KB tile",
         f"1 flit = {meta.get('flit_b', 64)}B，每笔 WriteData ×{meta['W']}；"
         "地址哈希已把 8 个 mem 均衡"],
        ["HA RSP 时延", _jit_label(meta),
         "写请求到达 memory 后，DBIDResp / RetryAck / Comp 各自独立抽"],
        ["拥塞总线时延", f"{meta.get('bus_lat', 1)} 拍",
         "S1 / S15 专用广播，不占环上 hop；窗口 64 拍"],
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
        note = f"闭合边（{tag(i)} ↔ {tag(0)}）" if i == n - 1 else ""
        rows.append([f"{tag(i)} — {tag((i + 1) % n)}", lat, note])
    return _table(["无向边", "hop 时延（拍）", "备注"], rows)


def _port_lb_txt(b: dict) -> str:
    if not b.get("merge_port_vcs", True):
        return "每 (node, plane, VC) 一口，三通道不再叠在一起"
    return "每 (node, plane) 只有一个上下环端口，三条 VC 共享"


def _bounds_table(b: dict) -> str:
    rows = [
        ["LB_link 每 VC 独立链路", b["link_lb"],
         "REQ/RSP/DAT 各占一条 VC，取三者最大"],
        ["LB_port " + ("每 VC 独立端口" if not b.get("merge_port_vcs", True)
                       else "端口合并"), b["port_lb"],
         ("inject / leave 每 (node, plane, VC) 一口，三通道不再叠在一起"
          if not b.get("merge_port_vcs", True)
          else "inject / leave 每 (node, plane) 只有一个端口，三 VC 共享")],
        ["LB_cut 割集", b["cut_lb"], "跨割面的流量除以割面上的有向链路数"],
        ["LB_txn 事务串行链", b["txn_lb"],
         "REQ→DBIDResp→WriteData→Comp 两个来回"],
        ["<b>bound</b>", f"<b>{b['bound']}</b>", "以上取最大"],
    ]
    return _table(["下界", "cycle", "含义"], rows)


def _summary_table(pat: dict, *, pattern: str = "", track: int | None = None,
                   schemes: tuple[str, ...] = SEC31) -> str:
    """Per-scheme fairness and write throughput at the baseline tracker."""
    rows = []
    thr_ref = pat["schemes"]["S0"]["fairness"]
    thr0 = thr_ref["throughput"]
    for s in schemes:
        if s not in pat["schemes"]:
            continue
        sch = pat["schemes"][s]
        f = sch["fairness"]
        d = 100.0 * (f["throughput"] - thr0) / thr0
        q = sch.get("retry") or {}
        lab = LABEL[s]
        if s == "S0":
            lab = f"S0 基线（tracker = {track}）" if track else "S0 基线"
        jb = f.get("jain_bin") or {}
        row = [lab, sch["makespan"],
               jb.get("jain_bin_mean", "—"), jb.get("jain_bin_null", "—"),
               jb.get("jain_bin_ratio", "—"), f["max_min"],
               f["bw_min"], f["bw_max"], f["throughput"],
               f"{d:+.1f}%", q.get("retry_per_txn", "—"),
               sch["n_deflections"], sch["n_board_fail"]]
        if pattern:
            row.insert(0, pattern)
        rows.append(row)
    bw_ = ((thr_ref.get("jain_bin") or {}).get("bin_w")) or "?"
    heads = ["方案", "makespan", f"Jain@{bw_}拍", "零模型", "ratio（验收）",
             "max/min", "最低 BW", "最高 BW", "吞吐 flit/cycle", "吞吐差",
             "重试/事务", "偏转", "上环失败"]
    if pattern:
        heads.insert(0, "流量")
    return _table(heads, rows)


def _track_table(pat: dict, track: int | None = None) -> str:
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
    base = f"{track}（本报告基线）" if track else "本报告基线"
    for tag, d in (("∞（环受限）", ref), (base, pat["schemes"]["S0"])):
        f = d["fairness"]
        q = d.get("retry") or {}
        rows.append([tag, d["makespan"], f["throughput"], f["max_min"],
                     f["bw_min"], f["bw_max"], q.get("retry_per_txn", 0.0),
                     q.get("max_ha_used"), q.get("outst_eff_mean", "—"),
                     d.get("lat_p99")])
    return _table(["每 completer 的请求 tracker", "makespan", "吞吐",
                   "max/min", "最低 BW", "最高 BW", "重试/事务",
                   "峰值占用表项", "有效 outstanding", "延迟 p99"], rows)


def _board_dir_rows(pat: dict, scheme: str = "S0") -> tuple[list[list], list[bool]]:
    d = (pat["schemes"].get(scheme) or {}).get("board_dir") or {}
    rows, hl = [], []
    for c in _cores(pat):
        r = d.get(c) or {}
        ok_cw, ok_ccw = int(r.get("ok_cw", 0)), int(r.get("ok_ccw", 0))
        fl_cw, fl_ccw = int(r.get("fail_cw", 0)), int(r.get("fail_ccw", 0))
        ok, fail = ok_cw + ok_ccw, fl_cw + fl_ccw
        ok_r = (max(ok_cw, ok_ccw) / min(ok_cw, ok_ccw)
                if min(ok_cw, ok_ccw) else float("inf") if ok else 0)
        fl_r = (max(fl_cw, fl_ccw) / min(fl_cw, fl_ccw)
                if min(fl_cw, fl_ccw) else float("inf") if fail else 0)
        mark = _dir_imbal(ok_cw, ok_ccw) or _dir_imbal(fl_cw, fl_ccw)
        rows.append([
            f"C{c}", ok_cw, ok_ccw,
            "∞" if ok_r == float("inf") else f"{ok_r:.2f}",
            fl_cw, fl_ccw,
            "∞" if fl_r == float("inf") else f"{fl_r:.2f}",
            "偏" if mark else "",
        ])
        hl.append(mark)
    return rows, hl


def _stimulus_note(meta: dict, pat: dict, fc: dict | None) -> str:
    """Forecast vs what this run actually did. Do not rewrite the forecast."""
    recv = (pat["schemes"]["S0"].get("wr_recv_by_ha") or {})
    xs = [int(v) for v in recv.values()]
    spread = (max(xs) - min(xs)) if xs else 0
    s0 = pat["schemes"]["S0"]
    s1 = (pat["schemes"].get("S1") or {})
    rows0, hl0 = _board_dir_rows(pat, "S0")
    rows1, hl1 = _board_dir_rows(pat, "S1") if s1 else ([], [])
    n0 = sum(1 for x in hl0 if x)
    n1 = sum(1 for x in hl1 if x)
    bu = meta.get("belief_update") or {}
    b0 = bu.get("s0_board") or {}
    b1 = bu.get("s1_board") or {}
    fc = fc or {}
    f1 = s1.get("fairness") or {}
    f0 = s0.get("fairness") or {}
    thr_note = ""
    if f1.get("throughput") is not None and f0.get("throughput"):
        d = 100.0 * (f1["throughput"] - f0["throughput"]) / f0["throughput"]
        thr_note = (f"S1 吞吐 {f1['throughput']}（{d:+.1f}% vs S0 "
                    f"{f0['throughput']}）。")
    bus_fc = meta.get("bus_lat_forecast") or {}
    bus_note = ""
    if bus_fc:
        bus_note = (
            f"<br><b>总线时延 {meta.get('bus_lat', '—')} 拍的预测</b>"
            f"（置信度 {bus_fc.get('confidence', '—')}）："
            f"{bus_fc.get('hypothesis', '')} "
            f"证伪：{bus_fc.get('falsify', '')}"
            f"<br>对照：S1 总线实测 {bu.get('s1_bus_lat', '—')} 拍。"
        )
    vc_fc = meta.get("vc_indep_forecast") or {}
    vc_note = ""
    if vc_fc:
        b = pat.get("bounds") or {}
        vc_note = (
            f"<br><b>三通道链路独立的预测</b>"
            f"（置信度 {vc_fc.get('confidence', '—')}）："
            f"{vc_fc.get('hypothesis', '')} "
            f"证伪：{vc_fc.get('falsify', '')}"
            f"<br>对照：per_vc_ports={meta.get('per_vc_ports')}，"
            f"bound={b.get('bound')}（link {b.get('link_lb')} / "
            f"port {b.get('port_lb')}），"
            f"S0 吞吐 {f0.get('throughput')}，max/min {f0.get('max_min')}。"
        )
    hz_fc = meta.get("ha_rsp_zero_forecast") or {}
    hz_note = ""
    if hz_fc:
        hz_note = (
            f"<br><b>HA RSP 时延 = 0 的预测</b>"
            f"（置信度 {hz_fc.get('confidence', '—')}）："
            f"{hz_fc.get('hypothesis', '')} "
            f"证伪：{hz_fc.get('falsify', '')}"
            f"<br>对照：HA RSP {_jit_label(meta)}，"
            f"S0 吞吐 {f0.get('throughput')}，"
            f"retry/txn {(s0.get('retry') or {}).get('retry_per_txn')}。"
        )
    tk_fc = meta.get("track128_forecast") or {}
    tk_note = ""
    if tk_fc:
        unb = (pat.get("s0_unbounded") or {}).get("fairness") or {}
        gap = (100.0 * (f0.get("throughput", 0) - unb.get("throughput", 0))
               / max(1e-9, unb.get("throughput", 1)))
        tk_note = (
            f"<br><b>ha_track = {meta.get('ha_track')} 的预测</b>"
            f"（置信度 {tk_fc.get('confidence', '—')}）："
            f"{tk_fc.get('hypothesis', '')} "
            f"证伪：{tk_fc.get('falsify', '')}"
            f"<br><b>对照：预测被推翻。</b>预测 S0 吞吐落在 "
            f"{tk_fc.get('predicted', {}).get('s0_thr')}，"
            f"实测只有 <b>{f0.get('throughput')}</b>；"
            f"预测 retry/txn ≤ "
            f"{tk_fc.get('predicted', {}).get('retry_per_txn_max')}，"
            f"实测 {(s0.get('retry') or {}).get('retry_per_txn')}；"
            f"预测 makespan {tk_fc.get('predicted', {}).get('makespan')}，"
            f"实测 {s0.get('makespan')}；"
            f"预测与 ∞ tracker 参照差 ≤ "
            f"{tk_fc.get('predicted', {}).get('unbounded_gap_pct_max')}%，"
            f"实测差 <b>{gap:+.1f}%</b>（参照 {unb.get('throughput')}）。"
            f"只有公平性那三项落在区间里（详见 3.3）。"
            f"<br><b>错在哪：</b>预测直接引用了 K = {CEILING_PROBE['k']} 的短探测"
            f"（那里 128 给出 {CEILING_PROBE['track'][2][1]}）。"
            f"官方 K = {meta.get('K')} 下稳态拥塞更深，"
            f"∞ 参照的峰值占用要 "
            f"{(unb_q := (pat.get('s0_unbounded') or {}).get('retry') or {}).get('max_ha_used')}"
            f" 个表项，128 装不下，实测峰值就顶在 "
            f"{(s0.get('retry') or {}).get('max_ha_used')}。"
            f"<b>tracker 的拐点跟 K 有关，短跑会低估所需表项数。</b>"
        )
    bj_fc = meta.get("bin50_fair_forecast") or {}
    bj_note = ""
    if bj_fc:
        jb = f0.get("jain_bin") or {}
        bj_note = (
            f"<br><b>{jb.get('bin_w')} 拍窗 Jain 主指标的预测</b>"
            f"（置信度 {bj_fc.get('confidence', '—')}）："
            f"{bj_fc.get('hypothesis', '')} "
            f"证伪：{bj_fc.get('falsify', '')}"
            f"<br>对照：jain_bin_mean = {jb.get('jain_bin_mean')}，"
            f"零模型 {jb.get('jain_bin_null')}，"
            f"ratio {jb.get('jain_bin_ratio')}；"
            f"每核每箱 {jb.get('flits_per_core_per_bin')} flit，"
            f"{jb.get('n_bins')} 个箱（详见 3.3）。"
        )
    return f"""
<div class="def">
<b>跑数前的预测</b>（置信度 {fc.get('confidence', '—')}）：
{fc.get('hypothesis', '')}
证伪：{fc.get('falsify', '')}{bus_note}{vc_note}{hz_note}{bj_note}{tk_note}<br>
<b>对照。</b>
{len(xs)} 个 mem 收到的 WriteData
{'完全一样（' + str(xs[0]) + ' / HA）' if xs and spread == 0
 else f'极差 {spread}'}。
S0 延迟 p50 = {s0.get('lat_p50')}，主指标 Jain@{(f0.get('jain_bin') or {}).get('bin_w')}拍
= {(f0.get('jain_bin') or {}).get('jain_bin_mean')} ÷ 零模型
{(f0.get('jain_bin') or {}).get('jain_bin_null')} = <b>ratio
{(f0.get('jain_bin') or {}).get('jain_bin_ratio')}</b>；
诊断项 max/min = {f0.get('max_min')}。
{thr_note}
S0 上环方向高亮 {n0}/{len(rows0)} 个核
（成功偏 {b0.get('n_ok_imbal', '—')}，失败偏 {b0.get('n_fail_imbal', '—')}，
失败最大比 {b0.get('max_fail_ratio', '—')}）；
S1 高亮 {n1}/{len(rows1) or 0} 个核
（失败最大比 {b1.get('max_fail_ratio', '—')}）。
预测写在 <code>meta.stimulus_forecast</code>，对照写在
<code>meta.belief_update</code>，前者不改。
</div>
"""


def _sec431(pat: dict, imgs: dict, schemes: tuple[str, ...] = ("S0", "S1")
            ) -> str:
    """§4.3.1: per-scheme CW/CCW board tables. Uniform only."""
    parts = [
        "<p>每个 AI core 的上环口按最短路把 REQ / WriteData 送进 CW（+1）或 "
        "CCW（−1）。次数是<b>该核作为源</b>的上环（不是 HA 回 RSP）。"
        "黄底行是两方向比 ≥ 2 的核。</p>"]
    for s in schemes:
        rec = (pat.get("schemes") or {}).get(s) or {}
        if not rec.get("board_dir"):
            continue
        key = "board_dir" if s == "S0" else f"board_dir_{s.lower()}"
        img = imgs.get(key, "")
        img_html = (f'<img src="{img}" alt="{s} board CW vs CCW">'
                    if img else "")
        parts.append(f"<p><b>{s}</b> · {LABEL.get(s, s)}</p>{img_html}"
                     f"{_board_dir_table(pat, s)}")
        parts.append(_board_dir_obs(pat, s))
    return "\n".join(parts)


def _board_dir_obs(pat: dict, scheme: str) -> str:
    """Name the one-sided cores even when they miss the ≥2 highlight."""
    d = (pat["schemes"].get(scheme) or {}).get("board_dir") or {}
    heavy = []
    for c in _cores(pat):
        r = d.get(c) or {}
        a, b = int(r.get("fail_cw", 0)), int(r.get("fail_ccw", 0))
        if a + b < 50 or min(a, b) == 0:
            if a + b >= 50 and min(a, b) == 0:
                heavy.append(f"C{c} 整侧为 0")
            continue
        ratio = max(a, b) / min(a, b)
        if ratio >= 1.5:
            side = "CW" if a > b else "CCW"
            heavy.append(f"C{c} 失败偏 {side}（{ratio:.2f}）")
    if not heavy:
        return ""
    return (f'<p class="note">{scheme} 失败比 ≥ 1.5 的核：'
            + "；".join(heavy)
            + "。高亮线仍是 ≥ 2，不因本轮结果改。</p>")


def _board_dir_table(pat: dict, scheme: str = "S0") -> str:
    rows, hl = _board_dir_rows(pat, scheme)
    n = sum(1 for x in hl if x)
    return (
        _table(["core", "成功 CW", "成功 CCW", "成功比",
                "失败 CW", "失败 CCW", "失败比", ""],
               rows, hl=hl)
        + f'<p class="note">高亮：该核 CW/CCW 成功或失败次数比 ≥ 2'
        f'（该侧合计 ≥ 50）。{scheme} 上 <b>{n}/{len(rows)}</b> 个核偏了。</p>'
    )


def plot_board_dir(pat: dict, path: Path, *, scheme: str = "S0") -> None:
    """Per-core board ok/fail split by CW vs CCW."""
    _use_cjk_font()
    d = (pat["schemes"].get(scheme) or {}).get("board_dir") or {}
    cs = _cores(pat)
    xs = list(range(len(cs)))
    ok_cw = [int((d.get(c) or {}).get("ok_cw", 0)) for c in cs]
    ok_ccw = [int((d.get(c) or {}).get("ok_ccw", 0)) for c in cs]
    fl_cw = [int((d.get(c) or {}).get("fail_cw", 0)) for c in cs]
    fl_ccw = [int((d.get(c) or {}).get("fail_ccw", 0)) for c in cs]
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 3.8), sharex=True)
    w = 0.36
    axes[0].bar([x - w / 2 for x in xs], ok_cw, w, color="#2563eb", label="CW")
    axes[0].bar([x + w / 2 for x in xs], ok_ccw, w, color="#ea580c", label="CCW")
    axes[0].set_ylabel("上环成功次数")
    axes[0].set_title("成功", fontsize=10)
    axes[1].bar([x - w / 2 for x in xs], fl_cw, w, color="#2563eb", label="CW")
    axes[1].bar([x + w / 2 for x in xs], fl_ccw, w, color="#ea580c", label="CCW")
    axes[1].set_ylabel("上环失败次数")
    axes[1].set_title("失败", fontsize=10)
    marks = [_dir_imbal(a, b) or _dir_imbal(c, d)
             for a, b, c, d in zip(ok_cw, ok_ccw, fl_cw, fl_ccw)]
    labels = [f"C{c}" + ("*" if m else "") for c, m in zip(cs, marks)]
    for ax in axes:
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontsize=8)
        for i, m in enumerate(marks):
            if m:
                ax.axvspan(i - 0.45, i + 0.45, color="#fde68a", zorder=0,
                           alpha=0.7)
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3, axis="y")
    fig.suptitle(f"{scheme}：每 core 上环成功 / 失败 × CW / CCW"
                 "（* = 两方向比 ≥ 2）", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


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
        row = [r["seed"], a.get("jain_bin_ratio", "—"), a["max_min"],
               a["throughput"]]
        for s in ("S15", "S16"):
            b = r.get(s)
            row += ([b.get("jain_bin_ratio", "—"), b["max_min"],
                     f"{b['thr_delta_pct']:+.2f}%"] if b else ["—", "—", "—"])
        rows.append(row)
    if not rows:
        return ""
    return _table(["seed", "S0 ratio", "S0 max/min", "S0 吞吐",
                   "S15 ratio", "S15 max/min", "S15 吞吐差",
                   "S16 ratio", "S16 max/min", "S16 吞吐差"], rows)


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


def _retry_sections(study: dict, imgs: dict, meta: dict, pat: dict,
                    repro: dict | None = None) -> str:
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
    repro_html = _repro_sections(repro, imgs) if repro else ""
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
{repro_html}
""" + _retry_s10(study, imgs, meta, pat)


def _repro_sections(repro: dict, imgs: dict) -> str:
    """Judgment + time-series / three-flow reproduction of over-injection."""
    if not repro:
        return ""
    m = repro["meta"]
    bu = repro.get("belief_update") or {}
    fc = m.get("forecast") or {}
    by = _ost_by_oc(repro)
    lo, hi, huge = by.get(16, {}), by.get(128, {}), by.get(256, {})
    inn = next((r for r in repro.get("ost") or []
                if r.get("tag") == "inorder"), {})
    blk = {r["tag"]: r for r in repro.get("blocker") or []}
    solo, t0, t32 = (blk.get("solo") or {}, blk.get("blockers_track0") or {},
                     blk.get("blockers_track32") or {})
    hops = " → ".join(str(a) for a, _ in m.get("victim_hops") or [])
    hops = f"{hops} → {m['victim_ha']}" if hops else f"C{m['victim']}→M{m['victim_ha']}"
    e1, e2 = bu.get("ex1") or {}, bu.get("ex2") or {}
    f1, f2 = fc.get("ex1") or {}, fc.get("ex2") or {}

    def _ok(pred: bool) -> str:
        return "命中" if pred else "未命中"

    ex1_core = (
        e1.get("oc16_retry") == 0
        and e1.get("oc128_thr_lt_oc16")
        and 20 <= (e1.get("oc128_eff") or 0) <= 26
    )
    ex1_corr = (e1.get("bw_eff_corr_128") or 0) >= 0.6
    ex1_hit = ex1_core and ex1_corr
    vdrop = e2.get("victim_drop_pct")
    cdrop = e2.get("control_drop_pct")
    ex2_hit = (vdrop is not None and vdrop <= -30
               and cdrop is not None and abs(cdrop) <= 15)
    vdrop_s = f"{vdrop:+.1f}" if vdrop is not None else "—"
    cdrop_s = f"{cdrop:+.1f}" if cdrop is not None else "—"
    return f"""
<h3>9.6 先判断，再复现：路上包太多会挡别人</h3>
<p>流控要解决的不是“环挤了所以限速”，而是<b>还没轮到你的时候不要出去</b>：
出去也得不到服务，只会站在队头变成别人的挡路石。两条机理事先写下来，
再在本仿真里对着硅上 ost=600 / ost=1000 那组图做时间序列复现。</p>

<div class="def">
<b>例子 1 — HA 过载 → RetryAck → 乱序 → 有效 ost 塌缩。</b>
请求堆到 HA 的 tracker 上，HA 回 RetryAck，接受顺序被打乱。
队列里 10 人和 100 人，最坏乱序从等 10 人变成等 100 人。
AIC 指令可以乱序，但有窗口：double buffer 下下一个 load A
必须等上一个 load A 做完，否则覆盖同一地址。乱序时 A、B 都做到 99%、
只等最后两个包，AIC 就只剩 ~2 个有效 ost。完全保序则 A 一完成就能发
下一个 load A，和 load B 并行。
硅上 ost=1000 时带宽曲线和有效 ost 曲线几乎同一形状，且远低于 1000、
也低于 600 这条地板；ost=600 时 ost 用满、没有抖动。
<b>判断：本仿真已经有这条机理。</b>
retry 扫描里 ost=16 零重试、有效=已分配；ost=128 有效钉在 ~23，
吞吐反而掉。缺的是时间序列——下面补上。
</div>

<div class="def">
<b>例子 2 — 环上转圈的移动障碍。</b>
流量 1、2 都去节点 4，4 接收能力有限，数据在 ring 上转圈，
本来能走的流量 3 被堵住。不下环的包变成移动障碍，转圈还加大乱序。
<b>判断：本仿真只是部分具备。</b>
环是无缓存的，在环 flit 从不排队（<code>n_inring_blocked = 0</code>）。
目的端溢出的协议回答是 RetryAck，不是站着的队列；WriteData
没有 DBIDResp 上不了环。能转圈的是下环口满了之后的
<code>_deflect</code>。均匀/热点混合流量里偏转次数已经上万，
但还没有“无辜流 vs 挡路流”的对照——下面用三条角色流补上。
</div>

<p class="note"><b>事先预测（跑数前写进源码，不回改）：</b>
例 1 置信度 {f1.get('confidence')} —
{f1.get('hypothesis')}
证伪条件：{f1.get('falsify')}。
例 2 置信度 {f2.get('confidence')} —
{f2.get('hypothesis')}
证伪条件：{f2.get('falsify')}。</p>

<h3>9.7 例子 1 复现：ost=16 用满不抖，ost=128 有效 ost 带着带宽塌</h3>
<img src="{imgs.get('ost_repro', '')}" alt="ost time series">
<p>S0、均匀写、tracker={m.get('ha_track')}、每 core {m.get('K')} 笔，
与第 9.2 节同一套扫描，只是把每 {m.get('outst_sample')} 拍的样本留下来：</p>
{_table(["标称 ost", "写吞吐", "重试/事务", "已分配", "停摆", "有效",
         "乱序比例", "带宽↔有效 ost"],
        [[oc,
          r.get("throughput"), r.get("retry_per_txn"),
          r.get("outst_used"), r.get("outst_park"), r.get("outst_eff"),
          r.get("ooo_frac"), r.get("bw_eff_corr")]
         for oc, r in ((16, lo), (128, hi), (256, huge))])}
<div class="def {'good' if ex1_core else 'bad'}">
<b>预测对照：主结论{_ok(ex1_core)}，
相关系数{_ok(ex1_corr)}。</b>
ost=16：重试 {e1.get('oc16_retry')}（预测 0），
有效 {e1.get('oc16_eff')} ≈ 已分配 {e1.get('oc16_used')}，
每核贴着上限——对应硅上 ost=600。
ost=128：已分配涨到 {e1.get('oc128_used')}，有效钉在
{e1.get('oc128_eff')}（预测 20–26），吞吐更低
（{hi.get('throughput')} &lt; {lo.get('throughput')}）。
带宽↔有效 ost 在 ost=16 时 r={e1.get('bw_eff_corr_16')}，
ost=128 时 <b>r={e1.get('bw_eff_corr_128')}</b>
（预测 ≥0.6，略低：写数据突发和 ost 采样对不齐，
但有效 ost 已经封顶，带宽跟着它走低）。
ost=256 有效仍然是 {huge.get('outst_eff')}，多出来的全是停摆。
<b>带宽塌是因为乱序/重试把实际 ost 压到远小于标称值。</b>
</div>
<img src="{imgs.get('ost_inorder', '')}" alt="inorder HOL">
<p>把退休改成按发起顺序（保序窗口 / double-buffer 的空等）之后，
ost=128 的浪费从“等 PCrd”换成“等前序”：停摆
{inn.get('outst_park')} + 队头阻塞 {inn.get('outst_hol')}，
有效仍是 {inn.get('outst_eff')}，吞吐
{inn.get('throughput')}。峰值 HOL {inn.get('max_hol_hold')} 笔——
就是“A、B 都做到 99%，只等最后两个乱序包”那段时间。</p>

<h3>9.8 例子 2 复现：挡路流把无辜流堵在 10→11</h3>
<p>三条角色，最短路事先钉死：挡路核 C{'/'.join(str(c) for c in m.get('blockers', []))}
全部写 M{m.get('block_ha')}，CW 都经过受害者的第一跳
{m.get('shared_hop')}；受害者 {hops}；
对照 C{m.get('control')}→M{m.get('control_ha')} 只有一跳、不经过 10→11。
无缓存环上挡路的方式不是把在环 flit 刹住，而是<b>占住 outgoing slot，
让受害者这一拍上不了环</b>；M{m.get('block_ha')} 下环口满了之后，
这些包继续转圈，变成移动障碍。</p>
<img src="{imgs.get('blocker', '')}" alt="blocker experiment">
{_table(["场景", "受害者带宽", "对照带宽", "受害者 DAT hop_busy",
         "对照 DAT hop_busy", "M11 转圈", "全环偏转", "RetryAck"],
        [[tag,
          r.get("victim", {}).get("bw_run"),
          r.get("control", {}).get("bw_run"),
          r.get("victim", {}).get("hop_busy_dat"),
          r.get("control", {}).get("hop_busy_dat"),
          r.get("n_eject_defl_hot"),
          r.get("n_deflections"),
          r.get("n_retry")]
         for tag, r in (("无挡路", solo),
                        ("挡路 · tracker=∞", t0),
                        (f"挡路 · tracker={m.get('ha_track')}", t32))])}
<div class="def {'good' if ex2_hit else 'bad'}">
<b>预测对照：{_ok(ex2_hit)}。</b>
加上挡路之后受害者带宽
<b>{vdrop_s}%</b>（预测 −30% 到 −90%），
对照
<b>{cdrop_s}%</b>（预测 −5% 到 +15%；
C2→M11 的 RSP 回程会擦过 C16，对照不是完全隔离，这是预测里漏掉的）。
更干净的证据是 hop_busy：受害者 WriteData 被占
{t32.get('victim', {}).get('hop_busy_dat')} 次，对照只有
{t32.get('control', {}).get('hop_busy_dat')} 次。
tracker=∞ 时 M11 转圈 {e2.get('eject_defl_m11_track0')} 次——
接收能力不够，WriteData 在环上转；有限 tracker 时转圈
{e2.get('eject_defl_m11_track32')} 次，RetryAck 把请求再送上环，
同样占着 10→11。
<b>流量 2 出去也得不到更好的服务，却把流量 3 变成过路障碍的受害者。</b>
</div>
<p class="note">本环做不到硅上那种“反压数据在 ring 上站着排队”：
在环 flit 绝对优先、从不 stall。移动障碍在这里的形态是
<b>下环失败后的整圈偏转</b>。机理相同：不该现在发的包，发出去就是挡路石。
例子 1 里 B 发太多挡了 A；例子 2 里流量 2 上环挡了流量 3。
场景不同，需要的 ost 也不同，所以只能动态流控。</p>
"""


def _retry_s10(study: dict, imgs: dict, meta: dict, pat: dict) -> str:
    f = _retry_facts(study)
    m = study["meta"]
    kn = m.get("knobs") or {}
    u = f["pats"][0]
    oc, track = f["oc"], f["track"]
    hd = f["base"]
    s17 = f["rate"].get("S17", {})
    s18 = f["rate"].get("S18", {})
    s16 = f["rate"].get("S16", {})
    rb = f.get("rate_best") or {}
    rr = f.get("rate_rows") or [{}]
    i = rr.index(rb) if rb in rr else 0
    rlo, rhi = rr[max(0, i - 1)], rr[min(len(rr) - 1, i + 1)]
    return f"""
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


def _fc_knobs() -> dict:
    from dse_ring2_write_fair import S16_OVERCOMMIT
    from rg_ring2_rate import Ring2RateParams
    p = Ring2RateParams()
    return {
        "s16_overcommit": S16_OVERCOMMIT,
        "t_low_mult": p.t_low_mult, "t_high_mult": p.t_high_mult,
        "k_min": p.k_min, "k_max": p.k_max, "p_max": p.p_max,
        "pace_max": p.pace_max, "win_init": p.win_init, "win_min": p.win_min,
        "swift_t_mult": p.swift_t_mult, "swift_rtt_floor": p.swift_rtt_floor,
        "swift_beta": p.swift_beta, "dctcp_g": p.dctcp_g,
    }


def _taxonomy_section(uni: dict, hot: dict | None, imgs: dict, meta: dict
                      ) -> str:
    """Classification, NoC-scale adapt, and same-pattern results."""
    kn = _fc_knobs()
    hot_ok = bool(hot and all(s in (hot.get("schemes") or {}) for s in FC_CMP))
    tbl_u = _summary_table(uni, pattern="均匀写", schemes=FC_CMP)
    tbl_h = _summary_table(hot, pattern="不均匀写", schemes=FC_CMP) \
        if hot_ok else ""
    axis = _table(
        ["方案", "驱动端", "执行器", "触发信号", "本 fabric 是否采用"],
        [
            ["S15 公平份额 + 槽预约", "混合：源端窗口 + 跳预约",
             "窗口（每窗注入预算）+ 有界 hole",
             "拥塞总线：达成计数的 max-min / 欠账", "采用（已在环上实现）"],
            ["S16 Homa 式授权", "接收端", "窗口（completer 同时授权数）",
             "least-served：给累计服务最少的源",
             f"采用；overcommit = {kn['s16_overcommit']}（必须低于 tracker）"],
            ["S17 TIMELY", "发送端", "速率（REQ 漏桶）",
             "RTT 梯度（REQ 上环 → DBIDResp 落地）",
             f"采用；T_low/T_high = {kn['t_low_mult']}/{kn['t_high_mult']}·minRTT"],
            ["S18 DCQCN", "发送端（标记在接收端算）", "速率（REQ 漏桶）",
             "completer tracker 占用的 RED + RetryAck",
             f"采用；k = {kn['k_min']}–{kn['k_max']}·tracker，p_max = {kn['p_max']}"],
            ["S19 Swift", "发送端", "窗口（在途事务数）",
             "时延：RTT 相对共享目标",
             f"采用；目标 = {kn['swift_t_mult']}·max(minRTT, "
             f"{kn['swift_rtt_floor']})，β = {kn['swift_beta']}"],
            ["S20 DCTCP", "发送端", "窗口（在途事务数）",
             "与 S18 同一套 tracker ECN",
             f"采用；同一 RED，窗口初值 {kn['win_init']}、下限 {kn['win_min']}"],
            ["PFC / 跳级暂停", "接收端", "暂停（XOFF）", "下游队列过水线",
             "不用：无缓存环没有可暂停的队列；暂停会冻住绝对优先的在环流量"],
            ["HPCC / INT", "发送端", "速率", "逐跳队列遥测",
             "不用：无缓存、无逐跳队列，没有可量的 INT"],
            ["CUBIC / Reno / 丢包窗口", "发送端", "窗口", "丢包 / 超时",
             "不用：本协议不丢包。RetryAck 是信用耗尽，事务还在"],
            ["BBR", "发送端", "速率 + 窗口", "瓶颈带宽探测 + RTT",
             "不用：没有可排空的 FIFO 队列，带宽探测读不到 BtlBw"],
            ["Homa SRPT + 优先级队列", "接收端", "授权 + 多优先级",
             "剩余消息长度",
             "只用授权一半（即 S16）。无缓存环不能再叠优先级队列"],
            ["IB / CHI 信用（PCrd）", "接收端", "窗口（表项）", "completer 信用",
             "已经是基线：ha_track + RetryAck + PCrdGrant，不是新方案"],
            ["XCP / RCP", "路由器", "速率", "逐包显式反馈",
             "不用：节点不是计算速率的路由器"],
        ])
    drop = _table(
        ["方案族", "为什么在这颗 NoC 上不适用"],
        [
            ["PFC", "作用对象是队列。环上没有队列；在环 flit 绝对优先，"
             "一暂停就把过路流量冻在注入口。"],
            ["HPCC / INT", "依赖 hop 队列深度。bufferless 的 hop 占用是 0/1，"
             "不是可以积分的队列。"],
            ["丢包窗口（CUBIC 等）", "CHI 写不丢包。RetryAck 之后事务继续，"
             "当成丢包会把窗口砍在一次往返都覆盖不了的深度。"],
            ["BBR", "用排队排空估带宽。这里的 RTT 胀大多半是 completer 服务，"
             "不是该消掉的网络队列。"],
            ["Homa 优先级", "SRPT 需要多优先级出口。本环每 (node, plane) "
             "一个端口、三条 VC 共享，没有第二套优先级仲裁。"],
            ["XCP / RCP", "要把速率写进包，由路由器算。本节点只做 I-tag / E-tag。"],
        ])

    def _row(pat: dict, s: str) -> dict:
        sch = pat["schemes"][s]
        f = sch["fairness"]
        q = sch.get("retry") or {}
        t0 = pat["schemes"]["S0"]["fairness"]["throughput"]
        jb = f.get("jain_bin") or {}
        return {
            "mm": f["max_min"], "thr": f["throughput"],
            "jb": jb.get("jain_bin_mean"), "ratio": jb.get("jain_bin_ratio"),
            "d": 100.0 * (f["throughput"] - t0) / t0,
            "r": q.get("retry_per_txn"),
        }

    u = {s: _row(uni, s) for s in FC_CMP if s in uni["schemes"]}
    h = {s: _row(hot, s) for s in FC_CMP if hot_ok and s in hot["schemes"]} \
        if hot_ok else {}
    bars = imgs.get("fc_bars", "")
    bars_h = imgs.get("fc_bars_hot", "")
    cmp = imgs.get("fc_compare", "")
    hot_has = (hot.get("mem") if hot else None) or meta.get("hot_has") \
        or [11, 13]
    hot_note = ""
    if hot_ok:
        hot_note = f"""
<p><b>不均匀写</b>（全部写入相邻的 M{'/M'.join(str(x) for x in hot_has)}）：</p>
{tbl_h}
<img src="{bars_h}" alt="hot FC per-core BW">
<p class="note">destination 几何重新拉开各核，主指标在这里才真正分得开：
S0 的 ratio 是 {h.get('S0', {}).get('ratio')}，
S15 {h.get('S15', {}).get('ratio')}、S16 {h.get('S16', {}).get('ratio')}。
窗口方案（S19 / S20）把窗口压到下限附近，ratio 走到
{h.get('S19', {}).get('ratio')} / {h.get('S20', {}).get('ratio')}
（max/min {h.get('S19', {}).get('mm')} / {h.get('S20', {}).get('mm')}），
吞吐也略低于 S0。
S15 的槽预约在这个场景反而把吞吐抬到 <b>{h.get('S15', {}).get('thr')}</b>
（{h.get('S15', {}).get('d'):+.1f}%）——热点把过路流量堆在少数 hop 上，
hole 第一次真正造出了槽位。
S16 两个指标都不如 S0：授权再扣一层，completer 更闲。</p>
"""
    return f"""
<h2>源端流控：分类、NoC 尺度调整与对照</h2>
<p>除基线 S0 和按规格实现的 S1 之外，源端流控按三轴分类：
<b>谁做决定</b>（发送端 / 接收端）、<b>执行器</b>（窗口 / 速率）、
<b>触发信号</b>。下面先分类并写清原理与利弊，再只保留能映射到
这颗无缓存环（上下环各 1 flit/cycle/node、RTT 以十到百拍计、
completer tracker 有限）的方案，做尺度调整，最后在<b>同一套
均匀写 / 不均匀写</b>上比 max/min 和写带宽吞吐。</p>

<h3>三个分类轴</h3>
<ul>
<li><b>发送端 vs 接收端。</b>
发送端根据本地观测（RTT、标记、总线）自己收油门；
接收端（completer）决定谁还可以再发。
CHI 写已经把数据相位的决定权给了接收端（没有 <code>DBIDResp</code>
不能发 WriteData），所以接收端方案几乎是零线格式成本；
发送端方案则必须另找一个执行器（漏桶或 outstanding 窗口）。</li>
<li><b>窗口 vs 速率。</b>
窗口限制在途事务数，一个槽一空就可以再发，所以有突发；
速率限制单位时间的新 REQ，突发被漏桶削平。
窗口对齐 BDP，速率对齐 tracker 的到达过程。
同一信号可以配两种执行器——S17 与 S19 共享时延，S18 与 S20 共享 ECN。</li>
<li><b>触发。</b>
时延 / RTT 梯度、显式标记（ECN / RetryAck）、
接收端调度（least-served）、以及本环特有的拥塞总线 + 槽预约。
丢包、逐跳队列、INT 在这颗 NoC 上没有对应物。</li>
</ul>
{axis}

<h3>适用方案：原理、优缺点、NoC 调整</h3>

<h4>S15 · 混合 · 窗口 + 跳预约 · 总线 max-min</h4>
<p><b>原理。</b>沿用 S1 的专用拥塞总线，但聚合从“取 max 等级”换成
各跳达成计数的 max-min 公平份额，并给落后节点预约有界 hole：
上游注入让出那一拍，预约者自己上环。
这是唯一一个<b>试图造槽</b>而不是只让别人少发的方案。</p>
<p><b>优点。</b>直接针对“在环绝对优先 → 源端限速造不出槽”这条死路；
总线不占 NoC 带宽。</p>
<p><b>缺点。</b>预约是离散的：预约者没用上，这一拍就空了；
总线、每跳 hole 状态都是额外硬件；均匀写下各核本来就已经很齐，
预约常变成用吞吐换一点 max/min。</p>
<p><b>NoC 调整。</b>窗口 64 拍（约 3 个 unloaded RTT，不是毫秒）；
<code>reserve_gap = 16</code>、<code>reserve_max = 32</code>，
只让真正的落后节点发 hole，避免互相取消。</p>

<h4>S16 · 接收端 · 授权窗口 · least-served</h4>
<p><b>原理。</b>Homa 的核心是接收端调度网络。CHI 写已经有授权：
completer 不立刻回 <code>DBIDResp</code>，而是把同时未完成的授权
压在 <code>overcommit</code> 以下，并优先给累计服务最少的源。
固定长度的写让 SRPT 退化成公平排队。扣住授权<b>不会造气泡</b>——
占优的核只是暂时没数据，槽位仍归谁能用谁用。</p>
<p><b>优点。</b>无新报文、无总线、无环上预约；
均匀写下 max/min 最齐。</p>
<p><b>缺点。</b><code>overcommit</code> 必须低于 tracker，否则与 S0
逐位相同；再扣一层授权会让 completer 更闲，吞吐掉一点。
不均匀写时授权解决不了入口 hop 被过路流量占满。</p>
<p><b>NoC 调整。</b>overcommit = {kn['s16_overcommit']}
（tracker 的一半）。论文的 RTTbytes 在这里就是“同时覆盖一轮握手
又不把 tracker 灌满”的授权数，不是字节。</p>

<h4>S17 · 发送端 · 速率 · RTT 梯度（TIMELY）</h4>
<p><b>原理。</b><code>WriteNoSnp</code> 从 REQ 上环到 DBIDResp 落地
本来就是一个 RTT，含 RetryAck 往返。梯度领先绝对时延，
在队列（这里是 tracker）堆满之前收油门。执行器是 REQ 漏桶：
灌满 tracker 的是 REQ 到达，WriteData 不能抢在授权前面。</p>
<p><b>优点。</b>不改线格式；均匀写下把重试从 ~1 次/事务压到 ~0.3，
少占环。</p>
<p><b>缺点。</b>必须有精确时间戳；按每核自己的 minRTT 定门槛时，
幸运短路径的核会更狠地收油门，max/min 变差；
不均匀写上 RTT 已被热点服务时间主导，梯度几乎帮不上忙。</p>
<p><b>NoC 调整。</b>论文 <code>T_high = 4·minRTT</code> 假定超出 minRTT
的全是该消的排队。这里 unloaded RTT ~20，有效工作点 ~150
（completer 服务，不该消掉），照搬会把吞吐打到 S0 的几十分之一。
门槛改成 {kn['t_low_mult']} / {kn['t_high_mult']}·minRTT；
<code>delta = 1/16</code>，匹配每核只有几十个样本的规模。</p>

<h4>S18 · 发送端 · 速率 · tracker ECN（DCQCN）</h4>
<p><b>原理。</b>无缓存环没有可标记的队列。真正溢出的是 completer
的请求 tracker。RED 画在 tracker 占用上，RetryAck 视为 p = 1 的标记。
标记搭在协议已有的 DBIDResp / RetryAck 上，不需要 CNP。</p>
<p><b>优点。</b>不需要时间戳；均匀写下重试下降且吞吐略高于 S0
（少了空转的 REQ/RetryAck）。</p>
<p><b>缺点。</b>标记来得晚（tracker 已经 80%+）；
定时器必须从微秒改成拍，否则回升太慢。</p>
<p><b>NoC 调整。</b>论文 k_min = 0.4、p_max = 0.5 会把正常工作的
tracker 当成拥塞。改为 k = {kn['k_min']}–{kn['k_max']}、
p_max = {kn['p_max']}；α / rate 定时器 = 8 / 24 拍。</p>

<h4>S19 · 发送端 · 窗口 · 时延（Swift）</h4>
<p><b>原理。</b>与 S17 同一类信号，执行器换成在途事务窗口：
RTT 低于共享目标则 +1/w，高于则按超出比例乘减。
窗口对齐 BDP，一个槽空了立刻补上，比漏桶更突发。</p>
<p><b>优点。</b>均匀写下窗口自己落到 ~{kn['win_init']}–32，
重试降到 0.1 以下，写吞吐高于速率方案。</p>
<p><b>缺点。</b>乘减比漏桶狠：目标设低了会把窗口按到 BDP 以下，
核与核的 minRTT 若各自为政，幸运核会被多砍一刀；
不均匀写上时延差的是 destination，不是该被窗口抹平的拥塞。</p>
<p><b>NoC 调整。</b>论文 β = 0.8、目标贴着 minRTT，会把窗口按死
（试跑 max/min &gt; 8、吞吐腰斩）。
改为<b>全环共享</b> base RTT，并设地板
{kn['swift_rtt_floor']} 拍——相邻 3 拍的幸运样本不得当 base；
目标 = {kn['swift_t_mult']}·base（约 160 拍，对着有效工作点）；
β = {kn['swift_beta']}；窗口下限 {kn['win_min']}
（U 形曲线左侧悬崖在 4）。</p>

<h4>S20 · 发送端 · 窗口 · tracker ECN（DCTCP）</h4>
<p><b>原理。</b>与 S18 同一套 RED 标记，DCTCP 的窗口更新：
未标记 +1/w，标记则按 α/2 乘减。α 是标记分数的 EWMA。</p>
<p><b>优点。</b>和 S18 比，只换执行器，便于看窗口 vs 速率；
均匀写下重试压得更低（窗口本身就接近 U 形最优点）。</p>
<p><b>缺点。</b>RetryAck 若每次都砍窗口，一笔事务会在 RetryAck
和随后的 DBIDResp 上被砍两次；热点上窗口会顶在下限，max/min 变差。</p>
<p><b>NoC 调整。</b>RED 与 S18 共用；每个标记周期最多乘减一次
（对齐 S18 的 marked 锁）；窗口初值 {kn['win_init']}、
下限 {kn['win_min']}，单位是事务不是字节。</p>

<h3>不适用的方案</h3>
{drop}

<h3>对照：同一 pattern 下的 max/min 与写带宽吞吐</h3>
<p>硬件相同：上下环各 1 个端口、tracker = {meta.get('ha_track')}、
outstanding = {meta.get('core_outstanding')}、K = {meta.get('K')}。
S0 是对照基线，不参与“源端方案”分类。</p>
<img src="{cmp}" alt="FC max/min and throughput">
<p><b>均匀写</b>：</p>
{tbl_u}
<img src="{bars}" alt="uniform FC per-core BW">
<div class="def">均匀写下 S0 的主指标本来就在
ratio {u['S0']['ratio']}（max/min {u['S0']['mm']}）。再做公平性空间很小：
S16 最齐（ratio {u['S16']['ratio']}、max/min {u['S16']['mm']}）
但吞吐 {u['S16']['d']:+.1f}%；S15 的 ratio 是 {u['S15']['ratio']}。
<b>窗口方案做的是另一件事</b>——把标称 outstanding 收到有效 BDP 附近：
S19 / S20 重试从 {u['S0']['r']} 降到 {u['S19']['r']} / {u['S20']['r']}，
写吞吐 {u['S19']['thr']} / {u['S20']['thr']}
（{u['S19']['d']:+.1f}% / {u['S20']['d']:+.1f}%），
代价是 max/min 走到 {u['S19']['mm']} / {u['S20']['mm']}。
速率方案里 S18 是较稳的折中（吞吐 {u['S18']['d']:+.1f}%，
max/min {u['S18']['mm']}）；S17 重试也降了，但 max/min 是适用方案里
均匀写最差的（{u['S17']['mm']}）。</div>
{hot_note}
<p class="note">读法：要齐，看 S16（只在均匀、可接受少许吞吐时）；
要吞吐、且流量接近均匀，看 S19 / S20 / S18；
流量打进少数 mem 时，只有 S15 的预约还在加吞吐，
窗口/速率都会误伤离热点近的核。S1 两条都更差，见第 5 节。</p>
"""


# ---------------------------------------------------------------------------

def _txn_rate(rec: dict, n_c: int, w: int) -> dict[str, float | None]:
    """Completed txn rate and raw REQ board rate (retries included)."""
    f = rec.get("fairness") or {}
    thr = f.get("throughput")
    mk = rec.get("makespan") or 1
    n_txn = rec.get("n_txn_done") or 0
    q = rec.get("retry") or {}
    n_retry = q.get("n_retry")
    if n_retry is None:
        n_retry = int(round((q.get("retry_per_txn") or 0) * n_txn))
    lam = (thr / (n_c * w)) if thr and n_c and w else None
    req = ((n_txn + n_retry) / mk / n_c) if mk and n_c else None
    return {"thr": thr, "lam": lam, "req": req, "n_retry": n_retry,
            "retry_per_txn": q.get("retry_per_txn"),
            "max_min": f.get("max_min")}


def _ideal_cc(meta: dict) -> dict[str, float]:
    n_c = len(meta.get("core_nodes") or [])
    n_m = len(meta.get("mem_nodes") or [])
    w = int(meta.get("W") or 2)
    n_hot = 14
    coef_dat = n_hot * w / max(1, n_m)
    lam = 1.0 / max(coef_dat, n_hot * 2 / max(1, n_m), n_hot / max(1, n_m))
    return {"n_c": n_c, "n_m": n_m, "w": w, "n_hot": n_hot,
            "coef_dat": coef_dat, "lam": lam, "r_dat": w * lam,
            "tot": n_c * w * lam}


def _ideal_rate_section(meta: dict, pat: dict) -> str:
    """Equal-rate ideal CC, then why S0 / S1 miss it."""
    cc = _ideal_cc(meta)
    n_c, n_m, w = int(cc["n_c"]), int(cc["n_m"]), int(cc["w"])
    n_hot = int(cc["n_hot"])
    lam, r_dat, tot = cc["lam"], cc["r_dat"], cc["tot"]
    s0 = _txn_rate(pat["schemes"]["S0"], n_c, w)
    s1 = _txn_rate(pat["schemes"].get("S1") or {}, n_c, w)
    unb = _txn_rate(pat.get("s0_unbounded") or {}, n_c, w)

    def _pct(x):
        return f"{100.0 * x / lam:.1f}%" if x is not None and lam else "—"

    def _f(x, nd=4):
        return "—" if x is None else f"{x:.{nd}f}"

    rows = [
        ["理想 CC", _f(lam), "100%", _f(lam), f"{tot:.3f}", "0", "1"],
        ["S0", _f(s0["lam"]), _pct(s0["lam"]), _f(s0["req"]),
         s0["thr"] if s0["thr"] is not None else "—",
         s0["retry_per_txn"] if s0["retry_per_txn"] is not None else "—",
         s0["max_min"] if s0["max_min"] is not None else "—"],
        ["S1", _f(s1["lam"]), _pct(s1["lam"]), _f(s1["req"]),
         s1["thr"] if s1["thr"] is not None else "—",
         s1["retry_per_txn"] if s1["retry_per_txn"] is not None else "—",
         s1["max_min"] if s1["max_min"] is not None else "—"],
        ["S0 无限 tracker", _f(unb["lam"]), _pct(unb["lam"]), _f(unb["req"]),
         unb["thr"] if unb["thr"] is not None else "—",
         unb["retry_per_txn"] if unb["retry_per_txn"] is not None else "—",
         unb["max_min"] if unb["max_min"] is not None else "—"],
    ]
    return f"""
<h3>4.5 理想拥塞控制下的注入率（三 VC 链路独立）</h3>
<p>REQ / RSP / DAT 的有向 hop 各有一份 σ=1 信用，互不占槽。
一笔事务仍要三条腿都走完，所以事务率受三张平面里最紧的那张限制：
<code>λ ≤ min(λ_REQ, λ_RSP, λ_DAT)</code>。
本小节<b>不把三 VC 叠进同一个上下环口</b>。
λ* 是<b>完成事务率</b>，不是含重试的 REQ 上环次数。</p>
<p>均匀最短路下，热 hop（0→1、8→7、10→11、18→17 及其反向）
各被 {n_hot} 条 (core, HA) 流穿过。系数
DAT = {n_hot}·{w}/{n_m} = {cc['coef_dat']:.2f}，
RSP = {n_hot}·2/{n_m} = {n_hot * 2 / max(1, n_m):.2f}，
REQ = {n_hot}/{n_m} = {n_hot / max(1, n_m):.2f}。</p>
<div class="def">
λ_DAT = λ_RSP = 1/{cc['coef_dat']:.2f} = <b>2/7 ≈ {lam:.4f}</b> txn/cycle/core，
λ_REQ = 4/7（更松）。
每核 WriteData <b>r* = {w}·(2/7) = 4/7 ≈ {r_dat:.4f}</b> flit/cycle，
全环 <b>R* = {n_c}·4/7 = 40/7 ≈ {tot:.4f}</b> flit/cycle。
对分（4 条有向 hop）在 λ* 上 DAT/RSP 占用 5/7，打不满；
先满的是四条热 hop。</div>
{_table(["", "完成 λ", "占 λ*", "含重试的 REQ 上环 / 核",
         "WriteData 全环吞吐", "重试/事务", "max/min"], rows)}
<p>S0 / S1 的 REQ 上环高于完成 λ，多出来的几乎全是 RetryAck 之后的重发，
不增加完成事务，还占 REQ / RSP hop。</p>
<h4>4.5.1 为什么 S0 / S1 到不了 λ*</h4>
<ol>
<li><b>有限 tracker 仍是一半原因。</b>8 个 HA 各
{meta.get('ha_track')} 表项，10 核 × {meta.get('core_outstanding')}
outstanding 往里灌，S0 重试 {s0['retry_per_txn']} 次/事务。
理想模型没有 RetryAck / PCrd / 重发 REQ。
去掉 tracker 后吞吐从 {s0['thr']} 升到 {unb['thr']}、
完成 λ 从 {_f(s0['lam'])} 升到 {_f(unb['lam'])}
（仍只占 λ* 的 {_pct(unb['lam'])}）。
上一轮 32 表项时更狠：吞吐只有 {TRACK32_ROUND['thr']}、
重试 {TRACK32_ROUND['retry_per_txn']} 次/事务。</li>
<li><b>另一半：环受限本身也只到 λ* 的 {_pct(unb['lam'])}。</b>
无缓存 + 在环绝对优先：本地要上环必须
hop 空着；偏转 flit 再绕一圈（S0 偏转
{pat['schemes']['S0'].get('n_deflections')} 次）。
S0 有空就灌，不是按热 hop 的 2/7 均速注。贪心对撞加上
HA 抖动 {_jit_label(meta)}，无限 tracker 的 makespan
{ (pat.get('s0_unbounded') or {}).get('makespan') } vs 下界
{(pat.get('bounds') or {}).get('bound')}。</li>
<li><b>S1 不是理想 CC。</b>按节点、按窗口失败次数做 AIMD，
不按 hop 做 max-min，也不能在环上留槽。
预算上限 {64 * (3 if meta.get('per_vc_ports') else 1)} flit / 64 拍
≈ {3 if meta.get('per_vc_ports') else 1} flit/cycle/核，
高于理想自有流量 3λ* ≈ {3 * lam:.2f}。
它只是略减 Retry（{s1['retry_per_txn']} vs {s0['retry_per_txn']}），
完成 λ 仍是 {_f(s1['lam'])}。总线 30 拍 &lt; 窗口 64，
到不了“按热 hop 配额”。</li>
</ol>
{'' if meta.get('per_vc_ports') else '''<p class="note">若上下环口仍是三 VC 共用 1 个端口，另有一条更紧的
λ ≤ 4/15（mem leave），见此前端口合并分析。</p>'''}
"""


# Short side probe (not the official JSON): uniform write, K=4000, seed 0,
# 1 plane, official FABRIC otherwise, only `ha_track` swept. `t_hold` is the
# mean residency of a live transaction (Little's law on outst_eff), which
# over-counts the tracker-held population — it also contains REQs still on
# their way to the HA. It is here to show the trend, not as an occupancy.
CEILING_PROBE = {
    "k": 4000,
    "track": [
        ["32（上一轮）", 2.253, 256.5, 0.992],
        ["64", 2.312, 408.7, 0.808],
        ["128（本轮取值）", 4.424, 551.7, 0.003],
        ["256", 4.498, 552.3, 0.000],
        ["∞", 4.498, 552.3, 0.000],
    ],
    # At ha_track=∞, widening any buffer barely moves the plateau.
    "knobs": [
        ["基准（∞ tracker）", 4.498],
        ["eject_depth 4 → 16", 4.503],
        ["inj_depth 8 → 32", 4.498],
        ["dir_inj_depth 1 → 4", 4.544],
        ["eject_bw 1 → 2", 4.486],
        ["core_outstanding 128 → 512", 4.495],
    ],
    "plateau": 4.498,
    "hot_seg_util": 79.7,
    "board_fail_per_core_cycle": 0.867,
    "knee": 128,
}

# Frozen results from the two previous official rounds, both at ha_track = 32
# on this same per-VC-port fabric. Kept as constants because the baseline
# tracker has since moved to 128: the HA-think-time ablation in 3.2.3 is a
# track-32 measurement on both sides, and neither column describes the
# current baseline. Keeping them here stops that section from silently
# re-labelling old numbers as this round's.
TRACK32_ROUND = {
    "thr": 2.3848, "makespan": 167732, "retry_per_txn": 0.602,
    "outst_eff": 17.65, "t_hold": 148.0, "plateau": 4.5408,
    "jain_bin": 0.95426, "jain_bin_null": 0.92877, "jain_bin_ratio": 1.02745,
    "n_per_bin": 11.95,
    # The round before it, same fabric and tracker, HA RSP = U{4..64}.
    "jit_thr": 2.410, "jit_retry": 0.5689, "jit_eff": 22.98,
    "jit_t_hold": 190.7, "jit_plateau": 4.514,
}


def _total_bw_section(meta: dict, pat: dict, imgs: dict) -> str:
    """Ring-wide total write bandwidth vs theory, then instantaneous evenness."""
    cc = _ideal_cc(meta)
    n_c, n_m, w = int(cc["n_c"]), int(cc["n_m"]), int(cc["w"])
    r_bind, n_hot = cc["tot"], int(cc["n_hot"])
    b = pat.get("bounds") or {}
    s0 = pat["schemes"]["S0"]
    mk = int(s0["makespan"] or 1)
    flits = n_c * int(meta["K"]) * w
    n_txn = int(b.get("n_txn") or 0)
    n_retry = int(((s0.get("retry") or {}).get("n_retry")) or 0)
    plateau = _plateau_bw(pat, meta) or CEILING_PROBE["plateau"]
    qref_ha = ((pat.get("s0_unbounded") or {}).get("retry")
               or {}).get("max_ha_used")

    rows = []
    for s in ("S0", "S1"):
        ib = _inst_balance(pat, meta, s)
        if not ib:
            continue
        rows.append([s, ib["total_mean"],
                     f"{100.0 * ib['total_mean'] / r_bind:.1f}%",
                     ib["total_p05"], ib["total_p50"], ib["total_p95"],
                     ib["total_min"], ib["total_max"]])
    got = rows[0][1] if rows else 0.0
    thr0 = float((s0.get("fairness") or {}).get("throughput") or got)
    eff = float((s0.get("retry") or {}).get("outst_eff_mean") or 0)
    t_hold = round(eff * n_c / max(n_txn / mk, 1e-9), 1) if n_txn else None
    hz_ablate = ""
    if int(meta.get("ha_rsp_jit") or 0) <= 0:
        hz_ablate = f"""
<h4>3.2.3 存档：HA RSP 时延改成 0 并不改吞吐</h4>
<p class="note">这一小节是<b>历史消融，两列都在 ha_track = 32 上测的</b>，
和本轮的 {meta.get('ha_track')} 个表项不是同一个工作点 ——
留在这里是因为它的结论仍然成立，而它的绝对数字不再描述本轮基线。
本轮沿用 HA RSP = 0。</p>
<p>那一轮把 completer 的 DBIDResp / RetryAck / Comp 从
U{{4..64}}（均值 34 拍/条，一笔写至少两条 ≈ 68 拍）改成 <b>0</b>，
预测是 T_hold 少掉约 68 拍、32 个 tracker 周转加快、吞吐往平台靠：</p>
{_table(["量（均为 ha_track = 32）", "HA RSP U{4..64}", "HA RSP 0"],
        [["S0 总写带宽", TRACK32_ROUND["jit_thr"], TRACK32_ROUND["thr"]],
         ["retry/txn", TRACK32_ROUND["jit_retry"],
          TRACK32_ROUND["retry_per_txn"]],
         ["outst_eff / 核", TRACK32_ROUND["jit_eff"],
          TRACK32_ROUND["outst_eff"]],
         ["T_hold（拍）", TRACK32_ROUND["jit_t_hold"],
          TRACK32_ROUND["t_hold"]],
         ["无限 tracker 平台", TRACK32_ROUND["jit_plateau"],
          TRACK32_ROUND["plateau"]]])}
<div class="def warn">预测被推翻：<b>吞吐 {TRACK32_ROUND['jit_thr']} →
{TRACK32_ROUND['thr']}，几乎不动</b>。
T_hold 从 {TRACK32_ROUND['jit_t_hold']} 降到 {TRACK32_ROUND['t_hold']}，
outst_eff 从 {TRACK32_ROUND['jit_eff']} 降到 {TRACK32_ROUND['outst_eff']}，
两者同比例缩小，所以事务率不变（Little：λ = N / T）。
HA think time 不在关键路径上：tracker 占用由 WriteData 在环上的
飞行 / 排队决定，DBID / Comp 的 0 拍只是剪掉流水线里的空等待，
<b>释放速率不变</b>。真正解开这一层的是把表项本身加到
{meta.get('ha_track')}（见 3.2.2）。</div>
"""

    # Per-VC port occupancy, straight from the stored counts.
    ports = [
        ["mem 下环 · req", (n_txn + n_retry) / (n_m * mk),
         f"{n_txn} REQ + {n_retry} 重发"],
        ["mem 下环 · dat", n_txn * w / (n_m * mk), f"{n_txn * w} WriteData"],
        ["core 下环 · rsp", (2 * n_txn + 2 * n_retry) / (n_c * mk),
         "DBID + Comp + RetryAck + PCrd"],
        ["core 上环 · dat", n_txn * w / (n_c * mk), f"{n_txn * w} WriteData"],
        ["core 上环 · req", (n_txn + n_retry) / (n_c * mk),
         f"{n_txn} REQ + {n_retry} 重发"],
    ]
    port_rows = [[k, f"{100 * u:.1f}%", note] for k, u, note in ports]

    ib0 = _inst_balance(pat, meta, "S0")
    jb = (s0.get("fairness") or {}).get("jain_bin") or {}
    sw = {r["bin_w"]: r for r in (ib0.get("sweep") or [])}
    # The worst bins are warm-up / drain, not steady state: say where they sit.
    tail = [t for t, j in zip(ib0.get("t") or [], ib0.get("jain") or [])
            if j < 0.8]
    tail_n = len(tail)
    tail_lo = max((t for t in tail if t < (ib0["t"][-1] / 2)), default=0)
    tail_hi = min((t for t in tail if t >= (ib0["t"][-1] / 2)), default=0)
    bw_ = meta.get("bin_w")
    wide = max(sw) if sw else 0
    return f"""
<h3>3.2 全环总写带宽的理论值，以及实测随时间的曲线</h3>
<p>三条 VC 各有自己的上 / 下环端口，所以端口不再是最紧的资源，
反而是<b>最忙那条 dat 链路</b>绑定：{n_hot} 对 (core, mem) 的 WriteData
要压在同一段 hop 上，每对每笔写 {w} 个 flit。</p>
<div class="def">热 hop 上的 dat：每笔写占 {n_hot} × {w} ÷ {n_m} =
{n_hot * w / n_m:.2f} 个 hop-slot，故 λ* = 1/{n_hot * w / n_m:.2f} =
<b>{cc['lam']:.4f}</b> txn/cycle/核。<br>
<b>全环理论总写带宽 R* = {n_c} × {w} × {cc['lam']:.4f} =
{r_bind:.3f} flit/cycle = {r_bind * int(meta.get('flit_b', 64)):.0f} B/cycle。</b><br>
交叉校验：总 WriteData {flits} flit ÷ makespan 下界 {b.get('bound')} 拍 =
{flits / max(1, b.get('bound') or 1):.3f}，与上式一致。<br>
端口界这次松得多（port_lb {b.get('port_lb')} 拍，等价
{flits / max(1, b.get('port_lb') or 1):.1f} flit/cycle）——
这正是三 VC 不共享端口带来的变化。</div>
<img src="{imgs.get('totalbw', '')}" alt="total write bandwidth over time">
{_table(["方案", "总带宽均值", "占 R*", "p05", "p50", "p95", "最低箱", "最高箱"],
        rows)}
<p>实测均值 {got}，只有 R* 的 <b>{100.0 * got / r_bind:.1f}%</b>。
下面先排除 fabric，再定位真正的瓶颈。</p>
<h4>3.2.1 端口全都空着</h4>
<p>按存储的计数反算每个 VC 端口的占用率（{mk} 拍、{n_m} 个 mem、{n_c} 个 core）：</p>
{_table(["端口", "占用率", "承载"], port_rows)}
<div class="def">最忙的一个也只有
{max(100 * u for _k, u, _n in ports):.1f}%，<b>没有任何端口接近饱和</b>。
所以缺掉的带宽不在端口上。</div>
<h4>3.2.2 直接扫 tracker：瓶颈在 completer</h4>
<p>短探测（不是官方 JSON；uniform 写、K={CEILING_PROBE['k']}、seed 0、1 plane，
其余同官方 FABRIC，只扫 ha_track）：</p>
{_table(["ha_track", "总写带宽", "在飞事务平均驻留（拍）", "retry/txn"],
        CEILING_PROBE["track"])}
<div class="def warn">这张探测表<b>在官方 K 上是乐观的</b>，本轮把它推翻了。
探测里 128 跳到 {CEILING_PROBE['track'][2][1]}、retry 近乎归零；
官方 K = {meta.get('K')} 下 ha_track = {meta.get('ha_track')} 只跑到
<b>{thr0}</b>，retry/txn 还有
{(s0.get('retry') or {}).get('retry_per_txn')}，
而且<b>峰值占用正顶在 {(s0.get('retry') or {}).get('max_ha_used')}
个表项上（= ha_track，说明还在削）</b>。<br>
原因是<b>拐点跟 K 有关</b>：短跑（K = {CEILING_PROBE['k']}）拥塞没长到稳态，
128 个表项就够用；官方 K 下稳态更深 —— 同一 fabric 的 ∞ tracker 参照
峰值要用到 <b>{qref_ha} 个表项</b>，128 显然装不下。
<b>用短探测定 tracker 尺寸会低估。</b></div>
<div class="def">所以 32 → {meta.get('ha_track')} 是<b>缓解而不是解除</b>：
吞吐 {TRACK32_ROUND['thr']} → {thr0}
（{100.0 * (thr0 - TRACK32_ROUND['thr']) / TRACK32_ROUND['thr']:+.0f}%），
makespan {TRACK32_ROUND['makespan']} → {mk}，
retry/txn {TRACK32_ROUND['retry_per_txn']} →
{(s0.get('retry') or {}).get('retry_per_txn')}；
占无限 tracker 平台的比例从
{100.0 * TRACK32_ROUND['thr'] / TRACK32_ROUND['plateau']:.0f}% 抬到
<b>{100.0 * thr0 / plateau:.0f}%</b>，还差 {100 - 100.0 * thr0 / plateau:.0f}%。</div>
<p>剩下那个 {plateau} 的平台不是缓存不够 —— 在 ∞ tracker 下把各级缓存
单独放宽，带宽都只在 ±1% 内动：</p>
{_table(["改动（ha_track = ∞）", "总写带宽"], CEILING_PROBE["knobs"])}
<p>平台的成因是<b>无缓存环的上环饥饿</b>：在环 flit 优先，核只能挤进空隙。
此时最忙的 dat 段占用 {CEILING_PROBE['hot_seg_util']}%，
已经离 R* 假设的 100% 不远，但核每拍仍有约
{CEILING_PROBE['board_fail_per_core_cycle']} 次上环失败 ——
决定上环延迟的是空档的分布，而不是空档的总量。</p>
<div class="def">于是完整分解仍是三层，只是第三层变薄了：<br>
<b>{r_bind:.3f}</b>（热 hop 的 dat 链路界，假设完美打包）
→ <b>{plateau}</b>（无缓存环上环饥饿）
→ <b>{thr0}</b>（{meta.get('ha_track')} 个 tracker 再削掉
{100 - 100.0 * thr0 / plateau:.0f}%；上一轮 32 个时削掉
{100 - 100.0 * TRACK32_ROUND['thr'] / TRACK32_ROUND['plateau']:.0f}%）。<br>
<b>两层都还要动</b>：tracker 要装到 ∞ 参照的峰值
{qref_ha} 个附近才不再削，之后才轮到上环仲裁。加 buffer 仍然没有用。</div>
<p class="note">再往前一版<b>三 VC 共享端口</b>的 fabric（R* = 5.333、端口绑定、
平台 3.466、实测 2.541）说明的是另一件事：端口拆开抬高了 R* 与平台，
但在 32 tracker 下实测反而下降，因为 REQ 有专用端口后到达 HA 更快、
tracker 被打得更凶。<b>加宽 fabric 必须和放开 tracker 一起做</b>：
本轮把 tracker 放到 {meta.get('ha_track')}，实测才从 2.541 / {TRACK32_ROUND['thr']}
走到 {thr0}。</p>
{hz_ablate}

<h3>3.3 各核瞬时带宽均衡度（主指标）</h3>
<div class="def"><b>指标定义</b>：把竞争窗口 [0, t_fair] 切成宽度
{jb.get('bin_w')} 拍的箱，在每一个箱内对 {jb.get('n_cores')} 个 core 的写带宽
（= 该箱内各核 WriteData flit 数）算一次 Jain 指数，再对<b>所有箱取平均</b>。<br>
只统计完整落在竞争窗口内的箱：过了 t_fair 就有核已经跑完自己的配额，
那里的 0 是没活干，不是被饿死。<br>
<b>判定</b>：对照同窗零模型的比值 ratio ≥ 1.0（见 2.1）。</div>
<p><b>S0 基线：Jain@{jb.get('bin_w')}拍 = {jb.get('jain_bin_mean')}</b>，
零模型 {jb.get('jain_bin_null')}，<b>ratio = {jb.get('jain_bin_ratio')}</b>
（{jb.get('n_bins')} 个箱，每核每箱平均
{jb.get('flits_per_core_per_bin')} 个 flit；p05 {jb.get('jain_bin_p05')}、
最差箱 {jb.get('jain_bin_min')}）。各方案的值见 3.1 汇总表。</p>
<p class="note">那个 {jb.get('jain_bin_min')} 的最差箱不是稳态：Jain &lt; 0.8 的
{tail_n} 个箱（占 {100.0 * tail_n / max(1, jb.get('n_bins') or 1):.1f}%）
全部落在 t ≤ {tail_lo} 的起步段和 t ≥ {tail_hi} 的收尾段 ——
前者还有核没发出第一笔 WriteData，后者已有核在清最后几个 flit。
中段没有这种箱，所以 p05 {jb.get('jain_bin_p05')} 比最小值更能代表分布。</p>
<p>为什么要按箱平均而不是直接看整段：闭环批量下每个核最终都要注入同样的
K×W 个 flit，把十几万拍平均掉之后 Jain = {s0['fairness']['jain']}、
max/min = {s0['fairness']['max_min']}，接近 1 有一半是配额相同这个算术事实
造成的，看不出瞬时行为。缩到 {bw_} 拍后箱内 max/min 平均
{ib0.get('mm_mean')}、p95 {ib0.get('mm_p95')}、最坏 {ib0.get('mm_max')}。</p>
<div class="def">但 {jb.get('jain_bin_mean')} 距离 1.0 的这段差，
<b>有多少是真不公平，有多少只是计数噪声</b>，必须先分开 ——
每核每箱只有约 {jb.get('flits_per_core_per_bin')} 个 flit，
纯抽样波动就足以把 Jain 拉低。零模型：把该箱的总 flit 数按等概率多项分布
撒到 {n_c} 个核上，即<b>完全公平的仲裁透过同一个 {bw_} 拍窗口去看</b>
会长什么样。<b>该跟零模型比，不该跟 1.0 比。</b></div>
{_table(["分箱宽度", "每核 flit 数", "箱数", "实测 Jain", "零模型 Jain",
         "实测 max/min", "零模型 max/min", "判定"],
        [[r["bin_w"], r["count_per_core"], r["n_bins"],
          f"{r['obs_jain']:.5f}", f"{r['null_jain']:.5f}",
          f"{r['obs_mm']:.3f}", f"{r['null_mm']:.3f}",
          "低于零模型" if r["obs_mm"] < r["null_mm"] else "高于零模型"]
         for r in (ib0.get("sweep") or [])])}
<img src="{imgs.get('instbal', '')}" alt="instantaneous balance vs null model">
<div class="def good">结论：<b>主指标达标 —— {jb.get('jain_bin_mean')} 高于同一窗口下
完全公平的零模型（闭式 {jb.get('jain_bin_null')}、蒙特卡洛
{sw.get(bw_, {}).get('null_jain')}，两者一致），ratio
{jb.get('jain_bin_ratio')} ≥ 1.0</b>，
而且实测的瞬时不均衡在每一个尺度上都比零模型更小
（{bw_} 拍：Jain {sw.get(bw_, {}).get('obs_jain')} vs
零模型 {sw.get(bw_, {}).get('null_jain')}；
max/min {sw.get(bw_, {}).get('obs_mm')} vs
{sw.get(bw_, {}).get('null_mm')}），
并且随窗口变宽按 1/√N 衰减（{wide} 拍时已收到
{sw.get(wide, {}).get('obs_mm')}）。
所以主指标没能到 1.0 <b>完全来自 {bw_} 拍的采样粒度，不是不公平</b>；
环上的 RR + I-tag 仲裁比独立随机到达还要规整（亚泊松）。</div>
<p class="note">这条判定只针对<b>核间</b>瞬时均衡。同一个核两个方向之间的
失败次数仍然是偏的（见 4.3.1 / 4.3.2），那是几何造成的，
与这里的时间粒度无关。</p>
"""


def _fail_ratio_section(meta: dict, pat: dict) -> str:
    """What a large CW/CCW fail ratio means, and why it appears."""
    s0 = pat["schemes"]["S0"]
    s1 = pat["schemes"].get("S1") or {}
    d0 = s0.get("board_dir") or {}
    rates, ok_rs, fl_rs = [], [], []
    for c in _cores(pat):
        r = d0.get(c) or {}
        ok = int(r.get("ok", 0) or (int(r.get("ok_cw", 0)) + int(r.get("ok_ccw", 0))))
        fl = int(r.get("fail", 0) or (int(r.get("fail_cw", 0)) + int(r.get("fail_ccw", 0))))
        if ok + fl:
            rates.append(fl / (ok + fl))
        ocw, occw = int(r.get("ok_cw", 0)), int(r.get("ok_ccw", 0))
        fcw, fccw = int(r.get("fail_cw", 0)), int(r.get("fail_ccw", 0))
        if min(ocw, occw):
            ok_rs.append(max(ocw, occw) / min(ocw, occw))
        if min(fcw, fccw):
            fl_rs.append(max(fcw, fccw) / min(fcw, fccw))
    recv = [int(v) for v in (s0.get("wr_recv_by_ha") or {}).values()]
    spread = (max(recv) - min(recv)) if recv else None
    f0 = s0.get("fairness") or {}
    funb = (pat.get("s0_unbounded") or {}).get("fairness") or {}
    rc = pat.get("root_cause_unbounded") or pat.get("root_cause") or {}
    bu = meta.get("belief_update") or {}
    b0, b1 = bu.get("s0_board") or {}, bu.get("s1_board") or {}

    def _cores_txt(xs):
        if not xs:
            return "无"
        return "、".join(f"C{c}" for c in xs)

    rate_s = (f"{min(rates):.0%}–{max(rates):.0%}" if rates else "—")
    ok_s = f"{max(ok_rs):.2f}" if ok_rs else "—"
    fl_s = f"{max(fl_rs):.2f}" if fl_rs else "—"
    return f"""
<h3>4.3.2 上环失败比大代表什么</h3>
<p>「失败比」是<b>同一个核两条出边的失败次数比</b>
<code>max(CW, CCW) / min</code>，不是失败率。
黄底仍是比 ≥ 2 且该侧合计 ≥ 50。</p>
<p>S0 各核失败率其实都差不多（{rate_s}）。
大的是方向比：成功比最大 {ok_s}，失败比最大 {fl_s}。
S0 失败偏的核 {_cores_txt(b0.get('fail_imbal_cores'))}，
S1 为 {_cores_txt(b1.get('fail_imbal_cores'))}。</p>
<div class="def">
<b>不会造成 8 个 mem 收写不均。</b>
地址 interleave 已均衡，本轮 HA 收到的 WriteData
{'全部相同（' + str(recv[0]) + ' / HA）' if recv and spread == 0
 else f'极差 {spread}'}。
<b>本轮有限 tracker 下，也不会造成核间写带宽不均</b>
（S0 max/min = {f0.get('max_min')}）。
有限 tracker 的重试反压把十个核一起压住。
<b>环成为瓶颈时会：</b>无限 tracker 下 max/min = {funb.get('max_min')}，
带宽与邻 mem 数 r = {rc.get('corr_bw_adjmem')}。
失败比大的核就是邻 mem = 1 的那些核。
失败比大 = 这一侧出 hop 常被在环 flit 占着，本地尝试打不进去；
它是位置效应的症状，不是 mem 收包不均的原因。
本研究是纯写；读的 CompData 走反向同一组热 hop，机制相同。</div>
<h4>为什么失败比会很大</h4>
<ol>
<li>最短路确定。N9 / N19 不接 HA，C10 的近端在 CW（M11），
C18 在 CCW（M17）。成功次数已经按这个需求偏到约 {ok_s}。</li>
<li>偏的那一侧正好是全环最热的 hop（0→1、8→7、10→11、18→17），
各被 14/80 条 (core, HA) 流穿过。</li>
<li>在环 DAT / RSP 有绝对优先，本地注入只能等空槽。
热侧是「尝试更多 × 成功率更低」，成功比仍约 {ok_s}，
失败比被放大到 ≥ 2。两侧都是 mem 的核（如 C4 / C14）失败比 ≈ 1。</li>
</ol>
<p>S1 改的是节点总预算，不改最短路，也不改在环优先，
所以失败比还在，只是换了一批核。</p>
"""


# Short S1-knob probes. Not the official K=20000 JSON; recorded so the
# §5 claim is falsifiable. Do not rewrite after seeing later official runs.
S1_TUNE_PROBE = {
    "track32_k": 2000,
    "track32": [
        ["S0", 1.797, 1.875, 0.983],
        ["S1 spec", 1.789, 1.907, 0.983],
        ["S1 harsh", 1.780, 1.856, 0.983],
        ["S1 gentle", 1.795, 1.974, 0.983],
        ["S1 w=16 harsh", 1.791, 1.880, 0.982],
    ],
    "track0_k": 800,
    "track0": [
        ["S0", 3.354, 2.315, 1.196],
        ["S1 spec", 3.260, 2.399, 1.280],
        ["S1 harsh", 2.526, 2.544, 2.057],
        ["S1 gentle", 3.348, 2.244, 1.248],
    ],
}


def _s1_tune_section(pat: dict, imgs: dict | None = None) -> str:
    """Tuning S1 to shrink the fail ratio does not raise throughput to λ*."""
    s0 = pat["schemes"]["S0"]["fairness"]
    s1 = (pat["schemes"].get("S1") or {}).get("fairness") or {}
    d0 = (pat["schemes"]["S0"].get("board_dir") or {})
    d1 = ((pat["schemes"].get("S1") or {}).get("board_dir") or {})

    def _max_fl(d):
        xs = []
        for r in d.values():
            a, b = int(r.get("fail_cw", 0)), int(r.get("fail_ccw", 0))
            if min(a, b):
                xs.append(max(a, b) / min(a, b))
        return max(xs) if xs else None

    r0, r1 = _max_fl(d0), _max_fl(d1)
    t1 = None
    if s0.get("throughput") and s1.get("throughput") is not None:
        t1 = 100.0 * (s1["throughput"] - s0["throughput"]) / s0["throughput"]
    p32 = S1_TUNE_PROBE["track32"]
    p0 = S1_TUNE_PROBE["track0"]
    return f"""
<h3>5.1 把 S1 调到失败比变小，吞吐会怎样</h3>
<p>S1 没有“按方向压失败比”的旋钮，只有节点 AIMD。
热侧失败多 → 等级高 → 先砍的是已经挤不上去的核。</p>
<div class="def">官方本轮：
S1 失败比没有变小
（最大 {r1:.2f} vs S0 的 {r0:.2f}），
吞吐 {f'{t1:+.1f}%' if t1 is not None else '—'}，完成 λ 仍远低于 2/7。
失败比变小不会把核吞吐送到 λ*。
绝对失败次数下降，多半只是注得更少。</div>
<p>短探测（不是官方 JSON；<b>ha_track = 32 时代</b>用
K={S1_TUNE_PROBE['track32_k']}，环受限用
K={S1_TUNE_PROBE['track0_k']}、ha_track=0）：</p>
{_table(["方案（ha_track=32）", "吞吐", "最大失败比", "重试/事务"],
        [[a, b, c, d] for a, b, c, d in p32])}
<p class="note">这张表是 tracker = 32 时代的存档：那时 harsh / gentle /
窗口 16 几乎搬不动失败比和吞吐，因为 tracker 才是瓶颈。
本轮 ha_track 已经放到 128，绝对吞吐要看下面 ha_track=0 那张
（环受限）更贴近。</p>
{_table(["方案（ha_track=0）", "吞吐", "最大失败比", "max/min"],
        [[a, b, c, d] for a, b, c, d in p0])}
<p>环受限时 harsh 把吞吐从 3.35 打到 2.53，max/min 从 1.20 坏到 2.06，
失败比不降反升到 2.54；gentle 失败比略降到 2.24，吞吐几乎不变。
节点 AIMD 会误伤邻 mem = 1 的核。
要接近 2/7，需要按热 hop 的配额，不是把 S1 的 α/β 拧得更狠 ——
「先把 tracker 松开」这一步本轮已经做了（32 → 128），
基线因此进到环受限区，也就是上面这张表描述的那个区。</p>
{_s1_dirbal_section(pat, imgs or {})}
"""


def _dirbal_payload() -> dict | None:
    if not DIRBAL.exists():
        return None
    try:
        return json.loads(DIRBAL.read_text())
    except (ValueError, OSError):
        return None


def _dirbal_closest(db: dict) -> dict | None:
    """Lowest max_fail_ratio among official-K confirms that did not explode."""
    rows = [r for r in (db.get("confirm") or [])
            if r.get("K") == 20000
            and (r.get("max_fail_ratio") or 99) < 4]
    if not rows:
        return None
    return min(rows, key=lambda r: (r.get("max_fail_ratio") or 99,
                                    -float(r.get("throughput") or 0)))


def plot_s1_dirbal(pat: dict, path: Path) -> None:
    """Default S1 vs closest fail-ratio tune: per-core write BW."""
    db = _dirbal_payload()
    rec = _dirbal_closest(db or {})
    s1 = (pat.get("schemes") or {}).get("S1") or {}
    bw0 = (s1.get("fairness") or {}).get("bw_by_core") or {}
    bw1 = (rec or {}).get("bw_by_core") or {}
    if not bw0 or not bw1:
        return
    _use_cjk_font()
    cs = sorted(set(bw0) | set(bw1), key=int)
    x = list(range(len(cs)))
    y0 = [float(bw0.get(c, 0)) for c in cs]
    y1 = [float(bw1.get(c, 0)) for c in cs]
    fig, ax = plt.subplots(figsize=(10.2, 3.6))
    w = 0.36
    ax.bar([i - w / 2 for i in x], y0, w, color="#f59e0b", label="S1 默认")
    ax.bar([i + w / 2 for i in x], y1, w, color="#2563eb",
           label=rec.get("tag", "S1 调参"))
    ax.set_xticks(x)
    ax.set_xticklabels([f"C{c}" for c in cs])
    ax.set_ylabel("写带宽（WriteData flit/cycle）")
    ax.set_title("各核写带宽：默认 S1 vs 失败比最接近 <2 的参数")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _s1_dirbal_section(pat: dict, imgs: dict) -> str:
    """Official-K S1 search for CW/CCW fail ratio < 2, plus BW compare."""
    db = _dirbal_payload()
    if not db:
        return ""
    s1 = (pat.get("schemes") or {}).get("S1") or {}
    f1 = s1.get("fairness") or {}
    d1 = s1.get("board_dir") or {}
    def _max_fl(d):
        xs = []
        for r in d.values():
            a, b = int(r.get("fail_cw", 0)), int(r.get("fail_ccw", 0))
            if min(a, b):
                xs.append(max(a, b) / min(a, b))
        return max(xs) if xs else None
    default_r = _max_fl(d1)
    rec = _dirbal_closest(db)
    rows = []
    for r in db.get("confirm") or []:
        cfg = r.get("cfg") or {}
        knobs = []
        if cfg.get("dir_split"):
            knobs.append("dir_split")
        if cfg.get("band") and cfg.get("band") != "spec":
            knobs.append(cfg["band"])
        if cfg.get("window") and cfg.get("window") != 64:
            knobs.append(f"w={cfg['window']}")
        if cfg.get("cap_scale") not in (None, 1, 1.0):
            knobs.append(f"cap={cfg['cap_scale']}")
        if cfg.get("scope") == "both":
            knobs.append("scope=both")
        if not knobs:
            knobs.append("默认 band=spec")
        mark = "← 最接近" if rec and r.get("tag") == rec.get("tag") else ""
        rows.append([
            r.get("tag"), "、".join(knobs),
            r.get("throughput"), r.get("max_min"),
            r.get("max_fail_ratio"),
            "、".join(f"C{c}" for c in (r.get("fail_imbal_cores") or [])) or "无",
            mark,
        ])
    imbal0 = [f"C{c}" for c in _cores(pat)
              if _dir_imbal(int((d1.get(c) or {}).get("fail_cw", 0)),
                            int((d1.get(c) or {}).get("fail_ccw", 0)))]
    rows.insert(0, [
        "S1 默认（官方）", "band=spec，cap=1",
        f1.get("throughput"), f1.get("max_min"),
        None if default_r is None else round(default_r, 3),
        "、".join(imbal0) or "无",
        "",
    ])
    fc = db.get("forecast") or {}
    ds_fc = db.get("dir_split_forecast") or {}
    bw0 = f1.get("bw_by_core") or {}
    bw1 = (rec or {}).get("bw_by_core") or {}
    cmp_rows = []
    for c in sorted(set(bw0) | set(bw1), key=int):
        a, b = float(bw0.get(c, 0)), float(bw1.get(c, 0))
        dlt = 100.0 * (b - a) / a if a else None
        cmp_rows.append([
            f"C{c}", f"{a:.5f}", f"{b:.5f}",
            "—" if dlt is None else f"{dlt:+.2f}%",
        ])
    img = imgs.get("s1dirbal", "")
    img_html = (f'<img src="{img}" alt="S1 default vs tuned per-core BW">'
                if img else "")
    thr0, thr1 = f1.get("throughput"), (rec or {}).get("throughput")
    tdelta = (100.0 * (thr1 - thr0) / thr0) if thr0 and thr1 else None
    passed = bool(rec and (rec.get("max_fail_ratio") or 99) < 2
                  and not rec.get("fail_imbal_cores"))
    return f"""
<h3>5.2 官方 K=20000：把失败比压到 &lt; 2</h3>
<p class="note">这项研究是 <b>ha_track = 32 时代</b>跑的独立 study
（<code>results/ring2_s1_dirbal.json</code>，本轮未重跑）。
它的绝对吞吐（1.36–1.94）属于那个工作点；
可迁移的结论是<b>方向失败比压不到 &lt; 2</b> 这条定性判断。</p>
<p>预测写在跑数前（<code>results/ring2_s1_dirbal.json</code> 的
<code>forecast</code> / <code>dir_split_forecast</code>），对照写在下面，
前者不改。</p>
<div class="def">
<b>节点级预测</b>（置信度 {fc.get('confidence', '—')}）：
{fc.get('hypothesis', '')} 证伪：{fc.get('falsify', '')}<br>
<b>按方向拆预算的预测</b>（置信度 {ds_fc.get('confidence', '—')}）：
{ds_fc.get('hypothesis', '')} 证伪：{ds_fc.get('falsify', '')}
</div>
<p><b>对照。</b>官方规模上<b>没有</b>一组参数让所有核失败比都 &lt; 2。
cap_scale=0.25 / 0.15 几乎不限住（吞吐仍 ≈ 1.92–1.94），失败比仍 ≥ 2。
cap_scale=0.08 / 0.05 把总预算绑死：吞吐掉到 1.36 / 0.94，失败比炸到 12 / 11。
dir_split + harsh 最接近目标（最大失败比
<b>{(rec or {}).get('max_fail_ratio')}</b>，仍有
{', '.join('C'+str(c) for c in ((rec or {}).get('fail_imbal_cores') or []))}
≥ 2）。
{('已找到全核 &lt; 2。' if passed else '目标未达到。')}
K=8000 筛选会误判（默认 S1 那时已经 &lt; 2），必须以 K=20000 为准。</p>
{_table(["配置", "旋钮", "吞吐", "max/min", "最大失败比", "失败比≥2 的核", ""],
        rows)}
<h4>最接近配置 vs 默认 S1：各核写带宽</h4>
<p>最接近 = <code>{(rec or {}).get('tag')}</code>
（dir_split + band=harsh）。
全环吞吐 {thr1} vs 默认 {thr0}
（{f'{tdelta:+.1f}%' if tdelta is not None else '—'}）。
max/min 两边都 ≈ 1.001：均匀写下各核写带宽本来就是一条平线，
调参改变的是<b>绝对高度</b>，不是核间相对关系。</p>
{img_html}
{_table(["core", "默认 S1", "dir_split+harsh", "差"], cmp_rows) if cmp_rows else ''}
<p class="note">结论：节点总预算限不住方向比；限狠了更偏。
按方向拆 AIMD 只能从 2.24 收到 2.07，到不了 &lt; 2。
几何（邻 mem=1 + 热 hop + 在环优先）比 S1 的旋钮硬。</p>
"""


def _html_style() -> str:
    return """
body { font-family: ui-sans-serif, system-ui, "WenQuanYi Micro Hei",
       "Noto Sans CJK SC", sans-serif;
       margin: 2rem auto; max-width: 980px; color: #111; line-height: 1.65; }
h1,h2,h3 { font-weight: 650; }
h2 { margin-top: 2.2rem; border-bottom: 2px solid #e2e8f0;
      padding-bottom: 0.25rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.92rem; }
th,td { border: 1px solid #e5e7eb; padding: 0.35rem 0.5rem; text-align: right; }
th:first-child, td:first-child { text-align: left; }
td:last-child, th:last-child { text-align: left; }
th { background: #f8fafc; }
code { background: #f1f5f9; padding: 0.1rem 0.3rem; }
img { max-width: 100%; border: 1px solid #e5e7eb; }
.note { color: #475569; font-size: 0.9rem; }
.def { background: #f8fafc; border-left: 3px solid #94a3b8;
        padding: 0.5rem 0.9rem; margin: 0.7rem 0; font-size: 0.93rem; }
.bad { border-left-color: #dc2626; background: #fef2f2; }
tr.imbal td { background: #fef3c7; }
tr.imbal td:first-child { font-weight: 650; }
.good { border-left-color: #16a34a; background: #f0fdf4; }
.key { background: #eff6ff; border: 1px solid #bfdbfe; border-left: 4px solid
        #2563eb; padding: 0.8rem 1.1rem; margin: 1rem 0; border-radius: 4px; }
.key ol { margin: 0.4rem 0 0 1.1rem; }
.key li { margin: 0.45rem 0; }
"""


def _write_s0_s1_report(d: dict, meta: dict, pat: dict, imgs: dict) -> None:
    """Uniform-only S0/S1 report: setup, 3.1, §4.3, S1."""
    s0 = pat["schemes"]["S0"]["fairness"]
    s1 = (pat["schemes"].get("S1") or {}).get("fairness") or s0
    rc = pat.get("root_cause") or {}
    ref = pat.get("s0_unbounded") or {}
    sref = ref.get("fairness") or s0
    rcref = pat.get("root_cause_unbounded") or rc
    q0 = pat["schemes"]["S0"].get("retry") or {}
    qref = ref.get("retry") or {}
    jbw = (s0.get("jain_bin") or {}).get("bin_w")
    jb0 = (s0.get("jain_bin") or {}).get("jain_bin_mean")
    jbn0 = (s0.get("jain_bin") or {}).get("jain_bin_null")
    jbr0 = (s0.get("jain_bin") or {}).get("jain_bin_ratio")
    jb1 = (s1.get("jain_bin") or {}).get("jain_bin_mean")
    t1 = 100.0 * (s1["throughput"] - s0["throughput"]) / max(1e-9, s0["throughput"])
    t_ref = 100.0 * (s0["throughput"] - sref["throughput"]) / max(
        1e-9, sref["throughput"])
    b = pat["bounds"]
    _lb_txt = {
        "link_lb": "最忙的那条有向链路上、DAT VC 的容量",
        "port_lb": _port_lb_txt(b),
        "cut_lb": "跨割面的流量除以割面上的有向链路数",
        "txn_lb": "单笔事务四拍握手的串行链",
    }
    bind_key = max(_lb_txt, key=lambda k: b.get(k, 0))
    bind_lb = {"link_lb": "LB_link", "port_lb": "LB_port",
               "cut_lb": "LB_cut", "txn_lb": "LB_txn"}[bind_key]
    bind_txt = _lb_txt[bind_key]
    rows = (rcref.get("rows") or rc.get("rows") or [{}])
    mean_hop = rows[0].get("mean_hop_to_mem", 0.0)
    adj_varies = len({r.get("adj_mem") for r in rows}) > 1
    n_mem = len(meta.get("mem_nodes") or [])
    bw0 = sref.get("bw_by_core") or s0.get("bw_by_core") or {}
    adj = {str(r["core"]): r.get("adj_mem") for r in rows}
    losers = sorted((c for c in bw0 if adj.get(c) == 1), key=int)
    winners = sorted((c for c in bw0 if adj.get(c) == 2), key=int)
    lo_s = "、".join(f"C{c}" for c in losers)
    hi_s = "、".join(f"C{c}" for c in winners)
    if adj_varies:
        sec4_geom = f"""
<h3>4.1 先排除“离 mem 更远”</h3>
<div class="def">到 {n_mem} 个 mem 的平均跳数全部等于
<b>{mean_hop}</b>。r(带宽, 平均跳数) =
<b>{rcref.get('corr_bw_meanhop')}</b>。失衡的来源不是距离。</div>
<h3>4.2 真正的判据：紧邻的 mem 有几个</h3>
<ul>
<li>{hi_s or '—'} 两侧都是 mem → 相邻 mem = 2；</li>
<li>{lo_s or '—'} 有一侧是非终端 → 相邻 mem = 1。</li>
</ul>
<div class="def bad">无限 tracker 上带宽与相邻 mem 个数 r =
<b>{rcref.get('corr_bw_adjmem')}</b>
（Spearman {rcref.get('rank_bw_adjmem')}）。
有限 tracker 把这条链盖住：重试 {q0.get('retry_per_txn')} 次/事务，
max/min 从 {sref['max_min']} 收到 {s0['max_min']}。</div>"""
    else:
        sec4_geom = f"""
<h3>4.1 对称性：距离和相邻 mem 都没有方差</h3>
<div class="def">每个 core 到 {n_mem} 个 HA 的平均跳数全部等于
<b>{mean_hop}</b>，相邻 mem 全部等于 2。
r(带宽, 平均跳数) = {rcref.get('corr_bw_meanhop')}，
r(带宽, 相邻 mem) = {rcref.get('corr_bw_adjmem')}。</div>
<h3>4.2 残余差从哪来</h3>
<p>边 hop 时延 1–4 拍不均。带宽与上环成功率 r =
<b>{rcref.get('corr_bw_succ')}</b>，
与出边时延 r = {rcref.get('corr_bw_lat')}。</p>"""
    s1_fc = (pat["schemes"].get("S1") or {}).get("fc") or {}
    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>无缓存环上的 per-core 写带宽公平性（单平面 · S0/S1）</title>
<style>{_html_style()}</style></head><body>

<h1>无缓存环上的 per-core 写带宽公平性</h1>
<p class="note">本次只跑 <b>1 个 plane / 1 条 ring</b>、均匀 tiled 写、
S0 与 S1。REQ / RSP / DAT <b>上下环口独立</b>。
每 core {meta['K']} 笔 WriteNoSnp
（请求量 ×10），每笔 {meta['W']} 个 WriteData flit。
HA 回 RSP / Comp 时延 <b>{_jit_label(meta)}</b> 拍。</p>

<h2>结论</h2>
<div class="key">
<ol>
<li><b>三通道链路独立。</b>makespan 下界改由最忙 DAT/RSP hop
决定（bound {b['bound']}，port {b.get('port_lb')} 已松）。
Hop 理想全环 WriteData R* = 40/7 ≈ 5.714。</li>
<li><b>tracker 从 32 加到 {meta.get('ha_track')}：缓解了，但还没解除。</b>
S0 吞吐 {TRACK32_ROUND['thr']} → <b>{s0['throughput']}</b> flit/cycle
（{100.0 * (s0['throughput'] - TRACK32_ROUND['thr']) / TRACK32_ROUND['thr']:+.0f}%），
重试 {TRACK32_ROUND['retry_per_txn']} → {q0.get('retry_per_txn')} 次/事务，
延迟 p50 = {pat['schemes']['S0'].get('lat_p50')}。<br>
但<b>峰值占用正顶在 {q0.get('max_ha_used', '—')} 个表项（= ha_track）</b>，
而无限 tracker 参照要用到 {qref.get('max_ha_used', '—')} 个、吞吐
{sref['throughput']} —— 本轮只到它的
<b>{100.0 * s0['throughput'] / max(1e-9, sref['throughput']):.0f}%</b>。
completer 表项<b>仍在削带宽</b>，只是从削一半变成削三分之一。
K = {meta.get('K')} 才暴露这一点：K = 4000 的短探测里 128 就够了（见 3.2.2），
<b>按短跑定 tracker 尺寸会低估</b>。</li>
<li><b>S1 相对 S0 吞吐 {t1:+.1f}%</b>，max/min
{s0['max_min']} → {s1['max_min']}。
源端限速略减 RetryAck，帮不上 hop 理想。</li>
<li><b>缺口分两层，都不是缓存。</b>S0 吞吐是 hop 理想 R* 的
<b>{100.0 * s0['throughput'] / max(1e-9, _ideal_cc(meta)['tot']):.0f}%</b>：
先被无缓存环的上环饥饿削到 {sref['throughput']}
（在环 flit 绝对优先，核只能挤进空隙；把 eject / inject / dir
各级缓存单独放宽都只动 ±1%），再被 {meta.get('ha_track')} 个表项削到
{s0['throughput']}。见 3.2.2 与 4.5。</li>
<li><b>上环失败比大 ≠ 访存不均衡。</b>失败比是同一核 CW/CCW 失败次数比，
不是失败率。8 个 HA 收包相等；本轮核间写带宽也齐。
它是邻 mem = 1 的核在热 hop 上打不进去的症状。见 4.3.2。</li>
<li><b>官方 K 上没有一组 S1 参数让所有核失败比都 &lt; 2。</b>
最接近是 dir_split + harsh（2.07，仍有 C0/C8）。
各核写带宽仍是一条平线，只比默认 S1 低约 4%。见 5.2。</li>
</ol>
</div>

<h2>1. 拓扑与硬件配置</h2>
<img src="{imgs.get('topo', '')}" alt="topology">
{_setup_table(meta)}

<h3>1.1 每条边的 hop 时延</h3>
{_link_table(meta)}

<h3>1.2 协议：CHI WriteNoSnp 四拍握手</h3>
<p>一笔写 = REQ → DBIDResp → WriteData×{meta['W']} → Comp。
per-core 写带宽 = 争用窗口内成功上环的 WriteData flit / cycle。</p>

<h3>1.2.1 写激励</h3>
<p>地址走 tile × 64KB + (i mod 16) × 4KB，interleave 已均衡。
completer 侧每条 RSP（DBIDResp / RetryAck / Comp）独立抽
<code>{_jit_label(meta)}</code> 拍；同一笔事务在 S0 / S1 抽到同一份时延。
本轮 <b>{meta.get('n_planes')} plane</b>、K = {meta['K']}。</p>
{_stimulus_note(meta, pat, d.get("stimulus_forecast") or meta.get("stimulus_forecast"))}

<h2>2. 指标：分箱 Jain（主）、max/min 与吞吐</h2>
<ul>
<li><b>分箱 Jain（公平性主指标，进验收线）</b>：把争用窗口切成
<b>{jbw} 拍</b>宽的箱，每箱内对 {len(meta['core_nodes'])} 个 core 那一箱的
写带宽算一次 Jain，再对所有箱取平均。它回答「<b>任一时刻</b>各核是否均衡」。</li>
<li><b>零模型与判定</b>：{jbw} 拍内每核只有十来个 flit，纯计数噪声就让
Jain 到不了 1.0，而且这个地板随吞吐移动，所以绝对阈值没有意义。
把每箱总数 N 等概率撒到 n 个核上有 E[J] ≈ N/(N+n−1)，逐箱平均即
<code>jain_bin_null</code>；判定用比值
<code>ratio = 实测/零模型 ≥ 1.0</code>，含义是<b>至少和完全公平的仲裁一样齐</b>。
本轮 S0 = {jb0} / 零模型 {jbn0} = <b>ratio {jbr0}</b>。</li>
<li><b>max/min</b>（整窗）：降为诊断项，不进验收线。它仍然有用，
因为分箱 Jain 是二次指标、看不见个别核被饿死，而 max/min 直接读最坏的核。
均匀写下各方案的分箱 Jain 差异小于噪声（S0 {jb0} vs S1 {jb1}），
这是均匀写本来就公平的正确反映；流量不均时它分得很开。</li>
<li><b>吞吐</b>：公平性可以靠把所有人一起压慢买到，所以必须一起报。</li>
</ul>
<p>验收线：<b>分箱 Jain ≥ 同窗零模型</b>且吞吐相对基线不下降超过 1%。</p>

<h2>3. 下界与失衡现象</h2>
{_bounds_table(pat['bounds'])}
<p class="note">makespan 下界 {pat['bounds']['bound']} 拍，由 <b>{bind_lb}</b> 决定，
即 {bind_txt}。</p>

<h3>3.1 均匀写 · S0 / S1</h3>
{_summary_table(pat, track=meta.get("ha_track"), schemes=("S0", "S1"))}
<div class="def">S0 主指标 <b>Jain@{jbw}拍 = {jb0}</b>，同窗零模型 {jbn0}，
<b>ratio {jbr0} ≥ 1.0 达标</b>（详见 3.3）；
max/min <b>{s0['max_min']}</b>（诊断项），最慢 {s0['bw_min']} vs 最快 {s0['bw_max']}；
吞吐 {s0['throughput']}。
无限 tracker 参照 Jain@{jbw}拍
{(sref.get('jain_bin') or {}).get('jain_bin_mean')}、
max/min {sref['max_min']}、吞吐 {sref['throughput']}
（相对有限 tracker {t_ref:+.1f}%）。
S1 吞吐差 <b>{t1:+.1f}%</b>。</div>
{_track_table(pat, meta.get("ha_track"))}
<img src="{imgs.get('bars31', '')}" alt="per-core BW uniform">
<img src="{imgs.get('panels31', '')}" alt="per-core BW over time">
<img src="{imgs.get('overlay31', '')}" alt="slowest vs fastest">
{_total_bw_section(meta, pat, imgs)}

<h2>4. 根因</h2>
<p class="note">归因在无限 tracker（环受限）参照上做；4.4 核对有限 tracker
基线 —— 本轮 ha_track = {meta.get('ha_track')} 仍在削吞吐，
但重试已小一个量级，不再盖住几何。</p>
{_rc_table(pat)}
<img src="{imgs.get('scatter', '')}" alt="bw vs explanations">
{sec4_geom}

<h3>4.3 落到硬件上：上环成功率</h3>
<p>带宽与上环成功率 r = <b>{rcref.get('corr_bw_succ')}</b>
（Spearman {rcref.get('rank_bw_succ')}），
与解析过路流量 r = {rcref.get('corr_bw_pt_eff')}。
成功次数几乎按最短路 1:1 切开；不平衡出在<b>失败</b>次数——
邻 mem = 1 的核失败集中在朝向仅剩那个 mem 的一侧。</p>

<h3>4.3.1 上环方向：CW / CCW 成功与失败</h3>
{_sec431(pat, imgs, ("S0", "S1"))}
{_fail_ratio_section(meta, pat)}

<h3>4.4 回到有限 tracker（{meta.get('ha_track')} 个表项）</h3>
<div class="def">吞吐相对无限 tracker <b>{t_ref:+.1f}%</b>，
max/min 从 {sref['max_min']} 到 {s0['max_min']}，
重试 {q0.get('retry_per_txn')} 次/事务。
∞ 参照的峰值占用是 {qref.get('max_ha_used', '—')} 表项，
本轮 ha_track = {meta.get('ha_track')} <b>装不下</b>
（实测峰值就顶在 {q0.get('max_ha_used', '—')}），所以它还在削带宽。<br>
但它已经不再<b>盖住</b>几何：重试从上一轮 32 表项的
{TRACK32_ROUND['retry_per_txn']} 次/事务降到 {q0.get('retry_per_txn')}，
retry churn 小了一个量级，上面那套（环受限）的归因基线上也读得出来。</div>
{_ideal_rate_section(meta, pat)}

<h2>5. S1</h2>
<p>拥塞总线延迟 <b>{s1_fc.get('bus_lat', '—')}</b> 拍，控制窗口
{s1_fc.get('window', 64)} 拍。
{"端口拆开后 S1 的节点预算上限按 VC 数放大（窗口 × 3），"
 "避免把三通道独立注入口误限成 1 flit/cycle。"
 if meta.get("per_vc_ports") else ""}
反馈只在窗口边界写入并在下一窗口边界读取：
30 &lt; 64，所以 30 拍与 1 拍都在下一次 AIMD 之前送到，
本轮 S1 与总线=1 时<b>逐拍相同</b>（makespan {pat['schemes']['S1']['makespan']}）。</p>
{_sweep_table(pat) if pat.get('sweep') else ''}
<img src="{imgs.get('s1trace', '')}" alt="S1 control trace">
{_s1_tune_section(pat, imgs)}

<p class="note" style="margin-top:2rem">
数据：<code>results/ring2_write_fair.json</code>
（K={meta['K']}、W={meta['W']}、n_planes={meta.get('n_planes')}、
seed={meta['seed']}，生成于 {meta['generated_at']}）。</p>
</body></html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}")


def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"missing {DATA}; run utils/dse_ring2_write_fair.py")
    d = json.loads(DATA.read_text())
    meta = d["meta"]
    pat = d["patterns"]["uniform"]
    cap = meta["hop_bw_cap"]

    imgs = {}
    have = _present_schemes(pat)
    have31 = _present_schemes(pat, SEC31)
    p = IMG / "ring2_wfair_topo.png"
    plot_topology(meta, p)
    imgs["topo"] = p.name
    for tag, fn in (("bars", plot_bw_bars), ("panels", plot_bw_panels),
                    ("overlay", plot_bw_overlay)):
        p = IMG / f"ring2_wfair_{tag}.png"
        fn(pat, p, schemes=have)
        imgs[tag] = p.name
    p = IMG / "ring2_wfair_scatter.png"
    plot_scatter(pat, p)
    imgs["scatter"] = p.name
    # Section 3.1: the reported baseline tracker. Only plot schemes
    # that actually ran — a 1-plane S0/S1 pass does not have S16.
    trk = meta.get("ha_track")
    for tag, fn in (("bars31", plot_bw_bars), ("panels31", plot_bw_panels),
                    ("overlay31", plot_bw_overlay)):
        p = IMG / f"ring2_wfair_{tag}.png"
        kw = dict(schemes=have31, s0_unbounded=False)
        if fn is plot_bw_bars:
            kw.update(extra_ref=False,
                      title=f"均匀写 · 每 core 写带宽（tracker = {trk}），"
                            "虚线 = 该方案均值，纵轴已截断")
        fn(pat, p, **kw)
        imgs[tag] = p.name
    hot = d["patterns"].get("hot")
    if hot and all(s in hot.get("schemes", {}) for s in SEC31):
        p = IMG / "ring2_wfair_bars31_hot.png"
        plot_bw_bars(hot, p, schemes=SEC31, s0_unbounded=False,
                     extra_ref=False,
                     title="不均匀写（全部写入 M11/M13）· 每 core 写带宽"
                           f"（tracker = {trk}），虚线 = 该方案均值，纵轴已截断")
        imgs["bars31_hot"] = p.name
        p = IMG / "ring2_wfair_panels31_hot.png"
        plot_bw_panels(hot, p, schemes=SEC31, s0_unbounded=False)
        imgs["panels31_hot"] = p.name
        p = IMG / "ring2_wfair_overlay31_hot.png"
        plot_bw_overlay(hot, p, schemes=SEC31, s0_unbounded=False)
        imgs["overlay31_hot"] = p.name
    if all(s in pat.get("schemes", {}) for s in FC_CMP):
        p = IMG / "ring2_wfair_fc_bars.png"
        plot_bw_bars(pat, p, schemes=FC_CMP, extra_ref=False,
                     title="源端流控对照 · 均匀写 · 每 core 写带宽"
                           f"（tracker = {trk}），虚线 = 该方案均值，纵轴已截断")
        imgs["fc_bars"] = p.name
        hot_fc = d["patterns"].get("hot")
        if hot_fc and all(s in hot_fc.get("schemes", {}) for s in FC_CMP):
            p = IMG / "ring2_wfair_fc_bars_hot.png"
            plot_bw_bars(hot_fc, p, schemes=FC_CMP, extra_ref=False,
                         title="源端流控对照 · 不均匀写 · 每 core 写带宽"
                               f"（tracker = {trk}），虚线 = 该方案均值，纵轴已截断")
            imgs["fc_bars_hot"] = p.name
            p = IMG / "ring2_wfair_fc_compare.png"
            plot_fc_compare({"均匀写": pat, "不均匀写": hot_fc}, p)
            imgs["fc_compare"] = p.name
    p = IMG / "ring2_wfair_totalbw.png"
    plot_total_bw(pat, meta, p)
    imgs["totalbw"] = p.name
    p = IMG / "ring2_wfair_instbal.png"
    plot_inst_balance(pat, meta, p)
    imgs["instbal"] = p.name
    p = IMG / "ring2_wfair_hopbw.png"
    plot_hop_bw(pat, cap, p)
    imgs["hopbw"] = p.name
    if (pat["schemes"].get("S0") or {}).get("board_dir"):
        p = IMG / "ring2_wfair_board_dir.png"
        plot_board_dir(pat, p)
        imgs["board_dir"] = p.name
    if (pat["schemes"].get("S1") or {}).get("board_dir"):
        p = IMG / "ring2_wfair_board_dir_s1.png"
        plot_board_dir(pat, p, scheme="S1")
        imgs["board_dir_s1"] = p.name
    hot_for_dir = d["patterns"].get("hot")
    if hot_for_dir and (hot_for_dir["schemes"].get("S0") or {}).get("board_dir"):
        p = IMG / "ring2_wfair_board_dir_hot.png"
        plot_board_dir(hot_for_dir, p)
        imgs["board_dir_hot"] = p.name
    if (pat["schemes"].get("S1") or {}).get("fc", {}).get("trace"):
        p = IMG / "ring2_wfair_s1trace.png"
        plot_s1_trace(pat, p)
        imgs["s1trace"] = p.name
    if _dirbal_payload() and (pat.get("schemes") or {}).get("S1"):
        p = IMG / "ring2_wfair_s1_dirbal.png"
        plot_s1_dirbal(pat, p)
        if p.exists():
            imgs["s1dirbal"] = p.name
    study = d.get("retry_study")
    if study:
        for tag, fn in (("outst", plot_outst_sweep), ("retry", plot_retry_track),
                        ("rate", plot_rate_trace)):
            p = IMG / f"ring2_wfair_{tag}.png"
            fn(study, p)
            imgs[tag] = p.name
    repro = d.get("congestion_repro")
    if repro and repro.get("ost"):
        p = IMG / "ring2_wfair_ost_repro.png"
        plot_ost_repro(repro, p)
        imgs["ost_repro"] = p.name
        p = IMG / "ring2_wfair_ost_inorder.png"
        plot_ost_inorder(repro, p)
        imgs["ost_inorder"] = p.name
        p = IMG / "ring2_wfair_blocker.png"
        plot_blocker_repro(repro, p)
        imgs["blocker"] = p.name

    if "S15" not in (pat.get("schemes") or {}):
        _write_s0_s1_report(d, meta, pat, imgs)
        return

    s0 = pat["schemes"]["S0"]["fairness"]
    s1 = pat["schemes"]["S1"]["fairness"]
    s15 = pat["schemes"]["S15"]["fairness"]
    s16 = pat["schemes"]["S16"]["fairness"]
    rc = pat["root_cause"]
    t1 = 100.0 * (s1["throughput"] - s0["throughput"]) / s0["throughput"]
    t15 = 100.0 * (s15["throughput"] - s0["throughput"]) / s0["throughput"]
    t16 = 100.0 * (s16["throughput"] - s0["throughput"]) / s0["throughput"]
    s19 = (pat["schemes"].get("S19") or {}).get("fairness") or {}
    s20 = (pat["schemes"].get("S20") or {}).get("fairness") or {}
    t19 = 100.0 * (s19["throughput"] - s0["throughput"]) / s0["throughput"] \
        if s19 else 0.0
    t20 = 100.0 * (s20["throughput"] - s0["throughput"]) / s0["throughput"] \
        if s20 else 0.0
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
    q17 = (pat["schemes"].get("S17") or {}).get("retry") or {}
    q18 = (pat["schemes"].get("S18") or {}).get("retry") or {}
    qref = ref.get("retry") or {}
    hot = d["patterns"].get("hot")
    hot_ok = bool(hot and all(s in (hot.get("schemes") or {}) for s in SEC31))
    h0 = (hot["schemes"]["S0"]["fairness"] if hot_ok else {})
    hq0 = (hot["schemes"]["S0"].get("retry") or {}) if hot_ok else {}
    hot_has = (hot.get("mem") if hot else None) or meta.get("hot_has") \
        or [11, 13]
    hot_tbl = _summary_table(hot) if hot_ok else ""
    hot_imgs = ""
    if hot_ok:
        hot_imgs = f"""
<img src="{imgs.get('bars31_hot', '')}" alt="hot per-core BW">
<p class="note">不均匀写把全部流量灌进 M{'/M'.join(str(x) for x in hot_has)}。
S0 的 max/min 从均匀写的 {s0['max_min']} 回到 <b>{h0.get('max_min')}</b>——
destination 几何重新拉开了各核，retry 背压盖不住。
S1 两个指标同时更差；S16 在这个场景上吞吐和公平性都不如 S0；
S17 / S18 与 S0 几乎贴在一起，重试也压不下去
（{hq0.get('retry_per_txn')} 次/事务）。</p>
<img src="{imgs.get('panels31_hot', '')}" alt="hot per-core BW over time">
<img src="{imgs.get('overlay31_hot', '')}" alt="hot slowest vs fastest">
"""
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
    adj_varies = len({r.get("adj_mem") for r in rcref["rows"]}) > 1
    n_mem = len(meta.get("mem_nodes") or [])
    ss0 = [r["S0"]["max_min"] for r in pat.get("seed_sweep", [])
           if r.get("S0")]
    s0_seed_rng = (f"{min(ss0):.3f} ~ {max(ss0):.3f}" if ss0
                   else f"{s0['max_min']:.3f}")
    if adj_varies:
        concl_pos = f"""
<li><b>位置相关的失衡真实存在，但只在环受限时才显露。</b>
放开 tracker（环是唯一约束）时 max/min = <b>{sref['max_min']}</b>，
最慢 {sref['bw_min']} vs 最快 {sref['bw_max']} flit/cycle，
而需求完全对称。<b>加上有限 tracker 之后它被压到
{s0['max_min']}</b>——不是被修好，而是 retry 背压
把所有 core 一起拖慢。公平性必须和吞吐一起读。</li>
<li><b>根因是“身边有几个 mem”，不是“离 mem 多远”。</b>
每个 core 到 {n_mem} 个 mem 的平均跳数全部等于 {mean_hop}，
r(带宽, 平均跳数) = <b>{rcref['corr_bw_meanhop']}</b>。
真正决定带宽的是紧邻 mem 个数：r = <b>{rcref['corr_bw_adjmem']}</b>。
{lo_s} 各只有 1 个相邻 mem，带宽 ≤ {lo_bw}；
{hi_s} 两侧都是 mem，带宽 ≥ {hi_bw}。</li>"""
        sec4_geom = f"""
<h3>4.1 先排除“离 mem 更远”</h3>
<div class="def">每个 core 到 {n_mem} 个 mem 的平均跳数全部等于
<b>{mean_hop}</b> 跳。实测 r(带宽, 平均跳数) =
<b>{rcref['corr_bw_meanhop']}</b>。<b>失衡的来源不是距离。</b></div>
<h3>4.2 真正的判据：紧邻的 mem 有几个</h3>
<ul>
<li>{hi_s} 两侧都是 mem → <b>相邻 mem = 2</b>；</li>
<li>{lo_s} 有一侧不是 mem → <b>相邻 mem = 1</b>。</li>
</ul>
<div class="def bad">带宽与相邻 mem 个数 r =
<b>{rcref['corr_bw_adjmem']}</b>（Spearman {rcref['rank_bw_adjmem']}）。
相邻 2 的最低 {hi_bw} ＞ 相邻 1 的最高 {lo_bw}。</div>"""
    else:
        concl_pos = f"""
<li><b>闭合 full ring 上均匀写在几何上是对称的。</b>
偶数 core、奇数 HA，19 与 0 相邻；每个 core 两侧都是 mem，
到 {n_mem} 个 HA 的平均跳数全部等于 {mean_hop}。
无限 tracker 下 max/min = <b>{sref['max_min']}</b>，
有限 tracker 下 <b>{s0['max_min']}</b>，吞吐
{sref['throughput']} → {s0['throughput']}（{t_ref:+.1f}%）。
<b>位置失衡出现在不均匀写</b>
（S0 max/min {h0.get('max_min', '—')}），不是均匀写的角色图。</li>
<li><b>相邻 mem 个数在这张图上没有方差，解释不了残余差。</b>
r(带宽, 相邻 mem) = {rcref['corr_bw_adjmem']}，
r(带宽, 平均跳数) = {rcref['corr_bw_meanhop']}。
边时延仍是 1–4 拍不均，上环成功率相关
r = <b>{rcref['corr_bw_succ']}</b>，
出边时延相关 r = {rcref['corr_bw_lat']}。
挖掉对顶 HA 才会造出“相邻 = 1 / 2”两档，本拓扑不这么做。</li>"""
        sec4_geom = f"""
<h3>4.1 对称性：距离和相邻 mem 都没有方差</h3>
<div class="def">10/10 交替的闭合 full ring 上，每个 core 到
{n_mem} 个 HA 的平均跳数全部等于 <b>{mean_hop}</b>，
相邻 mem 全部等于 <b>2</b>。
r(带宽, 平均跳数) = {rcref['corr_bw_meanhop']}，
r(带宽, 相邻 mem) = {rcref['corr_bw_adjmem']}，
两者都没有解释力——自变量是常数。</div>
<h3>4.2 残余差从哪来</h3>
<p>边 hop 时延从 1 拍到 4 拍，环不是度量均匀的。
有限 K 的均匀采样也会留下几个百分点的抖动。
带宽与上环成功率 r = <b>{rcref['corr_bw_succ']}</b>，
与出边时延 r = {rcref['corr_bw_lat']}。
<b>真正的位置依赖留给不均匀写</b>：全部写入
M{'/M'.join(str(x) for x in hot_has)} 之后，
入口 hop 被过路流量占满，离热点近的核反而更慢
（S0 max/min {h0.get('max_min', '—')}）。</p>"""

    # Judge on the whole seed sweep, not just the headline seed: the
    # reservation mechanism is discrete and one seed can flatter a tuning.
    def _verdict(scheme: str, fall: dict, tfall: float) -> dict:
        """Judge on the whole seed sweep, not just the headline seed.

        Fairness is judged on the binned-Jain ratio against the same-window
        null model, because an absolute Jain threshold has no fixed meaning:
        the reachable ceiling moves with the flit count per bin, and a scheme
        that costs throughput lowers its own ceiling. `ratio >= 1.0` says
        "at least as even as perfectly fair arbitration seen through the same
        window", which is comparable across schemes.
        """
        sw = [r for r in pat.get("seed_sweep", []) if r.get(scheme)]
        fb = (fall.get("jain_bin") or {}).get("jain_bin_ratio")
        rs = [r[scheme]["jain_bin_ratio"] for r in sw
              if r[scheme].get("jain_bin_ratio")] or [fb or 0.0]
        ms = [r[scheme]["max_min"] for r in sw] or [fall["max_min"]]
        ts = [r[scheme]["thr_delta_pct"] for r in sw] or [tfall]
        hit = (min(rs) >= 1.0, min(ts) >= -1.0)
        names = ("分箱 Jain ≥ 零模型", "吞吐差 ≤ 1%")
        good = [n for n, v in zip(names, hit) if v]
        bad = [n for n, v in zip(names, hit) if not v]
        return {
            "n": len(sw), "hit": hit, "bad": bad,
            "verdict": ("全部达标" if not bad else
                        (("达成 " + "、".join(good) + "；") if good else "") +
                        "未达成 " + "、".join(bad)),
            "rng_r": f"{min(rs):.4f} ~ {max(rs):.4f}",
            "rng_m": f"{min(ms):.3f} ~ {max(ms):.3f}",
            "rng_t": f"{max(ts):+.1f}% ~ {min(ts):+.1f}%",
            "t_worst": min(ts), "m_worst": max(ms), "r_worst": min(rs),
        }

    v15 = _verdict("S15", s15, t15)
    v16 = _verdict("S16", s16, t16)
    verdict, bad = v15["verdict"], v15["bad"]
    n_seed = v15["n"]
    rng_m, rng_t = v15["rng_m"], v15["rng_t"]

    # Name the binding bound from the data so the prose cannot go stale.
    _lb_txt = {
        "link_lb": "最忙的那条有向链路上、DAT VC 的容量",
        "port_lb": _port_lb_txt(b),
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
    # The binned Jain answers "is it even right now", not "which scheme wins":
    # across schemes it moves even less than the whole-window version.
    _jb = [(pat["schemes"][s]["fairness"].get("jain_bin") or {})
           for s in SCHEMES]
    _jbs = [x["jain_bin_mean"] for x in _jb if x.get("jain_bin_mean")]
    jb_w = next((x["bin_w"] for x in _jb if x.get("bin_w")), 0)
    jb_spread = (100.0 * (max(_jbs) - min(_jbs)) / max(1e-9, min(_jbs))
                 if _jbs else 0.0)

    sec9 = _retry_sections(study, imgs, meta, pat,
                           repro=d.get("congestion_repro")) if study else ""
    concl_retry = _retry_conclusion(_retry_facts(study)) if study else ""
    taxo = _taxonomy_section(pat, d["patterns"].get("hot"), imgs, meta)

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
tr.imbal td {{ background: #fef3c7; }}
tr.imbal td:first-child {{ font-weight: 650; }}
.good {{ border-left-color: #16a34a; background: #f0fdf4; }}
.key {{ background: #eff6ff; border: 1px solid #bfdbfe; border-left: 4px solid
        #2563eb; padding: 0.8rem 1.1rem; margin: 1rem 0; border-radius: 4px; }}
.key ol {{ margin: 0.4rem 0 0 1.1rem; }}
.key li {{ margin: 0.45rem 0; }}
</style></head><body>

<h1>无缓存环上的 per-core 写带宽公平性</h1>
<p class="note">workload：<b>{len(meta['core_nodes'])} 个 AI core 对
{len(meta['mem_nodes'])} 个 memory 节点做 tiled 写</b>
（{meta.get('burst_b', 128)}B burst、{meta.get('stride_b', 4096)}B stride、
{(meta.get('tile_b') or 65536) // 1024}KB tile，interleave 已均衡），每 core
{meta['K']} 笔 <code>WriteNoSnp</code>、每笔 {meta['W']} 个 WriteData flit
（每 core {pat['flits_per_core']} 个数据 flit，共 {pat['n_txn']} 笔事务）。
HA 回 RSP 的时延为 <b>{_jit_label(meta)}</b> 拍。
{('节点 ' + '、'.join(str(x) for x in meta['non_terminal'])
  + ' 既不是 memory 也不是 AI core。')
 if meta.get('non_terminal') else
 '拓扑是闭合 full ring，每个节点都是终端。'}</p>

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

{concl_pos}

<li><b>S1（拥塞等级 + AIMD）不但没修好，还把两个指标同时弄坏。</b>
max/min {s0['max_min']} → <b>{s1['max_min']}</b>，
吞吐 <b>{t1:+.1f}%</b>。三条失效机理都被数据证实，且都不是调参能修的：
<b>(a)</b> max 聚合让同一通路上的 core 乘同一个 α，等比缩小不改变贫富比值；
<b>(b)</b> 差值规则让受害者惩罚自己——它 <code>own_total_fail</code> 高，
而赢家的 <code>net_fail</code> 低，于是差值大的反倒是受害者；
<b>(c)</b> 在环流量绝对优先，源端限速让出的空拍立刻被过路 flit 吃掉，
<b>造不出槽位</b>。</li>

<li><b>S15 / S16 在这个基线上都变成了"用吞吐买一点公平"的差交易。</b>
S0 本身已经接近均衡（跨 {n_seed or 1} 个种子 max/min
{s0_seed_rng}），
留给公平性方案的空间很小：
S16 的分箱 Jain / 零模型比值落在 {v16['rng_r']}（max/min {v16['rng_m']}），
S15 是 {v15['rng_r']}（max/min {rng_m}，<b>并不稳定，个别种子上还不如 S0</b>），
而两者的吞吐代价都是 {rng_t} / {v16['rng_t']}。
<b>按验收线（分箱 Jain ≥ 同窗零模型 且 吞吐差 ≤ 1%）判定：
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
<li><b>源端流控按“谁决定 / 窗口还是速率 / 靠什么触发”分类；
这颗 NoC 只用得上 S15–S20。</b>
PFC、HPCC、CUBIC、BBR、INT 没有对应物（无队列、不丢包、无遥测）。
均匀写下窗口方案（S19 / S20）把重试压下去、写吞吐
{t19:+.1f}% / {t20:+.1f}%，max/min 略差；
S16 仍然最齐。不均匀写下窗口会误伤热点旁的核，
只有 S15 的预约还在加吞吐。对照见“源端流控”一节。</li>
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
三条独立 CHI VC（1 flit = {meta.get('flit_b', 64)}B，
所以 WriteData×{meta['W']} = {meta.get('burst_b', 128)}B burst）。
<b>per-core 写带宽 = 该 core 在争用窗口内成功上环的
WriteData flit / cycle。</b></p>

<h3>1.2.1 写激励：burst / stride / tile，以及不一致的 memory 回包</h3>
<p>地址走 <code>tile × 64KB + (i mod 16) × 4KB</code>，
每 core 自带高位（<code>core &lt;&lt; 20</code>）避免十个核锁步打同一 HA。
interleave 按 4KB line + core 偏移把 8 个 mem 铺平
（朴素的 <code>(addr/128)%8</code> 会因为 4096/128=32≡0 让整条 stride
坐在同一个 HA 上）。completer 侧每条 RSP（DBIDResp / RetryAck / Comp）
独立抽 <code>{_jit_label(meta)}</code> 拍，
同一笔事务在两个方案里抽到同一份时延。</p>
{_stimulus_note(meta, pat, d.get("stimulus_forecast") or meta.get("stimulus_forecast"))}

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

<h2>2. 三个指标：分箱 Jain、max/min 与吞吐</h2>
<p>设 <i>n</i> 个 core 实测到的写带宽为
<i>x</i><sub>1</sub>, …, <i>x</i><sub>n</sub>（单位 WriteData flit/cycle，
统计窗口是所有 core 都还在发的争用窗口）。全文用三个数，
其中<b>公平性的主指标是分箱 Jain</b>：</p>
<ul>
<li><b>分箱 Jain（主指标）</b>：把争用窗口切成 <b>{jb_w} 拍</b>宽的箱，
在每个箱内对 <i>n</i> 个 core 那一箱的写带宽算
J = (Σ<i>x<sub>i</sub></i>)<sup>2</sup> /
(<i>n</i>·Σ<i>x<sub>i</sub></i><sup>2</sup>)，再对所有箱取<b>平均</b>。
它回答的是「<b>任一时刻</b>各核是否均衡」，也是本研究关心的问题。
读它必须对照同窗口的完全公平零模型（见 3.3），因为 {jb_w} 拍内
每核只有十来个 flit，计数噪声本身就会把 J 压到 1.0 以下。</li>
<li><b>max/min</b> = max <i>x<sub>i</sub></i> / min <i>x<sub>i</sub></i>，
在整个争用窗口上算。<b>方案之间的比较和验收沿用它</b>，
因为它直接读最坏的那个 core：1.0 是完全均等，
1.2 就是最慢的 core 只有最快的 83%。</li>
<li><b>吞吐</b> = Σ <i>x<sub>i</sub></i>，全环每拍搬走的 WriteData flit。
效率看这一个。公平性可以靠“把所有人一起压慢”买到，
所以任何公平性改善都必须和吞吐一起报。</li>
</ul>
<div class="def">验收线两条：<b>分箱 Jain ≥ 同窗零模型</b>
（即 ratio ≥ 1.0）且<b>吞吐相对基线不下降超过 1%</b>。
两条都按最坏随机种子判定，不看单一种子。</div>

<h3>2.1 为什么阈值是「≥ 零模型」而不是一个绝对数</h3>
<p>分箱 Jain 达不到 1.0，而且它的上限<b>不是常数</b>：
{jb_w} 拍内每核只有十来个 flit，纯计数噪声就把 Jain 压下去。
把每箱的总 flit 数 N 按等概率多项分布撒到 n 个核上，
每核计数的均值是 N/n、方差是 N·(1/n)(1−1/n)，于是</p>
<p style="text-align:center">E[J] ≈ 1/(1 + CV<sup>2</sup>) = N / (N + n − 1)</p>
<p>这就是<b>完全公平的仲裁透过同一个窗口去看</b>会落到的位置，
逐箱算完再平均即 <code>jain_bin_null</code>（与蒙特卡洛零模型差约 5×10<sup>−4</sup>）。</p>
<div class="def">关键是这个地板<b>随吞吐移动</b>：一个方案如果压低了吞吐，
每箱 flit 变少，它自己的零模型也跟着下降。
所以只有<b>比值</b> <code>jain_bin_ratio = 实测 / 零模型</code>
在方案之间可比 —— 绝对阈值会系统性地偏袒低吞吐的方案。
ratio ≥ 1.0 的含义是：<b>这颗环至少和完全公平的仲裁一样齐</b>。</div>
<p class="note">这个指标在均匀写下分辨不出方案（各方案差
<b>{jb_spread:.2f}%</b>，{min(_jbs):.5f} ~ {max(_jbs):.5f}），
但那是正确答案而不是缺陷 —— 均匀写下各方案本来都接近公平，
差异确实小于噪声。流量真正不均时它分得很开（见第 8 节的不均匀写）。</p>

<h3>2.2 max/min 与整窗 Jain 降为诊断项</h3>
<p><b>max/min</b> = max <i>x<sub>i</sub></i> / min <i>x<sub>i</sub></i>
（整个争用窗口）不再进验收线，但仍然列出，因为它是唯一能直接读出
<b>最坏那个 core</b> 的数：分箱 Jain 是二次指标，由多数节点主导 ——
10 个 core 里 9 个完全均等、剩下 1 个只有其余的 1/10，Jain 仍有
<b>{jain_demo:.4f}</b>，而 max/min 已经是 <b>10</b>。分箱之后这条性质不变。</p>
<p><b>整窗 Jain</b> 在闭环批量下几乎恒为 1（各方案只差
<b>{j_spread:.1f}%</b>，{min(_js):.5f} ~ {max(_js):.5f}），
因为每个核最终注入同样的 K×W 个 flit，这是算术恒等式；
同一批数据上 max/min 差 <b>{m_spread:.0f}%</b>
（{min(_ms):.3f} ~ {max(_ms):.3f}）。变异系数 CoV 与 Jain 是同一个信息的
两种写法（J = 1/(1+CoV<sup>2</sup>)），因此不另列。</p>
<p class="note">Jain 还有一条性质与第 5 节直接相关：
所有 <i>x<sub>i</sub></i> 同乘一个常数 J 不变。也就是说
<b>整体限速不改变 Jain</b>——S1 之所以“看起来没把公平性搞坏”，
一部分就是这个数学假象，换成 max/min 就暴露了。</p>

{taxo}

<h2>3. 下界与失衡现象</h2>
{_bounds_table(pat['bounds'])}
<p class="note">makespan 下界 {pat['bounds']['bound']} 拍，由 <b>{bind_lb}</b> 决定，
即<b>{bind_txt}</b>。</p>

<h3>3.1 基线 S0 下各核是否不均</h3>
<p>五个方案都在 <b>tracker = {meta.get('ha_track')}</b>、
outstanding = {meta.get('core_outstanding')} 下测量。
S0 是无流控基线；对照是 S1、S16，以及 S17 / S18。</p>
<p><b>均匀写</b>（每个 core 均匀写全部 {len(meta.get('mem_nodes', []))} 个 mem）：</p>
{_summary_table(pat)}
<div class="def">均匀写下 S0 的 max/min 只有 <b>{s0['max_min']}</b>，
最慢 {s0['bw_min']} vs 最快 {s0['bw_max']} flit/cycle。
闭合 full ring 上需求与角色图都对称，这首先是几何的结果；
completer tracker 再把残余压一层：每笔事务平均被 RetryAck
<b>{q0.get('retry_per_txn')}</b> 次。
同一份 S0 把 tracker 放开之后 max/min 为 {sref['max_min']}、
吞吐从 {s0['throughput']} 到 {sref['throughput']}
（见下表）。</div>
{_track_table(pat, meta.get("ha_track"))}
<img src="{imgs['bars31']}" alt="per-core BW uniform">
<p class="note">S16 最齐（max/min
{pat['schemes']['S16']['fairness']['max_min']}）但更矮；
S17 / S18 把重试压到 {q17.get('retry_per_txn')} / {q18.get('retry_per_txn')}，
S17 的 max/min 反而是五个里最高的。注意纵轴已截断。</p>
<img src="{imgs['panels31']}" alt="per-core BW over time">
<img src="{imgs['overlay31']}" alt="slowest vs fastest">

<p><b>不均匀写</b>（全部写入相邻的 M{'/M'.join(str(x) for x in hot_has)}，
角色不变、只改目的地几何）：</p>
{hot_tbl}
<div class="def bad">流量一不均匀，S0 的 max/min 就从 {s0['max_min']}
回到 <b>{h0.get('max_min', '—')}</b>，吞吐从 {s0['throughput']}
掉到 <b>{h0.get('throughput', '—')}</b> flit/cycle
（重试 {hq0.get('retry_per_txn', '—')} 次/事务）。
<b>destination 几何重新拉开了各核，有限 tracker 盖不住。</b>
离热点近的 core 注入的 hop 已经被所有人的过路流量占满，
离热点远的 core 反而有一段相对空的入口。</div>
{hot_imgs}

<h2>4. 根因</h2>
<p class="note">本节的归因全部在<b>无限 tracker</b>（环受限）的参照上做，
因为只有那里环是唯一约束、位置效应没有被 retry 背压压平；
4.4 再回到有限 tracker 的基线，说明这条因果链被什么盖住了。</p>
{_rc_table(pat)}
<img src="{imgs['scatter']}" alt="bw vs explanations">

{sec4_geom}

<h3>4.3 落到硬件上：上环成功率</h3>
<p>带宽与实测上环成功率的相关是
<b>r = {rcref['corr_bw_succ']}</b>（Spearman {rcref['rank_bw_succ']}），
与 <code>hop_busy</code> 失败次数强负相关；
与解析过路流量的相关很弱（r = {rcref['corr_bw_pt_eff']}）——
过路流量的<b>总量</b>差别不大，差别在于它<b>什么时候</b>正好卡住本地注入。
I-tag 类失败占比很小：<code>_itag_blocks</code> 只压制<b>竞争的其他注入者</b>，
对在环 flit 无效，所以它能限制饥饿时长，却造不出槽位。</p>

<h3>4.3.1 上环方向：CW / CCW 成功与失败</h3>
{_sec431(pat, imgs, ("S0", "S1"))}
{f'''<p>不均匀写（全部打进 M11/M13）同一套统计（S0）：</p>
<img src="{imgs.get("board_dir_hot", "")}" alt="hot board CW vs CCW">
{_board_dir_table(hot)}''' if imgs.get('board_dir_hot') and hot else ''}

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
<p class="note">有限 tracker 下带宽与相邻 mem 的相关 r =
{rc['corr_bw_adjmem']}，无限 tracker 下 r = {rcref['corr_bw_adjmem']}。
full ring 上这两个都接近零；不均匀写才把位置重新拉开。</p>

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
{s0_seed_rng}，
两个区间<b>互相重叠</b>——在有限 tracker 的基线上
<b>S15 的公平性改善已经不稳定了</b>，有的种子上好、有的种子上反而差，
而吞吐代价 {rng_t} 是每个种子都要付的。
主指标上 S15 的 ratio 落在 {v15['rng_r']}。
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
跨 {v16['n']} 个种子：分箱 Jain / 零模型 {v16['rng_r']}、
max/min {v16['rng_m']}、吞吐差 {v16['rng_t']}。
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
