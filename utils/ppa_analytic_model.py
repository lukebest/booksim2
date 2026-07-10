#!/usr/bin/env python3
"""Reproducible analytic PPA model for Arch-A2 CalSlot-Hybrid-ZB-NoCombine (DSE Trial 2).

Trial 1 audited Arch-A total was 1.065 (with combine 0.027 and control 0.195).
Trial 2 removes combine and leans control to 0.185 → 1.028.

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

# Audited Trial-1/2 interior BG/escape provision (5×20 flits).
ARCH_A2_INTERIOR_FLITS = 100
ARCH_A2_BUFFERS = 0.365
ARCH_A2_CONTROL = 0.185

CAL_SLOTS_DEFAULT = 1024
CAL_BANKS = 2
CAL_ENTRY_BITS = 13

FLOONOC_MC_DELTA = 0.058
FLOONOC_COMBINE_DELTA = 0.027  # comparison-only; Arch-A2 default uses 0
CALIBRATION_UNCERTAINTY = 0.30

TRIAL1_AREA = 1.065
TRIAL1_POWER = 0.98
ARCH_A2_POWER = 0.96


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


def calendar_area(slots: int = CAL_SLOTS_DEFAULT, banks: int = CAL_BANKS) -> float:
    bits = banks * slots * CAL_ENTRY_BITS
    k_ctrl = 0.040 / (CAL_BANKS * CAL_SLOTS_DEFAULT * CAL_ENTRY_BITS)
    return bits * k_ctrl


def arch_a2_area(
    cal_slots: int = CAL_SLOTS_DEFAULT,
    mc_delta: float = FLOONOC_MC_DELTA,
    combine_delta: float = 0.0,
    control: float = ARCH_A2_CONTROL,
    buffers: float = ARCH_A2_BUFFERS,
) -> AreaBreakdown:
    return AreaBreakdown(
        crossbar=BASELINE_CROSSBAR,
        buffers=buffers,
        calendar=calendar_area(cal_slots),
        multicast=mc_delta,
        combine=combine_delta,
        control=control,
    )


def trial1_area() -> AreaBreakdown:
    return AreaBreakdown(
        crossbar=BASELINE_CROSSBAR,
        buffers=ARCH_A2_BUFFERS,
        calendar=calendar_area(),
        multicast=FLOONOC_MC_DELTA,
        combine=FLOONOC_COMBINE_DELTA,
        control=0.195,
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
) -> int:
    hop_h = bg_window + t_router + h_link
    hop_v = bg_window + t_router + v_link
    return 2 * ramp + dx * hop_h + dy * hop_v


def sensitivity_table() -> None:
    print("=== Calendar depth sensitivity (1024 default) ===")
    print(f"{'depth':>6} {'calendar':>10} {'total_area':>12}")
    for depth in (512, 1024, 2048):
        br = arch_a2_area(cal_slots=depth)
        print(f"{depth:6d} {br.calendar:10.3f} {br.total:12.3f}")

    print("\n=== Combine on/off (Trial1 vs Trial2) ===")
    t1 = trial1_area()
    t2 = arch_a2_area()
    print(f"  Trial1 Arch-A:  {t1.total:.3f} (combine={t1.combine:.3f}, ctrl={t1.control:.3f})")
    print(f"  Trial2 Arch-A2: {t2.total:.3f} (combine={t2.combine:.3f}, ctrl={t2.control:.3f})")
    print(f"  delta area:     {t2.total - t1.total:+.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensitivity", action="store_true")
    args = parser.parse_args()

    br = arch_a2_area()
    t1 = trial1_area()
    bound_12hop = bg_delivery_bound(5, 7)

    print("=== Baseline IQ XY (normalized) ===")
    print(f"  total     {BASELINE_TOTAL:.3f}")

    print("\n=== Arch-A2 area derivation (Trial 2) ===")
    print(f"  crossbar   {br.crossbar:.3f}")
    print(f"  buffers    {br.buffers:.3f}  ({ARCH_A2_INTERIOR_FLITS} flits audited)")
    print(
        f"  calendar   {br.calendar:.3f}  "
        f"({CAL_BANKS}×{CAL_SLOTS_DEFAULT}×{CAL_ENTRY_BITS} = "
        f"{CAL_BANKS * CAL_SLOTS_DEFAULT * CAL_ENTRY_BITS} bits)"
    )
    print(f"  multicast  {br.multicast:.3f}")
    print(f"  combine    {br.combine:.3f}  (Tier A — removed)")
    print(f"  control    {br.control:.3f}")
    print(f"  total      {br.total:.3f}  ({(br.total - 1.0) * 100:+.1f}% vs IQ-XY)")
    print(f"  power      {ARCH_A2_POWER:.2f}×")

    print("\n=== vs Trial 1 Arch-A ===")
    print(f"  Trial1 area/power: {t1.total:.3f} / {TRIAL1_POWER:.2f}×")
    print(
        f"  delta area/power:  {br.total - t1.total:+.3f} "
        f"/ {ARCH_A2_POWER - TRIAL1_POWER:+.2f}×"
    )

    print("\n=== BG delivery bound (12-hop) ===")
    print(f"  |dx|=5, |dy|=7: {bound_12hop} cycles")

    if args.sensitivity:
        print()
        sensitivity_table()


if __name__ == "__main__":
    main()
