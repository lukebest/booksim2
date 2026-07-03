#!/usr/bin/env python3
"""Dump scheme comparison JSON for report (run once)."""
import json
from pathlib import Path
import allreduce_bound as ab
import hamilton_ring as hr
import sim_allreduce_16x16 as sa

ROOT = Path(__file__).resolve().parents[1]
R_LAT = 2

def main():
    sa.sz.cfg(16, 16, 4, 6)
    sa.sz.init_ring()
    order = hr.snake_cycle(16, 16)
    out = []
    for M in range(1, 7):
        lb = ab.allreduce_bounds(M, R_LAT)["combined"]
        for name, fn in [
            ("tree_reduce_bcast", lambda: sa.scheme_tree(136, flits=M, r_lat=R_LAT)),
            ("ring_uni_rs_ag", lambda: sa.scheme_ring(order, True, False, flits=M, r_lat=R_LAT)),
            ("ring_bi_rs_ag", lambda: sa.scheme_ring(order, True, True, flits=M, r_lat=R_LAT)),
        ]:
            s = fn()
            out.append({
                "M": M, "name": name,
                "makespan": s.get("makespan"),
                "ok": s.get("ok"),
                "lb": lb,
                "efficiency": s["makespan"] / lb if s.get("makespan") else None,
            })
            print(f"M={M} {name} mk={s.get('makespan')} ok={s.get('ok')}")
    path = ROOT / "results" / "allreduce_schemes.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {path}")

if __name__ == "__main__":
    main()
