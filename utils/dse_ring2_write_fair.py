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
BIN_W = 50               # fairness window: Jain of the 10 cores per 50 cycles
T_MAX = 4_000_000
# Two adjacent memory nodes standing in for one clustered memory region. Both
# are memory in this study (9 and 19 are not), so the roles are unchanged and
# only the destination geometry differs from `uniform`.
HOT_HAS = (11, 13)

# -- the retry / outstanding study ------------------------------------------
# Request tracker entries per completer, and the baseline for every scheme:
# a completer that runs out of entries must RetryAck. The size has to sit above
# the peak occupancy the workload actually reaches, or the tracker -- not the
# ring -- becomes the binding resource and the study measures the wrong thing.
#
# Per-direction up-ring ports moved that peak. On the shared port the peak was
# 243 entries and 256 was just enough (zero RetryAck). With each direction on
# its own port the inject side is faster, the peak rises to 422, and 256 pegs
# at saturation: 20278 RetryAcks, 60834 extra REQ / RSP flits on the ring, and
# total write bandwidth falls from 95.69% to 76.13% of R*. 512 covers the 422
# peak and takes retries back to zero; 512 / 1024 / 4096 all measure
# identically, which is the witness that the tracker has stopped binding.
# See `PERDIR_PROBE`.
RETRY_TRACK = 512
# S16 has to grant from *below* the tracker to do anything at all: at
# overcommit >= ha_track its pump never withholds a grant and it degenerates
# to S0 exactly (pinned by verify_ring2_20.py). Being under the tracker is
# necessary but not sufficient -- what has to be tight is the in-flight budget
# the *ring* can sustain, not the tracker. Measured on the study workload at
# ha_track = 256 (k=3000, peak occupancy ~204): 64 gives max/min 1.073 at
# +2.4% throughput over S0, while 128 -- also "below the tracker" -- lands at
# 1.177 and does nothing. So this stays at 64 and is not scaled with ha_track.
S16_OVERCOMMIT = 64
OUTST_POINTS = (4, 8, 16, 32, 64, 128, 256)
TRACK_POINTS = (8, 16, 32, 64, 128, 0)      # 0 = unlimited tracker
RETRY_SCHEMES = ("S0", "S16", "S17", "S18")
RETRY_K = 800                # shorter batch: the grid is 56 runs wide
OUTST_SAMPLE = 16            # cycles between outstanding-occupancy samples
BUF_SAMPLE = 16              # cycles between fabric-FIFO occupancy samples
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
# (link-delay sum, then hops, then CW), a depth-12 shared up-ring FIFO plus
# a depth-8 inject Q per direction, 1 flit/cycle board, a depth-12 two-write /
# one-read leave buffer at 1 flit/cycle PE drain, and a finite request tracker.
# The tracker is part of the baseline, not of one scheme.
HA_RSP_JIT_LO = 0        # inclusive; each HA RSP / Comp is U{lo..hi}
HA_RSP_JIT = 0           # 0 = constant t_ha_service (also 0): no HA think time
# Dedicated congestion-bus delivery delay (S1 / S15). Not a ring hop.
FC_BUS_LAT = 30
# S22's operating point, from the confirmation run at the study's own K.
# `dfc_window=2` with `dfc_bus_lat=1` is what makes the deficit an
# instantaneous measure rather than a long-run one, which is what the 50-cycle
# index actually asks for.
#
# `dfc_margin` is the knob that had to move once I-tag was implemented as
# specified: a fairer baseline means most deficit gaps are small, and yielding
# on those near-level gaps spends a hop without moving the index. At `margin=2`
# -- the value tuned against the old broadcast baseline -- the same controller
# now costs 1.93% at the official K, outside the acceptance line. `margin=4`
# refuses those swaps and lands at -0.04%.
#
# Chosen over `margin=3` (Jain 0.9915 at -0.55%) on robustness, not on the
# single official-K number: `margin=4` holds Jain 0.99062 to five decimals
# across a 2x change in run length, so its thinner Jain headroom is stable
# rather than lucky. See `S22_ROBUST`.
#
# The deeper inject Q is not free and is priced separately in the report: it
# is what gives `dfc_dodge` candidates to overtake with, and on the stock
# depth-8 Q the same controller costs -2.18%.
S22_CFG = dict(dfc_window=2, dfc_bus_lat=1, dfc_thresh=0.5, dfc_hold=16,
               dfc_margin=4.0, dfc_dodge=32,
               inj_depth=32, dir_inj_depth=32)
# S1's phase-2 operating point: per-direction budgets are what make the
# CW/CCW board-failure counts even, and they also stop the AIMD from costing
# throughput (see `S1_DIRBAL`).
S1_CFG = dict(dir_split=True, band="spec", cap_scale=0.5, window=64,
              pace_burst=1)
# I-tag and E-tag at their specified semantics, and live rather than dormant.
# Both used to be inert on this workload: `t_inj = 64` is above the longest run
# of consecutive failed boards the fabric ever produces (41 cycles), and E-tag
# waited for a fourth failed eject when the rule is to tag on the first. Their
# behaviour was also not the specified one -- see `TAG_AUDIT`. At these settings
# the pair is strictly better than dormant on both axes (+0.23% throughput,
# +0.0088 binned Jain), because `itag_mode="reserve"` spends one slot per tag
# instead of holding off a whole ring direction for a whole starvation period.
ITAG_MODE = "reserve"
T_INJ = 4                # consecutive failed boards before a node raises I-tag
T_XFER = 1               # failed ejects before E-tag: the specified value is 1
# The up-ring port structure. This is a full ring, so each node's inject side
# is *two* port groups -- one per direction -- and each group carries REQ / RSP
# / DAT. Six inject ports per node, each 1 flit/cycle. The down-ring side is
# unchanged: one two-write-one-read buffer per node per VC, draining 1
# flit/cycle, so `per_dir_ports` deliberately does not touch the leave side.
#
# Consequence worth flagging: `inj_sel` only reorders a port group holding more
# than one queue (`_board_one`). With the directions on separate ports every
# group is a singleton, so `inj_sel="free_slot"` is now a **no-op** -- verified
# bit-identical to `rr` in `PERDIR_PROBE`. It is kept in the dict only so the
# shared-port comparison rows in that probe stay reproducible.
PER_DIR_PORTS = True
FABRIC = dict(plane_sel="least_occupied", per_vc_srcq=True,
              per_vc_ports=True, shared_inj=True, two_write_leave=True,
              inj_depth=12, dir_inj_depth=8, eject_depth=12,
              inj_sel="free_slot", per_dir_ports=PER_DIR_PORTS,
              itag_mode=ITAG_MODE, t_inj=T_INJ, t_xfer=T_XFER,
              core_outstanding=CORE_OUTSTANDING_WR, ha_track=RETRY_TRACK,
              outst_sample=OUTST_SAMPLE, buf_sample=BUF_SAMPLE,
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
# Written before the ha_rsp_jit=0 re-run. Do not edit after seeing results.
HA_RSP_ZERO_FORECAST = {
    "hypothesis": (
        "HA 回 RSP / Comp 的 think time 从 U{4..64}（均值 34 拍/条，"
        "一笔写至少 DBID+Comp 两条 ≈ 68 拍）改成 0。"
        "上一轮 T_hold ≈ 191 拍、R=2.41、retry/txn=0.57，"
        "其中一块就是这段 completer 等待。"
        "去掉之后 32 个 tracker 周转加快，retry 应下降，"
        "S0 吞吐应往无缓存环平台（上一轮无限 tracker ≈ 4.51）靠。"
        "32 tracker 未必完全松绑（68 拍只是 T_hold 的一部分），"
        "但差距应明显收窄。无限 tracker 平台本身不应大变："
        "HA 时延不是环的限制。"
    ),
    "predicted": {
        "s0_thr": [3.0, 4.6],
        "retry_per_txn": [0.0, 0.40],
        "s0_thr_gt_prev": 2.41,
        "inf_track_thr": [4.3, 5.0],
        "bound": 70000,
    },
    "confidence": 0.6,
    "falsify": (
        "S0 吞吐仍 ≤ 2.6（HA 时延不在关键路径上），"
        "或 retry/txn 仍 ≥ 0.50"
    ),
}

BIN50_FAIR_FORECAST = {
    "hypothesis": (
        f"公平性主指标改成：{BIN_W} 拍宽的窗内对 10 个核的写带宽算 Jain，"
        "再对竞争窗口内的所有箱取平均。"
        "S0 总写带宽 ≈ 2.25 flit/cycle，摊到 10 个核上每核每箱只有约 "
        "0.225 × 50 ≈ 11 个 flit，因此这个指标会被计数噪声主导："
        "等概率多项分布的零模型本身就只有 "
        "1/(1 + var/mean²) ≈ 1/(1 + 10.2/128) ≈ 0.926。"
        "上一轮 128 拍分箱已看到实测 max/min 低于零模型（亚泊松），"
        "所以实测均值应落在零模型之上、1.0 之下。"
        "结论不应改变：仍是采样粒度而非核间不公平。"
    ),
    "predicted": {
        "jain_bin_mean": [0.93, 0.97],
        "gt_null_jain": True,
        "flits_per_core_per_bin": [9.0, 13.0],
        "n_bins_min": 2000,
    },
    "confidence": 0.75,
    "falsify": (
        f"jain_bin_mean 低于同窗零模型的 null_jain，"
        f"或落到 0.90 以下 —— 那说明 {BIN_W} 拍尺度上存在真实的核间不公平，"
        "而不是抽样波动"
    ),
}


TRACK128_FORECAST = {
    "hypothesis": (
        "ha_track 32 → 128，跨过 tracker 扫描找到的拐点。"
        "上一轮 32 tracker 下 S0 = 2.385 flit/cycle、retry/txn = 0.63，"
        "而短探测里 128 给出 4.424、retry/txn = 0.003，"
        "无限 tracker 平台是 4.498。"
        "所以预期：吞吐接近翻倍并贴上无缓存环平台，重试基本归零，"
        "makespan 从 16.8 万降到 9 万附近，"
        "而 s0_unbounded 参照与 S0 的差距收到几个百分点以内 —— "
        "瓶颈从 completer 表项换成上环饥饿。"
        "公平性主指标应仍达标但数值上移：吞吐翻倍使每箱 flit 数从 11.9 涨到约 22，"
        "零模型地板随之从 0.929 抬到约 0.961，实测约 0.98，ratio 仍在 1.02 附近。"
        "整份报告的主线会因此改变：第 9、10 节讨论的重试浪费不再是主要损失。"
    ),
    "predicted": {
        "s0_thr": [4.2, 4.7],
        "retry_per_txn_max": 0.05,
        "makespan": [85000, 95000],
        "unbounded_gap_pct_max": 3.0,
        "jain_bin_mean": [0.972, 0.988],
        "jain_bin_null": [0.955, 0.966],
        "jain_bin_ratio": [1.010, 1.030],
    },
    "confidence": 0.8,
    "falsify": (
        "S0 吞吐仍 ≤ 3.0（128 表项仍不够，或别的东西在绑定），"
        "或 retry/txn > 0.05，"
        "或 ratio < 1.0（放开 tracker 反而制造出真实的核间不公平）"
    ),
}


TRACK256_FORECAST = {
    "hypothesis": (
        "ha_track 128 → 256。上一轮的预测栽在引用了 K=4000 的短探测，"
        "这次改用官方 K 上的直接测量：同一 fabric 的 ∞ tracker 参照"
        "峰值只用到 195 个表项，而 128 装不下（实测占用死死顶在 128）。"
        "256 > 195，所以配额应当永远不会触发 —— "
        "一旦不触发，轨迹就与 ∞ 参照逐拍一致（不动点论证："
        "cap 不生效则动力学与无 cap 相同，而无 cap 下峰值 195 < 256，自洽）。"
        "因此预测不是「接近 ∞」而是「等于 ∞」：makespan 88090、"
        "吞吐 4.5408、retry 恰好 0、峰值占用 195。"
        "剩下与 hop 理想 R* = 5.714 的差距全部归无缓存环的上环饥饿，"
        "端口那一层（每节点上/下环 1 flit/cycle/VC → 8 flit/cycle）从来不绑定。"
        "公平性：每箱 flit 数从 15.2 升到约 22.8，"
        "零模型地板随之抬到约 0.962，实测约 0.982，ratio 约 1.021（仍达标但比 128 时低）。"
    ),
    "predicted": {
        "s0_thr": 4.5408,
        "makespan": 88090,
        "retry_per_txn": 0.0,
        "max_ha_used": 195,
        "jain_bin_mean": 0.98183,
        "jain_bin_null": 0.96197,
        "jain_bin_ratio": 1.02064,
    },
    "confidence": 0.85,
    "falsify": (
        "出现任何重试（retry > 0），或 makespan 偏离 88090 超过 1% —— "
        "那说明占用峰值本身跟 cap 有关，不动点论证不成立，"
        "或者仿真存在我没考虑到的路径依赖"
    ),
}


UPQ_12_8_FORECAST = {
    "hypothesis": (
        "每 VC 的上环源队列从「8 深双向共享 FIFO + 每向 1 深 inject Q」"
        "改成「12 深共享 FIFO + 每向 8 深 inject Q」；端口、链路、下环、"
        "tracker 和路由不变。预测总写带宽仍约 4.54 flit/cycle，变化在 ±1% 内。"
        "原因是上一轮 tracker 已不绑定，端口最忙也只有 56.8%，而在 ∞ tracker "
        "历史消融里 inj_depth 8→32 完全不动、dir_inj_depth 1→4 也只动约 1%。"
        "更深的源队列能保存更多等待 flit，却不能让被 transit flit 占用的"
        "出链路产生新空槽；因此无缓存环的上环时序冲突应继续绑定。"
    ),
    "predicted": {
        "s0_thr": [4.495, 4.586],
        "s0_thr_delta_pct": [-1.0, 1.0],
        "retry_per_txn": 0.0,
        "bound": 70000,
        "rstar": 5.714,
    },
    "confidence": 0.8,
    "falsify": (
        "S0 吞吐相对 4.5408 提升超过 3%（> 4.677），"
        "或热 DAT hop 利用率明显越过上一轮约 79.7%；"
        "那说明 depth-1 方向 Q 的短时阻塞确实是欠载的重要来源"
    ),
}


EJECT12_BUFOCC_FORECAST = {
    "hypothesis": (
        "下环两写一读 buffer 从 4 深加到 12 深，上环队列保持 12+8，"
        "并首次逐 FIFO 采样占用率。"
        "预测吞吐几乎不动（4.577 ± 1%）：上一轮 eject_depth 4→16 的历史消融"
        "只从 4.498 走到 4.503，而下环 dat 端口占用只有 56.8%，"
        "PE 每拍仍只读 1 flit —— 加深接收缓存不改变排空速率。"
        "占用率预测：三类 capped FIFO 都远离满。"
        "下环 leave buffer 平均占用 < 1 flit（到站流量 0.57 flit/cycle/mem，"
        "排空 1 flit/cycle，M/D/1 式短队列），满的比例 ≈ 0；"
        "每向 8 深 inject Q 是最有压力的一层，因为上环失败率高达 84.6%，"
        "flit 会在这里积压，预测平均占用 3~7、满的比例 20%~70%；"
        "12 深共享 FIFO 被 dir Q 的背压推着，也会经常满（> 20%），"
        "但真正的堵点在 dir Q 出口而不是 FIFO 容量。"
        "PE 侧 req_pend 会很深（outstanding 128 远大于队列容量）。"
    ),
    "predicted": {
        "s0_thr": [4.531, 4.623],
        "leave_full_pct_max": 1.0,
        "leave_occ_mean_max": 1.5,
        "dirq_full_pct_mean": [20.0, 70.0],
        "retry_per_txn": 0.0,
    },
    "confidence": 0.7,
    "falsify": (
        "下环 leave buffer 出现明显满（full_pct > 1%）—— "
        "那说明 PE 排空真的是瓶颈之一，而不是端口占用率算出来的 56.8%；"
        "或吞吐变化超过 1%；"
        "或 dir Q 几乎从不满（< 5%），那说明上环失败并不会在源端积压，"
        "flit 是在更上游（PE 侧）等，加深 dir Q 本来就不可能有用"
    ),
}


INJ_SEL_FORECAST = {
    "hypothesis": (
        "上环仲裁器原本按 round-robin 在两个方向 Q 之间选，选定之后才发现"
        "出链路被 transit flit 占住 —— 那一拍就浪费了，即使另一个方向的 hop "
        "是空的。`inj_sel=free_slot` 让仲裁器先看出链路这拍是否真的空闲"
        "（环上锁存器本来就有这个本地信号，不需要新增缓存或总线），"
        "再决定端口给哪个方向；端口仍是 1 flit/cycle，transit 仍绝对优先。"
        "K=2500 短探测上这一改把 S0 从 4.588 抬到 5.1986（80.3% → 91.0% R*）。"
        "预测在官方 K=20000 上同样成立：稳态拥塞更深，'一个方向忙另一个方向空'"
        "的拍数只会更多，所以增益不应低于短探测。"
    ),
    "predicted": {
        "s0_thr": [5.10, 5.30],
        "pct_r_star": [89.0, 93.0],
        "makespan": [75000, 78500],
        "retry_per_txn": 0.0,
        # The fix lets whoever has a free slot go, which correlates with
        # position, so instantaneous evenness should get slightly worse.
        "jain_bin_ratio": [0.97, 1.00],
    },
    "confidence": 0.8,
    "falsify": (
        "S0 吞吐低于 4.9（增益不到 7%）—— 那说明 K=2500 的短探测像当年的"
        "tracker 探测一样高估了效果，官方 K 的稳态里空闲 hop 槽并没有那么多；"
        "或 retry 变成非零（仲裁器改动不应该影响 completer 表项压力）"
    ),
}

# Frozen: the same knob sweep that found the arbiter fix. Buffer depths,
# the E-tag threshold and the reserved eject slots were all inert, so the
# ceiling probe is now a one-knob story.
INJ_SEL_PROBE = {
    "k": 2500, "r_star": 5.7117,
    "rows": [
        ["rr（旧仲裁器）", 4.588, 80.3, 0.96848, 1.00586],
        ["free_slot（本轮）", 5.1986, 91.0, 0.95406, 0.98619],
    ],
    "inert": [
        ["t_xfer 4 → 1 / ∞（E-tag 门限）", 5.1986],
        ["resv_ej 1 → 0 / 12", 5.1986],
        ["inj_depth 12 → 32", 5.1986],
        ["eject_depth 12 → 32", 5.1986],
        ["dir_inj_depth 8 → 32", 5.198],
        ["eject_bw 1 → 2", 5.1648],
    ],
    # I-tag trades bandwidth for evenness monotonically, on the fixed arbiter.
    "t_inj": [
        [2, 4.0016, 70.1, 0.98996, 1.03437],
        [3, 4.4053, 77.1, 0.98433, 1.02434],
        [4, 4.6966, 82.2, 0.98104, 1.01815],
        [6, 4.9471, 86.6, 0.97021, 1.00457],
        [8, 5.0434, 88.3, 0.95929, 0.99184],
        [12, 5.1568, 90.3, 0.95302, 0.98489],
        [16, 5.1905, 90.9, 0.95665, 0.98887],
        ["∞（本轮基线）", 5.1986, 91.0, 0.95406, 0.98619],
    ],
}

# Frozen: 2x2x2 factorial separating the arbiter from the two buffer depths.
# The report used to compare this round's number against an earlier round's
# and credit the whole gain to the eject buffer, but the arbiter had changed in
# between, so that was not a controlled comparison. K=3000, seed 0, one plane.
DEPTH_FACTORIAL = {
    "k": 3000,
    # arbiter, up-ring queue (shared + per-dir), eject depth, thr
    "rows": [
        ["rr", "8+1", 4, 4.5286],
        ["rr", "8+1", 12, 4.5045],
        ["rr", "12+8", 4, 4.5910],
        ["rr", "12+8", 12, 4.5889],
        ["free_slot", "8+1", 4, 4.9558],
        ["free_slot", "8+1", 12, 4.9875],
        ["free_slot", "12+8", 4, 5.1383],
        ["free_slot", "12+8", 12, 5.1872],
    ],
    # Each effect twice: once with the other factors at this round's setting,
    # once at the old setting. The gap between the two columns is the
    # interaction, and it is the point -- a deeper per-direction queue is only
    # worth something once the arbiter can choose which direction to use.
    "effects": [
        # change, effect at current settings, at old settings, what varies
        ["上环仲裁器 rr → free_slot", 13.04, 9.43,
         "队列 12+8 / 下环 12 深 · 队列 8+1 / 下环 4 深"],
        ["上环队列 8+1 → 12+8", 4.00, 1.87,
         "free_slot 仲裁器 · rr 仲裁器（均 下环 12 深）"],
        ["下环 buffer 4 → 12 深", 0.95, -0.05,
         "free_slot 仲裁器 · rr 仲裁器（均 队列 12+8）"],
    ],
    "total_pct": 14.54,      # (rr, 8+1, 4) 4.5286 -> (free_slot, 12+8, 12)
    # Both depths together, measured directly rather than by summing the two
    # main effects -- they interact, so adding them is the very mistake this
    # table exists to correct. free_slot, 8+1/4 (4.9558) -> 12+8/12 (5.1872).
    "buf_pct": 4.67,
    "buf_pct_rr": 1.33,      # the same pair on rr: 4.5286 -> 4.5889
}

# Frozen: the up-ring port structure correction. This is a full ring, so each
# node's inject side is two port groups -- one per direction -- each carrying
# REQ / RSP / DAT: six inject ports per node, 1 flit/cycle each. The down-ring
# side is unchanged (one two-write-one-read buffer per node per VC, 1 flit/cycle
# drain), so the change is deliberately asymmetric.
#
# Everything here is at the official K, one plane, seed 0.
# `probe_ring2_perdir.py` and `probe_ring2_perdir_why.py` produce it.
PERDIR_PROBE = {
    "k": 20000, "r_star": 5.7143,
    # R* does not move. Splitting the board port by direction halves its floor,
    # so the busiest *link* still binds -- the ceiling was never a port-capacity
    # question. Floors in cycles, at the official K.
    "bounds": {
        "link_lb": 70000,
        "board_lb_shared": 50000, "board_lb_shared_at": "node 1, rsp",
        "board_lb_per_dir": 25000, "board_lb_per_dir_at": "node 1, rsp, ccw",
        "leave_lb": 50000, "leave_lb_at": "node 1, dat",
        "binding": "link_lb", "r_star_before": 5.7143, "r_star_after": 5.7143,
    },
    # The tracker sweep. `ha_track` had to grow with the port change: a faster
    # inject side raises peak tracker occupancy, and once it pegs, RetryAck puts
    # whole extra transactions back on the ring.
    "track_rows": [
        # per_dir, ha_track, thr, % R*, Jbin, max/min, retries, peak tracker,
        # deflections, flits delivered vs the 1000000 the workload implies
        [False, 256, 5.2174, 91.30, 0.96765, 1.0288, 0, 243, 0, 1000000],
        [False, 512, 5.2174, 91.30, 0.96765, 1.0288, 0, 243, 0, 1000000],
        [False, 4096, 5.2174, 91.30, 0.96765, 1.0288, 0, 243, 0, 1000000],
        [True, 256, 4.3500, 76.13, 0.93993, 1.0964, 20278, 256, 882, 1060834],
        [True, 512, 5.4681, 95.69, 0.87865, 1.6931, 0, 422, 2306, 1000000],
        [True, 1024, 5.4681, 95.69, 0.87865, 1.6931, 0, 422, 2306, 1000000],
        [True, 4096, 5.4681, 95.69, 0.87865, 1.6931, 0, 422, 2306, 1000000],
    ],
    "chosen_track": 512,
    # `inj_sel` only reorders a port group holding more than one queue. With the
    # directions separated every group is a singleton, so the arbiter is inert.
    "free_slot_is_noop": True,
    "free_slot_equals_rr": {"thr": 4.3500, "jain_bin": 0.93993,
                            "note": "bit-identical at ha_track=256"},
    # Two causes that were tested and eliminated before landing on the tracker.
    "eliminated": [
        ["路由改变", "choose_dir 只看链路时延、完全确定，"
         "且两种端口结构下 REQ 的 hop 穿越数逐位相同（74976）。"],
    ],
    # Retracted. The eject-side evidence that put this in `eliminated` was taken
    # at ha_track=256, i.e. inside the retry storm, where the completer bound
    # everything and no ring-side knob could show through. Re-measured on the
    # shipped tracker it reverses sign: eject_bw=2 now *helps*. See CEILING_GAP.
    "eliminated_retracted": [
        ["下环 buffer / E-tag 绕环",
         "曾以「eject_bw=2 把偏转清零、吞吐反而更差（82.59%）」排除它，"
         "但那组数是在 <b>ha_track=256</b> 下测的 —— 那时 completer 的 retry "
         "风暴绑定一切，环侧任何旋钮都显不出来。"
         "在出厂 tracker=512 上重测，结论<b>反转</b>："
         "eject_bw=2 吞吐从 95.69% 升到 <b>96.49%</b>、绕环加价精确归零。"
         "下环读侧速率是真实限制项，见 3.1.9。"],
    ],
    "verdict": (
        "按方向拆上环端口在<b>两个方向上都改变了结论</b>：总带宽从 91.30% 抬到 "
        "<b>95.69% R*</b>（前提是 tracker 跟上，见下），"
        "而瞬时均衡度从 Jain <b>0.96765 掉到 0.87865</b>、"
        "整窗 max/min 从 1.0288 坏到 1.6931 —— "
        "一个节点现在能在同一拍向两个方向各上环一个 flit，"
        "邻 mem 多的核的位置优势被放大。"),
}

# Frozen: why the per-bin mean write bandwidth reads *above* R*, and which of
# the two numbers is at fault. Neither the bound nor the simulator is:
#
#   * R* = 5.7143 is a bound on the **whole-run** average. It comes from one
#     link -- the routing puts 70000 DAT crossings on hop 0->1, at 1 flit/cycle
#     that needs 70000 cycles, so makespan >= 70000 and 400000/70000 = 5.7143.
#     The measured makespan is 73152, i.e. 95.69% of the bound. No violation.
#   * The simulator conserves flits exactly (400000 delivered for 400000 asked)
#     and the binding hop never carries more than 1 flit/cycle: its busiest
#     50-cycle bin holds exactly 50 crossings, utilisation 1.0000 and never
#     above.
#
# What was wrong is the **comparison**. The per-bin table averages only bins
# wholly inside the contention window [0, t_fair], because past t_fair a core
# has run out of quota and its zeros are not unfairness. That window is 55700 of
# 73152 cycles and it is the *busy* part of the run, so its mean (6.0207)
# necessarily exceeds the whole-run average (5.4681). Dividing a window mean by
# a whole-run bound is not a meaningful ratio.
#
# A window may exceed R* only by running a mix skewed *away* from the binding
# hop, and that is measurable rather than a hand-wave: carrying 335353 write
# flits in 55700 cycles requires the hop's share of them to be at most
# 55700/335353 = 16.61%, against a 17.50% nominal share. Measured in-window
# share is 16.20% and in-window hop utilisation 0.9755, with the deferred
# crossings cleared in the tail at 0.9035. `probe_ring2_hotslot_time.py`.
OVER_RSTAR = {
    "k": 20000, "r_star": 5.7143, "makespan": 73152,
    "whole_run": 5.4681, "whole_run_pct": 95.69,
    "conservation": {"delivered": 400000, "asked": 400000},
    "hot_hop": "0->1",
    # Measured crossings exceed the routing's 70000 because deflected DAT flits
    # ride an extra lap. The bound is therefore *conservative*, not violated:
    # the achievable ceiling given real deflections is 400000/70104 = 5.7058.
    "hot_crossings_measured": 70104, "hot_crossings_nominal": 70000,
    "achievable_ceiling": 5.7058,
    "peak_bin_crossings": 50, "bin_w": 50, "peak_bin_util": 1.0,
    "window": {"bins": 1114, "span": 55700, "pct_of_makespan": 76.1,
               "write_flits": 335353, "rate": 6.0207, "pct_r_star": 105.4,
               "hot_util": 0.9755, "hot_share_pct": 16.20,
               "nominal_share_pct": 17.50, "share_ceiling_pct": 16.61},
    "tail": {"span": 17452, "write_flits": 64647, "rate": 3.7043,
             "hot_util": 0.9035},
    "verdict": (
        "理论上限没错，仿真也没错，<b>错的是把两个不同口径的数放在一起比</b>："
        "6.0207 是<u>竞争窗内</u>的均值（1114 个箱、55700 拍，占 makespan 的 "
        "76.1%），R* 是<u>全程</u> makespan 界。全程实测 5.4681 = 95.69% R*，"
        "从未越界；绑定 hop 最忙的一个 50 拍箱正好 50 次穿越、利用率 1.0000，"
        "一次都没超过 1 flit/cycle。窗内能跑到 105.4% 是因为窗内的流量组合"
        "<b>偏离了绑定 hop</b>：它只吃到 16.20% 的窗内写 flit（名义 17.50%，"
        "可行上限 16.61%），欠下的穿越在收尾段以 0.9035 的利用率补完。"),
}

# Frozen: has S0 reached the *reachable* ceiling, and what holds the last 4.31%?
# Produced by `probe_ring2_ceiling_gap.py` (decomposition + idle attribution)
# and `probe_ring2_ceiling_fix.py` (one intervention per candidate cause), both
# at the official K on the shipped fabric.
#
# The whole section rests on one identity that held exactly in all eight
# configurations measured, which is what makes the causes additively priceable:
#
#     makespan = 70000 (the routing's load on the binding hop)
#              + surcharge (extra crossings of it by flits riding another lap)
#              + idle (cycles it sat empty)
CEILING_GAP = {
    "k": 20000, "r_star": 5.7143, "floor": 70000,
    "thr": 5.4681, "pct": 95.69, "makespan": 73152,
    # The binding VC is RSP, not DAT. Earlier rounds looked at DAT because that
    # was binding on the shared port; per-direction ports moved it.
    "binding": {"vc": "rsp", "hops": "1→0, 11→10, 7→8, 17→18",
                "crossings": 71056, "util": 0.97135, "surcharge": 1056,
                "idle": 2096},
    "dat_ref": {"hops": "0→1, 10→11, 8→7, 18→17", "crossings": 70104,
                "util": 0.95833, "surcharge": 104, "idle": 3048},
    # Given the deflections that actually happen, the reachable ceiling is
    # 400000/71056; the shipped run sits at 97.13% of it, which is exactly the
    # binding hop's utilisation. Consistent by construction.
    "reachable": 5.6294, "reachable_pct_r_star": 98.51, "pct_of_reachable": 97.13,
    # Every idle cycle on the four binding RSP hops, attributed by the
    # simulator's own board-failure causes. `other` is 0: nothing unexplained.
    "attrib": [
        # cause, cycles, share, what it means
        ["I-tag 让位", 4529, 53.97,
         "I-tag 预留refused了本地上环（<code>_itag_blocks</code>）"],
        ["dry（无货）", 2600, 30.98,
         "该方向队列是空的 —— 四段握手是串行的，HA 手里还没有 RSP 可发"],
        ["raced（同拍被抢）", 1029, 12.26,
         "试的时候 segment 已被在环 flit 拿走（在环优先，同拍内）"],
        ["hol（共享 FIFO 队头阻塞）", 234, 2.79,
         "flit 在两向共享的 12 深 FIFO 里，没能挪进本方向 inject Q"],
        ["其它", 0, 0.0, "无 —— 归因是完备的"],
    ],
    "attrib_total": 8392,
    # One intervention per candidate cause. `surcharge` + `idle` + 70000 equals
    # `makespan` on every row, so each row's cost is decomposable.
    "rows": [
        # case, thr, pct R*, surcharge, idle, makespan, deflections, Jbin
        ["现状（shipped）", 5.4681, 95.69, 1056, 2096, 73152, 2306, 0.87865],
        ["I-tag 休眠 t_inj=1e9", 5.4241, 94.92, 2622, 1123, 73745, 5643,
         0.81565],
        ["下环 eject 32", 5.4619, 95.58, 1146, 2089, 73235, 2291, 0.87642],
        ["下环 eject 64", 5.4787, 95.88, 782, 2228, 73010, 1535, 0.87642],
        ["下环 eject 64 + I-tag 休眠", 5.2206, 91.36, 3413, 3206, 76619, 6816,
         0.80562],
        ["core_outstanding 256", 4.5854, 80.24, 15010, 2224, 87234, 2550,
         0.85736],
        ["eject_bw 2（破规则，仅作上界）", 5.5134, 96.49, 0, 2550, 72550, 0,
         0.87767],
        ["eject_bw 2 + I-tag 休眠（破规则，仅作上界）", 5.6375, 98.66, 0, 954,
         70954, 0, 0.81597],
    ],
    "verdict": (
        "<b>没有达到，S0 停在 95.69% R*，缺口 4.31%。</b>"
        "缺口能<u>逐拍对齐地</u>拆成两块：1.44% 是绕环加价"
        "（1056 次多余穿越绑定 hop），2.87% 是绑定 hop 的空转（2096 拍）。"
        "根因排序是被实验定出来的，不是猜的：<b>①下环「两写一读」的读侧速率"
        "是唯一的结构性根因</b> —— 把 <code>eject_bw</code> 改成 2 让加价"
        "<u>精确归零</u>、偏转归零；而把 buffer 从 12 加深到 64 只把加价压到 "
        "782（32 档甚至更差，1146）。<b>加容量治不了，加速率才治得了</b>，"
        "说明短的是<u>速率</u>不是<u>容量</u>。"
        "<b>②I-tag 的空转不是浪费，是在买东西</b>：它占了空转的 53.97%，"
        "但休眠它之后空转只省下 973 拍、加价却涨了 1566 拍，净亏 —— "
        "吞吐从 95.69% 掉到 94.92%，Jain 从 0.87865 掉到 0.81565。"
        "I-tag 是在用空槽换绕环，汇率是划算的。"
        "<b>③不是供给不足</b>：core_outstanding 从 128 加到 256 使加价爆到 "
        "15010、吞吐崩到 80.24%，多灌载荷只会喂出更多绕环。"
        "两条根因是<b>耦合</b>的：只有先把读侧速率补上，I-tag 才变成纯开销"
        "（eject_bw=2 下休眠 I-tag 能到 98.66%）—— 也就是说 I-tag 的价值"
        "完全是下环速率不足的衍生品。"),
}

# Frozen: the variance decomposition that sets the phase-3 target. Per-bin
# unfairness is almost entirely timing jitter around near-equal long-run
# rates, so regularising injection timing can reach Jain > 0.99 without
# giving up bandwidth. Numbers are K=2500, 50-cycle bins.
# Frozen: the I-tag / E-tag audit against the specified semantics, and the
# measurement that shows where S0's distance from R* actually comes from.
# K=2000, seed 0, one plane; the same fabric as the official run except for the
# knob under test. `probe_ring2_hoputil.py`, `probe_ring2_idleslot.py` and
# `probe_ring2_tags.py` produce these.
TAG_AUDIT = {
    "k": 2000, "r_star": 5.7143, "link_lb": 7000, "port_lb": 5000,
    "base_thr": 5.1673, "base_pct": 90.43, "base_jbin": 0.94909,
    # The bound is a link bound, and the binding hop carries *exactly* its
    # routed load, so the whole shortfall is idle cycles on that one link.
    "hot": [
        # VC, hot hop, measured util, flits carried, routed load
        ["rsp", "1→0", 0.90428, 7000, 7000],
        ["dat", "8→7", 0.90428, 7000, 7000],
        ["req", "8→7", 0.45214, 3500, 3500],
    ],
    # Every wasted slot on the binding hops, classified. `port_none` is the
    # only column an arbiter or tagging fix could have moved, and it is zero.
    "waste": [
        # hop, who, wasted, dry, port(total), port->other dir, port->idle
        ["1→0 (rsp)", "mem", 741, 92, 649, 649, 0],
        ["11→10 (rsp)", "mem", 741, 83, 658, 658, 0],
        ["7→8 (rsp)", "mem", 741, 22, 719, 718, 1],
        ["8→7 (dat)", "core", 741, 328, 413, 413, 0],
        ["18→17 (dat)", "core", 741, 324, 417, 417, 0],
        ["0→1 (dat)", "core", 741, 276, 465, 465, 0],
    ],
    "span": 7741,
    # Breaking the 1 flit/cycle/node rule on purpose, to price the coupling.
    "per_dir_ports": {"thr": 5.4157, "pct": 94.77, "jbin": 0.85136,
                      "hot_rsp_util": 0.97604, "hot_dat_util": 0.95695,
                      "defl_rsp": 209, "defl_dat": 68},
    # Why neither mechanism ever fired as shipped.
    "dormant": {"starve_max": 41, "t_inj_shipped": 64, "n_defl": 0,
                "t_xfer_shipped": 4},
    # The two blocking semantics side by side at equal thresholds.
    "modes": [
        # label, t_inj, thr, thr vs dormant %, Jbin, tags raised, slots yielded
        ["broadcast（原实现，封停整个方向）", 4, 4.6954, -9.13, 0.98079, 7039, 0],
        ["broadcast", 8, 5.0422, -2.42, 0.96124, 981, 0],
        ["broadcast", 16, 5.1813, 0.27, 0.95201, 62, 0],
        ["segment（只封停会穿越的注入口）", 4, 5.1593, -0.15, 0.96513, 3907, 0],
        ["segment", 8, 5.1720, 0.09, 0.96251, 622, 0],
        ["reserve（规定语义：定向上游 + 单槽预约）", 2, 5.1533, -0.27, 0.95898,
         12146, 4748],
        ["reserve（选定）", 4, 5.1793, 0.23, 0.95788, 3205, 1554],
        ["reserve", 8, 5.1660, -0.03, 0.95412, 592, 331],
        ["reserve", 16, 5.1653, -0.04, 0.95017, 60, 34],
    ],
    # E-tag at the specified threshold, on this workload.
    "etag": [["t_xfer 4 → 1（规定值）", 5.1673, 0.0, 0],
             ["t_xfer 1 + resv_ej 0", 5.1673, 0.0, 0],
             ["t_xfer 1 + resv_ej 4", 5.1673, 0.0, 0]],
    # E-tag on the final fabric at the study's own K, from the full run's S0
    # digest. It is implemented to spec and covered by a directed test, but
    # this workload never reaches the condition that raises it: the two-write-
    # one-read port drains 1 flit/cycle, so although the 12-deep eject buffer
    # does touch its ceiling, no arriving flit is ever turned away.
    "etag_official": {
        "k": 20000, "eject_depth": 12, "max_ejectq": 12,
        "n_eject_full_deflect": 0, "n_deflections": 0, "n_etag_raised": 0,
        "note": (
            "E-tag 在 S0 上<b>一次都没有触发</b>：下环 buffer 占用峰值确实打到 12 "
            "（满），但两写一读端口每拍排掉 1 flit，预留槽足够吸收瞬时堆积，"
            "所以没有任何 flit 因为下环失败而被迫绕环（<code>n_deflections=0</code>）。"
            "这既说明规定的 E-tag 语义在本负载下是一条<b>活性保险</b>而非带宽机制，"
            "也从侧面确认了 8.7% 的带宽缺口与下环无关 —— 缺口全在上环端口。"),
    },
    "chosen": {"itag_mode": "reserve", "t_inj": 4, "t_xfer": 1,
               "thr": 5.1793, "thr_delta_pct": 0.23, "jbin": 0.95788,
               "jbin_delta": 0.00879},
    # What the two implementations did versus what was specified.
    "gaps": [
        ["I-tag 的作用范围",
         "规定：向<b>上游节点</b>发起，该节点暂停注入 / 让出一个空 slot",
         "原实现：置起后封停该 (plane, 方向, VC) 上<b>所有</b>其他注入口，"
         "包括 flit 根本不经过饥饿节点那个 hop 的",
         "已按规定实现（<code>itag_mode=reserve</code>）：标记沿环上溯到"
         "「其 flit 会占掉该 hop」的最近节点，只有它让路"],
        ["让出的 slot 归谁",
         "规定：空 slot 顺环流到发起节点，由发起节点放入 flit，然后清除标记",
         "原实现：无预约。只是封停别人，直到饥饿节点自己抢到 —— "
         "所以一个标记的代价是<b>整段饥饿时长</b>，不是一个 slot",
         "已实现单槽预约：记录 (让路节点, 气泡到达时刻)，"
         "只封停气泡还要经过的节点、且只封到它到达为止；"
         "发起节点上环即清除标记与预约"],
        ["I-tag 门限",
         "规定：上环「超过一定阈值的时间无空位」",
         "原实现门限 64 拍，而全程最长连续上环失败只有 41 拍 —— "
         "<b>结构上永远触发不了</b>（实测 n_itag_raised = 0）",
         "门限改为 4 拍：会触发，且在 reserve 语义下带宽不降反升 +0.23%"],
        ["E-tag 触发时机",
         "规定：因两写一读 buffer 满而<b>未能下环</b>即打标",
         "原实现要累计 4 次偏转才打标（<code>t_xfer=4</code>）",
         "改为 <code>t_xfer=1</code>"],
        ["E-tag 的优先级",
         "规定：再次到达目的节点时拥有<b>最高下环优先级</b>，"
         "在仲裁下环端口时<b>挤占普通 flit 的下环权</b>",
         "原实现<b>完全没有实现这一条</b>：下环仲裁 (<code>_leave_order</code>) "
         "只按方向轮转，根本不看 <code>e_tag</code>；"
         "E-tag 拿到的只是几个<b>额外的 buffer 表项</b>（<code>resv_ej</code>），"
         "那是「保留容量」而不是「优先级」——"
         "只要仲裁不让它先走，同一个 flit 可以反复丢掉下环权，"
         "而这正是该机制要防的无限循环",
         "已实现：<code>_leave_order</code> 把带 E-tag 的排在任何普通 flit 之前，"
         "多个 E-tag 之间按已绕环次数降序，"
         "回归 <code>etag_preempts_normal_leave</code> 钉住"],
    ],
}

# Written after the runs in probe_ring2_tags.py, against its FORECAST.
TAG_BELIEF = {
    "held": (
        "两条主要预测成立。按规定语义补全 I-tag <b>没有</b>把总带宽抬起来"
        "（最好 +0.25%，远不足 1%），"
        "所以「S0 达不到 R* 是因为 I-tag/E-tag 没实现」这个假设被否证；"
        "而 reserve 语义确实比 broadcast 便宜得多 —— 同一个门限 t_inj=4 上，"
        "reserve +0.23% vs broadcast −9.13%，差 9.4 个百分点。"
        "E-tag 按规定门限 t_xfer=1 在本工作负载上完全惰性（下环失败 0 次），"
        "与预测的 ±0.3% 一致。"
    ),
    "wrong": (
        "预测 broadcast 在 t_inj=8 上要掉 8~25%，实测只掉 2.42%。"
        "锚错了门限：之前报告里 −23% 那个点是 t_inj=2，"
        "而标记触发次数随门限陡降（t_inj=4 触发 7039 次，t_inj=8 只有 981 次），"
        "所以「broadcast 很贵」成立，但贵在低门限，不是在任何门限上都贵。"
    ),
    "why": (
        "更根本的一点是这轮才测出来的：缺口不是「有 flit 却没槽」，"
        "而是「有槽却没有端口去用它」。绑定 hop 上被浪费的槽里，"
        "port_none = 0 —— 没有一个是仲裁或标记失误造成的，"
        "全部是本节点每 VC 那一个上环端口在同一拍被<b>另一个方向</b>占用。"
        "I-tag 制造气泡，而这里气泡本来就有余；缺的是端口。"
        "这也解释了为什么把端口按方向拆开（故意违反 1 flit/cycle/node）"
        "能把占比从 90.43% 拉到 94.77%，而任何 I-tag 设置都不能。"
    ),
    "revised": (
        "R* 作为链路计数上限是对的，但它<b>不可达</b>，"
        "原因与 I-tag/E-tag 无关：一个节点的两条出向 hop 只能由它自己或 transit 填，"
        "而它每 VC 只有一个上环端口。要同时填满两条 hop，"
        "两条 hop 上的气泡必须在时间上恰好互补 —— 那是一个全局调度问题，"
        "无缓存环上的本地仲裁做不到。I-tag 的正确定位是公平性旋钮，"
        "E-tag 的正确定位是把下环失败造成的绕环<b>限制在一圈</b>，"
        "两者都不是带宽机制。"
    ),
}

JITTER_DECOMP = {
    "k": 2500, "bin_w": 50,
    "rows": [
        # label, per-core per-bin mean, var_between, var_within,
        # within share, Jain if timing were perfectly regular, max/min
        ["rr", 23.38, 0.016, 18.929, 0.999, 0.99997, 1.015],
        ["free_slot", 26.82, 1.004, 36.665, 0.973, 0.99861, 1.0982],
    ],
}


# Frozen: phase 2. Screening S1's AIMD for even CW/CCW board failures, on the
# spec-compliant fabric (I-tag reserve, E-tag priority), K=2000, 62 settings.
# The number to move is `failmax` -- the worst core's ratio between its two
# directions' board-failure counts.
#
# The headline is negative and it is the point: the spec I-tag already did
# this job. I-tag raises its flag on a (plane, dir, VC) key, so a node starved
# in CW gets its yielded bubble in CW -- the remedy lands on exactly the
# direction that is short. That drops S0's own failmax from 5.776 (broadcast
# fabric) to 3.846, and leaves the AIMD with nothing to equalize.
S1_DIRBAL = {
    "k": 2000, "s0_thr": 5.1793, "s0_failmax": 3.846, "s0_jbin": 0.95788,
    "n_settings": 62, "n_better_failmax": 5, "n_identical_to_s0": 7,
    "s0_failmax_broadcast": 5.776,
    "rows": [
        # label, failmax, cores with ratio>=2, thr, thr vs S0 %, Jbin, max/min
        ["S0（基线，规范 I-tag/E-tag）", 3.846, 5, 5.1793, 0.0, 0.95788, 1.1019],
        ["S1 默认", 5.54, 3, 4.4608, -13.87, 0.89281, 2.849],
        ["S1 cap=0.5", 5.28, 3, 4.3469, -16.07, 0.88898, 2.8818],
        ["S1 dir_split", 4.268, 5, 5.17, -0.18, 0.95559, 1.1445],
        ["S1 dir_split gentle w=64 burst=1（旧选定）", 3.94, 4, 5.1753, -0.08,
         0.95825, 1.1158],
        ["S1 dir_split w=64 burst=0（唯一保带宽且更均）", 3.829, 4, 5.15, -0.57,
         0.95217, 1.1028],
        ["S1 dir_split w=128 burst=0", 3.502, 4, 4.3048, -16.88, 0.87575,
         2.1858],
        ["S1 harsh cap=0.5", 3.472, 4, 4.111, -20.63, 0.86691, 2.5559],
    ],
    # Why the default is still bad: the AIMD decreases on the node's *own*
    # board failures, and on a bufferless ring those failures come from other
    # nodes' transit traffic, so the victim throttles itself.
    "note_default_bw": {"min": 0.2338, "max": 0.5909},
    "verdict": (
        "62 组里只有 5 组的 failmax 真的低于 S0 自己的 3.846，其中唯一不砍带宽的一组"
        "（dir_split w=64 burst=0）把 failmax 只压了 0.4%（3.846 → 3.829），"
        "却付掉 0.57% 带宽；真正压得动方向失败比的几组（3.47 ~ 3.50）一律要付 "
        "17% ~ 23% 带宽。另有 7 组的三项指标与 S0 逐位相同 —— 那是 AIMD 根本没有"
        "生效，等于 S0。所以阶段二的结论是：<b>在规范 fabric 上，方向失败比这条"
        "旋钮已经被 I-tag 转完了，AIMD 没有剩余可调空间</b>。"
    ),
    # Phase 2's second question: does even directional failure buy even
    # per-core bandwidth? Answered at official K in `S22_CONFIRM` -- no.
}

# Frozen: phase 3. Confirmation of the shortlist at the study's own K, all
# against the same S0 reference in the same process, on the spec-compliant
# fabric (I-tag reserve, E-tag priority).
S22_CONFIRM = {
    "k": 20000, "r_star": 5.7143,
    "targets": {"jain_bin_mean": 0.99, "thr_delta_pct": 1.0},
    "rows": [
        # label, Jbin, thr, thr vs S0 %, % of R*, max/min, failmax, pass
        ["S0", 0.96765, 5.2174, 0.00, 91.30, 1.0288, 3.864, False],
        ["S0 dirq=32（只加缓存，不加控制）", 0.96831, 5.2121, -0.10, 91.21,
         1.0304, 3.674, False],
        # S1_CFG, i.e. the same configuration the main run reports as S1T.
        ["S1 调优（阶段二，= S1T）", 0.96821, 5.1982, -0.37, 90.97, 1.0461,
         3.843, False],
        ["S22 w=3 margin=0", 0.99093, 4.9946, -4.27, 87.41, 1.0002, 2.383,
         False],
        ["S22 w=2 margin=2（旧基线上的选定点）", 0.99205, 5.1165, -1.93, 89.54,
         1.0001, 2.764, False],
        ["S22 w=3 margin=2", 0.99144, 5.1242, -1.79, 89.67, 1.0004, 2.687,
         False],
        ["S22 w=3 margin=3", 0.99114, 5.1887, -0.55, 90.80, 1.0002, 2.954,
         True],
        ["S22 w=2 margin=3", 0.99147, 5.1889, -0.55, 90.81, 1.0001, 2.776,
         True],
        ["S22 w=2 margin=4（选定）", 0.99062, 5.2153, -0.04, 91.27, 1.0002,
         3.001, True],
        ["S22 w=3 margin=4 thresh=1", 0.99005, 5.2022, -0.29, 91.04, 1.0004,
         2.882, True],
        ["S22 w=2 margin=4 thresh=1", 0.99030, 5.2074, -0.19, 91.13, 1.0003,
         3.225, True],
        ["S22 dirq=8 margin=3（不加缓存）", 0.99076, 5.1038, -2.18, 89.32,
         1.0001, 2.287, False],
    ],
}

# Frozen: phase 3. Why `margin=4` ships rather than `margin=3`, and how far
# the phase-3 claim extends.
#
# A seed sweep cannot break the tie: on the `uniform` pattern this study has
# no stochastic component -- the tiled channel hash is deterministic and
# `HA_RSP_JIT = 0` -- so seed 1 reproduces seed 0 bit for bit (verified). The
# axes that can move the answer are the ones that change the offered load.
S22_ROBUST = {
    "targets": {"jain_bin_mean": 0.99, "thr_delta_pct": 1.0},
    "no_seed_noise": True,
    "rows": [
        # pattern, K, label, thr, thr vs S0 %, Jbin, max/min, pass
        ["uniform", 10000, "S0", 5.2120, 0.0, 0.96772, 1.0400, None],
        ["uniform", 10000, "S22 m=3", 5.1850, -0.52, 0.99161, 1.0003, True],
        ["uniform", 10000, "S22 m=4", 5.2062, -0.11, 0.99062, 1.0013, True],
        ["uniform", 20000, "S0", 5.2174, 0.0, 0.96765, 1.0288, None],
        ["uniform", 20000, "S22 m=3", 5.1889, -0.55, 0.99147, 1.0001, True],
        ["uniform", 20000, "S22 m=4", 5.2153, -0.04, 0.99062, 1.0002, True],
        ["hot", 5000, "S0", 0.9420, 0.0, 0.57664, 1.0704, None],
        ["hot", 5000, "S22 m=3", 0.9954, 5.67, 0.70654, 1.0922, False],
        ["hot", 5000, "S22 m=4", 0.9973, 5.87, 0.71590, 1.0881, False],
    ],
    "choice": (
        "两个候选在<b>环受限</b>的 uniform 上都过线，但 margin=4 的 Jain "
        "在 K 翻一倍时稳定在 0.99062（五位小数不动），说明它 6e-4 的余量是"
        "<b>稳的</b>而不是运气；margin=3 多 1.5e-3 的 Jain 余量，"
        "却要一直付 0.55% 带宽。"),
    "scope": (
        "在 hot 上（所有写打进一个两节点 mem 簇）总带宽只有 0.94 flit/cycle，"
        "不到 uniform 的 1/5 —— 瓶颈已经从环挪到了 completer，"
        "50 拍窗内的差异主要由 HA 的串行服务决定，"
        "任何<b>环侧</b>控制器都拿不到 0.99（S0 自己只有 0.577）。"
        "值得记一笔的是 S22 在那里也<b>没有变坏</b>："
        "Jain 0.577 → 0.716，总带宽还 +5.9%（让路让排队更早交给了空闲的 HA）。"),
}

# Frozen: phase 3, the two mechanisms that were tried and rejected, and why.
# Both are recorded because each one's failure mode is what motivates S22.
S22_REJECTED = {
    "rows": [
        # label, Jbin, thr vs S0 %, the structural reason
        ["I-tag 加 scope=segment + hold（只改仲裁，不加总线）", 0.99013, -5.8,
         "I-tag 只能整段封停，被 transit 饿死的节点会一直举旗，"
         "让上游注入口空转 —— 让出的槽位没有交给落后的节点"],
        ["S21 定速漏桶（sender pacing，burst=1）", 0.99340, -17.0,
         "信用闸门只能'扣住'，而环上空槽是不规则出现的，"
         "扣住就错过；burst=1 还把速率量化到 1/2"],
        ["S21 定速漏桶（burst=2）", 0.99210, -10.0,
         "放大桶深能拿回速率，但同样的桶深也让突发漏回来，这笔交易不闭合"],
    ],
    # Provenance: these three were measured on the pre-fix fabric (I-tag in
    # broadcast mode, E-tag without eject priority). They are kept as the
    # design rationale for S22 -- each row's failure mode is structural and the
    # I-tag fix does not touch it, since neither mechanism's problem was the
    # baseline's fairness. They are not re-measured because both were rejected
    # on a structural argument, not on a threshold.
    "note_fabric": (
        "这三行测于<b>补全前</b>的 fabric（I-tag broadcast、E-tag 无下环优先）。"
        "保留原值是因为每一行被否掉的理由都是<b>结构性</b>的 —— "
        "「扣住的槽没人接手」和「闸门错过不规则空槽」这两件事，"
        "与基线本身齐不齐无关，规范 I-tag 的修正碰不到它们。"),
}

# Frozen: hardware cost, counted per node against S1's own bill. Both schemes
# ride one dedicated broadcast bus; the question the report has to answer is
# whether S22 needs anything S1 does not.
HW_COST = {
    "n_nodes": 20, "n_cores": 10, "bus_bits": 6,
    "rows": [
        # item, S1, S22, comment
        ["专有流控总线", "1 条广播，6 bit/次发布（两个 3 bit 拥塞等级）",
         "1 条广播，6 bit/次发布（本窗上环计数，饱和）",
         "同一条总线、同一位宽。S22 复用 S1 的线，只换了线上放什么"],
        ["总线接收表", "每节点存全环视图（20 × 6 bit）",
         "每节点存参与核的累计计数（10 × 8 bit 模加）",
         "两边都要一张全局表，S22 的项数更少但要累加；"
         "赤字被 dfc_cap=64 夹住，8 bit 模加就够，不会真的无界增长"],
        ["总线时延", "30 拍（窗口 64 的一半）", "1 拍",
         "S22 唯一比 S1 更苛刻的地方：它要的是瞬时进度，"
         "反馈晚一个窗口就退化成长期均值（灵敏度见 6.5.1）"],
        ["窗口计数器", "1 个 7 bit（window=64）", "1 个 2 bit（window=2）",
         "S22 的控制窗口短得多，计数器反而更窄"],
        ["每节点算术", "α/β 乘法 + 预算按比例缩放 + 令牌桶",
         "10 项加法树求均值 + 减法 + 两次比较",
         "S22 去掉了乘法器和令牌桶，换成加法树；"
         "均值可用 10 项定点加法 + 常数乘 1/10（或按 8 近似）"],
        ["注入闸门", "令牌桶，可扣住空闲槽",
         "无闸门；只在仲裁时让路",
         "这是两者机制上的根本差别，也是 S22 不丢带宽的原因"],
        ["inject Q 深度", "每向 8", "每向 32",
         "唯一实打实的额外面积：前瞻要有候选可选。"
         "深度 8 上同一控制器带宽掉 2.18%（见 6.3 表末行与 6.5.2）"],
        ["保序逻辑", "不需要（先到先发）",
         "需要：前瞻只允许跨目的地超越",
         "每拍最多比较 32 个目的地标签，遇到同目的地即停，"
         "所以同一个 WriteData burst 内部永不换序"],
    ],
    "verdict": (
        "同一条 6 bit 广播总线、同一量级的节点状态：S22 把 S1 的"
        "「乘法器 + 令牌桶 + 20 项视图」换成「加法树 + 10 项模加表」，"
        "算术更浅。代价有两笔：总线时延必须做到 1 拍，"
        "inject Q 从每向 8 加深到 32（含 ≤32 项目的地比较的前瞻逻辑）。"
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
    if scheme == "S22":
        from rg_ring2_dfc import Ring2DfcParams, Ring2DfcSim
        kw = {**FABRIC, **S22_CFG, **(cfg or {})}
        return Ring2DfcSim(topo, Ring2DfcParams(**kw), seed=seed)
    if scheme == "S21":
        from rg_ring2_pace import Ring2PaceParams, Ring2PaceSim
        return Ring2PaceSim(topo, Ring2PaceParams(**kw), seed=seed)
    if scheme in ("S17", "S18", "S19", "S20"):
        from rg_ring2_rate import (Ring2DcqcnSim, Ring2DctcpSim,
                                   Ring2RateParams, Ring2SwiftSim,
                                   Ring2TimelySim)
        cls = {"S17": Ring2TimelySim, "S18": Ring2DcqcnSim,
               "S19": Ring2SwiftSim, "S20": Ring2DctcpSim}[scheme]
        return cls(topo, Ring2RateParams(**kw), seed=seed)
    from rg_ring2_fc import Ring2FcParams, Ring2FcSim
    kw = {"bus_lat": FC_BUS_LAT, **kw}
    # "S1T" is S1 with the phase-2 direction-balanced settings. It is a
    # separate name so the published S1 rows keep meaning stock AIMD.
    if scheme == "S1T":
        kw = {"bus_lat": FC_BUS_LAT, **FABRIC, **S1_CFG, **(cfg or {})}
    p = Ring2FcParams(mode="s15" if scheme == "S15" else "s1", **kw)
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


def binned_jain(inject_times: dict[int, list[int]], bin_w: int,
                t_fair: int) -> dict[str, Any]:
    """Mean over `bin_w`-cycle bins of Jain across the cores in that bin.

    This is the headline fairness number. A whole-run figure cannot show
    anything: in a closed batch every core injects the same `K*W` flits, so
    end-of-run counts are equal by construction and Jain is 1 by arithmetic.
    Averaging the per-bin index keeps the instantaneous view instead.

    Only bins wholly inside the contention window count. Past `t_fair` the
    first core has run out of work, and a zero there is an empty queue, not
    an unfair arbiter.

    A bin this short holds few flits per core, so the index also carries
    counting noise, and 1.0 is not reachable. `jain_bin_null` is where a
    perfectly fair arbiter would land when observed through the same window:
    split each bin's total multinomially over the cores at equal probability
    and the per-core count has mean N/n and variance N(1/n)(1-1/n), so

        E[J] ~ 1/(1 + CV^2) = N / (N + n - 1)

    per bin. `jain_bin_ratio = mean / null` is then the number to judge:
    >= 1.0 means the fabric is at least as even as perfectly fair
    arbitration. An absolute threshold on `jain_bin_mean` would be
    meaningless, because the floor moves with the flit count per bin and a
    scheme that costs throughput lowers its own floor.

    The closed form is first order (1/(1+E[CV^2]) rather than E[1/(1+CV^2)]),
    so it runs ~1e-3 below an actual equal-probability draw -- the acceptance
    line is lenient by about that much. `verify_ring2_20` pins the gap.
    """
    cs = sorted(inject_times)
    nbin = int(t_fair) // bin_w if bin_w > 0 else 0
    if not cs or nbin <= 0:
        return {}
    n = len(cs)
    cnt = [[0] * nbin for _ in cs]
    for i, c in enumerate(cs):
        for t in inject_times[c]:
            b = int(t) // bin_w
            if 0 <= b < nbin:
                cnt[i][b] += 1
    tot = [sum(cnt[i][b] for i in range(n)) for b in range(nbin)]
    vals = sorted(jain([cnt[i][b] for i in range(n)]) for b in range(nbin))
    per_bin = sum(vals) / nbin
    null = sum(N / (N + n - 1) if N else 0.0 for N in tot) / nbin
    return {
        "bin_w": bin_w, "n_bins": nbin, "n_cores": n,
        "jain_bin_mean": round(per_bin, 5),
        "jain_bin_null": round(null, 5),
        "jain_bin_ratio": round(per_bin / null, 5) if null else None,
        "jain_bin_p05": round(vals[int(0.05 * nbin)], 5),
        "jain_bin_min": round(vals[0], 5),
        "flits_per_core_per_bin": round(sum(tot) / n / nbin, 2),
    }


def digest(r: dict[str, Any], *, flits_per_core: int, bin_w: int
           ) -> dict[str, Any]:
    """Trim a raw run down to what the report needs."""
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    fair = fairness_stats(inj, r.get("makespan") or 1, flits_per_core)
    fair["jain_bin"] = binned_jain(inj, bin_w, fair.get("t_fair") or 0)
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
        "n_itag_yield": r.get("n_itag_yield", 0),
        "n_etag_raised": r.get("n_etag_raised", 0),
        # Measured occupancy of every directed hop, and the longest run of
        # consecutive failed boards: the two things the R* comparison and the
        # I-tag threshold have to be judged against.
        "hop_use": r.get("hop_use", {}),
        "starve": r.get("starve", {}),
        "n_outst_wait": r.get("n_outst_wait", 0),
        "max_core_outstanding": r.get("max_core_outstanding", 0),
        "max_srcq": r.get("max_srcq"), "max_ejectq": r.get("max_ejectq"),
        "buffers": r.get("buffers") or {},
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
    inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
    f = fairness_stats(inj, r["makespan"] or 1, k * W)
    jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
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
        "jain_bin": jb.get("jain_bin_mean"),
        "jain_bin_null": jb.get("jain_bin_null"),
        "jain_bin_ratio": jb.get("jain_bin_ratio"),
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
    jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
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
        "jain_bin": jb.get("jain_bin_mean"),
        "jain_bin_null": jb.get("jain_bin_null"),
        "jain_bin_ratio": jb.get("jain_bin_ratio"),
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
            inj = {int(c): v for c, v in r["wr_inject_by_core"].items()}
            f = fairness_stats(inj, r["makespan"], k * W)
            jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
            thr0 = f["throughput"] if scheme == "S0" else thr0
            row[scheme] = {
                "jain": f["jain"], "max_min": f["max_min"],
                "jain_bin": jb.get("jain_bin_mean"),
                "jain_bin_null": jb.get("jain_bin_null"),
                "jain_bin_ratio": jb.get("jain_bin_ratio"),
                "throughput": f["throughput"],
                "thr_delta_pct": (
                    round(100.0 * (f["throughput"] - thr0) / thr0, 2)
                    if thr0 else 0.0),
            }
        rows.append(row)
        print(f"    seed {sd}: " + "  ".join(
            f"{s} Jbin={row[s]['jain_bin']}/{row[s]['jain_bin_null']} "
            f"mm={row[s]['max_min']} "
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
    print(f"{'scheme':6} {'mk':>8} {'ok':>3} {'Jbin':>8} {'null':>8} "
          f"{'ratio':>7} {'max/min':>8} {'bwmin':>8} {'thr':>7} {'fail':>9}")
    for name, d in pat["schemes"].items():
        f = d["fairness"]
        jb = f.get("jain_bin") or {}
        print(f"{name:6} {d['makespan']:>8} {int(bool(d['completed'])):>3} "
              f"{jb.get('jain_bin_mean', 0):>8} {jb.get('jain_bin_null', 0):>8} "
              f"{jb.get('jain_bin_ratio', 0):>7} {f['max_min']:>8} "
              f"{f['bw_min']:>8} {f['throughput']:>7} "
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
            "t_ha_service": bp.t_ha_service,
            "ha_rsp_zero_forecast": HA_RSP_ZERO_FORECAST,
            "bin50_fair_forecast": BIN50_FAIR_FORECAST,
            "track128_forecast": TRACK128_FORECAST,
            "track256_forecast": TRACK256_FORECAST,
            "upq_12_8_forecast": UPQ_12_8_FORECAST,
            "eject12_bufocc_forecast": EJECT12_BUFOCC_FORECAST,
            "inj_sel_forecast": INJ_SEL_FORECAST,
            "inj_sel_probe": INJ_SEL_PROBE,
            "jitter_decomp": JITTER_DECOMP,
            "inj_sel": bp.inj_sel,
            "t_inj": bp.t_inj, "t_xfer": bp.t_xfer, "resv_ej": bp.resv_ej,
            "buf_sample": bp.buf_sample,
            "bus_lat": FC_BUS_LAT,
            "bus_lat_forecast": BUS_LAT_FORECAST,
            "per_vc_ports": bp.per_vc_ports,
            "per_dir_ports": bp.per_dir_ports,
            "perdir_probe": PERDIR_PROBE,
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
