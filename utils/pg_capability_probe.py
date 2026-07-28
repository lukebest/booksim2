#!/usr/bin/env python3
"""Probe each PG routing scheme for the two static hard properties.

For every (scenario × semantics) the scheme is asked to build a full routing
table with **no extra sacrifice** beyond the unavoidable isolated nodes, and the
outcome is classified:

  ok        — legal table, nothing sacrificed
  sacrifice — legal table, but the scheme forced good nodes out by construction
  fail_path — could not build paths at all (fault avoidance failed)
  fail_cdg  — built a table whose CDG has a cycle (deadlock freedom failed)

The third property (in-order delivery) can only be observed in the DES, so it is
read from the sweep results by the report generator instead.

Writes results/pg_capability.json.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.setrecursionlimit(10000)

import pg_routing as R
from pg_faults_8x6 import all_scenarios, expand_pg

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "pg_capability.json"

SCHEMES = ["east_first", "xy", "rect_xy", "updown", "segment",
           "fault_ring_vc", "lash", "lash_tor", "stripe_vc", "dual_updown",
           "virtual_mesh"]

_REAL_VALIDATE = R.validate_routing


def classify(pg: dict, scheme: str) -> tuple[str, int]:
    """Zero-extra-sacrifice attempt → (verdict, n_forced_sacrificed)."""
    gen = R.SCHEME_GENERATORS[scheme]
    raw = gen(pg)
    if raw is None:
        # gen_virtual_mesh self-validates; retry with the gate off to tell a
        # pathing failure apart from a CDG failure.
        R.validate_routing = lambda *a, **k: (True, "ok")
        try:
            raw = gen(pg)
        finally:
            R.validate_routing = _REAL_VALIDATE
        return ("fail_path" if raw is None else "fail_cdg"), 0

    forced = set(raw.get("forced_sacrificed", []))
    compute = [n for n in raw.get("compute_nodes", pg["compute_nodes"])
               if n not in forced]
    adj = raw.get("route_adj", pg["route_adj"])
    paths = {k: v for k, v in raw["paths"].items()
             if k[0] in compute and k[1] in compute}
    if len(paths) != len(compute) * max(0, len(compute) - 1):
        return "fail_path", len(forced)
    ok, why = _REAL_VALIDATE(paths, compute, adj, raw.get("vc_of"))
    if not ok:
        return ("fail_cdg" if "cycle" in why else "fail_path"), len(forced)
    return ("sacrifice" if forced else "ok"), len(forced)


def main() -> None:
    t0 = time.time()
    scens = all_scenarios()
    out: dict[str, dict] = {}
    for sch in SCHEMES:
        out[sch] = {"ok": 0, "sacrifice": 0, "fail_path": 0, "fail_cdg": 0,
                    "forced_nodes": 0, "cases": {}}

    for s in scens:
        for sem in ("dead", "transit"):
            pg = expand_pg(s, sem)
            iso = {n for n in pg["compute_nodes"] if not pg["route_adj"].get(n)}
            base = R.apply_sacrifice(pg, iso, sem == "dead") if iso else pg
            for sch in SCHEMES:
                try:
                    verdict, n_forced = classify(base, sch)
                except RecursionError:
                    verdict, n_forced = "fail_path", 0
                rec = out[sch]
                rec[verdict] += 1
                rec["forced_nodes"] += n_forced
                if verdict != "ok":
                    rec["cases"][f"{s['name']}/{sem}"] = (
                        verdict if not n_forced else f"{verdict}+{n_forced}")
                print(f"  {s['name']:22s} {sem:8s} {sch:16s} {verdict}",
                      flush=True)

    doc = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_cells": len(scens) * 2,
            "elapsed_s": round(time.time() - t0, 2),
            "note": "zero-extra-sacrifice attempt per scenario x semantics; "
                    "isolated compute nodes dropped first",
        },
        "schemes": out,
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"\nWrote {OUT}  ({doc['meta']['elapsed_s']}s)")
    for sch in SCHEMES:
        r = out[sch]
        print(f"  {sch:16s} ok={r['ok']:2d} sac={r['sacrifice']:2d} "
              f"path_fail={r['fail_path']:2d} cdg_fail={r['fail_cdg']:2d} "
              f"forced_nodes={r['forced_nodes']}")


if __name__ == "__main__":
    main()
