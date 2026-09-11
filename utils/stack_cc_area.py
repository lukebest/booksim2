#!/usr/bin/env python3
"""Silicon cost of the congestion-control schemes, in FF-equivalents.

The accounting is `pareto_ring2_cc.hw_cost` rescaled to the stacked fabric.
It is crude but auditable: every term is state somebody has to put on the
die, counted at a stated width, and the arithmetic is converted to an
FF-equivalent area so a comparator does not look free next to a register
file. The five terms are

  bus       the broadcast word latched at every station that listens;
  table     the per-station view of the other members;
  counters  window counters, deficits, service counts;
  arith     comparators, adders and the reduction that produces the mean;
  queue     inject-queue entries *beyond* the stock fabric, at flit width.

The last term is the one that decides the ranking, and it is why S22's
look-ahead variant is not "about S1's level": queue SRAM is ~288 bits an
entry against tens of bits for any controller register, so widening the
per-direction inject queue from 8 to 32 on 120 top-die stations costs two
orders of magnitude more than the entire controller.

Wherever the simulator can measure a quantity, it is measured rather than
assumed: S1's path table is sized from `mean_path_nodes`, S22's deficit
table from the member count it actually ran with, S16's service table from
its arbitration grain, and the inject depth from the parameters the winning
configuration used.

Usage:
    python3 stack_cc_area.py            # from results/stack_cc_focus.json
    python3 stack_cc_area.py --list
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FOCUS = ROOT / "results" / "stack_cc_focus.json"
OUT = ROOT / "results" / "stack_cc_area.json"

# Geometry of the fabric the cost is paid on.
N_TOP = 120              # top-die ring stations, 6 dies x 20
N_CORE = 60              # AI cores
N_HA = 96                # bottom-die home agents
N_ATTACH = 48            # bottom-die attach points / D2D landings
N_STATION = N_TOP + N_HA + N_ATTACH
N_GROUP = 6

FLIT_BITS = 288          # 256 b payload + ~32 b of routing / VC / tag
STOCK_DIR_DEPTH = 8      # FABRIC dir_inj_depth
STOCK_SHARED_DEPTH = 12  # FABRIC inj_depth
N_VC = 3
N_DIR = 2

# FF-equivalent area of one arithmetic unit. Rough, consistently applied.
ARITH = {"cmp": 20, "add": 40, "addtree6": 200, "addtree10": 360,
         "addtree60": 2360, "mult": 400, "ewma": 440}


def hw_cost(spec: dict[str, Any]) -> tuple[int, dict[str, int]]:
    """Added state in FF-equivalents, plus the per-item breakdown."""
    b: dict[str, int] = {}
    b["bus"] = spec.get("bus_bits", 0) * spec.get("bus_scope", 0)
    b["table"] = (spec.get("table_entries", 0) * spec.get("table_bits", 0)
                  * spec.get("table_scope", 0))
    b["counters"] = (spec.get("counter_bits", 0)
                     * spec.get("counter_scope", 0))
    b["arith"] = sum(ARITH[k] * n for k, n in
                     (spec.get("arith") or {}).items()) * spec.get(
                         "arith_scope", 0)
    extra_dir = max(0, spec.get("dir_inj_depth", STOCK_DIR_DEPTH)
                    - STOCK_DIR_DEPTH)
    extra_sh = max(0, spec.get("inj_depth", STOCK_SHARED_DEPTH)
                   - STOCK_SHARED_DEPTH)
    b["queue"] = ((extra_dir * N_DIR + extra_sh)
                  * N_VC * spec.get("queue_scope", 0) * FLIT_BITS)
    return sum(b.values()), b


# ---------------------------------------------------------------------------
# per-scheme specs, sized from what the winning run actually did
# ---------------------------------------------------------------------------

def spec_s0() -> tuple[dict[str, Any], str]:
    return {}, "没有任何新增状态：核只看 outstanding 窗口和环上有没有空槽。"


def spec_s1(fc: dict[str, Any], knobs: dict[str, Any]) -> tuple[dict, str]:
    """AIMD window, a 6-bit broadcast, and a per-source path table."""
    use_bus = knobs.get("use_bus", True) and fc.get("use_bus", True)
    path = fc.get("mean_path_nodes") or 0
    entries = int(math.ceil(path)) if use_bus else 0
    spec = {
        # Two 3-bit levels, latched at every station that has to see them.
        "bus_bits": 6 if use_bus else 0,
        "bus_scope": N_STATION if use_bus else 0,
        # 受控节点: each controlled source keeps the level of every station
        # its own flits ride through, and takes the max.
        "table_entries": entries, "table_bits": 6,
        "table_scope": N_CORE if use_bus else 0,
        # window budget, spent count, two fail counters
        "counter_bits": 15, "counter_scope": N_CORE,
        "arith": {"mult": 2, "add": 2, "cmp": 2}, "arith_scope": N_CORE,
        "queue_scope": N_TOP,
    }
    note = (f"每个 core 一份受控节点表，实测平均 {path:.1f} 项 × 6 bit；"
            f"6-bit 拥塞总线在 {N_STATION} 个站点各latch 一份"
            if use_bus else
            "无总线变体：只留本地 AIMD 计数器，路径表和总线寄存器全部取消")
    return spec, note


def spec_s22(fc: dict[str, Any], knobs: dict[str, Any]) -> tuple[dict, str]:
    """Deficit table, the reduction that makes its mean, and the look-ahead."""
    entries = int(fc.get("table_entries") or 0)
    grain = fc.get("grain", knobs.get("dfc_grain", "core"))
    nodes = fc.get("nodes", knobs.get("dfc_scope_nodes", "core_only"))
    bus_free = float(fc.get("target") or 0) > 0
    act = {"core_only": N_CORE, "ha_only": N_HA,
           "both": N_CORE + N_HA}[nodes]
    dodge = int(fc.get("dodge") or 0)
    depth = int(knobs.get("dir_inj_depth", STOCK_DIR_DEPTH))
    tree = "addtree60" if entries > 10 else ("addtree10" if entries > 6
                                             else "addtree6")
    # The post has to be wide enough to distinguish members that are not
    # equal; a saturating post makes the whole controller blind, so the width
    # the run actually used is the width that gets charged.
    width = int(fc.get("bus_width_bits") or 6)
    spec = {
        "bus_bits": 0 if bus_free else width,
        "bus_scope": 0 if bus_free else act,
        "table_entries": 0 if bus_free else entries,
        "table_bits": max(8, width),
        "table_scope": 0 if bus_free else act,
        # deficit register plus the window count it posts
        "counter_bits": 10 + (8 if bus_free else 0), "counter_scope": act,
        # one reduction for the table mean, the deficit update, and one
        # comparator per look-ahead entry the arbiter may examine
        "arith": ({"add": 2, "cmp": max(1, dodge)} if bus_free else
                  {tree: 1, "add": 2, "cmp": max(1, dodge)}),
        "arith_scope": act,
        "dir_inj_depth": depth,
        "queue_scope": N_TOP if depth > STOCK_DIR_DEPTH else 0,
    }
    note = (f"{'按 core' if grain == 'core' else '按 group'}记赤字，"
            f"表 {entries} 项 × {max(8, width)} bit，落在 {act} 个站点；"
            f"总线 {width} bit；前瞻 {dodge} 项要 {max(1, dodge)} 个比较器")
    if bus_free:
        note += "；无总线变体：赤字本地累加，总线与表全部取消"
    if depth > STOCK_DIR_DEPTH:
        note += (f"；每方向注入队列从 {STOCK_DIR_DEPTH} 加深到 {depth}，"
                 f"这一项就是主要面积")
    return spec, note


def spec_grant(fc: dict[str, Any], knobs: dict[str, Any]) -> tuple[dict, str]:
    """Per-completer service counters and the arbiter that reads them."""
    entries = int(fc.get("table_entries") or N_CORE)
    grain = fc.get("grain", "core")
    spec = {
        # cumulative granted flits, one counter per arbitration class
        "table_entries": entries, "table_bits": 20, "table_scope": N_HA,
        # outstanding-grant count and the round-robin pointer
        "counter_bits": 10 + 6, "counter_scope": N_HA,
        # a min-tree over the classes, plus the overcommit compare
        "arith": {"cmp": max(1, entries - 1), "add": 1}, "arith_scope": N_HA,
        "queue_scope": 0,
    }
    note = (f"每个 HA 一张 {entries} 项服务计数表"
            f"（{'按 group' if grain == 'group' else '按 core'}），"
            f"仲裁是 {max(1, entries - 1)} 级比较；授权本身走已有的 DBIDResp/"
            f"CompData，不加总线、不加缓存"
            f"（承诺峰值 {fc.get('peak_grants', 0)} 笔，S0 也要同样的落地缓存）")
    return spec, note


BUILD = {"s0": lambda fc, kw: spec_s0(),
         "s1": spec_s1, "s22": spec_s22,
         "s16": spec_grant, "s16g": spec_grant}


def build(blob: dict[str, Any]) -> dict[str, Any]:
    meta = blob.get("meta") or {}
    grid = blob.get("grid") or {}
    chosen = meta.get("chosen") or {}
    rows = []
    for s, fn in BUILD.items():
        if s not in chosen and s != "s0":
            continue
        cfg = chosen.get(s, "base")
        knobs = (grid.get(s) or {}).get(cfg, {})
        wr = ((blob.get("schemes") or {}).get("write") or {}).get(s) or {}
        fc = ((wr.get("full") or {}).get("fc")) or {}
        spec, note = fn(fc, knobs)
        cost, brk = hw_cost(spec)
        rows.append({
            "scheme": s, "config": cfg, "label": (meta.get("label") or
                                                  {}).get(s, s),
            "cost_ff": cost, "breakdown": brk, "spec": spec, "note": note,
        })
    rows.sort(key=lambda r: r["cost_ff"])
    return {
        "geometry": {"n_top": N_TOP, "n_core": N_CORE, "n_ha": N_HA,
                     "n_attach": N_ATTACH, "n_station": N_STATION,
                     "n_group": N_GROUP, "flit_bits": FLIT_BITS,
                     "stock_dir_depth": STOCK_DIR_DEPTH,
                     "stock_inj_depth": STOCK_SHARED_DEPTH,
                     "n_vc": N_VC, "n_dir": N_DIR},
        "arith": ARITH,
        "rows": rows,
    }


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    if not FOCUS.exists():
        raise SystemExit(f"missing {FOCUS}; run dse_stack_cc_sweep.py --emit")
    out = build(json.loads(FOCUS.read_text()))
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"{'scheme':7s} {'config':24s} {'FF-eq':>10s}  breakdown")
    for r in out["rows"]:
        brk = " ".join(f"{k}={v}" for k, v in r["breakdown"].items() if v)
        print(f"{r['scheme']:7s} {r['config']:24s} "
              f"{r['cost_ff']:>10,d}  {brk}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
