#!/usr/bin/env python3
"""Sweep the congestion-control families against the ideal-CC reference.

Everything is measured relative to the ideal controller derived in
`ideal_ring2_cc.py`, on two axes that are kept separate on purpose:

  * bandwidth   thr / R*_fair, where R*_fair = 40/7 is the most write bandwidth
                any controller can carry while giving every core an equal rate.
  * fairness    Jbin / jain_bin_ideal, where the ideal is evaluated at the
                scheme's *own* delivered flit count. A scheme therefore cannot
                flatter itself on the fairness axis by throttling -- throttling
                shows up on the bandwidth axis instead.

and combined into one scalar for the Pareto plot:

    eta = (thr * Jbin) / (R*_fair * jain_bin_ideal_at_R*)

`eta = 1` is the ideal controller. This is fairness-weighted delivered
bandwidth, normalised -- monotone in both objectives, no arbitrary weights.

The taxonomy being swept, because the brief asks specifically for it:

  driver   sender  = the requester decides its own rate/window
           recv    = the completer (HA) decides, by withholding
  control  rate    = tokens per cycle (leaky bucket / pacer)
           window  = outstanding cap
           arb     = no gate at all; only the arbitration order moves
  trigger  bus     = the dedicated 6-bit broadcast, 30 cycles, unavoidably
           delay   = in-band RTT, measured off Comp arrival, no bus
           ecn     = in-band mark on the RSP channel, no bus
           local   = the node's own observation only, no signalling

The 30-cycle bus rule only bites the `bus` trigger. `delay`, `ecn` and `local`
signal over channels that already exist, so they pay no bus latency at all --
which is the main reason this sweep is worth running rather than assuming S22 is
the only candidate.

Usage:
    PYTHONHASHSEED=0 python3 sweep_ring2_cc_family.py [K]
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, S1_CFG, S22_CFG, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  jain_ideal_bin, run_scheme)
from pareto_ring2_cc import upsert
from rg_ring2_topo import CHI_VCS_WRITE, Ring2Topology

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "sweep_ring2_cc_family.json")
IDEAL = json.loads((Path(__file__).resolve().parents[1] / "results"
                    / "ideal_ring2_cc.json").read_text())

# Start every paced scheme unthrottled so the controller can only subtract:
# a core owns one board port per plane per direction, so 2 REQ/cycle here.
CEIL = 2.0

# --- hardware specs, in the FF-equivalent model of `pareto_ring2_cc` ---------
HW_NONE: dict = {}
HW_ITAG = {"counter_bits": 6 * 6, "arith": {"cmp": 6}}
HW_S1 = {"bus_bits": 6, "table_entries": 20, "table_bits": 6,
         "counter_bits": 15, "arith": {"mult": 2, "add": 2, "cmp": 2}}
HW_S15 = {**HW_S1, "arith": {"mult": 2, "add": 3, "cmp": 3}}
# Receiver-driven grant withholding: no bus, no table. The HA already tracks
# outstanding entries, so the added state is a budget counter and a comparator.
HW_S16 = {"counter_bits": 10, "counter_scope": 10, "arith": {"cmp": 2, "add": 1},
          "arith_scope": 10}
# In-band rate control: per-core rate register, RTT min/last registers, an
# EWMA. No bus, no global table.
HW_RATE_INBAND = {"counter_bits": 8 * 4, "counter_scope": 10,
                  "arith": {"ewma": 1, "mult": 1, "cmp": 3}, "arith_scope": 10}
# In-band window control: window register + counter, no multiplier for DCTCP's
# alpha EWMA... it does need one, so keep the ewma term.
HW_WIN_INBAND = {"counter_bits": 8 * 3, "counter_scope": 10,
                 "arith": {"ewma": 1, "add": 2, "cmp": 2}, "arith_scope": 10}
HW_S21 = {"counter_bits": 16, "counter_scope": 10,
          "arith": {"ewma": 1, "cmp": 2}, "arith_scope": 10}
HW_S21EQ = {**HW_S21, "bus_bits": 6, "table_entries": 10, "table_bits": 6,
            "arith": {"ewma": 1, "cmp": 3, "add": 1}}
HW_S22 = {"bus_bits": 6, "table_entries": 10, "table_bits": 8,
          "counter_bits": 10, "arith": {"addtree10": 1, "add": 2, "cmp": 32},
          "dir_inj_depth": 32, "inj_depth": 32}

# name, scheme, cfg-overrides, (driver, control, trigger), hw, bus_rule_ok
CASES: list[tuple] = [
    ("S0 baseline", "S0", {}, ("-", "none", "none"), HW_NONE, True),
    ("I-tag t_inj2 hold2", "S0", {"t_inj": 2, "itag_hold": 2},
     ("-", "arb", "local"), HW_ITAG, True),
    ("S1 AIMD", "S1", {}, ("sender", "rate", "bus"), HW_S1, True),
    ("S1T AIMD dir-split", "S1T", {}, ("sender", "rate", "bus"), HW_S1, True),
    ("S15 fair-share+resv", "S15", {}, ("sender", "rate", "bus"), HW_S15, True),
    ("S16 grant withhold", "S16", {}, ("recv", "window", "local"), HW_S16,
     True),
    ("S17 Timely", "S17", {"pace_init": CEIL},
     ("sender", "rate", "delay"), HW_RATE_INBAND, True),
    ("S18 DCQCN", "S18", {"pace_init": CEIL},
     ("sender", "rate", "ecn"), HW_RATE_INBAND, True),
    ("S19 Swift", "S19", {}, ("sender", "window", "delay"), HW_WIN_INBAND,
     True),
    ("S20 DCTCP", "S20", {}, ("sender", "window", "ecn"), HW_WIN_INBAND, True),
    ("S21 pacer hr1.5", "S21",
     {"pace_burst": 1.0, "pace_headroom": 1.5, "pace_gain": 0.05,
      "pace_equalise": False}, ("sender", "rate", "local"), HW_S21, True),
    ("S21+eq bus30", "S21",
     {"pace_burst": 1.0, "pace_headroom": 1.5, "pace_gain": 0.25,
      "pace_equalise": True, "pace_tol": 0.02, "pace_window": 64,
      "pace_bus_lat": 30}, ("sender", "rate", "bus"), HW_S21EQ, True),
    ("S22 deficit-yield bus30 w32", "S22",
     {**S22_CFG, "dfc_window": 32, "dfc_bus_lat": 30, "dfc_margin": 3.0},
     ("recv", "arb", "bus"), HW_S22, True),
]
# Dropped: the same controller with `dfc_bus_lat=1`. It scored well, but a
# one-cycle broadcast is not a thing this design can build, and carrying an
# unbuildable point through the Pareto plot only invites it to be read as an
# option. The 30-cycle rule is a physical constraint, not a knob.


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    fpc = k * W_FLITS
    n = IDEAL["n_cores"]

    r_ideal = IDEAL["r_fair"]
    j_ideal_at_r = jain_ideal_bin(int(round(r_ideal * BIN_W)), n)
    u_ideal = r_ideal * j_ideal_at_r
    print(f"K={k}   ideal: bw={r_ideal:.4f} flit/cycle  "
          f"Jbin_ideal={j_ideal_at_r:.5f}  U_ideal={u_ideal:.4f}\n")
    print(f"{'scheme':<30}{'drv':>7}{'ctl':>7}{'trig':>6}"
          f"{'bw/ideal':>10}{'J/ideal':>9}{'eta':>8}  lines")

    rows = []
    for name, scheme, over, tax, hw, bus_ok in CASES:
        cfg = dict(FABRIC)
        if scheme == "S1T":
            cfg.update(S1_CFG)
        cfg.update(over)
        try:
            r = run_scheme(scheme, topo, tx, cfg=cfg, quiet=True)
        except Exception:
            print(f"{name:<30}  FAILED")
            traceback.print_exc(limit=1)
            continue
        inj = {int(c): v for c, v in (r.get("wr_inject_by_core") or {}).items()}
        f = fairness_stats(inj, r["makespan"] or 1, fpc)
        jb = binned_jain(inj, BIN_W, f.get("t_fair") or 0)
        thr, jm = f["throughput"], jb["jain_bin_mean"]
        bw_rel = thr / r_ideal
        j_rel = jm / (jb.get("jain_bin_ideal") or 1.0)
        eta = (thr * jm) / u_ideal
        # Acceptance lines, both now stated against the ideal.
        ok_j = jm > 0.99
        ok_b = bw_rel > 0.99
        row = {"name": name, "scheme": scheme, "k": k, "driver": tax[0],
               "control": tax[1], "trigger": tax[2], "thr": thr,
               "jain_bin": jm, "jain_bin_ideal": jb.get("jain_bin_ideal"),
               "bw_vs_ideal": round(bw_rel, 5), "jain_vs_ideal": round(j_rel, 5),
               "eta": round(eta, 5), "maxmin": f["max_min"],
               "bus_rule_ok": bus_ok, "pass_jain": ok_j, "pass_bw": ok_b}
        rows.append(row)
        upsert(name, k=k, thr=thr, jain_bin=jm, hw=hw, bus_rule_ok=bus_ok,
               pass_jain=ok_j, pass_bw=ok_b, eta=round(eta, 5),
               bw_vs_ideal=round(bw_rel, 5), jain_vs_ideal=round(j_rel, 5),
               driver=tax[0], control=tax[1], trigger=tax[2],
               delta_pct=round(100 * (bw_rel - 1), 2), note=name)
        flag = "  <== PASS" if (ok_j and ok_b and bus_ok) else ""
        print(f"{name:<30}{tax[0]:>7}{tax[1]:>7}{tax[2]:>6}"
              f"{bw_rel:>10.4f}{j_rel:>9.4f}{eta:>8.4f}"
              f"  {'J' if ok_j else '-'}{'B' if ok_b else '-'}"
              f"{'' if bus_ok else ' (bus-rule violation)'}{flag}", flush=True)

    OUT.write_text(json.dumps({"k": k, "ideal_bw": r_ideal,
                               "ideal_jain_bin": j_ideal_at_r,
                               "u_ideal": u_ideal, "rows": rows}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
