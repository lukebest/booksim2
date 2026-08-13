#!/usr/bin/env python3
"""Pareto plot for deadlock RECOVERY schemes (separate from avoidance).

Reads results/pg_recovery_e2e.json and writes results/pg_recovery_pareto.png
plus a markdown table on stdout.  Axes match results/pg_e2e_pareto.png so the
two classes can be read side by side, but the fronts are computed and drawn
separately as requested.

Each recovery mechanism is swept over three routings (see the sweep driver);
area depends only on the mechanism, so a mechanism's routings form a vertical
stack at one x, distinguished by marker shape.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "pg_recovery_e2e.json"
OUT_PNG = ROOT / "results" / "pg_recovery_pareto.png"

LABEL = {
    "none": "no recovery",
    "sb": "Static Bubble",
    "spin": "SPIN",
    "swap": "SWAP",
}
RT_LABEL = {
    "xy_detour": "baseline XY + detour",
    "minmax": "turn-free, min-max load",
    "updown_relax": "M3' Up*/Down* core + free completion",
    "super_turn_1vc": "super-turn turn set squeezed onto 1 VC",
}
RT_MARK = {"xy_detour": "o", "minmax": "^", "updown_relax": "s",
           "super_turn_1vc": "v"}
RT_SHORT = {"xy_detour": "XY+detour", "minmax": "min-max",
            "updown_relax": "M3'-relax", "super_turn_1vc": "super-turn/1VC"}
AVOID_LABEL = {
    "xy": "M1 XY",
    "east_first": "M0 East-first",
    "updown": "M3 Up*/Down*",
    "updown_best_root": "M3' best-root",
    "segment": "M4 Segment",
}
COLOR = {"sb": "#0f766e", "spin": "#b45309", "swap": "#7c3aed"}


def pareto(pts: list[dict], xk: str, yk: str) -> list[dict]:
    out = [p for p in pts
           if not any(o is not p and o[xk] <= p[xk] and o[yk] <= p[yk]
                      and (o[xk] < p[xk] or o[yk] < p[yk]) for o in pts)]
    return sorted(out, key=lambda p: p[xk])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--out", type=Path, default=OUT_PNG)
    args = ap.parse_args()

    doc = json.loads(args.src.read_text())
    meta = doc["meta"]
    m0s = meta["m0_list"]
    routings = meta["routings"]
    n_scen = meta["n_scenarios"]
    summary = doc["summary"]
    avoid = doc.get("avoidance_reference", [])

    fig, axes = plt.subplots(1, len(m0s), figsize=(14.2, 6.4))
    if len(m0s) == 1:
        axes = [axes]
    for ax, m0 in zip(axes, m0s):
        rec = [s for s in summary if s["m0"] == m0 and s["n_ok"]
               and s["kind"] != "none"]
        av = [s for s in avoid if s["m0"] == m0 and s.get("num_vc") == 1]
        front = pareto([s for s in rec if s.get("complete")],
                       "area", "t_e2e_ns_worst")

        # The best 1-VC avoidance scheme is the yardstick of this whole
        # section, so it gets a full-width line instead of a labelled dot:
        # the point of the figure is that the M3'-relax squares land on it.
        best_av = min(av, key=lambda s: s["t_e2e_ns_worst"]) if av else None
        if best_av:
            ax.axhline(best_av["t_e2e_ns_worst"], color="#0369a1", lw=1.1,
                       ls="-.", alpha=0.75, zorder=1)
            ax.annotate("best 1-VC avoidance: %s, worst %.0f ns  (A=%d/%d, "
                        "no runtime mechanism)"
                        % (AVOID_LABEL.get(best_av["scheme"],
                                           best_av["scheme"]),
                           best_av["t_e2e_ns_worst"], best_av["A_med"],
                           best_av["A_worst"]),
                        xy=(0.985, best_av["t_e2e_ns_worst"]),
                        xycoords=("axes fraction", "data"),
                        xytext=(0, 5), textcoords="offset points",
                        ha="right", fontsize=7.0, color="#0369a1", zorder=5)
        # Remaining avoidance points: dots only, one merged label per cluster.
        groups: list[list[dict]] = []
        for s in sorted((x for x in av if x is not best_av),
                        key=lambda x: (round(x["area"], 4),
                                       x["t_e2e_ns_worst"])):
            if (groups and math.isclose(groups[-1][0]["area"], s["area"],
                                        abs_tol=1e-4)
                    and s["t_e2e_ns_worst"]
                    < 1.25 * groups[-1][-1]["t_e2e_ns_worst"]):
                groups[-1].append(s)
            else:
                groups.append([s])
        for g in sorted(groups, key=lambda g: -g[0]["t_e2e_ns_worst"]):
            s = g[0]
            ax.scatter([s["area"]], [s["t_e2e_ns_worst"]], s=42, marker="D",
                       facecolor="#e2e8f0", edgecolor="#94a3b8", lw=0.8,
                       zorder=2)
            near_line = (best_av and s["t_e2e_ns_worst"]
                         < 1.6 * best_av["t_e2e_ns_worst"])
            if near_line:
                continue
            names = " / ".join(AVOID_LABEL.get(x["scheme"], x["scheme"])
                               for x in g)
            ax.annotate("%s   [avoidance, A=%d/%d]"
                        % (names, s["A_med"], s["A_worst"]),
                        xy=(s["area"], s["t_e2e_ns_worst"]),
                        xytext=(10, 4), textcoords="offset points",
                        fontsize=6.8, color="#64748b", zorder=2)

        for s in rec:
            c = COLOR[s["kind"]]
            mk = RT_MARK[s["routing"]]
            on = any(f is s for f in front)
            if s["area_hi"] > s["area"]:
                # Control logic can only be priced by each paper's own
                # synthesis; the bar reaches the higher third-party figure.
                ax.errorbar([s["area"]], [s["t_e2e_ns_worst"]],
                            xerr=[[0.0], [s["area_hi"] - s["area"]]],
                            fmt="none", ecolor=c, elinewidth=0.9, capsize=2.5,
                            alpha=0.55, zorder=1)
            ax.plot([s["area"], s["area"]],
                    [s["t_e2e_ns_med"], s["t_e2e_ns_worst"]],
                    color=c, lw=1.0, alpha=0.5, zorder=1)
            ax.scatter([s["area"]], [s["t_e2e_ns_med"]], s=22, marker=mk,
                       facecolor="white", edgecolor=c, lw=1.0, zorder=3)
            ax.scatter([s["area"]], [s["t_e2e_ns_worst"]], marker=mk,
                       s=118 if on else 62, color=c, edgecolor="#1f2937",
                       lw=0.7, zorder=4)
            tag = ""
            if s["n_ordered_ok"] < s["n_ok"]:
                tag = "  out of order"
            if not s.get("complete"):
                tag += "  [%d/%d finished]" % (s["n_ok"], s["n_scen_total"])
            if tag:
                ax.annotate(tag, xy=(s["area"], s["t_e2e_ns_worst"]),
                            xytext=(7, -2), textcoords="offset points",
                            fontsize=6.6, color=c)

        # One label per mechanism cluster, above its topmost point.
        for kind in ("sb", "spin", "swap"):
            pts = [s for s in rec if s["kind"] == kind]
            if not pts:
                continue
            top = max(pts, key=lambda s: s["t_e2e_ns_worst"])
            # Leftmost cluster would run off the axes if centred.
            ha = ("left" if top["area"] <= min(s["area"] for s in rec)
                  else "center")
            ax.annotate(LABEL[kind], xy=(top["area"], top["t_e2e_ns_worst"]),
                        xytext=(-6 if ha == "left" else 0, 13),
                        textcoords="offset points",
                        ha=ha, fontsize=8.2, color=COLOR[kind],
                        weight="bold")

        sub = []
        if len(front) > 1:
            ax.plot([s["area"] for s in front],
                    [s["t_e2e_ns_worst"] for s in front], color="#dc2626",
                    lw=1.3, ls="--", zorder=3)
            sub.append("dashed red = recovery Pareto front on (area, worst "
                       "time); packet ordering is not one of its axes")
        elif front:
            sub.append("recovery front = {%s, %s} alone"
                       % (LABEL[front[0]["kind"]],
                          RT_SHORT[front[0]["routing"]]))
        none_bits = []
        for routing in routings:
            nr = next((s for s in summary if s["m0"] == m0
                       and s["routing"] == routing and s["kind"] == "none"),
                      None)
            if nr:
                none_bits.append("%s %d/%d" % (RT_SHORT[routing],
                                               nr.get("n_ok", 0), n_scen))
        if none_bits:
            sub.append("scenarios completing without any recovery: "
                       + ", ".join(none_bits))
        ax.set_yscale("log")
        lo = min([s["t_e2e_ns_med"] for s in rec]
                 + [s["t_e2e_ns_worst"] for s in av])
        hi = max(s["t_e2e_ns_worst"] for s in rec)
        ax.set_ylim(lo / 1.8, hi * 3.4)
        ax.set_xlim(min(s["area"] for s in rec + av) - 0.006,
                    max(s["area_hi"] for s in rec) + 0.014)
        ax.set_xlabel("router area  (normalized, IQ-XY baseline = 1.0)")
        ax.set_ylabel("end-to-end time  (ns)  = compute + alltoall  (log)")
        ax.set_title("alltoall payload m0 = %d flit%s @ 48 PE"
                     % (m0, "s" if m0 > 1 else ""),
                     fontsize=10.5, pad=8 + 9 * len(sub))
        for j, line in enumerate(sub):
            ax.text(0.5, 1.005 + 0.026 * (len(sub) - 1 - j), line,
                    transform=ax.transAxes, ha="center", va="bottom",
                    fontsize=7.2, color="#475569")
        ax.grid(alpha=0.3, ls=":", which="both")

    handles = [Line2D([], [], marker=RT_MARK[r], color="#334155", ls="",
                      ms=7, label="routing: " + RT_LABEL[r])
               for r in routings]
    handles.append(Line2D([], [], marker="D", color="#94a3b8", ls="",
                          ms=6, label="1-VC deadlock avoidance (reference)"))
    handles.append(Line2D([], [], marker="|", color="#64748b", ls="-",
                          lw=1.0, ms=7,
                          label="horizontal bar = same design, area re-priced "
                                "from the paper's own overhead (left end) up "
                                "to the third-party one (right end)"))
    fig.legend(handles=handles, fontsize=8.0, loc="lower center", ncol=3,
               frameon=False, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle(
        "8x6 PG NoC - deadlock RECOVERY (1 VC, zero node sacrifice) x %d "
        "routings: end-to-end time vs router area\n"
        "filled = worst of %d fault scenarios, hollow = median; area depends "
        "only on the mechanism, so one mechanism's routings share an x"
        % (len(routings), n_scen), fontsize=9.5)
    fig.tight_layout(rect=(0, 0.075, 1, 0.9))
    fig.savefig(args.out, dpi=130)
    plt.close(fig)

    for m0 in m0s:
        print("\n### m0 = %d" % m0)
        print("| routing | mechanism (SB=HPCA'17, SPIN=ISCA'18, "
              "SWAP=MICRO'19) | VC | area (paper / 3rd-party) | "
              "peak load med | A med/worst | sac worst | in-order | "
              "T_e2e med (ns) | T_e2e worst (ns) | scenarios finished |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for routing in routings:
            for s in summary:
                if s["m0"] != m0 or s["routing"] != routing:
                    continue
                if not s["n_ok"]:
                    print("| %s | %s | 1 | - | - | - | - | - | - | - | "
                          "0/%d (%s) |"
                          % (RT_LABEL[routing], LABEL[s["kind"]],
                             s["n_scen_total"],
                             ", ".join(x for x in s["reasons"] if x)))
                    continue
                print("| %s | %s | 1 | %.3f / %.3f | %d | %d/%d | %d | %d/%d "
                      "| %.0f | **%.0f** | %d/%d |"
                      % (RT_LABEL[routing], LABEL[s["kind"]], s["area"],
                         s["area_hi"], s["max_load_med"], s["A_med"],
                         s["A_worst"], s["sac_worst"], s["n_ordered_ok"],
                         s["n_ok"], s["t_e2e_ns_med"], s["t_e2e_ns_worst"],
                         s["n_ok"], s["n_scen_total"]))
    print("\nWrote %s" % args.out)


if __name__ == "__main__":
    main()
