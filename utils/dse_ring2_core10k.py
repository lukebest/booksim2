#!/usr/bin/env python3
"""Same-pattern, 10000 response flits/core: S0–S14.

Workload: uniform random HA, K=2500 txns/core, R=4 → 10000 recv flits/core.
Compares receive-bandwidth time series and per-destination-core on-ramp
counts (CW / CCW successes and failures).

All fifteen schemes ride the same datapath: per-link hop delays (1–4 cycles), 8-deep boarding
queue per (node, plane), point-to-point credit, I-tag / E-tag, and a
100 outstanding-read cap per AI core.

Writes results/ring2_core10k.json and the comparison PNGs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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
from rg_ring2_topo import Ring2Topology, build_uniform, cores, paths_for_txns

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "ring2_core10k.json"
R_FLITS = 4
K_PER_CORE = 2_500           # 2500 * 4 = 10000 recv flits / core
BIN_W = 64
FLITS_PER_CORE = K_PER_CORE * R_FLITS


def _bin_rate(times: list[int], t_max: int, bin_w: int = BIN_W
              ) -> tuple[list[int], list[float]]:
    nbin = max(1, (t_max + bin_w) // bin_w)
    rate = [0.0] * nbin
    for t in times:
        rate[min(max(int(t), 0) // bin_w, nbin - 1)] += 1.0 / bin_w
    return [i * bin_w for i in range(nbin)], rate


def _run(scheme: str, topo, txns, seed: int) -> dict:
    p = Ring2BaseParams(plane_sel="least_occupied")
    t0 = time.perf_counter()
    print(f"  running {scheme}  n_txn={len(txns)} ...", flush=True)
    if scheme == "S0":
        r = run_base(topo, txns, params=p, seed=seed)
    elif scheme == "S1":
        r = run_aimd(topo, txns, params=p, seed=seed)
    elif scheme == "S3":
        r = run_pop(topo, txns, params=p, seed=seed)
    elif scheme == "S4":
        r = run_dist(topo, txns, params=Ring2DistParams(
            plane_sel="least_occupied", leave_useful=True), seed=seed)
    elif scheme == "S5":
        r = run_dist(topo, txns, params=s5_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S6":
        r = run_dist(topo, txns, params=s6_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S7":
        r = run_dist(topo, txns, params=s7_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S8":
        r = run_dist(topo, txns, params=s8_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S9":
        r = run_dist(topo, txns, params=s9_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S10":
        r = run_dist(topo, txns, params=s10_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S11":
        r = run_dist(topo, txns, params=s11_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S12":
        r = run_dist(topo, txns, params=s12_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S13":
        r = run_dist(topo, txns, params=s13_params(plane_sel="least_occupied"),
                     seed=seed)
    elif scheme == "S14":
        r = run_dist(topo, txns, params=s14_params(plane_sel="least_occupied"),
                     seed=seed)
    else:
        r = run_rg(topo, txns, cfg=RGConfig(
            algo="islip", iters=2, plane_sel="least_occupied", seed=seed),
                   skip_replay=len(txns) >= 20_000)
    recv = {int(k): v for k, v in (r.get("recv_by_core") or {}).items()}
    board = {int(k): v for k, v in (r.get("board_by_core") or {}).items()}
    print(f"    {scheme} mk={r.get('makespan')} ok={r.get('completed')} "
          f"{time.perf_counter() - t0:.1f}s  "
          f"recv0={len(recv.get(0, []))}", flush=True)
    return {
        "scheme": scheme,
        "makespan": r.get("makespan"),
        "completed": r.get("completed"),
        "n_delivered_flits": r.get("n_delivered_flits"),
        "n_txn_done": r.get("n_txn_done"),
        "n_board_fail": r.get("n_board_fail", 0),
        "n_deflections": r.get("n_deflections", 0),
        "max_srcq": r.get("max_srcq"),
        # S2 only: flits released but not yet boarded. A granted source knows
        # its own t0, so this is backlog demand, not required queue depth.
        "max_src_wait": r.get("max_src_wait"),
        "max_ejectq": r.get("max_ejectq", 0),
        "n_admit_stall": r.get("n_admit_stall", 0),
        "lat_p50": r.get("lat_p50"),
        "lat_p99": r.get("lat_p99"),
        "n_pull_issued": r.get("n_pull_issued", 0),
        "max_pull_outstanding": r.get("max_pull_outstanding", 0),
        "max_core_outstanding": r.get("max_core_outstanding", 0),
        "n_outst_wait": r.get("n_outst_wait", 0),
        "recv_by_core": recv,
        "board_by_core": board,
        "hop_starts": list(r.get("hop_starts") or []),
        "wall_secs": round(time.perf_counter() - t0, 1),
    }


def plot_panels(traces: dict[str, dict], path: Path, *, bin_w: int,
                k: int, r_flits: int, flits_per_core: int) -> None:
    cs = cores()
    cmap = plt.get_cmap("tab10")
    t_max_all = max(
        (max((max(ts) for ts in tr["recv_by_core"].values()), default=0)
         for tr in traces.values()),
        default=1)
    fig, axes = plt.subplots(15, 1, figsize=(9.6, 36.8), sharex=True)
    for ax, scheme in zip(axes, ("S0", "S1", "S2", "S3", "S4", "S5", "S6",
                                 "S7", "S8", "S9", "S10", "S11", "S12",
                                 "S13", "S14")):
        tr = traces[scheme]
        t_max = max((max(ts) for ts in tr["recv_by_core"].values()), default=1)
        mean = None
        xs = []
        for i, c in enumerate(cs):
            xs, ys = _bin_rate(tr["recv_by_core"].get(c, []), t_max, bin_w)
            ax.plot(xs, ys, color=cmap(i % 10), lw=0.9, alpha=0.8,
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
    fig.suptitle(
        f"Per-core receive bandwidth  ·  uniform K={k} R={r_flits}  "
        f"({flits_per_core} flits/core)",
        fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_overlay(traces: dict[str, dict], path: Path, *, bin_w: int,
                 flits_per_core: int, bound: int | None = None) -> None:
    """Same axes: mean recv bandwidth of the five schemes."""
    colors = {"S0": "#2563eb", "S1": "#16a34a", "S2": "#dc2626",
              "S3": "#9333ea", "S4": "#ea580c", "S5": "#0d9488",
              "S6": "#c026d3", "S7": "#7c3aed", "S8": "#ca8a04",
              "S9": "#be123c", "S10": "#047857", "S11": "#9a3412",
              "S12": "#4338ca", "S13": "#0369a1", "S14": "#db2777"}
    # S3 is drawn dashed on top of S0: after the outstanding-cap
    # alignment the two means coincide, and a second solid line would
    # hide S0 completely.
    styles = {"S0": "-", "S1": "-", "S2": "-", "S3": (0, (5, 2.5)),
              "S4": "-", "S5": "-", "S6": "-", "S7": "-", "S8": "-",
              "S9": "-", "S10": "-", "S11": "-", "S12": "-", "S13": "-",
              "S14": "-"}
    fig, ax = plt.subplots(figsize=(9.2, 4.2))
    t_max_all = max(
        (max((max(ts) for ts in tr["recv_by_core"].values()), default=0)
         for tr in traces.values()),
        default=1)
    cs = cores()
    for scheme in ("S1", "S2", "S0", "S3", "S4", "S5", "S6", "S7", "S8",
                   "S9", "S10", "S11", "S12", "S13", "S14"):
        tr = traces[scheme]
        acc = None
        xs = []
        for c in cs:
            xs, ys = _bin_rate(tr["recv_by_core"].get(c, []), t_max_all, bin_w)
            if acc is None:
                acc = [0.0] * len(ys)
            for j, y in enumerate(ys):
                acc[j] += y / len(cs)
        ax.plot(xs, acc, color=colors[scheme], lw=1.8, linestyle=styles[scheme],
                label=f"{scheme}  mk={tr['makespan']}")
    if bound and bound > 0:
        # Constant-rate drain that meets the analytic makespan floor:
        # flits_per_core delivered in `bound` cycles, then drop to 0.
        rate_lb = flits_per_core / bound
        ax.plot([0, bound, bound, t_max_all],
                [rate_lb, rate_lb, 0.0, 0.0],
                color="#111827", lw=1.4, ls=":",
                label=f"bound  mk≥{bound}  {rate_lb:.2f} flit/cyc")
        ax.axvline(bound, color="#111827", lw=0.7, ls=":", alpha=0.45)
    ax.set_xlabel("cycle")
    ax.set_ylabel("mean recv flit / cycle / core")
    ax.set_title(
        f"S0–S14 on the same uniform batch  "
        f"({flits_per_core} flits/core, bin={bin_w})")
    ax.set_xlim(0, t_max_all)
    ax.set_ylim(bottom=0)
    ax.grid(True, ls=":", alpha=0.45)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_directed_link_bw(traces: dict[str, dict], path: Path, *,
                          bin_w: int, title: str, cap: int = 80) -> None:
    """Sum of directed-hop launches / cycle (REQ+DAT VCs, cap = 160)."""
    colors = {"S0": "#2563eb", "S1": "#16a34a", "S2": "#dc2626",
              "S3": "#9333ea", "S4": "#ea580c", "S5": "#0d9488",
              "S6": "#c026d3", "S7": "#7c3aed", "S8": "#ca8a04",
              "S9": "#be123c", "S10": "#047857", "S11": "#9a3412",
              "S12": "#4338ca", "S13": "#0369a1", "S14": "#db2777"}
    styles = {"S0": "-", "S1": "-", "S2": "-", "S3": (0, (5, 2.5)),
              "S4": "-", "S5": "-", "S6": "-", "S7": "-", "S8": "-",
              "S9": "-", "S10": "-", "S11": "-", "S12": "-", "S13": "-",
              "S14": "-"}
    t_max_all = max((tr.get("makespan") or 1) for tr in traces.values())
    fig, ax = plt.subplots(figsize=(9.2, 4.4))
    for scheme in ("S1", "S2", "S0", "S3", "S4", "S5", "S6", "S7", "S8",
                   "S9", "S10", "S11", "S12", "S13", "S14"):
        tr = traces.get(scheme)
        if not tr:
            continue
        hops = tr.get("hop_starts")
        hb = tr.get("hop_bw") or {}
        if hops:
            n_hops = len(hops)
            xs, rate = _bin_rate(hops, t_max_all, bin_w)
        elif hb.get("t") and hb.get("rate"):
            xs, rate = hb["t"], hb["rate"]
            n_hops = hb.get("n_hops", "?")
        else:
            continue
        ax.plot(xs, rate, color=colors[scheme], lw=1.7,
                linestyle=styles[scheme],
                label=f"{scheme}  mk={tr.get('makespan')}  hops={n_hops}")
    ax.axhline(cap, color="#111827", lw=1.3, ls=":",
               label=f"REQ+DAT hop cap  {cap} flit/cyc")
    ax.set_xlabel("cycle")
    ax.set_ylabel("directed-hop starts / cycle")
    ax.set_title(title)
    ax.set_xlim(0, t_max_all)
    ax.set_ylim(bottom=0)
    ax.grid(True, ls=":", alpha=0.45)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=K_PER_CORE)
    ap.add_argument("--R", type=int, default=R_FLITS)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--quick", action="store_true",
                    help="K=500 (2000 flits/core) smoke")
    args = ap.parse_args()
    k = 500 if args.quick else args.k
    R = args.R
    flits = k * R
    bin_w = 16 if args.quick else 64

    topo = Ring2Topology()
    txns = build_uniform(k=k, m_resp=R, seed=args.seed)
    rp, sp = paths_for_txns(topo, txns, strategy="least_occupied")
    bounds = topo.analytic_bounds(rp, sp, m_req=1, m_resp=R)
    t0 = time.perf_counter()
    traces = {}
    for scheme in ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8",
                   "S9", "S10", "S11", "S12", "S13", "S14"):
        traces[scheme] = _run(scheme, topo, txns, args.seed)

    panel = ROOT / "results" / "ring2_core_recv_bw_10k.png"
    overlay = ROOT / "results" / "ring2_core_recv_bw_10k_overlay.png"
    link_bw = ROOT / "results" / "ring2_link_bw_10k.png"
    plot_panels(traces, panel, bin_w=bin_w, k=k, r_flits=R,
                flits_per_core=flits)
    plot_overlay(traces, overlay, bin_w=bin_w, flits_per_core=flits,
                 bound=bounds["bound"])
    plot_directed_link_bw(
        traces, link_bw, bin_w=bin_w,
        title=f"Network directed-hop bandwidth  ·  uniform K={k} R={R}  "
              f"({flits} flits/core, bin={bin_w})",
        cap=topo.hop_bw_cap)

    slim = {
        "meta": {
            "pattern": "uniform", "K": k, "R": R, "seed": args.seed,
            "flits_per_core": flits, "bin_w": bin_w,
            "plane_sel": "least_occupied",
            "hop_lat": topo.hop_lat,
            "link_lats": list(topo.link_lats),
            "inj_depth": Ring2BaseParams().inj_depth,
            "eject_depth": Ring2BaseParams().eject_depth,
            "pop_window": Ring2BaseParams().pop_window,
            "core_outstanding": Ring2BaseParams().core_outstanding,
            "bound": bounds["bound"],
            "link_lb": bounds["link_lb"],
            "port_lb": bounds["port_lb"],
            "cut_lb": bounds["cut_lb"],
            "n_vc": topo.n_vc,
            "hop_bw_cap": topo.hop_bw_cap,
            "single_txn_lb": bounds["single_txn_lb"],
            "ideal_recv_rate": round(flits / bounds["bound"], 4),
            "aimd": {
                "alpha": Ring2BaseParams().alpha,
                "beta": Ring2BaseParams().beta,
                "epoch": Ring2BaseParams().epoch,
                "rate_min": Ring2BaseParams().rate_min,
            },
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "schemes": {},
        "wall_secs": round(time.perf_counter() - t0, 1),
    }
    for name, tr in traces.items():
        rates = {}
        t_max = tr["makespan"] or 1
        for c, ts in tr["recv_by_core"].items():
            xs, ys = _bin_rate(ts, t_max, bin_w)
            rates[str(c)] = {"t": xs, "rate": [round(y, 4) for y in ys],
                             "n_recv": len(ts)}
        hop_xs, hop_ys = _bin_rate(tr.get("hop_starts") or [], t_max, bin_w)
        slim["schemes"][name] = {
            "makespan": tr["makespan"],
            "completed": tr["completed"],
            "n_delivered_flits": tr["n_delivered_flits"],
            "n_txn_done": tr["n_txn_done"],
            "wall_secs": tr["wall_secs"],
            "n_deflections": tr["n_deflections"],
            "max_srcq": tr["max_srcq"],
            "max_src_wait": tr["max_src_wait"],
            "max_ejectq": tr["max_ejectq"],
            "n_admit_stall": tr["n_admit_stall"],
            "lat_p50": tr["lat_p50"],
            "lat_p99": tr["lat_p99"],
            "n_pull_issued": tr["n_pull_issued"],
            "max_pull_outstanding": tr["max_pull_outstanding"],
            "max_core_outstanding": tr["max_core_outstanding"],
            "n_outst_wait": tr["n_outst_wait"],
            "board_by_core": {str(c): v for c, v in tr["board_by_core"].items()},
            "recv_binned": rates,
            "hop_bw": {
                "t": hop_xs, "rate": [round(y, 3) for y in hop_ys],
                "n_hops": len(tr.get("hop_starts") or []),
            },
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(slim, indent=1))
    print(f"wrote {OUT}  {slim['wall_secs']}s")
    print(f"{'scheme':6} {'mk':>8} {'ok':>3} {'board':>7} {'fail':>8} "
          f"{'cw':>6} {'ccw':>6} {'defl':>6} {'srcq':>5} {'wait':>6} "
          f"{'ejq':>4} {'p50':>7} {'p99':>7}")
    for name, tr in traces.items():
        b = tr["board_by_core"]
        board = sum(v["board"] for v in b.values())
        fail = sum(v["board_fail"] for v in b.values())
        cw = sum(v["board_cw"] for v in b.values())
        ccw = sum(v["board_ccw"] for v in b.values())
        print(f"{name:6} {tr['makespan']:>8} {int(tr['completed']):>3} "
              f"{board:>7} {fail:>8} {cw:>6} {ccw:>6} "
              f"{tr['n_deflections']:>6} {str(tr['max_srcq']):>5} "
              f"{str(tr['max_src_wait']):>6} "
              f"{tr['max_ejectq']:>4} {str(tr['lat_p50']):>7} "
              f"{str(tr['lat_p99']):>7}")


if __name__ == "__main__":
    main()
