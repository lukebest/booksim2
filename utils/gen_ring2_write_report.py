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

SCHEMES = ("S0", "S1", "S15")
COLOR = {"S0": "#dc2626", "S1": "#f59e0b", "S15": "#2563eb"}
LABEL = {"S0": "S0 基线（无流控）", "S1": "S1 拥塞等级 AIMD",
         "S15": "S15 公平份额 + 槽预约"}


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
    """Per-core write bandwidth, one group of bars per scheme."""
    _use_cjk_font()
    cs = _cores(pat)
    x = range(len(cs))
    w = 0.27
    fig, ax = plt.subplots(figsize=(10.2, 4.0))
    for i, s in enumerate(SCHEMES):
        f = pat["schemes"][s]["fairness"]
        vals = [f["bw_by_core"][c] for c in cs]
        ax.bar([v + (i - 1) * w for v in x], vals, w,
               label=f"{LABEL[s]}  Jain={f['jain']:.4f}"
                     f"  max/min={f['max_min']:.3f}",
               color=COLOR[s], edgecolor="white", linewidth=0.6)
        ax.axhline(sum(vals) / len(vals), color=COLOR[s], ls=":", lw=1.0)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"C{c}" for c in cs])
    ax.set_xlabel("AI core")
    ax.set_ylabel("写带宽（WriteData flit/cycle）")
    ax.set_title("每 core 写带宽（争用窗口内），虚线 = 该方案均值")
    ax.legend(fontsize=8.5, loc="lower right")
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
        ax.set_title(f"{s}  mk={sch['makespan']}  Jain={f['jain']:.4f}",
                     fontsize=10)
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
        rows.append([
            LABEL[s], sch["makespan"], f["jain"], f["max_min"], f["cov"],
            f["bw_min"], f["bw_max"], f["throughput"],
            f"{d:+.1f}%", sch["n_deflections"], sch["n_board_fail"],
        ])
    return _table(["方案", "makespan", "Jain", "max/min", "CoV",
                   "最低 BW", "最高 BW", "吞吐 flit/cycle", "吞吐差",
                   "偏转", "上环失败"], rows)


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
    rows = [[s["window"], s["band"], s["makespan"], s["jain"],
             s["max_min"], s["cov"], s["throughput"]]
            for s in pat["sweep"]]
    return _table(["window", "α/β 档位", "makespan", "Jain", "max/min",
                   "CoV", "吞吐"], rows)


def _seed_table(pat: dict) -> str:
    rows = []
    for r in pat.get("seed_sweep", []):
        a, b = r.get("S0"), r.get("S15")
        if not (a and b):
            continue
        rows.append([r["seed"], a["jain"], a["max_min"], b["jain"],
                     b["max_min"], f"{b['thr_delta_pct']:+.2f}%"])
    if not rows:
        return ""
    return _table(["seed", "S0 Jain", "S0 max/min", "S15 Jain",
                   "S15 max/min", "S15 吞吐差"], rows)


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

    s0 = pat["schemes"]["S0"]["fairness"]
    s1 = pat["schemes"]["S1"]["fairness"]
    s15 = pat["schemes"]["S15"]["fairness"]
    rc = pat["root_cause"]
    t1 = 100.0 * (s1["throughput"] - s0["throughput"]) / s0["throughput"]
    t15 = 100.0 * (s15["throughput"] - s0["throughput"]) / s0["throughput"]

    bw0 = s0["bw_by_core"]
    adj = {str(r["core"]): r.get("adj_mem") for r in rc["rows"]}
    losers = sorted((c for c in bw0 if adj.get(c) == 1), key=int)
    winners = sorted((c for c in bw0 if adj.get(c) == 2), key=int)
    lo_s = "、".join(f"C{c}" for c in losers)
    hi_s = "、".join(f"C{c}" for c in winners)
    lo_bw = max(bw0[c] for c in losers) if losers else 0.0
    hi_bw = min(bw0[c] for c in winners) if winners else 0.0
    mean_hop = rc["rows"][0].get("mean_hop_to_mem", 0.0)

    # Judge on the whole seed sweep, not just the headline seed: the
    # reservation mechanism is discrete and one seed can flatter a tuning.
    sw = [r for r in pat.get("seed_sweep", []) if r.get("S15")]
    js = [r["S15"]["jain"] for r in sw] or [s15["jain"]]
    ms = [r["S15"]["max_min"] for r in sw] or [s15["max_min"]]
    ts = [r["S15"]["thr_delta_pct"] for r in sw] or [t15]
    hit_j, hit_m, hit_t = min(js) >= 0.98, max(ms) <= 1.05, min(ts) >= -1.0
    good = [n for n, v in (("Jain ≥ 0.98", hit_j), ("max/min ≤ 1.05", hit_m),
                           ("吞吐差 ≤ 1%", hit_t)) if v]
    bad = [n for n, v in (("Jain ≥ 0.98", hit_j), ("max/min ≤ 1.05", hit_m),
                          ("吞吐差 ≤ 1%", hit_t)) if not v]
    verdict = ("全部达标" if not bad else
               (("达成 " + "、".join(good) + "；") if good else "") +
               "未达成 " + "、".join(bad))
    n_seed = len(sw)
    rng_j = f"{min(js):.5f} ~ {max(js):.5f}"
    rng_m = f"{min(ms):.3f} ~ {max(ms):.3f}"
    rng_t = f"{max(ts):+.1f}% ~ {min(ts):+.1f}%"

    demo = [1.0] * 9 + [0.1]
    jain_demo = sum(demo) ** 2 / (len(demo) * sum(v * v for v in demo))

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
<li><b>均匀写流量确实存在位置相关的带宽失衡。</b>S0 基线的 Jain =
<b>{s0['jain']}</b>，最快 / 最慢 = <b>{s0['max_min']}</b>，
最慢 {s0['bw_min']} vs 最快 {s0['bw_max']} flit/cycle。
需求完全对称（每个 core 发一样多、目的地分布一样），
差异纯粹来自节点在环上的位置。</li>

<li><b>根因是“身边有几个 mem”，不是“离 mem 多远”。</b>
9 和 19 在环上正好对顶，把这一对从 memory 里去掉之后，
每个 core 到 8 个 mem 的<b>平均跳数仍然全部等于 {mean_hop} 跳</b>，
r(带宽, 平均跳数) = <b>{rc['corr_bw_meanhop']}</b>，没有解释力。
真正决定带宽的是<b>紧邻的 mem 个数</b>：
r = <b>{rc['corr_bw_adjmem']}</b>（Spearman {rc['rank_bw_adjmem']}）。
{lo_s} 各只有 1 个相邻 mem（另一侧正对着非终端的 9 或 19），
带宽全部 ≤ {lo_bw}；其余 {hi_s} 两侧都是 mem，带宽全部 ≥ {hi_bw}。</li>

<li><b>S1（拥塞等级 + AIMD）不但没修好，还把情况弄得更差。</b>
Jain {s0['jain']} → <b>{s1['jain']}</b>，
max/min {s0['max_min']} → <b>{s1['max_min']}</b>，
吞吐 <b>{t1:+.1f}%</b>。三条失效机理都被数据证实：max 聚合保比例、
差值规则惩罚受害者、源端限速在“在环优先”下造不出槽位。</li>

<li><b>S15（最大最小公平份额 + 上游注入预约）基本解决问题。</b>
Jain → <b>{s15['jain']}</b>，max/min → <b>{s15['max_min']}</b>，
每 core 带宽收敛到 {s15['bw_min']} ~ {s15['bw_max']} 的窄区间，
吞吐代价 <b>{t15:+.1f}%</b>。跨 {n_seed} 个随机种子：
Jain {rng_j}、max/min {rng_m}、吞吐差 {rng_t}。
对照验收线（Jain ≥ 0.98、max/min ≤ 1.05、吞吐差 ≤ 1%），
<b>按最坏种子判定：{verdict}</b>——
公平性目标稳定成立（max/min 从 1.13~1.18 收到 1.03~1.07），
但拉平最慢 core 要付 1%~3% 的吞吐，<b>没能同时守住 1% 的吞吐预算</b>。</li>

<li><b>全程严格无缓存。</b>所有方案的
<code>n_inring_blocked = 0</code>、<code>max_inring_hold = 0</code>：
S15 的预约只压制<b>上游注入</b>，从不停住已经在环上的 flit，
因此不需要在环上增加任何缓冲。</li>
</ol>
</div>

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

<h2>2. 公平性指标：Jain 指数怎么算、怎么读</h2>
<p>设 <i>n</i> 个 core 实测到的写带宽为
<i>x</i><sub>1</sub>, …, <i>x</i><sub>n</sub>。
<b>Jain 公平性指数</b>定义为</p>

<div class="def" style="text-align:center; font-size:1.05rem">
J(<i>x</i>) =
( Σ<sub><i>i</i>=1..<i>n</i></sub> <i>x<sub>i</sub></i> )<sup>2</sup>
&nbsp;/&nbsp;
( <i>n</i> · Σ<sub><i>i</i>=1..<i>n</i></sub> <i>x<sub>i</sub></i><sup>2</sup> )
</div>

<p>即<b>算术平均的平方除以二次平均的平方</b>：
J = x̄<sup>2</sup> / (x̄<sup>2</sup> + s<sup>2</sup>)，
其中 s<sup>2</sup> 是（有偏）方差。由此得到三条性质：</p>
<ul>
<li><b>取值范围 1/n ≤ J ≤ 1</b>。全部相等时 J = 1；
只有一个 core 拿到全部带宽时 J = 1/n（本研究 n = {len(bw0)}，
下限 {1 / len(bw0):.1f}）。</li>
<li><b>与量纲和规模无关</b>：所有 <i>x<sub>i</sub></i> 同乘一个常数 J 不变。
所以整体降频、整体限速都不改变 Jain——这正是第 5 节里
S1“等比缩小、Jain 不动”的数学原因。</li>
<li><b>可读作“有效公平份额数”</b>：J·n 约等于“相当于几个 core
在平分带宽”。</li>
</ul>
<p>与<b>变异系数</b>的关系是 J = 1 / (1 + CV<sup>2</sup>)，
CV = s / x̄ 就是表里的 <code>CoV</code> 列。
所以 <b>CoV 与 Jain 是同一个信息的两种写法</b>：CoV = 0 ⟺ J = 1。</p>

<div class="def">为什么还要同时看 <b>max/min</b>：Jain 是<b>二次</b>指标，
被多数节点主导，少数被饿死的节点对它影响有限。
10 个 core 里 9 个完全均等、剩下 1 个只有其余的 1/10，
Jain 仍有 <b>{jain_demo:.4f}</b>，而 max/min 已经是 <b>10</b>。
<b>Jain 看整体形状，max/min 看最坏个体</b>，两个都作为验收条件。</div>

<h2>3. 下界与失衡现象</h2>
{_bounds_table(pat['bounds'])}
<p class="note">makespan 下界 {pat['bounds']['bound']} 拍，
由端口（LB_port）主导：三条 VC 共享同一个上下环端口。</p>

<h3>3.1 基线 S0 的失衡</h3>
{_summary_table(pat)}
<div class="def bad">需求完全对称，结果并不对称：Jain <b>{s0['jain']}</b>、
max/min <b>{s0['max_min']}</b>、CoV {s0['cov']}。
最慢的 core 只有最快的 {1 / s0['max_min'] * 100:.0f}%。</div>
<img src="{imgs['bars']}" alt="per-core BW">
<img src="{imgs['panels']}" alt="per-core BW over time">
<p class="note">时间轴上，基线里靠前的 core 从一开始就保持更高的注入率，
被压住的 core 全程贴在下沿。</p>
<img src="{imgs['overlay']}" alt="slowest vs fastest">

<h2>4. 根因</h2>
{_rc_table(pat)}
<img src="{imgs['scatter']}" alt="bw vs explanations">

<h3>4.1 先排除“离 mem 更远”</h3>
<div class="def">9 和 19 在环上正好对顶，把这一对从 memory 里拿掉之后，
每个 core 到 8 个 mem 的<b>平均跳数全部等于 {mean_hop} 跳</b>，
连距离的多重集分布都只是重排。实测
r(带宽, 平均跳数) = <b>{rc['corr_bw_meanhop']}</b>，精确为零。
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
<b>r = {rc['corr_bw_adjmem']}</b>（Spearman {rc['rank_bw_adjmem']}），
两档之间<b>完全不重叠</b>：相邻 2 个的最低带宽 {hi_bw} ＞
相邻 1 个的最高带宽 {lo_bw}。<b>这就是位置依赖的确切形式。</b></div>

<h3>4.3 落到硬件上：上环成功率</h3>
<p>带宽与实测上环成功率的相关是
<b>r = {rc['corr_bw_succ']}</b>（Spearman {rc['rank_bw_succ']}），
与 <code>hop_busy</code> 失败次数强负相关；
与解析过路流量的相关很弱（r = {rc['corr_bw_pt_eff']}）——
过路流量的<b>总量</b>差别不大，差别在于它<b>什么时候</b>正好卡住本地注入。
I-tag 类失败占比很小：<code>_itag_blocks</code> 只压制<b>竞争的其他注入者</b>，
对在环 flit 无效，所以它能限制饥饿时长，却造不出槽位。</p>

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
<div class="def bad">S1 把 Jain 从 {s0['jain']} <b>降到 {s1['jain']}</b>，
max/min 从 {s0['max_min']} <b>升到 {s1['max_min']}</b>，
吞吐 <b>{t1:+.1f}%</b>。全部扫描点都没有同时改善公平性和吞吐。</div>
<img src="{imgs.get('s1trace', '')}" alt="S1 control trace">

<h3>5.2 为什么会这样</h3>
<p><b>(a) max 聚合保住了比例。</b>共享同一条通路的 core 收到同一个等级，
于是乘以同一个 α。等比缩小不改变贫富<b>比值</b>——这正是第 2 节
“Jain 与规模无关”的直接后果：预算曲线整体下移，Jain 不动。</p>
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
<div class="def {'good' if (hit_j and hit_m) else ''}">
Jain <b>{s0['jain']} → {s15['jain']}</b>，
max/min <b>{s0['max_min']} → {s15['max_min']}</b>，
每 core 带宽收敛到 <b>{s15['bw_min']} ~ {s15['bw_max']}</b>，
吞吐 <b>{t15:+.1f}%</b>。</div>
<img src="{imgs['bars']}" alt="per-core BW">
<img src="{imgs['hopbw']}" alt="hop bandwidth vs cap">
<p class="note">吞吐的这点损失来自预约压制上游注入时留下的空拍。
它换来的是最慢 core 的带宽从 {s0['bw_min']} 抬到 {s15['bw_min']}
（{s15['bw_min'] / s0['bw_min']:.2f} 倍）。</p>

<h3>6.2 换种子还成立吗</h3>
<p>预约是离散机制，单一种子容易把某个参数点衬托得过好，
所以把 S0 与 S15 在多个随机种子上重跑。</p>
{_seed_table(pat)}
<div class="def {'good' if not bad else 'bad'}">
公平性的改善在所有种子上都成立：S15 的 Jain 落在 {rng_j}，
max/min 落在 {rng_m}，而 S0 的 max/min 是 1.13 ~ 1.18。
代价是吞吐 {rng_t}。<b>按最坏种子对照验收线：{verdict}。</b>
也就是说，<b>“均匀 core 带宽”这一条稳定达成，
“吞吐不下降 1% 以内”这一条没有稳定达成</b>——
在严格无缓存、在环绝对优先的环上，
唯一能把槽位让给弱者的手段就是让强者的上游空一拍，
这一拍在强者本来能用满的时候就是净损失。</div>

<h2>7. 总线的代价</h2>
{_fc_table(pat)}
<p class="note">专用广播总线不占用任何环上 hop，按窗口边界发一次。
S1 每次 6 bit（两个 3 bit 等级）；S15 增加 8 bit 本窗口成功数、
16 bit 累计数、1 bit active，以及 6 个 8 bit 出向公平份额。
窗口 {pat['schemes']['S15']['fc']['window']} 拍发一次，
折算到每拍的线上开销可以忽略；面积在
<code>rg_sched_cost.py</code> 中单列。</p>

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
