#!/usr/bin/env python3
"""Compute AllGather makespan m=1..6 for Q1 ring, Q4 border, B2 ringfollow on 16x16.

Output: results/allgather_makespan.json
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "allgather_makespan.json"
MSG_SWEEP = ROOT / "results" / "msg_size_sweep.json"
sys.path.insert(0, str(ROOT / "utils"))

import sched_ring_zerobuf as S
import sched_zerobuf_compare as Z
import sim_fused_rings as fr
from sweep_afifo_depth import shape_cfg
from sweep_quad_ring_shapes import make_quads

MX = MY = 16
H, V, RAMP = 4, 6, 1
N = MX * MY
MSG_SIZES = tuple(range(1, 7))
AFIFO_CAP = 5
CROSS_LAT = 6


def ring_makespan(bidir, ramp_bw, flits):
    Z.cfg(MX, MY, H, V)
    Z.init_ring()
    foot = {s: Z.fp_ring(s, Z.RING_ORDER, Z.RING_POS, bidir, ramp_bw)
            for s in range(N)}
    mk, mo, busy = Z.pack(foot, ramp_bw, list(range(N)), flits=flits)
    ok = Z.verify(busy, ramp_bw, flits=flits)
    return {"makespan": mk, "max_inject_off": mo, "ok": ok, "ramp_bw": ramp_bw}


def quad_makespan_atomic(scheme, flits):
    """Fast path: schedule_atomic @ AFIFO cap=5 with sweep-optimal shapes."""
    fr.cfg(MX, MY, H, V, cross=CROSS_LAT)
    cfg = shape_cfg(MX, scheme, "bi")
    quads = make_quads(cfg)
    if scheme == "border":
        deliv = lambda s, b, q=quads: S.deliv_border_quads(s, b, q)
        order, cap = "natural", AFIFO_CAP
    else:
        deliv = lambda s, b, q=quads: S.deliv_ringfollow_quads(s, b, q)
        order, cap = "quad", 3   # best @ AFIFO<=5 uses peak 3 (see sweep)
    r = S.schedule_atomic(MX, True, 2, deliv, afifo_cap=cap,
                          order=order, quads=quads, flits=flits)
    if not r.get("ok"):
        return {"makespan": None, "ok": False, "afifo_depth": None,
                "afifo_balanced": None}
    return {
        "makespan": r["makespan"],
        "ok": True,
        "afifo_depth": r["afifo_depth"],
        "afifo_balanced": r["afifo_balanced"]["peak"],
        "max_inject_off": r["max_inject_off"],
        "cfg": [list(x) for x in cfg],
        "method": "atomic",
    }


def load_border_cached():
    """m=1..5 from msg_size_sweep.json (best wormhole search @ AFIFO<=5)."""
    if not MSG_SWEEP.exists():
        return {}
    data = json.loads(MSG_SWEEP.read_text(encoding="utf-8"))
    row = data.get("configs", {}).get("16x16_bi", {}).get("by_ramp", {}).get("2", [])
    out = {}
    for i, mk in enumerate(row, start=1):
        if i <= 5:
            out[str(i)] = {"makespan": mk, "ok": True, "method": "cached_msg_size_sweep"}
    return out


def main():
    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "mx": MX, "my": MY, "h": H, "v": V, "n": N,
        "afifo_cap": AFIFO_CAP, "cross_lat": CROSS_LAT,
        "msg_sizes": list(MSG_SIZES),
        "schemes": {},
    }
    border_cached = load_border_cached()

    for m in MSG_SIZES:
        print(f"m={m}...", flush=True)
        out["schemes"].setdefault("q1_ring_uni", {})[str(m)] = ring_makespan(False, 1, m)
        out["schemes"].setdefault("q1_ring_bi", {})[str(m)] = ring_makespan(True, 2, m)

        if str(m) in border_cached:
            out["schemes"].setdefault("q4_border_bi", {})[str(m)] = border_cached[str(m)]
        else:
            out["schemes"].setdefault("q4_border_bi", {})[str(m)] = quad_makespan_atomic(
                "border", m)

        out["schemes"].setdefault("b2_ringfollow_bi", {})[str(m)] = quad_makespan_atomic(
            "ringfollow", m)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    for name in ("q1_ring_bi", "q4_border_bi", "b2_ringfollow_bi"):
        mk1 = out["schemes"][name]["1"]["makespan"]
        mk6 = out["schemes"][name]["6"]["makespan"]
        print(f"  {name}: m=1->{mk1}  m=6->{mk6}")


if __name__ == "__main__":
    main()
