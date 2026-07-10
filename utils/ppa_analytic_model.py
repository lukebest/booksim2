#!/usr/bin/env python3
"""Reproducible analytic PPA model for Arch-A4 SparseCal-SharedPool-ZB-NoCombine
(DSE Trial 4).

Trial 3 Arch-A3: SparseCal + dedicated 5×20 BG FIFOs → 1.000× area.
Trial 4 Arch-A4: same SparseCal + SharedPool-BG (40 shared + 5×2 reserve = 50)
→ buffer area ~0.183, total ~0.823×.

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

# Trial 1–3 dedicated interior BG/escape provision.
ARCH_A3_INTERIOR_FLITS = 100
ARCH_A3_BUFFERS = 0.365

# Trial 4 SharedPool-BG.
BG_SHARED_POOL = 40
BG_PER_PORT_RESERVE = 2
BG_PORTS = 5
ARCH_A4_INTERIOR_FLITS = BG_SHARED_POOL + BG_PORTS * BG_PER_PORT_RESERVE  # 50
# Linear scale from audited 100-flit → 0.365 class.
ARCH_A4_BUFFERS = round(ARCH_A3_BUFFERS * (ARCH_A4_INTERIOR_FLITS / ARCH_A3_INTERIOR_FLITS), 3)
# Shared-pool free-list / reserve accounting control overhead.
SHARED_POOL_CTRL_DELTA = 0.005

# Dense calendar calibration anchor (Trial 1/2).
DENSE_CAL_BANKS = 2
DENSE_CAL_SLOTS = 1024
DENSE_CAL_ENTRY_BITS = 13
DENSE_CAL_AREA = 0.040
K_CTRL = DENSE_CAL_AREA / (DENSE_CAL_BANKS * DENSE_CAL_SLOTS * DENSE_CAL_ENTRY_BITS)

# SparseCal (Trial 3/4): 23-bit events; depth 128; dual-bank.
SPARSE_CAL_BANKS = 2
SPARSE_CAL_DEPTH = 128
SPARSE_CAL_EVENT_BITS = 23
SPARSE_MATCH_CTRL_DELTA = 0.003

FLOONOC_MC_DELTA = 0.058
FLOONOC_COMBINE_DELTA = 0.027
CALIBRATION_UNCERTAINTY = 0.30

TRIAL1_AREA = 1.065
TRIAL1_POWER = 0.98
TRIAL2_AREA = 1.028
TRIAL2_POWER = 0.96
TRIAL2_CONTROL = 0.185
ARCH_A3_CONTROL = TRIAL2_CONTROL + SPARSE_MATCH_CTRL_DELTA  # 0.188
ARCH_A3_POWER = 0.95
ARCH_A4_CONTROL = ARCH_A3_CONTROL + SHARED_POOL_CTRL_DELTA  # 0.193
ARCH_A4_POWER = 0.92


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


def dense_calendar_area(
    slots: int = DENSE_CAL_SLOTS, banks: int = DENSE_CAL_BANKS
) -> float:
    return banks * slots * DENSE_CAL_ENTRY_BITS * K_CTRL


def sparse_calendar_area(
    depth: int = SPARSE_CAL_DEPTH,
    banks: int = SPARSE_CAL_BANKS,
    event_bits: int = SPARSE_CAL_EVENT_BITS,
) -> float:
    return banks * depth * event_bits * K_CTRL


def arch_a4_area(
    cal_depth: int = SPARSE_CAL_DEPTH,
    mc_delta: float = FLOONOC_MC_DELTA,
    combine_delta: float = 0.0,
    control: float = ARCH_A4_CONTROL,
    buffers: float = ARCH_A4_BUFFERS,
) -> AreaBreakdown:
    return AreaBreakdown(
        crossbar=BASELINE_CROSSBAR,
        buffers=buffers,
        calendar=sparse_calendar_area(cal_depth),
        multicast=mc_delta,
        combine=combine_delta,
        control=control,
    )


def arch_a3_area(
    cal_depth: int = SPARSE_CAL_DEPTH,
    mc_delta: float = FLOONOC_MC_DELTA,
    combine_delta: float = 0.0,
    control: float = ARCH_A3_CONTROL,
    buffers: float = ARCH_A3_BUFFERS,
) -> AreaBreakdown:
    return AreaBreakdown(
        crossbar=BASELINE_CROSSBAR,
        buffers=buffers,
        calendar=sparse_calendar_area(cal_depth),
        multicast=mc_delta,
        combine=combine_delta,
        control=control,
    )


def arch_a2_area() -> AreaBreakdown:
    return AreaBreakdown(
        crossbar=BASELINE_CROSSBAR,
        buffers=ARCH_A3_BUFFERS,
        calendar=dense_calendar_area(),
        multicast=FLOONOC_MC_DELTA,
        combine=0.0,
        control=TRIAL2_CONTROL,
    )


def trial1_area() -> AreaBreakdown:
    return AreaBreakdown(
        crossbar=BASELINE_CROSSBAR,
        buffers=ARCH_A3_BUFFERS,
        calendar=dense_calendar_area(),
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
    """Conservative hard-TDM bound (Trial 2). Soft-prio is strictly better."""
    hop_h = bg_window + t_router + h_link
    hop_v = bg_window + t_router + v_link
    return 2 * ramp + dx * hop_h + dy * hop_v


def soft_prio_bg_bound(
    dx: int,
    dy: int,
    *,
    max_cal_occupancy: int = 49,
    horizon: int = 952,
    t_router: int = 3,
    h_link: int = 7,
    v_link: int = 9,
    ramp: int = 1,
) -> int:
    """Soft-prio: BG uses non-matching cycles; occupancy-aware hop wait."""
    idle = max(horizon - max_cal_occupancy, 1)
    wait = max(2, (horizon + idle - 1) // idle)
    hop_h = wait + t_router + h_link
    hop_v = wait + t_router + v_link
    return 2 * ramp + dx * hop_h + dy * hop_v


def soft_prio_shared_pool_bound(
    dx: int = 5,
    dy: int = 7,
    *,
    pool_size: int = BG_SHARED_POOL,
) -> int:
    """Conservative soft + shared-pool contention bound.

    Reserve-covered single-flit traffic matches soft_prio_bg_bound (~160).
    Adversarial deep bursts may wait for shared-pool turnover; add at most
    one full pool drain (pool_size eligible cycles) amortized on the path.
    """
    return soft_prio_bg_bound(dx, dy) + pool_size


def sensitivity_table() -> None:
    print("=== Shared-pool size sensitivity (reserve=2 fixed) ===")
    print(f"{'pool':>6} {'flits':>8} {'buffers':>10} {'total_area':>12}")
    for pool in (32, 40, 48, 64):
        flits = pool + BG_PORTS * BG_PER_PORT_RESERVE
        buf = round(ARCH_A3_BUFFERS * (flits / ARCH_A3_INTERIOR_FLITS), 3)
        br = arch_a4_area(buffers=buf)
        print(f"{pool:6d} {flits:8d} {buf:10.3f} {br.total:12.3f}")

    print("\n=== SparseCal depth sensitivity (default 128) ===")
    print(f"{'depth':>6} {'calendar':>10} {'total_area':>12}")
    for depth in (64, 128, 256, 512):
        br = arch_a4_area(cal_depth=depth)
        print(f"{depth:6d} {br.calendar:10.3f} {br.total:12.3f}")

    print("\n=== Trial lineage ===")
    t1 = trial1_area()
    t2 = arch_a2_area()
    t3 = arch_a3_area()
    t4 = arch_a4_area()
    print(f"  Trial1 Arch-A:  {t1.total:.3f}")
    print(f"  Trial2 Arch-A2: {t2.total:.3f}")
    print(f"  Trial3 Arch-A3: {t3.total:.3f} (dedicated 100 flits)")
    print(
        f"  Trial4 Arch-A4: {t4.total:.3f} "
        f"(shared {BG_SHARED_POOL}+reserve {BG_PORTS}×{BG_PER_PORT_RESERVE}="
        f"{ARCH_A4_INTERIOR_FLITS})"
    )
    print(f"  delta T4−T3:    {t4.total - t3.total:+.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensitivity", action="store_true")
    args = parser.parse_args()

    br = arch_a4_area()
    t3 = arch_a3_area()
    t2 = arch_a2_area()
    t1 = trial1_area()
    bits = SPARSE_CAL_BANKS * SPARSE_CAL_DEPTH * SPARSE_CAL_EVENT_BITS
    bound_hard = bg_delivery_bound(5, 7)
    bound_soft = soft_prio_bg_bound(5, 7)
    bound_pool = soft_prio_shared_pool_bound(5, 7)

    print("=== Baseline IQ XY (normalized) ===")
    print(f"  total     {BASELINE_TOTAL:.3f}")

    print("\n=== Arch-A4 SharedPool area derivation (Trial 4) ===")
    print(f"  crossbar   {br.crossbar:.3f}")
    print(
        f"  buffers    {br.buffers:.3f}  "
        f"({ARCH_A4_INTERIOR_FLITS} flits = {BG_SHARED_POOL} shared + "
        f"{BG_PORTS}×{BG_PER_PORT_RESERVE} reserve; was {ARCH_A3_INTERIOR_FLITS})"
    )
    print(
        f"  calendar   {br.calendar:.3f}  "
        f"({SPARSE_CAL_BANKS}×{SPARSE_CAL_DEPTH}×{SPARSE_CAL_EVENT_BITS} = "
        f"{bits} bits)"
    )
    print(f"  multicast  {br.multicast:.3f}")
    print(f"  combine    {br.combine:.3f}  (Tier A — removed)")
    print(
        f"  control    {br.control:.3f}  "
        f"(+{SPARSE_MATCH_CTRL_DELTA:.3f} match +{SHARED_POOL_CTRL_DELTA:.3f} pool)"
    )
    print(f"  total      {br.total:.3f}  ({(br.total - 1.0) * 100:+.1f}% vs IQ-XY)")
    print(f"  power      {ARCH_A4_POWER:.2f}×")

    print("\n=== vs Trial 3 / Trial 2 / Trial 1 ===")
    print(f"  Trial3 area/power: {t3.total:.3f} / {ARCH_A3_POWER:.2f}×")
    print(f"  Trial2 area/power: {t2.total:.3f} / {TRIAL2_POWER:.2f}×")
    print(f"  Trial1 area/power: {t1.total:.3f} / {TRIAL1_POWER:.2f}×")
    print(
        f"  delta T4−T3:       {br.total - t3.total:+.3f} / "
        f"{ARCH_A4_POWER - ARCH_A3_POWER:+.2f}×"
    )

    print("\n=== BG delivery bound (12-hop) ===")
    print(f"  hard 1-in-16 (conservative):     {bound_hard} cycles")
    print(f"  soft-prio reserve-covered:        {bound_soft} cycles")
    print(f"  soft+shared-pool contention:      {bound_pool} cycles")

    if args.sensitivity:
        print()
        sensitivity_table()


if __name__ == "__main__":
    main()
