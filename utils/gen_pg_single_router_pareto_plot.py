#!/usr/bin/env python3
"""Pareto: at most one dead router (corner / edge / center).

Reads results/pg_single_router_e2e.json and writes
results/pg_single_router_pareto.png.  Avoidance and recovery sit on the
same axes so the two families can be compared under this milder fault model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from gen_pg_e2e_pareto_plot import LABELS, pareto

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "pg_single_router_e2e.json"
OUT_PNG = ROOT / "results" / "pg_single_router_pareto.png"

REC_COLOR = {
    "none": "#64748b",
    "sb": "#0f766e",
    "spin": "#b45309",
    "swap": "#7c3aed",
}
REC_LABEL = {
    "none": "no recovery",
    "sb": "Static Bubble",
    "spin": "SPIN",
    "swap": "SWAP",
}
RT_MARK = {
    "xy_detour": "o",
    "minmax": "^",
    "updown_relax": "s",
    "super_turn_1vc": "v",
}
RT_SHORT = {
    "xy_detour": "XY+detour",
    "minmax": "min-max",
    "updown_relax": "M3'-relax",
    "super_turn_1vc": "super-turn/1VC",
}


def _label_avoid(scheme: str) -> str:
    return LABELS.get(scheme, scheme)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=SRC)
    ap.add_argument("--out", type=Path, default=OUT_PNG)
    args = ap.parse_args()
    data = json.loads(args.src.read_text())
    meta = data["meta"]
    m0s = meta["m0_list"]
    n_scen = meta["n_scenarios"]
    skip = {"lash_tor", "stripe_vc", "virtual_mesh", "fault_ring_vc"}
    avoid = [s for s in data["summary_avoid"] if s["scheme"] not in skip]
    rec = [s for s in data["summary_recovery"] if s.get("n_ok")]

    fig, axes = plt.subplots(1, len(m0s), figsize=(13.8, 6.4))
    if len(m0s) == 1:
        axes = [axes]

    for ax, m0 in zip(axes, m0s):
        av = [s for s in avoid if s["m0"] == m0 and not s.get("partial")]
        # Front only among schemes that keep the residual graph
        # (at most the one physically dead router).  Heavy-sacrifice
        # turn models stay on the plot as gray points.
        av_ft = [s for s in av if s.get("sac_worst", 0) <= 1]
        rv = [s for s in rec if s["m0"] == m0 and s.get("complete")]
        # Unified front: treat recovery rows as scheme-like points.
        pts = ([{"area": s["area"], "t_e2e_ns_worst": s["t_e2e_ns_worst"],
                 "kind": "avoid", "src": s} for s in av_ft]
               + [{"area": s["area"], "t_e2e_ns_worst": s["t_e2e_ns_worst"],
                   "kind": "rec", "src": s}
                  for s in rv
                  if s["kind"] != "swap"
                  and s.get("n_ordered_ok", 0) == s.get("n_ok", 0)])
        front = pareto(pts, "area", "t_e2e_ns_worst")
        fset = {(p["kind"],
                 p["src"]["scheme"] if p["kind"] == "avoid"
                 else (p["src"]["routing"], p["src"]["kind"]))
                for p in front}

        # Avoidance: merge identical (area, worst)
        merged: dict[tuple[float, int], dict] = {}
        for s in av:
            k = (s["area"], round(s["t_e2e_ns_worst"]))
            g = merged.setdefault(k, {"area": s["area"], "vc": s["num_vc"],
                                      "worst": s["t_e2e_ns_worst"],
                                      "med": s["t_e2e_ns_med"],
                                      "on": False, "names": []})
            g["names"].append(_label_avoid(s["scheme"]))
            g["med"] = min(g["med"], s["t_e2e_ns_med"])
            g["on"] = g["on"] or (("avoid", s["scheme"]) in fset
                                  and s.get("sac_worst", 0) <= 1)
            g["cut"] = g.get("cut") or s.get("sac_worst", 0) > 1
            g["ft"] = g.get("ft") or s.get("sac_worst", 0) <= 1
        for g in merged.values():
            ax.plot([g["area"], g["area"]], [g["med"], g["worst"]],
                    color="#94a3b8", lw=1.1, zorder=1)
            ax.scatter([g["area"]], [g["med"]], s=22, facecolor="white",
                       edgecolor="#64748b", zorder=2)
            ax.scatter([g["area"]], [g["worst"]], s=80 if g["on"] else 42,
                       color=("#dc2626" if g["on"]
                              else ("#cbd5e1" if g.get("cut") else "#64748b")),
                       edgecolor="#1f2937", lw=0.7, zorder=3, marker="D")

        for s in rv:
            c = REC_COLOR[s["kind"]]
            mk = RT_MARK.get(s["routing"], "o")
            on = ("rec", (s["routing"], s["kind"])) in fset
            if s.get("area_hi", s["area"]) > s["area"] + 1e-4:
                ax.errorbar([s["area"]], [s["t_e2e_ns_worst"]],
                            xerr=[[0.0], [s["area_hi"] - s["area"]]],
                            fmt="none", ecolor=c, elinewidth=0.8,
                            capsize=2.2, alpha=0.5, zorder=1)
            ax.scatter([s["area"]], [s["t_e2e_ns_med"]], s=22,
                       facecolor="white", edgecolor=c, marker=mk, zorder=2)
            ax.scatter([s["area"]], [s["t_e2e_ns_worst"]],
                       s=70 if on else 40, color=c, marker=mk,
                       edgecolor="#1f2937", lw=0.6, zorder=4,
                       alpha=1.0 if s["kind"] != "none" else 0.55)

        if front:
            ax.plot([p["area"] for p in front],
                    [p["t_e2e_ns_worst"] for p in front],
                    color="#dc2626", lw=1.3, ls="--", zorder=2,
                    label="Pareto front (worst)")

        lo = min([g["med"] for g in merged.values()]
                 + [s["t_e2e_ns_med"] for s in rv] or [1])
        hi = max([g["worst"] for g in merged.values()]
                 + [s["t_e2e_ns_worst"] for s in rv] or [2])
        ax.set_yscale("log")
        ax.set_ylim(lo / 1.25, hi * 2.2)
        xmax = max([g["area"] for g in merged.values()]
                   + [s.get("area_hi", s["area"]) for s in rv] or [1])
        ax.set_xlim(0.78, max(1.45, xmax + 0.12))

        ly = None
        for g in sorted(merged.values(), key=lambda g: g["worst"]):
            if not g.get("ft"):
                continue
            ly = g["worst"] if ly is None else max(g["worst"], ly * 1.12)
            ax.annotate(" / ".join(g["names"]) + f"  (VC{g['vc']})",
                        xy=(g["area"], g["worst"]),
                        xytext=(g["area"] + 0.04, ly),
                        fontsize=6.8, va="center",
                        color="#b91c1c" if g["on"] else "#475569",
                        arrowprops=dict(arrowstyle="-", lw=0.55,
                                        color="#cbd5e1",
                                        shrinkA=0, shrinkB=2))
        # Recovery labels only for points that are not stacked on M3'
        # (updown_relax + any mech ≈ M3' time).
        for s in rv:
            if s["routing"] == "updown_relax":
                continue
            if s["kind"] == "none" and s["n_ok"] < n_scen:
                continue
            name = "%s+%s" % (RT_SHORT[s["routing"]], REC_LABEL[s["kind"]])
            if s.get("n_ordered_ok", s.get("n_ok", 0)) == 0:
                name += "  (out of order)"
            ax.annotate(name, xy=(s["area"], s["t_e2e_ns_worst"]),
                        xytext=(4, 6), textcoords="offset points",
                        fontsize=6.4, color=REC_COLOR[s["kind"]])

        ax.set_xlabel("router area  (normalized, IQ-XY baseline = 1.0)")
        ax.set_ylabel("end-to-end time  (ns)  = compute + alltoall")
        tok = meta.get("total_tokens", {})
        tok_s = tok.get(str(m0), tok.get(m0, "?"))
        ax.set_title(f"alltoall payload m\u2080 = {m0} flit"
                     f"{'s' if m0 > 1 else ''} @ 48 PE"
                     f"   ({tok_s} tokens)")
        ax.grid(alpha=0.3, ls=":")
        ax.legend(fontsize=7.5, loc="upper right")

    handles = [
        Line2D([], [], marker="D", color="#dc2626", ls="", ms=7,
               label="deadlock avoidance (on front)"),
        Line2D([], [], marker="D", color="#64748b", ls="", ms=6,
               label="deadlock avoidance"),
    ]
    for k, lab in REC_LABEL.items():
        if k == "none":
            continue
        handles.append(Line2D([], [], marker="o", color=REC_COLOR[k],
                              ls="", ms=7, label="recovery: " + lab))
    for rt, mk in RT_MARK.items():
        handles.append(Line2D([], [], marker=mk, color="#334155", ls="",
                              ms=6, label="routing: " + RT_SHORT[rt]))
    fig.legend(handles=handles, fontsize=7.4, loc="lower center", ncol=4,
               frameon=False, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle(
        "8\u00d76 PG NoC \u2014 at most 1 dead router "
        "(healthy + 4 corners + 4 edge midpoints + 2 interior)\n"
        f"filled = worst of {n_scen} location-stratified scenarios, "
        "hollow = median; diamonds = avoidance / BB, "
        "other markers = recovery",
        fontsize=9.5)
    fig.tight_layout(rect=(0, 0.08, 1, 0.90))
    fig.savefig(args.out, dpi=130)
    plt.close(fig)
    print("Wrote", args.out)

    for m0 in m0s:
        print(f"\n### m0 = {m0}  (single-router catalogue, n={n_scen})\n")
        print("| family | scheme | VC | area | A med/worst | sac | "
              "T_e2e med | T_e2e worst | Pareto |")
        print("|---|---|---|---|---|---|---|---|---|")
        for s in sorted(av if m0 == m0s[0] else
                        [x for x in avoid if x["m0"] == m0],
                        key=lambda x: x["t_e2e_ns_worst"]):
            if s["m0"] != m0:
                continue
            print("| avoid | %s | %s | %.3f | %s/%s | %s | %.0f | **%.0f** | %s |"
                  % (_label_avoid(s["scheme"]), s["num_vc"], s["area"],
                     s["A_med"], s["A_worst"], s.get("sac_worst", "?"),
                     s["t_e2e_ns_med"], s["t_e2e_ns_worst"],
                     "**yes**" if s.get("pareto_worst") else ""))
        for s in sorted((x for x in rec if x["m0"] == m0 and x.get("n_ok")),
                        key=lambda x: x.get("t_e2e_ns_worst", 1e18)):
            print("| rec | %s + %s | 1 | %.3f | %s/%s | %s | %.0f | **%.0f** | |"
                  % (RT_SHORT.get(s["routing"], s["routing"]),
                     REC_LABEL.get(s["kind"], s["kind"]),
                     s.get("area", 0), s.get("A_med", "?"),
                     s.get("A_worst", "?"), s.get("sac_worst", "?"),
                     s.get("t_e2e_ns_med", 0), s.get("t_e2e_ns_worst", 0)))


if __name__ == "__main__":
    main()
