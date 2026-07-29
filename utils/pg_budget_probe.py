#!/usr/bin/env python3
"""Capability probe under the budget fault model (≤4R / ≤8L).

Compares fixed east-first, super_turn, and the retained baselines on:
  zero-extra-sacrifice path success, CDG acyclicity, VC used, sacrifice cost.

  python3 utils/pg_budget_probe.py
  python3 utils/pg_budget_probe.py --quick   # 1 sample per (nr,nl) cell
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.setrecursionlimit(10000)

import pg_faults_budget_8x6 as B
import pg_routing as R

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_budget_capability.json"

SCHEMES = [
    "east_first", "super_turn", "updown", "lash", "stripe_vc",
    "dual_updown", "virtual_mesh",
]


def drop_iso(pg, sem="dead"):
    iso = {n for n in pg["compute_nodes"] if not pg["route_adj"].get(n)}
    return R.apply_sacrifice(pg, iso, sem == "dead") if iso else pg


def probe_one(pg, scheme: str) -> dict:
    base = drop_iso(pg)
    gen = R.SCHEME_GENERATORS[scheme]
    raw = gen(base)
    if raw is None:
        sol = R.solve_scheme(pg, scheme)
        return {
            "verdict": "fail_then_sac" if sol.get("feasible") else "infeasible",
            "zero_sac_ok": False,
            "n_sacrificed": sol.get("n_sacrificed", -1),
            "A": sol.get("n_compute_used", 0),
            "num_vc": sol.get("num_vc", 0),
            "turn_mode": None, "turn_vc": None,
        }
    compute = raw.get("compute_nodes", base["compute_nodes"])
    adj = raw.get("route_adj", base["route_adj"])
    ok, why = R.validate_routing(raw["paths"], compute, adj, raw.get("vc_of"))
    forced = len(raw.get("forced_sacrificed", []))
    return {
        "verdict": "ok" if ok and forced == 0 else (
            "forced_sac" if ok else "fail_cdg"),
        "zero_sac_ok": bool(ok and forced == 0),
        "n_sacrificed": forced,
        "A": len(compute),
        "num_vc": int(raw.get("num_vc") or 1),
        "turn_mode": raw.get("turn_mode"),
        "turn_vc": raw.get("turn_vc"),
        "cdg_ok": ok, "cdg_why": why if not ok else "ok",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    n_per = 1 if args.quick else 4
    cat = B.write_catalog(n_per_cell=n_per, seed=args.seed)
    scens = cat["scenarios"]
    t0 = time.time()
    tallies = {s: Counter() for s in SCHEMES}
    vc_hist = defaultdict(Counter)
    mode_hist = Counter()
    rows = []
    for i, scen in enumerate(scens):
        pg = B.expand_budget(scen, "dead")
        for sch in SCHEMES:
            r = probe_one(pg, sch)
            tallies[sch][r["verdict"]] += 1
            if r["zero_sac_ok"]:
                tallies[sch]["zero_ok"] += 1
            vc_hist[sch][r["num_vc"]] += 1
            if sch == "super_turn" and r.get("turn_mode"):
                mode_hist[f"{r['turn_mode']}/vc{r.get('turn_vc')}"] += 1
            rows.append({"scenario": scen["name"], "scheme": sch, **r})
        if (i + 1) % 40 == 0:
            print(f"  …{i+1}/{len(scens)}", flush=True)

    summary = {}
    for sch in SCHEMES:
        c = tallies[sch]
        n = len(scens)
        summary[sch] = {
            "n": n,
            "zero_sac_ok": c["zero_ok"],
            "zero_sac_frac": round(c["zero_ok"] / n, 4),
            "verdicts": dict(c),
            "vc_hist": {str(k): v for k, v in sorted(vc_hist[sch].items())},
        }
        print(f"{sch:16s} zero_sac={c['zero_ok']:4d}/{n} "
              f"({c['zero_ok']/n*100:5.1f}%)  vc={dict(vc_hist[sch])}  "
              f"verdicts={dict(c)}", flush=True)
    print(f"super_turn modes: {dict(mode_hist)}", flush=True)

    doc = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.time() - t0, 1),
            "catalog": cat["meta"],
            "schemes": SCHEMES,
        },
        "summary": summary,
        "super_turn_modes": dict(mode_hist),
        "rows": rows,
    }
    OUT.write_text(json.dumps(doc, indent=1))
    print(f"\nWrote {OUT}  ({doc['meta']['elapsed_s']}s)")


if __name__ == "__main__":
    main()
