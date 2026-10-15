#!/usr/bin/env python3
"""Steady-state sweep of CA-credit and CA-rate on the 8x6 mesh and ring.

Writes results/ca_credit_rate_8x6.json. The HTML section is produced by
utils/gen_ca_section.py from this file plus the existing bisection sweep.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rg_steady_des import SteadyParams, run_steady

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "ca_credit_rate_8x6.json"

WARM, MEAS = 3000, 12000
LAMS = (0.10, 0.20, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.80)
# Ring's knee sits higher than the mesh's; these two points resolve it.
RING_EXTRA = (0.65, 0.75)
HA_TIGHT = (0.05, 0.10, 0.15, 0.20, 0.30, 0.40)


def jobs() -> list[dict]:
    out = []
    for fabric in ("mesh", "ring"):
        lams = LAMS + (RING_EXTRA if fabric == "ring" else ())
        for scale in (0.5, 1.0, 2.0):
            for lam in lams:
                out.append({
                    "config": f"{fabric}_ca_credit",
                    "design": f"{fabric}_credit_s{scale:g}",
                    "lam": lam,
                    "ca_credit_scale": scale,
                    "ca_period": 1,
                    "ha_trackers": 0,
                })
        for lam in HA_TIGHT:
            out.append({
                "config": f"{fabric}_ca_credit",
                "design": f"{fabric}_credit_s1_ha16",
                "lam": lam,
                "ca_credit_scale": 1.0,
                "ca_period": 1,
                "ha_trackers": 16,
            })
        for period in (16, 64, 256):
            for lam in lams:
                out.append({
                    "config": f"{fabric}_ca_rate",
                    "design": f"{fabric}_rate_p{period}",
                    "lam": lam,
                    "ca_period": period,
                    "ca_credit_scale": 1.0,
                    "ha_trackers": 0,
                })
    return out


def _job(spec: dict) -> dict:
    design = spec["design"]
    cfg = spec["config"]
    p = SteadyParams(
        warmup=WARM, measure=MEAS, buf_depth=20,
        lam=spec["lam"],
        ca_credit_scale=spec["ca_credit_scale"],
        ca_period=spec["ca_period"],
        ha_trackers=spec["ha_trackers"],
    )
    t0 = time.perf_counter()
    r = run_steady(cfg, p)
    r["secs"] = round(time.perf_counter() - t0, 2)
    r["design"] = design
    return r


def main() -> None:
    import os
    work = jobs()
    ncpu = os.cpu_count() or 4
    workers = max(1, min(8, ncpu))
    print(f"{len(work)} points, {workers} workers, "
          f"warmup={WARM} measure={MEAS}", flush=True)
    rows = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_job, j) for j in work]
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            rows.append(r)
            print(f"[{i:3d}/{len(work)}] {r['design']:22} "
                  f"lam={r['lam']:.2f} acc={r['accepted']:.3f} "
                  f"stab={int(r['stable'])} p99={r['p99']} "
                  f"{r['secs']:.1f}s", flush=True)
    rows.sort(key=lambda r: (r["design"], r["lam"]))
    doc = {
        "meta": {
            "warmup": WARM,
            "measure": MEAS,
            "buf_depth": 20,
            "fifo_depth": 4,
            "secs": round(time.perf_counter() - t0, 1),
            "n": len(rows),
            "note": (
                "CA-credit / CA-rate from docs/rg.md, admitted onto the "
                "existing mesh_base and ring_base data planes. "
                "ha_trackers=0 sizes the HA pool to anchor x no-load RTT."
            ),
        },
        "rows": rows,
    }
    OUT.write_text(json.dumps(doc), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB) "
          f"in {doc['meta']['secs']:.0f}s", flush=True)


if __name__ == "__main__":
    main()
