#!/usr/bin/env python3
"""Re-measure exactly the numbers the architecture-review deck quotes.

The report pipeline (`dse_ring2_write_fair` -> `gen_ring2_write_report`) sweeps
far more than the deck shows and takes correspondingly longer. This driver runs
only the deck's own cases at the current knobs -- `CORE_OUTSTANDING_WR` and
`BIN_W` -- and writes one JSON that the figure script and the PPTX builder both
read, so a slide cannot quote a number no run produced.

Cases:
  write / uniform   S0, S1, S1U, S1D, S1T, S16, I-tag hold, S19, S20, S22,
                    S26, S27, S28, S28S, S29
  read   / uniform  S0, S1-R (HA-scoped AIMD), S16-R (least-served grant)
  read payload      S0 with CompData = 1 / 2 / 4 flits

Every write row also carries each core's finish cycle and a sampled
cumulative-progress curve, which is what the per-core completion figures read.

Usage:
    PYTHONHASHSEED=0 python3 deck_ring2_data.py [K_write] [K_read] [jobs]
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, CORE_NODES, CORE_OUTSTANDING_WR,
                                  FABRIC, MEM_NODES, S1_CFG, S16_OVERCOMMIT,
                                  S22_CFG, S26_CFG, S27_CFG, S28_CFG,
                                  S28S_CFG, S29_CFG, W_FLITS, bin_rate,
                                  binned_jain, build_pattern, digest,
                                  fairness_stats, jain, jain_ideal_bin,
                                  run_scheme)
from rg_ring2_topo import (CHI_VCS, CHI_VCS_WRITE, Ring2Topology,
                           build_tiled_read)

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("DECK_OUT") or ROOT / "results" / "deck_ring2_data.json")
IDEAL = json.loads((ROOT / "results" / "ideal_ring2_cc.json").read_text())

# S22 is taken at its stock-queue operating point: the deep-queue variant buys a
# little more index for ~86x the hardware, so it is not the one being proposed.
S22_STOCK = {**S22_CFG, "inj_depth": FABRIC["inj_depth"],
             "dir_inj_depth": FABRIC["dir_inj_depth"], "dfc_bus_lat": 30,
             "dfc_window": 64, "dfc_dodge": 8, "dfc_margin": 3.0}

WRITE_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("S0", "S0", {}),
    ("S1", "S1", {}),
    # S1's congestion level split by which failure feeds it. Stock S1 takes
    # max(board failures, eject deflections) and broadcasts both fields; the
    # two variants keep exactly one of them and zero the other bus field.
    ("S1U", "S1", {"signal": "up"}),
    ("S1D", "S1", {"signal": "down"}),
    ("S1T", "S1T", dict(S1_CFG)),
    ("S16", "S16", {"overcommit": S16_OVERCOMMIT}),
    ("ITAG", "S0", {"t_inj": 2, "itag_hold": 2}),
    ("S19", "S19", {}),
    ("S20", "S20", {}),
    ("S22", "S22", S22_STOCK),
    # The four families the taxonomy had no representative for. Same fabric,
    # same K, same bin width as every row above, so the deck can put them in
    # one table with S0 / S1 without a caveat.
    ("S26", "S26", S26_CFG),
    ("S27", "S27", S27_CFG),
    ("S28", "S28", S28_CFG),
    ("S28S", "S28S", S28S_CFG),
    ("S29", "S29", S29_CFG),
]
# The read controller has to act where the read data is injected, which is the
# HA, not the requester: scoping stock S1 to the cores would gate REQs that are
# not the congested resource. `ha_only` is the honest read-side port of S1.
READ_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("S0", "S0", {}),
    ("S1-R", "S1", {"scope": "ha_only"}),
    ("S16-R", "S16", {"overcommit": 16}),
]
# CompData sizes for the read-side S0 payload study. 2 is the deck's stock
# 128 B burst; 1 and 4 bracket it. Same K transactions per core each time, so
# the REQ stream is identical and only the DAT payload per REQ changes.
READ_PAYLOADS = (1, 2, 4)
CUM_STEP = 200            # cycles between samples of the cumulative curves


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


def cumulative(times: dict[int, list[int]], makespan: int,
               step: int = CUM_STEP) -> dict[str, Any]:
    """Each core's delivered-flit count sampled every `step` cycles.

    This is the per-core completion curve: it rises until that core's last
    flit and is flat afterwards, so the x at which it goes flat is the core's
    finish cycle and the spread of those x's is the completion-time skew.
    """
    n = makespan // step + 2
    xs = [i * step for i in range(n)]
    out = {}
    for c, ts in sorted(times.items()):
        cnt = [0] * n
        for t in ts:
            cnt[min(n - 1, int(t) // step + 1)] += 1
        run = 0
        for i in range(n):
            run += cnt[i]
            cnt[i] = run
        out[str(c)] = cnt
    return {"t": xs, "by_core": out}


def _write_case(args: tuple[str, str, dict[str, Any], int]) -> tuple[str, dict[str, Any]]:
    name, scheme, over, k = args
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    txns = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    cfg = {**FABRIC, **over}
    raw = run_scheme(scheme, topo, txns, cfg=cfg, quiet=True)
    d = digest(raw, flits_per_core=k * W_FLITS, bin_w=BIN_W)
    f = d["fairness"]
    ts, cnt = _counts(d["wr_binned"], int(f["t_fair"] or 0))
    hop = d.get("hop_use") or {}
    busiest = sorted(hop.items(),
                     key=lambda kv: -(kv[1].get("util") or 0))[:4]
    inj = {int(c): v for c, v in (raw.get("wr_inject_by_core") or {}).items()}
    row = {
        "throughput": f["throughput"],
        "bw_by_core": f["bw_by_core"],
        "max_min": f["max_min"],
        "t_fair": f["t_fair"],
        "finish_by_core": f["finish_by_core"],
        "bw_run_by_core": f["bw_run_by_core"],
        "max_min_run": f["max_min_run"],
        "jain_bin": f["jain_bin"],
        "makespan": d["makespan"],
        "lat_p50": d["lat_p50"], "lat_p99": d["lat_p99"],
        "n_etag": d["n_etag_raised"], "n_itag": d["n_itag_raised"],
        "n_board_fail": d["n_board_fail"],
        "n_deflections": d["n_deflections"],
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
        "cum": cumulative(inj, d["makespan"] or 1),
    }
    fc = d.get("fc") or {}
    if fc:
        row["fc"] = {k2: fc.get(k2) for k2 in
                     ("signal", "signal_sum", "n_fc_deny", "n_aimd_increase",
                      "n_aimd_decrease", "mean_budget", "mean_level",
                      "mean_recv_level")}
    print(f"  write {name:<4} bw={f['throughput']:.4f} "
          f"J{BIN_W}={f['jain_bin']['jain_bin_mean']:.5f} "
          f"max/min={f['max_min']:.4f} mk={d['makespan']}", flush=True)
    return name, row


def _read_row(raw: dict[str, Any], k: int, m_resp: int) -> dict[str, Any]:
    recv = {int(c): list(v)
            for c, v in (raw.get("rd_recv_by_core") or {}).items()}
    f = fairness_stats(recv, raw["makespan"] or 1, k * m_resp)
    jb = binned_jain(recv, BIN_W, f.get("t_fair") or 0)
    series = {}
    for c, ts in sorted(recv.items()):
        xs, ys = bin_rate(ts, raw["makespan"] or 1, BIN_W)
        series[str(c)] = {"t": xs, "rate": [round(y, 4) for y in ys]}
    hop = raw.get("hop_use") or {}
    busiest = sorted(hop.items(),
                     key=lambda kv: -(kv[1].get("util") or 0))[:4]
    return {
        "m_resp": m_resp,
        "throughput": f["throughput"], "bw_by_core": f["bw_by_core"],
        "max_min": f["max_min"], "t_fair": f["t_fair"],
        "finish_by_core": f["finish_by_core"],
        "max_min_run": f["max_min_run"],
        "jain_bin": jb, "makespan": raw["makespan"],
        "lat_p50": raw.get("lat_p50"), "lat_p99": raw.get("lat_p99"),
        "n_board_fail": raw.get("n_board_fail", 0),
        "n_deflections": raw.get("n_deflections", 0),
        "busiest_hops": [[h, v.get("util"), v.get("n"), v.get("defl")]
                         for h, v in busiest],
        "recv_binned": series,
        "cum": cumulative(recv, raw["makespan"] or 1),
    }


def _read_case(args: tuple[str, str, dict[str, Any], int, int]
               ) -> tuple[str, dict[str, Any]]:
    name, scheme, over, k, m_resp = args
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS, route="latency")
    txns = build_tiled_read(k=k, m_resp=m_resp, mem=MEM_NODES,
                            core_set=CORE_NODES)
    cfg = {**FABRIC, **over}
    raw = run_scheme(scheme, topo, txns, cfg=cfg, quiet=True)
    row = _read_row(raw, k, m_resp)
    print(f"  read  {name:<6} m={m_resp} bw={row['throughput']:.4f} "
          f"J{BIN_W}={row['jain_bin']['jain_bin_mean']:.5f} "
          f"max/min={row['max_min']:.4f}", flush=True)
    return name, row


def read_ideal(k: int, m_resp: int) -> tuple[float, float]:
    """Read-side equal-rate and max-total bounds for a given CompData size.

    Same fabric, same tiled destinations, but the payload travels HA->core, so
    the binding hop is the mirror of the write one. Recomputed per payload
    size rather than assumed: with a 1-flit payload the REQ stream is as heavy
    as the DAT stream and the binding VC can change.
    """
    from collections import defaultdict
    from ideal_ring2_cc import coefficients, solve_max_total, solve_theta
    mix: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for t in build_tiled_read(k=k, m_resp=m_resp, mem=MEM_NODES,
                              core_set=CORE_NODES):
        mix[t.core][t.ha] += 1
    p = {c: {h: v / sum(row.values()) for h, v in sorted(row.items())}
         for c, row in sorted(mix.items())}
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS, route="latency")
    _cores, _names, a = coefficients(topo, p, mult={"req": 1, "dat": m_resp},
                                     reverse={"dat"})
    r_fair = m_resp * float(solve_theta(a, 1.0).sum())
    r_max = m_resp * float(solve_max_total(a).sum())
    return r_fair, r_max


def main() -> None:
    kw = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
    kr = int(sys.argv[2]) if len(sys.argv) > 2 else 5_000
    jobs = int(sys.argv[3]) if len(sys.argv) > 3 else max(1, (os.cpu_count() or 2) - 1)
    n = len(CORE_NODES)
    r_fair = IDEAL["r_fair"]
    print(f"K_write={kw}  K_read={kr}  bin={BIN_W}  "
          f"core_outstanding={CORE_OUTSTANDING_WR}  jobs={jobs}", flush=True)

    # Every case is an independent closed-batch run with its own seed state,
    # so they can go in parallel; the output is keyed, not ordered.
    write_jobs = [(nm, sc, ov, kw) for nm, sc, ov in WRITE_CASES]
    read_jobs = [(nm, sc, ov, kr, W_FLITS) for nm, sc, ov in READ_CASES]
    payload_jobs = [(f"S0-m{m}", "S0", {}, kr, m) for m in READ_PAYLOADS]
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        fw = [ex.submit(_write_case, j) for j in write_jobs]
        fr = [ex.submit(_read_case, j) for j in read_jobs]
        fp = [ex.submit(_read_case, j) for j in payload_jobs]
        write = dict(f.result() for f in fw)
        read = dict(f.result() for f in fr)
        payload = dict(f.result() for f in fp)
    write = {nm: write[nm] for nm, _s, _o in WRITE_CASES}
    read = {nm: read[nm] for nm, _s, _o in READ_CASES}

    r_read_fair, r_read_max = read_ideal(kr, W_FLITS)
    print(f"  read ideal equal-rate={r_read_fair:.4f}  "
          f"max-total={r_read_max:.4f}", flush=True)
    for m in READ_PAYLOADS:
        rf, rm = read_ideal(kr, m)
        payload[f"S0-m{m}"]["ideal"] = {
            "r_fair": round(rf, 6), "r_max": round(rm, 6),
            "jain_bin_ideal": jain_ideal_bin(int(round(rf * BIN_W)), n)}
        print(f"  read payload m={m}: ideal equal-rate={rf:.4f} "
              f"max-total={rm:.4f}", flush=True)

    data = {
        "meta": {
            "k_write": kw, "k_read": kr, "bin_w": BIN_W,
            "core_outstanding": CORE_OUTSTANDING_WR, "w_flits": W_FLITS,
            "n_cores": n, "cores": list(CORE_NODES), "has": list(MEM_NODES),
            "fc_bus_lat": 30, "cum_step": CUM_STEP,
            "read_payloads": list(READ_PAYLOADS),
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
        "read_payload": payload,
    }
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
