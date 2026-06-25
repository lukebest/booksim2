#!/usr/bin/env python3
"""Multi-scheme allgather sweep for wormhole m-flit messages (m=2..5).

Extends sweep_buffer_pareto.py: same scheme families and two buffer regimes
  §1 strict router_buf=0 + border AFIFO <= 5
  §2 pipelined with link_buf <= 6 and down-ramp burst 0..6

Output: results/buffer_pareto_msg_size.json
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from sweep_buffer_pareto import AFIFO_CAP, sweep

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "buffer_pareto_msg_size.json"
FLIT_BYTES = 64
MSG_SIZES = (2, 3, 4, 5)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[2, 3, 4, 5])
    ap.add_argument("--ramps", type=int, nargs="+", default=[1, 2])
    args = ap.parse_args()
    msg_sizes = tuple(args.sizes)
    ramp_bws = tuple(args.ramps)

    out = dict(
        updated=datetime.now(timezone.utc).isoformat(),
        mesh="16x16",
        n=256,
        flit_bytes=FLIT_BYTES,
        msg_sizes=list(msg_sizes),
        ramp_bws=list(ramp_bws),
        afifo_cap=AFIFO_CAP,
        model=("wormhole m-flit messages, router_buf=0 (strict) or "
               "pipelined link<=6 + ramp burst 0..6"),
        by_msg_size={},
    )
    t0 = time.time()
    for m in msg_sizes:
        print(f"\n=== m={m} flit ===", flush=True)
        t1 = time.time()
        block = sweep(16, ramp_bws, flits=m, fast_border=True)
        block["elapsed_s"] = time.time() - t1
        out["by_msg_size"][str(m)] = block
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
        for rb in ramp_bws:
            feas = [s for s in block["strict_afifo5"][str(rb)] if s.get("makespan")]
            if feas:
                b = min(feas, key=lambda s: s["makespan"])
                print(f"  ramp={rb} strict: {b['name']} ({b['dir']}) mk={b['makespan']}",
                      flush=True)
            bp = block["burst_pareto"][str(rb)]["by_ramp_burst"].get("6")
            if bp:
                print(f"  ramp={rb} burst R=6: {bp['scheme']} ({bp['dir']}) mk={bp['makespan']}",
                      flush=True)
        print(f"  done m={m} in {block['elapsed_s']:.1f}s", flush=True)

    out["elapsed_s"] = time.time() - t0
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT} ({out['elapsed_s']:.1f}s total)")


if __name__ == "__main__":
    main()
