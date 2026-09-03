#!/usr/bin/env python3
"""One scalar for "bandwidth and fairness", derived from the ideal frontier.

The deck so far collapses a scheme to  eta = R * J / R*  (total write bandwidth
times binned Jain, over the ideal controller's same product). This script asks
what that product means, fits the ideal controller's own bandwidth-fairness
curve, and derives the scalar for which that curve is a level set.

Fairness coordinate. Jain's index of per-core rates x_c is exactly

    J = xbar^2 / (xbar^2 + sigma^2) = 1 / (1 + CoV^2),   CoV = sqrt((1 - J) / J)

so CoV is Jain re-expressed as the relative spread of the rates. Every fit
below is done in CoV, because the frontier is linear there and is not linear
in J or in 1 - J.

Frontier fit. `tradeoff_ring2_cc.json` holds R_ideal(J): the most total
bandwidth an infinitely fast controller can deliver at each Jain target, from
the link-occupancy LP. In the CoV coordinate it is a straight line through the
equal-rate point:

    R_ideal = R* + kappa * CoV,      kappa fitted here (~2.1 flit/cycle per CoV)

which is what an LP has to give: perturbing the equal-rate optimum by moving
rate from the constrained cores to the others changes both the total and the
spread linearly in the perturbation, so their ratio is a constant.

The scalar. Slide a measured point (CoV_s, R_s) down a frontier-parallel line
to CoV = 0 and read off the bandwidth:

    Phi = R - kappa * CoV,           phi = Phi / R*

`phi` is 1.0 at every point of the frontier, so it is the unique (up to
monotone transform) scalar for which the ideal controller's whole operating
curve scores the same. 1 - phi is the shortfall against the ideal in bandwidth
units, with unfairness priced at the ideal's exchange rate, and it splits as

    1 - phi = (R* - R) / R*      bandwidth the scheme does not deliver
            + kappa * CoV / R*   bandwidth an ideal exchange would need to
                                 remove the scheme's unfairness

The product eta is the same construction with the wrong denominator: it
divides by R*/J instead of R* + kappa CoV, which along the frontier itself
varies by 3.4% and peaks at J ~ 0.97 -- the product ranks the ideal controller
higher for tolerating some unfairness than for being fair.

Knob prediction. If a scheme's knob traded bandwidth for fairness at the
ideal exchange rate, phi would be constant along the knob and

    CoV(R) = (R - phi R*) / kappa,    J(R) = 1 / (1 + CoV^2)

would predict the fairness reached at any lower bandwidth. That is checked
here against every knob sweep already on disk (S16 overcommit, S22 window,
S28 target/gain, S29 slot, S1 band/cap). The slope a knob actually achieves,
kappa_s = dR / dCoV, relative to kappa, is the knob's exchange efficiency:
slope < kappa means the knob is repairing mechanism loss (moving toward the
frontier, phi rising), slope = kappa is a frontier-parallel trade, slope >
kappa is buying fairness dearer than the ideal, and a knee where CoV stops
falling while R still drops is pure loss.

Usage:
    python3 metric_ring2_cc.py        # writes results/metric_ring2_cc.json
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
OUT = RES / "metric_ring2_cc.json"


def cov_of(j: float) -> float:
    return math.sqrt(max(0.0, (1.0 - j) / j)) if j > 0 else float("inf")


def jain_of_cov(c: float) -> float:
    return 1.0 / (1.0 + c * c)


def fit_kappa(curve: list[dict[str, float]], r_fair: float) -> dict[str, Any]:
    """Least-squares slope of R - R* against CoV, intercept pinned at R*.

    Also reports the same fit against 1 - J so the choice of coordinate is
    backed by a number, not asserted.
    """
    pts = sorted({(round(p["jain_bin"], 6), round(p["bw_monotone"], 6))
                  for p in curve if p["jain_bin"] < 0.99999})
    xs = [cov_of(j) for j, _ in pts]
    ys = [bw - r_fair for _, bw in pts]
    kappa = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
    resid = [y - kappa * x for x, y in zip(xs, ys)]
    rel = [abs(r) / (r_fair + kappa * x) for r, x in zip(resid, xs)]
    # linear-in-(1-J) alternative
    us = [1.0 - j for j, _ in pts]
    k1 = sum(u * y for u, y in zip(us, ys)) / sum(u * u for u in us)
    resid1 = [y - k1 * u for u, y in zip(us, ys)]
    # local slopes, to show kappa is flat and the (1-J) slope is not
    local = []
    for (j, bw) in pts[::7]:
        c = cov_of(j)
        if c > 0:
            local.append({"jain": j, "cov": round(c, 4),
                          "kappa_local": round((bw - r_fair) / c, 3),
                          "slope_1mJ_local": round((bw - r_fair) / (1 - j), 2),
                          "R_times_J": round(bw * j, 4)})
    return {
        "kappa": round(kappa, 4),
        "n_points": len(pts),
        "rms_resid_flit": round(math.sqrt(sum(r * r for r in resid) / len(resid)), 4),
        "max_rel_resid": round(max(rel), 4),
        "alt_slope_1mJ": round(k1, 3),
        "alt_rms_resid_flit": round(math.sqrt(sum(r * r for r in resid1) / len(resid1)), 4),
        "product_on_frontier_min": round(min(bw * j for j, bw in pts + [(1.0, r_fair)]), 4),
        "product_on_frontier_max": round(max(bw * j for j, bw in pts), 4),
        "local": local,
    }


def score(bw: float, jain: float, r_fair: float, kappa: float) -> dict[str, float]:
    c = cov_of(jain)
    r_ideal = r_fair + kappa * c
    return {
        "bw": bw, "jain": jain, "cov": round(c, 5),
        "eta": round(bw * jain / r_fair, 5),
        "phi": round((bw - kappa * c) / r_fair, 5),
        "rho": round(bw / r_ideal, 5),
        "gap_bw": round((r_fair - bw) / r_fair, 5),
        "gap_fair": round(kappa * c / r_fair, 5),
        "n_eff": round(10 * jain, 3),
    }


def _rows(path: str) -> list[dict[str, Any]]:
    d = json.loads((RES / path).read_text())
    return d["rows"], d.get("k")


def knob_sweeps(r_fair: float, kappa: float) -> list[dict[str, Any]]:
    """Every 1-D knob sweep on disk, as (knob, R, J) with the phi prediction.

    The anchor for the prediction is the sweep point at the scheme's official
    operating value, so the test is: knowing only the official point's phi,
    how well does frontier-parallel trading predict J at the other knob
    settings?
    """
    out = []

    def add(name, knob, anchor, pts, k):
        pts = [p for p in pts if p[1] is not None and p[2] is not None]
        if not pts:
            return
        a = next((p for p in pts if p[0] == anchor), pts[0])
        phi_a = (a[1] - kappa * cov_of(a[2])) / r_fair
        rows = []
        for kv, bw, j in pts:
            c = cov_of(j)
            c_pred = max(0.0, (bw - phi_a * r_fair) / kappa)
            rows.append({"knob": kv, "bw": bw, "jain": j, "cov": round(c, 5),
                         "phi": round((bw - kappa * c) / r_fair, 5),
                         "jain_pred": round(jain_of_cov(c_pred), 5),
                         "cov_pred": round(c_pred, 5)})
        # slope of the useful segment: consecutive points where both fall
        segs = []
        srt = sorted(rows, key=lambda r: -r["bw"])
        for p, q in zip(srt, srt[1:]):
            d_bw, d_cov = p["bw"] - q["bw"], p["cov"] - q["cov"]
            if d_bw > 1e-6:
                segs.append({"from": p["knob"], "to": q["knob"],
                             "d_bw": round(d_bw, 4), "d_cov": round(d_cov, 4),
                             "kappa_s": round(d_bw / d_cov, 3) if d_cov > 1e-6 else None,
                             "eff": round(kappa * d_cov / d_bw, 3)})
        out.append({"scheme": name, "knob": knob, "k": k, "anchor": anchor,
                    "phi_anchor": round(phi_a, 5), "rows": rows, "segments": segs})

    rows, k = _rows("probe_ring2_s16_oc.json")
    add("S16", "overcommit", 16,
        [(r["overcommit"] if r["overcommit"] is not None else 64,
          r.get("thr"), r.get("jain_bin")) for r in rows], k)

    rows, k = _rows("probe_ring2_gapcc2.json")
    s28 = [r for r in rows if r.get("scheme") == "S28" and
           r["cfg"].get("rcp_mode", "rcp") != "static" and "rcp_alpha" in r["cfg"]]
    add("S28", "rcp_alpha·burst", "a0.25·b2.0",
        [(f"a{r['cfg']['rcp_alpha']}·b{r['cfg']['rcp_pace_burst']}",
          r.get("thr"), r.get("jain_bin")) for r in s28], k)
    s29 = [r for r in rows if r.get("scheme") == "S29"
           and r["cfg"].get("tdma_window") == 64
           and r["cfg"].get("tdma_dodge") == 8]
    add("S29", "tdma_slot", 2,
        [(r["cfg"]["tdma_slot"], r.get("thr"), r.get("jain_bin")) for r in s29], k)

    rows, k = _rows("probe_ring2_gapcc.json")
    s27 = [r for r in rows if r.get("scheme") == "S27" and r["cfg"].get("bp_reach") == 2]
    add("S27", "bp_xoff", 0.9,
        [(r["cfg"]["bp_xoff"], r.get("thr"), r.get("jain_bin")) for r in s27], k)
    s26 = [r for r in rows if r.get("scheme") == "S26" and r["cfg"].get("route_thresh") == 0.05]
    add("S26", "route_max_extra", 2,
        [(r["cfg"]["route_max_extra"], r.get("thr"), r.get("jain_bin")) for r in s26], k)

    d = json.loads((RES / "probe_ring2_s1_dirbal.json").read_text())
    s1 = []

    def dig(o):
        if isinstance(o, dict):
            if "thr" in o and "jain_bin" in o and isinstance(o.get("over"), dict):
                ov = o["over"]
                if not ov.get("dir_split") and "window" not in ov:
                    s1.append((f"{ov.get('band')}·cap{ov.get('cap_scale')}",
                               o["thr"], o["jain_bin"]))
            else:
                for v in o.values():
                    dig(v)
        elif isinstance(o, list):
            for v in o:
                dig(v)
    dig(d["grids"])
    add("S1", "band·cap_scale", "spec·cap1.0", s1, d.get("k"))

    d = json.loads((RES / "probe_ring2_s22_retune.json").read_text())
    s22 = [(f"w{r['cfg'].get('dfc_window')}·m{r['cfg'].get('dfc_margin')}",
            r.get("thr"), r.get("jain_bin")) for r in d["rows"]
           if isinstance(r.get("cfg"), dict)]
    if s22:
        add("S22", "dfc_window·margin", None, s22, d.get("k"))
    return out


def main() -> None:
    tr = json.loads((RES / "tradeoff_ring2_cc.json").read_text())
    r_fair, r_max = tr["r_fair"], tr["r_max"]
    fit = fit_kappa(tr["jain_curve"], r_fair)
    kappa = fit["kappa"]
    deck = json.loads((RES / "deck_ring2_data.json").read_text())
    schemes = {nm: score(r["throughput"], r["jain_bin"]["jain_bin_mean"], r_fair, kappa)
               for nm, r in deck["write"].items()}
    # pairwise meaning: phi difference split into its two terms
    base = schemes["S0"]
    for nm, s in schemes.items():
        s["d_phi_vs_S0"] = round(s["phi"] - base["phi"], 5)
        s["d_phi_bw_term"] = round(-(s["gap_bw"] - base["gap_bw"]), 5)
        s["d_phi_fair_term"] = round(-(s["gap_fair"] - base["gap_fair"]), 5)
        s["ratio_phi_vs_S0"] = round(s["phi"] / base["phi"], 5)
        s["ratio_phi_vs_S1"] = round(s["phi"] / schemes["S1"]["phi"], 5)
    out = {
        "r_fair": r_fair, "r_max": r_max, "kappa": kappa, "fit": fit,
        "frontier_cov_max": round(cov_of(tr["jain_curve"][0]["jain_bin"]), 4),
        "schemes": schemes,
        "knobs": knob_sweeps(r_fair, kappa),
    }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"kappa = {kappa}  (rms {fit['rms_resid_flit']} flit, "
          f"max rel {fit['max_rel_resid']*100:.2f}%)   "
          f"alt (1-J) slope rms {fit['alt_rms_resid_flit']}")
    print(f"R*J on frontier: {fit['product_on_frontier_min']} .. "
          f"{fit['product_on_frontier_max']}")
    print(f"{'scheme':6} {'bw':>7} {'J':>8} {'CoV':>7} {'eta':>7} {'phi':>7} "
          f"{'rho':>7} {'gapBW':>7} {'gapFair':>8}")
    for nm, s in schemes.items():
        print(f"{nm:6} {s['bw']:7.4f} {s['jain']:8.5f} {s['cov']:7.4f} "
              f"{s['eta']:7.4f} {s['phi']:7.4f} {s['rho']:7.4f} "
              f"{s['gap_bw']:7.4f} {s['gap_fair']:8.4f}")
    for kb in out["knobs"]:
        print(f"-- {kb['scheme']} knob={kb['knob']} K={kb['k']} "
              f"phi@anchor={kb['phi_anchor']}")
        for r in sorted(kb["rows"], key=lambda r: -r["bw"]):
            print(f"   {str(r['knob']):>14} bw={r['bw']:.4f} J={r['jain']:.5f} "
                  f"phi={r['phi']:.4f}  J_pred={r['jain_pred']:.5f}")
        for s in kb["segments"]:
            print(f"      {s['from']}->{s['to']}: dBW={s['d_bw']} dCoV={s['d_cov']} "
                  f"kappa_s={s['kappa_s']} eff={s['eff']}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
