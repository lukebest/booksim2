#!/usr/bin/env python3
"""Audit report grid cells: recommend(buffer_budget=0) vs expected selection rules."""
import json
from pathlib import Path
import autogen_allgather as A

ROOT = Path(__file__).resolve().parents[1]
sweep = json.load(open(ROOT / "results/allgather_scale_sweep.json"))
strict = json.load(open(ROOT / "results/zerobuf_strict_m1.json"))
sizes = ["4x4", "6x8", "8x8", "12x16", "16x16", "32x32"]  # exclude 64x64
issues = []
ok = 0
total = 0
for size in sizes:
    mx, my = (int(x) for x in size.split("x"))
    for rb in (1, 2):
        for m in (1, 2, 3, 4, 5):
            total += 1
            rec = A.recommend(mx, my, m, rb, sweep, buffer_budget=0)
            cell = sweep["data"].get(size, {}).get("bw", {}).get(str(rb), {}).get(str(m))
            if not cell:
                issues.append(f"MISSING {size} bw={rb} m={m}")
                continue
            if m == 1 and size in strict["data"]:
                bwblock = strict["data"][size]["bw"].get(str(rb))
                if bwblock:
                    sb = bwblock["best"]
                    if rec["scheme"] != sb["name"] or rec["makespan"] != sb["makespan"]:
                        issues.append(
                            f"{size} bw={rb} m=1: rec={rec['scheme']}({rec['makespan']}) "
                            f"!= strict={sb['name']}({sb['makespan']})")
                    else:
                        ok += 1
                elif size == "32x32":
                    ed = A._result_by_name(cell, A.M1_EXTRAPOLATE_SCHEME)
                    if not ed:
                        issues.append(f"{size} bw={rb} m=1: missing {A.M1_EXTRAPOLATE_SCHEME}")
                    elif rec["scheme"] != A.M1_EXTRAPOLATE_SCHEME or rec["makespan"] != ed["makespan"]:
                        issues.append(
                            f"{size} bw={rb} m=1: rec={rec['scheme']}({rec['makespan']}) "
                            f"!= extrapolate={A.M1_EXTRAPOLATE_SCHEME}({ed['makespan']})")
                    else:
                        ok += 1
                else:
                    issues.append(f"{size} bw={rb} m=1: no strict bw block")
            elif m == 1 and size == "32x32":
                ed = A._result_by_name(cell, A.M1_EXTRAPOLATE_SCHEME)
                if not ed:
                    issues.append(f"{size} bw={rb} m=1: missing {A.M1_EXTRAPOLATE_SCHEME}")
                elif rec["scheme"] != A.M1_EXTRAPOLATE_SCHEME or rec["makespan"] != ed["makespan"]:
                    issues.append(
                        f"{size} bw={rb} m=1: rec={rec['scheme']}({rec['makespan']}) "
                        f"!= extrapolate={A.M1_EXTRAPOLATE_SCHEME}({ed['makespan']})")
                else:
                    ok += 1
            elif m == 2:
                ed = A._result_by_name(cell, A.M2_OPTIMAL_SCHEME)
                if not ed:
                    issues.append(f"{size} bw={rb} m=2: missing {A.M2_OPTIMAL_SCHEME}")
                elif rec["scheme"] != A.M2_OPTIMAL_SCHEME or rec["makespan"] != ed["makespan"]:
                    issues.append(
                        f"{size} bw={rb} m=2: rec={rec['scheme']}({rec['makespan']}) "
                        f"!= {A.M2_OPTIMAL_SCHEME}({ed['makespan']})")
                else:
                    ok += 1
            else:
                bz = cell.get("best_zero_buffer")
                zc = [r for r in cell.get("results", [])
                      if r.get("max_link_wait") == 0 and r.get("max_ramp_wait") == 0]
                witness_best = min(zc, key=lambda r: r["makespan"]) if zc else None
                if witness_best:
                    if rec["scheme"] != witness_best["name"] or rec["makespan"] != witness_best["makespan"]:
                        issues.append(
                            f"{size} bw={rb} m={m}: rec={rec['scheme']}({rec['makespan']}) "
                            f"!= witness={witness_best['name']}({witness_best['makespan']})")
                    else:
                        ok += 1
                elif rec.get("buffer_limited") and m == 5:
                    ok += 1
                elif rec.get("buffer_limited"):
                    issues.append(
                        f"{size} bw={rb} m={m}: NO zero-buffer witness; fallback "
                        f"{rec['scheme']}({rec['makespan']}) buf="
                        f"{rec.get('max_link_wait')}/{rec.get('max_ramp_wait')}")
                else:
                    ok += 1
print(f"OK: {ok}/{total}")
for i in issues:
    print(" ", i)
raise SystemExit(1 if issues else 0)
