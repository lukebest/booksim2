#!/usr/bin/env python3
"""Pareto view: congestion-control benefit against hardware overhead.

The brief asks for a scheme whose hardware is "about S1's level" and which hits
two lines at once -- per-bin Jain > 0.99 and total write bandwidth within 1% of
S0 -- under a hard rule: **any use of the dedicated flow-control bus costs 30
cycles**, not negotiable.

Benefit is collapsed to one scalar so the plot has a y-axis:

    U = total write bandwidth x per-bin Jain          (flit/cycle)

i.e. fairness-weighted delivered bandwidth. It is monotone in both objectives,
has the units of the thing being bought, and does not need an arbitrary weight.
`u_rel` normalises it to S0's own U so 1.0 means "as much fairly-delivered
bandwidth as the uncontrolled baseline". The two hard lines are kept as separate
booleans, because a scalar must not be allowed to hide a failed constraint.

Hardware overhead is counted in **equivalent flip-flops of added state**, which
is crude but auditable and, importantly, dominated by a term everyone forgets:

  * `bus`      -- registers holding the broadcast word at every node.
  * `table`    -- the per-node global view (S1's 20-entry level table, S22's
                  10-entry deficit table, S23's 10-entry rate table).
  * `counters` -- window counters, credits, rate registers.
  * `arith`    -- arithmetic converted to FF-equivalent area: a comparator is
                  cheap, an adder tree moderate, a multiplier expensive.
  * `queue`    -- **extra inject-queue entries beyond the stock fabric**, at
                  flit width. This is the term that decides the plot: S22 needs
                  depth 8 -> 32 per direction so its look-ahead has candidates
                  to overtake with, and queue SRAM is ~300 bits per entry
                  against tens of bits for any controller state.

The consequence is worth stating plainly, because it bears directly on the
brief's own constraint: on this accounting **S22 is not "about S1's level"**.
Its controller arithmetic is indeed cheaper than S1's, but its inject queues
add two orders of magnitude more storage than S1's entire mechanism.

Usage:
    python3 pareto_ring2_cc.py            # re-plot from the registry
    python3 pareto_ring2_cc.py --list     # print the table
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "results" / "pareto_ring2_cc.json"
PNG = ROOT / "results" / "pareto_ring2_cc.png"

N_NODES = 20
N_CORES = 10
FLIT_BITS = 288          # 256 b payload + ~32 b of routing / VC / tag
STOCK_DIR_DEPTH = 8      # FABRIC dir_inj_depth
STOCK_SHARED_DEPTH = 12  # FABRIC inj_depth
N_VC = 3
N_DIR = 2

# FF-equivalent area for one arithmetic unit. Rough but consistently applied.
ARITH = {"cmp": 20, "add": 40, "addtree10": 360, "mult": 400, "ewma": 440}


def hw_cost(spec: dict) -> tuple[int, dict]:
    """Added state in FF-equivalents, plus the per-item breakdown."""
    b = {}
    b["bus"] = spec.get("bus_bits", 0) * N_NODES
    b["table"] = (spec.get("table_entries", 0) * spec.get("table_bits", 0)
                  * N_NODES)
    b["counters"] = spec.get("counter_bits", 0) * spec.get("counter_scope",
                                                           N_NODES)
    b["arith"] = sum(ARITH[k] * n for k, n in
                     (spec.get("arith") or {}).items()) * spec.get(
                         "arith_scope", N_NODES)
    extra_dir = max(0, spec.get("dir_inj_depth", STOCK_DIR_DEPTH)
                    - STOCK_DIR_DEPTH)
    extra_sh = max(0, spec.get("inj_depth", STOCK_SHARED_DEPTH)
                   - STOCK_SHARED_DEPTH)
    b["queue"] = ((extra_dir * N_DIR + extra_sh) * N_VC * N_NODES * FLIT_BITS)
    return sum(b.values()), b


def load() -> dict:
    if REG.exists():
        return json.loads(REG.read_text())
    return {"schemes": []}


def save(reg: dict) -> None:
    REG.write_text(json.dumps(reg, indent=2, ensure_ascii=False))


def upsert(name: str, **kw) -> None:
    """Add or replace one scheme's row. Called by the probes as they measure."""
    reg = load()
    rows = [r for r in reg["schemes"] if r["name"] != name]
    cost, breakdown = hw_cost(kw.get("hw") or {})
    row = dict(kw)
    row["name"] = name
    row["hw_cost"] = cost
    row["hw_breakdown"] = breakdown
    thr, jb = kw["thr"], kw["jain_bin"]
    row["u"] = round(thr * jb, 4)
    rows.append(row)
    reg["schemes"] = sorted(rows, key=lambda r: r["hw_cost"])
    s0 = next((r for r in rows if r["name"] == "S0"), None)
    if s0:
        for r in reg["schemes"]:
            r["u_rel"] = round(r["u"] / s0["u"], 4)
    save(reg)


def frontier(rows: list[dict]) -> list[dict]:
    """Cheapest-first scan keeping only rows nothing dominates.

    Only buildable points are eligible: a scheme that needs a faster bus than
    the 30-cycle rule allows is not on any frontier, it is off the table.
    """
    best, out = -1e9, []
    for r in sorted((x for x in rows if x.get("bus_rule_ok", True)),
                    key=lambda x: x["hw_cost"]):
        if r.get("eta", r["u"]) > best:
            out.append(r)
            best = r.get("eta", r["u"])
    return out


def plot(reg: dict) -> None:
    rows = reg["schemes"]
    if not rows:
        print("registry empty")
        return
    fig, ax = plt.subplots(figsize=(15.5, 8.0))
    # Most schemes pile into a narrow eta band, so labelling in place is
    # unreadable. Park every label in a sorted column outside the axes and run a
    # leader line back to its point: no overlap regardless of how many schemes
    # accumulate, and the column doubles as a ranking.
    fig.subplots_adjust(left=0.06, right=0.575, top=0.90, bottom=0.09)

    def y(r: dict) -> float:
        return r.get("eta", r["u"])

    for r in rows:
        ok = r.get("pass_jain") and r.get("pass_bw")
        feas = r.get("bus_rule_ok", True)
        if not feas:
            c, m, lbl = "#bbbbbb", "x", None
        elif ok:
            c, m, lbl = "#1a7f37", "*", None
        else:
            c, m, lbl = "#1f6feb", "o", None
        ax.scatter(max(r["hw_cost"], 1), y(r), s=260 if ok else 90,
                   c=c, marker=m, zorder=3, edgecolors="k",
                   linewidths=0.6, label=lbl)

    order = sorted(rows, key=lambda r: -y(r))
    slots = len(order)
    for i, r in enumerate(order):
        ok = r.get("pass_jain") and r.get("pass_bw")
        feas = r.get("bus_rule_ok", True)
        col = "#777777" if not feas else ("#1a7f37" if ok else "#111111")
        ly = 0.985 - i * (0.97 / max(1, slots - 1))
        ax.annotate(
            f"{r['name']}   J={r['jain_bin']:.4f}  "
            f"bw={r.get('bw_vs_ideal', 0):.3f}  {r['hw_cost']:,} FF-eq",
            xy=(max(r["hw_cost"], 1), y(r)), xycoords="data",
            xytext=(1.035, ly), textcoords="axes fraction",
            fontsize=7.5, color=col, va="center", zorder=4,
            arrowprops=dict(arrowstyle="-", lw=0.5, color="#bbbbbb",
                            shrinkA=0, shrinkB=3,
                            connectionstyle="arc3,rad=0.0"))

    fpts = [(max(r["hw_cost"], 1), y(r)) for r in frontier(rows)]
    if len(fpts) > 1:
        ax.plot([p[0] for p in fpts], [p[1] for p in fpts], "--",
                c="#1f6feb", lw=1.2, alpha=0.7, label="Pareto frontier")

    ideal = reg.get("ideal") or {}
    s0 = next((r for r in rows if r["name"].startswith("S0")), None)
    if ideal and s0:
        # Both acceptance lines met at once: Jain 0.99 and bandwidth within 1%
        # of S0. In eta terms that is the product of the two.
        need = (0.99 * 0.99 * ideal["s0_thr"]) / ideal["u"]
        ax.axhline(need, color="#1a7f37", ls=":", lw=1.5,
                   label=f"passes both lines: η ≥ {need:.3f}")
        ax.axhline(y(s0), color="#999", ls="-.", lw=1.0,
                   label=f"S0 baseline η = {y(s0):.3f}")
        ax.axhline(1.0, color="#b34700", ls="-", lw=1.4,
                   label=f"ideal controller η = 1.0  "
                         f"(bw {ideal['bw']:.4f}, Jain {ideal['jain_bin']:.4f})")

    ax.set_xscale("log")
    ax.set_xlabel("added hardware state (FF-equivalents, log scale) → more expensive")
    ax.set_ylabel("η  =  (bandwidth × per-bin Jain) / same for the ideal controller")
    ax.set_title(
        "Ring congestion control: benefit against the ideal controller vs hardware\n"
        "grey ✕ = needs a bus faster than the mandated 30 cycles, "
        "green ★ = passes both acceptance lines")
    ax.grid(alpha=0.25, which="both")
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(PNG, dpi=150)
    print(f"wrote {PNG}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    reg = load()
    if a.list:
        print(f"{'scheme':<26}{'U':>8}{'U/S0':>8}{'Jain':>9}{'Δbw%':>8}"
              f"{'hw(FF-eq)':>11}  lines  bus-rule")
        for r in reg["schemes"]:
            print(f"{r['name']:<26}{r['u']:>8.4f}{r.get('u_rel', 0):>8.3f}"
                  f"{r['jain_bin']:>9.5f}{r['delta_pct']:>8.2f}"
                  f"{r['hw_cost']:>11,}"
                  f"   {'✓' if r.get('pass_jain') and r.get('pass_bw') else '✗'}"
                  f"     {'✓' if r.get('bus_rule_ok', True) else '✗'}")
    plot(reg)


if __name__ == "__main__":
    main()
