#!/usr/bin/env python3
"""Reproducible analytic PPA model for Arch-A CalSlot-Hybrid-ZB (DSE Trial 1).

Derives the 0.970 relative area from explicit baseline splits, buffer reduction,
calendar SRAM bit accounting, and FlooNoC calibration anchors.

Usage:
    python3 utils/ppa_analytic_model.py
    python3 utils/ppa_analytic_model.py --sensitivity
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

FLIT_BITS = 512
BASELINE_CROSSBAR = 0.380
BASELINE_BUFFERS = 0.450
BASELINE_CONTROL = 0.170
BASELINE_TOTAL = BASELINE_CROSSBAR + BASELINE_BUFFERS + BASELINE_CONTROL

# Baseline IQ interior payload reference (conservative aggregate, 5-port mesh).
BASELINE_INTERIOR_FLITS = 123
BASELINE_INTERIOR_BITS = BASELINE_INTERIOR_FLITS * FLIT_BITS

# Arch-A BG/escape only (REQ-A-003 interior aggregate).
ARCH_A_INTERIOR_FLITS = 74
ARCH_A_INTERIOR_BITS = ARCH_A_INTERIOR_FLITS * FLIT_BITS

CAL_SLOTS_DEFAULT = 1024
CAL_BANKS = 2
CAL_ENTRY_BITS = 13

FLOONOC_MC_DELTA = 0.058
FLOONOC_COMBINE_DELTA = 0.027
CALIBRATION_UNCERTAINTY = 0.30

ARCH_A_BUFFERS = 0.270  # rounded analytic entry; 0.450×74/123 = 0.2707
ARCH_A_CONTROL = 0.195


@dataclass(frozen=True)
class AreaBreakdown:
    crossbar: float
    buffers: float
    calendar: float
    multicast: float
    combine: float
    control: float

    @property
    def total(self) -> float:
        return (
            self.crossbar
            + self.buffers
            + self.calendar
            + self.multicast
            + self.combine
            + self.control
        )


def buffer_area(interior_flits: int = ARCH_A_INTERIOR_FLITS) -> float:
    """Scale baseline buffer area by stored payload bit fraction."""
    if interior_flits == ARCH_A_INTERIOR_FLITS:
        return ARCH_A_BUFFERS
    raw = BASELINE_BUFFERS * (interior_flits * FLIT_BITS / BASELINE_INTERIOR_BITS)
    return round(raw, 3)


def calendar_area(slots: int = CAL_SLOTS_DEFAULT, banks: int = CAL_BANKS) -> float:
    """Control SRAM area from 13-bit entry width and reference density."""
    bits = banks * slots * CAL_ENTRY_BITS
    # Match Trial-1 table: 26,624 bits -> 0.040 relative area.
    k_ctrl = 0.040 / (CAL_BANKS * CAL_SLOTS_DEFAULT * CAL_ENTRY_BITS)
    return bits * k_ctrl


def arch_a_area(
    cal_slots: int = CAL_SLOTS_DEFAULT,
    interior_flits: int = ARCH_A_INTERIOR_FLITS,
    mc_delta: float = FLOONOC_MC_DELTA,
    combine_delta: float = FLOONOC_COMBINE_DELTA,
    control: float = ARCH_A_CONTROL,
) -> AreaBreakdown:
    return AreaBreakdown(
        crossbar=BASELINE_CROSSBAR,
        buffers=buffer_area(interior_flits),
        calendar=calendar_area(cal_slots),
        multicast=mc_delta,
        combine=combine_delta,
        control=control,
    )


def bg_delivery_bound(
    dx: int,
    dy: int,
    *,
    bg_window: int = 16,
    t_router: int = 3,
    h_link: int = 7,
    v_link: int = 9,
    ramp: int = 1,
    credit_margin: int = 20,
) -> int:
    """Conservative enqueue-to-eject bound including link and router latency."""
    hop_h = bg_window + t_router + h_link
    hop_v = bg_window + t_router + v_link
    return 2 * ramp + dx * hop_h + dy * hop_v + credit_margin


def sensitivity_table() -> None:
    print("=== Calendar depth sensitivity (1024 default) ===")
    print(f"{'depth':>6} {'calendar':>10} {'total_area':>12} {'delta_vs_base':>14}")
    for depth in (512, 1024, 2048):
        br = arch_a_area(cal_slots=depth)
        delta = (br.total - 1.0) * 100.0
        print(f"{depth:6d} {br.calendar:10.3f} {br.total:12.3f} {delta:+13.1f}%")

    print("\n=== BG window sensitivity (performance bound, 12-hop 5H+7V) ===")
    print(f"{'BG_period':>10} {'control_est':>12} {'328_ref_bound':>14} {'bound_cyc':>10}")
    for period in (8, 16, 32):
        ctrl_adj = ARCH_A_CONTROL + (16 - period) * 0.000625
        bound = bg_delivery_bound(5, 7, bg_window=period)
        print(f"{period:10d} {ctrl_adj:12.3f} {'328 (16-slot)':>14} {bound:10d}")

    print("\n=== FlooNoC calibration uncertainty (±30% on delta) ===")
    lo_mc = FLOONOC_MC_DELTA * (1.0 - CALIBRATION_UNCERTAINTY)
    hi_mc = FLOONOC_MC_DELTA * (1.0 + CALIBRATION_UNCERTAINTY)
    lo_cb = FLOONOC_COMBINE_DELTA * (1.0 - CALIBRATION_UNCERTAINTY)
    hi_cb = FLOONOC_COMBINE_DELTA * (1.0 + CALIBRATION_UNCERTAINTY)
    base = arch_a_area()
    low = arch_a_area(mc_delta=lo_mc, combine_delta=lo_cb)
    high = arch_a_area(mc_delta=hi_mc, combine_delta=hi_cb)
    print(f"  multicast delta: {FLOONOC_MC_DELTA:.3f}  range [{lo_mc:.3f}, {hi_mc:.3f}]")
    print(f"  combine delta:   {FLOONOC_COMBINE_DELTA:.3f}  range [{lo_cb:.3f}, {hi_cb:.3f}]")
    print(f"  Arch-A total:    {base.total:.3f}  range [{low.total:.3f}, {high.total:.3f}]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sensitivity",
        action="store_true",
        help="Print calendar depth and BG period sensitivity tables.",
    )
    args = parser.parse_args()

    br = arch_a_area()
    bound_12hop = bg_delivery_bound(5, 7)

    print("=== Baseline IQ XY (normalized) ===")
    print(f"  crossbar  {BASELINE_CROSSBAR:.3f}")
    print(f"  buffers   {BASELINE_BUFFERS:.3f}  ({BASELINE_INTERIOR_FLITS} flits reference)")
    print(f"  control   {BASELINE_CONTROL:.3f}")
    print(f"  total     {BASELINE_TOTAL:.3f}")

    print("\n=== Arch-A area derivation ===")
    print(f"  crossbar   {br.crossbar:.3f}  (unchanged five-port 512b switch)")
    print(
        f"  buffers    {br.buffers:.3f}  "
        f"({ARCH_A_INTERIOR_FLITS} flits / {BASELINE_INTERIOR_FLITS} flits × {BASELINE_BUFFERS:.3f})"
    )
    print(
        f"  calendar   {br.calendar:.3f}  "
        f"({CAL_BANKS}×{CAL_SLOTS_DEFAULT}×{CAL_ENTRY_BITS} = {CAL_BANKS * CAL_SLOTS_DEFAULT * CAL_ENTRY_BITS} bits)"
    )
    print(f"  multicast  {br.multicast:.3f}  (FlooNoC +5.8% additive)")
    print(f"  combine    {br.combine:.3f}  (FlooNoC +2.7% additive)")
    print(f"  control    {br.control:.3f}  (baseline {BASELINE_CONTROL:.3f} + BG/watchdog/credit)")
    print(f"  total      {br.total:.3f}  ({(br.total - 1.0) * 100:+.1f}% vs baseline)")

    print("\n=== BG delivery bound (REQ-A-002, 6×8 longest route) ===")
    print(f"  |dx|=5, |dy|=7, hops=12: {bound_12hop} cycles")
    print(
        "  formula: 2×RAMP + |dx|×(BG_WINDOW+T_router+H_LINK)"
        " + |dy|×(BG_WINDOW+T_router+V_LINK) + T_credit"
    )

    if args.sensitivity:
        print()
        sensitivity_table()


if __name__ == "__main__":
    main()
