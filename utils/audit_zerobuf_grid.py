#!/usr/bin/env python3
"""Audit all 70 grid cells: zero-buffer best vs recommend(buffer_budget=0)."""
import json
from pathlib import Path
import autogen_allgather as A

ROOT = Path(__file__).resolve().parents[1]
sweep = json.load(open(ROOT / "results/allgather_scale_sweep.json"))
strict = json.load(open(ROOT / "results/zerobuf_strict_m1.json"))
sizes = ["4x4", "6x8", "8x8", "12x16", "16x16", "32x32", "64x64"]
issues = []
ok = 0
for size in sizes:
    mx, my = (int(x) for x in size.split("x"))
    for rb in (1, 2):
        for m in (1, 2, 3, 4, 5):
            rec = A.recommend(mx, my, m, rb, sweep, buffer_budget=0)
            cell = sweep["data"].get(size, {}).get("bw", {}).get(str(rb), {}).get(str(m))
            if not cell:
                issues.append(f"MISSING {size} bw={rb} m={m}")
                continue
            bz = cell.get("best_zero_buffer")
            zc = [r for r in cell.get("results", [])
                  if r.get("max_link_wait") == 0 and r.get("max_ramp_wait") == 0]
            witness_best = min(zc, key=lambda r: r["makespan"]) if zc else None
            if m == 1 and size in strict["data"]:
                sb = strict["data"][size]["bw"][str(rb)]["best"]
                if rec["scheme"] != sb["name"] or rec["makespan"] != sb["makespan"]:
                    issues.append(f"{size} bw={rb} m=1: rec={rec['scheme']}({rec['makespan']}) != strict={sb['name']}({sb['makespan']})")
                else:
                    ok += 1
            elif witness_best:
                if rec["scheme"] != witness_best["name"] or rec["makespan"] != witness_best["makespan"]:
                    issues.append(f"{size} bw={rb} m={m}: rec={rec['scheme']}({rec['makespan']}) != witness={witness_best['name']}({witness_best['makespan']})")
                else:
                    ok += 1
            elif rec.get("buffer_limited"):
                issues.append(f"{size} bw={rb} m={m}: NO zero-buffer witness; fallback {rec['scheme']}({rec['makespan']}) buf={rec.get('max_link_wait')}/{rec.get('max_ramp_wait')}")
            else:
                ok += 1
print(f"OK: {ok}/70")
for i in issues:
    print(" ", i)
raise SystemExit(1 if issues else 0)
