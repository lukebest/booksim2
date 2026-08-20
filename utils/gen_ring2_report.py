#!/usr/bin/env python3
"""HTML report for the 20-node dual-plane ring study."""

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

from rg_ring2_aimd import run_batch as run_aimd
from rg_ring2_base import Ring2BaseParams, run_batch as run_base
from rg_ring2_dist import (
    Ring2DistParams, run_batch as run_dist, s5_params, s6_params,
    s7_params, s8_params, s9_params, s10_params, s11_params, s12_params,
    s13_params, s14_params,
)
from rg_ring2_pop import run_batch as run_pop
from rg_ring2_rg import RGConfig, run_batch as run_rg
from rg_ring2_topo import (
    Ring2Topology, build_allpairs, build_uniform, cores, paths_for_txns,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "report_ring2_20node.html"
BIN_W = 4                                  # cycles per bandwidth sample


def _load(name: str) -> dict:
    p = ROOT / "results" / name
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _table(headers: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = []
    for r in rows:
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _bin_rate(times: list[int], t_max: int, bin_w: int = BIN_W
              ) -> tuple[list[int], list[float]]:
    """Receive rate in flits/cycle, one sample per `bin_w` cycles."""
    nbin = max(1, (t_max + bin_w) // bin_w)
    rate = [0.0] * nbin
    for t in times:
        rate[min(max(t, 0) // bin_w, nbin - 1)] += 1.0 / bin_w
    return [i * bin_w for i in range(nbin)], rate


def _collect_traces(topo: Ring2Topology, txns, *, seed: int = 0
                    ) -> dict[str, dict]:
    p = Ring2BaseParams(plane_sel="least_occupied")
    s0 = run_base(topo, txns, params=p, seed=seed)
    s1 = run_aimd(topo, txns, params=p, seed=seed)
    s2 = run_rg(topo, txns, cfg=RGConfig(algo="islip", iters=2,
                                        plane_sel="least_occupied",
                                        seed=seed))
    s3 = run_pop(topo, txns, params=p, seed=seed)
    s4 = run_dist(topo, txns, params=Ring2DistParams(
        plane_sel="least_occupied", leave_useful=True), seed=seed)
    s5 = run_dist(topo, txns, params=s5_params(plane_sel="least_occupied"),
                  seed=seed)
    s6 = run_dist(topo, txns, params=s6_params(plane_sel="least_occupied"),
                  seed=seed)
    s7 = run_dist(topo, txns, params=s7_params(plane_sel="least_occupied"),
                  seed=seed)
    s8 = run_dist(topo, txns, params=s8_params(plane_sel="least_occupied"),
                  seed=seed)
    s9 = run_dist(topo, txns, params=s9_params(plane_sel="least_occupied"),
                  seed=seed)
    s10 = run_dist(topo, txns, params=s10_params(plane_sel="least_occupied"),
                   seed=seed)
    s11 = run_dist(topo, txns, params=s11_params(plane_sel="least_occupied"),
                   seed=seed)
    s12 = run_dist(topo, txns, params=s12_params(plane_sel="least_occupied"),
                   seed=seed)
    s13 = run_dist(topo, txns, params=s13_params(plane_sel="least_occupied"),
                  seed=seed)
    s14 = run_dist(topo, txns, params=s14_params(plane_sel="least_occupied"),
                   seed=seed)
    out = {}
    for name, r in (("S0", s0), ("S1", s1), ("S2", s2), ("S3", s3),
                    ("S4", s4), ("S5", s5), ("S6", s6), ("S7", s7),
                    ("S8", s8), ("S9", s9), ("S10", s10), ("S11", s11),
                    ("S12", s12), ("S13", s13), ("S14", s14)):
        recv = {int(k): v for k, v in (r.get("recv_by_core") or {}).items()}
        out[name] = {
            "makespan": r.get("makespan"),
            "completed": r.get("completed"),
            "recv_by_core": recv,
        }
    return out


def plot_core_recv_bw(traces: dict[str, dict], path: Path, *,
                      title: str, bin_w: int = BIN_W) -> None:
    cs = cores()
    cmap = plt.get_cmap("tab10")
    fig, axes = plt.subplots(15, 1, figsize=(9.2, 36.8), sharex=False)
    t_max_all = max(
        (max((max(ts) for ts in tr["recv_by_core"].values()), default=0)
         for tr in traces.values()),
        default=1)
    for ax, scheme in zip(axes, ("S0", "S1", "S2", "S3", "S4", "S5", "S6",
                                 "S7", "S8", "S9", "S10", "S11", "S12",
                                 "S13", "S14")):
        tr = traces[scheme]
        t_max = max(
            (max(ts) for ts in tr["recv_by_core"].values()), default=1)
        mean = None
        for i, c in enumerate(cs):
            xs, ys = _bin_rate(tr["recv_by_core"].get(c, []), t_max, bin_w)
            ax.plot(xs, ys, color=cmap(i % 10), lw=1.1, alpha=0.85,
                    label=f"core {c}")
            if mean is None:
                mean = [0.0] * len(ys)
            for j, y in enumerate(ys):
                mean[j] += y / len(cs)
        if mean:
            ax.plot(xs, mean, color="#111827", lw=1.6, ls="--",
                    label="mean", zorder=5)
        ax.set_ylabel("recv flit / cycle")
        ax.set_title(f"{scheme}  makespan={tr['makespan']}", loc="left",
                     fontsize=10)
        ax.set_xlim(0, t_max_all)
        ax.set_ylim(bottom=0)
        ax.grid(True, ls=":", alpha=0.45)
        if scheme == "S0":
            ax.legend(ncol=6, fontsize=7, frameon=False, loc="upper right")
    axes[-1].set_xlabel("cycle")
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _bound_rows(topo: Ring2Topology, big: dict, cmp_: dict
                ) -> tuple[list[list], dict]:
    """Bound components for the two headline workloads + measured ratios."""
    meta = big.get("meta") or {}
    k = meta.get("K") or 2500
    R = meta.get("R") or 4
    cases = [
        ("allpairs m=1 R=4", build_allpairs(m=1, m_resp=4), 4),
        (f"uniform K={k} R={R}", build_uniform(k=k, m_resp=R, seed=0), R),
    ]
    # measured makespans: allpairs from the sweep, uniform from the 10k run
    measured: dict[str, dict[str, int]] = {cases[0][0]: {}, cases[1][0]: {}}
    for s in cmp_.get("summary") or []:
        if s["pattern"] == "allpairs" and s.get("m") == 1 and s.get("R") == 4:
            measured[cases[0][0]][s["scheme"]] = s.get("makespan_mean")
    for sch, v in (big.get("schemes") or {}).items():
        measured[cases[1][0]][sch] = v.get("makespan")

    fields = [("LB_link 段带宽", "link_lb"), ("LB_port 端口", "port_lb"),
              ("LB_cut 二等分割", "cut_lb"), ("LB_txn 单事务串行", "single_txn_lb"),
              ("bound = max(·)", "bound")]
    rows: list[list] = []
    bounds: dict = {}
    for name, txns, r in cases:
        rp, sp = paths_for_txns(topo, txns, strategy="least_occupied")
        b = topo.analytic_bounds(rp, sp, m_req=1, m_resp=r)
        bounds[name] = b
        rows.append([f"<b>{name}</b>", f"{len(txns)} 事务", "", ""])
        for label, key in fields:
            hit = " ← 主导" if (key != "bound" and b[key] == b["bound"]) else ""
            rows.append([label, b[key], hit, ""])
        for sch in ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8",
                    "S9", "S10", "S11", "S12", "S13", "S14"):
            mk = measured[name].get(sch)
            if mk is None:
                continue
            rows.append([f"实测 {sch}", mk, "",
                         f"{mk / max(1, b['bound']):.2f}× bound"])
    return rows, bounds


def main() -> None:
    cmp_ = _load("ring2_20node.json")
    pareto = _load("ring2_rg_pareto.json")
    verify = _load("verify_ring2_20.json")

    sum_rows = []
    for s in cmp_.get("summary") or []:
        mk = s.get("m") if s["pattern"] == "allpairs" else s.get("K")
        sum_rows.append([
            s["scheme"], s["pattern"], s.get("R"), mk,
            s.get("makespan_mean"), s.get("makespan_min"),
            s.get("makespan_max"), s.get("bound"),
            "yes" if s.get("all_completed") else "NO",
        ])

    front_rows = [[p["tag"], p["area_norm"], p["makespan"]]
                  for p in (pareto.get("pareto") or [])]
    ver_rows = [[r["name"], "ok" if r["ok"] else "FAIL"]
                for r in (verify.get("rows") or [])]

    big = _load("ring2_core10k.json")
    board_html = ""
    if big.get("schemes"):
        meta = big.get("meta") or {}
        SCH = [s for s in ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7",
                           "S8", "S9", "S10", "S11", "S12", "S13", "S14")
               if s in big["schemes"]]
        board_rows = [["makespan"] + [big["schemes"][s].get("makespan")
                                      for s in SCH]]
        for label, field in (("偏转次数", "n_deflections"),
                             ("上环队列峰值", "max_srcq"),
                             ("下环队列峰值", "max_ejectq"),
                             ("响应时延 p50", "lat_p50"),
                             ("响应时延 p99", "lat_p99"),
                             ("HA 放出响应", "n_pull_issued"),
                             ("core 窗口峰值", "max_pull_outstanding"),
                             ("每核 outstanding 峰值", "max_core_outstanding")):
            board_rows.append(
                [label] + [big["schemes"][s].get(field, "—") if
                           big["schemes"][s].get(field) is not None else "—"
                           for s in SCH])
        cores_s = sorted({int(c) for s in big["schemes"].values()
                          for c in (s.get("board_by_core") or {})},
                         key=int)
        for c in cores_s:
            rec = [f"core {c}"]
            for sch in SCH:
                b = (big["schemes"][sch].get("board_by_core") or {}).get(
                    str(c), {})
                rec.append(
                    f"上环 {b.get('board', 0)} "
                    f"(CW {b.get('board_cw', 0)} / CCW {b.get('board_ccw', 0)})"
                    f"<br>失败 {b.get('board_fail', 0)} "
                    f"(CW {b.get('board_fail_cw', 0)} / "
                    f"CCW {b.get('board_fail_ccw', 0)})")
            board_rows.append(rec)
        tot = ["合计"]
        for sch in SCH:
            bb = (big["schemes"][sch].get("board_by_core") or {}).values()
            board = sum(v.get("board", 0) for v in bb)
            cw = sum(v.get("board_cw", 0) for v in bb)
            ccw = sum(v.get("board_ccw", 0) for v in bb)
            fail = sum(v.get("board_fail", 0) for v in bb)
            fcw = sum(v.get("board_fail_cw", 0) for v in bb)
            fccw = sum(v.get("board_fail_ccw", 0) for v in bb)
            tot.append(
                f"上环 {board} (CW {cw} / CCW {ccw})"
                f"<br>失败 {fail} (CW {fcw} / CCW {fccw})")
        board_rows.append(tot)
        board_html = f"""
<h2>4. 同 pattern 对照 · 每核 {meta.get('flits_per_core', 10000)} 响应 flit</h2>
<p class="note">uniform K={meta.get('K')} R={meta.get('R')} seed={meta.get('seed')}，
<code>plane_sel=least_occupied</code>，hop 时延 {meta.get('hop_lat')} 拍，
上环队列 {meta.get('inj_depth')} 深，下环队列 {meta.get('eject_depth')}。
每个 core 收到的响应 flit 数完全相同。叠图共用一条时间轴，S0–S14 可直接对比。
512 对齐后 S3 与 S0 的均值曲线重合，叠图里 S3 画成虚线以免把 S0 盖住。
S4 是 kind-aware leave；S5 预约目的 leave 口，消灭双方向同拍到达导致的偏转；
S6 在 S5 上把同拍 dest 冲突改成 oldest-first；S7 在第一跳被占时换 plane；
S8 在注入时现场选 hop+dest 都空的 plane；
S9 在第一跳仍忙时改走另一环方向（绕路最多 +2 hop）；
S10 只对响应做这次改向，请求仍走最短路；
S11 同拍争同一第一跳时只留最老的响应；
S12 在 dest 与第一跳上做一波 request-grant，dest grant 在 hop 失败后让出；
S13 在 dest-granted 的 hop grant 里优先剩余 hop 更短的；
S14 在 HA 两个 srcq 被 late_plane 绑到同一第一跳时，短/老的留下，另一条换 plane。
黑点线是解析下界对应的理想接收：每核 {meta.get('flits_per_core')} flit 在
<code>bound={meta.get('bound')}</code> 拍内匀速收完
（{meta.get('ideal_recv_rate')} flit/cycle/core），之后为 0。
上环统计只针对<b>发往该 core 的响应数据</b>：上环 = 成功注入，CW = 方向 +1，
CCW = 方向 −1，失败 = 发现 slot 忙或被 I-tag 挡住的注入尝试（<b>不含</b> AIMD
令牌拒绝、也不含 S3 的接收窗口等待）。S2 的失败数是 0 是因为 hop 已被预约；
S3 不预约 hop，读请求受每核 512 条 outstanding 限制，HA 按已到请求调度响应。
S4 不预约 hop，只改 leave 口的种类优先级。
S5 预约 dest 的 leave 时隙，偏转为 0。
S6 同面积，同拍 dest 冲突留最老的 flit。
S7 同预约表，本 plane 第一跳忙则改绑到另一 plane。
S8 注入时在 hop+dest 都空的 plane 里选占用更低的那个。
S9 同预约表，第一跳仍忙则改走另一方向（≤+2 hop）。
S10 只对响应改向。
S11 同拍争同一第一跳时只留最老的响应。
S12 dest 先 grant、hop 再 accept，hop 失败则 dest 让给下一名。
S13 hop grant 优先剩余 hop 更短的。
S14 HA 同节点两条 srcq 争同一第一跳时，输家换 plane。</p>
<p><img src="ring2_core_recv_bw_10k_overlay.png" alt="十五方案均值接收带宽叠图"></p>
<p><img src="ring2_core_recv_bw_10k.png" alt="每核接收带宽 10k"></p>
{_table(["", "S0 RR", "S1 AIMD", "S2 request-grant",
         "S3 push-on-pull", "S4 kind-aware leave",
         "S5 leave-slot lock", "S6 oldest dest",
         "S7 hop bounce", "S8 late plane", "S9 late dir",
         "S10 resp late dir", "S11 hop hold", "S12 hop islip",
         "S13 hop short", "S14 HA sib plane"][:1+len(SCH)],
        board_rows)}
<p class="note">墙钟 {big.get('wall_secs', '?')}s。</p>
"""
    else:
        board_html = "<h2>4. 同 pattern 每核 10000 flit</h2><p class='note'>跑 <code>python3 utils/dse_ring2_core10k.py</code> 填充本节。</p>"

    topo = Ring2Topology()
    bnd_rows, bnd = _bound_rows(topo, big, cmp_)
    n_dir = len(topo.directed_links)
    n_s2 = len([r for r in (pareto.get("rows") or [])
                if r.get("scheme") == "S2"])

    # headline: how close the best scheme gets to the floor on the big run
    uni = next((v for k, v in bnd.items() if k.startswith("uniform")), {})
    uni_bound = uni.get("bound")
    s2_mk = ((big.get("schemes") or {}).get("S2") or {}).get("makespan")
    if uni_bound and s2_mk:
        close = (f"此时 S2 做到 {s2_mk} 拍，而下界是 {uni_bound} 拍，"
                 f"即约 {s2_mk / uni_bound:.2f}× 物理极限")
    else:
        close = "跑满 §4 的驱动后这里会给出实测与下界的比值"

    png = "ring2_rg_pareto.png"
    html = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>双全环 20 节点 — 十五方案 makespan + RG Pareto</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, "Noto Sans CJK SC", sans-serif;
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
</style></head><body>
<h1>双全环 20 节点：十五方案 makespan + request-grant Pareto</h1>
<p class="note">偶数 index 是 AI core，奇数是 memory Home Agent。两个独立的双向
ring plane；每节点每 plane 一个端口，plane 内两个方向共用该端口的 buffer。
流量是读往返（请求 1 flit，响应 R flit）。<b>makespan = 最后一个响应 flit 在发起
core 处被 drain 的那一拍。</b></p>

<h2>0. makespan 的理论下界</h2>
<p>下界是<b>可达性的必要条件</b>，用来判断一个方案离物理极限还有多远，而不是宣称
某个值可达。所有下界都来自「某个资源必须搬运的总量 ÷ 该资源的容量」这一类
计数论证，因此对<em>任何</em>调度策略都成立，包括离线最优。</p>

<h3>0.1 记号</h3>
<div class="def">
<code>N</code> = {topo.n} 节点 · <code>P</code> = {topo.n_planes} 个 plane ·
<code>σ</code> = {topo.sigma} 拍/flit（同一有向段上连续两个 flit 的最小间隔）·
<code>λ</code> = {topo.hop_lat} 拍（相邻节点 hop 时延）·
有向段总数 <code>2PN</code> = {n_dir}<br>
事务集合 <code>T</code>；每个 <code>t ∈ T</code> 是 core→HA 的
<code>m_req</code> flit 请求，加 HA→core 的 <code>m_resp</code> flit 响应。<br>
<code>π_req(t)</code> / <code>π_resp(t)</code> 是取最短方向的路径，
<code>hops(π)</code> 是跳数。
</div>

<h3>0.2 LB_link：段带宽下界</h3>
<p>对每条<em>不区分 plane</em> 的有向邻接 <code>(u,v)</code>，统计必须穿过它的
flit 总数</p>
<div class="def"><code>L(u,v) = Σ_t m_req·[⟨u,v⟩ ∈ π_req(t)]
+ m_resp·[⟨u,v⟩ ∈ π_resp(t)]</code></div>
<p>plane 分配是<em>策略</em>而非物理约束——同一条有向 hop 两个 plane 都能承载，
所以该有向邻接的合并容量是每 <code>σ</code> 拍 <code>P</code> 个 flit：</p>
<div class="def"><code>makespan ≥ LB_link = σ · ⌈ max<sub>(u,v)</sub> L(u,v) / P ⌉</code></div>
<p class="note">这里必须除以 <code>P</code>。如果改用某个具体
<code>plane_sel</code> 策略下的单 plane 峰值负载，会在仿真器运行时平衡得更好时
反过来<em>高于</em>实测 makespan，那就不是下界了。</p>

<h3>0.3 LB_port：端口下界</h3>
<p>对每个节点 <code>n</code>，令 <code>B(n)</code> 为必须在 <code>n</code>
上环的 flit 总数、<code>E(n)</code> 为必须在 <code>n</code> 下环的 flit 总数
（跨 plane 合并，请求与响应合并）。每节点每 plane 各 1 个 inject / eject 端口：</p>
<div class="def"><code>makespan ≥ LB_port = σ · ⌈ max<sub>n</sub>
max(B(n), E(n)) / P ⌉</code></div>

<h3>0.4 LB_cut：二等分割下界</h3>
<p>环的二等分需要切开<em>两个</em>缺口。取割集</p>
<div class="def"><code>X = {{⟨N/2−1, N/2⟩, ⟨N/2, N/2−1⟩, ⟨N−1, 0⟩, ⟨0, N−1⟩}}
× P 个 plane</code>，共 <code>|X|</code> = {4 * topo.n_planes} 条有向段</div>
<p>令 <code>C</code> 为所有路径穿过 <code>X</code> 的 flit-段数总和，则</p>
<div class="def"><code>makespan ≥ LB_cut = σ · ⌈ C / |X| ⌉</code></div>
<p class="note">这里数的是<em>实际</em>穿越次数。core/HA 交错布局下大量路径只有
1 跳，套用「一半流量必然过割」的经验假设会高估到超过实测值。</p>

<h3>0.5 LB_txn：单事务串行时延下界</h3>
<p>一个事务内部是严格串行的：请求上环 → 飞越 <code>hops·λ</code> →
<code>m_req·σ</code> 拍排空 → HA 服务 <code>t_ha</code> → 响应飞越 →
<code>m_resp·σ</code> 拍排空。取最深的那个事务：</p>
<div class="def"><code>makespan ≥ LB_txn = max<sub>t</sub> [ hops(π_req(t))·λ
+ m_req·σ + t_ha + hops(π_resp(t))·λ + m_resp·σ ]</code></div>

<h3>0.6 合成与实测对照</h3>
<div class="def"><code>bound = max(LB_link, LB_port, LB_cut, LB_txn)</code></div>
{_table(["项", "值", "", "比值"], bnd_rows)}
<p>两档流量的<b>主导项完全不同</b>。allpairs 只有 100 个事务，资源计数远没
饱和，瓶颈是单个事务的往返时延（<code>LB_txn</code>）；到了每核 10000 flit
这一档，段带宽（<code>LB_link</code>）成为主导，{close}。</p>

<h3>0.7 这些下界为什么不紧</h3>
<p>每一条都是一次<em>松弛</em>，被忽略的约束如下：</p>
<ul>
<li><b>请求→响应依赖被丢掉了</b>（除 <code>LB_txn</code> 之外）。资源类下界把
两波流量当成两支独立车队，实际上任何响应都不能早于它对应的请求下环。这一条是
allpairs 那档 <code>bound</code> 只有 41 的直接原因：100 个事务的资源计数太小，
而依赖链没被计入。</li>
<li><b>不含偏转。</b>下界假设每个 flit 只走最短路。S0 实测的 flit-hop 总量比
最短路多约 40%，那些多出来的 hop 是偏转绕圈产生的。</li>
<li><b>plane 分配当成自由变量</b>（除以 <code>P</code>）。真实系统里它是一个
在线决策，不可能后验最优。</li>
<li><b>不含上环队列深度、leave 端口冲突、I-tag / E-tag 的抑制效应。</b></li>
<li><b>四项各自取 max，没有联立。</b>真正的 LP 松弛（同时满足段、端口、割和
依赖约束）会给出更高的下界。</li>
</ul>

<h2>1. 共同数据面（十五方案完全相同）</h2>
<p class="note">S0–S8 <em>不是</em>九种不同的 fabric。它们共用同一条
点对点 credit 数据面、同样 8 深的上环队列、同样的 I-tag / E-tag 保证。整个
扫参只改变「一个源被允许如何花掉 credit / 谁先占用 leave 口」。</p>
{_table(["层", "S0 RR", "S1 AIMD", "S2 request-grant", "S3 push-on-pull",
         "S4 kind-aware leave", "S5 leave-slot", "S6 oldest dest",
         "S7 hop bounce"], [
    ["相邻节点 hop 时延", "2 拍", "2 拍", "2 拍", "2 拍", "2 拍", "2 拍",
     "2 拍", "2 拍"],
    ["上环队列（每 node, plane）", "8 flit", "8 flit", "8 flit", "8 flit",
     "8 flit", "8 flit", "8 flit", "8 flit"],
    ["下环队列（每 node, plane）", "4 + 1 E-tag", "4 + 1 E-tag",
     "4 + 1 E-tag", "4 + 1 E-tag", "4 + 1 E-tag", "4 + 1 E-tag",
     "4 + 1 E-tag", "4 + 1 E-tag"],
    ["inject / eject 端口", "每 (node, plane) 1 个", "每 (node, plane) 1 个",
     "每 (node, plane) 1 个", "每 (node, plane) 1 个", "每 (node, plane) 1 个",
     "每 (node, plane) 1 个", "每 (node, plane) 1 个", "每 (node, plane) 1 个"],
    ["点对点 credit 流控", "有", "有", "有", "有", "有", "有", "有", "有"],
    ["I-tag（上环饥饿有界）", "有", "有", "有", "有", "有", "有", "有", "有"],
    ["E-tag（下环 / 预留 eject）", "有", "有", "有", "有", "有", "有", "有", "有"],
    ["每核 outstanding 读", "512", "512", "512", "512", "512", "512", "512",
     "512"],
    ["有 credit 时 RR 上环", "有", "有", "—",
     "有（读请求受 512/核 outstanding 卡）", "有", "有", "有", "有"],
    ["AIMD 源端速率（失败 piggyback）", "—", "有", "—", "—", "—", "—", "—",
     "—"],
    ["上环前 request-grant 匹配", "—", "—", "有", "—", "—", "—", "—", "—"],
    ["读请求作 POP 调度信息", "—", "—", "—", "有（HA RR 响应）", "—", "—",
     "—", "—"],
    ["kind-aware leave", "—", "—", "—", "—", "有（无额外 bit）", "—", "—",
     "—"],
    ["dest leave 时隙预约", "—", "—", "—", "—", "—", "有（节点号）",
     "有（oldest）", "有（oldest）"],
    ["hop_bounce 换 plane", "—", "—", "—", "—", "—", "—", "—", "有"],
])}
<ul>
<li><b>Credit：</b>每条有向 hop 是一对 credit。上游发 flit 前先扣 credit，
下游槽位空出后归还。没有 credit 绝不发送。共 {n_dir} 条有向段
（{topo.n_planes} 个 plane × 2 个方向 × {topo.n} 节点）。</li>
<li><b>上环队列：</b>每 (node, plane) 8 个 flit，plane 内双向共用。PE 把 flit
交给 fabric 外的 backlog，只有队列有空位才 admit，所以注入点是<em>真反压</em>，
不是把整批流量一次吞下。</li>
<li><b>I-tag：</b>某个源在某 (plane, dir) 上饿了 <code>t_inj</code> 拍之后升起
I-tag，抑制该环向上其他节点上环，直到它自己上去。作用是给上环饥饿一个上界。</li>
<li><b>E-tag：</b>一个 flit 下环失败（共享的 per-plane eject 队列满，或该拍
唯一的 leave 端口已被占用）<code>t_xfer</code> 次之后升起 E-tag，可以使用
<code>resv_ej</code> 条预留 eject 槽；否则偏转，再绕一圈。这里改绑到预留的
<em>eject</em> 表项，不是 HiRD 原版的 transfer-FIFO E-tag。</li>
</ul>

<h2>2. 验证</h2>
<p>{verify.get("n_ok", 0)}/{verify.get("n_total", 0)} 项检查通过。</p>
{_table(["检查项", "结果"], ver_rows)}

<h2>3. 十五方案 makespan 扫参（默认 plane_sel=least_occupied, eject_depth=4）</h2>
<p class="note">每一行都跑在同一条 credit + I-tag / E-tag 数据面上。
S0 = RR 上环，无源端速率控制。
S1 = S0 + 失败计数 piggyback + AIMD 令牌桶（默认温和：α=0.15 / β=0.85 / epoch=64 / rate_min=0.30）。
S2 = 同样的 hop 上做 request-grant iSLIP（I=2, interval, arc）。
S3 = 读请求本身是 POP 调度信息：每核最多 512 条 outstanding 读，HA 对已到请求 RR 后给响应，不预约 hop。
S4 = leave 口按种类排序：core 优先下响应，HA 优先下请求。
S5 = 预约 dest 的 leave 时隙，同拍冲突留节点号更小的源，偏转为 0。
S6 = S5 + 同拍 dest 冲突留最老的 flit（t_gen），面积与 S5 相同。
S7 = S6 + 本 plane 第一跳忙则改绑到另一 plane。
S8 = S7 + 注入时现场选 hop+dest 都空的 plane（占用更低者）。
S9 = S8 + 第一跳仍忙则改走另一环方向（绕路 ≤+2 hop）。
S10 = S9 + 只对响应改向。
S11 = S10 + 同拍争同一第一跳时只留最老的响应。
S12 = S11 + dest-then-hop request-grant（I=1；hop 失败则 dest 让出）。
S13 = S12 + hop grant 优先剩余 hop 更短的。
S14 = S13 + HA 同节点两条 srcq 争同一第一跳时输家换 plane。
bound 列是 §0 的解析下界。</p>
{_table(["方案", "pattern", "R", "m 或 K", "均值", "最小", "最大", "bound", "完成"],
        sum_rows)}
<p class="note">墙钟 {cmp_.get("wall_secs", "?")}s，{len(cmp_.get("rows") or [])} 行。
Quick={ (cmp_.get("meta") or {}).get("quick") }。</p>

{board_html}

<h2>5. request-grant 的面积 / makespan Pareto</h2>
<p class="note">y = makespan_des + t_sched_cycles（调度器延迟计回）。
x = area_norm（IQ-XY router = 1.0，按节点摊）。S0–S14 作为参考点画在同一张
图上。面积对十五方案都计入<em>共同</em>的 credit + 8 深上环队列 + I-tag / E-tag
数据面（含每核 512 条 outstanding 记分板）；S2 再加仲裁器，S3 加 HA pending/RR，
S4 与 S0 同位，S5–S14 加 dest leave 时隙窗口——都<b>没有</b>删掉站点存储。
等效 bit 模型，标定到 mesh <code>greedy_ff = 0.05</code>，不是 mm²。</p>
<p><img src="{png}" alt="Pareto 前沿"></p>
<p class="note">图只画 S0–S14 左下角膝点；S5–S14 同面积，S14 留在真实 x（前沿顶点），其余点沿 x 稍稍错开以便辨认。
S2 族只保留距离前沿最近的一个（全图归一化欧氏距离），其余云点不画。
x 轴在膝点与该 S2 之间断开。</p>
{_table(["配置 tag", "area_norm", "makespan"], front_rows)}
<p class="note">{pareto.get("n_front", 0)} 个非支配点，
共评估 {len(pareto.get("rows") or [])} 个（其中 S2 {n_s2} 个），
墙钟 {pareto.get("wall_secs", "?")}s。</p>

<h3>5.1 为什么图上 S2 有这么多点</h3>
<p>因为 <b>S2 不是一个方案，而是一族方案</b>。S0–S14 各自只有一种硬件结构，
所以各出一个点；而 request-grant 的「仲裁器」是一个可设计的对象，每一组旋钮
取值对应一块<em>不同的、都可实现的</em>电路，面积和调度延迟都不同，因此每一组
都必须单独评估、单独画点。旋钮空间：</p>
{_table(["旋钮", "取值", "个数"], [
    ["匹配算法", "islip, pim, rr_oldest, lqf, ocf, bvn, greedy_ff, "
     "wavefront, batched_bcfs", 9],
    ["迭代轮数 I", "islip / pim 取 1,2,4；其余只有 1", "3 或 1"],
    ["冲突域 spatial_reuse", "arc（只锁弧段）/ whole_ring（锁整环）", 2],
    ["占用表示 conflict_domain", "interval（区间表）/ free_at（标量时刻）", 2],
    ["仲裁器 arbiter", "central / per_plane / distributed_token", "2 或 3"],
    ["VOQ 粒度", "per_dst / per_plane_dir / grouped", "1 或 3"],
])}
<p>主网格是「算法×迭代」13 种 × 冲突域 2 × 占用表示 2 × 仲裁器 2 = 104 个配置，
再加一片较窄的补充切片（VOQ 粒度、token 仲裁器、带 RTT 的流水线）去掉重复后
共 {n_s2} 个 S2 点。</p>
<p>关键在于<b>这些点绝大多数没有意义、也不该有意义</b>——它们的作用是把前沿
「撑」出来。只有 {pareto.get("n_front", 0)} 个点是非支配的。散点越密，说明
「S2 能不能赢」这个问题被问得越充分：一个只画了自己最好配置的 S2 是无法反驳的，
而画满 {n_s2} 个配置之后，S2 仍被同面积的 S14 压住，这个结论就有分量了。
S11（67）/ S12 / S13（68）同面积，被 S14 的 64 支配。</p>
<p class="note">注意 y 轴已经把 <code>t_sched_cycles</code> 计回去了。这是为什么
很多 S2 点被推到图的上方：<code>batched_bcfs</code> 在纯数据面上极快
（DES 只要几十拍），但它的组合逻辑深度换算出上千拍的调度延迟，于是自己把自己
罚出了前沿。一个只因为造不出来才快的算法，会在这张图上付出代价。</p>

<h2>6. 怎么读这组对比</h2>
<ul>
<li>把十五方案读成<b>同一块 fabric 上的十五种策略</b>。credit + I-tag + E-tag
永远都在。环上流量依然从不 stall（前视短于 hop 时延）；I-tag 给上环饥饿定上界；
E-tag 给下环活锁定上界。</li>
<li><b>S0</b> 是反应式基线：hop 空就用 RR 花掉 credit，除 I-tag 之外没有任何
源端速率控制。它是<em>工作守恒</em>的，代价是每次成功上环约伴随 2 次重试——
重试不烧 slot，所以这笔代价落在时延上，不落在 makespan 上。</li>
<li><b>S1</b> 把上环 / 下环失败计数回传给源端，对令牌桶速率做 AIMD。默认温和配置
（α=0.15 / β=0.85 / epoch=64 / rate_min=0.30）在小流量上可以赢 S0；10k 上
makespan 是 S0 的约 1.22×。教科书组合会一路乘到 0.05 地板，那是旋钮过狠，不是 AIMD 不能用。</li>
<li>S1 仍是<b>在拿一点吞吐换时延</b>：10k 响应 p50 是 23 拍（S0 是 2758），
outstanding 峰值 54，碰不到 512 的记分板。</li>
<li><b>S2</b> 保留同样的 credit + 上环队列 + I-tag / E-tag 数据面，在上环<em>之前
</em>加一次 request-grant 匹配，使 flit 只在 hop 已被预约时注入。它要付一个
仲裁器加一小笔控制面开销。端口按 (node, plane) 计价之后（与 S0 的 DES 一致），
S2 在数据面上仍最快（10k 10044），但把 <code>t_sched_cycles</code> 计回后
被同面积的 S14（64）压出前沿。S11（67）/ S12 / S13（68）同面积，不进前沿。</li>
<li><b>S3</b> 用读 memory 的请求当 POP 调度信息：五方案对齐为每核最多
512 条 outstanding 读，HA 对已到达的多条请求做 RR，再放出该请求的响应。环上 hop
仍是反应式的，所以它<b>不</b>消除 slot 忙导致的上环失败。没有单独的
pull-token RTT——请求在数据面上走到 HA，本身就是 grant。</li>
<li><b>S4</b> 是分布式、零额外 bit 的 leave 优先级：core 上先让响应下环（解锁
outstanding），HA 上先让请求下环（尽快放出响应）。allpairs 上它支配 S0（同面积、
122 vs 129）；10k 反而慢一截（15075 vs 14886）。</li>
<li><b>S5</b> 预约 dest 的 leave 时隙：注入前算 ETA，若该 (dst, plane, cycle)
已被占用则本拍不上环。同拍多个候选留节点号更小的源。消灭双方向同拍到达造成的偏转（10k 偏转 11348→0），
makespan 14886→13522（1.51× bound，S2 仍是 1.12×）。allpairs 129→100。
面积只多一张 64 拍窗口的 leave 记分板。</li>
<li><b>S6</b> 与 S5 同一张预约表，同拍 dest 冲突改留最老的 flit。allpairs 仍是 100
（Pareto 上与 S5 重合）；10k 13522→13200（1.48× bound），p99 3816→3050。
面积与 S5 相同。</li>
<li><b>S7</b> 在 S6 上加 hop_bounce：本 plane 第一跳被占时，若另一 plane 的
第一跳和 dest leave 都空，就改绑过去。allpairs 100→83；10k 13200→12824
（1.43× bound）。面积与 S5 / S6 相同，所以 allpairs Pareto 上 S7 支配 S5 / S6。
p99 从 3050 升到 3917——换 plane 换来吞吐，尾核公平回退一点。</li>
<li><b>S8</b> 在注入时现场选 hop 和 dest leave 都空的 plane；两个都能上就走
占用更低的。allpairs 83→72；10k 12824→11971（1.34× bound）。面积仍是
<code>ring2_ej</code>，Pareto 上当时 S8 支配 S7。</li>
<li><b>S9</b> 在 S8 上：本方向第一跳仍忙时，若另一方向绕路不超过 +2 hop
且 dest leave 空，就改走那边。10k stall 里第一跳拒绝（98376）远大于 dest
leave（4675）。allpairs 72→73；10k 11971→11809（1.32× bound）。面积仍是
<code>ring2_ej</code>。同面积被 S8 的 72 压住。</li>
<li><b>S10</b> 只让响应走 late_dir，请求保持最短路。allpairs 73→69（同面积压住
S8 的 72）；10k 11809→11781。slack=1 是空操作，slack=4 等于 S9，slack=8
和 hold 都输。当时 allpairs Pareto 前沿是 S4 / S1 / S10。</li>
<li><b>S11</b> 同拍多个响应争同一第一跳时只留最老的（不预约未来 hop）。
dest-aware late_dir 全部输。allpairs 69→67；10k 11781→11451（1.28× bound）；
p99 4248→2512。面积仍是 <code>ring2_ej</code>。allpairs Pareto 前沿是
S4 / S1 / S11。</li>
<li><b>S12</b> 在 dest leave 与第一跳上做一波本地 request-grant：dest
grant 等到 hop accept 才提交，hop 失败则 dest 让给下一名。10k
11451→11402；allpairs 67→68。面积仍是 <code>ring2_ej</code>。
I=2 在 10k 上回退到 11481，不作为默认。</li>
<li><b>S13</b> 在 dest-granted 的 hop grant 里优先剩余 hop 更短的。10k
11402→11288 / 11399 / 11270；allpairs 仍 68；K=500 2419→2362。
面积仍是 <code>ring2_ej</code>。同面积被 S11 的 67 支配。</li>
<li><b>S14</b> 在 HA 两个 srcq 被 late_plane 绑到同一第一跳时，短/老的留下，
另一条换到 hop+dest 都空的另一 plane。allpairs 68→<b>64</b>（压住 S11 的 67）；
10k 11288→<b>11043 / 11224 / 11201</b>（三 seed 全赢）。面积仍是
<code>ring2_ej</code>。allpairs Pareto 前沿是 S4 / S1 / <b>S14</b>。
两端都做（<code>late_plane_sib=1</code>）allpairs 71，只做 core 则 70 / 11382，
都不作为默认。</li>
<li>4 深的下环队列在这些负载下几乎没有作用：峰值占用只有 1。偏转来自
每 (node, plane) <b>唯一</b>的那个 leave 端口，两个方向都要抢它。真正的限制是
端口数量，不是队列深度。</li>
</ul>
<p class="note">写稿：<code>docs/phase-7-exploration/ring2-20node-core-ha.md</code></p>
</body></html>
"""
    OUT.write_text(html)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
