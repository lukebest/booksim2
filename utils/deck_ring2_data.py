#!/usr/bin/env python3
"""Re-measure exactly the numbers the architecture-review deck quotes.

The report pipeline (`dse_ring2_write_fair` -> `gen_ring2_write_report`) sweeps
far more than the deck shows and takes correspondingly longer. This driver runs
only the deck's own cases at the current knobs -- `CORE_OUTSTANDING_WR` and
`BIN_W` -- and writes one JSON that the figure script and the PPTX builder both
read, so a slide cannot quote a number no run produced.

Cases:
  write / uniform   S0, S1 (stock AIMD), S1T (direction-split, tuned)
  read   / uniform  S0, S1-R (HA-scoped AIMD), S16-R (least-served grant)

Usage:
    PYTHONHASHSEED=0 python3 deck_ring2_data.py [K_write] [K_read]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, CORE_NODES, CORE_OUTSTANDING_WR,
                                  FABRIC, MEM_NODES, S1_CFG, S16_OVERCOMMIT,
                                  S22_CFG, W_FLITS, bin_rate, binned_jain,
                                  build_pattern, digest, fairness_stats, jain,
                                  jain_ideal_bin, run_scheme)
from rg_ring2_topo import (CHI_VCS, CHI_VCS_WRITE, Ring2Topology,
                           build_tiled_read)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "deck_ring2_data.json"
IDEAL = json.loads((ROOT / "results" / "ideal_ring2_cc.json").read_text())

# S22 is taken at its stock-queue operating point: the deep-queue variant buys a
# little more index for ~86x the hardware, so it is not the one being proposed.
S22_STOCK = {**S22_CFG, "inj_depth": FABRIC["inj_depth"],
             "dir_inj_depth": FABRIC["dir_inj_depth"], "dfc_bus_lat": 30,
             "dfc_window": 64, "dfc_dodge": 8, "dfc_margin": 3.0}

WRITE_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("S0", "S0", {}),
    ("S1", "S1", {}),
    ("S1T", "S1T", dict(S1_CFG)),
    ("S16", "S16", {"overcommit": S16_OVERCOMMIT}),
    ("S22", "S22", S22_STOCK),
]
# The read controller has to act where the read data is injected, which is the
# HA, not the requester: scoping stock S1 to the cores would gate REQs that are
# not the congested resource. `ha_only` is the honest read-side port of S1.
READ_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("S0", "S0", {}),
    ("S1-R", "S1", {"scope": "ha_only"}),
    ("S16-R", "S16", {"overcommit": 16}),
]


def _counts(binned: dict[str, Any], t_fair: int) -> tuple[list[int], dict[str, list[int]]]:
    """Per-bin flit counts per core, restricted to whole contention-window bins."""
    cs = sorted(binned, key=int)
    ts = binned[cs[0]]["t"]
    idx = [i for i, t in enumerate(ts) if t + BIN_W <= t_fair]
    cnt = {c: [int(round(binned[c]["rate"][i] * BIN_W)) for i in idx]
           for c in cs}
    return [ts[i] for i in idx], cnt


def regular_ceiling(cnt: dict[str, list[int]]) -> dict[str, Any]:
    """Binned Jain after replacing every bin count by that core's own mean.

    Jitter is erased and only the long-run rate differences survive, so this is
    the ceiling of any mechanism that regularises *when* a core is served
    without moving *how much* it is served. Also returns the between/within
    variance split, which is reported alongside because it is the statistic
    that looks like an answer and is not one: at a few tens of flits per core
    per bin, counting noise dominates the variance regardless of the rates.
    """
    cs = sorted(cnt, key=int)
    nb = len(cnt[cs[0]])
    means = {c: sum(cnt[c]) / nb for c in cs}
    grand = sum(means.values()) / len(cs)
    v_between = sum((means[c] - grand) ** 2 for c in cs) / len(cs)
    v_within = sum(sum((x - means[c]) ** 2 for x in cnt[c]) / nb
                   for c in cs) / len(cs)
    lo, hi = min(means.values()), max(means.values())
    return {
        "jain_regular": round(jain([means[c] for c in cs]), 5),
        "rate_min": round(lo, 3), "rate_max": round(hi, 3),
        "rate_ratio": round(hi / lo, 4) if lo else None,
        "var_between": round(v_between, 3), "var_within": round(v_within, 3),
        "within_share": round(v_within / (v_between + v_within), 3),
        "flits_per_core_per_bin": round(grand, 2),
    }


def totals(cnt: dict[str, list[int]]) -> dict[str, Any]:
    """Per-bin total write bandwidth inside the contention window."""
    cs = sorted(cnt, key=int)
    nb = len(cnt[cs[0]])
    tot = [sum(cnt[c][b] for c in cs) / BIN_W for b in range(nb)]
    srt = sorted(tot)
    return {"series": [round(x, 4) for x in tot],
            "mean": round(sum(tot) / nb, 4),
            "p05": round(srt[int(0.05 * nb)], 4),
            "p50": round(srt[nb // 2], 4),
            "p95": round(srt[int(0.95 * nb)], 4),
            "min": round(srt[0], 4), "max": round(srt[-1], 4)}


def ceiling_gap(hop_use: dict[str, Any], makespan: int, k: int, n: int,
                r_fair: float) -> dict[str, Any]:
    """Split the gap between R* and the measured throughput, cycle for cycle.

    The bound R* is set by one resource: at the equal-rate optimum the binding
    hop is busy every cycle carrying nothing but first-pass traffic. The
    measured run misses R* for exactly two reasons, both readable off that same
    hop -- cycles it spent carrying a flit that had already been round once
    (`surcharge`), and cycles it spent idle. `floor + surcharge + idle` equals
    the measured makespan identically, which is what makes this a decomposition
    rather than an attribution.
    """
    if not hop_use:
        return {}
    name, top = max(hop_use.items(), key=lambda kv: kv[1].get("util") or 0)
    crossings = int(top.get("n") or 0)
    floor = round(k * W_FLITS * n / r_fair)
    return {
        "hop": name, "vc": name.rsplit(":", 1)[-1],
        "crossings": crossings, "util": top.get("util"),
        "defl": int(top.get("defl") or 0),
        "floor": floor, "surcharge": crossings - floor,
        "idle": makespan - crossings, "makespan": makespan,
        # What the run could have reached given the re-circulations it actually
        # incurred: the same hop, busy every cycle.
        "reachable": round(k * W_FLITS * n / crossings, 4) if crossings else None,
    }


def fail_ratio(board_dir: dict[str, Any]) -> dict[str, float]:
    """Per-core max/min of the two directions' failed-board counts."""
    out = {}
    for c, d in board_dir.items():
        f = [d.get("fail_cw", 0), d.get("fail_ccw", 0)]
        out[c] = round(max(f) / min(f), 3) if min(f) else None
    return out


def run_write(k: int) -> dict[str, Any]:  # noqa: C901
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    out: dict[str, Any] = {}
    for name, scheme, over in WRITE_CASES:
        cfg = {**FABRIC, **over}
        raw = run_scheme(scheme, topo, txns, cfg=cfg, quiet=True)
        d = digest(raw, flits_per_core=k * W_FLITS, bin_w=BIN_W)
        f = d["fairness"]
        ts, cnt = _counts(d["wr_binned"], int(f["t_fair"] or 0))
        hop = d.get("hop_use") or {}
        busiest = sorted(hop.items(),
                         key=lambda kv: -(kv[1].get("util") or 0))[:4]
        out[name] = {
            "throughput": f["throughput"],
            "bw_by_core": f["bw_by_core"],
            "max_min": f["max_min"],
            "t_fair": f["t_fair"],
            "jain_bin": f["jain_bin"],
            "makespan": d["makespan"],
            "lat_p50": d["lat_p50"], "lat_p99": d["lat_p99"],
            "n_etag": d["n_etag_raised"], "n_itag": d["n_itag_raised"],
            "n_board_fail": d["n_board_fail"],
            "max_core_outstanding": d["max_core_outstanding"],
            "recv_by_ha": d["wr_recv_by_ha"],
            "busiest_hops": [[h, v.get("util"), v.get("n"), v.get("defl")]
                             for h, v in busiest],
            "hop_util": {h: v.get("util") for h, v in hop.items()},
            "ceiling_gap": ceiling_gap(hop, d["makespan"] or 1, k,
                                       len(CORE_NODES), IDEAL["r_fair"]),
            "fail_ratio": fail_ratio(d["board_dir"]),
            "bin_t": ts, "total": totals(cnt),
            "regular": regular_ceiling(cnt),
            "per_core_binned": {c: [round(x / BIN_W, 4) for x in v]
                                for c, v in cnt.items()},
        }
        print(f"  write {name:<4} bw={f['throughput']:.4f} "
              f"J{BIN_W}={f['jain_bin']['jain_bin_mean']:.5f} "
              f"max/min={f['max_min']:.4f} mk={d['makespan']}", flush=True)
    return out


def run_read(k: int) -> dict[str, Any]:
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS, route="latency")
    txns = build_tiled_read(k=k, m_resp=W_FLITS, mem=MEM_NODES,
                            core_set=CORE_NODES)
    out: dict[str, Any] = {}
    for name, scheme, over in READ_CASES:
        cfg = {**FABRIC, **over}
        raw = run_scheme(scheme, topo, txns, cfg=cfg, quiet=True)
        recv = {int(c): list(v)
                for c, v in (raw.get("rd_recv_by_core") or {}).items()}
        f = fairness_stats(recv, raw["makespan"] or 1, k * W_FLITS)
        jb = binned_jain(recv, BIN_W, f.get("t_fair") or 0)
        series = {}
        for c, ts in sorted(recv.items()):
            xs, ys = bin_rate(ts, raw["makespan"] or 1, BIN_W)
            series[str(c)] = {"t": xs, "rate": [round(y, 4) for y in ys]}
        out[name] = {
            "throughput": f["throughput"], "bw_by_core": f["bw_by_core"],
            "max_min": f["max_min"], "t_fair": f["t_fair"],
            "jain_bin": jb, "makespan": raw["makespan"],
            "recv_binned": series,
        }
        print(f"  read  {name:<6} bw={f['throughput']:.4f} "
              f"J{BIN_W}={jb['jain_bin_mean']:.5f} "
              f"max/min={f['max_min']:.4f}", flush=True)
    return out


def main() -> None:
    kw = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
    kr = int(sys.argv[2]) if len(sys.argv) > 2 else 5_000
    n = len(CORE_NODES)
    r_fair = IDEAL["r_fair"]
    print(f"K_write={kw}  K_read={kr}  bin={BIN_W}  "
          f"core_outstanding={CORE_OUTSTANDING_WR}", flush=True)

    write = run_write(kw)
    read = run_read(kr)

    # Read-side equal-rate bound: same fabric, same tiled destinations, but the
    # payload travels HA->core, so the binding hop is the mirror of the write
    # one. Symmetry makes it the same number; it is recomputed, not assumed.
    from ideal_ring2_cc import coefficients, solve_max_total, solve_theta
    from collections import defaultdict
    mix: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for t in build_tiled_read(k=kr, m_resp=W_FLITS, mem=MEM_NODES,
                              core_set=CORE_NODES):
        mix[t.core][t.ha] += 1
    p = {c: {h: v / sum(row.values()) for h, v in sorted(row.items())}
         for c, row in sorted(mix.items())}
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS, route="latency")
    _cores, _names, a = coefficients(topo, p, mult={"req": 1, "dat": W_FLITS},
                                     reverse={"dat"})
    r_read_fair = W_FLITS * float(solve_theta(a, 1.0).sum())
    r_read_max = W_FLITS * float(solve_max_total(a).sum())
    print(f"  read ideal equal-rate={r_read_fair:.4f}  "
          f"max-total={r_read_max:.4f}", flush=True)

    data = {
        "meta": {
            "k_write": kw, "k_read": kr, "bin_w": BIN_W,
            "core_outstanding": CORE_OUTSTANDING_WR, "w_flits": W_FLITS,
            "n_cores": n, "cores": list(CORE_NODES), "has": list(MEM_NODES),
            "fc_bus_lat": 30,
        },
        "ideal": {
            "r_fair": r_fair, "r_max": IDEAL["r_max"],
            "lam_star": IDEAL["lambda_fair"],
            "jain_bin_ideal": jain_ideal_bin(int(round(r_fair * BIN_W)), n),
            "flits_per_bin": round(r_fair * BIN_W, 1),
            "read_r_fair": round(r_read_fair, 6),
            "read_r_max": round(r_read_max, 6),
        },
        "write": write,
        "read": read,
    }
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
