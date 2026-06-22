#!/usr/bin/env python3
"""Complete 16x16 shape optimization without full 4096 sweep."""

import json
from datetime import datetime, timezone
from pathlib import Path

import sched_ring_zerobuf as S
from sweep_quad_ring_shapes import cfg_str, cfg_short, canonical_shapes, make_quads, run_one_cfg

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "optimal_quad_shapes.json"
AFIFO_CAP = 5

# From 4096 sweep (16x16 bi spread=0)
BORDER_BI_CFG = (("vflip", 1), ("rect", 1), ("rect", 3), ("vflip", 3))
RINGFOLLOW_BI_CFG = (("vflip", 3), ("rect", 3), ("rect", 1), ("rect", 3))


def best_spread(sz, scheme, bidir, ramp_bw, cfg, cap=AFIFO_CAP, min_mk=True):
    """min_mk: pick lowest makespan; else pick lowest mk with bal<=cap."""
    best_any = None
    best_bal = None
    for sp in range(35):
        for lb in (False, True):
            r = run_one_cfg(cfg, scheme, sz, bidir, ramp_bw, spread=sp, lb_cross=lb)
            if not r.get("ok"):
                continue
            bal = r["afifo_balanced"]["peak"]
            rec = dict(cfg=cfg_short(cfg), cfg_str=cfg_str(cfg),
                       makespan=r["makespan"], afifo_depth=r["afifo_depth"],
                       afifo_balanced=bal, spread=sp, lb_cross=lb)
            if best_any is None or rec["makespan"] < best_any["makespan"]:
                best_any = rec
            if bal <= cap and (best_bal is None or rec["makespan"] < best_bal["makespan"]):
                best_bal = rec
    pick = best_bal if (not min_mk and best_bal) else best_any
    return best_any, best_bal, pick


def scan_uniform(sz, scheme, bidir, ramp_bw, min_mk=True):
    shapes = canonical_shapes(["rect", "vflip", "vband"], sz)
    best_any, best_bal = None, None
    for shape in shapes:
        cfg = (shape, shape, shape, shape)
        for lb in (False, True):
            r = run_one_cfg(cfg, scheme, sz, bidir, ramp_bw, spread=0, lb_cross=lb)
            if not r.get("ok"):
                continue
            bal = r["afifo_balanced"]["peak"]
            rec = dict(cfg=cfg_short(cfg), cfg_str=cfg_str(cfg),
                       makespan=r["makespan"], afifo_depth=r["afifo_depth"],
                       afifo_balanced=bal, spread=0, lb_cross=lb)
            if best_any is None or rec["makespan"] < best_any["makespan"]:
                best_any = rec
            if bal <= AFIFO_CAP and (best_bal is None or rec["makespan"] < best_bal["makespan"]):
                best_bal = rec
    pick_cfg = tuple(tuple(x) for x in (best_any or best_bal)["cfg"])
    _, _, pick = best_spread(sz, scheme, bidir, ramp_bw, pick_cfg, min_mk=min_mk)
    return best_any, best_bal, pick


def main():
  # Load partial or create skeleton
  if OUT.exists():
    data = json.loads(OUT.read_text())
  else:
    data = {"sizes": {}}

  data["updated"] = datetime.now(timezone.utc).isoformat()
  data["afifo_cap"] = AFIFO_CAP
  data.setdefault("sizes", {})
  data["sizes"].setdefault("16x16", {})
  data["sizes"]["16x16"].setdefault("border", {})
  # 16x16 uni: uniform shapes, min makespan
  print("16x16 border uni (uniform shapes)...")
  any_, bal, pick = scan_uniform(16, "border", False, 1, min_mk=True)
  data["sizes"]["16x16"]["border"]["uni"] = {
    "best_any": any_, "best_balanced": bal, "chosen": pick, "n_configs": 8,
  }
  print(f"  chosen mk={pick['makespan']} bal={pick['afifo_balanced']}")

  # 16x16 bi: shape-opt cfg, min makespan (240cy @ spread=0)
  print("16x16 border bi (shape-opt cfg)...")
  any_, bal, pick = best_spread(16, "border", True, 2, BORDER_BI_CFG, min_mk=True)
  data["sizes"]["16x16"]["border"]["bi"] = {
    "best_any": any_, "best_balanced": bal, "chosen": pick, "n_configs": 1,
  }
  print(f"  chosen mk={pick['makespan']} bal={pick['afifo_balanced']}")

  # ringfollow 16x16 bi
  print("16x16 ringfollow bi...")
  any_, bal, pick = best_spread(16, "ringfollow", True, 2, RINGFOLLOW_BI_CFG, min_mk=True)
  if "16x16" not in data["sizes"]:
    data["sizes"]["16x16"] = {}
  data["sizes"]["16x16"]["ringfollow"] = {"bi": {
    "best_any": any_, "best_balanced": bal, "chosen": pick,
  }}

  OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
  print(f"Wrote {OUT}")


if __name__ == "__main__":
  main()
