#!/usr/bin/env python3
"""Compute AllGather makespan m=1..6 for Q1 ring, Q4 border, B2 ringfollow on 16x16.

Model: horizontal snake_cycle Hamilton ring (Q1), down-ramp = 1 flit/cy/node (all schemes).

Output: results/allgather_makespan.json
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "allgather_makespan.json"
sys.path.insert(0, str(ROOT / "utils"))

import hamilton_ring as hr
import sched_ring_zerobuf as S
import sched_zerobuf_compare as Z
import sim_fused_rings as fr
from sweep_afifo_depth import shape_cfg
from sweep_quad_ring_shapes import make_quads

MX = MY = 16
H, V, RAMP = 4, 6, 1
RAMP_BW = 1
N = MX * MY
MSG_SIZES = tuple(range(1, 7))
AFIFO_CAP = 5
CROSS_LAT = 6


def snake_order():
    return hr.snake_cycle(MX, MY)


def ring_makespan(bidir, flits):
    Z.cfg(MX, MY, H, V)
    order = snake_order()
    pos = {nd: k for k, nd in enumerate(order)}
    foot = {s: Z.fp_ring(s, order, pos, bidir, RAMP_BW) for s in range(N)}
    mk, mo, busy = Z.pack(foot, RAMP_BW, list(range(N)), flits=flits)
    ok = Z.verify(busy, RAMP_BW, flits=flits)
    return {
        "makespan": mk, "max_inject_off": mo, "ok": ok,
        "ramp_bw": RAMP_BW, "ring": "snake_cycle",
    }


def quad_makespan_atomic(scheme, flits):
    fr.cfg(MX, MY, H, V, cross=CROSS_LAT)
    cfg = shape_cfg(MX, scheme, "bi")
    quads = make_quads(cfg)
    if scheme == "border":
        deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
        order, cap = "natural", AFIFO_CAP
    else:
        deliv = lambda s, b, q=quads: S.deliv_ringfollow_quads(s, b, q)
        order, cap = "quad", 3
    r = S.schedule_atomic(MX, True, RAMP_BW, deliv, afifo_cap=cap,
                          order=order, quads=quads, flits=flits)
    if not r.get("ok"):
        return {"makespan": None, "ok": False, "afifo_depth": None,
                "afifo_balanced": None, "ramp_bw": RAMP_BW}
    return {
        "makespan": r["makespan"],
        "ok": True,
        "afifo_depth": r["afifo_depth"],
        "afifo_balanced": r["afifo_balanced"]["peak"],
        "max_inject_off": r["max_inject_off"],
        "cfg": [list(x) for x in cfg],
        "method": "atomic",
        "ramp_bw": RAMP_BW,
    }


def main():
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "mx": MX, "my": MY, "h": H, "v": V, "n": N,
        "ramp_bw": RAMP_BW,
        "q1_ring": "snake_cycle",
        "afifo_cap": AFIFO_CAP, "cross_lat": CROSS_LAT,
        "msg_sizes": list(MSG_SIZES),
        "schemes": {},
    }

    for m in MSG_SIZES:
        print(f"m={m}...", flush=True)
        out["schemes"].setdefault("q1_ring_uni", {})[str(m)] = ring_makespan(False, m)
        out["schemes"].setdefault("q1_ring_bi", {})[str(m)] = ring_makespan(True, m)
        out["schemes"].setdefault("q4_border_bi", {})[str(m)] = quad_makespan_atomic(
            "border", m)
        out["schemes"].setdefault("b2_ringfollow_bi", {})[str(m)] = quad_makespan_atomic(
            "ringfollow", m)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    for name in ("q1_ring_uni", "q1_ring_bi", "q4_border_bi", "b2_ringfollow_bi"):
        mk1 = out["schemes"][name]["1"]["makespan"]
        mk6 = out["schemes"][name]["6"]["makespan"]
        print(f"  {name}: m=1->{mk1}  m=6->{mk6}")


if __name__ == "__main__":
    main()
