#!/usr/bin/env python3
"""Pareto plot: router area vs end-to-end (compute + alltoall) time.

Reads results/pg_e2e_pareto.json (or --budget → pg_budget_e2e_pareto.json)
and writes the matching PNG plus a markdown table on stdout.

  python3 utils/gen_pg_e2e_pareto_plot.py
  python3 utils/gen_pg_e2e_pareto_plot.py --budget
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "pg_e2e_pareto.json"
OUT_PNG = ROOT / "results" / "pg_e2e_pareto.png"

LABELS = {
    "xy": "M1 XY",
    "rect_xy": "M2 Rect-XY",
    "updown": "M3 Up*/Down*",
    "updown_lb": "M3+LB",
    "segment": "M4 Segment",
    "segment_lb": "M4+LB",
    "fault_ring_vc": "M5 f-ring",
    "fault_half_ring": "M5h half-ring",
    "lash": "M6 LASH",
    "lash_tor": "M6b LASH-TOR",
    "stripe_vc": "M7 Stripe",
    "dual_updown": "M9 Dual UD",
    "virtual_mesh": "M10 Virtual",
    "east_first": "M0 East-first",
    "super_turn": "M0s Super-turn",
    "super_turn_1vc": "M0s1 Super-turn 1VC",
}


def _n_scen(meta: dict) -> str:
    return str(meta.get("n_scenarios")
               or meta.get("catalog", {}).get("n_scenarios")
               or "?")


def pareto(pts: list[dict], xk: str, yk: str) -> list[dict]:
    out = [p for p in pts
           if not any(o is not p and o[xk] <= p[xk] and o[yk] <= p[yk]
                      and (o[xk] < p[xk] or o[yk] < p[yk]) for o in pts)]
    return sorted(out, key=lambda p: p[xk])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", action="store_true",
                    help="plot budget-fault (≤4R/≤8L) Pareto instead of 36-cat")
    ap.add_argument("--src", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    src = args.src or (
        ROOT / "results" / "pg_budget_e2e_pareto.json" if args.budget else SRC)
    out_png = args.out or (
        ROOT / "results" / "pg_budget_e2e_pareto.png" if args.budget
        else OUT_PNG)

    data = json.loads(src.read_text())
    summary, meta = data["summary"], data["meta"]
    m0s = meta["m0_list"]
    n_scen = meta.get("catalog", {}).get("n_scenarios") or meta.get(
        "n_scenarios", "?")
    fault_note = meta.get("fault_model", "fixed catalogue")

    fig, axes = plt.subplots(1, len(m0s), figsize=(13.5, 5.8))
    if len(m0s) == 1:
        axes = [axes]
    for ax, m0 in zip(axes, m0s):
        cand = [s for s in summary if s["m0"] == m0]
        full = [s for s in cand if not s.get("partial")]
        front = pareto(full, "area", "t_e2e_ns_worst")
        fset = {s["scheme"] for s in front}

        # Schemes landing on the same (area, worst) share one label.
        merged: dict[tuple[float, int], dict] = {}
        for s in cand:
            k = (s["area"], round(s["t_e2e_ns_worst"]))
            g = merged.setdefault(k, {"area": s["area"], "vc": s["num_vc"],
                                      "worst": s["t_e2e_ns_worst"],
                                      "med": s["t_e2e_ns_med"],
                                      "on": False, "names": []})
            g["names"].append(LABELS.get(s["scheme"], s["scheme"]))
            g["med"] = min(g["med"], s["t_e2e_ns_med"])
            g["on"] = g["on"] or s["scheme"] in fset

        xmax = max(g["area"] for g in merged.values()) + 0.35
        for g in merged.values():
            ax.plot([g["area"], g["area"]], [g["med"], g["worst"]],
                    color="#94a3b8", lw=1.2, zorder=1)
            ax.scatter([g["area"]], [g["med"]], s=26,
                       facecolor="white", edgecolor="#64748b", zorder=2)
            ax.scatter([g["area"]], [g["worst"]], s=88 if g["on"] else 46,
                       color="#dc2626" if g["on"] else "#64748b",
                       edgecolor="#1f2937", lw=0.7, zorder=3)

        ax.plot([s["area"] for s in front],
                [s["t_e2e_ns_worst"] for s in front],
                color="#dc2626", lw=1.4, ls="--", zorder=2,
                label="Pareto front (worst case)")

        lo = min(g["med"] for g in merged.values())
        hi = max(g["worst"] for g in merged.values())
        pad = (hi - lo) * 0.08 or 1.0
        ax.set_ylim(lo - pad, hi + pad * 2.2)
        ax.set_xlim(0.75, max(3.45, xmax))

        gap = (hi - lo + 2 * pad) * 0.058
        ly = -1e18
        for g in sorted(merged.values(), key=lambda g: g["worst"]):
            ly = max(g["worst"], ly + gap)
            ax.annotate(" / ".join(g["names"]) + f"  (VC{g['vc']})",
                        xy=(g["area"], g["worst"]),
                        xytext=(g["area"] + 0.055, ly),
                        fontsize=7.6, va="center",
                        color="#b91c1c" if g["on"] else "#475569",
                        arrowprops=dict(arrowstyle="-", lw=0.6,
                                        color="#cbd5e1",
                                        shrinkA=0, shrinkB=2))

        ax.set_xlabel("router area  (normalized, IQ-XY baseline = 1.0)")
        ax.set_ylabel("end-to-end time  (ns)  = compute + alltoall")
        tok = meta.get("total_tokens", {})
        tok_s = tok.get(str(m0), tok.get(m0, "?"))
        ax.set_title(f"alltoall payload m\u2080 = {m0} flit"
                     f"{'s' if m0 > 1 else ''} @ 48 PE"
                     f"   ({tok_s} tokens)")
        ax.grid(alpha=0.3, ls=":")
        ax.legend(fontsize=8, loc="upper right")

    fig.suptitle(
        "8\u00d76 PG NoC \u2014 end-to-end MoE FFN time vs router area   "
        f"(PE 8\u00d764\u00d716/cy @ {meta['freq_ghz']} GHz, strong scaling, "
        "serial compute+comm)\n"
        f"filled = worst of {n_scen} scenarios ({fault_note}), "
        "hollow = median; area scales with VC count only",
        fontsize=9.5)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(out_png, dpi=130)
    plt.close(fig)

    for m0 in m0s:
        cand = sorted((s for s in summary if s["m0"] == m0),
                      key=lambda s: s["t_e2e_ns_worst"])
        tok = meta.get("total_tokens", {})
        tok_s = tok.get(str(m0), tok.get(m0, "?"))
        print(f"\n### m0 = {m0} flit  ({tok_s} tokens total)\n")
        print("| scheme | VC | area | A med/worst | sac med | "
              "T_e2e med (ns) | T_e2e worst (ns) | comm frac | Pareto |")
        print("|---|---|---|---|---|---|---|---|---|")
        for s in cand:
            print(f"| {LABELS.get(s['scheme'], s['scheme'])} | {s['num_vc']} "
                  f"| {s['area']:.3f} | {s['A_med']}/{s['A_worst']} "
                  f"| {s['sac_med']} | {s['t_e2e_ns_med']:.0f} "
                  f"| **{s['t_e2e_ns_worst']:.0f}** "
                  f"| {s['comm_frac_med']:.2f} "
                  f"| {'**yes**' if s.get('pareto_worst') else ''} |")
    print(f"\nWrote {out_png}")


if __name__ == "__main__":
    main()
