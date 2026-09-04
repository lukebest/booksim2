#!/usr/bin/env python3
"""ReadNoSnp bandwidth, instantaneous fairness, and hardware Pareto study.

The read/write comparison keeps the fabric and address stream fixed.  Only the
CHI payload direction changes:

    write: REQ core→HA, DBIDResp HA→core, WriteData core→HA, Comp HA→core
    read:  REQ core→HA, CompData×2 HA→core

Headline read fairness is the mean Jain index of CompData *received by each
core* in complete 50-cycle bins while all ten cores still have demand.  HA
injection Jain is retained as a diagnostic, but is not substituted for what a
core actually observes.

Forecast recorded before the official K=5000 run (after a K=100 smoke test and
the earlier random-read S16 probe):
  * Uniform equal-rate bandwidth remains 40/7 CompData flit/cycle by topology
    symmetry; max-total is higher only by starving disadvantaged cores.
  * S0 should reach >90% of that bound but remain below 0.99 binned Jain.
  * S1-R must control HA CompData injection, not requester REQs.  Stock S1 is
    expected to lose bandwidth; the direction-split setting should be closer
    to S0, but neither signal identifies the destination core.
  * S16-R should be the strongest low-cost candidate because the HA can select
    the least-served destination locally.  A 30-cycle bus cannot add
    information to that decision and should not improve both metrics.
  * S18/S20's write ECN signal is absent: ReadNoSnp has no DBIDResp and this
    model has no read tracker.  Their measured rows must be marked inapplicable
    rather than interpreted as successful read controllers.

Usage:
    PYTHONHASHSEED=0 python3 dse_ring2_read_fair.py [K]
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (
    BIN_W, CORE_NODES, FABRIC, MEM_NODES, S1_CFG, S22_CFG, W_FLITS,
    bin_rate, binned_jain, fairness_stats, jain_ideal_bin, run_scheme,
)
from ideal_ring2_cc import coefficients, jain, solve_max_total, solve_theta
from pareto_ring2_cc import hw_cost
from rg_ring2_topo import (
    CHI_VCS, Ring2Topology, Txn, build_hot_read, build_tiled_read,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "ring2_read_fair.json"
PARETO_PNG = ROOT / "results" / "ring2_read_pareto.png"
S0_TIME_PNG = ROOT / "results" / "ring2_read_s0_timeseries.png"
HOT_HAS = (11, 13)
R_FLITS = W_FLITS
CORE_OUTSTANDING = 128

# The FF-equivalent accounting is identical to the write Pareto model, with
# explicit scope for state that exists only at HAs.  S16 includes the
# least-served table; calling it "free" would hide the state doing the fairness
# work.  S23-R needs one rate/credit pair per (HA, destination core, direction).
HW_NONE: dict[str, Any] = {}
HW_ITAG = {"counter_bits": 6 * 6, "arith": {"cmp": 6}}
HW_S1 = {"bus_bits": 6, "table_entries": 20, "table_bits": 6,
         "counter_bits": 15, "arith": {"mult": 2, "add": 2, "cmp": 2}}
HW_S15 = {**HW_S1, "arith": {"mult": 2, "add": 3, "cmp": 3}}
HW_S16 = {"table_entries": 10, "table_bits": 16, "table_scope": 10,
          "counter_bits": 8, "counter_scope": 10,
          "arith": {"cmp": 2, "add": 1}, "arith_scope": 10}
HW_S16_RR = {"counter_bits": 12, "counter_scope": 10,
             "arith": {"cmp": 1, "add": 1}, "arith_scope": 10}
HW_RATE = {"counter_bits": 8 * 4, "counter_scope": 10,
           "arith": {"ewma": 1, "mult": 1, "cmp": 3}, "arith_scope": 10}
HW_WIN = {"counter_bits": 8 * 3, "counter_scope": 10,
          "arith": {"ewma": 1, "add": 2, "cmp": 2}, "arith_scope": 10}
HW_S21 = {"counter_bits": 16, "counter_scope": 10,
          "arith": {"ewma": 1, "cmp": 2}, "arith_scope": 10}
HW_S21EQ = {**HW_S21, "bus_bits": 6,
            "table_entries": 10, "table_bits": 6}
HW_S22 = {"bus_bits": 6, "table_entries": 10, "table_bits": 8,
          "counter_bits": 10, "arith": {"addtree10": 1, "add": 2, "cmp": 32},
          "dir_inj_depth": 32, "inj_depth": 32, "n_vc": 2}
HW_S23R = {"bus_bits": 6, "table_entries": 10, "table_bits": 6,
           "table_scope": 10, "counter_bits": 16 * 10 * 2,
           "counter_scope": 10, "arith": {"add": 2, "cmp": 2},
           "arith_scope": 10}


# name, simulator, read-side overrides, taxonomy, hardware, applicable
CASES: list[tuple[str, str, dict[str, Any], tuple[str, str, str], dict, bool]] = [
    ("S0", "S0", {}, ("-", "none", "none"), HW_NONE, True),
    ("I-tag-R", "S0", {"t_inj": 2, "itag_hold": 2},
     ("local", "arb", "starvation"), HW_ITAG, True),
    ("S1-R", "S1", {"scope": "ha_only"},
     ("HA", "rate", "bus30"), HW_S1, True),
    ("S1T-R", "S1T", {**S1_CFG, "scope": "ha_only"},
     ("HA", "dir-rate", "bus30"), HW_S1, True),
    ("S1-R REQ-admit", "S1", {"scope": "core_only"},
     ("core", "REQ-admit", "bus30"), HW_S1, True),
    ("S1T-R REQ-admit", "S1T", {**S1_CFG, "scope": "core_only"},
     ("core", "REQ-admit", "bus30"), HW_S1, True),
    ("S15-R", "S15", {"scope": "ha_only"},
     ("HA", "rate+reserve", "bus30"), HW_S15, True),
    ("S16-R least-served", "S16", {"overcommit": 16},
     ("HA", "window+order", "local"), HW_S16, True),
    ("S16-R round-robin", "S16", {"overcommit": 16, "policy": "round_robin"},
     ("HA", "window+order", "local"), HW_S16_RR, True),
    ("S16-R bus30", "S16", {"overcommit": 16, "grant_lat": 30},
     ("HA", "window+order", "bus30"), {**HW_S16, "bus_bits": 6}, True),
    ("S17-R TIMELY", "S17", {"pace_init": 2.0},
     ("core", "REQ-rate", "read-RTT"), HW_RATE, True),
    ("S18-R DCQCN", "S18", {"pace_init": 2.0},
     ("core", "REQ-rate", "missing-ECN"), HW_RATE, False),
    ("S19-R Swift", "S19", {},
     ("core", "outstanding", "read-RTT"), HW_WIN, True),
    ("S20-R DCTCP", "S20", {},
     ("core", "outstanding", "missing-ECN"), HW_WIN, False),
    ("S21-R HA pacer", "S21",
     {"pace_vcs": ("dat",), "pace_scope": "ha_only",
      "pace_burst": 1.0, "pace_headroom": 1.5, "pace_gain": 0.05},
     ("HA", "DAT-rate", "local"), HW_S21, True),
    ("S21-R+eq", "S21",
     {"pace_vcs": ("dat",), "pace_scope": "ha_only",
      "pace_burst": 1.0, "pace_headroom": 1.5, "pace_gain": 0.25,
      "pace_equalise": True, "pace_tol": 0.02, "pace_window": 64,
      "pace_bus_lat": 30},
     ("HA", "DAT-rate", "bus30"), HW_S21EQ, True),
    ("S22-R HA-yield", "S22",
     {**S22_CFG, "dfc_vcs": ("dat",), "dfc_scope_nodes": "ha_only",
      "dfc_window": 32, "dfc_bus_lat": 30, "dfc_margin": 3.0},
     ("HA", "DAT-arb", "bus30"), HW_S22, True),
    ("S23-R per-core pacer", "S23",
     {"fair_vcs": ("dat",), "fair_scope_nodes": "ha_only",
      "fair_identity": "dst", "fair_signal": "bus", "fair_bus_lat": 30},
     ("HA/core", "DAT-rate", "bus30"), HW_S23R, True),
]


def _mix(txns: Sequence[Txn]) -> dict[int, dict[int, float]]:
    cnt: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for txn in txns:
        cnt[txn.core][txn.ha] += 1
    return {
        c: {h: n / sum(row.values()) for h, n in sorted(row.items())}
        for c, row in sorted(cnt.items())
    }


def ideal_read(topo: Ring2Topology, txns: Sequence[Txn]) -> dict[str, Any]:
    cores, names, a = coefficients(
        topo, _mix(txns), mult={"req": 1, "dat": R_FLITS},
        reverse={"dat"})
    fair = solve_theta(a, 1.0)
    total = solve_max_total(a)

    def point(lam) -> dict[str, Any]:
        load = a.T @ lam
        peak = float(load.max())
        binding = [names[i] for i, x in enumerate(load)
                   if math.isclose(float(x), peak, rel_tol=0, abs_tol=1e-8)]
        return {
            "read_bw": R_FLITS * float(lam.sum()),
            "txn_rates": {str(c): round(float(x), 8)
                          for c, x in zip(cores, lam)},
            "jain": jain(lam),
            "binding": binding,
        }

    return {"equal_rate": point(fair), "max_total": point(total)}


def _times(raw: dict, key: str) -> dict[int, list[int]]:
    return {int(c): list(ts) for c, ts in (raw.get(key) or {}).items()}


def _binned_series(times: dict[int, list[int]], t_max: int) -> dict[str, Any]:
    out = {}
    for core, ts in sorted(times.items()):
        x, y = bin_rate(ts, t_max, BIN_W)
        out[str(core)] = {"t": x, "rate": [round(v, 4) for v in y]}
    return out


def run_one(name: str, scheme: str, over: dict[str, Any], tax: tuple[str, ...],
            hw: dict, applicable: bool, topo: Ring2Topology,
            txns: Sequence[Txn], ideal: dict[str, Any], k: int) -> dict[str, Any]:
    cfg = {**FABRIC, "core_outstanding": CORE_OUTSTANDING, **over}
    raw = run_scheme(scheme, topo, txns, cfg=cfg, quiet=True)
    recv = _times(raw, "rd_recv_by_core")
    inject = _times(raw, "rd_inject_by_core")
    fair = fairness_stats(recv, raw["makespan"] or 1, k * R_FLITS)
    jb = binned_jain(recv, BIN_W, fair.get("t_fair") or 0)
    inj_fair = fairness_stats(inject, raw["makespan"] or 1, k * R_FLITS)
    inj_jb = binned_jain(inject, BIN_W, inj_fair.get("t_fair") or 0)
    cost, breakdown = hw_cost(hw)
    fc = raw.get("fc") or {}
    row = {
        "name": name, "scheme": scheme, "cfg": over,
        "driver": tax[0], "control": tax[1], "trigger": tax[2],
        "applicable": applicable, "completed": raw["completed"],
        "makespan": raw["makespan"], "throughput": fair["throughput"],
        "fairness": fair, "jain_bin": jb,
        "inject_jain_bin": inj_jb, "hardware_ff_eq": cost,
        "hardware_breakdown": breakdown,
        "n_board_fail": raw.get("n_board_fail", 0),
        "n_deflections": raw.get("n_deflections", 0),
        "n_etag": raw.get("n_etag_raised", 0),
        "hop_use": raw.get("hop_use") or {},
        "fc": fc,
        "recv_binned": _binned_series(recv, raw["makespan"] or 1),
    }
    r_fair = ideal["equal_rate"]["read_bw"]
    row["bw_vs_ideal"] = round(fair["throughput"] / r_fair, 6)
    row["jain_vs_ideal"] = jb.get("jain_vs_ideal")
    row["eta"] = round(
        fair["throughput"] * (jb.get("jain_bin_mean") or 0)
        / (r_fair * jain_ideal_bin(round(r_fair * BIN_W), len(CORE_NODES))),
        6)
    print(f"{name:<24} bw={fair['throughput']:.4f} "
          f"({row['bw_vs_ideal']:.3%})  "
          f"J50={jb.get('jain_bin_mean', 0):.5f}  "
          f"Jinj={inj_jb.get('jain_bin_mean', 0):.5f}  "
          f"cost={cost:,}{'' if applicable else '  N/A signal'}",
          flush=True)
    return row


def _frontier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best = -1.0
    out = []
    for row in sorted((r for r in rows if r["applicable"]),
                      key=lambda r: (r["hardware_ff_eq"], -r["eta"])):
        if row["eta"] > best:
            out.append(row)
            best = row["eta"]
    return out


def plot_pareto(loads: dict[str, Any]) -> None:
    fig, all_axes = plt.subplots(
        1, 3, figsize=(18, 7.2), gridspec_kw={"width_ratios": [1, 1, 0.9]})
    axes, key_ax = all_axes[:2], all_axes[2]
    names = [r["name"] for r in loads["uniform"]["rows"]]
    number = {name: i + 1 for i, name in enumerate(names)}
    for ax, load in zip(axes, ("uniform", "hot")):
        rows = loads[load]["rows"]
        for row in rows:
            x = max(1, row["hardware_ff_eq"])
            if not row["applicable"]:
                color, marker = "#999999", "x"
            elif row["name"] == "S0":
                color, marker = "#111111", "s"
            else:
                color, marker = "#1f6feb", "o"
            ax.scatter(x, row["eta"], color=color, marker=marker, s=55,
                       zorder=3)
            ax.annotate(str(number[row["name"]]), (x, row["eta"]),
                        ha="center", va="center", fontsize=6,
                        color="white" if marker != "x" else "#666666",
                        fontweight="bold", zorder=4)
        front = _frontier(rows)
        if len(front) > 1:
            ax.plot([max(1, r["hardware_ff_eq"]) for r in front],
                    [r["eta"] for r in front], "--", color="#1a7f37",
                    lw=1.3, label="Pareto frontier")
        ax.axhline(1.0, color="#b34700", lw=1.0, ls=":",
                   label="ideal equal-rate CC")
        ax.set_xscale("log")
        ax.set_xlabel("added hardware (FF-equivalents, log scale)")
        ax.set_ylabel("η = (read BW × Jain50) / ideal")
        ax.set_title(f"{load} read traffic")
        ax.grid(alpha=0.25, which="both")
        ax.legend(fontsize=8)
    key_ax.axis("off")
    key_ax.set_title("Point key · added hardware", loc="left", fontsize=10)
    for i, row in enumerate(loads["uniform"]["rows"]):
        color = "#777777" if not row["applicable"] else "#111111"
        suffix = "  N/A: missing read signal" if not row["applicable"] else ""
        key_ax.text(0.0, 0.97 - i * 0.059,
                    f"{number[row['name']]:>2}  {row['name']}"
                    f" · {row['hardware_ff_eq']:,} FF-eq{suffix}",
                    transform=key_ax.transAxes, fontsize=7.2, va="top",
                    color=color)
    fig.suptitle("Read congestion control: benefit versus hardware overhead")
    fig.tight_layout()
    fig.savefig(PARETO_PNG, dpi=170)
    plt.close(fig)


def plot_s0_time(loads: dict[str, Any]) -> None:
    row = next(r for r in loads["uniform"]["rows"] if r["name"] == "S0")
    ideal = loads["uniform"]["ideal"]["equal_rate"]["read_bw"]
    t_fair = row["fairness"]["t_fair"]
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    all_series = []
    for core, series in sorted(row["recv_binned"].items(), key=lambda x: int(x[0])):
        ax0.plot(series["t"], series["rate"], lw=0.75, alpha=0.8,
                 label=f"core {core}")
        all_series.append(series)
    ax0.set_ylabel("received CompData (flit/cycle, 50-cycle bins)")
    ax0.set_title("S0 instantaneous per-core read bandwidth")
    ax0.axvline(t_fair, color="#666666", ls=":", lw=1.0,
                label=f"fairness window ends: cycle {t_fair}")
    ax0.grid(alpha=0.2)
    ax0.legend(ncol=5, fontsize=7)
    if all_series:
        n = min(len(s["rate"]) for s in all_series)
        x = all_series[0]["t"][:n]
        total = [sum(s["rate"][i] for s in all_series) for i in range(n)]
        ax1.plot(x, total, color="#1f6feb", lw=1.0, label="S0 total")
    ax1.axhline(ideal, color="#b34700", ls="--", lw=1.3,
                label=f"equal-rate LP bound = {ideal:.4f}")
    ax1.axvline(t_fair, color="#666666", ls=":", lw=1.0,
                label="first core finishes")
    ax1.set_xlabel("cycle")
    ax1.set_ylabel("total CompData (flit/cycle)")
    ax1.set_title("S0 aggregate read bandwidth over time")
    ax1.grid(alpha=0.2)
    ax1.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(S0_TIME_PNG, dpi=170)
    plt.close(fig)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--replot":
        out = json.loads(OUT.read_text())
        case_hw = {name: hw for name, _scheme, _over, _tax, hw, _ok in CASES}
        for load in ("uniform", "hot"):
            rows = out["loads"][load]["rows"]
            for row in rows:
                cost, breakdown = hw_cost(case_hw[row["name"]])
                row["hardware_ff_eq"] = cost
                row["hardware_breakdown"] = breakdown
            out["loads"][load]["pareto"] = [
                r["name"] for r in _frontier(rows)]
        OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        plot_pareto(out["loads"])
        plot_s0_time(out["loads"])
        print(f"refreshed {OUT}\nwrote {PARETO_PNG}\nwrote {S0_TIME_PNG}")
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--resume":
        out = json.loads(OUT.read_text())
        k = out["method"]["k_per_core"]
        topo = Ring2Topology(n_planes=1, vcs=CHI_VCS, route="latency")
        workloads = {
            "uniform": build_tiled_read(
                k=k, m_resp=R_FLITS, mem=MEM_NODES, core_set=CORE_NODES),
            "hot": build_hot_read(k=k, m_resp=R_FLITS, hot_has=HOT_HAS),
        }
        for load, txns in workloads.items():
            section = out["loads"][load]
            rows = section["rows"]
            have = {r["name"] for r in rows}
            for case in CASES:
                if case[0] not in have:
                    rows.append(run_one(*case, topo, txns, section["ideal"], k))
            s0 = next(r for r in rows if r["name"] == "S0")
            for row in rows:
                row["bw_vs_s0"] = round(
                    row["throughput"] / s0["throughput"], 6)
                row["pass_jain_099"] = (
                    (row["jain_bin"].get("jain_bin_mean") or 0) > 0.99)
                row["within_1pct_s0"] = abs(row["bw_vs_s0"] - 1.0) < 0.01
            section["pareto"] = [r["name"] for r in _frontier(rows)]
        OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
        plot_pareto(out["loads"])
        plot_s0_time(out["loads"])
        print(f"resumed {OUT}\nwrote {PARETO_PNG}\nwrote {S0_TIME_PNG}")
        return
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS, route="latency")
    workloads = {
        "uniform": build_tiled_read(
            k=k, m_resp=R_FLITS, mem=MEM_NODES, core_set=CORE_NODES),
        "hot": build_hot_read(k=k, m_resp=R_FLITS, hot_has=HOT_HAS),
    }
    out: dict[str, Any] = {
        "method": {
            "k_per_core": k, "read_flits_per_txn": R_FLITS,
            "bin_cycles": BIN_W, "core_outstanding": CORE_OUTSTANDING,
            "cores": list(CORE_NODES), "uniform_has": list(MEM_NODES),
            "hot_has": list(HOT_HAS),
            "headline_fairness": "CompData receive times at destination cores",
            "forecast": __doc__.split("Forecast recorded", 1)[1].split(
                "Usage:", 1)[0].strip(),
        },
        "loads": {},
    }
    for load, txns in workloads.items():
        ideal = ideal_read(topo, txns)
        print(f"\n[{load}] K={k}  equal-rate={ideal['equal_rate']['read_bw']:.6f} "
              f"max-total={ideal['max_total']['read_bw']:.6f}",
              flush=True)
        rows = [run_one(name, scheme, over, tax, hw, applicable, topo,
                        txns, ideal, k)
                for name, scheme, over, tax, hw, applicable in CASES]
        s0 = next(r for r in rows if r["name"] == "S0")
        for row in rows:
            row["bw_vs_s0"] = round(row["throughput"] / s0["throughput"], 6)
            row["pass_jain_099"] = (
                (row["jain_bin"].get("jain_bin_mean") or 0) > 0.99)
            row["within_1pct_s0"] = abs(row["bw_vs_s0"] - 1.0) < 0.01
        out["loads"][load] = {
            "ideal": ideal, "rows": rows,
            "pareto": [r["name"] for r in _frontier(rows)],
        }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    plot_pareto(out["loads"])
    plot_s0_time(out["loads"])
    print(f"\nwrote {OUT}\nwrote {PARETO_PNG}\nwrote {S0_TIME_PNG}")


if __name__ == "__main__":
    main()
