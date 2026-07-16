#!/usr/bin/env python3
"""axis+CCW only: makespan vs chip-implementation area, with Pareto front.

Implementation design variables (per the user's spec):
  * W = crossbar physical bandwidth toward the down-ramp (eject) direction
        [flits/cycle the crossbar can push into the eject FIFO],
  * B = burst-buffer (eject FIFO) depth [flits],
  * E = read-burst-buffer-then-write-SRAM bandwidth [flits/cycle],
        i.e. the sustained drain out of the FIFO into the gather SRAM.

Coupling: a burst buffer only helps when W > E (burst in, drain out).  With
B=0 there is no absorption, so W>E is wasted and only W==E is coherent.

Makespan(W,E,B) is the rigid wide-eject pack of axis+CCW (reusing the shared
FIFO model: crossbar writes <=W/cy into a depth-B FIFO drained <=E/cy).

Area model (normalized to IQ-XY router = 1.0), incremental over the cheapest
implementation (W=E=1, B=0):
  crossbar eject ports : 0.076*(W-1)          (extra crossbar out columns)
  burst buffer         : A_FLIT*B*(W+E)/2      (depth B, W write + E read ports)
  multi-write SRAM     : SRAM_PORT*(E-1)       (E write ports on the gather SRAM)
A "high" multiport-premium variant is also computed for sensitivity.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import ppa_analytic_model as PPA
import sched_zerobuf_compare as S
import dse_burst_sweep_8x6 as BSW
from dse_tree_allgather_6x8 import MX, MY, H, V, N, formal_bounds, axis_ccw_tree

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "results" / "axis_area_makespan.json"
OUT_PNG = ROOT / "results" / "axis_ccw_area_makespan.png"

W_RANGE = [1, 2, 3, 4, 5]
E_RANGE = [1, 2, 3, 4]
B_RANGE = [0, 1, 2, 3, 4, 5, 6, 8, 11]

# Area coefficients (normalized to IQ-XY = 1.0).
A_FLIT = round(PPA.ARCH_A3_BUFFERS / PPA.ARCH_A3_INTERIOR_FLITS, 5)  # 0.00365 / 512b flit
CROSSBAR_PORT = round(PPA.BASELINE_CROSSBAR / 5, 4)                   # 0.076 per out-port
GATHER_DEPTH = N - 1                                                  # 47 flits held per PE


def area_parts(W: int, E: int, B: int, *, buf_mult: float = 1.0,
               sram_prem: float = 0.5, port_super: bool = False):
    """Return (crossbar, buffer, sram) area deltas under given coefficients."""
    xbar = CROSSBAR_PORT * (W - 1)
    port_factor = ((W + E) - 1) if port_super else (W + E) / 2.0
    buf = buf_mult * A_FLIT * B * port_factor
    sram = sram_prem * A_FLIT * GATHER_DEPTH * (E - 1)
    return xbar, buf, sram


def area_model(W: int, E: int, B: int, *, premium: str = "nominal") -> dict:
    if premium == "nominal":
        xbar, buf, sram = area_parts(W, E, B)
    else:  # high: superlinear buffer ports + full-cost SRAM write ports
        xbar, buf, sram = area_parts(W, E, B, sram_prem=1.0, port_super=True)
    delta = xbar + buf + sram
    return {
        "crossbar_eject": round(xbar, 5),
        "burst_buffer": round(buf, 5),
        "multiwrite_sram": round(sram, 5),
        "delta": round(delta, 5),
        "total": round(1.0 + delta, 5),
    }


def crossover_scan(points):
    """Find the smallest buffer-cost multiplier (and SRAM premium) at which an
    E>=2 config first appears on the makespan/area Pareto front."""
    def first_e2(buf_mult, sram_prem):
        pts = []
        for p in points:
            if p["makespan"] is None:
                continue
            xbar, buf, sram = area_parts(
                p["W"], p["E"], p["B"], buf_mult=buf_mult, sram_prem=sram_prem)
            pts.append({"W": p["W"], "E": p["E"], "B": p["B"],
                        "mk": p["makespan"], "area": 1.0 + xbar + buf + sram})
        fr = pareto(pts, "area", "mk")
        return any(q["E"] >= 2 for q in fr), fr

    # sweep buffer multiplier (SRAM premium fixed at nominal 0.5)
    buf_cross = None
    g = 1.0
    while g <= 3.0:
        hit, _ = first_e2(g, 0.5)
        if hit:
            buf_cross = round(g, 3)
            break
        g += 0.01
    # sweep SRAM premium downward (buffer nominal)
    sram_cross = None
    a = 0.5
    while a >= 0.0:
        hit, _ = first_e2(1.0, a)
        if hit:
            sram_cross = round(a, 3)
        a -= 0.01
    _, fr_at_buf = first_e2(buf_cross, 0.5) if buf_cross else (None, [])
    return {
        "buffer_cost_multiplier_for_E2": buf_cross,
        "sram_premium_below_which_E2": sram_cross,
        "note": "nominal buf_mult=1.0, sram_prem=0.5; E=1 leads by <1% at nominal",
        "pareto_at_buffer_crossover": [
            {"W": q["W"], "E": q["E"], "B": q["B"], "makespan": q["mk"],
             "area": round(q["area"], 4)} for q in fr_at_buf],
    }


def coherent_points():
    seen = set()
    for E in E_RANGE:
        for W in W_RANGE:
            if W < E:
                continue  # pushing slower than draining wastes W
            for B in B_RANGE:
                if B == 0 and W != E:
                    continue  # no absorption -> extra W is pure waste
                key = (W, E, B)
                if key in seen:
                    continue
                seen.add(key)
                yield W, E, B


def pareto(points, xkey, ykey):
    out = []
    for p in points:
        if p[ykey] is None:
            continue
        dom = any(
            q[ykey] is not None and q[xkey] <= p[xkey] and q[ykey] <= p[ykey]
            and (q[xkey] < p[xkey] or q[ykey] < p[ykey])
            for q in points
        )
        if not dom:
            out.append(p)
    return sorted(out, key=lambda p: (p[xkey], p[ykey]))


def make_plot(points, front, lb):
    fig, ax = plt.subplots(figsize=(8.4, 5.6))
    xs = [p["area_total"] for p in points if p["makespan"]]
    ys = [p["makespan"] for p in points if p["makespan"]]
    ws = [p["W"] for p in points if p["makespan"]]
    sc = ax.scatter(xs, ys, c=ws, cmap="viridis", s=48, edgecolor="#334155",
                    linewidth=0.4, zorder=3, label="design points (color=W)")
    fx = [p["area_total"] for p in front]
    fy = [p["makespan"] for p in front]
    ax.plot(fx, fy, "-o", color="#dc2626", lw=2, ms=7, zorder=4,
            label="Pareto front")
    for p in front:
        ax.annotate(f"W{p['W']}/E{p['E']}/B{p['B']}",
                    (p["area_total"], p["makespan"]),
                    textcoords="offset points", xytext=(6, 6),
                    fontsize=7, color="#991b1b")
    ax.axhline(lb, color="#2563eb", ls="--", lw=1, zorder=1)
    ax.text(max(xs), lb + 1, f"rb=2 lower bound LB={lb}", color="#2563eb",
            fontsize=8, ha="right", va="bottom")
    ax.set_xlabel("chip implementation area (normalized, IQ-XY=1.0)")
    ax.set_ylabel("makespan (cycles)")
    ax.set_title("axis+CCW: makespan vs implementation area (vars W/E/B)")
    ax.grid(True, ls=":", alpha=0.5)
    cb = fig.colorbar(sc, ax=ax)
    cb.set_label("crossbar down-ramp bandwidth W (flits/cy)")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130)
    plt.close(fig)


def main() -> None:
    S.cfg(MX, MY, H, V)
    S.init_ring()
    S.init_quadrants()
    fps, _stretch, _dil = BSW.build(axis_ccw_tree)
    orders = [BSW.corner_order()]
    for _n, gen in S.SRC_ORDERS.items():
        try:
            orders.append(list(gen()))
        except TypeError:
            continue
    lb = formal_bounds(1)["T_lb"]

    points = []
    for W, E, B in coherent_points():
        BSW.XBAR_WRITE = W
        BSW.DRAIN = E
        rec = BSW.pack_with_buffer(fps, B, orders)
        mk = rec["makespan"] if rec else None
        anom = area_model(W, E, B, premium="nominal")
        ahi = area_model(W, E, B, premium="high")
        points.append({
            "W": W, "E": E, "B": B,
            "makespan": mk,
            "area_total": anom["total"],
            "area_total_high": ahi["total"],
            "area_breakdown": anom,
        })

    front = pareto(points, "area_total", "makespan")
    front_high = pareto(
        [{**p, "area_total": p["area_total_high"]} for p in points],
        "area_total", "makespan")
    crossover = crossover_scan(points)
    make_plot(points, front, lb)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scheme": "axis_ccw",
        "model": {
            "mesh": [MX, MY], "H": H, "V": V, "rb_nominal": 2,
            "lower_bound_rb2": lb,
            "design_vars": {"W": W_RANGE, "E": E_RANGE, "B": B_RANGE},
            "area_coeffs": {
                "A_FLIT_per_flit_1w1r": A_FLIT,
                "crossbar_port": CROSSBAR_PORT,
                "gather_depth_flits": GATHER_DEPTH,
            },
            "coupling": "W>=E; B=0 requires W==E; buffer area scales with (W+E)/2",
        },
        "points": points,
        "pareto_nominal": front,
        "pareto_high_premium": front_high,
        "e1_e2_crossover": crossover,
        "plot": str(OUT_PNG.relative_to(ROOT)),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"points={len(points)} pareto={len(front)} LB={lb}")
    print(f"{'W':>2} {'E':>2} {'B':>3} {'mk':>4} {'area':>7}   [nominal front]")
    for p in front:
        print(f"{p['W']:>2} {p['E']:>2} {p['B']:>3} {p['makespan']:>4} "
              f"{p['area_total']:>7.4f}")
    print("[high-premium front]")
    for p in front_high:
        print(f"{p['W']:>2} {p['E']:>2} {p['B']:>3} {p['makespan']:>4} "
              f"{p['area_total']:>7.4f}")
    print(f"[crossover] E2 enters Pareto when buffer cost x >= "
          f"{crossover['buffer_cost_multiplier_for_E2']} "
          f"OR sram premium <= {crossover['sram_premium_below_which_E2']}")
    print(f"Wrote {OUT_JSON}\nWrote {OUT_PNG}")


if __name__ == "__main__":
    main()
