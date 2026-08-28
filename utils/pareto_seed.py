#!/usr/bin/env python3
"""Rebuild the Pareto registry from the measurement files, one convention.

Single source of truth for the Pareto plot. Nothing is hand-typed: every row is
read back out of the JSON a probe wrote, so the plot cannot drift from what was
actually measured. All rows are taken at the same screening K so the comparison
is apples to apples; official-K confirmation of the frontier is a separate step.

Conventions, applied uniformly:

  bw_vs_ideal    thr / R*_fair, R*_fair = 40/7 from `ideal_ring2_cc`
  jain_vs_ideal  Jbin / (ideal deterministic Jain at the same delivered count)
  eta            (thr * Jbin) / (R*_fair * ideal Jain at R*)   <- the y-axis
  delta_pct      % against the ideal's bandwidth (not against S0)
  delta_s0_pct   % against S0, because the brief's bandwidth line is stated
                 that way
  pass_jain      Jbin > 0.99
  pass_bw        |delta_s0_pct| < 1
  bus_rule_ok    False only if the scheme needs the bus faster than 30 cycles
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import BIN_W, jain_ideal_bin
from pareto_ring2_cc import REG, load, plot, upsert

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"

# --- hardware specs, in the FF-equivalent model of `pareto_ring2_cc` ---------
HW: dict[str, dict] = {
    "none": {},
    "itag": {"counter_bits": 36, "arith": {"cmp": 6}},
    "s1": {"bus_bits": 6, "table_entries": 20, "table_bits": 6,
           "counter_bits": 15, "arith": {"mult": 2, "add": 2, "cmp": 2}},
    "s15": {"bus_bits": 6, "table_entries": 20, "table_bits": 6,
            "counter_bits": 15, "arith": {"mult": 2, "add": 3, "cmp": 3}},
    "s16": {"counter_bits": 10, "counter_scope": 10,
            "arith": {"cmp": 2, "add": 1}, "arith_scope": 10},
    "rate_inband": {"counter_bits": 32, "counter_scope": 10,
                    "arith": {"ewma": 1, "mult": 1, "cmp": 3},
                    "arith_scope": 10},
    "win_inband": {"counter_bits": 24, "counter_scope": 10,
                   "arith": {"ewma": 1, "add": 2, "cmp": 2}, "arith_scope": 10},
    "s21": {"counter_bits": 16, "counter_scope": 10,
            "arith": {"ewma": 1, "cmp": 2}, "arith_scope": 10},
    "s21eq": {"bus_bits": 6, "table_entries": 10, "table_bits": 6,
              "counter_bits": 16, "counter_scope": 10,
              "arith": {"ewma": 1, "cmp": 3, "add": 1}, "arith_scope": 10},
    "s22": {"bus_bits": 6, "table_entries": 10, "table_bits": 8,
            "counter_bits": 10, "arith": {"addtree10": 1, "add": 2, "cmp": 32},
            "dir_inj_depth": 32, "inj_depth": 32},
    # Same controller at stock queue depth. `probe_ring2_cheap` shows the 32-deep
    # inject queues buy +0.014 eta once the bus costs 30 cycles, so the entire
    # queue term -- 1.19M of S22's 1.20M FF-equivalents -- comes off, and the
    # look-ahead shrinks from 32 comparators to 8.
    "s22stock": {"bus_bits": 6, "table_entries": 10, "table_bits": 8,
                 "counter_bits": 10,
                 "arith": {"addtree10": 1, "add": 2, "cmp": 8}},
    "s23": {"bus_bits": 6, "table_entries": 10, "table_bits": 8,
            "counter_bits": 24, "counter_scope": 10,
            "arith": {"addtree10": 1, "add": 2, "cmp": 3}, "arith_scope": 10},
    # The fair share is a topology constant, so there is nothing to signal:
    # one rate register and one credit counter per (core, VC, direction).
    "s24": {"counter_bits": 32, "counter_scope": 10,
            "arith": {"cmp": 2, "add": 2}, "arith_scope": 10},
    # S25 keeps S22's yield actuator but takes its deficit from the same constant
    # target S24 uses, so the flow-control bus, its 10-entry mirror table and its
    # adder tree all disappear. What remains is S24's counter plus a 1-bit
    # request line between ring neighbours -- a local wire, not the dedicated bus,
    # so it is not subject to the 30-cycle rule. `dfc_dodge` is measured inert
    # here, so there is no look-ahead comparator bank and no queue growth.
    "s25": {"bus_bits": 1, "counter_bits": 24, "counter_scope": 10,
            "arith": {"add": 2, "cmp": 3}, "arith_scope": 10},
}

# Which hardware spec and taxonomy each family-sweep row belongs to.
FAMILY_HW = {
    "S0 baseline": ("none", "-", "none", "none"),
    "I-tag t_inj2 hold2": ("itag", "-", "arb", "local"),
    "S1 AIMD": ("s1", "sender", "rate", "bus"),
    "S1T AIMD dir-split": ("s1", "sender", "rate", "bus"),
    "S15 fair-share+resv": ("s15", "sender", "rate", "bus"),
    "S16 grant withhold": ("s16", "recv", "window", "local"),
    "S17 Timely": ("rate_inband", "sender", "rate", "delay"),
    "S18 DCQCN": ("rate_inband", "sender", "rate", "ecn"),
    "S19 Swift": ("win_inband", "sender", "window", "delay"),
    "S20 DCTCP": ("win_inband", "sender", "window", "ecn"),
    "S21 pacer hr1.5": ("s21", "sender", "rate", "local"),
    "S21+eq bus30": ("s21eq", "sender", "rate", "bus"),
    "S22 deficit-yield bus30 w32": ("s22", "recv", "arb", "bus"),
    "S22 deficit-yield bus1 w2": ("s22", "recv", "arb", "bus"),
}


def main() -> None:
    ideal = json.loads((RES / "ideal_ring2_cc.json").read_text())
    n, r_ideal = ideal["n_cores"], ideal["r_fair"]
    j_ideal_at_r = jain_ideal_bin(int(round(r_ideal * BIN_W)), n)
    u_ideal = r_ideal * j_ideal_at_r

    fam = json.loads((RES / "sweep_ring2_cc_family.json").read_text())
    s0 = next(r["thr"] for r in fam["rows"] if r["name"] == "S0 baseline")
    k = fam["k"]

    rows: list[tuple] = []
    for r in fam["rows"]:
        hwk, drv, ctl, trg = FAMILY_HW[r["name"]]
        rows.append((r["name"], r["thr"], r["jain_bin"], HW[hwk],
                     r["bus_rule_ok"], drv, ctl, trg, r["k"]))

    # S23: best adaptive fair-share pacer point, by eta.
    s23 = json.loads((RES / "probe_ring2_s23.json").read_text())
    b23 = max(s23["rows"], key=lambda x: x["eta"])
    rows.append((f"S23 fair-share pacer ({b23['case']})", b23["thr"],
                 b23["jain_bin"], HW["s23"], True, "sender", "rate", "bus",
                 s23["k"]))

    # S22 at stock queue depth, and the S24 burst family: both from the probe that
    # asked which expensive parts are still load-bearing at a 30-cycle bus.
    cheap = json.loads((RES / "probe_ring2_cheap.json").read_text())
    c22 = max((r for r in cheap["rows"] if r["group"] == "s22"
               and r["over"].get("dir_inj_depth") == 8), key=lambda x: x["eta"])
    rows.append(("S22 deficit-yield STOCK depth bus30 w64", c22["thr"],
                 c22["jain_bin"], HW["s22stock"], True, "recv", "arb", "bus",
                 cheap["k"]))

    # S24 is a gate with no signalling at all, so its burst depth traces a clean
    # one-parameter frontier: burst 1 discards every unused credit (most regular,
    # least bandwidth), a deep bank discards none (S0-like). Carry all three
    # characteristic points -- the two endpoints bound what an injection-gating
    # actuator can do, and the middle is where its eta peaks.
    s24 = {r["over"]["fair_burst"]: r for r in cheap["rows"]
           if r["group"] == "s24"}
    for burst, label in ((1.0, "Jain-max"), (8.0, "eta-max"),
                         (16.0, "bw-parity")):
        b = s24[burst]
        rows.append((f"S24 rate-pinned {label} (burst {burst:g})", b["thr"],
                     b["jain_bin"], HW["s24"], True, "sender", "rate", "none",
                     cheap["k"]))

    # S25: the cross of the two above -- S22's yield actuator on S24's constant
    # local target. Both sweeps are pooled; the eta-max and the bandwidth-max
    # points bracket what the yield actuator does once the bus is gone.
    s25 = (json.loads((RES / "probe_ring2_s25.json").read_text())["rows"]
           + json.loads((RES / "probe_ring2_s25b.json").read_text())["rows"])
    for pick, label in ((lambda x: x["eta"], "eta-max"),
                        (lambda x: x["thr"], "bw-max")):
        b = max(s25, key=pick)
        rows.append((f"S25 local-target yield {label}", b["thr"],
                     b["jain_bin"], HW["s25"], True, "recv", "arb", "local",
                     2000))

    REG.unlink(missing_ok=True)
    for name, thr, jain, hw, bus_ok, drv, ctl, trg, kk in rows:
        d_ideal = 100 * (thr / r_ideal - 1)
        d_s0 = 100 * (thr - s0) / s0
        upsert(name, k=kk, thr=thr, jain_bin=jain, hw=hw, bus_rule_ok=bus_ok,
               driver=drv, control=ctl, trigger=trg,
               bw_vs_ideal=round(thr / r_ideal, 5),
               # Jain against the ideal evaluated at *this scheme's* delivered
               # count, so throttling cannot flatter the fairness axis; the
               # bandwidth cost of throttling shows up in `bw_vs_ideal` instead.
               jain_vs_ideal=round(
                   jain / jain_ideal_bin(int(round(thr * BIN_W)), n), 5),
               eta=round(thr * jain / u_ideal, 5),
               delta_pct=round(d_ideal, 2), delta_s0_pct=round(d_s0, 2),
               pass_jain=jain > 0.99, pass_bw=abs(d_s0) < 1.0)

    reg = load()
    reg["ideal"] = {"bw": r_ideal, "jain_bin": j_ideal_at_r, "u": u_ideal,
                    "s0_thr": s0, "k": k}
    REG.write_text(json.dumps(reg, indent=2, ensure_ascii=False))

    print(f"K={k}   ideal bw={r_ideal:.4f}  Jbin={j_ideal_at_r:.5f}  "
          f"U={u_ideal:.4f}   S0 thr={s0}\n")
    print(f"{'scheme':<40}{'eta':>7}{'bw/id':>7}{'Jain':>9}"
          f"{'vsS0%':>7}{'hw(FF-eq)':>11}  lines bus")
    for r in sorted(reg["schemes"], key=lambda x: -x["eta"]):
        print(f"{r['name']:<40}{r['eta']:>7.4f}{r['bw_vs_ideal']:>7.4f}"
              f"{r['jain_bin']:>9.5f}{r['delta_s0_pct']:>7.2f}"
              f"{r['hw_cost']:>11,}"
              f"   {'Y' if r['pass_jain'] and r['pass_bw'] else '-'}"
              f"    {'Y' if r['bus_rule_ok'] else 'N'}")
    plot(reg)


if __name__ == "__main__":
    main()
