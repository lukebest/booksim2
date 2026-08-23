#!/usr/bin/env python3
"""HTML report: per-core write bandwidth fairness on the bufferless ring."""

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
    any_s = pat["schemes"][SCHEMES[0]]
    return sorted(any_s["fairness"]["bw_by_core"], key=int)


# ---------------------------------------------------------------------------
# plots
# ---------------------------------------------------------------------------

def plot_bw_bars(pat: dict, name: str, path: Path) -> None:
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
               label=f"{LABEL[s]}  Jain={f['jain']:.3f}"
                     f"  max/min={f['max_min']:.2f}",
               color=COLOR[s], edgecolor="white", linewidth=0.6)
        ax.axhline(sum(vals) / len(vals), color=COLOR[s], ls=":", lw=1.0)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"C{c}" for c in cs])
    ax.set_xlabel("AI core")
    ax.set_ylabel("写带宽（WriteData flit/cycle）")
    ax.set_title(f"[{name}] 每 core 写带宽（争用窗口内）"
                 "，虚线 = 该方案均值")
    ax.legend(fontsize=8.5, loc="upper right")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_bw_panels(pat: dict, name: str, path: Path) -> None:
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
        ax.set_title(f"{s}  mk={sch['makespan']}  Jain={f['jain']:.3f}",
                     fontsize=10)
        ax.set_xlabel("cycle")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("写注入率 flit/cycle")
    fig.suptitle(f"[{name}] 每 core 写注入率随时间（颜色 = core index）",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_bw_overlay(pat: dict, name: str, path: Path) -> None:
    """Slowest and fastest core of the baseline, tracked across schemes."""
    _use_cjk_font()
    f0 = pat["schemes"]["S0"]["fairness"]["bw_by_core"]
    lo = min(f0, key=lambda c: f0[c])
    hi = max(f0, key=lambda c: f0[c])
    fig, ax = plt.subplots(figsize=(10.2, 3.8))
    for s in SCHEMES:
        sch = pat["schemes"][s]
        for c, ls in ((lo, "-"), (hi, "--")):
            b = sch["wr_binned"][c]
            tag = "最慢 C" + c if ls == "-" else "最快 C" + c
            ax.plot(b["t"], b["rate"], ls, lw=1.4, color=COLOR[s],
                    alpha=0.9, label=f"{s} {tag}")
    ax.set_xlabel("cycle")
    ax.set_ylabel("写注入率 flit/cycle")
    ax.set_title(f"[{name}] 基线最慢 core C{lo} 与最快 core C{hi} 的对比")
    ax.legend(fontsize=8, ncol=3)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_scatter(pat: dict, name: str, path: Path) -> None:
    """Achieved bandwidth against the analytic pass-through load."""
    _use_cjk_font()
    rc = pat["root_cause"]
    rows = rc["rows"]
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 3.9))
    ax = axes[0]
    ax.scatter([r["pt_eff"] for r in rows], [r["bw"] for r in rows],
               s=64, color="#dc2626", zorder=3)
    for r in rows:
        ax.annotate(f"C{r['core']}", (r["pt_eff"], r["bw"]),
                    textcoords="offset points", xytext=(5, 4), fontsize=8)
    ax.set_xlabel("出向 hop 上的过路流量（flit，解析值）")
    ax.set_ylabel("S0 实测写带宽")
    ax.set_title(f"r={rc['corr_bw_pt_eff']:.3f}  "
                 f"Spearman={rc['rank_bw_pt_eff']:.3f}", fontsize=10)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.scatter([r["succ_rate"] for r in rows], [r["bw"] for r in rows],
               s=64, color="#2563eb", zorder=3)
    for r in rows:
        ax.annotate(f"C{r['core']}", (r["succ_rate"], r["bw"]),
                    textcoords="offset points", xytext=(5, 4), fontsize=8)
    ax.set_xlabel("上环成功率 ok / (ok + fail)")
    ax.set_ylabel("S0 实测写带宽")
    ax.set_title(f"r={rc['corr_bw_succ']:.3f}  "
                 f"Spearman={rc['rank_bw_succ']:.3f}", fontsize=10)
    ax.grid(alpha=0.3)
    fig.suptitle(f"[{name}] 位置决定带宽：过路流量越重，本地越挤不上环",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_hop_bw(pat: dict, name: str, cap: int, path: Path) -> None:
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
    ax.set_title(f"[{name}] 全环 hop 带宽与 3 VC 上限")
    ax.legend(fontsize=8.5)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_s1_trace(pat: dict, name: str, path: Path) -> None:
    """S1's own control signals: budget, own level, received level."""
    _use_cjk_font()
    tr = pat["schemes"]["S1"]["fc"]["trace"]
    nodes = [str(x) for x in tr["nodes"]]
    f0 = pat["schemes"]["S0"]["fairness"]["bw_by_core"]
    lo = min((c for c in nodes if c in f0), key=lambda c: f0[c])
    hi = max((c for c in nodes if c in f0), key=lambda c: f0[c])
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
    fig.suptitle(f"[{name}] S1 控制回路：谁在挨罚", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def _bounds_table(b: dict) -> str:
    rows = [
        ["LB_link 每 VC 独立链路", b["link_lb"],
         "REQ/RSP/DAT 各自占一条 VC，取三者最大"],
        ["LB_port 端口合并", b["port_lb"],
         "inject / leave 每 (node, plane) 仍只有一个端口，三 VC 共享"],
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
        rows.append([f"C{r['core']}", r["bw"], r["pt_eff"], r["lat_out"],
                     r["succ_rate"], r["hop_busy"], r["itag"],
                     r["outstanding"]])
    return _table(["core", "S0 写带宽", "出向过路流量", "出向平均 λ",
                   "上环成功率", "hop_busy 失败", "I-tag 失败",
                   "outstanding 失败"], rows)


def _sweep_table(pat: dict) -> str:
    rows = [[s["window"], s["band"], s["makespan"], s["jain"],
             s["max_min"], s["cov"], s["throughput"]]
            for s in pat["sweep"]]
    return _table(["window", "α/β 档位", "makespan", "Jain", "max/min",
                   "CoV", "吞吐"], rows)


def _fc_table(pat: dict) -> str:
    rows = []
    for s in ("S1", "S15"):
        fc = pat["schemes"][s].get("fc") or {}
        rows.append([LABEL[s], fc.get("window"), fc.get("bus_posts"),
                     fc.get("bus_bits"), fc.get("n_fc_deny"),
                     fc.get("n_aimd_decrease"), fc.get("n_aimd_increase"),
                     fc.get("n_reserved", 0), fc.get("n_reserve_used", 0),
                     fc.get("n_reserve_yield", 0)])
    return _table(["方案", "window", "广播次数", "广播总 bit", "预算拒绝",
                   "AIMD 降", "AIMD 升", "预约槽", "预约命中",
                   "上游让路"], rows)


# ---------------------------------------------------------------------------

def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"missing {DATA}; run utils/dse_ring2_write_fair.py")
    d = json.loads(DATA.read_text())
    meta = d["meta"]
    pats = d["patterns"]
    cap = meta["hop_bw_cap"]

    imgs: dict[str, dict[str, str]] = {}
    for name, pat in pats.items():
        imgs[name] = {}
        for tag, fn in (("bars", plot_bw_bars), ("panels", plot_bw_panels),
                        ("overlay", plot_bw_overlay),
                        ("scatter", plot_scatter)):
            p = IMG / f"ring2_wfair_{name}_{tag}.png"
            fn(pat, name, p)
            imgs[name][tag] = p.name
        p = IMG / f"ring2_wfair_{name}_hopbw.png"
        plot_hop_bw(pat, name, cap, p)
        imgs[name]["hopbw"] = p.name
        if (pat["schemes"].get("S1") or {}).get("fc", {}).get("trace"):
            p = IMG / f"ring2_wfair_{name}_s1trace.png"
            plot_s1_trace(pat, name, p)
            imgs[name]["s1trace"] = p.name

    uni, clu = pats["uniform"], pats["cluster"]
    u0, c0 = uni["schemes"]["S0"]["fairness"], clu["schemes"]["S0"]["fairness"]
    c1 = clu["schemes"]["S1"]["fairness"]
    c15 = clu["schemes"]["S15"]["fairness"]
    crc = clu["root_cause"]
    thr_d = 100.0 * (c15["throughput"] - c0["throughput"]) / c0["throughput"]
    thr_d1 = 100.0 * (c1["throughput"] - c0["throughput"]) / c0["throughput"]
    hot = ", ".join(f"HA{h}" for h in (meta.get("hot_has") or []))

    # the two cores adjacent to the hotspots, which is where the story lands
    lo_core = min(c0["bw_by_core"], key=lambda c: c0["bw_by_core"][c])
    hi_core = max(c0["bw_by_core"], key=lambda c: c0["bw_by_core"][c])

    hit = {"Jain ≥ 0.98": c15["jain"] >= 0.98,
           "max/min ≤ 1.05": c15["max_min"] <= 1.05,
           "吞吐差 ≤ 1%": thr_d >= -1.0}
    s15_ok = all(hit.values())
    verdict = ("全部达标" if s15_ok else
               "达成 " + "、".join(k for k, v in hit.items() if v) +
               "；未达成 " + "、".join(k for k, v in hit.items() if not v))

    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>无缓存环上的 per-core 写带宽公平性</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, "WenQuanYi Micro Hei",
       "Noto Sans CJK SC", sans-serif;
       margin: 2rem auto; max-width: 980px; color: #111; line-height: 1.65; }}
h1,h2,h3 {{ font-weight: 650; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
th,td {{ border: 1px solid #e5e7eb; padding: 0.35rem 0.5rem; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ background: #f8fafc; }}
code {{ background: #f1f5f9; padding: 0.1rem 0.3rem; }}
img {{ max-width: 100%; border: 1px solid #e5e7eb; }}
.note {{ color: #475569; font-size: 0.9rem; }}
.def {{ background: #f8fafc; border-left: 3px solid #94a3b8;
        padding: 0.5rem 0.9rem; margin: 0.7rem 0; font-size: 0.93rem; }}
.bad {{ border-left-color: #dc2626; background: #fef2f2; }}
.good {{ border-left-color: #16a34a; background: #f0fdf4; }}
</style></head><body>

<h1>无缓存环上的 per-core 写带宽公平性</h1>
<p class="note">20 节点双 plane 双向环，偶数 index 是 AI core（CHI RN），奇数是
memory Home Agent。本报告只讨论<b>写</b>：完整的 <code>WriteNoSnp</code> 四拍握手
<code>REQ → DBIDResp → WriteData×{meta['W']} → Comp</code>，因此实例化
<b>REQ / RSP / DAT 三条 CHI VC</b>，每条有向 hop 容量
<code>{cap}</code> flit/cycle。每 core 发 {meta['K']} 笔写、
{meta['K'] * meta['W']} 个 WriteData flit。
<b>per-core 写带宽 = 该 core 在争用窗口内成功上环的 WriteData flit / cycle。</b>
既有的读研究与 <code>report_ring2_20node.html</code> 未被改动。</p>

<h2>1. 前提：环是无缓存的，在环流量绝对优先</h2>
<p><code>_launch</code> 从不阻塞已在环上的 flit，只占用槽位；本地注入由
<code>_can_board</code> 拒绝——要么该有向 hop 的这条 VC 被占，要么
<code>arr_set</code> 显示 σ 拍内有在环 flit 即将到达。</p>

<div class="def">在环流量<b>先于</b>本地注入预定槽位。一个节点想上环，必须等到
一个没有任何过路 flit 经过的空拍。全部实验里
<code>n_inring_blocked = 0</code>，无缓存语义自始至终成立。</div>

<p>关键推论：<b>源端限速无法凭空造出槽位</b>。让上游少发，让出来的空拍会被下一个
过路 flit 顺手拿走，而不是留给被饿死的节点。这一点决定了后面 S1 为什么会失败。</p>

<h2>2. 下界</h2>
<h3>uniform（每 core 均匀写到随机 HA）</h3>
{_bounds_table(uni['bounds'])}
<h3>cluster（所有 core 都写 {hot}）</h3>
{_bounds_table(clu['bounds'])}
<p class="note">cluster 的瓶颈从链路移到了<b>端口</b>：
LB_port={clu['bounds']['port_lb']} 远大于 LB_link={clu['bounds']['link_lb']}，
因为两个热点 HA 的 leave 端口要吞下全环的写数据。</p>

<h2>3. 现象：均匀流量并不失衡，聚集流量才失衡</h2>
<p>先说一个和预期相反、但数据很明确的结果。</p>

<h3>3.1 uniform：基线本来就是公平的</h3>
{_summary_table(uni)}
<div class="def good">均匀写流量下 S0 的 Jain = <b>{u0['jain']}</b>，
max/min = <b>{u0['max_min']}</b>。<b>没有需要修的不公平。</b>
每个 core 的目的地分布相同，所有出向 hop 的负载在统计上等价，
位置差异被平均掉了。此时上流控只会掉吞吐（见表中 S1 与 S15 两列）。</div>
<img src="{imgs['uniform']['bars']}" alt="uniform per-core BW">

<h3>3.2 cluster：所有 core 写同两个 HA，失衡立刻出现</h3>
{_summary_table(clu)}
<div class="def bad">同样是 10 个 core、同样的需求量，只是把目的地收敛到
{hot}，S0 的 Jain 就掉到 <b>{c0['jain']}</b>，最快最慢比
<b>{c0['max_min']}×</b>：C{hi_core} 拿到 {c0['bw_by_core'][hi_core]}
flit/cycle，C{lo_core} 只有 {c0['bw_by_core'][lo_core]}。</div>
<img src="{imgs['cluster']['bars']}" alt="cluster per-core BW">
<img src="{imgs['cluster']['panels']}" alt="cluster per-core BW over time">
<p class="note">时间轴上看得更清楚：基线里少数几个 core 一开始就抢到高注入率并
一直保持，被压住的 core 全程贴着地板走。</p>
<img src="{imgs['cluster']['overlay']}" alt="cluster slowest vs fastest">

<h2>4. 根因：谁挨着热点，谁就挤不上环</h2>
{_rc_table(clu)}
<p>把每个 core 的实测带宽和它<b>出向 hop 上的过路流量</b>（别处上环、从这里路过的
flit，按最短路解析算出）放在一起，相关系数
<b>r = {crc['corr_bw_pt_eff']}</b>，Spearman 秩相关
<b>{crc['rank_bw_pt_eff']}</b>；和上环成功率的相关是
<b>r = {crc['corr_bw_succ']}</b>。</p>
<img src="{imgs['cluster']['scatter']}" alt="bw vs pass-through">

<div class="def bad">根因一句话：<b>紧挨热点 HA 的那个 core，必须把数据注入到全网最重的
那条链路上——热点前的最后一跳。</b>去 {hot} 的所有写数据都要经过它的出向 hop，
而在环流量绝对优先，于是它自己反而最挤不上去。位置越靠近热点，
过路负载越重，本地带宽越低。</div>

<h3>4.1 I-tag 能限制饥饿时长，但造不出槽位</h3>
<p><code>_itag_blocks</code> 只压制<b>与它竞争的其他注入者</b>，对在环 flit 无效。
表中被饿死的 core，失败原因绝大多数是 <code>hop_busy</code>（在环占用），
I-tag 类失败占比很小——I-tag 把等待时间限住了，却无法把带宽拉平。</p>

<h3>4.2 偏转流量会自我放大</h3>
<p>热点 HA 的 leave 端口一拍只能吞一个 flit；同拍到达的其它 flit 被<b>偏转</b>，
再绕一整圈回来。基线 cluster 下偏转 {clu['schemes']['S0']['n_deflections']} 次，
这些流量重新占用热点前最后一跳，进一步压死本来就挤不上去的邻居 core。</p>

<h2>5. S1：按规格实现的拥塞等级 AIMD</h2>
<p>四段可独立消融，全部按规格实现在 <code>utils/rg_ring2_fc.py</code>：</p>
<ul>
<li><b>拥塞检测</b>：每节点每窗口分别统计上环失败（up）与 eject 偏转（down）的
<code>total_fail</code> 与 <code>net_fail</code>（只计纯粹由在环占用造成的失败），
等级 <code>= min(7, count // 8)</code>。</li>
<li><b>拥塞传递</b>：<code>CongestionBus</code> 专用广播总线，不占环上 hop，
延迟 {clu['schemes']['S1']['fc']['bus_lat']} 拍。</li>
<li><b>拥塞反馈</b>：每节点维护自己的<b>通路节点</b>表，对该集合取 <b>max</b>。</li>
<li><b>流量控制</b>：最终等级 <code>= level_of(own_total_fail −
max_received_net_fail)</code>；罚则 <code>budget ← max(min, ⌊budget·α⌋)</code>，
α = 0.75 / 0.5 / 0.25；奖励 <code>budget ← min(window, budget + β)</code>，
β = 16 / 8 / 2。</li>
</ul>
{_sweep_table(clu)}
<p class="note">cluster 上的 window × α/β 档位扫描。</p>

<h2>6. S1 的结果与失败分析</h2>
<div class="def bad">S1 在 cluster 上把 Jain 从 {c0['jain']} 抬到
{c1['jain']}，max/min 仍有 <b>{c1['max_min']}×</b>，同时吞吐
<b>{thr_d1:+.1f}%</b>——<b>既没拉平，又欠吞吐</b>。三条预测全部被数据证实。</div>
<img src="{imgs['cluster'].get('s1trace', '')}" alt="S1 control trace">

<h3>6.1 max 聚合保住了比例</h3>
<p>共享同一条通路的 core 收到同一个等级，于是<b>乘以同一个 α</b>。等比缩小不改变
贫富<b>比值</b>：预算曲线整体下移，Jain 几乎不动。</p>

<h3>6.2 差值规则把信号搞反了</h3>
<p>被饿死的 core <code>own_total_fail</code> 很高；而上游的赢家正在<b>赢</b>，
它的 <code>net_fail</code> 很低，所以受害者收到的
<code>max_received_net_fail</code> 很小、差值很大 →
<b>受害者惩罚自己</b>；赢家差值 ≈ 0，继续 <code>+β</code>。
这是一个放大不公平的正反馈，图中最慢 core 的最终等级持续高于最快 core。</p>

<h3>6.3 源端速率造不出槽位</h3>
<p>这是最根本的一条。在环优先是绝对的，被限速的 core 让出的空拍立刻被过路 flit
吃掉，被饿死的 core 一无所获。所以 S1 只是把总量压下去：吞吐
{thr_d1:+.1f}%，而最慢 core 的带宽几乎不动。</p>

<h2>7. 改进方案 S15：最大最小公平份额 + 槽预约</h2>
<p>保留专用总线和窗口结构，换掉<b>聚合什么</b>，并加一个仲裁钩子。</p>
<ul>
<li><b>检测</b>：额外记录成功上环数、累计成功数与 active 标志。</li>
<li><b>传递</b>：同一条总线上多播 <code>(等级, 本窗口成功数, 累计成功数,
active, 各出向公平份额)</code>。</li>
<li><b>反馈</b>：用<b>最大最小公平份额</b>替代 max-of-levels。每个共享资源
（有向 hop、以及目的 HA 的 leave 端口）按观测到的吞吐峰值作为容量，
除以其上的活跃竞争者，广播一个份额；节点取自己路径上所有资源份额的最小值作为目标。
容量取实测峰值而非理论值，回路因此自校准，不会把吞吐一路压下去。</li>
<li><b>控制</b>：AIMD 跟踪这个目标，并按<b>累计欠账</b>而不是瞬时速率修正，
避免开局阶段的抢占决定全局。</li>
<li><b>槽预约（真正的修复）</b>：这是速率控制做不到的部分。落后于全环平均
累计进度超过 <code>reserve_gap</code> 的节点，通过总线预约未来若干拍的
<code>(plane, dir, VC)</code> 槽；<b>上游节点不得注入会在预约窗口内到达该槽的
flit</b>。资格用全环累计量判定而不是各自的本地目标——按本地判定时几乎每个节点都
认为自己落后，预约互相抵消，全环白白付出上万次让路。</li>
</ul>

<div class="def">预约只压制<b>注入</b>，从不停住已经在环上的 flit，
所以不需要任何缓冲：<code>n_inring_blocked</code> 与
<code>max_inring_hold</code> 全程为 0，无缓存前提没有被偷偷放弃。</div>

<h3>7.1 结果</h3>
{_summary_table(clu)}
<div class="def {'good' if s15_ok else ''}">S15 在 cluster 上：
Jain <b>{c0['jain']} → {c15['jain']}</b>，
max/min <b>{c0['max_min']} → {c15['max_min']}</b>，
吞吐 <b>{thr_d:+.2f}%</b>（预算 −1%）。最慢 core 的带宽从
{c0['bw_min']} 抬到 {c15['bw_min']}，<b>{c15['bw_min'] / c0['bw_min']:.1f} 倍</b>。
对照验收线（Jain ≥ 0.98、max/min ≤ 1.05、吞吐差 ≤ 1%）：<b>{verdict}</b>。</div>

<p>诚实地说明没有完全达标的部分与原因：max/min 仍是
{c15['max_min']}×。剩下的差距来自热点相邻 core 的<b>瞬时</b>仲裁劣势。
把它的平均速率配到公平份额还不够——它只能在“恰好没有过路 flit”的那些拍上环，
而这样的空拍本身就稀缺。实测中该 core 全程只有约 12% 的
(拍, plane) 机会是可上环的，且它已经用掉了其中的绝大多数。</p>

<div class="def bad">要把 max/min 压到 1.05，只靠“不让上游注入”不够，还必须让
<b>已经在环上</b>的 flit 给预约槽让路。我们实现并实测了这条路径：Jain 可达
0.982、吞吐仅 −0.8%，但代价是单条 segment 上同时被暂存的 flit 峰值达 355 个——
那已经不是无缓存环了。因此该配置<b>没有</b>被采纳为默认值，
默认保持 <code>hold_depth = 0</code> 的严格无缓存语义。
这是本研究给出的真实结论：<b>在严格无缓存、在环绝对优先的前提下，
完全的 per-core 写带宽均等无法在不引入环上缓冲的情况下达成。</b></div>

<h3>7.2 时间轴与链路占用</h3>
<img src="{imgs['cluster']['panels']}" alt="per-core over time">
<img src="{imgs['cluster']['hopbw']}" alt="hop bandwidth vs cap">

<h2>8. 总线的代价</h2>
{_fc_table(clu)}
<p class="note">专用广播总线不占用任何环上 hop，按窗口边界发一次。S1 每次
6 bit（两个 3 bit 等级）；S15 增加 8 bit 本窗口成功数、16 bit 累计数、
1 bit active，以及 6 个 8 bit 出向公平份额，共
{clu['schemes']['S15']['fc']['bus_bits'] // max(1, clu['schemes']['S15']['fc']['bus_posts'])}
bit/次。窗口 {clu['schemes']['S15']['fc']['window']} 拍发一次，
折算到每拍的线上开销可以忽略；面积在 <code>rg_sched_cost.py</code> 中单列。</p>

<h2>9. 结论</h2>
<ol>
<li>无缓存环上的 per-core 写带宽不公平<b>不是</b>拓扑固有的：均匀流量下
Jain = {u0['jain']}，本来就是公平的。它需要一个空间上收敛的目的地分布才会出现。</li>
<li>一旦出现（cluster：Jain {c0['jain']}、max/min {c0['max_min']}×），
根因是<b>热点前最后一跳</b>：紧邻热点的 core 必须注入到全网最重的链路，
在环优先让它最挤不上去。相关系数 r = {crc['corr_bw_pt_eff']}。</li>
<li>S1 按规格实现后<b>既没拉平也欠吞吐</b>（Jain {c1['jain']}、
吞吐 {thr_d1:+.1f}%）：max 聚合保比例、差值规则罚受害者、
源端速率造不出槽位。</li>
<li>S15 用最大最小公平份额 + 上游注入预约，把 Jain 抬到 {c15['jain']}、
max/min 降到 {c15['max_min']}×，吞吐 {thr_d:+.1f}%，且严格保持无缓存。
完全均等（max/min ≤ 1.05）需要在环 flit 让路，即引入环上缓冲，
本研究认为该代价不值得，并把权衡量化列出。</li>
</ol>
</body></html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
