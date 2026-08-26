#!/usr/bin/env python3
"""Per-core write-bandwidth fairness on the bufferless 20-node ring.

Workload: every AI core issues `K` CHI WriteNoSnp transactions to a
uniform-random memory HA. One transaction is the full four-phase handshake

    REQ (core->HA) -> DBIDResp (HA->core) -> WriteData xW (core->HA)
    -> Comp (HA->core)

so the run instantiates three CHI VCs (REQ / RSP / DAT) and the directed-hop
bandwidth cap is 80 x 3 = 240 flit/cycle.

The ring is bufferless with strict in-ring priority: `_launch` never stalls a
flit already on the ring, it only occupies the outgoing slot, and `_can_board`
refuses a local injection whenever that slot is taken or reserved. The
question this driver answers is whether that priority makes the achieved
write bandwidth depend on where a core sits on the ring, and whether a source
rate controller can repair it.

Schemes
-------
S0   baseline: RR inject + I-tag / E-tag, no flow control.
S1   the 4-part congestion-control spec (detect / propagate / feed back /
     AIMD on an integer per-window injection budget).
S15  max-min fair share over the same bus plus bounded slot reservation.
S16  receiver-driven grant pacing (Homa-style) over the stock DBIDResp.
S17  TIMELY: RTT-gradient rate control, paced on REQ.
S18  DCQCN: RED marks off the completer's request tracker, paced on REQ.
S19  Swift-like: delay-triggered outstanding window.
S20  DCTCP-like: tracker-ECN-triggered outstanding window.

Every completer has a finite CHI request tracker (`ha_track`), so one that is
over-subscribed must answer RetryAck instead of accepting. That is part of the
baseline, not of one scheme: at the tracker and outstanding cap used here
almost every transaction is bounced once, which is the pressure all four
schemes are measured under.

`--retry` turns on the second study, which sweeps the per-core outstanding cap
on two workloads. That is what shows the cap has an interior optimum -- too
small does not cover the round trip, too large drowns the tracker in retries
which park outstanding slots and reorder the stream -- and that the optimum
moves with the workload, so no static value is right.

Writes results/ring2_write_fair.json; plotting lives in
gen_ring2_write_report.py so the plots can be redrawn without re-simulating.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

_UTILS = Path(__file__).resolve().parent
if str(_UTILS) not in sys.path:
    sys.path.insert(0, str(_UTILS))

from rg_ring2_base import Ring2BaseParams, Ring2BaseSim
from rg_ring2_topo import (
    BURST_BYTES, BURST_FLITS, CHI_VCS_WRITE, FLIT_BYTES, N_NODES,
    Ring2Topology, STRIDE_BYTES, TILE_BYTES, Txn, build_hot_write,
    build_tiled_write, build_uniform_write, cores, has, hop_count,
    shortest_dir, write_bounds, write_paths_for_txns,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "ring2_write_fair.json"
K_PER_CORE = 20_000
N_PLANES_STUDY = 1
W_FLITS = BURST_FLITS   # 128B burst / 64B flit
BIN_W = 128
T_MAX = 4_000_000
# Two adjacent memory nodes standing in for one clustered memory region. Both
# are memory in this study (9 and 19 are not), so the roles are unchanged and
# only the destination geometry differs from `uniform`.
HOT_HAS = (11, 13)

# -- the retry / outstanding study ------------------------------------------
# Request tracker entries per completer, and the baseline for every scheme:
# a completer that runs out of entries must RetryAck. 32 is what S16 pins its
# write-data buffer to, so holding the tracker there asks every scheme to live
# inside the same completer budget.
RETRY_TRACK = 32
# S16 has to grant from *below* the tracker to do anything at all: at
# overcommit >= ha_track its pump never withholds a grant and it degenerates
# to S0 exactly (pinned by verify_ring2_20.py).
S16_OVERCOMMIT = 16
OUTST_POINTS = (4, 8, 16, 32, 64, 128, 256)
TRACK_POINTS = (8, 16, 32, 64, 128, 0)      # 0 = unlimited tracker
RETRY_SCHEMES = ("S0", "S16", "S17", "S18")
RETRY_K = 800                # shorter batch: the grid is 56 runs wide
OUTST_SAMPLE = 16            # cycles between outstanding-occupancy samples
# Injection rates to pin, in REQ/cycle/core. Pinning the rate removes the
# controller entirely, so the best of these is the ceiling any rate-based
# scheme could reach if it guessed perfectly and never oscillated. The gap
# between it and S17 / S18 is what being reactive costs.
RATE_POINTS = (0.06, 0.10, 0.125, 0.15, 0.20, 0.30, 0.50, 1.0, 2.0)

# Flits per VC per transaction: REQ 1, RSP 2 (DBIDResp + Comp), DAT W.
M_REQ, M_RSP = 1, 2


# ---------------------------------------------------------------------------
# Fairness math
# ---------------------------------------------------------------------------

def jain(xs: Sequence[float]) -> float:
    """(sum x)^2 / (n * sum x^2). 1.0 = perfectly equal, 1/n = one winner."""
    xs = [float(x) for x in xs]
    if not xs:
        return 0.0
    s2 = sum(x * x for x in xs)
    if s2 <= 0:
        return 0.0
    return (sum(xs) ** 2) / (len(xs) * s2)


def cov(xs: Sequence[float]) -> float:
    xs = [float(x) for x in xs]
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    if mu == 0:
        return 0.0
    var = sum((x - mu) ** 2 for x in xs) / len(xs)
    return math.sqrt(var) / mu


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    dx = [x - mx for x in xs[:n]]
    dy = [y - my for y in ys[:n]]
    den = math.sqrt(sum(a * a for a in dx) * sum(b * b for b in dy))
    if den == 0:
        return 0.0
    return sum(a * b for a, b in zip(dx, dy)) / den


def _ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Rank correlation: the bandwidth / pass-through relation is monotone
    but hyperbolic, so Pearson understates how tight it is."""
    return pearson(_ranks(list(xs)), _ranks(list(ys)))


def fairness_stats(inject_times: dict[int, list[int]], makespan: int,
                   n_flits_per_core: int) -> dict[str, Any]:
    """Per-core write bandwidth, measured while every core still has work.

    In a closed batch every core eventually injects the same `K*W` WriteData
    flits, so end-of-run counts are equal by construction and say nothing.
    The contention window is what matters: `t_fair` is the cycle the first
    core runs out of work, and up to that point all ten cores are competing
    for the same hops. Bandwidth over `[0, t_fair]` is therefore the honest
    per-core share. `finish` (each core's own last WriteData board) is
    reported alongside as the end-to-end view.
    """
    cs = sorted(inject_times)
    if not cs:
        return {}
    finish = {c: (max(ts) if ts else 0) for c, ts in inject_times.items()}
    t_fair = min(finish.values()) or 1
    got = {c: sum(1 for t in inject_times[c] if t <= t_fair) for c in cs}
    bw = {c: got[c] / t_fair for c in cs}
    vals = [bw[c] for c in cs]
    lo, hi = min(vals), max(vals)
    # Full-run view: a starved core simply takes longer to push its own quota.
    bw_run = {c: (n_flits_per_core / finish[c] if finish[c] else 0.0)
              for c in cs}
    vals_run = [bw_run[c] for c in cs]
    return {
        "t_fair": t_fair,
        "bw_by_core": {str(c): round(bw[c], 5) for c in cs},
        "got_by_core": {str(c): got[c] for c in cs},
        "finish_by_core": {str(c): finish[c] for c in cs},
        "bw_run_by_core": {str(c): round(bw_run[c], 5) for c in cs},
        "jain": round(jain(vals), 5),
        "max_min": round(hi / lo, 4) if lo > 0 else float("inf"),
        "cov": round(cov(vals), 5),
        "bw_min": round(lo, 5), "bw_max": round(hi, 5),
        "bw_mean": round(sum(vals) / len(vals), 5),
        "jain_run": round(jain(vals_run), 5),
        "max_min_run": (round(max(vals_run) / min(vals_run), 4)
                        if min(vals_run) > 0 else float("inf")),
        "throughput": round(len(cs) * n_flits_per_core / max(1, makespan), 4),
    }


# ---------------------------------------------------------------------------
# Analytic contention: who has to ride past whom
# ---------------------------------------------------------------------------

def pass_through_load(topo: Ring2Topology, txns: Sequence[Txn]
                      ) -> dict[str, Any]:
    """Flits that board elsewhere and then cross a node's outgoing hops.

    Walk each transaction's three legs along its shortest path. The first
    edge of a leg is the source's own on-ramp; every later edge is
    pass-through traffic, which under strict in-ring priority occupies an
    intermediate node's outgoing slot before that node can inject anything of
    its own. Planes are collapsed and divided by `n_planes`, because plane
    choice is a policy, not a physical fact.

    A core does not care equally about both of its outgoing hops: it only
    contends on the ones its own traffic routes over. `pt_eff` therefore
    weights each direction by the share of the core's own WriteData that
    leaves that way, which is the load the core actually has to fight.
    """
    n_planes = max(1, topo.n_planes)
    per_vc: dict[str, dict[tuple[int, int], float]] = {
        vc: defaultdict(float) for vc in ("req", "rsp", "dat")}
    own: dict[tuple[int, int], float] = defaultdict(float)
    for t in txns:
        legs = (("req", t.core, t.ha, M_REQ),
                ("rsp", t.ha, t.core, M_RSP),
                ("dat", t.core, t.ha, t.m_wdata))
        for vc, src, dst, m in legs:
            if src == dst or m <= 0:
                continue
            d = topo.choose_dir(src, dst)
            if vc == "dat":
                own[(src, d)] += m
            node = src
            for i in range(hop_count(src, dst, d, topo.n)):
                if i:                       # i == 0 is the source's own board
                    per_vc[vc][(node, d)] += m / n_planes
                node = (node + d) % topo.n

    by_vc = {vc: {n: round(tbl[(n, 1)] + tbl[(n, -1)], 2)
                  for n in range(topo.n)}
             for vc, tbl in per_vc.items()}
    by_vc["all"] = {n: round(sum(by_vc[v][n] for v in ("req", "rsp", "dat")),
                             2)
                    for n in range(topo.n)}
    # Injectors come from the workload: an odd node that is not memory can be
    # a core, so index parity is not the right thing to iterate over.
    srcs = sorted({t.core for t in txns}) or cores(topo.n)
    eff: dict[int, float] = {}
    for c in srcs:
        tot = own[(c, 1)] + own[(c, -1)]
        if tot <= 0:
            eff[c] = 0.0
            continue
        eff[c] = round(sum(
            (own[(c, d)] / tot) * sum(per_vc[v][(c, d)]
                                      for v in ("req", "rsp", "dat"))
            for d in (1, -1)), 2)
    return {
        "by_vc": by_vc,
        "eff": eff,
        "dat_cw": {n: round(per_vc["dat"][(n, 1)], 2) for n in range(topo.n)},
        "dat_ccw": {n: round(per_vc["dat"][(n, -1)], 2)
                    for n in range(topo.n)},
        "own_cw": {c: own[(c, 1)] for c in srcs},
        "own_ccw": {c: own[(c, -1)] for c in srcs},
    }


def hop_latency_by_core(topo: Ring2Topology) -> dict[int, float]:
    """Mean latency of each node's two outgoing links."""
    return {c: (topo.hop_lat_from(c, 1) + topo.hop_lat_from(c, -1)) / 2
            for c in range(topo.n)}


# ---------------------------------------------------------------------------
# Run one scheme
# ---------------------------------------------------------------------------

CORE_OUTSTANDING_WR = 128     # write study only; the read study keeps 100

# Every scheme rides the same fabric: one plane, latency-shortest routing
# (link-delay sum, then hops, then CW), a depth-8 shared up-ring FIFO plus
# one inject Q per direction, 1 flit/cycle board, a two-write / one-read
# leave buffer at 1 flit/cycle PE drain, and a finite request tracker.
# The tracker is part of the baseline, not of one scheme.
HA_RSP_JIT_LO = 4        # inclusive; each HA RSP / Comp is U{lo..hi}
HA_RSP_JIT = 64
# Dedicated congestion-bus delivery delay (S1 / S15). Not a ring hop.
FC_BUS_LAT = 30
FABRIC = dict(plane_sel="least_occupied", per_vc_srcq=True,
              per_vc_ports=True, shared_inj=True, two_write_leave=True,
              inj_depth=8, dir_inj_depth=1,
              core_outstanding=CORE_OUTSTANDING_WR, ha_track=RETRY_TRACK,
              outst_sample=OUTST_SAMPLE,
              ha_rsp_jit_lo=HA_RSP_JIT_LO, ha_rsp_jit=HA_RSP_JIT)
# Written before the run. Do not edit after seeing results.
STIMULUS_FORECAST = {
    "hypothesis": (
        "单平面、请求量 ×10、DBIDResp/RetryAck/Comp 各抽 U{4..64} 的均匀 tiled 写："
        "成功上环仍按最短路接近 1:1；"
        "失败次数比双平面更偏，邻 mem 少的核失败比 ≥ 2；"
        "S1 两边一起限速，改不了方向比，吞吐低于 S0。"
    ),
    "predicted": {
        "n_planes": 1,
        "ha_count_spread": 0,
        "s0_ok_ratio_lt": 1.5,
        "s0_fail_ratio_ge2_gap_cores": True,
        "gap_cores": [0, 8, 10, 18],
        "s1_thr_lt_s0": True,
        "thr_range": [1.2, 2.2],
    },
    "confidence": 0.55,
    "falsify": (
        "成功比 ≥ 2，或所有核失败比仍 < 2（与双平面一样），"
        "或 S1 吞吐不低于 S0"
    ),
}
# Written before the bus_lat=30 re-run. Do not edit after seeing results.
BUS_LAT_FORECAST = {
    "hypothesis": (
        "拥塞总线时延改为 30 拍（控制窗口 64 的一半）后，"
        "S1 用的是更旧的拥塞等级；上一轮总线=1 时 S1 已几乎等于 S0，"
        "更晚的反馈再拉开吞吐的空间很小。"
        "CW/CCW 失败比仍由邻 mem 几何决定，S1 改不了。"
    ),
    "predicted": {
        "s1_thr_delta_pct": [-1.0, 0.5],
        "s1_max_min": [1.07, 1.12],
        "fail_imbal_cores": [0, 8, 10, 18],
        "max_fail_ratio": [1.8, 2.0],
    },
    "confidence": 0.7,
    "falsify": "S1 吞吐相对 S0 掉超过 2%，或失败偏的核/方向翻面",
}
# Written before the per-VC-port re-run. Do not edit after seeing results.
VC_INDEP_FORECAST = {
    "hypothesis": (
        "REQ/RSP/DAT 上下环口拆开后，端口不再叠三 VC；"
        "均等最短路的上限改由最忙 DAT/RSP hop 决定，"
        "λ*=2/7，全环 WriteData R*=40/7≈5.714。"
        "S0 仍受在环优先，到不了这条线，但应明显高于共用端口时的 2.67。"
        "位置效应更明显；S1 按窗口×3 放大预算后仍接近 S0。"
    ),
    "predicted": {
        "s0_thr": [3.5, 5.5],
        "bound": 70000,
        "s0_max_min_gt": 1.08,
        "s1_thr_delta_pct": [-2.0, 1.0],
    },
    "confidence": 0.6,
    "falsify": "S0 吞吐仍 ≤ 2.8，或 bound 仍由合并端口的 75000 决定",
}
# Written before the shared-buffer / latency-route re-run. Do not edit after.
SHARED_BUF_FORECAST = {
    "hypothesis": (
        "上环两方向共用 8 深 FIFO，其后每向一个 inject Q；"
        "上环 / 下环各 1 flit/cycle/node，下环两写一读。"
        "端口重新合并，HA leave 叠三 VC，λ 上限靠近 4/15。"
        "共享 FIFO 可能 HOL：outstanding 卡住的 REQ 挡后面的 WriteData。"
        "时延最短在当前 link_lats 上与跳数最短重合（无严格方向翻转）。"
        "两写一读减少双向同时到站的偏转，但 PE 仍 1/cycle。"
    ),
    "predicted": {
        "s0_thr": [1.0, 2.4],
        "bound": 75000,
        "n_dir_flip_strict": 0,
        "merge_port_vcs": True,
    },
    "confidence": 0.5,
    "falsify": "S0 吞吐 ≥ 3.0（端口仍像拆开），或 bound 仍是 hop 的 70000",
}
# Written before the per-VC-port re-run. Do not edit after seeing results.
# Informed by a K=1000 probe, so the confidence is on the official-K numbers
# holding the *direction* the probe showed, not on the exact values.
PER_VC_PORT_FORECAST = {
    "hypothesis": (
        "三条 VC 不再共享上 / 下环端口：req/rsp/dat 各自 1 flit/cycle/node，"
        "整套上环结构按 VC 复制（每 VC 一个 8 深共享 FIFO + 每向 1 深 inject Q），"
        "下环也是每 VC 一个 4 深 buffer、两写一读。"
        "端口拆开后 port 界放松到 50000，bound 由 dat 的 link 界 70000 接管，"
        "R* 升到 400000/70000 ≈ 5.714。"
        "但吞吐**反而会降**：REQ 有了专用端口后到达 HA 快得多，"
        "32 深 tracker 被打得更凶，retry/txn 从 0.43 涨到接近 1，"
        "churn 吃掉端口拆分的收益。瓶颈更彻底地移到 completer。"
        "下环偏转几乎消失（每 VC 独立 leave buffer）。"
    ),
    "predicted": {
        "bound": 70000,
        "merge_port_vcs": False,
        "s0_thr": [2.0, 2.5],
        "s0_thr_below_merged": True,
        "retry_per_txn": [0.8, 1.1],
        "eject_defl_drops": True,
        "inf_track_thr_above_merged": True,
    },
    "confidence": 0.6,
    "falsify": (
        "S0 吞吐 ≥ 2.6（即端口拆分净赚，retry churn 没吃掉收益），"
        "或 retry/txn 仍 ≤ 0.6，或 bound 不是 70000"
    ),
}


def base_params() -> Ring2BaseParams:
    """Shared datapath settings. Every write scheme rides the same fabric."""
    return Ring2BaseParams(**FABRIC)


def make_sim(scheme: str, topo: Ring2Topology, *, seed: int,
             cfg: dict[str, Any] | None = None) -> Ring2BaseSim:
    # `cfg` overrides the shared fabric, so a sweep can move a datapath knob
    # (the outstanding cap, the completer tracker) as well as a scheme knob.
    kw = {**FABRIC, **(cfg or {})}
    if scheme == "S0":
        return Ring2BaseSim(topo, Ring2BaseParams(**kw), seed=seed)
    if scheme == "S16":
        from rg_ring2_grant import Ring2GrantParams, Ring2GrantSim
        # Withholding a grant is the whole mechanism, and it can only withhold
        # from below the tracker -- at or above it every arriving REQ that was
        # accepted is grantable and S16 is S0 under another name.
        kw = {"overcommit": S16_OVERCOMMIT, **kw}
        return Ring2GrantSim(topo, Ring2GrantParams(**kw), seed=seed)
    if scheme in ("S17", "S18", "S19", "S20"):
        from rg_ring2_rate import (Ring2DcqcnSim, Ring2DctcpSim,
                                   Ring2RateParams, Ring2SwiftSim,
                                   Ring2TimelySim)
        cls = {"S17": Ring2TimelySim, "S18": Ring2DcqcnSim,
               "S19": Ring2SwiftSim, "S20": Ring2DctcpSim}[scheme]
        return cls(topo, Ring2RateParams(**kw), seed=seed)
    from rg_ring2_fc import Ring2FcParams, Ring2FcSim
    kw = {"bus_lat": FC_BUS_LAT, **kw}
    p = Ring2FcParams(mode="s1" if scheme == "S1" else "s15", **kw)
    return Ring2FcSim(topo, p, seed=seed)


def run_scheme(scheme: str, topo: Ring2Topology, txns: Sequence[Txn], *,
               seed: int = 0, cfg: dict[str, Any] | None = None,
               t_max: int = T_MAX, quiet: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    sim = make_sim(scheme, topo, seed=seed, cfg=cfg)
    sim.offer_batch(txns)
    last_count, last_progress = 0, 0
    while sim.t < t_max and not sim.done():
        sim.step()
        if sim.st["n_delivered_flits"] != last_count:
            last_count = sim.st["n_delivered_flits"]
            last_progress = sim.t
        elif sim.t - last_progress > 100_000:
            break
    r = sim.summary()
    r["scheme"] = scheme
    r["stall_detected"] = not r["completed"]
    r["hop_starts"] = list(sim.hop_starts)
    r["cfg"] = dict(cfg or {})
    r["wall_secs"] = round(time.perf_counter() - t0, 1)
    extra = getattr(sim, "fc_summary", None)
    if callable(extra):
        r["fc"] = extra()
    if not quiet:
        print(f"    {scheme} mk={r['makespan']} ok={r['completed']} "
              f"{r['wall_secs']}s", flush=True)
    return r


def digest(r: dict[str, Any], *, flits_per_core: int, bin_w: int
           ) -> dict[str, Any]:
    """Trim a raw run down to what the report needs."""
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    fair = fairness_stats(inj, r.get("makespan") or 1, flits_per_core)
    t_max = r.get("makespan") or 1
    binned = {}
    for c, ts in sorted(inj.items()):
        xs, ys = bin_rate(ts, t_max, bin_w)
        binned[str(c)] = {"t": xs, "rate": [round(y, 4) for y in ys],
                          "n": len(ts)}
    hop_xs, hop_ys = bin_rate(r.get("hop_starts") or [], t_max, bin_w)
    out = {
        "scheme": r["scheme"], "cfg": r.get("cfg", {}),
        "makespan": r.get("makespan"), "completed": r.get("completed"),
        "n_delivered_flits": r.get("n_delivered_flits"),
        "n_txn_done": r.get("n_txn_done"), "n_txn_target": r.get("n_txn_target"),
        "n_board_fail": r.get("n_board_fail", 0),
        "n_deflections": r.get("n_deflections", 0),
        "n_eject_full_deflect": r.get("n_eject_full_deflect", 0),
        "n_inring_blocked": r.get("n_inring_blocked", 0),
        "max_inring_hold": r.get("max_inring_hold", 0),
        "n_itag_raised": r.get("n_itag_raised", 0),
        "n_etag_raised": r.get("n_etag_raised", 0),
        "n_outst_wait": r.get("n_outst_wait", 0),
        "max_core_outstanding": r.get("max_core_outstanding", 0),
        "max_srcq": r.get("max_srcq"), "max_ejectq": r.get("max_ejectq"),
        "lat_p50": r.get("lat_p50"), "lat_p99": r.get("lat_p99"),
        "lat_max": r.get("lat_max"),
        "wall_secs": r.get("wall_secs"),
        "fairness": fair,
        "wr_binned": binned,
        "board_fail_by_src": r.get("board_fail_by_src", {}),
        "inj_by_hop": r.get("inj_by_hop", {}),
        "wr_recv_by_ha": {str(h): len(v)
                          for h, v in (r.get("wr_recv_by_ha") or {}).items()},
        "hop_bw": {"t": hop_xs, "rate": [round(y, 3) for y in hop_ys],
                   "n_hops": len(r.get("hop_starts") or [])},
        "board_dir": board_dir_from_inj(r.get("inj_by_hop") or {},
                                        sorted(inj)),
    }
    if "fc" in r:
        out["fc"] = r["fc"]
    if "retry" in r:
        out["retry"] = r["retry"]
    return out


def board_dir_from_inj(inj_by_hop: dict[str, dict[str, int]],
                       cores: Sequence[int]) -> dict[str, dict[str, int]]:
    """Per-core inject wins/losses split by CW (+1) and CCW (−1)."""
    out: dict[str, dict[str, int]] = {}
    for c in cores:
        cw = inj_by_hop.get(f"{c}:1") or {}
        ccw = inj_by_hop.get(f"{c}:-1") or {}
        ok_cw, ok_ccw = int(cw.get("ok", 0)), int(ccw.get("ok", 0))
        fl_cw, fl_ccw = int(cw.get("fail", 0)), int(ccw.get("fail", 0))
        out[str(c)] = {
            "ok_cw": ok_cw, "ok_ccw": ok_ccw,
            "fail_cw": fl_cw, "fail_ccw": fl_ccw,
            "ok": ok_cw + ok_ccw, "fail": fl_cw + fl_ccw,
        }
    return out


def dir_imbalanced(a: int, b: int, *, min_n: int = 50, ratio: float = 2.0
                   ) -> bool:
    """True when one direction has at least `ratio` times the other."""
    if a + b < min_n:
        return False
    lo = min(a, b)
    return True if lo == 0 else max(a, b) / lo >= ratio


def _dir_ratio(a: int, b: int) -> float:
    if a + b <= 0:
        return 0.0
    lo = min(a, b)
    return float("inf") if lo == 0 else max(a, b) / lo


def uniform_belief(pats: dict[str, Any]) -> dict[str, Any]:
    uni = pats.get("uniform") or {}
    s0u = (uni.get("schemes") or {}).get("S0") or {}
    s1u = (uni.get("schemes") or {}).get("S1") or {}
    return {
        "s0_thr": (s0u.get("fairness") or {}).get("throughput"),
        "s1_thr": (s1u.get("fairness") or {}).get("throughput"),
        "s0_max_min": (s0u.get("fairness") or {}).get("max_min"),
        "s1_max_min": (s1u.get("fairness") or {}).get("max_min"),
        "s0_board": board_dir_belief(s0u.get("board_dir") or {}),
        "s1_board": board_dir_belief(s1u.get("board_dir") or {}),
        "s1_bus_lat": ((s1u.get("fc") or {}).get("bus_lat")),
    }


def board_dir_belief(board_dir: dict[str, dict[str, int]]
                     ) -> dict[str, Any]:
    """Summarise CW/CCW imbalance without rewriting the forecast."""
    ok_imbal, fail_imbal = [], []
    max_ok, max_fail = 0.0, 0.0
    for c, r in board_dir.items():
        ok_r = _dir_ratio(int(r.get("ok_cw", 0)), int(r.get("ok_ccw", 0)))
        fl_r = _dir_ratio(int(r.get("fail_cw", 0)), int(r.get("fail_ccw", 0)))
        max_ok, max_fail = max(max_ok, ok_r), max(max_fail, fl_r)
        if dir_imbalanced(int(r.get("ok_cw", 0)), int(r.get("ok_ccw", 0))):
            ok_imbal.append(int(c))
        if dir_imbalanced(int(r.get("fail_cw", 0)), int(r.get("fail_ccw", 0))):
            fail_imbal.append(int(c))
    return {
        "ok_imbal_cores": sorted(ok_imbal),
        "fail_imbal_cores": sorted(fail_imbal),
        "max_ok_ratio": None if max_ok == float("inf") else round(max_ok, 3),
        "max_fail_ratio": (None if max_fail == float("inf")
                           else round(max_fail, 3)),
        "n_ok_imbal": len(ok_imbal),
        "n_fail_imbal": len(fail_imbal),
    }


def bin_rate(times: Sequence[int], t_max: int, bin_w: int = BIN_W
             ) -> tuple[list[int], list[float]]:
    nbin = max(1, (t_max + bin_w) // bin_w)
    rate = [0.0] * nbin
    for t in times:
        rate[min(max(int(t), 0) // bin_w, nbin - 1)] += 1.0 / bin_w
    return [i * bin_w for i in range(nbin)], rate


# ---------------------------------------------------------------------------
# Root-cause table
# ---------------------------------------------------------------------------

def root_cause(topo: Ring2Topology, txns: Sequence[Txn],
               s0: dict[str, Any]) -> dict[str, Any]:
    pt = pass_through_load(topo, txns)
    lat = hop_latency_by_core(topo)
    bw = {int(c): v for c, v in s0["fairness"]["bw_by_core"].items()}
    # Which nodes inject is a property of the workload, not of index parity:
    # once some odd node stops being memory it can be a core instead.
    cs = sorted(bw) or cores(topo.n)
    fails = s0.get("board_fail_by_src", {})

    def cause(c: int, key: str) -> int:
        return int(fails.get(f"{c}:dat", {}).get(key, 0))

    rows = []
    for c in cs:
        ok = cause(c, "ok")
        busy, itag = cause(c, "hop_busy"), cause(c, "itag")
        budget, outst = cause(c, "fc_budget"), cause(c, "outstanding")
        tries = ok + busy + itag + budget + outst
        rows.append({
            "core": c,
            "bw": bw.get(c, 0.0),
            "pt_eff": pt["eff"][c],
            "pt_dat": pt["by_vc"]["dat"][c],
            "pt_all": pt["by_vc"]["all"][c],
            "lat_out": lat[c],
            "ok": ok, "hop_busy": busy, "itag": itag,
            "fc_budget": budget, "outstanding": outst,
            "succ_rate": round(ok / tries, 4) if tries else 0.0,
        })
    bws = [r["bw"] for r in rows]
    # How many memory nodes sit right next to each core. A write to a
    # neighbour occupies one segment and then leaves the ring; everything
    # else has to ride deeper, blocking more nodes and being blocked more.
    # Mean distance to memory cannot see this -- remove an antipodal pair of
    # memory nodes and every core's mean distance is still identical.
    mem = sorted({t.ha for t in txns})
    adj = [sum(1 for h in mem
               if min((h - c) % topo.n, (c - h) % topo.n) == 1) for c in cs]
    mean_hop = [sum(min((h - c) % topo.n, (c - h) % topo.n)
                    for h in mem) / max(1, len(mem)) for c in cs]
    for r, a, mh in zip(rows, adj, mean_hop):
        r["adj_mem"] = a
        r["mean_hop_to_mem"] = round(mh, 2)
    hops = []
    for key, v in (s0.get("inj_by_hop") or {}).items():
        tot = v["ok"] + v["fail"]
        if tot:
            hops.append({"hop": key, "lat": v["lat"], "ok": v["ok"],
                         "fail": v["fail"],
                         "succ": round(v["ok"] / tot, 4)})
    return {
        "rows": rows,
        "pass_through": {
            "by_vc": {vc: {str(k): v for k, v in d.items()}
                      for vc, d in pt["by_vc"].items()},
            "eff": {str(k): v for k, v in pt["eff"].items()},
            "dat_cw": {str(k): v for k, v in pt["dat_cw"].items()},
            "dat_ccw": {str(k): v for k, v in pt["dat_ccw"].items()},
        },
        "corr_bw_pt_eff": round(pearson(bws, [r["pt_eff"] for r in rows]), 4),
        "rank_bw_pt_eff": round(spearman(bws, [r["pt_eff"] for r in rows]), 4),
        "rank_bw_succ": round(spearman(bws, [r["succ_rate"] for r in rows]), 4),
        "corr_bw_pt_dat": round(pearson(bws, [r["pt_dat"] for r in rows]), 4),
        "corr_bw_pt_all": round(pearson(bws, [r["pt_all"] for r in rows]), 4),
        "corr_bw_lat": round(pearson(bws, [r["lat_out"] for r in rows]), 4),
        "corr_bw_succ": round(pearson(bws, [r["succ_rate"] for r in rows]), 4),
        "corr_bw_adjmem": round(pearson(bws, [float(a) for a in adj]), 4),
        "rank_bw_adjmem": round(spearman(bws, [float(a) for a in adj]), 4),
        "corr_bw_meanhop": round(pearson(bws, mean_hop), 4),
        "mem": mem,
        "hops": sorted(hops, key=lambda h: h["lat"]),
        "corr_succ_lat": round(pearson([h["succ"] for h in hops],
                                       [float(h["lat"]) for h in hops]), 4),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

# The ring is a closed full ring (19 wraps onto 0). Nodes 9 and 19 sit on
# that ring and forward, but they are neither memory nor AI core — they
# never source or sink a write. The memory set is the eight remaining odd
# nodes, so the role map is no longer rotationally symmetric.
NON_TERMINAL = (9, 19)
MEM_NODES = tuple(h for h in has(N_NODES) if h not in NON_TERMINAL)
CORE_NODES = tuple(cores(N_NODES))


def build_pattern(name: str, *, k: int, W: int, seed: int) -> list[Txn]:
    """The workloads under study.

    `uniform` is the headline one: 10 AI cores walking a 128B / 4KB / 64KB
    tiled write whose channel hash already balances the 8 memory nodes.
    `hot` keeps the same roles but funnels every write into one two-node
    memory cluster, which loads the completers far harder for the same
    injection rate. It exists to show that the best outstanding cap is a
    property of the workload, not of the fabric.
    """
    if name == "uniform":
        return build_tiled_write(k=k, m_wdata=W, mem=MEM_NODES,
                                 core_set=CORE_NODES)
    if name == "hot":
        return build_hot_write(k=k, m_wdata=W, hot_has=HOT_HAS, n=N_NODES)
    raise ValueError(f"unknown pattern {name}")


# ---------------------------------------------------------------------------
# Retry / reordering / effective outstanding
# ---------------------------------------------------------------------------

def _rate_knobs() -> dict[str, Any]:
    """The tunings S17 / S18 actually ran with, so the report cannot drift."""
    from rg_ring2_rate import Ring2RateParams
    p = Ring2RateParams()
    return {k: getattr(p, k) for k in
            ("pace_max", "pace_min", "pace_burst", "t_low_mult",
             "t_high_mult", "timely_beta", "delta", "hai_n", "k_min", "k_max",
             "p_max", "g", "alpha_timer", "rate_timer", "fast_recovery",
             "win_init", "win_min", "win_max", "swift_t_mult",
             "swift_rtt_floor", "swift_beta", "swift_ai", "dctcp_g")}


def retry_point(scheme: str, topo: Ring2Topology, txns: Sequence[Txn], *,
                seed: int, k: int, W: int, cfg: dict[str, Any],
                keep_trace: bool = False) -> dict[str, Any]:
    """One grid point: what the cap bought, and what the retries cost."""
    r = run_scheme(scheme, topo, txns, seed=seed, cfg=cfg, quiet=True)
    f = fairness_stats(r.get("wr_inject_by_core") or {}, r["makespan"] or 1,
                       k * W)
    q = r.get("retry") or {}
    fc = r.get("fc") or {}
    row = {"trace": fc.get("trace")} if keep_trace else {}
    return {
        **row,
        "scheme": scheme,
        "core_outstanding": cfg.get("core_outstanding"),
        "ha_track": cfg.get("ha_track", 0),
        "inorder_retire": bool(cfg.get("inorder_retire")),
        "makespan": r.get("makespan"), "completed": r.get("completed"),
        "throughput": f.get("throughput", 0.0), "jain": f.get("jain", 0.0),
        "max_min": f.get("max_min", 0.0), "bw_min": f.get("bw_min", 0.0),
        "lat_p50": r.get("lat_p50"), "lat_p99": r.get("lat_p99"),
        "n_retry": q.get("n_retry", 0),
        "retry_per_txn": q.get("retry_per_txn", 0.0),
        "max_ha_used": q.get("max_ha_used", 0),
        "ooo_frac": q.get("ooo_frac", 0.0),
        "ooo_mean_disp": q.get("ooo_mean_disp", 0.0),
        "ooo_max_disp": q.get("ooo_max_disp", 0),
        "retire_ooo": q.get("retire_ooo_frac", 0.0),
        "outst_eff": q.get("outst_eff_mean", 0.0),
        "outst_used": q.get("outst_used_mean", 0.0),
        "outst_park": q.get("outst_park_mean", 0.0),
        "outst_hol": q.get("outst_hol_mean", 0.0),
        "max_hol_hold": q.get("max_hol_hold", 0),
        "rate_mean": fc.get("rate_mean_all"),
        "n_mark": fc.get("n_mark"),
        "n_board_fail": r.get("n_board_fail", 0),
        "wall_secs": r.get("wall_secs"),
    }


def _say(tag: str, row: dict[str, Any]) -> None:
    print(f"    {tag} mk={row['makespan']} thr={row['throughput']:.3f} "
          f"retry/txn={row['retry_per_txn']:.3f} "
          f"eff={row['outst_eff']:.1f}/{row['outst_used']:.1f} "
          f"ooo={row['ooo_frac']:.3f} max/min={row['max_min']}", flush=True)


def retry_study(topo: Ring2Topology, *, k: int, W: int, seed: int,
                patterns: Sequence[str] = ("uniform", "hot")
                ) -> dict[str, Any]:
    """Give the completers a finite tracker and sweep the outstanding cap.

    Three views. `sweep_outst` is the headline: the cap has an interior
    optimum, and where it sits depends on the workload. `sweep_track` shows
    the same tension from the completer's side. `ablate_order` separates the
    two ways reordering wastes an outstanding slot -- the slot parked waiting
    for a protocol credit, and the slot of a finished transaction held back
    behind an older one.
    """
    out: dict[str, Any] = {
        "meta": {"K": k, "W": W, "seed": seed, "ha_track": RETRY_TRACK,
                 "s16_overcommit": S16_OVERCOMMIT,
                 "outst_points": list(OUTST_POINTS),
                 "track_points": list(TRACK_POINTS),
                 "schemes": list(RETRY_SCHEMES),
                 "patterns": list(patterns),
                 "outst_sample": OUTST_SAMPLE,
                 "headline_outst": CORE_OUTSTANDING_WR,
                 "hot_has": list(HOT_HAS),
                 "knobs": _rate_knobs()},
        "sweep_outst": [], "sweep_track": [], "ablate_order": [],
        "sweep_rate": [], "rate_trace": {},
    }
    for pattern in patterns:
        txns = build_pattern(pattern, k=k, W=W, seed=seed)
        print(f"\n  [retry:{pattern}] outstanding sweep, "
              f"ha_track={RETRY_TRACK}", flush=True)
        for scheme in RETRY_SCHEMES:
            for oc in OUTST_POINTS:
                # Keep the controller's own trace at the headline cap only:
                # it is what the rate plot draws, and one run of it is enough.
                keep = (scheme in ("S17", "S18") and pattern == patterns[0]
                        and oc == CORE_OUTSTANDING_WR)
                row = retry_point(
                    scheme, topo, txns, seed=seed, k=k, W=W, keep_trace=keep,
                    cfg={"core_outstanding": oc, "ha_track": RETRY_TRACK,
                         "outst_sample": OUTST_SAMPLE})
                row["pattern"] = pattern
                if keep:
                    out["rate_trace"][scheme] = row.pop("trace", None)
                out["sweep_outst"].append(row)
                _say(f"{pattern} {scheme} outst={oc}", row)

    txns = build_pattern(patterns[0], k=k, W=W, seed=seed)
    print(f"\n  [retry:{patterns[0]}] tracker sweep, outstanding="
          f"{CORE_OUTSTANDING_WR}", flush=True)
    for track in TRACK_POINTS:
        row = retry_point(
            "S0", topo, txns, seed=seed, k=k, W=W,
            cfg={"core_outstanding": CORE_OUTSTANDING_WR, "ha_track": track,
                 "outst_sample": OUTST_SAMPLE})
        row["pattern"] = patterns[0]
        out["sweep_track"].append(row)
        _say(f"S0 ha_track={track or 'inf'}", row)

    print(f"\n  [retry:{patterns[0]}] static injection rate, "
          f"outstanding={CORE_OUTSTANDING_WR}", flush=True)
    for rate in RATE_POINTS:
        row = retry_point(
            "S17", topo, txns, seed=seed, k=k, W=W,
            cfg={"core_outstanding": CORE_OUTSTANDING_WR,
                 "ha_track": RETRY_TRACK, "outst_sample": OUTST_SAMPLE,
                 "pace_min": rate, "pace_init": rate, "pace_max": rate})
        row["pattern"] = patterns[0]
        row["scheme"] = "static"
        row["pace"] = rate
        out["sweep_rate"].append(row)
        _say(f"pinned rate={rate}", row)

    print("\n  [retry] in-order retirement ablation", flush=True)
    for track in (0, RETRY_TRACK):
        for inorder in (False, True):
            row = retry_point(
                "S0", topo, txns, seed=seed, k=k, W=W,
                cfg={"core_outstanding": CORE_OUTSTANDING_WR,
                     "ha_track": track, "inorder_retire": inorder,
                     "outst_sample": OUTST_SAMPLE})
            row["pattern"] = patterns[0]
            out["ablate_order"].append(row)
            _say(f"S0 ha_track={track or 'inf'} inorder={int(inorder)}", row)
    return out


# ---------------------------------------------------------------------------
# Congestion reproduction: ost collapse (ex1) and innocent-flow block (ex2)
# ---------------------------------------------------------------------------
# Forecasts are part of the source. Do not edit them after a run; the
# belief_update field is what records the surprise.

REPRO_BLOCKERS = (2, 4, 6, 8)
REPRO_BLOCK_HA = 11
REPRO_VICTIM = 10
REPRO_VICTIM_HA = 15
REPRO_CONTROL = 16
REPRO_CONTROL_HA = 17

REPRO_FORECAST = {
    "ex1": {
        "hypothesis": (
            "oc=16: used≈eff, flat near the cap, retry=0, like silicon ost=600. "
            "oc=128/256: used high and jittery, park large, eff pinned ~23, "
            "write-BW tracks eff and sits below the oc=16 run."
        ),
        "predicted": {
            "oc16_retry": 0.0,
            "oc16_eff_used_gap": "<0.5",
            "oc128_eff": [20, 26],
            "oc128_used": [80, 120],
            "oc128_thr_lt_oc16": True,
            "bw_eff_corr": [0.6, 1.0],
        },
        "confidence": 0.85,
        "falsify": "oc=128 eff near the cap, or BW does not track eff",
    },
    "ex2": {
        "hypothesis": (
            "CW blockers 2/4/6/8→M11 occupy hop 10→11, so victim C10→M15 "
            "loses inject slots; control C16→M17 shares no hop and barely moves. "
            "Unlimited tracker puts more WriteData on the ring (higher eject "
            "deflect at M11); tracker=32 adds REQ/Retry circling instead."
        ),
        "predicted": {
            "victim_drop_pct": [30, 90],
            "control_drop_pct": [-5, 15],
            "eject_defl_m11_with_blockers": ">>0",
        },
        "confidence": 0.65,
        "falsify": "victim throughput unchanged, or control drops as much as victim",
    },
}


def _directed_hops(src: int, dst: int, n: int = N_NODES,
                   topo: Ring2Topology | None = None) -> list[tuple[int, int]]:
    d = topo.choose_dir(src, dst) if topo is not None else shortest_dir(src, dst, n)
    hops, i = [], src
    for _ in range(hop_count(src, dst, d, n)):
        nxt = (i + d) % n
        hops.append((i, nxt))
        i = nxt
    return hops


def build_blocker_write(*, k: int, W: int, blockers: bool) -> list[Txn]:
    """Victim and control always; the four CW writers to M11 are optional."""
    out: list[Txn] = []
    tid = 0
    roles = [(REPRO_VICTIM, REPRO_VICTIM_HA),
             (REPRO_CONTROL, REPRO_CONTROL_HA)]
    if blockers:
        roles = [(c, REPRO_BLOCK_HA) for c in REPRO_BLOCKERS] + roles
    for core, ha in roles:
        for _ in range(k):
            out.append(Txn(tid, core, ha, 1, 0, "write", W))
            tid += 1
    return out


def _role_row(r: dict[str, Any], core: int, k: int, W: int) -> dict[str, Any]:
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    ts = inj.get(core) or []
    finish = max(ts) if ts else 0
    fails = r.get("board_fail_by_src") or {}
    dat = fails.get(f"{core}:dat") or {}
    req = fails.get(f"{core}:req") or {}
    hop = (r.get("inj_by_hop") or {}).get(f"{core}:1") or {}
    n_wr = k * W
    return {
        "core": core,
        "n_wr": len(ts),
        "finish": finish,
        "bw_run": round(n_wr / finish, 5) if finish else 0.0,
        "hop_busy_dat": int(dat.get("hop_busy", 0)),
        "hop_busy_req": int(req.get("hop_busy", 0)),
        "ok_dat": int(dat.get("ok", 0)),
        "inj_ok": int(hop.get("ok", 0)),
        "inj_fail": int(hop.get("fail", 0)),
    }


def _ost_series(q: dict[str, Any]) -> dict[str, Any]:
    tr = q.get("ost_trace") or {}
    t = tr.get("t") or []
    used, park, hol, eff = (tr.get(k) or [] for k in
                            ("used", "park", "hol", "eff"))
    mean = []
    for row in (used, park, hol, eff):
        mean.append([round(sum(xs) / max(1, len(xs)), 3) for xs in row])
    return {
        "t": t,
        "cores": tr.get("cores") or [],
        "used": used, "park": park, "hol": hol, "eff": eff,
        "used_mean": mean[0], "park_mean": mean[1],
        "hol_mean": mean[2], "eff_mean": mean[3],
    }


def _bw_eff_corr(wr_t: Sequence[int], ost_t: Sequence[int],
                 ost_eff: Sequence[float], bin_w: int, t_max: int) -> float:
    xs, rate = bin_rate(wr_t, t_max, bin_w)
    if len(xs) < 4 or not ost_t:
        return 0.0
    # Align each BW bin to the last ost sample at or before the bin centre.
    aligned = []
    j = 0
    for x in xs:
        mid = x + bin_w // 2
        while j + 1 < len(ost_t) and ost_t[j + 1] <= mid:
            j += 1
        aligned.append(ost_eff[j] if ost_eff else 0.0)
    # Drop the empty tail after the last write.
    last = max((i for i, y in enumerate(rate) if y > 0), default=0)
    return round(pearson(rate[:last + 1], aligned[:last + 1]), 4)


def _ost_point(scheme: str, topo: Ring2Topology, txns: Sequence[Txn], *,
               seed: int, k: int, W: int, cfg: dict[str, Any],
               bin_w: int) -> dict[str, Any]:
    r = run_scheme(scheme, topo, txns, seed=seed, cfg=cfg, quiet=True)
    q = r.get("retry") or {}
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    all_wr = [t for ts in inj.values() for t in ts]
    f = fairness_stats(inj, r["makespan"] or 1, k * W)
    tr = _ost_series(q)
    row = {
        "scheme": scheme,
        "core_outstanding": cfg.get("core_outstanding"),
        "ha_track": cfg.get("ha_track", RETRY_TRACK),
        "inorder_retire": bool(cfg.get("inorder_retire")),
        "makespan": r.get("makespan"),
        "completed": r.get("completed"),
        "throughput": f.get("throughput", 0.0),
        "retry_per_txn": q.get("retry_per_txn", 0.0),
        "ooo_frac": q.get("ooo_frac", 0.0),
        "max_min": f.get("max_min", 0.0),
        "outst_eff": q.get("outst_eff_mean", 0.0),
        "outst_used": q.get("outst_used_mean", 0.0),
        "outst_park": q.get("outst_park_mean", 0.0),
        "outst_hol": q.get("outst_hol_mean", 0.0),
        "max_hol_hold": q.get("max_hol_hold", 0),
        "n_deflections": r.get("n_deflections", 0),
        "n_eject_full_deflect": r.get("n_eject_full_deflect", 0),
        "wr_binned": {"t": [], "rate": []},
        "ost": tr,
        "bw_eff_corr": 0.0,
        "wall_secs": r.get("wall_secs"),
    }
    if all_wr:
        xs, ys = bin_rate(all_wr, r["makespan"] or 1, bin_w)
        row["wr_binned"] = {"t": xs, "rate": [round(y, 4) for y in ys]}
        row["bw_eff_corr"] = _bw_eff_corr(
            all_wr, tr["t"], tr["eff_mean"], bin_w, r["makespan"] or 1)
    return row


def _blocker_point(topo: Ring2Topology, txns: Sequence[Txn], *,
                   seed: int, k: int, W: int, cfg: dict[str, Any],
                   tag: str) -> dict[str, Any]:
    r = run_scheme("S0", topo, txns, seed=seed, cfg=cfg, quiet=True)
    defl = r.get("n_eject_defl_by_dst") or {}
    victim = _role_row(r, REPRO_VICTIM, k, W)
    control = _role_row(r, REPRO_CONTROL, k, W)
    print(f"    {tag} mk={r['makespan']} "
          f"V.bw={victim['bw_run']:.3f} C.bw={control['bw_run']:.3f} "
          f"defl={r.get('n_deflections', 0)} "
          f"eject@{REPRO_BLOCK_HA}={defl.get(str(REPRO_BLOCK_HA), 0)}",
          flush=True)
    return {
        "tag": tag,
        "ha_track": cfg.get("ha_track"),
        "core_outstanding": cfg.get("core_outstanding"),
        "makespan": r.get("makespan"),
        "completed": r.get("completed"),
        "n_deflections": r.get("n_deflections", 0),
        "n_eject_full_deflect": r.get("n_eject_full_deflect", 0),
        "n_eject_defl_hot": int(defl.get(str(REPRO_BLOCK_HA), 0)),
        "n_retry": (r.get("retry") or {}).get("n_retry", 0),
        "victim": victim,
        "control": control,
        "wall_secs": r.get("wall_secs"),
    }


def congestion_repro(topo: Ring2Topology, *, k: int, W: int, seed: int
                     ) -> dict[str, Any]:
    """Reproduce the two over-injection stories with traces, not just means.

    Example 1 is the same uniform write as the retry study, but this time the
    outstanding samples are kept as a series so bandwidth can be overlaid on
    effective ost the way the silicon plots do. Example 2 is a three-role
    pattern that the closed-batch uniform/hot mixes cannot isolate.
    """
    bin_w = 32
    oc_points = (16, 128, 256)
    out: dict[str, Any] = {
        "meta": {
            "K": k, "W": W, "seed": seed, "ha_track": RETRY_TRACK,
            "outst_sample": OUTST_SAMPLE, "bin_w": bin_w,
            "oc_points": list(oc_points),
            "blockers": list(REPRO_BLOCKERS),
            "block_ha": REPRO_BLOCK_HA,
            "victim": REPRO_VICTIM, "victim_ha": REPRO_VICTIM_HA,
            "control": REPRO_CONTROL, "control_ha": REPRO_CONTROL_HA,
            "victim_hops": _directed_hops(REPRO_VICTIM, REPRO_VICTIM_HA, topo=topo),
            "control_hops": _directed_hops(REPRO_CONTROL, REPRO_CONTROL_HA, topo=topo),
            "blocker_hops": {
                str(c): _directed_hops(c, REPRO_BLOCK_HA, topo=topo)
                for c in REPRO_BLOCKERS
            },
            "forecast": REPRO_FORECAST,
        },
        "ost": [],
        "blocker": [],
    }
    shared = {REPRO_VICTIM_HA: _directed_hops(
        REPRO_VICTIM, REPRO_VICTIM_HA, topo=topo)[:1]}
    out["meta"]["shared_hop"] = shared[REPRO_VICTIM_HA]
    # Geometry pin: every blocker must ride the victim's first hop; control
    # must not. If this ever fails the experiment is measuring the wrong thing.
    v0 = tuple(out["meta"]["victim_hops"][0])
    assert all(v0 in hops for hops in out["meta"]["blocker_hops"].values()), \
        out["meta"]["blocker_hops"]
    assert v0 not in out["meta"]["control_hops"], out["meta"]["control_hops"]

    txns = build_pattern("uniform", k=k, W=W, seed=seed)
    print(f"\n  [repro:ex1] outstanding traces, ha_track={RETRY_TRACK}",
          flush=True)
    for oc in oc_points:
        row = _ost_point(
            "S0", topo, txns, seed=seed, k=k, W=W, bin_w=bin_w,
            cfg={"core_outstanding": oc, "ha_track": RETRY_TRACK,
                 "outst_sample": OUTST_SAMPLE, "outst_trace": True})
        out["ost"].append(row)
        _say(f"repro outst={oc}", row)
        print(f"      bw~eff r={row['bw_eff_corr']}", flush=True)
    row = _ost_point(
        "S0", topo, txns, seed=seed, k=k, W=W, bin_w=bin_w,
        cfg={"core_outstanding": 128, "ha_track": RETRY_TRACK,
             "inorder_retire": True, "outst_sample": OUTST_SAMPLE,
             "outst_trace": True})
    row["tag"] = "inorder"
    out["ost"].append(row)
    _say("repro outst=128 inorder", row)

    print("\n  [repro:ex2] innocent-flow vs circling blockers", flush=True)
    cfg0 = {"core_outstanding": CORE_OUTSTANDING_WR, "ha_track": RETRY_TRACK,
            "outst_sample": 0}
    solo = build_blocker_write(k=k, W=W, blockers=False)
    both = build_blocker_write(k=k, W=W, blockers=True)
    out["blocker"].append(_blocker_point(
        topo, solo, seed=seed, k=k, W=W, cfg=cfg0, tag="solo"))
    out["blocker"].append(_blocker_point(
        topo, both, seed=seed, k=k, W=W,
        cfg={**cfg0, "ha_track": 0}, tag="blockers_track0"))
    out["blocker"].append(_blocker_point(
        topo, both, seed=seed, k=k, W=W, cfg=cfg0, tag="blockers_track32"))

    by_oc = {r["core_outstanding"]: r for r in out["ost"]
             if not r.get("tag")}
    lo, hi = by_oc[16], by_oc[128]
    by_tag = {r["tag"]: r for r in out["blocker"]}
    v0 = by_tag["solo"]["victim"]["bw_run"]
    c0 = by_tag["solo"]["control"]["bw_run"]
    v32 = by_tag["blockers_track32"]["victim"]["bw_run"]
    c32 = by_tag["blockers_track32"]["control"]["bw_run"]
    out["belief_update"] = {
        "ex1": {
            "oc16_retry": lo["retry_per_txn"],
            "oc16_eff": lo["outst_eff"], "oc16_used": lo["outst_used"],
            "oc128_retry": hi["retry_per_txn"],
            "oc128_eff": hi["outst_eff"], "oc128_used": hi["outst_used"],
            "oc128_thr_lt_oc16": hi["throughput"] < lo["throughput"],
            "bw_eff_corr_16": lo["bw_eff_corr"],
            "bw_eff_corr_128": hi["bw_eff_corr"],
        },
        "ex2": {
            "victim_drop_pct": round(100.0 * (v32 - v0) / max(1e-9, v0), 1),
            "control_drop_pct": round(100.0 * (c32 - c0) / max(1e-9, c0), 1),
            "eject_defl_m11_track32": by_tag["blockers_track32"][
                "n_eject_defl_hot"],
            "eject_defl_m11_track0": by_tag["blockers_track0"][
                "n_eject_defl_hot"],
        },
    }
    return out


def seed_sweep(pattern: str, topo: Ring2Topology, *, k: int, W: int,
               seeds: Sequence[int], schemes: Sequence[str]
               ) -> list[dict[str, Any]]:
    """Re-run the headline schemes on other seeds.

    The reservation mechanism is discrete, so a single seed can flatter a
    tuning point. Report the spread instead.
    """
    rows: list[dict[str, Any]] = []
    for sd in seeds:
        txns = build_pattern(pattern, k=k, W=W, seed=sd)
        row: dict[str, Any] = {"seed": sd}
        thr0 = None
        for scheme in schemes:
            r = run_scheme(scheme, topo, txns, seed=sd, quiet=True)
            f = fairness_stats(r["wr_inject_by_core"], r["makespan"], k * W)
            thr0 = f["throughput"] if scheme == "S0" else thr0
            row[scheme] = {
                "jain": f["jain"], "max_min": f["max_min"],
                "throughput": f["throughput"],
                "thr_delta_pct": (
                    round(100.0 * (f["throughput"] - thr0) / thr0, 2)
                    if thr0 else 0.0),
            }
        rows.append(row)
        print(f"    seed {sd}: " + "  ".join(
            f"{s} jain={row[s]['jain']} mm={row[s]['max_min']} "
            f"({row[s]['thr_delta_pct']:+.2f}%)" for s in schemes), flush=True)
    return rows


def run_pattern(pattern: str, topo: Ring2Topology, *, k: int, W: int,
                seed: int, bin_w: int, schemes: Sequence[str],
                sweep_s1: bool, seeds: Sequence[int] = ()) -> dict[str, Any]:
    txns = build_pattern(pattern, k=k, W=W, seed=seed)
    flits_per_core = k * W
    vp = write_paths_for_txns(topo, txns, strategy="least_occupied")
    bounds = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W,
                          merge_port_vcs=not FABRIC.get("per_vc_ports"))
    print(f"\n[{pattern}] K={k} W={W} txns={len(txns)} "
          f"wdata/core={flits_per_core} bound={bounds['bound']}", flush=True)

    runs: dict[str, dict[str, Any]] = {}
    for scheme in schemes:
        print(f"  running {scheme} ...", flush=True)
        r = run_scheme(scheme, topo, txns, seed=seed)
        runs[scheme] = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)

    # The same baseline with an unlimited tracker. It is the only way to say
    # how much of the imbalance the ring causes and how much the retry
    # backpressure hides: with a finite tracker the completer, not the core's
    # position, is what limits a core, so the cores look far more alike.
    print("  running S0 with an unlimited tracker (reference) ...", flush=True)
    r = run_scheme("S0", topo, txns, seed=seed, cfg={"ha_track": 0})
    ref = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)

    sweep_oc: list[dict[str, Any]] = []
    if sweep_s1 and "S16" in schemes:
        print("  sweeping S16 overcommit ...", flush=True)
        for oc in (2, 4, 8, 16, 24, 32, 48, 64, 128):
            r = run_scheme("S16", topo, txns, seed=seed,
                           cfg={"overcommit": oc}, quiet=True)
            d = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)
            f = d["fairness"]
            sweep_oc.append({
                "overcommit": oc, "makespan": d["makespan"],
                "jain": f["jain"], "max_min": f["max_min"],
                "cov": f["cov"], "throughput": f["throughput"],
                "peak_grants": (d.get("fc") or {}).get("peak_grants"),
                "grant_delay_mean": (d.get("fc") or {}).get(
                    "grant_delay_mean"),
                "lat_p99": d.get("lat_p99"),
            })
            print(f"    sweep S16 oc={oc} mk={d['makespan']} "
                  f"jain={f['jain']} max/min={f['max_min']} "
                  f"thr={f['throughput']}", flush=True)
        # Grant-on-arrival is exactly the baseline policy, so it also
        # measures the completer buffering the baseline silently needs.
        r = run_scheme("S16", topo, txns, seed=seed,
                       cfg={"overcommit": 10 ** 9}, quiet=True)
        d = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)
        sweep_oc.append({
            "overcommit": None, "makespan": d["makespan"],
            "jain": d["fairness"]["jain"],
            "max_min": d["fairness"]["max_min"],
            "cov": d["fairness"]["cov"],
            "throughput": d["fairness"]["throughput"],
            "peak_grants": (d.get("fc") or {}).get("peak_grants"),
            "grant_delay_mean": (d.get("fc") or {}).get("grant_delay_mean"),
            "lat_p99": d.get("lat_p99"),
        })
        print(f"    sweep S16 oc=inf (=S0 policy) "
              f"peak_grants={(d.get('fc') or {}).get('peak_grants')}",
              flush=True)

    ablate: list[dict[str, Any]] = []
    if sweep_s1 and "S16" in schemes:
        for tag, cfg in (("least_served + eager", {}),
                         ("round_robin", {"policy": "round_robin"}),
                         ("no eager grant", {"eager": False})):
            r = run_scheme("S16", topo, txns, seed=seed, cfg=cfg, quiet=True)
            d = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)
            f = d["fairness"]
            ablate.append({
                "variant": tag, "makespan": d["makespan"],
                "jain": f["jain"], "max_min": f["max_min"],
                "throughput": f["throughput"],
                "grant_delay_mean": (d.get("fc") or {}).get(
                    "grant_delay_mean"),
            })
            print(f"    ablate S16 {tag}: jain={f['jain']} "
                  f"max/min={f['max_min']} thr={f['throughput']}", flush=True)

    sweep: list[dict[str, Any]] = []
    if sweep_s1:
        for window in (64, 128):
            for band in ("spec", "harsh", "gentle"):
                cfg = {"window": window, "band": band}
                r = run_scheme("S1", topo, txns, seed=seed, cfg=cfg,
                               quiet=True)
                d = digest(r, flits_per_core=flits_per_core, bin_w=bin_w)
                f = d["fairness"]
                sweep.append({
                    "window": window, "band": band,
                    "makespan": d["makespan"], "jain": f["jain"],
                    "max_min": f["max_min"], "cov": f["cov"],
                    "throughput": f["throughput"],
                    "bw_min": f["bw_min"], "bw_max": f["bw_max"],
                })
                print(f"    sweep S1 w={window} band={band} "
                      f"mk={d['makespan']} jain={f['jain']} "
                      f"max/min={f['max_min']}", flush=True)

    seeds_out: list[dict[str, Any]] = []
    if seeds:
        print("  seed robustness ...", flush=True)
        seeds_out = seed_sweep(pattern, topo, k=k, W=W, seeds=seeds,
                               schemes=[s for s in ("S0", "S15", "S16")
                                        if s in schemes])

    out: dict[str, Any] = {
        "pattern": pattern, "K": k, "W": W,
        "flits_per_core": flits_per_core, "n_txn": len(txns),
        "mem": sorted({t.ha for t in txns}),
        "core_set": sorted({t.core for t in txns}),
        "bounds": bounds, "schemes": runs, "sweep": sweep,
        "sweep_oc": sweep_oc, "ablate": ablate,
        "seed_sweep": seeds_out,
        "s0_unbounded": ref,
    }
    if "S0" in runs:
        out["root_cause"] = root_cause(topo, txns, runs["S0"])
        # The position effect is only fully visible when the ring is the sole
        # constraint, so attribute it on the unbounded reference too.
        out["root_cause_unbounded"] = root_cause(topo, txns, ref)
    return out


def _report(pat: dict[str, Any]) -> None:
    print(f"\n[{pat['pattern']}]  bound={pat['bounds']['bound']}")
    print(f"{'scheme':6} {'mk':>8} {'ok':>3} {'jain':>8} {'max/min':>8} "
          f"{'cov':>8} {'bwmin':>8} {'bwmax':>8} {'thr':>7} {'fail':>9}")
    for name, d in pat["schemes"].items():
        f = d["fairness"]
        print(f"{name:6} {d['makespan']:>8} {int(bool(d['completed'])):>3} "
              f"{f['jain']:>8} {f['max_min']:>8} {f['cov']:>8} "
              f"{f['bw_min']:>8} {f['bw_max']:>8} {f['throughput']:>7} "
              f"{d['n_board_fail']:>9}")
    rc = pat.get("root_cause")
    if rc:
        print(f"  bw vs effective pass-through: r={rc['corr_bw_pt_eff']} "
              f"rank={rc['rank_bw_pt_eff']}   "
              f"bw vs out-link λ: r={rc['corr_bw_lat']}   "
              f"inject-success vs λ: r={rc['corr_succ_lat']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=K_PER_CORE)
    ap.add_argument("--W", type=int, default=W_FLITS)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quick", action="store_true", help="K=200 smoke run")
    ap.add_argument("--schemes", default="S0,S1")
    ap.add_argument("--patterns", default="uniform")
    ap.add_argument("--n-planes", type=int, default=N_PLANES_STUDY)
    ap.add_argument("--seeds", default="",
                    help="extra seeds for the S0/S15 robustness sweep")
    ap.add_argument("--sweep", action="store_true",
                    help="also sweep the S1 window / alpha-beta bands")
    ap.add_argument("--retry", action="store_true",
                    help="finite completer tracker: sweep the outstanding cap")
    ap.add_argument("--retry-k", type=int, default=RETRY_K,
                    help="batch size for the retry sweeps")
    ap.add_argument("--repro", action="store_true",
                    help="ost time-series + innocent-flow blocker experiment")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--merge", action="store_true",
                    help="add --schemes into an existing JSON; do not replace")
    args = ap.parse_args()

    k = 200 if args.quick else args.k
    bin_w = 32 if args.quick else BIN_W
    schemes = [s for s in args.schemes.split(",") if s]
    patterns = [s for s in args.patterns.split(",") if s]

    topo = Ring2Topology(vcs=CHI_VCS_WRITE, n_planes=args.n_planes,
                         route="latency")
    t0 = time.perf_counter()
    if args.merge:
        out = Path(args.out)
        if not out.exists():
            raise SystemExit(f"--merge needs an existing {out}")
        payload = json.loads(out.read_text())
        old_k = payload.get("meta", {}).get("K", k)
        if not args.quick and old_k != k:
            raise SystemExit(f"--merge K={k} != existing K={old_k}")
        bin_w = payload.get("meta", {}).get("bin_w", bin_w)
        for pattern in patterns:
            pat = payload.setdefault("patterns", {}).setdefault(pattern, {})
            if "schemes" not in pat:
                raise SystemExit(f"--merge: no existing pattern {pattern}")
            txns = build_pattern(pattern, k=old_k, W=args.W, seed=args.seed)
            flits = pat.get("flits_per_core") or old_k * args.W
            print(f"\n[merge {pattern}] K={old_k} + {schemes}", flush=True)
            for scheme in schemes:
                r = run_scheme(scheme, topo, txns, seed=args.seed)
                pat["schemes"][scheme] = digest(
                    r, flits_per_core=flits, bin_w=bin_w)
            _report(pat)
        payload["meta"]["schemes"] = sorted(
            set(payload["meta"].get("schemes") or []) | set(schemes),
            key=lambda s: (len(s), s))
        payload["meta"]["bus_lat"] = FC_BUS_LAT
        payload["meta"]["bus_lat_forecast"] = BUS_LAT_FORECAST
        payload["meta"]["belief_update"] = uniform_belief(
            payload.get("patterns") or {})
        payload["meta"]["generated_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload["wall_secs"] = round(time.perf_counter() - t0, 1)
        out.write_text(json.dumps(payload, indent=1))
        print(f"\nmerged {out}  {payload['wall_secs']}s")
        return
    if args.repro:
        out = Path(args.out)
        if not out.exists():
            raise SystemExit(f"--repro needs an existing {out}")
        payload = json.loads(out.read_text())
        rk = 100 if args.quick else args.retry_k
        print(f"\n[repro] K={rk} W={args.W} seed={args.seed}", flush=True)
        payload["congestion_repro"] = congestion_repro(
            topo, k=rk, W=args.W, seed=args.seed)
        payload["meta"]["generated_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload["wall_secs"] = round(time.perf_counter() - t0, 1)
        out.write_text(json.dumps(payload, indent=1))
        print(f"\nrepro -> {out}  {payload['wall_secs']}s")
        return
    out_pats = {
        p: run_pattern(p, topo, k=k, W=args.W, seed=args.seed, bin_w=bin_w,
                       schemes=schemes, sweep_s1=args.sweep,
                       seeds=[int(x) for x in args.seeds.split(",") if x])
        for p in patterns
    }
    retry_out = None
    if args.retry:
        rk = 100 if args.quick else args.retry_k
        retry_out = retry_study(topo, k=rk, W=args.W, seed=args.seed)
    bp = base_params()
    payload = {
        "meta": {
            "K": k, "W": args.W, "seed": args.seed, "bin_w": bin_w,
            "patterns": patterns, "schemes": schemes,
            "plane_sel": bp.plane_sel, "routing": "shortest_path",
            "route": topo.route,
            "mem_nodes": list(MEM_NODES), "core_nodes": list(CORE_NODES),
            "non_terminal": list(NON_TERMINAL),
            "n_planes": topo.n_planes, "sigma": topo.sigma,
            "board_ports": topo.board_ports,
            "leave_ports": topo.leave_ports,
            "t_xfer": bp.t_xfer, "eject_bw": bp.eject_bw,
            "vcs": list(topo.vcs), "n_vc": topo.n_vc,
            "hop_bw_cap": topo.hop_bw_cap,
            "link_lats": list(topo.link_lats),
            "inj_depth": bp.inj_depth, "eject_depth": bp.eject_depth,
            "dir_inj_depth": bp.dir_inj_depth,
            "shared_inj": bp.shared_inj, "two_write_leave": bp.two_write_leave,
            "t_inj": bp.t_inj, "per_vc_srcq": bp.per_vc_srcq,
            "core_outstanding": bp.core_outstanding,
            "ha_track": bp.ha_track, "s16_overcommit": S16_OVERCOMMIT,
            "ha_rsp_jit_lo": bp.ha_rsp_jit_lo,
            "ha_rsp_jit": bp.ha_rsp_jit,
            "bus_lat": FC_BUS_LAT,
            "bus_lat_forecast": BUS_LAT_FORECAST,
            "per_vc_ports": bp.per_vc_ports,
            "vc_indep_forecast": VC_INDEP_FORECAST,
            "shared_buf_forecast": SHARED_BUF_FORECAST,
            "per_vc_port_forecast": PER_VC_PORT_FORECAST,
            "flit_b": FLIT_BYTES, "burst_b": BURST_BYTES,
            "stride_b": STRIDE_BYTES, "tile_b": TILE_BYTES,
            "stimulus_forecast": STIMULUS_FORECAST,
            "m_req": M_REQ, "m_rsp": M_RSP,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "patterns": out_pats,
        "wall_secs": round(time.perf_counter() - t0, 1),
    }
    if retry_out is not None:
        payload["retry_study"] = retry_out
    payload["meta"]["belief_update"] = uniform_belief(out_pats)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Keep an existing retry / repro only when the fabric matches.
    # A 1-plane / new-jitter run must not inherit dual-plane leftovers.
    old_payload = None
    if out.exists():
        try:
            old_payload = json.loads(out.read_text())
        except (ValueError, OSError):
            old_payload = None
    old_meta = (old_payload or {}).get("meta") or {}
    same_fabric = all(
        old_meta.get(k) == payload["meta"].get(k)
        for k in ("n_planes", "K", "W", "ha_rsp_jit", "ha_rsp_jit_lo",
                  "per_vc_ports", "route", "shared_inj", "two_write_leave"))
    if retry_out is None and same_fabric and old_payload:
        if old_payload.get("retry_study") is not None:
            payload["retry_study"] = old_payload["retry_study"]
    if same_fabric and old_payload and old_payload.get("congestion_repro"):
        payload["congestion_repro"] = old_payload["congestion_repro"]
    out.write_text(json.dumps(payload, indent=1))
    print(f"\nwrote {out}  {payload['wall_secs']}s")
    for pat in out_pats.values():
        _report(pat)


if __name__ == "__main__":
    main()
