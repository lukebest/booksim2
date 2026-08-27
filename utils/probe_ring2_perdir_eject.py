#!/usr/bin/env python3
"""The real cost of per-direction up-ring ports is paid on the *down-ring* side.

Splitting the inject port by direction should only add capacity, yet at the
study's K total write bandwidth falls from 91.3% to 76.1% of R*. Two candidate
causes are already eliminated:

  * not retries -- `n_retry = 0` and sweeping `ha_track` over 256 / 1024 / 4096
    changes nothing at all (`probe_ring2_perdir_why`);
  * not re-routing -- `choose_dir` is deterministic, and REQ crossings are
    bit-identical between the two port structures.

What is left is recirculation, and the arithmetic closes exactly: at K=1500 the
per-direction fabric shows 526 deflections and 10520 extra hop crossings on
RSP+DAT, and 526 x 20 hops = 10520. Every extra crossing is a flit riding
another full lap.

The mechanism is the asymmetry the change introduces. Up-ring is now two ports
per VC, one per direction, so a node can *deliver* into a peer's eject buffer
from both directions in the same cycle. Down-ring is unchanged and deliberately
so: one two-write-one-read buffer per node per VC, draining 1 flit/cycle. Two
writes in, one read out -- so a sustained two-sided arrival overruns it, the
eject fails, and E-tag sends the flit around again. The E-tag mechanism is
working exactly as specified; it is the drain rate that is short.

This is the decisive test: hold the port structure at per-direction and give the
eject side more room (depth, then reserved slots, then drain rate). If the chain
above is right, deflections collapse toward zero and throughput recovers past
the shared-port baseline. `eject_bw=2` is included as the matching-capacity
reference -- it breaks the stated one-read-per-cycle rule, so it is a diagnostic
bound, not a proposal.

Forecast, written before this ran: depth alone will *not* fix it, because the
problem is a rate mismatch rather than a burst-absorption one -- a deeper buffer
just defers the overflow, so expect deflections to fall only modestly and
throughput to stay well under 90% of R*. `eject_bw=2` should remove deflections
almost entirely and land at or above 94% of R*. If depth alone does fix it, the
overload is bursty rather than sustained and the honest fix is a deeper eject
buffer.

First result, at ha_track = 256 -- and **retracted**, see below. Depth behaved as
predicted (deflections 981 -> 319 -> 103 for eject 12 / 32 / 64) but throughput
did not move (84.83% -> 84.46% -> 84.46%), and `eject_bw = 2` removed
deflections completely while throughput got *worse*, 82.59%. That reads like a
clean falsification: eliminate the suspected mechanism, metric degrades,
therefore not the cause. The tell was in `extra_crossings`, where REQ kept ~19k
excess crossings even in the zero-deflection row -- and REQ cannot deflect on its
way to a completer, so those were extra *transactions*, traced in
`probe_ring2_perdir_why` to RetryAck once the 256-entry tracker saturates.

Retraction, after re-running at the shipped ha_track = 512. The whole first
result was taken **inside the retry storm**, where the completer bound
everything and no ring-side knob could show through; "throughput got worse" only
meant "the bottleneck was not on the ring at that operating point". With the
tracker no longer binding, the same experiment reverses sign:

    eject 12 (shipped)  93.87% R*   defl 1447
    eject 32            95.17% R*   defl  793
    eject 64            95.30% R*   defl  438
    eject_bw 2          96.12% R*   defl    0

So the down-ring drain rate *is* a real limiter, and the original forecast in
this file was right for the right reason. `probe_ring2_ceiling_gap.py` and
`probe_ring2_ceiling_fix.py` price it exactly at the official K.

The general lesson, worth more than the number: an elimination experiment is
only valid at an operating point where the suspected mechanism *could* bind.

Usage:
    PYTHONHASHSEED=0 python3 probe_ring2_perdir_eject.py [K]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dse_ring2_write_fair import (BIN_W, FABRIC, M_REQ, M_RSP, W_FLITS,
                                  binned_jain, build_pattern, fairness_stats,
                                  run_scheme)
from rg_ring2_topo import (CHI_VCS_WRITE, Ring2Topology, write_bounds,
                           write_paths_for_txns)

OUT = (Path(__file__).resolve().parents[1] / "results"
       / "probe_ring2_perdir_eject.json")

CASES: list[tuple[str, dict[str, Any]]] = [
    ("shared port（参照）", {"per_dir_ports": False}),
    ("per-dir, eject 12（现状）", {}),
    ("per-dir, eject 32", {"eject_depth": 32}),
    ("per-dir, eject 64", {"eject_depth": 64}),
    ("per-dir, eject 12 + resv_ej 4", {"resv_ej": 4}),
    ("per-dir, eject 12 + eject_bw 2（破规则，仅作上界）",
     {"eject_bw": 2}),
]


def _eject_buf(r: dict[str, Any]) -> dict[str, Any]:
    """Occupancy of the down-ring buffer classes, from the sampler."""
    out = {}
    for c in ((r.get("buffers") or {}).get("by_class") or []):
        if str(c.get("buffer", "")).startswith("ejectq"):
            out[c["buffer"]] = {"depth": c.get("depth"),
                                "occ_mean_pct": c.get("occ_mean_pct"),
                                "full_pct_mean": c.get("full_pct_mean"),
                                "n_ever_full": c.get("n_ever_full")}
    return out


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    topo = Ring2Topology(n_planes=1, vcs=CHI_VCS_WRITE, route="latency")
    tx = build_pattern("uniform", k=k, W=W_FLITS, seed=0)
    vp = write_paths_for_txns(topo, tx, strategy="least_occupied")
    b = write_bounds(topo, vp, m_req=M_REQ, m_rsp=M_RSP, m_wdata=W_FLITS,
                     merge_port_vcs=False)
    r_star = len(topo.cores) * k * W_FLITS / max(1, b["bound"])
    # Assigned hop crossings, so "extra" can be attributed to extra laps.
    mult = {"req": M_REQ, "rsp": M_RSP, "dat": W_FLITS}
    assigned = {vc: sum(p.hops * mult[vc] for p in ps)
                for vc, ps in vp.items()}
    print(f"K={k}  R*={r_star:.4f}  assigned crossings={assigned}\n",
          flush=True)

    rows = []
    t0 = time.time()
    for lab, over in CASES:
        cfg = {**FABRIC, **over}
        r = run_scheme("S0", topo, tx, seed=0, cfg=cfg, quiet=True)
        inj = r["wr_inject_by_core"]
        f = fairness_stats(inj, r["makespan"], k * W_FLITS)
        jb = binned_jain(inj, BIN_W, f["t_fair"])
        thr = len(tx) * W_FLITS / max(1, r["makespan"])
        actual: dict[str, int] = {}
        for key, v in (r.get("hop_use") or {}).items():
            vc = key.rsplit(":", 1)[-1]
            actual[vc] = actual.get(vc, 0) + v["n"]
        extra = {vc: actual.get(vc, 0) - assigned.get(vc, 0)
                 for vc in sorted(assigned)}
        n_defl = r.get("n_deflections") or 0
        row = {
            "label": lab, "over": over,
            "thr": round(thr, 4), "pct_r_star": round(100 * thr / r_star, 2),
            "jain_bin": round(jb["jain_bin_mean"], 5),
            "max_min": round(f["max_min"], 4), "makespan": r["makespan"],
            "n_deflections": n_defl,
            "n_eject_full_deflect": r.get("n_eject_full_deflect"),
            "n_etag_raised": r.get("n_etag_raised"),
            "extra_crossings": extra,
            "extra_total": sum(max(0, v) for v in extra.values()),
            "extra_per_defl": round(sum(max(0, v) for v in extra.values())
                                    / max(1, n_defl), 2),
            "max_ejectq": r.get("max_ejectq"),
            "eject_buffers": _eject_buf(r),
        }
        rows.append(row)
        print(f"  {lab:<38} thr={thr:.4f} ({row['pct_r_star']:.2f}% R*) "
              f"Jbin={row['jain_bin']:.5f} mm={row['max_min']:.4f}  "
              f"defl={n_defl} etag={row['n_etag_raised']}  "
              f"extra={extra}  extra/defl={row['extra_per_defl']}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"k": k, "r_star": round(r_star, 4), "assigned": assigned,
         "n_nodes": topo.n, "rows": rows,
         "wall_secs": round(time.time() - t0, 1)}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
