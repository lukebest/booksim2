#!/usr/bin/env python3
"""三性质核验 under the budget fault model (≤4R / ≤8L, non-overlap).

Same classification as pg_capability_probe.py, on the stratified budget catalog:

  ok        — legal table, nothing sacrificed
  sacrifice — legal table, but the scheme forced good nodes out by construction
  fail_path — could not build paths (fault avoidance failed)
  fail_cdg  — built a table whose CDG has a cycle (deadlock freedom failed)

Ordering is constructive (single path + deterministic vc_of); the report may
cross-check DES ordered_ok from e2e rows when present.

  python3 utils/pg_budget_probe.py
  python3 utils/pg_budget_probe.py --quick   # 1 sample per (nr,nl) cell
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.setrecursionlimit(10000)

import pg_faults_budget_8x6 as B
import pg_routing as R

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_budget_capability.json"

# All schemes with a generator; LB variants share the base generator.
SCHEMES = [
    "east_first", "super_turn_1vc", "super_turn",
    "xy", "rect_xy", "updown", "segment",
    "fault_ring_vc", "fault_half_ring",
    "lash", "lash_tor", "stripe_vc",
    "dual_updown", "virtual_mesh",
]

_REAL_VALIDATE = R.validate_routing


def classify(pg: dict, scheme: str) -> tuple[str, int, int]:
    """Zero-extra-sacrifice → (verdict, n_forced_sacrificed, num_vc)."""
    gen = R.SCHEME_GENERATORS[scheme]
    raw = gen(pg)
    if raw is None:
        # Distinguish path fail vs CDG fail for generators that self-validate.
        R.validate_routing = lambda *a, **k: (True, "ok")
        try:
            raw = gen(pg)
        finally:
            R.validate_routing = _REAL_VALIDATE
        if raw is None:
            return "fail_path", 0, 0
        return "fail_cdg", 0, int(raw.get("num_vc") or 1)

    forced = set(raw.get("forced_sacrificed", []))
    compute = [n for n in raw.get("compute_nodes", pg["compute_nodes"])
               if n not in forced]
    adj = raw.get("route_adj", pg["route_adj"])
    paths = {k: v for k, v in raw["paths"].items()
             if k[0] in compute and k[1] in compute}
    nvc = int(raw.get("num_vc") or 1)
    if len(paths) != len(compute) * max(0, len(compute) - 1):
        return "fail_path", len(forced), nvc
    ok, why = _REAL_VALIDATE(paths, compute, adj, raw.get("vc_of"))
    if not ok:
        return (("fail_cdg" if "cycle" in why else "fail_path"),
                len(forced), nvc)
    return ("sacrifice" if forced else "ok"), len(forced), nvc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-per-cell", type=int, default=None)
    args = ap.parse_args()
    n_per = args.n_per_cell if args.n_per_cell is not None else (
        1 if args.quick else 4)
    # Prefer existing catalog when it matches n_per; else regenerate.
    cat_path = ROOT / "results" / "pg_faults_budget_8x6.json"
    if cat_path.exists():
        cat = json.loads(cat_path.read_text())
        if cat.get("meta", {}).get("n_per_cell") != n_per:
            cat = B.write_catalog(n_per_cell=n_per, seed=args.seed)
    else:
        cat = B.write_catalog(n_per_cell=n_per, seed=args.seed)
    scens = cat["scenarios"]

    t0 = time.time()
    out: dict[str, dict] = {
        sch: {"ok": 0, "sacrifice": 0, "fail_path": 0, "fail_cdg": 0,
              "forced_nodes": 0, "cases": {},
              "vc_hist": {}}
        for sch in SCHEMES
    }

    for i, scen in enumerate(scens):
        pg = B.expand_budget(scen, "dead")
        iso = {n for n in pg["compute_nodes"] if not pg["route_adj"].get(n)}
        base = R.apply_sacrifice(pg, iso, True) if iso else pg
        for sch in SCHEMES:
            try:
                verdict, n_forced, nvc = classify(base, sch)
            except RecursionError:
                verdict, n_forced, nvc = "fail_path", 0, 0
            rec = out[sch]
            rec[verdict] += 1
            rec["forced_nodes"] += n_forced
            if nvc:
                vh = rec["vc_hist"]
                vh[str(nvc)] = vh.get(str(nvc), 0) + 1
            if verdict != "ok":
                rec["cases"][scen["name"]] = (
                    verdict if not n_forced else f"{verdict}+{n_forced}")
        if (i + 1) % 20 == 0 or i + 1 == len(scens):
            print(f"  …{i + 1}/{len(scens)}", flush=True)

    n = len(scens)
    summary = {}
    for sch in SCHEMES:
        r = out[sch]
        summary[sch] = {
            "n": n,
            "ok": r["ok"],
            "sacrifice": r["sacrifice"],
            "fail_path": r["fail_path"],
            "fail_cdg": r["fail_cdg"],
            "forced_nodes": r["forced_nodes"],
            "zero_sac_ok": r["ok"],
            "zero_sac_frac": round(r["ok"] / n, 4) if n else 0.0,
            "vc_hist": r["vc_hist"],
        }
        print(f"{sch:16s} ok={r['ok']:3d} sac={r['sacrifice']:3d} "
              f"path={r['fail_path']:3d} cdg={r['fail_cdg']:3d} "
              f"forced={r['forced_nodes']:4d}  vc={r['vc_hist']}", flush=True)

    doc = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.time() - t0, 1),
            "n_cells": n,
            "semantics": "dead",
            "fault_model": "budget_≤4R_≤8L_nonoverlap",
            "catalog": cat["meta"],
            "schemes": SCHEMES,
            "note": "zero-extra-sacrifice attempt per budget scenario (dead); "
                    "isolated compute nodes dropped first",
        },
        "schemes": out,
        "summary": summary,
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"\nWrote {OUT}  ({doc['meta']['elapsed_s']}s)")


if __name__ == "__main__":
    main()
